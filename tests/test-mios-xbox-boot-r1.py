#!/usr/bin/env python3
# AI-hint: Two-sided tests for the runtime FAILs of the MiOS-Xbox round-1 debug boot (build-20261023T224037Z in a GPU-less transient VM, xbox-m1/boot-round-1.md): AutoLogon persistence cannot be finished at SetupComplete (Windows transfers the answer-file AutoLogon + LogonCount only at the first sign-in) so the SYSTEM Verify task deletes AutoLogonCount and the SetupComplete step defers to it; the desktop hive's DEFAULT_USER values that the first sign-in overwrites are re-applied by Verify (Set-MiOSUserValue / Merge-MiOSKv); the Hydrate stage (desktop user) writes under %LOCALAPPDATA%\MiOS because the SYSTEM state dir is read-only for users; the OS-control server survives an invalid console at logon (Write-Host "The handle is invalid"); the per-request agent relay is shipped but never registered as a task (components.ship invocation); probe.py authenticates WinRM as the elevation admin (the desktop account is a standard user) and re-executes under its pywinrm venv even when the venv python is a symlink. Every positive control has a negative control (the code the round-1 image ran, or HEAD). No network, no VM.
# AI-related: usr/libexec/mios/win/xbox/answer.py, usr/libexec/mios/win/xbox/render_spec.py, usr/libexec/mios/win/xbox/probe.py, usr/share/mios/windows/build/MiOS-Xbox.Provision.ps1, usr/share/mios/windows/mios-oscontrol-server.ps1, usr/share/mios/mios.toml, src/mios-rs/miosd/src/fhs_sync.rs (cargo tests), tests/test-mios-xbox-provisioning.py
"""Run: python3 -I tests/test-mios-xbox-boot-r1.py -v   (temp dirs only; no network, no VM)"""

from __future__ import annotations

import copy
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

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
_spec = importlib.util.spec_from_file_location("mios_xbox_provisioning_tests_r1", os.path.join(HERE, "test-mios-xbox-provisioning.py"))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)   # shares its fixture SSOT layers, fake inputs and payload base (no ~/.ssh read)
A, R = P.A, P.R
PROV = os.path.join(ROOT, "usr", "share", "mios", "windows", "build", "MiOS-Xbox.Provision.ps1")
OSC = os.path.join(ROOT, "usr", "share", "mios", "windows", "mios-oscontrol-server.ps1")
PROBE = os.path.join(ROOT, "usr", "libexec", "mios", "win", "xbox", "probe.py")
HAVE_PWSH = bool(shutil.which("pwsh"))

# The autologon_persistent step exactly as the round-1 image ran it (baked mios-xbox-setup.ps1 of
# build-20261023T224037Z, lines 327-334): it failed "Winlogon DefaultUserName is not the desktop account" because
# Winlogon still held OOBE's defaultuser0 autologon at SetupComplete.
OLD_AUTOLOGON_STEP = """    $wl = 'HKLM:\\SOFTWARE\\Microsoft\\Windows NT\\CurrentVersion\\Winlogon'
    Remove-ItemProperty -Path $wl -Name 'AutoLogonCount' -ErrorAction SilentlyContinue
    $v = Get-ItemProperty -Path $wl
    if ([string]$v.AutoAdminLogon -ne '1') { throw 'Winlogon AutoAdminLogon is not 1 (the answer-file AutoLogon is not applied)' }
    if ([string]$v.DefaultUserName -ne 'user') { throw 'Winlogon DefaultUserName is not the desktop account' }
    if (@($v.PSObject.Properties.Name) -contains 'AutoLogonCount') { throw 'Winlogon AutoLogonCount is still set' }
"""


def _rd(p: str) -> str:
    with open(p, encoding="utf-8-sig") as fh:
        return fh.read()


def _git_head(rel: str):
    if not shutil.which("git"):
        return None
    r = subprocess.run(["git", "-C", ROOT, "show", f"HEAD:{rel}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 and r.stdout else None


def _pwsh(script_text: str, tmp: str, env=None):
    path = os.path.join(tmp, f"t{len(os.listdir(tmp))}.ps1")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(script_text)
    return subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", path], capture_output=True, text=True,
                          timeout=180, env={**os.environ, **(env or {})})


def _ps_functions(text: str, names, tmp: str) -> str:
    """The named function definitions of a PowerShell script, extracted with the PowerShell parser (AST)."""
    src = os.path.join(tmp, "src.ps1")
    with open(src, "w", encoding="utf-8") as fh:
        fh.write(text)
    q = ",".join("'" + n + "'" for n in names)
    r = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command",
                        f"$a = [System.Management.Automation.Language.Parser]::ParseFile('{src}', [ref]$null, [ref]$null); "
                        f"$a.FindAll({{ param($n) $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and "
                        f"@({q}) -contains $n.Name }}, $true) | ForEach-Object {{ $_.Extent.Text; '' }}"],
                       capture_output=True, text=True, timeout=120)
    if r.returncode:
        raise AssertionError(r.stderr)
    return r.stdout


# ---------------------------------------------------------------------------
# 1. persistent AutoLogon: SetupComplete defers to the SYSTEM Verify task
# ---------------------------------------------------------------------------

class TestAutologonDeferred(P.PayloadBase):
    MOCKS = r"""
$script:removed = @()
function Get-ItemProperty { param($Path) [pscustomobject]$script:wl }
function Remove-ItemProperty { param($Path, $Name, $ErrorAction) $script:removed += $Name; $script:wl.Remove($Name) }
function Get-ScheduledTask { param($TaskPath, $TaskName, $ErrorAction) if ($script:verifyTask) { [pscustomobject]@{ TaskName = $TaskName } } }
"""

    def _run(self, body: str, wl: dict, verify_task: bool, tmp: str):
        lib = A._stage_header(self.doc, "test", os.path.join(tmp, "setup.log"))
        lib[3] = "$MiOSSerialPort = 'COM9'"
        wl_ps = "; ".join(f"'{k}' = '{v}'" for k, v in wl.items())
        script = "\n".join(lib) + "\n" + self.MOCKS + f"$script:wl = [ordered]@{{ {wl_ps} }}\n" + \
            f"$script:verifyTask = ${'true' if verify_task else 'false'}\n" + \
            "Invoke-MiOSStep 'autologon_persistent' {\n" + body + "} | Out-Null\n" + \
            "ConvertTo-Json -Compress -InputObject @{ state = $MiOSState['autologon_persistent']; removed = @($script:removed) }\n"
        r = _pwsh(script, tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return json.loads(r.stdout.strip().splitlines()[-1])

    def test_positive_step_order_and_contents(self):
        names = self.steps()
        self.assertLess(names.index("verify_task"), names.index("autologon_persistent"),
                        "the deferral requires the Verify task, so it is registered first")
        self.assertEqual(names[-1], "finalize_rid500")
        s = self.step("autologon_persistent")
        vt = self.x["provision"]["verify_task"].split("\\")[-1]
        self.assertIn("Remove-ItemProperty -Path $wl -Name 'AutoLogonCount'", s)
        self.assertIn(f"-TaskName '{vt}'", s)
        self.assertNotIn("DefaultUserName is not the desktop account", s)

    @unittest.skipUnless(HAVE_PWSH, "pwsh not installed")
    def test_pwsh_round1_oobe_state_both_sides(self):
        tmp = tempfile.mkdtemp(dir=self.tmp)
        oobe = {"AutoAdminLogon": "1", "DefaultUserName": "defaultuser0"}   # Winlogon at SetupComplete (round-1 record)
        new = self.step("autologon_persistent")
        # positive: OOBE state + Verify task registered -> ok (deferred, nothing removed now)
        r = self._run(new, oobe, True, tmp)
        self.assertEqual(r["state"], "ok", r)
        self.assertEqual(r["removed"], [])
        # the deferral is never silent: without the Verify task it fails
        r = self._run(new, oobe, False, tmp)
        self.assertTrue(r["state"].startswith("failed:") and "Verify task" in r["state"], r)
        # Windows already transferred the desktop AutoLogon -> AutoLogonCount deleted now
        r = self._run(new, {"AutoAdminLogon": "1", "DefaultUserName": self.doc["users"]["desktop"]["name"],
                            "AutoLogonCount": "9999999"}, True, tmp)
        self.assertEqual(r["state"], "ok", r)
        self.assertEqual(r["removed"], ["AutoLogonCount"])
        # no AutoLogon configured at all is a failure
        r = self._run(new, {"AutoAdminLogon": "0", "DefaultUserName": "defaultuser0"}, True, tmp)
        self.assertTrue(r["state"].startswith("failed:"), r)
        # negative: the step the round-1 image ran fails on the very same OOBE state
        r = self._run(OLD_AUTOLOGON_STEP, oobe, True, tmp)
        self.assertEqual(r["state"], "failed: Winlogon DefaultUserName is not the desktop account", r)


# ---------------------------------------------------------------------------
# 2. Verify reconcile: AutoLogonCount + the desktop hive's DEFAULT_USER values
# ---------------------------------------------------------------------------

FAKE_KEY = r"""
class FakeKey {
    [hashtable]$Store; [string]$Path
    FakeKey([hashtable]$store, [string]$path) { $this.Store = $store; $this.Path = $path }
    [object] OpenSubKey([string]$k, [bool]$w) { $p = "$($this.Path)\$k"; if ($this.Store.ContainsKey($p)) { return [FakeKey]::new($this.Store, $p) }; return $null }
    [object] CreateSubKey([string]$k) { $p = "$($this.Path)\$k"; if (-not $this.Store.ContainsKey($p)) { $this.Store[$p] = @{} }; return [FakeKey]::new($this.Store, $p) }
    [string[]] GetValueNames() { return @($this.Store[$this.Path].Keys) }
    [object] GetValue([string]$n, $d, $o) { return $this.Store[$this.Path][$n].V }
    [string] GetValueKind([string]$n) { return $this.Store[$this.Path][$n].K }
    [void] SetValue([string]$n, $v, [string]$kind) { $this.Store[$this.Path][$n] = @{ V = $v; K = $kind }; $script:writes++ }
    [void] DeleteValue([string]$n) { $this.Store[$this.Path].Remove($n); $script:writes++ }
    [void] Close() { }
}
function Get-Prop($Object, [string]$Name) { if ($null -eq $Object) { return $null }; $p = $Object.PSObject.Properties[$Name]; if ($p) { return $p.Value }; return $null }
"""


@unittest.skipUnless(HAVE_PWSH, "pwsh not installed")
class TestVerifyReconcile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-xbox-r1-verify.")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_static_reconcile_precedes_the_record(self):
        t = _rd(PROV)
        verify = t.split("# Verify (SYSTEM)", 1)[1]
        self.assertLess(verify.index("Remove-ItemProperty -Path $wlPath -Name 'AutoLogonCount'"), verify.index("$autologon = [ordered]"))
        self.assertIn("[string](Get-Prop $w0 'DefaultUserName') -eq $deskName", verify)
        self.assertIn("Set-MiOSUserValue $uroot $it", verify)
        self.assertIn("user_defaults = $userDefaults", verify)
        self.assertIn("persisted_at = $persistedAt", verify)
        head = _git_head("usr/share/mios/windows/build/MiOS-Xbox.Provision.ps1")
        if head is not None:   # negative: the code the round-1 image ran never deleted AutoLogonCount or touched the hive
            self.assertNotIn("Set-MiOSUserValue", head)
            self.assertNotIn("-Name 'AutoLogonCount'", head)

    def test_pwsh_user_values_both_sides(self):
        fns = _ps_functions(_rd(PROV), ["Merge-MiOSKv", "Set-MiOSUserValue"], self.tmp)
        items = [
            {"key": "Software\\Microsoft\\Windows\\CurrentVersion\\Search", "name": "SearchboxTaskbarMode", "type": "REG_DWORD", "data": 0},
            {"key": "Software\\Microsoft\\Windows\\CurrentVersion\\Themes\\Personalize", "name": "AppsUseLightTheme", "type": "REG_DWORD", "data": 0},
            {"key": "Control Panel\\Desktop", "name": "Wallpaper", "type": "REG_SZ", "data": ""},
            {"key": "Software\\Microsoft\\DirectX\\UserGpuPreferences", "name": "DirectXUserGlobalSettings", "type": "REG_SZ", "data": 1, "kv_field": "AutoHDREnable"},
            {"key": "Software\\Microsoft\\Windows\\CurrentVersion\\Run", "name": "OneDriveSetup", "delete": True},
            {"key": "Software\\X", "name": "Big", "type": "REG_DWORD", "data": 4294967295},
            {"key": "Software\\X", "name": "Absent", "type": "REG_DWORD", "data": 1},
        ]
        script = FAKE_KEY + fns + r"""
$script:writes = 0
$store = @{
  'U\Software\Microsoft\Windows\CurrentVersion\Search' = @{ SearchboxTaskbarMode = @{ V = 2; K = 'DWord' } }
  'U\Software\Microsoft\Windows\CurrentVersion\Themes\Personalize' = @{ AppsUseLightTheme = @{ V = 1; K = 'DWord' } }
  'U\Control Panel\Desktop' = @{ Wallpaper = @{ V = 'C:\WINDOWS\web\wallpaper\Windows\img0.jpg'; K = 'String' } }
  'U\Software\Microsoft\DirectX\UserGpuPreferences' = @{ DirectXUserGlobalSettings = @{ V = 'SwapEffectUpgradeEnable=1;AutoHDREnable=0;'; K = 'String' } }
  'U\Software\Microsoft\Windows\CurrentVersion\Run' = @{ OneDriveSetup = @{ V = 'x'; K = 'String' } }
}
$root = [FakeKey]::new($store, 'U')
$items = Get-Content -Raw -LiteralPath $env:ITEMS | ConvertFrom-Json
$first = @($items | ForEach-Object { [bool](Set-MiOSUserValue $root $_) })
$w1 = $script:writes
$second = @($items | ForEach-Object { [bool](Set-MiOSUserValue $root $_) })
$bad = try { Set-MiOSUserValue $root ([pscustomobject]@{ key = 'K'; name = 'N'; type = 'REG_BINARY'; data = 1 }); 'no-throw' } catch { $_.Exception.Message }
ConvertTo-Json -Compress -Depth 4 -InputObject @{
  first = $first; second = $second; writes_first = $w1; writes_second = $script:writes - $w1; bad = $bad
  search = $store['U\Software\Microsoft\Windows\CurrentVersion\Search'].SearchboxTaskbarMode.V
  wallpaper = $store['U\Control Panel\Desktop'].Wallpaper.V
  hdr = $store['U\Software\Microsoft\DirectX\UserGpuPreferences'].DirectXUserGlobalSettings.V
  onedrive = $store['U\Software\Microsoft\Windows\CurrentVersion\Run'].ContainsKey('OneDriveSetup')
  big = $store['U\Software\X'].Big.V; big_kind = $store['U\Software\X'].Big.K
}
"""
        ip = os.path.join(self.tmp, "items.json")
        with open(ip, "w") as fh:
            json.dump(items, fh)
        r = _pwsh(script, self.tmp, {"ITEMS": ip})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        o = json.loads(r.stdout.strip().splitlines()[-1])
        # positive: every drifted value is set (the round-1 first-sign-in values), the deletion happens, the kv list
        # keeps its other fields, a full 32-bit DWORD survives, and a second pass changes nothing
        self.assertEqual(o["first"], [True, True, True, True, True, True, True])
        self.assertEqual(o["second"], [False] * 7)
        self.assertEqual(o["writes_second"], 0)
        self.assertEqual(o["search"], 0)
        self.assertEqual(o["wallpaper"], "")
        self.assertEqual(o["hdr"], "SwapEffectUpgradeEnable=1;AutoHDREnable=1;")
        self.assertFalse(o["onedrive"])
        self.assertEqual((o["big"], o["big_kind"]), (-1, "DWord"))
        # negative: an unsupported item is an error the caller records, never a silent skip
        self.assertIn("unsupported DEFAULT_USER value type REG_BINARY", o["bad"])


# ---------------------------------------------------------------------------
# 3. Hydrate (desktop user) never writes into the SYSTEM state dir
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAVE_PWSH and os.geteuid() != 0, "pwsh missing or running as root (a 0555 dir does not stop root)")
class TestHydrateOutputDir(unittest.TestCase):
    STUBS = "function Get-AppxPackage { param($Name, $AllUsers, $ErrorAction) $null }\n"

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-xbox-r1-hyd.")
        self.state = os.path.join(self.tmp, "ProgramData-MiOS")
        os.makedirs(self.state)
        self.spec = os.path.join(self.state, "xbox-spec.json")
        with open(self.spec, "w") as fh:
            json.dump({"projection": "guest", "spec": {"provision": {"hydrate_task": "MiOS\\MiOS-StoreHydrate"},
                                                      "store_hydrate": {"packages": [{"name": "Microsoft.GamingServices", "store_id": "9MWPM2CQNLHN"}]}}}, fh)
        os.chmod(self.state, 0o555)   # Protect-MiOSDir: Users may read, never write (round-1 ACL)
        self.local = os.path.join(self.tmp, "LocalAppData")
        os.makedirs(self.local)

    def tearDown(self):
        os.chmod(self.state, 0o755)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _run(self, text: str):
        # test shim: WindowsIdentity is Windows-only in pwsh; the same substitution is applied to both sides
        text = text.replace("[Security.Principal.WindowsIdentity]::GetCurrent().Name", "'MIOS\\user'")
        p = os.path.join(self.tmp, "prov.ps1")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        body = self.STUBS + f"& '{p}' -Stage Hydrate -SpecPath '{self.spec}' -StateDir '{self.state}'\n'HYDRATE-RC=' + $?\n"
        return _pwsh(body, tempfile.mkdtemp(dir=self.tmp), {"LOCALAPPDATA": self.local})

    def test_hydrate_both_sides(self):
        r = self._run(_rd(PROV))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("HYDRATE-RC=True", r.stdout, r.stdout + r.stderr)
        self.assertIn("winget not available yet", r.stdout)
        rec = os.path.join(self.local, "MiOS", "hydrate-state.json")
        self.assertTrue(os.path.isfile(rec), r.stdout + r.stderr)
        with open(rec, encoding="utf-8-sig") as fh:
            self.assertEqual(json.load(fh)["pending"], ["Microsoft.GamingServices"])
        self.assertTrue(os.path.isfile(os.path.join(self.local, "MiOS", "provision-hydrate.log")))
        self.assertEqual(sorted(os.listdir(self.state)), ["xbox-spec.json"])
        head = _git_head("usr/share/mios/windows/build/MiOS-Xbox.Provision.ps1")
        if head is None:
            self.skipTest("no git HEAD for the negative control")
        shutil.rmtree(os.path.join(self.local, "MiOS"))
        r = self._run(head)   # negative: the round-1 stage wrote its record into the read-only SYSTEM dir and died
        self.assertIn("HYDRATE-RC=False", r.stdout, r.stdout + r.stderr)
        self.assertRegex(r.stderr + r.stdout, r"(?i)denied|unauthori[sz]ed")
        self.assertFalse(os.path.exists(os.path.join(self.local, "MiOS", "hydrate-state.json")))
        self.assertEqual(sorted(os.listdir(self.state)), ["xbox-spec.json"])


# ---------------------------------------------------------------------------
# 4. OS-control server: an invalid console at logon is not fatal
# ---------------------------------------------------------------------------

@unittest.skipUnless(HAVE_PWSH, "pwsh not installed")
class TestOsControlConsole(unittest.TestCase):
    BROKEN_HOST = ("function Write-Host { throw [System.Management.Automation.Host.HostException]::new("
                   "'The Win32 internal error \"The handle is invalid\" 0x6 occurred while getting console output buffer information.') }\n")

    def _run(self, text: str, tmp: str):
        fns = _ps_functions(text, ["Write-MiosStatus", "Info", "Ok", "Warn"], tmp)
        script = "$ErrorActionPreference = 'Stop'\n" + self.BROKEN_HOST + fns + \
            "try { Info 'a'; Ok 'MiOS OS-control executor listening'; Warn 'c'; 'SURVIVED' } catch { 'DIED: ' + $_.Exception.Message }\n"
        r = _pwsh(script, tmp)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.strip().splitlines()[-1]

    def test_status_lines_both_sides(self):
        tmp = tempfile.mkdtemp(prefix="mios-xbox-r1-osc.")
        try:
            self.assertEqual(self._run(_rd(OSC), tmp), "SURVIVED")
            head = _git_head("usr/share/mios/windows/mios-oscontrol-server.ps1")
            if head is None:
                self.skipTest("no git HEAD for the negative control")
            out = self._run(head.lstrip("\ufeff"), tmp)   # negative: the round-1 server exited 1 at every logon
            self.assertTrue(out.startswith("DIED:") and "handle is invalid" in out, out)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# 5. the per-request agent relay is shipped, never registered as a task
# ---------------------------------------------------------------------------

class TestAgentRelayPerRequest(P.PayloadBase):
    def test_positive_shipped_but_no_task(self):
        comps = {c["name"]: c for c in self.x["components"]["ship"]}
        self.assertEqual(comps["mios-agent-relay"].get("invocation"), "per-request")
        self.assertNotIn("MiOS-AgentRelay", self.x["tasks"])
        self.assertNotIn("task:MiOS-AgentRelay", self.steps())
        self.assertNotIn("mios-agent-relay.exe'", "\n".join(l for l in self.setup.splitlines() if "New-ScheduledTaskAction" in l))
        self.assertTrue(os.path.isfile(os.path.join(self.pay, A._win_rel(self.dest), "mios-agent-relay.exe")))
        self.assertIn("task:MiOS-OsControl", self.steps())

    def test_negative_task_or_service_for_a_per_request_cli_is_refused(self):
        for kind, ent in (("tasks", {"component": "mios-agent-relay", "run_as": "system", "trigger": "startup"}),
                          ("services", {"component": "mios-agent-relay", "host": "self", "args": "", "display": "x"})):
            merged = copy.deepcopy(self.merged)
            merged["windows"]["xbox"][kind]["MiOS-AgentRelay"] = ent
            with self.assertRaises(R.SpecError) as cm:
                R.render(merged, P.LAYERS)
            self.assertIn(f"{kind}.MiOS-AgentRelay.component", str(cm.exception))
            self.assertIn("per-request", str(cm.exception))
        merged = copy.deepcopy(self.merged)
        for c in merged["windows"]["xbox"]["components"]["ship"]:
            if c["name"] == "mios-agent-relay":
                c["invocation"] = "daemon"
        with self.assertRaises(R.SpecError) as cm:
            R.render(merged, P.LAYERS)
        self.assertIn("invocation", str(cm.exception))


# ---------------------------------------------------------------------------
# 6. probe.py: WinRM as the elevation admin; venv re-exec through a symlinked python
# ---------------------------------------------------------------------------

def _load(path: str, name: str):
    sys.path.insert(0, os.path.dirname(PROBE))
    try:
        sp = importlib.util.spec_from_file_location(name, path)
        m = importlib.util.module_from_spec(sp)
        sp.loader.exec_module(m)
        return m
    finally:
        sys.path.pop(0)


class TestProbeAccount(unittest.TestCase):
    DOC = {"users": {"desktop": {"name": "user", "password": "d-secret"}, "admin": {"name": "admin", "password": "a-secret"}}}

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-xbox-r1-probe.")
        self.new = _load(PROBE, "probe_r1_new")
        head = _git_head("usr/libexec/mios/win/xbox/probe.py")
        self.old = None
        if head is not None:
            p = os.path.join(self.tmp, "probe_head.py")
            with open(p, "w") as fh:
                fh.write(head)
            self.old = _load(p, "probe_r1_head")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_credentials_both_sides(self):
        self.assertEqual(self.new._credentials(self.DOC), ("admin", "a-secret"))
        self.assertEqual(self.new._credentials({"users": {"desktop": {"name": "user", "password": "d"}}}), ("user", "d"))
        with self.assertRaises(Exception):
            self.new._credentials({"users": {"desktop": {"name": "user"}}})
        if self.old is None:
            self.skipTest("no git HEAD for the negative control")
        # negative: the round-1 probe signed WinRM in as the standard desktop user (InvalidCredentialsError)
        self.assertEqual(self.old._credentials(self.DOC), ("user", "d-secret"))

    def test_reexec_through_symlinked_venv_both_sides(self):
        real = os.path.join(self.tmp, "python3.real")
        with open(real, "w") as fh:
            fh.write("#!/bin/sh\n")
        os.chmod(real, 0o755)
        venv_py = os.path.join(self.tmp, "venv", "bin", "python")
        os.makedirs(os.path.dirname(venv_py))
        os.symlink(real, venv_py)
        doc = {"spec": {"probe": {"python": venv_py}}}

        def attempt(mod):
            calls = []
            saved = (sys.executable, os.environ.pop("MIOS_XBOX_PROBE_REEXEC", None), mod.os.execv)
            sys.executable = real            # running under the system python the venv links to
            mod.os.execv = lambda path, argv: calls.append(path)
            try:
                mod._reexec_with_winrm(doc, ["run"])
            finally:
                sys.executable = saved[0]
                mod.os.execv = saved[2]
                os.environ.pop("MIOS_XBOX_PROBE_REEXEC", None)
                if saved[1] is not None:
                    os.environ["MIOS_XBOX_PROBE_REEXEC"] = saved[1]
            return calls

        self.assertEqual(attempt(self.new), [venv_py])
        if self.old is None:
            self.skipTest("no git HEAD for the negative control")
        self.assertEqual(attempt(self.old), [], "the round-1 probe compared realpaths and never re-executed")


if __name__ == "__main__":
    unittest.main()
