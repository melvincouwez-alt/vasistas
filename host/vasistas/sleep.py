"""Mise en veille de Windows quand personne ne s'en sert.

Deux niveaux, réglés dans l'application compagnon (config.json) :
- `sleep_minutes` : après ce délai sans usage, la VM est suspendue (QMP stop). Elle ne
  consomme plus de processeur ; la mémoire reste prise et le réveil est immédiat.
- `shutdown_minutes` : après ce délai sans usage et sans aucune fenêtre ouverte, Windows
  est éteint (mémoire rendue). Jamais avec une fenêtre ouverte : un document pourrait
  ne pas être enregistré.
`sleep_on_battery_only` (vrai par défaut) limite les deux au fonctionnement sur batterie.
`sleep_without_windows_minutes` (5 par défaut) : sans aucune fenêtre Windows ouverte, la VM est
suspendue après ce délai, sur secteur aussi. Rien n'est visible, le réveil au lancement suivant
prend moins d'une seconde : Windows ne consomme plus rien entre deux usages.

`auto_shutdown_min` (0 = jamais ; 15, 30, 60 ou 120) : Windows est arrêté proprement après ce
délai sans aucune fenêtre ouverte, sur secteur comme sur batterie, qu'il soit en veille ou non.
Démarrage en veille (`vasistas boot --sleep`, ouverture de session) : Windows est mis en veille
dès qu'il est prêt, si rien n'a été ouvert entre-temps.

« Usage » = une fenêtre Windows au premier plan, ou un message de l'hôte vers l'invité
(souris, clavier, lancement, exec). Le réveil passe par `wake()`, appelé avant chaque envoi.
Au réveil (VM ou portable sorti de veille), l'horloge de Windows est remise à l'heure.
"""

import logging
import os
import threading
import time
from datetime import datetime

from gi.repository import Gio, GLib

from . import vm

log = logging.getLogger(__name__)

CHECK_S = 30
DEFAULTS = {"sleep_minutes": 15, "shutdown_minutes": 0, "sleep_on_battery_only": True,
            "sleep_without_windows_minutes": 5, "auto_shutdown_min": 0}
AUTO_SHUTDOWN_CHOICES = (0, 15, 30, 60, 120)
# démarrage en veille : laisser Windows finir son ouverture de session (dossiers partagés,
# agent) avant de le suspendre
BOOT_SLEEP_SETTLE_S = 20
BOOT_SLEEP_ENV = "VASISTAS_BOOT_SLEEP"
# messages vers l'invité qui comptent comme un usage
ACTIVE = {"mouse.move", "mouse.button", "mouse.wheel", "key", "launch", "exec", "window.activate",
          "window.resize", "window.close", "clipboard", "debug.windows", "bench.post"}


def settings():
    cfg = vm.load_config()
    s = {k: cfg.get(k, v) for k, v in DEFAULTS.items()}
    # ancien réglage « Éteindre Windows après » (batterie seulement, remplacé par l'arrêt
    # automatique sans fenêtre ouverte) : repris s'il était posé et que le nouveau ne l'est pas
    if not s["auto_shutdown_min"] and s["shutdown_minutes"]:
        s["auto_shutdown_min"] = min(AUTO_SHUTDOWN_CHOICES[1:], key=lambda m: abs(m - s["shutdown_minutes"]))
    s["shutdown_minutes"] = 0
    return s


def on_battery():
    # UPower, avec repli sur /sys/class/power_supply
    from . import power
    return power.on_battery()


def auto_shutdown_due(minutes, windows_open, empty_since, now):
    """Vrai s'il faut arrêter Windows : `minutes` (0 = jamais) écoulées depuis `empty_since`
    (instant où la dernière fenêtre s'est fermée, None tant qu'une fenêtre est ouverte)."""
    if not minutes or windows_open or empty_since is None:
        return False
    return now - empty_since >= minutes * 60


class SleepManager:
    def __init__(self, app):
        self.app = app
        self.last_use = time.monotonic()
        self.paused = False
        self.lock = threading.Lock()
        self.empty_since = None   # dernière fenêtre fermée (arrêt automatique)
        self.ready_since = None
        self.boot_sleep = os.environ.pop(BOOT_SLEEP_ENV, "") == "1"
        GLib.timeout_add_seconds(CHECK_S, self._check)
        GLib.timeout_add_seconds(2, self._watch)
        # profil de puissance et imprimantes : branchés ici, app.py crée SleepManager
        from . import power, printers
        self.power = power.PowerManager(app)
        self.printers = printers.PrinterSync(app)
        # sortie de veille du portable : Windows a dormi aussi, son horloge retarde
        try:
            bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)
            bus.signal_subscribe("org.freedesktop.login1", "org.freedesktop.login1.Manager",
                                 "PrepareForSleep", "/org/freedesktop/login1", None,
                                 Gio.DBusSignalFlags.NONE, self._on_host_sleep)
        except GLib.Error:
            pass

    def activity(self, msg_type=None):
        if msg_type is None or msg_type in ACTIVE:
            self.last_use = time.monotonic()
            if self.empty_since is not None:
                self.empty_since = self.last_use  # lancement en cours : pas d'arrêt
            if self.paused:
                self.wake()

    def wake(self):
        """Reprend la VM suspendue (appelé depuis n'importe quel fil, sans attendre QEMU :
        les messages vers l'invité attendent dans la file d'envoi du canal)."""
        with self.lock:
            if not self.paused:
                return
            self.paused = False
        vm.qmp_worker().call_async("cont", on_error=lambda e: log.warning("réveil : %s", e))
        log.info("Windows réveillé")
        GLib.idle_add(self.sync_clock)

    def sync_clock(self):
        """Remet Windows à l'heure de Linux (l'horloge de l'invité s'arrête pendant la pause)."""
        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        self.app.send({"t": "exec", "req": 0, "script": f"Set-Date -Date '{now}' | Out-Null"})
        return False

    def _on_host_sleep(self, *args):
        going_down = args[-1].unpack()[0]
        if not going_down and self.app.guest_ready:
            GLib.timeout_add_seconds(3, self.sync_clock)

    def _idle_minutes(self):
        if any(getattr(v, "is_active", lambda: False)() for v in self.app.views.values()):
            self.last_use = time.monotonic()
        return (time.monotonic() - self.last_use) / 60

    def _watch(self):
        """Toutes les 2 s : fenêtres ouvertes ou non, démarrage en veille, imprimantes au
        démarrage de Windows."""
        self.printers.check()
        now = time.monotonic()
        if not self.app.guest_ready:
            self.empty_since = self.ready_since = None
            return True
        if self.ready_since is None:
            self.ready_since = now
        if self.app.views:
            self.empty_since = None
        elif self.empty_since is None:
            self.empty_since = now
        if self.boot_sleep and now - self.ready_since >= BOOT_SLEEP_SETTLE_S:
            self.boot_sleep = False
            if not self.app.views and not self.app.launch_queue and not self.app.pending_exec:
                if self.pause():
                    log.info("Windows prêt et mis en veille (démarrage en veille)")
        return True

    def _check(self):
        try:
            self._maybe_sleep()
        except Exception:
            log.exception("veille")
        return True

    def _maybe_sleep(self):
        if not self.app.guest_ready or not vm.pid():
            return
        s = settings()
        if auto_shutdown_due(s["auto_shutdown_min"], bool(self.app.views), self.empty_since,
                             time.monotonic()):
            log.info("Windows arrêté : aucune fenêtre ouverte depuis %d min", s["auto_shutdown_min"])
            self.empty_since = None
            self.wake()
            threading.Thread(target=vm.stop, daemon=True).start()
            return
        idle = self._idle_minutes()
        if not self.app.views and s["sleep_without_windows_minutes"] and not self.paused \
                and idle >= s["sleep_without_windows_minutes"]:
            if self.pause():
                log.info("Windows en veille : aucune fenêtre ouverte depuis %d min", idle)
            return
        if s["sleep_on_battery_only"] and not on_battery():
            return
        if s["shutdown_minutes"] and idle >= s["shutdown_minutes"] and not self.app.views:
            log.info("Windows éteint après %d min sans usage", idle)
            self.wake()
            threading.Thread(target=vm.stop, daemon=True).start()
            return
        if s["sleep_minutes"] and idle >= s["sleep_minutes"] and not self.paused:
            if self.pause():
                log.info("Windows en veille après %d min sans usage", idle)

    def pause(self):
        """Suspend la VM ; le prochain usage la réveille. Renvoie l'état voulu (la commande
        part dans le fil QMP ; en cas d'échec, l'état repasse à « éveillé »)."""
        with self.lock:
            if self.paused or not vm.pid():
                return self.paused
            self.paused = True

        def failed(e):
            log.warning("mise en veille : %s", e)
            with self.lock:
                self.paused = False
        vm.qmp_worker().call_async("stop", on_error=failed)
        return True
