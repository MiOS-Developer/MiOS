#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Enables and symlinks security services (usbguard, auditd, fapolicyd) into the multi-user.target.wants directory and pr...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
source "$(dirname "${BASH_SOURCE[0]}")/lib/common.sh"

chmod 0600 /usr/lib/usbguard/usbguard-daemon.conf 2>/dev/null || true

# Absolute path, never `command -v`: miosd installs to /usr/libexec/mios, which
# nothing puts on PATH at bake time, so the lookup this replaced could never
# succeed and the branch below it was dead on every build (task_962). Same
# `miosd harden` stage 40 calls; it is idempotent, and 40 runs first.
_miosd=""
_h51="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
for _c in "${MIOS_MIOSD_BIN:-}" \
          /usr/libexec/mios/miosd \
          "${_h51}/src/mios-rs/target/release/miosd" \
          "${_h51}/src/mios-rs/target/debug/miosd"; do
    if [[ -n "$_c" && -x "$_c" ]]; then _miosd="$_c"; break; fi
done

if [[ -n "$_miosd" ]]; then
    "$_miosd" harden
    mios_ok "Hardening services enabled via miosd"
else
    WANTS=/usr/lib/systemd/system/multi-user.target.wants
    install -d -m 0755 "${WANTS}"

    mios_log "Enable hardening services"
    for unit in \
        usbguard.service \
        auditd.service \
        fapolicyd.service
    do
        if [[ -f "/usr/lib/systemd/system/${unit}" ]]; then
            ln -sf "../${unit}" "${WANTS}/${unit}"
            mios_ok "Enabled ${unit}"
        else
            mios_skip "${unit} not installed"
        fi
    done
fi

if command -v fagenrules &>/dev/null; then
    mios_log "Pre-generate fapolicyd trust database"
    chown -R fapolicyd:fapolicyd /etc/fapolicyd 2>/dev/null || true
    fagenrules --load 2>/dev/null || true
    fapolicyd-cli --update 2>/dev/null || true
fi

mios_ok "Hardening services wired"
# Install the committed [security.luks] projection, never re-derive it, so /etc
# carries exactly the bytes check_clevis_luks diffed.
_clevis_env="$(dirname "${BASH_SOURCE[0]}")/../etc/mios/clevis-luks.env"
[[ -f "${_clevis_env}" ]] || { mios_err "clevis-luks.env absent: ${_clevis_env}"; exit 1; }
install -D -m 0644 "${_clevis_env}" /etc/mios/clevis-luks.env
mios_ok "Installed the committed clevis-luks.env projection"

# Declarative Flatpak permission lockdown profile (task_433)
_fp_override="$(dirname "${BASH_SOURCE[0]}")/../usr/share/flatpak/overrides/global"
if [[ -f "${_fp_override}" ]]; then
    install -D -m 0644 "${_fp_override}" /usr/share/flatpak/overrides/global 2>/dev/null || true
    mios_ok "Installed global Flatpak lockdown profile"
fi
