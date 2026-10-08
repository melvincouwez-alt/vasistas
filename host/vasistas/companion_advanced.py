"""Réglages fins de l'affichage (Affichage > Options avancées), maintenance, et page « Aide et
diagnostic » du compagnon."""

from gi.repository import Gtk

from . import power, vm, winctl
from .companion_common import RESOURCES, Page, Section, dim, row
from .companion_display import RestartBanner
from .i18n import _


class FineTuneSection(Section):
    """Réglages que le curseur Performances pose d'un coup, un par un."""
    __gtype_name__ = "VasistasFineTuneSection"

    def __init__(self, win):
        super().__init__("utilities-system-monitor", _("Réglages fins"),
                         _("Détail des paramètres réglés par les deux curseurs de performance."))
        self.win = win
        self.syncing = False
        self.banner = RestartBanner(win)
        self.column.append(self.banner)
        self.drops = {}

        self.header(_("Performance de la machine"))
        keys = [k for k, _l, _d in RESOURCES]
        self.drop("resources", [_(label) for _k, label, _d in RESOURCES], keys, restart=True,
                  title=_("Puissance allouée à Windows"),
                  sub=_("Nombre de cœurs et quantité de mémoire attribués à Windows au démarrage. La mémoire que "
                        "Windows n'utilise pas est rendue à Linux en continu."))
        auto = self.auto = Gtk.Switch()
        auto.connect("notify::active", self.on_auto)
        self.add(row(_("Adapter à l'alimentation"),
                     _("Sur batterie ou en mode Économie du système, Windows utilise uniquement les cœurs les plus "
                       "économes et dispose de moins de mémoire."), auto))

        self.header(_("Images"))
        self.drop("timer", [_("Sur secteur seulement"), _("Toujours"), _("Jamais")], list(power.TIMERS),
                  title=_("Minuterie précise (1 ms)"),
                  sub=_("Windows se réveille toutes les millisecondes au lieu de toutes les 15,6 ms : images plus "
                        "régulières, consommation légèrement plus élevée. Prend effet immédiatement."))
        self.drop("occluded_ms", [_("Selon la puissance")] + [_("{n} ms", n=ms) for ms in power.OCCLUDED_CHOICES],
                  [None, *power.OCCLUDED_CHOICES], title=_("Fenêtres recouvertes"),
                  sub=_("Délai entre deux images d'une fenêtre cachée derrière une autre. Selon la puissance : "
                        "1 s en économie, 500 ms en équilibré, 250 ms en performances."))
        vsync_labels = {0: _("Désactivée"), 1: _("60 Hz annoncés, sans cadence"),
                        3: _("Cadence sans signal à Windows"), 7: _("Complète (60 Hz)")}
        self.drop("vsync", [vsync_labels[m] for m in power.VSYNC_MODES], list(power.VSYNC_MODES), restart=True,
                  title=_("Synchronisation verticale"),
                  sub=_("Avec le pilote d'écran modifié (page Préférences, section Expérimental), Windows compose ses "
                        "images au rythme d'un écran à 60 Hz. Prend effet au prochain démarrage de Windows."))

        self.header(_("Veille et arrêt"))
        from . import sleep
        pause = [(0, _("Jamais"))] + [(m, _("{n} minutes", n=m)) for m in (5, 10, 15, 30)] + [(60, _("1 heure"))]
        self.drop("sleep_minutes", [t for _v, t in pause], [v for v, _t in pause],
                  title=_("Mettre Windows en veille après"),
                  sub=_("Délai sans fenêtre Windows au premier plan et sans clic dans une fenêtre Windows. Windows "
                        "sort de veille immédiatement au premier clic."))
        self.drop("sleep_without_windows_minutes", [t for _v, t in pause], [v for v, _t in pause],
                  title=_("Mettre en veille sans fenêtre ouverte après"),
                  sub=_("Délai sans aucune application Windows ouverte. En veille, Windows n'utilise plus le "
                        "processeur. Le lancement de la prochaine application Windows sort Windows de veille en moins "
                        "d'une seconde."))
        auto_off = [(m, _("Jamais") if not m else _("{n} minutes", n=m) if m < 60 else
                     _("1 heure") if m == 60 else _("{n} heures", n=m // 60)) for m in sleep.AUTO_SHUTDOWN_CHOICES]
        self.drop("auto_shutdown_min", [t for _v, t in auto_off], [v for v, _t in auto_off],
                  title=_("Arrêter Windows sans fenêtre ouverte après"),
                  sub=_("Vasistas arrête proprement Windows lorsqu'aucune application Windows n'est ouverte pendant ce "
                        "délai. Windows redémarre automatiquement à l'ouverture de la prochaine application Windows."))
        bat = self.battery_only = Gtk.Switch()
        bat.connect("notify::active", lambda sw, _p: self.syncing or self.save("sleep_on_battery_only",
                                                                               sw.get_active()))
        self.add(row(_("Veille et arrêt seulement sur batterie"),
                     _("Sur secteur, Windows reste éveillé tant qu'une fenêtre Windows est ouverte."), bat))

        back = Gtk.Button(label=_("Revenir au mode Équilibré"))
        back.connect("clicked", lambda *_a: self.reset())
        self.get_action_area().append(back)
        self.connect("map", lambda *_a: self.sync())

    def drop(self, key, labels, values, title, sub, restart=False):
        d = Gtk.DropDown.new_from_strings(labels)

        def on(dd, _p):
            if self.syncing:
                return
            self.save(key, values[dd.get_selected()])
            if key == "resources":
                winctl.set_resources(values[dd.get_selected()])
            if restart:
                self.banner.need(key)
        d.connect("notify::selected", on)
        self.drops[key] = (d, values)
        self.add(row(title, sub, d))

    @staticmethod
    def save(key, value):
        cfg = vm.load_config()
        if value is None:
            cfg.pop(key, None)
        else:
            cfg[key] = value
        vm.save_config(cfg)

    def on_auto(self, sw, _p):
        if not self.syncing:
            self.save("resources_auto", sw.get_active())

    def reset(self):
        cfg = vm.load_config()
        restart = power.apply_mode(cfg, 1)
        vm.save_config(cfg)
        self.banner.need(*restart)
        self.sync()

    def sync(self):
        from . import sleep
        cfg = vm.load_config()
        current = dict(power.PRESET_DEFAULTS)
        current.update(sleep.settings())
        current.update({k: cfg[k] for k in cfg if k in self.drops})
        self.syncing = True
        try:
            for key, (d, values) in self.drops.items():
                v = current.get(key)
                d.set_selected(values.index(v) if v in values else 0)
            self.auto.set_active(cfg.get("resources_auto", True) is not False)
            self.battery_only.set_active(sleep.settings()["sleep_on_battery_only"])
        finally:
            self.syncing = False
        self.banner.refresh()


class MaintenanceSection(Section):
    """Ce qui se fait rarement : reconfigurer Windows pour Vasistas, l'assistant."""
    __gtype_name__ = "VasistasMaintenanceSection"

    def __init__(self, win):
        super().__init__("applications-utilities", _("Maintenance"), "")
        self.win = win
        self.header(_("Maintenance"))
        self.add(dim(_("À utiliser si une fonction de Vasistas ne fonctionne plus correctement, ou pour réinstaller "
                       "Windows.")))
        self.configure_btn = Gtk.Button(label=_("Reconfigurer"))
        self.configure_btn.connect("clicked", lambda *_a: self.win.page("windows").configure())
        self.add(row(_("Configuration de Windows pour Vasistas"),
                     _("Réinstalle l'agent, les pilotes et les réglages nécessaires (dossiers partagés, pas "
                       "d'écran de verrouillage…) sans toucher à vos applications ni à vos fichiers."),
                     self.configure_btn))
        from .companion_windows import CONFIGURE
        self.configure_btn.set_visible(CONFIGURE.exists())
        wiz = Gtk.Button(label=_("Ouvrir l'assistant"))
        wiz.connect("clicked", lambda *_a: self.win.get_application().open_wizard())
        self.add(row(_("Assistant de configuration"),
                     _("Installer Windows, ses pilotes et les applications, étape par étape."), wiz))

    def update(self, state):
        from .companion_common import guest_ready
        self.configure_btn.set_sensitive(guest_ready(state))


class HelpPage(Page):
    """Aide et diagnostic : guide, visite, nouveautés, diagnostic avec rapport, maintenance."""
    __gtype_name__ = "VasistasHelpPage"

    def __init__(self, win):
        super().__init__("help-contents", _("Aide et diagnostic"),
                         _("Documentation de Vasistas, vérification du fonctionnement et réparation "
                           "des problèmes."), two_columns=True)
        self.win = win
        from .companion_diagnose import DiagnosePage
        self.header(_("Aide"))
        helps = Gtk.Box(spacing=6)
        for label, action in ((_("Guide rapide"), "show_guide"), (_("Visite guidée"), "show_tour"),
                              (_("Quoi de neuf"), "show_whatsnew")):
            b = Gtk.Button(label=label)
            b.connect("clicked", lambda _b, a=action: getattr(self.win.get_application(), a)())
            helps.append(b)
        self.add(helps)
        self.sections = {"maintenance": MaintenanceSection(win), "diagnose": DiagnosePage(win)}
        self.column.append(self.sections["maintenance"])
        self.use_right()
        self.column.append(self.sections["diagnose"])

    def open_sub(self, name):
        return self.sections.get(name)

    def update(self, state):
        for section in self.sections.values():
            section.update(state)
