#!/usr/bin/env bash
# Build the zip that Foundry downloads when installing by manifest URL.
#
# Foundry unpacks the archive straight into Data/modules/<module id>/, so
# module.json has to sit at the ROOT of the zip -- not inside a folder. That is
# why this zips the contents of magenta-music/ rather than the directory.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
out="$here/../dist/magenta-music.zip"

version=$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
          "$here/magenta-music/module.json" | head -1)

mkdir -p "$(dirname "$out")"
rm -f "$out"
(cd "$here/magenta-music" && zip -qr "$out" . -x '.*' -x '__MACOSX/*')

echo "built $out (version $version)"
unzip -l "$out"
