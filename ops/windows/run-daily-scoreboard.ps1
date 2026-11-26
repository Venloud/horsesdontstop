# Nightly research (market data + SEC filings reads only; never places orders):
#   record today's bars -> crawl new insider purchases -> strategy engine
#   (research, champion, autopilot-settings.json) -> legacy scoreboard.
# -InsiderOnly: morning refresh of insider purchases and live signals.
param([switch]$InsiderOnly)
$ErrorActionPreference = 'Stop'
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
$log = Join-Path $folder 'nightly-research.log'
function Read-Dpapi([string]$name) {
    $s = (Get-Content -LiteralPath (Join-Path $folder $name) -Raw).Trim() | ConvertTo-SecureString
    [pscredential]::new('x', $s).GetNetworkCredential().Password
}
function Invoke-Step([string]$label, [string[]]$pyArgs) {
    $out = & $python @pyArgs 2>&1 | Out-String
    $rc = $LASTEXITCODE
    Add-Content -LiteralPath $log -Value ("==== " + (Get-Date -Format o) + " $label (exit $rc)`n" + $out)
    Write-Host $out
    return $rc
}
$code = 2
try {
    $env:ALPACA_PAPER_API_KEY = Read-Dpapi 'alpaca-paper-key.dpapi'
    $env:ALPACA_PAPER_SECRET_KEY = Read-Dpapi 'alpaca-paper-secret.dpapi'
    Push-Location $repo
    try {
        if ($InsiderOnly) {
            $code = Invoke-Step 'insider' @('-m', 'bot.insider_signals', '--daily', '4')
        } else {
            $code = Invoke-Step 'recorder' @('-m', 'bot.daily_market_recorder')
            if ($code -eq 0) {
                Invoke-Step 'insider' @('-m', 'bot.insider_signals', '--daily', '5') | Out-Null
                $code = Invoke-Step 'strategy engine' @('-m', 'bot.strategy_engine')
                Invoke-Step 'legacy scoreboard' @('-m', 'bot.strategy_scoreboard') | Out-Null
            }
        }
    } finally { Pop-Location }
} catch {
    Write-Host "NIGHTLY RESEARCH BLOCKED ($($_.Exception.GetType().Name)); details withheld."
} finally { Remove-Item Env:ALPACA_PAPER_API_KEY, Env:ALPACA_PAPER_SECRET_KEY -ErrorAction SilentlyContinue }
exit $code
