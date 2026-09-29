"""Fenêtres GTK qui affichent une fenêtre de l'invité et lui renvoient les entrées."""

import logging
import time

import gi

gi.require_version("GdkWayland", "4.0")
from gi.repository import Gdk, GdkWayland, GLib, Graphene, Gsk, Gtk  # noqa: E402

from . import keymap

log = logging.getLogger(__name__)

# Codes WM_NCHITTEST
HTCAPTION = 2
HT_EDGES = {
    10: Gdk.SurfaceEdge.WEST,
    11: Gdk.SurfaceEdge.EAST,
    12: Gdk.SurfaceEdge.NORTH,
    13: Gdk.SurfaceEdge.NORTH_WEST,
    14: Gdk.SurfaceEdge.NORTH_EAST,
    15: Gdk.SurfaceEdge.SOUTH,
    16: Gdk.SurfaceEdge.SOUTH_WEST,
    17: Gdk.SurfaceEdge.SOUTH_EAST,
}
DOUBLE_CLICK_S = 0.4
# Barre de titre : Office (recherche, nom du fichier), l'Explorateur ou Edge y placent des
# commandes qu'ils gèrent eux-mêmes alors que WM_NCHITTEST y répond HTCAPTION. Le clic part donc
# vers Windows ; le déplacement de la fenêtre Linux ne commence qu'au-delà de ce glissement.
CAPTION_DRAG_PX = 4
HOST_EDGE_PX = 6
EDGE_CURSORS = {
    Gdk.SurfaceEdge.NORTH: "ns-resize", Gdk.SurfaceEdge.SOUTH: "ns-resize",
    Gdk.SurfaceEdge.WEST: "ew-resize", Gdk.SurfaceEdge.EAST: "ew-resize",
    Gdk.SurfaceEdge.NORTH_WEST: "nwse-resize", Gdk.SurfaceEdge.SOUTH_EAST: "nwse-resize",
    Gdk.SurfaceEdge.NORTH_EAST: "nesw-resize", Gdk.SurfaceEdge.SOUTH_WEST: "nesw-resize",
}
MOVE_INTERVAL_MS = 8  # au plus 120 mouvements de souris par seconde vers l'invité
RESIZE_DEBOUNCE_MS = 40
RESIZE_THROTTLE_MS = 120
SIZE_TOLERANCE_PX = 2
# Gala (WindowStateSaver) redonne à chaque fenêtre la taille et la place qu'avait la n-ième
# fenêtre de la même application : juste après l'affichage, la taille de Windows fait foi.
MAP_GRACE_MS = 1500
MAX_SIZE_FIXES = 2


def scale_of(widget) -> float:
    native = widget.get_native()
    if native is None:
        return 1.0
    surface = native.get_surface()
    if surface is None:
        return float(widget.get_scale_factor())
    return surface.get_scale()


class GuestView(Gtk.Picture):
    """Image d'une fenêtre de l'invité, plus la gestion souris.

    `owner` fournit send(), le modèle de la fenêtre et, pour une fenêtre de premier
    niveau, la surface à déplacer ou redimensionner."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.set_content_fit(Gtk.ContentFit.FILL)
        self.set_can_shrink(True)
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_focusable(True)
        self.hit = 1
        self.buttons = set()
        self.last_caption_click = 0.0
        self.caption_press = None   # clic en cours dans la barre de titre, pas encore un glissement
        self.swallowed = set()      # boutons dont le relâchement ne va pas à Windows
        self.scroll_acc = [0.0, 0.0]
        self.alloc = (0, 0)
        self.pending_move = None
        self.move_source = 0
        self.on_edge = False
        self.cursor_name = "default"
        self.texture = None
        self.src_rect = None
        legacy = Gtk.EventControllerLegacy()
        legacy.connect("event", self._on_event)
        self.add_controller(legacy)
        # molette : contrôleur dédié (le contrôleur générique ne reçoit pas ces événements)
        self.last_guest_pos = None
        scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.BOTH_AXES)
        scroll.connect("scroll", self._on_scroll)
        self.add_controller(scroll)

    def set_texture(self, texture):
        """Image propre à la fenêtre (tuiles de l'agent)."""
        self.texture = texture
        self.src_rect = None
        self.queue_draw()

    def set_screen(self, texture, rect):
        """Portion `rect` (x, y, w, h en pixels invité) de la texture de tout l'écran."""
        self.texture = texture
        self.src_rect = tuple(rect)
        self.queue_draw()

    def do_snapshot(self, snapshot):
        # Pixel pour pixel, sans lissage : le filtrage linéaire rend le texte flou au
        # moindre écart d'un pixel entre l'image et la fenêtre.
        texture = self.texture
        if texture is None:
            return
        # Échelle fixe (1 pixel invité = 1 pixel écran) : pendant un redimensionnement,
        # l'image garde sa taille le temps que Windows redessine, au lieu d'être étirée.
        k = 1 / scale_of(self)
        W, H = self.get_width(), self.get_height()
        x, y, w, h = self.src_rect or (0, 0, texture.get_width(), texture.get_height())
        snapshot.push_clip(Graphene.Rect().init(0, 0, min(W, w * k), min(H, h * k)))
        snapshot.append_scaled_texture(
            texture, Gsk.ScalingFilter.NEAREST,
            Graphene.Rect().init(-x * k, -y * k, texture.get_width() * k, texture.get_height() * k))
        snapshot.pop()

    def do_size_allocate(self, width, height, baseline):
        Gtk.Picture.do_size_allocate(self, width, height, baseline)
        if (width, height) != self.alloc:
            self.alloc = (width, height)
            self.owner.on_view_resized(width, height)

    # -- souris --

    def _on_scroll(self, ctrl, dx, dy):
        if self.last_guest_pos is None:
            return False
        # une molette donne ±1 par cran ; un pavé tactile donne des pixels
        unit = ctrl.get_unit() if hasattr(ctrl, "get_unit") else Gdk.ScrollUnit.WHEEL
        factor = 120 if unit == Gdk.ScrollUnit.WHEEL else 12
        self._flush_move()
        self.scroll_acc[0] += dx * factor
        self.scroll_acc[1] -= dy * factor
        ix, iy = int(self.scroll_acc[0]), int(self.scroll_acc[1])
        if ix or iy:
            self.scroll_acc[0] -= ix
            self.scroll_acc[1] -= iy
            gx, gy = self.last_guest_pos
            self.owner.send({"t": "mouse.wheel", "id": self.owner.wid, "x": gx, "y": gy, "dx": ix, "dy": iy})
        return True

    def _to_guest(self, event):
        ok, sx, sy = event.get_position()
        if not ok:
            return None
        native = self.get_native()
        tx, ty = native.get_surface_transform()
        res = native.translate_coordinates(self, sx - tx, sy - ty)
        if not res:
            return None
        x, y = res[-2:]  # (x, y) ou (ok, x, y) selon la version de PyGObject
        s = scale_of(self)
        return int(x * s), int(y * s), sx, sy

    def _on_event(self, ctrl, event):
        # PyGObject ne sait pas passer un GdkEvent dans ce signal : il arrive à None
        event = event or ctrl.get_current_event()
        if event is None:
            return False
        et = event.get_event_type()
        if et not in (Gdk.EventType.MOTION_NOTIFY, Gdk.EventType.BUTTON_PRESS,
                      Gdk.EventType.BUTTON_RELEASE):
            return False
        pos = self._to_guest(event)
        if pos is None:
            return False
        gx, gy, sx, sy = pos
        wid = self.owner.wid
        send = self.owner.send

        edge = self._host_edge(event)
        if et == Gdk.EventType.MOTION_NOTIFY and self.caption_press is not None:
            cp = self.caption_press
            if abs(sx - cp["sx"]) < CAPTION_DRAG_PX and abs(sy - cp["sy"]) < CAPTION_DRAG_PX:
                return True  # petit tremblement : toujours un clic, Windows ne voit pas bouger
            # glissement : Windows reçoit un clic sur place (sans effet sur la barre de titre) et
            # la fenêtre Linux suit le pointeur
            self.caption_press = None
            self.buttons.discard(cp["button"])
            send({"t": "mouse.button", "id": wid, "x": cp["gx"], "y": cp["gy"], "button": cp["button"],
                  "down": False})
            toplevel = self.owner.toplevel_surface()
            if toplevel is not None:
                toplevel.begin_move(cp["device"], cp["button"], cp["sx"], cp["sy"], event.get_time())
            return True
        if et == Gdk.EventType.MOTION_NOTIFY:
            if edge is not None:
                # bordure de redimensionnement gérée par l'hôte : l'invité ne voit rien
                self.set_cursor_from_name(EDGE_CURSORS[edge])
                self.on_edge = True
                return True
            if self.on_edge:
                self.on_edge = False
                self._apply_cursor()
            self.last_guest_pos = (gx, gy)
            self.pending_move = {"t": "mouse.move", "id": wid, "x": gx, "y": gy}
            if not self.move_source:
                self.move_source = GLib.timeout_add(MOVE_INTERVAL_MS, self._on_move_timer)
            return True

        self._flush_move()


        button = event.get_button()
        if et == Gdk.EventType.BUTTON_PRESS:
            if self.get_focusable():
                self.grab_focus()
            toplevel = self.owner.toplevel_surface()
            if button == 1 and toplevel is not None and edge is not None:
                toplevel.begin_resize(edge, event.get_device(), button, sx, sy, event.get_time())
                return True
            if button == 1 and toplevel is not None:
                if self.hit == HTCAPTION:
                    if time.monotonic() - self.last_caption_click < DOUBLE_CLICK_S:
                        # double-clic : agrandi ou rétabli par l'hôte ; Windows ne voit que le
                        # premier clic (sinon il agrandirait de son côté et contredirait l'hôte)
                        self.last_caption_click = 0.0
                        self.swallowed.add(button)
                        self.owner.toggle_maximize()
                        return True
                    self.caption_press = {"gx": gx, "gy": gy, "sx": sx, "sy": sy, "button": button,
                                          "device": event.get_device()}
                    self.buttons.add(button)
                    send({"t": "mouse.move", "id": wid, "x": gx, "y": gy})
                    send({"t": "mouse.button", "id": wid, "x": gx, "y": gy, "button": button, "down": True})
                    return True
                edge = HT_EDGES.get(self.hit)
                if edge is not None:
                    toplevel.begin_resize(edge, event.get_device(), button, sx, sy, event.get_time())
                    return True
            self.buttons.add(button)
            send({"t": "mouse.move", "id": wid, "x": gx, "y": gy})
            send({"t": "mouse.button", "id": wid, "x": gx, "y": gy, "button": button, "down": True})
            return True

        # BUTTON_RELEASE
        if button in self.swallowed:
            self.swallowed.discard(button)
            return True
        if self.caption_press is not None and self.caption_press["button"] == button:
            # clic simple dans la barre de titre : peut-être le premier d'un double-clic
            self.caption_press = None
            self.last_caption_click = time.monotonic()
        if button in self.buttons:
            self.buttons.discard(button)
            send({"t": "mouse.button", "id": wid, "x": gx, "y": gy, "button": button, "down": False})
        return True

    def _on_move_timer(self):
        self.move_source = 0
        self._flush_move()
        return False

    def _flush_move(self):
        if self.move_source:
            GLib.source_remove(self.move_source)
            self.move_source = 0
        if self.pending_move is not None:
            self.owner.send(self.pending_move)
            self.pending_move = None
        return False

    def set_hover(self, hit, cursor):
        self.hit = hit
        self.cursor_name = cursor or "default"
        if not self.on_edge:
            self._apply_cursor()

    def _apply_cursor(self):
        name = self.cursor_name
        if name.startswith("img:"):
            cur = self.owner.app.cursors.get(name)
            if cur is not None:
                self.set_cursor(cur)
                return
            name = "default"
        self.set_cursor_from_name(name)

    def _host_edge(self, event):
        """Bord de fenêtre sous le pointeur, si la fenêtre est redimensionnable.
        Windows 11 met ses poignées dans des bordures invisibles hors de la zone visible,
        que l'hôte n'affiche pas : on offre les nôtres, sur une marge de quelques pixels."""
        toplevel = self.owner.toplevel_surface()
        win = self.get_root()
        if toplevel is None or not isinstance(win, Gtk.Window) or not win.get_resizable() \
                or win.is_maximized() or win.is_fullscreen():
            return None
        ok, sx, sy = event.get_position()
        if not ok:
            return None
        w, h = win.get_width(), win.get_height()
        m = HOST_EDGE_PX
        west, east = sx < m, sx >= w - m
        north, south = sy < m, sy >= h - m
        key = ("N" if north else "S" if south else "") + ("W" if west else "E" if east else "")
        return {"N": Gdk.SurfaceEdge.NORTH, "S": Gdk.SurfaceEdge.SOUTH, "W": Gdk.SurfaceEdge.WEST,
                "E": Gdk.SurfaceEdge.EAST, "NW": Gdk.SurfaceEdge.NORTH_WEST, "NE": Gdk.SurfaceEdge.NORTH_EAST,
                "SW": Gdk.SurfaceEdge.SOUTH_WEST, "SE": Gdk.SurfaceEdge.SOUTH_EAST}.get(key)


class KeyboardMixin:
    """Clavier capturé au niveau de la fenêtre GTK, transmis en scancodes."""

    def _setup_keyboard(self):
        self.held_keys = set()
        ctrl = Gtk.EventControllerKey()
        ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        ctrl.connect("key-pressed", self._on_key, True)
        ctrl.connect("key-released", self._on_key, False)
        self.add_controller(ctrl)

    def _on_key(self, ctrl, keyval, keycode, state, down):
        msg = keymap.xkb_to_message(keycode, down)
        if msg is None:
            return False
        if down:
            self.held_keys.add(keycode)
        else:
            self.held_keys.discard(keycode)
        self.send(msg)
        return True

    def release_keys(self):
        for keycode in list(self.held_keys):
            msg = keymap.xkb_to_message(keycode, False)
            if msg:
                self.send(msg)
        self.held_keys.clear()


class GuestWindow(KeyboardMixin, Gtk.Window):
    """Fenêtre de premier niveau : normale, dialogue, ou popup sans parent."""

    def __init__(self, app, info):
        # Pas rattachée à la Gtk.Application : GTK annoncerait son identifiant D-Bus au
        # compositeur, et le dock regrouperait toutes les fenêtres sous Vasistas.
        super().__init__()
        self.app = app
        self.wid = info["id"]
        self.kind = info.get("kind", "normal")
        self.guest_size = (0, 0)
        self.resize_source = 0
        self.last_resize_ms = 0
        self.watched_surface = None
        self.set_decorated(False)
        self.add_css_class("vasistas")
        self.view = GuestView(self)
        self.set_child(self.view)
        self.set_resizable(self.kind != "popup")
        self._setup_keyboard()
        self.connect("close-request", self._on_close_request)
        self.connect("notify::is-active", self._on_active)
        # réduite ou entièrement masquée sous Linux : l'agent cesse de la capturer
        self.connect("notify::suspended", lambda w, _p: self.send(
            {"t": "window.hosthidden", "id": self.wid, "hidden": w.is_suspended()}))
        self.app_name = info.get("app")
        self.map_time = 0
        self.size_fixes = 0
        self.connect("realize", self._on_realize)
        # GTK repose l'identifiant de l'application à l'affichage : on repasse derrière
        self.connect("map", self._on_realize)
        self.connect("map", self._on_map_size)
        self.update(info)

    def send(self, msg):
        self.app.send(msg)

    def _on_realize(self, win):
        surface = self.get_surface()
        if surface is not None and surface is not self.watched_surface:
            # fenêtre passée sur un autre écran, ou échelle de l'écran modifiée
            self.watched_surface = surface
            surface.connect("enter-monitor", self._on_surface_changed)
            surface.connect("notify::scale", self._on_surface_changed)
        # Identifiant Wayland propre à l'application Windows : le dock regroupe ses
        # fenêtres sous le lanceur APP_ID.<app> et en prend l'icône.
        if not self.app_name:
            log.debug("fenêtre %s sans application", self.wid)
            return
        from .desktop import app_desktop_id
        app_id = app_desktop_id(self.app_name)
        if isinstance(surface, GdkWayland.WaylandToplevel):
            surface.set_application_id(app_id)
            log.debug("fenêtre %s : app_id %s", self.wid, app_id)
        else:
            log.debug("fenêtre %s : surface %s, app_id non posé", self.wid, type(surface).__name__)

    def _on_surface_changed(self, *args):
        # L'invité change d'échelle si c'est la fenêtre de travail ; Windows renvoie
        # ensuite la nouvelle taille (rect). En attendant, taille hôte recalculée.
        if self.app.last_active is self:
            self.app.schedule_update_scale()
        w, h = self.guest_size
        if w and h and not self.is_maximized():
            s = scale_of(self)
            self.set_default_size(max(1, round(w / s)), max(1, round(h / s)))
        self.view.queue_draw()

    def toplevel_surface(self):
        return self.get_surface() if self.kind != "popup" else None

    def update(self, info):
        if "title" in info:
            self.set_title(info["title"] or "Vasistas")
        if "rect" in info:
            _, _, w, h = info["rect"]
            self.guest_size = (w, h)
            if not self.is_maximized():
                s = scale_of(self) if self.get_realized() else self.app.scale
                self.set_default_size(max(1, round(w / s)), max(1, round(h / s)))
            elif self.get_realized():
                # fenêtre hôte agrandie : c'est elle qui impose sa taille à l'invité
                self.on_view_resized(*self.view.alloc)

    def _on_map_size(self, win):
        self.map_time = GLib.get_monotonic_time() // 1000
        self.size_fixes = 0

    def _imposed_size(self, width, height):
        """Vrai si le compositeur vient d'imposer une taille à la fenêtre tout juste affichée
        (géométrie mémorisée par Gala) : on lui redemande celle de Windows au lieu de
        rétrécir la fenêtre Windows. Deux essais au plus (une fenêtre plus grande que
        l'écran reste contrainte par le compositeur : sa taille gagne alors)."""
        if not self.map_time or self.is_maximized() or self.size_fixes >= MAX_SIZE_FIXES:
            return False
        if GLib.get_monotonic_time() // 1000 - self.map_time > MAP_GRACE_MS:
            return False
        gw, gh = self.guest_size
        s = scale_of(self)
        if not gw or not gh or (abs(round(width * s) - gw) <= SIZE_TOLERANCE_PX
                                and abs(round(height * s) - gh) <= SIZE_TOLERANCE_PX):
            return False
        self.size_fixes += 1
        lw, lh = max(1, round(gw / s)), max(1, round(gh / s))
        log.info("fenêtre %s : %dx%d imposé par le compositeur, retour à %dx%d", self.wid, width, height, lw, lh)

        def fix():
            # même valeur que la taille par défaut actuelle : GTK ne redemanderait rien
            self.set_default_size(lw + 1, lh)
            self.set_default_size(lw, lh)
            return False
        GLib.idle_add(fix)
        return True

    def on_view_resized(self, width, height):
        if self.kind == "popup":
            return
        if self._imposed_size(width, height):
            return
        # Pendant un glissement : une demande toutes les RESIZE_THROTTLE_MS au plus (Office
        # recalcule toute sa mise en page à chaque taille), plus une dernière à l'arrêt.
        if self.resize_source:
            GLib.source_remove(self.resize_source)
        now = GLib.get_monotonic_time() // 1000
        wait = max(RESIZE_DEBOUNCE_MS, RESIZE_THROTTLE_MS - (now - self.last_resize_ms))
        self.resize_source = GLib.timeout_add(int(wait), self._send_resize)

    def _send_resize(self):
        self.resize_source = 0
        self.last_resize_ms = GLib.get_monotonic_time() // 1000
        w, h = self.view.alloc
        s = scale_of(self)
        pw, ph = round(w * s), round(h * s)
        gw, gh = self.guest_size
        if abs(pw - gw) > SIZE_TOLERANCE_PX or abs(ph - gh) > SIZE_TOLERANCE_PX:
            # petite taille : trace pour retrouver qui rétrécit les fenêtres (Word à 400x248)
            (log.info if pw < 640 or ph < 400 else log.debug)(
                "fenêtre %s : %dx%d demandé (invité %dx%d, vue %dx%d, échelle %.3f, réalisée %s, visible %s)",
                self.wid, pw, ph, gw, gh, w, h, s, self.get_realized(), self.get_mapped())
            self.send({"t": "window.resize", "id": self.wid, "w": pw, "h": ph})
        return False

    def toggle_maximize(self):
        if self.is_maximized():
            self.unmaximize()
        else:
            self.maximize()

    def request(self, action):
        # l'invité a été agrandi (bouton, démarrage agrandi) : on agrandit, sans basculer
        if action == "maximize":
            if self.is_maximized():
                self.on_view_resized(*self.view.alloc)
            else:
                self.maximize()
        elif action == "minimize":
            self.minimize()

    def _on_close_request(self, win):
        self.send({"t": "window.close", "id": self.wid})
        return True  # la fenêtre disparaît quand l'invité confirme

    def _on_active(self, win, pspec):
        if self.is_active():
            self.app.window_activated(self)
        else:
            self.app.window_deactivated(self)

    def destroy_view(self):
        self.app.forget(self.wid)
        self.destroy()


class GuestPopup(Gtk.Popover):
    """Menu, info-bulle ou liste déroulante de l'invité, ancré dans sa fenêtre parente."""

    def __init__(self, app, info, parent_view, parent_origin):
        super().__init__()
        self.app = app
        self.wid = info["id"]
        self.parent_origin = parent_origin  # coin de la fenêtre parente, pixels invité
        self.rect = info["rect"]
        self.parent_view = parent_view
        self.add_css_class("vasistas")
        self.set_autohide(False)
        self.set_has_arrow(False)
        self.set_can_focus(False)
        self.set_position(Gtk.PositionType.BOTTOM)
        self.view = GuestView(self)
        self.view.set_focusable(False)
        self.set_child(self.view)
        self.set_parent(parent_view)
        self._place()
        self.popup()

    def send(self, msg):
        self.app.send(msg)

    def toplevel_surface(self):
        return None

    def toggle_maximize(self):
        pass

    def on_view_resized(self, width, height):
        pass

    def update(self, info):
        if "rect" in info:
            self.rect = info["rect"]
            self._place()

    def set_parent_origin(self, origin):
        self.parent_origin = origin
        self._place()

    def _place(self):
        x, y, w, h = self.rect
        ox, oy = self.parent_origin
        # échelle de l'écran où se trouve la fenêtre parente
        s = scale_of(self.parent_view) if self.parent_view.get_realized() else self.app.scale
        lw, lh = max(1, round(w / s)), max(1, round(h / s))
        self.view.set_size_request(lw, lh)
        rect = Gdk.Rectangle()
        rect.x = round((x - ox) / s)
        rect.y = round((y - oy) / s) - 1
        rect.width = lw
        rect.height = 1
        self.set_pointing_to(rect)

    def destroy_view(self):
        self.app.forget(self.wid)
        self.popdown()
        self.unparent()


CSS = b"""
window.vasistas { background: black; }
popover.vasistas { background: none; padding: 0; margin: 0; box-shadow: none; }
popover.vasistas > contents {
    padding: 0; margin: 0; border: none; border-radius: 0;
    box-shadow: none; background: none;
}
"""
