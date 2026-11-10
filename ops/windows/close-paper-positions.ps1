# Ask the bot to close its PAPER positions with the guarded exit routine
# (cancel stop leg -> confirm -> one market SELL -> confirm fill; re-protects
# automatically if the SELL fails).
#   -DryRun        show what would be closed
#   -Symbol WMT    close only that symbol
param([switch]$DryRun, [string]$Symbol)
$ErrorActionPreference = 'Stop'
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$dbHost = 'ep-summer-salad-b5btnpj6.c-7.us-east-2.aws.neon.tech'
function Read-Dpapi([string]$name) {
    $s = (Get-Content -LiteralPath (Join-Path $folder $name) -Raw).Trim() | ConvertTo-SecureString
    [pscredential]::new('x', $s).GetNetworkCredential().Password
}
$names = 'PAPER_JOURNAL_DSN','ALPACA_PAPER_API_KEY','ALPACA_PAPER_SECRET_KEY',
         'PAPER_ACCOUNT_SHA256','PAPER_MONITOR_NTFY_TOPIC'
try {
    $pw = [Uri]::EscapeDataString((Read-Dpapi 'neon-monitor.dpapi'))
    $env:PAPER_JOURNAL_DSN = "postgresql://hds_paper_win_monitor:${pw}@${dbHost}:5432/neondb?sslmode=require&channel_binding=require"
    Remove-Variable pw
    $env:ALPACA_PAPER_API_KEY = Read-Dpapi 'alpaca-paper-key.dpapi'
    $env:ALPACA_PAPER_SECRET_KEY = Read-Dpapi 'alpaca-paper-secret.dpapi'
    $env:PAPER_ACCOUNT_SHA256 = (Get-Content -LiteralPath (Join-Path $folder 'paper-account-sha256.txt') -Raw).Trim()
    if (Test-Path (Join-Path $folder 'paper-ntfy-topic.dpapi')) { $env:PAPER_MONITOR_NTFY_TOPIC = Read-Dpapi 'paper-ntfy-topic.dpapi' }
    $extra = @(); if ($DryRun) { $extra += '--dry-run' }; if ($Symbol) { $extra += @('--symbol', $Symbol) }
    Push-Location $repo
    try { & (Join-Path $repo '.venv\Scripts\python.exe') -m bot.paper_close_positions @extra; exit $LASTEXITCODE }
    finally { Pop-Location }
} finally { foreach ($n in $names) { Remove-Item "Env:$n" -ErrorAction SilentlyContinue } }
