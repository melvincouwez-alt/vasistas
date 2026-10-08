"""Onglet « Restauration » de l'application compagnon (voir restore.py) : la liste des points à gauche,
avec celui que l'allègement prend avant de toucher à Windows (slim.SNAPSHOT), les points
automatiques à droite."""

import threading
import time

from gi.repository import GLib, Granite, Gtk

from . import restore, slim, vm
from .companion_common import ColumnsPage, Section, card, clear, confirm, dim, row
from .i18n import _

NEW = ""  # ligne provisoire du point en cours de création


def slim_point():
    """Le point « avant allègement » sous la forme d'un point de restore.points(), None s'il n'existe pas."""
    for s in restore.snapshots():
        if s["tag"] == slim.SNAPSHOT:
            try:
                size = restore.exclusive_sizes(vm.DISK).get(slim.SNAPSHOT)
            except (OSError, ValueError):
                size = None
            return {"tag": slim.SNAPSHOT, "slim": True, "auto": True, "name": None, "created": s["created"],
                    "size": size, "label": _("Avant allègement")}
    return None


class RestorePage(Section):
    """Liste des points : en créer un, revenir à l'un d'eux, en supprimer."""
    __gtype_name__ = "VasistasRestorePage"

    def __init__(self, win):
        super().__init__("document-revert", _("Points de restauration"), "")
        self.win = win
        self.busy = False
        self.running = bool(vm.pid())
        self.items = []
        self.working = None  # (tag ou NEW, texte) : la ligne qui tourne pendant l'opération
        self.revealers = {}

        self.header(_("Points de restauration"))
        self.add(dim(_("Un point de restauration enregistre l'état complet de Windows. Les fichiers des dossiers "
                       "partagés ne sont jamais modifiés par une restauration.")))
        self.name = Gtk.Entry(placeholder_text=_("Nom (facultatif)"), hexpand=True)
        self.name.connect("activate", lambda *_a: self.create())
        self.create_btn = Gtk.Button(label=_("Créer un point de restauration"))
        self.create_btn.add_css_class(Granite.STYLE_CLASS_SUGGESTED_ACTION)
        self.create_btn.connect("clicked", lambda *_a: self.create())
        new = Gtk.Box(spacing=6)
        new.append(self.name)
        new.append(self.create_btn)
        self.add(new)
        self.list = self.add(card())
        self.empty = self.add(dim(_("Aucun point de restauration pour l'instant.")))
        self.space = self.add(dim(""))
        self.connect("map", lambda *_a: self.refresh())

    def update(self, state):
        running = bool(state.get("pid"))
        if running != self.running:
            self.running = running
            self.show_points(self.items)

    def refresh(self):
        def work():
            try:
                items = restore.points(with_sizes=True)
                extra = slim_point()
                if extra:
                    items = sorted(items + [extra], key=lambda p: p["created"], reverse=True)
                err = None
            except restore.RestoreError as e:
                items, err = [], str(e)
            free = restore.free_bytes()
            GLib.idle_add(self.show_points, items, free, err)
        threading.Thread(target=work, name="vasistas-restore-list", daemon=True).start()

    def show_points(self, items, free=None, err=None):
        self.items = items
        clear(self.list)
        self.revealers = {}
        if self.working and self.working[0] == NEW:
            self.list.append(self.point_row({"tag": NEW, "auto": False, "label": self.working[1],
                                             "created": time.time()}))
        for p in items:
            self.list.append(self.point_row(p))
        self.list.set_visible(bool(items) or bool(self.working))
        self.empty.set_visible(not items and not self.working)
        if err:
            self.empty.set_label(_("Lecture impossible : {e}", e=err))
        if free is not None:
            gb = free / 1e9
            text = _("{n} Go libres sur le disque.", n=f"{gb:.0f}")
            if gb < restore.MIN_FREE_GB:
                text += " " + _("Moins de {n} Go libres : impossible de créer un nouveau point de restauration.",
                                n=restore.MIN_FREE_GB)
            self.space.set_label(text)
        self.create_btn.set_sensitive(not self.busy and vm.DISK.exists())
        return False

    def point_row(self, p):
        box = Gtk.Box(spacing=12, margin_start=6, margin_end=6, margin_top=4, margin_bottom=4)
        box.append(Gtk.Image(icon_name="document-revert" if p["auto"] else "document-save", pixel_size=32,
                             valign=Gtk.Align.CENTER))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        texts.append(Gtk.Label(label=p["label"], xalign=0, wrap=True))
        bits = [_("créé avant l'allègement") if p.get("slim") else
                _("automatique") if p["auto"] else _("créé manuellement")]
        if p.get("name") or p.get("slim"):
            bits.insert(0, restore.short_date(p["created"]))
        if p.get("size") is not None:
            bits.append(restore.fmt_size(p["size"]))
        if p["tag"] != NEW:
            texts.append(dim(" · ".join(bits)))
        box.append(texts)
        rev = Gtk.Revealer(child=box, reveal_child=True, transition_type=Gtk.RevealerTransitionType.SLIDE_UP,
                           transition_duration=300)
        self.revealers[p["tag"]] = rev
        if self.working and self.working[0] == p["tag"]:
            # opération en cours sur ce point : une roue à la place des boutons
            if p["tag"] != NEW:
                box.append(dim(self.working[1]))
            box.append(Gtk.Spinner(spinning=True, valign=Gtk.Align.CENTER, width_request=24, height_request=24))
            return Gtk.ListBoxRow(activatable=False, child=rev)
        back = Gtk.Button(label=_("Revenir…"), valign=Gtk.Align.CENTER,
                          tooltip_text=_("Remettre Windows dans cet état"))
        back.set_sensitive(not self.busy)
        back.connect("clicked", lambda *_a: self.confirm_revert(p))
        drop = Gtk.Button(icon_name="edit-delete-symbolic", valign=Gtk.Align.CENTER,
                          tooltip_text=_("Supprimer ce point de restauration et libérer l'espace disque qu'il "
                                         "occupe"))
        drop.add_css_class(Granite.STYLE_CLASS_FLAT)
        drop.set_sensitive(not self.busy)
        drop.connect("clicked", lambda *_a: self.confirm_delete(p))
        box.append(back)
        box.append(drop)
        return Gtk.ListBoxRow(activatable=False, child=rev)

    # -- actions --

    def _work(self, message, fn, done, tag=None, text=None, collapse=False):
        """fn() hors du fil GTK ; done(résultat ou exception) dans le fil GTK. `tag` : le point (ou NEW)
        dont la ligne montre une roue et `text` pendant l'opération."""
        self.busy = True
        self.working = (tag, text) if tag else None
        self.collapse = collapse  # la ligne se replie une fois l'opération réussie (suppression)
        self.show_points(self.items)
        self.win.notify(message)

        def run():
            try:
                res = fn()
            except Exception as e:  # noqa: BLE001 - affiché à l'utilisateur
                res = e
            GLib.idle_add(self._done, res, done)
        threading.Thread(target=run, name="vasistas-restore", daemon=True).start()

    def _done(self, res, done):
        self.busy = False
        tag = self.working and self.working[0]
        self.working = None
        done(res)
        rev = self.revealers.get(tag)
        if isinstance(res, Exception) or rev is None or not self.collapse:
            self.refresh()
        else:
            # point supprimé : sa ligne se replie avant la nouvelle liste
            rev.set_reveal_child(False)
            GLib.timeout_add(rev.get_transition_duration() + 50, lambda: self.refresh() and False)
        return False

    def create(self, stop_first=False):
        if self.busy:
            return
        name = self.name.get_text().strip() or None

        def fn():
            if stop_first:
                vm.stop()
            return restore.create(name=name)

        def done(res):
            if isinstance(res, restore.NeedsStop):
                confirm(self.win, _("Arrêter Windows pour créer le point de restauration ?"),
                        _("Windows ne répond pas : le point de restauration ne peut pas être créé tant que Windows "
                          "est en marche. Les documents ouverts dans Windows et non enregistrés seront perdus."),
                        _("Arrêter et créer"), lambda: self.create(stop_first=True))
            elif isinstance(res, Exception):
                self.win.notify(_("Échec : {e}", e=res))
            else:
                self.name.set_text("")
                self.win.notify(_("Point créé : {label}", label=res["label"]))
        self._work(_("Création du point de restauration…"), fn, done, NEW, _("Création du point…"))

    def _revert(self, p):
        """Remet le disque dans l'état du point, après avoir arrêté Windows s'il tourne."""
        vm.stop()
        if p.get("slim"):
            slim.rollback()
            slim.CACHE.unlink(missing_ok=True)
        else:
            restore.revert(p["tag"])

    def confirm_revert(self, p):
        ago = time.time() - p["created"]
        when = _("il y a {n} jour(s)", n=int(ago // 86400)) if ago >= 86400 else _("aujourd'hui")
        detail = _("Windows revient à l'état du {date} ({when}). Les modifications faites dans Windows depuis "
                   "cette date sont perdues : applications installées, mises à jour, réglages, et fichiers "
                   "enregistrés ailleurs que dans les dossiers partagés.",
                   date=restore.short_date(p["created"]), when=when)
        if vm.pid():
            detail += "\n\n" + _("Vasistas arrête d'abord Windows : enregistrez les documents ouverts dans "
                                 "Windows.")
        newer = [x for x in self.items if x["created"] > p["created"]]
        if newer:
            detail += "\n\n" + _("Les {n} point(s) de restauration plus récent(s) restent disponibles.", n=len(newer))
        confirm(self.win, _("Revenir au point « {label} » ?", label=p["label"]), detail, _("Revenir à ce point"),
                lambda: self._work(_("Retour au point de restauration…"), lambda: self._revert(p),
                                   lambda res: self.win.notify(
                                       _("Échec : {e}", e=res) if isinstance(res, Exception)
                                       else _("Windows est revenu au point « {label} »", label=p["label"])),
                                   p["tag"], _("Retour en cours…")))

    def confirm_delete(self, p):
        confirm(self.win, _("Supprimer le point « {label} » ?", label=p["label"]),
                _("Windows ne pourra plus revenir à cet état. L'espace disque occupé par ce point de restauration "
                  "est rendu à Linux."),
                _("Supprimer"),
                lambda: self._work(_("Suppression du point…"),
                                   slim.drop_snapshot if p.get("slim") else lambda: restore.delete(p["tag"]),
                                   lambda res: self.win.notify(
                                       _("Échec : {e}", e=res) if isinstance(res, Exception)
                                       else _("Point de restauration supprimé")),
                                   p["tag"], _("Suppression…"), collapse=True))


class RestoreAutoSection(Section):
    """Points créés d'eux-mêmes avant les changements importants."""
    __gtype_name__ = "VasistasRestoreAutoSection"

    def __init__(self, win):
        super().__init__("document-revert", _("Points automatiques"), "")
        self.header(_("Points automatiques"))
        s = restore.settings()
        auto = Gtk.Switch(active=s["auto"])
        auto.connect("notify::active", self.on_auto)
        self.add(row(_("Avant les changements importants"),
                     _("Vasistas crée un point de restauration avant Windows Update et avant l'installation d'une "
                       "application."), auto))
        keep = Gtk.SpinButton.new_with_range(1, 10, 1)
        keep.set_value(s["keep"])
        keep.connect("value-changed", self.on_keep)
        self.add(row(_("Nombre de points automatiques conservés"),
                     _("Passé ce quota, les anciens points de restauration automatiques sont supprimés "
                       "automatiquement. Les points de restauration créés manuellement ne sont jamais supprimés "
                       "automatiquement."), keep))

    def update(self, state):
        pass

    def on_auto(self, sw, _p):
        c = vm.load_config()
        c["restore_auto"] = sw.get_active()
        vm.save_config(c)

    def on_keep(self, spin):
        c = vm.load_config()
        c["restore_keep"] = int(spin.get_value())
        vm.save_config(c)


class RestorationPage(ColumnsPage):
    __gtype_name__ = "VasistasRestorationPage"

    def __init__(self, win):
        super().__init__("document-revert", _("Restauration"),
                         _("Remettre Windows dans un état antérieur s'il ne fonctionne plus correctement après une "
                           "modification."),
                         [("restore", RestorePage(win))], [("restore-auto", RestoreAutoSection(win))])
