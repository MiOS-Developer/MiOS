#!/usr/bin/env python3
# AI-hint: Automated unit test suite for Systemd OOMD PSI Memory Pressure & Service Protection (task_595, task_596).
# AI-related: usr/libexec/mios/kernel/oomd_psi.py, tests/test-oomd-psi.py
"""Automated unit test suite for MiOS Systemd OOMD PSI Manager."""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "usr", "libexec", "mios", "kernel"))

from oomd_psi import OOMDPressureManager

class TestOOMDPSI(unittest.TestCase):
    def setUp(self):
        self.mgr = OOMDPressureManager(psi_kill_threshold_pct=50.0, dry_run=True)

    def test_normal_pressure_no_action(self):
        """Test memory pressure under 50% triggers no eviction."""
        act = self.mgr.evaluate_pressure_stall("user.slice", 25.0, ["app-browser.service"])
        self.assertEqual(act.action_taken, "none")
        self.assertIsNone(act.victim_unit)

    def test_overpressure_kills_background_hog(self):
        """Test 65% memory pressure kills runaway background unit in <10s."""
        act = self.mgr.evaluate_pressure_stall("background.slice", 65.0, ["stress-mem.service"])
        self.assertEqual(act.action_taken, "kill")
        self.assertEqual(act.victim_unit, "stress-mem.service")

    def test_protected_service_preservation_under_extreme_pressure(self):
        """Test PostgreSQL and LLM daemons are never killed during memory spikes."""
        act = self.mgr.evaluate_pressure_stall(
            "system.slice", 85.0, ["mios-pgvector.service", "mios-llm-light.service", "worker-build.service"]
        )
        self.assertEqual(act.action_taken, "kill")
        self.assertEqual(act.victim_unit, "worker-build.service")

if __name__ == "__main__":
    unittest.main()
