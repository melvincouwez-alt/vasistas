"""Trames du canal : encodage, décodage, recalage après une coupure."""

import socket

from vasistas import protocol


def _reader(data: bytes):
    a, b = socket.socketpair()
    a.sendall(data)
    a.close()
    return protocol.FrameReader(b)


def test_json_aller_retour():
    msg = {"t": "window.update", "id": 7, "rect": [1, 2, 3, 4], "title": "Été"}
    t, m = _reader(protocol.pack_json(msg)).read()
    assert t == protocol.T_JSON and m == msg


def test_tuile_compressee():
    px = bytes(range(256)) * 4  # 16 x 16 x 4 octets
    frame = protocol.pack_tile(9, 3, 4, 16, 16, px)
    t, payload = _reader(frame).read()
    assert t == protocol.T_TILE
    assert protocol.unpack_tile(payload) == (9, 3, 4, 16, 16, px)


def test_recalage_apres_dechets():
    msg = {"t": "hello"}
    data = b"\x00VSxx\xff\xff" + b"garbage" + protocol.pack_json(msg)
    t, m = _reader(data).read()
    assert m == msg


def test_json_invalide_ignore():
    bad = protocol.MAGIC + (5).to_bytes(4, "little") + bytes([protocol.T_JSON]) + b"{no}"
    t, m = _reader(bad + protocol.pack_json({"t": "ok"})).read()
    assert m == {"t": "ok"}
