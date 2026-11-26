# Read-only release preflight (no Neon or broker writes).
#   -Stage locked      current safe state
#   -Stage supervised  ready for ONE supervised autonomous entry
param([ValidateSet('locked','supervised')][string]$Stage = 'locked')
$ErrorActionPreference = 'Stop'
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$dbHost = 'ep-summer-salad-b5btnpj6.c-7.us-east-2.aws.neon.tech'
function Read-Dpapi([string]$name) {
    $s = (Get-Content -LiteralPath (Join-Path $folder $name) -Raw).Trim() | ConvertTo-SecureString
    [pscredential]::new('x', $s).GetNetworkCredential().Password
}
$names = 'PAPER_JOURNAL_DSN','PAPER_EXECUTOR_DSN','PAPER_RECONCILER_DSN',
         'ALPACA_PAPER_API_KEY','ALPACA_PAPER_SECRET_KEY','PAPER_ACCOUNT_SHA256'
try {
    $pw = [Uri]::EscapeDataString((Read-Dpapi 'neon-monitor.dpapi'))
    $env:PAPER_JOURNAL_DSN = "postgresql://hds_paper_win_monitor:${pw}@${dbHost}:5432/neondb?sslmode=require&channel_binding=require"
    Remove-Variable pw
    $env:PAPER_EXECUTOR_DSN = Read-Dpapi 'neon-executor-dsn.dpapi'
    $env:PAPER_RECONCILER_DSN = Read-Dpapi 'neon-reconciler-dsn.dpapi'
    $env:ALPACA_PAPER_API_KEY = Read-Dpapi 'alpaca-paper-key.dpapi'
    $env:ALPACA_PAPER_SECRET_KEY = Read-Dpapi 'alpaca-paper-secret.dpapi'
    $env:PAPER_ACCOUNT_SHA256 = (Get-Content -LiteralPath (Join-Path $folder 'paper-account-sha256.txt') -Raw).Trim()
    Push-Location $repo
    try { & (Join-Path $repo '.venv\Scripts\python.exe') -m bot.paper_release_preflight --stage $Stage; exit $LASTEXITCODE }
    finally { Pop-Location }
} finally { foreach ($n in $names) { Remove-Item "Env:$n" -ErrorAction SilentlyContinue } }
