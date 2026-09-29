#!/bin/sh
# Archive d'une version publiée : dist/vasistas-<version>.tar.gz (agent Windows compilé compris)
# et dist/SHA256SUMS, à joindre à la version GitHub (la mise à jour intégrée vérifie l'empreinte).
set -e
cd "$(dirname "$0")/.."
VERSION="$(sed -n 's/^VERSION = "\(.*\)"/\1/p' host/vasistas/version.py)"
./check.sh
NAME="vasistas-$VERSION"
rm -rf "dist/$NAME" "dist/$NAME.tar.gz"
mkdir -p "dist/$NAME"
git ls-files -z | grep -zv "^docs/perf.jsonl$" | tar cf - --null -T - | (cd "dist/$NAME" && tar xf -)
mkdir -p "dist/$NAME/guest/Vasistas.Agent/bin/Release"
cp -a guest/Vasistas.Agent/bin/Release/net48 "dist/$NAME/guest/Vasistas.Agent/bin/Release/"
(cd dist && tar czf "$NAME.tar.gz" "$NAME" && rm -rf "$NAME" && sha256sum "$NAME.tar.gz" > SHA256SUMS)
echo "dist/$NAME.tar.gz"
cat dist/SHA256SUMS
