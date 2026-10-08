"""Application GTK : une fenêtre native par fenêtre de l'invité."""

import base64

import cairo
import gi

try:
    # régions cairo passées à GTK : paquet python3-gi-cairo ; sans lui, texture renvoyée en entier
    gi.require_foreign("cairo")
    HAVE_CAIRO_REGION = True
except ImportError:
    HAVE_CAIRO_REGION = False
import logging
import os
import struct
import subprocess
import sys
import threading
import time

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import placement, protocol, vm  # noqa: E402
from .channel import Channel  # noqa: E402
from .display import Screen  # noqa: E402
from .i18n import _  # noqa: E402
from .window import CSS, MAP_GRACE_MS, GuestPopup, GuestWindow  # noqa: E402

log = logging.getLogger(__name__)

APP_ID = "io.github.melvincouwez.Vasistas"
DEACTIVATE_DELAY_MS = 80
SCALE_DEBOUNCE_MS = 1000
READY_TIMEOUT_S = 150
SPLASH_MAX_S = 45


def _short_name(name):
    """« Microsoft Word » -> « Word » pour la carte de chargement."""
    return name[len("Microsoft "):] if name.startswith("Microsoft ") else name


def _split_files(cmdline, args):
    """Sépare les fichiers existants (chemins, relatifs au dossier de l'appelant, ou URI file://)
    des autres arguments, passés tels quels à Windows (« /safe »)."""
    paths, rest = [], []
    for a in args:
        path = cmdline.create_file_for_arg(a).get_path() if a else None
        if path and os.path.isfile(path):
            paths.append(path)
        else:
            rest.append(a)
    return paths, rest

NEW_APP_DELAY_MS = 2000
# Après un changement de géométrie, l'écran de QEMU montre encore l'ancienne image quelques
# dizaines de ms : on garde l'ancien cadrage jusqu'à la première zone modifiée qui touche le
# nouveau, au plus RECT_HOLD_S.
RECT_HOLD_S = 0.15
STALL_TICK_MS = 250
STALL_IDLE_TICK_MS = 1000  # aucune fenêtre Windows au premier plan
STALL_MS = 50        # retard au-delà duquel le fil GTK est compté comme gelé
WATCHDOG_S = 5
# cadence de l'image quand aucune fenêtre Windows n'est active
BACKGROUND_FRAME_MS = 100
# durée attendue entre l'agent prêt (ou réveillé) et la fenêtre de l'appli
OPEN_EXPECTED_S = 4
SCREEN_TRIES = 5


def _suspend_offset():
    """Temps passé en veille depuis le démarrage (BOOTTIME - MONOTONIC) : il grandit à chaque
    mise en veille de l'hôte, ce qui distingue un gel du fil GTK d'une veille."""
    return time.clock_gettime(time.CLOCK_BOOTTIME) - time.monotonic()


def notify(summary, body=""):
    try:
        subprocess.Popen(["notify-send", "-a", "Vasistas", summary, body])
    except OSError:
        pass



WINDOWS_SCALES = (100, 125, 150, 175, 200, 225, 250, 300, 350, 400, 450, 500)


def windows_step(scale):
    """Échelle envoyée à Windows : toujours un palier qu'il accepte (100, 125… 500 %),
    jamais l'échelle brute de l'écran Linux (1,667 sur le portable)."""
    return min(WINDOWS_SCALES, key=lambda p: abs(p - scale * 100)) / 100


def windows_dpi(scale):
    """DPI que Windows prendra pour une échelle demandée : le palier le plus proche,
    comme Display.Apply dans l'agent."""
    step = min(WINDOWS_SCALES, key=lambda p: abs(p - scale * 100))
    return round(step * 96 / 100)

def longest_run(values, gap=3):
    """(début, fin) du plus long bloc de valeurs croissantes sans trou de plus de `gap`, ou None."""
    best = cur = None
    for v in values:
        cur = (cur[0], v) if cur and v - cur[1] <= gap else (v, v)
        if best is None or cur[1] - cur[0] > best[1] - best[0]:
            best = cur
    return best


class VasistasApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.views = {}           # id -> GuestWindow | GuestPopup
        self.infos = {}           # id -> dernier état connu (window.new + updates)
        self.textures = {}        # id -> dernière image, pour les vues créées plus tard
        self.inline = set()       # menus affichés dans l'image de leur fenêtre (pas de Popover)
        self.launch_queue = []
        self.next_req = 1
        self.guest_ready = False
        self.channel = None
        self.scale = 1.0
        # DPI Windows -> échelle de l'hôte demandée pour lui (175 % dans Windows pour
        # un écran Linux à 1,667 : Windows ne fait que des paliers)
        self.dpi_scales = {}
        self.scale_source = 0
        self.sleep = None
        self.balloon = None
        self.watched_monitors = []
        self.last_active = None   # dernière fenêtre active
        self.work_monitor = None  # écran dont Windows prend l'échelle (voir _current_monitor)
        self.held = False
        self.deactivate_source = 0
        self.starting_vm = False
        self.ready_timeout = 0
        self.splash = None
        self.splash_app = None
        self.pending_exec = {}    # req -> fonction de réponse du socket de contrôle
        self.screen_texture = None
        self.no_dmabuf = False      # import dmabuf refusé une fois : copie ensuite
        self.dmabuf_logged = False
        self.bench_state = None
        self.bench_occ = None       # banc de la fenêtre recouverte (bench_occluded)
        self.bench_resolution = None  # résolution d'essai imposée par le banc
        self.cursors = {}         # curseurs d'image envoyés par l'agent, par nom
        self.all_windows = set()  # toutes les fenêtres hôte créées, pour le balayage des orphelines
        self.screen = None        # écran lu dans QEMU (affichage D-Bus), sinon None
        self.screen_connecting = False
        self.screen_tries = 0       # nouvelles tentatives d'accès à l'écran de QEMU
        self.damage = []          # zones modifiées en attente
        self.damage_source = 0
        self.damage_guard = 0     # délai de garde si l'horloge d'affichage ne bat pas
        self.perf = {"damage": 0, "crops": 0, "crop_ms": 0.0, "background": 0}
        self.screen_stale = False   # zones ignorées faute de fenêtre visible
        self.frames_total = 0       # images de l'écran mises à jour depuis le lancement
        self.boot_started = 0.0     # démarrage de la VM lancé par l'hôte (durée apprise)
        # gels du fil GTK : battement toutes les STALL_TICK_MS, retard mesuré
        self.stalls = {"count": 0, "max_ms": 0.0, "total_ms": 0.0}
        self.stall_last = 0.0
        self.stall_ms = STALL_TICK_MS
        self.monitors_source = 0
        self.remember_sources = {}  # fenêtre -> temporisation de la mémoire de sa place

    # -- cycle de vie --

    def do_startup(self):
        Gtk.Application.do_startup(self)
        display = Gdk.Display.get_default()
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.Window.set_default_icon_name(APP_ID)
        from . import splash
        provider = Gtk.CssProvider()
        provider.load_from_data(splash.CSS)
        Gtk.StyleContext.add_provider_for_display(display, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        self.channel = Channel(str(vm.SERIAL), self.on_message, self.on_frame, self.on_state)
        monitors = display.get_monitors()
        monitors.connect("items-changed", lambda *a: self._watch_monitors())
        monitors.connect("items-changed", lambda *a: self._monitors_changed())
        self._watch_monitors()
        self.channel.start()
        from .clipboard import ClipboardSync
        self.clipboard = ClipboardSync(self.send, lambda: any(
            isinstance(v, GuestWindow) and v.is_active() for v in self.views.values()))
        from .control import ControlServer
        self.control = ControlServer(self._on_control)
        self.control.start()
        from .sleep import SleepManager
        self.sleep = SleepManager(self)
        from .balloon import BalloonManager
        self.balloon = BalloonManager(self)
        from .look import LookSync
        from .winnotify import WindowsNotifications
        from .wintray import WindowsTray
        # apparence : pas un usage (ne réveille pas Windows), d'où channel.send
        self.look = LookSync(self.channel.send)
        self.winnotify = WindowsNotifications(self.send)
        self.wintray = WindowsTray(self.send)
        GLib.timeout_add_seconds(2, self._sweep_orphans)
        self.titlebar_state = self.native_titlebar()
        self.stall_last = time.monotonic()
        self.stall_off = _suspend_offset()
        self.gtk_ident = threading.get_ident()
        GLib.timeout_add(STALL_TICK_MS, self._stall_tick)
        threading.Thread(target=self._watchdog, name="vasistas-watchdog", daemon=True).start()

    def _watch_monitors(self):
        """Suit l'échelle et la taille de chaque écran (réglages modifiés, écran branché)."""
        monitors = Gdk.Display.get_default().get_monitors()
        for i in range(monitors.get_n_items()):
            monitor = monitors.get_item(i)
            if monitor not in self.watched_monitors:
                self.watched_monitors.append(monitor)
                monitor.connect("notify::scale", lambda *a: self.schedule_update_scale())
                monitor.connect("notify::geometry", lambda *a: self.schedule_update_scale())
        self.schedule_update_scale()

    # -- écrans et place des fenêtres (placement.py) --

    def _top_views(self):
        """Fenêtres principales : ni menus, ni boîtes de dialogue rattachées à une autre."""
        return [v for v in self.views.values() if isinstance(v, GuestWindow) and v.kind != "popup"
                and v.get_transient_for() is None]

    def _secondary(self, view):
        """Boîte de dialogue, écran d'accueil (taille fixe), ou autre fenêtre d'une appli déjà ouverte (dialogue d'enregistrement
        ou de connexion sans propriétaire suivi) : ni la place ni la taille mémorisées de l'appli,
        et elle ne les remplace pas."""
        return view.kind == "dialog" or not view.sizable or any(
            v is not view and v.app_name == view.app_name for v in self._top_views())

    def _target_monitor(self, view, memory=None):
        """Écran choisi pour l'application de `view` (None : laisser Gala choisir)."""
        monitors = placement.gdk_monitors()
        if memory is None:
            memory = placement.remembered(placement.current_signature(), view.app_name) or {}
        connector = placement.target_connector(placement.choice_for(view.app_name),
                                               [m.get_connector() for m in monitors], memory.get("monitor"))
        return placement.by_connector(connector) if connector else None

    def _fit_monitor(self, view, target):
        """Écran dont la taille borne la fenêtre : celui choisi, sinon celui de la dernière fenêtre
        Windows active (Gala ouvre les fenêtres sur l'écran actif), sinon le plus petit."""
        if target is not None:
            return target
        if self.last_active is not None and self.last_active is not view:
            monitor = placement.monitor_of(self.last_active)
            if monitor is not None:
                return monitor
        monitors = placement.gdk_monitors()
        if not monitors:
            return None
        return min(monitors, key=lambda m: m.get_geometry().width * m.get_geometry().height)

    def _resize_to(self, view, lw, lh):
        """Taille logique imposée à une fenêtre : demandée à Windows et prise comme taille attendue
        (sinon le premier `rect` de Windows, encore à l'ancienne taille, la ferait revenir)."""
        gs = view.guest_scale()
        gw, gh = round(lw * gs), round(lh * gs)
        view.guest_size = (gw, gh)
        view.set_default_size(lw, view._host_height(gh, gs))
        self.send({"t": "window.resize", "id": view.wid, "w": gw, "h": gh})

    def placement_prepare(self, view):
        """Avant la première apparition d'une fenêtre principale : taille mémorisée pour ces
        écrans-là, jamais plus grande que l'écran. Gala la centre ensuite sur l'écran actif
        (center-new-windows) : la taille doit être la bonne dès l'affichage."""
        if view.kind == "popup" or view.get_transient_for() is not None or not view.app_name \
                or self._secondary(view):
            return
        view.memory = placement.remembered(placement.current_signature(), view.app_name) or {}
        view.target = self._target_monitor(view, view.memory)
        # Gala (WindowStateSaver) réimpose à chaque appli la place et la taille de sa n-ième
        # fenêtre, quel que soit l'écran : il laisse de côté les fenêtres non redimensionnables
        # au moment où elles apparaissent. Vasistas tient sa propre mémoire, par écrans.
        view.set_resizable(False)
        gw, gh = view.guest_size
        if not gw or not gh or view.is_maximized():
            return
        gs = view.guest_scale()
        lw, lh = round(gw / gs), round(gh / gs)
        if view.memory and placement.choice_for(view.app_name) != placement.AUTO:
            lw, lh = view.memory.get("w", lw), view.memory.get("h", lh)
        screen = self._fit_monitor(view, view.target)
        if screen is not None and placement.settings()["clamp"]:
            g = screen.get_geometry()
            lw, lh = placement.clamp_size(lw, lh, g.width, g.height, placement.CLAMP_SHARE)
        if (lw, lh) != (round(gw / gs), round(gh / gs)):
            log.info("fenêtre %s : ouverte en %dx%d (Windows proposait %dx%d)",
                     view.wid, lw, lh, round(gw / gs), round(gh / gs))
            self._resize_to(view, lw, lh)

    def placement_on_map(self, view):
        """Première apparition : passage sur l'écran choisi pour l'application (Gala garde la
        fenêtre centrée en changeant d'écran), agrandie si elle l'était la dernière fois."""
        # écran de travail recalculé une fois la fenêtre posée (voir _current_monitor)
        GLib.timeout_add(MAP_GRACE_MS + 50, lambda: self.schedule_update_scale() or False)
        if view.placed or view.kind == "popup" or view.get_transient_for() is not None or not view.app_name \
                or self._secondary(view):
            return
        view.placed = True
        target, memory = view.target, view.memory or {}

        def move():
            if self.views.get(view.wid) is not view:
                return False
            view.set_resizable(True)
            if target is not None and placement.monitor_of(view) is not target:
                placement.move_to(view, target, done=self.schedule_update_scale)
            if memory.get("max") and placement.choice_for(view.app_name) != placement.AUTO:
                view.maximize()
            return False
        GLib.timeout_add(placement.MOVE_AFTER_MAP_MS, move)

    def remember_window(self, view, delay_ms=1500):
        """Garde l'écran et la taille de la fenêtre pour la prochaine ouverture de son appli."""
        old = self.remember_sources.pop(view, 0)
        if old:
            GLib.source_remove(old)

        def save():
            self.remember_sources.pop(view, None)
            if not view.app_name or view.kind == "popup" or view.get_transient_for() is not None \
                    or view.moving or view.is_fullscreen() or self._secondary(view):
                return False
            monitor = placement.monitor_of(view)
            if monitor is None:
                return False
            sig = placement.current_signature()
            old_entry = placement.remembered(sig, view.app_name) or {}
            if view.is_maximized():
                w, h = old_entry.get("w", view.get_width()), old_entry.get("h", view.get_height())
            else:
                w, h = view.get_width(), view.get_height()
            placement.remember(sig, view.app_name, monitor.get_connector(), w, h, view.is_maximized())
            return False
        if delay_ms:
            self.remember_sources[view] = GLib.timeout_add(delay_ms, save)
        else:
            save()

    def reset_windows(self):
        """Toutes les fenêtres principales : taille raisonnable, centrées sur l'écran choisi pour
        leur application (ou celui où elles sont), échelle de Windows recalée."""
        views = self._top_views()
        if self.guest_ready:
            # côté Windows : fenêtres rétablies, ramenées dans l'écran de l'invité et centrées
            self.send({"t": "windows.reset", "max": placement.RESET_SHARE})
        for view in views:
            target = self._target_monitor(view) or placement.monitor_of(view) or self._current_monitor()
            if view.is_fullscreen():
                view.unfullscreen()
            if view.is_maximized():
                view.unmaximize()
            gw, gh = view.guest_size
            if target is not None and gw and gh:
                g = target.get_geometry()
                gs = view.guest_scale()
                lw, lh = placement.clamp_size(round(gw / gs), round(gh / gs), g.width, g.height,
                                              placement.RESET_SHARE)
                self._resize_to(view, lw, lh)
            # masquée puis réaffichée : Gala la replace comme une nouvelle fenêtre, centrée sur
            # l'écran actif ; elle passe ensuite sur son écran en restant centrée
            view.placed = False
            view.target = target
            view.memory = {}
            view.set_resizable(False)  # voir placement_prepare
            view.set_visible(False)
            GLib.timeout_add(60, lambda v=view: (self.views.get(v.wid) is v and v.present()) or False)
        log.info("affichages réinitialisés : %d fenêtre(s)", len(views))
        self.schedule_update_scale()
        return len(views)

    def _monitors_changed(self):
        """Écran branché ou débranché : fenêtres remises en place (option), une fois posé."""
        if self.monitors_source:
            GLib.source_remove(self.monitors_source)

        def run():
            self.monitors_source = 0
            if placement.settings()["reset_on_change"] and self._top_views():
                self.reset_windows()
            return False
        self.monitors_source = GLib.timeout_add(2500, run)

    @staticmethod
    def guest_options():
        """Options de l'agent envoyées avec hello : garde-fou de taille, bannières et icônes de
        la zone de notification de Windows relayées au bureau."""
        cfg = vm.load_config()
        return {"clamp": placement.settings(cfg)["clamp"],
                "notifications": cfg.get("win_notifications", True) is not False,
                "tray": cfg.get("win_tray", True) is not False}

    def _refresh_titlebars(self):
        """L'option « barre de titre du bureau » a peut-être été changée dans le compagnon
        (requête refresh_options)."""
        state = self.native_titlebar()
        if state != self.titlebar_state:
            self.titlebar_state = state
            for view in list(self.views.values()):
                if isinstance(view, GuestWindow):
                    view._apply_caption(view.nc, self.infos.get(view.wid, {}).get("rect"))

    def native_titlebar(self):
        """Option expérimentale : barre de titre du bureau à la place de celle de Windows."""
        return bool(vm.load_config().get("native_titlebar", False))

    def guest_scale_for(self, dpi):
        """Échelle hôte d'une fenêtre dessinée par Windows à `dpi`."""
        if not dpi:
            return self.scale
        return self.dpi_scales.get(dpi, dpi / 96)

    def schedule_update_scale(self):
        """Regroupe les changements (plusieurs fenêtres qui changent d'écran en même temps)."""
        # temporisation glissante : tant que la fenêtre passe d'un écran à l'autre (glisser
        # à cheval sur deux écrans), on attend qu'elle se pose avant de changer l'échelle
        if self.scale_source:
            GLib.source_remove(self.scale_source)
        self.scale_source = GLib.timeout_add(SCALE_DEBOUNCE_MS, self._update_scale)

    def _current_monitor(self):
        """Écran de référence de l'invité : celui qui porte le plus de surface de fenêtres
        Windows. Windows n'a qu'un écran, donc une seule échelle ; un simple changement de
        focus ne la change pas (chaque changement fait redessiner toutes les fenêtres),
        seul un déplacement de fenêtre, une ouverture, une fermeture ou un écran modifié."""
        display = Gdk.Display.get_default()
        monitors = display.get_monitors()
        items = [monitors.get_item(i) for i in range(monitors.get_n_items())]
        if not items:
            return None
        area = {}
        settling = False
        now_ms = GLib.get_monotonic_time() // 1000
        for view in list(self.views.values()):
            if not isinstance(view, GuestWindow) or not view.get_visible() or not view.get_realized():
                continue
            if view.moving or now_ms - (getattr(view, "map_time", 0) or 0) < MAP_GRACE_MS:
                settling = True
                continue
            surface = view.get_surface()
            if surface is None or surface.get_state() & Gdk.ToplevelState.MINIMIZED:
                continue
            monitor = display.get_monitor_at_surface(surface)
            if monitor is not None:
                area[monitor] = area.get(monitor, 0) + view.get_width() * view.get_height()
        if area:
            best = max(area, key=area.get)
            # à surface égale, on garde l'écran actuel (pas de bascule pour rien)
            if self.work_monitor in area and area[self.work_monitor] >= area[best]:
                best = self.work_monitor
            self.work_monitor = best
            return best
        if self.work_monitor in items:
            return self.work_monitor
        if settling:
            return None  # seules des fenêtres en train de se poser : on attend qu'elles le soient
        # sans fenêtre : le plus grand écran (l'écran externe au bureau, sinon l'écran du portable)
        return max(items, key=lambda m: m.get_geometry().width * m.get_geometry().height)

    def update_scale_now(self):
        if self.scale_source:
            GLib.source_remove(self.scale_source)
        self._update_scale()

    def _update_scale(self):
        """Windows prend l'échelle de l'écran hôte : l'image arrive à la bonne taille, pixel pour pixel."""
        self.scale_source = 0
        monitor = self._current_monitor()
        if monitor is None:
            return False
        scale = monitor.get_scale()
        changed = scale != self.scale
        self.scale = scale
        # statistiques de l'agent toutes les 2 s seulement en mode verbeux (-v)
        self.channel.hello_extra = {"scale": windows_step(scale), "stats": log.isEnabledFor(logging.DEBUG),
                                    **self.guest_options()}
        self.dpi_scales[windows_dpi(scale)] = scale
        if self.guest_ready:
            if changed:
                log.info("échelle de l'invité : %.3f (%s)", scale, monitor.get_connector())
            # redonnée même sans changement côté hôte : Windows a pu en changer seul (échelle
            # mémorisée par résolution, réglage à la main) ; l'agent ne fait rien si elle est juste
            self.send({"t": "display", "scale": windows_step(scale)})
        self._apply_resolution()
        return False

    def _apply_resolution(self, force=False):
        """Écran de Windows fixe, assez grand pour le plus grand écran de l'hôte (en pixels
        physiques) : une fenêtre agrandie côté hôte y tient sans étirement, et changer
        d'écran de travail ne redimensionne plus les fenêtres ni le mode du pilote."""
        monitors = Gdk.Display.get_default().get_monitors()
        items = [monitors.get_item(i) for i in range(monitors.get_n_items())]
        if self.screen is None or not items:
            return
        sizes = [(round(m.get_geometry().width * m.get_scale()), round(m.get_geometry().height * m.get_scale()))
                 for m in items]
        w, h = max(x for x, _ in sizes), max(y for _, y in sizes)
        if self.bench_resolution:
            w, h = self.bench_resolution
        # déjà demandée à cet écran : pas redemandée à chaque calcul d'échelle si Windows la refuse
        if force or ((w, h) != (self.screen.width, self.screen.height)
                     and getattr(self.screen, "asked_resolution", None) != (w, h)):
            self.screen.asked_resolution = (w, h)
            log.info("résolution de l'invité : %dx%d", w, h)
            big = max(items, key=lambda m: m.get_geometry().width * m.get_geometry().height)
            # le pilote peut ignorer SetUIInfo (virtio-gpu DOD) : l'agent change alors le mode
            self.screen.set_ui_info(w, h, big.get_width_mm(), big.get_height_mm())
            self.send({"t": "display", "resolution": [w, h]})

    def do_shutdown(self):
        if self.channel:
            self.channel.stop()
        Gtk.Application.do_shutdown(self)

    def do_command_line(self, cmdline):
        args = cmdline.get_arguments()[1:]
        args = [a for a in args if not a.startswith("-")] \
            if args[:1] not in (["launch"], ["launch-app"], ["open"]) else args
        if not self.held:
            self.hold()
            self.held = True
        if args and args[0] == "launch" and len(args) > 1:
            self.launch(args[1], args[2:])
        elif args and args[0] == "launch-app" and len(args) > 1:
            # fichiers passés par le lanceur (%F) : chemins Linux, à traduire pour Windows
            paths, rest = _split_files(cmdline, args[2:])
            if paths:
                self.open_files(paths, args[1])
            if rest or not paths:
                self.launch(self._app_command(args[1]), rest, app=args[1], name=self._app_name(args[1]))
        elif args and args[0] == "open" and len(args) > 1:
            paths, rest = _split_files(cmdline, args[1:])
            for a in rest:
                notify(_("Fichier introuvable"), a)
            self.open_files(paths)
        return 0

    @staticmethod
    def _app_command(app):
        from . import desktop
        return desktop.launch_command(app) or app

    @staticmethod
    def _app_name(app):
        from . import desktop
        entry = desktop.load_registry().get(app)
        return _short_name(entry["name"]) if entry else app

    # -- fichiers Linux ouverts dans Windows (files.py) --

    def open_files(self, paths, app=None):
        """Ouvre des fichiers Linux dans l'application Windows `app`, ou dans celle désignée
        pour leur extension. Un fichier hors des dossiers partagés passe par une question."""
        from . import files
        for path in paths:
            target = app or files.app_for(path)
            if not target:
                notify(_("Aucune application Windows pour ce fichier"),
                       _("{name} : aucune application Windows n'est associée à cette extension dans "
                         "Vasistas.", name=os.path.basename(path)))
                continue
            win = files.to_windows(path)
            if win is None:
                self._ask_outside(path, target)
            else:
                self._launch_file(target, win)

    def _launch_file(self, app, win_path):
        cmd = self._app_command(app)
        if cmd.lower().startswith("shell:"):
            # appli du Store (nouvel Outlook) : pas de ligne de commande, Windows choisit
            # l'application par l'extension du fichier
            self.launch(win_path, [], app=app, name=self._app_name(app))
        else:
            self.launch(cmd, [win_path], app=app, name=self._app_name(app))

    def _ask_outside(self, path, app):
        from . import files
        name = os.path.basename(path)
        folder = os.path.dirname(path)
        temporary = files.is_temporary(path)
        drives = ", ".join(f"{label} ({drive})" for _, _, drive, label in vm.shares())
        buttons = [_("Annuler"), _("Ouvrir une copie")] + ([] if temporary else [_("Partager le dossier")])
        dialog = Gtk.AlertDialog(
            message=_("« {name} » n'est pas dans un dossier partagé avec Windows", name=name),
            detail=(_("Windows a seulement accès à : {drives}. Vasistas peut copier le fichier dans "
                      "Téléchargements et ouvrir cette copie.",
                      drives=drives)
                    if temporary else
                    _("Windows a seulement accès à : {drives}. Vasistas peut copier le fichier dans "
                      "Téléchargements, ou partager le dossier « {folder} » avec Windows : les modifications sont "
                      "alors enregistrées dans le fichier d'origine.",
                      drives=drives, folder=os.path.basename(folder) or folder)),
            buttons=buttons, cancel_button=0, default_button=len(buttons) - 1, modal=True)

        def done(d, res):
            try:
                choice = d.choose_finish(res)
            except GLib.Error:
                return
            if choice == 1:
                self._open_copy(path, app)
            elif choice == 2:
                self._share_and_open(path, app)
        self.hold()
        dialog.choose(self.get_active_window(), None, lambda d, r: (done(d, r), self.release()))

    def _open_copy(self, path, app):
        import shutil
        from . import files
        dest = files.copy_target(path)
        if dest is None:
            notify(_("Copie impossible"), _("Aucun dossier partagé avec Windows."))
            return

        def work():
            try:
                shutil.copy2(path, dest)
            except OSError as e:
                GLib.idle_add(notify, _("Copie impossible"), str(e))
                return
            GLib.idle_add(lambda: self._launch_file(app, files.to_windows(dest)) and False)
        threading.Thread(target=work, name="vasistas-copy", daemon=True).start()

    def _share_and_open(self, path, app):
        from . import files
        folder = os.path.dirname(os.path.realpath(path))
        added = vm.add_share(folder)
        if added is None:
            notify(_("Partage impossible"), _("Aucune lettre de lecteur libre dans Windows."))
            return
        tag, drive, label = added
        if not vm.pid() and not os.environ.get("VASISTAS_SOCKET"):
            # Windows démarrera avec ce dossier (shares.txt reconstruit au démarrage) ;
            # VM en marche, même en plein démarrage : branchement à chaud, le montage attend l'agent
            self._launch_file(app, files.to_windows(path))
            return

        def plugged(ok):
            if not ok:
                notify(_("« {label} » sera visible au prochain démarrage de Windows", label=label),
                       _("Aucun emplacement libre pour partager un dossier pendant que Windows est en marche."))
                return False
            self.run_script(vm.mount_script(tag, drive, label), mounted)
            return False

        def mounted(res):
            if "True" in (res.get("out") or ""):
                self._launch_file(app, files.to_windows(path))
            else:
                notify(_("« {label} » ne répond pas dans Windows", label=label), (res.get("out") or "")[-200:])

        threading.Thread(target=lambda: GLib.idle_add(plugged, vm.hotplug_share(tag, folder)),
                         name="vasistas-share", daemon=True).start()

    def run_script(self, script, reply):
        """Script PowerShell dans l'invité ; `reply({"code", "out"})` dans le fil GTK."""
        n = self.next_req
        self.next_req += 1
        self.pending_exec[n] = reply
        self.launch_queue.append({"t": "exec", "req": n,
                                  "script": "$ProgressPreference = 'SilentlyContinue'\n" + script})
        if self.guest_ready:
            self._flush_launches()

    # -- envoi --

    def send(self, msg):
        if self.sleep is not None:
            # un usage réveille Windows avant que le message parte
            self.sleep.activity(msg.get("t"))
        if msg.get("t") == "launch" and self.balloon is not None:
            self.balloon.boost()
        return self.channel.send(msg)

    def launch(self, cmd, args, app=None, name=None):
        self.launch_queue.append({"t": "launch", "req": self.next_req, "cmd": cmd, "args": list(args)})
        self.next_req += 1
        if app and not any(i.get("app") == app for i in self.infos.values()):
            waking = self.sleep is not None and self.sleep.paused
            stage = _("Démarrage de Windows…") if not self.guest_ready else \
                _("Réveil de Windows…") if waking else _("Ouverture…")
            self._show_splash(app, name or app, stage)
            if not self.guest_ready:
                self.splash.set_progress(0.03, expected_s=self._expected_boot_s(), until=0.85)
            else:
                self.splash.set_progress(0.6 if waking else 0.85, expected_s=OPEN_EXPECTED_S, until=0.97)
        if self.guest_ready:
            self._flush_launches()
            return
        # socket imposé (faux invité) : pas de VM à démarrer
        if not os.environ.get("VASISTAS_SOCKET") and not vm.pid() and not self.starting_vm:
            self._check_disk()
            self.starting_vm = True
            self.boot_started = time.monotonic()
            if not app:
                notify(_("Démarrage de Windows"), _("L'application s'ouvrira dans une trentaine de secondes."))
            threading.Thread(target=self._start_vm, daemon=True).start()
        if not self.ready_timeout:
            self.ready_timeout = GLib.timeout_add_seconds(READY_TIMEOUT_S, self._ready_timed_out)

    # -- carte de chargement --

    @staticmethod
    def _check_disk():
        """Moins de 5 Go libres pour le disque de Windows : prévenir avant qu'il ne bloque."""
        import shutil
        try:
            free = shutil.disk_usage(vm.DATA).free
        except OSError:
            return
        if free < 5e9:
            from . import notices
            notices.low_disk(round(free / 1e9, 1))

    @staticmethod
    def _expected_boot_s():
        """Durée habituelle d'un démarrage de Windows jusqu'à l'agent (moyenne glissante)."""
        return float(vm.load_config().get("boot_s") or 30)

    def _learn_boot(self):
        if not self.boot_started:
            return
        took = time.monotonic() - self.boot_started
        self.boot_started = 0.0
        if 5 < took < 600:
            cfg = vm.load_config()
            old = float(cfg.get("boot_s") or took)
            cfg["boot_s"] = round(0.6 * old + 0.4 * took, 1)
            vm.save_config(cfg)

    def _show_splash(self, app, name, stage):
        from . import desktop, splash
        if self.splash is not None:
            self.splash.finish()
        icon = desktop.app_icon_name(app) if desktop.app_icon_path(app).exists() else APP_ID
        self.splash = splash.Splash(name, stage, icon, desktop.app_desktop_id(app),
                                    mode="performance" if vm.gpu_active() else vm.load_config().get("mode"),
                                    on_closed=self._on_splash_destroy)
        self.splash_app = app
        self.splash.present()
        # filet de sécurité : l'appli peut réutiliser une fenêtre déjà ouverte
        self.splash_timeout = GLib.timeout_add_seconds(SPLASH_MAX_S, self._splash_expired, self.splash)

    def _on_splash_destroy(self, win):
        if win is self.splash:
            self.splash = None
            self.splash_app = None

    def _splash_expired(self, win):
        if win is self.splash:
            win.finish()
        return False

    def _splash_stage(self, text, error=False):
        if self.splash is not None:
            self.splash.set_stage(text, error)
            if error:
                self.splash.finish(6000)

    def _start_vm(self):
        try:
            vm.start()
        except (SystemExit, Exception) as e:
            log.error("démarrage de la VM : %s", e)
            from . import notices
            detail = str(e)[-300:]  # `e` n'existe plus quand la lambda tourne
            GLib.idle_add(lambda: notices.send(
                _("La machine virtuelle ne démarre pas"), detail,
                [("diagnose", _("Diagnostic"), lambda: notices.open_companion("diagnose"))],
                key="vm-start", urgent=True) and False)
            GLib.idle_add(self._splash_stage, _("La machine virtuelle ne démarre pas."), True)
        finally:
            self.starting_vm = False

    def _ready_timed_out(self):
        self.ready_timeout = 0
        if not self.guest_ready and self.launch_queue:
            self._splash_stage(_("Windows ne répond pas."), error=True)
            from . import notices
            notices.windows_not_responding()
        return False

    def _flush_launches(self):
        while self.launch_queue:
            self.send(self.launch_queue.pop(0))

    # -- activation --

    def screen_region(self, rect):
        """Copie d'un rectangle (pixels invité) de l'écran de QEMU, en texture ; None sans écran."""
        sc = self.screen
        if sc is None or sc.fb is None:
            return None
        x, y, w, h = rect
        x, y = max(0, x), max(0, y)
        w, h = min(w, sc.width - x), min(h, sc.height - y)
        if w <= 0 or h <= 0:
            return None
        fb = memoryview(sc.fb)
        data = b"".join(fb[(y + r) * sc.stride + x * 4:(y + r) * sc.stride + (x + w) * 4] for r in range(h))
        return Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.B8G8R8X8, GLib.Bytes.new(data), w * 4)

    def screen_thumbnail(self, width):
        """Écran de Windows réduit à `width` pixels environ (une colonne et une ligne sur k),
        en PNG base64 pour le compagnon ; None sans écran."""
        sc = self.screen
        if sc is None or sc.fb is None or not sc.width or not sc.height:
            return None
        k = max(1, sc.width // max(64, width))
        fb = memoryview(sc.fb)
        rows = [fb[y * sc.stride:y * sc.stride + sc.width * 4].cast("I")[::k].tolist()
                for y in range(0, sc.height, k)]
        # recadré sur ce qui n'est pas noir (Windows n'occupe souvent qu'une partie de l'écran) ;
        # une ligne ou une colonne compte si plus de 3 % de ses points sont allumés
        lit = [[1 if v & 0xFFFFFF else 0 for v in r] for r in rows]
        w0, h0 = len(lit[0]), len(lit)
        ys = [y for y, r in enumerate(lit) if sum(r) > w0 * 0.03]
        xs = [x for x, c in enumerate(zip(*lit)) if sum(c) > h0 * 0.03]
        ys, xs = longest_run(ys), longest_run(xs)
        if ys and xs:
            rows = [r[xs[0]:xs[1] + 1] for r in rows[ys[0]:ys[1] + 1]]
        w, h = len(rows[0]), len(rows)
        data = b"".join(struct.pack(f"{w}I", *r) for r in rows)
        texture = Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.B8G8R8X8, GLib.Bytes.new(data), w * 4)
        return base64.b64encode(texture.save_to_png_bytes().get_data()).decode()

    def window_activated(self, win):
        if self.deactivate_source:
            GLib.source_remove(self.deactivate_source)
            self.deactivate_source = 0
        # le focus ne change pas l'échelle de l'invité (voir _current_monitor)
        self.last_active = win
        self.screen_resume()
        self.send({"t": "window.activate", "id": win.wid})
        # copie faite sous Linux avant de revenir dans une application Windows
        self.clipboard.push()

    def window_deactivated(self, win):
        # Passer d'une fenêtre invitée à une autre ne doit pas renvoyer l'invité
        # sur sa fenêtre cachée : on attend de voir si une autre prend le relais.
        win.release_keys()
        if self.deactivate_source:
            GLib.source_remove(self.deactivate_source)
        self.deactivate_source = GLib.timeout_add(DEACTIVATE_DELAY_MS, self._deactivate, win.wid)

    def _deactivate(self, wid):
        self.deactivate_source = 0
        if not any(isinstance(v, GuestWindow) and v.is_active() for v in self.views.values()):
            self.send({"t": "window.deactivate", "id": wid})
        return False

    # -- messages de l'invité --

    def on_state(self, connected):
        if not connected:
            # Windows arrêté de lui-même : rendre la carte graphique prêtée
            threading.Thread(target=lambda: (time.sleep(3), vm.release_gpu()), daemon=True).start()
            self.guest_ready = False
            if self.screen is not None:
                # QEMU parti : écran partagé et dmabuf rendus tout de suite, sans attendre un
                # Disable qui ne viendra pas
                self.screen._m_Disable(None, None)
            self.screen = None
            self.screen_texture = None
            for wid in list(self.views):
                self._close(wid)
            self.infos.clear()
            self.textures.clear()
            self.wintray.clear()
            self._fail_sent_requests(_("invité pas prêt"))
        return False

    def _fail_sent_requests(self, error):
        """Requêtes déjà parties vers l'agent (exec, explain) : aucune réponse ne viendra si
        l'agent ou Windows s'arrête, le client du socket de contrôle resterait bloqué ; celles
        encore en file partiront au prochain hello."""
        queued = {m.get("req") for m in self.launch_queue}
        for req in [r for r in self.pending_exec if r not in queued]:
            self.pending_exec.pop(req)({"code": 1, "out": "", "error": error})

    def on_message(self, msg):
        t = msg.get("t")
        handler = {
            "hello": self._on_hello,
            "window.new": self._on_new,
            "window.update": self._on_update,
            "window.close": lambda m: self._close(m["id"]),
            "window.focus": self._on_focus,
            "window.request": self._on_request,
            "sync": self._on_sync,
            "hover": self._on_hover,
            "launched": self._on_launched,
            "app.icon": self._on_app_icon,
            "cursor.image": self._on_cursor_image,
            "exec.result": self._on_exec_result,
            "debug.windows": self._on_debug_windows,
            "agent.start": self._on_agent_start,
            "bench.posted": self._on_bench_posted,
            "clipboard": lambda m: self.clipboard.from_guest(m),
            "notify": lambda m: self.winnotify.show(m),
            "tray": lambda m: self.wintray.update(m.get("items") or []),
            "windows.reset.done": lambda m: log.info("Windows : %s fenêtre(s) recentrée(s)", m.get("count")),
            "log": lambda m: log.info("invité : %s", m.get("msg")),
        }.get(t)
        if handler is None:
            log.debug("message ignoré : %s", t)
        else:
            try:
                handler(msg)
            except Exception:
                log.exception("traitement de %s", t)
        return False

    def _on_hello(self, msg):
        if not self.guest_ready:
            log.info("invité prêt : écran %s, dpi %s", msg.get("screen"), msg.get("dpi"))
        self.guest_ready = True
        # hôte relancé pendant qu'une touche était tenue : Windows n'a jamais reçu le relâchement
        # (Ctrl resté enfoncé = la molette zoome, les clics deviennent des Ctrl+clic)
        for sc, ext in ((0x1D, False), (0x1D, True), (0x2A, False), (0x36, False), (0x38, False),
                        (0x38, True), (0x5B, True), (0x5C, True)):
            self.channel.send({"t": "key", "sc": sc, "ext": ext, "down": False})  # pas un usage
        self._learn_boot()
        from . import notices
        notices.withdraw("not-responding")
        self.agent_version = msg.get("agentVersion")
        if self.agent_version:
            from .version import VERSION, newer
            if newer(VERSION, self.agent_version):
                notices.old_agent(self.agent_version)
        self._splash_stage(_("Ouverture…"))
        if self.splash is not None:
            self.splash.set_progress(0.88, expected_s=OPEN_EXPECTED_S, until=0.97)
        # le hello peut partir avant la première mesure des écrans : options redonnées ici
        self.channel.send({"t": "display", **self.guest_options()})
        self.look.push()
        if self.screen is None and not os.environ.get("VASISTAS_SOCKET"):
            self._connect_screen()
        if self.ready_timeout:
            GLib.source_remove(self.ready_timeout)
            self.ready_timeout = 0
        self._flush_launches()

    # -- mesure de latence : touche envoyée -> zone modifiée dans la fenêtre --

    def _bench(self, req, reply):
        """Tape `n` caractères dans la fenêtre `wid` (Bloc-notes conseillé) et mesure le délai
        entre l'envoi de chaque touche et la première zone modifiée qui la touche."""
        wid = req.get("wid") or next((w for w, i in self.infos.items() if "Bloc-notes" in (i.get("title") or "")), None)
        if wid is None or self.screen is None:
            reply({"error": _("pas de Bloc-notes ouvert, ou écran QEMU indisponible")})
            return
        n = int(req.get("bench", 20))
        self.send({"t": "window.activate", "id": wid})
        state = {"i": 0, "t0": 0.0, "lat": [], "wid": wid, "n": n, "reply": reply, "timer": 0,
                 "via": req.get("via", "agent")}
        self.bench_state = state
        GLib.timeout_add(500, self._bench_next)

    def _bench_next(self):
        st = self.bench_state
        if st is None:
            return False
        if st["i"] >= st["n"]:
            lat = sorted(st["lat"])
            self.bench_state = None
            if "qmp" in st:
                st["qmp"].close()
            res = {"samples": len(lat)}
            if lat:
                res.update(median_ms=round(lat[len(lat) // 2], 1), p90_ms=round(lat[int(len(lat) * 0.9)], 1),
                           min_ms=round(lat[0], 1), max_ms=round(lat[-1], 1))
            st["reply"](res)
            return False
        st["i"] += 1
        st["t0"] = time.perf_counter()
        sc = 0x10 + (st["i"] % 10)
        if st.get("via") == "qmp":
            # clavier virtuel de QEMU : sans le canal ni l'agent
            if "qmp" not in st:
                st["qmp"] = vm.Qmp()
            st["qmp"].cmd("send-key", keys=[{"type": "qcode", "data": "azertyuiop"[st["i"] % 10]}])
        else:
            self.send({"t": "key", "sc": sc, "ext": False, "down": True})
            self.send({"t": "key", "sc": sc, "ext": False, "down": False})
        st["timer"] = GLib.timeout_add(1000, self._bench_timeout)
        return False

    def _bench_hit(self, rects):
        st = self.bench_state
        if st is None or not st["t0"]:
            return
        x, y, w, h = self.infos.get(st["wid"], {}).get("rect", (0, 0, 0, 0))
        if any(x0 < x + w and x < x1 and y0 < y + h and y < y1 for x0, y0, x1, y1 in rects):
            st["lat"].append((time.perf_counter() - st["t0"]) * 1000)
            st["t0"] = 0.0
            GLib.source_remove(st["timer"])
            GLib.timeout_add(150, self._bench_next)

    def _bench_timeout(self):
        st = self.bench_state
        if st is not None:
            st["t0"] = 0.0
            GLib.timeout_add(150, self._bench_next)
        return False

    # -- latence d'une fenêtre recouverte : caractère posté par l'agent -> image (tuiles) reçue --

    def _bench_occluded(self, req, reply):
        wid, n = req.get("wid"), int(req.get("n", 10))
        if wid not in self.infos or not self.infos[wid].get("occluded"):
            reply({"error": _("fenêtre absente ou pas recouverte dans Windows")})
            return
        self.bench_occ = {"wid": wid, "n": n, "i": 0, "t0": 0.0, "lat": [], "lost": 0, "reply": reply, "timer": 0}
        self._bench_occ_next()

    def _bench_occ_next(self):
        st = self.bench_occ
        if st is None:
            return False
        if st["i"] >= st["n"]:
            self.bench_occ = None
            lat = sorted(st["lat"])
            res = {"samples": len(lat), "lost": st["lost"]}
            if lat:
                res.update(median_ms=round(lat[len(lat) // 2], 1), p90_ms=round(lat[int(len(lat) * 0.9)], 1),
                           min_ms=round(lat[0], 1), max_ms=round(lat[-1], 1))
            st["reply"](res)
            return False
        st["i"] += 1
        st["t0"] = 0.0
        self.send({"t": "bench.post", "id": st["wid"], "i": st["i"]})
        st["timer"] = GLib.timeout_add(3000, self._bench_occ_timeout)
        return False

    def _on_bench_posted(self, msg):
        st = self.bench_occ
        if st is not None and msg.get("id") == st["wid"] and msg.get("i") == st["i"]:
            st["t0"] = time.perf_counter()

    def _bench_occ_timeout(self):
        st = self.bench_occ
        if st is not None:
            st["lost"] += 1
            st["t0"] = 0.0
            self._bench_occ_next()
        return False

    def _on_agent_start(self, msg):
        """Agent (re)lancé pendant que l'hôte tourne : refaire la poignée de main."""
        log.info("agent relancé dans l'invité")
        self._fail_sent_requests(_("agent relancé"))
        self.send(protocol.hello(**self.channel.hello_extra))
        if self.screen is not None:
            self.send({"t": "display", "framebuffer": True})
            self.update_scale_now()  # échelle de l'écran de travail, pas la valeur par défaut (1.0)
            GLib.timeout_add(500, lambda: self._apply_resolution() and False)

    def _on_exec_result(self, msg):
        reply = self.pending_exec.pop(msg.get("req"), None)
        if reply is not None:
            reply({"code": msg.get("code", 1), "out": msg.get("out") or ""})

    def _on_debug_windows(self, msg):
        reply = self.pending_exec.pop(msg.get("req"), None)
        if reply is not None:
            reply({"windows": msg.get("windows") or []})

    # -- socket de contrôle (vasistas exec, application compagnon) --

    def _on_control(self, req, reply):
        if "exec" in req:
            self.run_script(req["exec"], reply)
        elif "send" in req:
            # message brut vers l'agent (expériences, application compagnon)
            self.send(req["send"])
            reply({"ok": True})
        elif "bench" in req:
            self._bench(req, reply)
        elif "bench_occluded" in req:
            self._bench_occluded(req["bench_occluded"], reply)
        elif "resolution" in req:
            # essai (banc) : résolution de l'écran de Windows imposée jusqu'à {"resolution": null}
            self.bench_resolution = req["resolution"]
            self._apply_resolution(force=True)
            reply({"ok": True})
        elif "sleep" in req:
            reply({"paused": self.sleep.pause() if req["sleep"] else (self.sleep.wake() or False)})
        elif "windows" in req:
            reply({"windows": [{k: v for k, v in info.items() if k != "t"} for info in self.infos.values()],
                   "screen": [self.screen.width, self.screen.height] if self.screen else None})
        elif "stats" in req:
            reply({"stalls": dict(self.stalls), "views": len(self.views), "frames": self.frames_total,
                   "dmabuf": self.screen is not None and self.screen.dmabuf is not None and not self.no_dmabuf,
                   "channel": self.channel.stats()})
        elif "explain" in req:
            if not self.guest_ready:
                reply({"error": _("invité pas prêt")})
                return
            n = self.next_req
            self.next_req += 1
            self.pending_exec[n] = reply
            self.send({"t": "debug.windows", "req": n, "all": bool(req.get("all"))})
        elif "refresh_options" in req:
            # réglages changés dans le compagnon : redonnés à l'agent, sans compter comme un usage
            self._refresh_titlebars()
            if self.guest_ready:
                self.channel.send({"t": "display", **self.guest_options()})
                self.look.push()
            reply({"ok": True})
        elif "reset_windows" in req:
            reply({"count": self.reset_windows()})
        elif "thumbnail" in req:
            reply({"png": self.screen_thumbnail(int(req["thumbnail"] or 480))})
        elif "status" in req:
            reply({"guest_ready": self.guest_ready, "windows": len(self.views), "pid": os.getpid(),
                   "agent_version": getattr(self, "agent_version", None),
                   "idle_s": round(time.monotonic() - self.sleep.last_use) if self.sleep else None,
                   "screen": self.screen is not None,
                   "paused": bool(self.sleep and self.sleep.paused),
                   "power": self.sleep.power.snapshot() if self.sleep else None})
        else:
            reply({"error": _("requête inconnue")})

    def _on_cursor_image(self, msg):
        """Curseur propre à une application Windows (Office...) : même forme sous Linux."""
        # L'image est aux pixels de Windows (échelle de l'invité, 1,67 sur le portable) ; GTK
        # prend la taille d'une texture de curseur pour des pixels logiques et Gala l'agrandit
        # encore : curseur énorme. On la ramène à sa taille logique.
        try:
            from gi.repository import GdkPixbuf
            loader = GdkPixbuf.PixbufLoader.new_with_type("png")
            loader.write(base64.b64decode(msg["png"]))
            loader.close()
            pb = loader.get_pixbuf()
            k = max(1.0, self.scale)
            w, h = max(1, round(pb.get_width() / k)), max(1, round(pb.get_height() / k))
            if (w, h) != (pb.get_width(), pb.get_height()):
                pb = pb.scale_simple(w, h, GdkPixbuf.InterpType.HYPER)
            tex = Gdk.Texture.new_for_pixbuf(pb)
            self.cursors[msg["name"]] = Gdk.Cursor.new_from_texture(
                tex, min(w - 1, round(msg.get("x", 0) / k)), min(h - 1, round(msg.get("y", 0) / k)), None)
        except GLib.Error as e:
            log.warning("curseur %s : %s", msg.get("name"), e.message)

    def _on_app_icon(self, msg):
        from . import desktop
        app = desktop.canonical_app(msg.get("app"))
        if app:
            desktop.save_app_icon(app, base64.b64decode(msg["png"]))

    def _on_new(self, info):
        from . import desktop
        wid = info["id"]
        new_app = False
        info["app"] = desktop.canonical_app(info.get("app"))
        if info.get("app") and info.get("kind") != "popup":
            new_app = desktop.ensure_app(info["app"], info.get("appName"), info.get("exe"))
        view, old = self.views.get(wid), self.infos.get(wid)
        if isinstance(view, GuestWindow) and old and (old.get("kind"), old.get("owner")) == (info.get("kind"), info.get("owner")):
            # même fenêtre renvoyée (hello de l'agent, fenêtre invisible le temps d'un balayage) :
            # mise à jour sur place ; la recréer la ferait replacer par Gala, changer d'écran,
            # rebasculer l'échelle de Windows et perdre ses menus
            self._on_update(info)
            if info.get("maximized") and not view.is_maximized():
                view.maximize()
            if self.screen is not None:
                self._refresh_from_screen([wid])
            return
        if wid in self.views:
            self._close(wid)
        self.infos[wid] = dict(info)
        if self.splash is not None and info.get("app") == self.splash_app and info.get("kind") != "popup":
            self.splash.set_progress(1.0)
            # la fenêtre hôte arrive après NEW_APP_DELAY_MS pour une appli nouvelle
            self.splash.finish(NEW_APP_DELAY_MS if new_app else 300)
        self._create(wid, delay_ms=NEW_APP_DELAY_MS if new_app else 0)
        # des popups attendaient peut-être cette fenêtre
        for other, oinfo in list(self.infos.items()):
            if other not in self.views and oinfo.get("owner") == wid:
                self._create(other)

    def _create(self, wid, delay_ms=0):
        info = self.infos[wid]
        owner = info.get("owner") or 0
        if info.get("kind") == "popup" and owner:
            parent = self.views.get(owner)
            if parent is None:
                return  # créé quand le propriétaire arrive
            if self._inline_ok(wid):
                # Le menu est déjà dans l'image de sa fenêtre (écran de QEMU), à sa place, avec son
                # ombre : pas de surface Wayland à placer, les clics passent par la fenêtre parente.
                self.inline.add(wid)
                return
            origin = tuple(self.infos[owner]["rect"][:2])
            if self._from_screen(owner):
                # Le menu déborde de sa fenêtre : il devient un popup que le bureau peut décaler pour
                # le garder à l'écran. Sous lui, la fenêtre parente garde l'image d'avant le menu,
                # sinon le menu dessiné par Windows dans l'écran de QEMU apparaîtrait en double.
                parent.view.freeze(wid, info["rect"], self.screen_region(info["rect"]))
            view = GuestPopup(self, info, parent.view, origin)
        else:
            view = GuestWindow(self, info)
            self.all_windows.add(view)
            parent = self.views.get(owner)
            if isinstance(parent, GuestWindow):
                view.set_transient_for(parent)
            if info.get("maximized"):
                view.maximize()
            self.placement_prepare(view)
            if delay_ms:
                # lanceur tout juste créé : laisser le dock le découvrir avant d'afficher
                # (sauf si la fenêtre a été fermée entre-temps : present() la ressusciterait
                # hors du suivi, fenêtre fantôme que le balayage ne voit plus)
                GLib.timeout_add(delay_ms, lambda v=view, w=wid:
                                 (self.views.get(w) is v and v.present()) or False)
            else:
                view.present()
        self.views[wid] = view
        if wid in self.textures:
            view.view.set_texture(self.textures[wid])
        if self.screen is not None:
            self._refresh_from_screen([wid])

    def _inline_ok(self, wid):
        """Menu entièrement dans sa fenêtre parente, elle-même lue dans l'écran de QEMU."""
        info = self.infos.get(wid)
        owner = info.get("owner") if info else 0
        parent = self.infos.get(owner)
        if not info or not parent or not isinstance(self.views.get(owner), GuestWindow) \
                or not self._from_screen(owner):
            return False
        x, y, w, h = info["rect"]
        px, py, pw, ph = parent["rect"]
        return px <= x and py <= y and x + w <= px + pw and y + h <= py + ph

    def _on_sync(self, msg):
        """Liste des fenêtres suivies par l'agent (toutes les 3 s) : fermer ce qu'il ne connaît
        plus, redemander ce qu'on a manqué. Les messages arrivent dans l'ordre : pas de course."""
        ids = set(msg.get("ids") or [])
        for wid in [w for w in self.infos if w not in ids]:
            log.info("fenêtre %s absente de l'invité : fermée", wid)
            self._close(wid)
        missing = [w for w in ids if w not in self.infos]
        if missing:
            log.info("fenêtres inconnues de l'hôte, redemandées : %s", missing)
            self.send({"t": "resend", "ids": missing})

    def _on_update(self, msg):
        wid = msg["id"]
        info = self.infos.get(wid)
        if info is None:
            return
        if "rect" in msg and self.screen is not None and info.get("rect") != msg["rect"] \
                and "shown_rect" not in info:
            info["shown_rect"] = info.get("rect")
            info["rect_since"] = time.monotonic()
        info.update(msg)
        if wid in self.inline:
            if not self._inline_ok(wid):
                # sorti de sa fenêtre, ou fenêtre parente recouverte : surface à part
                self.inline.discard(wid)
                self._create(wid)
            return
        if "occluded" in msg:
            # fenêtre passée en capture par l'agent : ses menus ne sont plus dans son image
            for other in [o for o in self.inline if self.infos.get(o, {}).get("owner") == wid]:
                if not self._inline_ok(other):
                    self.inline.discard(other)
                    self._create(other)
        view = self.views.get(wid)
        if view is None:
            return
        view.update(msg)
        if "rect" in msg:
            origin = tuple(msg["rect"][:2])
            for other, v in self.views.items():
                if isinstance(v, GuestPopup) and self.infos[other].get("owner") == wid:
                    v.set_parent_origin(origin)
        if msg.get("minimized") and isinstance(view, GuestWindow):
            view.minimize()
        if self.screen is not None and ("rect" in msg or msg.get("occluded") is False):
            self._refresh_from_screen([wid])

    def _on_focus(self, msg):
        view = self.views.get(msg["id"])
        # seulement si une fenêtre invitée a déjà le focus : ne pas voler celui du bureau
        if isinstance(view, GuestWindow) and any(
                isinstance(v, GuestWindow) and v.is_active() for v in self.views.values()):
            view.present()

    def _on_request(self, msg):
        view = self.views.get(msg["id"])
        if isinstance(view, GuestWindow):
            view.request(msg.get("action"))

    def _on_hover(self, msg):
        view = self.views.get(msg["id"])
        if view is not None:
            view.view.set_hover(msg.get("hit", 1), msg.get("cursor"))

    def _on_launched(self, msg):
        if not msg.get("ok"):
            notify(_("Lancement impossible"), msg.get("error") or "")
            self._splash_stage(_("Lancement impossible."), error=True)

    def _close(self, wid):
        for other, info in list(self.infos.items()):
            if info.get("owner") == wid and (isinstance(self.views.get(other), GuestPopup) or other in self.inline):
                # menu gardé connu : recréé si sa fenêtre revient (_on_new), retiré par le sync sinon
                self._close(other)
                self.infos[other] = info
        self.inline.discard(wid)
        view = self.views.get(wid)
        if view is not None:
            view.destroy_view()
        self.forget(wid)

    def forget(self, wid):
        if self.last_active is not None and self.last_active.wid == wid:
            self.last_active = None
        self.inline.discard(wid)
        view = self.views.pop(wid, None)
        source = self.remember_sources.pop(view, 0)
        if source:
            GLib.source_remove(source)  # mémorisation d'une fenêtre qui n'existe plus
        if isinstance(view, GuestWindow):
            self.schedule_update_scale()  # l'écran de travail peut changer
        self.infos.pop(wid, None)
        self.textures.pop(wid, None)

    # -- images --

    # Deux sources d'image : l'écran de QEMU pour les fenêtres visibles dans l'invité,
    # les tuiles de l'agent pour celles qui y sont recouvertes.

    def _connect_screen(self):
        """Écran de QEMU : la partie QMP passe par le fil QMP, la suite revient au fil GTK."""
        if self.screen_connecting:
            return
        self.screen_connecting = True

        def attached(fut):
            if fut.exception():
                log.warning("affichage D-Bus indisponible : %s", fut.exception())
            sock = None if fut.exception() else fut.result()
            GLib.idle_add(self._screen_attached, sock)
        vm.qmp_worker().submit(Screen.attach).add_done_callback(attached)

    def _screen_attached(self, sock):
        self.screen_connecting = False
        if sock is None or not self.guest_ready:
            if sock is not None:
                sock.close()
            elif self.guest_ready and self.screen is None and self.screen_tries < SCREEN_TRIES:
                # QEMU tout juste relancé : l'affichage D-Bus peut ne pas être prêt
                self.screen_tries += 1
                GLib.timeout_add_seconds(2, lambda: self._connect_screen() and False)
            return False
        self.screen_tries = 0
        screen = Screen(self._on_damage)
        if not screen.open(sock):
            return False
        self.screen = screen
        self.send({"t": "display", "framebuffer": True})
        self.update_scale_now()  # échelle de l'écran de travail, pas la valeur par défaut (1.0)
        log.info("écran lu dans QEMU")
        GLib.timeout_add(500, lambda: self._apply_resolution() and False)
        if log.isEnabledFor(logging.DEBUG):
            GLib.timeout_add_seconds(2, self._stats_tick)
        return False

    def _from_screen(self, wid):
        info = self.infos.get(wid)
        if self.screen is None or info is None or info.get("occluded") or info.get("minimized"):
            return False
        # jamais de découpe hors de l'écran (l'agent capture alors la fenêtre lui-même)
        x, y, w, h = info["rect"]
        return x >= 0 and y >= 0 and x + w <= self.screen.width and y + h <= self.screen.height

    def _shown_rect(self, wid, rects=()):
        """Cadrage à afficher : l'ancien tant que l'écran n'a pas été redessiné au nouvel endroit."""
        info = self.infos[wid]
        old = info.get("shown_rect")
        if old is None:
            return info["rect"]
        x, y, w, h = info["rect"]
        if time.monotonic() - info["rect_since"] > RECT_HOLD_S or \
                any(x0 < x + w and x < x1 and y0 < y + h and y < y1 for x0, y0, x1, y1 in rects):
            info.pop("shown_rect", None)
            return info["rect"]
        return old

    def _on_damage(self, x, y, w, h):
        self.perf["damage"] += 1
        if self.bench_state is not None:
            self._bench_hit([(x, y, x + w, y + h)])
        if not self.damage_source:
            view = self._tick_view()
            if view is None:
                # Aucune fenêtre lue dans l'écran n'est visible sous Linux (réduites, cachées,
                # autre espace de travail) : rien à dessiner. L'image entière sera refaite quand
                # une fenêtre reviendra (screen_resume).
                self.damage.clear()
                self.screen_stale = True
                return
            self.damage.append((x, y, x + w, y + h))
            # Une mise à jour par image affichée : au prochain battement de l'horloge d'affichage
            # d'une fenêtre visible (plusieurs zones de QEMU regroupées). Le délai de garde couvre
            # une horloge qui ne bat pas. Sans fenêtre Windows active (on travaille dans une
            # application Linux), 10 images par seconde au plus suffisent.
            self.damage_source = -1
            if any(isinstance(v, GuestWindow) and v.is_active() for v in self.views.values()):
                view.add_tick_callback(lambda *_: self._flush_damage())
                self.damage_guard = GLib.timeout_add(50, self._guard_flush)
            else:
                self.perf["background"] += 1
                GLib.timeout_add(BACKGROUND_FRAME_MS, self._flush_damage)
        else:
            self.damage.append((x, y, x + w, y + h))

    def screen_resume(self):
        """Une fenêtre redevient visible ou active : image de tout l'écran refaite si des zones
        ont été laissées de côté entre-temps."""
        if self.screen_stale and self.screen is not None and self.screen.fb is not None:
            self.screen_stale = False
            self._on_damage(0, 0, self.screen.width, self.screen.height)

    def _tick_view(self):
        for wid, v in self.views.items():
            if isinstance(v, GuestWindow) and v.get_mapped() and not v.is_suspended() \
                    and self._from_screen(wid):
                return v.view
        return None

    def _screen_builder(self, scr):
        """Texture de tout l'écran : dmabuf sans copie si possible, copie mémoire sinon."""
        if scr.dmabuf is not None and not self.no_dmabuf:
            fd, offset = scr.dmabuf
            b = Gdk.DmabufTextureBuilder()
            b.set_display(Gdk.Display.get_default())
            b.set_width(scr.width)
            b.set_height(scr.height)
            b.set_fourcc(0x34325258)  # XR24 = octets B, G, R, X
            b.set_modifier(0)         # linéaire
            b.set_n_planes(1)
            b.set_fd(0, fd)
            b.set_stride(0, scr.stride)
            b.set_offset(0, offset)
            return b, True
        builder = Gdk.MemoryTextureBuilder()
        # bytes() : copie en C ; GLib.Bytes.new(memoryview) copie octet par octet (1 s !)
        builder.set_bytes(GLib.Bytes.new(bytes(scr.fb)))
        builder.set_width(scr.width)
        builder.set_height(scr.height)
        builder.set_stride(scr.stride)
        builder.set_format(Gdk.MemoryFormat.B8G8R8X8)
        return builder, False

    def _guard_flush(self):
        self.damage_guard = 0
        return self._flush_damage()

    def _flush_damage(self):
        """Une texture pour tout l'écran de la VM ; GTK ne renvoie au GPU que les zones
        modifiées (update_region). Les fenêtres visibles en affichent chacune leur portion."""
        if self.damage_guard:
            # le battement est arrivé avant le délai de garde : pas de réveil inutile
            GLib.source_remove(self.damage_guard)
            self.damage_guard = 0
        if not self.damage_source:
            return False  # déjà fait (battement puis délai de garde)
        self.damage_source = 0
        rects, self.damage = self.damage, []
        scr = self.screen
        if scr is None or scr.fb is None:
            return False
        t0 = time.perf_counter()
        builder, dmabuf = self._screen_builder(scr)
        prev = self.screen_texture
        if HAVE_CAIRO_REGION and prev is not None \
                and (prev.get_width(), prev.get_height()) == (scr.width, scr.height):
            region = cairo.Region()
            for x0, y0, x1, y1 in rects:
                region.union(cairo.RectangleInt(x0, y0, x1 - x0, y1 - y0))
            builder.set_update_texture(prev)
            builder.set_update_region(region)
        try:
            self.screen_texture = builder.build(None, None) if dmabuf else builder.build()
        except GLib.Error as e:
            log.warning("pas de zéro copie (%s) : retour à la copie", e.message)
            self.no_dmabuf = True
            builder, _ = self._screen_builder(scr)
            self.screen_texture = builder.build()
        if dmabuf and not self.dmabuf_logged:
            self.dmabuf_logged = True
            log.info("écran affiché sans copie (dmabuf)")
        n = 0
        for wid, view in self.views.items():
            if self._from_screen(wid):
                rect = self._shown_rect(wid, rects)
                x, y, w, h = rect
                if any(x0 < x + w and x < x1 and y0 < y + h and y < y1 for x0, y0, x1, y1 in rects) \
                        or view.view.texture is not self.screen_texture or tuple(rect) != view.view.src_rect:
                    view.view.set_screen(self.screen_texture, rect)
                    n += 1
        self.perf["crops"] += n
        self.frames_total += 1
        self.perf["crop_ms"] += (time.perf_counter() - t0) * 1000
        return False

    def _stall_tick(self):
        now = time.monotonic()
        late = (now - self.stall_last) * 1000 - self.stall_ms
        self.stall_last = now
        off = _suspend_offset()
        # sortie de veille : le gel des tâches avant la veille n'est pas un gel de Vasistas
        slept, self.stall_off = off - self.stall_off > 0.5, off
        if late > STALL_MS and not slept:
            st = self.stalls
            st["count"] += 1
            st["total_ms"] += late
            st["max_ms"] = max(st["max_ms"], late)
            if late > 250:
                log.warning("fil GTK gelé %.0f ms", late)
        # ponytail: sans fenêtre Windows au premier plan, un battement par seconde au lieu de
        # quatre ; un gel court (50 à 250 ms) n'y est vu qu'une fois sur quatre environ, le chien
        # de garde (WATCHDOG_S) n'est pas touché
        want = STALL_TICK_MS if self.last_active is not None and self.last_active.is_active() \
            else STALL_IDLE_TICK_MS
        if want != self.stall_ms:
            self.stall_ms = want
            GLib.timeout_add(want, self._stall_tick)
            return False
        return True

    def _watchdog(self):
        """Fil GTK figé plus de WATCHDOG_S : piles de tous les fils dans le journal, une fois par gel,
        pour trouver la cause sans rien relancer."""
        import faulthandler
        import traceback
        # VASISTAS_STALL_TRACE=1 : pile du fil GTK dès qu'il a 200 ms de retard (diagnostic des à-coups)
        trace = bool(os.environ.get("VASISTAS_STALL_TRACE"))
        dumped = traced = False
        while True:
            time.sleep(0.05 if trace else 1)
            if trace:
                late = time.monotonic() - self.stall_last - self.stall_ms / 1000
                if late > 0.2 and not traced:
                    frame = sys._current_frames().get(self.gtk_ident)
                    log.warning("fil GTK en retard de %.0f ms :\n%s", late * 1000,
                                "".join(traceback.format_stack(frame)[-12:]) if frame else "?")
                    traced = True
                elif late <= 0:
                    traced = False
            frozen = time.monotonic() - self.stall_last > WATCHDOG_S \
                and _suspend_offset() - self.stall_off < 0.5
            if frozen and not dumped:
                log.error("fil GTK figé depuis %d s, piles des fils :", WATCHDOG_S)
                faulthandler.dump_traceback(file=sys.stderr, all_threads=True)
                dumped = True
            elif not frozen:
                dumped = False

    def _sweep_orphans(self):
        """Ferme les fenêtres hôte qui ne correspondent plus à aucune fenêtre de l'invité
        (identifiant réutilisé par Windows, fermeture manquée...)."""
        live = set(self.views.values())
        for win in list(self.all_windows):
            if win not in live:
                self.all_windows.discard(win)
                try:
                    win.destroy()
                except Exception:
                    pass
        return True

    def _stats_tick(self):
        st = self.perf
        if st["damage"]:
            log.info("hôte 2 s : %d zones reçues/s, %d rafraîchissements/s, mise à jour %.1f ms, "
                     "région partielle %s", st["damage"] / 2, st["crops"] / 2,
                     st["crop_ms"] / max(1, st["crops"]), HAVE_CAIRO_REGION)
        for k in st:
            st[k] = 0
        return True

    def _refresh_from_screen(self, wids):
        """Nouvelle fenêtre, ou fenêtre déplacée / redevenue visible dans l'invité."""
        if self.screen_stale:
            self.screen_resume()  # texture en retard : image entière refaite d'abord
            return False
        if self.screen_texture is None:
            if self.screen is not None and self.screen.fb is not None:
                self._on_damage(0, 0, self.screen.width, self.screen.height)
            return
        for wid in wids:
            view = self.views.get(wid)
            if view is not None and self._from_screen(wid):
                view.view.set_screen(self.screen_texture, self._shown_rect(wid))
                if "shown_rect" in self.infos[wid]:
                    # sans zone modifiée, le nouveau cadrage s'applique au plus tard après le délai
                    GLib.timeout_add(int(RECT_HOLD_S * 1000) + 10, self._refresh_from_screen, [wid])
        return False

    def on_frame(self, wid, w, h, data):
        if self._from_screen(wid):
            return  # l'écran de QEMU fait foi
        st = self.bench_occ
        if st is not None and wid == st["wid"] and st["t0"]:
            st["lat"].append((time.perf_counter() - st["t0"]) * 1000)
            st["t0"] = 0.0
            GLib.source_remove(st["timer"])
            GLib.timeout_add(300, self._bench_occ_next)
        texture = Gdk.MemoryTexture.new(w, h, Gdk.MemoryFormat.B8G8R8A8_PREMULTIPLIED,
                                        GLib.Bytes.new(data), w * 4)
        self.textures[wid] = texture
        view = self.views.get(wid)
        if view is not None:
            view.view.set_texture(texture)


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    # Une instance précédente encore en train de quitter bloque l'enregistrement D-Bus
    # (« Failed to register ») : on réessaie quelques fois au lieu d'abandonner en silence.
    for attempt in range(5):
        app = VasistasApp()
        rc = app.run(["vasistas"] + list(argv))
        if rc == 0 or app.get_is_registered():
            return rc
        time.sleep(1)
    notify(_("Vasistas ne démarre pas"), _("Une autre instance de Vasistas, encore en cours de fermeture, bloque "
                                           "le démarrage."))
    return rc
