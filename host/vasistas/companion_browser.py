"""Page « Navigateur » de l'application compagnon : l'extension Chrome du projet m365-linux.

L'extension renvoie les documents Microsoft 365 cliqués dans Chrome vers une appli : en ligne
(appli Microsoft 365 du bureau) ou Office de la VM. Les réglages sont ceux de m365-linux
(~/.config/m365-linux/config.json, clé `target` par appli), partagés avec la fenêtre de
l'extension et l'appli Réglages Microsoft 365.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Granite", "7.0")
from gi.repository import Granite, Gtk  # noqa: E402

from .companion_common import Page, dim, row  # noqa: E402

def _m365_dir():
    """Dossier du projet m365-linux : VASISTAS_M365, config.json « m365_linux_dir », ou installé
    dans ~/.local/share/m365-linux. Sans lui, la page n'apparaît pas."""
    if os.environ.get("VASISTAS_M365"):
        return Path(os.environ["VASISTAS_M365"])
    try:
        from .vm import load_config
        if load_config().get("m365_linux_dir"):
            return Path(load_config()["m365_linux_dir"]).expanduser()
    except (OSError, ValueError):
        pass
    return Path.home() / ".local/share/m365-linux"


M365 = _m365_dir()
EXT_ID = "igknaiiijofdhmongnogaielfncjnnlc"
LINKS_LOG = Path.home() / ".cache/m365-linux/links.log"
CHOICES = [("vm", "Windows (Vasistas)"), ("web", "En ligne")]


def _config_module():
    sys.path.insert(0, str(M365 / "bin"))
    try:
        import m365_config
        return m365_config
    except ImportError:
        return None
    finally:
        sys.path.pop(0)


def available():
    return (M365 / "extension" / "manifest.json").exists() and _config_module() is not None


def extension_state():
    """(chargée, version) d'après les préférences de Chrome ; version None si Chrome ne la note pas
    (extension non empaquetée : relue sur le disque à chaque rechargement)."""
    for name in ("Secure Preferences", "Preferences"):
        try:
            prefs = json.loads((Path.home() / ".config/google-chrome/Default" / name).read_text())
        except (OSError, ValueError):
            continue
        e = prefs.get("extensions", {}).get("settings", {}).get(EXT_ID)
        if e:
            return True, e.get("manifest", {}).get("version")
    return False, None


class BrowserPage(Page):
    __gtype_name__ = "VasistasBrowserPage"

    def __init__(self, win):
        super().__init__("web-browser", "Navigateur",
                         "Documents Word, Excel et PowerPoint cliqués dans Chrome : ouverts dans Office "
                         "de Windows ou en ligne.")
        self.win = win
        cfg_mod = _config_module()
        box = self.box

        loaded, version = extension_state()
        want = json.loads((M365 / "extension" / "manifest.json").read_text()).get("version")
        state = Gtk.Label(xalign=0, wrap=True, hexpand=True)
        if not loaded:
            state.set_label("Extension « Microsoft 365 pour elementary » pas encore chargée dans Chrome.")
        elif version and version != want:
            state.set_label(f"Extension chargée en version {version} : rechargez-la pour passer à la {want}.")
        else:
            state.set_label(f"Extension chargée. Après une mise à jour (version actuelle {want}), "
                            "cliquez sur ⟳ dans la page des extensions.")
        btn = Gtk.Button(label="Ouvrir les extensions de Chrome", valign=Gtk.Align.CENTER)
        btn.connect("clicked", lambda *_: self.open_extensions())
        top = Gtk.Box(spacing=12)
        top.append(state)
        top.append(btn)
        box.append(top)
        box.append(dim(f"Première fois : mode développeur, « Charger l'extension non empaquetée », dossier "
                       f"{M365 / 'extension'}. Après une mise à jour : bouton ⟳ de l'extension."))

        box.append(Granite.HeaderLabel.new("Ouvrir les documents dans"))
        lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        lb.add_css_class("rich-list")
        lb.add_css_class("card")
        lb.add_css_class("rounded")
        config = cfg_mod.load()
        for app in cfg_mod.VM_APPS:
            name = cfg_mod.APPS[app][0]
            keys = [k for k, _ in CHOICES]
            drop = Gtk.DropDown.new_from_strings([t for _, t in CHOICES])
            cur = config["apps"][app].get("target", "web")
            drop.set_selected(keys.index(cur) if cur in keys else 1)

            def on(d, _p, app=app, keys=keys):
                cfg_mod.set_value(app, "target", keys[d.get_selected()])
                self.win.notify("Réglage enregistré, l'extension le reçoit tout de suite")
            drop.connect("notify::selected", on)
            sub = "Outlook de bureau s'ouvre sans le message cliqué" if app == "outlook" else ""
            r = row(name, sub, drop)
            r.set_margin_start(6)
            r.set_margin_end(6)
            lb.append(r)
        box.append(lb)
        box.append(dim("Office de Windows reçoit l'adresse du fichier lui-même : l'extension la retrouve "
                       "avec votre session SharePoint dans Chrome."))

        box.append(Granite.HeaderLabel.new("Derniers liens"))
        self.log = Gtk.Label(xalign=0, wrap=True, selectable=True)
        self.log.add_css_class(Granite.STYLE_CLASS_SMALL_LABEL)
        box.append(self.log)
        self.connect("map", lambda *_: self.show_log())

    def show_log(self):
        try:
            lines = LINKS_LOG.read_text().splitlines()[-5:]
        except OSError:
            lines = []
        out = []
        for ln in reversed(lines):
            parts = ln.split(" | ")
            if len(parts) >= 5:
                ok = "ouvert en fichier" if parts[4] not in ("None", "") else "adresse du fichier introuvable"
                out.append(f"{parts[0][:19]}  {parts[1]} : {ok}")
        self.log.set_label("\n".join(out) or "Aucun lien pour l'instant.")

    def open_extensions(self):
        subprocess.Popen(["google-chrome", "chrome://extensions/?id=" + EXT_ID], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
