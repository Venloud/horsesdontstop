# Install one bounded, prepare-only PAPER executor task from the stable checkout.
# It can reserve at most one proposal. It never dispatches or changes controls.
$ErrorActionPreference = 'Stop'
$taskTimeHelper = Join-Path $PSScriptRoot 'scheduled-task-time.ps1'
. $taskTimeHelper
$task = 'HorsesDontStop-PAPER-Executor'
if (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue) {
    throw 'Executor task already exists; inspect it before any update.'
}
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if ($repo -ne 'C:\Users\van02\horsesdontstop') { throw 'Install only from the stable runtime checkout.' }
$runner = Join-Path $PSScriptRoot 'run-paper-executor.ps1'
$python = Join-Path $repo '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $runner) -or -not (Test-Path -LiteralPath $python)) {
    throw 'Stable executor runner or Python environment missing.'
}
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
foreach ($name in @('alpaca-paper-key.dpapi','alpaca-paper-secret.dpapi',
                    'neon-monitor.dpapi','neon-executor-dsn.dpapi',
                    'neon-reconciler-dsn.dpapi','paper-account-sha256.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $folder $name))) {
        throw "Required protected runtime file is missing: $name"
    }
}
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy RemoteSigned -File "' + $runner + '"') `
    -WorkingDirectory $repo
$triggers = @()
foreach ($day in @('Monday','Tuesday','Wednesday','Thursday','Friday')) {
    foreach ($at in @('09:36','09:41','09:46')) {
        $triggers += New-HdsEasternWeeklyTrigger -DaysOfWeek @($day) -At $at
    }
}
$settings = New-ScheduledTaskSettingsSet -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 2) `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 8) `
    -MultipleInstances IgnoreNew -StartWhenAvailable `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $task -Action $action -Trigger $triggers `
    -Principal $principal -Settings $settings `
    -Description 'Bounded Alpaca PAPER candidate/preparation coordinator; never dispatches orders.' | Out-Null
Write-Host 'Registered one prepare-only PAPER executor task; submission release remains false.'
Get-ScheduledTask -TaskName $task | Select-Object TaskName,State
