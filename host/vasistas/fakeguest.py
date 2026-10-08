"""Faux invité : remplace la VM pour tester l'hôte.

Serveur unix sur le chemin du port virtio-serial. Dessine des fenêtres en Python pur
(barre de titre, boutons, carré qui suit la souris, compteur de touches) et parle le
même protocole que l'agent Windows : placement des fenêtres, notification factice
quelques secondes après la connexion (option `notifications`), icône de zone de
notification (option `tray`).
"""

import base64
import logging
import os
import socket
import struct
import threading
import time
import zlib

from . import protocol, version, vm
from .i18n import _

log = logging.getLogger(__name__)

CAPTION_H = 60
BORDER = 8
BTN_W = 90
HTCLIENT, HTCAPTION, HTCLOSE, HTMAXBUTTON, HTMINBUTTON = 1, 2, 20, 9, 8
HT_EDGE = {(-1, 0): 10, (1, 0): 11, (0, -1): 12, (-1, -1): 13, (1, -1): 14,
           (0, 1): 15, (-1, 1): 16, (1, 1): 17}
CURSOR_EDGE = {10: "ew-resize", 11: "ew-resize", 12: "ns-resize", 15: "ns-resize",
               13: "nwse-resize", 17: "nwse-resize", 14: "nesw-resize", 16: "nesw-resize"}
PALETTE = [(0x2B, 0x57, 0x9A), (0x21, 0x73, 0x46), (0xB7, 0x47, 0x2A), (0x7B, 0x1F, 0xA2)]
MIN_W, MIN_H = 200, 150   # taille minimale des fausses fenêtres (WM_GETMINMAXINFO de l'agent)
CLAMP_MAX = 0.9           # garde-fou à la création, comme l'agent
NOTIFY_DELAY = 3.0        # secondes entre la connexion et la notification factice
TRAY_KEY = "fake:onedrive"


def fit_rect(rect, screen, ratio):
    """Règle de `windows.reset` et du garde-fou à la création : taille ramenée à au plus
    `ratio` fois l'écran sur chaque axe (sans passer sous la taille minimale), puis centrée."""
    _x, _y, w, h = rect
    sw, sh = screen
    w = max(MIN_W, min(w, int(sw * ratio)))
    h = max(MIN_H, min(h, int(sh * ratio)))
    return [max(0, (sw - w) // 2), max(0, (sh - h) // 2), w, h]


def place_rect(rect, screen):
    """Rectangle de `window.place` ramené dans l'écran, comme le fait l'agent."""
    x, y, w, h = rect
    sw, sh = screen
    w = max(MIN_W, min(w, sw))
    h = max(MIN_H, min(h, sh))
    return [max(0, min(x, sw - w)), max(0, min(y, sh - h)), w, h]


def off_screen(rect, screen):
    x, y, w, h = rect
    return x < 0 or y < 0 or x + w > screen[0] or y + h > screen[1]


def png_rgba(w, h, pixels: bytes) -> bytes:
    """PNG RGBA 8 bits minimal (sans dépendance) : icône de la zone de notification."""
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
    raw = b"".join(b"\x00" + pixels[y * w * 4:(y + 1) * w * 4] for y in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def tray_icon(size=32) -> str:
    """Nuage bleu sur fond transparent, en base64 : fausse icône OneDrive."""
    px = bytearray(size * size * 4)
    blobs = ((0.38, 0.58, 0.22), (0.6, 0.5, 0.28), (0.78, 0.62, 0.18))
    for y in range(size):
        for x in range(size):
            u, v = (x + 0.5) / size, (y + 0.5) / size
            if any((u - cx) ** 2 + (v - cy) ** 2 <= r * r for cx, cy, r in blobs) and v < 0.8:
                px[(y * size + x) * 4:(y * size + x + 1) * 4] = bytes((0x00, 0x78, 0xD4, 0xFF))
    return base64.b64encode(png_rgba(size, size, bytes(px))).decode()


class Win:
    def __init__(self, wid, title, rect, kind="normal", owner=0, color=(0x2B, 0x57, 0x9A)):
        self.wid, self.title, self.kind, self.owner = wid, title, kind, owner
        self.x, self.y, self.w, self.h = rect
        self.color = color
        self.mouse = (0, 0)
        self.keys = 0
        self.hover = (None, None)

    def info(self):
        return {"id": self.wid, "title": self.title, "rect": [self.x, self.y, self.w, self.h],
                "kind": self.kind, "owner": self.owner, "maximized": False}

    def hit(self, x, y):
        if self.kind == "popup":
            return HTCLIENT
        ex = -1 if x < BORDER else 1 if x >= self.w - BORDER else 0
        ey = -1 if y < BORDER else 1 if y >= self.h - BORDER else 0
        if (ex, ey) != (0, 0):
            return HT_EDGE[(ex, ey)]
        if y < CAPTION_H:
            if x >= self.w - BTN_W:
                return HTCLOSE
            if x >= self.w - 2 * BTN_W:
                return HTMAXBUTTON
            if x >= self.w - 3 * BTN_W:
                return HTMINBUTTON
            return HTCAPTION
        return HTCLIENT

    def render(self) -> bytes:
        w, h = self.w, self.h
        b, g, r = self.color[2], self.color[1], self.color[0]
        fb = bytearray(bytes((0xF3, 0xF3, 0xF3, 255)) * (w * h))

        def rect(x0, y0, rw, rh, bgr):
            x0, y0 = max(0, x0), max(0, y0)
            x1, y1 = min(w, x0 + rw), min(h, y0 + rh)
            if x1 <= x0 or y1 <= y0:
                return
            row = bytes((bgr[0], bgr[1], bgr[2], 255)) * (x1 - x0)
            for yy in range(y0, y1):
                o = (yy * w + x0) * 4
                fb[o:o + len(row)] = row

        if self.kind == "popup":
            rect(0, 0, w, h, (0xFF, 0xFF, 0xFF))
            for i in range(h // 50):
                rect(10, i * 50 + 10, w - 20, 30, (0xE0, 0xE0, 0xE0) if i % 2 else (0xD0, 0xD0, 0xD0))
            mx, my = self.mouse
            if 0 <= my < h:
                i = my // 50
                rect(10, i * 50 + 10, w - 20, 30, (b, g, r))
        else:
            rect(0, 0, w, CAPTION_H, (b, g, r))
            rect(w - BTN_W, 0, BTN_W, CAPTION_H, (0x23, 0x11, 0xC4))
            rect(w - 2 * BTN_W + 30, 25, 30, 12, (0xFF, 0xFF, 0xFF))
            rect(w - 3 * BTN_W + 30, 35, 30, 4, (0xFF, 0xFF, 0xFF))
            for i in range(min(self.keys, 60)):
                rect(40 + i * 24, CAPTION_H + 30, 16, 40, (b, g, r))
            mx, my = self.mouse
            rect(mx - 30, my - 30, 60, 60, (0x00, 0x8C, 0xFF))
            rect(0, 0, w, 2, (0x40, 0x40, 0x40))
            rect(0, h - 2, w, 2, (0x40, 0x40, 0x40))
            rect(0, 0, 2, h, (0x40, 0x40, 0x40))
            rect(w - 2, 0, 2, h, (0x40, 0x40, 0x40))
        return bytes(fb)


class FakeGuest:
    def __init__(self, conn):
        self.conn = conn
        self.lock = threading.Lock()
        self.wins = {}
        self.next_id = 100
        self.launch_count = 0
        self.dirty = set()
        self.prev = {}  # id -> (w, h, image envoyée)
        self.options = {"clamp": True, "notifications": False, "tray": False}
        self.notify_count = 0
        self.alive = True
        threading.Thread(target=self._render_loop, daemon=True).start()

    def send(self, data: bytes):
        with self.lock:
            self.conn.sendall(data)

    def msg(self, m):
        self.send(protocol.pack_json(m))

    def frame(self, win):
        """Demande un rendu ; les demandes rapprochées sont regroupées (60 Hz max)."""
        with self.lock:
            self.dirty.add(win.wid)

    def _render_loop(self):
        while self.alive:
            time.sleep(1 / 60)
            with self.lock:
                ids, self.dirty = self.dirty, set()
            for wid in ids:
                win = self.wins.get(wid)
                if win is not None:
                    try:
                        self._send_frame(win)
                    except OSError:
                        self.alive = False
                        return

    def _send_frame(self, win):
        data = win.render()
        stride = win.w * 4
        step = 32
        old = self.prev.get(win.wid)
        same_size = old is not None and old[:2] == (win.w, win.h)
        for y in range(0, win.h, step):
            a, b = y * stride, (y + min(step, win.h - y)) * stride
            if same_size and old[2][a:b] == data[a:b]:
                continue
            self.send(protocol.pack_tile(win.wid, 0, y, win.w, (b - a) // stride, data[a:b],
                                         compress=False))
        self.prev[win.wid] = (win.w, win.h, data)
        self.msg({"t": "frame", "id": win.wid, "w": win.w, "h": win.h})

    def add(self, win):
        rect = [win.x, win.y, win.w, win.h]
        if win.kind != "popup" and self.options["clamp"] and off_screen(rect, vm.SCREEN):
            win.x, win.y, win.w, win.h = fit_rect(rect, vm.SCREEN, CLAMP_MAX)
        self.wins[win.wid] = win
        self.msg({"t": "window.new", **win.info()})
        self.frame(win)

    def close(self, wid):
        for other in [w for w in self.wins.values() if w.owner == wid]:
            self.close(other.wid)
        if self.wins.pop(wid, None):
            self.msg({"t": "window.close", "id": wid})

    def new_window(self, title):
        wid = self.next_id
        self.next_id += 1
        n = len(self.wins)
        color = PALETTE[(wid - 100) % len(PALETTE)]
        self.add(Win(wid, title, (200 + 80 * n, 150 + 60 * n, 1600, 1000), color=color))

    def run(self):
        reader = protocol.FrameReader(self.conn)
        while True:
            ftype, m = reader.read()
            if ftype != protocol.T_JSON:
                continue
            try:
                self.handle(m)
            except (KeyError, ValueError) as e:
                log.warning("message invalide %s : %s", m, e)

    def set_options(self, m):
        """Champs `clamp`, `notifications` et `tray` de hello/display ; renvoie ceux qui
        viennent de passer à vrai."""
        turned_on = set()
        for k in self.options:
            if isinstance(m.get(k), bool):
                if m[k] and not self.options[k]:
                    turned_on.add(k)
                self.options[k] = m[k]
        return turned_on

    def schedule_notify(self, delay=None):
        def fire():
            if self.alive and self.options["notifications"]:
                try:
                    self.notify()
                except OSError:
                    pass
        timer = threading.Timer(NOTIFY_DELAY if delay is None else delay, fire)
        timer.daemon = True
        timer.start()

    def notify(self):
        self.notify_count += 1
        self.msg({"t": "notify", "id": self.notify_count, "app": "outlook", "appName": "Faux Outlook",
                  "title": "Marie Dupont", "body": f"Réunion de 14 h déplacée (message {self.notify_count})"})

    def send_tray(self):
        self.msg({"t": "tray", "items": [{
            "key": TRAY_KEY, "tooltip": "OneDrive - Faux", "png": tray_icon(),
            "exe": r"C:\Program Files\Microsoft OneDrive\OneDrive.exe", "source": "uia"}]})

    def reset_windows(self, ratio):
        count = 0
        for win in list(self.wins.values()):
            if win.kind == "popup":
                continue
            win.x, win.y, win.w, win.h = fit_rect([win.x, win.y, win.w, win.h], vm.SCREEN, ratio)
            self.msg({"t": "window.update", "id": win.wid, "rect": [win.x, win.y, win.w, win.h]})
            self.frame(win)
            count += 1
        self.msg({"t": "windows.reset.done", "count": count})

    def tray_click(self, m):
        if m.get("key") != TRAY_KEY:
            return
        if m.get("button") == "right":
            # menu ouvert près de l'icône, au pied de l'écran de Windows, comme dans la VM
            owner = next((w.wid for w in self.wins.values() if w.kind != "popup"), 0)
            wid = self.next_id
            self.next_id += 1
            self.add(Win(wid, "", (vm.SCREEN[0] - 440, vm.SCREEN[1] - 340, 420, 300), kind="popup",
                         owner=owner, color=PALETTE[0]))
        else:
            self.new_window("Faux OneDrive")

    def handle(self, m):
        t = m["t"]
        if t == "hello":
            turned_on = self.set_options(m)
            self.msg({"t": "hello", "version": protocol.VERSION, "agentVersion": version.VERSION,
                      "screen": list(vm.SCREEN), "dpi": 160})
            if not self.wins:
                self.new_window("Document 1 - Faux Word")
            else:
                self.prev.clear()
                for win in list(self.wins.values()):
                    self.msg({"t": "window.new", **win.info()})
                    self.frame(win)
            if self.options["tray"]:
                self.send_tray()
            if "notifications" in turned_on:
                self.schedule_notify()
            return
        if t == "display":
            turned_on = self.set_options(m)
            if "tray" in turned_on:
                self.send_tray()
            if "notifications" in turned_on:
                self.schedule_notify()
            return
        if t in ("theme", "fonts"):
            log.info("%s : %s", t, {k: v for k, v in m.items() if k != "t"})
            return
        if t == "windows.reset":
            self.reset_windows(float(m.get("max", 0.8)))
            return
        if t == "notify.activate":
            log.info("notification %s activée", m.get("id"))
            self.new_window(f"Message {m.get('id')} - Faux Outlook")
            return
        if t == "tray.click":
            self.tray_click(m)
            return
        if t == "launch":
            self.launch_count += 1
            self.new_window(f"{m['cmd']} ({self.launch_count})")
            self.msg({"t": "launched", "req": m["req"], "ok": True})
            return
        if t == "key":
            if m.get("down"):
                for win in self.wins.values():
                    if win.kind != "popup":
                        win.keys += 1
                        self.frame(win)
                        break
            return
        win = self.wins.get(m.get("id"))
        if win is None:
            return
        if t == "mouse.move":
            win.mouse = (m["x"], m["y"])
            hit = win.hit(m["x"], m["y"])
            cursor = CURSOR_EDGE.get(hit, "default")
            if (hit, cursor) != win.hover:
                win.hover = (hit, cursor)
                self.msg({"t": "hover", "id": win.wid, "hit": hit, "cursor": cursor})
            self.frame(win)
        elif t == "mouse.button" and m["down"]:
            self.click(win, m["x"], m["y"], m["button"])
        elif t == "mouse.wheel":
            win.color = tuple((c + (8 if m["dy"] > 0 else -8)) % 256 for c in win.color)
            self.frame(win)
        elif t == "window.close":
            self.close(win.wid)
        elif t == "window.resize":
            win.w, win.h = max(200, m["w"]), max(150, m["h"])
            self.msg({"t": "window.update", "id": win.wid, "rect": [win.x, win.y, win.w, win.h]})
            self.frame(win)
        elif t == "window.place" and win.kind != "popup":
            win.x, win.y, win.w, win.h = place_rect([m["x"], m["y"], m["w"], m["h"]], vm.SCREEN)
            self.msg({"t": "window.update", "id": win.wid, "rect": [win.x, win.y, win.w, win.h]})
            self.frame(win)
        elif t == "window.deactivate":
            for p in [w for w in self.wins.values() if w.kind == "popup"]:
                self.close(p.wid)

    def click(self, win, x, y, button):
        # un clic ailleurs ferme les menus ouverts
        for p in [w for w in self.wins.values() if w.kind == "popup" and w is not win]:
            self.close(p.wid)
        if win.kind == "popup":
            self.close(win.wid)
            return
        hit = win.hit(x, y)
        if button == 1 and hit == HTCLOSE:
            self.close(win.wid)
        elif button == 1 and hit == HTMAXBUTTON:
            self.msg({"t": "window.request", "id": win.wid, "action": "maximize"})
        elif button == 1 and hit == HTMINBUTTON:
            self.msg({"t": "window.request", "id": win.wid, "action": "minimize"})
        elif button == 3 and hit == HTCLIENT:
            wid = self.next_id
            self.next_id += 1
            self.add(Win(wid, "", (win.x + x, win.y + y, 420, 300), kind="popup",
                         owner=win.wid, color=win.color))
        elif button == 1 and hit == HTCLIENT:
            win.color = PALETTE[(PALETTE.index(win.color) + 1) % len(PALETTE)] \
                if win.color in PALETTE else PALETTE[0]
            self.frame(win)


def main():
    if vm.pid():
        raise SystemExit(_("la VM tourne : le socket est à elle"))
    path = str(vm.SERIAL)
    vm.ensure_data()
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(path)
    srv.listen(1)
    log.info("faux invité en écoute sur %s", path)
    guest_state = None
    try:
        while True:
            conn, _addr = srv.accept()
            log.info("hôte connecté")
            guest = FakeGuest(conn)
            if guest_state:
                guest.wins, guest.next_id = guest_state
            try:
                guest.run()
            except (EOFError, OSError) as e:
                log.info("hôte déconnecté (%s)", e or "fin")
            guest.alive = False
            guest_state = (guest.wins, guest.next_id)
            conn.close()
    except KeyboardInterrupt:
        pass
    finally:
        srv.close()
        os.unlink(path)
    return 0
