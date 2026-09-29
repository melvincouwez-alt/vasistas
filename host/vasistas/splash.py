"""Carte de chargement affichée pendant le démarrage de Windows et l'ouverture d'une appli.

Petite fenêtre sans décoration, au style elementary (carte arrondie, couleurs du thème),
avec l'icône de l'application et les cinq points qui tournent du démarrage de Windows.
Elle porte l'identifiant Wayland de l'application lancée : le dock montre déjà son icône.
"""

import logging
import math
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkWayland", "4.0")
from gi.repository import GdkWayland, GLib, Gtk  # noqa: E402

log = logging.getLogger(__name__)

CSS = b"""
window.vasistas-splash { background: none; }
window.vasistas-splash .card {
    background: @bg_color;
    border-radius: 12px;
    padding: 18px 22px;
    margin: 12px;
    box-shadow: 0 0 0 1px alpha(black, 0.12), 0 6px 18px alpha(black, 0.22);
}
window.vasistas-splash .splash-title { font-weight: bold; font-size: 1.15em; }
window.vasistas-splash .splash-stage { opacity: 0.7; }
window.vasistas-splash .splash-dots { color: @accent_color; }
window.vasistas-splash.performance .splash-dots { color: #76b900; }
window.vasistas-splash .splash-chip {
    font-size: 0.8em; font-weight: bold;
    color: #3a5c00; background: alpha(#76b900, 0.18);
    border-radius: 999px; padding: 1px 8px; margin-top: 4px;
}
window.vasistas-splash.battery .splash-chip { color: @fg_color; background: alpha(@fg_color, 0.1); }
window.vasistas-splash .splash-error { color: @error_color; opacity: 1; }
"""

DOTS = 5
PERIOD_S = 2.2          # un tour complet d'un point
DOT_DELAY_S = 0.11      # écart entre deux points qui se suivent
SIZE = 36

# pastille selon la configuration de la VM (réglée par l'application compagnon)
MODES = {
    "performance": "⚡ Haute performance",   # carte graphique dédiée à la VM
    "battery": "Économie d'énergie",
}


class Splash(Gtk.Window):
    def __init__(self, title, stage, icon_name, app_id=None, mode=None, on_closed=None):
        """`mode` : configuration de la VM affichée en pastille (voir MODES)."""
        super().__init__(decorated=False, resizable=False)
        self.add_css_class("vasistas-splash")
        self.on_closed = on_closed
        if mode in MODES:
            self.add_css_class(mode)
        self.set_title(title)
        self.app_id = app_id
        self.start = time.monotonic()
        self.closing = False

        card = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        card.add_css_class("card")
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(48)
        card.append(icon)

        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        self.title_label = Gtk.Label(label=title, xalign=0)
        self.title_label.add_css_class("splash-title")
        self.stage_label = Gtk.Label(label=stage, xalign=0, width_chars=26, max_width_chars=34, wrap=True)
        self.stage_label.add_css_class("splash-stage")
        texts.append(self.title_label)
        texts.append(self.stage_label)
        if mode in MODES:
            chip = Gtk.Label(label=MODES[mode], xalign=0, halign=Gtk.Align.START)
            chip.add_css_class("splash-chip")
            texts.append(chip)
        card.append(texts)

        self.dots = Gtk.DrawingArea(content_width=SIZE, content_height=SIZE, valign=Gtk.Align.CENTER)
        self.dots.add_css_class("splash-dots")
        self.dots.set_draw_func(self._draw)
        card.append(self.dots)
        self.set_child(card)

        self.connect("map", self._on_map)
        self.tick = self.dots.add_tick_callback(lambda *a: (self.dots.queue_draw(), True)[1])

    def _on_map(self, _):
        surface = self.get_surface()
        if self.app_id and isinstance(surface, GdkWayland.WaylandToplevel):
            surface.set_application_id(self.app_id)

    def set_stage(self, text, error=False):
        self.stage_label.set_label(text)
        if error:
            self.stage_label.add_css_class("splash-error")
            if self.tick:
                self.dots.remove_tick_callback(self.tick)
                self.tick = 0
            self.dots.set_visible(False)

    def finish(self, delay_ms=0):
        """Fondu puis fermeture."""
        if self.closing:
            return
        self.closing = True

        def fade():
            op = self.get_opacity() - 0.12
            if op <= 0.03:
                self.destroy()
                if self.on_closed:
                    self.on_closed(self)
                return False
            self.set_opacity(op)
            return True

        GLib.timeout_add(delay_ms, lambda: GLib.timeout_add(16, fade) and False)

    # Cinq points sur un cercle : chacun accélère en bas et ralentit en haut, comme
    # l'animation de démarrage de Windows, en couleur d'accent du thème.
    def _draw(self, area, cr, w, h):
        color = area.get_color()
        t = time.monotonic() - self.start
        cx, cy, r = w / 2, h / 2, min(w, h) / 2 - 3
        dot = max(1.6, SIZE / 14)
        for i in range(DOTS):
            p = ((t - i * DOT_DELAY_S) % PERIOD_S) / PERIOD_S
            if t - i * DOT_DELAY_S < 0:
                continue
            # vitesse variable : lent en haut (p≈0), rapide en bas
            a = 2 * math.pi * (p - math.sin(2 * math.pi * p) / (2 * math.pi) * 0.75)
            x, y = cx + r * math.sin(a), cy - r * math.cos(a)
            cr.set_source_rgba(color.red, color.green, color.blue, color.alpha)
            cr.arc(x, y, dot, 0, 2 * math.pi)
            cr.fill()
