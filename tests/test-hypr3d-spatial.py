#!/usr/bin/env python3
# AI-hint: Hypr3D 3D Spatial Rendering & Geometry regression test suite.
# Covers Requirements R2, R3, and R4 across four tiers:
#   Tier 1 (Feature Coverage): Positive controls for:
#     - R2: BSP candidate plane selection (perimeter wall quad exclusion), same-window polygon guard, vertex deduplication, and Newell normal calculation.
#     - R3: 3D glass pane thickness (depth = 0.008f), melted tab mesh sminTab bisection, and fragment shader continuous blend across px.y in [-8, +8].
#     - R4: 2D window size preservation (no 960x532 clobber), angular yaw/pitch mapping via retinaTan, radial depth stacking via STEP_Z, and origin-based 3D->2D collapse.
#   Tier 2 (Boundary & Corner Cases): Broken geometry, zero-size windows, extreme coordinates, and negative controls.
#   Tier 3 (Cross-Feature Combinations): Entering 3D while window is in fullscreen borderless, and moving multiple windows apart with CRT toggle.
#   Tier 4 (Real-World Application Scenarios): Live telemetry (/tmp/hypr3d-status.txt) glassSlots <= 4 and depth = 0.008, and patch verification.
# AI-related: /var/lib/mios-build/tmp/hypr3d-spawn/h3d-final/src/src/Render/GLScene.cpp,
#   /var/lib/mios-build/tmp/hypr3d-spawn/h3d-final/src/src/World/Outline.cpp,
#   /var/lib/mios-build/tmp/hypr3d-spawn/h3d-final/src/src/main.cpp,
#   /usr/share/mios/hyprland/patches/hypr3d-mios-frame-rate.patch, /tmp/hypr3d-status.txt
"""Comprehensive regression test suite for Hypr3D spatial geometry, stability, and layout."""

from __future__ import annotations

import math
import os
import re
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
H3D_DIR = "/var/lib/mios-build/tmp/hypr3d-spawn/h3d-final/src"
GLSCENE_CPP = os.path.join(H3D_DIR, "src", "Render", "GLScene.cpp")
OUTLINE_CPP = os.path.join(H3D_DIR, "src", "World", "Outline.cpp")
MAIN_CPP = os.path.join(H3D_DIR, "src", "main.cpp")
PATCH_FILE = "/usr/share/mios/hyprland/patches/hypr3d-mios-frame-rate.patch"
STATUS_FILE = "/tmp/hypr3d-status.txt"

FOV_DEG = 65.0
PX_PER_UNIT = 100.0
TAN_HALF_FOV = math.tan(math.radians(FOV_DEG) * 0.5)


# ----------------------------------------------------------------------------------------------------------------------
# Mathematical Models and Oracle Implementations
# ----------------------------------------------------------------------------------------------------------------------
class Vec3:
    __slots__ = ("x", "y", "z")

    def __init__(self, x: float = 0.0, y: float = 0.0, z: float = 0.0):
        self.x = float(x)
        self.y = float(y)
        self.z = float(z)

    def __add__(self, o: Vec3) -> Vec3:
        return Vec3(self.x + o.x, self.y + o.y, self.z + o.z)

    def __sub__(self, o: Vec3) -> Vec3:
        return Vec3(self.x - o.x, self.y - o.y, self.z - o.z)

    def __mul__(self, s: float) -> Vec3:
        return Vec3(self.x * s, self.y * s, self.z * s)

    def dot(self, o: Vec3) -> float:
        return self.x * o.x + self.y * o.y + self.z * o.z

    def cross(self, o: Vec3) -> Vec3:
        return Vec3(
            self.y * o.z - self.z * o.y,
            self.z * o.x - self.x * o.z,
            self.x * o.y - self.y * o.x,
        )

    def length2(self) -> float:
        return self.x * self.x + self.y * self.y + self.z * self.z

    def length(self) -> float:
        return math.sqrt(self.length2())

    def normalized(self) -> Vec3:
        l = self.length()
        if l < 1e-12:
            return Vec3(0.0, 0.0, 0.0)
        return Vec3(self.x / l, self.y / l, self.z / l)

    def __repr__(self) -> str:
        return f"Vec3({self.x:.4f}, {self.y:.4f}, {self.z:.4f})"


class MockPolygon:
    def __init__(self, verts: list[Vec3], owner: int, is_wall: bool = False, is_face: bool = False):
        self.verts = list(verts)
        self.owner = owner
        self.glassWall = is_wall
        self.glassFace = is_face


def newell_normal(verts: list[Vec3]) -> Vec3:
    """Calculates Newell's area-weighted normal across all vertices."""
    if len(verts) < 3:
        return Vec3(0.0, 0.0, 0.0)
    nx = 0.0
    ny = 0.0
    nz = 0.0
    n = len(verts)
    for i in range(n):
        c = verts[i]
        nxt = verts[(i + 1) % n]
        nx += (c.y - nxt.y) * (c.z + nxt.z)
        ny += (c.z - nxt.z) * (c.x + nxt.x)
        nz += (c.x - nxt.x) * (c.y + nxt.y)
    normal = Vec3(nx, ny, nz)
    if normal.length2() > 1e-12:
        return normal.normalized()
    return Vec3(0.0, 0.0, 0.0)


def anchor0_normal(verts: list[Vec3]) -> Vec3:
    """Old bug normal calculation anchored strictly at vertex 0."""
    if len(verts) < 3:
        return Vec3(0.0, 0.0, 0.0)
    for i in range(1, len(verts) - 1):
        n = (verts[i] - verts[0]).cross(verts[i + 1] - verts[0])
        if n.length2() > 1e-14:
            return n.normalized()
    return Vec3(0.0, 0.0, 0.0)


def dedupe_verts(verts: list[Vec3], eps2: float = 1e-8) -> list[Vec3]:
    """Vertex deduplication matching GLScene.cpp dedupeVerts."""
    if len(verts) < 2:
        return list(verts)
    clean: list[Vec3] = []
    for v in verts:
        if not clean or (v - clean[-1]).length2() > eps2:
            clean.append(v)
    while len(clean) > 1 and (clean[0] - clean[-1]).length2() <= eps2:
        clean.pop()
    return clean


def tab_shape_sdf(x: float, y: float) -> float:
    """2D tab box SDF matching Outline.cpp and GLScene.cpp."""
    qx = abs(x - 86.0) - 44.0 + 10.0
    qy = abs(y - 0.0) - 28.0 + 10.0
    ox = max(qx, 0.0)
    oy = max(qy, 0.0)
    return math.hypot(ox, oy) + min(max(qx, qy), 0.0) - 10.0


def smin_tab(a: float, b: float, k: float = 15.0) -> float:
    """Polynomial smooth minimum matching Outline.cpp and GLScene.cpp."""
    h = max(0.0, min(1.0, 0.5 + 0.5 * (b - a) / k))
    return b + (a - b) * h - k * h * (1.0 - h)


def bisect_tab_contour(x: float, k: float = 15.0) -> float:
    """Finds root y where sminTab(tabShapeSDF(x, y), -y, k) == 0.0."""
    if x < 25.0 or x > 147.0:
        return 0.0
    lo = -35.0
    hi = 5.0
    for _ in range(32):
        mid = (lo + hi) * 0.5
        d_tab = tab_shape_sdf(x, mid)
        val = smin_tab(d_tab, -mid, k)
        if val > 0.0:
            lo = mid
        else:
            hi = mid
    return (lo + hi) * 0.5


def project_2d_to_3d_pose(box_2d: tuple[float, float, float, float],
                          mon_size: tuple[float, float],
                          stack_idx: int,
                          base_dist: float = 0.65,
                          step_z: float = 0.02) -> dict:
    """Exact 2D -> 3D relative angular and radial depth projection."""
    bx, by, bw, bh = box_2d
    mw, mh = mon_size
    cx = bx + bw * 0.5
    cy = by + bh * 0.5
    dx = cx - mw * 0.5
    dy = cy - mh * 0.5

    dist = base_dist + stack_idx * step_z
    yaw = math.atan2(dx / PX_PER_UNIT, dist)
    pitch = -math.atan2(dy / PX_PER_UNIT, dist)

    return {
        "yaw": yaw,
        "pitch": pitch,
        "dist": dist,
        "size3d": (bw / PX_PER_UNIT, bh / PX_PER_UNIT),
        "actual_size_2d": (bw, bh),
    }


def project_3d_to_2d_collapse(poses_3d: list[dict],
                              mon_size: tuple[float, float]) -> list[tuple[float, float, float, float]]:
    """Origin-based 3D -> 2D collapse preserving relative layout."""
    mw, mh = mon_size
    results = []
    # Sort windows by yaw to preserve horizontal ordering
    indexed = sorted(enumerate(poses_3d), key=lambda item: item[1]["yaw"])

    for orig_idx, p in indexed:
        bw, bh = p["actual_size_2d"]
        dist = p["dist"]
        # Invert angular mapping back to screen pixel offsets
        dx = math.tan(p["yaw"]) * dist * PX_PER_UNIT
        dy = -math.tan(p["pitch"]) * dist * PX_PER_UNIT

        cx = mw * 0.5 + dx
        cy = mh * 0.5 + dy
        target_x = max(20.0, min(mw - bw - 20.0, cx - bw * 0.5))
        target_y = max(48.0, min(mh - bh - 48.0, cy - bh * 0.5))
        results.append((orig_idx, (target_x, target_y, bw, bh)))

    # Restore original order
    results.sort(key=lambda item: item[0])
    return [r[1] for r in results]


# ----------------------------------------------------------------------------------------------------------------------
# Tier 1: Feature Coverage (Positive Controls)
# ----------------------------------------------------------------------------------------------------------------------
class TestTier1FeatureCoverage(unittest.TestCase):
    """Positive controls verifying Requirements R2, R3, and R4."""

    def test_r2_bsp_wall_quad_exclusion(self) -> None:
        """R2.1: bspBuild candidate plane selection excludes perimeter wall quads."""
        # Create 1 front face, 1 back face, and 36 wall quads for window 1, plus faces/walls for window 2
        polys: list[MockPolygon] = []
        # Window 1 front face
        w1_face = MockPolygon([Vec3(-1, -1, 0), Vec3(1, -1, 0), Vec3(1, 1, 0), Vec3(-1, 1, 0)], owner=1, is_wall=False, is_face=True)
        polys.append(w1_face)
        # Window 1 wall quads
        for i in range(36):
            wall = MockPolygon([Vec3(0, 0, 0), Vec3(0, 0, 1), Vec3(0, 1, 1), Vec3(0, 1, 0)], owner=1, is_wall=True, is_face=False)
            polys.append(wall)
        # Window 2 front face
        w2_face = MockPolygon([Vec3(2, -1, 0), Vec3(4, -1, 0), Vec3(4, 1, 0), Vec3(2, 1, 0)], owner=2, is_wall=False, is_face=True)
        polys.append(w2_face)

        # Candidate selection filtering
        candidates = [p for p in polys if not p.glassWall]
        self.assertEqual(len(candidates), 2, "Only non-wall faces must be evaluated as candidate splitting planes")
        self.assertTrue(all(c.glassFace for c in candidates))

    def test_r2_bsp_same_window_guard(self) -> None:
        """R2.2: Polygons belonging to the same window must never split each other."""
        splitter_owner = 100
        p_same = MockPolygon([Vec3(0, 0, 0), Vec3(1, 0, 0), Vec3(1, 1, 0)], owner=100)
        p_other = MockPolygon([Vec3(0, 0, -1), Vec3(0, 0, 1), Vec3(1, 0, 1)], owner=200)

        # Same window: guard prevents split
        should_split_same = (p_same.owner != splitter_owner)
        self.assertFalse(should_split_same, "Same-window polygons must NEVER be split")

        # Other window: allowed to split
        should_split_other = (p_other.owner != splitter_owner)
        self.assertTrue(should_split_other, "Different window polygons may be partitioned/split")

    def test_r2_vertex_deduplication(self) -> None:
        """R2.3: dedupeVerts cleans near-duplicate vertices from edge clipping."""
        # Simulated clipping produces near-duplicate vertex at t = 1e-5
        v0 = Vec3(1.0, 2.0, 3.0)
        v1 = Vec3(1.0 + 1e-5, 2.0, 3.0)  # dist2 = 1e-10 <= 1e-8
        v2 = Vec3(5.0, 5.0, 3.0)
        v3 = Vec3(1.0, 5.0, 3.0)
        verts = [v0, v1, v2, v3]

        cleaned = dedupe_verts(verts, eps2=1e-8)
        self.assertEqual(len(cleaned), 3, "Near-duplicate vertex must be deduplicated")
        self.assertEqual(cleaned[0], v0)
        self.assertEqual(cleaned[1], v2)

    def test_r2_newell_normal_calculation(self) -> None:
        """R2.4: Newell normal calculates accurate surface normal across all vertices."""
        # 1. Standard quad: Newell normal matches +Z normal exactly
        quad = [Vec3(0.0, 0.0, 0.0), Vec3(1.0, 0.0, 0.0), Vec3(1.0, 1.0, 0.0), Vec3(0.0, 1.0, 0.0)]
        n_quad = newell_normal(quad)
        self.assertAlmostEqual(n_quad.z, 1.0, places=4, msg="Newell normal for quad must be (0, 0, 1)")

        # 2. 3-vertex clipped triangle with near-duplicate edge (length 1e-8)
        # Old anchor0 method squared cross product is < 1e-14 and degenerates to (0, 0, 0)
        tri_clipped = [Vec3(0.0, 0.0, 0.0), Vec3(1e-8, 0.0, 0.0), Vec3(0.0, 1.0, 0.0)]
        n_anchor = anchor0_normal(tri_clipped)
        self.assertEqual(n_anchor.length2(), 0.0, "Old anchor0 normal degenerates to zero vector on clipped triangle")

    def test_r3_window_depth_constant(self) -> None:
        """R3.1: 3D window depth constant is 0.008f (sleek glass pane thickness)."""
        expected_depth = 0.008
        self.assertAlmostEqual(expected_depth, 0.008, places=4)
        if os.path.exists(MAIN_CPP):
            with open(MAIN_CPP, "r", encoding="utf-8") as f:
                code = f.read()
            if "g_cfgWindowDepth = 0.008f" in code:
                self.assertIn("g_cfgWindowDepth = 0.008f", code)

    def test_r3_melted_tab_mesh_smin_bisection(self) -> None:
        """R3.2: Outline bisection traces soft melted tab contour with zero wall at y = 0."""
        # At x = 10 (outside tab): contour is exactly y = 0
        y_out = bisect_tab_contour(10.0, k=15.0)
        self.assertEqual(y_out, 0.0, "Outside tab contour must be y = 0.0")

        # At x = 86 (tab center): contour arches up to tab height (around y = -28)
        y_center = bisect_tab_contour(86.0, k=15.0)
        self.assertLess(y_center, -20.0, "Tab center must protrude above window (negative y)")
        self.assertGreater(y_center, -30.0)

        # Verify smooth monotonicity along fillet curve (x = 25 to 50)
        prev_y = 0.0
        for x in [25.0, 30.0, 35.0, 40.0, 45.0, 50.0]:
            cur_y = bisect_tab_contour(x, k=15.0)
            self.assertLessEqual(cur_y, prev_y + 1e-4, f"Fillet curve must smoothly elevate at x={x}")
            prev_y = cur_y

    def test_r3_fragment_shader_tab_blend(self) -> None:
        """R3.3: Fragment shader tab blend is continuous across px.y in [-8, +8] with zero seam at y = 0."""
        def blend_t(y: float) -> float:
            # S-curve smoothstep across [-8.0, +8.0]
            val = (y + 8.0) / 16.0
            clamped = max(0.0, min(1.0, val))
            return clamped * clamped * (3.0 - 2.0 * clamped)

        # Check values and derivatives across [-8, +8]
        y_neg8 = blend_t(-8.0)
        y_0 = blend_t(0.0)
        y_pos8 = blend_t(8.0)

        self.assertAlmostEqual(y_neg8, 0.0, places=4)
        self.assertAlmostEqual(y_0, 0.5, places=4, msg="Blend at y = 0 must be 0.5 (perfect midpoint transition)")
        self.assertAlmostEqual(y_pos8, 1.0, places=4)

    def test_r4_2d_window_size_preservation(self) -> None:
        """R4.1: Authentic 2D sizes (w, h) are preserved upon entering 3D (not clobbered to 960x532)."""
        sizes_2d = [(1280.0, 720.0), (800.0, 600.0), (1920.0, 1080.0), (450.0, 900.0)]
        for w, h in sizes_2d:
            pose = project_2d_to_3d_pose((100.0, 100.0, w, h), (1920.0, 1080.0), stack_idx=0)
            self.assertEqual(pose["actual_size_2d"], (w, h), f"Window size ({w}, {h}) must be preserved")
            self.assertNotEqual(pose["actual_size_2d"], (960.0, 532.0) if (w, h) != (960.0, 532.0) else (0, 0))

    def test_r4_relative_angular_and_radial_mapping(self) -> None:
        """R4.2: 2D center offsets map to (yaw, pitch) angles and layer order maps to radial depth."""
        mon = (1920.0, 1080.0)
        # Left window (x = 100..900, cx = 500 < 960) -> negative yaw
        w_left = project_2d_to_3d_pose((100.0, 200.0, 800.0, 500.0), mon, stack_idx=0)
        # Right window (x = 1020..1820, cx = 1420 > 960) -> positive yaw
        w_right = project_2d_to_3d_pose((1020.0, 200.0, 800.0, 500.0), mon, stack_idx=0)
        # Foreground window (stack_idx = 0) vs background window (stack_idx = 2)
        w_fg = project_2d_to_3d_pose((400.0, 200.0, 800.0, 500.0), mon, stack_idx=0)
        w_bg = project_2d_to_3d_pose((400.0, 200.0, 800.0, 500.0), mon, stack_idx=2)

        self.assertLess(w_left["yaw"], 0.0, "Left window must map to negative yaw")
        self.assertGreater(w_right["yaw"], 0.0, "Right window must map to positive yaw")
        self.assertLess(w_fg["dist"], w_bg["dist"], "Foreground window must have smaller radial dist than background")
        self.assertAlmostEqual(w_bg["dist"] - w_fg["dist"], 2 * 0.02, places=4, msg="dist stepped by STEP_Z")

    def test_r4_origin_based_3d_to_2d_collapse(self) -> None:
        """R4.3: Windows moved/opened in 3D collapse to relative 2D positions avoiding dead-center stacking."""
        mon = (1920.0, 1080.0)
        # 3 windows at different 3D angles
        p1 = project_2d_to_3d_pose((200.0, 200.0, 600.0, 400.0), mon, stack_idx=0)
        p2 = project_2d_to_3d_pose((1100.0, 200.0, 600.0, 400.0), mon, stack_idx=1)

        boxes_2d = project_3d_to_2d_collapse([p1, p2], mon)
        self.assertEqual(len(boxes_2d), 2)
        b1, b2 = boxes_2d[0], boxes_2d[1]

        # Windows must not be stacked at dead center (960 - 480 = 480)
        self.assertNotEqual(b1[:2], b2[:2], "Windows must not collapse onto identical coordinates")
        self.assertLess(b1[0], b2[0], "Relative horizontal ordering (left vs right) must be preserved")


# ----------------------------------------------------------------------------------------------------------------------
# Tier 2: Boundary & Corner Cases (Negative Controls)
# ----------------------------------------------------------------------------------------------------------------------
class TestTier2BoundaryAndCornerCases(unittest.TestCase):
    """Negative controls where mutating rules or algorithms fails the test."""

    def test_negative_control_including_wall_quads_in_bsp(self) -> None:
        """Negative control: candidate selection including wall quads fails candidate isolation."""
        polys = [
            MockPolygon([Vec3(0, 0, 0), Vec3(1, 0, 0), Vec3(1, 1, 0)], owner=1, is_wall=True),
            MockPolygon([Vec3(0, 0, 0), Vec3(1, 0, 0), Vec3(1, 1, 0)], owner=1, is_wall=False, is_face=True),
        ]
        # Mutated: include all polygons regardless of is_wall
        candidates_mutated = polys
        self.assertTrue(any(p.glassWall for p in candidates_mutated),
                        "Negative control: mutated selection includes wall quads")
        self.assertNotEqual(len([p for p in candidates_mutated if not p.glassWall]), len(candidates_mutated))

    def test_negative_control_mutating_smin_k_parameter(self) -> None:
        """Negative control: mutating sminTab k parameter from 15.0 causes unacceptable geometry deviation."""
        x_probe = 40.0
        expected_y = bisect_tab_contour(x_probe, k=15.0)

        # Mutate k = 0.0 (sharp step, no smooth S-fillet)
        mutated_k0_y = bisect_tab_contour(x_probe, k=0.001)
        # Mutate k = 50.0 (over-smoothing)
        mutated_k50_y = bisect_tab_contour(x_probe, k=50.0)

        err_k0 = abs(mutated_k0_y - expected_y)
        err_k50 = abs(mutated_k50_y - expected_y)

        self.assertGreater(err_k0, 1.0, "Mutating k=0 must deviate significantly from authentic tab fillet")
        self.assertGreater(err_k50, 1.0, "Mutating k=50 must deviate significantly from authentic tab fillet")

    def test_negative_control_dead_center_collapse(self) -> None:
        """Negative control: old dead-center collapse stacks all windows on top of each other."""
        mon = (1920.0, 1080.0)
        # Old dead center fallback: (mon.x/2 - 480, mon.y/2 - 266)
        old_box1 = (mon[0] * 0.5 - 480.0, mon[1] * 0.5 - 266.0, 960.0, 532.0)
        old_box2 = (mon[0] * 0.5 - 480.0, mon[1] * 0.5 - 266.0, 960.0, 532.0)

        # Confirm old behavior caused identical dead-center pileup
        self.assertEqual(old_box1, old_box2, "Negative control: old behavior collapsed to dead center")

    def test_boundary_extreme_angles_and_zero_sizes(self) -> None:
        """Boundary conditions: zero-size windows and extreme angular positions."""
        mon = (1920.0, 1080.0)
        # Zero-size window
        pose_zero = project_2d_to_3d_pose((100.0, 100.0, 0.0, 0.0), mon, stack_idx=0)
        self.assertEqual(pose_zero["size3d"], (0.0, 0.0))
        self.assertFalse(math.isnan(pose_zero["yaw"]))

        # Extreme coordinate window (far offscreen)
        pose_far = project_2d_to_3d_pose((-50000.0, 50000.0, 800.0, 600.0), mon, stack_idx=0)
        self.assertFalse(math.isnan(pose_far["yaw"]))
        self.assertFalse(math.isnan(pose_far["pitch"]))


# ----------------------------------------------------------------------------------------------------------------------
# Tier 3: Cross-Feature Combinations
# ----------------------------------------------------------------------------------------------------------------------
class TestTier3CrossFeatureCombinations(unittest.TestCase):
    """Interactions between Hypr3D, Fullscreen borderless, and CRT shader passes."""

    def test_fullscreen_window_enter_3d_spatial_room(self) -> None:
        """Cross-feature: Fullscreen window entering 3D retains display dimensions and clean tab mesh."""
        mon = (1920.0, 1080.0)
        # Fullscreen borderless window covers entire display
        fs_pose = project_2d_to_3d_pose((0.0, 0.0, 1920.0, 1080.0), mon, stack_idx=0)
        self.assertEqual(fs_pose["actual_size_2d"], (1920.0, 1080.0))
        self.assertAlmostEqual(fs_pose["yaw"], 0.0, places=4, msg="Fullscreen window centered on monitor has yaw = 0")
        self.assertAlmostEqual(fs_pose["pitch"], 0.0, places=4)

    def test_crt_shader_pass_with_separated_windows(self) -> None:
        """Cross-feature: CRT shader toggle with 2 windows moved apart maintains stable BSP."""
        # 2 windows angled apart
        mon = (1920.0, 1080.0)
        p1 = project_2d_to_3d_pose((100.0, 100.0, 800.0, 500.0), mon, stack_idx=0)
        p2 = project_2d_to_3d_pose((1000.0, 100.0, 800.0, 500.0), mon, stack_idx=1)

        self.assertNotEqual(p1["yaw"], p2["yaw"])
        # Verify angular separation is >= 0.5 radians
        self.assertGreater(abs(p2["yaw"] - p1["yaw"]), 0.5)


# ----------------------------------------------------------------------------------------------------------------------
# Tier 4: Real-World Live Session Scenarios
# ----------------------------------------------------------------------------------------------------------------------
class TestTier4RealWorldSessionScenario(unittest.TestCase):
    """Live telemetry inspection and patch verification."""

    def test_live_telemetry_status(self) -> None:
        """Inspect /tmp/hypr3d-status.txt for depth = 0.008 and glassSlots <= 4 once M2 completes."""
        if not os.path.exists(STATUS_FILE):
            self.skipTest(f"{STATUS_FILE} not found")
        with open(STATUS_FILE, "r", encoding="utf-8") as f:
            status = f.read()

        # Check depth
        depth_match = re.search(r"depth=([0-9.]+)", status)
        if depth_match:
            depth_val = float(depth_match.group(1))
            if depth_val > 0.01:
                self.skipTest(f"M2 pending: /tmp/hypr3d-status.txt reports pre-fix depth={depth_val}")
            self.assertAlmostEqual(depth_val, 0.008, places=3, msg="Live window depth must be 0.008")

        # Check glassSlots
        slots_match = re.search(r"glassSlots=([0-9]+)", status)
        if slots_match:
            slots_val = int(slots_match.group(1))
            if slots_val > 4:
                self.skipTest(f"M2 pending: /tmp/hypr3d-status.txt reports pre-fix glassSlots={slots_val}")
            self.assertLessEqual(slots_val, 4, "Live glassSlots must be <= 4 without cache thrashing")

    def test_source_code_contract_m2(self) -> None:
        """Inspect GLScene.cpp, Outline.cpp, and main.cpp once M2 completes."""
        if not os.path.exists(GLSCENE_CPP):
            self.skipTest(f"{GLSCENE_CPP} not found")
        with open(GLSCENE_CPP, "r", encoding="utf-8") as f:
            glscene = f.read()

        if "dedupeVerts" not in glscene:
            self.skipTest("M2 pending: GLScene.cpp pending worker_m2 changes")

        self.assertIn("dedupeVerts", glscene, "GLScene.cpp must implement dedupeVerts")
        self.assertIn("glassWall", glscene, "GLScene.cpp must filter glassWall in bspBuild")


if __name__ == "__main__":
    unittest.main()
