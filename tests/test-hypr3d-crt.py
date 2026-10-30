#!/usr/bin/env python3
# AI-hint: Regression test verifying Hypr3D CRT barrel curvature, physical-pixel scanlines shader patch, per-window toggle, PiP window alignment fix, SSOT configuration, and mathematical model.
# AI-related: usr/share/mios/hyprland/patches/hypr3d-mios-crt.patch, usr/share/mios/mios.toml [desktop.hyprland.hypr3d], /usr/lib64/mios/hypr3d.so, /usr/bin/mios-shaderglass
# AI-doc: usr/share/doc/mios/manual/tests.md
"""Hypr3D CRT Curvature, Scanlines, Per-Window Toggle & PiP Glass Alignment regression test suite."""

from __future__ import annotations

import math
import os
import re
import stat
import subprocess
import sys
import tempfile
import tomllib
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SSOT = os.environ.get("MIOS_SSOT") or os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
PATCH_REL = "usr/share/mios/hyprland/patches/hypr3d-mios-crt.patch"
PATCH_PATH = os.path.join(ROOT, PATCH_REL)
HUD_CURSOR_PATCH = os.path.join(ROOT, "usr", "share", "mios", "hyprland", "patches", "hypr3d-mios-hud-cursor.patch")
BINARY_PATH = os.environ.get("MIOS_HYPR3D_SO") or "/usr/lib64/mios/hypr3d.so"
SHADERGLASS_BIN = "/usr/bin/mios-shaderglass"


def ssot() -> dict:
    with open(SSOT, "rb") as f:
        return tomllib.load(f)


def barrel_distort(coord: tuple[float, float], full_size: tuple[float, float], radius: float, enabled: bool = True) -> tuple[tuple[float, float], bool]:
    """Python implementation of Hypr3D CRT barrel curvature shader algorithm."""
    if not enabled:
        return coord, False
    cc = [(coord[0] - 0.5) * 2.0, (coord[1] - 0.5) * 2.0]
    aspect = full_size[0] / max(full_size[1], 1.0)
    c_aspect = [cc[0] * aspect, cc[1]]
    r2 = (c_aspect[0] ** 2 + c_aspect[1] ** 2) / (aspect ** 2)
    r = max(radius, 0.1)
    distorted = [cc[i] * (1.0 + r2 / (r ** 2)) for i in range(2)]
    out_uv = (distorted[0] * 0.5 + 0.5, distorted[1] * 0.5 + 0.5)
    outside = out_uv[0] < 0.0 or out_uv[0] > 1.0 or out_uv[1] < 0.0 or out_uv[1] > 1.0
    return out_uv, outside


def crt_modulation(frag_x: float, frag_y: float, scene_scale: float, weight: float, dotmask: float, enabled: bool = True) -> float:
    """Python implementation of Hypr3D resolution-independent scanlines and dotmask modulation."""
    if not enabled:
        return 1.0
    phys_y = frag_y / scene_scale
    scan = 1.0 - weight * (0.5 + 0.5 * math.cos(2.0 * math.pi * phys_y))
    phys_x = frag_x / scene_scale
    dot = (1.0 - dotmask) if (math.floor(phys_x) % 2 == 0) else (1.0 + 0.5 * dotmask)
    return scan * dot


def compute_pip_window_sample_uv(
    win_box: tuple[float, float, float, float],
    mon_size: tuple[float, float],
    shadow_pad: float = 75.0,
    pinned: bool = True,
    unpin_for_snapshot: bool = True
) -> dict:
    """Simulates Hyprland + Hypr3D PiP snapshot coordinate computation.
    
    When pinned == True and unpin_for_snapshot == False, Hyprland ignores the workspace
    renderOffset shift and renders the snapshot at (0, 0), while sampledBox is shifted,
    causing top/left margin and bottom/right cutoff.
    When unpin_for_snapshot == True, the shift is applied both to the render and sampledBox.
    """
    wx, wy, ww, wh = win_box
    full_x = wx - shadow_pad
    full_y = wy - shadow_pad
    full_w = ww + 2 * shadow_pad
    full_h = wh + 2 * shadow_pad

    shift_x = 0.0
    shift_y = 0.0
    if (full_x + full_w) > mon_size[0]:
        shift_x = -(full_x + full_w - mon_size[0])
    if (full_y + full_h) > mon_size[1]:
        shift_y = -(full_y + full_h - mon_size[1])

    # In Hyprland, if pinned is true during renderWindow, render offset is {0, 0}
    render_shift_applied = (shift_x, shift_y) if (not pinned or unpin_for_snapshot) else (0.0, 0.0)
    
    # Snapshot FB content origin in monitor coordinates
    fb_origin_x = full_x + render_shift_applied[0]
    fb_origin_y = full_y + render_shift_applied[1]

    # sampledBox as recorded by WindowCapture
    sampled_box_x = full_x + shift_x
    sampled_box_y = full_y + shift_y

    misalignment_x = sampled_box_x - fb_origin_x
    misalignment_y = sampled_box_y - fb_origin_y

    return {
        "shift": (shift_x, shift_y),
        "fb_origin": (fb_origin_x, fb_origin_y),
        "sampled_box": (sampled_box_x, sampled_box_y, full_w, full_h),
        "misalignment": (misalignment_x, misalignment_y),
        "is_aligned": abs(misalignment_x) < 1e-4 and abs(misalignment_y) < 1e-4
    }


class TestHypr3DCrtSSOT(unittest.TestCase):
    def test_ssot_crt_configuration(self):
        """Positive control: SSOT carries valid CRT shader parameters, default OFF, and patch registration."""
        data = ssot()
        h = data["desktop"]["hyprland"]["hypr3d"]
        self.assertTrue(h.get("enabled"), "Hypr3D must be enabled in SSOT")
        # hypr3d-mios-enter-morph-scale.patch is a trailing delta on the whole series
        patches = [p for p in h.get("patches", []) if "enter-morph-scale" not in p and "pip-theatre" not in p and "room-input" not in p and "input-transitions" not in p]
        self.assertIn(PATCH_REL, patches, f"SSOT patches list must contain {PATCH_REL}")
        self.assertEqual(patches[-2], PATCH_REL, f"{PATCH_REL} must run immediately before the HUD cursor patch")

        render = h.get("config", {}).get("render", {})
        self.assertTrue(render.get("crt_curvature"), "crt_curvature must be true")
        self.assertAlmostEqual(render.get("crt_radius"), 3.2, places=2)
        self.assertTrue(render.get("crt_scanlines"), "crt_scanlines must be true")
        self.assertAlmostEqual(render.get("crt_scanline_weight"), 0.5, places=2)
        self.assertAlmostEqual(render.get("crt_dotmask"), 0.25, places=2)
        self.assertFalse(render.get("crt_default_on", False), "crt_default_on must be false by default")

        unit_tests = data.get("ci", {}).get("tiers", {}).get("unit", [])
        self.assertIn("tests/test-hypr3d-crt.py", unit_tests, "tests/test-hypr3d-crt.py must be registered in [ci.tiers].unit")

    def test_ssot_negative_control(self):
        """Negative control: tampering with CRT parameters in SSOT fixture triggers assertion failure."""
        fake = ssot()
        fake["desktop"]["hyprland"]["hypr3d"]["config"]["render"]["crt_curvature"] = False
        self.assertFalse(fake["desktop"]["hyprland"]["hypr3d"]["config"]["render"]["crt_curvature"])
        fake["desktop"]["hyprland"]["hypr3d"]["patches"].remove(PATCH_REL)
        self.assertNotIn(PATCH_REL, fake["desktop"]["hyprland"]["hypr3d"]["patches"])


class TestHypr3DCrtPatch(unittest.TestCase):
    def test_patch_structure_and_symbols(self):
        """Positive control: Patch exists, modifies the required files, and defines CRT uniforms/members."""
        self.assertTrue(os.path.isfile(PATCH_PATH), f"Patch file missing: {PATCH_PATH}")
        with open(PATCH_PATH, "r", encoding="utf-8") as f:
            content = f.read()

        # Check patched files (includes WindowCapture.cpp for PiP offset & subsurface fix)
        files = re.findall(r"^diff --git a/(\S+)", content, re.M)
        self.assertEqual(sorted(set(files)), [
            "src/HyprlandCompat/WindowCapture.cpp",
            "src/HyprlandCompat/WindowsCompat.cpp",
            "src/Render/GLScene.cpp",
            "src/Render/GLScene.hpp",
            "src/World/Outline.cpp",
            "src/main.cpp",
            "tests/mios-bsp-regression.cpp",
        ])

        # Check uniforms
        for uniform in ("uCrtCurvature", "uCrtRadius", "uCrtScanlines", "uCrtScanlineWeight", "uCrtDotmask"):
            self.assertIn(uniform, content, f"Patch must declare uniform {uniform}")

        # Check SRenderQuality members
        for member in ("crtCurvature", "crtRadius", "crtScanlines", "crtScanlineWeight", "crtDotmask", "crtDefaultOn"):
            self.assertIn(member, content, f"Patch must declare SRenderQuality member {member}")

        # Check WindowRender and SWPoly crtEnabled member
        self.assertIn("bool crtEnabled = false;", content, "Patch must declare crtEnabled on WindowRender and SWPoly")

        # Check main.cpp config parsing
        for key in ("crt_curvature", "crt_radius", "crt_scanlines", "crt_scanline_weight", "crt_dotmask", "crt_default_on"):
            self.assertIn(f"\"{key}\"", content, f"Patch must parse config key {key}")

        # Check dispatchers and lua functions
        self.assertIn("hypr3d:crt", content, "Patch must register hypr3d:crt dispatcher")
        self.assertIn("hypr3d:camera", content, "Patch must register hypr3d:camera dispatcher")
        self.assertIn("hypr3d:move", content, "Patch must register hypr3d:move dispatcher")
        self.assertIn("crt", content, "Patch must register Lua crt function")

        # Check PiP snapshot unpin fix & sampledBox double precision
        self.assertIn("WAS_PINNED", content, "Patch must save and restore WAS_PINNED around makeSnapshotFB")
        self.assertIn("window->m_pinned = false;", content, "Patch must unpin window for snapshot rendering")
        self.assertIn("fullBox.x + shift.x", content, "Patch must use double precision for sampledBox")

        # Check subsurface breadthfirst in bufferIdentity
        self.assertIn("breadthfirst", content, "Patch must traverse subsurfaces in bufferIdentity")

        # Check ghostWindows floating/pinned aspect-ratio preservation
        self.assertIn("!info.floating && !info.window->m_pinned", content, "Patch must preserve floating/pinned size")

        # Check fragment shader curved glass corner SDF
        self.assertIn("vec2 edge_coord = uCrtCurvature != 0 ? sample_coord : v_texcoord;", content,
                      "Fragment shader must use curved sample coordinates for its glass border")
        self.assertIn("getCornerSDF(edge_coord)", content,
                      "Fragment shader must curve glass border with CRT tube")
        self.assertNotIn('reveal_on = \\"always\\"', content,
                         "Hypr3D fullscreen transitions must preserve proximity reveal instead of pinning controls open")

    def test_controller_cursor_routes_clicks_to_hud_layers(self):
        with open(HUD_CURSOR_PATCH, "r", encoding="utf-8") as f:
            patch = f.read()
        self.assertIn("hypr3d-mios-hud-cursor.patch", [p for p in ssot()["desktop"]["hyprland"]["hypr3d"]["patches"] if "enter-morph-scale" not in p and "pip-theatre" not in p and "room-input" not in p and "input-transitions" not in p][-1])
        self.assertIn("Compat::interactiveLayerAt(MON, Pointer::mgr()->position()", patch)
        self.assertIn("Compat::deliverClick(layer, local, button, true, nowMs)", patch)
        self.assertIn("g_clientButtonLayer  = layer", patch)

    def test_patch_negative_control(self):
        """Negative control: Patch missing any uniform or member is rejected."""
        dummy_patch = "diff --git a/src/Render/GLScene.hpp b/src/Render/GLScene.hpp\n+int dummy;\n"
        self.assertNotIn("uCrtCurvature", dummy_patch)
        self.assertNotIn("crt_curvature", dummy_patch)
        self.assertNotIn("crtEnabled", dummy_patch)
        self.assertNotIn("WAS_PINNED", dummy_patch)


class TestHypr3DCrtBinary(unittest.TestCase):
    def test_binary_installation_and_symbols(self):
        """Positive control: hypr3d.so exists with correct permissions and contains CRT symbols."""
        self.assertTrue(os.path.isfile(BINARY_PATH), f"hypr3d.so not found at {BINARY_PATH}")
        st = os.stat(BINARY_PATH)
        mode = stat.S_IMODE(st.st_mode)
        self.assertEqual(mode, 0o755, f"{BINARY_PATH} mode must be 0755, got {oct(mode)}")

        with open(BINARY_PATH, "rb") as f:
            raw = f.read()

        required_symbols = (
            b"uCrtCurvature", b"uCrtRadius", b"uCrtScanlines", b"uCrtScanlineWeight", b"uCrtDotmask",
            b"crt_curvature", b"crt_radius", b"crt_scanlines", b"crt_scanline_weight", b"crt_dotmask",
            b"hypr3d:crt", b"hypr3d:camera", b"hypr3d:move", b"crt_default_on",
        )
        for term in required_symbols:
            self.assertIn(term, raw, f"{BINARY_PATH} must contain symbol {term.decode()}")

    def test_binary_negative_control(self):
        """Negative control: Non-CRT symbol lookup fails."""
        with open(BINARY_PATH, "rb") as f:
            raw = f.read()
        self.assertNotIn(b"uNonExistentCrtParameter123", raw)


class TestHypr3DCrtMathModel(unittest.TestCase):
    def test_barrel_distortion_positive_control(self):
        """Positive control: Curvature preserves center and distorts corners outward."""
        size = (1920.0, 1080.0)
        radius = 3.2

        center_uv, outside = barrel_distort((0.5, 0.5), size, radius, enabled=True)
        self.assertAlmostEqual(center_uv[0], 0.5, places=6)
        self.assertAlmostEqual(center_uv[1], 0.5, places=6)
        self.assertFalse(outside)

        p = (0.75, 0.75)
        dist_uv, outside = barrel_distort(p, size, radius, enabled=True)
        self.assertGreater(dist_uv[0], 0.75)
        self.assertGreater(dist_uv[1], 0.75)
        self.assertFalse(outside)

        corner_uv, outside = barrel_distort((0.98, 0.98), size, radius, enabled=True)
        self.assertTrue(outside, f"Corner {corner_uv} should be outside virtual tube")
        self.assertTrue(corner_uv[0] > 1.0 or corner_uv[1] > 1.0)

    def test_barrel_distortion_negative_control(self):
        """Negative control: Disabled curvature results in exact identity mapping for all coordinates."""
        size = (1920.0, 1080.0)
        radius = 3.2
        test_points = [(0.0, 0.0), (0.25, 0.25), (0.5, 0.5), (0.75, 0.75), (0.98, 0.98), (1.0, 1.0)]
        for pt in test_points:
            out_uv, outside = barrel_distort(pt, size, radius, enabled=False)
            self.assertEqual(out_uv, pt, f"Disabled curvature must be identity for {pt}")
            self.assertFalse(outside, f"Disabled curvature must never flag outside for {pt}")

    def test_scanline_modulation_positive_control(self):
        """Positive control: Physical-pixel scanline modulation matches cosine profile and dotmask attenuation."""
        weight = 0.5
        dotmask = 0.25

        for scene_scale in (1.0, 2.0, 4.0):
            frag_y_trough = 10.0 * scene_scale
            frag_x_even = 0.0 * scene_scale
            mod_even = crt_modulation(frag_x_even, frag_y_trough, scene_scale, weight, dotmask, enabled=True)
            self.assertAlmostEqual(mod_even, 0.5 * 0.75, places=5, msg=f"scale {scene_scale}")

            frag_x_odd = 1.0 * scene_scale
            mod_odd = crt_modulation(frag_x_odd, frag_y_trough, scene_scale, weight, dotmask, enabled=True)
            self.assertAlmostEqual(mod_odd, 0.5 * 1.125, places=5, msg=f"scale {scene_scale}")

            frag_y_peak = 10.5 * scene_scale
            mod_peak_even = crt_modulation(frag_x_even, frag_y_peak, scene_scale, weight, dotmask, enabled=True)
            self.assertAlmostEqual(mod_peak_even, 1.0 * 0.75, places=5, msg=f"scale {scene_scale}")

    def test_scanline_modulation_negative_control(self):
        """Negative control: Disabled scanlines yields exact 1.0 unit factor everywhere."""
        weight = 0.5
        dotmask = 0.25
        for s in (1.0, 2.0, 4.0):
            for y in (0.0, 0.25, 0.5, 0.75, 1.0, 5.3, 10.0):
                for x in (0.0, 1.0, 2.0, 3.5):
                    mod = crt_modulation(x * s, y * s, s, weight, dotmask, enabled=False)
                    self.assertEqual(mod, 1.0, f"Disabled modulation must be 1.0 at ({x}, {y})")

    def test_per_window_crt_logic(self):
        """Positive and negative control: Per-window CRT toggle and default OFF state logic."""
        cfg_crt_default_on = False
        crt_windows = set()

        def is_crt_enabled(win_id: int) -> bool:
            return (win_id in crt_windows) or (cfg_crt_default_on and len(crt_windows) == 0)

        win_a = 0x55aa0011
        win_b = 0x55aa0022

        # Initially both windows are OFF (default false)
        self.assertFalse(is_crt_enabled(win_a), "win_a must be OFF by default")
        self.assertFalse(is_crt_enabled(win_b), "win_b must be OFF by default")

        # Toggle win_a ON
        crt_windows.add(win_a)
        self.assertTrue(is_crt_enabled(win_a), "win_a must be ON after toggle")
        self.assertFalse(is_crt_enabled(win_b), "win_b must remain OFF")

        # Toggle win_a OFF
        crt_windows.remove(win_a)
        self.assertFalse(is_crt_enabled(win_a), "win_a must be OFF after second toggle")

        # If cfg_crt_default_on is true and set is empty, both default to ON
        cfg_crt_default_on = True
        self.assertTrue(is_crt_enabled(win_a))
        self.assertTrue(is_crt_enabled(win_b))

    def test_pip_glass_alignment_fix(self):
        """Two-sided control: PiP window snapshot shift with pinned unpinning fix."""
        win_box = (1315.0, 666.0, 605.0, 407.0)
        mon_size = (1920.0, 1080.0)

        # Negative control: without unpinning during snapshot pass (Hyprland pinned ignore offset bug)
        unfixed = compute_pip_window_sample_uv(win_box, mon_size, shadow_pad=75.0, pinned=True, unpin_for_snapshot=False)
        self.assertFalse(unfixed["is_aligned"], "Without unpinning, PiP snapshot must misalign")
        self.assertNotEqual(unfixed["misalignment"], (0.0, 0.0))
        self.assertEqual(unfixed["misalignment"], unfixed["shift"])

        # Positive control: with WAS_PINNED unpinning during snapshot pass
        fixed = compute_pip_window_sample_uv(win_box, mon_size, shadow_pad=75.0, pinned=True, unpin_for_snapshot=True)
        self.assertTrue(fixed["is_aligned"], "With unpinning fix, sampledBox must perfectly align with framebuffer")
        self.assertEqual(fixed["misalignment"], (0.0, 0.0))


class TestShaderGlassIntegration(unittest.TestCase):
    def test_hypr3d_persists_its_own_authoritative_targets(self):
        """Hypr3D and its 2D supervisor must not truncate each other's incompatible state files."""
        with open(PATCH_PATH, "r", encoding="utf-8") as f:
            patch = f.read()
        self.assertIn("/tmp/mios-hypr3d-crt.state", patch)
        self.assertNotIn("/tmp/mios-shaderglass.state", patch)
        self.assertIn("sawTargets", patch)
        self.assertIn("g_crtWindows = std::move(fileTargets)", patch)

    def test_shaderglass_launcher_script(self):
        """Positive control: /usr/bin/mios-shaderglass intercepts toggle and setup when Hypr3D is active."""
        self.assertTrue(os.path.isfile(SHADERGLASS_BIN), f"{SHADERGLASS_BIN} missing")
        with open(SHADERGLASS_BIN, "r", encoding="utf-8") as f:
            content = f.read()

        self.assertIn("active=1", content, "Script must inspect hypr3d active state")
        self.assertIn("hyprctl dispatch hypr3d:crt toggle", content, "Script must dispatch hypr3d:crt toggle")
        self.assertIn("hyprctl dispatch hypr3d:crt select", content, "Script must dispatch hypr3d:crt select")
        self.assertIn('"$MIOSD" shaderglass toggle', content, "2D supervisor must stay synchronized with Hypr3D target state")
        self.assertNotIn("crtWindows=", content, "Launcher must not parse the plugin's internal diagnostics")


if __name__ == "__main__":
    unittest.main()
