# Allègement de Windows : moteur générique exécuté par `exec` (voir slim.py).
# L'hôte place devant ce fichier une ligne `$Plan = '<json>'` :
#   { "action": "status" | "apply" | "restore", "items": [ { "key": ..., "svc": [...], ... } ] }
# Types d'éléments :
#   svc  : [{ "name": "DiagTrack", "start": 4 }]   (Start du registre ; « X_* » = service par utilisateur)
#   reg  : [{ "path": "HKLM:\...", "name": "...", "type": "DWord", "value": 0 }]
#   task : ["\Microsoft\Windows\Feedback\Siuf\*"]
#   appx : ["Microsoft.BingNews"]                  (suppression définitive, compte et image)
#   cap  : ["Browser.InternetExplorer~"]           (préfixe de capacité Windows, suppression définitive)
#   feat : ["WorkFolders-Client"]                  (fonctionnalité facultative, réactivable)
#   ps   : { "test": "...", "apply": "...", "restore": "..." }  (apply renvoie une chaîne gardée pour restore)
# Les valeurs d'origine vont dans C:\ProgramData\Vasistas\slim-backup.json, écrites au premier
# passage seulement : un second « apply » ne remplace pas la vraie valeur d'origine.
# Sortie : une ligne JSON ASCII { "status": { key: "on"|"off"|"partial" }, "errors": [...], "reboot": bool }.

[Console]::OutputEncoding = [Text.Encoding]::UTF8
$ErrorActionPreference = 'Stop'
$plan = $Plan | ConvertFrom-Json
$backupFile = 'C:\ProgramData\Vasistas\slim-backup.json'
$errors = New-Object System.Collections.ArrayList
$script:reboot = $false

function Load-Backup {
    $h = @{}
    if (Test-Path $backupFile) {
        $o = Get-Content $backupFile -Raw -Encoding UTF8 | ConvertFrom-Json
        foreach ($p in $o.PSObject.Properties) { $h[$p.Name] = $p.Value }
    }
    $h
}
function Save-Backup($h) {
    New-Item -ItemType Directory -Force (Split-Path $backupFile) | Out-Null
    ($h | ConvertTo-Json -Depth 6) | Set-Content $backupFile -Encoding UTF8
}
function Err($key, $what, $e) { [void]$errors.Add("${key}: $what : $($e.Exception.Message)") }

function Svc-Key($name) { 'HKLM:\SYSTEM\CurrentControlSet\Services\' + ($name -replace '_\*$', '') }
function Svc-Start($name) {
    $k = Svc-Key $name
    if (-not (Test-Path $k)) { return $null }
    (Get-ItemProperty $k -Name Start -ErrorAction SilentlyContinue).Start
}
function Reg-Get($r) {
    if (-not (Test-Path $r.path)) { return $null }
    $item = Get-Item $r.path
    if ($item.GetValueNames() -notcontains $r.name) { return $null }
    @{ value = $item.GetValue($r.name); type = $item.GetValueKind($r.name).ToString() }
}
function Reg-Set($path, $name, $type, $value) {
    if (-not (Test-Path $path)) { New-Item -Path $path -Force | Out-Null }
    if ($type -eq 'MultiString') { $value = [string[]]@($value) }
    New-ItemProperty -Path $path -Name $name -PropertyType $type -Value $value -Force | Out-Null
}
function Same($a, $b) { (@($a) -join "`n") -eq (@($b) -join "`n") }
function Tasks($pattern) {
    $path = $pattern.Substring(0, $pattern.LastIndexOf('\') + 1)
    $name = $pattern.Substring($pattern.LastIndexOf('\') + 1)
    @(Get-ScheduledTask -TaskPath $path -TaskName $name -ErrorAction SilentlyContinue)
}
function Caps($prefix) { @(Get-WindowsCapability -Online | Where-Object { $_.Name -like "$prefix*" -and $_.State -eq 'Installed' }) }

# -- état : liste de booléens (appliqué ou non) par élément --
function Test-Item($it) {
    $r = New-Object System.Collections.ArrayList
    foreach ($s in @($it.svc)) { if ($s) { $v = Svc-Start $s.name; [void]$r.Add(($v -eq $null) -or ($v -eq $s.start)) } }
    foreach ($g in @($it.reg)) { if ($g) { $v = Reg-Get $g; [void]$r.Add(($v -ne $null) -and (Same $v.value $g.value)) } }
    foreach ($t in @($it.task)) { if ($t) { foreach ($x in (Tasks $t)) { [void]$r.Add($x.State -eq 'Disabled') } } }
    foreach ($a in @($it.appx)) {
        if ($a) {
            $here = @(Get-AppxPackage -Name $a -ErrorAction SilentlyContinue).Count +
                    @(Get-AppxProvisionedPackage -Online | Where-Object DisplayName -eq $a).Count
            [void]$r.Add($here -eq 0)
        }
    }
    foreach ($c in @($it.cap)) { if ($c) { [void]$r.Add((Caps $c).Count -eq 0) } }
    foreach ($f in @($it.feat)) {
        if ($f) {
            $st = (Get-WindowsOptionalFeature -Online -FeatureName $f -ErrorAction SilentlyContinue).State
            [void]$r.Add(($st -eq $null) -or ($st -ne 'Enabled'))
        }
    }
    if ($it.ps -and $it.ps.test) { [void]$r.Add([bool](& ([scriptblock]::Create($it.ps.test)))) }
    $r
}
function State($it) {
    $r = @(Test-Item $it)
    if ($r.Count -eq 0) { return 'on' }
    $n = @($r | Where-Object { $_ }).Count
    if ($n -eq $r.Count) { 'on' } elseif ($n -eq 0) { 'off' } else { 'partial' }
}

# -- appliquer --
function Apply-Item($it, $bk) {
    $key = $it.key
    $b = $bk[$key]
    $first = $b -eq $null
    if ($first) { $b = @{ svc = @{}; reg = @(); task = @{}; ps = $null } }
    foreach ($s in @($it.svc)) {
        if (-not $s) { continue }
        try {
            $old = Svc-Start $s.name
            if ($old -eq $null) { continue }
            if ($first) { $b.svc[$s.name] = $old }
            Set-ItemProperty (Svc-Key $s.name) -Name Start -Value $s.start
            if ($s.start -eq 4) {
                Get-Service -Name ($s.name) -ErrorAction SilentlyContinue |
                    Where-Object Status -eq 'Running' | Stop-Service -Force -ErrorAction SilentlyContinue
            }
        } catch { Err $key "service $($s.name)" $_ }
    }
    foreach ($g in @($it.reg)) {
        if (-not $g) { continue }
        try {
            if ($first) {
                $v = Reg-Get $g
                $b.reg += ,@{ path = $g.path; name = $g.name; existed = ($v -ne $null);
                              type = $(if ($v) { $v.type }); value = $(if ($v) { $v.value }) }
            }
            Reg-Set $g.path $g.name $g.type $g.value
        } catch { Err $key "registre $($g.path)\$($g.name)" $_ }
    }
    foreach ($t in @($it.task)) {
        if (-not $t) { continue }
        foreach ($x in (Tasks $t)) {
            try {
                if ($first) { $b.task[$x.TaskPath + $x.TaskName] = ($x.State -ne 'Disabled') }
                if ($x.State -ne 'Disabled') { Disable-ScheduledTask -TaskPath $x.TaskPath -TaskName $x.TaskName | Out-Null }
            } catch { Err $key "tâche $($x.TaskName)" $_ }
        }
    }
    foreach ($a in @($it.appx)) {
        if (-not $a) { continue }
        try {
            Get-AppxPackage -Name $a -AllUsers -ErrorAction SilentlyContinue |
                ForEach-Object { Remove-AppxPackage -Package $_.PackageFullName -AllUsers -ErrorAction SilentlyContinue }
            Get-AppxPackage -Name $a -ErrorAction SilentlyContinue | Remove-AppxPackage
            Get-AppxProvisionedPackage -Online | Where-Object DisplayName -eq $a |
                ForEach-Object { Remove-AppxProvisionedPackage -Online -PackageName $_.PackageName | Out-Null }
        } catch { Err $key "appli $a" $_ }
    }
    foreach ($c in @($it.cap)) {
        if (-not $c) { continue }
        foreach ($x in (Caps $c)) {
            try {
                $res = Remove-WindowsCapability -Online -Name $x.Name
                if ($res.RestartNeeded) { $script:reboot = $true }
            } catch { Err $key "composant $($x.Name)" $_ }
        }
    }
    foreach ($f in @($it.feat)) {
        if (-not $f) { continue }
        try {
            $st = (Get-WindowsOptionalFeature -Online -FeatureName $f -ErrorAction SilentlyContinue).State
            if ($st -eq 'Enabled') {
                $res = Disable-WindowsOptionalFeature -Online -FeatureName $f -NoRestart
                if ($res.RestartNeeded) { $script:reboot = $true }
            }
        } catch { Err $key "fonctionnalité $f" $_ }
    }
    if ($it.ps -and $it.ps.apply) {
        try {
            $out = & ([scriptblock]::Create($it.ps.apply))
            if ($first) { $b.ps = [string]($out -join "`n") }
        } catch { Err $key 'script' $_ }
    }
    if ($first) { $bk[$key] = $b }
}

# -- restaurer (hors appx/cap, définitifs) --
function Restore-Item($it, $bk) {
    $key = $it.key
    $b = $bk[$key]
    if ($b -eq $null) { return }
    if ($b.svc) {
        foreach ($p in $b.svc.PSObject.Properties) {
            try {
                Set-ItemProperty (Svc-Key $p.Name) -Name Start -Value ([int]$p.Value)
                if ([int]$p.Value -eq 2) { Get-Service -Name $p.Name -ErrorAction SilentlyContinue | Start-Service -ErrorAction SilentlyContinue }
            } catch { Err $key "service $($p.Name)" $_ }
        }
    }
    foreach ($g in @($b.reg)) {
        if (-not $g) { continue }
        try {
            if ($g.existed) { Reg-Set $g.path $g.name $g.type $g.value }
            elseif (Test-Path $g.path) { Remove-ItemProperty -Path $g.path -Name $g.name -ErrorAction SilentlyContinue }
        } catch { Err $key "registre $($g.path)\$($g.name)" $_ }
    }
    if ($b.task) {
        foreach ($p in $b.task.PSObject.Properties) {
            if (-not $p.Value) { continue }
            try {
                $i = $p.Name.LastIndexOf('\')
                Enable-ScheduledTask -TaskPath $p.Name.Substring(0, $i + 1) -TaskName $p.Name.Substring($i + 1) | Out-Null
            } catch { Err $key "tâche $($p.Name)" $_ }
        }
    }
    foreach ($f in @($it.feat)) {
        if (-not $f) { continue }
        try {
            $res = Enable-WindowsOptionalFeature -Online -FeatureName $f -NoRestart -All
            if ($res.RestartNeeded) { $script:reboot = $true }
        } catch { Err $key "fonctionnalité $f" $_ }
    }
    if ($it.ps -and $it.ps.restore) {
        try { $B = $b.ps; & ([scriptblock]::Create($it.ps.restore)) $B | Out-Null } catch { Err $key 'script' $_ }
    }
    $bk.Remove($key)
}

$bk = Load-Backup
# ConvertFrom-Json donne des PSCustomObject : les tables de services/tâches déjà sauvées restent telles quelles
foreach ($it in $plan.items) {
    try {
        if ($plan.action -eq 'apply') { Apply-Item $it $bk }
        elseif ($plan.action -eq 'restore') { Restore-Item $it $bk }
    } catch { Err $it.key $plan.action $_ }
}
if ($plan.action -ne 'status') { Save-Backup $bk }

$status = @{}
foreach ($it in $plan.items) {
    try { $status[$it.key] = State $it } catch { $status[$it.key] = 'unknown'; Err $it.key 'état' $_ }
}
$ram = Get-CimInstance Win32_OperatingSystem
$out = @{
    status = $status; errors = @($errors); reboot = $script:reboot
    backup = @($bk.Keys)
    ram_used_mb = [int](($ram.TotalVisibleMemorySize - $ram.FreePhysicalMemory) / 1KB)
    processes = @(Get-Process).Count
    services = @(Get-Service | Where-Object Status -eq 'Running').Count
}
# ASCII seulement : la sortie de exec passe par la page de code de la console
$json = $out | ConvertTo-Json -Depth 4 -Compress
[regex]::Replace($json, '[^\x00-\x7F]', { param($m) '\u{0:x4}' -f [int][char]$m.Value })
