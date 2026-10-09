#!/usr/bin/env python3
# AI-hint: Automated unit test suite for PipeWire WebRTC desktop video streamer (task_732, task_733).
# AI-doc: usr/share/doc/mios/manual/ch38-remote-desktop-and-gnome-grd.md
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_STREAM_BIN = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-screen-stream")


class TestWebRTCScreenStream(unittest.TestCase):
    """Validates PipeWire WebRTC screen stream pipeline and encoder configurations."""

    def test_binary_executable(self):
        self.assertTrue(os.path.isfile(_STREAM_BIN), f"Missing {_STREAM_BIN}")
        self.assertTrue(os.access(_STREAM_BIN, os.X_OK), f"Not executable {_STREAM_BIN}")

    def test_default_check_output(self):
        res = subprocess.run(
            [sys.executable, _STREAM_BIN, "--check", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        self.assertEqual(data.get("status"), "ready")
        self.assertEqual(data.get("target_fps"), 60)
        self.assertLess(data.get("target_latency_ms"), 30)

        pipeline = data.get("pipeline", {})
        self.assertTrue(pipeline.get("zero_copy_dmabuf"))
        self.assertIn("video/x-raw(memory:DMABuf)", pipeline.get("pipeline_str", ""))
        self.assertIn("pipewiresrc", pipeline.get("pipeline_str", ""))
        self.assertIn("webrtcbin", pipeline.get("pipeline_str", ""))

    def test_nvenc_and_vaapi_pipeline_generation(self):
        # Force NVENC H264
        res_nvenc = subprocess.run(
            [sys.executable, _STREAM_BIN, "--encoder", "nvenc", "--codec", "h264", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data_nvenc = json.loads(res_nvenc.stdout)
        pipe_str = data_nvenc["pipeline"]["pipeline_str"]
        self.assertIn("nvh264enc", pipe_str)
        self.assertIn("preset=low-latency-hq", pipe_str)

        # Force VAAPI AV1
        res_vaapi = subprocess.run(
            [sys.executable, _STREAM_BIN, "--encoder", "vaapi", "--codec", "av1", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data_vaapi = json.loads(res_vaapi.stdout)
        pipe_str_vaapi = data_vaapi["pipeline"]["pipeline_str"]
        self.assertIn("vaapiav1enc", pipe_str_vaapi)


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestWebRTCScreenStream)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
