"""Applications installées dans Windows, lues par le socket de contrôle (script guest/apps.ps1).

La liste est gardée dans ~/.local/share/vasistas/installed.json : l'application compagnon
l'affiche même VM arrêtée.
"""

import json
import re
from pathlib import Path

from . import control
from .i18n import N_
from .vm import DATA

SCRIPT = Path(__file__).resolve().parent / "guest" / "apps.ps1"
CACHE = DATA / "installed.json"
CHUNK = 60000  # exec renvoie au plus 64 Ko
CATEGORIES = {
    "user": N_("Installées"),
    "windows": N_("Applications Windows"),
    "system": N_("Système et administration"),
}


def _exec(script, timeout=180):
    res = control.request({"exec": script}, timeout=timeout)
    if res.get("error"):
        raise RuntimeError(res["error"])
    if res.get("code"):
        raise RuntimeError(res.get("out") or f"code {res['code']}")
    return res.get("out", "")


def fetch():
    """Relit la liste dans l'invité (quelques secondes) et met le cache à jour."""
    import base64
    size = int(_exec(SCRIPT.read_text(encoding="utf-8")).strip() or 0)
    parts = []
    for start in range(0, size, CHUNK):
        n = min(CHUNK, size - start)
        parts.append(_exec(f"[Console]::Out.Write([IO.File]::ReadAllText(\"$env:TEMP\\vasistas-apps.b64\")"
                           f".Substring({start}, {n}))", timeout=60).strip())
    apps = json.loads(base64.b64decode("".join(parts)).decode("utf-8")) if size else []
    from .desktop import canonical_app
    for a in apps:
        # même règle que l'agent (Apps.cs) : lettres et chiffres seulement
        a["id"] = canonical_app(re.sub(r"[^a-z0-9]", "", a["id"].lower()))
    # un même identifiant peut venir de deux raccourcis (Outlook classique et nouveau) : le premier gagne
    seen, unique = set(), []
    for a in apps:
        if a["id"] not in seen:
            seen.add(a["id"])
            unique.append(a)
    unique.sort(key=lambda a: a["name"].lower())
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(unique, ensure_ascii=False))
    return unique


def cached():
    try:
        return json.loads(CACHE.read_text())
    except (OSError, ValueError):
        return []
