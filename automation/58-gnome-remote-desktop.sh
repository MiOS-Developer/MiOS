#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Remote desktop phase. With [remote.rdp].enabled it installs [packages.remote-desktop] (xrdp + wayvnc) and enables xrdp.service (SSOT-rendered config via /usr/libexec/mios/mios-rdp, PAM gateway auth, TLS) plus mios-rdp-firewall.service (RDP port on the tailnet zone only); the per-session capture (mios-rdp-vnc.service) is pulled in by mios-desktop.target. With it disabled, xrdp stays masked as before.
# AI-related: xrdp.service, xrdp-sesman.service, mios-rdp-firewall.service, /usr/lib/systemd/user/mios-rdp-vnc.service, /usr/libexec/mios/mios-rdp, /usr/share/mios/mios.toml [remote.rdp] [packages.remote-desktop]
set -euo pipefail
# shellcheck disable=SC1090  # log.sh resolves at runtime: build ctx or installed
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
source "${SCRIPT_DIR}/lib/packages.sh"

get() { MIOS_TOML_ROOT="$ROOT_DIR" python3 "$ROOT_DIR/usr/libexec/mios/mios-toml-get" "$@"; }

if [[ "$(get remote.rdp enabled false 2>/dev/null || echo false)" != true ]]; then
    mios_log "[remote.rdp].enabled=false: masking xrdp.service, xrdp-sesman.service"
    systemctl mask xrdp.service xrdp-sesman.service 2>/dev/null || true
    systemctl disable mios-rdp-firewall.service 2>/dev/null || true
    mios_ok "xrdp masked"
    exit 0
fi

mios_log "[remote.rdp]: RDP into the live Hyprland session (xrdp -> wayvnc)"
install_packages "remote-desktop"
systemctl unmask xrdp.service xrdp-sesman.service 2>/dev/null || true
# The RPM %post generates /etc/xrdp/{cert,key}.pem at build time; MiOS never
# uses them (mios-rdp creates a per-host cert under [remote.rdp].cert_dir on
# first start), so drop them instead of baking one key into every image.
rm -f /etc/xrdp/cert.pem /etc/xrdp/key.pem
systemctl enable xrdp.service mios-rdp-firewall.service
mios_ok "xrdp.service + mios-rdp-firewall.service enabled; mios-rdp-vnc.service rides mios-desktop.target"
