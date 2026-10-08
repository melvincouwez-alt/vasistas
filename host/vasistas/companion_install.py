"""Page « Installer » de l'application compagnon : Microsoft Office (outil de déploiement
Office) et autres applications Windows courantes (winget), voir catalog.py."""

import threading

from gi.repository import GLib, Granite, Gtk

from . import catalog, regional
from .companion_common import Section, card, dim, guest_ready, row
from .i18n import N_, _

MATCH_OS = catalog.MATCH_OS


def _restore_point(reason, app=None):
    """Point de restauration automatique avant une installation (option restore_auto) ; renvoie
    une remarque à ajouter au message final si le point n'a pas pu être créé."""
    from . import restore
    _point, err = restore.auto_point(reason, app=app)
    return " " + _("(point de restauration non créé : {e})", e=err) if err else ""


class InstallPage(Section):
    __gtype_name__ = "VasistasInstallPage"

    def __init__(self, win):
        super().__init__("system-software-install", _("Installer"),
                         _("Installez dans Windows Microsoft Office et d'autres applications, téléchargées depuis le "
                           "site de leur éditeur. Les logiciels payants nécessitent une licence."))
        self.win = win
        self.busy = False
        self.ready = False
        self.installed = {}      # clé -> installée ?
        self.checked_once = False
        self.app_widgets = {}    # clé -> (état, bouton, liste des versions)

        self.recheck = Gtk.Button(label=_("Relire l'état"))
        self.recheck.set_tooltip_text(_("Vérifier dans Windows quelles applications sont déjà installées"))
        self.recheck.connect("clicked", lambda *_: self.check())
        self.get_action_area().append(self.recheck)
        self.connect("map", self.on_map)

        self.office_section()
        self.apps_section()
        self.connect("map", lambda *_: self.ready and not self.checked_once and self.check())

    # -- Office --

    def office_section(self):
        self.header("Microsoft Office")
        self.office_state = self.add(dim(_("État de l'installation d'Office inconnu : cliquez sur « Relire l'état ».")))

        self.products = catalog.OFFICE_PRODUCTS
        self.product = Gtk.DropDown.new_from_strings([_(p["label"]) for p in self.products])
        self.product_note = dim("")
        self.product.connect("notify::selected", lambda *_: self.on_product())
        self.add(row(_("Offre"), _("Choisissez l'offre de votre abonnement ou de votre licence. Avec une autre offre, "
                                   "Office s'installe mais son activation échoue."), self.product))
        self.add(self.product_note)

        self.channel = Gtk.DropDown()
        self.channel_hint = dim("")
        self.channel.connect("notify::selected", lambda *_: self.on_channel())
        self.add(row(_("Version"), _("Fréquence de publication des nouvelles fonctionnalités d'Office."), self.channel))
        self.add(self.channel_hint)

        # langue principale : langue de Windows ou une langue complète d'Office
        self.languages = [(MATCH_OS, _("Langue de Windows"))] + [(c, _(n)) for c, n, full in catalog.OFFICE_LANGUAGES
                                                                 if c != MATCH_OS and full]
        self.language = Gtk.DropDown.new_from_strings([n for _c, n in self.languages])
        self.language.set_enable_search(True)
        wanted = catalog.language_for_locale(regional.linux_locale())
        codes = [c for c, _ in self.languages]
        self.language.set_selected(codes.index(wanted) if wanted in codes else 0)
        self.add(row(_("Langue"), _("Langue de l'interface d'Office."), self.language))

        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=5,
                           column_spacing=12, row_spacing=4, homogeneous=True)
        self.office_apps = {}
        for a in catalog.OFFICE_APPS:
            chk = Gtk.CheckButton(label=_(a["label"]), active=not a["excluded"])
            self.office_apps[a["id"]] = chk
            flow.append(chk)
        exp = Gtk.Expander(label=_("Applications incluses"), child=flow, margin_top=6)
        self.add(exp)

        self.addons = {}
        addon_box = Gtk.Box(spacing=18, margin_top=6)
        for key, a in catalog.ADDONS.items():
            chk = Gtk.CheckButton(label=_(a["label"]))
            self.addons[key] = chk
            addon_box.append(chk)
        self.add(row(_("Compléments"), _("Visio et Project nécessitent chacun leur propre licence."), addon_box))

        self.office_btn = Gtk.Button(label=_("Installer Office"), halign=Gtk.Align.END, margin_top=6)
        self.office_btn.add_css_class(Granite.STYLE_CLASS_SUGGESTED_ACTION)
        self.office_btn.connect("clicked", lambda *_: self.confirm_office())
        self.add(self.office_btn)
        self.product.set_selected(0)
        self.on_product()

    def on_product(self):
        p = self.products[self.product.get_selected()]
        self.product_note.set_label(_(p["note"]) if p.get("note") else "")
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
            message=_("Installer {name} dans Windows ?", name=_(p["label"])),
            detail=_("Le téléchargement et l'installation prennent 10 à 30 minutes et se déroulent sans intervention "
                     "de votre part. Office s'active à la première ouverture, avec votre compte ou votre clé de "
                     "produit."),
            buttons=[_("Annuler"), _("Installer")], cancel_button=0, default_button=1)

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
            note = _restore_point("install-office")
            res = catalog.install_office(p["key"], channel=channel, languages=[language], exclude=exclude,
                                         addons=addons)
            return (_("{name} installé", name=_(p["label"])) if res.get("ok", res.get("code") == 0)
                    else _("Échec de l'installation d'Office (code {code})", code=res.get("code"))) + note
        self.run(_("Installation de {name}… (10 à 30 minutes)", name=_(p["label"])), work, refresh=True)

    # -- autres applications --

    def apps_section(self):
        self.header(_("Autres applications"))
        self.add(dim(_("Vasistas installe ces applications sans intervention de votre part avec winget, le "
                       "gestionnaire de paquets de Windows, depuis le site de chaque éditeur. Les applications "
                       "installées apparaissent ensuite dans la section « Dans le menu ».")))
        by_cat = {}
        for a in catalog.APPS:
            by_cat.setdefault(a["category"], []).append(a)
        for cat in catalog.CATEGORIES:
            if cat not in by_cat:
                continue
            label = Gtk.Label(label=_(cat), xalign=0, margin_top=12)
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
        texts.append(Gtk.Label(label=_(a["label"]), xalign=0))
        sub = _(a["description"]) if a.get("description") else ""
        if a.get("note"):
            sub = _("{description}. {note}", description=sub, note=_(a["note"]))
        texts.append(dim(sub))
        box.append(texts)
        variants = catalog.variant_choices(a["key"])
        drop = None
        if len(variants) > 1:
            drop = Gtk.DropDown.new_from_strings([_(label) for _v, label in variants])
            drop.set_valign(Gtk.Align.CENTER)
            drop.set_tooltip_text(_("Version à installer"))
            box.append(drop)
        state = Gtk.Label(label="", valign=Gtk.Align.CENTER)
        state.add_css_class(Granite.STYLE_CLASS_DIM_LABEL)
        state.add_css_class(Granite.STYLE_CLASS_SMALL_LABEL)
        box.append(state)
        btn = Gtk.Button(label=_("Installer"), valign=Gtk.Align.CENTER)
        btn.connect("clicked", lambda *_: self.on_app(a, drop, variants))
        box.append(btn)
        self.app_widgets[a["key"]] = (state, btn, drop)
        return Gtk.ListBoxRow(activatable=False, child=box)

    def on_app(self, a, drop, variants):
        key = a["key"]
        if self.installed.get(key):
            self.run(_("Désinstallation de {name}…", name=_(a["label"])),
                     lambda: self._summary(catalog.uninstall_apps([key]), a, False), refresh=True)
            return
        variant = variants[drop.get_selected()][0] if drop else None
        language = regional.windows_locale()
        def work():
            note = _restore_point("install", a["label"])
            return self._summary(catalog.install_apps([key], variants={key: variant} if variant else None,
                                                      language=language), a, True) + note
        self.run(_("Installation de {name}…", name=_(a["label"])), work, refresh=True)

    @staticmethod
    def _summary(res, a, install):
        r = res.get(a["key"]) or {}
        name = _(a["label"])
        if r.get("ok"):
            return _("{name} installé", name=name) if install else _("{name} désinstallé", name=name)
        return _("{name} : {result}", name=name, result=catalog.describe_code(r.get("code")))

    # -- état et travaux --

    def on_map(self, *_a):
        if self.ready and not self.checked_once:
            self.check()

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
            btn.set_label(_("Désinstaller") if self.installed.get(key) else _("Installer"))
            btn.remove_css_class(Granite.STYLE_CLASS_DESTRUCTIVE_ACTION)
            if drop:
                drop.set_sensitive(ok and not self.installed.get(key))
            if key in self.installed:
                state.set_label(_("Installé") if self.installed[key] else "")

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
                GLib.idle_add(self.win.notify, _("Lecture impossible : {e}", e=e))
            GLib.idle_add(self.done)
        threading.Thread(target=work, name="vasistas-install-status", daemon=True).start()

    def show_status(self, office, apps):
        self.installed = apps
        if office.get("installed"):
            products = ", ".join(office.get("products") or []) or "Office"
            channel = _(catalog.CHANNELS.get(office.get("channel") or "", N_("canal inconnu")))
            self.office_state.set_label(_("Office est installé : {products}, version {version}, {channel}. Une "
                                          "réinstallation remplace l'installation actuelle.", products=products,
                                          version=office.get("version") or "?", channel=channel.lower()))
            self.office_btn.set_label(_("Réinstaller Office"))
        else:
            self.office_state.set_label(_("Office n'est pas installé dans Windows."))
            self.office_btn.set_label(_("Installer Office"))
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
                msg = _("Échec : {e}", e=e)
            GLib.idle_add(self.win.notify, msg)
            GLib.idle_add(self.done, refresh)
        threading.Thread(target=job, name="vasistas-install", daemon=True).start()

    def done(self, refresh=False):
        self.busy = False
        self.sensitize()
        if refresh:
            self.check()
        return False
