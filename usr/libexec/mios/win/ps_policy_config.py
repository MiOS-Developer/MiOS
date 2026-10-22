#!/usr/bin/env python3
# AI-hint: Windows PowerShell RemoteSigned policy, Developer Mode registry & profile configurator
# AI-related: tests/test-ps-policy-config.py, usr/share/mios/mios.toml, usr/libexec/mios/win/unattend_gen.py
# AI-functions: PowerShellPolicyEngine, PolicyConfig, generate_reg_file, generate_ps1_script
"""
MiOS Windows PowerShell Execution Policy & Developer Mode Registry Configurator.

Configures:
1. PowerShell ExecutionPolicy: RemoteSigned (enforcing security balance without machine-wide Bypass).
2. Windows Developer Mode: AllowDevelopmentWithoutDevLicense = 1.
3. Win32 Long Paths: LongPathsEnabled = 1.
4. PowerShell $PROFILE setup: UTF-8 encoding, MiOS AI endpoint variables, and WSL proxy functions.

Supports emitting standalone .reg files, .ps1 deployment scripts, and live winreg application.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

class ExecutionPolicy(str, Enum):
    REMOTE_SIGNED = "RemoteSigned"
    UNRESTRICTED = "Unrestricted"
    RESTRICTED = "Restricted"
    ALL_SIGNED = "AllSigned"

@dataclass
class PolicyConfig:
    """Settings for Windows developer registry and PowerShell environment."""
    execution_policy: ExecutionPolicy = ExecutionPolicy.REMOTE_SIGNED
    enable_dev_mode: bool = True
    enable_long_paths: bool = True
    setup_profile: bool = True
    ai_endpoint: str = "http://127.0.0.1:8700/v1"
    profile_path: Optional[str] = None

class PowerShellPolicyEngine:
    """Generates registry configurations and PowerShell setup scripts."""

    def __init__(self, config: PolicyConfig, mock: bool = False, dry_run: bool = False):
        self.config = config
        self.mock = mock
        self.dry_run = dry_run

    def generate_reg_content(self) -> str:
        """Generate Windows Registry Editor Version 5.00 format text."""
        # Security Assertion: Never allow global Bypass
        if str(self.config.execution_policy).lower() == "bypass":
            raise ValueError("SECURITY VIOLATION: ExecutionPolicy 'Bypass' cannot be set globally. Use 'RemoteSigned'.")

        reg_lines = [
            "Windows Registry Editor Version 5.00",
            "",
            "; MiOS Windows Developer Environment & PowerShell Security Policy",
            "",
            "[HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\PowerShell\\1\\ShellIds\\Microsoft.PowerShell]",
            f'"ExecutionPolicy"="{self.config.execution_policy.value}"',
            "",
            "[HKEY_LOCAL_MACHINE\\SOFTWARE\\Policies\\Microsoft\\Windows\\PowerShell]",
            f'"ExecutionPolicy"="{self.config.execution_policy.value}"',
            "",
        ]

        if self.config.enable_dev_mode:
            reg_lines.extend([
                "[HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\AppModelUnlock]",
                '"AllowDevelopmentWithoutDevLicense"=dword:00000001',
                "",
            ])

        if self.config.enable_long_paths:
            reg_lines.extend([
                "[HKEY_LOCAL_MACHINE\\SYSTEM\\CurrentControlSet\\Control\\FileSystem]",
                '"LongPathsEnabled"=dword:00000001',
                "",
            ])

        return "\r\n".join(reg_lines)

    def generate_ps1_content(self) -> str:
        """Generate standalone PowerShell deployment script."""
        if str(self.config.execution_policy).lower() == "bypass":
            raise ValueError("SECURITY VIOLATION: ExecutionPolicy 'Bypass' cannot be set globally.")

        ps1_lines = [
            "# MiOS Windows Environment Setup Script",
            "# Enforces RemoteSigned policy, Developer Mode, and Long Paths",
            "$ErrorActionPreference = 'Stop'",
            "",
            f"# 1. Set Execution Policy to {self.config.execution_policy.value}",
            f"Set-ExecutionPolicy -Scope LocalMachine -ExecutionPolicy {self.config.execution_policy.value} -Force",
            "",
        ]

        if self.config.enable_dev_mode:
            ps1_lines.extend([
                "# 2. Enable Windows Developer Mode",
                "New-Item -Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\AppModelUnlock' -Force | Out-Null",
                "Set-ItemProperty -Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\AppModelUnlock' -Name 'AllowDevelopmentWithoutDevLicense' -Value 1 -Type DWord",
                "",
            ])

        if self.config.enable_long_paths:
            ps1_lines.extend([
                "# 3. Enable Long Paths",
                "Set-ItemProperty -Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\FileSystem' -Name 'LongPathsEnabled' -Value 1 -Type DWord",
                "",
            ])

        if self.config.setup_profile:
            ps1_lines.extend([
                "# 4. Initialize CurrentUserCurrentHost PowerShell Profile",
                "if (!(Test-Path -Path $PROFILE)) {",
                "    New-Item -ItemType File -Path $PROFILE -Force | Out-Null",
                "}",
                "$profileAdditions = @'",
                "# --- MiOS Environment Profile Hook ---",
                "$OutputEncoding = [System.Text.Encoding]::UTF8",
                "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
                "[Console]::InputEncoding = [System.Text.Encoding]::UTF8",
                f"$env:MIOS_AI_ENDPOINT = '{self.config.ai_endpoint}'",
                "function mios { & wsl.exe -d MiOS-DEV -- mios $args }",
                "# -------------------------------------",
                "'@",
                "Add-Content -Path $PROFILE -Value $profileAdditions",
                "",
            ])

        return "\r\n".join(ps1_lines)

    def generate_profile_script(self) -> str:
        """Generate content for Microsoft.PowerShell_profile.ps1."""
        return (
            "# MiOS Windows PowerShell Operator Profile\n"
            "$OutputEncoding = [System.Text.Encoding]::UTF8\n"
            "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
            "[Console]::InputEncoding = [System.Text.Encoding]::UTF8\n"
            f"$env:MIOS_AI_ENDPOINT = '{self.config.ai_endpoint}'\n"
            "function mios { & wsl.exe -d MiOS-DEV -- mios $args }\n"
        )

    def apply_registry_live(self) -> List[str]:
        """Apply registry entries directly via winreg (Windows only)."""
        actions: List[str] = []
        if sys.platform == "win32" and not self.mock and not self.dry_run:
            import winreg

            # 1. ExecutionPolicy
            try:
                with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\PowerShell\1\ShellIds\Microsoft.PowerShell") as k:
                    winreg.SetValueEx(k, "ExecutionPolicy", 0, winreg.REG_SZ, self.config.execution_policy.value)
                actions.append(f"Set HKLM\\...\\Microsoft.PowerShell:ExecutionPolicy = {self.config.execution_policy.value}")
            except Exception as e:
                actions.append(f"Failed to set ExecutionPolicy (requires admin): {e}")

            # 2. Developer Mode
            if self.config.enable_dev_mode:
                try:
                    with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\AppModelUnlock") as k:
                        winreg.SetValueEx(k, "AllowDevelopmentWithoutDevLicense", 0, winreg.REG_DWORD, 1)
                    actions.append("Set HKLM\\...\\AppModelUnlock:AllowDevelopmentWithoutDevLicense = 1")
                except Exception as e:
                    actions.append(f"Failed to set DeveloperMode: {e}")

            # 3. Long Paths
            if self.config.enable_long_paths:
                try:
                    with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\FileSystem") as k:
                        winreg.SetValueEx(k, "LongPathsEnabled", 0, winreg.REG_DWORD, 1)
                    actions.append("Set HKLM\\...\\FileSystem:LongPathsEnabled = 1")
                except Exception as e:
                    actions.append(f"Failed to set LongPathsEnabled: {e}")
        else:
            actions.append(f"[mock/simulated] Set ExecutionPolicy = {self.config.execution_policy.value}")
            if self.config.enable_dev_mode:
                actions.append("[mock/simulated] Set DeveloperMode = 1")
            if self.config.enable_long_paths:
                actions.append("[mock/simulated] Set LongPathsEnabled = 1")

        return actions

    def run(self, emit_reg: bool = False, emit_ps1: bool = False, apply: bool = False, output_path: Optional[str] = None) -> Dict[str, Any]:
        """Execute policy generation or application."""
        reg_text = self.generate_reg_content()
        ps1_text = self.generate_ps1_content()
        actions: List[str] = []

        if apply or (not emit_reg and not emit_ps1):
            actions = self.apply_registry_live()

        if output_path and not self.mock and not self.dry_run:
            parent = os.path.dirname(output_path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            content_to_write = ps1_text if output_path.endswith(".ps1") else reg_text
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(content_to_write)

        return {
            "status": "success",
            "policy": self.config.execution_policy.value,
            "developer_mode": self.config.enable_dev_mode,
            "long_paths": self.config.enable_long_paths,
            "ai_endpoint": self.config.ai_endpoint,
            "actions": actions,
            "reg_content_preview": reg_text[:200] + "...",
            "ps1_content_preview": ps1_text[:200] + "...",
            "output_path": output_path,
            "dry_run": self.dry_run,
            "mock": self.mock,
        }

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS Windows PowerShell Execution Policy & Developer Mode Configurator"
    )
    parser.add_argument("--policy", choices=["RemoteSigned", "Unrestricted", "Restricted", "AllSigned"], default="RemoteSigned", help="PowerShell execution policy")
    parser.add_argument("--disable-dev-mode", action="store_true", help="Do not enable Developer Mode")
    parser.add_argument("--disable-long-paths", action="store_true", help="Do not enable Long Paths")
    parser.add_argument("--ai-endpoint", default="http://127.0.0.1:8700/v1", help="Canonical MiOS AI endpoint")
    parser.add_argument("--emit-reg", action="store_true", help="Emit Windows .reg registry export")
    parser.add_argument("--emit-ps1", action="store_true", help="Emit PowerShell deployment script")
    parser.add_argument("--apply", action="store_true", help="Apply settings directly to Windows Registry")
    parser.add_argument("--output", help="Target output file path for .reg or .ps1")
    parser.add_argument("--dry-run", action="store_true", help="Simulate configuration without writing")
    parser.add_argument("--mock", action="store_true", help="Run deterministic mock execution for CI testing")
    parser.add_argument("--json", action="store_true", help="Format output as JSON dictionary")

    args = parser.parse_args()

    cfg = PolicyConfig(
        execution_policy=ExecutionPolicy(args.policy),
        enable_dev_mode=not args.disable_dev_mode,
        enable_long_paths=not args.disable_long_paths,
        ai_endpoint=args.ai_endpoint,
    )

    engine = PowerShellPolicyEngine(cfg, mock=args.mock, dry_run=args.dry_run)

    try:
        if args.emit_reg and not args.output and not args.json:
            print(engine.generate_reg_content())
            return 0
        if args.emit_ps1 and not args.output and not args.json:
            print(engine.generate_ps1_content())
            return 0

        res = engine.run(
            emit_reg=args.emit_reg,
            emit_ps1=args.emit_ps1,
            apply=args.apply,
            output_path=args.output,
        )
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"[ps_policy_config] SUCCESS: Configured policy={res['policy']}, DevMode={res['developer_mode']}, LongPaths={res['long_paths']}")
            for a in res["actions"]:
                print(f"  - {a}")
            if args.output:
                print(f"  Output saved to: {args.output}")
        return 0
    except Exception as e:
        err = {"status": "error", "error": str(e)}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"[ps_policy_config] ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
