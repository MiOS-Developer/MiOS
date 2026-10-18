#!/usr/bin/env python3
# AI-hint: Looking Glass B6 spice-direct host input client configuration and keybinding integration.
# AI-related: tests/test-looking-glass.py, usr/share/doc/mios/manual/ch21-looking-glass-b7-and-kvmfr.md
"""
MiOS Looking Glass B6 Client Configuration & Direct Input Manager.

Manages Looking Glass B6 client.ini generation/parsing, direct SPICE UNIX socket
input configuration (/var/run/libvirt/qemu/<vm>-spice.sock), Hyprland windowrulev2
rules, GNOME custom keybinding integration, and client execution argument synthesis.
"""

from __future__ import annotations

import argparse
import configparser
import io
import json
import os
import stat
import sys
from typing import Any, Dict, List, Optional, Union

DEFAULT_SHM_FILE = "/dev/kvmfr0"
FALLBACK_SHM_FILE = "/dev/shm/looking-glass"
DEFAULT_SPICE_SOCKET_DIR = "/var/run/libvirt/qemu"
DEFAULT_ESCAPE_KEY = "KEY_SCROLLLOCK"
DEFAULT_VM_NAME = "win11"

class LookingGlassConfigManager:
    """Manages Looking Glass B6 client.ini configurations, direct SPICE sockets, and keybindings."""

    def __init__(
        self,
        vm_name: str = DEFAULT_VM_NAME,
        shm_file: str = DEFAULT_SHM_FILE,
        spice_socket: Optional[str] = None,
        escape_key: str = DEFAULT_ESCAPE_KEY,
        full_screen: bool = False,
        auto_resize: bool = True,
        keep_aspect: bool = True,
        ui_theme: str = "dark",
        raw_mouse: bool = True,
        auto_capture: bool = True,
        mouse_sens: int = 0,
        grab_keyboard: bool = True,
        warp_support: bool = True,
        fractional_scale: bool = True,
        allow_dma: bool = True,
    ) -> None:
        self.vm_name = vm_name
        self.shm_file = shm_file
        self.escape_key = escape_key
        self.full_screen = full_screen
        self.auto_resize = auto_resize
        self.keep_aspect = keep_aspect
        self.ui_theme = ui_theme
        self.raw_mouse = raw_mouse
        self.auto_capture = auto_capture
        self.mouse_sens = mouse_sens
        self.grab_keyboard = grab_keyboard
        self.warp_support = warp_support
        self.fractional_scale = fractional_scale
        self.allow_dma = allow_dma
        self.spice_socket = self.resolve_spice_socket(vm_name, spice_socket)

    @staticmethod
    def resolve_spice_socket(vm_name: str, explicit_path: Optional[str] = None) -> str:
        """Resolves the SPICE direct UNIX domain socket path for a target VM."""
        if explicit_path:
            return explicit_path

        candidates = [
            f"/var/run/libvirt/qemu/{vm_name}-spice.sock",
            f"/run/libvirt/qemu/{vm_name}-spice.sock",
            f"/var/run/libvirt/qemu/{vm_name}.spice",
            f"/run/libvirt/qemu/{vm_name}.spice",
        ]
        for candidate in candidates:
            if os.path.exists(candidate):
                return candidate

        return f"{DEFAULT_SPICE_SOCKET_DIR}/{vm_name}-spice.sock"

    def get_default_config_dict(self) -> Dict[str, Dict[str, Any]]:
        """Returns the canonical Looking Glass B6 configuration structure."""
        return {
            "app": {
                "shmFile": self.shm_file,
                "allowDMA": self.allow_dma,
            },
            "win": {
                "fullScreen": self.full_screen,
                "autoResize": self.auto_resize,
                "keepAspect": self.keep_aspect,
                "uiTheme": self.ui_theme,
            },
            "input": {
                "escapeKey": self.escape_key,
                "rawMouse": self.raw_mouse,
                "autoCapture": self.auto_capture,
                "mouseSens": self.mouse_sens,
                "grabKeyboard": self.grab_keyboard,
            },
            "spice": {
                "enable": True,
                "host": self.spice_socket,
                "port": 0,
                "audio": False,
            },
            "wayland": {
                "warpSupport": self.warp_support,
                "fractionalScale": self.fractional_scale,
            },
        }

    def generate_ini(self, overrides: Optional[Dict[str, Dict[str, Any]]] = None) -> str:
        """Generates canonical Looking Glass B6 client.ini content with optional overrides."""
        config_data = self.get_default_config_dict()
        if overrides:
            for section, options in overrides.items():
                if section not in config_data:
                    config_data[section] = {}
                for key, val in options.items():
                    config_data[section][key] = val

        parser = configparser.ConfigParser()
        parser.optionxform = str  # Preserve camelCase keys

        for section, options in config_data.items():
            parser.add_section(section)
            for key, val in options.items():
                if isinstance(val, bool):
                    parser.set(section, key, "true" if val else "false")
                else:
                    parser.set(section, key, str(val))

        output = io.StringIO()
        parser.write(output)
        return output.getvalue().strip() + "\n"

    @staticmethod
    def parse_ini(ini_content_or_path: str) -> Dict[str, Dict[str, Any]]:
        """Parses Looking Glass client.ini from a file path or raw string."""
        parser = configparser.ConfigParser()
        parser.optionxform = str

        if os.path.exists(ini_content_or_path):
            with open(ini_content_or_path, "r", encoding="utf-8") as f:
                parser.read_file(f)
        else:
            parser.read_string(ini_content_or_path)

        result: Dict[str, Dict[str, Any]] = {}
        for section in parser.sections():
            result[section] = {}
            for key, val in parser.items(section):
                if val.lower() == "true":
                    result[section][key] = True
                elif val.lower() == "false":
                    result[section][key] = False
                elif val.isdigit():
                    result[section][key] = int(val)
                else:
                    result[section][key] = val
        return result

    def validate_spice_socket(self, mock: bool = False) -> Dict[str, Any]:
        """Validates existence and socket permissions of the SPICE direct UNIX socket."""
        if mock or os.name == "nt":
            return {
                "status": "pass",
                "path": self.spice_socket,
                "is_socket": True,
                "mock": True,
            }

        if not os.path.exists(self.spice_socket):
            return {
                "status": "fail",
                "path": self.spice_socket,
                "error": f"SPICE UNIX socket not found at {self.spice_socket}",
                "mock": False,
            }

        st = os.stat(self.spice_socket)
        is_sock = stat.S_ISSOCK(st.st_mode)
        return {
            "status": "pass" if is_sock else "fail",
            "path": self.spice_socket,
            "is_socket": is_sock,
            "mock": False,
        }

    def validate_shm(self, mock: bool = False) -> Dict[str, Any]:
        """Validates IVSHMEM device node or shared memory framebuffer file."""
        if mock or os.name == "nt":
            return {
                "status": "pass",
                "path": self.shm_file,
                "accessible": True,
                "mock": True,
            }

        if not os.path.exists(self.shm_file):
            return {
                "status": "fail",
                "path": self.shm_file,
                "error": f"Shared memory device {self.shm_file} not found",
                "mock": False,
            }

        st = os.stat(self.shm_file)
        mode_ok = (st.st_mode & 0o777) == 0o660
        return {
            "status": "pass" if mode_ok else "fail",
            "path": self.shm_file,
            "accessible": mode_ok,
            "mode": oct(st.st_mode),
            "mock": False,
        }

    def generate_hyprland_rules(
        self,
        app_class: str = "looking-glass-client",
        title_pattern: str = "Looking Glass.*",
    ) -> str:
        """Generates Hyprland windowrulev2 configuration and capture toggle keybindings."""
        return f"""# Looking Glass B6 Hyprland Window Rules & Keybindings
windowrulev2 = fullscreen, class:^({app_class})$, title:^({title_pattern})$
windowrulev2 = idleinhibit always, class:^({app_class})$
windowrulev2 = immediate, class:^({app_class})$
windowrulev2 = noanim, class:^({app_class})$

# Capture toggle shortcut (ScrollLock)
bind = $mainMod, Scroll_Lock, exec, /usr/bin/looking-glass-client -f {self.shm_file}
"""

    def generate_gnome_rules(self) -> str:
        """Generates GNOME custom keybinding shell commands for Looking Glass capture."""
        kb_path = "/org/gnome/settings-daemon/plugins/media-keys/custom-keybindings/looking-glass/"
        return f"""# GNOME Looking Glass Keybinding Configuration
gsettings set org.gnome.settings-daemon.plugins.media-keys custom-keybindings "['{kb_path}']"
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:{kb_path} name "Looking Glass VM Capture"
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:{kb_path} command "/usr/bin/looking-glass-client -f {self.shm_file}"
gsettings set org.gnome.settings-daemon.plugins.media-keys.custom-keybinding:{kb_path} binding "Scroll_Lock"
"""

    def build_client_launch_args(
        self,
        extra_args: Optional[List[str]] = None,
        config_path: Optional[str] = None,
    ) -> List[str]:
        """Builds Looking Glass client launch command argument list."""
        cmd = ["looking-glass-client"]
        if config_path:
            cmd.extend(["-C", config_path])
        else:
            cmd.extend(["-f", self.shm_file])
            cmd.extend([f"spice:host={self.spice_socket}", "spice:port=0"])
            cmd.extend([f"input:escapeKey={self.escape_key}"])
            if self.full_screen:
                cmd.append("win:fullScreen=true")
            if self.allow_dma:
                cmd.append("app:allowDMA=true")

        if extra_args:
            cmd.extend(extra_args)
        return cmd

    def verify_all(self, mock: bool = False) -> Dict[str, Any]:
        """Executes full diagnostic validation of Looking Glass configuration."""
        spice_res = self.validate_spice_socket(mock=mock)
        shm_res = self.validate_shm(mock=mock)
        ini_content = self.generate_ini()
        parsed = self.parse_ini(ini_content)

        ini_valid = (
            parsed.get("app", {}).get("shmFile") == self.shm_file
            and parsed.get("spice", {}).get("host") == self.spice_socket
            and parsed.get("input", {}).get("escapeKey") == self.escape_key
        )

        overall_pass = (
            (spice_res["status"] == "pass" or mock or os.name == "nt")
            and (shm_res["status"] == "pass" or mock or os.name == "nt")
            and ini_valid
        )

        return {
            "status": "pass" if overall_pass else "fail",
            "vm_name": self.vm_name,
            "shm_file": self.shm_file,
            "spice_socket": self.spice_socket,
            "escape_key": self.escape_key,
            "checks": {
                "spice_socket": spice_res["status"],
                "shm_device": shm_res["status"],
                "ini_generation": "pass" if ini_valid else "fail",
            },
            "mock": mock or os.name == "nt",
        }

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS Looking Glass B6 Direct SPICE Host Input & Config Utility."
    )
    parser.add_argument("--vm-name", type=str, default=DEFAULT_VM_NAME, help="Target virtual machine domain name.")
    parser.add_argument("--shm-file", type=str, default=DEFAULT_SHM_FILE, help="Path to IVSHMEM file or kvmfr device.")
    parser.add_argument("--spice-socket", type=str, default=None, help="Explicit path to SPICE direct UNIX socket.")
    parser.add_argument("--escape-key", type=str, default=DEFAULT_ESCAPE_KEY, help="Input capture escape key.")
    parser.add_argument("--generate-ini", action="store_true", help="Generate Looking Glass client.ini content.")
    parser.add_argument("--parse-ini", type=str, default=None, help="Parse and dump an existing client.ini file.")
    parser.add_argument("--generate-hyprland", action="store_true", help="Generate Hyprland window rules and binds.")
    parser.add_argument("--generate-gnome", action="store_true", help="Generate GNOME custom keybinding commands.")
    parser.add_argument("--launch-args", action="store_true", help="Synthesize client CLI execution arguments.")
    parser.add_argument("--verify", action="store_true", help="Verify SPICE socket and IVSHMEM configuration.")
    parser.add_argument("--output", type=str, default=None, help="Optional output file path to write results.")
    parser.add_argument("--json", action="store_true", help="Output results in structured JSON format.")
    parser.add_argument("--mock", action="store_true", help="Run in mock/synthetic mode for verification.")
    args = parser.parse_args()

    manager = LookingGlassConfigManager(
        vm_name=args.vm_name,
        shm_file=args.shm_file,
        spice_socket=args.spice_socket,
        escape_key=args.escape_key,
    )

    result_text = ""
    json_data: Optional[Dict[str, Any]] = None

    if args.generate_ini:
        result_text = manager.generate_ini()
        if args.json:
            json_data = {"ini": result_text, "parsed": manager.parse_ini(result_text)}

    elif args.parse_ini:
        parsed_data = manager.parse_ini(args.parse_ini)
        if args.json:
            json_data = parsed_data
        else:
            result_text = json.dumps(parsed_data, indent=2)

    elif args.generate_hyprland:
        result_text = manager.generate_hyprland_rules()
        if args.json:
            json_data = {"rules": result_text, "compositor": "hyprland"}

    elif args.generate_gnome:
        result_text = manager.generate_gnome_rules()
        if args.json:
            json_data = {"rules": result_text, "compositor": "gnome"}

    elif args.launch_args:
        launch_list = manager.build_client_launch_args()
        if args.json:
            json_data = {"args": launch_list, "command": " ".join(launch_list)}
        else:
            result_text = " ".join(launch_list)

    elif args.verify or not sys.argv[1:]:
        verify_results = manager.verify_all(mock=args.mock or os.name == "nt")
        if args.json:
            json_data = verify_results
        else:
            result_text = (
                f"[looking-glass-config] Status: {verify_results['status'].upper()} (mock={verify_results['mock']})\n"
                f"  - VM: {verify_results['vm_name']}\n"
                f"  - SHM: {verify_results['shm_file']} ({verify_results['checks']['shm_device']})\n"
                f"  - SPICE Socket: {verify_results['spice_socket']} ({verify_results['checks']['spice_socket']})\n"
                f"  - INI Generation: {verify_results['checks']['ini_generation']}\n"
            )
        if not args.output and not args.json:
            sys.stdout.write(result_text)
            return 0 if verify_results["status"] == "pass" else 1

    if json_data is not None and args.json:
        result_text = json.dumps(json_data, indent=2) + "\n"

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(result_text)
    else:
        sys.stdout.write(result_text + ("\n" if not result_text.endswith("\n") else ""))

    return 0

if __name__ == "__main__":
    sys.exit(main())
