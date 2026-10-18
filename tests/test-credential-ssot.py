#!/usr/bin/env python3
# AI-hint: Positive + negative controls for the ONE fail-closed layered-SSOT credential resolution across every MiOS image pipeline (Python/CLI resolver, unattend_gen, the PowerShell answer-file/NTLite/mios-provision/specialize renderers, Render-Sysrescue, the SystemRescue autorun, the kickstart template and the Linux user-creation hash).
# AI-related: usr/lib/mios/mios_credentials.py, usr/libexec/mios/mios-credential, usr/libexec/mios/win/unattend_gen.py, mios-bootstrap field/autounattend/MiOS-Provision.lib.ps1, field/autounattend/New-MiOSAutounattend.ps1, field/autounattend/ConvertTo-MiOSPreset.ps1, field/autounattend/New-MiOSISO.ps1, field/resources/ventoy/Render-Sysrescue.ps1
"""Every renderer takes the account name, password and AutoLogon settings from the
LAYERED SSOT (vendor < host /etc/mios/mios.d < user ~/.config/mios), and fails closed
(non-zero, naming the key) when a credential is missing. Fixture values only -- no
real credential is read, printed or written outside the temp dir.

Env: MIOS_CRED_TEST_ROOT (MiOS tree, default repo root), MIOS_BOOTSTRAP_DIR (bootstrap
tree, default /var/lib/mios-build/repos/mios-bootstrap-build). PowerShell cases skip
when pwsh is absent.
"""

from __future__ import annotations

import base64
import importlib.util
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.environ.get("MIOS_CRED_TEST_ROOT") or os.path.join(_HERE, ".."))
BOOT = os.path.abspath(os.environ.get("MIOS_BOOTSTRAP_DIR") or "/var/lib/mios-build/repos/mios-bootstrap-build")
LIB = os.path.join(ROOT, "usr", "lib", "mios")
CRED_CLI = os.path.join(ROOT, "usr", "libexec", "mios", "mios-credential")
AU = os.path.join(BOOT, "field", "autounattend")
VENTOY = os.path.join(BOOT, "field", "resources", "ventoy")
PWSH = shutil.which("pwsh")
NS = {"u": "urn:schemas-microsoft-com:unattend"}

# Fixture credentials (never real values).
V_USER, V_PASS = "fixture-vendor-user", "fixture-vendor-pass"
H_USER = "fixture-host-user"          # host tier: /etc/mios/mios.d/90-cred.toml
U_PASS = "fixture-pass-1"             # user tier: ~/.config/mios/mios.toml
SVC = "fixture-svc"
FORBIDDEN = {"mios", "user", "password", "mios-sudo", "Administrator"}

sys.path.insert(0, LIB)
import mios_credentials as mc  # noqa: E402


def _main_ssot_tables(*names: str) -> str:
    """The named [windows.xbox.*] tables copied verbatim from the main vendor SSOT (one source: the
    bootstrap lib reads the MiOS-Xbox spec keys and refuses without them)."""
    src = os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
    out, keep = [], False
    with open(src, encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("["):
                keep = line.split("#", 1)[0].strip() in {f"[{n}]" for n in names}
            if keep:
                out.append(line)
    return "".join(out)


XBOX_SPEC_TABLES = _main_ssot_tables("windows.xbox.preferences", "windows.xbox.start")


def _clean_env(extra: dict) -> dict:
    env = {k: v for k, v in os.environ.items() if not k.startswith("MIOS_")}
    env.update({"MIOS_DB_AUTHORITATIVE": "0", "PYTHONDONTWRITEBYTECODE": "1"})
    env.update(extra)
    return env


class Fixture:
    """A temp tier tree: vendor (+vendor mios.d), host (+mios.d override), user override."""

    def __init__(self, vendor: str, host_frag: str | None = None, user: str | None = None):
        self.dir = tempfile.mkdtemp(prefix="mios-cred-test-")
        self.vendor = self._w("usr/share/mios/mios.toml", vendor)
        os.makedirs(os.path.join(self.dir, "usr/lib/mios/mios.d"), exist_ok=True)
        os.makedirs(os.path.join(self.dir, "etc/mios/mios.d"), exist_ok=True)
        os.makedirs(os.path.join(self.dir, "home/.config/mios/mios.d"), exist_ok=True)
        if host_frag is not None:
            self._w("etc/mios/mios.d/90-cred.toml", host_frag)
        if user is not None:
            self._w("home/.config/mios/mios.toml", user)
        self.env = _clean_env({
            "MIOS_BOOTSTRAP_BASE_TOML": "-",
            "MIOS_VENDOR_TOML": self.vendor,
            "MIOS_VENDOR_TOML_D": os.path.join(self.dir, "usr/lib/mios/mios.d"),
            "MIOS_HOST_TOML": os.path.join(self.dir, "etc/mios/mios.toml"),
            "MIOS_HOST_TOML_D": os.path.join(self.dir, "etc/mios/mios.d"),
            "MIOS_USER_TOML": os.path.join(self.dir, "home/.config/mios/mios.toml"),
            "MIOS_USER_TOML_D": os.path.join(self.dir, "home/.config/mios/mios.d"),
            "HOME": os.path.join(self.dir, "home"),
            "XDG_CONFIG_HOME": os.path.join(self.dir, "home/.config"),
        })

    def _w(self, rel: str, text: str) -> str:
        p = os.path.join(self.dir, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def path(self, rel: str) -> str:
        return os.path.join(self.dir, rel)

    def cleanup(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def merged(self) -> dict:
        saved = dict(os.environ)
        try:
            os.environ.clear()
            os.environ.update(self.env)
            return mc.load()
        finally:
            os.environ.clear()
            os.environ.update(saved)


VENDOR = f'''
[identity]
username = "{V_USER}"
fullname = "Fixture Vendor"
default_password = "{V_PASS}"
[autounattend]
computer_name = "FIXTUREPC"
autologon_user = ""
[autounattend.service]
enable = true
svc_user = "{SVC}"
svc_password = ""
[field.sysrescue]
enable = true
username = ""
password = ""
wipe_passphrase = "fixturewipe"
''' + XBOX_SPEC_TABLES
HOST = f'[identity]\nusername = "{H_USER}"\n'
USER = f'[identity]\ndefault_password = "{U_PASS}"\n'


def _layered() -> Fixture:
    return Fixture(VENDOR, HOST, USER)


def _decode_pw(node) -> str:
    val = node.findtext("u:Value", namespaces=NS) or ""
    if (node.findtext("u:PlainText", namespaces=NS) or "").lower() == "false":
        val = base64.b64decode(val).decode("utf-16le")
        assert val.endswith("Password")
        val = val[:-8]
    return val


def _assert_no_literal_creds(tc: unittest.TestCase, root):
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag in ("Name", "Username") and el.text is not None:
            tc.assertNotIn(el.text, FORBIDDEN, f"literal account name in <{tag}>")


# ============================================================================ Python resolver
class TestResolverPython(unittest.TestCase):
    def test_positive_layered_values_win(self):
        fx = _layered()
        try:
            cred = mc.resolve(fx.merged())
        finally:
            fx.cleanup()
        self.assertEqual(cred["username"], H_USER)            # host mios.d override
        self.assertEqual(cred["default_password"], U_PASS)    # user-tier override
        self.assertEqual([a["name"] for a in cred["accounts"]], [H_USER])
        self.assertEqual(cred["accounts"][0]["password"], U_PASS)
        self.assertEqual(cred["service"]["user"], SVC)
        self.assertEqual(cred["service"]["password"], U_PASS)  # "" inherits default_password
        al = cred["autologon"]
        self.assertEqual((al["user"], al["password"], al["persistent"], al["count"]),
                         (H_USER, U_PASS, True, mc.PERSISTENT_LOGON_COUNT))

    def _neg(self, vendor: str, key: str):
        fx = Fixture(vendor)
        try:
            with self.assertRaises(mc.CredentialError) as cm:
                mc.resolve(fx.merged())
        finally:
            fx.cleanup()
        self.assertIn(key, str(cm.exception))
        return str(cm.exception)

    def test_negative_missing_password(self):
        msg = self._neg(VENDOR.replace(f'default_password = "{V_PASS}"', 'default_password = ""'),
                        "identity.default_password")
        self.assertNotIn(V_PASS, msg)

    def test_negative_missing_username(self):
        self._neg(VENDOR.replace(f'username = "{V_USER}"', 'username = ""'), "identity.username")

    def test_negative_missing_svc_user(self):
        self._neg(VENDOR.replace(f'svc_user = "{SVC}"', 'svc_user = ""'), "autounattend.service.svc_user")

    def test_negative_unknown_autologon_user(self):
        self._neg(VENDOR.replace('autologon_user = ""', 'autologon_user = "ghost"'), "autologon_user")

    def test_negative_nonpersistent_needs_count(self):
        self._neg(VENDOR.replace('autologon_user = ""', 'autologon_persistent = false'),
                  "autounattend.autologon_count")


# ============================================================================ CLI
class TestCredentialCli(unittest.TestCase):
    def _run(self, fx: Fixture, *args):
        return subprocess.run([sys.executable, CRED_CLI, *args], env=fx.env,
                              capture_output=True, text=True, timeout=60)

    def test_username_hash_and_json(self):
        fx = _layered()
        try:
            r = self._run(fx, "username")
            self.assertEqual((r.returncode, r.stdout.strip()), (0, H_USER))
            h = self._run(fx, "hash").stdout.strip()
            self.assertTrue(h.startswith("$6$"))
            salt = h.split("$")[2]
            chk = subprocess.run(["openssl", "passwd", "-6", "-salt", salt, "-stdin"],
                                 input=U_PASS + "\n", capture_output=True, text=True).stdout.strip()
            self.assertEqual(h, chk, "hash is not of the user-tier password")
            out = fx.path("resolved.json")
            self.assertEqual(self._run(fx, "win-json", "--out", out).returncode, 0)
            self.assertEqual(stat.S_IMODE(os.stat(out).st_mode), 0o600)
            with open(out, encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual(doc["scalars"]["identity.username"], H_USER)
            self.assertEqual(doc["scalars"]["identity.default_password"], U_PASS)
        finally:
            fx.cleanup()

    def test_env_hash_alias(self):
        fx = _layered()
        try:
            fx.env["MIOS_PASSWORD_HASH"] = "$6$fixturesalt$fixturehashvalue"
            self.assertEqual(self._run(fx, "hash").stdout.strip(), "$6$fixturesalt$fixturehashvalue")
        finally:
            fx.cleanup()

    def test_negative_exit3_names_key(self):
        fx = Fixture(VENDOR.replace(f'default_password = "{V_PASS}"', 'default_password = ""'))
        try:
            for cmd in (["hash"], ["check"], ["win-json", "--out", fx.path("x.json")]):
                r = self._run(fx, *cmd)
                self.assertEqual(r.returncode, 3, cmd)
                self.assertIn("identity.default_password", r.stderr)
                self.assertNotIn(V_PASS, r.stderr + r.stdout)
            self.assertFalse(os.path.exists(fx.path("x.json")))
        finally:
            fx.cleanup()


# ============================================================================ unattend_gen.py
def _load_unattend_gen():
    p = os.path.join(ROOT, "usr", "libexec", "mios", "win", "unattend_gen.py")
    spec = importlib.util.spec_from_file_location("unattend_gen_credtest", p)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


class TestUnattendGen(unittest.TestCase):
    def _cli(self, fx: Fixture, out: str):
        code = ("import sys,runpy; sys.argv=['unattend_gen.py','--output',%r];"
                "sys.exit(runpy.run_path(%r, run_name='m')['main']())") % (
            out, os.path.join(ROOT, "usr/libexec/mios/win/unattend_gen.py"))
        return subprocess.run([sys.executable, "-c", code], env=fx.env, capture_output=True, text=True, timeout=60)

    def test_positive(self):
        fx = _layered()
        try:
            out = fx.path("autounattend.xml")
            r = self._cli(fx, out)
            self.assertEqual(r.returncode, 0, r.stderr[-400:])
            root = ET.parse(out).getroot()
        finally:
            fx.cleanup()
        accts = root.findall(".//u:LocalAccount", NS)
        self.assertEqual([a.findtext("u:Name", namespaces=NS) for a in accts], [H_USER])
        self.assertEqual(_decode_pw(accts[0].find("u:Password", NS)), U_PASS)
        al = root.find(".//u:AutoLogon", NS)
        self.assertEqual(al.findtext("u:Username", namespaces=NS), H_USER)
        self.assertEqual(_decode_pw(al.find("u:Password", NS)), U_PASS)
        self.assertEqual(al.findtext("u:LogonCount", namespaces=NS), str(mc.PERSISTENT_LOGON_COUNT))
        cmds = [c.findtext("u:CommandLine", namespaces=NS) for c in root.findall(".//u:SynchronousCommand", NS)]
        self.assertTrue(any("AutoLogonCount" in (c or "") for c in cmds))
        _assert_no_literal_creds(self, root)

    def test_negative(self):
        fx = Fixture(VENDOR.replace(f'default_password = "{V_PASS}"', 'default_password = ""'))
        try:
            out = fx.path("autounattend.xml")
            r = self._cli(fx, out)
            self.assertEqual(r.returncode, 3)
            self.assertIn("identity.default_password", r.stderr)
            self.assertFalse(os.path.exists(out))
        finally:
            fx.cleanup()
        ug = _load_unattend_gen()
        with self.assertRaises(ValueError):
            ug.UnattendGenerator(ug.UnattendConfig(username="", password="")).build_xml_tree()


# ============================================================================ PowerShell
def _ps(fx: Fixture, script: str, timeout: int = 300):
    return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-Command", script], env=fx.env,
                          capture_output=True, text=True, timeout=timeout)


@unittest.skipUnless(PWSH and os.path.isdir(AU), "pwsh or bootstrap tree absent")
class TestWindowsAnswerFiles(unittest.TestCase):
    def _render(self, fx: Fixture, out: str, extra: str = ""):
        return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File",
                               os.path.join(AU, "New-MiOSAutounattend.ps1"), "-TomlPath", fx.vendor,
                               "-OutXml", out, "-ObfuscatePasswords"] + (extra.split() if extra else []),
                              env=fx.env, capture_output=True, text=True, timeout=300)

    def test_autounattend_positive(self):
        fx = _layered()
        try:
            out = fx.path("autounattend.xml")
            r = self._render(fx, out)
            self.assertEqual(r.returncode, 0, r.stderr[-600:])
            root = ET.parse(out).getroot()
            accts = root.findall(".//u:LocalAccount", NS)
            self.assertEqual([a.findtext("u:Name", namespaces=NS) for a in accts], [H_USER])
            self.assertEqual(_decode_pw(accts[0].find("u:Password", NS)), U_PASS)
            al = root.find(".//u:AutoLogon", NS)
            self.assertEqual(al.findtext("u:Enabled", namespaces=NS), "true")
            self.assertEqual(al.findtext("u:Username", namespaces=NS), H_USER)
            self.assertEqual(_decode_pw(al.find("u:Password", NS)), U_PASS)
            self.assertEqual(al.findtext("u:LogonCount", namespaces=NS), "9999999")
            ext = root.find("{urn:mios:unattend}Extensions")
            fl = base64.b64decode(ext.findtext("{urn:mios:unattend}FirstLogonScript")).decode("utf-16le")
            self.assertIn("/v AutoLogonCount /f", fl)                       # persistent sign-in
            self.assertIn(f"$_.Name -ne ''{H_USER}''", fl)                  # not password-expired
            sp = base64.b64decode(ext.findtext("{urn:mios:unattend}SpecializeScript")).decode("utf-16le")
            self.assertIn(f'/ru "{SVC}" /rp "{U_PASS}"', sp)               # service tasks: SSOT svc creds
            _assert_no_literal_creds(self, root)
        finally:
            fx.cleanup()

    def test_autounattend_resolved_json_overrides_fork(self):
        # Bootstrap-style vendor fork says V_USER; the main layered SSOT (projected by
        # mios-credential win-json) says H_USER/U_PASS -> the projection wins.
        main = _layered()
        fork = Fixture(VENDOR)
        try:
            js = main.path("resolved.json")
            r = subprocess.run([sys.executable, CRED_CLI, "win-json", "--out", js], env=main.env,
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 0, r.stderr)
            out = fork.path("a.xml")
            r = self._render(fork, out, f"-ResolvedJson {js}")
            self.assertEqual(r.returncode, 0, r.stderr[-600:])
            root = ET.parse(out).getroot()
            al = root.find(".//u:AutoLogon", NS)
            self.assertEqual(al.findtext("u:Username", namespaces=NS), H_USER)
            self.assertEqual(_decode_pw(al.find("u:Password", NS)), U_PASS)
        finally:
            main.cleanup()
            fork.cleanup()

    def test_autounattend_negative(self):
        for bad, key in ((VENDOR.replace(f'default_password = "{V_PASS}"', 'default_password = ""'), "identity.default_password"),
                         (VENDOR.replace(f'username = "{V_USER}"', 'username = ""'), "identity.username"),
                         (VENDOR.replace(f'svc_user = "{SVC}"', 'svc_user = ""'), "autounattend.service.svc_user")):
            fx = Fixture(bad)
            try:
                out = fx.path("autounattend.xml")
                r = self._render(fx, out)
                self.assertNotEqual(r.returncode, 0)
                self.assertIn(key, r.stderr + r.stdout)
                self.assertFalse(os.path.exists(out))
                self.assertNotIn(V_PASS, r.stderr + r.stdout)
            finally:
                fx.cleanup()

    def test_ntlite_preset_path_is_retired(self):
        # xb-20: the NTLite preset converter produced the legacy MiOS-Xbox images; it now refuses before
        # reading or writing anything (MiOS-Xbox images come only from usr/libexec/mios/win/xbox/build.py).
        fx = Fixture(VENDOR, None, USER)
        try:
            out = fx.path("preset.xml")
            r = subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File",
                                os.path.join(AU, "ConvertTo-MiOSPreset.ps1"), "-InputPreset",
                                os.path.join(AU, "presets", "mios-xbox.xml"), "-OutputPreset", out,
                                "-TomlPath", fx.vendor], env=fx.env, capture_output=True, text=True, timeout=300)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("retired", r.stdout + r.stderr)
            self.assertFalse(os.path.exists(out))
            self.assertNotIn(U_PASS, r.stdout + r.stderr)
        finally:
            fx.cleanup()

    def test_mios_provision_cmd_and_specialize(self):
        fx = _layered()
        try:
            dst = fx.path("scripts")
            os.makedirs(dst)
            script = f'''
$ErrorActionPreference = 'Stop'
. '{os.path.join(AU, "MiOS-Provision.lib.ps1")}'
$ast = [System.Management.Automation.Language.Parser]::ParseFile('{os.path.join(AU, "New-MiOSISO.ps1")}', [ref]$null, [ref]$null)
$fn = $ast.Find({{ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq 'Set-MiOSProvisionWiring' }}, $true)
Invoke-Expression $fn.Extent.Text
$t = Read-MiosToml -Path '{fx.vendor}'
Set-MiOSProvisionWiring -Mount '{dst}' -ScriptsDst '{dst}' -Toml $t | Out-Null
(New-MiOSHostServiceCommands -Toml $t) -join "`n" | Set-Content -LiteralPath '{fx.path("spec.txt")}'
'''
            r = _ps(fx, script)
            self.assertEqual(r.returncode, 0, r.stderr[-600:])
            with open(os.path.join(dst, "mios-provision.cmd"), encoding="ascii") as fh:
                cmd = fh.read()
            self.assertIn(f'set "ALUSER={H_USER}"', cmd)
            self.assertIn(f'set "ALPW={U_PASS}"', cmd)
            self.assertIn(f'if "%SVCUSER%"=="" set "SVCUSER={SVC}"', cmd)
            # setup phase: RID-500 service account + provisioning shell (re-armed N boots) ...
            self.assertIn('/v DefaultUserName /t REG_SZ /d "%SVCUSER%"', cmd)
            # ... then the SSOT console account is staged (admin-only) for a persistent hand-over.
            self.assertIn('MiOS\\AutoLogon" /v User /t REG_SZ /d "%ALUSER%"', cmd)
            self.assertIn('MiOS\\AutoLogon" /v Persistent /t REG_DWORD /d 1', cmd)
            self.assertIn("S-1-5-32-544", cmd)
            with open(fx.path("spec.txt"), encoding="utf-8") as fh:
                spec = fh.read()
            self.assertIn(f'/ru "{SVC}" /rp "{U_PASS}"', spec)
        finally:
            fx.cleanup()


@unittest.skipUnless(PWSH and os.path.isdir(VENTOY), "pwsh or bootstrap tree absent")
class TestFieldMedia(unittest.TestCase):
    def _stick(self, fx: Fixture) -> str:
        stick = fx.path("stick")
        os.makedirs(os.path.join(stick, "ventoy"))
        for f in ("sysrescue_grub.cfg", "sysrescue_syslinux.cfg", "mios-kickstart.cfg"):
            shutil.copy(os.path.join(VENTOY, f), os.path.join(stick, "ventoy", f))
        return stick

    def _render(self, fx: Fixture, stick: str):
        return subprocess.run([PWSH, "-NoProfile", "-NonInteractive", "-File",
                               os.path.join(VENTOY, "Render-Sysrescue.ps1"), "-TargetRoot", stick,
                               "-TomlPath", fx.vendor], env=fx.env, capture_output=True, text=True, timeout=300)

    def test_templates_carry_no_literal_credential(self):
        for f in ("sysrescue_grub.cfg", "sysrescue_syslinux.cfg"):
            with open(os.path.join(VENTOY, f), encoding="utf-8") as fh:
                txt = fh.read()
            self.assertNotRegex(txt, r"\b(rootpass|userpass)=mios\b")
            self.assertNotRegex(txt, r"(?m)^password rescue mios\b")
        with open(os.path.join(VENTOY, "mios-kickstart.cfg"), encoding="utf-8") as fh:
            ks = fh.read()
        self.assertRegex(ks, r"(?m)^user --name=__mios_ks_user__ \S+ --lock$")
        self.assertNotRegex(ks, r"(?m)^user .*--password=")

    def test_render_positive(self):
        fx = _layered()
        try:
            stick = self._stick(fx)
            r = self._render(fx, stick)
            self.assertEqual(r.returncode, 0, r.stderr[-600:])
            with open(os.path.join(stick, "ventoy", "sysrescue_grub.cfg"), encoding="ascii") as fh:
                grub = fh.read()
            self.assertIn(f"rootpass={U_PASS}", grub)
            self.assertIn("password rescue fixturewipe", grub)
            self.assertNotIn("__MIOS_SR_", grub)
            with open(os.path.join(stick, "ventoy", "mios-kickstart.cfg"), encoding="ascii") as fh:
                ks = fh.read()
            self.assertRegex(ks, rf"(?m)^user --name={re.escape(H_USER)} \S+ --iscrypted --password=\$6\$")
            with open(os.path.join(stick, "mios-sysrescue.env"), encoding="ascii") as fh:
                env = fh.read()
            self.assertIn(f"MIOS_SR_USER='{H_USER}'", env)      # [field.sysrescue].username "" -> identity
            self.assertIn(f"MIOS_SR_PASS='{U_PASS}'", env)
        finally:
            fx.cleanup()

    def test_render_negative(self):
        fx = Fixture(VENDOR.replace(f'default_password = "{V_PASS}"', 'default_password = ""'))
        try:
            stick = self._stick(fx)
            r = self._render(fx, stick)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("default_password", r.stderr + r.stdout)
            with open(os.path.join(stick, "ventoy", "sysrescue_grub.cfg"), encoding="utf-8") as fh:
                self.assertIn("__MIOS_SR_ROOTPASS__", fh.read())   # untouched, no literal substituted
            self.assertFalse(os.path.exists(os.path.join(stick, "mios-sysrescue.env")))
        finally:
            fx.cleanup()


# ============================================================================ SystemRescue autorun
class TestSysrescueAutorun(unittest.TestCase):
    SCRIPTS = [os.path.join(ROOT, "usr/share/mios/ventoy/autorun/01-sysrescue-firstboot.sh"),
               os.path.join(BOOT, "field/resources/autorun/01-sysrescue-firstboot.sh")]

    def _cred_block(self, path: str) -> str:
        with open(path, encoding="utf-8") as fh:
            txt = fh.read()
        a = txt.index("MIOS_USER=\"\"; MIOS_PASS=\"\"")
        b = txt.index("# 1. SSH host keys")
        return txt[a:b]

    def _run(self, block: str, d: str):
        prog = "set -euo pipefail\n" + block + '\nprintf "%s|%s\\n" "$MIOS_USER" "$MIOS_PASS"\n'
        return subprocess.run(["bash", "-c", prog, os.path.join(d, "autorun0")],
                              capture_output=True, text=True, timeout=30)

    def test_env_positive_and_fail_closed(self):
        for path in self.SCRIPTS:
            if not os.path.isfile(path):
                continue
            with self.subTest(path=path):
                block = self._cred_block(path)
                self.assertNotRegex(block, r'MIOS_(USER|PASS)="mios"')
                d = tempfile.mkdtemp(prefix="mios-sr-")
                try:
                    with open(os.path.join(d, "mios-sysrescue.env"), "w") as fh:
                        fh.write(f"MIOS_SR_USER='{H_USER}'\nMIOS_SR_PASS='{U_PASS}'\n")
                    r = self._run(block, d)
                    self.assertEqual(r.stdout.strip().splitlines()[-1], f"{H_USER}|{U_PASS}")
                    os.unlink(os.path.join(d, "mios-sysrescue.env"))
                    r = self._run(block, d)
                    self.assertEqual(r.stdout.strip().splitlines()[-1], "|")
                    self.assertIn("no SSOT credential", r.stderr)
                finally:
                    shutil.rmtree(d, ignore_errors=True)


# ============================================================================ static negative controls
class TestNoLiteralCredentialDefaults(unittest.TestCase):
    def _read(self, *parts):
        p = os.path.join(*parts)
        if not os.path.isfile(p):
            self.skipTest(f"{p} absent")
        with open(p, encoding="utf-8", errors="replace") as fh:
            return fh.read()

    def test_linux_user_creation(self):
        s = self._read(ROOT, "automation/11-user.sh")
        self.assertNotRegex(s, r"openssl passwd -6 '")
        self.assertNotIn('MIOS_IDENTITY_USERNAME:-mios', s)
        self.assertIn("_mios_cred hash", s)
        b = self._read(ROOT, "automation/build-mios.sh")
        self.assertNotIn('--build-arg MIOS_PASSWORD_HASH=', b)
        cf = self._read(ROOT, "Containerfile")
        self.assertNotIn("ARG MIOS_USER_PASSWORD_HASH", cf)
        self.assertIn("--mount=type=secret,id=mios_user_password_hash", cf)
        jf = self._read(ROOT, "Justfile")
        self.assertNotIn("--build-arg MIOS_USER_PASSWORD_HASH", jf)
        self.assertIn("--secret id=mios_user_password_hash", jf)

    def test_ai_firstboot_seed(self):
        s = self._read(ROOT, "usr/libexec/mios/mios-ai-firstboot")
        self.assertNotRegex(s, r'"name":\s*"user"')
        self.assertNotRegex(s, r'raw_pwd == "user"')
        self.assertNotRegex(s, r'default_password",\s*"user"')

    def test_powershell_literal_fallbacks_gone(self):
        pats = [r"Get-Toml \$Toml 'identity\.(username|default_password)' '(mios|user)'",
                r"'autounattend\.service\.svc_(user|password)' '(mios-sudo|mios|user)'",
                r"@\{u='mios';p="]
        for f in ("MiOS-Provision.lib.ps1", "New-MiOSAutounattend.ps1", "ConvertTo-MiOSPreset.ps1",
                  "New-MiOSISO.ps1", "MiOS-Provision.ps1", "Deploy-MiOSXbox.ps1", "Invoke-MiOSProvision.ps1"):
            s = self._read(AU, f)
            for p in pats:
                self.assertNotRegex(s, p, f"{f}: {p}")

    def test_windows_installer_login_credential(self):
        s = self._read(BOOT, "build-mios.ps1")
        self.assertNotRegex(s, r"return '\$6\$")                      # no precomputed literal hash
        self.assertNotRegex(s, r"return @\{ Hash = ''; Plain = '[^']+' \}")  # no literal last resort
        self.assertIn("no login credential in any mios.toml layer", s)

    def test_runtime_driver_hands_over_persistent_autologon(self):
        s = self._read(AU, "MiOS-Provision.ps1")
        self.assertIn("HKLM:\\SOFTWARE\\MiOS\\AutoLogon", s)
        self.assertIn("Remove-ItemProperty -Path $winlogon -Name AutoLogonCount", s)
        self.assertNotRegex(s, r"Cfg 'identity\.default_password' '(user|mios)'")

    def test_tracked_preset_scrubbed(self):
        p = os.path.join(AU, "MiOS-Xbox.xml")
        if not os.path.isfile(p):
            self.skipTest("MiOS-Xbox.xml absent")
        root = ET.parse(p).getroot()
        for el in root.iter():
            if el.tag.split("}")[-1] in ("Name", "Username"):
                self.assertNotIn((el.text or "").strip(), FORBIDDEN)
        for pw in root.iter():
            if pw.tag.split("}")[-1] == "Password":
                for v in pw:
                    if v.tag.split("}")[-1] == "Value":
                        self.assertEqual((v.text or "").strip(), "")


if __name__ == "__main__":
    unittest.main(verbosity=2)
