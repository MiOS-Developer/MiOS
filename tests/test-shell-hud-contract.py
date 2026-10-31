#!/usr/bin/env python3
# AI-hint: Offline contract of the shell/HUD lane: the [desktop.topbar] scroll-volume keys and the pinned
# on_focus_under_fullscreen are valid SSOT (with mutated negative controls) and projected into hyprland.lua and the
# configurator; the island carries the wheel handler, the percentage chip and the volumeWheel IPC; drawer, search and
# keymap use the shared Glass corner rule and fill without a hand-drawn outline (main's versions are the negative
# control); battery rows show a device glyph and a level glyph; the carried Quickshell patches are well-formed, the
# bake step applies the event-socket reconnect, and (with MIOS_QUICKSHELL_SRC, an upstream checkout) the patch applies
# and the update-group gate passes. Nothing here touches the live desktop.
# AI-related: usr/share/mios/quickshell/PanelWindow.qml, usr/share/mios/quickshell/AppDrawer.qml,
# usr/share/mios/quickshell/Search.qml, usr/share/mios/quickshell/Keymap.qml, usr/share/mios/mios.toml [desktop.topbar],
# src/quickshell/patches/hyprland-event-socket-reconnect.patch, automation/66-bake-quickshell.sh,
# tests/test-shell-hud-live.py, tests/test-quickshell-candidate-live.py
# AI-functions: topbar_errors, misc_errors, glass_surface_errors, TestSsot, TestQml, TestQuickshellPatches
"""Shell/HUD lane contract tests (offline)."""
from __future__ import annotations

import copy
import os
import re
import subprocess
import tempfile
import tomllib
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SSOT = os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
QS = os.path.join(ROOT, "usr", "share", "mios", "quickshell")
LUA = os.path.join(ROOT, "usr", "share", "mios", "hyprland", "hyprland.lua")
HTML = os.path.join(ROOT, "usr", "share", "mios", "configurator", "mios.html")
PATCH = os.path.join(ROOT, "src", "quickshell", "patches", "hyprland-event-socket-reconnect.patch")
BAKE = os.path.join(ROOT, "automation", "66-bake-quickshell.sh")
GATE = os.path.join(ROOT, "src", "quickshell", "check-update-groups.py")


def read(p: str) -> str:
    with open(p, encoding="utf-8") as f:
        return f.read()


def ssot() -> dict:
    with open(SSOT, "rb") as f:
        return tomllib.load(f)


def topbar_errors(d: dict) -> list[str]:
    t = d.get("desktop", {}).get("topbar", {})
    e = []
    if not isinstance(t.get("volume_scroll"), bool):
        e.append("volume_scroll must be a boolean")
    s = t.get("volume_scroll_step_pct")
    if not isinstance(s, int) or isinstance(s, bool) or not 1 <= s <= 25:
        e.append("volume_scroll_step_pct must be an integer 1..25")
    m = t.get("volume_max_pct")
    if not isinstance(m, int) or isinstance(m, bool) or not 1 <= m <= 150:
        e.append("volume_max_pct must be an integer 1..150")
    o = t.get("volume_osd_ms")
    if not isinstance(o, int) or isinstance(o, bool) or o < 0:
        e.append("volume_osd_ms must be an integer >= 0")
    return e


def misc_errors(d: dict) -> list[str]:
    misc = d.get("desktop", {}).get("hyprland", {}).get("config", {}).get("misc", {})
    e = []
    if misc.get("on_focus_under_fullscreen") != 2:
        e.append("on_focus_under_fullscreen must be 2 (a focused window comes to the front over fullscreen content)")
    bars = d.get("desktop", {}).get("hyprland", {}).get("hyprbars", {}).get("config", {})
    r = bars.get("reveal_radius")
    if not isinstance(r, int) or not 0 <= r <= 12:
        e.append("hyprbars reveal_radius must be a narrow integer 0..12 (less sensitive controls proximity)")
    return e


SURFACES = {  # file -> the main surface's id (or None: the first glass surface filling its window)
    "Search.qml": None,
    "Keymap.qml": "card",
    "SecretPrompt.qml": "modalBox",
}


def surface_block(text: str, ident: str | None) -> str:
    if ident:
        m = re.search(r"(?:Rectangle|GlassSurface)\s*\{\s*id:\s*%s\b" % re.escape(ident), text)
    else:
        m = re.search(r"(?:Rectangle|GlassSurface)\s*\{\s*(?:id:\s*\w+\s*)?anchors\.fill:\s*parent", text)
    if not m:
        return ""
    return text[m.start():m.start() + 700]


def glass_surface_errors(name: str, text: str) -> list[str]:
    b = surface_block(text, SURFACES[name])
    e = []
    if not b:
        return [f"{name}: main surface not found"]
    # the design language: the one glass component (GlassSurface: Glass.corner of its size) or the Glass corner rule
    glass_shape = b.startswith("GlassSurface") and not re.search(r"^\s*radius:", b, re.M)
    if not (glass_shape or "radius: Glass.cornerFor(height)" in b):
        e.append(f"{name}: surface radius must be the design language's (GlassSurface / Glass.cornerFor(height))")
    # GlassSurface's default colour is Glass.fill
    if not re.search(r"color:.*Glass\.(fill|fillHex)", b) and not (b.startswith("GlassSurface") and not re.search(r"^\s*color:", b, re.M)):
        e.append(f"{name}: surface fill must be the shared Glass fill colour")
    if "outlineColor" in b:
        e.append(f"{name}: no hand-drawn outline (the rim is hyprglass' fresnel)")
    return e


class TestSsot(unittest.TestCase):
    def test_topbar_volume_keys(self):
        self.assertEqual(topbar_errors(ssot()), [])

    def test_topbar_negative_controls(self):
        d = ssot()
        for key, bad in (("volume_scroll", "yes"), ("volume_scroll_step_pct", 0), ("volume_max_pct", 400),
                         ("volume_osd_ms", -1)):
            m = copy.deepcopy(d)
            m["desktop"]["topbar"][key] = bad
            self.assertTrue(topbar_errors(m), key)

    def test_window_misc(self):
        self.assertEqual(misc_errors(ssot()), [])
        m = copy.deepcopy(ssot())
        m["desktop"]["hyprland"]["config"]["misc"]["on_focus_under_fullscreen"] = 0
        self.assertTrue(misc_errors(m))
        m = copy.deepcopy(ssot())
        m["desktop"]["hyprland"]["hyprbars"]["config"]["reveal_radius"] = 48
        self.assertTrue(misc_errors(m))

    def test_projections(self):
        lua = read(LUA)
        d = ssot()
        self.assertIn('["on_focus_under_fullscreen"] = 2', lua)
        r = d["desktop"]["hyprland"]["hyprbars"]["config"]["reveal_radius"]
        self.assertIn(f'["reveal_radius"] = {r}', lua)
        html = read(HTML)
        for k in ("volume_scroll", "volume_scroll_step_pct", "volume_max_pct", "volume_osd_ms"):
            self.assertIn(f'data-key="desktop.topbar.{k}"', html, k)


QML_TOOL = os.environ.get("MIOS_QML_TOOL", "/usr/lib64/qt6/bin/qml")
# The commit before the wheel-up clamp fix: its volumeWheel lowers a sink that sits above volume_max_pct.
PRE_CLAMP_FIX = "5b24d4716"
# Negative controls: main as it was before this lane merged (main now contains the fix itself).
PRE_SHELL_MAIN = "c2c7d0f90"
WHEEL_CASES = [  # (label, volume, muted, max, deltas) -> expected (volume, muted, last return)
    ("step up", 0.30, False, 1.0, [120], (0.35, False, 35)),
    ("step down", 0.30, False, 1.0, [-120], (0.25, False, 25)),
    ("clamp at max", 0.98, False, 1.0, [120], (1.0, False, 100)),
    ("floor at zero", 0.02, False, 1.0, [-120], (0.0, False, 0)),
    ("above max: wheel-up keeps the level", 1.30, False, 1.0, [120], (1.30, False, 130)),
    ("above max: wheel-down steps from it", 1.30, False, 1.0, [-120], (1.25, False, 125)),
    ("muted: wheel-up unmutes and steps", 0.30, True, 1.0, [120], (0.35, False, 35)),
    ("muted: wheel-down stays muted", 0.30, True, 1.0, [-120], (0.25, True, 25)),
    ("half notches accumulate", 0.30, False, 1.0, [60, 60], (0.35, False, 35)),
]


def function_text(qml: str, name: str) -> str:
    """The source of `function <name>(...) { ... }` (brace-matched) from a QML file."""
    start = re.search(r"\n\s*function " + re.escape(name) + r"\(deltaY\)\s*\{", qml)
    if not start:
        raise AssertionError(f"function {name} not found")
    i = qml.index("{", start.start())
    depth = 0
    for j in range(i, len(qml)):
        depth += {"{": 1, "}": -1}.get(qml[j], 0)
        if depth == 0:
            return qml[start.start():j + 1].strip()
    raise AssertionError("unbalanced braces")


def run_wheel_cases(qml_source: str) -> dict:
    """Run the island's real volumeWheel() in Qt's own QML engine (offscreen) against a mock sink."""
    fn = function_text(qml_source, "volumeWheel")
    cases = ",".join("[%s,%s,%s,%s,[%s]]" % (json_num(v), str(m).lower(), json_num(mx), json_num(0), ",".join(map(str, d)))
                     for _, v, m, mx, d, _ in WHEEL_CASES)
    doc = """import QtQuick
Item {
    id: pill
    property bool volumeScroll: true
    property real volumeStepPct: 5
    property real volumeMax: 1.0
    property real volumeWheelAcc: 0
    property int osd: 0
    QtObject { id: sinkObj; property int id: 1; property QtObject audio: QtObject { property real volume: 0; property bool muted: false } }
    property var sink: sinkObj
    function showVolumeOsd() { osd++ }
    %s
    Component.onCompleted: {
        const out = []
        for (const c of [%s]) {
            sinkObj.audio.volume = c[0]; sinkObj.audio.muted = c[1]; volumeMax = c[2]; volumeWheelAcc = 0
            let r = -1
            for (const d of c[4]) r = volumeWheel(d)
            out.push([Math.round(sinkObj.audio.volume * 100) / 100, sinkObj.audio.muted, r])
        }
        console.log("RESULT " + JSON.stringify(out))
        Qt.quit()
    }
}
""" % (fn, cases)
    with tempfile.TemporaryDirectory(prefix="mios-wheel-") as d:
        path = os.path.join(d, "wheel.qml")
        with open(path, "w") as f:
            f.write(doc)
        r = subprocess.run([QML_TOOL, path], capture_output=True, text=True, timeout=60,
                           env=dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_FORCE_STDERR_LOGGING="1",
                                    QT_LOGGING_RULES="qml=true;js=true;default.debug=true"))
    line = next((l for l in (r.stdout + r.stderr).splitlines() if "RESULT " in l), None)
    if line is None:
        raise AssertionError("qml run failed: " + (r.stdout + r.stderr)[-800:])
    import json
    got = json.loads(line.split("RESULT ", 1)[1])
    return {label: tuple(g) for (label, *_), g in zip(WHEEL_CASES, got)}


def json_num(x: float) -> str:
    return repr(float(x))


class TestQml(unittest.TestCase):
    @unittest.skipUnless(os.access(QML_TOOL, os.X_OK), "needs Qt's qml tool")
    def test_volume_wheel_behaviour(self):
        """The real volumeWheel() in Qt's QML engine: steps, clamps, floors, mute, accumulation, and wheel-up never
        lowers a sink that another app set above volume_max_pct."""
        got = run_wheel_cases(read(os.path.join(QS, "PanelWindow.qml")))
        for label, *_, want in WHEEL_CASES:
            self.assertEqual(got[label], (want[0], want[1], want[2]), label)

    @unittest.skipUnless(os.access(QML_TOOL, os.X_OK), "needs Qt's qml tool")
    def test_volume_wheel_negative_control_pre_fix(self):
        """The volumeWheel() before the clamp fix drops a 130 % sink to 100 % on wheel-up (the defect)."""
        r = subprocess.run(["git", "-C", ROOT, "show", f"{PRE_CLAMP_FIX}:usr/share/mios/quickshell/PanelWindow.qml"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            self.skipTest("pre-fix commit not reachable from this checkout")
        got = run_wheel_cases(r.stdout)
        self.assertEqual(got["above max: wheel-up keeps the level"], (1.0, False, 100))
        self.assertEqual(got["step up"], (0.35, False, 35))   # the rest of the harness agrees with the old code

    def test_island_scroll_volume(self):
        t = read(os.path.join(QS, "PanelWindow.qml"))
        self.assertRegex(t, r"WheelHandler\s*\{[^}]*pill\.volumeWheel\(event\.angleDelta\.y\)")
        self.assertIn("id: volumeChip", t)
        self.assertIn("function volumeWheel(deltaY: int): int", t)  # IPC
        for k in ("volume_scroll", "volume_scroll_step_pct", "volume_max_pct", "volume_osd_ms"):
            self.assertIn("bar." + k, t)
        # the chip is not gated on the sound item alone (it shows while that item is hidden)
        self.assertRegex(t, r"visible: pill\.volumeOsd && pill\.volumePct >= 0 && \(!soundGlyph\.visible")

    def test_battery_glyphs(self):
        t = read(os.path.join(QS, "PanelWindow.qml"))
        self.assertIn("function batteryLevelIcon(d)", t)
        self.assertIn("function batteryGlyphs(d)", t)
        self.assertIn("text: pill.batteryGlyphs(modelData)", t)
        self.assertIn("text: pill.batteryGlyphs(batteryRow.modelData)", t)

    def test_glass_surfaces(self):
        for name in SURFACES:
            self.assertEqual(glass_surface_errors(name, read(os.path.join(QS, name))), [], name)

    def test_glass_surfaces_negative_control_main(self):
        """main's drawer/search/keymap (theme radius, bg fill, outline) fail the same rule."""
        failing = 0
        for name in SURFACES:
            r = subprocess.run(["git", "-C", ROOT, "show", f"{PRE_SHELL_MAIN}:usr/share/mios/quickshell/{name}"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                self.skipTest("main not reachable from this checkout")
            if glass_surface_errors(name, r.stdout):
                failing += 1
        self.assertEqual(failing, len(SURFACES))

    def test_nested_items_override(self):
        t = read(os.path.join(QS, "Nested.qml"))
        self.assertIn('MIOS_NESTED_TOPBAR_ITEMS', t)
        self.assertIn('"clock,fps,workspaces"', t)  # default unchanged

    def test_nested_app_menu(self):
        """The nested shell carries the desktop's app-menu pills (Config.qml parity), gated by topbarShown."""
        t = read(os.path.join(QS, "Nested.qml"))
        self.assertRegex(t, r"MiOS\.AppMenu \{ screen: modelData; shellState: state; reveal: reveal \}")
        a = read(os.path.join(QS, "AppMenu.qml"))
        self.assertIn('shellState.topbarShown("app_menu")', a)  # hidden unless the item is listed
        self.assertIn("component Pill: GlassSurface", a)      # the pills are the island's glass blobs (one component)
        r = subprocess.run(["git", "-C", ROOT, "show", f"{PRE_SHELL_MAIN}:usr/share/mios/quickshell/Nested.qml"],
                           capture_output=True, text=True)
        if r.returncode == 0:  # negative control: main's nested shell has no app menus
            self.assertNotIn("MiOS.AppMenu", r.stdout)

    def test_secret_prompt_reads_caller_text(self):
        """miosd passes the prompt title/message in the environment; Process (Quickshell.Io) has no env table."""
        t = read(os.path.join(QS, "SecretPrompt.qml"))
        self.assertIn('Quickshell.env("MIOS_SECRET_PROMPT_TITLE")', t)
        self.assertIn('Quickshell.env("MIOS_SECRET_PROMPT_MSG")', t)
        self.assertNotIn("Process.env", t)
        r = subprocess.run(["git", "-C", ROOT, "show", f"{PRE_SHELL_MAIN}:usr/share/mios/quickshell/SecretPrompt.qml"],
                           capture_output=True, text=True)
        if r.returncode == 0:  # negative control: main reads a non-existent Process.env and shows no title
            self.assertIn("Process.env", r.stdout)


class TestQuickshellPatches(unittest.TestCase):
    def test_patch_shape(self):
        p = read(PATCH)
        self.assertIn("src/wayland/hyprland/ipc/connection.cpp", p)
        self.assertIn("scheduleEventReconnect", p)
        self.assertIn("QLocalSocket::ClosingState", p)  # a peer close passes Closing before Unconnected
        self.assertIn("refreshToplevels", p)            # resync after the reconnect

    def test_bake_carries_patch(self):
        b = read(BAKE)
        self.assertIn("hyprland-event-socket-reconnect.patch", b)
        self.assertRegex(b, r"git -C \"\$BUILD_DIR\" apply --verbose \"\$reconnect_patch\"")

    def test_patch_applies_upstream(self):
        src = os.environ.get("MIOS_QUICKSHELL_SRC")
        if not src:
            self.skipTest("MIOS_QUICKSHELL_SRC (an upstream Quickshell checkout) not given")
        with tempfile.TemporaryDirectory() as tmp:
            wt = os.path.join(tmp, "src")
            subprocess.run(["git", "-C", src, "worktree", "add", "-q", "--detach", wt, "HEAD"], check=True)
            try:
                r = subprocess.run(["git", "-C", wt, "apply", "--check", PATCH], capture_output=True, text=True)
                self.assertEqual(r.returncode, 0, r.stderr)
                g = subprocess.run(["python3", "-I", GATE, os.path.join(wt, "src")], capture_output=True, text=True)
                self.assertEqual(g.returncode, 0, g.stdout + g.stderr)
            finally:
                subprocess.run(["git", "-C", src, "worktree", "remove", "--force", wt], check=False)


if __name__ == "__main__":
    unittest.main()
