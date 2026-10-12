#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Configures firewalld rules via firewall-offline-cmd to open specific TCP ports for MiOS services (Hermes, Open We...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck source=usr/lib/mios/log.sh
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done

source "$(dirname "$0")/lib/common.sh"

mios_log "Configuring firewalld ports for 'MiOS' services"

# Absolute path, never `command -v`: miosd installs to /usr/libexec/mios, which
# is not on PATH at bake time, so the lookup this replaced could never succeed
# and the branch below it was dead on every build (task_962).
_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_miosd=""
for _c in "${MIOS_MIOSD_BIN:-}" \
          /usr/libexec/mios/miosd \
          "$_here/../src/mios-rs/target/release/miosd" \
          "$_here/../src/mios-rs/target/debug/miosd"; do
    if [ -n "$_c" ] && [ -x "$_c" ]; then _miosd="$_c"; break; fi
done

if [ -z "$_miosd" ]; then
    mios_err "Native miosd is required to configure firewall ports (task_962)"
    exit 1
fi

"$_miosd" firewall-ports
mios_ok "Configured firewalld ports via miosd"

