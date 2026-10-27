#!/usr/bin/env python3
# AI-hint: Two-sided tests for the MiOS-Xbox provisioning fixes (fix-a M1, R3 items 1/2/4/5/6/9): privileged work is the SYSTEM SetupComplete stage, never a standard user's FirstLogonCommand (RID-500, persistent AutoLogon, hydrate + Verify tasks); the OpenSSH.Server FOD is resolved from the same UUP update, fetched under Microsoft's repository name, baked offline and proven in the image, and sshd is configured from SSOT inputs in the image (no GitHub, no Windows Update); FhsSync/miosd/wallpaperd/oscontrol/agent-relay are registered with what the image ships; the provisioning log reaches the host (SerialPort writer + <serial><log file>); no SetupType/CmdLine hook; guest tools are individual verified MSIs (gt MSI + SPICE agent carved from the Burn bundle by manifest SHA-1) and the network profile is Private before WinRM. Every positive control has a negative control proving the old behaviour is caught. No network, no VM, fixture credentials only.
# AI-related: usr/libexec/mios/win/xbox/answer.py, usr/libexec/mios/win/xbox/render_spec.py, usr/libexec/mios/win/xbox/conform.py, usr/libexec/mios/win/xbox/spec_items.py, usr/libexec/mios/win/xbox/fetch.py, usr/libexec/mios/win/xbox/resolve_latest.py, usr/libexec/mios/vfio/setup-looking-glass.py, usr/share/mios/windows/build/New-MiOSXboxImage.ps1, usr/share/mios/windows/build/MiOS-Xbox.Provision.ps1, usr/share/mios/windows/mios-ssh-setup.ps1, usr/share/mios/mios.toml
"""Run: python3 -I tests/test-mios-xbox-provisioning.py -v   (temp dirs only; no network, no VM)"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
XBOX = os.path.join(ROOT, "usr", "libexec", "mios", "win", "xbox")
sys.path.insert(0, XBOX)
import render_spec as R  # noqa: E402
import answer as A  # noqa: E402
import conform as C  # noqa: E402
import fetch as F  # noqa: E402
import resolve_latest as RL  # noqa: E402
import spec_items as S  # noqa: E402

VENDOR = os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
FIXTURE_IDENTITY = os.path.join(HERE, "fixtures", "mios-xbox", "fixture-identity.toml")
LAYERS = [VENDOR, FIXTURE_IDENTITY]
UNS = "{urn:schemas-microsoft-com:unattend}"
BUILD_PS = os.path.join(ROOT, "usr", "share", "mios", "windows", "build")
SLG_PATH = os.path.join(ROOT, "usr", "libexec", "mios", "vfio", "setup-looking-glass.py")
_spec = importlib.util.spec_from_file_location("setup_looking_glass_m1", SLG_PATH)
SLG = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(SLG)
OLD_COM1_RE = re.compile(r"AppendAllText\(\s*'\\\\\.\\COM1'", re.I)   # the .NET Framework device-path write that always throws


def _rd(p: str) -> str:
    with open(p, encoding="utf-8") as fh:
        return fh.read()


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _no_network(*a, **k):
    raise AssertionError("network access in an offline test")


_HOME = tempfile.mkdtemp(prefix="mios-xbox-m1-home.")   # no ~/.ssh keys: the fixture identity's SSOT key is the only one
R._operator_home = lambda: _HOME


class Fixture:
    merged = None
    doc = None

    @classmethod
    def get(cls):
        if cls.doc is None:
            cls.merged = R.mios_toml.load_merged(LAYERS)
            cls.doc = R.render(copy.deepcopy(cls.merged), LAYERS)
        return copy.deepcopy(cls.merged), copy.deepcopy(cls.doc)


def fake_inputs(tmp: str, doc) -> dict:
    """Verified-input stand-ins (fixture bytes) for every role answer.py stages, plus the FOD set."""
    nat = os.path.join(tmp, "native")
    os.makedirs(nat, exist_ok=True)
    rows = []
    for c in doc["spec"]["components"]["ship"]:
        if c["kind"] == "native":
            n = c["source"].split(":", 1)[1] + ".exe"
            b = f"fixture native {n}".encode()
            with open(os.path.join(nat, n), "wb") as fh:
                fh.write(b)
            rows.append({"name": n, "sha256": _sha(b)})
    with open(os.path.join(nat, "manifest.json"), "w") as fh:
        json.dump(rows, fh)
    dl = os.path.join(tmp, "dl")
    os.makedirs(dl, exist_ok=True)
    inputs = {"installers": {}}
    for role, fn in (("virtio_guest_tools", "virtio-win-gt-x64.msi"), ("spice_vdagent", "spice-vdagent-x64.msi"),
                     ("qemu_ga", "qemu-ga-x86_64.msi"), ("winfsp", "winfsp-9.9.99999.msi"),
                     ("webview2", "MicrosoftEdgeWebView2RuntimeInstallerX64.exe"),
                     ("looking_glass_host", "looking-glass-host.zip"), ("looking_glass_idd", "looking-glass-idd.zip")):
        p = os.path.join(dl, fn)
        with open(p, "wb") as fh:
            fh.write(f"fixture {role}".encode())
        inputs["installers"][role] = {"path": p, "sha256": _sha(f"fixture {role}".encode()), "signer": "Fixture Signer",
                                      "version": "fixture", "url": f"https://example.invalid/{fn}"}
    drv = os.path.join(tmp, "drivers")
    vw = doc["spec"]["drivers"]["virtio_win"]
    for inf in vw["inf"]:
        d = os.path.join(drv, inf, vw["os_dir"], vw["arch"])
        os.makedirs(d, exist_ok=True)
        with open(os.path.join(d, inf + ".inf"), "w") as fh:
            fh.write('[Version]\nClass=System\nProvider=%RH%\n[Strings]\nRH="Red Hat, Inc."\n')
    inputs["drivers_dir"] = drv
    fod = os.path.join(tmp, "fod")
    os.makedirs(fod, exist_ok=True)
    x = doc["spec"]
    for cap in x["features"]["capabilities_add"]:
        with open(os.path.join(fod, A.fod_cab_name(x["features"]["capability_fod"][cap]["package"], x["source"]["arch"])), "wb") as fh:
            fh.write(f"fixture fod {cap}".encode())
    with open(os.path.join(fod, x["features"]["fod_metadata"]), "wb") as fh:
        fh.write(b"fixture fod metadata")
    inputs["fod_dir"] = fod
    return inputs


class PayloadBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mios-xbox-m1.")
        cls.merged, cls.doc = Fixture.get()
        cls.inputs = fake_inputs(cls.tmp, cls.doc)
        cls.pay = os.path.join(cls.tmp, "payload")
        cls.summary = A.render_payload(cls.doc, cls.inputs, cls.pay, ROOT, os.path.join(cls.tmp, "native"))
        cls.spec = os.path.join(cls.tmp, "xbox-spec.json")
        with open(cls.spec, "w") as fh:
            json.dump(cls.doc, fh)
        cls.setup = _rd(os.path.join(cls.pay, "Windows", "Setup", "Scripts", A.SETUP_SCRIPT))
        cls.x = cls.doc["spec"]
        cls.dest = cls.x["components"]["dest_dir"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def gate(self, pay):
        rep = os.path.join(tempfile.mkdtemp(dir=self.tmp), "r.json")
        with contextlib.redirect_stdout(io.StringIO()):
            rc = C.run_rendered(self.spec, [pay, self.inputs["drivers_dir"]], rep, ROOT)
        with open(rep) as fh:
            return rc, {f["id"] for f in json.load(fh)["findings"]}

    def copy_payload(self):
        d = tempfile.mkdtemp(prefix="pay.", dir=self.tmp)
        shutil.rmtree(d)
        shutil.copytree(self.pay, d)
        return d

    def step(self, name: str) -> str:
        m = re.search(r"^Invoke-MiOSStep '" + re.escape(name) + r"' \{\n(.*?)^\}", self.setup, re.S | re.M)
        self.assertIsNotNone(m, f"no SYSTEM step {name}")
        return m.group(1)

    def steps(self):
        return re.findall(r"^Invoke-MiOSStep '([^']+)'", self.setup, re.M)


# ---------------------------------------------------------------------------
# item 1: privileged first-logon work runs as SYSTEM
# ---------------------------------------------------------------------------

class TestItem1SystemStage(PayloadBase):
    def test_positive_no_firstlogon_commands_and_privileged_steps_are_system(self):
        root = ET.parse(os.path.join(self.pay, "autounattend.xml")).getroot()
        self.assertIsNone(root.find(f".//{UNS}FirstLogonCommands"))
        self.assertEqual(sorted(os.listdir(os.path.join(self.pay, "Windows", "Setup", "Scripts"))), ["SetupComplete.cmd", A.SETUP_SCRIPT])
        names = self.steps()
        for s in ("autologon_persistent", "store_hydrate_task", "verify_task", "finalize_rid500"):
            self.assertIn(s, names)
        self.assertEqual(names[-1], "finalize_rid500", "RID 500 is disabled last, after the admin is proven")
        self.assertLess(names.index("groups"), names.index("finalize_rid500"))
        rid = self.step("finalize_rid500")
        self.assertIn("S-1-5-32-544", rid)
        self.assertIn("Disable-LocalUser", rid)
        self.assertIn("RID 500 is still enabled", rid)
        self.assertIn("Remove-ItemProperty -Path $wl -Name 'AutoLogonCount'", self.step("autologon_persistent"))
        hyd = self.step("store_hydrate_task")
        self.assertIn(f"-UserId '{self.doc['users']['desktop']['name']}' -LogonType Interactive -RunLevel Limited", hyd)
        self.assertIn("-Stage Hydrate", hyd)
        ver = self.step("verify_task")
        self.assertIn("-UserId 'SYSTEM' -LogonType ServiceAccount", ver)
        self.assertIn("-Stage Verify", ver)
        self.assertIn("& $Prov -Stage Verify", self.setup)
        cmd = _rd(os.path.join(self.pay, "Windows", "Setup", "Scripts", "SetupComplete.cmd"))
        self.assertIn(A.SETUP_SCRIPT, cmd)
        rc, ids = self.gate(self.pay)
        self.assertEqual(rc, 0, ids)

    def test_positive_verify_removes_hydrate_task_as_system(self):
        t = _rd(os.path.join(BUILD_PS, "MiOS-Xbox.Provision.ps1"))
        self.assertIn("Unregister-ScheduledTask -TaskPath $hydrateTask[0]", t)
        self.assertIn("provision-{0}.log", t)        # the user stage never appends to a SYSTEM-owned log
        hydrate = t.split("if ($Stage -eq 'Hydrate')", 1)[1].split("    return\n", 1)[0]
        self.assertNotIn("Unregister-ScheduledTask", hydrate)   # a standard user cannot delete a SYSTEM-registered task

    def test_negative_spec_refuses_first_logon_commands(self):
        merged, _ = Fixture.get()
        merged["windows"]["xbox"]["provision"]["first_logon_commands"] = True
        with self.assertRaises(R.SpecError) as cm:
            R.render(merged, LAYERS)
        self.assertIn("provision.first_logon_commands", str(cm.exception))
        doc = copy.deepcopy(self.doc)
        doc["spec"]["provision"]["first_logon_commands"] = True
        with self.assertRaises(A.PayloadError):
            A.render_answer_file(doc)

    def test_negative_old_firstlogon_design_is_caught_by_the_gate(self):
        """The R3 image: RID-500/AutoLogon/hydrate as FirstLogonCommands of the standard autologon user."""
        d = self.copy_payload()
        sp = os.path.join(d, "Windows", "Setup", "Scripts", A.SETUP_SCRIPT)
        body = _rd(sp)
        body = re.sub(r"^Invoke-MiOSStep 'finalize_rid500' \{\n.*?^\}\n", "", body, flags=re.S | re.M)
        with open(sp, "w") as fh:
            fh.write(body)
        with open(os.path.join(d, "Windows", "Setup", "Scripts", "mios-xbox-firstlogon.ps1"), "w") as fh:
            fh.write("Invoke-MiOSStep 'finalize_rid500' {\n    Get-LocalUser | Where-Object { $_.SID.Value -like '*-500' } | Disable-LocalUser\n}\n")
        ap = os.path.join(d, "autounattend.xml")
        xml = _rd(ap).replace("<OOBE>", '<FirstLogonCommands><SynchronousCommand wcm:action="add"><CommandLine>powershell.exe '
                              '-File "C:\\Windows\\Setup\\Scripts\\mios-xbox-firstlogon.ps1"</CommandLine><Order>1</Order>'
                              '</SynchronousCommand></FirstLogonCommands><OOBE>', 1)
        self.assertIn("FirstLogonCommands", xml)
        with open(ap, "w") as fh:
            fh.write(xml)
        rc, ids = self.gate(d)
        self.assertEqual(rc, 1)
        self.assertIn("users.rid500-not-system", ids)
        self.assertIn("users.firstlogon-standard-user", ids)

    def test_negative_rid500_missing_entirely(self):
        d = self.copy_payload()
        sp = os.path.join(d, "Windows", "Setup", "Scripts", A.SETUP_SCRIPT)
        with open(sp, "w") as fh:
            fh.write(re.sub(r"^Invoke-MiOSStep 'finalize_rid500' \{\n.*?^\}\n", "", _rd(sp), flags=re.S | re.M))
        _, ids = self.gate(d)
        self.assertIn("missing:users.rid500", ids)


# ---------------------------------------------------------------------------
# item 2: OpenSSH.Server baked offline, configured from SSOT inputs in the image
# ---------------------------------------------------------------------------

class TestItem2Ssh(PayloadBase):
    def test_positive_ssh_inputs_from_the_ssot_in_the_image(self):
        ssh = self.doc["ssh"]
        st = json.loads(_rd(os.path.join(self.pay, *A._win_rel(ssh["settings_path"]).split("/"))))
        self.assertEqual(st["port"], self.merged["ports"]["ssh"])
        self.assertEqual(st["windows"], self.merged["ssh"]["windows"])
        keys = _rd(os.path.join(self.pay, *A._win_rel(ssh["authorized_keys_path"]).split("/"))).splitlines()
        self.assertEqual(keys, [self.merged["auth"]["existing_ssh_key"]])
        s = self.step("ssh_setup")
        self.assertIn("-InboxOpenSsh", s)
        self.assertIn(ssh["settings_path"], s)
        self.assertNotRegex(self.setup, r"(?i)Invoke-WebRequest|Invoke-RestMethod|github\.com|releases/latest")
        self.assertLess(self.steps().index("capability:OpenSSH.Server"), self.steps().index("ssh_setup"))

    def test_positive_capability_fallback_is_local_only(self):
        c = self.step("capability:OpenSSH.Server")
        self.assertIn("-Source $Fod -LimitAccess", c)
        self.assertIn("Windows\\System32\\OpenSSH\\sshd.exe", c)
        arch = self.x["source"]["arch"]
        cab = A.fod_cab_name(self.x["features"]["capability_fod"]["OpenSSH.Server"]["package"], arch)
        self.assertEqual(cab, "OpenSSH-Server-Package~31bf3856ad364e35~amd64~~.cab")   # Microsoft's sample package name
        staged = {r["path"] for r in self.summary["components"]}
        self.assertIn(f"{A._win_rel(self.dest)}/fod/{cab}", staged)
        self.assertIn(f"{A._win_rel(self.dest)}/fod/{self.x['features']['fod_metadata']}", staged)

    def test_positive_servicing_bakes_offline_and_fails_closed(self):
        t = _rd(os.path.join(BUILD_PS, "New-MiOSXboxImage.ps1"))
        self.assertIn("Add-WindowsCapability -Path $mount -Name $c.Name -Source $FodDir -LimitAccess", t)
        self.assertIn("capability $cap not baked", t)
        self.assertIn("orphan Services key", t)
        self.assertNotIn("setupcomplete (online)", t)
        w = _rd(os.path.join(BUILD_PS, "MiOS-Xbox-Worker.ps1"))
        self.assertIn("FodDir = (Join-Path $job 'fod')", w)

    def test_negative_no_operator_key_or_a_private_key_fails_the_render(self):
        merged, _ = Fixture.get()
        merged["auth"]["existing_ssh_key"] = ""
        with self.assertRaises(R.SpecError) as cm:
            R.render(merged, LAYERS)
        self.assertIn("ssh.authorized_keys_key", str(cm.exception))
        merged, _ = Fixture.get()
        merged["auth"]["existing_ssh_key"] = "-----BEGIN OPENSSH PRIVATE KEY-----"
        with self.assertRaises(R.SpecError):
            R.render(merged, LAYERS)
        merged, _ = Fixture.get()
        merged["windows"]["xbox"]["ssh"]["install"] = "online"
        with self.assertRaises(R.SpecError):
            R.render(merged, LAYERS)

    def test_negative_payload_without_fod_or_ssh_inputs(self):
        inp = copy.deepcopy(self.inputs)
        inp.pop("fod_dir")
        with self.assertRaises(A.PayloadError):
            A.render_payload(self.doc, inp, os.path.join(self.tmp, "p-nofod"), ROOT, os.path.join(self.tmp, "native"))
        doc = copy.deepcopy(self.doc)
        doc.pop("ssh")
        with self.assertRaises(A.PayloadError):
            A.render_payload(doc, self.inputs, os.path.join(self.tmp, "p-nossh"), ROOT, os.path.join(self.tmp, "native"))

    def _image_obs(self, with_proof: bool, image_path: bool):
        obs = C.Observations()
        if with_proof:
            obs.files["windows/system32/openssh/sshd.exe"] = __file__
        obs.add_reg("HKEY_LOCAL_MACHINE\\SYSTEM\\ControlSet001\\Services\\sshd", "Start", "dword", 2, "SYSTEM:1")
        if image_path:
            obs.add_reg("HKEY_LOCAL_MACHINE\\SYSTEM\\ControlSet001\\Services\\sshd", "ImagePath", "expand_sz",
                        "C:\\Windows\\System32\\OpenSSH\\sshd.exe", "SYSTEM:2")
        obs.script_text.append(("image:/Windows/Setup/Scripts/x.ps1", "#"))
        return obs

    def test_image_gate_proves_the_baked_capability_both_sides(self):
        items = [i for i in S.items_from_rendered(self.doc, ROOT) if i["id"] in ("capabilities.OpenSSH.Server", "reg.settings.ssh.start")]
        self.assertEqual(next(i for i in items if i["kind"] == "capability")["proof"], "Windows/System32/OpenSSH/sshd.exe")
        _, f = C.evaluate(items, self._image_obs(True, True), "image")
        self.assertEqual([x.id for x in f], [])
        _, f = C.evaluate(items, self._image_obs(False, False), "image")   # the R3 image: client-only, orphan sshd key
        ids = {x.id for x in f}
        self.assertIn("missing:capabilities.OpenSSH.Server.offline", ids)
        self.assertIn("off-spec:reg.settings.ssh.start.orphan-service", ids)

    def test_resolver_records_the_fod_of_the_same_update_both_sides(self):
        cfg = {"fod_packages": {"OpenSSH.Server": "OpenSSH-Server-Package"}, "fod_metadata": "FoDMetadata_Client.cab"}

        class Http:
            def __init__(self, files):
                self.files = files

            def request(self, url, method="GET", follow=True):
                assert url == "https://api.example.invalid/get.php?id=u-1&noLinks=1", url
                return RL.Response(200, {}, json.dumps({"response": {"files": self.files}}).encode())

        def row(b):
            return {"sha256": hashlib.sha256(b).hexdigest(), "sha1": hashlib.sha1(b).hexdigest(), "size": len(b)}
        files = {"cabs_OpenSSH-Client-Package-amd64.cab": row(b"c"), "cabs_OpenSSH-Server-Package-amd64.cab": row(b"s"),
                 "FoDMetadata_Client.cab": row(b"m")}
        rows = RL.resolve_fod(cfg, Http(files), "https://api.example.invalid", "u-1", "amd64", sleep=lambda s: None)
        self.assertEqual([(r["name"], r["canonical"]) for r in rows],
                         [("cabs_OpenSSH-Server-Package-amd64.cab", "OpenSSH-Server-Package~31bf3856ad364e35~amd64~~.cab"),
                          ("FoDMetadata_Client.cab", "FoDMetadata_Client.cab")])
        files.pop("cabs_OpenSSH-Server-Package-amd64.cab")   # the edition list's client-only set (R3)
        with self.assertRaises(RL.ResolveError):
            RL.resolve_fod(cfg, Http(files), "https://api.example.invalid", "u-1", "amd64", sleep=lambda s: None)

    def test_fetch_stages_the_fod_under_its_repository_name_both_sides(self):
        body = b"fixture sshd fod"
        man = {"artifacts": {"windows_uup": {"uuid": "u-1", "files": [], "integrity": {"kind": "sha256-per-file"},
                                             "fod_files": [{"name": "cabs_OpenSSH-Server-Package-amd64.cab", "sha256": _sha(body),
                                                            "size": len(body), "canonical": "OpenSSH-Server-Package~31bf3856ad364e35~amd64~~.cab"}]}}}
        spec = {"spec": {"features": {"capabilities_add": ["OpenSSH.Server"]}, "resolve": {"uupdump_api": "https://api.example.invalid"}}}

        class Http:
            def request(self, url, method="GET", follow=True):
                return RL.Response(200, {}, json.dumps({"response": {"files": {"cabs_OpenSSH-Server-Package-amd64.cab": {
                    "sha256": _sha(body), "url": "https://dl.example.invalid/x.cab"}}}}).encode())

        class DL:
            tries, sleep = 1, staticmethod(lambda s: None)

            def __init__(self, data):
                self.data = data

            def get(self, url, dest, size=None):
                with open(dest, "wb") as fh:
                    fh.write(self.data)
        d = os.path.join(self.tmp, "fod-fetch")
        F.fetch_fod(man, spec, d, DL(body), Http())
        self.assertEqual(os.listdir(d), ["OpenSSH-Server-Package~31bf3856ad364e35~amd64~~.cab"])
        d2 = os.path.join(self.tmp, "fod-fetch-bad")
        with self.assertRaises(F.FetchError):
            F.fetch_fod(man, spec, d2, DL(b"tampered fod"), Http())
        self.assertEqual(os.listdir(d2), [])
        man["artifacts"]["windows_uup"]["fod_files"] = []
        with self.assertRaises(F.FetchError):
            F.fetch_fod(man, spec, os.path.join(self.tmp, "fod-none"), DL(body), Http())

    def test_inbox_ssh_setup_never_downloads(self):
        t = _rd(os.path.join(ROOT, "usr", "share", "mios", "windows", "mios-ssh-setup.ps1"))
        inbox = t.split("if($InboxOpenSsh){", 1)[1].split("} else {", 1)[0]
        self.assertNotRegex(inbox, r"Invoke-WebRequest|Invoke-RestMethod|Resolve-MiOSOpenSshRelease")
        if not shutil.which("pwsh"):
            self.skipTest("pwsh not installed")
        tmp = tempfile.mkdtemp(dir=self.tmp)
        with open(os.path.join(tmp, "settings.json"), "w") as fh:
            json.dump({"port": 8100, "windows": dict(self.merged["ssh"]["windows"], root=tmp)}, fh)
        with open(os.path.join(tmp, "keys"), "w") as fh:
            fh.write(self.merged["auth"]["existing_ssh_key"] + "\n")
        sysroot = os.path.join(tmp, "Windows")
        os.makedirs(sysroot)
        ps = ("function Invoke-WebRequest { throw 'NETWORK' }; function Invoke-RestMethod { throw 'NETWORK' }; "
              f"try {{ & '{ROOT}/usr/share/mios/windows/mios-ssh-setup.ps1' -SettingsPath '{tmp}/settings.json' "
              f"-AuthorizedKeysPath '{tmp}/keys' -InboxOpenSsh; exit 0 }} catch {{ Write-Output \"THROWN: $_\"; exit 9 }}")
        r = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                           timeout=120, env={**os.environ, "SystemRoot": sysroot})
        self.assertEqual(r.returncode, 9, r.stdout + r.stderr)
        self.assertIn("inbox OpenSSH server is not installed", r.stdout)   # no capability -> refuses, never downloads
        self.assertNotIn("NETWORK", r.stdout)


# ---------------------------------------------------------------------------
# item 4: MiOS componentry registered with what the image ships
# ---------------------------------------------------------------------------

class TestItem4Componentry(PayloadBase):
    def test_positive_services_and_tasks_point_at_shipped_files(self):
        d = A._win_rel(self.dest)
        fhs = self.step("service:MiOS-FhsSync")
        self.assertIn(f'\'"{self.dest}\\miosd.exe" fhs-sync --daemon --interval 15 --root C:\\\'', fhs)
        self.assertIn("Start-MiOSService 'MiOS-FhsSync'", fhs)
        self.assertIn(f'"{self.dest}\\mios-wallpaperd.exe"', self.step("service:MiOS-Wallpaper-Service"))
        self.assertIn(f'"{self.dest}\\MiOS-Daemon.exe"', self.step("service:miosd"))
        osc = self.step("task:MiOS-OsControl")
        self.assertIn("mios-oscontrol-server.ps1", osc)
        self.assertIn(f"-Port {self.merged['ports']['oscontrol']}", osc)
        self.assertIn("-LogonType Interactive", osc)
        self.assertIn(f"http add urlacl url=http://127.0.0.1:{self.merged['ports']['oscontrol']}/", osc)
        # the agent relay is a per-request CLI (round-1 boot: the startup task exited 2): shipped, never a task
        self.assertNotIn("task:MiOS-AgentRelay", self.steps())
        self.assertLess(self.steps().index("wallpaper_setup"), self.steps().index("service:MiOS-Wallpaper-Service"))
        tool = open(os.path.join(self.pay, d, "MiosServiceTool.exe"), "rb").read()
        self.assertEqual(open(os.path.join(self.pay, d, "MiOS-Daemon.exe"), "rb").read(), tool)
        cfg = _rd(os.path.join(self.pay, d, "MiOS-Daemon.cfg")).splitlines()
        self.assertEqual(cfg[0], f"{self.dest}\\miosd.exe")
        self.assertTrue(cfg[1].startswith("daemon "))
        page = os.path.join(self.pay, "Windows", "Web", "MiOS", "living-wallpaper.html")
        self.assertEqual(_sha(open(page, "rb").read()),
                         _sha(open(os.path.join(ROOT, "usr", "share", "mios", "branding", "living-wallpaper.html"), "rb").read()))
        for m in re.finditer(r"Test-Path -LiteralPath '([^']+)'\)\) \{ throw '[^']+ is not in the image'", self.setup):
            rel = A._win_rel(m.group(1))
            self.assertTrue(os.path.isfile(os.path.join(self.pay, *rel.split("/"))), f"{m.group(1)} is registered but not staged")

    def test_negative_old_wiring_is_gone_and_bad_spec_refused(self):
        for old in ("-Install -Config", "mios-oscontrol-server.exe", "C:\\usr\\bin\\miosd.exe", "sc.exe create"):
            self.assertNotIn(old, self.setup)
        merged, _ = Fixture.get()
        merged["windows"]["xbox"]["services"]["MiOS-FhsSync"]["component"] = "not-shipped"
        with self.assertRaises(R.SpecError):
            R.render(merged, LAYERS)
        merged, _ = Fixture.get()
        merged["windows"]["xbox"]["tasks"]["MiOS-OsControl"]["run_as"] = "admin"
        with self.assertRaises(R.SpecError):
            R.render(merged, LAYERS)
        tree = tempfile.mkdtemp(dir=self.tmp)
        os.makedirs(os.path.join(tree, "usr", "share", "mios", "windows"))
        with open(os.path.join(tree, "usr", "share", "mios", "windows", "MiOS-FhsSync.cfg"), "w") as fh:
            fh.write("C:\\usr\\bin\\miosd.exe\n")   # no argument line
        with self.assertRaises(A.PayloadError):
            A.service_binpath(self.doc, "MiOS-FhsSync", tree)


# ---------------------------------------------------------------------------
# item 5: provisioning log reachable from the host
# ---------------------------------------------------------------------------

class TestItem5HostLog(PayloadBase):
    def test_positive_serial_port_writer_everywhere(self):
        texts = {A.SETUP_SCRIPT: self.setup}
        for f in ("New-MiOSXboxImage.ps1", "MiOS-Xbox.Provision.ps1"):
            texts[f] = _rd(os.path.join(BUILD_PS, f))
        for name, t in texts.items():
            self.assertIn("System.IO.Ports.SerialPort", t, name)
            self.assertIsNone(OLD_COM1_RE.search(t), name)
        self.assertIn(f"$MiOSSerialPort = '{self.x['provision']['serial_port']}'", self.setup)
        cmd = _rd(os.path.join(self.pay, "Windows", "Setup", "Scripts", "SetupComplete.cmd"))
        self.assertIn("> COM1 2>nul", cmd)

    def test_negative_old_device_path_write_is_detected(self):
        old = "try { [System.IO.File]::AppendAllText('\\\\.\\COM1', \"$line`r`n\") } catch {}"
        self.assertIsNotNone(OLD_COM1_RE.search(old))

    def test_log_line_survives_a_missing_port(self):
        if not shutil.which("pwsh"):
            self.skipTest("pwsh not installed")
        tmp = tempfile.mkdtemp(dir=self.tmp)
        lib = A._stage_header(self.doc, "test", os.path.join(tmp, "setup.log"))
        lib[3] = "$MiOSSerialPort = 'COM9'"   # no such port here: the writer must degrade, never throw
        script = os.path.join(tmp, "t.ps1")
        with open(script, "w") as fh:
            fh.write("\n".join(lib) + "\nWrite-MiOSLog 'hello host' | Out-Null\nInvoke-MiOSStep 'boom' { throw 'x' } | Out-Null\n"
                     f"Save-MiOSState '{tmp}/state.json' | Out-Null\n")
        r = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", script], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        log = _rd(os.path.join(tmp, "setup.log"))
        self.assertIn("hello host", log)
        self.assertIn("boom FAILED: x", log)
        self.assertEqual(json.loads(_rd(os.path.join(tmp, "state.json")))["steps"]["boom"], "failed: x")

    def test_domain_profile_logs_the_serial_port_both_sides(self):
        cfg = SLG.lg_config({"vm": {"looking_glass": dict(SLG.LG_DEFAULTS, serial_log="/var/log/libvirt/qemu/{domain}-serial0.log")}})
        xml = _rd(os.path.join(ROOT, "usr", "libexec", "mios", "Xbox-AutoEnroll.xml"))
        bare = re.sub(r"\s*<log file=[^>]*/>", "", xml)
        self.assertIn("no host serial log", " ".join(SLG.profile_problems(bare, cfg)))
        out = SLG.apply_profile(bare, cfg)
        self.assertIn('<log file="/var/log/libvirt/qemu/Xbox-serial0.log" append="on"', out)
        self.assertEqual([p for p in SLG.profile_problems(out, cfg) if "serial" in p], [])
        self.assertEqual(SLG.apply_profile(out, cfg).count("<log "), 1)   # idempotent
        with self.assertRaises(ValueError):
            SLG.serial_log_path(dict(cfg, serial_log="/tmp/{domain}.log"), "Xbox")
        vendor = R.mios_toml.load_merged([VENDOR])
        self.assertEqual(vendor["vm"]["looking_glass"]["serial_log"], "/var/log/libvirt/qemu/{domain}-serial0.log")


# ---------------------------------------------------------------------------
# item 6: no SetupType/CmdLine hook
# ---------------------------------------------------------------------------

class TestItem6NoSetupHook(unittest.TestCase):
    HOOK_RE = re.compile(r"MIOS_SYS\\Setup'?\s+/v\s+(SetupType|CmdLine)", re.I)

    def test_positive_servicing_script_leaves_cmdline_alone(self):
        t = _rd(os.path.join(BUILD_PS, "New-MiOSXboxImage.ps1"))
        self.assertIsNone(self.HOOK_RE.search(t))
        self.assertNotIn("SetupType=2", t.replace("No SetupType/CmdLine hook", ""))
        self.assertIn("is hooked", t)
        obs = C.Observations()
        obs.add_reg("HKEY_LOCAL_MACHINE\\SYSTEM\\Setup", "CmdLine", "sz", "oobe\\windeploy.exe", "SYSTEM:1")
        _, f = C.evaluate([], obs, "image")
        self.assertEqual([x.id for x in f], [])
        self.assertIn("Setup", C._DENY_KEYS["system"])

    def test_negative_hooked_image_is_denied(self):
        old = "& reg.exe add 'HKLM\\MIOS_SYS\\Setup' /v CmdLine /t REG_SZ /d 'cmd.exe /c C:\\Windows\\Setup\\Scripts\\SetupComplete.cmd' /f"
        self.assertIsNotNone(self.HOOK_RE.search(old))
        obs = C.Observations()
        obs.add_reg("HKEY_LOCAL_MACHINE\\SYSTEM\\Setup", "CmdLine", "sz", "cmd.exe /c C:\\Windows\\Setup\\Scripts\\SetupComplete.cmd", "SYSTEM:1")
        _, f = C.evaluate([], obs, "image")
        self.assertIn("deny.setup-cmdline-hook", {x.id for x in f})


# ---------------------------------------------------------------------------
# item 9: individual guest-tool MSIs, network profile Private before WinRM
# ---------------------------------------------------------------------------

def _cab(files):
    """A minimal uncompressed MS-CAB (one folder, one CFDATA per file, checksum 0) for the Burn fixtures."""
    names = [n.encode() + b"\0" for n, _ in files]
    hdr_len, folder_len = 36, 8
    cffiles = b""
    off = 0
    for (n, data), nm in zip(files, names):
        cffiles += struct.pack("<IIHHHH", len(data), off, 0, 0x5A21, 0x0000, 0x20) + nm
        off += len(data)
    data_start = hdr_len + folder_len + len(cffiles)
    stream = b"".join(d for _, d in files)            # one stored (uncompressed) CFDATA block for the folder
    blocks = struct.pack("<IHH", 0, len(stream), len(stream)) + stream
    total = data_start + len(blocks)
    header = struct.pack("<4sIIIIIBBHHHHH", b"MSCF", 0, total, 0, hdr_len + folder_len, 0, 3, 1, 1, len(files), 0, 0, 0)
    folder = struct.pack("<IHH", data_start, 1, 0)
    return header + folder + cffiles + blocks


def _bundle(msis):
    """A Burn-shaped exe: PE stub, UX container (manifest '0'), attached container (a0.. payloads)."""
    pay, pkgs, att = [], [], []
    for i, (cond, body, sha1) in enumerate(msis):
        pid = f"vwi.p{i}"
        pay.append(f'<Payload Id="{pid}" FilePath="{pid}" Hash="{sha1 or hashlib.sha1(body).hexdigest().upper()}" '
                   f'Packaging="embedded" SourcePath="a{i}" Container="WixAttachedContainer"/>')
        pkgs.append(f'<MsiPackage Id="{pid}" InstallCondition="{cond}" Version="0.10.5"><PayloadRef Id="{pid}"/></MsiPackage>')
        att.append((f"a{i}", body))
    man = ('<BurnManifest xmlns="http://schemas.microsoft.com/wix/2008/Burn">' + "".join(pay)
           + "<Chain>" + "".join(pkgs) + "</Chain></BurnManifest>").encode()
    return b"MZ" + b"\0" * 510 + _cab([("0", man), ("u0", b"ux")]) + _cab(att)


class TestItem9GuestTools(PayloadBase):
    def test_positive_individual_msis_and_private_network_before_winrm(self):
        names = self.steps()
        gt = self.step("virtio_win_guest_tools")
        self.assertIn("virtio-win-gt-x64.msi", gt)
        self.assertIn("Invoke-MiOSMsi", gt)
        self.assertIn("spice-vdagent-x64.msi", self.step("spice_vdagent"))
        self.assertIn("Start-MiOSService 'spice-agent'", self.step("spice_vdagent"))
        self.assertIn("Get-WindowsDriver -Online", self.step("virtio_drivers"))
        self.assertNotIn("virtio-win-guest-tools.exe", self.setup)
        self.assertNotIn("/install', '/quiet'", self.setup)
        self.assertIn("'/l*v'", self.setup)
        self.assertIn("-NetworkCategory 'Private'", self.step("network_private"))
        self.assertLess(names.index("network_private"), names.index("winrm"))
        self.assertIn("Start-MiOSService 'WinRM'", self.step("winrm"))
        inst = {r for r in self.summary["installers"]}
        self.assertIn("spice_vdagent", inst)
        self.assertTrue(self.summary["installers"]["virtio_guest_tools"]["path"].endswith("/virtio-win-gt-x64.msi"))

    def test_negative_composite_bundle_refused_and_network_category_checked(self):
        doc = copy.deepcopy(self.doc)
        doc["spec"]["guest_tools"]["virtio_win_guest_tools"]["file"] = "virtio-win-guest-tools.exe"
        with self.assertRaises(A.PayloadError) as cm:
            A.installer_names(doc, self.inputs)
        self.assertIn("1603", str(cm.exception))
        merged, _ = Fixture.get()
        merged["windows"]["xbox"]["provision"]["network_category"] = "Public"
        with self.assertRaises(R.SpecError):
            R.render(merged, LAYERS)

    def test_positive_qemu_ga_com_plus_retry_and_driver_repair(self):
        names = self.steps()
        q = self.step("qemu_ga")
        self.assertLess(q.index("Start-MiOSDtc"), q.index("Invoke-MiOSMsi"))   # COM+ (qga-vss RegisterCom) needs MSDTC
        self.assertIn("Add-MiOSRetry 'qemu-ga'", q)
        self.assertIn("Add-MiOSRetry 'spice-vdagent'", self.step("spice_vdagent"))
        for s in ("virtio_win_guest_tools", "qemu_ga", "spice_vdagent"):   # drivers proven AFTER every MSI (rollback)
            self.assertLess(names.index(s), names.index("virtio_drivers"))
        v = self.step("virtio_drivers")
        self.assertIn("/add-driver (Join-Path $Drv '*.inf') /subdirs /install", v)
        r = self.step("guest_tools_retry")
        self.assertIn("-Stage Retry", r)
        self.assertIn("'MiOS-GuestToolsRetry'", r)
        d = A._win_rel(self.dest)
        vw = self.x["drivers"]["virtio_win"]
        for inf in vw["inf"]:
            self.assertTrue(os.path.isfile(os.path.join(self.pay, d, "drivers", inf, vw["os_dir"], vw["arch"], inf + ".inf")), inf)
        prov = _rd(os.path.join(BUILD_PS, "MiOS-Xbox.Provision.ps1"))
        retry = prov.split("if ($Stage -eq 'Retry') {", 1)[1].split("\n}\n", 1)[0]
        for needle in ("MSDTC", "Get-FileHash", "Unregister-ScheduledTask", "retry.json"):
            self.assertIn(needle, retry)

    def test_negative_failed_msi_is_queued_not_lost(self):
        if not shutil.which("pwsh"):
            self.skipTest("pwsh not installed")
        tmp = tempfile.mkdtemp(dir=self.tmp)
        lib = A._stage_header(self.doc, "test", os.path.join(tmp, "setup.log"))
        lib[3] = "$MiOSSerialPort = 'COM9'"
        script = os.path.join(tmp, "q.ps1")
        with open(script, "w") as fh:
            fh.write("\n".join(lib) + "\nInvoke-MiOSStep 'qemu_ga' { try { throw 'msiexec qemu-ga exited 1603 RegisterCom 0x8004E00F' } "
                     "catch { Add-MiOSRetry 'qemu-ga' 'C:\\x.msi' 'abc' 'QEMU-GA'; throw } } | Out-Null\n"
                     "ConvertTo-Json -Compress -InputObject @{ state = $MiOSState['qemu_ga']; retry = @($script:MiOSRetry) }\n")
        r = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-File", script], capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = json.loads(r.stdout.strip().splitlines()[-1])
        self.assertTrue(out["state"].startswith("failed: msiexec qemu-ga exited 1603"))
        self.assertEqual([e["name"] for e in out["retry"]], ["qemu-ga"])
        self.assertEqual(out["retry"][0]["service"], "QEMU-GA")
        inp = copy.deepcopy(self.inputs)
        inp.pop("drivers_dir")
        with self.assertRaises(A.PayloadError):   # no verified driver tree -> no repair source -> no payload
            A.render_payload(self.doc, inp, os.path.join(self.tmp, "p-nodrv"), ROOT, os.path.join(self.tmp, "native"))

    def test_burn_carve_both_sides(self):
        if not shutil.which("7z"):
            self.skipTest("7z not installed")
        agent64 = b"MSI\0 Spice Agent 0.10.0 (64-bit) \0body64"
        agent32 = b"MSI\0 Spice Agent 0.10.0 (32-bit) \0body32"
        drivers = b"MSI\0 Virtio-win installer for drivers \0"
        tmp = tempfile.mkdtemp(dir=self.tmp)
        good = os.path.join(tmp, "good.exe")
        with open(good, "wb") as fh:
            fh.write(_bundle([("VersionNT64", drivers, None), ("NOT VersionNT64", agent32, None), ("VersionNT64", agent64, None)]))
        out = F.carve_burn_msi(good, "Spice Agent", os.path.join(tmp, "w1"), os.path.join(tmp, "o", "spice.msi"))
        self.assertEqual(open(out, "rb").read(), agent64)
        bad = os.path.join(tmp, "bad.exe")   # manifest SHA-1 does not match the payload
        with open(bad, "wb") as fh:
            fh.write(_bundle([("VersionNT64", agent64, "0" * 40)]))
        with self.assertRaises(F.FetchError):
            F.carve_burn_msi(bad, "Spice Agent", os.path.join(tmp, "w2"), os.path.join(tmp, "o", "x.msi"))
        two = os.path.join(tmp, "two.exe")   # ambiguous
        with open(two, "wb") as fh:
            fh.write(_bundle([("VersionNT64", agent64, None), ("VersionNT64", agent64 + b"2", None)]))
        with self.assertRaises(F.FetchError):
            F.carve_burn_msi(two, "Spice Agent", os.path.join(tmp, "w3"), os.path.join(tmp, "o", "y.msi"))
        with self.assertRaises(F.FetchError):
            F.carve_burn_msi(good, "No Such Agent", os.path.join(tmp, "w4"), os.path.join(tmp, "o", "z.msi"))

    def test_rendered_stage_parses_in_powershell(self):
        if not shutil.which("pwsh"):
            self.skipTest("pwsh not installed")
        files = [os.path.join(self.pay, "Windows", "Setup", "Scripts", A.SETUP_SCRIPT),
                 os.path.join(BUILD_PS, "MiOS-Xbox.Provision.ps1"), os.path.join(BUILD_PS, "New-MiOSXboxImage.ps1"),
                 os.path.join(ROOT, "usr", "share", "mios", "windows", "mios-ssh-setup.ps1")]
        ps = ("$bad = 0; foreach ($f in (ConvertFrom-Json $env:FILES)) { $e = $null; "
              "[void][System.Management.Automation.Language.Parser]::ParseFile($f, [ref]$null, [ref]$e); "
              "if ($e.Count) { $bad++; Write-Output \"$f : $($e[0].Message) @$($e[0].Extent.StartLineNumber)\" } }; exit $bad")
        r = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                           timeout=300, env={**os.environ, "FILES": json.dumps(files)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)


if __name__ == "__main__":
    _sock = socket.socket
    socket.socket = _no_network  # type: ignore[assignment]
    try:
        unittest.main()
    finally:
        socket.socket = _sock
        shutil.rmtree(_HOME, ignore_errors=True)
