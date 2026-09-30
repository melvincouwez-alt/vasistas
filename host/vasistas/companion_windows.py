"""Pages « Windows » (état, démarrage, maintenance) et « Performances » (puissance, veille,
carte graphique) de l'application compagnon."""

import os
import threading
from pathlib import Path

from gi.repository import Gio, GLib, Granite, Gtk

from . import control, desktop, vm
from .app import APP_ID
from .companion_common import RESOURCES, Page, dim, guest_ready, host_ready, row, spawn

AUTOSTART = os.path.expanduser(f"~/.config/autostart/{APP_ID}.autostart.desktop")
CONFIGURE = vm.INSTALL_DIR / "configure-windows.ps1"
STATES = {
    "start": "Démarrage de Windows…", "stop": "Arrêt de Windows…", "restart": "Redémarrage de Windows…",
}


class WindowsPage(Page):
    """État de la machine virtuelle et ce qui s'y fait rarement : démarrage automatique,
    mises à jour de Windows, reconfiguration."""
    __gtype_name__ = "VasistasWindowsPage"

    def __init__(self, win):
        super().__init__(APP_ID, "Windows", "")
        self.win = win
        self.busy = None  # « start », « stop », « restart »
        self.state = {}

        actions = self.get_action_area()
        self.sleep_btn = Gtk.Button(label="Mettre en veille")
        self.sleep_btn.connect("clicked", lambda *_: self.sleep_now())
        self.restart_btn = Gtk.Button(label="Redémarrer")
        self.restart_btn.connect("clicked", lambda *_: self.vm_action("restart"))
        self.stop_btn = Gtk.Button(label="Arrêter")
        self.stop_btn.connect("clicked", lambda *_: self.vm_action("stop"))
        self.start_btn = Gtk.Button(label="Démarrer")
        self.start_btn.add_css_class(Granite.STYLE_CLASS_SUGGESTED_ACTION)
        self.start_btn.connect("clicked", lambda *_: self.vm_action("start"))
        for b in (self.sleep_btn, self.restart_btn, self.stop_btn, self.start_btn):
            actions.append(b)

        self.header("Démarrage")
        auto = Gtk.Switch(active=os.path.exists(AUTOSTART))
        auto.connect("notify::active", self.on_autostart)
        self.add(row("Démarrer Windows à l'ouverture de session",
                     "Les applications s'ouvrent ensuite sans attendre le démarrage de Windows.", auto))

        self.header("Son")
        snd = Gtk.Switch(active=vm.load_config().get("sound", True) is not False)

        def on_snd(sw, _p):
            c = vm.load_config()
            c["sound"] = sw.get_active()
            vm.save_config(c)
        snd.connect("notify::active", on_snd)
        self.add(row("Son de Windows",
                     "Haut-parleurs et microphone, par PipeWire. Le microphone n'est lu que lorsqu'une "
                     "application de Windows l'ouvre. S'applique au prochain démarrage de Windows.", snd))

        self.header("Apparence")
        tb = Gtk.Switch(active=bool(vm.load_config().get("native_titlebar", False)))

        def on_tb(sw, _p):
            c = vm.load_config()
            c["native_titlebar"] = sw.get_active()
            vm.save_config(c)
        tb.connect("notify::active", on_tb)
        self.add(row("Barre de titre du bureau (expérimental)",
                     "Les fenêtres dont Windows dessine la barre de titre (Explorateur, Bloc-notes classique, "
                     "boîtes de dialogue…) reçoivent celle du bureau, avec ses coins arrondis et son ombre. "
                     "Office, Edge et les applications qui dessinent leur propre barre gardent la leur. "
                     "S'applique aussi aux fenêtres déjà ouvertes.", tb))

        self.header("Maintenance")
        self.wu_btn = Gtk.Button(label="Ouvrir Windows Update")
        self.wu_btn.connect("clicked", lambda *_: self.windows_update())
        self.add(row("Mises à jour de Windows",
                     "Windows n'installe pas de mises à jour pendant que vous travaillez : lancez-les "
                     "environ une fois par mois.", self.wu_btn))
        self.wu_label = self.add(dim(""))
        self.show_wu()
        self.configure_btn = Gtk.Button(label="Reconfigurer")
        self.configure_btn.connect("clicked", lambda *_: self.configure())
        self.add(row("Configuration de Windows pour Vasistas",
                     "Réinstalle l'agent, les pilotes et les réglages nécessaires (dossiers partagés, "
                     "pas d'écran de verrouillage…) sans toucher à vos applications ni à vos fichiers.",
                     self.configure_btn))
        self.configure_btn.set_visible(CONFIGURE.exists())
        wiz = Gtk.Button(label="Ouvrir l'assistant")
        wiz.connect("clicked", lambda *_: self.win.get_application().open_wizard())
        self.add(row("Assistant de configuration",
                     "Installer Windows, ses pilotes et les applications, étape par étape.", wiz))
        self.update({})

    def on_autostart(self, sw, _p):
        if sw.get_active():
            os.makedirs(os.path.dirname(AUTOSTART), exist_ok=True)
            with open(AUTOSTART, "w") as f:
                f.write("[Desktop Entry]\nType=Application\nName=Vasistas\n"
                        f"Exec={desktop._launcher()} boot\nIcon={APP_ID}\nNoDisplay=true\n"
                        "X-GNOME-Autostart-enabled=true\n")
        elif os.path.exists(AUTOSTART):
            os.unlink(AUTOSTART)

    def update(self, state):
        self.state = state
        running = bool(state.get("pid"))
        host = state.get("host") or {}
        ready = bool(host.get("guest_ready"))
        if self.busy == "start" and ready:
            self.busy = None
        if self.busy == "stop" and not running:
            self.busy = None
        if self.busy:
            text, kind = STATES[self.busy], Granite.SettingsPageStatusType.WARNING
            status = "Patientez…"
        elif not running:
            text, kind, status = "Windows est arrêté. Il démarre de lui-même quand vous ouvrez une " \
                                 "application Windows.", Granite.SettingsPageStatusType.OFFLINE, "Arrêté"
        elif not ready:
            text, kind, status = "Démarrage de Windows…", Granite.SettingsPageStatusType.WARNING, "Démarrage"
        elif host.get("paused"):
            text = "En veille : Windows se réveille au premier clic dans l'une de ses fenêtres."
            kind, status = Granite.SettingsPageStatusType.WARNING, "En veille"
        else:
            n = host.get("windows", 0)
            text = "En marche, " + ("aucune fenêtre ouverte." if not n else
                                    "1 fenêtre ouverte." if n == 1 else f"{n} fenêtres ouvertes.")
            kind, status = Granite.SettingsPageStatusType.SUCCESS, "En marche"
        self.set_description(text)
        self.set_status(status)
        self.set_status_type(kind)
        self.start_btn.set_visible(not running and not self.busy)
        self.stop_btn.set_visible(running and not self.busy)
        self.restart_btn.set_visible(running and not self.busy)
        self.sleep_btn.set_visible(ready and not host.get("paused") and not self.busy)
        self.configure_btn.set_sensitive(ready)
        self.wu_btn.set_sensitive(ready)

    def vm_action(self, action):
        self.busy = action
        self.update(self.state)

        def work():
            try:
                if action in ("stop", "restart"):
                    vm.stop()
                if action in ("start", "restart"):
                    if host_ready() is None:
                        spawn("run")
                    vm.start()
                    if action == "restart":
                        GLib.idle_add(setattr, self, "busy", "start")
            except Exception as e:  # noqa: BLE001 - affiché à l'utilisateur
                GLib.idle_add(self.win.notify, f"Échec : {e}")
                GLib.idle_add(setattr, self, "busy", None)
        threading.Thread(target=work, daemon=True).start()

    def sleep_now(self):
        def work():
            try:
                control.request({"sleep": True}, timeout=5)
            except OSError as e:
                GLib.idle_add(self.win.notify, f"Échec : {e}")
            self.win.poll()
        threading.Thread(target=work, daemon=True).start()

    def show_wu(self):
        import time
        t = vm.load_config().get("last_windows_update")
        if not t:
            self.wu_label.set_label("Jamais lancées depuis Vasistas.")
            return
        days = int((time.time() - t) / 86400)
        txt = "Dernière fois : aujourd'hui." if days == 0 else \
            f"Dernière fois : il y a {days} jour{'s' if days > 1 else ''}."
        if days > 30:
            txt += " Plus d'un mois : pensez à les lancer."
        self.wu_label.set_label(txt)

    def windows_update(self):
        if not guest_ready(self.state):
            self.win.notify("Démarrez Windows d'abord")
            return
        from . import slim

        def work():
            try:
                slim.open_windows_update()
                GLib.idle_add(self.win.notify, "Windows Update est ouvert")
            except Exception as e:  # noqa: BLE001
                GLib.idle_add(self.win.notify, f"Échec : {e}")
            GLib.idle_add(self.show_wu)
        threading.Thread(target=work, daemon=True).start()

    def configure(self):
        self.configure_btn.set_sensitive(False)
        self.win.notify("Configuration de Windows en cours (quelques minutes)…")

        def work():
            try:
                res = control.request({"exec": CONFIGURE.read_text(encoding="utf-8-sig")}, timeout=1800)
                out = res.get("out") or ""
                msg = "Windows est configuré pour Vasistas" if res.get("code") == 0 else \
                    "Configuration incomplète : " + out.strip().splitlines()[-2][:160] \
                    if len(out.strip().splitlines()) > 1 else "Configuration incomplète"
                if "reboot\":true" in out.replace(" ", ""):
                    msg += ". Redémarrez Windows pour terminer"
            except OSError as e:
                msg = f"Échec : {e}"
            GLib.idle_add(self.win.notify, msg)
            GLib.idle_add(self.configure_btn.set_sensitive, True)
        threading.Thread(target=work, daemon=True).start()


class PerformancePage(Page):
    """Puissance donnée à Windows, mise en veille, carte graphique dédiée."""
    __gtype_name__ = "VasistasPerformancePage"

    def __init__(self, win):
        super().__init__("utilities-system-monitor", "Performances",
                         "Ce que Windows prend à l'ordinateur, et quand il le rend.")
        self.win = win
        self.resources_section()
        self.sleep_section()
        self.gpu_section()

    def resources_section(self):
        self.header("Puissance")
        keys = [k for k, _, _ in RESOURCES]
        cfg = vm.load_config()
        current = cfg.get("resources") if cfg.get("resources") in keys else "balanced"
        drop = Gtk.DropDown.new_from_strings([label for _, label, _ in RESOURCES])
        drop.set_selected(keys.index(current))
        hint = dim(RESOURCES[keys.index(current)][2] + ".")

        def on_res(d, _p):
            key = keys[d.get_selected()]
            c = vm.load_config()
            c["resources"] = key
            # pastille « Économie d'énergie » sur la carte d'ouverture des applications
            if key == "battery":
                c["mode"] = "battery"
            elif c.get("mode") == "battery":
                c.pop("mode")
            vm.save_config(c)
            hint.set_label(RESOURCES[d.get_selected()][2] + ". Appliqué au prochain démarrage de Windows.")
        drop.connect("notify::selected", on_res)
        self.add(row("Puissance allouée à Windows",
                     "La mémoire que Windows n'utilise pas est rendue à Linux au fil de l'eau.", drop))
        self.add(hint)

    def sleep_section(self):
        from . import sleep
        s = sleep.settings()
        self.header("Veille")
        pause = [(0, "Jamais"), (5, "5 minutes"), (10, "10 minutes"), (15, "15 minutes"), (30, "30 minutes"),
                 (60, "1 heure")]
        off = [(0, "Jamais"), (30, "30 minutes"), (60, "1 heure"), (120, "2 heures"), (240, "4 heures")]

        def minutes_drop(key, choices):
            values = [v for v, _ in choices]
            d = Gtk.DropDown.new_from_strings([t for _, t in choices])
            d.set_selected(values.index(s[key]) if s[key] in values else 0)

            def on(dd, _p):
                c = vm.load_config()
                c[key] = values[dd.get_selected()]
                vm.save_config(c)
            d.connect("notify::selected", on)
            return d
        self.add(row("Mettre Windows en veille après",
                     "Sans fenêtre Windows au premier plan ni clic dedans. Réveil immédiat au premier clic.",
                     minutes_drop("sleep_minutes", pause)))
        self.add(row("Mettre en veille sans fenêtre ouverte après",
                     "Aucune application Windows ouverte : Windows ne consomme plus rien jusqu'au "
                     "prochain lancement, qui le réveille en moins d'une seconde.",
                     minutes_drop("sleep_without_windows_minutes", pause)))
        self.add(row("Éteindre Windows après",
                     "Seulement si aucune fenêtre Windows n'est ouverte : la mémoire est rendue à Linux.",
                     minutes_drop("shutdown_minutes", off)))
        bat = Gtk.Switch(active=s["sleep_on_battery_only"])

        def on_bat(sw, _p):
            c = vm.load_config()
            c["sleep_on_battery_only"] = sw.get_active()
            vm.save_config(c)
        bat.connect("notify::active", on_bat)
        self.add(row("Veille et arrêt seulement sur batterie",
                     "Sur secteur, Windows reste éveillé tant qu'une de ses fenêtres est ouverte.", bat))

    def gpu_section(self):
        self.header("Carte graphique")
        keys = ["off", "auto", "always"]
        labels = ["Jamais", "Automatique", "Dès que possible"]
        cfg = vm.load_config()
        drop = Gtk.DropDown.new_from_strings(labels)
        drop.set_selected(keys.index(cfg.get("gpu", "off")) if cfg.get("gpu", "off") in keys else 0)

        def on_gpu(d, _p):
            c = vm.load_config()
            c["gpu"] = keys[d.get_selected()]
            vm.save_config(c)
            self.update_gpu()
        drop.connect("notify::selected", on_gpu)
        self.add(row("Prêter la carte graphique dédiée à Windows",
                     "Pour les ordinateurs à deux cartes graphiques, quand la carte dédiée est libre. "
                     "Automatique : seulement sur secteur. Expérimental.", drop))
        self.gpu_label = dim("")
        self.add(self.gpu_label)
        self.gpu_install = Gtk.Button(label="Copier la commande d'installation", halign=Gtk.Align.START,
                                      visible=False)
        self.gpu_install.connect("clicked", lambda *_: self.copy(
            f"sudo {desktop.HOST_DIR / 'tools' / 'install-gpu-helper.sh'}",
            "Commande copiée : collez-la dans un terminal"))
        self.add(self.gpu_install)
        self.update_gpu()

    def update_gpu(self):
        def work():
            from . import gpu
            state, card_info = gpu.status()
            GLib.idle_add(self.show_gpu, state, card_info)
        threading.Thread(target=work, daemon=True).start()

    def show_gpu(self, state, card_info):
        from . import gpu
        text = gpu.MESSAGES[state]
        if card_info:
            text = f"{card_info['name'].split(' (rev')[0]} : {text[0].lower()}{text[1:]}"
        if state == "helper":
            text += " Il demande une installation unique avec sudo."
        if vm.gpu_active():
            text = "Carte graphique dédiée prêtée à Windows en ce moment."
        self.gpu_label.set_label(text)
        self.gpu_install.set_visible(state == "helper")
        return False

    def copy(self, text, message):
        self.get_display().get_clipboard().set(text)
        self.win.notify(message)
