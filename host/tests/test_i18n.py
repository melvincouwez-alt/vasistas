"""Traductions : chaque texte marqué par _() ou N_() dans l'hôte a son anglais, et le catalogue est propre."""

import ast
import json
import string
from pathlib import Path

import pytest

from vasistas import i18n

PKG = Path(__file__).resolve().parents[1] / "vasistas"
LOCALE = PKG / "locale" / "en"
MODULES = sorted(p.name for p in PKG.glob("*.py"))
DASHES = ("—", "–")


def _texts(path):
    """Chaînes littérales passées à _() ou N_() dans un module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("_", "N_") \
                and node.args and isinstance(node.args[0], ast.Constant) and isinstance(node.args[0].value, str):
            yield node.args[0].value


def _files():
    return {path.name: json.loads(path.read_text(encoding="utf-8")) for path in sorted(LOCALE.glob("*.json"))}


def _catalog():
    out = {}
    for entries in _files().values():
        out.update(entries)
    return out


def _fields(text):
    return {f for _, f, _, _ in string.Formatter().parse(text) if f is not None and f != ""}


@pytest.mark.parametrize("module", MODULES)
def test_textes_traduits(module):
    catalog = _catalog()
    missing = [t for t in _texts(PKG / module) if t not in catalog]
    assert not missing


def test_catalogues_lisibles():
    for name, entries in _files().items():
        assert isinstance(entries, dict) and entries is not None, name
        for fr, en in entries.items():
            assert isinstance(en, str) and en.strip(), (name, fr)


def test_pas_de_tiret_cadratin():
    for name, entries in _files().items():
        for fr, en in entries.items():
            for dash in DASHES:
                assert dash not in fr and dash not in en, (name, fr)


def test_memes_champs():
    for fr, en in _catalog().items():
        assert _fields(fr) == _fields(en), fr


def test_pas_de_doublon():
    seen = {}
    for name, entries in _files().items():
        for fr in entries:
            assert fr not in seen, f"{fr!r} dans {seen.get(fr)} et {name}"
            seen[fr] = name


def test_anglais(monkeypatch):
    monkeypatch.setattr(i18n, "_lang", "en")
    assert i18n._("Accueil") == "Home"
    assert i18n._("{n} fenêtre(s) remise(s) en place.", n=2) == "2 window(s) put back in place."
    assert i18n.N_("Accueil") == "Accueil"


def test_francais(monkeypatch):
    monkeypatch.setattr(i18n, "_lang", "fr")
    assert i18n._("Accueil") == "Accueil"
    assert i18n._("{n} fenêtre(s) remise(s) en place.", n=2) == "2 fenêtre(s) remise(s) en place."


def test_langue_lue_dans_la_config(monkeypatch):
    from vasistas import vm
    monkeypatch.setattr(vm, "load_config", lambda: {"language": "en"})
    monkeypatch.setattr(i18n, "_lang", None)
    assert i18n.current() == "en"
    assert i18n._("Accueil") == "Home"
    monkeypatch.setattr(vm, "load_config", lambda: {"language": "fr"})
    assert i18n.refresh() == "fr"
    assert i18n._("Accueil") == "Accueil"


def _binds_underscore(func):
    """Vrai si la fonction (hors fonctions et compréhensions imbriquées) lie `_` localement."""
    args = func.args
    names = [a.arg for a in args.posonlyargs + args.args + args.kwonlyargs]
    names += [a.arg for a in (args.vararg, args.kwarg) if a]
    if "_" in names:
        return True
    todo = list(ast.iter_child_nodes(func)) if not isinstance(func, ast.Lambda) else [func.body]
    while todo:
        node = todo.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef,
                             ast.ListComp, ast.SetComp, ast.DictComp, ast.GeneratorExp)):
            continue
        if isinstance(node, ast.Name) and node.id == "_" and isinstance(node.ctx, ast.Store):
            return True
        if isinstance(node, ast.ExceptHandler) and node.name == "_":
            return True
        todo.extend(ast.iter_child_nodes(node))
    return False


def _calls_underscore(func):
    body = [func.body] if isinstance(func, ast.Lambda) else func.body
    todo = list(body)
    while todo:
        node = todo.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            continue
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "_":
            return True
        todo.extend(ast.iter_child_nodes(node))
    return False


@pytest.mark.parametrize("module", MODULES)
def test_pas_de_variable_qui_masque_la_traduction(module):
    """Un `_` jetable dans une fonction qui appelle _() la masquerait (UnboundLocalError)."""
    tree = ast.parse((PKG / module).read_text(encoding="utf-8"))
    bad = [node.lineno for node in ast.walk(tree)
           if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
           and _calls_underscore(node) and _binds_underscore(node)]
    assert not bad
