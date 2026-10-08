"""Pages « Écrans et fenêtres » (écran de chaque application, taille, réinitialisation) et
« Intégration au bureau » (apparence, notifications et icônes de Windows, langue) du compagnon."""

import threading

from gi.repository import Gdk, GLib, Granite, Gtk

from . import control, desktop, look, placement, vm
from .companion_common import Section, card, clear, dim, row
from .i18n import _
from .winctl import APP_ID


def ask_host(req, done=None):
    """Requête au socket de contrôle hors du fil GTK ; `done(réponse ou None)` dans le fil GTK."""
    def work():
        try:
            res = control.request(req, timeout=10)
        except OSError:
            res = None
        if done is not None:
            GLib.idle_add(lambda: done(res) and False)
    threading.Thread(target=work, daemon=True).start()


def set_config(key, value):
    cfg = vm.load_config()
    cfg[key] = value
    vm.save_config(cfg)
    # options de l'agent et apparence redonnées à Windows s'il tourne (sans le réveiller)
    ask_host({"refresh_options": True})


def dropdown(labels, selected, on_change):
    dd = Gtk.DropDown.new_from_strings(labels)
    dd.set_selected(max(0, selected))
    dd.connect("notify::selected", lambda d, _p: on_change(d.get_selected()))
    return dd


class ScreensPage(Section):
    """Où s'ouvrent les fenêtres de chaque application, et le bouton qui remet tout en place."""
    __gtype_name__ = "VasistasScreensPage"

    def __init__(self, win):
        super().__init__("preferences-desktop-display", _("Écrans et fenêtres"),
                         _("Écran d'ouverture de chaque application, taille des fenêtres"))
        self.win = win
        reset = Gtk.Button(label=_("Réinitialiser les affichages"))
        reset.add_css_class(Granite.STYLE_CLASS_SUGGESTED_ACTION)
        reset.set_tooltip_text(_("Toutes les fenêtres Windows sont ramenées à 80 % de leur écran au maximum et centrées. "
                                 "Aucune fenêtre n'est fermée."))
        reset.connect("clicked", lambda *_: self.reset())
        self.get_action_area().append(reset)

        self.header(_("Écran d'ouverture"))
        self.default_box = Gtk.Box()
        self.add(row(_("Par défaut"), _("S'applique aux applications sans réglage particulier. « Écran actif » : "
                                        "l'écran sur lequel vous travaillez, comme pour les applications Linux."),
                     self.default_box))
        self.apps_list = self.add(card())
        self.add(dim(_("Si l'écran choisi n'est pas branché, la fenêtre s'ouvre sur l'écran actif.")))

        self.header(_("Taille des fenêtres"))
        s = placement.settings()
        clamp = Gtk.Switch(active=s["clamp"])
        clamp.connect("notify::active", lambda sw, _p: self.save(clamp=sw.get_active()))
        self.add(row(_("Jamais plus grande que l'écran"),
                     _("Une fenêtre trop grande pour l'écran où elle s'ouvre est réduite à 90 % de l'écran."),
                     clamp))
        auto = Gtk.Switch(active=s["reset_on_change"])
        auto.connect("notify::active", lambda sw, _p: self.save(reset_on_change=sw.get_active()))
        self.add(row(_("Remettre en place au branchement d'un écran"),
                     _("Quand un écran est branché ou débranché, les fenêtres sont recentrées comme avec "
                       "« Réinitialiser les affichages »."), auto))
        forget = Gtk.Button(label=_("Effacer"))
        forget.connect("clicked", lambda *_a: (placement.forget_all(),
                                              self.win.notify(_("Tailles et écrans mémorisés effacés."))))
        self.add(row(_("Tailles mémorisées"),
                     _("Vasistas retient l'écran et la taille de chaque application pour chaque "
                       "combinaison d'écrans (portable seul, portable et écran externe…)."), forget))

        monitors = Gdk.Display.get_default().get_monitors()
        monitors.connect("items-changed", lambda *_: self.fill())
        self.fill()

    def save(self, **values):
        placement.save_settings(values)
        ask_host({"refresh_options": True})

    def choices(self, with_default):
        """[(valeur, libellé)] : écran actif, dernier écran, puis chaque écran branché."""
        out = [(None, _("Comme par défaut"))] if with_default else []
        out += [(placement.AUTO, _("Écran actif")), (placement.LAST, _("Dernier écran utilisé"))]
        for monitor in placement.gdk_monitors():
            out.append((monitor.get_connector(), placement.label(monitor)))
        return out

    def chooser(self, current, with_default, on_change):
        choices = self.choices(with_default)
        values = [v for v, _l in choices]
        if current not in values:
            # écran choisi mais pas branché : gardé dans la liste pour ne pas perdre le réglage
            choices.append((current, _("{name} (débranché)", name=current)))
            values.append(current)
        return dropdown([lbl for _v, lbl in choices], values.index(current),
                        lambda i: on_change(values[i]))

    def fill(self):
        s = placement.settings()
        clear(self.default_box)
        self.default_box.append(self.chooser(s["default"], False, self.set_default))
        clear(self.apps_list)
        # seulement les applications du menu (et celles déjà réglées) : pas les processus de
        # Windows vus en passant (COM Surrogate, WebView2, consoles…)
        registry = {app: entry for app, entry in desktop.load_registry().items()
                    if desktop.in_menu(app) or app in s["apps"]}
        if not registry:
            self.apps_list.append(Gtk.Label(label=_("Aucune application Windows dans le menu pour l'instant."),
                                            margin_top=12, margin_bottom=12))
            return
        for app, entry in sorted(registry.items(), key=lambda kv: (kv[1].get("name") or kv[0]).lower()):
            icon = Gtk.Image(pixel_size=32)
            if desktop.app_icon_path(app).exists():
                icon.set_from_file(str(desktop.app_icon_path(app)))
            else:
                icon.set_from_icon_name(APP_ID)
            line = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
            line.append(icon)
            line.append(Gtk.Label(label=entry.get("name") or app, xalign=0, hexpand=True))
            line.append(self.chooser(s["apps"].get(app), True, lambda v, a=app: self.set_app(a, v)))
            self.apps_list.append(line)

    def set_default(self, value):
        self.save(default=value)

    def set_app(self, app, value):
        apps = placement.settings()["apps"]
        if value is None:
            apps.pop(app, None)
        else:
            apps[app] = value
        self.save(apps=apps)

    def reset(self):
        def done(res):
            if res is None:
                self.win.notify(_("Aucune fenêtre Windows ouverte."))
            elif res.get("error"):
                self.win.notify(res["error"])
            else:
                self.win.notify(_("{n} fenêtre(s) remise(s) en place.", n=res.get("count", 0)))
        ask_host({"reset_windows": True}, done)


class IntegrationPage(Section):
    """Ce que Windows prend du bureau, et ce qu'il lui renvoie."""
    __gtype_name__ = "VasistasIntegrationPage"

    def __init__(self, win):
        super().__init__("preferences-desktop-theme", _("Intégration au bureau"), "")
        self.win = win
        cfg = vm.load_config()

        self.header(_("Apparence et intégration"))
        theme = Gtk.Switch(active=cfg.get("win_theme", True) is not False)
        theme.connect("notify::active", lambda sw, _p: set_config("win_theme", sw.get_active()))
        self.add(row(_("Windows suit le style du bureau"),
                     _("Le mode sombre et la couleur d'accent d'elementary sont appliqués aux applications Windows "
                       "dès qu'ils sont modifiés dans les Paramètres."), theme))
        smoothing = cfg.get("font_smoothing", "auto")
        labels = [_("Ne pas modifier"), _("Niveaux de gris, comme Linux"), _("ClearType")]
        self.add(row(_("Lissage des polices"),
                     _("Les niveaux de gris donnent un texte proche de celui des applications Linux, "
                       "sans liseré coloré."),
                     dropdown(labels, look.SMOOTHING.index(smoothing) if smoothing in look.SMOOTHING else 0,
                              lambda i: set_config("font_smoothing", look.SMOOTHING[i]))))

        notes = Gtk.Switch(active=cfg.get("win_notifications", True) is not False)
        notes.connect("notify::active", lambda sw, _p: set_config("win_notifications", sw.get_active()))
        self.add(row(_("Notifications de Windows sur le bureau"),
                     _("Les bannières d'Outlook, de Teams ou des mises à jour deviennent des notifications "
                       "du bureau. Cliquer dessus ouvre l'application sur le message."), notes))
        tray = Gtk.Switch(active=cfg.get("win_tray", True) is not False)
        tray.connect("notify::active", lambda sw, _p: set_config("win_tray", sw.get_active()))
        self.add(row(_("Icônes de la zone de notification"),
                     _("OneDrive, Teams et les autres icônes de la barre des tâches de Windows apparaissent dans "
                       "le panneau. Un clic sur une icône a le même effet que dans Windows."), tray))
