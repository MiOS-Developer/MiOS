#!/usr/bin/env python3
# AI-hint: Mobile layout profile ([desktop.mobile]) checks that never touch the live desktop: SSOT keys,
# the projection (mios-gen render-hyprland, run under a Lua stub through an RDP attach -> phone resize ->
# detach), the shell contract, qmllint, and (MIOS_QS_PROBE=1) a window-less Quickshell probe of
# Mobile.qml. Positive and negative controls in every class.
# AI-related: usr/share/mios/mios.toml [desktop.mobile], tools/native/mios-gen/src/render.rs (mobile_profile),
# usr/share/mios/quickshell/Mobile.qml, usr/share/mios/quickshell/PanelWindow.qml, usr/share/mios/quickshell/Sidebar.qml
"""Mobile layout profile tests."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SSOT = os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
QS_DIR = os.environ.get("MIOS_QS_DIR") or os.path.join(ROOT, "usr", "share", "mios", "quickshell")
RENDER = os.path.join(ROOT, "tools", "native", "mios-gen", "src", "render.rs")
STATE_FILE = "mios-mobile-outputs.json"
BEGIN, END = "-- MiOS mobile layout profile ([desktop.mobile])", "-- end MiOS mobile layout profile"

KEYS = {
    "enabled", "portrait", "breakpoint_px", "applies_to", "remote_name_prefixes", "layout", "gaps_in_px",
    "gaps_out_px", "hyprbars_tab", "notch_gap_px",
    "shoulder_dot_px", "app_menu", "window_controls", "remote_effects",
    "island_motion", "island_hold",   # the status island's motion between the window tabs (PanelWindow.qml)
}
CONNECTOR = re.compile(r"\b(?:HDMI|DP|eDP|DVI|VGA|WAYLAND|X11)-[A-Z]?-?\d|HEADLESS-[A-Z0-9]")


def mobile_table() -> dict:
    with open(SSOT, "rb") as f:
        return tomllib.load(f)["desktop"]["mobile"]


def ssot_errors(m: dict) -> list[str]:
    errors = []
    if set(m) != KEYS:
        errors.append(f"keys differ: {sorted(set(m) ^ KEYS)}")
    if not isinstance(m.get("breakpoint_px"), int) or m.get("breakpoint_px", 0) <= 0:
        errors.append("breakpoint_px must be a positive integer")
    if not m.get("applies_to") or set(m.get("applies_to", [])) - {"local", "remote"}:
        errors.append("applies_to must list local and/or remote")
    for p in m.get("remote_name_prefixes", []):
        if CONNECTOR.search(p) or not re.fullmatch(r"[A-Za-z0-9_-]+", p):
            errors.append(f"remote_name_prefixes entry {p!r} names an output")
    for c in m.get("window_controls", []):
        if "{address}" not in c.get("action", ""):
            errors.append(f"window control {c.get('label')!r} does not target {{address}}")
    if not isinstance(m.get("island_motion"), str) or not m.get("island_motion"):
        errors.append("island_motion must name a [desktop.hyprland] animations leaf")
    hold = m.get("island_hold")
    if not isinstance(hold, list) or not all(isinstance(h, str) and h for h in hold):
        errors.append("island_hold must list [desktop.hyprland] animations leaves")
    return errors


class Ssot(unittest.TestCase):
    def test_positive_vendor_table(self):
        m = mobile_table()
        self.assertEqual(ssot_errors(m), [])
        self.assertEqual(m["breakpoint_px"], 900)
        self.assertEqual(sorted(m["applies_to"]), ["local", "remote"])
        self.assertTrue(m["enabled"] and m["portrait"])

    def test_negative_output_name_and_bad_rule_rejected(self):
        m = dict(mobile_table())
        m["remote_name_prefixes"] = ["HEADLESS-MIOS-RDP"]
        m["applies_to"] = ["phone"]
        m["window_controls"] = [{"label": "Close", "icon": "x", "action": "hyprctl dispatch 'hl.dsp.window.close()'"}]
        errs = ssot_errors(m)
        self.assertEqual(len(errs), 3, errs)


class Contract(unittest.TestCase):
    """The projection and the shell agree on the hand-off and on the hook style."""

    def test_positive_shared_state_file_and_monitor_hooks(self):
        with open(RENDER, encoding="utf-8") as f:
            render = f.read()
        with open(os.path.join(QS_DIR, "Mobile.qml"), encoding="utf-8") as f:
            mobile = f.read()
        self.assertIn(STATE_FILE, render)
        self.assertIn(STATE_FILE, mobile)
        for ev in ("monitor.added", "monitor.removed", "monitor.layout_changed"):
            self.assertIn(f'hl.on("{ev}"', render)
        for name in ("PanelWindow.qml", "Sidebar.qml"):
            with open(os.path.join(QS_DIR, name), encoding="utf-8") as f:
                self.assertIn("Mobile.isMobile(screen.name)", f.read(), name)

    def test_negative_no_live_injection_or_output_names(self):
        with open(RENDER, encoding="utf-8") as f:
            render = f.read()
        body = render[render.index("const MOBILE_LUA"):render.index("pub fn mobile_profile")]
        self.assertNotIn("hyprctl", body)
        self.assertIsNone(CONNECTOR.search(body.replace("HEADLESS-MIOS-RDP", "")), "output name in the Lua")


HARNESS = r"""
local ws, win, hooks = {}, {}, {}
local MONS = {{name = "HDMI-A-1", width = 1920, height = 1080, scale = 1, transform = 0}}
hl = {
 get_monitors = function() local t = {} for i, m in ipairs(MONS) do t[i] = m end return t end,
 workspace_rule = function(s) s.enabled = true table.insert(ws, s) return {set_enabled = function(_, v) s.enabled = v end} end,
 window_rule = function(s) s.enabled = true table.insert(win, s) return {set_enabled = function(_, v) s.enabled = v end} end,
 on = function(ev, fn) hooks[ev] = fn end,
}
dofile(os.getenv("CHUNK"))
local function report(tag)
 local out = {}
 for _, s in ipairs(ws) do out[#out + 1] = s.workspace .. "=" .. tostring(s.enabled) end
 local f = io.open(os.getenv("XDG_RUNTIME_DIR") .. "/mios-mobile-outputs.json")
 local j = f and f:read("*a") or "{}"
 if f then f:close() end
 print(tag .. "\t" .. table.concat(out, ",") .. "\t" .. (j:gsub("\n", "")))
end
report("boot")
local rdp = {name = "HEADLESS-MIOS-RDP", width = 1280, height = 720, scale = 1, transform = 0}
table.insert(MONS, rdp) hooks["monitor.added"](rdp)
report("attach")
rdp.width, rdp.height = 1100, 2080 hooks["monitor.layout_changed"]()
report("phone")
table.remove(MONS) hooks["monitor.removed"]()
report("detach")
"""


def _mios_gen() -> str | None:
    for c in (os.environ.get("MIOS_GEN"),
              "/var/lib/mios-build/tmp/mobile-static-target/x86_64-unknown-linux-musl/release/mios-gen",
              "/usr/bin/mios-gen"):
        if c and os.access(c, os.X_OK):
            out = subprocess.run([c, "render-hyprland", "--root", ROOT, "--runtime"], capture_output=True, text=True, timeout=60)
            if out.returncode == 0 and BEGIN in out.stdout:
                return out.stdout
    return None


class Projection(unittest.TestCase):
    """1100x2080 (the operator's phone over RDP) gets the mobile rules; 1920x1080 never does."""

    @classmethod
    def setUpClass(cls):
        cls.full = _mios_gen()
        cls.lua = shutil.which("luajit") or shutil.which("lua")
        if not cls.full or not cls.lua:
            raise unittest.SkipTest("needs a mios-gen with the mobile profile (MIOS_GEN) and luajit/lua")

    def run_chunk(self) -> dict[str, tuple[str, dict]]:
        chunk = self.full[self.full.index(BEGIN):self.full.index(END) + len(END)] + "\n"
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "chunk.lua"), "w") as f:
                f.write(chunk)
            with open(os.path.join(d, "h.lua"), "w") as f:
                f.write(HARNESS)
            out = subprocess.run([self.lua, os.path.join(d, "h.lua")], capture_output=True, text=True, timeout=30,
                                 env={**os.environ, "CHUNK": os.path.join(d, "chunk.lua"), "XDG_RUNTIME_DIR": d})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertNotIn("mobile", out.stderr)
        res = {}
        for line in out.stdout.splitlines():
            tag, rules, js = line.split("\t")
            res[tag] = (rules, json.loads(js))
        return res

    def test_positive_phone_output_gets_mobile_rules(self):
        r = self.run_chunk()
        rules, js = r["phone"]
        self.assertIn("m[HEADLESS-MIOS-RDP]=true", rules)
        self.assertTrue(js["outputs"]["HEADLESS-MIOS-RDP"]["mobile"])
        self.assertEqual(js["outputs"]["HEADLESS-MIOS-RDP"]["origin"], "remote")
        self.assertEqual(js["config"]["breakpoint_px"], 900)
        self.assertIn("m[HEADLESS-MIOS-RDP]=false", r["detach"][0])

    def test_negative_desktop_output_gets_nothing(self):
        r = self.run_chunk()
        for tag in ("boot", "attach", "phone", "detach"):
            self.assertNotIn("HDMI-A-1", r[tag][0], tag)
            self.assertFalse(r[tag][1]["outputs"]["HDMI-A-1"]["mobile"], tag)
        self.assertEqual(r["attach"][0], "")  # 1280x720 landscape remote: not phone-sized


class Qml(unittest.TestCase):
    def test_qmllint_parses(self):
        lint = shutil.which("qmllint") or "/usr/lib64/qt6/bin/qmllint"
        if not os.access(lint, os.X_OK):
            self.skipTest("qmllint missing")
        for name in ("Mobile.qml", "PanelWindow.qml", "Sidebar.qml"):
            out = subprocess.run([lint, "-I", "/usr/lib64/qt6/qml", name], cwd=QS_DIR, capture_output=True, text=True, timeout=120)
            self.assertNotIn("[syntax]", out.stdout + out.stderr, name)
            self.assertNotIn("Error:", out.stdout + out.stderr, name)

    def test_negative_qmllint_catches_syntax_error(self):
        lint = shutil.which("qmllint") or "/usr/lib64/qt6/bin/qmllint"
        if not os.access(lint, os.X_OK):
            self.skipTest("qmllint missing")
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "Bad.qml"), "w") as f:
                f.write("import QtQuick\nItem { property bool x: ( }\n")
            out = subprocess.run([lint, "Bad.qml"], cwd=d, capture_output=True, text=True, timeout=60)
            self.assertIn("[syntax]", out.stdout + out.stderr)


PROBE = """import QtQuick
import Quickshell as QS
import "." as MiOS
QS.ShellRoot {
    Component { id: panel; MiOS.PanelWindow {} }
    Component { id: dock; MiOS.Sidebar {} }
    Component.onCompleted: MiOS.Mobile.outputs
    Timer {
        interval: 2500
        running: true
        onTriggered: {
            console.warn("PROBE " + JSON.stringify({ panel: panel.status === Component.Ready, dock: dock.status === Component.Ready,
                phone: MiOS.Mobile.isMobile("HEADLESS-MIOS-RDP"), desk: MiOS.Mobile.isMobile("HDMI-A-1"),
                controls: (MiOS.Mobile.config.window_controls || []).length }))
            Qt.quit()
        }
    }
}
"""


@unittest.skipUnless(os.environ.get("MIOS_QS_PROBE") == "1", "window-less Quickshell probe (MIOS_QS_PROBE=1)")
class QuickshellProbe(unittest.TestCase):
    """Compiles the surfaces without instantiating any (no window on any output)."""

    def probe(self, fixture: str | None) -> dict:
        with tempfile.TemporaryDirectory() as d:
            qs = os.path.join(d, "qs")
            shutil.copytree(QS_DIR, qs)
            with open(os.path.join(qs, "MobileProbe.qml"), "w") as f:
                f.write(PROBE)
            state = os.path.join(d, "state.json")
            if fixture is not None:
                with open(state, "w") as f:
                    f.write(fixture)
            out = subprocess.run(["quickshell", "--path", os.path.join(qs, "MobileProbe.qml")], capture_output=True,
                                 text=True, timeout=60, env={**os.environ, "MIOS_MOBILE_OUTPUTS_JSON": state})
        line = next(l for l in (out.stdout + out.stderr).splitlines() if "PROBE " in l)
        return json.loads(line.split("PROBE ", 1)[1])

    def test_positive_fixture_verdict(self):
        fixture = json.dumps({"version": 1, "config": mobile_table(), "outputs": {
            "HDMI-A-1": {"mobile": False, "origin": "local", "logical_width": 1920, "logical_height": 1080},
            "HEADLESS-MIOS-RDP": {"mobile": True, "origin": "remote", "logical_width": 550, "logical_height": 1040}}})
        r = self.probe(fixture)
        self.assertEqual(r, {"panel": True, "dock": True, "phone": True, "desk": False, "controls": 3})

    def test_negative_missing_and_malformed_files_mean_desktop(self):
        for fixture in (None, "{not json", json.dumps({"config": {"enabled": False}, "outputs": {
                "HEADLESS-MIOS-RDP": {"mobile": True}}})):
            r = self.probe(fixture)
            self.assertFalse(r["phone"], fixture)
            self.assertFalse(r["desk"], fixture)
            self.assertTrue(r["panel"] and r["dock"], fixture)


if __name__ == "__main__":
    unittest.main()
