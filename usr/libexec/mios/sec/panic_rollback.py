#!/usr/bin/env python3
# AI-hint: Linux pstore kernel panic scanner, boot failure tracker, and emergency bootc rollback engine.
# AI-related: tests/test-sec.py, usr/share/doc/mios/manual/sec.md
"""
MiOS Kernel Panic Monitor and Emergency bootc Rollback Engine.
Scans pstore ramoops logs for early-boot kernel crashes, maintains persistent failure counters,
and triggers automated bootc rollback if consecutive failures exceed threshold.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

class PanicRollbackHandler:
    """Handles pstore panic detection, failure tracking, and bootc deployment rollback."""

    DEFAULT_STATE_FILE = "/var/lib/mios/boot_fails.json"
    DEFAULT_PSTORE_DIR = "/sys/fs/pstore"

    def __init__(self, mock: bool = False, dry_run: bool = False, mock_failures: int = 0) -> None:
        self.mock = mock
        self.dry_run = dry_run
        self.mock_failures = mock_failures
        self._in_memory_state = {"failure_count": mock_failures, "history": []}

    def scan_pstore(
        self,
        pstore_dir: str = "/sys/fs/pstore",
        mock_files: Optional[Dict[str, str]] = None,
    ) -> List[Dict[str, Any]]:
        """Scans pstore filesystem for kernel panic, Oops, or ramoops dumps."""
        panics: List[Dict[str, Any]] = []

        if self.mock:
            if mock_files:
                for fname, content in mock_files.items():
                    if "Kernel panic" in content or "Oops" in content or "BUG:" in content:
                        panics.append({
                            "filename": fname,
                            "reason": "Kernel panic detected in ramoops log",
                            "snippet": content[:200],
                            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                        })
            elif self.mock_failures > 0:
                panics.append({
                    "filename": "dmesg-ramoops-0",
                    "reason": "Mock kernel panic - not syncing: Fatal exception in interrupt",
                    "snippet": "[ 0.123456] Kernel panic - not syncing: VFS: Unable to mount root fs",
                    "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                })
            return panics

        if not os.path.exists(pstore_dir):
            return []

        for fname in os.listdir(pstore_dir):
            if fname.startswith(("dmesg-", "console-", "pmsg-")):
                fpath = os.path.join(pstore_dir, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="replace") as f:
                        text = f.read()
                        if "Kernel panic" in text or "Oops" in text or "BUG:" in text:
                            match = re.search(r"(Kernel panic[^\n]+|Oops:[^\n]+)", text)
                            reason = match.group(1) if match else "Kernel panic detected"
                            panics.append({
                                "filename": fname,
                                "reason": reason,
                                "snippet": text[:300],
                                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(os.path.getmtime(fpath))),
                            })
                except Exception:
                    pass

        return panics

    def read_state(
        self,
        state_file: str = "/var/lib/mios/boot_fails.json",
    ) -> Dict[str, Any]:
        """Reads persistent boot failure state."""
        if self.mock and not os.path.exists(state_file):
            return self._in_memory_state

        if os.path.exists(state_file):
            try:
                with open(state_file, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {"failure_count": 0, "history": []}

    def record_failure(
        self,
        reason: str = "panic",
        state_file: str = "/var/lib/mios/boot_fails.json",
    ) -> int:
        """Increments persistent boot failure count and records incident timestamp."""
        state = self.read_state(state_file)
        count = state.get("failure_count", 0) + 1
        history = state.get("history", [])

        history.append({
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "reason": reason,
        })
        new_state = {"failure_count": count, "history": history[-10:]}

        if self.mock or self.dry_run:
            self._in_memory_state = new_state
            if not self.dry_run:
                try:
                    os.makedirs(os.path.dirname(state_file) or ".", exist_ok=True)
                    with open(state_file, "w", encoding="utf-8") as f:
                        json.dump(new_state, f, indent=2)
                except Exception:
                    pass
            return count

        os.makedirs(os.path.dirname(state_file), exist_ok=True)
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump(new_state, f, indent=2)

        return count

    def reset_counter(
        self,
        state_file: str = "/var/lib/mios/boot_fails.json",
    ) -> bool:
        """Resets boot failure counter to zero upon successful greenboot check."""
        clean_state = {"failure_count": 0, "history": []}
        if self.mock or self.dry_run:
            self._in_memory_state = clean_state
            if not self.dry_run:
                try:
                    with open(state_file, "w", encoding="utf-8") as f:
                        json.dump(clean_state, f, indent=2)
                except Exception:
                    pass
            return True

        if os.path.exists(state_file):
            try:
                with open(state_file, "w", encoding="utf-8") as f:
                    json.dump(clean_state, f, indent=2)
            except Exception:
                return False
        return True

    def execute_bootc_rollback(self) -> bool:
        """Executes emergency bootc rollback."""
        if self.mock or self.dry_run:
            return True

        bootc_bin = shutil.which("bootc")
        if bootc_bin:
            proc = subprocess.run([bootc_bin, "rollback"], capture_output=True, text=True)
            return proc.returncode == 0
        return False

    def evaluate_rollback(
        self,
        max_failures: int = 3,
        state_file: str = "/var/lib/mios/boot_fails.json",
        pstore_dir: str = "/sys/fs/pstore",
    ) -> Dict[str, Any]:
        """Evaluates whether to trigger emergency bootc rollback."""
        panics = self.scan_pstore(pstore_dir=pstore_dir)
        state = self.read_state(state_file)
        current_fails = state.get("failure_count", 0)

        # If panic found in pstore, record failure
        if panics and current_fails < len(panics):
            current_fails = self.record_failure(reason=panics[0]["reason"], state_file=state_file)

        rollback_triggered = current_fails >= max_failures
        rollback_success = False

        if rollback_triggered:
            rollback_success = self.execute_bootc_rollback()

        status_str = "rollback_triggered" if rollback_triggered else "healthy"

        return {
            "status": status_str,
            "failure_count": current_fails,
            "max_failures": max_failures,
            "panic_traces_found": len(panics),
            "panics": panics,
            "action": "bootc_rollback" if rollback_triggered else "none",
            "rollback_executed": rollback_success,
            "mock": self.mock,
        }

def main() -> int:
    parser = argparse.ArgumentParser(description="MiOS Panic Monitor & Emergency bootc Rollback Engine")
    parser.add_argument("--check-panic", action="store_true", help="Scan pstore for kernel crash logs")
    parser.add_argument("--record-failure", action="store_true", help="Record a boot failure incident")
    parser.add_argument("--reset-counter", action="store_true", help="Reset failure counter to 0 on successful boot")
    parser.add_argument("--evaluate-rollback", action="store_true", help="Evaluate failure count and trigger rollback if needed")
    parser.add_argument("--max-failures", type=int, default=3, help="Maximum allowed consecutive failures (default: 3)")
    parser.add_argument("--pstore-dir", default="/sys/fs/pstore", help="Path to pstore mount directory")
    parser.add_argument("--state-file", default="/var/lib/mios/boot_fails.json", help="Path to boot failure state JSON")
    parser.add_argument("--mock", action="store_true", help="Run with deterministic mock engine")
    parser.add_argument("--mock-failures", type=int, default=0, help="Initial mock failure count")
    parser.add_argument("--dry-run", action="store_true", help="Dry-run simulation mode")
    parser.add_argument("--json", action="store_true", help="Format output as JSON dictionary")

    args = parser.parse_args()
    handler = PanicRollbackHandler(mock=args.mock, dry_run=args.dry_run, mock_failures=args.mock_failures)
    result: Dict[str, Any] = {"status": "ok", "mock": args.mock}

    try:
        if args.record_failure:
            count = handler.record_failure(reason="manual_or_probe", state_file=args.state_file)
            result.update({"action": "record_failure", "new_failure_count": count})

        elif args.reset_counter:
            reset_ok = handler.reset_counter(state_file=args.state_file)
            result.update({"action": "reset_counter", "reset": reset_ok, "failure_count": 0})

        elif args.check_panic:
            panics = handler.scan_pstore(pstore_dir=args.pstore_dir)
            result.update({"action": "check_panic", "panic_traces_found": len(panics), "panics": panics})

        else:
            eval_res = handler.evaluate_rollback(
                max_failures=args.max_failures,
                state_file=args.state_file,
                pstore_dir=args.pstore_dir,
            )
            result.update(eval_res)

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"[+] Panic Rollback Engine: status={result.get('status')}")
            for k, v in result.items():
                print(f"    {k}: {v}")

        return 0

    except Exception as exc:
        err = {"status": "error", "error": str(exc), "mock": args.mock}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"[-] Error: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
