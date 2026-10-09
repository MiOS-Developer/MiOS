#!/usr/bin/env python3
# AI-hint: Automated unit test suite for Kernel Livepatching & MOK Signature Verification (task_625, task_626).
# AI-related: usr/libexec/mios/kernel/kpatch_mgr.py, tests/test-kpatch-mgr.py
"""Automated unit test suite for MiOS Kernel Livepatch Manager."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "kernel"))

from kpatch_mgr import MAX_PATCH_LATENCY_MS, KernelLivepatchManager

class TestKpatchMgr(unittest.TestCase):
    def setUp(self):
        self.mgr = KernelLivepatchManager(dry_run=True)

    def test_signed_livepatch_applies_under_100ms(self):
        """Test signed livepatch verifies MOK signature and redirects in <100ms."""
        res = self.mgr.apply_signed_livepatch("CVE-2026-3388", "sys_bpf_check", mock_is_signed=True)
        self.assertTrue(res.is_applied)
        self.assertTrue(res.ftrace_redirected)
        self.assertLess(res.patch_latency_ms, MAX_PATCH_LATENCY_MS)

    def test_unsigned_livepatch_rejected(self):
        """Test unsigned livepatch is strictly rejected."""
        res = self.mgr.apply_signed_livepatch("CVE-2026-9999", "kernel_execve", mock_is_signed=False)
        self.assertFalse(res.is_applied)
        self.assertFalse(res.is_mok_signed)

if __name__ == "__main__":
    unittest.main()
