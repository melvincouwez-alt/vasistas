"""Page « Windows » du compagnon : état de la machine virtuelle dans l'en-tête, configuration
simple de Windows en cartes (mises à jour, son, apparence, allègement) ; clavier
et imprimantes dans les options avancées. Les commandes (démarrer, arrêter…) sont sur l'Accueil."""

import os
import threading

from gi.repository import GLib, Granite, Gtk

from . import control, desktop, vm, winctl
from .companion_common import ColumnsPage, Section, advanced_card, dim, guest_ready, row
from .i18n import N_, _
from .winctl import APP_ID

AUTOSTART = os.path.expanduser(f"~/.config/autostart/{APP_ID}.autostart.desktop")
CONFIGURE = vm.INSTALL_DIR / "configure-windows.ps1"
STATES = {
    "start": N_("Démarrage de Windows…"), "stop": N_("Arrêt de Windows…"),
    "restart": N_("Redémarrage de Windows…"),
}


def autostart_text(launcher, sleep):
    """Lanceur de démarrage automatique : `boot`, ou `boot --sleep` (mis en veille une fois prêt)."""
    return ("[Desktop Entry]\nType=Application\nName=Vasistas\n"
            f"Exec={launcher} boot{' --sleep' if sleep else ''}\nIcon={APP_ID}\nNoDisplay=true\n"
            "X-GNOME-Autostart-enabled=true\n")


def write_autostart(sleep):
    os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
    with open(AUTOSTART, "w") as f:
        f.write(autostart_text(desktop._launcher(), sleep))


class GeneralSection(Section):
    """Mises à jour et son de Windows."""
    __gtype_name__ = "VasistasWindowsGeneralSection"

    def __init__(self, win):
        super().__init__(APP_ID, _("Général"), "")
        self.win = win
        self.state = {}
        self.header(_("Mises à jour de Windows"))
        self.wu_btn = Gtk.Button(label=_("Ouvrir Windows Update"))
        self.wu_btn.connect("clicked", lambda *_: self.windows_update())
        self.add(row(_("Lancer les mises à jour"),
                     _("Windows n'installe pas les mises à jour pendant que vous travaillez : lancez les mises à "
                       "jour de Windows environ une fois par mois."), self.wu_btn))
        self.wu_label = self.add(dim(""))
        self.show_wu()

        self.header(_("Son"))
        snd = Gtk.Switch(active=vm.load_config().get("sound", True) is not False)

        def on_snd(sw, _p):
            c = vm.load_config()
            c["sound"] = sw.get_active()
            vm.save_config(c)
        snd.connect("notify::active", on_snd)
        self.add(row(_("Son de Windows"),
                     _("Haut-parleurs et microphone. Le microphone n'est capté que lorsqu'une application Windows "
                       "l'utilise. Le changement s'applique au prochain démarrage de Windows."), snd))

    def update(self, state):
        self.state = state
        self.wu_btn.set_sensitive(guest_ready(state))

    def show_wu(self):
        import time
        t = vm.load_config().get("last_windows_update")
        if not t:
            self.wu_label.set_label(_("Mises à jour jamais lancées depuis Vasistas."))
            return
        days = int((time.time() - t) / 86400)
        txt = _("Dernière fois : aujourd'hui.") if days == 0 else \
            _("Dernière fois : il y a 1 jour.") if days == 1 else \
            _("Dernière fois : il y a {n} jours.", n=days)
        if days > 30:
            txt += " " + _("Les dernières mises à jour datent de plus d'un mois : lancez les mises à jour de "
                           "Windows.")
        self.wu_label.set_label(txt)

    def windows_update(self):
        if not guest_ready(self.state):
            self.win.notify(_("Démarrez Windows d'abord"))
            return
        from . import restore, slim
        if restore.settings()["auto"]:
            self.win.notify(_("Création d'un point de restauration avant la mise à jour…"))

        def work():
            # point de restauration automatique (option) ; sans lui, la mise à jour s'ouvre quand même
            _point, err = restore.auto_point("windows-update")
            try:
                slim.open_windows_update()
                GLib.idle_add(self.win.notify, _("Windows Update est ouvert, sans point de restauration : {e}",
                                                 e=err) if err else _("Windows Update est ouvert"))
            except Exception as e:  # noqa: BLE001
                GLib.idle_add(self.win.notify, _("Échec : {e}", e=e))
            GLib.idle_add(self.show_wu)
        threading.Thread(target=work, daemon=True).start()


class WindowsPage(ColumnsPage):
    """État de la machine virtuelle (en-tête) et configuration de Windows en deux colonnes."""
    __gtype_name__ = "VasistasWindowsPage"

    def __init__(self, win):
        from .companion_keyboard import KeyboardPage
        from .companion_printers import PrintersPage
        from .companion_screens import IntegrationPage
        from .companion_slim import SlimPage
        super().__init__(APP_ID, "Windows", "",
                         [("general", GeneralSection(win))],
                         [("slim", SlimPage(win)), ("integration", IntegrationPage(win))])
        self.win = win
        self.busy = None  # « start », « stop », « restart » (lancés par le diagnostic ou la bannière)
        self.state = {}
        extra = {"keyboard": KeyboardPage(win), "printers": PrintersPage(win)}
        self.sections.update(extra)
        self.left_column.append(advanced_card(*extra.values()))
        self.update({})

    def update(self, state):
        super().update(state)
        self.state = state
        running = bool(state.get("pid"))
        host = state.get("host") or {}
        ready = bool(host.get("guest_ready"))
        if self.busy == "start" and ready:
            self.busy = None
        if self.busy == "stop" and not running:
            self.busy = None
        if self.busy:
            text, kind = _(STATES[self.busy]), Granite.SettingsPageStatusType.WARNING
            status = _("Patientez…")
        elif not running:
            text, kind, status = _("Windows est arrêté. Windows démarre automatiquement quand vous ouvrez une "
                                   "application Windows."), Granite.SettingsPageStatusType.OFFLINE, _("Arrêté")
        elif not ready:
            text, kind, status = (_("Démarrage de Windows…"), Granite.SettingsPageStatusType.WARNING,
                                  _("Démarrage"))
        elif host.get("paused"):
            text = _("En veille : Windows sort automatiquement de veille au premier clic dans une fenêtre Windows.")
            kind, status = Granite.SettingsPageStatusType.WARNING, _("En veille")
        else:
            n = host.get("windows", 0)
            text = (_("En marche, aucune fenêtre ouverte.") if not n else
                    _("En marche, 1 fenêtre ouverte.") if n == 1 else
                    _("En marche, {n} fenêtres ouvertes.", n=n))
            kind, status = Granite.SettingsPageStatusType.SUCCESS, _("En marche")
        self.set_description(text)
        self.set_status(status)
        self.set_status_type(kind)

    def vm_action(self, action):
        self.busy = action
        self.update(self.state)

        def work():
            try:
                getattr(winctl, action)()
                if action == "restart":
                    GLib.idle_add(setattr, self, "busy", "start")
            except Exception as e:  # noqa: BLE001 - affiché à l'utilisateur
                GLib.idle_add(self.win.notify, _("Échec : {e}", e=e))
                GLib.idle_add(setattr, self, "busy", None)
        threading.Thread(target=work, daemon=True).start()

    def sleep_now(self):
        def work():
            try:
                winctl.sleep()
            except OSError as e:
                GLib.idle_add(self.win.notify, _("Échec : {e}", e=e))
            self.win.poll()
        threading.Thread(target=work, daemon=True).start()

    def configure(self):
        self.win.notify(_("Configuration de Windows en cours (quelques minutes)…"))

        def work():
            try:
                res = control.request({"exec": CONFIGURE.read_text(encoding="utf-8-sig")}, timeout=1800)
                out = res.get("out") or ""
                msg = _("Windows est configuré pour Vasistas") if res.get("code") == 0 else \
                    _("Configuration incomplète : {detail}", detail=out.strip().splitlines()[-2][:160]) \
                    if len(out.strip().splitlines()) > 1 else _("Configuration incomplète")
                if "reboot\":true" in out.replace(" ", ""):
                    msg += _(". Redémarrez Windows pour terminer")
            except OSError as e:
                msg = _("Échec : {e}", e=e)
            GLib.idle_add(self.win.notify, msg)
        threading.Thread(target=work, daemon=True).start()
