#!/usr/bin/env python3
# AI-hint: Automated unit test suite for pstore ramoops kernel crash buffer manager and extractor (task_734).
# AI-doc: usr/share/doc/mios/manual/ch45-diagnostic-tools-and-profilers.md
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_RAMOOPS_KARGS = os.path.join(_ROOT, "usr", "lib", "bootc", "kargs.d", "40-mios-ramoops.toml")
_PSTORE_SERVICE = os.path.join(_ROOT, "usr", "lib", "systemd", "system", "mios-pstore.service")
_PSTORE_BIN = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-pstore")

SIMULATED_PANIC = """[  120.450123] Kernel panic - not syncing: Fatal exception in interrupt
[  120.450145] CPU: 3 PID: 451 Comm: llama-swap Not tainted 6.12.0-mios #1
[  120.450160] RIP: 0010:[<ffffffff810a1234>]  faulting_instruction+0x14/0x30
[  120.450175] CR2: 00007f9012345000
[  120.450180] Call Trace:
[  120.450185]  <TASK>
[  120.450190]  [<ffffffff810b2345>] subfunction_a+0x25/0x50
[  120.450195]  [<ffffffff810c3456>] tensor_worker+0x110/0x200
[  120.450200]  </TASK>
[  120.450205] ---[ end trace 0000000000000000 ]---
"""


class TestPstoreCrash(unittest.TestCase):
    """Validates pstore ramoops kargs, systemd service, and extractor behavior."""

    def test_kargs_configuration(self):
        self.assertTrue(os.path.isfile(_RAMOOPS_KARGS), f"Missing {_RAMOOPS_KARGS}")
        with open(_RAMOOPS_KARGS, "r") as f:
            content = f.read()
        self.assertIn("ramoops.mem_address=0x1f0000000", content)
        self.assertIn("ramoops.mem_size=0x1000000", content)
        self.assertIn("ramoops.record_size=262144", content)
        self.assertIn("ramoops.console_size=262144", content)

    def test_systemd_service(self):
        self.assertTrue(os.path.isfile(_PSTORE_SERVICE), f"Missing {_PSTORE_SERVICE}")
        with open(_PSTORE_SERVICE, "r") as f:
            content = f.read()
        self.assertIn("ExecStart=/usr/libexec/mios/mios-pstore --extract --purge", content)
        self.assertIn("Description=MiOS Persistent Kernel Crash Buffer Extractor", content)

    def test_extractor_parsing_and_purge(self):
        self.assertTrue(os.path.isfile(_PSTORE_BIN), f"Missing {_PSTORE_BIN}")
        self.assertTrue(os.access(_PSTORE_BIN, os.X_OK), f"Not executable {_PSTORE_BIN}")

        with tempfile.TemporaryDirectory() as tmp_pstore, tempfile.TemporaryDirectory() as tmp_archive:
            fake_dump = os.path.join(tmp_pstore, "dmesg-ramoops-0")
            with open(fake_dump, "w") as f:
                f.write(SIMULATED_PANIC)

            res = subprocess.run(
                [
                    sys.executable,
                    _PSTORE_BIN,
                    "--pstore-dir",
                    tmp_pstore,
                    "--archive-dir",
                    tmp_archive,
                    "--extract",
                    "--purge",
                    "--json",
                ],
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(res.stdout)
            self.assertEqual(data.get("dumps_found"), 1)
            panics = data.get("panics", [])
            self.assertEqual(len(panics), 1)

            entry = panics[0]
            parsed = entry.get("parsed", {})
            self.assertTrue(parsed.get("is_panic"))
            self.assertIn("Fatal exception in interrupt", parsed.get("reason", ""))
            self.assertEqual(len(parsed.get("backtrace", [])), 2)
            self.assertEqual(parsed.get("registers", {}).get("CR2"), "00007f9012345000")

            # Verify file was purged from pstore
            self.assertTrue(entry.get("purged"))
            self.assertFalse(os.path.exists(fake_dump), "Dump file was not purged")

            # Verify file was archived
            archived = glob_files = os.listdir(tmp_archive)
            self.assertEqual(len(archived), 1)
            self.assertTrue(archived[0].endswith("dmesg-ramoops-0"))


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestPstoreCrash)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
