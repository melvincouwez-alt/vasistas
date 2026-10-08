"""Puissance donnée à Windows selon l'alimentation et les applications ouvertes.

Profils de vm.RESOURCES : « battery », « balanced », « performance ». Réglages (config.json) :
- `resources` : profil choisi, celui du secteur ;
- `resources_auto` (vrai par défaut) : sur batterie (ou mode Économie du système), profil
  « battery » ; sur secteur, le profil choisi ;
- `app_modes` : mode d'affichage réservé à une application ({app: eco|balanced|smooth}),
  appliqué tant qu'elle est au premier plan (Power BI en Fluide par défaut).

Le curseur Affichage du compagnon (MODES) règle tout d'un coup : puissance, minuterie,
synchronisation et cadence des fenêtres recouvertes.

QEMU ne change de cœurs ni de mémoire qu'au démarrage : vm.resources() prend le profil du
moment (boot_profile). Le reste suit à chaud (PowerManager) :
- mémoire : le ballon (balloon.py) ne laisse pas Windows dépasser celle du profil ;
- processeur : sur « battery », les fils de QEMU ne tournent que sur les cœurs les plus
  sobres (affinité) ;
- capture : cadence des fenêtres recouvertes dans l'agent (message `capture`).

Le système maison ~/.local/opt/modes-energie (s'il existe) suit power-profiles-daemon : son
mode Économie est « power-saver », que le profil automatique traite comme la batterie.
"""

import functools
import json
import logging
import os
from pathlib import Path

from .i18n import N_

log = logging.getLogger(__name__)

PROFILES = ("battery", "balanced", "performance")
# autre nom d'une application dans les réglages -> identifiants (exécutable en minuscules)
APP_ALIASES = {"powerbi": ("pbidesktop", "powerbi")}
DEFAULT_APP_MODES = {"pbidesktop": "smooth"}
# fenêtres recouvertes : délai entre deux captures dans l'agent (ms)
OCCLUDED_MS = {"battery": 1000, "balanced": 500, "performance": 250}
CHECK_S = 5
MODES_ENERGIE = Path.home() / ".local/opt/modes-energie/modes.json"
# noms du menu d'énergie du système, quand modes-energie est installé
SYSTEM_NAMES = {"battery": N_("Économie"), "balanced": N_("Équilibré"), "performance": N_("Performance")}
RESOURCE_LABELS = {"battery": N_("Économie d'énergie"), "balanced": N_("Équilibré"),
                   "performance": N_("Performances")}
PPD = (("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles"),
       ("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles"))
POWER_SUPPLY = Path("/sys/class/power_supply")


# -- choix du profil (fonctions pures) --

def settings(cfg=None):
    """(profil choisi, automatique ?)."""
    if cfg is None:
        from . import vm
        cfg = vm.load_config()
    chosen = cfg.get("resources") if cfg.get("resources") in PROFILES else "balanced"
    return chosen, cfg.get("resources_auto", True) is not False


def canonical(app):
    """Identifiant d'application : exécutable en minuscules, alias résolus (powerbi -> pbidesktop)."""
    app = _norm(app)
    for alias, ids in APP_ALIASES.items():
        if app == alias:
            return ids[0]
    return app


def app_modes(cfg):
    """{app: clé de MODES}. Sans réglage : l'ancienne liste `heavy_apps` passe en Fluide,
    sinon Power BI en Fluide."""
    keys = {k for k, *_r in MODES}
    raw = cfg.get("app_modes")
    if not isinstance(raw, dict):
        heavy = cfg.get("heavy_apps")
        raw = {a: "smooth" for a in heavy} if isinstance(heavy, list) else DEFAULT_APP_MODES
    return {canonical(a): m for a, m in raw.items() if m in keys and _norm(a)}


# -- réglages d'affichage (page Performances, onglet Avancé) --
# `timer` : minuterie de Windows à 1 ms, « auto » (pas sur batterie), « always », « never » ;
# `occluded_ms` : délai fixe entre deux captures d'une fenêtre recouverte (absent : selon le profil) ;
# `vsync` : mode du pilote maison (bits, docs/pilote-maison.md), écrit dans l'invité par l'hôte ;
# `renderer` : rendu GTK de l'hôte, « auto », « vulkan » ou « gl » (__main__.py).
TIMERS = ("auto", "always", "never")
RENDERERS = ("auto", "vulkan", "gl")
OCCLUDED_CHOICES = (100, 250, 500, 1000, 2000)
VSYNC_MODES = (0, 1, 3, 7)
VSYNC_DEFAULT = 7

# curseur Affichage : (clé, nom, ce que ça fait, batterie, performances, réglages, profil à chaud)
# Le profil à chaud sert quand le mode est réservé à une application au premier plan : cœurs
# utilisés et mémoire laissée (les cœurs et la mémoire totale ne changent qu'au démarrage).
MODES = [
    ("eco", N_("Optimisé"),
     N_("Windows rafraîchit moins souvent l'écran et les fenêtres cachées derrière d'autres fenêtres. Adapté aux "
        "courriels et aux documents."),
     N_("Batterie : la plus longue."),
     N_("Performances : suffisantes pour la bureautique, défilement un peu moins régulier."),
     {"timer": "never", "vsync": 0, "occluded_ms": 1000},
     "battery"),
    ("balanced", N_("Équilibré"),
     N_("Compromis entre autonomie et fluidité : images au rythme d'un écran à 60 Hz, minuterie précise sur secteur."),
     N_("Batterie : normale."),
     N_("Performances : bonnes pour un usage courant."),
     {"timer": "auto", "vsync": 7, "occluded_ms": None},
     None),
    ("smooth", N_("Fluide"),
     N_("Windows vise 60 images par seconde en permanence, fenêtres cachées comprises. Pour "
        "Power BI, les vidéos et les longs défilements."),
     N_("Batterie : se vide plus vite, même quand Windows ne fait rien."),
     N_("Performances : les meilleures."),
     {"timer": "always", "vsync": 7, "occluded_ms": 250},
     "performance"),
]
# curseur « Performance de la machine » : cœurs et mémoire de Windows (vm.RESOURCES), au démarrage
MACHINE = [
    ("battery", N_("Économie"), N_("4 cœurs et 6 Go : Windows utilise peu de ressources de l'ordinateur.")),
    ("balanced", N_("Équilibré"), N_("8 cœurs et 8 Go : réglage adapté à un usage courant.")),
    ("performance", N_("Performances"), N_("12 cœurs et 12 Go : pour Power BI et les gros classeurs.")),
]
PRESET_DEFAULTS = {"timer": "auto", "vsync": VSYNC_DEFAULT, "occluded_ms": None}
# réglages qui ne prennent effet qu'au prochain démarrage de Windows
RESTART_KEYS = ("vsync",)


def machine_index(cfg):
    keys = [k for k, *_r in MACHINE]
    return keys.index(cfg.get("resources")) if cfg.get("resources") in keys else 1


def mode_index(cfg):
    """Cran du curseur correspondant aux réglages, None s'ils ont été changés à la main."""
    for i, (*_r, values, _p) in enumerate(MODES):
        if all(cfg.get(k, PRESET_DEFAULTS[k]) == v for k, v in values.items()):
            return i
    return None


def apply_mode(cfg, index):
    """Réglages du cran `index` posés dans cfg ; renvoie les clés qui demandent un redémarrage."""
    values = MODES[index][5]
    restart = [k for k in RESTART_KEYS if cfg.get(k, PRESET_DEFAULTS[k]) != values[k]]
    for k, v in values.items():
        if v is None:
            cfg.pop(k, None)
        else:
            cfg[k] = v
    return restart


def mode_values(key):
    return next(m[5] for m in MODES if m[0] == key)


def capture_message(cfg, profile):
    """Message `capture` de l'agent pour ce profil et ces réglages (timer, occluded_ms)."""
    timer = cfg.get("timer") if cfg.get("timer") in TIMERS else "auto"
    on = timer == "always" or (timer == "auto" and profile != "battery")
    ms = cfg.get("occluded_ms")
    return {"t": "capture", "occluded_ms": ms if ms in OCCLUDED_CHOICES else OCCLUDED_MS[profile],
            "timer_ms": 1 if on else 0}


def vsync_mode(cfg, profile=None):
    """Mode du pilote maison ; coupé sur le profil batterie (+4 points de processeur au repos).
    Le pilote ne relit la clé qu'à son chargement : vaut pour le prochain démarrage de Windows."""
    if profile == "battery":
        return 0
    v = cfg.get("vsync", VSYNC_DEFAULT)
    return v if v in VSYNC_MODES else VSYNC_DEFAULT


# Écrit le mode dans la clé du service du pilote maison, s'il est posé (pilote d'origine : rien).
# Sortie : absent, same ou changed (le pilote ne relit la clé qu'à son chargement).
VSYNC_PS = r"""$k = 'HKLM:\SYSTEM\CurrentControlSet\Services\VioGpuDod'
$s = Get-ItemProperty $k -ErrorAction SilentlyContinue
if (-not $s -or $s.ImagePath -notlike '*viogpudo-vasistas*') { 'absent'; return }
if ($s.VasistasVsync -eq %d) { 'same' } else { Set-ItemProperty $k VasistasVsync %d -Type DWord; 'changed' }"""


def _norm(name):
    return "".join(c for c in str(name).lower() if c.isalnum())


def choose_profile(chosen, auto, battery, saver=False, rule=None):
    """Profil à appliquer. Un mode réservé à l'application au premier plan l'emporte (son profil,
    ou l'automatique pour Équilibré) ; sinon batterie et mode Économie passent avant le choix."""
    rule_profile = next((m[6] for m in MODES if m[0] == rule), None)
    if rule_profile:
        return rule_profile
    if rule:
        auto = True
    if not auto:
        return chosen
    if battery or saver:
        return "battery"
    return chosen


def reason(chosen, auto, battery, saver=False, rule=None):
    """Pourquoi ce profil, en clair (non traduit : passer par _ à l'affichage)."""
    if rule:
        return N_("réglage de l'application au premier plan")
    if not auto:
        return N_("choisi")
    if battery:
        return N_("sur batterie")
    if saver:
        return N_("mode Économie du système")
    return N_("sur secteur")


def memory_bytes(text):
    """« 6G » -> octets (format de -m de QEMU)."""
    text = str(text).strip().upper()
    units = {"K": 2 ** 10, "M": 2 ** 20, "G": 2 ** 30, "T": 2 ** 40}
    if text and text[-1] in units:
        return int(float(text[:-1]) * units[text[-1]])
    return int(text) * 2 ** 20  # sans unité : Mo, comme QEMU


def memory_cap(profile):
    """Mémoire que Windows peut garder à chaud dans ce profil (octets), None : pas de plafond."""
    from . import vm
    if profile == "performance" or profile not in vm.RESOURCES:
        return None
    return memory_bytes(vm.RESOURCES[profile][1])


def efficient_cpus(perfs, n):
    """Les n processeurs logiques les plus sobres ({cpu: highest_perf}) ; à égalité, les
    premiers numéros. Ensemble vide si on ne sait rien."""
    if not perfs or n <= 0:
        return set()
    return set(sorted(perfs, key=lambda c: (perfs[c], c))[:n])


def battery_from_sysfs(supplies):
    """Sur batterie d'après /sys/class/power_supply : [{type, online, status, scope}].
    Un secteur branché (Mains, USB) l'emporte ; batteries d'appareils (souris) ignorées."""
    for s in supplies:
        if s.get("type") in ("Mains", "USB", "USB_C", "USB_PD") and str(s.get("online", "")).strip() == "1":
            return False
    return any(s.get("type") == "Battery" and s.get("scope", "System") != "Device"
               and str(s.get("status", "")).strip() == "Discharging" for s in supplies)


def parse_qemu_cmdline(argv):
    """(cœurs, mémoire en octets, guestfwd présent) d'après la ligne de commande de QEMU."""
    cpus = mem = None
    fwd = False
    for i, arg in enumerate(argv[:-1]):
        nxt = argv[i + 1]
        if arg == "-smp":
            first = nxt.split(",")[0]
            cpus = int(first) if first.isdigit() else None
        elif arg == "-m":
            try:
                mem = memory_bytes(nxt.split(",")[0])
            except ValueError:
                mem = None
        elif arg in ("-nic", "-netdev") and "guestfwd=" in nxt:
            fwd = True
    return cpus, mem, fwd


# -- état du système --

def _sysfs_supplies():
    out = []
    try:
        entries = list(POWER_SUPPLY.iterdir())
    except OSError:
        return out
    for d in entries:
        info = {}
        for key in ("type", "online", "status", "scope"):
            try:
                info[key] = (d / key).read_text().strip()
            except OSError:
                pass
        out.append(info)
    return out


@functools.lru_cache(maxsize=None)
def _proxy(name, path, iface):
    """Proxy du bus système : GIO tient ses propriétés à jour (PropertiesChanged), une lecture
    ne fait plus d'aller-retour D-Bus ni ne réveille le service."""
    from gi.repository import Gio
    return Gio.DBusProxy.new_for_bus_sync(Gio.BusType.SYSTEM, Gio.DBusProxyFlags.NONE, None,
                                          name, path, iface, None)


def _dbus_get(name, path, iface, prop):
    value = _proxy(name, path, iface).get_cached_property(prop)
    if value is None:
        raise LookupError(f"{name} {prop}")  # service absent : repli de l'appelant
    return value.unpack()


def on_battery():
    """UPower (OnBattery), sinon /sys/class/power_supply."""
    try:
        return bool(_dbus_get("org.freedesktop.UPower", "/org/freedesktop/UPower",
                              "org.freedesktop.UPower", "OnBattery"))
    except Exception:  # noqa: BLE001 - pas de bus système, UPower absent
        return battery_from_sysfs(_sysfs_supplies())


def system_profile():
    """Profil de power-profiles-daemon (power-saver, balanced, performance) ou None."""
    for name, path in PPD:
        try:
            return str(_dbus_get(name, path, name, "ActiveProfile"))
        except Exception:  # noqa: BLE001
            continue
    return None


def energy_modes():
    """Vrai si le système maison modes-energie est installé (noms alignés sur son menu)."""
    try:
        json.loads(MODES_ENERGIE.read_text())
        return True
    except (OSError, ValueError):
        return False


def profile_label(profile):
    """Nom affiché d'un profil : celui du menu d'énergie si modes-energie est là."""
    from .i18n import _
    if energy_modes():
        return _(SYSTEM_NAMES[profile])
    return _(RESOURCE_LABELS[profile])


def boot_profile(cfg=None):
    """Profil au démarrage de QEMU (cœurs et mémoire) : les applications lourdes ne sont pas
    encore ouvertes, seules l'alimentation et le mode du système comptent."""
    chosen, auto = settings(cfg)
    if not auto:
        return chosen
    return choose_profile(chosen, auto, on_battery(), system_profile() == "power-saver")


def qemu_cmdline(pid):
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [a.decode(errors="replace") for a in raw.split(b"\0") if a]


def cpu_perfs():
    """{cpu: highest_perf} (ACPI CPPC) ; vide si le noyau ne le donne pas."""
    out = {}
    for d in Path("/sys/devices/system/cpu").glob("cpu[0-9]*"):
        try:
            out[int(d.name[3:])] = int((d / "acpi_cppc/highest_perf").read_text())
        except (OSError, ValueError):
            continue
    return out


def set_affinity(pid, cpus):
    """Affinité de tous les fils d'un processus ; cpus vide : tous les processeurs."""
    target = cpus or set(range(os.cpu_count() or 1))
    n = 0
    try:
        tids = os.listdir(f"/proc/{pid}/task")
    except OSError:
        return 0
    for tid in tids:
        try:
            os.sched_setaffinity(int(tid), target)
            n += 1
        except OSError:
            pass
    return n


class PowerManager:
    """Applique à chaud le profil du moment (créé par SleepManager : app.py n'est pas touché)."""

    def __init__(self, app):
        from gi.repository import GLib
        self.app = app
        self.profile = None
        self.why = None
        self.rule = self.rule_app = None
        self.applied_pid = None
        self.sent_ready = False
        self.sent_capture = self.sent_vsync = None
        self.cpu_info = cpu_perfs()
        GLib.timeout_add_seconds(CHECK_S, self._tick)

    def foreground_app(self):
        """Application de la dernière fenêtre Windows active, tant qu'elle est au premier plan."""
        win = getattr(self.app, "last_active", None)
        if win is None or not win.is_active():
            return None
        return getattr(self.app, "infos", {}).get(win.wid, {}).get("app")

    def current(self, cfg):
        """(profil, raison, mode réservé à l'application au premier plan ou None)."""
        chosen, auto = settings(cfg)
        fg = self.foreground_app()
        rule = app_modes(cfg).get(canonical(fg)) if fg else None
        self.rule_app = canonical(fg) if rule else None
        battery = on_battery() if auto or rule else False
        saver = (auto or rule) and system_profile() == "power-saver"
        return (choose_profile(chosen, auto, battery, saver, rule),
                reason(chosen, auto, battery, saver, rule), rule)

    def snapshot(self):
        """Profil du moment pour le compagnon (requête status)."""
        return {"profile": self.profile, "why": self.why, "rule": self.rule, "app": self.rule_app}

    def memory_cap(self):
        """Plafond du ballon pour le profil du moment (None : aucun)."""
        return memory_cap(self.profile) if self.profile else None

    def _tick(self):
        try:
            self._apply()
        except Exception:  # noqa: BLE001 - ne jamais arrêter la minuterie
            log.exception("profil de puissance")
        return True

    def _apply(self):
        from . import vm
        pid = vm.pid()
        if not pid:
            self.applied_pid = None
            self.sent_ready = False
            return
        cfg = vm.load_config()
        profile, why, rule = self.current(cfg)
        self.rule = rule
        changed = profile != self.profile or pid != self.applied_pid
        if changed:
            if profile != self.profile:
                log.info("profil de puissance : %s (%s)", profile, why)
            self.profile, self.why, self.applied_pid = profile, why, pid
            n_cpus = vm.RESOURCES.get("battery", (4, ""))[0]
            cpus = efficient_cpus(self.cpu_info, n_cpus) if profile == "battery" else set()
            set_affinity(pid, cpus)
        ready = bool(self.app.guest_ready)
        # agent sans ce message : il l'ignore
        # timer_ms : minuterie de Windows à 1 ms (DWM à ~49 images/s au lieu de ~41)
        msg = capture_message(mode_values(rule) if rule else cfg, profile)
        if ready and (changed or not self.sent_ready or msg != self.sent_capture):
            self.app.send(msg)
            self.sent_capture = msg
        if not ready:
            self.sent_capture = self.sent_vsync = None
        elif vsync_mode(cfg, profile) != self.sent_vsync:
            self.sync_vsync(vsync_mode(cfg, profile))
        self.sent_ready = ready

    def sync_vsync(self, mode):
        """Mode du pilote maison redonné à Windows (au démarrage et quand il change)."""
        self.sent_vsync = mode

        def done(res):
            from . import vm
            out = (res.get("out") or "").strip()
            log.info("pilote maison : mode %d (%s)", mode, out or res)
            if out in ("absent", "same", "changed"):
                c = vm.load_config()
                if c.get("vsync_driver") != (out != "absent"):
                    c["vsync_driver"] = out != "absent"
                    vm.save_config(c)
        self.app.run_script(VSYNC_PS % (mode, mode), done)
