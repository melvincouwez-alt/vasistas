"""Page « Fichiers » de l'application compagnon : extensions ouvertes dans Windows (files.py)."""

import re
import threading

from gi.repository import GLib, Granite, Gtk

from . import desktop, files
from .companion_common import Page, dim

THEME_ICONS = {app: icon for _, app, _, icon in desktop.APPS}


class FilesPage(Page):
    __gtype_name__ = "VasistasFilesPage"

    def __init__(self, win):
        super().__init__("document-open", "Fichiers",
                         "Les fichiers de ces types s'ouvrent dans les applications Windows, d'un "
                         "double-clic dans Fichiers.")
        self.win = win
        self.busy = False
        reset = Gtk.Button(label="Choix par défaut",
                           tooltip_text="Office et Power BI dans Windows, le reste sous Linux")
        reset.connect("clicked", lambda *_: self.run(lambda: files.apply(dict(files.DEFAULTS)),
                                                     "Choix par défaut rétablis"))
        self.get_action_area().append(reset)
        self.cards = self.add(Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6))
        self.add(dim("Windows ne voit que les dossiers partagés (page Dossiers). Pour un fichier rangé "
                     "ailleurs, Vasistas propose d'en ouvrir une copie dans Téléchargements ou de "
                     "partager son dossier. Une extension retirée revient à l'application qui "
                     "l'ouvrait avant."))
        self.fill()

    def fill(self):
        child = self.cards.get_first_child()
        while child:
            nxt = child.get_next_sibling()
            self.cards.remove(child)
            child = nxt
        desig = files.designations()
        for app, name, exts in files.catalog(desig):
            self.cards.append(self.card(app, name, exts, desig))
        return False

    def card(self, app, name, exts, desig):
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_top=6)
        card.add_css_class("card")
        card.add_css_class("rounded")
        head = Gtk.Box(spacing=12, margin_start=12, margin_end=8, margin_top=8)
        icon = Gtk.Image(pixel_size=32)
        path = desktop.app_icon_path(app)
        if path.exists():
            icon.set_from_file(str(path))
        else:
            icon.set_from_icon_name(THEME_ICONS.get(app, "application-x-executable"))
        head.append(icon)
        head.append(Gtk.Label(label=name, xalign=0, hexpand=True))
        head.append(self.add_button(app, name))
        card.append(head)

        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=12,
                           column_spacing=18, row_spacing=6, homogeneous=True,
                           margin_start=12, margin_end=12, margin_bottom=12)
        for ext in exts:
            tb = Gtk.CheckButton(label=f".{ext}", active=desig.get(ext) == app)
            other = desig.get(ext)
            if other and other != app:
                tb.set_tooltip_text(f"Aujourd'hui ouvert dans {files.app_name(other)}")
            elif other == app:
                tb.set_tooltip_text(f"Ouvert dans {name} (Windows)")
            else:
                tb.set_tooltip_text("Ouvert sous Linux")
            tb.connect("toggled", self.on_toggle, ext, app, name)
            flow.append(tb)
        card.append(flow)
        return card

    def add_button(self, app, name):
        entry = Gtk.Entry(placeholder_text="extension, ex. csv", width_chars=16)
        ok = Gtk.Button(label="Ajouter")
        ok.add_css_class("suggested-action")
        pop_box = Gtk.Box(spacing=6, margin_start=6, margin_end=6, margin_top=6, margin_bottom=6)
        pop_box.append(entry)
        pop_box.append(ok)
        pop = Gtk.Popover(child=pop_box)
        btn = Gtk.MenuButton(icon_name="list-add-symbolic", popover=pop, valign=Gtk.Align.CENTER,
                             tooltip_text=f"Ouvrir une autre extension dans {name}")
        btn.add_css_class(Granite.STYLE_CLASS_FLAT)

        def add(*_):
            ext = files.normalize(entry.get_text())
            if not re.fullmatch(r"[a-z0-9_-]{1,12}", ext):
                self.win.notify("Extension invalide")
                return
            pop.popdown()
            entry.set_text("")
            self.run(lambda: files.set_designation(ext, app), f".{ext} s'ouvre dans {name}")
        entry.connect("activate", add)
        ok.connect("clicked", add)
        return btn

    def on_toggle(self, tb, ext, app, name):
        if self.busy:
            return
        on = tb.get_active()
        msg = f".{ext} s'ouvre dans {name}" if on else f".{ext} ne s'ouvre plus dans {name}"
        self.run(lambda: files.set_designation(ext, app if on else None), msg)

    def run(self, work, message):
        """Application des choix hors du fil GTK (update-mime-database prend une seconde)."""
        if self.busy:
            return
        self.busy = True
        self.cards.set_sensitive(False)

        def job():
            try:
                work()
                GLib.idle_add(self.win.notify, message)
            except Exception as e:  # noqa: BLE001
                GLib.idle_add(self.win.notify, f"Échec : {e}")
            GLib.idle_add(self.done)
        threading.Thread(target=job, name="vasistas-files", daemon=True).start()

    def done(self):
        self.busy = False
        self.fill()
        self.cards.set_sensitive(True)
        return False
