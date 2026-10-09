#!/usr/bin/env python3
# AI-hint: Hardware OPAL 2.0 SED / LUKS2 automated disk partitioning and TPM 2.0 enrollment engine.
# AI-related: usr/libexec/mios/storage/opal_luks_partition.py, tests/test-storage.py, /etc/crypttab
"""Hardware OPAL 2.0 SED / LUKS2 Automated Disk Partitioning Engine (task_493).

Discovers NVMe/SATA storage drives, detects TCG OPAL 2.0 Self-Encrypting Drive (SED)
hardware capabilities via sedutil-cli/sysfs, activates hardware Locking Range 0,
falls back to software LUKS2 AES-XTS-512 with TPM 2.0 (PCR 7+11) binding, and applies
MiOS standard GPT partitioning layouts (ESP, Root, Userspace, DB/Ceph).
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass, field
import json
import logging
import os
import re
import subprocess
import sys
from typing import Any, Dict, List, Optional, Tuple

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mios-opal-luks")

@dataclass
class PartitionSpec:
    """Specification for a disk partition."""
    name: str
    size_gb: float  # 0 or negative means remainder of disk
    fs_type: str  # vfat, ext4, xfs, btrfs, raw
    mount_point: str
    encrypted_type: str = "none"  # "opal2", "luks2", "none"
    part_num: int = 1

@dataclass
class DiskDevice:
    """Represents a discovered physical disk drive."""
    path: str
    model: str
    serial: str
    size_bytes: int
    is_sed: bool = False
    is_opal2: bool = False
    is_locked: bool = False
    luks_version: Optional[int] = None
    tpm_bound: bool = False
    partitions: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

DEFAULT_LAYOUT: List[PartitionSpec] = [
    PartitionSpec(name="ESP", size_gb=1.0, fs_type="vfat", mount_point="/boot/efi", encrypted_type="none", part_num=1),
    PartitionSpec(name="MiOS-Root", size_gb=64.0, fs_type="ext4", mount_point="/", encrypted_type="luks2", part_num=2),
    PartitionSpec(name="MiOS-Home", size_gb=200.0, fs_type="xfs", mount_point="/var/home", encrypted_type="luks2", part_num=3),
    PartitionSpec(name="MiOS-Data", size_gb=0.0, fs_type="xfs", mount_point="/var/lib/pgsql", encrypted_type="luks2", part_num=4),
]

class OpalLuksPartitionEngine:
    """Engine managing OPAL 2.0 SED hardware encryption, LUKS2, and GPT partitioning."""

    def __init__(self, mock: bool = False) -> None:
        self.mock = mock
        self._mock_devices: Dict[str, DiskDevice] = {}
        if self.mock:
            self._init_mock_state()

    def _init_mock_state(self) -> None:
        """Populates simulated storage hardware for hermetic tests."""
        self._mock_devices["/dev/nvme0n1"] = DiskDevice(
            path="/dev/nvme0n1",
            model="Samsung SSD 990 PRO 2TB",
            serial="S6B2NJ0W100001",
            size_bytes=2000398934016,
            is_sed=True,
            is_opal2=True,
            is_locked=False,
            luks_version=None,
            tpm_bound=False,
            partitions=[
                {"num": 1, "name": "ESP", "size_gb": 1.0, "fs_type": "vfat"},
                {"num": 2, "name": "MiOS-Root", "size_gb": 64.0, "fs_type": "ext4"},
            ],
        )
        self._mock_devices["/dev/sda"] = DiskDevice(
            path="/dev/sda",
            model="Crucial CT1000MX500SSD1",
            serial="2345E7890123",
            size_bytes=1000204886016,
            is_sed=False,
            is_opal2=False,
            is_locked=False,
            luks_version=2,
            tpm_bound=True,
            partitions=[
                {"num": 1, "name": "MiOS-Data", "size_gb": 931.5, "fs_type": "xfs"},
            ],
        )

    def scan_drives(self) -> List[DiskDevice]:
        """Scans host or mock environment for physical disk drives and evaluates SED/LUKS status."""
        if self.mock:
            return list(self._mock_devices.values())

        devices: List[DiskDevice] = []
        try:
            # 1. Discover block devices via lsblk JSON
            res = subprocess.run(
                ["lsblk", "-J", "-b", "-o", "NAME,PATH,MODEL,SERIAL,SIZE,TYPE,FSTYPE,MOUNTPOINTS"],
                capture_output=True,
                text=True,
                check=True,
            )
            data = json.loads(res.stdout)
            blockdevices = data.get("blockdevices", [])

            for bd in blockdevices:
                if bd.get("type") != "disk":
                    continue
                dpath = bd.get("path") or f"/dev/{bd.get('name')}"
                model = (bd.get("model") or "Generic Disk").strip()
                serial = (bd.get("serial") or "Unknown").strip()
                size_bytes = int(bd.get("size") or 0)

                # Check SED OPAL 2.0 capabilities
                is_sed, is_opal2, is_locked = self._probe_opal_sed(dpath)

                # Check LUKS version
                luks_ver, tpm_bound = self._probe_luks_tpm(dpath, bd.get("children", []))

                parts = []
                for child in bd.get("children", []):
                    parts.append({
                        "name": child.get("name"),
                        "path": child.get("path"),
                        "size_bytes": child.get("size"),
                        "fstype": child.get("fstype"),
                        "mountpoint": child.get("mountpoints"),
                    })

                device = DiskDevice(
                    path=dpath,
                    model=model,
                    serial=serial,
                    size_bytes=size_bytes,
                    is_sed=is_sed,
                    is_opal2=is_opal2,
                    is_locked=is_locked,
                    luks_version=luks_ver,
                    tpm_bound=tpm_bound,
                    partitions=parts,
                )
                devices.append(device)
        except Exception as e:
            logger.warning("Error scanning physical drives via lsblk: %s", e)

        return devices

    def _probe_opal_sed(self, disk_path: str) -> Tuple[bool, bool, bool]:
        """Probes drive for TCG OPAL 2.0 SED capabilities using sedutil-cli."""
        if self.mock:
            dev = self._mock_devices.get(disk_path)
            if dev:
                return dev.is_sed, dev.is_opal2, dev.is_locked
            return False, False, False

        try:
            res = subprocess.run(
                ["sedutil-cli", "--scan"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            # Example output: /dev/nvme0n1  2  OPAL 2.0  Samsung SSD 990 PRO
            for line in res.stdout.splitlines():
                if disk_path in line:
                    is_opal2 = "OPAL 2.0" in line or "OPAL 2" in line or "Opal2" in line
                    is_sed = is_opal2 or "SED" in line or "OPAL" in line
                    is_locked = "Locked" in line or "L" in line.split()
                    return is_sed, is_opal2, is_locked
        except FileNotFoundError:
            logger.debug("sedutil-cli not installed or not in PATH")
        except Exception as e:
            logger.debug("Error probing sedutil-cli for %s: %s", disk_path, e)

        return False, False, False

    def _probe_luks_tpm(self, disk_path: str, children: List[Dict[str, Any]]) -> Tuple[Optional[int], bool]:
        """Probes partitions for LUKS2 headers and systemd-cryptenroll TPM2 bindings."""
        if self.mock:
            dev = self._mock_devices.get(disk_path)
            if dev:
                return dev.luks_version, dev.tpm_bound
            return None, False

        luks_version: Optional[int] = None
        tpm_bound = False

        # Inspect partitions or raw disk
        target_paths = [disk_path] + [c.get("path") for c in children if c.get("path")]
        for p in target_paths:
            try:
                res = subprocess.run(
                    ["cryptsetup", "isLuks", p],
                    capture_output=True,
                    text=True,
                )
                if res.returncode == 0:
                    # Query LUKS header dump
                    dump = subprocess.run(
                        ["cryptsetup", "luksDump", p],
                        capture_output=True,
                        text=True,
                    )
                    if "Version:       2" in dump.stdout or "LUKS2" in dump.stdout:
                        luks_version = 2
                    elif "Version:       1" in dump.stdout:
                        luks_version = 1

                    if "systemd-tpm2" in dump.stdout or "tpm2" in dump.stdout:
                        tpm_bound = True
            except Exception:
                continue

        return luks_version, tpm_bound

    def setup_opal_sed(self, disk_path: str, admin_password: str = "MiOSAdminSecretP@ss1") -> Dict[str, Any]:
        """Initializes OPAL 2.0 SED on disk and configures Locking Range 0."""
        if self.mock:
            if disk_path not in self._mock_devices:
                raise ValueError(f"Device {disk_path} not found in mock device list.")
            dev = self._mock_devices[disk_path]
            if not dev.is_opal2:
                raise RuntimeError(f"Drive {disk_path} does not support TCG OPAL 2.0.")
            dev.is_locked = True
            return {
                "status": "success",
                "device": disk_path,
                "type": "opal2_sed",
                "locking_range": 0,
                "locked": True,
                "message": "OPAL 2.0 initial setup completed and Locking Range 0 enabled.",
            }

        # 1. Verify sedutil-cli presence
        # 2. sedutil-cli --initialsetup <admin_password> <disk_path>
        # 3. sedutil-cli --enableLockingRange 0 <admin_password> <disk_path>
        cmd_init = ["sedutil-cli", "--initialsetup", admin_password, disk_path]
        res_init = subprocess.run(cmd_init, capture_output=True, text=True)
        if res_init.returncode != 0:
            raise RuntimeError(f"sedutil-cli initialsetup failed on {disk_path}: {res_init.stderr.strip() or res_init.stdout.strip()}")

        cmd_range = ["sedutil-cli", "--enableLockingRange", "0", admin_password, disk_path]
        res_range = subprocess.run(cmd_range, capture_output=True, text=True)
        if res_range.returncode != 0:
            raise RuntimeError(f"sedutil-cli enableLockingRange failed on {disk_path}: {res_range.stderr.strip()}")

        return {
            "status": "success",
            "device": disk_path,
            "type": "opal2_sed",
            "locking_range": 0,
            "locked": True,
            "message": "OPAL 2.0 SED successfully initialized and locked.",
        }

    def setup_luks2_tpm(
        self,
        partition_path: str,
        passphrase: str = "MiOSSecureRecoveryKey2026",
        pcr_list: Optional[List[int]] = None,
    ) -> Dict[str, Any]:
        """Formats target partition as LUKS2 AES-XTS-512 and binds keyslot to TPM 2.0."""
        if pcr_list is None:
            pcr_list = [7, 11]
        pcr_str = "+".join(str(p) for p in pcr_list)

        if self.mock:
            # Find device owning this partition or create simulated entry
            found = False
            for dev in self._mock_devices.values():
                if dev.path in partition_path or partition_path == dev.path:
                    dev.luks_version = 2
                    dev.tpm_bound = True
                    found = True
                    break
            if not found:
                self._mock_devices[partition_path] = DiskDevice(
                    path=partition_path,
                    model="Virtual LUKS Device",
                    serial="VIRT-LUKS-001",
                    size_bytes=68719476736,
                    luks_version=2,
                    tpm_bound=True,
                )
            return {
                "status": "success",
                "target": partition_path,
                "luks_version": 2,
                "cipher": "aes-xts-plain64",
                "key_size": 512,
                "tpm_bound": True,
                "pcrs": pcr_list,
                "message": f"LUKS2 formatted with TPM2 bound to PCRs {pcr_str}.",
            }

        # 1. Format LUKS2 container
        cmd_format = [
            "cryptsetup", "luksFormat",
            "--type", "luks2",
            "--cipher", "aes-xts-plain64",
            "--key-size", "512",
            "--hash", "sha512",
            "--pbkdf", "argon2id",
            "--batch-mode",
            partition_path,
        ]
        res_fmt = subprocess.run(cmd_format, input=f"{passphrase}\n", capture_output=True, text=True)
        if res_fmt.returncode != 0:
            raise RuntimeError(f"cryptsetup luksFormat failed on {partition_path}: {res_fmt.stderr.strip()}")

        # 2. Enroll TPM 2.0 with systemd-cryptenroll
        cmd_enroll = [
            "systemd-cryptenroll",
            "--tpm2-device=auto",
            f"--tpm2-pcrs={pcr_str}",
            partition_path,
        ]
        res_enroll = subprocess.run(cmd_enroll, input=f"{passphrase}\n", capture_output=True, text=True)
        if res_enroll.returncode != 0:
            raise RuntimeError(f"systemd-cryptenroll failed on {partition_path}: {res_enroll.stderr.strip()}")

        return {
            "status": "success",
            "target": partition_path,
            "luks_version": 2,
            "cipher": "aes-xts-plain64",
            "key_size": 512,
            "tpm_bound": True,
            "pcrs": pcr_list,
            "message": f"LUKS2 container initialized and bound to TPM 2.0 PCRs {pcr_str}.",
        }

    def apply_partition_layout(
        self,
        disk_path: str,
        layout: Optional[List[PartitionSpec]] = None,
    ) -> Dict[str, Any]:
        """Applies GPT partition table and creates partitions based on specification."""
        if layout is None:
            layout = DEFAULT_LAYOUT

        if self.mock:
            if disk_path not in self._mock_devices:
                self._mock_devices[disk_path] = DiskDevice(
                    path=disk_path,
                    model="Generic Mock Disk",
                    serial="MOCK-001",
                    size_bytes=1000204886016,
                )
            dev = self._mock_devices[disk_path]
            dev.partitions = [
                {"num": spec.part_num, "name": spec.name, "size_gb": spec.size_gb, "fs_type": spec.fs_type, "mount": spec.mount_point}
                for spec in layout
            ]
            return {
                "status": "success",
                "disk": disk_path,
                "table_type": "gpt",
                "partitions_created": len(layout),
                "layout": [asdict(s) for s in layout],
                "message": f"GPT layout with {len(layout)} partitions created.",
            }

        # 1. Execute parted / sgdisk
        # Destroy old table and create GPT
        subprocess.run(["sgdisk", "--zap-all", disk_path], check=True, capture_output=True)

        current_start_mib = 1
        for spec in layout:
            part_num = spec.part_num
            if spec.size_gb > 0:
                size_mib = int(spec.size_gb * 1024)
                end_mib = current_start_mib + size_mib
                cmd = ["sgdisk", f"-n={part_num}:{current_start_mib}M:{end_mib}M", f"-c={part_num}:{spec.name}", disk_path]
                current_start_mib = end_mib
            else:
                # Rest of disk
                cmd = ["sgdisk", f"-n={part_num}:{current_start_mib}M:0", f"-c={part_num}:{spec.name}", disk_path]

            res = subprocess.run(cmd, capture_output=True, text=True)
            if res.returncode != 0:
                raise RuntimeError(f"sgdisk partition creation failed: {res.stderr.strip()}")

        # Reread partition table
        subprocess.run(["partprobe", disk_path], capture_output=True)

        return {
            "status": "success",
            "disk": disk_path,
            "table_type": "gpt",
            "partitions_created": len(layout),
            "layout": [asdict(s) for s in layout],
            "message": f"GPT partition table successfully written to {disk_path}.",
        }

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MiOS Hardware OPAL 2.0 SED & LUKS2 TPM Partitioning Engine (task_493)")
    parser.add_argument("--scan", action="store_true", help="Scan and list storage devices with SED and LUKS status")
    parser.add_argument("--setup-opal", metavar="DEVICE", help="Initialize OPAL 2.0 SED hardware encryption on target disk")
    parser.add_argument("--setup-luks", metavar="PARTITION", help="Format LUKS2 with TPM 2.0 PCR 7+11 binding on target partition")
    parser.add_argument("--partition", metavar="DEVICE", help="Apply standard GPT partition layout on target disk")
    parser.add_argument("--admin-password", default="MiOSAdminSecretP@ss1", help="Admin password for OPAL 2.0 / LUKS2 recovery")
    parser.add_argument("--mock", action="store_true", help="Run with simulated storage devices for safe testing")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")
    return parser.parse_args()

def main() -> int:
    args = parse_args()
    engine = OpalLuksPartitionEngine(mock=args.mock)

    try:
        if args.scan:
            drives = engine.scan_drives()
            data = [d.to_dict() for d in drives]
            if args.json:
                print(json.dumps({"status": "ok", "drives": data}, indent=2))
            else:
                print(f"Discovered {len(drives)} storage drive(s):")
                for d in drives:
                    print(f"  {d.path} ({d.model}, {d.size_bytes // (1024**3)} GB)")
                    print(f"    OPAL 2.0 SED: {d.is_opal2} (Locked: {d.is_locked})")
                    print(f"    LUKS Version: {d.luks_version} (TPM2 Bound: {d.tpm_bound})")
                    print(f"    Partitions: {len(d.partitions)}")
            return 0

        if args.setup_opal:
            res = engine.setup_opal_sed(args.setup_opal, admin_password=args.admin_password)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"OPAL SED Configured: {res['message']}")
            return 0

        if args.setup_luks:
            res = engine.setup_luks2_tpm(args.setup_luks, passphrase=args.admin_password)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"LUKS2 + TPM2 Configured: {res['message']}")
            return 0

        if args.partition:
            res = engine.apply_partition_layout(args.partition)
            if args.json:
                print(json.dumps(res, indent=2))
            else:
                print(f"Partition Layout Applied: {res['message']}")
            return 0

        # If no flags specified, default to scan
        drives = engine.scan_drives()
        print(json.dumps({"status": "ok", "drives": [d.to_dict() for d in drives]}, indent=2))
        return 0

    except Exception as e:
        logger.error("Operation failed: %s", e)
        if args.json:
            print(json.dumps({"status": "error", "error": str(e)}, indent=2))
        return 1

if __name__ == "__main__":
    sys.exit(main())
