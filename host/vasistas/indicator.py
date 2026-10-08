"""Indicateur dans le panneau : état de Windows, applications récentes, commandes de la VM.

Processus à part (`vasistas indicator`), une seule instance par session (nom D-Bus
APP_ID.Indicator), lancé à l'ouverture de session si l'option `indicator` de config.json est
vraie (par défaut). Écrit avec Gio.DBus seulement, sans GTK : le panneau dessine l'icône et le
menu lui-même. Protocoles :
- org.kde.StatusNotifierItem (objet /StatusNotifierItem), inscrit auprès de
  org.kde.StatusNotifierWatcher, et réinscrit quand le panneau redémarre ;
- com.canonical.dbusmenu (objet /MenuBar) pour le menu. Les identifiants des entrées sont
  renumérotés à chaque reconstruction ; LayoutUpdated prévient le panneau.

État : vm.pid() (QEMU tourne) et la requête `status` du socket de contrôle, qui ne compte pas
comme un usage (Windows en veille y reste). Relu toutes les 5 s quand QEMU tourne, toutes les
2 s menu ouvert, au démarrage ou à l'arrêt. Windows arrêté, rien n'est relu : un moniteur sur
le dossier de données signale qemu.pid, control.sock et config.json. Sans l'hôte `vasistas run`,
l'indicateur marche quand même (Windows arrêté ou en démarrage).
"""

import json
import logging
import os
import threading
import time

from gi.repository import Gio, GLib

from . import control, vm, winctl
from .i18n import _
from .winctl import APP_ID

log = logging.getLogger(__name__)

BUS_NAME = f"{APP_ID}.Indicator"
ITEM_PATH = "/StatusNotifierItem"
MENU_PATH = "/MenuBar"
WATCHER = "org.kde.StatusNotifierWatcher"
ICON_DIR = winctl.HOST_DIR.parent / "data" / "icons"
AUTOSTART = os.path.expanduser(f"~/.config/autostart/{APP_ID}.indicator.autostart.desktop")
APPS_DIR = os.path.expanduser("~/.local/share/applications")
RECENT = vm.DATA / "indicator-recent.json"
MAX_APPS = 6
POLL_FAST_S = 2
POLL_S = 5
BUSY_MAX_S = 300  # action restée sans effet visible : on cesse d'attendre
MENU_MAX_S = 120  # menu ouvert sans « closed » (panneau relancé) : on revient au rythme lent
WATCHED = {"qemu.pid", "control.sock", "config.json"}

ITEM_XML = """<node><interface name="org.kde.StatusNotifierItem">
<property name="Category" type="s" access="read"/>
<property name="Id" type="s" access="read"/>
<property name="Title" type="s" access="read"/>
<property name="Status" type="s" access="read"/>
<property name="WindowId" type="i" access="read"/>
<property name="IconThemePath" type="s" access="read"/>
<property name="IconName" type="s" access="read"/>
<property name="IconPixmap" type="a(iiay)" access="read"/>
<property name="OverlayIconName" type="s" access="read"/>
<property name="OverlayIconPixmap" type="a(iiay)" access="read"/>
<property name="AttentionIconName" type="s" access="read"/>
<property name="AttentionIconPixmap" type="a(iiay)" access="read"/>
<property name="AttentionMovieName" type="s" access="read"/>
<property name="ToolTip" type="(sa(iiay)ss)" access="read"/>
<property name="ItemIsMenu" type="b" access="read"/>
<property name="Menu" type="o" access="read"/>
<method name="ContextMenu"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
<method name="Activate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
<method name="SecondaryActivate"><arg name="x" type="i" direction="in"/><arg name="y" type="i" direction="in"/></method>
<method name="Scroll"><arg name="delta" type="i" direction="in"/><arg name="orientation" type="s" direction="in"/></method>
<signal name="NewTitle"/><signal name="NewIcon"/><signal name="NewAttentionIcon"/>
<signal name="NewOverlayIcon"/><signal name="NewToolTip"/>
<signal name="NewStatus"><arg name="status" type="s"/></signal>
</interface></node>"""

MENU_XML = """<node><interface name="com.canonical.dbusmenu">
<property name="Version" type="u" access="read"/>
<property name="TextDirection" type="s" access="read"/>
<property name="Status" type="s" access="read"/>
<property name="IconThemePath" type="as" access="read"/>
<method name="GetLayout"><arg type="i" direction="in"/><arg type="i" direction="in"/><arg type="as" direction="in"/>
<arg type="u" direction="out"/><arg type="(ia{sv}av)" direction="out"/></method>
<method name="GetGroupProperties"><arg type="ai" direction="in"/><arg type="as" direction="in"/>
<arg type="a(ia{sv})" direction="out"/></method>
<method name="GetProperty"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="out"/></method>
<method name="Event"><arg type="i" direction="in"/><arg type="s" direction="in"/><arg type="v" direction="in"/>
<arg type="u" direction="in"/></method>
<method name="EventGroup"><arg type="a(isvu)" direction="in"/><arg type="ai" direction="out"/></method>
<method name="AboutToShow"><arg type="i" direction="in"/><arg type="b" direction="out"/></method>
<method name="AboutToShowGroup"><arg type="ai" direction="in"/><arg type="ai" direction="out"/>
<arg type="ai" direction="out"/></method>
<signal name="ItemsPropertiesUpdated"><arg type="a(ia{sv})"/><arg type="a(ias)"/></signal>
<signal name="LayoutUpdated"><arg type="u"/><arg type="i"/></signal>
<signal name="ItemActivationRequested"><arg type="i"/><arg type="u"/></signal>
</interface></node>"""


# -- réglage et démarrage automatique --

def enabled():
    try:
        return vm.load_config().get("indicator", True) is not False
    except ValueError:  # config.json en cours d'écriture
        return True


def sync_autostart():
    """Lanceur de session présent si l'option est active, absent sinon."""
    if enabled():
        from .desktop import _launcher
        os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
        with open(AUTOSTART, "w") as f:
            f.write("[Desktop Entry]\nType=Application\nName=Vasistas\n"
                    f"Exec={_launcher()} indicator\nIcon={APP_ID}\nNoDisplay=true\n"
                    "X-GNOME-Autostart-enabled=true\n")
    elif os.path.exists(AUTOSTART):
        os.unlink(AUTOSTART)


def set_enabled(on):
    """Interrupteur du compagnon : lance l'indicateur tout de suite ; éteint, celui qui tourne
    voit config.json changer et s'en va."""
    c = vm.load_config()
    c["indicator"] = bool(on)
    vm.save_config(c)
    sync_autostart()
    if on:
        winctl.spawn("indicator")


# -- état et menu (logique pure, testée dans tests/test_indicator.py) --

def phase(state):
    """stopped, starting, stopping, ready ou sleeping, d'après {pid, host, busy}."""
    busy = state.get("busy")
    host = state.get("host") or {}
    if busy == "stop":
        return "stopping"
    if busy in ("start", "restart"):
        return "starting"
    if not state.get("pid"):
        return "stopped"
    if not host.get("guest_ready"):
        return "starting"
    if host.get("paused"):
        return "sleeping"
    return "ready"


def settle(busy, state):
    """Action en cours terminée ? Rend la nouvelle valeur de busy (None une fois l'effet visible)."""
    running = bool(state.get("pid"))
    ready = bool((state.get("host") or {}).get("guest_ready"))
    if busy in ("start", "restart") and running and ready:
        return None
    if busy == "stop" and not running:
        return None
    return busy


def status_text(state):
    p = phase(state)
    if p == "stopped":
        return _("Windows est arrêté")
    if p == "starting":
        return _("Windows démarre…")
    if p == "stopping":
        return _("Windows s'arrête…")
    if p == "sleeping":
        return _("Windows est en veille")
    n = (state.get("host") or {}).get("windows", 0)
    if not n:
        return _("Windows est prêt")
    if n == 1:
        return _("Windows est prêt · 1 fenêtre")
    return _("Windows est prêt · {n} fenêtres", n=n)


def icon_name(p):
    """Icône symbolique de l'état (data/icons/hicolor/symbolic/apps)."""
    return f"{APP_ID}-{'starting' if p == 'stopping' else p}-symbolic"


def menu_apps(open_apps, recent, names, limit=MAX_APPS):
    """Applications du menu : ouvertes d'abord, puis récentes. [(id, nom)]"""
    seen = []
    for app in list(open_apps) + list(recent):
        if app and app not in seen:
            seen.append(app)
    return [(app, names.get(app) or app) for app in seen[:limit]]


SEPARATOR = {"type": "separator"}


def build_menu(state, apps, power, icons=None):
    """Entrées du menu : [{label, action, enabled, icon, toggle, checked, children} | SEPARATOR].
    `apps` : [(id, nom)] ; `power` : clé de vm.RESOURCES choisie ; `icons` : {id: icône}."""
    icons = icons or {}
    p = phase(state)
    items = [{"label": status_text(state), "enabled": False}]
    if apps:
        items.append(SEPARATOR)
        items += [{"label": name, "action": ("launch", app), "icon": icons.get(app)} for app, name in apps]
    items.append(SEPARATOR)
    if p == "stopped":
        items.append({"label": _("Démarrer Windows"), "action": ("start",)})
    if p == "ready":
        items.append({"label": _("Mettre Windows en veille"), "action": ("sleep",)})
    if p == "sleeping":
        items.append({"label": _("Réveiller Windows"), "action": ("wake",)})
    if p in ("starting", "ready", "sleeping"):
        items.append({"label": _("Arrêter Windows"), "action": ("stop",)})
    if p == "ready":
        items.append({"label": _("Réinitialiser les affichages"), "action": ("reset",)})
    choices = [{"label": _(label), "action": ("power", key), "toggle": "radio", "checked": key == power}
               for key, label, _detail in winctl.RESOURCES]
    if state.get("pid"):
        choices += [SEPARATOR, {"label": _("S'applique au prochain démarrage de Windows"), "enabled": False}]
    items.append({"label": _("Puissance de Windows"), "children": choices})
    items.append(SEPARATOR)
    items.append({"label": _("Ouvrir Vasistas"), "action": ("companion",)})
    items.append({"label": _("Quitter l'indicateur"), "action": ("quit",)})
    return items


def layout(items):
    """Arbre dbusmenu numéroté : (racine, {id: action}). Nœud = (id, {propriété: Variant}, [nœuds])."""
    actions = {}
    counter = [0]

    def node(entry):
        counter[0] += 1
        nid = counter[0]
        props = {}
        if entry.get("type") == "separator":
            props["type"] = GLib.Variant("s", "separator")
            return nid, props, []
        # « _ » marque un raccourci clavier dans dbusmenu : doublé pour s'afficher tel quel
        props["label"] = GLib.Variant("s", entry.get("label", "").replace("_", "__"))
        if entry.get("enabled") is False:
            props["enabled"] = GLib.Variant("b", False)
        if entry.get("icon"):
            props["icon-name"] = GLib.Variant("s", entry["icon"])
        if entry.get("toggle"):
            props["toggle-type"] = GLib.Variant("s", entry["toggle"])
            props["toggle-state"] = GLib.Variant("i", 1 if entry.get("checked") else 0)
        if entry.get("action"):
            actions[nid] = entry["action"]
        children = [node(c) for c in entry.get("children", [])]
        if children:
            props["children-display"] = GLib.Variant("s", "submenu")
        return nid, props, children

    root = (0, {"children-display": GLib.Variant("s", "submenu")}, [node(e) for e in items])
    return root, actions


def node_variant(n, depth=-1, names=()):
    """Nœud au format (ia{sv}av), enfants jusqu'à `depth` niveaux (-1 : tous)."""
    nid, props, children = n
    if names:
        props = {k: v for k, v in props.items() if k in names}
    kids = [] if depth == 0 else [GLib.Variant("(ia{sv}av)", node_variant(c, depth - 1, names))
                                  for c in children]
    return nid, props, kids


def find_node(n, nid):
    if n[0] == nid:
        return n
    for c in n[2]:
        found = find_node(c, nid)
        if found:
            return found
    return None


def all_nodes(n):
    yield n
    for c in n[2]:
        yield from all_nodes(c)


def signature(n):
    """Contenu d'un arbre, pour ne prévenir le panneau que s'il a changé."""
    nid, props, children = n
    return nid, sorted((k, v.print_(False)) for k, v in props.items()), [signature(c) for c in children]


# -- applications (lanceurs et registre de desktop.py, lus sans charger GTK) --

def _launcher_info(app):
    """Nom, icône et visibilité dans le menu Applications d'après le lanceur de l'application."""
    path = os.path.join(APPS_DIR, f"{APP_ID}.{app}".lower() + ".desktop")
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    info = {"visible": True}
    for line in lines:
        key, sep, value = line.partition("=")
        if not sep:
            continue
        if key == "Name":
            info["name"] = value
        elif key == "Icon":
            info["icon"] = value
        elif key == "NoDisplay":
            info["visible"] = value != "true"
    return info


def _registry():
    """Applications vues dans l'invité (desktop.load_registry, sans importer desktop/GTK)."""
    try:
        return json.loads((vm.DATA / "apps.json").read_text())
    except (OSError, ValueError):
        return {}


def load_recent():
    try:
        return [a for a in json.loads(RECENT.read_text()) if isinstance(a, str)]
    except (OSError, ValueError, TypeError):
        return []


def save_recent(apps):
    try:
        RECENT.parent.mkdir(parents=True, exist_ok=True)
        RECENT.write_text(json.dumps(apps[:20]))
    except OSError:
        pass


class Indicator:
    def __init__(self, bus, quit_cb):
        self.bus = bus
        self.quit_cb = quit_cb
        self.state = {}
        self.busy = None
        self.busy_since = 0
        self.open_apps = []
        self.windows_seen = None
        self.recent = load_recent()
        self.menu_open = False
        self.menu_since = 0
        self.shown_key = None  # état déjà montré par le menu (voir _apply)
        self.revision = 1
        self.root, self.actions = layout([])
        self.icon = icon_name("stopped")
        self.tooltip = ""
        self.timer = 0
        self.refreshing = False
        self.again = False
        self.item_name = f"org.kde.StatusNotifierItem-{os.getpid()}-1"
        self.monitor = None
        self.debounce = 0

    # -- mise en place --

    def start(self):
        """Faux si un autre indicateur tourne déjà dans la session."""
        if self._request_name(BUS_NAME) != 1:
            log.info("indicateur déjà lancé")
            return False
        self._request_name(self.item_name)
        item = Gio.DBusNodeInfo.new_for_xml(ITEM_XML).interfaces[0]
        menu = Gio.DBusNodeInfo.new_for_xml(MENU_XML).interfaces[0]
        self.bus.register_object_with_closures2(ITEM_PATH, item, self._on_item_call, self._item_property, None)
        self.bus.register_object_with_closures2(MENU_PATH, menu, self._on_menu_call, self._menu_property, None)
        # le panneau peut démarrer après nous, ou redémarrer : on s'inscrit à chaque apparition
        Gio.bus_watch_name_on_connection(self.bus, WATCHER, Gio.BusNameWatcherFlags.NONE,
                                         self._on_watcher, None)
        vm.ensure_data()
        self.monitor = Gio.File.new_for_path(str(vm.DATA)).monitor_directory(Gio.FileMonitorFlags.NONE, None)
        self.monitor.connect("changed", self._on_data_changed)
        self.rebuild()
        self.refresh()
        return True

    def _request_name(self, name):
        # DO_NOT_QUEUE : 1 = nom obtenu, 3 = déjà pris
        res = self.bus.call_sync("org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus",
                                 "RequestName", GLib.Variant("(su)", (name, 4)), GLib.VariantType("(u)"),
                                 Gio.DBusCallFlags.NONE, -1, None)
        return res.unpack()[0]

    def _on_watcher(self, conn, name, owner):
        conn.call(WATCHER, "/StatusNotifierWatcher", WATCHER, "RegisterStatusNotifierItem",
                  GLib.Variant("(s)", (self.item_name,)), None, Gio.DBusCallFlags.NONE, 3000, None,
                  self._on_registered)

    def _on_registered(self, conn, res):
        try:
            conn.call_finish(res)
            log.info("indicateur inscrit auprès du panneau")
        except GLib.Error as e:
            log.warning("inscription auprès du panneau : %s", e.message)

    def _on_data_changed(self, _mon, file, _other, event):
        if file.get_basename() not in WATCHED or event not in (
                Gio.FileMonitorEvent.CREATED, Gio.FileMonitorEvent.DELETED, Gio.FileMonitorEvent.CHANGES_DONE_HINT):
            return
        if file.get_basename() == "config.json" and not enabled():
            log.info("indicateur désactivé dans le compagnon")
            self.quit_cb()
            return
        if not self.debounce:
            self.debounce = GLib.timeout_add(300, self._debounced)

    def _debounced(self):
        self.debounce = 0
        self.rebuild()
        self.refresh()
        return False

    # -- état --

    def refresh(self):
        """Relit l'état dans un fil (le socket peut tarder), puis l'applique dans la boucle."""
        if self.refreshing:
            self.again = True
            return
        self.refreshing = True
        want_windows = self.menu_open
        seen = self.windows_seen

        def work():
            state = {"pid": vm.pid(), "host": winctl.host_ready()}
            host = state["host"] or {}
            open_apps = None
            # liste des fenêtres : seulement si leur nombre a changé ou menu ouvert ; ne réveille rien
            if host.get("guest_ready") and host.get("windows") and (want_windows or host.get("windows") != seen):
                try:
                    res = control.request({"windows": True}, timeout=1)
                    open_apps = [w.get("app") for w in res.get("windows", []) if w.get("app")]
                except (OSError, ValueError):
                    pass
            elif not host.get("windows"):
                open_apps = []
            GLib.idle_add(self._apply, state, open_apps)
        threading.Thread(target=work, daemon=True).start()

    def _apply(self, state, open_apps):
        self.refreshing = False
        if self.busy and time.monotonic() - self.busy_since > BUSY_MAX_S:
            self.busy = None
        self.busy = settle(self.busy, state)
        state["busy"] = self.busy
        self.state = state
        self.windows_seen = (state.get("host") or {}).get("windows")
        if open_apps is not None:
            self.open_apps = list(dict.fromkeys(open_apps))
            self.remember(self.open_apps)
        # relecture toutes les 5 s VM en marche : menu, icône et infobulle ne dépendent que de
        # ceci ; les lanceurs et apps.json sont relus à l'ouverture du menu (opened)
        key = (phase(state), (state.get("host") or {}).get("windows"), bool(state.get("pid")), tuple(self.open_apps))
        if self.menu_open or key != self.shown_key:
            self.shown_key = key
            self.rebuild()
        self.schedule()
        if self.again:
            self.again = False
            self.refresh()
        return False

    def remember(self, apps):
        recent = list(apps) + [a for a in self.recent if a not in apps]
        if recent != self.recent:
            self.recent = recent
            save_recent(recent)

    def schedule(self):
        """Prochaine lecture : rapide menu ouvert ou action en cours, lente QEMU lancé, aucune sinon."""
        p = phase(self.state)
        if self.menu_open and time.monotonic() - self.menu_since > MENU_MAX_S:
            self.menu_open = False
        delay = POLL_FAST_S if (self.menu_open or self.busy or p in ("starting", "stopping")) else \
            POLL_S if self.state.get("pid") else 0
        if self.timer:
            GLib.source_remove(self.timer)
            self.timer = 0
        if delay:
            self.timer = GLib.timeout_add_seconds(delay, self._tick)

    def _tick(self):
        self.timer = 0
        self.refresh()
        return False

    def apps(self):
        """[(id, nom)] et {id: icône} pour le menu."""
        reg = _registry()
        recent = self.recent
        if not recent and not self.open_apps:
            # pas encore d'historique : les applications montrées dans le menu Applications
            recent = sorted((a for a in reg if (_launcher_info(a) or {}).get("visible")),
                            key=lambda a: (reg[a].get("name") or a).lower())
        names, icons = {}, {}
        for app in list(self.open_apps) + recent[:MAX_APPS * 2]:
            info = _launcher_info(app) or {}
            names[app] = info.get("name") or (reg.get(app) or {}).get("name") or app
            if info.get("icon"):
                icons[app] = info["icon"]
        return menu_apps(self.open_apps, recent, names), icons

    def rebuild(self):
        apps, icons = self.apps()
        try:
            power = winctl.resources()
        except ValueError:  # config.json en cours d'écriture
            power = None
        root, actions = layout(build_menu(self.state, apps, power, icons))
        if signature(root) != signature(self.root):
            self.root, self.actions = root, actions
            self.revision += 1
            self._emit(MENU_PATH, "com.canonical.dbusmenu", "LayoutUpdated",
                       GLib.Variant("(ui)", (self.revision, 0)))
        else:
            self.actions = actions
        icon = icon_name(phase(self.state))
        if icon != self.icon:
            self.icon = icon
            self._emit(ITEM_PATH, "org.kde.StatusNotifierItem", "NewIcon", None)
        tooltip = status_text(self.state)
        if tooltip != self.tooltip:
            self.tooltip = tooltip
            self._emit(ITEM_PATH, "org.kde.StatusNotifierItem", "NewToolTip", None)

    def _emit(self, path, iface, name, params):
        try:
            self.bus.emit_signal(None, path, iface, name, params)
        except GLib.Error as e:
            log.warning("signal %s : %s", name, e.message)

    # -- D-Bus : StatusNotifierItem --

    def _item_property(self, conn, sender, path, iface, name):
        values = {
            "Category": GLib.Variant("s", "ApplicationStatus"),
            "Id": GLib.Variant("s", "vasistas"),
            "Title": GLib.Variant("s", "Vasistas"),
            "Status": GLib.Variant("s", "Active"),
            "WindowId": GLib.Variant("i", 0),
            "IconThemePath": GLib.Variant("s", str(ICON_DIR)),
            "IconName": GLib.Variant("s", self.icon),
            "OverlayIconName": GLib.Variant("s", ""),
            "AttentionIconName": GLib.Variant("s", ""),
            "AttentionMovieName": GLib.Variant("s", ""),
            "ToolTip": GLib.Variant("(sa(iiay)ss)", (self.icon, [], "Vasistas", self.tooltip)),
            "ItemIsMenu": GLib.Variant("b", True),
            "Menu": GLib.Variant("o", MENU_PATH),
        }
        return values.get(name) or GLib.Variant("a(iiay)", [])

    def _on_item_call(self, conn, sender, path, iface, method, params, invocation):
        if method == "Activate":
            self.run_action(("companion",))
        invocation.return_value(None)

    # -- D-Bus : com.canonical.dbusmenu --

    def _menu_property(self, conn, sender, path, iface, name):
        return {
            "Version": GLib.Variant("u", 3),
            "TextDirection": GLib.Variant("s", "ltr"),
            "Status": GLib.Variant("s", "normal"),
        }.get(name) or GLib.Variant("as", [])

    def _on_menu_call(self, conn, sender, path, iface, method, params, invocation):
        args = params.unpack()
        if method == "GetLayout":
            parent, depth, names = args
            n = find_node(self.root, parent)
            if n is None:
                invocation.return_dbus_error("com.canonical.dbusmenu.Error.UnknownId", f"entrée {parent}")
                return
            invocation.return_value(GLib.Variant("(u(ia{sv}av))", (self.revision, node_variant(n, depth, names))))
        elif method == "GetGroupProperties":
            ids, names = args
            out = [(n[0], node_variant(n, 0, names)[1]) for n in all_nodes(self.root) if not ids or n[0] in ids]
            invocation.return_value(GLib.Variant("(a(ia{sv}))", (out,)))
        elif method == "GetProperty":
            n = find_node(self.root, args[0])
            if n is None or args[1] not in n[1]:
                invocation.return_dbus_error("com.canonical.dbusmenu.Error.UnknownId", f"{args[0]} {args[1]}")
                return
            invocation.return_value(GLib.Variant("(v)", (n[1][args[1]],)))
        elif method == "Event":
            self.on_event(args[0], args[1])
            invocation.return_value(None)
        elif method == "EventGroup":
            errors = [e[0] for e in args[0] if find_node(self.root, e[0]) is None]
            for e in args[0]:
                self.on_event(e[0], e[1])
            invocation.return_value(GLib.Variant("(ai)", (errors,)))
        elif method == "AboutToShow":
            if args[0] == 0:
                self.opened()
            invocation.return_value(GLib.Variant("(b)", (False,)))
        elif method == "AboutToShowGroup":
            if 0 in args[0]:
                self.opened()
            invocation.return_value(GLib.Variant("(aiai)", ([], [])))
        else:
            invocation.return_value(None)

    def opened(self):
        """Menu ouvert : état relu tout de suite, puis toutes les 2 s jusqu'à sa fermeture."""
        self.menu_open = True
        self.menu_since = time.monotonic()
        self.rebuild()  # applications ajoutées depuis : lanceurs relus avant l'affichage
        self.refresh()

    def on_event(self, nid, kind):
        if kind == "opened" and nid == 0:
            self.opened()
        elif kind == "closed" and nid == 0:
            self.menu_open = False
            self.schedule()
        elif kind == "clicked" and nid in self.actions:
            self.run_action(self.actions[nid])

    # -- actions --

    def run_action(self, action):
        name, *rest = action
        log.info("action %s", " ".join(action))
        if name == "launch":
            winctl.spawn("launch-app", rest[0])
            self.remember([rest[0]])
            self.rebuild()
        elif name in ("start", "stop"):
            self.busy, self.busy_since = name, time.monotonic()
            self.state["busy"] = name
            self.rebuild()
            self.schedule()
            self._in_thread(getattr(winctl, name), reset_busy=True)
        elif name in ("sleep", "wake"):
            self._in_thread(getattr(winctl, name))
        elif name == "reset":
            self._in_thread(self._reset_windows)
        elif name == "power":
            winctl.set_resources(rest[0])
            self.rebuild()
        elif name == "companion":
            winctl.spawn("companion")
        elif name == "quit":
            self.quit_cb()

    def _in_thread(self, fn, reset_busy=False):
        def work():
            try:
                fn()
            except Exception as e:  # noqa: BLE001 - affiché à l'utilisateur
                log.warning("échec : %s", e)
                GLib.idle_add(self.notify, _("Échec : {error}", error=e))
                if reset_busy:
                    GLib.idle_add(setattr, self, "busy", None)
            GLib.idle_add(self.refresh)
        threading.Thread(target=work, daemon=True).start()

    @staticmethod
    def _reset_windows():
        """Recrée les fenêtres de l'hôte (requête reset_windows de app.py)."""
        res = control.request({"reset_windows": True}, timeout=10)
        if res.get("error"):
            raise RuntimeError(res["error"])

    def notify(self, text):
        self.bus.call("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                      "org.freedesktop.Notifications", "Notify",
                      GLib.Variant("(susssasa{sv}i)", ("Vasistas", 0, APP_ID, "Vasistas", text, [], {}, -1)),
                      None, Gio.DBusCallFlags.NONE, 3000, None, None)
        return False


def main():
    loop = GLib.MainLoop()
    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    indicator = Indicator(bus, loop.quit)
    if not indicator.start():
        return 0
    loop.run()
    return 0
