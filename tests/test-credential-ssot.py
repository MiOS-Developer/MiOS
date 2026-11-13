#!/usr/bin/env python3
# AI-hint: Positive + negative controls for the ONE fail-closed layered-SSOT credential resolution across every MiOS image pipeline (Python/CLI resolver, unattend_gen, the PowerShell answer-file/NTLite/mios-provision/specialize renderers, Render-Sysrescue, the SystemRescue autorun, the kickstart template and the Linux user-creation hash).
# AI-related: usr/lib/mios/mios_credentials.py, usr/libexec/mios/mios-credential, usr/libexec/mios/win/unattend_gen.py
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
BOOT = os.path.abspath(os.environ.get("MIOS_BOOTSTRAP_DIR") or
                       os.environ.get("MIOS_BOOTSTRAP_ROOT") or
                       "/var/lib/mios-build/repos/mios-bootstrap-build")
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
        a = txt.index('MIOS_USER=""')
        b = txt.index("# 1. SSH host keys")
        return txt[a:b]

    def _run(self, block: str, d: str):
        prog = "set -euo pipefail\n" + block + '\nprintf "%s|%s\\n" "$MIOS_USER" "${_mios_sysrescue_password-${MIOS_PASS-}}"\n'
        return subprocess.run(["bash", "-c", prog, os.path.join(d, "autorun0")],
                              capture_output=True, text=True, timeout=30)

    def test_env_positive_and_fail_closed(self):
        for path in self.SCRIPTS:
            if not os.path.isfile(path):
                continue
            with self.subTest(path=path):
                block = self._cred_block(path)
                self.assertNotRegex(block, r'(?:MIOS_USER|MIOS_PASS|_mios_sysrescue_password)="mios"')
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



# ============================================================================ operator tier (mios.html -> services)
import http.server  # noqa: E402
import threading  # noqa: E402
import types  # noqa: E402

import mios_toml  # noqa: E402

OP_USER, OP_PASS = "fixop", "fixture-operator-pass"   # what mios.html saved (operator tier)
OTHER_PASS = "fixture-other-human-pass"
OPTIER = ('[security.operator_tier]\nvalues = ["identity.default_password", "portal.password", "ttyd.auth_pass"]\n'
          'toggles = ["tailscale.serve.enabled", "tailscale.serve.routes.*.enabled"]\n')


def _getpw(homes: dict):
    def getpw(name):
        if name not in homes:
            raise KeyError(name)
        return types.SimpleNamespace(pw_dir=homes[name])
    return getpw


def _round2_view(getpw=None, euid=None, uid_min=None):
    """PLANT: the round-2 resolver -- the WHOLE operator tier merged over vendor/host."""
    vendor, vendor_d, host, host_d, _u, _ud = mios_toml._tier_dirs()
    base = mios_toml.load_strict([vendor] + mios_toml._frags(vendor_d) + [host] + mios_toml._frags(host_d))
    op_file, op_d, reason, _system = mios_toml.operator_inputs(getpw, euid, uid_min, base)
    mios_toml.deep_merge(base, mios_toml.load_strict([op_file] + mios_toml._frags(op_d)))
    return base, reason, frozenset()


def _own_tier_view(getpw=None, euid=None, uid_min=None):
    """PLANT: the pre-fix resolver -- the caller's own (root's) tiers."""
    return mios_toml.load_merged(mios_toml.layer_paths()), "planted", frozenset()


class _OpFixture(Fixture):
    """Vendor V_PASS, host names OP_USER whose home holds OP_PASS, and the CALLER's
    own (root's) home holds nothing -- the Blade's lockout shape."""

    def __init__(self, host_frag=f'[identity]\nusername = "{OP_USER}"\n', op_toml=None, vendor_extra=""):
        super().__init__(VENDOR + OPTIER + vendor_extra, host_frag, None)
        self.op_home = self.path("home-op")
        self._w("home-op/.config/mios/mios.toml",
                op_toml if op_toml is not None else f'[identity]\ndefault_password = "{OP_PASS}"\n')
        for k in ("MIOS_USER_TOML", "MIOS_USER_TOML_D"):
            self.env.pop(k)
        self.getpw = _getpw({OP_USER: self.op_home})

    def view(self, euid=0, plant=None):
        saved = dict(os.environ)
        orig = mios_toml.operator_view
        try:
            os.environ.clear()
            os.environ.update(self.env)
            if plant:
                mios_toml.operator_view = plant
            return mc.operator_load(getpw=self.getpw, euid=euid)
        finally:
            mios_toml.operator_view = orig
            os.environ.clear()
            os.environ.update(saved)

    def resolve(self, surface="forge", euid=0, plant=None):
        data, reason, _taken = self.view(euid, plant)
        return mc.surface_password(surface, data, env={})["value"], reason


def _operator_defect(fx: _OpFixture, **kw):
    """None when a system caller resolves the operator's mios.html value; else the defect."""
    got, reason = fx.resolve(**kw)
    if got == V_PASS:
        return f"operator tier ignored: a system caller resolved the vendor value ({reason})"
    if got != OP_PASS:
        return f"resolved an unexpected tier ({reason})"
    return None


class TestOperatorTier(unittest.TestCase):
    def test_positive_system_caller_reads_the_operator_tier(self):
        fx = _OpFixture()
        try:
            self.assertIsNone(_operator_defect(fx))
            self.assertIn(OP_USER, fx.resolve()[1])
        finally:
            fx.cleanup()

    def test_negative_plain_layer_paths_ignores_the_operator(self):
        fx = _OpFixture()
        try:
            defect = _operator_defect(fx, plant=_own_tier_view)
            self.assertIsNotNone(defect)
            self.assertIn("operator tier ignored", defect)
        finally:
            fx.cleanup()

    def test_explicit_user_toml_wins(self):
        fx = _OpFixture()
        try:
            fx.env["MIOS_USER_TOML"] = fx._w("explicit.toml", f'[identity]\ndefault_password = "{OTHER_PASS}"\n')
            got, reason = fx.resolve()
            self.assertEqual((got, reason), (OTHER_PASS, "explicit MIOS_USER_TOML"))
        finally:
            fx.cleanup()

    def test_human_caller_keeps_its_own_tier(self):
        fx = _OpFixture()
        try:
            fx._w("home/.config/mios/mios.toml", f'[identity]\ndefault_password = "{OTHER_PASS}"\n')
            got, reason = fx.resolve(euid=4242)
            self.assertEqual((got, reason), (OTHER_PASS, "human caller's own tier"))
        finally:
            fx.cleanup()

    def test_negative_operator_tier_cannot_name_the_operator(self):
        # Only vendor/host name the account. With the vendor account absent the view FAILS
        # (it never falls back to the vendor default); a username the tier claims is ignored.
        fx = _OpFixture(host_frag=None,
                        op_toml=f'[identity]\nusername = "{OP_USER}"\ndefault_password = "{OP_PASS}"\n')
        try:
            with self.assertRaises(mc.CredentialError) as cm:
                fx.resolve()
            self.assertIn("absent", str(cm.exception))
        finally:
            fx.cleanup()
        fx = _OpFixture(op_toml=f'[identity]\nusername = "someone-else"\ndefault_password = "{OP_PASS}"\n')
        try:
            data, reason, _taken = fx.view()
            self.assertEqual(mc.surface_password("forge", data, env={})["value"], OP_PASS)
            self.assertEqual(mc.surface_user("forge", data), OP_USER)
            self.assertIn("ignored", reason)
        finally:
            fx.cleanup()

    def test_negative_invisible_operator_home_fails_closed(self):
        # A unit with ProtectHome=yes sees no /home: an operator file there would look absent
        # and the vendor default would win. The home itself must be visible, or nothing resolves.
        fx = _OpFixture()
        try:
            fx.getpw = _getpw({OP_USER: fx.path("hidden-home")})
            with self.assertRaises(mc.CredentialError) as cm:
                fx.resolve()
            self.assertIn("not visible", str(cm.exception))
        finally:
            fx.cleanup()

    def test_negative_no_layer_fails_closed(self):
        fx = _OpFixture()
        try:
            fx.env["MIOS_VENDOR_TOML"] = fx.path("absent.toml")
            fx.env["MIOS_HOST_TOML_D"] = fx.path("absent.d")
            with self.assertRaises(mc.CredentialError):
                fx.resolve()
            with self.assertRaises(mc.CredentialError) as cm:
                mc.surface_password("forge", {}, env={})
            self.assertIn("identity.default_password", str(cm.exception))
        finally:
            fx.cleanup()


# The operator tier as a hostile writer would shape it: every key that steers root code.
HOSTILE = f'''[identity]
username = "root"
default_password = "{OP_PASS}"
email = "evil@example.invalid"
[security.credential_apply]
surfaces = ["shadow"]
[security.operator_tier]
values = ["paths.libexec_dir"]
[paths]
libexec_dir = "/tmp/fixture-evil"
[network]
loopback = "10.9.9.9"
[ports]
forge_http = 1
[units]
forge = "evil.service"
[portal]
password = "{OTHER_PASS}"
public_host = "x\\n[Service]"
[tailscale.serve]
enabled = true
[tailscale.serve.routes.portal]
enabled = true
upstream = "cockpit"
[tailscale.serve.routes.evil]
enabled = true
'''
VEXTRA = '''[security.credential_apply]
surfaces = ["portal"]
[paths]
libexec_dir = "/usr/libexec/mios"
[network]
loopback = "127.0.0.1"
[ports]
forge_http = 8400
[units]
forge = "mios-forge.service"
[portal]
public_host = ""
[services.forge]
initial_repo = "mios"
[tailscale.serve]
enabled = false
[tailscale.serve.routes.portal]
enabled = false
upstream = "agent_pipe"
'''
_STEERING = {"identity.username": OP_USER, "identity.email": None,
             "security.credential_apply.surfaces": ["portal"], "paths.libexec_dir": "/usr/libexec/mios",
             "network.loopback": "127.0.0.1", "ports.forge_http": 8400, "units.forge": "mios-forge.service",
             "portal.public_host": "", "tailscale.serve.routes.portal.upstream": "agent_pipe",
             "security.operator_tier.values": ["identity.default_password", "portal.password", "ttyd.auth_pass"]}


def _restricted_defect(data: dict):
    """None when root's view took ONLY the credential values and toggles from HOSTILE."""
    for key, want in _STEERING.items():
        if mios_toml._dotted_get(data, key) != want:
            return f"operator tier steered root: {key}"
    if mios_toml._dotted_get(data, "tailscale.serve.routes.evil") is not None:
        return "operator tier created a route table"
    for key, want in (("identity.default_password", OP_PASS), ("portal.password", OTHER_PASS),
                      ("tailscale.serve.enabled", True), ("tailscale.serve.routes.portal.enabled", True)):
        if mios_toml._dotted_get(data, key) != want:
            return f"operator credential or toggle not taken: {key}"
    return None


class TestRestrictedView(unittest.TestCase):
    """Critical C1/C2: root code takes from the operator tier ONLY [security.operator_tier]."""

    def test_positive_only_values_and_toggles_cross(self):
        fx = _OpFixture(op_toml=HOSTILE, vendor_extra=VEXTRA)
        try:
            data, reason, taken = fx.view()
            self.assertIsNone(_restricted_defect(data))
            self.assertEqual(set(taken), {"identity.default_password", "portal.password",
                                          "tailscale.serve.enabled", "tailscale.serve.routes.portal.enabled"})
            self.assertIn("ignored", reason)
        finally:
            fx.cleanup()

    def test_negative_round2_view_is_steered(self):
        fx = _OpFixture(op_toml=HOSTILE, vendor_extra=VEXTRA)
        try:
            defect = _restricted_defect(fx.view(plant=_round2_view)[0])
            self.assertIsNotNone(defect)
            self.assertTrue(defect.startswith("operator tier steered root"), defect)
        finally:
            fx.cleanup()

    # A refused credential VALUE fails closed: the operator's credential is present but
    # unusable, and resolving the vendor default instead would push a public value to
    # every surface (a numeric PIN, a tab, a newline, a boolean).
    REFUSED = ("default_password = 12345678", 'default_password = "pw\\twith-tab"',
               'default_password = "a\\nb"', "default_password = true")

    def test_refused_credential_fails_closed_and_a_refused_toggle_keeps_the_vendor_value(self):
        for row in self.REFUSED:
            fx = _OpFixture(op_toml=f"[identity]\n{row}\n", vendor_extra=VEXTRA)
            try:
                self.assertIsNone(_refused_defect(fx), row)
            finally:
                fx.cleanup()
        fx = _OpFixture(op_toml=f'[identity]\ndefault_password = "{OP_PASS}"\n[tailscale.serve]\nenabled = "true"\n',
                        vendor_extra=VEXTRA)
        try:                                    # control: a valid string applies; a bad toggle stays off
            data, reason, taken = fx.view()
            self.assertEqual(mc.surface_password("forge", data, env={})["value"], OP_PASS)
            self.assertIs(mios_toml._dotted_get(data, "tailscale.serve.enabled"), False)
            self.assertEqual(taken, frozenset({"identity.default_password"}))
            self.assertIn("refused toggle", reason)
        finally:
            fx.cleanup()

    def test_negative_a_view_that_skips_a_refused_value_resolves_the_vendor_default(self):
        fx = _OpFixture(op_toml="[identity]\ndefault_password = 12345678\n", vendor_extra=VEXTRA)
        try:
            self.assertEqual(_refused_defect(fx, plant=_skipping_view),
                             "a refused operator credential resolved to the vendor default")
        finally:
            fx.cleanup()


def _skipping_view(getpw=None, euid=None, uid_min=None):
    """PLANT: the round-3 view -- a refused operator value is skipped, so vendor/host wins."""
    base = mios_toml.base_view()
    op_file, op_d, reason, _system = mios_toml.operator_inputs(getpw, euid, uid_min, base)
    op = mios_toml.load_strict([op_file] + mios_toml._frags(op_d))
    for dotted in base["security"]["operator_tier"]["values"]:
        v = mios_toml._dotted_get(op, dotted)
        if isinstance(v, str) and v and not mios_toml._CTRL.search(v):
            mios_toml._dotted_set(base, dotted, v)
    return mios_toml.derive_ports(base), reason + "; refused", frozenset()


def _refused_defect(fx: "_OpFixture", plant=None):
    """None when a refused operator credential raises (naming the key, never the value)."""
    try:
        got, _reason = fx.resolve(plant=plant)
    except mc.CredentialError as exc:
        msg = str(exc)
        if "identity.default_password" not in msg or "refused" not in msg:
            return f"the error does not name the refused key: {msg}"
        if any(s in msg for s in ("12345678", "with-tab", "a\nb")):
            return "the error carries the refused value"
        return None
    return ("a refused operator credential resolved to the vendor default" if got == V_PASS
            else "a refused operator credential resolved to a value")


def _fail_closed_defect(fx: _OpFixture, **kw):
    """None when an unparseable or unlocatable tier raises; else what resolved instead."""
    try:
        got, _reason = fx.resolve(**kw)
    except mc.CredentialError as exc:
        return None if "fixture-broken-secret" not in str(exc) else "the error carries the tier's content"
    return f"a broken operator tier resolved to {'the vendor default' if got == V_PASS else 'a value'}"


class TestFailClosed(unittest.TestCase):
    """High: a typo in mios.html, a broken host layer or an absent operator account must
    write nothing -- never fall back to the public vendor default."""

    def test_positive_broken_layers_raise_naming_the_file(self):
        fx = _OpFixture(op_toml='[identity\ndefault_password = "fixture-broken-secret"\n')
        try:
            self.assertIsNone(_fail_closed_defect(fx))
            with self.assertRaises(mc.CredentialError) as cm:
                fx.resolve()
            self.assertIn("home-op/.config/mios/mios.toml", str(cm.exception))
        finally:
            fx.cleanup()
        fx = _OpFixture()
        try:
            fx._w("etc/mios/mios.d/95-broken.toml", "[x\n")
            self.assertIsNone(_fail_closed_defect(fx))
            os.unlink(fx.path("etc/mios/mios.d/95-broken.toml"))
            fx.getpw = _getpw({})                       # the operator account is absent
            self.assertIsNone(_fail_closed_defect(fx))
        finally:
            fx.cleanup()

    def test_negative_degrading_loader_falls_back_to_the_vendor_default(self):
        fx = _OpFixture(op_toml='[identity\ndefault_password = "fixture-broken-secret"\n')
        orig, orig_op = mios_toml.load_strict, mios_toml.load_operator_strict
        try:
            mios_toml.load_strict = lambda paths: mios_toml.load_merged([p for p in paths if p])   # PLANT
            mios_toml.load_operator_strict = lambda op_file, _uid: mios_toml.load_merged([op_file])
            defect = _fail_closed_defect(fx)
        finally:
            mios_toml.load_strict, mios_toml.load_operator_strict = orig, orig_op
            fx.cleanup()
        self.assertEqual(defect, "a broken operator tier resolved to the vendor default")


class TestOperatorFiles(unittest.TestCase):
    """Root reads the operator's ~/.config/mios tree without following a link, blocking on a
    FIFO, crossing a hard link or reading an unbounded file."""

    def _defect(self, plant_fn, plant=None):
        fx = _OpFixture()
        try:
            secret = fx._w("root-only.toml", '[identity]\ndefault_password = "fixture-root-only"\n')
            os.chmod(secret, 0o600)
            plant_fn(fx, secret)
            try:
                got, _reason = fx.resolve(plant=plant)
            except mc.CredentialError as exc:
                return None if "fixture-root-only" not in str(exc) else "the error carries the file's content"
            return "a planted file was read" if got == "fixture-root-only" else f"resolved {got!r}"
        finally:
            fx.cleanup()

    @staticmethod
    def _link_file(fx, secret):
        os.unlink(fx.path("home-op/.config/mios/mios.toml"))
        os.symlink(secret, fx.path("home-op/.config/mios/mios.toml"))

    @staticmethod
    def _link_dir(fx, secret):
        shutil.rmtree(fx.path("home-op/.config/mios"))
        os.makedirs(fx.path("elsewhere"))
        os.rename(secret, fx.path("elsewhere/mios.toml"))
        os.symlink(fx.path("elsewhere"), fx.path("home-op/.config/mios"))

    @staticmethod
    def _hard_link(fx, secret):
        os.unlink(fx.path("home-op/.config/mios/mios.toml"))
        os.link(secret, fx.path("home-op/.config/mios/mios.toml"))

    @staticmethod
    def _fragment_link(fx, secret):
        os.makedirs(fx.path("home-op/.config/mios/mios.d"))
        os.symlink(secret, fx.path("home-op/.config/mios/mios.d/50-x.toml"))

    @staticmethod
    def _oversize(fx, _secret):
        with open(fx.path("home-op/.config/mios/mios.toml"), "w") as fh:
            fh.write("# " + "x" * (mios_toml.OPERATOR_MAX_BYTES + 8) + "\n")

    @staticmethod
    def _fifo(fx, _secret):
        os.unlink(fx.path("home-op/.config/mios/mios.toml"))
        os.mkfifo(fx.path("home-op/.config/mios/mios.toml"))

    def test_positive_the_operator_file_is_read_and_planted_shapes_are_refused(self):
        fx = _OpFixture()
        try:
            self.assertEqual(fx.resolve()[0], OP_PASS)                   # control: a plain file is read
            fx._w("home-op/.config/mios/mios.d/50-frag.toml", '[identity]\ndefault_password = "fixture-frag"\n')
            self.assertEqual(fx.resolve()[0], "fixture-frag")            # its mios.d is read too
        finally:
            fx.cleanup()
        for plant in (self._link_file, self._link_dir, self._hard_link, self._fragment_link,
                      self._oversize, self._fifo):
            self.assertIsNone(self._defect(plant), plant.__name__)

    def test_negative_a_following_reader_takes_the_planted_file(self):
        follow = lambda getpw=None, euid=None, uid_min=None: _round2_view(getpw, euid, uid_min)  # noqa: E731
        for plant in (self._link_file, self._link_dir, self._hard_link):
            self.assertEqual(self._defect(plant, plant=follow), "a planted file was read", plant.__name__)


class TestPortalLogin(unittest.TestCase):
    """The Portal never takes a tier default: the applied credstore, else nothing."""

    class _Log:
        def __init__(self):
            self.errors = []

        def error(self, msg, *a):
            self.errors.append(msg % a if a else msg)

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="mios-portal-login-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.data = {"identity": {"username": OP_USER, "default_password": V_PASS},
                     "paths": {"install_env": os.path.join(self.d, "install.env")}}

    def test_positive_credstore_is_the_login(self):
        cdir = os.path.join(self.d, "creds")
        os.mkdir(cdir)
        for name, text in (("mios.portal.password", OP_PASS), ("mios.portal.user", OP_USER)):
            _envfile(cdir, name, text, 0o400)
        log = self._Log()
        self.assertEqual(mc.portal_login(self.data, log, env={"CREDENTIALS_DIRECTORY": cdir,
                                                              "MIOS_PORTAL_PASSWORD": "fixture-env"}),
                         (OP_PASS, OP_USER))
        self.assertEqual(log.errors, [])

    def test_negative_no_credstore_refuses_every_login(self):
        log = self._Log()
        self.assertEqual(mc.portal_login(self.data, log, env={}), ("", ""),
                         "the Portal fell back to a tier default")
        self.assertTrue(log.errors)
        _envfile(self.d, "install.env", "MIOS_PORTAL_PASSWORD=fixture-leak\nMIOS_PORTAL_USER=fixop\n", 0o644)
        got = mc.portal_login(self.data, self._Log(), env={"MIOS_PORTAL_PASSWORD": "fixture-leak",
                                                           "MIOS_PORTAL_USER": OP_USER})
        self.assertEqual(got, ("", ""), "an install.env projection became the Portal login")


class TestParity(unittest.TestCase):
    """Law 13: root code reading the SSOT in bash (forge-firstboot.sh) reads it through
    `mios-credential get`, the same view `apply` uses -- and for every non-credential key
    that view equals what the native resolver gives the same root caller."""

    KEYS = (("network", "loopback"), ("ports", "forge_http"), ("services.forge", "initial_repo"),
            ("identity", "username"), ("units", "forge"))

    def _cli_get(self, fx, key):
        env = dict(fx.env, MIOS_USER_TOML=fx.path("home-op/.config/mios/mios.toml"), MIOS_RESOLVER_NATIVE="0")
        r = subprocess.run([sys.executable, CRED_CLI, "get", key], env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip()

    def _native(self, fx, sect, key):
        tool = shutil.which("mios-toml-get") or os.path.join(ROOT, "tools/native/target/x86_64-unknown-linux-musl/release/mios-toml-get")
        if not os.access(tool, os.X_OK):
            self.skipTest("native mios-toml-get is not built")
        env = dict(fx.env, MIOS_USER_TOML=fx.path("absent-user.toml"), MIOS_USER_TOML_D=fx.path("absent-user.d"))
        r = subprocess.run([tool, sect, key], env=env, capture_output=True, text=True, timeout=60)
        return r.stdout.strip()

    def test_positive_cli_get_equals_the_native_resolver(self):
        fx = _OpFixture(op_toml=HOSTILE, vendor_extra=VEXTRA)
        try:
            for sect, key in self.KEYS:
                self.assertEqual(self._cli_get(fx, f"{sect}.{key}"), self._native(fx, sect, key), f"{sect}.{key}")
            env = dict(fx.env, MIOS_USER_TOML=fx.path("home-op/.config/mios/mios.toml"))
            r = subprocess.run([sys.executable, CRED_CLI, "get", "identity.default_password"], env=env,
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(r.returncode, 2, "a secret path was printed")
            self.assertNotIn(OP_PASS, r.stdout + r.stderr)
        finally:
            fx.cleanup()

    def test_negative_round2_view_diverges_from_the_native_resolver(self):
        fx = _OpFixture(op_toml=HOSTILE, vendor_extra=VEXTRA)
        try:
            data = fx.view(plant=_round2_view)[0]
            diverged = [f"{s}.{k}" for s, k in self.KEYS
                        if mc._s(mc._sect(data, s).get(k)) != self._native(fx, s, k)]
            self.assertTrue(diverged, "the planted round-2 view agreed with the native resolver")
        finally:
            fx.cleanup()

    def test_forge_firstboot_reads_only_through_the_view(self):
        with open(os.path.join(ROOT, "usr/libexec/mios/forge-firstboot.sh"), encoding="utf-8") as fh:
            s = fh.read()
        self.assertNotIn("mios-toml-get", s)
        for key in ("ports.forge_http", "network.loopback", "services.forge.initial_repo", "units.forge"):
            self.assertIn(f"_get {key}", s)


def _envfile(d: str, name: str, body: str, mode: int = 0o600) -> str:
    p = os.path.join(d, name)
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(body)
    os.chmod(p, mode)
    return p


_LAYERED = {"identity": {"username": OP_USER, "default_password": OP_PASS},
            "portal": {"password": ""}, "ttyd": {"auth_pass": ""}}


class TestSurfaceChain(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="mios-cred-chain-")
        self.addCleanup(shutil.rmtree, self.d, True)

    def _sp(self, surface, data=None, env=None, secrets="", install=""):
        sec = _envfile(self.d, "secrets.env", secrets) if secrets else os.path.join(self.d, "none")
        ins = _envfile(self.d, "install.env", install, 0o640) if install else os.path.join(self.d, "none")
        return mc.surface_password(surface, data or _LAYERED, env=env or {}, secrets_env=sec, install_env=ins)

    def test_positive_tier_order_matches_owui(self):
        both = "MIOS_OPERATOR_PASSWORD=op-override\nMIOS_FORGE_ADMIN_PASSWORD=forge-override\n"
        self.assertEqual(self._sp("forge", secrets=both)["value"], "op-override")
        got = self._sp("forge", secrets="MIOS_FORGE_ADMIN_PASSWORD=forge-override\n")
        self.assertEqual((got["value"], got["tier"], got["shadowed"]),
                         ("forge-override", "MIOS_FORGE_ADMIN_PASSWORD (secrets.env)", True))
        data = {"identity": dict(_LAYERED["identity"]), "portal": {"password": "portal-key"},
                "ttyd": {"auth_pass": "ttyd-key"}}
        self.assertEqual(self._sp("portal", data)["value"], "portal-key")
        self.assertEqual(self._sp("ttyd", data)["value"], "ttyd-key")
        got = self._sp("code_server")
        self.assertEqual((got["value"], got["tier"], got["shadowed"]),
                         (OP_PASS, "[identity].default_password", False))
        self.assertEqual(self._sp("owui", env={"MIOS_OPERATOR_PASSWORD": "__random__"})["value"], "__random__")
        # '__random__' would leave a Forgejo admin password nobody knows: refused.
        for s in ("forge", "portal"):
            with self.assertRaises(mc.CredentialError):
                self._sp(s, env={"MIOS_OPERATOR_PASSWORD": "__random__"})

    def test_negative_install_env_and_rotated_secret_are_not_tiers(self):
        got = self._sp("forge", secrets="MIOS_IDENTITY_DEFAULT_PASSWORD=rotated-random\n",
                       install="MIOS_IDENTITY_DEFAULT_PASSWORD=stale-bridge\n")
        self.assertEqual(got["value"], OP_PASS)
        got = self._sp("forge", env={"MIOS_FORGE_ADMIN_PASSWORD": "leaked"},
                       install="MIOS_FORGE_ADMIN_PASSWORD=leaked\n")
        self.assertEqual(got["value"], OP_PASS)

    def test_negative_unsafe_secrets_env_is_refused(self):
        _envfile(self.d, "loose.env", "MIOS_OPERATOR_PASSWORD=x\n", 0o644)
        with self.assertRaises(mc.CredentialError) as cm:
            mc.surface_password("forge", _LAYERED, env={}, secrets_env=os.path.join(self.d, "loose.env"),
                                install_env=os.path.join(self.d, "none"))
        self.assertIn("0644", str(cm.exception))
        real = _envfile(self.d, "real.env", "MIOS_OPERATOR_PASSWORD=x\n")
        link = os.path.join(self.d, "link.env")
        os.symlink(real, link)
        with self.assertRaises(mc.CredentialError):
            mc.surface_password("forge", _LAYERED, env={}, secrets_env=link,
                                install_env=os.path.join(self.d, "none"))

    def test_balanced_env_quoting_preserves_password_punctuation(self):
        for encoded, expected in (("\"fixture-ending'\"", "fixture-ending'"),
                                  ("'fixture-ending\"'", 'fixture-ending"'),
                                  ("fixture-bare'", "fixture-bare'"),
                                  ('fixture-bare"', 'fixture-bare"'),
                                  ('" fixture-spaces "', ' fixture-spaces ')):
            with self.subTest(encoded=encoded):
                self.assertEqual(self._sp("forge", secrets=f"MIOS_OPERATOR_PASSWORD={encoded}\n")["value"], expected)


# ---------------------------------------------------------------- apply (fake tools + fake Forgejo)
_RECORDER = f"""#!{sys.executable}
import json, os, sys, time
stdin = "" if sys.stdin is None or sys.stdin.isatty() else sys.stdin.read()
with open(os.environ["FAKE_CALLS"], "a") as fh:
    fh.write(json.dumps({{"argv": [os.path.basename(sys.argv[0])] + sys.argv[1:], "stdin": stdin}}) + "\\n")
if "generate-access-token" in sys.argv:
    print("fixture-token-0001")
if "try-restart" in sys.argv and "--no-block" not in sys.argv:
    time.sleep(2)       # a synchronous restart of a unit ordered After= the oneshot blocks
if "try-restart" in sys.argv and os.environ.get("FAKE_RESTART_FAIL"):
    sys.exit(1)
if "--property=ActiveState" in sys.argv:
    print(os.environ.get("FAKE_ACTIVE_STATE", "active"))
"""
_RUN_CLI = """import os, runpy, sys
sys.path.insert(0, os.environ["FAKE_LIB"])
import mios_credentials as mc
mc.CREDSTORE_DIR = os.environ["FAKE_CREDSTORE"]
mc.ROOT_REPO = os.environ["FAKE_ROOT_REPO"]
mc.SHADOW_FILE = os.environ["FAKE_SHADOW"]
if os.environ.get("FAKE_LOCK_FAIL"):
    def _no_lock(_data):
        raise OSError(13, "Permission denied")
    mc.apply_lock = _no_lock
if os.environ.get("FAKE_STATE_PROBE_RAISE"):
    import subprocess
    _run = mc._run
    def _probe(cmd, **kwargs):
        if "--property=ActiveState" in cmd:
            if os.environ["FAKE_STATE_PROBE_RAISE"] == "timeout":
                raise subprocess.TimeoutExpired(cmd, 60)
            raise OSError(5, "Input/output error")
        return _run(cmd, **kwargs)
    mc._run = _probe
if os.environ.get("FAKE_LEDGER_RO") or os.environ.get("FAKE_LEDGER_RECORD_RO"):
    import errno
    _save = mc.Ledger._save
    def _ro(self):
        if os.environ.get("FAKE_LEDGER_RO") or self._load().get("portal"):
            raise OSError(errno.EROFS, "Read-only file system")
        return _save(self)
    mc.Ledger._save = _ro
sys.argv = [os.environ["FAKE_CLI"]] + sys.argv[1:]
runpy.run_path(os.environ["FAKE_CLI"], run_name="__main__")
"""
_ARGON2_STUB = '''class PasswordHasher:
    def hash(self, secret):
        import hashlib
        return "$argon2id$v=19$m=65536,t=3,p=4$Zml4dHVyZXNhbHQ$" + hashlib.sha256(b"salt" + secret.encode()).hexdigest()
'''
V_DEFAULT = "fixture-vendor-default"


def _request(rows: dict, credential) -> str:
    """An inbox request as the Portal writes it, built independently of inbox_submit: sorted
    `dotted = value` rows plus an HMAC-SHA256 over them keyed by the Portal's credential
    (None: no proof row at all)."""
    import hashlib
    import hmac
    lines = [f"{k} = {('true' if v else 'false') if isinstance(v, bool) else json.dumps(v)}"
             for k, v in sorted(rows.items())]
    if credential is not None:
        mac = hmac.new(credential.encode(), "\n".join(lines).encode(), hashlib.sha256).hexdigest()
        lines.append(f'request.proof = "{mac}"')
    return "\n".join(lines) + "\n"


class _FakeForgejo(http.server.BaseHTTPRequestHandler):
    state: dict = {}

    def log_message(self, *a):
        pass

    def _auth(self):
        import base64 as b64
        h = self.headers.get("Authorization", "")
        if h.startswith("Basic "):
            u, _, p = b64.b64decode(h[6:]).decode().partition(":")
            return "basic", u, p
        if h.startswith("token "):
            return "token", h[6:], ""
        return "", "", ""

    def _reply(self, code, body=None):
        raw = json.dumps(body if body is not None else {}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _handle(self):
        st = self.state
        n = int(self.headers.get("Content-Length") or 0)
        body = json.loads(self.rfile.read(n) or b"{}") if n else {}
        st["requests"].append({"method": self.command, "path": self.path})
        if self.path == "/redirect":
            self.send_response(st["redirect_code"])
            self.send_header("Location", f"http://localhost:{self.server.server_port}/landed")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        if self.path == "/landed":
            return self._reply(200)
        kind, who, pw = self._auth()
        basic_ok = kind == "basic" and who == st["user"] and pw == st["password"]
        if self.path == "/api/v1/version":
            return self._reply(200, {"version": "fixture"})
        if self.path == "/api/v1/user":
            return self._reply(200 if basic_ok else 401)
        if self.command == "PATCH" and self.path == f"/api/v1/admin/users/{st['user']}":
            if kind == "token" and who == "fixture-token-0001":
                if st.get("patch_code", 200) != 200:
                    return self._reply(st["patch_code"])
                st["password"] = body.get("password", "")
                st["patches"] += 1
                return self._reply(200)
            return self._reply(401)
        if self.path == f"/api/v1/users/{st['user']}/tokens" and self.command == "GET":
            return self._reply(200, st["tokens"]) if basic_ok else self._reply(401)
        if self.command == "DELETE" and self.path.startswith(f"/api/v1/users/{st['user']}/tokens/"):
            if not basic_ok:
                return self._reply(401)
            tid = self.path.rsplit("/", 1)[1]
            st["tokens"] = [t for t in st["tokens"] if str(t["id"]) != tid]
            return self._reply(204)
        if self.command == "POST" and self.path == "/api/v1/user/repos":
            return self._reply(201 if basic_ok else 401)
        return self._reply(404)

    do_GET = do_PATCH = do_DELETE = do_POST = _handle


class TestApply(unittest.TestCase):
    """mios-credential apply against fake podman/systemctl/chpasswd (an otherwise empty PATH)
    and a threaded fake Forgejo. The secret must reach each surface and nowhere else; the
    credential is the operator tier's (u.toml), the vendor default is never applied."""

    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="mios-cred-apply-")
        self.addCleanup(shutil.rmtree, self.d, True)
        self.bin = os.path.join(self.d, "bin")
        os.mkdir(self.bin)
        for tool in ("podman", "systemctl", "chpasswd"):
            p = os.path.join(self.bin, tool)
            with open(p, "w") as fh:
                fh.write(_RECORDER)
            os.chmod(p, 0o755)
        for tool in ("python3", "openssl", "sh", "git"):
            real = shutil.which(tool)
            if real:
                os.symlink(real, os.path.join(self.bin, tool))
        self.runner = os.path.join(self.d, "run-cli.py")
        with open(self.runner, "w") as fh:
            fh.write(_RUN_CLI)
        self.stub = os.path.join(self.d, "argon2-stub")
        os.makedirs(os.path.join(self.stub, "argon2"))
        with open(os.path.join(self.stub, "argon2", "__init__.py"), "w") as fh:
            fh.write(_ARGON2_STUB)
        self.nokdf = os.path.join(self.d, "no-argon2")      # shadows cryptography: no Argon2id
        os.makedirs(os.path.join(self.nokdf, "cryptography"))
        with open(os.path.join(self.nokdf, "cryptography", "__init__.py"), "w") as fh:
            fh.write("")
        for sub in ("inbox", "root-repo", "libexec"):
            os.mkdir(os.path.join(self.d, sub))
        self.calls = os.path.join(self.d, "calls.jsonl")
        _FakeForgejo.state = {"user": OP_USER, "password": "fixture-old-pass", "patches": 0, "requests": [],
                              "tokens": [{"id": 7, "name": "mios-credential-apply-111"}, {"id": 8, "name": "laptop"}]}
        self.srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _FakeForgejo)
        threading.Thread(target=self.srv.serve_forever, daemon=True).start()
        self.addCleanup(self.srv.server_close)
        self.addCleanup(self.srv.shutdown)
        self.agents = os.path.join(self.d, "agents")
        os.mkdir(self.agents)
        self.surfaces = '["portal", "forge", "code_server", "ttyd", "shadow"]'
        self.backoff = 300

    def _vendor(self) -> str:
        d = self.d
        return f'''[identity]
username = "{OP_USER}"
default_password = "{V_DEFAULT}"
{OPTIER}[paths]
etc_dir = "{d}"
var_dir = "{d}/var"
install_env = "{d}/install.env"
var_credential_apply_dir = "${{MIOS_VAR_DIR}}/ledger"
var_agents_dir = "{self.agents}"
libexec_dir = "{d}/libexec"
run_credential_inbox_dir = "{d}/inbox"
[network]
loopback = "127.0.0.1"
[ports]
forge_http = {self.srv.server_address[1]}
[units]
forge = "mios-forge.service"
agent_pipe = "mios-agent-pipe.service"
code_server = "mios-agents.service"
ttyd_bash = "mios-ttyd-bash.service"
ttyd_powershell = "mios-ttyd-powershell.service"
[services.forge]
ready_timeout_s = 5
initial_repo = "mios"
home = "/data/gitea"
app_ini = "/data/gitea/conf/app.ini"
[security.credential_apply]
surfaces = {self.surfaces}
retry_backoff_s = {self.backoff}
retry_backoff_max_s = 86400
[tailscale.serve]
enabled = false
'''

    def _apply(self, *args, password=OP_PASS, operator=None, argon2=True, extra=None, popen=False, edited=False):
        vendor = _envfile(self.d, "vendor.toml", self._vendor(), 0o644)
        user = os.path.join(self.d, "u.toml")
        want = operator if operator is not None else (
            f'[identity]\ndefault_password = "{password}"\n' if password is not None else None)
        if want is None:
            if os.path.exists(user):
                os.unlink(user)
        elif not os.path.exists(user) or open(user, encoding="utf-8").read() != want:
            _envfile(self.d, "u.toml", want)
            if not edited:      # set up as an old file: only edited=True is a hand edit after queued saves
                os.utime(user, ns=(10**18, 10**18))
        env = _clean_env({"MIOS_VENDOR_TOML": vendor, "MIOS_VENDOR_TOML_D": os.path.join(self.d, "v.d"),
                          "MIOS_HOST_TOML": os.path.join(self.d, "h.toml"),
                          "MIOS_HOST_TOML_D": os.path.join(self.d, "h.d"),
                          "MIOS_USER_TOML": user, "MIOS_USER_TOML_D": os.path.join(self.d, "u.d"),
                          "MIOS_RESOLVER_NATIVE": "0", "PATH": self.bin, "FAKE_CALLS": self.calls,
                          "FAKE_LIB": LIB, "FAKE_CLI": CRED_CLI, "FAKE_CREDSTORE": os.path.join(self.d, "credstore"),
                          "FAKE_ROOT_REPO": os.path.join(self.d, "root-repo"),
                          "FAKE_SHADOW": os.path.join(self.d, "shadow"),
                          "PYTHONPATH": {True: self.stub, False: "", "none": self.nokdf}[argon2]})
        env.update(extra or {})
        if popen:
            return subprocess.Popen([sys.executable, self.runner, "apply", *args], env=env,
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return subprocess.run([sys.executable, self.runner, "apply", *args], env=env,
                              capture_output=True, text=True, timeout=120)

    def _recorded(self):
        if not os.path.exists(self.calls):
            return []
        with open(self.calls) as fh:
            return [json.loads(line) for line in fh]

    def _mints(self):
        return len([c for c in self._recorded() if "generate-access-token" in c["argv"]])

    def _assert_private(self, r, secret=OP_PASS):
        for c in self._recorded():
            self.assertNotIn(secret, " ".join(c["argv"]), "secret on argv")
            if c["argv"][0] != "chpasswd":
                self.assertNotIn(secret, c["stdin"], "secret on a tool's stdin")
        self.assertNotIn(secret, r.stdout + r.stderr, "secret in the reconciler's output")
        for q in _FakeForgejo.state["requests"]:
            self.assertNotIn(secret, q["path"], "secret in a URL")

    def test_credential_http_never_follows_redirects(self):
        base = f"http://127.0.0.1:{self.srv.server_port}"
        for status in (301, 302, 303, 307, 308):
            for method, credentials in (("GET", {"basic": (OP_USER, OP_PASS)}),
                                        ("POST", {"token": "fixture-admin-token", "body": {"password": OP_PASS}})):
                with self.subTest(status=status, method=method):
                    _FakeForgejo.state["redirect_code"] = status
                    _FakeForgejo.state["requests"] = []
                    self.assertEqual(mc._http(method, base + "/redirect", **credentials), (status, None))
                    self.assertEqual(_FakeForgejo.state["requests"], [{"method": method, "path": "/redirect"}],
                                     "the redirected server received a credential-bearing request")
        # Positive control: ordinary direct HTTP still reaches the endpoint.
        self.assertEqual(mc._http("GET", base + "/api/v1/version")[0], 200)

    def test_positive_forge_patch_sweep_then_noop_then_verify_repairs(self):
        r = self._apply("forge")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("forge: changed", r.stderr)
        self.assertEqual(_FakeForgejo.state["password"], OP_PASS)
        self.assertEqual(self._mints(), 1)
        self.assertEqual([t["name"] for t in _FakeForgejo.state["tokens"]], ["laptop"],
                         "a leftover mios-credential-apply-* token survived, or another token was revoked")
        self._assert_private(r)
        n = len(self._recorded())
        r = self._apply("forge")
        self.assertIn("forge: unchanged", r.stderr)
        self.assertEqual(len(self._recorded()), n, "an unchanged credential re-ran the forge CLI")
        _FakeForgejo.state["password"] = "fixture-recreated-db"        # the forge DB was recreated
        r = self._apply("--verify", "forge")
        self.assertIn("forge: changed", r.stderr)
        self.assertEqual((_FakeForgejo.state["password"], _FakeForgejo.state["patches"]), (OP_PASS, 2))
        self._assert_private(r)

    def test_negative_forge_failure_backs_off_instead_of_minting_again(self):
        _FakeForgejo.state["patch_code"] = 422                         # e.g. below MIN_PASSWORD_LENGTH
        r = self._apply("forge")
        self.assertIn("forge: failed (admin PATCH returned HTTP 422", r.stderr)
        r = self._apply("forge")
        self.assertIn("retried after", r.stderr)
        self.assertEqual(self._mints(), 1, "a failed value minted another admin token on the next run")
        _FakeForgejo.state["patch_code"] = 200
        r = self._apply("forge", password="fixture-changed-in-mios-html")   # a new value: no backoff
        self.assertIn("forge: changed", r.stderr)
        self.assertIn("revoked", r.stderr)

    def test_negative_forge_refuses_random(self):
        r = self._apply("forge", password="__random__")
        self.assertIn("forge: failed", r.stderr)
        self.assertIn("honoured only by owui", r.stderr)
        self.assertEqual((self._mints(), _FakeForgejo.state["patches"]), (0, 0))

    def test_positive_code_server_argon2_0600_as_the_owner(self):
        cfg_dir = os.path.join(self.agents, ".config", "code-server")
        os.makedirs(cfg_dir)
        cfg = os.path.join(cfg_dir, "config.yaml")
        with open(cfg, "w") as fh:
            fh.write("bind-addr: 127.0.0.1:8080\nauth: none\npassword: fixture-plaintext\ncert: false\n")
        r = self._apply("code_server")
        self.assertEqual(r.returncode, 0, r.stderr)
        st = os.stat(cfg)
        self.assertEqual(stat.S_IMODE(st.st_mode), 0o600)
        self.assertEqual(st.st_uid, os.stat(self.agents).st_uid)
        with open(cfg) as fh:
            text = fh.read()
        for want in ("bind-addr: 127.0.0.1:8080", "cert: false", "auth: password", 'hashed-password: "$argon2id$'):
            self.assertIn(want, text)
        self.assertNotIn("fixture-plaintext", text)
        self.assertNotIn(OP_PASS, text)
        self.assertIn(["systemctl", "--no-block", "try-restart", "mios-agents.service"],
                      [c["argv"] for c in self._recorded()])
        mtime = st.st_mtime_ns
        r = self._apply("code_server")
        self.assertIn("code_server: unchanged", r.stderr)
        self.assertEqual(os.stat(cfg).st_mtime_ns, mtime)
        self._assert_private(r)

    def test_code_server_rejects_auth_and_exact_hash_drift_after_apply(self):
        self.assertEqual(self._apply("code_server").returncode, 0)
        cfg = os.path.join(self.agents, ".config", "code-server", "config.yaml")
        original = open(cfg, encoding="utf-8").read()
        for bad in (original.replace("auth: password", "auth: none"),
                    re.sub(r'(?m)^hashed-password:.*$', 'hashed-password: "fixture-other-hash"', original),
                    original + "password: fixture-extra-password\n"):
            with self.subTest(config=bad):
                _envfile(os.path.dirname(cfg), "config.yaml", bad)
                result = self._apply("--check", "code_server")
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("code_server: drift", result.stderr)
                self.assertEqual(open(cfg, encoding="utf-8").read(), bad, "check repaired the config")
                result = self._apply("code_server")
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("code_server: changed", result.stderr)
                self.assertEqual(open(cfg, encoding="utf-8").read(), original)

    def test_positive_code_server_argon2id_from_the_images_cryptography(self):
        try:
            from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
        except ImportError:
            self.skipTest("python3-cryptography without Argon2id")
        import base64
        r = self._apply("code_server", argon2=False)        # no argon2-cffi: the image's cryptography
        self.assertEqual(r.returncode, 0, r.stderr)
        with open(os.path.join(self.agents, ".config", "code-server", "config.yaml")) as fh:
            m = re.search(r'^hashed-password: "\$argon2id\$v=19\$m=(\d+),t=(\d+),p=(\d+)\$([A-Za-z0-9+/]+)'
                          r'\$([A-Za-z0-9+/]+)"$', fh.read(), re.M)
        self.assertTrue(m, "not an argon2id PHC string")
        pad = lambda t: base64.b64decode(t + "=" * (-len(t) % 4))  # noqa: E731
        salt, want = pad(m.group(4)), pad(m.group(5))
        self.assertGreaterEqual(len(salt), 16, "no real salt")
        got = Argon2id(salt=salt, length=len(want), iterations=int(m.group(2)), lanes=int(m.group(3)),
                       memory_cost=int(m.group(1))).derive(OP_PASS.encode())
        self.assertEqual(got, want, "the PHC string does not verify the operator credential")
        self._assert_private(r)

    def test_negative_code_server_never_writes_an_unsalted_hash(self):
        r = self._apply("code_server", argon2="none")       # neither argon2-cffi nor Argon2id
        self.assertIn("code_server: failed (no argon2 implementation", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.agents, ".config", "code-server", "config.yaml")))

    def test_negative_code_server_refuses_links_in_the_ide_home(self):
        secret = _envfile(self.d, "root-only.txt", "fixture-root-only-line\n", 0o600)
        cfg_dir = os.path.join(self.agents, ".config", "code-server")
        os.makedirs(cfg_dir)
        os.symlink(secret, os.path.join(cfg_dir, "config.yaml"))
        r = self._apply("code_server")
        self.assertIn("code_server: failed (refusing", r.stderr)
        self.assertTrue(os.path.islink(os.path.join(cfg_dir, "config.yaml")), "the link was replaced")
        with open(secret) as fh:
            self.assertEqual(fh.read(), "fixture-root-only-line\n")
        shutil.rmtree(os.path.join(self.agents, ".config"))
        os.symlink(self.d, os.path.join(self.agents, ".config"))       # a symlinked .config
        r = self._apply("code_server")
        self.assertIn("code_server: failed (refusing", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.d, "code-server")))

    def test_negative_code_server_fifo_never_blocks_the_run(self):
        cfg_dir = os.path.join(self.agents, ".config", "code-server")
        os.makedirs(cfg_dir)
        fifo = os.path.join(cfg_dir, "config.yaml")
        os.mkfifo(fifo)
        r = self._apply("code_server", "portal")           # the AI agents' home holds a FIFO
        self.assertIn("code_server: failed (refusing", r.stderr)
        self.assertIn("portal: changed", r.stderr, "a FIFO in the IDE home stopped the later surfaces")
        self.assertTrue(stat.S_ISFIFO(os.lstat(fifo).st_mode))
        # Negative control: the round-3 open (no O_NONBLOCK) blocks on that FIFO.
        dfd = os.open(cfg_dir, os.O_RDONLY | os.O_DIRECTORY)
        done = threading.Event()

        def blocking_open():
            fd = os.open("config.yaml", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dfd)   # PLANT
            os.close(fd)
            done.set()
        t = threading.Thread(target=blocking_open, daemon=True)
        t.start()
        blocked = not done.wait(1.5)
        os.close(os.open(fifo, os.O_WRONLY | os.O_NONBLOCK))   # release the planted reader
        t.join(5)
        os.close(dfd)
        self.assertTrue(blocked, "the planted blocking open returned: this control no longer proves O_NONBLOCK")

    def test_inbox_entries_that_are_not_files_never_stop_apply(self):
        inbox = os.path.join(self.d, "inbox")
        self._apply("portal")                                   # the Portal accepts OP_PASS
        os.mkdir(os.path.join(inbox, "0001-1.toml"))            # group mios-ai can mkdir; sticky: only root removes
        os.mkfifo(os.path.join(inbox, "0002-1.toml"))
        os.mkdir(os.path.join(inbox, "0003-1.toml"))
        _envfile(os.path.join(inbox, "0003-1.toml"), "x", "")
        _envfile(inbox, "0004-1.toml", _request({"identity.default_password": "fixture-after-plants"}, OP_PASS))
        r = self._apply("portal", "ttyd", password=None, operator=f'[identity]\ndefault_password = "{OP_PASS}"\n')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertNotIn("Traceback", r.stderr)
        for name in ("0001-1.toml", "0002-1.toml", "0003-1.toml"):
            self.assertIn(f"inbox: {name}: refused", r.stderr)
        self.assertIn("inbox: 0004-1.toml: merged identity.default_password", r.stderr)
        self.assertIn("portal: changed", r.stderr)
        self.assertEqual(sorted(os.listdir(inbox)), ["0003-1.toml"], "an empty dir or a FIFO was left queued")
        with open(os.path.join(self.d, "credstore", "mios.portal.password")) as fh:
            self.assertEqual(fh.read(), "fixture-after-plants")

    def test_negative_refused_operator_credential_writes_nothing(self):
        for row in ("default_password = 12345678", 'default_password = "pw\\twith-tab"'):
            r = self._apply("--all", operator=f"[identity]\n{row}\n")
            self.assertEqual(r.returncode, 3, r.stderr)
            self.assertIn("identity.default_password is refused", r.stderr)
            self.assertNotIn("12345678", r.stdout + r.stderr)
            self.assertFalse(os.path.exists(os.path.join(self.d, "credstore")), "a refused value wrote a surface")
            self.assertEqual(self._recorded(), [], "a refused value reached a tool")
            self.assertEqual(_FakeForgejo.state["patches"], 0)
        r = self._apply("portal", operator='[identity]\ndefault_password = "fixture-a-good-value"\n')
        self.assertEqual(r.returncode, 0, r.stderr)           # control: a valid string still applies

    def test_ledger_write_failure_is_reported_for_its_surface(self):
        r = self._apply("portal", extra={"FAKE_LEDGER_RECORD_RO": "1"})
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertNotIn("Traceback", r.stderr, "a read-only ledger crashed the reconciler")
        self.assertIn("portal: changed", r.stderr)
        self.assertIn("portal: the ledger was not written", r.stderr)
        with open(os.path.join(self.d, "credstore", "mios.portal.password")) as fh:
            self.assertEqual(fh.read(), OP_PASS)

    def test_restart_intent_must_be_durable_before_changing_credentials(self):
        r = self._apply("portal", extra={"FAKE_LEDGER_RO": "1"})
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.d, "credstore")), "credentials changed without durable activation intent")
        self.assertEqual(self._recorded(), [])

    def test_restart_enqueue_failure_is_retried_after_files_already_match(self):
        targets = {"portal": os.path.join(self.d, "credstore", "mios.portal.password"),
                   "code_server": os.path.join(self.agents, ".config", "code-server", "config.yaml"),
                   "ttyd": os.path.join(self.d, "credstore", "mios.ttyd.password")}
        expected = {"portal": ["mios-agent-pipe.service"], "code_server": ["mios-agents.service"],
                    "ttyd": ["mios-ttyd-bash.service", "mios-ttyd-powershell.service"]}
        state_file = os.path.join(self.d, "var", "ledger", "state.json")
        for surface, path in targets.items():
            with self.subTest(surface=surface):
                result = self._apply(surface, extra={"FAKE_RESTART_FAIL": "1"})
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertIn("consumer activation pending", result.stderr)
                with open(state_file, encoding="utf-8") as fh:
                    state = json.load(fh)
                self.assertEqual(state["_restart_pending"][surface], expected[surface])
                self.assertNotIn(surface, state, "a failed enqueue was recorded as fully applied")
                before = (open(path, "rb").read(), os.stat(path).st_mtime_ns)
                calls = len(self._recorded())
                result = self._apply(surface)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"{surface}: unchanged", result.stderr)
                self.assertEqual((open(path, "rb").read(), os.stat(path).st_mtime_ns), before)
                retries = [c["argv"][-1] for c in self._recorded()[calls:] if "try-restart" in c["argv"]]
                self.assertEqual(retries, expected[surface])
                with open(state_file, encoding="utf-8") as fh:
                    state = json.load(fh)
                self.assertEqual(state["_restart_pending"][surface], [])
                self.assertIn(surface, state)

    def test_no_restart_keeps_active_agents_and_durably_reports_activation(self):
        result = self._apply("--no-restart", "portal", "code_server", "ttyd")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--no-restart; active sessions kept", result.stderr)
        self.assertFalse([c for c in self._recorded() if "try-restart" in c["argv"]])
        state_file = os.path.join(self.d, "var", "ledger", "state.json")
        with open(state_file, encoding="utf-8") as fh:
            state = json.load(fh)
        self.assertEqual(state["_restart_pending"]["code_server"], ["mios-agents.service"])
        before = open(state_file, "rb").read()
        result = self._apply("--check", "portal", "code_server", "ttyd")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("consumer activation pending (--check; no change)", result.stderr)
        self.assertEqual(open(state_file, "rb").read(), before)
        # A unit's own pre-start hook must not fail or restart itself.
        result = self._apply("--no-restart", "code_server", extra={"FAKE_ACTIVE_STATE": "activating"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("code_server: unchanged", result.stderr)
        self.assertFalse([c for c in self._recorded() if "try-restart" in c["argv"]])
        calls = len(self._recorded())
        result = self._apply("portal", "code_server", "ttyd")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len([c for c in self._recorded()[calls:] if "try-restart" in c["argv"]]), 4)

    def test_no_restart_prepares_inactive_consumers_for_their_next_start(self):
        result = self._apply("--no-restart", "portal", "code_server", "ttyd", extra={"FAKE_ACTIVE_STATE": "inactive"})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("consumer activation pending", result.stderr)
        self.assertFalse([c for c in self._recorded() if "try-restart" in c["argv"]])
        with open(os.path.join(self.d, "var", "ledger", "state.json"), encoding="utf-8") as fh:
            self.assertFalse(any((json.load(fh).get("_restart_pending") or {}).values()))

    def test_no_restart_probe_exceptions_leave_unknown_consumers_pending(self):
        for failure in ("timeout", "oserror"):
            with self.subTest(failure=failure):
                result = self._apply("--no-restart", "portal", "code_server", "ttyd",
                                     extra={"FAKE_STATE_PROBE_RAISE": failure})
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("consumer activation pending", result.stderr)
                self.assertFalse([c for c in self._recorded() if "try-restart" in c["argv"]])
                with open(os.path.join(self.d, "var", "ledger", "state.json"), encoding="utf-8") as fh:
                    self.assertEqual(json.load(fh)["_restart_pending"]["code_server"], ["mios-agents.service"])
                with open(os.path.join(self.d, "credstore", "mios.portal.password"), encoding="utf-8") as fh:
                    self.assertEqual(fh.read(), OP_PASS)

    def test_concurrent_applies_are_serialized(self):
        import fcntl
        import time
        self._apply("portal")
        fd = os.open(os.path.join(self.d, "var", "ledger", "lock"), os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX)                          # another apply holds the lock
        try:
            p = self._apply("portal", password="fixture-changed-while-locked", popen=True)
            time.sleep(1.5)
            self.assertIsNone(p.poll(), "an apply ran while another held the lock")
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        out, err = p.communicate(timeout=60)
        self.assertEqual(p.returncode, 0, err)
        self.assertIn("portal: changed", err)

    def test_owui_verify_rechecks_an_applied_admin(self):
        self.surfaces = '["owui"]'
        seen = os.path.join(self.d, "owui-argv")
        _envfile(os.path.join(self.d, "libexec"), "mios-owui-bootstrap-admin",
                 f'#!/bin/sh\necho "$*" >> {seen}\n'
                 f'[ -e {self.d}/db-recreated ] && echo "admin user created (role=admin)" >&2\nexit 0\n', 0o755)
        argv = lambda: open(seen).read().splitlines() if os.path.exists(seen) else []  # noqa: E731
        self.assertIn("owui: changed", self._apply("owui").stderr)
        self.assertEqual(argv(), [""])
        self.assertIn("owui: unchanged", self._apply("owui").stderr)
        self.assertEqual(argv(), [""], "an unchanged credential re-ran the bootstrap without --verify")
        r = self._apply("--verify", "owui")
        self.assertIn("owui: unchanged", r.stderr)
        self.assertEqual(argv(), ["", "--verify"], "--verify never re-checked the applied admin")
        open(os.path.join(self.d, "db-recreated"), "w").close()   # the OWUI database was recreated
        r = self._apply("--verify", "owui")
        self.assertIn("owui: --verify re-created the admin", r.stderr)

    def _ttyd_pw(self):
        with open(os.path.join(self.d, "credstore", "mios.ttyd.password")) as fh:
            return fh.read()

    def test_ttyd_gets_its_own_login_never_the_operator_credential(self):
        # The host tier carries the operator credential: ttyd still takes no shared one.
        _envfile(self.d, "h.toml", f'[identity]\ndefault_password = "{OP_PASS}"\n', 0o644)
        r = self._apply("ttyd")                                 # the operator saved OP_PASS
        self.assertEqual(r.returncode, 0, r.stderr)
        store = os.path.join(self.d, "credstore")
        gen = self._ttyd_pw()
        self.assertNotIn(gen, (OP_PASS, V_DEFAULT, ""), "ttyd took a shared credential")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(store, "mios.ttyd.password")).st_mode), 0o400)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.d, "var", "ledger", "ttyd.generated")).st_mode), 0o600)
        _envfile(self.d, "secrets.env", "MIOS_OPERATOR_PASSWORD=fixture-secrets-operator\n")
        r = self._apply("ttyd")
        self.assertIn("ttyd: unchanged", r.stderr)            # generated once, then kept
        self.assertEqual(self._ttyd_pw(), gen, "MIOS_OPERATOR_PASSWORD reached ttyd, or the login was regenerated")
        r = self._apply("ttyd", operator=f'[identity]\ndefault_password = "{OP_PASS}"\n[ttyd]\nauth_pass = "fixture-ttyd-own"\n')
        self.assertIn("ttyd: changed", r.stderr)               # the operator gave ttyd its own value
        self.assertEqual(self._ttyd_pw(), "fixture-ttyd-own")
        self.assertIn(["systemctl", "--no-block", "try-restart", "mios-ttyd-bash.service"],
                      [c["argv"] for c in self._recorded()])
        # A ttyd login equal to any shared credential is refused and the applied one withdrawn.
        for same in (OP_PASS, V_DEFAULT, "fixture-secrets-operator"):
            r = self._apply("ttyd", operator=f'[identity]\ndefault_password = "{OP_PASS}"\n[ttyd]\nauth_pass = "{same}"\n')
            self.assertEqual(r.returncode, 1, r.stderr)
            self.assertIn("ttyd: failed (ttyd's own login equals a credential another surface uses", r.stderr)
            self.assertFalse(os.path.exists(os.path.join(store, "mios.ttyd.password")), "the shared value stayed applied")
            self.assertNotIn(same, r.stdout + r.stderr)

    def test_a_ttyd_login_that_becomes_the_operator_credential_takes_the_running_ttyd_down(self):
        own = "fixture-ttyd-own"
        r = self._apply("ttyd", operator=f'[identity]\ndefault_password = "{OP_PASS}"\n[ttyd]\nauth_pass = "{own}"\n')
        self.assertIn("ttyd: changed", r.stderr)               # ttyd now runs with `-c user:own` on argv
        ttyd = ("mios-ttyd-bash.service", "mios-ttyd-powershell.service")

        def restarts(since):
            return [c["argv"][-1] for c in self._recorded()[since:]
                    if c["argv"][:3] == ["systemctl", "--no-block", "try-restart"] and c["argv"][-1] in ttyd]
        before = len(self._recorded())
        made_shared = f'[identity]\ndefault_password = "{own}"\n[ttyd]\nauth_pass = "{own}"\n'
        r = self._apply("ttyd", operator=made_shared)       # the operator credential becomes ttyd's login
        self.assertIn("ttyd: failed (ttyd's own login equals a credential another surface uses", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.d, "credstore", "mios.ttyd.password")))
        self.assertEqual(sorted(restarts(before)), sorted(ttyd), "the running ttyd kept the withdrawn login on argv")
        self.assertNotIn(own, r.stdout + r.stderr)
        before = len(self._recorded())
        self._apply("ttyd", operator=made_shared)           # nothing left to withdraw: no restart again
        self.assertEqual(restarts(before), [])
        # Withdrawal remains pending after --no-restart or an unsuccessful enqueue,
        # even though a later apply has no credential file left to remove.
        for deferred in (True, False):
            with self.subTest(deferred=deferred):
                r = self._apply("ttyd", operator=f'[identity]\ndefault_password = "{OP_PASS}"\n[ttyd]\nauth_pass = "{own}"\n')
                self.assertEqual(r.returncode, 0, r.stderr)
                before = len(self._recorded())
                r = self._apply(*(("--no-restart",) if deferred else ()), "ttyd", operator=made_shared,
                                extra={} if deferred else {"FAKE_RESTART_FAIL": "1"})
                self.assertEqual(r.returncode, 1, r.stderr)
                self.assertFalse(os.path.exists(os.path.join(self.d, "credstore", "mios.ttyd.password")))
                self.assertEqual(sorted(restarts(before)), [] if deferred else sorted(ttyd))
                with open(os.path.join(self.d, "var", "ledger", "state.json"), encoding="utf-8") as fh:
                    self.assertEqual(sorted(json.load(fh)["_restart_pending"]["ttyd"]), sorted(ttyd))
                before = len(self._recorded())
                r = self._apply("ttyd", operator=made_shared)
                self.assertEqual(r.returncode, 1, r.stderr)  # the colliding login is still refused
                self.assertEqual(sorted(restarts(before)), sorted(ttyd))
                before = len(self._recorded())
                self._apply("ttyd", operator=made_shared)
                self.assertEqual(restarts(before), [])

    def test_clearing_the_operator_credential_never_downgrades_a_surface(self):
        r = self._apply("portal", "forge")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_FakeForgejo.state["password"], OP_PASS)
        for kind, kw in (("removed tier", {"password": None}),
                         ("emptied credential", {"operator": '[identity]\ndefault_password = ""\n'})):
            r = self._apply("portal", "forge", **kw)
            self.assertEqual(r.returncode, 3, f"{kind}: {r.stderr}")
            self.assertIn("refusing to move portal, forge from the operator's credential back to the vendor/host "
                          "default", r.stderr, kind)
            self.assertEqual(self._portal_pw(), OP_PASS, f"{kind}: the Portal moved to the vendor default")
            self.assertEqual(_FakeForgejo.state["password"], OP_PASS, f"{kind}: Forgejo moved to the vendor default")
            self.assertNotIn(V_DEFAULT, r.stdout + r.stderr)
        r = self._apply("portal", "forge", password="fixture-next-operator")      # a NEW credential still moves
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self._portal_pw(), "fixture-next-operator")

    def _portal_pw(self):
        with open(os.path.join(self.d, "credstore", "mios.portal.password")) as fh:
            return fh.read()

    def test_forge_keeps_its_generated_password_until_the_operator_sets_one(self):
        r = self._apply("--bootstrap", "forge", password=None)       # a fresh host, no operator credential
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("forge: skipped (no operator-set credential: the Forgejo admin keeps its own password)", r.stderr)
        self.assertEqual(_FakeForgejo.state["password"], "fixture-old-pass", "Forgejo moved to the vendor default")
        self.assertEqual(_FakeForgejo.state["patches"], 0)
        self.assertNotIn("/api/v1/user/repos", [q["path"] for q in _FakeForgejo.state["requests"]])
        r = self._apply("forge")                           # the operator sets one: the timer's plain run
        self.assertIn("forge: changed", r.stderr)
        self.assertEqual(_FakeForgejo.state["password"], OP_PASS)
        self.assertIn("forge: initial repo mios: created", r.stderr, "the first value did not bootstrap the repo")

    def test_installer_records_the_typed_password_as_the_operator_credential(self):
        # D0: the installer records the typed password in the operator's tier; forge follows it.
        trees = [ROOT] + ([BOOT] if os.path.isfile(os.path.join(BOOT, "installation", "mios-install.sh")) else [])
        typed = 'fixture "typed" \\ pw'
        for tree in trees:
            with open(os.path.join(tree, "installation", "mios-install.sh"), encoding="utf-8") as fh:
                s = fh.read()
            m = re.search(r"(?ms)^write_operator_credential\(\) \{\n.*?^\}\n", s)
            self.assertTrue(m, f"{tree}: no write_operator_credential")
            body = re.search(r"(?ms)^stage_user_profile_artifacts\(\) \{\n.*?^\}\n", s).group(0)
            self.assertGreater(body.find('write_operator_credential "${LINUX_USER}"'),
                               body.rfind("seed_user_skel_for_all_accounts"), f"{tree}: a later skel copy erases it")
            home = os.path.join(self.d, f"home-{len(tree)}")
            os.makedirs(os.path.join(home, ".config", "mios"))
            with open(os.path.join(ROOT, "etc", "skel", ".config", "mios", "mios.toml"), encoding="utf-8") as fh:
                skel = fh.read()
            _envfile(os.path.join(home, ".config", "mios"), "mios.toml", skel, 0o644)
            stub = ('log_ok() { :; }\nlog_err() { echo "$*" >&2; }\nlog_warn() { :; }\n'
                    'getent() { echo "fixop:x:1000:1000::$HOME_DIR:/bin/bash"; }\nsudo() { shift 2; "$@"; }\n')
            for pw, rc in ((typed, 0), ("fixture\nnewline", 1), ("", 1)):
                r = subprocess.run(["bash", "-c", stub + m.group(0) + 'USER_PASSWORD="$1"; write_operator_credential fixop', "_", pw],
                                   env=dict(os.environ, HOME_DIR=home), capture_output=True, text=True, timeout=30)
                self.assertEqual(r.returncode, rc, f"{tree}: {pw!r}: {r.stderr}")
            path = os.path.join(home, ".config", "mios", "mios.toml")
            import tomllib
            with open(path, "rb") as fh:
                got = tomllib.load(fh)
            self.assertEqual(got["identity"], {"fullname": "MiOS User", "default_password": typed}, tree)
            self.assertEqual({k: v for k, v in got.items() if k != "identity"},
                             {k: v for k, v in tomllib.loads(skel).items() if k != "identity"}, f"{tree}: the skel tier changed")
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)
            self.assertNotIn(typed, m.group(0))
        with open(path, encoding="utf-8") as fh:
            tier = fh.read()
        r = self._apply("forge", operator=tier)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(_FakeForgejo.state["password"], typed, "forge did not follow the installer's credential")

    def _tier(self):
        with open(os.path.join(self.d, "u.toml"), encoding="utf-8") as fh:
            return fh.read()

    def test_a_portal_save_older_than_a_hand_edit_never_overwrites_it(self):
        inbox = os.path.join(self.d, "inbox")
        self._apply("portal")                                  # the Portal accepts OP_PASS
        req = _envfile(inbox, "0001-1.toml", _request({"identity.default_password": "fixture-portal-save"}, OP_PASS))
        os.utime(req, ns=(10**18 + 5, 10**18 + 5))            # queued, THEN the operator edits by hand
        r = self._apply("portal", password="fixture-hand-edit", edited=True)
        self.assertIn("inbox: 0001-1.toml: dropped (queued before the operator tier was last edited by hand", r.stderr)
        self.assertEqual(self._portal_pw(), "fixture-hand-edit")
        req = _envfile(inbox, "0002-1.toml", _request({"identity.default_password": "fixture-portal-save"}, "fixture-hand-edit"))
        r = self._apply("portal", operator=self._tier())       # no edit since: the newer save is merged
        self.assertIn("inbox: 0002-1.toml: merged identity.default_password", r.stderr)
        self.assertEqual(self._portal_pw(), "fixture-portal-save")
        req = _envfile(inbox, "0003-1.toml", _request({"identity.default_password": "fixture-portal-2"}, "fixture-portal-save"))
        os.utime(req, ns=(10**18 + 6, 10**18 + 6))            # older than the reconciler's OWN merge: still taken
        r = self._apply("portal", operator=self._tier())
        self.assertIn("inbox: 0003-1.toml: merged identity.default_password", r.stderr)

    def test_exec_ttyd_reads_only_the_credstore_and_no_ps_shows_the_operator_credential(self):
        _envfile(self.d, "h.toml", f'[identity]\ndefault_password = "{OP_PASS}"\n', 0o644)   # host tier = operator's
        self._apply("ttyd")
        store = os.path.join(self.d, "credstore")
        gen = self._ttyd_pw()
        fake = os.path.join(self.bin, "ttyd")                  # a stand-in ttyd that sleeps
        _envfile(self.bin, "ttyd", f"#!{sys.executable}\nimport time\ntime.sleep(30)\n", 0o755)
        env = _clean_env({"PATH": self.bin, "CREDENTIALS_DIRECTORY": store,
                          "MIOS_OPERATOR_PASSWORD": OP_PASS, "PYTHONPATH": LIB})
        p = subprocess.Popen([sys.executable, CRED_CLI, "exec", "ttyd", "--", "-p", "1"], env=env)
        try:
            import time
            cmdline = ""
            for _ in range(50):
                with open(f"/proc/{p.pid}/cmdline", "rb") as fh:
                    cmdline = fh.read().replace(b"\0", b" ").decode()
                if fake in cmdline:
                    break
                time.sleep(0.1)
            self.assertIn(f"-c {OP_USER}:{gen} -p 1", cmdline, "ttyd did not get the credstore login")
            seen = cmdline
            if os.geteuid() == 0:   # what ANOTHER local uid reads in /proc (ps as nobody)
                seen = subprocess.run([sys.executable, "-c", "import os,sys; os.setgid(65534); os.setuid(65534); "
                                       "sys.stdout.write(open(sys.argv[1],'rb').read().replace(b'\\0',b' ').decode())",
                                       f"/proc/{p.pid}/cmdline"], capture_output=True, text=True, timeout=30).stdout
                self.assertIn(f"-c {OP_USER}:", seen, "the nobody read saw nothing")
            for shared in (OP_PASS, V_DEFAULT):
                self.assertNotIn(shared, seen, "a shared credential is readable in /proc")
        finally:
            p.kill()
            p.wait()
        r = subprocess.run([sys.executable, CRED_CLI, "exec", "ttyd", "--", "-p", "1"],
                           env=dict(env, CREDENTIALS_DIRECTORY=os.path.join(self.d, "absent")),
                           capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 3, "exec ttyd started without an applied login")
        self.assertIn("mios-credential apply ttyd", r.stderr)

    def test_positive_portal_credstore_and_restart_only_on_change(self):
        r = self._apply("portal")
        self.assertEqual(r.returncode, 0, r.stderr)
        p = os.path.join(self.d, "credstore", "mios.portal.password")
        self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o400)
        with open(p) as fh:
            self.assertEqual(fh.read(), OP_PASS)
        restarts = [c["argv"] for c in self._recorded() if "try-restart" in c["argv"]]
        self.assertEqual(restarts, [["systemctl", "--no-block", "try-restart", "mios-agent-pipe.service"]])
        r = self._apply("portal")
        self.assertIn("portal: unchanged", r.stderr)
        self.assertEqual(len([c for c in self._recorded() if "try-restart" in c["argv"]]), 1)
        r = self._apply("portal", password="fixture-changed-in-mios-html")   # the operator changes it
        self.assertIn("portal: changed", r.stderr)
        with open(p) as fh:
            self.assertEqual(fh.read(), "fixture-changed-in-mios-html")
        self._assert_private(r, "fixture-changed-in-mios-html")

    def test_negative_no_restart_is_synchronous(self):
        self._apply("portal")
        self._apply("ttyd", password="fixture-changed-in-mios-html")
        sync = [c["argv"] for c in self._recorded() if "try-restart" in c["argv"] and "--no-block" not in c["argv"]]
        self.assertEqual(sync, [], "a restart ran synchronously from the oneshot its unit is ordered after")

    def test_check_reports_drift_without_changing(self):
        r = self._apply("--check", "portal")
        self.assertEqual(r.returncode, 1)
        self.assertIn("portal: drift", r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.d, "credstore")))
        self.assertFalse(os.path.exists(os.path.join(self.d, "var")), "check created ledger directories/key/lock")
        self.assertEqual(self._recorded(), [])

    def test_check_does_not_repair_or_replace_an_existing_short_ledger_key(self):
        self.assertEqual(self._apply("portal").returncode, 0)
        directory = os.path.join(self.d, "var", "ledger")
        _envfile(directory, "key", "short")
        snapshot = lambda: {n: (open(os.path.join(directory, n), "rb").read(), os.stat(os.path.join(directory, n)).st_mtime_ns)
                            for n in os.listdir(directory)}  # noqa: E731
        before = snapshot()
        self._apply("--check", "portal")
        self.assertEqual(snapshot(), before)

    def test_mutating_apply_fails_closed_when_lock_cannot_be_acquired(self):
        result = self._apply("portal", extra={"FAKE_LOCK_FAIL": "1"})
        self.assertEqual(result.returncode, 3, result.stderr)
        self.assertIn("no lock", result.stderr)
        self.assertIn("nothing was changed", result.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.d, "credstore")))
        self.assertFalse(os.path.exists(os.path.join(self.d, "var")))
        self.assertEqual(self._recorded(), [])

    def test_shadow_follows_only_an_operator_set_credential(self):
        if not shutil.which("openssl"):
            self.skipTest("openssl absent")
        r = self._apply("shadow", password=None)               # the operator never saved one
        self.assertIn("shadow: skipped (no operator-set credential", r.stderr)
        self.assertFalse([c for c in self._recorded() if c["argv"][0] == "chpasswd"],
                         "the vendor default rotated the Linux login")
        r = self._apply("shadow")
        self.assertEqual(r.returncode, 0, r.stderr)
        ch = [c for c in self._recorded() if c["argv"][0] == "chpasswd"]
        self.assertEqual(ch[0]["argv"], ["chpasswd", "-e"])
        self.assertRegex(ch[0]["stdin"], rf"^{OP_USER}:\$6\$")
        self._assert_private(r)

    def _shadow(self, password):
        h = subprocess.run(["openssl", "passwd", "-6", "-stdin"], input=password + "\n", capture_output=True,
                           text=True, check=True).stdout.strip()
        _envfile(self.d, "shadow", f"root:!:19000::::::\n{OP_USER}:{h}:19000:0:99999:7:::\n")

    def test_shadow_login_already_set_to_the_credential_is_unchanged(self):
        import ctypes.util
        if not shutil.which("openssl") or not ctypes.util.find_library("crypt"):
            self.skipTest("openssl or libcrypt absent")
        self._shadow(OP_PASS)                       # the Blade: the login was set by hand already
        r = self._apply("shadow")
        self.assertIn("shadow: unchanged", r.stderr)
        self.assertFalse([c for c in self._recorded() if c["argv"][0] == "chpasswd"],
                         "a login that already accepts the credential was rewritten")
        self._shadow("fixture-some-other-login")    # negative: a different login IS brought in line
        os.unlink(os.path.join(self.d, "var", "ledger", "state.json"))
        r = self._apply("shadow")
        self.assertIn("shadow: changed", r.stderr)
        self.assertEqual(len([c for c in self._recorded() if c["argv"][0] == "chpasswd"]), 1)

    def test_negative_hostile_operator_tier_cannot_steer_root(self):
        if not shutil.which("openssl"):
            self.skipTest("openssl absent")
        evil = os.path.join(self.d, "evil")
        os.mkdir(evil)
        marker = os.path.join(self.d, "evil-ran")
        _envfile(evil, "mios-owui-bootstrap-admin", f"#!/bin/sh\ntouch {marker}\n", 0o755)
        hostile = (f'[identity]\nusername = "root"\ndefault_password = "{OP_PASS}"\n'
                   f'[security.credential_apply]\nsurfaces = ["shadow", "owui"]\n'
                   f'[paths]\nlibexec_dir = "{evil}"\n[network]\nloopback = "10.9.9.9"\n')
        self.surfaces = '["shadow"]'
        r = self._apply("--all", operator=hostile)
        ch = [c for c in self._recorded() if c["argv"][0] == "chpasswd"]
        self.assertTrue(ch and all(c["stdin"].startswith(f"{OP_USER}:") for c in ch),
                        "chpasswd was retargeted by the operator tier")
        self.assertNotIn("owui:", r.stderr, "the operator tier added a surface")
        self.assertFalse(os.path.exists(marker), "root ran a tool from an operator-tier path")

    def test_negative_broken_operator_tier_writes_nothing(self):
        r = self._apply("--all", operator='[identity\ndefault_password = "fixture-broken-secret"\n')
        self.assertEqual(r.returncode, 3, r.stderr)
        self.assertIn("operator tier unresolved", r.stderr)
        self.assertNotIn("fixture-broken-secret", r.stdout + r.stderr)
        self.assertFalse(os.path.exists(os.path.join(self.d, "credstore")), "a broken tier wrote the credstore")
        self.assertEqual(self._recorded(), [])

    def test_owui_records_only_a_reconciled_admin(self):
        self.surfaces = '["owui"]'
        self.backoff = 0                         # retry at once: the second run must not read 'unchanged'
        tool = _envfile(os.path.join(self.d, "libexec"), "mios-owui-bootstrap-admin", "#!/bin/sh\nexit 4\n", 0o755)
        r = self._apply("owui")
        self.assertIn("owui: failed (no Open WebUI admin matches", r.stderr)
        _envfile(os.path.join(self.d, "libexec"), "mios-owui-bootstrap-admin", "#!/bin/sh\nexit 0\n", 0o755)
        r = self._apply("owui")
        self.assertIn("owui: changed", r.stderr, "a left-as-is admin was recorded as applied")
        r = self._apply("owui")
        self.assertIn("owui: unchanged", r.stderr)
        self.assertTrue(os.path.exists(tool))

    def test_portal_save_reaches_the_operator_tier_through_the_inbox(self):
        user = _envfile(self.d, "u.toml", '# the operator\'s own note\n[desktop]\ntheme = "fixture"\n'
                                          f'[identity]\ndefault_password = "{OP_PASS}"\n')
        inbox = os.path.join(self.d, "inbox")
        hostile = {"identity.default_password": "fixture-portal-saved", "identity.username": "root",
                   "paths.libexec_dir": "/tmp/fixture-evil", "tailscale.serve.enabled": True}
        # Before anything was applied there is no Portal login to prove: nothing is merged.
        _envfile(inbox, "0000-1.toml", _request(hostile, OP_PASS))
        r = self._apply("--check", "portal", password=None, operator=open(user).read())
        self.assertIn("not merged (--check)", r.stderr)
        r = self._apply("portal", password=None, operator=open(user).read())
        self.assertIn("inbox: 0000-1.toml: refused (no proof of the Portal's current credential)", r.stderr)
        self.assertEqual(os.listdir(inbox), [])
        # The Portal now accepts OP_PASS (the credstore): only a request that proves it merges.
        _envfile(inbox, "0001-1.toml", _request(hostile, OP_PASS))
        os.symlink(os.path.join(self.d, "root-only.txt"), os.path.join(inbox, "0002-1.toml"))
        forged = {"identity.default_password": "fixture-forged-save"}
        _envfile(inbox, "0003-1.toml", _request(forged, "fixture-a-guess"))      # keyed by a guess
        _envfile(inbox, "0004-1.toml", _request(forged, None))                   # no proof at all
        # a proved request someone else saw, with one key swapped in afterwards
        _envfile(inbox, "0005-1.toml", _request({"identity.default_password": "fixture-seen"}, OP_PASS)
                 .replace("fixture-seen", "fixture-forged-save"))
        _envfile(inbox, "0006-1.toml", _request(forged, OP_PASS).replace(
            "request.proof", f'request.note = "{OP_PASS}"\nrequest.proof'))         # an extra row
        r = self._apply("portal", password=None, operator=open(user).read())
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("inbox: 0001-1.toml: merged identity.default_password, tailscale.serve.enabled", r.stderr)
        self.assertIn("inbox: 0002-1.toml: refused", r.stderr)
        for forged in ("0003-1.toml", "0004-1.toml", "0005-1.toml", "0006-1.toml"):
            self.assertIn(f"inbox: {forged}: refused (no proof of the Portal's current credential)", r.stderr)
        self.assertNotIn("fixture-forged-save", open(user).read(), "an unproved request reached the operator tier")
        self.assertEqual(os.listdir(inbox), [])
        with open(user) as fh:
            text = fh.read()
        import tomllib
        doc = tomllib.loads(text)
        self.assertEqual(doc["identity"], {"default_password": "fixture-portal-saved"},
                         "the inbox carried more than [security.operator_tier] into the operator tier")
        self.assertEqual(doc["desktop"], {"theme": "fixture"})
        self.assertIn("# the operator's own note", text)
        self.assertNotIn("paths", doc)
        self.assertEqual(stat.S_IMODE(os.stat(user).st_mode), 0o600)
        with open(os.path.join(self.d, "credstore", "mios.portal.password")) as fh:
            self.assertEqual(fh.read(), "fixture-portal-saved")
        self.assertNotIn("fixture-portal-saved", r.stdout + r.stderr)

    def test_portal_save_shadowed_by_user_fragment_stays_queued_without_writes(self):
        self.assertEqual(self._apply("portal").returncode, 0)
        user = os.path.join(self.d, "u.toml")
        before = open(user, "rb").read()
        fragments = os.path.join(self.d, "u.d")
        os.mkdir(fragments)
        fragment = _envfile(fragments, "90-password.toml", f'[identity]\ndefault_password = "{OP_PASS}"\n')
        same = _envfile(os.path.join(self.d, "inbox"), "0000-same.toml",
                        _request({"identity.default_password": OP_PASS}, OP_PASS))
        self.assertEqual(self._apply("portal").returncode, 0)
        self.assertFalse(os.path.exists(same), "a fragment with the same effective value wrongly blocked the save")
        pending = _envfile(os.path.join(self.d, "inbox"), "0001-save.toml",
                           _request({"identity.default_password": "fixture-shadowed-save"}, OP_PASS))
        result = self._apply("portal")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Portal save is shadowed", result.stderr)
        self.assertIn("nothing merged", result.stderr)
        self.assertNotIn("fixture-shadowed-save", result.stdout + result.stderr)
        self.assertEqual(open(user, "rb").read(), before)
        self.assertTrue(os.path.exists(pending), "an ineffective save was consumed")
        with open(os.path.join(self.d, "credstore", "mios.portal.password"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), OP_PASS)
        # Once the higher-priority pin is removed, the SAME authenticated request can land.
        os.unlink(fragment)
        result = self._apply("portal")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(os.path.exists(pending))
        with open(os.path.join(self.d, "credstore", "mios.portal.password"), encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "fixture-shadowed-save")

    def test_unmerged_save_stays_queued_with_every_later_one(self):
        inbox = os.path.join(self.d, "inbox")
        self._apply("portal")                                   # the Portal accepts OP_PASS
        # An operator tier whose layout an edit cannot keep (an inline table) refuses the
        # first save; the second alone would merge -- but never ahead of the first.
        stuck = f'tailscale = {{ serve = {{ enabled = false }} }}\n[identity]\ndefault_password = "{OP_PASS}"\n'
        _envfile(inbox, "0001-1.toml", _request({"tailscale.serve.enabled": True}, OP_PASS))
        _envfile(inbox, "0002-1.toml", _request({"identity.default_password": "fixture-newer-save"}, OP_PASS))
        r = self._apply("portal", password=None, operator=stuck)
        self.assertIn("inbox: 0001-1.toml: not merged", r.stderr)
        self.assertNotIn("fixture-newer-save", open(os.path.join(self.d, "u.toml")).read(),
                         "a later save overtook an earlier one that is still queued")
        self.assertEqual(sorted(os.listdir(inbox)), ["0001-1.toml", "0002-1.toml"],
                         "an unmerged save, or a later one, was dropped")
        fixed = f'[identity]\ndefault_password = "{OP_PASS}"\n[tailscale.serve]\nenabled = false\n'
        r = self._apply("portal", password=None, operator=fixed)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertLess(r.stderr.index("inbox: 0001-1.toml: merged"), r.stderr.index("inbox: 0002-1.toml: merged"))
        self.assertEqual(os.listdir(inbox), [])
        import tomllib
        with open(os.path.join(self.d, "u.toml"), "rb") as fh:
            doc = tomllib.load(fh)
        self.assertEqual((doc["identity"]["default_password"], doc["tailscale"]["serve"]["enabled"]),
                         ("fixture-newer-save", True))
        with open(os.path.join(self.d, "credstore", "mios.portal.password")) as fh:
            self.assertEqual(fh.read(), "fixture-newer-save")

    def test_scrub_reports_a_read_only_target_instead_of_failing(self):
        # forge-firstboot's sandbox keeps /etc/mios read-only: the scrub must report and go on.
        _envfile(self.d, "secrets.env", "MIOS_IDENTITY_DEFAULT_PASSWORD=fixture-rotated\n")
        logs = []
        import errno
        orig = mc._atomic

        def readonly(*_a, **_k):
            raise OSError(errno.EROFS, "Read-only file system")
        mc._atomic = readonly
        try:
            n = mc.scrub_leftovers({"paths": {"etc_dir": self.d}}, logs.append)
        finally:
            mc._atomic = orig
        self.assertEqual(n, 0)
        self.assertTrue(any("could not remove the stale MIOS_IDENTITY_DEFAULT_PASSWORD" in m for m in logs), logs)
        self.assertNotIn("fixture-rotated", " ".join(logs))

    def test_inbox_submit_is_what_the_reconciler_merges(self):
        """The Portal's writer (inbox_submit) and root's reader (ingest_inbox) agree."""
        inbox = os.path.join(self.d, "inbox")
        data = {"paths": {"run_credential_inbox_dir": inbox}}
        with self.assertRaises(mc.CredentialError):
            mc.inbox_submit(data, {"identity.default_password": "fixture-x"}, "")      # no login, no queue
        with self.assertRaises(mc.CredentialError):
            mc.inbox_submit({"paths": {"run_credential_inbox_dir": os.path.join(self.d, "absent")}},
                            {"identity.default_password": "fixture-x"}, OP_PASS)
        self.assertEqual(os.listdir(inbox), [])
        self._apply("portal")                                   # the Portal accepts OP_PASS
        name = mc.inbox_submit(data, {"identity.default_password": 'fixture "quoted" \\ save',
                                      "tailscale.serve.enabled": True}, OP_PASS)
        self.assertEqual(os.listdir(inbox), [name])
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(inbox, name)).st_mode), 0o600)
        self.assertRegex(name, r"^\d+-[0-9a-f]{32}\.toml$", "a request name its writers could guess")
        with open(os.path.join(inbox, name)) as fh:
            self.assertNotIn(OP_PASS, fh.read(), "the Portal's current credential was written to the inbox")
        r = self._apply("portal", password=None, operator=f'[identity]\ndefault_password = "{OP_PASS}"\n')
        self.assertIn(f"inbox: {name}: merged identity.default_password, tailscale.serve.enabled", r.stderr)
        with open(os.path.join(self.d, "credstore", "mios.portal.password")) as fh:
            self.assertEqual(fh.read(), 'fixture "quoted" \\ save')

    def test_scrub_removes_the_old_copies_of_the_credential(self):
        _envfile(self.d, "secrets.env", "HA_PASSWORD=keep\nMIOS_IDENTITY_DEFAULT_PASSWORD=fixture-rotated\n")
        repo = os.path.join(self.d, "root-repo")
        git = shutil.which("git")
        if git:
            subprocess.run([git, "init", "-q", repo], check=True)
            forge = f"127.0.0.1:{self.srv.server_address[1]}"
            subprocess.run([git, "-C", repo, "remote", "add", "origin",
                            f"http://{OP_USER}:fixture-old-url-pass@{forge}/{OP_USER}/mios.git"], check=True)
        r = self._apply("portal")
        self.assertIn("scrub: removed the stale MIOS_IDENTITY_DEFAULT_PASSWORD", r.stderr)
        with open(os.path.join(self.d, "secrets.env")) as fh:
            self.assertEqual(fh.read(), "HA_PASSWORD=keep\n")
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.d, "secrets.env")).st_mode), 0o600)
        if git:
            url = subprocess.run([git, "-C", repo, "remote", "get-url", "origin"], capture_output=True,
                                 text=True).stdout.strip()
            self.assertEqual(url, f"http://{forge}/{OP_USER}/mios.git")
            # An operator's own token on another host is not ours: logged, never removed.
            token = "https://fixture-ghp-token@github.com/mios-developer/mios.git"
            subprocess.run([git, "-C", repo, "remote", "set-url", "origin", token], check=True)
            r2 = self._apply("portal")
            self.assertIn("not this Forgejo's admin -- left as it is", r2.stderr)
            self.assertEqual(subprocess.run([git, "-C", repo, "remote", "get-url", "origin"], capture_output=True,
                                            text=True).stdout.strip(), token)
            self.assertNotIn("fixture-ghp-token", r2.stdout + r2.stderr)
        self.assertNotIn("fixture-old-url-pass", r.stdout + r.stderr)
        self.assertNotIn("fixture-old-url-pass", json.dumps(self._recorded()))
        self.assertNotIn("fixture-rotated", r.stdout + r.stderr)

    def test_scrub_strips_userinfo_whatever_the_password_holds(self):
        hosts = ["127.0.0.1:8400", "localhost:8400"]
        for pw in ("fixture/with/slashes", "fixture@with@ats", "fixture:colon", "fixture%2Fenc", "fixture?q#f"):
            for host in hosts:
                url = f"http://{OP_USER}:{pw}@{host}/{OP_USER}/mios.git"
                clean, state = mc._strip_userinfo(url, OP_USER, hosts)
                self.assertEqual((clean, state), (f"http://{host}/{OP_USER}/mios.git", "stripped"), pw)
        for url in ("http://127.0.0.1:8400/u/mios.git", "https://github.com/x/y.git", "git@host:x/y.git"):
            self.assertEqual(mc._strip_userinfo(url, OP_USER, hosts), (url, "none"))
        url = "https://other:pa/ss@example.invalid/x.git"          # not the forge shape: never guessed at
        self.assertEqual(mc._strip_userinfo(url, OP_USER, hosts), (url, "unparsed"))
        for pw in ("a@b/c", "a:b@c/d", "x/y@z"):    # urlsplit splits at the FIRST '@': the forge shape wins
            url = f"http://{OP_USER}:{pw}@127.0.0.1:8400/{OP_USER}/mios.git"
            self.assertEqual(mc._strip_userinfo(url, OP_USER, hosts),
                             (f"http://127.0.0.1:8400/{OP_USER}/mios.git", "stripped"), pw)
        for url in ("https://ghp_fixture@github.com/mios-developer/mios.git",       # an operator's token
                    "http://someone:tok@127.0.0.1:8400/x.git"):                       # not the forge admin
            self.assertEqual(mc._strip_userinfo(url, OP_USER, hosts), (url, "foreign"))
        self.assertEqual(mc._strip_userinfo(f"http://{OP_USER}:pw@127.0.0.1:8400", OP_USER, hosts),
                         ("http://127.0.0.1:8400", "stripped"))
        # Negative control: the round-3 regex left a '/' password in place and kept part of an '@' one.
        old = re.compile(r"^(https?://)[^/@]+@(.+)$")
        url = f"http://{OP_USER}:fixture/with/slashes@127.0.0.1:8400/{OP_USER}/mios.git"
        self.assertIsNone(old.match(url))
        m = old.match(f"http://{OP_USER}:fixture@with@ats@127.0.0.1:8400/x.git")
        self.assertIn("with@ats", m.group(1) + m.group(2))


def _unit_settings(name: str) -> dict:
    out: dict = {}
    with open(os.path.join(ROOT, "usr", "lib", "systemd", "system", name), encoding="utf-8") as fh:
        for line in fh:
            if "=" in line and not line.lstrip().startswith(("#", "[")):
                k, _, v = line.strip().partition("=")
                out.setdefault(k, []).append(v)
    return out


def _ledger_sandbox_defects(units: dict) -> list:
    """Each unit (name -> text) that runs `mios-credential apply` (in an Exec line, or through
    a libexec script that does) inside a ProtectSystem=strict sandbox without the ledger dir
    in ReadWritePaths=, or with the operator home hidden, unless the line runs with '+'."""
    ledger = "/var/lib/mios/credential-apply"
    out = []
    for name, text in sorted(units.items()):
        settings, execs = {}, []
        for line in text.splitlines():
            k, sep, v = line.strip().partition("=")
            if not sep or line.lstrip().startswith(("#", "[")):
                continue
            settings.setdefault(k, []).append(v)
            if k.startswith("Exec"):
                execs.append(v)
        runs = []
        for v in execs:
            cmd = v.split()[0] if v.split() else ""
            script = os.path.join(ROOT, cmd.lstrip("+-@!:").lstrip("/")) if cmd else ""
            body = open(script, encoding="utf-8", errors="replace").read() if os.path.isfile(script) else ""
            if "mios-credential apply" in v or re.search(r"\$CRED\"? apply|mios-credential\"? apply", body):
                runs.append(v)
        for v in runs:
            if v.startswith("+"):
                continue
            rw = " ".join(settings.get("ReadWritePaths", [])).split()
            if settings.get("ProtectSystem", ["no"])[-1] in ("strict", "full") and ledger not in [p.lstrip("-") for p in rw]:
                out.append(f"{name}: runs mios-credential apply without {ledger} in ReadWritePaths=")
            if settings.get("ProtectHome", ["no"])[-1] in ("yes", "true", "tmpfs"):
                out.append(f"{name}: runs mios-credential apply with the operator home hidden")
    return out


class TestReconcilerUnit(unittest.TestCase):
    def _shipped(self) -> dict:
        d = os.path.join(ROOT, "usr", "lib", "systemd", "system")
        return {n: open(os.path.join(d, n), encoding="utf-8").read() for n in os.listdir(d)
                if n.endswith(".service")}

    def test_every_unit_that_runs_apply_can_write_its_ledger(self):
        units = self._shipped()
        self.assertEqual(_ledger_sandbox_defects(units), [])
        runners = [n for n in units if "mios-credential apply" in units[n]]
        self.assertIn("mios-agents.service", runners, "the scan no longer sees the IDE's ExecStartPre")
        # Negative control: the round-3 mios-agents sandbox (no ledger dir) is reported.
        planted = dict(units)
        planted["mios-agents.service"] = re.sub(r"(?m)^ReadWritePaths=.*$",
                                                "ReadWritePaths=/var/lib/mios/agents /var/lib/containers /run",
                                                units["mios-agents.service"])
        self.assertEqual(_ledger_sandbox_defects(planted),
                         ["mios-agents.service: runs mios-credential apply without "
                          "/var/lib/mios/credential-apply in ReadWritePaths="])

    def test_ttyd_units_import_their_own_login(self):
        for name in ("mios-ttyd-bash.service", "mios-ttyd-powershell.service"):
            u = _unit_settings(name)
            self.assertEqual(sorted(u.get("ImportCredential", [])), ["mios.ttyd.password", "mios.ttyd.user"], name)
            self.assertIn("mios-credential-apply.service", " ".join(u.get("After", [])), name)

    def test_timer_reconciliation_preserves_active_sessions_from_ssot(self):
        import tomllib
        with open(os.path.join(ROOT, "usr", "share", "mios", "mios.toml"), "rb") as fh:
            ssot = tomllib.load(fh)
        command = ssot["units"]["mios-credential-apply.service"]["Service"]["ExecStart"]
        self.assertIn("--no-restart", command.split())
        self.assertEqual(_unit_settings("mios-credential-apply.service").get("ExecStart"), [command],
                         "Native unit projection must precede the credential suite")

    def test_operator_home_is_writable_for_the_inbox_merge(self):
        # systemd gives a ProtectHome= mount priority over a ReadWritePaths= entry for the
        # same path (/home chases to /var/home), so ProtectHome=read-only would turn every
        # Portal save into EROFS; ProtectSystem=strict + ReadWritePaths= is the boundary.
        u = _unit_settings("mios-credential-apply.service")
        self.assertEqual(u.get("ProtectSystem"), ["strict"])
        rw = " ".join(u.get("ReadWritePaths", [])).split()
        for need in ("-/var/home", "-/home", "/run", "/etc"):
            self.assertIn(need, rw)
        self.assertNotIn(u.get("ProtectHome", ["no"])[-1], ("yes", "true", "read-only", "tmpfs"),
                         "ProtectHome= masks the operator home the inbox merge writes")


class TestConfiguratorNeverSavesABlankCredential(unittest.TestCase):
    """mios.html omits a blank credential key (data-inherit) instead of saving ""."""

    def test_credential_fields_inherit_when_blank(self):
        with open(os.path.join(ROOT, "usr", "share", "mios", "configurator", "mios.html"), encoding="utf-8") as fh:
            html = fh.read()
        for key in ("identity.default_password", "ttyd.auth_pass"):
            tag = re.search(r'<input[^>]*data-key="' + re.escape(key) + r'"[^>]*>', html)
            self.assertTrue(tag, key)
            self.assertIn('data-inherit="true"', tag.group(0), f"a blank {key} is saved as an empty string")
        gather = re.search(r"(?s)function gatherFromForm\(\) \{.*?\n\}", html).group(0)
        self.assertRegex(gather, r'el\.dataset\.inherit === "true" && !el\.value\.trim\(\)\) \{\s*if \(DATA\[sec\]\) delete DATA\[sec\]\[key\]')
        self.assertNotIn("ttyd.require_auth", html, "the configurator offers a setting nothing reads")


class TestInstallEnvCarriesNoCredential(unittest.TestCase):
    """install.env is world-readable within mios-*: the operator credential never lands there."""

    def test_sync_env_projects_no_secret_key(self):
        script = os.path.join(ROOT, "usr", "libexec", "mios", "system-sync-env.sh")
        with tempfile.TemporaryDirectory(prefix="mios-sync-env-") as home:
            env = {"PATH": "/usr/bin:/bin", "HOME": home, "MIOS_ROOT": ROOT,
                   "MIOS_IDENTITY_DEFAULT_PASSWORD": "fixture-leak-a", "MIOS_TTYD_AUTH_PASS": "fixture-leak-b"}
            r = subprocess.run(["bash", script, "--dry-run"], env=env, capture_output=True, text=True, timeout=120)
            keys = subprocess.run(["bash", script, "--secret-keys"], env=env, capture_output=True,
                                  text=True, timeout=60).stdout.split()
        self.assertEqual(r.returncode, 0, r.stderr[-400:])
        self.assertIn("MIOS_IDENTITY_DEFAULT_PASSWORD", keys)
        self.assertIn("MIOS_TTYD_AUTH_PASS", keys)
        for k in keys:
            self.assertNotRegex(r.stdout, rf"(?m)^{k}=", f"install.env projects secret key {k}")
        self.assertNotIn("fixture-leak", r.stdout + r.stderr)

    def test_sync_env_selftest_tail_preserves_old_output_and_redacts_failures(self):
        # Execute the actual publication tail, not a reimplementation; no live paths or
        # resolver are used. Both failure controls would reach mv in the old tail.
        script = os.path.join(ROOT, "usr", "libexec", "mios", "system-sync-env.sh")
        with open(script, encoding="utf-8") as fh:
            source = fh.read()
        self.assertEqual(source.count("\n_st=0\n"), 1)
        tail = source.split("\n_st=0\n", 1)[1]
        program = ('set -euo pipefail\n_read_security_array() { printf "%s\\n" MIOS_TEST_SECRET; }\n'
                   '_st=0\n' + tail)
        cases = ((b'MIOS_TEST_SECRET="fixture-quoted-secret"\n', False),
                 (b"MIOS_TEST_PUBLIC='unterminated\n", False),
                 (b"MIOS_TEST_PUBLIC=fixture-valid-bare\n", True))
        for body, valid in cases:
            with self.subTest(body=body), tempfile.TemporaryDirectory(prefix="mios-env-tail-") as directory:
                tmp = os.path.join(directory, "incoming.env")
                out = os.path.join(directory, "installed.env")
                _envfile(directory, "incoming.env", body.decode())
                _envfile(directory, "installed.env", "MIOS_OLD=preserved\n")
                result = subprocess.run(["bash", "-c", program],
                                        env={"PATH": "/usr/bin:/bin", "TMP": tmp, "OUT": out},
                                        capture_output=True, text=True, timeout=30)
                if valid:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(open(out, "rb").read(), body)
                else:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertEqual(open(out, "rb").read(), b"MIOS_OLD=preserved\n")
                    self.assertNotIn("fixture-quoted-secret", result.stdout + result.stderr)

    def test_forge_firstboot_hands_the_password_to_the_reconciler(self):
        # Static guard (the script needs a live Forgejo): `mios-credential apply` sets the password
        # via the API body -- never install.env, a literal, a file, argv or git-URL userinfo.
        with open(os.path.join(ROOT, "usr", "libexec", "mios", "forge-firstboot.sh"), encoding="utf-8") as fh:
            s = fh.read()
        self.assertIn('"$CRED" apply --wait --bootstrap forge', s)
        self.assertIn("--random-password >/dev/null 2>&1", s)
        for bad in ("/etc/mios/install.env", "source ", "MIOS_IDENTITY_DEFAULT_PASSWORD", "--password ", "admin_password",
                    "curl -sS -u", "openssl rand", "admin-password"):
            self.assertNotIn(bad, s, f"forge-firstboot.sh carries {bad!r}")

    def test_installer_records_the_account_it_created_in_the_host_tier(self):
        # Root code finds the operator only through vendor/host [identity].username: the
        # installer must name the account it created there (Law 15: both trees).
        trees = [ROOT] + ([BOOT] if os.path.isfile(os.path.join(BOOT, "installation", "mios-install.sh")) else [])
        for tree in trees:
            with open(os.path.join(tree, "installation", "mios-install.sh"), encoding="utf-8") as fh:
                s = fh.read()
            m = re.search(r"(?ms)^write_host_identity\(\) \{\n.*?^\}\n", s)
            self.assertTrue(m, f"{tree}: no write_host_identity")
            self.assertIn('write_host_identity "${LINUX_USER}"', s, f"{tree}: the installer never calls it")
            with tempfile.TemporaryDirectory(prefix="mios-install-id-") as d:
                stub = 'log_ok() { :; }\nlog_err() { echo "$*" >&2; }\nPROFILE_DIR="$1"\n'
                for user, rc in (("fixop", 0), ('x"\n[evil]', 1), ("Root", 1)):
                    r = subprocess.run(["bash", "-c", stub + m.group(0) + 'write_host_identity "$2"', "_", d, user],
                                       capture_output=True, text=True, timeout=30)
                    self.assertEqual(r.returncode, rc, f"{tree}: {user!r}: {r.stderr}")
                import tomllib
                with open(os.path.join(d, "mios.d", "10-identity.toml"), "rb") as fh:
                    self.assertEqual(tomllib.load(fh), {"identity": {"username": "fixop"}},
                                     f"{tree}: a refused name overwrote the recorded account")

    def test_installer_writes_no_forge_admin_key(self):
        # Law 15: the installer (mirrored byte for byte in mios-bootstrap) neither prompts for a
        # Forgejo admin nor writes a forge-admin key to install.env -- forge-firstboot reads neither.
        trees = [ROOT] + ([BOOT] if os.path.isfile(os.path.join(BOOT, "installation", "mios-install.sh")) else [])
        for tree in trees:
            with open(os.path.join(tree, "installation", "mios-install.sh"), encoding="utf-8") as fh:
                s = fh.read()
            for bad in ("_FORGE_ADMIN_", "FORGE_ADMIN_USER=", "Forge admin", "forge/admin-password"):
                self.assertNotIn(bad, s, f"{tree}: installation/mios-install.sh still carries {bad!r}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
