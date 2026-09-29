"""Images ISO officielles de Windows : choix de la version et de la langue, lien chez Microsoft,
téléchargement.

Deux sources, vérifiées le 2026-09-29 (jusqu'au lien et à la taille annoncée) :

- Pages grand public (software-download/windows11, windows10ISO) : ISO multi-édition. Microsoft
  ne publie pas de lien direct ; la page passe par une API (software-download-connector) qui
  n'accepte qu'une session « enregistrée », même chemin que Fido (pbatard/Fido) :
  1. identifiant d'édition du produit lu dans la page (3321 : Windows 11 25H2, 2618 : Windows 10) ;
  2. session (uuid) déclarée à vlscppe.microsoft.com/tags, puis aller-retour ov-df.microsoft.com
     (mdt.js donne `w` et `rticks`, renvoyés avec l'heure) ;
  3. getskuinformationbyproductedition : une référence (SKU) par langue ;
  4. GetProductDownloadLinksBySku (avec Referer) : lien du fichier, valable 24 h.
  Microsoft refuse parfois (« Sentinel », code 715-123130 : trop de demandes depuis la même
  adresse, VPN, pays sous sanctions) : `DownloadBlocked`, l'utilisateur passe alors par la page
  officielle dans son navigateur.
- Evaluation Center (evalcenter/download-windows-11-…) : versions d'évaluation de 90 jours,
  sans clé, liens go.microsoft.com/fwlink fixes (un par langue), redirigés vers le fichier.

Licence : l'ISO multi-édition installe l'édition de la clé donnée à l'installation. Sans clé
de l'utilisateur, une clé générique publique de Microsoft (GENERIC_KEYS) choisit l'édition :
Windows s'installe sans être activé, l'utilisateur l'active ensuite avec sa propre licence
(Paramètres > Système > Activation). Les versions d'évaluation s'installent sans clé et ne se
convertissent pas en version sous licence : il faut alors réinstaller avec l'ISO multi-édition.
Module sans interface, appels bloquants : hors du fil GTK.
"""

import json
import locale as _locale
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from http.cookiejar import CookieJar
from pathlib import Path

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
TIMEOUT_S = 30
ORG_ID = "y6jn8c31"
PROFILE_ID = "606624d44113"
INSTANCE_ID = "560dc9f3-1aa5-4a2f-b63c-9e18f8d0e175"   # constant chez Microsoft (Fido)
PRODUCT_EDITION_ID = 3321  # repli : Windows 11 25H2 v2, ISO multi-édition x64
REFERER = "https://www.microsoft.com/software-download/windows11"
LINK_VALID_S = 24 * 3600
RETRY_S = 5
ARCH_TYPES = {0: "x86", 1: "x64", 2: "arm64"}  # DownloadType de l'API
EVAL_PAGE = "https://www.microsoft.com/en-us/evalcenter/{page}"
FWLINK = "https://go.microsoft.com/fwlink/?linkid={id}&clcid=0x409&culture=en-us&country=us"

# Langues proposées pour Windows 11 25H2 (nom Microsoft, nom affiché), relevées le 2026-09-29.
# get_link interroge Microsoft : une langue retirée depuis donne une DownloadError claire.
LANGUAGES = [
    ("German", "Allemand"), ("English", "Anglais (États-Unis)"),
    ("English International", "Anglais international"), ("Arabic", "Arabe"),
    ("Bulgarian", "Bulgare"), ("Chinese (Simplified)", "Chinois (simplifié)"),
    ("Chinese (Traditional)", "Chinois (traditionnel)"), ("Korean", "Coréen"),
    ("Croatian", "Croate"), ("Danish", "Danois"), ("Spanish", "Espagnol"),
    ("Spanish (Mexico)", "Espagnol (Mexique)"), ("Estonian", "Estonien"), ("Finnish", "Finnois"),
    ("French", "Français"), ("French Canadian", "Français canadien"), ("Greek", "Grec"),
    ("Hebrew", "Hébreu"), ("Hungarian", "Hongrois"), ("Italian", "Italien"),
    ("Japanese", "Japonais"), ("Latvian", "Letton"), ("Lithuanian", "Lituanien"),
    ("Dutch", "Néerlandais"), ("Norwegian", "Norvégien"), ("Polish", "Polonais"),
    ("Portuguese", "Portugais"), ("Brazilian Portuguese", "Portugais (Brésil)"),
    ("Romanian", "Roumain"), ("Russian", "Russe"), ("Serbian Latin", "Serbe (latin)"),
    ("Slovak", "Slovaque"), ("Slovenian", "Slovène"), ("Swedish", "Suédois"),
    ("Czech", "Tchèque"), ("Thai", "Thaï"), ("Turkish", "Turc"), ("Ukrainian", "Ukrainien"),
]

# langue (code ISO 639) -> langue Microsoft ; variantes régionales à part
_BY_LANG = {
    "fr": "French", "en": "English International", "de": "German", "es": "Spanish",
    "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "pl": "Polish", "ru": "Russian",
    "uk": "Ukrainian", "cs": "Czech", "sk": "Slovak", "sl": "Slovenian", "hr": "Croatian",
    "sr": "Serbian Latin", "bg": "Bulgarian", "ro": "Romanian", "hu": "Hungarian", "el": "Greek",
    "tr": "Turkish", "ar": "Arabic", "he": "Hebrew", "ja": "Japanese", "ko": "Korean",
    "zh": "Chinese (Simplified)", "th": "Thai", "sv": "Swedish", "da": "Danish",
    "nb": "Norwegian", "nn": "Norwegian", "no": "Norwegian", "fi": "Finnish", "et": "Estonian",
    "lv": "Latvian", "lt": "Lithuanian",
}
_BY_REGION = {
    ("fr", "CA"): "French Canadian", ("en", "US"): "English", ("en", "CA"): "English",
    ("pt", "BR"): "Brazilian Portuguese", ("zh", "TW"): "Chinese (Traditional)",
    ("zh", "HK"): "Chinese (Traditional)", ("zh", "MO"): "Chinese (Traditional)",
}
# espagnol d'Amérique : image « Spanish (Mexico) »
_LATAM = {"MX", "AR", "BO", "CL", "CO", "CR", "CU", "DO", "EC", "GT", "HN", "NI", "PA", "PE",
          "PR", "PY", "SV", "US", "UY", "VE"}

# Éditions : (clé, nom affiché, version qui l'installe)
EDITIONS = [
    ("pro", "Professionnel", "win11"),
    ("home", "Famille", "win11"),
    ("education", "Éducation", "win11"),
    ("pro_education", "Professionnel Éducation", "win11"),
    ("pro_workstations", "Professionnel pour les stations de travail", "win11"),
    ("enterprise", "Entreprise (évaluation)", "win11_eval"),
    ("enterprise_ltsc", "Entreprise LTSC 2024 (évaluation)", "ltsc_eval"),
    ("iot_enterprise_ltsc", "IoT Entreprise LTSC 2024 (évaluation)", "iot_ltsc_eval"),
]
# éditions présentes dans l'ISO multi-édition grand public (Windows 11 et Windows 10)
CONSUMER_EDITIONS = ["home", "pro", "education", "pro_education", "pro_workstations"]
# Clés génériques publiques de Microsoft : elles choisissent l'édition de l'ISO multi-édition
# et installent SANS activer. Pro, Éducation, Pro Éducation, Pro Workstations : clés « client
# KMS » (GVLK) publiées par Microsoft (learn.microsoft.com, kms-client-activation-keys,
# vérifiées le 2026-09-29) ; l'installateur 25H2 accepte celle de Pro (la clé générique
# « retail » VK7JG-… est refusée). Famille n'a pas de clé dans cette table : TX9XD-… est la
# clé client KMS de Famille qui circule, pas testée avec l'installateur 25H2 ; si elle est
# refusée, choisir l'image « Windows 11 Famille » par son nom (InstallFrom/MetaData IMAGE/NAME)
# sans clé. Une édition installée avec une clé générique passe à une licence de détail en
# saisissant la clé de l'utilisateur dans Paramètres > Activation. Entreprise n'est pas dans
# l'ISO grand public : avec une licence Entreprise, installer Pro puis saisir la clé Entreprise
# (changement d'édition par clé), sinon prendre la version d'évaluation.
GENERIC_KEYS = {
    "pro": "W269N-WFGWX-YVC9B-4J6C9-T83GX",
    "home": "TX9XD-98N7V-6WMQ6-BX7FG-H8Q99",
    "education": "NW6C2-QMPVW-D7KKK-3GKT6-VCFB2",
    "pro_education": "6TP4R-GNPTD-KYYHQ-7B7DP-J447Y",
    "pro_workstations": "NRG8B-VKK3Q-CXVCJ-9G2XF-6Q84J",
}
KEY_RE = re.compile(r"^[A-Z0-9]{5}(-[A-Z0-9]{5}){4}$")

# Microsoft Store : produits vérifiés le 2026-09-29 (la locale de la page change seule)
_STORE_PRODUCTS = {"pro": "windows-11-pro/dg7gmgf0d8h4", "home": "windows-11-home/dg7gmgf0krt0"}

# -- versions --

# Langues de l'Evaluation Center (code de la page -> nom Microsoft), relevées le 2026-09-29
EVAL_CODES = {
    "en-US": "English", "en-GB": "English International", "zh-CN": "Chinese (Simplified)",
    "zh-TW": "Chinese (Traditional)", "fr-FR": "French", "de-DE": "German", "it-IT": "Italian",
    "ja-JP": "Japanese", "ko-KR": "Korean", "pt-BR": "Brazilian Portuguese", "es-ES": "Spanish",
}
# Liens fwlink de l'Evaluation Center : (famille, architecture) -> {langue Microsoft: linkid}.
# Relus dans la page si l'un d'eux ne répond plus (fetch_languages).
EVAL_LINKS = {
    ("enterprise", "x64"): {
        "English": 2334167, "English International": 2334364, "Chinese (Simplified)": 2334169,
        "Chinese (Traditional)": 2334269, "French": 2334272, "German": 2334170, "Italian": 2334274,
        "Japanese": 2334365, "Korean": 2334366, "Brazilian Portuguese": 2334171, "Spanish": 2334275,
    },
    ("ltsc", "x64"): {
        "English": 2289029, "English International": 2289033, "Chinese (Simplified)": 2288085,
        "Chinese (Traditional)": 2288282, "French": 2288930, "German": 2288932, "Italian": 2288933,
        "Japanese": 2288181, "Korean": 2288289, "Brazilian Portuguese": 2289301, "Spanish": 2289211,
    },
    ("iot", "x64"): {"English": 2270353},
    ("iot", "arm64"): {"English": 2269595},
}


def _names(names):
    fr = dict(LANGUAGES)
    return [(n, fr.get(n, n)) for n in names]


# Versions proposées, vérifiées le 2026-09-29 jusqu'au lien et à la taille (HEAD) :
# win11 Win11_25H2_French_x64_v2.iso 7,9 Gio ; win11_eval …25h2…CLIENTENTERPRISEEVAL…fr-fr.iso
# 6,6 Gio ; ltsc_eval …CLIENT_LTSC_EVAL_x64FRE_fr-fr.iso 4,8 Gio ; iot_ltsc_eval
# …CLIENT_IOT_LTSC_EVAL_x64FRE_en-us.iso 4,7 Gio ; win10 Win10_22H2_French_x64v1.iso 5,7 Gio.
# `languages` : liste vérifiée ; fetch_languages() la relit chez Microsoft.
VERSIONS = [
    {
        "key": "win11", "label": "Windows 11 (25H2)",
        "description": "Version actuelle, toutes les éditions grand public (Famille, Professionnel, "
                       "Éducation…). S'active avec votre licence.",
        "method": "api", "page": "windows11", "product_edition_id": 3321,
        "referer": "https://www.microsoft.com/software-download/windows11",
        "arch": ["x64"], "languages": list(LANGUAGES), "eval": False, "needs_key": True,
        "editions": CONSUMER_EDITIONS, "default": True,
        "note": "Clé de produit à l'installation : la vôtre, ou une clé générique qui installe "
                "sans activer (activation ensuite dans Paramètres).",
    },
    {
        "key": "win11_eval", "label": "Windows 11 Entreprise, évaluation 90 jours",
        "description": "Sans clé ni licence : idéale pour essayer. Expire au bout de 90 jours.",
        "method": "evalcenter", "page": "download-windows-11-enterprise", "family": "enterprise",
        "arch": ["x64"], "languages": _names(EVAL_LINKS[("enterprise", "x64")]), "eval": True,
        "needs_key": False, "editions": ["enterprise"],
        "note": "Activation en ligne demandée après l'installation. Après 90 jours : fond noir, "
                "rappel permanent, arrêt toutes les heures. Ne passe pas à une licence : "
                "réinstaller avec Windows 11 (25H2) pour garder Windows.",
    },
    {
        "key": "ltsc_eval", "label": "Windows 11 Entreprise LTSC 2024, évaluation 90 jours",
        "description": "Édition à support long, sans nouveautés ni Microsoft Store, plus légère. "
                       "Sans clé ; expire au bout de 90 jours.",
        "method": "evalcenter", "page": "download-windows-11-enterprise", "family": "ltsc",
        "arch": ["x64"], "languages": _names(EVAL_LINKS[("ltsc", "x64")]), "eval": True,
        "needs_key": False, "editions": ["enterprise_ltsc"],
        "note": "Base 24H2 (build 26100). Sans Microsoft Store : le nouvel Outlook et les "
                "applications du Store ne s'installent pas. Mêmes limites d'évaluation.",
    },
    {
        "key": "iot_ltsc_eval", "label": "Windows 11 IoT Entreprise LTSC 2024, évaluation 90 jours",
        "description": "Variante LTSC pour appareils dédiés, en anglais seulement. Sans clé ; "
                       "expire au bout de 90 jours.",
        "method": "evalcenter", "page": "download-windows-11-iot-enterprise-ltsc-eval", "family": "iot",
        "arch": ["x64", "arm64"], "languages": _names(["English"]), "eval": True,
        "needs_key": False, "editions": ["iot_enterprise_ltsc"],
        "note": "Anglais (États-Unis) seulement ; un module de langue s'ajoute ensuite dans "
                "Windows. Sans Microsoft Store.",
    },
    {
        "key": "win10", "label": "Windows 10 (22H2)",
        "description": "Ancienne version, pour les applications qui l'exigent.",
        "method": "api", "page": "windows10ISO", "product_edition_id": 2618,
        "referer": "https://www.microsoft.com/software-download/windows10ISO",
        "arch": ["x64", "x86"], "languages": list(LANGUAGES), "eval": False, "needs_key": True,
        "editions": CONSUMER_EDITIONS, "end_of_support": "2025-10-14",
        "note": "Support terminé le 14 octobre 2025 : plus de mises à jour de sécurité sans le "
                "programme payant ESU. Mêmes clés génériques que Windows 11.",
    },
]
DEFAULT_VERSION = "win11"
_BY_KEY = {v["key"]: v for v in VERSIONS}
# langue absente d'une version : la plus proche avant l'anglais
_NEAREST = {
    "French Canadian": "French", "Spanish (Mexico)": "Spanish", "Portuguese": "Brazilian Portuguese",
    "English": "English International", "English International": "English",
}


def get_version(key):
    """Description d'une version (dict de VERSIONS) ; ValueError si inconnue."""
    try:
        return _BY_KEY[key]
    except KeyError:
        raise ValueError(f"version inconnue : {key} ({', '.join(_BY_KEY)})") from None


def needs_key(version_key):
    """Vrai si l'installation demande une clé de produit (celle de l'utilisateur ou générique)."""
    return get_version(version_key)["needs_key"]


def version_for_edition(edition, licensed=False):
    """Version à télécharger pour une édition. Entreprise avec licence : Windows 11 (installer
    Pro, puis passer à Entreprise en saisissant la clé) ; sans licence : l'évaluation."""
    if edition == "enterprise" and licensed:
        return DEFAULT_VERSION
    for key, _, ver in EDITIONS:
        if key == edition:
            return ver
    raise ValueError(f"édition inconnue : {edition}")


def install_key(version_key, edition="pro", user_key=None):
    """Clé à donner à l'installateur : None pour une évaluation (sans clé), la clé de
    l'utilisateur si elle est fournie (ValueError si mal formée), sinon la clé générique de
    l'édition (Entreprise sous licence : celle de Pro)."""
    v = get_version(version_key)
    if not v["needs_key"]:
        return None
    if user_key:
        key = normalize_key(user_key)
        if key is None:
            raise ValueError("clé de produit mal formée (25 caractères, XXXXX-XXXXX-XXXXX-XXXXX-XXXXX)")
        return key
    edition = "pro" if edition == "enterprise" else edition
    if edition not in v["editions"]:
        raise ValueError(f"l'édition {edition} n'est pas dans {v['label']}")
    return GENERIC_KEYS[edition]


class DownloadError(RuntimeError):
    """Lien ou téléchargement impossible (réseau, réponse inattendue de Microsoft)."""


class DownloadBlocked(DownloadError):
    """Microsoft refuse de servir cette adresse (« Sentinel », code 715-123130)."""


class DownloadCancelled(DownloadError):
    """Téléchargement interrompu à la demande ; le fichier .part reste pour la reprise."""


# -- locale, pages --

def _system_locale():
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        value = os.environ.get(var)
        if value and value not in ("C", "POSIX"):
            return value
    try:
        return _locale.getlocale()[0] or "en_US"
    except ValueError:
        return "en_US"


def _split_locale(loc):
    """« fr_FR.UTF-8 », « fr-fr » -> (« fr », « FR »)."""
    loc = (loc or "").split(".")[0].split("@")[0].replace("-", "_")
    lang, _, region = loc.partition("_")
    return lang.lower(), region.upper()


def web_locale(loc=None):
    """Locale des adresses microsoft.com : « fr-fr »."""
    lang, region = _split_locale(loc or _system_locale())
    if not lang or lang in ("c", "posix"):
        return "en-us"
    return f"{lang}-{(region or lang).lower()}"


def official_page(loc=None):
    """Page officielle de téléchargement de Windows 11, dans la langue du système."""
    return f"https://www.microsoft.com/{web_locale(loc)}/software-download/windows11"


def buy_url(edition="pro", loc=None):
    """Achat d'une licence Windows 11 au Microsoft Store (Pro ou Famille)."""
    product = _STORE_PRODUCTS.get(edition, _STORE_PRODUCTS["pro"])
    return f"https://www.microsoft.com/{web_locale(loc)}/d/{product}"


OFFICIAL_PAGE = official_page()
BUY_URL = buy_url()


def languages(version_key=DEFAULT_VERSION):
    """[(nom Microsoft, nom affiché)] des langues d'une version, sans appel réseau."""
    return list(get_version(version_key)["languages"])


def default_language(version_key=DEFAULT_VERSION, loc=None):
    """Langue de la version la plus proche de la locale (système par défaut)."""
    names = [n for n, _ in languages(version_key)]
    lang = language_for_locale(loc)
    for candidate in (lang, _NEAREST.get(lang), "English", "English International"):
        if candidate in names:
            return candidate
    return names[0]


def language_for_locale(loc=None):
    """Langue de l'ISO la plus proche de la locale (système par défaut) ; repli « English »."""
    lang, region = _split_locale(loc or _system_locale())
    if (lang, region) in _BY_REGION:
        return _BY_REGION[(lang, region)]
    if lang == "es" and region in _LATAM:
        return "Spanish (Mexico)"
    if lang == "zh" and region in ("SG", "CN", ""):
        return "Chinese (Simplified)"
    return _BY_LANG.get(lang, "English")


# -- clés de produit --

def normalize_key(text):
    """Clé saisie (minuscules, espaces, sans tirets) -> « XXXXX-XXXXX-XXXXX-XXXXX-XXXXX »,
    ou None si ce n'en est pas une."""
    raw = re.sub(r"[^A-Za-z0-9]", "", text or "").upper()
    if len(raw) != 25:
        return None
    key = "-".join(raw[i:i + 5] for i in range(0, 25, 5))
    return key if KEY_RE.match(key) else None


# -- lien de téléchargement --

class _Session:
    """Requêtes HTTP d'une même session (cookies partagés), comme le navigateur."""

    def __init__(self, timeout=TIMEOUT_S):
        self.timeout = timeout
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(CookieJar()))

    def request(self, url, headers=None, method="GET"):
        """(code HTTP, en-têtes, corps en octets)."""
        req = urllib.request.Request(url, method=method, headers={
            "User-Agent": USER_AGENT, "Accept": "*/*", **(headers or {})})
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                return r.status, r.headers, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read()
        except (urllib.error.URLError, OSError) as e:
            raise DownloadError(f"Microsoft ne répond pas : {getattr(e, 'reason', e)}") from e

    def resolve(self, url):
        """(adresse finale après redirections, code HTTP, en-têtes) par une requête HEAD."""
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": USER_AGENT, "Accept": "*/*"})
        try:
            with self.opener.open(req, timeout=self.timeout) as r:
                return r.geturl(), r.status, r.headers
        except urllib.error.HTTPError as e:
            return url, e.code, e.headers
        except (urllib.error.URLError, OSError) as e:
            raise DownloadError(f"Microsoft ne répond pas : {getattr(e, 'reason', e)}") from e


_session_factory = _Session  # remplacé dans les tests


def _json(body, what):
    try:
        return json.loads(body)
    except ValueError as e:
        raise DownloadError(f"réponse illisible de Microsoft ({what})") from e


def _blocked_message(page_html):
    """Message de refus affiché par la page de Microsoft (msg-01), sinon un message à nous.
    Suivi du conseil : attendre, ou passer par la page officielle dans le navigateur."""
    m = re.search(r'id="msg-01"[^>]*value="(.*?)"', page_html or "", re.S)
    text = ""
    if m:
        text = re.sub(r"<[^>]+>", "", m.group(1).replace("&lt;", "<").replace("&gt;", ">"))
        text = re.sub(r"\s+", " ", text).strip()
    if "715-123130" not in text:
        text = ("Microsoft refuse le téléchargement depuis cette adresse (code 715-123130) : "
                "trop de demandes, VPN ou pays sous sanctions")
    return (text.rstrip(". ") + ". Réessayer dans une heure, ou télécharger l'image depuis la page "
            "officielle dans le navigateur, ou choisir une version d'évaluation.")


def _is_sentinel(error):
    """Refus de « Sentinel », la protection de Microsoft : type 9 (adresse bannie, 715-123130)
    ou type 8 (trop de demandes rapprochées, passager)."""
    return error.get("Type") in (8, 9) or "Sentinel" in str(error.get("Key", "")) + str(error.get("Value", ""))


def _edition_id(page_html, fallback=PRODUCT_EDITION_ID):
    """Identifiant de l'ISO multi-édition dans la page (première option « Windows »)."""
    m = re.search(r'<option value="(\d+)"[^>]*>\s*Windows', page_html or "")
    return int(m.group(1)) if m else fallback


def _api_locale(loc):
    lang, region = web_locale(loc).split("-")
    return f"{lang}-{region.upper()}"


class _ApiSession:
    """Session enregistrée auprès de Microsoft pour une version des pages grand public."""

    API = "https://www.microsoft.com/software-download-connector/api/"

    def __init__(self, v, loc):
        self.v, self.loc = v, loc
        self.s = _session_factory()
        page_url = f"https://www.microsoft.com/{web_locale(loc)}/software-download/{v['page']}"
        status, _, body = self.s.request(page_url)
        self.page = body.decode("utf-8", "replace") if status == 200 else ""
        self.edition = _edition_id(self.page, v["product_edition_id"])
        # session déclarée à Microsoft (sans quoi l'API refuse de répondre)
        self.sid = sid = str(uuid.uuid4())
        self.s.request(f"https://vlscppe.microsoft.com/tags?org_id={ORG_ID}&session_id={sid}")
        _, _, js = self.s.request(f"https://ov-df.microsoft.com/mdt.js?instanceId={INSTANCE_ID}"
                                  f"&PageId=si&session_id={sid}")
        js = js.decode("utf-8", "replace")
        w = re.search(r"[?&]w=([A-F0-9]+)", js)
        rticks = re.search(r'rticks\="\+?(\d+)', js)
        if not w or not rticks:
            raise DownloadError("protection du site de Microsoft : réponse inattendue (ov-df)")
        self.s.request(f"https://ov-df.microsoft.com/?session_id={sid}&CustomerId={INSTANCE_ID}"
                       f"&PageId=si&w={w.group(1)}&mdt={int(time.time() * 1000)}&rticks={rticks.group(1)}")

    def _query(self, **params):
        return urllib.parse.urlencode({"profile": PROFILE_ID, "friendlyFileName": "undefined",
                                       "Locale": _api_locale(self.loc), "sessionID": self.sid, **params})

    def skus(self):
        q = self._query(productEditionId=self.edition, SKU="undefined")
        for attempt in range(3):  # parfois vide au premier essai (Fido fait de même)
            if attempt:
                time.sleep(2)
            status, _, body = self.s.request(self.API + "getskuinformationbyproductedition?" + q)
            data = _json(body, "langues") if status == 200 else {}
            if data.get("Skus") and not data.get("Errors"):
                return data["Skus"]
            if _is_sentinel((data.get("Errors") or [{}])[0]):
                raise DownloadBlocked(_blocked_message(self.page))
        raise DownloadError("Microsoft ne donne pas la liste des langues")

    def link(self, sku_id, arch):
        q = self._query(productEditionId="undefined", SKU=sku_id)
        _, _, body = self.s.request(self.API + "GetProductDownloadLinksBySku?" + q,
                                    {"Referer": self.v["referer"]})
        data = _json(body, "liens")
        errors = data.get("Errors") or []
        if errors:
            if _is_sentinel(errors[0]):
                raise DownloadBlocked(_blocked_message(self.page))
            raise DownloadError(f"Microsoft : {errors[0].get('Value') or 'erreur inconnue'}")
        url = next((o.get("Uri") for o in data.get("ProductDownloadOptions") or []
                    if ARCH_TYPES.get(o.get("DownloadType")) == arch), None)
        if not url:
            raise DownloadError(f"pas d'image {arch} dans la réponse de Microsoft")
        return url


# -- Evaluation Center --

def _parse_eval_page(page_html):
    """{(famille, architecture): {langue Microsoft: linkid}} lus dans une page de l'Evaluation
    Center (libellés anglais : « Download Windows 11 Enterprise ISO LTSC 64-bit (fr-FR) »)."""
    found = {}
    for m in re.finditer(r'<a [^>]*?data-target="https://go\.microsoft\.com/fwlink/\?linkid=(\d+)'
                         r'&amp;[^"]*"[^>]*?aria-label="([^"]*)"', page_html or "", re.I):
        link_id, label = int(m.group(1)), m.group(2)
        code = re.search(r"\(([a-z]{2}-[A-Za-z]{2})\)", label)
        language = EVAL_CODES.get(code.group(1)[:3] + code.group(1)[3:].upper()) if code else "English"
        if not language or "ISO" not in label:
            continue
        family = "iot" if "IoT" in label else "ltsc" if "LTSC" in label else "enterprise"
        arch = "arm64" if "ARM64" in label else "x64"
        found.setdefault((family, arch), {})[language] = link_id
    return found


def _refresh_eval(v):
    s = _session_factory()
    status, _, body = s.request(EVAL_PAGE.format(page=v["page"]))
    found = _parse_eval_page(body.decode("utf-8", "replace") if status == 200 else "")
    if not found:
        raise DownloadError("page de l'Evaluation Center illisible")
    EVAL_LINKS.update(found)
    return found


def _eval_link(v, language, arch):
    """Lien final (après redirection) d'une ISO d'évaluation, nom et taille."""
    for attempt in range(2):
        link_id = EVAL_LINKS.get((v["family"], arch), {}).get(language)
        if link_id is not None:
            s = _session_factory()
            final, status, headers = s.resolve(FWLINK.format(id=link_id))
            if status == 200 and final.lower().split("?")[0].endswith(".iso"):
                size = headers.get("Content-Length")
                return final, urllib.parse.unquote(Path(urllib.parse.urlsplit(final).path).name), \
                    int(size) if size else None
        if attempt == 0:
            _refresh_eval(v)  # liens changés depuis le relevé : relire la page
    raise DownloadError(f"pas d'image {arch} en « {language} » pour {v['label']}")


# -- langues et liens --

def fetch_languages(version_key=DEFAULT_VERSION, loc=None):
    """Langues proposées aujourd'hui par Microsoft pour cette version : [(nom Microsoft, nom
    affiché)]. Met à jour la liste de la version. Appel réseau."""
    v = get_version(version_key)
    if v["method"] == "api":
        skus = _ApiSession(v, loc).skus()
        out = [(k["Language"], k.get("LocalizedLanguage") or dict(LANGUAGES).get(k["Language"], k["Language"]))
               for k in skus]
    else:
        found = _refresh_eval(v)
        names = []
        for (family, _arch), table in found.items():
            if family == v["family"]:
                names += [n for n in table if n not in names]
        out = _names(names)
    if out:
        v["languages"] = out
    return out


def get_link(language=None, version=DEFAULT_VERSION, arch="x64", loc=None):
    """Lien officiel de l'ISO : (url, nom du fichier, taille en octets ou None).

    `language` : nom Microsoft (« French »), sinon la langue de la version la plus proche du
    système. Lien des pages grand public valable 24 h ; lien d'évaluation fixe.
    Lève DownloadBlocked si Microsoft refuse cette adresse, DownloadError sinon."""
    v = get_version(version)
    if arch not in v["arch"]:
        raise DownloadError(f"{v['label']} n'existe pas en {arch} ({', '.join(v['arch'])})")
    language = language or default_language(v["key"], loc)
    if v["method"] == "evalcenter":
        return _eval_link(v, language, arch)
    for attempt in range(2):
        session = _ApiSession(v, loc)
        sku = next((k for k in session.skus() if k.get("Language") == language), None)
        if sku is None:
            raise DownloadError(f"langue « {language} » non proposée par Microsoft pour {v['label']}")
        try:
            url = session.link(sku["Id"], arch)
            break
        except DownloadBlocked:
            if attempt:
                raise
            time.sleep(RETRY_S)  # refus parfois passager : une nouvelle session, une fois
    filename = urllib.parse.unquote(Path(urllib.parse.urlsplit(url).path).name) or "Windows.iso"
    size = None
    try:
        status, headers, _ = session.s.request(url, method="HEAD")
        if status == 200 and headers.get("Content-Length"):
            size = int(headers["Content-Length"])
    except (DownloadError, ValueError):
        pass
    return url, filename, size


# -- téléchargement --

_urlopen = urllib.request.urlopen  # remplacé dans les tests


def download(url, dest, progress=None, cancel=None, expected_size=None, chunk=1 << 20, timeout=60):
    """Télécharge `url` vers `dest` : blocs écrits dans « dest.part », renommé à la fin.

    Reprend un « .part » existant (en-tête Range). `progress(fait, total)` est appelé après
    chaque bloc (total None si inconnu) ; `cancel` (threading.Event) arrête proprement
    (DownloadCancelled, le .part reste). La taille finale est contrôlée."""
    dest = Path(dest)
    part = dest.with_name(dest.name + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    offset = part.stat().st_size if part.exists() else 0
    if expected_size and offset > expected_size:
        part.unlink()
        offset = 0
    if expected_size and offset == expected_size:
        part.replace(dest)
        return dest
    headers = {"User-Agent": USER_AGENT}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    req = urllib.request.Request(url, headers=headers)
    try:
        with _urlopen(req, timeout=timeout) as r:
            if offset and r.status != 206:
                offset = 0  # le serveur ignore Range : on repart de zéro
            length = r.headers.get("Content-Length")
            total = expected_size or (offset + int(length) if length else None)
            done = offset
            with open(part, "ab" if offset else "wb") as f:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise DownloadCancelled("téléchargement interrompu")
                    block = r.read(chunk)
                    if not block:
                        break
                    f.write(block)
                    done += len(block)
                    if progress:
                        progress(done, total)
    except urllib.error.HTTPError as e:
        if e.code == 416 and expected_size and offset == expected_size:
            part.replace(dest)
            return dest
        if e.code in (403, 410):
            raise DownloadError("lien expiré (valable 24 h) : en demander un nouveau") from e
        raise DownloadError(f"téléchargement refusé (HTTP {e.code})") from e
    except (urllib.error.URLError, OSError) as e:
        raise DownloadError(f"téléchargement interrompu : {getattr(e, 'reason', e)} "
                            "(il reprendra où il s'est arrêté)") from e
    size = part.stat().st_size
    if total is not None and size != total:
        raise DownloadError(f"fichier incomplet ({size} octets sur {total}) : relancer pour reprendre")
    part.replace(dest)
    return dest
