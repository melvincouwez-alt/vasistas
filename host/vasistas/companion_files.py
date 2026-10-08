"""Section « Ouvrir avec Windows » (page Fichiers) du compagnon : extensions ouvertes dans Windows (files.py)."""

import re
import threading

from gi.repository import GLib, Gtk

from . import desktop, files
from .companion_common import Section, card, clear, dim, mode_chip
from .i18n import _

THEME_ICONS = {app: icon for _, app, _, icon in desktop.APPS}


class FilesPage(Section):
    __gtype_name__ = "VasistasFilesPage"

    def __init__(self, win):
        super().__init__("document-open", _("Fichiers"),
                         _("Les fichiers des types listés ci-dessous s'ouvrent dans les applications Windows par un "
                           "double-clic dans l'application Fichiers."))
        self.win = win
        self.busy = False
        self.header(_("Ouvrir avec Windows"))
        reset = Gtk.Button(label=_("Rétablir les choix par défaut"),
                           tooltip_text=_("Ouvrir les fichiers Office et Power BI dans Windows, et les autres fichiers "
                                          "sous Linux"))
        reset.connect("clicked", lambda *_a: self.run(lambda: files.apply(dict(files.DEFAULTS)),
                                                      _("Choix par défaut rétablis")))
        self.get_action_area().append(reset)
        self.cards = self.add(card())
        self.add(dim(_("Windows n'a accès qu'aux dossiers partagés. Pour un fichier situé dans un autre dossier, "
                       "Vasistas propose d'en ouvrir une copie.")))
        self.fill()

    def fill(self):
        clear(self.cards)
        desig = files.designations()
        for app, name, exts in files.catalog(desig):
            self.cards.append(self.app_row(app, name, exts, desig))
        return False

    def app_row(self, app, name, exts, desig):
        """Une ligne par application : ses extensions en étiquettes, « Modifier » pour les choisir."""
        line = Gtk.Box(spacing=12, margin_start=6, margin_end=6, margin_top=6, margin_bottom=6)
        icon = Gtk.Image(pixel_size=32, valign=Gtk.Align.CENTER)
        path = desktop.app_icon_path(app)
        if path.exists():
            icon.set_from_file(str(path))
        else:
            icon.set_from_icon_name(THEME_ICONS.get(app, "application-x-executable"))
        line.append(icon)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True, valign=Gtk.Align.CENTER)
        texts.append(Gtk.Label(label=name, xalign=0))
        chips = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=10,
                            column_spacing=4, row_spacing=4)
        active = [e for e in exts if desig.get(e) == app]
        for ext in active:
            chips.append(mode_chip(None, f".{ext}"))
        if not active:
            chips.append(dim(_("Aucun type de fichier associé : les fichiers correspondants s'ouvrent sous Linux")))
        texts.append(chips)
        line.append(texts)
        line.append(self.edit_button(app, name, exts, desig))
        return Gtk.ListBoxRow(activatable=False, child=line)

    def edit_button(self, app, name, exts, desig):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=8, margin_end=8,
                      margin_top=8, margin_bottom=8)
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4,
                           column_spacing=12, row_spacing=4, homogeneous=True)
        for ext in exts:
            tb = Gtk.CheckButton(label=f".{ext}", active=desig.get(ext) == app)
            other = desig.get(ext)
            if other and other != app:
                tb.set_tooltip_text(_("Actuellement ouvert dans {name}", name=files.app_name(other)))
            elif other == app:
                tb.set_tooltip_text(_("Ouvert dans {name} (Windows)", name=name))
            else:
                tb.set_tooltip_text(_("Ouvert sous Linux"))
            tb.connect("toggled", self.on_toggle, ext, app, name)
            flow.append(tb)
        box.append(flow)
        entry = Gtk.Entry(placeholder_text=_("Extension, par exemple csv"), width_chars=16, hexpand=True)
        ok = Gtk.Button(label=_("Ajouter"))
        ok.add_css_class("suggested-action")
        add_box = Gtk.Box(spacing=6)
        add_box.append(entry)
        add_box.append(ok)
        box.append(add_box)
        pop = Gtk.Popover(child=box)
        # libellé et flèche (l'icône par défaut du thème est un engrenage)
        inner = Gtk.Box(spacing=6)
        inner.append(Gtk.Label(label=_("Modifier")))
        inner.append(Gtk.Image(icon_name="pan-down-symbolic"))
        btn = Gtk.MenuButton(child=inner, popover=pop, valign=Gtk.Align.CENTER,
                             tooltip_text=_("Types de fichiers ouverts dans {name}", name=name))

        def add(*_a):
            ext = files.normalize(entry.get_text())
            if not re.fullmatch(r"[a-z0-9_-]{1,12}", ext):
                self.win.notify(_("Extension invalide"))
                return
            pop.popdown()
            entry.set_text("")
            self.run(lambda: files.set_designation(ext, app), _(".{ext} s'ouvre dans {name}", ext=ext, name=name))
        entry.connect("activate", add)
        ok.connect("clicked", add)
        return btn

    def on_toggle(self, tb, ext, app, name):
        if self.busy:
            return
        on = tb.get_active()
        msg = _(".{ext} s'ouvre dans {name}", ext=ext, name=name) if on else \
            _(".{ext} ne s'ouvre plus dans {name}", ext=ext, name=name)
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
                GLib.idle_add(self.win.notify, _("Échec : {e}", e=e))
            GLib.idle_add(self.done)
        threading.Thread(target=job, name="vasistas-files", daemon=True).start()

    def done(self):
        self.busy = False
        self.fill()
        self.cards.set_sensitive(True)
        return False
