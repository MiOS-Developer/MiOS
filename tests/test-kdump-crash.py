#!/usr/bin/env python3
# AI-hint: Automated unit test suite for reserved memory kdump and zstd crash dump extraction (task_459).
# AI-doc: usr/share/doc/mios/manual/ch45-diagnostic-tools-and-profilers.md
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_KDUMP_BIN = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-kdump")


class TestKdumpCrash(unittest.TestCase):
    """Validates kdump configuration, kargs, phase script, and dump compression."""

    def test_kargs_file_present(self):
        kargs_path = os.path.join(_ROOT, "usr", "lib", "bootc", "kargs.d", "41-mios-kdump.toml")
        self.assertTrue(os.path.isfile(kargs_path), f"Missing {kargs_path}")
        content = pathlib.Path(kargs_path).read_text(encoding="utf-8")
        self.assertIn("crashkernel=256M", content)

    def test_kdump_conf_content(self):
        conf_path = os.path.join(_ROOT, "etc", "kdump.conf")
        self.assertTrue(os.path.isfile(conf_path), f"Missing {conf_path}")
        content = pathlib.Path(conf_path).read_text(encoding="utf-8")
        self.assertIn("path /var/crash", content)
        self.assertIn("makedumpfile", content)
        self.assertIn("extra_modules zstd", content)
        self.assertIn("default reboot", content)

    def test_tmpfiles_crash_dir(self):
        tmpfiles_path = os.path.join(_ROOT, "usr", "lib", "tmpfiles.d", "mios-kdump.conf")
        self.assertTrue(os.path.isfile(tmpfiles_path), f"Missing {tmpfiles_path}")
        content = pathlib.Path(tmpfiles_path).read_text(encoding="utf-8")
        self.assertIn("d /var/crash", content)

    def test_phase_script_present(self):
        phase_path = os.path.join(_ROOT, "automation", "28-kdump-config.sh")
        self.assertTrue(os.path.isfile(phase_path), f"Missing {phase_path}")
        self.assertTrue(os.access(phase_path, os.X_OK))

    def test_kdump_cli_status(self):
        env = os.environ.copy()
        env["MIOS_ROOT"] = _ROOT
        res = subprocess.run([_KDUMP_BIN, "status", "--json"], env=env, capture_output=True, text=True)
        self.assertEqual(res.returncode, 0, f"Error: {res.stderr}")
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "ready")
        self.assertTrue(data.get("crashkernel_reserved"))
        self.assertTrue(data.get("config_present"))
        self.assertEqual(data.get("compression"), "zstd")

    def test_kdump_cli_compress(self):
        env = os.environ.copy()
        env["MIOS_ROOT"] = _ROOT
        with tempfile.TemporaryDirectory() as td:
            dummy_core = os.path.join(td, "vmcore-test")
            with open(dummy_core, "wb") as f:
                f.write(b"MOCK_VMCORE_HEADER_DATA" * 500)
            self.assertTrue(os.path.isfile(dummy_core))

            res = subprocess.run([_KDUMP_BIN, "compress", dummy_core, "--json"], env=env, capture_output=True, text=True)
            self.assertEqual(res.returncode, 0, f"Error: {res.stderr}")
            data = json.loads(res.stdout)
            self.assertEqual(data.get("status"), "success")
            out_file = data.get("compressed_path")
            self.assertTrue(os.path.isfile(out_file))
            self.assertFalse(os.path.isfile(dummy_core), "Raw uncompressed core should be removed")


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestKdumpCrash)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
