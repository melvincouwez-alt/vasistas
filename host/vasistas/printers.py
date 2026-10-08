"""Imprimantes de Linux dans Windows (réglage `printers`, faux par défaut).

Chaque file d'impression de CUPS devient dans Windows une imprimante IPP (« Nom (Linux) »,
pilote de classe IPP de Microsoft), qui imprime par CUPS : pilotes, réseau et options restent
ceux de Linux.

Chemin réseau : la VM est en réseau user (slirp, voir vm.qemu_args). Plutôt que d'ouvrir CUPS
au réseau, QEMU redirige PRINT_ADDR:631, adresse virtuelle vue seulement de la VM, vers
printproxy.py (`guestfwd=…-cmd:`), qui parle au socket local de CUPS. La configuration de CUPS
d'Ubuntu (`Listen localhost:631` et `/run/cups/cups.sock`) suffit, sans rien y changer. La
redirection est posée au démarrage de QEMU : activer le partage demande un redémarrage de
Windows.

Synchronisation : au démarrage de Windows (PrinterSync, branché par SleepManager) et sur
demande (bouton « Actualiser » du compagnon). Les imprimantes ajoutées portent le commentaire
TAG ; seules celles-là sont retirées quand elles ne sont plus dans CUPS ou que le partage est
coupé.
"""

import json
import logging
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from urllib.parse import quote

from .i18n import _

log = logging.getLogger(__name__)

PRINT_ADDR = "10.0.2.6"   # adresse libre du réseau user de QEMU (10.0.2.2 : hôte, .3 : DNS)
PORT = 631
TAG = "Vasistas (Linux)"
SUFFIX = " (Linux)"
PROXY = Path(__file__).resolve().parent / "printproxy.py"
DRIVER = "Microsoft IPP Class Driver"
# noms de files CUPS : pas d'espace, de « / » ni de « # » ; on reste prudent pour PowerShell
_SAFE_NAME = re.compile(r"^[A-Za-z0-9_.@+\-]{1,127}$")


def enabled(cfg=None):
    if cfg is None:
        from . import vm
        cfg = vm.load_config()
    return bool(cfg.get("printers", False))


# -- lecture des imprimantes de Linux --

def parse_lpstat_v(text):
    """[(file, adresse)] d'après `lpstat -v` (anglais ou français)."""
    out = []
    for line in text.splitlines():
        m = re.match(r"^\s*(?:device for|périphérique pour)\s+(\S+?)\s*:\s*(\S+)\s*$", line)
        if m:
            out.append((m.group(1), m.group(2)))
    return out


def parse_lpstat_e(text):
    """Noms de destinations d'après `lpstat -e` (une par ligne)."""
    return [line.strip() for line in text.splitlines() if line.strip() and " " not in line.strip()]


def parse_lpstat_d(text):
    """Destination par défaut d'après `lpstat -d`, ou None."""
    m = re.search(r"(?:system default destination|destination système par défaut)\s*:\s*(\S+)", text)
    return m.group(1) if m else None


def _lpstat(*args):
    env = dict(os.environ, LC_ALL="C", LANG="C")
    try:
        res = subprocess.run(["lpstat", *args], capture_output=True, text=True, timeout=10, env=env)
    except (OSError, subprocess.SubprocessError):
        return ""
    return res.stdout


def linux_printers():
    """Files d'impression de CUPS : [{name, uri, default}]. Seules les files créées (lpstat -v) :
    les imprimantes seulement découvertes sur le réseau (lpstat -e) n'ont pas de file à
    laquelle Windows puisse envoyer."""
    queues = parse_lpstat_v(_lpstat("-v"))
    default = parse_lpstat_d(_lpstat("-d"))
    return [{"name": n, "uri": u, "default": n == default} for n, u in queues if _SAFE_NAME.match(n)]


def discovered_only():
    """Destinations vues par CUPS sans file créée (pour expliquer leur absence)."""
    queues = {n for n, _ in parse_lpstat_v(_lpstat("-v"))}
    return [n for n in parse_lpstat_e(_lpstat("-e")) if n not in queues]


# -- côté Windows --

def windows_name(name):
    return name.replace("_", " ") + SUFFIX


def printer_url(name):
    return f"http://{PRINT_ADDR}:{PORT}/printers/{quote(name, safe='')}"


def _ps(s):
    from . import vm
    return vm.ps_quote(s)


SYNC_PS = r"""
$res = @{ added = @(); removed = @(); kept = @(); errors = @() }
$mine = @(Get-Printer -ErrorAction SilentlyContinue | Where-Object { $_.Comment -eq $tag })
foreach ($p in $mine) {
  if (-not ($want | Where-Object { $_.Name -eq $p.Name })) {
    try { Remove-Printer -Name $p.Name -ErrorAction Stop; $res.removed += $p.Name }
    catch { $res.errors += ($p.Name + ' : ' + $_.Exception.Message) }
  }
}
foreach ($w in $want) {
  if ($mine | Where-Object { $_.Name -eq $w.Name }) { $res.kept += $w.Name; continue }
  try {
    try { Add-Printer -Name $w.Name -IppURL $w.Url -ErrorAction Stop }
    catch {
      # repli (Windows sans -IppURL) : port Internet vers la même adresse, pilote de classe IPP
      if (-not (Get-PrinterDriver -Name $driver -ErrorAction SilentlyContinue)) {
        Add-PrinterDriver -Name $driver -ErrorAction Stop }
      & rundll32.exe printui.dll,PrintUIEntry /if /b $w.Name /r $w.Url /m $driver /z /q
      foreach ($i in 1..10) { if (Get-Printer -Name $w.Name -ErrorAction SilentlyContinue) { break }; Start-Sleep 1 }
      if (-not (Get-Printer -Name $w.Name -ErrorAction SilentlyContinue)) { throw }
    }
    Set-Printer -Name $w.Name -Comment $tag -ErrorAction SilentlyContinue
    $res.added += $w.Name
  } catch { $res.errors += ($w.Name + ' : ' + $_.Exception.Message) }
}
'VASISTAS-RESULT ' + ($res | ConvertTo-Json -Compress)
"""


def sync_script(printers):
    """Script PowerShell : ajoute dans Windows les imprimantes données ([{name}], liste vide :
    retire celles de Vasistas) et retire celles de Vasistas qui n'y sont plus."""
    items = []
    for p in printers:
        name = p["name"]
        if not _SAFE_NAME.match(name):
            raise ValueError(_("nom d'imprimante invalide : {name}", name=repr(name)))
        items.append(f"  @{{ Name = {_ps(windows_name(name))}; Url = {_ps(printer_url(name))} }}")
    head = ("$want = @(\n" + ",\n".join(items) + "\n)\n" if items else "$want = @()\n")
    head += f"$tag = {_ps(TAG)}\n$driver = {_ps(DRIVER)}\n"
    return head + SYNC_PS


def parse_result(out):
    from .catalog import parse_result as parse
    return parse(out)


# -- côté QEMU --

def nic_options(cfg=None):
    """Options à ajouter à `-nic user` : redirection de PRINT_ADDR:631 vers printproxy.py."""
    if not enabled(cfg) or not PROXY.exists():
        return ""
    # -I -S : relais en bibliothèque standard seule, lancé à chaque connexion IPP ; ni site ni
    # variables PYTHON* à charger (~4 ms de moins par connexion)
    cmd = f"{shlex.quote(sys.executable)} -I -S {shlex.quote(str(PROXY))}"
    # QEMU sépare les options par des virgules : les doubler dans la commande
    return f",guestfwd=tcp:{PRINT_ADDR}:{PORT}-cmd:{cmd.replace(',', ',,')}"


def forward_active(pid=None):
    """Vrai si la VM en marche a été démarrée avec la redirection des imprimantes."""
    from . import power, vm
    pid = pid or vm.pid()
    if not pid:
        return False
    return power.parse_qemu_cmdline(power.qemu_cmdline(pid))[2]


class PrinterSync:
    """Synchronise les imprimantes à chaque démarrage de Windows (créé par SleepManager, dont la
    minuterie de 2 s appelle check)."""

    def __init__(self, app):
        self.app = app
        self.was_ready = False

    def check(self):
        ready = bool(self.app.guest_ready)
        if ready and not self.was_ready:
            try:
                self.sync()
            except Exception:  # noqa: BLE001
                log.exception("imprimantes")
        self.was_ready = ready

    def sync(self):
        from . import vm
        cfg = vm.load_config()
        if enabled(cfg):
            if not forward_active():
                log.info("imprimantes : Windows démarré sans le partage, au prochain démarrage")
                return
            printers = linux_printers()
        elif cfg.get("printers_cleanup"):
            printers = []  # partage coupé pendant que Windows était arrêté : retirer
        else:
            return

        def done(res):
            parsed = parse_result(res.get("out"))
            log.info("imprimantes : %s", json.dumps(parsed, ensure_ascii=False) if parsed else res.get("out", "")[-300:])
            if not printers and parsed and not parsed.get("errors"):
                c = vm.load_config()
                c.pop("printers_cleanup", None)
                vm.save_config(c)
        self.app.run_script(sync_script(printers), done)
