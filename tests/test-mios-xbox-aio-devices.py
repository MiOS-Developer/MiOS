#!/usr/bin/env python3
# AI-hint: Two-sided tests for xbox-bm-01/37: one MiOS-Xbox image for VMs and bare metal (guest steps gated per PCI device at setup, executed in PowerShell) and one Looking Glass server; spec, image gate and probe each name a planted defect.
# AI-related: usr/share/mios/mios.toml, usr/libexec/mios/win/xbox/render_spec.py, usr/libexec/mios/win/xbox/answer.py, usr/libexec/mios/win/xbox/spec_items.py, usr/libexec/mios/win/xbox/conform.py, usr/libexec/mios/win/xbox/probe.py, usr/share/mios/windows/build/MiOS-Xbox.Provision.ps1, usr/share/mios/windows/xbox/mios-xbox-probe.ps1, usr/share/mios/windows/mios-looking-glass-idd.ps1
"""Run: python3 -I tests/test-mios-xbox-aio-devices.py -v   (temp dirs only; no network, no VM; the PowerShell gate
cases run with pwsh or Windows PowerShell and are skipped, named as UNRUN, without either)"""

from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("test_mios_xbox_provisioning_aio", os.path.join(HERE, "test-mios-xbox-provisioning.py"))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)
A, C, R, S = P.A, P.C, P.R, P.S
_probe_spec = importlib.util.spec_from_file_location("mios_xbox_probe_aio", os.path.join(P.XBOX, "probe.py"))
PR = importlib.util.module_from_spec(_probe_spec)
_probe_spec.loader.exec_module(PR)

GUEST_STEPS = ("virtio_win_guest_tools", "qemu_ga", "spice_vdagent", "winfsp", "virtiofs", "looking_glass_idd")
BARE_METAL_PCI = ["10DE:2489", "1022:1480", "1022:1481", "8086:1539", "1987:5021"]   # GPU, host bridges, NIC, NVMe
VM_PCI = ["1AF4:1000", "1AF4:1003", "1AF4:1041", "1AF4:105A", "1AF4:1110", "1B36:0001", "10DE:2489"]
VM_NO_IVSHMEM = [p for p in VM_PCI if p != "1AF4:1110"]


def _powershell():
    return shutil.which("pwsh") or (shutil.which("powershell") if os.name == "nt" else None)


class Base(P.PayloadBase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.items = S.items_from_spec(cls.doc, P.ROOT)
        cls.gate_items = [it for it in cls.items if it["kind"] in ("guest_tool", "looking_glass")]

    def scratch(self, prefix: str = "t.") -> str:
        """A fresh directory under the class scratch, removed when the test ends."""
        d = tempfile.mkdtemp(prefix=prefix, dir=self.tmp)
        self.addCleanup(shutil.rmtree, d, True)
        return d

    def conform_findings(self, setup_text: str):
        d = self.copy_payload()
        with open(os.path.join(d, "Windows", "Setup", "Scripts", A.SETUP_SCRIPT), "w", encoding="utf-8") as fh:
            fh.write(setup_text)
        obs = C.collect_rendered([d, self.inputs["drivers_dir"]])
        _, findings = C.evaluate(self.gate_items, obs, "rendered", S.spec_policy(self.doc))
        return {f.id for f in findings}


# ---------------------------------------------------------------------------
# SSOT: the device table and every guest-only piece's required_device
# ---------------------------------------------------------------------------

class TestSpec(Base):
    def test_positive_every_guest_piece_names_a_declared_device(self):
        devs = {k for k, v in self.x["devices"].items() if isinstance(v, dict)}
        self.assertTrue({"virtio", "virtio_serial", "viofs", "ivshmem"} <= devs)
        for g, t in self.x["guest_tools"].items():
            self.assertIn(t["required_device"], devs, g)
        self.assertEqual(self.x["drivers"]["looking_glass_idd"]["required_device"], "ivshmem")
        self.assertIn("1AF4:1110", self.x["devices"]["ivshmem"]["pci"])
        self.assertTrue(self.x["provision"]["mirror_task"].startswith("MiOS\\"))

    def test_negative_spec_refuses_a_missing_unknown_or_malformed_device(self):
        cases = {
            "missing required_device": lambda x: x["guest_tools"]["qemu_ga"].pop("required_device"),
            "unknown device": lambda x: x["guest_tools"]["virtiofs"].__setitem__("required_device", "nvme"),
            "malformed pci": lambda x: x["devices"]["ivshmem"].__setitem__("pci", ["1af4:1110"]),
            "second Looking Glass server": lambda x: x["guest_tools"]["looking_glass_host"].__setitem__("install", True),
            "no device tables": lambda x: [x["devices"].pop(k) for k in [k for k, v in x["devices"].items() if isinstance(v, dict)]],
        }
        for name, plant in cases.items():
            merged = copy.deepcopy(self.merged)
            plant(merged["windows"]["xbox"])
            with self.subTest(case=name), self.assertRaises(R.SpecError):
                R.render(merged, P.LAYERS)


# ---------------------------------------------------------------------------
# SetupComplete: probe first, every guest step gated on its device, one Looking Glass server
# ---------------------------------------------------------------------------

class TestSetupComplete(Base):
    def test_positive_shared_directory_acl_precedes_every_write_and_device_gate(self):
        acl = A.shared_directory_acl(self.doc, P.ROOT)
        with open(os.path.join(P.ROOT, self.x["drivers"]["looking_glass_idd"]["installer"]), encoding="utf-8-sig") as fh:
            canonical = fh.read().split("# BEGIN Protect-MiOSDir", 1)[1].split("# END Protect-MiOSDir", 1)[0]
        self.assertEqual(acl, "\n".join(canonical.splitlines()[1:]).strip())
        self.assertEqual(self.setup.count("function Protect-MiOSDir("), 1)
        protect = f"foreach ($d in @($MiOS, {A._ps(self.x['provision']['state_dir'])}) | Select-Object -Unique) {{ Protect-MiOSDir $d $true $false }}"
        self.assertIn(protect, self.setup)
        for later in ("\nNew-Item -ItemType Directory -Force -Path", "Write-MiOSLog ('SYSTEM stage start", "Invoke-MiOSStep 'environment_probe'", "Invoke-MiOSGuestStep 'looking_glass_idd'"):
            self.assertLess(self.setup.index(protect), self.setup.index(later))
        doc = copy.deepcopy(self.doc)
        doc["spec"]["components"]["dest_dir"] = r"C:\Program Files\MiOS-Fixture"
        doc["spec"]["provision"]["state_dir"] = r"C:\ProgramData\MiOS-Fixture"
        setup = A.render_setup_script(doc, self.inputs, P.ROOT)
        self.assertIn("$MiOS = 'C:\\Program Files\\MiOS-Fixture'", setup)
        self.assertIn("@($MiOS, 'C:\\ProgramData\\MiOS-Fixture')", setup)

    def test_negative_shared_acl_source_requires_unique_canonical_markers(self):
        src = self.x["drivers"]["looking_glass_idd"]["installer"]
        for body in ("function Protect-MiOSDir {}", "# BEGIN Protect-MiOSDir\n# BEGIN Protect-MiOSDir\n# END Protect-MiOSDir"):
            tree = self.scratch("acl-source.")
            target = os.path.join(tree, *src.split("/"))
            os.makedirs(os.path.dirname(target))
            with open(target, "w", encoding="utf-8") as fh:
                fh.write(body)
            with self.subTest(body=body), self.assertRaisesRegex(A.PayloadError, "markers"):
                A.shared_directory_acl(self.doc, tree)

    def test_positive_probe_first_and_every_guest_step_gated(self):
        names = self.steps()
        self.assertEqual(names[0], "environment_probe")
        for s in GUEST_STEPS:
            self.assertIn(s, names)
            self.assertLess(names.index("environment_probe"), names.index(s))
        for g in ("virtio_win_guest_tools", "qemu_ga", "spice_vdagent", "winfsp", "virtiofs"):
            dev = self.x["guest_tools"][g]["required_device"]
            self.assertRegex(self.setup, rf"(?m)^Invoke-MiOSGuestStep '{g}' '{dev}' \{{")
        self.assertRegex(self.setup, r"(?m)^Invoke-MiOSGuestStep 'looking_glass_idd' 'ivshmem' \{")
        self.assertNotIn("looking_glass_host", names)
        self.assertIn(f"-MirrorTask '{self.x['provision']['mirror_task']}'", self.step("looking_glass_idd"))
        probe = self.step("environment_probe")
        self.assertIn("HypervisorPresent", probe)
        self.assertNotRegex(probe, r"if \(\$hv")   # a fact only, never the deciding gate

    def test_positive_image_gate_is_green_for_the_rendered_gates(self):
        ids = self.conform_findings(self.setup)
        self.assertFalse({i for i in ids if i.startswith(("guest_tools.", "looking_glass.", "missing:guest_tools", "missing:looking_glass"))}, ids)

    def test_negative_image_gate_catches_each_planted_defect(self):
        qd = self.x["guest_tools"]["qemu_ga"]["required_device"]
        vd = self.x["guest_tools"]["virtiofs"]["required_device"]
        plants = {
            "ungated qemu_ga": (f"Invoke-MiOSGuestStep 'qemu_ga' '{qd}' {{", "Invoke-MiOSStep 'qemu_ga' {",
                                "guest_tools.ungated:guest_tools.qemu_ga"),
            "virtiofs on the wrong device": (f"Invoke-MiOSGuestStep 'virtiofs' '{vd}' {{", "Invoke-MiOSGuestStep 'virtiofs' 'virtio' {",
                                             "guest_tools.wrong-device:guest_tools.virtiofs"),
            "second Looking Glass server": ("Invoke-MiOSGuestStep 'winfsp'", "Invoke-MiOSGuestStep 'looking_glass_host' 'ivshmem' {\n}\nInvoke-MiOSGuestStep 'winfsp'",
                                            "looking_glass.two-servers"),
            "mirror task at the root path": (f"-MirrorTask '{self.x['provision']['mirror_task']}'", "-MirrorTask 'MiOS display mirror'",
                                             "looking_glass.mirror-task-path"),
        }
        for name, (old, new, fid) in plants.items():
            self.assertEqual(self.setup.count(old), 1, name)
            with self.subTest(plant=name):
                self.assertIn(fid, self.conform_findings(self.setup.replace(old, new)))


# ---------------------------------------------------------------------------
# the rendered PowerShell gate, executed against fake device sets
# ---------------------------------------------------------------------------

@unittest.skipUnless(_powershell(), "UNRUN: needs pwsh or Windows PowerShell to execute the rendered gate")
class TestGateExecution(Base):
    def run_shared_prelude(self, ivshmem, fail_acl=False, fail_prelude=False):
        """Run the actual setup prefix with recording stand-ins for filesystem/ACL effects; a failed ACL must stop it."""
        prefix = self.setup.split("Invoke-MiOSStep 'environment_probe' {", 1)[0]
        standins = r'''
function Protect-MiOSDir([string]$Path, [bool]$UsersRead, [bool]$Quarantine) {
    $script:Events.Add(('acl:{0}:{1}:{2}' -f $Path, $UsersRead, $Quarantine))
    if ($script:FailAcl) { throw 'planted ACL failure' }
}
function New-Item { param($ItemType, [switch]$Force, $Path) $script:Events.Add('write:directories') }
function Write-MiOSLog($m) { $script:Events.Add('write:log') }
function Get-MiOSAccount { 'fixture SYSTEM' }
'''
        standins += "$MiOSDevices['ivshmem'] = @{ present = $" + str(ivshmem).lower() + " }\n"
        acl = A.shared_directory_acl(self.doc, P.ROOT)
        self.assertEqual(prefix.count(acl), 1, "the rendered ACL must be replaced by the recording stand-in exactly once")
        prefix = prefix.replace(acl, standins, 1)
        d = self.scratch("shared-prelude.")
        # The rendered prefix resolves $PsExe before its first ACL call. Linux has no SystemRoot;
        # use a private, existing directory without changing the real Windows environment or prefix.
        system_root = os.path.join(d, "SystemRoot")
        os.mkdir(system_root, 0o700)
        env = {k: v for k, v in os.environ.items() if k.casefold() != "systemroot"}
        env["SystemRoot"] = system_root
        script = os.path.join(d, "prelude.ps1")
        with open(script, "w", encoding="utf-8-sig") as fh:
            fh.write("$script:Events = New-Object 'System.Collections.Generic.List[string]'\n")
            fh.write("$script:UnexpectedFailure = $null\n")
            fh.write("$script:FailAcl = $" + str(fail_acl).lower() + "\ntry {\n")
            if fail_prelude:
                fh.write("throw [InvalidOperationException]::new('planted prelude failure')\n")
            fh.write(prefix)
            fh.write("$script:Events.Add('write:environment')\n} catch {\n"
                     "    if ($script:FailAcl -and $_.Exception.Message -ceq 'planted ACL failure') {\n"
                     "        $script:Events.Add('failed:acl')\n"
                     "    } else {\n"
                     "        $script:UnexpectedFailure = [ordered]@{ type = $_.Exception.GetType().FullName; "
                     "message = $_.Exception.Message; position = $_.InvocationInfo.PositionMessage }\n"
                     "    }\n}\n")
            fh.write("ConvertTo-Json -InputObject ([ordered]@{ events = @($script:Events.ToArray()); "
                     "unexpected_failure = $script:UnexpectedFailure }) -Depth 4 -Compress\n")
        r = subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script],
                           capture_output=True, text=True, timeout=120, env=env)
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-800:])
        result = json.loads(r.stdout)
        self.assertIsNone(result["unexpected_failure"],
                          "unexpected shared prelude exception: " + json.dumps(result["unexpected_failure"]))
        return result["events"]

    def test_positive_shared_roots_are_secured_before_bare_metal_or_vm_writes(self):
        expected = [f"acl:{p}:True:False" for p in (self.x["components"]["dest_dir"], self.x["provision"]["state_dir"])]
        for ivshmem in (False, True):
            with self.subTest(ivshmem=ivshmem):
                events = self.run_shared_prelude(ivshmem)
                self.assertEqual(events[:2], expected)
                self.assertEqual(events[2:], ["write:directories", "write:log", "write:environment"])

    def test_negative_acl_failure_stops_all_shared_writes(self):
        events = self.run_shared_prelude(False, fail_acl=True)
        self.assertEqual(events, [f"acl:{self.x['components']['dest_dir']}:True:False", "failed:acl"])

    def test_negative_unexpected_prelude_exception_is_not_an_acl_failure(self):
        with self.assertRaisesRegex(
                AssertionError, r"unexpected shared prelude exception: .*System.InvalidOperationException.*planted prelude failure"):
            self.run_shared_prelude(False, fail_prelude=True)

    def run_gate(self, pci, plant_ungated: bool = False):
        """Execute the rendered PS library + environment_probe + every guest gate (bodies replaced by a marker, or by the
        old bare-metal failure when plant_ungated) against a fake PCI set. Returns (steps state, environment record)."""
        d = self.scratch("gate.")
        rec = os.path.join(d, "environment.json")
        state = os.path.join(d, "setup-state.json")
        probe = self.step("environment_probe").replace(self.x["devices"]["record"].replace("'", "''"), rec.replace("'", "''"))
        gates = re.findall(r"(?m)^Invoke-MiOSGuestStep '([^']+)' '([^']+)' \{", self.setup)
        self.assertEqual({g for g, _ in gates}, set(GUEST_STEPS))
        lines = [
            f"$MiOSStage = 'test'; $MiOSLogFile = '{os.path.join(d, 'setup.log')}'; $MiOSLogDir = '{d}'",
            "$MiOSSerialPort = 'COM250'; $MiOSSerialBaud = 115200",
            A._PS_LIB.strip("\n"),
            "$script:FakePci = @(" + ", ".join(f"'PCI\\VEN_{p[:4]}&DEV_{p[5:]}&SUBSYS_00000000&REV_00\\3&0&0'" for p in pci) + ")",
            "function Get-PnpDevice { param([switch]$PresentOnly, $ErrorAction) $script:FakePci | ForEach-Object { [pscustomobject]@{ InstanceId = $_ } } }",
            "function Get-CimInstance { param($ClassName, $ErrorAction) [pscustomobject]@{ HypervisorPresent = $true } }",
        ]
        if plant_ungated:   # the old behaviour: no gate, the guest service start throws on bare metal
            lines.append("function Invoke-MiOSGuestStep([string]$Name, [string]$Device, [scriptblock]$Body) { Invoke-MiOSStep $Name { throw \"service $Name is Stopped after start\" } }")
        lines += ["Invoke-MiOSStep 'environment_probe' {", probe.rstrip("\n"), "}"]
        lines += [f"Invoke-MiOSGuestStep '{g}' '{dev}' {{ $null = 1 }}" for g, dev in gates]
        lines.append(f"Save-MiOSState '{state}' | Out-Null")
        script = os.path.join(d, "gate.ps1")
        with open(script, "w", encoding="utf-8-sig") as fh:
            fh.write("\n".join(lines) + "\n")
        r = subprocess.run([_powershell(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", script],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(r.returncode, 0, r.stdout[-800:] + r.stderr[-800:])
        with open(state, encoding="utf-8-sig") as fh:
            steps = json.load(fh)["steps"]
        with open(rec, encoding="utf-8-sig") as fh:
            env = json.load(fh)
        return steps, env

    @staticmethod
    def bare_metal_problems(steps):
        return [f"{s}: {v}" for s, v in steps.items() if s in GUEST_STEPS and not str(v).startswith("not applicable")] + \
               [f"{s}: {v}" for s, v in steps.items() if str(v).startswith("failed")]

    def test_positive_bare_metal_records_not_applicable_never_failed(self):
        steps, env = self.run_gate(BARE_METAL_PCI)
        self.assertEqual(self.bare_metal_problems(steps), [])
        self.assertEqual(steps["environment_probe"], "ok")
        self.assertIn("no virtio_serial device", steps["qemu_ga"])
        self.assertIn("1AF4:1110", steps["looking_glass_idd"])
        self.assertTrue(env["hypervisor_present"])          # recorded as a fact ...
        self.assertFalse(env["devices"]["ivshmem"]["present"])   # ... and it decided nothing

    def test_positive_vm_runs_every_guest_step(self):
        steps, env = self.run_gate(VM_PCI)
        self.assertEqual({s: steps[s] for s in GUEST_STEPS}, {s: "ok" for s in GUEST_STEPS})
        self.assertTrue(all(v["present"] for v in env["devices"].values()))

    def test_negative_old_ungated_failure_is_caught(self):
        steps, _ = self.run_gate(BARE_METAL_PCI, plant_ungated=True)
        problems = self.bare_metal_problems(steps)
        self.assertTrue(any("failed" in p for p in problems), problems)


# ---------------------------------------------------------------------------
# the in-guest probe: not applicable is conforming, with evidence; claims without the device are findings
# ---------------------------------------------------------------------------

class TestProbe(Base):
    def probe_doc(self, pci, running=(), idd=False, task_state=None, legacy=(), host_svc=None):
        obs = {}
        for it in self.gate_items:
            if it["kind"] == "guest_tool":
                svc = it.get("service")
                obs[it["id"]] = {"service": ({"present": svc in running, "status": "Running" if svc in running else None,
                                              "start": "Automatic" if svc in running else None})} if svc else {}
            else:
                obs[it["id"]] = {"idd": idd, "task": {"present": task_state is not None, "state": task_state},
                                 "legacy_tasks": list(legacy), "host_service": host_svc or {"present": False}}
        return {"schema": PR.PROBE_SCHEMA, "pci_present": list(pci), "observations": obs}

    def ev(self, doc):
        results, findings = PR.evaluate(self.gate_items, doc)
        return {r["id"]: r for r in results}, {f.id for f in findings}

    def services(self):
        return [it["service"] for it in self.gate_items if it["kind"] == "guest_tool" and it.get("service") and it.get("install") is not False]

    def test_positive_bare_metal_is_conforming_with_evidence(self):
        res, ids = self.ev(self.probe_doc(BARE_METAL_PCI))
        self.assertEqual(ids, set())
        for it in self.gate_items:
            self.assertEqual(res[it["id"]]["status"], "not-applicable", it["id"])
            self.assertRegex(res[it["id"]]["detail"], r"no \w+ device \(PCI .* absent|not installed by the SSOT")

    def test_positive_vm_with_every_device_passes(self):
        res, ids = self.ev(self.probe_doc(VM_PCI, running=self.services(), idd=True, task_state="Ready"))
        self.assertEqual(ids, set())
        self.assertEqual(res["looking_glass.idd"]["status"], "pass")
        self.assertEqual(res["guest_tools.qemu_ga"]["status"], "pass")
        self.assertEqual(res["guest_tools.looking_glass_host"]["status"], "not-applicable")   # one server: the IDD

    def test_negative_vm_requires_idd_and_the_enabled_mirror_task(self):
        for idd, task, finding in ((False, "Ready", "missing:looking_glass.idd"),
                                   (True, None, "missing:looking_glass.idd.task"),
                                   (True, "Disabled", "missing:looking_glass.idd.task")):
            with self.subTest(idd=idd, task=task):
                res, ids = self.ev(self.probe_doc(VM_PCI, running=self.services(), idd=idd, task_state=task))
                self.assertIn(finding, ids)
                self.assertEqual(res["looking_glass.idd"]["status"], "fail")

    def test_positive_resolved_mirror_policy_can_disable_the_task_requirement(self):
        doc = copy.deepcopy(self.doc)
        doc["resolved"]["vm.display_ref"]["mirror_displays"] = False
        items = [it for it in S.items_from_spec(doc, P.ROOT) if it["kind"] == "looking_glass"]
        self.assertEqual(len(items), 1)
        self.assertIs(items[0]["mirror_required"], False)
        self.assertIs(next(it for it in self.gate_items if it["kind"] == "looking_glass")["mirror_required"], True)
        self.assertIn("-MirrorDisplays 0", A.render_setup_script(doc, self.inputs, P.ROOT))
        for task in (None, "Disabled"):
            with self.subTest(task=task):
                results, findings = PR.evaluate(items, self.probe_doc(VM_PCI, idd=True, task_state=task))
                self.assertEqual(findings, [])
                self.assertEqual(results[0]["status"], "pass")

    def test_negative_claims_without_the_device_and_leftovers_are_findings(self):
        cases = {
            "VM claims Looking Glass without IVSHMEM": (dict(pci=VM_NO_IVSHMEM, running=self.services(), idd=True), "looking_glass.idd-without-ivshmem"),
            "Duplicate task enabled without IVSHMEM": (dict(pci=BARE_METAL_PCI, task_state="Ready"), "looking_glass.mirror-without-ivshmem"),
            "bare metal runs QEMU-GA anyway": (dict(pci=BARE_METAL_PCI, running=["QEMU-GA"]), "guest_tools.running-not-applicable:guest_tools.qemu_ga"),
            "root-path mirror task left behind": (dict(pci=VM_PCI, running=self.services(), idd=True, task_state="Ready",
                                                       legacy=["\\MiOS display mirror"]), "looking_glass.legacy-mirror-task"),
            "two Looking Glass servers": (dict(pci=VM_PCI, running=self.services(), idd=True, task_state="Ready",
                                               host_svc={"present": True, "status": "Stopped", "start": "Automatic"}), "looking_glass.two-servers"),
            "VM device present, service stopped": (dict(pci=VM_PCI, running=[s for s in self.services() if s != "QEMU-GA"], idd=True, task_state="Ready"),
                                                   "guest_tools.not-running:guest_tools.qemu_ga"),
        }
        for name, (kw, fid) in cases.items():
            doc = self.probe_doc(**kw)
            if name == "VM device present, service stopped":
                doc["observations"]["guest_tools.qemu_ga"]["service"] = {"present": True, "status": "Stopped", "start": "Automatic"}
            with self.subTest(case=name):
                self.assertIn(fid, self.ev(doc)[1])

    def test_negative_a_guest_without_a_pci_list_waives_nothing(self):
        doc = self.probe_doc(BARE_METAL_PCI)
        doc.pop("pci_present")
        _, ids = self.ev(doc)
        self.assertIn("missing:guest_tools.qemu_ga", ids)
        self.assertIn("probe.device-unknown:looking_glass.idd", ids)
        self.assertIn("missing:looking_glass.idd", ids)
        self.assertIn("missing:looking_glass.idd.task", ids)

    def test_negative_unknown_pci_evidence_never_passes_the_idd(self):
        doc = self.probe_doc(VM_PCI, running=self.services(), idd=True, task_state="Ready")
        doc.pop("pci_present")
        res, ids = self.ev(doc)
        self.assertIn("probe.device-unknown:looking_glass.idd", ids)
        self.assertEqual(res["looking_glass.idd"]["status"], "fail")


if __name__ == "__main__":
    unittest.main()
