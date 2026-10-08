"""Pages « Paramètres » (Vasistas lui-même), « Expérimental » et « À propos » du compagnon."""

import os

from gi.repository import Gio, Gtk

from . import desktop, vm
from .companion_common import Page, Section, dim, row
from .companion_screens import dropdown, set_config
from .companion_windows import AUTOSTART, write_autostart
from .i18n import _
from .version import ISSUES, PRERELEASE, VERSION, WEBSITE
from .winctl import APP_ID


def save(key, value):
    cfg = vm.load_config()
    cfg[key] = value
    vm.save_config(cfg)


class SettingsPage(Page):
    """Démarrage, indicateur du panneau, langue, mises à jour de Vasistas, aide."""
    __gtype_name__ = "VasistasSettingsPage"

    def __init__(self, win):
        super().__init__("preferences-system", _("Préférences"), _("Réglages du fonctionnement de Vasistas."),
                         two_columns=True)
        self.win = win
        cfg = vm.load_config()

        self.header(_("Démarrage"))
        auto = Gtk.Switch(active=os.path.exists(AUTOSTART))
        auto.connect("notify::active", self.on_autostart)
        self.add(row(_("Préparer Windows à l'ouverture de session"),
                     _("Windows démarre à l'ouverture de la session Linux : les applications Windows s'ouvrent ensuite "
                       "sans attendre le démarrage de Windows."), auto))
        self.boot_sleep = Gtk.Switch(active=bool(cfg.get("autostart_sleep", False)), sensitive=auto.get_active())
        self.boot_sleep.connect("notify::active", self.on_boot_sleep)
        self.add(row(_("Démarrer en veille"),
                     _("Windows démarre puis se met en veille dès qu'il est prêt : la première application Windows "
                       "s'ouvre presque immédiatement, et Windows n'utilise pas le processeur en "
                       "attendant."), self.boot_sleep))

        self.header(_("Panneau"))
        from . import indicator
        ind = Gtk.Switch(active=indicator.enabled())
        ind.connect("notify::active", lambda sw, _p: indicator.set_enabled(sw.get_active()))
        self.add(row(_("Indicateur dans le panneau"),
                     _("Affiche en haut de l'écran l'état de Windows, les applications récentes et les commandes de "
                       "Windows."), ind))

        self.header(_("Apparence"))
        themes = [None, "light", "dark"]
        current = cfg.get("companion_theme") if cfg.get("companion_theme") in themes else None

        def on_theme(i):
            save("companion_theme", themes[i])
            self.win.get_application().follow_theme()
        self.add(row(_("Thème de Vasistas"),
                     _("Thème clair, thème sombre ou thème du système (réglage d'elementary OS)."),
                     dropdown([_("Comme le système"), _("Clair"), _("Sombre")], themes.index(current), on_theme)))

        self.header(_("Langue"))
        from . import i18n
        values = list(i18n.LANGUAGES)

        def on_lang(i):
            set_config("language", values[i])
            self.win.notify(_("La langue change à la prochaine ouverture de Vasistas."))
        self.add(row(_("Langue de Vasistas"),
                     _("Automatique : langue du système. La langue de Windows est choisie lors de l'installation de "
                       "Windows."),
                     dropdown([_("Automatique"), "Français", "English"], values.index(i18n.setting()), on_lang)))

        self.use_right()
        self.header(_("Mises à jour de Vasistas"))
        upd = Gtk.Switch(active=cfg.get("check_updates", True) is not False)
        upd.connect("notify::active", lambda sw, _p: save("check_updates", sw.get_active()))
        self.add(row(_("Prévenir quand une nouvelle version sort"),
                     _("Vasistas vérifie au plus une fois par jour si une nouvelle version est disponible."), upd))
        now = Gtk.Button(label=_("Rechercher maintenant"), halign=Gtk.Align.START)
        now.connect("clicked", lambda *_a: self.win.get_application().show_updates())
        self.add(now)

        self.sections = {"experimental": ExperimentalSection(win)}
        from . import companion_browser
        if companion_browser.available():
            self.sections["browser"] = companion_browser.BrowserPage(win)
            self.column.append(self.sections["browser"])
        self.column.append(self.sections["experimental"])

    def open_sub(self, name):
        return self.sections.get(name)

    def update(self, state):
        for section in self.sections.values():
            section.update(state)

    def on_autostart(self, sw, _p):
        if sw.get_active():
            write_autostart(bool(vm.load_config().get("autostart_sleep", False)))
        elif os.path.exists(AUTOSTART):
            os.unlink(AUTOSTART)
        self.boot_sleep.set_sensitive(sw.get_active())

    def on_boot_sleep(self, sw, _p):
        save("autostart_sleep", sw.get_active())
        if os.path.exists(AUTOSTART):
            write_autostart(sw.get_active())


class ExperimentalSection(Section):
    """Options en cours de mise au point, chacune avec ce qu'elle risque."""
    __gtype_name__ = "VasistasExperimentalSection"

    def __init__(self, win):
        super().__init__("applications-science", _("Expérimental"), "")
        self.win = win
        cfg = vm.load_config()

        self.header(_("Expérimental"))
        self.add(dim(_("Options en cours de mise au point. En cas de problème, désactivez l'option concernée : le "
                       "fonctionnement précédent est rétabli.")))
        tb = Gtk.Switch(active=bool(cfg.get("native_titlebar", False)))
        # set_config prévient l'hôte, qui applique la barre aux fenêtres déjà ouvertes
        tb.connect("notify::active", lambda sw, _p: set_config("native_titlebar", sw.get_active()))
        self.add(row(_("Barre de titre du bureau"),
                     _("Les fenêtres dont Windows dessine la barre de titre (Explorateur, Bloc-notes classique, boîtes "
                       "de dialogue…) reçoivent la barre de titre du bureau Linux, avec ses coins arrondis et son "
                       "ombre. Office, Edge et les applications qui dessinent leur propre barre de titre conservent la "
                       "leur. Risque : barre de titre en double sur certaines fenêtres."), tb))

        driver = cfg.get("vsync_driver")
        state = (_("Installé dans ce Windows.") if driver else
                 _("Non installé dans ce Windows : le réglage de synchronisation verticale "
                   "n'a aucun effet.") if driver is False else
                 _("État inconnu : démarrez Windows pour vérifier si le pilote est installé."))
        notice = Gtk.Button(label=_("Lire la notice"), valign=Gtk.Align.CENTER)
        notice.connect("clicked", lambda *_a: Gtk.FileLauncher.new(
            Gio.File.new_for_path(str(desktop.HOST_DIR.parent / "docs" / "pilote-maison.md"))).launch(
            self.get_root(), None, None))
        self.add(row(_("Pilote d'écran modifié"),
                     _("Pilote modifié qui fournit à Windows la cadence d'un écran physique à 60 Hz, pour des images "
                       "plus régulières. Ce pilote nécessite le mode test de Windows. Risque : écran noir, réparable "
                       "en revenant à un point de restauration.") + " " + state, notice))


class AboutPage(Page):
    """Version, licence, liens ; ce que l'ancienne fenêtre « À propos » montrait, en page."""
    __gtype_name__ = "VasistasAboutPage"

    def __init__(self, win):
        super().__init__(APP_ID, _("À propos"),
                         _("Les applications Windows d'une machine virtuelle, une fenêtre chacune, sur le "
                           "bureau Linux. Sans bureau à distance (RDP)."), two_columns=True)
        self.win = win
        from . import updates
        mode = {"dev": _("Version de développement (dépôt git)"), "installed": _("Version installée"),
                "other": _("Version en copie locale")}[updates.mode()]
        self.header("Vasistas")
        grid = Gtk.Grid(column_spacing=24, row_spacing=6)
        self.agent = Gtk.Label(label="–", xalign=0, selectable=True)
        lines = [
            (_("Version"), Gtk.Label(label=_("{version} bêta", version=VERSION) if PRERELEASE else VERSION,
                                     xalign=0, selectable=True)),
            (_("Installation"), Gtk.Label(label=mode, xalign=0)),
            (_("Agent dans Windows"), self.agent),
            ("QEMU", Gtk.Label(label=str(vm.QEMU), xalign=0, selectable=True, ellipsize=1)),
            (_("Données"), Gtk.Label(label=str(vm.DATA), xalign=0, selectable=True, ellipsize=1)),
            (_("Licence"), Gtk.Label(label="MIT", xalign=0)),
        ]
        for i, (name, value) in enumerate(lines):
            grid.attach(dim(name), 0, i, 1, 1)
            grid.attach(value, 1, i, 1, 1)
        self.add(grid)

        self.use_right()
        self.header(_("Liens"))
        links = Gtk.Box(spacing=6)
        for label, url in ((_("Page du projet"), WEBSITE), (_("Signaler un problème"), ISSUES)):
            b = Gtk.LinkButton(uri=url, label=label)
            links.append(b)
        self.add(links)
        self.add(dim(_("© 2026 melvincouwez-alt. Windows, Office et Power BI sont des marques de "
                       "Microsoft ; Vasistas n'est ni affilié à Microsoft ni approuvé par lui.")))
        whatsnew = Gtk.Button(label=_("Quoi de neuf"))
        whatsnew.connect("clicked", lambda *_a: self.win.get_application().show_whatsnew())
        self.get_action_area().append(whatsnew)

    def update(self, state):
        host = state.get("host") or {}
        self.agent.set_label(host.get("agent_version") or _("Windows arrêté"))

