#!/usr/bin/env python3
# AI-hint: Boot cycle diff accrual analyzer classifying safe vs high-risk modifications and emitting audit ledgers.
# AI-related: usr/share/doc/mios/adr/0018-shutdown-diff-snapshotting-and-boot-cycle-accrual.md, usr/share/doc/mios/manual/ch63-diff-snapshotting-boot-accrual-and-hitl-rollin.md, tests/test-diff-accrual.py
# AI-functions: DiffAccrualEngine, classify_risk, classify_path, redact_secrets, atomic_write_json, snapshot_diffs, accrue_diffs, main
"""
WS-DIFFCYCLE (task_411): Boot Cycle Diff Accrual & Risk Classifier.
Ingests historical diff snapshots across power cycles, deduplicates entries,
classifies file mutations into Safe vs High-Risk vs Review tiers,
and atomically exports structured audit ledgers for operator inspection and image baking.
"""

from __future__ import annotations

import argparse
import datetime
import fnmatch
import json
import os
import re
import subprocess
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

DEFAULT_DIFF_DIR = "/var/lib/mios/diffs"
DEFAULT_SNAPSHOT_DIR = "/var/lib/mios/snapshots/boot-diffs"
DEFAULT_LEDGER_PATH = "/var/run/mios/accrued-diffs.json"
EXECUTION_TIMEOUT_SECS = 3.0

SAFE_PATTERNS = [
    ".config/*",
    "config/*",
    "etc/skel/*",
    "var/lib/mios/ai/skills/*",
    "etc/NetworkManager/system-connections/*",
    "usr/share/doc/*",
    "usr/share/mios/themes/*",
    "*.md",
]

HIGH_RISK_PATTERNS = [
    "etc/pam.d/*",
    "etc/shadow*",
    "etc/sudoers*",
    "usr/bin/*",
    "usr/sbin/*",
    "usr/lib/systemd/*",
    "etc/systemd/*",
    "etc/kargs.d/*",
    "etc/kernel/*",
    "etc/nftables/*",
    "usr/share/mios/security/egress.nft",
    "etc/containers/*",
    "etc/crio/*",
]

KV_SECRET_PATTERN = re.compile(
    r"(?i)(api[_-]?key|secret|password|token|bearer|passphrase|access[_-]?key)(\s*[:=]\s*)['\"]?([^\s'\"]+)['\"]?"
)
PK_SECRET_PATTERN = re.compile(
    r"-----BEGIN\s+[A-Z\s]+PRIVATE\s+KEY-----[\s\S]*?-----END\s+[A-Z\s]+PRIVATE\s+KEY-----|-----BEGIN\s+[A-Z\s]+PRIVATE\s+KEY-----"
)

def redact_secrets(content: str) -> str:
    """Sanitize secret values to comply with SECRETS-NEVER-IN-ENV and persistence hygiene."""
    if not content:
        return ""
    sanitized = KV_SECRET_PATTERN.sub(r"\1\2[REDACTED]", content)
    sanitized = PK_SECRET_PATTERN.sub(r"[REDACTED_PRIVATE_KEY]", sanitized)
    return sanitized

def classify_risk(file_path: str) -> str:
    """Classify file mutation into 'safe', 'high-risk', or 'review' tier."""
    norm_path = file_path.replace("\\", "/")
    while norm_path.startswith("./"):
        norm_path = norm_path[2:]
    norm_path = norm_path.lstrip("/")

    for pat in HIGH_RISK_PATTERNS:
        if fnmatch.fnmatch(norm_path, pat):
            return "high-risk"
    for pat in SAFE_PATTERNS:
        if fnmatch.fnmatch(norm_path, pat):
            return "safe"
    if norm_path.startswith("etc/"):
        return "high-risk"
    return "review"

def classify_path(file_path: str) -> str:
    """Alias for classify_risk for interface compatibility."""
    risk = classify_risk(file_path)
    return risk if risk in ("safe", "high-risk") else "safe"

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

def snapshot_diffs(
    root_dir: str,
    output_dir: str = DEFAULT_SNAPSHOT_DIR,
    boot_id: Optional[str] = None,
    timeout_secs: float = EXECUTION_TIMEOUT_SECS,
) -> Dict[str, Any]:
    """Capture modified and untracked files into an atomic snapshot (compatibility helper)."""
    start_time = time.monotonic()
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not boot_id:
        boot_id = str(uuid.uuid4())[:8]

    modified_files: List[Dict[str, Any]] = []

    git_dir = os.path.join(root_dir, ".git")
    if os.path.isdir(git_dir):
        try:
            proc = subprocess.run(
                ["git", "--git-dir", git_dir, "--work-tree", root_dir, "status", "--porcelain=v1", "-uall"],
                capture_output=True,
                text=True,
                timeout=timeout_secs,
                check=False,
            )
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    if len(line) < 3:
                        continue
                    status = line[:2].strip()
                    rel_path = line[3:].strip()
                    if rel_path.startswith('"') and rel_path.endswith('"'):
                        rel_path = rel_path[1:-1]
                    norm_path = rel_path.replace("\\", "/")
                    full_path = os.path.join(root_dir, norm_path)
                    size = os.path.getsize(full_path) if os.path.isfile(full_path) else 0
                    diff_type = (
                        "untracked"
                        if status == "??"
                        else "modified"
                        if "M" in status
                        else "deleted"
                        if "D" in status
                        else "added"
                    )
                    risk = classify_risk(norm_path)
                    modified_files.append({
                        "path": norm_path,
                        "status": status,
                        "type": diff_type,
                        "size_bytes": size,
                        "risk": risk,
                    })
        except subprocess.TimeoutExpired:
            sys.stderr.write(f"[diff-accrual] Snapshot timed out after {timeout_secs}s\n")
        except Exception as e:
            sys.stderr.write(f"[diff-accrual] Git status failed: {e}\n")

    duration_ms = (time.monotonic() - start_time) * 1000.0

    snapshot = {
        "schema_version": "1.0",
        "timestamp": timestamp,
        "boot_id": boot_id,
        "root": root_dir,
        "duration_ms": round(duration_ms, 2),
        "total_files": len(modified_files),
        "total_changes": len(modified_files),
        "files": modified_files,
        "changes": modified_files,
        "status": "ok",
    }

    out_file = os.path.join(output_dir, f"{timestamp}-{boot_id}.json")
    atomic_write_json(out_file, snapshot)
    return snapshot

def accrue_diffs(
    snapshot_dir: str = DEFAULT_SNAPSHOT_DIR,
    ledger_path: str = DEFAULT_LEDGER_PATH,
    mock: bool = False,
    dry_run: bool = False,
) -> Dict[str, Any]:
    """Aggregate historical diff snapshots and generate risk-classified audit ledger."""
    engine = DiffAccrualEngine(
        snapshots_dir=snapshot_dir,
        ledger_path=ledger_path,
        mock=mock,
        dry_run=dry_run,
    )
    return engine.generate_ledger()

class DiffAccrualEngine:
    """Core engine parsing snapshots and generating classified audit ledgers."""

    def __init__(
        self,
        snapshots_dir: str = DEFAULT_SNAPSHOT_DIR,
        ledger_path: str = DEFAULT_LEDGER_PATH,
        mock: bool = False,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> None:
        self.snapshots_dir = snapshots_dir
        self.ledger_path = ledger_path
        self.mock = mock
        self.dry_run = dry_run
        self.verbose = verbose

    def _get_mock_snapshots(self) -> List[Dict[str, Any]]:
        """Return synthetic snapshots for headless mock testing."""
        return [
            {
                "schema_version": "1.0",
                "timestamp": "20260826T200000Z",
                "boot_id": "mockboot01",
                "changes": [
                    {
                        "path": "var/lib/mios/ai/skills/custom-agent.md",
                        "status": "??",
                        "type": "untracked",
                        "size_bytes": 1024,
                        "risk": "safe",
                    },
                    {
                        "path": "etc/skel/.bashrc",
                        "status": "M ",
                        "type": "modified",
                        "size_bytes": 350,
                        "risk": "safe",
                    },
                ],
            },
            {
                "schema_version": "1.0",
                "timestamp": "20260826T210000Z",
                "boot_id": "mockboot02",
                "changes": [
                    {
                        "path": "etc/pam.d/system-auth",
                        "status": "M ",
                        "type": "modified",
                        "size_bytes": 512,
                        "risk": "high-risk",
                    },
                    {
                        "path": "etc/mios/profile.toml",
                        "status": "M ",
                        "type": "modified",
                        "size_bytes": 2048,
                        "risk": "high-risk",
                    },
                ],
            },
        ]

    def ingest_snapshots(self) -> Dict[str, Dict[str, Any]]:
        """Read and deduplicate snapshots from the snapshot directory."""
        accrued: Dict[str, Dict[str, Any]] = {}

        if self.mock and (not os.path.isdir(self.snapshots_dir) or not os.listdir(self.snapshots_dir)):
            snapshots = self._get_mock_snapshots()
            for snap in snapshots:
                items = snap.get("changes") or snap.get("files") or []
                for item in items:
                    p = item["path"]
                    risk_val = item.get("risk", classify_risk(p))
                    accrued[p] = {
                        "path": p,
                        "status": item.get("status", "M"),
                        "type": item.get("type", "modified"),
                        "size_bytes": item.get("size_bytes", 0),
                        "risk": risk_val,
                        "last_observed": snap.get("timestamp"),
                        "boot_id": snap.get("boot_id"),
                    }
            return accrued

        if not os.path.isdir(self.snapshots_dir):
            return accrued

        for fn in sorted(os.listdir(self.snapshots_dir)):
            if not fn.endswith(".json"):
                continue
            fp = os.path.join(self.snapshots_dir, fn)
            try:
                with open(fp, "r", encoding="utf-8") as f:
                    snap = json.load(f)
                    items = snap.get("changes") or snap.get("files") or []
                    for item in items:
                        p = item["path"]
                        risk_val = item.get("risk", classify_risk(p))
                        accrued[p] = {
                            "path": p,
                            "status": item.get("status", "M"),
                            "type": item.get("type", "modified"),
                            "size_bytes": item.get("size_bytes", 0),
                            "risk": risk_val,
                            "last_observed": snap.get("timestamp"),
                            "boot_id": snap.get("boot_id"),
                        }
            except (json.JSONDecodeError, OSError) as exc:
                if self.verbose:
                    sys.stderr.write(f"[diff-accrual] Skipped invalid snapshot {fn}: {exc}\n")
                continue

        return accrued

    def generate_ledger(self) -> Dict[str, Any]:
        """Construct the complete accrued audit ledger and optionally persist it."""
        accrued = self.ingest_snapshots()
        entries = list(accrued.values())

        safe_diffs = [e for e in entries if e["risk"] == "safe"]
        high_risk_diffs = [e for e in entries if e["risk"] == "high-risk"]
        review_diffs = [e for e in entries if e["risk"] == "review"]

        ledger = {
            "schema_version": "1.0",
            "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "total_diffs": len(entries),
            "total_accrued": len(entries),
            "safe_count": len(safe_diffs),
            "high_risk_count": len(high_risk_diffs),
            "review_count": len(review_diffs),
            "safe_diffs": safe_diffs,
            "high_risk_diffs": high_risk_diffs,
            "review_diffs": review_diffs,
            "entries": sorted(entries, key=lambda x: (x["risk"], x["path"])),
            "status": "ready_for_review",
        }

        if not self.dry_run:
            atomic_write_json(self.ledger_path, ledger)
            ledger["ledger_path"] = self.ledger_path

        return ledger

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="MiOS WS-DIFFCYCLE (task_411) Boot Cycle Diff Accrual & Risk Classifier",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command")

    # Snapshot subparser (compatibility)
    snap_parser = subparsers.add_parser("snapshot", help="Capture pre-poweroff diff snapshot")
    snap_parser.add_argument("--root", default=os.environ.get("MIOS_ROOT", "/"))
    snap_parser.add_argument("--output-dir", "--snapshot-dir", dest="output_dir", default=DEFAULT_SNAPSHOT_DIR)
    snap_parser.add_argument("--boot-id", default=None)
    snap_parser.add_argument("--timeout-secs", type=float, default=EXECUTION_TIMEOUT_SECS)

    # Accrue subparser
    accrue_parser = subparsers.add_parser("accrue", help="Accrue historical diffs into audit ledger")
    accrue_parser.add_argument("--snapshots-dir", "--snapshot-dir", dest="snapshots_dir", default=DEFAULT_SNAPSHOT_DIR)
    accrue_parser.add_argument("--ledger-out", "--ledger", "--out", dest="ledger_out", default=DEFAULT_LEDGER_PATH)
    accrue_parser.add_argument("--mock", action="store_true", help="Run with mock snapshots in memory")
    accrue_parser.add_argument("--dry-run", action="store_true", help="Do not write ledger to disk")
    accrue_parser.add_argument("--json", action="store_true", help="Output JSON envelope")
    accrue_parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logs")

    # Classify subparser
    classify_parser = subparsers.add_parser("classify", help="Classify file path risk")
    classify_parser.add_argument("--path", required=True, help="File path to classify")
    classify_parser.add_argument("--json", action="store_true", help="Output JSON envelope")

    # Status subparser
    status_parser = subparsers.add_parser("status", help="Show summary of existing accrued ledger")
    status_parser.add_argument("--ledger", default=DEFAULT_LEDGER_PATH, help="Path to accrued ledger")
    status_parser.add_argument("--json", action="store_true", help="Output JSON envelope")

    # Also top-level flags for standalone calls without subcommands
    parser.add_argument("--snapshots-dir", "--snapshot-dir", dest="top_snapshots_dir", default=DEFAULT_SNAPSHOT_DIR)
    parser.add_argument("--ledger-out", "--ledger", "--out", dest="top_ledger_out", default=DEFAULT_LEDGER_PATH)
    parser.add_argument("--path", dest="top_path", default=None, help="Classify single path")
    parser.add_argument("--mock", action="store_true", help="Run with mock snapshots in memory")
    parser.add_argument("--dry-run", action="store_true", help="Do not write ledger to disk")
    parser.add_argument("--json", action="store_true", help="Output JSON envelope")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose logs")

    args = parser.parse_args(argv)

    if args.command == "snapshot":
        snap = snapshot_diffs(args.root, args.output_dir, args.boot_id, args.timeout_secs)
        print(f"[diff-accrual] Snapshot recorded: {snap['total_changes']} changes in {snap['duration_ms']}ms")
        return 0
    elif args.command == "accrue":
        engine = DiffAccrualEngine(
            snapshots_dir=args.snapshots_dir,
            ledger_path=args.ledger_out,
            mock=args.mock,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
        ledger = engine.generate_ledger()
        if args.json:
            print(json.dumps({"status": "ok", "ledger": ledger}, indent=2))
        else:
            print(
                f"[diff-accrual] Accrued ledger written: {ledger['total_diffs']} changes "
                f"(Safe: {ledger['safe_count']}, High-Risk: {ledger['high_risk_count']}, Review: {ledger.get('review_count', 0)})"
            )
        return 0
    elif args.command == "classify":
        risk = classify_risk(args.path)
        if args.json:
            print(json.dumps({"status": "ok", "path": args.path, "risk": risk}, indent=2))
        else:
            print(risk)
        return 0
    elif args.command == "status":
        if os.path.isfile(args.ledger):
            with open(args.ledger, "r", encoding="utf-8") as f:
                ledger = json.load(f)
            if args.json:
                print(json.dumps({"status": "ok", "ledger": ledger}, indent=2))
            else:
                print(f"[diff-accrual] Ledger: {args.ledger}")
                print(f"  Total diffs: {ledger.get('total_diffs', 0)}")
                print(f"  Safe diffs: {ledger.get('safe_count', 0)}")
                print(f"  High-risk diffs: {ledger.get('high_risk_count', 0)}")
                print(f"  Review diffs: {ledger.get('review_count', 0)}")
                print(f"  Status: {ledger.get('status', 'unknown')}")
        else:
            if args.json:
                print(json.dumps({"status": "error", "message": f"Ledger not found: {args.ledger}"}, indent=2))
            else:
                print(f"[diff-accrual] Ledger not found: {args.ledger}")
            return 1
        return 0
    else:
        # Default action when no subcommand is specified
        if args.top_path:
            risk = classify_risk(args.top_path)
            if args.json:
                print(json.dumps({"status": "ok", "path": args.top_path, "risk": risk}, indent=2))
            else:
                print(risk)
            return 0

        engine = DiffAccrualEngine(
            snapshots_dir=args.top_snapshots_dir,
            ledger_path=args.top_ledger_out,
            mock=args.mock,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
        ledger = engine.generate_ledger()
        if args.json:
            print(json.dumps({"status": "ok", "ledger": ledger}, indent=2))
        else:
            print(
                f"[diff-accrual] Accrued ledger written: {ledger['total_diffs']} changes "
                f"(Safe: {ledger['safe_count']}, High-Risk: {ledger['high_risk_count']}, Review: {ledger.get('review_count', 0)})"
            )
        return 0

if __name__ == "__main__":
    sys.exit(main())
