#!/usr/bin/env python3
# AI-hint: Automated IOMMU group parser and PCIe ACS override topology recommendation tool (task_357).
# AI-related: tests/test-virt.py, usr/share/doc/mios/manual/ch21-looking-glass-b7-and-kvmfr.md
"""
MiOS IOMMU Group Parser and PCIe ACS Override Topology Auditor.
Audits /sys/kernel/iommu_groups/ to verify target GPU isolation for VFIO passthrough.
Detects multifunction companion devices and shared root port/endpoint conflicts.
Recommends UKI-baked kernel command-line arguments (pcie_acs_override) when isolation is broken.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import sys
from typing import Any, Dict, List, Optional, Tuple

PCI_CLASS_MAP = {
    "0x0100": "SCSI storage controller",
    "0x0101": "IDE interface",
    "0x0104": "RAID bus controller",
    "0x0106": "SATA controller (AHCI)",
    "0x0108": "Non-Volatile memory controller (NVMe)",
    "0x0200": "Ethernet controller",
    "0x0280": "Network controller (Wireless)",
    "0x0300": "VGA compatible controller",
    "0x0302": "3D controller",
    "0x0401": "Multimedia audio controller",
    "0x0403": "Audio device (HD Audio / Soundwire)",
    "0x0600": "Host bridge",
    "0x0601": "ISA bridge",
    "0x0604": "PCI bridge (Root Port / Switch)",
    "0x0c03": "USB controller (xHCI/EHCI)",
    "0x0c05": "SMBus controller",
}

def decode_pci_class(class_code_raw: str) -> str:
    """Decodes hex PCI class code (e.g. 0x030000 or 030000) to human-readable string."""
    cleaned = class_code_raw.strip().lower()
    if cleaned.startswith("0x"):
        cleaned = cleaned[2:]
    # Pad to 6 hex chars if needed
    cleaned = cleaned.zfill(6)
    base_sub = f"0x{cleaned[:4]}"
    return PCI_CLASS_MAP.get(base_sub, f"PCI device ({base_sub})")

@dataclasses.dataclass
class PCIDevice:
    """Represents a physical or virtual PCI device endpoint."""
    bdf: str
    domain: str
    bus: str
    slot: str
    function: str
    vendor_id: str
    device_id: str
    class_code: str
    class_name: str
    driver: Optional[str] = None
    iommu_group: Optional[int] = None
    boot_vga: bool = False

    @property
    def slot_address(self) -> str:
        """Returns domain:bus:slot address without function (e.g. 0000:01:00)."""
        return f"{self.domain}:{self.bus}:{self.slot}"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "bdf": self.bdf,
            "slot_address": self.slot_address,
            "domain": self.domain,
            "bus": self.bus,
            "slot": self.slot,
            "function": self.function,
            "vendor_id": self.vendor_id,
            "device_id": self.device_id,
            "class_code": self.class_code,
            "class_name": self.class_name,
            "driver": self.driver,
            "iommu_group": self.iommu_group,
            "boot_vga": self.boot_vga,
        }

class IOMMUParser:
    """Parses sysfs IOMMU groups and assesses isolation status for VFIO passthrough."""

    BDF_PATTERN = re.compile(r"^([0-9a-fA-F]{4})[:_]([0-9a-fA-F]{2})[:_]([0-9a-fA-F]{2})\.([0-7])$")

    def __init__(self, sysfs_root: str = "/sys", mock: bool = False) -> None:
        self.sysfs_root = sysfs_root
        self.mock = mock

    @classmethod
    def sanitize_bdf_for_fs(cls, bdf_str: str) -> str:
        """Sanitizes BDF string for filesystems (like NTFS) that forbid colons."""
        if os.name == "nt":
            return bdf_str.replace(":", "_")
        return bdf_str

    @classmethod
    def parse_bdf(cls, bdf_str: str) -> Tuple[str, str, str, str]:
        """Normalizes and extracts (domain, bus, slot, function) from BDF string."""
        raw = bdf_str.strip()
        m = cls.BDF_PATTERN.match(raw)
        if not m:
            # Try matching short form bus:slot.function (e.g. 01:00.0 -> 0000:01:00.0)
            short_m = re.match(r"^([0-9a-fA-F]{2})[:_]([0-9a-fA-F]{2})\.([0-7])$", raw)
            if short_m:
                return "0000", short_m.group(1).lower(), short_m.group(2).lower(), short_m.group(3)
            raise ValueError(f"Invalid PCI BDF identifier format: {bdf_str}")
        return m.group(1).lower(), m.group(2).lower(), m.group(3).lower(), m.group(4)

    def _read_sysfs_file(self, path: str, default: str = "") -> str:
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read().strip()
        except OSError:
            return default

    def _get_mock_groups(self) -> Dict[int, List[PCIDevice]]:
        """Provides default synthetic IOMMU groups when running in mock mode."""
        gpu_vga = PCIDevice(
            bdf="0000:01:00.0",
            domain="0000",
            bus="01",
            slot="00",
            function="0",
            vendor_id="0x10de",
            device_id="0x2484",
            class_code="0x030000",
            class_name="VGA compatible controller",
            driver="nvidia",
            iommu_group=13,
            boot_vga=False,
        )
        gpu_audio = PCIDevice(
            bdf="0000:01:00.1",
            domain="0000",
            bus="01",
            slot="00",
            function="1",
            vendor_id="0x10de",
            device_id="0x228b",
            class_code="0x040300",
            class_name="Audio device (HD Audio / Soundwire)",
            driver="snd_hda_intel",
            iommu_group=13,
            boot_vga=False,
        )
        host_bridge = PCIDevice(
            bdf="0000:00:00.0",
            domain="0000",
            bus="00",
            slot="00",
            function="0",
            vendor_id="0x8086",
            device_id="0x4660",
            class_code="0x060000",
            class_name="Host bridge",
            driver=None,
            iommu_group=0,
            boot_vga=False,
        )
        igpu = PCIDevice(
            bdf="0000:00:02.0",
            domain="0000",
            bus="00",
            slot="02",
            function="0",
            vendor_id="0x8086",
            device_id="0x4680",
            class_code="0x030000",
            class_name="VGA compatible controller",
            driver="i915",
            iommu_group=1,
            boot_vga=True,
        )
        return {
            0: [host_bridge],
            1: [igpu],
            13: [gpu_vga, gpu_audio],
        }

    def parse_groups(self) -> Dict[int, List[PCIDevice]]:
        """Parses all IOMMU groups from sysfs hierarchy."""
        if self.mock:
            return self._get_mock_groups()

        groups_dir = os.path.join(self.sysfs_root, "kernel", "iommu_groups")
        pci_devices_dir = os.path.join(self.sysfs_root, "bus", "pci", "devices")

        groups: Dict[int, List[PCIDevice]] = {}

        if os.path.exists(groups_dir):
            try:
                entries = sorted(os.listdir(groups_dir), key=lambda x: int(x) if x.isdigit() else 999999)
            except OSError:
                entries = []

            for entry in entries:
                if not entry.isdigit():
                    continue
                group_id = int(entry)
                dev_dir = os.path.join(groups_dir, entry, "devices")
                if not os.path.isdir(dev_dir):
                    continue
                dev_list: List[PCIDevice] = []
                try:
                    bdfs = os.listdir(dev_dir)
                except OSError:
                    bdfs = []
                for bdf in sorted(bdfs):
                    dev = self._parse_device_from_sysfs(bdf, group_id=group_id)
                    if dev:
                        dev_list.append(dev)
                if dev_list:
                    groups[group_id] = dev_list
        elif os.path.exists(pci_devices_dir):
            # Fallback: inspect /sys/bus/pci/devices/<bdf>/iommu_group symlink
            try:
                bdfs = os.listdir(pci_devices_dir)
            except OSError:
                bdfs = []
            for bdf in sorted(bdfs):
                dev_path = os.path.join(pci_devices_dir, bdf)
                iommu_link = os.path.join(dev_path, "iommu_group")
                group_id: Optional[int] = None
                if os.path.exists(iommu_link):
                    try:
                        target = os.readlink(iommu_link)
                        group_name = os.path.basename(target)
                        if group_name.isdigit():
                            group_id = int(group_name)
                    except OSError:
                        group_id = None
                if group_id is not None:
                    dev = self._parse_device_from_sysfs(bdf, group_id=group_id)
                    if dev:
                        groups.setdefault(group_id, []).append(dev)

        return groups

    def _parse_device_from_sysfs(self, raw_bdf: str, group_id: Optional[int] = None) -> Optional[PCIDevice]:
        norm_bdf_str = raw_bdf.replace("_", ":")
        try:
            domain, bus, slot, func = self.parse_bdf(norm_bdf_str)
        except ValueError:
            return None

        # Look in bus/pci/devices or kernel/iommu_groups/<group>/devices
        candidates = [
            os.path.join(self.sysfs_root, "bus", "pci", "devices", raw_bdf),
            os.path.join(self.sysfs_root, "bus", "pci", "devices", norm_bdf_str),
            os.path.join(self.sysfs_root, "bus", "pci", "devices", raw_bdf.replace(":", "_")),
        ]
        if group_id is not None:
            candidates.extend([
                os.path.join(self.sysfs_root, "kernel", "iommu_groups", str(group_id), "devices", raw_bdf),
                os.path.join(self.sysfs_root, "kernel", "iommu_groups", str(group_id), "devices", norm_bdf_str),
                os.path.join(self.sysfs_root, "kernel", "iommu_groups", str(group_id), "devices", raw_bdf.replace(":", "_")),
            ])

        dev_path: Optional[str] = None
        for cand in candidates:
            if os.path.exists(cand):
                dev_path = cand
                break

        if not dev_path:
            return None

        vendor_raw = self._read_sysfs_file(os.path.join(dev_path, "vendor"), "0x0000")
        device_raw = self._read_sysfs_file(os.path.join(dev_path, "device"), "0x0000")
        class_raw = self._read_sysfs_file(os.path.join(dev_path, "class"), "0x000000")
        boot_vga_raw = self._read_sysfs_file(os.path.join(dev_path, "boot_vga"), "0")

        # Determine driver
        driver: Optional[str] = None
        driver_path = os.path.join(dev_path, "driver")
        if os.path.exists(driver_path):
            try:
                driver = os.path.basename(os.readlink(driver_path))
            except OSError:
                driver = "bound"

        class_name = decode_pci_class(class_raw)
        boot_vga = (boot_vga_raw.strip() == "1")

        canonical_bdf = f"{domain}:{bus}:{slot}.{func}"
        return PCIDevice(
            bdf=canonical_bdf,
            domain=domain,
            bus=bus,
            slot=slot,
            function=func,
            vendor_id=vendor_raw,
            device_id=device_raw,
            class_code=class_raw,
            class_name=class_name,
            driver=driver,
            iommu_group=group_id,
            boot_vga=boot_vga,
        )

    def find_device(self, target_bdf: str) -> Optional[PCIDevice]:
        """Locates device metadata across all parsed IOMMU groups."""
        try:
            domain, bus, slot, func = self.parse_bdf(target_bdf)
            norm_bdf = f"{domain}:{bus}:{slot}.{func}"
        except ValueError:
            return None

        groups = self.parse_groups()
        for _, dev_list in groups.items():
            for dev in dev_list:
                if dev.bdf.lower() == norm_bdf.lower():
                    return dev
        return None

    def audit_isolation(self, target_bdf: str) -> Dict[str, Any]:
        """
        Audits whether target device is cleanly isolated in its IOMMU group.
        Companion functions on the same physical slot (same domain:bus:slot) are legitimate siblings.
        External devices sharing the group are flagged as conflicts requiring PCIe ACS override.
        """
        try:
            domain, bus, slot, func = self.parse_bdf(target_bdf)
            norm_bdf = f"{domain}:{bus}:{slot}.{func}"
        except ValueError as e:
            return {
                "status": "error",
                "target_bdf": target_bdf,
                "error": str(e),
                "isolated": False,
            }

        groups = self.parse_groups()
        target_group_id: Optional[int] = None
        target_dev: Optional[PCIDevice] = None

        for gid, dev_list in groups.items():
            for dev in dev_list:
                if dev.bdf.lower() == norm_bdf.lower():
                    target_group_id = gid
                    target_dev = dev
                    break
            if target_dev:
                break

        if not target_dev or target_group_id is None:
            return {
                "status": "not_found",
                "target_bdf": norm_bdf,
                "error": f"Device {norm_bdf} not found in any IOMMU group.",
                "isolated": False,
                "iommu_enabled": len(groups) > 0,
            }

        group_devices = groups[target_group_id]
        target_slot = target_dev.slot_address

        companions: List[Dict[str, Any]] = []
        conflicts: List[Dict[str, Any]] = []

        for dev in group_devices:
            if dev.slot_address == target_slot:
                companions.append(dev.to_dict())
            else:
                conflicts.append(dev.to_dict())

        is_isolated = (len(conflicts) == 0)

        # Build recommendation adhering to Architectural Invariant 2 (UKI vs MOK)
        # and Invariant 3 (venus vs CUDA)
        recommendation: str
        uki_kargs: Optional[str] = None
        security_warning: Optional[str] = None

        if is_isolated:
            recommendation = (
                "Clean hardware IOMMU isolation confirmed. All group members belong to the same physical slot. "
                "No ACS override kernel arguments required."
            )
        else:
            uki_kargs = "pcie_acs_override=downstream,multifunction"
            recommendation = (
                f"IOMMU Group {target_group_id} contains {len(conflicts)} conflicting shared device(s). "
                "Hardware isolation is insufficient for direct VFIO passthrough without unbinding conflicting devices. "
                "To split IOMMU groups, rebuild the signed Unified Kernel Image (UKI) with "
                f"'{uki_kargs}'. Note: Kernel arguments are baked into the signed UKI (shim -> systemd-boot -> UKI), "
                "not injected via runtime MOK."
            )
            security_warning = (
                "PCIe ACS override bypasses hardware isolation boundaries. Devices in the same physical group "
                "may theoretically perform peer-to-peer DMA snooping."
            )

        return {
            "status": "pass" if is_isolated else "conflict",
            "target_bdf": norm_bdf,
            "target_device": target_dev.to_dict(),
            "iommu_group": target_group_id,
            "isolated": is_isolated,
            "total_group_devices": len(group_devices),
            "companions": companions,
            "conflicts": conflicts,
            "recommendation": recommendation,
            "uki_kargs": uki_kargs,
            "security_warning": security_warning,
            "invariants": {
                "uki_vs_mok": "Kernel cmdline parameters must be baked into signed UKI; MOK only governs out-of-tree module signing.",
                "venus_vs_cuda": "venus VirtIO-GPU is graphics/Vulkan only; CUDA guest acceleration requires whole-device VFIO passthrough.",
            },
        }

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS IOMMU Group Parser and PCIe ACS Override Topology Auditor."
    )
    parser.add_argument("--audit", action="store_true", help="Audit IOMMU isolation for target device.")
    parser.add_argument("--device", "-d", type=str, help="Target PCI device BDF (e.g. 0000:01:00.0 or 01:00.0).")
    parser.add_argument("--list-groups", action="store_true", help="List all detected IOMMU groups and devices.")
    parser.add_argument("--sysfs-root", type=str, default="/sys", help="Custom sysfs root path for synthetic testing.")
    parser.add_argument("--mock", action="store_true", help="Use built-in mock IOMMU topology.")
    parser.add_argument("--json", action="store_true", help="Format output as JSON.")
    args = parser.parse_args()

    # Automatically enable mock mode on Windows if real sysfs is missing
    is_mock = args.mock or (os.name == "nt" and not os.path.exists(os.path.join(args.sysfs_root, "kernel", "iommu_groups")))
    iommu = IOMMUParser(sysfs_root=args.sysfs_root, mock=is_mock)

    if args.list_groups:
        groups = iommu.parse_groups()
        result_data = {
            "total_groups": len(groups),
            "mock": is_mock,
            "groups": {str(gid): [d.to_dict() for d in devs] for gid, devs in groups.items()},
        }
        if args.json:
            sys.stdout.write(json.dumps(result_data, indent=2) + "\n")
        else:
            sys.stdout.write(f"[iommu-parser] Total IOMMU Groups: {len(groups)} (mock={is_mock})\n")
            for gid, devs in groups.items():
                sys.stdout.write(f"  Group {gid}:\n")
                for d in devs:
                    vga_flag = " [boot_vga]" if d.boot_vga else ""
                    sys.stdout.write(f"    - {d.bdf} [{d.vendor_id}:{d.device_id}] {d.class_name} (driver: {d.driver or 'none'}){vga_flag}\n")
        return 0

    if args.audit or args.device:
        target = args.device or "0000:01:00.0"
        report = iommu.audit_isolation(target)
        if args.json:
            sys.stdout.write(json.dumps(report, indent=2) + "\n")
        else:
            sys.stdout.write(f"[iommu-audit] Target: {report.get('target_bdf')} -> Status: {report.get('status', '').upper()}\n")
            sys.stdout.write(f"  - Isolated: {report.get('isolated')}\n")
            sys.stdout.write(f"  - IOMMU Group: {report.get('iommu_group')}\n")
            sys.stdout.write(f"  - Companions ({len(report.get('companions', []))}): {[c['bdf'] for c in report.get('companions', [])]}\n")
            sys.stdout.write(f"  - Conflicts ({len(report.get('conflicts', []))}): {[c['bdf'] for c in report.get('conflicts', [])]}\n")
            sys.stdout.write(f"  - Recommendation: {report.get('recommendation')}\n")
            if report.get("security_warning"):
                sys.stdout.write(f"  - Warning: {report.get('security_warning')}\n")
        return 0 if report.get("isolated") else 1

    parser.print_help()
    return 0

if __name__ == "__main__":
    sys.exit(main())
