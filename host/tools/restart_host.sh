#!/bin/sh
# Relance l'hôte GTK (développement) : arrête l'instance en cours, attend sa fin, relance
# `vasistas run` détaché, journal dans ~/.local/share/vasistas/host.log.
HOST_DIR="$(cd "$(dirname "$0")/.." && pwd)"
pkill -f "python3 (-P )?-m vasistas (run|launch-app|launch|boot|open)( |$)"
for i in $(seq 1 40); do
    pgrep -f "python3 (-P )?-m vasistas (run|launch-app|launch|boot|open)( |$)" >/dev/null || break
    sleep 0.25
done
cd "$HOST_DIR" && setsid nohup env PYTHONPATH="$HOST_DIR" python3 -m vasistas run "$@" \
    >> "$HOME/.local/share/vasistas/host.log" 2>&1 < /dev/null &
