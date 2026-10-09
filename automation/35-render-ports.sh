#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Renders every [ports] entry from mios.toml into install.env as MIOS_PORT_* via miosd, resolved by absolute path.
# AI-related: usr/share/mios/mios.toml, src/mios-rs/miosd/src/main.rs, automation/lib/globals.sh
set -euo pipefail
# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done

TOML_FILE="/usr/share/mios/mios.toml"
ENV_FILE="/etc/mios/install.env"
_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

mios_log "Extract ports from $TOML_FILE to $ENV_FILE"

mkdir -p "$(dirname "$ENV_FILE")"
touch "$ENV_FILE"

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
    mios_err "miosd not found -- cannot render ports. Build it: cd src/mios-rs && cargo build --release -p miosd"
    exit 2
fi

"$_miosd" render-ports --toml "$TOML_FILE" --out "$ENV_FILE"
mios_ok "Wrote MIOS_PORT_* to $ENV_FILE via miosd"
