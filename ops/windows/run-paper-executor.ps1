# Bounded PAPER executor coordinator. Prepare mode can reserve one exact
# proposal; dispatch mode is separately hard-gated in Python and the Neon DB.
param(
    [switch]$Dispatch,
    [string]$ProposalPath
)

$ErrorActionPreference = 'Stop'
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
if ($repo -ne 'C:\Users\van02\horsesdontstop') { throw 'Executor must run from the stable runtime checkout.' }
if (-not (Test-Path -LiteralPath $python)) { throw 'Stable runtime Python environment is missing.' }

function Read-HdsExecutorSecret([string]$name) {
    $path = Join-Path $folder $name
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing encrypted executor credential: $name" }
    $secure = (Get-Content -LiteralPath $path -Raw).Trim() | ConvertTo-SecureString
    return [pscredential]::new('local', $secure).GetNetworkCredential().Password
}

$names = @('ALPACA_PAPER_API_KEY','ALPACA_PAPER_SECRET_KEY','PAPER_JOURNAL_DSN',
           'PAPER_EXECUTOR_DSN','PAPER_RECONCILER_DSN','PAPER_ACCOUNT_SHA256',
           'PAPER_SUPERVISOR_RECEIPT')
try {
    foreach ($name in @('alpaca-paper-key.dpapi','alpaca-paper-secret.dpapi',
                        'neon-monitor.dpapi','neon-executor-dsn.dpapi',
                        'neon-reconciler-dsn.dpapi','paper-account-sha256.txt')) {
        if (-not (Test-Path -LiteralPath (Join-Path $folder $name))) {
            throw "Missing existing protected runtime file: $name"
        }
    }
    $env:ALPACA_PAPER_API_KEY = Read-HdsExecutorSecret 'alpaca-paper-key.dpapi'
    $env:ALPACA_PAPER_SECRET_KEY = Read-HdsExecutorSecret 'alpaca-paper-secret.dpapi'
    $env:PAPER_JOURNAL_DSN = Read-HdsExecutorSecret 'neon-monitor.dpapi'
    $env:PAPER_EXECUTOR_DSN = Read-HdsExecutorSecret 'neon-executor-dsn.dpapi'
    $env:PAPER_RECONCILER_DSN = Read-HdsExecutorSecret 'neon-reconciler-dsn.dpapi'
    $env:PAPER_ACCOUNT_SHA256 = (Get-Content -LiteralPath (Join-Path $folder 'paper-account-sha256.txt') -Raw).Trim()
    $env:PAPER_SUPERVISOR_RECEIPT = Join-Path $folder 'supervisor-runtime.json'
    $output = Join-Path $folder 'paper-executor\latest.json'
    Push-Location $repo
    try {
        if ($Dispatch) {
            if (-not $ProposalPath -or -not (Test-Path -LiteralPath $ProposalPath)) {
                throw 'Dispatch requires an existing exact prepared proposal file.'
            }
            & $python -m bot.paper_executor_service --dispatch-file $ProposalPath --output $output
        } else {
            & $python -m bot.paper_executor_service --prepare --output $output
        }
        $result = $LASTEXITCODE
        if ($null -eq $result) { $result = 2 }
        Write-Host "PAPER EXECUTOR EXIT CODE: $result; no secret values displayed."
        exit $result
    } finally { Pop-Location }
} catch {
    Write-Host 'PAPER EXECUTOR BLOCKED: required protected configuration or runtime evidence unavailable.'
    exit 2
} finally {
    foreach ($name in $names) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
}
