#!/usr/bin/env python3
# AI-hint: Automated unit test suite for GPU Terminal Rendering & Sub-5ms Keystroke Latency (task_671, task_672).
# AI-related: usr/libexec/mios/desktop/gpu_terminal.py, tests/test-gpu-terminal.py, etc/skel/.config/alacritty/alacritty.toml
"""Automated unit test suite for MiOS GPU Terminal Manager."""

import contextlib
import io
import os
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch

_ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
sys.path.insert(0, os.path.join(_ROOT, "usr", "libexec", "mios", "desktop"))

import gpu_terminal  # noqa: E402
from gpu_terminal import MAX_KEYSTROKE_LATENCY_MS, MIN_GLYPH_THROUGHPUT_CPS, GPUTerminalManager  # noqa: E402

_VENDOR = os.path.join(_ROOT, "usr", "share", "mios", "mios.toml")

def _env(user_toml=""):
    """The overlay pinned to this tree's vendor tier plus an optional user tier; no host tier."""
    return patch.dict(os.environ, {
        "MIOS_VENDOR_TOML": _VENDOR, "MIOS_VENDOR_TOML_D": os.path.join(_ROOT, "usr", "lib", "mios", "mios.d"),
        "MIOS_HOST_TOML": "/nonexistent/mios.toml", "MIOS_USER_TOML": user_toml or "/nonexistent/user/mios.toml"})

def _with_padding(padding):
    return {"theme": {"padding": padding, "scrollbar_state": "hidden", "font": {"family": "F", "size": 12}}}

class TestGPUTerminal(unittest.TestCase):
    def setUp(self):
        env = _env()
        env.start()
        self.addCleanup(env.stop)
        self.mgr = GPUTerminalManager(renderer="Vulkan", dry_run=True)

    def test_alacritty_config_generation(self):
        """Test generated Alacritty config specifies opacity and the [theme.font] family and size."""
        conf = self.mgr.generate_alacritty_config()
        with open(_VENDOR, "rb") as fh:
            theme_cfg = tomllib.load(fh)["theme"]
            font = theme_cfg["font"]
            expected_opacity = float(theme_cfg.get("opacity", 50)) / 100.0
        self.assertIn(f"opacity = {expected_opacity:.2f}", conf)
        self.assertEqual(tomllib.loads(conf)["font"], {"normal": {"family": font["family"]}, "size": float(font["size"])})

    def test_padding_from_user_tier(self):
        with tempfile.TemporaryDirectory(prefix="mios-test-gpu-") as tmp:
            user = os.path.join(tmp, "mios.toml")
            with open(user, "w", encoding="utf-8") as fh:
                fh.write('[theme]\npadding = "3"\n[theme.edge]\nwm_gaps_outer_px = 7\n')
            with _env(user):
                conf = GPUTerminalManager(dry_run=True).generate_alacritty_config()
        self.assertIn("padding = { x = 3, y = 3 }\n", conf)
        window = tomllib.loads(conf)["window"]
        self.assertEqual(window["padding"], {"x": 3, "y": 3})
        self.assertIs(window["dynamic_padding"], False)

    def test_symmetric_two_value_padding(self):
        conf = self.mgr.generate_alacritty_config(_with_padding("4, 2"))
        self.assertEqual(tomllib.loads(conf)["window"]["padding"], {"x": 4, "y": 2})

    def test_asymmetric_padding_rejected(self):
        for raw in ("1, 2, 3, 2", "1, 2, 1, 4"):
            with self.subTest(raw=raw), self.assertRaisesRegex(ValueError, r"alacritty: asymmetric \[theme\]\.padding"):
                self.mgr.generate_alacritty_config(_with_padding(raw))
        with self.assertRaisesRegex(ValueError, r"not a non-negative integer"):
            self.mgr.generate_alacritty_config(_with_padding("-1"))

    def test_check_fixture_both_sides(self):
        self.assertEqual(gpu_terminal.main(["--check-fixture", _ROOT]), 0)
        with tempfile.TemporaryDirectory(prefix="mios-test-gpu-") as tmp:
            copy = os.path.join(tmp, gpu_terminal.GOLDEN)
            self.assertEqual(gpu_terminal.main(["--write-fixture", tmp]), 0)
            with open(copy, encoding="utf-8") as fh:
                text = fh.read()
            with open(copy, "w", encoding="utf-8") as fh:
                fh.write(text.replace("dynamic_padding = false", "dynamic_padding = true"))
            with contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(gpu_terminal.main(["--check-fixture", tmp]), 1)
            self.assertIn("alacritty.toml:6: committed 'dynamic_padding = true'", err.getvalue())
            vendor = os.path.join(tmp, "vendor.toml")
            with open(vendor, "w", encoding="utf-8") as fh:
                fh.write('[theme]\npadding = "1, 2, 3, 4"\nscrollbar_state = "hidden"\n')
            with patch.dict(os.environ, {"MIOS_VENDOR_TOML": vendor}), contextlib.redirect_stderr(io.StringIO()) as err:
                self.assertEqual(gpu_terminal.main(["--check-fixture", _ROOT]), 1)
            self.assertIn("alacritty: asymmetric [theme].padding '1, 2, 3, 4'", err.getvalue())

    def test_glyph_throughput_and_sub_5ms_latency(self):
        """Test glyph throughput exceeds 1,000,000 chars/s and keystroke latency < 5ms."""
        prof = self.mgr.benchmark_render_performance()
        self.assertGreaterEqual(prof.glyph_throughput_chars_per_sec, MIN_GLYPH_THROUGHPUT_CPS)
        self.assertLess(prof.keystroke_latency_ms, MAX_KEYSTROKE_LATENCY_MS)
        self.assertTrue(prof.vsync_locked)

if __name__ == "__main__":
    unittest.main()
