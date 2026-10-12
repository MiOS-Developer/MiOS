#!/usr/bin/env python3
# AI-hint: Automated unit test suite for declarative nftables inter-container firewall rule generator (task_423).
# AI-doc: usr/share/doc/mios/manual/ch28-dynamic-network-and-firewall-management.md
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_FW_BIN = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-firewall-isolate")
_SERVICE_FILE = os.path.join(_ROOT, "usr", "lib", "systemd", "system", "mios-firewall.service")
_TOML_PATH = os.path.join(_ROOT, "usr", "share", "mios", "mios.toml")


class TestNftablesFirewallIsolate(unittest.TestCase):
    """Validates declarative nftables inter-container firewall rule generator."""

    def test_binary_executable(self):
        self.assertTrue(os.path.isfile(_FW_BIN), f"Missing {_FW_BIN}")
        self.assertTrue(os.access(_FW_BIN, os.X_OK), f"Not executable: {_FW_BIN}")

    def test_service_unit_exists(self):
        self.assertTrue(os.path.isfile(_SERVICE_FILE), f"Missing {_SERVICE_FILE}")
        with open(_SERVICE_FILE, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("mios-firewall-isolate", content)
        self.assertIn("WantedBy=multi-user.target", content)

    def test_ruleset_generation_and_policy(self):
        res = subprocess.run(
            [_FW_BIN, "--toml", _TOML_PATH, "--check", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "valid")
        self.assertEqual(data.get("default_policy"), "drop")
        self.assertGreaterEqual(data.get("rule_count", 0), 5)

        ruleset = data.get("ruleset", "")
        self.assertIn("table inet mios_container_isolate", ruleset)
        self.assertIn("policy drop;", ruleset)
        self.assertIn("ct state established,related accept", ruleset)
        self.assertIn("[mios-fw-drop]", ruleset)
        self.assertIn("tcp dport 8700 accept", ruleset)  # agent_pipe
        self.assertIn("tcp dport 8720 accept", ruleset)  # hermes
        self.assertIn("tcp dport 5432 accept", ruleset)  # pgvector


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestNftablesFirewallIsolate)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
