<#
Autoconfiguration de Windows pour Vasistas.

Rend prête pour Vasistas une machine Windows 10 ou 11 (VM QEMU/KVM) installée autrement que
par l'assistant, ou répare une installation : pilotes virtio, WinFsp (dossiers partagés),
service VirtIO-FS, agent Vasistas et sa tâche d'ouverture de session, réglages de la machine
et de la session, ouverture de session automatique. Idempotent : ce qui est déjà en place
n'est pas touché. Aucun redémarrage automatique.

Paramètres (arguments de -File, ou variables posées avant le texte du script quand il passe
par `vasistas exec`, qui ne permet pas de bloc param()) :
  -Check                  contrôle seul : rapporte ce qui manque, ne modifie rien
  -User NOM               compte qui ouvre la session Windows (défaut : utilisateur courant)
  -AutoLogonPassword MDP  mot de passe de ce compte, pour l'ouverture de session automatique
  -AgentSource SOURCE     agent Vasistas : dossier, .zip ou URL (défaut : CD de partage)
  -NoDownload             aucun téléchargement (CD virtio, MSI WinFsp ou agent locaux)
  -SkipDrivers            ne pas vérifier ni installer les pilotes virtio

Depuis l'hôte :      vasistas exec @install/configure-windows.ps1
Contrôle seul :      vasistas exec "$(printf '$Check = $true\n'; cat install/configure-windows.ps1)"
Dans Windows (administrateur) :
  powershell -ExecutionPolicy Bypass -File configure-windows.ps1 -Check

Dernière ligne : un objet JSON {"ok", "ready", "check", "reboot", "steps"} pour l'hôte ;
états : ok (déjà fait), done (fait), todo (à faire), warning, skipped, failed.
Voir docs/configure-windows.md.
#>

# Sortie redirigée (vasistas exec) : accents en UTF-8. Pas dans une console interactive,
# dont le changement survivrait au script.
if ([Console]::IsOutputRedirected) { [Console]::OutputEncoding = [Text.Encoding]::UTF8 }
$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

# -- paramètres --

foreach ($n in 'Check', 'NoDownload', 'SkipDrivers') {
    if (-not (Get-Variable -Name $n -Scope Script -ErrorAction SilentlyContinue)) { Set-Variable -Name $n -Value $false -Scope Script }
}
foreach ($n in 'User', 'AutoLogonPassword', 'AgentSource') {
    if (-not (Get-Variable -Name $n -Scope Script -ErrorAction SilentlyContinue)) { Set-Variable -Name $n -Value $null -Scope Script }
}
for ($i = 0; $i -lt $args.Count; $i++) {
    switch -Regex ([string]$args[$i]) {
        '^-Check$'             { $Check = $true }
        '^-NoDownload$'        { $NoDownload = $true }
        '^-SkipDrivers$'       { $SkipDrivers = $true }
        '^-User$'              { $i++; $User = $args[$i] }
        '^-AutoLogonPassword$' { $i++; $AutoLogonPassword = $args[$i] }
        '^-AgentSource$'       { $i++; $AgentSource = $args[$i] }
        default { Write-Host "Paramètre inconnu : $($args[$i])"; exit 2 }
    }
}
$Check = [bool]$Check

# -- constantes (mêmes chemins que specialize.ps1 et boot.ps1) --

$VasistasDir = 'C:\Program Files\Vasistas'
$AgentDir = Join-Path $VasistasDir 'Agent'
$AgentExe = Join-Path $AgentDir 'Vasistas.Agent.exe'
$BootPs1 = Join-Path $VasistasDir 'boot.ps1'
$Marker = Join-Path $VasistasDir 'installed'   # sans lui, boot.ps1 éteint la machine (fin d'installation)
$VirtioFsExe = 'C:\Program Files\Virtio-Win\VioFS\virtiofs.exe'
$LaunchCtl = 'C:\Program Files (x86)\WinFsp\bin\launchctl-x64.exe'
$VirtioUrl = 'https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win-gt-x64.msi'
$WinFspApi = 'https://api.github.com/repos/winfsp/winfsp/releases/latest'
$TempDir = Join-Path $env:TEMP 'vasistas-setup'

$Steps = [ordered]@{}
$script:NeedReboot = $false
$Labels = @{ ok = 'déjà fait'; done = 'fait'; todo = 'à faire'; warning = 'attention'; skipped = 'ignoré'; failed = 'échec' }

function Step($id, $title, $state, $detail) {
    $Steps[$id] = $state
    $line = '[{0}] {1}' -f $Labels[$state], $title
    if ($detail) { $line += " : $detail" }
    Write-Host $line
}

function Get-Uninstall($pattern) {
    Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
                     'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*' -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -match $pattern } | Select-Object -First 1
}

function Find-OnDrives($filter) {
    foreach ($d in Get-PSDrive -PSProvider FileSystem) {
        $hit = Get-ChildItem -LiteralPath $d.Root -Filter $filter -File -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    $null
}

function Save-Download($url, $name) {
    New-Item -ItemType Directory -Force -Path $TempDir | Out-Null
    $dest = Join-Path $TempDir $name
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $dest
    $dest
}

function Install-Msi($msi) {
    $p = Start-Process msiexec.exe -Wait -PassThru -ArgumentList '/i', "`"$msi`"", '/qn', '/norestart'
    if ($p.ExitCode -eq 3010) { $script:NeedReboot = $true }
    $p.ExitCode
}

# Valeurs de registre voulues : Path, Name, Value, Type ; Absent = la valeur doit manquer
# (pas « Remove » : $table.Remove serait la méthode Remove des tables, toujours vraie).
function Test-Reg($e) {
    $cur = (Get-ItemProperty -LiteralPath $e.Path -Name $e.Name -ErrorAction SilentlyContinue).($e.Name)
    if ($e.Absent) { return $null -eq $cur }
    if ($null -eq $cur) { return $false }
    [string]$cur -eq [string]$e.Value
}

function Set-Reg($e) {
    if ($e.Absent) {
        Remove-ItemProperty -LiteralPath $e.Path -Name $e.Name -ErrorAction SilentlyContinue
        return
    }
    if (-not (Test-Path -LiteralPath $e.Path)) { New-Item -Path $e.Path -Force | Out-Null }
    $type = if ($e.Type) { $e.Type } else { 'DWord' }
    Set-ItemProperty -LiteralPath $e.Path -Name $e.Name -Value $e.Value -Type $type
}

# (pas « R » : c'est un alias de PowerShell, prioritaire sur les fonctions)
function RegValue($path, $name, $value, $type = 'DWord') { @{ Path = $path; Name = $name; Value = $value; Type = $type } }

function Short-Name($name) { ([string]$name -split '\\')[-1] }

Write-Host ('Vasistas : configuration de Windows' + $(if ($Check) { ' (contrôle seul, rien n''est modifié)' } else { '' }))

# -- 1. droits --

$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)
if ($isAdmin) {
    Step admin 'Droits administrateur' ok
} elseif ($Check) {
    Step admin 'Droits administrateur' warning 'absents : contrôle possible, application impossible'
} else {
    Step admin 'Droits administrateur' failed 'relancer PowerShell « en tant qu''administrateur »'
    Write-Output (@{ ok = $false; ready = $false; check = $Check; reboot = $false; steps = $Steps } | ConvertTo-Json -Compress)
    exit 1
}

# compte cible
if (-not $User) { $User = $env:USERNAME }
$UserName = Short-Name $User
$UserSid = $null
# compte local d'abord : « nom » seul est ambigu quand l'ordinateur porte le même nom que le
# compte (ordinateur VASISTAS, compte vasistas)
$candidates = if ($User -match '\\') { @($User) } else { @("$env:COMPUTERNAME\$User", $User) }
foreach ($c in $candidates) {
    try {
        $UserSid = (New-Object Security.Principal.NTAccount($c)).Translate([Security.Principal.SecurityIdentifier]).Value
        $User = $c
        break
    } catch { }
}
if ($UserSid) { Step account "Compte $User" ok }
else { Step account "Compte $User" failed 'introuvable sur cette machine' }

# CD de partage de Vasistas (vasistas.tag, dossier Agent, boot.ps1)
$ShareRoot = Get-PSDrive -PSProvider FileSystem |
    Where-Object { Test-Path (Join-Path $_.Root 'vasistas.tag') } | Select-Object -First 1 | ForEach-Object { $_.Root }

# -- 2. pilotes virtio --

$title = 'Pilotes virtio'
if ($SkipDrivers) {
    Step virtio $title skipped
} else {
    $vi = Get-Uninstall '^Virtio-win'
    $missing = @(Get-PnpDevice -PresentOnly -ErrorAction SilentlyContinue |
        Where-Object { $_.InstanceId -match '^PCI\\VEN_1AF4' -and $_.Status -ne 'OK' })
    if ($vi -and (Test-Path $VirtioFsExe) -and $missing.Count -eq 0) {
        Step virtio $title ok "version $($vi.DisplayVersion)"
    } else {
        $why = @()
        if (-not $vi) { $why += 'paquet virtio-win absent' }
        if (-not (Test-Path $VirtioFsExe)) { $why += 'virtiofs.exe absent' }
        if ($missing.Count) { $why += "$($missing.Count) périphérique(s) sans pilote" }
        if ($Check) {
            Step virtio $title todo ($why -join ', ')
        } else {
            $msi = Find-OnDrives 'virtio-win-gt-x64.msi'
            if (-not $msi -and -not $NoDownload) {
                try { $msi = Save-Download $VirtioUrl 'virtio-win-gt-x64.msi' } catch { Write-Host "  téléchargement : $($_.Exception.Message)" }
            }
            if (-not $msi) {
                Step virtio $title failed 'virtio-win-gt-x64.msi introuvable (insérer le CD virtio-win)'
            } else {
                # le canal de l'agent (virtio-serial) peut être coupé quelques secondes
                $code = Install-Msi $msi
                if ($code -in 0, 3010) { $script:NeedReboot = $true; Step virtio $title done "installés ($msi)" }
                else { Step virtio $title failed "msiexec, code $code" }
            }
        }
    }
}

# -- 3. WinFsp (dossiers partagés) --

$title = 'WinFsp (dossiers partagés)'
$wf = Get-Uninstall '^WinFsp'
if ((Test-Path $LaunchCtl) -and (Get-Service 'WinFsp.Launcher' -ErrorAction SilentlyContinue)) {
    Step winfsp $title ok $(if ($wf) { "version $($wf.DisplayVersion)" } else { '' })
} elseif ($Check) {
    Step winfsp $title todo 'absent : les dossiers Linux ne seront pas visibles'
} else {
    $how = $null
    $msi = Find-OnDrives 'winfsp*.msi'
    if ($msi) {
        if ((Install-Msi $msi) -in 0, 3010) { $how = "MSI local $msi" }
    }
    if (-not (Test-Path $LaunchCtl) -and -not $NoDownload -and (Get-Command winget -ErrorAction SilentlyContinue)) {
        winget install --id WinFsp.WinFsp -e --silent --accept-package-agreements --accept-source-agreements --disable-interactivity | Out-Null
        if (Test-Path $LaunchCtl) { $how = 'winget' }
    }
    if (-not (Test-Path $LaunchCtl) -and -not $NoDownload) {
        try {
            [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
            $rel = Invoke-RestMethod -UseBasicParsing -Uri $WinFspApi
            $asset = $rel.assets | Where-Object { $_.name -match '^winfsp-[\d.]+\.msi$' } | Select-Object -First 1
            if ($asset) {
                $msi = Save-Download $asset.browser_download_url $asset.name
                if ((Install-Msi $msi) -in 0, 3010) { $how = "GitHub $($rel.tag_name)" }
            }
        } catch { Write-Host "  GitHub : $($_.Exception.Message)" }
    }
    if (Test-Path $LaunchCtl) { Step winfsp $title done "installé ($how)" }
    else { Step winfsp $title failed 'installation impossible (poser winfsp-*.msi sur un lecteur, ou autoriser le téléchargement)' }
}

# -- 4. service VirtIO-FS lancé par WinFsp (comme boot.ps1) --

$title = 'Service VirtIO-FS pour WinFsp'
$fsKey = 'HKLM:\SOFTWARE\WOW6432Node\WinFsp\Services\virtiofs'
$fsWant = @(
    (RegValue $fsKey 'Executable' $VirtioFsExe 'String'),
    (RegValue $fsKey 'CommandLine' '-t %1 -m %2' 'String'),
    (RegValue $fsKey 'Security' 'D:P(A;;RPWPLC;;;WD)' 'String'),
    (RegValue $fsKey 'JobControl' 1)
)
$fsBad = @($fsWant | Where-Object { -not (Test-Reg $_) })
# le service VirtioFsSvc de virtio-win monte le premier dossier tout seul : en démarrage
# automatique, il concurrence les lecteurs montés par WinFsp
$svc = Get-Service 'VirtioFsSvc' -ErrorAction SilentlyContinue
$svcAuto = $svc -and $svc.StartType -eq 'Automatic'
if ($fsBad.Count -eq 0 -and -not $svcAuto) {
    Step virtiofs $title ok
} elseif ($Check) {
    $why = @()
    if ($fsBad.Count) { $why += "$($fsBad.Count) valeur(s) à écrire" }
    if ($svcAuto) { $why += 'VirtioFsSvc en démarrage automatique (à passer en manuel)' }
    Step virtiofs $title todo ($why -join ', ')
} elseif (-not (Test-Path $VirtioFsExe)) {
    Step virtiofs $title failed 'virtiofs.exe absent (pilotes virtio)'
} else {
    try {
        $fsBad | ForEach-Object { Set-Reg $_ }
        # seulement le type de démarrage : un service en marche n'est pas arrêté
        if ($svcAuto) { Set-Service -Name 'VirtioFsSvc' -StartupType Manual }
        Step virtiofs $title done
    } catch { Step virtiofs $title failed $_.Exception.Message }
}

# -- 5. agent Vasistas, boot.ps1, tâche d'ouverture de session --

function Resolve-AgentSource {
    # Rend le dossier qui contient Vasistas.Agent.exe, ou $null. En contrôle : ni
    # téléchargement ni décompression.
    $src = $null
    if ($AgentSource) {
        if ($AgentSource -match '^https?://') {
            if ($Check -or $NoDownload) { return $null }
            $AgentSource = Save-Download $AgentSource 'vasistas-agent.zip'
        }
        if ($AgentSource -match '\.zip$') {
            if ($Check) { return $null }
            $out = Join-Path $TempDir 'agent'
            Remove-Item -Recurse -Force $out -ErrorAction SilentlyContinue
            Expand-Archive -LiteralPath $AgentSource -DestinationPath $out -Force
            $src = $out
        } else {
            $src = $AgentSource
        }
    } elseif ($ShareRoot) {
        $src = Join-Path $ShareRoot 'Agent'
    }
    if (-not $src -or -not (Test-Path $src)) { return $null }
    $exe = Get-ChildItem -LiteralPath $src -Recurse -Filter 'Vasistas.Agent.exe' -File -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($exe) { $exe.DirectoryName } else { $null }
}

function Get-DiffFiles($from, $to, [switch]$Older) {
    # Fichiers de $from absents de $to, ou différents et plus récents. -Older : ceux qui
    # diffèrent mais sont plus anciens que la copie installée (agent mis à jour à chaud
    # depuis l'hôte après la création du CD de partage) : jamais recopiés.
    Get-ChildItem -LiteralPath $from -Recurse -File | Where-Object {
        $rel = $_.FullName.Substring($from.TrimEnd('\').Length).TrimStart('\')
        $dst = Join-Path $to $rel
        if (-not (Test-Path -LiteralPath $dst)) { return -not $Older }
        if ((Get-FileHash -LiteralPath $_.FullName).Hash -eq (Get-FileHash -LiteralPath $dst).Hash) { return $false }
        $newer = $_.LastWriteTimeUtc -gt (Get-Item -LiteralPath $dst).LastWriteTimeUtc.AddSeconds(2)
        if ($Older) { -not $newer } else { $newer }
    }
}

$title = 'Agent Vasistas'
$agentSrc = Resolve-AgentSource
if (-not $agentSrc) {
    if (Test-Path $AgentExe) {
        $why = if ($AgentSource -and $Check) { "source $AgentSource non lue en contrôle" } else { 'pas de source pour comparer (CD de partage absent)' }
        Step agent $title ok "présent ; $why"
    } elseif ($Check) {
        Step agent $title todo 'absent, et pas de source (CD de partage ou -AgentSource)'
    } else {
        Step agent $title failed 'absent, et pas de source (CD de partage ou -AgentSource)'
    }
} else {
    $diff = @(Get-DiffFiles $agentSrc $AgentDir)
    $kept = @(Get-DiffFiles $agentSrc $AgentDir -Older)
    $keptNote = if ($kept.Count) { " ; $($kept.Count) fichier(s) installé(s) plus récent(s) que la source, gardé(s)" } else { '' }
    if ($diff.Count -eq 0) {
        Step agent $title ok "à jour ($agentSrc)$keptNote"
    } elseif ($Check) {
        Step agent $title todo "$($diff.Count) fichier(s) à copier depuis $agentSrc$keptNote"
    } else {
        try {
            New-Item -ItemType Directory -Force -Path $AgentDir | Out-Null
            Get-ChildItem -LiteralPath $AgentDir -Filter '*.old-*' -File -ErrorAction SilentlyContinue |
                Remove-Item -Force -ErrorAction SilentlyContinue
            # Agent en marche (c'est lui qui exécute ce script quand il vient de l'hôte) : ses
            # fichiers verrouillés sont renommés, jamais le processus arrêté. La nouvelle version
            # part au prochain lancement de l'agent (boot.ps1 le relance s'il s'arrête).
            $running = @(Get-Process -Name 'Vasistas.Agent' -ErrorAction SilentlyContinue |
                Where-Object { $_.Path -and $_.Path -like "$AgentDir\*" })
            foreach ($f in $diff) {
                $rel = $f.FullName.Substring($agentSrc.TrimEnd('\').Length).TrimStart('\')
                $dst = Join-Path $AgentDir $rel
                New-Item -ItemType Directory -Force -Path (Split-Path $dst) | Out-Null
                if ($running.Count -and (Test-Path -LiteralPath $dst)) {
                    Rename-Item -LiteralPath $dst -NewName ((Split-Path $dst -Leaf) + '.old-' + [DateTime]::Now.Ticks) -ErrorAction SilentlyContinue
                }
                Copy-Item -LiteralPath $f.FullName -Destination $dst -Force
            }
            attrib -r "$AgentDir\*" /s | Out-Null   # copiés depuis un CD : lecture seule
            $note = if ($running.Count) { ' ; pris en compte au prochain lancement de l''agent' } else { '' }
            Step agent $title done "$($diff.Count) fichier(s) copié(s)$note"
        } catch { Step agent $title failed $_.Exception.Message }
    }
}

$title = 'Script d''ouverture de session (boot.ps1)'
$bootSrc = @(
    $(if ($ShareRoot) { Join-Path $ShareRoot 'boot.ps1' }),
    $(if ($agentSrc) { Join-Path $agentSrc 'boot.ps1' }),
    $(if ($agentSrc) { Join-Path (Split-Path $agentSrc) 'boot.ps1' }),
    $(if ($PSScriptRoot) { Join-Path $PSScriptRoot 'boot.ps1' }),
    (Join-Path (Get-Location) 'boot.ps1')
) | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
$bootSame = $bootSrc -and (Test-Path $BootPs1) -and
    ((Get-FileHash -LiteralPath $bootSrc).Hash -eq (Get-FileHash -LiteralPath $BootPs1).Hash)
$todo = @()
if (-not $bootSame -and ($bootSrc -or -not (Test-Path $BootPs1))) { $todo += 'boot.ps1' }
if (-not (Test-Path $Marker)) { $todo += 'marqueur « installed » (sans lui, boot.ps1 éteint Windows)' }
if ($todo.Count -eq 0) {
    Step boot $title ok $(if ($bootSrc) { '' } else { 'présent ; pas de source pour comparer' })
} elseif ($Check) {
    Step boot $title todo ($todo -join ', ')
} elseif (-not $bootSrc -and -not (Test-Path $BootPs1)) {
    Step boot $title failed 'boot.ps1 introuvable (CD de partage, -AgentSource ou à côté de ce script)'
} else {
    try {
        New-Item -ItemType Directory -Force -Path $VasistasDir | Out-Null
        if ($bootSrc -and -not $bootSame) { Copy-Item -LiteralPath $bootSrc -Destination $BootPs1 -Force; attrib -r $BootPs1 | Out-Null }
        if (-not (Test-Path $Marker)) { New-Item -ItemType File -Path $Marker | Out-Null }
        Step boot $title done ($todo -join ', ')
    } catch { Step boot $title failed $_.Exception.Message }
}

$title = "Tâche planifiée Vasistas (ouverture de session de $UserName)"
$task = Get-ScheduledTask -TaskName 'Vasistas' -ErrorAction SilentlyContinue
$taskOk = $task -and $task.Principal.RunLevel -eq 'Highest' -and
    (Short-Name $task.Principal.UserId) -eq $UserName -and
    @($task.Actions | Where-Object { $_.Arguments -like '*\Vasistas\boot.ps1*' }).Count -gt 0 -and
    @($task.Triggers | Where-Object { $_.CimClass.CimClassName -eq 'MSFT_TaskLogonTrigger' -and (Short-Name $_.UserId) -eq $UserName }).Count -gt 0
if ($taskOk) {
    Step task $title ok
} elseif ($Check) {
    Step task $title todo $(if ($task) { 'à corriger (compte, droits ou action)' } else { 'absente' })
} elseif (-not $UserSid) {
    Step task $title failed "compte $User introuvable"
} else {
    try {
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' `
            -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$BootPs1`""
        $trigger = New-ScheduledTaskTrigger -AtLogOn -User $User
        $principal = New-ScheduledTaskPrincipal -UserId $User -LogonType Interactive -RunLevel Highest
        $settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
            -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew
        # -Force remplace la définition, sans arrêter une instance en cours
        Register-ScheduledTask -TaskName 'Vasistas' -Action $action -Trigger $trigger `
            -Principal $principal -Settings $settings -Force | Out-Null
        Step task $title done 'effective à la prochaine ouverture de session'
    } catch { Step task $title failed $_.Exception.Message }
}

# -- 6. réglages de la machine (specialize.ps1) --

function Get-PowerAc($sub, $setting) {
    # index secteur (AC) actuel ; sortie de powercfg localisée : on lit les deux dernières
    # valeurs hexadécimales (secteur puis batterie)
    $hex = @(powercfg /query SCHEME_CURRENT $sub $setting 2>$null | Select-String -Pattern '0x[0-9a-fA-F]+\s*$' |
        ForEach-Object { $_.Matches[0].Value.Trim() })
    if ($hex.Count -ge 2) { [Convert]::ToInt64($hex[-2], 16) } else { $null }
}

$title = 'Réglages de la machine (veille, verrouillage, UAC, mises à jour)'
$machine = @(
    (RegValue 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\Personalization' 'NoLockScreen' 1),
    (RegValue 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System' 'PromptOnSecureDesktop' 0),
    (RegValue 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' 'NoAutoRebootWithLoggedOnUsers' 1)
)
$power = [ordered]@{
    'monitor-timeout-ac'   = @('SUB_VIDEO', 'VIDEOIDLE')
    'standby-timeout-ac'   = @('SUB_SLEEP', 'STANDBYIDLE')
    'hibernate-timeout-ac' = @('SUB_SLEEP', 'HIBERNATEIDLE')
}
$bad = @()
$regBad = @($machine | Where-Object { -not (Test-Reg $_) })
$bad += $regBad | ForEach-Object { $_.Name }
$powerBad = @($power.Keys | Where-Object { (Get-PowerAc $power[$_][0] $power[$_][1]) -ne 0 })
$bad += $powerBad
$hib = (Get-ItemProperty 'HKLM:\SYSTEM\CurrentControlSet\Control\Power' -Name HibernateEnabled -ErrorAction SilentlyContinue).HibernateEnabled
if ($hib -ne 0) { $bad += 'hibernation' }
if ($bad.Count -eq 0) {
    Step machine $title ok
} elseif ($Check) {
    Step machine $title todo ($bad -join ', ')
} else {
    try {
        $regBad | ForEach-Object { Set-Reg $_ }
        foreach ($k in $powerBad) { powercfg /change $k 0 | Out-Null }
        if ($hib -ne 0) { powercfg /hibernate off | Out-Null }
        Step machine $title done ($bad -join ', ')
    } catch { Step machine $title failed $_.Exception.Message }
}

# -- 7. réglages de la session (boot.ps1) --

$title = "Réglages de la session de $UserName"
$curSid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$root = $null
if ($UserSid -and $UserSid -eq $curSid) { $root = 'HKCU:' }
elseif ($UserSid -and (Test-Path "Registry::HKEY_USERS\$UserSid")) { $root = "Registry::HKEY_USERS\$UserSid" }
if (-not $root) {
    Step session $title skipped 'session fermée : boot.ps1 les appliquera à la prochaine ouverture'
} else {
    $session = @(
        (RegValue "$root\Control Panel\Desktop" 'ScreenSaveActive' '0' 'String'),
        (RegValue "$root\Control Panel\Desktop" 'DelayLockInterval' 0),
        (RegValue "$root\Software\Microsoft\Windows\CurrentVersion\Explorer\VisualEffects" 'VisualFXSetting' 2),
        (RegValue "$root\Control Panel\Desktop\WindowMetrics" 'MinAnimate' '0' 'String'),
        (RegValue "$root\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize" 'EnableTransparency' 0),
        (RegValue "$root\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced" 'TaskbarAnimations' 0),
        @{ Path = "$root\Control Panel\Desktop"; Name = 'LogPixels'; Absent = $true },
        (RegValue "$root\Control Panel\Desktop" 'Win8DpiScaling' 0),
        (RegValue "$root\Control Panel\Desktop" 'Wallpaper' '' 'String'),
        (RegValue "$root\Control Panel\Colors" 'Background' '0 0 0' 'String'),
        (RegValue "$root\Software\Microsoft\Windows\CurrentVersion\PushNotifications" 'ToastEnabled' 0),
        (RegValue "$root\Software\Policies\Microsoft\Edge\WebView2\AdditionalBrowserArguments" 'olk.exe' '--disable-gpu' 'String'),
        (RegValue "$root\Software\Microsoft\Office\16.0\Common\Graphics" 'DisableHardwareAcceleration' 1),
        (RegValue "$root\Software\Microsoft\Office\16.0\Common\Graphics" 'DisableAnimations' 1)
    )
    $sesBad = @($session | Where-Object { -not (Test-Reg $_) })
    # barre des tâches masquée automatiquement (octet 8 de StuckRects3 = 3)
    $sr = "$root\Software\Microsoft\Windows\CurrentVersion\Explorer\StuckRects3"
    $bytes = (Get-ItemProperty -LiteralPath $sr -ErrorAction SilentlyContinue).Settings
    $barBad = $bytes -and $bytes[8] -ne 3
    $n = $sesBad.Count + [int][bool]$barBad
    if ($n -eq 0) {
        Step session $title ok
    } elseif ($Check) {
        $names = @($sesBad | ForEach-Object { $_.Name })
        if ($barBad) { $names += 'barre des tâches' }
        Step session $title todo ("{0} réglage(s) : {1}" -f $n, ($names -join ', '))
    } else {
        try {
            $sesBad | ForEach-Object { Set-Reg $_ }
            if ($barBad) { $bytes[8] = 3; Set-ItemProperty -LiteralPath $sr -Name Settings -Value $bytes }
            # l'Explorateur n'est pas relancé ici : fond d'écran et barre des tâches changent à la
            # prochaine ouverture de session
            Step session $title done "$n réglage(s) ; complets à la prochaine ouverture de session"
        } catch { Step session $title failed $_.Exception.Message }
    }
}

# -- 8. ouverture de session automatique --

$title = "Ouverture de session automatique ($UserName)"
$wlKey = 'HKLM:\SOFTWARE\Microsoft\Windows NT\CurrentVersion\Winlogon'
$wl = Get-ItemProperty -LiteralPath $wlKey -ErrorAction SilentlyContinue
$autoOk = $wl.AutoAdminLogon -eq '1' -and $wl.DefaultUserName -eq $UserName
if ($autoOk -and -not $AutoLogonPassword) {
    $note = if ($wl.DefaultPassword) { 'mot de passe en clair dans le registre (relancer avec -AutoLogonPassword pour le protéger)' }
            elseif ($null -ne $wl.AutoLogonCount -and [int64]$wl.AutoLogonCount -lt 10000) { "limitée à $($wl.AutoLogonCount) ouvertures (-AutoLogonPassword la rend permanente)" }
            else { '' }
    Step autologon $title ok $note
} elseif (-not $AutoLogonPassword) {
    Step autologon $title warning ('non configurée : sans elle, Windows attend à l''écran de connexion et Vasistas ' +
        'ne voit rien. Relancer avec -AutoLogonPassword')
} elseif ($Check) {
    Step autologon $title todo 'sera configurée (mot de passe fourni)'
} else {
    try {
        Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
public static class VasistasLogon {
    [StructLayout(LayoutKind.Sequential)] struct LsaString { public ushort Length, MaximumLength; public IntPtr Buffer; }
    [StructLayout(LayoutKind.Sequential)] struct LsaAttributes { public int Length; public IntPtr RootDirectory, ObjectName; public uint Attributes; public IntPtr SecurityDescriptor, SecurityQualityOfService; }
    [DllImport("advapi32.dll")] static extern uint LsaOpenPolicy(IntPtr system, ref LsaAttributes attrs, uint access, out IntPtr policy);
    [DllImport("advapi32.dll")] static extern uint LsaStorePrivateData(IntPtr policy, ref LsaString key, ref LsaString data);
    [DllImport("advapi32.dll")] static extern uint LsaClose(IntPtr policy);
    [DllImport("advapi32.dll")] static extern int LsaNtStatusToWinError(uint status);
    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool LogonUser(string user, string domain, string password, int type, int provider, out IntPtr token);
    [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
    static LsaString Make(string s) {
        var u = new LsaString();
        u.Buffer = Marshal.StringToHGlobalUni(s);
        u.Length = (ushort)(s.Length * 2);
        u.MaximumLength = (ushort)(s.Length * 2 + 2);
        return u;
    }
    public static bool Valid(string user, string domain, string password) {
        IntPtr token;
        if (!LogonUser(user, domain, password, 2, 0, out token)) return false;
        CloseHandle(token);
        return true;
    }
    // Secret LSA « DefaultPassword » : lu par Winlogon, pas en clair dans le registre
    public static int Store(string key, string value) {
        var attrs = new LsaAttributes();
        attrs.Length = Marshal.SizeOf(attrs);
        IntPtr policy;
        uint st = LsaOpenPolicy(IntPtr.Zero, ref attrs, 0x000F0FFF, out policy);
        if (st != 0) return LsaNtStatusToWinError(st);
        var k = Make(key);
        var d = Make(value);
        try { st = LsaStorePrivateData(policy, ref k, ref d); }
        finally { Marshal.FreeHGlobal(k.Buffer); Marshal.FreeHGlobal(d.Buffer); LsaClose(policy); }
        return st == 0 ? 0 : LsaNtStatusToWinError(st);
    }
}
'@
        $parts = [string]$User -split '\\'
        $domain = if ($parts.Count -gt 1) { $parts[0] } else { $env:COMPUTERNAME }
        if (-not [VasistasLogon]::Valid($UserName, $domain, $AutoLogonPassword)) {
            Step autologon $title failed 'mot de passe refusé par Windows'
        } else {
            $rc = [VasistasLogon]::Store('DefaultPassword', $AutoLogonPassword)
            if ($rc -ne 0) {
                Step autologon $title failed "secret LSA, code $rc"
            } else {
                Set-ItemProperty -LiteralPath $wlKey -Name AutoAdminLogon -Value '1' -Type String
                Set-ItemProperty -LiteralPath $wlKey -Name DefaultUserName -Value $UserName -Type String
                Set-ItemProperty -LiteralPath $wlKey -Name DefaultDomainName -Value $domain -Type String
                Remove-ItemProperty -LiteralPath $wlKey -Name DefaultPassword -ErrorAction SilentlyContinue
                # compteur de l'installation automatique : à zéro, l'ouverture automatique s'arrête
                Remove-ItemProperty -LiteralPath $wlKey -Name AutoLogonCount -ErrorAction SilentlyContinue
                Step autologon $title done 'permanente (mot de passe en secret LSA)'
            }
        }
    } catch { Step autologon $title failed $_.Exception.Message }
}

# -- 9. bilan --

$failed = @($Steps.Keys | Where-Object { $Steps[$_] -eq 'failed' })
$todoLeft = @($Steps.Keys | Where-Object { $Steps[$_] -eq 'todo' })
$ok = $failed.Count -eq 0
$ready = $ok -and $todoLeft.Count -eq 0
Write-Host ''
if ($Check) {
    Write-Host $(if ($ready) { 'Bilan : Windows est prêt pour Vasistas.' }
                 else { "Bilan : $($todoLeft.Count) étape(s) à faire, $($failed.Count) en échec. Relancer sans -Check pour appliquer." })
} else {
    Write-Host $(if ($ok) { 'Bilan : configuration terminée.' } else { "Bilan : $($failed.Count) étape(s) en échec : $($failed -join ', ')." })
}
if ($script:NeedReboot) { Write-Host 'Redémarrer Windows pour terminer (pilotes).' }
Write-Output (@{ ok = $ok; ready = $ready; check = $Check; reboot = $script:NeedReboot; steps = $Steps } | ConvertTo-Json -Compress)
if (-not $ok) { exit 1 }
