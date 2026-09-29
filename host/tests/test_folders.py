"""Dossiers de Windows reliés aux dossiers Linux : parties sans Windows."""

import json

import pytest

folders = pytest.importorskip("vasistas.folders")
from vasistas import vm  # noqa: E402


def test_dossiers_relies_par_lecteur(tmp_path, monkeypatch):
    monkeypatch.setattr(vm, "CONFIG", tmp_path / "config.json")
    (tmp_path / "config.json").write_text(json.dumps({"folders": {"documents": "Z:\\", "pictures": "X:\\"}}))
    assert folders.using_drive("z:") == ["documents"]
    assert folders.using_drive("W:") == []


def test_fichiers_windows_masques_une_seule_fois(tmp_path):
    folders._hide_clutter(tmp_path)
    folders._hide_clutter(tmp_path)
    assert (tmp_path / ".hidden").read_text().splitlines() == folders.WINDOWS_CLUTTER


def test_apostrophes_powershell():
    assert folders._q("L'été") == "'L''été'"


def test_proposition_par_defaut():
    assert folders.SUGGESTED == ["documents", "downloads", "pictures"]
