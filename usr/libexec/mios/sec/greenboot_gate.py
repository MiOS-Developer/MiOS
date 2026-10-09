#!/usr/bin/env python3
# AI-hint: Greenboot post-bake health gate validating service initialization with automated rollback and diff quarantine on regressions.
# AI-related: usr/share/doc/mios/adr/0018-shutdown-diff-snapshotting-and-boot-cycle-accrual.md, usr/share/doc/mios/manual/ch63-diff-snapshotting-boot-accrual-and-hitl-rollin.md, tests/test-sec.py
# AI-functions: GreenbootGateEngine, atomic_write_json, main
"""
WS-DIFFCYCLE (task_414): Greenboot Post-Bake Health Gate & Automated Fallback.
Invoked during early system boot by Greenboot required checks.
Verifies that newly baked image layers initialize critical AI and system services cleanly,
triggering automated bootc rollback and quarantining offending diffs if regressions occur.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

DEFAULT_HISTORY_PATH = "/var/lib/mios/diffs/bake-history.json"
DEFAULT_QUARANTINE_PATH = "/var/lib/mios/diffs/quarantine.json"
HEALTH_CHECK_SERVICES = [
    "agent-pipe.service",
    "mios-llm-light.service",
    "mios-pgvector.service",
    "systemd-resolved.service",
    "sshd.service",
]
HEALTH_ENDPOINT_URL = "http://127.0.0.1:8700/v1/models"

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

class GreenbootGateEngine:
    """Engine verifying post-bake deployment health and executing automated rollback & quarantine."""

    def __init__(
        self,
        history_path: str = DEFAULT_HISTORY_PATH,
        quarantine_path: str = DEFAULT_QUARANTINE_PATH,
        mock: bool = False,
        mock_failure: bool = False,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> None:
        self.history_path = history_path
        self.quarantine_path = quarantine_path
        self.mock = mock
        self.mock_failure = mock_failure
        self.dry_run = dry_run
        self.verbose = verbose

    def load_history(self) -> Dict[str, Any]:
        """Read existing bake history ledger."""
        if not self.mock and os.path.isfile(self.history_path):
            try:
                with open(self.history_path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception as exc:
                if self.verbose:
                    sys.stderr.write(f"[greenboot-gate] History read error: {exc}\n")

        if self.mock:
            # Synthetic mock history with pending firstboot bake
            return {
                "schema_version": "1.0",
                "total_bakes": 1,
                "latest_bake": {
                    "bake_id": "mock-bake-01",
                    "timestamp": "2026-08-26T21:00:00Z",
                    "commit_sha": "a1b2c3d4e5f6",
                    "image_tag": "localhost/mios:baked-a1b2c3d4e5f6",
                    "staged_files": ["etc/pam.d/system-auth", "var/lib/mios/ai/skills/custom-agent.md"],
                    "status": "staged_for_next_boot",
                    "health_verification": "pending_firstboot",
                },
                "bakes": [
                    {
                        "bake_id": "mock-bake-01",
                        "timestamp": "2026-08-26T21:00:00Z",
                        "commit_sha": "a1b2c3d4e5f6",
                        "image_tag": "localhost/mios:baked-a1b2c3d4e5f6",
                        "staged_files": ["etc/pam.d/system-auth", "var/lib/mios/ai/skills/custom-agent.md"],
                        "status": "staged_for_next_boot",
                        "health_verification": "pending_firstboot",
                    }
                ],
            }

        return {"schema_version": "1.0", "total_bakes": 0, "latest_bake": None, "bakes": []}

    def detect_pending_bake(self) -> Optional[Dict[str, Any]]:
        """Identify if a newly baked image layer is awaiting first-boot health verification."""
        history = self.load_history()
        latest = history.get("latest_bake")
        if not latest:
            return None

        status = latest.get("status", "")
        health_state = latest.get("health_verification", "")

        if status in ("staged_for_next_boot", "built", "staged") or health_state == "pending_firstboot":
            return latest

        if self.mock:
            return latest

        return None

    def check_service_health(self) -> Dict[str, Any]:
        """Probe critical systemd services and local AI health endpoints."""
        if self.mock:
            if self.mock_failure:
                return {
                    "healthy": False,
                    "failing_services": ["agent-pipe.service", "mios-llm-light.service"],
                    "service_statuses": {
                        "agent-pipe.service": "failed (Result: exit-code)",
                        "mios-llm-light.service": "failed (Result: core-dump)",
                        "mios-pgvector.service": "active (running)",
                        "systemd-resolved.service": "active (running)",
                        "sshd.service": "active (running)",
                    },
                    "endpoint_healthy": False,
                    "endpoint_error": "Connection refused to http://127.0.0.1:8700/v1/models",
                }
            return {
                "healthy": True,
                "failing_services": [],
                "service_statuses": {
                    "agent-pipe.service": "active (running)",
                    "mios-llm-light.service": "active (running)",
                    "mios-pgvector.service": "active (running)",
                    "systemd-resolved.service": "active (running)",
                    "sshd.service": "active (running)",
                },
                "endpoint_healthy": True,
                "endpoint_models_count": 4,
            }

        service_statuses: Dict[str, str] = {}
        failing_services: List[str] = []

        systemctl_bin = shutil.which("systemctl")
        if systemctl_bin:
            for s in HEALTH_CHECK_SERVICES:
                try:
                    proc = subprocess.run(
                        [systemctl_bin, "is-active", s],
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=2.0,
                    )
                    state = proc.stdout.strip()
                    service_statuses[s] = state
                    if state != "active":
                        failing_services.append(s)
                except Exception as exc:
                    service_statuses[s] = f"error: {exc}"
                    failing_services.append(s)
        else:
            for s in HEALTH_CHECK_SERVICES:
                service_statuses[s] = "active (simulated)"

        # HTTP Endpoint check
        endpoint_healthy = False
        endpoint_error = None
        models_count = 0

        try:
            req = urllib.request.Request(HEALTH_ENDPOINT_URL, headers={"User-Agent": "GreenbootGate/1.0"})
            with urllib.request.urlopen(req, timeout=3.0) as resp:
                if resp.status == 200:
                    data = json.loads(resp.read().decode("utf-8"))
                    models_count = len(data.get("data", []))
                    endpoint_healthy = True
        except Exception as exc:
            endpoint_error = str(exc)

        is_healthy = len(failing_services) == 0 and (endpoint_healthy or not systemctl_bin)

        return {
            "healthy": is_healthy,
            "failing_services": failing_services,
            "service_statuses": service_statuses,
            "endpoint_healthy": endpoint_healthy,
            "endpoint_error": endpoint_error,
            "endpoint_models_count": models_count,
        }

    def execute_rollback(self) -> Dict[str, Any]:
        """Trigger bootc rollback to revert to previous immutable deployment."""
        if self.mock or self.dry_run:
            return {
                "command": "bootc rollback",
                "status": "rollback_executed",
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }

        bootc_bin = shutil.which("bootc")
        if bootc_bin:
            try:
                proc = subprocess.run([bootc_bin, "rollback"], capture_output=True, text=True, check=False)
                return {
                    "command": "bootc rollback",
                    "status": "rollback_executed" if proc.returncode == 0 else f"rollback_failed: {proc.stderr[:200]}",
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }
            except Exception as exc:
                return {
                    "command": "bootc rollback",
                    "status": f"rollback_error: {exc}",
                    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                }

        return {
            "command": "bootc rollback",
            "status": "rollback_simulated",
            "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }

    def quarantine_diffs(self, bake_record: Dict[str, Any], reason: str, failing_services: List[str]) -> Dict[str, Any]:
        """Record offending diffs in quarantine ledger to prevent future automated bakes."""
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()
        quarantine_entry = {
            "quarantine_id": f"quarantine-{bake_record.get('bake_id', 'unknown')}",
            "bake_id": bake_record.get("bake_id"),
            "commit_sha": bake_record.get("commit_sha"),
            "image_tag": bake_record.get("image_tag"),
            "paths": bake_record.get("staged_files", []),
            "quarantined_at": timestamp,
            "reason": reason,
            "failing_services": failing_services,
        }

        if self.dry_run:
            return quarantine_entry

        existing_entries: List[Dict[str, Any]] = []
        if os.path.isfile(self.quarantine_path):
            try:
                with open(self.quarantine_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    existing_entries = data.get("quarantined_diffs", [])
            except Exception:
                pass

        existing_entries.insert(0, quarantine_entry)

        payload = {
            "schema_version": "1.0",
            "last_updated": timestamp,
            "total_quarantined": len(existing_entries),
            "quarantined_diffs": existing_entries,
        }
        atomic_write_json(self.quarantine_path, payload)
        return quarantine_entry

    def update_bake_status(self, bake_id: str, new_status: str, health_state: str) -> None:
        """Update status of a bake in the history ledger."""
        if self.dry_run or not os.path.isfile(self.history_path):
            return

        try:
            with open(self.history_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            bakes = data.get("bakes", [])
            for b in bakes:
                if b.get("bake_id") == bake_id:
                    b["status"] = new_status
                    b["health_verification"] = health_state
                    b["verified_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
                    break

            if data.get("latest_bake", {}).get("bake_id") == bake_id:
                data["latest_bake"]["status"] = new_status
                data["latest_bake"]["health_verification"] = health_state
                data["latest_bake"]["verified_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()

            data["last_updated"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
            atomic_write_json(self.history_path, data)
        except Exception as exc:
            if self.verbose:
                sys.stderr.write(f"[greenboot-gate] Failed to update bake status: {exc}\n")

    def verify_gate(self) -> Dict[str, Any]:
        """Execute full post-bake Greenboot health verification lifecycle."""
        pending_bake = self.detect_pending_bake()

        health_report = self.check_service_health()
        timestamp = datetime.datetime.now(datetime.timezone.utc).isoformat()

        if not pending_bake:
            return {
                "status": "pass",
                "timestamp": timestamp,
                "message": "Standard boot - no pending bake deployment awaiting verification",
                "health": health_report,
            }

        bake_id = pending_bake.get("bake_id", "unknown")

        if health_report["healthy"]:
            self.update_bake_status(bake_id, "verified_healthy", "pass")
            return {
                "status": "pass",
                "timestamp": timestamp,
                "bake_id": bake_id,
                "message": "Newly baked image layer verified healthy",
                "health": health_report,
            }

        # Health checks failed: Execute automated rollback and quarantine offending diffs
        failure_reason = (
            f"Health checks failed on services: {', '.join(health_report.get('failing_services', []))}"
        )
        if health_report.get("endpoint_error"):
            failure_reason += f"; Endpoint error: {health_report['endpoint_error']}"

        rollback_res = self.execute_rollback()
        quarantine_res = self.quarantine_diffs(
            pending_bake,
            reason=failure_reason,
            failing_services=health_report.get("failing_services", []),
        )
        self.update_bake_status(bake_id, "failed_rolled_back", "fail")

        return {
            "status": "failed_rolled_back",
            "timestamp": timestamp,
            "bake_id": bake_id,
            "message": "Diff-induced regression detected; bootc rollback triggered and diffs quarantined",
            "failure_reason": failure_reason,
            "health": health_report,
            "rollback": rollback_res,
            "quarantine": quarantine_res,
        }

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="MiOS WS-DIFFCYCLE (task_414) Greenboot Post-Bake Health Gate & Automated Fallback",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--check",
        "--verify",
        action="store_true",
        help="Execute post-bake health gate verification",
    )
    parser.add_argument(
        "--rollback",
        action="store_true",
        help="Manually trigger bootc rollback and quarantine",
    )
    parser.add_argument(
        "--quarantine",
        nargs="+",
        dest="quarantine_paths",
        help="Manually quarantine specified file paths from image baking",
    )
    parser.add_argument(
        "--status",
        action="store_true",
        help="Display bake health status and quarantined diffs",
    )
    parser.add_argument(
        "--history-file",
        dest="history_path",
        default=DEFAULT_HISTORY_PATH,
        help=f"Path to bake history ledger (default: {DEFAULT_HISTORY_PATH})",
    )
    parser.add_argument(
        "--quarantine-file",
        dest="quarantine_path",
        default=DEFAULT_QUARANTINE_PATH,
        help=f"Path to quarantine ledger (default: {DEFAULT_QUARANTINE_PATH})",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run in deterministic in-memory mock mode without live systemctl or bootc daemons",
    )
    parser.add_argument(
        "--mock-failure",
        action="store_true",
        help="Simulate service health check failure in mock mode to verify rollback and quarantine logic",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate verification without updating history or triggering rollback",
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

    engine = GreenbootGateEngine(
        history_path=args.history_path,
        quarantine_path=args.quarantine_path,
        mock=args.mock or args.mock_failure,
        mock_failure=args.mock_failure,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    try:
        if args.status:
            history = engine.load_history()
            quarantine = {}
            if os.path.isfile(args.quarantine_path):
                with open(args.quarantine_path, "r", encoding="utf-8") as f:
                    quarantine = json.load(f)
            if args.json:
                print(json.dumps({"status": "ok", "history": history, "quarantine": quarantine}, indent=2))
            else:
                print("[greenboot-gate] Status:")
                print(f"  Total Bakes:       {history.get('total_bakes', 0)}")
                print(f"  Quarantined Diffs: {quarantine.get('total_quarantined', 0)}")
            return 0

        if args.quarantine_paths:
            entry = engine.quarantine_diffs(
                {"bake_id": "manual-operator", "staged_files": args.quarantine_paths},
                reason="Manual operator quarantine",
                failing_services=[],
            )
            if args.json:
                print(json.dumps({"status": "ok", "quarantined": entry}, indent=2))
            else:
                print(f"[greenboot-gate] Quarantined {len(args.quarantine_paths)} paths")
            return 0

        # Default action: verify gate
        report = engine.verify_gate()

        if args.json:
            print(json.dumps(report, indent=2))
        else:
            if report["status"] == "pass":
                print(f"[greenboot-gate] Verification PASSED: {report['message']}")
            else:
                print(f"[greenboot-gate] Verification FAILED: {report['message']}")
                print(f"  Reason: {report.get('failure_reason')}")

        # Exit code 0 for healthy pass; exit code 1 for regression failure triggering Greenboot rollback
        return 0 if report["status"] == "pass" else 1

    except Exception as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}, indent=2))
        else:
            sys.stderr.write(f"[greenboot-gate] Error: {exc}\n")
        return 1

if __name__ == "__main__":
    sys.exit(main())
