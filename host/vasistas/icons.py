"""Icônes des applications Windows au format du thème elementary.

L'icône extraite de l'exécutable est calée dans le gabarit du thème (carré de 104 px sur
128, légèrement plus bas que le centre, comme les icônes système), avec une ombre douce,
et reçoit l'emblème Vasistas en bas à droite à partir de 32 px.
"""

import math
from pathlib import Path

import cairo
import gi

gi.require_version("Gdk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib  # noqa: E402

SIZES = (16, 24, 32, 48, 64, 128, 256)
BADGE_MIN_SIZE = 32
EMBLEM = Path(__file__).resolve().parents[2] / "data/icons/hicolor/128x128/apps/io.github.melvincouwez.Vasistas.svg"

# gabarit à 128 px : zone de l'icône et de l'emblème
BOX = (12, 13, 104, 104)       # x, y, côté ; bas vers 117 comme les carrés du thème
BADGE = (68, 66, 60)           # x, y, côté : déborde un peu du coin bas droit
OUTLINE = 2.5                  # liseré blanc autour de l'emblème, en px à 128


def _pixbuf_from_png(png: bytes) -> GdkPixbuf.Pixbuf:
    loader = GdkPixbuf.PixbufLoader.new_with_type("png")
    loader.write(png)
    loader.close()
    return loader.get_pixbuf()


def _surface_from_pixbuf(pb: GdkPixbuf.Pixbuf) -> cairo.ImageSurface:
    """Pixbuf RGBA vers surface cairo ARGB32 prémultipliée."""
    if not pb.get_has_alpha():
        pb = pb.add_alpha(False, 0, 0, 0)
    w, h, stride = pb.get_width(), pb.get_height(), pb.get_rowstride()
    src = pb.get_pixels()
    surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    dst = surf.get_data()
    dstride = surf.get_stride()
    for y in range(h):
        row = src[y * stride:y * stride + w * 4]
        out = bytearray(w * 4)
        for x in range(w):
            r, g, b, a = row[x * 4:x * 4 + 4]
            out[x * 4] = b * a // 255
            out[x * 4 + 1] = g * a // 255
            out[x * 4 + 2] = r * a // 255
            out[x * 4 + 3] = a
        dst[y * dstride:y * dstride + w * 4] = out
    surf.mark_dirty()
    return surf


def _trim(surf: cairo.ImageSurface):
    """Rectangle occupé (alpha > 0) : les icônes Windows ont souvent des marges."""
    w, h, stride = surf.get_width(), surf.get_height(), surf.get_stride()
    data = surf.get_data()
    x0, y0, x1, y1 = w, h, -1, -1
    for y in range(h):
        row = data[y * stride:y * stride + w * 4]
        for x in range(w):
            if row[x * 4 + 3] > 8:
                x0, x1 = min(x0, x), max(x1, x)
                y0, y1 = min(y0, y), max(y1, y)
    if x1 < 0:
        return 0, 0, w, h
    return x0, y0, x1 - x0 + 1, y1 - y0 + 1


def _paint_scaled(cr, surf, src_rect, dst_x, dst_y, dst_side):
    sx, sy, sw, sh = src_rect
    k = dst_side / max(sw, sh)
    ox = dst_x + (dst_side - sw * k) / 2
    oy = dst_y + (dst_side - sh * k) / 2
    cr.save()
    cr.translate(ox, oy)
    cr.scale(k, k)
    cr.set_source_surface(surf, -sx, -sy)
    cr.get_source().set_filter(cairo.FILTER_BEST)
    cr.paint()
    cr.restore()


def compose(png: bytes, size: int, with_badge=True) -> bytes:
    """Icône elementary de `size` px, en PNG."""
    src = _surface_from_pixbuf(_pixbuf_from_png(png))
    rect = _trim(src)
    k = size / 128
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(out)
    bx, by, side = BOX[0] * k, BOX[1] * k, BOX[2] * k

    # ombre douce : silhouette noire décalée vers le bas, en trois passes d'étalement
    shadow = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    scr = cairo.Context(shadow)
    _paint_scaled(scr, src, rect, bx, by, side)
    for dy, alpha in ((1.0, 0.18), (2.0, 0.12), (3.0, 0.06)):
        cr.save()
        cr.set_source_rgba(0.094, 0.094, 0.094, alpha)
        cr.mask_surface(shadow, 0, dy * max(1.0, k))
        cr.restore()

    _paint_scaled(cr, src, rect, bx, by, side)

    if with_badge:
        _paint_badge(cr, size)
    return _png(out)


def compose_native(svg: Path, size: int) -> bytes:
    """Icône déjà au style elementary (SVG d'un thème) : rendue telle quelle à `size` px,
    avec l'emblème Vasistas."""
    out = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(out)
    pb = GdkPixbuf.Pixbuf.new_from_file_at_size(str(svg), size, size)
    cr.set_source_surface(_surface_from_pixbuf(pb), (size - pb.get_width()) / 2, (size - pb.get_height()) / 2)
    cr.paint()
    _paint_badge(cr, size)
    return _png(out)


def _paint_badge(cr, size):
    k = size / 128
    if size >= BADGE_MIN_SIZE and EMBLEM.exists():
        bx2, by2, bside = BADGE[0] * k, BADGE[1] * k, BADGE[2] * k
        px = max(1, round(bside))
        emblem = GdkPixbuf.Pixbuf.new_from_file_at_size(str(EMBLEM), px * 2, px * 2)
        esurf = _surface_from_pixbuf(emblem)
        # liseré blanc qui suit la forme de l'emblème : sa silhouette étalée dans
        # toutes les directions, peinte en blanc sous l'emblème
        mask = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
        _paint_scaled(cairo.Context(mask), esurf, _trim(esurf), bx2, by2, bside)
        r = max(1.0, OUTLINE * k)
        cr.save()
        cr.set_source_rgba(1, 1, 1, 1)
        steps = 16
        for i in range(steps):
            a = 2 * math.pi * i / steps
            cr.mask_surface(mask, r * math.cos(a), r * math.sin(a))
        cr.restore()
        cr.set_source_surface(mask, 0, 0)
        cr.paint()


def _png(out):
    out.flush()
    buf = []
    out.write_to_png(_Writer(buf))
    return b"".join(buf)


class _Writer:
    def __init__(self, buf):
        self.buf = buf

    def write(self, data):
        self.buf.append(bytes(data))
        return len(data)


def install(app_icon_name: str, png: bytes, icons_dir: Path):
    """Écrit l'icône composée à chaque taille dans icons_dir/<n>x<n>/apps/<nom>.png."""
    for size in SIZES:
        path = icons_dir / f"{size}x{size}/apps/{app_icon_name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(compose(png, size))


# thèmes où chercher une icône dessinée au style elementary, par ordre de préférence
THEME_DIRS = (Path.home() / ".local/share/icons/hicolor", Path("/usr/share/icons/elementary"),
              Path("/usr/share/icons/hicolor"))


def find_native(names) -> dict:
    """{taille: fichier SVG} de la première icône du thème trouvée parmi `names`.
    Chaque taille prend le dessin le plus proche (au-dessus de préférence)."""
    for name in names:
        found = {}
        for base in THEME_DIRS:
            for f in base.glob(f"*/apps/{name}.svg"):
                side = f.parent.parent.name.split("x")[0].split("@")[0]
                if side.isdigit() and "@" not in f.parent.parent.name:
                    found.setdefault(int(side), f)
        if found:
            avail = sorted(found)
            return {size: found[next((a for a in avail if a >= size), avail[-1])] for size in SIZES}
    return {}


def install_native(app_icon_name: str, svgs: dict, icons_dir: Path):
    for size, svg in svgs.items():
        path = icons_dir / f"{size}x{size}/apps/{app_icon_name}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(compose_native(svg, size))


if __name__ == "__main__":
    import sys
    raw = Path(sys.argv[1]).read_bytes()
    for s in (128, 48, 32):
        Path(f"{sys.argv[2]}-{s}.png").write_bytes(compose(raw, s))
