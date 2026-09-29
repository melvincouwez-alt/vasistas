"""Mesure de fluidité sans GTK : connexion directe à l'agent, sélection à la souris en
va-et-vient dans une fenêtre, images par seconde et délai action -> image.

Usage : python3 bench.py [titre]   (la fenêtre doit être ouverte dans l'invité)
"""

import socket
import sys
import threading
import time

from vasistas import protocol, vm

title = sys.argv[1] if len(sys.argv) > 1 else "Bloc-notes"
s = socket.socket(socket.AF_UNIX)
s.connect(str(vm.SERIAL))
reader = protocol.FrameReader(s)
lock = threading.Lock()


def send(m):
    with lock:
        s.sendall(protocol.pack_json(m))


s.settimeout(2)
while True:  # l'agent peut ne pas être encore lancé
    send({"t": "hello", "version": 1, "scale": 1.0})
    try:
        t, m = reader.read()
        if t == protocol.T_JSON and m["t"] == "hello":
            break
    except socket.timeout:
        pass
send({"t": "launch", "req": 1, "cmd": "notepad", "args": []})
s.settimeout(None)
windows = {}
target = None
deadline = time.time() + 30
while target is None and time.time() < deadline:
    t, m = reader.read()
    if t == protocol.T_JSON and m["t"] == "window.new":
        windows[m["id"]] = m
        if title.lower() in (m.get("title") or "").lower() and m["kind"] != "popup":
            target = m
if target is None:
    sys.exit(f"pas de fenêtre « {title} » : {[w.get('title') for w in windows.values()]}")
wid = target["id"]
w, h = target["rect"][2:]
print(f"fenêtre {target['title']!r} {w}x{h}")

stats = {"frames": 0, "tiles": 0, "bytes": 0, "decode": 0.0, "lat": []}
last_input = [0.0]
stop = [False]


def read_loop():
    while not stop[0]:
        t, m = reader.read()
        if t == protocol.T_TILE:
            t0 = time.perf_counter()
            protocol.unpack_tile(m)
            stats["decode"] += time.perf_counter() - t0
            stats["tiles"] += 1
            stats["bytes"] += len(m)
        elif m["t"] == "frame" and m["id"] == wid:
            stats["frames"] += 1
            if last_input[0]:
                stats["lat"].append(time.perf_counter() - last_input[0])
                last_input[0] = 0.0
        elif m["t"] == "log":
            print("  agent :", m["msg"])


threading.Thread(target=read_loop, daemon=True).start()
send({"t": "window.activate", "id": wid})
time.sleep(0.5)
# du texte à sélectionner : 12 lignes de touches de la rangée du haut
for _ in range(12):
    for sc in list(range(0x10, 0x1A)) * 6 + [0x1C]:
        send({"t": "key", "sc": sc, "ext": False, "down": True})
        send({"t": "key", "sc": sc, "ext": False, "down": False})
time.sleep(1)
for k in stats:
    stats[k] = [] if k == "lat" else 0
y0 = 200
send({"t": "mouse.button", "id": wid, "x": 60, "y": y0, "button": 1, "down": True})
t_start = time.perf_counter()
n = 0
while time.perf_counter() - t_start < 5:
    phase = (n % 120) / 60
    x = int(60 + (min(phase, 2 - phase)) * (w - 200))
    y = int(y0 + (min(phase, 2 - phase)) * 400)
    if not last_input[0]:
        last_input[0] = time.perf_counter()
    send({"t": "mouse.move", "id": wid, "x": x, "y": y})
    n += 1
    time.sleep(1 / 120)
send({"t": "mouse.button", "id": wid, "x": 60, "y": y0, "button": 1, "down": False})
dt = time.perf_counter() - t_start
time.sleep(2.5)
stop[0] = True
lat = sorted(stats["lat"])
print(f"{stats['frames'] / dt:.0f} images/s, {stats['tiles'] / dt:.0f} tuiles/s, "
      f"{stats['bytes'] / dt / 1e6:.1f} Mo/s, décodage hôte {stats['decode'] / dt * 1000:.0f} ms par seconde")
if lat:
    print(f"délai action -> image : médiane {lat[len(lat) // 2] * 1000:.0f} ms, "
          f"90e centile {lat[int(len(lat) * 0.9)] * 1000:.0f} ms")
