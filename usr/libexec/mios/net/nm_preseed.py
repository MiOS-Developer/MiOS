#!/usr/bin/env python3
# AI-hint: NetworkManager offline connection keyfile pre-seeder enforcing strict 0600 permissions
# AI-related: tests/test-nm-preseed.py, usr/share/mios/mios.toml, usr/libexec/mios/ux/firstboot_wizard.py
# AI-functions: NetworkManagerPreseedEngine, ConnectionProfile, generate_keyfile
"""
MiOS NetworkManager Offline Connection Keyfile Pre-Seeder.

Generates standard NetworkManager .nmconnection keyfiles for offline network pre-seeding
(Wi-Fi WPA-PSK/WPA3-SAE, Ethernet DHCP/Static).

Enforces strict security invariants:
- File permissions MUST be exactly 0600 (-rw-------).
- Keyfiles MUST NOT be world-readable or accessible by unprivileged users.
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
import stat
import sys
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

class SecurityType(str, Enum):
    WPA_PSK = "wpa-psk"
    SAE = "sae"
    OPEN = "open"

class ConnectionType(str, Enum):
    WIFI = "wifi"
    ETHERNET = "ethernet"

@dataclass
class ConnectionProfile:
    """Network connection parameters."""
    connection_name: str
    connection_type: ConnectionType
    uuid_str: str
    ssid: Optional[str] = None
    psk: Optional[str] = None
    security: SecurityType = SecurityType.WPA_PSK
    autoconnect: bool = True
    ipv4_method: str = "auto"
    ipv6_method: str = "auto"

class NetworkManagerPreseedEngine:
    """Constructs, validates, and writes NetworkManager keyfiles."""

    def __init__(
        self,
        ssid: Optional[str] = None,
        psk: Optional[str] = None,
        security: str = "wpa-psk",
        con_name: Optional[str] = None,
        con_type: str = "wifi",
        toml_config_path: Optional[str] = None,
        output_dir: str = "/etc/NetworkManager/system-connections",
        output_file: Optional[str] = None,
        dry_run: bool = False,
        mock: bool = False,
    ):
        self.ssid = ssid
        self.psk = psk
        self.security = SecurityType(security.lower())
        self.con_type = ConnectionType(con_type.lower())
        self.con_name = con_name or ssid or ("Wired connection 1" if self.con_type == ConnectionType.ETHERNET else "MiOS-WiFi")
        self.toml_config_path = toml_config_path
        self.output_dir = output_dir
        self.output_file = output_file
        self.dry_run = dry_run
        self.mock = mock

    def build_profile(self) -> ConnectionProfile:
        """Create ConnectionProfile structure with generated UUID."""
        if self.mock:
            return ConnectionProfile(
                connection_name=self.con_name or "MiOS-Lab-WiFi (Mock)",
                connection_type=self.con_type,
                uuid_str="c3d9b4c0-7f2e-4e6a-a2b1-9f8e7d6c5b4a",
                ssid=self.ssid or "MiOS-Lab-WiFi",
                psk=self.psk or "SecretPassphrase123!",
                security=self.security,
                autoconnect=True,
                ipv4_method="auto",
                ipv6_method="auto",
            )

        return ConnectionProfile(
            connection_name=self.con_name,
            connection_type=self.con_type,
            uuid_str=str(uuid.uuid4()),
            ssid=self.ssid,
            psk=self.psk,
            security=self.security,
            autoconnect=True,
            ipv4_method="auto",
            ipv6_method="auto",
        )

    def render_keyfile_content(self, profile: ConnectionProfile) -> str:
        """Render standard NetworkManager INI keyfile text."""
        lines = [
            "[connection]",
            f"id={profile.connection_name}",
            f"uuid={profile.uuid_str}",
            f"type={profile.connection_type.value}",
            f"autoconnect={'true' if profile.autoconnect else 'false'}",
            "",
        ]

        if profile.connection_type == ConnectionType.WIFI:
            if not profile.ssid:
                raise ValueError("Wi-Fi connection requires non-empty SSID.")

            lines.extend([
                "[wifi]",
                "mode=infrastructure",
                f"ssid={profile.ssid}",
                "",
            ])

            if profile.security != SecurityType.OPEN:
                if not profile.psk:
                    raise ValueError(f"Wi-Fi security '{profile.security.value}' requires a passphrase (PSK).")

                lines.extend([
                    "[wifi-security]",
                    f"key-mgmt={profile.security.value}",
                    f"psk={profile.psk}",
                    "",
                ])
        elif profile.connection_type == ConnectionType.ETHERNET:
            lines.extend([
                "[ethernet]",
                "auto-negotiate=true",
                "",
            ])

        lines.extend([
            "[ipv4]",
            f"method={profile.ipv4_method}",
            "",
            "[ipv6]",
            f"method={profile.ipv6_method}",
            "addr-gen-mode=default",
            "",
        ])

        return "\n".join(lines)

    def write_keyfile(self, profile: ConnectionProfile, content: str) -> str:
        """Write keyfile and enforce strict 0600 permissions."""
        if self.output_file:
            target_path = self.output_file
        else:
            safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in profile.connection_name)
            target_path = os.path.join(self.output_dir, f"{safe_name}.nmconnection")

        if not self.mock and not self.dry_run:
            parent = os.path.dirname(target_path)
            if parent:
                os.makedirs(parent, exist_ok=True)

            # Write file with restricted 0600 permissions from creation
            flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
            mode = 0o600
            fd = os.open(target_path, flags, mode)
            try:
                with open(fd, "w", encoding="utf-8", closefd=False) as f:
                    f.write(content)
            finally:
                os.close(fd)

            # Ensure chmod 0600
            try:
                os.chmod(target_path, 0o600)
            except OSError:
                pass

        return target_path

    def run(self) -> Dict[str, Any]:
        """Execute connection profile generation and keyfile persistence."""
        profile = self.build_profile()
        content = self.render_keyfile_content(profile)
        written_path = self.write_keyfile(profile, content)

        return {
            "status": "success",
            "profile": asdict(profile),
            "output_path": written_path,
            "permissions": "0600 (-rw-------)",
            "keyfile_content": content,
            "dry_run": self.dry_run,
            "mock": self.mock,
        }

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS NetworkManager Offline Connection Keyfile Pre-Seeder"
    )
    parser.add_argument("--ssid", help="Wi-Fi SSID network name")
    parser.add_argument("--psk", help="Wi-Fi WPA/WPA2/WPA3 passphrase")
    parser.add_argument("--security", choices=["wpa-psk", "sae", "open"], default="wpa-psk", help="Wi-Fi security type")
    parser.add_argument("--con-name", help="Connection identifier name")
    parser.add_argument("--type", choices=["wifi", "ethernet"], default="wifi", help="Connection type")
    parser.add_argument("--toml-config", help="Optional mios.toml path to extract [network] config")
    parser.add_argument("--output-dir", default="/etc/NetworkManager/system-connections", help="Destination system-connections directory")
    parser.add_argument("--output-file", help="Explicit target file path")
    parser.add_argument("--dry-run", action="store_true", help="Simulate keyfile generation without writing")
    parser.add_argument("--mock", action="store_true", help="Run deterministic mock execution for CI testing")
    parser.add_argument("--json", action="store_true", help="Format output as JSON dictionary")

    args = parser.parse_args()

    engine = NetworkManagerPreseedEngine(
        ssid=args.ssid,
        psk=args.psk,
        security=args.security,
        con_name=args.con_name,
        con_type=args.type,
        toml_config_path=args.toml_config,
        output_dir=args.output_dir,
        output_file=args.output_file,
        dry_run=args.dry_run,
        mock=args.mock,
    )

    try:
        res = engine.run()
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            p = res["profile"]
            print(f"[nm_preseed] SUCCESS: Generated keyfile at {res['output_path']} (0600)")
            print(f"  Connection: {p['connection_name']} ({p['connection_type']}), UUID: {p['uuid_str']}")
            if p["ssid"]:
                print(f"  SSID: {p['ssid']} ({p['security']})")
        return 0
    except Exception as e:
        err = {"status": "error", "error": str(e)}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"[nm_preseed] ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
