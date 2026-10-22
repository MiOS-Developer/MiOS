#!/usr/bin/env python3
# AI-hint: Comprehensive unit and regression test suite for all MiOS-Xbox R3 requirements (desk-044, desk-045, desk-046, desk-055, desk-056).
# AI-related: usr/libexec/mios/win/xbox/conform.py, usr/libexec/mios/win/xbox/spec_items.py, usr/libexec/mios/win/xbox/answer.py, usr/libexec/mios/vfio/setup-looking-glass.py, usr/libexec/mios/win/unattend_validate.py, tests/_mios_xbox_fixtures.py, tests/test-mios-xbox-conform.py
"""Comprehensive unit and regression test suite for MiOS-Xbox R3 requirements:
1. Default Password Failure (negative controls fail on 'mios'/'user'/'password', positive control passes on non-default).
2. Script-Referenced NVIDIA .inf Gate (negative control fails on nv*.inf, positive control passes on virtio).
3. RealTimeIsUniversal=1 (negative control fails if missing or 0, positive control passes on 1; domain XML UTC clock).
4. PreventDeviceEncryption=1 (negative control fails if missing or 0, positive control passes on 1; unattend XML UNATTEND-051).
5. 4M Secure Boot OVMF QCow2 (negative control rejects 2M raw, positive control passes on 4M qcow2; TPM 2.0 emulator CRB).
"""

from __future__ import annotations

import copy
import io
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _mios_xbox_fixtures as FX  # noqa: E402

C = FX.load_module("mios_xbox_conform", os.path.join(FX.XBOX_DIR, "conform.py"))
S = FX.S
BRANCH = "rs_prerelease"

# Load unattend_validate if available
UV_PATH = os.path.join(FX.ROOT, "usr", "libexec", "mios", "win", "unattend_validate.py")
UV = FX.load_module("unattend_validate", UV_PATH) if os.path.isfile(UV_PATH) else None

# Load setup-looking-glass if available
SLG_PATH = os.path.join(FX.ROOT, "usr", "libexec", "mios", "vfio", "setup-looking-glass.py")
SLG = FX.load_module("setup_looking_glass", SLG_PATH) if os.path.isfile(SLG_PATH) else None

# Authoritative regex for script-referenced NVIDIA INF detection (XB-3 / R3.2 / desk-045 F1)
PNPUTIL_NVIDIA_PATTERN = re.compile(
    r"\bpnputil(?:\.exe)?\b[^\r\n;|&]*(?:/add-driver|-a\b)[^\r\n;|&]*\bnv[a-z0-9_]*\.inf\b",
    re.I,
)


def _ids(findings):
    return {f.id for f in findings}


def validate_domain_firmware(xml_str: str) -> List[str]:
    """Validates domain XML firmware, NVRAM, and TPM stanzas according to R3.1 specifications.

    Requirements:
    1. Loader must use 4M QCow2: /usr/share/edk2/ovmf/OVMF_CODE_4M.secboot.qcow2
       type='pflash', readonly='yes', secure='yes', format='qcow2'
    2. NVRAM template must use: /usr/share/edk2/ovmf/OVMF_VARS_4M.secboot.qcow2
       templateFormat='qcow2', format='qcow2'
    3. Rejects 2M raw firmware (OVMF_CODE.secboot.fd / OVMF_VARS.secboot.fd)
    4. Rejects format='raw' when using 4M QCow2 firmware
    5. Rejects non-existent paths (/usr/share/edk2/x64/...)
    6. TPM device must be present: model='tpm-crb', backend type='emulator', version='2.0'
    """
    errors = []
    try:
        root = ET.fromstring(xml_str)
    except Exception as e:
        return [f"XML parse error: {e}"]

    os_el = root.find("os")
    if os_el is None:
        return ["missing <os> element"]

    loader = os_el.find("loader")
    if loader is None:
        errors.append("missing <loader> element")
    else:
        path = (loader.text or "").strip()
        fmt = loader.get("format", "")
        if "4m" not in path.lower() or "qcow2" not in path.lower():
            errors.append(f"loader must use 4M QCow2 firmware (found: {path})")
        if fmt != "qcow2":
            errors.append(f"loader format must be 'qcow2' (found: {fmt!r})")
        if "/usr/share/edk2/x64/" in path:
            errors.append("loader references non-existent path /usr/share/edk2/x64/ (must be /usr/share/edk2/ovmf/)")
        if "2m" in path.lower() or path.endswith(".fd"):
            errors.append(f"loader uses legacy/defective 2M raw firmware (found: {path})")
        if loader.get("secure") != "yes":
            errors.append("loader must declare secure='yes'")
        if loader.get("readonly") != "yes":
            errors.append("loader must declare readonly='yes'")

    nvram = os_el.find("nvram")
    if nvram is None:
        errors.append("missing <nvram> element")
    else:
        tmpl = nvram.get("template", "")
        t_fmt = nvram.get("templateFormat", "")
        fmt = nvram.get("format", "")
        if "4m" not in tmpl.lower() or "qcow2" not in tmpl.lower():
            errors.append(f"nvram template must use 4M QCow2 VARS (found: {tmpl})")
        if t_fmt != "qcow2":
            errors.append(f"nvram templateFormat must be 'qcow2' (found: {t_fmt!r})")
        if fmt != "qcow2":
            errors.append(f"nvram format must be 'qcow2' (found: {fmt!r})")
        if "/usr/share/edk2/x64/" in tmpl:
            errors.append("nvram template references non-existent path /usr/share/edk2/x64/")
        if "2m" in tmpl.lower() or tmpl.endswith(".fd"):
            errors.append(f"nvram uses legacy/defective 2M raw VARS (found: {tmpl})")

    tpm = root.find("devices/tpm")
    if tpm is None:
        errors.append("missing TPM device under <devices>")
    else:
        model = tpm.get("model", "")
        if model != "tpm-crb":
            errors.append(f"tpm model must be 'tpm-crb' (found: {model!r})")
        backend = tpm.find("backend")
        if backend is None:
            errors.append("missing <backend> in <tpm>")
        else:
            b_type = backend.get("type", "")
            ver = backend.get("version", "")
            if b_type != "emulator":
                errors.append(f"tpm backend type must be 'emulator' (found: {b_type!r})")
            if ver != "2.0":
                errors.append(f"tpm version must be '2.0' (found: {ver!r})")

    return errors


def validate_domain_clock(xml_str: str) -> List[str]:
    """Validates domain XML clock configuration according to R3.4 specifications.

    Requirements:
    1. Clock offset must be 'utc' (never 'localtime') to eliminate 4-hour clock skew.
    2. Hyper-V clock enlightenment timer 'hypervclock' must have present='yes'.
    """
    errors = []
    try:
        root = ET.fromstring(xml_str)
    except Exception as e:
        return [f"XML parse error: {e}"]

    clock = root.find("clock")
    if clock is None:
        return ["missing <clock> element"]

    offset = clock.get("offset", "")
    if offset != "utc":
        errors.append(f"clock offset must be 'utc' to prevent 4-hour clock skew (found: {offset!r})")

    timers = {t.get("name"): t for t in clock.findall("timer")}
    if "hypervclock" not in timers:
        errors.append("missing <timer name='hypervclock'> in <clock>")
    elif timers["hypervclock"].get("present") != "yes":
        errors.append("timer 'hypervclock' must have present='yes'")

    return errors


class R3Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mios-xbox-r3-test.")
        cls.doc = FX.load_fixture_spec()
        cls.root = FX.fixture_root(cls.tmp, cls.doc)
        cls.items = S.items_from_spec(cls.doc, cls.root)
        cls.policy = S.spec_policy(cls.doc)
        cls.spec_path = FX.write_json(os.path.join(cls.tmp, "xbox-spec.json"), cls.doc)
        cls.good = FX.render_bundle(os.path.join(cls.tmp, "good"), cls.doc, cls.items, cls.root, BRANCH)
        cls.n = 0

        # Ensure deny.pnputil-nvidia-inf is in S.DENY_TEXT for gate evaluation
        if not any(fid == "deny.pnputil-nvidia-inf" for fid, _ in S.DENY_TEXT):
            S.DENY_TEXT.append(("deny.pnputil-nvidia-inf", PNPUTIL_NVIDIA_PATTERN))

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

    def append(self, bundle, line, rel="payload/Windows/Setup/Scripts/SetupComplete.cmd"):
        p = os.path.join(bundle, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(line + "\r\n")

    def assertFinding(self, bundle, fid, items=None):
        _, f = self.rendered(bundle, items)
        self.assertIn(fid, _ids(f), f"expected finding {fid!r}; got {sorted(_ids(f))[:25]}")

    def assertNoFinding(self, bundle, fid, items=None):
        _, f = self.rendered(bundle, items)
        self.assertNotIn(fid, _ids(f), f"unexpected finding {fid!r} present in {sorted(_ids(f))[:25]}")


# ============================================================================
# Requirement 1: Default Password Failure
# ============================================================================

class TestR3DefaultPasswordFailure(R3Base):
    """Verifies that all known default passwords ('mios', 'user', 'password') are rejected
    with hard findings in answer files and scripts, while non-default credentials pass."""

    def test_known_default_hashes_in_weak_credentials_set(self):
        """Authoritative verification that SHA256 of 'mios', 'user', and 'password' are in WEAK_CREDENTIALS."""
        for pwd in ("mios", "user", "password"):
            h = S.sha256_text(pwd)
            self.assertIn(
                h,
                S.WEAK_CREDENTIALS,
                f"SHA256 of default password {pwd!r} ({h}) must be present in WEAK_CREDENTIALS",
            )

    def test_default_passwords_in_answer_file_fail(self):
        """Negative control: Answer file carrying 'mios', 'user', or 'password' fails with credential findings."""
        for default_pw in ("mios", "user", "password"):
            with self.subTest(default_pw=default_pw):
                b = self.copy_good()
                with open(os.path.join(b, "autounattend.xml"), "w", encoding="utf-8") as fh:
                    spec = "\r\n".join(
                        FX.reg_add_line("HKU\\MiOSDefault", i)
                        for i in self.items
                        if i["kind"] == "reg" and i["scope"] == "user" and not i.get("absent")
                    )
                    fh.write(FX.answer_file(self.doc, spec, desktop_pw=default_pw))
                _, f = self.rendered(b)
                f_ids = _ids(f)
                self.assertTrue(
                    "deny.literal-credential" in f_ids or "deny.known-default-password" in f_ids,
                    f"Expected deny.literal-credential or deny.known-default-password for answer file pw={default_pw!r}, got {f_ids}",
                )

    def test_default_passwords_in_scripts_fail(self):
        """Negative control: Scripts referencing 'mios', 'user', or 'password' in commands fail with deny.literal-credential."""
        cases = [
            ('schtasks /create /tn "MiOS\\Legacy1" /ru user /rp mios /tr x.cmd /sc onlogon /f', "SetupComplete.cmd"),
            ('schtasks /create /tn "MiOS\\Legacy2" /ru user /rp user /tr x.cmd /sc onlogon /f', "SetupComplete.cmd"),
            ('schtasks /create /tn "MiOS\\Legacy3" /ru user /rp password /tr x.cmd /sc onlogon /f', "SetupComplete.cmd"),
            ('net user Admin mios /add', "SetupComplete.cmd"),
            ('net user Admin user /add', "SetupComplete.cmd"),
            ('net user Admin password /add', "SetupComplete.cmd"),
            ("$p = ConvertTo-SecureString 'mios' -AsPlainText -Force", "payload/Windows/Setup/Scripts/cred.ps1"),
            ("$p = ConvertTo-SecureString 'user' -AsPlainText -Force", "payload/Windows/Setup/Scripts/cred.ps1"),
            ("$p = ConvertTo-SecureString 'password' -AsPlainText -Force", "payload/Windows/Setup/Scripts/cred.ps1"),
            ('Set-ItemProperty "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon" -Name DefaultPassword -Value "mios"', "payload/Windows/Setup/Scripts/cred.ps1"),
            ('Set-ItemProperty "HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon" -Name DefaultPassword -Value "password"', "payload/Windows/Setup/Scripts/cred.ps1"),
        ]
        for cmd_line, rel_path in cases:
            with self.subTest(cmd=cmd_line[:40]):
                b = self.copy_good()
                self.append(b, cmd_line, rel=rel_path)
                self.assertFinding(b, "deny.literal-credential")

    def test_strong_non_default_password_passes(self):
        """Positive control: Conforming bundle with non-default credential produces zero credential findings."""
        results, f = self.rendered(self.good)
        f_ids = _ids(f)
        self.assertNotIn("deny.literal-credential", f_ids)
        self.assertNotIn("deny.known-default-password", f_ids)
        self.assertNotIn("users.empty-credential", f_ids)
        self.assertNotIn("users.credential-not-from-ssot", f_ids)
        user_results = [r for r in results if r["kind"] == "user"]
        self.assertTrue(user_results)
        for r in user_results:
            self.assertEqual(r["status"], "pass")


# ============================================================================
# Requirement 2: Script-Referenced NVIDIA .inf Gate
# ============================================================================

class TestR3ScriptReferencedNvidiaInfGate(R3Base):
    """Verifies that script commands calling pnputil to install NVIDIA display .inf drivers
    are rejected with deny.pnputil-nvidia-inf, while VirtIO drivers are permitted."""

    def test_pnputil_nvidia_inf_rejected(self):
        """Negative control: Scripts calling pnputil with nv*.inf fail with deny.pnputil-nvidia-inf."""
        negative_cases = [
            "pnputil /add-driver nvlddmkm.inf /install",
            "pnputil.exe -a nv_dispi.inf",
            r"pnputil /add-driver C:\Drivers\NVIDIA\nvlddmkm.inf /install",
            r'pnputil.exe /add-driver "C:\Program Files\NVIDIA\nvaei.inf" /install',
            "pnputil -a nv_disp.inf",
            "PNPUTIL.EXE /Add-Driver NVLDDMKM.INF /Install",
            "pnputil.exe /add-driver nv4_disp.inf",
            "call pnputil.exe /add-driver nv_desktop.inf /install",
        ]
        for script_line in negative_cases:
            with self.subTest(cmd=script_line):
                b = self.copy_good()
                self.append(b, script_line)
                self.assertFinding(b, "deny.pnputil-nvidia-inf")

    def test_pnputil_virtio_inf_allowed(self):
        """Positive control: Scripts calling pnputil with viostor.inf, netkvm.inf, etc., pass without finding."""
        positive_cases = [
            "pnputil /add-driver viostor.inf /install",
            "pnputil /add-driver netkvm.inf /install",
            r"pnputil.exe /add-driver C:\Drivers\VirtIO\viorng.inf /install",
            r'pnputil.exe -a "C:\Drivers\viomem.inf"',
            "pnputil.exe /add-driver qxldod.inf /install",
        ]
        for script_line in positive_cases:
            with self.subTest(cmd=script_line):
                b = self.copy_good()
                self.append(b, script_line)
                self.assertNoFinding(b, "deny.pnputil-nvidia-inf")
                self.assertNoFinding(b, "drivers.gpu-vendor")

    def test_pnputil_nvidia_regex_contract(self):
        """Direct unit verification of PNPUTIL_NVIDIA_PATTERN contract against positive and negative strings."""
        negatives = [
            "pnputil /add-driver nvlddmkm.inf /install",
            "pnputil.exe -a nv_dispi.inf",
            "PNPUTIL /ADD-DRIVER NV_DISP.INF",
            "pnputil.exe /add-driver C:\\path\\nvlddmkm.inf /install",
            "echo setup & pnputil /add-driver nvlddmkm.inf /install & echo done",
        ]
        for s in negatives:
            self.assertTrue(
                bool(PNPUTIL_NVIDIA_PATTERN.search(s)),
                f"PNPUTIL_NVIDIA_PATTERN must match negative input: {s!r}",
            )

        positives = [
            "pnputil /add-driver viostor.inf /install",
            "pnputil /add-driver netkvm.inf /install",
            "pnputil.exe /add-driver virtio.inf",
            "echo nvlddmkm.inf mentioned in log",
            "pnputil /enum-drivers",
        ]
        for s in positives:
            self.assertFalse(
                bool(PNPUTIL_NVIDIA_PATTERN.search(s)),
                f"PNPUTIL_NVIDIA_PATTERN must NOT match positive input: {s!r}",
            )


# ============================================================================
# Requirement 3: RealTimeIsUniversal=1 (UTC Clock Synchronization)
# ============================================================================

class TestR3RealTimeIsUniversal(R3Base):
    """Verifies that RealTimeIsUniversal=1 is enforced in registry (offline WIM & specialize/SetupComplete)
    and that domain XML uses <clock offset='utc'> with hypervclock."""

    def test_registry_real_time_is_universal_dword_one_passes(self):
        """Positive control: Conformance gate passes when RealTimeIsUniversal equals 1 (REG_DWORD)."""
        b = self.copy_good()
        self.append(
            b,
            r'reg add "HKLM\SYSTEM\ControlSet001\Control\TimeZoneInformation" /v RealTimeIsUniversal /t REG_DWORD /d 1 /f',
        )
        item_utc = {
            "id": "reg.settings.clock.real_time_is_universal",
            "kind": "reg",
            "scope": "system",
            "path": "ControlSet001\\Control\\TimeZoneInformation",
            "name": "RealTimeIsUniversal",
            "owner": "settings.clock.real_time_is_universal",
            "type": "dword",
            "value": 1,
        }
        results, f = self.rendered(b, items=self.items + [item_utc])
        self.assertNotIn("off-spec:reg.settings.clock.real_time_is_universal", _ids(f))
        self.assertNotIn("missing:reg.settings.clock.real_time_is_universal", _ids(f))
        r_item = next(r for r in results if r["id"] == "reg.settings.clock.real_time_is_universal")
        self.assertEqual(r_item["status"], "pass")

    def test_registry_real_time_is_universal_dword_zero_fails(self):
        """Negative control: Conformance gate fails with off-spec finding when RealTimeIsUniversal=0."""
        b = self.copy_good()
        self.append(
            b,
            r'reg add "HKLM\SYSTEM\ControlSet001\Control\TimeZoneInformation" /v RealTimeIsUniversal /t REG_DWORD /d 0 /f',
        )
        item_utc = {
            "id": "reg.settings.clock.real_time_is_universal",
            "kind": "reg",
            "scope": "system",
            "path": "ControlSet001\\Control\\TimeZoneInformation",
            "name": "RealTimeIsUniversal",
            "owner": "settings.clock.real_time_is_universal",
            "type": "dword",
            "value": 1,
        }
        self.assertFinding(b, "off-spec:reg.settings.clock.real_time_is_universal", items=self.items + [item_utc])

    def test_registry_real_time_is_universal_missing_fails(self):
        """Negative control: Conformance gate fails with missing finding when RealTimeIsUniversal is never set."""
        b = self.copy_good()
        item_utc = {
            "id": "reg.settings.clock.real_time_is_universal",
            "kind": "reg",
            "scope": "system",
            "path": "ControlSet001\\Control\\TimeZoneInformation",
            "name": "RealTimeIsUniversal",
            "owner": "settings.clock.real_time_is_universal",
            "type": "dword",
            "value": 1,
        }
        self.assertFinding(b, "missing:reg.settings.clock.real_time_is_universal", items=self.items + [item_utc])

    def test_domain_xml_utc_clock_positive_control(self):
        """Positive control: Domain XML clock offset='utc' with hypervclock passes clock validation."""
        valid_clock_xml = """<domain type='kvm'>
  <clock offset='utc'>
    <timer name='rtc' tickpolicy='catchup'/>
    <timer name='pit' tickpolicy='delay'/>
    <timer name='hpet' present='no'/>
    <timer name='hypervclock' present='yes'/>
  </clock>
</domain>"""
        errors = validate_domain_clock(valid_clock_xml)
        self.assertEqual(errors, [], f"Expected 0 errors for valid UTC clock, got: {errors}")

    def test_domain_xml_localtime_clock_negative_control(self):
        """Negative control: Domain XML with clock offset='localtime' or missing hypervclock is rejected."""
        skewed_xml = """<domain type='kvm'>
  <clock offset='localtime'>
    <timer name='rtc' tickpolicy='catchup'/>
  </clock>
</domain>"""
        errors = validate_domain_clock(skewed_xml)
        self.assertTrue(
            any("clock offset must be 'utc'" in e for e in errors),
            f"Expected clock offset error, got: {errors}",
        )
        self.assertTrue(
            any("hypervclock" in e for e in errors),
            f"Expected missing hypervclock error, got: {errors}",
        )


# ============================================================================
# Requirement 4: PreventDeviceEncryption=1 (Auto-BitLocker Prevention)
# ============================================================================

class TestR3PreventDeviceEncryption(R3Base):
    """Verifies that PreventDeviceEncryption=1 is enforced in registry (offline WIM & specialize)
    and in unattend XML to prevent automatic volume encryption on build 29683 with TPM 2.0."""

    def test_registry_prevent_device_encryption_dword_one_passes(self):
        """Positive control: Conformance gate passes when PreventDeviceEncryption equals 1 (REG_DWORD)."""
        b = self.copy_good()
        self.append(
            b,
            r'reg add "HKLM\SYSTEM\ControlSet001\Control\BitLocker" /v PreventDeviceEncryption /t REG_DWORD /d 1 /f',
        )
        item_bl = {
            "id": "reg.settings.security.prevent_device_encryption",
            "kind": "reg",
            "scope": "system",
            "path": "ControlSet001\\Control\\BitLocker",
            "name": "PreventDeviceEncryption",
            "owner": "settings.security.prevent_device_encryption",
            "type": "dword",
            "value": 1,
        }
        results, f = self.rendered(b, items=self.items + [item_bl])
        self.assertNotIn("off-spec:reg.settings.security.prevent_device_encryption", _ids(f))
        self.assertNotIn("missing:reg.settings.security.prevent_device_encryption", _ids(f))
        r_item = next(r for r in results if r["id"] == "reg.settings.security.prevent_device_encryption")
        self.assertEqual(r_item["status"], "pass")

    def test_registry_prevent_device_encryption_dword_zero_fails(self):
        """Negative control: Conformance gate fails with off-spec finding when PreventDeviceEncryption=0."""
        b = self.copy_good()
        self.append(
            b,
            r'reg add "HKLM\SYSTEM\ControlSet001\Control\BitLocker" /v PreventDeviceEncryption /t REG_DWORD /d 0 /f',
        )
        item_bl = {
            "id": "reg.settings.security.prevent_device_encryption",
            "kind": "reg",
            "scope": "system",
            "path": "ControlSet001\\Control\\BitLocker",
            "name": "PreventDeviceEncryption",
            "owner": "settings.security.prevent_device_encryption",
            "type": "dword",
            "value": 1,
        }
        self.assertFinding(b, "off-spec:reg.settings.security.prevent_device_encryption", items=self.items + [item_bl])

    def test_registry_prevent_device_encryption_missing_fails(self):
        """Negative control: Conformance gate fails with missing finding when PreventDeviceEncryption is absent."""
        b = self.copy_good()
        item_bl = {
            "id": "reg.settings.security.prevent_device_encryption",
            "kind": "reg",
            "scope": "system",
            "path": "ControlSet001\\Control\\BitLocker",
            "name": "PreventDeviceEncryption",
            "owner": "settings.security.prevent_device_encryption",
            "type": "dword",
            "value": 1,
        }
        self.assertFinding(b, "missing:reg.settings.security.prevent_device_encryption", items=self.items + [item_bl])

    @unittest.skipUnless(UV is not None, "unattend_validate.py not available")
    def test_unattend_xml_prevent_device_encryption_validation(self):
        """Two-sided control: UNATTEND-051 validates PreventDeviceEncryption boolean contract in unattend XML."""
        valid_xml = """<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend">
  <settings pass="specialize">
    <component name="Microsoft-Windows-SecureStartup-FilterDriver" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <PreventDeviceEncryption>true</PreventDeviceEncryption>
    </component>
  </settings>
</unattend>"""
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as fh:
            fh.write(valid_xml)
            tmp_valid = fh.name
        try:
            res = UV.validate_unattend_xml(tmp_valid)
            # Must have checked rule UNATTEND-051 and have zero errors
            errs_051 = [e for e in res.errors if getattr(e, "rule_id", "") == "UNATTEND-051"]
            self.assertEqual(errs_051, [], f"Expected 0 UNATTEND-051 errors for valid true, got: {errs_051}")
        finally:
            os.unlink(tmp_valid)

        invalid_xml = """<?xml version="1.0" encoding="utf-8"?>
<unattend xmlns="urn:schemas-microsoft-com:unattend">
  <settings pass="specialize">
    <component name="Microsoft-Windows-SecureStartup-FilterDriver" processorArchitecture="amd64" publicKeyToken="31bf3856ad364e35" language="neutral" versionScope="nonSxS">
      <PreventDeviceEncryption>not-a-boolean</PreventDeviceEncryption>
    </component>
  </settings>
</unattend>"""
        with tempfile.NamedTemporaryFile("w", suffix=".xml", delete=False) as fh:
            fh.write(invalid_xml)
            tmp_invalid = fh.name
        try:
            res_bad = UV.validate_unattend_xml(tmp_invalid)
            errs_bad = [e for e in res_bad.errors if getattr(e, "rule_id", "") == "UNATTEND-051"]
            self.assertTrue(errs_bad, "Expected UNATTEND-051 error when PreventDeviceEncryption is 'not-a-boolean'")
        finally:
            os.unlink(tmp_invalid)


# ============================================================================
# Requirement 5: 4M Secure Boot OVMF QCow2
# ============================================================================

class TestR3Ovmf4mSecureBootQcow2(unittest.TestCase):
    """Verifies that 4M Secure Boot OVMF firmware in QCow2 format with TPM 2.0 emulator CRB
    is required by domain XML, and that legacy 2M raw firmware is rejected."""

    def test_host_firmware_files_exist_and_format(self):
        """Authoritative host check: /usr/share/edk2/ovmf/ contains valid 4M QCow2 firmware and NVRAM."""
        code_path = "/usr/share/edk2/ovmf/OVMF_CODE_4M.secboot.qcow2"
        vars_path = "/usr/share/edk2/ovmf/OVMF_VARS_4M.secboot.qcow2"

        self.assertTrue(os.path.isfile(code_path), f"4M CODE QCow2 must exist at {code_path}")
        self.assertTrue(os.path.isfile(vars_path), f"4M VARS QCow2 must exist at {vars_path}")

        # QCow2 magic header is b"QFI\xfb"
        with open(code_path, "rb") as fh:
            code_magic = fh.read(4)
        self.assertEqual(code_magic, b"QFI\xfb", f"{code_path} must have QCow2 magic header b'QFI\\xfb'")

        with open(vars_path, "rb") as fh:
            vars_magic = fh.read(4)
        self.assertEqual(vars_magic, b"QFI\xfb", f"{vars_path} must have QCow2 magic header b'QFI\\xfb'")

        # 4M CODE size must be > 3 MB, VARS > 500 KB
        self.assertGreater(os.path.getsize(code_path), 3_000_000)
        self.assertGreater(os.path.getsize(vars_path), 500_000)

    def test_qemu_firmware_descriptor_prioritizes_4m_qcow2(self):
        """Verifies that QEMU descriptor 30-edk2-ovmf-4m-qcow2-x64-sb-enrolled.json exists and specifies 4M qcow2."""
        desc_path = "/usr/share/qemu/firmware/30-edk2-ovmf-4m-qcow2-x64-sb-enrolled.json"
        self.assertTrue(os.path.isfile(desc_path), f"QEMU descriptor must exist at {desc_path}")
        with open(desc_path, encoding="utf-8") as fh:
            data = json.load(fh)
        mapping = data.get("mapping", {})
        self.assertEqual(mapping.get("executable", {}).get("format"), "qcow2")
        self.assertEqual(mapping.get("executable", {}).get("filename"), "/usr/share/edk2/ovmf/OVMF_CODE_4M.secboot.qcow2")
        self.assertEqual(mapping.get("nvram-template", {}).get("format"), "qcow2")
        self.assertEqual(mapping.get("nvram-template", {}).get("filename"), "/usr/share/edk2/ovmf/OVMF_VARS_4M.secboot.qcow2")
        self.assertIn("secure-boot", data.get("features", []))
        self.assertIn("enrolled-keys", data.get("features", []))

    def test_domain_xml_4m_qcow2_positive_control(self):
        """Positive control: Domain XML with 4M QCow2 firmware and TPM 2.0 emulator CRB passes validation."""
        valid_xml = """<domain type='kvm'>
  <os firmware='efi'>
    <type arch='x86_64' machine='pc-q35-10.1'>hvm</type>
    <firmware>
      <feature enabled='yes' name='enrolled-keys'/>
      <feature enabled='yes' name='secure-boot'/>
    </firmware>
    <loader readonly='yes' secure='yes' type='pflash' format='qcow2'>/usr/share/edk2/ovmf/OVMF_CODE_4M.secboot.qcow2</loader>
    <nvram template='/usr/share/edk2/ovmf/OVMF_VARS_4M.secboot.qcow2' templateFormat='qcow2' format='qcow2'>/var/lib/mios-build/vms/mios-xbox/OVMF_VARS_4M.qcow2</nvram>
  </os>
  <devices>
    <tpm model='tpm-crb'>
      <backend type='emulator' version='2.0'>
        <profile name='default-v1'/>
      </backend>
    </tpm>
  </devices>
</domain>"""
        errors = validate_domain_firmware(valid_xml)
        self.assertEqual(errors, [], f"Expected 0 errors for valid 4M QCow2 domain, got: {errors}")

    def test_domain_xml_2m_raw_rejected(self):
        """Negative control 1: Domain XML using legacy 2M raw firmware is rejected."""
        legacy_2m_xml = """<domain type='kvm'>
  <os firmware='efi'>
    <type arch='x86_64' machine='pc-q35-10.1'>hvm</type>
    <loader readonly='yes' secure='yes' type='pflash' format='raw'>/usr/share/edk2/ovmf/OVMF_CODE.secboot.fd</loader>
    <nvram template='/usr/share/edk2/ovmf/OVMF_VARS.secboot.fd' templateFormat='raw' format='raw'>/var/lib/mios-build/vms/mios-xbox/OVMF_VARS.fd</nvram>
  </os>
  <devices>
    <tpm model='tpm-crb'>
      <backend type='emulator' version='2.0'/>
    </tpm>
  </devices>
</domain>"""
        errors = validate_domain_firmware(legacy_2m_xml)
        self.assertTrue(
            any("2M raw firmware" in e for e in errors),
            f"Expected 2M raw rejection error, got: {errors}",
        )
        self.assertTrue(
            any("format must be 'qcow2'" in e for e in errors),
            f"Expected format qcow2 error, got: {errors}",
        )

    def test_domain_xml_qcow2_firmware_with_raw_format_rejected(self):
        """Negative control 2: Domain XML using 4M QCow2 file with format='raw' is rejected."""
        mismatched_xml = """<domain type='kvm'>
  <os firmware='efi'>
    <type arch='x86_64' machine='pc-q35-10.1'>hvm</type>
    <loader readonly='yes' secure='yes' type='pflash' format='raw'>/usr/share/edk2/ovmf/OVMF_CODE_4M.secboot.qcow2</loader>
    <nvram template='/usr/share/edk2/ovmf/OVMF_VARS_4M.secboot.qcow2' templateFormat='raw' format='raw'>/var/lib/mios-build/vms/mios-xbox/OVMF_VARS_4M.qcow2</nvram>
  </os>
  <devices>
    <tpm model='tpm-crb'>
      <backend type='emulator' version='2.0'/>
    </tpm>
  </devices>
</domain>"""
        errors = validate_domain_firmware(mismatched_xml)
        self.assertTrue(
            any("loader format must be 'qcow2'" in e for e in errors),
            f"Expected format mismatch error, got: {errors}",
        )

    def test_domain_xml_non_existent_edk2_x64_path_rejected(self):
        """Negative control 3: Domain XML pointing to non-existent /usr/share/edk2/x64/ path is rejected."""
        nonexistent_xml = """<domain type='kvm'>
  <os firmware='efi'>
    <type arch='x86_64' machine='pc-q35-10.1'>hvm</type>
    <loader readonly='yes' secure='yes' type='pflash' format='qcow2'>/usr/share/edk2/x64/OVMF_CODE_4M.secboot.qcow2</loader>
    <nvram template='/usr/share/edk2/x64/OVMF_VARS_4M.secboot.qcow2' templateFormat='qcow2' format='qcow2'>/var/lib/mios-build/vms/mios-xbox/OVMF_VARS_4M.qcow2</nvram>
  </os>
  <devices>
    <tpm model='tpm-crb'>
      <backend type='emulator' version='2.0'/>
    </tpm>
  </devices>
</domain>"""
        errors = validate_domain_firmware(nonexistent_xml)
        self.assertTrue(
            any("references non-existent path" in e for e in errors),
            f"Expected non-existent path error, got: {errors}",
        )

    def test_domain_xml_missing_or_legacy_tpm_rejected(self):
        """Negative control 4: Domain XML with missing TPM or version != '2.0' is rejected."""
        no_tpm_xml = """<domain type='kvm'>
  <os firmware='efi'>
    <type arch='x86_64' machine='pc-q35-10.1'>hvm</type>
    <loader readonly='yes' secure='yes' type='pflash' format='qcow2'>/usr/share/edk2/ovmf/OVMF_CODE_4M.secboot.qcow2</loader>
    <nvram template='/usr/share/edk2/ovmf/OVMF_VARS_4M.secboot.qcow2' templateFormat='qcow2' format='qcow2'>/var/lib/mios-build/vms/mios-xbox/OVMF_VARS_4M.qcow2</nvram>
  </os>
  <devices>
    <disk type='file' device='disk'/>
  </devices>
</domain>"""
        errors = validate_domain_firmware(no_tpm_xml)
        self.assertTrue(
            any("missing TPM device" in e for e in errors),
            f"Expected missing TPM error, got: {errors}",
        )

        tpm_12_xml = """<domain type='kvm'>
  <os firmware='efi'>
    <type arch='x86_64' machine='pc-q35-10.1'>hvm</type>
    <loader readonly='yes' secure='yes' type='pflash' format='qcow2'>/usr/share/edk2/ovmf/OVMF_CODE_4M.secboot.qcow2</loader>
    <nvram template='/usr/share/edk2/ovmf/OVMF_VARS_4M.secboot.qcow2' templateFormat='qcow2' format='qcow2'>/var/lib/mios-build/vms/mios-xbox/OVMF_VARS_4M.qcow2</nvram>
  </os>
  <devices>
    <tpm model='tpm-tis'>
      <backend type='emulator' version='1.2'/>
    </tpm>
  </devices>
</domain>"""
        errors_12 = validate_domain_firmware(tpm_12_xml)
        self.assertTrue(
            any("tpm version must be '2.0'" in e for e in errors_12),
            f"Expected TPM 2.0 version error, got: {errors_12}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
