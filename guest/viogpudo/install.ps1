# Pose le viogpudo compilé par build.sh (vsync à 60 Hz) à côté du pilote d'origine.
# Variables attendues avant ce script : $SysB64 (contenu du .sys en base64) ou $Restore = $true.
# Le service du périphérique pointe vers System32\drivers\viogpudo-vasistas.sys ; l'ancien ImagePath est gardé
# dans ImagePathVasistasOrig pour revenir en arrière. Il faut le mode test-signing (redémarrage).
$ErrorActionPreference = 'Stop'
# service du périphérique (VioGpuDod), lu sur la carte VirtIO GPU
$dev = Get-PnpDevice -Class Display | Where-Object FriendlyName -like '*VirtIO GPU*' | Select-Object -First 1
$name = (Get-PnpDeviceProperty -InstanceId $dev.InstanceId -KeyName DEVPKEY_Device_Service).Data
$svc = "HKLM:\SYSTEM\CurrentControlSet\Services\$name"
$dst = "$env:SystemRoot\System32\drivers\viogpudo-vasistas.sys"

if ($Restore) {
    $orig = (Get-ItemProperty $svc).ImagePathVasistasOrig
    if ($orig) {
        Set-ItemProperty $svc ImagePath $orig
        Remove-ItemProperty $svc ImagePathVasistasOrig
        Remove-ItemProperty $svc VasistasVsync -ErrorAction SilentlyContinue
    }
    "pilote d'origine rétabli ($orig), effectif au prochain démarrage"
    return
}

# certificat de test, créé une fois, approuvé par la machine
$cert = Get-ChildItem Cert:\LocalMachine\My -CodeSigningCert | Where-Object Subject -eq 'CN=Vasistas test' | Select-Object -First 1
if (-not $cert) {
    $cert = New-SelfSignedCertificate -Type CodeSigningCert -Subject 'CN=Vasistas test' `
        -CertStoreLocation Cert:\LocalMachine\My -KeyAlgorithm RSA -KeyLength 2048 -NotAfter (Get-Date).AddYears(10)
    foreach ($store in 'Root', 'TrustedPublisher') {
        $s = New-Object System.Security.Cryptography.X509Certificates.X509Store($store, 'LocalMachine')
        $s.Open('ReadWrite'); $s.Add($cert); $s.Close()
    }
}

$tmp = "$env:TEMP\viogpudo-vasistas.sys"
[IO.File]::WriteAllBytes($tmp, [Convert]::FromBase64String($SysB64))
$sig = Set-AuthenticodeSignature -FilePath $tmp -Certificate $cert -HashAlgorithm SHA256
if ($sig.Status -ne 'Valid') { throw "signature : $($sig.Status) $($sig.StatusMessage)" }
# un pilote chargé verrouille son fichier : on peut le renommer, pas l'écraser
Remove-Item "$dst.old*" -Force -ErrorAction SilentlyContinue
if (Test-Path $dst) { Rename-Item $dst ("$dst.old" + (Get-Date -Format 'HHmmss')) }
Copy-Item $tmp $dst -Force

$cur = (Get-ItemProperty $svc).ImagePath
if (-not (Get-ItemProperty $svc).ImagePathVasistasOrig) {
    New-ItemProperty $svc -Name ImagePathVasistasOrig -Value $cur -PropertyType ExpandString | Out-Null
}
Set-ItemProperty $svc ImagePath '\SystemRoot\System32\drivers\viogpudo-vasistas.sys'
# vsync simulé complet (fonctions DDI + minuterie + notification), voir viogpudo.h
New-ItemProperty $svc -Name VasistasVsync -Value 7 -PropertyType DWord -Force | Out-Null

$ts = (bcdedit /enum '{current}') -match 'testsigning\s+Yes'
if (-not $ts) { bcdedit /set testsigning on | Out-Null; "test-signing activé" }
"pilote posé ($dst), redémarrer Windows"
