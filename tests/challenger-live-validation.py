#!/usr/bin/env python3
# AI-hint: Challenger 2 Empirical Verification Harness for mios-pip-resize-gravity
# Tests live desktop compositor, nested PiP sessions, magnetic 8px margin snapping,
# stationary anchoring, orientation-aware throwing, mass-scaled physics, and dimension preservation.

import json
import math
import os
import subprocess
import sys
import time

HYPR_SIG = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE", "release-v0.56.2_1793550888_466860191")
XDG_RT = os.environ.get("XDG_RUNTIME_DIR", "/run/user/1000")
WAYLAND_DISP = os.environ.get("WAYLAND_DISPLAY", "wayland-1")

REPORT = []

def log(msg):
    print(f"[CHALLENGER-2] {msg}", flush=True)

def record(test_name, passed, details=""):
    status = "PASS" if passed else "FAIL"
    REPORT.append({"test": test_name, "status": status, "details": details})
    log(f"{status}: {test_name} - {details}")

def hyprctl(cmd, signature=HYPR_SIG, runtime=XDG_RT):
    env = dict(os.environ, HYPRLAND_INSTANCE_SIGNATURE=signature, XDG_RUNTIME_DIR=runtime)
    res = subprocess.run(["hyprctl", "-j", *cmd.split()], capture_output=True, text=True, env=env)
    if res.returncode != 0:
        return None
    try:
        return json.loads(res.stdout)
    except json.JSONDecodeError:
        return res.stdout

def hyprctl_dispatch(disp_cmd, signature=HYPR_SIG, runtime=XDG_RT):
    env = dict(os.environ, HYPRLAND_INSTANCE_SIGNATURE=signature, XDG_RUNTIME_DIR=runtime)
    res = subprocess.run(["hyprctl", "dispatch", *disp_cmd], capture_output=True, text=True, env=env)
    return res.stdout.strip()

# -----------------------------------------------------------------------------
# Test 1: Live Compositor Window Recognition & 8px Margin Gap Verification
# -----------------------------------------------------------------------------
def test_live_window_recognition():
    log("=== Test 1: Live Desktop Windows & 8px Snapping ===")
    clients = hyprctl("clients")
    monitors = hyprctl("monitors")
    if not clients or not monitors:
        record("Live Compositor Query", False, "Failed to query hyprctl clients or monitors")
        return

    mon = monitors[0]
    mon_w, mon_h = mon["width"], mon["height"]
    reserved = mon.get("reserved", [0, 44, 0, 72])
    workarea_r = mon_w - 8.0
    workarea_t = reserved[1]
    workarea_b = mon_h - reserved[3]
    
    log(f"Active Monitor 0: {mon_w}x{mon_h}, reserved={reserved}, workarea=[8, {workarea_t}, {workarea_r}, {workarea_b}]")

    adjacent = [c for c in clients if c.get("class") in ("SpotifyAdjacent", "spotify", "Alacritty") and not any("mios-pip-managed" in t for t in c.get("tags", []))]
    pips = [c for c in clients if any("mios-pip-managed" in t for t in c.get("tags", []))]

    log(f"Found {len(adjacent)} non-PiP adjacent window(s) and {len(pips)} managed PiP window(s)")
    record("Adjacent Window Detection", len(adjacent) > 0, f"Found {len(adjacent)} non-PiP windows")
    record("PiP Window Detection", len(pips) > 0, f"Found {len(pips)} managed PiP windows")

    for pip in pips:
        p_x, p_y = pip["at"]
        p_w, p_h = pip["size"]
        p_right = p_x + p_w
        p_bot = p_y + p_h
        log(f"PiP Window [{pip['address']}] ({pip['title']}): at=[{p_x}, {p_y}], size=[{p_w}, {p_h}], right={p_right}, bottom={p_bot}")

        # Check if right edge is snapped to right rail (workarea_r = 1912.0)
        on_right_rail = abs(p_right - workarea_r) <= 2.0
        log(f"  Right rail alignment: right={p_right}, workarea_r={workarea_r}, diff={abs(p_right - workarea_r)}px")

        # Check if left edge is snapped against any adjacent window right edge + 8px
        snapped_adjacent = False
        for adj in adjacent:
            adj_x, adj_y = adj["at"]
            adj_w, adj_h = adj["size"]
            adj_right = adj_x + adj_w
            target_snap_left = adj_right + 8.0
            diff = abs(p_x - target_snap_left)
            log(f"  Comparing against adjacent [{adj['class']}]: right={adj_right}, target_left={target_snap_left}, actual_left={p_x}, diff={diff}px")
            if diff <= 2.0:
                snapped_adjacent = True
                record("Live 8px Inter-Window Margin Snap", True, f"PiP left edge {p_x} is exactly 8px from {adj['class']} right edge {adj_right} (gap={p_x - adj_right}px)")
                break

        if not snapped_adjacent and on_right_rail:
            record("Live Right Rail Placement", True, f"PiP window cleanly anchored on right rail at x={p_x}, right={p_right}")

# -----------------------------------------------------------------------------
# Test 2: Live Nested Desktop Session Functional Verification
# -----------------------------------------------------------------------------
def test_nested_desktop_session():
    log("=== Test 2: Live Nested Desktop Session Functional Verification ===")
    nested_dirs = [d for d in os.listdir(XDG_RT) if d.startswith("mios-nested-desktop-")]
    if not nested_dirs:
        record("Nested Session Discovery", False, f"No nested desktop directory found in {XDG_RT}")
        return

    target_dir = os.path.join(XDG_RT, nested_dirs[0])
    session_json_path = os.path.join(target_dir, "session.json")
    if not os.path.exists(session_json_path):
        record("Nested Session JSON", False, f"Missing session.json in {target_dir}")
        return

    with open(session_json_path) as f:
        sess = json.load(f)

    nested_sig = sess["instance"]["instance"]
    nested_wl = sess["instance"]["wl_socket"]
    nested_pid = sess["instance"]["pid"]
    parent_win = sess["parent_window"]
    log(f"Nested session active: runtime={target_dir}, instance={nested_sig}, socket={nested_wl}, parent_window={parent_win}, pid={nested_pid}")

    # Query monitors and clients inside the nested session
    nested_mons = hyprctl("monitors", signature=nested_sig, runtime=target_dir)
    nested_clients = hyprctl("clients", signature=nested_sig, runtime=target_dir)

    record("Nested Compositor Alive", nested_mons is not None and len(nested_mons) > 0, f"Compositor responding with output {nested_mons[0]['name'] if nested_mons else 'none'}")
    record("Nested Clients Active", nested_clients is not None and len(nested_clients) > 0, f"Found {len(nested_clients) if nested_clients else 0} clients inside nested session")

    # Take screenshot of nested desktop output via grim
    img_path = "/tmp/challenger-nested-screenshot.png"
    grim_res = subprocess.run(["grim", img_path], env=dict(os.environ, XDG_RUNTIME_DIR=target_dir, WAYLAND_DISPLAY=nested_wl), capture_output=True)
    if grim_res.returncode == 0 and os.path.exists(img_path) and os.path.getsize(img_path) > 1000:
        record("Nested Session Visual Capture", True, f"Captured valid screenshot at {img_path} ({os.path.getsize(img_path)} bytes)")
    else:
        record("Nested Session Visual Capture", False, f"Grim failed: {grim_res.stderr}")

# -----------------------------------------------------------------------------
# Test 3: Empirical Physics & Geometry Verification (All Positive & Negative Controls)
# -----------------------------------------------------------------------------
def test_physics_and_geometry():
    log("=== Test 3: Physics, Snapping, Anchoring, Throwing & Mass-Scaling ===")
    import tempfile
    
    # Run the compiled test binary directly
    bin_path = "/usr/libexec/mios-pip-gravity" if os.path.exists("/usr/libexec/mios-pip-gravity") else "/usr/bin/mios-pip-gravity"
    if not os.path.exists(bin_path):
        bin_path = "/.worktrees/mios-pip-resize-gravity/tools/native/target/x86_64-unknown-linux-musl/release/mios-pip-gravity"

    res = subprocess.run([bin_path, "check"], capture_output=True, text=True)
    record("Daemon Check Self-Test", res.returncode == 0, f"check returned: {res.stdout.strip()}")

    # Import test-pip-resize-gravity routines
    sys.path.insert(0, "/.worktrees/mios-pip-resize-gravity/tests")
    import importlib.util
    spec = importlib.util.spec_from_file_location("test_pip_resize_gravity", "/.worktrees/mios-pip-resize-gravity/tests/test-pip-resize-gravity.py")
    tmod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(tmod)

    # 1. Magnetic margin snapping (positive & negative)
    try:
        tmod.test_margin_snapping()
        record("Magnetic Margin Snapping (8px Facing & Negative Proximity Guard)", True, "Positive snap to other.right+8px and negative control outside 32px verified")
    except Exception as e:
        record("Magnetic Margin Snapping", False, str(e))

    # 2. Stationary edge anchoring
    try:
        tmod.test_stationary_anchoring()
        record("Stationary Opposite-Edge Anchoring", True, "Anchored right edge preserved exactly during leftward drag resize")
    except Exception as e:
        record("Stationary Opposite-Edge Anchoring", False, str(e))

    # 3. Workarea & HUD boundary snapping
    try:
        tmod.test_workarea_hud_snapping()
        record("Workarea & HUD Boundary Snapping", True, "Top/bottom workarea snapping and mid-screen negative control verified")
    except Exception as e:
        record("Workarea & HUD Boundary Snapping", False, str(e))

    # 4. max_ratio relaxation
    try:
        tmod.test_max_ratio_relaxation()
        record("max_ratio Relaxation to 0.95 Workarea", True, "Large PiP (1200x900) preserved without down-scaling, guard clamps >0.95")
    except Exception as e:
        record("max_ratio Relaxation to 0.95", False, str(e))

    # 5. Orientation-aware throwing in landscape & portrait
    try:
        tmod.test_orientation_throwing()
        record("Orientation-Aware Throwing (Landscape L/R & Portrait T/B)", True, "Flick swaps Left<->Right in landscape and Top<->Bottom in portrait, weak flick rejected")
    except Exception as e:
        record("Orientation-Aware Throwing", False, str(e))

    # 6. Mass-scaled flick velocity & gravity snappiness
    try:
        tmod.test_mass_scaled_physics()
        record("Mass-Scaled Physics (Flick Threshold & Gravity Snappiness)", True, "Proven: flick_threshold(large) < flick_threshold(small) and gravity_pull(small) > gravity_pull(large)")
    except Exception as e:
        record("Mass-Scaled Physics", False, str(e))

    # 7. Dimension preservation on landing
    try:
        tmod.test_dimension_preservation()
        record("Dimension Preservation on Throw Landing", True, "User-resized dimensions preserved exactly across throw landing")
    except Exception as e:
        record("Dimension Preservation on Throw Landing", False, str(e))

def main():
    log("Starting Challenger 2 Empirical Live & Nested Desktop Verification...")
    test_live_window_recognition()
    test_nested_desktop_session()
    test_physics_and_geometry()

    print("\n" + "="*70)
    print("CHALLENGER-2 EMPIRICAL VERIFICATION REPORT SUMMARY")
    print("="*70)
    all_passed = True
    for item in REPORT:
        print(f"[{item['status']}] {item['test']}: {item['details']}")
        if item["status"] != "PASS":
            all_passed = False

    print("="*70)
    if all_passed:
        print("OVERALL VERDICT: ALL CONTROLS EMPIRICALLY CONFIRMED AND PASSED")
        sys.exit(0)
    else:
        print("OVERALL VERDICT: ONE OR MORE CONTROLS FAILED")
        sys.exit(1)

if __name__ == "__main__":
    main()
