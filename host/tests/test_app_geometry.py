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


def test_battement_ralenti_sans_fenetre_active(monkeypatch):
    sources = []
    monkeypatch.setattr(app.GLib, "timeout_add", lambda ms, fn: sources.append(ms) or 1)
    win = SimpleNamespace(active=False, is_active=lambda: win.active)
    f = SimpleNamespace(stall_last=time.monotonic(), stall_off=app._suspend_offset(), stall_ms=app.STALL_TICK_MS, last_active=win,
                        stalls={"count": 0, "max_ms": 0.0, "total_ms": 0.0}, _stall_tick=None)
    assert VasistasApp._stall_tick(f) is False and sources == [app.STALL_IDLE_TICK_MS]
    assert VasistasApp._stall_tick(f) is True  # déjà au ralenti
    win.active = True
    assert VasistasApp._stall_tick(f) is False and sources[-1] == app.STALL_TICK_MS
    assert f.stalls["count"] == 0


def test_sortie_de_veille_pas_comptee_comme_gel(monkeypatch):
    monkeypatch.setattr(app.GLib, "timeout_add", lambda ms, fn: 1)
    f = SimpleNamespace(stall_last=time.monotonic() - 5, stall_off=app._suspend_offset() - 3600,
                        stall_ms=app.STALL_IDLE_TICK_MS, last_active=None,
                        stalls={"count": 0, "max_ms": 0.0, "total_ms": 0.0}, _stall_tick=None)
    VasistasApp._stall_tick(f)
    assert f.stalls["count"] == 0
    f.stall_last -= 5  # même retard, sans veille : compté
    VasistasApp._stall_tick(f)
    assert f.stalls["count"] == 1


def test_deconnexion_repond_aux_scripts_partis(monkeypatch):
    monkeypatch.setattr(app.vm, "release_gpu", lambda: None)
    got = {}
    f = SimpleNamespace(guest_ready=True, screen=None, screen_texture=object(), views={}, infos={}, textures={},
                        wintray=SimpleNamespace(clear=lambda: None), _close=None,
                        launch_queue=[{"t": "exec", "req": 2}],
                        pending_exec={1: lambda r: got.setdefault(1, r), 2: lambda r: got.setdefault(2, r)})
    f._fail_sent_requests = lambda error: VasistasApp._fail_sent_requests(f, error)
    VasistasApp.on_state(f, False)
    assert got[1]["error"] and 2 not in got  # le 2 attend encore son envoi
    assert list(f.pending_exec) == [2] and f.screen_texture is None
