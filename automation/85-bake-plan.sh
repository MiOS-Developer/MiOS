#!/usr/bin/env bash
# MIOS_APPLY_CLASS=bake-only
# AI-hint: Projects the sharded bake-plan files (.list) under /usr/lib/mios/bake/plan.d/ AI-related: usr/share/mios/mios.toml, to...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail

# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done

_self="${BASH_SOURCE[0]}"
_self_dir="$(cd "$(dirname "$_self")" && pwd)"
ROOT="$(cd "$_self_dir/.." && pwd)"

source "$_self_dir/lib/common.sh" 2>/dev/null || {
    mios_warn "Lib/common.sh unavailable"
    exit 0
}

mios_log "Projecting bake-plan lists from mios.toml SSOT"

# No `command -v miosd` branch: unreachable at bake (task_962), and it ran the
# Python anyway. Below dispatches by path, never by lookup.

if [[ -x "/usr/libexec/mios/mios-bake-plan" ]]; then
    mios_log "Using native /usr/libexec/mios/mios-bake-plan"
    if ! /usr/libexec/mios/mios-bake-plan; then
        mios_err "failed to generate bake plan lists (native binary)"
        exit 1
    fi
elif [[ -x "${ROOT}/tools/native/target/release/mios-bake-plan" ]]; then
    mios_log "Using native tools/native target release binary"
    if ! "${ROOT}/tools/native/target/release/mios-bake-plan"; then
        mios_err "failed to generate bake plan lists (native binary)"
        exit 1
    fi
else
    mios_err "no native mios-bake-plan binary found (/usr/libexec/mios/mios-bake-plan or tools/native/target/release/mios-bake-plan)"
    exit 1
fi

mios_ok "Bake-plan lists projected"
exit 0
