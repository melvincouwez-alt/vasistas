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
    # Super (LEFTMETA/RIGHTMETA) non transmise : elle ouvrirait le menu Démarrer de
    # l'invité, et Pantheon s'en sert déjà pour ses propres raccourcis.
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

KEY_PAUSE = 119
VK_PAUSE = 0x13


def xkb_to_message(keycode: int, down: bool):
    """Message `key` pour un keycode XKB, ou None si la touche n'est pas gérée."""
    ev = keycode - 8
    if ev == KEY_PAUSE:
        return {"t": "key", "vk": VK_PAUSE, "down": down}
    if ev in _EXTENDED:
        return {"t": "key", "sc": _EXTENDED[ev], "ext": True, "down": down}
    if ev in _PLAIN_EXTRA:
        return {"t": "key", "sc": _PLAIN_EXTRA[ev], "ext": False, "down": down}
    if 1 <= ev <= 83:
        return {"t": "key", "sc": ev, "ext": False, "down": down}
    return None
