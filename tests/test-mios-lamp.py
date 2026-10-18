#!/usr/bin/env python3
# AI-hint: Magic lamp ([desktop.hyprland.lamp], mios-wobbly.so lamp.cpp/lampmath.hpp, and Quickshell DockLamp.qml +
# Sidebar.qml) and per-vblank wobbly frame-pacing tests. Verifies SSOT keys and feature-detected minimize actions
# (positive and negative controls), compiles and runs the C++ host test suites (tests/model_test.cpp and
# tests/lamp_test.cpp), runs qmllint on DockLamp.qml and Sidebar.qml (with a planted syntax-error negative control),
# and exercises DockLamp.qml in a window-less Quickshell across desktop (1920x1080), hover-magnification, auto-hide,
# phone output at scale 2.5 (432x936 at monX=2000) and remote phone (ui=2.5), minimized window tracking on
# special:minimized, atomic dock-icons/launch JSON publication, 10 s launch pruning, and stale/malformed fallbacks.
# Never touches the live compositor or operator windows.
# AI-related: usr/share/mios/mios.toml [desktop.hyprland.lamp] [desktop.hyprland.hyprbars] [desktop.mobile],
# tools/native/mios-gen/src/render.rs (lamp_profile), usr/share/mios/hyprland/plugins/mios-wobbly/lamp.cpp,
# usr/share/mios/hyprland/plugins/mios-wobbly/lampmath.hpp, usr/share/mios/hyprland/plugins/mios-wobbly/wobble_model.hpp,
# usr/share/mios/hyprland/plugins/mios-wobbly/tests/lamp_test.cpp, usr/share/mios/hyprland/plugins/mios-wobbly/tests/model_test.cpp,
# usr/share/mios/quickshell/DockLamp.qml, usr/share/mios/quickshell/Sidebar.qml
"""Magic lamp and wobbly frame-pacing unit and integration tests."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import tomllib
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SSOT = os.environ.get("MIOS_SSOT") or os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
QS_DIR = os.environ.get("MIOS_QS_DIR") or os.path.join(ROOT, "usr", "share", "mios", "quickshell")
PLUGIN_DIR = os.path.join(ROOT, "usr", "share", "mios", "hyprland", "plugins", "mios-wobbly")
RENDER_RS = os.path.join(ROOT, "tools", "native", "mios-gen", "src", "render.rs")
QS = shutil.which("quickshell")
CXX = shutil.which("g++") or shutil.which("clang++")

EVENTS = {"launch", "minimize", "restore"}
IDENT = re.compile(r"^[A-Za-z0-9_-]+$")


def load_ssot() -> dict:
    with open(SSOT, "rb") as f:
        return tomllib.load(f)


def lamp_ssot_errors(d: dict) -> list[str]:
    h = d.get("desktop", {}).get("hyprland", {})
    lamp = h.get("lamp")
    if not isinstance(lamp, dict):
        return ["[desktop.hyprland.lamp] missing"]
    if "mios-wobbly" not in h:
        return ["[desktop.hyprland.mios-wobbly] missing"]
    errs = []
    if not isinstance(lamp.get("enabled"), bool):
        errs.append("enabled must be a boolean")
    ev = lamp.get("events")
    if not isinstance(ev, list) or not ev or not all(isinstance(x, str) and x in EVENTS for x in ev):
        errs.append("events must be a non-empty list of launch, minimize, restore")
    for k, lo, hi in (
        ("duration_open_ms", 50, 5000),
        ("duration_minimize_ms", 50, 5000),
        ("duration_restore_ms", 50, 5000),
        ("mesh_columns", 1, 64),
        ("mesh_rows", 4, 128),
        ("launch_timeout_ms", 500, 60000),
    ):
        v = lamp.get(k)
        if isinstance(v, bool) or not isinstance(v, int) or not (lo <= v <= hi):
            errs.append(f"{k} must be an integer in {lo}..={hi}")
    c = lamp.get("curve")
    curves = {x.get("name"): x.get("points") for x in h.get("curves", []) if isinstance(x, dict)}
    pts = curves.get(c) if isinstance(c, str) else c
    if (
        not isinstance(pts, list)
        or len(pts) != 2
        or not all(isinstance(p, list) and len(p) == 2 and all(isinstance(n, (int, float)) for n in p) for p in pts)
        or not (0.0 <= pts[0][0] <= 1.0 and 0.0 <= pts[1][0] <= 1.0 and -2.0 <= pts[0][1] <= 3.0 and -2.0 <= pts[1][1] <= 3.0)
    ):
        errs.append("curve must be [[x1, y1], [x2, y2]] with x in 0..=1 or a named curve")
    if lamp.get("hidden_dock") not in ("edge", "icon"):
        errs.append("hidden_dock must be 'edge' or 'icon'")
    if lamp.get("fallback_edge") not in ("bottom", "top", "left", "right"):
        errs.append("fallback_edge must be bottom, top, left, or right")
    pa = lamp.get("pane_alpha")
    if isinstance(pa, bool) or not isinstance(pa, (int, float)) or not (0.05 <= float(pa) <= 1.0):
        errs.append("pane_alpha must be a number in 0.05..=1.0")
    ws = lamp.get("minimized_workspace")
    if not isinstance(ws, str) or not ws.startswith("special:") or not IDENT.match(ws[len("special:"):]):
        errs.append("minimized_workspace must be special:<name>")
    tag = lamp.get("anim_tag")
    if not isinstance(tag, str) or not IDENT.match(tag):
        errs.append("anim_tag must match [A-Za-z0-9_-]+")
    if lamp.get("remote") != "same":
        errs.append("remote must be 'same'")
    if not isinstance(lamp.get("hide_hyprbars"), bool):
        errs.append("hide_hyprbars must be a boolean")
    sh = lamp.get("windowshade_open")
    if sh is not None and (not isinstance(sh, str) or not IDENT.match(sh)):
        errs.append("windowshade_open must be a shader identifier")
    return errs


def minimize_action_errors(d: dict) -> list[str]:
    errs = []
    buttons = d.get("desktop", {}).get("hyprland", {}).get("hyprbars", {}).get("buttons", [])
    min_btns = [b for b in buttons if b.get("icon") == "–" or "minimize" in b.get("action", "")]
    if not min_btns:
        errs.append("hyprbars minimize button missing")
    else:
        act = min_btns[0].get("action", "")
        if "hl.plugin.mios_lamp and hl.plugin.mios_lamp.minimize()" not in act:
            errs.append(f"hyprbars minimize button does not feature-detect hl.plugin.mios_lamp.minimize: {act}")
        if 'workspace = "special:minimized"' not in act or "follow = false" not in act:
            errs.append(f"hyprbars minimize button missing silent special:minimized fallback: {act}")
    wc = d.get("desktop", {}).get("mobile", {}).get("window_controls", [])
    mob_min = [c for c in wc if c.get("label") == "Minimize"]
    if not mob_min:
        errs.append("mobile Minimize window control missing")
    else:
        act = mob_min[0].get("action", "")
        if 'hl.plugin.mios_lamp and hl.plugin.mios_lamp.minimize({window = "address:{address}"})' not in act:
            errs.append(f"mobile Minimize does not feature-detect hl.plugin.mios_lamp.minimize: {act}")
        if 'workspace = "special:minimized"' not in act or "follow = false" not in act:
            errs.append(f"mobile Minimize missing silent special:minimized fallback: {act}")
    return errs


class SsotAndProjection(unittest.TestCase):
    def test_positive_vendor_ssot_and_minimize_actions(self):
        d = load_ssot()
        self.assertEqual(lamp_ssot_errors(d), [])
        self.assertEqual(minimize_action_errors(d), [])
        with open(RENDER_RS, encoding="utf-8") as f:
            rs = f.read()
        self.assertIn("out += &lamp_profile(data)?;", rs)
        self.assertIn("pub fn lamp_profile(data: &Value)", rs)

    def test_negative_invalid_lamp_keys_and_unfallback_actions_rejected(self):
        base = load_ssot()
        bad_cases = (
            ("enabled", "yes", "enabled"),
            ("events", ["close"], "events"),
            ("events", [], "events"),
            ("duration_open_ms", 10, "duration_open_ms"),
            ("duration_minimize_ms", 9000, "duration_minimize_ms"),
            ("mesh_columns", 0, "mesh_columns"),
            ("mesh_rows", 2, "mesh_rows"),
            ("curve", [[1.5, 0.0], [0.2, 1.0]], "curve"),
            ("curve", "nonexistentCurve", "curve"),
            ("hidden_dock", "nowhere", "hidden_dock"),
            ("fallback_edge", "center", "fallback_edge"),
            ("pane_alpha", 0.0, "pane_alpha"),
            ("pane_alpha", 1.5, "pane_alpha"),
            ("minimized_workspace", "minimized", "minimized_workspace"),
            ("minimized_workspace", "special:bad space", "minimized_workspace"),
            ("anim_tag", "../bad", "anim_tag"),
            ("remote", "lite", "remote"),
            ("hide_hyprbars", "true", "hide_hyprbars"),
            ("windowshade_open", "../none", "windowshade_open"),
            ("launch_timeout_ms", 100, "launch_timeout_ms"),
        )
        for k, v, frag in bad_cases:
            d = copy.deepcopy(base)
            d["desktop"]["hyprland"]["lamp"][k] = v
            errs = lamp_ssot_errors(d)
            self.assertTrue(any(frag in e for e in errs), (k, v, errs))

        d_act = copy.deepcopy(base)
        d_act["desktop"]["hyprland"]["hyprbars"]["buttons"][1]["action"] = (
            "hyprctl dispatch 'hl.dsp.window.move({workspace = \"special:minimized\"})'"
        )
        self.assertTrue(minimize_action_errors(d_act))


@unittest.skipUnless(CXX, "C++ compiler missing")
class PluginHostTests(unittest.TestCase):
    """Runs the C++ unit tests for wobble_model.hpp and lampmath.hpp (positive and negative controls)."""

    def test_wobble_model_and_lampmath_cpp_suites(self):
        with tempfile.TemporaryDirectory(prefix="mios-lamp-cpp-") as d:
            for src_name, bin_name in (("model_test.cpp", "model-test"), ("lamp_test.cpp", "lamp-test")):
                src = os.path.join(PLUGIN_DIR, "tests", src_name)
                exe = os.path.join(d, bin_name)
                comp = subprocess.run(
                    [CXX, "-O2", "-std=c++2b", "-Wall", "-Wextra", f"-I{PLUGIN_DIR}", src, "-o", exe],
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertEqual(comp.returncode, 0, comp.stderr)
                run = subprocess.run([exe], capture_output=True, text=True, timeout=30)
                self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
                self.assertTrue("passed" in run.stdout or "OK (0 failures)" in run.stdout, run.stdout)

    def test_wobble_shader_uniforms_and_fade_contract(self):
        main_cpp = os.path.join(PLUGIN_DIR, "main.cpp")
        with open(main_cpp, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("uniform float uGain;", src)
        self.assertIn("clamp(texture(uTex, vUV) * uGain, 0.0, 1.0)", src)
        self.assertIn("MiosLamp::wobblePaneFade", src)
        self.assertIn("WINDOW_ALPHA_LAYOUT", src)
        self.assertIn("            w->alpha(Desktop::View::WINDOW_ALPHA_LAYOUT)->setValueAndWarp(s.paneFade);\n", src)
        self.assertIn("            glUniform1f(g_locGain, gain);", src)
        self.assertNotIn("glUniform1f(g_locGain, 1.0f);", src)

    def test_lamp_cpp_compositor_contracts(self):
        lamp_cpp = os.path.join(PLUGIN_DIR, "lamp.cpp")
        with open(lamp_cpp, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn("        rec.geometry  = CBox{w->positionAnimation()->goal(), w->sizeAnimation()->goal()};\n", src)
        self.assertEqual(src.count("rec.geometry  = CBox{w->positionAnimation()->goal(), w->sizeAnimation()->goal()};"), 2)
        self.assertIn(
            "        doRestoreMoves(*s, w);\n        g_lamps.emplace(w.get(), std::move(s));",
            src,
        )
        self.assertIn(
            '                continue; // Do NOT overwrite with "slot", "edge", or "fallback"',
            src,
        )
        self.assertIn(
            "                sib->positionAnimation()->warp();\n                sib->sizeAnimation()->warp();",
            src,
        )


class QmlSyntax(unittest.TestCase):
    def test_positive_qmllint_dock_lamp_and_sidebar(self):
        lint = shutil.which("qmllint") or "/usr/lib64/qt6/bin/qmllint"
        if not os.access(lint, os.X_OK):
            self.skipTest("qmllint missing")
        for name in ("DockLamp.qml", "Sidebar.qml", "Config.qml"):
            out = subprocess.run(
                [lint, "-I", "/usr/lib64/qt6/qml", name],
                cwd=QS_DIR,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertNotIn("[syntax]", out.stdout + out.stderr, name)
            self.assertNotIn("Error:", out.stdout + out.stderr, name)

    def test_negative_qmllint_catches_planted_syntax_error(self):
        lint = shutil.which("qmllint") or "/usr/lib64/qt6/bin/qmllint"
        if not os.access(lint, os.X_OK):
            self.skipTest("qmllint missing")
        with tempfile.TemporaryDirectory(prefix="mios-lamp-lint-") as d:
            bad = os.path.join(d, "BrokenDock.qml")
            with open(bad, "w", encoding="utf-8") as f:
                f.write("import QtQuick\nItem { property var rect: [1, 2, }\n")
            out = subprocess.run([lint, bad], cwd=d, capture_output=True, text=True, timeout=30)
            self.assertIn("[syntax]", out.stdout + out.stderr)

    def test_sidebar_minimized_tile_restores_with_minimized_source(self):
        sidebar_qml = os.path.join(QS_DIR, "Sidebar.qml")
        with open(sidebar_qml, "r", encoding="utf-8") as f:
            src = f.read()
        self.assertIn('DockLamp.restore(addr, "minimized", rect)', src)
        self.assertNotIn('DockLamp.restore(addr, "icon", rect)', src)


DOCK_HARNESS_QML = """import QtQuick
import Quickshell

ShellRoot {
    id: root
    Component.onCompleted: {
        // 1. Desktop output (1920x1080, scale 1, ui 1, 4 pinned icons)
        const deskTiles = [
            { kind: "drawer", scale: 1 },
            { kind: "icon", app_id: "mios-terminal", desktop_id: "mios-terminal.desktop", classes: ["mios-terminal", "Alacritty"], scale: 1 },
            { kind: "icon", app_id: "mios-zen", desktop_id: "mios-zen.desktop", classes: ["mios-zen", "zen"], scale: 1 },
            { kind: "icon", app_id: "org.gnome.Nautilus", desktop_id: "org.gnome.Nautilus.desktop", classes: ["org.gnome.Nautilus", "nautilus"], scale: 1 },
            { kind: "icon", app_id: "org.gnome.Software", desktop_id: "org.gnome.Software.desktop", classes: ["org.gnome.Software"], scale: 1 }
        ]
        DockLamp.setOutput("HDMI-A-1", DockLamp.computeOutputGeometry({
            monX: 0, monY: 0, screenLogicalW: 1920, screenLogicalH: 1080, ui: 1,
            edgeMargin: 8, dockHeight: 56, dockRevealPx: 2, visible: true, edge: "bottom",
            tiles: deskTiles
        }))

        // 2. Desktop output with hover-magnified icon 0 (scale 1.15)
        const magTiles = JSON.parse(JSON.stringify(deskTiles))
        magTiles[1].scale = 1.15
        DockLamp.setOutput("HDMI-MAG", DockLamp.computeOutputGeometry({
            monX: 0, monY: 0, screenLogicalW: 1920, screenLogicalH: 1080, ui: 1,
            edgeMargin: 8, dockHeight: 56, dockRevealPx: 2, visible: true, edge: "bottom",
            tiles: magTiles
        }))

        // 3. Auto-hidden dock (visible = false)
        DockLamp.setOutput("HDMI-HIDDEN", DockLamp.computeOutputGeometry({
            monX: 0, monY: 0, screenLogicalW: 1920, screenLogicalH: 1080, ui: 1,
            edgeMargin: 8, dockHeight: 56, dockRevealPx: 2, visible: false, edge: "bottom",
            tiles: deskTiles
        }))

        // 4. Phone output at compositor scale 2.5 (1080x2340 -> 432x936 logical at monX=2000, monY=0, ui=1)
        // with one minimized window in the dock strip
        const phoneTiles = [
            { kind: "drawer", scale: 1 },
            { kind: "icon", app_id: "mios-terminal", desktop_id: "mios-terminal.desktop", classes: ["mios-terminal", "Alacritty"], scale: 1 },
            { kind: "minimized", address: "0xdeadbeef01", scale: 1 }
        ]
        DockLamp.setOutput("HEADLESS-PHONE", DockLamp.computeOutputGeometry({
            monX: 2000, monY: 0, screenLogicalW: 432, screenLogicalH: 936, ui: 1,
            edgeMargin: 8, dockHeight: 56, dockRevealPx: 2, visible: true, edge: "bottom",
            tiles: phoneTiles
        }))

        // 5. Remote phone output where shell applies ui=2.5 (1100x2080 at monX=3000, monY=0, ui=2.5)
        DockLamp.setOutput("HEADLESS-RPHONE", DockLamp.computeOutputGeometry({
            monX: 3000, monY: 0, screenLogicalW: 1100, screenLogicalH: 2080, ui: 2.5,
            edgeMargin: 8, dockHeight: 56, dockRevealPx: 2, visible: true, edge: "bottom",
            tiles: phoneTiles
        }))

        // Seed an expired launch (15 s old) and then record a fresh launch to test 10 s pruning
        DockLamp.pendingLaunches = [{
            id: "old-stale-entry",
            time_ms: Date.now() - 15000,
            app_id: "stale.app",
            desktop_id: "stale.app.desktop",
            classes: ["stale.app"],
            output: "HDMI-A-1",
            rect: [880, 1016, 48, 56]
        }]
        DockLamp.recordLaunch(
            { id: "org.gnome.Nautilus.desktop", appId: "org.gnome.Nautilus", startupClass: "nautilus", icon: "org.gnome.Nautilus" },
            "HDMI-A-1",
            [992, 1016, 48, 56]
        )
        DockLamp.flushDockWrite()
    }
}
"""


@unittest.skipUnless(QS, "quickshell missing")
class DockLampContractIntegration(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-docklamp-")
        self.qs = os.path.join(self.tmp, "qs")
        os.makedirs(self.qs, exist_ok=True)
        shutil.copy(os.path.join(QS_DIR, "DockLamp.qml"), self.qs)
        self.shell_qml = os.path.join(self.qs, "shell.qml")
        with open(self.shell_qml, "w", encoding="utf-8") as f:
            f.write(DOCK_HARNESS_QML)
        self.dock_json = os.path.join(self.tmp, "mios-dock-icons.json")
        self.launch_json = os.path.join(self.tmp, "mios-lamp-launch.json")
        self.fixture_json = os.path.join(self.tmp, "fixture.json")
        self.log_path = os.path.join(self.tmp, "qs.log")
        self.procs = []
        self.logs = []
        self.env = dict(os.environ)
        for k in ("HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY", "DISPLAY"):
            self.env.pop(k, None)

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

    def start_harness(self, fixture_text: str | None = None) -> None:
        if fixture_text is not None:
            with open(self.fixture_json, "w", encoding="utf-8") as f:
                f.write(fixture_text)
        self.env = dict(
            os.environ,
            QT_QPA_PLATFORM="offscreen",
            MIOS_DOCK_ICONS_JSON=self.dock_json,
            MIOS_LAMP_LAUNCH_JSON=self.launch_json,
            MIOS_DOCK_FIXTURE_JSON=self.fixture_json if fixture_text is not None else "",
        )
        for k in ("HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY", "DISPLAY"):
            self.env.pop(k, None)

        sig = self.env.get("HYPRLAND_INSTANCE_SIGNATURE", "")
        if "5329" in sig or "1792180537" in sig:
            raise RuntimeError(f"Unsafe compositor instance signature targeting live compositor: {sig}")

        stub_dir = os.path.join(self.tmp, "bin")
        os.makedirs(stub_dir, exist_ok=True)
        stub_hyprctl = os.path.join(stub_dir, "hyprctl")
        with open(stub_hyprctl, "w", encoding="utf-8") as f:
            f.write(
                "#!/bin/sh\n"
                'sig="${HYPRLAND_INSTANCE_SIGNATURE:-}"\n'
                'case "$sig" in\n'
                '    *5329*|release-v0.56.2_1792180537*)\n'
                '        echo "FATAL: hyprctl called against live compositor instance $sig" >&2\n'
                '        exit 1\n'
                '        ;;\n'
                'esac\n'
                'echo "ok"\n'
                'exit 0\n'
            )
        os.chmod(stub_hyprctl, 0o755)
        self.env["PATH"] = f"{stub_dir}:{self.env.get('PATH', '')}"

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
        r = subprocess.run(
            [QS, "ipc", "--path", self.shell_qml, "call", "docklamp", *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=15,
        )
        return r.stdout.strip()

    def wait_state(self, cond, timeout: float = 10.0) -> dict:
        end = time.monotonic() + timeout
        last = {}
        while time.monotonic() < end:
            out = self.call_ipc("state")
            if out.startswith("{"):
                last = json.loads(out)
                if cond(last):
                    return last
            time.sleep(0.15)
        log_txt = open(self.log_path, encoding="utf-8").read() if os.path.exists(self.log_path) else ""
        self.fail(f"DockLamp condition not met within {timeout}s; last={last}; log={log_txt}")

    def test_positive_geometry_publication_magnification_autohide_phone_and_launches(self):
        fixture = json.dumps({
            "clients": [
                {"address": "0xdeadbeef01", "workspace": {"name": "special:minimized"}, "class": "lamptest.phone1", "title": "Phone Win", "monitorName": "HEADLESS-PHONE"},
                {"address": "0x12345678", "workspace": {"name": "1"}, "class": "org.gnome.Nautilus", "title": "Files", "monitorName": "HDMI-A-1"},
            ]
        })
        self.start_harness(fixture)
        st = self.wait_state(lambda s: s.get("write_count", 0) >= 1 and s.get("launch_write_count", 0) >= 1)

        # Verify atomic files on disk
        self.assertTrue(os.path.isfile(self.dock_json))
        self.assertTrue(os.path.isfile(self.launch_json))
        with open(self.dock_json, encoding="utf-8") as f:
            disk_dock = json.load(f)
        with open(self.launch_json, encoding="utf-8") as f:
            disk_launch = json.load(f)

        self.assertEqual(disk_dock["version"], 1)
        outs = disk_dock["outputs"]

        # 1. Desktop (1920x1080): centered dock [816, 1016, 288, 56], 4 icons, slot at [1104, 1016, 48, 56]
        desk = outs["HDMI-A-1"]
        self.assertTrue(desk["visible"])
        self.assertEqual(desk["edge"], "bottom")
        self.assertEqual(desk["dock_rect"], [816, 1016, 288, 56])
        self.assertEqual(desk["edge_rect"], [816, 1078, 288, 2])
        self.assertEqual([ic["rect"] for ic in desk["icons"]], [
            [880, 1016, 48, 56],
            [936, 1016, 48, 56],
            [992, 1016, 48, 56],
            [1048, 1016, 48, 56],
        ])
        self.assertEqual(desk["minimized_slot"], [1104, 1016, 48, 56])

        # 2. Hover-magnification on icon 0 expands width/height and shifts x/y by >= 1 px around center
        mag = outs["HDMI-MAG"]
        self.assertEqual(mag["icons"][0]["rect"], [877, 1012, 55, 64])
        self.assertEqual(mag["icons"][1]["rect"], [936, 1016, 48, 56])

        # 3. Auto-hide sets visible=false while preserving dock_rect and edge_rect
        hid = outs["HDMI-HIDDEN"]
        self.assertFalse(hid["visible"])
        self.assertEqual(hid["dock_rect"], [816, 1016, 288, 56])
        self.assertEqual(hid["edge_rect"], [816, 1078, 288, 2])

        # 4. Phone output at scale 2.5 (432x936 logical at monX=2000, monY=0) includes global offset + minimized[]
        phone = outs["HEADLESS-PHONE"]
        self.assertEqual(phone["dock_rect"], [2128, 872, 176, 56])
        self.assertEqual(phone["icons"][0]["rect"], [2192, 872, 48, 56])
        self.assertEqual(phone["minimized"], [{"address": "0xdeadbeef01", "rect": [2248, 872, 48, 56]}])

        # 5. Remote phone output with ui=2.5 (1100x2080 at monX=3000) scales dock and icon rects by 2.5x
        rphone = outs["HEADLESS-RPHONE"]
        self.assertEqual(rphone["dock_rect"], [3330, 1920, 440, 140])
        self.assertEqual(rphone["icons"][0]["rect"], [3490, 1920, 120, 140])
        self.assertEqual(rphone["minimized"], [{"address": "0xdeadbeef01", "rect": [3630, 1920, 120, 140]}])

        # 6. Minimized window tracking: only special:minimized client is in minimized_windows
        self.assertEqual([m["address"] for m in st["minimized_windows"]], ["0xdeadbeef01"])

        # 7. Launch file pruned the 15s-old stale entry and kept only the fresh Nautilus launch
        self.assertEqual(len(disk_launch["pending"]), 1)
        p0 = disk_launch["pending"][0]
        self.assertEqual(p0["app_id"], "org.gnome.Nautilus")
        self.assertIn("nautilus", p0["classes"])
        self.assertEqual(p0["rect"], [992, 1016, 48, 56])

        # 8. Feature-detected minimize and restore dispatchers
        min_cmd = self.call_ipc("minimize", "0xdeadbeef01")
        self.assertIn('hl.plugin.mios_lamp and hl.plugin.mios_lamp.minimize({window = "address:0xdeadbeef01"})', min_cmd)
        self.assertIn('hl.dsp.window.move({workspace = "special:minimized", window = "address:0xdeadbeef01", follow = false})', min_cmd)

        res_cmd = self.call_ipc("restore", "0xdeadbeef01", "minimized")
        self.assertIn('hl.plugin.mios_lamp and hl.plugin.mios_lamp.restore({window = "address:0xdeadbeef01", source = "minimized"})', res_cmd)
        self.assertIn('source = "minimized"', res_cmd)
        self.assertIn('hl.dsp.window.move({workspace = "e+0", window = "address:0xdeadbeef01"})', res_cmd)

        st_after = json.loads(self.call_ipc("state"))
        self.assertIn("0xdeadbeef01", st_after.get("restoring_windows", {}))

    def test_negative_malformed_fixture_and_bad_address_degrade_safely(self):
        self.start_harness("{malformed json")
        st = self.wait_state(lambda s: s.get("write_count", 0) >= 1)
        self.assertEqual(st["minimized_windows"], [])
        # Invalid window address must be rejected immediately and never injected
        bad_cmd = self.call_ipc("minimize", '0x12"; os.execute("bad")')
        self.assertEqual(bad_cmd, "error: invalid address")
        st_after = json.loads(self.call_ipc("state"))
        self.assertNotIn("os.execute", st_after.get("last_dispatched", ""))
        self.assertNotIn("0x12", st_after.get("last_dispatched", ""))


if __name__ == "__main__":
    unittest.main()
