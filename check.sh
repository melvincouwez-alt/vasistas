#!/bin/sh
# Vérifications avant chaque mise à jour de l'hôte ou de l'agent :
# syntaxe Python, tests de l'hôte, compilation de l'agent Windows.
set -e
cd "$(dirname "$0")"
echo "== syntaxe Python"
python3 -m compileall -q host/vasistas host/tools host/tests
echo "== tests de l'hôte"
python3 -m pytest -q host/tests
echo "== agent Windows"
DOTNET="${DOTNET:-$HOME/.dotnet/dotnet}"
out=$("$DOTNET" build -c Release -v quiet -nologo guest/Vasistas.Agent 2>&1) || { echo "$out"; exit 1; }
echo "$out" | grep -E "warning CS|avertissement CS" || true
echo "tout est bon"
