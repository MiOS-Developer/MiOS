#!/usr/bin/env python3
# AI-hint: Two-sided tests for the MiOS-Xbox M1 reconcile fixes (runtime FAILs of baseline-boot.md the fixer lanes left open): no false-ok SetupComplete step (deny.false-ok-step: a state-changing cmdlet silenced with -ErrorAction SilentlyContinue in a Windows\Setup\Scripts stage), RDP proven reachable (fDenyTSConnections, inbox rules on every profile, TermService, a TCP listener), WinRM configured even when Enable-PSRemoting refuses and proven listening, the Looking Glass host start failure logged instead of swallowed, the cached Panther answer file (plain-text SSOT passwords) removed by the SYSTEM stage before RID 500 is disabled, the Verify record carrying listeners + answer-file cache, the offline default time zone baked by DISM /Set-TimeZone from the rendered region (baseline N-c: specialize zone switch under RealTimeIsUniversal ran the install 7 h slow), and the host tmpfiles entry for /var/lib/swtpm-localca matching swtpm-tools (tss:root 0750; root:root 0755 broke every new vTPM). Each positive control has a negative control proving the old code is caught. No network, no VM.
# AI-related: usr/libexec/mios/win/xbox/answer.py, usr/libexec/mios/win/xbox/conform.py, usr/share/mios/windows/build/New-MiOSXboxImage.ps1, usr/share/mios/windows/build/MiOS-Xbox.Provision.ps1, usr/share/mios/windows/mios-ssh-setup.ps1, usr/lib/tmpfiles.d/50-mios.conf, tests/test-mios-xbox-provisioning.py
"""Run: python3 -I tests/test-mios-xbox-reconcile.py -v   (temp dirs only; no network, no VM)"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
_spec = importlib.util.spec_from_file_location("mios_xbox_provisioning_tests", os.path.join(HERE, "test-mios-xbox-provisioning.py"))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)   # shares its fixture SSOT layers, fake inputs and payload base (no ~/.ssh read)
A, C, R = P.A, P.C, P.R
BUILD_PS = P.BUILD_PS
TMPFILES = os.path.join(ROOT, "usr", "lib", "tmpfiles.d", "50-mios.conf")


def _rd(p: str) -> str:
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def _git_head(rel: str):
    """The HEAD version of a tracked file (the pre-fix code), or None outside a git checkout."""
    if not shutil.which("git"):
        return None
    r = subprocess.run(["git", "-C", ROOT, "show", f"HEAD:{rel}"], capture_output=True, text=True)
    return r.stdout if r.returncode == 0 and r.stdout else None


def _pwsh(script_text: str, tmp: str, env=None):
    path = os.path.join(tmp, "t.ps1")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(script_text)
    return subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", path], capture_output=True, text=True,
                          timeout=180, env={**os.environ, **(env or {})})


def _gate_script(text: str, rel: str):
    obs = C.Observations()
    C.parse_script(text, rel, obs)
    return [f for f in obs.findings if f.id == "deny.false-ok-step"]


def _tz_baked(servicing: str) -> bool:
    """The servicing script sets the offline default time zone from the rendered region, before any hive is loaded."""
    m = re.search(r"dism\.exe[^\n]*\"/Image:\$mount\"[^\n]*\"/Set-TimeZone:\$tz\"", servicing, re.I)
    src = re.search(r"\$tz\s*=\s*\[string\]\(Need \$doc\.region\.timezone\b", servicing)
    load = servicing.find("reg.exe load")
    return bool(m and src and 0 <= m.start() < load and servicing.find("Mount-WindowsImage") < m.start())


def _swtpm_line_ok(text: str) -> bool:
    rows = [ln.split() for ln in text.splitlines() if ln.split()[1:2] == ["/var/lib/swtpm-localca"]]
    return len(rows) == 1 and rows[0][0] == "d" and rows[0][2:5] == ["0750", "tss", "root"]


# ---------------------------------------------------------------------------
# false-ok steps (baseline N-e, R3-8): the gate denies them; the rendered stage has none
# ---------------------------------------------------------------------------

class TestFalseOkGate(P.PayloadBase):
    STAGE = "Windows/Setup/Scripts/" + A.SETUP_SCRIPT

    def test_positive_rendered_stage_has_no_false_ok_step_and_gate_passes(self):
        self.assertEqual(_gate_script(self.setup, self.STAGE), [])
        rc, ids = self.gate(self.pay)
        self.assertNotIn("deny.false-ok-step", ids)
        self.assertEqual(rc, 0, sorted(ids))

    def test_negative_head_stage_literals_are_denied(self):
        head = _git_head("usr/libexec/mios/win/xbox/answer.py")
        if head is None:
            self.skipTest("no git checkout")
        lines = [m.group(1).replace("\\\\", "\\") for m in re.finditer(r'"((?:[^"\\]|\\.)*SilentlyContinue(?:[^"\\]|\\.)*)"', head)]
        hits = _gate_script("\n".join(lines), self.STAGE)
        txt = " | ".join(h.source for h in hits)
        self.assertGreaterEqual(len(hits), 8, txt)   # sshd_service x3, rdp_firewall x2, mios_daemons, qemu-ga/LG starts
        joined = "\n".join(lines)
        for old in ("Set-Service -Name sshd -StartupType Automatic -ErrorAction SilentlyContinue",
                    "Enable-NetFirewallRule -Name 'RemoteDesktop-UserMode-In-TCP' -ErrorAction SilentlyContinue"):
            self.assertIn(old, joined)
            self.assertEqual(len(_gate_script(old, self.STAGE)), 1, old)

    def test_negative_planted_line_fails_the_rendered_gate(self):
        d = self.copy_payload()
        p = os.path.join(d, "Windows", "Setup", "Scripts", A.SETUP_SCRIPT)
        with open(p, "a", encoding="utf-8", newline="") as fh:
            fh.write("\r\nInvoke-MiOSStep 'x' {\r\n    Start-Service -Name 'sshd' -EA 0\r\n}\r\n")
        rc, ids = self.gate(d)
        self.assertIn("deny.false-ok-step", ids)
        self.assertNotEqual(rc, 0)

    def test_probes_cleanups_and_non_stage_scripts_stay_allowed(self):
        for ok in ("Get-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' -ErrorAction SilentlyContinue | Disable-NetFirewallRule",
                   "Remove-NetFirewallRule -Name 'MiOS-WinRM-HTTP' -ErrorAction SilentlyContinue",
                   "if (-not (Get-LocalGroupMember -SID 'S-1-5-32-545' -Member 'u' -ErrorAction SilentlyContinue)) { Add-LocalGroupMember -SID 'S-1-5-32-545' -Member 'u' }",
                   "try { Start-Service -Name 'MSDTC' } catch { Write-MiOSLog 'x' }",
                   "Set-Service -Name 'x' -StartupType Automatic -ErrorAction Stop"):
            self.assertEqual(_gate_script(ok, self.STAGE), [], ok)
        silent = "Set-Service -Name sshd -StartupType Automatic -ErrorAction SilentlyContinue"
        self.assertEqual(_gate_script(silent, "Program Files/MiOS/Set-MiOSWallpaper.ps1"), [])   # not a Setup stage
        self.assertEqual(len(_gate_script(silent, "install.wim:/Windows/Setup/Scripts/mios-xbox-firstlogon.ps1")), 1)


# ---------------------------------------------------------------------------
# RDP / WinRM reachability, Looking Glass host, Panther answer-file cache
# ---------------------------------------------------------------------------

class TestReachabilityAndCleanup(P.PayloadBase):
    def test_positive_rdp_step_proves_the_listener(self):
        s = self.step("rdp")
        self.assertIn("fDenyTSConnections -ne 0", s)
        self.assertIn("WinStations\\RDP-Tcp')).PortNumber", s)
        self.assertIn("-Enabled True -Profile Any -RemoteAddress @('LocalSubnet', '100.64.0.0/10')", s)
        self.assertIn("Start-Service -Name 'TermService'", s)
        self.assertIn("Wait-MiOSListen $rdpPort 'Remote Desktop'", s)
        self.assertNotIn("rdp_firewall", self.steps())
        self.assertLess(self.steps().index("network_private"), self.steps().index("rdp"))

    def test_positive_winrm_falls_back_and_proves_the_listener(self):
        s = self.step("winrm")
        port = int(self.doc["winrm"]["port"])
        self.assertRegex(s, r"try \{ Enable-PSRemoting -SkipNetworkProfileCheck -Force \| Out-Null \}\s*\n\s*catch \{")
        self.assertIn("New-Item -Path WSMan:\\localhost\\Listener -Transport HTTP -Address * -Force", s)
        self.assertIn(f"Wait-MiOSListen {port} 'WinRM'", s)
        self.assertLess(s.index("New-NetFirewallRule -Name 'MiOS-WinRM-HTTP'"), s.index("Wait-MiOSListen"))

    def test_negative_head_winrm_and_rdp_had_no_listener_proof(self):
        head = _git_head("usr/libexec/mios/win/xbox/answer.py")
        if head is None:
            self.skipTest("no git checkout")
        self.assertNotIn("Wait-MiOSListen", head)
        self.assertIn("Invoke-MiOSStep 'rdp_firewall'", head)
        self.assertRegex(head, r'"    Enable-PSRemoting -SkipNetworkProfileCheck -Force \| Out-Null",')   # unguarded

    def test_positive_looking_glass_host_start_failure_is_logged_not_swallowed(self):
        s = self.step("looking_glass_host")
        svc = self.x["guest_tools"]["looking_glass_host"]["service"]
        self.assertIn(f"Set-Service -Name '{svc}' -StartupType Automatic\n", s)
        self.assertRegex(s, r"try \{ Start-Service -Name '" + re.escape(svc) + r"' \} catch \{ Write-MiOSLog \(")
        self.assertNotIn("SilentlyContinue", s)

    def test_positive_answer_file_cache_removed_before_rid500(self):
        names = self.steps()
        self.assertIn("answer_file_cache", names)
        self.assertLess(names.index("verify_task"), names.index("answer_file_cache"))
        self.assertEqual(names[-1], "finalize_rid500")
        s = self.step("answer_file_cache")
        for rel in ("Panther\\\\unattend.xml", "Panther\\\\unattend-original.xml", "System32\\\\Sysprep\\\\unattend.xml"):
            self.assertIn(rel.replace("\\\\", "\\"), s)
        self.assertIn("throw", s)

    def _run_lib(self, body: str, tmp: str, env=None):
        lib = A._stage_header(self.doc, "test", os.path.join(tmp, "setup.log"))
        lib[3] = "$MiOSSerialPort = 'COM9'"   # no port here: the writer degrades
        return _pwsh("\n".join(lib) + "\n" + body, tmp, env)

    def test_pwsh_answer_file_cache_both_sides(self):
        if not shutil.which("pwsh"):
            self.skipTest("pwsh not installed")
        body = "Invoke-MiOSStep 'answer_file_cache' {\n" + self.step("answer_file_cache") + "} | Out-Null\n" \
               "ConvertTo-Json -Compress -InputObject @{ state = $MiOSState['answer_file_cache'] }\n"
        # positive: the cache files are removed and the step is ok
        tmp = tempfile.mkdtemp(dir=self.tmp)
        root = os.path.join(tmp, "win")
        for rel in (("Panther", "unattend.xml"), ("Panther", "unattend-original.xml"), ("System32", "Sysprep", "unattend.xml")):
            os.makedirs(os.path.join(root, *rel[:-1]), exist_ok=True)
            with open(os.path.join(root, *rel), "w") as fh:
                fh.write("<unattend/>")
        r = self._run_lib(body, tmp, {"SystemRoot": root})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(json.loads(r.stdout.strip().splitlines()[-1])["state"], "ok")
        self.assertFalse(os.path.exists(os.path.join(root, "Panther", "unattend.xml")))
        self.assertFalse(os.path.exists(os.path.join(root, "System32", "Sysprep", "unattend.xml")))
        # negative: a cache entry that cannot be removed is a recorded failure, never ok
        tmp2 = tempfile.mkdtemp(dir=self.tmp)
        root2 = os.path.join(tmp2, "win")
        os.makedirs(os.path.join(root2, "Panther", "unattend.xml"))
        with open(os.path.join(root2, "Panther", "unattend.xml", "child"), "w") as fh:
            fh.write("x")
        r = self._run_lib(body, tmp2, {"SystemRoot": root2})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertTrue(json.loads(r.stdout.strip().splitlines()[-1])["state"].startswith("failed:"))

    def test_pwsh_wait_listen_both_sides(self):
        if not shutil.which("pwsh"):
            self.skipTest("pwsh not installed")
        tmp = tempfile.mkdtemp(dir=self.tmp)
        body = ("function Get-NetTCPConnection { param($State, $LocalPort, $ErrorAction) if ($LocalPort -eq 4242) { [pscustomobject]@{ LocalPort = 4242 } } }\n"
                "Invoke-MiOSStep 'up' { Wait-MiOSListen 4242 'Up' 4 } | Out-Null\n"
                "Invoke-MiOSStep 'down' { Wait-MiOSListen 4343 'Down' 3 } | Out-Null\n"
                "ConvertTo-Json -Compress -InputObject @{ up = $MiOSState['up']; down = $MiOSState['down'] }\n")
        r = self._run_lib(body, tmp)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertEqual(out["up"], "ok")
        self.assertEqual(out["down"], "failed: Down is not listening on TCP 4343 after 3 s")

    def test_verify_record_carries_listeners_and_answer_cache(self):
        prov = _rd(os.path.join(BUILD_PS, "MiOS-Xbox.Provision.ps1"))
        verify = prov.split("# Verify (SYSTEM)", 1)[1]   # Verify reconciles AutoLogonCount + user defaults (round-1), so no longer read-only
        self.assertIn("$names += 'sshd', 'TermService', 'WinRM'", verify)
        self.assertIn("listening = $listen; answer_file_cache = @($answerCache)", verify)
        for key in ("'rdp'", "'winrm'", "'ssh'"):
            self.assertIn(key, verify)
        retry = prov.split("if ($Stage -eq 'Retry') {", 1)[1].split("\n}\n", 1)[0]
        self.assertNotRegex(retry, r"Start-Service[^\n]*SilentlyContinue")
        head = _git_head("usr/share/mios/windows/build/MiOS-Xbox.Provision.ps1")
        if head is not None:   # negative: the pre-fix Verify recorded neither listeners nor the answer-file cache
            self.assertNotIn("answer_file_cache", head)
            self.assertNotIn("Get-NetTCPConnection", head)

    def test_guest_tool_retry_also_runs_once_after_setup(self):
        s = self.step("guest_tools_retry")
        self.assertIn("$t = New-ScheduledTaskTrigger -AtStartup", s)
        self.assertIn("$t2 = New-ScheduledTaskTrigger -Once -At ([DateTime]::Now.AddMinutes(3))", s)
        self.assertIn("-Trigger @($t, $t2)", s)
        head = _git_head("usr/libexec/mios/win/xbox/answer.py")
        if head is not None:   # negative: HEAD had no retry at all (a failed MSI was lost)
            self.assertNotIn("guest_tools_retry", head)

    def test_ssh_setup_waits_for_the_listener(self):
        s = _rd(os.path.join(ROOT, "usr", "share", "mios", "windows", "mios-ssh-setup.ps1"))
        self.assertRegex(s, r"while\(-not \(Get-NetTCPConnection -State Listen -LocalPort \(\[int\]\$settings\.port\)[^\n]*Start-Sleep")


# ---------------------------------------------------------------------------
# offline time zone (baseline N-c)
# ---------------------------------------------------------------------------

class TestOfflineTimeZone(P.PayloadBase):
    def test_positive_servicing_bakes_the_rendered_zone_before_the_hives_load(self):
        self.assertTrue(_tz_baked(_rd(os.path.join(BUILD_PS, "New-MiOSXboxImage.ps1"))))
        root = P.ET.fromstring(_rd(os.path.join(self.pay, "autounattend.xml")))
        tz = [e.text for e in root.iter(P.UNS + "TimeZone")]
        self.assertEqual(tz, [self.doc["region"]["timezone"]])   # the zone specialize applies = the zone baked offline
        guest = json.loads(_rd(os.path.join(self.pay, "ProgramData", "MiOS", "xbox-spec.json")))
        self.assertEqual(guest["region"]["timezone"], self.doc["region"]["timezone"])   # the worker reads it here

    def test_negative_head_servicing_left_the_stock_zone(self):
        head = _git_head("usr/share/mios/windows/build/New-MiOSXboxImage.ps1")
        if head is None:
            self.skipTest("no git checkout")
        self.assertFalse(_tz_baked(head))
        late = _rd(os.path.join(BUILD_PS, "New-MiOSXboxImage.ps1"))
        block = re.search(r"\n    \$tz = .*?Say \"offline default time zone \$tz\"\n", late, re.S).group(0)
        moved = late.replace(block, "\n").replace("    Say 'offline registry imported'\n", "    Say 'offline registry imported'\n" + block)
        self.assertFalse(_tz_baked(moved), "a zone set after the hives are loaded is not accepted")


# ---------------------------------------------------------------------------
# build.py: plan validates what the SYSTEM stage registers; the worker job carries the FOD set
# ---------------------------------------------------------------------------

def _shut_off(cmd, **kw):
    if "domstate" in cmd:
        return 0, "shut off\n"
    raise AssertionError(f"plan ran {cmd[:4]} (plan is read-only: domstate only)")


class TestBuildPlanAndJob(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import build as B   # noqa: E402  (same directory as answer.py, already on sys.path)
        cls.B = B
        cls.tmp = tempfile.mkdtemp(prefix="mios-xbox-reconcile-build.")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_positive_plan_reports_the_provisioning_shape(self):
        p = self.B.plan(P.LAYERS, _shut_off)
        self.assertFalse([q for q in p["problems"] if "wallpaper" in q or "services." in q or "pwsh" in q], p["problems"])
        self.assertEqual(p["services"], sorted(Fixture_doc()["spec"]["services"]))
        self.assertFalse(p["first_logon_commands"])
        self.assertEqual(p["capabilities_baked_offline"], {"OpenSSH.Server": "OpenSSH-Server-Package"})
        self.assertIn("pwsh", p["tools"])

    def test_negative_plan_names_a_missing_wallpaper_page_and_pwsh(self):
        frag = os.path.join(self.tmp, "no-page.toml")
        with open(frag, "w") as fh:
            fh.write('[theme.wallpaper]\nhtml_path = "/usr/share/mios/branding/no-such-living-wallpaper.html"\n')
        p = self.B.plan(P.LAYERS + [frag], _shut_off)
        self.assertTrue(any(q.startswith("windows.xbox.wallpaper.html_ref:") for q in p["problems"]), p["problems"])
        which = self.B.shutil.which
        self.B.shutil.which = lambda t: None if t == "pwsh" else which(t)
        try:
            p = self.B.plan(P.LAYERS, _shut_off)
        finally:
            self.B.shutil.which = which
        self.assertIn("host tool pwsh missing", p["problems"])

    def _build(self, with_fod: bool):
        B = self.B
        _, doc = P.Fixture.get()
        root = tempfile.mkdtemp(prefix="b.", dir=self.tmp)
        doc["spec"]["build"]["work_root"] = os.path.join(root, "work")
        b = B.Build(os.path.join(root, "build-x"), P.LAYERS)
        os.makedirs(b.reports)
        b.doc = doc
        os.makedirs(b.work, exist_ok=True)
        inp = P.fake_inputs(os.path.join(root, "in"), doc)
        for k in ("uup_dir", "converter_dir"):
            inp[k] = os.path.join(root, "in", k)
            os.makedirs(inp[k])
            with open(os.path.join(inp[k], "x"), "w") as fh:
                fh.write(k)
        if not with_fod:
            inp.pop("fod_dir")
        with open(os.path.join(b.work, "inputs.json"), "w") as fh:
            json.dump(inp, fh)
        os.makedirs(b.payload, exist_ok=True)
        with open(b.guest_spec_path, "w") as fh:
            json.dump(R.guest_projection(doc), fh)
        return b, inp

    def test_job_carries_the_fod_set_both_sides(self):
        b, inp = self._build(True)
        job = self.B.stage_job(b, "rs_prerelease")
        self.assertEqual(sorted(os.listdir(os.path.join(job, "fod"))), sorted(os.listdir(inp["fod_dir"])))
        self.assertTrue(any(n.endswith("~31bf3856ad364e35~amd64~~.cab") for n in os.listdir(os.path.join(job, "fod"))))
        b, _ = self._build(False)
        with self.assertRaises(self.B.BuildError) as cm:
            self.B.stage_job(b, "rs_prerelease")
        self.assertIn("capability FOD set was not fetched", str(cm.exception))


def Fixture_doc():
    return P.Fixture.get()[1]


# ---------------------------------------------------------------------------
# host: swtpm local CA directory (baseline N-a)
# ---------------------------------------------------------------------------

class TestSwtpmLocalCa(unittest.TestCase):
    def test_positive_tmpfiles_matches_swtpm_tools(self):
        self.assertTrue(_swtpm_line_ok(_rd(TMPFILES)))
        if shutil.which("rpm"):
            r = subprocess.run(["rpm", "-qlv", "swtpm-tools"], capture_output=True, text=True)
            row = [ln.split() for ln in r.stdout.splitlines() if ln.rstrip().endswith(" /var/lib/swtpm-localca")]
            if r.returncode == 0 and row:   # the package's own declaration: drwxr-x--- tss root
                self.assertEqual((row[0][0], row[0][2], row[0][3]), ("drwxr-x---", "tss", "root"))

    def test_negative_head_line_is_rejected(self):
        head = _git_head("usr/lib/tmpfiles.d/50-mios.conf")
        if head is None:
            self.skipTest("no git checkout")
        self.assertFalse(_swtpm_line_ok(head))
        self.assertFalse(_swtpm_line_ok("d /var/lib/swtpm-localca      0755 root root -\n"))


if __name__ == "__main__":
    _sock = socket.socket
    socket.socket = P._no_network  # type: ignore[assignment]
    try:
        unittest.main()
    finally:
        socket.socket = _sock
        shutil.rmtree(P._HOME, ignore_errors=True)
