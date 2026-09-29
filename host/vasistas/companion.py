"""Application compagnon : piloter la machine virtuelle Windows et régler Vasistas.

Fenêtre elementary (GTK 4 + Granite) au format des Paramètres : barre latérale à gauche
(Granite.SettingsSidebar, avec l'état de Windows), une page par sujet à droite. Elle parle à
l'hôte `vasistas run` par le socket de contrôle et lit ou écrit ~/.local/share/vasistas
(config.json, apps.json, installed.json).
"""

import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Granite", "7.0")
from gi.repository import Gdk, Gio, GLib, Granite, Gtk  # noqa: E402

from . import guestapps, vm  # noqa: E402
from .app import APP_ID  # noqa: E402
from .companion_common import RESOURCES, host_ready, spawn  # noqa: E402,F401 (utilisés par l'assistant)
from .version import PRERELEASE, VERSION, WEBSITE  # noqa: E402

COMPANION_ID = f"{APP_ID}.Companion"
POLL_S = 2
# Marges et tailles des pages de réglages, reprises de l'application Paramètres d'elementary
# (Granite ne les fournit pas : chaque application les pose elle-même)
CSS = """
settingspage .header-area widget:dir(ltr) { margin-left: 0.33rem; margin-right: 0.75rem; }
settingspage .header-area image.large-icons { -gtk-icon-size: 4rem; }
settingspage .header-area image.large-icons:dir(ltr) { margin-left: -0.33rem; margin-right: 0.58rem; }
settingspage .header-area label.title-2 { font-weight: 600; font-size: 2rem; }
settingspage .header-area label:not(.title-2) { font-size: 0.95rem; opacity: 0.85; }
settingspage .header-area, settingspage .content-area { padding: 1rem; }
settingspage .buttonbox { padding: 1rem; border-spacing: 0.5rem; }
settingssidebar list { background: inherit; min-width: 16.67rem; }
settingssidebar list row { padding: 0.5rem; }
settingssidebar list row overlay { min-width: calc(32px + 0.5rem); }
settingssidebar list row overlay:dir(ltr) { margin-right: 0.5rem; }
settingssidebar list row overlay:dir(rtl) { margin-left: 0.5rem; }
.banner { padding: 0; }
"""


class Window(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Vasistas", default_width=960, default_height=680,
                         icon_name=APP_ID)
        self.state = {}

        header = Gtk.HeaderBar()
        header.add_css_class(Granite.STYLE_CLASS_FLAT)
        menu = Gio.Menu()
        menu.append("Guide rapide", "app.guide")
        menu.append("Rechercher des mises à jour", "app.updates")
        menu.append("À propos de Vasistas", "app.about")
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text="Menu", primary=True))
        self.set_titlebar(header)

        from .companion_apps import AppsPage
        from .companion_files import FilesPage
        from .companion_folders import FoldersPage
        from .companion_install import InstallPage
        from .companion_slim import SlimPage
        from .companion_windows import PerformancePage, WindowsPage
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True, vexpand=True)
        self.pages = [
            ("windows", WindowsPage(self)),
            ("performance", PerformancePage(self)),
            ("folders", FoldersPage(self)),
            ("apps", AppsPage(self)),
            ("install", InstallPage(self)),
            ("files", FilesPage(self)),
            ("slim", SlimPage(self)),
        ]
        from . import companion_browser
        if companion_browser.available():
            self.pages.append(("browser", companion_browser.BrowserPage(self)))
        for name, page in self.pages:
            self.stack.add_named(page, name)
        sidebar = Granite.SettingsSidebar(stack=self.stack, width_request=220)

        # bandeau « nouvelle version », au-dessus des pages
        self.banner_label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        see = Gtk.Button(label="Voir")
        see.connect("clicked", lambda *_: self.get_application().show_updates(self.release))
        later = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Plus tard")
        later.add_css_class(Granite.STYLE_CLASS_FLAT)
        later.connect("clicked", lambda *_: self.banner.set_reveal_child(False))
        bar = Gtk.Box(spacing=12, margin_start=18, margin_end=12, margin_top=8, margin_bottom=8)
        bar.append(Gtk.Image(icon_name="system-software-update", pixel_size=24))
        bar.append(self.banner_label)
        bar.append(see)
        bar.append(later)
        bar_frame = Gtk.Box()
        bar_frame.add_css_class(Granite.STYLE_CLASS_ACCENT)
        bar_frame.add_css_class("banner")
        bar_frame.append(bar)
        self.banner = Gtk.Revealer(child=bar_frame, reveal_child=False)
        self.release = None

        right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        right.append(self.banner)
        right.append(self.stack)
        paned = Gtk.Paned(start_child=sidebar, end_child=right, shrink_start_child=False,
                          resize_start_child=False)
        self.toast = Granite.Toast(title="")
        overlay = Gtk.Overlay(child=paned)
        overlay.add_overlay(self.toast)
        self.set_child(overlay)

        self.poll()
        self.poll_source = GLib.timeout_add_seconds(POLL_S, self.poll)
        self.connect("close-request", lambda *a: GLib.source_remove(self.poll_source) and False)
        self.check_updates()

    def notify(self, text):
        self.toast.set_title(text)
        self.toast.send_notification()
        return False

    def poll(self):
        def work():
            state = {"pid": vm.pid(), "host": host_ready()}
            GLib.idle_add(self.show_state, state)
        threading.Thread(target=work, daemon=True).start()
        return True

    def show_state(self, state):
        self.state = state
        for _, page in self.pages:
            page.update(state)
        return False

    def page(self, name):
        return dict(self.pages).get(name)

    # -- mises à jour --

    def check_updates(self):
        """Vérification discrète, au plus une fois par jour (updates.check garde le résultat)."""
        if vm.load_config().get("check_updates") is False:
            return

        def work():
            from . import updates
            try:
                status, rel = updates.check()
            except updates.UpdateError:
                return
            if status == "available":
                GLib.idle_add(self.show_banner, rel)
        threading.Thread(target=work, daemon=True).start()

    def show_banner(self, rel):
        self.release = rel
        kind = " (préversion)" if rel.get("prerelease") else ""
        self.banner_label.set_label(f"Vasistas {rel['version']}{kind} est disponible.")
        self.banner.set_reveal_child(True)
        return False


class App(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=COMPANION_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        Granite.init()
        gs = Granite.Settings.get_default()
        gtk = Gtk.Settings.get_default()

        def follow(*_):
            gtk.set_property("gtk-application-prefer-dark-theme",
                             gs.get_property("prefers-color-scheme") == Granite.SettingsColorScheme.DARK)
        follow()
        gs.connect("notify::prefers-color-scheme", follow)
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        for name, handler, accels in (("guide", self.show_guide, ["F1"]),
                                      ("updates", lambda: self.show_updates(), []),
                                      ("about", self.show_about, []),
                                      ("quit", self.quit, ["<Control>q"])):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_a, h=handler: h())
            self.add_action(act)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)

    def do_activate(self):
        win = self.get_active_window()
        if win is None:
            win = Window(self)
            win.present()
            # première fois : Windows pas encore installé
            if not vm.DISK.exists() and not vm.load_config().get("setup_done"):
                self.open_wizard()
            return
        win.present()

    def open_wizard(self):
        from .wizard import Wizard
        win = self.get_active_window()

        def done():
            apps = win.page("apps")
            if apps is not None:
                apps.fill(guestapps.cached())
            win.poll()
        Wizard(win, on_done=done).present()

    def show_guide(self):
        from .guide import GuideWindow
        GuideWindow(self.get_active_window()).present()

    def show_updates(self, release=None):
        from .companion_updates import UpdateWindow
        UpdateWindow(self.get_active_window(), release).present()

    def show_about(self):
        from . import updates
        mode = {"dev": "développement (dépôt git)", "installed": "installée",
                "other": "copie locale"}[updates.mode()]
        about = Gtk.AboutDialog(
            transient_for=self.get_active_window(), modal=True,
            program_name="Vasistas", logo_icon_name=APP_ID,
            version=VERSION + (" (préversion)" if PRERELEASE else ""),
            comments="Les applications Windows d'une machine virtuelle, une fenêtre chacune, sur le "
                     "bureau Linux. Sans bureau à distance (RDP).",
            website=WEBSITE, website_label="Page du projet",
            copyright="© 2026 Les contributeurs de Vasistas", license_type=Gtk.License.MIT_X11,
            system_information=f"Version {mode}\nQEMU : {vm.QEMU}\nDonnées : {vm.DATA}")
        about.present()


def main(argv=None):
    return App().run([sys.argv[0]] + list(argv or []))
