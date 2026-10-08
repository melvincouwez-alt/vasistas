"""Page « Imprimantes » de l'application compagnon : les imprimantes de Linux dans Windows
(printers.py)."""

import threading

from .companion_common import Section, card, clear, dim, guest_ready, row  # versions de Gtk et Granite fixées

from gi.repository import GLib, Granite, Gtk  # noqa: E402

from . import control, printers, vm  # noqa: E402
from .i18n import _  # noqa: E402


class PrintersPage(Section):
    __gtype_name__ = "VasistasPrintersPage"

    def __init__(self, win):
        super().__init__("printer", _("Imprimantes"),
                         _("Imprimer depuis Windows sur les imprimantes de Linux."))
        self.win = win
        self.state = {}
        self.refresh_btn = Gtk.Button(label=_("Actualiser"))
        self.refresh_btn.set_tooltip_text(_("Relire les imprimantes de Linux et les reporter dans Windows"))
        self.refresh_btn.connect("clicked", lambda *_: self.sync())
        self.get_action_area().append(self.refresh_btn)

        sw = Gtk.Switch(active=printers.enabled())
        sw.connect("notify::active", self.on_switch)
        self.add(row(_("Partager les imprimantes avec Windows"),
                     _("Chaque imprimante de Linux apparaît dans Windows sous son nom suivi de "
                       "« (Linux) ». Linux garde les pilotes et les réglages ; rien n'est ouvert au "
                       "réseau. S'applique au prochain démarrage de Windows."), sw))
        self.hint = self.add(dim(""))
        self.header(_("Imprimantes de Linux"))
        self.list = self.add(card())
        self.fill()

    def update(self, state):
        self.state = state
        ready = guest_ready(state)
        on = printers.enabled()
        self.refresh_btn.set_sensitive(on)
        if not on:
            text = ""
        elif state.get("pid") and not printers.forward_active(state.get("pid")):
            text = _("Windows a démarré avant l'activation de cette option : redémarrez Windows pour partager les "
                     "imprimantes.")
        elif not ready:
            text = _("Les imprimantes sont reportées dans Windows au prochain démarrage de Windows.")
        else:
            text = _("Les imprimantes sont reportées dans Windows à chaque démarrage de Windows, ou avec le bouton "
                     "« Actualiser ».")
        self.hint.set_label(text)
        self.hint.set_visible(bool(text))

    def fill(self):
        clear(self.list)

        def work():
            items = printers.linux_printers()
            found = printers.discovered_only() if not items else []
            GLib.idle_add(self.show, items, found)
        threading.Thread(target=work, daemon=True).start()

    def show(self, items, found):
        clear(self.list)
        if not items:
            text = _("Aucune imprimante ajoutée dans les Paramètres de Linux.")
            if found:
                text += " " + _("Détectées sur le réseau sans être ajoutées : {names}.", names=", ".join(found))
            self.list.append(Gtk.Label(label=text, wrap=True, margin_top=8, margin_bottom=8,
                                       margin_start=6, margin_end=6))
        for p in items:
            box = Gtk.Box(spacing=12, margin_start=6, margin_end=6, margin_top=4, margin_bottom=4)
            box.append(Gtk.Image(icon_name="printer", pixel_size=32))
            labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
            title = printers.windows_name(p["name"])
            if p["default"]:
                title += " · " + _("par défaut sous Linux")
            labels.append(Gtk.Label(label=title, xalign=0))
            sub = Gtk.Label(label=p["uri"], xalign=0, ellipsize=3)
            sub.add_css_class(Granite.STYLE_CLASS_DIM_LABEL)
            sub.add_css_class(Granite.STYLE_CLASS_SMALL_LABEL)
            labels.append(sub)
            box.append(labels)
            self.list.append(Gtk.ListBoxRow(activatable=False, child=box))
        return False

    def on_switch(self, sw, _p):
        c = vm.load_config()
        c["printers"] = sw.get_active()
        if not sw.get_active():
            c["printers_cleanup"] = True  # retirer les imprimantes de Windows
        else:
            c.pop("printers_cleanup", None)
        vm.save_config(c)
        self.update(self.state)
        if not sw.get_active() and guest_ready(self.state):
            self.sync()

    def sync(self):
        """Ajoute ou retire maintenant les imprimantes dans Windows (Windows en marche)."""
        self.fill()
        if not guest_ready(self.state):
            if printers.enabled():
                self.win.notify(_("Les imprimantes seront reportées au prochain démarrage de Windows"))
            return
        on = printers.enabled()
        if on and not printers.forward_active(self.state.get("pid")):
            self.win.notify(_("Redémarrez Windows pour partager les imprimantes"))
            return
        self.refresh_btn.set_sensitive(False)

        def work():
            try:
                items = printers.linux_printers() if on else []
                res = control.request({"exec": printers.sync_script(items)}, timeout=180)
                parsed = printers.parse_result(res.get("out"))
                if res.get("error") or not parsed:
                    msg = _("Échec : {error}", error=res.get("error") or (res.get("out") or "").strip()[-160:])
                elif parsed.get("errors"):
                    msg = _("Échec : {error}", error="; ".join(parsed["errors"])[:200])
                else:
                    if not on:
                        c = vm.load_config()
                        c.pop("printers_cleanup", None)
                        vm.save_config(c)
                    msg = _("Imprimantes dans Windows : {n} ajoutée(s), {m} retirée(s)",
                            n=len(parsed.get("added") or []), m=len(parsed.get("removed") or []))
            except (OSError, ValueError) as e:
                msg = _("Échec : {error}", error=e)
            GLib.idle_add(self.win.notify, msg)
            GLib.idle_add(self.refresh_btn.set_sensitive, printers.enabled())
        threading.Thread(target=work, daemon=True).start()
