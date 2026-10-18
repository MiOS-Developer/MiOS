#!/bin/bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Installs and configures virtualization (KVM/QEMU/Libvirt), container runtimes (Podman/Buildah), Cockpit management, and CrowdSec se...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck disable=SC1090  # log.sh resolves at runtime: build ctx or installed
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${SCRIPT_DIR}/lib/packages.sh"
source "${SCRIPT_DIR}/lib/common.sh"

mios_log "Install KVM/QEMU/Libvirt"
install_packages "virt"

mios_log "Install container runtime + self-build tools"
install_packages "containers"

install_packages "self-build"

mios_log "Install build toolchain"
install_packages "build-toolchain"

mios_log "Install Cockpit"
install_packages_strict "cockpit"

mios_log "Install boot + update management tools"
install_packages "boot"

mios_log "Install CrowdSec"
install_packages "security"

if [ -d /etc/crowdsec ]; then

    if [ -f /etc/crowdsec/config.yaml ]; then
        sed -i 's/^online_client:/# online_client:/' /etc/crowdsec/config.yaml 2>/dev/null || true
    fi
    mios_ok "CrowdSec sovereign/offline mode configured"
fi

mios_log "Install mDNS/DNS-SD discovery"
install_packages "network-discovery"

mios_log "Install Windows interop tools"
install_packages "wintools"

mios_log "Install gaming packages"
GAMING_PKGS=$(get_packages "gaming")
if [[ -n "$GAMING_PKGS" ]]; then
    ($DNF_BIN "${DNF_SETOPT[@]}" install -y "${DNF_OPTS[@]}" --skip-unavailable --exclude=udev-joystick-blacklist-rm $GAMING_PKGS) || {
        mios_warn "Some gaming packages failed to install"
    }
fi

mios_log "Install guest agents"
install_packages "guests"

mios_log "Install storage packages"
install_packages "storage"

mios_log "Install HA stack"
install_packages "ha"

mios_log "Install CLI utilities"
install_packages "utils"

mios_log "Install Waydroid"
install_packages "android"

mios_log "Download VirtIO-Win ISO"
VIRTIO_URL="https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/latest-virtio/virtio-win.iso"
mkdir -p ${MIOS_SHARE_DIR}/virtio
scurl -sL "$VIRTIO_URL" -o ${MIOS_SHARE_DIR}/virtio/virtio-win.iso 2>/dev/null || {
    mios_warn "VirtIO-Win ISO download failed"
}

mios_ok "Virtualization stack ready"

mkdir -p /etc/mios
/usr/libexec/mios/mios-metal-vfio-gen > /etc/mios/metal-vfio.env
mios_ok "Materialized metal-vfio.env"

/usr/libexec/mios/mios-metal-mesh-gen > /etc/mios/metal-mesh.env
mios_ok "Materialized metal-mesh.env"
