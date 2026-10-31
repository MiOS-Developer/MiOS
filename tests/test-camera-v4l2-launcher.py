#!/usr/bin/env python3
# AI-hint: Offline positive/negative controls for usr/bin/mios-camera-v4l2: the PipeWire V4L2 library is preloaded only when remote cameras, V4L2 compatibility and the library are all present in the layered SSOT; otherwise the program starts unchanged with a reason. No camera, PipeWire or device access.
# AI-related: usr/bin/mios-camera-v4l2, usr/share/mios/mios.toml, usr/share/doc/mios/remote-camera.md
# AI-functions: LauncherTest

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = ROOT / "usr/bin/mios-camera-v4l2"
SSOT = ROOT / "usr/share/mios/mios.toml"


class LauncherTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.library = self.dir / "libpw-v4l2.so"
        self.library.write_bytes(b"")
        (self.dir / "home").mkdir()

    def tearDown(self):
        self.tmp.cleanup()

    def ssot(self, camera="true", v4l2="true", library=None):
        text = SSOT.read_text()
        library = str(self.library) if library is None else library
        replaced = []
        for line in text.splitlines():
            if line.startswith("camera_v4l2 "):
                line = f"camera_v4l2 = {v4l2}"
            elif line.startswith("camera_v4l2_library "):
                line = f'camera_v4l2_library = "{library}"'
            elif line.startswith("camera              = true"):
                line = f"camera              = {camera}"
            replaced.append(line)
        self.count = getattr(self, "count", 0) + 1
        path = self.dir / f"mios-{self.count}.toml"
        path.write_text("\n".join(replaced) + "\n")
        return path

    def run_launcher(self, ssot, *args):
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.dir / "home"), "MIOS_VENDOR_TOML": str(ssot), "LANG": "C.UTF-8"}
        return subprocess.run([str(LAUNCHER), *args], env=env, capture_output=True, text=True, timeout=30)

    def test_ssot_declares_the_launcher_keys(self):
        text = SSOT.read_text()
        self.assertIn('camera_v4l2_library = "/usr/lib64/pipewire-0.3/v4l2/libpw-v4l2.so"', text)
        self.assertRegex(text, r"\ncamera_v4l2 = true ")
        self.assertIn('"pipewire-v4l2"', text)

    def test_preloads_when_all_conditions_hold(self):
        run = self.run_launcher(self.ssot(), "sh", "-c", 'printf %s "$LD_PRELOAD"')
        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertEqual(run.stdout, str(self.library))
        check = self.run_launcher(self.ssot(), "--check")
        self.assertEqual(check.returncode, 0)
        self.assertIn("active", check.stdout)

    def test_existing_preload_is_kept(self):
        env_prog = 'printf %s "$LD_PRELOAD"'
        ssot = self.ssot()
        env = {"PATH": "/usr/bin:/bin", "HOME": str(self.dir / "home"), "MIOS_VENDOR_TOML": str(ssot), "LD_PRELOAD": "/x/other.so"}
        run = subprocess.run([str(LAUNCHER), "sh", "-c", env_prog], env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(run.stdout, f"{self.library} /x/other.so")

    def test_negative_controls_start_the_program_without_the_library(self):
        cases = {
            "camera": (self.ssot(camera="false"), "remote.rdp.redirect.camera"),
            "v4l2": (self.ssot(v4l2="false"), "remote.rdp.native.camera_v4l2"),
            "missing": (self.ssot(library=str(self.dir / "absent.so")), "pipewire-v4l2"),
            "relative": (self.ssot(library="libpw-v4l2.so"), "absolute path"),
        }
        for name, (ssot, reason) in cases.items():
            with self.subTest(name):
                run = self.run_launcher(ssot, "sh", "-c", 'printf %s "${LD_PRELOAD:-none}"')
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(run.stdout, "none")
                self.assertIn(reason, run.stderr)
                check = self.run_launcher(ssot, "--check")
                self.assertEqual(check.returncode, 3)
                self.assertIn(reason, check.stdout)

    def test_program_exit_status_and_usage(self):
        run = self.run_launcher(self.ssot(), "sh", "-c", "exit 7")
        self.assertEqual(run.returncode, 7)
        self.assertEqual(self.run_launcher(self.ssot()).returncode, 2)
        self.assertEqual(self.run_launcher(self.ssot(), "--bogus").returncode, 2)


if __name__ == "__main__":
    unittest.main()
