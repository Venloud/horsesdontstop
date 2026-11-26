# Install a separate, read-only autopilot health check. Never replace an existing task.
# Alpaca's PAPER market clock determines holidays, shortened sessions and market-open time.
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scheduled-task-time.ps1')
$task = 'HorsesDontStop-Autopilot-Watchdog'
if (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue) {
    throw 'Existing watchdog task requires inspection, not replacement.'
}
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
foreach ($name in @('alpaca-paper-key.dpapi','alpaca-paper-secret.dpapi')) {
    if (-not (Test-Path -LiteralPath (Join-Path $folder $name))) {
        throw 'Missing existing PAPER credential file.'
    }
}
$runner = Join-Path $PSScriptRoot 'run-autopilot-watchdog.ps1'
$arg = '-NoProfile -ExecutionPolicy RemoteSigned -File "' + $runner + '"'
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $arg -WorkingDirectory $repo
$triggers = @()
foreach ($day in @('Monday','Tuesday','Wednesday','Thursday','Friday')) {
    $t = New-HdsEasternWeeklyTrigger -DaysOfWeek @($day) -At '09:30'
    $t.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 5) `
        -RepetitionDuration (New-TimeSpan -Hours 6 -Minutes 45)).Repetition
    $triggers += $t
}
$settings = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 3) `
    -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $task -Action $action -Trigger $triggers `
    -Principal $principal -Settings $settings `
    -Description 'Read-only Alpaca PAPER autopilot receipt freshness monitor; 15-minute threshold, alerts only.' | Out-Null
Write-Host "Registered $task. Scheduled independently of the autopilot. Windows must stay signed in and awake."
