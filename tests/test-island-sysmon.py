#!/usr/bin/env python3
# AI-hint: Status island system monitor and mobile island geometry. Sysmon.qml (the native sampler: /proc/stat, /proc/meminfo,
# /proc/net/dev, /sys/class/drm gpu_busy_percent, /sys/class/hwmon temps) runs in a window-less Quickshell against fixture
# trees (MIOS_SYSMON_ROOT) and a fixture resolver (MIOS_TOML_GET): every source parses (positive), passthrough GPUs and
# their sensors are never read, missing / permission-denied / malformed sources degrade to "unavailable" without a log
# line (negative), and the sampler's CPU cost on the real host stays negligible. The SSOT tables ([desktop.topbar.sysmon],
# [desktop.mobile] island_motion / island_hold) are validated with negative controls, and the island geometry rule
# (PanelWindow.qml islandGoal: one edge margin clear of every window-controls tab on its row, bar height, top margin)
# is modelled from the hyprbars patch's own tab rule with touching / overlapping / wrong-height islands as negatives.
# (On a nested compositor the same rule is measured against the live island IPC state -- mobile.state island.px and
# tab_rects -- and the buttons the plugin drew.) Nothing here touches the live desktop. MIOS_QS_DIR points at a staged
# QML directory.
# AI-related: usr/share/mios/quickshell/Sysmon.qml, usr/share/mios/quickshell/SysmonItems.qml,
# usr/share/mios/quickshell/SysmonDetails.qml, usr/share/mios/quickshell/PanelWindow.qml, usr/share/mios/quickshell/Mobile.qml,
# usr/share/mios/mios.toml [desktop.topbar.sysmon] [desktop.mobile], usr/share/mios/hyprland/patches/hyprbars-overlay.patch
"""Island system monitor and mobile island geometry tests."""

from __future__ import annotations

import copy
import json
import math
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
import tomllib
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SSOT = os.environ.get("MIOS_SSOT") or os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
QS_DIR = os.environ.get("MIOS_QS_DIR") or os.path.join(ROOT, "usr", "share", "mios", "quickshell")
PATCH = os.path.join(ROOT, "usr", "share", "mios", "hyprland", "patches", "hyprbars-overlay.patch")
QS = shutil.which("quickshell")
ITEMS = {"cpu", "ram", "gpu", "temp", "net"}


def ssot() -> dict:
    with open(SSOT, "rb") as f:
        return tomllib.load(f)


# ---- SSOT --------------------------------------------------------------------------------------------------------
def sysmon_errors(d: dict) -> list[str]:
    t = d.get("desktop", {}).get("topbar", {}).get("sysmon")
    if not isinstance(t, dict):
        return ["[desktop.topbar.sysmon] missing"]
    e = []
    if not isinstance(t.get("enabled"), bool):
        e.append("enabled must be a boolean")
    if not isinstance(t.get("interval_ms"), int) or t["interval_ms"] < 500:
        e.append("interval_ms must be an integer >= 500 (low cost)")
    for k in ("items", "mobile_items"):
        v = t.get(k)
        if not isinstance(v, list) or not set(v) <= ITEMS or len(set(v)) != len(v):
            e.append(f"{k} must list distinct items of {sorted(ITEMS)}")
    for k in ("cpu_temp_chips", "gpu_temp_chips", "passthrough_drivers", "net_interfaces"):
        if not isinstance(t.get(k), list) or not all(isinstance(x, str) and x for x in t[k]):
            e.append(f"{k} must be a list of names")
    if "vfio-pci" not in t.get("passthrough_drivers", []):
        e.append("passthrough_drivers must name vfio-pci (a VM's GPU is never read)")
    if not (isinstance(t.get("gpu_smoothing"), (int, float)) and 0 < t["gpu_smoothing"] <= 1):
        e.append("gpu_smoothing must be in (0, 1]")
    for k in ("warn_pct", "warn_temp_c", "rediscover_s"):
        if not isinstance(t.get(k), (int, float)) or t[k] <= 0:
            e.append(f"{k} must be positive")
    return e


def island_errors(d: dict) -> list[str]:
    m = d["desktop"]["mobile"]
    leaves = {a.get("leaf") for a in d["desktop"]["hyprland"].get("animations", [])}
    e = []
    if m.get("island_motion") not in leaves:
        e.append(f"island_motion {m.get('island_motion')!r} is not a [desktop.hyprland] animations leaf")
    hold = m.get("island_hold")
    if not isinstance(hold, list) or not hold or not set(hold) <= leaves:
        e.append(f"island_hold {hold!r} must list [desktop.hyprland] animations leaves")
    return e


class Ssot(unittest.TestCase):
    def test_positive_tables(self):
        d = ssot()
        self.assertEqual(sysmon_errors(d), [])
        self.assertEqual(island_errors(d), [])
        self.assertLessEqual(1000 / d["desktop"]["topbar"]["sysmon"]["interval_ms"], 1.0, "default must be <= 1 Hz")

    def test_negative_bad_values(self):
        for key, val, frag in (("interval_ms", 100, "interval_ms"), ("items", ["cpu", "disk"], "items"),
                               ("mobile_items", ["cpu", "cpu"], "mobile_items"), ("passthrough_drivers", [], "vfio-pci"),
                               ("gpu_smoothing", 0, "gpu_smoothing"), ("enabled", "yes", "enabled")):
            d = copy.deepcopy(ssot())
            d["desktop"]["topbar"]["sysmon"][key] = val
            self.assertTrue(any(frag in x for x in sysmon_errors(d)), (key, sysmon_errors(d)))
        d = copy.deepcopy(ssot())
        del d["desktop"]["topbar"]["sysmon"]
        self.assertTrue(sysmon_errors(d))
        for key, val in (("island_motion", "slideIn"), ("island_hold", ["fadeOut", "nope"]), ("island_hold", [])):
            d = copy.deepcopy(ssot())
            d["desktop"]["mobile"][key] = val
            self.assertTrue(island_errors(d), (key, val))


# ---- island geometry model ---------------------------------------------------------------------------------------
def plugin_tab_rule(text: str) -> bool:
    """The patch still places mobile tabs by the rule PanelWindow.qml mirrors (rounding + fillet inset, button + 2 pad)."""
    added = "\n".join(l[1:] for l in text.splitlines() if l.startswith("+") and not l.startswith("+++"))
    return all(re.search(rx, added) for rx in (
        r"const double FILLET = std::min\(ROUND / 2\.0, H / 2\.0\);",
        r"const double INSET  = ROUND \+ FILLET;",
        r"const double W      = mobileButtonSize\(\) \+ 2\.0 \* PAD;",
        r"return \{SMobileTab\{INSET, W\}, SMobileTab\{windowWidth - INSET - W, W\}\};",
        r"return PM && W->position\(Desktop::View::IGeometric::GEOMETRIC_GOAL\)\.y - tabHeight\(\) < PM->m_position\.y;"))


def tabs_for(win: dict, d: dict) -> list[dict]:
    bars = d["desktop"]["hyprland"]["hyprbars"]
    H, r = bars["config"]["bar_height"], d["desktop"]["hyprland"]["config"]["decoration"]["rounding"]
    w = bars["buttons"][bars["mobile"]["close_button"]]["size"] + 2 * bars["config"]["pill_padding"]
    inset = r + min(r / 2, H / 2)
    top = win["y"] if win["y"] - H < 0 else win["y"] - H
    return [{"x0": win["x"] + inset, "x1": win["x"] + inset + w, "y0": top, "y1": top + H},
            {"x0": win["x"] + win["w"] - inset - w, "x1": win["x"] + win["w"] - inset, "y0": top, "y1": top + H}]


def island_goal(tabs: list[dict], out_w: float, margin: float, top: float, height: float) -> tuple[float, float]:
    """PanelWindow.qml islandGoal (1D free-span interval algorithm)."""
    if out_w <= 2 * margin:
        return 0.0, 0.0
    mid = out_w / 2.0
    bottom = top + height
    blocked = []
    for t in tabs:
        y0, y1 = t.get("y0", top), t.get("y1", bottom)
        if y0 >= bottom + margin or y1 <= top - margin:
            continue
        rx, rw = t["x0"], t["x1"] - t["x0"]
        b_start = max(margin, rx - margin)
        b_end = min(out_w - margin, rx + rw + margin)
        if b_end > b_start:
            blocked.append({"start": b_start, "end": b_end})
    blocked.sort(key=lambda b: b["start"])
    merged = []
    for b in blocked:
        if not merged:
            merged.append(dict(b))
        else:
            prev = merged[-1]
            if b["start"] <= prev["end"]:
                prev["end"] = max(prev["end"], b["end"])
            else:
                merged.append(dict(b))
    free = []
    cur = margin
    for m_val in merged:
        if m_val["start"] > cur:
            free.append({"start": cur, "end": m_val["start"], "len": m_val["start"] - cur})
        cur = max(cur, m_val["end"])
    if cur < out_w - margin:
        free.append({"start": cur, "end": out_w - margin, "len": (out_w - margin) - cur})
    if not free:
        return mid, 0.0
    best = None
    for f in free:
        if best is None:
            best = f
        elif f["len"] > best["len"] + 0.001:
            best = f
        elif abs(f["len"] - best["len"]) <= 0.001:
            f_center = (f["start"] + f["end"]) / 2.0
            best_center = (best["start"] + best["end"]) / 2.0
            if abs(f_center - mid) < abs(best_center - mid):
                best = f
    left = math.ceil(best["start"])
    right = math.floor(best["end"])
    w = max(0.0, right - left)
    if w < height:
        c = (best["start"] + best["end"]) / 2.0
        return c, 0.0
    return left, w


def tab_overlap(island: dict, tab: dict) -> float:
    """Horizontal overlap in pixels between island and tab on same row (0.0 if no overlap or hidden)."""
    if island.get("w", 0) <= 0:
        return 0.0
    if tab["y0"] >= island["y"] + island["h"] or tab["y1"] <= island["y"]:
        return 0.0
    return max(0.0, min(island["x"] + island["w"], tab["x1"]) - max(island["x"], tab["x0"]))


def tab_clearance(island: dict, tab: dict) -> float:
    """Horizontal clearance in pixels between island and tab on same row. Returns float('inf') if hidden or different row."""
    if island.get("w", 0) <= 0:
        return float("inf")
    if tab["y0"] >= island["y"] + island["h"] or tab["y1"] <= island["y"]:
        return float("inf")
    ix0, ix1 = island["x"], island["x"] + island["w"]
    tx0, tx1 = tab["x0"], tab["x1"]
    if tx0 < ix1 and tx1 > ix0:
        return -(min(ix1, tx1) - max(ix0, tx0))
    return min(abs(tx0 - ix1), abs(ix0 - tx1))


def check(island: dict, tabs: list[dict], margin: float, height: float, tol: float = 1.0) -> list[str]:
    e = []
    if island.get("w", 0) <= 0:
        return e  # hidden island: zero footprint on screen, clear of all tabs
    x0, x1, y0, y1 = island["x"], island["x"] + island["w"], island["y"], island["y"] + island["h"]
    if abs(island["h"] - height) > 0.5:
        e.append(f"height {island['h']} != {height}")
    if abs(island["y"] - margin) > 0.5:
        e.append(f"top {island['y']} != {margin}")
    for t in tabs:
        if t["y0"] >= y1 + margin or t["y1"] <= y0 - margin:
            continue
        if t["x0"] < x1 and t["x1"] > x0:
            e.append(f"overlaps tab {t}")
        elif min(abs(t["x0"] - x1), abs(x0 - t["x1"])) < margin - tol:
            e.append(f"closer than {margin} to tab {t}")
    return e


class Geometry(unittest.TestCase):
    def setUp(self):
        self.d = ssot()
        self.m = self.d["theme"]["shell"]["edge_margin_px"]
        self.h = self.d["theme"]["shell"]["top_height_px"]

    def test_positive_plugin_rule_is_the_mirrored_one(self):
        with open(PATCH, encoding="utf-8") as f:
            self.assertTrue(plugin_tab_rule(f.read()))
        with open(os.path.join(QS_DIR, "PanelWindow.qml"), encoding="utf-8") as f:
            qml = f.read()
        for frag in ("Glass.mobileTabWidth", "Glass.mobileTabInset(H, windowRadius)", "(y - H < 0 ? y : y - H)",
                     "const bStart = Math.max(islandMargin, r.x - islandMargin)",
                     "const bEnd = Math.min(W - islandMargin, r.x + r.w + islandMargin)",
                     "r.y >= bottom + islandMargin || r.y + r.h <= top - islandMargin", "islandHold.restart()"):
            self.assertIn(frag, qml)
        self.assertNotIn("left = c - islandHeight / 2", qml, "PanelWindow.qml must not use expanding center fallback")
        self.assertNotIn("left = Math.max(left, r.x + r.w + islandMargin)", qml)
        self.assertNotIn("right = Math.min(right, r.x - islandMargin)", qml)
        self.assertIn("return ({ x: c, w: 0 })", qml, "PanelWindow.qml must collapse island when space is insufficient")

    def test_negative_patch_drift_detected(self):
        with open(PATCH, encoding="utf-8") as f:
            text = f.read()
        self.assertFalse(plugin_tab_rule(text.replace("const double INSET  = ROUND + FILLET;", "const double INSET  = ROUND;")))

    def test_positive_phone_0_1_2_windows(self):
        out_w, gaps = 440, self.d["desktop"]["mobile"]["gaps_out_px"]
        top_win = self.m + self.h + self.m          # margins.top + exclusive zone (bar + one edge margin)
        cases = {
            0: [],
            1: [{"x": gaps, "y": top_win, "w": out_w - 2 * gaps}],
            2: [{"x": gaps, "y": top_win, "w": out_w - 2 * gaps}, {"x": 70, "y": 64, "w": 300}],   # a float near the top
        }
        for n, wins in cases.items():
            tabs = [t for w in wins for t in tabs_for(w, self.d)]
            x, w = island_goal(tabs, out_w, self.m, self.m, self.h)
            isl = {"x": x, "w": w, "y": self.m, "h": self.h}
            self.assertEqual(check(isl, tabs, self.m, self.h), [], (n, isl, tabs))
            if n == 0:
                self.assertEqual((x, w), (self.m, out_w - 2 * self.m))
            else:   # fills the room: exactly one edge margin from the bounding tabs
                row = [t for t in tabs if t["y0"] < self.m + self.h + self.m]
                self.assertAlmostEqual(x - max(t["x1"] for t in row if t["x1"] <= x), self.m)
                self.assertAlmostEqual(min(t["x0"] for t in row if t["x0"] >= x + w) - (x + w), self.m)

    def test_negative_touching_overlapping_or_wrong_height(self):
        tabs = tabs_for({"x": 6, "y": 44, "w": 428}, self.d)
        x, w = island_goal(tabs, 440, self.m, self.m, self.h)
        good = {"x": x, "w": w, "y": self.m, "h": self.h}
        self.assertEqual(check(good, tabs, self.m, self.h), [])
        for bad in (dict(good, x=tabs[0]["x1"], w=w + (x - tabs[0]["x1"])),          # touching the left tab
                    dict(good, x=tabs[0]["x1"] - 5, w=w + 10),                      # overlapping it
                    dict(good, h=self.h + 4),                                       # a different height
                    dict(good, y=0)):                                               # not at the top margin
            self.assertTrue(check(bad, tabs, self.m, self.h), bad)

    def test_positive_and_negative_3_plus_windows(self):
        out_w = 440
        top_win = self.m + self.h + self.m

        # 1. 3 tiled windows (middle window straddles center): 140 + 160 + 140
        wins_3tiled = [{"x": 0, "y": top_win, "w": 140}, {"x": 140, "y": top_win, "w": 160}, {"x": 300, "y": top_win, "w": 140}]
        tabs = [t for w in wins_3tiled for t in tabs_for(w, self.d)]
        x, w = island_goal(tabs, out_w, self.m, self.m, self.h)
        isl = {"x": x, "w": w, "y": self.m, "h": self.h}
        self.assertEqual(max(tab_overlap(isl, t) for t in tabs), 0.0)
        self.assertTrue(w == 0.0 or (w >= self.h and min(tab_clearance(isl, t) for t in tabs) >= self.m - 1e-3))
        self.assertEqual(check(isl, tabs, self.m, self.h), [])

        # Negative control for 3 tiled: old centered round island overlaps middle window tabs by 7.0 px
        bad_isl = {"x": out_w / 2.0 - self.h / 2.0, "w": self.h, "y": self.m, "h": self.h}
        self.assertAlmostEqual(max(tab_overlap(bad_isl, t) for t in tabs), 7.0, places=1)
        self.assertTrue(any("overlaps tab" in e for e in check(bad_isl, tabs, self.m, self.h)))

        # 2. 3 floating windows (staggered across top row) -> 0.0 px overlap, clearance >= 8.0 px or hidden
        wins_3float = [{"x": 10, "y": top_win, "w": 180}, {"x": 130, "y": top_win, "w": 180}, {"x": 250, "y": top_win, "w": 180}]
        tabs = [t for w in wins_3float for t in tabs_for(w, self.d)]
        x, w = island_goal(tabs, out_w, self.m, self.m, self.h)
        isl = {"x": x, "w": w, "y": self.m, "h": self.h}
        self.assertEqual(max(tab_overlap(isl, t) for t in tabs), 0.0)
        self.assertTrue(w == 0.0 or min(tab_clearance(isl, t) for t in tabs) >= self.m - 1e-3)
        self.assertEqual(check(isl, tabs, self.m, self.h), [])

        # 3. 4 tiled/floating windows -> 0.0 px overlap, clearance >= 8.0 px or hidden
        wins_4 = [{"x": 6, "y": top_win, "w": 90}, {"x": 100, "y": top_win, "w": 90},
                  {"x": 200, "y": top_win, "w": 110}, {"x": 320, "y": top_win, "w": 110}]
        tabs = [t for w in wins_4 for t in tabs_for(w, self.d)]
        x, w = island_goal(tabs, out_w, self.m, self.m, self.h)
        isl = {"x": x, "w": w, "y": self.m, "h": self.h}
        self.assertEqual(max(tab_overlap(isl, t) for t in tabs), 0.0)
        self.assertTrue(w == 0.0 or min(tab_clearance(isl, t) for t in tabs) >= self.m - 1e-3)
        self.assertEqual(check(isl, tabs, self.m, self.h), [])

        # 4. 3 windows leaving wide central room -> island visible, clearance >= 8.0 px
        wins_room = [{"x": 6, "y": top_win, "w": 90}, {"x": 100, "y": top_win, "w": 90}, {"x": 340, "y": top_win, "w": 94}]
        tabs = [t for w in wins_room for t in tabs_for(w, self.d)]
        x, w = island_goal(tabs, out_w, self.m, self.m, self.h)
        isl = {"x": x, "w": w, "y": self.m, "h": self.h}
        self.assertGreaterEqual(w, self.h)
        self.assertEqual(max(tab_overlap(isl, t) for t in tabs), 0.0)
        self.assertGreaterEqual(min(tab_clearance(isl, t) for t in tabs), self.m - 1e-3)
        self.assertEqual(check(isl, tabs, self.m, self.h), [])

    def test_positive_and_negative_tiny_windows(self):
        out_w = 440
        top_win = self.m + self.h + self.m
        bars = self.d["desktop"]["hyprland"]["hyprbars"]
        H, r = bars["config"]["bar_height"], self.d["desktop"]["hyprland"]["config"]["decoration"]["rounding"]
        tab_w = bars["buttons"][bars["mobile"]["close_button"]]["size"] + 2 * bars["config"]["pill_padding"]
        inset = r + min(r / 2, H / 2)

        # 1. Fully constrained gaps (< 44 px between tabs, outer spans blocked by side tabs)
        # Must collapse island to width 0 with 0.0 px overlap
        for gap in [0, 2, 5, 8, 12, 16, 20, 24, 27, 28, 30, 35, 40, 43]:
            mid = out_w / 2.0
            tabs = [
                {"x0": self.m, "x1": mid - gap / 2.0, "y0": top_win - H, "y1": top_win},
                {"x0": mid + gap / 2.0, "x1": out_w - self.m, "y0": top_win - H, "y1": top_win}
            ]
            x, w = island_goal(tabs, out_w, self.m, self.m, self.h)
            isl = {"x": x, "w": w, "y": self.m, "h": self.h}
            self.assertEqual(max(tab_overlap(isl, t) for t in tabs), 0.0, f"gap {gap} overlapped tabs")
            self.assertEqual(w, 0.0, f"gap {gap} should hide island")
            self.assertEqual(check(isl, tabs, self.m, self.h), [])

        # 2. Fully constrained gaps >= 44 px fit island with w >= 28 px and clearance >= 8.0 px
        for gap in [44, 50, 70, 100]:
            mid = out_w / 2.0
            tabs = [
                {"x0": self.m, "x1": mid - gap / 2.0, "y0": top_win - H, "y1": top_win},
                {"x0": mid + gap / 2.0, "x1": out_w - self.m, "y0": top_win - H, "y1": top_win}
            ]
            x, w = island_goal(tabs, out_w, self.m, self.m, self.h)
            isl = {"x": x, "w": w, "y": self.m, "h": self.h}
            self.assertGreaterEqual(w, self.h)
            self.assertEqual(max(tab_overlap(isl, t) for t in tabs), 0.0)
            self.assertGreaterEqual(min(tab_clearance(isl, t) for t in tabs), self.m - 1e-3)
            self.assertEqual(check(isl, tabs, self.m, self.h), [])

        # 3. Centered floating tiny window (where outer spans are open)
        # The algorithm chooses open span with 0.0 px overlap and >= 8.0 px clearance
        for gap in [0, 10, 20, 30]:
            win_w = 2 * inset + 2 * tab_w + gap
            win_x = out_w / 2.0 - win_w / 2.0
            tabs = tabs_for({"x": win_x, "y": top_win, "w": win_w}, self.d)
            x, w = island_goal(tabs, out_w, self.m, self.m, self.h)
            isl = {"x": x, "w": w, "y": self.m, "h": self.h}
            self.assertEqual(max(tab_overlap(isl, t) for t in tabs), 0.0)
            self.assertTrue(w == 0.0 or min(tab_clearance(isl, t) for t in tabs) >= self.m - 1e-3)
            self.assertEqual(check(isl, tabs, self.m, self.h), [])

        # 4. Negative control: gap = 20 px with old centered 28 px round island overlaps each tab by 4.0 px
        win_w = 2 * inset + 2 * tab_w + 20
        win_x = out_w / 2.0 - win_w / 2.0
        tabs = tabs_for({"x": win_x, "y": top_win, "w": win_w}, self.d)
        bad_isl = {"x": (out_w - self.h) / 2.0, "w": self.h, "y": self.m, "h": self.h}
        self.assertAlmostEqual(tab_overlap(bad_isl, tabs[0]), 4.0, places=1)
        self.assertAlmostEqual(tab_overlap(bad_isl, tabs[1]), 4.0, places=1)
        self.assertTrue(any("overlaps tab" in e for e in check(bad_isl, tabs, self.m, self.h)))

    def test_positive_scaled_outputs_clearance_or_hidden(self):
        # High-DPI and remote scaled outputs (scale 1.0, 1.5, 2.0, 2.5, 3.0)
        bars = self.d["desktop"]["hyprland"]["hyprbars"]
        H_base = bars["config"]["bar_height"]

        for scale in [1.0, 1.5, 2.0, 2.5, 3.0]:
            out_w = 440.0 * scale
            m = self.m * scale
            h = self.h * scale
            H = H_base * scale
            top_win = (self.m + self.h + self.m) * scale
            mid = out_w / 2.0

            # Constrained gap (< 44 * scale px between tabs) -> collapses to 0 with 0.0 px overlap
            tabs_narrow = [
                {"x0": m, "x1": mid - 10.0 * scale, "y0": top_win - H, "y1": top_win},
                {"x0": mid + 10.0 * scale, "x1": out_w - m, "y0": top_win - H, "y1": top_win}
            ]
            x, w = island_goal(tabs_narrow, out_w, m, m, h)
            isl = {"x": x, "w": w, "y": m, "h": h}
            self.assertEqual(max(tab_overlap(isl, t) for t in tabs_narrow), 0.0)
            self.assertEqual(w, 0.0)
            self.assertEqual(check(isl, tabs_narrow, m, h), [])

            # Wide gap (>= 44 * scale px between tabs) -> fits with clearance >= m
            tabs_wide = [
                {"x0": m, "x1": mid - 30.0 * scale, "y0": top_win - H, "y1": top_win},
                {"x0": mid + 30.0 * scale, "x1": out_w - m, "y0": top_win - H, "y1": top_win}
            ]
            x, w = island_goal(tabs_wide, out_w, m, m, h)
            isl = {"x": x, "w": w, "y": m, "h": h}
            self.assertGreaterEqual(w, h)
            self.assertEqual(max(tab_overlap(isl, t) for t in tabs_wide), 0.0)
            self.assertGreaterEqual(min(tab_clearance(isl, t) for t in tabs_wide), m - 1e-3)
            self.assertEqual(check(isl, tabs_wide, m, h), [])


# ---- the native sampler, window-less ------------------------------------------------------------------------------
SHELL = """import QtQuick
import Quickshell
ShellRoot { property bool on: Sysmon.active }
"""


def write(path: str, text: str, mode: int | None = None) -> None:
    """Replace by rename, so the sampler never reads a half-written counter file."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
    if mode is not None:
        os.chmod(tmp, mode)
    os.replace(tmp, path)


def stat_text(user: int, system: int, idle: int) -> str:
    return (f"cpu  {user} 0 {system} {idle} 0 0 0 0 0 0\n"
            f"cpu0 {user // 2} 0 {system // 2} {idle // 2} 0 0 0 0 0 0\n"
            f"cpu1 {user // 2} 0 {system // 2} {idle // 2} 0 0 0 0 0 0\nintr 1 2 3\nctxt 99\n")


def netdev_text(rx: int, tx: int) -> str:
    return ("Inter-|   Receive                                                |  Transmit\n"
            " face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed\n"
            f"    lo: 999999 10 0 0 0 0 0 0 999999 10 0 0 0 0 0 0\n"
            f"  eth0: {rx} 10 0 0 0 0 0 0 {tx} 10 0 0 0 0 0 0\n"
            f"veth12: {rx * 3} 10 0 0 0 0 0 0 {tx * 3} 10 0 0 0 0 0 0\n")


def fixture(root: str) -> None:
    write(f"{root}/proc/stat", stat_text(100, 100, 800))
    write(f"{root}/proc/meminfo", "MemTotal:       16000000 kB\nMemFree:  1000000 kB\nMemAvailable:    4000000 kB\n"
                                  "SwapTotal:       2000000 kB\nSwapFree:        1000000 kB\n")
    write(f"{root}/proc/net/dev", netdev_text(1000, 1000))
    drivers = f"{root}/sys/bus/pci/drivers"
    for drv in ("amdgpu", "vfio-pci", "i915", "k10temp"):
        os.makedirs(f"{drivers}/{drv}", exist_ok=True)

    def dev(name: str, drv: str) -> str:
        p = f"{root}/sys/devices/{name}"
        os.makedirs(p, exist_ok=True)
        os.symlink(f"{drivers}/{drv}", f"{p}/driver")
        return p
    # GPUs: card0 is a VM's (vfio-pci), card1 the desktop's (connected), card2 a second host GPU
    write(f"{root}/sys/class/powercap/intel-rapl:0/name", "package-0\n")
    write(f"{root}/sys/class/powercap/intel-rapl:0/energy_uj", "10000000\n")
    write(f"{root}/sys/class/powercap/intel-rapl:0/max_energy_range_uj", "65532610987\n")
    for card, drv, busy, conn, vga, uw in (("card0", "vfio-pci", "99", "disconnected", "0", "350000000"),
                                           ("card1", "amdgpu", "40", "connected", "1", "120000000"),
                                           ("card2", "i915", "10", "disconnected", "0", "")):
        d = dev(f"pci-{card}", drv)
        write(f"{d}/gpu_busy_percent", busy + "\n")
        write(f"{d}/boot_vga", vga + "\n")
        if uw:
            write(f"{d}/hwmon/hwmon_{card}/power1_input", uw + "\n")
        os.makedirs(f"{root}/sys/class/drm/{card}", exist_ok=True)
        os.symlink(d, f"{root}/sys/class/drm/{card}/device")
        write(f"{root}/sys/class/drm/{card}-HDMI-A-1/status", conn + "\n")
    # sensors: CPU (k10temp), the desktop GPU (amdgpu), a VM's GPU (amdgpu on vfio-pci: never read), nvme (not CPU/GPU)
    for i, (name, drv, temps) in enumerate((("k10temp", "k10temp", {"temp1": ("Tctl", "61000"), "temp3": ("Tccd1", "70500")}),
                                             ("amdgpu", "amdgpu", {"temp1": ("edge", "50000")}),
                                             ("amdgpu", "vfio-pci", {"temp1": ("edge", "95000")}),
                                             ("nvme", "", {"temp1": ("Composite", "80000")}))):
        h = f"{root}/sys/class/hwmon/hwmon{i}"
        write(f"{h}/name", name + "\n")
        if drv:
            os.symlink(dev(f"hw{i}", drv), f"{h}/device")
        for t, (label, val) in temps.items():
            write(f"{h}/{t}_input", val + "\n")
            write(f"{h}/{t}_label", label + "\n")
    # interfaces: eth0 is physical (has a device), veth12 and lo are not
    os.makedirs(f"{root}/sys/class/net/eth0/device", exist_ok=True)
    os.makedirs(f"{root}/sys/class/net/veth12", exist_ok=True)
    os.makedirs(f"{root}/sys/class/net/lo", exist_ok=True)


@unittest.skipUnless(QS, "quickshell missing")
class Sampler(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-sysmon-")
        self.qs = os.path.join(self.tmp, "qs")
        os.makedirs(self.qs)
        for name in ("Sysmon.qml", "Theme.qml"):
            shutil.copy(os.path.join(QS_DIR, name), self.qs)
        write(os.path.join(self.qs, "shell.qml"), SHELL)
        self.procs = []
        self.logs = []

    def tearDown(self):
        for f in self.logs:
            f.close()
        for p in self.procs:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        for dirpath, dirnames, filenames in os.walk(self.tmp):
            for n in dirnames + filenames:
                p = os.path.join(dirpath, n)
                if not os.path.islink(p):
                    os.chmod(p, 0o700)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start(self, root: str | None, cfg: dict) -> str:
        resolver = os.path.join(self.tmp, "toml-get")
        write(resolver, "#!/bin/sh\ncat <<'EOF'\n" + json.dumps(cfg) + "\nEOF\n", 0o755)
        env = dict(os.environ, QT_QPA_PLATFORM="offscreen", MIOS_TOML_GET=resolver,
                   MIOS_THEME_BRIDGE=os.path.join(self.tmp, "no-bridge.json"))
        if root is not None:
            env["MIOS_SYSMON_ROOT"] = root
        self.log = os.path.join(self.tmp, "qs.log")
        out = open(self.log, "w")
        self.logs.append(out)
        p = subprocess.Popen([QS, "--path", os.path.join(self.qs, "shell.qml")], env=env, stdout=out, stderr=subprocess.STDOUT)
        self.procs.append(p)
        self.pid = p.pid
        return self.qs

    def state(self) -> dict:
        r = subprocess.run([QS, "ipc", "--path", os.path.join(self.qs, "shell.qml"), "call", "sysmon", "state"],
                           capture_output=True, text=True, timeout=20)
        return json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}

    def wait(self, cond, timeout=12.0) -> dict:
        end = time.monotonic() + timeout
        s = {}
        while time.monotonic() < end:
            s = self.state()
            if s and cond(s):
                return s
            time.sleep(0.2)
        self.fail(f"condition not met; last state {s}")

    def sysmon_log(self) -> list[str]:
        with open(self.log, errors="replace") as f:
            text = re.sub(r"\x1b\[[0-9;]*m", "", f.read())
        return [l for l in text.splitlines() if "Sysmon" in l or "TypeError" in l or "ReferenceError" in l]

    def test_positive_line_file_published(self):
        # MiOS-Tmux reads the live line from a file ("EPOCH<TAB>LINE<TAB>COMPACT") instead of spawning `quickshell ipc`
        # per refresh; COMPACT (mobile_items, icon+value, no watts) feeds narrow tmux clients (mios-sysmon-line --compact)
        root = os.path.join(self.tmp, "root")
        fixture(root)
        line_file = os.path.join(self.tmp, "mios-sysmon.line")
        os.environ["MIOS_SYSMON_LINE_FILE"] = line_file
        try:
            self.start(root, {"interval_ms": 500})
        finally:
            os.environ.pop("MIOS_SYSMON_LINE_FILE", None)
        s = self.wait(lambda s: s.get("samples", 0) >= 3 and s.get("status_line"))
        self.wait(lambda s: os.path.exists(line_file))
        ts, line, compact = open(line_file).read().rstrip("\n").split("\t", 2)
        self.assertLessEqual(abs(time.time() - int(ts)), 5)
        self.assertTrue(line and "\U000f035b" in line, line)              # the RAM item: the island's own line
        self.assertTrue(compact and "\U000f035b" in compact, compact)
        self.assertNotIn("\t", compact)
        self.assertNotRegex(compact, r"\d+W\b")                          # no watts on the narrow line
        self.assertNotRegex(compact, "[\U000f0ee0\U000f035b\U000f08ae\U000f050f\U000f0045] ")  # icon+value
        self.wait(lambda s: open(line_file).read().rstrip("\n").split("\t")[1:] == [s["status_line"], s["compact_line"]])

    def test_positive_sysmon_line_compact_reads_the_published_field(self):
        # mios-sysmon-line --compact COLS prints the compact field cut to COLS columns (whole items, from the end);
        # a two-field file (an older shell) is compacted from the full line; a stale file is never shown as live.
        script = os.path.join(ROOT, "usr", "libexec", "mios", "mios-sysmon-line")
        rt = os.path.join(self.tmp, "rt")
        os.makedirs(rt, mode=0o700, exist_ok=True)
        full = "\U000f0ee0 12% 45W  \U000f035b 45%  \U000f08ae 3% 18W  \U000f050f 52\u00b0  \U000f0045 14K \U000f005d46K"
        compact = "\U000f0ee012% \U000f035b45% \U000f08ae3% \U000f050f52\u00b0 \U000f004514K\U000f005d46K"

        def run(record: str, *args: str) -> str:
            with open(os.path.join(rt, "mios-sysmon.line"), "w") as f:
                f.write(record)
            env = {"PATH": "/usr/bin:/bin", "XDG_RUNTIME_DIR": rt, "LANG": "C"}
            return subprocess.run(["bash", script, *args], env=env, capture_output=True, text=True,
                                  timeout=10).stdout.rstrip("\n")

        now = str(int(time.time()))
        four = "\U000f0ee012% \U000f035b45% \U000f08ae3% \U000f050f52\u00b0"
        self.assertEqual(run(f"{now}\t{full}\t{compact}\n"), full)                  # wide clients: unchanged
        self.assertEqual(run(f"{now}\t{full}\t{compact}\n", "--compact", "0"), compact)
        self.assertEqual(run(f"{now}\t{full}\t{compact}\n", "--compact", "24"), four)
        self.assertEqual(run(f"{now}\t{full}\n", "--compact", "24"), four)           # older two-field publisher
        self.assertEqual(run(f"{now}\t{full}\t{compact}\n", "--compact", "24", "--ascii"), "CPU12% RAM45% GPU3% 52C")
        self.assertEqual(run(f"{now}\t{full}\t\n", "--compact", "24"), "")          # mobile_items = []: nothing
        stale = run(f"{int(now) - 60}\t{full}\t{compact}\n", "--compact", "24")
        self.assertNotEqual(stale, four)                                             # live /proc fallback instead
        self.assertNotRegex(stale, r"\d+W\b")

    def test_negative_apu_socket_power_is_not_gpu_watts(self):
        # amdgpu's power sensor on an APU (gpu_metrics table v2/v3) is the whole socket's power: not GPU watts.
        # A dGPU's table is v1, and its power stays (positive control in the same fixture shape).
        for rev, expect in ((2, None), (1, 120)):
            with self.subTest(rev=rev):
                root = os.path.join(self.tmp, f"root-apu{rev}")
                fixture(root)
                with open(f"{root}/sys/class/drm/card1/device/gpu_metrics", "wb") as f:
                    f.write(bytes([0x60, 0x00, rev, 0x01]) + bytes(92))
                self.start(root, {"interval_ms": 500})
                s = self.wait(lambda s: s.get("scanned") and s.get("samples", 0) >= 2 and s.get("gpu") is not None)
                if expect is None:
                    self.assertIsNone(s["gpu_watts"], s["gpu_watts"])
                else:
                    self.wait(lambda s: s.get("gpu_watts") == expect)
                for p in self.procs:
                    p.terminate()
                    p.wait(5)
                self.procs.clear()

    def test_positive_root_sampler_preferred_over_rapl(self):
        # mios-power-sampler's coarse value (integer uW) is the CPU watts source; RAPL energy_uj, root-only on a real
        # system (PLATYPUS mitigation), is then not even discovered. 0.4 W also proves the uW reading is never mW-scaled.
        root = os.path.join(self.tmp, "root")
        fixture(root)
        write(f"{root}/run/mios/power/cpu_package_uw", "37400000\n")
        self.start(root, {"interval_ms": 500})
        s = self.wait(lambda s: s.get("scanned") and s.get("cpu_watts") is not None)
        self.assertAlmostEqual(s["cpu_watts"], 37.4, delta=0.05)
        self.assertEqual(s["sources"]["cpu_power"], f"{root}/run/mios/power/cpu_package_uw")
        write(f"{root}/run/mios/power/cpu_package_uw", "400000\n")
        s = self.wait(lambda s: s.get("cpu_watts") is not None and s["cpu_watts"] < 30)
        self.wait(lambda s: abs(s["cpu_watts"] - 0.4) < 0.1, timeout=15)

    def test_positive_fixture_every_source(self):
        root = os.path.join(self.tmp, "root")
        fixture(root)
        self.start(root, {"interval_ms": 500})
        s = self.wait(lambda s: s.get("scanned") and s.get("samples", 0) >= 2)
        n = s["samples"]
        write(f"{root}/proc/stat", stat_text(200, 200, 1400))          # +200 busy of +800 total = 25 %
        # eth0 receives 1 MB/s and sends 250 kB/s (veth12 three times that: must not count)
        stop = threading.Event()

        def traffic():
            t0, rx, tx, e0 = time.monotonic(), 1000, 1000, 10_000_000
            while not stop.is_set():
                dt = time.monotonic() - t0
                write(f"{root}/proc/net/dev", netdev_text(rx + int(1_000_000 * dt), tx + int(250_000 * dt)))
                write(f"{root}/sys/class/powercap/intel-rapl:0/energy_uj", f"{e0 + int(45_000_000 * dt)}\n")
                time.sleep(0.05)
        th = threading.Thread(target=traffic)
        th.start()
        try:
            time.sleep(2.2)
            s = self.wait(lambda s: s["samples"] >= n + 3 and s["cpu"] is not None and s["net_down"] is not None)
        finally:
            stop.set()
            th.join()
        self.assertEqual(s["interval_ms"], 500)
        self.assertEqual(s["cores"], 2)
        self.assertAlmostEqual(s["ram"], 75.0)
        self.assertEqual((s["ram_used_kib"], s["swap_used_kib"]), (12_000_000, 1_000_000))
        self.assertEqual(s["gpu"], 40)                                   # the desktop's GPU, not the VM's 99
        self.assertEqual(s["gpu_watts"], 120)                            # card1 120 W, never the VM's 350 W
        self.assertEqual(s["gpus"][0]["watts"], 120)
        self.assertEqual(s["text"]["gpu_watts"], "120W")
        self.assertTrue(s["cpu_watts"] is not None and 25.0 <= s["cpu_watts"] <= 65.0, s["cpu_watts"])
        self.assertIn("120W", s["status_line"])
        self.assertEqual([g["card"] for g in s["gpus"]], ["card1", "card2"])
        self.assertEqual(sorted(s["sources"]["gpus"]), ["card1:amdgpu", "card2:i915"])
        self.assertEqual(s["temp"], 70.5)                                # hottest CPU/GPU; nvme 80 and the VM GPU's 95 ignored
        self.assertEqual(sorted(t["label"] for t in s["temps"]), ["Tccd1", "Tctl", "edge"])
        self.assertEqual(s["sources"]["nets"], ["eth0"])
        self.assertEqual([n["name"] for n in s["nets"]], ["eth0"])       # veth/lo not counted
        self.assertAlmostEqual(s["cpu"], 25.0, places=2)
        self.assertTrue(700_000 <= s["net_down"] <= 1_300_000, s["net_down"])
        self.assertTrue(175_000 <= s["net_up"] <= 325_000, s["net_up"])
        self.assertEqual(s["text"]["cpu"], " 25%")
        self.assertEqual(s["text"]["temp"], " 71°")
        self.assertEqual(len(s["text"]["down"]), 4)
        self.assertEqual(self.sysmon_log(), [])

    def test_negative_missing_sources_degrade(self):
        root = os.path.join(self.tmp, "empty")
        os.makedirs(root)
        self.start(root, {"interval_ms": 500})
        s = self.wait(lambda s: s.get("scanned"))
        time.sleep(1.2)
        s = self.state()
        for k in ("cpu", "ram", "gpu", "temp", "net_down", "net_up"):
            self.assertIsNone(s[k], k)
        self.assertEqual((s["gpus"], s["temps"], s["nets"]), ([], [], []))
        self.assertEqual(s["text"]["cpu"], " --%")
        self.assertEqual(self.sysmon_log(), [])

    def test_negative_permission_denied_and_malformed_degrade(self):
        if os.geteuid() == 0:
            self.skipTest("root reads mode-000 files")
        root = os.path.join(self.tmp, "root")
        fixture(root)
        os.chmod(f"{root}/proc/stat", 0)
        os.chmod(f"{root}/sys/class/hwmon/hwmon0/temp3_input", 0)        # the hottest sensor: unreadable
        os.chmod(f"{root}/sys/devices/pci-card1/gpu_busy_percent", 0)    # the primary GPU: unreadable
        write(f"{root}/proc/meminfo", "garbage\n")
        self.start(root, {"interval_ms": 500})
        s = self.wait(lambda s: s.get("scanned") and s.get("samples", 0) == 0 and s["temp"] is not None)
        time.sleep(1.2)
        s = self.state()
        self.assertIsNone(s["cpu"])
        self.assertIsNone(s["ram"])
        self.assertEqual(s["temp"], 61.0)                               # the readable sensors still count
        self.assertEqual(s["gpu"], 10)                                  # the next host GPU; never the VM's
        self.assertEqual(self.sysmon_log(), [])

    def test_negative_disabled_samples_nothing_and_interval_clamped(self):
        root = os.path.join(self.tmp, "root")
        fixture(root)
        self.start(root, {"enabled": False, "interval_ms": 10})
        time.sleep(2.0)
        s = self.state()
        self.assertFalse(s["active"])
        self.assertEqual(s["samples"], 0)
        self.assertEqual(s["items"], [])
        self.assertEqual(s["interval_ms"], 500)

    def test_cost_on_this_host_is_negligible(self):
        if not os.path.isdir("/proc") or not os.path.isdir("/sys/class"):
            self.skipTest("no /proc or /sys")
        self.start(None, {})
        self.wait(lambda s: s.get("samples", 0) >= 2)

        def ticks() -> int:
            with open(f"/proc/{self.pid}/stat") as f:
                v = f.read().rsplit(")", 1)[1].split()
            return int(v[11]) + int(v[12])
        hz = os.sysconf("SC_CLK_TCK")
        t0, c0 = time.monotonic(), ticks()
        time.sleep(10)
        pct = 100 * (ticks() - c0) / hz / (time.monotonic() - t0)
        print(f"\n[test-island-sysmon] sampler shell CPU over 10 s: {pct:.2f}% of one core", flush=True)
        self.assertLess(pct, 1.0, f"sampler shell used {pct:.2f}% of one CPU")


if __name__ == "__main__":
    unittest.main()
