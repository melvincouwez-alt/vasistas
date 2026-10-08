"""Éléments communs aux pages de l'application compagnon (GTK 4 + Granite)."""

import base64

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Granite", "7.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Granite, Gtk  # noqa: E402

# partagés avec l'indicateur du panneau (winctl.py, sans GTK)
from .i18n import _  # noqa: E402
from .winctl import RESOURCES, host_ready, spawn  # noqa: E402,F401


class Cards:
    """Contenu rangé en cartes : chaque header() ouvre une carte titrée, add() remplit la carte
    en cours (avant le premier titre : au-dessus des cartes). `self.column` reçoit les cartes."""

    def _init_cards(self, column):
        self.column = column
        self.box = column
        self._first_head = None
        self.card_actions = None

    def _adopt_head(self, head):
        """La première carte reçoit les boutons d'action de la section, en haut à droite."""
        if self._first_head is None:
            self._first_head = head
            if self.card_actions is not None:
                head.append(self.card_actions)

    def card_action_area(self):
        """Boutons d'action dans l'en-tête de la première carte (créée plus tard si besoin)."""
        if self.card_actions is None:
            self.card_actions = Gtk.Box(spacing=6, valign=Gtk.Align.CENTER, halign=Gtk.Align.END)
            if self._first_head is not None:
                self._first_head.append(self.card_actions)
        return self.card_actions

    def first_card(self, box):
        """Carte sans titre posée en tête : une rangée pour les boutons d'action."""
        head = Gtk.Box(halign=Gtk.Align.END)
        box.append(head)
        self._adopt_head(head)

    def header(self, text):
        box, head = dash_card(text)
        self.column.append(box)
        self.box = box
        self._adopt_head(head)
        return head.get_first_child()

    def subheader(self, text):
        """Intertitre dans la carte en cours."""
        label = Gtk.Label(label=text, xalign=0, margin_top=10)
        label.add_css_class(Granite.STYLE_CLASS_H4_LABEL)
        return self.add(label)

    def add(self, widget):
        """Dans la carte en cours ; avant tout titre, dans une carte sans titre."""
        if self.box is self.column:
            self.box = plain_card()
            self.column.append(self.box)
            self.first_card(self.box)
        self.box.append(widget)
        return widget

    def update(self, state):
        """État de Windows relu toutes les 2 s ({pid, host})."""


class Page(Cards, Gtk.Box):
    """Page du compagnon : en-tête (icône, titre, description, état, « ? »), cartes qui défilent,
    boutons d'action en bas (get_action_area)."""
    __gtype_name__ = "VasistasPage"

    def __init__(self, icon, title, description, activatable=False, two_columns=False):
        Gtk.Box.__init__(self, orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.head = Gtk.Box(spacing=14, margin_top=20, margin_start=24, margin_end=24, margin_bottom=4)
        self.head.append(Gtk.Image(icon_name=icon, pixel_size=40))
        texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        self.title_label = Gtk.Label(label=title, xalign=0)
        self.title_label.add_css_class(Granite.STYLE_CLASS_H2_LABEL)
        self.description_label = dim(description)
        self.description_label.set_visible(bool(description))
        texts.append(self.title_label)
        texts.append(self.description_label)
        self.head.append(texts)
        self.status_dot = Gtk.Box(valign=Gtk.Align.CENTER, visible=False)
        self.status_dot.add_css_class("live-dot")
        self.status_label = dim("")
        self.status_label.set_visible(False)
        self.head.append(self.status_dot)
        self.head.append(self.status_label)
        self.append(self.head)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=14, margin_bottom=24,
                         margin_start=24, margin_end=24)
        self.append(Gtk.ScrolledWindow(child=column, hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True))
        self.right_column = None
        if two_columns:
            # deux colonnes de cartes : header()/add() vont à gauche, puis à droite après use_right()
            cols = Gtk.Box(spacing=18, homogeneous=True)
            left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
            self.right_column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
            cols.append(left)
            cols.append(self.right_column)
            column.append(cols)
            column = left
        self.left_column = column
        self.actions = Gtk.Box(spacing=6, halign=Gtk.Align.END, margin_start=24, margin_end=24, margin_bottom=16,
                               margin_top=8, visible=False)
        self.append(self.actions)
        self._init_cards(column)

    def get_action_area(self):
        self.actions.set_visible(True)
        return self.actions

    def use_right(self):
        """Cartes suivantes dans la colonne de droite (page à deux colonnes)."""
        self.column = self.box = self.right_column

    def set_description(self, text):
        self.description_label.set_label(text)
        self.description_label.set_visible(bool(text))

    def set_status(self, text):
        self.status_label.set_label(text)
        self.status_label.set_visible(bool(text))
        self.status_dot.set_visible(bool(text))

    def set_status_type(self, kind):
        on = kind == Granite.SettingsPageStatusType.SUCCESS
        warn = kind == Granite.SettingsPageStatusType.WARNING
        self.status_dot.set_css_classes(["live-dot"] + ([] if on else ["warn"] if warn else ["off"]))

    def add_help(self, on_click):
        """Bouton « ? » à droite de l'en-tête : ouvre l'aide de la page."""
        btn = Gtk.Button(label="?", valign=Gtk.Align.CENTER, tooltip_text=_("Aide sur cette page"))
        btn.add_css_class(Granite.STYLE_CLASS_CIRCULAR)
        btn.connect("clicked", lambda *_a: on_click())
        self.head.append(btn)
        return btn


class Section(Cards, Gtk.Box):
    """Contenu d'une page rangé dans une autre (onglet, sous-page d'Avancé) : mêmes méthodes
    que Page (header, add, get_action_area, update), sans en-tête."""
    __gtype_name__ = "VasistasSection"

    def __init__(self, icon, title, description, activatable=False):
        Gtk.Box.__init__(self, orientation=Gtk.Orientation.VERTICAL, spacing=18, hexpand=True)
        self.icon, self.title, self.description = icon, title, description
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        self.append(column)
        self._init_cards(column)

    def get_action_area(self):
        return self.card_action_area()


class Tabs(Gtk.Box):
    """Onglets d'une page : sections (Section) sous un sélecteur centré ; seule la section
    affichée suit l'état de Windows."""

    def __init__(self, sections):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=18, hexpand=True)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vhomogeneous=False)
        for name, title, section in sections:
            self.stack.add_titled(section, name, title)
        self.state = {}
        self.stack.connect("notify::visible-child", lambda st, _p: st.get_visible_child() and
                           st.get_visible_child().update(self.state))
        if len(sections) > 1:
            self.append(Gtk.StackSwitcher(stack=self.stack, halign=Gtk.Align.CENTER))
        self.append(self.stack)

    def update(self, state):
        self.state = state
        child = self.stack.get_visible_child()
        if child is not None:
            child.update(state)

    def open_sub(self, name):
        child = self.stack.get_child_by_name(name)
        if child is not None:
            self.stack.set_visible_child(child)
        return child


class ColumnsPage(Page):
    """Page de sections (Section) côte à côte : `left` et `right` sont des listes de
    (nom, section) ; open_sub(nom) renvoie la section."""
    __gtype_name__ = "VasistasColumnsPage"

    def __init__(self, icon, title, description, left, right):
        super().__init__(icon, title, description, two_columns=True)
        self.sections = dict(left + right)
        for _n, section in left:
            self.column.append(section)
        self.use_right()
        for _n, section in right:
            self.column.append(section)

    def update(self, state):
        for section in self.sections.values():
            section.update(state)

    def open_sub(self, name):
        return self.sections.get(name)


class TabbedPage(Page):
    """Page faite d'onglets (Tabs)."""
    __gtype_name__ = "VasistasTabbedPage"

    def __init__(self, icon, title, description, sections):
        super().__init__(icon, title, description)
        self.tabs = Tabs(sections)
        self.column.append(self.tabs)

    def update(self, state):
        self.tabs.update(state)

    def open_sub(self, name):
        return self.tabs.open_sub(name)


# -- tableau de bord --

def badge(icon, color, small=False):
    """Icône symbolique sur une pastille discrète ; `color` (classe c-<couleur>) reste libre pour
    un thème qui voudrait des teintes."""
    img = Gtk.Image(icon_name=icon, valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER)
    img.add_css_class("pastille")
    img.add_css_class(f"c-{color}")
    if small:
        img.add_css_class("small")
    return img


def mode_chip(key, text):
    """Étiquette arrondie à la teinte du mode `key` (None : neutre)."""
    lbl = Gtk.Label(label=text, valign=Gtk.Align.CENTER)
    lbl.add_css_class("chip")
    lbl.add_css_class(f"mode-{key}" if key else "tile")
    return lbl


def dash_card(title, icon=None, color=None):
    """Carte : (carte, boîte d'en-tête) ; le contenu s'ajoute à la carte, sous l'en-tête. Avec
    `color`, l'icône est posée sur une pastille de cette couleur."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    for c in (Granite.STYLE_CLASS_CARD, Granite.STYLE_CLASS_ROUNDED, "dash-card"):
        box.add_css_class(c)
    head = Gtk.Box(spacing=12)
    if icon and color:
        head.append(badge(icon, color))
    elif icon:
        head.append(Gtk.Image(icon_name=icon, pixel_size=32))
    label = Gtk.Label(label=title, xalign=0, hexpand=True, wrap=True)
    label.add_css_class(Granite.STYLE_CLASS_H3_LABEL)
    head.append(label)
    box.append(head)
    return box, head


def advanced_card(*sections):
    """Carte repliée « Options avancées » qui contient les sections données."""
    box = plain_card()
    inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=12)
    for s in sections:
        inner.append(s)
    exp = Gtk.Expander(child=inner)
    title = Gtk.Label(label=_("Options avancées"), xalign=0)
    title.add_css_class(Granite.STYLE_CLASS_H3_LABEL)
    exp.set_label_widget(title)
    box.append(exp)
    return box


def plain_card():
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    for c in (Granite.STYLE_CLASS_CARD, Granite.STYLE_CLASS_ROUNDED, "dash-card"):
        box.add_css_class(c)
    return box


def tile(name):
    """Petite tuile : intitulé discret, valeur ; renvoie (tuile, étiquette de la valeur)."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
    box.add_css_class("tile")
    box.append(dim(name))
    value = Gtk.Label(xalign=0, label="–", ellipsize=3)
    value.add_css_class("tile-value")
    box.append(value)
    return box, value


def columns(right_width=380):
    """Deux colonnes de cartes dans une page qui défile : (page, gauche, droite)."""
    outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_top=24, margin_bottom=24,
                    margin_start=24, margin_end=24)
    cols = Gtk.Box(spacing=18)
    left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, hexpand=True)
    right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, width_request=right_width)
    cols.append(left)
    cols.append(right)
    outer.append(cols)
    page = Gtk.ScrolledWindow(child=outer, hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True, vexpand=True)
    return page, outer, left, right


def confirm(parent, title, detail, action_label, on_yes, destructive=True, icon="dialog-warning"):
    """Demande de confirmation ; bouton d'action rouge (destructif) à droite de « Annuler »."""
    from .i18n import _
    # le constructeur porte un autre nom selon la version de PyGObject
    make = getattr(Granite.MessageDialog, "with_image_from_icon_name", None) or \
        Granite.MessageDialog.new_with_image_from_icon_name
    dlg = make(title, detail, icon, Gtk.ButtonsType.NONE)
    dlg.set_transient_for(parent)
    dlg.set_modal(True)
    dlg.add_button(_("Annuler"), Gtk.ResponseType.CANCEL)
    btn = dlg.add_button(action_label, Gtk.ResponseType.ACCEPT)
    btn.add_css_class(Granite.STYLE_CLASS_DESTRUCTIVE_ACTION if destructive
                      else Granite.STYLE_CLASS_SUGGESTED_ACTION)
    dlg.set_default_response(Gtk.ResponseType.CANCEL)

    def answered(d, response):
        d.destroy()
        if response == Gtk.ResponseType.ACCEPT:
            on_yes()
    dlg.connect("response", answered)
    dlg.present()
    return dlg


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


def guest_ready(state):
    host = state.get("host") or {}
    return bool(state.get("pid") and host.get("guest_ready"))

