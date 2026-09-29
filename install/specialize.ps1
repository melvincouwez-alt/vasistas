# Phase specialize de l'installation (compte SYSTEM) : pilotes, réglages machine,
# tâche qui lance boot.ps1 à chaque ouverture de session de vasistas.
# Les réglages propres à l'utilisateur sont dans boot.ps1.

$ErrorActionPreference = 'Continue'
$share = $PSScriptRoot
Start-Transcript -Path 'C:\Windows\Temp\vasistas-specialize.log' -Force

# Pilotes et services virtio (vioserial, NetKVM, balloon...)
$msi = Get-PSDrive -PSProvider FileSystem |
    ForEach-Object { Join-Path $_.Root 'virtio-win-gt-x64.msi' } |
    Where-Object { Test-Path $_ } | Select-Object -First 1
if ($msi) {
    Start-Process msiexec.exe -Wait -ArgumentList '/i', "`"$msi`"", '/qn', '/norestart'
} else {
    Write-Warning 'virtio-win-gt-x64.msi introuvable'
}

$dir = 'C:\Program Files\Vasistas'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
Copy-Item (Join-Path $share 'boot.ps1') $dir -Force

$action = New-ScheduledTaskAction -Execute 'powershell.exe' `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$dir\boot.ps1`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User 'vasistas'
$principal = New-ScheduledTaskPrincipal -UserId 'vasistas' -LogonType Interactive -RunLevel Highest
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
Register-ScheduledTask -TaskName 'Vasistas' -Action $action -Trigger $trigger `
    -Principal $principal -Settings $settings -Force | Out-Null

function Set-Reg($path, $name, $value, $type = 'DWord') {
    if (-not (Test-Path $path)) { New-Item -Path $path -Force | Out-Null }
    Set-ItemProperty -Path $path -Name $name -Value $value -Type $type
}

# Ni veille ni écran de verrouillage
powercfg /change monitor-timeout-ac 0
powercfg /change standby-timeout-ac 0
powercfg /change hibernate-timeout-ac 0
powercfg /hibernate off
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization' 'NoLockScreen' 1

# UAC sur le bureau courant, pour que l'agent voie et transmette l'invite
Set-Reg 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' 'PromptOnSecureDesktop' 0

# Pas de redémarrage automatique pour les mises à jour pendant une session
Set-Reg 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' 'NoAutoRebootWithLoggedOnUsers' 1

Stop-Transcript
