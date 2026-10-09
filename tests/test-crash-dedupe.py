#!/usr/bin/env python3
# AI-hint: Automated unit test suite for task_502 Fuzz Crash Deduplication & Bug Logger.
# AI-related: usr/libexec/mios/kernel/crash_dedupe.py, tests/test-crash-dedupe.py
"""Automated unit test suite for Kernel Fuzz Crash Deduplication & Bug Logger (task_502)."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import unittest
from unittest.mock import patch

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_MODULE_PATH = os.path.join(_ROOT, "usr", "libexec", "mios", "kernel", "crash_dedupe.py")

spec = importlib.util.spec_from_file_location("crash_dedupe", _MODULE_PATH)
if spec and spec.loader:
    crash_dedupe = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = crash_dedupe
    spec.loader.exec_module(crash_dedupe)
else:
    raise ImportError(f"Could not load crash_dedupe module from {_MODULE_PATH}")

class TestCrashDedupe(unittest.TestCase):
    """Validates stack normalization, hash deduplication, reproducer extraction, and bug recording."""

    def setUp(self) -> None:
        self.engine = crash_dedupe.CrashTriageEngine(mock=True)

    def test_normalize_frame(self) -> None:
        """Asserts stripping of runtime offsets and memory pointers."""
        raw_frame = "  [  12.450] ? bpf_prog_put+0x42/0x90 [<ffffffff81234567>]"
        norm = self.engine.normalize_frame(raw_frame)
        self.assertEqual(norm, "bpf_prog_put")

    def test_compute_stack_hash_invariance(self) -> None:
        """Asserts that identical callstack with different instruction offsets produces identical hash."""
        stack_1 = ["bpf_prog_put+0x10/0x80", "bpf_prog_release+0x05/0x20", "ksys_close+0x14/0x50"]
        stack_2 = ["bpf_prog_put+0x42/0x90", "bpf_prog_release+0x18/0x20", "ksys_close+0x48/0x90"]

        hash_1 = self.engine.compute_stack_hash(stack_1)
        hash_2 = self.engine.compute_stack_hash(stack_2)
        self.assertEqual(hash_1, hash_2)

    def test_parse_log_kasan_trace(self) -> None:
        """Asserts parsing of realistic KASAN crash log and field extraction."""
        log = """
[    3.412005] BUG: KASAN: use-after-free in bpf_prog_put+0x42/0x90
[    3.412010] Read of size 8 at addr ffff888004523010 by task syz-executor/1420
[    3.412025] Call Trace:
[    3.412045]  bpf_prog_put+0x42/0x90
[    3.412050]  bpf_prog_release+0x18/0x20
[    3.412055]  __fput+0x105/0x3a0
==================================================================
# syz-reproducer:
bpf$BPF_PROG_LOAD(0x1, &(0x7f0000)=..., 0x80)
"""
        reports = self.engine.parse_log(log)
        self.assertEqual(len(reports), 1)
        rep = reports[0]
        self.assertEqual(rep.subsystem, "bpf")
        self.assertEqual(rep.fault_type, "use-after-free")
        self.assertIn("bpf_prog_put", rep.stack_frames)
        self.assertIsNotNone(rep.syz_reproducer)

    def test_record_to_db_increment(self) -> None:
        """Asserts that duplicate crash occurrence count increments properly in database."""
        report = crash_dedupe.CrashReport(
            crash_hash="test_unique_hash_99",
            title="KASAN: slab-out-of-bounds in virtiofs_read",
            subsystem="virtiofs",
            fault_type="slab-out-of-bounds",
            stack_frames=["virtiofs_read", "vfs_read"],
        )

        res1 = self.engine.record_to_db(report)
        self.assertEqual(res1["status"], "inserted")
        self.assertEqual(res1["occurrences"], 1)

        res2 = self.engine.record_to_db(report)
        self.assertEqual(res2["status"], "updated")
        self.assertEqual(res2["occurrences"], 2)

    def test_cli_list_json(self) -> None:
        """Asserts CLI execution with --list --mock --json."""
        with patch("sys.argv", ["crash_dedupe.py", "--list", "--mock", "--json"]):
            with patch("builtins.print") as mock_print:
                ret = crash_dedupe.main()
                self.assertEqual(ret, 0)
                mock_print.assert_called()
                parsed = json.loads(mock_print.call_args[0][0])
                self.assertEqual(parsed["status"], "ok")
                self.assertIn("bugs", parsed)

if __name__ == "__main__":
    unittest.main()
