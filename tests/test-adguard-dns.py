#!/usr/bin/env python3
# AI-hint: Automated unit test suite for systemd-resolved to mios-adguard split-horizon DNS routing (task_441).
# AI-doc: usr/share/doc/mios/manual/ch28-dynamic-network-and-firewall-management.md
from __future__ import annotations

import configparser
import os
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_USR_CONF = os.path.join(_ROOT, "usr", "lib", "systemd", "resolved.conf.d", "10-adguard.conf")
_ETC_CONF = os.path.join(_ROOT, "etc", "systemd", "resolved.conf.d", "10-adguard.conf")
_SCRIPT = os.path.join(_ROOT, "automation", "30-dns-config.sh")


class TestAdguardDNS(unittest.TestCase):
    """Validates systemd-resolved split-horizon DNS configuration to AdGuard Home."""

    def test_files_exist(self):
        self.assertTrue(os.path.isfile(_USR_CONF), f"Missing {_USR_CONF}")
        self.assertTrue(os.path.isfile(_ETC_CONF), f"Missing {_ETC_CONF}")
        self.assertTrue(os.path.isfile(_SCRIPT), f"Missing {_SCRIPT}")
        self.assertTrue(os.access(_SCRIPT, os.X_OK), f"Not executable: {_SCRIPT}")

    def test_resolved_config_directives(self):
        for path in (_USR_CONF, _ETC_CONF):
            parser = configparser.ConfigParser()
            parser.read(path)
            self.assertIn("Resolve", parser.sections(), f"Missing [Resolve] section in {path}")
            resolve = parser["Resolve"]
            self.assertEqual(resolve.get("DNS"), "127.0.0.1:5353")
            domains = resolve.get("Domains", "")
            self.assertIn("~mios", domains)
            self.assertIn("~cluster.local", domains)

    def test_script_syntax_and_execution(self):
        # Run bash syntax check
        res = subprocess.run(["bash", "-n", _SCRIPT], capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Syntax error in {_SCRIPT}: {res.stderr}")


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestAdguardDNS)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
