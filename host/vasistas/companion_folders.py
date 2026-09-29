"""Page « Dossiers » de l'application compagnon : dossiers Linux partagés avec Windows
(virtiofs) et dossiers de Windows reliés aux dossiers Linux (folders.py)."""

import os
import threading
from pathlib import Path

from gi.repository import GLib, Granite, Gtk

from . import control, files, folders, vm
from .companion_common import Page, card, clear, dim, host_ready, row

LAUNCHCTL = "& 'C:\\Program Files (x86)\\WinFsp\\bin\\launchctl-x64.exe'"


class FoldersPage(Page):
    __gtype_name__ = "VasistasFoldersPage"

    def __init__(self, win):
        super().__init__("folder", "Dossiers",
                         "Les dossiers Linux que Windows voit, et ceux qu'il utilise pour ses documents.")
        self.win = win
        self.busy = False

        self.header("Dossiers partagés")
        self.add(dim("Chaque dossier apparaît comme un lecteur dans l'Explorateur de Windows et dans les "
                     "fenêtres « Ouvrir » et « Enregistrer » des applications."))
        self.shares_box = self.add(card())
        add = Gtk.Button(label="Ajouter un dossier…", halign=Gtk.Align.START, margin_top=6)
        add.connect("clicked", lambda *_: self.add_share())
        self.add(add)

        self.header("Dossiers de Windows")
        self.add(dim("Relié, un dossier de Windows est le dossier Linux du même nom : « Enregistrer » dans "
                     "Word propose ~/Documents, le navigateur de Windows télécharge dans ~/Téléchargements. "
                     "Un dossier Linux pas encore partagé l'est au passage. Ce que contenaient les dossiers "
                     "de Windows reste dans Windows."))
        self.suggest = Gtk.Button(label="Relier Documents, Téléchargements et Images",
                                  halign=Gtk.Align.START, margin_top=6)
        self.suggest.add_css_class(Granite.STYLE_CLASS_SUGGESTED_ACTION)
        self.suggest.connect("clicked", lambda *_: self.set_folders(
            [(k, True) for k in folders.SUGGESTED if k not in folders.linked()]))
        self.add(self.suggest)
        self.folders_box = self.add(card())
        self.folders_box.set_margin_top(6)
        self.fill()

    def fill(self):
        self.fill_shares()
        self.fill_folders()
        return False

    # -- dossiers partagés --

    def fill_shares(self):
        clear(self.shares_box)
        home = os.path.expanduser("~")
        for tag, path, drive, label in vm.shares():
            b = Gtk.Box(spacing=12, margin_start=6, margin_end=6, margin_top=4, margin_bottom=4)
            b.append(Gtk.Image(icon_name="folder", pixel_size=32))
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
            texts.append(Gtk.Label(label=f"{label}  ·  lecteur {drive}", xalign=0))
            texts.append(dim(str(path).replace(home, "~", 1)))
            b.append(texts)
            rm = Gtk.Button(icon_name="list-remove-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text="Ne plus partager ce dossier")
            rm.add_css_class(Granite.STYLE_CLASS_FLAT)
            rm.connect("clicked", lambda *_, t=tag: self.remove_share(t))
            b.append(rm)
            self.shares_box.append(Gtk.ListBoxRow(activatable=False, child=b))

    def add_share(self):
        dialog = Gtk.FileDialog(title="Dossier à partager avec Windows")

        def done(d, res):
            try:
                folder = d.select_folder_finish(res)
            except GLib.Error:
                return
            path = folder.get_path()
            if any(str(p) == path for _, p, _, _ in vm.shares()):
                self.win.notify("Ce dossier est déjà partagé")
                return
            added = vm.add_share(path)
            if added is None:
                self.win.notify("Plus de lettre de lecteur libre dans Windows")
                return
            self.fill()
            tag, drive, label = added
            threading.Thread(target=self.mount_share, args=(tag, path, drive, label), daemon=True).start()
        dialog.select_folder(self.win, None, done)

    def mount_share(self, tag, path, drive, label):
        """Branche le dossier sur Windows en marche ; sinon il arrive au prochain démarrage."""
        ok = vm.hotplug_share(tag, Path(path))
        if ok and host_ready():
            try:
                res = control.request({"exec": vm.mount_script(tag, drive, label)}, timeout=90)
                ok = "True" in res.get("out", "")
            except OSError:
                ok = False
        msg = (f"« {label} » est visible dans Windows sur le lecteur {drive}" if ok else
               f"« {label} » sera visible sur le lecteur {drive} au prochain démarrage de Windows")
        GLib.idle_add(self.win.notify, msg)

    def remove_share(self, tag):
        items = [(p, dr, lb) for t, p, dr, lb in vm.shares() if t != tag]
        drive = next((dr for t, _, dr, _ in vm.shares() if t == tag), None)
        tied = folders.using_drive(drive) if drive else []
        if tied and not host_ready():
            self.win.notify("Démarrez Windows d'abord : des dossiers de Windows passent par ce lecteur")
            return
        vm.save_shares(items)
        self.fill_shares()
        if host_ready():
            def work():
                # un dossier de Windows relié à ce lecteur retrouve d'abord son emplacement d'origine
                for k in tied:
                    try:
                        folders.unlink(k)
                    except (RuntimeError, OSError):
                        pass
                control.request({"exec": f"{LAUNCHCTL} stop virtiofs vfs{tag} | Out-Null"}, timeout=30)
                GLib.idle_add(self.fill_folders)
            threading.Thread(target=work, daemon=True).start()
        self.win.notify(f"Dossier retiré (lecteur {drive})")

    # -- dossiers de Windows --

    def fill_folders(self):
        clear(self.folders_box)
        linked = folders.linked()
        home = os.path.expanduser("~")
        for key, name, *_ in folders.FOLDERS:
            path = folders.linux_dir(key)
            win_path = files.to_windows(path)
            where = str(path).replace(home, "~", 1)
            if key in linked:
                sub = f"{where} · {linked[key]}"
            elif win_path:
                sub = f"{where} · déjà visible dans Windows ({win_path})"
            else:
                sub = f"{where} · sera partagé avec Windows"
            sw = Gtk.Switch(active=key in linked, sensitive=not self.busy)
            sw.connect("notify::active", lambda s, _p, k=key: self.set_folders([(k, s.get_active())]))
            r = row(name, sub, sw)
            r.set_margin_start(6)
            r.set_margin_end(6)
            self.folders_box.append(Gtk.ListBoxRow(activatable=False, child=r))
        self.suggest.set_visible(not any(k in linked for k in folders.SUGGESTED))
        self.suggest.set_sensitive(not self.busy)
        return False

    def set_folders(self, changes):
        """[(clé, relier ?)] appliqués dans Windows, hors du fil GTK."""
        changes = [(k, on) for k, on in changes if on != (k in folders.linked())]
        if not changes or self.busy:
            return
        if not host_ready():
            self.win.notify("Démarrez Windows pour relier ses dossiers")
            GLib.idle_add(self.fill_folders)
            return
        self.busy = True
        self.fill_folders()
        self.win.notify("Dossiers de Windows : mise à jour…")

        def work():
            done, errors = [], []
            for key, on in changes:
                name = folders.BY_KEY[key][1]
                try:
                    folders.link(key) if on else folders.unlink(key)
                    done.append(name)
                except (RuntimeError, OSError) as e:
                    errors.append(f"{name} : {e}")
            if errors:
                msg = "; ".join(errors)
            elif changes[0][1]:
                msg = (f"{', '.join(done)} relié{'s' if len(done) > 1 else ''} à Linux. Relancez les "
                       "applications ouvertes pour qu'elles en tiennent compte")
            else:
                msg = f"{', '.join(done)} : retour aux dossiers de Windows"
            GLib.idle_add(self.done, msg)
        threading.Thread(target=work, name="vasistas-folders", daemon=True).start()

    def done(self, msg):
        self.busy = False
        self.fill()
        self.win.notify(msg)
        return False
