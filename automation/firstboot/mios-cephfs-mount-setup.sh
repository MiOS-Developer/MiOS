#!/usr/bin/env bash
# AI-hint: bash Firstboot setup script for CephFS mount/automount systemd template units and login profiles.
# AI-doc: usr/share/doc/mios/manual/firstboot.md

set -euo pipefail

if [ "${MIOS_STORAGE_CEPHFS_ENABLE:-false}" != "true" ]; then
    echo "[cephfs-mount-setup] CephFS integration disabled in SSOT"
    exit 0
fi

echo "[cephfs-mount-setup] Initializing systemd mount templates for CephFS"

mkdir -p /etc/systemd/system
mkdir -p /etc/profile.d

render_template() {
    local src="$1"
    local dest="$2"
    if command -v envsubst >/dev/null 2>&1; then
        envsubst < "$src" > "$dest"
    else
        sed -e "s|\${MIOS_STORAGE_CEPHFS_ENABLE}|${MIOS_STORAGE_CEPHFS_ENABLE:-false}|g" \
            -e "s|\${MIOS_STORAGE_CEPHFS_MONITORS}|${MIOS_STORAGE_CEPHFS_MONITORS:-127.0.0.1:6789}|g" \
            -e "s|\${MIOS_STORAGE_CEPHFS_FS_NAME}|${MIOS_STORAGE_CEPHFS_FS_NAME:-cephfs}|g" \
            -e "s|\${MIOS_STORAGE_CEPHFS_TENANT_ID}|${MIOS_STORAGE_CEPHFS_TENANT_ID:-mios}|g" \
            -e "s|\${MIOS_STORAGE_CEPHFS_DATA_POOL_HOT}|${MIOS_STORAGE_CEPHFS_DATA_POOL_HOT:-cephfs_data_hot}|g" \
            -e "s|\${MIOS_STORAGE_CEPHFS_DATA_POOL_BULK}|${MIOS_STORAGE_CEPHFS_DATA_POOL_BULK:-cephfs_data_bulk}|g" \
            -e "s|\${MIOS_STORAGE_CEPHFS_XDG_CACHE_HOME_OVERRIDE}|${MIOS_STORAGE_CEPHFS_XDG_CACHE_HOME_OVERRIDE:-/run/user/{uid}/.cache}|g" \
            -e "s|\${MIOS_STORAGE_CEPHFS_MOUNT_OPTIONS}|${MIOS_STORAGE_CEPHFS_MOUNT_OPTIONS:-noatime,fsc,_netdev}|g" \
            -e "s|\${MIOS_STORAGE_CEPHFS_KEYRING_DIR}|${MIOS_STORAGE_CEPHFS_KEYRING_DIR:-/etc/ceph/keyring.d}|g" \
            -e "s|\${MIOS_STORAGE_CEPHFS_AUTOMOUNT_IDLE_TIMEOUT_S}|${MIOS_STORAGE_CEPHFS_AUTOMOUNT_IDLE_TIMEOUT_S:-600}|g" \
            "$src" > "$dest"
    fi
}

render_template "/usr/share/mios/systemd/home-@.mount.tmpl" "/etc/systemd/system/home-@.mount"
render_template "/usr/share/mios/systemd/home-@.automount.tmpl" "/etc/systemd/system/home-@.automount"
render_template "/usr/share/mios/profile.d/mios-xdg-cephfs.sh" "/etc/profile.d/mios-xdg-cephfs.sh"

chmod +x /etc/profile.d/mios-xdg-cephfs.sh 2>/dev/null || true

if [ -x /usr/libexec/mios/mios-ceph-configure ]; then
    echo "[cephfs-mount-setup] Running mios-ceph-configure"
    /usr/libexec/mios/mios-ceph-configure || true
fi

if command -v systemctl >/dev/null 2>&1; then
    echo "[cephfs-mount-setup] Reloading systemd daemon"
    systemctl daemon-reload || true

    echo "[cephfs-mount-setup] Enabling cachefilesd service"
    systemctl enable --now cachefilesd || true

    echo "[cephfs-mount-setup] Enabling CephFS automount for mios user"
    systemctl enable "home-mios.automount" || true
else
    echo "[cephfs-mount-setup] systemctl not available"
fi

echo "[cephfs-mount-setup] Setup completed successfully"
