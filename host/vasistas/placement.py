"""Écrans et place des fenêtres Windows : écran choisi par application, taille et écran
mémorisés pour chaque configuration d'écrans, garde-fou de taille, réinitialisation.

Sous Wayland, une application ne choisit ni sa position ni son écran : c'est Gala qui place
les fenêtres. Deux comportements de Gala (vérifiés dans un Gala isolé à deux écrans) servent :
- une fenêtre passée en plein écran sur un écran puis rétablie revient centrée sur cet écran ;
- une fenêtre masquée puis réaffichée est centrée sur l'écran actif.

Réglages (config.json, clé `screens`) :
    {"default": "auto", "apps": {"powerbi": "DP-2"}, "reset_on_change": true, "clamp": true}
Écran : « auto » (celui que choisit Gala, l'écran actif), « last » (le dernier utilisé par
l'application avec ces écrans-là) ou le nom d'un connecteur (« eDP-1 », « DP-2 »).
"""

import json
import logging

from . import vm

log = logging.getLogger(__name__)

GEOMETRY = vm.DATA / "windows.json"
AUTO, LAST = "auto", "last"
# taille au plus, en part de l'écran : à l'ouverture (garde-fou) et à la réinitialisation
CLAMP_SHARE = 0.9
RESET_SHARE = 0.8
# Gala a besoin d'un court délai entre l'affichage, le plein écran et le retour (mesuré :
# sans délai, la fenêtre reste dans le coin de l'écran au lieu d'être centrée)
MOVE_AFTER_MAP_MS = 300
UNFULLSCREEN_MS = 250

DEFAULTS = {"default": AUTO, "apps": {}, "reset_on_change": True, "clamp": True}


def settings(config=None):
    cfg = (config if config is not None else vm.load_config()).get("screens") or {}
    out = dict(DEFAULTS)
    out.update({k: v for k, v in cfg.items() if k in DEFAULTS})
    out["apps"] = dict(out.get("apps") or {})
    return out


def save_settings(values):
    cfg = vm.load_config()
    cur = settings(cfg)
    cur.update(values)
    cfg["screens"] = cur
    vm.save_config(cfg)


def choice_for(app, config=None):
    """Écran voulu pour une application : son réglage, sinon celui par défaut."""
    s = settings(config)
    from .power import APP_ALIASES
    # réglage écrit sous un autre nom de la même appli (« powerbi » pour pbidesktop)
    names = [app] + [alias for alias, ids in APP_ALIASES.items() if app in ids]
    return next((s["apps"][n] for n in names if s["apps"].get(n)), None) or s["default"] or AUTO


# -- description des écrans (fonctions pures sur des tuples, testables sans GTK) --

def monitor_key(connector, width, height, scale):
    return f"{connector}:{width}x{height}@{round(scale, 3)}"


def signature(monitors):
    """Identifiant d'une configuration d'écrans : [(connecteur, l, h, échelle)], ordre libre."""
    return "|".join(sorted(monitor_key(*m) for m in monitors))


def clamp_size(w, h, area_w, area_h, share):
    """Taille ramenée à `share` de l'écran au plus, proportions de chaque axe gardées à part
    (une fenêtre étroite et très haute ne devient pas minuscule en largeur)."""
    return min(w, max(1, int(area_w * share))), min(h, max(1, int(area_h * share)))


def target_connector(choice, connectors, last=None):
    """Connecteur où ouvrir la fenêtre, ou None pour laisser Gala choisir."""
    if choice == LAST:
        return last if last in connectors else None
    if choice and choice != AUTO:
        return choice if choice in connectors else None
    return None


# -- mémoire de la taille et de l'écran par application --

def load_geometry():
    try:
        return json.loads(GEOMETRY.read_text())
    except (OSError, ValueError):
        return {}


def remembered(sig, app):
    return (load_geometry().get(sig) or {}).get(app)


def remember(sig, app, connector, w, h, maximized):
    data = load_geometry()
    entry = data.setdefault(sig, {})
    old = entry.get(app)
    new = {"monitor": connector, "w": int(w), "h": int(h), "max": bool(maximized)}
    if old == new:
        return
    entry[app] = new
    try:
        GEOMETRY.parent.mkdir(parents=True, exist_ok=True)
        GEOMETRY.write_text(json.dumps(data, indent=1))
    except OSError as e:
        log.warning("mémoire des fenêtres : %s", e)


def forget_all():
    try:
        GEOMETRY.unlink()
    except FileNotFoundError:
        pass


# -- côté GTK --

def gdk_monitors():
    from gi.repository import Gdk
    monitors = Gdk.Display.get_default().get_monitors()
    return [monitors.get_item(i) for i in range(monitors.get_n_items())]


def describe(monitor):
    g = monitor.get_geometry()
    return (monitor.get_connector() or "?", g.width, g.height, monitor.get_scale())


def current_signature():
    return signature([describe(m) for m in gdk_monitors()])


def by_connector(connector):
    return next((m for m in gdk_monitors() if m.get_connector() == connector), None)


def monitor_of(window):
    from gi.repository import Gdk
    surface = window.get_surface() if window.get_realized() else None
    if surface is None:
        return None
    return Gdk.Display.get_default().get_monitor_at_surface(surface)


def label(monitor):
    """Nom lisible d'un écran : « Écran du portable », sinon fabricant et modèle."""
    from .i18n import _
    connector = monitor.get_connector() or ""
    g = monitor.get_geometry()
    size = f"{round(g.width * monitor.get_scale())}×{round(g.height * monitor.get_scale())}"
    if connector.startswith(("eDP", "LVDS", "DSI")):
        name = _("Écran du portable")
    else:
        name = " ".join(p for p in (monitor.get_manufacturer(), monitor.get_model()) if p) or connector
    return f"{name} ({connector}, {size})"


def move_to(window, monitor, done=None):
    """Pose `window` au centre de `monitor` : plein écran sur cet écran, puis retour."""
    from gi.repository import GLib
    if monitor is None:
        return
    was_max = window.is_maximized()
    window.moving = True

    def settled():
        window.moving = False
        if was_max:
            window.maximize()
        # taille réelle après le retour : Windows la reçoit maintenant
        window.on_view_resized(*window.view.alloc)
        if done is not None:
            done()
        return False

    def back():
        window.unfullscreen()
        GLib.timeout_add(UNFULLSCREEN_MS, settled)
        return False

    window.fullscreen_on_monitor(monitor)
    GLib.timeout_add(UNFULLSCREEN_MS, back)
