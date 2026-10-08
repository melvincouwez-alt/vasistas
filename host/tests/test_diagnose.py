"""Diagnostic : anonymisation du rapport, liste des paquets (diagnose.py)."""

from vasistas import diagnose

HOME = "/home/marie"
SHARES = [("/home/marie/Documents", "Documents"), ("/home/marie/Projets Dupont", "Projets Dupont")]


def anon(text):
    return diagnose.anonymize(text, HOME, "marie", host="portable-marie", secrets=["s3cr3tP4ss"],
                              shares=SHARES, drives=["Z:", "X:"], windows_user="")


def test_password_never_appears():
    out = anon('config {"user": "vasistas", "password": "s3cr3tP4ss"}')
    assert "s3cr3tP4ss" not in out
    assert "<secret>" in out


def test_shared_files_hidden_with_spaces():
    out = anon("2026-10-04 INFO ouverture de /home/marie/Documents/Bilan 2025 final.docx -> Z:\\Bilan 2025 final.docx")
    assert "Bilan" not in out and "final" not in out
    assert "<partage" in out and "Z:\\<fichier>" in out


def test_custom_share_label_hidden():
    out = anon("partage ajouté : Projets Dupont (X:)")
    assert "Dupont" not in out


def test_generic_labels_kept():
    assert "Documents" in anon("lecteur Documents (Linux) remonté")


def test_home_user_host_email():
    out = anon("lecture de /home/marie/.local/share/vasistas/apps.json par marie sur portable-marie, "
               "compte marie.martin@example.org, ~/Images/vacances.png")
    assert "marie" not in out.replace("<utilisateur>", "")
    assert "portable-marie" not in out
    assert "example.org" not in out
    assert "vacances" not in out
    # chemins de configuration (dossiers cachés) gardés lisibles
    assert "~/.local/share/vasistas/apps.json" in out


def test_windows_profile_path():
    out = anon(r"C:\Users\Marie\AppData\Local\Temp\x.tmp")
    assert "Marie" not in out
    assert r"C:\Users\<utilisateur>\AppData" in out


def test_vasistas_words_untouched():
    # le compte Windows par défaut s'appelle « vasistas » : les noms de modules ne doivent pas bouger
    out = diagnose.anonymize("vasistas.app INFO prêt", HOME, "marie", windows_user="")
    assert out == "vasistas.app INFO prêt"


def test_required_packages_from_install_sh():
    pkgs = diagnose.required_packages()
    assert "qemu-system-x86" in pkgs and "virtiofsd" in pkgs
