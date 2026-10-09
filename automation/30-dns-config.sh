#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: systemd-resolved to mios-adguard split-horizon DNS routing configurator (task_441).
# AI-doc: usr/share/doc/mios/manual/ch28-dynamic-network-and-firewall-management.md
set -euo pipefail
# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
source "$(dirname "$0")/lib/common.sh"

mios_log "Configuring systemd-resolved split-horizon routing to mios-adguard (task_441)"

install -d -m 0755 /etc/systemd/resolved.conf.d
install -d -m 0755 /usr/lib/systemd/resolved.conf.d

cat > /usr/lib/systemd/resolved.conf.d/10-adguard.conf <<'EOF'
# AI-hint: systemd-resolved to mios-adguard split-horizon DNS routing drop-in (task_441).
# AI-doc: usr/share/doc/mios/manual/ch28-dynamic-network-and-firewall-management.md

[Resolve]
DNS=127.0.0.1:5353
FallbackDNS=1.1.1.1 9.9.9.9
Domains=~mios ~cluster.local
DNSOverTLS=opportunistic
MulticastDNS=yes
LLMNR=no
EOF
chmod 0644 /usr/lib/systemd/resolved.conf.d/10-adguard.conf

# Mirror to /etc for runtime overlay compatibility
cp -f /usr/lib/systemd/resolved.conf.d/10-adguard.conf /etc/systemd/resolved.conf.d/10-adguard.conf
chmod 0644 /etc/systemd/resolved.conf.d/10-adguard.conf

mios_ok "systemd-resolved configured: DNS=127.0.0.1:5353 Domains=~mios ~cluster.local"
