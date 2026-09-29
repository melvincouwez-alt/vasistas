"""Assistant de configuration initiale de Vasistas (ouvert par l'application compagnon).

Étapes : vérifications de l'ordinateur ; Windows (version, langue, licence, image ISO
téléchargée chez Microsoft, pilotes) ; réglages (puissance, dossiers partagés) ; installation
de Windows ; préparation de Windows et applications (Office, autres) ; fin. Chaque étape peut
être refaite plus tard ; une VM déjà installée saute l'installation.
"""

import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Granite", "7.0")
from gi.repository import Gio, GLib, Granite, Gtk  # noqa: E402

from . import control, vm, winiso  # noqa: E402
from .app import APP_ID  # noqa: E402

VIRTIO_URL = ("https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/"
              "stable-virtio/virtio-win.iso")
MIN_FREE_GB = 100
# dossiers proposés au partage : (dossier spécial XDG, nom affiché)
SPECIAL = [
    (GLib.UserDirectory.DIRECTORY_DOCUMENTS, "Documents"),
    (GLib.UserDirectory.DIRECTORY_DOWNLOAD, "Téléchargements"),
    (GLib.UserDirectory.DIRECTORY_DESKTOP, "Bureau"),
    (GLib.UserDirectory.DIRECTORY_PICTURES, "Images"),
    (GLib.UserDirectory.DIRECTORY_MUSIC, "Musique"),
    (GLib.UserDirectory.DIRECTORY_VIDEOS, "Vidéos"),
]


def dim(text):
    lbl = Gtk.Label(label=text, xalign=0, wrap=True)
    lbl.add_css_class(Granite.STYLE_CLASS_DIM_LABEL)
    return lbl


def check_line(ok, title, hint=""):
    box = Gtk.Box(spacing=10, margin_top=4)
    icon = Gtk.Image(icon_name="process-completed" if ok else "dialog-warning", pixel_size=24,
                     valign=Gtk.Align.START)
    box.append(icon)
    texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    texts.append(Gtk.Label(label=title, xalign=0, wrap=True))
    if hint and not ok:
        h = dim(hint)
        h.set_selectable(True)
        texts.append(h)
    box.append(texts)
    return box


def clear(box):
    child = box.get_first_child()
    while child:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


def set_config(**values):
    cfg = vm.load_config()
    for k, v in values.items():
        if v is None:
            cfg.pop(k, None)
        else:
            cfg[k] = v
    vm.save_config(cfg)


class Wizard(Gtk.Window):
    def __init__(self, parent, on_done=None):
        super().__init__(title="Configuration de Vasistas", transient_for=parent, modal=True,
                         default_width=700, default_height=640, icon_name=APP_ID)
        self.on_done = on_done
        self.cancel = threading.Event()
        header = Gtk.HeaderBar()
        header.add_css_class(Granite.STYLE_CLASS_FLAT)
        self.set_titlebar(header)

        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vexpand=True)
        self.pages = []
        self.index = 0
        for build in (self.page_welcome, self.page_windows, self.page_settings, self.page_install,
                      self.page_apps, self.page_done):
            name, widget = build()
            self.stack.add_named(widget, name)
            self.pages.append(name)

        self.back = Gtk.Button(label="Précédent")
        self.back.connect("clicked", lambda *_: self.go(-1))
        self.next = Gtk.Button(label="Suivant")
        self.next.add_css_class(Granite.STYLE_CLASS_SUGGESTED_ACTION)
        self.next.connect("clicked", lambda *_: self.go(1))
        self.dots = Gtk.Label()
        self.dots.add_css_class(Granite.STYLE_CLASS_DIM_LABEL)
        bar = Gtk.Box(spacing=8, margin_start=18, margin_end=18, margin_top=12, margin_bottom=18)
        bar.append(self.back)
        bar.append(Gtk.Box(hexpand=True))
        bar.append(self.dots)
        bar.append(Gtk.Box(hexpand=True))
        bar.append(self.next)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(self.stack)
        root.append(bar)
        self.set_child(root)
        self.connect("close-request", lambda *_: self.cancel.set() or False)
        self.index = 0
        self.update_nav()

    # -- navigation --

    def go(self, step):
        if self.index + step >= len(self.pages):
            set_config(setup_done=True)
            if "open_with" not in vm.load_config():
                # Office et Power BI s'ouvrent dans Windows d'un double-clic (page Fichiers)
                from . import files
                files.apply()
            if self.on_done:
                self.on_done()
            self.close()
            return
        self.index = max(0, self.index + step)
        self.stack.set_visible_child_name(self.pages[self.index])
        self.update_nav()

    def update_nav(self):
        name = self.pages[self.index]
        self.back.set_visible(self.index > 0)
        self.next.set_label("Terminer" if self.index == len(self.pages) - 1 else "Suivant")
        self.dots.set_label(" ".join("●" if i == self.index else "○" for i in range(len(self.pages))))
        self.next.set_sensitive(True)
        refresh = getattr(self, f"refresh_{name}", None)
        if refresh:
            refresh()

    def page(self, title, subtitle, icon=APP_ID):
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=28, margin_end=28,
                      margin_top=12, margin_bottom=12)
        head = Gtk.Box(spacing=14, margin_bottom=10)
        head.append(Gtk.Image(icon_name=icon, pixel_size=64))
        tb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER)
        t = Gtk.Label(label=title, xalign=0, wrap=True)
        t.add_css_class(Granite.STYLE_CLASS_H2_LABEL)
        tb.append(t)
        sub = dim(subtitle)
        tb.append(sub)
        head.append(tb)
        box.append(head)
        box.subtitle = sub
        return box

    def scrolled(self, box):
        return Gtk.ScrolledWindow(child=box, hscrollbar_policy=Gtk.PolicyType.NEVER)

    # -- 1. bienvenue et vérifications --

    def page_welcome(self):
        box = self.page("Bienvenue dans Vasistas",
                        "Les applications Windows, une fenêtre chacune, sur votre bureau Linux. Cet assistant "
                        "vérifie l'ordinateur, installe Windows puis les applications.")
        self.checks = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(Granite.HeaderLabel.new("Cet ordinateur"))
        box.append(self.checks)
        return "welcome", self.scrolled(box)

    def refresh_welcome(self):
        clear(self.checks)
        kvm = os.access("/dev/kvm", os.R_OK | os.W_OK)
        qemu = bool(shutil.which(vm.QEMU)) or Path(vm.QEMU).exists()
        ovmf = vm.OVMF_CODE.exists() and vm.OVMF_VARS.exists()
        vm.DATA.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(vm.DATA).free / 1e9
        ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 2**30
        installed = vm.DISK.exists()
        items = [
            (kvm, "Virtualisation matérielle (KVM)",
             "Activez la virtualisation (SVM ou VT-x) dans le BIOS, puis : sudo usermod -aG kvm $USER, "
             "et rouvrez la session."),
            (qemu, "QEMU", "sudo apt install qemu-system-x86 qemu-utils"),
            (ovmf, "Micrologiciel UEFI (OVMF)", "sudo apt install ovmf"),
            (bool(vm.VIRTIOFSD), "Partage de dossiers (virtiofsd)", "sudo apt install virtiofsd"),
            (installed or free >= MIN_FREE_GB, f"Espace disque : {free:.0f} Go libres",
             f"Windows et ses applications demandent environ {MIN_FREE_GB} Go."),
            (ram >= 15, f"Mémoire : {ram:.0f} Go", "16 Go conseillés : Windows en prend 6 à 12."),
        ]
        for ok, title, hint in items:
            self.checks.append(check_line(ok, title, hint))
        if installed:
            self.checks.append(check_line(True, "Windows est déjà installé"))
        self.next.set_sensitive(kvm and qemu and ovmf)

    # -- 2. Windows : version, langue, licence, ISO, pilotes --

    def page_windows(self):
        box = self.page("Windows", "Choisissez la version et la langue de Windows, puis sa licence. L'image "
                        "d'installation se télécharge ici, chez Microsoft.", "computer")
        cfg = vm.load_config()
        self.versions = winiso.VERSIONS
        keys = [v["key"] for v in self.versions]
        box.append(Granite.HeaderLabel.new("Version"))
        self.version = Gtk.DropDown.new_from_strings([v["label"] for v in self.versions])
        cur = cfg.get("windows_version", winiso.DEFAULT_VERSION)
        self.version.set_selected(keys.index(cur) if cur in keys else 0)
        box.append(self.version)
        self.version_note = dim("")
        box.append(self.version_note)

        box.append(Granite.HeaderLabel.new("Langue"))
        self.language = Gtk.DropDown()
        self.language.set_enable_search(True)
        box.append(self.language)
        box.append(dim("Langue des menus de Windows. Le format des dates, le clavier et le fuseau horaire "
                       "sont repris de ce système."))

        self.license_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.license_box.append(Granite.HeaderLabel.new("Licence"))
        self.with_key = Gtk.CheckButton(label="J'ai une clé de produit")
        self.key_entry = Gtk.Entry(placeholder_text="XXXXX-XXXXX-XXXXX-XXXXX-XXXXX", margin_start=28,
                                   max_length=40)
        self.without_key = Gtk.CheckButton(label="Installer sans clé et activer plus tard", group=self.with_key)
        editions = [e for e in winiso.EDITIONS if e[0] in winiso.CONSUMER_EDITIONS]
        self.editions = [e[0] for e in editions]
        self.edition = Gtk.DropDown.new_from_strings([e[1] for e in editions])
        ed = cfg.get("windows_edition", "pro")
        self.edition.set_selected(self.editions.index(ed) if ed in self.editions else 0)
        ed_row = Gtk.Box(spacing=8, margin_start=28)
        ed_row.append(Gtk.Label(label="Édition :"))
        ed_row.append(self.edition)
        for w in (self.with_key, self.key_entry, self.without_key, ed_row):
            self.license_box.append(w)
        self.license_box.append(dim("Sans clé, Windows s'installe dans l'édition choisie et fonctionne avec un "
                                    "rappel d'activation, jusqu'à ce que vous saisissiez votre clé "
                                    "(Paramètres, Système, Activation)."))
        self.buy = Gtk.LinkButton(label="Acheter une licence Windows 11 Professionnel", uri=winiso.buy_url(),
                                  halign=Gtk.Align.START)
        self.license_box.append(self.buy)
        box.append(self.license_box)
        if cfg.get("windows_key"):
            self.with_key.set_active(True)
            self.key_entry.set_text(cfg["windows_key"])
        else:
            self.without_key.set_active(True)

        box.append(Granite.HeaderLabel.new("Image d'installation"))
        self.iso_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(self.iso_box)
        btns = Gtk.Box(spacing=8)
        self.dl_btn = Gtk.Button(label="Télécharger")
        self.dl_btn.add_css_class(Granite.STYLE_CLASS_SUGGESTED_ACTION)
        self.dl_btn.connect("clicked", lambda *_: self.download_iso())
        pick = Gtk.Button(label="Choisir un fichier ISO…")
        pick.connect("clicked", lambda *_: self.pick_iso())
        page = Gtk.Button(label="Page de Microsoft")
        page.connect("clicked", lambda *_: Gtk.UriLauncher.new(self.official_page()).launch(self, None, None))
        self.stop_btn = Gtk.Button(label="Annuler", visible=False)
        self.stop_btn.connect("clicked", lambda *_: self.cancel.set())
        for b in (self.dl_btn, pick, page, self.stop_btn):
            btns.append(b)
        box.append(btns)
        self.progress = Gtk.ProgressBar(visible=False, show_text=True, margin_top=6)
        box.append(self.progress)

        box.append(Granite.HeaderLabel.new("Pilotes"))
        self.virtio_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        box.append(self.virtio_box)

        self.version.connect("notify::selected", lambda *_: self.on_version())
        self.language.connect("notify::selected", lambda *_: self.save_windows())
        self.edition.connect("notify::selected", lambda *_: self.save_windows())
        self.with_key.connect("toggled", lambda *_: self.save_windows())
        self.without_key.connect("toggled", lambda *_: self.save_windows())
        self.key_entry.connect("changed", lambda *_: self.save_windows())
        self.downloading = False
        self.on_version(initial=True)
        return "windows", self.scrolled(box)

    def current_version(self):
        return self.versions[self.version.get_selected()]

    def official_page(self):
        return self.current_version().get("page") or winiso.official_page()

    def on_version(self, initial=False):
        v = self.current_version()
        note = v.get("description", "")
        if v.get("note"):
            note += " " + v["note"]
        self.version_note.set_label(note)
        self.langs = winiso.languages(v["key"])
        names = [n for _, n in self.langs]
        self.language.set_model(Gtk.StringList.new(names))
        wanted = vm.load_config().get("windows_language") if initial else None
        codes = [c for c, _ in self.langs]
        if wanted not in codes:
            wanted = winiso.default_language(v["key"])
        self.language.set_selected(codes.index(wanted) if wanted in codes else 0)
        self.license_box.set_visible(v["needs_key"])
        self.save_windows()

    def save_windows(self):
        v = self.current_version()
        lang = self.langs[self.language.get_selected()][0] if self.langs else None
        key = None
        if v["needs_key"] and self.with_key.get_active():
            key = winiso.normalize_key(self.key_entry.get_text())
        self.key_entry.set_sensitive(self.with_key.get_active())
        self.edition.set_sensitive(self.without_key.get_active())
        set_config(windows_version=v["key"], windows_language=lang,
                   windows_edition=self.editions[self.edition.get_selected()],
                   windows_key=key if (v["needs_key"] and self.with_key.get_active()) else None)
        self.refresh_windows()

    def refresh_windows(self):
        if not hasattr(self, "iso_box"):
            return
        installed = vm.DISK.exists()
        clear(self.iso_box)
        cfg = vm.load_config()
        info = cfg.get("windows_iso") or {}
        iso = vm.WIN_ISO.exists()
        if installed and not iso:
            self.iso_box.append(check_line(True, "Inutile : Windows est déjà installé"))
        elif iso:
            name = info.get("name") or os.path.basename(os.path.realpath(vm.WIN_ISO))
            same = info.get("version") == cfg.get("windows_version") and \
                info.get("language") == cfg.get("windows_language")
            hint = "" if same or info.get("picked") else \
                "Cette image ne correspond pas aux choix ci-dessus : téléchargez-la de nouveau."
            if info.get("picked"):
                hint = "Image choisie à la main : vérifiez qu'elle correspond à la version et à la langue."
            self.iso_box.append(check_line(same, f"Image prête : {name}", hint))
        else:
            self.iso_box.append(check_line(False, "Image à télécharger (5 à 8 Go)",
                                           "Téléchargée directement chez Microsoft. Comptez 5 à 20 minutes."))
        self.dl_btn.set_sensitive(not self.downloading and not installed)
        clear(self.virtio_box)
        virtio = vm.VIRTIO_ISO.exists()
        self.virtio_box.append(check_line(virtio, "Pilotes virtio pour Windows",
                                          "Environ 700 Mo, publiés par le projet Fedora."))
        if not virtio:
            get = Gtk.Button(label="Télécharger les pilotes", halign=Gtk.Align.START)
            get.connect("clicked", lambda *_: self.download_virtio())
            self.virtio_box.append(get)
        v = self.current_version()
        key_ok = not v["needs_key"] or self.without_key.get_active() or \
            winiso.normalize_key(self.key_entry.get_text()) is not None
        if self.index < len(self.pages) and self.pages[self.index] == "windows":
            self.next.set_sensitive((iso or installed) and virtio and key_ok and not self.downloading)

    def pick_iso(self):
        dialog = Gtk.FileDialog(title="Image ISO de Windows")
        filt = Gtk.FileFilter(name="Images ISO")
        filt.add_pattern("*.iso")
        store = Gio.ListStore.new(Gtk.FileFilter)
        store.append(filt)
        dialog.set_filters(store)

        def done(d, res):
            try:
                path = d.open_finish(res).get_path()
            except GLib.Error:
                return
            vm.WIN_ISO.parent.mkdir(parents=True, exist_ok=True)
            vm.WIN_ISO.unlink(missing_ok=True)
            vm.WIN_ISO.symlink_to(path)
            set_config(windows_iso={"name": os.path.basename(path), "picked": True})
            self.refresh_windows()
        dialog.open(self, None, done)

    def download_iso(self):
        v = self.current_version()
        lang = self.langs[self.language.get_selected()][0]
        self.downloading = True
        self.cancel.clear()
        self.progress.set_visible(True)
        self.progress.set_fraction(0)
        self.progress.set_text("Demande du lien à Microsoft…")
        self.stop_btn.set_visible(True)
        self.refresh_windows()

        def progress(done, total):
            if total:
                GLib.idle_add(self.progress.set_fraction, done / total)
                GLib.idle_add(self.progress.set_text, f"{done / 1e9:.1f} / {total / 1e9:.1f} Go")

        def work():
            msg = None
            try:
                url, name, size = winiso.get_link(lang, v["key"])
                free = shutil.disk_usage(vm.DATA).free
                if size and free < size + 2e9:
                    raise winiso.DownloadError(f"Pas assez de place : {size / 1e9:.1f} Go nécessaires.")
                if vm.WIN_ISO.is_symlink():
                    vm.WIN_ISO.unlink()
                winiso.download(url, vm.WIN_ISO, progress, self.cancel, expected_size=size)
                set_config(windows_iso={"version": v["key"], "language": lang, "name": name})
                msg = f"{name} téléchargé."
            except winiso.DownloadCancelled:
                msg = "Téléchargement interrompu : il reprendra où il s'est arrêté."
            except (winiso.DownloadError, OSError) as e:
                msg = str(e)
            GLib.idle_add(self.download_done, msg)
        threading.Thread(target=work, daemon=True).start()

    def download_done(self, msg):
        self.downloading = False
        self.stop_btn.set_visible(False)
        self.progress.set_text(msg or "")
        self.refresh_windows()
        return False

    def download_virtio(self):
        self.progress.set_visible(True)
        self.next.set_sensitive(False)

        def work():
            tmp = vm.VIRTIO_ISO.with_suffix(".part")
            try:
                vm.VIRTIO_ISO.parent.mkdir(parents=True, exist_ok=True)
                with urllib.request.urlopen(VIRTIO_URL, timeout=30) as r, open(tmp, "wb") as f:
                    total = int(r.headers.get("Content-Length") or 0)
                    done = 0
                    while chunk := r.read(1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        if total:
                            GLib.idle_add(self.progress.set_fraction, done / total)
                            GLib.idle_add(self.progress.set_text, f"Pilotes : {done >> 20} / {total >> 20} Mo")
                tmp.replace(vm.VIRTIO_ISO)
            except OSError as e:
                GLib.idle_add(self.progress.set_text, f"Échec : {e}")
                tmp.unlink(missing_ok=True)
                return
            GLib.idle_add(self.progress.set_visible, False)
            GLib.idle_add(self.refresh_windows)
        threading.Thread(target=work, daemon=True).start()

    # -- 3. réglages --

    def page_settings(self):
        from .companion_common import RESOURCES
        box = self.page("Réglages", "Modifiables ensuite dans Vasistas.", "preferences-system")
        box.append(Granite.HeaderLabel.new("Puissance allouée à Windows"))
        keys = [k for k, _, _ in RESOURCES]
        drop = Gtk.DropDown.new_from_strings([f"{label} · {hint}" for _, label, hint in RESOURCES])
        cur = vm.load_config().get("resources", "balanced")
        drop.set_selected(keys.index(cur) if cur in keys else 1)
        drop.connect("notify::selected", lambda d, _p: set_config(resources=keys[d.get_selected()]))
        box.append(drop)

        box.append(Granite.HeaderLabel.new("Dossiers visibles dans Windows"))
        box.append(dim("Chaque dossier devient un lecteur dans l'Explorateur de Windows. Enregistrez-y vos "
                       "documents pour les retrouver sous Linux."))
        current = {str(p) for _, p, _, _ in vm.shares()}
        self.share_checks = []
        for special, label in SPECIAL:
            path = GLib.get_user_special_dir(special)
            if not path or not os.path.isdir(path) or path == os.path.expanduser("~"):
                continue
            chk = Gtk.CheckButton(label=f"{label}  ({path.replace(os.path.expanduser('~'), '~', 1)})",
                                  active=path in current)
            chk.connect("toggled", lambda *_: self.save_share_checks())
            self.share_checks.append((chk, path, label))
            box.append(chk)
        return "settings", self.scrolled(box)

    def save_share_checks(self):
        old = {str(p): (dr, lb) for _, p, dr, lb in vm.shares()}
        chosen = {path: label for chk, path, label in self.share_checks if chk.get_active()}
        # garder les autres dossiers ajoutés dans Vasistas, et leurs lettres
        items = [(p, dr, lb) for p, (dr, lb) in old.items()
                 if p in chosen or p not in {path for _, path, _ in self.share_checks}]
        used = {dr for _, dr, _ in items}
        free = [f"{c}:" for c in "ZYXWVUTSRQPONMLKJIHGFE" if f"{c}:" not in used]
        for path, label in chosen.items():
            if path not in old:
                items.append((path, free.pop(0), label))
        vm.save_shares(items)

    # -- 4. installation de Windows --

    def page_install(self):
        box = self.page("Installation de Windows", "", "system-software-install")
        self.install_page = box
        self.install_label = Gtk.Label(xalign=0, wrap=True)
        box.append(self.install_label)
        self.install_btn = Gtk.Button(label="Installer Windows", halign=Gtk.Align.START)
        self.install_btn.add_css_class(Granite.STYLE_CLASS_SUGGESTED_ACTION)
        self.install_btn.connect("clicked", lambda *_: self.install_windows())
        box.append(self.install_btn)
        self.install_spin = Gtk.Spinner(halign=Gtk.Align.START)
        box.append(self.install_spin)
        box.append(dim("Une fenêtre montre l'installation ; elle se ferme d'elle-même à la fin, après deux "
                       "redémarrages de Windows. Il n'y a rien à faire pendant ce temps."))
        return "install", self.scrolled(box)

    def refresh_install(self):
        cfg = vm.load_config()
        v = next((x for x in winiso.VERSIONS if x["key"] == cfg.get("windows_version")), winiso.VERSIONS[0])
        lang = dict(winiso.LANGUAGES).get(cfg.get("windows_language"), cfg.get("windows_language") or "")
        self.install_page.subtitle.set_label(f"{v['label']}, {lang.lower()}, avec un compte local (sans compte "
                                             "Microsoft).")
        installed = vm.DISK.exists()
        running = getattr(self, "installing", False)
        self.install_label.set_label("Windows est installé." if installed and not running else
                                     "Installation en cours : 20 à 40 minutes." if running else
                                     "Prêt à installer Windows.")
        self.install_btn.set_visible(not installed and not running)
        self.next.set_sensitive(installed and not running)

    def install_windows(self):
        self.installing = True
        self.install_spin.start()
        self.refresh_install()
        env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1]))

        def work():
            log = open(vm.DATA / "install.log", "ab")
            rc = subprocess.run([sys.executable, "-m", "vasistas", "vm", "install"], env=env,
                                stdout=log, stderr=log).returncode
            GLib.idle_add(self.install_finished, rc)
        threading.Thread(target=work, daemon=True).start()

    def install_finished(self, rc):
        self.installing = False
        self.install_spin.stop()
        self.refresh_install()
        if rc != 0 or not vm.DISK.exists():
            self.install_label.set_label(f"L'installation a échoué (code {rc}). Détails : {vm.DATA / 'install.log'}")
        return False

    # -- 5. préparation de Windows et applications --

    def page_apps(self):
        from . import catalog
        box = self.page("Applications", "Installées dans Windows, depuis les serveurs de leurs éditeurs.",
                        "applications-office")
        self.prepare = Gtk.CheckButton(label="Outils de Vasistas dans Windows (dossiers partagés, pilotes)",
                                       active=True)
        box.append(self.prepare)
        box.append(Granite.HeaderLabel.new("Microsoft Office"))
        self.office = Gtk.CheckButton(label="Installer Office", active=True)
        box.append(self.office)
        self.office_products = catalog.OFFICE_PRODUCTS
        self.office_product = Gtk.DropDown.new_from_strings([p["label"] for p in self.office_products])
        self.office_product.set_margin_start(28)
        self.office_product.set_halign(Gtk.Align.START)
        box.append(self.office_product)
        box.append(dim("Choisissez l'offre de votre abonnement ou de votre licence. Office s'installe dans la "
                       "langue de Windows et s'active à la première ouverture. La page Installer de Vasistas "
                       "propose ensuite d'autres versions, langues et compléments (Visio, Project)."))
        box.append(Granite.HeaderLabel.new("Autres applications"))
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=3,
                           column_spacing=12, row_spacing=4, homogeneous=True)
        self.app_checks = {}
        for a in catalog.APPS:
            chk = Gtk.CheckButton(label=a["label"])
            chk.set_tooltip_text(a.get("description", ""))
            self.app_checks[a["key"]] = chk
            flow.append(chk)
        box.append(flow)
        self.apps_btn = Gtk.Button(label="Installer", halign=Gtk.Align.START, margin_top=8)
        self.apps_btn.add_css_class(Granite.STYLE_CLASS_SUGGESTED_ACTION)
        self.apps_btn.connect("clicked", lambda *_: self.install_apps())
        box.append(self.apps_btn)
        self.apps_label = dim("Vous pouvez aussi passer cette étape et installer plus tard depuis Vasistas.")
        box.append(self.apps_label)
        # déjà présentes d'après la dernière liste lue dans Windows
        from . import guestapps
        have = {a["id"] for a in guestapps.cached()}
        if "winword" in have:
            self.office.set_active(False)
            self.office.set_label("Installer Office (déjà installé)")
        return "apps", self.scrolled(box)

    def install_apps(self):
        from . import catalog, regional
        from .companion_common import host_ready, spawn
        product = self.office_products[self.office_product.get_selected()]["key"] if self.office.get_active() \
            else None
        keys = [k for k, chk in self.app_checks.items() if chk.get_active()]
        prepare = self.prepare.get_active()
        if not (product or keys or prepare):
            return
        self.apps_btn.set_sensitive(False)
        self.next.set_sensitive(False)

        def say(text):
            GLib.idle_add(self.apps_label.set_label, text)

        def work():
            try:
                say("Démarrage de Windows…")
                if host_ready() is None:
                    spawn("run")
                vm.start()
                for _ in range(300):
                    st = host_ready()
                    if st and st.get("guest_ready"):
                        break
                    time.sleep(1)
                else:
                    raise RuntimeError("Windows ne répond pas")
                if prepare:
                    say("Préparation de Windows (outils de Vasistas)…")
                    script = (vm.INSTALL_DIR / "configure-windows.ps1").read_text(encoding="utf-8-sig")
                    res = control.request({"exec": script}, timeout=1800)
                    if res.get("code"):
                        raise RuntimeError("préparation : " + (res.get("out") or "")[-300:])
                if product:
                    say("Installation d'Office… (10 à 30 minutes)")
                    r = catalog.install_office(product)
                    if not r.get("ok", r.get("code") == 0):
                        raise RuntimeError(f"Office : code {r.get('code')}")
                if keys:
                    say("Installation des autres applications…")
                    res = catalog.install_apps(keys, language=regional.windows_locale())
                    failed = [catalog.APPS_BY_KEY[k]["label"] for k, r in res.items() if not r.get("ok")]
                    if failed:
                        raise RuntimeError("non installées : " + ", ".join(failed))
                say("Tout est installé.")
            except (OSError, RuntimeError, SystemExit, ValueError) as e:
                say(f"Échec : {e}")
            GLib.idle_add(self.apps_btn.set_sensitive, True)
            GLib.idle_add(self.next.set_sensitive, True)
        threading.Thread(target=work, daemon=True).start()

    # -- 6. fin --

    def page_done(self):
        box = self.page("C'est prêt", "Vos applications Windows sont dans le menu Applications.")
        box.append(dim("Dans Vasistas : la page Menu Applications choisit les applications affichées, la page "
                       "Fichiers les types de documents ouverts dans Windows, la page Dossiers ce que Windows "
                       "voit de vos dossiers, et la page Installer ajoute des applications."))
        guide = Gtk.Button(label="Ouvrir le guide rapide", halign=Gtk.Align.START, margin_top=12)
        guide.connect("clicked", lambda *_: self.open_guide())
        box.append(guide)
        return "done", self.scrolled(box)

    def open_guide(self):
        from .guide import GuideWindow
        GuideWindow(self.get_transient_for() or self).present()
