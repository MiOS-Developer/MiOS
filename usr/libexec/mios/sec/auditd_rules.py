#!/usr/bin/env python3
# AI-hint: Auditd rule generation, syntax validation, deployment, and security audit log parser.
# AI-related: tests/test-sec.py, usr/share/doc/mios/manual/sec.md
"""
MiOS Auditd Security Rules Manager and Configuration Access Monitor.
Generates Linux audit rules watching critical configuration paths (/etc/mios, /usr/share/mios),
validates auditctl syntax, handles deployment via augenrules, and parses audit events.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

class AuditdRulesManager:
    """Generates, validates, deploys, and parses auditd rules for MiOS system integrity."""

    DEFAULT_WATCHES = [
        {"path": "/etc/mios/", "perms": "wa", "key": "mios_config_change"},
        {"path": "/usr/share/mios/", "perms": "wa", "key": "mios_config_change"},
        {"path": "/etc/containers/policy.json", "perms": "wa", "key": "container_policy_change"},
        {"path": "/usr/share/containers/systemd/", "perms": "wa", "key": "quadlet_config_change"},
    ]

    def __init__(self, mock: bool = False, dry_run: bool = False) -> None:
        self.mock = mock
        self.dry_run = dry_run

    def generate_rules(
        self,
        watch_paths: Optional[List[Dict[str, str]]] = None,
    ) -> List[str]:
        """Generates auditd rules list according to auditctl syntax."""
        watches = watch_paths or self.DEFAULT_WATCHES
        rules: List[str] = [
            "# -----------------------------------------------------------------------------",
            "# MiOS Security Audit Rules — Configuration & Prompt Tamper Monitoring",
            "# -----------------------------------------------------------------------------",
            "-D",  # Delete existing rules
            "-b 8192",  # Buffer size
            "-f 1",  # Flag failure
        ]

        for w in watches:
            p = w.get("path", "")
            perms = w.get("perms", "wa")
            key = w.get("key", "mios_config_change")
            if p:
                rules.append(f"-w {p} -p {perms} -k {key}")

        return rules

    def validate_rules_syntax(self, rules: List[str]) -> Tuple[bool, List[str]]:
        """Validates syntax of audit rule lines against auditctl specifications."""
        errors: List[str] = []
        valid_perms = set("rwxa")

        for idx, line in enumerate(rules, 1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue

            parts = s.split()
            flag = parts[0]

            if flag == "-w":
                if len(parts) < 2:
                    errors.append(f"Line {idx}: -w missing path argument: '{s}'")
                    continue
                path = parts[1]
                if not path.startswith("/"):
                    errors.append(f"Line {idx}: -w path must be absolute: '{path}'")

                # Check optional perms and keys
                i = 2
                while i < len(parts):
                    if parts[i] == "-p":
                        if i + 1 >= len(parts):
                            errors.append(f"Line {idx}: -p missing permissions")
                            break
                        p_val = parts[i + 1]
                        if not all(c in valid_perms for c in p_val):
                            errors.append(f"Line {idx}: invalid permission flags: '{p_val}'")
                        i += 2
                    elif parts[i] == "-k":
                        if i + 1 >= len(parts):
                            errors.append(f"Line {idx}: -k missing key name")
                            break
                        i += 2
                    else:
                        errors.append(f"Line {idx}: unexpected parameter '{parts[i]}'")
                        break

            elif flag in ("-D", "-f", "-b", "-e", "-a", "-A", "-d", "-w"):
                pass  # Recognized auditctl control flags
            else:
                errors.append(f"Line {idx}: unrecognized auditctl flag: '{flag}'")

        return (len(errors) == 0, errors)

    def deploy_rules_file(
        self,
        rules: List[str],
        destination: str = "/usr/lib/audit/rules.d/90-mios-config.rules",
    ) -> bool:
        """Writes audit rules file and signals augenrules to reload rules."""
        if self.mock or self.dry_run:
            if not self.dry_run:
                os.makedirs(os.path.dirname(destination) or ".", exist_ok=True)
                with open(destination, "w", encoding="utf-8") as f:
                    f.write("\n".join(rules) + "\n")
            return True

        os.makedirs(os.path.dirname(destination), exist_ok=True)
        with open(destination, "w", encoding="utf-8") as f:
            f.write("\n".join(rules) + "\n")

        augenrules_bin = shutil.which("augenrules")
        if augenrules_bin:
            proc = subprocess.run([augenrules_bin, "--load"], capture_output=True, text=True)
            return proc.returncode == 0

        return True

    def parse_audit_events(
        self,
        events_log: str,
        key_tag: str = "mios_config_change",
    ) -> List[Dict[str, Any]]:
        """Parses raw audit log text for events matching the specified key tag."""
        content = ""
        if os.path.exists(events_log):
            with open(events_log, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        else:
            content = events_log

        events: List[Dict[str, Any]] = []
        pattern = re.compile(
            r"type=(PATH|SYSCALL|PROCTITLE)\s+msg=audit\(([\d\.]+):(\d+)\):\s*(.*)",
            re.MULTILINE,
        )

        for match in pattern.finditer(content):
            rec_type, timestamp, audit_id, body = match.groups()
            kv = dict(re.findall(r'(\w+)=("[^"]*"|\S+)', body))

            # Match key tag
            key_val = kv.get("key", "").strip('"')
            if key_tag and key_val != key_tag:
                continue

            events.append({
                "record_type": rec_type,
                "timestamp": timestamp,
                "audit_id": audit_id,
                "key": key_val,
                "syscall": kv.get("syscall", "").strip('"'),
                "exe": kv.get("exe", "").strip('"'),
                "name": kv.get("name", "").strip('"'),
                "comm": kv.get("comm", "").strip('"'),
            })

        return events

def main() -> int:
    parser = argparse.ArgumentParser(description="MiOS Auditd Rules Manager & Monitor")
    parser.add_argument("--generate", action="store_true", help="Generate audit rule definitions")
    parser.add_argument("--deploy", action="store_true", help="Deploy rules to /usr/lib/audit/rules.d/")
    parser.add_argument("--rule-file", default="/usr/lib/audit/rules.d/90-mios-config.rules", help="Path to audit rules file")
    parser.add_argument("--validate", action="store_true", help="Validate audit rule file syntax")
    parser.add_argument("--query-key", default="mios_config_change", help="Audit key tag to query/filter")
    parser.add_argument("--audit-log", default="/var/log/audit/audit.log", help="Path to audit.log file")
    parser.add_argument("--mock", action="store_true", help="Run with deterministic mock engine")
    parser.add_argument("--dry-run", action="store_true", help="Dry-run mode without writing")
    parser.add_argument("--json", action="store_true", help="Format output as JSON dictionary")

    args = parser.parse_args()
    manager = AuditdRulesManager(mock=args.mock, dry_run=args.dry_run)
    result: Dict[str, Any] = {"status": "ok", "mock": args.mock}

    try:
        rules = manager.generate_rules()

        if args.validate:
            valid, errs = manager.validate_rules_syntax(rules)
            result.update({"action": "validate", "valid": valid, "errors": errs})
            if not valid:
                result["status"] = "fail"

        elif args.deploy:
            deployed = manager.deploy_rules_file(rules, destination=args.rule_file)
            result.update({
                "action": "deploy",
                "deployed": deployed,
                "rule_file": args.rule_file,
                "rules_count": len([r for r in rules if not r.startswith("#")]),
            })

        elif args.audit_log and os.path.exists(args.audit_log):
            events = manager.parse_audit_events(args.audit_log, key_tag=args.query_key)
            result.update({"action": "parse_events", "events_found": len(events), "events": events})

        else:
            result.update({
                "action": "generate",
                "rules_count": len(rules),
                "rule_file": args.rule_file,
                "watches": manager.DEFAULT_WATCHES,
                "rules": rules,
            })

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"[+] Auditd Rules Manager: status={result.get('status')}")
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
