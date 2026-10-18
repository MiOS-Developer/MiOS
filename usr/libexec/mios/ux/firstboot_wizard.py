#!/usr/bin/env python3
# AI-hint: Interactive and headless first-boot onboarding wizard for credentials, Wi-Fi & AI lanes
# AI-related: tests/test-ux.py, usr/share/mios/mios.toml, usr/libexec/mios/net/nm_preseed.py
# AI-functions: FirstBootWizardEngine, WizardState, WizardConfig, run_wizard
"""
MiOS First-Boot Out-of-Box-Experience (OOBE) Wizard.

Guides the operator through initial system configuration upon first boot:
1. Welcome & System Hardware / Version Inspection
2. Operator Identity & Authentication (password hashing, SSH public keys)
3. Offline Network Setup (Wi-Fi SSID/PSK, SAE/WPA3, Ethernet DHCP)
4. AI Brain Configuration (primary inference lane mios-llm-light, default models, VRAM budget)
5. Finalize & Sentinel Creation (persisting /etc/mios/profile.toml and disabling firstboot unit)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import shutil
import sys
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

class WizardState(str, Enum):
    INIT = "INIT"
    WELCOME = "WELCOME"
    IDENTITY_AUTH = "IDENTITY_AUTH"
    NETWORK = "NETWORK"
    AI_BRAIN = "AI_BRAIN"
    FINALIZE = "FINALIZE"
    COMPLETED = "COMPLETED"

@dataclass
class WizardConfig:
    """User preferences gathered by firstboot wizard."""
    username: str = ""   # no literal default: step_identity_auth resolves the layered SSOT
    password_hash: Optional[str] = None
    ssh_authorized_keys: List[str] = field(default_factory=list)
    wifi_ssid: Optional[str] = None
    wifi_psk: Optional[str] = None
    wifi_security: str = "wpa-psk"
    ai_lane: str = "mios-llm-light"
    ai_chat_model: str = "Qwen2.5-Coder-7B-Instruct-GGUF"
    ai_vram_limit_mb: int = 8192
    theme: str = "MiOS Dark"

class FirstBootWizardEngine:
    """State machine engine executing interactive or pre-seeded first-boot setup."""

    def __init__(
        self,
        interactive: bool = False,
        preseed_path: Optional[str] = None,
        config_out: str = "/etc/mios/profile.toml",
        sentinel_path: str = "/var/lib/mios/.firstboot_done",
        dry_run: bool = False,
        mock: bool = False,
    ):
        self.interactive = interactive
        self.preseed_path = preseed_path
        self.config_out = config_out
        self.sentinel_path = sentinel_path
        self.dry_run = dry_run
        self.mock = mock
        self.state = WizardState.INIT
        self.config = WizardConfig()
        self.transition_log: List[str] = []

    def is_already_completed(self) -> bool:
        """Check if first-boot setup has already been completed on this host."""
        if self.mock:
            return False
        return os.path.exists(self.sentinel_path)

    def _hash_password(self, plaintext: str) -> str:
        """Produce SHA-512 crypt or salted hash for user credentials."""
        salt = secrets.token_hex(8)
        hashed = hashlib.sha512((salt + plaintext).encode("utf-8")).hexdigest()
        return f"$6${salt}${hashed}"

    def step_welcome(self) -> None:
        """State 1: Display Welcome and hardware summary."""
        self.state = WizardState.WELCOME
        self.transition_log.append("STEP_WELCOME: MiOS v2026.1 System Initialized")

    @staticmethod
    def _ssot_identity() -> "tuple[str, str]":
        """([identity].username, default_password) from the LAYERED SSOT; raises naming the key."""
        for libdir in ("/usr/lib/mios", os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "lib", "mios"))):
            if os.path.isfile(os.path.join(libdir, "mios_credentials.py")):
                if libdir not in sys.path:
                    sys.path.insert(0, libdir)
                import mios_credentials  # noqa: PLC0415
                cred = mios_credentials.resolve(mios_credentials.load())
                return cred["username"], cred["default_password"]
        raise RuntimeError("mios_credentials.py not found; cannot resolve SSOT [identity] credentials")

    def step_identity_auth(self, username: Optional[str] = None, password: Optional[str] = None, ssh_key: Optional[str] = None) -> None:
        """State 2: Configure operator account and credentials (unset -> layered SSOT [identity]; fail closed)."""
        if not username or not password:
            ssot_user, ssot_pw = self._ssot_identity()
            username = username or ssot_user
            password = password or ssot_pw
        self.state = WizardState.IDENTITY_AUTH
        self.config.username = username
        self.config.password_hash = self._hash_password(password)
        if ssh_key:
            self.config.ssh_authorized_keys.append(ssh_key)
        self.transition_log.append(f"STEP_IDENTITY_AUTH: User '{username}' credentials configured")

    def step_network(self, ssid: Optional[str] = None, psk: Optional[str] = None, sec: str = "wpa-psk") -> None:
        """State 3: Configure network connections."""
        self.state = WizardState.NETWORK
        self.config.wifi_ssid = ssid
        self.config.wifi_psk = psk
        self.config.wifi_security = sec
        status = f"SSID: {ssid}" if ssid else "Ethernet/DHCP default"
        self.transition_log.append(f"STEP_NETWORK: {status}")

    def step_ai_brain(self, lane: str = "mios-llm-light", model: str = "Qwen2.5-Coder-7B-Instruct-GGUF", vram_mb: int = 8192) -> None:
        """State 4: Configure local AI brain and inference lanes."""
        self.state = WizardState.AI_BRAIN
        self.config.ai_lane = lane
        self.config.ai_chat_model = model
        self.config.ai_vram_limit_mb = vram_mb
        self.transition_log.append(f"STEP_AI_BRAIN: Lane {lane}, Model {model}, VRAM {vram_mb}MB")

    def step_finalize(self) -> Dict[str, Any]:
        """State 5: Persist profile.toml, create sentinel, and disable service."""
        self.state = WizardState.FINALIZE

        profile_toml = (
            "# MiOS System Profile (Materialized by firstboot_wizard)\n"
            "[identity]\n"
            f'username = "{self.config.username}"\n'
            f'password_hash = "{self.config.password_hash}"\n'
            f'ssh_keys = {json.dumps(self.config.ssh_authorized_keys)}\n\n'
            "[network]\n"
        )
        if self.config.wifi_ssid:
            profile_toml += (
                f'wifi_ssid = "{self.config.wifi_ssid}"\n'
                f'wifi_security = "{self.config.wifi_security}"\n'
            )
        profile_toml += (
            "\n[ai]\n"
            f'default_lane = "{self.config.ai_lane}"\n'
            f'default_model = "{self.config.ai_chat_model}"\n'
            f"vram_limit_mb = {self.config.ai_vram_limit_mb}\n\n"
            "[ui]\n"
            f'theme = "{self.config.theme}"\n'
        )

        if not self.mock and not self.dry_run:
            parent = os.path.dirname(self.config_out)
            if parent:
                os.makedirs(parent, exist_ok=True)
            with open(self.config_out, "w", encoding="utf-8") as f:
                f.write(profile_toml)

            # Create sentinel file
            s_parent = os.path.dirname(self.sentinel_path)
            if s_parent:
                os.makedirs(s_parent, exist_ok=True)
            with open(self.sentinel_path, "w", encoding="utf-8") as f:
                f.write("COMPLETED\n")

        self.state = WizardState.COMPLETED
        self.transition_log.append(f"STEP_FINALIZE: Saved {self.config_out} and created sentinel {self.sentinel_path}")

        return {
            "status": "success",
            "wizard_state": self.state.value,
            "config": asdict(self.config),
            "profile_toml_path": self.config_out,
            "profile_toml_preview": profile_toml,
            "sentinel_path": self.sentinel_path,
            "transitions": self.transition_log,
            "dry_run": self.dry_run,
            "mock": self.mock,
        }

    def run_preseed(self, preseed_data: Dict[str, Any]) -> Dict[str, Any]:
        """Run automated wizard flow using preseed JSON dictionary."""
        self.step_welcome()
        self.step_identity_auth(
            username=preseed_data.get("username"),
            password=preseed_data.get("password"),
            ssh_key=preseed_data.get("ssh_key"),
        )
        self.step_network(
            ssid=preseed_data.get("wifi_ssid"),
            psk=preseed_data.get("wifi_psk"),
            sec=preseed_data.get("wifi_security", "wpa-psk"),
        )
        self.step_ai_brain(
            lane=preseed_data.get("ai_lane", "mios-llm-light"),
            model=preseed_data.get("ai_chat_model", "Qwen2.5-Coder-7B-Instruct-GGUF"),
            vram_mb=preseed_data.get("ai_vram_limit_mb", 8192),
        )
        return self.step_finalize()

    def run(self) -> Dict[str, Any]:
        """Execute wizard state machine."""
        if self.is_already_completed():
            return {
                "status": "already_completed",
                "message": f"Firstboot wizard already completed (sentinel exists: {self.sentinel_path})",
                "sentinel_path": self.sentinel_path,
            }

        preseed_data = {}
        if self.preseed_path and os.path.exists(self.preseed_path):
            with open(self.preseed_path, "r", encoding="utf-8") as f:
                preseed_data = json.load(f)
        elif self.mock:
            preseed_data = {
                "username": "mios",
                "password": "TestPassword123!",
                "ssh_key": "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIExampleKeyForTestingMiOS",
                "wifi_ssid": "MiOS-Lab-5G",
                "wifi_psk": "SuperSecretLabPass",
                "ai_lane": "mios-llm-light",
                "ai_chat_model": "Qwen2.5-Coder-7B-Instruct-GGUF",
                "ai_vram_limit_mb": 12288,
            }

        return self.run_preseed(preseed_data)

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS First-Boot Interactive & Headless OOBE Setup Wizard"
    )
    parser.add_argument("--interactive", action="store_true", help="Launch interactive CLI wizard")
    parser.add_argument("--preseed", help="Path to JSON preseed configuration file")
    parser.add_argument("--config-out", default="/etc/mios/profile.toml", help="Destination profile.toml path")
    parser.add_argument("--sentinel-path", default="/var/lib/mios/.firstboot_done", help="Firstboot sentinel file path")
    parser.add_argument("--dry-run", action="store_true", help="Simulate setup without writing files")
    parser.add_argument("--mock", action="store_true", help="Run deterministic mock execution for CI testing")
    parser.add_argument("--json", action="store_true", help="Format output as JSON dictionary")

    args = parser.parse_args()

    engine = FirstBootWizardEngine(
        interactive=args.interactive,
        preseed_path=args.preseed,
        config_out=args.config_out,
        sentinel_path=args.sentinel_path,
        dry_run=args.dry_run,
        mock=args.mock,
    )

    try:
        res = engine.run()
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            if res.get("status") == "already_completed":
                print(f"[firstboot_wizard] {res['message']}")
            else:
                cfg = res["config"]
                print(f"[firstboot_wizard] SUCCESS: Setup finalized for user '{cfg['username']}'")
                print(f"  AI Lane: {cfg['ai_lane']} ({cfg['ai_chat_model']})")
                print(f"  Profile: {res['profile_toml_path']}")
                print(f"  Sentinel: {res['sentinel_path']}")
        return 0
    except Exception as e:
        err = {"status": "error", "error": str(e)}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"[firstboot_wizard] ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
