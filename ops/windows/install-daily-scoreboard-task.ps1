# Install the after-close market recorder + strategy scoreboard (weekdays 16:30 ET).
# Market-data reads only; never places orders. Never replaces an existing task.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scheduled-task-time.ps1')
$task = 'HorsesDontStop-Daily-Scoreboard'
if (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue) {
    throw 'Existing scoreboard task requires inspection; not overwritten.'
}
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$runner = Join-Path $PSScriptRoot 'run-daily-scoreboard.ps1'
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy RemoteSigned -File "' + $runner + '"') -WorkingDirectory $repo
$triggers = @(New-HdsEasternWeeklyTrigger -DaysOfWeek @('Monday','Tuesday','Wednesday','Thursday','Friday') -At '16:30')
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $task -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Description 'Record market day and score every strategy. Read-only market data; no orders.' | Out-Null
Write-Host "Registered $task (weekdays 16:30 ET)."
