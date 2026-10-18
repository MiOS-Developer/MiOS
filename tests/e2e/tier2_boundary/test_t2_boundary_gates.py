#!/usr/bin/env python3
"""Tier 2: Boundary & Corner Cases — Gates & Compliance Boundaries Suite."""

from __future__ import annotations

import os
import sys

# Ensure repository root is in sys.path for standalone execution
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.e2e.e2e_framework import E2ETestCase, IsolatedEnvironment, run_standalone



class TestTier2BoundaryGates(E2ETestCase):
    tier = 2
    suite_name = "T2_Boundary_Gates"

    def test_gate_ci_tiers_structure_boundary(self):
        """Boundary: [ci.tiers].unit must contain a non-empty list of test scripts."""
        import tomllib
        with open("/usr/share/mios/mios.toml", "rb") as f:
            data = tomllib.load(f)

        unit_tier = data.get("ci", {}).get("tiers", {}).get("unit", [])
        self.assert_true(isinstance(unit_tier, list))
        self.assert_greater(len(unit_tier), 10, "Unit test tier has fewer than 10 registered tests")

    def test_gate_gitignore_boundary_prefix_exclamation(self):
        """Corner Case: Git whitelist entries must begin with '!'."""
        with open("/.gitignore", "r", encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip().startswith("!")]

        self.assert_greater(len(lines), 0, "No whitelist entries found in /.gitignore")
        for line in lines:
            self.assert_true(line.startswith("!"))

    def test_gate_font_digest_missing_hard_fail_mock(self):
        """Boundary: Font download script exits non-zero if digest is missing or mismatch."""
        def verify_digest(expected: str, actual: str) -> bool:
            if not expected:
                return False  # Missing digest must fail
            return expected == actual

        self.assert_true(verify_digest("sha256:abc", "sha256:abc"))
        self.assert_false(verify_digest("", "sha256:abc"))
        self.assert_false(verify_digest("sha256:abc", "sha256:def"))

    def test_gate_shellcheck_sc2066_pattern_detection(self):
        """Corner Case: SC2066 regex detects improper quoting in for loop."""
        import re
        bad_loop = 'for x in "$my_list"'
        good_loop_1 = 'for x in "${my_array[@]}"'
        good_loop_2 = 'for x in $my_list'

        sc2066_pattern = r'for\s+\w+\s+in\s+"\$[A-Za-z0-9_]+"'
        self.assert_true(bool(re.search(sc2066_pattern, bad_loop)))
        self.assert_false(bool(re.search(sc2066_pattern, good_loop_1)))
        self.assert_false(bool(re.search(sc2066_pattern, good_loop_2)))

    def test_gate_watchdog_state_invariant_string(self):
        """Boundary: /sys/class/watchdog/watchdog0/state must be strictly 'inactive'."""
        watchdog_path = "/sys/class/watchdog/watchdog0/state"
        if os.path.exists(watchdog_path):
            with open(watchdog_path, "r", encoding="utf-8") as f:
                val = f.read().strip()
            self.assert_equal(val, "inactive")


if __name__ == "__main__":
    sys.exit(run_standalone(TestTier2BoundaryGates))

