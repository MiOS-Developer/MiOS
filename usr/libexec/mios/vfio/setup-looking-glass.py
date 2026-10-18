#!/usr/bin/env python3
# AI-hint: Looking Glass B6 IVSHMEM shared memory setup and VFIO passthrough validation.
# AI-related: tests/test-looking-glass.py, usr/share/doc/mios/manual/ch21-looking-glass-b7-and-kvmfr.md
"""
MiOS Looking Glass B6 Shared Memory & VFIO Configuration Utility.
Manages IVSHMEM device node permissions, shm allocation (64MB/128MB), and domain XML generation.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional

class LookingGlassManager:
    """Manages Looking Glass shared memory framebuffer allocation and XML generation."""

    def __init__(self, shm_path: str = "/dev/shm/looking-glass", size_mb: int = 64, device_node: str = "/dev/kvmfr0") -> None:
        self.shm_path = shm_path
        self.size_mb = size_mb
        self.device_node = device_node

    def generate_ivshmem_xml(self) -> str:
        """Generates libvirt IVSHMEM domain snippet for Looking Glass B6."""
        return f"""<shmem name="looking-glass">
  <model type="ivshmem-plain"/>
  <size unit="M">{self.size_mb}</size>
</shmem>"""

    def generate_qemu_cmdline(self) -> str:
        """Generates raw QEMU command-line argument snippet for Looking Glass IVSHMEM."""
        size_bytes = self.size_mb * 1024 * 1024
        target_path = self.device_node if os.path.exists(self.device_node) else self.shm_path
        return f"-object memory-backend-file,id=shmmem-looking-glass,mem-path={target_path},size={size_bytes},share=yes -device ivshmem-plain,id=shmem0,memdev=shmmem-looking-glass"

    def validate_shm_allocation(self, mock: bool = False) -> bool:
        if mock:
            return True
        if not os.path.exists(self.shm_path):
            return False
        st = os.stat(self.shm_path)
        return (st.st_mode & 0o777) == 0o660

    def validate_kvmfr_device(self, mock: bool = False) -> bool:
        if mock:
            return True
        if not os.path.exists(self.device_node):
            return False
        st = os.stat(self.device_node)
        return (st.st_mode & 0o777) == 0o660

    def verify_all(self, mock: bool = False) -> Dict[str, Any]:
        shm_ok = self.validate_shm_allocation(mock=mock)
        kvmfr_ok = self.validate_kvmfr_device(mock=mock)
        status = "pass" if (shm_ok or kvmfr_ok) else "fail"
        return {
            "status": status,
            "mock": mock,
            "size_mb": self.size_mb,
            "shm_path": self.shm_path,
            "device_node": self.device_node,
            "checks": {
                "shm_allocation": "pass" if shm_ok else "fail",
                "kvmfr_device": "pass" if kvmfr_ok else "fail",
            },
        }

def main() -> int:
    parser = argparse.ArgumentParser(description="MiOS Looking Glass B6 Shared Memory & VFIO Configuration Utility.")
    parser.add_argument("--verify", action="store_true", help="Verify IVSHMEM device node and permissions.")
    parser.add_argument("--generate", action="store_true", help="Generate libvirt domain IVSHMEM XML snippet.")
    parser.add_argument("--generate-qemu", action="store_true", help="Generate raw QEMU command-line argument snippet.")
    parser.add_argument("--mock", action="store_true", help="Run verification in mock/synthetic mode.")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format.")
    parser.add_argument("--size-mb", type=int, default=64, help="Looking Glass framebuffer size in MB (e.g. 64, 128).")
    parser.add_argument("--shm-path", type=str, default="/dev/shm/looking-glass", help="Path to IVSHMEM memory file.")
    parser.add_argument("--device-node", type=str, default="/dev/kvmfr0", help="Path to kvmfr character device.")
    args = parser.parse_args()

    lg = LookingGlassManager(shm_path=args.shm_path, size_mb=args.size_mb, device_node=args.device_node)

    if args.generate:
        xml = lg.generate_ivshmem_xml()
        if args.json:
            sys.stdout.write(json.dumps({"xml": xml, "size_mb": args.size_mb}, indent=2) + "\n")
        else:
            sys.stdout.write(xml + "\n")
        return 0

    if args.generate_qemu:
        cmdline = lg.generate_qemu_cmdline()
        if args.json:
            sys.stdout.write(json.dumps({"cmdline": cmdline, "size_mb": args.size_mb}, indent=2) + "\n")
        else:
            sys.stdout.write(cmdline + "\n")
        return 0

    if args.verify or not sys.argv[1:]:
        results = lg.verify_all(mock=args.mock or os.name == "nt")
        if args.json:
            sys.stdout.write(json.dumps(results, indent=2) + "\n")
        else:
            sys.stdout.write(f"[looking-glass-setup] Status: {results['status'].upper()} (mock={results['mock']})\n")
            sys.stdout.write(f"  - Size: {results['size_mb']} MB\n")
            sys.stdout.write(f"  - SHM ({results['shm_path']}): {results['checks']['shm_allocation']}\n")
            sys.stdout.write(f"  - KVMFR ({results['device_node']}): {results['checks']['kvmfr_device']}\n")
        return 0 if results["status"] == "pass" else 1

    parser.print_help()
    return 0

if __name__ == "__main__":
    sys.exit(main())
