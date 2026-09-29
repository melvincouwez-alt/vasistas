"""Socket de contrôle de l'hôte : une requête JSON par ligne, une réponse JSON par ligne.

Sert à `vasistas exec` et servira à l'application compagnon. Requêtes :
- {"exec": "<script PowerShell>"} -> {"code": n, "out": "..."}
- {"status": true} -> {"guest_ready": bool, "windows": n}
"""

import json
import logging
import os
import socket
import threading

from gi.repository import GLib

from .vm import DATA

log = logging.getLogger(__name__)

SOCKET = DATA / "control.sock"


class ControlServer:
    """Fil d'écoute ; chaque requête est traitée dans le fil GTK via `handler(req, reply)`,
    `reply(dict)` pouvant être appelé plus tard (réponse de l'invité)."""

    def __init__(self, handler):
        self.handler = handler
        self.sock = None

    def start(self):
        try:
            os.unlink(SOCKET)
        except FileNotFoundError:
            pass
        SOCKET.parent.mkdir(parents=True, exist_ok=True)
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.bind(str(SOCKET))
        os.chmod(SOCKET, 0o600)
        self.sock.listen(8)
        threading.Thread(target=self._accept, name="vasistas-control", daemon=True).start()

    def _accept(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            self._serve_lines(conn)
        except OSError:
            pass  # client parti avant la réponse (délai dépassé côté compagnon)

    def _serve_lines(self, conn):
        with conn, conn.makefile("rwb") as f:
            for line in f:
                try:
                    req = json.loads(line)
                except ValueError:
                    continue
                done = threading.Event()
                result = {}

                def reply(res, result=result, done=done):
                    result.update(res)
                    done.set()

                GLib.idle_add(self._dispatch, req, reply)
                done.wait()
                try:
                    f.write(json.dumps(result).encode() + b"\n")
                    f.flush()
                except OSError:
                    return

    def _dispatch(self, req, reply):
        try:
            self.handler(req, reply)
        except Exception as e:
            log.exception("requête de contrôle")
            reply({"error": str(e)})
        return False


def request(req: dict, timeout=None) -> dict:
    """Client : envoie une requête à l'hôte en cours d'exécution."""
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(str(SOCKET))
    with s, s.makefile("rwb") as f:
        f.write(json.dumps(req).encode() + b"\n")
        f.flush()
        line = f.readline()
    return json.loads(line) if line else {"error": "pas de réponse"}
