"""Écran de la VM lu directement dans QEMU (affichage D-Bus, pair à pair).

QEMU tourne avec `-display dbus,p2p=yes`. On lui passe par QMP (getfd + add_client)
une extrémité d'une paire de sockets : c'est la connexion D-Bus « console ». On y
enregistre un écouteur (RegisterListener) sur une seconde paire de sockets ; QEMU nous
appelle alors pour chaque changement d'écran :
- Scanout / Update : pixels copiés dans le message ;
- ScanoutMap / UpdateMap : écran en mémoire partagée (memfd), seul le rectangle arrive.

Windows n'a plus rien à capturer : l'hôte découpe chaque fenêtre dans l'écran.

Zéro copie (2026-09-29) : la mémoire partagée de ScanoutMap devient un dmabuf par
/dev/udmabuf ; GTK l'affiche sans copie (GdkDmabufTexture). Sinon, repli sur la copie.
"""

import fcntl
import logging
import mmap
import os
import socket
import struct

from gi.repository import Gio, GLib

from . import vm

log = logging.getLogger(__name__)

CONSOLE_PATH = "/org/qemu/Display1/Console_0"
LISTENER_PATH = "/org/qemu/Display1/Listener"
# ioctl UDMABUF_CREATE = _IOW('u', 0x42, struct udmabuf_create { u32 memfd, flags; u64 offset, size; })
UDMABUF_CREATE = (1 << 30) | (24 << 16) | (ord("u") << 8) | 0x42
UDMABUF_FLAGS_CLOEXEC = 1
PAGE = mmap.PAGESIZE


def udmabuf(memfd, offset, size):
    """(fd dmabuf, décalage du premier pixel dans ce dmabuf), ou None si impossible."""
    if os.environ.get("VASISTAS_NO_DMABUF"):
        return None
    start = offset - offset % PAGE
    length = -(-(offset - start + size) // PAGE) * PAGE
    try:
        dev = os.open("/dev/udmabuf", os.O_RDWR | os.O_CLOEXEC)
    except OSError as e:
        log.info("pas de zéro copie : /dev/udmabuf %s", e.strerror)
        return None
    try:
        arg = bytearray(struct.pack("IIQQ", memfd, UDMABUF_FLAGS_CLOEXEC, start, length))
        return fcntl.ioctl(dev, UDMABUF_CREATE, arg, True), offset - start
    except OSError as e:
        try:
            seals = fcntl.fcntl(memfd, 1034)  # F_GET_SEALS
        except OSError:
            seals = "?"
        log.info("pas de zéro copie : udmabuf %s (sceaux %s, fichier %d o, début %d, longueur %d)",
                 e.strerror, seals, os.fstat(memfd).st_size, start, length)
        return None
    finally:
        os.close(dev)

LISTENER_XML = """
<node>
  <interface name="org.qemu.Display1.Listener">
    <method name="Scanout">
      <arg type="u" name="width" direction="in"/><arg type="u" name="height" direction="in"/>
      <arg type="u" name="stride" direction="in"/><arg type="u" name="pixman_format" direction="in"/>
      <arg type="ay" name="data" direction="in"/>
    </method>
    <method name="Update">
      <arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/>
      <arg type="i" name="width" direction="in"/><arg type="i" name="height" direction="in"/>
      <arg type="u" name="stride" direction="in"/><arg type="u" name="pixman_format" direction="in"/>
      <arg type="ay" name="data" direction="in"/>
    </method>
    <method name="ScanoutDMABUF">
      <arg type="h" name="dmabuf" direction="in"/><arg type="u" name="width" direction="in"/>
      <arg type="u" name="height" direction="in"/><arg type="u" name="stride" direction="in"/>
      <arg type="u" name="fourcc" direction="in"/><arg type="t" name="modifier" direction="in"/>
      <arg type="b" name="y0_top" direction="in"/>
    </method>
    <method name="UpdateDMABUF">
      <arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/>
      <arg type="i" name="width" direction="in"/><arg type="i" name="height" direction="in"/>
    </method>
    <method name="Disable"/>
    <method name="MouseSet">
      <arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/>
      <arg type="i" name="on" direction="in"/>
    </method>
    <method name="CursorDefine">
      <arg type="i" name="width" direction="in"/><arg type="i" name="height" direction="in"/>
      <arg type="i" name="hot_x" direction="in"/><arg type="i" name="hot_y" direction="in"/>
      <arg type="ay" name="data" direction="in"/>
    </method>
    <property name="Interfaces" type="as" access="read"/>
  </interface>
  <interface name="org.qemu.Display1.Listener.Unix.Map">
    <method name="ScanoutMap">
      <arg type="h" name="handle" direction="in"/><arg type="u" name="offset" direction="in"/>
      <arg type="u" name="width" direction="in"/><arg type="u" name="height" direction="in"/>
      <arg type="u" name="stride" direction="in"/><arg type="u" name="pixman_format" direction="in"/>
    </method>
    <method name="UpdateMap">
      <arg type="i" name="x" direction="in"/><arg type="i" name="y" direction="in"/>
      <arg type="i" name="width" direction="in"/><arg type="i" name="height" direction="in"/>
    </method>
  </interface>
</node>
"""


class Screen:
    """Copie de l'écran de la VM (BGRX, `stride` octets par ligne) et zones modifiées.

    on_damage(x, y, w, h) est appelé dans le fil GTK après chaque mise à jour."""

    def __init__(self, on_damage, on_resize=None):
        self.on_damage = on_damage
        self.on_resize = on_resize
        self.width = self.height = self.stride = 0
        self.fb = None          # bytearray (copie) ou mmap (mémoire partagée)
        self.mapped = None
        self.dmabuf = None      # (fd, décalage) de l'écran partagé, pour une texture sans copie
        self.console = None
        self.listener = None
        self._keep = []

    # -- connexion --

    def connect(self):
        """Ouvre la console D-Bus de QEMU (appel bloquant, pour les tests hors hôte GTK).
        Renvoie faux si la VM n'a pas l'affichage D-Bus."""
        q = None
        try:
            q = vm.Qmp()
            sock = self.attach(q)
        except (OSError, EOFError) as e:
            log.warning("affichage D-Bus indisponible : %s", e)
            return False
        finally:
            if q is not None:
                q.close()
        return sock is not None and self.open(sock)

    @staticmethod
    def attach(qmp):
        """Partie QMP (fil QMP de l'hôte) : passe à QEMU une extrémité d'une paire de sockets.
        Renvoie l'autre extrémité, ou None."""
        ours, theirs = socket.socketpair()
        try:
            qmp.send_fd("vasistas-display", theirs.fileno())
            qmp.cmd("add_client", protocol="@dbus-display", fdname="vasistas-display")
        except (OSError, EOFError):
            # connexion QMP périmée (VM relancée) : le fil QMP réessaie sur une connexion neuve
            ours.close()
            raise
        except RuntimeError as e:
            log.warning("affichage D-Bus indisponible : %s", e)
            ours.close()
            return None
        finally:
            theirs.close()
        return ours

    def open(self, ours):
        """Partie D-Bus (fil GTK) : console et écouteur sur la connexion obtenue par attach()."""
        try:
            self.console = self._dbus_client(ours)
        except GLib.Error as e:
            log.warning("console D-Bus : %s", e.message)
            return False
        self._register_listener()
        return True

    def _dbus_client(self, sock):
        self._keep.append(sock)
        gsock = Gio.Socket.new_from_fd(sock.detach())
        stream = gsock.connection_factory_create_connection()
        return Gio.DBusConnection.new_sync(
            stream, None, Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT, None, None)

    def _register_listener(self):
        ours, theirs = socket.socketpair()
        fds = Gio.UnixFDList.new()
        fds.append(theirs.fileno())
        # QEMU ouvre la connexion de l'écouteur pendant qu'il traite l'appel : on
        # l'établit de notre côté en parallèle, sans attendre la réponse.
        self.console.call_with_unix_fd_list(
            None, CONSOLE_PATH, "org.qemu.Display1.Console", "RegisterListener",
            GLib.Variant("(h)", (0,)), None, Gio.DBusCallFlags.NONE, -1, fds, None,
            self._on_registered, theirs)
        self._keep.append(ours)
        gsock = Gio.Socket.new_from_fd(ours.detach())
        stream = gsock.connection_factory_create_connection()
        # Messages retenus tant que l'objet n'est pas enregistré : sinon le premier appel de
        # QEMU (Scanout) échoue et il abandonne l'écouteur.
        Gio.DBusConnection.new(stream, None,
                               Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
                               | Gio.DBusConnectionFlags.DELAY_MESSAGE_PROCESSING,
                               None, None, self._on_listener_connection)

    def _on_registered(self, conn, result, theirs):
        theirs.close()
        try:
            conn.call_with_unix_fd_list_finish(result)
            log.info("écouteur d'écran enregistré")
        except GLib.Error as e:
            log.error("RegisterListener : %s", e.message)

    def _on_listener_connection(self, source, result):
        try:
            self.listener = Gio.DBusConnection.new_finish(result)
        except GLib.Error as e:
            log.error("connexion de l'écouteur : %s", e.message)
            return
        info = Gio.DBusNodeInfo.new_for_xml(LISTENER_XML)
        for iface in info.interfaces:
            self.listener.register_object(LISTENER_PATH, iface, self._on_call, self._on_get_property, None)
        self.listener.start_message_processing()

    def set_ui_info(self, width, height, width_mm=0, height_mm=0):
        """Demande à l'invité la résolution `width` x `height` (pixels physiques).
        Le pilote virtio-gpu de Windows la reçoit comme un nouvel écran branché."""
        if self.console is None:
            return
        self.console.call(None, CONSOLE_PATH, "org.qemu.Display1.Console", "SetUIInfo",
                          GLib.Variant("(qqiiuu)", (width_mm, height_mm, 0, 0, width, height)),
                          None, Gio.DBusCallFlags.NONE, -1, None, self._on_ui_info)

    def _on_ui_info(self, conn, result):
        try:
            conn.call_finish(result)
        except GLib.Error as e:
            log.warning("SetUIInfo : %s", e.message)

    # -- appels de QEMU --

    def _on_get_property(self, conn, sender, path, iface, prop):
        if prop == "Interfaces":
            return GLib.Variant("as", ["org.qemu.Display1.Listener.Unix.Map"])
        return None

    def _on_call(self, conn, sender, path, iface, method, params, invocation):
        try:
            handler = getattr(self, "_m_" + method, None)
            if handler is not None:
                handler(params, invocation)
        except Exception:
            log.exception("écran : %s", method)
        invocation.return_value(None)

    def _set_size(self, w, h, stride):
        changed = (w, h) != (self.width, self.height)
        self.width, self.height, self.stride = w, h, stride
        if changed and self.on_resize:
            self.on_resize(w, h)

    @staticmethod
    def _args(params, n):
        """Arguments simples sans passer par unpack() (très lent sur un tableau d'octets)."""
        return [params.get_child_value(i).unpack() for i in range(n)]

    @staticmethod
    def _bytes(params, i):
        return params.get_child_value(i).get_data_as_bytes().get_data()

    def _m_Scanout(self, params, inv):
        w, h, stride, fmt = self._args(params, 4)
        data = self._bytes(params, 4)
        self._unmap()
        self.fb = bytearray(data)
        self._set_size(w, h, stride)
        self.on_damage(0, 0, w, h)

    def _m_Update(self, params, inv):
        x, y, w, h, stride, fmt = self._args(params, 6)
        data = self._bytes(params, 6)
        if self.fb is None or self.mapped is not None:
            return
        row = w * 4
        dst = self.stride
        mv = memoryview(data)
        off = y * dst + x * 4
        for r in range(h):
            self.fb[off:off + row] = mv[r * stride:r * stride + row]
            off += dst
        self.on_damage(x, y, w, h)

    def _m_ScanoutMap(self, params, inv):
        _, offset, w, h, stride, fmt = params.unpack()
        fdlist = inv.get_message().get_unix_fd_list()
        fd = fdlist.get(0)
        self._unmap()
        size = offset + stride * h
        self.mapped = mmap.mmap(fd, size, mmap.MAP_SHARED, mmap.PROT_READ)
        self.dmabuf = udmabuf(fd, offset, stride * h)
        os.close(fd)
        self.fb = memoryview(self.mapped)[offset:offset + stride * h]
        self._set_size(w, h, stride)
        self.on_damage(0, 0, w, h)

    def _m_UpdateMap(self, params, inv):
        x, y, w, h = params.unpack()
        self.on_damage(x, y, w, h)

    def _m_Disable(self, params, inv):
        self._unmap()
        self.fb = None

    def _unmap(self):
        if self.dmabuf is not None:
            # la dernière texture peut encore s'en servir le temps d'une image
            fd = self.dmabuf[0]
            GLib.timeout_add_seconds(2, lambda: os.close(fd) or False)
            self.dmabuf = None
        if self.mapped is not None:
            fb, self.fb = self.fb, None
            if isinstance(fb, memoryview):
                fb.release()
            self.mapped.close()
            self.mapped = None


def main():
    """Test : compte les mises à jour d'écran pendant 10 s."""
    logging.basicConfig(level=logging.INFO)
    loop = GLib.MainLoop()
    stats = {"n": 0, "px": 0}

    def on_damage(x, y, w, h):
        stats["n"] += 1
        stats["px"] += w * h

    screen = Screen(on_damage, lambda w, h: print("écran", w, h))
    if not screen.connect():
        return 1

    def report():
        print(f"{stats['n'] / 2:.0f} mises à jour/s, {stats['px'] / 2e6:.1f} Mpx/s, "
              f"mémoire partagée : {screen.mapped is not None}")
        stats["n"] = stats["px"] = 0
        return True

    GLib.timeout_add_seconds(2, report)
    GLib.timeout_add_seconds(10, loop.quit)
    loop.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
