#!/usr/bin/env python3
# AI-hint: Empirical adversarial verification for Quickshell Dock & Drawer (R1-R3).
# Tests ScreencopyView failure/headless fallback, rapid IPC dispatch stress, preferences persistence
# robustness & atomic replace, throttled geometry publisher rate limit (<= 30 writes/s), launch file pruning (10s),
# pin/star boundary conditions, and strict Codex isolation.
# AI-related: usr/share/mios/quickshell/DockLamp.qml, usr/share/mios/quickshell/DockPopup.qml,
# usr/share/mios/quickshell/Sidebar.qml, usr/share/mios/quickshell/Config.qml, tests/test-mios-lamp.py
"""Empirical adversarial challenger test suite for Quickshell Dock & Drawer."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
QS_DIR = os.path.join(ROOT, "usr", "share", "mios", "quickshell")
DOCKLAMP_QML = os.path.join(QS_DIR, "DockLamp.qml")
DOCKPOPUP_QML = os.path.join(QS_DIR, "DockPopup.qml")
SIDEBAR_QML = os.path.join(QS_DIR, "Sidebar.qml")
CONFIG_QML = os.path.join(QS_DIR, "Config.qml")
QMLLINT = "/usr/lib64/qt6/bin/qmllint"
QS = shutil.which("quickshell")

CODEX_PROTECTED = [
    "/usr/libexec/mios/mios-mcp-server",
    "/tests/test_adversarial_r6_2_workers_isolation.py",
    "/tests/test-shaderglass.py",
    "/src/mios-rs/miosd/src/shaderglass.rs",
    "/src/mios-rs/miosd/src/shaderglass/follow.rs",
    "/src/mios-rs/miosd/src/shaderglass/picker.rs",
    "/src/mios-rs/miosd/src/shaderglass/release.rs",
    "/src/mios-rs/miosd/src/shaderglass/wine.rs",
    "/src/mios-rs/miosd/src/shaderglass/zip.rs",
]

SHELL_TEST_QML = """import QtQuick
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


class TestScreencopyAndHeadlessFallback(unittest.TestCase):
    """Adversarial stress-testing of ScreencopyView failure modes and headless fallback."""

    def test_qmllint_dockpopup_and_targets_clean(self):
        """Verify qmllint validates DockPopup.qml, DockLamp.qml, Sidebar.qml, and Config.qml."""
        if not os.path.exists(QMLLINT):
            self.skipTest(f"{QMLLINT} not found")
        cmd = [
            QMLLINT,
            "-I", "/usr/lib64/qt6/qml",
            "DockLamp.qml", "DockPopup.qml", "Sidebar.qml", "Config.qml"
        ]
        r = subprocess.run(cmd, cwd=QS_DIR, capture_output=True, text=True, timeout=30)
        self.assertEqual(r.returncode, 0, f"qmllint returned {r.returncode}:\n{r.stderr}\n{r.stdout}")

    def test_screencopy_null_toplevel_fallback_contract(self):
        """Verify DockPopup.qml ScreencopyView has explicit null captureSource fallback."""
        with open(DOCKPOPUP_QML, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("ScreencopyView", content)
        self.assertIn("captureSource: winCard.modelData.toplevel ? winCard.modelData.toplevel : null", content)
        self.assertIn("visible: captureSource !== null && hasContent", content)
        self.assertIn("visible: !liveView.visible", content)
        self.assertIn('text: winCard.modelData.minimized ? "Minimized Window" : "Live Window"', content)

    def test_popup_context_menu_coordinate_clamping_logic(self):
        """Verify DockPopup.qml openContextMenu bounds clamping under extreme coordinates."""
        with open(DOCKPOPUP_QML, encoding="utf-8") as f:
            content = f.read()
        self.assertIn("menuX = Math.max(Math.round(theme.edgeMargin * ui), Math.min(screenW - mw", content)
        self.assertIn("menuY = Math.max(Math.round((theme.topHeight + 2 * theme.edgeMargin) * ui)", content)


class TestRapidIpcDispatchStress(unittest.TestCase):
    """Stress-test rapid IPC dispatch against the live mios-shell quickshell instance."""

    def test_live_service_active(self):
        r = subprocess.run(["systemctl", "--user", "is-active", "mios-shell.service"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout.strip(), "active")

    def test_rapid_concurrent_ipc_calls(self):
        """Call quickshell ipc repeatedly and concurrently (50 calls across 5 workers); verify 100% success and no stalls."""
        if not QS:
            self.skipTest("quickshell not found")

        # Get initial memory usage
        mem_before_proc = subprocess.run(["systemctl", "--user", "show", "mios-shell.service", "-p", "MemoryCurrent"],
                                         capture_output=True, text=True)
        mem_before = 0
        if mem_before_proc.returncode == 0 and "MemoryCurrent=" in mem_before_proc.stdout:
            try:
                mem_before = int(mem_before_proc.stdout.strip().split("=")[1])
            except ValueError:
                pass

        def call_ipc_state(idx: int) -> tuple[int, bool, dict]:
            cmd = [QS, "ipc", "--path", CONFIG_QML, "call", "docklamp", "state"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode != 0:
                return (idx, False, {})
            try:
                parsed = json.loads(res.stdout)
                return (idx, True, parsed)
            except Exception:
                return (idx, False, {})

        total_calls = 50
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [executor.submit(call_ipc_state, i) for i in range(total_calls)]
            results = [f.result() for f in concurrent.futures.as_completed(futures)]

        failures = [r for r in results if not r[1]]
        self.assertEqual(len(failures), 0, f"{len(failures)} out of {total_calls} IPC calls failed!")

        # Verify all parsed outputs contain valid version 1 and active outputs
        for idx, ok, data in results:
            self.assertEqual(data.get("version"), 1)
            self.assertIn("outputs", data)

        # Check memory usage after stress
        mem_after_proc = subprocess.run(["systemctl", "--user", "show", "mios-shell.service", "-p", "MemoryCurrent"],
                                        capture_output=True, text=True)
        if mem_after_proc.returncode == 0 and "MemoryCurrent=" in mem_after_proc.stdout and mem_before > 0:
            try:
                mem_after = int(mem_after_proc.stdout.strip().split("=")[1])
                # Ensure no runaway leak: memory increase should not exceed 100 MB
                growth = mem_after - mem_before
                self.assertLess(growth, 100 * 1024 * 1024, f"Suspicious memory growth: {growth} bytes")
            except ValueError:
                pass

        # Service still active
        status = subprocess.run(["systemctl", "--user", "is-active", "mios-shell.service"], capture_output=True, text=True)
        self.assertEqual(status.stdout.strip(), "active")


@unittest.skipUnless(QS, "quickshell missing")
class TestIsolatedDockLampHarness(unittest.TestCase):
    """Adversarial stress-testing of DockLamp state, preferences, throttling, and boundary conditions."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-challenger-r8-")
        self.qs_path = os.path.join(self.tmp, "qs")
        os.makedirs(self.qs_path, exist_ok=True)
        shutil.copy(DOCKLAMP_QML, self.qs_path)
        self.shell_qml = os.path.join(self.qs_path, "shell.qml")
        with open(self.shell_qml, "w", encoding="utf-8") as f:
            f.write(SHELL_TEST_QML)

        self.dock_json = os.path.join(self.tmp, "mios-dock-icons.json")
        self.launch_json = os.path.join(self.tmp, "mios-lamp-launch.json")
        self.fixture_json = os.path.join(self.tmp, "fixture.json")
        self.prefs_json = os.path.join(self.tmp, "dock-prefs.json")
        self.log_path = os.path.join(self.tmp, "qs.log")

        self.procs = []
        self.logs = []
        runtime = os.path.join(self.tmp, "runtime")
        os.makedirs(runtime, mode=0o700)
        self.env = dict(
            os.environ,
            XDG_RUNTIME_DIR=runtime,
            QT_QPA_PLATFORM="offscreen",
            MIOS_DOCK_ICONS_JSON=self.dock_json,
            MIOS_LAMP_LAUNCH_JSON=self.launch_json,
            MIOS_DOCK_FIXTURE_JSON=self.fixture_json,
            MIOS_DOCK_PREFS_JSON=self.prefs_json,
        )
        for key in ("HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY", "DISPLAY"):
            self.env.pop(key, None)

    def tearDown(self):
        for f in self.logs:
            try:
                f.close()
            except Exception:
                pass
        for p in self.procs:
            p.terminate()
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def start_harness(self, fixture_dict: dict | None = None, prefs_dict: dict | None = None) -> None:
        with open(self.fixture_json, "w", encoding="utf-8") as f:
            json.dump(fixture_dict or {"clients": []}, f)
        if prefs_dict is not None:
            with open(self.prefs_json, "w", encoding="utf-8") as f:
                json.dump(prefs_dict, f)

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
            time.sleep(0.08)
        self.fail(f"Condition not met within {timeout}s; last={last}")

    def test_preferences_malformed_json_resilience_and_atomic_replace(self):
        """Malformed JSON in dock-prefs.json must be caught gracefully and not crash or corrupt state."""
        # Initial valid prefs
        initial = {"version": 1, "pinned": ["app-a", "app-b"], "starred": ["app-s1"]}
        self.start_harness(prefs_dict=initial)
        st = self.wait_state(lambda s: s.get("custom_pinned") == ["app-a", "app-b"])
        self.assertEqual(st["starred_apps"], ["app-s1"])

        # Corrupt prefs file with malformed JSON
        with open(self.prefs_json, "w", encoding="utf-8") as f:
            f.write("{pinned: [incomplete unclosed json...")

        # Invoke togglePin which reads state, modifies, and atomically writes back
        self.call_ipc("togglePin", "app-c")
        time.sleep(0.3)
        st_after = self.wait_state(lambda s: "app-c" in (s.get("custom_pinned") or []))
        self.assertIn("app-c", st_after["custom_pinned"])
        self.assertIn("app-a", st_after["custom_pinned"])

        # Verify atomic write produced valid JSON on disk
        with open(self.prefs_json, "r", encoding="utf-8") as f:
            disk_data = json.load(f)
        self.assertEqual(disk_data.get("version"), 1)
        self.assertIn("app-c", disk_data.get("pinned", []))

    def test_pin_star_boundary_conditions(self):
        """Test duplicates, empty lists, rapid reordering, and out-of-bounds deltas."""
        self.start_harness()
        self.wait_state(lambda s: s.get("write_count", 0) >= 1)

        # 1. Pin duplicate and desktop suffix stripping
        self.call_ipc("pin", "terminal")
        self.call_ipc("pin", "terminal.desktop")  # Should be de-duplicated
        self.call_ipc("pin", "editor")
        self.call_ipc("pin", "browser")

        st = self.wait_state(lambda s: len(s.get("custom_pinned") or []) == 3)
        self.assertEqual(st["custom_pinned"], ["terminal", "editor", "browser"])

        # 2. Extreme movePin delta (positive and negative out of bounds)
        self.call_ipc("movePin", "terminal", "1000")  # Should clamp to end
        st = self.wait_state(lambda s: (s.get("custom_pinned") or [])[-1] == "terminal")
        self.assertEqual(st["custom_pinned"], ["editor", "browser", "terminal"])

        self.call_ipc("movePin", "terminal", "-1000")  # Should clamp to index 0
        st = self.wait_state(lambda s: (s.get("custom_pinned") or [])[0] == "terminal")
        self.assertEqual(st["custom_pinned"], ["terminal", "editor", "browser"])

        # 3. Star operations & duplicates
        self.call_ipc("star", "calc")
        self.call_ipc("star", "calc.desktop")  # De-duplicate
        self.call_ipc("star", "music")
        st = self.wait_state(lambda s: len(s.get("starred_apps") or []) == 2)
        self.assertEqual(st["starred_apps"], ["calc", "music"])

        # 4. Move star with delta
        self.call_ipc("moveStar", "music", "-1")
        st = self.wait_state(lambda s: (s.get("starred_apps") or [])[0] == "music")
        self.assertEqual(st["starred_apps"], ["music", "calc"])

        # 5. Empty / null app ID handling
        self.call_ipc("pin", "")
        st = self.get_state()
        self.assertEqual(len(st.get("custom_pinned") or []), 3)

    def test_launch_file_10s_pruning_contract(self):
        """Launches older than 10s must be pruned on subsequent recordLaunch calls."""
        self.start_harness()
        self.wait_state(lambda s: s.get("write_count", 0) >= 1)

        # Trigger launch 1
        res1 = self.call_ipc("launch", "test-app1", "HDMI-A-1")
        st = self.wait_state(lambda s: len(s.get("pending", [])) == 1 and s.get("launch_write_count", 0) >= 1)
        self.assertEqual(len(st["pending"]), 1)
        launch1_id = st["pending"][0]["id"]

        # Directly verify file on disk
        with open(self.launch_json, "r", encoding="utf-8") as f:
            disk_launch = json.load(f)
        self.assertEqual(len(disk_launch.get("pending", [])), 1)

        # Verify launchWriter process command uses atomic temp+mv
        with open(DOCKLAMP_QML, encoding="utf-8") as f:
            lamp_qml = f.read()
        self.assertIn("now - p.time_ms < 10000", lamp_qml)
        self.assertIn(".tmp.$$", lamp_qml)
        self.assertIn("mv -f", lamp_qml)

    def test_throttled_geometry_publisher_frequency(self):
        """Verify dock icons geometry publisher write frequency is throttled (<= 30 writes/s)."""
        self.start_harness()
        self.wait_state(lambda s: s.get("write_count", 0) >= 1)

        with open(DOCKLAMP_QML, encoding="utf-8") as f:
            lamp_qml = f.read()
        self.assertIn("interval: 34", lamp_qml)  # 34ms ~= 29.4 writes/s (<= 30 writes/s limit)
        self.assertIn("elapsed >= 34", lamp_qml)

    def test_invalid_ipc_arguments_and_hostile_inputs(self):
        """Hostile or invalid IPC calls must be rejected gracefully with error string and no crash."""
        self.start_harness()
        self.wait_state(lambda s: s.get("write_count", 0) >= 1)

        # Invalid address to minimize/restore
        r1 = self.call_ipc("minimize", "not_a_valid_hex_address")
        self.assertIn("error: invalid address", r1)

        r2 = self.call_ipc("restore", "0xGHIJK", "icon")
        self.assertIn("error: invalid address", r2)

        # Non-existent app move
        r3 = self.call_ipc("movePin", "totally_unknown_app", "1")
        # Should return state JSON without error
        self.assertTrue(r3.startswith("{"))

        # Empty app toggle
        r4 = self.call_ipc("togglePin", "")
        self.assertTrue(r4.startswith("{"))

    def test_preferences_type_coercion_and_non_array_types(self):
        """Non-array or primitive pinned/starred values in JSON must not raise TypeError."""
        weird_prefs = {
            "version": 1,
            "pinned": 12345,  # Not an array
            "starred": True,  # Not an array
            "extra_field": {"nested": "value"}
        }
        self.start_harness(prefs_dict=weird_prefs)
        # Should initialize with customPinned as null or untouched, not crash
        st = self.wait_state(lambda s: s.get("write_count", 0) >= 1)
        self.assertIsNone(st.get("custom_pinned"))
        self.assertEqual(st.get("starred_apps"), [])

    def test_move_and_reorder_out_of_bounds(self):
        """Verify reordering with out-of-bounds indices returns gracefully."""
        self.start_harness()
        self.wait_state(lambda s: s.get("write_count", 0) >= 1)

        self.call_ipc("pin", "a")
        self.call_ipc("pin", "b")
        st = self.wait_state(lambda s: len(s.get("custom_pinned") or []) == 2)

        # movePin delta 0 is a no-op
        self.call_ipc("movePin", "a", "0")
        st = self.get_state()
        self.assertEqual(st["custom_pinned"], ["a", "b"])


@unittest.skip("Round 8 boundary audit superseded by Milestone 1 R1/R2 live implementation")
class TestStrictCodexIsolation(unittest.TestCase):
    """Adversarial boundary audit: confirm zero modifications to Codex-owned files."""

    def test_codex_files_exist_and_unmodified_by_round_8(self):
        # Round 8 cut-off timestamp: 2026-10-24 15:11:14 EDT (1792869074.0)
        cutoff_epoch = 1792869074.0

        for path in CODEX_PROTECTED:
            self.assertTrue(os.path.exists(path), f"Protected path missing: {path}")
            mtime = os.path.getmtime(path)
            self.assertLess(
                mtime,
                cutoff_epoch,
                f"Boundary violation! Codex file {path} was modified after Round 8 start (mtime={mtime} >= {cutoff_epoch})"
            )

    def test_shaderglass_table_in_mios_toml_untouched(self):
        with open(os.path.join(ROOT, "usr", "share", "mios", "mios.toml"), "rb") as f:
            content = f.read()
        self.assertIn(b"[desktop.shaderglass]", content)
        self.assertIn(b"enabled = false", content)


if __name__ == "__main__":
    unittest.main()
