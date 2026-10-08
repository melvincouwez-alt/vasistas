"""Carte graphique dédiée prêtée à Windows (VFIO), quand la machine le permet.

Réglage `gpu` dans config.json (application compagnon) :
- "off"  : jamais ;
- "auto" : haute performance graphique si la carte est disponible au démarrage de Windows
           (ordinateur branché, carte inutilisée côté Linux) ;
- "always" : dès que la carte est disponible, même sur batterie.

La carte passe au pilote vfio-pci avant le démarrage de QEMU et revient à son pilote Linux
après l'arrêt. Ces deux gestes demandent root : ils passent par l'assistant système
/usr/local/libexec/vasistas-gpu (tools/vasistas-gpu), appelé avec pkexec ; la règle polkit
posée avec lui l'autorise sans mot de passe pour l'utilisateur de la session.
"""

import logging
import subprocess
from pathlib import Path

from .i18n import N_

log = logging.getLogger(__name__)

PCI = Path("/sys/bus/pci/devices")
HELPER = Path("/usr/local/libexec/vasistas-gpu")
ASUS_DGPU = Path("/sys/devices/platform/asus-nb-wmi/dgpu_disable")
DISCRETE_VENDORS = {"0x10de": "NVIDIA", "0x1002": "AMD", "0x8086": "Intel"}

# états, du plus bloquant au prêt ; message pour l'application compagnon
MESSAGES = {
    "absent": N_("Aucune carte graphique dédiée détectée : la carte est absente ou désactivée en ce moment (mode "
                 "Éco, cardwire…)."),
    "disabled": N_("La carte graphique dédiée est désactivée (mode économie d'ASUS). Réactivez-la avec asusctl "
                   "ou Armoury Crate."),
    "iommu": N_("L'IOMMU n'est pas actif : activez AMD-Vi / VT-d dans le BIOS."),
    "group": N_("La carte graphique dédiée partage son groupe IOMMU avec d'autres périphériques : impossible de la "
                "prêter seule à Windows."),
    "helper": N_("L'assistant système n'est pas installé."),
    "screens": N_("Un écran externe est branché sur la carte graphique dédiée : la carte ne peut "
                  "pas être prêtée à Windows tant que cet écran l'utilise."),
    "busy": N_("La carte graphique dédiée est utilisée par une application Linux."),
    "battery": N_("L'ordinateur est sur batterie."),
    "ready": N_("Prête : Windows utilisera la carte graphique dédiée à son prochain démarrage."),
}


def _read(p):
    try:
        return Path(p).read_text().strip()
    except OSError:
        return ""


def find():
    """Carte dédiée : {"addr", "name", "functions": [adresses du même emplacement], "driver"} ou None.
    Dédiée = carte d'affichage qui n'est pas celle du bureau (pas de boot_vga)."""
    for dev in sorted(PCI.iterdir()) if PCI.exists() else []:
        cls = _read(dev / "class")
        if not cls.startswith("0x03") or _read(dev / "boot_vga") == "1":
            continue
        vendor = _read(dev / "vendor")
        if vendor not in DISCRETE_VENDORS:
            continue
        # la carte qui pilote l'écran interne est celle du bureau (carte intégrée)
        if any(_read(c / "status") == "connected" for c in _connectors(dev) if "-eDP-" in c.name):
            continue
        slot = dev.name.rsplit(".", 1)[0]
        functions = sorted(p.name for p in PCI.glob(f"{slot}.*"))
        name = _lspci_name(dev.name) or f"Carte {DISCRETE_VENDORS[vendor]}"
        driver = (dev / "driver").resolve().name if (dev / "driver").exists() else None
        return {"addr": dev.name, "name": name, "functions": functions, "driver": driver}
    return None


def _connectors(dev):
    return [c for card in (dev / "drm").glob("card*") for c in card.glob("card*-*")] if (dev / "drm").exists() else []


def screens(card):
    """Sorties de la carte qui affichent un écran en ce moment (écran externe branché dessus)."""
    return [c.name.split("-", 1)[1] for c in _connectors(PCI / card["addr"]) if _read(c / "enabled") == "enabled"]


def _lspci_name(addr):
    try:
        out = subprocess.run(["lspci", "-s", addr], capture_output=True, text=True, timeout=3).stdout
        return out.split(": ", 1)[1].strip() if ": " in out else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _in_use(card):
    """Vrai si une application Linux se sert de la carte (calcul, rendu)."""
    if card["driver"] == "nvidia":
        try:
            out = subprocess.run(["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader"],
                                 capture_output=True, text=True, timeout=5).stdout
            if out.strip():
                return True
        except (OSError, subprocess.TimeoutExpired):
            pass
    # rendu : un processus a ouvert le nœud DRM de la carte
    nodes = [str(p.resolve()) for p in (PCI / card["addr"] / "drm").glob("*")] if (PCI / card["addr"] / "drm").exists() else []
    devnodes = {f"/dev/dri/{Path(n).name}" for n in nodes}
    for proc in Path("/proc").iterdir():
        if not proc.name.isdigit():
            continue
        try:
            for fd in (proc / "fd").iterdir():
                if str(fd.readlink()) in devnodes:
                    return True
        except OSError:
            continue
    return False


def on_battery():
    for ps in Path("/sys/class/power_supply").glob("*"):
        if _read(ps / "type") == "Mains":
            return _read(ps / "online") != "1"
    return False


def status(check_power=True):
    """(état, carte) : état parmi MESSAGES."""
    card = find()
    if card is None:
        return ("disabled" if _read(ASUS_DGPU) == "1" else "absent"), None
    group = PCI / card["addr"] / "iommu_group" / "devices"
    if not group.exists():
        return "iommu", card
    if {p.name for p in group.iterdir()} - set(card["functions"]):
        return "group", card
    if card["driver"] != "vfio-pci" and screens(card):
        return "screens", card
    if card["driver"] != "vfio-pci" and _in_use(card):
        return "busy", card
    if not HELPER.exists():
        return "helper", card
    if check_power and on_battery():
        return "battery", card
    return "ready", card


def plan(mode):
    """Carte à prêter pour ce démarrage selon le réglage, ou None."""
    if mode not in ("auto", "always"):
        return None
    state, card = status(check_power=(mode == "auto"))
    if state != "ready":
        log.info("carte graphique dédiée non prêtée : %s", MESSAGES[state])
        return None
    return card


def bind(card):
    subprocess.run(["pkexec", str(HELPER), "bind", *card["functions"]], check=True, timeout=60)


def release(card):
    try:
        subprocess.run(["pkexec", str(HELPER), "unbind", *card["functions"]], check=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        log.warning("carte graphique non rendue à Linux : %s", e)


def qemu_args(card):
    args = []
    for i, fn in enumerate(card["functions"]):
        opt = f"vfio-pci,host={fn}"
        if i == 0 and len(card["functions"]) > 1:
            opt += ",multifunction=on"
        args += ["-device", opt]
    return args
