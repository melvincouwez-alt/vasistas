"""Notifications de Windows (Outlook, Teams, mises à jour d'Office…) en notifications du bureau.

L'agent lit les bannières de Windows et les envoie (`notify`) ; chacune devient une
notification système au nom et à l'icône de l'application Windows (indice desktop-entry :
le lanceur Vasistas de l'appli). Cliquer la bulle ouvre la bannière dans Windows
(`notify.activate`), donc l'application sur le bon message.
"""

import logging

from gi.repository import Gio, GLib

from .i18n import _

log = logging.getLogger(__name__)

BUS = "org.freedesktop.Notifications"
PATH = "/org/freedesktop/Notifications"


class WindowsNotifications:
    def __init__(self, send):
        self.send = send
        self.by_id = {}    # identifiant de la bulle -> identifiant de la bannière Windows
        self.bus = None
        try:
            self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
            self.bus.signal_subscribe(BUS, BUS, "ActionInvoked", PATH, None, Gio.DBusSignalFlags.NONE,
                                      self._on_action)
            self.bus.signal_subscribe(BUS, BUS, "NotificationClosed", PATH, None, Gio.DBusSignalFlags.NONE,
                                      self._on_closed)
        except GLib.Error as e:
            log.warning("notifications indisponibles : %s", e.message)

    def show(self, msg):
        if self.bus is None:
            return
        from . import desktop
        app = msg.get("app") or ""
        name = msg.get("appName") or app or "Windows"
        hints = {"urgency": GLib.Variant("y", 1)}
        icon = ""
        if app:
            hints["desktop-entry"] = GLib.Variant("s", desktop.app_desktop_id(app))
            if desktop.app_icon_path(app).exists():
                icon = desktop.app_icon_name(app)
        params = GLib.Variant("(susssasa{sv}i)", (
            name, 0, icon, msg.get("title") or name, msg.get("body") or "",
            ["default", _("Ouvrir")], hints, -1))

        def done(conn, res):
            try:
                nid = conn.call_finish(res).unpack()[0]
                self.by_id[nid] = msg.get("id")
            except GLib.Error as e:
                log.warning("notification de Windows : %s", e.message)
        self.bus.call(BUS, PATH, BUS, "Notify", params, GLib.VariantType("(u)"),
                      Gio.DBusCallFlags.NONE, 2000, None, done)

    def _on_action(self, conn, sender, path, iface, signal, params):
        nid, action = params.unpack()
        if nid in self.by_id:
            self.send({"t": "notify.activate", "id": self.by_id.pop(nid)})

    def _on_closed(self, conn, sender, path, iface, signal, params):
        self.by_id.pop(params.unpack()[0], None)
