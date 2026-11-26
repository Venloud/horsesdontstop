# Read-only local freshness monitor. Does not import the autopilot executor.
$ErrorActionPreference = 'Stop'
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
$log = Join-Path $folder 'autopilot-watchdog.log'
function Read-Dpapi([string]$name) {
    $s = (Get-Content -LiteralPath (Join-Path $folder $name) -Raw).Trim() | ConvertTo-SecureString
    [pscredential]::new('x', $s).GetNetworkCredential().Password
}
$names = 'ALPACA_PAPER_API_KEY','ALPACA_PAPER_SECRET_KEY','PAPER_MONITOR_NTFY_TOPIC'
$code = 2
try {
    $env:ALPACA_PAPER_API_KEY = Read-Dpapi 'alpaca-paper-key.dpapi'
    $env:ALPACA_PAPER_SECRET_KEY = Read-Dpapi 'alpaca-paper-secret.dpapi'
    if (Test-Path (Join-Path $folder 'paper-ntfy-topic.dpapi')) {
        $env:PAPER_MONITOR_NTFY_TOPIC = Read-Dpapi 'paper-ntfy-topic.dpapi'
    }
    Push-Location $repo
    try {
        $out = & $python -m bot.autopilot_watchdog --folder $folder 2>&1 | Out-String
        $code = $LASTEXITCODE
        Write-Host $out
        Add-Content -LiteralPath $log -Value ("==== " + (Get-Date -Format o) + "`n" + $out)
    } finally { Pop-Location }
} catch {
    Write-Host 'WATCHDOG LAUNCH FAILED; error details withheld.'
} finally { foreach ($n in $names) { Remove-Item "Env:$n" -ErrorAction SilentlyContinue } }
exit $code
