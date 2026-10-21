#!/usr/bin/env python3
# AI-hint: Adversarial fixture tests for dock restore identity, expiry, and invalid inputs.
# AI-related: usr/share/mios/quickshell/DockLamp.qml, tests/test-mios-lamp.py
"""Adversarial challenge test harness for DockLamp.qml restoringWindows logic."""

import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

QS = shutil.which("quickshell")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
QS_DIR = os.path.join(ROOT, "usr/share/mios/quickshell")

SHELL_QML = """import QtQuick
import Quickshell

ShellRoot {
    id: root
    Component.onCompleted: {
        const deskTiles = [
            { kind: "drawer", scale: 1 },
            { kind: "icon", app_id: "test-app", desktop_id: "test-app.desktop", classes: ["test-app"], scale: 1 }
        ]
        DockLamp.setOutput("HDMI-A-1", DockLamp.computeOutputGeometry({
            monX: 0, monY: 0, screenLogicalW: 1920, screenLogicalH: 1080, ui: 1,
            edgeMargin: 8, dockHeight: 56, dockRevealPx: 2, visible: true, edge: "bottom",
            tiles: deskTiles
        }))
        DockLamp.flushDockWrite()
    }
}
"""

@unittest.skipUnless(QS, "quickshell missing")
class DockLampRestoringAdversarialTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-challenger-docklamp-")
        self.qs_path = os.path.join(self.tmp, "qs")
        os.makedirs(self.qs_path, exist_ok=True)
        shutil.copy(os.path.join(QS_DIR, "DockLamp.qml"), self.qs_path)
        self.shell_qml = os.path.join(self.qs_path, "shell.qml")
        with open(self.shell_qml, "w", encoding="utf-8") as f:
            f.write(SHELL_QML)
        self.dock_json = os.path.join(self.tmp, "mios-dock-icons.json")
        self.launch_json = os.path.join(self.tmp, "mios-lamp-launch.json")
        self.fixture_json = os.path.join(self.tmp, "fixture.json")
        self.log_path = os.path.join(self.tmp, "qs.log")
        self.procs = []
        self.logs = []
        runtime = os.path.join(self.tmp, "runtime")
        os.makedirs(runtime, mode=0o700)
        self.env = dict(os.environ, XDG_RUNTIME_DIR=runtime)
        for key in ("HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY", "DISPLAY"):
            self.env.pop(key, None)

    def tearDown(self):
        for f in self.logs:
            f.close()
        for p in self.procs:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start_harness(self, fixture_dict: dict) -> None:
        with open(self.fixture_json, "w", encoding="utf-8") as f:
            json.dump(fixture_dict, f)
        self.env = dict(
            self.env,
            QT_QPA_PLATFORM="offscreen",
            MIOS_DOCK_ICONS_JSON=self.dock_json,
            MIOS_LAMP_LAUNCH_JSON=self.launch_json,
            MIOS_DOCK_FIXTURE_JSON=self.fixture_json,
        )
        out = open(self.log_path, "w", encoding="utf-8")
        self.logs.append(out)
        p = subprocess.Popen(
            [QS, "--path", self.shell_qml],
            env=self.env,
            stdout=out,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.procs.append(p)

    def call_ipc(self, *args: str) -> str:
        if self.procs and self.procs[-1].poll() is not None:
            with open(self.log_path, encoding="utf-8") as log:
                self.fail("Dock fixture exited before IPC:\n" + log.read())
        r = subprocess.run(
            [QS, "ipc", "--path", self.shell_qml, "call", "docklamp", *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return r.stdout.strip()

    def get_state(self) -> dict:
        out = self.call_ipc("state")
        if out.startswith("{"):
            return json.loads(out)
        return {}

    def wait_state(self, cond, timeout: float = 8.0) -> dict:
        end = time.monotonic() + timeout
        last = {}
        while time.monotonic() < end:
            s = self.get_state()
            if s and cond(s):
                return s
            last = s
            time.sleep(0.1)
        self.fail(f"Condition not met within {timeout}s; last={last}")

    def update_fixture(self, fixture_dict: dict) -> None:
        with open(self.fixture_json, "w", encoding="utf-8") as f:
            json.dump(fixture_dict, f)
        # Touch or invoke refresh
        self.call_ipc("flush")

    def test_ttl_expiration_after_workspace_leave(self):
        """Window leaves special:minimized during restore; restoringWindows must retain it for 800ms then prune."""
        # Initial: Window W1 minimized on special:minimized
        initial_fixture = {
            "clients": [
                {"address": "0xdeadbeef01", "workspace": {"name": "special:minimized"}, "class": "test.win1", "title": "Window 1"}
            ]
        }
        self.start_harness(initial_fixture)
        st = self.wait_state(lambda s: len(s.get("minimized_windows", [])) == 1)
        self.assertEqual(st["minimized_windows"][0]["address"], "0xdeadbeef01")

        # Initiate restore
        res_cmd = self.call_ipc("restore", "0xdeadbeef01", "fixture")
        self.assertIn("0xdeadbeef01", res_cmd)

        # Simulate Hyprland moving window away from special:minimized (fixture now empty)
        empty_fixture = {"clients": []}
        self.update_fixture(empty_fixture)

        # Immediate check (150ms after restore): W1 must STILL be in minimized_windows due to restoringWindows map!
        time.sleep(0.15)
        st_mid = self.get_state()
        addrs_mid = [m["address"] for m in st_mid.get("minimized_windows", [])]
        self.assertIn("0xdeadbeef01", addrs_mid, "Window must be retained during the 800ms restore window")

        # Wait until TTL expiration (>= 900ms total elapsed)
        time.sleep(0.85)
        self.call_ipc("flush") # Trigger any pending ticks
        st_post = self.wait_state(
            lambda s: "0xdeadbeef01" not in [m["address"] for m in s.get("minimized_windows", [])],
            timeout=4.0
        )
        addrs_post = [m["address"] for m in st_post.get("minimized_windows", [])]
        self.assertNotIn("0xdeadbeef01", addrs_post, "Window must be pruned after 800ms TTL")

    def test_duplicate_and_casing_restore_idempotence(self):
        """Duplicate restore calls and mixed casing must not duplicate entries in minimized_windows."""
        fixture = {
            "clients": [
                {"address": "0x55aa112233", "workspace": {"name": "special:minimized"}, "class": "test.dup", "title": "Dup Test"}
            ]
        }
        self.start_harness(fixture)
        self.wait_state(lambda s: len(s.get("minimized_windows", [])) == 1)

        # Call restore multiple times rapidly
        self.call_ipc("restore", "0x55aa112233", "fixture")
        self.call_ipc("restore", "0x55aa112233", "fixture")
        self.call_ipc("restore", "0x55AA112233", "fixture") # Uppercase
        self.call_ipc("restore", "55aa112233", "fixture")   # Missing 0x prefix

        # Clear fixture
        self.update_fixture({"clients": []})

        st = self.get_state()
        mins = st.get("minimized_windows", [])
        addresses = [m["address"] for m in mins]
        self.assertEqual(addresses.count("0x55aa112233"), 1, f"Address must appear exactly once, got {addresses}")

    def test_staggered_multiple_restores(self):
        """Multiple windows restored at different times must expire independently according to their own TTL."""
        fixture = {
            "clients": [
                {"address": "0x1111", "workspace": {"name": "special:minimized"}, "class": "test.w1", "title": "W1"},
                {"address": "0x2222", "workspace": {"name": "special:minimized"}, "class": "test.w2", "title": "W2"}
            ]
        }
        self.start_harness(fixture)
        self.wait_state(lambda s: len(s.get("minimized_windows", [])) == 2)

        # Restore W1 at t = 0
        self.call_ipc("restore", "0x1111", "fixture")
        # Clear W1 from fixture immediately
        self.update_fixture({
            "clients": [
                {"address": "0x2222", "workspace": {"name": "special:minimized"}, "class": "test.w2", "title": "W2"}
            ]
        })

        # Wait 450ms, then restore W2
        time.sleep(0.45)
        self.call_ipc("restore", "0x2222", "fixture")
        # Clear W2 from fixture
        self.update_fixture({"clients": []})

        # At t = 500ms: both W1 and W2 should still be present in minimized_windows
        st_500 = self.get_state()
        addrs_500 = set(m["address"] for m in st_500.get("minimized_windows", []))
        self.assertTrue({"0x1111", "0x2222"}.issubset(addrs_500), f"Both windows must be present at t=500ms, got {addrs_500}")

        # At t = 950ms: W1 elapsed ~950ms (>800ms) -> expired. W2 elapsed ~500ms (<800ms) -> still active.
        time.sleep(0.5)
        st_950 = self.wait_state(
            lambda s: "0x1111" not in [m["address"] for m in s.get("minimized_windows", [])],
            timeout=3.0
        )
        addrs_950 = [m["address"] for m in st_950.get("minimized_windows", [])]
        self.assertNotIn("0x1111", addrs_950, "W1 must have expired")
        self.assertIn("0x2222", addrs_950, "W2 must still be active")

        # At t = 1500ms: W2 elapsed >800ms -> expired as well.
        time.sleep(0.6)
        st_final = self.wait_state(
            lambda s: len(s.get("minimized_windows", [])) == 0,
            timeout=3.0
        )
        self.assertEqual(len(st_final.get("minimized_windows", [])), 0, "All windows must be pruned")

    def test_invalid_address_inputs(self):
        """Invalid and hostile address formats must be rejected by normAddress and not pollute restoringWindows."""
        self.start_harness({"clients": []})
        self.wait_state(lambda s: s.get("write_count", 0) >= 1)

        bad_addrs = [
            "",
            "0x",
            "not_hex",
            "0xGHIJK",
            "0x12; rm -rf /",
            "   ",
            "0x 1234",
            "null",
            "undefined"
        ]
        for bad in bad_addrs:
            self.call_ipc("restore", bad, "fixture")

        st = self.get_state()
        self.assertEqual(len(st.get("minimized_windows", [])), 0, f"No invalid address should be recorded in minimized_windows, got {st.get('minimized_windows')}")

    def test_negative_control_unminimized_client_never_tracked(self):
        """A window on normal workspace that was never restored must never appear in minimized_windows."""
        fixture = {
            "clients": [
                {"address": "0x9999", "workspace": {"name": "1"}, "class": "test.normal", "title": "Normal Win"}
            ]
        }
        self.start_harness(fixture)
        st = self.wait_state(lambda s: s.get("write_count", 0) >= 1)
        self.assertEqual(st.get("minimized_windows", []), [], "Normal workspace window must not be in minimized_windows")


if __name__ == "__main__":
    unittest.main()
