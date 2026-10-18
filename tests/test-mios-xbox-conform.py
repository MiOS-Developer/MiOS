#!/usr/bin/env python3
# AI-hint: Two-sided tests for the MiOS-Xbox offline conformance gate (xb-03/xb-16): a bundle rendered from the fixture xbox-spec.json passes (rendered inputs and a real offline install.wim built with hivexregedit + wimlib-imagex), and every planted legacy value or missing item -- TaskbarAl=0, SearchboxTaskbarMode=1, Start_ShowDownloads/Documents=1, LockedStartLayout, an NVIDIA/AMD/Intel display .inf, a literal/empty/foreign credential, a removed component or virtio driver, a stable-virtio URL, a Start family mismatch, an off-spec SSOT value -- fails with its named finding; a legacy-shaped answer file is reported non-conforming.
# AI-related: usr/libexec/mios/win/xbox/conform.py, usr/libexec/mios/win/xbox/spec_items.py, tests/_mios_xbox_fixtures.py, tests/fixtures/mios-xbox/xbox-spec.fixture.json
"""MiOS-Xbox offline conformance gate tests.  Optional: MIOS_XBOX_LEGACY_ANSWERFILE=<readable copy of
the legacy autounattend.xml> adds a check against the real legacy answer file."""

from __future__ import annotations

import base64
import contextlib
import copy
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _mios_xbox_fixtures as FX  # noqa: E402

C = FX.load_module("mios_xbox_conform", os.path.join(FX.XBOX_DIR, "conform.py"))
S = FX.S
BRANCH = "rs_prerelease"
FIXTURE_PW = None  # set in setUpModule; only ever compared, never printed


def setUpModule():
    global FIXTURE_PW
    FIXTURE_PW = FX.load_fixture_spec()["users"]["desktop"]["password"]


def _ids(findings):
    return {f.id for f in findings}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mios-xbox-conform-test.")
        cls.doc = FX.load_fixture_spec()
        cls.root = FX.fixture_root(cls.tmp, cls.doc)
        cls.items = S.items_from_spec(cls.doc, cls.root)
        cls.policy = S.spec_policy(cls.doc)
        cls.spec_path = FX.write_json(os.path.join(cls.tmp, "xbox-spec.json"), cls.doc)
        cls.good = FX.render_bundle(os.path.join(cls.tmp, "good"), cls.doc, cls.items, cls.root, BRANCH)
        cls.n = 0

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def copy_good(self):
        type(self).n += 1
        d = os.path.join(self.tmp, f"case{self.n}")
        shutil.copytree(self.good, d)
        return d

    def rendered(self, bundle, items=None):
        obs = C.collect_rendered([bundle])
        if not obs.answer_seen:
            obs.findings.append(S.Finding("answer.missing", "x"))
        return C.evaluate(items or self.items, obs, "rendered", self.policy)

    def item(self, iid):
        return next(i for i in self.items if i["id"] == iid)

    def append(self, bundle, line, rel="payload/Windows/Setup/Scripts/SetupComplete.cmd"):
        with open(os.path.join(bundle, rel), "a", encoding="utf-8") as fh:
            fh.write(line + "\r\n")

    def assertFinding(self, bundle, fid, items=None):
        _, f = self.rendered(bundle, items)
        self.assertIn(fid, _ids(f), f"expected {fid}; got {sorted(_ids(f))[:20]}")


class TestRenderedPositive(Base):
    def test_item_inventory_covers_every_spec_area(self):
        kinds = {i["kind"] for i in self.items}
        for k in ("reg", "feature", "capability", "driver", "guest_tool", "component", "build_family",
                  "user", "rid500", "computer_name", "appx", "gpu_step"):
            self.assertIn(k, kinds)
        ids = {i["id"] for i in self.items}
        self.assertIn("reg.preferences.taskbar_align", ids)
        self.assertIn("reg.preferences.search_box_mode", ids)
        self.assertTrue(any(i.startswith("reg.xbox.feature_overrides.xbox_fse_") for i in ids))
        self.assertFalse(any("password" in json.dumps(i) and "password_sha256" not in i for i in self.items if i["kind"] == "user"))

    def test_conforming_bundle_is_green(self):
        results, f = self.rendered(self.good)
        self.assertEqual([], [str(x) for x in f])
        self.assertFalse([r for r in results if r["status"] == "fail"])
        passed = {r["id"] for r in results if r["status"] == "pass"}
        for iid in ("reg.preferences.taskbar_align", "drivers.virtio_win.viostor", "components.miosd",
                    "users.desktop", "users.rid500", "start.layout_family", "features.Microsoft-Hyper-V-All"):
            self.assertIn(iid, passed)

    def test_cli_exit_codes_and_report(self):
        rep = os.path.join(self.tmp, "report.json")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = C.main(["--root", self.root, "rendered", "--spec", self.spec_path, "--input", self.good, "--report", rep])
        self.assertEqual(0, rc, out.getvalue()[-2000:])
        with open(rep) as fh:
            data = json.load(fh)
        self.assertTrue(data["ok"])
        self.assertEqual("mios-xbox-conform/1", data["schema"])
        self.assertNotIn(FIXTURE_PW, out.getvalue())
        self.assertNotIn(FIXTURE_PW, json.dumps(data))

    def test_variable_password_not_flagged(self):
        b = self.copy_good()
        self.append(b, 'New-LocalUser -Name "Admin" -Password "$AdminPassword"', rel="payload/Windows/Setup/Scripts/x.ps1")
        _, f = self.rendered(b)
        self.assertNotIn("deny.literal-credential", _ids(f))


class TestRenderedNegative(Base):
    def test_taskbar_left_aligned(self):
        items = copy.deepcopy(self.items)
        next(i for i in items if i["id"] == "reg.preferences.taskbar_align")["value"] = 0
        b = FX.render_bundle(os.path.join(self.tmp, "left"), self.doc, items, self.root, BRANCH)
        _, f = self.rendered(b)
        self.assertIn("off-spec:reg.preferences.taskbar_align", _ids(f))
        self.assertIn("deny.TaskbarAl=0", _ids(f))

    def test_search_box_shown(self):
        b = self.copy_good()
        self.append(b, r'reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Search" /v SearchboxTaskbarMode /t REG_DWORD /d 1 /f')
        _, f = self.rendered(b)
        self.assertIn("deny.SearchboxTaskbarMode=1", _ids(f))
        self.assertIn("off-spec:reg.preferences.search_box_mode", _ids(f))

    def test_start_show_downloads_documents(self):
        b = self.copy_good()
        self.append(b, r'reg add "HKU\MiOSDefault\Software\Microsoft\Windows\CurrentVersion\Start" /v Start_ShowDownloads /t REG_DWORD /d 1 /f')
        self.append(b, "Set-ItemProperty -Path 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Start' -Name Start_ShowDocuments -Value 1 -Type DWord",
                    rel="payload/Windows/Setup/Scripts/mios-firstlogon.ps1")
        _, f = self.rendered(b)
        self.assertIn("deny.Start_ShowDownloads=1", _ids(f))
        self.assertIn("deny.Start_ShowDocuments=1", _ids(f))

    def test_locked_start_layout_block(self):
        b = self.copy_good()
        self.append(b, r'reg add "HKLM\SOFTWARE\Policies\Microsoft\Windows\Explorer" /v LockedStartLayout /t REG_DWORD /d 1 /f')
        self.assertFinding(b, "deny.LockedStartLayout")
        b2 = self.copy_good()
        with open(os.path.join(b2, "payload", "LayoutModification.xml"), "w") as fh:
            fh.write('<LayoutModificationTemplate xmlns="http://schemas.microsoft.com/Start/2014/LayoutModification"/>\n')
        self.append(b2, r'copy "%~dp0LayoutModification.xml" "C:\Users\Default\AppData\Local\Microsoft\Windows\Shell\"')
        self.assertFinding(b2, "deny.LockedStartLayout")

    def test_gpu_vendor_display_inf(self):
        for base, prov in (("nvlddmkm", "NVIDIA"), ("fixture_amd_disp", "Advanced Micro Devices, Inc."),
                           ("fixture_intel_disp", "Intel Corporation")):
            with self.subTest(base=base):
                b = self.copy_good()
                d = os.path.join(b, "drivers", base)
                os.makedirs(d)
                with open(os.path.join(d, base + ".inf"), "w") as fh:
                    fh.write(FX.virtio_inf(base, cls="Display", provider=prov))
                self.assertFinding(b, "drivers.gpu-vendor")

    def test_non_display_vendor_inf_is_not_a_gpu_driver(self):
        b = self.copy_good()
        d = os.path.join(b, "drivers", "fixture_intel_net")
        os.makedirs(d)
        with open(os.path.join(d, "fixture_intel_net.inf"), "w") as fh:
            fh.write(FX.virtio_inf("fixture_intel_net", cls="Net", provider="Intel Corporation"))
        _, f = self.rendered(b)
        self.assertNotIn("drivers.gpu-vendor", _ids(f))

    def test_literal_mios_password(self):
        b = self.copy_good()
        with open(os.path.join(b, "autounattend.xml"), "w") as fh:
            spec = "\r\n".join(FX.reg_add_line("HKU\\MiOSDefault", i) for i in self.items if i["kind"] == "reg" and i["scope"] == "user" and not i.get("absent"))
            fh.write(FX.answer_file(self.doc, spec, desktop_pw="mios"))
        _, f = self.rendered(b)
        self.assertIn("deny.literal-credential", _ids(f))
        self.assertIn("users.password:users.desktop", _ids(f))
        b2 = self.copy_good()
        self.append(b2, 'schtasks /create /tn "MiOS\\Legacy" /ru user /rp mios /tr x.cmd /sc onlogon /f')
        self.assertFinding(b2, "deny.literal-credential")

    def test_foreign_credential_literal_in_script(self):
        b = self.copy_good()
        self.append(b, "$p = ConvertTo-SecureString 'Hunter2-not-ssot' -AsPlainText -Force", rel="payload/Windows/Setup/Scripts/x.ps1")
        self.assertFinding(b, "deny.literal-credential")

    def test_evasion_positional_set_itemproperty(self):
        b = self.copy_good()
        self.append(b, 'Set-ItemProperty "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon" -Name DefaultPassword -Value "Hunter2-positional-leak"', rel="payload/Windows/Setup/Scripts/x.ps1")
        self.assertFinding(b, "deny.literal-credential")

    def test_evasion_reordered_convertto_securestring(self):
        b = self.copy_good()
        self.append(b, "$p = ConvertTo-SecureString -AsPlainText -Force 'Hunter2-reordered'", rel="payload/Windows/Setup/Scripts/x.ps1")
        self.assertFinding(b, "deny.literal-credential")

    def test_evasion_piped_convertto_securestring(self):
        b = self.copy_good()
        self.append(b, "'Hunter2-piped' | ConvertTo-SecureString -AsPlainText -Force", rel="payload/Windows/Setup/Scripts/x.ps1")
        self.assertFinding(b, "deny.literal-credential")

    def test_evasion_colon_parameter_syntax(self):
        b = self.copy_good()
        self.append(b, 'New-LocalUser -Name "Admin" -Password:"Hunter2-colon-syntax"', rel="payload/Windows/Setup/Scripts/x.ps1")
        self.assertFinding(b, "deny.literal-credential")

    def test_evasion_preceding_switch_set_itemproperty(self):
        b = self.copy_good()
        self.append(b, 'Set-ItemProperty -Force "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon" -Name DefaultPassword -Value "Hunter2-switch-preceding"', rel="payload/Windows/Setup/Scripts/x.ps1")
        self.assertFinding(b, "deny.literal-credential")

    def test_evasion_fully_positional_set_itemproperty(self):
        b = self.copy_good()
        self.append(b, 'Set-ItemProperty "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon" DefaultPassword "Hunter2-positional-all"', rel="payload/Windows/Setup/Scripts/x.ps1")
        self.assertFinding(b, "deny.literal-credential")

    def test_evasion_preceding_switch_convertto_securestring(self):
        b = self.copy_good()
        self.append(b, '$s = ConvertTo-SecureString -ErrorAction "Stop" "Hunter2-preceding-switch" -AsPlainText -Force', rel="payload/Windows/Setup/Scripts/x.ps1")
        self.assertFinding(b, "deny.literal-credential")

    def test_evasion_unquoted_convertto_securestring(self):
        b = self.copy_good()
        self.append(b, '$s = ConvertTo-SecureString Hunter2Unquoted -AsPlainText -Force', rel="payload/Windows/Setup/Scripts/x.ps1")
        self.assertFinding(b, "deny.literal-credential")

    def test_evasion_unquoted_password_parameter(self):
        b = self.copy_good()
        self.append(b, 'New-LocalUser -Name "Admin" -Password Hunter2UnquotedParam', rel="payload/Windows/Setup/Scripts/x.ps1")
        self.assertFinding(b, "deny.literal-credential")

    def test_evasion_multiline_continuation(self):
        b = self.copy_good()
        self.append(b, '"Hunter2-multiline" `\n| ConvertTo-SecureString -AsPlainText -Force', rel="payload/Windows/Setup/Scripts/x.ps1")
        self.assertFinding(b, "deny.literal-credential")

    def test_evasion_parenthesized_expression(self):
        b = self.copy_good()
        self.append(b, '("Hunter2-parenthesized") | ConvertTo-SecureString -AsPlainText -Force', rel="payload/Windows/Setup/Scripts/x.ps1")
        self.assertFinding(b, "deny.literal-credential")

    def test_evasion_dollar_in_password_string(self):
        b = self.copy_good()
        self.append(b, "Set-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon' -Name DefaultPassword -Value 'Hunter2$With$Dollar'", rel="payload/Windows/Setup/Scripts/x.ps1")
        self.assertFinding(b, "deny.literal-credential")

    def test_evasion_double_quoted_dollar_password(self):
        b = self.copy_good()
        self.append(b, 'New-LocalUser -Name "Admin" -Password "P@ssw0rd$123"', rel="payload/Windows/Setup/Scripts/x.ps1")
        self.assertFinding(b, "deny.literal-credential")

    def test_evasion_set_itempropertyvalue(self):
        b = self.copy_good()
        self.append(b, 'Set-ItemPropertyValue -Path "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon" -Name DefaultPassword -Value "Secret123"', rel="payload/Windows/Setup/Scripts/x.ps1")
        self.assertFinding(b, "deny.literal-credential")

    def test_evasion_alias_sp(self):
        b = self.copy_good()
        self.append(b, 'sp "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon" DefaultPassword "Hunter2-alias-sp"', rel="payload/Windows/Setup/Scripts/x.ps1")
        self.assertFinding(b, "deny.literal-credential")

    def test_evasion_equals_syntax(self):
        b = self.copy_good()
        self.append(b, 'New-LocalUser -Name "Admin" -Password="Hunter2-equals"', rel="payload/Windows/Setup/Scripts/x.ps1")
        self.assertFinding(b, "deny.literal-credential")

    def test_empty_password(self):
        b = self.copy_good()
        with open(os.path.join(b, "autounattend.xml"), "w") as fh:
            fh.write(FX.answer_file(self.doc, "rem", desktop_pw=""))
        self.assertFinding(b, "users.empty-credential")

    def test_autologon_other_account(self):
        b = self.copy_good()
        with open(os.path.join(b, "autounattend.xml"), "w") as fh:
            fh.write(FX.answer_file(self.doc, "rem", autologon_user="user"))
        self.assertFinding(b, "users.autologon")

    def test_component_removed_or_changed(self):
        b = self.copy_good()
        os.unlink(os.path.join(b, "payload", self.item("components.mios-wallpaperd")["path"]))
        self.assertFinding(b, "missing:components.mios-wallpaperd")
        b2 = self.copy_good()
        with open(os.path.join(b2, "payload", self.item("components.Set-MiOSWallpaper")["path"]), "ab") as fh:
            fh.write(b"tampered\n")
        self.assertFinding(b2, "off-spec:components.Set-MiOSWallpaper")

    def test_virtio_driver_removed(self):
        b = self.copy_good()
        shutil.rmtree(os.path.join(b, "drivers", "viostor"))
        self.assertFinding(b, "missing:drivers.virtio_win.viostor")

    def test_guest_tool_not_staged(self):
        b = self.copy_good()
        os.unlink(os.path.join(b, "payload", "Windows", "Setup", "Scripts", "qemu-ga-x86_64.msi"))
        self.assertFinding(b, "missing:guest_tools.qemu_ga")

    def test_stable_virtio_url(self):
        b = self.copy_good()
        self.append(b, "curl -LO https://fedorapeople.org/groups/virt/virtio-win/direct-downloads/stable-virtio/virtio-win.iso")  # pin-gate:allow (planted)
        self.assertFinding(b, "deny.stable-virtio")

    def test_script_pnputil_nvidia_inf_rejected(self):
        # Negative control 1: pnputil /add-driver with nvidia .inf in SetupComplete.cmd is rejected
        b = self.copy_good()
        self.append(b, "pnputil.exe /add-driver C:\\drivers\\nv_dispi.inf /install")
        self.assertFinding(b, "deny.pnputil-nvidia-inf")

        # Negative control 2: pnputil -a with nvidia .inf
        b2 = self.copy_good()
        self.append(b2, "pnputil -a nvlddmkm.inf")
        self.assertFinding(b2, "deny.pnputil-nvidia-inf")

        # Positive control: non-nvidia driver .inf (e.g. virtio) is permitted
        b3 = self.copy_good()
        self.append(b3, "pnputil.exe /add-driver C:\\drivers\\viostor.inf /install")
        _, f = self.rendered(b3)
        self.assertNotIn("deny.pnputil-nvidia-inf", _ids(f))


    def test_start_family_mismatch(self):
        b = self.copy_good()
        FX.write_json(os.path.join(b, "build-manifest.json"), {"windows": {"build": "26100", "branch": "fixture_unknown_release"}})
        self.assertFinding(b, "start.family-mismatch")

    def test_feature_enabled_against_spec(self):
        b = self.copy_good()
        self.append(b, "dism /online /enable-feature /featurename:Microsoft-Hyper-V-All /all")
        self.assertFinding(b, "off-spec:features.Microsoft-Hyper-V-All")

    def test_rid500_not_disabled(self):
        b = self.copy_good()
        p = os.path.join(b, "payload", "Windows", "Setup", "Scripts", "SetupComplete.cmd")
        with open(p) as fh:
            txt = fh.read().replace("net user Administrator /active:no", "rem")
        with open(p, "w") as fh:
            fh.write(txt)
        self.assertFinding(b, "missing:users.rid500")

    def test_no_answer_file(self):
        b = self.copy_good()
        os.unlink(os.path.join(b, "autounattend.xml"))
        self.assertFinding(b, "answer.missing")

    def test_refuses_to_walk_root(self):
        with self.assertRaises(SystemExit):
            C.collect_rendered(["/"])


class TestSpecNegative(Base):
    def _doc(self, mutate):
        d = copy.deepcopy(self.doc)
        mutate(d)
        return d

    def test_user_layer_left_taskbar_is_off_spec(self):
        def m(d):
            next(r for r in d["registry"] if r["owner"] == "preferences.taskbar_align")["data"] = 0
        items = S.items_from_spec(self._doc(m), self.root)
        self.assertIn("spec-deny.TaskbarAl=0", _ids(S.spec_deny_findings(items)))

    def test_gpu_driver_planted_in_image_spec(self):
        def m(d):
            d["spec"]["drivers"]["virtio_win"]["inf"].append("nvlddmkm")
        items = S.items_from_spec(self._doc(m), self.root)
        self.assertIn("spec.gpu-driver", _ids(S.spec_deny_findings(items)))

    def test_known_default_credential_in_spec(self):
        def m(d):
            d["users"]["desktop"]["password"] = "mios"
        items = S.items_from_spec(self._doc(m), self.root)
        self.assertIn("warn.weak-credential", _ids(S.spec_credential_warnings(items)))
        self.assertIn("deny.known-default-password", _ids(S.spec_deny_findings(items)))

    def test_empty_spec_credential_is_a_hard_error(self):
        def m(d):
            d["users"]["desktop"]["password"] = ""
        p = FX.write_json(os.path.join(self.tmp, "empty-pw.json"), self._doc(m))
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            rc = C.main(["--root", self.root, "rendered", "--spec", p, "--input", self.good])
        self.assertEqual(2, rc)
        self.assertIn("empty credential", err.getvalue())

    def test_unrendered_spec_rejected(self):
        p = FX.write_json(os.path.join(self.tmp, "raw.json"), {"windows": {"xbox": {"preferences": {"taskbar_align": 1}}}})
        with contextlib.redirect_stderr(io.StringIO()), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(2, C.main(["rendered", "--spec", p, "--input", self.good]))


class TestLegacyAnswerFile(Base):
    LEGACY_LINES = r"""
& cmd.exe /d /c 'cmd /c reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced" /v TaskbarAl /t REG_DWORD /d 0 /f'
& cmd.exe /d /c 'cmd /c reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Search" /v SearchboxTaskbarMode /t REG_DWORD /d 1 /f'
& cmd.exe /d /c 'cmd /c reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Start" /v Start_ShowDownloads /t REG_DWORD /d 1 /f'
& cmd.exe /d /c 'cmd /c reg add "HKCU\Software\Microsoft\Windows\CurrentVersion\Start" /v Start_ShowDocuments /t REG_DWORD /d 1 /f'
& cmd.exe /d /c 'cmd /c reg add "HKU\MiOSDefault\Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced" /v TaskbarAl /t REG_DWORD /d 0 /f'
& cmd.exe /d /c 'schtasks /create /tn "MiOS\Hydrate" /ru user /rp user /tr C:\mios\h.cmd /sc onlogon /f'
""".strip().splitlines()

    def test_legacy_shaped_answer_file_is_non_conforming(self):
        b = os.path.join(self.tmp, "legacy")
        os.makedirs(b)
        blob = base64.b64encode("\r\n".join(self.LEGACY_LINES).encode("utf-16-le")).decode()
        xml = FX.answer_file(self.doc, "rem", desktop_pw="user", autologon_user="user").replace(
            '<Extensions xmlns="urn:mios:unattend">', f'<Extensions xmlns="urn:mios:unattend"><FirstLogonScript>{blob}</FirstLogonScript>')
        with open(os.path.join(b, "autounattend.xml"), "w") as fh:
            fh.write(xml)
        _, f = self.rendered(b)
        ids = _ids(f)
        for fid in ("deny.TaskbarAl=0", "deny.SearchboxTaskbarMode=1", "deny.Start_ShowDownloads=1",
                    "deny.Start_ShowDocuments=1", "deny.literal-credential", "users.autologon",
                    "off-spec:reg.preferences.taskbar_align", "missing:components.miosd",
                    "missing:drivers.virtio_win.viostor", "missing:users.rid500"):
            self.assertIn(fid, ids)

    @unittest.skipUnless(os.environ.get("MIOS_XBOX_LEGACY_ANSWERFILE"), "set MIOS_XBOX_LEGACY_ANSWERFILE to a readable legacy autounattend.xml")
    def test_real_legacy_answer_file(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = C.main(["--root", self.root, "rendered", "--spec", self.spec_path, "--input", os.environ["MIOS_XBOX_LEGACY_ANSWERFILE"]])
        self.assertEqual(1, rc)
        for fid in ("deny.TaskbarAl=0", "deny.SearchboxTaskbarMode=1", "deny.Start_ShowDownloads=1", "deny.literal-credential"):
            self.assertIn(f"FINDING {fid}:", out.getvalue())


def _image_tools():
    if not (shutil.which("wimlib-imagex") and shutil.which("hivexregedit") and shutil.which("perl")):
        return False
    return subprocess.run(["perl", "-MWin::Hivex", "-e", "1"], capture_output=True).returncode == 0


@unittest.skipUnless(_image_tools(), "wimlib-imagex / hivexregedit / perl Win::Hivex not installed")
class TestImage(Base):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.answer = os.path.join(cls.good, "autounattend.xml")
        cls.good_wim = FX.build_image(os.path.join(cls.tmp, "img-good"), cls.good, cls.items, BRANCH)

    def image(self, wim, items=None):
        wd = tempfile.mkdtemp(dir=self.tmp)
        obs = C.collect_image(wim, 1, items or self.items, wd, self.answer)
        return C.evaluate(items or self.items, obs, "image", self.policy)

    def build(self, name, **kw):
        items = kw.pop("items", self.items)
        return FX.build_image(os.path.join(self.tmp, name), self.good, items, kw.pop("branch", BRANCH), **kw)

    def test_conforming_image_is_green(self):
        results, f = self.image(self.good_wim)
        self.assertEqual([], [str(x) for x in f])
        passed = {r["id"] for r in results if r["status"] == "pass"}
        for iid in ("reg.preferences.taskbar_align", "reg.xbox.hags", "drivers.virtio_win.netkvm",
                    "components.mios-agent-relay", "gpu.script", "start.layout_family", "users.desktop"):
            self.assertIn(iid, passed)

    def test_cli_image_mode(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = C.main(["--root", self.root, "image", "--spec", self.spec_path, "--wim", self.good_wim, "--index", "1", "--answer", self.answer])
        self.assertEqual(0, rc, out.getvalue()[-1500:])

    def test_index_chosen_by_spec_edition(self):
        wim = os.path.join(self.tmp, "edition.wim")
        shutil.copyfile(self.good_wim, wim)
        with self.assertRaises(C.ToolError):          # no image carries the spec edition yet
            C.pick_index(wim, self.doc["spec"]["source"]["edition"])
        subprocess.run(["wimlib-imagex", "info", wim, "1", "--image-property", "WINDOWS/EDITIONID=Professional"],
                       check=True, capture_output=True, timeout=120)
        self.assertEqual(1, C.pick_index(wim, self.doc["spec"]["source"]["edition"]))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(0, C.main(["--root", self.root, "image", "--spec", self.spec_path, "--wim", wim, "--answer", self.answer]))

    def test_left_taskbar_in_default_hive(self):
        items = copy.deepcopy(self.items)
        next(i for i in items if i["id"] == "reg.preferences.taskbar_align")["value"] = 0
        wim = self.build("img-left", items=items)
        _, f = self.image(wim)
        self.assertIn("off-spec:reg.preferences.taskbar_align", _ids(f))
        self.assertIn("deny.TaskbarAl=0", _ids(f))

    def test_nvidia_inf_in_driver_store(self):
        wim = self.build("img-nv", extra_infs={"nvlddmkm": FX.virtio_inf("nvlddmkm", cls="Display", provider="NVIDIA")})
        _, f = self.image(wim)
        self.assertIn("drivers.gpu-vendor", _ids(f))

    def test_component_and_driver_missing(self):
        wim = self.build("img-miss", drop=[self.item("components.miosd")["path"],
                                            "Windows/System32/DriverStore/FileRepository/" + next(
                                                d for d in os.listdir(os.path.join(self.tmp, "img-good", "tree", "Windows", "System32", "DriverStore", "FileRepository"))
                                                if d.startswith("viostor.inf_"))])
        _, f = self.image(wim)
        self.assertIn("missing:components.miosd", _ids(f))
        self.assertIn("missing:drivers.virtio_win.viostor", _ids(f))

    def test_family_mismatch(self):
        wim = self.build("img-fam", branch="fixture_unknown_release")
        _, f = self.image(wim)
        self.assertIn("start.family-mismatch", _ids(f))

    def test_feature_selected_against_spec(self):
        items = copy.deepcopy(self.items)
        next(i for i in items if i["id"] == "features.Microsoft-Hyper-V-All")["state"] = "enabled"
        wim = self.build("img-hv", items=items)
        _, f = self.image(wim)
        self.assertIn("off-spec:features.Microsoft-Hyper-V-All", _ids(f))


_LIVE = []


def _live_doc():
    if not _LIVE:
        try:
            _LIVE.append(FX.live_render())
        except Exception as e:  # noqa: BLE001 -- reported by the test, not at import
            _LIVE.append(e)
    return _LIVE[0]


@unittest.skipUnless(os.path.isfile(os.path.join(FX.XBOX_DIR, "render_spec.py")) and os.path.isfile(os.path.join(FX.STAGED_DIR, "xbox-spec.toml")),
                     "render_spec.py or the staged [windows.xbox] fragment is not present yet")
class TestLiveSsot(unittest.TestCase):
    """Live vendor mios.toml + staged fragments (load_merged) rendered by render_spec.py."""

    def test_live_render_gates_two_sided(self):
        doc = _live_doc()
        if isinstance(doc, Exception):
            raise doc
        self.assertIsNotNone(doc, "live SSOT has no [windows.xbox]")
        with tempfile.TemporaryDirectory() as t:
            root = FX.fixture_root(t, doc)
            items = S.items_from_spec(doc, root)
            pol = S.spec_policy(doc)
            fams = sorted(next(i for i in items if i["kind"] == "build_family")["families"])
            good = FX.render_bundle(os.path.join(t, "g"), doc, items, root, fams[0])
            _, f = C.evaluate(items, C.collect_rendered([good]), "rendered", pol)
            self.assertEqual([], [str(x) for x in f])   # includes the spec deny-list (no legacy value in the SSOT)
            bad = copy.deepcopy(items)
            next(i for i in bad if i["id"] == "reg.preferences.taskbar_align")["value"] = 0
            b2 = FX.render_bundle(os.path.join(t, "b"), doc, bad, root, fams[0])
            _, f2 = C.evaluate(items, C.collect_rendered([b2]), "rendered", pol)
            self.assertIn("deny.TaskbarAl=0", _ids(f2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
