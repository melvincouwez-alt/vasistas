"""Écran et taille des fenêtres : règles de placement.py, sans GTK."""

import pytest

placement = pytest.importorskip("vasistas.placement")


def test_signature_ne_depend_pas_de_l_ordre():
    a = [("eDP-1", 1728, 1080, 1.6666), ("DP-2", 3440, 1440, 1.0)]
    assert placement.signature(a) == placement.signature(list(reversed(a)))
    assert placement.signature(a) != placement.signature(a[:1])


def test_garde_fou_par_axe():
    # plus large que l'écran : seule la largeur est réduite
    assert placement.clamp_size(3000, 600, 1728, 1080, 0.9) == (1555, 600)
    # déjà assez petite : inchangée
    assert placement.clamp_size(800, 600, 1728, 1080, 0.9) == (800, 600)
    # trop grande partout
    assert placement.clamp_size(4000, 3000, 1728, 1080, 0.8) == (1382, 864)


@pytest.mark.parametrize("choice,last,expected", [
    ("auto", "DP-2", None),
    ("last", "DP-2", "DP-2"),
    ("last", "HDMI-1", None),     # écran de la dernière fois absent : Gala choisit
    ("last", None, None),
    ("eDP-1", None, "eDP-1"),
    ("HDMI-1", None, None),       # écran choisi débranché
])
def test_ecran_cible(choice, last, expected):
    assert placement.target_connector(choice, ["eDP-1", "DP-2"], last) == expected


def test_reglages_par_defaut_et_par_appli():
    cfg = {"screens": {"default": "last", "apps": {"powerbi": "DP-2"}}}
    assert placement.choice_for("powerbi", cfg) == "DP-2"
    assert placement.choice_for("pbidesktop", cfg) == "DP-2"  # réglage écrit sous l'alias
    assert placement.choice_for("word", cfg) == "last"
    assert placement.choice_for("word", {}) == "auto"
    s = placement.settings({"screens": {"clamp": False, "inconnu": 1}})
    assert s["clamp"] is False and s["reset_on_change"] is True and "inconnu" not in s


def test_memoire_par_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(placement, "GEOMETRY", tmp_path / "windows.json")
    seul = placement.signature([("eDP-1", 1728, 1080, 1.667)])
    bureau = placement.signature([("eDP-1", 1728, 1080, 1.667), ("DP-2", 3440, 1440, 1.0)])
    placement.remember(seul, "outlook", "eDP-1", 1200, 800, False)
    placement.remember(bureau, "outlook", "DP-2", 2000, 1200, True)
    assert placement.remembered(seul, "outlook") == {"monitor": "eDP-1", "w": 1200, "h": 800, "max": False}
    assert placement.remembered(bureau, "outlook")["monitor"] == "DP-2"
    assert placement.remembered(bureau, "word") is None
    placement.forget_all()
    assert placement.remembered(seul, "outlook") is None
