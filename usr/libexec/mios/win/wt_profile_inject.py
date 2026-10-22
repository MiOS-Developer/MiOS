#!/usr/bin/env python3
# AI-hint: Windows Terminal settings.json profile injector with MiOS tabs & color palette
# AI-related: tests/test-wt-profile-inject.py, usr/share/mios/mios.toml, usr/libexec/mios/win/unattend_gen.py, usr/lib/mios/mios_toml.py, usr/share/mios/wsl/terminal-profile.json, etc/wsl-distribution.conf
# AI-functions: WindowsTerminalProfileInjector, ProfileConfig, ColorScheme, inject_wt_profiles, wt_edge, fixture_render
"""
MiOS Windows Terminal Profile & Color Scheme Injector.

Non-destructively updates Windows Terminal settings.json with MiOS development profiles
(WSL2 Dev container, Host SSH loopback, Serial Console) and canonical 'MiOS Dark'
color schemes extracted from mios.toml [colors].

Preserves existing user profiles, custom keybindings, and global terminal preferences.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

_TREE = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", ".."))
if os.path.join(_TREE, "usr", "lib", "mios") not in sys.path:
    sys.path.insert(0, os.path.join(_TREE, "usr", "lib", "mios"))
import mios_toml  # noqa: E402 -- required: padding/scrollbarState resolve through edge_insets()

# WSL's [windowsterminal].profileTemplate (etc/wsl-distribution.conf); rendered at bake by automation/65-bake-hyprland.sh
GOLDEN = "usr/share/mios/wsl/terminal-profile.json"
WSL_GUID = "{a4b89f81-9b1c-4e8a-b86a-6b45a98d0001}"
SSH_GUID = "{a4b89f81-9b1c-4e8a-b86a-6b45a98d0002}"
SERIAL_GUID = "{a4b89f81-9b1c-4e8a-b86a-6b45a98d0003}"

def color_scheme(data):
    """Windows Terminal's exact schema, projected from the layered palette."""
    palette = mios_toml.colors(data)
    scheme = {"name": data["theme"]["terminal"]["scheme_name"],
              "background": palette["bg"], "foreground": palette["fg"],
              "cursorColor": palette["cursor"], "selectionBackground": palette["muted"]}
    for index, color in enumerate(("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white")):
        field = "purple" if color == "magenta" else color
        scheme[field] = palette[f"ansi_{index}_{color}"]
        scheme["bright" + field.title()] = palette[f"ansi_{index + 8}_bright_{color}"]
    for key, value in scheme.items():
        if key != "name" and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError(f"SSOT Windows Terminal color {key} is invalid")
    return scheme

def wt_edge(data: Dict[str, Any]) -> Tuple[str, str]:
    """(padding, scrollbarState) in WT profile grammar, normalised through edge_insets()."""
    ins = mios_toml.edge_insets(data)
    l, t, r, b = ins["left"], ins["top"], ins["right"], ins["bottom"]
    if l == t == r == b:
        padding = f"{l}"
    elif l == r and t == b:
        padding = f"{l}, {t}"
    else:
        padding = f"{l}, {t}, {r}, {b}"
    return padding, str(mios_toml.section(data, "theme")["scrollbar_state"])

@dataclass
class TerminalProfile:
    """Windows Terminal profile entry definition."""
    guid: str
    name: str
    commandline: str
    colorScheme: Optional[str] = None
    startingDirectory: Optional[str] = None
    icon: Optional[str] = None
    hidden: bool = False
    padding: Optional[str] = None
    scrollbarState: Optional[str] = None
    useAcrylic: Optional[bool] = None
    opacity: Optional[int] = None
    unfocusedAppearance: Optional[Dict[str, Any]] = None
    systemBackdrop: Optional[str] = None
    font: Optional[Dict[str, Any]] = None

class WindowsTerminalProfileInjector:
    """Non-destructive modifier for Windows Terminal settings.json."""

    def __init__(
        self,
        settings_path: Optional[str] = None,
        ssh_port: int = 2222,
        ssh_user: str = "mios",
        toml_config_path: Optional[str] = None,
        set_default: bool = False,
        dry_run: bool = False,
        mock: bool = False,
        data: Optional[Dict[str, Any]] = None,
    ):
        if data is None:
            extra = [toml_config_path] if toml_config_path else []  # tier-major overlay (Law 13), no DB/native resolver
            data = mios_toml.load_merged(mios_toml.layer_paths() + extra)
        self.padding, self.scrollbar_state = wt_edge(data)
        self.color_scheme = color_scheme(data)
        self.data = data
        theme = mios_toml.section(data, "theme")
        self.use_acrylic = bool(theme.get("acrylic", True))
        self.opacity = int(theme.get("opacity", 50))
        self.unfocused_appearance = {"opacity": theme["unfocused_opacity"],
                                     "useAcrylic": theme["unfocused_acrylic"]}
        for value in (self.opacity, self.unfocused_appearance["opacity"]):
            if type(value) is not int or not 0 <= value <= 100:
                raise ValueError("[theme] opacity must be an integer from 0 to 100")
        self.system_backdrop = str(theme.get("system_backdrop", "acrylic"))
        font_cfg = mios_toml.section(data, "theme.font")
        self.font = {
            "face": str(font_cfg.get("family", "GeistMono Nerd Font Mono")),
            "size": int(font_cfg.get("size", 12)),
            "weight": str(font_cfg.get("weight", "normal")),
        }
        self.settings_path = settings_path
        self.ssh_port = ssh_port
        self.ssh_user = ssh_user
        self.toml_config_path = toml_config_path
        self.set_default = set_default
        self.dry_run = dry_run
        self.mock = mock

    def locate_settings_file(self) -> str:
        """Find the active Windows Terminal settings.json path."""
        if self.settings_path:
            return self.settings_path

        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if local_app_data:
            # Standard MS Store package path
            p1 = os.path.join(
                local_app_data,
                "Packages",
                "Microsoft.WindowsTerminal_8wekyb3d8bbwe",
                "LocalState",
                "settings.json",
            )
            if os.path.exists(p1):
                return p1

            # Standard MSIX / unpackaged path
            p2 = os.path.join(local_app_data, "Microsoft", "Windows Terminal", "settings.json")
            if os.path.exists(p2):
                return p2

        # Fallback default scratch path
        return "C:\\mios\\scratch\\settings.json"

    def _strip_comments(self, json_str: str) -> str:
        """Strip JavaScript/JSONC comments (// and /* */) for standard json parser."""
        json_str = re.sub(r"/\*.*?\*/", "", json_str, flags=re.DOTALL)
        lines = []
        for line in json_str.splitlines():
            # Strip trailing comment if not in string
            stripped = re.sub(r"(?<!:)//.*$", "", line)
            lines.append(stripped)
        return "\n".join(lines)

    def load_settings(self, path: str) -> Dict[str, Any]:
        """Read and parse existing settings.json or generate baseline template."""
        if self.mock:
            return {
                "$schema": "https://aka.ms/terminal-profiles-schema",
                "defaultProfile": "{61c54bbd-c2c6-5271-96e7-009a87ff44bf}",
                "profiles": {
                    "defaults": {},
                    "list": [
                        {
                            "guid": "{61c54bbd-c2c6-5271-96e7-009a87ff44bf}",
                            "name": "Windows PowerShell",
                            "commandline": "powershell.exe",
                            "hidden": False,
                        },
                        {
                            "guid": "{0caa0dad-35be-5f56-a8ff-afceeeaa6101}",
                            "name": "Command Prompt",
                            "commandline": "cmd.exe",
                            "hidden": False,
                        },
                    ],
                },
                "schemes": [
                    {
                        "name": "Campbell",
                        "background": "#0C0C0C",
                        "foreground": "#CCCCCC",
                    }
                ],
            }

        if os.path.exists(path):
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read()
            clean = self._strip_comments(content)
            try:
                return json.loads(clean)
            except Exception:
                pass

        # Return baseline skeleton
        return {
            "$schema": "https://aka.ms/terminal-profiles-schema",
            "profiles": {
                "defaults": {},
                "list": [],
            },
            "schemes": [],
        }

    def build_mios_profiles(self) -> List[TerminalProfile]:
        """Construct the trio of MiOS profiles, each carrying the SSOT padding and scrollbarState."""
        profiles = [
            TerminalProfile(
                guid=WSL_GUID,
                name="MiOS WSL (Development)",
                commandline="mios.cmd terminal",
                colorScheme=self.color_scheme["name"],
                hidden=False,
            ),
            TerminalProfile(
                guid=SSH_GUID,
                name="MiOS Host SSH",
                commandline=f"ssh -p {self.ssh_port} {self.ssh_user}@127.0.0.1",
                colorScheme=self.color_scheme["name"],
                hidden=False,
            ),
            TerminalProfile(
                guid=SERIAL_GUID,
                name="MiOS Serial Console",
                commandline="powershell.exe -NoExit -Command \"Write-Host 'Connecting to MiOS Serial Console...'; plink.exe -serial COM1 -sercfg 115200,8,n,1,N\"",
                colorScheme=self.color_scheme["name"],
                hidden=False,
            ),
        ]
        for p in profiles:
            p.font = dict(self.font)
            p.padding, p.scrollbarState = self.padding, self.scrollbar_state
            p.useAcrylic = self.use_acrylic
            p.opacity = self.opacity
            p.unfocusedAppearance = dict(self.unfocused_appearance)
            p.systemBackdrop = self.system_backdrop
        return profiles

    def merge_profiles(self, settings: Dict[str, Any], profiles: List[TerminalProfile]) -> Tuple[int, int]:
        """Merge MiOS profiles into settings.json profiles list without deleting existing items."""
        # Check profile list format (can be settings["profiles"]["list"] or settings["profiles"])
        if "profiles" not in settings or not isinstance(settings["profiles"], dict):
            settings["profiles"] = {"defaults": {}, "list": []}

        if "list" not in settings["profiles"] or not isinstance(settings["profiles"]["list"], list):
            settings["profiles"]["list"] = []

        target_list: List[Dict[str, Any]] = settings["profiles"]["list"]
        added = 0
        updated = 0

        for p in profiles:
            p_dict = {
                "guid": p.guid,
                "name": p.name,
                "commandline": p.commandline,
                "colorScheme": p.colorScheme or self.color_scheme["name"],
                "hidden": p.hidden,
            }
            if p.startingDirectory:
                p_dict["startingDirectory"] = p.startingDirectory
            if p.icon:
                p_dict["icon"] = p.icon
            if p.font is not None:
                p_dict["font"] = p.font
            if p.padding is not None:
                p_dict["padding"] = p.padding
            if p.scrollbarState is not None:
                p_dict["scrollbarState"] = p.scrollbarState
            if p.useAcrylic is not None:
                p_dict["useAcrylic"] = p.useAcrylic
            if p.opacity is not None:
                p_dict["opacity"] = p.opacity
            if p.unfocusedAppearance is not None:
                p_dict["unfocusedAppearance"] = p.unfocusedAppearance
            if p.systemBackdrop is not None:
                p_dict["systemBackdrop"] = p.systemBackdrop

            # Find matching profile by GUID or Name
            matched = False
            for idx, existing in enumerate(target_list):
                if existing.get("guid") == p.guid or existing.get("name") == p.name:
                    # Update in-place
                    target_list[idx].update(p_dict)
                    matched = True
                    updated += 1
                    break

            if not matched:
                target_list.append(p_dict)
                added += 1

        if self.set_default:
            settings["defaultProfile"] = WSL_GUID

        defaults = settings["profiles"].setdefault("defaults", {})
        for appearance in [defaults, *target_list]:
            appearance.update(colorScheme=self.color_scheme["name"], font=dict(self.font),
                              padding=self.padding, scrollbarState=self.scrollbar_state,
                              opacity=self.opacity, useAcrylic=self.use_acrylic)
            unfocused = appearance.get("unfocusedAppearance")
            if not isinstance(unfocused, dict):
                unfocused = appearance["unfocusedAppearance"] = {}
            unfocused.update(self.unfocused_appearance)

        return added, updated

    def merge_schemes(self, settings: Dict[str, Any]) -> bool:
        """Merge MiOS Dark color scheme into schemes array."""
        if "schemes" not in settings or not isinstance(settings["schemes"], list):
            settings["schemes"] = []

        schemes: List[Dict[str, Any]] = settings["schemes"]
        for idx, s in enumerate(schemes):
            if s.get("name") == self.color_scheme["name"]:
                schemes[idx] = self.color_scheme.copy()
                return True

        schemes.append(self.color_scheme.copy())
        return True

    def merged_settings(self, target_path: str) -> Tuple[Dict[str, Any], int, int, List[TerminalProfile]]:
        """The settings at target_path with the MiOS profiles and scheme merged in."""
        settings = self.load_settings(target_path)
        mios_profiles = self.build_mios_profiles()
        added, updated = self.merge_profiles(settings, mios_profiles)
        self.merge_schemes(settings)
        return settings, added, updated, mios_profiles

    def run(self) -> Dict[str, Any]:
        """Execute non-destructive Windows Terminal profile injection."""
        target_path = self.locate_settings_file()
        settings, added, updated, mios_profiles = self.merged_settings(target_path)
        formatted_json = json.dumps(settings, indent=4)

        if not self.mock and not self.dry_run:
            # Create backup if original exists
            if os.path.exists(target_path):
                shutil.copyfile(target_path, f"{target_path}.bak")
            mios_toml.write_atomic(target_path, formatted_json)

        return {
            "status": "success",
            "settings_path": target_path,
            "profiles_added": added,
            "profiles_updated": updated,
            "injected_profiles": [asdict(p) for p in mios_profiles],
            "scheme_injected": self.color_scheme["name"],
            "default_profile_set": self.set_default,
            "dry_run": self.dry_run,
            "mock": self.mock,
        }

def fixture_render() -> str:
    """The WSL terminal profile template, rendered from the vendor tier (WSL adds name and commandLine)."""
    injector = WindowsTerminalProfileInjector(mock=True, data=mios_toml.vendor_tree(_TREE))
    profile = {
        "colorScheme": injector.color_scheme["name"],
        "font": injector.font,
        "padding": injector.padding,
        "scrollbarState": injector.scrollbar_state,
        "useAcrylic": injector.use_acrylic,
        "opacity": injector.opacity,
        "unfocusedAppearance": injector.unfocused_appearance,
        "systemBackdrop": injector.system_backdrop,
    }
    return json.dumps({"profiles": [profile], "schemes": [injector.color_scheme]}, indent=4) + "\n"

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS Windows Terminal Profile & Color Scheme Injector"
    )
    parser.add_argument("--settings-json", help="Path to Windows Terminal settings.json")
    parser.add_argument("--ssh-port", type=int, default=2222, help="Host SSH port for loopback profile (default: 2222)")
    parser.add_argument("--ssh-user", default="mios", help="Host SSH username (default: mios)")
    parser.add_argument("--toml-config", help="Optional mios.toml layered above the user tier")
    parser.add_argument("--set-default", action="store_true", help="Set MiOS WSL as the default terminal profile")
    parser.add_argument("--dry-run", action="store_true", help="Simulate profile merging without writing to disk")
    parser.add_argument("--mock", action="store_true", help="Run deterministic mock execution for CI testing")
    parser.add_argument("--json", action="store_true", help="Format output as JSON dictionary")
    fixture = parser.add_mutually_exclusive_group()
    fixture.add_argument("--check-fixture", metavar="ROOT", help=f"Diff ROOT/{GOLDEN} against the vendor-tier render")
    fixture.add_argument("--write-fixture", metavar="ROOT", help=f"Regenerate ROOT/{GOLDEN} from the vendor tier")

    args = parser.parse_args()
    if args.check_fixture or args.write_fixture:
        try:
            return mios_toml.golden_gate("wt_profile_inject", args.check_fixture or args.write_fixture,
                                         {GOLDEN: fixture_render()}, write=bool(args.write_fixture))
        except (ValueError, OSError) as exc:
            print(f"[wt_profile_inject] ERROR: {exc}", file=sys.stderr)
            return 1

    try:
        injector = WindowsTerminalProfileInjector(
            settings_path=args.settings_json,
            ssh_port=args.ssh_port,
            ssh_user=args.ssh_user,
            toml_config_path=args.toml_config,
            set_default=args.set_default,
            dry_run=args.dry_run,
            mock=args.mock,
        )
        res = injector.run()
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"[wt_profile_inject] SUCCESS: Injected MiOS profiles into {res['settings_path']}")
            print(f"  Added: {res['profiles_added']}, Updated: {res['profiles_updated']}, Scheme: {res['scheme_injected']}")
            for p in res["injected_profiles"]:
                print(f"  - {p['name']} ({p['guid']}) -> {p['commandline']}")
        return 0
    except Exception as e:
        err = {"status": "error", "error": str(e)}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"[wt_profile_inject] ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
