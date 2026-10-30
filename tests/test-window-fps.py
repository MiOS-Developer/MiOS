#!/usr/bin/env python3
# AI-hint: Focused-window frame rate beside the host rate on the status island ("144 FPS/92 FPS"): the SSOT switch and
#          configurator input, the mios-pacing meter (source contract + its host test, which the pre-change header
#          cannot build: negative control), and the shell formulas evaluated with node from the QML sources themselves
#          (Config.qml / Nested.qml windowFpsFor, PanelWindow.qml fpsText; the pre-change shells have none).
#          The live counterpart is tests/test-window-fps-live.py (MIOS_LIVE_NESTED=1).
# AI-related: usr/share/mios/hyprland/plugins/mios-pacing/main.cpp, usr/share/mios/hyprland/plugins/mios-pacing/pacing.hpp,
# AI-related: usr/share/mios/hyprland/plugins/mios-pacing/tests/pacing_test.cpp, usr/share/mios/quickshell/PanelWindow.qml,
# AI-related: usr/share/mios/quickshell/Config.qml, usr/share/mios/quickshell/Nested.qml, usr/share/mios/mios.toml [desktop.topbar],
# AI-related: usr/share/mios/configurator/mios.html, tests/test-window-fps-live.py
# AI-functions: qml_function, qml_property, SsotAndSource, HostTest, ShellFormula
"""Focused-window FPS tests."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACING = ROOT / "usr/share/mios/hyprland/plugins/mios-pacing"
QS = ROOT / "usr/share/mios/quickshell"
BASE = os.environ.get("MIOS_WINDOW_FPS_BASE", "main")  # the pre-change revision for the negative controls
BASE_TREE = os.environ.get("MIOS_WINDOW_FPS_BASE_TREE", "")  # or an exported copy of it (a build container has no git)


def git_show(rel: str) -> str | None:
    if BASE_TREE:
        p = Path(BASE_TREE) / rel
        return p.read_text() if p.is_file() else None
    r = subprocess.run(["git", "-C", str(ROOT), "show", f"{BASE}:{rel}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 else None


def qml_function(text: str, name: str) -> str | None:
    """The JS source of `function name(...) {...}` in a QML file (balanced braces), or None."""
    m = re.search(rf"function {name}\([^)]*\)\s*\{{", text)
    if not m:
        return None
    depth, i = 0, m.end() - 1
    while i < len(text):
        depth += {"{": 1, "}": -1}.get(text[i], 0)
        if depth == 0:
            return text[m.start():i + 1]
        i += 1
    return None


def qml_property(text: str, name: str) -> str | None:
    """The binding expression of `readonly property <type> name: ...` (continuation lines included), or None."""
    m = re.search(rf"^(\s*)readonly property \w+ {name}:(.*)$", text, re.M)
    if not m:
        return None
    lines, indent = [m.group(2)], len(m.group(1))
    for line in text[m.end() + 1:].splitlines():
        if not line.strip() or len(line) - len(line.lstrip()) <= indent:
            break
        lines.append(line)
    return "\n".join(lines)


def node(script: str):
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    if r.returncode != 0:
        raise AssertionError(r.stderr)
    return json.loads(r.stdout)


class SsotAndSource(unittest.TestCase):
    def test_ssot_switch_and_configurator(self) -> None:
        d = tomllib.loads((ROOT / "usr/share/mios/mios.toml").read_text())
        self.assertIs(d["desktop"]["topbar"]["window_fps"], True)
        html = (ROOT / "usr/share/mios/configurator/mios.html").read_text()
        self.assertRegex(html, r'data-key="desktop\.topbar\.window_fps" data-type="bool"')

    def test_plugin_meter_contract(self) -> None:
        main = (PACING / "main.cpp").read_text()
        for need in ("m_events.stateCommit.listen", "m_events.newSubsurface.listen", "m_events.window.active.listen",
                     "m_events.window.destroy.listen", "MiosPacing::isFrame(", '\\"window\\":', 'rest == "window"', "followFocused()"):
            self.assertIn(need, main)
        hdr = (PACING / "pacing.hpp").read_text()
        self.assertIn("struct SWindowRate", hdr)
        self.assertIn("inline bool isFrame(", hdr)
        # Negative control: the pre-change plugin has no window meter.
        old = git_show("usr/share/mios/hyprland/plugins/mios-pacing/main.cpp")
        if old is not None:
            self.assertNotIn("stateCommit", old)
            self.assertNotIn('\\"window\\":', old)

    def test_shells_read_the_meter(self) -> None:
        for f in ("Config.qml", "Nested.qml"):
            t = (QS / f).read_text()
            self.assertIn("frameWindow = status.window || null", t, f)
            self.assertIsNotNone(qml_function(t, "windowFpsFor"), f)
        nested = (QS / "Nested.qml").read_text()
        self.assertIn('["hyprctl", "mios-pacing", "status"]', nested)
        self.assertIn('"desktop.topbar", "window_fps"', nested)
        panel = (QS / "PanelWindow.qml").read_text()
        self.assertIn("text: pill.fpsText", panel)
        self.assertIn("window_fps: pill.windowFps", panel)
        # Negative controls: the pre-change shells cannot show a window rate (the nested one shows no rate at all).
        for f in ("Config.qml", "Nested.qml"):
            old = git_show(f"usr/share/mios/quickshell/{f}")
            if old is not None:
                self.assertIsNone(qml_function(old, "windowFpsFor"), f)
        old = git_show("usr/share/mios/quickshell/Nested.qml")
        if old is not None:
            self.assertIn("function fpsFor(name) { return -1 }", old)
        old = git_show("usr/share/mios/quickshell/PanelWindow.qml")
        if old is not None:
            self.assertIsNone(qml_property(old, "fpsText"))
            self.assertNotIn("windowFps", old)


@unittest.skipUnless(shutil.which("g++") and shutil.which("make"), "no C++ toolchain")
class HostTest(unittest.TestCase):
    def test_meter_host_test_passes(self) -> None:
        with tempfile.TemporaryDirectory() as t:
            r = subprocess.run(["make", "-C", str(PACING), "test", f"TEST={t}/gate"], capture_output=True, text=True, timeout=300)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
            self.assertIn("all tests passed", r.stdout)

    def test_pre_change_header_cannot_build_the_meter_test(self) -> None:
        old = git_show("usr/share/mios/hyprland/plugins/mios-pacing/pacing.hpp")
        if old is None:
            self.skipTest(f"{BASE} not available")
        with tempfile.TemporaryDirectory() as t:
            tp = Path(t)
            (tp / "tests").mkdir()
            (tp / "pacing.hpp").write_text(old)
            shutil.copy(PACING / "tests/pacing_test.cpp", tp / "tests/pacing_test.cpp")
            r = subprocess.run(["g++", "-std=c++2b", "-fsyntax-only", str(tp / "tests/pacing_test.cpp")], capture_output=True, text=True, timeout=300)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("SWindowRate", r.stderr)


@unittest.skipUnless(shutil.which("node"), "no node")
class ShellFormula(unittest.TestCase):
    """The QML formulas, evaluated from the QML source text."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.config = qml_function((QS / "Config.qml").read_text(), "windowFpsFor")
        cls.nested = qml_function((QS / "Nested.qml").read_text(), "windowFpsFor")
        cls.text = qml_property((QS / "PanelWindow.qml").read_text(), "fpsText")
        assert cls.config and cls.nested and cls.text

    def window_fps(self, src: str, cases: list) -> list:
        js = f"""
const out = [];
for (const c of {json.dumps(cases)}) {{
  const self = {{ topbarConfig: c.cfg || {{}}, frameWindow: c.win, windowFpsShown: c.shown !== false }};
  const f = new Function("self", "name", "with (self) {{ return (" + {json.dumps(src.replace("function windowFpsFor(name)", "function (name)", 1))} + ")(name) }}");
  out.push(f(self, c.name));
}}
console.log(JSON.stringify(out));
"""
        return node(js)

    def test_window_fps_for(self) -> None:
        win = {"address": "0x1", "output": "DP-1", "fps": 91.6}
        cases = [
            {"name": "DP-1", "win": win},                                 # 92 on the window's output
            {"name": "DP-2", "win": win},                                 # another output: hidden
            {"name": "DP-1", "win": None},                                # nothing focused: hidden
            {"name": "DP-1", "win": win, "cfg": {"window_fps": False}, "shown": False},  # SSOT off
            {"name": "DP-1", "win": {"output": "DP-1", "fps": 0}},        # an idle window: 0, still known
            {"name": "DP-1", "win": {"output": "DP-1", "fps": "x"}},      # garbage: hidden
        ]
        want = [92, -1, -1, -1, 0, -1]
        self.assertEqual(self.window_fps(self.config, cases), want)
        self.assertEqual(self.window_fps(self.nested, cases), want)

    def test_island_text(self) -> None:
        cases = [
            {"remoteFrames": False, "outputFps": 144, "windowFps": 92},
            {"remoteFrames": False, "outputFps": 144, "windowFps": -1},
            {"remoteFrames": False, "outputFps": -1, "windowFps": 30},
            {"remoteFrames": False, "outputFps": 60, "windowFps": 0},
            {"remoteFrames": True, "outputFps": 59.94, "windowFps": 30},
        ]
        js = f"""
const out = [];
for (const c of {json.dumps(cases)}) {{
  const f = new Function("c", "with (c) {{ return (" + {json.dumps(self.text)} + ") }}");
  out.push(f(c));
}}
console.log(JSON.stringify(out));
"""
        self.assertEqual(node(js), ["144 FPS/92 FPS", "144 FPS", "FPS —/30 FPS", "60 FPS/0 FPS", "59.9 RDP FPS/30 FPS"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
