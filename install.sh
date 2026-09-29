#!/bin/sh
# Installe Vasistas pour l'utilisateur courant, sans droits administrateur :
#   ~/.local/opt/vasistas/<version>   fichiers de l'application
#   ~/.local/opt/vasistas/current     lien vers la version active
#   ~/.local/bin/vasistas             commande (lanceurs du menu, démarrage automatique)
# Les paquets du système manquants sont listés avec la commande apt à lancer.
# --update : appelé par la mise à jour intégrée (pas de question, dépendances seulement signalées).
set -e
SRC="$(cd "$(dirname "$0")" && pwd)"
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$SRC/host/vasistas/version.py")"
ROOT="$HOME/.local/opt/vasistas"
BIN="$HOME/.local/bin"
UPDATE=0
[ "$1" = "--update" ] && UPDATE=1

echo "Vasistas $VERSION"

# 1. Paquets du système
missing=""
for pkg in python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-granite-7.0 qemu-system-x86 qemu-utils ovmf virtiofsd; do
    dpkg -s "$pkg" >/dev/null 2>&1 || missing="$missing $pkg"
done
if [ -n "$missing" ]; then
    echo "Paquets manquants :$missing"
    echo "  sudo apt install$missing"
    [ "$UPDATE" = 1 ] || { echo "Installez-les puis relancez ./install.sh"; exit 1; }
fi
if [ ! -w /dev/kvm ]; then
    echo "Attention : /dev/kvm inaccessible. Activez la virtualisation dans le BIOS, puis :"
    echo "  sudo usermod -aG kvm $USER   (et rouvrez la session)"
fi

# 2. Agent Windows : fourni compilé dans les versions publiées, compilé ici sinon
AGENT="$SRC/guest/Vasistas.Agent/bin/Release/net48/Vasistas.Agent.exe"
if [ ! -f "$AGENT" ]; then
    DOTNET="${DOTNET:-$(command -v dotnet || echo "$HOME/.dotnet/dotnet")}"
    if [ -x "$DOTNET" ]; then
        "$DOTNET" build -c Release -v quiet -nologo "$SRC/guest/Vasistas.Agent"
    else
        echo "Agent Windows absent et SDK .NET introuvable : utilisez une version publiée"
        echo "(archive vasistas-<version>.tar.gz), qui contient l'agent compilé."
        exit 1
    fi
fi

# 3. Fichiers
mkdir -p "$ROOT" "$BIN"
TARGET="$ROOT/$VERSION"
if [ "$(cd "$SRC" && pwd -P)" != "$(mkdir -p "$TARGET" && cd "$TARGET" && pwd -P)" ]; then
    rm -rf "$TARGET"
    mkdir -p "$TARGET"
    (cd "$SRC" && tar cf - --exclude=.git --exclude=__pycache__ --exclude=build --exclude=dist \
        --exclude=obj --exclude='*.log' --exclude=.venv .) | (cd "$TARGET" && tar xf -)
fi
ln -sfn "$VERSION" "$ROOT/current"

# 4. Commande
cat > "$BIN/vasistas" <<WRAP
#!/bin/sh
# Vasistas (version active : ~/.local/opt/vasistas/current ; -P : ignorer le dossier courant)
exec env PYTHONPATH="$ROOT/current/host" python3 -P -m vasistas "\$@"
WRAP
chmod 755 "$BIN/vasistas"

# 5. Lanceurs, icônes, types de fichiers
"$BIN/vasistas" desktop >/dev/null

# 6. Garder la version active et la précédente
ls -1dt "$ROOT"/*/ 2>/dev/null | grep -v "/current/$" | tail -n +3 | while read -r old; do
    [ "$(basename "$old")" = "$VERSION" ] || rm -rf "$old"
done

echo "Vasistas $VERSION installé. Ouvrez « Vasistas » dans le menu Applications."
[ "$UPDATE" = 1 ] && echo "Relancez Vasistas pour utiliser la nouvelle version ; l'agent Windows suit au prochain démarrage de Windows."
exit 0
