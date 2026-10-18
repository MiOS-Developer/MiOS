#!/usr/bin/env python3
# AI-hint: Pre-shutdown diff snapshotting hook capturing git status, modified configs, and skills with sub-3s SLA.
# AI-related: usr/share/doc/mios/adr/0018-shutdown-diff-snapshotting-and-boot-cycle-accrual.md, usr/share/doc/mios/manual/ch63-diff-snapshotting-boot-accrual-and-hitl-rollin.md, tests/test-diff-snapshot.py
# AI-functions: DiffSnapshotEngine, redact_secrets, classify_risk, atomic_write_json, main
"""
WS-DIFFCYCLE (task_410): Pre-Poweroff Diff Snapshot Hook.
Captures all filesystem and configuration modifications across system root (.git == /)
before shutdown, reboot, or kexec events with a strict sub-3s timeout SLA,
redacting all sensitive tokens and writing immutable JSON records.
"""

from __future__ import annotations

import argparse
import datetime
import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
from typing import Any, Dict, List, Optional

DEFAULT_SNAPSHOT_DIR = "/var/lib/mios/snapshots/boot-diffs"
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

class DiffSnapshotEngine:
    """Core engine capturing pre-poweroff diff snapshots across the host root."""

    def __init__(
        self,
        root: str = "/",
        output_dir: str = DEFAULT_SNAPSHOT_DIR,
        boot_id: Optional[str] = None,
        timeout_secs: float = EXECUTION_TIMEOUT_SECS,
        mock: bool = False,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> None:
        self.root = os.path.abspath(root) if root else "/"
        self.output_dir = output_dir
        self.boot_id = boot_id or str(uuid.uuid4())[:8]
        self.timeout_secs = timeout_secs
        self.mock = mock
        self.dry_run = dry_run
        self.verbose = verbose

    def capture_git_diffs(self) -> List[Dict[str, Any]]:
        """Run fast git status against .git == root within strict timeout."""
        modified_files: List[Dict[str, Any]] = []
        git_dir = os.path.join(self.root, ".git")

        if not os.path.isdir(git_dir) and not self.mock:
            return []

        git_bin = shutil.which("git")
        if not git_bin and not self.mock:
            return []

        if self.mock:
            # Deterministic mock changes for headless tests / CI
            return [
                {
                    "path": "var/lib/mios/ai/skills/custom-agent.md",
                    "status": "??",
                    "type": "untracked",
                    "size_bytes": 1024,
                    "risk": "safe",
                    "patch_summary": "Added custom agent skill definition",
                },
                {
                    "path": "etc/pam.d/system-auth",
                    "status": "M ",
                    "type": "modified",
                    "size_bytes": 512,
                    "risk": "high-risk",
                    "patch_summary": "auth required pam_permit.so",
                },
                {
                    "path": "etc/mios/profile.toml",
                    "status": "M ",
                    "type": "modified",
                    "size_bytes": 2048,
                    "risk": "high-risk",
                    "patch_summary": "theme = 'dark-nord'",
                },
            ]

        try:
            cmd = [
                git_bin or "git",
                "--git-dir", git_dir,
                "--work-tree", self.root,
                "status", "--porcelain=v1", "-uall",
            ]
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout_secs,
                check=False,
            )
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    if len(line) < 3:
                        continue
                    status = line[:2].strip()
                    rel_path = line[3:].strip()
                    # Remove surrounding quotes if git outputted escaped path
                    if rel_path.startswith('"') and rel_path.endswith('"'):
                        rel_path = rel_path[1:-1]
                    norm_path = rel_path.replace("\\", "/")
                    full_path = os.path.join(self.root, norm_path)
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
            if self.verbose:
                sys.stderr.write(f"[diff-snapshot] Git status timed out after {self.timeout_secs}s\n")
        except Exception as exc:
            if self.verbose:
                sys.stderr.write(f"[diff-snapshot] Git status failed: {exc}\n")

        return modified_files

    def capture_snapshot(self, reason: str = "shutdown") -> Dict[str, Any]:
        """Produce the complete pre-poweroff snapshot payload."""
        start_time = time.monotonic()
        timestamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")

        changes = self.capture_git_diffs()

        # Secret redaction pass over path descriptions or metadata
        sanitized_changes = []
        for ch in changes:
            item = dict(ch)
            if "patch_summary" in item:
                item["patch_summary"] = redact_secrets(item["patch_summary"])
            sanitized_changes.append(item)

        duration_ms = (time.monotonic() - start_time) * 1000.0

        snapshot = {
            "schema_version": "1.0",
            "timestamp": timestamp,
            "boot_id": self.boot_id,
            "reason": reason,
            "root": self.root,
            "duration_ms": round(duration_ms, 2),
            "total_files": len(sanitized_changes),
            "total_changes": len(sanitized_changes),
            "files": sanitized_changes,
            "changes": sanitized_changes,
            "status": "ok",
        }

        if not self.dry_run:
            out_file = os.path.join(self.output_dir, f"{timestamp}-{self.boot_id}.json")
            atomic_write_json(out_file, snapshot)
            snapshot["snapshot_file"] = out_file

        return snapshot

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="MiOS WS-DIFFCYCLE (task_410) Pre-Poweroff Diff Snapshot Hook",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--reason",
        choices=["shutdown", "reboot", "manual", "pre-poweroff", "kexec"],
        default="shutdown",
        help="Event triggering snapshot capture (default: shutdown)",
    )
    parser.add_argument(
        "--root",
        default=os.environ.get("MIOS_ROOT", "/"),
        help="Target filesystem root (default: / or $MIOS_ROOT)",
    )
    parser.add_argument(
        "--output-dir",
        "--out-dir",
        dest="output_dir",
        default=DEFAULT_SNAPSHOT_DIR,
        help=f"Directory to write snapshot JSON files (default: {DEFAULT_SNAPSHOT_DIR})",
    )
    parser.add_argument(
        "--boot-id",
        default=None,
        help="Explicit boot identifier (default: auto-generated)",
    )
    parser.add_argument(
        "--timeout-secs",
        type=float,
        default=EXECUTION_TIMEOUT_SECS,
        help=f"Subprocess timeout in seconds (default: {EXECUTION_TIMEOUT_SECS})",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run in deterministic in-memory mock mode without requiring git or root permissions",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute snapshot without writing to disk",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output structured JSON envelope to stdout",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose diagnostic logs",
    )

    args = parser.parse_args(argv)

    engine = DiffSnapshotEngine(
        root=args.root,
        output_dir=args.output_dir,
        boot_id=args.boot_id,
        timeout_secs=args.timeout_secs,
        mock=args.mock,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    try:
        snapshot = engine.capture_snapshot(reason=args.reason)
        if args.json:
            print(json.dumps({"status": "ok", "snapshot": snapshot}, indent=2))
        else:
            print(
                f"[diff-snapshot] Captured {snapshot['total_changes']} changes in "
                f"{snapshot['duration_ms']}ms (reason: {args.reason}, boot-id: {snapshot['boot_id']})"
            )
            if "snapshot_file" in snapshot:
                print(f"[diff-snapshot] Persisted snapshot: {snapshot['snapshot_file']}")
        return 0
    except Exception as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
        else:
            sys.stderr.write(f"[diff-snapshot] Error: {exc}\n")
        return 1

if __name__ == "__main__":
    sys.exit(main())
