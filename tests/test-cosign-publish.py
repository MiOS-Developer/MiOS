#!/usr/bin/env python3
# AI-hint: Automated unit test suite for local Cosign image signing and registry push gate (task_454).
# AI-doc: usr/share/doc/mios/manual/ch18-supply-chain-and-image-integrity.md
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_PUBLISH_BIN = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-cosign-publish")
_REGISTRY_SVC = os.path.join(_ROOT, "usr", "lib", "systemd", "system", "mios-registry.service")


class TestCosignPublish(unittest.TestCase):
    """Validates local Cosign signing, registry push, and verification tool."""

    def test_binaries_and_units_exist(self):
        self.assertTrue(os.path.isfile(_PUBLISH_BIN), f"Missing {_PUBLISH_BIN}")
        self.assertTrue(os.access(_PUBLISH_BIN, os.X_OK), f"Not executable: {_PUBLISH_BIN}")
        self.assertTrue(os.path.isfile(_REGISTRY_SVC), f"Missing {_REGISTRY_SVC}")

    def test_registry_service_syntax(self):
        with open(_REGISTRY_SVC, "r") as f:
            content = f.read()
        self.assertIn("[Unit]", content)
        self.assertIn("[Service]", content)
        self.assertIn("[Install]", content)
        self.assertIn("127.0.0.1:5000:5000", content)
        self.assertIn("registry:2", content)

    def test_mock_sign_and_verify(self):
        res = subprocess.run(
            [_PUBLISH_BIN, "--mock", "--json", "--image", "localhost:5000/mios:latest"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0, f"Failed: {res.stderr}")
        data = json.loads(res.stdout)
        self.assertTrue(data.get("ready_for_switch"))
        self.assertTrue(data["signing"]["success"])
        self.assertTrue(data["verification"]["verified"])

    def test_mock_unsigned_image_rejected(self):
        res = subprocess.run(
            [_PUBLISH_BIN, "--mock", "--json", "--verify-only", "--image", "localhost:5000/unsigned:latest"],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertFalse(data.get("verified"))
        self.assertFalse(data.get("permitted_for_switch"))


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestCosignPublish)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
