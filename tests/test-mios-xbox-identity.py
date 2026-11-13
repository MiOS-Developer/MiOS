#!/usr/bin/env python3
# AI-hint: Two-sided tests for xbox-bm-39, the MiOS identity of every MiOS-Xbox image: accent, cursor scheme, font, persistent theme and OEM branding from the SSOT; render, payload, image gate and probe each refuse or name a planted drift.
# AI-related: usr/share/mios/mios.toml, usr/libexec/mios/win/xbox/render_spec.py, usr/libexec/mios/win/xbox/answer.py, usr/libexec/mios/win/xbox/fetch.py, usr/libexec/mios/win/xbox/spec_items.py, usr/libexec/mios/win/xbox/conform.py, usr/libexec/mios/win/xbox/probe.py, usr/share/mios/windows/xbox/mios-xbox-probe.ps1, usr/share/mios/windows/build/New-MiOSXboxImage.ps1, tests/_mios_xbox_fixtures.py
"""Run: python3 -I tests/test-mios-xbox-identity.py -v   (temp dirs only; no network, no VM; the probe-script case runs
with pwsh or Windows PowerShell and is skipped, named as UNRUN, without either)"""

from __future__ import annotations

import base64
import copy
import fractions
import functools
import hashlib
import importlib.util
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import xml.etree.ElementTree as ET
import tempfile
import unittest
import zipfile
import zlib

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("test_mios_xbox_provisioning_identity", os.path.join(HERE, "test-mios-xbox-provisioning.py"))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)
A, C, R, S, FX = P.A, P.C, P.R, P.S, P.FX
_probe_spec = importlib.util.spec_from_file_location("mios_xbox_probe_identity", os.path.join(P.XBOX, "probe.py"))
PR = importlib.util.module_from_spec(_probe_spec)
_probe_spec.loader.exec_module(PR)

PROBE_PS1 = os.path.join(P.ROOT, "usr", "share", "mios", "windows", "xbox", "mios-xbox-probe.ps1")
REG_REL = A.STATE_DIR.split("/") + ["xbox-registry.reg"]


def _powershell():
    return shutil.which("pwsh") or (shutil.which("powershell") if os.name == "nt" else None)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity_items(items):
    return [it for it in items if it["id"].startswith(("identity.", "reg.identity."))]


def read_reg(path: str) -> str:
    with open(path, "rb") as fh:
        raw = fh.read()
    assert raw[:2] == b"\xff\xfe", "REGEDIT5 must be UTF-16LE with BOM"
    return raw[2:].decode("utf-16-le")


def write_reg(path: str, text: str) -> None:
    with open(path, "wb") as fh:
        fh.write(b"\xff\xfe" + text.encode("utf-16-le"))


def append_byte(path: str) -> None:
    with open(path, "ab") as fh:
        fh.write(b"\0")


def rezip(src: str, dst: str, drop=(), add=None) -> str:
    """A copy of a zip without the members in drop and with add {member: bytes} written in."""
    with zipfile.ZipFile(src) as zi, zipfile.ZipFile(dst, "w") as zo:
        for m in zi.namelist():
            if m not in drop:
                zo.writestr(m, zi.read(m))
        for m, data in (add or {}).items():
            zo.writestr(m, data)
    return dst


def det_bytes(n: int, seed: str) -> bytes:
    """n deterministic pseudo-random bytes."""
    out = b"".join(hashlib.sha256(f"{seed}:{i}".encode()).digest() for i in range(n // 32 + 1))
    return out[:n]


def png_chunks(chunks) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"".join(struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
                                            for t, d in chunks)


def png_file(w: int, h: int, pixels: bytes, ctype: int = 6, filters=(0,), depth: int = 8, interlace: int = 0) -> bytes:
    """A PNG written test-side: row y uses filters[y % len(filters)], filtered with the PNG specification's predictors."""
    bpp = 4 if ctype == 6 else 3
    stride, raw, prev = w * bpp, bytearray(), bytes(w * bpp)
    for y in range(h):
        line, ft = pixels[y * stride:(y + 1) * stride], filters[y % len(filters)]
        raw.append(ft)
        for i in range(stride):
            a, b, c = (line[i - bpp] if i >= bpp else 0), prev[i], (prev[i - bpp] if i >= bpp else 0)
            pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)   # |p-a|, |p-b|, |p-c| with p = a + b - c
            pred = (0, a, b, (a + b) // 2, a if pa <= pb and pa <= pc else b if pb <= pc else c)[ft]
            raw.append((line[i] - pred) & 0xFF)
        prev = line
    return png_chunks([(b"IHDR", struct.pack(">IIBBBBB", w, h, depth, ctype, 0, 0, interlace)),
                       (b"IDAT", zlib.compress(bytes(raw))), (b"IEND", b"")])


def ref_logo_pixels(w: int, h: int, rgba: bytes, px: int, bg: tuple) -> list:
    """Top-down [(r, g, b)] of an exact 2-D area average (rationals) of premultiplied RGBA over bg, rounded half up."""
    fr, out = fractions.Fraction, []
    for oy in range(px):
        y0, y1 = fr(oy * h, px), fr((oy + 1) * h, px)
        for ox in range(px):
            x0, x1 = fr(ox * w, px), fr((ox + 1) * w, px)
            col, alpha = [fr(0)] * 3, fr(0)
            for sy in range(h):
                dy = min(y1, sy + 1) - max(y0, sy)
                for sx in range(w):
                    dx = min(x1, sx + 1) - max(x0, sx)
                    if dy > 0 and dx > 0:
                        r, g, b, a = rgba[(sy * w + sx) * 4:(sy * w + sx) * 4 + 4]
                        alpha += dx * dy * a
                        col = [col[i] + dx * dy * a * v for i, v in enumerate((r, g, b))]
            cell = (x1 - x0) * (y1 - y0) * 255
            out.append(tuple(math.floor(col[i] / cell + bg[i] * (1 - alpha / cell) + fr(1, 2)) for i in range(3)))
    return out


def bmp32_pixels(bmp: bytes, px: int) -> list:
    """Top-down [(r, g, b, a)] of a bottom-up 32-bit BMP."""
    rows = [bmp[54 + y * px * 4:54 + (y + 1) * px * 4] for y in range(px)][::-1]
    return [(r[i + 2], r[i + 1], r[i], r[i + 3]) for r in rows for i in range(0, len(r), 4)]


class Base(P.PayloadBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.items = S.items_from_spec(cls.doc, P.ROOT)
        cls.ident = identity_items(cls.items)
        cls.idn = cls.doc["identity"]
        cls.cur = cls.idn["cursor"]
        cls.font = cls.idn["font"]

    def scratch(self, prefix: str = "t.") -> str:
        """A fresh directory under the class scratch, removed when the test ends."""
        d = tempfile.mkdtemp(prefix=prefix, dir=self.tmp)
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def font_tree(self, mark: bytes = None, rel: str = None) -> str:
        """A scratch tree with the vendored font archive (so only the OEM logo can fail) and, if given, a mark at rel."""
        tree = self.scratch("tree.")
        arch = os.path.join(tree, *self.font["archive"].split("/"))
        os.makedirs(os.path.dirname(arch))
        shutil.copyfile(os.path.join(P.ROOT, *self.font["archive"].split("/")), arch)
        if mark is not None:
            p = os.path.join(tree, *(rel or self.idn["oem_logo"]["source"]).split("/"))
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "wb") as fh:
                fh.write(mark)
        return tree

    def resolved(self, merged=None):
        """(windows.xbox table, every resolved identity *_ref) the way render() hands them to render_identity."""
        m = merged if merged is not None else self.merged
        x = m["windows"]["xbox"]
        get = lambda dotted: functools.reduce(lambda d, k: d[k], dotted.split("."), m)   # noqa: E731
        return x, {f"identity.{k}": get(v) for k, v in x["identity"].items() if k.endswith("_ref")}

    def ref(self, merged, key):
        """The SSOT table/value a [windows.xbox.identity] ref names, inside merged (so a plant edits the SSOT); for a key
        that is not a ref, the [windows.xbox.identity] table itself."""
        idn = merged["windows"]["xbox"]["identity"]
        if not key.endswith("_ref"):
            return idn, key
        path = idn[key].split(".")
        return functools.reduce(lambda d, k: d[k], path[:-1], merged), path[-1]


# ---------------------------------------------------------------------------
# SSOT: every identity value comes from the one shared key, validated at render time
# ---------------------------------------------------------------------------

class TestSpec(Base):
    def test_positive_accent_byte_order_from_the_shared_accent(self):
        x, res = self.resolved()
        res["identity.accent_ref"] = "#1A407F"
        rec, reg = R.render_identity(x, res)
        self.assertEqual(rec["accent_abgr"], 0xFF7F401A)   # DWM AccentColor / Explorer AccentColorMenu: 0xAABBGGRR
        self.assertEqual(rec["accent_argb"], 0xFF1A407F)   # unattend Themes/WindowColor: 0xAARRGGBB
        self.assertEqual(rec["theme"]["window_color"], "0xff1a407f")   # lower-case with the 0x prefix (Microsoft's format)
        by = {r["owner"]: r for r in reg}
        for owner in ("identity.accent.dwm_accent", "identity.accent.menu"):
            self.assertEqual(by[owner]["data"], 0xFF7F401A, owner)
        # values Windows derives from WindowColor at the first sign-in are not pinned (they would read off-spec in the guest)
        self.assertNotIn("identity.accent.start_menu", by)
        self.assertNotIn("identity.accent.dwm_colorization", by)
        # the rendered spec carries the SSOT accent itself, never a second copy
        tbl, key = self.ref(self.merged, "accent_ref")
        self.assertEqual(self.idn["accent"], tbl[key])

    def test_positive_cursor_scheme_and_font_rows(self):
        reg = {r["owner"]: r for r in self.doc["registry"] if r["owner"].startswith("identity.")}
        self.assertEqual(reg["identity.cursor.scheme_name"]["name"], "")            # the key's default value (@)
        self.assertEqual(reg["identity.cursor.scheme_name"]["data"], self.cur["scheme_name"])
        self.assertEqual(reg["identity.cursor.scheme_source"]["data"], R.SCHEME_SOURCE_SYSTEM)
        live = "C:\\Windows\\" + self.cur["install_dir"] + "\\"
        for role, f in self.cur["roles"].items():
            self.assertEqual(reg[f"identity.cursor.role.{role}"]["data"], live + f, role)
        self.assertEqual(reg["identity.cursor.system_scheme"]["data"].split(","), [live + f for f in self.cur["scheme_files"]])
        theme = self.resolved()[1]["identity.cursor_theme_ref"]
        self.assertTrue(self.cur["scheme_name"].startswith(theme), "the Windows build is the SSOT cursor theme")
        self.assertTrue(self.font["files"])
        again = R._font_members(self.font["family"], os.path.join(P.ROOT, *self.font["archive"].split("/")))
        self.assertEqual(again, self.font["files"])
        for f in self.font["files"]:
            self.assertRegex(f["file"], r"\.(otf|ttf)$")
            self.assertRegex(f["sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(reg[f"identity.font.{f['file']}"]["name"], f["registry_name"])
            self.assertEqual(reg[f"identity.font.{f['file']}"]["data"], f["file"])

    def test_positive_theme_and_oem_come_from_the_ssot(self):
        th, m = self.idn["theme"], self.merged
        self.assertEqual(th["name"], m["theme"]["windows"]["theme_name"])
        self.assertEqual(th["mode"], m["theme"]["mode"])
        self.assertEqual(th["uwp_apps_use_light_theme"], m["theme"]["mode"] == "light")
        self.assertEqual(th["transparency"], m["theme"]["windows"]["transparency"])
        bg = th["background"]
        self.assertEqual(bg["color"].lower(), m["colors"]["bg"].lower())
        self.assertTrue(bg["unattend"].lower().startswith("%windir%\\web\\"), bg["unattend"])   # a subfolder of %WINDIR%
        data = R.solid_bmp(R._rgb(bg["color"], "test"), bg["px"])
        self.assertEqual((_sha(data), len(data)), (bg["sha256"], bg["size"]))
        self.assertEqual(data, R.solid_bmp(R._rgb(bg["color"], "test"), bg["px"]))   # byte-identical every time
        self.assertEqual(data[:2], b"BM")
        self.assertEqual(struct.unpack("<iiHH", data[18:30]), (bg["px"], bg["px"], 1, 24))
        self.assertEqual(data[54:57], bytes(reversed(R._rgb(bg["color"], "test"))))   # BGR
        reg = {r["owner"]: r for r in self.doc["registry"] if r["owner"].startswith("identity.")}
        light = 1 if th["mode"] == "light" else 0
        self.assertEqual(reg["identity.theme.apps_light"]["data"], light)
        self.assertEqual(reg["identity.theme.system_light"]["data"], light)
        self.assertEqual(reg["identity.theme.transparency"]["data"], int(m["theme"]["windows"]["transparency"]))
        for value_name, key in (("Manufacturer", "oem_manufacturer"), ("Model", "oem_model"),
                                ("SupportURL", "oem_support_url"), ("SupportHours", "oem_support_hours")):
            r = reg[f"identity.oem.{key[4:]}"]
            self.assertEqual((r["hive"], r["key"], r["name"], r["data"]), ("SOFTWARE", R.OEM_KEY, value_name, m["branding"][key]))
            self.assertEqual(self.idn["oem"][value_name], m["branding"][key])

    def test_positive_items_cover_every_identity_file_and_value(self):
        ids = {it["id"] for it in self.ident}
        self.assertIn("reg.identity.cursor.scheme_name", ids)
        self.assertIn("reg.identity.accent.dwm_accent", ids)
        for f in self.cur["scheme_files"]:
            self.assertIn(f"identity.cursor.{f}", ids)
        for f in self.font["files"]:
            self.assertIn(f"identity.font.{f['file']}", ids)
        for iid in ("identity.theme", "identity.theme.background", "reg.identity.theme.system_light",
                    "reg.identity.theme.apps_light", "reg.identity.theme.transparency", "reg.identity.oem.manufacturer",
                    "reg.identity.oem.model", "reg.identity.oem.support_url", "reg.identity.oem.support_hours",
                    "reg.identity.oem.logo", "identity.oem_logo"):
            self.assertIn(iid, ids)
        S.validate_items(self.ident)

    def test_negative_render_identity_refuses_drift(self):
        cases = {
            "accent not #RRGGBB": ("accent_ref", None, "#1A407"),
            "cursor build over plain http": ("cursor_ref", "asset_url", "http://example.invalid/c.zip"),
            "cursor pin not a sha256": ("cursor_ref", "asset_sha256", "0" * 63),
            "cursor pin missing": ("cursor_ref", "asset_sha256", ""),
            "role outside the scheme": ("cursor_ref", "roles", lambda v: {**v, "Arrow": "not_in_scheme.cur"}),
            "role file not a plain name": ("cursor_ref", "roles", lambda v: {**v, "Arrow": "..\\x.cur"}),
            "base size out of range": ("cursor_ref", "base_size", 8),
            "off-theme cursor build": ("cursor_ref", "scheme_name", "Other-Theme Cursors"),
            "install_dir escapes the system root": ("cursor_ref", "install_dir", "..\\..\\Temp"),
            "font archive drifted from its pin": ("font_ref", "native_sha256", "0" * 64),
            "font family absent from the archive": ("font_ref", "family", "No Such Family Mono"),
            "font archive not in the tree": ("font_ref", "native_archive", "usr/share/mios/vendored/fonts/absent.zip"),
            "accent too dark for WindowColor": ("accent_ref", None, "#05050A"),
            "accent too light for WindowColor": ("accent_ref", None, "#F0F0FA"),
            "theme name empty": ("theme_name_ref", None, " "),
            "theme mode not dark/light": ("theme_mode_ref", None, "dim"),
            "transparency not a boolean": ("transparency_ref", None, "yes"),
            "background colour not #RRGGBB": ("background_color_ref", None, "indigo"),
            "background not a .bmp": ("background", None, "Windows\\Web\\Wallpaper\\MiOS\\mios.jpg"),
            "background outside %WINDIR%": ("background", None, "ProgramData\\MiOS\\mios-background.bmp"),
            "background not in a subfolder": ("background", None, "Windows\\mios-background.bmp"),
            "background escapes": ("background", None, "Windows\\..\\Temp\\mios-background.bmp"),
            "OEM manufacturer empty": ("oem_manufacturer_ref", None, " "),
            "OEM support URL not a string": ("oem_support_url_ref", None, 7),
        }
        for name, (ref, field, val) in cases.items():
            merged = copy.deepcopy(self.merged)
            tbl, key = self.ref(merged, ref)
            if field is None:
                tbl[key] = val
            else:
                tbl[key][field] = val(tbl[key][field]) if callable(val) else val
            x, res = self.resolved(merged)
            with self.subTest(case=name), self.assertRaises(R.SpecError):
                R.render_identity(x, res)

    def test_negative_render_refuses_a_bad_accent_end_to_end(self):
        merged = copy.deepcopy(self.merged)
        tbl, key = self.ref(merged, "accent_ref")
        tbl[key] = "blue"
        with self.assertRaises(R.SpecError):
            R.render(merged, P.LAYERS)


# ---------------------------------------------------------------------------
# OEM logo: the MiOS mark decoded (stdlib PNG), box-filtered to 120x120 and written as a 32-bit BMP, exactly
# ---------------------------------------------------------------------------

class TestOemLogo(Base):
    def test_positive_png_round_trip_every_filter_and_colour_type(self):
        for ctype, bpp in ((6, 4), (2, 3)):
            pix = det_bytes(7 * 5 * bpp, f"px{ctype}")
            want = pix if bpp == 4 else b"".join(pix[i:i + 3] + b"\xff" for i in range(0, len(pix), 3))
            for filters in ((0,), (1,), (2,), (3,), (4,), (0, 1, 2, 3, 4)):
                with self.subTest(ctype=ctype, filters=filters):
                    self.assertEqual(R.png_rgba(png_file(7, 5, pix, ctype, filters)), (7, 5, want))

    def test_positive_downscale_equals_an_exact_rational_reference(self):
        bg = (10, 200, 30)
        for w, h, px in ((7, 7, 3), (5, 5, 5), (8, 8, 3), (6, 4, 3)):
            rgba = bytearray(det_bytes(w * h * 4, f"ds{w}{h}"))
            rgba[3::8] = bytes(len(rgba[3::8]))   # some fully transparent pixels
            bmp = R.rgba_to_bmp32(w, h, bytes(rgba), px, bg)
            with self.subTest(size=(w, h, px)):
                self.assertEqual(bmp[:2], b"BM")
                self.assertEqual(struct.unpack("<IiiHHI", bmp[14:34]), (40, px, px, 1, 32, 0))
                self.assertEqual(len(bmp), 54 + px * px * 4)
                got = bmp32_pixels(bmp, px)
                self.assertEqual([p[:3] for p in got], ref_logo_pixels(w, h, bytes(rgba), px, bg))
                self.assertEqual({p[3] for p in got}, {255})
        for n_src, n_dst in ((512, 120), (5, 3), (3, 3)):
            for taps in R._box_taps(n_src, n_dst):
                self.assertEqual(sum(wt for _, wt in taps), n_src)
                self.assertEqual([s for s, _ in taps], list(range(taps[0][0], taps[-1][0] + 1)))

    def test_positive_half_transparent_white_composites_by_hand(self):
        rgba = bytes([255, 255, 255, 255, 0, 0, 0, 0, 0, 0, 0, 0, 255, 255, 255, 255])   # 2x2: two opaque white, two clear
        self.assertEqual(bmp32_pixels(R.rgba_to_bmp32(2, 2, rgba, 1, (0, 0, 0)), 1), [(128, 128, 128, 255)])
        self.assertEqual(bmp32_pixels(R.rgba_to_bmp32(2, 2, rgba, 1, (255, 0, 0)), 1), [(255, 128, 128, 255)])

    def test_positive_logo_record_bitmap_and_value_from_the_ssot(self):
        lg, m = self.idn["oem_logo"], self.merged
        self.assertEqual(lg["live"], m["branding"]["oem_logo"])
        self.assertEqual(lg["source"], m["desktop"]["portal"]["gecko"]["icon_source"])
        self.assertEqual(lg["path"], lg["live"][3:].replace("\\", "/"))
        self.assertEqual(lg["path"].split("/")[0].lower(), self.x["identity"]["system_root"].lower())
        self.assertEqual((lg["px"], lg["background"].lower()), (R.OEM_LOGO_PX, m["colors"]["bg"].lower()))
        with open(os.path.join(P.ROOT, *lg["source"].split("/")), "rb") as fh:
            mark = fh.read()
        self.assertEqual(_sha(mark), lg["source_sha256"])
        R.oem_logo_bmp.cache_clear()
        bmp = R.oem_logo_bmp(mark, lg["px"], R._rgb(lg["background"], "test"))
        self.assertEqual((_sha(bmp), len(bmp)), (lg["sha256"], lg["size"]))   # byte-identical to the spec's record
        self.assertEqual(struct.unpack("<iiHH", bmp[18:30]), (120, 120, 1, 32))   # Microsoft: 32-bit colour, 120x120
        self.assertEqual({p[3] for p in bmp32_pixels(bmp, 120)}, {255})
        reg = {r["owner"]: r for r in self.doc["registry"]}
        r = reg["identity.oem.logo"]
        self.assertEqual((r["hive"], r["key"], r["name"], r["type"], r["data"]), ("SOFTWARE", R.OEM_KEY, "Logo", "REG_SZ", lg["live"]))

    def test_negative_png_decoder_refuses_what_it_cannot_read(self):
        good = png_file(4, 4, det_bytes(64, "neg"))
        ihdr_end = 8 + 8 + 13 + 4
        bad_crc = good[:ihdr_end - 1] + bytes([good[ihdr_end - 1] ^ 1]) + good[ihdr_end:]
        raw16 = png_chunks([(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 16, 6, 0, 0, 0)), (b"IDAT", zlib.compress(b"\0" * 132)), (b"IEND", b"")])
        cases = {
            "not a PNG": (b"BM" + good[2:], "not a PNG"),
            "chunk CRC broken": (bad_crc, "fails its CRC"),
            "truncated in a chunk header": (good[:ihdr_end + 10], "truncated"),
            "truncated in a chunk body": (good[:ihdr_end + 20], "truncated"),
            "no IEND": (good[:-12], "no IEND"),
            "16-bit": (raw16, "unsupported PNG"),
            "palette": (png_file(4, 4, det_bytes(48, "p"), 3), "unsupported PNG"),
            "interlaced": (png_file(4, 4, det_bytes(64, "i"), interlace=1), "unsupported PNG"),
            "unknown filter type": (png_chunks([(b"IHDR", struct.pack(">IIBBBBB", 2, 1, 8, 6, 0, 0, 0)),
                                                (b"IDAT", zlib.compress(b"\x05" + bytes(8))), (b"IEND", b"")]), "filter type 5"),
            "image data of the wrong size": (png_chunks([(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 0)),
                                                         (b"IDAT", zlib.compress(bytes(9))), (b"IEND", b"")]), "wrong size"),
            "image data not zlib": (png_chunks([(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 0)),
                                                (b"IDAT", b"not zlib"), (b"IEND", b"")]), "does not inflate"),
        }
        for name, (data, why) in cases.items():
            with self.subTest(case=name), self.assertRaisesRegex(ValueError, why):
                R.png_rgba(data)
        for name, (w, h) in {"not square": (240, 120), "smaller than 120": (64, 64)}.items():
            with self.subTest(case=name), self.assertRaisesRegex(ValueError, "square and at least 120x120"):
                R.oem_logo_bmp(png_file(w, h, bytes(w * h * 4)), 120, (0, 0, 0))

    def test_negative_render_refuses_a_bad_logo_path_or_mark(self):
        root = self.x["identity"]["system_root"]
        paths = {
            "another drive": f"D:\\{root}\\Web\\MiOS\\mios-logo.bmp",
            "not a .bmp": f"C:\\{root}\\Web\\MiOS\\mios-logo.png",
            "outside %SystemRoot%": "C:\\ProgramData\\MiOS\\mios-logo.bmp",
            "not in a subfolder": f"C:\\{root}\\mios-logo.bmp",
            "escapes": f"C:\\{root}\\..\\Temp\\MiOS\\mios-logo.bmp",
            "empty folder name": f"C:\\{root}\\Web\\\\mios-logo.bmp",
            "relative": f"{root}\\Web\\MiOS\\mios-logo.bmp",
            "longer than 256": f"C:\\{root}\\Web\\" + "x" * 250 + ".bmp",
        }
        for name, val in paths.items():
            merged = copy.deepcopy(self.merged)
            tbl, key = self.ref(merged, "oem_logo_ref")
            tbl[key] = val
            x, res = self.resolved(merged)
            with self.subTest(case=name), self.assertRaisesRegex(R.SpecError, re.escape(self.x["identity"]["oem_logo_ref"])):
                R.render_identity(x, res)
        src_key = re.escape(self.x["identity"]["oem_logo_source_ref"])
        marks = {
            "mark not in the tree": ("usr/share/mios/portal/absent.png", None, "not a file in the tree"),
            "mark escapes the tree": ("../outside.png", None, "not a file in the tree"),
            "mark absolute": ("/etc/hosts", None, "not a file in the tree"),
            "mark not a PNG": ("usr/share/mios/branding/mios.txt", None, "not a PNG"),
            "mark too small": ("usr/share/mios/test/small.png", png_file(64, 64, bytes(64 * 64 * 4)), "at least 120x120"),
            "mark not square": ("usr/share/mios/test/wide.png", png_file(240, 120, bytes(240 * 120 * 4)), "square"),
        }
        for name, (rel, data, why) in marks.items():
            merged = copy.deepcopy(self.merged)
            tbl, key = self.ref(merged, "oem_logo_source_ref")
            tbl[key] = rel
            x, res = self.resolved(merged)
            tree = self.font_tree(data, rel) if data is not None else P.ROOT
            with self.subTest(case=name), self.assertRaisesRegex(R.SpecError, f"{src_key}: .*{why}"):
                R.render_identity(x, res, tree)

    def test_png_decoder_and_bitmap_agree_with_windows_gdiplus(self):
        ps = _powershell()
        if os.name != "nt" or not ps:
            self.skipTest("UNRUN: needs Windows PowerShell or pwsh with System.Drawing (Windows only)")
        lg = self.idn["oem_logo"]
        with open(os.path.join(P.ROOT, *lg["source"].split("/")), "rb") as fh:
            mark = fh.read()
        logo = R.oem_logo_bmp(mark, lg["px"], R._rgb(lg["background"], "test"))
        d = self.scratch("gdip.")
        files = {"mark": os.path.join(d, "mark.png"), "logo": os.path.join(d, "logo.bmp")}
        for k, data in (("mark", mark), ("logo", logo)):
            with open(files[k], "wb") as fh:
                fh.write(data)
        script = os.path.join(d, "dump.ps1")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("param([string]$Path)\nAdd-Type -AssemblyName System.Drawing\n$b = [System.Drawing.Bitmap]::FromFile($Path)\n"
                     "try {\n  $r = New-Object System.Drawing.Rectangle 0, 0, $b.Width, $b.Height\n"
                     "  $d = $b.LockBits($r, [System.Drawing.Imaging.ImageLockMode]::ReadOnly, [System.Drawing.Imaging.PixelFormat]::Format32bppArgb)\n"
                     "  try { $buf = New-Object byte[] ($d.Stride * $b.Height); [System.Runtime.InteropServices.Marshal]::Copy($d.Scan0, $buf, 0, $buf.Length)\n"
                     "        \"$($b.Width) $($b.Height) $($d.Stride) \" + [Convert]::ToBase64String($buf) } finally { $b.UnlockBits($d) }\n"
                     "} finally { $b.Dispose() }\n")

        def gdip(path):
            out = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script, "-Path", path],
                                 capture_output=True, text=True, timeout=120)
            self.assertEqual(out.returncode, 0, out.stderr[-1500:])
            w, h, stride, b64 = out.stdout.split()
            self.assertEqual(int(stride), int(w) * 4)
            raw = base64.b64decode(b64)
            return int(w), int(h), [(raw[i + 2], raw[i + 1], raw[i], raw[i + 3]) for i in range(0, len(raw), 4)]

        w, h, rgba = R.png_rgba(mark)
        gw, gh, gpx = gdip(files["mark"])
        ours = [tuple(rgba[i:i + 4]) for i in range(0, len(rgba), 4)]
        self.assertEqual((gw, gh), (w, h))
        self.assertEqual([p[3] for p in gpx], [p[3] for p in ours])                               # alpha everywhere
        self.assertEqual([p for p in gpx if p[3]], [p for p in ours if p[3]])                     # colour where visible
        lw, lh, lpx = gdip(files["logo"])
        self.assertEqual((lw, lh), (lg["px"], lg["px"]))
        self.assertEqual([p[:3] for p in lpx], [p[:3] for p in bmp32_pixels(logo, lg["px"])])


# ---------------------------------------------------------------------------
# payload: exactly the pinned files, refused on any disagreement
# ---------------------------------------------------------------------------

class TestPayload(Base):
    def stage(self, doc, inputs, tree_root=None):
        out = self.scratch("ident.")
        return A.stage_identity(doc, inputs, out, tree_root or P.ROOT), out

    def bad_archive(self, plant):
        """(doc, inputs) whose cursor archive was built from a planted contract and is re-pinned in the doc, so only the
        install.inf/member check can catch it."""
        doc = copy.deepcopy(self.doc)
        fake = copy.deepcopy(doc)
        plant(fake["identity"]["cursor"])
        inp = FX.cursor_archive_inputs(self.scratch("cur."), fake)
        doc["identity"]["cursor"]["asset_sha256"] = inp["cursor_archive"]["sha256"]
        return doc, {"identity": inp}

    def test_positive_payload_carries_every_identity_file(self):
        rows = {r["name"]: r for r in self.summary["identity"]}
        want = ({f"cursor:{f}" for f in self.cur["scheme_files"]} | {f"font:{f['file']}" for f in self.font["files"]}
                | {"theme:background", "oem:logo"})
        self.assertEqual(set(rows), want)
        for r in rows.values():
            with open(os.path.join(self.pay, *r["path"].split("/")), "rb") as fh:
                self.assertEqual(_sha(fh.read()), r["sha256"], r["name"])
        with open(os.path.join(self.pay, *A.STATE_DIR.split("/"), "payload.json")) as fh:
            self.assertEqual(json.load(fh)["identity"], self.summary["identity"])

    def test_positive_registry_carries_the_scheme_default_value_and_fonts(self):
        text = read_reg(os.path.join(self.pay, *REG_REL))
        self.assertIn(f'@="{self.cur["scheme_name"]}"', text.splitlines())
        for f in self.font["files"]:
            self.assertIn(f'"{f["registry_name"]}"="{f["file"]}"', text.splitlines())
        self.assertIn('"Logo"="' + self.idn["oem_logo"]["live"].replace("\\", "\\\\") + '"', text.splitlines())
        self.assertNotIn('""=', text)

    def test_positive_answer_file_carries_the_theme(self):
        root = ET.parse(os.path.join(self.pay, "autounattend.xml")).getroot()
        ns = {"u": A.UNS}
        tms = root.findall("u:settings[@pass='oobeSystem']/u:component[@name='Microsoft-Windows-Shell-Setup']/u:Themes", ns)
        self.assertEqual(len(tms), 1)
        th = self.idn["theme"]
        got = [(e.tag.split("}")[-1], e.text) for e in tms[0]]
        self.assertEqual(got, [("DesktopBackground", th["background"]["unattend"]), ("ThemeName", th["name"]),
                               ("UWPAppsUseLightTheme", str(th["uwp_apps_use_light_theme"]).lower()),
                               ("WindowColor", th["window_color"])])   # the schema's element order

    def test_negative_answer_file_refuses_a_spec_without_a_theme(self):
        doc = copy.deepcopy(self.doc)
        del doc["identity"]["theme"]
        with self.assertRaisesRegex(A.PayloadError, "identity.theme"):
            A.render_answer_file(doc)

    def test_positive_stage_identity_on_a_fresh_tree(self):
        rows, _ = self.stage(self.doc, self.inputs)
        self.assertEqual(rows, self.summary["identity"])

    def test_negative_stage_refuses_a_wrong_or_disagreeing_cursor_archive(self):
        src = self.inputs["identity"]["cursor_archive"]["path"]
        var = self.cur["variant_dir"]
        tampered = rezip(src, os.path.join(self.tmp, "tampered.zip"), add={f"{var}/extra.cur": b"x"})
        missing = rezip(src, os.path.join(self.tmp, "missing.zip"), drop=(f"{var}/{self.cur['scheme_files'][-1]}",))
        no_inf = rezip(src, os.path.join(self.tmp, "noinf.zip"), drop=(f"{var}/install.inf",))

        def repinned(path):
            doc = copy.deepcopy(self.doc)
            with open(path, "rb") as fh:
                doc["identity"]["cursor"]["asset_sha256"] = _sha(fh.read())
            return doc, {"identity": {"cursor_archive": {"path": path}}}

        def swap_roles(c):
            a = sorted(c["roles"])[0]
            b = next(r for r in sorted(c["roles"]) if c["roles"][r] != c["roles"][a])   # two roles on different files
            c["roles"][a], c["roles"][b] = c["roles"][b], c["roles"][a]

        cases = {
            "archive differs from the pin": ((self.doc, {"identity": {"cursor_archive": {"path": tampered}}}), "sha256 differs"),
            "archive not fetched": ((self.doc, {}), "not fetched"),
            "SCHEME_NAME differs": (self.bad_archive(lambda c: c.__setitem__("scheme_name", c["scheme_name"] + " X")), "SCHEME_NAME"),
            "CUR_DIR differs": (self.bad_archive(lambda c: c.__setitem__("install_dir", "Cursors\\Other")), "CUR_DIR"),
            "scheme file order differs": (self.bad_archive(lambda c: c.__setitem__("scheme_files", list(reversed(c["scheme_files"])))),
                                          "scheme file order"),
            "role map differs": (self.bad_archive(swap_roles), "role map"),
            "a cursor file is missing": (repinned(missing), "missing from"),
            "no install.inf": (repinned(no_inf), "install.inf"),
        }
        for name, ((doc, inputs), why) in cases.items():
            with self.subTest(case=name), self.assertRaisesRegex(A.PayloadError, re.escape(why)):
                self.stage(doc, inputs)

    def test_negative_stage_refuses_a_font_archive_changed_since_render(self):
        tree = self.scratch("tree.")
        arch = os.path.join(tree, *self.font["archive"].split("/"))
        os.makedirs(os.path.dirname(arch))
        rezip(os.path.join(P.ROOT, *self.font["archive"].split("/")), arch, drop=(self.font["files"][0]["member"],))
        with self.assertRaisesRegex(A.PayloadError, "absent or changed"):
            self.stage(self.doc, self.inputs, tree)
        with self.assertRaisesRegex(A.PayloadError, "absent or changed"):
            self.stage(self.doc, self.inputs, self.scratch("empty."))

    def test_negative_stage_refuses_a_background_that_differs_from_the_spec(self):
        doc = copy.deepcopy(self.doc)
        doc["identity"]["theme"]["background"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(A.PayloadError, "differs from the spec's sha256"):
            self.stage(doc, self.inputs)
        doc = copy.deepcopy(self.doc)
        doc["identity"]["theme"]["background"]["color"] = "#000001"   # a colour the recorded sha256 is not of
        with self.assertRaisesRegex(A.PayloadError, "differs from the spec's sha256"):
            self.stage(doc, self.inputs)
        doc = copy.deepcopy(self.doc)
        del doc["identity"]["theme"]
        with self.assertRaisesRegex(A.PayloadError, "identity.theme.background"):
            self.stage(doc, self.inputs)

    def test_positive_stage_renders_the_logo_from_a_tree_with_the_same_mark(self):
        with open(os.path.join(P.ROOT, *self.idn["oem_logo"]["source"].split("/")), "rb") as fh:
            rows, out = self.stage(self.doc, self.inputs, self.font_tree(fh.read()))
        row = next(r for r in rows if r["name"] == "oem:logo")
        with open(os.path.join(out, *row["path"].split("/")), "rb") as fh:
            self.assertEqual(_sha(fh.read()), self.idn["oem_logo"]["sha256"])

    def test_negative_stage_refuses_an_oem_logo_that_differs_from_the_spec(self):
        lg = self.idn["oem_logo"]
        with open(os.path.join(P.ROOT, *lg["source"].split("/")), "rb") as fh:
            mark = fh.read()
        other = png_file(120, 120, det_bytes(120 * 120 * 4, "other mark"))
        changed = re.escape(lg["source"]) + " is absent or changed"

        def plant(field, val):
            doc = copy.deepcopy(self.doc)
            doc["identity"]["oem_logo"][field] = val
            return doc

        no_logo = copy.deepcopy(self.doc)
        del no_logo["identity"]["oem_logo"]
        cases = {
            "recorded sha256 differs": (plant("sha256", "0" * 64), None, "OEM logo: the rendered image differs"),
            "recorded background differs": (plant("background", "#000001"), None, "OEM logo: the rendered image differs"),
            "mark changed since render": (self.doc, self.font_tree(other), changed),
            "mark absent": (self.doc, self.font_tree(), changed),
            "no logo record": (no_logo, None, "identity.oem_logo"),
        }
        self.assertNotEqual(_sha(other), _sha(mark))
        for name, (doc, tree, why) in cases.items():
            with self.subTest(case=name), self.assertRaisesRegex(A.PayloadError, why):
                self.stage(doc, self.inputs, tree)


# ---------------------------------------------------------------------------
# image gate (conform, rendered inputs)
# ---------------------------------------------------------------------------

class TestImageGate(Base):
    def ids(self, pay):
        obs = C.collect_rendered([pay, self.inputs["drivers_dir"]])
        results, findings = C.evaluate(self.ident, obs, "rendered", S.spec_policy(self.doc))
        return {r["id"]: r for r in results}, {f.id for f in findings if "identity" in f.id}

    def plant_reg(self, pay, fn):
        p = os.path.join(pay, *REG_REL)
        write_reg(p, fn(read_reg(p)))

    def plant_answer(self, pay, fn):
        p = os.path.join(pay, "autounattend.xml")
        with open(p, encoding="utf-8") as fh:
            text = fh.read()
        new = fn(text)
        assert new != text, "the plant changed nothing"
        with open(p, "w", encoding="utf-8", newline="") as fh:
            fh.write(new)

    def test_positive_conforming_payload_has_no_identity_finding(self):
        _, ids = self.ids(self.pay)
        self.assertEqual(ids, set())

    def test_negative_missing_or_altered_identity_is_named(self):
        f0 = self.font["files"][0]
        c0 = self.cur["scheme_files"][0]
        font_path = lambda d: os.path.join(d, *self.font["dest_dir"].split("/"), f0["file"])   # noqa: E731
        cur_path = lambda d: os.path.join(d, *self.cur["dest_dir"].split("/"), c0)             # noqa: E731
        accent = f'"AccentColor"=dword:{self.idn["accent_abgr"]:08x}'
        th = self.idn["theme"]
        light = 1 if th["mode"] == "light" else 0
        logo_path = lambda d: os.path.join(d, *self.idn["oem_logo"]["path"].split("/"))   # noqa: E731
        logo_line = '"Logo"="' + self.idn["oem_logo"]["live"].replace("\\", "\\\\") + '"\r\n'
        cases = {
            "font file removed": (lambda d: os.remove(font_path(d)), f"missing:identity.font.{f0['file']}"),
            "font file altered": (lambda d: append_byte(font_path(d)), f"off-spec:identity.font.{f0['file']}"),
            "cursor file removed": (lambda d: os.remove(cur_path(d)), f"missing:identity.cursor.{c0}"),
            "cursor scheme value removed": (lambda d: self.plant_reg(d, lambda t: t.replace(f'@="{self.cur["scheme_name"]}"\r\n', "")),
                                            "missing:reg.identity.cursor.scheme_name"),
            "accent off-spec": (lambda d: self.plant_reg(d, lambda t: t.replace(accent, '"AccentColor"=dword:ff000000')),
                                "off-spec:reg.identity.accent.dwm_accent"),
            "font registration removed": (lambda d: self.plant_reg(d, lambda t: t.replace(f'"{f0["registry_name"]}"="{f0["file"]}"\r\n', "")),
                                          f"missing:reg.identity.font.{f0['file']}"),
            "Themes element removed": (lambda d: self.plant_answer(d, lambda t: re.sub(r"\s*<Themes>.*?</Themes>", "", t, flags=re.S)),
                                       "missing:identity.theme"),
            "WindowColor not the accent": (lambda d: self.plant_answer(d, lambda t: t.replace(f"<WindowColor>{th['window_color']}<", "<WindowColor>0xff0078d7<")),
                                           "off-spec:identity.theme.WindowColor"),
            "another theme name": (lambda d: self.plant_answer(d, lambda t: t.replace(f"<ThemeName>{th['name']}<", "<ThemeName>Windows<")),
                                   "off-spec:identity.theme.ThemeName"),
            "apps colour mode flipped": (lambda d: self.plant_answer(d, lambda t: re.sub(r"<UWPAppsUseLightTheme>\w+<",
                                         f"<UWPAppsUseLightTheme>{str(not th['uwp_apps_use_light_theme']).lower()}<", t)),
                                         "off-spec:identity.theme.UWPAppsUseLightTheme"),
            "theme background removed": (lambda d: os.remove(os.path.join(d, *th["background"]["path"].split("/"))),
                                         "missing:identity.theme.background"),
            "system colour mode flipped": (lambda d: self.plant_reg(d, lambda t: t.replace(f'"SystemUsesLightTheme"=dword:{light:08x}',
                                                                                             f'"SystemUsesLightTheme"=dword:{1 - light:08x}')),
                                           "off-spec:reg.identity.theme.system_light"),
            "OEM manufacturer removed": (lambda d: self.plant_reg(d, lambda t: t.replace(f'"Manufacturer"="{self.idn["oem"]["Manufacturer"]}"\r\n', "")),
                                         "missing:reg.identity.oem.manufacturer"),
            "OEM logo removed": (lambda d: os.remove(logo_path(d)), "missing:identity.oem_logo"),
            "OEM logo altered": (lambda d: append_byte(logo_path(d)), "off-spec:identity.oem_logo"),
            "OEM Logo value removed": (lambda d: self.plant_reg(d, lambda t: t.replace(logo_line, "")), "missing:reg.identity.oem.logo"),
        }
        for name, (plant, want) in cases.items():
            d = self.copy_payload()
            plant(d)
            with self.subTest(case=name):
                _, ids = self.ids(d)
                self.assertIn(want, ids)


# ---------------------------------------------------------------------------
# in-guest probe
# ---------------------------------------------------------------------------

class TestProbe(Base):
    def probe(self):
        users = [it for it in self.items if it["kind"] == "user"]
        return FX.conforming_probe(self.ident + users, "", P.ROOT, self.doc)

    def ev(self, doc):
        results, findings = PR.evaluate(self.ident, doc)
        return {r["id"]: r for r in results}, {f.id for f in findings}

    def test_positive_conforming_guest(self):
        res, ids = self.ev(self.probe())
        self.assertEqual(ids, set())
        self.assertEqual(res["identity.theme"]["status"], "skipped")   # an answer-file element: the image gate's check
        for iid in ("reg.identity.theme.system_light", "reg.identity.oem.manufacturer", "identity.theme.background",
                    "identity.oem_logo", "reg.identity.oem.logo"):
            self.assertEqual(res[iid]["status"], "pass", iid)

    def test_negative_guest_drift_is_named(self):
        f0 = self.font["files"][0]["file"]
        c0 = self.cur["scheme_files"][0]
        cases = {
            "font absent": (f"identity.font.{f0}", {"present": False}, f"missing:identity.font.{f0}"),
            "font altered": (f"identity.font.{f0}", {"present": True, "sha256": "0" * 64}, f"off-spec:identity.font.{f0}"),
            "cursor file absent": (f"identity.cursor.{c0}", {"present": False}, f"missing:identity.cursor.{c0}"),
            "scheme value absent": ("reg.identity.cursor.scheme_name", {"present": False}, "missing:reg.identity.cursor.scheme_name"),
            "accent off-spec": ("reg.identity.accent.dwm_accent", {"present": True, "type": "dword", "value": 0xFF000000},
                                "off-spec:reg.identity.accent.dwm_accent"),
            "system colour mode flipped": ("reg.identity.theme.system_light",
                                           {"present": True, "type": "dword", "value": 0 if self.idn["theme"]["mode"] == "light" else 1},
                                           "off-spec:reg.identity.theme.system_light"),
            "theme background absent": ("identity.theme.background", {"present": False}, "missing:identity.theme.background"),
            "OEM manufacturer absent": ("reg.identity.oem.manufacturer", {"present": False}, "missing:reg.identity.oem.manufacturer"),
            "OEM logo absent": ("identity.oem_logo", {"present": False}, "missing:identity.oem_logo"),
            "OEM logo altered": ("identity.oem_logo", {"present": True, "sha256": "0" * 64}, "off-spec:identity.oem_logo"),
            "Logo value elsewhere": ("reg.identity.oem.logo", {"present": True, "type": "sz", "value": "C:\\Windows\\OEM\\logo.bmp"},
                                     "off-spec:reg.identity.oem.logo"),
        }
        for name, (iid, obs, want) in cases.items():
            doc = self.probe()
            doc["observations"][iid] = obs
            with self.subTest(case=name):
                self.assertIn(want, self.ev(doc)[1])

    def test_probe_script_file_hash_branch_both_ways(self):
        ps = _powershell()
        if not ps:
            self.skipTest("UNRUN: no pwsh/powershell on this host")
        with open(PROBE_PS1, encoding="utf-8-sig") as fh:
            text = fh.read()
        m = re.search(r"^([ \t]*)'file_hash' \{\r?\n.*?^\1\}", text, re.S | re.M)
        self.assertIsNotNone(m, "the probe script has no file_hash branch")
        f0 = self.font["files"][0]
        drive = self.scratch("sysdrive.")
        dst = os.path.join(drive, *self.font["dest_dir"].split("/"), f0["file"])
        os.makedirs(os.path.dirname(dst))
        shutil.copyfile(os.path.join(self.pay, *self.font["dest_dir"].split("/"), f0["file"]), dst)
        it = next(i for i in self.ident if i["id"] == f"identity.font.{f0['file']}")
        absent = {**it, "id": "identity.font.absent.otf", "path": self.font["dest_dir"] + "/absent.otf"}
        # the request shape the probe script reads ($req.items): a bare top-level array would arrive as ONE object in PS 5.1
        items_json = FX.write_json(os.path.join(self.tmp, "file-hash-request.json"), {"items": [it, absent]})
        harness = os.path.join(self.tmp, "file-hash-harness.ps1")
        with open(harness, "w", encoding="utf-8") as fh:
            fh.write("param([string]$Items)\n$ErrorActionPreference = 'Stop'\n$obsMap = [ordered]@{}\n"
                     "$req = Get-Content -LiteralPath $Items -Raw | ConvertFrom-Json\n"
                     "foreach ($it in @($req.items)) {\n    $id = [string]$it.id\n    switch ([string]$it.kind) {\n"
                     + m.group(0) + "\n    }\n}\n$obsMap | ConvertTo-Json -Depth 4 -Compress\n")
        # the branch resolves image-relative paths against $env:SystemDrive: point it at a temp tree
        env = {k: v for k, v in os.environ.items() if k.upper() != "SYSTEMDRIVE"}
        env["SystemDrive"] = drive
        out = subprocess.run([ps, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", harness, "-Items", items_json],
                             capture_output=True, text=True, env=env, timeout=120)
        self.assertEqual(out.returncode, 0, out.stderr[-1500:])
        obs = json.loads(out.stdout)
        self.assertEqual(obs[it["id"]], {"present": True, "sha256": f0["sha256"]})
        self.assertEqual(obs[absent["id"]], {"present": False})
        doc = self.probe()
        doc["observations"].update(obs)
        results, findings = PR.evaluate([it, absent], doc)
        self.assertEqual({f.id for f in findings}, {f"missing:{absent['id']}"})


if __name__ == "__main__":
    unittest.main()
