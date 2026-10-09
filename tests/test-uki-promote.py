#!/usr/bin/env python3
# AI-hint: Automated unit test suite for automated UKI A/B boot promotion and Greenboot validation gate (task_452).
# AI-doc: usr/share/doc/mios/manual/ch08-bootloader-and-unified-kernel-images-uki.md
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_USR_PROMOTE = os.path.join(_ROOT, "usr", "lib", "greenboot", "check", "required.d", "10-uki-promote.sh")
_ETC_PROMOTE = os.path.join(_ROOT, "etc", "greenboot", "check", "required.d", "10-uki-promote.sh")


class TestUkiPromote(unittest.TestCase):
    """Validates Greenboot UKI promotion gate."""

    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_scripts_exist_and_executable(self):
        self.assertTrue(os.path.isfile(_USR_PROMOTE), f"Missing {_USR_PROMOTE}")
        self.assertTrue(os.access(_USR_PROMOTE, os.X_OK), f"Not executable: {_USR_PROMOTE}")
        self.assertTrue(os.path.isfile(_ETC_PROMOTE), f"Missing {_ETC_PROMOTE}")
        self.assertTrue(os.access(_ETC_PROMOTE, os.X_OK), f"Not executable: {_ETC_PROMOTE}")

    def test_no_op_when_no_staged_uki(self):
        env = dict(os.environ, MIOS_BOOT_DIR=self.tmpdir.name)
        res = subprocess.run([_USR_PROMOTE], env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0)

    def test_successful_atomic_promotion(self):
        boot_dir = self.tmpdir.name
        efi_dir = os.path.join(boot_dir, "EFI", "Linux")
        loader_dir = os.path.join(boot_dir, "loader")
        entries_dir = os.path.join(loader_dir, "entries")

        os.makedirs(efi_dir, exist_ok=True)
        os.makedirs(entries_dir, exist_ok=True)

        next_efi = os.path.join(efi_dir, "mios-next.efi")
        default_efi = os.path.join(efi_dir, "mios.efi")
        next_conf = os.path.join(entries_dir, "mios-next.conf")
        default_conf = os.path.join(entries_dir, "mios.conf")
        loader_conf = os.path.join(loader_dir, "loader.conf")

        with open(next_efi, "w") as f:
            f.write("NEW_STAGED_EFI_BINARY_V2\n")
        with open(next_conf, "w") as f:
            f.write("title MiOS Next\nlinux /EFI/Linux/mios-next.efi\n")
        with open(loader_conf, "w") as f:
            f.write("timeout 3\ndefault mios-next.conf\n")

        env = dict(os.environ, MIOS_BOOT_DIR=boot_dir)
        res = subprocess.run([_USR_PROMOTE], env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Promotion failed: {res.stderr}")

        # Check atomic promotion outcomes
        self.assertFalse(os.path.exists(next_efi), "Staged next.efi should be moved")
        self.assertTrue(os.path.exists(default_efi), "Default mios.efi must exist")
        with open(default_efi, "r") as f:
            self.assertEqual(f.read().strip(), "NEW_STAGED_EFI_BINARY_V2")

        self.assertFalse(os.path.exists(next_conf), "mios-next.conf should be cleaned up")
        self.assertTrue(os.path.exists(default_conf), "mios.conf must exist")

        with open(loader_conf, "r") as f:
            content = f.read()
            self.assertIn("default mios.conf", content)


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestUkiPromote)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
