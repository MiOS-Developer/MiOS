#!/usr/bin/env python3
# AI-hint: Automated unit test suite for PipeWire AEC filter and loopback manager (task_730, task_731).
# AI-doc: usr/share/doc/mios/manual/audio.md
from __future__ import annotations

import json
import os
import subprocess
import sys
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_AEC_CONF = os.path.join(_ROOT, "etc", "pipewire", "pipewire.conf.d", "20-aec-loopback.conf")
_AEC_BIN = os.path.join(_ROOT, "usr", "libexec", "mios", "mios-audio-aec")


class TestPipeWireAEC(unittest.TestCase):
    """Validates PipeWire WebRTC AEC filter and loopback sink declarations."""

    def test_configuration_file_exists(self):
        self.assertTrue(os.path.isfile(_AEC_CONF), f"Missing {_AEC_CONF}")
        with open(_AEC_CONF, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("libpipewire-module-echo-cancel", content)
        self.assertIn("aec/libspa-aec-webrtc", content)
        self.assertIn("echo-cancel-source", content)
        self.assertIn("echo-cancel-sink", content)
        self.assertIn("virtual-sink", content)
        self.assertIn("webrtc.noise_suppression = true", content)

    def test_aec_manager_cli(self):
        self.assertTrue(os.path.isfile(_AEC_BIN), f"Missing {_AEC_BIN}")
        self.assertTrue(os.access(_AEC_BIN, os.X_OK), f"Not executable {_AEC_BIN}")

        res = subprocess.run(
            [sys.executable, _AEC_BIN, "--check", "--conf", _AEC_CONF, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(res.stdout)
        cfg = data.get("configuration", {})
        self.assertEqual(cfg.get("status"), "valid")
        self.assertTrue(cfg.get("has_echo_cancel"))
        self.assertTrue(cfg.get("has_virtual_sink"))
        self.assertTrue(cfg.get("has_webrtc_aec"))
        self.assertEqual(cfg.get("source_name"), "echo-cancel-source")
        self.assertEqual(cfg.get("sink_name"), "virtual-sink")


def main() -> int:
    suite = unittest.TestLoader().loadTestsFromTestCase(TestPipeWireAEC)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
