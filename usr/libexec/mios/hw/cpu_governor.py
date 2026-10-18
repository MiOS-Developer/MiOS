#!/usr/bin/env python3
# AI-hint: Automated CPU governor switching and frequency scaling manager for MiOS.
# AI-related: usr/libexec/mios/hw/cpu_governor.py, /etc/libvirt/hooks/qemu, tests/test-hw.py
"""Automated CPU governor switcher and frequency scaling manager for MiOS.

Manages CPU frequency governors via /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor.
Integrates with libvirt qemu hook to dynamically set performance mode when VMs run,
and restore prior power-saving governors upon VM termination.

Architectural Invariant:
Do NOT keep CPUs in fixed performance governor indefinitely when the machine is idle.
"""

from __future__ import annotations

import argparse
import glob
import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("mios-cpu-governor")

DEFAULT_STATE_FILE = "/run/mios/cpu_governor_state.json"
DEFAULT_PERFORMANCE_GOVERNOR = "performance"
DEFAULT_IDLE_GOVERNOR = "powersave"

class CPUGovernorManager:
    """Manages CPU governors and EPP settings across online system CPUs."""

    def __init__(
        self,
        sysfs_root: str = "/",
        state_file: str = DEFAULT_STATE_FILE,
        dry_run: bool = False,
    ) -> None:
        self.sysfs_root = os.path.abspath(sysfs_root)
        self.state_file = state_file
        self.dry_run = dry_run

    @property
    def cpu_base_dir(self) -> str:
        """Return base directory for CPU devices in sysfs."""
        return os.path.join(self.sysfs_root, "sys", "devices", "system", "cpu")

    def get_online_cpu_ids(self) -> List[int]:
        """Discover all online CPU IDs from sysfs."""
        cpu_pattern = os.path.join(self.cpu_base_dir, "cpu[0-9]*")
        cpu_dirs = glob.glob(cpu_pattern)
        cpu_ids = []
        for path in cpu_dirs:
            basename = os.path.basename(path)
            try:
                cid = int(basename[3:])
                # Check online status if online file exists (cpu0 might not have online file)
                online_file = os.path.join(path, "online")
                if os.path.isfile(online_file):
                    with open(online_file, "r", encoding="utf-8") as f:
                        if f.read().strip() == "0":
                            continue
                cpu_ids.append(cid)
            except (ValueError, OSError):
                continue
        cpu_ids.sort()
        return cpu_ids

    def _read_file_safe(self, path: str) -> Optional[str]:
        """Safely read string from a sysfs path."""
        if not os.path.isfile(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return f.read().strip()
        except OSError as e:
            logger.debug("Failed reading %s: %s", path, e)
            return None

    def _write_file_safe(self, path: str, content: str) -> bool:
        """Safely write string to a sysfs path."""
        if self.dry_run:
            logger.info("[DRY-RUN] Write '%s' -> %s", content, path)
            return True
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return True
        except OSError as e:
            logger.warning("Failed writing '%s' to %s: %s", content, path, e)
            return False

    def get_cpu_info(self, cpu_id: int) -> Dict[str, Any]:
        """Get comprehensive frequency scaling information for a given CPU."""
        cpufreq_dir = os.path.join(self.cpu_base_dir, f"cpu{cpu_id}", "cpufreq")
        governor = self._read_file_safe(os.path.join(cpufreq_dir, "scaling_governor"))
        avail_govs_raw = self._read_file_safe(os.path.join(cpufreq_dir, "scaling_available_governors"))
        avail_govs = avail_govs_raw.split() if avail_govs_raw else []
        cur_freq = self._read_file_safe(os.path.join(cpufreq_dir, "scaling_cur_freq"))
        min_freq = self._read_file_safe(os.path.join(cpufreq_dir, "scaling_min_freq"))
        max_freq = self._read_file_safe(os.path.join(cpufreq_dir, "scaling_max_freq"))
        driver = self._read_file_safe(os.path.join(cpufreq_dir, "scaling_driver"))
        epp = self._read_file_safe(os.path.join(cpufreq_dir, "energy_performance_preference"))
        avail_epp_raw = self._read_file_safe(os.path.join(cpufreq_dir, "energy_performance_available_preferences"))
        avail_epp = avail_epp_raw.split() if avail_epp_raw else []

        return {
            "cpu_id": cpu_id,
            "governor": governor or "unknown",
            "available_governors": avail_govs,
            "cur_freq_khz": int(cur_freq) if cur_freq and cur_freq.isdigit() else None,
            "min_freq_khz": int(min_freq) if min_freq and min_freq.isdigit() else None,
            "max_freq_khz": int(max_freq) if max_freq and max_freq.isdigit() else None,
            "driver": driver,
            "epp": epp,
            "available_epp": avail_epp,
            "has_cpufreq": os.path.isdir(cpufreq_dir),
        }

    def get_all_cpu_states(self) -> List[Dict[str, Any]]:
        """Collect states for all online CPUs."""
        return [self.get_cpu_info(cid) for cid in self.get_online_cpu_ids()]

    def set_governor(
        self,
        governor: str,
        cpu_ids: Optional[List[int]] = None,
        epp: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Apply governor (and optional energy performance preference) to CPUs."""
        target_cpus = cpu_ids if cpu_ids is not None else self.get_online_cpu_ids()
        success_cpus: List[int] = []
        failed_cpus: List[int] = []

        for cid in target_cpus:
            cpufreq_dir = os.path.join(self.cpu_base_dir, f"cpu{cid}", "cpufreq")
            gov_file = os.path.join(cpufreq_dir, "scaling_governor")

            # Check availability if available_governors file exists
            avail_govs_raw = self._read_file_safe(os.path.join(cpufreq_dir, "scaling_available_governors"))
            if avail_govs_raw:
                avail_govs = avail_govs_raw.split()
                if governor not in avail_govs:
                    logger.warning("Governor '%s' not in available list %s for cpu%d", governor, avail_govs, cid)

            ok = self._write_file_safe(gov_file, governor)
            if ok:
                success_cpus.append(cid)
            else:
                failed_cpus.append(cid)

            if epp:
                epp_file = os.path.join(cpufreq_dir, "energy_performance_preference")
                if os.path.isfile(epp_file):
                    self._write_file_safe(epp_file, epp)

        return {
            "target_governor": governor,
            "target_epp": epp,
            "success_cpus": success_cpus,
            "failed_cpus": failed_cpus,
            "total_requested": len(target_cpus),
            "status": "ok" if not failed_cpus else "partial_or_failed",
        }

    def load_persisted_state(self) -> Dict[str, Any]:
        """Read saved state registry."""
        default_state = {"active_domains": {}, "saved_states": {}}
        if not os.path.isfile(self.state_file):
            return default_state
        try:
            with open(self.state_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return default_state
            if not isinstance(data.get("active_domains"), dict):
                data["active_domains"] = {}
            if not isinstance(data.get("saved_states"), dict):
                data["saved_states"] = {}
            return data
        except (json.JSONDecodeError, OSError) as e:
            logger.warning("Corrupted or unreadable state file %s: %s", self.state_file, e)
            return default_state

    def save_persisted_state(self, state: Dict[str, Any]) -> bool:
        """Write state registry to disk atomically."""
        if self.dry_run:
            return True
        try:
            os.makedirs(os.path.dirname(self.state_file), exist_ok=True)
            tmp_file = f"{self.state_file}.tmp.{os.getpid()}"
            with open(tmp_file, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2)
            os.replace(tmp_file, self.state_file)
            return True
        except OSError as e:
            logger.error("Failed saving governor state to %s: %s", self.state_file, e)
            return False

    def switch_to_performance(
        self,
        domain: str = "default",
        governor: str = DEFAULT_PERFORMANCE_GOVERNOR,
        epp: str = "performance",
    ) -> Dict[str, Any]:
        """Snapshot current governor state and switch to high performance mode."""
        state = self.load_persisted_state()
        active_domains = state.get("active_domains", {})
        saved_states = state.get("saved_states", {})

        # Snapshot current CPU governors if this is the first active domain
        if not active_domains:
            current_snapshot = {}
            for info in self.get_all_cpu_states():
                cid_str = str(info["cpu_id"])
                current_snapshot[cid_str] = {
                    "governor": info["governor"],
                    "epp": info.get("epp"),
                }
            saved_states = current_snapshot

        active_domains[domain] = {
            "requested_governor": governor,
            "epp": epp,
        }

        state["active_domains"] = active_domains
        state["saved_states"] = saved_states
        self.save_persisted_state(state)

        result = self.set_governor(governor=governor, epp=epp)
        result["domain"] = domain
        result["active_domains_count"] = len(active_domains)
        return result

    def restore_governor(
        self,
        domain: str = "default",
        fallback_governor: str = DEFAULT_IDLE_GOVERNOR,
        fallback_epp: str = "balance_power",
    ) -> Dict[str, Any]:
        """Release performance lock for domain; restore original state when no domains active."""
        state = self.load_persisted_state()
        active_domains = state.get("active_domains", {})
        saved_states = state.get("saved_states", {})

        if domain in active_domains:
            del active_domains[domain]

        state["active_domains"] = active_domains
        restored = False
        res_info: Dict[str, Any] = {
            "domain": domain,
            "remaining_domains": list(active_domains.keys()),
            "status": "ok",
        }

        # If all domains stopped, restore original governors per core
        if not active_domains:
            restored_cpus = []
            failed_cpus = []
            for info in self.get_all_cpu_states():
                cid = info["cpu_id"]
                cid_str = str(cid)
                target_gov = fallback_governor
                target_epp = fallback_epp

                if cid_str in saved_states:
                    target_gov = saved_states[cid_str].get("governor") or fallback_governor
                    target_epp = saved_states[cid_str].get("epp") or fallback_epp

                cpufreq_dir = os.path.join(self.cpu_base_dir, f"cpu{cid}", "cpufreq")
                gov_file = os.path.join(cpufreq_dir, "scaling_governor")
                if self._write_file_safe(gov_file, target_gov):
                    restored_cpus.append(cid)
                else:
                    failed_cpus.append(cid)

                if target_epp:
                    epp_file = os.path.join(cpufreq_dir, "energy_performance_preference")
                    if os.path.isfile(epp_file):
                        self._write_file_safe(epp_file, target_epp)

            state["saved_states"] = {}
            restored = True
            res_info["restored"] = True
            res_info["restored_cpus"] = restored_cpus
            res_info["failed_cpus"] = failed_cpus
        else:
            restored = False
            res_info["restored"] = False
            res_info["note"] = f"Other domains still active: {list(active_domains.keys())}"

        self.save_persisted_state(state)
        return res_info

    def handle_libvirt_hook(
        self,
        domain: str,
        phase: str,
        operation: str,
        sub_operation: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Dispatch libvirt hook operations (e.g. 'prepare begin', 'release end', 'started', 'stopped')."""
        phase_op = f"{phase} {operation}".strip().lower()
        logger.info("Handling libvirt hook for domain '%s': %s (sub: %s)", domain, phase_op, sub_operation)

        if phase_op in ("prepare begin", "started", "start begin", "prepare"):
            return self.switch_to_performance(domain=domain)
        elif phase_op in ("release end", "stopped", "stop end", "release"):
            return self.restore_governor(domain=domain)
        else:
            return {
                "domain": domain,
                "phase_op": phase_op,
                "status": "ignored",
                "message": "No governor action required for this hook phase",
            }

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS CPU Governor Manager & Libvirt Hook Handler (task_364)"
    )
    parser.add_argument(
        "--action",
        choices=["status", "set", "restore", "switch-performance", "hook-qemu", "list"],
        default="status",
        help="Action to perform",
    )
    parser.add_argument("--governor", default=DEFAULT_PERFORMANCE_GOVERNOR, help="Target governor name")
    parser.add_argument("--epp", default=None, help="Target energy performance preference")
    parser.add_argument("--domain", default="default", help="Libvirt VM domain name")
    parser.add_argument("--cpus", default=None, help="Comma-separated CPU IDs (e.g. 0,1,2,3)")
    parser.add_argument("--sysfs-root", default="/", help="Root directory for sysfs (default /)")
    parser.add_argument("--state-file", default=DEFAULT_STATE_FILE, help="Path to state tracking JSON file")
    parser.add_argument("--dry-run", action="store_true", help="Simulate writes without modifying sysfs")
    parser.add_argument("--json", action="store_true", help="Output results in JSON format")

    # Arguments for hook-qemu
    parser.add_argument("hook_args", nargs="*", help="Positional hook arguments: <domain> <phase> <operation> [sub_operation]")

    args = parser.parse_args()

    manager = CPUGovernorManager(
        sysfs_root=args.sysfs_root,
        state_file=args.state_file,
        dry_run=args.dry_run,
    )

    cpu_list = [int(c.strip()) for c in args.cpus.split(",") if c.strip().isdigit()] if args.cpus else None

    if args.action == "hook-qemu" or (args.hook_args and len(args.hook_args) >= 3):
        domain = args.hook_args[0] if len(args.hook_args) > 0 else args.domain
        phase = args.hook_args[1] if len(args.hook_args) > 1 else "prepare"
        operation = args.hook_args[2] if len(args.hook_args) > 2 else "begin"
        sub_op = args.hook_args[3] if len(args.hook_args) > 3 else None
        res = manager.handle_libvirt_hook(domain, phase, operation, sub_op)
    elif args.action in ("status", "list"):
        states = manager.get_all_cpu_states()
        persisted = manager.load_persisted_state()
        res = {
            "cpus": states,
            "total_online_cpus": len(states),
            "state_file": args.state_file,
            "active_domains": persisted.get("active_domains", {}),
            "saved_states": persisted.get("saved_states", {}),
        }
    elif args.action in ("set", "switch-performance"):
        res = manager.switch_to_performance(domain=args.domain, governor=args.governor, epp=args.epp or "performance")
    elif args.action == "restore":
        res = manager.restore_governor(domain=args.domain, fallback_governor=args.governor or DEFAULT_IDLE_GOVERNOR)
    else:
        res = {"error": f"Unknown action {args.action}"}

    if args.json:
        print(json.dumps(res, indent=2))
    else:
        if args.action in ("status", "list"):
            print(f"=== MiOS CPU Governor Status ({res['total_online_cpus']} online CPUs) ===")
            for cpu in res["cpus"]:
                print(f"  CPU {cpu['cpu_id']:2d}: gov={cpu['governor']:<12} cur={cpu['cur_freq_khz']} kHz epp={cpu['epp']}")
            print(f"Active Performance Domains: {list(res['active_domains'].keys())}")
        else:
            print(f"Action '{args.action}' completed: {res}")

    return 0

if __name__ == "__main__":
    sys.exit(main())
