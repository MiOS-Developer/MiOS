#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Projects UPS settings from mios.toml [power.ups] SSOT into the NUT config directory via miosd, resolved by absolute path.
# AI-related: usr/share/mios/mios.toml, src/mios-rs/miosd/src/main.rs, usr/lib/mios/log.sh
set -euo pipefail
# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done

mios_log "NUT configuration render"

TOML_FILE="${MIOS_TOML:-/usr/share/mios/mios.toml}"
UPS_CONF_DIR="${UPS_CONF_DIR:-/etc/ups}"
_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ ! -f "$TOML_FILE" ]]; then
    mios_err "manifest file $TOML_FILE not found"
    exit 1
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
    mios_err "miosd not found -- cannot render NUT config. Build it: cd src/mios-rs && cargo build --release -p miosd"
    exit 2
fi

"$_miosd" render-nut --toml "$TOML_FILE" --out-dir "$UPS_CONF_DIR"
mios_ok "NUT configuration rendered via miosd"
