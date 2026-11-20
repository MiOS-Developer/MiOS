#!/usr/bin/env bash
# AI-hint: bash Runs the user-unit projection check exactly as 65-bake-hyprland.sh invokes it: the build's source tree passes the [unit_projection].max_undeclared_user ratchet, while an image root that also holds package-shipped user units fails it, so the bake must point the check at the source tree.
# AI-related: automation/65-bake-hyprland.sh, tools/native/mios-unit-gen/src/main.rs, usr/share/mios/mios.toml
# AI-doc: usr/share/doc/mios/manual/tests.md
set -euo pipefail

_self_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$_self_dir/.." && pwd)"
PHASE="$ROOT/automation/65-bake-hyprland.sh"

log() { printf '[test-bake-user-unit-check] %s\n' "$*"; }
die() { printf '[test-bake-user-unit-check] ERROR: %s\n' "$*" >&2; exit 1; }

GEN="${UNIT_GEN_BIN:-}"
if [[ -z "$GEN" ]]; then
    for c in "$ROOT"/tools/native/target/*/debug/mios-unit-gen "$ROOT"/tools/native/target/debug/mios-unit-gen; do
        [[ -x "$c" ]] && { GEN="$c"; break; }
    done
fi
if [[ -z "$GEN" ]]; then
    (cd "$ROOT/tools/native" && cargo build -q -p mios-unit-gen) || die "cannot build mios-unit-gen"
    for c in "$ROOT"/tools/native/target/*/debug/mios-unit-gen "$ROOT"/tools/native/target/debug/mios-unit-gen; do
        [[ -x "$c" ]] && { GEN="$c"; break; }
    done
fi
[[ -x "$GEN" ]] || die "mios-unit-gen not found"

line="$(grep -E '^/usr/libexec/mios/mios-unit-gen .*--user --check$' "$PHASE")" \
    || die "65-bake-hyprland.sh no longer runs the user-unit check"
[[ "$(grep -cE '^/usr/libexec/mios/mios-unit-gen .*--user --check$' "$PHASE")" == 1 ]] || die "expected one check line"

fix="$(mktemp -d)"
trap 'rm -rf "$fix"' EXIT

# The build's source tree, as the Containerfile stages it (/tmp/build).
src="$fix/src"
mkdir -p "$src/automation" "$src/usr/share/mios" "$src/usr/lib/systemd"
cp "$ROOT/usr/share/mios/mios.toml" "$src/usr/share/mios/mios.toml"
cp -a "$ROOT/usr/lib/systemd/user" "$src/usr/lib/systemd/user"
if [[ -d "$ROOT/usr/lib/mios/mios.d" ]]; then
    mkdir -p "$src/usr/lib/mios" && cp -a "$ROOT/usr/lib/mios/mios.d" "$src/usr/lib/mios/mios.d"
fi

# Run the phase's own command line with this tree as its SCRIPT_DIR parent.
run_phase_line() {
    ( cd "$fix" && SCRIPT_DIR="$src/automation" bash -c "${line/\/usr\/libexec\/mios\/mios-unit-gen/\"$GEN\"}" ) >"$fix/out" 2>&1
}

log "Case 1: the phase's check passes on the build source tree"
run_phase_line || { cat "$fix/out" >&2; die "case 1: the shipped source tree failed the bake check"; }

log "Case 2: an image-like root with package user units fails the same ratchet"
img="$fix/image"
cp -a "$src" "$img"
for i in $(seq 1 40); do
    printf '[Unit]\nDescription=package unit %s\n[Service]\nExecStart=/usr/bin/true\n' "$i" \
        > "$img/usr/lib/systemd/user/package-$i.service"
done
if "$GEN" --root "$img" --user --check >"$fix/out" 2>&1; then
    die "case 2: package-shipped user units did not trip the ceiling (the ratchet stopped counting)"
fi
grep -q 'CEILING' "$fix/out" || { cat "$fix/out" >&2; die "case 2: failure is not the ceiling"; }

log "Case 3: the phase never points the check at the image root"
if [[ "$line" == *"--root / "* ]]; then die "case 3: 65-bake-hyprland.sh checks the image root"; fi

log "PASS"
