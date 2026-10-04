"""Extensions ouvertes dans Windows : chemins, types MIME, lanceurs, applications par défaut."""

import json

import pytest

files = pytest.importorskip("vasistas.files")
from vasistas import desktop, vm  # noqa: E402

WORD_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setattr(vm, "DATA", data)
    monkeypatch.setattr(vm, "CONFIG", data / "config.json")
    docs, dl = tmp_path / "Documents", tmp_path / "Téléchargements"
    docs.mkdir()
    dl.mkdir()
    (data / "config.json").write_text(json.dumps({"user": "t", "shares": [
        {"tag": "Documents", "path": str(docs), "drive": "Z:", "label": "Documents"},
        {"tag": "Telechargements", "path": str(dl), "drive": "Y:", "label": "Téléchargements"},
    ]}))
    monkeypatch.setattr(desktop, "APPS_DIR", tmp_path / "applications")
    monkeypatch.setattr(desktop, "_refresh_caches", lambda: None)
    monkeypatch.setattr(desktop, "refresh_desktop_database", lambda: None)
    monkeypatch.setattr(files, "MIME_DIR", tmp_path / "mime")
    monkeypatch.setattr(files, "MIME_PACKAGE", tmp_path / "mime/packages/v.xml")
    monkeypatch.setattr(files, "MIMEAPPS", tmp_path / "mimeapps.list")
    return tmp_path


def _mimeapps(env):
    from gi.repository import GLib
    kf = GLib.KeyFile()
    kf.load_from_file(str(env / "mimeapps.list"), GLib.KeyFileFlags.NONE)
    return kf


def test_chemin_windows_par_les_partages(env):
    (env / "Documents/AO 2026").mkdir()
    f = env / "Documents/AO 2026/mémoire.docx"
    f.write_text("x")
    assert files.to_windows(f) == "Z:\\AO 2026\\mémoire.docx"
    assert files.to_windows(env / "Téléchargements/a.pbix") == "Y:\\a.pbix"
    assert files.to_windows(env / "ailleurs.docx") is None


def test_lien_symbolique_suivi(env):
    target = env / "Documents/vrai.xlsx"
    target.write_text("x")
    link = env / "lien.xlsx"
    link.symlink_to(target)
    assert files.to_windows(link) == "Z:\\vrai.xlsx"


def test_partage_le_plus_profond_gagne(env):
    sub = env / "Documents/Projet"
    sub.mkdir()
    cfg = json.loads((env / "data/config.json").read_text())
    cfg["shares"].append({"tag": "Projet", "path": str(sub), "drive": "P:", "label": "Projet"})
    (env / "data/config.json").write_text(json.dumps(cfg))
    assert files.to_windows(sub / "a.docx") == "P:\\a.docx"


def test_copie_sans_ecraser(env):
    src = env / "a.docx"
    src.write_text("x")
    assert files.copy_target(src) == env / "Téléchargements/a.docx"
    (env / "Téléchargements/a.docx").write_text("y")
    assert files.copy_target(src) == env / "Téléchargements/a (2).docx"


def test_types_mime():
    assert files.mime_for(".DOCX") == WORD_MIME
    assert files.mime_for("msg") == "application/vnd.ms-outlook"
    # type trop large : propre à Vasistas, pour ne pas envoyer tous les .txt dans Windows
    assert files.mime_for("zzqlog") == "application/x-vasistas-zzqlog"
    assert files.siblings("xls") >= {"xls", "xlt"}
    # type ambigu : type Office imposé, pas de type maison qui capterait les fichiers Graphviz
    assert files.mime_for("dot") == "application/msword-template"
    assert "pot" in files.siblings("ppt")


def test_designation_par_defaut_puis_retour_a_l_application_d_avant(env):
    (env / "mimeapps.list").write_text(
        f"[Default Applications]\n{WORD_MIME}=lucarne-word.desktop\n\n"
        f"[Added Associations]\n{WORD_MIME}=lucarne-word.desktop;\n")
    files.apply()
    kf = _mimeapps(env)
    word = desktop.app_desktop_id("winword") + ".desktop"
    assert kf.get_string("Default Applications", WORD_MIME).startswith(word + ";")
    assert kf.get_string("Default Applications", "application/x-powerbi-report") == \
        desktop.app_desktop_id("pbidesktop") + ".desktop;"
    launcher = (desktop.APPS_DIR / word).read_text()
    assert "launch-app winword %F" in launcher
    assert WORD_MIME + ";" in launcher
    cfg = json.loads((env / "data/config.json").read_text())
    assert cfg["open_with"]["docx"] == "winword"
    assert cfg["open_with_previous"][WORD_MIME] == "lucarne-word.desktop"

    files.set_designation("docx", None)
    kf = _mimeapps(env)
    assert kf.get_string("Default Applications", WORD_MIME) == "lucarne-word.desktop;"
    assert word not in kf.get_string("Added Associations", WORD_MIME)
    assert WORD_MIME not in (desktop.APPS_DIR / word).read_text()
    assert WORD_MIME not in json.loads((env / "data/config.json").read_text())["open_with_previous"]


def test_extension_ajoutee_a_une_autre_application(env):
    files.apply()
    files.set_designation("csv", "excel")
    assert files.designations()["csv"] == "excel"
    assert "text/csv;" in (desktop.APPS_DIR / (desktop.app_desktop_id("excel") + ".desktop")).read_text()
    # une extension ne va qu'à une application
    files.set_designation("csv", "winword")
    assert "text/csv;" not in (desktop.APPS_DIR / (desktop.app_desktop_id("excel") + ".desktop")).read_text()
    assert files.app_for("/x/tableau.CSV") == "winword"
