#!/usr/bin/env bash
# AI-hint: bash Firstboot setup script for XDG profile script symlink and user directories init systemd user service.
# AI-doc: usr/share/doc/mios/manual/firstboot.md

set -euo pipefail

if [ "${MIOS_STORAGE_CEPHFS_ENABLE:-false}" != "true" ]; then
    echo "[xdg-setup] CephFS integration disabled in SSOT"
    exit 0
fi

echo "[xdg-setup] Setting up XDG profiles and user directories initializer"

mkdir -p /etc/profile.d
ln -sf /usr/share/mios/profile.d/mios-xdg-cephfs.sh /etc/profile.d/mios-xdg-cephfs.sh
echo "[xdg-setup] Symlinked XDG profile script to /etc/profile.d/mios-xdg-cephfs.sh"

mkdir -p /etc/xdg
cp /usr/share/mios/xdg/user-dirs.defaults /etc/xdg/user-dirs.defaults
echo "[xdg-setup] Configured /etc/xdg/user-dirs.defaults"

_op_user="${MIOS_IDENTITY_USERNAME:-mios}"
if ! getent passwd "$_op_user" >/dev/null; then
    echo "[xdg-setup] Operator user $_op_user not found"
else
    _op_home=$(getent passwd "$_op_user" | cut -d: -f6)
    _user_systemd_dir="$_op_home/.config/systemd/user"

    mkdir -p "$_user_systemd_dir/default.target.wants"

    cp /usr/lib/systemd/system/mios-xdg-userdir-init.service "$_user_systemd_dir/mios-xdg-userdir-init.service"

    ln -sf "../mios-xdg-userdir-init.service" "$_user_systemd_dir/default.target.wants/mios-xdg-userdir-init.service"

    chown -R "$_op_user:" "$_op_home/.config"
    echo "[xdg-setup] Installed and enabled mios-xdg-userdir-init.service for $_op_user"
fi

echo "[xdg-setup] Setup completed successfully"
