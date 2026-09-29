"""Dossiers de Windows (Documents, Téléchargements, Images…) reliés aux dossiers Linux.

Relié, un dossier connu de Windows (Known Folder) pointe sur le lecteur du dossier Linux
partagé : « Enregistrer » de Word propose ~/Documents, Edge télécharge dans
~/Téléchargements. SHSetKnownFolderPath (registre User Shell Folders), sans déplacer ce que
contenait l'ancien dossier de C:\\Users\\vasistas. Le chemin d'avant est gardé
(config.json « folders_previous ») pour revenir en arrière. Un dossier Linux pas encore
partagé l'est au passage (branché à chaud).

Les fonctions qui parlent à Windows passent par le socket de contrôle de l'hôte : elles
bloquent, à appeler hors du fil GTK.
"""

import json
from pathlib import Path

from gi.repository import GLib

from . import control, files, vm

# clé, nom affiché, dossier Linux (XDG), identifiant Windows (KNOWNFOLDERID), dossier
# d'origine sous %USERPROFILE%, proposé par « Tout relier »
FOLDERS = [
    ("documents", "Documents", GLib.UserDirectory.DIRECTORY_DOCUMENTS,
     "FDD39AD0-238F-46AF-ADB4-6C85480369C7", "Documents", True),
    ("downloads", "Téléchargements", GLib.UserDirectory.DIRECTORY_DOWNLOAD,
     "374DE290-123F-4565-9164-39C4925E467B", "Downloads", True),
    ("pictures", "Images", GLib.UserDirectory.DIRECTORY_PICTURES,
     "33E28130-4E1E-4676-835A-98395C3BC3BB", "Pictures", True),
    ("music", "Musique", GLib.UserDirectory.DIRECTORY_MUSIC,
     "4BD8D571-6D19-48D3-BE97-422220080E43", "Music", False),
    ("videos", "Vidéos", GLib.UserDirectory.DIRECTORY_VIDEOS,
     "18989B1D-99B5-455B-841C-AB7C74E4DDFC", "Videos", False),
    ("desktop", "Bureau", GLib.UserDirectory.DIRECTORY_DESKTOP,
     "B4BFCC3A-DB2C-424C-B029-7FE99A87C641", "Desktop", False),
]
BY_KEY = {f[0]: f for f in FOLDERS}
SUGGESTED = [f[0] for f in FOLDERS if f[5]]
# fichiers que Windows pose dans ses dossiers : masqués dans Fichiers (.hidden)
WINDOWS_CLUTTER = ["desktop.ini", "Thumbs.db"]

_PS_API = r'''
Add-Type -Namespace Vasistas -Name KnownFolders -MemberDefinition @"
[DllImport("shell32.dll")] public static extern int SHGetKnownFolderPath(ref Guid id, uint flags, IntPtr token, out IntPtr path);
[DllImport("shell32.dll")] public static extern int SHSetKnownFolderPath(ref Guid id, uint flags, IntPtr token, [MarshalAs(UnmanagedType.LPWStr)] string path);
"@ -ErrorAction SilentlyContinue
function Get-KF($id) {
    $g = [Guid]$id; $p = [IntPtr]::Zero
    # KF_FLAG_DONT_VERIFY : chemin enregistré, même si le lecteur n'est pas encore monté
    [void][Vasistas.KnownFolders]::SHGetKnownFolderPath([ref]$g, 0x4000, [IntPtr]::Zero, [ref]$p)
    [Runtime.InteropServices.Marshal]::PtrToStringUni($p)
}
function Set-KF($id, $path) {
    $g = [Guid]$id
    [Vasistas.KnownFolders]::SHSetKnownFolderPath([ref]$g, 0, [IntPtr]::Zero, $path)
}
'''


def _q(text):
    """Chaîne PowerShell entre apostrophes."""
    return "'" + str(text).replace("'", "''") + "'"


def linux_dir(key) -> Path:
    return Path(GLib.get_user_special_dir(BY_KEY[key][2]) or Path.home() / BY_KEY[key][1])


def _exec(script, timeout=60):
    res = control.request({"exec": _PS_API + script}, timeout=timeout)
    if "error" in res:
        raise RuntimeError(res["error"])
    return res.get("out") or ""


def _json_out(out):
    for line in reversed(out.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise RuntimeError(out.strip()[-300:] or "pas de réponse de Windows")


def status():
    """{clé: chemin Windows actuel} lu dans Windows."""
    script = "$r = @{}\n" + "".join(f"$r['{k}'] = Get-KF '{kid}'\n" for k, _, _, kid, _, _ in FOLDERS)
    script += "$r | ConvertTo-Json -Compress\n"
    return _json_out(_exec(script))


def linked():
    """{clé: chemin Windows} des dossiers reliés (config.json)."""
    return dict(vm.load_config().get("folders") or {})


def _ensure_share(key):
    """Chemin Windows du dossier Linux, partagé au besoin : (chemin, message ou None)."""
    path = linux_dir(key)
    path.mkdir(parents=True, exist_ok=True)
    win = files.to_windows(path)
    if win is not None:
        return win
    added = vm.add_share(path, BY_KEY[key][1])
    if added is None:
        raise RuntimeError("plus de lettre de lecteur libre dans Windows")
    tag, drive, label = added
    if not vm.hotplug_share(tag, path):
        raise RuntimeError(f"« {label} » sera partagé au prochain démarrage de Windows ; "
                           "relier le dossier ensuite")
    out = _exec(vm.mount_script(tag, drive, label))
    if "True" not in out:
        raise RuntimeError(f"le lecteur {drive} ne répond pas dans Windows")
    return files.to_windows(path)


def _hide_clutter(path: Path):
    """desktop.ini et Thumbs.db de Windows masqués dans Fichiers (fichier .hidden)."""
    hidden = path / ".hidden"
    try:
        have = set(hidden.read_text().splitlines()) if hidden.exists() else set()
        missing = [n for n in WINDOWS_CLUTTER if n not in have]
        if missing:
            with open(hidden, "a") as f:
                f.write("".join(n + "\n" for n in missing))
    except OSError:
        pass


def link(key):
    """Relie le dossier Windows `key` au dossier Linux correspondant. Rend le chemin Windows."""
    _, name, _, kid, _, _ = BY_KEY[key]
    target = _ensure_share(key)
    _hide_clutter(linux_dir(key))
    root = target if target.endswith("\\") else target + "\\"
    out = _exec(
        f"$before = Get-KF '{kid}'\n"
        f"if (-not (Test-Path -LiteralPath {_q(root)})) {{ throw 'lecteur absent' }}\n"
        f"$rc = Set-KF '{kid}' {_q(target)}\n"
        "@{ before = $before; rc = $rc; after = (Get-KF '" + kid + "') } | ConvertTo-Json -Compress\n")
    res = _json_out(out)
    if res.get("rc") != 0:
        raise RuntimeError(f"SHSetKnownFolderPath : code {res.get('rc')}")
    cfg = vm.load_config()
    prev = cfg.setdefault("folders_previous", {})
    before = res.get("before") or ""
    # le chemin d'origine seulement : pas un lecteur Vasistas d'une liaison précédente
    if key not in prev and before and before.upper()[:2] not in {d.upper() for _, _, d, _ in vm.shares()}:
        prev[key] = before
    cfg.setdefault("folders", {})[key] = target
    vm.save_config(cfg)
    return target


def unlink(key):
    """Rend au dossier Windows `key` son chemin d'avant (C:\\Users\\vasistas\\… par défaut)."""
    _, name, _, kid, default, _ = BY_KEY[key]
    cfg = vm.load_config()
    prev = (cfg.get("folders_previous") or {}).get(key) or f"%USERPROFILE%\\{default}"
    out = _exec(
        f"$p = [Environment]::ExpandEnvironmentVariables({_q(prev)})\n"
        "New-Item -ItemType Directory -Force -Path $p | Out-Null\n"
        f"$rc = Set-KF '{kid}' $p\n"
        "@{ rc = $rc; after = (Get-KF '" + kid + "') } | ConvertTo-Json -Compress\n")
    res = _json_out(out)
    if res.get("rc") != 0:
        raise RuntimeError(f"SHSetKnownFolderPath : code {res.get('rc')}")
    cfg = vm.load_config()
    (cfg.get("folders") or {}).pop(key, None)
    (cfg.get("folders_previous") or {}).pop(key, None)
    vm.save_config(cfg)
    return res.get("after")


def using_drive(drive):
    """Dossiers reliés qui passent par ce lecteur (à délier avant de retirer le partage)."""
    d = drive.upper().rstrip("\\")
    return [k for k, p in linked().items() if p.upper().startswith(d)]
