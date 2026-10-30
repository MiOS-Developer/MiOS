#!/usr/bin/env python3
# AI-hint: Offline contract of the shell/HUD lane: the [desktop.topbar] scroll-volume keys and the pinned
# on_focus_under_fullscreen are valid SSOT (with mutated negative controls) and projected into hyprland.lua and the
# configurator; the island carries the wheel handler, the percentage chip and the volumeWheel IPC; drawer, search and
# keymap use the shared Glass corner rule and fill without a hand-drawn outline (main's versions are the negative
# control); battery rows show a device glyph and a level glyph; the carried Quickshell patches are well-formed, the
# bake step applies the event-socket reconnect, and (with MIOS_QUICKSHELL_SRC, an upstream checkout) the patch applies
# and the update-group gate passes. Nothing here touches the live desktop.
# AI-related: usr/share/mios/quickshell/PanelWindow.qml, usr/share/mios/quickshell/AppDrawer.qml,
# usr/share/mios/quickshell/Search.qml, usr/share/mios/quickshell/Keymap.qml, usr/share/mios/mios.toml [desktop.topbar],
# src/quickshell/patches/hyprland-event-socket-reconnect.patch, automation/66-bake-quickshell.sh,
# tests/test-shell-hud-live.py, tests/test-quickshell-candidate-live.py
# AI-functions: topbar_errors, misc_errors, glass_surface_errors, TestSsot, TestQml, TestQuickshellPatches
"""Shell/HUD lane contract tests (offline)."""
from __future__ import annotations

import copy
import os
import re
import subprocess
import tempfile
import tomllib
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SSOT = os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
QS = os.path.join(ROOT, "usr", "share", "mios", "quickshell")
LUA = os.path.join(ROOT, "usr", "share", "mios", "hyprland", "hyprland.lua")
HTML = os.path.join(ROOT, "usr", "share", "mios", "configurator", "mios.html")
PATCH = os.path.join(ROOT, "src", "quickshell", "patches", "hyprland-event-socket-reconnect.patch")
BAKE = os.path.join(ROOT, "automation", "66-bake-quickshell.sh")
GATE = os.path.join(ROOT, "src", "quickshell", "check-update-groups.py")


def read(p: str) -> str:
    with open(p, encoding="utf-8") as f:
        return f.read()


def ssot() -> dict:
    with open(SSOT, "rb") as f:
        return tomllib.load(f)


def topbar_errors(d: dict) -> list[str]:
    t = d.get("desktop", {}).get("topbar", {})
    e = []
    if not isinstance(t.get("volume_scroll"), bool):
        e.append("volume_scroll must be a boolean")
    s = t.get("volume_scroll_step_pct")
    if not isinstance(s, int) or isinstance(s, bool) or not 1 <= s <= 25:
        e.append("volume_scroll_step_pct must be an integer 1..25")
    m = t.get("volume_max_pct")
    if not isinstance(m, int) or isinstance(m, bool) or not 1 <= m <= 150:
        e.append("volume_max_pct must be an integer 1..150")
    o = t.get("volume_osd_ms")
    if not isinstance(o, int) or isinstance(o, bool) or o < 0:
        e.append("volume_osd_ms must be an integer >= 0")
    return e


def misc_errors(d: dict) -> list[str]:
    misc = d.get("desktop", {}).get("hyprland", {}).get("config", {}).get("misc", {})
    e = []
    if misc.get("on_focus_under_fullscreen") != 2:
        e.append("on_focus_under_fullscreen must be 2 (a focused window comes to the front over fullscreen content)")
    bars = d.get("desktop", {}).get("hyprland", {}).get("hyprbars", {}).get("config", {})
    r = bars.get("reveal_radius")
    if not isinstance(r, int) or not 0 <= r <= 12:
        e.append("hyprbars reveal_radius must be a narrow integer 0..12 (less sensitive controls proximity)")
    return e


SURFACES = {  # file -> the main surface's id (or None: the first Rectangle carrying the surface fill)
    "AppDrawer.qml": "pane",
    "Search.qml": None,
    "Keymap.qml": "card",
}


def surface_block(text: str, ident: str | None) -> str:
    if ident:
        m = re.search(r"Rectangle\s*\{\s*id:\s*%s\b" % re.escape(ident), text)
    else:
        m = re.search(r"Rectangle\s*\{\s*anchors\.fill:\s*parent", text)
    if not m:
        return ""
    return text[m.start():m.start() + 700]


def glass_surface_errors(name: str, text: str) -> list[str]:
    b = surface_block(text, SURFACES[name])
    e = []
    if not b:
        return [f"{name}: main surface not found"]
    if "radius: Glass.cornerFor(height)" not in b:
        e.append(f"{name}: surface radius must be Glass.cornerFor(height)")
    if not re.search(r"color:.*Glass\.(fill|fillHex)", b):
        e.append(f"{name}: surface fill must be the shared Glass fill colour")
    if "outlineColor" in b:
        e.append(f"{name}: no hand-drawn outline (the rim is hyprglass' fresnel)")
    return e


class TestSsot(unittest.TestCase):
    def test_topbar_volume_keys(self):
        self.assertEqual(topbar_errors(ssot()), [])

    def test_topbar_negative_controls(self):
        d = ssot()
        for key, bad in (("volume_scroll", "yes"), ("volume_scroll_step_pct", 0), ("volume_max_pct", 400),
                         ("volume_osd_ms", -1)):
            m = copy.deepcopy(d)
            m["desktop"]["topbar"][key] = bad
            self.assertTrue(topbar_errors(m), key)

    def test_window_misc(self):
        self.assertEqual(misc_errors(ssot()), [])
        m = copy.deepcopy(ssot())
        m["desktop"]["hyprland"]["config"]["misc"]["on_focus_under_fullscreen"] = 0
        self.assertTrue(misc_errors(m))
        m = copy.deepcopy(ssot())
        m["desktop"]["hyprland"]["hyprbars"]["config"]["reveal_radius"] = 48
        self.assertTrue(misc_errors(m))

    def test_projections(self):
        lua = read(LUA)
        d = ssot()
        self.assertIn('["on_focus_under_fullscreen"] = 2', lua)
        r = d["desktop"]["hyprland"]["hyprbars"]["config"]["reveal_radius"]
        self.assertIn(f'["reveal_radius"] = {r}', lua)
        html = read(HTML)
        for k in ("volume_scroll", "volume_scroll_step_pct", "volume_max_pct", "volume_osd_ms"):
            self.assertIn(f'data-key="desktop.topbar.{k}"', html, k)


class TestQml(unittest.TestCase):
    def test_island_scroll_volume(self):
        t = read(os.path.join(QS, "PanelWindow.qml"))
        self.assertRegex(t, r"WheelHandler\s*\{[^}]*pill\.volumeWheel\(event\.angleDelta\.y\)")
        self.assertIn("id: volumeChip", t)
        self.assertIn("function volumeWheel(deltaY: int): int", t)  # IPC
        for k in ("volume_scroll", "volume_scroll_step_pct", "volume_max_pct", "volume_osd_ms"):
            self.assertIn("bar." + k, t)
        # the chip is not gated on the sound item alone (it shows while that item is hidden)
        self.assertRegex(t, r"visible: pill\.volumeOsd && pill\.volumePct >= 0 && \(!soundGlyph\.visible")

    def test_battery_glyphs(self):
        t = read(os.path.join(QS, "PanelWindow.qml"))
        self.assertIn("function batteryLevelIcon(d)", t)
        self.assertIn("function batteryGlyphs(d)", t)
        self.assertIn("text: pill.batteryGlyphs(modelData)", t)
        self.assertIn("text: pill.batteryGlyphs(batteryRow.modelData)", t)

    def test_glass_surfaces(self):
        for name in SURFACES:
            self.assertEqual(glass_surface_errors(name, read(os.path.join(QS, name))), [], name)

    def test_glass_surfaces_negative_control_main(self):
        """main's drawer/search/keymap (theme radius, bg fill, outline) fail the same rule."""
        failing = 0
        for name in SURFACES:
            r = subprocess.run(["git", "-C", ROOT, "show", f"main:usr/share/mios/quickshell/{name}"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                self.skipTest("main not reachable from this checkout")
            if glass_surface_errors(name, r.stdout):
                failing += 1
        self.assertEqual(failing, len(SURFACES))

    def test_nested_items_override(self):
        t = read(os.path.join(QS, "Nested.qml"))
        self.assertIn('MIOS_NESTED_TOPBAR_ITEMS', t)
        self.assertIn('"clock,fps,workspaces"', t)  # default unchanged


class TestQuickshellPatches(unittest.TestCase):
    def test_patch_shape(self):
        p = read(PATCH)
        self.assertIn("src/wayland/hyprland/ipc/connection.cpp", p)
        self.assertIn("scheduleEventReconnect", p)
        self.assertIn("QLocalSocket::ClosingState", p)  # a peer close passes Closing before Unconnected
        self.assertIn("refreshToplevels", p)            # resync after the reconnect

    def test_bake_carries_patch(self):
        b = read(BAKE)
        self.assertIn("hyprland-event-socket-reconnect.patch", b)
        self.assertRegex(b, r"git -C \"\$BUILD_DIR\" apply --verbose \"\$reconnect_patch\"")

    def test_patch_applies_upstream(self):
        src = os.environ.get("MIOS_QUICKSHELL_SRC")
        if not src:
            self.skipTest("MIOS_QUICKSHELL_SRC (an upstream Quickshell checkout) not given")
        with tempfile.TemporaryDirectory() as tmp:
            wt = os.path.join(tmp, "src")
            subprocess.run(["git", "-C", src, "worktree", "add", "-q", "--detach", wt, "HEAD"], check=True)
            try:
                r = subprocess.run(["git", "-C", wt, "apply", "--check", PATCH], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                g = subprocess.run(["python3", "-I", GATE, os.path.join(wt, "src")], capture_output=True, text=True)
                self.assertEqual(g.returncode, 0, g.stdout + g.stderr)
            finally:
                subprocess.run(["git", "-C", src, "worktree", "remove", "--force", wt], check=False)


if __name__ == "__main__":
    unittest.main()
