#!/usr/bin/env python3
# AI-hint: Screen lock manager with biometric FIDO2 and fingerprint authentication integration.
# AI-related: tests/test-ux.py, /etc/pam.d/swaylock, /etc/pam.d/gdm-password
# AI-functions: BiometricLockManager, BiometricSensor, main
"""
MiOS Screen Lock Manager with Biometric & FIDO2 Authentication (task_407).

Manages biometric hardware sensor inspection (fingerprint fprintd, FIDO2/CTAP2 pam_u2f)
and PAM configuration stack generation for swaylock, hyprlock, and gdm-password.
Unconditionally preserves password authentication fallback (auth include system-auth)
to guarantee the operator is never locked out.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.dirname(os.path.abspath(__file__))
_LIB_PATH = os.path.normpath(os.path.join(_HERE, "..", "..", "..", "lib", "mios"))
if os.path.isdir(_LIB_PATH) and _LIB_PATH not in sys.path:
    sys.path.insert(0, _LIB_PATH)

try:
    import mios_toml
except ImportError:
    mios_toml = None

DEFAULT_PAM_DIR = "/etc/pam.d"
U2F_AUTH_FILE = "/etc/mios/security/u2f_keys"

@dataclass
class BiometricSensor:
    """Biometric hardware sensor or hardware security key descriptor."""
    sensor_type: str  # "fingerprint", "fido2_ctap2", "smartcard"
    device_name: str
    driver: str
    is_enrolled: bool
    status: str
    capabilities: List[str]

class BiometricLockManager:
    """Manages biometric PAM stack generation, sensor queries, and screen lock invocation."""

    def __init__(
        self,
        pam_dir: str = DEFAULT_PAM_DIR,
        u2f_file: str = U2F_AUTH_FILE,
        mock: bool = False,
        dry_run: bool = False,
        verbose: bool = False,
    ) -> None:
        self.pam_dir = pam_dir
        self.u2f_file = u2f_file
        self.mock = mock
        self.dry_run = dry_run
        self.verbose = verbose

    def _get_palette(self) -> Dict[str, str]:
        """Fetch color scheme for screen lock styling."""
        if mios_toml is not None:
            try:
                return mios_toml.colors()
            except Exception:
                pass
        return {
            "bg": "#282262",
            "fg": "#E7DFD3",
            "accent": "#1A407F",
            "cursor": "#F35C15",
            "success": "#3E7765",
            "error": "#DC271B",
            "muted": "#948E8E",
        }

    def check_sensors(self) -> Dict[str, Any]:
        """Query attached fingerprint readers and FIDO2/CTAP2 hardware keys."""
        sensors: List[BiometricSensor] = []

        if self.mock:
            sensors.append(
                BiometricSensor(
                    sensor_type="fingerprint",
                    device_name="Synaptics Prometheus Touch Fingerprint Reader",
                    driver="pam_fprintd",
                    is_enrolled=True,
                    status="ready",
                    capabilities=["swipe", "touch", "verification"],
                )
            )
            sensors.append(
                BiometricSensor(
                    sensor_type="fido2_ctap2",
                    device_name="Yubico YubiKey 5 FIDO2 / CTAP2 Security Key",
                    driver="pam_u2f",
                    is_enrolled=True,
                    status="ready",
                    capabilities=["user_presence", "pin_verification", "hmac_secret"],
                )
            )
            return {
                "status": "success",
                "sensors_detected": len(sensors),
                "sensors": [asdict(s) for s in sensors],
                "mock": True,
            }

        # Real hardware probe: fprintd
        if shutil.which("fprintd-list"):
            try:
                username = os.environ.get("USER", "mios")
                res = subprocess.run(["fprintd-list", username], capture_output=True, text=True, timeout=2)
                if res.returncode == 0 and "found" in res.stdout.lower():
                    sensors.append(
                        BiometricSensor(
                            sensor_type="fingerprint",
                            device_name="fprintd Compatible Fingerprint Reader",
                            driver="pam_fprintd",
                            is_enrolled="enrolled" in res.stdout.lower() or "right-index" in res.stdout,
                            status="ready",
                            capabilities=["biometric_auth"],
                        )
                    )
            except Exception:
                pass

        # Real hardware probe: fido2-token
        if shutil.which("fido2-token"):
            try:
                res = subprocess.run(["fido2-token", "-L"], capture_output=True, text=True, timeout=2)
                if res.returncode == 0 and res.stdout.strip():
                    for line in res.stdout.strip().splitlines():
                        sensors.append(
                            BiometricSensor(
                                sensor_type="fido2_ctap2",
                                device_name=line.strip() or "FIDO2 CTAP2 Device",
                                driver="pam_u2f",
                                is_enrolled=os.path.exists(self.u2f_file),
                                status="ready",
                                capabilities=["fido2", "pin", "touch"],
                            )
                        )
            except Exception:
                pass

        return {
            "status": "success",
            "sensors_detected": len(sensors),
            "sensors": [asdict(s) for s in sensors],
            "mock": self.mock,
        }

    def render_pam_config(self, service_name: str) -> str:
        """Render PAM configuration stack with biometric auth and unconditional password fallback."""
        return f"""#%PAM-1.0
# MiOS Biometric & FIDO2 Authentication Stack for {service_name}
# Generated automatically by biometric_lock.py (task_407)
# Invariant: Password authentication fallback (system-auth) is unconditionally preserved.

auth        sufficient    pam_fprintd.so
auth        sufficient    pam_u2f.so cue authfile={self.u2f_file}
auth        include       system-auth
account     include       system-auth
password    include       system-auth
session     include       system-auth
"""

    def generate_pam_files(
        self,
        services: Optional[List[str]] = None,
        out_dir: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Generate PAM configuration files for specified desktop lock services."""
        dest_dir = out_dir or self.pam_dir
        target_services = services or ["swaylock", "hyprlock", "gdm-password"]
        files_written: List[str] = []
        previews: Dict[str, str] = {}

        for s in target_services:
            content = self.render_pam_config(s)
            previews[s] = content
            file_path = os.path.join(dest_dir, s)
            files_written.append(file_path)

            if not self.mock and not self.dry_run:
                os.makedirs(dest_dir, exist_ok=True)
                with open(file_path, "w", encoding="utf-8") as f:
                    f.write(content)

        return {
            "status": "success",
            "action": "generate_pam",
            "target_dir": dest_dir,
            "services": target_services,
            "files": files_written,
            "previews": previews,
            "dry_run": self.dry_run,
            "mock": self.mock,
        }

    def lock_screen(self) -> Dict[str, Any]:
        """Trigger desktop screen lock with themed visual arguments."""
        palette = self._get_palette()
        bg = palette.get("bg", "#282262").lstrip("#")
        accent = palette.get("accent", "#1A407F").lstrip("#")
        cursor = palette.get("cursor", "#F35C15").lstrip("#")
        fg = palette.get("fg", "#E7DFD3").lstrip("#")

        lock_cmd: List[str] = []
        lock_tool = "mock-lock"

        if self.mock or self.dry_run:
            lock_cmd = [
                "swaylock",
                "-f",
                f"--color={bg}",
                f"--ring-color={accent}",
                f"--inside-color={bg}",
                f"--key-hl-color={cursor}",
                f"--text-color={fg}",
                "--indicator-radius=80",
            ]
        elif shutil.which("swaylock"):
            lock_tool = "swaylock"
            lock_cmd = [
                "swaylock",
                "-f",
                f"--color={bg}",
                f"--ring-color={accent}",
                f"--inside-color={bg}",
                f"--key-hl-color={cursor}",
                f"--text-color={fg}",
                "--indicator-radius=80",
            ]
            try:
                subprocess.Popen(lock_cmd)
            except Exception as e:
                return {"status": "error", "error": f"Failed to execute swaylock: {e}"}
        elif shutil.which("hyprlock"):
            lock_tool = "hyprlock"
            lock_cmd = ["hyprlock"]
            try:
                subprocess.Popen(lock_cmd)
            except Exception as e:
                return {"status": "error", "error": f"Failed to execute hyprlock: {e}"}
        else:
            lock_tool = "loginctl"
            lock_cmd = ["loginctl", "lock-session"]
            try:
                subprocess.run(lock_cmd, check=False, timeout=5)
            except Exception as e:
                return {"status": "error", "error": f"Failed to execute loginctl lock-session: {e}"}

        return {
            "status": "success",
            "action": "lock_screen",
            "lock_tool": lock_tool,
            "command": lock_cmd,
            "dry_run": self.dry_run,
            "mock": self.mock,
        }

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS Biometric & FIDO2 Screen Lock Manager (task_407)"
    )
    parser.add_argument("--check-sensors", action="store_true", help="Inspect attached biometric hardware sensors")
    parser.add_argument("--generate-pam", action="store_true", help="Generate PAM configuration stack files")
    parser.add_argument(
        "--target-service",
        choices=["swaylock", "hyprlock", "gdm-password", "all"],
        default="all",
        help="Target lock service for PAM stack",
    )
    parser.add_argument("--out-dir", help="Output directory for PAM configuration files")
    parser.add_argument("--lock", action="store_true", help="Trigger desktop screen lock")
    parser.add_argument("--mock", action="store_true", help="Deterministic in-memory mock mode")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without writing files or locking")
    parser.add_argument("--json", action="store_true", help="Emit output as JSON")
    parser.add_argument("--verbose", action="store_true", help="Enable verbose output")

    args = parser.parse_args()

    manager = BiometricLockManager(
        pam_dir=args.out_dir or DEFAULT_PAM_DIR,
        mock=args.mock,
        dry_run=args.dry_run,
        verbose=args.verbose,
    )

    try:
        if args.check_sensors:
            result = manager.check_sensors()
        elif args.lock:
            result = manager.lock_screen()
        elif args.generate_pam:
            services = None if args.target_service == "all" else [args.target_service]
            result = manager.generate_pam_files(services=services, out_dir=args.out_dir)
        else:
            # Default action: check sensors and report summary
            result = manager.check_sensors()

        if args.json:
            print(json.dumps(result, indent=2))
        else:
            status = result.get("status", "ok")
            print(f"[biometric_lock] Status: {status}")
            if "sensors" in result:
                print(f"  Detected Sensors: {result['sensors_detected']}")
                for s in result["sensors"]:
                    print(f"  - {s['device_name']} [{s['sensor_type']}] (Driver: {s['driver']}, Enrolled: {s['is_enrolled']})")
            if "files" in result:
                for f in result["files"]:
                    print(f"  Generated PAM config: {f}")
        return 0 if result.get("status") == "success" else 1
    except Exception as e:
        err = {"status": "error", "error": str(e)}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"[biometric_lock] ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
