"""Points de restauration : noms, lecture de qemu-img, rotation, place prise (restore.py)."""

import shutil
import subprocess
import time

import pytest

from vasistas import i18n, restore

SAMPLE = """Snapshot list:
ID        TAG                 VM_SIZE                DATE       VM_CLOCK     ICOUNT
1         avant-allegement        0 B 2026-10-04 14:32:00   00:00:00.000          0
2         vas-a-20261004-143200   0 B 2026-10-04 14:32:00   00:00:00.000          0
3         vas-m-20261005-091500 1.5 GiB 2026-10-05 09:15:00   00:12:03.120         --
"""


@pytest.fixture(autouse=True)
def francais(monkeypatch):
    # langue fixée : ne lit pas la configuration de l'utilisateur
    monkeypatch.setattr(i18n, "_lang", "fr")


def ts(text):
    return time.mktime(time.strptime(text, "%Y-%m-%d %H:%M"))


def test_parse_list():
    snaps = restore.parse_list(SAMPLE)
    assert [s["tag"] for s in snaps] == ["avant-allegement", "vas-a-20261004-143200", "vas-m-20261005-091500"]
    assert snaps[0]["vm_size"] == 0
    assert snaps[2]["vm_size"] == int(1.5 * (1 << 30))
    assert snaps[1]["created"] == ts("2026-10-04 14:32")


def test_parse_list_empty():
    assert restore.parse_list("") == []
    assert restore.parse_list("Snapshot list:\nID TAG VM_SIZE DATE VM_CLOCK ICOUNT\n") == []


def test_auto_label_fr_en():
    when = ts(f"{time.localtime().tm_year}-10-04 14:32")
    assert restore.auto_label("windows-update", when, lang="fr") == "Avant mise à jour de Windows · 4 oct. 14:32"
    assert restore.auto_label("install", when, app="VLC", lang="fr") == "Avant installation de VLC · 4 oct. 14:32"
    assert restore.short_date(when, "en") == "Oct 4 14:32"
    # nom choisi à la main : gardé tel quel
    assert restore.auto_label("manual", when, name="Avant essai") == "Avant essai"


def test_short_date_other_year():
    assert restore.short_date(ts("2024-01-09 08:05"), "fr") == "9 janv. 2024 08:05"
    assert restore.short_date(ts("2024-01-09 08:05"), "en") == "Jan 9, 2024 08:05"


def test_new_tag_unique():
    when = ts("2026-10-04 14:32")
    assert restore.new_tag(True, when) == "vas-a-20261004-143200"
    assert restore.new_tag(False, when) == "vas-m-20261004-143200"
    taken = {"vas-a-20261004-143200", "vas-a-20261004-143200-2"}
    assert restore.new_tag(True, when, taken) == "vas-a-20261004-143200-3"


def test_rotation_keeps_newest_autos_and_all_manual():
    pts = [
        {"tag": "a1", "auto": True, "created": 1},
        {"tag": "m1", "auto": False, "created": 2},
        {"tag": "a2", "auto": True, "created": 3},
        {"tag": "a3", "auto": True, "created": 4},
        {"tag": "m2", "auto": False, "created": 5},
        {"tag": "a4", "auto": True, "created": 6},
    ]
    assert restore.to_rotate(pts, 3) == ["a1"]
    assert restore.to_rotate(pts, 2) == ["a1", "a2"]
    assert restore.to_rotate(pts, 10) == []
    # au moins un point automatique gardé, jamais un manuel
    assert restore.to_rotate(pts, 0) == ["a1", "a2", "a3"]
    assert restore.to_rotate([p for p in pts if not p["auto"]], 1) == []


@pytest.mark.skipif(not (shutil.which("qemu-img") and shutil.which("qemu-io")), reason="qemu-img absent")
def test_exclusive_sizes(tmp_path):
    disk = str(tmp_path / "d.qcow2")

    def run(*args):
        subprocess.run(args, check=True, capture_output=True)
    run("qemu-img", "create", "-q", "-f", "qcow2", disk, "1G")
    run("qemu-io", "-c", "write -P 1 0 8M", disk)
    run("qemu-img", "snapshot", "-c", "A", disk)
    run("qemu-io", "-c", "write -P 2 0 4M", disk)
    run("qemu-img", "snapshot", "-c", "B", disk)
    sizes = restore.exclusive_sizes(disk)
    mb = 1 << 20
    # A garde les 4 Mo réécrits depuis (plus une table L2) ; B ne garde rien en propre
    assert 4 * mb <= sizes["A"] <= 4 * mb + 128 * 1024
    assert sizes["B"] == 0
