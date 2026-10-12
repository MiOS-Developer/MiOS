#!/usr/bin/env python3
# AI-hint: Dynamic CPU Core Pinning and Cgroup v2 limits controller for mios-node workers.
# AI-related: src/mios-rs/mios-node/src/exec.rs, tests/test-node.py
"""
MiOS Dynamic Worker CPU Affinity and Cgroup v2 Resource Controller.
Manages CPU core affinity, cgroup v2 quotas (cpu.max, memory.max), and enforces Core 0 system reservation.
"""

from __future__ import annotations

import os
from enum import Enum
from typing import Dict, List, Optional, Set

class AffinityPolicy(Enum):
    EXCLUSIVE = "exclusive"
    SHARED = "shared"
    LOW_PRIORITY = "low_priority"

class NodeResourceLimits:
    """Resource bounds for node worker threads and processes."""

    def __init__(
        self,
        worker_cores: Optional[List[int]] = None,
        cpu_quota_pct: Optional[int] = 80,
        cpu_period_us: int = 100_000,
        memory_max_bytes: Optional[int] = 512 * 1024 * 1024,
        memory_high_bytes: Optional[int] = 400 * 1024 * 1024,
        exclude_core_zero: bool = True,
        cgroup_path: str = "/sys/fs/cgroup/mios.slice/worker",
    ) -> None:
        self.worker_cores = worker_cores or []
        self.cpu_quota_pct = cpu_quota_pct
        self.cpu_period_us = cpu_period_us
        self.memory_max_bytes = memory_max_bytes
        self.memory_high_bytes = memory_high_bytes
        self.exclude_core_zero = exclude_core_zero
        self.cgroup_path = cgroup_path

def filter_safe_worker_cores(
    total_system_cores: int,
    requested_cores: Optional[List[int]] = None,
    exclude_core_zero: bool = True,
) -> List[int]:
    """
    Strict Architectural Invariant:
    On multi-core systems, strips Core 0 to reserve it for kernel interrupts and system scheduling.
    On single-core systems, Core 0 is retained.
    """
    all_cores = list(range(total_system_cores))
    candidate_cores = requested_cores if requested_cores is not None else all_cores

    if total_system_cores <= 1 or not exclude_core_zero:
        return [c for c in candidate_cores if c < total_system_cores]

    return [c for c in candidate_cores if c != 0 and c < total_system_cores]

class WorkerAffinityController:
    """Tracks and assigns CPU core affinities to workers according to policy."""

    def __init__(
        self,
        total_system_cores: int,
        limits: Optional[NodeResourceLimits] = None,
    ) -> None:
        self.total_system_cores = total_system_cores
        self.limits = limits or NodeResourceLimits()
        requested = self.limits.worker_cores if self.limits.worker_cores else None
        self.available_worker_cores = filter_safe_worker_cores(
            total_system_cores, requested, self.limits.exclude_core_zero
        )
        self.allocated_exclusive_cores: Set[int] = set()

    def allocate_cores_for_policy(
        self, policy: AffinityPolicy, requested_count: int = 1
    ) -> List[int]:
        if not self.available_worker_cores:
            raise RuntimeError("No worker cores available in safe pool")

        if policy == AffinityPolicy.EXCLUSIVE:
            chosen = []
            for core in self.available_worker_cores:
                if core not in self.allocated_exclusive_cores:
                    chosen.append(core)
                    if len(chosen) == requested_count:
                        break

            if len(chosen) < requested_count:
                raise RuntimeError(
                    f"Insufficient exclusive cores: requested {requested_count}, found {len(chosen)}"
                )

            for c in chosen:
                self.allocated_exclusive_cores.add(c)
            return chosen

        elif policy == AffinityPolicy.SHARED:
            return list(self.available_worker_cores)

        elif policy == AffinityPolicy.LOW_PRIORITY:
            return [self.available_worker_cores[-1]]

        raise ValueError(f"Unknown affinity policy {policy}")

    def release_cores(self, cores: List[int]) -> None:
        for c in cores:
            self.allocated_exclusive_cores.discard(c)

class CgroupV2Controller:
    """Interacts with Linux cgroup v2 hierarchy to apply CPU and memory ceilings."""

    def __init__(self, cgroup_root: str = "/sys/fs/cgroup/mios.slice/worker") -> None:
        self.cgroup_root = cgroup_root

    @staticmethod
    def format_cpu_max(quota_pct: Optional[int], period_us: int) -> str:
        if quota_pct is not None:
            quota_us = (period_us * quota_pct) // 100
            return f"{quota_us} {period_us}"
        return f"max {period_us}"

    def apply_limits(self, limits: NodeResourceLimits) -> bool:
        if not os.path.exists(self.cgroup_root):
            try:
                os.makedirs(self.cgroup_root, exist_ok=True)
            except Exception:
                return False

        # 1. cpu.max
        cpu_max_content = self.format_cpu_max(limits.cpu_quota_pct, limits.cpu_period_us)
        try:
            with open(os.path.join(self.cgroup_root, "cpu.max"), "w", encoding="utf-8") as f:
                f.write(cpu_max_content)
        except Exception:
            pass

        # 2. memory.max
        if limits.memory_max_bytes is not None:
            try:
                with open(os.path.join(self.cgroup_root, "memory.max"), "w", encoding="utf-8") as f:
                    f.write(str(limits.memory_max_bytes))
            except Exception:
                pass

        # 3. memory.high
        if limits.memory_high_bytes is not None:
            try:
                with open(os.path.join(self.cgroup_root, "memory.high"), "w", encoding="utf-8") as f:
                    f.write(str(limits.memory_high_bytes))
            except Exception:
                pass

        return True

    def attach_pid(self, pid: int) -> bool:
        procs_path = os.path.join(self.cgroup_root, "cgroup.procs")
        if os.path.exists(procs_path):
            try:
                with open(procs_path, "w", encoding="utf-8") as f:
                    f.write(str(pid))
                return True
            except Exception:
                return False
        return False
