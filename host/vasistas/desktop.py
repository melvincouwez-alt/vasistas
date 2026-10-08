"""Lanceurs .desktop pour les applications Windows (Word, Excel, PowerPoint, Outlook, Power BI)."""

import json
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

from .winctl import APP_ID

APPS_DIR = Path.home() / ".local/share/applications"
HOST_DIR = Path(__file__).resolve().parents[1]
ICONS_SRC = HOST_DIR.parent / "data/icons/hicolor"
ICONS_DIR = Path.home() / ".local/share/icons/hicolor"

# nom affiché, identifiant (nom de l'exécutable en minuscules), commande Windows, icône
# du thème en attendant l'icône extraite de l'exécutable
APPS = [
    ("Word", "winword", "winword", "x-office-document"),
    ("Excel", "excel", "excel", "x-office-spreadsheet"),
    ("PowerPoint", "powerpnt", "powerpnt", "x-office-presentation"),
    ("Outlook", "outlook", "outlook", "internet-mail"),
    ("Power BI", "pbidesktop",
     r"C:\Program Files\Microsoft Power BI Desktop\bin\PBIDesktop.exe", "office-chart-bar"),
]


# Applications proposées en plusieurs versions : choix de l'utilisateur dans config.json
# ({"variants": {"outlook": "new"}}), première version par défaut.
VARIANTS = {
    "outlook": {
        "classic": "outlook",
        "new": r"shell:AppsFolder\Microsoft.OutlookForWindows_8wekyb3d8bbwe!Microsoft.OutlookforWindows",
    },
}
# exécutable d'une version -> identifiant de l'application : même lanceur, même icône dans le dock
ALIASES = {"olk": "outlook"}


# identifiant venu de l'invité : il entre dans un lanceur (ligne Exec) et un chemin d'icône
_APP_ID = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


def canonical_app(app):
    if not app or not _APP_ID.fullmatch(app):
        return None
    return ALIASES.get(app, app)


def variant(app):
    from .vm import load_config
    choices = VARIANTS[app]
    chosen = load_config().get("variants", {}).get(app)
    return chosen if chosen in choices else next(iter(choices))


def set_variant(app, choice):
    from .vm import load_config, save_config
    cfg = load_config()
    cfg.setdefault("variants", {})[app] = choice
    save_config(cfg)


def launch_command(app):
    """Commande Windows d'une application connue, ou None."""
    if app in VARIANTS:
        return VARIANTS[app][variant(app)]
    entry = load_registry().get(app)
    return entry["exe"] if entry else None


# -- options des lanceurs (page « Menu Applications ») --
# `launcher_suffix` (faux) : « Word (Windows) » au lieu de « Word » ; `launcher_emblem` (vrai) :
# emblème Vasistas sur les icônes ; `hidden_apps` : applications masquées du menu par
# l'utilisateur (NoDisplay=true, lanceur gardé pour le dock et les fichiers).

def launcher_options(cfg=None):
    """(suffixe, emblème, applications masquées)."""
    if cfg is None:
        from .vm import load_config
        cfg = load_config()
    hidden = cfg.get("hidden_apps")
    return (bool(cfg.get("launcher_suffix", False)), cfg.get("launcher_emblem", True) is not False,
            set(hidden) if isinstance(hidden, list) else set())


def launcher_name(name, suffix):
    from .i18n import _
    name = base_name(name)
    return _("{name} (Windows)", name=name) if suffix else name


def base_name(name):
    """Nom sans le suffixe « (Windows) » (ni sa traduction)."""
    from .i18n import _
    name = (name or "").replace("\n", " ").strip()
    for suffix in {" (Windows)", " " + _("{name} (Windows)", name="").strip()}:
        if name.endswith(suffix) and len(name) > len(suffix):
            return name[:-len(suffix)]
    return name


def entry_text(app, name, icon, launcher, categories=None, hidden=False, suffix=False):
    """Contenu du lanceur .desktop d'une application Windows."""
    from .i18n import _
    return ("[Desktop Entry]\n"
            "Type=Application\n"
            f"Name={launcher_name(name or app, suffix)}\n"
            f"GenericName={_('Application Windows (Vasistas)')}\n"
            f"Exec={launcher} launch-app {app} %F\n"
            f"Icon={icon}\n"
            + (f"Categories={categories}\n" if categories else "")
            + ("NoDisplay=true\n" if hidden else "")
            + f"StartupWMClass={app_desktop_id(app)}\n"
            "X-Vasistas=1\n")


def set_hidden(app, hidden):
    """Retient le choix « masqué du menu » (hidden_apps) : il survit à la réécriture des lanceurs."""
    from .vm import load_config, save_config
    cfg = load_config()
    apps = set(cfg.get("hidden_apps") or [])
    apps = apps | {app} if hidden else apps - {app}
    if sorted(apps) != sorted(cfg.get("hidden_apps") or []):
        cfg["hidden_apps"] = sorted(apps)
        save_config(cfg)


def apply_launcher_options():
    """Réécrit nom et visibilité de tous les lanceurs Vasistas d'après les options, recompose
    les icônes (emblème) et met à jour les caches du menu."""
    suffix, _emblem, hidden = launcher_options()
    reg = load_registry()
    prefix = app_desktop_id("")
    for path in sorted(APPS_DIR.glob(f"{prefix}*.desktop")):
        try:
            text = path.read_text()
        except OSError:
            continue
        if "X-Vasistas=1" not in text:
            continue
        app = path.name[len(prefix):-len(".desktop")]
        lines = text.splitlines()
        current = next((line[5:] for line in lines if line.startswith("Name=")), app)
        name = "Outlook" if app == "outlook" else (reg.get(app) or {}).get("name") or base_name(current)
        out = []
        for line in lines:
            if line.startswith("Name="):
                line = f"Name={launcher_name(name, suffix)}"
            elif line.startswith("NoDisplay=") and app in hidden:
                continue
            out.append(line)
        if app in hidden:
            out.insert(next((i for i, line in enumerate(out) if line.startswith("StartupWMClass=")), len(out)),
                       "NoDisplay=true")
        new = "\n".join(out) + "\n"
        if new != text:
            path.write_text(new)
    refresh_icons()


def install_icons():
    """Icône de Vasistas (fenêtre à vasistas, imposte basculée), une par taille."""
    for src in ICONS_SRC.glob(f"*/apps/{APP_ID}.svg"):
        dest = ICONS_DIR / src.relative_to(ICONS_SRC)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    ICONS_DIR.touch()


def install():
    from .i18n import _
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    install_icons()
    launcher = _launcher()
    main = APPS_DIR / f"{APP_ID}.desktop"
    main.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Vasistas\n"
        f"GenericName={_('Applications Windows')}\n"
        f"Comment={_('Les applications de la machine virtuelle Windows, chacune dans sa propre fenêtre')}\n"
        f"Exec={launcher} run\n"
        f"Icon={APP_ID}\n"
        "Categories=System;Emulator;\n"
        f"StartupWMClass={APP_ID}\n"
        "NoDisplay=true\n"
    )
    print(main)
    # entrée « Vasistas » du menu : l'application compagnon
    companion = APPS_DIR / f"{APP_ID}.Companion.desktop"
    companion.write_text(
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=Vasistas\n"
        f"GenericName={_('Applications Windows')}\n"
        f"Comment={_('Démarrer Windows et choisir les applications du menu')}\n"
        f"Exec={launcher} companion\n"
        f"Icon={APP_ID}\n"
        "Categories=System;Emulator;\n"
        "Keywords=Windows;VM;machine virtuelle;\n"
        f"StartupWMClass={APP_ID}.Companion\n"
    )
    print(companion)
    reg = load_registry()
    suffix, _emblem, hidden = launcher_options()
    for name, app, exe, icon in APPS:
        reg.setdefault(app, {"name": name, "exe": exe})
        path = APPS_DIR / f"{app_desktop_id(app)}.desktop"
        path.write_text(entry_text(app, name, app_icon_name(app) if app_icon_path(app).exists() else icon,
                                   launcher, "Office;", app in hidden, suffix))
        print(path)
    save_registry(reg)
    refresh_icons()
    # lanceurs réécrits : les types de fichiers ouverts dans Windows sont à redéclarer
    from . import files
    files.apply()
    return 0


# -- applications découvertes dans l'invité --
# Chaque fenêtre porte l'identifiant Wayland APP_ID.<app> ; le dock le relie au lanceur
# du même nom, qui donne l'icône et le nom. Ces lanceurs sont créés à la première fenêtre.

APP_ICONS_DIR = ICONS_DIR / "256x256/apps"
CUSTOM_ICONS = Path.home() / ".local/share/vasistas/icons-custom"


def app_icon_name(app):
    return f"{APP_ID}.{app}"


def app_icon_path(app):
    return APP_ICONS_DIR / f"{app_icon_name(app)}.png"


def _launcher():
    """Commande des lanceurs : ~/.local/bin/vasistas pour une version installée (elle suit les
    mises à jour), sinon cette copie (dépôt de développement)."""
    from . import updates
    if updates.mode() == "installed" and updates.WRAPPER.exists():
        return shlex.quote(str(updates.WRAPPER))
    return f"env PYTHONPATH={shlex.quote(str(HOST_DIR))} {shlex.quote(sys.executable)} -m vasistas"


def _refresh_caches():
    try:
        ICONS_DIR.touch()
        subprocess.Popen(["gtk-update-icon-cache", "-q", "-f", "-t", str(ICONS_DIR)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.Popen(["update-desktop-database", "-q", str(APPS_DIR)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


# icônes dessinées au style elementary (projet Lucarne, s'il est installé) : prioritaires sur
# l'icône extraite de Windows ; sinon une icône du thème au nom de l'application
NATIVE_ICONS = {
    "winword": "lucarne-word", "excel": "lucarne-excel", "powerpnt": "lucarne-powerpoint",
    "outlook": "lucarne-outlook", "pbidesktop": "lucarne-powerbi", "onenote": "lucarne-onenote",
    "msteams": "lucarne-teams", "ms-teams": "lucarne-teams", "olk": "lucarne-outlook",
}


def install_app_icon(app, png: bytes = None):
    """Pose l'icône de l'application : dessin elementary s'il existe, sinon l'icône
    Windows (png, ou l'originale gardée) calée au gabarit. Vrai si une icône est posée."""
    from . import icons
    from .vm import DATA
    badge = launcher_options()[1]
    custom = CUSTOM_ICONS / f"{app}.png"
    if custom.exists():
        icons.install(app_icon_name(app), custom.read_bytes(), ICONS_DIR, badge)
        return True
    svgs = icons.find_native([n for n in (NATIVE_ICONS.get(app), app) if n])
    if svgs:
        icons.install_native(app_icon_name(app), svgs, ICONS_DIR, badge)
        return True
    raw = DATA / "icons" / f"{app}.png"
    if png is None and raw.exists():
        png = raw.read_bytes()
    if png is None:
        return False
    icons.install(app_icon_name(app), png, ICONS_DIR, badge)
    return True


def set_custom_icon(app, png: bytes = None):
    """Icône choisie par l'utilisateur (png), ou retour à l'icône d'origine (None)."""
    custom = CUSTOM_ICONS / f"{app}.png"
    if png is None:
        custom.unlink(missing_ok=True)
    else:
        custom.parent.mkdir(parents=True, exist_ok=True)
        custom.write_bytes(png)
    if install_app_icon(app):
        _set_desktop_icon(app)
    _refresh_caches()


def refresh_icons():
    """Recompose toutes les icônes connues (après l'ajout d'un dessin elementary)."""
    from .vm import DATA
    apps = {p.stem for p in (DATA / "icons").glob("*.png")} | set(NATIVE_ICONS)
    for app in sorted(apps):
        if install_app_icon(app):
            _set_desktop_icon(app)
    _refresh_caches()


def _set_desktop_icon(app):
    desktop = APPS_DIR / f"{app_desktop_id(app)}.desktop"
    if desktop.exists():
        text = desktop.read_text()
        if "X-Vasistas=1" in text:
            lines = [f"Icon={app_icon_name(app)}" if line.startswith("Icon=") else line
                     for line in text.splitlines()]
            desktop.write_text("\n".join(lines) + "\n")


def save_app_icon(app, png: bytes):
    """Icône extraite de l'exécutable Windows, composée au format elementary (voir icons.py).
    L'originale est gardée dans ~/.local/share/vasistas/icons pour la personnalisation."""
    from . import icons
    from .vm import DATA
    raw = DATA / "icons" / f"{app}.png"
    if raw.exists() and raw.read_bytes() == png and app_icon_path(app).exists():
        return
    raw.parent.mkdir(parents=True, exist_ok=True)
    raw.write_bytes(png)
    install_app_icon(app, png)
    _set_desktop_icon(app)
    _refresh_caches()


def load_registry() -> dict:
    """Applications vues dans l'invité : {id: {name, exe}}. Le lanceur ne contient que
    l'identifiant (les chemins Windows et leurs « \\ » sont invalides dans Exec=)."""
    from .vm import DATA
    path = DATA / "apps.json"
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def save_registry(reg: dict):
    from .vm import DATA
    path = DATA / "apps.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(reg, indent=2, ensure_ascii=False))


def ensure_app(app, name, exe):
    """Lanceur pour une application vue dans l'invité, s'il n'existe pas encore.
    Masqué du menu (NoDisplay) : il sert au dock ; l'application compagnon choisira
    lesquelles afficher. Nom de fichier en minuscules : Gala compare en minuscules."""
    reg = load_registry()
    entry = {"name": name or app, "exe": exe or app}
    if reg.get(app) != entry:
        reg[app] = {**reg.get(app, {}), **entry}
        save_registry(reg)
    desktop = APPS_DIR / f"{app_desktop_id(app)}.desktop"
    if desktop.exists():
        return False
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    icon = app_icon_name(app) if app_icon_path(app).exists() else APP_ID
    desktop.write_text(entry_text(app, name, icon, _launcher(), hidden=True, suffix=launcher_options()[0]))
    _refresh_caches()
    return True


def app_desktop_id(app):
    """Identifiant Wayland de ses fenêtres et nom du lanceur, en minuscules."""
    return f"{APP_ID}.{app}".lower()


# -- menu Applications (application compagnon) --

MENU_CATEGORIES = {"user": "Office;", "windows": "Utility;", "system": "System;"}


def in_menu(app):
    """Vrai si le lanceur de l'application est visible dans le menu Applications."""
    path = APPS_DIR / f"{app_desktop_id(app)}.desktop"
    try:
        return "NoDisplay=true" not in path.read_text()
    except OSError:
        return False


def set_in_menu(app, visible, name=None, cmd=None, category="user", png: bytes = None):
    """Montre ou cache une application dans le menu Applications. Le lanceur est créé au
    besoin (commande Windows `cmd` enregistrée dans apps.json) ; caché, il reste pour le dock."""
    reg = load_registry()
    if cmd and app not in VARIANTS:
        reg[app] = {**reg.get(app, {}), "name": name or app, "exe": cmd}
        save_registry(reg)
    path = APPS_DIR / f"{app_desktop_id(app)}.desktop"
    if not path.exists():
        if png is not None and not app_icon_path(app).exists():
            save_app_icon(app, png)
        APPS_DIR.mkdir(parents=True, exist_ok=True)
        icon = app_icon_name(app) if app_icon_path(app).exists() else APP_ID
        path.write_text(entry_text(app, name, icon, _launcher(), MENU_CATEGORIES.get(category, "Utility;"),
                                   suffix=launcher_options()[0]))
    lines = [line for line in path.read_text().splitlines() if not line.startswith("NoDisplay=")]
    if not visible:
        lines.append("NoDisplay=true")
    path.write_text("\n".join(lines) + "\n")
    set_hidden(app, not visible)
    _refresh_caches()



# -- types de fichiers (files.py) --

def set_mime_types(app, mimes):
    """Types de fichiers que le lanceur de l'application ouvre (MimeType=), aucun si vide.
    Exec reçoit %F : les fichiers choisis dans Fichiers arrivent à `launch-app`."""
    path = APPS_DIR / f"{app_desktop_id(app)}.desktop"
    try:
        text = path.read_text()
    except OSError:
        return
    lines = []
    for line in text.splitlines():
        if line.startswith("MimeType="):
            continue
        if line.startswith("Exec=") and "launch-app" in line and "%F" not in line:
            line += " %F"
        lines.append(line)
    if mimes:
        lines.append("MimeType=" + "".join(f"{m};" for m in mimes))
    new = "\n".join(lines) + "\n"
    if new != text:
        path.write_text(new)


def launchers_with_mime():
    """Applications dont le lanceur Vasistas déclare des types de fichiers."""
    out = set()
    prefix = app_desktop_id("")
    for path in APPS_DIR.glob(f"{prefix}*.desktop"):
        try:
            text = path.read_text()
        except OSError:
            continue
        if "X-Vasistas=1" in text and "\nMimeType=" in text:
            out.add(path.name[len(prefix):-len(".desktop")])
    return out


def refresh_desktop_database():
    try:
        subprocess.run(["update-desktop-database", "-q", str(APPS_DIR)], timeout=30,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass
