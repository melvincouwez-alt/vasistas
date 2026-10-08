"""Section « Mes applications » (page Applications) du compagnon : applications Windows du menu
Applications, ajout des autres, icônes, version d'Outlook, options des lanceurs."""

import base64
import threading

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Granite, Gtk

from . import desktop, guestapps, vm
from .companion_common import Section, advanced_card, card, clear, guest_ready, row, spawn, texture_from_b64
from .i18n import N_, _

VARIANT_LABELS = {"classic": N_("Classique"), "new": N_("Nouveau")}
# applications sans intérêt dans le menu
HIDDEN = {"msoev", "setlang"}


class AppsPage(Section):
    __gtype_name__ = "VasistasAppsPage"

    def __init__(self, win):
        super().__init__("applications-other", _("Mes applications"),
                         _("Choisissez les applications Windows affichées dans le menu Applications et leur icône."))
        self.win = win
        self.rows = []
        self.apps = []
        self.header(_("Dans le menu"))
        self.refresh_btn = Gtk.Button(label=_("Actualiser la liste"))
        self.refresh_btn.set_tooltip_text(_("Lire de nouveau dans Windows la liste des applications installées"))
        self.refresh_btn.connect("clicked", lambda *_: self.refresh())
        self.get_action_area().append(self.refresh_btn)
        self.menu_list = self.add(card())

        # les autres applications de Windows, repliées : on les ajoute au menu d'un interrupteur
        self.header(_("Ajouter au menu"))
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6)
        self.search = Gtk.SearchEntry(placeholder_text=_("Rechercher une application"))
        self.search.connect("search-changed", lambda *_: self.filter())
        inner.append(self.search)
        self.list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.append(self.list_box)
        self.add(Gtk.Expander(label=_("Autres applications de Windows"), child=inner))
        self.column.append(advanced_card(self.launchers_box()))

        group = Gio.SimpleActionGroup()
        for name, handler in (("open", self.on_open), ("icon", self.on_icon), ("reset-icon", self.on_reset_icon)):
            act = Gio.SimpleAction.new(name, GLib.VariantType.new("s"))
            act.connect("activate", lambda _a, param, h=handler: h(param.get_string()))
            group.add_action(act)
        self.insert_action_group("apps", group)
        self.fill(guestapps.cached())

    def launchers_box(self):
        """Nom et icône des lanceurs (desktop.launcher_options)."""
        suffix, emblem, _hidden = desktop.launcher_options()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        sw_suffix = Gtk.Switch(active=suffix)
        sw_suffix.connect("notify::active", lambda s, _p: self.set_launcher_option("launcher_suffix", s.get_active()))
        box.append(row(_("Préciser « (Windows) » dans le nom"),
                       _("« Word (Windows) » au lieu de « Word » dans le menu Applications et le dock, "
                         "pour les distinguer des applications Linux du même nom."), sw_suffix))
        sw_emblem = Gtk.Switch(active=emblem)
        sw_emblem.connect("notify::active", lambda s, _p: self.set_launcher_option("launcher_emblem", s.get_active()))
        box.append(row(_("Emblème Vasistas sur les icônes"),
                       _("Vasistas ajoute son logo en bas à droite des icônes des applications Windows."), sw_emblem))
        return box

    def set_launcher_option(self, key, value):
        c = vm.load_config()
        c[key] = value
        vm.save_config(c)

        def work():
            desktop.apply_launcher_options()  # icônes recomposées : quelques secondes
            GLib.idle_add(self.refresh_icons)
            GLib.idle_add(self.win.notify, _("Lanceurs mis à jour"))
        threading.Thread(target=work, daemon=True).start()

    def refresh_icons(self):
        for r, a, _lb, _label in self.rows:
            self.set_row_icon(r.icon, a)
        return False

    def update(self, state):
        self.refresh_btn.set_sensitive(guest_ready(state))

    def refresh(self):
        self.refresh_btn.set_sensitive(False)
        self.win.notify(_("Lecture de la liste des applications installées dans Windows…"))

        def work():
            try:
                apps = guestapps.fetch()
                GLib.idle_add(self.fill, apps)
                GLib.idle_add(self.win.notify, _("{n} applications trouvées dans Windows", n=len(apps)))
            except Exception as e:  # noqa: BLE001
                GLib.idle_add(self.win.notify, _("Impossible de lire la liste des applications : {error}", error=e))
            GLib.idle_add(self.refresh_btn.set_sensitive, True)
        threading.Thread(target=work, daemon=True).start()

    def fill(self, apps):
        self.apps = apps
        clear(self.list_box)
        clear(self.menu_list)
        self.rows = []
        if not apps:
            empty = Granite.Placeholder(title=_("Liste des applications non disponible"),
                                        description=_("Démarrez Windows, puis cliquez sur « Actualiser la liste »."),
                                        icon=Gio.ThemedIcon.new("applications-other"))
            self.menu_list.append(empty)
            return False
        shown = [a for a in apps if a["id"] not in HIDDEN]
        in_menu = [a for a in shown if desktop.in_menu(a["id"])]
        for a in sorted(in_menu, key=lambda a: a["name"].lower()):
            r = self.app_row(a)
            self.menu_list.append(r)
            self.rows.append((r, a, None, None))
        if not in_menu:
            self.menu_list.append(Gtk.Label(label=_("Aucune application Windows n'est affichée dans le menu "
                                                    "Applications. Ajoutez des applications depuis la section "
                                                    "« Ajouter au menu » ci-dessous."),
                                            margin_top=12, margin_bottom=12))
        for cat, title in guestapps.CATEGORIES.items():
            items = [a for a in shown if a["category"] == cat and a not in in_menu]
            if not items:
                continue
            label = Granite.HeaderLabel.new(_(title))
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
            drop = Gtk.DropDown.new_from_strings([_(VARIANT_LABELS[c]) if c in VARIANT_LABELS else c for c in choices])
            drop.set_selected(choices.index(desktop.variant(app)))
            drop.set_tooltip_text(_("Version d'Outlook ouverte par le lanceur"))
            drop.connect("notify::selected", lambda d, _p: desktop.set_variant(app, choices[d.get_selected()]))
            drop.set_valign(Gtk.Align.CENTER)
            box.append(drop)

        menu = Gio.Menu()
        menu.append(_("Ouvrir"), f"apps.open::{app}")
        menu.append(_("Changer l'icône…"), f"apps.icon::{app}")
        menu.append(_("Rétablir l'icône d'origine"), f"apps.reset-icon::{app}")
        more = Gtk.MenuButton(icon_name="view-more-symbolic", menu_model=menu, valign=Gtk.Align.CENTER,
                              tooltip_text=_("Plus d'actions"))
        more.add_css_class(Granite.STYLE_CLASS_FLAT)
        box.append(more)

        sw = Gtk.Switch(active=desktop.in_menu(app), valign=Gtk.Align.CENTER,
                        tooltip_text=_("Afficher dans le menu Applications"))

        def on_menu(s, _p):
            png = base64.b64decode(a["icon"]) if a.get("icon") else None
            desktop.set_in_menu(app, s.get_active(), name, a["cmd"], a["category"], png)
            GLib.idle_add(self.fill, self.apps)  # l'appli change de carte (pas pendant le signal)
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
                png = icons.compose(base64.b64decode(a["icon"]), 64, desktop.launcher_options()[1])
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
        added = [(r, a, lb, label) for r, a, lb, label in self.rows if lb is not None]
        for r, a, lb, label in added:
            ok = not q or q in a["name"].lower() or q in a["id"]
            r.set_visible(ok)
            shown[lb] = shown.get(lb, False) or ok
        for r, a, lb, label in added:
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
        dialog = Gtk.FileDialog(title=_("Choisir une image"))
        filt = Gtk.FileFilter(name=_("Fichiers image"))
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
                self.win.notify(_("Impossible de lire l'image : {error}", error=e.message))
        dialog.open(self.win, None, done)

    def on_reset_icon(self, app):
        desktop.set_custom_icon(app, None)
        self.update_icon(app)

    def update_icon(self, app):
        for r, a, _, _ in self.rows:
            if a["id"] == app:
                self.set_row_icon(r.icon, a)
