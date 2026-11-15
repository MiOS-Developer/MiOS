#!/usr/bin/env python3
# AI-hint: Declarative nftables VPN kill-switch and fwmark split-tunnel manager for MiOS.
# Enforces strict default-drop on non-VPN public WAN traffic while preserving local mesh connectivity via fwmark 0x100.
# AI-doc: usr/share/doc/mios/manual/ch28-dynamic-network-and-firewall-management.md
import argparse
import json
import os
import re
import subprocess
import sys
from typing import Dict, List, Optional, Any

sys.path.insert(0, os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib", "mios"))
try:
    import mios_toml  # noqa: E402
except ImportError:  # the condition then fails visibly (255), never arms or skips
    mios_toml = None

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

# [network.vpn] in the layered SSOT decides whether the guard arms and names its tunnel.
SSOT_KILLSWITCH = "network.vpn.killswitch"
# systemd ExecCondition= semantics: 0 runs the unit, 1-254 skips it, 255 fails it.
CONDITION_ARM, CONDITION_SKIP, CONDITION_ERROR = 0, 1, 255
# mios_toml.operator_view reports an operator-tier toggle it refused in this reason clause.
_REFUSED_CLAUSE = "refused toggle(s) (not a boolean; the vendor/host value is kept): "
_IFNAME = re.compile(r"^[A-Za-z0-9_.-]{1,15}$")  # a Linux interface name (IFNAMSIZ - 1)

class VPNKillSwitchManager:
    """Manages declarative nftables rulesets for VPN kill-switch and fwmark split-tunneling."""

    def __init__(
        self,
        vpn_interface: Optional[str] = None,
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
        ])

        if self.vpn_interface:
            rules.extend([
                "        # 3. Allow traffic routed through the active VPN interface",
                f'        oifname "{self.vpn_interface}" accept',
                "",
            ])

        rules.extend([
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

def _note(message: str) -> None:
    print(f"mios-vpn-killswitch: {message}", file=sys.stderr)

class _BadSSOT(ValueError):
    """A [network.vpn] shape or value the guard cannot act on: the condition fails (255)."""

def _base_view():
    """Vendor + host tiers only: what an administrator declared."""
    return mios_toml.base_view()

def _operator_view():
    """Vendor + host in full, and from the operator's mios.html tier only the
    [security.operator_tier] toggles (network.vpn.killswitch among them)."""
    return mios_toml.operator_view()

def _refused_toggles(reason: str) -> set:
    if _REFUSED_CLAUSE not in reason:
        return set()
    tail = reason.split(_REFUSED_CLAUSE, 1)[1].split(";", 1)[0]
    return {name.strip() for name in tail.split(",") if name.strip()}

def _vpn_table(data):
    """[network.vpn] of a view, or None when it is not declared."""
    network = data.get("network")
    if network is None:
        return None
    if not isinstance(network, dict):
        raise _BadSSOT("[network] is not a table")
    vpn = network.get("vpn")
    if vpn is not None and not isinstance(vpn, dict):
        raise _BadSSOT("[network.vpn] is not a table")
    return vpn

def _killswitch(data, where: str):
    """[network.vpn].killswitch of a view: None (not declared), True or False."""
    vpn = _vpn_table(data)
    value = None if vpn is None else vpn.get("killswitch")
    if value is not None and not isinstance(value, bool):
        raise _BadSSOT(f"{where} [network.vpn].killswitch is a {type(value).__name__}, not a boolean")
    return value

def _interface(base) -> str:
    vpn = _vpn_table(base)
    interface = vpn.get("interface") if isinstance(vpn, dict) else None
    if not isinstance(interface, str) or not _IFNAME.match(interface):
        raise _BadSSOT("[network.vpn].interface is missing or not a valid interface name")
    return interface

def condition_exit(view=None, base=None) -> int:
    """ExecCondition= decision; reads the SSOT only and never touches the firewall.

    Tighten-only: the guard arms when the vendor/host tiers (base_view) set
    [network.vpn].killswitch = true, OR when the operator's mios.html tier
    (operator_view) does. A vendor/host true is final: the operator tier is then read
    for the journal only, so a process running as the operator can neither turn it off
    nor fail it open by breaking its own file.
    0: armed, with a valid vendor/host [network.vpn].interface.
    1: not armed -- false in every tier, or not declared (an older SSOT stays off).
    255: a tier the decision needs cannot be read, a value is not a boolean (the string
    "true" included), or the interface is not a valid name. The unit then fails visibly."""
    try:
        if mios_toml is None and (view is None or base is None):
            _note("the MiOS SSOT resolver (mios_toml) is not importable")
            return CONDITION_ERROR
        base_data = (base or _base_view)()
        host = _killswitch(base_data, "the vendor/host")
        if host is True:
            interface = _interface(base_data)
            try:
                data, reason, _taken = (view or _operator_view)()
                if SSOT_KILLSWITCH in _refused_toggles(reason) or _killswitch(data, "the operator") is not True:
                    _note("the operator tier's network.vpn.killswitch is ignored: the vendor/host true stands")
            except Exception as exc:  # noqa: BLE001 -- diagnostics only; the vendor/host true stands
                _note(f"the operator tier is unresolved ({exc}); the vendor/host true stands")
            _note(f"the vendor/host [network.vpn].killswitch is true; arming for {interface}")
            return CONDITION_ARM
        data, reason, _taken = (view or _operator_view)()
        if SSOT_KILLSWITCH in _refused_toggles(reason):
            _note("the operator tier's network.vpn.killswitch is not a boolean")
            return CONDITION_ERROR
        operator = _killswitch(data, "the operator")
        if host is None and operator is None:
            _note("[network.vpn] is not declared; not arming")
            return CONDITION_SKIP
        if operator is not True:
            _note("[network.vpn].killswitch is false; not arming")
            return CONDITION_SKIP
        interface = _interface(base_data)
        _note(f"the operator tier enables [network.vpn].killswitch; arming for {interface}")
        return CONDITION_ARM
    except _BadSSOT as exc:
        _note(str(exc))
        return CONDITION_ERROR
    except Exception as exc:  # noqa: BLE001 -- any failure must fail the unit, not skip it
        _note(f"SSOT unresolved: {exc}")
        return CONDITION_ERROR

def ssot_interface(view=None) -> str:
    """[network.vpn].interface from the vendor + host tiers (the operator tier never names it)."""
    return _interface(view() if view else _base_view())

def main():
    parser = argparse.ArgumentParser(description="MiOS Declarative VPN Kill-Switch and Split-Tunnel Manager")
    parser.add_argument("--interface", default=None, help="VPN interface name (default: [network.vpn].interface from the SSOT)")
    parser.add_argument("--dns-server", default=DEFAULT_DNS_SERVER, help="Local DNS server address (default: 127.0.0.1)")
    parser.add_argument("--dns-port", type=int, default=DEFAULT_DNS_PORT, help="Local DNS port (default: 53)")
    parser.add_argument("--fwmark", default=DEFAULT_FWMARK, help="Firewall mark for split tunnel (default: 0x100)")
    parser.add_argument("--peer", action="append", help="VPN peer endpoint (IP:PORT) to whitelist for handshakes")
    parser.add_argument("--render-only", action="store_true", help="Print rendered nftables rules without applying")
    parser.add_argument("--flush", action="store_true", help="Flush and remove the VPN killswitch ruleset")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without modifying firewall")
    parser.add_argument("--condition", action="store_true",
                        help="systemd ExecCondition=: exit 0 when [network.vpn].killswitch is true, 1 when it is "
                             "false or undeclared, 255 when the SSOT is unreadable or the value is not a boolean")
    args = parser.parse_args()

    if args.condition:
        sys.exit(condition_exit())

    if args.flush:
        mgr = VPNKillSwitchManager(dry_run=args.dry_run or args.render_only)
        result = mgr.flush_rules()
        print(json.dumps(result, indent=2))
        return

    interface = args.interface
    if interface is None:
        try:
            if mios_toml is None:
                raise ValueError("the MiOS SSOT resolver (mios_toml) is not importable")
            interface = ssot_interface()
        except Exception as exc:  # noqa: BLE001 -- refuse to render a guard without its tunnel
            print(json.dumps({"status": "error", "message": str(exc), "applied": False}, indent=2))
            sys.exit(1)
    elif not _IFNAME.match(interface):
        print(json.dumps({"status": "error", "message": "--interface is not a valid interface name",
                          "applied": False}, indent=2))
        sys.exit(1)

    mgr = VPNKillSwitchManager(
        vpn_interface=interface,
        dns_server=args.dns_server,
        dns_port=args.dns_port,
        fwmark=args.fwmark,
        dry_run=args.dry_run or args.render_only,
    )

    rules = mgr.render_nftables_rules(vpn_peer_endpoints=args.peer)

    if args.render_only:
        print(rules)
        return

    result = mgr.apply_rules(rules)
    print(json.dumps(result, indent=2))
    if result.get("status") == "error":
        sys.exit(1)

if __name__ == "__main__":
    main()
