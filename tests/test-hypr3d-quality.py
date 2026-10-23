#!/usr/bin/env python3
# AI-hint: Hypr3D render quality (operator 2026-10-22: "double the resolution scale in Hypr3D -- video shows jaggies on
# windows' edges ... also the 3D Wallpaper tiles vertically 3-4 times when looking around"). hypr3d-mios-ssaa.patch
# renders the scene at [desktop.hyprland.hypr3d.config.render].scale x the output (box-filtered down in the composite)
# and wraps the live wallpaper ONCE around the horizon (world.live_wrap = "mirror": centre ahead, mirrored back half,
# no seam; "repeat": one copy over 360 degrees) instead of blending three axis projections that each show the whole image.
#   1. static (always): the SSOT lists the patch after the render-quality patch, declares render.scale (1..4) and
#      world.live_wrap ("mirror" | "repeat"), and the patch carries the scale/box/viewport/wrap pieces. A Python model of
#      the shader mapping shows the new wrap once per turn, continuous at every step, with no 90-degree period; the model
#      of the OLD three-projection blend (negative control) repeats every ~90 degrees.
#   2. upstream (MIOS_H3D_UPSTREAM = a checkout of the CURRENT upstream HEAD): every SSOT patch applies in order.
#   3. nested (MIOS_H3D_NEST_SIG + MIOS_H3D_NEST_XDG + MIOS_H3D_NEST_WL + MIOS_H3D_NEW_SO + MIOS_H3D_OLD_SO: a NESTED
#      Hyprland with a private runtime dir and bus and a HEADLESS-DESK 1920x1080 output, never the live session):
#      (a) a white window with slanted edges at render.scale 1 vs 2: the edge's stair-step residual against a fitted
#          straight line and the error against an analytically supersampled coverage reference must drop clearly at 2;
#      (b) a ramp wallpaper (R = u, G = v) through 360 degrees of yaw in 15-degree steps: u(yaw) is the mirrored
#          triangle (once per turn), adjacent steps are continuous, the stitched horizon strip has no 90-degree
#          period; MIOS_H3D_OLD_SO (negative control: the same build with the pre-patch three-projection GLSL
#          restored -- the shipped pre-patch plugin has no player.look_yaw, so IPC cannot turn its camera) shows
#          the ~90-degree period;
#      (c) GPU cost at scale 1 vs 2 (iGPU busy %, frame rate, VRAM/GTT) -- reported, not gated.
#      Evidence (PNGs, JSON) goes to MIOS_H3D_EVIDENCE when set.
# AI-related: usr/share/mios/hyprland/patches/hypr3d-mios-ssaa.patch, usr/share/mios/hyprland/patches/
# hypr3d-mios-render-quality.patch, usr/share/mios/mios.toml [desktop.hyprland.hypr3d]
"""Hypr3D supersampling and live-wallpaper wrap tests."""

from __future__ import annotations

import json
import math
import os
import subprocess
import time
import tomllib
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SSOT = os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
PATCH = "usr/share/mios/hyprland/patches/hypr3d-mios-ssaa.patch"
UPSTREAM = os.environ.get("MIOS_H3D_UPSTREAM", "")
NEST_SIG = os.environ.get("MIOS_H3D_NEST_SIG", "")
NEST_XDG = os.environ.get("MIOS_H3D_NEST_XDG", "")
NEST_WL = os.environ.get("MIOS_H3D_NEST_WL", "")
NEW_SO = os.environ.get("MIOS_H3D_NEW_SO", "")
OLD_SO = os.environ.get("MIOS_H3D_OLD_SO", "")
EVIDENCE = os.environ.get("MIOS_H3D_EVIDENCE", "")
HERE = os.path.dirname(os.path.abspath(__file__))
GPU = os.environ.get("MIOS_H3D_GPU_SYSFS", "/sys/class/drm/card1/device")
HZ = int(os.environ.get("MIOS_H3D_HZ", "240"))  # nested output refresh for the cost test (240: GPU-bound fps)


def ssot() -> dict:
    with open(SSOT, "rb") as f:
        return tomllib.load(f)


# ---- shader models (mirror the GLSL in hypr3d-mios-ssaa.patch / the old blend) --------------------------------------
def tri(t: float) -> float:
    return 1.0 - abs(1.0 - (t % 2.0))


def new_u(yaw_deg: float, mirror: bool = True) -> float:
    az = math.radians(yaw_deg)
    az = math.atan2(math.sin(az), math.cos(az))
    return tri(0.5 + az / math.pi) if mirror else (0.5 + az / (2 * math.pi)) % 1.0


def old_u(yaw_deg: float) -> float:
    """The removed blend: x/z rectilinear projections weighted by |ray|^4 (y weight 0 at the horizon)."""
    a = math.radians(yaw_deg)
    x, z = math.sin(a), -math.cos(a)
    ax, az = abs(x), abs(z)
    wx, wz = ax ** 4, az ** 4
    ux = min(1.0, max(0.0, (z if x > 0 else -z) / max(ax, 1e-6) * 0.5 + 0.5))
    uz = min(1.0, max(0.0, (x if z < 0 else -x) / max(az, 1e-6) * 0.5 + 0.5))
    return (ux * wx + uz * wz) / (wx + wz)


def autocorr(seq: list[float], lag: int) -> float:
    n = len(seq)
    m = sum(seq) / n
    d = [s - m for s in seq]
    den = sum(v * v for v in d) or 1e-12
    return sum(d[i] * d[(i + lag) % n] for i in range(n)) / den


def extrema(seq: list[float], eps: float = 0.01) -> int:
    """Turning points of a cyclic sequence (sign changes of the slope, flat steps ignored)."""
    slopes = []
    n = len(seq)
    for i in range(n):
        d = seq[(i + 1) % n] - seq[i]
        if abs(d) > eps:
            slopes.append(1 if d > 0 else -1)
    return sum(1 for i in range(len(slopes)) if slopes[i] != slopes[i - 1])


# ---- image metrics ---------------------------------------------------------------------------------------------------
def load(path: str):
    from PIL import Image  # noqa: PLC0415 -- only the nested tier needs PIL

    return Image.open(path).convert("RGB")


def luma_rows(im, x0: int, x1: int, y0: int, y1: int) -> list[list[float]]:
    px = im.load()
    return [[(0.2126 * px[x, y][0] + 0.7152 * px[x, y][1] + 0.0722 * px[x, y][2]) / 255.0 for x in range(x0, x1)]
            for y in range(y0, y1)]


def edge_metrics(im, x0: int, x1: int, window: int = 8) -> dict:
    """Top edge of a bright quad over a dark background, columns [x0, x1).

    Per column: coarse edge = first row brighter than the midpoint; normalised coverage n = (I - bg) / (fg - bg) over
    +-window rows; sub-pixel edge e(x) = y_top + window*2 - sum(n). A straight antialiased edge gives a linear e(x); a
    stair-stepped one leaves a residual of up to +-0.5 px (RMS ~0.29). Also: error of n against the analytic box-filtered
    coverage of the fitted line (16x16 subsamples per pixel), and intermediate pixels per edge column.
    """
    w, h = im.size
    px = im.load()

    def L(x, y):
        r, g, b = px[x, y]
        return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0

    cols = []
    for x in range(x0, x1):
        ys = next((y for y in range(2, h - 2) if L(x, y) > 0.45), None)
        if ys is None or ys < window + 2 or ys > h - window - 12:
            continue
        cols.append((x, ys))
    # keep the longest run of columns on ONE straight-ish edge (drop corners / other edges), then its middle 70%
    runs, cur = [], []
    for c in cols:
        if cur and (c[0] != cur[-1][0] + 1 or abs(c[1] - cur[-1][1]) > 2):
            runs.append(cur)
            cur = []
        cur.append(c)
    if cur:
        runs.append(cur)
    cols = max(runs, key=len) if runs else []
    trim = len(cols) * 15 // 100
    cols = cols[trim:len(cols) - trim]
    if len(cols) < 50:
        raise AssertionError(f"edge not found in columns {x0}..{x1} ({len(cols)})")
    bg = sorted(L(x, y - window - 1) for x, y in cols)[len(cols) // 2]
    fg = sorted(L(x, y + window + 1) for x, y in cols)[len(cols) // 2]
    span = max(fg - bg, 1e-3)
    es, ns = [], []
    for x, ys in cols:
        top = ys - window
        n = [min(1.2, max(-0.2, (L(x, y) - bg) / span)) for y in range(top, ys + window)]
        ns.append((x, top, n))
        es.append((x, top + 2 * window - sum(n)))
    k = len(es)
    sx = sum(x for x, _ in es)
    sy = sum(e for _, e in es)
    sxx = sum(x * x for x, _ in es)
    sxy = sum(x * e for x, e in es)
    b = (k * sxy - sx * sy) / (k * sxx - sx * sx)
    a = (sy - b * sx) / k
    resid = [e - (a + b * x) for x, e in es]
    rms = math.sqrt(sum(r * r for r in resid) / k)
    # analytic reference: coverage of each pixel by the region below y = a + b*x (pixel [x,x+1) x [y,y+1), edge
    # positions measured from pixel tops; e(x) sums coverage, so the line passes through pixel centres in x)
    err2 = cnt = 0
    inter = 0
    S = 16
    for x, top, n in ns:
        for j, v in enumerate(n):
            y = top + j
            cov = 0
            for sxi in range(S):
                line = a + b * (x + (sxi + 0.5) / S - 0.5)
                cov += min(1.0, max(0.0, y + 1 - line))
            cov /= S
            err2 += (min(1.0, max(0.0, v)) - cov) ** 2
            cnt += 1
            if 0.1 < v < 0.9:
                inter += 1
    # stair-step energy: a stair-stepped edge (and its thin lit rim) moves whole pixels in and out of each column, so
    # the per-column integral of the band (sum n) jumps along x; an antialiased edge keeps it on a straight line.
    # RMS of the detrended column integral (= residual of e(x)) and its 1-px differences (sawtooth energy).
    saw = [abs((es[i + 1][1] - es[i][1]) - b) for i in range(k - 1)]
    return {"columns": k, "slope": b, "bg": bg, "fg": fg, "residual_rms_px": rms,
            "ref_rmse": math.sqrt(err2 / max(cnt, 1)), "intermediate_px_per_col": inter / k,
            "stair_energy": sum(saw) / len(saw)}


def rmse_vs_reference(im, ref, box: tuple[int, int, int, int], grad: float = 0.04) -> dict:
    """RMSE of luma against a supersampled reference capture, over the reference's edge pixels (|gradient| > grad)."""
    x0, y0, x1, y1 = box
    a = luma_rows(im, x0, x1, y0, y1)
    r = luma_rows(ref, x0, x1, y0, y1)
    err2 = n = jag = 0
    jag_energy = 0.0
    for y in range(1, len(r) - 1):
        ry, ay = r[y], a[y]
        for x in range(1, len(ry) - 1):
            g = max(abs(ry[x + 1] - ry[x - 1]), abs(r[y + 1][x] - r[y - 1][x])) * 0.5
            if g > grad:
                e = ay[x] - ry[x]
                err2 += e * e
                n += 1
                if abs(e) >= 20 / 255:  # a visibly wrong edge pixel: the stair steps
                    jag += 1
                    jag_energy += abs(e)
    return {"edge_pixels": n, "rmse": math.sqrt(err2 / max(n, 1)), "jaggy_pixels": jag,
            "jaggy_energy": round(jag_energy, 3)}


# ---- nested session driver -------------------------------------------------------------------------------------------
class Nest:
    def __init__(self):
        self.env = dict(os.environ, XDG_RUNTIME_DIR=NEST_XDG, WAYLAND_DISPLAY=NEST_WL)
        self.env.pop("HYPRLAND_INSTANCE_SIGNATURE", None)

    def hc(self, *args: str, check: bool = True) -> str:
        p = subprocess.run(["hyprctl", "-i", NEST_SIG, *args], env=self.env, capture_output=True, text=True,
                           timeout=20)
        if check and p.returncode != 0:
            raise AssertionError(f"nested hyprctl {args}: {p.stdout}{p.stderr}")
        return p.stdout.strip()

    def ev(self, lua: str) -> str:
        out = self.hc("eval", lua)
        if "error" in out.lower():
            raise AssertionError(f"nested eval failed: {lua}: {out}")
        return out

    def shot(self, path: str) -> str:
        subprocess.run(["grim", "-o", "HEADLESS-DESK", path], env=self.env, check=True, timeout=20)
        return path

    def plugin(self, so: str) -> None:
        listing = self.hc("plugin", "list", check=False)
        if "Plugin hypr3d" in listing:
            try:
                self.ev("if hl.plugin.hypr3d and hl.plugin.hypr3d.active() then hl.plugin.hypr3d.close() end")
            except AssertionError:
                pass
            time.sleep(1.5)
            for path in (NEW_SO, OLD_SO):  # unload is by path; the list does not name it
                self.hc("plugin", "unload", path, check=False)
            time.sleep(1.0)
        out = self.hc("plugin", "load", so)
        if "ok" not in out.lower() and "loaded" not in out.lower():
            raise AssertionError(f"plugin load {so}: {out}")
        time.sleep(0.5)

    def client(self, name: str, *cmd: str) -> subprocess.Popen:
        """A client in the NESTED session: systemd-run talks to the real user manager; the client gets the private
        runtime dir, Wayland display and bus."""
        inner = ["env", "-u", "QT_QPA_PLATFORMTHEME", "-u", "HYPRLAND_INSTANCE_SIGNATURE", f"XDG_RUNTIME_DIR={NEST_XDG}",
                 f"WAYLAND_DISPLAY={NEST_WL}", f"DBUS_SESSION_BUS_ADDRESS=unix:path={NEST_XDG}/bus",
                 "QT_QPA_PLATFORM=wayland", "QT_NO_XDG_DESKTOP_PORTAL=1", "GSETTINGS_BACKEND=memory", "GIO_USE_VFS=local",
                 "QT_WAYLAND_DISABLE_WINDOWDECORATION=1", *cmd]
        with open(evidence(f"h3d-{name}.log"), "w") as log:
            return subprocess.Popen(["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=1G", "-p",
                                     "MemorySwapMax=0", f"--unit=mios-h3d-test-{name}-{os.getpid()}", *inner],
                                    stdout=log, stderr=subprocess.STDOUT)

    def open3d(self, cfg: str) -> None:
        self.ev("local p=assert(hl.plugin.hypr3d); p.config(%s); if not p.active() then p.open() end" % cfg)
        time.sleep(3.0)

    def look(self, yaw: float, pitch: float = 0.0, settle: float = 0.35) -> None:
        self.ev("hl.plugin.hypr3d.config({player={look_yaw=%g, look_pitch=%g}})" % (yaw, pitch))
        time.sleep(settle)

    def status(self) -> str:
        try:
            with open("/tmp/hypr3d-status.txt", encoding="utf-8", errors="replace") as f:
                lines = [ln for ln in f.read().splitlines() if ln.startswith("render:")]
            return lines[-1] if lines else ""
        except OSError:
            return ""


def evidence(name: str) -> str:
    d = EVIDENCE or "/tmp"
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def save_json(name: str, data) -> None:
    with open(evidence(name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.chmod(evidence(name), 0o644)


def horizon_strip(nest: Nest, prefix: str, step: int = 15) -> tuple[list[float], list[float], list[str]]:
    """u at the view centre per yaw step, and a stitched 1-degree horizon strip of R (= u for the ramp wallpaper)."""
    shots, centre, strip = [], [], [0.0] * 360
    half_tan_x = math.tan(math.radians(65.0 / 2)) * 16 / 9
    px_per_tan = 960 / half_tan_x
    for yaw in range(0, 360, step):
        nest.look(yaw)
        path = nest.shot(evidence(f"{prefix}-yaw{yaw:03d}.png"))
        shots.append(path)
        im = load(path)
        p = im.load()

        def r_at(x: int) -> float:  # rows above the crosshair; R depends on azimuth only near the horizon
            return sum(p[x, y][0] for y in range(500, 521)) / 21 / 255.0

        centre.append(r_at(960))
        for d in range(-step // 2, step - step // 2):
            x = int(round(960 + px_per_tan * math.tan(math.radians(d))))
            strip[(yaw + d) % 360] = r_at(x)
    return centre, strip, shots


# ---- tests -----------------------------------------------------------------------------------------------------------
class Static(unittest.TestCase):
    def test_ssot_declares_patch_and_keys(self):
        h = ssot()["desktop"]["hyprland"]["hypr3d"]
        patches = h["patches"]
        self.assertIn(PATCH, patches)
        self.assertLess(patches.index("usr/share/mios/hyprland/patches/hypr3d-mios-render-quality.patch"),
                        patches.index(PATCH), "the SSAA patch applies after the render-quality patch")
        for p in patches:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, p)), p)
        scale = h["config"]["render"]["scale"]
        self.assertIsInstance(scale, (int, float))
        self.assertTrue(1.0 <= scale <= 4.0, scale)
        self.assertIn(h["config"]["world"].get("live_wrap"), ("mirror", "repeat"))
        self.assertNotIn("commit", h, "SSOT pulls latest upstream: no pinned commit")

    def test_patch_carries_ssaa_and_wrap(self):
        with open(os.path.join(ROOT, PATCH), encoding="utf-8") as f:
            text = f.read()
        added = "\n".join(ln[1:] for ln in text.splitlines() if ln.startswith("+") and not ln.startswith("+++"))
        for needle in ("float scale              = 2.0f", "kSceneBudgetBytes", "GL_MAX_RENDERBUFFER_SIZE",
                       "glViewport(0, 0, m_sceneWidth, m_sceneHeight)", "vec4 box(vec2 uv)", "uBoxTaps",
                       "uSceneScale", "miosLiveWallpaperUV", "\"live_wrap\"", "\"scale\"", "m_outputWidth",
                       "m_sceneWidth / 2"):
            self.assertIn(needle, added, needle)
        removed = "\n".join(ln[1:] for ln in text.splitlines() if ln.startswith("-") and not ln.startswith("---"))
        self.assertIn("weights = a * a * a * a", removed, "the three-projection live blend is removed")

    def test_model_new_wrap_once_seamless(self):
        us = [new_u(y) for y in range(0, 360, 15)]
        self.assertEqual(extrema(us), 2, us)  # one rise (front half) and one mirrored fall: the image appears once
        jumps = [abs(us[(i + 1) % len(us)] - us[i]) for i in range(len(us))]
        self.assertLessEqual(max(jumps), 15 / 180 + 1e-9, "no seam between 15-degree steps")
        strip = [new_u(d) for d in range(360)]
        self.assertLess(autocorr(strip, 90), 0.3)
        self.assertAlmostEqual(new_u(0), 0.5)
        self.assertAlmostEqual(new_u(90), 1.0)
        self.assertAlmostEqual(new_u(-90), 0.0)
        self.assertAlmostEqual(new_u(180), 0.5)
        rep = [new_u(d, mirror=False) for d in range(360)]
        self.assertGreater(max(abs(rep[(i + 1) % 360] - rep[i]) for i in range(360)), 0.9, "repeat joins behind")

    def test_model_old_blend_repeats_every_90_degrees(self):
        strip = [old_u(d) for d in range(360)]
        self.assertGreater(autocorr(strip, 90), 0.6, "negative control: the old blend has a ~90-degree period")
        self.assertGreaterEqual(extrema([old_u(y) for y in range(0, 360, 15)]), 6)


@unittest.skipUnless(UPSTREAM, "MIOS_H3D_UPSTREAM (checkout of the current upstream HEAD) not set")
class Upstream(unittest.TestCase):
    def test_patches_apply_in_order(self):
        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            work = os.path.join(tmp, "src")
            subprocess.run(["git", "-c", "safe.directory=*", "clone", "-q", UPSTREAM, work], check=True)
            for p in ssot()["desktop"]["hyprland"]["hypr3d"]["patches"]:
                r = subprocess.run(["git", "apply", "--check", os.path.join(ROOT, p)], cwd=work, capture_output=True,
                                   text=True)
                self.assertEqual(r.returncode, 0, f"{p}: {r.stderr}")
                subprocess.run(["git", "apply", os.path.join(ROOT, p)], cwd=work, check=True)
            shutil.rmtree(work, ignore_errors=True)


@unittest.skipUnless(NEST_SIG and NEST_XDG and NEST_WL and NEW_SO and OLD_SO, "nested Hypr3D session not provided")
class Nested(unittest.TestCase):
    RESULTS: dict = {}

    @classmethod
    def setUpClass(cls):
        cls.nest = Nest()
        cls.ramp = evidence("ramp.png")
        from PIL import Image  # noqa: PLC0415

        w, h = 1920, 1080
        im = Image.new("RGB", (w, h))
        px = im.load()
        for y in range(h):
            g = round(y / (h - 1) * 255)
            for x in range(w):
                px[x, y] = (round(x / (w - 1) * 255), g, 128)
        im.save(cls.ramp)
        cls.qml = os.environ.get("MIOS_H3D_QML_DIR", "")

    @classmethod
    def tearDownClass(cls):
        save_json("hypr3d-quality-results.json", cls.RESULTS)

    def _wallpaper(self) -> subprocess.Popen:
        p = self.nest.client("wall", "env", "QT_WAYLAND_SHELL_INTEGRATION=layer-shell", "/usr/lib64/qt6/bin/qml",
                             os.path.join(self.qml, "wall.qml"), "--", self.ramp)
        time.sleep(2.5)
        return p

    def _stop(self, name: str) -> None:
        subprocess.run(["systemctl", "--user", "stop", f"mios-h3d-test-{name}-{os.getpid()}.scope"],
                       capture_output=True)
        time.sleep(1.0)

    def test_b_wallpaper_wraps_once(self):
        cfg = '{world={monitor="HEADLESS-DESK", grid=false, live_wrap="mirror"}, render={scale=2.0}}'
        out = {}
        self._wallpaper()
        try:
            for label, so in (("old", OLD_SO), ("new", NEW_SO)):
                self.nest.plugin(so)
                self.nest.open3d(cfg if label == "new" else '{world={monitor="HEADLESS-DESK", grid=false}}')
                centre, strip, _ = horizon_strip(self.nest, f"wall-{label}")
                self.nest.ev("hl.plugin.hypr3d.close()")
                time.sleep(1.5)
                jumps = [abs(centre[(i + 1) % len(centre)] - centre[i]) for i in range(len(centre))]
                out[label] = {"u_centre": [round(u, 4) for u in centre], "extrema": extrema(centre, 0.02),
                              "max_step_jump": max(jumps), "autocorr_90": autocorr(strip, 90),
                              "autocorr_180": autocorr(strip, 180), "strip": [round(s, 4) for s in strip]}
        finally:
            self._stop("wall")
        self.RESULTS["wallpaper"] = out
        new, old = out["new"], out["old"]
        expect = [new_u(y) for y in range(0, 360, 15)]
        self.assertLess(max(abs(a - b) for a, b in zip(new["u_centre"], expect)), 0.03, new["u_centre"])
        self.assertEqual(new["extrema"], 2, new["u_centre"])
        self.assertLessEqual(new["max_step_jump"], 15 / 180 + 0.03)
        self.assertLess(new["autocorr_90"], 0.3)
        self.assertGreater(old["autocorr_90"], 0.6, "negative control: the old plugin repeats every ~90 degrees")
        self.assertGreaterEqual(old["extrema"], 6)

    def test_a_edge_aliasing(self):
        """Window edges (geometry edge + thin lit rim + rounded corner) at scale 1 vs 2, MSAA 4 (shipped) and 0.

        Reference: the same frame at scale 4 without MSAA (16 box samples per pixel, inside the scene budget)."""
        self.nest.client("win", "/usr/lib64/qt6/bin/qml", os.path.join(self.qml, "win.qml"))
        time.sleep(2.5)
        out = {}
        shots = {}
        try:
            self.nest.plugin(NEW_SO)
            self.nest.open3d('{world={monitor="HEADLESS-DESK", grid=false}, render={scale=1.0, msaa=4}}')
            self.nest.look(25, 10, settle=1.0)
            for label, render in (("ref_scale4_msaa0", "{scale=4.0, msaa=0}"), ("scale1_msaa4", "{scale=1.0, msaa=4}"),
                                  ("scale2_msaa4", "{scale=2.0, msaa=4}"), ("scale1_msaa0", "{scale=1.0, msaa=0}"),
                                  ("scale2_msaa0", "{scale=2.0, msaa=0}")):
                self.nest.ev("hl.plugin.hypr3d.config({render=%s})" % render)
                time.sleep(1.0)
                shots[label] = self.nest.shot(evidence(f"edge-{label}.png"))
                out[label] = {"status": self.nest.status()}
            self.nest.ev("hl.plugin.hypr3d.close()")
            time.sleep(1.5)
        finally:
            self._stop("win")
        ref = load(shots["ref_scale4_msaa0"])
        box = (0, 300, 1400, 1080)
        for label, path in shots.items():
            im = load(path)
            out[label].update(edge_metrics(im, 0, 1919))
            if label != "ref_scale4_msaa0":
                out[label]["vs_ref"] = rmse_vs_reference(im, ref, box)
        self.RESULTS["edges"] = out
        self.assertIn("scale=4", out["ref_scale4_msaa0"]["status"])
        self.assertIn("scale=2", out["scale2_msaa4"]["status"])
        self.assertIn("scale=1 ", out["scale1_msaa4"]["status"])
        for msaa in ("msaa4", "msaa0"):
            s1, s2 = out[f"scale1_{msaa}"], out[f"scale2_{msaa}"]
            # scale 1 is the negative control: its stair steps are the visibly wrong edge pixels
            self.assertGreater(s1["vs_ref"]["jaggy_pixels"], 50, (msaa, s1))
            self.assertLess(s2["vs_ref"]["jaggy_pixels"], s1["vs_ref"]["jaggy_pixels"] * 0.4, (msaa, s1, s2))
            self.assertLess(s2["vs_ref"]["rmse"], s1["vs_ref"]["rmse"] * 0.9, (msaa, s1, s2))
            self.assertLess(s2["residual_rms_px"], s1["residual_rms_px"], (msaa, s1, s2))

    def test_c_gpu_cost(self):
        """GPU cost (reported, not gated). The nested HEADLESS-DESK runs at MIOS_H3D_HZ (240 by default) so the frame
        rate is bounded by the GPU, not the 60 Hz pacing: ms/frame = 1000 / fps; MIOS_H3D_HZ=60 shows the delivered rate (the whole nested frame: room, windows, glass, composite).
        VRAM/GTT from the iGPU's amdgpu counters (shared with anything else on that GPU)."""
        def mem() -> dict:
            out = {}
            for k in ("mem_info_vram_used", "mem_info_gtt_used"):
                try:
                    with open(os.path.join(GPU, k)) as f:
                        out[k + "_mib"] = round(int(f.read()) / 2 ** 20, 1)
                except OSError:
                    pass
            return out

        def busy() -> float:
            try:
                vals = []
                for _ in range(20):
                    with open(os.path.join(GPU, "gpu_busy_percent")) as f:
                        vals.append(int(f.read()))
                    time.sleep(0.1)
                return sum(vals) / len(vals)
            except OSError:
                return -1.0

        self._wallpaper()
        self.nest.client("win", "/usr/lib64/qt6/bin/qml", os.path.join(self.qml, "win.qml"))
        time.sleep(2.5)
        out = {}
        mode = 'hl.monitor({output = "HEADLESS-DESK", mode = "1920x1080@%d", position = "0x2000", scale = 1})'
        try:
            self.nest.plugin(NEW_SO)
            self.nest.ev(mode % HZ)
            time.sleep(1.0)
            out["idle_2d"] = {"busy_pct": busy(), **mem()}
            for label, render in (("scale1_msaa4", "{scale=1.0, msaa=4}"), ("scale1_msaa0", "{scale=1.0, msaa=0}"),
                                  ("scale1.5_msaa4", "{scale=1.5, msaa=4}"), ("scale2_msaa4", "{scale=2.0, msaa=4}"),
                                  ("scale2_msaa2", "{scale=2.0, msaa=2}"), ("scale2_msaa0", "{scale=2.0, msaa=0}"),
                                  ("scale1_msaa4_again", "{scale=1.0, msaa=4}"),
                                  ("scale2_msaa4_again", "{scale=2.0, msaa=4}")):
                self.nest.open3d('{world={monitor="HEADLESS-DESK", grid=true, live_wrap="mirror"}, render=%s}' % render)
                self.nest.look(10, 4, settle=2.0)  # the plugin's fps is an EMA over frames; let it settle
                samples = []
                for _ in range(3):  # three 2 s windows; the median of ~24 status samples
                    end = time.time() + 2.0
                    while time.time() < end:
                        st = self.nest.status()
                        if "fps=" in st:
                            samples.append(float(st.split("fps=")[1]))
                        time.sleep(0.25)
                samples.sort()
                fps = samples[len(samples) // 2] if samples else 0.0
                out[label] = {"status": st, "fps": round(fps, 1), "fps_min": round(samples[0], 1) if samples else 0,
                              "fps_max": round(samples[-1], 1) if samples else 0,
                              "ms_per_frame": round(1000 / fps, 2) if fps else None, "busy_pct": busy(), **mem()}
            self.nest.ev("hl.plugin.hypr3d.close()")
            time.sleep(1.5)
        finally:
            try:
                self.nest.ev(mode % 60)
            except AssertionError:
                pass
            self._stop("win")
            self._stop("wall")
        self.RESULTS[f"gpu_{HZ}hz"] = out
        self.assertIn("scale=2", out["scale2_msaa4"]["status"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
