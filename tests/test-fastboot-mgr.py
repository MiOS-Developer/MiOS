#!/usr/bin/env python3
# AI-hint: Automated unit test suite for Systemd-Boot Fastboot & Emergency Rescue Fallback (task_643, task_644).
# AI-related: usr/libexec/mios/boot/fastboot_mgr.py, tests/test-fastboot-mgr.py
"""Automated unit test suite for MiOS Fastboot Manager."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "boot"))

from fastboot_mgr import MAX_LOADER_TIME_MS, FastbootManager

class TestFastbootMgr(unittest.TestCase):
    def setUp(self):
        self.mgr = FastbootManager(dry_run=True)

    def test_zero_timeout_loader_conf_generation(self):
        """Test generated loader.conf enforces timeout 0 and disables interactive editor."""
        conf = self.mgr.generate_loader_conf()
        self.assertIn("timeout 0", conf)
        self.assertIn("editor no", conf)

    def test_sub_300ms_firmware_handoff_latency(self):
        """Test direct UKI boot executes in <300ms."""
        res = self.mgr.simulate_boot_cycle(is_emergency_key_pressed=False)
        self.assertEqual(res["action"], "direct_boot_signed_uki")
        self.assertTrue(res["is_sub_300ms"])
        self.assertLess(res["loader_time_ms"], MAX_LOADER_TIME_MS)

    def test_emergency_key_opens_recovery_menu(self):
        """Test holding Space/Esc keys triggers emergency rollback menu."""
        res = self.mgr.simulate_boot_cycle(is_emergency_key_pressed=True)
        self.assertEqual(res["action"], "display_emergency_recovery_menu")

if __name__ == "__main__":
    unittest.main()
