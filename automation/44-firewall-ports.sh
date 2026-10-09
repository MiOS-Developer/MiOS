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
# and the branch below it was dead on every build (T-1018).
_here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_miosd=""
for _c in "${MIOS_MIOSD_BIN:-}" \
          /usr/libexec/mios/miosd \
          "$_here/../src/mios-rs/target/release/miosd" \
          "$_here/../src/mios-rs/target/debug/miosd"; do
    if [ -n "$_c" ] && [ -x "$_c" ]; then _miosd="$_c"; break; fi
done

if [ -n "$_miosd" ]; then
    "$_miosd" firewall-ports
    mios_ok "Configured firewalld ports via miosd"
    exit 0
fi

# Derive open ports from SSOT [firewall.open_ports]
_ssot_ports=()
if python3 -c 'import tomllib' 2>/dev/null; then
    mapfile -t _ssot_ports < <(python3 -c '
import tomllib, os
path = "/usr/share/mios/mios.toml"
if not os.path.exists(path):
    path = os.path.join(os.path.dirname(__file__), "../usr/share/mios/mios.toml")
if os.path.exists(path):
    with open(path, "rb") as f:
        data = tomllib.load(f)
    fw = data.get("firewall", {}).get("open_ports", [])
    ports = data.get("ports", {})
    for k in fw:
        val = ports.get(k)
        if val is not None:
            print(f"{val}")
' 2>/dev/null || true)
fi

if [ "${#_ssot_ports[@]}" -gt 0 ]; then
    for port in "${_ssot_ports[@]}"; do
        firewall-offline-cmd --zone=public --add-port="${port}/tcp" || true
    done
else
    firewall-offline-cmd --zone=public --add-port=${MIOS_PORTS_HERMES}/tcp
    firewall-offline-cmd --zone=public --add-port=${MIOS_PORTS_OPEN_WEBUI}/tcp
    firewall-offline-cmd --zone=public --add-port=${MIOS_PORTS_CODE_SERVER:-8900}/tcp
    firewall-offline-cmd --zone=public --add-port=${MIOS_PORTS_GUACAMOLE_WEB}/tcp
    firewall-offline-cmd --zone=public --add-port=${MIOS_PORTS_CEPH_DASHBOARD}/tcp
    firewall-offline-cmd --zone=public --add-port=${MIOS_PORTS_K3S_API}/tcp
    firewall-offline-cmd --zone=public --add-port=${MIOS_PORTS_RDP}/tcp
    firewall-offline-cmd --zone=public --add-port=${MIOS_PORTS_FORGE_HTTP}/tcp
    firewall-offline-cmd --zone=public --add-port=${MIOS_PORTS_FORGE_SSH}/tcp
    firewall-offline-cmd --zone=public --add-port=${MIOS_PORTS_COCKPIT_LINK}/tcp
    firewall-offline-cmd --zone=public --add-port=${MIOS_PORTS_ADGUARD_UI:-8050}/tcp
    firewall-offline-cmd --zone=public --add-port=${MIOS_PORTS_SSH}/tcp
    firewall-offline-cmd --zone=public --add-port=${MIOS_PORTS_COCKPIT}/tcp
fi

firewall-offline-cmd --zone=public --add-port=${MIOS_PORTS_ADGUARD_DNS:-53}/tcp
firewall-offline-cmd --zone=public --add-port=${MIOS_PORTS_ADGUARD_DNS:-53}/udp
firewall-offline-cmd --zone=public --add-service=ssh
firewall-offline-cmd --zone=public --add-service=mios-pxe

