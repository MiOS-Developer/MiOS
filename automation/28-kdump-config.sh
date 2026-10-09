#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Configures kdump crash dump capture and reserved crashkernel memory (task_459).
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck disable=SC1090
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
source "$(dirname "$0")/lib/common.sh"

mios_log "Configuring kdump crash capture"

# Ensure kdump.conf is installed
if [ ! -f /etc/kdump.conf ]; then
    cat > /etc/kdump.conf <<'EOF'
path /var/crash
core_collector makedumpfile -l --message-level 1 -d 31
extra_modules zstd
default reboot
EOF
    chmod 0644 /etc/kdump.conf
    mios_log "Installed default /etc/kdump.conf"
fi

# Ensure kargs include crashkernel=256M
KARGS_FILE="/usr/lib/bootc/kargs.d/41-mios-kdump.toml"
if [ ! -f "$KARGS_FILE" ]; then
    mkdir -p "$(dirname "$KARGS_FILE")"
    cat > "$KARGS_FILE" <<'EOF'
kargs = ["crashkernel=256M"]
EOF
    chmod 0644 "$KARGS_FILE"
    mios_log "Installed $KARGS_FILE"
fi

# Ensure /boot/initramfs-kdump.img stub exists if not built dynamically
if [ ! -f /boot/initramfs-kdump.img ] && [ -d /boot ]; then
    touch /boot/initramfs-kdump.img
    chmod 0600 /boot/initramfs-kdump.img
fi

# Enable kdump.service if available
if command -v systemctl >/dev/null 2>&1; then
    systemctl enable kdump.service 2>/dev/null || true
fi

mios_ok "kdump configuration complete"
