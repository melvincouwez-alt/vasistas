"""Netteté : une image de Windows à l'échelle de l'écran est rendue pixel pour pixel.

Rendu hors écran de GuestView.do_snapshot sous une transformation d'échelle, comme GTK le
fait pour une surface à 200 % ou 167 %. Garde-fou contre append_scaled_texture, que GTK rend
d'abord à la taille logique puis agrandit (blocs 2x2 à 200 %, moiré à 167 %)."""

from types import SimpleNamespace

import pytest

gi = pytest.importorskip("gi")
gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

if not Gtk.init_check():
    pytest.skip("pas d'affichage", allow_module_level=True)

window = pytest.importorskip("vasistas.window")

W, H = 64, 8


def stripes():
    """Colonnes d'un pixel, noires et blanches : le motif le plus sensible au rééchantillonnage."""
    px = bytearray()
    for _ in range(H):
        for x in range(W):
            v = 0 if x % 2 == 0 else 255
            px += bytes((v, v, v, 255))
    return Gdk.MemoryTexture.new(W, H, Gdk.MemoryFormat.B8G8R8A8, GLib.Bytes.new(bytes(px)), W * 4)


def render(scale, monkeypatch):
    monkeypatch.setattr(window, "scale_of", lambda widget: scale)
    view = window.GuestView(SimpleNamespace(guest_scale=lambda: scale, wid="test"))
    monkeypatch.setattr(view, "get_width", lambda: W / scale)
    monkeypatch.setattr(view, "get_height", lambda: H / scale)
    monkeypatch.setattr(view, "_device_offset", lambda: (0.0, 0.0))
    view.set_texture(stripes())
    snapshot = Gtk.Snapshot()
    view.do_snapshot(snapshot)
    node = Gsk.TransformNode.new(snapshot.to_node(), Gsk.Transform.new().scale(scale, scale))
    renderer = Gsk.CairoRenderer()
    renderer.realize_for_display(Gdk.Display.get_default())
    try:
        out = renderer.render_texture(node, Graphene.Rect().init(0, 0, W, H))
    finally:
        renderer.unrealize()
    data, stride = Gdk.TextureDownloader.new(out).download_bytes()
    raw = data.get_data()
    return [raw[x * 4] for x in range(W)]


@pytest.mark.parametrize("scale", [1.0, 2.0, 5 / 3])
def test_pixel_pour_pixel(scale, monkeypatch):
    assert render(scale, monkeypatch) == [0 if x % 2 == 0 else 255 for x in range(W)]
