"""Accueil du compagnon, en tableau de bord : état de Windows et ses commandes, applications
ouvertes avec leur mode d'affichage, raccourcis ; à droite l'écran en direct et le niveau de
performance, réglable en un clic."""

import os
import threading
import time

from gi.repository import GLib, Granite, Gtk

from . import control, desktop, power, vm, winctl
from .companion_common import badge, card, clear, columns, dash_card, dim, mode_chip
from .companion_screens import ask_host
from .i18n import N_, _
from .winctl import APP_ID

CLOCK_TICKS = os.sysconf("SC_CLK_TCK")


def qemu_usage(pid, previous=None):
    """Processeur (en % d'un cœur, depuis la mesure précédente) et mémoire réelle de QEMU."""
    try:
        with open(f"/proc/{pid}/stat") as f:
            fields = f.read().rsplit(")", 1)[1].split()
        cpu = int(fields[11]) + int(fields[12])   # utime + stime, en tops d'horloge
        with open(f"/proc/{pid}/status") as f:
            rss = next((int(line.split()[1]) for line in f if line.startswith("VmRSS:")), 0)
    except (OSError, ValueError, IndexError, StopIteration):
        return None
    now = time.monotonic()
    pct = None
    if previous and previous[0] == pid and now > previous[2]:
        pct = 100 * (cpu - previous[1]) / CLOCK_TICKS / (now - previous[2])
    return {"sample": (pid, cpu, now), "cpu": pct, "rss_mb": rss // 1024}


def phase(state):
    """« stopped », « starting », « ready », « asleep »."""
    host = state.get("host") or {}
    if not state.get("pid"):
        return "stopped"
    if host.get("paused"):
        return "asleep"
    return "ready" if host.get("guest_ready") else "starting"


TITLES = {
    "stopped": (N_("Windows est arrêté"), N_("Windows démarre automatiquement à "
                                             "l'ouverture d'une application Windows.")),
    "starting": (N_("Windows démarre…"), N_("Les applications demandées s'ouvriront dès que Windows sera prêt.")),
    "ready": (N_("Windows est prêt"), N_("Les applications Windows s'ouvrent immédiatement.")),
    "asleep": (N_("Windows est en veille"), N_("Windows sort de veille en une seconde à l'ouverture de la prochaine "
                                               "application Windows.")),
}


ICONS = {"stopped": "system-shutdown-symbolic", "starting": "content-loading-symbolic",
         "ready": "emblem-ok-symbolic", "asleep": "weather-clear-night-symbolic"}


class HomePage(Gtk.Box):
    __gtype_name__ = "VasistasHomePage"

    def __init__(self, win):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.win = win
        self.shown_phase = None
        self.last_windows = None
        self.busy = False
        self.syncing = False
        page, outer, left, right = columns()
        self.append(page)
        outer.prepend(self.alerts_card())
        left.append(self.windows_card())
        left.append(self.apps_card())
        left.append(self.shortcuts_card())
        from .companion_display import LivePanel
        self.live = LivePanel(compact=True)
        right.append(self.live)
        right.append(self.perf_card())

    def add_help(self, on_click):
        btn = Gtk.Button(label="?", valign=Gtk.Align.CENTER, tooltip_text=_("Aide sur cette page"))
        btn.add_css_class(Granite.STYLE_CLASS_CIRCULAR)
        btn.connect("clicked", lambda *_a: on_click())
        self.win_head.append(btn)
        return btn

    # -- alertes : ce qui demande une action --

    def alerts_card(self):
        self.alerts = Gtk.Box(spacing=12, visible=False)
        for c in (Granite.STYLE_CLASS_CARD, Granite.STYLE_CLASS_ROUNDED, "dash-card"):
            self.alerts.add_css_class(c)
        self.alerts.append(Gtk.Image(icon_name="dialog-information-symbolic", valign=Gtk.Align.CENTER))
        self.alert_text = Gtk.Label(xalign=0, wrap=True, hexpand=True)
        self.alerts.append(self.alert_text)
        btn = Gtk.Button(label=_("Mettre à jour Windows"), valign=Gtk.Align.CENTER)
        btn.connect("clicked", lambda *_a: self.windows_update())
        self.alerts.append(btn)
        return self.alerts

    def show_alerts(self, ready):
        last = vm.load_config().get("last_windows_update")
        days = int((time.time() - last) / 86400) if last else None
        late = ready and (days is None or days > 30)
        if late:
            self.alert_text.set_label(_("Windows n'a pas été mis à jour depuis plus d'un mois.") if days else
                                      _("Les mises à jour de Windows n'ont jamais été lancées depuis Vasistas."))
        self.alerts.set_visible(late)

    # -- Windows --

    def windows_card(self):
        box, self.win_head = dash_card("Windows", "computer-symbolic", "blue")
        line = Gtk.Box(spacing=16)
        self.icon = Gtk.Image(icon_name="system-shutdown-symbolic", pixel_size=48)
        line.append(self.icon)
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        self.title = Gtk.Label(xalign=0)
        self.title.add_css_class(Granite.STYLE_CLASS_H2_LABEL)
        self.subtitle = dim("")
        texts.append(self.title)
        texts.append(self.subtitle)
        line.append(texts)
        box.append(line)
        self.buttons = Gtk.Box(spacing=6, halign=Gtk.Align.END)
        box.append(self.buttons)
        return box

    def iter_buttons(self):
        b = self.buttons.get_first_child()
        while b:
            yield b
            b = b.get_next_sibling()

    def show_phase(self, p):
        if p == self.shown_phase:
            return
        self.shown_phase = p
        title, sub = TITLES[p]
        self.title.set_label(_(title))
        self.subtitle.set_label(_(sub))
        self.icon.set_from_icon_name(ICONS[p])
        clear(self.buttons)
        # une action principale ; les autres dans le menu ⋯
        primary, others = {
            "stopped": ((_("Démarrer"), winctl.start), []),
            "asleep": ((_("Réveiller"), winctl.wake), [(_("Arrêter"), winctl.stop)]),
            "ready": ((_("Mettre en veille"), winctl.sleep),
                      [(_("Redémarrer"), winctl.restart), (_("Arrêter"), winctl.stop)]),
            "starting": (None, [(_("Arrêter"), winctl.stop)]),
        }[p]
        if others:
            pop = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, margin_top=4, margin_bottom=4)
            more = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text=_("Autres actions"),
                                  popover=Gtk.Popover(child=pop))
            for label, fn in others:
                item = Gtk.Button(label=label)
                item.add_css_class(Granite.STYLE_CLASS_FLAT)
                item.get_child().set_xalign(0)
                self.wire(item, fn, more.popdown)
                pop.append(item)
            self.buttons.append(more)
        if primary:
            btn = Gtk.Button(label=primary[0])
            btn.add_css_class(Granite.STYLE_CLASS_SUGGESTED_ACTION)
            self.wire(btn, primary[1])
            self.buttons.append(btn)

    def wire(self, btn, fn, before=None):
        def run(_b):
            if before:
                before()
            for b in list(self.iter_buttons()):
                b.set_sensitive(False)

            def work():
                try:
                    fn()
                except (OSError, SystemExit, Exception) as e:  # noqa: BLE001 : message à l'utilisateur
                    GLib.idle_add(self.win.notify, str(e) or _("Échec"))
                GLib.idle_add(self.done_action)
            threading.Thread(target=work, daemon=True).start()
        btn.connect("clicked", run)

    def done_action(self):
        self.shown_phase = None  # boutons refaits au prochain état
        self.win.poll()
        return False

    # -- applications ouvertes --

    def apps_card(self):
        box, _head = dash_card(_("Applications ouvertes"), "view-app-grid-symbolic", "purple")
        self.windows_list = card()
        box.append(self.windows_list)
        return box

    def refresh_windows(self, ready):
        if not ready:
            self.show_windows([])
            return
        if self.busy:
            return
        self.busy = True

        def work():
            try:
                wins = control.request({"windows": True}, timeout=1).get("windows") or []
            except (OSError, ValueError):
                wins = None
            GLib.idle_add(self._windows_done, wins)
        threading.Thread(target=work, daemon=True).start()

    def _windows_done(self, wins):
        self.busy = False
        if wins is not None:
            self.show_windows(wins)
        return False

    def show_windows(self, wins):
        main = [w for w in wins if w.get("kind") != "popup" and not w.get("owner")]
        modes = power.app_modes(vm.load_config())
        key = [(w.get("id"), w.get("title"), w.get("app")) for w in main] + [sorted(modes.items())]
        if key == self.last_windows:
            return
        self.last_windows = key
        clear(self.windows_list)
        if not main:
            self.windows_list.append(Gtk.Label(label=_("Aucune application Windows ouverte."),
                                               margin_top=14, margin_bottom=14))
            return
        reg = desktop.load_registry()
        for w in main:
            app = w.get("app") or ""
            line = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
            icon = Gtk.Image(pixel_size=32)
            if app and desktop.app_icon_path(app).exists():
                icon.set_from_file(str(desktop.app_icon_path(app)))
            else:
                icon.set_from_icon_name(APP_ID)
            line.append(icon)
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
            texts.append(Gtk.Label(label=w.get("title") or app, xalign=0, ellipsize=3))
            texts.append(dim((reg.get(app) or {}).get("name") or app))
            line.append(texts)
            rule = modes.get(power.canonical(app))
            chip = mode_chip(rule, _(next(m[1] for m in power.MODES if m[0] == rule)) if rule else _("Par défaut"))
            chip.set_tooltip_text(_("Mode d'affichage de cette application"))
            line.append(chip)
            self.windows_list.append(line)

    # -- raccourcis --

    def shortcuts_card(self):
        box, _head = dash_card(_("Raccourcis"), "starred-symbolic", "amber")
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=2, min_children_per_line=2,
                           column_spacing=8, row_spacing=8, homogeneous=True)
        for icon, color, label, action in (
                ("system-software-install-symbolic", "purple", _("Installer une application"),
                 lambda: self.go("install")),
                ("folder-remote-symbolic", "blue", _("Partager un dossier"), lambda: self.go("folders")),
                ("software-update-available-symbolic", "green", _("Mettre à jour Windows"), self.windows_update),
                ("view-restore-symbolic", "teal", _("Réinitialiser l'affichage"), self.reset)):
            btn = Gtk.Button()
            inner = Gtk.Box(spacing=10, margin_top=2, margin_bottom=2)
            inner.append(badge(icon, color, small=True))
            inner.append(Gtk.Label(label=label, wrap=True, xalign=0))
            btn.set_child(inner)
            btn.connect("clicked", lambda _b, a=action: a())
            flow.append(btn)
        box.append(flow)
        return box

    def windows_update(self):
        general = self.win.show_page("general")
        if general is not None:
            general.windows_update()

    def go(self, name):
        if self.win.show_page(name) is None:
            self.win.notify(_("Cette page n'est pas disponible."))

    def reset(self):
        def done(res):
            if res is None:
                self.win.notify(_("Aucune fenêtre Windows ouverte."))
            else:
                self.win.notify(res.get("error") or _("{n} fenêtre(s) remise(s) en place.", n=res.get("count", 0)))
        ask_host({"reset_windows": True}, done)

    # -- performances --

    def perf_card(self):
        box, head = dash_card(_("Performances"), "power-profile-performance-symbolic", "green")
        self.machine_scale, self.machine_hint = self.slider(
            box, _("Performance de la machine"), [_(m[1]) for m in power.MACHINE], self.set_machine)
        self.mode_scale, self.mode_hint = self.slider(
            box, _("Performance d'affichage"), [_(m[1]) for m in power.MODES], self.set_mode)
        # l'explication du réglage machine passe en info-bulle : l'Accueil tient sans défiler
        self.machine_hint.set_visible(False)
        more = Gtk.Button(label=_("Réglages de l'affichage"), valign=Gtk.Align.CENTER)
        more.connect("clicked", lambda *_a: self.go("display"))
        head.append(more)
        return box

    def slider(self, box, title, marks, on_change):
        label = Gtk.Label(label=title, xalign=0, margin_top=4)
        label.add_css_class("tile-value")
        box.append(label)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, len(marks) - 1, 1)
        scale.add_css_class("modes")
        scale.set_draw_value(False)
        scale.set_round_digits(0)
        scale.set_margin_start(10)
        scale.set_margin_end(10)
        for i, mark in enumerate(marks):
            scale.add_mark(i, Gtk.PositionType.BOTTOM, mark)
        scale.connect("value-changed", lambda sc: self.syncing or on_change(round(sc.get_value())))
        box.append(scale)
        hint = dim("")
        box.append(hint)
        return scale, hint

    def set_machine(self, i):
        cfg = vm.load_config()
        key = power.MACHINE[i][0]
        if cfg.get("resources") == key:
            return
        winctl.set_resources(key)
        if vm.pid():
            self.win.notify(_("Prend effet au prochain démarrage de Windows."), _("Redémarrer"),
                            self.win.restart_windows)

    def set_mode(self, i):
        cfg = vm.load_config()
        if power.mode_index(cfg) == i:
            return
        restart = power.apply_mode(cfg, i)
        vm.save_config(cfg)
        if restart and vm.pid():
            self.win.notify(_("Mode {mode} sélectionné. Redémarrez Windows pour appliquer tous les réglages "
                              "de ce mode.",
                              mode=_(power.MODES[i][1])), _("Redémarrer"), self.win.restart_windows)

    def show_perf(self, state):
        cfg = vm.load_config()
        mi = power.machine_index(cfg)
        i = power.mode_index(cfg)
        p = ((state.get("host") or {}).get("power") or {})
        self.machine_hint.set_label(_(power.MACHINE[mi][2]))
        self.machine_scale.set_tooltip_text(_(power.MACHINE[mi][2]))
        if p.get("rule"):
            name = (desktop.load_registry().get(p.get("app")) or {}).get("name") or p.get("app") or ""
            self.mode_hint.set_label(_("En ce moment : mode {mode}, défini pour l'application {app}.", app=name,
                                       mode=_(next(m[1] for m in power.MODES if m[0] == p["rule"]))))
        elif i is None:
            self.mode_hint.set_label(_("Des réglages fins ont été modifiés dans les options avancées de la page "
                                       "Affichage."))
        else:
            self.mode_hint.set_label(_(power.MODES[i][2]))
        self.syncing = True
        try:
            self.machine_scale.set_value(mi)
            if i is not None:
                self.mode_scale.set_value(i)
        finally:
            self.syncing = False

    def update(self, state):
        p = phase(state)
        self.show_phase(p)
        self.refresh_windows(p == "ready")
        self.show_alerts(p == "ready")
        self.live.refresh(state)
        self.show_perf(state)
