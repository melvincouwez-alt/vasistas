# Power BI Desktop (version hors Store), installation silencieuse par winget.
# Dernière ligne : VASISTAS-RESULT {"code", "ok", "path"}.
$common = @('--accept-source-agreements', '--disable-interactivity')
winget install --id Microsoft.PowerBI -e --silent --accept-package-agreements --scope machine @common *> $null
$code = $LASTEXITCODE
$exe = Get-ChildItem 'C:\Program Files\Microsoft Power BI Desktop\bin' -Filter PBIDesktop.exe -ErrorAction SilentlyContinue |
    Select-Object -First 1 -ExpandProperty FullName
$r = @{ code = $code; ok = [bool]$exe; path = $exe }
'VASISTAS-RESULT ' + ($r | ConvertTo-Json -Compress)
