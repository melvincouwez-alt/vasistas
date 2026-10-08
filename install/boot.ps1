# Lancé à chaque ouverture de session (tâche planifiée, privilèges élevés).
# Applique les réglages de l'utilisateur, met à jour l'agent depuis l'ISO de partage,
# puis le relance s'il s'arrête. À la toute première ouverture, éteint la machine :
# c'est le signal de fin d'installation pour `vasistas vm install`.

$dir = 'C:\Program Files\Vasistas'
$agent = Join-Path $dir 'Agent\Vasistas.Agent.exe'
$marker = Join-Path $dir 'installed'

function Set-Reg($path, $name, $value, $type = 'DWord') {
    if (-not (Test-Path $path)) { New-Item -Path $path -Force | Out-Null }
    Set-ItemProperty -Path $path -Name $name -Value $value -Type $type
}

# Réglages de session : pas d'économiseur, animations et transparence coupées
Set-Reg 'HKCU:\Control Panel\Desktop' 'ScreenSaveActive' '0' 'String'
Set-Reg 'HKCU:\Control Panel\Desktop' 'DelayLockInterval' 0
Set-Reg 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects' 'VisualFXSetting' 2
Set-Reg 'HKCU:\Control Panel\Desktop\WindowMetrics' 'MinAnimate' '0' 'String'
Set-Reg 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' 'EnableTransparency' 0
Set-Reg 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced' 'TaskbarAnimations' 0
# Pas d'échelle personnalisée : l'agent règle l'échelle standard selon l'écran hôte
Remove-ItemProperty -Path 'HKCU:\Control Panel\Desktop' -Name 'LogPixels' -ErrorAction SilentlyContinue
Set-Reg 'HKCU:\Control Panel\Desktop' 'Win8DpiScaling' 0

# Fond d'écran noir uni : une zone de l'écran découverte par erreur apparaît noire sous Linux,
# pas une image
Set-Reg 'HKCU:\Control Panel\Desktop' 'Wallpaper' '' 'String'
Set-Reg 'HKCU:\Control Panel\Colors' 'Background' '0 0 0' 'String'
# Notifications de Windows coupées : invisibles sous Linux, elles recouvraient les fenêtres
Set-Reg 'HKCU:\Software\Microsoft\Windows\CurrentVersion\PushNotifications' 'ToastEnabled' 0
# Nouveau Outlook (WebView2) en rendu logiciel direct : sans GPU, son processus GPU émule
# DirectX sur le processeur, plus coûteux
Set-Reg 'HKCU:\Software\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments' 'olk.exe' '--disable-gpu' 'String'

# Office sans accélération matérielle : sans GPU, son rendu DirectX passe par l'émulation
# logicielle, plus lente que le chemin GDI
Set-Reg 'HKCU:\Software\Microsoft\Office\16.0\Common\Graphics' 'DisableHardwareAcceleration' 1
Set-Reg 'HKCU:\Software\Microsoft\Office\16.0\Common\Graphics' 'DisableAnimations' 1

# Barre des tâches masquée automatiquement
$sr = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StuckRects3'
$bytes = (Get-ItemProperty -Path $sr -ErrorAction SilentlyContinue).Settings
if ($bytes -and $bytes[8] -ne 3) {
    $bytes[8] = 3
    Set-ItemProperty -Path $sr -Name Settings -Value $bytes
    Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue
}

if (-not (Test-Path $marker)) {
    New-Item -ItemType File -Path $marker | Out-Null
    shutdown.exe /s /t 5 /c 'Vasistas : installation terminée'
    exit 0
}

$share = Get-PSDrive -PSProvider FileSystem |
    Where-Object { Test-Path (Join-Path $_.Root 'vasistas.tag') } | Select-Object -First 1
if ($share) {
    $src = Join-Path $share.Root 'Agent'
    if (Test-Path $src) {
        # /XO : un agent mis à jour à chaud (update_agent.py) plus récent que le CD reste en place
        robocopy $src (Join-Path $dir 'Agent') /MIR /NJH /NJS /NP /XO | Out-Null
        # copiés depuis un CD : lecture seule, ce qui bloquerait une mise à jour à chaud
        attrib -r (Join-Path $dir 'Agent\*') | Out-Null
    }
    Copy-Item (Join-Path $share.Root 'boot.ps1') $dir -Force -ErrorAction SilentlyContinue
}

# Dossiers Linux (virtiofs) montés par le lanceur de WinFsp, qui tourne en SYSTEM : les
# lecteurs sont alors visibles de toutes les sessions, Office compris (non élevé).
$vfs = 'C:\Program Files\Virtio-Win\VioFS\virtiofs.exe'
$launchctl = 'C:\Program Files (x86)\WinFsp\bin\launchctl-x64.exe'
$sharesFile = if ($share) { Join-Path $share.Root 'shares.txt' } else { $null }
if ($sharesFile -and (Test-Path $sharesFile) -and (Test-Path $vfs) -and (Test-Path $launchctl)) {
    $key = 'HKLM:\SOFTWARE\WOW6432Node\WinFsp\Services\virtiofs'
    New-Item -Path $key -Force | Out-Null
    Set-ItemProperty $key Executable $vfs
    Set-ItemProperty $key CommandLine '-t %1 -m %2'
    Set-ItemProperty $key Security 'D:P(A;;RPWPLC;;;WD)'
    Set-ItemProperty $key JobControl 1 -Type DWord
    foreach ($line in Get-Content $sharesFile) {
        $tag, $drive, $name = $line.Trim() -split ';'
        if (-not $tag -or -not $drive) { continue }
        if (-not (Test-Path "$drive\")) { & $launchctl start virtiofs "vfs$tag" $tag $drive | Out-Null }
        # nom affiché dans l'Explorateur (3e champ, choisi dans l'application compagnon)
        if (-not $name) { $name = if ($tag -eq 'Telechargements') { 'Téléchargements' } else { $tag } }
        $label = "$name (Linux)"
        Set-Reg "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\MountPoints2\$($drive.TrimEnd(':'))" '_LabelFromReg' $label 'String'
    }
}

if (-not (Test-Path $agent)) { exit 0 }

while ($true) {
    Start-Process -FilePath $agent -WorkingDirectory (Split-Path $agent) -Wait
    Start-Sleep -Seconds 2
}
