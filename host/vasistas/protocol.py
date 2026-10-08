"""Trames Vasistas : marqueur « VS », longueur u32 LE, type u8, charge utile (voir PROTOCOL.md)."""

import json
import struct
import zlib

try:
    from compression import zstd  # Python 3.14
except ImportError:
    zstd = None

VERSION = 1

T_JSON = 1
T_TILE = 2

TILE_HEADER = struct.Struct("<IHHHHB")
ENC_RAW = 0
ENC_DEFLATE = 1
ENC_ZSTD = 2

MAX_FRAME = 64 * 1024 * 1024
MAGIC = b"VS"


def hello(**extra) -> dict:
    """hello de l'hôte ; zstd : l'invité peut compresser ses tuiles en zstd."""
    return {"t": "hello", "version": VERSION, "zstd": zstd is not None, **extra}


def pack_json(msg: dict) -> bytes:
    body = json.dumps(msg, separators=(",", ":")).encode()
    return MAGIC + struct.pack("<IB", len(body) + 1, T_JSON) + body


def pack_tile(wid: int, x: int, y: int, w: int, h: int, bgra: bytes, compress=True) -> bytes:
    enc = ENC_RAW
    if compress:
        bgra = zlib.compress(bgra, 1, wbits=-15)
        enc = ENC_DEFLATE
    body = TILE_HEADER.pack(wid, x, y, w, h, enc) + bgra
    return MAGIC + struct.pack("<IB", len(body) + 1, T_TILE) + body


def unpack_tile(payload: bytes):
    wid, x, y, w, h, enc = TILE_HEADER.unpack_from(payload)
    data = memoryview(payload)[TILE_HEADER.size:]  # sans copie
    if enc == ENC_ZSTD:
        data = zstd.decompress(data)
    elif enc == ENC_DEFLATE:
        data = zlib.decompress(data, wbits=-15)
    if len(data) != w * h * 4:
        raise ValueError(f"tuile {w}x{h} : {len(data)} octets")
    return wid, x, y, w, h, data


class FrameReader:
    """Lit des trames complètes depuis un socket bloquant.

    Après une coupure, le flux peut reprendre au milieu d'une trame : on cherche alors
    le marqueur suivant et on ignore les trames incohérentes."""

    def __init__(self, sock):
        self.sock = sock
        self.buf = bytearray()

    def _fill(self, n: int):
        while len(self.buf) < n:
            chunk = self.sock.recv(max(n - len(self.buf), 1 << 20))
            if not chunk:
                raise EOFError
            self.buf += chunk

    def read(self):
        while True:
            self._fill(7)
            if self.buf[:2] != MAGIC:
                i = self.buf.find(MAGIC, 1)
                del self.buf[:i if i > 0 else len(self.buf) - 1]
                continue
            (length,) = struct.unpack_from("<I", self.buf, 2)
            ftype = self.buf[6]
            if length < 1 or length > MAX_FRAME or ftype not in (T_JSON, T_TILE):
                del self.buf[:2]
                continue
            self._fill(6 + length)
            body = self.buf[7:6 + length]  # une seule copie (bytearray)
            del self.buf[:6 + length]
            if ftype == T_JSON:
                try:
                    return T_JSON, json.loads(body)
                except ValueError:
                    continue
            return ftype, body
