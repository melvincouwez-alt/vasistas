"""Clics dans la barre de titre : transmis à Windows, déplacement Linux seulement en glissant."""

from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

if not Gtk.init_check():
    pytest.skip("pas d'affichage", allow_module_level=True)

window = pytest.importorskip("vasistas.window")


class Toplevel:
    def __init__(self):
        self.moves = []

    def begin_move(self, device, button, x, y, time):
        self.moves.append((button, x, y))


def _view():
    sent, top = [], Toplevel()
    owner = SimpleNamespace(wid=7, send=sent.append, toplevel_surface=lambda: top, maximized=[],
                            toggle_maximize=None)
    owner.toggle_maximize = lambda: owner.maximized.append(True)
    view = window.GuestView(owner)
    view.hit = window.HTCAPTION
    view._host_edge = lambda event: None
    return view, sent, top, owner


def _event(kind, x, y, button=1):
    ev = SimpleNamespace(get_event_type=lambda: kind, get_button=lambda: button,
                         get_device=lambda: None, get_time=lambda: 0)
    return ev, (x * 2, y * 2, x, y)


def _feed(view, kind, x, y):
    ev, pos = _event(kind, x, y)
    view._to_guest = lambda event: pos
    return view._on_event(None, ev)


def test_clic_dans_la_barre_de_titre_va_a_windows():
    view, sent, top, _ = _view()
    _feed(view, Gdk.EventType.BUTTON_PRESS, 100, 10)
    _feed(view, Gdk.EventType.MOTION_NOTIFY, 101, 11)   # tremblement : toujours un clic
    _feed(view, Gdk.EventType.BUTTON_RELEASE, 101, 11)
    buttons = [m for m in sent if m["t"] == "mouse.button"]
    assert [b["down"] for b in buttons] == [True, False]
    assert not top.moves


def test_glisser_deplace_la_fenetre_linux():
    view, sent, top, _ = _view()
    _feed(view, Gdk.EventType.BUTTON_PRESS, 100, 10)
    _feed(view, Gdk.EventType.MOTION_NOTIFY, 130, 12)
    assert top.moves == [(1, 100, 10)]
    # Windows a reçu un clic sur place, sans mouvement entre les deux
    buttons = [m for m in sent if m["t"] == "mouse.button"]
    assert [(b["down"], b["x"]) for b in buttons] == [(True, 200), (False, 200)]
    _feed(view, Gdk.EventType.BUTTON_RELEASE, 130, 12)
    assert len([m for m in sent if m["t"] == "mouse.button"]) == 2


def test_double_clic_agrandit_sans_second_clic_windows():
    view, sent, top, owner = _view()
    for _ in range(2):
        _feed(view, Gdk.EventType.BUTTON_PRESS, 100, 10)
        _feed(view, Gdk.EventType.BUTTON_RELEASE, 100, 10)
    assert owner.maximized == [True]
    assert len([m for m in sent if m["t"] == "mouse.button"]) == 2
