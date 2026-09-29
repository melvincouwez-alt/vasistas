from vasistas import keymap


def test_lettre():
    assert keymap.xkb_to_message(16 + 8, True) == {"t": "key", "sc": 16, "ext": False, "down": True}


def test_fleche_etendue():
    assert keymap.xkb_to_message(103 + 8, False) == {"t": "key", "sc": 0x48, "ext": True, "down": False}


def test_pause_par_vk():
    assert keymap.xkb_to_message(keymap.KEY_PAUSE + 8, True)["vk"] == keymap.VK_PAUSE


def test_super_non_transmise():
    # ouvrirait le menu Démarrer de l'invité
    assert keymap.xkb_to_message(125 + 8, True) is None
    assert keymap.xkb_to_message(126 + 8, True) is None
