"""Indicateur du panneau : état, menu et catalogue, sans D-Bus."""

import json
import re
from pathlib import Path

import pytest

indicator = pytest.importorskip("vasistas.indicator")
from gi.repository import GLib  # noqa: E402

from vasistas import i18n, winctl  # noqa: E402

HOST = Path(__file__).resolve().parents[1] / "vasistas"

STOPPED = {"pid": None, "host": None}
STARTING = {"pid": 42, "host": {"guest_ready": False, "windows": 0}}
READY = {"pid": 42, "host": {"guest_ready": True, "windows": 3, "paused": False}}
SLEEPING = {"pid": 42, "host": {"guest_ready": True, "windows": 1, "paused": True}}


@pytest.fixture(autouse=True)
def francais(monkeypatch):
    monkeypatch.setattr(i18n, "_lang", "fr")


def labels(items):
    return [e.get("label") for e in items if e.get("type") != "separator"]


def actions(items):
    out = []
    for e in items:
        if e.get("action"):
            out.append(e["action"])
        out += actions(e.get("children", []))
    return out


def test_phases():
    assert indicator.phase(STOPPED) == "stopped"
    assert indicator.phase(STARTING) == "starting"
    assert indicator.phase(READY) == "ready"
    assert indicator.phase(SLEEPING) == "sleeping"
    # hôte absent mais QEMU lancé : comme le compagnon, « démarrage »
    assert indicator.phase({"pid": 42, "host": None}) == "starting"
    assert indicator.phase({**STOPPED, "busy": "start"}) == "starting"
    assert indicator.phase({**READY, "busy": "stop"}) == "stopping"


def test_textes_d_etat():
    assert indicator.status_text(STOPPED) == "Windows est arrêté"
    assert indicator.status_text(STARTING) == "Windows démarre…"
    assert indicator.status_text(SLEEPING) == "Windows est en veille"
    assert indicator.status_text(READY) == "Windows est prêt · 3 fenêtres"
    assert indicator.status_text({"pid": 1, "host": {"guest_ready": True, "windows": 1}}) == \
        "Windows est prêt · 1 fenêtre"
    assert indicator.status_text({"pid": 1, "host": {"guest_ready": True, "windows": 0}}) == "Windows est prêt"


def test_textes_en_anglais(monkeypatch):
    monkeypatch.setattr(i18n, "_lang", "en")
    assert indicator.status_text(READY) == "Windows is ready · 3 windows"
    assert "Start Windows" in labels(indicator.build_menu(STOPPED, [], "balanced"))


def test_fin_des_actions_en_cours():
    assert indicator.settle("start", STARTING) == "start"
    assert indicator.settle("start", READY) is None
    assert indicator.settle("stop", READY) == "stop"
    assert indicator.settle("stop", STOPPED) is None
    assert indicator.settle(None, READY) is None


def test_icones_symboliques_presentes():
    folder = HOST.parents[1] / "data/icons/hicolor/symbolic/apps"
    for p in ("stopped", "starting", "stopping", "ready", "sleeping"):
        name = indicator.icon_name(p)
        assert name.endswith("-symbolic")
        assert (folder / f"{name}.svg").exists(), name
    assert indicator.ICON_DIR == HOST.parents[1] / "data/icons"


def test_applications_ouvertes_puis_recentes():
    names = {"winword": "Word", "excel": "Excel"}
    apps = indicator.menu_apps(["excel", "excel"], ["winword", "excel", "notepad"], names, limit=2)
    assert apps == [("excel", "Excel"), ("winword", "Word")]
    assert indicator.menu_apps([], ["notepad"], names) == [("notepad", "notepad")]


def test_menu_windows_arrete():
    items = indicator.build_menu(STOPPED, [("winword", "Word")], "battery", {"winword": "icone-word"})
    assert items[0] == {"label": "Windows est arrêté", "enabled": False}
    acts = actions(items)
    assert ("launch", "winword") in acts and ("start",) in acts
    assert ("stop",) not in acts and ("sleep",) not in acts and ("reset",) not in acts
    assert acts[-2:] == [("companion",), ("quit",)]
    word = next(e for e in items if e.get("action") == ("launch", "winword"))
    assert word["icon"] == "icone-word"
    power = next(e for e in items if e.get("children"))
    radios = [c for c in power["children"] if c.get("toggle") == "radio"]
    assert [c["action"] for c in radios] == [("power", k) for k, _l, _d in winctl.RESOURCES]
    assert [c["checked"] for c in radios] == [True, False, False]
    # pas de rappel « prochain démarrage » quand Windows est arrêté
    assert len(power["children"]) == len(radios)


def test_menu_selon_l_etat():
    ready = actions(indicator.build_menu(READY, [], "balanced"))
    assert ("sleep",) in ready and ("stop",) in ready and ("reset",) in ready and ("start",) not in ready
    asleep = actions(indicator.build_menu(SLEEPING, [], "balanced"))
    assert ("wake",) in asleep and ("stop",) in asleep and ("reset",) not in asleep and ("sleep",) not in asleep
    starting = actions(indicator.build_menu(STARTING, [], "balanced"))
    assert ("stop",) in starting and ("start",) not in starting
    stopping = actions(indicator.build_menu({**READY, "busy": "stop"}, [], "balanced"))
    assert not {("start",), ("stop",), ("sleep",), ("wake",), ("reset",)} & set(stopping)
    power = next(e for e in indicator.build_menu(READY, [], "balanced") if e.get("children"))
    assert power["children"][-1] == {"label": "S'applique au prochain démarrage de Windows", "enabled": False}


def test_arbre_dbusmenu():
    items = indicator.build_menu(READY, [("my_app", "Mon_appli")], "performance")
    root, acts = indicator.layout(items)
    nodes = list(indicator.all_nodes(root))
    ids = [n[0] for n in nodes]
    assert ids[0] == 0 and len(set(ids)) == len(ids)
    by_label = {n[1]["label"].get_string(): n for n in nodes if "label" in n[1]}
    # « _ » doublé : pas de raccourci clavier dans le nom de l'application
    assert acts[by_label["Mon__appli"][0]] == ("launch", "my_app")
    assert acts[by_label["Arrêter Windows"][0]] == ("stop",)
    assert by_label["Windows est prêt · 3 fenêtres"][1]["enabled"].get_boolean() is False
    power = by_label["Puissance de Windows"]
    assert power[1]["children-display"].get_string() == "submenu"
    perf = by_label["Performances"]
    assert perf[1]["toggle-type"].get_string() == "radio" and perf[1]["toggle-state"].get_int32() == 1
    assert any(n[1].get("type") and n[1]["type"].get_string() == "separator" for n in nodes)
    # format attendu par GetLayout, profondeur limitée et filtre des propriétés
    v = GLib.Variant("(u(ia{sv}av))", (1, indicator.node_variant(root)))
    assert v.get_type_string() == "(u(ia{sv}av))"
    shallow = indicator.node_variant(root, 0)
    assert shallow[2] == []
    only = indicator.node_variant(power, -1, ["label"])
    assert set(only[1]) == {"label"}
    # même menu, même signature : le panneau n'est pas prévenu pour rien
    again, _a = indicator.layout(indicator.build_menu(READY, [("my_app", "Mon_appli")], "performance"))
    assert indicator.signature(again) == indicator.signature(root)


def _literals(path, func):
    return set(re.findall(rf'(?<![\w.]){func}\("((?:[^"\\]|\\.)*)"', path.read_text()))


def test_catalogue_anglais_complet():
    en = {}
    for name in ("indicator.json", "winctl.json", "common.json"):
        en.update(json.loads((HOST / "locale/en" / name).read_text()))
    missing = (_literals(HOST / "indicator.py", "_") | _literals(HOST / "winctl.py", "N_")) - set(en)
    assert not missing
    # textes de l'interrupteur ajouté au compagnon
    switch = {t for t in _literals(HOST / "companion_settings.py", "_") if "ndicateur" in t or "panneau" in t}
    assert switch and switch <= set(en)


def test_app_id_identique_a_app_py():
    m = re.search(r'^APP_ID = "([^"]+)"', (HOST / "app.py").read_text(), re.M)
    assert m and m.group(1) == winctl.APP_ID


def test_menu_reconstruit_seulement_si_l_etat_change():
    from types import SimpleNamespace
    built = []
    f = SimpleNamespace(refreshing=True, busy=None, busy_since=0, state={}, windows_seen=None, open_apps=[],
                        menu_open=False, shown_key=None, again=False, remember=lambda apps: None,
                        schedule=lambda: None, rebuild=lambda: built.append(1))
    ready = {"pid": 42, "host": {"guest_ready": True, "windows": 1, "idle_s": 3}}
    indicator.Indicator._apply(f, dict(ready), ["winword"])
    indicator.Indicator._apply(f, dict(ready, host={**ready["host"], "idle_s": 8}), ["winword"])
    assert len(built) == 1  # seul idle_s a bougé
    indicator.Indicator._apply(f, dict(ready, host={**ready["host"], "windows": 2}), ["winword"])
    f.menu_open = True
    indicator.Indicator._apply(f, dict(ready, host={**ready["host"], "windows": 2}), ["winword"])
    assert len(built) == 3
