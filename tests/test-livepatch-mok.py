#!/usr/bin/env python3
# AI-hint: Automated unit test suite for MOK livepatch signature gate and IMA logger (task_726, task_727).
# AI-doc: usr/share/doc/mios/manual/ch41-machine-owner-key-management.md
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_LIVEPATCH_BIN = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-livepatch")


class TestLivepatchMOK(unittest.TestCase):
    """Validates MOK signature gate, unsigned rejection, and IMA measurement logging."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_binary_executable(self):
        self.assertTrue(os.path.isfile(_LIVEPATCH_BIN), f"Missing {_LIVEPATCH_BIN}")
        self.assertTrue(os.access(_LIVEPATCH_BIN, os.X_OK), f"Not executable {_LIVEPATCH_BIN}")

    def test_unsigned_module_rejection(self):
        unsigned_mod = os.path.join(self.tmpdir.name, "kpatch_unsigned.ko")
        with open(unsigned_mod, "wb") as f:
            f.write(b"\x7fELF" + b"\x00" * 1024)

        res = subprocess.run(
            [sys.executable, _LIVEPATCH_BIN, "--verify", unsigned_mod, "--json"],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 1)
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "failed")
        self.assertFalse(data.get("details", {}).get("verified"))

    def test_signed_module_and_ima_logging(self):
        signed_mod = os.path.join(self.tmpdir.name, "kpatch_signed.ko")
        # Module payload ending with Linux kernel module signature trailer
        # struct module_signature: 12 bytes header + 28 bytes magic
        sig_data = b"MOK-SIGNED-PAYLOAD-FOR-TESTING"
        trailer = b"~Module signature append~\n"
        with open(signed_mod, "wb") as f:
            f.write(b"\x7fELF" + sig_data + trailer)

        ima_log = os.path.join(self.tmpdir.name, "ima_log.txt")

        res = subprocess.run(
            [
                sys.executable,
                _LIVEPATCH_BIN,
                "--apply", signed_mod,
                "--ima-log", ima_log,
                "--dry-run",
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "success")
        self.assertTrue(data.get("signature", {}).get("verified"))
        self.assertTrue(data.get("ima_recorded"))

        # Verify IMA log content
        self.assertTrue(os.path.isfile(ima_log))
        with open(ima_log, "r") as f:
            ima_entry = f.read()
        self.assertIn("ima-ng", ima_entry)
        self.assertIn(data["sha256"], ima_entry)


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestLivepatchMOK)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
