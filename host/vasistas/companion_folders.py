"""Page « Dossiers » de l'application compagnon : dossiers Linux partagés avec Windows
(virtiofs) et dossiers de Windows reliés aux dossiers Linux (folders.py)."""

import os
import threading
from pathlib import Path

from gi.repository import GLib, Granite, Gtk

from . import control, folders, vm
from .companion_common import Section, card, clear, dim, guest_ready, host_ready
from .i18n import _

LAUNCHCTL = "& 'C:\\Program Files (x86)\\WinFsp\\bin\\launchctl-x64.exe'"


class FoldersPage(Section):
    __gtype_name__ = "VasistasFoldersPage"

    def __init__(self, win):
        super().__init__("folder", _("Dossiers"),
                         _("Les dossiers Linux accessibles depuis Windows, et les dossiers Linux dans lesquels Windows "
                           "enregistre ses documents."))
        self.win = win
        self.busy = False

        self.header(_("Dossiers partagés"))
        add = Gtk.Button(label=_("Ajouter un dossier…"))
        add.connect("clicked", lambda *_: self.add_share())
        self.suggest = Gtk.Button(label=_("Relier Documents, Téléchargements et Images"))
        self.suggest.add_css_class(Granite.STYLE_CLASS_SUGGESTED_ACTION)
        self.suggest.connect("clicked", lambda *_: self.set_folders(
            [(k, True) for k in folders.SUGGESTED if k not in folders.linked()]))
        actions = self.get_action_area()
        actions.append(self.suggest)
        actions.append(add)
        self.add(dim(_("Chaque dossier partagé apparaît comme un lecteur dans Windows. Lorsqu'un dossier de Windows "
                       "(Documents, Images…) est relié, Windows enregistre son contenu dans le dossier Linux du même "
                       "nom.")))
        self.shares_box = self.add(card())
        self.fill()

    def fill(self):
        """Une seule liste : les dossiers partagés, puis les dossiers connus pas encore partagés
        (atténués) ; un dossier connu porte l'interrupteur de liaison au dossier de Windows."""
        clear(self.shares_box)
        home = os.path.expanduser("~")
        linked = folders.linked()
        known = {folders.linux_dir(k).resolve(): (k, name) for k, name, *_r in folders.FOLDERS}
        shared = set()
        for tag, path, drive, label in vm.shares():
            key, _name = known.get(Path(path).resolve(), (None, None))
            shared.add(key)
            rm = Gtk.Button(icon_name="list-remove-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text=_("Ne plus partager ce dossier"))
            rm.add_css_class(Granite.STYLE_CLASS_FLAT)
            rm.connect("clicked", lambda *_, t=tag: self.remove_share(t))
            self.add_row(_("{label}  ·  lecteur {drive}", label=label, drive=drive),
                         str(path).replace(home, "~", 1), key, linked, rm)
        for key, name, *_r in folders.FOLDERS:
            if key not in shared:
                r = self.add_row(_(name), str(folders.linux_dir(key)).replace(home, "~", 1), key, linked)
                r.set_opacity(0.6)
        self.suggest.set_visible(not any(k in linked for k in folders.SUGGESTED))
        self.suggest.set_sensitive(not self.busy)
        return False

    def add_row(self, title, path, key, linked, extra=None):
        b = Gtk.Box(spacing=12, margin_start=6, margin_end=6, margin_top=4, margin_bottom=4)
        b.append(Gtk.Image(icon_name="folder", pixel_size=32))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        texts.append(Gtk.Label(label=title, xalign=0))
        texts.append(dim(path))
        b.append(texts)
        if key is not None:
            lbl = Gtk.Label(label=_("Relié à Windows"), valign=Gtk.Align.CENTER)
            lbl.add_css_class(Granite.STYLE_CLASS_DIM_LABEL)
            lbl.add_css_class(Granite.STYLE_CLASS_SMALL_LABEL)
            b.append(lbl)
            sw = Gtk.Switch(active=key in linked, sensitive=not self.busy, valign=Gtk.Align.CENTER,
                            tooltip_text=_("Windows enregistre le contenu de son dossier du même nom dans ce dossier "
                                           "Linux"))
            sw.connect("notify::active", lambda s, _p, k=key: self.set_folders([(k, s.get_active())]))
            b.append(sw)
        if extra is not None:
            b.append(extra)
        r = Gtk.ListBoxRow(activatable=False, child=b)
        self.shares_box.append(r)
        return r

    def add_share(self):
        dialog = Gtk.FileDialog(title=_("Dossier à partager avec Windows"))

        def done(d, res):
            try:
                folder = d.select_folder_finish(res)
            except GLib.Error:
                return
            path = folder.get_path()
            if any(str(p) == path for _t, p, _d, _l in vm.shares()):
                self.win.notify(_("Ce dossier est déjà partagé"))
                return
            added = vm.add_share(path)
            if added is None:
                self.win.notify(_("Aucune lettre de lecteur n'est disponible dans Windows"))
                return
            self.fill()
            tag, drive, label = added
            threading.Thread(target=self.mount_share, args=(tag, path, drive, label), daemon=True).start()
        dialog.select_folder(self.win, None, done)

    def mount_share(self, tag, path, drive, label):
        """Branche le dossier sur Windows en marche ; sinon il arrive au prochain démarrage."""
        ok = vm.hotplug_share(tag, Path(path))
        if ok and (host_ready() or {}).get("guest_ready"):
            try:
                res = control.request({"exec": vm.mount_script(tag, drive, label)}, timeout=90)
                ok = "True" in res.get("out", "")
            except OSError:
                ok = False
        msg = (_("« {label} » est visible dans Windows sur le lecteur {drive}", label=label, drive=drive) if ok else
               _("« {label} » sera visible sur le lecteur {drive} au prochain démarrage de Windows",
                 label=label, drive=drive))
        GLib.idle_add(self.win.notify, msg)

    def remove_share(self, tag):
        items = [(p, dr, lb) for t, p, dr, lb in vm.shares() if t != tag]
        drive = next((dr for t, _, dr, _ in vm.shares() if t == tag), None)
        tied = folders.using_drive(drive) if drive else []
        if tied and not guest_ready(self.win.state):
            self.win.notify(_("Démarrez d'abord Windows : des dossiers de Windows sont reliés à ce lecteur"))
            return
        vm.save_shares(items)
        self.fill()
        if guest_ready(self.win.state):
            def work():
                # un dossier de Windows relié à ce lecteur retrouve d'abord son emplacement d'origine
                for k in tied:
                    try:
                        folders.unlink(k)
                    except (RuntimeError, OSError):
                        pass
                try:
                    control.request({"exec": f"{LAUNCHCTL} stop virtiofs vfs{tag} | Out-Null"}, timeout=30)
                except OSError:
                    pass
                GLib.idle_add(self.fill)
            threading.Thread(target=work, daemon=True).start()
        self.win.notify(_("Dossier retiré des dossiers partagés (lecteur {drive})", drive=drive))

    def set_folders(self, changes):
        """[(clé, relier ?)] appliqués dans Windows, hors du fil GTK."""
        changes = [(k, on) for k, on in changes if on != (k in folders.linked())]
        if not changes or self.busy:
            return
        if not guest_ready(self.win.state):
            self.win.notify(_("Démarrez Windows pour relier ou délier ses dossiers"))
            GLib.idle_add(self.fill)
            return
        self.busy = True
        self.fill()
        self.win.notify(_("Mise à jour des dossiers de Windows…"))

        def work():
            done, errors = [], []
            for key, on in changes:
                name = _(folders.BY_KEY[key][1])
                try:
                    folders.link(key) if on else folders.unlink(key)
                    done.append(name)
                except (RuntimeError, OSError) as e:
                    errors.append(_("{name} : {error}", name=name, error=e))
            if errors:
                msg = "; ".join(errors)
            elif changes[0][1]:
                msg = _("{names} relié(s) aux dossiers Linux. Redémarrez les applications Windows ouvertes pour "
                        "appliquer ce changement", names=", ".join(done))
            else:
                msg = _("{names} : emplacement d'origine dans Windows rétabli", names=", ".join(done))
            GLib.idle_add(self.done, msg)
        threading.Thread(target=work, name="vasistas-folders", daemon=True).start()

    def done(self, msg):
        self.busy = False
        self.fill()
        self.win.notify(msg)
        return False
