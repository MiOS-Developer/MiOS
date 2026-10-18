#!/usr/bin/env python3
# AI-hint: Portable drive LUKS2 FIDO2 / CTAP2 token enrollment helper using systemd-cryptenroll.
# AI-related: tests/test-sec.py, usr/libexec/mios/mios-luks-enroll, usr/share/mios/mios.toml
# AI-functions: Fido2EnrollEngine, Fido2Token, LuksKeyslot, EnrollmentResult, StatusResult, main
"""
MiOS Portable Drive LUKS2 FIDO2 / CTAP2 Token Enrollment Engine.

Binds hardware security keys (YubiKey 5, SoloKeys, Nitrokey) to portable LUKS2 encrypted
partitions using systemd-cryptenroll. Enables secure, mobile LUKS2 volume unlocking across
heterogeneous host machines without relying on host-bound TPM2 PCR policies (ADR-0016 D15).
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

@dataclasses.dataclass
class Fido2Token:
    """Represents a discovered FIDO2/CTAP2 hardware token."""
    device_path: str
    product_name: str
    manufacturer: str = "Unknown"
    has_pin: bool = False
    has_uv: bool = False
    has_up: bool = True
    protocol: str = "CTAP2"

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

@dataclasses.dataclass
class LuksKeyslot:
    """Represents a LUKS2 keyslot."""
    slot_id: int
    slot_type: str  # "fido2", "passphrase", "recovery", "tpm2", "unknown"
    cipher: str = "aes-xts-plain64"
    priority: str = "normal"
    details: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

@dataclasses.dataclass
class EnrollmentResult:
    """Result of a FIDO2 token or recovery key enrollment operation."""
    status: str  # "success", "error", "skipped"
    device: str
    fido2_device: str
    keyslot: Optional[int] = None
    recovery_key: Optional[str] = None
    message: str = ""
    command_executed: Optional[str] = None
    details: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)

@dataclasses.dataclass
class StatusResult:
    """Status report of device LUKS2 format and enrolled tokens/keyslots."""
    status: str
    device: str
    is_luks2: bool
    uuid: str = ""
    label: str = ""
    tokens: List[Fido2Token] = dataclasses.field(default_factory=list)
    keyslots: List[LuksKeyslot] = dataclasses.field(default_factory=list)
    fido2_enrolled: bool = False
    recovery_enrolled: bool = False
    details: Dict[str, Any] = dataclasses.field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "device": self.device,
            "is_luks2": self.is_luks2,
            "uuid": self.uuid,
            "label": self.label,
            "tokens": [t.to_dict() for t in self.tokens],
            "keyslots": [k.to_dict() for k in self.keyslots],
            "fido2_enrolled": self.fido2_enrolled,
            "recovery_enrolled": self.recovery_enrolled,
            "details": self.details,
        }

class Fido2EnrollEngine:
    """Orchestrates FIDO2 discovery, LUKS2 verification, and keyslot enrollment."""

    def __init__(self, mock: bool = False, dry_run: bool = False, verbose: bool = False) -> None:
        self.mock = mock
        self.dry_run = dry_run
        self.verbose = verbose
        # In-memory mock storage for hermetic test execution
        self._mock_devices: Dict[str, Dict[str, Any]] = {
            "/dev/sdb2": {
                "is_luks2": True,
                "uuid": "a1b2c3d4-e5f6-7a8b-9c0d-1e2f3a4b5c6d",
                "label": "MiOS-Cat-Storage",
                "keyslots": {
                    0: LuksKeyslot(slot_id=0, slot_type="passphrase", cipher="aes-xts-plain64"),
                },
                "tokens": [],
            },
            "/dev/sdc1": {
                "is_luks2": True,
                "uuid": "f9e8d7c6-b5a4-3210-fedc-ba9876543210",
                "label": "MiOS-Backup",
                "keyslots": {
                    0: LuksKeyslot(slot_id=0, slot_type="passphrase", cipher="aes-xts-plain64"),
                    1: LuksKeyslot(slot_id=1, slot_type="fido2", cipher="aes-xts-plain64", details={"fido2_device": "/dev/hidraw0"}),
                },
                "tokens": [
                    Fido2Token(device_path="/dev/hidraw0", product_name="YubiKey 5 NFC", manufacturer="Yubico", has_pin=True, has_uv=False, has_up=True)
                ],
            },
            "/dev/sdd1": {
                "is_luks2": False,
                "uuid": "",
                "label": "Plain-FAT32",
                "keyslots": {},
                "tokens": [],
            },
        }
        self._mock_fido2_tokens: List[Fido2Token] = [
            Fido2Token(device_path="/dev/hidraw0", product_name="YubiKey 5 NFC", manufacturer="Yubico", has_pin=True, has_uv=False, has_up=True),
            Fido2Token(device_path="/dev/hidraw1", product_name="Solo 2", manufacturer="SoloKeys", has_pin=False, has_uv=True, has_up=True),
        ]

    def _log(self, msg: str) -> None:
        if self.verbose:
            print(f"[fido2-enroll] {msg}", file=sys.stderr)

    def discover_tokens(self) -> List[Fido2Token]:
        """Discovers attached CTAP2 / FIDO2 security keys."""
        if self.mock:
            self._log(f"Mock discovery returned {len(self._mock_fido2_tokens)} token(s).")
            return list(self._mock_fido2_tokens)

        tokens: List[Fido2Token] = []

        # 1. Try systemd-cryptenroll --fido2-device=list
        cryptenroll_bin = shutil.which("systemd-cryptenroll")
        if cryptenroll_bin:
            try:
                proc = subprocess.run(
                    [cryptenroll_bin, "--fido2-device=list"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if proc.returncode == 0:
                    for line in proc.stdout.strip().splitlines():
                        line = line.strip()
                        if not line or line.startswith("PATH") or line.startswith("---"):
                            continue
                        parts = line.split(maxsplit=1)
                        dev_path = parts[0]
                        prod_name = parts[1] if len(parts) > 1 else "Generic FIDO2 Device"
                        tokens.append(Fido2Token(
                            device_path=dev_path,
                            product_name=prod_name,
                            manufacturer="Detected via systemd-cryptenroll",
                            has_up=True,
                        ))
                    if tokens:
                        return tokens
            except Exception as e:
                self._log(f"systemd-cryptenroll device list failed: {e}")

        # 2. Try fido2-token -L
        fido2_token_bin = shutil.which("fido2-token")
        if fido2_token_bin:
            try:
                proc = subprocess.run(
                    [fido2_token_bin, "-L"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if proc.returncode == 0:
                    for line in proc.stdout.strip().splitlines():
                        line = line.strip()
                        if not line:
                            continue
                        # format: /dev/hidrawX: vendor=0x1050, product=0x0407 (YubiKey OTP+FIDO+CCID)
                        parts = line.split(":", 1)
                        dev_path = parts[0].strip()
                        prod_name = parts[1].strip() if len(parts) > 1 else "FIDO2 Token"
                        tokens.append(Fido2Token(
                            device_path=dev_path,
                            product_name=prod_name,
                            manufacturer="Detected via fido2-token",
                            has_up=True,
                        ))
                    if tokens:
                        return tokens
            except Exception as e:
                self._log(f"fido2-token -L failed: {e}")

        # 3. Fallback scan of /dev/hidraw* via /sys/class/hidraw
        if os.path.exists("/sys/class/hidraw"):
            try:
                for entry in sorted(os.listdir("/sys/class/hidraw")):
                    hidraw_dev = f"/dev/{entry}"
                    uevent_file = os.path.join("/sys/class/hidraw", entry, "device", "uevent")
                    prod_name = "HID Raw Device"
                    if os.path.exists(uevent_file):
                        with open(uevent_file, "r", encoding="utf-8", errors="ignore") as f:
                            content = f.read()
                            m = re.search(r"HID_NAME=(.+)", content)
                            if m:
                                prod_name = m.group(1).strip()
                    if any(kw in prod_name.lower() for kw in ["yubikey", "fido", "solokey", "nitrokey", "security key"]):
                        tokens.append(Fido2Token(
                            device_path=hidraw_dev,
                            product_name=prod_name,
                            manufacturer="Kernel HID driver",
                            has_up=True,
                        ))
            except Exception as e:
                self._log(f"sysfs hidraw scan failed: {e}")

        return tokens

    def inspect_device(self, device_path: str) -> StatusResult:
        """Inspects target partition to verify LUKS2 header and list active keyslots."""
        if not device_path:
            return StatusResult(status="error", device="", is_luks2=False, details={"error": "No device path provided"})

        if self.mock:
            dev_info = self._mock_devices.get(device_path)
            if not dev_info:
                return StatusResult(
                    status="error",
                    device=device_path,
                    is_luks2=False,
                    details={"error": f"Device {device_path} not found in mock store"},
                )
            is_luks2 = dev_info["is_luks2"]
            uuid_val = dev_info["uuid"]
            label_val = dev_info["label"]
            keyslots = list(dev_info["keyslots"].values())
            has_fido2 = any(k.slot_type == "fido2" for k in keyslots)
            has_recovery = any(k.slot_type == "recovery" for k in keyslots)
            tokens = self.discover_tokens()
            return StatusResult(
                status="ok" if is_luks2 else "not_luks2",
                device=device_path,
                is_luks2=is_luks2,
                uuid=uuid_val,
                label=label_val,
                tokens=tokens,
                keyslots=keyslots,
                fido2_enrolled=has_fido2,
                recovery_enrolled=has_recovery,
                details={"mock": True},
            )

        # Real execution: check cryptsetup
        cryptsetup_bin = shutil.which("cryptsetup")
        if not cryptsetup_bin:
            return StatusResult(
                status="error",
                device=device_path,
                is_luks2=False,
                details={"error": "cryptsetup binary not found on system"},
            )

        if not os.path.exists(device_path):
            return StatusResult(
                status="error",
                device=device_path,
                is_luks2=False,
                details={"error": f"Block device {device_path} does not exist"},
            )

        # Verify LUKS2 format
        is_luks2 = False
        try:
            p = subprocess.run(
                [cryptsetup_bin, "isLuks", "--type", "luks2", device_path],
                capture_output=True,
                text=True,
            )
            is_luks2 = (p.returncode == 0)
        except Exception as e:
            return StatusResult(
                status="error",
                device=device_path,
                is_luks2=False,
                details={"error": f"cryptsetup isLuks failed: {e}"},
            )

        if not is_luks2:
            return StatusResult(
                status="not_luks2",
                device=device_path,
                is_luks2=False,
                details={"message": "Target partition is not formatted as LUKS2"},
            )

        # Parse luksDump
        keyslots: List[LuksKeyslot] = []
        uuid_val = ""
        label_val = ""
        has_fido2 = False
        has_recovery = False

        try:
            dump_proc = subprocess.run(
                [cryptsetup_bin, "luksDump", device_path],
                capture_output=True,
                text=True,
            )
            if dump_proc.returncode == 0:
                dump_text = dump_proc.stdout
                m_uuid = re.search(r"UUID:\s+([a-f0-9\-]+)", dump_text, re.IGNORECASE)
                if m_uuid:
                    uuid_val = m_uuid.group(1).strip()
                m_label = re.search(r"Label:\s+([^\n]+)", dump_text)
                if m_label:
                    label_val = m_label.group(1).strip()

                # Check tokens in LUKS2 header
                if "systemd-fido2" in dump_text:
                    has_fido2 = True
                if "systemd-recovery" in dump_text:
                    has_recovery = True

                # Parse keyslots
                slot_matches = re.finditer(r"^\s*(\d+):\s+luks2", dump_text, re.MULTILINE)
                for m in slot_matches:
                    slot_id = int(m.group(1))
                    slot_type = "fido2" if (has_fido2 and slot_id > 0) else "passphrase"
                    keyslots.append(LuksKeyslot(slot_id=slot_id, slot_type=slot_type))
        except Exception as e:
            self._log(f"luksDump parsing encountered non-fatal error: {e}")

        tokens = self.discover_tokens()
        return StatusResult(
            status="ok",
            device=device_path,
            is_luks2=True,
            uuid=uuid_val,
            label=label_val,
            tokens=tokens,
            keyslots=keyslots,
            fido2_enrolled=has_fido2,
            recovery_enrolled=has_recovery,
        )

    def enroll_fido2(
        self,
        device_path: str,
        fido2_device: str = "auto",
        require_pin: bool = False,
        require_touch: bool = True,
        require_user_verification: bool = False,
        recovery_key: bool = False,
        wipe_existing_fido2: bool = False,
    ) -> EnrollmentResult:
        """Enrolls FIDO2/CTAP2 token into LUKS2 keyslot via systemd-cryptenroll."""
        self._log(f"Starting FIDO2 enrollment on {device_path} (fido2_device={fido2_device})")

        if self.mock:
            dev_info = self._mock_devices.get(device_path)
            if not dev_info or not dev_info["is_luks2"]:
                return EnrollmentResult(
                    status="error",
                    device=device_path,
                    fido2_device=fido2_device,
                    message=f"Device {device_path} is not a valid LUKS2 volume in mock store",
                )

            if wipe_existing_fido2:
                to_del = [sid for sid, k in dev_info["keyslots"].items() if k.slot_type == "fido2"]
                for sid in to_del:
                    del dev_info["keyslots"][sid]

            new_slot_id = max(dev_info["keyslots"].keys(), default=-1) + 1
            dev_info["keyslots"][new_slot_id] = LuksKeyslot(
                slot_id=new_slot_id,
                slot_type="fido2",
                cipher="aes-xts-plain64",
                details={
                    "fido2_device": fido2_device,
                    "require_pin": require_pin,
                    "require_touch": require_touch,
                    "require_uv": require_user_verification,
                },
            )

            rec_key_val = None
            if recovery_key:
                rec_slot_id = new_slot_id + 1
                rec_key_val = "xxxx-xxxx-xxxx-xxxx-xxxx-xxxx-xxxx-xxxx"
                dev_info["keyslots"][rec_slot_id] = LuksKeyslot(
                    slot_id=rec_slot_id,
                    slot_type="recovery",
                    cipher="aes-xts-plain64",
                )

            mock_cmd = (
                f"systemd-cryptenroll --fido2-device={fido2_device} "
                f"--fido2-with-client-pin={'yes' if require_pin else 'no'} "
                f"--fido2-with-user-presence={'yes' if require_touch else 'no'} "
                f"--fido2-with-user-verification={'yes' if require_user_verification else 'no'}"
            )
            if recovery_key:
                mock_cmd += " --recovery-key"
            mock_cmd += f" {device_path}"

            return EnrollmentResult(
                status="success",
                device=device_path,
                fido2_device=fido2_device,
                keyslot=new_slot_id,
                recovery_key=rec_key_val,
                message=f"Successfully enrolled FIDO2 security key to {device_path} keyslot {new_slot_id}",
                command_executed=mock_cmd,
                details={"mock": True, "pin_required": require_pin, "touch_required": require_touch},
            )

        # Pre-flight LUKS2 verification
        status = self.inspect_device(device_path)
        if not status.is_luks2:
            return EnrollmentResult(
                status="error",
                device=device_path,
                fido2_device=fido2_device,
                message=f"Device {device_path} is not a valid LUKS2 volume",
                details=status.details,
            )

        cryptenroll_bin = shutil.which("systemd-cryptenroll")
        if not cryptenroll_bin:
            return EnrollmentResult(
                status="error",
                device=device_path,
                fido2_device=fido2_device,
                message="systemd-cryptenroll executable not found in PATH",
            )

        # Build enrollment command
        cmd = [
            cryptenroll_bin,
            f"--fido2-device={fido2_device}",
            f"--fido2-with-client-pin={'yes' if require_pin else 'no'}",
            f"--fido2-with-user-presence={'yes' if require_touch else 'no'}",
            f"--fido2-with-user-verification={'yes' if require_user_verification else 'no'}",
        ]
        if wipe_existing_fido2:
            cmd.append("--wipe-slot=fido2")
        if recovery_key:
            cmd.append("--recovery-key")
        cmd.append(device_path)

        cmd_str = " ".join(cmd)
        self._log(f"Executing: {cmd_str}")

        if self.dry_run:
            return EnrollmentResult(
                status="success",
                device=device_path,
                fido2_device=fido2_device,
                message="Dry-run: command formulated successfully",
                command_executed=cmd_str,
                details={"dry_run": True},
            )

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if proc.returncode != 0:
                return EnrollmentResult(
                    status="error",
                    device=device_path,
                    fido2_device=fido2_device,
                    message=f"systemd-cryptenroll failed: {proc.stderr.strip()}",
                    command_executed=cmd_str,
                    details={"stderr": proc.stderr, "stdout": proc.stdout},
                )

            # Look for generated recovery key in stdout / stderr
            combined = proc.stdout + "\n" + proc.stderr
            rec_key_match = re.search(r"([a-z0-9]{4}(?:-[a-z0-9]{4}){7})", combined, re.IGNORECASE)
            extracted_rec_key = rec_key_match.group(1) if rec_key_match else None

            # Re-inspect to get newly assigned keyslot ID
            new_status = self.inspect_device(device_path)
            assigned_slot = None
            if new_status.keyslots:
                assigned_slot = new_status.keyslots[-1].slot_id

            return EnrollmentResult(
                status="success",
                device=device_path,
                fido2_device=fido2_device,
                keyslot=assigned_slot,
                recovery_key=extracted_rec_key,
                message=f"FIDO2 token successfully enrolled onto {device_path}",
                command_executed=cmd_str,
                details={"stdout": proc.stdout},
            )
        except subprocess.TimeoutExpired:
            return EnrollmentResult(
                status="error",
                device=device_path,
                fido2_device=fido2_device,
                message="Enrollment timed out waiting for user touch / PIN entry",
                command_executed=cmd_str,
            )
        except Exception as e:
            return EnrollmentResult(
                status="error",
                device=device_path,
                fido2_device=fido2_device,
                message=f"Unexpected error during enrollment: {e}",
                command_executed=cmd_str,
            )

    def wipe_keyslot(self, device_path: str, wipe_spec: str = "fido2") -> Dict[str, Any]:
        """Wipes keyslots matching specification ('fido2', 'recovery', slot index, or 'all')."""
        self._log(f"Wiping keyslots '{wipe_spec}' on {device_path}")

        if self.mock:
            dev_info = self._mock_devices.get(device_path)
            if not dev_info or not dev_info["is_luks2"]:
                return {"status": "error", "message": f"Device {device_path} not found in mock store"}

            wiped_count = 0
            if wipe_spec == "fido2":
                to_del = [sid for sid, k in dev_info["keyslots"].items() if k.slot_type == "fido2"]
            elif wipe_spec == "recovery":
                to_del = [sid for sid, k in dev_info["keyslots"].items() if k.slot_type == "recovery"]
            elif wipe_spec.isdigit():
                target_id = int(wipe_spec)
                to_del = [target_id] if target_id in dev_info["keyslots"] else []
            else:
                to_del = list(dev_info["keyslots"].keys())

            for sid in to_del:
                del dev_info["keyslots"][sid]
                wiped_count += 1

            return {
                "status": "success",
                "device": device_path,
                "wipe_spec": wipe_spec,
                "wiped_slots": to_del,
                "wiped_count": wiped_count,
                "message": f"Successfully wiped {wiped_count} keyslot(s)",
            }

        cryptenroll_bin = shutil.which("systemd-cryptenroll")
        if not cryptenroll_bin:
            return {"status": "error", "message": "systemd-cryptenroll binary not found"}

        cmd = [cryptenroll_bin, f"--wipe-slot={wipe_spec}", device_path]
        if self.dry_run:
            return {"status": "success", "dry_run": True, "command": " ".join(cmd)}

        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            if proc.returncode == 0:
                return {"status": "success", "device": device_path, "message": f"Wiped slot(s) {wipe_spec}"}
            return {"status": "error", "message": proc.stderr.strip()}
        except Exception as e:
            return {"status": "error", "message": str(e)}

    def test_unlock(self, device_path: str, fido2_device: Optional[str] = None) -> Dict[str, Any]:
        """Tests unlocking target LUKS2 volume using attached FIDO2 token."""
        if self.mock:
            dev_info = self._mock_devices.get(device_path)
            if not dev_info or not dev_info["is_luks2"]:
                return {"status": "error", "unlocked": False, "message": f"Device {device_path} not found in mock store"}
            has_fido2 = any(k.slot_type == "fido2" for k in dev_info["keyslots"].values())
            if has_fido2:
                return {
                    "status": "success",
                    "unlocked": True,
                    "device": device_path,
                    "message": "Mock FIDO2 token presence verified and unlock succeeded",
                }
            return {
                "status": "error",
                "unlocked": False,
                "device": device_path,
                "message": "No FIDO2 keyslot enrolled on this mock device",
            }

        # Real test unlock: systemd-cryptsetup / cryptsetup
        cryptsetup_bin = shutil.which("cryptsetup")
        if not cryptsetup_bin:
            return {"status": "error", "unlocked": False, "message": "cryptsetup not found"}

        # Attempt token unlock test
        cmd = [cryptsetup_bin, "open", "--test-passphrase", device_path]
        try:
            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            return {
                "status": "success" if proc.returncode == 0 else "failed",
                "unlocked": proc.returncode == 0,
                "device": device_path,
                "details": {"returncode": proc.returncode, "stderr": proc.stderr},
            }
        except Exception as e:
            return {"status": "error", "unlocked": False, "message": str(e)}

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fido2_enroll.py",
        description="MiOS Portable Drive LUKS2 FIDO2 / CTAP2 Token Enrollment Helper",
    )
    parser.add_argument("--device", "-d", type=str, help="Target partition block device path (e.g. /dev/sdb2)")
    parser.add_argument("--fido2-device", type=str, default="auto", help="FIDO2 device path (e.g. /dev/hidraw0 or auto)")
    parser.add_argument("--require-pin", "--pin", action="store_true", help="Require FIDO2 client PIN for unlocking")
    parser.add_argument("--no-touch", action="store_false", dest="require_touch", default=True, help="Disable user presence requirement")
    parser.add_argument("--require-uv", "--user-verification", action="store_true", help="Require biometric user verification")
    parser.add_argument("--recovery-key", "--recovery", action="store_true", help="Generate an emergency recovery passphrase keyslot")
    parser.add_argument("--wipe-slot", type=str, help="Wipe keyslots matching criteria ('fido2', 'recovery', slot ID, or 'all')")
    parser.add_argument("--list-tokens", action="store_true", help="List all discovered FIDO2 / CTAP2 tokens")
    parser.add_argument("--status", "--check", action="store_true", help="Inspect LUKS2 header and enrolled keyslots")
    parser.add_argument("--test-unlock", action="store_true", help="Test unlocking volume with attached FIDO2 token")
    parser.add_argument("--mock", action="store_true", help="Run hermetically in-memory with synthetic mock hardware fixtures")
    parser.add_argument("--dry-run", action="store_true", help="Simulate execution without modifying block devices")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose diagnostic logs")
    parser.add_argument("--json", action="store_true", help="Output results in structured JSON format")
    return parser

def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    engine = Fido2EnrollEngine(mock=args.mock, dry_run=args.dry_run, verbose=args.verbose)

    # 1. List tokens only
    if args.list_tokens:
        tokens = engine.discover_tokens()
        payload = {
            "status": "ok",
            "token_count": len(tokens),
            "tokens": [t.to_dict() for t in tokens],
        }
        if args.json:
            print(json.dumps(payload, indent=2))
        else:
            print(f"Discovered {len(tokens)} FIDO2/CTAP2 token(s):")
            for t in tokens:
                print(f"  - {t.device_path}: {t.product_name} ({t.manufacturer}) [PIN={t.has_pin}, UP={t.has_up}, UV={t.has_uv}]")
        return 0

    # 2. Status / Check target device
    if args.status:
        if not args.device:
            err = {"status": "error", "message": "--device is required when inspecting status"}
            print(json.dumps(err, indent=2) if args.json else f"Error: {err['message']}", file=sys.stderr)
            return 1
        st = engine.inspect_device(args.device)
        if args.json:
            print(json.dumps(st.to_dict(), indent=2))
        else:
            print(f"LUKS2 Status for {st.device}:")
            print(f"  - Formatted as LUKS2: {st.is_luks2}")
            print(f"  - UUID: {st.uuid}")
            print(f"  - FIDO2 Enrolled: {st.fido2_enrolled}")
            print(f"  - Recovery Enrolled: {st.recovery_enrolled}")
            print(f"  - Active Keyslots: {len(st.keyslots)}")
            for k in st.keyslots:
                print(f"      * Slot {k.slot_id} ({k.slot_type})")
        return 0 if st.status == "ok" else 1

    # 3. Wipe Keyslot
    if args.wipe_slot:
        if not args.device:
            err = {"status": "error", "message": "--device is required when wiping keyslots"}
            print(json.dumps(err, indent=2) if args.json else f"Error: {err['message']}", file=sys.stderr)
            return 1
        res = engine.wipe_keyslot(args.device, args.wipe_slot)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(res.get("message", json.dumps(res)))
        return 0 if res.get("status") == "success" else 1

    # 4. Test Unlock
    if args.test_unlock:
        if not args.device:
            err = {"status": "error", "message": "--device is required for unlock testing"}
            print(json.dumps(err, indent=2) if args.json else f"Error: {err['message']}", file=sys.stderr)
            return 1
        res = engine.test_unlock(args.device, args.fido2_device)
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(res.get("message", json.dumps(res)))
        return 0 if res.get("status") == "success" else 1

    # 5. Default Action: FIDO2 Token Enrollment
    if args.device:
        res = engine.enroll_fido2(
            device_path=args.device,
            fido2_device=args.fido2_device,
            require_pin=args.require_pin,
            require_touch=args.require_touch,
            require_user_verification=args.require_uv,
            recovery_key=args.recovery_key,
        )
        if args.json:
            print(json.dumps(res.to_dict(), indent=2))
        else:
            if res.status == "success":
                print(f"[OK] {res.message}")
                if res.recovery_key:
                    print(f"  Emergency Recovery Key: {res.recovery_key}")
                    print("  * Save this recovery key in a secure off-box location.")
            else:
                print(f"[ERROR] {res.message}", file=sys.stderr)
        return 0 if res.status == "success" else 1

    # If no arguments provided, print help
    parser.print_help()
    return 0

if __name__ == "__main__":
    sys.exit(main())
