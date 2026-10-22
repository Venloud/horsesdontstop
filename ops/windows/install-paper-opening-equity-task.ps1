# Install a user-session Windows Task Scheduler task, separate from supervisor.
# Reuses the existing Python collector and current-user DPAPI credential store.
$ErrorActionPreference = "Stop"
$runner = (Resolve-Path (Join-Path $PSScriptRoot "run-paper-opening-equity.ps1")).Path
$python = Join-Path (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path ".venv\Scripts\python.exe"
$secrets = Join-Path $env:LOCALAPPDATA "horsesdontstop"
foreach ($filename in @("alpaca-paper-key.dpapi", "alpaca-paper-secret.dpapi",
                        "neon-executor-dsn.dpapi", "paper-ntfy-topic.dpapi")) {
    if (-not (Test-Path -LiteralPath (Join-Path $secrets $filename))) {
        throw "Missing encrypted credential $filename. Task NOT registered."
    }
}
if (-not (Test-Path -LiteralPath $python)) { throw "Python environment missing." }
$task = "HorsesDontStop-PAPER-OpeningEquity"
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$action = New-ScheduledTaskAction -Execute "powershell.exe" -Argument ('-NoProfile -ExecutionPolicy RemoteSigned -File "' + $runner + '"')
$triggers = @(
    (New-ScheduledTaskTrigger -AtLogOn -User ([Security.Principal.WindowsIdentity]::GetCurrent().Name)),
    (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At "09:20")
)
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $task -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Description "PAPER opening equity, separate from read-only supervisor. No orders." -Force | Out-Null
Write-Host "Registered $task for interactive Windows user (logon and weekdays 09:20 local)."
Write-Host "Collector requires Windows session signed in and PC awake/network-connected."
Write-Host "Verify Windows timezone is Eastern; the Python/Neon market-open gates are authoritative."
