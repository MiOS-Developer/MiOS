#!/usr/bin/env bash
# AI-hint: bash Runs mios_fedora_release (automation/lib/common.sh) against fixture os-release files: a base image on the SSOT's Fedora release passes and prints it; a base on another release, or one naming no release, stops the build with the reason; 05-repos and 06-enable-external-repos take the release only from it.
# AI-related: automation/lib/common.sh, automation/05-repos.sh, automation/06-enable-external-repos.sh, usr/share/mios/mios.toml
# AI-doc: usr/share/doc/mios/manual/tests.md
set -euo pipefail

_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$_self_dir/.." && pwd)"
LIB="$ROOT/automation/lib/common.sh"

log() { printf '[test-fedora-release] %s\n' "$*"; }
die() { printf '[test-fedora-release] ERROR: %s\n' "$*" >&2; exit 1; }

grep -q '^mios_fedora_release()' "$LIB" || die "common.sh defines no mios_fedora_release()"
fix="$(mktemp -d)"
trap 'rm -rf "$fix"' EXIT

# rel OSRELEASE_TEXT PIN -> prints the helper's stdout; returns its status.
rel() {
    printf '%b' "$1" > "$fix/os-release"
    # shellcheck source=/dev/null
    ( source "$LIB" >/dev/null 2>&1; OS_RELEASE_FILE="$fix/os-release" MIOS_VERSIONS_FEDORA="$2" mios_fedora_release ) 2>"$fix/err"
}

log "Case 1: a base on the pinned release passes and prints it (quoted and bare)"
[[ "$(rel 'ID=fedora\nPLATFORM_ID="platform:f45"\n' 45)" == 45 ]] || { cat "$fix/err" >&2; die "case 1: quoted"; }
[[ "$(rel 'ID=mios\nPLATFORM_ID=platform:f45\n' 45)" == 45 ]] || { cat "$fix/err" >&2; die "case 1: bare"; }

log "Case 2: a base that rebased past the pin stops the build and names both releases"
if rel 'PLATFORM_ID="platform:f45"\n' 44 >/dev/null; then die "case 2: Fedora 45 base with pin 44 passed"; fi
grep -q 'Fedora 45 but \[versions\].fedora = 44' "$fix/err" || { cat "$fix/err" >&2; die "case 2: reason not reported"; }

log "Case 3: a base naming no Fedora release, or an unresolved pin, stops the build"
if rel 'ID=rhel\nPLATFORM_ID="platform:el9"\n' 45 >/dev/null; then die "case 3: non-Fedora base passed"; fi
if rel 'PLATFORM_ID="platform:f45"\n' '' >/dev/null; then die "case 3: empty pin passed"; fi

log "Case 4: the shipped resolver default equals the SSOT pin"
pin="$(python3 -c 'import sys,tomllib; print(tomllib.load(open(sys.argv[1],"rb"))["versions"]["fedora"])' "$ROOT/usr/share/mios/mios.toml")"
grep -q "^: \"\${MIOS_VERSIONS_FEDORA:=${pin}}\"" "$ROOT/automation/lib/globals.sh" \
    || die "case 4: globals.sh does not default MIOS_VERSIONS_FEDORA to [versions].fedora = $pin (run mios-gen sync)"

log "Case 5: the repo phases take the release only from mios_fedora_release"
for p in automation/05-repos.sh automation/06-enable-external-repos.sh; do
    grep -q '_fver="$(mios_fedora_release)" || exit 1' "$ROOT/$p" || die "case 5: $p does not resolve the release"
    if grep -q 'FEDORA_VERSION:-' "$ROOT/$p"; then die "case 5: $p still falls back to a literal release"; fi
done

log "PASS"
