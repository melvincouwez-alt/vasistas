"""Ouverture des fichiers Linux dans les applications Windows (extensions désignées).

Chaque extension désignée est reliée à une application Windows, dans config.json :
`open_with` = {"docx": "winword", …}. Côté bureau, le lanceur de l'application
(APP_ID.<app>.desktop) déclare les types MIME de ses extensions et devient l'application par
défaut pour ces types (~/.config/mimeapps.list). L'application qui l'était avant est notée
dans `open_with_previous` et reprend sa place quand l'extension n'est plus désignée.

Windows ne voit que les dossiers partagés : à l'ouverture, le chemin Linux devient un chemin
Windows par ces partages (~/Documents/a.docx -> Z:\\a.docx). Un fichier hors partage est
proposé à l'utilisateur par l'hôte (copie dans Téléchargements, ou partage de son dossier).
"""

import os
import subprocess
from pathlib import Path

from gi.repository import Gio, GLib

from . import desktop, vm

# Applications et extensions proposées ; les extensions du 2e ensemble sont désignées
# d'office (Office et Power BI). CSV, RTF, OpenDocument et EML restent au choix : Linux sait
# déjà les ouvrir.
CATALOG = [
    ("winword", "Word", ["docx", "doc", "docm", "dotx", "dotm", "dot", "rtf", "odt"],
     {"docx", "doc", "docm", "dotx", "dotm", "dot"}),
    ("excel", "Excel", ["xlsx", "xls", "xlsm", "xlsb", "xltx", "xltm", "xlt", "csv", "ods"],
     {"xlsx", "xls", "xlsm", "xlsb", "xltx", "xltm", "xlt"}),
    ("powerpnt", "PowerPoint", ["pptx", "ppt", "pptm", "ppsx", "pps", "potx", "pot", "odp"],
     {"pptx", "ppt", "pptm", "ppsx", "pps", "potx", "pot"}),
    ("outlook", "Outlook", ["msg", "eml"], {"msg"}),
    ("pbidesktop", "Power BI", ["pbix", "pbit", "pbids"], {"pbix", "pbit", "pbids"}),
]
DEFAULTS = {ext: app for app, _, _, on in CATALOG for ext in on}

# Types que la base MIME du système ne connaît pas : déclarés par Vasistas.
KNOWN_TYPES = {
    "msg": ("application/vnd.ms-outlook", "Message Outlook", "x-office-document", None),
    "pbix": ("application/x-powerbi-report", "Rapport Power BI", "x-office-spreadsheet", "application/zip"),
    "pbit": ("application/x-powerbi-template", "Modèle Power BI", "x-office-spreadsheet", "application/zip"),
    "pbids": ("application/x-powerbi-datasource", "Source de données Power BI", "x-office-spreadsheet",
              "application/json"),
    "one": ("application/onenote", "Section OneNote", "x-office-document", None),
}
# Extensions au type ambigu (.dot : Graphviz ou modèle Word, .pot : gettext ou PowerPoint) :
# type Office imposé, sans toucher à la base MIME (les fichiers Graphviz restent à Graphviz).
EXPLICIT = {"dot": "application/msword-template", "pot": "application/vnd.ms-powerpoint"}
# Types trop larges pour être confiés à une application Windows : l'extension reçoit son
# propre type (sous-type du type large), sinon désigner .log enverrait tous les .txt à Word.
GENERIC = {"application/octet-stream", "text/plain", "application/zip", "application/xml", "text/xml",
           "application/json", "application/x-zerosize"}
# Dossiers temporaires : les pièces jointes y sont extraites, pas question de les partager.
TEMP_DIRS = [Path("/tmp"), Path("/var/tmp"), Path.home() / ".cache", Path(f"/run/user/{os.getuid()}")]

MIME_DIR = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "mime"
MIME_PACKAGE = MIME_DIR / "packages" / "io.github.melvincouwez.Vasistas.xml"
MIMEAPPS = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "mimeapps.list"
OURS = desktop.app_desktop_id("")  # préfixe des lanceurs Vasistas : « io.github.melvincouwez.vasistas. »


def normalize(ext):
    return ext.strip().lower().lstrip("*").lstrip(".")


def designations() -> dict:
    """{extension: application} ; les choix par défaut tant que rien n'a été enregistré."""
    cfg = vm.load_config().get("open_with")
    return dict(cfg) if isinstance(cfg, dict) else dict(DEFAULTS)


def app_name(app):
    for a, name, _, _ in CATALOG:
        if a == app:
            return name
    entry = desktop.load_registry().get(app)
    return entry["name"] if entry else app


def catalog(desig=None):
    """[(application, nom, [extensions])] : le catalogue, plus les extensions et applications
    ajoutées par l'utilisateur."""
    desig = designations() if desig is None else desig
    out = [(app, name, list(exts)) for app, name, exts, _ in CATALOG]
    listed = {e for _, _, exts in out for e in exts}
    for ext, app in sorted(desig.items()):
        if ext in listed:
            continue
        entry = next((o for o in out if o[0] == app), None)
        if entry is None:
            entry = (app, app_name(app), [])
            out.append(entry)
        entry[2].append(ext)
    return out


def mime_for(ext):
    """Type MIME d'une extension ; propre à Vasistas si le type du système est trop large."""
    ext = normalize(ext)
    if ext in KNOWN_TYPES:
        return KNOWN_TYPES[ext][0]
    if ext in EXPLICIT:
        return EXPLICIT[ext]
    ct, uncertain = Gio.content_type_guess(f"x.{ext}", None)
    mime = Gio.content_type_get_mime_type(ct) or ct
    if uncertain or mime in GENERIC:
        return f"application/x-vasistas-{ext}"
    return mime


def _base_type(ext):
    ct, uncertain = Gio.content_type_guess(f"x.{ext}", None)
    mime = Gio.content_type_get_mime_type(ct) or ct
    return None if uncertain or mime == "application/octet-stream" else mime


def siblings(ext):
    """Extensions du catalogue au même type MIME (xls et xlt, ppt et pps) : désignées ensemble,
    puisque l'association se fait par type."""
    mime = mime_for(ext)
    return {e for _, _, exts, _ in CATALOG for e in exts if mime_for(e) == mime} | {normalize(ext)}


# -- base MIME de l'utilisateur --

def _mime_package(desig):
    types = {}
    for ext, (mime, comment, icon, parent) in KNOWN_TYPES.items():
        types[mime] = (ext, comment, icon, parent)
    for ext in desig:
        mime = mime_for(ext)
        if mime.startswith("application/x-vasistas-"):
            types[mime] = (ext, f"Fichier .{ext}", "x-office-document", _base_type(ext))
    lines = ['<?xml version="1.0" encoding="UTF-8"?>',
             '<!-- Écrit par Vasistas (files.py) : types des extensions ouvertes dans Windows -->',
             '<mime-info xmlns="http://www.freedesktop.org/standards/shared-mime-info">']
    for mime, (ext, comment, icon, parent) in sorted(types.items()):
        lines.append(f'  <mime-type type="{mime}">')
        lines.append(f"    <comment>{GLib.markup_escape_text(comment)}</comment>")
        if parent:
            lines.append(f'    <sub-class-of type="{parent}"/>')
        lines.append(f'    <generic-icon name="{icon}"/>')
        lines.append(f'    <glob pattern="*.{ext}" weight="60"/>')
        lines.append("  </mime-type>")
    lines.append("</mime-info>")
    return "\n".join(lines) + "\n"


def _write_mime_package(desig):
    text = _mime_package(desig)
    try:
        if MIME_PACKAGE.read_text() == text:
            return False
    except OSError:
        pass
    MIME_PACKAGE.parent.mkdir(parents=True, exist_ok=True)
    MIME_PACKAGE.write_text(text)
    try:
        subprocess.run(["update-mime-database", str(MIME_DIR)], timeout=60,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass
    return True


# -- associations (mimeapps.list) --

def _keyfile():
    kf = GLib.KeyFile()
    try:
        kf.load_from_file(str(MIMEAPPS), GLib.KeyFileFlags.KEEP_COMMENTS)
    except GLib.Error:
        pass
    return kf


def _get_list(kf, group, key):
    try:
        return [v for v in kf.get_string(group, key).split(";") if v]
    except GLib.Error:
        return []


def _set_list(kf, group, key, values):
    if values:
        kf.set_string(group, key, ";".join(values) + ";")
    else:
        try:
            kf.remove_key(group, key)
        except GLib.Error:
            pass


def _associate(desired: dict, previous: dict):
    """desired : {type MIME: lanceur Vasistas}. Les types dont Vasistas était l'application
    par défaut et qui ne sont plus désignés reviennent à l'application d'avant."""
    kf = _keyfile()
    D, A = "Default Applications", "Added Associations"
    current = set()
    if kf.has_group(D):
        current = {k for k in kf.get_keys(D)[0] if any(v.startswith(OURS) for v in _get_list(kf, D, k))}
    for mime, did in desired.items():
        before = _get_list(kf, D, mime)
        if before and not before[0].startswith(OURS) and mime not in previous:
            previous[mime] = before[0]
        _set_list(kf, D, mime, [did] + [v for v in before if v != did and not v.startswith(OURS)])
        added = _get_list(kf, A, mime)
        _set_list(kf, A, mime, [did] + [v for v in added if v != did and not v.startswith(OURS)])
    for mime in current - set(desired):
        rest = [v for v in _get_list(kf, D, mime) if not v.startswith(OURS)]
        prev = previous.pop(mime, None)
        if prev and prev not in rest:
            rest.insert(0, prev)
        _set_list(kf, D, mime, rest)
        _set_list(kf, A, mime, [v for v in _get_list(kf, A, mime) if not v.startswith(OURS)])
    MIMEAPPS.parent.mkdir(parents=True, exist_ok=True)
    kf.save_to_file(str(MIMEAPPS))


def apply(desig=None):
    """Enregistre les désignations et les applique au bureau : types MIME, lanceurs,
    applications par défaut."""
    desig = designations() if desig is None else {normalize(e): a for e, a in desig.items() if normalize(e)}
    cfg = vm.load_config()
    cfg["open_with"] = desig
    previous = dict(cfg.get("open_with_previous") or {})
    _write_mime_package(desig)
    by_app = {}
    for ext, app in desig.items():
        by_app.setdefault(app, set()).add(mime_for(ext))
    apps = {a for a, _, _, _ in CATALOG} | set(by_app) | desktop.launchers_with_mime()
    for app in apps:
        mimes = sorted(by_app.get(app, ()))
        if mimes:
            _ensure_launcher(app)
        desktop.set_mime_types(app, mimes)
    desired = {mime: desktop.app_desktop_id(app) + ".desktop" for app, mimes in by_app.items() for mime in mimes}
    _associate(desired, previous)
    cfg = vm.load_config()  # relu : la configuration a pu changer pendant update-mime-database
    cfg["open_with"] = desig
    cfg["open_with_previous"] = previous
    vm.save_config(cfg)
    desktop.refresh_desktop_database()
    return desig


def _ensure_launcher(app):
    if (desktop.APPS_DIR / f"{desktop.app_desktop_id(app)}.desktop").exists():
        return
    exe = next((e for n, a, e, _ in desktop.APPS if a == app), None) or desktop.launch_command(app) or app
    desktop.ensure_app(app, app_name(app), exe)


def set_designation(ext, app):
    """Désigne (app) ou libère (None) une extension, avec les extensions du même type."""
    desig = designations()
    for e in siblings(ext):
        if app:
            desig[e] = app
        else:
            desig.pop(e, None)
    return apply(desig)


def app_for(path):
    """Application désignée pour ce fichier, ou None."""
    ext = normalize(Path(path).suffix)
    return designations().get(ext) if ext else None


# -- chemins --

def to_windows(path):
    """Chemin Windows d'un fichier Linux par les dossiers partagés, ou None hors partage."""
    p = Path(path).resolve()
    best = None
    for _, root, drive, _ in vm.shares():
        try:
            rel = p.relative_to(Path(root).resolve())
        except ValueError:
            continue
        if best is None or len(rel.parts) < len(best[1].parts):
            best = (drive, rel)
    if best is None:
        return None
    drive, rel = best
    return drive.rstrip("\\") + "\\" + "\\".join(rel.parts)


def is_temporary(path):
    p = Path(path).resolve()
    return any(p.is_relative_to(d) for d in TEMP_DIRS)


def copy_target(path):
    """Où poser la copie d'un fichier hors partage : Téléchargements s'il est partagé, sinon
    le premier dossier partagé. Nom libre (« rapport (2).docx »)."""
    roots = [(tag, Path(root)) for tag, root, _, _ in vm.shares() if Path(root).is_dir()]
    if not roots:
        return None
    folder = next((r for t, r in roots if t == "Telechargements"), roots[0][1])
    src = Path(path)
    dest = folder / src.name
    n = 2
    while dest.exists():
        dest = folder / f"{src.stem} ({n}){src.suffix}"
        n += 1
    return dest
