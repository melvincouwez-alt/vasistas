"""Traductions de Vasistas (hôte, compagnon, assistant, commandes).

    from .i18n import _
    _("Ouvrir {name}", name="Word")   # « Ouvrir Word » ou « Open Word »

La clé est le texte français ; locale/en/*.json (un fichier par module, fusionnés) donne
l'anglais. La langue
vient du réglage `language` de config.json (auto, fr ou en ; auto = langue du système),
lue une fois puis gardée : refresh() la relit après un changement.
Texte absent du catalogue : le français est affiché tel quel.
"""

import json
import locale
import os
from pathlib import Path

CATALOG = Path(__file__).resolve().parent / "locale" / "en"
LANGUAGES = ("auto", "fr", "en")

_lang = None
_en = None


def system_language():
    """« fr » si le système est en français, « en » sinon."""
    for var in ("LC_ALL", "LC_MESSAGES", "LANGUAGE", "LANG"):
        value = os.environ.get(var)
        if value:
            return "fr" if value.lower().startswith("fr") else "en"
    try:
        code = locale.getlocale()[0] or ""
    except ValueError:
        code = ""
    return "fr" if code.lower().startswith("fr") else "en"


def setting():
    """Valeur du réglage : auto, fr ou en."""
    try:
        from . import vm
        value = vm.load_config().get("language", "auto")
    except Exception:
        value = "auto"
    return value if value in LANGUAGES else "auto"


def refresh():
    """Relit la langue (après un changement de réglage). Renvoie « fr » ou « en »."""
    global _lang
    value = setting()
    _lang = system_language() if value == "auto" else value
    return _lang


def current():
    return _lang or refresh()


def catalog():
    global _en
    if _en is None:
        _en = {}
        for path in sorted(CATALOG.glob("*.json")):
            try:
                _en.update(json.loads(path.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
    return _en


def _(text, **kw):
    if current() == "en":
        text = catalog().get(text, text)
    return text.format(**kw) if kw else text


def N_(text):
    """Marque un texte à traduire plus tard (listes, constantes) sans le traduire ici."""
    return text
