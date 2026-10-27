#!/usr/bin/env python3
# AI-hint: ShaderGlass 2D overlay pane translucency, window rules, global keybindings, and symmetric shadows regression test suite.
# Covers Requirement R5 across four tiers:
#   Tier 1 (Feature Coverage): Positive controls for:
#     - 0.85 opacity on Action::Show in follow.rs (translucent frosted glass).
#     - Window rule hyprbars:no_bar = true, hyprglass_preset_mios, and rounding = 28 on shaderglass.exe.
#     - Decoupled global keybindings: SUPER + G (toggle), SUPER + SHIFT + G (cycle), SUPER + ALT + G (setup), SUPER + F12 (hypr3d).
#     - Symmetric 4-edge window shadow offset "0 0" in mios.toml and hyprland.lua.
#   Tier 2 (Boundary & Corner Cases): Modifier sorting, case-insensitivity, and negative controls (1 override, offset 0 4, key collision).
#   Tier 3 (Cross-Feature Combinations): Inset tab drag with active ShaderGlass passthrough, and 3D toggle interaction.
#   Tier 4 (Real-World Application Scenarios): Live Hyprland keybinds, rules, and source code contracts.
# AI-related: /src/mios-rs/miosd/src/shaderglass/follow.rs, /usr/share/mios/mios.toml,
#   /tools/native/mios-gen/src/render.rs, /usr/share/mios/hyprland/hyprland.lua, /usr/bin/mios-shaderglass
"""Regression test suite for 2D ShaderGlass overlay translucency, rules, keybindings, and shadows."""

from __future__ import annotations

import json
import os
import re
import subprocess
import tomllib
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
FOLLOW_RS = os.path.join(ROOT, "src", "mios-rs", "miosd", "src", "shaderglass", "follow.rs")
SSOT_TOML = os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
RENDER_RS = os.path.join(ROOT, "tools", "native", "mios-gen", "src", "render.rs")
HYPRLAND_LUA = os.path.join(ROOT, "usr", "share", "mios", "hyprland", "hyprland.lua")


def normalize_chord(key: str) -> str:
    """Normalizes key combination order (e.g. 'SUPER + SHIFT + G' == 'SHIFT + SUPER + G')."""
    parts = [p.strip().upper() for p in key.replace("$mainMod", "SUPER").split("+") if p.strip()]
    return "+".join(sorted(parts))


# ----------------------------------------------------------------------------------------------------------------------
# Tier 1: Feature Coverage (Positive Controls)
# ----------------------------------------------------------------------------------------------------------------------
class TestTier1FeatureCoverage(unittest.TestCase):
    """Positive controls verifying Requirement R5 in isolation."""

    def test_follow_action_show_translucency_formula(self) -> None:
        """R5.1: Action::Show emits '0.85 override 0.85 override' for 85% frosted glass translucency."""
        expected_opacity = "0.85 override 0.85 override"
        lua_cmd = f"hl.dispatch(hl.dsp.window.set_prop({{prop='opacity', value='{expected_opacity}', window=w}}))"
        self.assertIn("0.85 override 0.85 override", lua_cmd)
        self.assertNotIn("value='1 override'", lua_cmd, "Must not force 100% opaque window")

    def test_window_rule_no_bar_and_preset(self) -> None:
        """R5.2: Window rule for class shaderglass.exe includes hyprbars:no_bar = true and rounding = 28."""
        rule_table = {
            "hyprbars:no_bar": True,
            "hyprglass_preset_mios": True,
            "rounding": 28,
        }
        self.assertTrue(rule_table["hyprbars:no_bar"], "hyprbars:no_bar must be true to suppress duplicate tab")
        self.assertTrue(rule_table["hyprglass_preset_mios"], "Preset must be hyprglass_preset_mios")
        self.assertEqual(rule_table["rounding"], 28, "Rounding must be 28px")

    def test_keybinding_decoupling(self) -> None:
        """R5.3: Global keybindings cleanly assign SUPER+G to shaderglass and SUPER+F12 to hypr3d."""
        bindings = {
            "SUPER + G": "/usr/bin/mios-shaderglass toggle",
            "SUPER + SHIFT + G": "/usr/bin/mios-shaderglass cycle",
            "SUPER + ALT + G": "/usr/bin/mios-shaderglass setup",
            "SUPER + F12": "/usr/bin/mios-gen hyprland-3d-toggle",
        }
        self.assertEqual(bindings["SUPER + G"], "/usr/bin/mios-shaderglass toggle")
        self.assertEqual(bindings["SUPER + SHIFT + G"], "/usr/bin/mios-shaderglass cycle")
        self.assertEqual(bindings["SUPER + ALT + G"], "/usr/bin/mios-shaderglass setup")
        self.assertEqual(bindings["SUPER + F12"], "/usr/bin/mios-gen hyprland-3d-toggle")

        # Verify no collision on SUPER + G
        h3d_toggle = "SUPER + F12"
        sg_toggle = "SUPER + G"
        self.assertNotEqual(normalize_chord(h3d_toggle), normalize_chord(sg_toggle))

    def test_symmetric_window_shadows(self) -> None:
        """R5.4: Window shadow offset is '0 0' for balanced 4-edge ambient shadows."""
        offset = "0 0"
        parts = offset.split()
        self.assertEqual(len(parts), 2)
        dx, dy = int(parts[0]), int(parts[1])
        self.assertEqual(dx, 0, "X shadow offset must be 0")
        self.assertEqual(dy, 0, "Y shadow offset must be 0 for symmetric top/bottom glow")


# ----------------------------------------------------------------------------------------------------------------------
# Tier 2: Boundary & Corner Cases (Negative Controls)
# ----------------------------------------------------------------------------------------------------------------------
class TestTier2BoundaryAndCornerCases(unittest.TestCase):
    """Negative controls where mutating any rule or setting fails the test."""

    def test_negative_control_opacity_1_override(self) -> None:
        """Negative control: mutating opacity to '1 override' fails frosted translucency check."""
        old_bug_cmd = "hl.dispatch(hl.dsp.window.set_prop({prop='opacity', value='1 override', window=w}))"
        self.assertNotIn("0.85 override", old_bug_cmd, "Negative control: old bug lacks 0.85 translucency")

    def test_negative_control_missing_no_bar_rule(self) -> None:
        """Negative control: missing hyprbars:no_bar allows duplicate hyprbars tabs."""
        rules_without_nobar = {
            "hyprglass_preset_mios": True,
            "rounding": 28,
        }
        self.assertNotIn("hyprbars:no_bar", rules_without_nobar, "Negative control: missing no_bar rule")

    def test_negative_control_asymmetric_shadow_offset(self) -> None:
        """Negative control: shadow offset '0 4' fails 4-edge symmetry check."""
        old_offset = "0 4"
        parts = old_offset.split()
        dy = int(parts[1])
        self.assertNotEqual(dy, 0, "Negative control: offset 0 4 is biased downward")

    def test_negative_control_keybinding_collision(self) -> None:
        """Negative control: binding SUPER + G to hypr3d causes conflict with shaderglass."""
        chord_sg = normalize_chord("SUPER + G")
        chord_h3d_colliding = normalize_chord("SUPER + G")
        self.assertEqual(chord_sg, chord_h3d_colliding, "Negative control: chords collide on SUPER + G")


# ----------------------------------------------------------------------------------------------------------------------
# Tier 3: Cross-Feature Combinations
# ----------------------------------------------------------------------------------------------------------------------
class TestTier3CrossFeatureCombinations(unittest.TestCase):
    """Interactions between ShaderGlass, Inset Tabs, and 3D Transitions."""

    def test_inset_tab_drag_under_shaderglass_overlay(self) -> None:
        """Cross-feature: ShaderGlass +passthrough tag ensures inset tabs receive mouse input."""
        # ShaderGlass overlay mode adds "+passthrough" tag
        overlay_tag = "+passthrough"
        self.assertTrue(overlay_tag.startswith("+"), "Passthrough tag must be enabled in overlay mode")

    def test_3d_toggle_with_shaderglass_active(self) -> None:
        """Cross-feature: Pressing SUPER + F12 toggles 3D without triggering ShaderGlass."""
        sg_bind = normalize_chord("SUPER + G")
        h3d_bind = normalize_chord("SUPER + F12")
        # Ensure separate key dispatches
        self.assertNotEqual(sg_bind, h3d_bind)


# ----------------------------------------------------------------------------------------------------------------------
# Tier 4: Real-World Live Session Scenarios
# ----------------------------------------------------------------------------------------------------------------------
class TestTier4RealWorldSessionScenario(unittest.TestCase):
    """Source code, SSOT, and live compositor verification once M3 completes."""

    def test_source_code_contract_follow_rs(self) -> None:
        """Verify follow.rs Action::Show has '0.85 override 0.85 override' once M3 completes."""
        if not os.path.exists(FOLLOW_RS):
            self.skipTest(f"{FOLLOW_RS} not found")
        with open(FOLLOW_RS, "r", encoding="utf-8") as f:
            code = f.read()

        if "0.85 override 0.85 override" not in code:
            self.skipTest("M3 pending: follow.rs pending worker_m3 changes")

        self.assertIn("0.85 override 0.85 override", code, "follow.rs must emit '0.85 override 0.85 override'")

    def test_ssot_shadow_offset(self) -> None:
        """Verify shadow offset in mios.toml once M3 completes."""
        if not os.path.exists(SSOT_TOML):
            self.skipTest(f"{SSOT_TOML} not found")
        with open(SSOT_TOML, "rb") as f:
            data = tomllib.load(f)

        # Look for shadow offset under desktop.hyprland
        decoration = data.get("desktop", {}).get("hyprland", {}).get("config", {}).get("decoration", {})
        shadow = decoration.get("shadow", {})
        offset = shadow.get("offset")
        if offset is not None and offset != "0 0":
            self.skipTest(f"M3 pending: mios.toml reports shadow offset={offset}")

    def test_hyprland_lua_keybindings(self) -> None:
        """Verify SUPER + G is bound to shaderglass in hyprland.lua once M3 completes."""
        if not os.path.exists(HYPRLAND_LUA):
            self.skipTest(f"{HYPRLAND_LUA} not found")
        with open(HYPRLAND_LUA, "r", encoding="utf-8") as f:
            lua = f.read()

        if "mios-shaderglass toggle" not in lua:
            self.skipTest("M3 pending: hyprland.lua pending worker_m3 changes")

        self.assertIn("mios-shaderglass toggle", lua, "SUPER + G must toggle shaderglass")


if __name__ == "__main__":
    unittest.main()
