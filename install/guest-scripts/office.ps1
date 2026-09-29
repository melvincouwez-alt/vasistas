# Office par l'outil de déploiement Office (ODT), sans fenêtre.
# $ConfigXml : fichier de configuration ODT, écrit par l'hôte (catalog.office_config).
# À défaut : Microsoft 365 Business Standard/Premium, 64 bits, canal $Channel (Current si
# absent), langue de Windows. L'activation se fait à la première ouverture, avec le compte
# de l'utilisateur.
# Dernière ligne : VASISTAS-RESULT {"code", "ok", "apps", "products", "version"}.
$ErrorActionPreference = 'Stop'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
if (-not $Channel) { $Channel = 'Current' }
if (-not $ConfigXml) {
    $ConfigXml = @"
<Configuration>
  <Add OfficeClientEdition="64" Channel="$Channel" AllowCdnFallback="True">
    <Product ID="O365BusinessRetail">
      <Language ID="MatchOS" Fallback="en-us" />
      <ExcludeApp ID="Groove" />
      <ExcludeApp ID="Lync" />
      <ExcludeApp ID="Teams" />
      <ExcludeApp ID="Publisher" />
    </Product>
  </Add>
  <Property Name="AUTOACTIVATE" Value="0" />
  <Property Name="FORCEAPPSHUTDOWN" Value="FALSE" />
  <Property Name="SharedComputerLicensing" Value="0" />
  <Updates Enabled="TRUE" />
  <Display Level="None" AcceptEULA="TRUE" />
</Configuration>
"@
}
$dir = 'C:\Vasistas\odt'
New-Item -ItemType Directory -Force -Path $dir | Out-Null
$ProgressPreference = 'SilentlyContinue'
Invoke-WebRequest -UseBasicParsing 'https://officecdn.microsoft.com/pr/wsus/setup.exe' -OutFile "$dir\setup.exe"
Set-Content -Encoding UTF8 -Path "$dir\config.xml" -Value $ConfigXml
$p = Start-Process "$dir\setup.exe" -ArgumentList '/configure', "`"$dir\config.xml`"" -Wait -PassThru -NoNewWindow
$c = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Office\ClickToRun\Configuration' -ErrorAction SilentlyContinue
$apps = Get-ChildItem 'C:\Program Files\Microsoft Office\root\Office16' -Filter *.exe -ErrorAction SilentlyContinue |
    Where-Object Name -in 'WINWORD.EXE','EXCEL.EXE','POWERPNT.EXE','OUTLOOK.EXE','ONENOTE.EXE','MSACCESS.EXE','VISIO.EXE','WINPROJ.EXE' |
    ForEach-Object Name
$r = @{ code = $p.ExitCode; ok = ($p.ExitCode -eq 0 -and @($apps).Count -gt 0); apps = @($apps);
        products = @(if ($c.ProductReleaseIds) { $c.ProductReleaseIds -split ',' }); version = $c.VersionToReport }
'VASISTAS-RESULT ' + ($r | ConvertTo-Json -Compress)
