"""Page « Allègement » de l'application compagnon (voir slim.py)."""

import threading
import time

from gi.repository import GLib, Granite, Gtk

from . import slim
from .companion_common import Section, dim, row
from .i18n import _


def _fmt_measure(m):
    if not m or m.get("ram_used_mb") is None:
        return ""
    when = time.strftime("%d/%m %H:%M", time.localtime(m.get("time", 0)))
    ram = f"{m['ram_used_mb']:,}".replace(",", "\u202f")
    return _("{ram} Mo de mémoire, {processes} processus, {services} services ({when})",
             ram=ram, processes=m["processes"], services=m["services"], when=when)


class SlimPage(Section):
    __gtype_name__ = "VasistasSlimPage"

    def __init__(self, win):
        super().__init__("edit-clear-all", _("Allègement"), "")
        self.win = win
        self.busy = False
        self.switches = {}  # key -> (switch, label d'état)
        self.status = slim.cached().get("status", {})

        self.header(_("Allègement"))
        self.scan_btn = Gtk.Button(label=_("Relire l'état"))
        self.scan_btn.set_tooltip_text(_("Lire dans Windows l'état de chaque élément"))
        self.scan_btn.connect("clicked", lambda *_: self.scan())
        self.get_action_area().append(self.scan_btn)
        self.add(dim(_("Retirer de Windows les éléments inutiles dans Vasistas, pour réduire l'utilisation de la "
                       "mémoire et du processeur.")))
        self.measure = self.add(Gtk.Label(xalign=0, wrap=True))
        self.baseline = self.add(dim(""))

        # niveau Maximal pas encore vérifié : ses éléments restent dans « Personnaliser… »
        levels = [n for n in slim.LEVELS if n < 3]
        drop = Gtk.DropDown.new_from_strings([_(slim.LEVELS[n]) for n in levels])
        drop.set_selected(1)
        go = Gtk.Button(label=_("Appliquer ce niveau"))
        go.connect("clicked", lambda *_: (self.pick_level(levels[drop.get_selected()]), self.confirm_apply()))
        lv = Gtk.Box(spacing=6)
        lv.append(drop)
        lv.append(go)
        self.add(row(_("Niveau"), _("Léger : collecte de données et publicité. Fort : ajoute les services et "
                                    "applications inutiles dans Vasistas."), lv))

        # détail : chaque élément, à cocher un par un
        detail = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=6)
        for group, title in slim.GROUPS.items():
            items = [it for it in slim.CATALOG if it["group"] == group]
            detail.append(Granite.HeaderLabel.new(_(title)))
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
                sub = _(it["subtitle"]) + ("" if it.get("reversible", True)
                                           else _(". Suppression définitive : réinstallation possible depuis le "
                                                  "Store ou en revenant au point de restauration"))
                if it["level"] == 3:
                    sub += _(". Expérimental : pas encore vérifié, créez un point de restauration avant")
                r = row(_(it["title"]), sub, end)
                r.set_margin_start(6)
                r.set_margin_end(6)
                lb.append(r)
                self.switches[it["key"]] = (sw, state)
            detail.append(lb)
        self.apply_btn = Gtk.Button(label=_("Appliquer"), halign=Gtk.Align.END, margin_top=12)
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.connect("clicked", lambda *_: self.confirm_apply())
        detail.append(self.apply_btn)
        detail.append(dim(_("Pour annuler un réglage, désactivez-le. Les applications et composants supprimés ne "
                            "peuvent être récupérés qu'en revenant au point de restauration.")))
        self.add(Gtk.Expander(label=_("Personnaliser…"), child=detail))

        back = Gtk.LinkButton(label=_("Annuler l'allègement : onglet Restauration"), halign=Gtk.Align.START)
        back.connect("activate-link", lambda *_a: self.win.show_page("restoration") and True)
        self.add(dim(_("Vasistas crée un point de restauration avant le premier allègement.")))
        self.add(back)

        self.show_status()

    # -- affichage --

    def show_status(self):
        labels = {"on": "", "partial": _("partiel"), "off": "", "unknown": "?"}
        for key, (sw, state) in self.switches.items():
            st = self.status.get(key)
            sw.set_active(st in ("on", "partial"))
            state.set_label(labels.get(st, _("non lu")))
            reversible = slim.BY_KEY[key].get("reversible", True)
            # une suppression faite ne se défait pas d'un interrupteur
            sw.set_sensitive(not self.busy and (reversible or st != "on"))
        c = slim.cached()
        last = _fmt_measure(c.get("last"))
        self.measure.set_label(_("Windows utilise {measure}", measure=last) if last
                               else _("Cliquez sur « Relire l'état » pour lire l'état dans Windows."))
        base = _fmt_measure(c.get("baseline"))
        self.baseline.set_label(_("Avant allègement : {measure}", measure=base) if base and c.get("status") and
                                any(v != "off" for v in c["status"].values()) else "")
        self.apply_btn.set_sensitive(not self.busy)
        self.scan_btn.set_sensitive(not self.busy)
        return False

    # -- actions --

    def pick_level(self, level):
        keys = set(slim.keys_for_level(level))
        for key, (sw, _st) in self.switches.items():
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
            self.win.notify(_("Échec : {error}", error=err))
        else:
            res = res or {}
            self.status.update(res.get("status", {}))
            errors = res.get("errors") or []
            if errors:
                self.win.notify(_("{n} erreur(s), détail dans host.log", n=len(errors)))
                import logging
                logging.getLogger(__name__).warning("allègement : %s", "\n".join(errors))
            elif done:
                self.win.notify(done(res))
        self.show_status()
        return False

    def scan(self):
        self._work(_("Lecture de l'état dans Windows…"), slim.status, lambda r: _("État lu"))

    def changes(self):
        to_apply, to_restore = [], []
        for key, (sw, _st) in self.switches.items():
            st = self.status.get(key, "off")
            if sw.get_active() and st != "on":
                to_apply.append(key)
            elif not sw.get_active() and st in ("on", "partial"):
                to_restore.append(key)
        return to_apply, to_restore

    def confirm_apply(self):
        to_apply, to_restore = self.changes()
        if not to_apply and not to_restore:
            self.win.notify(_("Aucune modification à appliquer"))
            return
        final = [_(slim.BY_KEY[k]["title"]) for k in to_apply if not slim.BY_KEY[k].get("reversible", True)]
        lines = [_("{n} élément(s) à appliquer, {m} à restaurer.", n=len(to_apply), m=len(to_restore))]
        if final:
            lines.append(_("Suppression définitive : {items}.", items=", ".join(final)))
        lines.append(_("Vasistas crée un point de restauration avant l'allègement, s'il n'en existe pas déjà un. "
                       "La suppression des composants peut prendre plusieurs minutes."))
        dlg = Gtk.AlertDialog(message=_("Alléger Windows ?"), detail="\n".join(lines),
                              buttons=[_("Annuler"), _("Appliquer")], cancel_button=0, default_button=1)

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
            return _("Allègement terminé. Redémarrez Windows pour finaliser.") if r.get("reboot") else \
                _("Allègement terminé. Certains réglages prennent effet au prochain démarrage de Windows.")
        self._work(_("Allègement en cours…"), fn, done)
