"""« Quoi de neuf » : nouveautés par version (whatsnew.json, français et anglais).

Le compagnon les montre une fois, au premier lancement après une mise à jour : la clé
`seen_version` de config.json garde la dernière version vue. Une installation neuve ne montre
rien (rien de « nouveau » pour qui découvre Vasistas) : seen_version prend la version courante.
"""

import json
from pathlib import Path

from . import version

FILE = Path(__file__).resolve().parent / "whatsnew.json"


def load():
    try:
        return json.loads(FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _core(v):
    return version.parse(v)[0]


def entries(lang, data=None, since=None, upto=None):
    """[(version, [points])] du plus récent au plus ancien ; seulement les versions après
    `since` (exclue) et jusqu'à `upto` (incluse, préversions comprises : 0.6.0-beta voit 0.6.0)."""
    data = load() if data is None else data
    out = []
    for v, texts in data.items():
        if since and _core(v) <= _core(since):
            continue
        if upto and _core(v) > _core(upto):
            continue
        points = texts.get(lang) or texts.get("fr") or []
        if points:
            out.append((v, points))
    out.sort(key=lambda e: _core(e[0]), reverse=True)
    return out


def pending(cfg, lang, current=version.VERSION, data=None, fresh_install=False):
    """Nouveautés à montrer au lancement : [(version, [points])], vide si rien de neuf."""
    seen = cfg.get("seen_version")
    if not seen and fresh_install:
        return []
    # sans seen_version : mise à jour depuis une version d'avant « Quoi de neuf », tout est neuf
    return entries(lang, data, since=seen, upto=current)
