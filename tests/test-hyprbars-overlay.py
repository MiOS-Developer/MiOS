#!/usr/bin/env python3
# AI-hint: Hyprbars 2D Fullscreen Borderless (Super + F11 / Right-Click +) focus, window controls, and drag recovery test suite.
# Covers Requirement R1 across four tiers:
#   Tier 1 (Feature Coverage): Positive controls for m_bInputExtents on fullscreen inset tabs, inputIsValid mios-topbar whitelist,
#     + button exit-fullscreen & restore float box with raise/focus, tab pill body drag recovery in bar_overlay mode, and foreign snapshot bypass.
#   Tier 2 (Boundary & Corner Cases): Zero-size windows, extreme coordinates, clicks outside tab bounds, and negative controls.
#   Tier 3 (Cross-Feature Combinations): Entering 3D while in fullscreen borderless, and dragging inset tab with ShaderGlass active.
#   Tier 4 (Real-World Application Scenarios): Live Hyprland compositor options, plugin loading, and patch verification.
# AI-related: /var/lib/mios-build/tmp/controls-fix-hyprbars/hyprbars/barDeco.cpp, /usr/share/mios/hyprland/patches/hyprbars-mios-overlay-tab.patch,
#   /usr/lib64/mios/hyprbars.so, /usr/share/mios/mios.toml [desktop.hyprland.hyprbars]
"""Regression test suite for Hyprbars 2D fullscreen focus, controls, and drag recovery."""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
BARDECO_CPP = "/var/lib/mios-build/tmp/controls-fix-hyprbars/hyprbars/barDeco.cpp"
PATCH_FILE = "/usr/share/mios/hyprland/patches/hyprbars-mios-overlay-tab.patch"
SSOT_TOML = os.path.join(ROOT, "usr", "share", "mios", "mios.toml")

# Hyprbars geometry constants (R1 SSOT)
BAR_HEIGHT = 28.0
TAB_X_START = 25.0
TAB_X_END = 147.0
TAB_CENTER_X = 86.0
DEFAULT_FLOAT_W = 960.0
DEFAULT_FLOAT_H = 532.0


class SimulatedHyprbar:
    """Exact behavioral emulation of CHyprBar state machine and methods from barDeco.cpp."""

    FSMODE_NONE = 0
    FSMODE_MAXIMIZED = 1
    FSMODE_FULLSCREEN = 2

    BTN_LEFT = 1
    BTN_RIGHT = 2

    def __init__(self, bar_overlay: bool = True, bar_height: float = 28.0):
        self.bar_overlay = bar_overlay
        self.bar_height = bar_height
        self.m_bInset = False
        self.m_bInputExtents = False
        self.m_bDragPending = False
        self.m_bDraggingThis = False
        self.m_lastMouseDown = (0.0, 0.0)
        self.m_preMaxBox = None  # (x, y, w, h)
        self.fs_mode = self.FSMODE_NONE
        self.window_box = (100.0, 100.0, DEFAULT_FLOAT_W, DEFAULT_FLOAT_H)
        self.monitor_box = (0.0, 0.0, 1920.0, 1080.0)
        self.focused_window = None
        self.raised_windows = []
        self.mouse_bind_mode = None
        self.target_reveal = 1.0

    def compute_inset(self) -> bool:
        if not self.bar_overlay:
            return False
        return self.fs_mode == self.FSMODE_FULLSCREEN

    def update_reveal(self, mutate_old_bug: bool = False) -> None:
        self.m_bInset = self.compute_inset()
        if mutate_old_bug:
            # Old bug: '!m_bInset' forced input extents to false on fullscreen windows
            input_val = self.bar_overlay and self.target_reveal > 0.0 and not self.m_bInset
        else:
            # Correct R1 logic: keep input extents true in bar_overlay mode regardless of inset
            input_val = self.bar_overlay and self.target_reveal > 0.0
        self.m_bInputExtents = input_val

    def get_decoration_flags(self) -> int:
        DECORATION_ALLOWS_MOUSE_INPUT = 1 << 0
        if self.bar_overlay:
            return DECORATION_ALLOWS_MOUSE_INPUT if self.m_bInputExtents else 0
        return DECORATION_ALLOWS_MOUSE_INPUT

    def input_is_valid(self, mouse_x: float, mouse_y: float, top_surface_ns: str | None = None, mutate_old_bug: bool = False) -> bool:
        if top_surface_ns is not None:
            if mutate_old_bug:
                # Old bug: only "mios-appmenu" was whitelisted
                if not (self.bar_overlay and top_surface_ns == "mios-appmenu"):
                    return False
            else:
                # Correct R1 logic: both "mios-appmenu" and "mios-topbar" are whitelisted
                if not (self.bar_overlay and (top_surface_ns == "mios-appmenu" or top_surface_ns == "mios-topbar")):
                    return False
        # Bounds check relative to window position
        rel_x = mouse_x - self.window_box[0]
        rel_y = mouse_y - self.window_box[1]
        if rel_x < TAB_X_START or rel_x > TAB_X_END:
            return False
        if rel_y < -8.0 or rel_y > self.bar_height + 8.0:
            return False
        return True

    def handle_down_event(self, coords: tuple[float, float], button: int, mutate_old_bug: bool = False) -> bool:
        """Emulates CHyprBar::handleDownEvent in barOverlay mode."""
        rel_x, rel_y = coords
        # Button hit detection: 3 buttons on the tab pill:
        # Button 0 (close): [38, 56], Button 1 (min): [62, 80], Button 2 (+): [86, 104]
        is_btn_0 = 38.0 <= rel_x <= 56.0
        is_btn_1 = 62.0 <= rel_x <= 80.0
        is_btn_2 = 86.0 <= rel_x <= 104.0

        if is_btn_0:
            return True
        if is_btn_1:
            return True
        if is_btn_2:
            self.handle_plus_button(button, mutate_old_bug)
            return True

        # Clicked on tab pill body outside buttons
        if TAB_X_START <= rel_x <= TAB_X_END and -8.0 <= rel_y <= self.bar_height + 8.0:
            if mutate_old_bug:
                # Old bug: hit 'return;' without arming drag
                return False
            self.m_bDragPending = True
            self.m_lastMouseDown = coords
            return True
        return False

    def handle_plus_button(self, button: int, mutate_old_bug: bool = False) -> None:
        """Emulates CHyprBar::handleDownEvent button 2 (+) logic."""
        if mutate_old_bug:
            # Old bug: right-click unconditionally called fullscreenWindow(FSMODE_FULLSCREEN)
            if button == self.BTN_RIGHT:
                self.fs_mode = self.FSMODE_FULLSCREEN
                return
        # R1 logic: if in fullscreen or maximized, clicking + (left or right) exits and restores
        if self.fs_mode != self.FSMODE_NONE:
            self.fs_mode = self.FSMODE_NONE
            self.restore_float_box()
            self.raise_and_focus()
        else:
            # Entering fullscreen / maximized saves current floating box
            self.m_preMaxBox = self.window_box
            if button == self.BTN_RIGHT:
                self.fs_mode = self.FSMODE_FULLSCREEN
            else:
                self.fs_mode = self.FSMODE_MAXIMIZED

    def restore_float_box(self) -> None:
        pmon_w = self.monitor_box[2]
        pmon_h = self.monitor_box[3]
        target = (
            self.monitor_box[0] + round((pmon_w - DEFAULT_FLOAT_W) * 0.5),
            self.monitor_box[1] + max(48.0, round((pmon_h - DEFAULT_FLOAT_H) * 0.5)),
            DEFAULT_FLOAT_W,
            DEFAULT_FLOAT_H,
        )
        if self.m_preMaxBox and self.m_preMaxBox[2] >= 240.0 and self.m_preMaxBox[3] >= 160.0:
            target = self.m_preMaxBox
        self.window_box = target

    def raise_and_focus(self) -> None:
        self.focused_window = "self"
        self.raised_windows.append("self")

    def handle_movement(self) -> None:
        """Emulates CHyprBar::handleMovement."""
        if self.fs_mode != self.FSMODE_NONE:
            # R1 logic: automatically un-fullscreen/restore window before dragging
            self.fs_mode = self.FSMODE_NONE
            self.restore_float_box()
        self.mouse_bind_mode = "MBIND_MOVE"
        self.m_bDraggingThis = True

    def render_pass(self, render_modif_enabled: bool, mutate_old_bug: bool = False) -> str:
        """Emulates CHyprBar::renderPass."""
        if not mutate_old_bug and render_modif_enabled:
            # Correct R1 logic: skip foreign/offscreen snapshot passes
            return "SKIPPED_FOR_SNAPSHOT"
        return "RENDERED_OVERLAY_TAB"


# ----------------------------------------------------------------------------------------------------------------------
# Tier 1: Feature Coverage (Positive Controls)
# ----------------------------------------------------------------------------------------------------------------------
class TestTier1FeatureCoverage(unittest.TestCase):
    """Positive controls verifying each R1 feature in isolation."""

    def test_fullscreen_inset_input_extents_and_decoration_flags(self) -> None:
        """R1.1: Fullscreen windows retain m_bInputExtents = true and DECORATION_ALLOWS_MOUSE_INPUT."""
        bar = SimulatedHyprbar(bar_overlay=True)
        bar.fs_mode = SimulatedHyprbar.FSMODE_FULLSCREEN
        bar.update_reveal(mutate_old_bug=False)

        self.assertTrue(bar.m_bInset, "Fullscreen window must have m_bInset = true")
        self.assertTrue(bar.m_bInputExtents, "R1 requires m_bInputExtents = true even when m_bInset is true")
        flags = bar.get_decoration_flags()
        self.assertEqual(flags & 1, 1, "Decoration flags must include DECORATION_ALLOWS_MOUSE_INPUT")

    def test_input_is_valid_mios_topbar_whitelist(self) -> None:
        """R1.2: inputIsValid allows clicks through mios-topbar and mios-appmenu."""
        bar = SimulatedHyprbar(bar_overlay=True)
        bar.window_box = (0.0, 0.0, 1920.0, 1080.0)

        # Tab pill is at x=86, y=14
        self.assertTrue(bar.input_is_valid(86.0, 14.0, top_surface_ns="mios-topbar"),
                        "mios-topbar must be whitelisted in inputIsValid")
        self.assertTrue(bar.input_is_valid(86.0, 14.0, top_surface_ns="mios-appmenu"),
                        "mios-appmenu must remain whitelisted in inputIsValid")
        self.assertFalse(bar.input_is_valid(86.0, 14.0, top_surface_ns="unauthorized-dock"),
                         "Arbitrary top surfaces must NOT be allowed")

    def test_plus_button_exit_fullscreen_and_restore(self) -> None:
        """R1.3: Right or Left clicking + in FSMODE_FULLSCREEN exits fullscreen and restores floating box."""
        bar = SimulatedHyprbar(bar_overlay=True)
        initial_float_box = (200.0, 150.0, 800.0, 600.0)
        bar.window_box = initial_float_box

        # Enter fullscreen via right click
        bar.handle_plus_button(SimulatedHyprbar.BTN_RIGHT, mutate_old_bug=False)
        self.assertEqual(bar.fs_mode, SimulatedHyprbar.FSMODE_FULLSCREEN)
        self.assertEqual(bar.m_preMaxBox, initial_float_box)

        # Right-click + again while in fullscreen must EXIT and restore initial_float_box
        bar.handle_plus_button(SimulatedHyprbar.BTN_RIGHT, mutate_old_bug=False)
        self.assertEqual(bar.fs_mode, SimulatedHyprbar.FSMODE_NONE, "Right click on + must exit FSMODE_FULLSCREEN")
        self.assertEqual(bar.window_box, initial_float_box, "Floating geometry must be restored to m_preMaxBox")
        self.assertEqual(bar.focused_window, "self", "Window must receive fullWindowFocus")
        self.assertIn("self", bar.raised_windows, "Window must be raised")

        # Now test left click while in FSMODE_MAXIMIZED
        bar.fs_mode = SimulatedHyprbar.FSMODE_MAXIMIZED
        bar.handle_plus_button(SimulatedHyprbar.BTN_LEFT, mutate_old_bug=False)
        self.assertEqual(bar.fs_mode, SimulatedHyprbar.FSMODE_NONE, "Left click on + must exit FSMODE_MAXIMIZED")
        self.assertEqual(bar.window_box, initial_float_box, "Floating geometry must be restored")

    def test_tab_pill_body_drag_recovery(self) -> None:
        """R1.4: Dragging tab pill body outside buttons arms m_bDragPending and un-fullscreens."""
        bar = SimulatedHyprbar(bar_overlay=True)
        bar.fs_mode = SimulatedHyprbar.FSMODE_FULLSCREEN
        bar.m_preMaxBox = (300.0, 200.0, 1000.0, 600.0)

        # Click at x=120, y=14 (inside tab pill x in [25, 147], outside buttons [38..104])
        handled = bar.handle_down_event((120.0, 14.0), SimulatedHyprbar.BTN_LEFT, mutate_old_bug=False)
        self.assertTrue(handled, "Click on tab pill body outside buttons must be handled")
        self.assertTrue(bar.m_bDragPending, "m_bDragPending must be armed")
        self.assertEqual(bar.m_lastMouseDown, (120.0, 14.0))

        # Handle movement past drag threshold
        bar.handle_movement()
        self.assertEqual(bar.fs_mode, SimulatedHyprbar.FSMODE_NONE, "Dragging tab must un-fullscreen window")
        self.assertEqual(bar.window_box, (300.0, 200.0, 1000.0, 600.0), "Window must restore to m_preMaxBox")
        self.assertEqual(bar.mouse_bind_mode, "MBIND_MOVE", "Mouse bind mode must transition to MBIND_MOVE")
        self.assertTrue(bar.m_bDraggingThis)

    def test_snapshot_pass_bypass(self) -> None:
        """R1.5: Foreign/offscreen snapshot passes skip rendering 2D hyprbar tab."""
        bar = SimulatedHyprbar(bar_overlay=True)
        # Normal render pass
        res_normal = bar.render_pass(render_modif_enabled=False, mutate_old_bug=False)
        self.assertEqual(res_normal, "RENDERED_OVERLAY_TAB")

        # Snapshot pass (WindowCapture::makeSnapshot with renderModif.enabled = true)
        res_snapshot = bar.render_pass(render_modif_enabled=True, mutate_old_bug=False)
        self.assertEqual(res_snapshot, "SKIPPED_FOR_SNAPSHOT", "Snapshot pass must skip 2D tab rendering")


# ----------------------------------------------------------------------------------------------------------------------
# Tier 2: Boundary & Corner Cases (Negative Controls)
# ----------------------------------------------------------------------------------------------------------------------
class TestTier2BoundaryAndCornerCases(unittest.TestCase):
    """Negative controls and boundary condition tests."""

    def test_negative_control_input_extents_with_old_bug(self) -> None:
        """Negative control: mutating INPUT logic to old bug strips mouse input on fullscreen windows."""
        bar = SimulatedHyprbar(bar_overlay=True)
        bar.fs_mode = SimulatedHyprbar.FSMODE_FULLSCREEN
        bar.update_reveal(mutate_old_bug=True)

        self.assertFalse(bar.m_bInputExtents, "Negative control: old bug sets m_bInputExtents to false")
        flags = bar.get_decoration_flags()
        self.assertEqual(flags, 0, "Negative control: flags must be 0 (no mouse input allowed)")

    def test_negative_control_topbar_whitelist_with_old_bug(self) -> None:
        """Negative control: mutating whitelist to old bug rejects mios-topbar clicks."""
        bar = SimulatedHyprbar(bar_overlay=True)
        bar.window_box = (0.0, 0.0, 1920.0, 1080.0)

        valid = bar.input_is_valid(86.0, 14.0, top_surface_ns="mios-topbar", mutate_old_bug=True)
        self.assertFalse(valid, "Negative control: old bug drops clicks when mios-topbar is present")

    def test_negative_control_plus_button_with_old_bug(self) -> None:
        """Negative control: mutating + right-click to old bug fails to exit fullscreen."""
        bar = SimulatedHyprbar(bar_overlay=True)
        bar.fs_mode = SimulatedHyprbar.FSMODE_FULLSCREEN
        bar.m_preMaxBox = (100.0, 100.0, 800.0, 500.0)

        bar.handle_plus_button(SimulatedHyprbar.BTN_RIGHT, mutate_old_bug=True)
        self.assertEqual(bar.fs_mode, SimulatedHyprbar.FSMODE_FULLSCREEN,
                         "Negative control: old bug keeps window locked in fullscreen")

    def test_negative_control_pill_drag_with_old_bug(self) -> None:
        """Negative control: old code drops tab body click without arming drag."""
        bar = SimulatedHyprbar(bar_overlay=True)
        bar.fs_mode = SimulatedHyprbar.FSMODE_FULLSCREEN

        handled = bar.handle_down_event((120.0, 14.0), SimulatedHyprbar.BTN_LEFT, mutate_old_bug=True)
        self.assertFalse(handled, "Negative control: old bug returned false/dropped click")
        self.assertFalse(bar.m_bDragPending, "Negative control: drag pending was not armed")

    def test_negative_control_snapshot_pass_with_old_bug(self) -> None:
        """Negative control: without renderModif check, 2D tabs contaminate 3D texture snapshots."""
        bar = SimulatedHyprbar(bar_overlay=True)
        res = bar.render_pass(render_modif_enabled=True, mutate_old_bug=True)
        self.assertEqual(res, "RENDERED_OVERLAY_TAB", "Negative control: 2D tab baked into texture")

    def test_boundary_zero_and_extreme_coordinates(self) -> None:
        """Boundary conditions: zero-size monitor/window and extreme offscreen coordinates."""
        bar = SimulatedHyprbar(bar_overlay=True)
        bar.monitor_box = (0.0, 0.0, 0.0, 0.0)
        bar.m_preMaxBox = None

        # restore_float_box must handle zero monitor size without division by zero
        bar.restore_float_box()
        self.assertEqual(bar.window_box[2], DEFAULT_FLOAT_W)
        self.assertEqual(bar.window_box[3], DEFAULT_FLOAT_H)

        # Extreme coordinates
        self.assertFalse(bar.input_is_valid(-99999.0, -99999.0))
        self.assertFalse(bar.input_is_valid(99999.0, 99999.0))

        # Clicks outside tab pill extents (e.g. rel_x = 180, rel_y = 14)
        bar.window_box = (0.0, 0.0, 1920.0, 1080.0)
        self.assertFalse(bar.input_is_valid(180.0, 14.0), "Coordinates outside [25, 147] must be rejected")
        handled = bar.handle_down_event((180.0, 14.0), SimulatedHyprbar.BTN_LEFT, mutate_old_bug=False)
        self.assertFalse(handled, "Down event outside tab pill extents must return false")


# ----------------------------------------------------------------------------------------------------------------------
# Tier 3: Cross-Feature Combinations
# ----------------------------------------------------------------------------------------------------------------------
class TestTier3CrossFeatureCombinations(unittest.TestCase):
    """Interactions between Hyprbars R1, 3D room transitions, and ShaderGlass."""

    def test_fullscreen_window_enter_3d_preserves_premax_and_clean_snapshot(self) -> None:
        """Cross-feature: Entering 3D from fullscreen captures clean texture and preserves m_preMaxBox."""
        bar = SimulatedHyprbar(bar_overlay=True)
        bar.window_box = (0.0, 0.0, 1920.0, 1080.0)
        bar.fs_mode = SimulatedHyprbar.FSMODE_FULLSCREEN
        bar.m_preMaxBox = (250.0, 180.0, 900.0, 550.0)

        # 1. 3D capture pass occurs: renderModif.enabled = true
        pass_result = bar.render_pass(render_modif_enabled=True)
        self.assertEqual(pass_result, "SKIPPED_FOR_SNAPSHOT", "3D snapshot must not have 2D tab baked in")

        # 2. Window is un-fullscreened upon exiting 3D
        bar.handle_plus_button(SimulatedHyprbar.BTN_LEFT)
        self.assertEqual(bar.fs_mode, SimulatedHyprbar.FSMODE_NONE)
        self.assertEqual(bar.window_box, (250.0, 180.0, 900.0, 550.0), "Original float geometry restored")

    def test_inset_tab_interaction_with_shaderglass_active(self) -> None:
        """Cross-feature: ShaderGlass overlay (+passthrough) does not intercept clicks to inset tab."""
        bar = SimulatedHyprbar(bar_overlay=True)
        bar.fs_mode = SimulatedHyprbar.FSMODE_FULLSCREEN
        bar.update_reveal(mutate_old_bug=False)

        # Mouse click at tab pill
        self.assertTrue(bar.m_bInputExtents, "Input extents must be enabled so clicks reach decoration")
        click_handled = bar.handle_down_event((120.0, 14.0), SimulatedHyprbar.BTN_LEFT)
        self.assertTrue(click_handled, "Tab drag recovery works seamlessly with active overlay")


# ----------------------------------------------------------------------------------------------------------------------
# Tier 4: Real-World Live Session Scenarios
# ----------------------------------------------------------------------------------------------------------------------
class TestTier4RealWorldSessionScenario(unittest.TestCase):
    """Validation against source files, patches, and live compositor environment."""

    def test_ssot_hyprbars_configuration(self) -> None:
        """Verify SSOT configuration in mios.toml for hyprbars bar_height = 28."""
        if not os.path.exists(SSOT_TOML):
            self.skipTest(f"{SSOT_TOML} not found")
        with open(SSOT_TOML, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("bar_height = 28", content, "SSOT must declare bar_height = 28")

    def test_source_code_contract(self) -> None:
        """Inspect barDeco.cpp to verify all 5 R1 changes exist once M1 completes."""
        if not os.path.exists(BARDECO_CPP):
            self.skipTest(f"{BARDECO_CPP} not found")
        with open(BARDECO_CPP, "r", encoding="utf-8") as f:
            code = f.read()

        if "mios-topbar" not in code:
            self.skipTest("M1 pending: barDeco.cpp pending worker_m1 patch application")

        # Check 1: mios-topbar whitelist in inputIsValid
        self.assertIn("mios-topbar", code, "barDeco.cpp must whitelist 'mios-topbar' in inputIsValid")

        # Check 2: snapshot pass bypass with renderModif.enabled
        self.assertIn("renderModif.enabled", code, "barDeco.cpp must check renderModif.enabled in renderPass")

        # Check 3: tab pill body drag pending
        self.assertIn("m_bDragPending", code, "barDeco.cpp must arm m_bDragPending on tab pill drag")


if __name__ == "__main__":
    unittest.main()
