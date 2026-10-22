#!/usr/bin/env python3
# AI-hint: Linux kernel lockdown mode probe, Secure Boot status, and module signing integrity auditor.
# AI-related: tests/test-sec.py, usr/share/doc/mios/manual/sec.md
"""
MiOS Linux Kernel Lockdown Mode and Integrity Probe.
Verifies whether the running kernel enforces lockdown [integrity] or [confidentiality],
checks SecureBoot state and module signature enforcement, and serves as a greenboot health check.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, Optional

class LockdownProbe:
    """Probes Linux kernel lockdown state, SecureBoot NVRAM, and module signature enforcement."""

    def __init__(self, mock: bool = False, mock_mode: str = "integrity") -> None:
        self.mock = mock
        self.mock_mode = mock_mode

    def read_lockdown_mode(
        self,
        sys_path: str = "/sys/kernel/security/lockdown",
        mock_content: Optional[str] = None,
    ) -> str:
        """Parses active lockdown mode from sysfs bracket notation (e.g. 'none [integrity] confidentiality')."""
        if self.mock:
            if mock_content:
                text = mock_content
            else:
                text = f"none [{self.mock_mode}] confidentiality" if self.mock_mode != "none" else "[none] integrity confidentiality"
        elif os.path.exists(sys_path):
            with open(sys_path, "r", encoding="utf-8") as f:
                text = f.read().strip()
        else:
            return "unknown"

        match = re.search(r"\[(\w+)\]", text)
        if match:
            return match.group(1).lower()

        # If no brackets found, check plain string
        text_lower = text.lower().strip()
        if text_lower in ("integrity", "confidentiality", "none"):
            return text_lower

        return "unknown"

    def check_secureboot(self, mock_state: Optional[bool] = None) -> bool:
        """Checks if UEFI Secure Boot is enabled on host."""
        if self.mock:
            return True if mock_state is None else mock_state

        # Check /sys/firmware/efi/efivars/SecureBoot-*
        efivars = "/sys/firmware/efi/efivars"
        if os.path.exists(efivars):
            for fname in os.listdir(efivars):
                if fname.startswith("SecureBoot-"):
                    try:
                        with open(os.path.join(efivars, fname), "rb") as f:
                            data = f.read()
                            # 4 bytes attributes + 1 byte state
                            if len(data) >= 5 and data[4] == 1:
                                return True
                    except Exception:
                        pass

        # Check mokutil fallback
        mokutil_bin = shutil.which("mokutil")
        if mokutil_bin:
            proc = subprocess.run([mokutil_bin, "--sb-state"], capture_output=True, text=True)
            if "SecureBoot enabled" in proc.stdout:
                return True

        return False

    def check_module_signing(self, mock_state: Optional[bool] = None) -> bool:
        """Checks if kernel module signature enforcement is active (sig_enforce=1)."""
        if self.mock:
            return True if mock_state is None else mock_state

        sig_param = "/sys/module/module/parameters/sig_enforce"
        if os.path.exists(sig_param):
            try:
                with open(sig_param, "r", encoding="utf-8") as f:
                    return f.read().strip() in ("Y", "1")
            except Exception:
                pass

        cmdline_path = "/proc/cmdline"
        if os.path.exists(cmdline_path):
            try:
                with open(cmdline_path, "r", encoding="utf-8") as f:
                    cmd = f.read()
                    if "module.sig_enforce=1" in cmd:
                        return True
            except Exception:
                pass

        return False

    def evaluate_lockdown_compliance(
        self,
        required_mode: str = "integrity",
        mock_content: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Evaluates whether host lockdown mode satisfies system security policy."""
        mode = self.read_lockdown_mode(mock_content=mock_content)
        sb = self.check_secureboot()
        sig = self.check_module_signing()

        # Integrity hierarchy: confidentiality >= integrity > none
        mode_ranks = {"none": 0, "unknown": 0, "integrity": 1, "confidentiality": 2}
        req_rank = mode_ranks.get(required_mode.lower(), 1)
        cur_rank = mode_ranks.get(mode, 0)

        compliant = cur_rank >= req_rank and mode != "none" and mode != "unknown"

        return {
            "status": "pass" if compliant else "fail",
            "lockdown_mode": mode,
            "required_mode": required_mode,
            "secure_boot": sb,
            "module_sig_enforce": sig,
            "compliant": compliant,
            "mock": self.mock,
        }

def main() -> int:
    parser = argparse.ArgumentParser(description="MiOS Kernel Lockdown Mode & Integrity Probe")
    parser.add_argument("--probe", action="store_true", help="Probe kernel lockdown and security status")
    parser.add_argument("--require-mode", choices=["integrity", "confidentiality"], default="integrity", help="Minimum required lockdown mode (default: integrity)")
    parser.add_argument("--mock", action="store_true", help="Run in mock probe mode")
    parser.add_argument("--mock-mode", choices=["none", "integrity", "confidentiality"], default="integrity", help="Synthetic lockdown mode for mock runs")
    parser.add_argument("--dry-run", action="store_true", help="Dry-run simulation mode")
    parser.add_argument("--json", action="store_true", help="Output JSON dictionary")

    args = parser.parse_args()
    probe = LockdownProbe(mock=args.mock, mock_mode=args.mock_mode)

    try:
        report = probe.evaluate_lockdown_compliance(required_mode=args.require_mode)

        if args.json:
            print(json.dumps(report, indent=2))
        else:
            print(f"[+] Kernel Lockdown Probe: status={report.get('status')} mode={report.get('lockdown_mode')}")
            for k, v in report.items():
                print(f"    {k}: {v}")

        return 0 if report.get("status") == "pass" else 1

    except Exception as exc:
        err = {"status": "error", "error": str(exc), "mock": args.mock}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"[-] Error: {exc}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
