#!/usr/bin/env python3
# AI-hint: Tests for the MiOS-Xbox spec renderer (xb-02): render == layered SSOT merge incl. host- and user-tier overrides (positive); missing taskbar_align, blank credential, dangling ref, duplicate registry owner, GPU vendor mode each fail naming the key (negative).
# AI-related: usr/libexec/mios/win/xbox/render_spec.py, usr/lib/mios/mios_toml.py, usr/share/mios/mios.toml, /home/mios/mios-desktop-work-1/staged-ssot/xbox-spec.toml
# AI-functions: TestXboxSpecRender, TestXboxSpecNegative
"""Layers under test (lowest first): vendor mios.toml, the staged [windows.xbox] fragment (until it
is merged into the vendor file), a fixture-credential layer, a host-tier and a user-tier override.
All credentials are fixtures; no network; nothing outside a temp dir is written."""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import stat
import sys
import tempfile
import tomllib
import unittest

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.normpath(os.path.join(_HERE, ".."))
_RENDER = os.path.join(_ROOT, "usr", "libexec", "mios", "win", "xbox", "render_spec.py")
_VENDOR = os.environ.get("MIOS_XBOX_TEST_VENDOR", os.path.join(_ROOT, "usr", "share", "mios", "mios.toml"))
_FRAGMENT = os.environ.get("MIOS_XBOX_SPEC_FRAGMENT", "/home/mios/mios-desktop-work-1/staged-ssot/xbox-spec.toml")
_FEATURES_TXT = "/var/lib/mios-build/repos/mios-bootstrap-build/field/autounattend/mios-xbox-features.txt"

_spec = importlib.util.spec_from_file_location("render_spec", _RENDER)
render_spec = importlib.util.module_from_spec(_spec)
sys.modules["render_spec"] = render_spec
_spec.loader.exec_module(render_spec)
mios_toml = render_spec.mios_toml

FIXTURE_USER = "fixture-admin"
FIXTURE_PASSWORD = "fixture-Cred-7f3a"  # test fixture, not a real credential

FIXTURE_LAYER = f'''
[identity]
username = "{FIXTURE_USER}"
default_password = "{FIXTURE_PASSWORD}"
'''
HOST_LAYER = '''
[windows.xbox.preferences]
task_view_button = 0
[windows.xbox.vm]
disk_size = "200G"
'''
USER_LAYER = '''
[windows.xbox.xbox]
auto_hdr = 0
[windows.xbox.vm]
disk_size = "256G"
'''


def _vendor_has_spec() -> bool:
    with open(_VENDOR, "rb") as fh:
        return "xbox" in (tomllib.load(fh).get("windows") or {})


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="xbox-spec-test-")
        self.addCleanup(self.tmp.cleanup)
        self.spec_in_vendor = _vendor_has_spec()
        if not self.spec_in_vendor and not os.path.isfile(_FRAGMENT):
            self.skipTest("no [windows.xbox] in the vendor SSOT and no staged fragment")

    def write(self, name, text):
        p = os.path.join(self.tmp.name, name)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def layers(self, vendor=None, fragment=None, fixture=FIXTURE_LAYER, host=HOST_LAYER, user=USER_LAYER):
        out = [vendor or _VENDOR]
        if not self.spec_in_vendor:
            out.append(fragment or _FRAGMENT)
        if fixture is not None:
            out.append(self.write("fixture.toml", fixture))
        out.append(self.write("host.toml", host))
        out.append(self.write("user.toml", user))
        return out

    def spec_source_copy(self, edit):
        """Copy whichever file holds [windows.xbox], apply edit(text)->text; return (vendor, fragment)."""
        src = _VENDOR if self.spec_in_vendor else _FRAGMENT
        with open(src, encoding="utf-8") as fh:
            text = edit(fh.read())
        p = self.write("spec-source.toml", text)
        return (p, None) if self.spec_in_vendor else (None, p)

    def assertFailsNaming(self, layers, key):
        with self.assertRaises(render_spec.SpecError) as cm:
            render_spec.render_layers(layers)
        self.assertEqual(cm.exception.key, key, str(cm.exception))
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = render_spec.main(["--layers", *layers, "--out", os.path.join(self.tmp.name, "neg.json")])
        self.assertEqual(rc, 2)
        self.assertIn(key, err.getvalue())
        self.assertNotIn(FIXTURE_PASSWORD, err.getvalue())
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "neg.json")))
        return cm.exception


class TestXboxSpecRender(_Base):
    def setUp(self):
        super().setUp()
        self.lay = self.layers()
        self.merged = mios_toml.load_merged(self.lay)
        self.out = render_spec.render_layers(self.lay)

    def reg(self, name, owner=None):
        hits = [r for r in self.out["registry"] if r["name"] == name and (owner is None or r["owner"] == owner)]
        self.assertEqual(len(hits), 1, name)
        return hits[0]

    def test_spec_equals_layered_merge(self):
        self.assertEqual(self.out["spec"], self.merged["windows"]["xbox"])
        self.assertEqual(self.out["layers"], self.lay)

    def test_host_and_user_tier_overrides(self):
        spec = self.out["spec"]
        self.assertEqual(spec["preferences"]["task_view_button"], 0)          # host tier
        self.assertEqual(self.reg("ShowTaskViewButton")["data"], 0)
        self.assertEqual(spec["xbox"]["auto_hdr"], 0)                         # user tier
        hdr = self.reg("DirectXUserGlobalSettings")
        self.assertEqual((hdr["kv_field"], hdr["data"]), ("AutoHDREnable", 0))
        self.assertEqual(spec["vm"]["disk_size"], "256G")                     # user beats host

    def test_vendor_values(self):
        spec = self.out["spec"]
        self.assertEqual(spec["preferences"]["taskbar_align"], 1)
        self.assertEqual(self.reg("TaskbarAl")["data"], 1)
        self.assertEqual(self.reg("SearchboxTaskbarMode")["data"], 0)
        self.assertEqual(spec["start"]["folders"], ["PersonalFolder", "Network", "FileExplorer", "Settings"])
        shown = {r["name"] for r in self.out["registry"] if r["owner"] == "start.folders" and r["data"] == 1}
        hidden = {r["name"] for r in self.out["registry"] if r["owner"] == "start.folders" and r["data"] == 0}
        self.assertEqual(shown, {"AllowPinnedFolder" + f for f in ("PersonalFolder", "Network", "FileExplorer", "Settings")})
        self.assertIn("AllowPinnedFolderDownloads", hidden)
        self.assertIn("AllowPinnedFolderDocuments", hidden)
        self.assertEqual(json.loads(self.reg("ConfigureStartPins")["data"]), {"pinnedList": []})
        self.assertEqual(spec["start"]["taskbar_pins"], [])
        self.assertEqual(spec["drivers"]["gpu"], "detect")
        self.assertEqual(spec["vm"]["name"], "mios-xbox")
        self.assertEqual(spec["vm"]["worker"]["name"], "mios-xbox-worker")
        self.assertFalse(spec["vm"]["worker"]["gpu_passthrough"])

    def test_refs_resolve_to_ssot(self):
        res = self.out["resolved"]
        for path, target in render_spec._walk_refs(self.out["spec"]):
            if path in render_spec.CREDENTIAL_REFS:
                self.assertEqual(res[path], "<credential>")
            else:
                self.assertEqual(res[path], render_spec._get(self.merged, target), path)
        users = self.out["users"]
        self.assertEqual(users["desktop"]["name"], FIXTURE_USER)
        self.assertTrue(users["desktop"]["password"] == FIXTURE_PASSWORD)
        self.assertTrue(users["builtin_administrator"]["password"] == FIXTURE_PASSWORD)
        self.assertEqual(users["builtin_administrator"]["disable_at"], "finalize")

    def test_feature_toggles_cover_features_file(self):
        ids = {f["id"] for f in self.out["feature_overrides"]}
        self.assertTrue({58989070, 59765208} <= ids)
        if os.path.isfile(_FEATURES_TXT):
            with open(_FEATURES_TXT, encoding="utf-8") as fh:
                want = {int(m.group(1)) for line in fh if (m := re.match(r"\s*(\d+)", line))}
            self.assertTrue(want <= ids)
        written = {r["key"].rsplit("\\", 1)[1] for r in self.out["registry"] if r["name"] == "EnabledState"}
        self.assertEqual(written, {str(f["id"]) for f in self.out["feature_overrides"] if f["enabled"]})

    def test_no_pins_in_spec(self):
        blob = json.dumps(self.out["spec"])
        self.assertNotIn("stable-virtio", blob)
        self.assertNotRegex(blob, r'"(build|version|[a-z_]*_version)": "\d')
        for name, tbl in self.out["spec"]["guest_tools"].items():
            self.assertNotIn("version", tbl, name)

    def test_cli_writes_0600_without_printing_secrets(self):
        dst = os.path.join(self.tmp.name, "xbox-spec.json")
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = render_spec.main(["--layers", *self.lay, "--out", dst])
        self.assertEqual(rc, 0)
        self.assertEqual(stat.S_IMODE(os.stat(dst).st_mode), 0o600)
        self.assertNotIn(FIXTURE_PASSWORD, out.getvalue() + err.getvalue())
        with open(dst, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["spec"], self.out["spec"])


class TestXboxSpecNegative(_Base):
    def test_missing_taskbar_align_fails(self):
        def drop(text):
            new, n = re.subn(r"(?m)^taskbar_align\s*=.*\n", "", text)
            self.assertEqual(n, 1)
            return new
        vendor, frag = self.spec_source_copy(drop)
        self.assertFailsNaming(self.layers(vendor=vendor, fragment=frag), "windows.xbox.preferences.taskbar_align")

    def test_blank_credential_fails(self):
        with open(_VENDOR, encoding="utf-8") as fh:
            text, n = re.subn(r'(?m)^(default_password\s*=\s*)"[^"]*"', r'\1""', fh.read(), count=1)
        self.assertEqual(n, 1)
        vendor = self.write("vendor-blank.toml", text)
        lay = self.layers(vendor=vendor, fixture=f'[identity]\nusername = "{FIXTURE_USER}"\n')
        self.assertFailsNaming(lay, "identity.default_password")

    def test_blank_credential_ref_fails(self):
        host = HOST_LAYER + '[windows.xbox.users.desktop]\npassword_ref = "identity.no_such_password"\n'
        self.assertFailsNaming(self.layers(host=host), "windows.xbox.users.desktop.password_ref")

    def test_duplicate_registry_owner_fails(self):
        user = USER_LAYER + ('[windows.xbox.registry_map]\n"preferences.taskbar_chat" = { hive = "DEFAULT_USER", '
                             'key = "Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Explorer\\\\Advanced", '
                             'name = "TaskbarAl", type = "REG_DWORD" }\n')
        err = self.assertFailsNaming(self.layers(user=user), "windows.xbox.preferences.taskbar_chat")
        self.assertIn("already owned", str(err))

    def test_gpu_vendor_mode_fails(self):
        user = USER_LAYER + '[windows.xbox.drivers]\ngpu = "nvidia"\n'
        self.assertFailsNaming(self.layers(user=user), "windows.xbox.drivers.gpu")

    def test_unmapped_timezone_fails(self):
        fixture = FIXTURE_LAYER + '[locale]\ntimezone = "Mars/Olympus_Mons"\n'
        self.assertFailsNaming(self.layers(fixture=fixture), "windows.xbox.settings.region.timezone_map.Mars/Olympus_Mons")


if __name__ == "__main__":
    unittest.main()
