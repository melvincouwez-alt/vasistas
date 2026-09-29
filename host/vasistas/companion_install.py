"""Page « Installer » de l'application compagnon : Microsoft Office (outil de déploiement
Office) et autres applications Windows courantes (winget), voir catalog.py."""

import threading

from gi.repository import Gio, GLib, Granite, Gtk

from . import catalog, regional
from .companion_common import Page, card, clear, dim, guest_ready, row

MATCH_OS = catalog.MATCH_OS


class InstallPage(Page):
    __gtype_name__ = "VasistasInstallPage"

    def __init__(self, win):
        super().__init__("system-software-install", "Installer",
                         "Microsoft Office et d'autres applications Windows, téléchargées chez leur éditeur. "
                         "Une licence est nécessaire pour les logiciels payants.")
        self.win = win
        self.busy = False
        self.ready = False
        self.installed = {}      # clé -> installée ?
        self.checked_once = False
        self.app_widgets = {}    # clé -> (état, bouton, liste des versions)

        self.recheck = Gtk.Button(label="Relire l'état")
        self.recheck.set_tooltip_text("Lire dans Windows ce qui est déjà installé")
        self.recheck.connect("clicked", lambda *_: self.check())
        self.get_action_area().append(self.recheck)

        self.office_section()
        self.apps_section()
        self.connect("map", lambda *_: self.ready and not self.checked_once and self.check())

    # -- Office --

    def office_section(self):
        self.header("Microsoft Office")
        self.office_state = self.add(dim("État inconnu : cliquez sur « Relire l'état »."))

        self.products = catalog.OFFICE_PRODUCTS
        self.product = Gtk.DropDown.new_from_strings([p["label"] for p in self.products])
        self.product_note = dim("")
        self.product.connect("notify::selected", lambda *_: self.on_product())
        self.add(row("Offre", "Celle de votre abonnement ou de votre licence : une autre installe Office "
                     "mais son activation échoue.", self.product))
        self.add(self.product_note)

        self.channel = Gtk.DropDown()
        self.channel_hint = dim("")
        self.channel.connect("notify::selected", lambda *_: self.on_channel())
        self.add(row("Version", "Fréquence des nouveautés.", self.channel))
        self.add(self.channel_hint)

        # langue principale : langue de Windows ou une langue complète d'Office
        self.languages = [(MATCH_OS, "Langue de Windows")] + [(c, n) for c, n, full in catalog.OFFICE_LANGUAGES
                                                              if c != MATCH_OS and full]
        self.language = Gtk.DropDown.new_from_strings([n for _, n in self.languages])
        self.language.set_enable_search(True)
        wanted = catalog.language_for_locale(regional.linux_locale())
        codes = [c for c, _ in self.languages]
        self.language.set_selected(codes.index(wanted) if wanted in codes else 0)
        self.add(row("Langue", "Langue des menus d'Office.", self.language))

        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=5,
                           column_spacing=12, row_spacing=4, homogeneous=True)
        self.office_apps = {}
        for a in catalog.OFFICE_APPS:
            chk = Gtk.CheckButton(label=a["label"], active=not a["excluded"])
            self.office_apps[a["id"]] = chk
            flow.append(chk)
        exp = Gtk.Expander(label="Applications incluses", child=flow, margin_top=6)
        self.add(exp)

        self.addons = {}
        addon_box = Gtk.Box(spacing=18, margin_top=6)
        for key, a in catalog.ADDONS.items():
            chk = Gtk.CheckButton(label=a["label"])
            self.addons[key] = chk
            addon_box.append(chk)
        self.add(row("Compléments", "Licence Visio ou Project à part.", addon_box))

        self.office_btn = Gtk.Button(label="Installer Office", halign=Gtk.Align.END, margin_top=6)
        self.office_btn.add_css_class(Granite.STYLE_CLASS_SUGGESTED_ACTION)
        self.office_btn.connect("clicked", lambda *_: self.confirm_office())
        self.add(self.office_btn)
        self.product.set_selected(0)
        self.on_product()

    def on_product(self):
        p = self.products[self.product.get_selected()]
        self.product_note.set_label(p.get("note") or "")
        self.product_note.set_visible(bool(p.get("note")))
        self.channels = catalog.channel_choices(p["key"])
        self.channel.set_model(Gtk.StringList.new([label for _, label, _ in self.channels]))
        self.channel.set_selected(0)
        self.channel.set_sensitive(len(self.channels) > 1)
        self.on_channel()

    def on_channel(self):
        i = self.channel.get_selected()
        if 0 <= i < len(getattr(self, "channels", [])):
            self.channel_hint.set_label(self.channels[i][2].capitalize() + ".")

    def confirm_office(self):
        p = self.products[self.product.get_selected()]
        dlg = Gtk.AlertDialog(
            message=f"Installer {p['label']} dans Windows ?",
            detail="Le téléchargement et l'installation prennent 10 à 30 minutes, sans fenêtre à surveiller. "
                   "Office s'active à la première ouverture, avec votre compte ou votre clé.",
            buttons=["Annuler", "Installer"], cancel_button=0, default_button=1)

        def answered(d, res):
            try:
                if d.choose_finish(res) == 1:
                    self.install_office(p)
            except GLib.Error:
                pass
        dlg.choose(self.win, None, answered)

    def install_office(self, p):
        channel = self.channels[self.channel.get_selected()][0]
        language = self.languages[self.language.get_selected()][0]
        exclude = [i for i, chk in self.office_apps.items() if not chk.get_active()]
        addons = [k for k, chk in self.addons.items() if chk.get_active()]

        def work():
            res = catalog.install_office(p["key"], channel=channel, languages=[language], exclude=exclude,
                                         addons=addons)
            return (f"{p['label']} installé" if res.get("ok", res.get("code") == 0)
                    else f"Installation d'Office : code {res.get('code')}")
        self.run(f"Installation de {p['label']}… (10 à 30 minutes)", work, refresh=True)

    # -- autres applications --

    def apps_section(self):
        self.header("Autres applications")
        self.add(dim("Installées en silence par winget, le gestionnaire de paquets de Windows, depuis "
                     "le site de chaque éditeur. Elles apparaissent ensuite dans la page Menu Applications."))
        by_cat = {}
        for a in catalog.APPS:
            by_cat.setdefault(a["category"], []).append(a)
        for cat in catalog.CATEGORIES:
            if cat not in by_cat:
                continue
            label = Gtk.Label(label=cat, xalign=0, margin_top=12)
            label.add_css_class(Granite.STYLE_CLASS_H4_LABEL)
            self.add(label)
            lb = self.add(card())
            for a in by_cat[cat]:
                lb.append(self.app_row(a))

    def app_row(self, a):
        box = Gtk.Box(spacing=12, margin_start=6, margin_end=6, margin_top=4, margin_bottom=4)
        box.append(Gtk.Image(icon_name=a.get("icon") or "application-x-executable", pixel_size=32,
                             valign=Gtk.Align.CENTER))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        texts.append(Gtk.Label(label=a["label"], xalign=0))
        sub = a.get("description", "") + (f". {a['note']}" if a.get("note") else "")
        texts.append(dim(sub))
        box.append(texts)
        variants = catalog.variant_choices(a["key"])
        drop = None
        if len(variants) > 1:
            drop = Gtk.DropDown.new_from_strings([label for _, label in variants])
            drop.set_valign(Gtk.Align.CENTER)
            drop.set_tooltip_text("Version à installer")
            box.append(drop)
        state = Gtk.Label(label="", valign=Gtk.Align.CENTER)
        state.add_css_class(Granite.STYLE_CLASS_DIM_LABEL)
        state.add_css_class(Granite.STYLE_CLASS_SMALL_LABEL)
        box.append(state)
        btn = Gtk.Button(label="Installer", valign=Gtk.Align.CENTER)
        btn.connect("clicked", lambda *_: self.on_app(a, drop, variants))
        box.append(btn)
        self.app_widgets[a["key"]] = (state, btn, drop)
        return Gtk.ListBoxRow(activatable=False, child=box)

    def on_app(self, a, drop, variants):
        key = a["key"]
        if self.installed.get(key):
            self.run(f"Désinstallation de {a['label']}…",
                     lambda: self._summary(catalog.uninstall_apps([key]), a, "désinstallé"), refresh=True)
            return
        variant = variants[drop.get_selected()][0] if drop else None
        language = regional.windows_locale()
        self.run(f"Installation de {a['label']}…",
                 lambda: self._summary(catalog.install_apps([key], variants={key: variant} if variant else None,
                                                            language=language), a, "installé"),
                 refresh=True)

    @staticmethod
    def _summary(res, a, verb):
        r = res.get(a["key"]) or {}
        if r.get("ok"):
            return f"{a['label']} {verb}"
        return f"{a['label']} : {catalog.describe_code(r.get('code'))}"

    # -- état et travaux --

    def update(self, state):
        ready = guest_ready(state)
        if ready != self.ready:
            self.ready = ready
            self.sensitize()
            if ready and self.get_mapped() and not self.checked_once:
                self.check()

    def sensitize(self):
        ok = self.ready and not self.busy
        self.recheck.set_sensitive(ok)
        self.office_btn.set_sensitive(ok)
        for key, (state, btn, drop) in self.app_widgets.items():
            btn.set_sensitive(ok)
            btn.set_label("Désinstaller" if self.installed.get(key) else "Installer")
            btn.remove_css_class(Granite.STYLE_CLASS_DESTRUCTIVE_ACTION)
            if drop:
                drop.set_sensitive(ok and not self.installed.get(key))
            if key in self.installed:
                state.set_label("Installé" if self.installed[key] else "")

    def check(self):
        if self.busy or not self.ready:
            return
        self.checked_once = True
        self.busy = True
        self.sensitize()

        def work():
            try:
                office = catalog.office_status()
                apps = catalog.status()
                GLib.idle_add(self.show_status, office, apps)
            except Exception as e:  # noqa: BLE001 - affiché à l'utilisateur
                GLib.idle_add(self.win.notify, f"Lecture impossible : {e}")
            GLib.idle_add(self.done)
        threading.Thread(target=work, name="vasistas-install-status", daemon=True).start()

    def show_status(self, office, apps):
        self.installed = apps
        if office.get("installed"):
            products = ", ".join(office.get("products") or []) or "Office"
            channel = catalog.CHANNELS.get(office.get("channel") or "", "canal inconnu")
            self.office_state.set_label(f"Installé : {products}, version {office.get('version') or '?'}, "
                                        f"{channel.lower()}. Réinstaller remplace cette installation.")
            self.office_btn.set_label("Réinstaller Office")
        else:
            self.office_state.set_label("Office n'est pas installé dans Windows.")
            self.office_btn.set_label("Installer Office")
        return False

    def run(self, message, work, refresh=False):
        """Travail long dans Windows, hors du fil GTK ; un seul à la fois."""
        if self.busy:
            return
        self.busy = True
        self.sensitize()
        self.win.notify(message)

        def job():
            try:
                msg = work()
            except Exception as e:  # noqa: BLE001 - affiché à l'utilisateur
                msg = f"Échec : {e}"
            GLib.idle_add(self.win.notify, msg)
            GLib.idle_add(self.done, refresh)
        threading.Thread(target=job, name="vasistas-install", daemon=True).start()

    def done(self, refresh=False):
        self.busy = False
        self.sensitize()
        if refresh:
            self.check()
        return False
