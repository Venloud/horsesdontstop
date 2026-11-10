# One-shot supervised PAPER pilot (authorized by the owner 2026-11-09).
# Runs unattended in a visible console from a scheduled task:
#   deploy release -> tests -> restart reconciler -> supervised preflight ->
#   ONE autonomous entry (retry while no candidate qualifies) -> lifecycle
#   follow-up (executor every 5 min = holding/maintenance) -> PAUSE -> report.
# Never arms trading, never stores credentials, never installs the schedule.
# Any failed gate stops the pilot, pauses trading and notifies.
param([string]$ReleaseRef = 'origin/release/paper-supervised-pilot',
      [string]$LatestEntryTime = '15:10', [int]$FollowMinutes = 45)
$ErrorActionPreference = 'Stop'
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
$repo = 'C:\Users\van02\horsesdontstop'
$python = Join-Path $repo '.venv\Scripts\python.exe'
$win = Join-Path $repo 'ops\windows'
$outDir = Join-Path $folder 'supervised-pilot'
New-Item -ItemType Directory -Path $outDir -Force | Out-Null
$stamp = Get-Date -Format 'yyyyMMdd-HHmmss'
Start-Transcript -Path (Join-Path $outDir "pilot-$stamp.log") | Out-Null
$report = [ordered]@{ started = (Get-Date).ToString('o'); steps = @() }

function Read-Dpapi([string]$name) {
    $s = (Get-Content -LiteralPath (Join-Path $folder $name) -Raw).Trim() | ConvertTo-SecureString
    [pscredential]::new('x', $s).GetNetworkCredential().Password
}
function Notify([string]$title, [string]$msg, [string]$prio = 'default') {
    try {
        $topic = Read-Dpapi 'paper-ntfy-topic.dpapi'
        Invoke-RestMethod -Method Post -Uri "https://ntfy.sh/$topic" -Body $msg -TimeoutSec 10 `
            -Headers @{ Title = "horsesdontstop PAPER pilot: $title"; Priority = $prio } | Out-Null
    } catch { Write-Host "notify failed: $($_.Exception.GetType().Name)" }
}
function Step([string]$name, [string]$result, $detail = $null) {
    Write-Host ("[{0}] {1}: {2}" -f (Get-Date -Format 'HH:mm:ss'), $name, $result)
    $script:report.steps += [ordered]@{ at = (Get-Date).ToString('o'); step = $name; result = $result; detail = $detail }
}
function Save-Report {
    $script:report.finished = (Get-Date).ToString('o')
    $script:report | ConvertTo-Json -Depth 12 | Set-Content (Join-Path $outDir "report-$stamp.json") -Encoding UTF8
}
function Pause-Trading([string]$why) {
    & powershell -NoProfile -ExecutionPolicy RemoteSigned -File (Join-Path $win 'set-paper-trading.ps1') -Stop -Reason "pilot: $why"
    Step 'pause' $(if ($LASTEXITCODE -eq 0) { 'PAUSED' } else { "PAUSE FAILED exit=$LASTEXITCODE" })
}
function Verify-Lifecycle {
    $env:PAPER_JOURNAL_DSN = $script:monitorDsn
    $env:ALPACA_PAPER_API_KEY = Read-Dpapi 'alpaca-paper-key.dpapi'
    $env:ALPACA_PAPER_SECRET_KEY = Read-Dpapi 'alpaca-paper-secret.dpapi'
    try {
        Push-Location $repo
        $json = & $python -m bot.paper_pilot_verify | Out-String
        return ($json | ConvertFrom-Json)
    } finally {
        Pop-Location
        foreach ($n in 'PAPER_JOURNAL_DSN','ALPACA_PAPER_API_KEY','ALPACA_PAPER_SECRET_KEY') { Remove-Item "Env:$n" -ErrorAction SilentlyContinue }
    }
}
function Run-Executor {
    & powershell -NoProfile -ExecutionPolicy RemoteSigned -File (Join-Path $win 'run-paper-executor.ps1') -Autonomous
    $code = $LASTEXITCODE
    $latest = Get-Content (Join-Path $folder 'paper-executor\latest.json') -Raw | ConvertFrom-Json
    $receipt = Get-Content (Join-Path $folder 'executor-runtime.json') -Raw | ConvertFrom-Json
    # latest.json is only rewritten on completion; the receipt is authoritative.
    $mode = $(if ($receipt.status -eq 'completed' -and $code -eq 0) { $latest.mode } else { 'BLOCKED' })
    return [ordered]@{ exit = $code; mode = $mode; reason = $latest.reason
                       receipt_status = $receipt.status; receipt_stage = $receipt.stage
                       receipt_reason = $receipt.safe_reason; latest = $latest }
}

$pw = [Uri]::EscapeDataString((Read-Dpapi 'neon-monitor.dpapi'))
$script:monitorDsn = "postgresql://hds_paper_win_monitor:${pw}@ep-summer-salad-b5btnpj6.c-7.us-east-2.aws.neon.tech:5432/neondb?sslmode=require&channel_binding=require"
Remove-Variable pw

try {
    Notify 'starting' 'Supervised PAPER pilot starting: deploy, tests, preflight, one entry.'
    # 1. Deploy the release (code gates ON; Neon controls remain the authority).
    Push-Location $repo
    try {
        git fetch -q origin
        if (git status --porcelain) { throw 'Runtime checkout has local changes; refusing to deploy.' }
        git checkout -q -B release/paper-supervised-pilot $ReleaseRef
        $head = git rev-parse --short HEAD
        $flags = Select-String bot\paper_supervised_execution.py,bot\paper_reconciler_service.py -Pattern '^PAPER_\w+_RELEASED = True' | Measure-Object
        if ($flags.Count -ne 2) { throw 'Release flags not found ON in deployed code.' }
        Step 'deploy' "release @ $head"
        & $python -m pytest -q -p no:cacheprovider | Select-Object -Last 1 | Tee-Object -Variable testLine | Out-Null
        if ($LASTEXITCODE -ne 0) { throw "Test suite failed: $testLine" }
        Step 'tests' "$testLine"
    } finally { Pop-Location }

    # 2. Restart the reconciler on release code (status writes now released).
    Stop-ScheduledTask -TaskName HorsesDontStop-PAPER-Reconciler -ErrorAction SilentlyContinue
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'bot\.paper_reconciler_service' } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    Start-Sleep 2
    Start-ScheduledTask -TaskName HorsesDontStop-PAPER-Reconciler
    Start-Sleep 45
    $rec = Get-Content (Join-Path $folder 'reconciler-runtime.json') -Raw | ConvertFrom-Json
    $age = ((Get-Date).ToUniversalTime() - [datetime]::Parse($rec.observed_at_utc).ToUniversalTime()).TotalSeconds
    if ($age -gt 90) { throw "Reconciler did not restart (receipt age ${age}s)." }
    Step 'reconciler' "restarted, receipt seq $($rec.sequence)"

    # 3. Supervised preflight must be READY (armed, policy enabled, market open, flat).
    & powershell -NoProfile -ExecutionPolicy RemoteSigned -File (Join-Path $win 'paper-release-preflight.ps1') -Stage supervised | Tee-Object -Variable pre | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Supervised preflight NOT READY (see log).' }
    Step 'preflight' 'READY'

    # 4. ONE entry. Retry every 15 minutes while no candidate qualifies.
    $entered = $false
    while (-not $entered) {
        $run = Run-Executor
        Step 'executor' "$($run.mode) exit=$($run.exit)" ([ordered]@{ reason = $run.reason; stage = $run.receipt_stage; blocked = $run.receipt_reason })
        if ($run.mode -eq 'SUBMITTED_POLICY_AUTHORIZED') {
            $entered = $true
            $l = $run.latest
            Notify 'order submitted' "BUY $($l.shares) $($l.symbol) limit $($l.buy_limit_usd) stop $($l.stop_usd) risk $($l.estimated_stop_risk_usd)" 'high'
            break
        }
        if ($run.mode -eq 'BLOCKED') { throw "Executor blocked at $($run.receipt_stage): $($run.receipt_reason)" }
        if ((Get-Date).ToString('HH:mm') -ge $LatestEntryTime) { Step 'entry' 'no qualifying candidate before cutoff'; break }
        Start-Sleep -Seconds 900
    }

    # 5. Follow the lifecycle: executor every 5 minutes (holding check + maintenance).
    if ($entered) {
        $deadline = (Get-Date).AddMinutes($FollowMinutes)
        $final = $null
        while ((Get-Date) -lt $deadline) {
            Start-Sleep -Seconds 60
            $v = Verify-Lifecycle
            Step 'lifecycle' $v.stage ([ordered]@{ intent = $v.intent; parent = $v.broker_parent; legs = $v.broker_legs; positions = $v.positions; problems = $v.problems; managed = $v.reconciliation.managed_exposure })
            if ($v.problems.Count -gt 0) { Notify 'lifecycle problem' (($v.problems -join ', ') + " at stage $($v.stage)") 'urgent' }
            if ($v.stage -eq 'FILLED_PROTECTED') {
                $hold = Run-Executor
                Step 'executor-holding' "$($hold.mode) exit=$($hold.exit)"
                $final = $v; if ($hold.mode -eq 'POSITION_MANAGED') { break }
            }
            if ($v.stage -in 'CANCELED','EXITED','PROBLEM') { $final = $v; break }
            if ((Get-Date).Minute % 5 -eq 0) { $m = Run-Executor; Step 'executor-maintenance' "$($m.mode) exit=$($m.exit)" }
        }
        $report.final_stage = $(if ($final) { $final.stage } else { 'TIMEOUT' })
        $report.final_evidence = $final
    }
    Pause-Trading 'supervised pilot complete'
    $msg = "Pilot finished: final stage $($report.final_stage). Trading PAUSED. Report in supervised-pilot folder."
    Notify 'finished' $msg $(if ($report.final_stage -eq 'FILLED_PROTECTED') { 'high' } else { 'default' })
    Step 'done' $msg
} catch {
    Step 'ABORTED' $_.Exception.Message
    Notify 'aborted' ("Pilot stopped: " + $_.Exception.Message) 'urgent'
    Pause-Trading 'pilot aborted'
} finally {
    Remove-Variable monitorDsn -Scope Script -ErrorAction SilentlyContinue
    Save-Report
    Stop-Transcript | Out-Null
    Write-Host "`nReport: $outDir\report-$stamp.json   (window stays open)"
    Read-Host 'Press Enter to close' | Out-Null
}
