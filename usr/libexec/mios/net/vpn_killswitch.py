#!/usr/bin/env python3
# AI-hint: Declarative nftables VPN kill-switch and fwmark split-tunnel manager for MiOS.
# Enforces strict default-drop on non-VPN public WAN traffic while preserving local mesh connectivity via fwmark 0x100.
# AI-doc: usr/share/doc/mios/manual/ch28-dynamic-network-and-firewall-management.md
import argparse
import json
import os
import subprocess
import sys
from typing import Dict, List, Optional, Any

DEFAULT_LOCAL_CIDRS = [
    "10.0.0.0/8",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "127.0.0.0/8",
    "169.254.0.0/16",
    "fe80::/10",
    "::1/128",
]

DEFAULT_DNS_SERVER = "127.0.0.1"
DEFAULT_DNS_PORT = 53
DEFAULT_FWMARK = "0x100"
DEFAULT_VPN_TABLE = "inet mios_vpn_guard"

class VPNKillSwitchManager:
    """Manages declarative nftables rulesets for VPN kill-switch and fwmark split-tunneling."""

    def __init__(
        self,
        vpn_interface: str = "wg0",
        local_cidrs: Optional[List[str]] = None,
        dns_server: str = DEFAULT_DNS_SERVER,
        dns_port: int = DEFAULT_DNS_PORT,
        fwmark: str = DEFAULT_FWMARK,
        dry_run: bool = False,
    ):
        self.vpn_interface = vpn_interface
        self.local_cidrs = local_cidrs or DEFAULT_LOCAL_CIDRS
        self.dns_server = dns_server
        self.dns_port = dns_port
        self.fwmark = fwmark
        self.dry_run = dry_run

    def render_nftables_rules(self, vpn_peer_endpoints: Optional[List[str]] = None) -> str:
        """Renders the complete nftables ruleset enforcing kill-switch with local mesh bypass."""
        vpn_peer_endpoints = vpn_peer_endpoints or []
        local_ipv4_set = ", ".join([c for c in self.local_cidrs if ":" not in c])
        local_ipv6_set = ", ".join([c for c in self.local_cidrs if ":" in c])

        rules = [
            "# MiOS Declarative VPN Kill-Switch & Split-Tunnel Policy",
            "# Generated dynamically by vpn_killswitch.py",
            f"table {DEFAULT_VPN_TABLE} {{",
            "    # Set of local and cluster mesh IPv4 networks allowed to bypass VPN",
            "    set local_ipv4 {",
            "        type ipv4_addr",
            "        flags interval",
            f"        elements = {{ {local_ipv4_set} }}",
            "    }",
            "",
        ]

        if local_ipv6_set:
            rules.extend([
                "    set local_ipv6 {",
                "        type ipv6_addr",
                "        flags interval",
                f"        elements = {{ {local_ipv6_set} }}",
                "    }",
                "",
            ])

        rules.extend([
            "    chain prerouting {",
            "        type filter hook prerouting priority mangle; policy accept;",
            "    }",
            "",
            "    chain output_mangle {",
            "        type route hook output priority mangle; policy accept;",
            f"        # Mark local network and cluster mesh traffic for direct routing (fwmark {self.fwmark})",
            f"        ip daddr @local_ipv4 meta mark set {self.fwmark} accept",
        ])

        if local_ipv6_set:
            rules.append(f"        ip6 daddr @local_ipv6 meta mark set {self.fwmark} accept")

        rules.extend([
            "    }",
            "",
            "    chain output_filter {",
            "        type filter hook output priority filter; policy drop;",
            "",
            "        # 1. Allow loopback traffic",
            '        oifname "lo" accept',
            "",
            "        # 2. Allow established and related connections",
            "        ct state established,related accept",
            "",
            "        # 3. Allow traffic routed through the active VPN interface",
            f'        oifname "{self.vpn_interface}" accept',
            "",
            "        # 4. Allow local mesh / private LAN bypass marked with fwmark",
            f"        meta mark {self.fwmark} accept",
            "        ip daddr @local_ipv4 accept",
        ])

        if local_ipv6_set:
            rules.append("        ip6 daddr @local_ipv6 accept")

        rules.extend([
            "",
            "        # 5. Allow local DNS resolution strictly to AdGuard / local resolver",
            f"        ip daddr {self.dns_server} udp dport {self.dns_port} accept",
            f"        ip daddr {self.dns_server} tcp dport {self.dns_port} accept",
        ])

        for peer in vpn_peer_endpoints:
            if ":" in peer:
                parts = peer.rsplit(":", 1)
                ip, port = parts[0], parts[1]
                rules.append("        # Allow WireGuard VPN peer handshake endpoint")
                rules.append(f"        ip daddr {ip} udp dport {port} accept")

        rules.extend([
            "    }",
            "}",
        ])

        return "\n".join(rules) + "\n"

    def apply_rules(self, rules_content: str) -> Dict[str, Any]:
        """Applies the rendered nftables ruleset via nft CLI."""
        if self.dry_run:
            return {
                "status": "dry_run",
                "rules": rules_content,
                "applied": False,
            }

        try:
            res = subprocess.run(
                ["nft", "-f", "-"],
                input=rules_content,
                text=True,
                capture_output=True,
                check=True,
            )
            return {
                "status": "success",
                "table": DEFAULT_VPN_TABLE,
                "applied": True,
                "stdout": res.stdout.strip(),
            }
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            return {
                "status": "error",
                "message": str(exc),
                "applied": False,
                "rules": rules_content,
            }

    def flush_rules(self) -> Dict[str, Any]:
        """Flushes and removes the mios_vpn_guard table."""
        if self.dry_run:
            return {"status": "dry_run", "action": "flush", "flushed": False}

        try:
            subprocess.run(
                ["nft", "delete", "table", DEFAULT_VPN_TABLE],
                capture_output=True,
                check=True,
            )
            return {"status": "success", "action": "flush", "flushed": True}
        except (subprocess.CalledProcessError, FileNotFoundError) as exc:
            return {"status": "error", "action": "flush", "message": str(exc), "flushed": False}

def main():
    parser = argparse.ArgumentParser(description="MiOS Declarative VPN Kill-Switch and Split-Tunnel Manager")
    parser.add_argument("--interface", default="wg0", help="Active VPN interface name (default: wg0)")
    parser.add_argument("--dns-server", default=DEFAULT_DNS_SERVER, help="Local DNS server address (default: 127.0.0.1)")
    parser.add_argument("--dns-port", type=int, default=DEFAULT_DNS_PORT, help="Local DNS port (default: 53)")
    parser.add_argument("--fwmark", default=DEFAULT_FWMARK, help="Firewall mark for split tunnel (default: 0x100)")
    parser.add_argument("--peer", action="append", help="VPN peer endpoint (IP:PORT) to whitelist for handshakes")
    parser.add_argument("--render-only", action="store_true", help="Print rendered nftables rules without applying")
    parser.add_argument("--flush", action="store_true", help="Flush and remove the VPN killswitch ruleset")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without modifying firewall")
    args = parser.parse_args()

    mgr = VPNKillSwitchManager(
        vpn_interface=args.interface,
        dns_server=args.dns_server,
        dns_port=args.dns_port,
        fwmark=args.fwmark,
        dry_run=args.dry_run or args.render_only,
    )

    if args.flush:
        result = mgr.flush_rules()
        print(json.dumps(result, indent=2))
        return

    rules = mgr.render_nftables_rules(vpn_peer_endpoints=args.peer)

    if args.render_only:
        print(rules)
        return

    result = mgr.apply_rules(rules)
    print(json.dumps(result, indent=2))

if __name__ == "__main__":
    main()
