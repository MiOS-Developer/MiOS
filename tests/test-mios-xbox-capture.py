#!/usr/bin/env python3
# AI-hint: Two-sided tests for the MiOS-Xbox Start layout capture (xb-11) and the build-worker hardening + image-gate negative control it ships with: capture.json is rendered only from the spec's start.folders/start.pins policy items (no credential), a capture account colliding with a real account or a malformed one is refused; verify_capture accepts a consistent record and names each planted defect (other family, unknown family, tampered start2.bin, non-empty grid, policy not in effect); installed assets pass check_assets until start2.bin is altered; one worker session runs exchange -> define -> boot -> quiesce -> prepare -> reboot (new boot id) -> collect and always stops, copies out read-only and undefines, also when collect fails; stage_capture installs into the tree and reports, refusing a bad record; the worker XML strips SPICE audio, uses one CPU socket and no S3/S4 (planted SPICE audio / 8 sockets are refused); the gate's negative control must be NON-CONFORMING. No network, no VM, temp dirs only.
# AI-related: usr/libexec/mios/win/xbox/capture.py, usr/share/mios/windows/build/MiOS-Xbox-Capture.ps1, usr/libexec/mios/win/xbox/worker.py, usr/libexec/mios/win/xbox/build.py, tests/test-mios-xbox-build.py
"""Run: python3 -I tests/test-mios-xbox-capture.py -v   (memory-light; no network; temp dirs only)"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
XBOX = os.path.join(ROOT, "usr", "libexec", "mios", "win", "xbox")
BUILD_PS = os.path.join(ROOT, "usr", "share", "mios", "windows", "build")
sys.path.insert(0, XBOX)
import render_spec as R  # noqa: E402
import worker as W  # noqa: E402
import capture as CAP  # noqa: E402
import build as B  # noqa: E402

VENDOR = os.path.join(ROOT, "usr", "share", "mios", "mios.toml")
FIXTURE_IDENTITY = os.path.join(HERE, "fixtures", "mios-xbox", "fixture-identity.toml")
LAYERS = [VENDOR, FIXTURE_IDENTITY]
FAM = "rs_prerelease"

BASE_XML = """<domain type='kvm'>
  <name>mios-xbox-current</name><uuid>11111111-2222-3333-4444-555555555555</uuid>
  <memory unit='KiB'>8388608</memory><currentMemory unit='KiB'>8388608</currentMemory><vcpu placement='static'>8</vcpu>
  <os firmware='efi'><type arch='x86_64' machine='pc-q35-10.2'>hvm</type>
    <loader readonly='yes' type='pflash' format='raw'>/usr/share/edk2/ovmf/OVMF_CODE.fd</loader>
    <nvram template='/usr/share/edk2/ovmf/OVMF_VARS.fd' templateFormat='raw' format='raw'>NVRAM</nvram>
    <boot dev='hd'/><boot dev='cdrom'/></os>
  <features><acpi/><apic/></features>
  <cpu mode='host-passthrough' check='none'><topology sockets='1' dies='1' clusters='1' cores='4' threads='2'/></cpu>
  <devices>
    <emulator>/usr/bin/qemu-system-x86_64</emulator>
    <disk type='file' device='disk'><driver name='qemu' type='qcow2'/><source file='/var/lib/mios-build/vms/mios-xbox-current/disk.qcow2'/><target dev='sda' bus='sata'/></disk>
    <controller type='sata' index='0'/>
    <controller type='virtio-serial' index='0'/>
    <interface type='network'><mac address='52:54:00:01:00:aa'/><source network='default'/><model type='rtl8139'/></interface>
    <channel type='spicevmc'><target type='virtio' name='com.redhat.spice.0'/></channel>
    <input type='tablet' bus='usb'/>
    <graphics type='spice' port='5900' autoport='no' listen='127.0.0.1'/>
    <graphics type='vnc' port='-1' autoport='yes' listen='127.0.0.1'/>
    <sound model='ich9'/>
    <audio id='1' type='spice'/>
    <video><model type='virtio' heads='1' primary='yes'/></video>
    <hostdev mode='subsystem' type='pci' managed='yes'><source><address domain='0x0000' bus='0x01' slot='0x00' function='0x0'/></source></hostdev>
  </devices>
</domain>"""

_DOC = None


def doc():
    global _DOC
    if _DOC is None:
        merged = R.mios_toml.load_merged(LAYERS)
        _DOC = R.render(merged, LAYERS)
    return copy.deepcopy(_DOC)


def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _record(d, data: bytes, family=FAM, pins=None, policy=None):
    want = [{"key": r["key"], "name": r["name"], "value": r["data"]} for r in d["registry"] if r["owner"] in CAP.START_OWNERS]
    return {"schema": CAP.CAPTURE_SCHEMA, "status": "done", "family": family, "family_source": "BuildBranch",
            "build": "29683", "ubr": "1000", "edition": "Professional", "user": "mios-capture",
            "captured_at": "2026-10-17T00:00:00Z", "explorer_sessions": ["MIOS\\mios-capture"],
            "start2_sha256": _sha(data), "start2_size": len(data),
            "pinned_export": {"pinnedList": [] if pins is None else pins},
            "visible_places": None, "opened_start": False,
            "policy_observed": want if policy is None else policy, "neutralized_legacy": []}


def _out(tmp, rec, data):
    o = os.path.join(tmp, "out")
    os.makedirs(o, exist_ok=True)
    with open(os.path.join(o, "capture.json"), "w") as fh:
        json.dump(rec, fh)
    with open(os.path.join(o, "start2.bin"), "wb") as fh:
        fh.write(data)
    return o


def _tree(tmp, d):
    """A temp MiOS tree with the asset directories' parent, owned like the real one."""
    t = os.path.join(tmp, "tree")
    os.makedirs(os.path.join(t, "usr", "share", "mios", "windows"), exist_ok=True)
    return t


# ---------------------------------------------------------------------------
# job
# ---------------------------------------------------------------------------

class TestCaptureJob(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-xbox-capture-job.")
        self.d = doc()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_positive_job_is_the_spec_start_policy_and_credential_free(self):
        j = CAP.capture_job(self.d, FAM, os.path.join(self.tmp, "job"))
        files = sorted(os.listdir(os.path.join(self.tmp, "job")))
        self.assertEqual(files, ["MiOS-Xbox-Capture.ps1", "capture.json"])
        names = {r["name"] for r in j["registry"]}
        self.assertIn("ConfigureStartPins", names)
        for f in self.d["spec"]["start"]["folders_universe"]:
            self.assertIn(self.d["spec"]["start"]["folder_policy_prefix"] + f, names)
        pins = next(r for r in j["registry"] if r["name"] == "ConfigureStartPins")
        self.assertEqual(json.loads(pins["data"]), {"pinnedList": self.d["spec"]["start"]["pins"]})
        on = sorted(r["name"] for r in j["registry"] if r["name"].startswith("AllowPinnedFolder") and r["data"] == 1)
        self.assertEqual(on, sorted("AllowPinnedFolder" + f for f in self.d["spec"]["start"]["folders"]))
        self.assertEqual(j["family"], FAM)
        self.assertEqual(j["user"], self.d["spec"]["start"]["capture"]["user"])
        with open(os.path.join(self.tmp, "job", "capture.json")) as fh:
            text = fh.read()
        for secret in (self.d["users"]["desktop"]["password"], self.d["users"]["builtin_administrator"]["password"]):
            self.assertNotIn(secret, text)

    def test_negative_capture_user_collides_or_is_malformed(self):
        for bad in (self.d["users"]["desktop"]["name"], self.d["users"]["service_account"]["name"], "Administrator",
                    "bad name", "x" * 21, ""):
            d = copy.deepcopy(self.d)
            d["spec"]["start"]["capture"]["user"] = bad
            with self.assertRaises(CAP.CaptureError, msg=bad):
                CAP.capture_job(d, FAM, os.path.join(self.tmp, "job"))

    def test_negative_missing_capture_table_or_start_policy(self):
        d = copy.deepcopy(self.d)
        del d["spec"]["start"]["capture"]
        with self.assertRaises(CAP.CaptureError):
            CAP.capture_job(d, FAM, os.path.join(self.tmp, "job"))
        d = copy.deepcopy(self.d)
        d["registry"] = [r for r in d["registry"] if r["owner"] != "start.pins"]
        with self.assertRaises(CAP.CaptureError):
            CAP.capture_job(d, FAM, os.path.join(self.tmp, "job"))

    def test_negative_render_requires_capture_keys(self):
        merged = R.mios_toml.load_merged(LAYERS)
        del merged["windows"]["xbox"]["start"]["capture"]["settle_s"]
        with self.assertRaises(R.SpecError):
            R.render(merged, LAYERS)
        merged = R.mios_toml.load_merged(LAYERS)
        del merged["windows"]["xbox"]["gate"]["negative_controls"]
        with self.assertRaises(R.SpecError):
            R.render(merged, LAYERS)


# ---------------------------------------------------------------------------
# verify + install + check
# ---------------------------------------------------------------------------

class TestVerifyAndAssets(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-xbox-capture-verify.")
        self.d = doc()
        self.data = b"MiOS fixture start2.bin \x00\x01\x02"

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_positive_verify_install_check(self):
        o = _out(self.tmp, _record(self.d, self.data), self.data)
        rec, data, key = CAP.verify_capture(o, self.d, FAM)
        self.assertEqual((data, key), (self.data, FAM))
        tree = _tree(self.tmp, self.d)
        self.assertIn("not captured", CAP.check_assets(self.d, FAM, tree))          # before install
        p = CAP.install_assets(self.d, key, rec, data, tree, "mios-xbox-worker")
        self.assertIsNone(CAP.check_assets(self.d, FAM, tree))
        self.assertIsNone(CAP.check_assets(self.d, FAM.upper(), tree))             # BuildBranch casing
        with open(p["start2"], "rb") as fh:
            self.assertEqual(fh.read(), self.data)
        with open(p["visible_places"]) as fh:
            r = json.load(fh)
        self.assertEqual((r["family"], r["start2_sha256"], r["captured_on"]), (FAM, _sha(self.data), "mios-xbox-worker"))
        self.assertTrue(r["visible_places_note"])                                   # null VisiblePlaces is explained
        self.assertEqual(oct(os.stat(p["start2"]).st_mode & 0o777), "0o644")

    def test_negative_planted_defects_are_named(self):
        cases = {
            "other family": (_record(self.d, self.data, family="ge_release"), self.data, "not the image family"),
            "unknown family": (_record(self.d, self.data, family="zz_unknown"), self.data, "no [windows.xbox.start.layout_assets"),
            "tampered blob": (_record(self.d, self.data), self.data + b"!", "differs from its capture record"),
            "pinned grid": (_record(self.d, self.data, pins=[{"packagedAppId": "Microsoft.WindowsStore_8wekyb3d8bbwe!App"}]),
                            self.data, "pins"),
            "policy absent": (_record(self.d, self.data, policy=[]), self.data, "was not in effect"),
        }
        for name, (rec, data, expect) in cases.items():
            with self.subTest(name):
                o = _out(os.path.join(self.tmp, name.replace(" ", "_")), rec, data)
                fam = None if name == "unknown family" else FAM
                with self.assertRaises(CAP.CaptureError) as cm:
                    CAP.verify_capture(o, self.d, fam)
                self.assertIn(expect, str(cm.exception))
        rec = _record(self.d, self.data)
        rec["status"] = "failed"
        with self.assertRaises(CAP.CaptureError):
            CAP.verify_capture(_out(os.path.join(self.tmp, "failed"), rec, self.data), self.d, FAM)

    def test_negative_altered_installed_asset_refused(self):
        o = _out(self.tmp, _record(self.d, self.data), self.data)
        rec, data, key = CAP.verify_capture(o, self.d, FAM)
        tree = _tree(self.tmp, self.d)
        p = CAP.install_assets(self.d, key, rec, data, tree)
        with open(p["start2"], "ab") as fh:
            fh.write(b"tamper")
        self.assertIn("differs from its capture record", CAP.check_assets(self.d, FAM, tree))
        os.unlink(p["visible_places"])
        self.assertIn("not captured", CAP.check_assets(self.d, FAM, tree))
        self.assertIn("has no [windows.xbox.start.layout_assets", CAP.check_assets(self.d, "zz_unknown", tree))


# ---------------------------------------------------------------------------
# worker session (fake libvirt + WinRM)
# ---------------------------------------------------------------------------

class CaptureSession:
    def __init__(self, boot, fail_collect=False):
        self.boot, self.fail_collect, self.scripts = boot, fail_collect, []

    def run_ps(self, script):
        self.scripts.append(script)
        rc, out = 0, "5"
        if script == W.BOOT_ID_PS:
            out = self.boot
        elif "quiesced" in script:
            out = "quiesced wuauserv=Stopped"
        elif "-Phase prepare" in script:
            out = '{"family":"rs_prerelease"}'
        elif "-Phase collect" in script:
            rc, out = (1, "") if self.fail_collect else (0, '{"status":"done"}')
        elif "shutdown.exe" in script:
            out = "rebooting"
        return type("R", (), {"status_code": rc, "std_out": out.encode(), "std_err": b"capture failed" if rc else b""})()


class TestRunCapture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-xbox-capture-run.")
        self.d = doc()
        self.cfg = W.WorkerConfig.from_spec(self.d, os.path.join(self.tmp, "w"))
        self.cfg.boot_timeout_s = 5
        nv = os.path.join(self.tmp, "VARS.fd")
        open(nv, "w").close()
        self.base = BASE_XML.replace("NVRAM", nv)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _runner(self):
        calls = []

        def run(cmd, **kw):
            calls.append(cmd)
            if cmd[0] == "virsh" and cmd[3] == "domstate":
                if cmd[4] == self.cfg.name:
                    return (0, "shut off\n") if any(c[:4] == ["virsh", "-c", W.CONNECT, "define"] for c in calls) else (1, "none")
                return 0, "shut off\n"
            if cmd[0] == "virsh" and cmd[3] == "dumpxml":
                return 0, self.base
            if cmd[0] == "virsh" and cmd[3] == "domifaddr":
                return 0, " vnet0  52:54:00:01:00:aa  ipv4  192.0.2.10/24\n"
            if cmd[0] == "qemu-img":
                open(cmd[-1] if cmd[-1].endswith(".qcow2") else cmd[-2], "w").close()
            return 0, ""
        run.calls = calls
        return run

    def _go(self, fail_collect=False):
        job = os.path.join(self.tmp, "job")
        CAP.capture_job(self.d, FAM, job)
        r = self._runner()
        sessions = []

        def factory(cfg, ip):
            s = CaptureSession(f"boot-{min(len(sessions), 1)}", fail_collect)
            sessions.append(s)
            return s
        orig = socket.create_connection
        socket.create_connection = lambda *a, **k: contextlib.nullcontext()   # type: ignore
        try:
            res = CAP.run_capture(self.cfg, job, os.path.join(self.tmp, "copy"), r, factory, lambda s: None,
                                  {"mem": lambda: 64.0, "wd": lambda: True})
        finally:
            socket.create_connection = orig  # type: ignore
        return res, r.calls, sessions, job

    def test_positive_sequence_reboot_and_cleanup(self):
        res, calls, sessions, job = self._go()
        joined = "\n".join(" ".join(c[:4]) for c in calls)
        self.assertLess(joined.index("guestfish --rw"), joined.index(f"virsh -c {W.CONNECT} define"))
        self.assertLess(joined.index(f"virsh -c {W.CONNECT} define"), joined.index(f"virsh -c {W.CONNECT} start"))
        self.assertIn("guestfish --ro", joined)
        self.assertIn(f"virsh -c {W.CONNECT} undefine", joined)
        scripts = [s for sess in sessions for s in sess.scripts]
        order = [i for i, s in enumerate(scripts) if "quiesced" in s or "-Phase prepare" in s or "shutdown.exe" in s or "-Phase collect" in s]
        kinds = ["q" if "quiesced" in scripts[i] else "p" if "-Phase prepare" in scripts[i] else "r" if "shutdown.exe" in scripts[i] else "c" for i in order]
        self.assertEqual(kinds, ["q", "p", "r", "c"])
        collect_sess = next(s for s in sessions if any("-Phase collect" in x for x in s.scripts))
        self.assertEqual(collect_sess.boot, "boot-1")                    # collected from the NEW boot
        self.assertFalse(os.path.exists(job))
        self.assertEqual(res, os.path.join(self.tmp, "copy", "out"))
        for p in (self.cfg.overlay, self.cfg.exchange, self.cfg.nvram):
            self.assertFalse(os.path.exists(p), p)

    def test_negative_collect_failure_still_cleans_up(self):
        with self.assertRaises(W.WorkerError) as cm:
            self._go(fail_collect=True)
        self.assertIn("capture failed", str(cm.exception))
        self.assertFalse(os.path.exists(self.cfg.overlay))
        self.assertFalse(os.path.exists(self.cfg.exchange))

    def test_negative_reboot_that_never_happens_fails(self):
        sess = CaptureSession("boot-0")
        orig = socket.create_connection
        socket.create_connection = lambda *a, **k: contextlib.nullcontext()   # type: ignore
        try:
            with self.assertRaises(W.WorkerError) as cm:
                W.reboot_and_wait(self.cfg, sess, self._runner(), sleep=lambda s: None,
                                  session_factory=lambda c, ip: CaptureSession("boot-0"))
        finally:
            socket.create_connection = orig  # type: ignore
        self.assertIn("did not come back", str(cm.exception))

    def test_locate_trims_null_drive_letter_and_assigns_if_missing(self):
        self.assertIn("Trim([char]0)", W._LOCATE)
        self.assertIn("Add-PartitionAccessPath -AssignDriveLetter", W._LOCATE)

    def test_run_exchange_script_refuses_bad_names(self):
        for bad in ("../x.ps1", "a b.ps1", "x.cmd", "x.ps1; rm"):
            with self.assertRaises(W.WorkerError):
                W.run_exchange_script(CaptureSession("b"), bad)


# ---------------------------------------------------------------------------
# build stage + worker XML hardening + negative-control gate
# ---------------------------------------------------------------------------

class TestStageCapture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-xbox-capture-stage.")
        self.d = doc()
        self.d["spec"]["build"]["work_root"] = os.path.join(self.tmp, "work")
        self.b = B.Build(os.path.join(self.tmp, "build-x"), LAYERS)
        os.makedirs(self.b.reports)
        self.b.doc = self.d
        self.tree = _tree(self.tmp, self.d)
        self.data = b"MiOS fixture start2.bin"
        self.orig = CAP.run_capture

    def tearDown(self):
        CAP.run_capture = self.orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _fake(self, rec):
        def fake(cfg, job, dest, *a, **k):
            self.assertTrue(os.path.isfile(os.path.join(job, "capture.json")))
            return _out(dest, rec, self.data)
        CAP.run_capture = fake

    def test_positive_stage_installs_and_reports(self):
        self._fake(_record(self.d, self.data))
        with contextlib.redirect_stdout(io.StringIO()):
            key = B.stage_capture(self.b, FAM, tree=self.tree)
        self.assertEqual(key, FAM)
        self.assertTrue(os.path.isfile(os.path.join(self.b.reports, "start-capture.json")))
        self.assertIsNone(CAP.check_assets(self.d, FAM, self.tree))
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(B.stage_family(self.b, FAM, tree=self.tree), FAM)

    def test_negative_bad_record_installs_nothing(self):
        self._fake(_record(self.d, self.data, pins=[{"desktopAppLink": "x.lnk"}]))
        with self.assertRaises(CAP.CaptureError):
            B.stage_capture(self.b, FAM, tree=self.tree)
        self.assertIn("not captured", CAP.check_assets(self.d, FAM, self.tree))
        with self.assertRaises(B.BuildError) as cm:
            B.stage_family(self.b, FAM, tree=self.tree)
        self.assertIn("no fallback blob", str(cm.exception))


class TestWorkerXmlHardening(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-xbox-capture-xml.")
        self.cfg = W.WorkerConfig.from_spec(doc(), self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_positive_no_audio_one_socket_no_sleep(self):
        root = ET.fromstring(W.generate_worker_xml(BASE_XML, self.cfg))
        self.assertFalse(root.findall(".//devices/audio"))
        self.assertFalse(root.findall(".//devices/sound"))
        self.assertEqual([g.get("type") for g in root.findall(".//devices/graphics")], ["vnc"])
        t = root.find("./cpu/topology")
        self.assertEqual((t.get("sockets"), t.get("cores"), t.get("threads")), ("1", str(self.cfg.vcpus), "1"))
        self.assertEqual({e.tag: e.get("enabled") for e in root.find("pm")}, {"suspend-to-mem": "no", "suspend-to-disk": "no"})
        if shutil.which("virt-xml-validate"):
            p = os.path.join(self.tmp, "w.xml")
            ET.ElementTree(root).write(p)
            r = subprocess.run(["virt-xml-validate", p, "domain"], capture_output=True, text=True, timeout=120)
            self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_qemu_access_grants_traverse_and_rw_on_worker_files(self):
        base = os.path.join(self.tmp, "var", "lib", "mios-build", "xbox", "build-1", "worker")
        os.makedirs(base, mode=0o700)
        cfg = W.WorkerConfig.from_spec(doc(), base)
        for p in (cfg.overlay, cfg.exchange, cfg.nvram):
            with open(p, "wb") as fh:
                fh.write(b"x")
            os.chmod(p, 0o600)
        W._qemu_access(cfg)
        if os.geteuid() == 0:
            for p in (cfg.overlay, cfg.exchange, cfg.nvram):
                self.assertEqual(oct(os.stat(p).st_mode & 0o777), "0o660")

    def test_negative_planted_spice_audio_and_sockets(self):
        root = ET.fromstring(W.generate_worker_xml(BASE_XML, self.cfg))
        a = ET.SubElement(root.find("devices"), "audio", {"id": "1", "type": "spice"})
        with self.assertRaises(W.WorkerError):
            W.validate_worker_xml(root, self.cfg)
        root.find("devices").remove(a)
        root.find("./cpu/topology").set("sockets", "8")
        with self.assertRaises(W.WorkerError):
            W.validate_worker_xml(root, self.cfg)


class TestNegativeControlGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="mios-xbox-capture-neg.")
        self.d = doc()
        self.b = B.Build(os.path.join(self.tmp, "build-x"), LAYERS)
        os.makedirs(self.b.reports)
        self.iso = os.path.join(self.tmp, "legacy.iso")
        open(self.iso, "w").close()
        self.d["spec"]["gate"]["negative_controls"] = [self.iso]
        self.b.doc = self.d
        self.orig = B._iso_gate

    def tearDown(self):
        B._iso_gate = self.orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_positive_legacy_non_conforming(self):
        B._iso_gate = lambda b, iso, rep, index=0: 1
        with contextlib.redirect_stdout(io.StringIO()):
            r = B.stage_gate_negative(self.b)
        self.assertEqual(list(r), [self.iso])

    def test_negative_gate_that_passes_legacy_fails_the_build(self):
        B._iso_gate = lambda b, iso, rep, index=0: 0
        with self.assertRaises(B.BuildError) as cm:
            B.stage_gate_negative(self.b)
        self.assertIn("does not bite", str(cm.exception))
        self.d["spec"]["gate"]["negative_controls"] = []
        with self.assertRaises(B.BuildError):
            B.stage_gate_negative(self.b)
        self.d["spec"]["gate"]["negative_controls"] = [os.path.join(self.tmp, "missing.iso")]
        with self.assertRaises(B.BuildError):
            B.stage_gate_negative(self.b)


class TestCapturePowerShell(unittest.TestCase):
    def setUp(self):
        with open(os.path.join(BUILD_PS, CAP.SCRIPT), encoding="utf-8") as fh:
            self.t = fh.read()

    def test_parses(self):
        if not shutil.which("pwsh"):
            self.skipTest("pwsh not installed")
        ps = ("$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile($env:F, [ref]$null, [ref]$e); "
              "if ($e.Count) { Write-Output \"$($e[0].Message) @$($e[0].Extent.StartLineNumber)\"; exit 1 }")
        r = subprocess.run(["pwsh", "-NoProfile", "-NonInteractive", "-Command", ps], capture_output=True, text=True,
                           timeout=300, env={**os.environ, "F": os.path.join(BUILD_PS, CAP.SCRIPT)})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_family_refusal_precedes_any_change_and_no_secret_is_emitted(self):
        self.assertLess(self.t.index("capture refused"), self.t.index("New-LocalUser"))
        self.assertLess(self.t.index("capture refused"), self.t.index("Remove-ItemProperty"))
        self.assertIn("RandomNumberGenerator", self.t)
        self.assertIn("$secret = $null", self.t)
        self.assertIn("StartupToGamingHome", self.t)
        import re
        for m in re.finditer(r"-Description\s+'([^']*)'", self.t):
            self.assertLessEqual(len(m.group(1)), 48, m.group(1))
        for ln in self.t.splitlines():
            if "$secret" in ln:
                self.assertNotRegex(ln, r"Write-|ConvertTo-Json|\$rec|Out-File|Add-Content", ln)
        for legacy in ("'TaskbarAl' -Value 0", "Start_ShowDownloads", "LayoutModification.xml' -Value", "ForceUnsigned"):
            self.assertNotIn(legacy, self.t)


if __name__ == "__main__":
    unittest.main()
