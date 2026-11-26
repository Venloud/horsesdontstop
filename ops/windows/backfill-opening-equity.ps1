# Broker-attested opening-equity backfill (use only when the collector missed
# today's 09:30-09:35 window). Reads Alpaca's own 09:30 portfolio-history bar.
#   -DryRun : show the decision without writing
param([switch]$DryRun)
$ErrorActionPreference = 'Stop'
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
function Read-Dpapi([string]$name) {
    $s = (Get-Content -LiteralPath (Join-Path $folder $name) -Raw).Trim() | ConvertTo-SecureString
    [pscredential]::new('x', $s).GetNetworkCredential().Password
}
$names = 'PAPER_EXECUTOR_DSN','ALPACA_PAPER_API_KEY','ALPACA_PAPER_SECRET_KEY'
try {
    $env:PAPER_EXECUTOR_DSN = Read-Dpapi 'neon-executor-dsn.dpapi'
    $env:ALPACA_PAPER_API_KEY = Read-Dpapi 'alpaca-paper-key.dpapi'
    $env:ALPACA_PAPER_SECRET_KEY = Read-Dpapi 'alpaca-paper-secret.dpapi'
    $extra = @(); if ($DryRun) { $extra = @('--dry-run') }
    Push-Location $repo
    try { & (Join-Path $repo '.venv\Scripts\python.exe') -m bot.paper_opening_backfill @extra; exit $LASTEXITCODE }
    finally { Pop-Location }
} finally { foreach ($n in $names) { Remove-Item "Env:$n" -ErrorAction SilentlyContinue } }
