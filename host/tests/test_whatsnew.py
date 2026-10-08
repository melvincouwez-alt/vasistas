"""« Quoi de neuf » : versions à montrer et fichier whatsnew.json (whatsnew.py)."""

from vasistas import whatsnew

DATA = {
    "0.6.0": {"fr": ["six"], "en": ["six en"]},
    "0.7.0": {"fr": ["sept"]},
    "0.5.0": {"fr": ["cinq"]},
}


def test_file_has_both_languages():
    data = whatsnew.load()
    assert "0.9.0" in data
    for v, texts in data.items():
        assert texts.get("fr") and texts.get("en"), v
        assert len(texts["fr"]) == len(texts["en"]), v
        assert not any("—" in t or "–" in t for t in texts["fr"] + texts["en"]), v


def test_pending_after_update():
    items = whatsnew.pending({"seen_version": "0.5.2"}, "fr", current="0.7.0", data=DATA)
    assert [v for v, _ in items] == ["0.7.0", "0.6.0"]


def test_nothing_when_seen():
    assert whatsnew.pending({"seen_version": "0.6.0"}, "fr", current="0.6.0", data=DATA) == []
    # préversion vue : la version finale n'apporte rien de plus
    assert whatsnew.pending({"seen_version": "0.6.0-beta.1"}, "fr", current="0.6.0", data=DATA) == []


def test_fresh_install_and_old_version():
    assert whatsnew.pending({}, "fr", current="0.6.0", data=DATA, fresh_install=True) == []
    # mise à jour depuis une version sans seen_version : tout ce qui est paru jusqu'ici
    assert [v for v, _ in whatsnew.pending({}, "fr", current="0.6.0", data=DATA)] == ["0.6.0", "0.5.0"]


def test_language_fallback():
    assert whatsnew.entries("en", DATA, since="0.6.0") == [("0.7.0", ["sept"])]
    assert whatsnew.entries("en", DATA, since="0.5.0", upto="0.6.0") == [("0.6.0", ["six en"])]
