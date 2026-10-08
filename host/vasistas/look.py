"""Windows suit l'apparence du bureau : mode sombre, couleur d'accent, lissage des polices.

Réglages (config.json) :
- `win_theme` : vrai par défaut, mode sombre et accent d'elementary envoyés à Windows (`theme`) ;
- `font_smoothing` : « auto » (rien n'est touché), « grayscale » ou « cleartype » (`fonts`).
"""

import logging

from gi.repository import Gio

from . import vm

log = logging.getLogger(__name__)

# Accents d'elementary (thème io.elementary.stylesheet.<nom>), teinte 500 de la palette
ACCENTS = {
    "strawberry": "#c6262e", "orange": "#f37329", "banana": "#f9c440", "lime": "#68b723",
    "mint": "#28bca3", "blueberry": "#3689e6", "grape": "#a56de2", "bubblegum": "#de3e80",
    "cocoa": "#715344", "slate": "#485a6c",
}
# noms de la clé accent-color de GNOME, rapprochés de la palette d'elementary
GNOME_ACCENTS = {"blue": "blueberry", "teal": "mint", "green": "lime", "yellow": "banana",
                 "orange": "orange", "red": "strawberry", "pink": "bubblegum", "purple": "grape",
                 "slate": "slate"}
SMOOTHING = ("auto", "grayscale", "cleartype")


def accent_from(gtk_theme, accent_color=None):
    """Couleur #rrggbb d'après le thème GTK d'elementary, sinon la clé accent-color de GNOME."""
    name = (gtk_theme or "").rsplit(".", 1)[-1]
    if name in ACCENTS:
        return ACCENTS[name]
    return ACCENTS.get(GNOME_ACCENTS.get(accent_color or ""))


def theme_message(dark, accent):
    msg = {"t": "theme", "dark": bool(dark)}
    if accent:
        msg["accent"] = accent
    return msg


class LookSync:
    """Envoie l'apparence au démarrage de l'agent et à chaque changement sous Linux."""

    def __init__(self, send):
        self.send = send
        self.last = None
        self.iface = Gio.Settings.new("org.gnome.desktop.interface")
        self.iface.connect("changed", self._changed)
        self.granite = None
        try:
            import gi
            gi.require_version("Granite", "7.0")
            from gi.repository import Granite
            self.granite = Granite.Settings.get_default()
            self.granite.connect("notify::prefers-color-scheme", self._changed)
        except (ImportError, ValueError):
            pass

    def dark(self):
        # la clé du bureau d'abord : Granite lit le portail de façon asynchrone et répond
        # « pas de préférence » tant qu'il n'a pas reçu la valeur (constaté au démarrage)
        if self.iface.get_string("color-scheme") == "prefer-dark":
            return True
        if self.granite is not None:
            from gi.repository import Granite
            return self.granite.get_property("prefers-color-scheme") == Granite.SettingsColorScheme.DARK
        return False

    def accent(self):
        keys = self.iface.list_keys()
        return accent_from(self.iface.get_string("gtk-theme"),
                           self.iface.get_string("accent-color") if "accent-color" in keys else None)

    def _changed(self, *args):
        self.push(force=False)

    def push(self, force=True):
        """À l'arrivée de l'agent (force) ou sur un changement de réglage du bureau."""
        cfg = vm.load_config()
        if cfg.get("win_theme", True) is not False:
            msg = theme_message(self.dark(), self.accent())
            if force or msg != self.last:
                self.last = msg
                log.info("apparence envoyée à Windows : %s", msg)
                self.send(msg)
        smoothing = cfg.get("font_smoothing", "auto")
        if force and smoothing in SMOOTHING and smoothing != "auto":
            self.send({"t": "fonts", "smoothing": smoothing})
