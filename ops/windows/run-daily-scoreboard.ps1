# Record today's market bars, then replay every strategy on the history.
# Market data reads only; never places orders.
$ErrorActionPreference = 'Stop'
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
function Read-Dpapi([string]$name) {
    $s = (Get-Content -LiteralPath (Join-Path $folder $name) -Raw).Trim() | ConvertTo-SecureString
    [pscredential]::new('x', $s).GetNetworkCredential().Password
}
$code = 2
try {
    $env:ALPACA_PAPER_API_KEY = Read-Dpapi 'alpaca-paper-key.dpapi'
    $env:ALPACA_PAPER_SECRET_KEY = Read-Dpapi 'alpaca-paper-secret.dpapi'
    Push-Location $repo
    try {
        & $python -m bot.daily_market_recorder; $code = $LASTEXITCODE
        if ($code -eq 0) { & $python -m bot.strategy_scoreboard; $code = $LASTEXITCODE }
    } finally { Pop-Location }
} catch {
    Write-Host "DAILY SCOREBOARD BLOCKED ($($_.Exception.GetType().Name)); details withheld."
} finally { Remove-Item Env:ALPACA_PAPER_API_KEY, Env:ALPACA_PAPER_SECRET_KEY -ErrorAction SilentlyContinue }
exit $code
