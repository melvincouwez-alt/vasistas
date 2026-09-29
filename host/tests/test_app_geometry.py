"""Règles de cadrage de l'hôte : quelle portion de l'écran de QEMU montrer, et quand."""

import time
from types import SimpleNamespace

import pytest

app = pytest.importorskip("vasistas.app")
VasistasApp = app.VasistasApp


def _fake(infos, screen=(1920, 1080)):
    return SimpleNamespace(infos=infos, screen=SimpleNamespace(width=screen[0], height=screen[1]))


def test_fenetre_visible_lue_dans_l_ecran():
    f = _fake({1: {"rect": [0, 0, 800, 600]}})
    assert VasistasApp._from_screen(f, 1)


@pytest.mark.parametrize("rect", [[-5, 0, 800, 600], [1500, 0, 800, 600], [0, 600, 800, 600]])
def test_jamais_hors_ecran(rect):
    f = _fake({1: {"rect": rect}})
    assert not VasistasApp._from_screen(f, 1)


def test_recouverte_ou_reduite():
    f = _fake({1: {"rect": [0, 0, 10, 10], "occluded": True}, 2: {"rect": [0, 0, 10, 10], "minimized": True}})
    assert not VasistasApp._from_screen(f, 1)
    assert not VasistasApp._from_screen(f, 2)


def test_ancien_cadrage_garde_jusqu_au_redessin():
    info = {"rect": [100, 0, 50, 50], "shown_rect": [0, 0, 50, 50], "rect_since": time.monotonic()}
    f = _fake({1: info})
    # zone modifiée ailleurs : on garde l'ancien cadrage
    assert VasistasApp._shown_rect(f, 1, [(500, 500, 600, 600)]) == [0, 0, 50, 50]
    # zone modifiée au nouvel endroit : nouveau cadrage, définitivement
    assert VasistasApp._shown_rect(f, 1, [(110, 10, 120, 20)]) == [100, 0, 50, 50]
    assert "shown_rect" not in info


def test_ancien_cadrage_abandonne_apres_le_delai():
    info = {"rect": [100, 0, 50, 50], "shown_rect": [0, 0, 50, 50],
            "rect_since": time.monotonic() - app.RECT_HOLD_S - 0.01}
    assert VasistasApp._shown_rect(_fake({1: info}), 1) == [100, 0, 50, 50]
