#!/usr/bin/env python3
# AI-hint: Automated unit test suite for Native Kernel Rootless Overlay Storage (task_649, task_650).
# AI-related: usr/libexec/mios/containers/native_storage.py, tests/test-native-storage.py
"""Automated unit test suite for MiOS Podman Native Storage Configurator."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "containers"))

from native_storage import PodmanStorageConfigurator

class TestNativeStorage(unittest.TestCase):
    def setUp(self):
        self.cfg = PodmanStorageConfigurator(dry_run=True)

    def test_storage_conf_generation_enforces_native_overlay(self):
        """Test generated storage.conf specifies driver=overlay and empty mount_program."""
        conf = self.cfg.generate_storage_conf()
        self.assertIn('driver = "overlay"', conf)
        self.assertIn('mount_program = ""', conf)
        self.assertIn("metacopy=on", conf)

    def test_native_overlay_performance_speedup_target(self):
        """Test native kernel overlay achieves >10x IOPS speedup over FUSE."""
        res = self.cfg.evaluate_driver_performance()
        self.assertTrue(res.is_native_kernel)
        self.assertGreaterEqual(res.estimated_iops_speedup, 10.0)

if __name__ == "__main__":
    unittest.main()
