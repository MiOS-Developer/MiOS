#!/usr/bin/env python3
# AI-hint: "ALL windows are glass" (operator 2026-10-16): every window -- Flatpak, RPM, VM viewers, dialogs, modals,
# Xwayland, floating or tiled -- carries hyprglass' edge light (bevel ring, fresnel rim, top specular) OVER its own
# content, and the hyprbars controls tab + its buttons are drawn BY hyprglass with the window's preset (one glass).
#   1. source/SSOT (always): the MiOS hyprglass patch adds the edge decoration for every window with no class, opacity
#      or float/tile filter and gates it only on the pane's enable rules; the SSOT turns it on; no window rule or tag
#      selects another preset; the hyprbars tab is pointed at hyprglass (glass_preset + button_glass) and its colour
#      layer is the shell surfaces' fill. Negative controls: mutated copies (a class filter, an opacity skip) fail.
#   2. built plugins (default whenever a hyprglass.so / hyprbars.so exists; MIOS_HYPRGLASS_SO / MIOS_HYPRBARS_SO pick a
#      staged build): the .so registers the MiOS keys and exports the shaped-glass call hyprbars resolves.
#   3. pixels (MIOS_GLASS_NEST_SIG + MIOS_GLASS_NEST_WL: a nested test compositor running the patched plugins and its
#      OWN test windows -- never the live desktop): every visible window's top/side edge band brightens with
#      edge_pass on vs 0, its interior does not, and a window tagged hyprglass_disabled loses it (the check catches
#      an excluded window).
# AI-related: usr/share/mios/hyprland/patches/hyprglass-mios-glass.patch, usr/share/mios/hyprland/patches/hyprbars-overlay.patch,
# usr/share/mios/mios.toml [desktop.hyprland.hyprglass] [desktop.hyprland.hyprbars] [desktop.mobile.remote_effects],
# tools/native/mios-gen/src/render.rs (HYPRGLASS_MIOS_KEYS, HYPRBARS_MIOS_KEYS)
"""Edge pass / unified glass tests."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import tomllib
import unittest

ROOT = os.environ.get("MIOS_TEST_ROOT") or os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
PATCHES = os.path.join(ROOT, "usr", "share", "mios", "hyprland", "patches")
SSOT = os.environ.get("MIOS_VENDOR_TOML") or os.path.join(ROOT, "usr", "share", "mios", "mios.toml")


def read(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def added(patch: str, path: str) -> str:
    """The '+' side of one file in a unified diff (the code the patch adds)."""
    out, on = [], False
    for line in patch.splitlines():
        if line.startswith("diff --git "):
            on = line.endswith(" b/" + path)
        elif on and line.startswith("+") and not line.startswith("+++"):
            out.append(line[1:])
    return "\n".join(out)


def edge_violations(patch: str) -> list[str]:
    """Why the edge pass would skip some windows (empty = every window gets it)."""
    errors = []
    main = added(patch, "src/main.cpp")
    glass = added(patch, "src/MiosGlass.cpp")
    shader = added(patch, "src/Shaders.hpp")
    if "makeUnique<CGlassEdgeDecoration>(window)" not in main:
        errors.append("onNewWindow does not add CGlassEdgeDecoration")
    hook = main[main.find("MiOS: the edge pass"):main.find("makeUnique<CGlassEdgeDecoration>")]
    if re.search(r"m_class|initialClass|m_title|opaque\(\)|m_isFloating|m_isX11|isModal|getClass", hook):
        errors.append("the edge decoration is filtered by window class/title/opacity/state")
    draw = glass[glass.find("void CGlassEdgeDecoration::draw"):glass.find("void CGlassEdgeDecoration::renderPass")]
    if "edgeEnabled()" not in draw:
        errors.append("draw() does not gate on the pane's enable rules (edgeEnabled)")
    if re.search(r"opaque\(\)|m_class|isGlassEnabled\(\)|m_isFloating|m_isX11", draw):
        errors.append("draw() skips windows by opacity/class/state")
    if "DECORATION_LAYER_OVER" not in glass:
        errors.append("the edge pass is not drawn over the window's content")
    if "edgePass == 1" not in shader or "fragColor = vec4(rgb, a) * k;" not in shader:
        errors.append("shader has no edge-only output")
    enabled = added(patch, "src/GlassDecoration.hpp")
    if "resolveEnabled() != EEnabledResolution::Disabled" not in enabled:
        errors.append("edgeEnabled() must ignore the opaque skip")
    return errors


def ssot_violations(data: dict) -> list[str]:
    errors = []
    h = data["desktop"]["hyprland"]
    g = h["hyprglass"]
    cfg = g.get("config", {})
    if not any(p.endswith("hyprglass-mios-glass.patch") for p in g.get("patches", [])):
        errors.append("hyprglass is built without hyprglass-mios-glass.patch")
    if not (isinstance(cfg.get("edge_pass"), (int, float)) and cfg["edge_pass"] > 0):
        errors.append("[desktop.hyprland.hyprglass.config] edge_pass is off")
    if not (isinstance(cfg.get("edge_pass_width"), (int, float)) and cfg["edge_pass_width"] >= 0):
        errors.append("edge_pass_width missing")
    if cfg.get("skip_opaque_windows") is not False:
        errors.append("skip_opaque_windows must be false (opaque windows keep their glass pane)")
    fx = data.get("desktop", {}).get("mobile", {}).get("remote_effects", {})
    if fx.get("enabled") is not False:
        errors.append("remote_effects gives remote windows another glass")
    blob = json.dumps(data)  # window rules, mobile/remote tables, anything that could tag a window
    if re.search(r"hyprglass_(?:preset_|theme_|disabled)", blob):
        errors.append("a rule or tag selects another glass preset/theme or disables glass for some windows")
    bars = h["hyprbars"]["config"]
    if bars.get("glass_preset") != g.get("config", {}).get("default_preset"):
        errors.append("the controls tab is not the windows' preset")
    if not (isinstance(bars.get("button_glass"), (int, float)) and bars["button_glass"] > 0):
        errors.append("hyprbars buttons are flat dots (button_glass)")
    if bars.get("pill_alpha") != "theme.shell.fill_opacity_pct":
        errors.append("the tab's colour layer is not the shell surfaces' fill ([theme.shell] fill_opacity_pct)")
    return errors


class SourceAndSsot(unittest.TestCase):
    def setUp(self):
        self.patch = read(os.path.join(PATCHES, "hyprglass-mios-glass.patch"))
        with open(SSOT, "rb") as f:
            self.data = tomllib.load(f)

    def test_every_window_gets_the_edge_pass(self):
        self.assertEqual(edge_violations(self.patch), [])

    def test_negative_controls_class_filter_and_opacity_skip_are_caught(self):
        filtered = self.patch.replace("+    // MiOS: the edge pass, drawn over the window's content (MiosGlass.hpp)\n",
                                      "+    // MiOS: the edge pass, drawn over the window's content (MiosGlass.hpp)\n+    if (window->m_class == \"spotify\") return;\n", 1)
        self.assertNotEqual(filtered, self.patch)
        self.assertTrue(edge_violations(filtered))
        skipping = self.patch.replace("+    if (!glass || !glass->edgeEnabled())", "+    if (!glass || !glass->edgeEnabled() || window->opaque())", 1)
        self.assertNotEqual(skipping, self.patch)
        self.assertTrue(edge_violations(skipping))
        nogate = self.patch.replace("glass->edgeEnabled()", "glass->isGlassEnabled()")
        self.assertTrue(edge_violations(nogate))

    def test_ssot_turns_it_on_with_one_glass(self):
        self.assertEqual(ssot_violations(self.data), [])

    def test_ssot_negative_controls(self):
        for mutate in (
            lambda d: d["desktop"]["hyprland"]["hyprglass"]["config"].__setitem__("edge_pass", 0),
            lambda d: d["desktop"]["mobile"]["remote_effects"].__setitem__("enabled", True),
            lambda d: d["desktop"]["hyprland"]["hyprbars"]["config"].__setitem__("button_glass", 0),
            lambda d: d["desktop"]["hyprland"]["hyprbars"]["config"].__setitem__("pill_alpha", "theme.opacity"),
            lambda d: d["desktop"]["hyprland"]["hyprglass"].__setitem__("patches", []),
            lambda d: d["desktop"]["hyprland"]["window_rules"].append({"match": {"class": "^(spotify)$"}, "tag": "+hyprglass_preset_flat"}),
        ):
            d = json.loads(json.dumps(self.data))
            mutate(d)
            self.assertTrue(ssot_violations(d))

    def test_hyprbars_draws_the_tab_through_hyprglass(self):
        bars = read(os.path.join(PATCHES, "hyprbars-overlay.patch"))
        code = added(bars, "hyprbars/barDeco.cpp")
        for needle in ("mios_hyprglass_draw_shape_v1", "RTLD_NOLOAD", "s.shape       = 1", "drawGlassButton(", "hyprglassRelease("):
            self.assertIn(needle, code)
        # the struct both sides compile must match field for field
        hg = added(self.patch, "src/MiosGlass.hpp")
        fields = lambda text: re.findall(r"^\s+(?:uint32_t|void\*|uintptr_t|float|int32_t|int64_t)\s+(\w+)(?:\[\d\])?;", text, re.M)
        hb_struct = code[code.find("struct SMiosGlassShapeV1"):code.find("};", code.find("struct SMiosGlassShapeV1"))]
        hg_struct = hg[hg.find("struct SMiosGlassShapeV1"):hg.find("};", hg.find("struct SMiosGlassShapeV1"))]
        self.assertTrue(fields(hg_struct))
        self.assertEqual(fields(hb_struct), fields(hg_struct))


def plugin(name: str) -> str | None:
    env = os.environ.get(f"MIOS_{name.upper()}_SO")
    if env:
        return env
    p = f"/usr/lib64/mios/{name}.so"
    return p if os.path.isfile(p) else None


class BuiltPlugins(unittest.TestCase):
    """Runs by default wherever the plugins are installed (or MIOS_HYPRGLASS_SO / MIOS_HYPRBARS_SO name a build)."""

    def strings(self, path: str) -> str:
        with open(path, "rb") as f:
            return f.read().decode("latin-1")

    def test_hyprglass_registers_the_edge_pass_and_exports_the_shape_call(self):
        path = plugin("hyprglass")
        if not path:
            self.skipTest("no hyprglass.so built or installed")
        blob = self.strings(path)
        for needle in ("plugin:hyprglass:edge_pass", "plugin:hyprglass:edge_pass_width", "mios_hyprglass_draw_shape_v1", "HyprGlassEdge", "plugin:hyprglass:fade_gamma"):
            self.assertTrue(needle in blob, f"{path} lacks {needle}: not built from the MiOS hyprglass patches")

    def test_hyprbars_resolves_hyprglass(self):
        path = plugin("hyprbars")
        if not path:
            self.skipTest("no hyprbars.so built or installed")
        blob = self.strings(path)
        for needle in ("plugin:hyprbars:glass_plugin", "plugin:hyprbars:button_glass", "mios_hyprglass_draw_shape_v1", "hyprbars:mobile"):
            self.assertTrue(needle in blob, f"{path} lacks {needle}: not built from the MiOS hyprbars patch")


def lum(p) -> float:
    return 0.2126 * p[0] + 0.7152 * p[1] + 0.0722 * p[2]


def delta(a, b) -> float:
    """Largest per-channel change: the light is additive/mixed, so a light window shows it in some channel too."""
    return max(abs(x - y) for x, y in zip(a, b))


@unittest.skipUnless(os.environ.get("MIOS_GLASS_NEST_SIG") and os.environ.get("MIOS_GLASS_NEST_WL") and shutil.which("grim"),
                     "pixel check needs a nested test compositor (MIOS_GLASS_NEST_SIG / MIOS_GLASS_NEST_WL) and grim")
class EdgePixels(unittest.TestCase):
    """Against a NESTED compositor only: its own test windows, its own outputs."""

    sig = os.environ.get("MIOS_GLASS_NEST_SIG", "")
    wl = os.environ.get("MIOS_GLASS_NEST_WL", "")

    def hypr(self, *args: str) -> str:
        return subprocess.run(["hyprctl", "-i", self.sig, *args], check=True, capture_output=True, text=True, timeout=10).stdout

    def shot(self, output: str, path: str) -> None:
        subprocess.run(["grim", "-o", output, path], check=True, env=dict(os.environ, WAYLAND_DISPLAY=self.wl), timeout=20)

    def edge(self, gain: float) -> None:
        self.hypr("eval", f"hl.plugin.hyprglass.config({{edge_pass = {gain}}})")
        time.sleep(1.2)

    def measure(self, tmp: str, tag: str):
        from PIL import Image  # noqa: PLC0415
        mons = {m["name"]: m for m in json.loads(self.hypr("monitors", "-j"))}
        clients = [c for c in json.loads(self.hypr("clients", "-j")) if c["mapped"] and not c["hidden"]]
        shots = {}
        for name, m in mons.items():
            if name.startswith("HEADLESS-"):
                shots[name] = os.path.join(tmp, f"{tag}-{name}.png")
                self.shot(name, shots[name])
        result = {}
        for c in clients:
            m = next((m for m in mons.values() if m["id"] == c["monitor"]), None)
            if not m or m["name"] not in shots or c["workspace"]["id"] != m["activeWorkspace"]["id"]:
                continue
            im = Image.open(shots[m["name"]]).convert("RGB")
            s = m["scale"]
            x0, y0 = (c["at"][0] - m["x"]) * s, (c["at"][1] - m["y"]) * s
            w, h = c["size"][0] * s, c["size"][1] * s
            others = [o for o in clients if o is not c and o["monitor"] == c["monitor"]]

            def free(px, py):
                gx, gy = m["x"] + px / s, m["y"] + py / s
                return not any(o["at"][0] <= gx <= o["at"][0] + o["size"][0] and o["at"][1] <= gy <= o["at"][1] + o["size"][1] for o in others)
            pts = [(int(x0 + w * f), int(y0 + d)) for f in (0.3, 0.45, 0.6, 0.75) for d in (2, 3, 4)]
            pts += [(int(x0 + d), int(y0 + h * f)) for f in (0.4, 0.55, 0.7) for d in (2, 3, 4)]
            inner = [(int(x0 + w * fx), int(y0 + h * fy)) for fx in (0.35, 0.5, 0.65) for fy in (0.45, 0.6)]
            pts = [p for p in pts if free(*p)]
            inner = [p for p in inner if free(*p)]
            result[c["address"]] = (c["class"] or c["title"], [im.getpixel(p) for p in pts], [im.getpixel(p) for p in inner])
        return result

    def test_every_visible_window_shows_the_edge_light(self):
        with tempfile.TemporaryDirectory() as tmp:
            try:
                self.edge(0)
                off = self.measure(tmp, "off")
                self.edge(1.0)
                on = self.measure(tmp, "on")
            finally:
                self.edge(1.0)
            self.assertGreaterEqual(len(on), 3, f"too few windows to judge: {on}")
            report, missing = [], []
            for addr, (name, edge_on, inner_on) in on.items():
                _, edge_off, inner_off = off[addr]
                gain = max((delta(a, b) for a, b in zip(edge_on, edge_off)), default=0.0)
                lift = max((lum(a) - lum(b) for a, b in zip(edge_on, edge_off)), default=0.0)
                drift = max((delta(a, b) for a, b in zip(inner_on, inner_off)), default=0.0)
                report.append(f"{name}: edge max-channel +{gain:.0f} luminance +{lift:.1f} interior {drift:.0f}")
                if gain < 4.0:
                    missing.append(name)
                self.assertLess(drift, 3.0, f"{name}: the edge pass reached the content: {report}")
            self.assertEqual(missing, [], "windows without the edge light: " + "; ".join(report))
            print("\n" + "\n".join(report))

    def test_negative_control_an_excluded_window_is_caught(self):
        clients = [c for c in json.loads(self.hypr("clients", "-j")) if c["mapped"] and c["floating"]]
        self.assertTrue(clients)
        victim = clients[0]["address"]
        with tempfile.TemporaryDirectory() as tmp:
            try:
                self.hypr("dispatch", f'hl.dsp.window.tag({{tag = "+hyprglass_disabled", window = "address:{victim}"}})')
                self.edge(0)
                off = self.measure(tmp, "off")
                self.edge(1.0)
                on = self.measure(tmp, "on")
            finally:
                self.hypr("dispatch", f'hl.dsp.window.tag({{tag = "-hyprglass_disabled", window = "address:{victim}"}})')
                self.edge(1.0)
            name, e_on, _ = on[victim]
            _, e_off, _ = off[victim]
            self.assertLess(max((delta(a, b) for a, b in zip(e_on, e_off)), default=0.0), 4.0, f"{name} still lit while excluded: the check would miss it")


if __name__ == "__main__":
    unittest.main(verbosity=2)
