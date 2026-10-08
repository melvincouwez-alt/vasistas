"""Faux invité : placement des fenêtres, notification factice, icône de zone de notification."""

import base64
import socket
import struct
import zlib

from vasistas import fakeguest, protocol, vm


def test_fit_rect_reduit_et_centre():
    assert fakeguest.fit_rect([-500, 3000, 5000, 900], (1000, 800), 0.8) == [100, 80, 800, 640]
    assert fakeguest.fit_rect([-500, 3000, 5000, 300], (1000, 800), 0.8) == [100, 250, 800, 300]


def test_fit_rect_garde_la_taille_minimale():
    x, y, w, h = fakeguest.fit_rect([0, 0, 50, 40], (1000, 800), 0.1)
    assert (w, h) == (fakeguest.MIN_W, fakeguest.MIN_H)
    assert (x, y) == ((1000 - w) // 2, (800 - h) // 2)


def test_place_rect_ramene_dans_l_ecran():
    assert fakeguest.place_rect([900, -20, 300, 200], (1000, 800)) == [700, 0, 300, 200]
    assert fakeguest.place_rect([10, 10, 4000, 4000], (1000, 800)) == [0, 0, 1000, 800]


def test_icone_png_valide():
    data = base64.b64decode(fakeguest.tray_icon(16))
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    w, h, depth, color = struct.unpack(">IIBB", data[16:26])
    assert (w, h, depth, color) == (16, 16, 8, 6)
    (n,) = struct.unpack(">I", data[33:37])
    assert data[37:41] == b"IDAT"
    assert len(zlib.decompress(data[41:41 + n])) == 16 * (1 + 16 * 4)


class _Peer:
    """Côté hôte d'un faux invité branché sur une paire de sockets."""

    def __init__(self):
        a, b = socket.socketpair()
        self.guest = fakeguest.FakeGuest(a)
        self.reader = protocol.FrameReader(b)
        b.settimeout(5)

    def until(self, t):
        while True:
            ftype, m = self.reader.read()
            if ftype == protocol.T_JSON and m["t"] == t:
                return m

    def close(self):
        self.guest.alive = False


def test_hello_avec_options_puis_reset():
    peer = _Peer()
    try:
        fakeguest.NOTIFY_DELAY, delay = 0.05, fakeguest.NOTIFY_DELAY
        try:
            peer.guest.handle({"t": "hello", "version": 1, "scale": 1.0, "tray": True, "notifications": True})
            win = peer.until("window.new")
            tray = peer.until("tray")
            assert [i["key"] for i in tray["items"]] == [fakeguest.TRAY_KEY]
            note = peer.until("notify")
            assert {"id", "app", "appName", "title", "body"} <= set(note)
        finally:
            fakeguest.NOTIFY_DELAY = delay

        peer.guest.handle({"t": "window.place", "id": win["id"], "x": 10, "y": 20, "w": 800, "h": 600})
        assert peer.until("window.update")["rect"] == [10, 20, 800, 600]

        peer.guest.handle({"t": "windows.reset", "max": 0.5})
        rect = peer.until("window.update")["rect"]
        assert rect == fakeguest.fit_rect([10, 20, 800, 600], vm.SCREEN, 0.5)
        assert peer.until("windows.reset.done")["count"] == 1
    finally:
        peer.close()


def test_garde_fou_a_la_creation():
    peer = _Peer()
    try:
        peer.guest.add(fakeguest.Win(500, "Trop grande", (-100, 0, vm.SCREEN[0] * 2, 400)))
        x, y, w, h = peer.until("window.new")["rect"]
        assert w == int(vm.SCREEN[0] * fakeguest.CLAMP_MAX) and x >= 0
        peer.guest.set_options({"clamp": False})
        peer.guest.add(fakeguest.Win(501, "Libre", (-100, 0, 300, 200)))
        assert peer.until("window.new")["rect"] == [-100, 0, 300, 200]
    finally:
        peer.close()
