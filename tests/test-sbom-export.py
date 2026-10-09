#!/usr/bin/env python3
# AI-hint: Automated unit test suite for Hermetic Podman OCI image synthesis and Syft SBOM generation (task_453).
# AI-doc: usr/share/doc/mios/manual/ch18-supply-chain-and-image-integrity.md
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_EXPORT_SCRIPT = os.path.join(_ROOT, "automation", "92-export-sbom.sh")
_SBOM_JSON = os.path.join(_ROOT, "usr", "share", "doc", "mios", "sbom.json")


class TestSbomExport(unittest.TestCase):
    """Validates SPDX SBOM generation and export pipeline."""

    def test_script_exists_and_executable(self):
        self.assertTrue(os.path.isfile(_EXPORT_SCRIPT), f"Missing {_EXPORT_SCRIPT}")
        self.assertTrue(os.access(_EXPORT_SCRIPT, os.X_OK), f"Not executable: {_EXPORT_SCRIPT}")

    def test_bash_syntax(self):
        res = subprocess.run(["bash", "-n", _EXPORT_SCRIPT], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Syntax error in {_EXPORT_SCRIPT}: {res.stderr}")

    def test_sbom_structure(self):
        # Run export script
        res = subprocess.run([_EXPORT_SCRIPT], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Export script failed: {res.stderr}")

        self.assertTrue(os.path.isfile(_SBOM_JSON), f"Missing {_SBOM_JSON}")
        with open(_SBOM_JSON, "r", encoding="utf-8") as f:
            data = json.load(f)

        self.assertIn("spdxVersion", data)
        self.assertEqual(data["spdxVersion"], "SPDX-2.3")
        self.assertEqual(data.get("SPDXID"), "SPDXRef-DOCUMENT")
        self.assertIn("packages", data)
        self.assertTrue(len(data["packages"]) > 0)


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestSbomExport)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
