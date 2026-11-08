#!/usr/bin/env python3
"""
ratchet.py - The Two-Clock Execution Pipeline & Ratchet Routine Engine.
Operates across two speeds:
- Fast Clock: in-loop automated compiler/linter diagnostics injection and transient git rollback.
- Slow Clock (The Ratchet): codifies escaped failures into permanent outer-harness rules in HARNESS.md and settings.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional


@dataclass
class RatchetRule:
    rule_id: str
    failure_category: str  # M-CPE, X-CPE, AST_INVALIDATION, RECURSION_LIMIT, PATH_ESCAPE
    description: str
    enforcement_hook: str  # PreToolUse, PostToolUse, Stop
    date_codified: str = field(default_factory=lambda: datetime.now().isoformat())


class RatchetEngine:
    def __init__(self, repo_root: Optional[str] = None):
        self.repo_root = Path(repo_root or self._find_repo_root()).resolve()
        self.harness_file = self.repo_root / "HARNESS.md"
        self.rules_file = self.repo_root / ".devloop_rules.json"
        self.artifacts_dir = self.repo_root / ".devloop_artifacts"
        self.artifacts_dir.mkdir(parents=True, exist_ok=True)

    def _find_repo_root(self) -> Path:
        cur = Path.cwd()
        for parent in [cur] + list(cur.parents):
            if (parent / ".git").exists():
                return parent
        return cur

    def load_rules(self) -> List[RatchetRule]:
        if self.rules_file.exists():
            try:
                data = json.loads(self.rules_file.read_text(encoding="utf-8"))
                return [RatchetRule(**r) for r in data]
            except Exception:
                pass
        return []

    def save_rules(self, rules: List[RatchetRule]):
        self.rules_file.write_text(json.dumps([asdict(r) for r in rules], indent=2), encoding="utf-8")
        self._update_harness_md(rules)

    def _update_harness_md(self, rules: List[RatchetRule]):
        lines = [
            "# HARNESS.md - Permanent Outer-Harness Failure Ratchet Log",
            "",
            "Every agent mistake that escapes in-loop fast-clock recovery is permanently codified",
            "into this ledger to guarantee it cannot recur in subsequent autonomous runs.",
            "",
            "## Codified Outer-Harness Rules",
            "",
            "| Rule ID | Category | Enforcement Hook | Description | Date Added |",
            "| :--- | :--- | :--- | :--- | :--- |",
        ]
        for r in rules:
            lines.append(f"| `{r.rule_id}` | `{r.failure_category}` | `{r.enforcement_hook}` | {r.description} | {r.date_codified[:10]} |")
        lines.append("")
        self.harness_file.write_text("\n".join(lines), encoding="utf-8")

    def record_failure(self, rule_id: str, category: str, description: str, hook: str = "PreToolUse"):
        rules = self.load_rules()
        rules.append(RatchetRule(
            rule_id=rule_id,
            failure_category=category,
            description=description,
            enforcement_hook=hook
        ))
        self.save_rules(rules)
        print(f"[RATCHET] Codified new permanent rule '{rule_id}' into HARNESS.md.")

    def rollback_fast_clock(self):
        print("==> Fast-Clock Trigger: Executing hard rollback to last verified git commit...")
        subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=self.repo_root, check=True)
        print("[ROLLBACK] Workspace restored to clean HEAD.")


def main():
    parser = argparse.ArgumentParser(description="Dev Loop Two-Clock Ratchet Engine")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    rec = subparsers.add_parser("record", help="Record an escaped failure into permanent outer-harness rules")
    rec.add_argument("--id", required=True, help="Unique rule ID (e.g., fence/path-traversal)")
    rec.add_argument("--category", required=True, choices=["M-CPE", "X-CPE", "AST_INVALIDATION", "PATH_ESCAPE", "TIMEOUT"], help="Failure category")
    rec.add_argument("--description", required=True, help="Rule description")
    rec.add_argument("--hook", default="PreToolUse", choices=["PreToolUse", "PostToolUse", "Stop"], help="Enforcement hook")

    subparsers.add_parser("rollback", help="Execute fast-clock transient recovery rollback")
    subparsers.add_parser("list", help="List codified ratchet rules")

    args = parser.parse_args()
    engine = RatchetEngine()

    if args.cmd == "record":
        engine.record_failure(args.id, args.category, args.description, hook=args.hook)
    elif args.cmd == "rollback":
        engine.rollback_fast_clock()
    elif args.cmd == "list":
        rules = engine.load_rules()
        for r in rules:
            print(f"- [{r.rule_id}] ({r.failure_category}): {r.description}")


if __name__ == "__main__":
    main()
