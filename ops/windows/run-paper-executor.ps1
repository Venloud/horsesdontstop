# Bounded PAPER executor coordinator. Prepare mode can reserve one exact
# proposal; dispatch mode is separately hard-gated in Python and the Neon DB.
param(
    [switch]$Dispatch,
    [string]$ProposalPath
)

$ErrorActionPreference = 'Stop'
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
$dbHost = 'ep-summer-salad-b5btnpj6.c-7.us-east-2.aws.neon.tech'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$python = Join-Path $repo '.venv\Scripts\python.exe'
$receipt = Join-Path $folder 'executor-runtime.json'
$stage = 'validate_stable_runtime_path'
$runId = [Guid]::NewGuid().ToString('N')
$started = [DateTime]::UtcNow.ToString('o')

function Write-HdsRuntimeReceipt([string]$status, [int]$code, [string]$errorType = $null) {
    try {
        $parent = Split-Path -Parent $receipt
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
        $record = [ordered]@{
            schema = 1; run_id = $runId; observed_at_utc = [DateTime]::UtcNow.ToString('o')
            started_at_utc = $started; mode = $(if ($Dispatch) { 'dispatch' } else { 'prepare' })
            stage = $stage; status = $status; error_type = $errorType
            exit_code = $code; broker_order_attempted = [bool]$Dispatch
        }
        $temp = "$receipt.$runId.tmp"
        $record | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $temp -Encoding UTF8
        Move-Item -LiteralPath $temp -Destination $receipt -Force
    } catch { }
}

function Read-HdsExecutorSecret([string]$name) {
    $path = Join-Path $folder $name
    if (-not (Test-Path -LiteralPath $path)) { throw "Missing encrypted executor credential: $name" }
    $secure = (Get-Content -LiteralPath $path -Raw).Trim() | ConvertTo-SecureString
    return [pscredential]::new('local', $secure).GetNetworkCredential().Password
}

$names = @('ALPACA_PAPER_API_KEY','ALPACA_PAPER_SECRET_KEY','PAPER_JOURNAL_DSN',
           'PAPER_EXECUTOR_DSN','PAPER_RECONCILER_DSN','PAPER_ACCOUNT_SHA256',
           'PAPER_SUPERVISOR_RECEIPT','PAPER_SUPERVISOR_ROOT_PID')
try {
    if ($repo -ne 'C:\Users\van02\horsesdontstop') { $stage='validate_stable_runtime_path'; throw 'Executor must run from the stable runtime checkout.' }
    if (-not (Test-Path -LiteralPath $python)) { $stage='validate_stable_python'; throw 'Stable runtime Python environment is missing.' }
    $stage = 'validate_protected_configuration_files'
    foreach ($name in @('alpaca-paper-key.dpapi','alpaca-paper-secret.dpapi',
                        'neon-monitor.dpapi','neon-executor-dsn.dpapi',
                        'neon-reconciler-dsn.dpapi','paper-account-sha256.txt')) {
        if (-not (Test-Path -LiteralPath (Join-Path $folder $name))) {
            throw "Missing existing protected runtime file: $name"
        }
    }
    $stage = 'decrypt_alpaca_paper_key'
    $env:ALPACA_PAPER_API_KEY = Read-HdsExecutorSecret 'alpaca-paper-key.dpapi'
    $stage = 'decrypt_alpaca_paper_secret'
    $env:ALPACA_PAPER_SECRET_KEY = Read-HdsExecutorSecret 'alpaca-paper-secret.dpapi'
    $stage = 'decrypt_monitor_database_credential'
    $monitorPassword = [Uri]::EscapeDataString(
        (Read-HdsExecutorSecret 'neon-monitor.dpapi'))
    $env:PAPER_JOURNAL_DSN = "postgresql://hds_paper_win_monitor:${monitorPassword}@${dbHost}:5432/neondb?sslmode=require&channel_binding=require"
    $stage = 'decrypt_executor_database_credential'
    $env:PAPER_EXECUTOR_DSN = Read-HdsExecutorSecret 'neon-executor-dsn.dpapi'
    $stage = 'decrypt_reconciler_database_credential'
    $env:PAPER_RECONCILER_DSN = Read-HdsExecutorSecret 'neon-reconciler-dsn.dpapi'
    $stage = 'read_paper_account_identity_pin'
    $env:PAPER_ACCOUNT_SHA256 = (Get-Content -LiteralPath (Join-Path $folder 'paper-account-sha256.txt') -Raw).Trim()
    $env:PAPER_SUPERVISOR_RECEIPT = Join-Path $folder 'supervisor-runtime.json'
    $stage = 'identify_single_live_supervisor_process'
    $supervisors = @(Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction Stop |
        Where-Object {
            $_.CommandLine -and
            $_.CommandLine -match '-m\s+bot\.paper_supervisor_loop(\s|$)' -and
            $_.CommandLine -notmatch '--diagnose'
        })
    $supervisorIds = @($supervisors | ForEach-Object { $_.ProcessId })
    $supervisorRoots = @($supervisors | Where-Object {
        $supervisorIds -notcontains $_.ParentProcessId
    })
    if ($supervisorRoots.Count -ne 1) {
        throw 'Exactly one continuous supervisor process root is required.'
    }
    $env:PAPER_SUPERVISOR_ROOT_PID = [string]$supervisorRoots[0].ProcessId
    $output = Join-Path $folder 'paper-executor\latest.json'
    $stage = 'invoke_python_executor'
    Push-Location $repo
    try {
        if ($Dispatch) {
            if (-not $ProposalPath -or -not (Test-Path -LiteralPath $ProposalPath)) {
                throw 'Dispatch requires an existing exact prepared proposal file.'
            }
            & $python -m bot.paper_executor_service --dispatch-file $ProposalPath --output $output --runtime-receipt $receipt
        } else {
            & $python -m bot.paper_executor_service --prepare --output $output --runtime-receipt $receipt
        }
        $result = $LASTEXITCODE
        if ($null -eq $result) { $result = 2 }
        Write-Host "PAPER EXECUTOR EXIT CODE: $result; no secret values displayed."
        $script:executorExitCode = $result
    } finally { Pop-Location }
} catch {
    $exceptionType = $_.Exception.GetType().Name
    Write-HdsRuntimeReceipt 'error' 2 $exceptionType
    Write-Host "PAPER EXECUTOR BLOCKED at stage '$stage' (error type: $exceptionType); details withheld to protect credentials."
    $script:executorExitCode = 2
} finally {
    Remove-Variable monitorPassword -ErrorAction SilentlyContinue
    foreach ($name in $names) { Remove-Item "Env:$name" -ErrorAction SilentlyContinue }
}
if ($null -eq $executorExitCode) { $executorExitCode = 2 }
exit $executorExitCode
