"""Points de restauration : instantanés internes du disque de Windows (qcow2).

Chaque point est un instantané interne de disk.qcow2 nommé « vas-a-AAAAMMJJ-HHMMSS » (automatique)
ou « vas-m-… » (manuel) ; restore.json garde, par nom, le motif et le nom choisi pour l'affichage.
Les autres instantanés du disque (celui de l'allègement, slim.SNAPSHOT, ou ceux faits à la main)
ne sont ni listés ni supprimés ici.

VM arrêtée : qemu-img snapshot -c/-l/-a/-d. VM en marche : l'agent vide d'abord le cache disque de
Windows (Write-VolumeCache), puis la VM est mise en pause le temps de l'instantané (QMP
blockdev-snapshot-internal-sync, une fraction de seconde) et repart. Revenir à ce point redémarre
Windows comme après une coupure de courant survenue juste après l'enregistrement des fichiers.
snapshot-save (QEMU 6+, état complet de la VM) n'est pas utilisé : il écrit toute la mémoire de
Windows dans le disque (jusqu'à 12 Go par point) et ne se recharge qu'avec exactement les mêmes
périphériques, alors que Vasistas branche des dossiers à chaud et prête parfois la carte graphique.
Sans agent qui répond, pas de point à chaud : NeedsStop, il faut arrêter Windows d'abord.

Revenir à un point n'est possible que VM arrêtée.
"""

import json
import re
import shutil
import struct
import subprocess
import time

from . import vm
from .i18n import N_, _, current

PREFIX = "vas-"
META = vm.DATA / "restore.json"
MIN_FREE_GB = 5  # en dessous, pas de nouveau point : le disque de Windows grossit avec eux
KEEP_DEFAULT = 3
REASONS = {
    "windows-update": N_("Avant mise à jour de Windows"),
    "install": N_("Avant installation de {app}"),
    "install-office": N_("Avant installation d'Office"),
    "manual": N_("Point manuel"),
}
MONTHS = {
    "fr": ("janv.", "févr.", "mars", "avr.", "mai", "juin", "juil.", "août", "sept.", "oct.", "nov.", "déc."),
    "en": ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"),
}


class RestoreError(Exception):
    pass


class NeedsStop(RestoreError):
    """Windows tourne et l'agent ne répond pas : arrêter Windows avant de créer le point."""


# -- noms et textes (fonctions pures, testées) --

def short_date(ts, lang=None):
    """« 4 oct. 14:32 » (ou « Oct 4 14:32 ») ; l'année seulement si ce n'est pas celle en cours."""
    lang = lang or current()
    t = time.localtime(ts)
    month = MONTHS.get(lang, MONTHS["en"])[t.tm_mon - 1]
    year = "" if t.tm_year == time.localtime().tm_year else f" {t.tm_year}"
    hm = f"{t.tm_hour:02d}:{t.tm_min:02d}"
    if lang == "fr":
        return f"{t.tm_mday} {month}{year} {hm}"
    return f"{month} {t.tm_mday}{',' + year if year else ''} {hm}"


def auto_label(reason, created, app=None, name=None, lang=None):
    """Nom affiché : « Avant mise à jour de Windows · 4 oct. 14:32 », ou le nom choisi."""
    if name:
        return name
    text = _(REASONS.get(reason, REASONS["manual"]), app=app or "?")
    return f"{text} · {short_date(created, lang)}"


def new_tag(auto, created, taken=()):
    """Nom de l'instantané dans le disque : sans espace, unique."""
    base = f"{PREFIX}{'a' if auto else 'm'}-{time.strftime('%Y%m%d-%H%M%S', time.localtime(created))}"
    tag, n = base, 2
    while tag in taken:
        tag, n = f"{base}-{n}", n + 1
    return tag


_SIZE_UNITS = {"B": 1, "KiB": 1 << 10, "MiB": 1 << 20, "GiB": 1 << 30, "TiB": 1 << 40}
_LINE = re.compile(r"^(\S+)\s+(\S+)\s+([\d.]+)\s*(B|KiB|MiB|GiB|TiB)\s+(\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\s")


def parse_list(text):
    """Sortie de « qemu-img snapshot -l » : [{id, tag, vm_size, created}] (created en secondes)."""
    out = []
    for line in text.splitlines():
        m = _LINE.match(line)
        if not m:
            continue
        sid, tag, size, unit, date = m.groups()
        try:
            created = time.mktime(time.strptime(date, "%Y-%m-%d %H:%M:%S"))
        except ValueError:
            continue
        out.append({"id": sid, "tag": tag, "vm_size": int(float(size) * _SIZE_UNITS[unit]),
                    "created": created})
    return out


def to_rotate(points, keep):
    """Points automatiques en trop (les plus anciens), à supprimer ; jamais un point manuel."""
    autos = sorted((p for p in points if p.get("auto")), key=lambda p: p["created"])
    keep = max(1, int(keep))
    return [p["tag"] for p in autos[:-keep]] if len(autos) > keep else []


def fmt_size(n):
    if n is None:
        return ""
    if n >= 1 << 30:
        return _("{n} Go", n=f"{n / (1 << 30):.1f}".replace(".", "," if current() == "fr" else "."))
    if n >= 1 << 20:
        return _("{n} Mo", n=round(n / (1 << 20)))
    return _("moins de 1 Mo")


# -- disque --

def _img(*args, check=True):
    res = subprocess.run([vm.QEMU_IMG, *args], capture_output=True, text=True)
    if check and res.returncode != 0:
        raise RestoreError((res.stderr or res.stdout).strip() or f"qemu-img {args[0]} : code {res.returncode}")
    return res.stdout


def _meta():
    try:
        return json.loads(META.read_text())
    except (OSError, ValueError):
        return {}


def _save_meta(meta):
    META.parent.mkdir(parents=True, exist_ok=True)
    tmp = META.with_suffix(".tmp")
    tmp.write_text(json.dumps(meta, ensure_ascii=False, indent=1))
    tmp.replace(META)


def snapshots():
    """Tous les instantanés du disque (y compris ceux qui ne sont pas des points)."""
    if not vm.DISK.exists():
        return []
    # -U : lisible même VM en marche (le disque est verrouillé par QEMU)
    return parse_list(_img("snapshot", "-l", "-U", str(vm.DISK)))


def points(with_sizes=False):
    """Points de restauration, du plus récent au plus ancien :
    [{tag, label, auto, reason, created, size}]."""
    meta = _meta()
    out = []
    for s in snapshots():
        tag = s["tag"]
        if not tag.startswith(PREFIX):
            continue
        m = meta.get(tag, {})
        auto = m.get("auto", tag.startswith(PREFIX + "a-"))
        created = m.get("created", s["created"])
        out.append({"tag": tag, "auto": auto, "reason": m.get("reason", "manual"), "app": m.get("app"),
                    "name": m.get("name"), "created": created, "size": None,
                    "label": auto_label(m.get("reason", "manual"), created, m.get("app"), m.get("name"))})
    out.sort(key=lambda p: p["created"], reverse=True)
    if with_sizes and out:
        try:
            sizes = exclusive_sizes(vm.DISK)
            for p in out:
                p["size"] = sizes.get(p["tag"])
        except (OSError, ValueError, struct.error):
            pass
    return out


def find(name):
    """Point désigné par son nom d'instantané ou son nom affiché (exact)."""
    for p in points():
        if name in (p["tag"], p["label"], p.get("name")):
            return p
    raise RestoreError(_("Point de restauration introuvable : {name}", name=name))


def free_bytes():
    vm.ensure_data()
    return shutil.disk_usage(vm.DATA).free


def enough_space():
    return free_bytes() >= MIN_FREE_GB * 10**9


def settings():
    cfg = vm.load_config()
    keep = cfg.get("restore_keep", KEEP_DEFAULT)
    # points automatiques : seulement si l'utilisateur les a demandés (onglet Restauration)
    return {"auto": cfg.get("restore_auto") is True,
            "keep": keep if isinstance(keep, int) and keep > 0 else KEEP_DEFAULT}


def _qmp(command, **args):
    # supprimer un gros instantané VM en marche prend des minutes : pas le délai de 5 s par défaut
    q = vm.Qmp(timeout=600)
    try:
        return q.execute(command, args)
    finally:
        q.close()


def _flush_guest():
    """Demande à Windows d'écrire sur le disque ce qu'il garde en mémoire. Vrai si c'est fait."""
    from . import control
    if not control.SOCKET.exists():
        return False
    try:
        res = control.request({"exec": "Write-VolumeCache -DriveLetter C; 'vide'"}, timeout=60)
    except OSError:
        return False
    return res.get("code") == 0 and "vide" in (res.get("out") or "")


def _snapshot_running(tag):
    if not _flush_guest():
        raise NeedsStop(_("Windows ne répond pas : arrêtez Windows, puis créez le point de restauration."))
    q = vm.Qmp()
    try:
        running = (q.cmd("query-status") or {}).get("running", False)
        if running:
            q.cmd("stop")
        try:
            q.execute("blockdev-snapshot-internal-sync", {"device": "disk", "name": tag})
        finally:
            if running:
                q.cmd("cont")
    except (OSError, EOFError, RuntimeError) as e:
        raise RestoreError(str(e)) from None
    finally:
        q.close()


def create(name=None, reason="manual", app=None, auto=False):
    """Crée un point ; renvoie sa description (voir points())."""
    if not vm.DISK.exists():
        raise RestoreError(_("Windows n'est pas installé."))
    if not enough_space():
        raise RestoreError(_("Moins de {n} Go libres : libérez de la place avant de créer un point de "
                             "restauration.", n=MIN_FREE_GB))
    if name and any(name in (p["label"], p["tag"]) for p in points()):
        raise RestoreError(_("Un point porte déjà ce nom : {name}", name=name))
    created = time.time()
    tag = new_tag(auto, created, {s["tag"] for s in snapshots()})
    if vm.pid():
        _snapshot_running(tag)
    else:
        _img("snapshot", "-c", tag, str(vm.DISK))
    meta = _meta()
    meta[tag] = {"auto": auto, "reason": reason, "created": created,
                 **({"app": app} if app else {}), **({"name": name} if name else {})}
    _save_meta(meta)
    return next((p for p in points() if p["tag"] == tag),
                {"tag": tag, "label": auto_label(reason, created, app, name), "auto": auto, "created": created})


def delete(name):
    p = find(name)
    tag = p["tag"]
    if vm.pid():
        try:
            _qmp("blockdev-snapshot-delete-internal-sync", device="disk", name=tag)
        except (OSError, EOFError, RuntimeError) as e:
            raise RestoreError(str(e)) from None
    else:
        _img("snapshot", "-d", tag, str(vm.DISK))
    meta = _meta()
    meta.pop(tag, None)
    _save_meta(meta)
    return p


def revert(name):
    """Remet tout le disque de Windows dans l'état du point. VM arrêtée obligatoire."""
    p = find(name)
    if vm.pid():
        raise RestoreError(_("Arrêtez Windows d'abord."))
    _img("snapshot", "-a", p["tag"], str(vm.DISK))
    # ce que Vasistas avait lu dans Windows ne vaut plus (allègement, applications installées)
    try:
        from . import slim
        c = slim.cached()
        if c.pop("status", None) is not None:
            slim.CACHE.write_text(json.dumps(c, ensure_ascii=False, indent=1))
    except (ImportError, OSError):
        pass
    return p


def rotate(keep=None):
    """Supprime les points automatiques en trop ; renvoie leurs noms."""
    keep = settings()["keep"] if keep is None else keep
    gone = []
    for tag in to_rotate(points(), keep):
        try:
            delete(tag)
            gone.append(tag)
        except RestoreError:
            pass
    return gone


def auto_point(reason, app=None):
    """Point automatique avant une action risquée, si l'option est active.
    Renvoie (point ou None, message d'erreur ou None) : l'action continue même sans point."""
    if not settings()["auto"]:
        return None, None
    try:
        p = create(reason=reason, app=app, auto=True)
    except RestoreError as e:
        return None, str(e)
    rotate()
    return p, None


# -- place prise par chaque point --

def exclusive_sizes(path):
    """Place libérée en supprimant chaque instantané : {nom: octets}.

    Lecture directe du qcow2 : un groupe (cluster) de données ou une table L2 n'appartient
    qu'à un instantané quand son compteur de références vaut 1 et que la table de cet
    instantané le désigne. Estimation (le disque peut changer pendant la lecture si Windows
    tourne), suffisante pour l'affichage."""
    with open(path, "rb") as f:
        hdr = f.read(104)
        if hdr[:4] != b"QFI\xfb":
            raise ValueError("pas un qcow2")
        version = struct.unpack(">I", hdr[4:8])[0]
        cluster_bits = struct.unpack(">I", hdr[20:24])[0]
        rt_offset = struct.unpack(">Q", hdr[48:56])[0]
        rt_clusters = struct.unpack(">I", hdr[56:60])[0]
        nb_snapshots = struct.unpack(">I", hdr[60:64])[0]
        snap_offset = struct.unpack(">Q", hdr[64:72])[0]
        refcount_order = struct.unpack(">I", hdr[96:100])[0] if version >= 3 else 4
        incompatible = struct.unpack(">Q", hdr[72:80])[0] if version >= 3 else 0
        cs = 1 << cluster_bits
        l2_entry = 16 if incompatible & (1 << 4) else 8  # sous-groupes étendus : 16 octets
        off_mask = 0x00FFFFFFFFFFFE00

        # compteurs de références
        bits = 1 << refcount_order
        per_block = cs * 8 // bits
        f.seek(rt_offset)
        rtable = struct.unpack(f">{rt_clusters * cs // 8}Q", f.read(rt_clusters * cs))
        refblocks = {}

        def refcount(offset):
            idx = offset >> cluster_bits
            bi, i = divmod(idx, per_block)
            if bi >= len(rtable) or not rtable[bi]:
                return 0
            block = refblocks.get(bi)
            if block is None:
                f.seek(rtable[bi] & ~0x1FF)
                block = refblocks[bi] = f.read(cs)
            if bits == 16:
                return struct.unpack_from(">H", block, i * 2)[0]
            if bits == 8:
                return block[i]
            if bits == 32:
                return struct.unpack_from(">I", block, i * 4)[0]
            if bits == 64:
                return struct.unpack_from(">Q", block, i * 8)[0]
            byte, shift = divmod(i * bits, 8)
            return (block[byte] >> shift) & ((1 << bits) - 1)

        # table des instantanés
        snaps = []
        f.seek(snap_offset)
        pos = snap_offset
        for _i in range(nb_snapshots):
            f.seek(pos)
            h = f.read(40)
            l1_off, l1_size = struct.unpack(">QI", h[0:12])
            id_len, name_len = struct.unpack(">HH", h[12:16])
            extra = struct.unpack(">I", h[36:40])[0]
            f.seek(pos + 40 + extra + id_len)
            name = f.read(name_len).decode("utf-8", "replace")
            snaps.append((name, l1_off, l1_size))
            pos += 40 + extra + id_len + name_len
            pos = (pos + 7) & ~7

        sizes = {}
        for name, l1_off, l1_size in snaps:
            total = 0
            f.seek(l1_off)
            l1 = struct.unpack(f">{l1_size}Q", f.read(l1_size * 8))
            for e in l1:
                l2_off = e & off_mask
                if not l2_off:
                    continue
                if refcount(l2_off) == 1:
                    total += cs
                f.seek(l2_off)
                entries = struct.unpack(f">{cs // 8}Q", f.read(cs))
                for v in entries[::l2_entry // 8]:
                    if not v or v & (1 << 62):  # vide, ou groupe compressé : ignoré (rare ici)
                        continue
                    data = v & off_mask
                    if data and refcount(data) == 1:
                        total += cs
            sizes[name] = total
        return sizes
