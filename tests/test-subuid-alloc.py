#!/usr/bin/env python3
# AI-hint: Automated unit test suite for deterministic /etc/subuid and /etc/subgid range generator (task_421).
# AI-doc: usr/share/doc/mios/manual/ch17-defense-in-depth-hardening.md
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_ALLOC_BIN = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-subuid-alloc")
_AUTO_SCRIPT = os.path.join(_ROOT, "automation", "31-subuid-alloc.sh")
_SYSUSERS_CONF = os.path.join(_ROOT, "usr", "lib", "sysusers.d", "50-mios-users.conf")


class TestSubuidAlloc(unittest.TestCase):
    """Validates deterministic subuid/subgid range allocations and collision checks."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_files_exist(self):
        self.assertTrue(os.path.isfile(_ALLOC_BIN), f"Missing {_ALLOC_BIN}")
        if sys.platform != "win32":
            self.assertTrue(os.access(_ALLOC_BIN, os.X_OK), f"Not executable: {_ALLOC_BIN}")
            self.assertTrue(os.access(_AUTO_SCRIPT, os.X_OK), f"Not executable: {_AUTO_SCRIPT}")
        self.assertTrue(os.path.isfile(_AUTO_SCRIPT), f"Missing {_AUTO_SCRIPT}")
        self.assertTrue(os.path.isfile(_SYSUSERS_CONF), f"Missing {_SYSUSERS_CONF}")

    def test_deterministic_formula(self):
        test_cases = [
            (1000, 100000, 65536),
            (1001, 165536, 65536),
            (1002, 231072, 65536),
            (1005, 427680, 65536),
        ]
        for uid, expected_base, expected_count in test_cases:
            res = subprocess.run(
                [sys.executable, _ALLOC_BIN, "--user", f"u{uid}", "--uid", str(uid), "--json"],
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(res.stdout)
            self.assertEqual(data["base"], expected_base)
            self.assertEqual(data["count"], expected_count)

    def test_sync_and_collision_check(self):
        subuid_f = os.path.join(self.tmpdir.name, "subuid")
        subgid_f = os.path.join(self.tmpdir.name, "subgid")

        res_sync = subprocess.run(
            [sys.executable, _ALLOC_BIN, "--sync", "--subuid", subuid_f, "--subgid", subgid_f, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        sync_data = json.loads(res_sync.stdout)
        self.assertEqual(sync_data["status"], "synchronized")

        res_check = subprocess.run(
            [sys.executable, _ALLOC_BIN, "--check", "--subuid", subuid_f, "--subgid", subgid_f, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        check_data = json.loads(res_check.stdout)
        self.assertEqual(check_data["status"], "valid")
        self.assertTrue(check_data["subuid_collision_free"])
        self.assertTrue(check_data["subgid_collision_free"])


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestSubuidAlloc)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
