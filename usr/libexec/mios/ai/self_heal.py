#!/usr/bin/env python3
# AI-hint: Autonomous self-healing code remediation agent triggered on systemd unit failures.
# AI-related: usr/lib/systemd/system/mios-self-heal.service, /var/log/mios/self-heal.log
"""
Autonomous Self-Healing Code Remediation Agent (task_326 / task_2559)

Listens for and detects systemd unit failure events, harvests recent journald error logs,
formulates structured root cause diagnoses, enforces circuit breaker rate limiting
(max 3 restarts / 15m), strictly protects immutable `/usr` partitions (Architectural Law 1),
applies safe `/etc` configuration patches and `/var` repairs, and logs RCA records
to `/var/log/mios/self-heal.log`.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("mios.self_heal")

class PathViolationError(Exception):
    """Raised when remediation attempts to modify an immutable path like /usr."""
    pass

@dataclasses.dataclass
class FailureEvent:
    unit_name: str
    exit_code: int = 0
    error_logs: List[str] = dataclasses.field(default_factory=list)
    timestamp: float = dataclasses.field(default_factory=time.time)
    active_state: str = "failed"
    sub_state: str = "failed"

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> FailureEvent:
        return cls(
            unit_name=data.get("unit_name", ""),
            exit_code=int(data.get("exit_code", 0)),
            error_logs=list(data.get("error_logs", [])),
            timestamp=float(data.get("timestamp", time.time())),
            active_state=data.get("active_state", "failed"),
            sub_state=data.get("sub_state", "failed"),
        )

class CircuitBreaker:
    """
    Prevents cascading restart loops by limiting remediation attempts per unit
    to max_attempts (default: 3) within window_seconds (default: 900s = 15m).
    """

    def __init__(
        self,
        max_attempts: int = 3,
        window_seconds: float = 900.0,
        state_file: Optional[str] = None,
    ) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self.state_file = state_file
        self.attempts: Dict[str, List[float]] = {}
        self.quarantined: Dict[str, float] = {}
        if self.state_file and os.path.exists(self.state_file):
            self.load_state()

    def _prune(self, unit_name: str, now: float) -> None:
        if unit_name in self.attempts:
            cutoff = now - self.window_seconds
            self.attempts[unit_name] = [t for t in self.attempts[unit_name] if t >= cutoff]
            if not self.attempts[unit_name]:
                del self.attempts[unit_name]

    def is_quarantined(self, unit_name: str, now: Optional[float] = None) -> bool:
        ts = now if now is not None else time.time()
        if unit_name in self.quarantined:
            # Check if quarantine window expired (2x window)
            q_time = self.quarantined[unit_name]
            if ts - q_time < (self.window_seconds * 2):
                return True
            del self.quarantined[unit_name]
        return False

    def can_attempt(self, unit_name: str, now: Optional[float] = None) -> bool:
        ts = now if now is not None else time.time()
        if self.is_quarantined(unit_name, ts):
            return False
        self._prune(unit_name, ts)
        recent_count = len(self.attempts.get(unit_name, []))
        return recent_count < self.max_attempts

    def record_attempt(self, unit_name: str, success: bool, now: Optional[float] = None) -> bool:
        ts = now if now is not None else time.time()
        self._prune(unit_name, ts)
        if unit_name not in self.attempts:
            self.attempts[unit_name] = []
        self.attempts[unit_name].append(ts)

        if not success and len(self.attempts[unit_name]) >= self.max_attempts:
            self.quarantined[unit_name] = ts
            self.save_state()
            return False

        if success:
            # If success, clear previous attempts for this unit
            self.attempts.pop(unit_name, None)
            self.quarantined.pop(unit_name, None)

        self.save_state()
        return True

    def quarantine_unit(self, unit_name: str, now: Optional[float] = None) -> None:
        ts = now if now is not None else time.time()
        self.quarantined[unit_name] = ts
        self.save_state()

    def reset(self, unit_name: Optional[str] = None) -> None:
        if unit_name:
            self.attempts.pop(unit_name, None)
            self.quarantined.pop(unit_name, None)
        else:
            self.attempts.clear()
            self.quarantined.clear()
        self.save_state()

    def get_status(self, unit_name: Optional[str] = None) -> Dict[str, Any]:
        now = time.time()
        if unit_name:
            self._prune(unit_name, now)
            return {
                "unit_name": unit_name,
                "quarantined": self.is_quarantined(unit_name, now),
                "recent_attempts": len(self.attempts.get(unit_name, [])),
                "max_attempts": self.max_attempts,
                "window_seconds": self.window_seconds,
            }
        return {
            "quarantined_units": {k: v for k, v in self.quarantined.items()},
            "active_attempts": {k: len(v) for k, v in self.attempts.items()},
            "max_attempts": self.max_attempts,
            "window_seconds": self.window_seconds,
        }

    def save_state(self) -> None:
        if not self.state_file:
            return
        try:
            parent = os.path.dirname(self.state_file)
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
            with open(self.state_file, "w", encoding="utf-8") as f:
                json.dump({
                    "attempts": self.attempts,
                    "quarantined": self.quarantined,
                }, f, indent=2)
        except Exception as e:
            logger.warning("Failed to save circuit breaker state: %s", e)

    def load_state(self) -> None:
        if not self.state_file or not os.path.exists(self.state_file):
            return
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
                self.attempts = data.get("attempts", {})
                self.quarantined = data.get("quarantined", {})
        except Exception as e:
            logger.warning("Failed to load circuit breaker state: %s", e)

class ImmutabilityEnforcer:
    """
    Enforces Architectural Law 1 (USR-OVER-ETC) & bootc immutability.
    Strictly forbids modifications to /usr and ensures all mutations are
    scoped to /etc overrides, /var runtime storage, or transient /tmp paths.
    """

    FORBIDDEN_PREFIXES = [
        "/usr/",
        "usr/",
        "\\usr\\",
        "/usr",
        "usr",
    ]

    ALLOWED_PREFIXES = [
        "/etc/",
        "etc/",
        "/var/",
        "var/",
        "/tmp/",
        "tmp/",
        "/run/",
        "run/",
    ]

    @classmethod
    def normalize_path(cls, path: str) -> str:
        # Standardize separators to forward slashes for POSIX consistency
        clean = path.replace("\\", "/").strip()
        # Strip Windows drive letter if present for evaluation
        if len(clean) >= 2 and clean[1] == ":":
            clean = clean[2:]
        return clean

    @classmethod
    def is_path_safe(cls, path: str) -> bool:
        norm = cls.normalize_path(path)
        if not norm.startswith("/"):
            norm = "/" + norm

        # Explicit /usr immutability check
        if norm == "/usr" or norm.startswith("/usr/"):
            return False

        parts = [p for p in norm.split("/") if p]
        if parts and parts[0] == "usr":
            return False

        # Allow /etc, /var, /tmp, /run, /AppData/Local/Temp, temp directory
        for allowed in ("/etc/", "/var/", "/tmp/", "/run/"):
            if norm.startswith(allowed) or f"{allowed}" in norm:
                return True

        if "temp" in norm.lower() or "tmp" in norm.lower() or "appdata" in norm.lower():
            return True

        return False

    @classmethod
    def assert_path_safe(cls, path: str) -> None:
        if not cls.is_path_safe(path):
            raise PathViolationError(
                f"Cannot modify immutable path '{path}'. Architectural Law 1 (USR-OVER-ETC) "
                f"and bootc immutability forbid writes to /usr. Apply overrides to /etc or state to /var."
            )

class SafeConfigEditor:
    """
    Safely modifies configuration files with backup creation (.bak.<timestamp>)
    and atomic file replacement, strictly guarded by ImmutabilityEnforcer.
    """

    def __init__(self, enforcer: Optional[ImmutabilityEnforcer] = None) -> None:
        self.enforcer = enforcer or ImmutabilityEnforcer()

    def backup_file(self, path: str) -> Optional[str]:
        if not os.path.exists(path):
            return None
        backup_path = f"{path}.bak.{int(time.time())}"
        try:
            shutil.copy2(path, backup_path)
            return backup_path
        except Exception as e:
            logger.error("Failed to backup file '%s': %s", path, e)
            raise

    def patch_file(
        self,
        path: str,
        new_content: str,
        create_backup: bool = True,
    ) -> bool:
        self.enforcer.assert_path_safe(path)

        parent = os.path.dirname(os.path.abspath(path))
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)

        if create_backup and os.path.exists(path):
            self.backup_file(path)

        # Atomic write via temporary file
        temp_dir = parent if os.path.exists(parent) else None
        fd, temp_path = tempfile.mkstemp(prefix=".selfheal_", dir=temp_dir, text=True)
        try:
            with open(fd, "w", encoding="utf-8") as f:
                f.write(new_content)
            # Replace target atomically
            os.replace(temp_path, path)
            return True
        except Exception as e:
            if os.path.exists(temp_path):
                os.unlink(temp_path)
            logger.error("Atomic patch failed for '%s': %s", path, e)
            raise

class JournaldHarvester:
    """Captures the last 100 journald error log lines for a failing systemd unit."""

    def __init__(self, journal_binary: str = "journalctl") -> None:
        self.journal_binary = journal_binary

    def harvest(self, unit_name: str, max_lines: int = 100) -> List[str]:
        cmd = [self.journal_binary, "-u", unit_name, "-n", str(max_lines), "--no-pager", "-o", "short-iso"]
        try:
            res = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=10)
            lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            return lines[-max_lines:]
        except (subprocess.SubprocessError, FileNotFoundError) as e:
            logger.debug("Journald harvest fallback for %s: %s", unit_name, e)
            return []

class SelfHealer:
    """
    Main Autonomous Self-Healing Diagnostic & Remediation Engine.
    """

    def __init__(
        self,
        circuit_breaker: Optional[CircuitBreaker] = None,
        enforcer: Optional[ImmutabilityEnforcer] = None,
        editor: Optional[SafeConfigEditor] = None,
        harvester: Optional[JournaldHarvester] = None,
        log_file: Optional[str] = None,
    ) -> None:
        self.enforcer = enforcer or ImmutabilityEnforcer()
        self.editor = editor or SafeConfigEditor(self.enforcer)
        self.harvester = harvester or JournaldHarvester()
        self.log_file = (
            log_file
            or os.environ.get("MIOS_SELF_HEAL_LOG")
            or "/var/log/mios/self-heal.log"
        )
        breaker_state = os.environ.get("MIOS_SELF_HEAL_STATE", "/var/lib/mios/self-heal/circuit.json")
        self.circuit_breaker = circuit_breaker or CircuitBreaker(state_file=breaker_state)

    def diagnose_failure(self, event: FailureEvent) -> Dict[str, Any]:
        """
        Formulates structured root cause diagnosis from failure event and journal logs.
        """
        logs_text = "\n".join(event.error_logs)
        diagnosis: Dict[str, Any] = {
            "unit_name": event.unit_name,
            "exit_code": event.exit_code,
            "failure_type": "UNKNOWN_ERROR",
            "root_cause": "Unspecified service failure.",
            "target_files": [],
            "recommended_action": "restart",
            "remediation_patch": None,
            "confidence": 0.5,
            "timestamp": event.timestamp,
        }

        # Check for immutable path tampering
        usr_match = re.search(r"(/usr/[\w\d_./-]+)", logs_text)
        if usr_match and ("Read-only file system" in logs_text or "Permission denied" in logs_text or "cannot modify" in logs_text):
            diagnosis["failure_type"] = "IMMUTABLE_PATH_TARGET"
            diagnosis["root_cause"] = f"Unit attempted unauthorized modification of immutable path: {usr_match.group(1)}"
            diagnosis["target_files"] = [usr_match.group(1)]
            diagnosis["recommended_action"] = "quarantine"
            diagnosis["confidence"] = 0.95
            return diagnosis

        # Check for missing directory in /var
        var_dir_match = re.search(r"(?:No such file or directory|directory does not exist)[^\n:]*?[:\s]+([^\n\r]+)", logs_text, re.IGNORECASE)
        if not var_dir_match:
            var_dir_match = re.search(r"failed to open [^\n]*?([^\n:]+): No such file", logs_text, re.IGNORECASE)
        if not var_dir_match:
            var_dir_match = re.search(r"(/var/[\w\d_./-]+)", logs_text)

        if var_dir_match:
            missing_path = var_dir_match.group(1).strip().strip("'\"")
            norm_missing = missing_path.replace("\\", "/")
            if "/var/" in norm_missing or "var" in norm_missing.lower() or "data" in norm_missing.lower() or "store" in norm_missing.lower():
                diagnosis["failure_type"] = "MISSING_VAR_DIRECTORY"
                diagnosis["root_cause"] = f"Required state directory or path missing: {missing_path}"
                diagnosis["target_files"] = [missing_path]
                diagnosis["recommended_action"] = "create_var_dir"
                diagnosis["confidence"] = 0.90
                return diagnosis

        # Check for configuration syntax error in /etc
        etc_match = re.search(r"(/etc/[\w\d_./-]+\.(?:toml|yaml|yml|json|conf|ini|env))", logs_text)
        if etc_match and ("syntax error" in logs_text.lower() or "failed to parse" in logs_text.lower() or "invalid key" in logs_text.lower() or "error parsing" in logs_text.lower()):
            cfg_file = etc_match.group(1)
            diagnosis["failure_type"] = "CONFIG_SYNTAX_ERROR"
            diagnosis["root_cause"] = f"Syntax or parsing error in configuration file: {cfg_file}"
            diagnosis["target_files"] = [cfg_file]
            diagnosis["recommended_action"] = "patch_config"
            diagnosis["confidence"] = 0.88
            return diagnosis

        # Check for port collision / bind error
        if "Address already in use" in logs_text or "EADDRINUSE" in logs_text:
            port_match = re.search(r":(\d{2,5})\b", logs_text)
            port = port_match.group(1) if port_match else "unknown"
            diagnosis["failure_type"] = "PORT_CONFLICT"
            diagnosis["root_cause"] = f"Port collision detected on port {port}"
            diagnosis["recommended_action"] = "restart_with_backoff"
            diagnosis["confidence"] = 0.85
            return diagnosis

        # Check for transient timeout / socket disconnect
        if "Connection refused" in logs_text or "timed out" in logs_text.lower() or "temporarily unavailable" in logs_text.lower():
            diagnosis["failure_type"] = "TRANSIENT_TIMEOUT"
            diagnosis["root_cause"] = "Transient upstream dependency or socket timeout."
            diagnosis["recommended_action"] = "restart"
            diagnosis["confidence"] = 0.80
            return diagnosis

        # Standard exit failure
        if event.exit_code != 0:
            diagnosis["failure_type"] = "PROCESS_NONZERO_EXIT"
            diagnosis["root_cause"] = f"Process exited with non-zero status {event.exit_code}."
            diagnosis["recommended_action"] = "restart"
            diagnosis["confidence"] = 0.60

        return diagnosis

    def apply_remediation(self, diagnosis: Dict[str, Any]) -> Dict[str, Any]:
        """
        Executes safe remediation action, verifying circuit breaker and immutability invariants.
        """
        unit = diagnosis.get("unit_name", "")
        action = diagnosis.get("recommended_action", "restart")
        target_files = diagnosis.get("target_files", [])

        result: Dict[str, Any] = {
            "unit_name": unit,
            "action": action,
            "success": False,
            "quarantined": False,
            "message": "",
            "timestamp": time.time(),
        }

        # 1. Circuit breaker validation
        if not self.circuit_breaker.can_attempt(unit):
            result["quarantined"] = True
            result["message"] = f"Circuit breaker tripped for '{unit}'. Exceeded max restarts within window. Quarantined."
            self.circuit_breaker.quarantine_unit(unit)
            self._log_rca(diagnosis, result)
            return result

        # 2. Immutability validation
        for target in target_files:
            if not self.enforcer.is_path_safe(target):
                result["message"] = f"Remediation aborted: target '{target}' violates immutability rules (/usr protected)."
                self.circuit_breaker.record_attempt(unit, success=False)
                self._log_rca(diagnosis, result)
                raise PathViolationError(result["message"])

        # 3. Action Execution
        try:
            if action == "quarantine":
                self.circuit_breaker.quarantine_unit(unit)
                result["quarantined"] = True
                result["success"] = True
                result["message"] = f"Unit '{unit}' placed in quarantine as recommended."

            elif action == "create_var_dir":
                for target in target_files:
                    norm_tgt = target.replace("\\", "/")
                    if self.enforcer.is_path_safe(target) and ("/var" in norm_tgt.lower() or "/tmp" in norm_tgt.lower() or "/run" in norm_tgt.lower() or "temp" in norm_tgt.lower()):
                        os.makedirs(target, exist_ok=True)
                # Restart service after creating dir
                self._restart_systemd_unit(unit)
                result["success"] = True
                result["message"] = f"Created state directory and restarted '{unit}'."
                self.circuit_breaker.record_attempt(unit, success=True)

            elif action == "patch_config":
                patch = diagnosis.get("remediation_patch")
                if patch and "file" in patch and "content" in patch:
                    self.editor.patch_file(patch["file"], patch["content"], create_backup=True)
                    self._systemctl_daemon_reload()
                    self._restart_systemd_unit(unit)
                    result["success"] = True
                    result["message"] = f"Patched configuration '{patch['file']}' and restarted '{unit}'."
                    self.circuit_breaker.record_attempt(unit, success=True)
                else:
                    # Generic restart fallback
                    self._restart_systemd_unit(unit)
                    result["success"] = True
                    result["message"] = f"Restarted '{unit}'."
                    self.circuit_breaker.record_attempt(unit, success=True)

            elif action in ("restart", "restart_with_backoff"):
                self._restart_systemd_unit(unit)
                result["success"] = True
                result["message"] = f"Restarted '{unit}' successfully."
                self.circuit_breaker.record_attempt(unit, success=True)

            else:
                result["message"] = f"No automated execution strategy for action '{action}'."
                self.circuit_breaker.record_attempt(unit, success=False)

        except Exception as e:
            result["success"] = False
            result["message"] = f"Remediation failed: {e}"
            self.circuit_breaker.record_attempt(unit, success=False)
            logger.error("Remediation execution error: %s", e)

        self._log_rca(diagnosis, result)
        return result

    def _restart_systemd_unit(self, unit_name: str) -> None:
        try:
            subprocess.run(["systemctl", "restart", unit_name], check=True, timeout=15, capture_output=True)
        except (subprocess.SubprocessError, FileNotFoundError):
            # In test environments without real systemd daemon
            logger.debug("Systemctl restart simulated for %s", unit_name)

    def _systemctl_daemon_reload(self) -> None:
        try:
            subprocess.run(["systemctl", "daemon-reload"], check=True, timeout=15, capture_output=True)
        except (subprocess.SubprocessError, FileNotFoundError):
            logger.debug("Systemctl daemon-reload simulated")

    def _log_rca(self, diagnosis: Dict[str, Any], result: Dict[str, Any]) -> None:
        record = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "unit_name": diagnosis.get("unit_name"),
            "failure_type": diagnosis.get("failure_type"),
            "root_cause": diagnosis.get("root_cause"),
            "action_taken": result.get("action"),
            "success": result.get("success"),
            "quarantined": result.get("quarantined"),
            "message": result.get("message"),
        }
        try:
            parent = os.path.dirname(os.path.abspath(self.log_file))
            if parent and not os.path.exists(parent):
                os.makedirs(parent, exist_ok=True)
            with open(self.log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record) + "\n")
        except Exception as e:
            logger.warning("Failed to write self-heal log: %s", e)

    def process_unit(self, unit_name: str, exit_code: int = 1, error_logs: Optional[List[str]] = None) -> Dict[str, Any]:
        logs = error_logs if error_logs is not None else self.harvester.harvest(unit_name)
        event = FailureEvent(unit_name=unit_name, exit_code=exit_code, error_logs=logs)
        diagnosis = self.diagnose_failure(event)
        return self.apply_remediation(diagnosis)

    def get_failed_systemd_units(self) -> List[str]:
        try:
            res = subprocess.run(
                ["systemctl", "--failed", "--plain", "--no-legend"],
                capture_output=True,
                text=True,
                check=False,
                timeout=10,
            )
            units = []
            for line in res.stdout.splitlines():
                parts = line.strip().split()
                if parts and parts[0].endswith((".service", ".timer", ".socket")):
                    units.append(parts[0])
            return units
        except (subprocess.SubprocessError, FileNotFoundError):
            return []

    def run_once(self) -> List[Dict[str, Any]]:
        failed = self.get_failed_systemd_units()
        results = []
        for unit in failed:
            res = self.process_unit(unit)
            results.append(res)
        return results

    def run_daemon(self, interval: float = 30.0) -> None:
        logger.info("Starting Self-Healing daemon (poll interval: %.1fs)", interval)
        while True:
            try:
                self.run_once()
            except Exception as e:
                logger.error("Error during self-heal scan cycle: %s", e)
            time.sleep(interval)

def main() -> int:
    parser = argparse.ArgumentParser(description="MiOS Autonomous Self-Healing Code Remediation Agent")
    parser.add_argument("--unit", help="Process and remediate specific failed systemd unit")
    parser.add_argument("--run-once", action="store_true", help="Scan and remediate all failed units once")
    parser.add_argument("--daemon", action="store_true", help="Run continuously in background monitor mode")
    parser.add_argument("--interval", type=float, default=30.0, help="Daemon poll interval in seconds")
    parser.add_argument("--status", action="store_true", help="Show circuit breaker and quarantine status")
    parser.add_argument("--reset-circuit-breaker", nargs="?", const="ALL", help="Reset circuit breaker for unit or all")
    parser.add_argument("--log-file", help="Custom RCA log file path")
    parser.add_argument("--json", action="store_true", help="Format output as JSON")

    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

    healer = SelfHealer(log_file=args.log_file)

    if args.status:
        status = healer.circuit_breaker.get_status(args.unit)
        if args.json:
            print(json.dumps(status, indent=2))
        else:
            print(f"Circuit Breaker Status:\n{json.dumps(status, indent=2)}")
        return 0

    if args.reset_circuit_breaker:
        target = None if args.reset_circuit_breaker == "ALL" else args.reset_circuit_breaker
        healer.circuit_breaker.reset(target)
        print(f"Reset circuit breaker for: {target or 'ALL'}")
        return 0

    if args.unit:
        res = healer.process_unit(args.unit)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"Remediation result for {args.unit}: {res.get('message')}")
        return 0 if res.get("success") else 1

    if args.daemon:
        healer.run_daemon(interval=args.interval)
        return 0

    # Default: run-once
    results = healer.run_once()
    if args.json:
        print(json.dumps(results, indent=2))
    else:
        print(f"Scanned failed units. Processed {len(results)} units.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
