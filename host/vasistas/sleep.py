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

« Usage » = une fenêtre Windows au premier plan, ou un message de l'hôte vers l'invité
(souris, clavier, lancement, exec). Le réveil passe par `wake()`, appelé avant chaque envoi.
Au réveil (VM ou portable sorti de veille), l'horloge de Windows est remise à l'heure.
"""

import logging
import threading
import time
from datetime import datetime

from gi.repository import Gio, GLib

from . import vm

log = logging.getLogger(__name__)

CHECK_S = 30
DEFAULTS = {"sleep_minutes": 15, "shutdown_minutes": 0, "sleep_on_battery_only": True,
            "sleep_without_windows_minutes": 5}
# messages vers l'invité qui comptent comme un usage
ACTIVE = {"mouse.move", "mouse.button", "mouse.wheel", "key", "launch", "exec", "window.activate",
          "window.resize", "window.close", "clipboard"}


def settings():
    cfg = vm.load_config()
    return {k: cfg.get(k, v) for k, v in DEFAULTS.items()}


def on_battery():
    try:
        bus = Gio.bus_get_sync(Gio.BusType.SYSTEM)
        res = bus.call_sync("org.freedesktop.UPower", "/org/freedesktop/UPower",
                            "org.freedesktop.DBus.Properties", "Get",
                            GLib.Variant("(ss)", ("org.freedesktop.UPower", "OnBattery")),
                            GLib.VariantType("(v)"), Gio.DBusCallFlags.NONE, 1000, None)
        return bool(res.unpack()[0])
    except GLib.Error:
        return False


class SleepManager:
    def __init__(self, app):
        self.app = app
        self.last_use = time.monotonic()
        self.paused = False
        self.lock = threading.Lock()
        GLib.timeout_add_seconds(CHECK_S, self._check)
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
