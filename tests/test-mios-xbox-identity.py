#!/usr/bin/env python3
# AI-hint: Two-sided tests for xbox-bm-39 (the MiOS identity in every MiOS-Xbox image): render_spec derives the accent, the Bibata cursor scheme and the Geist Mono Nerd Font family from the one shared SSOT value each and refuses a malformed accent, an unpinned/plain-http/off-theme cursor build, a role outside the scheme, an escaping install_dir, a bad base size and a font archive that drifted from its pin; answer.py stages exactly the pinned files and refuses a cursor archive with the wrong sha256, an install.inf that disagrees with the SSOT (scheme name, CUR_DIR, file order, role map), a missing cursor file and a changed font archive; the image gate (conform, rendered) and the in-guest probe (probe.py, and the probe script's file_hash branch EXECUTED in PowerShell) name a missing or altered font, a missing cursor file, a missing scheme value and an off-spec accent. Every positive control has a planted negative control. No network, no VM.
# AI-related: usr/share/mios/mios.toml [windows.xbox.identity] [theme.cursor_windows] [theme.font] [colors], usr/libexec/mios/win/xbox/render_spec.py, usr/libexec/mios/win/xbox/answer.py, usr/libexec/mios/win/xbox/fetch.py, usr/libexec/mios/win/xbox/spec_items.py, usr/libexec/mios/win/xbox/conform.py, usr/libexec/mios/win/xbox/probe.py, usr/share/mios/windows/xbox/mios-xbox-probe.ps1, usr/share/mios/windows/build/New-MiOSXboxImage.ps1, tests/_mios_xbox_fixtures.py
"""Run: python3 -I tests/test-mios-xbox-identity.py -v   (temp dirs only; no network, no VM; the probe-script case runs
with pwsh or Windows PowerShell and is skipped, named as UNRUN, without either)"""

from __future__ import annotations

import copy
import functools
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

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


class Base(P.PayloadBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.items = S.items_from_spec(cls.doc, P.ROOT)
        cls.ident = identity_items(cls.items)
        cls.idn = cls.doc["identity"]
        cls.cur = cls.idn["cursor"]
        cls.font = cls.idn["font"]

    def resolved(self, merged=None):
        """(windows.xbox table, the four resolved identity refs) the way render() hands them to render_identity."""
        m = merged if merged is not None else self.merged
        x = m["windows"]["xbox"]
        get = lambda dotted: functools.reduce(lambda d, k: d[k], dotted.split("."), m)   # noqa: E731
        return x, {f"identity.{k}": get(x["identity"][k]) for k in ("accent_ref", "cursor_ref", "cursor_theme_ref", "font_ref")}

    def ref(self, merged, key):
        """The SSOT table/value a [windows.xbox.identity] ref names, inside merged (so a plant edits the SSOT)."""
        path = merged["windows"]["xbox"]["identity"][key].split(".")
        return functools.reduce(lambda d, k: d[k], path[:-1], merged), path[-1]


# ---------------------------------------------------------------------------
# SSOT: every identity value comes from the one shared key, validated at render time
# ---------------------------------------------------------------------------

class TestSpec(Base):
    def test_positive_accent_byte_order_from_the_shared_accent(self):
        x, res = self.resolved()
        res["identity.accent_ref"] = "#1A407F"
        rec, reg = R.render_identity(x, res)
        self.assertEqual(rec["accent_abgr"], 0xFF7F401A)   # DWM AccentColor / Explorer accent: 0xAABBGGRR
        self.assertEqual(rec["accent_argb"], 0xFF1A407F)   # DWM ColorizationColor: 0xAARRGGBB
        by = {r["owner"]: r for r in reg}
        for owner in ("identity.accent.dwm_accent", "identity.accent.menu", "identity.accent.start_menu"):
            self.assertEqual(by[owner]["data"], 0xFF7F401A, owner)
        self.assertEqual(by["identity.accent.dwm_colorization"]["data"], 0xFF1A407F)
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

    def test_positive_items_cover_every_identity_file_and_value(self):
        ids = {it["id"] for it in self.ident}
        self.assertIn("reg.identity.cursor.scheme_name", ids)
        self.assertIn("reg.identity.accent.dwm_accent", ids)
        for f in self.cur["scheme_files"]:
            self.assertIn(f"identity.cursor.{f}", ids)
        for f in self.font["files"]:
            self.assertIn(f"identity.font.{f['file']}", ids)
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
# payload: exactly the pinned files, refused on any disagreement
# ---------------------------------------------------------------------------

class TestPayload(Base):
    def stage(self, doc, inputs, tree_root=None):
        out = tempfile.mkdtemp(prefix="ident.", dir=self.tmp)
        return A.stage_identity(doc, inputs, out, tree_root or P.ROOT), out

    def bad_archive(self, plant):
        """(doc, inputs) whose cursor archive was built from a planted contract and is re-pinned in the doc, so only the
        install.inf/member check can catch it."""
        doc = copy.deepcopy(self.doc)
        fake = copy.deepcopy(doc)
        plant(fake["identity"]["cursor"])
        inp = FX.cursor_archive_inputs(tempfile.mkdtemp(prefix="cur.", dir=self.tmp), fake)
        doc["identity"]["cursor"]["asset_sha256"] = inp["cursor_archive"]["sha256"]
        return doc, {"identity": inp}

    def test_positive_payload_carries_every_identity_file(self):
        rows = {r["name"]: r for r in self.summary["identity"]}
        want = {f"cursor:{f}" for f in self.cur["scheme_files"]} | {f"font:{f['file']}" for f in self.font["files"]}
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
        self.assertNotIn('""=', text)

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
        tree = tempfile.mkdtemp(prefix="tree.", dir=self.tmp)
        arch = os.path.join(tree, *self.font["archive"].split("/"))
        os.makedirs(os.path.dirname(arch))
        rezip(os.path.join(P.ROOT, *self.font["archive"].split("/")), arch, drop=(self.font["files"][0]["member"],))
        with self.assertRaisesRegex(A.PayloadError, "absent or changed"):
            self.stage(self.doc, self.inputs, tree)
        with self.assertRaisesRegex(A.PayloadError, "absent or changed"):
            self.stage(self.doc, self.inputs, tempfile.mkdtemp(prefix="empty.", dir=self.tmp))


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

    def test_positive_conforming_payload_has_no_identity_finding(self):
        _, ids = self.ids(self.pay)
        self.assertEqual(ids, set())

    def test_negative_missing_or_altered_identity_is_named(self):
        f0 = self.font["files"][0]
        c0 = self.cur["scheme_files"][0]
        font_path = lambda d: os.path.join(d, *self.font["dest_dir"].split("/"), f0["file"])   # noqa: E731
        cur_path = lambda d: os.path.join(d, *self.cur["dest_dir"].split("/"), c0)             # noqa: E731
        accent = f'"AccentColor"=dword:{self.idn["accent_abgr"]:08x}'
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
        _, ids = self.ev(self.probe())
        self.assertEqual(ids, set())

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
        drive = tempfile.mkdtemp(prefix="sysdrive.", dir=self.tmp)
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
