# Read-only Alpaca PAPER/Neon/Cloudflare supervisor.
# Uses existing Windows-user DPAPI credential files, never stores plaintext.
# Does NOT submit orders, change database controls, or enable trading.
param(
    [ValidateRange(1, 5)]
    [int]$MaxTicks = 3,
    [switch]$Continuous,
    [switch]$Diagnose,
    [switch]$TestAlert
)

$ErrorActionPreference = "Stop"
$folder = Join-Path $env:LOCALAPPDATA "horsesdontstop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$dbHost = "ep-summer-salad-b5btnpj6.c-7.us-east-2.aws.neon.tech"
$python = Join-Path $repo ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Existing HorsesDontStop virtual environment not found. No supervisor started."
}

# Count ROOT python processes of one long-running module. A venv launcher
# and its child interpreter share a command line and count once.
# -1 means the count itself failed; the diagnostic treats that as FAIL.
function Get-HdsServiceInstances([string]$module) {
    try {
        $procs = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction Stop |
            Where-Object {
                $_.CommandLine -and
                $_.CommandLine -match ("-m\s+" + [regex]::Escape($module) + "(\s|$)") -and
                $_.CommandLine -notmatch "--diagnose"
            })
        $ids = @($procs | ForEach-Object { $_.ProcessId })
        return @($procs | Where-Object { $ids -notcontains $_.ParentProcessId }).Count
    }
    catch {
        return -1
    }
}

function Read-HdsSecret([string]$name) {
    $file = Join-Path $folder $name
    if (-not (Test-Path -LiteralPath $file)) {
        throw "Missing Windows DPAPI credential file: $name"
    }
    $encrypted = (Get-Content -LiteralPath $file -Raw).Trim() |
        ConvertTo-SecureString
    return [pscredential]::new("local", $encrypted).GetNetworkCredential().Password
}

$envNames = @(
    "ALPACA_PAPER_API_KEY",
    "ALPACA_PAPER_SECRET_KEY",
    "PAPER_JOURNAL_DSN",
    "PAPER_HEARTBEAT_DSN",
    "PAPER_MONITOR_NTFY_TOPIC",
    "PAPER_EXTERNAL_LIVENESS_URL",
    "PAPER_EXTERNAL_LIVENESS_TOKEN"
)

try {
    $monitorPassword = [Uri]::EscapeDataString(
        (Read-HdsSecret "neon-monitor.dpapi")
    )
    $heartbeatPassword = [Uri]::EscapeDataString(
        (Read-HdsSecret "neon-heartbeat.dpapi")
    )

    $env:PAPER_JOURNAL_DSN = "postgresql://hds_paper_win_monitor:${monitorPassword}@${dbHost}:5432/neondb?sslmode=require&channel_binding=require"
    $env:PAPER_HEARTBEAT_DSN = "postgresql://hds_paper_heartbeat:${heartbeatPassword}@${dbHost}:5432/neondb?sslmode=require&channel_binding=require"
    $env:ALPACA_PAPER_API_KEY = Read-HdsSecret "alpaca-paper-key.dpapi"
    $env:ALPACA_PAPER_SECRET_KEY = Read-HdsSecret "alpaca-paper-secret.dpapi"
    $env:PAPER_MONITOR_NTFY_TOPIC = Read-HdsSecret "paper-ntfy-topic.dpapi"
    $env:PAPER_EXTERNAL_LIVENESS_TOKEN = Read-HdsSecret "beat-token.dpapi"
    $env:PAPER_EXTERNAL_LIVENESS_URL = "https://horsesdontstop-liveness-staging.vancastech.workers.dev/v1/heartbeat"

    $runTicks = if ($Continuous) { 0 } else { $MaxTicks }
    if ($TestAlert) {
        Write-Host "SENDING ONE TEST PAPER ALERT"
    }
    elseif ($Diagnose) {
        Write-Host "STARTING SANITIZED PAPER READ-ONLY DIAGNOSTIC (one-shot, not the continuous supervisor)"
    }
    elseif ($Continuous) {
        Write-Host "STARTING CONTINUOUS PAPER READ-ONLY SUPERVISION (Ctrl+C to stop)"
    }
    else {
        Write-Host "STARTING $runTicks PAPER READ-ONLY SUPERVISOR POLLS"
    }
    Write-Host "No broker orders will be submitted."

    Push-Location $repo
    try {
        if ($TestAlert) {
            & $python -m bot.paper_supervisor_loop --test-alert
        }
        elseif ($Diagnose) {
            # Sanitized JSON-lines log of fixed stage tokens, plus a one-alert-
            # per-unchanged-failure-per-day receipt file. No secrets written.
            $collectorCount = Get-HdsServiceInstances "bot.paper_opening_equity_daemon"
            $supervisorCount = Get-HdsServiceInstances "bot.paper_supervisor_loop"
            & $python -m bot.paper_supervisor_loop --diagnose --phase auto `
                --collector-instances $collectorCount `
                --supervisor-instances $supervisorCount `
                --log-file (Join-Path $folder "diagnostic-log.jsonl") `
                --alert-state (Join-Path $folder "diagnostic-alert-state.json")
        }
        elseif ($Continuous) {
            # Continuous supervisor: full broker+Neon poll and lease pulse while
            # the market is open or anything is exposed; broker-verified closed
            # and flat ticks skip Neon (free-tier compute) but still post the
            # Cloudflare liveness signal every 30 seconds.
            & $python -m bot.paper_supervisor_loop --max-ticks 0 --interval-seconds 30 --external-liveness --idle-neon-when-broker-closed-flat
        }
        else {
            & $python -m bot.paper_supervisor_loop --max-ticks $runTicks --interval-seconds 30 --external-liveness
        }
        $supervisorExitCode = $LASTEXITCODE
        if ($null -eq $supervisorExitCode) {
            # A missing native process status must not appear successful.
            $supervisorExitCode = 2
        }
        Write-Host "SUPERVISOR EXIT CODE: $supervisorExitCode"
    }
    finally {
        Pop-Location
    }
}
finally {
    foreach ($name in $envNames) {
        Remove-Item "Env:$name" -ErrorAction SilentlyContinue
    }
    Remove-Variable monitorPassword,heartbeatPassword -ErrorAction SilentlyContinue
}

# Let Task Scheduler distinguish a failed diagnostic (2) from success (0).
# Leave credential/environment cleanup above intact before returning the status.
if ($null -eq $supervisorExitCode) { exit 2 }
exit $supervisorExitCode
