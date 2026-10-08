"""Page « Navigateur » de l'application compagnon, si Lucarne est installé.

Lucarne (projet séparé) ouvre les services web Microsoft 365 dans des fenêtres du bureau ; son
extension Chrome renvoie les documents cliqués soit vers ces fenêtres, soit vers Office de la
VM. Cette page ne fait que piloter ce choix. Les deux projets ne partagent aucun code : Vasistas
passe par la commande `lucarne` (`status`, `config get`, `config set <appli> target vm|web`),
cherchée dans le PATH ou donnée par VASISTAS_LUCARNE. Sans elle, la page n'apparaît pas.
"""

import json
import os
import shlex
import shutil
import subprocess
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Granite", "7.0")
from gi.repository import Granite, Gtk  # noqa: E402

from .companion_common import Section, dim, plain_card, row  # noqa: E402
from .i18n import N_, _  # noqa: E402

CHOICES = [("vm", N_("Windows (Vasistas)")), ("web", N_("En ligne"))]


def _lucarne():
    if os.environ.get("VASISTAS_LUCARNE"):
        return shlex.split(os.environ["VASISTAS_LUCARNE"])
    found = shutil.which("lucarne") or shutil.which("lucarne", path=str(Path.home() / ".local/bin"))
    return [found] if found else None


def lucarne(*args):
    """Sortie JSON d'une commande `lucarne`, ou None (absent, erreur)."""
    cmd = _lucarne()
    if not cmd:
        return None
    try:
        out = subprocess.run(cmd + list(args), capture_output=True, text=True, timeout=10, check=True).stdout
        return json.loads(out) if out.strip() else {}
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def available():
    status = lucarne("status")
    return bool(status and status.get("format") == 1 and status.get("extension", {}).get("path"))


def extension_state(ext_id):
    """(chargée, version) d'après les préférences de Chrome ; version None si Chrome ne la note pas
    (extension non empaquetée : relue sur le disque à chaque rechargement)."""
    for name in ("Secure Preferences", "Preferences"):
        try:
            prefs = json.loads((Path.home() / ".config/google-chrome/Default" / name).read_text())
        except (OSError, ValueError):
            continue
        e = prefs.get("extensions", {}).get("settings", {}).get(ext_id)
        if e:
            return True, e.get("manifest", {}).get("version")
    return False, None


class BrowserPage(Section):
    __gtype_name__ = "VasistasBrowserPage"

    def __init__(self, win):
        super().__init__("web-browser", _("Navigateur"),
                         _("Choix de l'application qui ouvre les documents Word, Excel et PowerPoint cliqués dans "
                           "Chrome : Office de Windows ou Office en ligne."))
        self.win = win
        status = lucarne("status") or {}
        ext = status.get("extension", {})
        self.ext_id = ext.get("id")
        self.links_log = Path(status.get("linksLog", Path.home() / ".cache/lucarne/links.log"))
        box = self.box = plain_card()  # premier bloc, avant les titres : sa propre carte
        self.column.append(box)
        self.first_card(box)

        loaded, version = extension_state(self.ext_id)
        want = ext.get("version")
        state = Gtk.Label(xalign=0, wrap=True, hexpand=True)
        if not loaded:
            state.set_label(_("L'extension « Lucarne » n'est pas encore chargée dans Chrome."))
        elif version and version != want:
            state.set_label(_("Extension chargée en version {version} : rechargez l'extension pour passer à la version "
                              "{want}.",
                               version=version, want=want))
        else:
            state.set_label(_("Extension chargée. Après une mise à jour (version actuelle {want}), "
                              "cliquez sur ⟳ dans la page des extensions.", want=want))
        btn = Gtk.Button(label=_("Ouvrir les extensions de Chrome"), valign=Gtk.Align.CENTER)
        btn.connect("clicked", lambda *_: self.open_extensions())
        top = Gtk.Box(spacing=12)
        top.append(state)
        top.append(btn)
        box.append(top)
        box.append(dim(_("Première installation : activez le mode développeur, cliquez sur « Charger l'extension non "
                         "empaquetée » et sélectionnez le dossier {path}. Après une mise à jour : cliquez sur le "
                         "bouton ⟳ de l'extension.", path=ext.get("path"))))

        self.header(_("Ouvrir les documents dans"))
        box = self.box
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        lb.add_css_class("rich-list")
        lb.add_css_class("card")
        lb.add_css_class("rounded")
        config = (lucarne("config", "get") or {}).get("apps", {})
        for app in status.get("vmApps", {}):
            name = status.get("apps", {}).get(app, app)
            keys = [k for k, _ in CHOICES]
            drop = Gtk.DropDown.new_from_strings([_(t) for _k, t in CHOICES])
            cur = config.get(app, {}).get("target", "web")
            drop.set_selected(keys.index(cur) if cur in keys else 1)

            def on(d, _p, app=app, keys=keys):
                if lucarne("config", "set", app, "target", keys[d.get_selected()]) is None:
                    self.win.notify(_("Lucarne n'a pas pu enregistrer le réglage"))
                else:
                    self.win.notify(_("Réglage enregistré et transmis immédiatement à l'extension"))
            drop.connect("notify::selected", on)
            sub = _("Outlook de bureau s'ouvre sans le message cliqué") if app == "outlook" else ""
            r = row(name, sub, drop)
            r.set_margin_start(6)
            r.set_margin_end(6)
            lb.append(r)
        box.append(lb)
        box.append(dim(_("Office de Windows reçoit l'adresse du fichier lui-même : l'extension retrouve cette adresse "
                         "grâce à votre session SharePoint dans Chrome.")))

        self.header(_("Derniers liens"))
        box = self.box
        self.log = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.log.add_css_class(Granite.STYLE_CLASS_SMALL_LABEL)
        box.append(self.log)
        self.connect("map", lambda *_: self.show_log())

    def show_log(self):
        try:
            lines = self.links_log.read_text().splitlines()[-5:]
        except OSError:
            lines = []
        out = []
        for ln in reversed(lines):
            parts = ln.split(" | ")
            if len(parts) >= 5:
                ok = _("ouvert en fichier") if parts[4] not in ("None", "") else _("adresse du fichier introuvable")
                out.append(_("{when}  {app} : {result}", when=parts[0][:19], app=parts[1], result=ok))
        self.log.set_label("\n".join(out) or _("Aucun lien ouvert pour l'instant."))

    def open_extensions(self):
        subprocess.Popen(["google-chrome", "chrome://extensions/?id=" + (self.ext_id or "")], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
