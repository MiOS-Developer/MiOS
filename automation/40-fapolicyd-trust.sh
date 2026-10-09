#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Configures fapolicyd to use file-based trust (fs-verity) to enable secure, immutable application whitelisting on ComposeFS systems without boot delays.
# AI-related: fapolicyd.service
set -euo pipefail

# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done

mios_log "Set fapolicyd trust = file,rpmdb in /usr/lib and /etc fapolicyd.conf"

source "$(dirname "$0")/lib/common.sh"

# Absolute path, never `command -v`: miosd installs to /usr/libexec/mios, which
# nothing puts on PATH at bake time, so the lookup this replaced could never
# succeed and the branch below it was dead on every build (task_962).
_miosd=""
_here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for _c in "${MIOS_MIOSD_BIN:-}" \
          /usr/libexec/mios/miosd \
          "${_here}/src/mios-rs/target/release/miosd" \
          "${_here}/src/mios-rs/target/debug/miosd"; do
    if [[ -n "$_c" && -x "$_c" ]]; then _miosd="$_c"; break; fi
done

if [[ -n "$_miosd" ]]; then
    "$_miosd" harden
    mios_ok "Fapolicyd trust configured via miosd"
else
    for config in /usr/lib/fapolicyd/fapolicyd.conf /etc/fapolicyd/fapolicyd.conf; do
        if [[ -f "$config" ]]; then
            sed -i 's/^trust =.*/trust = file,rpmdb/' "$config" || true
        fi
    done
fi

systemctl enable fapolicyd.service
mios_ok "Trust = file,rpmdb set in fapolicyd.conf, fapolicyd.service enabled"
