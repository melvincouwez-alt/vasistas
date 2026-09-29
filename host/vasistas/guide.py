"""Guide de démarrage rapide (fenêtre ouverte depuis l'application compagnon et l'assistant)."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Granite", "7.0")
from gi.repository import Granite, Gtk  # noqa: E402

from .version import ISSUES  # noqa: E402

SECTIONS = [
    ("applications-other", "Ouvrir une application Windows",
     "Word, Excel, Outlook et les autres sont dans le menu Applications, comme les applications Linux. "
     "La première ouverture démarre Windows (une trentaine de secondes) ; les suivantes sont presque "
     "immédiates. Chaque fenêtre Windows est une fenêtre du bureau, avec son icône dans le dock."),
    ("document-open", "Ouvrir un fichier",
     "Un double-clic sur un document Word, Excel, PowerPoint ou Power BI dans Fichiers l'ouvre dans "
     "l'application Windows. La page Fichiers de Vasistas choisit les types concernés."),
    ("folder", "Où sont mes fichiers ?",
     "Windows voit vos dossiers Documents et Téléchargements comme des lecteurs (Documents (Linux), "
     "Téléchargements (Linux)). Enregistrez-y vos documents pour les retrouver sous Linux. La page "
     "Dossiers en ajoute d'autres et peut relier les dossiers Documents, Images… de Windows à ceux de "
     "Linux. Un fichier rangé ailleurs est proposé en copie."),
    ("edit-paste", "Copier et coller",
     "Le presse-papiers est commun : texte, texte mis en forme et images passent de Linux à Windows et "
     "inversement."),
    ("input-keyboard", "Clavier",
     "Les raccourcis vont à la fenêtre Windows active (Ctrl+C, Ctrl+S, Alt+Tab dans une application…). "
     "La touche Super reste au bureau Linux."),
    ("preferences-system-power", "Veille et arrêt",
     "Sans fenêtre Windows utilisée pendant un moment, Windows se met en veille et rend la main ; il se "
     "réveille au premier clic. La page Windows de Vasistas le démarre, l'arrête ou le redémarre ; la "
     "page Performances règle la veille et la puissance donnée à Windows."),
    ("system-software-update", "Mises à jour",
     "Windows n'installe pas ses mises à jour pendant que vous travaillez : lancez Windows Update "
     "depuis la page Windows environ une fois par mois. Vasistas vérifie lui-même ses nouvelles versions "
     "(menu de la fenêtre, « Rechercher des mises à jour »)."),
    ("dialog-question", "En cas de problème",
     "Une fenêtre a disparu : rouvrez l'application depuis le menu. Rien ne répond : page Windows, "
     "« Redémarrer ». Un lecteur partagé ne répond plus : il est remonté de lui-même en moins d'une "
     "minute. Le journal se trouve dans ~/.local/share/vasistas/host.log ; les problèmes se signalent "
     f"sur {ISSUES}."),
]


class GuideWindow(Gtk.Window):
    def __init__(self, parent=None):
        super().__init__(title="Guide rapide", transient_for=parent, default_width=620, default_height=680,
                         modal=False)
        header = Gtk.HeaderBar()
        header.add_css_class(Granite.STYLE_CLASS_FLAT)
        self.set_titlebar(header)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_start=24, margin_end=24,
                      margin_top=12, margin_bottom=24)
        title = Gtk.Label(label="Bien démarrer avec Vasistas", xalign=0)
        title.add_css_class(Granite.STYLE_CLASS_H1_LABEL)
        box.append(title)
        for icon, head, text in SECTIONS:
            item = Gtk.Box(spacing=16)
            item.append(Gtk.Image(icon_name=icon, pixel_size=32, valign=Gtk.Align.START))
            texts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
            h = Gtk.Label(label=head, xalign=0)
            h.add_css_class(Granite.STYLE_CLASS_H3_LABEL)
            texts.append(h)
            t = Gtk.Label(label=text, xalign=0, wrap=True, selectable=True)
            texts.append(t)
            item.append(texts)
            box.append(item)
        self.set_child(Gtk.ScrolledWindow(child=box, hscrollbar_policy=Gtk.PolicyType.NEVER))
