#!/usr/bin/env python3
# AI-hint: Automated unit test suite for unified mios CLI engine and output formatting (task_457).
# AI-doc: usr/share/doc/mios/manual/mios.md
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_MIOS_BIN = os.path.join(_ROOT, "usr", "bin", "mios")
_CLI_LIB = os.path.join(_ROOT, "usr", "lib", "mios")

if _CLI_LIB not in sys.path:
    sys.path.insert(0, _CLI_LIB)


class TestCliEngine(unittest.TestCase):
    """Validates unified CLI engine, POSIX exit codes, and adaptive formatting."""

    def setUp(self):
        self.env = os.environ.copy()
        self.env["MIOS_ROOT"] = _ROOT

    def test_cli_binary_exists(self):
        self.assertTrue(os.path.isfile(_MIOS_BIN), f"Missing {_MIOS_BIN}")
        self.assertTrue(os.access(_MIOS_BIN, os.X_OK), f"Not executable: {_MIOS_BIN}")

    def test_version_json_output(self):
        res = subprocess.run([_MIOS_BIN, "version", "--json"], env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Error: {res.stderr}")
        data = json.loads(res.stdout)
        self.assertEqual(data.get("version"), "0.3.0")
        self.assertEqual(data.get("law_conformance"), 16)

    def test_version_yaml_output(self):
        res = subprocess.run([_MIOS_BIN, "version", "--yaml"], env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Error: {res.stderr}")
        self.assertIn("version: 0.3.0", res.stdout)
        self.assertIn("law_conformance: 16", res.stdout)

    def test_health_json_output(self):
        res = subprocess.run([_MIOS_BIN, "health", "--json"], env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Error: {res.stderr}")
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "healthy")
        self.assertTrue(data.get("immutable_root"))

    def test_status_json_output(self):
        res = subprocess.run([_MIOS_BIN, "status", "--json"], env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Error: {res.stderr}")
        data = json.loads(res.stdout)
        self.assertIn("os", data)
        self.assertIn("cpu", data)
        self.assertIn("memory", data)
        self.assertIn("services", data)

    def test_formatter_module_direct(self):
        from cli.formatter import OutputFormatter
        formatter = OutputFormatter(force_format="json")
        sample = {"test": 123, "active": True}
        out = formatter.format(sample)
        parsed = json.loads(out)
        self.assertEqual(parsed["test"], 123)


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestCliEngine)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
