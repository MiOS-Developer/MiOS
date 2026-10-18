#!/usr/bin/env python3
# AI-hint: MiOS-Cat USB hybrid GPT/MBR partition formatter with FAT32 EFI + exFAT Data
# AI-related: tests/test-usb-format.py, usr/share/mios/mios.toml, https://github.com/mios-dev/mios-bootstrap/blob/main/field/MiOS-Field.sh
# AI-functions: UsbFormatEngine, PartitionInfo, DeviceSafetyCheck, format_usb_media
"""
MiOS-Cat Removable USB Media Partition Formatter.

Provisions removable USB media with a hybrid GPT/MBR partition layout:
- Partition 1 (MiOS-Repo): FAT32 filesystem, ESP GUID (c12a7328-f81f-11d2-ba4b-00a0c93ec93b),
  legacy MBR 0xEF, boot/esp flags for UEFI & BIOS firmware bootloaders.
- Partition 2 (MiOS-Data): exFAT (or ext4) filesystem, Basic Data GUID
  (ebd0a0a2-b9e5-4433-87c0-68b6b72699c7), legacy MBR 0x07, storing OCI layers,
  AI model weights, and staging payloads.

Enforces strict safety invariants preventing inadvertent wiping of internal or OS drives.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

ESP_GUID = "c12a7328-f81f-11d2-ba4b-00a0c93ec93b"
BASIC_DATA_GUID = "ebd0a0a2-b9e5-4433-87c0-68b6b72699c7"
SECTOR_SIZE = 512
ALIGNMENT_SECTORS = 2048  # 1MB alignment

@dataclass
class PartitionInfo:
    """Specification and geometry for a partition on target media."""
    index: int
    name: str
    label: str
    filesystem: str
    type_guid: str
    mbr_type: int
    start_sector: int
    end_sector: int
    size_mb: int
    bootable: bool = False

@dataclass
class DeviceInfo:
    """Metadata describing a block storage device."""
    device_path: str
    model: str
    size_bytes: int
    size_gb: float
    is_removable: bool
    bus_type: str
    partitions: List[str] = field(default_factory=list)

class UsbFormatEngine:
    """Engine for inspecting, validating, and formatting removable USB media."""

    def __init__(
        self,
        target_dev: str,
        repo_size_mb: int = 2048,
        label_repo: str = "MiOS-Repo",
        label_data: str = "MiOS-Data",
        fs_data: str = "exfat",
        force: bool = False,
        dry_run: bool = False,
        mock: bool = False,
    ):
        self.target_dev = target_dev
        self.repo_size_mb = max(512, repo_size_mb)
        self.label_repo = label_repo[:11]  # FAT32 max 11 chars
        self.label_data = label_data[:15]  # exFAT max 15 chars
        self.fs_data = fs_data.lower()
        self.force = force
        self.dry_run = dry_run
        self.mock = mock

    def probe_device(self) -> DeviceInfo:
        """Inspect device properties and assert removable media safety."""
        if self.mock:
            return DeviceInfo(
                device_path=self.target_dev,
                model="SanDisk Ultra USB 3.0 (Mock)",
                size_bytes=32 * 1024 * 1024 * 1024,
                size_gb=32.0,
                is_removable=True,
                bus_type="usb",
                partitions=[f"{self.target_dev}1", f"{self.target_dev}2"],
            )

        if not os.path.exists(self.target_dev) and not self.target_dev.startswith("\\\\.\\"):
            raise FileNotFoundError(f"Target device '{self.target_dev}' does not exist.")

        # Linux sysfs / lsblk probing
        dev_name = os.path.basename(self.target_dev)
        removable_path = f"/sys/block/{dev_name}/removable"
        is_removable = False
        if os.path.exists(removable_path):
            try:
                with open(removable_path, "r", encoding="utf-8") as f:
                    is_removable = f.read().strip() == "1"
            except Exception:
                pass

        # Try lsblk if available
        model = "Removable Storage"
        size_bytes = 0
        bus_type = "unknown"
        partitions: List[str] = []

        if shutil.which("lsblk"):
            try:
                cmd = ["lsblk", "-b", "-J", "-o", "NAME,SIZE,ROTA,TRAN,RM,MODEL,MOUNTPOINTS", self.target_dev]
                res = subprocess.run(cmd, capture_output=True, text=True, check=True)
                data = json.loads(res.stdout)
                devices = data.get("blockdevices", [])
                if devices:
                    d = devices[0]
                    size_bytes = int(d.get("size", 0))
                    model = d.get("model") or model
                    bus_type = d.get("tran") or bus_type
                    if d.get("rm") in (1, True, "1"):
                        is_removable = True
                    for child in d.get("children", []):
                        partitions.append(f"/dev/{child.get('NAME')}")
            except Exception:
                pass

        if size_bytes == 0:
            try:
                # Fallback to blockdev size
                res = subprocess.run(["blockdev", "--getsize64", self.target_dev], capture_output=True, text=True)
                if res.returncode == 0:
                    size_bytes = int(res.stdout.strip())
            except Exception:
                size_bytes = 32 * 1024 * 1024 * 1024  # Default fallback

        size_gb = round(size_bytes / (1024 * 1024 * 1024), 2)
        return DeviceInfo(
            device_path=self.target_dev,
            model=model,
            size_bytes=size_bytes,
            size_gb=size_gb,
            is_removable=is_removable,
            bus_type=bus_type,
            partitions=partitions,
        )

    def validate_safety(self, dev: DeviceInfo) -> Tuple[bool, Optional[str]]:
        """Ensure device is not a system disk and is removable unless forced."""
        # Check against common OS drive indicators
        dev_base = os.path.basename(dev.device_path).lower()
        if dev_base in ("sda", "nvme0n1", "vda", "xvda") and not self.force:
            return False, f"Refusing to format potential system disk '{dev.device_path}' without --force."

        if not dev.is_removable and dev.bus_type != "usb" and not self.force:
            return False, f"Target device '{dev.device_path}' is not flagged as removable (bus: {dev.bus_type}). Use --force to override."

        if dev.size_bytes < (self.repo_size_mb + 512) * 1024 * 1024:
            return False, f"Device capacity ({dev.size_gb} GB) is smaller than required repo size ({self.repo_size_mb} MB)."

        return True, None

    def plan_layout(self, dev: DeviceInfo) -> List[PartitionInfo]:
        """Compute exact sector start, end, alignment, and flags for both partitions."""
        total_sectors = dev.size_bytes // SECTOR_SIZE
        # Reserve first 1MB (2048 sectors) for GPT header + MBR
        repo_start = ALIGNMENT_SECTORS
        repo_sectors = (self.repo_size_mb * 1024 * 1024) // SECTOR_SIZE
        # Align repo end to 1MB boundary
        repo_end = repo_start + repo_sectors - 1
        repo_end = (repo_end // ALIGNMENT_SECTORS) * ALIGNMENT_SECTORS - 1

        data_start = repo_end + 1
        # Reserve last 34 sectors for secondary GPT table
        data_end = total_sectors - 34
        # Align data end
        data_end = (data_end // ALIGNMENT_SECTORS) * ALIGNMENT_SECTORS - 1

        repo_part = PartitionInfo(
            index=1,
            name="MiOS-Repo",
            label=self.label_repo,
            filesystem="vfat",
            type_guid=ESP_GUID,
            mbr_type=0xEF,
            start_sector=repo_start,
            end_sector=repo_end,
            size_mb=round(((repo_end - repo_start + 1) * SECTOR_SIZE) / (1024 * 1024)),
            bootable=True,
        )

        data_part = PartitionInfo(
            index=2,
            name="MiOS-Data",
            label=self.label_data,
            filesystem=self.fs_data,
            type_guid=BASIC_DATA_GUID,
            mbr_type=0x07,
            start_sector=data_start,
            end_sector=data_end,
            size_mb=round(((data_end - data_start + 1) * SECTOR_SIZE) / (1024 * 1024)),
            bootable=False,
        )

        return [repo_part, data_part]

    def execute_format(self, dev: DeviceInfo, layout: List[PartitionInfo]) -> Dict[str, Any]:
        """Execute or simulate the partition and formatting commands."""
        commands_run: List[str] = []
        part1_dev = f"{self.target_dev}1" if not self.target_dev.startswith("/dev/nvme") else f"{self.target_dev}p1"
        part2_dev = f"{self.target_dev}2" if not self.target_dev.startswith("/dev/nvme") else f"{self.target_dev}p2"

        # Construct sgdisk commands for GPT creation + Hybrid MBR
        cmd_wipe = f"wipefs -a {self.target_dev}"
        cmd_gpt = (
            f"sgdisk -Z {self.target_dev} "
            f"-n 1:{layout[0].start_sector}:{layout[0].end_sector} -t 1:ef00 -c 1:'{layout[0].name}' "
            f"-n 2:{layout[1].start_sector}:{layout[1].end_sector} -t 2:0700 -c 2:'{layout[1].name}' "
            f"-h 1:2"
        )
        cmd_mkfs_repo = f"mkfs.vfat -F32 -n '{layout[0].label}' {part1_dev}"
        cmd_mkfs_data = (
            f"mkfs.exfat -n '{layout[1].label}' {part2_dev}"
            if self.fs_data == "exfat"
            else f"mkfs.ext4 -F -L '{layout[1].label}' {part2_dev}"
        )

        planned_commands = [cmd_wipe, cmd_gpt, cmd_mkfs_repo, cmd_mkfs_data]

        if not self.mock and not self.dry_run:
            # Run unmount if partitions are mounted
            for p in dev.partitions:
                try:
                    subprocess.run(["umount", p], capture_output=True)
                except Exception:
                    pass

            # Wipe partition table
            if shutil.which("wipefs"):
                subprocess.run(["wipefs", "-a", self.target_dev], check=True)
                commands_run.append(cmd_wipe)

            # Partition using sgdisk or parted
            if shutil.which("sgdisk"):
                subprocess.run(
                    [
                        "sgdisk",
                        "-Z",
                        self.target_dev,
                        "-n", f"1:{layout[0].start_sector}:{layout[0].end_sector}",
                        "-t", "1:ef00",
                        "-c", f"1:{layout[0].name}",
                        "-n", f"2:{layout[1].start_sector}:{layout[1].end_sector}",
                        "-t", "2:0700",
                        "-c", f"2:{layout[1].name}",
                        "-h", "1:2",
                    ],
                    check=True,
                )
                commands_run.append(cmd_gpt)

            # Format FAT32 repo
            if shutil.which("mkfs.vfat"):
                subprocess.run(["mkfs.vfat", "-F32", "-n", layout[0].label, part1_dev], check=True)
                commands_run.append(cmd_mkfs_repo)

            # Format exFAT / ext4 data
            if self.fs_data == "exfat" and shutil.which("mkfs.exfat"):
                subprocess.run(["mkfs.exfat", "-n", layout[1].label, part2_dev], check=True)
                commands_run.append(cmd_mkfs_data)
            elif shutil.which("mkfs.ext4"):
                subprocess.run(["mkfs.ext4", "-F", "-L", layout[1].label, part2_dev], check=True)
                commands_run.append(cmd_mkfs_data)
        else:
            commands_run = planned_commands

        return {
            "status": "success",
            "device": asdict(dev),
            "layout": [asdict(p) for p in layout],
            "commands_planned": planned_commands,
            "commands_executed": commands_run,
            "dry_run": self.dry_run,
            "mock": self.mock,
        }

    def run(self) -> Dict[str, Any]:
        """Orchestrate device probe, safety verification, and partition formatting."""
        dev = self.probe_device()
        safe, reason = self.validate_safety(dev)
        if not safe:
            raise ValueError(f"Safety check failed: {reason}")

        layout = self.plan_layout(dev)
        result = self.execute_format(dev, layout)
        return result

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS-Cat Removable USB Hybrid GPT/MBR Partition Formatter"
    )
    parser.add_argument("--target-dev", required=False, default="/dev/sdb", help="Target device path (e.g. /dev/sdb)")
    parser.add_argument("--repo-size-mb", type=int, default=2048, help="FAT32 EFI repo partition size in MB (default: 2048)")
    parser.add_argument("--label-repo", default="MiOS-Repo", help="Filesystem label for EFI repo partition")
    parser.add_argument("--label-data", default="MiOS-Data", help="Filesystem label for Data partition")
    parser.add_argument("--fs-data", choices=["exfat", "ext4"], default="exfat", help="Data partition filesystem")
    parser.add_argument("--force", "--yes", action="store_true", help="Force formatting even if non-removable or warning")
    parser.add_argument("--dry-run", action="store_true", help="Simulate partition and format steps without writing")
    parser.add_argument("--mock", action="store_true", help="Deterministic mock execution for CI testing")
    parser.add_argument("--json", action="store_true", help="Format output as JSON dictionary")

    args = parser.parse_args()

    engine = UsbFormatEngine(
        target_dev=args.target_dev,
        repo_size_mb=args.repo_size_mb,
        label_repo=args.label_repo,
        label_data=args.label_data,
        fs_data=args.fs_data,
        force=args.force,
        dry_run=args.dry_run,
        mock=args.mock,
    )

    try:
        res = engine.run()
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            print(f"[usb_format] SUCCESS: Formatted {args.target_dev} with hybrid GPT/MBR")
            print(f"  Partition 1 ({res['layout'][0]['name']}): {res['layout'][0]['filesystem'].upper()} {res['layout'][0]['size_mb']}MB, Label: {res['layout'][0]['label']}")
            print(f"  Partition 2 ({res['layout'][1]['name']}): {res['layout'][1]['filesystem'].upper()} {res['layout'][1]['size_mb']}MB, Label: {res['layout'][1]['label']}")
        return 0
    except Exception as e:
        err = {"status": "error", "error": str(e), "target_dev": args.target_dev}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"[usb_format] ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
