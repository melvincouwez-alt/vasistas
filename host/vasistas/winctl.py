"""Commandes de Windows partagées par le compagnon et l'indicateur du panneau : démarrer,
arrêter, mettre en veille, réveiller, puissance allouée. Sans GTK (l'indicateur n'en charge pas).

Les fonctions bloquent (vm.start attend QEMU, vm.stop l'arrêt de Windows) : à appeler hors du
fil de l'interface.
"""

import os
import subprocess
import sys
from pathlib import Path

from . import control, vm
from .i18n import N_

# même valeur que app.APP_ID (app.py charge GTK 4, l'indicateur ne l'importe pas)
APP_ID = "io.github.melvincouwez.Vasistas"
HOST_DIR = Path(__file__).resolve().parents[1]

# puissance allouée à Windows (config.json « resources », voir vm.RESOURCES) : clé, nom, détail
RESOURCES = [
    ("battery", N_("Économie d'énergie"), N_("4 cœurs, 6 Go de mémoire : la batterie tient plus longtemps")),
    ("balanced", N_("Équilibré"), N_("8 cœurs, 8 Go de mémoire")),
    ("performance", N_("Performances"), N_("12 cœurs, 12 Go de mémoire : pour Power BI et les gros classeurs")),
]


def host_ready():
    """État de l'hôte : None s'il ne tourne pas, sinon sa réponse à status."""
    if not control.SOCKET.exists():
        return None
    try:
        return control.request({"status": True}, timeout=0.5)
    except (OSError, ValueError):  # réponse tronquée : hôte en train de quitter
        return None


def spawn(*args):
    """Lance une commande vasistas détachée (hôte, lancement d'application)."""
    env = dict(os.environ, PYTHONPATH=str(HOST_DIR))
    vm.ensure_data()
    with open(vm.DATA / "host.log", "ab") as log:  # l'enfant garde sa copie du descripteur
        subprocess.Popen([sys.executable, "-m", "vasistas", *args], env=env, start_new_session=True,
                         stdout=log, stderr=log)


def start():
    """Démarre Windows, et l'hôte qui affiche ses fenêtres s'il ne tourne pas."""
    if host_ready() is None:
        spawn("run")
    vm.start()


def stop():
    vm.stop()


def restart():
    stop()
    start()


def sleep():
    """Met Windows en veille (l'hôte suspend la VM). OSError si l'hôte ne répond pas."""
    return control.request({"sleep": True}, timeout=5)


def wake():
    return control.request({"sleep": False}, timeout=5)


def resources():
    """Clé de la puissance choisie (balanced par défaut)."""
    key = vm.load_config().get("resources")
    return key if key in [k for k, _, _ in RESOURCES] else "balanced"


def set_resources(key):
    """Puissance allouée à Windows, appliquée au prochain démarrage."""
    c = vm.load_config()
    c["resources"] = key
    # pastille « Économie d'énergie » sur la carte d'ouverture des applications
    if key == "battery":
        c["mode"] = "battery"
    elif c.get("mode") == "battery":
        c.pop("mode")
    vm.save_config(c)
