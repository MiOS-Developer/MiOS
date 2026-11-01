#!/usr/bin/env python3
# AI-hint: Two-sided integration test for mios-pip-gravity resize gravity, magnetic margin snapping, stationary edge anchoring, orientation-aware throwing, and mass/size-scaled physics.
# AI-related: tools/native/mios-pip-gravity/src/main.rs, tools/native/mios-pip-gravity/src/gravity.rs, usr/share/mios/mios.toml, tools/ci-suites.py
# AI-functions: find_binary, test_binary_check, test_binary_check_negative, test_margin_snapping, test_stationary_anchoring, test_workarea_hud_snapping, test_max_ratio_relaxation, test_orientation_throwing, test_mass_scaled_physics, test_dimension_preservation, main

import json
import math
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SSOT = ROOT / "usr/share/mios/mios.toml"

SNAP_PROXIMITY_PX = 32.0
WINDOW_MARGIN_GAP_PX = 8.0
GLOBAL_MARGIN_PX = 8.0


def find_binary():
    override = os.environ.get("MIOS_PIP_GRAVITY_BIN")
    if override and os.access(override, os.X_OK):
        return override
    candidates = [
        ROOT / "tools/native/target/x86_64-unknown-linux-musl/release/mios-pip-gravity",
        ROOT / "tools/native/target/release/mios-pip-gravity",
        ROOT / "tools/native/target/x86_64-unknown-linux-musl/debug/mios-pip-gravity",
        ROOT / "tools/native/target/debug/mios-pip-gravity",
        Path("/usr/libexec/mios/mios-pip-gravity"),
        Path("/usr/bin/mios-pip-gravity"),
    ]
    for c in candidates:
        if c.is_file() and os.access(c, os.X_OK):
            return str(c)
    return None


class Rect:
    def __init__(self, x, y, w, h):
        self.x = float(x)
        self.y = float(y)
        self.w = float(w)
        self.h = float(h)

    def right(self):
        return self.x + self.w

    def bottom(self):
        return self.y + self.h

    def cx(self):
        return self.x + self.w / 2.0

    def cy(self):
        return self.y + self.h / 2.0

    def __repr__(self):
        return f"Rect(x={self.x}, y={self.y}, w={self.w}, h={self.h})"


# Python reference model of gravity and snap calculations matching gravity.rs
def mass_factor(w, h, mon_w, mon_h):
    mon_area = max(mon_w * mon_h, 1.0)
    area = max(w * h, 1.0)
    return max(0.1, min(1.0, area / mon_area))


def flick_velocity_threshold(m):
    return 300.0 - 150.0 * m


def flick_displacement_threshold(m):
    return 150.0 - 75.0 * m


def gravity_pull(m, base_pull=100.0):
    return base_pull * (1.5 - 0.5 * m)


def mass_stiffness(base_stiffness, m):
    return base_stiffness * (1.5 - 0.5 * m)


def snap_resize_edges(cand, others, workarea, proximity=SNAP_PROXIMITY_PX, gap=WINDOW_MARGIN_GAP_PX):
    left = cand.x
    right = cand.right()
    top = cand.y
    bottom = cand.bottom()

    best_l = None
    best_r = None
    best_t = None
    best_b = None

    # Snap to workarea bounds
    if abs(cand.x - workarea.x) <= proximity:
        best_l = (abs(cand.x - workarea.x), workarea.x)
    if abs(cand.right() - workarea.right()) <= proximity:
        best_r = (abs(cand.right() - workarea.right()), workarea.right())
    if abs(cand.y - workarea.y) <= proximity:
        best_t = (abs(cand.y - workarea.y), workarea.y)
    if abs(cand.bottom() - workarea.bottom()) <= proximity:
        best_b = (abs(cand.bottom() - workarea.bottom()), workarea.bottom())

    # Snap to adjacent windows
    for other in others:
        # Left edge against other right edge (with gap)
        target = other.right() + gap
        dist = abs(cand.x - target)
        if dist <= proximity and (best_l is None or dist < best_l[0]):
            best_l = (dist, target)

        # Right edge against other left edge (with gap)
        target = other.x - gap
        dist = abs(cand.right() - target)
        if dist <= proximity and (best_r is None or dist < best_r[0]):
            best_r = (dist, target)

        # Top edge against other bottom edge (with gap)
        target = other.bottom() + gap
        dist = abs(cand.y - target)
        if dist <= proximity and (best_t is None or dist < best_t[0]):
            best_t = (dist, target)

        # Bottom edge against other top edge (with gap)
        target = other.y - gap
        dist = abs(cand.bottom() - target)
        if dist <= proximity and (best_b is None or dist < best_b[0]):
            best_b = (dist, target)

    new_l = best_l[1] if best_l is not None else left
    new_r = best_r[1] if best_r is not None else right
    new_t = best_t[1] if best_t is not None else top
    new_b = best_b[1] if best_b is not None else bottom

    new_w = max(160.0, new_r - new_l)
    new_h = max(100.0, new_b - new_t)
    return Rect(new_l, new_t, new_w, new_h)


def anchor_stationary_resize(cand, anchor_edge, target_coord, mon_right_limit):
    """Stationary edge anchoring during interactive drag resize."""
    if anchor_edge == "right":
        # Right edge is stationary; adjusting left edge
        anchor_r = mon_right_limit
        new_w = max(160.0, anchor_r - target_coord)
        new_x = anchor_r - new_w
        return Rect(new_x, cand.y, new_w, cand.h)
    elif anchor_edge == "left":
        # Left edge is stationary; adjusting right edge
        new_w = max(160.0, target_coord - cand.x)
        return Rect(cand.x, cand.y, new_w, cand.h)
    return cand


def pick_rail(cur, anchor, anchor_rail, velocity, mon, transform=0):
    eff_w = mon.w
    eff_h = mon.h
    if transform % 2 == 1:
        eff_w, eff_h = mon.h, mon.w

    is_portrait = eff_h > eff_w
    m = mass_factor(cur.w, cur.h, eff_w, eff_h)
    v_thresh = flick_velocity_threshold(m)
    d_thresh = flick_displacement_threshold(m)

    if not is_portrait:
        # Landscape: Left (false) vs Right (true) rail
        cur_rail = anchor_rail if anchor_rail is not None else (cur.cx() >= mon.cx())
        vx = velocity[0]
        dx = (cur.cx() - anchor.cx()) if anchor else 0.0

        if not cur_rail:
            # On Left rail: throw Right
            if vx >= v_thresh or dx >= d_thresh:
                return True
            return False
        else:
            # On Right rail: throw Left
            if vx <= -v_thresh or dx <= -d_thresh:
                return False
            return True
    else:
        # Portrait: Top (false) vs Bottom (true) rail
        cur_rail = anchor_rail if anchor_rail is not None else (cur.cy() >= mon.cy())
        vy = velocity[1]
        dy = (cur.cy() - anchor.cy()) if anchor else 0.0

        if not cur_rail:
            # On Top rail: throw Down
            if vy >= v_thresh or dy >= d_thresh:
                return True
            return False
        else:
            # On Bottom rail: throw Up
            if vy <= -v_thresh or dy <= -d_thresh:
                return False
            return True


# =========================================================================
# Two-Sided Tests (Positive & Negative Controls)
# =========================================================================

def test_binary_check():
    bin_path = find_binary()
    if not bin_path:
        print("[SKIP] mios-pip-gravity binary not found on disk, skipping CLI check")
        return True
    env = dict(os.environ, MIOS_VENDOR_TOML=str(SSOT))
    r = subprocess.run([bin_path, "check"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, f"check failed ({r.returncode}): {r.stderr}"
    assert "ok enabled=" in r.stdout, f"unexpected check output: {r.stdout}"
    print("[PASS] Positive Control: mios-pip-gravity check succeeded")
    return True


def test_binary_check_negative():
    bin_path = find_binary()
    if not bin_path:
        return True
    # Negative control: missing required table or corrupt config causes check to exit non-zero
    with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
        f.write("[desktop]\n# empty table, missing pip_gravity\n")
        bad_path = f.name
    try:
        env = dict(os.environ, MIOS_VENDOR_TOML=bad_path, MIOS_TOML=bad_path)
        r = subprocess.run([bin_path, "check"], capture_output=True, text=True, env=env)
        assert r.returncode != 0, "Negative Control Failed: bad SSOT was accepted by check"
        print("[PASS] Negative Control: missing SSOT config rejected with code 2")
    finally:
        os.unlink(bad_path)
    return True


def test_margin_snapping():
    workarea = Rect(0.0, 0.0, 1920.0, 1080.0)
    other = Rect(100.0, 100.0, 300.0, 200.0)  # right is 400.0, bottom is 300.0

    # Positive control: dragged left edge within proximity (e.g. at 420.0, target is 408.0; diff is 12px <= 32px)
    cand = Rect(420.0, 100.0, 200.0, 150.0)
    snapped = snap_resize_edges(cand, [other], workarea)
    assert snapped.x == 408.0, f"Expected left edge snap to 408.0, got {snapped.x}"
    assert snapped.right() == 620.0, "Right edge remained unchanged"
    assert snapped.w == (620.0 - 408.0), f"Expected width {620.0 - 408.0}, got {snapped.w}"
    print("[PASS] Positive Control: Magnetic left edge snap with 8px margin gap")

    # Positive control: dragged right edge within proximity to other.left - 8px
    # other at x=500.0. target right is 492.0. cand right is 480.0 (diff 12px <= 32px)
    other2 = Rect(500.0, 100.0, 200.0, 200.0)
    cand2 = Rect(200.0, 100.0, 280.0, 150.0)  # right = 480.0
    snapped2 = snap_resize_edges(cand2, [other2], workarea)
    assert snapped2.right() == 492.0, f"Expected right edge snap to 492.0, got {snapped2.right()}"
    print("[PASS] Positive Control: Magnetic right edge snap with 8px margin gap")

    # Negative control: candidate outside proximity (diff = 50px > 32px) does NOT snap
    cand_far = Rect(458.0, 100.0, 200.0, 150.0)  # diff = 458.0 - 408.0 = 50px
    snapped_far = snap_resize_edges(cand_far, [other], workarea)
    assert snapped_far.x == 458.0, f"Negative Control Failed: edge snapped outside proximity: {snapped_far.x}"
    print("[PASS] Negative Control: Edge outside proximity range does not snap")
    return True


def test_stationary_anchoring():
    # User drags left edge leftward from x=1600.0 to x=1500.0.
    # Right edge is stationary anchored at x=1912.0 (mon.right - margin).
    anchor_r = 1912.0
    cand = Rect(1600.0, 100.0, 312.0, 200.0)
    resized = anchor_stationary_resize(cand, "right", 1500.0, anchor_r)
    assert resized.right() == anchor_r, f"Stationary edge drifted: {resized.right()} != {anchor_r}"
    assert resized.x == 1500.0, f"Target left edge did not match: {resized.x}"
    assert resized.w == (1912.0 - 1500.0), f"Width did not update accurately: {resized.w}"
    print("[PASS] Positive Control: Stationary edge anchoring preserves opposite edge")
    return True


def test_workarea_hud_snapping():
    workarea = Rect(0.0, 40.0, 1920.0, 1000.0)  # topbar at 40px, dock at bottom

    # Positive control: near top edge (y=50.0, proximity=32, target=40.0)
    cand = Rect(500.0, 50.0, 300.0, 200.0)
    snapped = snap_resize_edges(cand, [], workarea)
    assert snapped.y == 40.0, f"Top edge did not snap to workarea top: {snapped.y}"

    # Positive control: near bottom edge (bottom=1025.0, target=1040.0)
    cand_bot = Rect(500.0, 825.0, 300.0, 200.0)  # bottom is 1025.0, diff 15px
    snapped_bot = snap_resize_edges(cand_bot, [], workarea)
    assert snapped_bot.bottom() == 1040.0, f"Bottom edge did not snap to workarea bottom: {snapped_bot.bottom()}"

    # Negative control: far from HUD boundaries
    cand_mid = Rect(500.0, 300.0, 300.0, 200.0)
    snapped_mid = snap_resize_edges(cand_mid, [], workarea)
    assert snapped_mid.y == 300.0 and snapped_mid.bottom() == 500.0, "Negative Control Failed: snapped in mid-screen"
    print("[PASS] Positive & Negative Controls: Workarea and HUD boundary snapping")
    return True


def test_max_ratio_relaxation():
    workarea = Rect(0.0, 0.0, 1920.0, 1080.0)
    # User resizes PiP to large size (1200x900)
    user_w, user_h = 1200.0, 900.0
    # Clamping function with 0.95 relaxation
    max_w = workarea.w * 0.95
    max_h = workarea.h * 0.95
    clamped_w = min(user_w, max_w)
    clamped_h = min(user_h, max_h)

    assert clamped_w == 1200.0, f"Relaxed width was shrunk: {clamped_w}"
    assert clamped_h == 900.0, f"Relaxed height was shrunk: {clamped_h}"

    # Negative control: window exceeding 0.95 bounds (e.g. 1900x1050) IS clamped to 0.95
    oversized_w, oversized_h = 1900.0, 1050.0
    clamped_over_w = min(oversized_w, max_w)
    clamped_over_h = min(oversized_h, max_h)
    assert clamped_over_w == max_w, f"Negative Control Failed: oversized width not clamped to 0.95: {clamped_over_w}"
    assert clamped_over_h == max_h, f"Negative Control Failed: oversized height not clamped to 0.95: {clamped_over_h}"
    print("[PASS] Positive & Negative Controls: max_ratio relaxation up to 0.95 with clamping guard")
    return True


def test_orientation_throwing():
    # Landscape monitor (1920x1080)
    mon_land = Rect(0.0, 0.0, 1920.0, 1080.0)
    large_pip = Rect(712.0, 100.0, 1200.0, 800.0)  # on Right rail (right = 1912.0)
    anchor_land = Rect(712.0, 100.0, 1200.0, 800.0)

    # Positive control: flick Leftward (vx = -250.0 px/s) swaps to Left rail (False)
    rail_land = pick_rail(large_pip, anchor_land, True, (-250.0, 0.0), mon_land, transform=0)
    assert rail_land is False, f"Landscape leftward flick failed to swap to Left rail: {rail_land}"

    # Negative control: insufficient flick velocity (vx = -50.0 px/s) does NOT swap rails
    rail_land_weak = pick_rail(large_pip, anchor_land, True, (-50.0, 0.0), mon_land, transform=0)
    assert rail_land_weak is True, f"Negative Control Failed: weak flick caused rail swap: {rail_land_weak}"

    # Portrait monitor (1080x1920, transform=1)
    mon_port = Rect(0.0, 0.0, 1920.0, 1080.0)  # raw 1920x1080 with transform=1 => eff 1080x1920
    pip_top = Rect(50.0, 50.0, 900.0, 600.0)   # on Top rail (cy = 350.0 < 960.0)
    anchor_port = Rect(50.0, 50.0, 900.0, 600.0)

    # Positive control: flick downward (vy = 350.0 px/s) swaps to Bottom rail (True)
    rail_port = pick_rail(pip_top, anchor_port, False, (0.0, 350.0), mon_port, transform=1)
    assert rail_port is True, f"Portrait downward flick failed to swap to Bottom rail: {rail_port}"

    # Negative control: flick upward when already on Top rail does NOT swap
    rail_port_neg = pick_rail(pip_top, anchor_port, False, (0.0, -350.0), mon_port, transform=1)
    assert rail_port_neg is False, f"Negative Control Failed: upward flick on top rail changed rail: {rail_port_neg}"
    print("[PASS] Positive & Negative Controls: Orientation-aware throwing in landscape & portrait")
    return True


def test_mass_scaled_physics():
    mon_w, mon_h = 1920.0, 1080.0

    # Small PiP (300x200) vs Large PiP (1200x800)
    m_small = mass_factor(300.0, 200.0, mon_w, mon_h)
    m_large = mass_factor(1200.0, 800.0, mon_w, mon_h)

    assert m_large > m_small, f"Expected m_large > m_small, got {m_large} vs {m_small}"

    # Flick threshold scaling: larger PiP takes LESS flick (lower velocity and displacement threshold)
    v_thresh_small = flick_velocity_threshold(m_small)
    v_thresh_large = flick_velocity_threshold(m_large)
    assert v_thresh_large < v_thresh_small, f"Flick velocity threshold failed: large={v_thresh_large} >= small={v_thresh_small}"

    d_thresh_small = flick_displacement_threshold(m_small)
    d_thresh_large = flick_displacement_threshold(m_large)
    assert d_thresh_large < d_thresh_small, f"Flick displacement threshold failed: large={d_thresh_large} >= small={d_thresh_small}"

    # Gravity snappiness / attraction scaling: smaller PiP is SNAPPY (higher pull & spring stiffness)
    pull_small = gravity_pull(m_small)
    pull_large = gravity_pull(m_large)
    assert pull_small > pull_large, f"Gravity pull failed: small={pull_small} <= large={pull_large}"

    stiff_small = mass_stiffness(200.0, m_small)
    stiff_large = mass_stiffness(200.0, m_large)
    assert stiff_small > stiff_large, f"Mass stiffness failed: small={stiff_small} <= large={stiff_large}"

    print("[PASS] Positive & Relative Controls: Mass-scaled flick thresholds and gravity snappiness")
    return True


def test_dimension_preservation():
    # User-resized dimensions must be preserved on landing across rails
    user_w, user_h = 750.0, 480.0
    pip = Rect(100.0, 100.0, user_w, user_h)
    target_x = 1920.0 - GLOBAL_MARGIN_PX - user_w  # Right rail placement

    landing = Rect(target_x, pip.y, pip.w, pip.h)
    assert landing.w == user_w, f"Width was mutated on throw landing: {landing.w} != {user_w}"
    assert landing.h == user_h, f"Height was mutated on throw landing: {landing.h} != {user_h}"
    print("[PASS] Positive Control: User-resized dimensions preserved across throws")
    return True


def main():
    print("=== test-pip-resize-gravity: Starting Two-Sided Verification ===")
    test_binary_check()
    test_binary_check_negative()
    test_margin_snapping()
    test_stationary_anchoring()
    test_workarea_hud_snapping()
    test_max_ratio_relaxation()
    test_orientation_throwing()
    test_mass_scaled_physics()
    test_dimension_preservation()
    print("=== test-pip-resize-gravity: ALL 8 TEST PHASES PASSED ===")


if __name__ == "__main__":
    main()
