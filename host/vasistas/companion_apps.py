"""Page « Menu Applications » de l'application compagnon : applications Windows installées,
choix de celles qui apparaissent dans le menu, icônes, version d'Outlook."""

import base64
import threading

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Granite, Gtk

from . import desktop, guestapps
from .companion_common import Page, card, clear, guest_ready, spawn, texture_from_b64

VARIANT_LABELS = {"classic": "Classique", "new": "Nouveau"}
# applications sans intérêt dans le menu
HIDDEN = {"msoev", "setlang"}


class AppsPage(Page):
    __gtype_name__ = "VasistasAppsPage"

    def __init__(self, win):
        super().__init__("applications-other", "Menu Applications",
                         "Les applications Windows à afficher dans le menu Applications, avec leur icône.")
        self.win = win
        self.rows = []
        self.refresh_btn = Gtk.Button(label="Relire la liste")
        self.refresh_btn.set_tooltip_text("Relire dans Windows la liste des applications installées")
        self.refresh_btn.connect("clicked", lambda *_: self.refresh())
        self.get_action_area().append(self.refresh_btn)
        self.search = Gtk.SearchEntry(placeholder_text="Rechercher une application")
        self.search.connect("search-changed", lambda *_: self.filter())
        self.add(self.search)
        self.list_box = self.add(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6))

        group = Gio.SimpleActionGroup()
        for name, handler in (("open", self.on_open), ("icon", self.on_icon), ("reset-icon", self.on_reset_icon)):
            act = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            act.connect("activate", lambda _a, param, h=handler: h(param.get_string()))
            group.add_action(act)
        self.insert_action_group("apps", group)
        self.fill(guestapps.cached())

    def update(self, state):
        self.refresh_btn.set_sensitive(guest_ready(state))

    def refresh(self):
        self.refresh_btn.set_sensitive(False)
        self.win.notify("Lecture des applications dans Windows…")

        def work():
            try:
                apps = guestapps.fetch()
                GLib.idle_add(self.fill, apps)
                GLib.idle_add(self.win.notify, f"{len(apps)} applications trouvées")
            except Exception as e:  # noqa: BLE001
                GLib.idle_add(self.win.notify, f"Lecture impossible : {e}")
            GLib.idle_add(self.refresh_btn.set_sensitive, True)
        threading.Thread(target=work, daemon=True).start()

    def fill(self, apps):
        clear(self.list_box)
        self.rows = []
        if not apps:
            empty = Granite.Placeholder(title="Aucune application lue",
                                        description="Démarrez Windows, puis cliquez sur « Relire la liste ».",
                                        icon=Gio.ThemedIcon.new("applications-other"))
            self.list_box.append(empty)
            return False
        for cat, title in guestapps.CATEGORIES.items():
            items = [a for a in apps if a["category"] == cat and a["id"] not in HIDDEN]
            if not items:
                continue
            label = Granite.HeaderLabel.new(title)
            self.list_box.append(label)
            lb = card()
            for a in items:
                r = self.app_row(a)
                lb.append(r)
                self.rows.append((r, a, lb, label))
            self.list_box.append(lb)
        self.filter()
        return False

    def app_row(self, a):
        app = a["id"]
        name = "Outlook" if app == "outlook" else a["name"]
        box = Gtk.Box(spacing=12, margin_start=6, margin_end=6, margin_top=4, margin_bottom=4)
        icon = Gtk.Image(pixel_size=32)
        self.set_row_icon(icon, a)
        box.append(icon)
        box.append(Gtk.Label(label=name, xalign=0, hexpand=True, ellipsize=3))

        if app in desktop.VARIANTS:
            choices = list(desktop.VARIANTS[app])
            drop = Gtk.DropDown.new_from_strings([VARIANT_LABELS.get(c, c) for c in choices])
            drop.set_selected(choices.index(desktop.variant(app)))
            drop.set_tooltip_text("Version ouverte par le lanceur")
            drop.connect("notify::selected", lambda d, _p: desktop.set_variant(app, choices[d.get_selected()]))
            drop.set_valign(Gtk.Align.CENTER)
            box.append(drop)

        menu = Gio.Menu()
        menu.append("Ouvrir", f"apps.open::{app}")
        menu.append("Changer l'icône…", f"apps.icon::{app}")
        menu.append("Icône d'origine", f"apps.reset-icon::{app}")
        more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, valign=Gtk.Align.CENTER,
                              tooltip_text="Plus d'actions")
        more.add_css_class(Granite.STYLE_CLASS_FLAT)
        box.append(more)

        sw = Gtk.Switch(active=desktop.in_menu(app), valign=Gtk.Align.CENTER,
                        tooltip_text="Afficher dans le menu Applications")

        def on_menu(s, _p):
            png = base64.b64decode(a["icon"]) if a.get("icon") else None
            desktop.set_in_menu(app, s.get_active(), name, a["cmd"], a["category"], png)
        sw.connect("notify::active", on_menu)
        box.append(sw)
        r = Gtk.ListBoxRow(activatable=False, child=box)
        r.icon = icon
        return r

    def set_row_icon(self, image, a):
        path = desktop.app_icon_path(a["id"])
        if path.exists():
            image.set_from_file(str(path))
            return
        tex = None
        if a.get("icon"):
            # même rendu que dans le menu : gabarit elementary + emblème Vasistas
            try:
                from . import icons
                png = icons.compose(base64.b64decode(a["icon"]), 64)
                tex = Gdk.Texture.new_from_bytes(GLib.Bytes.new(png))
            except Exception:  # noqa: BLE001 - icône Windows illisible : on garde l'originale
                tex = texture_from_b64(a["icon"])
        if tex:
            image.set_from_paintable(tex)
        else:
            image.set_from_icon_name("application-x-executable")

    def filter(self):
        q = self.search.get_text().strip().lower()
        shown = {}
        for r, a, lb, label in self.rows:
            ok = not q or q in a["name"].lower() or q in a["id"]
            r.set_visible(ok)
            shown[lb] = shown.get(lb, False) or ok
        for r, a, lb, label in self.rows:
            lb.set_visible(shown[lb])
            label.set_visible(shown[lb])

    def find(self, app):
        return next((a for _, a, _, _ in self.rows if a["id"] == app), None)

    def register(self, a):
        """Commande Windows connue de `launch-app`, même sans lanceur dans le menu."""
        reg = desktop.load_registry()
        if a["id"] not in desktop.VARIANTS and reg.get(a["id"], {}).get("exe") != a["cmd"]:
            reg[a["id"]] = {**reg.get(a["id"], {}), "name": a["name"], "exe": a["cmd"]}
            desktop.save_registry(reg)

    def on_open(self, app):
        a = self.find(app)
        if a:
            self.register(a)
            spawn("launch-app", app)

    def on_icon(self, app):
        dialog = Gtk.FileDialog(title="Choisir une image")
        filt = Gtk.FileFilter(name="Images")
        filt.add_pixbuf_formats()
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(filt)
        dialog.set_filters(store)

        def done(d, res):
            try:
                f = d.open_finish(res)
            except GLib.Error:
                return
            try:
                pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(f.get_path(), 256, 256, True)
                ok, png = pix.save_to_bufferv("png", [], [])
                desktop.set_custom_icon(app, bytes(png))
                self.update_icon(app)
            except GLib.Error as e:
                self.win.notify(f"Image illisible : {e.message}")
        dialog.open(self.win, None, done)

    def on_reset_icon(self, app):
        desktop.set_custom_icon(app, None)
        self.update_icon(app)

    def update_icon(self, app):
        for r, a, _, _ in self.rows:
            if a["id"] == app:
                self.set_row_icon(r.icon, a)
