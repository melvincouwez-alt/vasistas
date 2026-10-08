"""Icônes de la zone de notification de Windows (OneDrive, Office…) dans le panneau du bureau.

L'agent envoie la liste des icônes (`tray`) à chaque changement ; chacune devient un
StatusNotifierItem (org.kde.StatusNotifierItem) sur le bus de session, que le panneau affiche
comme les autres icônes de notification. Un clic part à Windows (`tray.click`) : le menu de
l'application s'ouvre dans Windows et arrive comme une fenêtre de l'invité.
"""

import base64
import logging
import os

from gi.repository import Gio, GLib

log = logging.getLogger(__name__)

WATCHER = "org.kde.StatusNotifierWatcher"
ITEM_IFACE = "org.kde.StatusNotifierItem"
ITEM_XML = """
<node>
  <interface name="org.kde.StatusNotifierItem">
    <property name="Category" type="s" access="read"/>
    <property name="Id" type="s" access="read"/>
    <property name="Title" type="s" access="read"/>
    <property name="Status" type="s" access="read"/>
    <property name="IconName" type="s" access="read"/>
    <property name="IconPixmap" type="a(iiay)" access="read"/>
    <property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
    <property name="ItemIsMenu" type="b" access="read"/>
    <property name="Menu" type="o" access="read"/>
    <method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
    <method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
    <signal name="NewIcon"/>
    <signal name="NewToolTip"/>
    <signal name="NewTitle"/>
  </interface>
</node>"""


def png_to_pixmap(png):
    """PNG -> (largeur, hauteur, octets ARGB32 gros-boutistes) attendus par IconPixmap."""
    from gi.repository import GdkPixbuf
    loader = GdkPixbuf.PixbufLoader.new_with_type("png")
    loader.write(png)
    loader.close()
    pb = loader.get_pixbuf()
    if not pb.get_has_alpha():
        pb = pb.add_alpha(False, 0, 0, 0)
    return pixmap_from_rgba(pb.get_width(), pb.get_height(), pb.get_rowstride(), pb.get_pixels())


def pixmap_from_rgba(w, h, stride, data):
    """RGBA (lignes de `stride` octets) -> ARGB serré, par tranches plutôt que pixel par pixel."""
    src = b"".join(data[y * stride:y * stride + w * 4] for y in range(h))
    out = bytearray(len(src))
    out[0::4], out[1::4], out[2::4], out[3::4] = src[3::4], src[0::4], src[1::4], src[2::4]
    return w, h, bytes(out)


class TrayItem:
    def __init__(self, bus, n, entry, click):
        self.bus = bus
        self.key = entry["key"]
        self.click = click
        self.tooltip = entry.get("tooltip") or ""
        self.pixmaps = []
        self.png = None
        self.update(entry, emit=False)
        self.name = f"org.kde.StatusNotifierItem-{os.getpid()}-{n}"
        self.path = "/StatusNotifierItem"
        info = Gio.DBusNodeInfo.new_for_xml(ITEM_XML).interfaces[0]
        # connexion à part : un objet /StatusNotifierItem par nom de bus
        self.conn = Gio.DBusConnection.new_for_address_sync(
            Gio.dbus_address_get_for_bus_sync(Gio.BusType.SESSION, None),
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION, None, None)
        self.reg = self.conn.register_object(self.path, info, self._on_call, self._on_property, None)
        self.owner = Gio.bus_own_name_on_connection(self.conn, self.name, Gio.BusNameOwnerFlags.NONE, None, None)
        self._register()

    def _register(self):
        self.conn.call(WATCHER, "/StatusNotifierWatcher", WATCHER, "RegisterStatusNotifierItem",
                       GLib.Variant("(s)", (self.name,)), None, Gio.DBusCallFlags.NONE, 2000, None,
                       lambda c, r: self._registered(c, r))

    def _registered(self, conn, res):
        try:
            conn.call_finish(res)
        except GLib.Error as e:
            log.debug("icône %s : pas de panneau (%s)", self.key, e.message)

    def update(self, entry, emit=True):
        tooltip = entry.get("tooltip") or ""
        pixmaps = self.pixmaps
        if entry.get("png") != self.png:  # la liste arrive entière : ne décoder que ce qui change
            self.png = entry.get("png")
            pixmaps = []
            if self.png:
                try:
                    pixmaps = [png_to_pixmap(base64.b64decode(self.png))]
                except (GLib.Error, ValueError) as e:
                    log.debug("icône %s illisible : %s", self.key, e)
        changed_icon = pixmaps != self.pixmaps
        changed_tip = tooltip != self.tooltip
        self.pixmaps, self.tooltip = pixmaps, tooltip
        if emit:
            if changed_icon:
                self._emit("NewIcon")
            if changed_tip:
                self._emit("NewToolTip")
                self._emit("NewTitle")

    def _emit(self, signal):
        try:
            self.conn.emit_signal(None, self.path, ITEM_IFACE, signal, None)
        except GLib.Error:
            pass

    def _on_property(self, conn, sender, path, iface, name):
        pix = GLib.Variant("a(iiay)", [(w, h, data) for w, h, data in self.pixmaps])
        return {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "vasistas-" + self.key),
            "Title": GLib.Variant("s", self.tooltip),
            "Status": GLib.Variant("s", "Active"),
            "IconName": GLib.Variant("s", "" if self.pixmaps else "io.github.melvincouwez.Vasistas"),
            "IconPixmap": pix,
            "ToolTip": GLib.Variant("(sa(iiay)ss)", ("", [], self.tooltip, "")),
            "ItemIsMenu": GLib.Variant("b", False),
            "Menu": GLib.Variant("o", "/NO_DBUSMENU"),
        }.get(name)

    def _on_call(self, conn, sender, path, iface, method, params, invocation):
        if method == "Activate":
            self.click(self.key, "left")
        elif method in ("ContextMenu", "SecondaryActivate"):
            self.click(self.key, "right")
        invocation.return_value(None)

    def close(self):
        try:
            Gio.bus_unown_name(self.owner)
            self.conn.unregister_object(self.reg)
            self.conn.close_sync(None)
        except GLib.Error:
            pass


class WindowsTray:
    """Ensemble des icônes, tenu à jour d'après les messages `tray` de l'agent."""

    def __init__(self, send):
        self.send = send
        self.items = {}
        self.n = 0

    def update(self, entries):
        keys = {e["key"] for e in entries if e.get("key")}
        for key in [k for k in self.items if k not in keys]:
            self.items.pop(key).close()
        for entry in entries:
            key = entry.get("key")
            if not key:
                continue
            if key in self.items:
                self.items[key].update(entry)
            else:
                self.n += 1
                try:
                    self.items[key] = TrayItem(None, self.n, entry, self._click)
                except GLib.Error as e:
                    log.warning("icône de Windows %s : %s", key, e.message)

    def _click(self, key, button):
        self.send({"t": "tray.click", "key": key, "button": button, "x": 0, "y": 0})

    def clear(self):
        for item in self.items.values():
            item.close()
        self.items.clear()
