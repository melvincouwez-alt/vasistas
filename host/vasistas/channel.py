"""Connexion au port virtio-serial et assemblage des images.

Un fil de lecture décode les trames, colle les tuiles dans un tampon par fenêtre et
remet au fil GTK les messages de contrôle et les images terminées (via GLib.idle_add).
"""

import collections
import logging
import socket
import threading
import time

from gi.repository import GLib

from . import protocol

log = logging.getLogger(__name__)

# Messages en attente d'envoi au plus (invité en pause ou figé) : au-delà, les mouvements de
# souris sont jetés, jamais les touches ni les clics (un relâchement perdu bloquerait la touche).
SEND_QUEUE_MAX = 2000
DROPPABLE = {"mouse.move", "mouse.wheel", "hover"}


class Surface:
    __slots__ = ("w", "h", "fb", "pending", "scheduled")

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.fb = bytearray(w * h * 4)
        self.pending = None
        self.scheduled = False

    def resize(self, w, h):
        if (w, h) != (self.w, self.h):
            self.w, self.h = w, h
            self.fb = bytearray(w * h * 4)

    def blit(self, x, y, w, h, data):
        if x + w > self.w or y + h > self.h:
            return  # tuile d'une taille périmée
        stride = self.w * 4
        row = w * 4
        fb = self.fb
        mv = memoryview(data)
        off = (y * self.w + x) * 4
        for r in range(h):
            fb[off:off + row] = mv[r * row:(r + 1) * row]
            off += stride


class Channel:
    """on_message(msg) et on_frame(id, w, h, bytes) sont appelés dans le fil GTK.
    on_state(connected: bool) aussi."""

    def __init__(self, path, on_message, on_frame, on_state):
        self.path = path
        self.on_message = on_message
        self.on_frame = on_frame
        self.on_state = on_state
        self.sock = None
        # envoi par un fil dédié : le fil GTK ne se bloque jamais sur le socket
        self.outbox = collections.deque()
        self.out_cond = threading.Condition()
        self.counters = {"sent": 0, "coalesced": 0, "dropped": 0, "max_queue": 0}
        self.surfaces = {}
        self.surf_lock = threading.Lock()
        self.guest_ready = False
        self.hello_extra = {}  # champs ajoutés au hello (échelle de l'écran)
        self._stop = False
        self._thread = threading.Thread(target=self._run, name="vasistas-reader", daemon=True)

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop = True
        s = self.sock
        if s:
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    # -- envoi (tout fil) --

    def send(self, msg: dict):
        """Met le message en file (tout fil) ; faux si aucun invité n'est connecté."""
        if self.sock is None:
            return False
        with self.out_cond:
            q = self.outbox
            if msg.get("t") == "mouse.move" and q and q[-1].get("t") == "mouse.move":
                q[-1] = msg  # mouvement pas encore parti : seul le dernier compte
                self.counters["coalesced"] += 1
            else:
                if len(q) >= SEND_QUEUE_MAX:
                    kept = collections.deque(m for m in q if m.get("t") not in DROPPABLE)
                    self.counters["dropped"] += len(q) - len(kept)
                    self.outbox = q = kept
                q.append(msg)
            self.counters["max_queue"] = max(self.counters["max_queue"], len(q))
            self.out_cond.notify()
        return True

    def stats(self):
        with self.out_cond:
            return dict(self.counters, queued=len(self.outbox))

    def _write_loop(self, s):
        """Fil d'envoi d'une connexion ; s'arrête quand elle change."""
        while True:
            with self.out_cond:
                while not self.outbox and self.sock is s:
                    # sans délai : _run réveille le fil (notify_all) quand la connexion tombe
                    self.out_cond.wait()
                if self.sock is not s:
                    return
                batch = list(self.outbox)
                self.outbox.clear()
            data = b"".join(protocol.pack_json(m) for m in batch)
            try:
                s.sendall(data)
                self.counters["sent"] += len(batch)
            except OSError as e:
                log.warning("envoi impossible : %s", e)
                return

    # -- fil de lecture --

    def _run(self):
        while not self._stop:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(self.path)
            except OSError:
                time.sleep(1)
                continue
            log.info("connecté à %s", self.path)
            with self.out_cond:
                self.outbox.clear()  # rien de l'ancienne connexion
                self.sock = s
            self.guest_ready = False
            threading.Thread(target=self._write_loop, args=(s,), name="vasistas-writer", daemon=True).start()
            GLib.idle_add(self.on_state, True)
            threading.Thread(target=self._hello_loop, args=(s,), daemon=True).start()
            try:
                self._read_loop(s)
            except (EOFError, OSError) as e:
                log.info("connexion fermée : %s", e or "fin de flux")
            except Exception:
                log.exception("erreur de lecture")
            with self.out_cond:
                self.sock = None
                self.outbox.clear()
                self.out_cond.notify_all()
            try:
                s.close()
            except OSError:
                pass
            with self.surf_lock:
                self.surfaces.clear()
            GLib.idle_add(self.on_state, False)
            time.sleep(1)

    def _hello_loop(self, s):
        while self.sock is s and not self.guest_ready:
            self.send(protocol.hello(**self.hello_extra))
            time.sleep(1)

    def _read_loop(self, s):
        reader = protocol.FrameReader(s)
        while True:
            ftype, payload = reader.read()
            if ftype == protocol.T_TILE:
                wid, x, y, w, h, data = protocol.unpack_tile(payload)
                surf = self.surfaces.get(wid)
                if surf is not None:
                    surf.blit(x, y, w, h, data)
            elif ftype == protocol.T_JSON:
                self._handle_json(payload)

    def _handle_json(self, msg):
        t = msg.get("t")
        if t == "hello":
            self.guest_ready = True
        elif t in ("window.new", "window.update") and "rect" in msg:
            _, _, w, h = msg["rect"]
            w, h = max(1, w), max(1, h)
            with self.surf_lock:
                surf = self.surfaces.get(msg["id"])
                if surf is None:
                    self.surfaces[msg["id"]] = Surface(w, h)
                else:
                    surf.resize(w, h)
        elif t == "window.close":
            with self.surf_lock:
                self.surfaces.pop(msg["id"], None)
        elif t == "frame":
            self._commit(msg["id"])
            return
        GLib.idle_add(self.on_message, msg)

    def _commit(self, wid):
        surf = self.surfaces.get(wid)
        if surf is None:
            return
        surf.pending = (surf.w, surf.h, bytes(surf.fb))
        if not surf.scheduled:
            surf.scheduled = True
            GLib.idle_add(self._deliver, wid, surf)

    def _deliver(self, wid, surf):
        surf.scheduled = False
        pending, surf.pending = surf.pending, None
        if pending is not None:
            self.on_frame(wid, *pending)
        return False
