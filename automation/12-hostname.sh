#!/bin/bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Sets the initial hostname template in /usr/lib/hostname.default based on the MIOS_IDENTITY_HOSTNAME build-arg to ensure a unique, stable...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck source=usr/lib/mios/log.sh
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

mios_log "Set default hostname template"

_hn="${MIOS_IDENTITY_HOSTNAME:-mios}"
install -d -m 0755 ${MIOS_USR_DIR}
echo "$_hn" > ${MIOS_USR_DIR}/hostname.default
mios_ok "Wrote ${MIOS_USR_DIR}/hostname.default: $_hn"
if [[ "$_hn" == "mios" ]]; then
    mios_log "Becomes mios-XXXXX on first boot via mios-init"
fi
