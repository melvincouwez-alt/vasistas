"""Icônes de la zone de notification : conversion RGBA -> ARGB d'IconPixmap."""

import os

from vasistas import wintray


def test_rgba_vers_argb_sans_remplissage():
    w, h, stride = 3, 2, 16  # 12 octets utiles par ligne, 4 de remplissage
    data = os.urandom(stride * h)
    want = bytearray()
    for y in range(h):
        for x in range(w):
            r, g, b, a = data[y * stride + x * 4:y * stride + x * 4 + 4]
            want += bytes((a, r, g, b))
    assert wintray.pixmap_from_rgba(w, h, stride, data) == (w, h, bytes(want))
