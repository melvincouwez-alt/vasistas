"""Remplace l'agent dans l'invité sans redémarrer la VM.

L'exécutable compilé est envoyé en morceaux par `exec` (limite de taille des scripts),
puis un processus détaché arrête l'agent, attend sa fin réelle, retire la lecture seule
(fichiers copiés depuis le CD) et met la nouvelle version en place. La boucle de boot.ps1
relance l'agent ; s'il n'y en a pas, le script le relance lui-même.

Usage : python3 tools/update_agent.py   (depuis host/, hôte `vasistas run` en marche)
"""

import base64
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from vasistas import control, vm  # noqa: E402

EXE = Path(__file__).resolve().parents[2] / "guest/Vasistas.Agent/bin/Release/net48/Vasistas.Agent.exe"
DIR = r"C:\Program Files\Vasistas\Agent"
CHUNK = 12000


def run(script):
    res = control.request({"exec": script}, timeout=120)
    if res.get("code"):
        raise SystemExit(f"échec ({res.get('code')}) : {res.get('out')}")
    return res.get("out", "")


def main():
    data = base64.b64encode(EXE.read_bytes()).decode()
    run(f"Remove-Item '{DIR}\\agent.b64' -ErrorAction SilentlyContinue")
    for i in range(0, len(data), CHUNK):
        run(f"Add-Content -NoNewline -Path '{DIR}\\agent.b64' -Value '{data[i:i + CHUNK]}'")
    # script d'échange écrit dans un fichier (chaîne littérale PowerShell : aucune interpolation)
    # Windows permet de renommer un exécutable en cours d'exécution : la nouvelle version est
    # en place avant l'arrêt de l'agent, sa relance ne peut démarrer qu'elle.
    swap = f"""$d = '{DIR}'
attrib -r "$d\\*"
Remove-Item "$d\\Vasistas.Agent.exe.old" -Force -ErrorAction SilentlyContinue
Move-Item -Force "$d\\Vasistas.Agent.exe" "$d\\Vasistas.Agent.exe.old"
Move-Item -Force "$d\\Vasistas.Agent.exe.new" "$d\\Vasistas.Agent.exe"
Start-Sleep 1
Get-Process Vasistas.Agent -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep 4
if (-not (Get-Process Vasistas.Agent -ErrorAction SilentlyContinue)) {{ Start-Process "$d\\Vasistas.Agent.exe" }}
"""
    out = run(
        f"$d = '{DIR}'\n"
        f"[IO.File]::WriteAllBytes(\"$d\\Vasistas.Agent.exe.new\", [Convert]::FromBase64String((Get-Content -Raw \"$d\\agent.b64\")))\n"
        f"Remove-Item \"$d\\agent.b64\"\n"
        f"$swap = @'\n{swap}'@\n"
        f"Set-Content -Path \"$env:TEMP\\vasistas-swap.ps1\" -Value $swap -Encoding UTF8\n"
        f"Start-Process powershell.exe -WindowStyle Hidden -ArgumentList '-NoProfile','-ExecutionPolicy','Bypass','-File',\"$env:TEMP\\vasistas-swap.ps1\"\n"
        f"'déposé : ' + (Get-Item \"$d\\Vasistas.Agent.exe.new\").Length + ' octets'"
    )
    print(out.strip())
    # boot.ps1 recopie l'agent du CD de partage à chaque ouverture de session : sans ce CD
    # refait, le prochain redémarrage de Windows remettrait l'ancien agent
    vm.build_share(install=False)
    q = vm.Qmp(vm.QMP)
    try:
        q.execute("blockdev-change-medium", {"device": "share", "filename": str(vm.SHARE), "format": "raw"})
    finally:
        q.close()
    print("CD de partage refait")


if __name__ == "__main__":
    main()
