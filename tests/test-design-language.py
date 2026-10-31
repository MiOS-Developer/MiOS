#!/usr/bin/env python3
# AI-hint: Gate for the ONE design language (mios.toml [theme.shell] comment block): fails when a Quickshell surface or a
# compositor rule hard-codes a radius, height, padding, shadow or glass parameter instead of the design tokens, and when
# the token projections disagree. QML (usr/share/mios/quickshell): every geometry binding (radius, heights, spacing,
# margins, paddings, border widths) is built from Glass.* tokens -- numeric literals other than 0, 1 and 2 (multipliers)
# fail, a bare literal fails, a radius must come from Glass (or another surface's radius) and never sits on a plain
# Rectangle (GlassSurface is the one shaped glass component), no QML shadow/glow/effect, no literal alpha on a fill.
# Compositor (mios.toml): the hyprbars tab geometry and button sizes are "theme.shell.*" / "design.*" references, the
# windows take decoration rounding_power from corner_power, one hyprglass preset ("mios") for windows, tab, layers and
# Hypr3D with one light angle, no per-surface glass overrides, and the plugins that still hard-code the circular curve
# (hyprbars, Hypr3D) force corner_power = 2. Projection: mios-sync-theme's "design" table equals the formulas (and, with
# MIOS_GEN set, mios-gen render-hyprland's hyprbars/decoration values). Negative control: main's tree must FAIL.
# Lines carrying "design-exempt:" with a reason are skipped (capture overlays, aspect ratios).
# AI-related: usr/share/mios/quickshell/Glass.qml, usr/share/mios/quickshell/GlassSurface.qml, usr/share/mios/mios.toml,
# usr/libexec/mios/mios-sync-theme, tools/native/mios-gen/src/render.rs (design_tokens, layer_geometry_source),
# tests/test-design-language-live.py
# AI-functions: qml_errors, toml_errors, bridge_errors, projection_errors, tree_errors, main_tree
"""Usage: python3 tests/test-design-language.py   (MIOS_GEN=<mios-gen built from this tree> adds the projection check;
MIOS_DESIGN_BASE=<git rev> picks the negative-control revision, default main)."""
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

TREE = Path(__file__).resolve().parents[1]
QS = Path("usr/share/mios/quickshell")
EXEMPT_FILES = {"Theme.qml", "Glass.qml", "GlassSurface.qml"}   # the bridge fallback, the tokens, the one component
GEOM = re.compile(r"^\s*(radius|height|implicitHeight|panelHeight|cardHeight|rowH|border\.width|spacing|rowSpacing|"
                  r"columnSpacing|(anchors\.)?margins|anchors\.(top|bottom|left|right)Margin|(top|bottom|left|right)?[pP]adding|"
                  r"Layout\.preferredHeight)\s*:\s*(?P<expr>.+?)\s*$")
NUM = re.compile(r"(?<![\w.#])(\d+(?:\.\d+)?)(?![\w.])")
SHADOW = re.compile(r"\b(DropShadow|MultiEffect|RectangularGlow|Glow\s*\{|shadowEnabled|layer\.effect)\b")
LITERAL_ALPHA = re.compile(r"withAlpha\([^()]*,\s*(\d*\.\d+|[2-9])\s*\)|Qt\.rgba\([^)]*,\s*(0?\.\d+)\s*\)|Qt\.(darker|lighter)\(")


def strip_comment(line: str) -> str:
    out, q = [], None
    i = 0
    while i < len(line):
        c = line[i]
        if q:
            if c == "\\":
                out.append(line[i:i + 2]); i += 2; continue
            if c == q:
                q = None
        elif c in "\"'":
            q = c
        elif line.startswith("//", i):
            break
        out.append(c)
        i += 1
    return "".join(out)


def qml_errors(root: Path) -> list[str]:
    e = []
    for f in sorted((root / QS).glob("*.qml")):
        if f.name in EXEMPT_FILES:
            continue
        stack = []   # enclosing object types
        for n, raw in enumerate(f.read_text().splitlines(), 1):
            where = f"{f.name}:{n}"
            if "design-exempt:" in raw:
                code = strip_comment(raw)
                stack_delta = code.count("{") - code.count("}")
                for _ in range(max(0, stack_delta)):
                    stack.append("")
                for _ in range(max(0, -stack_delta)):
                    stack and stack.pop()
                continue
            code = strip_comment(raw)
            if SHADOW.search(code):
                e.append(f"{where}: QML shadow/glow/effect ({code.strip()}): the light and shadow are hyprglass' preset")
            if LITERAL_ALPHA.search(code):
                e.append(f"{where}: literal alpha / colour factor on a fill ({code.strip()}): use a [theme.shell] opacity token")
            m = GEOM.match(code)
            if m:
                prop, expr = m.group(1), m.group("expr").split(";")[0].strip()
                if re.fullmatch(r"-?\d+(\.\d+)?", expr) and float(expr) != 0:
                    e.append(f"{where}: {prop}: {expr} is a hard-coded literal (use Glass.bar/control/inset/gap/panelPad/hairline)")
                else:
                    bad = [x for x in NUM.findall(expr) if float(x) not in (0.0, 1.0, 2.0)]
                    if bad:
                        e.append(f"{where}: {prop}: literal {', '.join(bad)} in '{expr}' (geometry comes from the design tokens)")
                if prop == "radius":
                    if "theme.radius" in expr or not ("Glass." in expr or "adius" in expr or re.search(r"\.r\b", expr)):
                        e.append(f"{where}: radius '{expr}' is not a design-language radius (Glass.corner/inner/panelRadius)")
                    if stack and stack[-1] == "Rectangle":
                        e.append(f"{where}: a shaped Rectangle -- shaped surfaces are GlassSurface (the one glass component)")
            # object nesting (one object per line opener, as the tree is written)
            for tok in re.finditer(r"(?:^|[\s:])([A-Z][\w.]*)\s*\{|(\{)|(\})", code):
                if tok.group(1):
                    stack.append(tok.group(1).split(".")[-1])
                elif tok.group(2):
                    stack.append("")
                elif stack:
                    stack.pop()
    return e


def toml_errors(d: dict, root: Path) -> list[str]:
    e = []
    shell = d.get("theme", {}).get("shell", {})
    for k in ("top_height_px", "inset_px", "edge_margin_px", "corner_power"):
        if not isinstance(shell.get(k), (int, float)):
            e.append(f"[theme.shell] {k} missing: the design language has no {k} token")
    h = d.get("desktop", {}).get("hyprland", {})
    bars = h.get("hyprbars", {})
    cfg = bars.get("config", {})
    want = {"bar_height": "theme.shell.top_height_px", "pill_padding": "theme.shell.inset_px",
            "bar_button_padding": "theme.shell.inset_px", "bar_padding": "design.tab_offset_px"}
    for k, v in want.items():
        if cfg.get(k) != v:
            e.append(f"hyprbars {k} = {cfg.get(k)!r}: must be the design reference {v!r}")
    for k in ("pill_inset", "pill_feather"):
        if k in cfg:
            e.append(f"hyprbars {k}: a per-surface geometry key the plugin never reads")
    for i, b in enumerate(bars.get("buttons", [])):
        if b.get("size") != "design.control_px":
            e.append(f"hyprbars button {i} size = {b.get('size')!r}: must be 'design.control_px' (bar - 2 x inset)")
    deco = h.get("config", {}).get("decoration", {})
    if deco.get("rounding_power") != "theme.shell.corner_power":
        e.append(f"decoration rounding_power = {deco.get('rounding_power')!r}: windows must take theme.shell.corner_power")
    hg = h.get("hyprglass", {})
    if hg.get("config", {}).get("default_preset") != "mios":
        e.append("hyprglass default_preset must be the one preset 'mios'")
    if cfg.get("glass_preset") != "mios":
        e.append("hyprbars glass_preset must be 'mios'")
    h3 = h.get("hypr3d", {}).get("config", {})
    if h3.get("glass_preset") != "mios" or "glass" in h3:
        e.append("Hypr3D must take the 'mios' preset and no [config.glass] override")
    lay = hg.get("config", {}).get("layers", {})
    for k in ("namespaces", "namespace_presets", "namespace_mask_thresholds", "preset"):
        if lay.get(k):
            e.append(f"hyprglass layers.{k}: a per-surface glass override")
    if d.get("desktop", {}).get("mobile", {}).get("remote_effects", {}).get("enabled"):
        e.append("[desktop.mobile.remote_effects] enabled: a second glass recipe")
    preset = hg.get("presets", {}).get("mios", {})
    if preset.get("specular_angle") is None or preset.get("specular_angle") != preset.get("bevel_angle"):
        e.append("hyprglass preset mios: specular_angle and bevel_angle must be set and equal (one light)")
    power = shell.get("corner_power")
    if isinstance(power, (int, float)) and power != 2:
        for patch in ("hyprbars-mios-overlay-tab.patch", "hypr3d-mios-glass.patch"):
            text = (root / "usr/share/mios/hyprland/patches" / patch).read_text(errors="replace")
            if "roundingPower = 2" in text or "const float roundingPower = 2.0" in text:
                e.append(f"corner_power {power}: {patch} still hard-codes the circular curve (roundingPower 2)")
    render = (root / "tools/native/mios-gen/src/render.rs").read_text()
    if 'getConfigValue("decoration:rounding_power")' not in render:
        e.append("render.rs layer_geometry_source: layer glass does not follow decoration:rounding_power")
    return e


def expected_design(d: dict) -> dict:
    s = d["theme"]["shell"]
    R = float(d["desktop"]["hyprland"]["config"]["decoration"]["rounding"])
    bar, inset, gap = float(s["top_height_px"]), float(s["inset_px"]), float(s["edge_margin_px"])
    return {"bar_px": bar, "inset_px": inset, "gap_px": gap, "control_px": bar - 2 * inset, "radius_px": R,
            "panel_pad_px": max(inset, R - bar / 2), "tab_offset_px": R + min(R, bar) / 2 + inset,
            "bar_centre_px": gap + bar / 2, "hairline_px": 1.0, "corner_power": float(s["corner_power"])}


def bridge_errors(root: Path, d: dict) -> list[str]:
    with tempfile.TemporaryDirectory() as out:
        env = dict(os.environ, MIOS_VENDOR_TOML=str(root / "usr/share/mios/mios.toml"), MIOS_HOST_TOML="/nonexistent",
                   MIOS_USER_TOML="/nonexistent", MIOS_THEME_OUT_DIR=out, MIOS_LIB_DIR="/usr/lib/mios")
        env.pop("MIOS_TOML", None)
        r = subprocess.run(["bash", str(root / "usr/libexec/mios/mios-sync-theme"), "--bridge-only"], env=env,
                           capture_output=True, text=True, timeout=120)
        p = Path(out) / "theme.json"
        if r.returncode or not p.is_file():
            return [f"mios-sync-theme failed: {r.stderr.strip()[-300:]}"]
        t = json.loads(p.read_text())
    got = t.get("design")
    if not isinstance(got, dict):
        return ["theme bridge has no 'design' table (Glass.qml would fall back to literals)"]
    e = [f"bridge design.{k} = {got.get(k)!r}, want {v}" for k, v in expected_design(d).items()
         if not isinstance(got.get(k), (int, float)) or abs(float(got[k]) - v) > 1e-9]
    hb = t.get("hyprbars", {})
    ex = expected_design(d)
    if hb.get("bar_height") != ex["bar_px"] or hb.get("pill_padding") != ex["inset_px"] or \
            any(b != ex["control_px"] for b in hb.get("buttons", [])):
        e.append(f"bridge hyprbars geometry {hb} disagrees with the design tokens")
    return e


def projection_errors(root: Path, d: dict) -> list[str]:
    gen = os.environ.get("MIOS_GEN")
    if not gen:
        return []
    env = dict(os.environ, MIOS_VENDOR_TOML=str(root / "usr/share/mios/mios.toml"), MIOS_HOST_TOML="/nonexistent",
               MIOS_USER_TOML="/nonexistent")
    env.pop("MIOS_TOML", None)
    # --check: the committed projection (usr/share/mios/hyprland/hyprland.lua) is what this mios-gen renders from the SSOT
    r = subprocess.run([gen, "render-hyprland", "--root", str(root), "--check"], env=env, capture_output=True, text=True, timeout=120)
    if r.returncode:
        return [f"mios-gen render-hyprland --check: the committed hyprland.lua drifts from the SSOT: {(r.stdout + r.stderr).strip()[-300:]}"]
    lua, ex = (root / "usr/share/mios/hyprland/hyprland.lua").read_text(), expected_design(d)
    e = []
    for key, want in (("bar_height", ex["bar_px"]), ("pill_padding", ex["inset_px"]), ("bar_button_padding", ex["inset_px"]),
                      ("bar_padding", ex["tab_offset_px"])):
        m = re.search(r'\["%s"\] = ([\d.]+)' % key, lua)
        if not m or float(m.group(1)) != want:
            e.append(f"render-hyprland hyprbars {key} = {m and m.group(1)}, want {want}")
    sizes = re.findall(r'add_button\(\{[^}]*\["size"\] = ([\d.]+)', lua)
    if not sizes or any(float(s) != ex["control_px"] for s in sizes):
        e.append(f"render-hyprland button sizes {sizes}, want {ex['control_px']}")
    m = re.search(r'\["rounding_power"\] = ([\d.]+)', lua)
    if not m or float(m.group(1)) != ex["corner_power"]:
        e.append(f"render-hyprland rounding_power = {m and m.group(1)}, want {ex['corner_power']}")
    if '"theme.shell.' in lua or '"design.' in lua:
        e.append("render-hyprland left a design reference unresolved")
    return e


def tree_errors(root: Path) -> list[str]:
    d = tomllib.loads((root / "usr/share/mios/mios.toml").read_text())
    e = qml_errors(root) + toml_errors(d, root)
    if not e:
        e += bridge_errors(root, d) + projection_errors(root, d)
    return e


def main_tree(dest: Path) -> Path:
    rev = os.environ.get("MIOS_DESIGN_BASE", "main")
    paths = ["usr/share/mios/quickshell", "usr/share/mios/mios.toml", "usr/libexec/mios/mios-sync-theme",
             "usr/share/mios/hyprland/patches", "tools/native/mios-gen/src/render.rs"]
    arch = subprocess.run(["git", "-C", str(TREE), "archive", rev, *paths], capture_output=True, check=True)
    subprocess.run(["tar", "-x", "-C", str(dest)], input=arch.stdout, check=True)
    return dest


class TestDesignLanguage(unittest.TestCase):
    def test_tree_uses_only_design_tokens(self):
        e = tree_errors(TREE)
        self.assertEqual(e, [], "\n".join(e[:60]))

    def test_negative_control_main_fails(self):
        with tempfile.TemporaryDirectory() as t:
            root = main_tree(Path(t))
            e = qml_errors(root)
            d = tomllib.loads((root / "usr/share/mios/mios.toml").read_text())
            te = toml_errors(d, root)
            print(f"\nNEGATIVE CONTROL ({os.environ.get('MIOS_DESIGN_BASE', 'main')}): {len(e)} QML offences, {len(te)} SSOT offences; e.g.",
                  *(e[:6] + te[:4]), sep="\n  ")
            self.assertTrue(e, "main's QML must fail the gate (hard-coded radii / heights)")
            self.assertTrue(te, "main's SSOT must fail the gate (literal hyprbars geometry)")

    def test_gate_catches_each_offence_kind(self):
        # synthetic offences on the tree's own files: each must be caught
        with tempfile.TemporaryDirectory() as t:
            root = Path(t)
            (root / QS).mkdir(parents=True)
            (root / QS / "Bad.qml").write_text(
                "Item {\n Rectangle {\n  radius: height / 2\n  height: 32\n  color: theme.withAlpha(theme.bg, 0.85)\n }\n"
                " GlassSurface {\n  radius: theme.radius\n  anchors.margins: 6\n  spacing: theme.edgeMargin + 4\n }\n"
                " DropShadow { }\n GlassSurface { height: Glass.bar; anchors.margins: Glass.inset }\n}\n")
            e = qml_errors(root)
            for needle in ("Bad.qml:3: radius", "Bad.qml:3: a shaped Rectangle", "Bad.qml:4: height: 32", "Bad.qml:5: literal alpha",
                           "Bad.qml:8: radius", "Bad.qml:9: anchors.margins: 6", "Bad.qml:10: spacing: literal 4", "Bad.qml:12: QML shadow"):
                self.assertTrue(any(x.startswith(needle) for x in e), (needle, e))
            self.assertFalse(any(x.startswith("Bad.qml:13") for x in e), e)


if __name__ == "__main__":
    unittest.main(verbosity=2)
