"""Application compagnon : piloter la machine virtuelle Windows et régler Vasistas.

Fenêtre elementary (GTK 4 + Granite) : les pages principales en boutons groupés dans la barre
de titre, les autres (Avancé, Paramètres…) dans son menu. Elle parle à
l'hôte `vasistas run` par le socket de contrôle et lit ou écrit ~/.local/share/vasistas
(config.json, apps.json, installed.json).
"""

import os
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Granite", "7.0")
from gi.repository import Gdk, Gio, GLib, Granite, Gtk  # noqa: E402

from . import guestapps, vm  # noqa: E402
from .companion_common import host_ready  # noqa: E402
from .i18n import N_, _  # noqa: E402
from .version import VERSION  # noqa: E402
from .winctl import APP_ID  # noqa: E402

COMPANION_ID = f"{APP_ID}.Companion"
POLL_S = 2
# Marges et tailles des pages de réglages, reprises de l'application Paramètres d'elementary
# (Granite ne les fournit pas : chaque application les pose elle-même ; nos pages sont des
# « simplesettingspage »)
CSS = """
settingspage .header-area widget:dir(ltr), simplesettingspage .header-area widget:dir(ltr) { margin-left: 0.33rem; margin-right: 0.75rem; }
settingspage .header-area image.large-icons, simplesettingspage .header-area image.large-icons { -gtk-icon-size: 4rem; }
settingspage .header-area image.large-icons:dir(ltr), simplesettingspage .header-area image.large-icons:dir(ltr) { margin-left: -0.33rem; margin-right: 0.58rem; }
settingspage .header-area label.title-2, simplesettingspage .header-area label.title-2 { font-weight: 600; font-size: 2rem; }
settingspage .header-area label:not(.title-2), simplesettingspage .header-area label:not(.title-2) { font-size: 0.95rem; opacity: 0.85; }
settingspage .header-area, simplesettingspage .header-area, settingspage .content-area, simplesettingspage .content-area { padding: 1rem; }
settingspage .buttonbox, simplesettingspage .buttonbox { padding: 1rem; border-spacing: 0.5rem; }
scale.modes trough { min-height: 6px; border-radius: 3px; background-color: transparent;
  background-image: linear-gradient(to right, #57C98A, #6FA8FF 50%, #F0B45A); }
scale.modes highlight { opacity: 0; }
scale.modes slider { min-width: 22px; min-height: 22px; }
.dash-card { padding: 18px; }
.tile { padding: 10px 12px; border-radius: 6px; background: alpha(@theme_fg_color, 0.06); }
.tile-value { font-weight: 600; font-size: 1.05em; }
.big-value { font-weight: 600; font-size: 1.4em; font-feature-settings: "tnum"; }
.live-dot { min-width: 8px; min-height: 8px; border-radius: 4px; background: #68B723; }
.live-dot.off { background: alpha(@theme_fg_color, 0.3); }
.preview { border-radius: 6px; }
.live-dot.warn { background: #F9C440; }
.pastille { border-radius: 9px; padding: 7px; -gtk-icon-size: 18px; background: alpha(@theme_fg_color, 0.07); }
.pastille.small { border-radius: 7px; padding: 5px; -gtk-icon-size: 14px; }
.chip { padding: 3px 10px; border-radius: 999px; font-size: 0.9em; background: alpha(@theme_fg_color, 0.07); }
.mode-title { font-weight: 700; font-size: 1.6em; }
.dash-card list.card { box-shadow: none; border: 1px solid alpha(@theme_fg_color, 0.1); }
.dash-card > stackswitcher { margin-bottom: 4px; }
.banner { padding: 0; }"""


class Window(Gtk.ApplicationWindow):
    PRIMARY = [("home", N_("Accueil")), ("apps", N_("Applications")), ("files", N_("Fichiers")),
               ("display", N_("Affichage")), ("windows", "Windows"), ("restoration", N_("Restauration"))]
    SECONDARY = [("settings", N_("Préférences")), ("help", N_("Aide et diagnostic")),
                 ("about", N_("À propos"))]

    def __init__(self, app):
        super().__init__(application=app, title="Vasistas", default_width=960, default_height=680,
                         icon_name=APP_ID)
        self.state = {}

        header = Gtk.HeaderBar()
        menu = Gio.Menu()
        more = Gio.Menu()
        for name, label in self.SECONDARY:
            more.append(_(label), f"win.page::{name}")
        menu.append_section(None, more)
        header.pack_end(Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=menu,
                                       tooltip_text=_("Menu"), primary=True))
        go = Gio.SimpleAction.new("page", GLib.VariantType.new("s"))
        go.connect("activate", lambda _a, v: self.show_page(v.get_string()))
        self.add_action(go)
        # pages principales : boutons groupés au centre de la barre de titre
        self.tabs = {}
        tabs = Gtk.Box()
        tabs.add_css_class("linked")
        first = None
        for name, label in self.PRIMARY:
            b = Gtk.ToggleButton(label=_(label), group=first)
            first = first or b
            b.connect("toggled", lambda btn, n=name: btn.get_active() and self.stack.get_visible_child_name() != n
                      and self.show_page(n))
            tabs.append(b)
            self.tabs[name] = b
        header.set_title_widget(tabs)
        self.set_titlebar(header)

        from .companion_advanced import HelpPage
        from .companion_apps import AppsPage
        from .companion_common import ColumnsPage
        from .companion_display import PerformancePage
        from .companion_files import FilesPage
        from .companion_folders import FoldersPage
        from .companion_home import HomePage
        from .companion_install import InstallPage
        from .companion_restore import RestorationPage
        from .companion_settings import AboutPage, SettingsPage
        from .companion_windows import WindowsPage
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True, vexpand=True)
        self.pages = [
            ("home", HomePage(self)),
            ("apps", ColumnsPage("applications-other", _("Applications"),
                                 _("Les applications Windows présentes dans le menu Applications et les applications "
                                   "disponibles à l'installation."),
                                 [("mine", AppsPage(self))], [("install", InstallPage(self))])),
            ("files", ColumnsPage("folder", _("Fichiers"),
                                  _("Les dossiers Linux partagés avec Windows et les types de fichiers ouverts dans "
                                    "les applications Windows."),
                                  [("folders", FoldersPage(self))], [("types", FilesPage(self))])),
            ("display", PerformancePage(self)),
            ("windows", WindowsPage(self)),
            ("restoration", RestorationPage(self)),
            ("help", HelpPage(self)),
            ("settings", SettingsPage(self)),
            ("about", AboutPage(self)),
        ]
        from .guide import PAGE_SECTIONS
        for name, page in self.pages:
            self.stack.add_named(page, name)
            # bouton « ? » : le guide ouvert sur la section de la page
            page.add_help(lambda s=PAGE_SECTIONS.get(name): self.get_application().show_guide(s))
        # page affichée : rattrape le dernier état lu (show_state ne met à jour qu'elle)
        self.stack.connect("notify::visible-child", lambda st, _p: self.state and st.get_visible_child() and
                           st.get_visible_child().update(self.state))
        # page d'ouverture (indicateur, aide, essais) : VASISTAS_PAGE=<nom>
        start = os.environ.get("VASISTAS_PAGE")
        if start and self.stack.get_child_by_name(start):
            # après la barre latérale, qui choisit sa première ligne en se construisant
            self.connect("map", lambda *_: GLib.timeout_add(100, lambda: self.show_page(start) and False))

        # bandeau « nouvelle version », au-dessus des pages
        self.banner_label = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        see = Gtk.Button(label=_("Voir"))
        see.connect("clicked", lambda *_: self.get_application().show_updates(self.release))
        later = Gtk.Button(icon_name="window-close-symbolic", tooltip_text=_("Plus tard"))
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
        self.toast = Granite.Toast(title="")
        self.toast_action = None
        self.toast.connect("default-action", lambda *_a: self.toast_action and self.toast_action())
        overlay = Gtk.Overlay(child=right)
        overlay.add_overlay(self.toast)
        self.set_child(overlay)

        self.poll()
        self.poll_source = GLib.timeout_add_seconds(POLL_S, self.poll)
        self.connect("close-request", lambda *a: GLib.source_remove(self.poll_source) and False)
        self.connect("notify::suspended", lambda *_: self.is_suspended() or self.poll())
        self.check_updates()

    def notify(self, text, action=None, on_action=None):
        """Message éphémère ; avec `action`, un bouton qui appelle on_action()."""
        self.toast.set_title(text)
        self.toast.set_default_action(action)
        self.toast_action = on_action
        self.toast.send_notification()
        return False

    def restart_windows(self):
        """Redémarre Windows après confirmation, en disant ce qui se passe (l'arrêt propre peut
        prendre jusqu'à une minute)."""
        from .companion_common import confirm

        def go():
            self.page("windows").vm_action("restart")
            self.notify(_("Windows s'arrête proprement puis redémarre. L'opération peut prendre une minute."))
            self.poll()
        confirm(self, _("Redémarrer Windows ?"),
                _("Les applications Windows ouvertes seront fermées. Enregistrez vos documents avant de redémarrer."),
                _("Redémarrer"), go)

    def poll(self):
        # fenêtre réduite ou masquée : rien à afficher, l'état est relu à son retour
        if self.is_suspended():
            return True

        def work():
            state = {"pid": vm.pid(), "host": host_ready()}
            GLib.idle_add(self.show_state, state)
        threading.Thread(target=work, daemon=True).start()
        return True

    def show_state(self, state):
        """Seule la page affichée suit l'état (lectures de /proc, de config.json, requêtes à
        l'hôte) ; une page rattrape le dernier état quand elle s'affiche."""
        self.state = state
        page = self.stack.get_visible_child()
        if page is not None:
            page.update(state)
        return False

    # anciens noms de pages (notifications, diagnostic, VASISTAS_PAGE) -> (page, onglet ou sujet)
    MOVED = {"install": ("apps", "install"), "mine": ("apps", "mine"), "folders": ("files", "folders"),
             "types": ("files", "types"), "general": ("windows", "general"),
             "integration": ("windows", "integration"), "slim": ("windows", "slim"),
             "restore": ("restoration", "restore"), "keyboard": ("windows", "keyboard"),
             "printers": ("windows", "printers"), "performance": ("display", None),
             "finetune": ("display", "finetune"), "screens": ("display", "screens"),
             "browser": ("settings", "browser"), "experimental": ("settings", "experimental"),
             "advanced": ("help", None), "diagnose": ("help", "diagnose"), "maintenance": ("help", "maintenance")}

    def show_page(self, name):
        """Affiche la page `name` (ou l'onglet, le sujet d'Avancé qui porte ce nom) et choisit sa
        ligne dans la barre latérale ; renvoie la page ou la section, None si elle n'existe pas."""
        name, sub = self.MOVED.get(name, (name, None))
        page = self.page(name)
        if page is None:
            return None
        self.stack.set_visible_child(page)
        for n, btn in self.tabs.items():
            if btn.get_active() != (n == name):
                btn.set_active(n == name)
        if name not in self.tabs:  # page du menu : aucun onglet allumé
            for btn in self.tabs.values():
                btn.set_active(False)
        return page.open_sub(sub) if sub and hasattr(page, "open_sub") else page

    def page(self, name):
        return dict(getattr(self, "pages", ())).get(name)  # pendant la construction : None

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
        if rel.get("prerelease"):
            text = _("Vasistas {version} (préversion) est disponible.", version=rel["version"])
        else:
            text = _("Vasistas {version} est disponible.", version=rel["version"])
        self.banner_label.set_label(text)
        self.banner.set_reveal_child(True)
        return False


class App(Gtk.Application):
    def __init__(self):
        # HANDLES_COMMAND_LINE : « vasistas companion --page diagnose » ouvre la page dans la fenêtre
        # déjà ouverte (notifications de l'hôte)
        super().__init__(application_id=COMPANION_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        Granite.init()
        gs = Granite.Settings.get_default()
        gtk = Gtk.Settings.get_default()

        def follow(*_):
            # thème choisi dans Paramètres, sinon celui du système
            forced = vm.load_config().get("companion_theme")
            dark = forced == "dark" if forced in ("light", "dark") else \
                gs.get_property("prefers-color-scheme") == Granite.SettingsColorScheme.DARK
            gtk.set_property("gtk-application-prefer-dark-theme", dark)
        self.follow_theme = follow
        follow()
        gs.connect("notify::prefers-color-scheme", follow)
        css = Gtk.CssProvider()
        css.load_from_string(CSS)
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        for name, handler, accels in (("guide", self.show_guide, ["F1"]),
                                      ("tour", self.show_tour, []),
                                      ("whatsnew", self.show_whatsnew, []),
                                      ("updates", lambda: self.show_updates(), []),
                                      ("about", lambda: self.get_active_window().show_page("about"), []),
                                      ("quit", self.quit, ["<Control>q"])):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda *_a, h=handler: h())
            self.add_action(act)
            if accels:
                self.set_accels_for_action(f"app.{name}", accels)

    def do_command_line(self, cmdline):
        args = cmdline.get_arguments()[1:]

        def opt(name):
            return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None
        self.activate()
        win = self.get_active_window()
        page = win.show_page(opt("--page")) if opt("--page") and isinstance(win, Window) else None
        fix = opt("--fix")
        if fix and page is not None and hasattr(page, "run_fix"):
            page.run_fix(fix)
        return 0

    def do_activate(self):
        win = self.get_active_window()
        if win is None:
            win = Window(self)
            win.present()
            # première fois : Windows pas encore installé
            if not vm.DISK.exists() and not vm.load_config().get("setup_done"):
                self.mark_seen()
                self.open_wizard()
                return
            self.maybe_whatsnew()
            return
        win.present()

    # -- accompagnement --

    def mark_seen(self):
        cfg = vm.load_config()
        if cfg.get("seen_version") != VERSION:
            cfg["seen_version"] = VERSION
            vm.save_config(cfg)

    def maybe_whatsnew(self):
        """Nouveautés montrées une fois, au premier lancement après une mise à jour."""
        from . import i18n, whatsnew
        items = whatsnew.pending(vm.load_config(), i18n.current())
        self.mark_seen()
        if items:
            GLib.timeout_add(600, lambda: self.show_whatsnew(items) and False)

    def show_whatsnew(self, items=None):
        from .companion_tour import WhatsNewWindow
        WhatsNewWindow(self.get_active_window(), items).present()

    def show_tour(self):
        from .companion_tour import TourWindow
        TourWindow(self.get_active_window()).present()

    def open_wizard(self):
        from .wizard import Wizard
        win = self.get_active_window()

        def done():
            win.page("apps").open_sub("mine").fill(guestapps.cached())
            win.poll()
        Wizard(win, on_done=done).present()

    def show_guide(self, section=None):
        from .guide import GuideWindow
        GuideWindow(self.get_active_window(), section).present()

    def show_updates(self, release=None):
        from .companion_updates import UpdateWindow
        UpdateWindow(self.get_active_window(), release).present()


def main(argv=None):
    return App().run([sys.argv[0]] + list(argv or []))
