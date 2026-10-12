#!/usr/bin/env python3
# AI-hint: Automated two-sided verification test suite for Milestone M4 (WS-SEC 4-Tier Zero-Trust Sandbox Ladder & Tetragon eBPF Enforcement).
# AI-related: usr/lib/mios/agent-pipe/mios_isolation.py, usr/share/containers/systemd/mios-enforcer.container, usr/libexec/mios/ai/audit_chain.py
"""Automated Two-Sided Verification Test Suite for Milestone M4 (WS-SEC).

Requirements Verified:
1. 4-Tier Zero-Trust Sandbox Execution Ladder:
   - Tier 1: Bubblewrap (bwrap <1ms)
   - Tier 2: Rootless Podman/crun (~15ms with Container Device Interface CDI)
   - Tier 3: gVisor runsc (~40ms)
   - Tier 4: libkrun microVMs (~150ms)
   - Progressive taint-driven tier selection and container profile compilation.
2. Standalone Cilium Tetragon eBPF Quadlet Container:
   - usr/share/containers/systemd/mios-enforcer.container
   - Validates image, capabilities (CAP_BPF, CAP_SYS_ADMIN, etc.), volumes, User=mios-enforcer,
     and ConditionVirtualization=!wsl.
   - TracingPolicy attaching kprobe to sys_enter_connect with Sigkill on unauthorized egress.
3. Out-of-Process eBPF/Tetragon Syscall Tracing & Quarantine (<65ms SLA):
   - Positive control: Authorized socket connect to 127.0.0.1:8700 ($MIOS_AI_ENDPOINT) is permitted.
   - Negative control: Unauthorized outbound socket connect (e.g. 1.1.1.1:443) triggers in-kernel
     Sigkill (signal 9, latency <= 2ms) and container isolation in <65ms.
4. Architectural Law 5 (UNIFIED-AI-REDIRECTS) Enforcement:
   - Cryptographic Merkle-tree audit logging via usr/libexec/mios/ai/audit_chain.py.
   - Ed25519 digital signature of violation blocks and tamper detection.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from typing import Any, Dict, List, Optional, Tuple


def find_repo_roots() -> List[str]:
    """Find candidate roots across workspaces."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    parent_dir = os.path.abspath(os.path.join(script_dir, ".."))
    candidates = [
        parent_dir,
        os.getcwd(),
        os.path.normpath(r"C:\MiOS"),
        os.path.normpath(r"M:\MiOS\worktrees\mios-integration"),
        os.path.normpath(r"C:\-dev-loop"),
        os.path.normpath(r"C:\MiOS-RnD"),
        "/mnt/c/MiOS",
        "/mnt/m/MiOS/worktrees/mios-integration",
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


def find_file(relative_paths: List[str]) -> Optional[str]:
    """Find the first matching existing file across repo candidate roots."""
    for root in find_repo_roots():
        for rel in relative_paths:
            full = os.path.join(root, rel)
            if os.path.isfile(full):
                return full
            if os.path.isfile(rel):
                return rel
    return None


class TestMilestoneM4TetragonQuarantine(unittest.TestCase):
    """Two-sided test suite for Milestone M4 (WS-SEC)."""

    @classmethod
    def setUpClass(cls):
        # Locate mios_isolation.py
        isolation_path = find_file(["usr/lib/mios/agent-pipe/mios_isolation.py"])
        assert isolation_path is not None, "usr/lib/mios/agent-pipe/mios_isolation.py must exist"

        spec = importlib.util.spec_from_file_location("mios_isolation", isolation_path)
        assert spec is not None and spec.loader is not None, "Failed to load mios_isolation module spec"
        cls.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.mod)  # type: ignore

        # Locate mios-enforcer.container
        cls.enforcer_path = find_file(["usr/share/containers/systemd/mios-enforcer.container"])
        assert cls.enforcer_path is not None, "usr/share/containers/systemd/mios-enforcer.container must exist"

    def setUp(self):
        # Create dedicated tempdir for isolated audit logging per test
        self.test_dir = tempfile.mkdtemp(prefix="mios_m4_test_")
        self.audit_log_path = os.path.join(self.test_dir, "audit_chain.jsonl")
        self.audit_key_path = os.path.join(self.test_dir, "node_ed25519.key")

    def tearDown(self):
        shutil.rmtree(self.test_dir, ignore_errors=True)

    # =========================================================================
    # Task 1: 4-Tier Zero-Trust Sandbox Ladder & Progressive Tier Selection
    # =========================================================================

    def test_4tier_ladder_order_and_specifications(self):
        """Positive Control: Verify exact 4-tier ladder sequence and isolation boundaries."""
        ladder = self.mod.LADDER_ORDER
        self.assertEqual(len(ladder), 4, "Ladder must contain exactly 4 progressive tiers")
        self.assertEqual(ladder[0], "bwrap", "Tier 1 must be Bubblewrap (bwrap)")
        self.assertEqual(ladder[1], "crun", "Tier 2 must be Rootless Podman/crun with CDI")
        self.assertEqual(ladder[2], "runsc", "Tier 3 must be gVisor runsc")
        self.assertEqual(ladder[3], "krun", "Tier 4 must be libkrun microVM")

        specs = self.mod.TIER_SPECS
        # Tier 1 specifications
        t1 = specs["bwrap"]
        self.assertEqual(t1["overhead_ms"], "<1ms")
        self.assertEqual(t1["isolation_boundary"], "namespaces_seccomp_landlock")
        self.assertFalse(t1["hw_accel_required"])
        self.assertEqual(t1["runtime_binary"], "bwrap")

        # Tier 2 specifications
        t2 = specs["crun"]
        self.assertEqual(t2["overhead_ms"], "~15ms")
        self.assertEqual(t2["isolation_boundary"], "cgroups_v2_user_namespace_cdi")
        self.assertFalse(t2["hw_accel_required"])
        self.assertEqual(t2["cdi_manifest"], "/etc/cdi/nvidia.json")

        # Tier 3 specifications
        t3 = specs["runsc"]
        self.assertEqual(t3["overhead_ms"], "~40ms")
        self.assertEqual(t3["isolation_boundary"], "userspace_kernel_sentry_gofer")
        self.assertFalse(t3["hw_accel_required"])
        self.assertEqual(t3["runtime_binary"], "runsc")

        # Tier 4 specifications
        t4 = specs["krun"]
        self.assertEqual(t4["overhead_ms"], "~150ms")
        self.assertEqual(t4["isolation_boundary"], "virtualization_hw_kernel_virtio_net")
        self.assertTrue(t4["hw_accel_required"])
        self.assertEqual(t4["runtime_binary"], "krun")
        self.assertTrue(t4["dev_kvm_required"])

    def test_progressive_tier_selection(self):
        """Positive Control: Verify progressive tier selection across taint levels."""
        select_tier = self.mod.select_tier

        # Level 0 / Clean -> Tier 1 (bwrap)
        self.assertEqual(select_tier(0), "bwrap")
        self.assertEqual(select_tier("clean"), "bwrap")
        self.assertEqual(select_tier("none"), "bwrap")
        self.assertEqual(select_tier(False), "bwrap")

        # Level 1 / Low / CDI -> Tier 2 (crun)
        self.assertEqual(select_tier(1), "crun")
        self.assertEqual(select_tier("low"), "crun")
        self.assertEqual(select_tier("cdi"), "crun")

        # Level 2 / Medium / Tainted -> Tier 3 (runsc)
        self.assertEqual(select_tier(2), "runsc")
        self.assertEqual(select_tier("medium"), "runsc")
        self.assertEqual(select_tier("gvisor"), "runsc")
        self.assertEqual(select_tier("tainted"), "runsc")
        self.assertEqual(select_tier(True), "runsc")

        # Level 3+ / High / Critical -> Tier 4 (krun)
        self.assertEqual(select_tier(3), "krun")
        self.assertEqual(select_tier(4), "krun")
        self.assertEqual(select_tier("high"), "krun")
        self.assertEqual(select_tier("critical"), "krun")
        self.assertEqual(select_tier("microvm"), "krun")

    def test_positive_untainted_preserves_requested_tier(self):
        """Positive Control: Untainted safe operations run without promotion event."""
        ladder = self.mod.SandboxExecutionLadder(dev_kvm_present=True)

        tier, event = ladder.resolve_effective_tier(verb="read_file", tainted=False, requested_tier="bwrap")
        self.assertEqual(tier, "bwrap")
        self.assertIsNone(event, "Untainted action must emit zero promotion event")

        tier, event = ladder.resolve_effective_tier(verb="list_dir", tainted=False, requested_tier="crun")
        self.assertEqual(tier, "crun")
        self.assertIsNone(event)

    def test_positive_tainted_progressive_promotion(self):
        """Positive Control: Tainted operations promote progressively with audit events."""
        ladder = self.mod.SandboxExecutionLadder(dev_kvm_present=True)

        # Moderate taint promotes from Tier 1 to Tier 3 (runsc)
        tier, event = ladder.resolve_effective_tier(verb="read_file", tainted=True, requested_tier="bwrap")
        self.assertEqual(tier, "runsc")
        self.assertIsNotNone(event)
        self.assertEqual(event.get("event"), "firewall_promote")
        self.assertEqual(event.get("to_tier"), "runsc")

        # High-risk shell verb promotes to Tier 4 (krun microVM)
        tier, event = ladder.resolve_effective_tier(verb="run_shell", tainted=True, requested_tier="bwrap")
        self.assertEqual(tier, "krun")
        self.assertIsNotNone(event)
        self.assertEqual(event.get("event"), "firewall_promote")
        self.assertEqual(event.get("to_tier"), "krun")

        # Explicit taint level 2 promotes to runsc
        tier, event = ladder.resolve_effective_tier(verb="eval_code", tainted=True, requested_tier="bwrap", taint_level=2)
        self.assertEqual(tier, "runsc")
        self.assertEqual(event.get("to_tier"), "runsc")

        # Explicit taint level 3 promotes to krun
        tier, event = ladder.resolve_effective_tier(verb="eval_code", tainted=True, requested_tier="bwrap", taint_level=3)
        self.assertEqual(tier, "krun")
        self.assertEqual(event.get("to_tier"), "krun")

    def test_negative_missing_kvm_degrades_closed(self):
        """PLANTED NEGATIVE CONTROL: Tainted execution requiring KVM degrades CLOSED on missing /dev/kvm."""
        # Host without /dev/kvm acceleration
        ladder_no_kvm = self.mod.SandboxExecutionLadder(dev_kvm_present=False)

        tier, event = ladder_no_kvm.resolve_effective_tier(verb="execute_codegen", tainted=True, requested_tier="bwrap")
        self.assertEqual(tier, "firewall_block", "Missing /dev/kvm must trigger degrade-closed firewall_block")
        self.assertIsNotNone(event)
        self.assertEqual(event.get("status"), "denied")
        self.assertEqual(event.get("reason"), "hardware_acceleration_unavailable")
        self.assertEqual(event.get("missing_device"), "/dev/kvm")

        # Restored /dev/kvm passes GREEN with clean promotion
        ladder_with_kvm = self.mod.SandboxExecutionLadder(dev_kvm_present=True)
        tier, event = ladder_with_kvm.resolve_effective_tier(verb="execute_codegen", tainted=True, requested_tier="bwrap")
        self.assertEqual(tier, "krun")
        self.assertEqual(event.get("event"), "firewall_promote")

    def test_container_profile_compiler_all_tiers(self):
        """Positive Control: Verify container profile compiler produces valid configs for all 4 tiers."""
        compiler = self.mod.ContainerProfileCompiler

        for t in ["bwrap", "crun", "runsc", "krun"]:
            prof = compiler.compile_profile(tier=t, container_name=f"test-sandbox-{t}")
            self.assertEqual(prof["tier"], t)
            self.assertIn("overhead_ms", prof)
            self.assertIn("quadlet_definition", prof)
            self.assertIn("cli_args", prof)
            self.assertTrue(len(prof["cli_args"]) > 0)
            self.assertIn(f"test-sandbox-{t}", prof["quadlet_definition"])

    # =========================================================================
    # Task 2: Standalone Cilium Tetragon eBPF Quadlet & TracingPolicy
    # =========================================================================

    def test_tetragon_enforcer_quadlet_structure(self):
        """Positive Control: Verify usr/share/containers/systemd/mios-enforcer.container configuration."""
        with open(self.enforcer_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Quadlet unit validation
        self.assertIn("ContainerName=mios-enforcer", content)
        self.assertIn("Image=quay.io/cilium/tetragon:v1.4.2", content)
        self.assertIn("User=mios-enforcer", content, "Must declare User=mios-enforcer for Law-6 compliance")
        self.assertIn("ConditionVirtualization=!wsl", content, "Must be inert on WSL2 dev VM")
        self.assertIn("ConditionPathExists=/sys/kernel/debug", content)

        # Capabilities verification
        self.assertIn("AddCapability=CAP_BPF", content)
        self.assertIn("AddCapability=CAP_SYS_ADMIN", content)
        self.assertIn("AddCapability=CAP_SYS_RESOURCE", content)
        self.assertIn("AddCapability=CAP_SYS_PTRACE", content)
        self.assertIn("AddCapability=CAP_NET_ADMIN", content)

        # Volumes verification
        self.assertIn("Volume=/sys/kernel/debug:/sys/kernel/debug:rw", content)
        self.assertIn("Volume=/sys/fs/bpf:/sys/fs/bpf:rw", content)
        self.assertIn("Volume=/etc/tetragon/tetragon.tp.d:/etc/tetragon/tetragon.tp.d:ro", content)
        self.assertIn("Volume=/var/run/tetragon:/var/run/tetragon:rw", content)

        # TracingPolicy specification check in header/comments
        self.assertIn("sys_enter_connect", content)
        self.assertIn("Sigkill", content)

    def test_tetragon_tracing_policy_compiler(self):
        """Positive Control: Verify TracingPolicy attaches kprobe to sys_enter_connect with Sigkill."""
        policy_builder = self.mod.TetragonTracingPolicy
        policy = policy_builder.compile_policy(
            name="agent-network-egress-quarantine",
            authorized_ports=[8700, 8500, 8520, 8600, 11450],
        )

        self.assertEqual(policy["apiVersion"], "cilium.io/v1alpha1")
        self.assertEqual(policy["kind"], "TracingPolicy")
        self.assertEqual(policy["metadata"]["name"], "agent-network-egress-quarantine")

        kprobes = policy["spec"]["kprobes"]
        self.assertEqual(len(kprobes), 1)
        kp = kprobes[0]
        self.assertEqual(kp["call"], "sys_enter_connect")
        self.assertTrue(kp["syscall"])

        selectors = kp["selectors"]
        self.assertTrue(len(selectors) > 0)
        sel = selectors[0]
        self.assertIn("mios-agents", sel["matchNamespaces"])
        self.assertIn({"action": "Sigkill"}, sel["matchActions"])

        # Check YAML export
        yaml_text = policy_builder.to_yaml(policy)
        self.assertIn("call: sys_enter_connect", yaml_text)
        self.assertIn("action: Sigkill", yaml_text)

    # =========================================================================
    # Task 3 & 4: Out-of-Process Syscall Tracing, <65ms Quarantine & Law 5 Audit
    # =========================================================================

    def test_positive_authorized_socket_connect_permitted(self):
        """Positive Control: Authorized socket connect to 127.0.0.1:8700 is permitted without quarantine."""
        monitor = self.mod.TetragonQuarantineMonitor(
            authorized_ports=[8700, 8500, 8520, 8600, 11450],
            audit_log_path=self.audit_log_path,
            audit_key_path=self.audit_key_path,
        )

        # 1. Connect to primary AI endpoint 127.0.0.1:8700
        res = monitor.evaluate_connect(dest_ip="127.0.0.1", dest_port=8700, pid=2048, comm="agent_hermes")
        self.assertEqual(res["status"], "allowed")
        self.assertFalse(res["quarantined"])
        self.assertFalse(res["sigkill_issued"])
        self.assertEqual(res["reason"], "authorized_local_ai_endpoint")
        self.assertTrue(res["latency_ms"] < 5.0)

        # 2. Connect to local inference lane 127.0.0.1:8500
        res2 = monitor.evaluate_connect(dest_ip="127.0.0.1", dest_port=8500, pid=2049, comm="agent_opencode")
        self.assertEqual(res2["status"], "allowed")
        self.assertFalse(res2["quarantined"])

        # 3. Connect to heavy inference lane 127.0.0.1:8520
        res3 = monitor.evaluate_connect(dest_ip="127.0.0.1", dest_port=8520, pid=2050, comm="agent_vision")
        self.assertEqual(res3["status"], "allowed")
        self.assertFalse(res3["quarantined"])

        # 4. Verify audit chain remains pristine and empty of violations
        ver = monitor.verify_audit_chain()
        self.assertTrue(ver["valid"])
        self.assertEqual(ver["blocks_verified"], 0)

    def test_negative_unauthorized_external_socket_triggers_sigkill_and_quarantine(self):
        """Negative Control: Unauthorized outbound socket (1.1.1.1:443) triggers Sigkill & <65ms quarantine."""
        monitor = self.mod.TetragonQuarantineMonitor(
            authorized_ports=[8700, 8500, 8520, 8600, 11450],
            audit_log_path=self.audit_log_path,
            audit_key_path=self.audit_key_path,
        )

        # Attempt unauthorized connection to Cloudflare DNS 1.1.1.1:443
        res = monitor.evaluate_connect(
            dest_ip="1.1.1.1",
            dest_port=443,
            pid=4096,
            comm="untrusted_script",
            container_id="sandbox-coderun-042",
        )

        # Verify in-kernel Sigkill
        self.assertEqual(res["status"], "quarantined")
        self.assertTrue(res["quarantined"])
        self.assertTrue(res["sigkill_issued"])
        self.assertEqual(res["signal"], 9)
        self.assertLessEqual(res["in_kernel_latency_ms"], 2.0, "In-kernel Sigkill must fire in <= 2.0ms")

        # Verify <65ms quarantine SLA
        self.assertTrue(res["sla_met"])
        self.assertLess(res["quarantine_latency_ms"], 65.0, "Container quarantine must complete within < 65ms SLA")

        # Verify Architectural Law 5 violation attribution
        self.assertEqual(res["law_violation"], "Law 5 (UNIFIED-AI-REDIRECTS)")

        # Verify Ed25519 Merkle-tree cryptographic audit block was created
        audit_block = res["audit_block"]
        self.assertIsNotNone(audit_block)
        self.assertEqual(audit_block["event_type"], "law5_egress_violation")
        self.assertEqual(audit_block["payload"]["dest_ip"], "1.1.1.1")
        self.assertEqual(audit_block["payload"]["dest_port"], 443)
        self.assertTrue(audit_block["payload"]["sla_met"])

        # Verify digital signature presence
        sig = res.get("signature")
        self.assertIsNotNone(sig)
        self.assertTrue(len(sig) > 0)

        # Verify cryptographic audit chain integrity
        ver = monitor.verify_audit_chain()
        self.assertTrue(ver["valid"])
        self.assertEqual(ver["status"], "verified")
        self.assertGreaterEqual(ver["blocks_verified"], 2)  # Genesis + violation block

    def test_negative_multiple_unauthorized_egress_destinations(self):
        """Negative Control: Intercepts multiple external targets and builds continuous Merkle chain."""
        monitor = self.mod.TetragonQuarantineMonitor(
            authorized_ports=[8700, 8500, 8520, 8600, 11450],
            audit_log_path=self.audit_log_path,
            audit_key_path=self.audit_key_path,
        )

        bad_targets = [
            ("8.8.8.8", 53),
            ("198.51.100.1", 80),
            ("203.0.113.5", 8080),
        ]

        for ip, port in bad_targets:
            res = monitor.evaluate_connect(dest_ip=ip, dest_port=port, pid=5000, comm="exfil_probe")
            self.assertTrue(res["quarantined"])
            self.assertTrue(res["sigkill_issued"])
            self.assertLess(res["quarantine_latency_ms"], 65.0)

        # Verify complete multi-block Merkle tree integrity
        ver = monitor.verify_audit_chain()
        self.assertTrue(ver["valid"])
        self.assertEqual(ver["status"], "verified")
        self.assertEqual(ver["blocks_verified"], len(bad_targets) + 1)  # Genesis + 3 violations

    def test_planted_negative_tampered_audit_chain_fails_red(self):
        """PLANTED NEGATIVE CONTROL: Tampered audit record breaks Merkle verification (fails RED)."""
        monitor = self.mod.TetragonQuarantineMonitor(
            authorized_ports=[8700, 8500, 8520, 8600, 11450],
            audit_log_path=self.audit_log_path,
            audit_key_path=self.audit_key_path,
        )

        # Record a violation
        monitor.evaluate_connect(dest_ip="1.1.1.1", dest_port=443, pid=9999, comm="rogue_worker")

        # Confirm chain is valid before tampering
        ver_clean = monitor.verify_audit_chain()
        self.assertTrue(ver_clean["valid"])

        # Read recorded blocks
        with open(self.audit_log_path, "r", encoding="utf-8") as f:
            lines = [json.loads(line) for line in f if line.strip()]

        import copy

        # Planted defect: tamper with payload in block index 1 (modify destination IP)
        tampered_blocks = copy.deepcopy(lines)
        tampered_blocks[1]["payload"]["dest_ip"] = "127.0.0.1"  # Attempt to forge authorized IP

        # Verify that tampered chain fails RED with payload_tampered
        ver_tampered = monitor.recorder.verify_chain(tampered_blocks)
        self.assertFalse(ver_tampered["valid"], "Tampered audit chain must fail RED")
        self.assertEqual(ver_tampered["status"], "payload_tampered")
        self.assertEqual(ver_tampered["failed_block_index"], 1)

        # Planted defect 2: tamper with signature in block index 1
        tampered_blocks_sig = copy.deepcopy(lines)
        tampered_blocks_sig[1]["signature"] = "0" * 128
        ver_bad_sig = monitor.recorder.verify_chain(tampered_blocks_sig)
        self.assertFalse(ver_bad_sig["valid"], "Forged signature must fail RED")

        # Restored original chain passes GREEN
        ver_restored = monitor.recorder.verify_chain(lines)
        self.assertTrue(ver_restored["valid"], "Restored original chain must pass GREEN")
        self.assertEqual(ver_restored["status"], "verified")


if __name__ == "__main__":
    unittest.main(verbosity=2)
