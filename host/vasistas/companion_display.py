"""Page « Affichage » du compagnon, en tableau de bord : curseur d'affichage à trois modes,
modes réservés à des applications, Windows en direct (aperçu de l'écran, images par seconde,
processeur, mémoire), veille, moteur de rendu et carte graphique dédiée."""

import threading
import time

from gi.repository import GLib, Granite, Gtk

from . import control, desktop, power, vm
from .companion_common import (advanced_card, card, clear, columns, dash_card, dim, guest_ready, mode_chip, row, texture_from_b64,
                               tile)
from .i18n import N_, _
from .winctl import APP_ID

SLEEP_STEPS = [(5, N_("5 min")), (15, N_("15 min")), (30, N_("30 min")), (60, N_("1 heure")), (0, N_("Jamais"))]


def mode_name(key):
    return _(next(m[1] for m in power.MODES if m[0] == key))


def app_name(app, registry):
    entry = registry.get(app) or {}
    return entry.get("name") or ("Power BI" if app == "pbidesktop" else app)


class RestartBanner(Gtk.Box):
    """Bandeau « Redémarrez Windows pour appliquer… » des réglages pris au démarrage."""
    NAMES = {"resources": N_("puissance allouée"), "vsync": N_("synchronisation verticale")}

    def __init__(self, win):
        super().__init__(spacing=12, margin_bottom=6, visible=False)
        self.win = win
        self.keys = set()
        self.add_css_class(Granite.STYLE_CLASS_CARD)
        self.add_css_class(Granite.STYLE_CLASS_ROUNDED)
        inner = Gtk.Box(spacing=12, margin_top=8, margin_bottom=8, margin_start=12, margin_end=8, hexpand=True)
        inner.append(Gtk.Image(icon_name="system-reboot", pixel_size=32))
        self.label = Gtk.Label(xalign=0, wrap=True, hexpand=True)
        inner.append(self.label)
        btn = self.button = Gtk.Button(label=_("Redémarrer Windows"), valign=Gtk.Align.CENTER)
        btn.add_css_class(Granite.STYLE_CLASS_SUGGESTED_ACTION)
        btn.connect("clicked", lambda *_a: self.restart())
        inner.append(btn)
        self.append(inner)

    def need(self, *keys):
        self.keys.update(keys)
        self.refresh()

    def refresh(self):
        windows = self.win.page("windows")
        restarting = windows is not None and windows.busy in ("restart", "start")
        if restarting:
            self.keys.clear()
            self.label.set_label(_("Redémarrage de Windows en cours : Windows s'arrête proprement puis redémarre."))
            self.button.set_sensitive(False)
            self.set_visible(True)
            return
        self.button.set_sensitive(True)
        if not self.keys or not vm.pid():
            self.set_visible(False)
            return
        self.label.set_label(_("Redémarrez Windows pour appliquer les réglages suivants : {what}.",
                               what=", ".join(_(self.NAMES[k]) for k in sorted(self.keys))))
        self.set_visible(True)

    def restart(self):
        self.win.restart_windows()
        GLib.timeout_add(300, lambda: self.refresh() and False)


# tuiles sous le curseur : (batterie, images, fenêtres cachées) de chaque mode
TILES = {
    "eco": (N_("La plus longue"), N_("Plus espacées"), N_("Chaque seconde")),
    "balanced": (N_("Normale"), N_("Rythme 60 Hz"), N_("Selon la machine")),
    "smooth": (N_("Plus courte"), N_("60 par seconde"), N_("4 fois par seconde")),
}
FPS_POINTS = 40


class Sparkline(Gtk.DrawingArea):
    """Courbe des dernières images par seconde."""

    def __init__(self):
        super().__init__(content_height=48, hexpand=True)
        self.values = []
        self.set_draw_func(self.draw)

    def push(self, v):
        self.values = (self.values + [v])[-FPS_POINTS:]
        self.queue_draw()

    def draw(self, _area, cr, w, h):
        if len(self.values) < 2:
            return
        top = max(60, max(self.values))
        step = w / (FPS_POINTS - 1)
        x0 = w - step * (len(self.values) - 1)
        cr.set_source_rgb(0x6F / 255, 0xA8 / 255, 1)
        cr.set_line_width(2)
        for i, v in enumerate(self.values):
            y = h - 2 - (h - 4) * min(v, top) / top
            (cr.move_to if i == 0 else cr.line_to)(x0 + i * step, y)
        cr.stroke()


class LivePanel(Gtk.Box):
    """Carte « En direct » : aperçu de l'écran de Windows, images par seconde, processeur et
    mémoire de QEMU ; refresh(state) à chaque relecture de l'état (toutes les 2 s). `compact` (Accueil) :
    aperçu plus bas, images par seconde en tuile, sans courbe, pour que la page tienne sans défiler."""

    def __init__(self, compact=False):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.sample = self.frames = None
        self.busy = False
        box, head = dash_card(_("En direct"), "video-display-symbolic", "teal")
        self.append(box)
        self.dot = Gtk.Box(valign=Gtk.Align.CENTER)
        self.dot.add_css_class("live-dot")
        self.state_label = dim("")
        self.state_label.set_wrap(False)
        head.append(self.dot)
        head.append(self.state_label)
        self.preview = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True)
        self.preview.add_css_class("preview")
        self.preview.set_tooltip_text(_("Écran de Windows en ce moment"))
        empty = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER)
        empty.append(Gtk.Image(icon_name="video-display-symbolic", pixel_size=48, opacity=0.4))
        text = dim(_("L'aperçu de l'écran de Windows s'affiche dans ce cadre lorsque Windows est en marche."))
        text.set_justify(Gtk.Justification.CENTER)
        text.set_xalign(0.5)
        empty.append(text)
        self.stack = Gtk.Stack(height_request=110 if compact else 190, transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.stack.add_named(empty, "empty")
        self.stack.add_named(self.preview, "shot")
        self.spark = Sparkline()
        if compact:
            # aperçu à gauche, chiffres en colonne à droite
            self.stack.set_hexpand(True)
            figures = Gtk.Grid(row_spacing=6, column_spacing=12, valign=Gtk.Align.CENTER)
            figures.add_css_class("tile")
            labels = []
            for i, name in enumerate((_("Images par seconde"), _("Processeur"), _("Mémoire"))):
                figures.attach(dim(name), 0, i, 1, 1)
                value = Gtk.Label(label="–", xalign=1)
                value.add_css_class("tile-value")
                figures.attach(value, 1, i, 1, 1)
                labels.append(value)
            self.fps_label, self.cpu_label, self.mem_label = labels
            side = Gtk.Box(spacing=12)
            side.append(self.stack)
            side.append(figures)
            box.append(side)
            return
        box.append(self.stack)
        fps_row = Gtk.Box()
        fps_row.append(Gtk.Label(label=_("Images par seconde"), xalign=0, hexpand=True))
        self.fps_label = Gtk.Label(label="–")
        self.fps_label.add_css_class("tile-value")
        fps_row.append(self.fps_label)
        box.append(fps_row)
        box.append(self.spark)
        tiles = Gtk.Box(spacing=10, homogeneous=True)
        t, self.cpu_label = tile(_("Processeur"))
        tiles.append(t)
        t, self.mem_label = tile(_("Mémoire"))
        tiles.append(t)
        box.append(tiles)

    def refresh(self, state):
        from .companion_home import qemu_usage
        ready = guest_ready(state)
        running = bool(state.get("pid"))
        self.dot.set_css_classes(["live-dot"] if ready else ["live-dot", "warn" if running else "off"])
        self.state_label.set_label(_("En marche") if ready else _("Démarrage…") if running else _("Arrêté"))
        pid = state.get("pid")
        usage = qemu_usage(pid, self.sample) if pid else None
        self.sample = usage and usage["sample"]
        cpu = usage and usage["cpu"]
        self.cpu_label.set_label("–" if cpu is None else _("{n} %", n=round(cpu)))
        self.mem_label.set_label("–" if not usage else
                                 _("{n} Go", n=f"{usage['rss_mb'] / 1024:.1f}".replace(".", ",")))
        if not ready:
            self.frames = None
            self.fps_label.set_label("–")
            self.preview.set_paintable(None)
            self.stack.set_visible_child_name("empty")
            return
        if self.busy or not self.get_mapped():
            return
        self.busy = True

        def work():
            try:
                stats = control.request({"stats": True}, timeout=1)
                shot = control.request({"thumbnail": 480}, timeout=2).get("png")
            except (OSError, ValueError):
                stats, shot = {}, None
            GLib.idle_add(self.show, stats, shot)
        threading.Thread(target=work, daemon=True).start()

    def show(self, stats, shot):
        self.busy = False
        now = time.monotonic()
        frames = stats.get("frames")
        if frames is not None and self.frames is not None and now > self.frames[1]:
            fps = (frames - self.frames[0]) / (now - self.frames[1])
            self.fps_label.set_label(str(round(fps)))
            self.spark.push(fps)
        if frames is not None:
            self.frames = (frames, now)
        texture = texture_from_b64(shot) if shot else None
        self.preview.set_paintable(texture)
        self.stack.set_visible_child_name("shot" if texture else "empty")
        return False


class PerformancePage(Gtk.Box):
    """Tableau de bord : à gauche ce qu'on règle, à droite ce qui se passe."""
    __gtype_name__ = "VasistasPerformancePage"

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.syncing = False
        page, outer, left, right = columns()
        self.append(page)
        self.banner = RestartBanner(win)
        outer.prepend(self.banner)
        left.append(self.mode_card())
        left.append(self.rules_card())
        self.live = LivePanel()
        right.append(self.live)
        right.append(self.options_card())
        from .companion_advanced import FineTuneSection
        from .companion_screens import ScreensPage
        self.sections = {"finetune": FineTuneSection(win), "screens": ScreensPage(win)}
        self.advanced = advanced_card(*self.sections.values())
        outer.append(self.advanced)
        self.connect("map", lambda *_a: self.sync())
        self.sync()

    def open_sub(self, name):
        section = self.sections.get(name)
        if section is not None:
            self.advanced.get_first_child().set_expanded(True)
        return section

    def add_help(self, on_click):
        btn = Gtk.Button(label="?", valign=Gtk.Align.CENTER, tooltip_text=_("Aide sur cette page"))
        btn.add_css_class(Granite.STYLE_CLASS_CIRCULAR)
        btn.connect("clicked", lambda *_a: on_click())
        self.mode_head.append(btn)
        return btn

    # -- mode d'affichage --

    def mode_card(self):
        box, self.mode_head = dash_card(_("Performance d'affichage"), "utilities-system-monitor-symbolic", "blue")
        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, len(power.MODES) - 1, 1)
        self.scale.add_css_class("modes")
        self.scale.set_draw_value(False)
        self.scale.set_round_digits(0)
        self.scale.set_margin_start(12)
        self.scale.set_margin_end(12)
        for i, m in enumerate(power.MODES):
            self.scale.add_mark(i, Gtk.PositionType.BOTTOM, _(m[1]))
        self.scale.connect("value-changed", self.on_mode)
        box.append(self.scale)
        tiles = Gtk.Box(spacing=10, homogeneous=True)
        self.tiles = []
        self.tile_boxes = []
        for name in (_("Batterie"), _("Images"), _("Fenêtres cachées")):
            t, value = tile(name)
            tiles.append(t)
            self.tiles.append(value)
            self.tile_boxes.append(t)
        box.append(tiles)
        self.mode_what = Gtk.Label(xalign=0, wrap=True)
        box.append(self.mode_what)
        self.now_label = dim("")
        box.append(self.now_label)
        return box

    def show_mode(self, index):
        if index is None:
            for value in self.tiles:
                value.set_label("–")
            for t in self.tile_boxes:
                t.set_css_classes(["tile"])
            self.mode_what.set_label(_("Des réglages fins ont été modifiés dans les options avancées. Déplacez le "
                                       "curseur pour revenir à l'un des trois modes prédéfinis."))
            return
        key, _n, what, _b, _p, _v, _pr = power.MODES[index]
        for value, text in zip(self.tiles, TILES[key]):
            value.set_label(_(text))
        for t in self.tile_boxes:
            t.set_css_classes(["tile", f"mode-{key}", "mode-card"])
        self.mode_what.set_label(_(what))

    def on_mode(self, scale):
        if self.syncing:
            return
        i = round(scale.get_value())
        cfg = vm.load_config()
        if power.mode_index(cfg) == i:
            return
        restart = power.apply_mode(cfg, i)
        vm.save_config(cfg)
        self.banner.need(*restart)
        self.show_mode(i)

    def sync(self):
        """Contrôles remis d'après config.json (Avancé peut les avoir changés)."""
        cfg = vm.load_config()
        self.syncing = True
        try:
            i = power.mode_index(cfg)
            if i is not None:
                self.scale.set_value(i)
            self.show_mode(i)
            minutes = [m for m, _l in SLEEP_STEPS]
            if cfg.get("sleep_minutes", 15) in minutes:
                self.sleep_drop.set_selected(minutes.index(cfg.get("sleep_minutes", 15)))
        finally:
            self.syncing = False
        self.fill_rules(cfg)
        self.banner.refresh()

    # -- modes réservés --

    def rules_card(self):
        box, head = dash_card(_("Modes par application"), "view-app-grid-symbolic", "purple")
        inner = Gtk.Box(spacing=6)
        inner.append(Gtk.Label(label=_("Ajouter")))
        inner.append(Gtk.Image(icon_name="pan-down-symbolic"))
        self.add_btn = Gtk.MenuButton(child=inner, popover=Gtk.Popover(), valign=Gtk.Align.CENTER)
        head.append(self.add_btn)
        box.append(dim(_("Lorsqu'une application de cette liste est au premier plan, son mode remplace celui du "
                         "curseur. Le nombre de cœurs et la quantité totale de mémoire restent ceux définis au "
                         "démarrage de Windows.")))
        self.rules = card()
        box.append(self.rules)
        return box

    def fill_rules(self, cfg=None):
        cfg = cfg or vm.load_config()
        modes = power.app_modes(cfg)
        reg = desktop.load_registry()
        clear(self.rules)
        if not modes:
            self.rules.append(Gtk.Label(label=_("Aucun mode par application : toutes les applications suivent le "
                                                "curseur."),
                                        margin_top=12, margin_bottom=12))
        active = getattr(self, "active_rule_app", None)
        for app, mode in sorted(modes.items(), key=lambda kv: app_name(kv[0], reg).lower()):
            line = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
            icon = Gtk.Image(pixel_size=32)
            path = desktop.app_icon_path(app)
            if path.exists():
                icon.set_from_file(str(path))
            else:
                icon.set_from_icon_name(APP_ID)
            line.append(icon)
            line.append(Gtk.Label(label=app_name(app, reg), xalign=0, hexpand=True))
            if app == active:
                line.append(mode_chip(mode, _("Actif")))
            keys = [m[0] for m in power.MODES]
            drop = Gtk.DropDown.new_from_strings([_(m[1]) for m in power.MODES])
            drop.set_selected(keys.index(mode))
            drop.set_valign(Gtk.Align.CENTER)
            drop.connect("notify::selected", lambda d, _p, a=app: self.set_rule(a, keys[d.get_selected()]))
            line.append(drop)
            rm = Gtk.Button(icon_name="edit-delete-symbolic", valign=Gtk.Align.CENTER,
                            tooltip_text=_("Suivre le curseur"))
            rm.add_css_class(Granite.STYLE_CLASS_FLAT)
            rm.connect("clicked", lambda _b, a=app: self.set_rule(a, None))
            line.append(rm)
            self.rules.append(line)

        # applications proposées : celles déjà vues dans Windows, sans mode réservé
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=6, margin_bottom=6,
                      margin_start=6, margin_end=6)
        choices = [(a, app_name(a, reg)) for a in reg if power.canonical(a) not in modes]
        for app, name in sorted(choices, key=lambda c: c[1].lower()):
            b = Gtk.Button(label=name)
            b.add_css_class(Granite.STYLE_CLASS_FLAT)
            b.get_child().set_xalign(0)
            b.connect("clicked", lambda _b, a=app: (self.add_btn.popdown(), self.set_rule(a, "smooth")))
            box.append(b)
        if not choices:
            box.append(dim(_("Les applications Windows apparaissent dans cette liste après leur première ouverture.")))
        self.add_btn.get_popover().set_child(Gtk.ScrolledWindow(
            child=box, propagate_natural_height=True, max_content_height=360,
            hscrollbar_policy=Gtk.PolicyType.NEVER))

    def set_rule(self, app, mode):
        cfg = vm.load_config()
        modes = power.app_modes(cfg)
        if mode is None:
            modes.pop(power.canonical(app), None)
        else:
            modes[power.canonical(app)] = mode
        cfg["app_modes"] = modes
        cfg.pop("heavy_apps", None)
        vm.save_config(cfg)
        GLib.idle_add(lambda: self.fill_rules() and False)  # pas pendant le signal du bouton

    # -- veille et options --

    def options_card(self):
        box, _head = dash_card(_("Veille et options"), "preferences-system-symbolic", "slate")
        self.sleep_drop = Gtk.DropDown.new_from_strings([_(lbl) for _m, lbl in SLEEP_STEPS])
        self.sleep_drop.connect("notify::selected", self.on_sleep)
        box.append(row(_("Veille après"), _("Windows sort de veille au premier clic dans une "
                                            "fenêtre Windows."), self.sleep_drop))

        cur = vm.load_config().get("renderer")
        values = [None, "vulkan", "gl"]
        render = Gtk.DropDown.new_from_strings([_("Automatique"), "Vulkan", "OpenGL"])
        render.set_selected(values.index(cur) if cur in values else 0)

        def on_render(d, _p):
            cfg = vm.load_config()
            v = values[d.get_selected()]
            if v is None:
                cfg.pop("renderer", None)
            else:
                cfg["renderer"] = v
            vm.save_config(cfg)
        render.connect("notify::selected", on_render)
        box.append(row(_("Moteur de rendu"), _("Choisir OpenGL si l'affichage clignote. Prend effet au prochain "
                                               "lancement de Vasistas."), render))

        keys = ["off", "auto", "always"]
        gpu = Gtk.DropDown.new_from_strings([_("Jamais"), _("Sur secteur"), _("Dès que possible")])
        cfg = vm.load_config()
        gpu.set_selected(keys.index(cfg.get("gpu", "off")) if cfg.get("gpu", "off") in keys else 0)

        def on_gpu(d, _p):
            c = vm.load_config()
            c["gpu"] = keys[d.get_selected()]
            vm.save_config(c)
            self.update_gpu()
        gpu.connect("notify::selected", on_gpu)
        box.append(row(_("Carte graphique dédiée"), _("Vasistas prête la carte graphique dédiée à Windows au démarrage "
                                                      "de Windows, si Linux ne l'utilise pas."), gpu))
        self.gpu_label = dim("")
        box.append(self.gpu_label)
        self.gpu_install = Gtk.Button(label=_("Copier la commande d'installation"), halign=Gtk.Align.START,
                                      visible=False)
        self.gpu_install.connect("clicked", lambda *_a: self.copy_install())
        box.append(self.gpu_install)
        self.update_gpu()
        return box

    def on_sleep(self, d, _p):
        if self.syncing:
            return
        cfg = vm.load_config()
        cfg["sleep_minutes"] = SLEEP_STEPS[d.get_selected()][0]
        vm.save_config(cfg)

    def copy_install(self):
        self.get_display().get_clipboard().set(f"sudo {desktop.HOST_DIR / 'tools' / 'install-gpu-helper.sh'}")
        self.win.notify(_("Commande copiée : collez-la dans un terminal"))

    def update_gpu(self):
        def work():
            from . import gpu
            state, card_info = gpu.status()
            GLib.idle_add(self.show_gpu, state, card_info)
        threading.Thread(target=work, daemon=True).start()

    def show_gpu(self, state, card_info):
        from . import gpu
        text = _(gpu.MESSAGES[state])
        if card_info:
            text = _("{card} : {message}", card=card_info['name'].split(' (rev')[0],
                     message=text[0].lower() + text[1:])
        if state == "helper":
            text += " " + _("Cet assistant nécessite une installation unique avec sudo.")
        if vm.gpu_active():
            text = _("La carte graphique dédiée est actuellement prêtée à Windows.")
        self.gpu_label.set_label(text)
        self.gpu_install.set_visible(state == "helper")
        return False

    # -- état --

    def update(self, state):
        host = state.get("host") or {}
        p = host.get("power") or {}
        running = bool(state.get("pid"))
        rule_app = None
        if not running or not p.get("profile"):
            self.now_label.set_label(_("Windows est arrêté : le mode sera appliqué au prochain démarrage de Windows."))
        elif p.get("rule"):
            self.now_label.set_label(_("En ce moment : mode {mode}, défini pour l'application au premier plan.",
                                       mode=mode_name(p["rule"])))
            rule_app = p.get("app")
        else:
            self.now_label.set_label(_("En ce moment : puissance {profile} ({why}).",
                                       profile=power.profile_label(p["profile"]), why=_(p.get("why") or "")))
        if rule_app != getattr(self, "active_rule_app", None):
            self.active_rule_app = rule_app
            self.fill_rules()
        self.live.refresh(state)
        self.banner.refresh()
        for section in self.sections.values():
            section.update(state)
