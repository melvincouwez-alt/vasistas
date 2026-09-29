"""Machine virtuelle Windows : installation, démarrage, arrêt (QEMU direct, sans libvirt)."""

import concurrent.futures
import io
import json
import os
import secrets
import shutil
import socket
import string
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "host" / "vendor"))

import pycdlib  # noqa: E402
import queue  # noqa: E402

MAIN_DATA = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local/share")) / "vasistas"
# Profil expérimental (VASISTAS_PROFILE=yttrium) : copie du disque, sockets et réglages à
# part, écran accéléré par le pilote Yttrium (Venus) ; les ISO restent communes.
PROFILE = os.environ.get("VASISTAS_PROFILE", "")
DATA = MAIN_DATA.with_name(f"vasistas-{PROFILE}") if PROFILE else MAIN_DATA
DISK = DATA / "disk.qcow2"
VARS = DATA / "OVMF_VARS.fd"
CONFIG = DATA / "config.json"
SHARE = DATA / "share.iso"
WIN_ISO = MAIN_DATA / "win11.iso"
VIRTIO_ISO = MAIN_DATA / "virtio-win.iso"
SERIAL = Path(os.environ.get("VASISTAS_SOCKET") or DATA / "serial.sock")
QMP = DATA / "qmp.sock"
# second moniteur QMP réservé à l'hôte GTK (connexion persistante) : `vasistas vm stop`,
# l'application compagnon ou slim gardent le premier, sans attendre que l'hôte le lâche
QMP_HOST = DATA / "qmp-host.sock"
PIDFILE = DATA / "qemu.pid"
GPU_STATE = DATA / "gpu.json"  # carte graphique prêtée à la VM en cours
LOGFILE = DATA / "qemu.log"



def _qemu_bin(name):
    """QEMU compilé dans ~/.local/opt/qemu-<version> (le plus récent) ou celui du système.
    VASISTAS_QEMU_DIR force un dossier bin, « system » force celui du système."""
    forced = os.environ.get("VASISTAS_QEMU_DIR")
    if forced == "system":
        return name
    if forced:
        return str(Path(forced) / name)
    def version(p):
        return tuple(int(x) if x.isdigit() else 0 for x in p.name.split("-", 1)[1].split("."))
    builds = sorted((p for p in (Path.home() / ".local/opt").glob("qemu-*") if (p / "bin" / name).exists()),
                    key=version)
    return str(builds[-1] / "bin" / name) if builds else name


QEMU = _qemu_bin("qemu-system-x86_64")
QEMU_IMG = _qemu_bin("qemu-img")

OVMF_CODE = Path("/usr/share/OVMF/OVMF_CODE_4M.fd")
OVMF_VARS = Path("/usr/share/OVMF/OVMF_VARS_4M.fd")
INSTALL_DIR = REPO / "install"
AGENT_BUILD = REPO / "guest" / "Vasistas.Agent" / "bin" / "Release" / "net48"

# Dossiers Linux visibles dans Windows (virtiofs) : étiquette, dossier, lecteur Windows.
# Par défaut Documents et Téléchargements, pas tout le dossier personnel. D'autres dossiers
# s'ajoutent dans l'application compagnon (config.json « shares »).
SHARES = [
    ("Documents", Path.home() / "Documents", "Z:"),
    ("Telechargements", Path.home() / "Téléchargements", "Y:"),
]
HOTPLUG_PORTS = 4  # emplacements PCIe libres : un dossier ajouté est branché sans redémarrer
VIRTIOFSD = next((p for p in (Path("/usr/libexec/virtiofsd"), Path("/usr/lib/qemu/virtiofsd")) if p.exists()), None)

DISK_SIZE = "96G"
MEMORY = "6G" if PROFILE else "8G"  # profil expérimental : tourne à côté de la VM principale
CPUS = 8
# Ressources de la VM, choisies dans l'application compagnon (config.json « resources »),
# appliquées au démarrage suivant : cœurs, mémoire
RESOURCES = {
    "battery": (4, "6G"),
    "balanced": (CPUS, MEMORY),
    "performance": (12, "12G"),
}


def resources():
    """(cœurs, mémoire) du réglage choisi ; le profil expérimental garde ses valeurs."""
    if PROFILE:
        return CPUS, MEMORY
    return RESOURCES.get(load_config().get("resources"), RESOURCES["balanced"])
SCREEN = (3440, 1800)  # couvre l'écran du portable (2880x1800) et l'externe (3440x1440)


def save_config(cfg: dict):
    CONFIG.write_text(json.dumps(cfg, indent=2))
    CONFIG.chmod(0o600)


def load_config() -> dict:
    if CONFIG.exists():
        return json.loads(CONFIG.read_text())
    alphabet = string.ascii_letters + string.digits
    cfg = {"user": "vasistas", "password": "".join(secrets.choice(alphabet) for _ in range(20))}
    DATA.mkdir(parents=True, exist_ok=True)
    CONFIG.write_text(json.dumps(cfg, indent=2))
    CONFIG.chmod(0o600)
    return cfg


# -- ISO de partage --

def _files_for_share(install: bool) -> dict:
    """Chemin Joliet -> contenu."""
    files = {"vasistas.tag": b"vasistas\r\n",
             # partages virtiofs à monter par boot.ps1 : étiquette;lecteur
             "shares.txt": "".join(f"{tag};{drive};{label}\r\n" for tag, path, drive, label in shares()
                                   if path.is_dir()).encode("utf-8-sig")}
    for name in ("boot.ps1", "specialize.ps1"):
        # PowerShell 5.1 lit les scripts sans BOM en ANSI
        files[name] = b"\xef\xbb\xbf" + (INSTALL_DIR / name).read_bytes()
    if install:
        files["autounattend.xml"] = unattend_xml().encode()
    extra = INSTALL_DIR / "configure-windows.ps1"
    if extra.exists():
        files["configure-windows.ps1"] = b"\xef\xbb\xbf" + extra.read_bytes()
    if AGENT_BUILD.is_dir():
        for p in sorted(AGENT_BUILD.rglob("*")):
            if p.is_file():
                files["Agent/" + p.relative_to(AGENT_BUILD).as_posix()] = p.read_bytes()
    return files


# Clé d'installation générique de Windows 11 Professionnel (KMS client, publiée par Microsoft) :
# installe sans activer. L'installateur 25H2 refuse la clé générique « retail » VK7JG-….
DEFAULT_KEY = "W269N-WFGWX-YVC9B-4J6C9-T83GX"


def product_key(cfg=None):
    """Clé de l'installation : celle de l'utilisateur, sinon la clé générique de l'édition
    choisie ; None pour une version d'évaluation (pas de clé)."""
    cfg = cfg or load_config()
    try:
        from . import winiso
        return winiso.install_key(cfg.get("windows_version", "win11"), cfg.get("windows_edition", "pro"),
                                  cfg.get("windows_key"))
    except (ImportError, ValueError):
        return cfg.get("windows_key") or DEFAULT_KEY


def unattend_xml():
    """Fichier de réponses de l'installation, complété pour cette machine et cet utilisateur."""
    import re
    from . import regional
    cfg = load_config()
    xml = (INSTALL_DIR / "autounattend.xml").read_text()
    key = product_key(cfg)
    if key is None:
        xml = re.sub(r"\s*<ProductKey>.*?</ProductKey>", "", xml, flags=re.S)
    values = {"@PASSWORD@": cfg["password"], "@PRODUCTKEY@": key or "",
              **regional.unattend_values(cfg.get("windows_language")), **(cfg.get("unattend") or {})}
    for k, value in values.items():
        xml = xml.replace(k, value)
    return xml


def build_share(install: bool):
    iso = pycdlib.PyCdlib()
    iso.new(joliet=3, vol_ident="VASISTAS")
    dirs = {"": ("/", "/")}
    counter = 0
    for path, data in _files_for_share(install).items():
        parts = path.split("/")
        parent = ""
        for part in parts[:-1]:
            key = parent + "/" + part
            if key not in dirs:
                counter += 1
                iso9660 = dirs[parent][0].rstrip("/") + f"/D{counter:05d}"
                joliet = dirs[parent][1].rstrip("/") + "/" + part
                iso.add_directory(iso9660, joliet_path=joliet)
                dirs[key] = (iso9660, joliet)
            parent = key
        counter += 1
        iso9660 = dirs[parent][0].rstrip("/") + f"/F{counter:05d}.;1"
        joliet = dirs[parent][1].rstrip("/") + "/" + parts[-1]
        iso.add_fp(io.BytesIO(data), len(data), iso9660, joliet_path=joliet)
    tmp = SHARE.with_suffix(".tmp")
    iso.write(str(tmp))
    iso.close()
    tmp.replace(SHARE)


# -- QEMU --

def qemu_args(install: bool, card=None) -> list:
    cpus, memory = resources()
    args = [
        QEMU, "-name", f"Vasistas-{PROFILE}" if PROFILE else "Vasistas",
        # hpet=off et PIT sans rattrapage : moins d'interruptions de minuterie dans Windows, qui a
        # l'horloge Hyper-V (hv-passthrough) ; vmport inutile (pas de VMware)
        "-machine", "q35,accel=kvm,hpet=off,vmport=off", "-cpu", "host,hv-passthrough",
        "-global", "kvm-pit.lost_tick_policy=discard",
        "-smp", f"{cpus},sockets=1,cores={cpus}", "-m", memory,
        "-drive", f"if=pflash,format=raw,readonly=on,file={OVMF_CODE}",
        "-drive", f"if=pflash,format=raw,file={VARS}",
        "-rtc", "base=localtime,driftfix=slew",
        # disque servi par un fil d'E/S à part : les accès disque (démarrage d'Office) ne
        # retardent plus la boucle principale de QEMU, qui sert aussi l'écran ; cache des tables
        # qcow2 dimensionné pour tout le disque (96 Go / 64 Ko x 8 o = 12 Mo)
        "-object", "iothread,id=io0",
        "-drive", f"if=none,id=disk,file={DISK},format=qcow2,cache=none,discard=unmap,"
                  "detect-zeroes=unmap,l2-cache-size=12M",
        "-device", "virtio-blk-pci,drive=disk,iothread=io0,bootindex=1",
        "-drive", f"if=none,id=virtio,media=cdrom,readonly=on,file={VIRTIO_ISO}",
        "-device", "ide-cd,drive=virtio,bus=ide.1",
        "-drive", f"if=none,id=share,media=cdrom,readonly=on,file={SHARE}",
        "-device", "ide-cd,drive=share,bus=ide.2",
        "-nic", "user,model=virtio-net-pci",
        "-device", "virtio-serial-pci",
        "-chardev", f"socket,id=vas,path={SERIAL},server=on,wait=off",
        "-device", "virtserialport,chardev=vas,name=org.vasistas.0",
        "-qmp", f"unix:{QMP},server=on,wait=off",
        "-qmp", f"unix:{QMP_HOST},server=on,wait=off",
        *(["-device", "virtio-vga-gl,hostmem=4G,blob=on,venus=on"] if PROFILE == "yttrium"
          else ["-device", f"virtio-vga,xres={SCREEN[0]},yres={SCREEN[1]}"]),
        "-device", "qemu-xhci", "-device", "usb-tablet",
        # ballon piloté par balloon.py (le pilote Windows ignore free-page-reporting, sans effet mais sans gêne)
        "-device", "virtio-balloon-pci,id=balloon0,free-page-reporting=on",
    ]
    if card:
        from . import gpu
        args += gpu.qemu_args(card)
    # emplacements libres pour brancher un dossier partagé à chaud
    for i in range(HOTPLUG_PORTS):
        args += ["-device", f"pcie-root-port,id=hp{i},chassis={20 + i},slot={20 + i}"]
    if not install and VIRTIOFSD:
        # virtiofs exige une mémoire partagée avec virtiofsd
        args += ["-object", f"memory-backend-memfd,id=mem,size={memory},share=on", "-numa", "node,memdev=mem"]
        for i, (tag, path, _, _) in enumerate(shares()):
            if path.is_dir():
                args += ["-chardev", f"socket,id=vfs{i},path={_vfs_socket(tag)}",
                         "-device", f"vhost-user-fs-pci,chardev=vfs{i},tag={tag}"]
    args += [
        "-pidfile", str(PIDFILE),
    ]
    if install:
        args += [
            "-drive", f"if=none,id=wincd,media=cdrom,readonly=on,file={WIN_ISO}",
            "-device", "ide-cd,drive=wincd,bus=ide.0,bootindex=0",
            "-display", "gtk,zoom-to-fit=on",
        ]
    elif PROFILE == "yttrium":
        # la doc d'Yttrium demande un affichage visible (fenêtre QEMU classique)
        args += ["-display", "gtk,gl=on,zoom-to-fit=on"]
    else:
        args += ["-display", "dbus,p2p=yes", "-daemonize"]
    return args


class Qmp:
    def __init__(self, path=QMP, timeout=5):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect(str(path))
        self.file = self.sock.makefile("rwb")
        self._read()  # bannière
        self.cmd("qmp_capabilities")

    def _read(self):
        while True:
            line = self.file.readline()
            if not line:
                raise EOFError("QMP fermé")
            msg = json.loads(line)
            if "event" not in msg:
                return msg

    def cmd(self, name, **arguments):
        return self.execute(name, arguments)

    def execute(self, name, arguments=None):
        """Comme cmd(), pour les commandes qui ont elles-mêmes un argument « name »."""
        req = {"execute": name}
        if arguments:
            req["arguments"] = arguments
        self.file.write(json.dumps(req).encode() + b"\n")
        self.file.flush()
        reply = self._read()
        if "error" in reply:
            raise RuntimeError(reply["error"].get("desc"))
        return reply.get("return")

    def send_fd(self, fdname, fd):
        """getfd : transmet un descripteur à QEMU (SCM_RIGHTS), nommé pour une commande suivante."""
        req = json.dumps({"execute": "getfd", "arguments": {"fdname": fdname}}).encode() + b"\n"
        socket.send_fds(self.sock, [req], [fd])
        reply = self._read()
        if "error" in reply:
            raise RuntimeError(reply["error"].get("desc"))

    def close(self):
        # QEMU ne sert qu'un client QMP à la fois : fermer vraiment (le makefile garde le socket ouvert)
        try:
            self.file.close()
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


class QmpWorker:
    """Un seul fil parle à QEMU pour l'hôte GTK.

    Le fil GTK n'attend jamais QMP (soumission asynchrone), et ballon, veille et affichage ne
    se disputent plus le moniteur : QEMU ne sert qu'un client par socket, le suivant attendait
    jusqu'à 5 s. Connexion persistante sur QMP_HOST quand la VM l'a, sinon une connexion par
    tâche sur QMP (VM démarrée avant ce second moniteur)."""

    def __init__(self):
        self.jobs = queue.Queue()
        self.conn = None
        self.persistent = False
        threading.Thread(target=self._run, name="vasistas-qmp", daemon=True).start()

    def submit(self, fn):
        """fn(qmp) exécutée dans le fil QMP ; renvoie un concurrent.futures.Future."""
        fut = concurrent.futures.Future()
        self.jobs.put((fn, fut))
        return fut

    def call(self, name, timeout=15, **arguments):
        """Commande QMP attendue (hors fil GTK)."""
        return self.submit(lambda q: q.execute(name, arguments)).result(timeout)

    def call_async(self, name, on_error=None, **arguments):
        fut = self.submit(lambda q: q.execute(name, arguments))
        if on_error is not None:
            fut.add_done_callback(lambda f: f.exception() and on_error(f.exception()))
        return fut

    def _drop(self):
        if self.conn is not None:
            self.conn.close()
            self.conn = None

    def _connection(self):
        if self.conn is None:
            path = QMP_HOST if QMP_HOST.exists() else QMP
            self.conn = Qmp(path)
            self.persistent = path == QMP_HOST
        return self.conn

    def _run(self):
        while True:
            fn, fut = self.jobs.get()
            if not fut.set_running_or_notify_cancel():
                continue
            for attempt in (1, 2):
                try:
                    fut.set_result(fn(self._connection()))
                    break
                except (OSError, EOFError) as e:
                    # VM relancée, connexion persistante coupée : une seconde chance
                    self._drop()
                    if attempt == 2:
                        fut.set_exception(e)
                except BaseException as e:
                    fut.set_exception(e)
                    break
            if not self.persistent:
                self._drop()


_worker = None
_worker_lock = threading.Lock()


def qmp_worker() -> QmpWorker:
    global _worker
    with _worker_lock:
        if _worker is None:
            _worker = QmpWorker()
        return _worker


def _press_keys_during_boot(seconds=12):
    """Le CD Windows demande « appuyez sur une touche » : on appuie pour lui.
    Pas plus longtemps : ensuite Espace activerait le bouton Annuler de l'installateur."""
    deadline = time.monotonic() + seconds
    qmp = None
    while time.monotonic() < deadline:
        try:
            if qmp is None:
                qmp = Qmp()
            qmp.cmd("send-key", keys=[{"type": "qcode", "data": "spc"}])
        except (OSError, EOFError, RuntimeError):
            qmp = None
        time.sleep(0.5)
    if qmp:
        qmp.close()


def pid():
    try:
        p = int(PIDFILE.read_text())
        os.kill(p, 0)
        return p
    except (OSError, ValueError):
        return None


def _check_files(install: bool):
    missing = [str(p) for p in (OVMF_CODE, OVMF_VARS, VIRTIO_ISO) if not p.exists()]
    if install and not WIN_ISO.exists():
        missing.append(str(WIN_ISO))
    if not install and not DISK.exists():
        missing.append(f"{DISK} (lancer `vasistas vm install`)")
    if missing:
        raise SystemExit("fichiers manquants :\n  " + "\n  ".join(missing))


def install(force=False):
    _check_files(True)
    if pid():
        raise SystemExit("la VM tourne déjà")
    if DISK.exists() and not force:
        raise SystemExit(f"{DISK} existe déjà (--force pour réinstaller)")
    load_config()
    subprocess.run([QEMU_IMG, "create", "-q", "-f", "qcow2", str(DISK), DISK_SIZE], check=True)
    shutil.copyfile(OVMF_VARS, VARS)
    build_share(install=True)
    for s in (SERIAL, QMP):
        s.unlink(missing_ok=True)
    print("installation de Windows : la fenêtre QEMU se ferme d'elle-même à la fin")
    proc = subprocess.Popen(qemu_args(True))
    threading.Thread(target=_press_keys_during_boot, daemon=True).start()
    rc = proc.wait()
    print(f"QEMU terminé (code {rc})")
    return rc


def share_tag(label):
    """Étiquette virtiofs : lettres et chiffres ASCII (le nom affiché garde ses accents)."""
    import unicodedata
    ascii_ = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode()
    return "".join(c for c in ascii_ if c.isalnum())[:30] or "Dossier"


def shares():
    """Dossiers partagés : [(étiquette, dossier, lecteur, nom affiché)]."""
    cfg = load_config().get("shares")
    if cfg is None:
        return [(tag, path, drive, "Téléchargements" if tag == "Telechargements" else tag)
                for tag, path, drive in SHARES]
    return [(s["tag"], Path(s["path"]), s["drive"], s.get("label") or s["tag"]) for s in cfg]


def save_shares(items):
    """items : [(dossier, lecteur, nom affiché)] ; étiquettes uniques recalculées."""
    cfg = load_config()
    out, used = [], set()
    for path, drive, label in items:
        tag = base = share_tag(label)
        n = 2
        while tag in used:
            tag, n = f"{base}{n}", n + 1
        used.add(tag)
        out.append({"tag": tag, "path": str(path), "drive": drive, "label": label})
    cfg["shares"] = out
    save_config(cfg)


def add_share(path, label=None):
    """Ajoute un dossier partagé (config.json) : (étiquette, lecteur, nom affiché), ceux du
    partage existant si le dossier l'est déjà, None s'il ne reste aucune lettre libre."""
    path = str(path)
    for tag, p, drive, lb in shares():
        if str(p) == path:
            return tag, drive, lb
    drives = free_drives()
    if not drives:
        return None
    label = label or os.path.basename(path.rstrip("/")) or "Dossier"
    save_shares([(p, dr, lb) for _, p, dr, lb in shares()] + [(path, drives[0], label)])
    tag = next(t for t, p, _, _ in shares() if str(p) == path)
    return tag, drives[0], label


def mount_script(tag, drive, label):
    """Script PowerShell qui monte un dossier branché à chaud (WinFsp) ; affiche True si le
    lecteur répond."""
    return (f"Start-Sleep 3; & 'C:\\Program Files (x86)\\WinFsp\\bin\\launchctl-x64.exe' "
            f"start virtiofs vfs{tag} {tag} {drive} | Out-Null; "
            f"New-Item -Force 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\"
            f"MountPoints2\\{drive.rstrip(':')}' | Set-ItemProperty -Name _LabelFromReg "
            f"-Value '{label.replace(chr(39), chr(39) * 2)} (Linux)'; "
            f"foreach ($i in 1..10) {{ if (Test-Path '{drive}\\') {{ break }}; Start-Sleep 1 }}; "
            + HEAL_SCRIPT + f"; Test-Path '{drive}\\'")


# Un dossier branché à chaud fait réinitialiser par Windows les autres périphériques virtio-fs :
# leurs lecteurs restent affichés mais ne répondent plus. Chaque lecteur mort est remonté
# (l'agent le fait aussi toutes les 30 s et avant d'ouvrir un fichier : ShareGuard.cs).
HEAL_SCRIPT = (
    "$lc = 'C:\\Program Files (x86)\\WinFsp\\bin\\launchctl-x64.exe'; "
    "foreach ($n in (& $lc list | ForEach-Object { $p = $_.Trim() -split ' '; "
    "if ($p.Count -eq 2 -and $p[0] -eq 'virtiofs') { $p[1] } })) { "
    "$info = (& $lc info virtiofs $n) -join ' '; "
    "if ($info -match '-t \"([^\"]+)\" -m \"([A-Za-z]:)\"') { $t = $Matches[1]; $d = $Matches[2]; "
    "if (-not (Test-Path \"$d\\\")) { & $lc stop virtiofs $n | Out-Null; Start-Sleep 1; "
    "& $lc start virtiofs $n $t $d | Out-Null; Start-Sleep 2; \"remonté $d\" } } }")


def free_drives():
    used = {d.upper() for _, _, d, _ in shares()}
    return [f"{c}:" for c in "XWVUTSRQPONMLKJIHGFE" if f"{c}:" not in used]


def _vfs_socket(tag):
    return DATA / f"vfs-{tag.lower()}.sock"


def _start_virtiofsd():
    """Un virtiofsd par dossier partagé ; il s'arrête de lui-même avec QEMU."""
    if not VIRTIOFSD:
        return
    for tag, path, _, _ in shares():
        if path.is_dir():
            _start_one_virtiofsd(tag, path)


def _start_one_virtiofsd(tag, path):
    sock = _vfs_socket(tag)
    sock.unlink(missing_ok=True)
    with open(DATA / f"virtiofsd-{tag.lower()}.log", "ab") as log:
        subprocess.Popen([str(VIRTIOFSD), "--shared-dir", str(path), "--socket-path", str(sock),
                          "--sandbox", "none", "--cache", "auto"],
                         stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    for _ in range(50):
        if sock.exists():
            break
        time.sleep(0.1)


def hotplug_share(tag, path):
    """Branche un dossier partagé sur la VM en marche (emplacement PCIe libre).
    Faux si impossible (VM démarrée avant l'ajout des emplacements, tous pris) :
    le dossier arrive alors au prochain démarrage."""
    if not pid() or not VIRTIOFSD:
        return False
    q = Qmp()
    try:
        used = set()
        for dev in q.cmd("qom-list", path="/machine/peripheral") or []:
            used.add(dev["name"])
        if f"vfs-{tag}" in used:
            return True
        # dossiers déjà branchés à chaud : un emplacement chacun, dans l'ordre
        taken = sum(1 for d in used if d.startswith("vfs-"))
        if f"hp{taken}" not in used:
            return False
        port = f"hp{taken}"
        _start_one_virtiofsd(tag, path)
        q.cmd("chardev-add", id=f"vfsc-{tag}",
              backend={"type": "socket", "data": {"addr": {"type": "unix", "data": {"path": str(_vfs_socket(tag))}},
                                                   "server": False}})
        q.cmd("device_add", driver="vhost-user-fs-pci", id=f"vfs-{tag}", chardev=f"vfsc-{tag}", tag=tag, bus=port)
        return True
    except (RuntimeError, OSError, EOFError):
        return False
    finally:
        q.close()


def start(wait_socket=True):
    if pid():
        return
    _check_files(False)
    build_share(install=False)
    for s in (SERIAL, QMP, QMP_HOST):
        s.unlink(missing_ok=True)
    _start_virtiofsd()
    card = None
    if not PROFILE:
        from . import gpu
        release_gpu()  # restée prêtée après un arrêt de Windows par lui-même
        card = gpu.plan(load_config().get("gpu", "off"))
        if card:
            try:
                gpu.bind(card)
                GPU_STATE.write_text(json.dumps(card))
            except (OSError, subprocess.SubprocessError) as e:
                print(f"carte graphique dédiée non prêtée : {e}", file=sys.stderr)
                card = None
    with open(LOGFILE, "ab") as log:
        if PROFILE == "yttrium":
            subprocess.Popen(qemu_args(False), stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                             start_new_session=True)
            for _ in range(50):
                if pid():
                    break
                time.sleep(0.1)
        else:
            try:
                subprocess.run(qemu_args(False, card), check=True, stdout=log, stderr=log)
            except subprocess.CalledProcessError:
                release_gpu()
                raise
    if wait_socket:
        for _ in range(50):
            if SERIAL.exists():
                break
            time.sleep(0.1)


def stop(timeout=60):
    p = pid()
    if not p:
        return
    try:
        q = Qmp()
        q.cmd("system_powerdown")
        q.close()
    except (OSError, EOFError, RuntimeError):
        pass
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and pid():
        time.sleep(0.5)
    if pid():
        os.kill(p, 15)
    # sockets laissés par QEMU : sans eux on sait que la VM est arrêtée
    for sock in (SERIAL, QMP, QMP_HOST):
        sock.unlink(missing_ok=True)
    release_gpu()


def gpu_active():
    return GPU_STATE.exists()


def release_gpu():
    """Rend à Linux la carte graphique prêtée, une fois QEMU arrêté."""
    if not GPU_STATE.exists() or pid():
        return
    from . import gpu
    try:
        gpu.release(json.loads(GPU_STATE.read_text()))
    finally:
        GPU_STATE.unlink(missing_ok=True)


def status() -> str:
    p = pid()
    if not p:
        return "arrêtée" if DISK.exists() else "non installée"
    return f"en marche (pid {p})"
