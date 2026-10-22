#!/usr/bin/env python3
# AI-hint: Two-sided tests for the MiOS-Xbox in-guest WinRM conformance probe (xb-18): the recorded output of a conforming guest evaluates all green against the fixture xbox-spec.json, the recorded legacy-VM output (left taskbar, search box, no QEMU-GA/virtio/components, legacy autologon, RID-500 enabled, no GPU record) is non-conforming with named findings, single planted defects (GPU step failed/mismatched/unexpected, component hash, family) each fail, the WinRM transport stages the read-only guest script in chunks without ever sending a credential, host-side checks are evaluated, and the PowerShell probe parses and covers every item kind without write cmdlets.
# AI-related: usr/libexec/mios/win/xbox/probe.py, usr/share/mios/windows/xbox/mios-xbox-probe.ps1, usr/libexec/mios/win/xbox/spec_items.py, tests/_mios_xbox_fixtures.py, tests/fixtures/mios-xbox/probe-conforming.recorded.json, tests/fixtures/mios-xbox/probe-legacy.recorded.json
"""MiOS-Xbox in-guest probe tests (recorded outputs; no VM, no network)."""

from __future__ import annotations

import base64
import contextlib
import copy
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _mios_xbox_fixtures as FX  # noqa: E402

P = FX.load_module("mios_xbox_probe", os.path.join(FX.XBOX_DIR, "probe.py"))
S = FX.S
PS1 = os.path.join(FX.ROOT, "usr", "share", "mios", "windows", "xbox", "mios-xbox-probe.ps1")


def _load(name):
    with open(os.path.join(FX.FIXTURES, name), encoding="utf-8") as fh:
        return json.load(fh)


def _ids(f):
    return {x.id for x in f}


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="mios-xbox-probe-test.")
        cls.doc = FX.load_fixture_spec()
        cls.root = FX.fixture_root(cls.tmp, cls.doc)
        cls.items = S.items_from_spec(cls.doc, cls.root)
        cls.good = _load("probe-conforming.recorded.json")
        cls.legacy = _load("probe-legacy.recorded.json")
        cls.pw = cls.doc["users"]["desktop"]["password"]

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def ev(self, probe, host=None):
        return P.evaluate(self.items, probe, host)

    def mutated(self, iid, value):
        p = copy.deepcopy(self.good)
        p["observations"][iid] = value
        return p


class TestRecorded(Base):
    def test_recordings_match_the_fixture_spec(self):
        ids = {i["id"] for i in self.items}
        self.assertEqual(ids, set(self.good["observations"]), "re-run tests/fixtures/mios-xbox/regen-recorded.py")
        self.assertEqual(ids, set(self.legacy["observations"]))

    def test_conforming_guest_is_green(self):
        results, f = self.ev(self.good)
        self.assertEqual([], [str(x) for x in f])
        self.assertFalse([r for r in results if r["status"] == "fail"])
        passed = {r["id"] for r in results if r["status"] == "pass"}
        for iid in ("reg.preferences.taskbar_align", "reg.preferences.search_box_mode", "reg.xbox.game_mode",
                    "store_hydrate.Microsoft.GamingServices", "guest_tools.qemu_ga", "drivers.virtio_win.viostor",
                    "components.miosd", "users.desktop", "users.rid500", "gpu.post_setup", "start.layout_family"):
            self.assertIn(iid, passed)

    def test_legacy_vm_is_non_conforming(self):
        _, f = self.ev(self.legacy)
        ids = _ids(f)
        for fid in ("off-spec:reg.preferences.taskbar_align", "deny.TaskbarAl=0", "deny.SearchboxTaskbarMode=1",
                    "missing:guest_tools.qemu_ga", "missing:guest_tools.spice_vdagent", "missing:drivers.virtio_win.viostor",
                    "missing:components.mios-wallpaperd", "users.missing:users.desktop", "users.rid500-enabled",
                    "missing:gpu.post_setup", "missing:store_hydrate.Microsoft.GamingServices", "off-spec:start.start2_bin"):
            self.assertIn(fid, ids)

    def test_cli_evaluate_exit_codes(self):
        spec = FX.write_json(os.path.join(self.tmp, "spec.json"), self.doc)
        for name, want in (("probe-conforming.recorded.json", 0), ("probe-legacy.recorded.json", 1)):
            out = io.StringIO()
            rep = os.path.join(self.tmp, name + ".report")
            with contextlib.redirect_stdout(out):
                rc = P.main(["--root", self.root, "evaluate", "--spec", spec, "--recorded",
                             os.path.join(FX.FIXTURES, name), "--report", rep])
            self.assertEqual(want, rc, out.getvalue()[-1500:])
            self.assertNotIn(self.pw, out.getvalue())
            with open(rep) as fh:
                self.assertNotIn(self.pw, fh.read())


class TestPlanted(Base):
    def test_taskbar_left(self):
        _, f = self.ev(self.mutated("reg.preferences.taskbar_align", {"present": True, "type": "dword", "value": 0}))
        self.assertIn("deny.TaskbarAl=0", _ids(f))

    def test_xbox_toggle_missing(self):
        iid = next(i["id"] for i in self.items if i["id"].startswith("reg.xbox.feature_overrides.xbox_fse_"))
        _, f = self.ev(self.mutated(iid, {"present": False}))
        self.assertIn(f"missing:{iid}", _ids(f))

    def test_feature_states(self):
        _, f = self.ev(self.mutated("features.Microsoft-Hyper-V-All", {"state": "Enabled"}))
        self.assertIn("off-spec:features.Microsoft-Hyper-V-All", _ids(f))
        p = copy.deepcopy(self.good)
        p["nested_virt"] = True
        p["observations"]["features.VirtualMachinePlatform"] = {"state": "Disabled"}
        _, f = self.ev(p)
        self.assertIn("missing:features.VirtualMachinePlatform", _ids(f))

    def test_guest_tool_stopped(self):
        _, f = self.ev(self.mutated("guest_tools.qemu_ga", {"service": {"present": True, "status": "Stopped"}}))
        self.assertIn("guest_tools.not-running:guest_tools.qemu_ga", _ids(f))

    def test_component_hash_and_task(self):
        _, f = self.ev(self.mutated("components.Set-MiOSWallpaper", {"present": True, "sha256": "0" * 64}))
        self.assertIn("off-spec:components.Set-MiOSWallpaper", _ids(f))
        _, f = self.ev(self.mutated("gpu.script", {"present": True, "task": {"present": False}}))
        self.assertIn("missing:gpu.script.task", _ids(f))

    def test_autologon_and_password(self):
        p = copy.deepcopy(self.good)
        p["autologon"]["default_user"] = "user"
        _, f = self.ev(p)
        self.assertIn("users.autologon", _ids(f))
        p = copy.deepcopy(self.good)
        p["autologon"]["password_sha256"] = S.sha256_text("not-the-ssot-value")
        _, f = self.ev(p)
        self.assertIn("users.password:users.desktop", _ids(f))
        p = copy.deepcopy(self.good)
        p["autologon"]["password_sha256"] = S.sha256_text(self.pw)
        _, f = self.ev(p)
        self.assertNotIn("users.password:users.desktop", _ids(f))
        p = copy.deepcopy(self.good)
        p["autologon"]["logon_count"] = 1
        _, f = self.ev(p)
        self.assertIn("users.autologon-persistent", _ids(f))

    def test_gpu_step_failed(self):
        o = copy.deepcopy(self.good["observations"]["gpu.post_setup"])
        o["record"]["status"] = "failed"
        o["record"]["devices"][0]["status"] = "failed"
        _, f = self.ev(self.mutated("gpu.post_setup", o))
        self.assertIn("gpu.step-failed", _ids(f))
        self.assertIn("gpu.not-installed", _ids(f))

    def test_gpu_driver_version_mismatch(self):
        o = copy.deepcopy(self.good["observations"]["gpu.post_setup"])
        o["record"]["devices"][0]["installed_driver_version"] = "31.0.15.1111"
        _, f = self.ev(self.mutated("gpu.post_setup", o))
        self.assertIn("gpu.driver-mismatch", _ids(f))

    def test_gpu_absent_vendor_installs_nothing(self):
        p = copy.deepcopy(self.good)
        p["display"] = [d for d in p["display"] if "VEN_1AF4" in d["device_id"]]
        p["observations"]["gpu.post_setup"] = {"present": True, "record": {"schema": 1, "status": "no-gpu", "devices": [], "skipped": [
            {"vendor_id": "1AF4", "device_id": "1050", "reason": "virtual display (skip_vendor_ids)"}]}}
        _, f = self.ev(p)
        self.assertEqual(set(), {x for x in _ids(f) if x.startswith("gpu.")})
        p["observations"]["gpu.post_setup"]["record"]["status"] = "ok"
        p["observations"]["gpu.post_setup"]["record"]["devices"] = copy.deepcopy(self.good["observations"]["gpu.post_setup"]["record"]["devices"])
        _, f = self.ev(p)
        self.assertIn("gpu.unexpected-install", _ids(f))

    def test_gpu_present_but_no_gpu_recorded(self):
        o = {"present": True, "record": {"schema": 1, "status": "no-gpu", "devices": [], "skipped": []}}
        _, f = self.ev(self.mutated("gpu.post_setup", o))
        self.assertIn("gpu.not-installed", _ids(f))

    def test_family_mismatch(self):
        p = copy.deepcopy(self.good)
        p["branch"] = "fixture_unknown_release"
        _, f = self.ev(p)
        self.assertIn("start.family-mismatch", _ids(f))

    def test_probe_errors_surface(self):
        p = self.mutated("drivers.virtio_win.netkvm", {"error": "Get-WindowsDriver failed"})
        _, f = self.ev(p)
        self.assertIn("probe.error:drivers.virtio_win.netkvm", _ids(f))
        p = copy.deepcopy(self.good)
        del p["observations"]["users.rid500"]
        _, f = self.ev(p)
        self.assertIn("probe.no-observation:users.rid500", _ids(f))

    def test_wrong_schema_rejected(self):
        p = copy.deepcopy(self.good)
        p["schema"] = "other"
        with self.assertRaises(S.SpecError):
            self.ev(p)


class TestHost(Base):
    def test_host_checks(self):
        calls = []

        def runner(cmd):
            calls.append(cmd)
            return (0, '{"return":{}}') if "guest-ping" in cmd[-1] else (1, "")
        shm = os.path.join(self.tmp, "shm")
        with open(shm, "wb") as fh:
            fh.truncate(128 * 1024 * 1024)
        h = P.host_checks("mios-xbox", [5930, 5900], shm, 128 * 1024 * 1024, runner=runner,
                          port_open=lambda host, port: port == 5930)
        self.assertTrue(h["guest-ping"]["ok"])
        self.assertTrue(h["port-5930"]["ok"])
        self.assertFalse(h["port-5900"]["ok"])
        self.assertTrue(h["shm"]["ok"])
        self.assertEqual("qemu-agent-command", calls[0][3])
        _, f = self.ev(self.good, h)
        self.assertEqual({"host.port-5900"}, _ids(f))
        h2 = P.host_checks("mios-xbox", [], shm, 64 * 1024 * 1024, runner=lambda c: (1, "error: Guest agent is not responding"))
        _, f = self.ev(self.good, h2)
        self.assertIn("host.guest-ping", _ids(f))
        self.assertIn("host.shm", _ids(f))


class FakeResult:
    def __init__(self, out=b"", rc=0, err=b""):
        self.std_out, self.status_code, self.std_err = out, rc, err


class FakeSession:
    def __init__(self, reply):
        self.cmds, self.reply = [], reply

    def run_ps(self, cmd):
        self.cmds.append(cmd)
        if "RequestB64" in cmd:
            return FakeResult(("WARNING: noise\r\n" + json.dumps(self.reply)).encode())
        return FakeResult()


class TestTransport(Base):
    def test_request_carries_no_secret_or_value(self):
        req = P.build_request(self.items)
        blob = json.dumps(req)
        self.assertEqual("mios-xbox-probe-request/1", req["schema"])
        self.assertEqual(self.doc["users"]["desktop"]["name"], req["desktop_user"])
        self.assertNotIn(self.pw, blob)
        self.assertNotIn("password_sha256", blob)
        self.assertNotIn('"value"', blob)

    def test_run_guest_stages_in_chunks_and_parses(self):
        sess = FakeSession(self.good)
        out = P.run_guest("http://192.0.2.1:5985/wsman", "u", self.pw, P.build_request(self.items),
                          script_path=PS1, session_factory=lambda: sess)
        self.assertEqual(self.good["observations"].keys(), out["observations"].keys())
        self.assertGreater(len(sess.cmds), 3)
        self.assertTrue(all(len(c) < 8000 for c in sess.cmds))
        self.assertFalse(any(self.pw in c for c in sess.cmds))
        self.assertIn("Remove-Item", sess.cmds[-1])
        staged = "".join(re.findall(r"-Value '([^']*)'", "\n".join(c for c in sess.cmds if ".req" not in c)))
        with open(PS1, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), base64.b64decode(staged).decode("utf-8"))

    def test_settings_from_ssot(self):
        st = P.probe_settings(self.doc)
        self.assertEqual(5985, st["winrm_port"])
        self.assertEqual("ntlm", st["transport"])
        self.assertGreater(st["read_timeout_s"], st["operation_timeout_s"])
        d = copy.deepcopy(self.doc)
        del d["spec"]["probe"]
        with self.assertRaises(S.SpecError):     # no SSOT transport -> no hidden default, nothing contacted
            P.run_guest("http://192.0.2.1:5985/wsman", "u", "p", P.build_request(self.items), script_path=PS1,
                        settings=P.probe_settings(d))
        hd = P.ssot_host_defaults(self.doc)     # live layered SSOT [vm.looking_glass] (read-only)
        self.assertEqual(self.doc["spec"]["vm"]["name"], hd["domain"])
        self.assertTrue(all(isinstance(p, int) and p > 0 for p in hd["ports"]))

    def test_credentials_from_spec_or_env(self):
        u, p = P._credentials(self.doc)
        self.assertEqual(self.doc["users"]["desktop"]["name"], u)
        self.assertEqual(self.pw, p)
        d = copy.deepcopy(self.doc)
        d["users"]["desktop"]["password"] = ""
        with self.assertRaises(S.SpecError):
            P._credentials(d)


class TestGuestScript(unittest.TestCase):
    def setUp(self):
        with open(PS1, encoding="utf-8") as fh:
            self.text = fh.read()

    @unittest.skipUnless(shutil.which("pwsh"), "pwsh not installed")
    def test_parses(self):
        quoted = "'" + PS1.replace("'", "''") + "'"
        r = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command",
                            f"$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile({quoted},[ref]$null,[ref]$e); "
                            "if ($e) { $e | ForEach-Object { $_.ToString() }; exit 1 }"],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)

    def test_covers_every_item_kind(self):
        for kind in S.KINDS:
            if kind == "file_hash":
                continue
            self.assertIn(f"'{kind}'", self.text, f"guest probe has no branch for kind {kind}")

    def test_read_only(self):
        code = "\n".join(ln for ln in self.text.splitlines() if not ln.lstrip().startswith("#"))
        for bad in ("Set-ItemProperty", "New-ItemProperty", "Remove-Item", "New-Item ", "Set-Content", "Add-Content",
                    "Enable-WindowsOptionalFeature", "Disable-WindowsOptionalFeature", "Set-Service", "Start-Service",
                    "Stop-Service", "Restart-", "reg.exe add", "reg.exe delete", "reg add", "reg delete",
                    "Set-LocalUser", "Enable-LocalUser", "Disable-LocalUser", "Register-ScheduledTask", "Out-File"):
            self.assertNotIn(bad, code, f"guest probe must be read-only ({bad})")

    def test_never_returns_a_credential(self):
        self.assertIn("password_sha256", self.text)
        self.assertRegex(self.text, r"\$dp = \$null")
        self.assertNotRegex(self.text, r"default_password\s*=\s*\$dp\b")


if __name__ == "__main__":
    unittest.main(verbosity=2)
