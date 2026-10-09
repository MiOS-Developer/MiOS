#!/usr/bin/env python3
# AI-hint: Automated unit test suite for dynamic plugin loader and subcommand discovery (task_458).
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


class TestPluginLoader(unittest.TestCase):
    """Validates dynamic plugin discovery and execution."""

    def setUp(self):
        self.env = os.environ.copy()
        self.env["MIOS_ROOT"] = _ROOT

    def test_plugin_loader_import_and_discovery(self):
        from cli.plugin_loader import PluginLoader
        loader = PluginLoader()
        plugins = loader.discover_plugins()
        self.assertIn("hello", plugins)
        info = plugins["hello"]
        self.assertEqual(info.name, "hello")
        self.assertTrue(info.is_python)
        self.assertIn("Sample dynamic plugin", info.description)

    def test_cli_plugin_help_output(self):
        res = subprocess.run([_MIOS_BIN, "--help"], env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)
        self.assertIn("Plugins:", res.stderr)
        self.assertIn("mios hello", res.stderr)

    def test_cli_plugin_execution_json(self):
        res = subprocess.run([_MIOS_BIN, "hello", "--json"], env=self.env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Error: {res.stderr}")
        data = json.loads(res.stdout)
        self.assertEqual(data.get("plugin"), "hello")
        self.assertEqual(data.get("status"), "active")

    def test_cli_unknown_plugin_fails(self):
        from cli.plugin_loader import PluginLoader
        loader = PluginLoader()
        rc = loader.execute_plugin("non_existent_subcommand_xyz", [])
        self.assertEqual(rc, 127)


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestPluginLoader)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
