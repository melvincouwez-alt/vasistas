"""Test de perte de touches : envoie N appuis de la touche A (scancode 0x10 = « a » en AZERTY)."""
import socket, sys, time
from vasistas import protocol, vm

n = int(sys.argv[1]) if len(sys.argv) > 1 else 200
delay = float(sys.argv[2]) if len(sys.argv) > 2 else 0.0
s = socket.socket(socket.AF_UNIX); s.connect(str(vm.SERIAL)); s.settimeout(2)
r = protocol.FrameReader(s)
while True:
    s.sendall(protocol.pack_json({"t": "hello", "version": 1, "scale": 1.0}))
    try:
        t, m = r.read()
        if t == 1 and m["t"] == "hello": break
    except socket.timeout: pass
wins = {}
end = time.time() + 3
s.settimeout(0.5)
while time.time() < end:
    try:
        t, m = r.read()
        if t == 1 and m["t"] == "window.new": wins[m["id"]] = m
    except socket.timeout: pass
np = [w for w in wins.values() if "Bloc-notes" in (w.get("title") or "")]
s.sendall(protocol.pack_json({"t": "window.activate", "id": np[0]["id"]}))
time.sleep(0.5)
for i in range(n):
    s.sendall(protocol.pack_json({"t": "key", "sc": 0x10, "ext": False, "down": True}))
    s.sendall(protocol.pack_json({"t": "key", "sc": 0x10, "ext": False, "down": False}))
    if delay: time.sleep(delay)
s.sendall(protocol.pack_json({"t": "key", "sc": 0x1C, "ext": False, "down": True}))
s.sendall(protocol.pack_json({"t": "key", "sc": 0x1C, "ext": False, "down": False}))
end = time.time() + 5
while time.time() < end:
    try:
        t, m = r.read()
        if t == 1 and m["t"] == "log": print(m["msg"])
    except socket.timeout: pass
