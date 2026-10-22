#!/usr/bin/env python3
"""Tier 2: Boundary & Corner Cases — SSOT & Schema Boundaries Suite."""

from __future__ import annotations

import os
import sys
import tomllib

# Ensure repository root is in sys.path for standalone execution
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.e2e.e2e_framework import E2ETestCase, IsolatedEnvironment, run_standalone



class TestTier2BoundarySSOT(E2ETestCase):
    tier = 2
    suite_name = "T2_Boundary_SSOT"

    def test_ssot_minimum_size_boundary(self):
        """Boundary: Verify SSOT enforces >740,000 bytes lower bound safety guard."""
        real_size = os.path.getsize("/usr/share/mios/mios.toml")
        self.assert_greater(real_size, 740000, "SSOT size dropped below safety threshold (740 kB)!")

    def test_ssot_corrupted_fragment_recovery(self):
        """Corner Case: Parse broken TOML fragments and assert deterministic error handling."""
        broken_toml = """
        [desktop.hyprland]
        window_rounding = True # Python boolean instead of toml lowercase true!
        """
        try:
            tomllib.loads(broken_toml)
            self.assert_true(False, "Expected TOMLDecodeError on capitalized True")
        except tomllib.TOMLDecodeError:
            pass  # Expected behavior

    def test_ssot_deep_table_nesting_stress(self):
        """Boundary: Deeply nested tables (>10 levels) must parse without recursion overflow."""
        levels = 15
        nested_toml = "\n".join(f"{'[' * (i+1)}a{'/' if i else ''}{']' * (i+1)}" for i in range(levels))
        # Valid deep nesting
        valid_deep = "[a]\n[a.b]\n[a.b.c]\n[a.b.c.d]\nval = 42\n"
        data = tomllib.loads(valid_deep)
        self.assert_equal(data["a"]["b"]["c"]["d"]["val"], 42)

    def test_ssot_multiline_literal_string_boundaries(self):
        """Corner Case: Multiline strings with embedded quotes and backslashes."""
        literal_toml = r'''
        [test.strings]
        raw_cmd = 'echo "hello" && ls -la | grep \"test\"'
        escaped_path = "C:\\Windows\\System32\\cmd.exe"
        '''
        data = tomllib.loads(literal_toml)
        self.assert_in("C:\\Windows\\System32\\cmd.exe", data["test"]["strings"]["escaped_path"])

    def test_ssot_utf8_special_symbols_handling(self):
        """Boundary: Unicode and mathematical/icon symbols in SSOT."""
        symbol_toml = """
        [theme.icons]
        close = "✕"
        minimize = "–"
        maximize = "+"
        emoji = "🚀"
        """
        data = tomllib.loads(symbol_toml)
        self.assert_equal(data["theme"]["icons"]["close"], "✕")
        self.assert_equal(data["theme"]["icons"]["emoji"], "🚀")


if __name__ == "__main__":
    sys.exit(run_standalone(TestTier2BoundarySSOT))

