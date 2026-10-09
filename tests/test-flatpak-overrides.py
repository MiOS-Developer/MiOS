#!/usr/bin/env python3
# AI-hint: Automated unit test suite for declarative Flatpak permission lockdown profiles (task_433).
# AI-doc: usr/share/doc/mios/manual/ch31-desktop-applications-and-flatpaks.md
from __future__ import annotations

import configparser
import json
import os
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_CLI = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-flatpak-lockdown")
_PROFILE = os.path.join(_ROOT, "usr", "share", "flatpak", "overrides", "global")


class TestFlatpakOverrides(unittest.TestCase):
    """Validates baked Flatpak permission lockdown profiles."""

    def test_files_exist(self):
        self.assertTrue(os.path.isfile(_PROFILE), f"Missing {_PROFILE}")
        self.assertTrue(os.path.isfile(_CLI), f"Missing {_CLI}")
        self.assertTrue(os.access(_CLI, os.X_OK), f"Not executable {_CLI}")

    def test_profile_contents(self):
        cfg = configparser.ConfigParser()
        cfg.read(_PROFILE)

        sockets = cfg.get("Context", "sockets", fallback="")
        filesystems = cfg.get("Context", "filesystems", fallback="")

        self.assertIn("!x11", sockets, "X11 socket must be disabled")
        self.assertIn("wayland", sockets, "Wayland socket must be enabled")
        self.assertIn("!host", filesystems, "Host filesystem access must be denied")

    def test_cli_validator(self):
        cmd = [sys.executable, _CLI] if sys.platform == "win32" or not os.access(_CLI, os.X_OK) else [_CLI]
        res = subprocess.run(
            cmd + ["--profile", _PROFILE, "--check", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "valid")
        self.assertTrue(data.get("x11_disabled"))
        self.assertTrue(data.get("wayland_enabled"))
        self.assertTrue(data.get("host_fs_denied"))
        self.assertTrue(data.get("valid"))


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestFlatpakOverrides)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
