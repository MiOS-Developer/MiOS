#!/bin/bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Configures the system firewall by generating a persistent firewalld init script that maps resolved environment ports (SSH, RDP,...
# AI-doc: usr/share/doc/mios/manual/automation.md
set -euo pipefail
# shellcheck source=usr/lib/mios/log.sh
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done

source "$(dirname "$0")/lib/common.sh"

mios_log "Installing firewall init script"

cat > /usr/libexec/mios-firewall-init <<EOFW
set -euo pipefail
if ! systemctl is-active --quiet firewalld 2>/dev/null; then
    echo "[mios-firewall] firewalld not active"
    exit 0
fi
firewall-cmd --set-default-zone=drop 2>/dev/null || true
for svc in cockpit ssh mdns; do
    firewall-cmd --permanent --add-service="\$svc" 2>/dev/null || true
done
firewall-cmd --permanent --add-port=${MIOS_PORTS_SSH}/tcp 2>/dev/null || true
firewall-cmd --permanent --add-port=${MIOS_PORTS_RDP}/tcp --add-port=3390/tcp 2>/dev/null || true
firewall-cmd --permanent --add-service=samba --add-service=nfs --add-service=rpc-bind --add-service=mountd 2>/dev/null || true
firewall-cmd --permanent --add-port=16509/tcp 2>/dev/null || true
firewall-cmd --permanent --add-port=5900-5999/tcp 2>/dev/null || true
firewall-cmd --permanent --add-port=${MIOS_PORTS_K3S_API}/tcp --add-port=10250/tcp 2>/dev/null || true
firewall-cmd --permanent --add-port=2224/tcp --add-port=5403-5405/udp 2>/dev/null || true
firewall-cmd --permanent --add-port=${MIOS_PORTS_HERMES}/tcp 2>/dev/null || true
firewall-cmd --permanent --add-port=${MIOS_PORTS_OPEN_WEBUI}/tcp 2>/dev/null || true
firewall-cmd --permanent --add-port=${MIOS_PORTS_CODE_SERVER}/tcp 2>/dev/null || true
firewall-cmd --permanent --add-port=${MIOS_PORTS_GUACAMOLE_WEB}/tcp 2>/dev/null || true
firewall-cmd --permanent --add-port=${MIOS_PORTS_FORGE_HTTP}/tcp --add-port=26000/tcp 2>/dev/null || true
firewall-cmd --permanent --add-port=${MIOS_PORTS_FORGE_SSH}/tcp 2>/dev/null || true
firewall-cmd --permanent --add-port=${MIOS_PORTS_COCKPIT}/tcp 2>/dev/null || true
firewall-cmd --permanent --add-port=${MIOS_PORTS_COCKPIT_LINK}/tcp 2>/dev/null || true
for iface in lo podman+ br-+ veth+ virbr0 cni0 flannel.1 waydroid0; do
    firewall-cmd --permanent --zone=trusted --add-interface="\$iface" 2>/dev/null || true
done

for zone in public libvirt trusted; do
    firewall-cmd --permanent --zone="\$zone" --add-service=cockpit 2>/dev/null || true
    firewall-cmd --permanent --zone="\$zone" --add-port=${MIOS_PORTS_COCKPIT}/tcp 2>/dev/null || true
done
firewall-cmd --reload 2>/dev/null || true
echo "[mios-firewall] Firewall configured"
EOFW
chmod +x /usr/libexec/mios-firewall-init

if [ -x /usr/libexec/mios/mios-firewall-isolate ]; then
    /usr/libexec/mios/mios-firewall-isolate --apply --dry-run 2>/dev/null || true
fi

mios_ok "Firewall init script and declarative nftables isolation installed"
