#!/usr/bin/env python3
# AI-hint: Offline unit-proof of the pure window-snap region geometry
# AI-related: /usr/libexec/mios/mios_window_region.py, /usr/libexec/mios/mios-window
"""Tests for mios_window_region: pure region-rectangle geometry."""
import importlib.util
import io
import os
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_MOD_PATH = os.path.join(_HERE, "mios_window_region.py")

_spec = importlib.util.spec_from_file_location("mios_window_region", _MOD_PATH)
mwr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mwr)

class RegionRectTests(unittest.TestCase):
    def test_maximize_fills_work_area(self):
        self.assertEqual(mwr.region_rect(1920, 1080, "maximize"), (0, 0, 1920, 1080))

    def test_left_half(self):
        self.assertEqual(mwr.region_rect(1920, 1080, "left-half"), (0, 0, 960, 1080))

    def test_right_half(self):
        self.assertEqual(mwr.region_rect(1920, 1080, "right-half"), (960, 0, 960, 1080))

    def test_top_half(self):
        self.assertEqual(mwr.region_rect(1920, 1080, "top-half"), (0, 0, 1920, 540))

    def test_bottom_half(self):
        self.assertEqual(mwr.region_rect(1920, 1080, "bottom-half"), (0, 540, 1920, 540))

    def test_left_and_right_tile_exactly_even(self):
        lx, ly, lw, lh = mwr.region_rect(1920, 1080, "left-half")
        rx, ry, rw, rh = mwr.region_rect(1920, 1080, "right-half")
        self.assertEqual(lx + lw, rx)            # no gap
        self.assertEqual(lw + rw, 1920)          # full coverage

    def test_left_and_right_tile_exactly_odd(self):
        lx, ly, lw, lh = mwr.region_rect(1921, 1080, "left-half")
        rx, ry, rw, rh = mwr.region_rect(1921, 1080, "right-half")
        self.assertEqual(lw, 960)
        self.assertEqual(rw, 961)
        self.assertEqual(lx + lw, rx)
        self.assertEqual(lw + rw, 1921)

    def test_top_and_bottom_tile_exactly_odd(self):
        _, ty, _, th = mwr.region_rect(1920, 1081, "top-half")
        _, by, _, bh = mwr.region_rect(1920, 1081, "bottom-half")
        self.assertEqual(th, 540)
        self.assertEqual(bh, 541)
        self.assertEqual(ty + th, by)
        self.assertEqual(th + bh, 1081)

    def test_quarters_cover_work_area(self):
        quarters = [
            mwr.region_rect(1920, 1080, q)
            for q in ("top-left", "top-right", "bottom-left", "bottom-right")
        ]
        area = sum(w * h for (_, _, w, h) in quarters)
        self.assertEqual(area, 1920 * 1080)

    def test_bare_compass_aliases(self):
        self.assertEqual(
            mwr.region_rect(1000, 800, "left"), mwr.region_rect(1000, 800, "left-half")
        )
        self.assertEqual(
            mwr.region_rect(1000, 800, "max"), mwr.region_rect(1000, 800, "maximize")
        )

    def test_normalize_underscores_and_case(self):
        self.assertEqual(
            mwr.region_rect(1000, 800, "LEFT_HALF"), mwr.region_rect(1000, 800, "left-half")
        )

    def test_unknown_region_is_none(self):
        self.assertIsNone(mwr.region_rect(1920, 1080, "diagonal"))
        self.assertIsNone(mwr.region_rect(1920, 1080, ""))

    def test_no_pixel_constants_scale_with_geometry(self):
        small = mwr.region_rect(800, 600, "right-half")
        big = mwr.region_rect(1600, 1200, "right-half")
        self.assertEqual(tuple(v * 2 for v in small), big)

class RectFromLayoutTests(unittest.TestCase):
    def _layout(self, x, y, w, h, extra=None):
        screens = [{"work": {"x": x, "y": y, "width": w, "height": h}}]
        if extra is not None:
            screens.append({"work": extra})
        return {"ok": True, "count": len(screens), "screens": screens}

    def test_origin_offset_added(self):
        layout = self._layout(1920, 0, 1920, 1080)
        self.assertEqual(mwr.rect_from_layout(layout, "left-half"), (1920, 0, 960, 1080))

    def test_taskbar_offset_work_area(self):
        layout = self._layout(0, 40, 1920, 1040)
        self.assertEqual(mwr.rect_from_layout(layout, "bottom-half"), (0, 40 + 520, 1920, 520))

    def test_monitor_index_selects_screen(self):
        layout = self._layout(0, 0, 1920, 1080, extra={"x": 1920, "y": 0, "width": 1280, "height": 720})
        self.assertEqual(mwr.rect_from_layout(layout, "maximize", monitor=1), (1920, 0, 1280, 720))

    def test_out_of_range_monitor_falls_back_to_primary(self):
        layout = self._layout(0, 0, 1920, 1080)
        self.assertEqual(mwr.rect_from_layout(layout, "maximize", monitor=9), (0, 0, 1920, 1080))

    def test_malformed_layout_is_none(self):
        self.assertIsNone(mwr.rect_from_layout({"screens": []}, "left-half"))
        self.assertIsNone(mwr.rect_from_layout({}, "left-half"))
        self.assertIsNone(mwr.rect_from_layout({"screens": [{"work": {}}]}, "left-half"))

    def test_unknown_region_from_layout_is_none(self):
        layout = self._layout(0, 0, 1920, 1080)
        self.assertIsNone(mwr.rect_from_layout(layout, "nope"))

class CliTests(unittest.TestCase):
    def _run(self, args, stdin_text):
        old_in, old_out = sys.stdin, sys.stdout
        sys.stdin = io.StringIO(stdin_text)
        sys.stdout = io.StringIO()
        try:
            code = mwr.main(args)
            out = sys.stdout.getvalue()
        finally:
            sys.stdin, sys.stdout = old_in, old_out
        return code, out

    def test_cli_prints_absolute_rect(self):
        layout = '{"screens":[{"work":{"x":0,"y":0,"width":1920,"height":1080}}]}'
        code, out = self._run(["right-half"], layout)
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), "960 0 960 1080")

    def test_cli_unknown_region_exit_2(self):
        layout = '{"screens":[{"work":{"x":0,"y":0,"width":1920,"height":1080}}]}'
        code, _ = self._run(["sideways"], layout)
        self.assertEqual(code, 2)

    def test_cli_bad_json_exit_3(self):
        code, _ = self._run(["left-half"], "not json")
        self.assertEqual(code, 3)

if __name__ == "__main__":
    unittest.main(verbosity=2)
