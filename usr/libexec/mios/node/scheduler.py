#!/usr/bin/env python3
# AI-hint: Task offloading priority queue with work-stealing scheduler for mios-node (task_336 / task_2569).
# AI-related: usr/libexec/mios/node/wasm_sandbox.py, tests/test-node.py
"""
MiOS Task Offloading Priority Queue & Work-Stealing Scheduler.
Provides 4-tier priority queues (Critical=0, High=1, Normal=2, Low=3),
work-stealing deques, global injector, hardware pin invariants, and network offload routing.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
import os
import sys
import threading
from typing import Dict, List, Optional, Tuple

_NODE_DIR = os.path.dirname(os.path.abspath(__file__))
if _NODE_DIR not in sys.path:
    sys.path.insert(0, _NODE_DIR)

class TaskPriority(IntEnum):
    CRITICAL = 0
    HIGH = 1
    NORMAL = 2
    LOW = 3

@dataclass
class TaskItem:
    task_id: int
    priority: TaskPriority
    tier: int = 1  # 1 = Wasm, 2 = Native
    target_arch: int = 0  # 0 = Agnostic, 1 = x86_64, 2 = aarch64, 3 = riscv64
    pinned_hardware: bool = False  # Invariant: If true, prohibited from being stolen away
    pinned_node_id: Optional[int] = None  # Specific node requirement if pinned
    memory_limit_bytes: int = 64 * 1024 * 1024
    execution_timeout_ms: int = 5000
    code_bytes: bytes = b""
    input_data: bytes = b""
    signature: Optional[bytes] = None
    public_key: Optional[bytes] = None
    submitted_at_ms: int = 0

    def is_stealable(self, requester_node_id: Optional[int] = None) -> bool:
        if self.pinned_hardware:
            return False
        if self.pinned_node_id is not None:
            if requester_node_id != self.pinned_node_id:
                return False
        return True

class ScheduledTargetType(IntEnum):
    LOCAL = 0
    OFFLOAD = 1
    REJECTED = 2

@dataclass
class ScheduledDecision:
    target_type: ScheduledTargetType
    node_id: Optional[int] = None
    reason: Optional[str] = None

class WorkerQueue:
    """Prioritized task deques for a single local worker thread."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: Dict[TaskPriority, List[TaskItem]] = {
            TaskPriority.CRITICAL: [],
            TaskPriority.HIGH: [],
            TaskPriority.NORMAL: [],
            TaskPriority.LOW: [],
        }

    def push(self, task: TaskItem) -> None:
        with self._lock:
            self._queues[task.priority].append(task)

    def pop_local(self) -> Optional[TaskItem]:
        with self._lock:
            for prio in (TaskPriority.CRITICAL, TaskPriority.HIGH, TaskPriority.NORMAL, TaskPriority.LOW):
                q = self._queues[prio]
                if q:
                    return q.pop()  # LIFO for cache locality
            return None

    def steal(self, requester_node_id: Optional[int] = None) -> Optional[TaskItem]:
        """Steals the oldest stealable task (FIFO for fairness)."""
        with self._lock:
            for prio in (TaskPriority.CRITICAL, TaskPriority.HIGH, TaskPriority.NORMAL, TaskPriority.LOW):
                q = self._queues[prio]
                for idx, task in enumerate(q):
                    if task.is_stealable(requester_node_id):
                        return q.pop(idx)
            return None

    def len(self) -> int:
        with self._lock:
            return sum(len(q) for q in self._queues.values())

class GlobalInjector:
    """Global prioritized injector queue for external task submission."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._queues: Dict[TaskPriority, List[TaskItem]] = {
            TaskPriority.CRITICAL: [],
            TaskPriority.HIGH: [],
            TaskPriority.NORMAL: [],
            TaskPriority.LOW: [],
        }

    def push(self, task: TaskItem) -> None:
        with self._lock:
            self._queues[task.priority].append(task)

    def pop(self) -> Optional[TaskItem]:
        with self._lock:
            for prio in (TaskPriority.CRITICAL, TaskPriority.HIGH, TaskPriority.NORMAL, TaskPriority.LOW):
                q = self._queues[prio]
                if q:
                    return q.pop(0)  # FIFO
            return None

    def steal(self, requester_node_id: Optional[int] = None) -> Optional[TaskItem]:
        with self._lock:
            for prio in (TaskPriority.CRITICAL, TaskPriority.HIGH, TaskPriority.NORMAL, TaskPriority.LOW):
                q = self._queues[prio]
                for idx, task in enumerate(q):
                    if task.is_stealable(requester_node_id):
                        return q.pop(idx)
            return None

    def len(self) -> int:
        with self._lock:
            return sum(len(q) for q in self._queues.values())

@dataclass
class SchedulerStats:
    tasks_ingested: int = 0
    tasks_executed_local: int = 0
    tasks_stolen_local: int = 0
    tasks_stolen_remote: int = 0
    tasks_offloaded: int = 0
    tasks_rejected: int = 0

class WorkStealingScheduler:
    """Multi-worker prioritized scheduler with work-stealing and hardware pin invariants."""

    def __init__(self, local_node_id: int, num_workers: int = 2) -> None:
        self.local_node_id = local_node_id
        self.num_workers = max(1, num_workers)
        self.workers = [WorkerQueue() for _ in range(self.num_workers)]
        self.injector = GlobalInjector()
        self.stats = SchedulerStats()
        self._stats_lock = threading.Lock()

    def submit_task(self, task: TaskItem, worker_hint: Optional[int] = None) -> ScheduledDecision:
        with self._stats_lock:
            self.stats.tasks_ingested += 1

        if worker_hint is not None:
            w_idx = worker_hint % self.num_workers
            self.workers[w_idx].push(task)
        else:
            self.injector.push(task)

        return ScheduledDecision(target_type=ScheduledTargetType.LOCAL)

    def pop_task(self, worker_id: int) -> Optional[TaskItem]:
        w_idx = worker_id % self.num_workers

        # 1. Try local worker queue
        task = self.workers[w_idx].pop_local()
        if task is not None:
            with self._stats_lock:
                self.stats.tasks_executed_local += 1
            return task

        # 2. Try global injector
        task = self.injector.pop()
        if task is not None:
            with self._stats_lock:
                self.stats.tasks_executed_local += 1
            return task

        # 3. Try stealing from peer workers
        for i in range(1, self.num_workers):
            victim_idx = (w_idx + i) % self.num_workers
            stolen = self.workers[victim_idx].steal(self.local_node_id)
            if stolen is not None:
                with self._stats_lock:
                    self.stats.tasks_stolen_local += 1
                    self.stats.tasks_executed_local += 1
                return stolen

        return None

    def handle_remote_steal_request(self, requester_node_id: int, max_tasks: int = 1) -> List[TaskItem]:
        stolen_tasks: List[TaskItem] = []

        while len(stolen_tasks) < max_tasks:
            t = self.injector.steal(requester_node_id)
            if t is not None:
                stolen_tasks.append(t)
            else:
                break

        if len(stolen_tasks) < max_tasks:
            for w in self.workers:
                while len(stolen_tasks) < max_tasks:
                    t = w.steal(requester_node_id)
                    if t is not None:
                        stolen_tasks.append(t)
                    else:
                        break
                if len(stolen_tasks) >= max_tasks:
                    break

        if stolen_tasks:
            with self._stats_lock:
                self.stats.tasks_stolen_remote += len(stolen_tasks)

        return stolen_tasks

    def route_task(
        self, task: TaskItem, peer_loads: Optional[List[Tuple[int, int]]] = None
    ) -> ScheduledDecision:
        if task.pinned_hardware:
            return ScheduledDecision(target_type=ScheduledTargetType.LOCAL)

        if task.pinned_node_id is not None:
            if task.pinned_node_id == self.local_node_id:
                return ScheduledDecision(target_type=ScheduledTargetType.LOCAL)
            else:
                return ScheduledDecision(
                    target_type=ScheduledTargetType.OFFLOAD, node_id=task.pinned_node_id
                )

        local_load = self.total_queue_depth()
        if local_load < 2 or not peer_loads:
            return ScheduledDecision(target_type=ScheduledTargetType.LOCAL)

        best_peer, best_load = min(peer_loads, key=lambda x: x[1])
        if best_load + 2 <= local_load:
            with self._stats_lock:
                self.stats.tasks_offloaded += 1
            return ScheduledDecision(
                target_type=ScheduledTargetType.OFFLOAD, node_id=best_peer
            )

        return ScheduledDecision(target_type=ScheduledTargetType.LOCAL)

    def total_queue_depth(self) -> int:
        return self.injector.len() + sum(w.len() for w in self.workers)

    def get_stats(self) -> SchedulerStats:
        with self._stats_lock:
            return SchedulerStats(
                tasks_ingested=self.stats.tasks_ingested,
                tasks_executed_local=self.stats.tasks_executed_local,
                tasks_stolen_local=self.stats.tasks_stolen_local,
                tasks_stolen_remote=self.stats.tasks_stolen_remote,
                tasks_offloaded=self.stats.tasks_offloaded,
                tasks_rejected=self.stats.tasks_rejected,
            )
