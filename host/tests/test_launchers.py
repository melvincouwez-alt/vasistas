"""Lanceurs .desktop selon les options : suffixe « (Windows) », masquage, emblème."""

import json

import pytest

from vasistas import desktop, icons, vm


@pytest.fixture
def home(tmp_path, monkeypatch):
    apps = tmp_path / "applications"
    apps.mkdir()
    monkeypatch.setattr(vm, "DATA", tmp_path)
    monkeypatch.setattr(vm, "CONFIG", tmp_path / "config.json")
    monkeypatch.setattr(desktop, "APPS_DIR", apps)
    refreshed = []
    monkeypatch.setattr(desktop, "refresh_icons", lambda: refreshed.append(True))
    monkeypatch.setattr(desktop, "_refresh_caches", lambda: None)

    def config(cfg):
        (tmp_path / "config.json").write_text(json.dumps(cfg))
    return apps, config, refreshed


def _fields(text):
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


def test_options_par_defaut(home):
    _apps, config, _r = home
    config({})
    assert desktop.launcher_options() == (False, True, set())
    config({"launcher_suffix": True, "launcher_emblem": False, "hidden_apps": ["excel"]})
    assert desktop.launcher_options() == (True, False, {"excel"})


def test_texte_du_lanceur():
    text = desktop.entry_text("winword", "Word", "x-office-document", "vasistas", "Office;")
    f = _fields(text)
    assert f["Name"] == "Word" and f["Categories"] == "Office;" and "NoDisplay" not in f
    assert f["Exec"] == "vasistas launch-app winword %F"
    assert f["StartupWMClass"] == desktop.app_desktop_id("winword") and f["X-Vasistas"] == "1"
    f = _fields(desktop.entry_text("winword", "Word", "i", "v", hidden=True, suffix=True))
    assert f["Name"] == "Word (Windows)" and f["NoDisplay"] == "true" and "Categories" not in f
    # pas de suffixe en double
    assert _fields(desktop.entry_text("a", "Word (Windows)", "i", "v", suffix=True))["Name"] == "Word (Windows)"


def test_nom_de_base():
    assert desktop.base_name("Word (Windows)") == "Word"
    assert desktop.base_name("Word") == "Word"
    assert desktop.base_name(" (Windows)") == "(Windows)"
    assert desktop.base_name("Bloc\nnotes") == "Bloc notes"


def test_reecriture_selon_les_options(home):
    apps, config, refreshed = home
    config({"launcher_suffix": True, "hidden_apps": ["excel"]})
    desktop.save_registry({"winword": {"name": "Word", "exe": "winword"},
                           "excel": {"name": "Excel", "exe": "excel"}})
    for app, name in (("winword", "Word"), ("excel", "Excel"), ("notepad", "Bloc-notes")):
        (apps / f"{desktop.app_desktop_id(app)}.desktop").write_text(
            desktop.entry_text(app, name, "i", "v", "Office;", hidden=app == "notepad"))
    other = apps / "autre.desktop"
    other.write_text("[Desktop Entry]\nName=Autre\n")
    desktop.apply_launcher_options()
    word = _fields((apps / f"{desktop.app_desktop_id('winword')}.desktop").read_text())
    excel = _fields((apps / f"{desktop.app_desktop_id('excel')}.desktop").read_text())
    notepad = _fields((apps / f"{desktop.app_desktop_id('notepad')}.desktop").read_text())
    assert word["Name"] == "Word (Windows)" and "NoDisplay" not in word
    assert excel["Name"] == "Excel (Windows)" and excel["NoDisplay"] == "true"
    # appli découverte, cachée par défaut : le reste (pas dans hidden_apps, mais pas révélée)
    assert notepad["Name"] == "Bloc-notes (Windows)" and notepad["NoDisplay"] == "true"
    assert other.read_text() == "[Desktop Entry]\nName=Autre\n"
    assert refreshed  # icônes recomposées (emblème)
    # retour au nom simple
    config({"launcher_suffix": False, "hidden_apps": []})
    desktop.apply_launcher_options()
    word = _fields((apps / f"{desktop.app_desktop_id('winword')}.desktop").read_text())
    assert word["Name"] == "Word"


def test_masquer_une_application(home):
    apps, config, _r = home
    config({})
    desktop.set_in_menu("excel", False, "Excel", "excel", "user")
    text = (apps / f"{desktop.app_desktop_id('excel')}.desktop").read_text()
    assert "NoDisplay=true" in text
    assert json.loads(vm.CONFIG.read_text())["hidden_apps"] == ["excel"]
    desktop.set_in_menu("excel", True)
    assert "NoDisplay=true" not in (apps / f"{desktop.app_desktop_id('excel')}.desktop").read_text()
    assert json.loads(vm.CONFIG.read_text())["hidden_apps"] == []


def _png():
    import cairo
    s = cairo.ImageSurface(cairo.FORMAT_ARGB32, 64, 64)
    cr = cairo.Context(s)
    cr.set_source_rgb(0.2, 0.4, 0.8)
    cr.rectangle(8, 8, 48, 48)
    cr.fill()
    return icons._png(s)


def test_embleme_facultatif():
    if not icons.EMBLEM.exists():
        pytest.skip("emblème absent")
    png = _png()
    assert icons.compose(png, 48, True) != icons.compose(png, 48, False)
