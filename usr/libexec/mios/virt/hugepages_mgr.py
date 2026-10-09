#!/usr/bin/env python3
# AI-hint: Hugepages automatic allocation, compaction, and teardown manager for KVM guests (task_362).
# AI-related: tests/test-virt.py, usr/share/doc/mios/manual/ch21-looking-glass-b7-and-kvmfr.md
"""
MiOS Hugepages Automatic Allocation, Memory Compaction, and Teardown Manager.
Dynamically provisions 2MB (2048kB) and 1GB (1048576kB) hugepages for KVM guest memory backing.
Executes proactive memory compaction (/proc/sys/vm/compact_memory) to defragment memory pools
prior to allocation, preventing guest VM startup failures. Generates libvirt <memoryBacking> XML.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, Optional, Tuple

PAGE_SIZES_KB = {
    "2M": 2048,
    "2MB": 2048,
    "1G": 1048576,
    "1GB": 1048576,
}

def normalize_page_size(page_size_str: str) -> str:
    cleaned = page_size_str.strip().upper()
    if cleaned in ("2M", "2MB", "2048", "2048K", "2048KB"):
        return "2M"
    if cleaned in ("1G", "1GB", "1048576", "1024M", "1024MB"):
        return "1G"
    raise ValueError(f"Unsupported hugepage size '{page_size_str}'. Supported sizes: 2M, 1G.")

class HugepagesManager:
    """Manages hugepages allocation, compaction, release, and libvirt XML generation."""

    def __init__(
        self,
        sysfs_root: str = "/sys",
        proc_root: str = "/proc",
        mock: bool = False,
    ) -> None:
        self.sysfs_root = sysfs_root
        self.proc_root = proc_root
        self.mock = mock

    def get_page_size_kb(self, page_size: str = "2M") -> int:
        norm = normalize_page_size(page_size)
        return PAGE_SIZES_KB[norm]

    def calculate_page_count(self, size_mb: int, page_size: str = "2M") -> int:
        """Calculates exact hugepages count needed for target RAM size in MB."""
        if size_mb <= 0:
            raise ValueError(f"Invalid size_mb '{size_mb}': Must be positive integer.")
        norm = normalize_page_size(page_size)
        if norm == "2M":
            if size_mb % 2 != 0:
                raise ValueError(f"Size {size_mb} MB must be an even multiple of 2 MB for 2M hugepages.")
            return size_mb // 2
        elif norm == "1G":
            if size_mb % 1024 != 0:
                raise ValueError(f"Size {size_mb} MB must be an exact multiple of 1024 MB (1GB) for 1G hugepages.")
            return size_mb // 1024
        raise ValueError(f"Unsupported page size {page_size}")

    def _read_file(self, path: str, default: str = "") -> str:
        if not os.path.exists(path):
            return default
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                return f.read().strip()
        except OSError:
            return default

    def _write_file(self, path: str, data: str) -> bool:
        if self.mock:
            return True
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(data)
            return True
        except OSError as e:
            sys.stderr.write(f"[hugepages-mgr] Write error on {path}: {e}\n")
            return False

    def trigger_compaction(self) -> Dict[str, Any]:
        """
        Triggers kernel memory compaction via /proc/sys/vm/compact_memory.
        Defragments contiguous physical pages before attempting hugepage allocation.
        """
        compact_path = os.path.join(self.proc_root, "sys", "vm", "compact_memory")
        success = self._write_file(compact_path, "1\n")
        return {
            "compaction_triggered": success,
            "compact_path": compact_path,
            "mock": self.mock,
        }

    def get_meminfo(self) -> Dict[str, int]:
        """Parses memory information from /proc/meminfo."""
        if self.mock:
            return {
                "MemTotal": 65536000,
                "MemFree": 45000000,
                "MemAvailable": 50000000,
                "HugePages_Total": 0,
                "HugePages_Free": 0,
                "HugePages_Rsvd": 0,
                "HugePages_Surp": 0,
                "Hugepagesize": 2048,
            }

        meminfo_path = os.path.join(self.proc_root, "meminfo")
        res: Dict[str, int] = {}
        if os.path.exists(meminfo_path):
            try:
                with open(meminfo_path, "r", encoding="utf-8") as f:
                    for line in f:
                        if ":" in line:
                            k, v = line.split(":", 1)
                            v_clean = v.strip().split()[0]
                            if v_clean.isdigit():
                                res[k.strip()] = int(v_clean)
            except OSError:
                pass
        return res

    def get_pool_status(self, page_size: str = "2M") -> Dict[str, Any]:
        """Reads current hugepages pool status from sysfs."""
        norm = normalize_page_size(page_size)
        size_kb = self.get_page_size_kb(norm)
        pool_dir = os.path.join(self.sysfs_root, "kernel", "mm", "hugepages", f"hugepages-{size_kb}kB")

        if self.mock:
            return {
                "page_size": norm,
                "page_size_kb": size_kb,
                "pool_dir": pool_dir,
                "nr_hugepages": 0,
                "free_hugepages": 0,
                "allocated_mb": 0,
            }

        nr_raw = self._read_file(os.path.join(pool_dir, "nr_hugepages"), "0")
        free_raw = self._read_file(os.path.join(pool_dir, "free_hugepages"), "0")

        nr_pages = int(nr_raw) if nr_raw.isdigit() else 0
        free_pages = int(free_raw) if free_raw.isdigit() else 0
        allocated_mb = (nr_pages * size_kb) // 1024

        return {
            "page_size": norm,
            "page_size_kb": size_kb,
            "pool_dir": pool_dir,
            "nr_hugepages": nr_pages,
            "free_hugepages": free_pages,
            "allocated_mb": allocated_mb,
        }

    def allocate(
        self,
        size_mb: int,
        page_size: str = "2M",
        compact: bool = True,
    ) -> Dict[str, Any]:
        """
        Dynamically allocates hugepages for requested VM RAM size.
        Optionally compacts memory first to ensure high-order contiguous blocks.
        """
        norm = normalize_page_size(page_size)
        size_kb = self.get_page_size_kb(norm)
        needed_pages = self.calculate_page_count(size_mb, norm)

        compaction_res = None
        if compact:
            compaction_res = self.trigger_compaction()

        pool_dir = os.path.join(self.sysfs_root, "kernel", "mm", "hugepages", f"hugepages-{size_kb}kB")
        nr_path = os.path.join(pool_dir, "nr_hugepages")

        current_pool = self.get_pool_status(norm)
        current_pages = current_pool["nr_hugepages"]
        target_pages = current_pages + needed_pages

        success = self._write_file(nr_path, f"{target_pages}\n")

        # In mock or temp testing, update synthetic file
        if not self.mock and os.path.exists(pool_dir):
            free_path = os.path.join(pool_dir, "free_hugepages")
            self._write_file(free_path, f"{target_pages}\n")

        xml_snippet = self.generate_domain_xml(size_mb, norm)

        return {
            "status": "allocated" if success else "failed",
            "size_mb": size_mb,
            "page_size": norm,
            "page_size_kb": size_kb,
            "requested_pages": needed_pages,
            "previous_pages": current_pages,
            "target_pages": target_pages,
            "compaction": compaction_res,
            "domain_xml": xml_snippet,
        }

    def release(
        self,
        size_mb: int,
        page_size: str = "2M",
    ) -> Dict[str, Any]:
        """
        Frees previously allocated hugepages upon VM shutdown/teardown.
        """
        norm = normalize_page_size(page_size)
        size_kb = self.get_page_size_kb(norm)
        pages_to_free = self.calculate_page_count(size_mb, norm)

        pool_dir = os.path.join(self.sysfs_root, "kernel", "mm", "hugepages", f"hugepages-{size_kb}kB")
        nr_path = os.path.join(pool_dir, "nr_hugepages")

        current_pool = self.get_pool_status(norm)
        current_pages = current_pool["nr_hugepages"]
        new_pages = max(0, current_pages - pages_to_free)

        success = self._write_file(nr_path, f"{new_pages}\n")

        if not self.mock and os.path.exists(pool_dir):
            free_path = os.path.join(pool_dir, "free_hugepages")
            self._write_file(free_path, f"{new_pages}\n")

        return {
            "status": "released" if success else "failed",
            "size_mb": size_mb,
            "page_size": norm,
            "pages_freed": pages_to_free,
            "previous_pages": current_pages,
            "remaining_pages": new_pages,
        }

    def generate_domain_xml(self, size_mb: int, page_size: str = "2M", locked: bool = True) -> str:
        """
        Generates libvirt <memoryBacking> domain XML snippet.
        """
        norm = normalize_page_size(page_size)
        size_kb = self.get_page_size_kb(norm)
        locked_tag = "  <locked/>\n" if locked else ""
        return f"""<memoryBacking>
  <hugepages>
    <page size="{size_kb}" unit="KiB"/>
  </hugepages>
{locked_tag}</memoryBacking>"""

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS Hugepages Automatic Allocation, Memory Compaction, and Teardown Manager."
    )
    parser.add_argument("--allocate", action="store_true", help="Allocate hugepages for VM memory backing.")
    parser.add_argument("--release", action="store_true", help="Release hugepages back to host pool.")
    parser.add_argument("--size-mb", type=int, default=8192, help="VM RAM size in MB (e.g. 8192, 16384).")
    parser.add_argument("--page-size", type=str, default="2M", choices=["2M", "1G"], help="Hugepage size: 2M or 1G (default: 2M).")
    parser.add_argument("--compact", action="store_true", help="Trigger memory compaction before allocation (default: True).")
    parser.add_argument("--no-compact", action="store_true", help="Skip memory compaction.")
    parser.add_argument("--status", action="store_true", help="Display current hugepages pool status.")
    parser.add_argument("--generate-xml", action="store_true", help="Generate libvirt <memoryBacking> XML.")
    parser.add_argument("--sysfs-root", type=str, default="/sys", help="Custom sysfs root path for synthetic testing.")
    parser.add_argument("--proc-root", type=str, default="/proc", help="Custom proc root path for synthetic testing.")
    parser.add_argument("--mock", action="store_true", help="Run in mock mode.")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format.")
    args = parser.parse_args()

    is_mock = args.mock or (os.name == "nt" and not os.path.exists(os.path.join(args.sysfs_root, "kernel", "mm", "hugepages")))
    mgr = HugepagesManager(
        sysfs_root=args.sysfs_root,
        proc_root=args.proc_root,
        mock=is_mock,
    )

    should_compact = not args.no_compact

    if args.generate_xml:
        xml = mgr.generate_domain_xml(args.size_mb, page_size=args.page_size)
        if args.json:
            sys.stdout.write(json.dumps({"size_mb": args.size_mb, "page_size": args.page_size, "xml": xml}, indent=2) + "\n")
        else:
            sys.stdout.write(xml + "\n")
        return 0

    if args.allocate:
        res = mgr.allocate(args.size_mb, page_size=args.page_size, compact=should_compact)
        if args.json:
            sys.stdout.write(json.dumps(res, indent=2) + "\n")
        else:
            sys.stdout.write(f"[hugepages-mgr] Allocated {res['requested_pages']} pages ({args.size_mb} MB @ {res['page_size']}):\n")
            sys.stdout.write(f"  - Target Total Pages: {res['target_pages']}\n")
            sys.stdout.write(f"  - Compaction: {res['compaction']['compaction_triggered'] if res['compaction'] else 'skipped'}\n")
            sys.stdout.write(f"  - Domain XML:\n{res['domain_xml']}\n")
        return 0 if res["status"] == "allocated" else 1

    if args.release:
        res = mgr.release(args.size_mb, page_size=args.page_size)
        if args.json:
            sys.stdout.write(json.dumps(res, indent=2) + "\n")
        else:
            sys.stdout.write(f"[hugepages-mgr] Released {res['pages_freed']} pages ({args.size_mb} MB @ {res['page_size']}):\n")
            sys.stdout.write(f"  - Remaining Pages: {res['remaining_pages']}\n")
        return 0 if res["status"] == "released" else 1

    if args.status or not sys.argv[1:]:
        p2m = mgr.get_pool_status("2M")
        p1g = mgr.get_pool_status("1G")
        mem = mgr.get_meminfo()
        st_data = {
            "2M_pool": p2m,
            "1G_pool": p1g,
            "meminfo": mem,
            "mock": is_mock,
        }
        if args.json:
            sys.stdout.write(json.dumps(st_data, indent=2) + "\n")
        else:
            sys.stdout.write(f"[hugepages-mgr] Hugepages Pool Status (mock={is_mock}):\n")
            sys.stdout.write(f"  - 2M Pool: {p2m['nr_hugepages']} total ({p2m['allocated_mb']} MB), {p2m['free_hugepages']} free\n")
            sys.stdout.write(f"  - 1G Pool: {p1g['nr_hugepages']} total ({p1g['allocated_mb']} MB), {p1g['free_hugepages']} free\n")
        return 0

    parser.print_help()
    return 0

if __name__ == "__main__":
    sys.exit(main())
