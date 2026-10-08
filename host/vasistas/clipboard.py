"""Presse-papiers partagé entre Linux et l'invité : texte, HTML et image PNG.

Sous Wayland, une application ne lit le presse-papiers que lorsqu'elle a le focus : le
contenu Linux part donc vers Windows quand une fenêtre invitée prend le focus, puis à
chaque changement tant qu'elle l'a. Une copie faite dans Windows arrive par l'agent.
"""

import base64
import hashlib
import logging
import threading

from gi.repository import Gdk, Gio, GLib

log = logging.getLogger(__name__)

MAX_BYTES = 24 << 20
# Lecture abandonnée au-delà : une application source qui ne répond pas (ou un ancien hôte
# figé qui détenait le presse-papiers) ne doit jamais bloquer l'hôte. Tout est asynchrone.
READ_TIMEOUT_MS = 2000


class ClipboardSync:
    def __init__(self, send, has_focus):
        self.send = send
        self.has_focus = has_focus        # vrai si une fenêtre invitée a le focus
        self.clip = Gdk.Display.get_default().get_clipboard()
        self.clip.connect("changed", self._on_changed)
        self.last_sent = None             # empreinte du dernier contenu envoyé à l'invité
        self.setting = False
        # contenu Linux changé depuis le dernier envoi : sans ça, chaque passage d'une fenêtre
        # Windows à l'autre relisait tout (une capture d'écran réencodée en PNG dans le fil GTK)
        self.dirty = True

    # -- Windows -> Linux --

    def from_guest(self, msg):
        providers = []
        text = msg.get("text")
        html = msg.get("html")
        png = msg.get("png")
        if png:
            data = base64.b64decode(png)
            try:
                texture = Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
                providers.append(Gdk.ContentProvider.new_for_value(texture))
            except GLib.Error as e:
                log.warning("image du presse-papiers : %s", e.message)
        if html:
            providers.append(Gdk.ContentProvider.new_for_bytes(
                "text/html", GLib.Bytes.new(html.encode("utf-8"))))
        if text:
            providers.append(Gdk.ContentProvider.new_for_bytes(
                "text/plain;charset=utf-8", GLib.Bytes.new(text.encode("utf-8"))))
            providers.append(Gdk.ContentProvider.new_for_value(text))
        if not providers:
            return
        # ce contenu vient de l'invité : ne pas le lui renvoyer
        self.last_sent = self._fingerprint(text, html, png)
        self.setting = True
        self.clip.set_content(Gdk.ContentProvider.new_union(providers))
        self.setting = False

    # -- Linux -> Windows --

    def _on_changed(self, clip):
        if self.setting or clip.is_local():
            return
        self.dirty = True
        if self.has_focus():
            self.push()

    def push(self):
        """Lit le presse-papiers Linux et l'envoie à l'invité s'il a changé."""
        if self.clip.is_local() or not self.dirty:
            return  # c'est nous qui l'avons posé (copie venant de Windows), ou rien de neuf
        self.dirty = False
        formats = self.clip.get_formats()
        state = {"text": None, "html": None, "png": None, "pending": 0}
        cancel = Gio.Cancellable()
        GLib.timeout_add(READ_TIMEOUT_MS, lambda: cancel.cancel() or False)

        def done():
            state["pending"] -= 1
            if state["pending"] == 0:
                self._send(state["text"], state["html"], state["png"])

        if formats.contain_gtype(Gdk.Texture):
            state["pending"] += 1
            self.clip.read_texture_async(cancel, self._on_texture, state, done)
        if formats.contain_mime_type("text/html"):
            state["pending"] += 1
            self.clip.read_async(["text/html"], GLib.PRIORITY_DEFAULT, cancel,
                                 self._on_stream, ("html", state, done, cancel))
        if formats.contain_gtype(str) or formats.contain_mime_type("text/plain;charset=utf-8") \
                or formats.contain_mime_type("text/plain"):
            state["pending"] += 1
            self.clip.read_text_async(cancel, self._on_text, state, done)

    def _on_texture(self, clip, result, state, done):
        try:
            texture = clip.read_texture_finish(result)
        except GLib.Error as e:
            log.debug("lecture image : %s", e.message)
            texture = None
        if texture is None:
            done()
            return

        # PNG d'une capture d'écran : 250 ms et plus, hors du fil GTK (la texture est immuable)
        def encode():
            png = texture.save_to_png_bytes().get_data()
            if len(png) <= MAX_BYTES:
                state["png"] = base64.b64encode(png).decode()
            GLib.idle_add(lambda: done() or False)
        threading.Thread(target=encode, name="vasistas-clip-png", daemon=True).start()

    def _on_text(self, clip, result, state, done):
        try:
            state["text"] = clip.read_text_finish(result)
        except GLib.Error as e:
            log.debug("lecture texte : %s", e.message)
        done()

    def _on_stream(self, clip, result, data):
        key, state, done, cancel = data
        try:
            stream, _ = clip.read_finish(result)
        except GLib.Error as e:
            log.debug("lecture %s : %s", key, e.message)
            done()
            return
        # copie asynchrone : un splice() synchrone attendait l'application source dans le fil
        # GTK et figeait l'hôte (constaté le 2026-09-29)
        out = Gio.MemoryOutputStream.new_resizable()
        out.splice_async(stream, Gio.OutputStreamSpliceFlags.CLOSE_SOURCE | Gio.OutputStreamSpliceFlags.CLOSE_TARGET,
                         GLib.PRIORITY_DEFAULT, cancel, self._on_spliced, (key, state, done))

    def _on_spliced(self, out, result, data):
        key, state, done = data
        try:
            out.splice_finish(result)
            raw = out.steal_as_bytes().get_data()
            if len(raw) <= MAX_BYTES:
                state[key] = raw.decode("utf-8", errors="replace")
        except GLib.Error as e:
            log.debug("lecture %s : %s", key, e.message)
        done()

    def _send(self, text, html, png):
        fp = self._fingerprint(text, html, png)
        if fp == self.last_sent or not (text or html or png):
            return
        self.last_sent = fp
        msg = {"t": "clipboard"}
        if text:
            msg["text"] = text
        if html:
            msg["html"] = html
        if png:
            msg["png"] = png
        self.send(msg)

    @staticmethod
    def _fingerprint(text, html, png):
        h = hashlib.sha1()
        for part in (text or "", html or "", png or ""):
            h.update(part.encode() if isinstance(part, str) else part)
            h.update(b"\0")
        return h.hexdigest()
