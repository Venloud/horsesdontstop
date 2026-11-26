# Exit-only PAPER position manager (SELL only, never BUY). Dry run unless -Release.
param([switch]$Release)
$ErrorActionPreference = 'Stop'
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
$receipt = Join-Path $folder 'exit-service-runtime.json'
$dbHost = 'ep-summer-salad-b5btnpj6.c-7.us-east-2.aws.neon.tech'
function Read-Dpapi([string]$name) {
    $s = (Get-Content -LiteralPath (Join-Path $folder $name) -Raw).Trim() | ConvertTo-SecureString
    [pscredential]::new('x', $s).GetNetworkCredential().Password
}
$names = 'PAPER_JOURNAL_DSN','ALPACA_PAPER_API_KEY','ALPACA_PAPER_SECRET_KEY',
         'PAPER_ACCOUNT_SHA256','PAPER_MONITOR_NTFY_TOPIC'
$code = 2
try {
    if ($repo -ne 'C:\Users\van02\horsesdontstop') { throw 'Exit service must run from the stable runtime checkout.' }
    $pw = [Uri]::EscapeDataString((Read-Dpapi 'neon-monitor.dpapi'))
    $env:PAPER_JOURNAL_DSN = "postgresql://hds_paper_win_monitor:${pw}@${dbHost}:5432/neondb?sslmode=require&channel_binding=require"
    Remove-Variable pw
    $env:ALPACA_PAPER_API_KEY = Read-Dpapi 'alpaca-paper-key.dpapi'
    $env:ALPACA_PAPER_SECRET_KEY = Read-Dpapi 'alpaca-paper-secret.dpapi'
    $env:PAPER_ACCOUNT_SHA256 = (Get-Content -LiteralPath (Join-Path $folder 'paper-account-sha256.txt') -Raw).Trim()
    if (Test-Path (Join-Path $folder 'paper-ntfy-topic.dpapi')) { $env:PAPER_MONITOR_NTFY_TOPIC = Read-Dpapi 'paper-ntfy-topic.dpapi' }
    $extra = @('--receipt', $receipt); if ($Release) { $extra += '--release' }
    Push-Location $repo
    try { & $python -m bot.paper_exit_service @extra; $code = $LASTEXITCODE }
    finally { Pop-Location }
} catch {
    Write-Host "PAPER EXIT SERVICE BLOCKED ($($_.Exception.GetType().Name)); details withheld to protect credentials."
} finally { foreach ($n in $names) { Remove-Item "Env:$n" -ErrorAction SilentlyContinue } }
exit $code
