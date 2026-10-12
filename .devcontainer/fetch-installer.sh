#!/usr/bin/env bash
# AI-hint: Downloads a shell installer to a file and refuses anything that is not a shell script, so devcontainer setup never pipes an unverified transfer into bash.

set -euo pipefail
[ "$#" -eq 2 ] || { echo "usage: fetch-installer.sh <url> <dest>" >&2; exit 2; }
url="$1"; dest="$2"
curl -fsSL --compressed --retry 4 --retry-delay 2 "$url" -o "$dest"
[ -s "$dest" ] || { echo "[fetch-installer] ERROR: downloaded installer is empty: $url" >&2; exit 1; }
if [ "$(head -c 2 "$dest")" != '#!' ]; then
    echo "[fetch-installer] ERROR: $url is not a shell script (no #! line; $(wc -c <"$dest") bytes) -- refusing to run it" >&2
    exit 1
fi
