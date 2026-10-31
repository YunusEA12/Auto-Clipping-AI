param([switch]$Stop)
$ErrorActionPreference = 'Stop'
$guardianRoot = $PSScriptRoot
$guardianTaskName = 'Auto-Clipping-Antigravity-Guardian'
if ($Stop) {
    Stop-ScheduledTask -TaskName $guardianTaskName -ErrorAction SilentlyContinue
    Unregister-ScheduledTask -TaskName $guardianTaskName -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output 'Guardian task removed. Fleet state unchanged.'
    exit 0
}
$guardianPython = Join-Path $guardianRoot '.venv\Scripts\python.exe'
$guardianCli = Join-Path $env:LOCALAPPDATA 'agy\bin\agy.exe'
foreach ($guardianFile in @($guardianPython, $guardianCli)) {
    if (-not (Test-Path -LiteralPath $guardianFile)) { throw "Missing runtime: $guardianFile" }
}
$guardianArgs = '"{0}" --enable-agent --agy "{1}"' -f (Join-Path $guardianRoot 'project_guardian.py'), $guardianCli
$guardianAction = New-ScheduledTaskAction -Execute $guardianPython -Argument $guardianArgs -WorkingDirectory $guardianRoot
$guardianTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 30)
$guardianUser = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$guardianPrincipal = New-ScheduledTaskPrincipal -UserId $guardianUser -LogonType Interactive -RunLevel Limited
$guardianSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName $guardianTaskName -Action $guardianAction -Trigger $guardianTrigger -Principal $guardianPrincipal -Settings $guardianSettings -Description 'Local project checks every 30 minutes; Antigravity subscription only when needed; no publishing or production deployment.' -Force | Out-Null
Start-ScheduledTask -TaskName $guardianTaskName
Get-ScheduledTask -TaskName $guardianTaskName | Select-Object TaskName, State
