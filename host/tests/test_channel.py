"""File d'envoi de l'hôte : ordre, fusion des mouvements, rien de perdu d'important."""

import socket
import threading

from vasistas import channel, protocol


def _channel():
    ch = channel.Channel("/nonexistent", lambda m: None, lambda *a: None, lambda c: None)
    ch.sock = object()  # « connecté », sans fil d'envoi : la file reste consultable
    return ch


def test_mouvements_fusionnes():
    ch = _channel()
    for x in range(5):
        ch.send({"t": "mouse.move", "id": 1, "x": x, "y": 0})
    assert [m["x"] for m in ch.outbox] == [4]
    assert ch.stats()["coalesced"] == 4


def test_ordre_garde_autour_d_un_clic():
    ch = _channel()
    ch.send({"t": "mouse.move", "id": 1, "x": 1, "y": 0})
    ch.send({"t": "mouse.button", "id": 1, "x": 1, "y": 0, "button": 1, "down": True})
    ch.send({"t": "mouse.move", "id": 1, "x": 2, "y": 0})
    assert [m["t"] for m in ch.outbox] == ["mouse.move", "mouse.button", "mouse.move"]


def test_file_pleine_garde_touches():
    ch = _channel()
    for i in range(channel.SEND_QUEUE_MAX):
        ch.send({"t": "mouse.wheel", "id": 1, "i": i})
    ch.send({"t": "key", "sc": 0x1D, "ext": False, "down": False})
    kinds = [m["t"] for m in ch.outbox]
    assert kinds == ["key"]
    assert ch.stats()["dropped"] == channel.SEND_QUEUE_MAX


def test_sans_connexion():
    ch = channel.Channel("/nonexistent", lambda m: None, lambda *a: None, lambda c: None)
    assert ch.send({"t": "key"}) is False


def test_fil_d_envoi_ecrit_dans_l_ordre():
    a, b = socket.socketpair()
    ch = channel.Channel("/nonexistent", lambda m: None, lambda *a: None, lambda c: None)
    ch.sock = a
    t = threading.Thread(target=ch._write_loop, args=(a,), daemon=True)
    t.start()
    for i in range(50):
        ch.send({"t": "key", "i": i})
    reader = protocol.FrameReader(b)
    got = [reader.read()[1]["i"] for _ in range(50)]
    assert got == list(range(50))
    with ch.out_cond:
        ch.sock = None
        ch.out_cond.notify_all()
    t.join(2)
    assert not t.is_alive()
