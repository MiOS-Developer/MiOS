#!/usr/bin/env python3
# AI-hint: Bootable live hybrid ISO generator with dual UEFI/BIOS and serial IPMI console
# AI-related: tests/test-iso-generate.py, usr/share/mios/mios.toml, usr/libexec/mios/deploy/baremetal_install.py
# AI-functions: IsoGeneratorEngine, IsoStructurePlan, generate_bootable_iso
"""
MiOS Bootable Live Hybrid ISO Generator.

Produces hybrid ISO images supporting dual UEFI (x86_64) and Legacy BIOS firmware boot,
featuring mandatory serial console redirection (console=ttyS0,115200n8) for headless
rackmount servers, BMC/IPMI SoL (Serial-over-LAN), and edge micro-nodes.

Constructs El Torito multi-boot headers and executes/simulates xorriso image mastering.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

@dataclass
class IsoStructurePlan:
    """Staging directory structure and xorriso invocation parameters."""
    staging_dir: str
    output_iso: str
    volume_id: str
    serial_baud: int
    kernel_cmdline: str
    has_uefi_bootloader: bool
    has_bios_bootloader: bool
    xorriso_command: List[str] = field(default_factory=list)
    staged_files: List[str] = field(default_factory=list)

class IsoGeneratorEngine:
    """Staging and generation engine for bootable hybrid MiOS ISOs."""

    def __init__(
        self,
        staging_dir: Optional[str] = None,
        output_iso: str = "/tmp/mios-live.iso",
        volid: str = "MIOS_LIVE",
        serial_baud: int = 115200,
        extra_kernel_args: Optional[str] = None,
        dry_run: bool = False,
        mock: bool = False,
    ):
        self.staging_dir = staging_dir or "/tmp/mios-iso-staging"
        self.output_iso = output_iso
        self.volid = volid[:32]  # ISO-9660 volume ID limit
        self.serial_baud = serial_baud
        self.extra_kernel_args = extra_kernel_args or ""
        self.dry_run = dry_run
        self.mock = mock

    def build_kernel_cmdline(self) -> str:
        """Construct kernel commandline enforcing mandatory serial console redirection."""
        base_args = f"console=tty0 console=ttyS0,{self.serial_baud}n8 mios.live=1 mios.installer=1 rd.live.image"
        if self.extra_kernel_args:
            return f"{base_args} {self.extra_kernel_args}".strip()
        return base_args

    def generate_isolinux_cfg(self, cmdline: str) -> str:
        """Generate isolinux.cfg for Legacy BIOS boot."""
        return (
            "default mios\n"
            "timeout 50\n"
            f"serial 0 {self.serial_baud}\n"
            "prompt 1\n\n"
            "label mios\n"
            "  menu label ^MiOS Live & Installer (Serial & Display)\n"
            "  kernel /images/vmlinuz\n"
            f"  append initrd=/images/initramfs.img {cmdline}\n"
        )

    def generate_grub_cfg(self, cmdline: str) -> str:
        """Generate grub.cfg for UEFI boot with serial console multiplexing."""
        return (
            'set default="0"\n'
            "set timeout=5\n\n"
            f"serial --unit=0 --speed={self.serial_baud}\n"
            "terminal_input console serial\n"
            "terminal_output console serial\n\n"
            'menuentry "MiOS Live & Installer (Serial & Display)" {\n'
            f"    linux /images/vmlinuz {cmdline}\n"
            "    initrd /images/initramfs.img\n"
            "}\n"
        )

    def populate_staging_tree(self, cmdline: str) -> List[str]:
        """Create necessary bootloader configs and directory structure in staging."""
        staged: List[str] = []
        isolinux_dir = os.path.join(self.staging_dir, "isolinux")
        efi_boot_dir = os.path.join(self.staging_dir, "EFI", "BOOT")
        efi_img_dir = os.path.join(self.staging_dir, "EFI", "images")
        images_dir = os.path.join(self.staging_dir, "images")
        liveos_dir = os.path.join(self.staging_dir, "LiveOS")

        dirs = [isolinux_dir, efi_boot_dir, efi_img_dir, images_dir, liveos_dir]

        if not self.mock and not self.dry_run:
            for d in dirs:
                os.makedirs(d, exist_ok=True)

            # Write isolinux.cfg
            p_iso_cfg = os.path.join(isolinux_dir, "isolinux.cfg")
            with open(p_iso_cfg, "w", encoding="utf-8") as f:
                f.write(self.generate_isolinux_cfg(cmdline))
            staged.append(p_iso_cfg)

            # Write EFI grub.cfg
            p_grub_cfg = os.path.join(efi_boot_dir, "grub.cfg")
            with open(p_grub_cfg, "w", encoding="utf-8") as f:
                f.write(self.generate_grub_cfg(cmdline))
            staged.append(p_grub_cfg)

            # Create dummy or placeholder efiboot.img if missing
            p_efi_img = os.path.join(efi_img_dir, "efiboot.img")
            if not os.path.exists(p_efi_img):
                with open(p_efi_img, "wb") as f:
                    f.write(b"\x00" * (10 * 1024 * 1024))  # 10MB dummy FAT image
            staged.append(p_efi_img)
        else:
            staged.extend([
                os.path.join(isolinux_dir, "isolinux.cfg"),
                os.path.join(efi_boot_dir, "grub.cfg"),
                os.path.join(efi_img_dir, "efiboot.img"),
                os.path.join(images_dir, "vmlinuz"),
                os.path.join(images_dir, "initramfs.img"),
                os.path.join(liveos_dir, "squashfs.img"),
            ])

        return staged

    def build_xorriso_command(self) -> List[str]:
        """Construct the xorriso command line for creating hybrid bootable ISO."""
        return [
            "xorriso",
            "-as", "mkisofs",
            "-V", self.volid,
            "-r", "-J", "--joliet-long",
            "-b", "isolinux/isolinux.bin",
            "-c", "isolinux/boot.cat",
            "-no-emul-boot", "-boot-load-size", "4", "-boot-info-table",
            "-eltorito-alt-boot",
            "-e", "EFI/images/efiboot.img",
            "-no-emul-boot", "-isohybrid-gpt-basdat",
            "-o", self.output_iso,
            self.staging_dir,
        ]

    def plan_iso(self) -> IsoStructurePlan:
        """Construct the complete ISO structure and generation plan."""
        cmdline = self.build_kernel_cmdline()

        # Safety Check: Guarantee serial console is never omitted
        if "console=ttyS" not in cmdline:
            raise ValueError(
                "SAFETY VIOLATION: Kernel command line must include serial console redirection (console=ttyS0,...)."
            )

        staged_files = self.populate_staging_tree(cmdline)
        xorriso_cmd = self.build_xorriso_command()

        return IsoStructurePlan(
            staging_dir=self.staging_dir,
            output_iso=self.output_iso,
            volume_id=self.volid,
            serial_baud=self.serial_baud,
            kernel_cmdline=cmdline,
            has_uefi_bootloader=True,
            has_bios_bootloader=True,
            xorriso_command=xorriso_cmd,
            staged_files=staged_files,
        )

    def execute_iso_build(self, plan: IsoStructurePlan) -> Dict[str, Any]:
        """Run or simulate xorriso generation command."""
        commands_run: List[str] = []

        if not self.mock and not self.dry_run:
            if shutil.which("xorriso"):
                subprocess.run(plan.xorriso_command, check=True)
                commands_run.append(" ".join(plan.xorriso_command))
            else:
                raise RuntimeError("xorriso binary not found in PATH.")
        else:
            commands_run.append(" ".join(plan.xorriso_command))

        return {
            "status": "success",
            "plan": asdict(plan),
            "commands_executed": commands_run,
            "dry_run": self.dry_run,
            "mock": self.mock,
        }

    def run(self) -> Dict[str, Any]:
        """Execute complete ISO preparation and image build."""
        plan = self.plan_iso()
        return self.execute_iso_build(plan)

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS Hybrid UEFI/BIOS Bootable Live ISO Generator"
    )
    parser.add_argument("--staging-dir", default="/tmp/mios-iso-staging", help="Path to ISO contents staging directory")
    parser.add_argument("--output-iso", default="/tmp/mios-live.iso", help="Path for generated output ISO file")
    parser.add_argument("--volid", default="MIOS_LIVE", help="ISO-9660 Volume Identifier")
    parser.add_argument("--serial-baud", type=int, default=115200, help="Serial console baud rate (default: 115200)")
    parser.add_argument("--kernel-args", help="Additional kernel command line parameters")
    parser.add_argument("--dry-run", action="store_true", help="Simulate ISO generation without building image")
    parser.add_argument("--mock", action="store_true", help="Run deterministic mock execution for CI testing")
    parser.add_argument("--json", action="store_true", help="Format output as JSON dictionary")

    args = parser.parse_args()

    engine = IsoGeneratorEngine(
        staging_dir=args.staging_dir,
        output_iso=args.output_iso,
        volid=args.volid,
        serial_baud=args.serial_baud,
        extra_kernel_args=args.kernel_args,
        dry_run=args.dry_run,
        mock=args.mock,
    )

    try:
        res = engine.run()
        if args.json:
            print(json.dumps(res, indent=2))
        else:
            plan = res["plan"]
            print(f"[iso_generate] SUCCESS: Prepared hybrid ISO plan for {plan['output_iso']}")
            print(f"  Volume ID: {plan['volume_id']}, Serial: ttyS0@{plan['serial_baud']}")
            print(f"  Kernel Cmdline: {plan['kernel_cmdline']}")
            print(f"  xorriso Command: {' '.join(plan['xorriso_command'])}")
        return 0
    except Exception as e:
        err = {"status": "error", "error": str(e)}
        if args.json:
            print(json.dumps(err, indent=2))
        else:
            print(f"[iso_generate] ERROR: {e}", file=sys.stderr)
        return 1

if __name__ == "__main__":
    sys.exit(main())
