#!/usr/bin/env python3
# AI-hint: Consolidated unit test suite for MiOS Looking Glass & Display domain (task_3353 / GATECAT-01).
# AI-related: usr/libexec/mios/display/looking_glass.py, usr/libexec/mios/vfio/setup-looking-glass.py, usr/share/doc/mios/manual/ch21-looking-glass-b7-and-kvmfr.md
"""Automated unit test suite for MiOS Looking Glass & Display domain.

Consolidates:
- Looking Glass B6 SPICE-direct client configuration, client.ini, and keybindings (test-looking-glass-config.py)
- Looking Glass B6 IVSHMEM shared memory setup and VFIO passthrough validation (test-looking-glass-setup.py)
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_CANDIDATES = [
    os.path.normpath(os.path.join(_HERE, "..")),
    os.path.normpath(r"M:\MiOS\worktrees\mios-integration"),
    os.path.normpath(r"C:\MiOS"),
]
_ROOT = next((c for c in _CANDIDATES if os.path.isfile(os.path.join(c, "usr", "libexec", "mios", "display", "looking_glass.py"))), os.path.normpath(os.path.join(_HERE, "..")))

# Load Looking Glass B6 Config Manager (usr/libexec/mios/display/looking_glass.py)
_LG_CONFIG_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "display", "looking_glass.py")
spec_lg = importlib.util.spec_from_file_location("looking_glass", _LG_CONFIG_PATH)
if spec_lg and spec_lg.loader:
    looking_glass = importlib.util.module_from_spec(spec_lg)
    sys.modules[spec_lg.name] = looking_glass
    spec_lg.loader.exec_module(looking_glass)
else:
    raise ImportError(f"Could not load looking_glass module from {_LG_CONFIG_PATH}")

# Load Looking Glass B6 IVSHMEM/VFIO Setup (usr/libexec/mios/vfio/setup-looking-glass.py)
_LG_SETUP_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "vfio", "setup-looking-glass.py")
spec_setup = importlib.util.spec_from_file_location("setup_looking_glass", _LG_SETUP_PATH)
if spec_setup and spec_setup.loader:
    setup_looking_glass = importlib.util.module_from_spec(spec_setup)
    sys.modules[spec_setup.name] = setup_looking_glass
    spec_setup.loader.exec_module(setup_looking_glass)
else:
    raise ImportError(f"Could not load setup-looking-glass module from {_LG_SETUP_PATH}")


# ============================================================================
# Domain 4.1: Looking Glass B6 Client Configuration & Keybinding Manager
# (Migrated from tests/test-looking-glass-config.py)
# ============================================================================

class TestLookingGlassConfig(unittest.TestCase):
    """Validates Looking Glass B6 client configuration, INI synthesis, and keybinding generators."""

    def setUp(self) -> None:
        self.manager = looking_glass.LookingGlassConfigManager(
            vm_name="win11-gaming",
            shm_file="/dev/kvmfr0",
            spice_socket="/var/run/libvirt/qemu/win11-gaming-spice.sock",
            escape_key="KEY_SCROLLLOCK",
            full_screen=False,
            allow_dma=True,
        )

    def test_default_config_dict(self) -> None:
        cfg = self.manager.get_default_config_dict()
        self.assertIn("app", cfg)
        self.assertIn("win", cfg)
        self.assertIn("input", cfg)
        self.assertIn("spice", cfg)
        self.assertIn("wayland", cfg)

        self.assertEqual(cfg["app"]["shmFile"], "/dev/kvmfr0")
        self.assertTrue(cfg["app"]["allowDMA"])
        self.assertEqual(cfg["input"]["escapeKey"], "KEY_SCROLLLOCK")
        self.assertEqual(cfg["spice"]["host"], "/var/run/libvirt/qemu/win11-gaming-spice.sock")
        self.assertEqual(cfg["spice"]["port"], 0)
        self.assertTrue(cfg["wayland"]["warpSupport"])

    def test_ini_generation_and_roundtrip_parse(self) -> None:
        ini_text = self.manager.generate_ini()
        self.assertIn("[app]", ini_text)
        self.assertIn("shmFile = /dev/kvmfr0", ini_text)
        self.assertIn("allowDMA = true", ini_text)
        self.assertIn("[spice]", ini_text)
        self.assertIn("host = /var/run/libvirt/qemu/win11-gaming-spice.sock", ini_text)
        self.assertIn("port = 0", ini_text)

        parsed = looking_glass.LookingGlassConfigManager.parse_ini(ini_text)
        self.assertEqual(parsed["app"]["shmFile"], "/dev/kvmfr0")
        self.assertIs(parsed["app"]["allowDMA"], True)
        self.assertEqual(parsed["input"]["escapeKey"], "KEY_SCROLLLOCK")
        self.assertEqual(parsed["spice"]["host"], "/var/run/libvirt/qemu/win11-gaming-spice.sock")
        self.assertEqual(parsed["spice"]["port"], 0)

    def test_ini_overrides(self) -> None:
        overrides = {
            "win": {"fullScreen": True, "uiTheme": "light"},
            "input": {"mouseSens": 5},
        }
        ini_text = self.manager.generate_ini(overrides=overrides)
        parsed = looking_glass.LookingGlassConfigManager.parse_ini(ini_text)
        self.assertIs(parsed["win"]["fullScreen"], True)
        self.assertEqual(parsed["win"]["uiTheme"], "light")
        self.assertEqual(parsed["input"]["mouseSens"], 5)

    def test_spice_socket_resolution(self) -> None:
        sock1 = looking_glass.LookingGlassConfigManager.resolve_spice_socket("win11")
        self.assertEqual(sock1, "/var/run/libvirt/qemu/win11-spice.sock")

        explicit = "/custom/socket.sock"
        sock2 = looking_glass.LookingGlassConfigManager.resolve_spice_socket("win11", explicit_path=explicit)
        self.assertEqual(sock2, explicit)

    def test_validate_mock_checks(self) -> None:
        spice_res = self.manager.validate_spice_socket(mock=True)
        self.assertEqual(spice_res["status"], "pass")
        self.assertTrue(spice_res["is_socket"])

        shm_res = self.manager.validate_shm(mock=True)
        self.assertEqual(shm_res["status"], "pass")
        self.assertTrue(shm_res["accessible"])

    def test_hyprland_rules_generation(self) -> None:
        rules = self.manager.generate_hyprland_rules()
        self.assertIn("windowrulev2 = fullscreen, class:^(looking-glass-client)$", rules)
        self.assertIn("windowrulev2 = idleinhibit always, class:^(looking-glass-client)$", rules)
        self.assertIn("windowrulev2 = immediate, class:^(looking-glass-client)$", rules)
        self.assertIn("bind = $mainMod, Scroll_Lock, exec, /usr/bin/looking-glass-client -f /dev/kvmfr0", rules)

    def test_gnome_rules_generation(self) -> None:
        gnome_cmds = self.manager.generate_gnome_rules()
        self.assertIn("gsettings set org.gnome.settings-daemon.plugins.media-keys", gnome_cmds)
        self.assertIn("Looking Glass VM Capture", gnome_cmds)
        self.assertIn("Scroll_Lock", gnome_cmds)
        self.assertIn("/usr/bin/looking-glass-client -f /dev/kvmfr0", gnome_cmds)

    def test_launch_args_synthesis(self) -> None:
        args = self.manager.build_client_launch_args()
        self.assertIn("looking-glass-client", args)
        self.assertIn("-f", args)
        self.assertIn("/dev/kvmfr0", args)
        self.assertIn("spice:host=/var/run/libvirt/qemu/win11-gaming-spice.sock", args)
        self.assertIn("spice:port=0", args)
        self.assertIn("input:escapeKey=KEY_SCROLLLOCK", args)

        cfg_args = self.manager.build_client_launch_args(config_path="/etc/looking-glass/client.ini")
        self.assertEqual(cfg_args, ["looking-glass-client", "-C", "/etc/looking-glass/client.ini"])

    def test_verify_all(self) -> None:
        res = self.manager.verify_all(mock=True)
        self.assertEqual(res["status"], "pass")
        self.assertEqual(res["checks"]["spice_socket"], "pass")
        self.assertEqual(res["checks"]["shm_device"], "pass")
        self.assertEqual(res["checks"]["ini_generation"], "pass")


# ============================================================================
# Domain 4.2: Looking Glass IVSHMEM Shared Memory & VFIO Setup
# (Migrated from tests/test-looking-glass-setup.py)
# ============================================================================

class TestLookingGlassSetup(unittest.TestCase):
    """Validates Looking Glass IVSHMEM XML generation and memory allocation checks."""

    def test_ivshmem_xml_generation(self):
        lg = setup_looking_glass.LookingGlassManager(size_mb=128)
        xml = lg.generate_ivshmem_xml()
        self.assertIn('<shmem name="looking-glass">', xml)
        self.assertIn('<model type="ivshmem-plain"/>', xml)
        self.assertIn('<size unit="M">128</size>', xml)

    def test_mock_shm_validation(self):
        lg = setup_looking_glass.LookingGlassManager()
        self.assertTrue(lg.validate_shm_allocation(mock=True))

    def test_mock_kvmfr_validation(self):
        lg = setup_looking_glass.LookingGlassManager()
        self.assertTrue(lg.validate_kvmfr_device(mock=True))

    def test_verify_all_mock(self):
        lg = setup_looking_glass.LookingGlassManager(size_mb=64)
        res = lg.verify_all(mock=True)
        self.assertEqual(res["status"], "pass")
        self.assertEqual(res["checks"]["shm_allocation"], "pass")
        self.assertEqual(res["checks"]["kvmfr_device"], "pass")

    def test_service_unit_file(self):
        svc_path = os.path.join(_ROOT, "usr", "lib", "systemd", "system", "mios-vfio-setup.service")
        self.assertTrue(os.path.exists(svc_path), f"Service file missing at {svc_path}")
        with open(svc_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("setup-looking-glass.py --verify", content)
        self.assertIn("[Install]", content)


if __name__ == "__main__":
    unittest.main()
