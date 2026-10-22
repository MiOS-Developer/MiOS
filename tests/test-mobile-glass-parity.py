#!/usr/bin/env python3
# AI-hint: Glass parity of the mobile window-controls tabs (ControlsTab.qml, the shell twin of hyprbars' mobile tabs),
# their buttons, the status pill and the dock with the desktop window controls (hyprbars overlay tab): every colour and radius must come from the SAME SSOT keys the hyprbars
# projection uses ([desktop.hyprland.hyprbars], its hyprglass glass_preset, [desktop.hyprland.config.decoration]
# rounding, [colors]) via Glass.qml -- never a literal. Static scan (positive + negative), a window-less Quickshell
# probe (MIOS_QS_PROBE=1) against a fixture SSOT with distinctive values (positive) and against copies whose
# tab/dot colour or radius was made literal (negative), a tab under the touch target (negative), and a cross-check with the live projection
# (mios-gen render-hyprland pill_color / add_button bg_color). Never touches the live desktop: the probe
# instantiates items without a window.
# AI-related: usr/share/mios/quickshell/Glass.qml, usr/share/mios/quickshell/ControlsTab.qml,
# usr/share/mios/quickshell/ControlDot.qml, usr/share/mios/quickshell/PanelWindow.qml,
# usr/share/mios/quickshell/Sidebar.qml, tools/native/mios-gen/src/render.rs (hyprbars glass_preset)
"""Mobile glass parity tests."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
QS_DIR = os.environ.get("MIOS_QS_DIR") or os.path.join(ROOT, "usr", "share", "mios", "quickshell")

# A literal where an SSOT-derived value belongs: a hex colour, or a colour/fill/radius bound to a number or string.
LITERAL = re.compile(r"#[0-9a-fA-F]{6,8}\b|\b(?:color|fill|fillColor|[a-zA-Z]*[rR]adius|ro|ri|rf)\s*:\s*(?:-?\d|\"(?!transparent\"))")


def _blocks() -> dict[str, str]:
    """The QML that draws the glass row and the dock, by file (only the material parts of the big files)."""
    def read(name: str) -> str:
        with open(os.path.join(QS_DIR, name), encoding="utf-8") as f:
            return f.read()
    out = {n: read(n) for n in ("Glass.qml", "ControlsTab.qml", "ControlDot.qml")}
    panel = read("PanelWindow.qml")
    out["PanelWindow.qml#tabs"] = panel[panel.index("component Tab"):panel.index("Row {\n                id: row")]
    side = read("Sidebar.qml")
    i = side.index("property real fillOpacity")
    out["Sidebar.qml#dock"] = side[i:side.index("focus: dock.expanded", i)]
    return out


# An input property's neutral default (`property real windowRadius: 0`) is not a value.
NEUTRAL_DEFAULT = re.compile(r"^\s*property\s+\w+\s+\w+\s*:\s*0\s*$")


def literal_hits(text: str) -> list[str]:
    hits = []
    for line in text.splitlines():
        code = line.split("//", 1)[0]
        if LITERAL.search(code) and not NEUTRAL_DEFAULT.match(code):
            hits.append(line.strip())
    return hits


class Static(unittest.TestCase):
    def test_positive_no_literal_colour_or_radius(self):
        for name, text in _blocks().items():
            self.assertEqual(literal_hits(text), [], name)

    def test_negative_scanner_flags_literals(self):
        for bad in ('        fill: "#282262"', "    readonly property real ro: 28",
                    '        color: "#DC271B"', "            radius: 14", "    innerRadius: 14"):
            self.assertTrue(literal_hits(bad), bad)
        self.assertFalse(literal_hits("    readonly property color fill: Glass.fill"))
        self.assertFalse(literal_hits("    property real windowRadius: 0       // input"))
        self.assertTrue(literal_hits("    property real windowRadius: 28"))
        self.assertFalse(literal_hits('    color: "transparent"'))   # no material at all is not a copied value
        self.assertTrue(literal_hits('    color: "navy"'))


# Fixture SSOT: values no vendor default has, so a literal anywhere shows up as a mismatch.
FIXTURE = {
    "colors": {"bg": "#101820", "fg": "#f0e0d0", "error": "#123456", "warning": "#223344", "success": "#334455",
               "subtle": "#654321"},
    "theme": {"opacity": 40},
    "desktop.hyprland.config.decoration": {"rounding": 17},
    "desktop.hyprland.hyprbars": {
        "config": {"bar_height": 28, "pill_palette": "bg", "pill_alpha": "theme.opacity", "pill_padding": 7,
                   "bar_button_padding": 5, "icon_on_hover": True, "glass_preset": "fx"},
        "buttons": [{"palette": "warning", "icon_palette": "fg", "size": 12, "icon": "-", "action": "hl.dsp.window.move()"},
                    {"palette": "error", "icon_palette": "fg", "size": 11, "icon": "x", "action": "hl.dsp.window.close()"}],
        "mobile": {"touch_target": 46, "close_button": 1, "menu_palette": "subtle", "menu_icon_palette": "fg", "menu_icon": "m"}},
    "desktop.hyprland.hyprglass": {"config": {"default_preset": "fx", "default_theme": "dark"},
                                   "presets": {"fx": {"dark": {"palette_fresnel": "subtle"}}}},
    "desktop.mobile": {},
}
# tab: drawn 25 x 28 (button 11 + 2 x pill_padding 7, bar_height 28), touch_target 46 x 46, corner min(17, 28) / 2, fillet min(17 / 2, 14)
EXPECT = {"loaded": True, "fill": "#66101820", "rc": 8.5, "rf": 8.5, "tab_w": 25, "tab_h": 28, "touch_w": 46, "touch_h": 46,
          "close": "#123456", "close_size": 11, "menu": "#654321", "menu_size": 11, "dot_x": 8.5 + (25 - 11) / 2, "icon": "#f0e0d0",
          "menu_icon_color": "#f0e0d0", "inset": 17 + 8.5}
TOUCH_MIN = 44   # policy floor for [desktop.hyprland.hyprbars.mobile] touch_target (logical px, finger-sized)


def touch_errors(r: dict, floor: float = TOUCH_MIN) -> list[str]:
    """A mobile tab's hit area must be at least the touch target wide and tall."""
    return [f"{k} {r.get(k)} < {floor}" for k in ("touch_w", "touch_h") if not (isinstance(r.get(k), (int, float)) and r[k] >= floor)]

PROBE = """import QtQuick
import Quickshell as QS
import "." as MiOS
QS.ShellRoot {
    Component { id: panel; MiOS.PanelWindow {} }
    Component { id: dock; MiOS.Sidebar {} }
    Component { id: tab; MiOS.ControlsTab { tabWidth: MiOS.Glass.mobileTabWidth; rowHeight: MiOS.Glass.barHeight; windowRadius: MiOS.Glass.rounding } }
    Component.onCompleted: MiOS.Glass.loaded   // instantiate the singleton now: it reads the SSOT asynchronously
    function fillOf(dot) { for (const c of dot.children) if (c.objectName === "dotFill") return String(c.color); return "" }
    Timer {
        interval: 3000
        running: true
        onTriggered: {
            const close = tab.createObject(null, { button: MiOS.Glass.closeButton })
            const menu = tab.createObject(null, { button: MiOS.Glass.menuButton })
            console.warn("PROBE " + JSON.stringify({ panel: panel.status === Component.Ready, dock: dock.status === Component.Ready,
                loaded: MiOS.Glass.loaded, fill: String(close.fill), rc: close.rc, rf: close.rf, tab_w: close.box.width,
                tab_h: close.box.height, touch_w: Math.max(close.box.width, MiOS.Glass.touchTarget),
                touch_h: Math.max(close.box.height, MiOS.Glass.touchTarget), close: fillOf(close.dot), close_size: close.dot.width,
                menu: fillOf(menu.dot), menu_size: menu.dot.width, dot_x: menu.dot.x, icon: String(MiOS.Glass.closeButton.iconColor),
                menu_icon_color: String(MiOS.Glass.menuButton.iconColor), rounding: MiOS.Glass.rounding,
                inset: MiOS.Glass.mobileTabInset(MiOS.Glass.barHeight, MiOS.Glass.rounding) }))
            Qt.quit()
        }
    }
}
"""

STUB = """#!/bin/sh
[ "$1" = --section ] || exit 2
cat "$(dirname "$0")/fix/$2.json" 2>/dev/null || exit 1
"""


def probe(qs_dir: str, fixture: dict | None, mutate=None) -> dict:
    with tempfile.TemporaryDirectory() as d:
        qs = os.path.join(d, "qs")
        shutil.copytree(qs_dir, qs)
        if mutate:
            mutate(qs)
        with open(os.path.join(qs, "GlassProbe.qml"), "w") as f:
            f.write(PROBE)
        env = {**os.environ, "MIOS_MOBILE_OUTPUTS_JSON": os.path.join(d, "none.json")}
        if fixture is not None:
            os.makedirs(os.path.join(d, "fix"))
            for sec, val in fixture.items():
                with open(os.path.join(d, "fix", sec + ".json"), "w") as f:
                    json.dump(val, f)
            stub = os.path.join(d, "toml-get")
            with open(stub, "w") as f:
                f.write(STUB)
            os.chmod(stub, 0o755)
            env["MIOS_TOML_GET"] = stub
        out = subprocess.run(["quickshell", "--path", os.path.join(qs, "GlassProbe.qml")], capture_output=True,
                             text=True, timeout=60, env=env, cwd=d)
    line = next((l for l in (out.stdout + out.stderr).splitlines() if "PROBE " in l), None)
    if line is None:
        raise AssertionError("probe produced no result:\n" + (out.stdout + out.stderr)[-2000:])
    return json.loads(line.split("PROBE ", 1)[1])


def mismatches(got: dict, expect: dict) -> list[str]:
    bad = []
    for k, v in expect.items():
        g = got.get(k)
        if isinstance(v, bool):
            ok = g is v
        elif isinstance(v, str):
            ok = isinstance(g, str) and g.lower() == v.lower()
        else:
            ok = isinstance(g, (int, float)) and abs(g - v) < 1e-6
        if not ok:
            bad.append(f"{k}: {g!r} != {v!r}")
    return bad


def _sub(name: str, old: str, new: str):
    def go(qs: str):
        p = os.path.join(qs, name)
        with open(p, encoding="utf-8") as f:
            s = f.read()
        assert old in s, (name, old)
        with open(p, "w", encoding="utf-8") as f:
            f.write(s.replace(old, new, 1))
    return go


@unittest.skipUnless(os.environ.get("MIOS_QS_PROBE") == "1", "window-less Quickshell probe (MIOS_QS_PROBE=1)")
class Probe(unittest.TestCase):
    def test_positive_values_follow_the_fixture_ssot(self):
        r = probe(QS_DIR, FIXTURE)
        self.assertTrue(r["panel"] and r["dock"], r)
        self.assertEqual(mismatches(r, EXPECT), [], r)

    def test_positive_tab_is_touch_sized(self):
        r = probe(QS_DIR, FIXTURE)
        self.assertEqual(touch_errors(r), [], r)

    def test_negative_tab_under_touch_target_is_caught(self):
        fx = json.loads(json.dumps(FIXTURE))
        fx["desktop.hyprland.hyprbars"]["mobile"]["touch_target"] = 30
        r = probe(QS_DIR, fx)
        self.assertTrue(touch_errors(r), r)   # 30 (> button 11 + 2 x 7) is narrower than the 44 px finger target

    def test_negative_literal_tab_width_is_caught(self):
        r = probe(QS_DIR, FIXTURE, _sub("Glass.qml", "closeButton.size + 2 * pillPadding", "32"))
        self.assertIn("tab_w", " ".join(mismatches(r, EXPECT)))

    def test_negative_literal_tab_colour_is_caught(self):
        r = probe(QS_DIR, FIXTURE, _sub("ControlsTab.qml", "readonly property color fill: Glass.fill",
                                        'readonly property color fill: "#80282262"'))
        self.assertIn("fill", " ".join(mismatches(r, EXPECT)))

    def test_negative_literal_dot_colour_is_caught(self):
        r = probe(QS_DIR, FIXTURE, _sub("ControlDot.qml", 'color: dot.button.color || "transparent"', 'color: "#DC271B"'))
        bad = " ".join(mismatches(r, EXPECT))
        self.assertIn("close:", bad)
        self.assertIn("menu:", bad)

    def test_negative_literal_radius_is_caught(self):
        r = probe(QS_DIR, FIXTURE, _sub("ControlsTab.qml", "Glass.tabCorner(rowHeight, windowRadius)", "14"))
        self.assertIn("rc:", " ".join(mismatches(r, EXPECT)))

    def test_negative_unreadable_ssot_is_not_reported_as_loaded(self):
        r = probe(QS_DIR, {})
        self.assertFalse(r["loaded"])
        self.assertTrue(mismatches(r, EXPECT))


def _projection() -> str | None:
    gen = os.environ.get("MIOS_GEN") or "/usr/bin/mios-gen"
    if not os.access(gen, os.X_OK):
        return None
    out = subprocess.run([gen, "render-hyprland", "--root", "/", "--runtime"], capture_output=True, text=True, timeout=60,
                         cwd=tempfile.gettempdir())
    return out.stdout if out.returncode == 0 else None


@unittest.skipUnless(os.environ.get("MIOS_QS_PROBE") == "1", "window-less Quickshell probe (MIOS_QS_PROBE=1)")
class LiveProjection(unittest.TestCase):
    """Glass.qml on the real layered SSOT resolves to what mios-gen projected into hyprbars."""

    @classmethod
    def setUpClass(cls):
        cls.lua = _projection()
        if not cls.lua or not os.access("/usr/bin/mios-toml-get", os.X_OK):
            raise unittest.SkipTest("needs mios-gen and mios-toml-get")

    def expected(self) -> dict:
        pill = re.search(r'\["pill_color"\] = "rgba\(([0-9A-Fa-f]{6})([0-9A-Fa-f]{2})\)"', self.lua)
        close = re.search(r'add_button\(\{\["bg_color"\] = "rgb\(([0-9A-Fa-f]{6})\)", \["fg_color"\] = "rgb\(([0-9A-Fa-f]{6})\)", '
                          r'\["size"\] = (\d+), \["icon"\] = "[^"]*", \["action"\] = "[^"]*window\.close', self.lua)
        rounding = re.search(r'\["rounding"\] = (\d+)', self.lua)
        self.assertTrue(pill and close and rounding, "projection lacks pill_color / close button / rounding")
        return {"loaded": True, "fill": f"#{pill.group(2)}{pill.group(1)}", "close": f"#{close.group(1)}",
                "icon": f"#{close.group(2)}", "close_size": int(close.group(3)), "rounding": int(rounding.group(1))}

    def test_positive_matches_projection(self):
        r = probe(QS_DIR, None)
        self.assertEqual(mismatches(r, self.expected()), [], r)

    def test_negative_fixture_does_not_match_projection(self):
        r = probe(QS_DIR, FIXTURE)
        self.assertTrue(mismatches(r, self.expected()))


REMOTE_PROBE = """import QtQuick
import Quickshell as QS
import "." as MiOS
QS.ShellRoot {
    // the namespace must be right on the FIRST read (no wait for a poll): read it at completion, quit later
    property string result: ""
    Component.onCompleted: {
        result = JSON.stringify({
            phone: MiOS.Mobile.layerNamespace("mios-topbar", "HEADLESS-MIOS-RDP"),
            dock: MiOS.Mobile.layerNamespace("mios-dock", "HEADLESS-MIOS-RDP"),
            desk: MiOS.Mobile.layerNamespace("mios-topbar", "HDMI-A-1"),
            unknown: MiOS.Mobile.layerNamespace("mios-topbar", "NOPE-1"),
            profile: MiOS.Glass.profileFor("HEADLESS-MIOS-RDP"), desk_profile: MiOS.Glass.profileFor("HDMI-A-1") })
    }
    Timer { interval: 300; running: true; onTriggered: { console.warn("PROBE " + result); Qt.quit() } }
}
"""


def remote_probe(remote_effects) -> dict:
    cfg = {"enabled": True}
    if remote_effects is not None:
        cfg["remote_effects"] = remote_effects
    state = {"version": 1, "config": cfg, "outputs": {
        "HDMI-A-1": {"mobile": False, "origin": "local"}, "HEADLESS-MIOS-RDP": {"mobile": True, "origin": "remote"}}}
    with tempfile.TemporaryDirectory() as d:
        qs = os.path.join(d, "qs")
        shutil.copytree(QS_DIR, qs)
        with open(os.path.join(qs, "RemoteProbe.qml"), "w") as f:
            f.write(REMOTE_PROBE)
        with open(os.path.join(d, "state.json"), "w") as f:
            json.dump(state, f)
        out = subprocess.run(["quickshell", "--path", os.path.join(qs, "RemoteProbe.qml")], capture_output=True, text=True,
                             timeout=60, cwd=d, env={**os.environ, "MIOS_MOBILE_OUTPUTS_JSON": os.path.join(d, "state.json"),
                                                     "MIOS_TOML_GET": "/bin/false"})
    line = next((l for l in (out.stdout + out.stderr).splitlines() if "PROBE " in l), None)
    if line is None:
        raise AssertionError("probe produced no result:\n" + (out.stdout + out.stderr)[-2000:])
    return json.loads(line.split("PROBE ", 1)[1])


@unittest.skipUnless(os.environ.get("MIOS_QS_PROBE") == "1", "window-less Quickshell probe (MIOS_QS_PROBE=1)")
class RemoteEffects(unittest.TestCase):
    """[desktop.mobile.remote_effects]: remote outputs' shell layers take the -remote namespace (cheaper preset),
    known synchronously at surface creation (the namespace cannot change later)."""

    RE = {"enabled": True, "applies_to": ["remote"], "preset": "mios-remote", "base_preset": "mios"}

    def test_positive_remote_output_gets_remote_namespace(self):
        r = remote_probe(self.RE)
        self.assertEqual(r, {"phone": "mios-topbar-remote", "dock": "mios-dock-remote", "desk": "mios-topbar",
                             "unknown": "mios-topbar", "profile": "lite", "desk_profile": "full"})

    def test_negative_disabled_absent_or_local_only_keep_the_base_namespace(self):
        for re_cfg in (None, {**self.RE, "enabled": False}, {**self.RE, "applies_to": ["local"]}):
            r = remote_probe(re_cfg)
            self.assertEqual((r["phone"], r["dock"], r["profile"]), ("mios-topbar", "mios-dock", "full"), re_cfg)
        r = remote_probe({**self.RE, "applies_to": ["local"]})
        self.assertEqual(r["desk"], "mios-topbar-remote")   # applies_to is honoured both ways


if __name__ == "__main__":
    unittest.main()
