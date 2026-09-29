# Applications installées dans Windows, pour l'application compagnon.
# Sortie : un tableau JSON encodé en base64 (UTF-8 intact, pas de coupure de ligne par la console),
# écrit dans $env:TEMP\vasistas-apps.b64 (exec ne renvoie que 64 Ko) ; le script affiche sa taille
#   [{name, id, cmd, kind: classic|store, category: windows|system|user, icon: PNG 48 px en base64}]
# id = nom de l'exécutable en minuscules, sans .exe : c'est aussi l'identifiant que l'agent
# donne aux fenêtres, donc celui du lanceur et du groupe dans le dock.

Add-Type -AssemblyName System.Drawing
$ErrorActionPreference = 'SilentlyContinue'

function Png64([System.Drawing.Image]$img) {
    $bmp = New-Object System.Drawing.Bitmap 48, 48
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.InterpolationMode = 'HighQualityBicubic'
    $g.DrawImage($img, 0, 0, 48, 48)
    $g.Dispose()
    $ms = New-Object System.IO.MemoryStream
    $bmp.Save($ms, [System.Drawing.Imaging.ImageFormat]::Png)
    $bmp.Dispose()
    [Convert]::ToBase64String($ms.ToArray())
}

$seen = @{}
$list = New-Object System.Collections.ArrayList
function Emit($name, $id, $cmd, $kind, $category, $icon) {
    if (-not $id -or $seen.ContainsKey($id)) { return }
    $seen[$id] = $true
    [void]$list.Add([pscustomobject]@{ name = $name; id = $id; cmd = $cmd; kind = $kind; category = $category; icon = $icon })
}

# Outils d'administration et consoles : catégorie « système »
$systemExe = 'mmc|regedit|cmd|powershell|powershell_ise|pwsh|taskmgr|control|msconfig|services|eventvwr|perfmon|resmon|msinfo32|dfrgui|cleanmgr|compmgmt|diskmgmt|devmgmt|gpedit|secpol|lusrmgr|iscsicpl|odbcad32|recoverydrive|mdsched|msdt|rstrui|systempropertiesadvanced|wf|taskschd|quickassist|psr|narrator|magnify|osk|charmap|dxdiag|winver|explorer|windowsterminal|wt|openconsole|mrt|sysdm'
$systemFolder = 'Administrative Tools|Outils d.administration|Windows Tools|Outils Windows|System Tools|Outils syst|Accessibility|Accessibilit|Windows PowerShell|Maintenance'

# -- applications classiques : raccourcis du menu Démarrer --
$shell = New-Object -ComObject WScript.Shell
$roots = @("$env:ProgramData\Microsoft\Windows\Start Menu\Programs",
           "$env:APPDATA\Microsoft\Windows\Start Menu\Programs")
foreach ($root in $roots) {
    Get-ChildItem $root -Recurse -Filter *.lnk | ForEach-Object {
        $lnk = $shell.CreateShortcut($_.FullName)
        $target = $lnk.TargetPath
        if (-not $target -or -not $target.ToLower().EndsWith('.exe') -or -not (Test-Path $target)) { return }
        $name = $_.BaseName
        if ($name -match 'uninstall|désinstall|readme|lisez-moi|help|aide') { return }
        $id = [IO.Path]::GetFileNameWithoutExtension($target).ToLower()
        $folder = $_.DirectoryName.Substring($root.Length)
        $category = 'user'
        if ($target -like "$env:windir\*") { $category = 'windows' }
        if ($id -match "^($systemExe)$" -or $folder -match $systemFolder) { $category = 'system' }
        $icon = $null
        $ico = [System.Drawing.Icon]::ExtractAssociatedIcon($target)
        if ($ico) { $icon = Png64 $ico.ToBitmap(); $ico.Dispose() }
        Emit $name $id $_.FullName 'classic' $category $icon
    }
}

# -- applications du Store (et applis Windows modernes) --
$provisioned = @{}
Get-AppxProvisionedPackage -Online | ForEach-Object { $provisioned[$_.DisplayName] = $true }
$starts = @{}
Get-StartApps | ForEach-Object { $starts[$_.AppID] = $_.Name }

Get-AppxPackage -PackageTypeFilter Main | Where-Object { -not $_.IsFramework } | ForEach-Object {
    $pkg = $_
    $manifest = Join-Path $pkg.InstallLocation 'AppxManifest.xml'
    if (-not (Test-Path $manifest)) { return }
    [xml]$xml = Get-Content $manifest -Raw
    foreach ($app in @($xml.Package.Applications.Application)) {
        if (-not $app) { continue }
        $aumid = "$($pkg.PackageFamilyName)!$($app.Id)"
        if (-not $starts.ContainsKey($aumid)) { continue }  # pas dans le menu Démarrer
        $exe = $app.Executable
        $id = if ($exe -and $exe -notmatch '\$') { [IO.Path]::GetFileNameWithoutExtension($exe).ToLower() }
              else { ($pkg.Name -replace '^Microsoft\.', '').ToLower() }
        $category = 'user'
        if ($pkg.SignatureKind -eq 'System') { $category = 'system' }
        elseif ($provisioned.ContainsKey($pkg.Name)) { $category = 'windows' }
        if ($id -match "^($systemExe)$") { $category = 'system' }
        # logo : Square44x44Logo (ou Square150x150Logo), fichier réel = variante d'échelle
        $icon = $null
        $visual = $app.VisualElements
        $logo = if ($visual.Square44x44Logo) { $visual.Square44x44Logo } else { $visual.Square150x150Logo }
        if ($logo) {
            $base = Join-Path $pkg.InstallLocation $logo
            $dir = Split-Path $base
            $stem = [IO.Path]::GetFileNameWithoutExtension($base)
            $file = Get-ChildItem $dir -Filter "$stem*.png" |
                Sort-Object @{ e = { if ($_.Name -match 'targetsize-48(_altform-unplated)?\.png$') { 0 }
                                     elseif ($_.Name -match 'targetsize-(64|96|256)') { 1 }
                                     elseif ($_.Name -match 'scale-200') { 2 } else { 3 } } } |
                Select-Object -First 1
            if ($file) { $img = [System.Drawing.Image]::FromFile($file.FullName); $icon = Png64 $img; $img.Dispose() }
        }
        Emit $starts[$aumid] $id "shell:AppsFolder\$aumid" 'store' $category $icon
    }
}

$json = ConvertTo-Json -InputObject @($list) -Compress -Depth 3
$b64 = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json))
[IO.File]::WriteAllText("$env:TEMP\vasistas-apps.b64", $b64)
[Console]::Out.Write($b64.Length)
