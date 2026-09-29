"""Page « Allègement » de l'application compagnon (voir slim.py)."""

import threading
import time

from gi.repository import GLib, Granite, Gtk

from . import slim, vm
from .companion_common import Page, dim, row


def _fmt_measure(m):
    if not m or m.get("ram_used_mb") is None:
        return ""
    when = time.strftime("%d/%m %H:%M", time.localtime(m.get("time", 0)))
    return (f"{m['ram_used_mb']:,} Mo de mémoire, {m['processes']} processus, "
            f"{m['services']} services ({when})").replace(",", " ")


class SlimPage(Page):
    __gtype_name__ = "VasistasSlimPage"

    def __init__(self, win):
        super().__init__("edit-clear-all", "Allègement",
                         "Retire de Windows ce qui ne sert pas dans Vasistas : moins de mémoire, "
                         "moins de processeur, moins de télémétrie.")
        self.win = win
        self.busy = False
        self.switches = {}  # key -> (switch, label d'état)
        self.status = slim.cached().get("status", {})
        box = self.box

        self.scan_btn = Gtk.Button(label="Relire l'état")
        self.scan_btn.set_tooltip_text("Lire dans Windows l'état de chaque élément")
        self.scan_btn.connect("clicked", lambda *_: self.scan())
        self.get_action_area().append(self.scan_btn)

        self.measure = Gtk.Label(xalign=0, wrap=True)
        box.append(self.measure)
        self.baseline = dim("")
        box.append(self.baseline)

        levels = list(slim.LEVELS)
        drop = Gtk.DropDown.new_from_strings([slim.LEVELS[n] for n in levels])
        drop.set_selected(1)
        pick = Gtk.Button(label="Cocher")
        pick.connect("clicked", lambda *_: self.pick_level(levels[drop.get_selected()]))
        lv = Gtk.Box(spacing=6)
        lv.append(drop)
        lv.append(pick)
        box.append(row("Niveau", "Cochez les éléments du niveau choisi, sans rien appliquer. "
                       "Léger : télémétrie, publicité, applis grand public. Fort : + services, "
                       "petites applis, Defender bridé. Maximal : + composants et maintenance.", lv))

        for group, title in slim.GROUPS.items():
            items = [it for it in slim.CATALOG if it["group"] == group]
            box.append(Granite.HeaderLabel.new(title))
            lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
            lb.add_css_class("rich-list")
            lb.add_css_class("card")
            lb.add_css_class("rounded")
            for it in items:
                sw = Gtk.Switch()
                state = Gtk.Label()
                state.add_css_class(Granite.STYLE_CLASS_DIM_LABEL)
                state.add_css_class(Granite.STYLE_CLASS_SMALL_LABEL)
                end = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
                end.append(state)
                end.append(sw)
                sub = it["subtitle"] + ("" if it.get("reversible", True)
                                        else ". Définitif : réinstallation depuis le Store ou l'instantané")
                r = row(it["title"], sub, end)
                r.set_margin_start(6)
                r.set_margin_end(6)
                lb.append(r)
                self.switches[it["key"]] = (sw, state)
            box.append(lb)

        self.apply_btn = Gtk.Button(label="Appliquer", halign=Gtk.Align.END, margin_top=12)
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.connect("clicked", lambda *_: self.confirm_apply())
        box.append(self.apply_btn)
        box.append(dim("Avant le premier changement, Vasistas prend un instantané du disque. Les réglages "
                       "se restaurent un par un en décochant ; les suppressions d'applis et de composants "
                       "ne reviennent qu'avec l'instantané."))

        box.append(Granite.HeaderLabel.new("Instantané du disque"))
        self.snap_label = Gtk.Label(xalign=0, wrap=True, hexpand=True)
        self.rollback_btn = Gtk.Button(label="Revenir à cet état…")
        self.rollback_btn.connect("clicked", lambda *_: self.confirm_rollback())
        self.drop_btn = Gtk.Button(label="Supprimer")
        self.drop_btn.connect("clicked", lambda *_: self.drop_snapshot())
        sb = Gtk.Box(spacing=6)
        sb.append(self.rollback_btn)
        sb.append(self.drop_btn)
        snap = Gtk.Box(spacing=12)
        snap.append(self.snap_label)
        snap.append(sb)
        box.append(snap)

        self.show_status()
        self.show_snapshot()

    # -- affichage --

    def show_status(self):
        labels = {"on": "", "partial": "partiel", "off": "", "unknown": "?"}
        for key, (sw, state) in self.switches.items():
            st = self.status.get(key)
            sw.set_active(st in ("on", "partial"))
            state.set_label(labels.get(st, "non lu"))
            reversible = slim.BY_KEY[key].get("reversible", True)
            # une suppression faite ne se défait pas d'un interrupteur
            sw.set_sensitive(not self.busy and (reversible or st != "on"))
        c = slim.cached()
        last = _fmt_measure(c.get("last"))
        self.measure.set_label(f"Windows utilise {last}" if last else "Cliquez sur « Relire l'état » pour lire l'état dans Windows.")
        base = _fmt_measure(c.get("baseline"))
        self.baseline.set_label(f"Avant allègement : {base}" if base and c.get("status") and
                                any(v != "off" for v in c["status"].values()) else "")
        self.apply_btn.set_sensitive(not self.busy)
        self.scan_btn.set_sensitive(not self.busy)
        return False

    def show_snapshot(self):
        def work():
            try:
                have = slim.SNAPSHOT in slim.snapshots()
            except Exception:  # noqa: BLE001
                have = False
            GLib.idle_add(self._show_snapshot, have)
        threading.Thread(target=work, daemon=True).start()

    def _show_snapshot(self, have):
        self.snap_label.set_label("Instantané « avant allègement » présent : il garde l'ancien disque, "
                                  "et donc de la place, tant qu'il n'est pas supprimé."
                                  if have else "Aucun instantané.")
        self.rollback_btn.set_sensitive(have and not self.busy)
        self.drop_btn.set_sensitive(have and not self.busy)
        return False

    # -- actions --

    def pick_level(self, level):
        keys = set(slim.keys_for_level(level))
        for key, (sw, _) in self.switches.items():
            if sw.get_sensitive():
                sw.set_active(key in keys)

    def _work(self, message, fn, done=None):
        self.busy = True
        self.show_status()
        self.win.notify(message)

        def run():
            try:
                res = fn()
                err = None
            except Exception as e:  # noqa: BLE001
                res, err = None, e
            GLib.idle_add(self._done, res, err, done)
        threading.Thread(target=run, daemon=True).start()

    def _done(self, res, err, done):
        self.busy = False
        if err is not None:
            self.win.notify(f"Échec : {err}")
        else:
            res = res or {}
            self.status.update(res.get("status", {}))
            errors = res.get("errors") or []
            if errors:
                self.win.notify(f"{len(errors)} erreur(s), détail dans host.log")
                import logging
                logging.getLogger(__name__).warning("allègement : %s", "\n".join(errors))
            elif done:
                self.win.notify(done(res))
        self.show_status()
        self.show_snapshot()
        return False

    def scan(self):
        self._work("Lecture de l'état dans Windows…", slim.status, lambda r: "État lu")

    def changes(self):
        to_apply, to_restore = [], []
        for key, (sw, _) in self.switches.items():
            st = self.status.get(key, "off")
            if sw.get_active() and st != "on":
                to_apply.append(key)
            elif not sw.get_active() and st in ("on", "partial"):
                to_restore.append(key)
        return to_apply, to_restore

    def confirm_apply(self):
        to_apply, to_restore = self.changes()
        if not to_apply and not to_restore:
            self.win.notify("Rien à changer")
            return
        final = [slim.BY_KEY[k]["title"] for k in to_apply if not slim.BY_KEY[k].get("reversible", True)]
        lines = [f"{len(to_apply)} élément(s) à appliquer, {len(to_restore)} à restaurer."]
        if final:
            lines.append("Définitif : " + ", ".join(final) + ".")
        lines.append("Un instantané du disque est pris avant, s'il n'existe pas déjà. "
                     "La suppression des composants peut prendre plusieurs minutes.")
        dlg = Gtk.AlertDialog(message="Alléger Windows ?", detail="\n".join(lines),
                              buttons=["Annuler", "Appliquer"], cancel_button=0, default_button=1)

        def answered(d, res):
            try:
                if d.choose_finish(res) == 1:
                    self.apply(to_apply, to_restore)
            except GLib.Error:
                pass
        dlg.choose(self.win, None, answered)

    def apply(self, to_apply, to_restore):
        def fn():
            out = {"status": {}, "errors": []}
            if to_apply:
                slim.snapshot()
                r = slim.apply(to_apply)
                out["status"].update(r.get("status", {}))
                out["errors"] += r.get("errors") or []
                out["reboot"] = r.get("reboot")
            if to_restore:
                r = slim.restore(to_restore)
                out["status"].update(r.get("status", {}))
                out["errors"] += r.get("errors") or []
                out["reboot"] = out.get("reboot") or r.get("reboot")
            return out

        def done(r):
            return "Fait. Redémarrez Windows pour terminer." if r.get("reboot") else \
                "Fait. Certains réglages prennent effet au prochain démarrage de Windows."
        self._work("Allègement en cours…", fn, done)

    def confirm_rollback(self):
        dlg = Gtk.AlertDialog(message="Revenir à l'état d'avant l'allègement ?",
                              detail="Tout le disque de Windows revient à l'instantané : les documents "
                                     "enregistrés dans Windows depuis (hors dossiers partagés) seront perdus. "
                                     "Windows doit être arrêté.",
                              buttons=["Annuler", "Revenir"], cancel_button=0, default_button=0)

        def answered(d, res):
            try:
                if d.choose_finish(res) == 1:
                    def fn():
                        slim.rollback()
                        slim.CACHE.unlink(missing_ok=True)
                        self.status = {}
                    self._work("Retour à l'instantané…", fn)
            except GLib.Error:
                pass
        dlg.choose(self.win, None, answered)

    def drop_snapshot(self):
        self._work("Suppression de l'instantané…", slim.drop_snapshot)

