#!/bin/bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Captures the MIOS_DESKTOP_FLATPAKS build-time variable into a system-level environment file at ${MIOS_USR_DIR}/env.d/flatpaks.env to...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck source=usr/lib/mios/log.sh
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

mios_log "Capturing Flatpak environment"

mkdir -p ${MIOS_USR_DIR}/env.d

ENV_FILE="${MIOS_USR_DIR}/env.d/flatpaks.env"

echo "# 'MiOS' System Environment Definition" > "$ENV_FILE"
echo "# Generated at build time: $" >> "$ENV_FILE"

if [[ -n "${MIOS_DESKTOP_FLATPAKS:-}" ]]; then
    echo "MIOS_DESKTOP_FLATPAKS=\"${MIOS_DESKTOP_FLATPAKS}\"" >> "$ENV_FILE"
    mios_ok "Captured MIOS_DESKTOP_FLATPAKS to ${ENV_FILE}"
else
    echo "MIOS_DESKTOP_FLATPAKS=\"\"" >> "$ENV_FILE"
    mios_skip "MIOS_DESKTOP_FLATPAKS not set, created empty env file"
fi

chmod 644 "$ENV_FILE"

mios_ok "Flatpak environment configured in /usr"
