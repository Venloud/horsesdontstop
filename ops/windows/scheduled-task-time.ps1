# Shared local-time trigger builder for PAPER tasks in the Eastern market zone.
# StartBoundary must omit a fixed UTC offset so Task Scheduler applies DST.
function New-HdsEasternWeeklyTrigger {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory)][string[]]$DaysOfWeek,
        [Parameter(Mandatory)][ValidatePattern('^\d{2}:\d{2}$')][string]$At
    )

    if ((Get-TimeZone).Id -ne 'Eastern Standard Time') {
        throw 'PAPER schedules require the Windows Eastern Time zone.'
    }

    $now = Get-Date
    $today = $now.Date
    $parts = $At.Split(':')
    $timeOfDay = [TimeSpan]::new([int]$parts[0], [int]$parts[1], 0)
    foreach ($name in $DaysOfWeek) {
        $day = [System.Enum]::Parse([System.DayOfWeek], $name)
        $daysAhead = (([int]$day - [int]$today.DayOfWeek) + 7) % 7
        if ($daysAhead -eq 0 -and $timeOfDay -le $now.TimeOfDay) { $daysAhead = 7 }
        $firstRun = $today.AddDays($daysAhead).Add($timeOfDay)
        $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek $name -At $firstRun
        $trigger.StartBoundary = $firstRun.ToString(
            'yyyy-MM-ddTHH:mm:ss',
            [Globalization.CultureInfo]::InvariantCulture)
        $trigger
    }
}
