# Autonomous PAPER portfolio manager. Dry run unless -Trade. No Neon, no Cloudflare.
param([switch]$Trade)
$ErrorActionPreference = 'Stop'
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
$log = Join-Path $folder 'autopilot.log'
function Read-Dpapi([string]$name) {
    $s = (Get-Content -LiteralPath (Join-Path $folder $name) -Raw).Trim() | ConvertTo-SecureString
    [pscredential]::new('x', $s).GetNetworkCredential().Password
}
$names = 'ALPACA_PAPER_API_KEY','ALPACA_PAPER_SECRET_KEY','PAPER_ACCOUNT_SHA256','PAPER_MONITOR_NTFY_TOPIC'
$code = 2
try {
    $env:ALPACA_PAPER_API_KEY = Read-Dpapi 'alpaca-paper-key.dpapi'
    $env:ALPACA_PAPER_SECRET_KEY = Read-Dpapi 'alpaca-paper-secret.dpapi'
    $env:PAPER_ACCOUNT_SHA256 = (Get-Content -LiteralPath (Join-Path $folder 'paper-account-sha256.txt') -Raw).Trim()
    if (Test-Path (Join-Path $folder 'paper-ntfy-topic.dpapi')) { $env:PAPER_MONITOR_NTFY_TOPIC = Read-Dpapi 'paper-ntfy-topic.dpapi' }
    $extra = @(); if ($Trade) { $extra += '--trade' }
    Push-Location $repo
    try {
        $out = & $python -m bot.autopilot @extra 2>&1 | Out-String
        $code = $LASTEXITCODE
        Write-Host $out
        Add-Content -LiteralPath $log -Value ("==== " + (Get-Date -Format o) + "`n" + $out)
    } finally { Pop-Location }
} catch {
    Write-Host "AUTOPILOT LAUNCH FAILED ($($_.Exception.GetType().Name)); details withheld to protect credentials."
} finally { foreach ($n in $names) { Remove-Item "Env:$n" -ErrorAction SilentlyContinue } }
exit $code
