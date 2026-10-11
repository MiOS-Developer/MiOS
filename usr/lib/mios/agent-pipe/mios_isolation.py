#!/usr/bin/env python3
# AI-hint: 4-Tier Sandbox Execution Ladder and Permissive Licensing Verification Engine (T-169 / ISOL-01 / Milestone M5).
# AI-related: usr/libexec/mios/mios-sandbox-exec, usr/share/containers/containers.conf.d/20-mios-krun-runtime.conf, etc/cdi/nvidia.json, etc/fapolicyd/fapolicyd.rules
"""MiOS 4-Tier Sandbox Execution Ladder & Permissive Licensing Engine.

Milestone M5:
Tier 1: Bubblewrap (bwrap <1ms) with --ro-bind / /, --unshare-all, --cap-drop ALL, seccomp filter on fd 9.
Tier 2: Rootless OCI (crun ~15ms) with CDI (/etc/cdi/nvidia.json) allocating GPU without --privileged.
Tier 3: MicroVMs (libkrun <=90ms) with /dev/kvm hardware acceleration and virtio-net namespace isolation.
Tier 4: Hardware VMs (KVM/VFIO) with direct PCIe passthrough and IVSHMEM looking-glass.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional, Tuple


TIER_1_BWRAP = "bwrap"
TIER_2_CRUN = "crun"
TIER_3_KRUN = "krun"
TIER_4_HARDWARE_VM = "hardware_vm"

LADDER_ORDER = [TIER_1_BWRAP, TIER_2_CRUN, TIER_3_KRUN, TIER_4_HARDWARE_VM]

TIER_SPECS: Dict[str, Dict[str, Any]] = {
    TIER_1_BWRAP: {
        "name": "Tier 1: Bubblewrap Userspace Sandbox",
        "overhead_ms": "<1ms",
        "isolation_boundary": "namespaces_seccomp_landlock",
        "hw_accel_required": False,
        "runtime_binary": "bwrap",
        "required_flags": ["--ro-bind / /", "--unshare-all", "--cap-drop ALL", "--seccomp 9"],
    },
    TIER_2_CRUN: {
        "name": "Tier 2: Rootless OCI Container",
        "overhead_ms": "~15ms",
        "isolation_boundary": "cgroups_v2_user_namespace_cdi",
        "hw_accel_required": False,
        "runtime_binary": "crun",
        "cdi_manifest": "/etc/cdi/nvidia.json",
        "requires_privileged": False,
    },
    TIER_3_KRUN: {
        "name": "Tier 3: MicroVM (libkrun)",
        "overhead_ms": "<=90ms",
        "isolation_boundary": "virtualization_hw_kernel_virtio_net",
        "hw_accel_required": True,
        "runtime_binary": "krun",
        "dev_kvm_required": True,
        "network_isolation": "virtio-net single-interface bridge namespace",
    },
    TIER_4_HARDWARE_VM: {
        "name": "Tier 4: Full Hardware VM (KVM/VFIO)",
        "overhead_ms": ">1000ms",
        "isolation_boundary": "iommu_pcie_vfio_hardware_boundary",
        "hw_accel_required": True,
        "runtime_binary": "qemu-system-x86_64",
        "dev_kvm_required": True,
        "vfio_passthrough": True,
        "ivshmem_buffer": "128MB kvmfr0",
    },
}

ALLOWED_LICENSES = {
    "MIT",
    "Apache-2.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "LGPL-2.1-or-later",
    "ISC",
    "Zlib",
    "CC0-1.0",
    "Unicode-3.0",
}

COPYLEFT_TAINT_LICENSES = {
    "GPL-3.0-only",
    "GPL-3.0-or-later",
    "GPL-2.0-only",
    "GPL-2.0-or-later",
    "AGPL-3.0-only",
    "AGPL-3.0-or-later",
    "SSPL-1.0",
}

LICENSING_REPLACEMENTS = {
    "gnu-coreutils": {
        "replacement": "uutils-coreutils",
        "license": "MIT",
        "upstream_license": "GPL-3.0-or-later",
        "eliminates": "GPLv3 copyleft taint in coreutils",
    },
    "runc": {
        "replacement": "youki",
        "license": "Apache-2.0",
        "upstream_license": "GPL-2.0-only",
        "eliminates": "GPLv2 copyleft taint in container runtime",
    },
    "grub2": {
        "replacement": "systemd-boot",
        "license": "LGPL-2.1-or-later",
        "upstream_license": "GPL-3.0-or-later",
        "eliminates": "GPLv3 copyleft taint in bootloader",
    },
    "dbus-daemon": {
        "replacement": "dbus-broker",
        "license": "Apache-2.0",
        "upstream_license": "GPL-2.0-or-later",
        "eliminates": "GPLv2 copyleft taint in system bus daemon",
    },
}


class SandboxExecutionLadder:
    """Manages 4-tier execution containment, taint promotion, and hardware gating."""

    def __init__(self, dev_kvm_present: bool = True) -> None:
        self.dev_kvm_present = dev_kvm_present

    def resolve_effective_tier(
        self,
        verb: str,
        tainted: bool,
        requested_tier: str = TIER_1_BWRAP,
    ) -> Tuple[str, Optional[Dict[str, Any]]]:
        """Resolves the effective sandbox tier based on taint state and hardware availability.

        Implements task_170 ISOL-01:
        - Tainted high-risk execution promotes from Tier 1/2 to Tier 3 (MicroVM) or Tier 4.
        - Emits a 'firewall_promote' event on promotion.
        - Degrades CLOSED to 'firewall_block' if promoted tier requires /dev/kvm and it is absent.
        """
        if not tainted:
            return requested_tier, None

        # Tainted session promotion
        current_idx = LADDER_ORDER.index(requested_tier) if requested_tier in LADDER_ORDER else 0
        promoted_tier = TIER_3_KRUN if current_idx < 2 else TIER_4_HARDWARE_VM

        # Check hardware acceleration requirement
        if TIER_SPECS[promoted_tier]["hw_accel_required"] and not self.dev_kvm_present:
            # Degrade closed: cannot run tainted workload safely without required hardware isolation
            return "firewall_block", {
                "event": "firewall_block",
                "reason": "hardware_acceleration_unavailable",
                "attempted_promotion": promoted_tier,
                "missing_device": "/dev/kvm",
                "status": "denied",
            }

        event = {
            "event": "firewall_promote",
            "from_tier": requested_tier,
            "to_tier": promoted_tier,
            "reason": "tainted_session_high_risk_verb",
            "verb": verb,
        }
        return promoted_tier, event

    @staticmethod
    def validate_bwrap_args(args: List[str]) -> Tuple[bool, List[str]]:
        """Verifies that Bubblewrap invocation includes all mandatory containment flags."""
        missing = []
        if "--ro-bind / /" not in " ".join(args):
            missing.append("--ro-bind / /")
        if "--unshare-all" not in args:
            missing.append("--unshare-all")
        if "--cap-drop" not in args or "ALL" not in args:
            missing.append("--cap-drop ALL")
        if "--seccomp" not in args or "9" not in args:
            missing.append("--seccomp 9")
        return (len(missing) == 0, missing)

    @staticmethod
    def validate_cdi_spec(spec: Dict[str, Any]) -> Tuple[bool, str]:
        """Validates that a CDI JSON specification complies with CDI v0.6.0 and isolates GPU nodes."""
        if spec.get("cdiVersion") != "0.6.0":
            return False, f"Invalid cdiVersion: expected '0.6.0', got '{spec.get('cdiVersion')}'"
        if spec.get("kind") != "nvidia.com/gpu":
            return False, f"Invalid kind: expected 'nvidia.com/gpu', got '{spec.get('kind')}'"

        devices = spec.get("devices", [])
        if not devices:
            return False, "CDI specification contains 0 devices"

        names = {d.get("name") for d in devices}
        if "all" not in names:
            return False, "Missing composite 'all' device in CDI spec"

        # Check for device nodes
        for dev in devices:
            container_edits = dev.get("containerEdits", {})
            nodes = [n.get("path") for n in container_edits.get("deviceNodes", [])]
            if not any(p.startswith("/dev/nvidia") for p in nodes if p):
                return False, f"Device '{dev.get('name')}' contains no /dev/nvidia* device nodes"

        return True, "Valid CDI specification binding /dev/nvidia* without --privileged"


class LicensingPolicyValidator:
    """Validates permissive licensing compliance and elimination of copyleft taint."""

    @staticmethod
    def check_license_compliance(spdx_license: str) -> Tuple[bool, str]:
        """Checks whether a license conforms to the Zero Copyleft Taint Policy."""
        if spdx_license in ALLOWED_LICENSES:
            return True, f"Permissive license '{spdx_license}' approved"
        if spdx_license in COPYLEFT_TAINT_LICENSES:
            return False, f"COPYLEFT TAINT DETECTED: License '{spdx_license}' violates zero copyleft policy"
        return False, f"License '{spdx_license}' is not in approved permissive roster"

    @staticmethod
    def get_replacement_info(upstream_name: str) -> Optional[Dict[str, str]]:
        """Returns details of permissive replacement for a GPL copyleft component."""
        return LICENSING_REPLACEMENTS.get(upstream_name)


class FapolicydPerimeterEvaluator:
    """Evaluates fapolicyd execution rules and verifies deny-by-default outside /usr."""

    @staticmethod
    def evaluate_execution(path: str, is_rpm_trusted: bool, uid: int = 1000) -> Tuple[bool, str]:
        """Evaluates whether an execution attempt is permitted or denied by fapolicyd rules."""
        norm_path = path.replace("\\", "/")

        # Rule 1: allow perm=any uid=0 trust=1 : all
        if uid == 0 and is_rpm_trusted:
            return True, "allow: root with RPM trust (trust=1)"

        # Rule 2 & 3: allow perm=execute trust=1
        if is_rpm_trusted and norm_path.startswith("/usr/"):
            return True, "allow: RPM-trusted binary in /usr/ (trust=1)"

        # Rule 4 & 5: carve-outs for MCP AI working directories
        if norm_path.startswith("/var/lib/mios/ai/") or norm_path.startswith("/srv/ai/mcp/"):
            return True, "allow: trusted MCP working directory carve-out"

        # Rule 6: carve-outs for agent coderun sandbox
        if norm_path.startswith("/var/home/mios/coderuns/") or norm_path.startswith("/var/home/mios/.coderun-snapshots/"):
            return True, "allow: agent coderun sandbox carve-out"

        # Explicit deny in 90-mios-deny.rules
        if not is_rpm_trusted and (
            norm_path.startswith("/home/")
            or norm_path.startswith("/var/home/")
            or norm_path.startswith("/run/media/")
            or norm_path.startswith("/mnt/")
        ):
            return False, "deny: explicit deny rule for untrusted binary in user home or external media (90-mios-deny.rules)"

        # Catch-all deny_audit perm=execute : all (e.g. /tmp, /var, etc.)
        return False, "deny_audit: unsigned/untrusted binary outside /usr denied by default (fapolicyd.rules:29)"
