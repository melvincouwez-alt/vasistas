"""Codes evdev vers scancodes PC jeu 1.

GTK sur Wayland donne des keycodes XKB : evdev + 8.
Les codes evdev 1 à 88 reprennent directement le jeu 1 (hors trous), les touches
étendues demandent le préfixe E0.
"""

# evdev -> (scancode, étendu)
_EXTENDED = {
    96: 0x1C,   # KP_ENTER
    97: 0x1D,   # RIGHTCTRL
    98: 0x35,   # KP_SLASH
    99: 0x37,   # SYSRQ / Impr écran
    100: 0x38,  # RIGHTALT / AltGr
    102: 0x47,  # HOME
    103: 0x48,  # UP
    104: 0x49,  # PAGEUP
    105: 0x4B,  # LEFT
    106: 0x4D,  # RIGHT
    107: 0x4F,  # END
    108: 0x50,  # DOWN
    109: 0x51,  # PAGEDOWN
    110: 0x52,  # INSERT
    111: 0x53,  # DELETE
    113: 0x20,  # MUTE
    114: 0x2E,  # VOLUMEDOWN
    115: 0x30,  # VOLUMEUP
    # Super (LEFTMETA/RIGHTMETA) : seulement sur demande (SUPER, réglage super_to_windows),
    # elle ouvrirait le menu Démarrer de l'invité et Pantheon s'en sert déjà.
    127: 0x5D,  # COMPOSE / menu contextuel
    163: 0x19,  # NEXTSONG
    164: 0x22,  # PLAYPAUSE
    165: 0x10,  # PREVIOUSSONG
}

_PLAIN_EXTRA = {
    86: 0x56,   # 102ND (touche < > des claviers AZERTY)
    87: 0x57,   # F11
    88: 0x58,   # F12
}

SUPER = {125: 0x5B, 126: 0x5C}  # LEFTMETA, RIGHTMETA : touches Windows gauche et droite

KEY_PAUSE = 119
VK_PAUSE = 0x13
KEY_LEFTALT = 56


def xkb_to_message(keycode: int, down: bool, super_key=False):
    """Message `key` pour un keycode XKB, ou None si la touche n'est pas gérée.
    `super_key` : transmettre aussi la touche Super (touche Windows)."""
    ev = keycode - 8
    if super_key and ev in SUPER:
        return {"t": "key", "sc": SUPER[ev], "ext": True, "down": down}
    if ev == KEY_PAUSE:
        return {"t": "key", "vk": VK_PAUSE, "down": down}
    if ev in _EXTENDED:
        return {"t": "key", "sc": _EXTENDED[ev], "ext": True, "down": down}
    if ev in _PLAIN_EXTRA:
        return {"t": "key", "sc": _PLAIN_EXTRA[ev], "ext": False, "down": down}
    if 1 <= ev <= 83:
        return {"t": "key", "sc": ev, "ext": False, "down": down}
    return None


# -- raccourcis réservés au bureau (réglage reserved_shortcuts) --
# Notation de GTK : « <Super>Left », « <Shift><Super>s », « <Control><Alt>t ».

MODIFIERS = {"control": "ctrl", "ctrl": "ctrl", "primary": "ctrl", "shift": "shift", "alt": "alt",
             "mod1": "alt", "super": "super", "meta": "super", "mod4": "super"}
DEFAULT_RESERVED = ["<Super>Tab", "<Super>a", "<Super>Left", "<Super>Right", "<Shift><Super>s",
                    "<Super>Print"]


def parse_shortcut(text):
    """(modificateurs, touche en minuscules) ou None si illisible."""
    import re
    text = (text or "").strip()
    mods = set()
    for m in re.findall(r"<([^>]+)>", text):
        mod = MODIFIERS.get(m.strip().lower())
        if mod is None:
            return None
        mods.add(mod)
    key = re.sub(r"<[^>]+>", "", text).strip()
    if not key or "<" in key or ">" in key:
        return None
    return frozenset(mods), key.lower()


def shortcut_matches(shortcuts, key_name, mods):
    """Vrai si la touche (nom GDK : « Left », « a ») avec ces modificateurs ({ctrl, shift,
    alt, super}) est l'un des raccourcis."""
    if not key_name:
        return False
    wanted = (frozenset(mods), key_name.lower())
    return any(parse_shortcut(s) == wanted for s in shortcuts)


def keyboard_settings(cfg):
    """(super_to_windows, alt_tab_windows, raccourcis réservés) d'après config.json."""
    reserved = cfg.get("reserved_shortcuts")
    if not isinstance(reserved, list):
        reserved = list(DEFAULT_RESERVED)
    return bool(cfg.get("super_to_windows", False)), bool(cfg.get("alt_tab_windows", False)), reserved


def inhibit_mode(super_to_windows, alt_tab_windows):
    """Quand demander au compositeur de laisser passer ses raccourcis : « active » tant que la
    fenêtre a le focus (Super est interceptée dès son appui), « alt » seulement pendant qu'Alt
    est enfoncé (Alt+Tab), None jamais."""
    if super_to_windows:
        return "active"
    if alt_tab_windows:
        return "alt"
    return None
