#!/usr/bin/env python3
# AI-hint: Automated unit test suite for Thunderbolt/USB4 eGPU and PCIe accelerator hotplug handler (task_439).
# AI-doc: usr/share/doc/mios/manual/ch37-gpu-capability-detection-and-passthrough-shims.md
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_RULES = os.path.join(_ROOT, "usr", "lib", "udev", "rules.d", "99-mios-egpu.rules")
_HOTPLUG_BIN = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-egpu-hotplug")


class TestEGPUHotplug(unittest.TestCase):
    """Validates dynamic eGPU hotplug udev rules and event handler."""

    def test_files_exist(self):
        self.assertTrue(os.path.isfile(_RULES), f"Missing {_RULES}")
        self.assertTrue(os.path.isfile(_HOTPLUG_BIN), f"Missing {_HOTPLUG_BIN}")
        self.assertTrue(os.access(_HOTPLUG_BIN, os.X_OK), f"Not executable: {_HOTPLUG_BIN}")

    def test_udev_rules_contents(self):
        with open(_RULES, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("SUBSYSTEM==\"thunderbolt\"", content)
        self.assertIn("ATTR{authorized}=\"1\"", content)
        self.assertIn("0x030000", content)  # VGA
        self.assertIn("0x030200", content)  # 3D
        self.assertIn("0x120000", content)  # Accelerators
        self.assertIn("mios-egpu-hotplug", content)

    def test_hotplug_invocation_positional(self):
        # Simulate udev calling: mios-egpu-hotplug add /devices/pci...
        res = subprocess.run(
            [_HOTPLUG_BIN, "add", "/devices/pci0000:00/0000:00:01.0/0000:01:00.0", "--dry-run", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "success")
        self.assertEqual(data.get("action"), "add")
        self.assertTrue(data.get("cdi_triggered"))

    def test_hotplug_remove_positional(self):
        # Simulate udev calling: mios-egpu-hotplug remove /devices/pci...
        res = subprocess.run(
            [_HOTPLUG_BIN, "remove", "/devices/pci0000:00/0000:00:01.0/0000:01:00.0", "--dry-run", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "success")
        self.assertEqual(data.get("action"), "remove")
        self.assertTrue(data.get("cdi_triggered"))


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestEGPUHotplug)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
