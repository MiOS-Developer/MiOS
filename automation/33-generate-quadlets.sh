#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Automatically generates Quadlet configuration files (.pod, .container, .network) from the mios.toml SSOT at im...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

TOML_FILE="${MIOS_TOML:-${ROOT}/usr/share/mios/mios.toml}"
OUT_DIR="${ROOT}/usr/share/containers/systemd"

mios_log "Generating Quadlets with native mios-gen from ${TOML_FILE}"

TARGET_DIR="/usr/share/containers/systemd"
if [[ -w "$TARGET_DIR" ]]; then
    OUT_DIR="$TARGET_DIR"
fi

# Absolute path, never `command -v`: miosd installs to /usr/libexec/mios, which
# nothing puts on PATH at bake time, so the lookup this replaced could never
# succeed and the branch below it was dead on every build (task_962).
_miosd=""
for _c in "${MIOS_MIOSD_BIN:-}" \
          /usr/bin/miosd \
          /usr/libexec/mios/miosd \
          "${ROOT}/src/mios-rs/target/release/miosd"; do
    if [[ -n "$_c" && -x "$_c" ]]; then _miosd="$_c"; break; fi
done

[[ -n "$_miosd" ]] || { mios_err "Native miosd is required; install the SSOT release catalog"; exit 1; }
MIOS_ROOT="$ROOT" MIOS_VENDOR_TOML="$TOML_FILE" MIOS_POD_OUT="$OUT_DIR" "$_miosd" generate-quadlets
mios_ok "Quadlets generated into ${OUT_DIR} via native mios-gen"
