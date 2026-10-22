# Read-only Alpaca PAPER/Neon/Cloudflare supervisor.
# Uses existing Windows-user DPAPI credential files, never stores plaintext.
# Does NOT submit orders, change database controls, or enable trading.
param(
    [ValidateRange(1, 5)]
    [int]$MaxTicks = 3
)

$ErrorActionPreference = "Stop"
$folder = Join-Path $env:LOCALAPPDATA "horsesdontstop"
$repo = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
$dbHost = "ep-summer-salad-b5btnpj6.c-7.us-east-2.aws.neon.tech"
$python = Join-Path $repo ".venv\Scripts\python.exe"

if (-not (Test-Path -LiteralPath $python)) {
    throw "Existing HorsesDontStop virtual environment not found. No supervisor started."
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

    Write-Host "STARTING $MaxTicks PAPER READ-ONLY SUPERVISOR POLLS"
    Write-Host "No broker orders will be submitted."

    Push-Location $repo
    try {
        & $python -m bot.paper_supervisor_loop --max-ticks $MaxTicks --interval-seconds 30 --external-liveness
        $supervisorExitCode = $LASTEXITCODE
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
