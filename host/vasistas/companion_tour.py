"""Accompagnement dans le compagnon : visite guidée (4 écrans) et fenêtre « Quoi de neuf »."""

from gi.repository import Granite, Gtk

from . import i18n, whatsnew
from .i18n import N_, _
from .version import VERSION
from .winctl import APP_ID

# (icône, titre, texte)
TOUR = [
    ("applications-other", N_("Ouvrir une application"),
     N_("Vos applications Windows sont dans le menu Applications, avec les autres applications. Cliquez sur une "
        "application : Windows démarre automatiquement si nécessaire, puis la fenêtre s'ouvre sur votre bureau, avec "
        "son icône dans le dock. Un double-clic sur un document Word ou Excel ouvre également ce document dans "
        "Windows.")),
    ("folder", N_("Où sont mes fichiers ?"),
     N_("Windows voit vos dossiers Documents et Téléchargements de Linux comme des lecteurs. Les documents enregistrés "
        "dans ces dossiers restent stockés sous Linux, même si Windows revient à un point de restauration. La page "
        "Fichiers permet de partager d'autres dossiers avec Windows.")),
    ("video-display", N_("L'indicateur et les affichages"),
     N_("L'icône de Vasistas dans le panneau indique si Windows est en marche ou en veille. Si une fenêtre s'affiche "
        "mal après un changement d'écran, choisissez « Réinitialiser les affichages » dans le menu de cette icône : "
        "Vasistas redessine les fenêtres sans fermer vos applications.")),
    ("help-contents", N_("Où trouver de l'aide"),
     N_("Chaque page de Vasistas a un bouton « ? » qui ouvre le guide à la section correspondante (F1 ouvre le guide "
        "complet). En cas de problème, le diagnostic (page Aide et diagnostic) recherche la cause, corrige ce qui peut "
        "l'être et prépare un rapport sans données personnelles pour signaler le problème.")),
]


class TourWindow(Gtk.Window):
    """Visite guidée : quatre écrans courts, points de progression, Précédent / Suivant / Commencer."""

    def __init__(self, parent=None):
        super().__init__(title=_("Visite guidée"), transient_for=parent, modal=True, default_width=560,
                         default_height=420, icon_name=APP_ID)
        header = Gtk.HeaderBar()
        header.add_css_class(Granite.STYLE_CLASS_FLAT)
        self.set_titlebar(header)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT, vexpand=True)
        for i, (icon, title, text) in enumerate(TOUR):
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_start=36, margin_end=36,
                          margin_top=12, valign=Gtk.Align.CENTER)
            box.append(Gtk.Image(icon_name=icon, pixel_size=96))
            t = Gtk.Label(label=_(title), wrap=True, justify=Gtk.Justification.CENTER)
            t.add_css_class(Granite.STYLE_CLASS_H2_LABEL)
            box.append(t)
            body = Gtk.Label(label=_(text), wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=60)
            box.append(body)
            self.stack.add_named(box, str(i))
        self.index = 0

        self.back = Gtk.Button(label=_("Précédent"))
        self.back.connect("clicked", lambda *_a: self.go(-1))
        self.next = Gtk.Button(label=_("Suivant"))
        self.next.add_css_class(Granite.STYLE_CLASS_SUGGESTED_ACTION)
        self.next.connect("clicked", lambda *_a: self.go(1))
        self.dots = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.dot_images = []
        for _i in TOUR:
            img = Gtk.Image(icon_name="pager-checked-symbolic", pixel_size=8)
            self.dots.append(img)
            self.dot_images.append(img)
        bar = Gtk.CenterBox(margin_start=18, margin_end=18, margin_top=12, margin_bottom=18)
        bar.set_start_widget(self.back)
        bar.set_center_widget(self.dots)
        bar.set_end_widget(self.next)
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(self.stack)
        root.append(bar)
        self.set_child(root)
        self.update_nav()

    def go(self, step):
        if self.index + step >= len(TOUR):
            self.close()
            return
        self.index = max(0, self.index + step)
        self.stack.set_visible_child_name(str(self.index))
        self.update_nav()

    def update_nav(self):
        self.back.set_visible(self.index > 0)
        self.next.set_label(_("Commencer") if self.index == len(TOUR) - 1 else _("Suivant"))
        for i, img in enumerate(self.dot_images):
            img.set_opacity(1.0 if i == self.index else 0.3)
        self.next.grab_focus()


class WhatsNewWindow(Gtk.Window):
    """Nouveautés des versions (toutes, ou seulement celles pas encore vues)."""

    def __init__(self, parent=None, items=None):
        super().__init__(title=_("Quoi de neuf"), transient_for=parent, modal=True, default_width=560,
                         default_height=520, icon_name=APP_ID)
        header = Gtk.HeaderBar()
        header.add_css_class(Granite.STYLE_CLASS_FLAT)
        self.set_titlebar(header)
        items = items if items is not None else whatsnew.entries(i18n.current(), upto=VERSION) \
            or whatsnew.entries(i18n.current())
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_start=24, margin_end=24,
                      margin_top=12, margin_bottom=12)
        top = Gtk.Box(spacing=16)
        top.append(Gtk.Image(icon_name=APP_ID, pixel_size=64))
        title = Gtk.Label(label=_("Quoi de neuf dans Vasistas"), xalign=0, wrap=True, hexpand=True)
        title.add_css_class(Granite.STYLE_CLASS_H2_LABEL)
        top.append(title)
        box.append(top)
        for ver, points in items:
            box.append(Granite.HeaderLabel.new(_("Version {v}", v=ver)))
            for p in points:
                row = Gtk.Box(spacing=10, margin_top=2)
                row.append(Gtk.Label(label="•", valign=Gtk.Align.START))
                row.append(Gtk.Label(label=p, xalign=0, wrap=True, hexpand=True, selectable=True))
                box.append(row)
        if not items:
            box.append(Gtk.Label(label=_("Rien de neuf pour cette version."), xalign=0))
        scroll = Gtk.ScrolledWindow(child=box, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        ok = Gtk.Button(label=_("Fermer"), halign=Gtk.Align.END, margin_end=24, margin_bottom=18, margin_top=6)
        ok.add_css_class(Granite.STYLE_CLASS_SUGGESTED_ACTION)
        ok.connect("clicked", lambda *_a: self.close())
        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        root.append(scroll)
        root.append(ok)
        self.set_child(root)
