#!/usr/bin/env python3
# AI-hint: Automated unit test suite for task_501 Headless QEMU Syzkaller / KASAN Fuzz Harness.
# AI-related: usr/libexec/mios/kernel/fuzz_harness.py, tests/test-kernel-fuzz.py
"""Automated unit test suite for Headless QEMU Kernel & eBPF Fuzz Harness (task_501)."""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_MODULE_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "kernel", "fuzz_harness.py")

spec = importlib.util.spec_from_file_location("fuzz_harness", _MODULE_PATH)
if spec and spec.loader:
    fuzz_harness = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = fuzz_harness
    spec.loader.exec_module(fuzz_harness)
else:
    raise ImportError(f"Could not load fuzz_harness module from {_MODULE_PATH}")

class TestKernelFuzz(unittest.TestCase):
    """Validates Syzkaller configuration synthesis, serial crash parsing, and fuzz execution."""

    def setUp(self) -> None:
        self.harness = fuzz_harness.KernelFuzzHarness(mock=True)
        self.tmp_dir = tempfile.mkdtemp(prefix="mios_test_fuzz_")

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_generate_syzkaller_config(self) -> None:
        """Asserts generation of valid Syzkaller JSON config with bpf syscalls enabled."""
        cfg_obj = fuzz_harness.FuzzConfig(
            kernel_image="/boot/vmlinuz-kasan",
            subsystems=["bpf", "storage"],
            vcpus=4,
            memory_mb=4096,
        )
        out_file = os.path.join(self.tmp_dir, "syzkaller.json")
        cfg_dict = self.harness.generate_syzkaller_config(cfg_obj, output_path=out_file)

        self.assertEqual(cfg_dict["target"], "linux/amd64")
        self.assertEqual(cfg_dict["procs"], 4)
        self.assertIn("bpf$*", cfg_dict["enable_syscalls"])
        self.assertTrue(os.path.exists(out_file))

    def test_parse_serial_logs_kasan(self) -> None:
        """Asserts extraction of KASAN use-after-free and panic signatures from serial logs."""
        log = """
[    1.200000] systemd[1]: Started Network.
[    2.500000] BUG: KASAN: use-after-free in bpf_prog_put+0x42/0x90
[    2.500010] Read of size 8 at addr ffff888004523010
[    2.500020] Kernel panic - not syncing: Fatal exception in interrupt
"""
        crashes = self.harness.parse_serial_logs(log)
        self.assertEqual(len(crashes), 2)
        self.assertEqual(crashes[0]["fault_type"], "use-after-free")
        self.assertIn("bpf_prog_put", crashes[0]["signature"])

    def test_run_fuzz_session_mock(self) -> None:
        """Asserts mock fuzz execution, mutation accounting, and crash detection."""
        res = self.harness.run_fuzz_session()
        self.assertEqual(res.status, "crashes_detected")
        self.assertGreater(res.mutations_count, 10000)
        self.assertGreater(res.crashes_count, 0)
        self.assertGreater(res.runtime_sec, 0.0)

    def test_cli_mock_json(self) -> None:
        """Asserts CLI execution with --mock --json."""
        with patch("sys.argv", ["fuzz_harness.py", "--mock", "--json"]):
            with patch("builtins.print") as mock_print:
                ret = fuzz_harness.main()
                self.assertEqual(ret, 0)
                mock_print.assert_called()
                parsed = json.loads(mock_print.call_args[0][0])
                self.assertEqual(parsed["status"], "crashes_detected")

if __name__ == "__main__":
    unittest.main()
