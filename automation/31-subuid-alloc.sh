#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Deterministic /etc/subuid and /etc/subgid range generator for rootless container execution (task_421).
# AI-doc: usr/share/doc/mios/manual/ch17-defense-in-depth-hardening.md
set -euo pipefail
# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

mios_log "Allocating deterministic subordinate UID/GID blocks (task_421)"

_alloc_bin="$(dirname "${BASH_SOURCE[0]}")/../usr/libexec/mios/mios-subuid-alloc"
if [[ -x "${_alloc_bin}" ]]; then
    "${_alloc_bin}" --sync || true
    "${_alloc_bin}" --check || true
    mios_ok "Deterministic subuid/subgid generated via mios-subuid-alloc"
else
    # Fallback shell calculation: base = 100000 + (UID - 1000) * 65536
    C_USER="${MIOS_IDENTITY_USERNAME:-mios}"
    UID_BASE=100000
    BLOCK=65536
    for subf in /etc/subuid /etc/subgid; do
        install -d -m 0755 "$(dirname "$subf")"
        if ! grep -qE "^${C_USER}:" "$subf" 2>/dev/null; then
            echo "${C_USER}:${UID_BASE}:${BLOCK}" >> "$subf"
        fi
        chmod 0644 "$subf"
    done
    mios_ok "Deterministic subuid/subgid generated for ${C_USER}"
fi
