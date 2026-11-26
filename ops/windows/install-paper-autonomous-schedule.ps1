# RELEASE STEP ONLY: run after the supervised lifecycle proof passes.
# Replaces the prepare-only executor triggers with autonomous runs every
# 5 minutes, 09:36-15:56 New York, weekdays. Entries are still limited by the
# Neon policy window (09:35-15:30); later runs perform risk-reducing
# maintenance only (stale/late entry cancel, unprotected-share flatten).
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'scheduled-task-time.ps1')
$task = 'HorsesDontStop-PAPER-Executor'
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if ($repo -ne 'C:\Users\van02\horsesdontstop') { throw 'Install only from the stable runtime checkout.' }
$runner = Join-Path $PSScriptRoot 'run-paper-executor.ps1'
$existing = Get-ScheduledTask -TaskName $task -ErrorAction Stop
$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument ('-NoProfile -ExecutionPolicy RemoteSigned -File "' + $runner + '" -Autonomous') `
    -WorkingDirectory $repo
$triggers = @()
foreach ($day in @('Monday','Tuesday','Wednesday','Thursday','Friday')) {
    $t = New-HdsEasternWeeklyTrigger -DaysOfWeek @($day) -At '09:36'
    $t.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) `
        -RepetitionInterval (New-TimeSpan -Minutes 5) `
        -RepetitionDuration (New-TimeSpan -Hours 6 -Minutes 21)).Repetition
    $triggers += $t
}
Set-ScheduledTask -TaskName $task -Action $action -Trigger $triggers | Out-Null
Write-Host 'Executor now runs -Autonomous every 5 minutes, 09:36-15:56 NY weekdays.'
Get-ScheduledTask -TaskName $task | Select-Object TaskName, State
