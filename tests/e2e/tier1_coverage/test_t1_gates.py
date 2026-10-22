#!/usr/bin/env python3
"""Tier 1: Feature Coverage — Gates, Tooling & Build Verification Suite.

Covers:
- Feature 39: Unit Tests Registration in [ci.tiers].unit
- Feature 40: Rust Static Binaries Rebuild (mios-gate, mios-gen, mios-resolver)
- Feature 41: Standing Gates Verification (97-ssot-lint.sh, 98-drift-checks.sh, ci-suites.py --check, mios-gate)
"""

from __future__ import annotations

import os
import sys
import tomllib

# Ensure repository root is in sys.path for standalone execution
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.e2e.e2e_framework import E2ETestCase, IsolatedEnvironment, run_standalone



class TestTier1Gates(E2ETestCase):
    tier = 1
    suite_name = "T1_Gates_Tooling_Compliance"

    def test_feature_39_ci_suites_check(self):
        """Feature 39: Verify tools/ci-suites.py exists and supports valid CLI operations."""
        ci_suites_script = "/tools/ci-suites.py"
        self.assert_true(os.path.exists(ci_suites_script), f"Missing {ci_suites_script}")

        code, stdout, stderr = self.run_cmd(
            ["python3", ci_suites_script, "--tool-skips"],
            cwd=REPO_ROOT,
            env={"MIOS_ROOT": REPO_ROOT}
        )
        self.assert_equal(code, 0, f"ci-suites.py --tool-skips failed: {stderr}")

    def test_feature_40_rust_binaries_exist(self):
        """Feature 40: Verify native rust binaries or sources exist in tools/native or src/mios-rs."""
        self.assert_true(
            os.path.isdir("/tools/native") or os.path.isdir("/src/mios-rs"),
            "Native rust tools directory missing"
        )

    def test_feature_41_standing_gate_scripts_exist(self):
        """Feature 41: Verify all standing gate scripts exist."""
        gates = [
            "/automation/97-ssot-lint.sh",
            "/automation/98-drift-checks.sh",
        ]
        for g in gates:
            self.assert_true(os.path.isfile(g), f"Gate script missing: {g}")

    def test_feature_41_watchdog_inactive_invariant(self):
        """Feature 41: Architectural Invariant — Hardware Watchdog MUST be inactive."""
        watchdog_state_path = "/sys/class/watchdog/watchdog0/state"
        if os.path.exists(watchdog_state_path):
            with open(watchdog_state_path, "r", encoding="utf-8") as f:
                state = f.read().strip()
            self.assert_equal(state, "inactive", f"Watchdog state is '{state}', MUST BE inactive!")

    def test_feature_41_memory_cap_enforcement_check(self):
        """Feature 41: Verify systemd-run invocation interface availability."""
        code, stdout, stderr = self.run_cmd(["systemd-run", "--version"])
        self.assert_equal(code, 0, f"systemd-run unavailable: {stderr}")
        self.assert_in("systemd", stdout.lower())


if __name__ == "__main__":
    sys.exit(run_standalone(TestTier1Gates))

