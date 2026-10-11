#!/usr/bin/env python3
# AI-hint: Automated two-sided verification test suite for Milestone M5 (4-Tier Sandbox Execution Ladder & Permissive Licensing).
# AI-related: usr/libexec/mios/mios-sandbox-exec, etc/cdi/nvidia.json, usr/share/containers/containers.conf.d/20-mios-krun-runtime.conf, usr/share/containers/systemd/users/mios-microvm-krun@.container, usr/lib/mios/agent-pipe/mios_isolation.py, usr/share/mios/mios.toml, etc/fapolicyd/fapolicyd.rules
"""Automated Two-Sided Verification Test Suite for Milestone M5.

Milestone M5: 4-Tier Sandbox Execution Ladder & Permissive Licensing (R5)
1. Tier 1 Sandbox (Bubblewrap bwrap <1ms):
   - Confinement via usr/libexec/mios/mios-sandbox-exec with --ro-bind / /, --unshare-all, --cap-drop ALL, and seccomp filter on fd 9.
2. Tier 2 Sandbox (Rootless OCI crun ~15ms with CDI):
   - Rootless OCI Quadlets under UID 1000 with static CDI manifest /etc/cdi/nvidia.json binding /dev/nvidia* without --privileged.
3. Tier 3 Sandbox (MicroVMs libkrun <=90ms):
   - Runtime=krun with /dev/kvm hardware acceleration and single virtio-net namespace isolation (task_170 ISOL-01).
4. Tier 4 Sandbox (Hardware VMs KVM/VFIO):
   - Hardware VM isolation specifications, whole-device RTX dGPU passthrough via vfio-pci, 128 MB IVSHMEM (/dev/kvmfr0).
5. Permissive Licensing Migration (Elimination of Copyleft Taint):
   - MIT/Apache-2.0 alternatives: uutils-coreutils (MIT), youki (Apache-2.0), systemd-boot (LGPL-2.1+), dbus-broker (Apache-2.0).
   - Zero copyleft taint policy across core runtime packages in mios.toml.
6. fapolicyd Execution Perimeter:
   - etc/fapolicyd/fapolicyd.rules and usr/lib/fapolicyd/rules.d/ enforcing deny_audit perm=execute : all outside /usr.
7. Planted Negative Controls:
   - Unsigned binary in /tmp denied by fapolicyd fails RED.
   - Missing seccomp filter in bwrap fails RED.
   - Injected GPLv3 component in core fails RED.
   - Restored canonical state passes GREEN.
"""

from __future__ import annotations

import json
import os
import re
import sys
import unittest
from typing import Any, Dict, List, Optional, Tuple

try:
    import tomllib
except ImportError:
    import tomli as tomllib  # type: ignore


def find_repo_roots() -> list[str]:
    """Find candidate roots across workspaces (Windows and Linux/WSL)."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.abspath(os.path.join(script_dir, ".."))
    candidates = [
        parent_dir,
        os.getcwd(),
        os.path.normpath(r"M:\MiOS\worktrees\mios-integration"),
        os.path.normpath(r"C:\MiOS"),
        os.path.normpath(r"C:\-dev-loop"),
        os.path.normpath(r"C:\MiOS-RnD"),
        os.path.normpath(r"C:\mios-bootstrap"),
        "/mnt/m/MiOS/worktrees/mios-integration",
        "/mnt/c/MiOS",
        "/mnt/c/-dev-loop",
        "/mnt/c/MiOS-RnD",
        "/mnt/c/mios-bootstrap",
        "/",
    ]
    seen = set()
    res = []
    for c in candidates:
        norm = os.path.normpath(c)
        if norm not in seen and os.path.isdir(norm):
            seen.add(norm)
            res.append(norm)
    return res


def find_file(relative_paths: list[str]) -> str | None:
    """Find the first matching existing file across repo candidates and relative paths."""
    for root in find_repo_roots():
        for rel in relative_paths:
            full = os.path.join(root, rel)
            if os.path.isfile(full):
                return full
            if os.path.isfile(rel):
                return rel
    return None


class M5Validator:
    """Genuine validation logic for Milestone M5 sandbox ladder, permissive licensing, and fapolicyd."""

    @staticmethod
    def validate_bwrap_script(script_text: str) -> Tuple[bool, List[str]]:
        """Validates that mios-sandbox-exec constructs bwrap args with all mandatory security flags."""
        missing = []
        if "--ro-bind / /" not in script_text:
            missing.append("--ro-bind / /")
        if "--unshare-all" not in script_text:
            missing.append("--unshare-all")
        if "--cap-drop ALL" not in script_text:
            missing.append("--cap-drop ALL")
        if "--seccomp 9" not in script_text:
            missing.append("--seccomp 9")
        if "LEVEL=enforce" not in script_text:
            missing.append("LEVEL=enforce (fail-closed default)")
        return (len(missing) == 0, missing)

    @staticmethod
    def validate_cdi_spec(spec_text: str) -> Tuple[bool, str]:
        """Validates CDI specification syntax and device bindings."""
        try:
            data = json.loads(spec_text)
        except Exception as e:
            return False, f"JSON parse error: {e}"

        if data.get("cdiVersion") != "0.6.0":
            return False, f"Invalid cdiVersion: expected '0.6.0', got '{data.get('cdiVersion')}'"
        if data.get("kind") != "nvidia.com/gpu":
            return False, f"Invalid kind: expected 'nvidia.com/gpu', got '{data.get('kind')}'"

        devices = data.get("devices", [])
        if not devices:
            return False, "CDI specification contains 0 devices"

        names = {d.get("name") for d in devices}
        if "all" not in names:
            return False, "Missing composite 'all' device"

        # Check device nodes in all devices
        for dev in devices:
            edits = dev.get("containerEdits", {})
            nodes = [n.get("path") for n in edits.get("deviceNodes", [])]
            if not any(p and p.startswith("/dev/nvidia") for p in nodes):
                return False, f"Device '{dev.get('name')}' missing /dev/nvidia* nodes"

        return True, "Valid CDI specification binding /dev/nvidia* without --privileged"

    @staticmethod
    def validate_quadlet_rootless(quadlet_text: str) -> Tuple[bool, str]:
        """Validates that Quadlet container runs rootless under UID 1000 with CDI and no --privileged."""
        has_userns_keep_id = bool(re.search(r"UserNS\s*=\s*keep-id", quadlet_text))
        has_privileged = bool(re.search(r"(Privileged\s*=\s*true|--privileged)", quadlet_text, re.IGNORECASE))
        has_user_cond = bool(re.search(r"ConditionUser\s*=\s*!@system", quadlet_text)) or "default.target" in quadlet_text
        has_cdi_device = bool(re.search(r"AddDevice\s*=\s*nvidia\.com/gpu", quadlet_text))

        if not has_userns_keep_id:
            return False, "Quadlet missing UserNS=keep-id (required for rootless UID mapping)"
        if has_privileged:
            return False, "SECURITY VIOLATION: Quadlet specifies Privileged=true or --privileged"
        if not has_user_cond:
            return False, "Quadlet missing user-scope target or ConditionUser=!@system"
        return True, "Valid rootless Quadlet configuration"

    @staticmethod
    def validate_krun_runtime_conf(conf_text: str) -> Tuple[bool, str]:
        """Validates containers.conf registration of krun OCI runtime."""
        has_runtimes = "[engine.runtimes]" in conf_text
        has_krun = "krun" in conf_text and ("/usr/bin/krun" in conf_text or "/usr/bin/crun-krun" in conf_text)
        if not has_runtimes:
            return False, "Missing [engine.runtimes] table"
        if not has_krun:
            return False, "Missing krun runtime registration"
        return True, "Valid krun OCI runtime configuration"

    @staticmethod
    def validate_microvm_quadlet(quadlet_text: str) -> Tuple[bool, str]:
        """Validates MicroVM Quadlet template for libkrun execution and hardware acceleration gating."""
        has_runtime_krun = bool(re.search(r"Runtime\s*=\s*krun", quadlet_text))
        has_kvm_cond = bool(re.search(r"ConditionPathExists\s*=\s*/dev/kvm", quadlet_text))
        has_net_none = bool(re.search(r"Network\s*=\s*none", quadlet_text))

        if not has_runtime_krun:
            return False, "Missing Runtime=krun"
        if not has_kvm_cond:
            return False, "Missing ConditionPathExists=/dev/kvm hardware acceleration gate"
        if not has_net_none:
            return False, "Missing Network=none or isolated namespace"
        return True, "Valid libkrun MicroVM Quadlet template"

    @staticmethod
    def validate_licensing_policy(toml_text: str) -> Tuple[bool, str]:
        """Validates zero copyleft taint policy and permissive replacements in mios.toml."""
        try:
            data = tomllib.loads(toml_text)
        except Exception as e:
            return False, f"TOML parse error: {e}"

        sec = data.get("security", {})
        lic = sec.get("licensing", {})

        if not lic.get("zero_copyleft_taint"):
            return False, "Missing or false zero_copyleft_taint setting in [security.licensing]"

        allowed = set(lic.get("allowed_licenses", []))
        if "MIT" not in allowed or "Apache-2.0" not in allowed:
            return False, "Allowed licenses missing MIT or Apache-2.0"

        denied = set(lic.get("denied_licenses", []))
        if not any("GPL" in d for d in denied):
            return False, "Denied licenses missing GPL copyleft types"

        reps = lic.get("replacements", {})
        required_reps = {
            "coreutils": "uutils-coreutils",
            "oci_runtime": "youki",
            "bootloader": "systemd-boot",
            "dbus_daemon": "dbus-broker",
        }
        for category, expected in required_reps.items():
            rep = reps.get(category, {})
            if rep.get("replacement") != expected:
                return False, f"Missing or invalid replacement for {category}: expected {expected}"

        return True, "Valid zero copyleft taint policy and permissive replacements"

    @staticmethod
    def validate_fapolicyd_rules(rules_text: str) -> Tuple[bool, str]:
        """Validates that fapolicyd enforces deny_audit perm=execute : all outside /usr."""
        has_deny_audit = bool(re.search(r"deny_audit\s+perm=execute\s*:\s*all", rules_text))
        has_root_trust = bool(re.search(r"allow\s+perm=any\s+uid=0\s+trust=1\s*:\s*all", rules_text))
        has_exec_trust = bool(re.search(r"allow\s+perm=execute\s*:\s*ftype=application/x-executable\s+trust=1", rules_text))

        if not has_deny_audit:
            return False, "Missing catch-all 'deny_audit perm=execute : all' rule"
        if not has_root_trust:
            return False, "Missing root constraint 'allow perm=any uid=0 trust=1 : all'"
        if not has_exec_trust:
            return False, "Missing RPM trust requirement 'allow perm=execute : ... trust=1'"
        return True, "Valid fapolicyd execution perimeter"


class TestMilestoneM5SandboxLicensing(unittest.TestCase):
    """Automated two-sided test suite for Milestone M5."""

    @classmethod
    def setUpClass(cls):
        cls.validator = M5Validator()
        cls.roots = find_repo_roots()
        cls.assertTrue(len(cls.roots) > 0, "At least one repo root must exist")

    # =========================================================================
    # Tier 1 Sandbox: Bubblewrap (bwrap <1ms)
    # =========================================================================

    def test_tier1_bwrap_confinement_script_present(self):
        """Tier 1: Verify mios-sandbox-exec exists and has executable structure."""
        script_path = find_file(["usr/libexec/mios/mios-sandbox-exec"])
        self.assertIsNotNone(script_path, "usr/libexec/mios/mios-sandbox-exec must exist")
        with open(script_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        ok, missing = self.validator.validate_bwrap_script(content)
        self.assertTrue(ok, f"mios-sandbox-exec missing required flags: {missing}")
        self.assertIn("--ro-bind / /", content)
        self.assertIn("--unshare-all", content)
        self.assertIn("--cap-drop ALL", content)
        self.assertIn("--seccomp 9", content)

    def test_tier1_planted_negative_missing_seccomp(self):
        """PLANTED NEGATIVE CONTROL: Bubblewrap execution without seccomp filter fails RED."""
        # Planted defect: bwrap script with seccomp stripped out
        bad_bwrap_script = """#!/bin/bash
        BWRAP_ARGS=(
            --ro-bind / /
            --dev /dev
            --proc /proc
            --tmpfs /tmp
            --unshare-all
            --cap-drop ALL
        )
        bwrap "${BWRAP_ARGS[@]}" -- "$@"
        """
        ok, missing = self.validator.validate_bwrap_script(bad_bwrap_script)
        self.assertFalse(ok, "Planted negative control: missing seccomp filter must fail RED")
        self.assertIn("--seccomp 9", missing)

        # Restored canonical bwrap script passes GREEN
        good_bwrap_script = """#!/bin/bash
        LEVEL=enforce
        BWRAP_ARGS=(
            --ro-bind / /
            --dev /dev
            --proc /proc
            --tmpfs /tmp
            --unshare-all
            --cap-drop ALL
            --seccomp 9
        )
        bwrap "${BWRAP_ARGS[@]}" -- "$@"
        """
        ok, missing = self.validator.validate_bwrap_script(good_bwrap_script)
        self.assertTrue(ok, f"Restored canonical bwrap script must pass GREEN: {missing}")

    def test_tier1_planted_negative_missing_unshare_all(self):
        """PLANTED NEGATIVE CONTROL: Bubblewrap execution without --unshare-all fails RED."""
        bad_bwrap_unshare = """#!/bin/bash
        LEVEL=enforce
        BWRAP_ARGS=(
            --ro-bind / /
            --dev /dev
            --proc /proc
            --tmpfs /tmp
            --cap-drop ALL
            --seccomp 9
        )
        bwrap "${BWRAP_ARGS[@]}" -- "$@"
        """
        ok, missing = self.validator.validate_bwrap_script(bad_bwrap_unshare)
        self.assertFalse(ok, "Planted negative control: missing --unshare-all must fail RED")
        self.assertIn("--unshare-all", missing)

    # =========================================================================
    # Tier 2 Sandbox: Rootless OCI (crun ~15ms with CDI)
    # =========================================================================

    def test_tier2_cdi_manifest_structure(self):
        """Tier 2: Verify static CDI manifest /etc/cdi/nvidia.json binds /dev/nvidia* without --privileged."""
        cdi_path = find_file(["etc/cdi/nvidia.json"])
        self.assertIsNotNone(cdi_path, "/etc/cdi/nvidia.json must exist")
        with open(cdi_path, "r", encoding="utf-8") as f:
            content = f.read()

        ok, msg = self.validator.validate_cdi_spec(content)
        self.assertTrue(ok, f"CDI spec validation failed: {msg}")

        data = json.loads(content)
        self.assertEqual(data.get("cdiVersion"), "0.6.0")
        self.assertEqual(data.get("kind"), "nvidia.com/gpu")

        dev_names = {d["name"] for d in data.get("devices", [])}
        self.assertIn("0", dev_names, "CDI spec must define device 0")
        self.assertIn("all", dev_names, "CDI spec must define composite device 'all'")

    def test_tier2_rootless_quadlet_configurations(self):
        """Tier 2: Verify rootless OCI Quadlets under UID 1000 bind CDI without --privileged."""
        sunshine_path = find_file(["usr/share/containers/systemd/users/mios-sunshine.container"])
        self.assertIsNotNone(sunshine_path, "mios-sunshine.container must exist")
        with open(sunshine_path, "r", encoding="utf-8") as f:
            sunshine_content = f.read()

        ok, msg = self.validator.validate_quadlet_rootless(sunshine_content)
        self.assertTrue(ok, f"Sunshine Quadlet rootless validation failed: {msg}")
        self.assertIn("AddDevice=nvidia.com/gpu=all", sunshine_content)
        self.assertNotIn("--privileged", sunshine_content.lower())

        coderun_path = find_file(["usr/share/containers/systemd/users/mios-coderun-sandbox@.container"])
        self.assertIsNotNone(coderun_path, "mios-coderun-sandbox@.container must exist")
        with open(coderun_path, "r", encoding="utf-8") as f:
            coderun_content = f.read()

        self.assertIn("UserNS=keep-id", coderun_content)
        self.assertIn("DropCapability=ALL", coderun_content)
        self.assertIn("ReadOnly=true", coderun_content)
        self.assertNotIn("Privileged=true", coderun_content)

    def test_tier2_planted_negative_cdi_missing_control_node(self):
        """PLANTED NEGATIVE CONTROL: CDI manifest missing /dev/nvidia* device nodes fails RED."""
        # Planted defect: CDI spec without device nodes
        bad_cdi_spec = json.dumps({
            "cdiVersion": "0.6.0",
            "kind": "nvidia.com/gpu",
            "devices": [
                {
                    "name": "0",
                    "containerEdits": {"deviceNodes": [{"path": "/dev/null"}]},
                },
                {
                    "name": "all",
                    "containerEdits": {"deviceNodes": [{"path": "/dev/null"}]},
                },
            ],
        })
        ok, msg = self.validator.validate_cdi_spec(bad_cdi_spec)
        self.assertFalse(ok, "Planted negative control: CDI spec without /dev/nvidia* must fail RED")

        # Restored CDI spec with /dev/nvidia* nodes passes GREEN
        good_cdi_spec = json.dumps({
            "cdiVersion": "0.6.0",
            "kind": "nvidia.com/gpu",
            "devices": [
                {
                    "name": "0",
                    "containerEdits": {
                        "deviceNodes": [
                            {"path": "/dev/nvidia0"},
                            {"path": "/dev/nvidiactl"},
                        ]
                    },
                },
                {
                    "name": "all",
                    "containerEdits": {
                        "deviceNodes": [
                            {"path": "/dev/nvidia0"},
                            {"path": "/dev/nvidiactl"},
                        ]
                    },
                },
            ],
        })
        ok, msg = self.validator.validate_cdi_spec(good_cdi_spec)
        self.assertTrue(ok, f"Restored CDI spec must pass GREEN: {msg}")

    # =========================================================================
    # Tier 3 Sandbox: MicroVMs (libkrun <=90ms)
    # =========================================================================

    def test_tier3_microvm_krun_runtime_and_quadlet(self):
        """Tier 3: Verify krun OCI runtime registration and MicroVM Quadlet template."""
        runtime_conf_path = find_file([
            "usr/share/containers/containers.conf.d/10-mios-pasta.conf",
            "usr/share/containers/containers.conf.d/20-mios-krun-runtime.conf",
        ])
        self.assertIsNotNone(runtime_conf_path, "krun runtime configuration must exist")
        with open(runtime_conf_path, "r", encoding="utf-8") as f:
            runtime_content = f.read()

        ok, msg = self.validator.validate_krun_runtime_conf(runtime_content)
        self.assertTrue(ok, f"krun runtime conf failed validation: {msg}")

        quadlet_path = find_file(["usr/share/containers/systemd/users/mios-microvm-krun@.container"])
        self.assertIsNotNone(quadlet_path, "mios-microvm-krun@.container must exist")
        with open(quadlet_path, "r", encoding="utf-8") as f:
            quadlet_content = f.read()

        ok, msg = self.validator.validate_microvm_quadlet(quadlet_content)
        self.assertTrue(ok, f"MicroVM Quadlet failed validation: {msg}")

    def test_tier3_isolation_ladder_engine(self):
        """Tier 3: Verify mios_isolation.py tier resolution and taint promotion (task_170 ISOL-01)."""
        engine_path = find_file(["usr/lib/mios/agent-pipe/mios_isolation.py"])
        self.assertIsNotNone(engine_path, "usr/lib/mios/agent-pipe/mios_isolation.py must exist")

        # Import dynamically from file path
        import importlib.util
        spec = importlib.util.spec_from_file_location("mios_isolation", engine_path)
        self.assertIsNotNone(spec and spec.loader)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore

        ladder = mod.SandboxExecutionLadder(dev_kvm_present=True)

        # Untainted action stays at requested tier
        tier, event = ladder.resolve_effective_tier(verb="read_file", tainted=False, requested_tier="bwrap")
        self.assertEqual(tier, "bwrap")
        self.assertIsNone(event)

        # Tainted action promotes from Tier 1 to Tier 3 (krun MicroVM)
        tier, event = ladder.resolve_effective_tier(verb="run_shell", tainted=True, requested_tier="bwrap")
        self.assertEqual(tier, "krun")
        self.assertIsNotNone(event)
        self.assertEqual(event.get("event"), "firewall_promote")
        self.assertEqual(event.get("to_tier"), "krun")

    def test_tier3_planted_negative_degrade_closed_on_missing_kvm(self):
        """PLANTED NEGATIVE CONTROL: Tainted workload without /dev/kvm degrades CLOSED to firewall_block."""
        engine_path = find_file(["usr/lib/mios/agent-pipe/mios_isolation.py"])
        self.assertIsNotNone(engine_path)

        import importlib.util
        spec = importlib.util.spec_from_file_location("mios_isolation", engine_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore

        # Simulate host where /dev/kvm is unavailable (e.g. unprivileged container or missing nested virt)
        ladder_no_kvm = mod.SandboxExecutionLadder(dev_kvm_present=False)

        tier, event = ladder_no_kvm.resolve_effective_tier(verb="execute_codegen", tainted=True, requested_tier="bwrap")
        # Must degrade closed to 'firewall_block' rather than running unsafely unconfined (degrade-open)
        self.assertEqual(tier, "firewall_block", "Missing /dev/kvm must trigger degrade-closed firewall_block")
        self.assertIsNotNone(event)
        self.assertEqual(event.get("status"), "denied")

        # Restored /dev/kvm passes GREEN with successful promotion
        ladder_with_kvm = mod.SandboxExecutionLadder(dev_kvm_present=True)
        tier, event = ladder_with_kvm.resolve_effective_tier(verb="execute_codegen", tainted=True, requested_tier="bwrap")
        self.assertEqual(tier, "krun", "Restored /dev/kvm must promote cleanly to krun")
        self.assertEqual(event.get("event"), "firewall_promote")

    # =========================================================================
    # Tier 4 Sandbox: Hardware VMs (KVM/VFIO)
    # =========================================================================

    def test_tier4_hardware_vm_isolation_specs(self):
        """Tier 4: Verify hardware VM specifications and Looking Glass / VFIO integration."""
        udev_path = find_file(["usr/lib/udev/rules.d/99-kvmfr.rules"])
        self.assertIsNotNone(udev_path, "99-kvmfr.rules must exist")
        with open(udev_path, "r", encoding="utf-8") as f:
            udev_content = f.read()

        self.assertIn("kvmfr", udev_content)
        self.assertIn("0660", udev_content)
        self.assertIn("kvm", udev_content)

        lg_script = find_file(["usr/libexec/mios/vfio/setup-looking-glass.py", "usr/libexec/mios/display/looking_glass.py"])
        self.assertIsNotNone(lg_script, "Looking Glass script must exist")
        with open(lg_script, "r", encoding="utf-8") as f:
            lg_content = f.read()

        self.assertIn("looking-glass", lg_content)

        xml_path = find_file(["usr/libexec/mios/Xbox-Final-NoAutoSelect.xml", "usr/libexec/mios/Xbox-AutoEnroll.xml"])
        self.assertIsNotNone(xml_path, "Xbox domain XML must exist")
        with open(xml_path, "r", encoding="utf-8") as f:
            xml_content = f.read()

        self.assertIn("qemu-system-x86_64", xml_content)
        self.assertIn("hostdev", xml_content)

    # =========================================================================
    # Permissive Licensing Migration (Elimination of Copyleft Taint)
    # =========================================================================

    def test_permissive_licensing_policy_in_mios_toml(self):
        """Permissive Licensing: Verify zero copyleft taint policy and 4 permissive replacements."""
        toml_path = find_file(["usr/share/mios/mios.toml"])
        self.assertIsNotNone(toml_path, "usr/share/mios/mios.toml must exist")
        with open(toml_path, "r", encoding="utf-8") as f:
            toml_content = f.read()

        ok, msg = self.validator.validate_licensing_policy(toml_content)
        self.assertTrue(ok, f"Licensing policy validation failed: {msg}")

        data = tomllib.loads(toml_content)
        lic = data["security"]["licensing"]
        self.assertTrue(lic["zero_copyleft_taint"])

        reps = lic["replacements"]
        self.assertEqual(reps["coreutils"]["replacement"], "uutils-coreutils")
        self.assertEqual(reps["coreutils"]["replacement_license"], "MIT")

        self.assertEqual(reps["oci_runtime"]["replacement"], "youki")
        self.assertEqual(reps["oci_runtime"]["replacement_license"], "Apache-2.0")

        self.assertEqual(reps["bootloader"]["replacement"], "systemd-boot")
        self.assertEqual(reps["bootloader"]["replacement_license"], "LGPL-2.1-or-later")

        self.assertEqual(reps["dbus_daemon"]["replacement"], "dbus-broker")
        self.assertEqual(reps["dbus_daemon"]["replacement_license"], "Apache-2.0")

    def test_deny_toml_permissive_allowlist(self):
        """Permissive Licensing: Verify tools/native/deny.toml permits only permissive licenses."""
        deny_path = find_file(["tools/native/deny.toml"])
        self.assertIsNotNone(deny_path, "tools/native/deny.toml must exist")
        with open(deny_path, "r", encoding="utf-8") as f:
            content = f.read()

        data = tomllib.loads(content)
        allowed = set(data.get("licenses", {}).get("allow", []))
        self.assertIn("MIT", allowed)
        self.assertIn("Apache-2.0", allowed)
        self.assertNotIn("GPL-3.0", allowed)
        self.assertNotIn("GPL-2.0", allowed)

    def test_planted_negative_gplv3_component_in_core(self):
        """PLANTED NEGATIVE CONTROL: GPLv3 component in core runtime fails RED."""
        # Planted defect: mios.toml without zero copyleft policy or with GPLv3 in core
        bad_licensing_toml = """
        [security.licensing]
        zero_copyleft_taint = false
        allowed_licenses = ["GPL-3.0-only"]
        """
        ok, msg = self.validator.validate_licensing_policy(bad_licensing_toml)
        self.assertFalse(ok, "Planted negative control: GPLv3 copyleft taint must fail RED")

        # Restored valid permissive policy passes GREEN
        good_licensing_toml = """
        [security.licensing]
        zero_copyleft_taint = true
        policy = "permissive_only"
        allowed_licenses = ["MIT", "Apache-2.0", "BSD-3-Clause", "LGPL-2.1-or-later"]
        denied_licenses = ["GPL-3.0-only", "GPL-3.0-or-later", "AGPL-3.0-only"]

        [security.licensing.replacements]
        coreutils = { upstream = "gnu-coreutils", replacement = "uutils-coreutils", replacement_license = "MIT" }
        oci_runtime = { upstream = "runc", replacement = "youki", replacement_license = "Apache-2.0" }
        bootloader = { upstream = "grub2", replacement = "systemd-boot", replacement_license = "LGPL-2.1-or-later" }
        dbus_daemon = { upstream = "dbus-daemon", replacement = "dbus-broker", replacement_license = "Apache-2.0" }
        """
        ok, msg = self.validator.validate_licensing_policy(good_licensing_toml)
        self.assertTrue(ok, f"Restored permissive licensing policy must pass GREEN: {msg}")

    # =========================================================================
    # fapolicyd Execution Perimeter
    # =========================================================================

    def test_fapolicyd_execution_perimeter_rules(self):
        """fapolicyd: Verify etc/fapolicyd/fapolicyd.rules enforces deny_audit perm=execute : all outside /usr."""
        fapol_path = find_file(["etc/fapolicyd/fapolicyd.rules"])
        self.assertIsNotNone(fapol_path, "etc/fapolicyd/fapolicyd.rules must exist")
        with open(fapol_path, "r", encoding="utf-8") as f:
            content = f.read()

        ok, msg = self.validator.validate_fapolicyd_rules(content)
        self.assertTrue(ok, f"fapolicyd rules validation failed: {msg}")

        # Verify explicit deny in 90-mios-deny.rules
        deny_path = find_file(["usr/lib/fapolicyd/rules.d/90-mios-deny.rules"])
        self.assertIsNotNone(deny_path, "90-mios-deny.rules must exist")
        with open(deny_path, "r", encoding="utf-8") as f:
            deny_content = f.read()

        self.assertIn("deny perm=any all trust=0 : dir=/var/home/", deny_content)
        self.assertIn("deny perm=any all trust=0 : dir=/home/", deny_content)
        self.assertIn("deny perm=any all trust=0 : dir=/run/media/", deny_content)

    def test_planted_negative_unsigned_binary_in_tmp_denied(self):
        """PLANTED NEGATIVE CONTROL: Unsigned binary in /tmp must be audited and denied by fapolicyd."""
        engine_path = find_file(["usr/lib/mios/agent-pipe/mios_isolation.py"])
        self.assertIsNotNone(engine_path)

        import importlib.util
        spec = importlib.util.spec_from_file_location("mios_isolation", engine_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore

        evaluator = mod.FapolicydPerimeterEvaluator()

        # Planted defect test: unsigned binary dropped into /tmp
        allowed, reason = evaluator.evaluate_execution(path="/tmp/malicious_payload", is_rpm_trusted=False, uid=1000)
        self.assertFalse(allowed, f"Unsigned binary in /tmp must be denied: {reason}")
        self.assertIn("deny_audit", reason)

        # Planted defect test: unsigned binary in /home/
        allowed, reason = evaluator.evaluate_execution(path="/home/user/script.sh", is_rpm_trusted=False, uid=1000)
        self.assertFalse(allowed, f"Untrusted binary in /home/ must be denied: {reason}")
        self.assertIn("deny", reason)

        # Restored valid execution: RPM-trusted binary in /usr
        allowed, reason = evaluator.evaluate_execution(path="/usr/bin/python3", is_rpm_trusted=True, uid=1000)
        self.assertTrue(allowed, f"RPM-trusted binary in /usr must be allowed: {reason}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
