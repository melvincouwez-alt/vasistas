"""Catalogue des applications à installer dans Windows : Office et applications courantes.

Office passe par l'outil de déploiement Office (ODT) : `office_config()` écrit le fichier de
configuration, `office_script()` le script PowerShell qui télécharge setup.exe et l'exécute
(modèle : install/guest-scripts/office.ps1). Les autres applications passent par winget
(identifiants vérifiés dans le dépôt winget). Tout s'exécute dans l'invité par `exec`, sans
fenêtre ; les scripts finissent par une ligne « VASISTAS-RESULT {json} » que `parse_result()`
retrouve dans la sortie.

Les fonctions qui parlent à Windows prennent `request(dict, timeout)` en paramètre (par défaut
le socket de contrôle de l'hôte) : elles bloquent, à appeler hors du fil GTK.
"""

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path

from .i18n import N_, _

OFFICE_PS1 = Path(__file__).resolve().parents[2] / "install" / "guest-scripts" / "office.ps1"
MARKER = "VASISTAS-RESULT "
LONG_TIMEOUT_S = 3600

# -- Office --

# Canaux de mise à jour acceptés par l'ODT (attribut Channel de <Add>) : la « version »
# d'Office que l'utilisateur choisit. Libellés par produit : channel_choices().
CHANNELS = {
    "Current": N_("Canal actuel"),
    "MonthlyEnterprise": N_("Mensuel entreprise"),
    "SemiAnnual": N_("Semestriel"),
    "CurrentPreview": N_("Canal actuel (préversion)"),
    "BetaChannel": N_("Insider bêta"),
    "PerpetualVL2024": N_("Version perpétuelle 2024"),
}
CHANNEL_HINTS = {
    "Current": N_("nouveautés chaque mois, dès leur sortie"),
    "MonthlyEnterprise": N_("nouveautés une fois par mois, à date fixe"),
    "SemiAnnual": N_("nouveautés deux fois par an, pour les postes qui exigent une validation préalable"),
    "CurrentPreview": N_("nouveautés du mois suivant, en avant-première"),
    "BetaChannel": N_("préversions destinées aux tests, déconseillées en production"),
    "PerpetualVL2024": N_("correctifs de sécurité uniquement, sans nouveautés"),
}
_M365 = ["Current", "MonthlyEnterprise", "SemiAnnual", "CurrentPreview", "BetaChannel"]

# L'identifiant ODT dépend de l'abonnement : un mauvais identifiant installe Office mais
# l'activation échoue. Correspondances publiées par Microsoft (« Product IDs supported by the
# Office Deployment Tool », 2025) : les offres « Apps for business / enterprise » vendues sans
# Teams ont leur identifiant EEANoTeams.
OFFICE_PRODUCTS = [
    {"key": "business", "label": N_("Microsoft 365 Business Standard ou Premium"),
     "product_id": "O365BusinessRetail", "family": "m365", "channels": _M365, "default_channel": "Current",
     "note": N_("Abonnement Microsoft 365 d'entreprise : connexion au compte à la première ouverture.")},
    {"key": "business-apps", "label": "Microsoft 365 Apps for business",
     "product_id": "O365BusinessEEANoTeamsRetail", "family": "m365", "channels": _M365,
     "default_channel": "Current",
     "note": N_("Abonnement « Apps for business » (sans Teams) : connexion au compte à la première ouverture.")},
    {"key": "enterprise", "label": N_("Microsoft 365 E3 ou E5 / Office 365 E3 ou E5"),
     "product_id": "O365ProPlusRetail", "family": "m365", "channels": _M365, "default_channel": "Current",
     "note": N_("Abonnement Microsoft 365 d'entreprise : connexion au compte professionnel.")},
    {"key": "enterprise-apps", "label": "Microsoft 365 Apps for enterprise",
     "product_id": "O365ProPlusEEANoTeamsRetail", "family": "m365", "channels": _M365,
     "default_channel": "MonthlyEnterprise",
     "note": N_("Abonnement « Apps for enterprise » (sans Teams) : connexion au compte professionnel.")},
    {"key": "home", "label": N_("Microsoft 365 Famille ou Personnel"),
     "product_id": "O365HomePremRetail", "family": "m365", "channels": ["Current", "CurrentPreview", "BetaChannel"],
     "default_channel": "Current",
     "note": N_("Abonnement grand public : connexion au compte Microsoft à la première ouverture.")},
    {"key": "home2024", "label": N_("Office Famille 2024"),
     "product_id": "Home2024Retail", "family": "2024", "channels": ["Current"], "default_channel": "Current",
     "channel_labels": {"Current": N_("Version 2024 (correctifs mensuels)")},
     "note": N_("Achat unique : activation avec le compte Microsoft qui a acheté Office.")},
    {"key": "homebusiness2024", "label": N_("Office Famille et Entreprise 2024"),
     "product_id": "HomeBusiness2024Retail", "family": "2024", "channels": ["Current"], "default_channel": "Current",
     "channel_labels": {"Current": N_("Version 2024 (correctifs mensuels)")},
     "note": N_("Achat unique : activation avec le compte Microsoft qui a acheté Office.")},
    {"key": "ltsc2024", "label": N_("Office LTSC Professionnel Plus 2024"),
     "product_id": "ProPlus2024Volume", "family": "ltsc2024", "channels": ["PerpetualVL2024"],
     "default_channel": "PerpetualVL2024",
     "note": N_("Licence en volume : clé MAK, ou serveur KMS de l'organisation.")},
]
PRODUCTS_BY_KEY = {p["key"]: p for p in OFFICE_PRODUCTS}

# Applications d'une suite qu'on peut ne pas installer (élément <ExcludeApp>)
OFFICE_APPS = [
    {"id": "Word", "label": "Word", "excluded": False},
    {"id": "Excel", "label": "Excel", "excluded": False},
    {"id": "PowerPoint", "label": "PowerPoint", "excluded": False},
    {"id": "Outlook", "label": N_("Outlook (classique)"), "excluded": False},
    {"id": "OutlookForWindows", "label": N_("Nouvel Outlook"), "excluded": False},
    {"id": "OneNote", "label": "OneNote", "excluded": False},
    {"id": "Access", "label": "Access", "excluded": False},
    {"id": "Publisher", "label": N_("Publisher (retiré par Microsoft en octobre 2026)"), "excluded": True},
    # OneDrive se désigne « Groove » dans l'ODT ; Skype Entreprise, « Lync »
    {"id": "Groove", "label": "OneDrive", "excluded": True},
    {"id": "Lync", "label": N_("Skype Entreprise"), "excluded": True},
    {"id": "Teams", "label": N_("Teams (à installer séparément depuis la section "
                                "« Autres applications »)"), "excluded": True},
]
DEFAULT_EXCLUDED = [a["id"] for a in OFFICE_APPS if a["excluded"]]

# Visio et Project : identifiant selon la famille du produit principal
ADDONS = {
    "visio": {"label": N_("Visio Professionnel"),
              "ids": {"m365": "VisioProRetail", "2024": "VisioPro2024Retail", "ltsc2024": "VisioPro2024Volume"}},
    "project": {"label": N_("Project Professionnel"),
                "ids": {"m365": "ProjectProRetail", "2024": "ProjectPro2024Retail",
                        "ltsc2024": "ProjectPro2024Volume"}},
}

# Langues de Microsoft 365 Apps (« Languages, culture codes, and companion proofing
# languages », Microsoft Learn) : code ODT, nom, langue complète ? Une langue partielle
# (module linguistique) ne traduit qu'une partie d'Office : elle ne peut pas être la langue
# principale. Visio et Project suivent la liste complète, sauf les variantes régionales
# (VISIO_FALLBACK).
MATCH_OS = "MatchOS"
OFFICE_LANGUAGES = [
    (MATCH_OS, N_("Langue de Windows"), True),
    ("ar-sa", N_("Arabe"), True), ("bg-bg", N_("Bulgare"), True), ("zh-cn", N_("Chinois (simplifié)"), True),
    ("zh-tw", N_("Chinois (traditionnel)"), True), ("hr-hr", N_("Croate"), True), ("cs-cz", N_("Tchèque"), True),
    ("da-dk", N_("Danois"), True), ("nl-nl", N_("Néerlandais"), True), ("en-us", N_("Anglais (États-Unis)"), True),
    ("en-gb", N_("Anglais (Royaume-Uni)"), True), ("et-ee", N_("Estonien"), True), ("fi-fi", N_("Finnois"), True),
    ("fr-fr", N_("Français"), True), ("fr-ca", N_("Français (Canada)"), True), ("de-de", N_("Allemand"), True),
    ("el-gr", N_("Grec"), True), ("he-il", N_("Hébreu"), True), ("hi-in", N_("Hindi"), True), ("hu-hu", N_("Hongrois"), True),
    ("id-id", N_("Indonésien"), True), ("it-it", N_("Italien"), True), ("ja-jp", N_("Japonais"), True),
    ("kk-kz", N_("Kazakh"), True), ("ko-kr", N_("Coréen"), True), ("lv-lv", N_("Letton"), True),
    ("lt-lt", N_("Lituanien"), True), ("ms-my", N_("Malais"), True), ("nb-no", N_("Norvégien (bokmål)"), True),
    ("nn-no", N_("Norvégien (nynorsk)"), True), ("pl-pl", N_("Polonais"), True),
    ("pt-pt", N_("Portugais (Portugal)"), True), ("pt-br", N_("Portugais (Brésil)"), True), ("ro-ro", N_("Roumain"), True),
    ("ru-ru", N_("Russe"), True), ("sr-latn-rs", N_("Serbe (latin)"), True), ("sk-sk", N_("Slovaque"), True),
    ("sl-si", N_("Slovène"), True), ("es-es", N_("Espagnol"), True), ("es-mx", N_("Espagnol (Mexique)"), True),
    ("sv-se", N_("Suédois"), True), ("th-th", N_("Thaï"), True), ("tr-tr", N_("Turc"), True), ("uk-ua", N_("Ukrainien"), True),
    ("vi-vn", N_("Vietnamien"), True),
    ("af-za", N_("Afrikaans"), False), ("sq-al", N_("Albanais"), False), ("hy-am", N_("Arménien"), False),
    ("as-in", N_("Assamais"), False), ("az-latn-az", N_("Azéri (latin)"), False), ("eu-es", N_("Basque"), False),
    ("bn-bd", N_("Bengali (Bangladesh)"), False), ("bn-in", N_("Bengali (Inde)"), False),
    ("bs-latn-ba", N_("Bosniaque (latin)"), False), ("ca-es", N_("Catalan"), False),
    ("ca-es-valencia", N_("Valencien"), False), ("si-lk", N_("Cingalais"), False), ("gd-gb", N_("Gaélique écossais"), False),
    ("gl-es", N_("Galicien"), False), ("cy-gb", N_("Gallois"), False), ("ka-ge", N_("Géorgien"), False),
    ("gu-in", N_("Goudjarati"), False), ("ha-latn-ng", N_("Haoussa"), False), ("ig-ng", N_("Igbo"), False),
    ("ga-ie", N_("Irlandais"), False), ("is-is", N_("Islandais"), False), ("kn-in", N_("Kannada"), False),
    ("rw-rw", N_("Kinyarwanda"), False), ("ky-kg", N_("Kirghize"), False), ("kok-in", N_("Konkani"), False),
    ("lb-lu", N_("Luxembourgeois"), False), ("mk-mk", N_("Macédonien"), False), ("ml-in", N_("Malayalam"), False),
    ("mt-mt", N_("Maltais"), False), ("mi-nz", N_("Maori"), False), ("mr-in", N_("Marathi"), False),
    ("ne-np", N_("Népalais"), False), ("or-in", N_("Odia"), False), ("ur-pk", N_("Ourdou"), False),
    ("uz-latn-uz", N_("Ouzbek (latin)"), False), ("ps-af", N_("Pachto"), False), ("pa-in", N_("Pendjabi (gurmukhi)"), False),
    ("fa-ir", N_("Persan"), False), ("rm-ch", N_("Romanche"), False),
    ("sr-cyrl-rs", N_("Serbe (cyrillique, Serbie)"), False),
    ("sr-cyrl-ba", N_("Serbe (cyrillique, Bosnie-Herzégovine)"), False), ("nso-za", N_("Sotho du Nord"), False),
    ("sw-ke", N_("Swahili"), False), ("tt-ru", N_("Tatar (cyrillique)"), False), ("ta-in", N_("Tamoul"), False),
    ("te-in", N_("Télougou"), False), ("tn-za", N_("Tswana"), False), ("wo-sn", N_("Wolof"), False),
    ("xh-za", N_("Xhosa"), False), ("yo-ng", N_("Yoruba"), False), ("zu-za", N_("Zoulou"), False),
]
LANGUAGE_NAMES = {code: name for code, name, _ in OFFICE_LANGUAGES}
FULL_LANGUAGES = {code for code, _, full in OFFICE_LANGUAGES if full}
# Visio et Project n'existent pas dans ces variantes régionales : langue de base à la place
VISIO_FALLBACK = {"en-gb": "en-us", "fr-ca": "fr-fr", "es-mx": "es-es"}
# locale POSIX -> langue ODT quand la langue seule ne suffit pas à choisir
_LOCALE_DEFAULTS = {"en": "en-us", "es": "es-es", "fr": "fr-fr", "pt": "pt-pt", "sr": "sr-latn-rs",
                    "zh": "zh-cn", "nb": "nb-no", "no": "nb-no", "ca": "ca-es", "bn": "bn-in"}


def language_for_locale(locale):
    """Code de langue ODT d'une locale POSIX (fr_FR.UTF-8 -> fr-fr, de_AT -> de-de),
    « MatchOS » à défaut."""
    if not locale:
        return MATCH_OS
    base = locale.split(".")[0].split("@")[0].lower().replace("_", "-")
    if base in ("c", "posix"):
        return MATCH_OS
    if base in LANGUAGE_NAMES:
        return base
    lang = base.split("-")[0]
    if lang in _LOCALE_DEFAULTS:
        return _LOCALE_DEFAULTS[lang]
    same = [code for code, _, _ in OFFICE_LANGUAGES if code.split("-")[0] == lang]
    return same[0] if same else MATCH_OS


def channel_choices(product_key):
    """[(canal, libellé, précision)] proposés pour un produit, le canal conseillé en premier."""
    product = PRODUCTS_BY_KEY[product_key]
    labels = product.get("channel_labels", {})
    order = [product["default_channel"]] + [c for c in product["channels"] if c != product["default_channel"]]
    out = []
    for c in order:
        label = _(labels.get(c, CHANNELS[c]))
        if c == product["default_channel"] and len(order) > 1:
            label = _("{label} (conseillé)", label=label)
        out.append((c, label, _(CHANNEL_HINTS[c])))
    return out


def _check_languages(languages, primary_required=True):
    unknown = [l for l in languages if l not in LANGUAGE_NAMES]
    if unknown:
        raise ValueError(f"langue Office inconnue : {', '.join(unknown)}")
    if primary_required and languages[0] not in FULL_LANGUAGES:
        raise ValueError(f"{LANGUAGE_NAMES[languages[0]]} : langue partielle, impossible comme langue principale")


def office_config(product_key, channel=None, languages=None, exclude=None, addons=None, shared=False,
                  pidkey=None, proofing=None):
    """Fichier de configuration ODT (XML) : 64 bits, installation silencieuse, mises à jour
    actives.

    `languages` : langue principale d'abord (langue complète ou MatchOS), puis les langues
    supplémentaires, installées pour Office et ses modules (MatchOS par défaut).
    `proofing` : langues de vérification seules (correcteur, sans traduire l'interface), par
    le produit ProofingTools. `exclude` : identifiants de OFFICE_APPS (DEFAULT_EXCLUDED si
    None) ; `addons` : clés de ADDONS ; `shared` : activation pour ordinateur partagé ;
    `pidkey` : clé de produit (licences en volume MAK)."""
    product = PRODUCTS_BY_KEY.get(product_key)
    if product is None:
        raise ValueError(f"produit Office inconnu : {product_key}")
    channel = channel or product["default_channel"]
    if channel not in product["channels"]:
        raise ValueError(f"canal {channel} impossible pour {product['label']}")
    languages = list(dict.fromkeys(languages or [MATCH_OS]))
    _check_languages(languages)
    proofing = [l for l in dict.fromkeys(proofing or []) if l not in languages]
    if proofing:
        _check_languages(proofing, primary_required=False)
    if MATCH_OS in proofing:
        raise ValueError("MatchOS impossible pour les langues de vérification")
    exclude = DEFAULT_EXCLUDED if exclude is None else list(exclude)
    known = {a["id"] for a in OFFICE_APPS}
    unknown = [e for e in exclude if e not in known]
    if unknown:
        raise ValueError(f"application Office inconnue : {', '.join(unknown)}")

    root = ET.Element("Configuration")
    add = ET.SubElement(root, "Add", OfficeClientEdition="64", Channel=channel, AllowCdnFallback="True")

    def product_el(pid, langs, exclusions, key=None):
        attrs = {"ID": pid}
        if key:
            attrs["PIDKEY"] = key
        el = ET.SubElement(add, "Product", attrs)
        for lang in langs:
            # MatchOS échoue si Office ne connaît pas la langue de Windows : anglais en secours
            ET.SubElement(el, "Language", {"ID": lang, **({"Fallback": "en-us"} if lang == MATCH_OS else {})})
        for app in exclusions:
            ET.SubElement(el, "ExcludeApp", ID=app)
        return el

    product_el(product["product_id"], languages, exclude, pidkey)
    for name in addons or []:
        addon = ADDONS.get(name)
        if addon is None:
            raise ValueError(f"module Office inconnu : {name}")
        langs = list(dict.fromkeys(VISIO_FALLBACK.get(l, l) for l in languages))
        # Visio et Project installent OneDrive eux aussi : même exclusion pour eux
        product_el(addon["ids"][product["family"]], langs, ["Groove"] if "Groove" in exclude else [])
    if proofing:
        product_el("ProofingTools", proofing, [])
    for name, value in (("AUTOACTIVATE", "0"), ("FORCEAPPSHUTDOWN", "FALSE"),
                        ("SharedComputerLicensing", "1" if shared else "0"),
                        ("PinIconsToTaskbar", "FALSE")):
        ET.SubElement(root, "Property", Name=name, Value=value)
    ET.SubElement(root, "Updates", Enabled="TRUE")
    ET.SubElement(root, "Display", Level="None", AcceptEULA="TRUE")
    ET.indent(root)
    return ET.tostring(root, encoding="unicode") + "\n"


def office_script(config_xml):
    """Script PowerShell complet : télécharge l'ODT, écrit config_xml, lance l'installation."""
    if "\n'@" in config_xml:
        raise ValueError("configuration Office invalide")
    return f"$ConfigXml = @'\n{config_xml.rstrip()}\n'@\n" + OFFICE_PS1.read_text(encoding="utf-8")


def office_status_script():
    return r"""
$c = Get-ItemProperty 'HKLM:\SOFTWARE\Microsoft\Office\ClickToRun\Configuration' -ErrorAction SilentlyContinue
$apps = Get-ChildItem 'C:\Program Files\Microsoft Office\root\Office16' -Filter *.exe -ErrorAction SilentlyContinue |
    Where-Object Name -in 'WINWORD.EXE','EXCEL.EXE','POWERPNT.EXE','OUTLOOK.EXE','ONENOTE.EXE','MSACCESS.EXE','VISIO.EXE','WINPROJ.EXE' |
    ForEach-Object Name
$r = @{ installed = [bool]$c; products = @(if ($c.ProductReleaseIds) { $c.ProductReleaseIds -split ',' });
        version = $c.VersionToReport; channel = $c.UpdateChannel; cdn = $c.CDNBaseUrl; apps = @($apps) }
'VASISTAS-RESULT ' + ($r | ConvertTo-Json -Compress)
"""


# -- autres applications (winget) --

CATEGORIES = [N_("Bureautique"), N_("Données"), N_("PDF"), N_("Création"), N_("Communication"),
              N_("Développement")]

# Identifiants vérifiés avec `winget show --id … -e` (dépôt winget, septembre 2026).
#
# `variants` : versions proposées, la première par défaut (clé, identifiant winget, libellé).
# `locale` : comment choisir la langue de l'application.
#   None : l'installateur suit la langue de Windows (installateurs multilingues : Acrobat
#          Reader « MUI », Teams, Zoom…) ; winget --locale n'y sert à rien, aucun de ces
#          paquets ne publie d'installateur par langue.
#   {"custom": "…{lang}…", "languages": […]} : option de l'installateur passée par
#          winget --custom (Power BI : LANGUAGE=fr-FR ; Visual Studio et SSMS :
#          --addProductLang fr-FR, module linguistique en plus de la langue de Windows).
#   {"winget": […]} : winget --locale, pour un paquet qui publie un installateur par langue.
VS_LANGUAGES = ["cs-CZ", "de-DE", "en-US", "es-ES", "fr-FR", "it-IT", "ja-JP", "ko-KR", "pl-PL", "pt-BR",
                "ru-RU", "tr-TR", "zh-CN", "zh-TW"]
POWERBI_LANGUAGES = ["ar-SA", "bg-BG", "ca-ES", "cs-CZ", "da-DK", "de-DE", "el-GR", "en-US", "es-ES", "et-EE",
                     "eu-ES", "fi-FI", "fr-FR", "gl-ES", "he-IL", "hi-IN", "hr-HR", "hu-HU", "id-ID", "it-IT",
                     "ja-JP", "kk-KZ", "ko-KR", "lt-LT", "lv-LV", "ms-MY", "nb-NO", "nl-NL", "pl-PL", "pt-BR",
                     "pt-PT", "ro-RO", "ru-RU", "sk-SK", "sl-SI", "sr-Cyrl-RS", "sr-Latn-RS", "sv-SE",
                     "th-TH", "tr-TR", "uk-UA", "vi-VN", "zh-CN", "zh-TW"]
APPS = [
    {"key": "powerbi", "label": "Power BI Desktop", "category": N_("Données"),
     "description": N_("Rapports et tableaux de bord Power BI"), "icon": "office-chart-bar", "note": "",
     "variants": [("desktop", "Microsoft.PowerBI", "Power BI Desktop")],
     "locale": {"custom": "LANGUAGE={lang}", "languages": POWERBI_LANGUAGES}},
    {"key": "powerbi-report-builder", "label": "Power BI Report Builder", "category": N_("Données"),
     "description": N_("Rapports paginés Power BI"), "icon": "office-chart-bar", "note": "",
     "variants": [("default", "Microsoft.PowerBIReportBuilder", "Power BI Report Builder")], "locale": None},
    {"key": "ssms", "label": "SQL Server Management Studio", "category": N_("Données"),
     "description": N_("Administration et requêtes SQL Server"), "icon": "network-server", "note": "",
     "variants": [("22", "Microsoft.SQLServerManagementStudio.22", "SSMS 22"),
                  ("21", "Microsoft.SQLServerManagementStudio.21", "SSMS 21"),
                  ("20", "Microsoft.SQLServerManagementStudio", "SSMS 20")],
     "locale": {"custom": "--addProductLang {lang}", "languages": VS_LANGUAGES, "variants": ["22", "21"]}},
    {"key": "tableau", "label": "Tableau", "category": N_("Données"),
     "description": N_("Analyse et visualisation de données"), "icon": "x-office-spreadsheet", "note": "",
     "variants": [("desktop", "Tableau.Desktop", N_("Tableau Desktop (licence Tableau)")),
                  ("reader", "Tableau.Reader", N_("Tableau Reader (lecture seule, gratuit)"))], "locale": None},
    {"key": "power-automate", "label": N_("Power Automate pour le bureau"), "category": N_("Bureautique"),
     "description": N_("Automatisation des tâches Windows et Office"), "icon": "system-run",
     "note": N_("Connexion avec un compte Microsoft ou professionnel à la première ouverture."),
     "variants": [("default", "Microsoft.PowerAutomateDesktop", N_("Power Automate pour le bureau"))], "locale": None},
    {"key": "notepadpp", "label": "Notepad++", "category": N_("Bureautique"),
     "description": N_("Éditeur de texte et de code"), "icon": "accessories-text-editor", "note": "",
     "variants": [("default", "Notepad++.Notepad++", "Notepad++")], "locale": None},
    {"key": "7zip", "label": "7-Zip", "category": N_("Bureautique"),
     "description": N_("Gestion des archives 7z, zip et rar dans "
                       "l'Explorateur de fichiers"), "icon": "package-x-generic", "note": "",
     "variants": [("default", "7zip.7zip", "7-Zip")], "locale": None},
    {"key": "acrobat", "label": "Adobe Acrobat", "category": N_("PDF"),
     "description": N_("Lecture, signature et modification de PDF"), "icon": "application-pdf",
     "note": N_("Reader est gratuit ; Acrobat Pro nécessite un abonnement Adobe."),
     "variants": [("reader64", "Adobe.Acrobat.Reader.64-bit", N_("Acrobat Reader 64 bits")),
                  ("reader32", "Adobe.Acrobat.Reader.32-bit", N_("Acrobat Reader 32 bits (compatible avec les anciens "
                                                                 "modules)")),
                  ("pro", "Adobe.Acrobat.Pro", N_("Acrobat Pro (abonnement)"))], "locale": None},
    {"key": "pdfxchange", "label": "PDF-XChange", "category": N_("PDF"),
     "description": N_("Annotation et modification de PDF"), "icon": "application-pdf",
     "note": N_("Fonctions avancées payantes."),
     "variants": [("editor", "TrackerSoftware.PDF-XChangeEditor", "PDF-XChange Editor"),
                  ("pro", "TrackerSoftware.PDF-XChangePRO", "PDF-XChange PRO")], "locale": None},
    {"key": "creative-cloud", "label": "Adobe Creative Cloud", "category": N_("Création"),
     "description": N_("Photoshop, Illustrator, InDesign…"), "icon": "applications-graphics",
     "note": N_("Abonnement Adobe nécessaire."),
     "variants": [("default", "Adobe.CreativeCloud", "Adobe Creative Cloud")], "locale": None},
    {"key": "sketchup", "label": "SketchUp", "category": N_("Création"),
     "description": N_("Modélisation 3D"), "icon": "applications-graphics", "note": N_("Abonnement Trimble nécessaire."),
     "variants": [("2026", "Trimble.SketchUp.2026", "SketchUp 2026"),
                  ("2025", "Trimble.SketchUp.2025", "SketchUp 2025")], "locale": None},
    {"key": "design-review", "label": "Autodesk Design Review", "category": N_("Création"),
     "description": N_("Lecture et annotation de plans DWF et DWG"), "icon": "x-office-drawing", "note": "",
     "variants": [("default", "Autodesk.DesignReview", "Autodesk Design Review")], "locale": None},
    {"key": "teams", "label": "Microsoft Teams", "category": N_("Communication"),
     "description": N_("Réunions et conversations d'équipe"), "icon": "internet-chat", "note": "",
     "variants": [("work", "Microsoft.Teams", N_("Teams (travail ou école)")),
                  ("free", "Microsoft.Teams.Free", N_("Teams gratuit (compte personnel)"))], "locale": None},
    {"key": "zoom", "label": "Zoom Workplace", "category": N_("Communication"),
     "description": N_("Réunions vidéo"), "icon": "camera-web", "note": "",
     "variants": [("default", "Zoom.Zoom", "Zoom Workplace")], "locale": None},
    {"key": "visual-studio", "label": "Visual Studio", "category": N_("Développement"),
     "description": N_("Environnement de développement .NET et C++"), "icon": "applications-development",
     "note": N_("Community : gratuit pour les particuliers et les petites équipes ; Professional et Enterprise : sur "
                "abonnement."),
     "variants": [("community", "Microsoft.VisualStudio.Community", "Community 2026"),
                  ("professional", "Microsoft.VisualStudio.Professional", "Professional 2026"),
                  ("enterprise", "Microsoft.VisualStudio.Enterprise", "Enterprise 2026"),
                  ("community-2022", "Microsoft.VisualStudio.2022.Community", "Community 2022"),
                  ("professional-2022", "Microsoft.VisualStudio.2022.Professional", "Professional 2022"),
                  ("enterprise-2022", "Microsoft.VisualStudio.2022.Enterprise", "Enterprise 2022")],
     "locale": {"custom": "--addProductLang {lang}", "languages": VS_LANGUAGES}},
]
for _a in APPS:
    # identifiant de la version par défaut (compatibilité : une application = un paquet)
    _a["winget_id"] = _a["variants"][0][1]
APPS_BY_KEY = {a["key"]: a for a in APPS}

# Codes de retour de winget (doc « winget return codes ») traités comme un succès : paquet
# déjà présent, ou installé en attente de redémarrage
WINGET_OK = {
    0: N_("installé"),
    -1978335135: N_("déjà installé"),            # 0x8A150061 PACKAGE_ALREADY_INSTALLED
    -1978334963: N_("une autre version est installée"),  # 0x8A15010D INSTALL_ALREADY_INSTALLED
    # 0x8A150109 REBOOT_REQUIRED_TO_FINISH
    -1978334967: N_("redémarrage de Windows nécessaire pour terminer l'installation"),
}
WINGET_NO_APPLICABLE_INSTALLER = -1978335216   # 0x8A150010 : pas d'installateur « machine »

_WINGET_PS = r"""
$ErrorActionPreference = 'Continue'
$common = @('--accept-source-agreements', '--disable-interactivity')
function Test-Installed($id) {
    winget list --id $id -e @common *> $null
    return ($LASTEXITCODE -eq 0)
}
function Install-One($id, $scope) {
    $a = @('install', '--id', $id, '-e', '--silent', '--accept-package-agreements') + $common
    if ($scope) { $a += @('--scope', $scope) }
    if ($locale[$id]) { $a += @('--locale', $locale[$id]) }
    if ($custom[$id]) { $a += @('--custom', $custom[$id]) }
    & winget @a *> $null
    return $LASTEXITCODE
}
$res = [ordered]@{}
foreach ($id in $ids) {
    $code = 0
    if ($action -eq 'install') {
        $code = Install-One $id 'machine'
        # paquet sans installateur pour tout l'ordinateur : installation pour l'utilisateur
        if ($code -eq @NOAPPLICABLE@) { $code = Install-One $id $null }
    } elseif ($action -eq 'uninstall') {
        winget uninstall --id $id -e --silent @common *> $null
        $code = $LASTEXITCODE
    }
    $inst = Test-Installed $id
    $ok = switch ($action) {
        'install' { $inst -or ($code -in @(@OKCODES@)) }
        'uninstall' { -not $inst }
        default { $true }
    }
    $res[$id] = @{ ok = [bool]$ok; code = $code; installed = [bool]$inst }
}
'VASISTAS-RESULT ' + ($res | ConvertTo-Json -Compress)
"""
_SAFE_ARG = re.compile(r"^[A-Za-z0-9=_.:/ -]{1,120}$")
_SAFE_LOCALE = re.compile(r"^[A-Za-z]{2,3}(-[A-Za-z0-9]{2,8}){0,2}$")


def _ps_table(values):
    return "@{" + "; ".join(f"'{k}' = '{v}'" for k, v in values.items()) + "}"


def winget_script(ids, action, custom=None, locale=None):
    """Script PowerShell winget pour ces identifiants : install, uninstall ou status.
    `custom` : {identifiant: options de l'installateur} (winget --custom) ; `locale` :
    {identifiant: langue} (winget --locale)."""
    if action not in ("install", "uninstall", "status"):
        raise ValueError(f"action inconnue : {action}")
    ids = list(ids)
    custom, locale = dict(custom or {}), dict(locale or {})
    for i in ids + list(custom) + list(locale):
        if not i or "'" in i or " " in i:
            raise ValueError(f"identifiant winget invalide : {i!r}")
    for v in custom.values():
        if not _SAFE_ARG.match(v):
            raise ValueError(f"option d'installation invalide : {v!r}")
    for v in locale.values():
        if not _SAFE_LOCALE.match(v):
            raise ValueError(f"langue invalide : {v!r}")
    head = ("$ids = @(" + ", ".join(f"'{i}'" for i in ids) + ")\n" + f"$action = '{action}'\n"
            + f"$custom = {_ps_table(custom)}\n$locale = {_ps_table(locale)}\n")
    body = (_WINGET_PS.replace("@NOAPPLICABLE@", str(WINGET_NO_APPLICABLE_INSTALLER))
            .replace("@OKCODES@", ", ".join(str(c) for c in WINGET_OK)))
    return head + body


def variant_choices(key):
    """[(version, libellé)] d'une application du catalogue, la version par défaut en premier."""
    return [(v, label) for v, _, label in APPS_BY_KEY[key]["variants"]]


def language_choices(key, variant=None):
    """Langues proposées pour une application ([] : elle suit la langue de Windows)."""
    loc = APPS_BY_KEY[key].get("locale") or {}
    if "variants" in loc and (variant or APPS_BY_KEY[key]["variants"][0][0]) not in loc["variants"]:
        return []
    return list(loc.get("languages") or loc.get("winget") or [])


def app_language(key, lang):
    """Langue du catalogue (ll-CC) la plus proche de `lang` (fr-fr, fr_FR.UTF-8…), ou None."""
    if not lang:
        return None
    wanted = lang.split(".")[0].replace("_", "-").lower()
    langs = language_choices(key)
    for l in langs:
        if l.lower() == wanted:
            return l
    base = wanted.split("-")[0]
    return next((l for l in langs if l.lower().split("-")[0] == base), None)


def _install_plan(keys, variants=None, language=None):
    """(identifiants, custom, locale, {identifiant: clé}) pour des applications du catalogue."""
    variants = variants or {}
    ids, custom, locale, owner = [], {}, {}, {}
    for key in keys:
        app = APPS_BY_KEY[key]
        chosen = variants.get(key) or app["variants"][0][0]
        wid = next((w for v, w, _ in app["variants"] if v == chosen), None)
        if wid is None:
            raise ValueError(f"version inconnue pour {app['label']} : {chosen}")
        ids.append(wid)
        owner[wid] = key
        lang = app_language(key, language) if language_choices(key, chosen) else None
        loc = app.get("locale") or {}
        if lang and "custom" in loc:
            custom[wid] = loc["custom"].format(lang=lang)
        elif lang and "winget" in loc:
            locale[wid] = lang
    return ids, custom, locale, owner


def parse_result(out):
    """Dernier résultat JSON d'un script du catalogue dans une sortie `exec` ({} sinon)."""
    for line in reversed((out or "").splitlines()):
        line = line.strip().lstrip("\ufeff")
        if line.startswith(MARKER):
            line = line[len(MARKER):]
        elif not line.startswith("{"):
            continue
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    return {}


# -- exécution dans Windows --

def _request():
    from . import control
    return control.request


def _run(script, request, timeout):
    res = (request or _request())({"exec": script}, timeout=timeout)
    if "error" in res:
        raise RuntimeError(res["error"])
    parsed = parse_result(res.get("out"))
    if not parsed:
        tail = (res.get("out") or "").strip()[-400:]
        raise RuntimeError(tail or _("aucun résultat renvoyé par Windows (code {code})", code=res.get("code")))
    return parsed


def install_office(product_key, channel=None, languages=None, exclude=None, addons=None, shared=False,
                   pidkey=None, proofing=None, request=None):
    """Installe Office dans Windows (plusieurs minutes). Rend {code, ok, apps, products, version}."""
    xml = office_config(product_key, channel, languages, exclude, addons, shared, pidkey, proofing)
    return _run(office_script(xml), request, LONG_TIMEOUT_S)


# Canal d'une installation existante, d'après l'adresse de son CDN (UpdateChannel ou CDNBaseUrl)
CDN_CHANNELS = {
    "492350f6-3a01-4f97-b9c0-c7c6ddf67d60": "Current",
    "64256afe-f5d9-4f86-8936-8840a6a4f5be": "CurrentPreview",
    "5440fd1f-7ecb-4221-8110-145efaa6372f": "BetaChannel",
    "55336b82-a18d-4dd6-b5f6-9e5095c314a6": "MonthlyEnterprise",
    "7ffbc6bf-bc32-4f92-8982-f9dd17fd3114": "SemiAnnual",
    "b8f9b850-328d-4355-9145-c59439a0c4cf": "SemiAnnualPreview",
}


def office_status(request=None):
    """Office installé dans Windows : {installed, products, version, channel, apps}. `channel`
    est le nom du canal (clé de CHANNELS) quand l'adresse du CDN est connue."""
    res = _run(office_status_script(), request, 60)
    url = (res.get("channel") or res.get("cdn") or "").lower()
    res["channel"] = next((name for guid, name in CDN_CHANNELS.items() if guid in url), None)
    return res


def install_apps(keys, request=None, variants=None, language=None):
    """Installe des applications du catalogue : {clé: {ok, code, installed}}. `variants` :
    {clé: version} (version par défaut sinon) ; `language` : langue voulue (fr-FR, ou une
    locale), appliquée aux applications qui la proposent, les autres suivent Windows."""
    ids, custom, locale, owner = _install_plan(keys, variants, language)
    if not ids:
        return {}
    res = _run(winget_script(ids, "install", custom, locale), request, LONG_TIMEOUT_S)
    return {owner[i]: res.get(i, {"ok": False, "code": None, "installed": False}) for i in ids}


def uninstall_apps(keys, request=None):
    """Désinstalle toutes les versions installées de ces applications : {clé: {ok, …}}."""
    found = installed_variants(keys, request)
    ids = [w for k in keys for v, w, _ in APPS_BY_KEY[k]["variants"] if v in found.get(k, [])]
    if not ids:
        return {k: {"ok": True, "code": 0, "installed": False} for k in keys}
    res = _run(winget_script(ids, "uninstall"), request, LONG_TIMEOUT_S)
    out = {}
    for k in keys:
        mine = [res.get(w, {}) for v, w, _ in APPS_BY_KEY[k]["variants"] if w in res]
        out[k] = {"ok": all(r.get("ok") for r in mine), "code": next((r.get("code") for r in mine
                                                                     if r.get("code")), 0),
                  "installed": any(r.get("installed") for r in mine)}
    return out


def installed_variants(keys=None, request=None):
    """{clé: [versions installées]} pour les applications du catalogue (toutes si None)."""
    keys = [a["key"] for a in APPS] if keys is None else list(keys)
    ids = [w for k in keys for _, w, _ in APPS_BY_KEY[k]["variants"]]
    if not ids:
        return {}
    res = _run(winget_script(ids, "status"), request, 600)
    return {k: [v for v, w, _ in APPS_BY_KEY[k]["variants"] if res.get(w, {}).get("installed")] for k in keys}


def status(keys=None, request=None):
    """{clé: installée ?} (une version quelconque) pour les applications du catalogue."""
    return {k: bool(v) for k, v in installed_variants(keys, request).items()}


def describe_code(code):
    """Texte d'un code de retour winget, pour l'interface."""
    if code in WINGET_OK:
        return _(WINGET_OK[code])
    if code is None:
        return _("pas de réponse")
    return _("échec (code {code})", code=f"{code & 0xFFFFFFFF:#010x}")
