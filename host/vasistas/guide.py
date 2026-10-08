"""Guide de démarrage rapide (fenêtre ouverte depuis l'application compagnon et l'assistant).

GuideWindow(parent, section="restore") ouvre le guide sur une section précise ; le bouton « ? »
de chaque page du compagnon y mène (PAGE_SECTIONS)."""

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Granite", "7.0")
gi.require_version("Graphene", "1.0")
from gi.repository import GLib, Granite, Graphene, Gtk  # noqa: E402

from .i18n import N_, _  # noqa: E402
from .version import ISSUES  # noqa: E402

# (clé, icône, titre, texte) ; textes traduits à l'affichage
SECTIONS = [
    ("open-app", "applications-other", N_("Ouvrir une application Windows"),
     N_("Word, Excel, Outlook et les autres applications Windows figurent dans le menu Applications, comme les "
        "applications Linux. À la première ouverture d'une application, Windows démarre automatiquement (environ "
        "trente secondes) ; les ouvertures suivantes sont presque immédiates. Chaque fenêtre Windows s'affiche comme "
        "une fenêtre du bureau, avec son icône dans le dock.")),
    ("open-file", "document-open", N_("Ouvrir un fichier"),
     N_("Un double-clic sur un document Word, Excel, PowerPoint ou Power BI dans l'application Fichiers ouvre ce "
        "document dans l'application Windows correspondante. Dans Vasistas, la page Fichiers, onglet « Ouvrir avec "
        "Windows », définit les types de fichiers concernés.")),
    ("files", "folder", N_("Où sont mes fichiers ?"),
     N_("Windows affiche vos dossiers Linux Documents et Téléchargements comme des lecteurs, nommés « Documents "
        "(Linux) » et « Téléchargements (Linux) ». Les documents enregistrés dans ces lecteurs sont accessibles depuis "
        "Linux. La page Fichiers de Vasistas permet de partager d'autres dossiers et de relier les dossiers Windows "
        "(Documents, Images…) aux dossiers Linux correspondants. Pour un fichier situé hors des dossiers partagés, "
        "Vasistas propose d'ouvrir une copie.")),
    ("displays", "video-display", N_("Écrans et fenêtres"),
     N_("Chaque fenêtre Windows peut être placée sur l'écran de votre choix, comme une fenêtre Linux, et une "
        "application peut s'ouvrir chaque fois sur le même écran. Si des fenêtres Windows s'affichent mal (floues, "
        "décalées ou mal placées) après un changement d'écran, la commande « Réinitialiser les affichages », "
        "disponible dans l'indicateur du panneau et dans l'application compagnon, redessine ces fenêtres.")),
    ("indicator", "preferences-desktop-display", N_("Indicateur du panneau"),
     N_("L'icône de Vasistas dans le panneau, en haut de l'écran, indique l'état de Windows (en marche, en veille, "
        "arrêté) et donne accès aux actions courantes : ouvrir une application, réinitialiser les affichages, mettre "
        "Windows en veille ou l'arrêter, ouvrir Vasistas.")),
    ("clipboard", "edit-paste", N_("Copier et coller"),
     N_("Le presse-papiers est partagé entre Linux et Windows : le texte, le texte mis en forme et les images copiés "
        "dans l'un peuvent être collés dans l'autre.")),
    ("keyboard", "input-keyboard", N_("Clavier"),
     N_("Les raccourcis clavier sont transmis à la fenêtre Windows active (Ctrl+C, Ctrl+S, Alt+Tab dans une "
        "application…). La touche Super reste réservée au bureau Linux.")),
    ("sleep", "preferences-system-power", N_("Veille et arrêt"),
     N_("Lorsqu'aucune fenêtre Windows n'est utilisée pendant un certain temps, Windows se met automatiquement en "
        "veille et libère les ressources de l'ordinateur ; Windows sort de veille au premier clic. L'Accueil de "
        "Vasistas permet de mettre Windows en veille, de l'arrêter ou de le redémarrer. La page Affichage règle les "
        "délais de mise en veille et, dans ses options avancées, les réglages détaillés.")),
    ("updates", "system-software-update", N_("Mises à jour"),
     N_("Windows n'installe pas ses mises à jour pendant que vous travaillez : lancez Windows Update depuis la page "
        "Windows de Vasistas environ une fois par mois. Vasistas recherche automatiquement ses propres nouvelles "
        "versions ; le bouton « Rechercher maintenant » de la page Préférences lance une recherche immédiate.")),
    ("restore", "document-revert", N_("Points de restauration"),
     N_("Un point de restauration enregistre l'état de tout le disque de Windows. Créez-en un dans l'onglet "
        "Restauration avant un changement risqué. Si vous cochez l'option, Vasistas crée aussi automatiquement un "
        "point de restauration avant Windows Update et avant l'installation d'une application, et conserve les trois "
        "derniers points automatiques ; les points de restauration créés manuellement sont conservés jusqu'à ce que "
        "vous les supprimiez. Si Windows ne fonctionne plus correctement après un changement, revenez au point de "
        "restauration créé avant ce changement. Les modifications faites dans Windows depuis ce point sont perdues, "
        "mais pas vos fichiers des dossiers partagés (Documents, Téléchargements…), qui restent sous Linux. Chaque "
        "point de restauration occupe de l'espace sur le disque.")),
    ("diagnose", "dialog-information", N_("Diagnostic"),
     N_("La page Aide et diagnostic vérifie ce dont Vasistas a besoin : virtualisation, paquets, espace "
        "disque, mémoire, connexion avec Windows, dossiers partagés, son, lanceurs du menu. Un bouton « Réparer » "
        "apparaît lorsque Vasistas peut corriger le problème automatiquement. Le bouton « Copier le rapport » prépare "
        "un texte sans données personnelles (ni nom, ni mot de passe, ni noms de fichiers) à joindre à un "
        "signalement.")),
    ("trouble", "dialog-question", N_("En cas de problème"),
     N_("Si une fenêtre Windows a disparu, rouvrez l'application depuis le menu Applications. Si Windows ne répond "
        "plus, choisissez « Redémarrer » dans la carte Windows de l'Accueil. Si un lecteur partagé ne répond plus, Vasistas le "
        "reconnecte automatiquement en moins d'une minute. Le diagnostic (page Aide et diagnostic) recherche la cause d'un "
        "problème et propose une réparation. Les problèmes peuvent être signalés sur {url}.")),
]
BY_KEY = {s[0]: s for s in SECTIONS}

# page du compagnon -> section du guide ouverte par son bouton « ? »
PAGE_SECTIONS = {
    "home": "open-app", "apps": "open-app", "files": "files", "display": "sleep", "windows": "updates", "restoration": "restore",
    "help": "diagnose", "settings": "updates", "about": "trouble",
}


def section_text(key):
    _key, _icon, title, text = BY_KEY[key]
    return _(title), _(text, url=ISSUES) if "{url}" in text else _(text)


class GuideWindow(Gtk.Window):
    def __init__(self, parent=None, section=None):
        super().__init__(title=_("Guide rapide"), transient_for=parent, default_width=620, default_height=680,
                         modal=False)
        header = Gtk.HeaderBar()
        header.add_css_class(Granite.STYLE_CLASS_FLAT)
        self.set_titlebar(header)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_start=24, margin_end=24,
                      margin_top=12, margin_bottom=24)
        title = Gtk.Label(label=_("Bien démarrer avec Vasistas"), xalign=0)
        title.add_css_class(Granite.STYLE_CLASS_H1_LABEL)
        box.append(title)
        self.items = {}
        for key, icon, _head, _text in SECTIONS:
            head, text = section_text(key)
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
            self.items[key] = item
        self.box = box
        self.scroll = Gtk.ScrolledWindow(child=box, hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.set_child(self.scroll)
        if section in self.items:
            self.connect("map", lambda *_a: GLib.timeout_add(100, self.show_section, section))

    def show_section(self, key):
        """Fait défiler jusqu'à la section `key`."""
        item = self.items.get(key)
        if item is None:
            return False
        origin = Graphene.Point()
        origin.init(0, 0)
        ok, point = item.compute_point(self.box, origin)
        if ok:
            self.scroll.get_vadjustment().set_value(max(0, point.y - 12))
        return False
