# Install only the separate observe-only reconciler task; never replace a task.
$ErrorActionPreference = 'Stop'
$task = 'HorsesDontStop-PAPER-Reconciler'
if (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue) {
    throw 'Existing reconciler task requires inspection; not overwritten.'
}
$repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
if ($repo -ne 'C:\Users\van02\horsesdontstop') { throw 'Install from the stable runtime checkout only.' }
$folder = Join-Path $env:LOCALAPPDATA 'horsesdontstop'
foreach ($name in @('neon-reconciler-dsn.dpapi','alpaca-paper-key.dpapi','alpaca-paper-secret.dpapi','paper-ntfy-topic.dpapi','paper-account-sha256.txt')) {
    if (-not (Test-Path -LiteralPath (Join-Path $folder $name))) { throw 'Required reconciler credential or account pin missing.' }
}
$runner = Join-Path $PSScriptRoot 'run-paper-reconciler.ps1'
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument ('-NoProfile -ExecutionPolicy RemoteSigned -File "' + $runner + '"') -WorkingDirectory $repo
$triggers = @((New-ScheduledTaskTrigger -AtLogOn -User $user), (New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '09:20'))
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $task -Action $action -Trigger $triggers -Principal $principal -Settings $settings -Description 'Independent restricted DB PAPER reconciler; broker GET only; observe only.' | Out-Null
Write-Host 'Registered observe-only PAPER reconciler. Windows session must remain signed in and awake.'
