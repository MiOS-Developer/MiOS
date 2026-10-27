#!/usr/bin/env python3
# AI-hint: Hypr3D edge quality (photo-shoot critic 2026-10-23: "square backdrop wedge outside the radius", "dashed dark
# rulers / doubled underlines", "dark outlines round light text", "hero slab dim mauve, glass only dims, does not
# frost", "a window seen through another window's glass stays sharp"). hypr3d-mios-edge-quality.patch:
#   - the window snapshot is taken as a FOREIGN render (identity render modifier), so hyprglass' pane and edge pass are
#     not baked into it over black: the client keeps its own opacity and the 3D face composites the desktop's stack
#     itself (pane over the 3D backdrop with the preset's frost, client, edge pass) -- no luma keying;
#   - outside the rounded outline only solid decorations (Hyprland's reserved extents) are drawn, never the 2D drop
#     shadow (its inner edge followed the square client box), and the outline is antialiased over one screen pixel;
#   - snapshot textures carry a mip chain (once per capture) sampled trilinear + anisotropic (render.anisotropy);
#   - CAS uses the single-scale limiter (1 - mx), the frost honours blur_sigma, and mios-gen projects
#     [desktop.hyprland.hypr3d.config].glass_preset (the hyprglass preset) into the plugin's glass table.
# Tiers:
#   1. static + model (always): SSOT + patch content; Python models of the old/new shader terms with negative controls
#      (CAS undershoot at white glyphs, keyed vs desktop composite, corner shadow clip); the preset resolver mirror.
#   2. upstream (MIOS_H3D_UPSTREAM = a checkout of the CURRENT upstream HEAD): every SSOT patch applies in order.
#   3. nested (MIOS_H3D_NEST_SIG + MIOS_H3D_NEST_XDG + MIOS_H3D_NEST_WL + MIOS_H3D_NEST_STATUS + MIOS_H3D_NEW_SO +
#      MIOS_H3D_OLD_SO + MIOS_H3D_QML_DIR: a NESTED Hyprland with the MiOS decoration and the hyprglass "mios" preset,
#      never the live session); MIOS_H3D_OLD_SO is the negative control (the shipped plugin), MIOS_H3D_NEW_SO the fix:
#      (1) corner: luma step between the wedge inside the client box past the arc and the band just outside the box;
#      (2) 1-px rules at steep angles: coefficient of variation of the rule's per-column contrast along the line;
#      (3) halos: undershoot below the local background in the ring round 3x3 white dots and round white text;
#      (4) slab vs the flat desktop pane (same window, same backdrop, 1:1 head-on): luma and saturation;
#      (5) window behind window: high-frequency energy of the rear window's text seen through the front pane vs
#          seen directly, against the same ratio on the flat desktop;
#      (6) cost (reported): nested fps at 240 Hz, static and with a client committing every frame.
#      Evidence (PNGs, JSON) goes to MIOS_H3D_EVIDENCE when set.
# AI-related: usr/share/mios/hyprland/patches/hypr3d-mios-edge-quality.patch, usr/share/mios/mios.toml
# [desktop.hyprland.hypr3d], tools/native/mios-gen/src/render.rs (hypr3d_glass), tests/test-hypr3d-quality.py
"""Hypr3D edge-quality tests: corner wedge, thin lines, text halos, one glass look, frost behind glass."""

from __future__ import annotations

import colorsys
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
import tomllib
import unittest

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
SSOT = os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
PATCH = "usr/share/mios/hyprland/patches/hypr3d-mios-edge-quality.patch"
STILLS = "usr/share/mios/hyprland/patches/hypr3d-mios-stills.patch"
UPSTREAM = os.environ.get("MIOS_H3D_UPSTREAM", "")
NEST_SIG = os.environ.get("MIOS_H3D_NEST_SIG", "")
NEST_XDG = os.environ.get("MIOS_H3D_NEST_XDG", "")
NEST_WL = os.environ.get("MIOS_H3D_NEST_WL", "")
NEST_STATUS = os.environ.get("MIOS_H3D_NEST_STATUS", "")  # the nested plugin's status file (its private /tmp)
NEW_SO = os.environ.get("MIOS_H3D_NEW_SO", "")
OLD_SO = os.environ.get("MIOS_H3D_OLD_SO", "")
QML = os.environ.get("MIOS_H3D_QML_DIR", "")
EVIDENCE = os.environ.get("MIOS_H3D_EVIDENCE", "")
HZ = int(os.environ.get("MIOS_H3D_HZ", "240"))


def ssot() -> dict:
    with open(SSOT, "rb") as f:
        return tomllib.load(f)


def patch_lines(sign: str) -> str:
    with open(os.path.join(ROOT, PATCH), encoding="utf-8") as f:
        text = f.read()
    return "\n".join(ln[1:] for ln in text.splitlines() if ln.startswith(sign) and not ln.startswith(sign * 3))


# ---- the hyprglass preset as Hypr3D's glass table (mirror of mios-gen render.rs hypr3d_glass) ----------------------
DARK = {"brightness": 0.82, "contrast": 0.90, "saturation": 0.80, "vibrancy": 0.15, "vibrancy_darkness": 0.0,
        "adaptive_dim": 0.4, "adaptive_boost": 0.0}
LIGHT = {"brightness": 1.12, "contrast": 0.92, "saturation": 0.85, "vibrancy": 0.12, "vibrancy_darkness": 0.0,
         "adaptive_dim": 0.0, "adaptive_boost": 0.4}
GLOBAL = {"blur_strength": 2.0, "blur_iterations": 3.0, "refraction_strength": 0.6, "chromatic_aberration": 0.5,
          "fresnel_strength": 0.6, "specular_strength": 0.8, "glass_opacity": 1.0, "edge_thickness": 0.06,
          "tint_color": 0x8899AA22, "lens_distortion": 0.5, "refraction_flow": 0.0, "refraction_spread": 1.0,
          "fresnel_tint": 0.0, "bevel_strength": 0.0, "bevel_size": 6.0, "fresnel_color": 0xFFFFFF00,
          "bevel_color": 0xFFFFFF00, "bevel_tint": 0.0, "bevel_angle": 315.0, "bevel_shadow": 0.0, "specular_angle": 0.0}
BUILTIN_GLASS = {"blur_strength": 1.0, "blur_iterations": 2.0, "lens_distortion": 0.3, "refraction_strength": 8.0,
                 "chromatic_aberration": 0.5, "fresnel_strength": 0.4, "specular_strength": 0.8, "glass_opacity": 1.0,
                 "edge_thickness": 0.06, "tint_color": 0xFFFFFF00}
GLASS_KEYS = ["refraction_strength", "chromatic_aberration", "fresnel_strength", "specular_strength", "glass_opacity",
              "edge_thickness", "lens_distortion", "brightness", "contrast", "saturation", "vibrancy",
              "vibrancy_darkness", "adaptive_dim", "adaptive_boost", "refraction_flow", "refraction_spread",
              "fresnel_tint", "bevel_strength", "bevel_size", "bevel_tint", "bevel_angle", "bevel_shadow",
              "specular_angle"]


def palette(table: dict, data: dict) -> dict:
    out = {}
    for k, v in table.items():
        out[k] = palette(v, data) if isinstance(v, dict) else v
    for role, target in (("tint", "tint_color"), ("fresnel", "fresnel_color"), ("bevel", "bevel_color")):
        if f"palette_{role}" in out:
            hexv = data["colors"][out.pop(f"palette_{role}")].lstrip("#")
            alpha = out.pop(f"{role}_alpha")
            out[target] = (int(hexv, 16) << 8) | round(alpha * 255)
    return out


def preset_glass(data: dict, name: str) -> dict:
    hg = data["desktop"]["hyprland"]["hyprglass"]
    presets = hg["presets"]
    config = palette(hg.get("config", {}), data)
    theme = config.get("default_theme", "dark")
    chain, builtin, nxt = [], False, name
    while nxt:
        if nxt in presets:
            p = palette(presets[nxt], data)
            chain.append(p)
            nxt = p.get("inherits")
        else:
            builtin, nxt = nxt == "glass", None

    def resolve(k):
        for p in chain:
            if isinstance(p.get(theme), dict) and k in p[theme]:
                return float(p[theme][k])
            if k in p and not isinstance(p[k], (dict, str)):
                return float(p[k])
        if builtin:
            if theme != "light" and k == "adaptive_dim":
                return 0.3
            if theme == "light" and k == "adaptive_boost":
                return 0.3
            if k in BUILTIN_GLASS:
                return float(BUILTIN_GLASS[k])
        if isinstance(config.get(theme), dict) and k in config[theme]:
            return float(config[theme][k])
        if k in config and not isinstance(config[k], (dict, str)):
            return float(config[k])
        return float({**(DARK if theme != "light" else LIGHT), **GLOBAL}[k])

    out = {k: round(resolve(k), 4) for k in GLASS_KEYS}
    for k in ("tint_color", "fresnel_color", "bevel_color"):
        out[k] = int(resolve(k)) & 0xFFFFFFFF
    it = min(5.0, max(1.0, resolve("blur_iterations")))
    out["blur_sigma"] = round(4.0 * max(0.0, resolve("blur_strength")) * math.sqrt(it), 4)
    out["edge_pass"] = round(min(4.0, max(0.0, float(config.get("edge_pass", 0.0)))), 4)
    out["edge_pass_width"] = round(min(256.0, max(0.0, float(config.get("edge_pass_width", 0.0)))), 4)
    return out


def lua_table(d: dict) -> str:
    return "{" + ", ".join(f"{k}={v!r}" if not isinstance(v, float) else f"{k}={v:.6g}" for k, v in d.items()) + "}"


# ---- shader models (mirror the GLSL: the old terms are the negative controls) -----------------------------------------
def cas(c: float, n: list[float], strength: float, limit: float, clamp: bool = True) -> float:
    """CAS 5-tap on one channel; limit = 2.0 (the removed doubled-scale headroom) or 1.0 (the fix); clamp = the new
    no-ringing clamp to the neighbourhood's min/max (the old shader clamped to 0..1 only)."""
    mn, mx = min([c, *n]), max([c, *n])
    amp = math.sqrt(min(1.0, max(0.0, min(mn, limit - mx) / max(mx, 1e-4))))
    k = amp * (-1.0 / (8.0 + (5.0 - 8.0) * strength))
    v = (c + sum(n) * k) / (1.0 + 4.0 * k)
    return min(mx, max(mn, v)) if clamp else min(1.0, max(0.0, v))


def smoothstep(a: float, b: float, x: float) -> float:
    t = min(1.0, max(0.0, (x - a) / (b - a)))
    return t * t * (3 - 2 * t)


def keyed(straight: float, a: float, glass: float, detail: float) -> float:
    """The removed 3D transmission key (luma only, grey): opaque snapshot, flat dark areas see-through."""
    ink = min(1.0, smoothstep(0.14, 0.52, straight))
    w = (0.16 + (0.82 - 0.16) * ink) * (1 - smoothstep(0.025, 0.16, detail)) + 0.98 * smoothstep(0.025, 0.16, detail)
    sa = a * w
    return straight * sa + glass * (1 - sa)


# ---- image metrics --------------------------------------------------------------------------------------------------
def load(path: str):
    from PIL import Image  # noqa: PLC0415 -- only the nested tier needs PIL

    return Image.open(path).convert("RGB")


def L(px, x: int, y: int) -> float:
    r, g, b = px[x, y]
    return (0.2126 * r + 0.7152 * g + 0.0722 * b) / 255.0


def mean_luma(im, pts) -> float:
    px = im.load()
    v = [L(px, x, y) for x, y in pts]
    return sum(v) / max(len(v), 1)


def region_stats(im, box) -> dict:
    x0, y0, x1, y1 = box
    px = im.load()
    n = (x1 - x0) * (y1 - y0)
    r = g = b = 0.0
    for y in range(y0, y1):
        for x in range(x0, x1):
            p = px[x, y]
            r += p[0]
            g += p[1]
            b += p[2]
    r, g, b = r / n / 255, g / n / 255, b / n / 255
    return {"rgb": [round(r * 255, 1), round(g * 255, 1), round(b * 255, 1)],
            "luma": round(0.2126 * r + 0.7152 * g + 0.0722 * b, 4), "sat": round(colorsys.rgb_to_hsv(r, g, b)[1], 4)}


def window_box(im) -> tuple[int, int, int, int]:
    """The slab's client box in a head-on frame: midpoint threshold between the far backdrop and the interior."""
    px = im.load()
    w, h = im.size
    bg = L(px, 8, 8)
    inner = mean_luma(im, [(x, y) for x in range(900, 1020, 4) for y in range(230, 300, 4)])
    mid = (bg + inner) / 2

    def hit(v):
        return (v - mid) * (inner - mid) > 0

    y = h // 2 + 40  # the rules band: plain background at the window edges
    x0 = next(x for x in range(w) if hit(L(px, x, y)) and hit(L(px, x + 3, y)))
    x1 = next(x for x in range(w - 1, 0, -1) if hit(L(px, x, y)) and hit(L(px, x - 3, y))) + 1
    x = (x0 + x1) // 2 + 300
    y0 = next(y for y in range(h) if hit(L(px, x, y)) and hit(L(px, x, y + 3)))
    y1 = next(y for y in range(h - 1, 0, -1) if hit(L(px, x, y)) and hit(L(px, x, y - 3))) + 1
    return x0, y0, x1, y1


def corner_metrics(im, box, corner: str, radius_px: float) -> dict:
    """The corner past the arc: wedge W (inside the client box, outside the arc by >= 2 px), band O (2..12 px outside
    the box along the corner), far backdrop B. A layer clipped by the box instead of the radius makes W differ from O."""
    x0, y0, x1, y1 = box
    r = radius_px
    sx, sy = (1, 1) if corner == "tl" else (1, -1)
    cx, cy = (x0, y0) if corner == "tl" else (x0, y1 - 1)
    wedge, band, far = [], [], []
    for dy in range(-14, int(r) + 1):
        for dx in range(-14, int(r) + 1):
            x, y = cx + sx * dx, cy + sy * dy
            if dx >= 1 and dy >= 1 and dx < r and dy < r:
                if math.hypot(r - dx - 0.5, r - dy - 0.5) > r + 2.0:
                    wedge.append((x, y))
            elif (-12 <= dx <= -2 and -12 <= dy < r) or (-12 <= dy <= -2 and -12 <= dx < r):
                band.append((x, y))
    for dy in range(60, 90):
        for dx in range(60, 90):
            far.append((cx - sx * dx, cy - sy * dy))
    lw, lo, lb = mean_luma(im, wedge), mean_luma(im, band), mean_luma(im, far)
    return {"wedge": round(lw, 4), "band": round(lo, 4), "far": round(lb, 4), "step": round(abs(lw - lo), 4),
            "halo": round(lb - lo, 4), "n_wedge": len(wedge)}


def rule_lines(im, xa: int, xb: int, ylo: int, yhi: int, count: int) -> list[tuple[float, float]]:
    """The `count` strongest local extrema of |L - local median| at columns xa and xb, paired top to bottom: lines
    (a perspective view keeps a straight rule straight)."""
    px = im.load()

    def peaks(x):
        col = [L(px, x, y) for y in range(ylo, yhi)]
        dev = []
        for i in range(8, len(col) - 8):
            ref = sorted(col[i - 8:i - 3] + col[i + 4:i + 9])[5]
            dev.append((abs(col[i] - ref), ylo + i))
        dev.sort(reverse=True)
        chosen = []
        for d, y in dev:
            if all(abs(y - c) > 6 for c in chosen):
                chosen.append(y)
            if len(chosen) == count:
                break
        return sorted(chosen)

    pa, pb = peaks(xa), peaks(xb)
    return [(ya, (yb - ya) / (xb - xa)) for ya, yb in zip(pa, pb)]


def line_continuity(im, xa: int, xb: int, line: tuple[float, float]) -> dict:
    """Per column along the rule: contrast = sum over +-2 rows of |L - background| (background: mean of rows 5..7
    away on both sides). Dashed or doubled lines vary along x: CoV = std/mean; gaps = columns below half the median."""
    px = im.load()
    y0, slope = line
    s = []
    for x in range(xa, xb):
        yc = y0 + slope * (x - xa)
        yi = int(round(yc))
        bg = sum(L(px, x, yi + d) for d in (-7, -6, -5, 5, 6, 7)) / 6
        s.append(sum(abs(L(px, x, yi + d) - bg) for d in range(-2, 3)))
    m = sum(s) / len(s)
    sd = math.sqrt(sum((v - m) ** 2 for v in s) / len(s))
    med = sorted(s)[len(s) // 2]
    return {"mean": round(m, 4), "cov": round(sd / max(m, 1e-6), 4),
            "gaps": round(sum(1 for v in s if v < 0.5 * med) / len(s), 4)}


def dot_halo(im, box, scale: float) -> dict:
    """Ring round the 3x3 white dots (page.qml grid at 760,620, pitch 16): mean undershoot below the local background
    (Chebyshev distance 6..7) of the ring pixels at distance 2..3 from the dot centre, relative to the background."""
    x0, y0 = box[0], box[1]
    px = im.load()
    under, mins = [], []
    for row in range(6):
        for col in range(16):
            cx = x0 + (760 + 1.5 + col * 16) * scale
            cy = y0 + (620 + 1.5 + row * 16) * scale
            ring, bgp = [], []
            for dy in range(-7, 8):
                for dx in range(-7, 8):
                    d = max(abs(dx), abs(dy))
                    p = L(px, int(cx + dx), int(cy + dy))
                    if 2 <= d <= 3:
                        ring.append(p)
                    elif 6 <= d <= 7:
                        bgp.append(p)
            bg = sum(bgp) / len(bgp)
            under.append(sum(max(0.0, bg - v) for v in ring) / len(ring) / max(bg, 1e-3))
            mins.append((bg - min(ring)) / max(bg, 1e-3))
    return {"undershoot": round(sum(under) / len(under), 4), "max_dip": round(sorted(mins)[len(mins) // 2], 4)}


def text_halo(im, box, scale: float) -> dict:
    """White text block (page.qml 80,620 .. 520,700): pixels within 2 px of a glyph pixel (L > bg + 0.3) that are not
    glyph pixels themselves; mean undershoot below the block's background (median of the region), relative."""
    x0, y0 = box[0], box[1]
    xa, xb = int(x0 + 80 * scale), int(x0 + 520 * scale)
    ya, yb = int(y0 + 618 * scale), int(y0 + 708 * scale)
    px = im.load()
    vals = {(x, y): L(px, x, y) for x in range(xa, xb) for y in range(ya, yb)}
    bg = sorted(vals.values())[len(vals) // 2]
    glyph = {p for p, v in vals.items() if v > bg + 0.3}
    ring = set()
    for (x, y) in glyph:
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                q = (x + dx, y + dy)
                if q in vals and q not in glyph:
                    ring.add(q)
    under = [max(0.0, bg - vals[q]) for q in ring]
    dark = sum(1 for q in ring if vals[q] < bg - 0.03)
    return {"bg": round(bg, 4), "undershoot": round(sum(under) / max(len(under), 1) / max(bg, 1e-3), 4),
            "dark_ring_frac": round(dark / max(len(ring), 1), 4), "ring": len(ring)}


def hf_energy(im, box) -> float:
    """Mean |4-neighbour Laplacian| of luma: the text's high-frequency energy (sharp text high, frosted low)."""
    x0, y0, x1, y1 = box
    px = im.load()
    acc = 0.0
    for y in range(y0 + 1, y1 - 1):
        for x in range(x0 + 1, x1 - 1):
            acc += abs(4 * L(px, x, y) - L(px, x - 1, y) - L(px, x + 1, y) - L(px, x, y - 1) - L(px, x, y + 1))
    return acc / ((x1 - x0 - 2) * (y1 - y0 - 2))


# ---- nested session driver ------------------------------------------------------------------------------------------
def evidence(name: str) -> str:
    d = EVIDENCE or tempfile.gettempdir()
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, name)


def save_json(name: str, data) -> None:
    with open(evidence(name), "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.chmod(evidence(name), 0o644)


class Nest:
    def __init__(self):
        self.env = dict(os.environ, XDG_RUNTIME_DIR=NEST_XDG, WAYLAND_DISPLAY=NEST_WL)
        self.env.pop("HYPRLAND_INSTANCE_SIGNATURE", None)
        self.units: list[str] = []

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

    def shot(self, name: str) -> str:
        path = evidence(name)
        subprocess.run(["grim", "-o", "HEADLESS-DESK", path], env=self.env, check=True, timeout=20)
        os.chmod(path, 0o644)
        return path

    def status(self, prefix: str) -> str:
        try:
            with open(NEST_STATUS, encoding="utf-8", errors="replace") as f:
                lines = [ln for ln in f.read().splitlines() if ln.startswith(prefix)]
            return lines[-1] if lines else ""
        except OSError:
            return ""

    def plugin(self, so: str) -> None:
        if "Plugin hypr3d" in self.hc("plugin", "list", check=False):
            try:
                self.ev("if hl.plugin.hypr3d and hl.plugin.hypr3d.active() then hl.plugin.hypr3d.close() end")
            except AssertionError:
                pass
            time.sleep(1.5)
            for path in (NEW_SO, OLD_SO):  # unload is by path
                self.hc("plugin", "unload", path, check=False)
            time.sleep(1.0)
        if NEST_STATUS:  # the nested plugin's own status file (its private /tmp): no stale lines from the last load
            open(NEST_STATUS, "w").close()
        out = self.hc("plugin", "load", so)
        if "ok" not in out.lower() and "loaded" not in out.lower():
            raise AssertionError(f"plugin load {so}: {out}")
        time.sleep(0.5)

    def client(self, name: str, *cmd: str) -> None:
        unit = f"mios-h3e-c-{name}-{os.getpid()}"
        inner = ["env", "-u", "QT_QPA_PLATFORMTHEME", "-u", "HYPRLAND_INSTANCE_SIGNATURE", f"XDG_RUNTIME_DIR={NEST_XDG}",
                 f"WAYLAND_DISPLAY={NEST_WL}", f"DBUS_SESSION_BUS_ADDRESS=unix:path={NEST_XDG}/bus",
                 "QT_QPA_PLATFORM=wayland", "QT_NO_XDG_DESKTOP_PORTAL=1", "GSETTINGS_BACKEND=memory", "GIO_USE_VFS=local",
                 "QT_WAYLAND_DISABLE_WINDOWDECORATION=1", "QT_SCALE_FACTOR=1", "QT_ENABLE_HIGHDPI_SCALING=0", *cmd]
        with open(evidence(f"client-{name}.log"), "w") as log:
            subprocess.Popen(["systemd-run", "--user", "--scope", "-q", "-p", "MemoryMax=1G", "-p", "MemorySwapMax=0",
                              f"--unit={unit}", *inner], stdout=log, stderr=subprocess.STDOUT)
        self.units.append(unit)
        time.sleep(3.0)

    def qml(self, name: str, *args: str) -> None:
        self.client(name, "/usr/lib64/qt6/bin/qml", os.path.join(QML, f"{name}.qml"), *(("--", *args) if args else ()))

    def stop(self, name: str) -> None:
        unit = f"mios-h3e-c-{name}-{os.getpid()}"
        subprocess.run(["systemctl", "--user", "stop", f"{unit}.scope"], capture_output=True)
        if unit in self.units:
            self.units.remove(unit)
        time.sleep(1.0)

    def stop_all(self) -> None:
        for unit in list(self.units):
            subprocess.run(["systemctl", "--user", "stop", f"{unit}.scope"], capture_output=True)
        self.units.clear()
        time.sleep(1.0)

    def open3d(self, render: str, windows: str, glass: str = "") -> None:
        self.ev('local p=assert(hl.plugin.hypr3d); p.config({world={monitor="HEADLESS-DESK", grid=false, '
                'live_wrap="mirror"}, render=%s, windows=%s%s}); p.open()' % (render, windows,
                                                                            f", glass={glass}" if glass else ""))
        time.sleep(3.5)

    def close3d(self) -> None:
        self.ev("if hl.plugin.hypr3d.active() then hl.plugin.hypr3d.close() end")
        time.sleep(1.5)

    def look(self, yaw: float, pitch: float, settle: float = 1.0) -> None:
        self.ev("hl.plugin.hypr3d.config({player={look_yaw=%g, look_pitch=%g}})" % (yaw, pitch))
        time.sleep(settle)


# the live toggle's render settings (SSOT [desktop.hyprland.hypr3d.config.render]) with stills on
RENDER = "{scale=2.0, msaa=4, fxaa=false, sharpen=0.25, texture_supersample=true, anisotropy=16, stills=true}"
REFERENCE = "{scale=4.0, msaa=0, fxaa=false, sharpen=0.0, texture_supersample=true, anisotropy=16, stills=true}"
HEAD_ON = "{window_scale=0.5, spawn_distance=4.238}"  # 1 logical px = 1 output px (fov 65, 1080 rows)
OBLIQUE = "{window_scale=0.5, spawn_distance=2.0}"     # the shoot's close-ups: 2.0-2.2 u
# look yaw/pitch and the screen columns the rules band spans: the rules at 45..65 degrees incidence, slanted 3..9 deg
VIEWS = ((45, 14, 700, 1150), (55, 18, 560, 1000))


# ---- tests -----------------------------------------------------------------------------------------------------------
class Static(unittest.TestCase):
    def test_ssot_declares_patch_and_keys(self):
        h = ssot()["desktop"]["hyprland"]["hypr3d"]
        patches = h["patches"]
        self.assertIn(PATCH, patches)
        self.assertEqual(patches.index(PATCH), patches.index(STILLS) + 1, "applies right after the stills patch")
        for p in patches:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, p)), p)
        cfg = h["config"]
        self.assertIn(cfg.get("glass_preset"), ssot()["desktop"]["hyprland"]["hyprglass"]["presets"],
                      "the room's glass names the desktop's hyprglass preset (one glass look)")
        self.assertEqual(cfg.get("glass_preset"),
                         ssot()["desktop"]["hyprland"]["hyprglass"]["config"]["default_preset"])
        self.assertNotIn("glass", cfg, "no hand-tuned copy of the preset")
        aniso = cfg["render"]["anisotropy"]
        self.assertIsInstance(aniso, int)
        self.assertTrue(1 <= aniso <= 16, aniso)
        self.assertTrue(cfg["render"]["texture_supersample"])
        self.assertNotIn("commit", h, "SSOT pulls latest upstream: no pinned commit")

    def test_patch_carries_the_fixes(self):
        added = patch_lines("+")
        for needle in ("class CForeignSnapshotScope", "RMOD_TYPE_TRANSLATE", "glGenerateMipmap(GL_TEXTURE_2D)",
                       "GL_LINEAR_MIPMAP_LINEAR", "GL_EXT_texture_filter_anisotropic", "uniform int uTexMip;",
                       "min(mn, 1.0 - mx)", "(1.0 + 4.0 * k), mn, mx)", "uniform vec4 uGlassDeco;", "uniform float uGlassWindowAlpha;",
                       "vec4 hgEdgePass(vec2 uv, float cornerSdf)", "float sdW = max(fwidth(sd), 1e-3);",
                       "vec4 D = C + (1.0 - C.a) * hgColor;", "unsigned int GLScene::frostGlassCopy()",
                       "uniform float uSigma;", "\"blur_sigma\"",
                       "getFullWindowReservedArea()", "WINDOW_ALPHA_ACTIVE", "{\"edge_pass\", \"edgePassGain\"",
                       "\"anisotropy\"", "texture: mip="):
            self.assertIn(needle, added, needle)
        removed = patch_lines("-")
        for needle in ("transmissionWeight", "capturedDetail", "min(mn, 2.0 - mx)",
                       "all(lessThanEqual(v_texcoord, vec2(1.0)))) discard;"):
            self.assertIn(needle, removed, f"the old term is gone: {needle}")
        self.assertNotIn("transmissionWeight", added)

    def test_patch_header_and_mode(self):
        path = os.path.join(ROOT, PATCH)
        with open(path, encoding="utf-8") as f:
            head = f.read().split("diff --git", 1)[0]
        self.assertIn("hypr3d-mios-stills.patch", head)
        self.assertGreater(len(head.splitlines()), 10, "header comment like the other MiOS patches")
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o644)

    def test_model_cas_limiter(self):
        # a white glyph edge on dark glass: centre = the dark pixel next to the glyph
        dark, white = 0.12, 1.0
        old = cas(dark, [white, dark, dark, dark], 0.25, 2.0, clamp=False)
        new = cas(dark, [white, dark, dark, dark], 0.25, 1.0)
        light = 0.87  # a white glyph at the window's 0.85 opacity: the limiter alone still rings
        self.assertLess(cas(dark, [light, dark, dark, dark], 0.25, 1.0, clamp=False), dark - 0.01)
        self.assertGreaterEqual(cas(dark, [light, dark, dark, dark], 0.25, 1.0), dark)
        self.assertLess(old, dark - 0.02, f"negative control: the 2 - mx limiter undershoots ({old:.3f})")
        self.assertGreaterEqual(new, dark - 1e-9, f"no undershoot next to white ({new:.3f})")
        mid = cas(0.4, [0.5, 0.35, 0.4, 0.42], 0.25, 1.0)
        self.assertNotAlmostEqual(mid, 0.4, places=3, msg="mid-range detail is still sharpened")

    def test_model_one_glass_look(self):
        """Desktop composite = client at its opacity over the pane. The removed key made opaque dark apps see-through
        (the backdrop dominated) and a light 1-px rule opaque with its dark matte: the deviation from the desktop is
        the negative control; the new composite is the desktop formula itself."""
        alpha_w, pane = 0.85, 0.42  # the window's opacity; the pane over a warm backdrop (luma)
        app_bg, rule = 0.06, 0.57
        desktop_bg = alpha_w * app_bg + (1 - alpha_w) * pane
        # old: snapshot opaque, client over the pane-over-black (~0.02)
        snap_bg = alpha_w * app_bg + (1 - alpha_w) * 0.02
        old_bg = keyed(snap_bg, 1.0, pane, detail=0.0)
        self.assertGreater(abs(old_bg - desktop_bg) / desktop_bg, 1.0, "negative control: the key doubled the luma")
        new_bg = alpha_w * app_bg + (1 - alpha_w) * pane
        self.assertAlmostEqual(new_bg, desktop_bg)
        # the key's phase dependence: the same rule pixel sampled at two sub-texel phases (detail high / low)
        on = keyed(alpha_w * rule + 0.003, 1.0, pane, detail=0.2)
        off = keyed(alpha_w * 0.5 * (rule + app_bg) + 0.003, 1.0, pane, detail=0.02)
        self.assertGreater(abs(on - off), 0.08, "negative control: the key turns a sampling phase into a dash")

    def test_model_corner_shadow_clip(self):
        """A drop shadow ring round a rounded box, cut the old way (discard past the arc only inside the box, draw
        everything outside it) leaves a step at the box corner; the new cut (only solid decorations) leaves none."""
        r = 28.0

        def shadow(dx, dy):  # dx, dy: px from the corner, + into the box
            d = math.hypot(max(r - dx, 0), max(r - dy, 0)) - r if dx < r and dy < r else -min(dx, dy)
            return 0.33 * max(0.0, 1 - d / 36.0) if d > 0 else 0.0

        def old(dx, dy):
            inside_box = dx >= 0 and dy >= 0
            past_arc = shadow(dx, dy) > 0
            return 0.0 if (inside_box and past_arc) else shadow(dx, dy)

        def new(dx, dy):  # only solid decorations past the outline; MiOS has none (border_size 0)
            return 0.0

        wedge, band = old(3, 3), old(-3, 3)
        self.assertGreater(abs(band - wedge), 0.2, "negative control: a square step at the box corner")
        pts = [(dx, dy) for dx in range(-14, 28) for dy in range(-14, 28) if shadow(dx, dy) > 0]
        self.assertTrue(pts)
        self.assertEqual(max(abs(new(*q) - new(3, 3)) for q in pts), 0.0, "no step and no halo past the outline")

    def test_preset_resolver_mirror(self):
        g = preset_glass(ssot(), "mios")
        p = ssot()["desktop"]["hyprland"]["hyprglass"]["presets"]["mios"]
        self.assertEqual(g["tint_color"] & 0xFF, round(p["dark"]["tint_alpha"] * 255))
        self.assertAlmostEqual(g["blur_sigma"], 4 * p["blur_strength"] * math.sqrt(p["blur_iterations"]), places=3)
        self.assertEqual(g["bevel_size"], p["bevel_size"])
        self.assertEqual(g["edge_pass"], ssot()["desktop"]["hyprland"]["hyprglass"]["config"]["edge_pass"])
        self.assertEqual(len(g), len(GLASS_KEYS) + 6)


@unittest.skipUnless(UPSTREAM, "MIOS_H3D_UPSTREAM (checkout of the current upstream HEAD) not set")
class Upstream(unittest.TestCase):
    def test_patches_apply_in_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = os.path.join(tmp, "src")
            subprocess.run(["git", "-c", "safe.directory=*", "clone", "-q", UPSTREAM, work], check=True)
            for p in ssot()["desktop"]["hyprland"]["hypr3d"]["patches"]:
                r = subprocess.run(["git", "apply", "--check", os.path.join(ROOT, p)], cwd=work, capture_output=True,
                                   text=True)
                self.assertEqual(r.returncode, 0, f"{p}: {r.stderr}")
                subprocess.run(["git", "apply", os.path.join(ROOT, p)], cwd=work, check=True)
            shutil.rmtree(work, ignore_errors=True)


@unittest.skipUnless(NEST_SIG and NEST_XDG and NEST_WL and NEST_STATUS and NEW_SO and OLD_SO and QML,
                     "nested Hypr3D session not provided")
class Nested(unittest.TestCase):
    """One capture pass per plugin and scene (setUpClass); each test measures its own defect on those frames."""

    RESULTS: dict = {}
    F: dict = {}

    @classmethod
    def setUpClass(cls):
        cls.nest = n = Nest()
        n.ev("hl.config({cursor={inactive_timeout=1}})")  # the nested software cursor hides after 1 s, in a corner
        n.hc("dispatch", "hl.dsp.cursor.move({x = 1919, y = 3079})")
        glass = lua_table(preset_glass(ssot(), ssot()["desktop"]["hyprland"]["hypr3d"]["config"]["glass_preset"]))
        cls.RESULTS["glass_projected"] = glass
        try:
            n.qml("wall", os.path.join(QML, "wall-uniform.png"))
            # scene A: the GitHub-like page
            n.qml("page")
            cls.F["2d_page"] = n.shot("2d-page.png")
            for label, so in (("old", OLD_SO), ("new", NEW_SO)):
                g = glass if label == "new" else ""  # the old toggle never projected the preset
                n.plugin(so)
                n.open3d(RENDER, HEAD_ON, g)
                cls.F[f"{label}_head"] = n.shot(f"{label}-head.png")
                cls.RESULTS[f"{label}_status"] = [n.status("render:"), n.status("texture:")]
                n.plugin(so)
                n.open3d(RENDER, OBLIQUE, g)
                for yaw, pitch, _, _ in VIEWS:
                    n.look(yaw, pitch)
                    cls.F[f"{label}_obl{yaw}"] = n.shot(f"{label}-oblique-y{yaw}.png")
                if label == "new":  # 16 box samples, no sharpening: the rules' own phase variation
                    n.plugin(so)
                    n.open3d(REFERENCE, OBLIQUE, g)
                    for yaw, pitch, _, _ in VIEWS:
                        n.look(yaw, pitch)
                        cls.F[f"ref_obl{yaw}"] = n.shot(f"ref-oblique-y{yaw}.png")
                if label == "old":  # diagnostic: the old key without CAS
                    n.plugin(so)
                    n.open3d(RENDER.replace("sharpen=0.25", "sharpen=0.0"), HEAD_ON)
                    cls.F["old_nocas_head"] = n.shot("old-head-nocas.png")
                n.close3d()
            n.stop("page")
            # scene B: a transparent terminal-like window in front of a dense text window
            n.qml("rear")
            n.qml("front")
            cls.F["2d_stack"] = n.shot("2d-stack.png")
            for label, so in (("old", OLD_SO), ("new", NEW_SO)):
                n.plugin(so)
                n.open3d(RENDER, HEAD_ON, glass if label == "new" else "")
                cls.F[f"{label}_stack"] = n.shot(f"{label}-stack.png")
                n.close3d()
        finally:
            n.stop_all()

    @classmethod
    def tearDownClass(cls):
        save_json("hypr3d-edge-quality-results.json", cls.RESULTS)

    def frame(self, key):
        return load(self.F[key])

    def test_0_new_plugin_filters_textures(self):
        st = self.RESULTS["new_status"]
        self.assertIn("scale=2", st[0])
        self.assertIn("texture: mip=1 aniso=16", st[1])
        self.assertEqual(self.RESULTS["old_status"][1], "", "negative control: the old plugin has no texture line")

    def test_1_corner_wedge(self):
        out = {}
        ref = self.frame("new_head")
        box = window_box(ref)
        scale = (box[2] - box[0]) / 1400.0
        out["box"] = box
        for label in ("old", "new"):
            im = self.frame(f"{label}_head")
            out[label] = {c: corner_metrics(im, box, c, 28 * scale) for c in ("tl", "bl")}
        self.RESULTS["corner"] = out
        for c in ("tl", "bl"):
            old, new = out["old"][c], out["new"][c]
            self.assertGreater(old["step"], 0.012, f"negative control: the old square wedge ({c}) {old}")
            self.assertLess(new["step"], 0.004, f"{c} {new}")
            self.assertLess(abs(new["halo"]), 0.004, f"no halo past the outline ({c}) {new}")
            self.assertLess(abs(new["wedge"] - new["far"]), 0.004, f"{c} {new}")

    def test_2_thin_lines(self):
        out = {}
        for yaw, _, xa, xb in VIEWS:
            ref = self.frame(f"ref_obl{yaw}")
            # rules seen in the 16x reference (a mismatched or non-rule pairing has no contrast along its line)
            lines = [ln for ln in rule_lines(ref, xa, xb, 600, 1075, 6) if line_continuity(ref, xa, xb, ln)["mean"] >= 0.1]
            self.assertGreaterEqual(len(lines), 3, f"rules found at yaw {yaw}: {lines}")
            res = {"lines": [[round(a, 2), round(b, 5)] for a, b in lines]}
            for label in ("old", "new", "ref"):
                im = self.frame(f"{label}_obl{yaw}")
                res[label] = [line_continuity(im, xa, xb, ln) for ln in lines]
            out[f"yaw{yaw}"] = res
        self.RESULTS["lines"] = out
        for key, res in out.items():
            # rules that stay on the slab over the whole column range (no gaps even in the reference)
            keep = [i for i, r in enumerate(res["ref"]) if r["gaps"] == 0.0]
            self.assertGreaterEqual(len(keep), 2, f"({key}) {res}")
            old = sum(res["old"][i]["cov"] for i in keep) / len(keep)
            new = sum(res["new"][i]["cov"] for i in keep) / len(keep)
            ref = sum(res["ref"][i]["cov"] for i in keep) / len(keep)
            res["mean_cov"] = {"old": round(old, 4), "new": round(new, 4), "ref": round(ref, 4)}
            self.assertGreater(old, 1.5 * new, f"negative control: the old rules vary more along x ({key}) {res}")
            self.assertLess(new, ref + 0.03, f"the new rules are as continuous as 16x supersampling ({key}) {res}")
            for r_new, r_ref in zip(res["new"], res["ref"]):  # gaps only where the reference has them (a rule leaving
                self.assertLessEqual(r_new["gaps"], r_ref["gaps"] + 0.03, f"({key}) {res}")  # the slab, say)

    def test_3_text_halos(self):
        out = {}
        ref = self.frame("new_head")
        box = window_box(ref)
        scale = (box[2] - box[0]) / 1400.0
        for label in ("old", "old_nocas", "new"):
            im = self.frame(f"{label}_head")
            out[label] = {"dots": dot_halo(im, box, scale), "text": text_halo(im, box, scale)}
        out["2d"] = {"dots": dot_halo(self.frame("2d_page"), box, scale),
                     "text": text_halo(self.frame("2d_page"), box, scale)}
        self.RESULTS["halos"] = out
        self.assertGreater(out["old"]["dots"]["undershoot"], 0.05, f"negative control {out['old']}")
        self.assertGreater(out["old"]["text"]["dark_ring_frac"], 0.2, f"negative control {out['old']}")
        self.assertLess(out["new"]["dots"]["undershoot"], out["2d"]["dots"]["undershoot"] + 0.015, out)
        self.assertLess(out["new"]["dots"]["max_dip"], 0.05, out)
        self.assertLess(out["new"]["text"]["dark_ring_frac"], 0.01, out)

    def test_4_slab_matches_desktop(self):
        out = {}
        ref = self.frame("new_head")
        box = window_box(ref)
        s = (box[2] - box[0]) / 1400.0
        flat = (int(box[0] + 700 * s), int(box[1] + 140 * s), int(box[0] + 1300 * s), int(box[1] + 280 * s))
        for label, key in (("desktop", "2d_page"), ("old", "old_head"), ("new", "new_head")):
            out[label] = region_stats(self.frame(key), flat)
        self.RESULTS["slab"] = out
        d, old, new = out["desktop"], out["old"], out["new"]
        self.assertGreater(abs(old["luma"] - d["luma"]) / d["luma"], 0.5, f"negative control {out}")
        self.assertLess(abs(new["luma"] - d["luma"]) / d["luma"], 0.10, out)
        self.assertLess(abs(new["sat"] - d["sat"]), 0.06, out)

    def test_5_window_behind_window(self):
        out = {}
        ref = self.frame("2d_stack")
        # front 900x560 centred: (510,260)..(1410,820); its empty right part; the rear seen directly left of it
        inside, outside = (840, 330, 1370, 780), (290, 330, 490, 780)
        for label, key in (("desktop", "2d_stack"), ("old", "old_stack"), ("new", "new_stack")):
            im = self.frame(key)
            hi, ho = hf_energy(im, inside), hf_energy(im, outside)
            out[label] = {"hf_inside": round(hi, 5), "hf_outside": round(ho, 5), "ratio": round(hi / ho, 4)}
        del ref
        self.RESULTS["behind"] = out
        # the rear text behind the front pane: its HF energy (absolute; same text, ~1:1 in all three frames) and
        # relative to the rear text seen directly (the room's direct view is a little softer: SSAA box, 0.15 u back)
        d, old, new = (out[k]["hf_inside"] for k in ("desktop", "old", "new"))
        dr, oldr, newr = (out[k]["ratio"] for k in ("desktop", "old", "new"))
        self.assertGreater(old / d, 2.5, f"negative control: the old room shows the rear text sharp {out}")
        self.assertGreater(oldr / dr, 3.0, f"negative control (ratio) {out}")
        self.assertLess(abs(new / d - 1.0), 0.30, f"the room frosts what is behind as the desktop does {out}")
        self.assertLess(newr / dr, 1.7, f"(ratio) {out}")


@unittest.skipUnless(NEST_SIG and NEST_XDG and NEST_WL and NEST_STATUS and NEW_SO and OLD_SO and QML
                     and os.environ.get("MIOS_H3D_COST"), "cost run not requested (MIOS_H3D_COST=1)")
class Cost(unittest.TestCase):
    def test_6_cost(self):
        """Reported, not gated: GPU-bound nested fps (HEADLESS-DESK at MIOS_H3D_HZ), static page and a client that
        commits every frame (a snapshot and, new, a mip chain per frame)."""
        n = Nest()
        out = {}
        mode = 'hl.monitor({output = "HEADLESS-DESK", mode = "1920x1080@%d", position = "0x2000", scale = 1})'
        glass = lua_table(preset_glass(ssot(), "mios"))
        try:
            n.qml("wall", os.path.join(QML, "wall-uniform.png"))
            n.ev(mode % HZ)
            time.sleep(1.0)
            for scene in ("page", "anim"):
                n.qml(scene)
                for label, so in (("old", OLD_SO), ("new", NEW_SO), ("old_again", OLD_SO), ("new_again", NEW_SO)):
                    n.plugin(so)
                    n.open3d(RENDER, HEAD_ON, glass if label.startswith("new") else "")
                    time.sleep(2.0)
                    samples = []
                    end = time.time() + 6.0
                    while time.time() < end:
                        st = n.status("render:")
                        if "fps=" in st:
                            samples.append(float(st.split("fps=")[1].split()[0]))
                        time.sleep(0.25)
                    samples.sort()
                    fps = samples[len(samples) // 2] if samples else 0.0
                    out[f"{scene}_{label}"] = {"fps": round(fps, 1), "ms": round(1000 / fps, 2) if fps else None,
                                               "n": len(samples)}
                    n.close3d()
                n.stop(scene)
        finally:
            try:
                n.ev(mode % 60)
            except AssertionError:
                pass
            n.stop_all()
        save_json(f"hypr3d-edge-cost-{HZ}hz.json", out)
        self.assertTrue(all(v["fps"] > 0 for v in out.values()), out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
