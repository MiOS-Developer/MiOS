#!/usr/bin/env python3
# AI-hint: Automated unit test suite for TPM 2.0 PCR 7/11 secret sealing and unsealing (task_437).
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
_TPM_BIN = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-tpm-seal")


class TestTPM2Seal(unittest.TestCase):
    """Validates TPM 2.0 PCR 7/11 automated secret sealing and unsealing."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_binary_executable(self):
        self.assertTrue(os.path.isfile(_TPM_BIN), f"Missing {_TPM_BIN}")
        self.assertTrue(os.access(_TPM_BIN, os.X_OK), f"Not executable: {_TPM_BIN}")

    def test_check_capabilities(self):
        res = subprocess.run(
            [_TPM_BIN, "check", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        self.assertIn("present", data)
        self.assertIn("7", data.get("supported_pcrs", []))
        self.assertIn("11", data.get("supported_pcrs", []))

    def test_seal_unseal_roundtrip(self):
        inp_secrets = os.path.join(self.tmpdir.name, "secrets.env")
        out_sealed = os.path.join(self.tmpdir.name, "secrets.env.tpm2")
        out_restored = os.path.join(self.tmpdir.name, "secrets.env.restored")

        secret_content = "MIOS_AUTH_SECRET=pcr7_11_cryptographic_token_98765\n"
        with open(inp_secrets, "w", encoding="utf-8") as f:
            f.write(secret_content)

        # Seal
        res_seal = subprocess.run(
            [_TPM_BIN, "seal", "--input", inp_secrets, "--output", out_sealed, "--pcrs", "7+11", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        seal_data = json.loads(res_seal.stdout)
        self.assertIn("sealed", seal_data.get("status", ""))
        self.assertEqual(seal_data.get("pcrs"), "7+11")
        self.assertTrue(os.path.isfile(out_sealed))

        # Unseal
        res_unseal = subprocess.run(
            [_TPM_BIN, "unseal", "--input", out_sealed, "--output", out_restored, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        unseal_data = json.loads(res_unseal.stdout)
        self.assertEqual(unseal_data.get("status"), "unsealed")

        with open(out_restored, "r", encoding="utf-8") as f:
            restored_content = f.read()

        self.assertEqual(secret_content, restored_content)


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestTPM2Seal)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
