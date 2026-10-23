# Register the two read-only PAPER monitoring tasks, replacing any earlier
# hand-registered versions of the SAME names (no duplicates are created):
#   HorsesDontStop-PAPER-Supervisor   continuous 30-second read-only supervisor
#   HorsesDontStop-PAPER-Diagnostic   one-shot diagnostic, weekdays 09:21 + 09:36
# Does not touch HorsesDontStop-PAPER-OpeningEquity, credentials or trading.
$ErrorActionPreference = "Stop"
$runner = (Resolve-Path (Join-Path $PSScriptRoot "run-paper-supervisor.ps1")).Path
$python = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path ".venv\Scripts\python.exe"
$secrets = Join-Path $env:LOCALAPPDATA "horsesdontstop"
foreach ($filename in @("alpaca-paper-key.dpapi", "alpaca-paper-secret.dpapi",
                        "neon-monitor.dpapi", "neon-heartbeat.dpapi",
                        "paper-ntfy-topic.dpapi", "beat-token.dpapi")) {
    if (-not (Test-Path -LiteralPath (Join-Path $secrets $filename))) {
        throw "Missing encrypted credential $filename. Tasks NOT registered."
    }
}
if (-not (Test-Path -LiteralPath $python)) { throw "Python environment missing." }
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$weekdays = @("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")

function New-HdsAction([string]$mode) {
    New-ScheduledTaskAction -Execute "powershell.exe" -Argument (
        '-NoProfile -ExecutionPolicy RemoteSigned -File "' + $runner + '" ' + $mode)
}

# Continuous supervisor: starts at logon and is re-asserted at 09:10 on
# weekdays; IgnoreNew keeps exactly one instance; restarts after a crash.
$supervisorSettings = New-ScheduledTaskSettingsSet -RestartCount 3 `
    -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "HorsesDontStop-PAPER-Supervisor" `
    -Action (New-HdsAction "-Continuous") `
    -Trigger @((New-ScheduledTaskTrigger -AtLogOn -User $user),
               (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "09:10")) `
    -Principal $principal -Settings $supervisorSettings -Force `
    -Description "Continuous read-only PAPER supervisor. No orders." | Out-Null

# One-shot diagnostic: 09:21 pre-open dependency check, 09:36 full check.
# The Python side picks the stage set from New York time.
$diagnosticSettings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -MultipleInstances IgnoreNew `
    -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "HorsesDontStop-PAPER-Diagnostic" `
    -Action (New-HdsAction "-Diagnose") `
    -Trigger @((New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "09:21"),
               (New-ScheduledTaskTrigger -Weekly -DaysOfWeek $weekdays -At "09:36")) `
    -Principal $principal -Settings $diagnosticSettings -Force `
    -Description "One-shot read-only PAPER diagnostic with log and phone alert. No orders." | Out-Null

Write-Host "Registered HorsesDontStop-PAPER-Supervisor (logon + weekdays 09:10, one instance)."
Write-Host "Registered HorsesDontStop-PAPER-Diagnostic (weekdays 09:21 and 09:36)."
Write-Host "Windows timezone must be Eastern; the Python/Neon gates are authoritative."
