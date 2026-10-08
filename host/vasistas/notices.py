"""Notifications du bureau qui proposent une action (« Windows ne répond pas » → Redémarrer).

    from . import notices
    notices.windows_not_responding(on_restart=self.restart_vm)
    notices.send("Titre", "Texte", [("open", "Ouvrir", callback)])

Par org.freedesktop.Notifications (Gio.DBus) : boutons d'action, réponse par le signal
ActionInvoked, reçue dans la boucle GLib de l'appelant (hôte ou compagnon). Sans service de
notifications sur le bus, repli sur notify-send --action (libnotify 0.7.10 et plus) dans un fil.
Les rappels sont appelés dans le fil GLib principal.
"""

import os
import subprocess
import sys
import threading

from gi.repository import Gio, GLib

from .i18n import _

APP_ID = "io.github.melvincouwez.Vasistas"
BUS_NAME = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"
IFACE = "org.freedesktop.Notifications"

_bus = None
_caps = None
_actions = {}    # id de notification -> {clé d'action: rappel}
_by_key = {}     # clé de message (une seule notification de chaque sorte) -> id
_lock = threading.Lock()


def _connect():
    """Connexion au bus de session et abonnement aux signaux, une fois."""
    global _bus
    if _bus is None:
        _bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        _bus.signal_subscribe(BUS_NAME, IFACE, "ActionInvoked", PATH, None, Gio.DBusSignalFlags.NONE,
                              _on_action)
        _bus.signal_subscribe(BUS_NAME, IFACE, "NotificationClosed", PATH, None, Gio.DBusSignalFlags.NONE,
                              _on_closed)
    return _bus


def _capabilities(bus):
    global _caps
    if _caps is None:
        try:
            _caps = set(bus.call_sync(BUS_NAME, PATH, IFACE, "GetCapabilities", None,
                                      GLib.VariantType("(as)"), Gio.DBusCallFlags.NONE, 2000, None)[0])
        except GLib.Error:
            _caps = set()
    return _caps


def _on_action(_conn, _sender, _path, _iface, _signal, params):
    nid, key = params.unpack()
    with _lock:
        cb = _actions.get(nid, {}).get(key)
    if cb is not None:
        GLib.idle_add(_call, cb)


def _on_closed(_conn, _sender, _path, _iface, _signal, params):
    nid = params.unpack()[0]
    with _lock:
        _actions.pop(nid, None)
        for k, v in list(_by_key.items()):
            if v == nid:
                _by_key.pop(k)


def _call(cb):
    try:
        cb()
    except Exception:  # noqa: BLE001 - une action ratée ne doit pas faire tomber l'hôte
        import logging
        logging.getLogger(__name__).exception("action de notification")
    return False


def send(summary, body="", actions=(), key=None, icon=APP_ID, urgent=False):
    """Affiche une notification ; actions : [(clé, texte du bouton, rappel)]. La clé « default »
    répond au clic sur la notification elle-même. `key` : une seule notification de cette sorte
    à la fois (la nouvelle remplace l'ancienne). Renvoie l'identifiant, ou 0 (repli)."""
    actions = list(actions)
    try:
        bus = _connect()
        caps = _capabilities(bus)
        flat = []
        if "actions" in caps:
            for k, label, _cb in actions:
                flat += [k, label]
        hints = {"desktop-entry": GLib.Variant("s", APP_ID),
                 "urgency": GLib.Variant("y", 2 if urgent else 1)}
        with _lock:
            replaces = _by_key.get(key, 0) if key else 0
        nid = bus.call_sync(BUS_NAME, PATH, IFACE, "Notify",
                            GLib.Variant("(susssasa{sv}i)", ("Vasistas", replaces, icon, summary, body, flat,
                                                             hints, -1)),
                            GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 2000, None)[0]
        with _lock:
            _actions[nid] = {k: cb for k, _label, cb in actions}
            if key:
                _by_key[key] = nid
        return nid
    except GLib.Error:
        _fallback(summary, body, actions)
        return 0


def _fallback(summary, body, actions):
    """notify-send : attend le choix dans un fil (--wait) et appelle le rappel dans le fil GLib."""
    args = ["notify-send", "-a", "Vasistas", "-i", APP_ID]
    if actions:
        args += [f"--action={k}={label}" for k, label, _cb in actions] + ["--wait"]
    args += [summary, body]
    callbacks = {k: cb for k, _label, cb in actions}

    def work():
        try:
            out = subprocess.run(args, capture_output=True, text=True).stdout.strip()
        except OSError:
            return
        if out in callbacks:
            GLib.idle_add(_call, callbacks[out])
    if actions:
        threading.Thread(target=work, name="vasistas-notify", daemon=True).start()
    else:
        try:
            subprocess.Popen(args)
        except OSError:
            pass


def withdraw(key):
    """Retire la notification de cette sorte (le problème est réglé)."""
    with _lock:
        nid = _by_key.pop(key, None)
    if nid and _bus is not None:
        try:
            _bus.call_sync(BUS_NAME, PATH, IFACE, "CloseNotification", GLib.Variant("(u)", (nid,)), None,
                           Gio.DBusCallFlags.NONE, 2000, None)
        except GLib.Error:
            pass


# -- actions par défaut --

def open_companion(page=None, fix=None):
    """Ouvre l'application compagnon (sur une page, avec une réparation à lancer)."""
    from . import desktop
    args = [sys.executable, "-m", "vasistas", "companion"]
    if page:
        args += ["--page", page]
    if fix:
        args += ["--fix", fix]
    env = dict(os.environ, PYTHONPATH=str(desktop.HOST_DIR))
    try:
        subprocess.Popen(args, env=env, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def _restart_vm():
    from . import vm

    def work():
        try:
            vm.stop()
            vm.start()
        except (SystemExit, Exception):  # noqa: BLE001
            GLib.idle_add(lambda: send(_("Windows ne redémarre pas"),
                                       _("Ouvrez le diagnostic pour connaître la cause du problème."),
                                       [("diagnose", _("Ouvrir le diagnostic"), lambda: open_companion("diagnose"))],
                                       key="restart-failed") and False)
    threading.Thread(target=work, name="vasistas-restart", daemon=True).start()


# -- messages types --

def windows_not_responding(on_restart=None, on_diagnose=None):
    """Windows (l'agent) ne s'est pas manifesté à temps."""
    return send(_("Windows ne répond pas"),
                _("Les applications demandées s'ouvriront dès que Windows répondra. Vous pouvez aussi redémarrer "
                  "Windows."),
                [("restart", _("Redémarrer Windows"), on_restart or _restart_vm),
                 ("diagnose", _("Ouvrir le diagnostic"), on_diagnose or (lambda: open_companion("diagnose")))],
                key="not-responding", urgent=True)


def low_disk(free_gb, on_open=None):
    """Plus assez de place sur le disque qui porte la VM."""
    return send(_("Plus assez d'espace disque"),
                _("Il reste {n} Go d'espace libre : Windows risque de s'arrêter. Libérez de l'espace disque ou "
                  "supprimez des points de restauration.", n=f"{free_gb:.0f}"),
                [("diagnose", _("Ouvrir le diagnostic"), on_open or (lambda: open_companion("diagnose"))),
                 ("default", _("Ouvrir le diagnostic"), on_open or (lambda: open_companion("diagnose")))],
                key="low-disk", urgent=True)


def old_agent(agent_version, host_version=None, on_update=None):
    """L'agent installé dans Windows est plus ancien que Vasistas."""
    from .version import VERSION
    return send(_("L'agent Vasistas installé dans Windows n'est pas à jour"),
                _("L'agent installé dans Windows est en version {agent}, Vasistas est en version {host}. La mise à "
                  "jour de l'agent prend environ une minute et ne modifie pas vos applications.",
                  agent=agent_version, host=host_version or VERSION),
                [("update", _("Mettre à jour"),
                  on_update or (lambda: open_companion("diagnose", fix="update-agent")))],
                key="old-agent")
