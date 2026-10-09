#!/usr/bin/env python3
# AI-hint: Automated unit test suite for scoped CDI specification generator (task_469).
# AI-doc: usr/share/doc/mios/manual/ch20-container-device-interface-plumbing.md
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_CDI_GEN = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-cdi-gen")


class TestCdiGen(unittest.TestCase):
    """Validates mios-cdi-gen scoped CDI generation."""

    def test_binary_exists_and_executable(self):
        self.assertTrue(os.path.isfile(_CDI_GEN), f"Missing {_CDI_GEN}")
        self.assertTrue(os.access(_CDI_GEN, os.X_OK), f"Not executable {_CDI_GEN}")

    def test_vendor_all_dry_run_json(self):
        res = subprocess.run(
            [sys.executable, _CDI_GEN, "--vendor", "all", "--dry-run", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "success")
        self.assertTrue(data.get("dry_run"))

        specs = data.get("specs", {})
        self.assertIn("nvidia", specs)
        self.assertIn("amd", specs)
        self.assertIn("intel", specs)

        # Check NVIDIA CDI spec schema
        nv_spec = specs["nvidia"]["spec"]
        self.assertEqual(nv_spec.get("cdiVersion"), "0.6.0")
        self.assertEqual(nv_spec.get("kind"), "nvidia.com/gpu")
        self.assertTrue(len(nv_spec.get("devices", [])) >= 1)
        nv_dev_0 = nv_spec["devices"][0]
        self.assertIn("containerEdits", nv_dev_0)
        nodes = [d["path"] for d in nv_dev_0["containerEdits"].get("deviceNodes", [])]
        self.assertTrue(any("nvidia" in p or "dxg" in p for p in nodes))

        # Check AMD CDI spec schema
        amd_spec = specs["amd"]["spec"]
        self.assertEqual(amd_spec.get("cdiVersion"), "0.6.0")
        self.assertEqual(amd_spec.get("kind"), "amd.com/gpu")

        # Check Intel CDI spec schema
        intel_spec = specs["intel"]["spec"]
        self.assertEqual(intel_spec.get("cdiVersion"), "0.6.0")
        self.assertEqual(intel_spec.get("kind"), "intel.com/gpu")

    def test_file_generation_in_temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            res = subprocess.run(
                [sys.executable, _CDI_GEN, "--vendor", "all", "--output-dir", tmpdir, "--json"],
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertEqual(res.returncode, 0)
            data = json.loads(res.stdout)
            self.assertEqual(data.get("status"), "success")

            for vendor in ["nvidia", "amd", "intel"]:
                path = os.path.join(tmpdir, f"{vendor}.json")
                self.assertTrue(os.path.isfile(path), f"File {path} not created")
                with open(path, "r") as f:
                    spec = json.load(f)
                    self.assertEqual(spec.get("cdiVersion"), "0.6.0")


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestCdiGen)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
