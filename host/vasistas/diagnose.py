"""Diagnostic : vérifications de l'ordinateur et de Vasistas, réparations simples, rapport.

Chaque vérification renvoie un Check : état (« ok », « warn » ou « error »), titre, explication
simple, et parfois une réparation (`fix`, clé de FIXES) ou une commande à copier (`command`,
celles qui demandent sudo). Le rapport texte (vasistas diagnose --report, bouton « Copier le
rapport ») ne contient aucune donnée personnelle : voir anonymize().
"""

import os
import platform
import re
import shutil
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path

from . import vm
from .i18n import N_, _
from .version import VERSION

OK, WARN, ERROR = "ok", "warn", "error"
MIN_FREE_GB = 5
LOW_FREE_GB = 20
LOG_LINES = 200
INSTALL_SH = vm.REPO / "install.sh"
DEFAULT_PACKAGES = ["python3-gi", "python3-gi-cairo", "gir1.2-gtk-4.0", "gir1.2-granite-7.0",
                    "qemu-system-x86", "qemu-utils", "ovmf", "virtiofsd"]


@dataclass
class Check:
    key: str
    title: str
    state: str
    detail: str = ""
    fix: str = None       # réparation possible (FIXES)
    command: str = None   # commande à copier (droits administrateur)


# -- vérifications --

def required_packages():
    """Paquets du système requis : la liste de install.sh (une seule source)."""
    try:
        m = re.search(r"^for pkg in (.+); do", INSTALL_SH.read_text(), re.M)
        if m:
            return m.group(1).split()
    except OSError:
        pass
    return list(DEFAULT_PACKAGES)


def check_kvm():
    if os.access("/dev/kvm", os.R_OK | os.W_OK):
        return Check("kvm", _("Virtualisation matérielle (KVM)"), OK, _("Accessible."))
    if not os.path.exists("/dev/kvm"):
        return Check("kvm", _("Virtualisation matérielle (KVM)"), ERROR,
                     _("Absente : activez la virtualisation (SVM ou VT-x) dans le BIOS de l'ordinateur."))
    return Check("kvm", _("Virtualisation matérielle (KVM)"), ERROR,
                 _("Votre compte utilisateur n'a pas accès à KVM. Lancez la commande ci-dessous, puis fermez et "
                   "rouvrez la session."),
                 command="sudo usermod -aG kvm $USER")


def check_packages():
    missing = []
    for pkg in required_packages():
        try:
            res = subprocess.run(["dpkg", "-s", pkg], capture_output=True, timeout=10)
            if res.returncode != 0:
                missing.append(pkg)
        except (OSError, subprocess.SubprocessError):
            return Check("packages", _("Paquets du système"), WARN, _("Impossible de vérifier les paquets installés "
                                                                       "(dpkg)."))
    if not missing:
        return Check("packages", _("Paquets du système"), OK, _("Tous les paquets requis sont installés."))
    return Check("packages", _("Paquets du système"), ERROR,
                 _("Paquets manquants : {list}. Installez-les avec la commande ci-dessous.", list=", ".join(missing)),
                 command="sudo apt install " + " ".join(missing))


def check_disk():
    vm.ensure_data()
    free = shutil.disk_usage(vm.DATA).free / 1e9
    title = _("Espace disque")
    if free < MIN_FREE_GB:
        return Check("disk", title, ERROR,
                     _("Seulement {n} Go libres : Windows risque de s'arrêter brutalement. Libérez de l'espace disque "
                       "ou supprimez des points de restauration.", n=f"{free:.0f}"), fix="restore-page")
    if free < LOW_FREE_GB:
        return Check("disk", title, WARN,
                     _("{n} Go libres : l'espace disque risque d'être insuffisant pour les mises à jour de "
                       "Windows.", n=f"{free:.0f}"),
                     fix="restore-page")
    return Check("disk", title, OK, _("{n} Go libres.", n=f"{free:.0f}"))


def _meminfo():
    out = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            k, _sep, v = line.partition(":")
            out[k] = int(v.split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        pass
    return out


def _gib(text):
    """« 8G » -> octets."""
    n = float(text.rstrip("GgMm"))
    return int(n * (1 << 30) if text[-1] in "Gg" else n * (1 << 20))


def check_ram():
    title = _("Mémoire")
    cpus, memory = vm.resources()
    if vm.pid():
        return Check("ram", title, OK, _("Windows est en marche avec {mem} de mémoire.", mem=memory.replace("G", " Go")))
    avail = _meminfo().get("MemAvailable")
    if avail is None:
        return Check("ram", title, WARN, _("Impossible de lire la mémoire disponible."))
    need = _gib(memory)
    if avail < need * 0.75:
        return Check("ram", title, WARN,
                     _("{free} Go disponibles pour {need} Go demandés par Windows : fermez des applications ou "
                       "choisissez le mode Optimisé dans la page Affichage.",
                       free=f"{avail / (1 << 30):.0f}", need=f"{need / (1 << 30):.0f}"), fix="performance-page")
    return Check("ram", title, OK, _("{free} Go disponibles, Windows en demande {need}.",
                                     free=f"{avail / (1 << 30):.0f}", need=f"{need / (1 << 30):.0f}"))


def _host_status():
    from . import control
    if not control.SOCKET.exists():
        return None
    try:
        return control.request({"status": True}, timeout=1)
    except OSError:
        return None


def check_qemu(host=None):
    title = _("Machine virtuelle (QEMU)")
    if not vm.DISK.exists():
        return Check("qemu", title, WARN, _("Windows n'est pas installé : ouvrez l'assistant d'installation."),
                     fix="wizard")
    if vm.pid():
        return Check("qemu", title, OK, _("La machine virtuelle est en marche."))
    return Check("qemu", title, OK, _("La machine virtuelle est arrêtée. Elle démarre automatiquement à l'ouverture "
                                      "d'une application Windows."))


def check_agent(host=None):
    title = _("Agent de Windows")
    if not vm.pid():
        return Check("agent", title, OK, _("Windows est arrêté."))
    if not vm.SERIAL.exists():
        return Check("agent", title, ERROR, _("Le canal de communication avec Windows est "
                                              "absent : redémarrez Windows."),
                     fix="restart-vm")
    if host is None:
        return Check("agent", title, WARN, _("Vasistas n'affiche pas les fenêtres de Windows en ce moment."),
                     fix="start-host")
    if not host.get("guest_ready"):
        return Check("agent", title, WARN, _("Windows est en cours de démarrage ou ne répond pas. Si Windows ne répond "
                                             "toujours pas dans quelques minutes, redémarrez "
                                             "Windows."), fix="restart-vm")
    return Check("agent", title, OK, _("L'agent de Windows répond."))


def check_agent_version(host=None):
    """Version de l'agent, si l'hôte la connaît (champ agent_version de la réponse status)."""
    title = _("Version de l'agent")
    agent = (host or {}).get("agent_version")
    if not agent:
        return Check("agent-version", title, OK, _("Version non communiquée par Windows."))
    from . import version
    if version.newer(VERSION, agent):
        return Check("agent-version", title, WARN,
                     _("L'agent de Windows ({agent}) est plus ancien que Vasistas ({host}).", agent=agent, host=VERSION),
                     fix="update-agent")
    return Check("agent-version", title, OK, _("Version {agent}, identique à celle de Vasistas.", agent=agent))


def _virtiofsd_sockets():
    """Sockets servis par un virtiofsd en marche."""
    socks = set()
    for p in Path("/proc").glob("[0-9]*/cmdline"):
        try:
            args = p.read_bytes().split(b"\0")
        except OSError:
            continue
        if args and args[0].endswith(b"virtiofsd") and b"--socket-path" in args:
            i = args.index(b"--socket-path")
            if i + 1 < len(args):
                socks.add(os.fsdecode(args[i + 1]))
    return socks


def check_shares():
    title = _("Lecteurs partagés")
    if not vm.VIRTIOFSD:
        return Check("shares", title, ERROR, _("virtiofsd n'est pas installé : Windows ne voit pas vos dossiers."),
                     command="sudo apt install virtiofsd")
    shares = vm.shares()
    missing = [drive for tag, path, drive, label in shares if not path.is_dir()]
    if missing:
        return Check("shares", title, WARN,
                     _("Dossier introuvable pour {drives} : le dossier a peut-être été déplacé ou supprimé. "
                       "Choisissez-le de nouveau dans la page Fichiers.", drives=", ".join(missing)),
                     fix="folders-page")
    if vm.pid():
        served = _virtiofsd_sockets()
        dead = [drive for tag, path, drive, label in shares if str(vm._vfs_socket(tag)) not in served]
        if dead:
            return Check("shares", title, WARN,
                         _("Partage arrêté pour {drives} : redémarrez Windows pour le rétablir.",
                           drives=", ".join(dead)), fix="restart-vm")
    return Check("shares", title, OK, _("{n} dossier(s) partagé(s).", n=len(shares)))


def check_sound():
    title = _("Son")
    if vm.load_config().get("sound", True) is False:
        return Check("sound", title, OK, _("Désactivé dans Vasistas."))
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    if os.path.exists(os.path.join(runtime, "pipewire-0")):
        return Check("sound", title, OK, _("PipeWire répond."))
    return Check("sound", title, WARN, _("PipeWire est introuvable : le son de Windows ne sera pas disponible."))


def _exec_ok(line):
    """Vrai si la commande d'un lanceur (Exec=) mène bien à Vasistas."""
    try:
        import shlex
        args = shlex.split(line)
    except ValueError:
        return False
    if not args:
        return False
    if args[0] == "env":
        args = args[1:]
        for a in list(args):
            if "=" in a and not a.startswith("/"):
                key, _sep, value = a.partition("=")
                if key == "PYTHONPATH" and not (Path(value) / "vasistas").is_dir():
                    return False
                args.pop(0)
            else:
                break
    prog = args[0] if args else ""
    return bool(prog) and (os.access(prog, os.X_OK) if "/" in prog else shutil.which(prog) is not None)


def broken_launchers():
    """Lanceurs Vasistas dont la commande ne mène plus nulle part (copie déplacée, ancienne version)."""
    from . import desktop
    from .winctl import APP_ID
    bad = []
    for path in sorted(desktop.APPS_DIR.glob("*.desktop")):
        if not path.name.lower().startswith(APP_ID.lower()):
            continue
        try:
            text = path.read_text()
        except OSError:
            continue
        m = re.search(r"^Exec=(.*)$", text, re.M)
        if not m or not _exec_ok(m.group(1)):
            bad.append(path)
    return bad


def check_launchers():
    title = _("Lanceurs du menu")
    try:
        bad = broken_launchers()
    except ImportError:
        return Check("launchers", title, WARN, _("Vérification impossible."))
    if bad:
        return Check("launchers", title, WARN,
                     _("{n} lanceur(s) ne pointent plus vers Vasistas (copie de Vasistas déplacée ou ancienne "
                       "version).", n=len(bad)), fix="launchers")
    return Check("launchers", title, OK, _("Tous les lanceurs sont valides."))


def wrong_associations():
    """Extensions désignées dont l'application par défaut n'est plus le lanceur Vasistas."""
    from gi.repository import GLib
    from . import desktop, files
    kf = files._keyfile()
    wrong = []
    for ext, app in files.designations().items():
        want = desktop.app_desktop_id(app) + ".desktop"
        try:
            first = kf.get_string("Default Applications", files.mime_for(ext)).split(";")[0]
        except GLib.Error:
            first = ""
        if first != want or not (desktop.APPS_DIR / want).exists():
            wrong.append(ext)
    return wrong


def check_files():
    title = _("Ouverture des fichiers")
    if "open_with" not in vm.load_config():
        return Check("files", title, OK, _("Pas encore configurée : réglez-la à la fin de l'assistant ou dans la page "
                                           "Fichiers."))
    try:
        wrong = wrong_associations()
    except Exception:  # noqa: BLE001 - vérification seulement
        return Check("files", title, WARN, _("Vérification impossible."))
    if wrong:
        return Check("files", title, WARN,
                     _("Ces types de fichiers ne s'ouvrent plus dans Windows : {list}.",
                       list=" ".join("." + e for e in sorted(wrong)[:8]) + ("…" if len(wrong) > 8 else "")),
                     fix="files")
    return Check("files", title, OK, _("Les types de fichiers choisis s'ouvrent dans Windows."))


def _autostart_dir():
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "autostart"


def check_indicator():
    """Indicateur du panneau et démarrage automatique (fichiers de ~/.config/autostart)."""
    from .winctl import APP_ID
    title = _("Indicateur et démarrage automatique")
    auto = _autostart_dir()
    from . import indicator
    # indicateur coupé exprès dans le compagnon : rien à signaler
    indicator_file = Path(indicator.AUTOSTART) if indicator.enabled() else None
    boot = (auto / f"{APP_ID}.autostart.desktop").exists()
    parts = [_("Windows démarre automatiquement à l'ouverture de la session.") if boot
             else _("Windows démarre automatiquement à l'ouverture de la première application Windows.")]
    if indicator_file is None:
        return Check("indicator", title, OK, " ".join(parts))
    if not indicator_file.exists():
        parts.append(_("L'indicateur du panneau ne démarre pas automatiquement avec la session."))
        return Check("indicator", title, WARN, " ".join(parts), fix="indicator")
    parts.append(_("L'indicateur du panneau démarre automatiquement avec la session."))
    return Check("indicator", title, OK, " ".join(parts))


def check_updates(network=True):
    from . import updates
    title = _("Mises à jour de Vasistas")
    try:
        if not network:
            raise updates.UpdateError("hors ligne")
        status, rel = updates.check()
    except updates.UpdateError:
        return Check("updates", title, OK, _("Vérification des mises à jour impossible pour le moment."))
    if status == "available" and rel:
        return Check("updates", title, WARN, _("Vasistas {v} est disponible.", v=rel["version"]), fix="updates")
    return Check("updates", title, OK, _("Vasistas {v} est à jour.", v=VERSION))


def checks(network=True):
    """Vérifications dans l'ordre d'affichage : [(clé, fonction sans argument)]."""
    host = {}

    def h():
        if "v" not in host:
            host["v"] = _host_status()
        return host["v"]
    return [
        ("kvm", check_kvm), ("packages", check_packages), ("disk", check_disk), ("ram", check_ram),
        ("qemu", lambda: check_qemu(h())), ("agent", lambda: check_agent(h())),
        ("agent-version", lambda: check_agent_version(h())), ("shares", check_shares), ("sound", check_sound),
        ("launchers", check_launchers), ("files", check_files), ("indicator", check_indicator),
        ("updates", lambda: check_updates(network)),
    ]


def run_all(network=True, on_result=None):
    """Lance toutes les vérifications ; on_result(check) après chacune."""
    out = []
    for key, fn in checks(network):
        try:
            c = fn()
        except Exception as e:  # noqa: BLE001 - une vérification cassée ne doit pas arrêter les autres
            c = Check(key, key, WARN, _("Vérification impossible : {e}", e=e))
        out.append(c)
        if on_result:
            on_result(c)
    return out


# -- réparations --

def fix_launchers():
    from . import desktop
    launcher = desktop._launcher()
    for path in broken_launchers():
        text = path.read_text()
        # garde la sous-commande (run, companion, launch-app <appli> %F…), remplace le programme
        text = re.sub(r"^Exec=.*?((?: (?:run|companion|boot|launch-app|open)\b).*)$",
                      lambda m: f"Exec={launcher}{m.group(1)}", text, flags=re.M)
        path.write_text(text)
    desktop.refresh_desktop_database()


def fix_files():
    from . import files
    files.apply()


def fix_restart_vm():
    vm.stop()
    vm.start()


FIXES = {
    # clé : (texte du bouton, fonction ; None = géré par la page du compagnon)
    "launchers": (N_("Réparer"), fix_launchers),
    "files": (N_("Réparer"), fix_files),
    "restart-vm": (N_("Redémarrer Windows"), fix_restart_vm),
    "start-host": (N_("Réparer"), None),
    "update-agent": (N_("Mettre à jour"), None),
    "restore-page": (N_("Points de restauration"), None),
    "performance-page": (N_("Performances"), None),
    "folders-page": (N_("Fichiers"), None),
    "wizard": (N_("Ouvrir l'assistant"), None),
    "indicator": (N_("Réparer"), None),
    "updates": (N_("Voir"), None),
}


def fix_label(key):
    return _(FIXES.get(key, ("Réparer", None))[0])


# -- rapport --

def _first_line(cmd):
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.splitlines()[0].strip()
    except (OSError, subprocess.SubprocessError, IndexError):
        return "?"


def os_name():
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip('"')
    except OSError:
        pass
    return "?"


def log_tail(path=None, n=LOG_LINES):
    """Dernières lignes utiles du journal de l'hôte (sans lignes vides ni DEBUG)."""
    path = path or vm.DATA / "host.log"
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 512 * 1024))
            lines = f.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    useful = [ln for ln in lines if ln.strip() and " DEBUG " not in ln]
    return useful[-n:]


def personal_values():
    """Ce qui ne doit pas sortir de l'ordinateur : dossier personnel, nom d'utilisateur, machine,
    mot de passe et clé de Windows, dossiers partagés."""
    cfg = {}
    try:
        cfg = vm.load_config() if vm.CONFIG.exists() else {}
    except (OSError, ValueError):
        pass
    try:
        import pwd
        user = pwd.getpwuid(os.getuid()).pw_name
    except (ImportError, KeyError):
        user = os.environ.get("USER", "")
    shares = []
    try:
        shares = vm.shares()
    except (OSError, ValueError, KeyError):
        pass
    windows_user = cfg.get("user") or ""
    return {"home": str(Path.home()), "user": user, "host": socket.gethostname(),
            "secrets": [v for v in (cfg.get("password"), cfg.get("windows_key")) if v],
            # « vasistas », le compte créé par défaut dans Windows, n'est pas personnel
            "windows_user": "" if windows_user == "vasistas" else windows_user,
            "shares": [(str(path), label) for _t, path, _d, label in shares],
            "drives": [drive for _t, _p, drive, _l in shares]}


_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
# fin d'un chemin dans une ligne du journal : les noms de fichiers ont souvent des espaces, on va
# donc jusqu'à un guillemet, une flèche ou la fin de la ligne (mieux vaut trop masquer que pas assez)
_PATH_END = r"""[^'"«»\n]*?(?=\s+->|\s+→|['"«»]|$)"""
# noms des dossiers partagés par défaut : pas personnels, gardés lisibles
_GENERIC_LABELS = {"Documents", "Téléchargements", "Telechargements", "Downloads", "Bureau", "Desktop", "Images",
                   "Pictures", "Musique", "Music", "Vidéos", "Videos", "Dossier"}


def anonymize(text, home, user, host="", secrets=(), shares=(), drives=(), windows_user=""):
    """Remplace dans `text` tout ce qui désigne la personne : mots de passe, chemins et noms de
    fichiers des dossiers partagés, dossier personnel, nom d'utilisateur, machine, adresses."""
    for s in secrets:
        if s:
            text = text.replace(s, "<secret>")
    # chemins des dossiers partagés : le dossier et tout fichier dessous
    for i, (path, label) in enumerate(sorted(shares, key=lambda s: -len(s[0])), 1):
        for p in {path, path.replace(home, "~", 1) if home and path.startswith(home) else path}:
            text = re.sub(re.escape(p) + r"(?:/" + _PATH_END + ")?", f"<partage {i}>", text, flags=re.M)
        if label and len(label) >= 3 and label not in _GENERIC_LABELS:
            text = re.sub(r"(?<!\w)" + re.escape(label) + r"(?!\w)", f"<partage {i}>", text)
    # fichiers ouverts depuis un lecteur partagé de Windows (Z:\Rapport.docx)
    for d in drives:
        text = re.sub(re.escape(d) + r"\\" + _PATH_END, f"{d}\\\\<fichier>", text, flags=re.I | re.M)
    text = re.sub(r"(?i)(C:\\Users\\)[^\\\s'\"]+", r"\1<utilisateur>", text)
    if home and home != "/":
        text = text.replace(home, "~")
    # fichiers du dossier personnel (les dossiers cachés de configuration restent lisibles)
    text = re.sub(r"~/(?!\.)" + _PATH_END, "~/<chemin>", text, flags=re.M)
    text = _EMAIL.sub("<courriel>", text)
    for word, repl in ((user, "<utilisateur>"), (windows_user, "<utilisateur>"), (host, "<machine>")):
        if word and len(word) >= 3:
            text = re.sub(r"(?<![\w.-])" + re.escape(word) + r"(?![\w-])", repl, text)
    return text


def report(results, log_lines=None):
    """Rapport texte, anonymisé."""
    qemu = _first_line([vm.QEMU, "--version"])
    marks = {OK: _("[ok]"), WARN: _("[attention]"), ERROR: _("[problème]")}
    lines = [
        _("Vasistas : rapport de diagnostic"),
        f"Vasistas {VERSION}",
        _("QEMU : {v}", v=qemu),
        _("Noyau : {v}", v=platform.release()),
        _("Système : {v}", v=os_name()),
        "",
    ]
    for c in results:
        lines.append(_("{mark} {title} : {detail}", mark=marks.get(c.state, c.state), title=c.title, detail=c.detail))
    lines += ["", _("Journal de l'hôte ({n} dernières lignes utiles) :", n=LOG_LINES)]
    lines += log_tail() if log_lines is None else log_lines
    v = personal_values()
    return anonymize("\n".join(lines), v["home"], v["user"], v["host"], v["secrets"], v["shares"], v["drives"],
                     v["windows_user"]) + "\n"
