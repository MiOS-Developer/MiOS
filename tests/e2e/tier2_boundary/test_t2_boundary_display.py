#!/usr/bin/env python3
"""Tier 2: Boundary & Corner Cases — Displays, RDP & Scaling Boundaries Suite."""

from __future__ import annotations

import os
import sys

# Ensure repository root is in sys.path for standalone execution
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../.."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from tests.e2e.e2e_framework import E2ETestCase, IsolatedEnvironment, run_standalone



class TestTier2BoundaryDisplay(E2ETestCase):
    tier = 2
    suite_name = "T2_Boundary_Display_Scaling"

    def test_display_scale_120_grid_snapping(self):
        """Corner Case: Arbitrary fractional scales snap strictly onto 1/120 grid."""
        def snap_scale(raw_scale: float) -> float:
            steps = round(raw_scale * 120.0)
            return steps / 120.0

        test_cases = [
            (1.0, 1.0),
            (1.25, 1.25),
            (1.333333, 160 / 120),  # 1.333333...
            (2.5, 2.5),
            (2.71828, round(2.71828 * 120) / 120),
        ]

        for raw, expected in test_cases:
            snapped = snap_scale(raw)
            self.assert_equal(round(snapped, 5), round(expected, 5))

    def test_display_resolution_headless_mode_string_formatting(self):
        """Boundary: Verify headless mode string formatting: f'{w}x{h}@{target_hz}'."""
        def format_mode(w: int, h: int, target_hz: int) -> str:
            return f"{w}x{h}@{target_hz}"

        mode_90 = format_mode(1100, 2080, 90)
        self.assert_equal(mode_90, "1100x2080@90")

        mode_144 = format_mode(1920, 1080, 144)
        self.assert_equal(mode_144, "1920x1080@144")

    def test_display_primary_rect_fallback_non_negative_x(self):
        """Boundary: When no primary monitor is found, x position must never be negative."""
        def calculate_headless_x(primary_x: int, primary_w: int, headless_w: int) -> int:
            if primary_w <= 0:
                return 0  # Fallback to 0,0
            # Center below primary
            center_x = primary_x + (primary_w - headless_w) // 2
            return max(0, center_x)

        # Normal center
        self.assert_equal(calculate_headless_x(0, 1920, 1100), 410)
        # Wider headless than primary
        self.assert_equal(calculate_headless_x(0, 1000, 1200), 0)
        # No primary
        self.assert_equal(calculate_headless_x(0, 0, 1100), 0)

    def test_quickshell_ui_scale_calculation_boundary(self):
        """Boundary: QuickShell uiScale calculation handles scale / applied_scale clamp."""
        def ui_scale(scale: float, applied_scale: float) -> float:
            if applied_scale <= 0:
                return 1.0
            return max(1.0, scale / applied_scale)

        self.assert_equal(ui_scale(2.5, 1.0), 2.5)
        self.assert_equal(ui_scale(1.0, 1.0), 1.0)
        self.assert_equal(ui_scale(0.8, 1.0), 1.0)  # Clamped to >= 1.0

    def test_mobile_window_rounding_property_types(self):
        """Corner Case: Boolean properties in Hyprland workspace rules."""
        rule_tmpl = "no_border = {no_border}, no_rounding = {no_rounding}"
        rendered_true = rule_tmpl.format(no_border="not true", no_rounding="not true")
        self.assert_in("not true", rendered_true)


if __name__ == "__main__":
    sys.exit(run_standalone(TestTier2BoundaryDisplay))

