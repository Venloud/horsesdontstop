# Separate PAPER opening-equity collector. Broker reads; time-gated Neon INSERT only.
# Current Windows user DPAPI is required; no order submissions or control changes.
$ErrorActionPreference = "Stop"
$folder = Join-Path $env:LOCALAPPDATA "horsesdontstop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$python = Join-Path $repo ".venv\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $python)) {
    throw "PAPER equity collector virtual environment missing."
}
function Read-HdsEquitySecret([string]$name) {
    $path = Join-Path $folder $name
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Missing PAPER equity Windows DPAPI credential: $name"
    }
    $secure = (Get-Content -LiteralPath $path -Raw).Trim() | ConvertTo-SecureString
    return [pscredential]::new("local", $secure).GetNetworkCredential().Password
}
$names = @("ALPACA_PAPER_API_KEY", "ALPACA_PAPER_SECRET_KEY",
           "PAPER_EXECUTOR_DSN", "PAPER_MONITOR_NTFY_TOPIC")
try {
    $env:ALPACA_PAPER_API_KEY = Read-HdsEquitySecret "alpaca-paper-key.dpapi"
    $env:ALPACA_PAPER_SECRET_KEY = Read-HdsEquitySecret "alpaca-paper-secret.dpapi"
    $env:PAPER_EXECUTOR_DSN = Read-HdsEquitySecret "neon-executor-dsn.dpapi"
    $env:PAPER_MONITOR_NTFY_TOPIC = Read-HdsEquitySecret "paper-ntfy-topic.dpapi"
    Write-Host "STARTING CONTINUOUS PAPER OPENING-EQUITY COLLECTOR"
    Write-Host "Broker reads only. Neon time-gated equity INSERT only. No orders."
    Push-Location $repo
    try {
        & $python -m bot.paper_opening_equity_daemon --interval-seconds 15
        $code = $LASTEXITCODE
        Write-Host "EQUITY COLLECTOR EXIT CODE: $code"
        exit $code
    }
    finally {
        Pop-Location
    }
}
catch {
    # Never echo driver errors or secret-bearing exception text.
    Write-Host "BLOCKED: PAPER equity collector setup or execution failed."
    exit 2
}
finally {
    foreach ($name in $names) {
        Remove-Item "Env:$name" -ErrorAction SilentlyContinue
    }
}
