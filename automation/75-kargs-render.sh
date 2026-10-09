#!/usr/bin/env bash
# MIOS_APPLY_CLASS=bake-only
# AI-hint: Projects kernel arguments from mios.toml [kargs] SSOT into /usr/lib/bootc/kargs.d via miosd, resolved by absolute path.
# AI-related: usr/share/mios/mios.toml, usr/lib/bootc/kargs.d, src/mios-rs/miosd/src/main.rs, usr/lib/mios/log.sh
set -euo pipefail
# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done

mios_log "Kargs.d render"

TOML_FILE="${MIOS_TOML:-/usr/share/mios/mios.toml}"
KARGS_DIR="${KARGS_DIR:-/usr/lib/bootc/kargs.d}"
_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "$TOML_FILE" ]]; then
    mios_err "manifest file $TOML_FILE not found"
    exit 1
fi

if [[ ! -d "$KARGS_DIR" ]]; then
    mios_warn "$KARGS_DIR not found, skipping kargs render"
    exit 0
fi

# Absolute path, never `command -v`: miosd installs to /usr/libexec/mios, which
# is not on PATH at bake time, so the lookup this replaced could never succeed
# and the branch below it was dead on every build (task_962).
_miosd=""
for _c in "${MIOS_MIOSD_BIN:-}" \
          /usr/libexec/mios/miosd \
          "$_here/../src/mios-rs/target/release/miosd" \
          "$_here/../src/mios-rs/target/debug/miosd"; do
    if [ -n "$_c" ] && [ -x "$_c" ]; then _miosd="$_c"; break; fi
done

if [ -z "$_miosd" ]; then
    mios_err "miosd not found -- cannot render kargs. Build it: cd src/mios-rs && cargo build --release -p miosd"
    exit 2
fi

"$_miosd" render-kargs --toml "$TOML_FILE" --kargs-dir "$KARGS_DIR"
mios_ok "Kargs.d rendered via miosd"
