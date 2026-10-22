#!/usr/bin/env python3
# AI-hint: Two-sided tests for the wired MiOS-Xbox build pipeline (build.py + answer.py + fetch.py + worker.py + render_spec users + probe defaults): the payload rendered from the real vendor SSOT (fixture credentials) passes the conformance gate while planted legacy values / service-account autologon / tampered inputs fail with named findings; users follow the operator decision (separate desktop admin, persistent autologon, never the service account or RID 500); the worker XML never carries a hostdev and only attaches the overlay + exchange disk; fetch verifies git-tree / size / converter config; the build stages run in order and always clean the worker up; probe.py defaults to the real rendered SSOT, never the fixture, and fails closed (exit 2) when unreachable. No network, no VM, no real credential.
# AI-related: usr/libexec/mios/win/xbox/build.py, usr/libexec/mios/win/xbox/answer.py, usr/libexec/mios/win/xbox/fetch.py, usr/libexec/mios/win/xbox/worker.py, usr/libexec/mios/win/xbox/render_spec.py, usr/libexec/mios/win/xbox/probe.py, usr/libexec/mios/win/xbox/conform.py
"""Run: python3 -I tests/test-mios-xbox-build.py -v   (memory-light; no network; temp dirs only)"""

from __future__ import annotations

import base64
import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import socket
import stat
import sys
import tarfile
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
XBOX = os.path.join(ROOT, "usr", "libexec", "mios", "win", "xbox")
sys.path.insert(0, XBOX)
sys.path.insert(0, HERE)
import render_spec as R  # noqa: E402
import answer as A  # noqa: E402
import conform as C  # noqa: E402
import fetch as F  # noqa: E402
import worker as W  # noqa: E402
import build as B  # noqa: E402
import resolve_latest as RL  # noqa: E402
import spec_items as S  # noqa: E402
import probe as P  # noqa: E402

VENDOR = os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
FIXTURE_IDENTITY = os.path.join(HERE, "fixtures", "mios-xbox", "fixture-identity.toml")
LAYERS = [VENDOR, FIXTURE_IDENTITY]
mios_toml = R.mios_toml
UNS = "{urn:schemas-microsoft-com:unattend}"


def _rd(p: str) -> str:
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def _rdb(p: str) -> bytes:
    with open(p, "rb") as fh:
        return fh.read()


def _wr(p: str, t: str) -> None:
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(t)


def _wrb(p: str, b: bytes, mode: str = "wb") -> None:
    with open(p, mode) as fh:
        fh.write(b)


def _json(p: str):
    with open(p, encoding="utf-8") as fh:
        return json.load(fh)


def _ids(findings):
    return {f.id for f in findings}


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _no_network(*a, **k):
    raise AssertionError("network access in an offline test")


class Fixture:
    """Shared, built once: merged SSOT (vendor + fixture identity), rendered spec, fake verified inputs."""
    merged = None
    doc = None

    @classmethod
    def get(cls):
        if cls.merged is None:
            cls.merged = mios_toml.load_merged(LAYERS)
            cls.doc = R.render(copy.deepcopy(cls.merged), LAYERS)
        return copy.deepcopy(cls.merged), copy.deepcopy(cls.doc)


def fake_inputs(tmp: str, doc) -> dict:
    nat = os.path.join(tmp, "native")
    os.makedirs(nat, exist_ok=True)
    rows = []
    for c in doc["spec"]["components"]["ship"]:
        if c["kind"] == "native":
            n = c["source"].split(":", 1)[1] + ".exe"
            b = f"fixture native {n}".encode()
            with open(os.path.join(nat, n), "wb") as fh:
                fh.write(b)
            rows.append({"name": n, "sha256": _sha(b), "bytes": len(b)})
    with open(os.path.join(nat, "manifest.json"), "w") as fh:
        json.dump(rows, fh)
    dl = os.path.join(tmp, "dl")
    os.makedirs(dl, exist_ok=True)
    inputs = {"installers": {}}
    for role, fn in (("virtio_guest_tools", "virtio-win-guest-tools.exe"), ("qemu_ga", "qemu-ga-x86_64.msi"),
                     ("winfsp", "winfsp-9.9.99999.msi"), ("webview2", "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"),
                     ("looking_glass_host", "looking-glass-host.zip"), ("looking_glass_idd", "looking-glass-idd.zip")):
        p = os.path.join(dl, fn)
        b = f"fixture {role}".encode()
        with open(p, "wb") as fh:
            fh.write(b)
        inputs["installers"][role] = {"path": p, "sha256": _sha(b), "signer": "Fixture Signer", "version": "fixture",
                                      "url": f"https://example.invalid/{fn}"}
    drv = os.path.join(tmp, "drivers")
    vw = doc["spec"]["drivers"]["virtio_win"]
    for inf in vw["inf"]:
        d = os.path.join(drv, inf, vw["os_dir"], vw["arch"])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, inf + ".inf"), "w") as fh:
            fh.write('[Version]\nClass=System\nProvider=%RH%\n[Strings]\nRH="Red Hat, Inc."\n')
    inputs["drivers_dir"] = drv
    return inputs


# ---------------------------------------------------------------------------
# users per operator decision (render_spec cross-check with mios_credentials)
# ---------------------------------------------------------------------------

class TestUsers(unittest.TestCase):
    def test_positive_desktop_admin_persistent_autologon(self):
        _, doc = Fixture.get()
        u = doc["users"]
        self.assertEqual(u["desktop"]["name"], "mios-fixture")       # identity.username of the layer
        self.assertEqual(u["desktop"]["logon_count"], 9999999)      # mios_credentials.PERSISTENT_LOGON_COUNT
        self.assertTrue(u["desktop"]["autologon"] and u["desktop"]["autologon_persistent"])
        self.assertIn(u["desktop"]["groups"][0], ("Users", "Administrators"))
        if "admin" in u:
            self.assertEqual(u["admin"]["groups"][0], "Administrators")
        self.assertNotEqual(u["desktop"]["name"].lower(), u["service_account"]["name"].lower())
        self.assertEqual(u["builtin_administrator"]["disable_at"], "finalize")

    def test_guest_projection_has_no_credential(self):
        _, doc = Fixture.get()
        g = R.guest_projection(doc)
        text = json.dumps(g)
        self.assertNotIn(doc["users"]["desktop"]["password"], text)
        self.assertEqual(g["projection"], "guest")
        self.assertEqual(g["users"]["desktop"]["name"], doc["users"]["desktop"]["name"])

    def _render(self, mutate):
        merged, _ = Fixture.get()
        mutate(merged)
        return R.render(merged, LAYERS)

    def test_negative_desktop_is_service_account(self):
        def m(d):
            d["windows"]["xbox"]["users"]["desktop"]["name_ref"] = "autounattend.service.svc_user"
        with self.assertRaises(R.SpecError) as cm:
            self._render(m)
        self.assertIn("service account", str(cm.exception))

    def test_negative_autologon_user_disagrees(self):
        def m(d):
            d["autounattend"]["autologon_user"] = d["autounattend"]["service"]["svc_user"]
        with self.assertRaises(R.SpecError) as cm:
            self._render(m)
        self.assertIn("identity/autounattend", str(cm.exception))   # the ONE resolver refuses service autologon

    def test_negative_rid500_name(self):
        def m(d):
            d["identity"]["username"] = "Administrator"
        with self.assertRaises(R.SpecError) as cm:
            self._render(m)
        self.assertIn("RID 500", str(cm.exception))

    def test_negative_empty_password(self):
        def m(d):
            d["identity"]["default_password"] = ""
        with self.assertRaises(R.SpecError):
            self._render(m)

    def test_negative_persistent_mismatch(self):
        def m(d):
            d["windows"]["xbox"]["users"]["desktop"]["autologon_persistent"] = False
        with self.assertRaises(R.SpecError) as cm:
            self._render(m)
        self.assertIn("autologon_persistent", str(cm.exception))

    def test_negative_worker_gpu(self):
        def m(d):
            d["windows"]["xbox"]["vm"]["worker"]["gpu_passthrough"] = True
        with self.assertRaises(R.SpecError):
            self._render(m)

    def test_error_never_carries_the_password(self):
        merged, doc = Fixture.get()
        merged["windows"]["xbox"]["users"]["desktop"]["name_ref"] = "autounattend.service.svc_user"
        try:
            R.render(merged, LAYERS)
        except R.SpecError as e:
            self.assertNotIn(doc["users"]["desktop"]["password"], str(e))


# ---------------------------------------------------------------------------
# payload (answer file + SetupComplete chain + registry + components) vs the gate
# ---------------------------------------------------------------------------

class TestPayload(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mios-xbox-build-test.")
        _, cls.doc = Fixture.get()
        cls.inputs = fake_inputs(cls.tmp, cls.doc)
        cls.native = os.path.join(cls.tmp, "native")
        cls.pay = os.path.join(cls.tmp, "payload")
        cls.summary = A.render_payload(cls.doc, cls.inputs, cls.pay, ROOT, cls.native)
        cls.spec = os.path.join(cls.tmp, "xbox-spec.json")
        with open(cls.spec, "w") as fh:
            json.dump(cls.doc, fh)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def gate(self, *extra):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = C.run_rendered(self.spec, [self.pay, self.inputs["drivers_dir"], *extra], None, ROOT)
        return rc, out.getvalue()

    def copy_payload(self):
        d = tempfile.mkdtemp(prefix="pay.", dir=self.tmp)
        shutil.rmtree(d)
        shutil.copytree(self.pay, d)
        return d

    def gate_on(self, pay):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = C.run_rendered(self.spec, [pay, self.inputs["drivers_dir"]], None, ROOT)
        return rc, out.getvalue()

    def test_positive_rendered_inputs_conform(self):
        rc, out = self.gate()
        self.assertEqual(rc, 0, out)
        self.assertIn("CONFORMING", out)

    def test_answer_file_users_from_ssot(self):
        root = ET.parse(os.path.join(self.pay, "autounattend.xml")).getroot()
        al = root.find(f".//{UNS}AutoLogon")
        self.assertEqual(al.findtext(f"{UNS}Username"), self.doc["users"]["desktop"]["name"])
        self.assertEqual(al.findtext(f"{UNS}LogonCount"), "9999999")
        self.assertEqual(al.findtext(f"{UNS}Password/{UNS}Value"), self.doc["users"]["desktop"]["password"])
        las = {la.findtext(f"{UNS}Name"): la.findtext(f"{UNS}Group") for la in root.findall(f".//{UNS}LocalAccount")}
        self.assertEqual(las[self.doc["users"]["desktop"]["name"]], self.doc["users"]["desktop"]["groups"][0])
        if "admin" in self.doc["users"]:
            self.assertEqual(las[self.doc["users"]["admin"]["name"]], "Administrators")
        self.assertEqual(root.find(f".//{UNS}AdministratorPassword/{UNS}Value").text,
                         self.doc["users"]["builtin_administrator"]["password"])
        self.assertEqual(root.findtext(f".//{UNS}ComputerName"), self.doc["users"]["computer_name"])
        self.assertEqual(root.findtext(f".//{UNS}TimeZone"), self.doc["region"]["timezone"])
        self.assertEqual(root.findtext(f".//{UNS}StartupToGamingHome"), "true")
        self.assertEqual(oct(os.stat(os.path.join(self.pay, "autounattend.xml")).st_mode & 0o777), "0o600")

    def test_no_credential_outside_the_answer_file(self):
        pw = {self.doc["users"]["desktop"]["password"], self.doc["users"]["builtin_administrator"]["password"]}
        for d, _, files in os.walk(self.pay):
            for f in files:
                if f == "autounattend.xml":
                    continue
                raw = _rdb(os.path.join(d, f))
                for text in (raw.decode("utf-8", "replace"), raw.decode("utf-16-le", "replace")):
                    for p in pw:
                        self.assertNotIn(p, text, f"credential literal in {f}")

    def test_registry_reg_covers_the_spec_and_is_centered(self):
        txt = _rdb(os.path.join(self.pay, "ProgramData", "MiOS", "xbox-registry.reg")).decode("utf-16")
        self.assertTrue(txt.startswith("Windows Registry Editor Version 5.00"))
        self.assertIn('"TaskbarAl"=dword:00000001', txt)
        self.assertIn('"SearchboxTaskbarMode"=dword:00000000', txt)
        self.assertIn("[HKEY_LOCAL_MACHINE\\MIOS_DEFU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced]", txt)
        self.assertNotIn('"TaskbarAl"=dword:00000000', txt)

    def test_setup_script_bakes_guest_tools_lg_gpu_winrm(self):
        s = _rd(os.path.join(self.pay, "Windows", "Setup", "Scripts", A.SETUP_SCRIPT))
        for needle in ("virtio-win-guest-tools.exe", "qemu-ga-x86_64.msi", "winfsp-9.9.99999.msi", "VirtioFsSvc",
                       "MicrosoftEdgeWebView2RuntimeInstallerX64.exe", "looking-glass-host-setup.exe", "mios-looking-glass-idd.ps1",
                       "mios-gpu-driver.ps1", "-Register", "Enable-PSRemoting", "MiOS-WinRM-HTTP", "-RemoteAddress 'LocalSubnet'",
                       "Assert-MiOSSigned", "Assert-MiOSSha256", "S-1-5-32-555", "Add-WindowsCapability"):
            self.assertIn(needle, s)
        self.assertNotIn("AllowUnencrypted -Value $true", s)
        cmd = _rd(os.path.join(self.pay, "Windows", "Setup", "Scripts", "SetupComplete.cmd"))
        self.assertIn(A.SETUP_SCRIPT, cmd)
        fl = _rd(os.path.join(self.pay, "Windows", "Setup", "Scripts", A.FIRSTLOGON_SCRIPT))
        self.assertIn("Disable-LocalUser", fl)
        self.assertIn("AutoLogonCount", fl)
        self.assertIn("MiOS-StoreHydrate", fl)

    def test_components_and_gpu_step_staged_with_hashes(self):
        names = {c["name"] for c in self.summary["components"]}
        for c in self.doc["spec"]["components"]["ship"]:
            self.assertIn(c["name"], names)
        self.assertIn("mios-gpu-driver", names)
        gpu = next(c for c in self.summary["components"] if c["name"] == "mios-gpu-driver")
        self.assertEqual(gpu["task"], self.doc["spec"]["gpu"]["task_name"])

    # -- negatives ----------------------------------------------------------------------------
    def test_negative_planted_left_taskbar(self):
        d = self.copy_payload()
        with open(os.path.join(d, "Windows", "Setup", "Scripts", "legacy.cmd"), "w") as fh:
            fh.write('reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Explorer\\Advanced" /v TaskbarAl /t REG_DWORD /d 0 /f\n')
        rc, out = self.gate_on(d)
        self.assertEqual(rc, 1)
        self.assertIn("deny.TaskbarAl=0", out)

    def test_negative_planted_search_and_downloads(self):
        d = self.copy_payload()
        with open(os.path.join(d, "Windows", "Setup", "Scripts", "legacy.cmd"), "w") as fh:
            fh.write('reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Search" /v SearchboxTaskbarMode /t REG_DWORD /d 1 /f\n'
                     'reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Start" /v Start_ShowDownloads /t REG_DWORD /d 1 /f\n')
        rc, out = self.gate_on(d)
        self.assertEqual(rc, 1)
        self.assertIn("deny.SearchboxTaskbarMode=1", out)
        self.assertIn("deny.Start_ShowDownloads=1", out)

    def test_negative_autologon_service_account(self):
        d = self.copy_payload()
        p = os.path.join(d, "autounattend.xml")
        t = _rd(p)
        t = t.replace(f"<Username>{self.doc['users']['desktop']['name']}</Username>",
                      f"<Username>{self.doc['users']['service_account']['name']}</Username>")
        _wr(p, t)
        rc, out = self.gate_on(d)
        self.assertEqual(rc, 1)
        self.assertIn("users.autologon-service-account", out)

    def test_negative_finite_logon_count(self):
        d = self.copy_payload()
        p = os.path.join(d, "autounattend.xml")
        t = _rd(p)
        _wr(p, t.replace("<LogonCount>9999999</LogonCount>", "<LogonCount>1</LogonCount>"))
        rc, out = self.gate_on(d)
        self.assertIn("users.autologon-persistent", out)

    def test_negative_gpu_step_removed(self):
        d = self.copy_payload()
        os.unlink(os.path.join(d, "Program Files", "MiOS", "windows", "mios-gpu-driver.ps1"))
        rc, out = self.gate_on(d)
        self.assertEqual(rc, 1)
        self.assertIn("missing:gpu.script", out)

    def test_negative_component_removed(self):
        d = self.copy_payload()
        os.unlink(os.path.join(d, "Program Files", "MiOS", "miosd.exe"))
        rc, out = self.gate_on(d)
        self.assertIn("missing:components.miosd", out)

    def test_negative_installer_not_staged(self):
        d = self.copy_payload()
        os.unlink(os.path.join(d, "Program Files", "MiOS", "installers", "qemu-ga-x86_64.msi"))
        rc, out = self.gate_on(d)
        self.assertIn("missing:guest_tools.qemu_ga", out)

    def test_negative_legacy_answer_file_alone(self):
        d = tempfile.mkdtemp(prefix="legacy.", dir=self.tmp)
        with open(os.path.join(d, "autounattend.xml"), "w") as fh:
            fh.write('<?xml version="1.0"?><unattend xmlns="urn:schemas-microsoft-com:unattend"><settings pass="oobeSystem">'
                     '<component name="Microsoft-Windows-Shell-Setup"><AutoLogon><Enabled>true</Enabled><LogonCount>1</LogonCount>'
                     '<Username>mios-sudo</Username><Password><Value>x-not-ssot</Value><PlainText>true</PlainText></Password></AutoLogon>'
                     '<FirstLogonCommands><SynchronousCommand><CommandLine>cmd /c reg add "HKCU\\Software\\Microsoft\\Windows\\CurrentVersion'
                     '\\Explorer\\Advanced" /v TaskbarAl /t REG_DWORD /d 0 /f</CommandLine></SynchronousCommand></FirstLogonCommands>'
                     '</component></settings></unattend>')
        rc, out = self.gate_on(d)
        self.assertEqual(rc, 1)
        for fid in ("deny.TaskbarAl=0", "users.autologon", "users.autologon-service-account"):
            self.assertIn(fid, out)

    def test_negative_tampered_installer_input(self):
        inputs = copy.deepcopy(self.inputs)
        inputs["installers"]["winfsp"]["sha256"] = "0" * 64
        with self.assertRaises(A.PayloadError) as cm:
            A.render_payload(self.doc, inputs, os.path.join(self.tmp, "p-tamper"), ROOT, self.native)
        self.assertIn("winfsp", str(cm.exception))

    def test_negative_native_component_mismatch(self):
        nat = os.path.join(self.tmp, "native-bad")
        shutil.copytree(self.native, nat)
        with open(os.path.join(nat, "miosd.exe"), "ab") as fh:
            fh.write(b"tampered")
        with self.assertRaises(A.PayloadError) as cm:
            A.render_payload(self.doc, self.inputs, os.path.join(self.tmp, "p-native"), ROOT, nat)
        self.assertIn("miosd", str(cm.exception))

    def test_negative_guest_projection_cannot_render_answer(self):
        with self.assertRaises(A.PayloadError):
            A.render_payload(R.guest_projection(self.doc), self.inputs, os.path.join(self.tmp, "p-guest"), ROOT, self.native)

    def test_negative_answer_refuses_service_autologon(self):
        doc = copy.deepcopy(self.doc)
        doc["users"]["desktop"]["name"] = doc["users"]["service_account"]["name"]
        with self.assertRaises(A.PayloadError):
            A.render_answer_file(doc)


# ---------------------------------------------------------------------------
# fetch: converter tree / config, downloads, UUP set
# ---------------------------------------------------------------------------

def _blob(b: bytes) -> str:
    return hashlib.sha1(f"blob {len(b)}\0".encode() + b).hexdigest()


class TestFetch(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-xbox-fetch-test.")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _tree_manifest(self, files):
        return {"artifacts": {"uup_converter": {"integrity": {"kind": "git-tree"},
                                                "files": [{"path": p, "git_sha1": _blob(b), "size": len(b)} for p, b in files.items()]}}}

    def test_verify_tree_positive_and_negative(self):
        files = {"convert-UUP.cmd": b"@echo off\r\n", "bin/x.exe": b"MZ"}
        d = os.path.join(self.tmp, "conv")
        for p, b in files.items():
            os.makedirs(os.path.dirname(os.path.join(d, p)) or d, exist_ok=True)
            _wrb(os.path.join(d, p), b)
        man = self._tree_manifest(files)
        self.assertEqual(RL.verify_tree(man, "uup_converter", d), 2)
        _wrb(os.path.join(d, "bin", "x.exe"), b"!", 'ab')
        with self.assertRaises(RL.ResolveError):
            RL.verify_tree(man, "uup_converter", d)
        _wrb(os.path.join(d, "bin", "x.exe"), b"MZ")
        _wrb(os.path.join(d, "planted.cmd"), b"x")
        with self.assertRaises(RL.ResolveError):
            RL.verify_tree(man, "uup_converter", d)

    def test_converter_config_overrides_and_unknown_key(self):
        up = "[convert-UUP]\r\nAutoStart    =0\r\nAddUpdates   =0\r\nSkipISO      =0\r\n\r\n[Store_Apps]\r\nSkipApps     =0\r\n"
        out = F.render_converter_config(up, {"AutoStart": 1, "SkipISO": 1})
        self.assertIn("AutoStart    =1", out)
        self.assertIn("SkipISO      =1", out)
        self.assertIn("SkipApps     =0", out)
        with self.assertRaises(F.FetchError):
            F.render_converter_config(up, {"NoSuchKey": 1})
        with self.assertRaises(F.FetchError):   # a [Store_Apps] key is not a [convert-UUP] key
            F.render_converter_config(up, {"SkipApps": 1})

    def test_safe_extract_refuses_symlink_and_escape(self):
        for bad in ("link", "escape"):
            tgz = os.path.join(self.tmp, f"{bad}.tgz")
            with tarfile.open(tgz, "w:gz") as tf:
                ti = tarfile.TarInfo("top/sub/ok.txt")
                ti.size = 2
                tf.addfile(ti, io.BytesIO(b"ok"))
                if bad == "link":
                    li = tarfile.TarInfo("top/sub/evil")
                    li.type = tarfile.SYMTYPE
                    li.linkname = "/etc/passwd"
                    tf.addfile(li)
                else:
                    ei = tarfile.TarInfo("top/sub/../../../evil")
                    ei.size = 1
                    tf.addfile(ei, io.BytesIO(b"x"))
            with self.assertRaises(F.FetchError):
                F._safe_extract(tgz, os.path.join(self.tmp, bad), "sub")
        tgz = os.path.join(self.tmp, "good.tgz")
        with tarfile.open(tgz, "w:gz") as tf:
            ti = tarfile.TarInfo("top/sub/ok.txt")
            ti.size = 2
            tf.addfile(ti, io.BytesIO(b"ok"))
        out = F._safe_extract(tgz, os.path.join(self.tmp, "good"), "sub")
        self.assertEqual(_rdb(os.path.join(out, "ok.txt")), b"ok")

    def test_authenticode_size_check_without_osslsigncode(self):
        p = os.path.join(self.tmp, "a.msi")
        _wrb(p, b"12345")
        man = {"artifacts": {"qemu_ga": {"integrity": {"kind": "authenticode", "size": 5, "signer": "Red Hat"}}}}
        old = os.environ.pop("MIOS_OSSLSIGNCODE", None)
        try:
            if shutil.which("osslsigncode"):
                self.skipTest("osslsigncode present: host verifies the signature")
            self.assertIn("worker", F._verify(man, "qemu_ga", p))
            man["artifacts"]["qemu_ga"]["integrity"]["size"] = 6
            with self.assertRaises(F.FetchError):
                F._verify(man, "qemu_ga", p)
            self.assertFalse(os.path.exists(p), "an unverified download must be deleted")
        finally:
            if old is not None:
                os.environ["MIOS_OSSLSIGNCODE"] = old

    def test_uup_live_set_must_equal_resolved(self):
        _, doc = Fixture.get()
        man = {"artifacts": {"windows_uup": {"uuid": "u", "lang": "en-us", "edition": "professional", "integrity": {"kind": "sha256-per-file"},
                                             "files": [{"name": "a.esd", "sha256": "1" * 64, "sha1": "2" * 40, "size": 1}]}}}

        class H:
            def request(self, url, method="GET", follow=True):
                return RL.Response(200, {}, json.dumps({"response": {"files": {"a.esd": {"sha256": "1" * 64, "url": "https://x/a"},
                                                                               "planted.cab": {"sha256": "3" * 64, "url": "https://x/p"}}}}).encode())
        with self.assertRaises(F.FetchError) as cm:
            F.fetch_uup(man, doc, os.path.join(self.tmp, "uup"), F.Downloader(1, lambda s: None), H())
        self.assertIn("differs from the resolved", str(cm.exception))

    def test_downloader_size_cap(self):
        sock = socket.socket
        socket.socket = _no_network  # type: ignore
        try:
            dl = F.Downloader(1, lambda s: None)
            src = os.path.join(self.tmp, "src.bin")
            _wrb(src, b"abcdef")
            with self.assertRaises(F.FetchError):
                dl.get("file://" + src, os.path.join(self.tmp, "dst.bin"), 3)
            self.assertFalse(os.path.exists(os.path.join(self.tmp, "dst.bin")))
            dl.get("file://" + src, os.path.join(self.tmp, "ok.bin"), 6)
            self.assertEqual(_rdb(os.path.join(self.tmp, "ok.bin")), b"abcdef")
        finally:
            socket.socket = sock  # type: ignore

    def test_stage_virtio_drivers_mixed_case_and_renamed_dirs(self):
        orig_run = F._run
        try:
            def fake_run(cmd, **kw):
                out = next(a[2:] for a in cmd if a.startswith("-o"))
                for d, inf in (("NetKVM", "netkvm"), ("Balloon", "balloon"), ("vioserial", "vioser"), ("viostor", "viostor")):
                    p = os.path.join(out, d, "w11", "amd64")
                    os.makedirs(p, exist_ok=True)
                    _wr(os.path.join(p, inf + ".inf"), "[Version]\n")
            F._run = fake_run
            vw = {"os_dir": "w11", "arch": "amd64", "inf": ["netkvm", "balloon", "vioser", "viostor"]}
            drv = F._stage_virtio_drivers("fake.iso", vw, self.tmp)
            for inf in vw["inf"]:
                self.assertTrue(os.path.isfile(os.path.join(drv, inf, "w11", "amd64", inf + ".inf")), inf)
            with self.assertRaises(F.FetchError) as cm:
                F._stage_virtio_drivers("fake.iso", {**vw, "inf": [*vw["inf"], "missingdrv"]}, self.tmp)
            self.assertIn("missingdrv", str(cm.exception))
        finally:
            F._run = orig_run

    def test_uup_refresh_on_expired_signed_url(self):
        _, doc = Fixture.get()
        b = b"x"
        sha256 = hashlib.sha256(b).hexdigest()
        sha1 = hashlib.sha1(b).hexdigest()
        man = {"artifacts": {"windows_uup": {"uuid": "u", "lang": "en-us", "edition": "professional", "integrity": {"kind": "sha256-per-file"},
                                             "files": [{"name": "a.esd", "sha256": sha256, "sha1": sha1, "size": 1}]}}}
        urls = ["https://expired.invalid/a", "https://fresh.invalid/a"]

        class H:
            def request(self, url, method="GET", follow=True):
                u = urls.pop(0)
                return RL.Response(200, {}, json.dumps({"response": {"files": {"a.esd": {"sha256": sha256, "url": u}}}}).encode())

        class DL(F.Downloader):
            def get(self, url, dest, size=None):
                if "expired" in url:
                    raise F.FetchError("HTTP Error 403: Forbidden")
                _wrb(dest, b)

        out = F.fetch_uup(man, doc, os.path.join(self.tmp, "uup-refresh"), DL(1, lambda s: None), H())
        self.assertEqual(_rdb(os.path.join(out, "a.esd")), b)


# ---------------------------------------------------------------------------
# worker: XML, guards, credentials
# ---------------------------------------------------------------------------

BASE_XML = """<domain type='kvm'>
  <name>mios-xbox-current</name><uuid>11111111-2222-3333-4444-555555555555</uuid>
  <memory unit='KiB'>8388608</memory><currentMemory unit='KiB'>8388608</currentMemory><vcpu placement='static'>8</vcpu>
  <os><type arch='x86_64' machine='pc-q35-10.2'>hvm</type>
    <loader readonly='yes' type='pflash' format='raw'>/usr/share/edk2/ovmf/OVMF_CODE.fd</loader>
    <nvram template='/usr/share/edk2/ovmf/OVMF_VARS.fd' templateFormat='raw' format='raw'>/var/lib/mios-build/vms/mios-xbox-current/OVMF_VARS.fd</nvram>
    <boot dev='cdrom'/></os>
  <cpu mode='host-passthrough' check='none'><topology sockets='1' cores='4' threads='2'/></cpu>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type='file' device='disk'><driver name='qemu' type='qcow2'/><source file='/var/lib/mios-build/vms/mios-xbox-current/disk.qcow2'/><target dev='sda' bus='sata'/></disk>
    <disk type='file' device='cdrom'><driver name='qemu' type='raw'/><source file='/var/lib/mios-build/artifacts/mios-xbox/virtio/virtio-win.iso'/><target dev='sdc' bus='sata'/></disk>
    <interface type='network'><mac address='52:54:00:01:00:aa'/><source network='default'/><model type='rtl8139'/><address type='pci' domain='0x0000' bus='0x02' slot='0x01' function='0x0'/></interface>
    <channel type='spicevmc'><target type='virtio' name='com.redhat.spice.0'/></channel>
    <graphics type='spice' port='5930' autoport='no' listen='127.0.0.1'/>
    <video><model type='virtio' heads='1' primary='yes'/></video>
    <hostdev mode='subsystem' type='pci' managed='yes'><source><address domain='0x0000' bus='0x01' slot='0x00' function='0x0'/></source></hostdev>
    <hostdev mode='subsystem' type='pci' managed='yes'><source><address domain='0x0000' bus='0x01' slot='0x00' function='0x1'/></source></hostdev>
    <shmem name='looking-glass'><model type='ivshmem-plain'/><size unit='M'>128</size></shmem>
    <tpm model='tpm-crb'><backend type='emulator' version='2.0'/></tpm>
  </devices>
</domain>"""


def _answer(path, name="legacy-user", pw="legacy-Fixture-1", plain=True, group="Administrators"):
    v = pw if plain else base64.b64encode((pw + "Password").encode("utf-16-le")).decode()
    with open(path, "w") as fh:
        fh.write(f'<unattend xmlns="urn:schemas-microsoft-com:unattend"><settings pass="oobeSystem"><component name="Microsoft-Windows-Shell-Setup">'
                 f'<UserAccounts><LocalAccounts><LocalAccount><Name>{name}</Name><Group>{group}</Group><Password><Value>{v}</Value>'
                 f'<PlainText>{"true" if plain else "false"}</PlainText></Password></LocalAccount></LocalAccounts></UserAccounts>'
                 f'</component></settings></unattend>')


class TestWorker(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-xbox-worker-test.")
        _, self.doc = Fixture.get()
        self.cfg = W.WorkerConfig.from_spec(self.doc, self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_xml_positive(self):
        xml = W.generate_worker_xml(BASE_XML, self.cfg)
        root = ET.fromstring(xml)
        self.assertEqual(root.findtext("name"), self.doc["spec"]["vm"]["worker"]["name"])
        self.assertIsNone(root.find("uuid"))
        self.assertEqual(root.findtext("memory"), str(self.doc["spec"]["vm"]["worker"]["memory_gib"]))
        self.assertEqual(root.find("memory").get("unit"), "GiB")
        for tag in ("hostdev", "shmem", "tpm", "channel"):
            self.assertFalse(root.findall(f".//{tag}"), tag)
        disks = [d.find("source").get("file") for d in root.findall(".//devices/disk")]
        self.assertEqual(disks, [self.cfg.overlay, self.cfg.exchange])
        self.assertEqual(root.findtext("./os/nvram"), self.cfg.nvram)
        self.assertEqual(root.find(".//interface/model").get("type"), "rtl8139")   # driver the base guest has
        self.assertEqual(root.find(".//graphics").get("listen"), "127.0.0.1")
        self.assertNotIn("mios-xbox-current/disk.qcow2", xml)

    def test_xml_negative_planted_hostdev_and_foreign_disk(self):
        xml = W.generate_worker_xml(BASE_XML, self.cfg)
        root = ET.fromstring(xml)
        hd = ET.SubElement(root.find("devices"), "hostdev", {"mode": "subsystem", "type": "pci"})
        with self.assertRaises(W.WorkerError):
            W.validate_worker_xml(root, self.cfg)
        root.find("devices").remove(hd)
        d = ET.SubElement(root.find("devices"), "disk", {"type": "file", "device": "disk"})
        ET.SubElement(d, "source", {"file": "/var/lib/mios-build/vms/mios-xbox-current/disk.qcow2"})
        with self.assertRaises(W.WorkerError):
            W.validate_worker_xml(root, self.cfg)

    def _runner(self, states):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            if cmd[:3] == ["virsh", "-c", W.CONNECT] and cmd[3] == "domstate":
                st = states.get(cmd[4])
                return (0, st + "\n") if st else (1, "error: failed to get domain")
            return 0, ""
        run.calls = calls
        return run

    def test_preflight_positive(self):
        r = self._runner({self.cfg.target: "shut off", self.cfg.base: "shut off"})
        W.preflight(self.cfg, r, mem=lambda: 64.0, wd=lambda: True)

    def test_preflight_negatives(self):
        with self.assertRaises(W.WorkerError):
            W.preflight(self.cfg, self._runner({self.cfg.target: "running"}), mem=lambda: 64.0, wd=lambda: True)
        with self.assertRaises(W.WorkerError):
            W.preflight(self.cfg, self._runner({self.cfg.base: "paused"}), mem=lambda: 64.0, wd=lambda: True)
        with self.assertRaises(W.WorkerError) as cm:
            W.preflight(self.cfg, self._runner({}), mem=lambda: 3.0, wd=lambda: True)
        self.assertIn("MemAvailable", str(cm.exception))
        with self.assertRaises(W.WorkerError):
            W.preflight(self.cfg, self._runner({}), mem=lambda: 64.0, wd=lambda: False)

    def test_credentials_from_declared_answer_file(self):
        p = os.path.join(self.tmp, "a.xml")
        self.cfg.base_answer_file = p
        _answer(p, plain=True)
        self.assertEqual(W.worker_credentials(self.cfg), ("legacy-user", "legacy-Fixture-1"))
        _answer(p, plain=False)
        self.assertEqual(W.worker_credentials(self.cfg), ("legacy-user", "legacy-Fixture-1"))
        _answer(p, group="Users")
        with self.assertRaises(W.WorkerError):
            W.worker_credentials(self.cfg)
        self.cfg.credential_source = "literal"
        with self.assertRaises(W.WorkerError):
            W.worker_credentials(self.cfg)

    def test_stop_is_graceful_then_destroy(self):
        states = {self.cfg.name: "running"}
        r = self._runner(states)
        W.stop(self.cfg, r, sleep=lambda s: None, grace_s=10)
        verbs = [c[3] for c in r.calls if c[:1] == ["virsh"]]
        self.assertLess(verbs.index("shutdown"), verbs.index("destroy"))


# ---------------------------------------------------------------------------
# build orchestration
# ---------------------------------------------------------------------------

class FakeSession:
    def __init__(self, status):
        self.status = status
        self.scripts = []

    def run_ps(self, script):
        self.scripts.append(script)
        out = json.dumps(self.status) if "status.json" in script else "5"
        return type("R", (), {"status_code": 0, "std_out": out.encode(), "std_err": b""})()


class TestBuild(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-xbox-build-orch.")
        _, self.doc = Fixture.get()
        self.doc["spec"]["build"]["work_root"] = os.path.join(self.tmp, "work")
        self.b = B.Build(os.path.join(self.tmp, "build-x"), LAYERS)
        os.makedirs(self.b.root)
        os.makedirs(self.b.reports)
        self.b.doc = self.doc

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_new_build_dir_is_private_and_unique(self):
        b = B.Build.new(os.path.join(self.tmp, "art"), LAYERS)
        self.assertEqual(stat.S_IMODE(os.stat(b.root).st_mode), 0o700)
        with self.assertRaises(FileExistsError):
            os.mkdir(b.root, 0o700)

    def test_stage_render_writes_0600_spec_and_credential_free_guest(self):
        old = os.umask(0o022)
        try:
            B.stage_render(self.b)
        finally:
            os.umask(old)
        self.assertEqual(stat.S_IMODE(os.stat(self.b.spec_path).st_mode), 0o600)
        g = _json(self.b.guest_spec_path)
        self.assertNotIn("password", json.dumps(g["users"]))

    def _worker_runner(self):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            if cmd[0] == "virsh" and cmd[3] == "domstate":
                if cmd[4] == self.doc["spec"]["vm"]["worker"]["name"]:
                    return (0, "shut off\n") if any(c[:4] == ["virsh", "-c", W.CONNECT, "define"] for c in calls) else (1, "no domain")
                return 0, "shut off\n"
            if cmd[0] == "virsh" and cmd[3] == "dumpxml":
                return 0, BASE_XML
            if cmd[0] == "virsh" and cmd[3] == "domifaddr":
                return 0, " vnet0  52:54:00:01:00:aa  ipv4  192.0.2.10/24\n"
            if cmd[0] == "qemu-img":
                open(cmd[-1] if cmd[-1].endswith(".qcow2") else cmd[-2], "w").close()
            return 0, ""
        run.calls = calls
        return run

    def _run_worker(self, status):
        job = os.path.join(self.tmp, "job")
        os.makedirs(job)
        os.environ_backup = None
        r = self._worker_runner()
        nv = os.path.join(self.tmp, "VARS.fd")
        open(nv, "w").close()
        base = BASE_XML.replace("/var/lib/mios-build/vms/mios-xbox-current/OVMF_VARS.fd", nv)

        def run(cmd, **kw):
            if cmd[0] == "virsh" and cmd[3] == "dumpxml":
                r.calls.append(cmd)
                return 0, base
            return r(cmd, **kw)
        run.calls = r.calls
        sess = FakeSession(status)
        orig_conn = socket.create_connection
        socket.create_connection = lambda *a, **k: contextlib.nullcontext()   # type: ignore
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                res = B.stage_worker(self.b, job, run, session_factory=lambda c, ip: sess, sleep=lambda s: None,
                                     guards={"mem": lambda: 64.0, "wd": lambda: True})
        finally:
            socket.create_connection = orig_conn  # type: ignore
        return res, run.calls, sess, job

    def test_stage_worker_positive_sequence_and_cleanup(self):
        res, calls, sess, job = self._run_worker({"stage": "done", "status": "done"})
        self.assertEqual(res["status"], "done")
        seq = [" ".join(c[:4]) for c in calls]
        joined = "\n".join(seq)
        self.assertLess(joined.index("guestfish --rw"), joined.index(f"virsh -c {W.CONNECT} define"))
        self.assertLess(joined.index(f"virsh -c {W.CONNECT} define"), joined.index(f"virsh -c {W.CONNECT} start"))
        self.assertIn("guestfish --ro", joined)                          # read-only copy-out
        self.assertIn(f"virsh -c {W.CONNECT} undefine", joined)          # cleanup
        self.assertTrue(any("Register-ScheduledTask" in s and "S-1-5-18" in s for s in sess.scripts))
        self.assertFalse(os.path.exists(job))

    def test_stage_worker_negative_failed_job_still_cleans_up(self):
        with self.assertRaises(W.WorkerError) as cm:
            self._run_worker({"stage": "convert", "status": "failed", "error": "converter failed"})
        self.assertIn("converter failed", str(cm.exception))

    def test_stage_family_negative_uncaptured(self):
        inputs_dir = os.path.join(self.b.work)
        os.makedirs(os.path.join(inputs_dir, "uup"))
        open(os.path.join(inputs_dir, "uup", "professional_en-us.esd"), "w").close()
        _wr(os.path.join(inputs_dir, "inputs.json"), json.dumps({"uup_dir": os.path.join(inputs_dir, "uup")}))
        _wr(self.b.manifest_path, json.dumps({"artifacts": {"windows_uup": {"edition": "professional", "lang": "en-us"}}}))
        tree = os.path.join(self.tmp, "tree")   # an uncaptured tree (independent of the real tree's captures)
        os.makedirs(tree)
        orig = (C.pick_index, C.wim_extract, C.hive_keys)
        C.pick_index = lambda esd, ed: 3
        C.wim_extract = lambda *a, **k: None
        try:
            for fam, expect in (("rs_prerelease", "not captured"), ("ge_unknown", "has no [windows.xbox.start.layout_assets")):
                C.hive_keys = lambda h, p, k, fam=fam: (f'Windows Registry Editor Version 5.00\n\n[HKEY_LOCAL_MACHINE\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion]\n'
                                                        f'"BuildBranch"="{fam}"\n')
                self.assertEqual(B.image_family(self.b), fam)
                with self.assertRaises(B.BuildError) as cm:
                    B.stage_family(self.b, tree=tree)
                self.assertIn(expect, str(cm.exception))
                self.assertIn("no fallback blob", str(cm.exception))
            os.rename(os.path.join(inputs_dir, "uup", "professional_en-us.esd"),
                      os.path.join(inputs_dir, "uup", "MetadataESD_professional_en-us.esd"))
            self.assertTrue(B._edition_esd(os.path.join(inputs_dir, "uup"), "professional", "en-us").endswith("MetadataESD_professional_en-us.esd"))
        finally:
            C.pick_index, C.wim_extract, C.hive_keys = orig

    def test_main_refuses_non_root_heavy_commands(self):
        if os.geteuid() == 0:
            self.skipTest("running as root")
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            rc = B.main(["stage"])
        self.assertEqual(rc, 2)
        self.assertIn("sudo", err.getvalue())

    def test_scoped_argv_uses_heavy_lock_and_memory_cap(self):
        argv = B._scoped_argv(["run"], self.doc["spec"])
        self.assertEqual(argv[:4], ["flock", "-w", "600", self.doc["spec"]["build"]["heavy_lock"]])
        self.assertIn(f"MemoryMax={self.doc['spec']['build']['host_memory_max']}", argv)
        self.assertIn("MemorySwapMax=0", argv)
        self.assertIn(f"--setenv={B.SCOPED_ENV}=1", argv)


# ---------------------------------------------------------------------------
# probe defaults (real SSOT spec, SSOT credentials, fail closed)
# ---------------------------------------------------------------------------

class TestProbeDefaults(unittest.TestCase):
    def setUp(self):
        _, self.doc = Fixture.get()
        self._default = P.default_spec
        P.default_spec = lambda env=None: copy.deepcopy(self.doc)   # the real rendered SSOT (fixture credentials only)

    def tearDown(self):
        P.default_spec = self._default

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = P.main(argv)
        return rc, out.getvalue(), err.getvalue()

    def test_default_request_is_the_real_spec_not_the_fixture(self):
        marker = copy.deepcopy(self.doc)
        marker["users"]["desktop"]["name"] = "ssot-rendered"     # distinguishes the default render from the fixture
        P.default_spec = lambda env=None: copy.deepcopy(marker)
        rc, out, _ = self.run_main(["request"])
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["desktop_user"], "ssot-rendered")
        fixture = _json(os.path.join(HERE, "fixtures", "mios-xbox", "xbox-spec.fixture.json"))
        self.assertNotEqual(fixture["users"]["desktop"]["name"], "ssot-rendered")

    def test_credentials_only_from_the_ssot_spec(self):
        self.assertEqual(P._credentials(self.doc), (self.doc["users"]["desktop"]["name"], self.doc["users"]["desktop"]["password"]))
        os.environ["MIOS_WINRM_USER"], os.environ["MIOS_WINRM_PASSWORD"] = "env-user", "env-not-ssot"
        try:
            self.assertEqual(P._credentials(self.doc)[0], self.doc["users"]["desktop"]["name"])   # env cannot override
        finally:
            del os.environ["MIOS_WINRM_USER"], os.environ["MIOS_WINRM_PASSWORD"]
        with self.assertRaises(S.SpecError):
            P._credentials(R.guest_projection(self.doc))

    def test_settings_required_from_ssot(self):
        st = P.probe_settings(self.doc)
        self.assertEqual(st["winrm_port"], self.doc["spec"]["probe"]["winrm_port"])
        bad = copy.deepcopy(self.doc)
        del bad["spec"]["probe"]["transport"]
        with self.assertRaises(S.SpecError):
            P.probe_settings(bad)

    def test_unreachable_fails_closed_exit_2(self):
        with tempfile.TemporaryDirectory() as t:
            rep = os.path.join(t, "r.json")
            rc, out, _ = self.run_main(["run", "--address", "192.0.2.1", "--report", rep])   # TEST-NET-1: nothing listens
            self.assertEqual(rc, 2)
            self.assertIn("UNREACHABLE", out)
            self.assertNotIn(" -> CONFORMING", out)
            r = _json(rep)
            self.assertFalse(r["ok"])
            self.assertFalse(any(i["status"] == "pass" for i in r["items"]))

    def test_fixture_only_with_flag(self):
        rc, out, _ = self.run_main(["request", "--fixture"])
        self.assertEqual(rc, 0)
        fx = _json(os.path.join(HERE, "fixtures", "mios-xbox", "xbox-spec.fixture.json"))
        self.assertEqual(json.loads(out)["desktop_user"], fx["users"]["desktop"]["name"])


# ---------------------------------------------------------------------------
# worker-side / rendered PowerShell (DISM needs Windows: parse + static contract here, live on the worker)
# ---------------------------------------------------------------------------

BUILD_PS = os.path.join(ROOT, "usr", "share", "mios", "windows", "build")


class TestPowerShell(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mios-xbox-ps-test.")
        _, cls.doc = Fixture.get()
        cls.inputs = fake_inputs(cls.tmp, cls.doc)
        cls.pay = os.path.join(cls.tmp, "payload")
        A.render_payload(cls.doc, cls.inputs, cls.pay, ROOT, os.path.join(cls.tmp, "native"))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_all_scripts_parse(self):
        if not shutil.which("pwsh"):
            self.skipTest("pwsh not installed")
        files = [os.path.join(BUILD_PS, f) for f in ("New-MiOSXboxImage.ps1", "MiOS-Xbox-Worker.ps1", "MiOS-Xbox.Provision.ps1",
                                                     "MiOS-Xbox-Capture.ps1")]
        files += [os.path.join(self.pay, "Windows", "Setup", "Scripts", f) for f in (A.SETUP_SCRIPT, A.FIRSTLOGON_SCRIPT)]
        ps = ("$bad = 0; foreach ($f in (ConvertFrom-Json $env:FILES)) { $e = $null; "
              "[void][System.Management.Automation.Language.Parser]::ParseFile($f, [ref]$null, [ref]$e); "
              "if ($e.Count) { $bad++; Write-Output \"$f : $($e[0].Message) @$($e[0].Extent.StartLineNumber)\" } }; exit $bad")
        import subprocess
        r = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                           timeout=300, env={**os.environ, "FILES": json.dumps(files)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_servicing_script_reads_only_the_spec(self):
        t = _rd(os.path.join(BUILD_PS, "New-MiOSXboxImage.ps1"))
        for legacy in ("<unattend", "LabConfig", "ForceUnsigned", "<TimeZone>UTC", "MIOS_AI_ENDPOINT", "net user Administrator",
                       "TaskbarAl", "SearchboxTaskbarMode", "mios-wallpaper.jpg", "MiOS Operator"):
            self.assertFalse(legacy in t, f"legacy literal {legacy!r} in New-MiOSXboxImage.ps1")
        for contract in ("projection -ne 'guest'", "refusing a credential-bearing spec", "Get-AuthenticodeSignature",
                         "deny_display_providers", "layout_assets", "xbox-registry.reg", "EditionId", "throw"):
            self.assertTrue(contract in t, f"New-MiOSXboxImage.ps1 lacks {contract!r}")

    def test_rendered_scripts_never_carry_a_secret_or_legacy_value(self):
        for f in (A.SETUP_SCRIPT, A.FIRSTLOGON_SCRIPT, "SetupComplete.cmd"):
            t = _rd(os.path.join(self.pay, "Windows", "Setup", "Scripts", f))
            self.assertNotIn(self.doc["users"]["desktop"]["password"], t)
            self.assertNotIn("TaskbarAl", t)
            self.assertNotRegex(t, r"(?i)-Password\s")


if __name__ == "__main__":
    unittest.main()
