# Independent restricted DB login and broker GET-only reconciliation.
# Status writing is deliberately NOT requested. No lease or control writes.
$ErrorActionPreference = 'Stop'
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
function Read-ReconcilerSecret([string]$name) {
    $secure = (Get-Content -LiteralPath (Join-Path $folder $name) -Raw).Trim() | ConvertTo-SecureString
    return [pscredential]::new('local', $secure).GetNetworkCredential().Password
}
$names = @('PAPER_RECONCILER_DSN','ALPACA_RECONCILER_PAPER_API_KEY',
           'ALPACA_RECONCILER_PAPER_SECRET_KEY','PAPER_MONITOR_NTFY_TOPIC','PAPER_RECONCILER_ACCOUNT_SHA256')
try {
    $env:PAPER_RECONCILER_DSN = Read-ReconcilerSecret 'neon-reconciler-dsn.dpapi'
    $env:ALPACA_RECONCILER_PAPER_API_KEY = Read-ReconcilerSecret 'alpaca-paper-key.dpapi'
    $env:ALPACA_RECONCILER_PAPER_SECRET_KEY = Read-ReconcilerSecret 'alpaca-paper-secret.dpapi'
    $env:PAPER_MONITOR_NTFY_TOPIC = Read-ReconcilerSecret 'paper-ntfy-topic.dpapi'
    $env:PAPER_RECONCILER_ACCOUNT_SHA256 = (Get-Content -LiteralPath (Join-Path $folder 'paper-account-sha256.txt') -Raw).Trim()
    Push-Location $repo
    try {
        & (Join-Path $repo '.venv\Scripts\python.exe') -m bot.paper_reconciler_service --continuous --receipt-file (Join-Path $folder 'reconciler-runtime.json')
        exit $LASTEXITCODE
    } finally { Pop-Location }
} catch {
    Write-Host 'BLOCKED: independent PAPER reconciler unavailable; no broker writes.'
    exit 2
} finally {
    foreach ($name in $names) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
}
