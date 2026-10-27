#!/usr/bin/env python3
# AI-hint: Pure-math regression for Hypr3D buildEnterMorph (fresh-window branch): the room placement of
#   tiled 2D windows must project back to the same on-screen rectangles. Models the pinhole mapping in
#   src/main.cpp (dir = FWD + RIGHT*xRatio + UP*yRatio, dist = baseDist + layer*STEP_Z, 1:1 at baseDist).
# AI-related: usr/share/mios/hyprland/patches/hypr3d-*.patch, tests/test-hyprbars-overlay.py
import unittest

MON_W, MON_H = 2560.0, 1440.0
BASE_DIST, STEP_Z = 0.65, 0.02
SPAWN_W, SPAWN_H = 960.0, 692.0  # retina spawn box ([desktop.hyprland.hypr3d.spawn] landscape_cells)


def tiles():
    # three tiled windows that tile the monitor edge to edge: x, y, w, h
    return [(0, 0, 1280, 1440), (1280, 0, 1280, 720), (1280, 720, 1280, 720)]


def group_scale(boxes):
    mw = max(b[2] for b in boxes)
    mh = max(b[3] for b in boxes)
    return max(0.2, min(1.0, SPAWN_W / mw, SPAWN_H / mh))


def project(box, layer, gs, compensate):
    """On-screen rect (px, monitor-centred) of a window placed by the enter morph at stacking layer
    `layer`; the group is scaled by gs and moved back to BASE_DIST/gs (retina enter projection)."""
    x, y, w, h = box
    cx, cy = (x + w / 2 - MON_W / 2) * gs, (y + h / 2 - MON_H / 2) * gs  # angular offsets compressed by gs
    dist = BASE_DIST / gs + layer * STEP_Z
    m = gs * dist / BASE_DIST if compensate else 1.0
    k = BASE_DIST / dist  # apparent px per world unit falls as 1/dist; 1:1 at BASE_DIST
    ow, oh = w * m * k, h * m * k
    return (cx - ow / 2, cy - oh / 2, ow, oh)


def max_error(compensate):
    """Max deviation of the projection from the 2D layout uniformly scaled by gs about the centre."""
    err = 0.0
    gs = group_scale(tiles())
    for layer, box in enumerate(tiles()):
        x, y, w, h = box
        want = ((x - MON_W / 2) * gs, (y - MON_H / 2) * gs, w * gs, h * gs)
        # new behaviour: tiles never overlap, so the group is coplanar (stack depth 0); the old
        # per-window radial stagger (layer = tile index) is the negative control
        got = project(box, 0 if compensate else layer, gs, compensate)
        err = max(err, *(abs(a - b) for a, b in zip(want, got)))
    return err


class EnterMorphScale(unittest.TestCase):
    def test_uncompensated_breaks_tiling(self):  # negative control: old behaviour
        self.assertGreater(max_error(False), 5.0)

    def test_compensated_preserves_tiling(self):  # fixed behaviour
        self.assertLess(max_error(True), 0.01)

    def test_no_window_exceeds_paper_box(self):  # retina: nothing larger than the spawn box on screen
        gs = group_scale(tiles())
        self.assertLess(gs, 1.0)
        for layer, box in enumerate(tiles()):
            _, _, w, h = project(box, 0, gs, True)
            self.assertLessEqual(w, SPAWN_W + 1e-6)
            self.assertLessEqual(h, SPAWN_H + 1e-6)


if __name__ == "__main__":
    unittest.main()
