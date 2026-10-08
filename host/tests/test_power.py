"""Profil de puissance automatique, plafond du ballon, arrêt automatique."""

import json

import pytest

from vasistas import balloon, power, sleep, vm


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setattr(vm, "DATA", tmp_path)
    monkeypatch.setattr(vm, "CONFIG", tmp_path / "config.json")

    def write(cfg):
        (tmp_path / "config.json").write_text(json.dumps(cfg))
    return write


@pytest.mark.parametrize("chosen,auto,battery,saver,rule,expected", [
    ("balanced", True, True, False, None, "battery"),
    ("balanced", True, False, False, None, "balanced"),
    ("balanced", True, False, True, None, "battery"),           # mode Économie du système
    ("balanced", False, True, True, None, "balanced"),          # automatique coupé
    ("balanced", True, True, False, "smooth", "performance"),   # Power BI au premier plan, même sur batterie
    ("performance", False, False, False, "eco", "battery"),
    ("performance", False, True, False, "balanced", "battery"),  # Équilibré réservé : suit l'alimentation
    ("performance", False, False, False, "balanced", "performance"),
])
def test_choix_du_profil(chosen, auto, battery, saver, rule, expected):
    assert power.choose_profile(chosen, auto, battery, saver, rule) == expected


def test_raison_affichee():
    assert power.reason("balanced", True, True) == "sur batterie"
    assert power.reason("balanced", True, False, rule="smooth") == "réglage de l'application au premier plan"
    assert power.reason("balanced", False, True) == "choisi"


def test_modes_par_application():
    assert power.app_modes({}) == {"pbidesktop": "smooth"}
    assert power.app_modes({"heavy_apps": ["powerbi", "Excel"]}) == {"pbidesktop": "smooth", "excel": "smooth"}
    assert power.app_modes({"app_modes": {"outlook": "eco", "x": "inconnu"}}) == {"outlook": "eco"}
    assert power.app_modes({"app_modes": {}}) == {}


def test_reglages_par_defaut(config):
    config({})
    assert power.settings() == ("balanced", True)
    config({"resources": "performance", "resources_auto": False})
    assert power.settings() == ("performance", False)
    config({"resources": "inconnu"})
    assert power.settings() == ("balanced", True)


def test_profil_au_demarrage(config, monkeypatch):
    monkeypatch.setattr(power, "system_profile", lambda: "balanced")
    monkeypatch.setattr(power, "on_battery", lambda: True)
    config({"resources": "performance"})
    assert vm.resources() == vm.RESOURCES["battery"]
    monkeypatch.setattr(power, "on_battery", lambda: False)
    assert vm.resources() == vm.RESOURCES["performance"]
    monkeypatch.setattr(power, "system_profile", lambda: "power-saver")
    assert vm.resources() == vm.RESOURCES["battery"]
    config({"resources": "performance", "resources_auto": False})
    monkeypatch.setattr(power, "on_battery", lambda: True)
    assert vm.resources() == vm.RESOURCES["performance"]


def test_batterie_par_sysfs():
    mains_on = {"type": "Mains", "online": "1"}
    mains_off = {"type": "Mains", "online": "0"}
    discharging = {"type": "Battery", "status": "Discharging", "scope": "System"}
    mouse = {"type": "Battery", "status": "Discharging", "scope": "Device"}
    assert power.battery_from_sysfs([mains_off, discharging])
    assert not power.battery_from_sysfs([mains_on, discharging])
    assert not power.battery_from_sysfs([mains_off, mouse])  # batterie de la souris
    assert not power.battery_from_sysfs([])
    assert not power.battery_from_sysfs([{"type": "USB", "online": "1"}, discharging])


def test_coeurs_sobres():
    perfs = {0: 196, 1: 202, 2: 208, 4: 125, 5: 125, 6: 125, 7: 125, 8: 125}
    assert power.efficient_cpus(perfs, 4) == {4, 5, 6, 7}
    assert power.efficient_cpus({}, 4) == set()
    assert power.efficient_cpus({0: 1, 1: 1}, 4) == {0, 1}


def test_memoire_et_ligne_de_commande():
    assert power.memory_bytes("6G") == 6 * 2 ** 30
    assert power.memory_bytes("512M") == 512 * 2 ** 20
    assert power.memory_bytes("2048") == 2048 * 2 ** 20
    assert power.memory_cap("battery") == 6 * 2 ** 30
    assert power.memory_cap("performance") is None
    argv = ["qemu", "-smp", "8,sockets=1,cores=8", "-m", "8G",
            "-nic", "user,model=virtio-net-pci,guestfwd=tcp:10.0.2.6:631-cmd:python3 p.py"]
    assert power.parse_qemu_cmdline(argv) == (8, 8 * 2 ** 30, True)
    assert power.parse_qemu_cmdline(["qemu", "-nic", "user"]) == (None, None, False)


def test_plafond_du_ballon():
    G = 2 ** 30
    assert balloon.capped(10 * G, 3 * G, None) == 10 * G
    assert balloon.capped(10 * G, 3 * G, 6 * G) == 6 * G
    # Windows utilise plus que le plafond : on ne descend pas sous son usage réel
    assert balloon.capped(10 * G, 7 * G, 6 * G) == 7 * G + balloon.HYSTERESIS
    assert balloon.capped(4 * G, 2 * G, 6 * G) == 4 * G


@pytest.mark.parametrize("minutes,windows,since,now,due", [
    (0, False, 0, 10_000, False),        # jamais
    (15, True, None, 10_000, False),     # une fenêtre ouverte
    (15, False, None, 10_000, False),    # pas encore mesuré
    (15, False, 0, 14 * 60, False),
    (15, False, 0, 15 * 60, True),
    (120, False, 100, 100 + 119 * 60, False),
    (120, False, 100, 100 + 120 * 60, True),
])
def test_arret_automatique(minutes, windows, since, now, due):
    assert sleep.auto_shutdown_due(minutes, windows, since, now) is due


def test_choix_d_arret_automatique():
    assert sleep.AUTO_SHUTDOWN_CHOICES == (0, 15, 30, 60, 120)
    assert sleep.DEFAULTS["auto_shutdown_min"] == 0


def test_demarrage_en_veille():
    import vasistas.companion_common  # noqa: F401 - fixe les versions de Gtk et Granite
    from vasistas.companion_windows import autostart_text
    assert "Exec=/bin/vasistas boot\n" in autostart_text("/bin/vasistas", False)
    assert "Exec=/bin/vasistas boot --sleep\n" in autostart_text("/bin/vasistas", True)


def test_mode_de_l_application_au_premier_plan(config, monkeypatch):
    monkeypatch.setattr(power, "on_battery", lambda: True)
    monkeypatch.setattr(power, "system_profile", lambda: "balanced")

    class Win:
        wid = 7
        active = True

        def is_active(self):
            return self.active

    class App:
        last_active = Win()
        infos = {7: {"app": "PBIDesktop"}}

    mgr = power.PowerManager.__new__(power.PowerManager)
    mgr.app = App()
    cfg = {"app_modes": {"powerbi": "smooth"}}
    assert mgr.current(cfg) == ("performance", "réglage de l'application au premier plan", "smooth")
    App.last_active.active = False  # fenêtre Linux au premier plan : le curseur reprend la main
    assert mgr.current(cfg) == ("battery", "sur batterie", None)
    assert power.capture_message(power.mode_values("smooth"), "performance") == \
        {"t": "capture", "occluded_ms": 250, "timer_ms": 1}


def test_recadrage_apercu():
    from vasistas.app import longest_run
    assert longest_run([0, 1, 2, 3, 40, 41]) == (0, 3)
    assert longest_run([0, 2, 4, 50]) == (0, 4)  # trous de 2 : même bloc
    assert longest_run([]) is None


def test_vsync_off_on_battery_profile():
    assert power.vsync_mode({"vsync": 7}, "battery") == 0
    assert power.vsync_mode({"vsync": 7}, "balanced") == 7
    assert power.vsync_mode({"vsync": 5}) == power.VSYNC_DEFAULT
