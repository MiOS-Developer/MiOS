#!/usr/bin/env python3
# AI-hint: Automated unit test suite for THP madvise and memory compaction tuning (task_744).
# AI-doc: usr/share/doc/mios/manual/ch22-cpu-topology-and-performance-pinning.md
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_SYSCTL_USR = os.path.join(_ROOT, "usr", "lib", "sysctl.d", "20-memory.conf")
_SYSCTL_ETC = os.path.join(_ROOT, "etc", "sysctl.d", "20-memory.conf")
_TMPFILES_THP = os.path.join(_ROOT, "usr", "lib", "tmpfiles.d", "mios-thp.conf")
_THP_TUNE = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-thp-tune")


class TestThpCompaction(unittest.TestCase):
    """Validates THP madvise and compaction configuration files and tuner."""

    def test_sysctl_configurations(self):
        for path in [_SYSCTL_USR, _SYSCTL_ETC]:
            self.assertTrue(os.path.isfile(path), f"Missing {path}")
            with open(path, "r") as f:
                content = f.read()
            self.assertIn("vm.compaction_proactiveness = 20", content)

    def test_tmpfiles_configuration(self):
        self.assertTrue(os.path.isfile(_TMPFILES_THP), f"Missing {_TMPFILES_THP}")
        with open(_TMPFILES_THP, "r") as f:
            content = f.read()
        self.assertIn("madvise", content)
        self.assertIn("defer+madvise", content)
        self.assertIn("10000", content)

    def test_thp_tune_binary(self):
        self.assertTrue(os.path.isfile(_THP_TUNE), f"Missing {_THP_TUNE}")
        self.assertTrue(os.access(_THP_TUNE, os.X_OK), f"Not executable {_THP_TUNE}")

        res = subprocess.run(
            [sys.executable, _THP_TUNE, "--dry-run", "--apply", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "success")
        self.assertTrue(data.get("dry_run"))

        knobs = data.get("knobs", {})
        self.assertEqual(knobs.get("enabled", {}).get("target"), "madvise")
        self.assertEqual(knobs.get("defrag", {}).get("target"), "defer+madvise")
        self.assertEqual(knobs.get("compaction_proactiveness", {}).get("target"), "20")


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestThpCompaction)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
