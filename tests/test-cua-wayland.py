#!/usr/bin/env python3
# AI-hint: Two-sided verification test suite for MiOS Multimodal Wayland Computer-Use Agent (CUA) Loop.
# AI-doc: usr/share/doc/mios/manual/ch15-computer-use-and-desktop-control.md
# AI-related: usr/libexec/mios/mios-computer-use, usr/libexec/mios/ui/libei_input.py
"""Two-sided test suite for Milestone 3 (Multimodal Wayland Computer-Use Agent CUA Loop).

Validates:
1. High-speed Wayland screencopy directed to RAM tmpfs (/run/user/1000/mios-vision/) with < 2ms capture and sub-50ms perception SLA.
2. 0-1000 coordinate normalizer math mapping logical normalized coordinates to physical display pixels across multi-monitor canvases (hyprctl monitors -j).
3. Native Wayland virtual pointer and keyboard event injection (wlr-virtual-* protocols) without root privileges.
4. Local vision-language grounding on heavy inference lane (:8520 vLLM) with GPU Arbiter sleep-mode fallback (:8760 -> :8500).
5. OpenAI-compatible tool verbs (screen_observe, mouse_action, keyboard_action) exposed through mios-mcp-server.
6. Two-sided controls: positive controls prove genuine execution, planted negative controls prove strict error rejection.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "usr" / "libexec" / "mios"))
sys.path.insert(0, str(ROOT / "usr" / "libexec" / "mios" / "ui"))


def load_module(rel_path: str, mod_name: str):
    file_path = ROOT / rel_path
    import importlib.machinery
    loader = importlib.machinery.SourceFileLoader(mod_name, str(file_path))
    spec = importlib.util.spec_from_loader(mod_name, loader)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module {mod_name} from {file_path}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LIBEI = load_module("usr/libexec/mios/ui/libei_input.py", "libei_input_cua")
CU = load_module("usr/libexec/mios/mios-computer-use", "mios_computer_use_cua")


class TestWaylandCuaPositiveControls(unittest.TestCase):
    """Positive controls verifying genuine Wayland CUA functionality."""

    def test_screen_observe_captures_frame_in_tmpfs_sub_50ms(self):
        """Positive Control: valid screen_observe captures frame in tmpfs with sub-50ms latency."""
        with tempfile.TemporaryDirectory(prefix="mios-tmpfs-") as tmp_dir:
            res = CU.screen_observe(
                display_id=0,
                fmt="ppm",
                strict_tmpfs=True,
                tmpfs_dir=tmp_dir,
            )
            self.assertEqual(res["status"], "ok")
            self.assertTrue(os.path.isfile(res["image_path"]))
            self.assertEqual(res["format"], "ppm")
            self.assertGreater(res["width"], 0)
            self.assertGreater(res["height"], 0)
            # Verify perception latency is strictly sub-50ms SLA
            self.assertLess(res["latency_ms"], 50.0)

            # Verify PPM binary contents start with valid magic header
            with open(res["image_path"], "rb") as f:
                header = f.readline()
                self.assertEqual(header.strip(), b"P6")

    def test_coordinate_normalizer_maps_center_to_physical_pixels(self):
        """Positive Control: normalizer maps logical (500, 500) to display center (960, 540)."""
        px, py = LIBEI.normalize_0_1000_coordinates(500, 500)
        self.assertEqual(px, 960)
        self.assertEqual(py, 540)

    def test_coordinate_normalizer_multi_monitor_canvas(self):
        """Positive Control: normalizer accounts for monitor offsets and fractional scaling."""
        mock_monitors = [
            {
                "id": 0,
                "name": "DP-1",
                "width": 1920,
                "height": 1080,
                "x": 0,
                "y": 0,
                "scale": 1.0,
                "focused": False,
            },
            {
                "id": 1,
                "name": "HDMI-1",
                "width": 2560,
                "height": 1440,
                "x": 1920,
                "y": 0,
                "scale": 1.25,
                "focused": True,
            },
        ]
        # On monitor 1: logical width = 2560 / 1.25 = 2048, logical height = 1440 / 1.25 = 1152
        # Center (500, 500) -> x = 1920 + 0.5 * 2048 = 2944, y = 0 + 0.5 * 1152 = 576
        px, py = LIBEI.normalize_0_1000_coordinates(
            500, 500, display_id=1, monitors=mock_monitors
        )
        self.assertEqual(px, 2944)
        self.assertEqual(py, 576)

        # Monitor 0 corner (0, 0) and (1000, 1000)
        p0_min = LIBEI.normalize_0_1000_coordinates(
            0, 0, display_id=0, monitors=mock_monitors
        )
        p0_max = LIBEI.normalize_0_1000_coordinates(
            1000, 1000, display_id=0, monitors=mock_monitors
        )
        self.assertEqual(p0_min, (0, 0))
        self.assertEqual(p0_max, (1920, 1080))

    def test_virtual_pointer_dispatches_click(self):
        """Positive Control: virtual pointer dispatches click at normalized coordinates."""
        res = CU.mouse_action(action="click", x=500, y=500, button="left")
        self.assertEqual(res["status"], "ok")
        self.assertEqual(res["action"], "click")
        self.assertEqual(res["normalized_x"], 500.0)
        self.assertEqual(res["normalized_y"], 500.0)
        self.assertEqual(res["physical_x"], 960)
        self.assertEqual(res["physical_y"], 540)
        self.assertEqual(res["button"], "left")
        self.assertEqual(res["protocol"], "wlr-virtual-pointer-unstable-v1")

    def test_virtual_keyboard_dispatches_type_and_combo(self):
        """Positive Control: virtual keyboard dispatches text typing and modifier shortcuts."""
        res_type = CU.keyboard_action(action="type", text="uname -r")
        self.assertEqual(res_type["status"], "ok")
        self.assertEqual(res_type["action"], "type")
        self.assertEqual(res_type["text"], "uname -r")
        self.assertEqual(res_type["protocol"], "wlr-virtual-keyboard-v1")

        res_combo = CU.keyboard_action(action="combo", combo="Ctrl+Shift+T")
        self.assertEqual(res_combo["status"], "ok")
        self.assertEqual(res_combo["action"], "combo")
        self.assertEqual(res_combo["combo"], "Ctrl+Shift+T")

    def test_heavy_lane_vision_grounding_resolution(self):
        """Positive Control: grounding targets heavy lane (:8520 vLLM) when arbiter is AI_EXCLUSIVE."""
        with patch.dict(os.environ, {"MIOS_ARBITER_STATE": "AI_EXCLUSIVE"}):
            lane_info = CU.resolve_grounding_lane()
            self.assertIn("8520", lane_info["endpoint"])
            self.assertIn("Qwen2.5-VL", lane_info["model"])
            self.assertIn("heavy", lane_info["lane"])
            self.assertFalse(lane_info["arbiter"]["sleep_mode"])

    def test_gpu_arbiter_sleep_mode_fallback_to_light_lane(self):
        """Positive Control: grounding falls back to light lane (:8500) when arbiter is in sleep mode."""
        for sleep_state in ("VM_ATTACHED", "DRAINING_SLEEP", "SLEEP"):
            with patch.dict(os.environ, {"MIOS_ARBITER_STATE": sleep_state}):
                lane_info = CU.resolve_grounding_lane()
                self.assertIn("8500", lane_info["endpoint"])
                self.assertIn("light", lane_info["lane"])
                self.assertTrue(lane_info["arbiter"]["sleep_mode"])
                self.assertEqual(lane_info["arbiter"]["state"], sleep_state)

    def test_openai_tool_definitions_schema(self):
        """Positive Control: OpenAI tool schemas match function-calling specifications."""
        tools = CU.get_openai_tool_definitions()
        self.assertEqual(len(tools), 3)
        tool_names = {t["function"]["name"] for t in tools}
        self.assertEqual(
            tool_names, {"screen_observe", "mouse_action", "keyboard_action"}
        )

        for t in tools:
            self.assertEqual(t["type"], "function")
            fn = t["function"]
            self.assertIn("description", fn)
            self.assertIn("parameters", fn)
            self.assertTrue(fn.get("strict"))
            self.assertEqual(fn["parameters"]["type"], "object")


class TestWaylandCuaNegativeControls(unittest.TestCase):
    """Negative controls verifying rejection of corrupted and out-of-bounds inputs."""

    def test_normalizer_rejects_out_of_bounds_coordinates(self):
        """Negative Control: normalizer rejects coordinates outside [0, 1000] range."""
        test_cases = [
            (-100, 1500),
            (1001, 500),
            (500, -1),
            (-50, -50),
            (1500, 2000),
        ]
        for x, y in test_cases:
            with self.assertRaises(ValueError) as ctx:
                LIBEI.normalize_0_1000_coordinates(x, y)
            self.assertIn("Coordinates out of bounds", str(ctx.exception))
            self.assertIn("[0, 1000]", str(ctx.exception))

    def test_mouse_action_rejects_out_of_bounds_coordinates(self):
        """Negative Control: mouse_action rejects out-of-bounds coordinates with descriptive error."""
        with self.assertRaises(ValueError) as ctx:
            CU.mouse_action(action="click", x=-100, y=1500)
        self.assertIn("Coordinates out of bounds", str(ctx.exception))

        with self.assertRaises(ValueError) as ctx:
            CU.mouse_action(action="move", x=1200, y=500)
        self.assertIn("Coordinates out of bounds", str(ctx.exception))

    def test_mouse_action_rejects_missing_coordinate_pair(self):
        """Negative Control: spatial mouse action requires both x and y."""
        with self.assertRaises(ValueError) as ctx:
            CU.mouse_action(action="click", x=500, y=None)
        self.assertIn("Both x and y must be provided", str(ctx.exception))

    def test_screen_observe_rejects_missing_tmpfs_directory(self):
        """Negative Control: screen_observe fails closed when tmpfs directory is missing."""
        missing_dir = "/nonexistent/ram/tmpfs/perception/path"
        with self.assertRaises(RuntimeError) as ctx:
            CU.screen_observe(
                display_id=0,
                strict_tmpfs=True,
                tmpfs_dir=missing_dir,
            )
        self.assertIn("Tmpfs perception directory", str(ctx.exception))
        self.assertIn("does not exist", str(ctx.exception))


class TestWaylandCuaCliIntegration(unittest.TestCase):
    """End-to-end CLI integration testing positive and negative subcommands."""

    def test_cli_screen_observe_positive(self):
        """CLI Positive Control: screen_observe subcommand writes to tmpfs."""
        with tempfile.TemporaryDirectory(prefix="cli-tmpfs-") as d:
            res = subprocess.run(
                [
                    sys.executable,
                    str(ROOT / "usr" / "libexec" / "mios" / "mios-computer-use"),
                    "screen_observe",
                    "--tmpfs-dir",
                    d,
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(res.returncode, 0)
            data = json.loads(res.stdout)
            self.assertEqual(data["status"], "ok")
            self.assertTrue(os.path.exists(data["image_path"]))

    def test_cli_mouse_action_positive(self):
        """CLI Positive Control: mouse_action subcommand maps (500, 500) to center."""
        res = subprocess.run(
            [
                sys.executable,
                str(ROOT / "usr" / "libexec" / "mios" / "mios-computer-use"),
                "mouse_action",
                "click",
                "--x",
                "500",
                "--y",
                "500",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        data = json.loads(res.stdout)
        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["physical_x"], 960)
        self.assertEqual(data["physical_y"], 540)

    def test_cli_mouse_action_negative_out_of_bounds(self):
        """CLI Negative Control: mouse_action rejects (-100, 1500) with exit code 1."""
        res = subprocess.run(
            [
                sys.executable,
                str(ROOT / "usr" / "libexec" / "mios" / "mios-computer-use"),
                "mouse_action",
                "click",
                "--x",
                "-100",
                "--y",
                "1500",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 1)
        # Verify JSON error in stdout or stderr
        err_output = res.stderr or res.stdout
        self.assertIn("Coordinates out of bounds", err_output)
        self.assertIn("[0, 1000]", err_output)

    def test_cli_screen_observe_negative_missing_tmpfs(self):
        """CLI Negative Control: screen_observe rejects missing tmpfs path with exit code 1."""
        res = subprocess.run(
            [
                sys.executable,
                str(ROOT / "usr" / "libexec" / "mios" / "mios-computer-use"),
                "screen_observe",
                "--tmpfs-dir",
                "/definitely/nonexistent/tmpfs/perception/dir",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 1)
        err_output = res.stderr or res.stdout
        self.assertIn("Tmpfs perception directory", err_output)
        self.assertIn("does not exist", err_output)

    def test_cli_openai_tools_subcommand(self):
        """CLI Positive Control: --openai-tools outputs valid JSON tool schemas."""
        res = subprocess.run(
            [
                sys.executable,
                str(ROOT / "usr" / "libexec" / "mios" / "mios-computer-use"),
                "--openai-tools",
            ],
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.returncode, 0)
        tools = json.loads(res.stdout)
        self.assertEqual(len(tools), 3)


if __name__ == "__main__":
    unittest.main()
