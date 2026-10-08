"""Touche Super, Alt+Tab et raccourcis réservés au bureau."""

from vasistas import keymap


def test_super_sur_demande():
    assert keymap.xkb_to_message(125 + 8, True) is None
    assert keymap.xkb_to_message(125 + 8, True, super_key=True) == {"t": "key", "sc": 0x5B, "ext": True, "down": True}
    assert keymap.xkb_to_message(126 + 8, False, super_key=True)["sc"] == 0x5C
    # les autres touches ne changent pas
    assert keymap.xkb_to_message(16 + 8, True, super_key=True) == keymap.xkb_to_message(16 + 8, True)


def test_lecture_des_raccourcis():
    assert keymap.parse_shortcut("<Super>Left") == (frozenset({"super"}), "left")
    assert keymap.parse_shortcut("<Shift><Super>s") == (frozenset({"shift", "super"}), "s")
    assert keymap.parse_shortcut("<Control><Alt>T") == (frozenset({"ctrl", "alt"}), "t")
    assert keymap.parse_shortcut("<Primary>q") == (frozenset({"ctrl"}), "q")
    assert keymap.parse_shortcut("Print") == (frozenset(), "print")
    for bad in ("", "<Super>", "<Hyperx>a", "<Super>a>"):
        assert keymap.parse_shortcut(bad) is None


def test_correspondance():
    reserved = ["<Super>Left", "<Shift><Super>s"]
    assert keymap.shortcut_matches(reserved, "Left", {"super"})
    assert keymap.shortcut_matches(reserved, "s", {"super", "shift"})
    assert not keymap.shortcut_matches(reserved, "Left", {"super", "shift"})
    assert not keymap.shortcut_matches(reserved, "s", {"super"})
    assert not keymap.shortcut_matches(reserved, "", set())


def test_reglages():
    assert keymap.keyboard_settings({}) == (False, False, keymap.DEFAULT_RESERVED)
    cfg = {"super_to_windows": True, "alt_tab_windows": True, "reserved_shortcuts": []}
    assert keymap.keyboard_settings(cfg) == (True, True, [])
    assert all(keymap.parse_shortcut(s) for s in keymap.DEFAULT_RESERVED)


class _Fake:
    """KeyboardMixin sans fenêtre GTK : messages envoyés et demandes d'inhibition notés."""

    def __init__(self, kb):
        import vasistas.app  # noqa: F401 - fixe les versions de Gtk, Gsk, Graphene
        from vasistas.window import KeyboardMixin
        self.mixin = KeyboardMixin
        self._modifiers = KeyboardMixin._modifiers
        self.sent, self.inhibit_calls = [], []
        self.held_keys, self.swallowed = set(), set()
        self.kb, self.inhibited, self.inhibit_lifted = kb, False, False

    def send(self, msg):
        self.sent.append(msg)

    def _inhibit(self, on):
        self.inhibit_calls.append(on)
        self.inhibited = on

    def key(self, keyval_name, keycode, mods=(), down=True):
        from gi.repository import Gdk
        state = Gdk.ModifierType(0)
        for m in mods:
            state |= {"ctrl": Gdk.ModifierType.CONTROL_MASK, "alt": Gdk.ModifierType.ALT_MASK,
                      "shift": Gdk.ModifierType.SHIFT_MASK, "super": Gdk.ModifierType.SUPER_MASK}[m]
        keyval = Gdk.keyval_from_name(keyval_name)
        return self.mixin._on_key(self, None, keyval, keycode + 8, state, down)


def test_raccourci_reserve_jamais_envoye():
    f = _Fake((True, False, ["<Super>Left"]))
    f.inhibited = True
    assert f.key("Left", 105, {"super"}) is True
    assert f.sent == [] and f.inhibit_calls == [False] and f.inhibit_lifted
    assert f.key("Left", 105, {"super"}, down=False) is True  # relâchement gardé aussi
    assert f.sent == []


def test_super_vers_windows():
    f = _Fake((True, False, []))
    f.key("Super_L", 125)
    assert f.sent == [{"t": "key", "sc": 0x5B, "ext": True, "down": True}]
    g = _Fake((False, False, []))
    assert g.key("Super_L", 125) is False and g.sent == []


def test_alt_tab():
    # Alt+Tab réservé au bureau quand l'option est coupée
    f = _Fake((True, False, []))
    f.key("Tab", 15, {"alt"})
    assert f.sent == []
    # option active, Alt tenu depuis une autre fenêtre : Alt renvoyé enfoncé avant Tab
    g = _Fake((False, True, []))
    g.key("Tab", 15, {"alt"})
    assert [m["sc"] for m in g.sent] == [0x38, 0x0F] and g.sent[0]["down"]
    # mode « alt » : inhibition pendant qu'Alt est enfoncé
    h = _Fake((False, True, []))
    h.key("Alt_L", 56)
    h.key("Alt_L", 56, {"alt"}, down=False)
    assert h.inhibit_calls == [True, False]


def test_mode_d_inhibition():
    assert keymap.inhibit_mode(False, False) is None
    assert keymap.inhibit_mode(False, True) == "alt"
    assert keymap.inhibit_mode(True, False) == "active"
    assert keymap.inhibit_mode(True, True) == "active"
