#!/usr/bin/env python3
# AI-hint: Hypr3D "Retina" window opening (operator 2026-10-23: "default Hypr3D window opening distance/size should be
# Zoom/Scale/DPI/Resolution/Formatting(Landscape, Portrait, Mobile, etc-etc) aware ... launch to Retina specifications
# based on text-size"). A window opens in the room at magnification M = 1 (on-screen size / 2D size): one snapshot
# texel per output pixel and every client's glyphs at their flat-desktop size. M = window_scale x d1 / distance with
# d1 = (logical height / 100 / 2) / tan(65 deg / 2); [desktop.hyprland.hypr3d.spawn] keeps the reading distance and
# mios-gen derives window_scale per output; hypr3d-mios-retina-spawn.patch adds the per-window part (live
# pixel_exact scale, fit into the work area minus the margin -- resize, else push back, never crop -- work-area
# centring, pixel snap, in-plane cascade with M-compensated stacking, texel-sharp sampling for render.scale >= 1.5);
# hypr3d-mios-transform.patch renders the room upright on rotated/flipped outputs (portrait by transform), which
# upstream drew sideways with the wrong window content.
# Tiers:
#   1. static + model (always): SSOT keys, patch content, an independent Python oracle of the math (landscape
#      1920x1080@1, portrait 1080x1920 by transform, 2560x1440@1.25, phone 1170x2532@3 mobile; small/large windows)
#      with negative controls (the stock 0.5 / 5 m placement is not 1:1 on any of them).
#   2. cli (MIOS_GEN = a built mios-gen): `mios-gen hyprland-3d-spawn` on monitor fixtures against the oracle; a
#      "fixed" criterion and bad arguments are refused.
#   3. upstream (MIOS_H3D_UPSTREAM = a checkout of the current upstream HEAD): every SSOT patch applies in order (the
#      retina and transform patches last, after the frame-rate patch), the patched source parses the new keys, and its
#      static_assert compiles -- and fails when kFovDeg or LOGICAL_PX_PER_UNIT change (negative control).
#   4. nested (MIOS_H3R_NEST_SIG + _XDG + _WL + _STATUS + MIOS_H3R_NEW_SO + MIOS_H3R_QML + MIOS_GEN +
#      MIOS_H3R_SSOT: a NESTED Hyprland, never the live session): measured on screen -- M = 1 and work-area centring
#      at the toggle, the spawn box and cascade for windows mapped in the room, resize and push-back of oversized
#      windows, 1-px detail with texel_sharp and pixel_snap (each against its negative control), and the real
#      `mios-gen hyprland-3d-toggle` driving the nest -- on rotated outputs too; there MIOS_H3R_OLD_SO (a plugin
#      without the transform patch, e.g. the installed one) is the negative control. Evidence: MIOS_H3R_EVIDENCE.
# AI-related: tools/native/mios-gen/src/hypr3d_spawn.rs, usr/share/mios/hyprland/patches/hypr3d-mios-retina-spawn.patch,
# usr/share/mios/mios.toml [desktop.hyprland.hypr3d.spawn], tests/test-hypr3d-edge-quality.py
"""Hypr3D Retina window opening: per-output scale, fit, cascade, pixel grid, texel-sharp sampling."""

from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import tomllib
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SSOT = os.environ.get("MIOS_H3R_SSOT") or os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
PATCH = "usr/share/mios/hyprland/patches/hypr3d-mios-retina-spawn.patch"
TRANSFORM = "usr/share/mios/hyprland/patches/hypr3d-mios-transform.patch"
FRAME_RATE = "usr/share/mios/hyprland/patches/hypr3d-mios-frame-rate.patch"
CRT = "usr/share/mios/hyprland/patches/hypr3d-mios-crt.patch"
HUD_CURSOR = "usr/share/mios/hyprland/patches/hypr3d-mios-hud-cursor.patch"
MIOS_GEN = os.environ.get("MIOS_GEN", "")
UPSTREAM = os.environ.get("MIOS_H3D_UPSTREAM", "")
NEST_SIG = os.environ.get("MIOS_H3R_NEST_SIG", "")
NEST_XDG = os.environ.get("MIOS_H3R_NEST_XDG", "")
NEST_WL = os.environ.get("MIOS_H3R_NEST_WL", "")
NEST_STATUS = os.environ.get("MIOS_H3R_NEST_STATUS", "")
NEW_SO = os.environ.get("MIOS_H3R_NEW_SO", "")
OLD_SO = os.environ.get("MIOS_H3R_OLD_SO", "")
QML = os.environ.get("MIOS_H3R_QML", "")
EVIDENCE = os.environ.get("MIOS_H3R_EVIDENCE", "")
CASE = os.environ.get("MIOS_H3R_CASE", "nest")

FOV_DEG, PX_PER_UNIT = 65.0, 100.0          # Hypr3D kFovDeg, LOGICAL_PX_PER_UNIT
TAN = math.tan(math.radians(FOV_DEG) / 2)
BARS = (0, 44, 0, 72)                       # the live DP-3 reserved [left, top, right, bottom]


def ssot() -> dict:
    with open(SSOT, "rb") as f:
        return tomllib.load(f)


def spawn_table(data: dict) -> dict:
    return data["desktop"]["hyprland"]["hypr3d"]["spawn"]


def resolve(data: dict, v):
    """A number, or the dotted SSOT key it follows."""
    if isinstance(v, str):
        for k in v.split("."):
            data = data[k]
        return float(data)
    return float(v)


# ---- the oracle (independent of hypr3d_spawn.rs; spawn_math.py's formulas) ---------------------------------------
def logical(w, h, s, t):
    return ((h, w) if t % 2 else (w, h))[0] / s, ((h, w) if t % 2 else (w, h))[1] / s


def d1(h_log):
    return (h_log / PX_PER_UNIT / 2.0) / TAN


def magnification(scale, dist, h_log):
    return scale * d1(h_log) / dist


def oracle(data, w, h, s, t, reserved=BARS, mobile=False, render_scale=2.0):
    sp = spawn_table(data)
    font = data
    for k in sp["text_size_source"].split("."):
        font = font[k]
    margin = resolve(data, sp["fit_margin_px"])
    top = max(reserved[1], data["theme"]["shell"]["top_height_px"])
    bottom = max(reserved[3], data["theme"]["shell"]["dock_height_px"] + data["theme"]["shell"]["edge_margin_px"])
    wl, hl = logical(w, h, s, t)
    fit = (max(16.0, wl - reserved[0] - reserved[2] - 2 * margin), max(16.0, hl - top - bottom - 2 * margin))
    if mobile and sp["mobile"] == "fill":
        box = (math.floor(fit[0]), math.floor(fit[1]))
    else:
        cols, rows = sp["portrait_cells"] if hl > wl else sp["landscape_cells"]
        box = (math.floor(min(fit[0], cols * font["cell_w_px"] + font["chrome_w_px"])),
               math.floor(min(fit[1], rows * font["cell_h_px"] + font["chrome_h_px"])))
    dist = float(sp["reading_distance_m"])
    return {"logical": (wl, hl), "d1": d1(hl), "S": dist / d1(hl), "dist": dist, "fit": fit, "spawn": box,
            "m_min": min(1.0, max(1.0 / render_scale, sp["min_text_px"] / font["size"])),
            "orientation": "portrait" if hl > wl else "landscape"}


def stock_size(o, w, h):
    """A window that stays on screen at the stock magnification (portrait outputs magnify 1.5x)."""
    m = magnification(0.5, 5.0, o["logical"][1])
    k = min(1.0, 0.8 * o["fit"][0] / (w * m), 0.8 * o["fit"][1] / (h * m))
    return max(160, int(w * k)), max(120, int(h * k))


def fit_window(o, w, h, mode="resize", min_size=None):
    fw, fh = o["fit"]
    bw, bh = w, h
    if mode == "resize" and (w > fw or h > fh):
        mw, mh = min_size or (1, 1)
        bw, bh = max(min(w, fw), mw), max(min(h, fh), mh)
    m = 1.0 if mode == "off" else min(1.0, fw / bw, fh / bh)
    return (bw, bh), m


OUTPUTS = {  # name: (mode w, mode h, scale, transform, mobile, expected logical, expected window_scale)
    "landscape": (1920, 1080, 1.0, 0, False, (1920, 1080), 0.076684),
    "portrait": (1920, 1080, 1.0, 1, False, (1080, 1920), 0.043135),
    "qhd125": (2560, 1440, 1.25, 0, False, (2048, 1152), 0.071892),
    "phone": (1170, 2532, 3.0, 0, True, (390, 844), 0.098127),
}


def monitors_fixture():
    rows = []
    for i, (name, (w, h, s, t, _m, _l, _s)) in enumerate(OUTPUTS.items()):
        rows.append({"name": f"OUT-{name}", "width": w, "height": h, "scale": s, "transform": t, "reserved": list(BARS),
                     "physicalWidth": 0, "physicalHeight": 0, "focused": i == 0})
    return rows


def evidence(name: str) -> str:
    d = EVIDENCE or tempfile.gettempdir()
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def hermetic_env(vendor=SSOT) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("HYPRLAND_INSTANCE_SIGNATURE", "WAYLAND_DISPLAY")}
    env.pop("MIOS_DESKTOP_HARDWARE_PROFILE", None)
    env.update(MIOS_VENDOR_TOML=vendor, MIOS_HOST_TOML="/nonexistent/mios.toml", MIOS_USER_TOML="/nonexistent/u.toml",
               XDG_RUNTIME_DIR=tempfile.gettempdir())
    return env


# ---- tier 1 ----------------------------------------------------------------------------------------------------------
class Static(unittest.TestCase):
    def test_ssot_policy(self):
        data = ssot()
        h = data["desktop"]["hyprland"]["hypr3d"]
        sp = h["spawn"]
        self.assertTrue(sp["enabled"])
        self.assertEqual(sp["criterion"], "retina")
        self.assertTrue(math.isfinite(float(sp["reading_distance_m"])) and float(sp["reading_distance_m"]) > 0)
        self.assertAlmostEqual(float(sp["eyeline_offset_m"]), 0.04, places=2)
        self.assertAlmostEqual(float(sp["tilt_angle_deg"]), 12.0, places=2)
        self.assertEqual(sp["open_effect_3d"], "magic-lamp")
        self.assertEqual(sp["close_effect_3d"], "glass-dissolve")
        self.assertEqual(sp["fit"], "resize")
        self.assertEqual(sp["mobile"], "fill")
        self.assertEqual(resolve(data, sp["fit_margin_px"]), data["theme"]["shell"]["edge_margin_px"])
        self.assertEqual(sp["text_size_source"], "theme.font")
        self.assertEqual(sp["landscape_cells"], [94, 34])
        self.assertEqual(sp["portrait_cells"], [66, 46])
        self.assertGreater(sp["portrait_cells"][1], sp["landscape_cells"][1])
        self.assertTrue(h["config"]["render"]["texel_sharp"])
        # no hard-coded window placement left in the room config: the policy derives it per output
        self.assertNotIn("window_scale", h["config"].get("windows", {}))
        patches = [p for p in h["patches"] if "enter-morph-scale" not in p and "pip-theatre" not in p and "room-input" not in p]  # trailing delta patch
        self.assertEqual(len(patches), len(set(patches)))
        edge = patches.index("usr/share/mios/hyprland/patches/hypr3d-mios-edge-quality.patch")
        self.assertLess(edge, len(patches) - 2)
        idx_patch = patches.index(PATCH)
        self.assertEqual(patches[idx_patch - 1], FRAME_RATE, "ordered after the frame-rate patch")
        if patches[-1] == HUD_CURSOR:
            self.assertEqual(patches[-4:], [PATCH, TRANSFORM, CRT, HUD_CURSOR])
        elif patches[-1] == CRT:
            self.assertEqual(patches[-3:], [PATCH, TRANSFORM, CRT])
        else:
            self.assertEqual(patches[-2:], [PATCH, TRANSFORM], "retina spawn, then rotated outputs, last")
        for p in patches:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, p)), p)

    def test_patch_content(self):
        with open(os.path.join(ROOT, PATCH), encoding="utf-8") as f:
            text = f.read()
        files = re.findall(r"^diff --git a/(\S+)", text, re.M)
        self.assertEqual(sorted(set(files)), ["src/Render/GLScene.cpp", "src/Render/GLScene.hpp", "src/main.cpp"])
        added = "\n".join(ln[1:] for ln in text.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        self.assertIn("static_assert(kFovDeg == 65.0f && World3D::LOGICAL_PX_PER_UNIT == 100.0f", added)
        for key in ("pixel_exact", "pixel_snap", "fit_margin", "cascade", "min_magnification", "texel_sharp"):
            self.assertIn(f'"{key}"', added, key)
        self.assertIn('"fit"', added)
        # the one-glass-look shader terms are untouched: only the client texture fetch (texSS) changes
        removed = "\n".join(ln[1:] for ln in text.splitlines() if ln.startswith("-") and not ln.startswith("---"))
        for term in ("hgEdgePass", "hgMaterial", "frostGlassCopy", "uGlassBackground"):
            self.assertNotIn(term, removed, term)
        # nothing of the frame-rate pacing is touched (max_fps / glass_fps / live_fps stay the other patch's)
        for term in ("max_fps", "glass_fps", "live_fps", "glassDue"):
            self.assertNotIn(term, removed, term)
        self.assertTrue(text.startswith("MiOS Hypr3D Retina window opening (applies after hypr3d-mios-frame-rate.patch)"))

    def test_transform_patch_content(self):
        with open(os.path.join(ROOT, TRANSFORM), encoding="utf-8") as f:
            text = f.read()
        files = re.findall(r"^diff --git a/(\S+)", text, re.M)
        self.assertEqual(sorted(set(files)), ["src/HyprlandCompat/WindowCapture.cpp", "src/Render/GLScene.cpp",
                                              "src/Render/GLScene.hpp", "src/main.cpp"])
        added = "\n".join(ln[1:] for ln in text.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        removed = "\n".join(ln[1:] for ln in text.splitlines() if ln.startswith("-") and not ln.startswith("---"))
        # the measured map: buffer -> upright texture coordinates is M_t itself for every wl_output_transform t
        self.assertIn("inline int miosOutputMap(int wlTransform) {\n    return wlTransform & 7;\n}", added)
        self.assertNotIn("getenv", added, "no dev override left in the patch")
        for term in ("uprightSnapshot", "m_transformedSize", "uOutTransform", "int outputTransform"):
            self.assertIn(term, added, term)
        for term in ("hgEdgePass", "hgMaterial", "frostGlassCopy", "uGlassBackground", "max_fps", "glass_fps"):
            self.assertNotIn(term, removed, term)
        self.assertTrue(text.startswith("MiOS Hypr3D rotated outputs (applies after hypr3d-mios-retina-spawn.patch)"))

    def test_oracle_is_one_to_one_on_every_output(self):
        data = ssot()
        for name, (w, h, s, t, mobile, want_logical, want_s) in OUTPUTS.items():
            o = oracle(data, w, h, s, t, mobile=mobile)
            self.assertEqual(o["logical"], want_logical, name)
            self.assertAlmostEqual(o["S"], want_s * float(spawn_table(data)["reading_distance_m"]) / 0.65, places=6, msg=name)  # reference vectors use 0.65 m
            self.assertAlmostEqual(magnification(o["S"], o["dist"], o["logical"][1]), 1.0, places=12)
            # negative control: the stock placement is not 1:1 here
            self.assertGreater(abs(magnification(0.5, 5.0, o["logical"][1]) - 1.0), 0.05, name)
        # the nested measurements of the stock placement (DESIGN.md section 5)
        self.assertAlmostEqual(magnification(0.5, 5.0, 1080), 0.8476, places=3)
        self.assertAlmostEqual(magnification(0.5, 5.0, 844), 0.6625, places=3)
        self.assertAlmostEqual(magnification(0.5, 5.0, 1440), 1.1302, places=3)
        land = oracle(data, 1920, 1080, 1, 0)
        self.assertEqual((land["fit"], land["spawn"], land["orientation"]), ((1904, 948), (960, 692), "landscape"))
        port = oracle(data, 1920, 1080, 1, 1)
        self.assertEqual((port["fit"], port["spawn"], port["orientation"]), ((1064, 1788), (680, 932), "portrait"))
        phone = oracle(data, 1170, 2532, 3, 0, mobile=True)
        self.assertEqual((phone["fit"], phone["spawn"]), ((374, 712), (374, 712)))
        # windows: small untouched at M 1; large resized at M 1; a refusing client pushed back, never cropped
        self.assertEqual(fit_window(land, 640, 400), ((640, 400), 1.0))
        self.assertEqual(fit_window(land, 2400, 1400), ((1904, 948), 1.0))
        box, m = fit_window(land, 2400, 1400, min_size=(2400, 1400))
        self.assertAlmostEqual(m, 948 / 1400)
        self.assertLessEqual(box[0] * m, land["fit"][0] + 1e-9)
        self.assertLessEqual(box[1] * m, land["fit"][1] + 1e-9)
        self.assertLess(m, land["m_min"], "flagged below the legibility floor")


# ---- tier 2 ----------------------------------------------------------------------------------------------------------
@unittest.skipUnless(MIOS_GEN, "MIOS_GEN (a built mios-gen) not set")
class Cli(unittest.TestCase):
    def run_cli(self, *args, vendor=SSOT, monitors=None, mobile=None):
        with tempfile.TemporaryDirectory() as tmp:
            mpath = os.path.join(tmp, "monitors.json")
            with open(mpath, "w") as f:
                json.dump(monitors if monitors is not None else monitors_fixture(), f)
            extra = []
            if mobile is not None:
                bpath = os.path.join(tmp, "mobile.json")
                with open(bpath, "w") as f:
                    json.dump(mobile, f)
                extra = ["--mobile", bpath]
            return subprocess.run([MIOS_GEN, "hyprland-3d-spawn", "--monitors", mpath, *extra, *args],
                                  env=hermetic_env(vendor), capture_output=True, text=True, timeout=30)

    def test_plan_matches_the_oracle(self):
        data = ssot()
        mobile = {"outputs": {"OUT-phone": {"mobile": True}}}
        p = self.run_cli("--render-scale", "2", "--window", "640x400", "--window", "2400x1400", mobile=mobile)
        self.assertEqual(p.returncode, 0, p.stderr)
        rows = {r["output"]: r for r in json.loads(p.stdout)["outputs"]}
        for name, (w, h, s, t, mob, _l, _s) in OUTPUTS.items():
            r = rows[f"OUT-{name}"]
            o = oracle(data, w, h, s, t, mobile=mob)
            self.assertAlmostEqual(r["window_scale"], o["S"], places=6, msg=name)
            self.assertAlmostEqual(r["magnification"], 1.0, places=6, msg=name)
            self.assertEqual(tuple(r["spawn_box"]), o["spawn"], name)
            self.assertEqual(tuple(r["fit_box"]), o["fit"], name)
            self.assertEqual(r["orientation"], o["orientation"], name)
            self.assertEqual(r["mobile"], mob, name)
            self.assertAlmostEqual(r["min_magnification"], o["m_min"], places=4)
            # the SSOT text size on screen: the flat desktop's in the room, not the plugin default's
            font = data["theme"]["font"]["size"]
            self.assertAlmostEqual(r["text_px"]["flat"], font * s, places=3)
            self.assertAlmostEqual(r["text_px"]["room"], font * s, places=3)
            self.assertGreater(abs(r["text_px"]["stock_room"] / r["text_px"]["flat"] - 1.0), 0.05, name)
            for win in r["windows"]:
                box, m = fit_window(o, *win["request"])
                self.assertEqual(tuple(win["box"]), box, (name, win))
                self.assertAlmostEqual(win["magnification"], m, places=6)
            keys = r["plugin_keys"]
            self.assertEqual((keys["pixel_exact"], keys["fit"], keys["spawn_size"]),
                             (True, "resize", {"x": o["spawn"][0], "y": o["spawn"][1]}))
        # the stock placement it replaces (negative control)
        self.assertAlmostEqual(rows["OUT-landscape"]["stock_magnification"], 0.8476, places=4)

    def test_mobile_verdict_changes_the_spawn_box(self):
        on = self.run_cli("--output", "OUT-landscape", mobile={"outputs": {"OUT-landscape": {"mobile": True}}})
        off = self.run_cli("--output", "OUT-landscape", mobile={"outputs": {"OUT-landscape": {"mobile": False}}})
        self.assertEqual(json.loads(on.stdout)["outputs"][0]["spawn_box"], [1904, 948])
        self.assertEqual(json.loads(off.stdout)["outputs"][0]["spawn_box"], [960, 692])

    def test_unreserved_output_keeps_the_topbar_and_dock_clear(self):
        data = ssot()
        o = oracle(data, 1920, 1080, 1.0, 0, reserved=(0, 0, 0, 0))
        self.assertEqual(o["fit"], (1904.0, 972.0))
        self.assertEqual(o["spawn"], (960, 692))

    def test_refusals(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(SSOT, encoding="utf-8") as f:
                data = f.read()
            fixed = os.path.join(tmp, "fixed.toml")
            n = data.count('criterion = "retina"')
            self.assertEqual(n, 1)
            with open(fixed, "w") as f:
                f.write(data.replace('criterion = "retina"', 'criterion = "fixed"'))
            p = self.run_cli(vendor=fixed)
            self.assertEqual(p.returncode, 2)
            self.assertIn("off", p.stderr)
            bad = os.path.join(tmp, "bad.toml")
            with open(bad, "w") as f:
                f.write(data.replace('criterion = "retina"', 'criterion = "crisp"'))
            p = self.run_cli(vendor=bad)
            self.assertEqual(p.returncode, 2)
            self.assertIn("spawn.criterion", p.stderr)
        for args in (["--window", "12"], ["--render-scale", "9"], ["--output", "NOPE"]):
            p = self.run_cli(*args)
            self.assertEqual(p.returncode, 2, args)
        self.assertEqual(self.run_cli(monitors=[]).returncode, 2)


# ---- tier 3 ----------------------------------------------------------------------------------------------------------
@unittest.skipUnless(UPSTREAM, "MIOS_H3D_UPSTREAM (checkout of the current upstream HEAD) not set")
class Upstream(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="h3r-up-")
        cls.work = os.path.join(cls.tmp, "src")
        subprocess.run(["git", "-c", "safe.directory=*", "clone", "-q", UPSTREAM, cls.work], check=True)
        cls.applied = []
        for p in ssot()["desktop"]["hyprland"]["hypr3d"]["patches"]:
            r = subprocess.run(["git", "apply", "--check", os.path.join(ROOT, p)], cwd=cls.work, capture_output=True,
                               text=True)
            cls.applied.append((p, r.returncode, r.stderr))
            if r.returncode == 0:
                subprocess.run(["git", "apply", os.path.join(ROOT, p)], cwd=cls.work, check=True)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_patches_apply_in_order(self):
        for p, rc, err in self.applied:
            self.assertEqual(rc, 0, f"{p}: {err}")
        ordered = [a[0] for a in self.applied]
        index = ordered.index(PATCH)
        self.assertEqual(ordered[index - 1:index + 2], [FRAME_RATE, PATCH, TRANSFORM])
        if CRT in ordered:
            self.assertEqual(ordered[index + 2], CRT)

    def test_upstream_is_the_latest_head(self):
        """Never pinned: the checkout is at the upstream default branch's HEAD (skipped offline)."""
        r = subprocess.run(["git", "-c", "safe.directory=*", "ls-remote", "https://github.com/samine825/Hypr3D.git", "HEAD"],
                           capture_output=True, text=True, timeout=60)
        if r.returncode:
            self.skipTest(f"ls-remote failed (offline?): {r.stderr.strip()}")
        here = subprocess.run(["git", "-c", "safe.directory=*", "-C", UPSTREAM, "rev-parse", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
        self.assertEqual(here, r.stdout.split()[0])

    def test_patches_need_their_base(self):
        """Negative controls for the order: the retina patch builds on the earlier MiOS patches (it does not apply to
        bare upstream), and the transform patch builds on the retina patch (it does not apply without it)."""
        d = tempfile.mkdtemp(prefix="h3r-order-", dir=self.tmp)
        work = os.path.join(d, "src")
        subprocess.run(["git", "-c", "safe.directory=*", "clone", "-q", UPSTREAM, work], check=True)

        def check(p):
            return subprocess.run(["git", "apply", "--check", os.path.join(ROOT, p)], cwd=work, capture_output=True).returncode

        self.assertNotEqual(check(PATCH), 0, "the retina patch applied to bare upstream")
        patches = ssot()["desktop"]["hyprland"]["hypr3d"]["patches"]
        for p in patches[:patches.index(PATCH)]:
            subprocess.run(["git", "apply", os.path.join(ROOT, p)], cwd=work, check=True)
        self.assertNotEqual(check(TRANSFORM), 0, "the transform patch applied without the retina patch")
        self.assertEqual(check(PATCH), 0)

    def test_keys_parse_in_the_config_binding(self):
        with open(os.path.join(self.work, "src/main.cpp"), encoding="utf-8") as f:
            src = f.read()
        for key, kind in (("pixel_exact", "SET_BOOL"), ("pixel_snap", "SET_BOOL"), ("fit_margin", "SET_NUM"),
                          ("cascade", "SET_NUM"), ("min_magnification", "SET_NUM"), ("texel_sharp", "SET_BOOL"),
                          ("fit", "SET_STRING")):
            self.assertRegex(src, rf'{kind}\(idx, "{key}"', key)
        # the frame-rate keys still parse next to them
        for key in ("max_fps", "glass_fps", "live_fps"):
            self.assertIn(f'"{key}"', src, key)
        # the scene renders upright and the composite follows the output transform
        self.assertIn("ROOM_MON ? static_cast<int>(ROOM_MON->m_transform) : 0", src)

    @unittest.skipUnless(shutil.which("g++"), "no g++")
    def test_static_assert_guards_the_mirrored_constants(self):
        with open(os.path.join(self.work, "src/main.cpp"), encoding="utf-8") as f:
            src = f.read()
        m = re.search(r"static_assert\(kFovDeg == 65\.0f && World3D::LOGICAL_PX_PER_UNIT == 100\.0f,\s*\"[^\"]*\"\);", src)
        self.assertIsNotNone(m)

        def compile_with(fov, ppu):
            d = tempfile.mkdtemp(prefix="h3r-sa-", dir=self.tmp)
            shutil.copytree(os.path.join(self.work, "src"), os.path.join(d, "src"))
            for rel, old, new in (("src/Render/GLScene.hpp", "kFovDeg = 65.0f", f"kFovDeg = {fov}f"),
                                  ("src/World/World3D.hpp", "LOGICAL_PX_PER_UNIT = 100.0f", f"LOGICAL_PX_PER_UNIT = {ppu}f")):
                path = os.path.join(d, rel)
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                self.assertEqual(text.count(old), 1, rel)
                with open(path, "w", encoding="utf-8") as f:
                    f.write(text.replace(old, new))
            tu = os.path.join(d, "sa.cpp")
            with open(tu, "w") as f:
                f.write('#include "Render/GLScene.hpp"\n#include "World/World3D.hpp"\nnamespace H3D {\n'
                        f"{m.group(0)}\n}}\nint main() {{}}\n")
            return subprocess.run(["g++", "-std=c++23", "-fsyntax-only", "-I", os.path.join(d, "src"), tu],
                                  capture_output=True, text=True, timeout=120)

        self.assertEqual(compile_with("65.0", "100.0").returncode, 0)
        for fov, ppu in (("60.0", "100.0"), ("65.0", "128.0")):  # negative controls: upstream changed a constant
            r = compile_with(fov, ppu)
            self.assertNotEqual(r.returncode, 0, (fov, ppu))
            self.assertIn("hypr3d-mios-retina-spawn", r.stderr)


# ---- tier 4 ----------------------------------------------------------------------------------------------------------
NESTED = NEST_SIG and NEST_XDG and NEST_WL and NEST_STATUS and NEW_SO and QML
COLOURS = {"red": (255, 0, 0), "blue": (0, 0, 255), "green": (0, 255, 0), "magenta": (255, 0, 255)}


class Nest:
    def __init__(self):
        self.env = {k: v for k, v in os.environ.items() if k not in ("WAYLAND_DISPLAY", "DISPLAY",
                                                                     "HYPRLAND_INSTANCE_SIGNATURE")}
        self.env.update(XDG_RUNTIME_DIR=NEST_XDG, WAYLAND_DISPLAY=NEST_WL)
        self.units: list[str] = []
        inst = json.loads(subprocess.run(["hyprctl", "instances", "-j"], env=self.env, capture_output=True,
                                         text=True).stdout or "[]")
        assert [i["instance"] for i in inst] == [NEST_SIG], f"the private runtime must hold only the nest: {inst}"
        self.mon = [m for m in json.loads(self.hc("-j", "monitors")) if m["name"] == "HEADLESS-DESK"][0]

    def hc(self, *a, check=True):
        p = subprocess.run(["hyprctl", "-i", NEST_SIG, *a], env=self.env, capture_output=True, text=True, timeout=20)
        if check and p.returncode:
            raise AssertionError(f"nested hyprctl {a}: {p.stdout}{p.stderr}")
        return p.stdout.strip()

    def ev(self, lua):
        out = self.hc("eval", lua)
        if "error" in out.lower():
            raise AssertionError(f"nested eval failed: {lua}: {out}")
        return out

    def plugin(self, so):
        if "Plugin hypr3d" in self.hc("plugin", "list", check=False):
            try:
                self.ev("if hl.plugin.hypr3d and hl.plugin.hypr3d.active() then hl.plugin.hypr3d.close() end")
            except AssertionError:
                pass
            time.sleep(1.5)
            for cand in dict.fromkeys(c for c in (so, NEW_SO, OLD_SO, "/usr/lib64/mios/hypr3d.so") if c):
                self.hc("plugin", "unload", cand, check=False)
            time.sleep(1.0)
        open(NEST_STATUS, "w").close()
        out = self.hc("plugin", "load", so)
        if "ok" not in out.lower() and "loaded" not in out.lower():
            raise AssertionError(f"plugin load {so}: {out}")
        time.sleep(0.5)

    def client(self, name, w, h, colours=("red", "blue"), fixed=False):
        unit = f"mios-h3r-c-{name}-{os.getpid()}"
        hexes = ["#%02x%02x%02x" % COLOURS[c] for c in colours]
        inner = ["env", "-u", "QT_QPA_PLATFORMTHEME", "-u", "HYPRLAND_INSTANCE_SIGNATURE", "-u", "QT_SCALE_FACTOR",
                 f"XDG_RUNTIME_DIR={NEST_XDG}", f"WAYLAND_DISPLAY={NEST_WL}",
                 f"DBUS_SESSION_BUS_ADDRESS=unix:path={NEST_XDG}/bus", "QT_QPA_PLATFORM=wayland",
                 "QT_NO_XDG_DESKTOP_PORTAL=1", "GSETTINGS_BACKEND=memory", "GIO_USE_VFS=local",
                 "QT_WAYLAND_DISABLE_WINDOWDECORATION=1", "QT_ENABLE_HIGHDPI_SCALING=1",
                 "QT_SCALE_FACTOR_ROUNDING_POLICY=PassThrough", "/usr/lib64/qt6/bin/qml",
                 os.path.join(QML, "win.qml"), "--", str(w), str(h), *hexes, *(["fixed"] if fixed else [])]
        with open(evidence(f"{CASE}-client-{name}.log"), "w") as log:
            subprocess.Popen(["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=1G", "-p", "MemorySwapMax=0",
                              "-p", "CPUQuota=100%", f"--unit={unit}", *inner], stdout=log, stderr=subprocess.STDOUT)
        self.units.append(unit)
        time.sleep(3.0)

    def stop_all(self):
        for u in self.units:
            subprocess.run(["systemctl", "--user", "stop", f"{u}.scope"], capture_output=True)
        self.units.clear()
        time.sleep(1.0)

    def clients(self):
        return json.loads(self.hc("-j", "clients"))

    def status(self, prefix):
        try:
            with open(NEST_STATUS, encoding="utf-8", errors="replace") as f:
                lines = [ln for ln in f.read().splitlines() if ln.startswith(prefix)]
            return lines[-1] if lines else ""
        except OSError:
            return ""

    def open3d(self, windows, render_scale=2.0, sharp=True):
        render = ("{scale=%g, msaa=4, fxaa=false, sharpen=0.25, texture_supersample=true, anisotropy=16, stills=true, "
                  "texel_sharp=%s}" % (render_scale, "true" if sharp else "false"))
        self.ev('local p=assert(hl.plugin.hypr3d); p.config({world={monitor="HEADLESS-DESK", grid=false}, '
                'render=%s, windows=%s}); p.open()' % (render, windows))
        time.sleep(4.0)

    def close3d(self):
        self.ev("if hl.plugin.hypr3d.active() then hl.plugin.hypr3d.close() end")
        time.sleep(2.0)

    def shot(self, name):
        from PIL import Image
        path = evidence(f"{CASE}-{name}.png")
        subprocess.run(["grim", "-o", "HEADLESS-DESK", path], env=self.env, check=True, timeout=20)
        os.chmod(path, 0o644)
        return Image.open(path).convert("RGB")

    def look(self, name, colours, size, s, timeout=10.0):
        """measure() on fresh shots until both markers show (or the timeout); size() = the client's box now."""
        end = time.monotonic() + timeout
        while True:
            r = measure(self.shot(name), colours, *size(), s)
            if "error" not in r or time.monotonic() > end:
                return r
            time.sleep(0.7)


def centroids(im, names):
    """Centroid (output px) of each marker colour's solid area. A 7x7 erosion keeps only areas at least 7 px across,
    so thin coloured lines (the nested compositor's warning banners) never count; a square's centroid is unchanged."""
    from PIL import ImageChops, ImageFilter
    bands = im.split()
    out = {}
    for n in names:
        mask = None
        for band, on in zip(bands, COLOURS[n]):
            m = band.point(lambda v, on=on: 255 if (v > 200 if on else v < 70) else 0)
            mask = m if mask is None else ImageChops.multiply(mask, m)
        mask = mask.filter(ImageFilter.MinFilter(7))
        box = mask.getbbox()
        if not box:
            out[n] = None
            continue
        sx = sy = cnt = 0
        crop = mask.crop(box)
        cw = crop.size[0]
        for i, v in enumerate(getattr(crop, "get_flattened_data", crop.getdata)()):
            if v:
                sx += i % cw
                sy += i // cw
                cnt += 1
        out[n] = (box[0] + sx / cnt + 0.5, box[1] + sy / cnt + 0.5) if cnt > 20 else None
    return out


def measure(im, colours, w, h, s):
    cs = centroids(im, colours)
    A, B = cs[colours[0]], cs[colours[1]]
    if not A or not B:
        return {"error": f"markers {colours} not found: {cs}"}
    kx, ky = (B[0] - A[0]) / (w - 60), (B[1] - A[1]) / (h - 60)
    px, (W, H) = im.load(), im.size
    x0 = A[0] + 40 * kx
    x1 = A[0] + 140 * kx
    y0 = A[1]
    vals = sorted((0.2126 * px[x, int(y0)][0] + 0.7152 * px[x, int(y0)][1] + 0.0722 * px[x, int(y0)][2]) / 255
                  for x in range(int(x0), int(x1)) if 0 <= x < W and 0 <= int(y0) < H)
    g = round(vals[int(0.95 * (len(vals) - 1))] - vals[int(0.05 * (len(vals) - 1))], 3) if vals else None
    return {"M": (kx / s, ky / s), "centre": ((A[0] + B[0]) / 2, (A[1] + B[1]) / 2), "grating": g,
            "glyph_px": ink_heights(im, A, kx, ky)}


TEXT_BANDS = ((58, 78), (78, 98), (98, 124))  # win.qml's three text lines (12, 12, 16 px), window-local logical rows


def ink_heights(im, a, kx, ky):
    """On-screen ink height (output px) of each text line of the test window: the rows with a dark pixel inside the
    line's band (window-local logical px mapped through the marker-derived scale). The glyph size the operator reads:
    at M = 1 it equals the flat desktop's."""
    px, (W, H) = im.load(), im.size
    out = []
    for y0, y1 in TEXT_BANDS:
        sy0, sy1 = int(round(a[1] + (y0 - 30) * ky)), int(round(a[1] + (y1 - 30) * ky))
        sx0, sx1 = int(round(a[0] + (10 - 30) * kx)), int(round(a[0] + (300 - 30) * kx))
        rows = [y for y in range(max(0, sy0), min(H, sy1))
                if any((0.2126 * px[x, y][0] + 0.7152 * px[x, y][1] + 0.0722 * px[x, y][2]) < 127
                       for x in range(max(0, sx0), min(W, sx1)))]
        out.append(rows[-1] - rows[0] + 1 if rows else 0)
    return out


RETINA = ('{pixel_exact=true, spawn_distance=5.0, spawn_size={x=%d, y=%d}, fit="resize", fit_margin=8, '
          'pixel_snap=true, cascade=32, min_magnification=%.4f}')
STOCK = '{pixel_exact=false, window_scale=0.5, spawn_distance=5.0, fit="off", pixel_snap=false, cascade=0}'
# per case: a typical window, an oversized one (logical px)
WINDOWS = {"landscape": ((900, 560), (2400, 1400)), "qhd125": ((900, 560), (2600, 1500)),
           "phone": ((300, 500), (600, 1000)), "portrait": ((900, 560), (1400, 2200))}


@unittest.skipUnless(NESTED, "nested Hypr3D session not provided")
class Nested(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.n = Nest()
        m = cls.n.mon
        cls.s = float(m["scale"])
        cls.transform = int(m["transform"])
        data = ssot()
        mobile = CASE == "phone"
        cls.o = oracle(data, m["width"], m["height"], cls.s, cls.transform, tuple(m["reserved"]), mobile)
        l, t, r, b = m["reserved"]
        wl, hl = cls.o["logical"]
        # the work-area centre in output px (the 2D `center` rule's spot)
        cls.centre = ((l + (wl - l - r) / 2) * cls.s, (t + (hl - t - b) / 2) * cls.s)
        cls.retina = RETINA % (cls.o["spawn"][0], cls.o["spawn"][1], cls.o["m_min"])
        cls.small, cls.big = WINDOWS.get(CASE.split("-")[0], WINDOWS["landscape"])
        cls.results = {"case": CASE, "monitor": {k: m[k] for k in ("width", "height", "scale", "transform", "reserved")},
                       "oracle": {k: v for k, v in cls.o.items()}}

    @classmethod
    def tearDownClass(cls):
        cls.n.stop_all()
        with open(evidence(f"{CASE}-results.json"), "w") as f:
            json.dump(cls.results, f, indent=2, default=str)
        os.chmod(evidence(f"{CASE}-results.json"), 0o644)

    def setUp(self):
        self.n.plugin(NEW_SO)

    def tearDown(self):
        try:
            self.n.close3d()
        except AssertionError:
            pass
        self.n.stop_all()

    def assertOne(self, res, tol=0.003, what=""):
        self.assertNotIn("error", res, what)
        self.assertAlmostEqual(res["M"][0], 1.0, delta=tol, msg=f"{what} M x {res}")
        self.assertAlmostEqual(res["M"][1], 1.0, delta=tol, msg=f"{what} M y {res}")

    def assertCentred(self, res, centre, tol=1.5, what=""):
        self.assertAlmostEqual(res["centre"][0], centre[0], delta=tol, msg=f"{what} centre x {res}")
        self.assertAlmostEqual(res["centre"][1], centre[1], delta=tol, msg=f"{what} centre y {res}")

    def size_of(self, colour="red"):
        hexv = "#%02x%02x%02x" % COLOURS[colour]
        return [c["size"] for c in self.n.clients() if c["title"].startswith("h3r-win") and c["title"].endswith(hexv)]

    def test_stock_placement_is_not_one_to_one(self):
        """Negative control: the plugin's own defaults (0.5 / 5 m) on this output."""
        w, h = stock_size(self.o, *self.small)
        self.n.client("stock", w, h)
        flat = self.n.look("stock-2d", ("red", "blue"), lambda: (w, h), self.s)
        self.n.open3d(STOCK)
        r = self.n.look("stock-3d", ("red", "blue"), lambda: (w, h), self.s)
        want = magnification(0.5, 5.0, self.o["logical"][1])
        self.results["stock"] = {"2d": flat, "3d": r}
        self.assertNotIn("error", r)
        self.assertAlmostEqual(r["M"][0], want, delta=0.003)
        self.assertGreater(abs(r["M"][0] - 1.0), 0.05)
        # the text the operator reads is not the flat desktop's size
        self.assertGreater(abs(sum(r["glyph_px"]) / sum(flat["glyph_px"]) - 1.0), 0.04, (flat, r))

    def test_enter_opens_at_one_to_one_on_the_work_area_centre(self):
        w, h = self.small
        self.n.client("enter", w, h)
        flat = self.n.look("enter-2d", ("red", "blue"), lambda: (w, h), self.s)
        self.n.open3d(self.retina)
        r = self.n.look("enter-3d", ("red", "blue"), lambda: (w, h), self.s)
        self.results["enter"] = {"2d": flat, "3d": r, "status": self.n.status("spawn:")}
        self.assertOne(r, what="enter")
        self.assertCentred(r, self.centre, what="enter")
        self.assertCentred(flat, self.centre, what="2d (the center rule)")  # the room opens where 2D put it
        # Retina text: every line's glyphs at the flat desktop's on-screen size (+-1 px), none smaller
        self.assertTrue(all(g > 0 for g in flat["glyph_px"]), flat)
        for a, b in zip(r["glyph_px"], flat["glyph_px"]):
            self.assertLessEqual(abs(a - b), 1, (r["glyph_px"], flat["glyph_px"]))
        st = self.n.status("spawn:")
        self.assertIn(" exact=1 ", st)
        self.assertAlmostEqual(float(re.search(r" scale=([0-9.]+)", st).group(1)), self.o["S"], places=4)

    def test_spawn_box_and_cascade_in_the_room(self):
        w, h = self.small
        self.n.open3d(self.retina)
        self.n.client("b", w, h)
        time.sleep(1.0)
        rb = self.n.look("spawn-b", ("red", "blue"), lambda: self.size_of("red")[0], self.s)
        size_b = self.size_of("red")[0]
        self.n.client("c", w, h, ("green", "magenta"))
        time.sleep(1.5)
        rc = self.n.look("spawn-c", ("green", "magenta"), lambda: self.size_of("green")[0], self.s)
        size_c = self.size_of("green")[0]
        self.results["spawn"] = {"b": rb, "c": rc, "sizes": [size_b, size_c], "status": self.n.status("spawn:")}
        # the box the room asked for: the spawn cells clamped into the fit area (a client may keep its own)
        self.assertTrue(all(v <= f + 0.5 for v, f in zip(size_b, self.o["fit"])), size_b)
        self.assertOne(rb, what="b")
        self.assertCentred(rb, self.centre, what="b")
        self.assertOne(rc, what="c (cascaded, M-compensated)")
        # one cascade step right/down while it still fits the fit area (a mobile "fill" box has no room: it stacks)
        room = min(self.o["fit"][0] - size_c[0], self.o["fit"][1] - size_c[1])
        step = 32 * self.s if room >= 32 else 0.0
        self.assertCentred(rc, (rb["centre"][0] + step, rb["centre"][1] + step), what="c")

    def test_upstream_depth_nudge_magnifies(self):
        """Negative control for the cascade: cascade = 0 keeps upstream's 0.15 m nudge (M = d / (d - 0.15))."""
        w, h = self.small
        self.n.open3d(self.retina.replace("cascade=32", "cascade=0"))
        self.n.client("b0", w, h)
        time.sleep(1.0)
        rb = self.n.look("nudge-b", ("red", "blue"), lambda: self.size_of("red")[0], self.s)
        self.n.client("c0", w, h, ("green", "magenta"))
        time.sleep(1.5)
        rc = self.n.look("nudge-c", ("green", "magenta"), lambda: self.size_of("green")[0], self.s)
        self.results["nudge"] = {"b": rb, "c": rc}
        self.assertAlmostEqual(rc["M"][0], 5.0 / 4.85, delta=0.004)
        self.assertCentred(rc, rb["centre"], tol=2.0, what="c0")

    def test_oversized_window_is_resized_into_the_work_area(self):
        w, h = self.big
        self.n.client("big", w, h)
        self.n.open3d(self.retina)
        r = self.n.look("big-3d", ("red", "blue"), lambda: self.size_of("red")[0], self.s)
        size = self.size_of("red")[0]
        self.results["big"] = {"size": size, "3d": r, "status": self.n.status("spawn:")}
        self.assertEqual(tuple(size), fit_window(self.o, w, h)[0])
        self.assertOne(r, what="big")
        self.assertCentred(r, self.centre, what="big")

    def test_refusing_client_is_pushed_back_never_cropped(self):
        w, h = self.big
        self.n.client("fixed", w, h, fixed=True)
        self.n.open3d(self.retina)
        r = self.n.look("fixed-3d", ("red", "blue"), lambda: self.size_of("red")[0], self.s)
        size = self.size_of("red")[0]
        _, want = fit_window(self.o, w, h, min_size=(w, h))
        self.results["fixed"] = {"size": size, "3d": r, "want_M": want, "status": self.n.status("spawn:")}
        self.assertEqual(tuple(size), (w, h))
        self.assertAlmostEqual(r["M"][0], want, delta=0.004)
        self.assertAlmostEqual(r["M"][1], want, delta=0.004)
        self.assertCentred(r, self.centre, tol=2.0, what="fixed")
        # never cropped: the whole window fits the fit area on screen
        self.assertLessEqual(w * r["M"][0], self.o["fit"][0] + 2)
        self.assertLessEqual(h * r["M"][1], self.o["fit"][1] + 2)
        self.assertIn(" pushed=1 ", self.n.status("spawn:"))

    @unittest.skipUnless(CASE == "landscape", "1-px detail is measured on the landscape output")
    def test_texel_sharp_keeps_one_pixel_detail_at_render_scale_2(self):
        out = {}
        for sharp in (True, False):
            self.n.plugin(NEW_SO)  # no pose memory from the other variant (window addresses get reused)
            self.n.client(f"g{int(sharp)}", 900, 560)
            self.n.open3d(self.retina, render_scale=2.0, sharp=sharp)
            out[sharp] = self.n.look(f"grating-r2-sharp{int(sharp)}", ("red", "blue"), lambda: (900, 560), self.s)
            self.n.close3d()
            self.n.stop_all()
        self.results["texel_sharp"] = out
        self.assertGreaterEqual(out[True]["grating"], 0.9)
        self.assertAlmostEqual(out[False]["grating"], 0.5, delta=0.1)  # negative control: bilinear-then-box

    @unittest.skipUnless(CASE == "landscape", "1-px detail is measured on the landscape output")
    def test_pixel_snap_aligns_an_odd_sized_window_at_render_scale_1(self):
        out = {}
        for snap in (True, False):
            self.n.plugin(NEW_SO)  # no pose memory from the other variant (window addresses get reused)
            self.n.client(f"o{int(snap)}", 901, 561)
            self.n.open3d(self.retina.replace("pixel_snap=true", f"pixel_snap={'true' if snap else 'false'}"),
                          render_scale=1.0)
            time.sleep(1.0)
            out[snap] = self.n.look(f"grating-r1-odd-snap{int(snap)}", ("red", "blue"), lambda: (901, 561), self.s)
            self.n.close3d()
            self.n.stop_all()
        self.results["pixel_snap"] = out
        self.assertGreaterEqual(out[True]["grating"], 0.9)
        self.assertLessEqual(out[False]["grating"], 0.1)  # negative control: half-pixel phase


@unittest.skipUnless(NESTED, "nested Hypr3D session not provided")
class NestedStatus(unittest.TestCase):
    def test_status_numbers_on_rotated_outputs(self):
        """The plugin's live numbers on any output, rotated ones included: scale = d / d1 and the fit area."""
        n = Nest()
        m = n.mon
        o = oracle(ssot(), m["width"], m["height"], float(m["scale"]), int(m["transform"]), tuple(m["reserved"]))
        n.plugin(NEW_SO)
        try:
            n.open3d(RETINA % (o["spawn"][0], o["spawn"][1], o["m_min"]))
            st = n.status("spawn:")
        finally:
            n.close3d()
        self.assertIn(" exact=1 ", st)
        got = float(re.search(r" scale=([0-9.]+)", st).group(1))
        self.assertAlmostEqual(got, o["S"], places=4)
        area = re.search(r" area=([0-9.-]+),([0-9.-]+),([0-9.]+)x([0-9.]+)", st)
        self.assertIsNotNone(area, st)
        self.assertAlmostEqual(float(area.group(3)), o["fit"][0], places=3)
        self.assertAlmostEqual(float(area.group(4)), o["fit"][1], places=3)


@unittest.skipUnless(NESTED and OLD_SO, "nested Hypr3D session + MIOS_H3R_OLD_SO (a plugin without the transform patch)")
class NestedTransform(unittest.TestCase):
    """Rotated / flipped outputs: with hypr3d-mios-transform.patch the room is upright and a window opens at its 2D size
    on the monitor centre; a plugin without it (the negative control) draws the room through the panel's buffer
    orientation, so the window is not there."""

    def test_rotated_output_room_is_upright(self):
        n = Nest()
        m = n.mon
        if int(m["transform"]) == 0:
            self.skipTest("normal output: nothing to rotate")
        s = float(m["scale"])
        o = oracle(ssot(), m["width"], m["height"], s, int(m["transform"]), tuple(m["reserved"]))
        w, h = WINDOWS.get(CASE.split("-")[0], WINDOWS["landscape"])[0]
        # the keys every plugin reads (what mios-gen sends an unpatched plugin): 1:1 at the monitor centre
        keys = "{window_scale=%.6f, spawn_distance=%.4f}" % (o["S"], o["dist"])
        centre = (o["logical"][0] / 2 * s, o["logical"][1] / 2 * s)
        res = {}
        try:
            for tag, so in (("patched", NEW_SO), ("unpatched", OLD_SO)):
                n.plugin(so)
                n.client(f"rot-{tag}", w, h)
                n.open3d(keys)
                res[tag] = n.look(f"transform-{tag}", ("red", "blue"), lambda: (w, h), s, timeout=6.0)
                res[tag + "_render"] = n.status("render:")
                n.close3d()
                n.stop_all()
        finally:
            n.stop_all()
        with open(evidence(f"{CASE}-transform.json"), "w") as f:
            json.dump(res, f, indent=2)
        good, bad = res["patched"], res["unpatched"]
        self.assertNotIn("error", good, good)
        self.assertAlmostEqual(good["M"][0], 1.0, delta=0.003, msg=good)
        self.assertAlmostEqual(good["M"][1], 1.0, delta=0.003, msg=good)
        self.assertAlmostEqual(good["centre"][0], centre[0], delta=1.5, msg=good)
        self.assertAlmostEqual(good["centre"][1], centre[1], delta=1.5, msg=good)
        # negative control: without the patch the window is missing, mis-sized or off its place
        wrong = ("error" in bad or abs(bad["M"][0] - 1.0) > 0.05 or abs(bad["M"][1] - 1.0) > 0.05
                 or math.dist(bad["centre"], centre) > 20)
        self.assertTrue(wrong, f"the unpatched plugin showed the window upright at 1:1: {bad}")


@unittest.skipUnless(NESTED and MIOS_GEN, "nested Hypr3D session + MIOS_GEN not provided")
class NestedToggle(unittest.TestCase):
    """The real `mios-gen hyprland-3d-toggle` (the room toggle, SUPER+F12) against the nest, with the staged SSOT."""

    def toggle(self, vendor):
        env = hermetic_env(vendor)
        env.update(XDG_RUNTIME_DIR=NEST_XDG, HYPRLAND_INSTANCE_SIGNATURE=NEST_SIG, WAYLAND_DISPLAY=NEST_WL)
        return subprocess.run([MIOS_GEN, "hyprland-3d-toggle"], env=env, capture_output=True, text=True, timeout=60)

    def run_case(self, vendor, tag, stock=False):
        n = Nest()
        s = float(n.mon["scale"])
        n.plugin(NEW_SO)
        w, h = WINDOWS.get(CASE.split("-")[0], WINDOWS["landscape"])[0]
        if stock:
            m = n.mon
            w, h = stock_size(oracle(ssot(), m["width"], m["height"], s, int(m["transform"]), tuple(m["reserved"])), w, h)
        try:
            n.client(f"t{tag}", w, h)
            p = self.toggle(vendor)
            self.assertEqual(p.returncode, 0, p.stderr)
            time.sleep(4.0)
            self.assertEqual(n.hc("eval", "assert(hl.plugin.hypr3d.active(), 'room closed')"), "ok")
            r = n.look(f"toggle-{tag}", ("red", "blue"), lambda: (w, h), s)
            st = n.status("spawn:")
            self.pacing = n.status("pacing:")
            p2 = self.toggle(vendor)
            self.assertEqual(p2.returncode, 0, p2.stderr)
            time.sleep(3.0)
            self.assertEqual(n.hc("eval", "assert(not hl.plugin.hypr3d.active(), 'room open')"), "ok")
            return r, st, n.mon
        finally:
            n.stop_all()

    def test_toggle_opens_windows_at_one_to_one(self):
        r, st, mon = self.run_case(SSOT, "retina")
        with open(evidence(f"{CASE}-toggle.json"), "w") as f:
            json.dump({"retina": r, "status": st}, f, indent=2)
        self.assertNotIn("error", r)
        self.assertAlmostEqual(r["M"][0], 1.0, delta=0.003)
        self.assertIn(" exact=1 ", st)
        self.assertIn(" fit=resize ", st)
        o = oracle(ssot(), mon["width"], mon["height"], float(mon["scale"]), int(mon["transform"]),
                   tuple(mon["reserved"]), CASE == "phone")
        self.assertIn(f" spawnBox={o['spawn'][0]}x{o['spawn'][1]} ", st)
        # the frame-rate overlay rides the same toggle: its rates still reach the room (nothing here resets them)
        if ssot()["desktop"].get("framerate", {}).get("enabled"):
            rates = dict(re.findall(r" (glass_fps|live_fps)=([0-9.]+)", " " + self.pacing))
            self.assertGreater(float(rates.get("glass_fps", 0)), 0, self.pacing)
            self.assertGreater(float(rates.get("live_fps", 0)), 0, self.pacing)

    def test_toggle_with_the_policy_off_keeps_the_stock_placement(self):
        with tempfile.TemporaryDirectory() as tmp:
            with open(SSOT, encoding="utf-8") as f:
                data = f.read()
            fixed = os.path.join(tmp, "fixed.toml")
            with open(fixed, "w") as f:
                f.write(data.replace('criterion = "retina"', 'criterion = "fixed"'))
            r, st, mon = self.run_case(fixed, "fixed", stock=True)
        want = magnification(0.5, 5.0, oracle(ssot(), mon["width"], mon["height"], float(mon["scale"]),
                                              int(mon["transform"]))["logical"][1])
        self.assertNotIn("error", r)
        self.assertAlmostEqual(r["M"][0], want, delta=0.003)
        self.assertIn(" exact=0 ", st)


# ---- tier 5: the operator's own applications -------------------------------------------------------------------------
# MIOS_H3R_APPS=1 + the nest + MIOS_GEN: the MiOS terminal (alacritty with the MiOS-rendered config, i.e. the SSOT
# [theme.font] GeistMono 12) and the MiOS-Portal browser (the Gecko build, a page with 16 px Geist and 12 px GeistMono
# text) are opened at the flat desktop's default size (the live `mios-placement` rule) and at an oversized size, then
# the room is opened by the real `mios-gen hyprland-3d-toggle`. Measured on screen, against the same window in 2D:
#   (a) texel:pixel at the window centre = 1 / M, M = the on-screen distance of two markers around the window centre
#       in the room over the same distance in 2D (where the compositor draws one buffer texel per output pixel);
#   (b) the ink height (screen px) of every text line, x-height line included, room vs 2D;
#   (c) the window's on-screen rectangle inside the work area minus the fit margin, all four corners visible.
# The old build (MIOS_H3R_OLD_SO + MIOS_H3R_OLD_GEN + MIOS_H3R_OLD_SSOT: the frame-rate stage, i.e. everything but
# this lane; the fixed 0.5 / 5 m placement) is the negative control. Apps run with `env -i` (nothing of the live
# session), the private bus (nothing activatable: no portals), the browser in its own network namespace.
APPS = os.environ.get("MIOS_H3R_APPS", "") == "1"
OLD_GEN = os.environ.get("MIOS_H3R_OLD_GEN", "")
OLD_SSOT = os.environ.get("MIOS_H3R_OLD_SSOT", "")
GECKO = os.environ.get("MIOS_H3R_GECKO", "/usr/lib/mios/portal/mios-portal")
ALACRITTY_MIOS = os.environ.get("MIOS_H3R_ALACRITTY_CONFIG", "/etc/skel/.config/alacritty/alacritty.toml")
LIVE_LUA = "/usr/share/mios/hyprland/hyprland.lua"
APP_COLOURS = {"red": (255, 0, 0), "blue": (0, 0, 255), "yellow": (255, 255, 0), "cyan": (0, 255, 255),
               "magenta": (255, 0, 255), "green": (0, 255, 0)}
CORNERS = ("yellow", "cyan", "magenta", "green")  # top-left, top-right, bottom-left, bottom-right
APP_BIG = {"landscape": (2400, 1400), "portrait": (1400, 2200), "phone": (600, 1000)}

# The MiOS-Portal's floating chat panel and Zen sidebar are kept closed (prefs): they float over the page and would
# cover the markers on a narrow window; the browser keeps the MiOS autoconfig, theme and fonts otherwise.
# Text bands below the red marker, in units of the marker's on-screen height / width (so one layout serves 2D and
# any magnification): (name, top, bottom), and the band's width.
TERM_BANDS = (("A 12pt", 1.5, 2.0), ("x-height 12pt", 2.0, 2.5), ("C 12pt", 2.5, 3.0))   # red = 4 cells x 2 rows
TERM_BAND_W = 5.0
PAGE_BANDS = (("A 16px", 1.25, 1.85), ("x-height 16px", 2.0, 2.6), ("C 12px mono", 2.75, 3.2))  # red = 40x40 CSS px
PAGE_BAND_W = 7.0

TERM_DRAW = r"""#!/bin/bash
# Hypr3D retina test card for the MiOS terminal: two markers around the grid centre (red 4x2 cells, blue 4x2 cells
# 24 cells right and 7 rows down), three text rows under the red one, a 2x1 marker in each corner; redrawn on resize.
printf '\e[?25l'
at() { printf '\e[%d;%dH' "$1" "$2"; }
blk() { local i; for ((i = 0; i < $4; i++)); do at $(($1 + i)) "$2"; printf '\e[48;2;%d;%d;%dm%*s\e[0m' "$5" "$6" "$7" "$3" ''; done; }
draw() {
  read -r R C < <(stty size)
  cr=$((R / 2)); cc=$((C / 2))
  printf '\e[0m\e[2J'
  blk $((cr - 4)) $((cc - 14)) 4 2 255 0 0
  blk $((cr + 3)) $((cc + 10)) 4 2 0 0 255
  at $((cr - 1)) $((cc - 14)); printf 'MiOS Hypr3D 0123 xhg'
  at "$cr" $((cc - 14)); printf 'xxxx vvvv wwww zzzz'
  at $((cr + 1)) $((cc - 14)); printf 'The quick brown fox'
  blk 1 1 2 1 255 255 0
  blk 1 $((C - 1)) 2 1 0 255 255
  blk "$R" 1 2 1 255 0 255
  blk "$R" $((C - 1)) 2 1 0 255 0
}
trap draw WINCH
draw
while :; do sleep 1 & wait $!; done
"""

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>h3r-page</title><style>
html, body { margin: 0; height: 100%; overflow: hidden; background: #fff; color: #000; }
div, canvas { position: fixed; }
.m { width: 40px; height: 40px; } .c { width: 12px; height: 12px; }
.t { white-space: nowrap; font: 16px/24px "Geist"; } .mono { font: 12px/18px "GeistMono Nerd Font Mono"; }
</style></head><body>
<div id="r" class="m" style="background:#f00"></div><div id="b" class="m" style="background:#00f"></div>
<canvas id="g"></canvas>
<div id="la" class="t">MiOS Hypr3D 0123 xhg</div><div id="lb" class="t">xxxx vvvv wwww zzzz</div>
<div id="lc" class="t mono">The quick brown fox jumps</div>
<div class="c" style="left:0;top:0;background:#ff0"></div><div class="c" style="right:0;top:0;background:#0ff"></div>
<div class="c" style="left:0;bottom:0;background:#f0f"></div><div class="c" style="right:0;bottom:0;background:#0f0"></div>
<script>
// everything on whole device pixels around the viewport centre: red 40x40 at (-140,-110), blue at (+100,+70),
// a 1-device-pixel grating right of red, text rows 50 / 80 / 110 CSS px under red's top
function put(id, x, y) { const e = document.getElementById(id); e.style.left = x + "px"; e.style.top = y + "px"; }
function lay() {
  const d = window.devicePixelRatio || 1, snap = v => Math.round(v * d) / d;
  const cx = snap(Math.floor(innerWidth / 2)), cy = snap(Math.floor(innerHeight / 2));
  put("r", cx - 140, cy - 110); put("b", cx + 100, cy + 70);
  put("la", cx - 140, cy - 60); put("lb", cx - 140, cy - 30); put("lc", cx - 140, cy);
  const g = document.getElementById("g"); put("g", cx - 90, cy - 110);
  g.style.width = "120px"; g.style.height = "40px"; g.width = Math.round(120 * d); g.height = Math.round(40 * d);
  const k = g.getContext("2d"); k.fillStyle = "#fff"; k.fillRect(0, 0, g.width, g.height); k.fillStyle = "#000";
  for (let x = 0; x < g.width; x += 2) k.fillRect(x, 0, 1, g.height);
  document.title = "h3r-page " + innerWidth + "x" + innerHeight + "@" + d;
}
addEventListener("resize", lay); matchMedia("(resolution: 1dppx)").addEventListener("change", lay); lay();
</script></body></html>
"""

GECKO_PREFS = """user_pref("browser.startup.homepage_override.mstone", "ignore");
user_pref("browser.aboutwelcome.enabled", false);
user_pref("browser.sessionstore.resume_from_crash", false);
user_pref("browser.shell.checkDefaultBrowser", false);
user_pref("datareporting.policy.dataSubmissionEnabled", false);
user_pref("toolkit.telemetry.enabled", false);
user_pref("widget.use-xdg-desktop-portal.file-picker", 0);
user_pref("widget.use-xdg-desktop-portal.mime-handler", 0);
user_pref("widget.use-xdg-desktop-portal.settings", 0);
user_pref("widget.use-xdg-desktop-portal.location", 0);
user_pref("widget.use-xdg-desktop-portal.open-uri", 0);
user_pref("widget.use-xdg-desktop-portal.native-messaging", 0);
user_pref("general.smoothScroll", false);
user_pref("ui.prefersReducedMotion", 1);
user_pref("layout.css.devPixelsPerPx", "-1.0");
user_pref("mios.portal.chat.autohide", false);
user_pref("sidebar.revamp", false);
user_pref("sidebar.verticalTabs", false);
"""


def marker_boxes(im, names):
    """bbox + centroid (output px) of each marker colour's solid area (an opening keeps areas >= 5 px across)."""
    from PIL import ImageChops, ImageFilter
    bands = im.split()
    out = {}
    for n in names:
        mask = None
        for band, on in zip(bands, APP_COLOURS[n]):
            m = band.point(lambda v, on=on: 255 if (v > 200 if on else v < 70) else 0)
            mask = m if mask is None else ImageChops.multiply(mask, m)
        mask = mask.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MaxFilter(5))
        box = mask.getbbox()
        if not box:
            out[n] = None
            continue
        crop = mask.crop(box)
        cw = crop.size[0]
        sx = sy = cnt = 0
        for i, v in enumerate(getattr(crop, "get_flattened_data", crop.getdata)()):
            if v:
                sx += i % cw
                sy += i // cw
                cnt += 1
        fill = cnt / ((box[2] - box[0]) * (box[3] - box[1]))
        out[n] = {"box": box, "c": (box[0] + sx / cnt + 0.5, box[1] + sy / cnt + 0.5), "fill": round(fill, 3)} \
            if cnt > 20 and fill > 0.8 else None
    return out


def white_box(im):
    """bbox of the near-white area (the terminal window: white background, dark text, markers)."""
    from PIL import ImageChops, ImageFilter
    mask = None
    for band in im.split():
        m = band.point(lambda v: 255 if v > 230 else 0)
        mask = m if mask is None else ImageChops.multiply(mask, m)
    return mask.filter(ImageFilter.MinFilter(5)).filter(ImageFilter.MaxFilter(5)).getbbox()


CROSSHAIR_PX = 14  # the room's aiming crosshair (Hypr3D drawCrosshair: 10 px arms + outline) at the output centre


def app_measure(im, bands, band_w, grating=False):
    """(a) the two centre markers, (b) the ink height of each text band (the room's crosshair square at the output
    centre is left out, in 2D too), the grating's contrast, (c) the window edges from the corner markers."""
    mk = marker_boxes(im, ("red", "blue") + CORNERS)
    r, b = mk["red"], mk["blue"]
    if not r or not b:
        return {"error": f"centre markers not found: red {r} blue {b}"}
    px, (W, H) = im.load(), im.size
    rw, rh = r["box"][2] - r["box"][0], r["box"][3] - r["box"][1]
    lum = lambda x, y: (0.2126 * px[x, y][0] + 0.7152 * px[x, y][1] + 0.0722 * px[x, y][2]) / 255
    cross = lambda x, y: abs(x - W / 2) <= CROSSHAIR_PX and abs(y - H / 2) <= CROSSHAIR_PX
    glyph = {}
    for name, t0, t1 in bands:
        y0, y1 = int(round(r["box"][1] + t0 * rh)), int(round(r["box"][1] + t1 * rh))
        x0, x1 = r["box"][0], int(round(r["box"][0] + band_w * rw))
        rows = [y for y in range(max(0, y0), min(H, y1))
                if any(lum(x, y) < 0.5 and not cross(x, y) for x in range(max(0, x0), min(W, x1)))]
        glyph[name] = rows[-1] - rows[0] + 1 if rows else 0
    out = {"red": r["c"], "blue": b["c"], "delta": (b["c"][0] - r["c"][0], b["c"][1] - r["c"][1]),
           "mid": ((r["c"][0] + b["c"][0]) / 2, (r["c"][1] + b["c"][1]) / 2), "marker_px": (rw, rh), "glyph_px": glyph,
           "corners": {n: (mk[n]["box"] if mk[n] else None) for n in CORNERS}}
    if grating:  # the 1-device-px grating right of red: luminance spread (p95 - p5) along red's centre row
        y = int(r["c"][1])
        xs = range(int(r["box"][0] + 1.5 * rw), int(r["box"][0] + 4.0 * rw))
        vals = sorted(lum(x, y) for x in xs if 0 <= x < W and 0 <= y < H)
        out["grating"] = round(vals[int(0.95 * (len(vals) - 1))] - vals[int(0.05 * (len(vals) - 1))], 3) if vals else None
    # each edge from the corner markers on it (an app's own overlay may cover one corner; one marker per edge is
    # enough to place the edge): left = top-left / bottom-left, top = top-left / top-right, ...
    c = out["corners"]
    edge = lambda names, i, f: (f(c[n][i] for n in names if c[n]) if any(c[n] for n in names) else None)
    e = (edge(("yellow", "magenta"), 0, min), edge(("yellow", "cyan"), 1, min), edge(("cyan", "green"), 2, max),
         edge(("magenta", "green"), 3, max))
    out["content_edges"] = e
    out["content_rect"] = e if all(v is not None for v in e) else None
    out["corners_found"] = sum(1 for n in CORNERS if c[n])
    return out


def size_tag(flat):
    return "x".join(map(str, flat.get("client_size") or ()))


def placement_size_rule():
    """The flat desktop's default window size: the live `mios-placement` rule's size expression (read-only)."""
    try:
        with open(LIVE_LUA, encoding="utf-8") as f:
            m = re.search(r'\["name"\] = "mios-placement".*?\["size"\] = "([^"]+)"', f.read())
        return m.group(1) if m else None
    except OSError:
        return None


@unittest.skipUnless(NESTED and APPS and MIOS_GEN and OLD_SO and OLD_GEN and OLD_SSOT,
                     "nested session + MIOS_H3R_APPS=1 + MIOS_GEN + the old build (MIOS_H3R_OLD_SO/_GEN/_SSOT)")
class NestedApps(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.n = Nest()
        m = cls.n.mon
        cls.s = float(m["scale"])
        cls.o = oracle(ssot(), m["width"], m["height"], cls.s, int(m["transform"]), tuple(m["reserved"]), CASE == "phone")
        margin = resolve(ssot(), spawn_table(ssot())["fit_margin_px"])
        l, t, _r, _b = m["reserved"]
        fx, fy = (l + margin) * cls.s, (t + margin) * cls.s
        cls.fit = (fx, fy, fx + cls.o["fit"][0] * cls.s, fy + cls.o["fit"][1] * cls.s)   # output px
        cls.out_px = (cls.o["logical"][0] * cls.s, cls.o["logical"][1] * cls.s)
        cls.stock_m = magnification(0.5, 5.0, cls.o["logical"][1])
        cls.tmp = tempfile.mkdtemp(prefix="h3r-apps-", dir=os.path.join(QML, "run"))
        os.chmod(cls.tmp, 0o755)
        with open(os.path.join(cls.tmp, "draw.sh"), "w") as f:
            f.write(TERM_DRAW)
        with open(os.path.join(cls.tmp, "page.html"), "w") as f:
            f.write(PAGE)
        with open(os.path.join(cls.tmp, "alacritty.toml"), "w") as f:
            # the MiOS config (font family + size from [theme.font]) imported unchanged; only what the measurement
            # needs on top: opaque, black on white, no decorations, a steady cursor
            f.write('[general]\nimport = ["%s"]\nlive_config_reload = false\n\n[window]\nopacity = 1.0\n'
                    'decorations = "None"\n\n[colors.primary]\nbackground = "#ffffff"\nforeground = "#000000"\n\n'
                    '[cursor]\nstyle = { shape = "Block", blinking = "Never" }\n' % ALACRITTY_MIOS)
        with open(ALACRITTY_MIOS, "rb") as f:
            cls.term_font = tomllib.load(f)["font"]
        size = placement_size_rule()
        big = APP_BIG.get(CASE.split("-")[0], APP_BIG["landscape"])
        cls.rules = [("h3r-app-typ", "^(h3r-term|h3r-browser)$", size or "%d %d" % cls.o["spawn"]),
                     ("h3r-app-big", "^(h3r-term-big|h3r-browser-big)$", "%d %d" % big)]
        cls.results = {"case": CASE, "monitor": {k: m[k] for k in ("width", "height", "scale", "transform", "reserved")},
                       "logical": cls.o["logical"], "fit_box_px": cls.fit, "stock_M": cls.stock_m,
                       "placement_rule": size, "big": big, "terminal_font": cls.term_font,
                       "page_fonts": {"A": "16px Geist", "x-height": "16px Geist", "C": "12px GeistMono Nerd Font Mono"}}

    @classmethod
    def tearDownClass(cls):
        cls.n.stop_all()
        with open(evidence(f"{CASE}-apps.json"), "w") as f:
            json.dump(cls.results, f, indent=2, default=str)
        os.chmod(evidence(f"{CASE}-apps.json"), 0o644)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def tearDown(self):
        try:
            self.n.ev("if hl.plugin.hypr3d and hl.plugin.hypr3d.active() then hl.plugin.hypr3d.close() end")
            time.sleep(2.0)
        except AssertionError:
            pass
        self.n.stop_all()

    # -- the applications --------------------------------------------------------------------------------------------
    def launch(self, kind, cls_name, tag):
        home = tempfile.mkdtemp(prefix=f"home-{tag}-", dir=self.tmp)
        base = ["env", "-i", f"HOME={home}", "PATH=/usr/bin:/bin", "LANG=C.UTF-8", f"XDG_RUNTIME_DIR={NEST_XDG}",
                f"WAYLAND_DISPLAY={NEST_WL}", f"DBUS_SESSION_BUS_ADDRESS=unix:path={NEST_XDG}/bus",
                f"XDG_CONFIG_HOME={home}/.config", f"XDG_CACHE_HOME={home}/.cache", f"XDG_DATA_HOME={home}/.local/share",
                "GSETTINGS_BACKEND=memory", "GIO_USE_VFS=local", "GTK_USE_PORTAL=0", "NO_AT_BRIDGE=1", "GTK_A11Y=none",
                "__EGL_VENDOR_LIBRARY_FILENAMES=/usr/share/glvnd/egl_vendor.d/50_mesa.json"]
        if kind == "term":
            cmd = [*base, "alacritty", "--config-file", os.path.join(self.tmp, "alacritty.toml"), "--class", cls_name,
                   "-e", "bash", os.path.join(self.tmp, "draw.sh")]
            mem = "1G"
        else:
            prof = os.path.join(home, "profile")
            os.makedirs(prof)
            with open(os.path.join(prof, "user.js"), "w") as f:
                f.write(GECKO_PREFS)
            # its own network namespace: the page is a local file and nothing reaches the live host's services
            cmd = ["unshare", "-U", "--map-current-user", "-n", *base, "MOZ_ENABLE_WAYLAND=1",
                   f"MOZ_APP_REMOTINGNAME={cls_name}", "MOZ_CRASHREPORTER_DISABLE=1", "MOZ_CRASHREPORTER_NO_REPORT=1",
                   "MOZ_NO_REMOTE=1", GECKO, "--name", cls_name, "--class", cls_name, "--profile", prof, "--no-remote",
                   "--new-instance", "file://" + os.path.join(self.tmp, "page.html")]
            mem = "2G"
        unit = f"mios-h3r-c-{tag}-{os.getpid()}"
        with open(evidence(f"{CASE}-app-{tag}.log"), "w") as log:
            subprocess.Popen(["systemd-run", "--user", "--scope", "-q", "-p", f"MemoryMax={mem}", "-p", "MemorySwapMax=0",
                              "-p", "CPUQuota=200%", f"--unit={unit}", *cmd], stdout=log, stderr=subprocess.STDOUT)
        os.chmod(evidence(f"{CASE}-app-{tag}.log"), 0o644)
        self.n.units.append(unit)

    def client(self, cls_name):
        c = [c for c in self.n.clients() if c["class"] == cls_name and c["mapped"]]
        return c[0] if c else None

    def window_rect(self, c):
        """The client's rectangle in output px (2D: the compositor draws it at its logical box x scale)."""
        mx, my = self.n.mon["x"], self.n.mon["y"]
        x, y = (c["at"][0] - mx) * self.s, (c["at"][1] - my) * self.s
        return (x, y, x + c["size"][0] * self.s, y + c["size"][1] * self.s)

    def look(self, name, kind, ready=lambda r: True, timeout=12.0):
        end = time.monotonic() + timeout
        bands, bw = (TERM_BANDS, TERM_BAND_W) if kind == "term" else (PAGE_BANDS, PAGE_BAND_W)
        while True:
            r = app_measure(self.n.shot(name), bands, bw, grating=kind == "browser")
            if ("error" not in r and ready(r)) or time.monotonic() > end:
                return r
            time.sleep(0.8)

    def toggle(self, gen, vendor):
        env = hermetic_env(vendor)
        env.update(XDG_RUNTIME_DIR=NEST_XDG, HYPRLAND_INSTANCE_SIGNATURE=NEST_SIG, WAYLAND_DISPLAY=NEST_WL)
        p = subprocess.run([gen, "hyprland-3d-toggle"], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(p.returncode, 0, p.stderr)
        return p

    def run_app(self, kind, size):
        """2D, then the room by the real toggle: new build, then (a fresh window) the old build."""
        cls_name = f"h3r-{kind}" + ("-big" if size == "big" else "")
        res = {}
        for build, so, gen, vendor in (("new", NEW_SO, MIOS_GEN, SSOT), ("old", OLD_SO, OLD_GEN, OLD_SSOT)):
            tag = f"{kind}-{size}-{build}"
            self.n.plugin(so)
            # the size rules and a hidden pointer (its arrow sits on the text in 2D; the room draws its own
            # crosshair, left out of the measurement); a plugin load re-reads the nest's config, so after it
            for name, cls_re, sz in self.rules:
                self.n.ev('hl.window_rule({name = "%s", match = {class = "%s"}, float = true, center = true, '
                          'size = "%s"})' % (name, cls_re, sz))
            self.n.ev('hl.config({cursor = {invisible = true}})')
            self.launch(kind, cls_name, tag)
            end = time.monotonic() + 45
            while not self.client(cls_name) and time.monotonic() < end:
                time.sleep(0.5)
            c = self.client(cls_name)
            self.assertIsNotNone(c, f"{cls_name} did not map")
            time.sleep(2.0 if kind == "term" else 4.0)
            flat = self.look(f"apps-{tag}-2d", kind, timeout=30.0)
            c = self.client(cls_name)
            flat["window_rect"] = self.window_rect(c)
            flat["client_size"] = c["size"]
            flat["title"] = c["title"]
            if kind == "term":
                flat["white_rect"] = white_box(self.n.shot(f"apps-{tag}-2d-w"))
            self.toggle(gen, vendor)
            time.sleep(4.0)
            self.assertEqual(self.n.hc("eval", "assert(hl.plugin.hypr3d.active(), 'room closed')"), "ok")
            # a resized window redraws: wait until its markers are back at the flat spacing (new) or give up (old)
            ready = (lambda r: all(abs(a - b) <= 1.5 for a, b in zip(r["delta"], flat["delta"]))) if build == "new" \
                else (lambda r: True)
            room = self.look(f"apps-{tag}-3d", kind, ready=ready, timeout=12.0)
            room["status"] = self.n.status("spawn:")
            c3 = self.client(cls_name)
            room["client_size"] = c3["size"] if c3 else None
            room["title"] = c3["title"] if c3 else ""
            if kind == "term":
                room["white_rect"] = white_box(self.n.shot(f"apps-{tag}-3d-w"))
            self.toggle(gen, vendor)
            time.sleep(2.5)
            self.n.stop_all()
            res[build] = {"2d": flat, "3d": room}
            if "error" not in room and "error" not in flat:
                M = (room["delta"][0] / flat["delta"][0], room["delta"][1] / flat["delta"][1])
                rect = self.rect3d(kind, flat, room, M)
                res[build].update({
                    "M": M, "texel_per_px": (1 / M[0], 1 / M[1]),
                    "glyph_px": {k: (flat["glyph_px"][k], room["glyph_px"][k]) for k in flat["glyph_px"]},
                    "mid_shift_px": math.dist(room["mid"], flat["mid"]), "rect_3d": rect,
                    "inside_fit": rect is not None and self.inside(rect, self.fit),
                    "inside_output": rect is not None and self.inside(rect, (0, 0, *self.out_px)),
                    "edges_visible": self.on_screen(room.get("content_rect"))})
        self.results[f"{kind}-{size}"] = res
        return res

    def rect3d(self, kind, flat, room, M):
        """The window's rectangle on screen in the room: the edges of its content (corner markers) widened by what
        lies between content and window edge, magnified by M -- the terminal's grid remainder (right / bottom, from
        its size in the room and the cell size measured in 2D), the browser's chrome (measured in 2D)."""
        if kind == "term":
            if not room.get("content_rect") or not room.get("client_size"):
                return None
            cw, ch = flat["marker_px"][0] / 4.0, flat["marker_px"][1] / 2.0   # red = 4 cells x 2 rows, at M = 1
            w, h = room["client_size"][0] * self.s, room["client_size"][1] * self.s
            x0, y0, x1, y1 = room["content_rect"]
            return (x0, y0, x1 + (w - math.floor(w / cw + 1e-6) * cw) * M[0], y1 + (h - math.floor(h / ch + 1e-6) * ch) * M[1])
        # the chrome per edge, in 2D (output px at M = 1) where both the window edge and the page edge are on screen;
        # an edge that is off screen in 2D (a window wider / taller than the output) takes the rest of that axis'
        # chrome from the page's own viewport size (its title: "h3r-page WxH@dpr") against the client size
        ce, wr = flat.get("content_edges") or (None,) * 4, flat.get("window_rect")
        W, H = self.out_px
        vis = lambda v, lim: v is not None and 0 < v < lim
        pad = [None] * 4
        for i, lim in enumerate((W, H, W, H)):
            if vis(ce[i], lim) and 0 <= wr[i] <= lim:
                pad[i] = abs(ce[i] - wr[i])
        if None in pad:
            m = re.search(r"h3r-page (\d+)x(\d+)@", room.get("title") or "")
            cs = room.get("client_size")
            if not m or not cs:
                return None
            chrome = ((cs[0] - int(m.group(1))) * self.s, (cs[1] - int(m.group(2))) * self.s)
            for a, b, axis in ((0, 2, 0), (1, 3, 1)):
                if pad[a] is None and pad[b] is None:
                    pad[a] = pad[b] = chrome[axis] / 2
                elif pad[a] is None:
                    pad[a] = max(0.0, chrome[axis] - pad[b])
                elif pad[b] is None:
                    pad[b] = max(0.0, chrome[axis] - pad[a])
        self.results.setdefault("browser_chrome_px", {})[f"{size_tag(flat)}"] = pad
        if not room.get("content_rect"):
            return None
        x0, y0, x1, y1 = room["content_rect"]
        return (x0 - pad[0] * M[0], y0 - pad[1] * M[1], x1 + pad[2] * M[0], y1 + pad[3] * M[1])

    def on_screen(self, rect):
        """All four window edges are on screen, none at the output's border (cut off)."""
        return rect is not None and rect[0] > 0 and rect[1] > 0 and rect[2] < self.out_px[0] and rect[3] < self.out_px[1]

    @staticmethod
    def inside(rect, box, tol=2.0):
        return rect[0] >= box[0] - tol and rect[1] >= box[1] - tol and rect[2] <= box[2] + tol and rect[3] <= box[3] + tol

    # -- the checks --------------------------------------------------------------------------------------------------
    def check(self, res, kind, size):
        new, old = res["new"], res["old"]
        self.assertNotIn("error", new["2d"], new)
        self.assertNotIn("error", new["3d"], new)
        flat_glyphs = new["2d"]["glyph_px"]
        self.assertTrue(all(v > 0 for v in flat_glyphs.values()), flat_glyphs)
        cs = new["3d"]["client_size"]
        # (a) one texel per output pixel at the window centre -- or, for a client that will not shrink into the fit
        # box, the push-back M = fit / its box (fitted by distance, never cropped)
        want = min(1.0, self.o["fit"][0] / cs[0], self.o["fit"][1] / cs[1]) if cs else 1.0
        for v in new["M"]:
            self.assertAlmostEqual(v, want, delta=0.004, msg=f"{kind}-{size} M {new['M']} want {want}")
        # (b) every text line at the flat desktop's on-screen size (+-1 px) when M = 1
        if want == 1.0:
            for k, (a, b) in new["glyph_px"].items():
                self.assertLessEqual(abs(a - b), 1, f"{kind}-{size} {k}: 2D {a} px, room {b} px")
        # where 2D put it (a typical window); an oversized one is centred in the fit box
        if size == "typ":
            self.assertLessEqual(new["mid_shift_px"], 2.0, new)
        # (c) inside the work area minus the margin, all four corners on screen
        self.assertTrue(new["edges_visible"], new["3d"]["corners"])
        self.assertIsNotNone(new["rect_3d"])
        self.assertTrue(new["inside_fit"], f"{kind}-{size}: {new['rect_3d']} not inside {self.fit}")
        if kind == "browser" and want == 1.0:
            self.assertGreaterEqual(new["3d"]["grating"], 0.9 * new["2d"]["grating"], new)
        # negative control: the old build places at the fixed 0.5 / 5 m -- not 1:1, text not the 2D size; an
        # oversized window leaves the work area (cropped); on a rotated output its room is not upright (no markers)
        if int(self.n.mon["transform"]) != 0:
            # no upright room: the window is missing, mis-sized or off its place
            wrong = "M" not in old or abs(old["M"][0] - 1.0) > 0.05 or abs(old["M"][1] - 1.0) > 0.05 \
                or old["mid_shift_px"] > 20
            self.assertTrue(wrong, f"the old build showed the window upright at 1:1 on a rotated output: {old}")
            return
        self.assertIn("M", old, old)
        self.assertGreater(abs(old["M"][0] - 1.0), 0.05, old)
        self.assertAlmostEqual(old["M"][0], self.stock_m, delta=0.01, msg=old)
        a = sum(v[0] for v in old["glyph_px"].values())
        b = sum(v[1] for v in old["glyph_px"].values())
        self.assertGreater(abs(b / a - 1.0), 0.04, old["glyph_px"])
        if size == "big":
            self.assertFalse(old["inside_fit"] and old["edges_visible"], old)

    def test_1_terminal_typical(self):
        self.check(self.run_app("term", "typ"), "term", "typ")

    def test_2_terminal_oversized(self):
        self.check(self.run_app("term", "big"), "term", "big")

    def test_3_browser_typical(self):
        self.check(self.run_app("browser", "typ"), "browser", "typ")

    def test_4_browser_oversized(self):
        self.check(self.run_app("browser", "big"), "browser", "big")


if __name__ == "__main__":
    unittest.main(verbosity=2)
