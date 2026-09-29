#!/bin/sh
# Installe l'assistant système qui prête la carte graphique dédiée à Windows (à lancer avec sudo).
set -e
here=$(dirname "$(readlink -f "$0")")
install -D -m 755 "$here/vasistas-gpu" /usr/local/libexec/vasistas-gpu
install -D -m 644 "$here/io.github.melvincouwez.vasistas.gpu.policy" \
    /usr/share/polkit-1/actions/io.github.melvincouwez.vasistas.gpu.policy
echo "Assistant installé : /usr/local/libexec/vasistas-gpu"
