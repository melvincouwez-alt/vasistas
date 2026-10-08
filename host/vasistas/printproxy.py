#!/usr/bin/env python3
"""Relais d'une connexion IPP de Windows vers CUPS, lancé par QEMU pour chaque connexion.

QEMU (réseau user) redirige l'adresse PRINT_ADDR:631 de la VM vers cette commande
(`guestfwd=…-cmd:`) : la connexion arrive sur l'entrée et la sortie standard. CUPS n'est
donc jamais ouvert au réseau ; le relais parle à son socket local (/run/cups/cups.sock), ou à
localhost:631 à défaut.

CUPS refuse une requête locale dont l'en-tête Host n'est pas « localhost » (protection contre
le rebinding DNS) : le relais le remplace, et demande « Connection: close » pour que chaque
requête HTTP arrive sur une connexion neuve, donc avec un en-tête réécrit.

Autonome (aucun import du paquet vasistas) : QEMU le lance tel quel.
"""

import os
import re
import select
import socket
import sys

CUPS_SOCKET = "/run/cups/cups.sock"
CUPS_TCP = ("localhost", 631)
MAX_HEAD = 64 * 1024
CHUNK = 65536
# Sur son socket local, CUPS reconnaît l'appelant comme l'utilisateur Linux (souvent dans
# lpadmin) : seules l'impression et la lecture d'une file ou de son icône passent, jamais /admin
ALLOWED = re.compile(rb"(POST|GET) /(printers|icons)/[A-Za-z0-9_.@+%-]+(\.png)?(\?[^ ]*)? HTTP/1\.[01]")


def rewrite_head(head: bytes) -> bytes:
    """En-tête HTTP (jusqu'à la ligne vide comprise) : Host -> localhost, Connection: close."""
    lines = head.split(b"\r\n")
    out = [lines[0]]
    for line in lines[1:]:
        name = line.split(b":", 1)[0].strip().lower()
        if name in (b"host", b"connection", b"keep-alive"):
            continue
        out.append(line)
    # out finit par deux lignes vides (fin d'en-tête) : insérer avant
    while out and out[-1] == b"":
        out.pop()
    out += [b"Host: localhost", b"Connection: close", b"", b""]
    return b"\r\n".join(out)


def _connect():
    if os.path.exists(CUPS_SOCKET):
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            s.connect(CUPS_SOCKET)
            return s
        except OSError:
            s.close()
    return socket.create_connection(CUPS_TCP, timeout=10)


def _read_head(fd):
    buf = b""
    while b"\r\n\r\n" not in buf:
        data = os.read(fd, CHUNK)
        if not data:
            return None, b""
        buf += data
        if len(buf) > MAX_HEAD:
            return None, b""
    head, rest = buf.split(b"\r\n\r\n", 1)
    return head + b"\r\n\r\n", rest


def main():
    fin, fout = sys.stdin.fileno(), sys.stdout.fileno()
    head, rest = _read_head(fin)
    if head is None:
        return 1
    if not ALLOWED.fullmatch(head.split(b"\r\n", 1)[0]):
        os.write(fout, b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
        return 1
    cups = _connect()
    cups.settimeout(None)
    cups.sendall(rewrite_head(head) + rest)
    open_in = True
    while True:
        watch = [cups] + ([fin] if open_in else [])
        ready, _, _ = select.select(watch, [], [], 300)
        if not ready:
            return 0
        if fin in ready:
            data = os.read(fin, CHUNK)
            if data:
                cups.sendall(data)
            else:
                # pas de demi-fermeture vers CUPS : il abandonnerait la réponse ; il ferme
                # lui-même après elle (Connection: close)
                open_in = False
        if cups in ready:
            data = cups.recv(CHUNK)
            if not data:
                return 0
            while data:
                n = os.write(fout, data)
                data = data[n:]


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, KeyboardInterrupt):
        sys.exit(1)
