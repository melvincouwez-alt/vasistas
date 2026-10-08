"""Imprimantes de Linux dans Windows : lecture de lpstat, script PowerShell, relais vers CUPS."""

import pytest

from vasistas import printers, printproxy

LPSTAT_V = """device for HP_LaserJet_M15: ipp://192.168.1.20/ipp/print
device for Brother-DCP: usb://Brother/DCP-L2530DW?serial=E78
device for PDF: cups-pdf:/
"""
LPSTAT_V_FR = "périphérique pour HP_LaserJet_M15 : ipp://192.168.1.20/ipp/print\n"


def test_lpstat_v():
    assert printers.parse_lpstat_v(LPSTAT_V) == [
        ("HP_LaserJet_M15", "ipp://192.168.1.20/ipp/print"),
        ("Brother-DCP", "usb://Brother/DCP-L2530DW?serial=E78"),
        ("PDF", "cups-pdf:/"),
    ]
    assert printers.parse_lpstat_v(LPSTAT_V_FR) == [("HP_LaserJet_M15", "ipp://192.168.1.20/ipp/print")]
    assert printers.parse_lpstat_v("lpstat: No destinations added.\n") == []


def test_lpstat_e_et_d():
    assert printers.parse_lpstat_e("HP_LaserJet_M15\nEPSON_ET_2810_Series\n\n") == [
        "HP_LaserJet_M15", "EPSON_ET_2810_Series"]
    assert printers.parse_lpstat_d("system default destination: PDF\n") == "PDF"
    assert printers.parse_lpstat_d("destination système par défaut : PDF\n") == "PDF"
    assert printers.parse_lpstat_d("no system default destination\n") is None


def test_imprimantes_de_linux(monkeypatch):
    out = {("-v",): LPSTAT_V + "device for bad'name: x:/\n", ("-d",): "system default destination: PDF\n"}
    monkeypatch.setattr(printers, "_lpstat", lambda *a: out.get(a, ""))
    found = printers.linux_printers()
    assert [p["name"] for p in found] == ["HP_LaserJet_M15", "Brother-DCP", "PDF"]  # nom douteux écarté
    assert [p["default"] for p in found] == [False, False, True]


def test_noms_et_adresses():
    assert printers.windows_name("HP_LaserJet_M15") == "HP LaserJet M15 (Linux)"
    assert printers.printer_url("HP_LaserJet_M15") == "http://10.0.2.6:631/printers/HP_LaserJet_M15"
    assert printers.printer_url("a+b@c") == "http://10.0.2.6:631/printers/a%2Bb%40c"


def test_script_powershell():
    script = printers.sync_script([{"name": "HP_LaserJet_M15"}, {"name": "PDF"}])
    assert "@{ Name = 'HP LaserJet M15 (Linux)'; Url = 'http://10.0.2.6:631/printers/HP_LaserJet_M15' }" in script
    assert "@{ Name = 'PDF (Linux)'; Url = 'http://10.0.2.6:631/printers/PDF' }" in script
    assert "$tag = 'Vasistas (Linux)'" in script
    assert "$driver = 'Microsoft IPP Class Driver'" in script
    assert "Add-Printer -Name $w.Name -IppURL $w.Url" in script
    assert "Remove-Printer" in script and "VASISTAS-RESULT" in script
    # aucune imprimante : tout ce que Vasistas a ajouté est retiré
    assert printers.sync_script([]).startswith("$want = @()\n")
    with pytest.raises(ValueError):
        printers.sync_script([{"name": "x'; Remove-Item C:\\ -Recurse; '"}])


def test_resultat():
    out = "bruit\nVASISTAS-RESULT {\"added\":[\"PDF (Linux)\"],\"removed\":[],\"kept\":[],\"errors\":[]}\n"
    assert printers.parse_result(out)["added"] == ["PDF (Linux)"]


def test_option_de_qemu(monkeypatch):
    assert printers.nic_options({"printers": False}) == ""
    monkeypatch.setattr(printers, "PROXY", printers.PROXY.with_name("a,b.py"))
    monkeypatch.setattr(printers.PROXY.__class__, "exists", lambda self: True)
    opt = printers.nic_options({"printers": True})
    assert opt.startswith(",guestfwd=tcp:10.0.2.6:631-cmd:")
    assert "a,,b.py" in opt  # virgule doublée pour QEMU


def test_relais_reecrit_l_entete():
    head = (b"POST /printers/PDF HTTP/1.1\r\nHost: 10.0.2.6:631\r\nContent-Type: application/ipp\r\n"
            b"Connection: Keep-Alive\r\nTransfer-Encoding: chunked\r\n\r\n")
    out = printproxy.rewrite_head(head)
    assert out.startswith(b"POST /printers/PDF HTTP/1.1\r\n")
    assert b"Host: localhost\r\n" in out and b"10.0.2.6" not in out
    assert b"Connection: close\r\n" in out and b"Keep-Alive" not in out
    assert b"Content-Type: application/ipp\r\n" in out and b"Transfer-Encoding: chunked\r\n" in out
    assert out.endswith(b"\r\n\r\n") and out.count(b"\r\n\r\n") == 1
