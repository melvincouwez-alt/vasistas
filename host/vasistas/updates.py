"""Mises à jour de Vasistas depuis les versions publiées sur GitHub.

Installation (install.sh) : ~/.local/opt/vasistas/<version>, lien « current » vers la version
active, commande ~/.local/bin/vasistas qui lance toujours la version active ; les lanceurs du
menu passent par elle. Une mise à jour dépose la nouvelle version à côté, vérifie son empreinte
(SHA256SUMS de la version publiée), lance son install.sh --update, puis garde l'ancienne
version en réserve. L'agent Windows suit au démarrage suivant de Windows (copié depuis le CD
de partage).

Un dépôt git (développement) ne se met pas à jour ici : git pull.
"""

import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from . import version
from .vm import DATA

APP_ROOT = Path.home() / ".local/opt/vasistas"
WRAPPER = Path.home() / ".local/bin/vasistas"
HOST_DIR = Path(__file__).resolve().parents[1]
SOURCE_DIR = HOST_DIR.parent
CACHE = DATA / "update.json"
CHECK_EVERY_S = 24 * 3600
API = f"https://api.github.com/repos/{version.GITHUB_REPO}/releases?per_page=10"


class UpdateError(Exception):
    pass


def mode():
    """« dev » (dépôt git), « installed » (install.sh) ou « other » (copie lancée à la main)."""
    if (SOURCE_DIR / ".git").exists():
        return "dev"
    try:
        SOURCE_DIR.relative_to(APP_ROOT.resolve())
        return "installed"
    except ValueError:
        return "other"


def _get(url, timeout=15, accept="application/vnd.github+json"):
    req = urllib.request.Request(url, headers={"Accept": accept,
                                               "User-Agent": f"Vasistas/{version.VERSION}"})
    return urllib.request.urlopen(req, timeout=timeout)


def latest(include_prerelease=version.PRERELEASE):
    """Dernière version publiée : {version, name, notes, url, asset, sums, prerelease}, ou None."""
    try:
        with _get(API) as r:
            releases = json.load(r)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise UpdateError("Dépôt introuvable ou privé : pas de version publiée à consulter.")
        raise UpdateError(f"GitHub répond {e.code}.")
    except (OSError, ValueError) as e:
        raise UpdateError(f"GitHub injoignable : {e}")
    for rel in releases:
        if rel.get("draft") or (rel.get("prerelease") and not include_prerelease):
            continue
        assets = {a["name"]: a["browser_download_url"] for a in rel.get("assets") or []}
        tarball = next((u for n, u in assets.items() if n.startswith("vasistas-") and n.endswith(".tar.gz")), None)
        return {
            "version": rel.get("tag_name", "").lstrip("vV"),
            "name": rel.get("name") or rel.get("tag_name"),
            "notes": rel.get("body") or "",
            "url": rel.get("html_url"),
            "asset": tarball,
            "sums": assets.get("SHA256SUMS"),
            "prerelease": bool(rel.get("prerelease")),
            "published": rel.get("published_at"),
        }
    return None


def check(force=False):
    """(« available » | « uptodate », version publiée ou None). Résultat gardé un jour."""
    if not force:
        try:
            cached = json.loads(CACHE.read_text())
            if time.time() - cached.get("time", 0) < CHECK_EVERY_S:
                rel = cached.get("release")
                return ("available" if rel and version.newer(rel["version"]) else "uptodate"), rel
        except (OSError, ValueError):
            pass
    rel = latest()
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps({"time": time.time(), "release": rel}))
    except OSError:
        pass
    return ("available" if rel and version.newer(rel["version"]) else "uptodate"), rel


def _download(url, dest, progress=None, cancel=None):
    with _get(url, timeout=30, accept="application/octet-stream") as r, open(dest, "wb") as f:
        total = int(r.headers.get("Content-Length") or 0)
        done = 0
        while chunk := r.read(1 << 18):
            if cancel is not None and cancel.is_set():
                raise UpdateError("Mise à jour annulée.")
            f.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)


def _verify(archive, sums_url):
    if not sums_url:
        raise UpdateError("Version publiée sans fichier SHA256SUMS : installation refusée.")
    with _get(sums_url, accept="text/plain") as r:
        sums = r.read().decode()
    expected = next((line.split()[0] for line in sums.splitlines()
                     if line.strip().endswith(archive.name)), None)
    h = hashlib.sha256()
    with open(archive, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    if expected is None or h.hexdigest() != expected.lower():
        raise UpdateError("Empreinte de l'archive incorrecte : téléchargement corrompu ou modifié.")


def install(release, progress=None, cancel=None):
    """Télécharge, vérifie et installe `release` (voir latest()). Rend le dossier installé."""
    if mode() == "dev":
        raise UpdateError("Version de développement (dépôt git) : mettre à jour avec git pull.")
    if not release or not release.get("asset"):
        raise UpdateError("Cette version publiée n'a pas d'archive d'installation.")
    work = DATA / "updates"
    shutil.rmtree(work, ignore_errors=True)
    work.mkdir(parents=True)
    archive = work / release["asset"].rsplit("/", 1)[-1]
    _download(release["asset"], archive, progress, cancel)
    _verify(archive, release.get("sums"))
    target = APP_ROOT / release["version"]
    staging = work / "extract"
    with tarfile.open(archive) as tar:
        tar.extractall(staging, filter="data")
    roots = [p for p in staging.iterdir() if p.is_dir()]
    if len(roots) != 1 or not (roots[0] / "install.sh").exists():
        raise UpdateError("Archive inattendue : install.sh introuvable.")
    shutil.rmtree(target, ignore_errors=True)
    APP_ROOT.mkdir(parents=True, exist_ok=True)
    shutil.move(str(roots[0]), target)
    res = subprocess.run(["sh", str(target / "install.sh"), "--update"], capture_output=True, text=True,
                         timeout=600, env=dict(os.environ, VASISTAS_FROM=version.VERSION))
    shutil.rmtree(work, ignore_errors=True)
    if res.returncode != 0:
        raise UpdateError("install.sh a échoué : " + (res.stderr or res.stdout)[-400:])
    return target
