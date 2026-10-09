#!/usr/bin/env python3
# AI-hint: Interactive CLI and Quickshell diff auditor enabling operator inspection, approval, rejection, and bake staging.
# AI-related: usr/share/doc/mios/adr/0018-shutdown-diff-snapshotting-and-boot-cycle-accrual.md, usr/share/doc/mios/manual/ch63-diff-snapshotting-boot-accrual-and-hitl-rollin.md, tests/test-ux.py
# AI-functions: DiffAuditorEngine, atomic_write_json, main
"""
WS-DIFFCYCLE (task_412): Human-In-The-Loop Interactive Diff Auditor.
Provides terminal CLI ('mios diff audit') and Quickshell ('DiffReview.qml') back-end
for reviewing accrued boot-cycle diffs, approving safe/custom modifications,
rejecting suspicious mutations, and staging approved diffs for autonomous OCI image baking.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import time
from typing import Any, Dict, List, Optional, Set

DEFAULT_LEDGER_PATH = "/var/run/mios/accrued-diffs.json"
DEFAULT_STAGED_PATH = "/var/run/mios/staged-bake-diffs.json"

def atomic_write_json(target_path: str, data: Any) -> None:
    """Write JSON data to disk using an atomic replace pattern to prevent corruption."""
    parent = os.path.dirname(os.path.abspath(target_path))
    if parent and not os.path.exists(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except OSError:
            pass

    tmp_file = f"{target_path}.tmp.{os.getpid()}.{int(time.time() * 1000)}"
    payload = json.dumps(data, indent=2, sort_keys=True)
    try:
        with open(tmp_file, "w", encoding="utf-8") as f:
            f.write(payload)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_file, target_path)
    finally:
        if os.path.exists(tmp_file):
            try:
                os.remove(tmp_file)
            except OSError:
                pass

class DiffAuditorEngine:
    """Engine managing operator review and staging of accrued filesystem diffs."""

    def __init__(
        self,
        ledger_path: str = DEFAULT_LEDGER_PATH,
        staged_out: str = DEFAULT_STAGED_PATH,
        mock: bool = False,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> None:
        self.ledger_path = ledger_path
        self.staged_out = staged_out
        self.mock = mock
        self.dry_run = dry_run
        self.verbose = verbose
        self._ledger_cache: Optional[Dict[str, Any]] = None

    def _get_mock_ledger(self) -> Dict[str, Any]:
        """Return synthetic accrued diffs ledger for headless mock testing."""
        return {
            "schema_version": "1.0",
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "total_diffs": 3,
            "total_accrued": 3,
            "safe_count": 1,
            "high_risk_count": 2,
            "review_count": 0,
            "safe_diffs": [
                {
                    "path": "var/lib/mios/ai/skills/custom-agent.md",
                    "status": "??",
                    "type": "untracked",
                    "size_bytes": 1024,
                    "risk": "safe",
                    "last_observed": "20260826T210000Z",
                }
            ],
            "high_risk_diffs": [
                {
                    "path": "etc/pam.d/system-auth",
                    "status": "M ",
                    "type": "modified",
                    "size_bytes": 512,
                    "risk": "high-risk",
                    "last_observed": "20260826T210000Z",
                },
                {
                    "path": "etc/mios/profile.toml",
                    "status": "M ",
                    "type": "modified",
                    "size_bytes": 2048,
                    "risk": "high-risk",
                    "last_observed": "20260826T210000Z",
                },
            ],
            "review_diffs": [],
            "entries": [
                {
                    "path": "var/lib/mios/ai/skills/custom-agent.md",
                    "status": "??",
                    "type": "untracked",
                    "size_bytes": 1024,
                    "risk": "safe",
                    "last_observed": "20260826T210000Z",
                },
                {
                    "path": "etc/pam.d/system-auth",
                    "status": "M ",
                    "type": "modified",
                    "size_bytes": 512,
                    "risk": "high-risk",
                    "last_observed": "20260826T210000Z",
                },
                {
                    "path": "etc/mios/profile.toml",
                    "status": "M ",
                    "type": "modified",
                    "size_bytes": 2048,
                    "risk": "high-risk",
                    "last_observed": "20260826T210000Z",
                },
            ],
            "status": "ready_for_review",
        }

    def load_ledger(self) -> Dict[str, Any]:
        """Load accrued diffs ledger from disk or synthetic mock."""
        if self._ledger_cache:
            return self._ledger_cache

        if self.mock and not os.path.isfile(self.ledger_path):
            self._ledger_cache = self._get_mock_ledger()
            return self._ledger_cache

        if os.path.isfile(self.ledger_path):
            try:
                with open(self.ledger_path, "r", encoding="utf-8") as f:
                    self._ledger_cache = json.load(f)
                    return self._ledger_cache
            except Exception as exc:
                if self.verbose:
                    sys.stderr.write(f"[diff-auditor] Failed to parse ledger: {exc}\n")

        if self.mock:
            self._ledger_cache = self._get_mock_ledger()
            return self._ledger_cache

        empty_ledger = {
            "schema_version": "1.0",
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "total_diffs": 0,
            "total_accrued": 0,
            "safe_count": 0,
            "high_risk_count": 0,
            "review_count": 0,
            "safe_diffs": [],
            "high_risk_diffs": [],
            "review_diffs": [],
            "entries": [],
            "status": "empty",
        }
        self._ledger_cache = empty_ledger
        return empty_ledger

    def list_entries(self) -> List[Dict[str, Any]]:
        """Return all accrued entries with risk classification."""
        ledger = self.load_ledger()
        return ledger.get("entries", [])

    def process_decisions(
        self,
        approve_safe: bool = False,
        approve_paths: Optional[List[str]] = None,
        reject_paths: Optional[List[str]] = None,
        approve_all: bool = False,
    ) -> Dict[str, Any]:
        """Apply operator review decisions and generate staged bake manifest."""
        ledger = self.load_ledger()
        entries = ledger.get("entries", [])

        approved_set: Set[str] = set()
        rejected_set: Set[str] = set()

        if reject_paths:
            for p in reject_paths:
                norm_p = p.replace("\\", "/").lstrip("./").lstrip("/")
                rejected_set.add(norm_p)

        if approve_all:
            for e in entries:
                p = e["path"]
                if p not in rejected_set:
                    approved_set.add(p)
        else:
            if approve_safe:
                for e in entries:
                    if e.get("risk") == "safe" and e["path"] not in rejected_set:
                        approved_set.add(e["path"])

            if approve_paths:
                for p in approve_paths:
                    norm_p = p.replace("\\", "/").lstrip("./").lstrip("/")
                    if norm_p not in rejected_set:
                        approved_set.add(norm_p)

        approved_diffs: List[Dict[str, Any]] = []
        rejected_diffs: List[Dict[str, Any]] = []
        pending_diffs: List[Dict[str, Any]] = []

        for e in entries:
            p = e["path"]
            item = dict(e)
            if p in approved_set:
                item["audit_state"] = "approved"
                approved_diffs.append(item)
            elif p in rejected_set:
                item["audit_state"] = "rejected"
                rejected_diffs.append(item)
            else:
                item["audit_state"] = "pending"
                pending_diffs.append(item)

        manifest = {
            "schema_version": "1.0",
            "staged_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "staged_by": "operator",
            "source_ledger": self.ledger_path,
            "total_accrued": len(entries),
            "total_approved": len(approved_diffs),
            "total_rejected": len(rejected_diffs),
            "total_pending": len(pending_diffs),
            "bake_ready": len(approved_diffs) > 0,
            "approved_diffs": approved_diffs,
            "rejected_diffs": rejected_diffs,
            "pending_diffs": pending_diffs,
            "status": "staged" if approved_diffs else "no_changes_staged",
        }

        if not self.dry_run and approved_diffs:
            atomic_write_json(self.staged_out, manifest)
            manifest["staged_manifest_file"] = self.staged_out

        return manifest

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="MiOS WS-DIFFCYCLE (task_412) Human-In-The-Loop Interactive Diff Auditor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="List all accrued diffs with risk classification and status",
    )
    parser.add_argument(
        "--approve-safe",
        action="store_true",
        help="Batch approve all safe-tier modifications (dotfiles, skills, network connections)",
    )
    parser.add_argument(
        "--approve",
        nargs="+",
        dest="approve_paths",
        help="Approve specific file paths for image bake staging",
    )
    parser.add_argument(
        "--reject",
        nargs="+",
        dest="reject_paths",
        help="Reject/quarantine specific file paths from image bake staging",
    )
    parser.add_argument(
        "--approve-all",
        action="store_true",
        help="Approve all accrued modifications regardless of risk tier",
    )
    parser.add_argument(
        "--stage",
        action="store_true",
        help="Stage approved diffs into staged bake manifest",
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Run interactive terminal auditing session",
    )
    parser.add_argument(
        "--ledger",
        "--input",
        dest="ledger_path",
        default=DEFAULT_LEDGER_PATH,
        help=f"Path to accrued diffs ledger (default: {DEFAULT_LEDGER_PATH})",
    )
    parser.add_argument(
        "--staged-out",
        "--out",
        dest="staged_out",
        default=DEFAULT_STAGED_PATH,
        help=f"Path to output staged bake manifest (default: {DEFAULT_STAGED_PATH})",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run in deterministic mock mode without requiring live host state",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Process review decisions without writing staged manifest to disk",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON envelope",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose diagnostic logs",
    )

    args = parser.parse_args(argv)

    engine = DiffAuditorEngine(
        ledger_path=args.ledger_path,
        staged_out=args.staged_out,
        mock=args.mock,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    try:
        if args.list:
            entries = engine.list_entries()
            if args.json:
                print(json.dumps({"status": "ok", "total": len(entries), "entries": entries}, indent=2))
            else:
                print(f"[diff-auditor] Accrued Modifications ({len(entries)} items):")
                for e in entries:
                    risk_tag = f"[{e.get('risk', 'review').upper()}]"
                    print(f"  {risk_tag:<12} {e.get('status', ' ')}  {e.get('path')} ({e.get('size_bytes', 0)} bytes)")
            return 0

        # Review / Staging execution
        should_process = (
            args.approve_safe
            or args.approve_paths
            or args.reject_paths
            or args.approve_all
            or args.stage
            or args.mock
        )

        if should_process:
            manifest = engine.process_decisions(
                approve_safe=args.approve_safe,
                approve_paths=args.approve_paths,
                reject_paths=args.reject_paths,
                approve_all=args.approve_all,
            )
            if args.json:
                print(json.dumps({"status": "ok", "staged_manifest": manifest}, indent=2))
            else:
                print(
                    f"[diff-auditor] Staged {manifest['total_approved']} approved diffs "
                    f"({manifest['total_rejected']} rejected, {manifest['total_pending']} pending)"
                )
                if "staged_manifest_file" in manifest:
                    print(f"[diff-auditor] Manifest written to: {manifest['staged_manifest_file']}")
            return 0

        # Default fallback: list status
        entries = engine.list_entries()
        if args.json:
            print(json.dumps({"status": "ok", "total": len(entries), "entries": entries}, indent=2))
        else:
            print(f"[diff-auditor] Accrued Modifications ({len(entries)} items):")
            for e in entries:
                risk_tag = f"[{e.get('risk', 'review').upper()}]"
                print(f"  {risk_tag:<12} {e.get('status', ' ')}  {e.get('path')} ({e.get('size_bytes', 0)} bytes)")
        return 0

    except Exception as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
        else:
            sys.stderr.write(f"[diff-auditor] Error: {exc}\n")
        return 1

if __name__ == "__main__":
    sys.exit(main())
