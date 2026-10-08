"""Page « Diagnostic » de l'application compagnon (voir diagnose.py)."""

import threading

from gi.repository import GLib, Gtk

from . import diagnose
from .companion_common import Section, card, clear, confirm, dim, spawn
from .i18n import _
from .version import ISSUES

ICONS = {diagnose.OK: "process-completed", diagnose.WARN: "dialog-warning", diagnose.ERROR: "dialog-error"}


class DiagnosePage(Section):
    __gtype_name__ = "VasistasDiagnosePage"

    def __init__(self, win):
        super().__init__("dialog-information", _("Diagnostic"),
                         _("Vasistas vérifie les composants dont il a besoin et propose une réparation lorsque c'est "
                           "possible. Le rapport ne contient ni votre nom, ni vos mots de passe, ni les noms de vos "
                           "fichiers."))
        self.win = win
        self.running = False
        self.results = []
        self.pending_fix = None

        self.rerun = Gtk.Button(label=_("Vérifier de nouveau"))
        self.rerun.connect("clicked", lambda *_a: self.run())
        self.copy_btn = Gtk.Button(label=_("Copier le rapport"))
        self.copy_btn.connect("clicked", lambda *_a: self.copy_report())
        self.issue_btn = Gtk.Button(label=_("Signaler un problème"))
        self.issue_btn.connect("clicked", lambda *_a: self.copy_report(open_issues=True))
        for b in (self.rerun, self.copy_btn, self.issue_btn):
            self.get_action_area().append(b)

        self.summary = self.add(Gtk.Label(xalign=0, wrap=True))
        self.list = self.add(card())
        self.add(dim(_("Le bouton « Signaler un problème » copie le rapport, puis ouvre la page de signalement des "
                       "problèmes : collez le rapport dans votre message.")))
        self.connect("map", lambda *_a: self.results or self.run())

    # -- vérifications --

    def run(self):
        if self.running:
            return
        self.running = True
        self.results = []
        clear(self.list)
        self.summary.set_label(_("Vérification en cours…"))
        self.sensitize()

        def work():
            diagnose.run_all(on_result=lambda c: GLib.idle_add(self.add_result, c))
            GLib.idle_add(self.finished)
        threading.Thread(target=work, name="vasistas-diagnose", daemon=True).start()

    def sensitize(self):
        for b in (self.rerun, self.copy_btn, self.issue_btn):
            b.set_sensitive(not self.running)

    def add_result(self, c):
        self.results.append(c)
        self.list.append(self.result_row(c))
        return False

    def finished(self):
        self.running = False
        self.sensitize()
        errors = sum(c.state == diagnose.ERROR for c in self.results)
        warns = sum(c.state == diagnose.WARN for c in self.results)
        if errors:
            text = _("{n} problème(s) à régler.", n=errors)
        elif warns:
            text = _("Tout fonctionne, {n} point(s) à surveiller.", n=warns)
        else:
            text = _("Toutes les vérifications sont réussies.")
        self.summary.set_label(text)
        if self.pending_fix:
            key, self.pending_fix = self.pending_fix, None
            self.run_fix(key)
        return False

    def result_row(self, c):
        box = Gtk.Box(spacing=12, margin_start=6, margin_end=6, margin_top=4, margin_bottom=4)
        box.append(Gtk.Image(icon_name=ICONS.get(c.state, "dialog-question"), pixel_size=24,
                             valign=Gtk.Align.CENTER))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        texts.append(Gtk.Label(label=c.title, xalign=0, wrap=True))
        texts.append(dim(c.detail))
        if c.command:
            cmd = dim(c.command)
            cmd.set_selectable(True)
            cmd.add_css_class("monospace")
            texts.append(cmd)
        box.append(texts)
        if c.command:
            b = Gtk.Button(label=_("Copier la commande"), valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda *_a: self.copy(c.command, _("Commande copiée : collez-la dans un terminal")))
            box.append(b)
        if c.fix and c.state != diagnose.OK:
            b = Gtk.Button(label=diagnose.fix_label(c.fix), valign=Gtk.Align.CENTER)
            b.connect("clicked", lambda *_a: self.run_fix(c.fix))
            box.append(b)
        return Gtk.ListBoxRow(activatable=False, child=box)

    # -- réparations --

    def run_fix(self, key):
        """Réparation `key` (diagnose.FIXES) ; aussi appelée par « vasistas companion --fix KEY »."""
        if self.running:
            self.pending_fix = key
            return
        app = self.win.get_application()
        pages = {"restore-page": "restore", "performance-page": "performance", "folders-page": "folders"}
        if key in pages:
            self.win.show_page(pages[key])
        elif key == "wizard":
            app.open_wizard()
        elif key == "updates":
            app.show_updates()
        elif key == "start-host":
            spawn("run")
            self.win.notify(_("Redémarrage de Vasistas…"))
            GLib.timeout_add_seconds(5, lambda: self.run() and False)
        elif key == "update-agent":
            windows = self.win.page("windows")
            if windows is not None and hasattr(windows, "configure"):
                windows.configure()
        elif key == "restart-vm":
            confirm(self.win, _("Redémarrer Windows ?"),
                    _("Les documents ouverts dans Windows et non enregistrés seront perdus."),
                    _("Redémarrer"), self._restart)
        elif key == "indicator":
            self._fix_indicator()
        else:
            fn = diagnose.FIXES.get(key, (None, None))[1]
            if fn is None:
                return
            self.win.notify(_("Réparation en cours…"))

            def work():
                try:
                    fn()
                    msg = _("Réparation terminée")
                except Exception as e:  # noqa: BLE001 - affiché à l'utilisateur
                    msg = _("Échec : {e}", e=e)
                GLib.idle_add(self.win.notify, msg)
                GLib.idle_add(self.run)
            threading.Thread(target=work, name="vasistas-fix", daemon=True).start()

    def _restart(self):
        windows = self.win.page("windows")
        if windows is not None and hasattr(windows, "vm_action"):
            windows.vm_action("restart")
        else:
            threading.Thread(target=diagnose.fix_restart_vm, daemon=True).start()
        self.win.notify(_("Redémarrage de Windows…"))

    def _fix_indicator(self):
        from . import indicator
        indicator.set_enabled(True)
        self.win.notify(_("L'indicateur du panneau démarrera automatiquement avec la session"))
        self.run()

    # -- rapport --

    def copy(self, text, message):
        self.get_display().get_clipboard().set(text)
        self.win.notify(message)

    def copy_report(self, open_issues=False):
        results = list(self.results)

        def work():
            text = diagnose.report(results or diagnose.run_all(network=False))
            GLib.idle_add(self._copied, text, open_issues)
        threading.Thread(target=work, name="vasistas-report", daemon=True).start()

    def _copied(self, text, open_issues):
        self.copy(text, _("Rapport copié : collez-le dans votre message") if open_issues
                  else _("Rapport copié"))
        if open_issues:
            Gtk.UriLauncher.new(ISSUES).launch(self.win, None, None)
        return False
