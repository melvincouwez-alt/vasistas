"""Fenêtre « Mises à jour de Vasistas » : version publiée sur GitHub, notes, installation."""

import threading

from gi.repository import Gio, GLib, Granite, Gtk

from . import updates, version
from .companion_common import dim
from .i18n import _


class UpdateWindow(Gtk.Window):
    def __init__(self, parent, release=None):
        super().__init__(title=_("Mises à jour de Vasistas"), transient_for=parent, modal=True,
                         default_width=520, default_height=440)
        self.parent = parent
        self.release = release
        self.cancel = threading.Event()
        header = Gtk.HeaderBar()
        header.add_css_class(Granite.STYLE_CLASS_FLAT)
        self.set_titlebar(header)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=24, margin_end=24,
                      margin_top=12, margin_bottom=18)
        top = Gtk.Box(spacing=16)
        top.append(Gtk.Image(icon_name="system-software-update", pixel_size=48, valign=Gtk.Align.START))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        self.title = Gtk.Label(label=_("Recherche d'une nouvelle version…"), xalign=0, wrap=True)
        self.title.add_css_class(Granite.STYLE_CLASS_H3_LABEL)
        texts.append(self.title)
        self.subtitle = dim(_("Version installée : {version}", version=version.VERSION))
        texts.append(self.subtitle)
        top.append(texts)
        box.append(top)

        self.notes = Gtk.Label(xalign=0, yalign=0, wrap=True, selectable=True)
        self.notes_scroll = Gtk.ScrolledWindow(child=self.notes, vexpand=True, visible=False,
                                               hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.notes_scroll.add_css_class(Granite.STYLE_CLASS_CARD)
        box.append(self.notes_scroll)
        self.progress = Gtk.ProgressBar(show_text=True, visible=False)
        box.append(self.progress)
        self.spinner = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER, vexpand=True)
        box.append(self.spinner)

        buttons = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        self.close_btn = Gtk.Button(label=_("Fermer"))
        self.close_btn.connect("clicked", lambda *_: self.on_close())
        self.web_btn = Gtk.Button(label=_("Voir sur GitHub"), visible=False)
        self.web_btn.connect("clicked", lambda *_: Gtk.UriLauncher.new(
            (self.release or {}).get("url") or version.WEBSITE).launch(self, None, None))
        self.install_btn = Gtk.Button(label=_("Installer"), visible=False)
        self.install_btn.add_css_class(Granite.STYLE_CLASS_SUGGESTED_ACTION)
        self.install_btn.connect("clicked", lambda *_: self.install())
        self.restart_btn = Gtk.Button(label=_("Relancer Vasistas"), visible=False)
        self.restart_btn.add_css_class(Granite.STYLE_CLASS_SUGGESTED_ACTION)
        self.restart_btn.connect("clicked", lambda *_: self.restart())
        for b in (self.close_btn, self.web_btn, self.install_btn, self.restart_btn):
            buttons.append(b)
        box.append(buttons)
        self.set_child(box)
        self.connect("close-request", lambda *_: self.cancel.set() or False)

        if release:
            self.show_result("available", release)
        else:
            threading.Thread(target=self.check, daemon=True).start()

    def check(self):
        try:
            status, rel = updates.check(force=True)
            GLib.idle_add(self.show_result, status, rel)
        except updates.UpdateError as e:
            GLib.idle_add(self.show_error, str(e))

    def show_error(self, text):
        self.spinner.set_visible(False)
        self.title.set_label(_("Impossible de vérifier les mises à jour"))
        self.subtitle.set_label(text)
        self.web_btn.set_visible(True)
        return False

    def show_result(self, status, rel):
        self.release = rel
        self.spinner.set_visible(False)
        if status != "available" or not rel:
            self.title.set_label(_("Vasistas est à jour"))
            self.subtitle.set_label(_("Version installée : {version}", version=version.VERSION))
            return False
        if rel.get("prerelease"):
            self.title.set_label(_("Vasistas {version} (préversion) est disponible", version=rel["version"]))
        else:
            self.title.set_label(_("Vasistas {version} est disponible", version=rel["version"]))
        self.notes.set_label(rel.get("notes") or _("Aucune note de version."))
        self.notes_scroll.set_visible(True)
        self.web_btn.set_visible(True)
        mode = updates.mode()
        if mode == "installed" and rel.get("asset"):
            self.subtitle.set_label(_("Version installée : {version}. La nouvelle version est installée à côté de la "
                                      "version actuelle, qui reste disponible.", version=version.VERSION))
            self.install_btn.set_visible(True)
        elif mode == "dev":
            self.subtitle.set_label(_("Version de développement (dépôt git) : mettez-la à jour avec git pull."))
        else:
            self.subtitle.set_label(_("Installez la nouvelle version avec son script install.sh, depuis GitHub."))
        return False

    def install(self):
        self.install_btn.set_sensitive(False)
        self.progress.set_visible(True)
        self.progress.set_text(_("Téléchargement…"))

        def progress(done, total):
            if total:
                GLib.idle_add(self.progress.set_fraction, done / total)
                GLib.idle_add(self.progress.set_text, _("{done} / {total} Mo", done=done >> 20, total=total >> 20))

        def work():
            try:
                updates.install(self.release, progress, self.cancel)
                GLib.idle_add(self.installed)
            except (updates.UpdateError, OSError) as e:
                GLib.idle_add(self.failed, str(e))
        threading.Thread(target=work, daemon=True).start()

    def installed(self):
        self.progress.set_fraction(1)
        self.progress.set_text(_("Installée"))
        self.title.set_label(_("Vasistas {version} est installé", version=self.release["version"]))
        self.subtitle.set_label(_("Relancez Vasistas pour utiliser la nouvelle version. L'agent Windows est mis à jour "
                                  "au prochain démarrage de Windows."))
        self.install_btn.set_visible(False)
        self.restart_btn.set_visible(True)
        return False

    def failed(self, text):
        self.progress.set_visible(False)
        self.install_btn.set_sensitive(True)
        self.subtitle.set_label(_("Échec : {error}", error=text))
        return False

    def restart(self):
        """Relance l'application compagnon depuis la nouvelle version ; l'hôte (fenêtres Windows)
        passe à la nouvelle version à sa prochaine ouverture."""
        Gio.Subprocess.new([str(updates.WRAPPER), "companion"], Gio.SubprocessFlags.NONE)
        self.parent.get_application().quit()

    def on_close(self):
        self.cancel.set()
        self.close()
