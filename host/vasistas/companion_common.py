"""Éléments communs aux pages de l'application compagnon (GTK 4 + Granite)."""

import base64
import os
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Granite", "7.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Granite, Gtk  # noqa: E402

from . import control, desktop, vm  # noqa: E402

RESOURCES = [
    ("battery", "Économie d'énergie", "4 cœurs, 6 Go de mémoire : la batterie tient plus longtemps"),
    ("balanced", "Équilibré", "8 cœurs, 8 Go de mémoire"),
    ("performance", "Performances", "12 cœurs, 12 Go de mémoire : pour Power BI et les gros classeurs"),
]


class Page(Granite.SimpleSettingsPage):
    """Page au format des Paramètres d'elementary : icône, titre, description, puis le contenu
    (self.box) et, en bas, les boutons d'action (self.get_action_area())."""
    __gtype_name__ = "VasistasPage"

    def __init__(self, icon, title, description, activatable=False):
        super().__init__(icon_name=icon, title=title, description=description, activatable=activatable)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        self.get_content_area().attach(self.box, 0, 0, 1, 1)

    def header(self, text):
        label = Granite.HeaderLabel.new(text)
        self.box.append(label)
        return label

    def add(self, widget):
        self.box.append(widget)
        return widget

    def update(self, state):
        """État de Windows relu toutes les 2 s par la fenêtre ({pid, host})."""


def row(title, subtitle, widget):
    """Réglage : titre et explication à gauche, contrôle à droite."""
    box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6)
    labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
    labels.append(Gtk.Label(label=title, xalign=0, wrap=True))
    if subtitle:
        s = Gtk.Label(label=subtitle, xalign=0, wrap=True)
        s.add_css_class(Granite.STYLE_CLASS_DIM_LABEL)
        s.add_css_class(Granite.STYLE_CLASS_SMALL_LABEL)
        labels.append(s)
    widget.set_valign(Gtk.Align.CENTER)
    box.append(labels)
    box.append(widget)
    return box


def dim(text):
    lbl = Gtk.Label(label=text, xalign=0, wrap=True)
    lbl.add_css_class(Granite.STYLE_CLASS_DIM_LABEL)
    lbl.add_css_class(Granite.STYLE_CLASS_SMALL_LABEL)
    return lbl


def card():
    """Liste encadrée (carte arrondie) pour des éléments de même nature."""
    lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    lb.add_css_class(Granite.STYLE_CLASS_RICH_LIST)
    lb.add_css_class(Granite.STYLE_CLASS_CARD)
    lb.add_css_class(Granite.STYLE_CLASS_ROUNDED)
    return lb


def clear(container):
    child = container.get_first_child()
    while child:
        nxt = child.get_next_sibling()
        container.remove(child)
        child = nxt


def texture_from_b64(data):
    try:
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(base64.b64decode(data)))
    except (GLib.Error, ValueError):
        return None


def host_ready():
    """État de l'hôte : None s'il ne tourne pas, sinon sa réponse à status."""
    if not control.SOCKET.exists():
        return None
    try:
        return control.request({"status": True}, timeout=0.5)
    except OSError:
        return None


def guest_ready(state):
    host = state.get("host") or {}
    return bool(state.get("pid") and host.get("guest_ready"))


def spawn(*args):
    """Lance une commande vasistas détachée (hôte, lancement d'application)."""
    env = dict(os.environ, PYTHONPATH=str(desktop.HOST_DIR))
    log = open(vm.DATA / "host.log", "ab")
    subprocess.Popen([sys.executable, "-m", "vasistas", *args], env=env, start_new_session=True,
                     stdout=log, stderr=log)
