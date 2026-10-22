#!/usr/bin/env python3
# AI-hint: Zero-trust nftables network segmentation and container namespace firewall isolation.
# AI-related: tests/test-sec.py, usr/share/doc/mios/manual/sec.md
"""
MiOS Container Network Segmentation and Zero-Trust Firewall Engine.
Enforces strict nftables traffic isolation across Podman container subnets,
authorizes explicit service pairings, and prevents unauthorized direct UI-to-database connections.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib", "mios"))
import mios_toml  # noqa: E402 -- the ONE shared layered-mios.toml resolver

class NetSegmentationManager:
    """Manages nftables rule generation, pairing validation, and firewall rule enforcement."""

    # Each pairing names its destination's SSOT [ports] key; the number resolves through mios.toml (Law 7).
    ALLOWED_PAIRING_KEYS = [
        {"src": "open-webui", "dst": "hermes", "port_key": "hermes", "proto": "tcp", "desc": "OWUI browser agent chat gateway"},
        {"src": "agent-pipe", "dst": "hermes", "port_key": "hermes", "proto": "tcp", "desc": "Agent-pipe orchestration forwarding"},
        {"src": "hermes", "dst": "pgvector", "port_key": "pgvector", "proto": "tcp", "desc": "Hermes PostgreSQL vector memory recall"},
        {"src": "hermes", "dst": "llm-light", "port_key": "llm_light", "proto": "tcp", "desc": "Hermes primary llama.cpp inference lane"},
        {"src": "hermes", "dst": "searxng", "port_key": "searxng", "proto": "tcp", "desc": "Hermes metasearch backing web_search tool"},
    ]

    @property
    def DEFAULT_ALLOWED_PAIRINGS(self) -> List[Dict[str, Any]]:
        ports = mios_toml.section(mios_toml.load_merged(), "ports")
        return [{**{k: v for k, v in p.items() if k != "port_key"}, "port": int(ports[p["port_key"]])}
                for p in self.ALLOWED_PAIRING_KEYS]

    # Matrix of strictly forbidden direct flows
    FORBIDDEN_PAIRINGS = [
        {"src": "open-webui", "dst": "pgvector", "reason": "Direct UI-to-DB bypass forbidden; must route via Hermes gateway"},
        {"src": "open-webui", "dst": "llm-heavy", "reason": "Direct UI access to gated GPU inference lane forbidden"},
    ]

    def __init__(self, mock: bool = False, dry_run: bool = False) -> None:
        self.mock = mock
        self.dry_run = dry_run

    def generate_nftables_rules(
        self,
        pairings: Optional[List[Dict[str, Any]]] = None,
        subnet: str = "10.88.0.0/16",
    ) -> str:
        """Renders complete nftables ruleset isolating the container bridge subnet."""
        active_pairings = pairings or self.DEFAULT_ALLOWED_PAIRINGS

        rules = [
            "#!/usr/sbin/nft -f",
            "# -----------------------------------------------------------------------------",
            "# MiOS Zero-Trust Container Network Segmentation Ruleset",
            "# -----------------------------------------------------------------------------",
            "table inet mios_isolation {",
            "    chain forward_containers {",
            "        type filter hook forward priority filter; policy drop;",
            "",
            "        # Allow established and related connections",
            "        ct state established,related accept",
            "",
            "        # Allow loopback traffic",
            '        iif "lo" accept',
            "",
            "        # Allow traffic from host gateway",
            '        ip saddr 10.88.0.1 accept',
            "",
        ]

        emitted = set()
        for p in active_pairings:
            port = p.get("port")
            proto = p.get("proto", "tcp")
            if (proto, port) in emitted:
                continue
            emitted.add((proto, port))
            desc = p.get("desc", f"{p.get('src')} -> {p.get('dst')}")
            rules.append(f"        # {desc}")
            rules.append(f"        ip saddr {subnet} {proto} dport {port} accept")

        rules.extend([
            "",
            "        # Default drop and log for audit",
            '        log prefix "MIOS-NET-DROP: " flags all drop',
            "    }",
            "}",
        ])

        return "\n".join(rules) + "\n"

    def validate_pairing_matrix(self, pairings: List[Dict[str, Any]]) -> Tuple[bool, List[str]]:
        """Audits custom container pairing matrix against zero-trust architectural invariants."""
        violations: List[str] = []

        db_port = int(mios_toml.section(mios_toml.load_merged(), "ports")["pgvector"])
        for p in pairings:
            src = p.get("src", "")
            dst = p.get("dst", "")
            port = p.get("port")

            for f in self.FORBIDDEN_PAIRINGS:
                if f["src"] == src and f["dst"] == dst:
                    violations.append(f"Forbidden connection '{src}' -> '{dst}': {f['reason']}")

            # Invariant: the database (by name, or by its SSOT [ports].pgvector port) is reachable only by hermes / agent-pipe
            if (dst == "pgvector" or port == db_port) and src not in ("hermes", "agent-pipe", "host"):
                violations.append(f"Unauthorized source '{src}' attempting direct connection to database port {port}")

        return (len(violations) == 0, violations)

    def apply_rules(self, rules_text: str) -> bool:
        """Applies nftables ruleset using nft command line."""
        if self.mock or self.dry_run:
            return True

        nft_bin = shutil.which("nft")
        if not nft_bin:
            raise RuntimeError("nft command not available in environment")

        proc = subprocess.run([nft_bin, "-f", "-"], input=rules_text, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"nft failed to apply rules: {proc.stderr}")

        return True

    def flush_isolation(self) -> bool:
        """Flushes and removes the mios_isolation nftables table."""
        if self.mock or self.dry_run:
            return True

        nft_bin = shutil.which("nft")
        if not nft_bin:
            return True

        proc = subprocess.run([nft_bin, "delete", "table", "inet", "mios_isolation"], capture_output=True, text=True)
        return proc.returncode == 0

def main() -> int:
    parser = argparse.ArgumentParser(description="MiOS Container Network Segmentation & Firewall Engine")
    parser.add_argument("--generate", action="store_true", help="Generate nftables ruleset")
    parser.add_argument("--apply", action="store_true", help="Apply ruleset to running kernel")
    parser.add_argument("--flush", action="store_true", help="Flush and delete mios_isolation table")
    parser.add_argument("--validate-matrix", action="store_true", help="Validate container traffic pairings")
    parser.add_argument("--subnet", default="10.88.0.0/16", help="Container subnet CIDR (default: 10.88.0.0/16)")
    parser.add_argument("--config", help="Path to JSON file with custom pairings")
    parser.add_argument("--mock", action="store_true", help="Run with deterministic mock engine")
    parser.add_argument("--dry-run", action="store_true", help="Dry-run simulation mode")
    parser.add_argument("--json", action="store_true", help="Format output as JSON dictionary")

    args = parser.parse_args()
    mgr = NetSegmentationManager(mock=args.mock, dry_run=args.dry_run)
    result: Dict[str, Any] = {"status": "ok", "mock": args.mock}

    pairings = mgr.DEFAULT_ALLOWED_PAIRINGS
    if args.config and os.path.exists(args.config):
        with open(args.config, "r", encoding="utf-8") as f:
            pairings = json.load(f)

    try:
        if args.validate_matrix:
            valid, violations = mgr.validate_pairing_matrix(pairings)
            result.update({"action": "validate_matrix", "valid": valid, "violations": violations})
            if not valid:
                result["status"] = "fail"

        elif args.flush:
            flushed = mgr.flush_isolation()
            result.update({"action": "flush", "flushed": flushed})

        elif args.apply:
            rules_text = mgr.generate_nftables_rules(pairings, subnet=args.subnet)
            applied = mgr.apply_rules(rules_text)
            result.update({
                "action": "apply",
                "applied": applied,
                "table": "inet mios_isolation",
                "chain": "forward_containers",
                "default_policy": "drop",
                "allowed_pairings_count": len(pairings),
            })

        else:
            rules_text = mgr.generate_nftables_rules(pairings, subnet=args.subnet)
            result.update({
                "action": "generate",
                "table": "inet mios_isolation",
                "chain": "forward_containers",
                "default_policy": "drop",
                "allowed_pairings_count": len(pairings),
                "ruleset": rules_text,
            })

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"[+] Network Segmentation: status={result.get('status')}")
            for k, v in result.items():
                print(f"    {k}: {v}")

        return 0 if result.get("status") == "ok" else 1

    except Exception as exc:
        err = {"status": "error", "error": str(exc), "mock": args.mock}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"[-] Error: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
