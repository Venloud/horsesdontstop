# Install the exit-only PAPER task: every 5 minutes, 09:31-16:00 ET, weekdays.
# It can only SELL bot positions (take profit / trailing / time limit); it never buys.
# Dry run by default; pass -Release only after reviewing dry-run receipts.
# Never replaces an existing task.
param([switch]$Release)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scheduled-task-time.ps1')
$task = 'HorsesDontStop-PAPER-Exits'
if (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue) {
    throw 'Existing exit task requires inspection; not overwritten. Unregister it yourself first.'
}
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if ($repo -ne 'C:\Users\van02\horsesdontstop') { throw 'Install from the stable runtime checkout only.' }
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
foreach ($name in @('neon-monitor.dpapi','alpaca-paper-key.dpapi','alpaca-paper-secret.dpapi','paper-account-sha256.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $folder $name))) { throw 'Required exit-service credential or account pin missing.' }
}
$runner = Join-Path $PSScriptRoot 'run-paper-exit-service.ps1'
$arg = '-NoProfile -ExecutionPolicy RemoteSigned -File "' + $runner + '"'
if ($Release) { $arg += ' -Release' }
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg -WorkingDirectory $repo
$weekdays = @('Monday','Tuesday','Wednesday','Thursday','Friday')
$triggers = @(New-HdsEasternWeeklyTrigger -DaysOfWeek $weekdays -At '09:31')
foreach ($t in $triggers) {
    $t.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Hours 6 -Minutes 30)).Repetition
}
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
$mode = $(if ($Release) { 'RELEASED (submits exit SELLs)' } else { 'DRY RUN (no orders)' })
Register-ScheduledTask -TaskName $task -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Description "Exit-only PAPER manager, $mode. SELL only; never buys." | Out-Null
Write-Host "Registered $task in $mode mode. Windows session must remain signed in and awake."
