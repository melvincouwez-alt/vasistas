"""Ballon mémoire automatique : rendre à Linux la mémoire dont Windows ne se sert pas.

Windows ne rend rien de lui-même (pas de « free page reporting » dans son pilote). Toutes les
10 s, l'hôte lit l'usage réel de Windows (statistiques du pilote Balloon de virtio-win) et
règle la taille du ballon : Windows garde ce qu'il utilise plus une marge. Gonfler (reprendre
de la mémoire) se fait par petits pas ; dégonfler (rendre à Windows) tout de suite, et d'avance
au lancement d'une application.

Mesuré le 2026-09-29 : le dégonflage va à ~120 Mo/s, d'où une marge large.
Réglage `balloon_auto` dans config.json (vrai par défaut).
Profil de puissance (power.py) : Windows ne garde pas plus que la mémoire du profil du moment
(6 Go sur batterie même s'il a démarré avec 12), sans jamais descendre sous son usage réel.
"""

import logging
import threading
import time

from gi.repository import GLib

from . import vm

log = logging.getLogger(__name__)

MB = 2 ** 20
INTERVAL_S = 10
MIN_TARGET = 3072 * MB     # jamais moins pour Windows
MARGIN_MIN = 1536 * MB     # marge au-dessus de l'usage réel
MARGIN_RATIO = 0.4
INFLATE_STEP = 1024 * MB   # reprise progressive
HYSTERESIS = 512 * MB
BOOST_S = 90               # après un lancement : pas de reprise pendant ce temps


def enabled():
    return vm.load_config().get("balloon_auto", True)


def capped(target, used, cap):
    """Cible plafonnée par le profil de puissance, jamais sous l'usage réel plus une marge."""
    if cap is None:
        return target
    return min(target, max(cap, used + HYSTERESIS))


class BalloonManager:
    def __init__(self, app):
        self.app = app
        self.path = None
        self.max = None
        self.boost_until = 0.0
        self.lock = threading.Lock()
        GLib.timeout_add_seconds(INTERVAL_S, self._tick)

    @staticmethod
    def _qmp(command, **args):
        # fil QMP commun de l'hôte : plus de conflit avec la veille ou l'affichage
        return vm.qmp_worker().call(command, **args)

    def _setup(self):
        for base in ("/machine/peripheral", "/machine/peripheral-anon"):
            try:
                children = self._qmp("qom-list", path=base)
            except RuntimeError:
                continue
            for c in children:
                if c["type"] == "child<virtio-balloon-pci>":
                    self.path = f"{base}/{c['name']}"
        if not self.path:
            return False
        self._qmp("qom-set", path=self.path, property="guest-stats-polling-interval", value=INTERVAL_S)
        self.max = self._qmp("query-memory-size-summary")["base-memory"]
        log.info("ballon : %s, %d Mo au plus", self.path, self.max // MB)
        return True

    def _tick(self):
        # tests en mémoire d'abord : config.json et le fichier pid ne sont lus que VM en marche
        if (self.app.guest_ready and not (self.app.sleep and self.app.sleep.paused)
                and not self.lock.locked() and enabled() and vm.pid()):
            threading.Thread(target=self._adjust, daemon=True).start()
        return True

    def _adjust(self):
        with self.lock:
            try:
                if self.path is None and not self._setup():
                    return
                res = self._qmp("qom-get", path=self.path, property="guest-stats")
                if not res.get("last-update"):
                    # QEMU relancé : son intervalle de statistiques est revenu à 0, à reposer
                    self.path = None
                    return
                if time.time() - res["last-update"] > 30:
                    return  # pilote muet (Windows qui démarre)
                s = res["stats"]
                actual = self._qmp("query-balloon")["actual"]
                # le total vu par Windows ne baisse pas avec le ballon ; « disponible » oui
                used = s["stat-total-memory"] - s["stat-available-memory"] - (self.max - actual)
                if used <= 0 or actual > self.max:
                    # VM relancée avec une autre taille, ou statistiques d'avant le démarrage :
                    # on relit la configuration au prochain passage au lieu de reprendre de la mémoire
                    self.path = None
                    return
                target = max(MIN_TARGET, used + max(MARGIN_MIN, int(used * MARGIN_RATIO)))
                target = min(self.max, target)
                if time.monotonic() < self.boost_until:
                    target = self.max
                target = capped(target, used, self._cap())
                if target > actual + 128 * MB:
                    self._set(target, f"rendu à Windows (utilise {used // MB} Mo)")
                elif actual - target > HYSTERESIS:
                    self._set(max(target, actual - INFLATE_STEP), f"repris (Windows utilise {used // MB} Mo)")
            except (OSError, EOFError, RuntimeError, KeyError, TimeoutError) as e:
                log.debug("ballon : %s", e)
                self.path = None

    def _cap(self):
        power = getattr(self.app.sleep, "power", None) if self.app.sleep else None
        return power.memory_cap() if power is not None else None

    def _set(self, value, why):
        value = value // MB * MB
        self._qmp("balloon", value=value)
        log.info("ballon : Windows à %d Mo, %s", value // MB, why)

    def boost(self):
        """Lancement d'une application : rendre toute la mémoire à Windows d'avance."""
        self.boost_until = time.monotonic() + BOOST_S
        if enabled() and vm.pid() and not self.lock.locked():
            def work():
                with self.lock:
                    try:
                        if self.path is None and not self._setup():
                            return
                        cap = self._cap()
                        top = min(self.max, cap) if cap else self.max
                        if self._qmp("query-balloon")["actual"] < top:
                            self._set(top, "lancement d'une application")
                    except (OSError, EOFError, RuntimeError, TimeoutError) as e:
                        log.debug("ballon : %s", e)
            threading.Thread(target=work, daemon=True).start()
