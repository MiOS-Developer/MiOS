#!/usr/bin/env python3
# AI-hint: Hypr3D stills mode (hypr3d-mios-stills.patch). render.stills = true gives a clean frame for screenshots and
# photography: the crosshair is hidden, and the room draws at RENDER_LAST_MOMENT over the top and overlay layers (bars,
# dock, notifications) instead of under them. Every frame of the 2026-10-22 photo shoot failed critique with the
# crosshair at the frame centre and the dock/island stamped over the 3D scene; this switch removes both.
#   1. static (always): the SSOT lists the patch after the SSAA patch and declares render.stills = false for normal
#      use; the patch parses render.stills, gates both crosshair sites and the render stage on it; the lines it
#      replaces are the unconditional upstream ones (negative control: without the patch nothing hides the crosshair).
#   2. evidence (MIOS_H3D_STILLS_ON + MIOS_H3D_STILLS_OFF: two grim frames of the same 3D scene on a 1920x1080 output,
#      taken with stills on and off): the stills-on frame has no crosshair at the centre and no dock or island band;
#      the stills-off frame (negative control) shows the white crosshair and the bars.
# AI-related: usr/share/mios/hyprland/patches/hypr3d-mios-stills.patch, usr/share/mios/mios.toml
# [desktop.hyprland.hypr3d], tests/test-hypr3d-quality.py
"""Hypr3D stills-mode tests."""

from __future__ import annotations

import os
import tomllib
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SSOT = os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
PATCH = "usr/share/mios/hyprland/patches/hypr3d-mios-stills.patch"
SSAA = "usr/share/mios/hyprland/patches/hypr3d-mios-ssaa.patch"
ON = os.environ.get("MIOS_H3D_STILLS_ON", "")
OFF = os.environ.get("MIOS_H3D_STILLS_OFF", "")


def ssot():
    with open(SSOT, "rb") as f:
        return tomllib.load(f)


def patch_lines(sign):
    with open(os.path.join(ROOT, PATCH), encoding="utf-8") as f:
        lines = f.read().splitlines()
    return "\n".join(ln[1:] for ln in lines if ln.startswith(sign) and not ln.startswith(sign * 3))


class Static(unittest.TestCase):
    def test_ssot_declares_patch_and_default(self):
        h = ssot()["desktop"]["hyprland"]["hypr3d"]
        self.assertIn(PATCH, h["patches"])
        self.assertLess(h["patches"].index(SSAA), h["patches"].index(PATCH), "stills applies after the SSAA patch")
        self.assertTrue(os.path.isfile(os.path.join(ROOT, PATCH)))
        self.assertIs(h["config"]["render"].get("stills"), False, "normal use keeps the crosshair and the shell")

    def test_patch_gates_crosshair_and_stage(self):
        added = patch_lines("+")
        for needle in ('"stills"', "g_cfgStills",
                       "g_cfgUnderLayers && !g_cfgStills ? RENDER_POST_WINDOWS : RENDER_LAST_MOMENT",
                       "setCrosshairVisible(!g_cfgStills && !pointerFree())",
                       "setCrosshairVisible(!g_cfgStills)"):
            self.assertIn(needle, added, needle)

    def test_negative_upstream_lines_are_unconditional(self):
        removed = patch_lines("-")
        self.assertIn("setCrosshairVisible(!pointerFree())", removed)
        self.assertIn("setCrosshairVisible(true)", removed)
        self.assertIn("g_cfgUnderLayers ? RENDER_POST_WINDOWS : RENDER_LAST_MOMENT", removed)


def luma(path):
    from PIL import Image

    return Image.open(path).convert("L")


def peak(im, box):
    return max(im.crop(box).tobytes())


@unittest.skipUnless(ON and OFF, "MIOS_H3D_STILLS_ON / MIOS_H3D_STILLS_OFF evidence frames not provided")
class Evidence(unittest.TestCase):
    def regions(self, im):
        w, h = im.size
        return {
            "crosshair": (w // 2 - 14, h // 2 - 14, w // 2 + 14, h // 2 + 14),
            "dock": (w // 2 - 300, h - 70, w // 2 + 300, h - 20),
            "island": (w // 2 - 330, 8, w // 2 + 330, 34),
        }

    def test_stills_frame_is_clean(self):
        im = luma(ON)
        r = self.regions(im)
        self.assertLess(peak(im, r["crosshair"]), 200, "no white crosshair at the centre")
        self.assertLess(peak(im, r["dock"]), 200, "no dock icons over the scene")
        self.assertLess(peak(im, r["island"]), 200, "no status-island text over the scene")

    def test_negative_normal_frame_shows_crosshair_and_bars(self):
        im = luma(OFF)
        r = self.regions(im)
        self.assertGreaterEqual(peak(im, r["crosshair"]), 240, "the crosshair is drawn without stills")
        self.assertGreaterEqual(peak(im, r["dock"]), 200, "the dock draws over the room without stills")


if __name__ == "__main__":
    unittest.main(verbosity=2)
