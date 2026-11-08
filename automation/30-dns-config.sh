#!/usr/bin/env bash
# MIOS_APPLY_CLASS=universal
# AI-hint: Installs the tracked systemd-resolved drop-in unchanged (host DNS stays on the discovered links) and fails the bake if a DNS= or FallbackDNS= entry would send unicast DNS into resolved's own mDNS listener.
# AI-doc: usr/share/doc/mios/manual/ch28-dynamic-network-and-firewall-management.md
# AI-related: usr/lib/systemd/resolved.conf.d/10-adguard.conf, etc/systemd/resolved.conf.d/10-adguard.conf, tests/test-adguard-dns.py
set -euo pipefail
# shellcheck source=/dev/null
for _mlog in "$(dirname "${BASH_SOURCE[0]}")/../usr/lib/mios/log.sh" /usr/lib/mios/log.sh; do [ -r "$_mlog" ] && . "$_mlog" && break; done
source "$(dirname "$0")/lib/common.sh"

CTX="${CTX:-/ctx}"
DESTDIR="${1:-}"  # the build passes none; tests/test-adguard-dns.py stages the install under a scratch root
MDNS_PORT=5353    # the drop-in sets MulticastDNS=yes, so resolved itself answers there (RFC 6762)
# The port of one resolved.conf server entry: ADDR[:PORT][%IFACE][#NAME], an IPv6 port as [ADDR]:PORT.
_dns_port() {
    local e="${1%%#*}"; e="${e%%\%*}"
    if [[ "$e" == \[*\]:* ]]; then echo "${e##*]:}"; elif [[ "$e" == *:*:* || "$e" != *:* ]]; then echo 53; else echo "${e##*:}"; fi
}

# /etc/systemd is outside the 01 overlay, so this phase is what ships the /etc copy; both are the tracked files.
for rel in usr/lib/systemd/resolved.conf.d/10-adguard.conf etc/systemd/resolved.conf.d/10-adguard.conf; do
    src="${CTX}/${rel}"
    [[ -f "$src" ]] || die "tracked resolved drop-in missing: ${src}"
    while IFS= read -r line; do
        [[ "$line" =~ ^[[:space:]]*(Fallback)?DNS[[:space:]]*=(.*)$ ]] || continue
        read -ra entries <<< "${BASH_REMATCH[2]}"
        for entry in "${entries[@]}"; do
            [[ "$(_dns_port "$entry")" != "$MDNS_PORT" ]] || die "${src}: '${entry}' sends unicast DNS into the local mDNS listener"
        done
    done < "$src"
    install -D -m 0644 "$src" "${DESTDIR}/${rel}"
done
mios_ok "systemd-resolved drop-in installed as tracked: no unicast DNS into the mDNS port"
