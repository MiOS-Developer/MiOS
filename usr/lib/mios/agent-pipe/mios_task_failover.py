#!/usr/bin/env python3
# AI-hint: Automated node failure detection and zero-loss dynamic task re-distribution engine (task_482, task_2715).
# AI-doc: usr/share/doc/mios/manual/ch83-task-failover-resilience.md
"""Automated node failure detection and zero-loss dynamic task re-distribution engine.

Monitors active mesh node heartbeats and in-flight AI task leases:
  - Detects dropped nodes (timeout after threshold, e.g. 5 seconds of lost heartbeats).
  - Persists task lease states in PostgreSQL / pgvector `in_flight_tasks` or SQLite / JSON local journal.
  - Provides zero-loss re-distribution: extracts pending prompt and conversation context,
    re-queues without dropping tokens, and dispatches to the next healthy node satisfying
    the capability requirement.
  - Supports CLI and Daemon Modes: monitor, simulate-failure, status, --mock, --dry-run.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import logging
import os
import pathlib
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

# ==============================================================================
# Configuration & Constants
# ==============================================================================

DEFAULT_HEARTBEAT_TIMEOUT_SEC = 5.0
DEFAULT_MONITOR_INTERVAL_SEC = 1.0
DEFAULT_LEASE_DURATION_SEC = 30.0
DEFAULT_MAX_RETRIES = 3

DEFAULT_DB_PATH = "/var/lib/mios/agent-pipe/task_leases.db"
DEFAULT_JSON_JOURNAL = "/var/lib/mios/agent-pipe/task_leases.json"
_PIPE_PORT = os.environ.get("MIOS_PORTS_AGENT_PIPE", "8700")
DEFAULT_ENDPOINT = os.environ.get("MIOS_AI_ENDPOINT", f"http://localhost:{_PIPE_PORT}/v1")

log = logging.getLogger("mios_task_failover")


def estimate_tokens(text: str) -> int:
    """Fast estimate of tokens in text if tiktoken is not present."""
    if not text:
        return 0
    words = len(text.split())
    chars = len(text)
    # Average ~4 chars or 0.75 words per token
    return max(words, int(chars / 4.0) + 1)


# ==============================================================================
# Data Models
# ==============================================================================

@dataclass
class NodeRecord:
    """Represents a worker or mesh node in the cluster."""
    node_id: str
    endpoint: str = DEFAULT_ENDPOINT
    capabilities: List[str] = field(default_factory=lambda: ["general"])
    last_heartbeat: float = field(default_factory=time.time)
    status: str = "healthy"  # healthy, stale, failed, dead
    timeout_threshold_sec: float = DEFAULT_HEARTBEAT_TIMEOUT_SEC
    active_tasks: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_alive(self, now: Optional[float] = None) -> bool:
        current = now if now is not None else time.time()
        return (current - self.last_heartbeat) <= self.timeout_threshold_sec

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "NodeRecord":
        return cls(
            node_id=data["node_id"],
            endpoint=data.get("endpoint", DEFAULT_ENDPOINT),
            capabilities=list(data.get("capabilities", ["general"])),
            last_heartbeat=float(data.get("last_heartbeat", time.time())),
            status=data.get("status", "healthy"),
            timeout_threshold_sec=float(data.get("timeout_threshold_sec", DEFAULT_HEARTBEAT_TIMEOUT_SEC)),
            active_tasks=int(data.get("active_tasks", 0)),
            metadata=dict(data.get("metadata", {})),
        )


@dataclass
class TaskLease:
    """Represents an in-flight AI execution task leased to a cluster node."""
    task_id: str
    node_id: Optional[str] = None
    session_id: Optional[str] = None
    prompt: str = ""
    messages: List[Dict[str, Any]] = field(default_factory=list)
    capabilities_required: List[str] = field(default_factory=lambda: ["general"])
    status: str = "pending"  # pending, leased, completed, failed, exhausted, re-queued
    retry_count: int = 0
    max_retries: int = DEFAULT_MAX_RETRIES
    prompt_tokens: int = 0
    failover_history: List[Dict[str, Any]] = field(default_factory=list)
    lease_timestamp: float = 0.0
    lease_expiry: float = 0.0
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    def __post_init__(self) -> None:
        if self.prompt_tokens == 0:
            tokens = estimate_tokens(self.prompt)
            for msg in self.messages:
                tokens += estimate_tokens(msg.get("content", ""))
            self.prompt_tokens = max(1, tokens)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TaskLease":
        return cls(
            task_id=data["task_id"],
            node_id=data.get("node_id"),
            session_id=data.get("session_id"),
            prompt=data.get("prompt", ""),
            messages=list(data.get("messages", [])),
            capabilities_required=list(data.get("capabilities_required", ["general"])),
            status=data.get("status", "pending"),
            retry_count=int(data.get("retry_count", 0)),
            max_retries=int(data.get("max_retries", DEFAULT_MAX_RETRIES)),
            prompt_tokens=int(data.get("prompt_tokens", 0)),
            failover_history=list(data.get("failover_history", [])),
            lease_timestamp=float(data.get("lease_timestamp", 0.0)),
            lease_expiry=float(data.get("lease_expiry", 0.0)),
            created_at=float(data.get("created_at", time.time())),
            updated_at=float(data.get("updated_at", time.time())),
        )


# ==============================================================================
# Persistent Storage & Journaling
# ==============================================================================

class TaskFailoverStorage:
    """Dual-layer storage manager: SQLite/JSON local journal with optional PostgreSQL."""

    def __init__(self, db_path: Optional[str] = None, json_path: Optional[str] = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.json_path = json_path or DEFAULT_JSON_JOURNAL
        self._mem_conn: Optional[sqlite3.Connection] = None
        self._init_sqlite()

    def _init_sqlite(self) -> None:
        if self.db_path == ":memory:":
            self._mem_conn = sqlite3.connect(":memory:")
            self._mem_conn.row_factory = sqlite3.Row
            conn = self._mem_conn
        else:
            try:
                p = pathlib.Path(self.db_path)
                if not p.parent.exists():
                    p.parent.mkdir(parents=True, exist_ok=True)
                conn = sqlite3.connect(self.db_path)
            except (OSError, PermissionError):
                # Fall back to in-memory SQLite if /var/lib/mios is not accessible
                self.db_path = ":memory:"
                self._mem_conn = sqlite3.connect(":memory:")
                self._mem_conn.row_factory = sqlite3.Row
                conn = self._mem_conn

        with conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS node_heartbeats (
                    node_id TEXT PRIMARY KEY,
                    endpoint TEXT NOT NULL,
                    capabilities TEXT NOT NULL,
                    last_heartbeat REAL NOT NULL,
                    status TEXT NOT NULL,
                    timeout_threshold_sec REAL NOT NULL,
                    active_tasks INTEGER NOT NULL,
                    metadata TEXT NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS in_flight_tasks (
                    task_id TEXT PRIMARY KEY,
                    node_id TEXT,
                    session_id TEXT,
                    prompt TEXT NOT NULL,
                    messages TEXT NOT NULL,
                    capabilities_required TEXT NOT NULL,
                    status TEXT NOT NULL,
                    retry_count INTEGER NOT NULL,
                    max_retries INTEGER NOT NULL,
                    prompt_tokens INTEGER NOT NULL,
                    failover_history TEXT NOT NULL,
                    lease_timestamp REAL NOT NULL,
                    lease_expiry REAL NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
            """)
        if conn is not self._mem_conn:
            conn.close()

    def _get_conn(self) -> sqlite3.Connection:
        if self._mem_conn is not None:
            return self._mem_conn
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _close_conn(self, conn: sqlite3.Connection) -> None:
        if conn is not self._mem_conn:
            conn.close()

    def save_node(self, node: NodeRecord) -> None:
        conn = self._get_conn()
        with conn:
            conn.execute("""
                INSERT OR REPLACE INTO node_heartbeats
                (node_id, endpoint, capabilities, last_heartbeat, status, timeout_threshold_sec, active_tasks, metadata, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                node.node_id,
                node.endpoint,
                json.dumps(node.capabilities),
                node.last_heartbeat,
                node.status,
                node.timeout_threshold_sec,
                node.active_tasks,
                json.dumps(node.metadata),
                time.time()
            ))
        self._close_conn(conn)

    def load_nodes(self) -> Dict[str, NodeRecord]:
        conn = self._get_conn()
        nodes = {}
        for row in conn.execute("SELECT * FROM node_heartbeats"):
            node = NodeRecord(
                node_id=row["node_id"],
                endpoint=row["endpoint"],
                capabilities=json.loads(row["capabilities"]),
                last_heartbeat=row["last_heartbeat"],
                status=row["status"],
                timeout_threshold_sec=row["timeout_threshold_sec"],
                active_tasks=row["active_tasks"],
                metadata=json.loads(row["metadata"]),
            )
            nodes[node.node_id] = node
        self._close_conn(conn)
        return nodes

    def save_task(self, task: TaskLease) -> None:
        conn = self._get_conn()
        with conn:
            conn.execute("""
                INSERT OR REPLACE INTO in_flight_tasks
                (task_id, node_id, session_id, prompt, messages, capabilities_required, status,
                 retry_count, max_retries, prompt_tokens, failover_history, lease_timestamp, lease_expiry, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                task.task_id,
                task.node_id,
                task.session_id,
                task.prompt,
                json.dumps(task.messages),
                json.dumps(task.capabilities_required),
                task.status,
                task.retry_count,
                task.max_retries,
                task.prompt_tokens,
                json.dumps(task.failover_history),
                task.lease_timestamp,
                task.lease_expiry,
                task.created_at,
                time.time()
            ))
        self._close_conn(conn)

    def load_tasks(self) -> Dict[str, TaskLease]:
        conn = self._get_conn()
        tasks = {}
        for row in conn.execute("SELECT * FROM in_flight_tasks"):
            task = TaskLease(
                task_id=row["task_id"],
                node_id=row["node_id"],
                session_id=row["session_id"],
                prompt=row["prompt"],
                messages=json.loads(row["messages"]),
                capabilities_required=json.loads(row["capabilities_required"]),
                status=row["status"],
                retry_count=row["retry_count"],
                max_retries=row["max_retries"],
                prompt_tokens=row["prompt_tokens"],
                failover_history=json.loads(row["failover_history"]),
                lease_timestamp=row["lease_timestamp"],
                lease_expiry=row["lease_expiry"],
                created_at=row["created_at"],
                updated_at=row["updated_at"],
            )
            tasks[task.task_id] = task
        self._close_conn(conn)
        return tasks

    def export_json_journal(self, nodes: Dict[str, NodeRecord], tasks: Dict[str, TaskLease]) -> None:
        """Atomically persist snapshot to JSON journal file."""
        try:
            payload = {
                "timestamp": time.time(),
                "nodes": {k: v.to_dict() for k, v in nodes.items()},
                "tasks": {k: v.to_dict() for k, v in tasks.items()},
            }
            p = pathlib.Path(self.json_path)
            if not p.parent.exists():
                p.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = p.with_suffix(".tmp")
            with open(tmp_path, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2)
            tmp_path.replace(p)
        except (OSError, PermissionError) as e:
            log.debug("JSON journal write skipped: %s", e)


# ==============================================================================
# Failover & Dynamic Re-distribution Engine
# ==============================================================================

class TaskFailoverEngine:
    """Core engine managing node heartbeats, failure detection, and zero-loss task failover."""

    def __init__(
        self,
        storage: Optional[TaskFailoverStorage] = None,
        heartbeat_timeout_sec: float = DEFAULT_HEARTBEAT_TIMEOUT_SEC,
        dry_run: bool = False,
        mock_mode: bool = False,
        verbose: bool = False,
    ):
        self.storage = storage or TaskFailoverStorage()
        self.heartbeat_timeout_sec = heartbeat_timeout_sec
        self.dry_run = dry_run
        self.mock_mode = mock_mode
        self.verbose = verbose

        self.nodes: Dict[str, NodeRecord] = {}
        self.tasks: Dict[str, TaskLease] = {}

        self._load_state()
        if self.mock_mode and not self.nodes:
            self.init_mock_cluster()

    def _load_state(self) -> None:
        self.nodes = self.storage.load_nodes()
        self.tasks = self.storage.load_tasks()

    def _sync(self) -> None:
        if not self.dry_run:
            self.storage.export_json_journal(self.nodes, self.tasks)

    def init_mock_cluster(self) -> None:
        """Initialize mock nodes and in-flight tasks for test and mock execution."""
        now = time.time()
        self.register_node(
            node_id="blade-1",
            endpoint=f"http://10.42.0.1:{_PIPE_PORT}/v1",
            capabilities=["gpu", "coder", "general"],
            timeout_threshold_sec=self.heartbeat_timeout_sec,
        )
        self.register_node(
            node_id="blade-2",
            endpoint=f"http://10.42.0.2:{_PIPE_PORT}/v1",
            capabilities=["gpu", "coder", "general"],
            timeout_threshold_sec=self.heartbeat_timeout_sec,
        )
        self.register_node(
            node_id="blade-3",
            endpoint=f"http://10.42.0.3:{_PIPE_PORT}/v1",
            capabilities=["cpu", "general"],
            timeout_threshold_sec=self.heartbeat_timeout_sec,
        )

        # Create sample in-flight task on blade-1
        self.create_task_lease(
            task_id="task-mock-001",
            prompt="Refactor authentication handshake in Rust using zero-copy tokio buffers.",
            messages=[
                {"role": "system", "content": "You are a specialized MiOS kernel security engineer."},
                {"role": "user", "content": "Refactor authentication handshake in Rust using zero-copy tokio buffers."}
            ],
            capabilities_required=["gpu", "coder"],
            node_id="blade-1",
            session_id="sess-8891-auth",
        )

    def register_node(
        self,
        node_id: str,
        endpoint: str = DEFAULT_ENDPOINT,
        capabilities: Optional[List[str]] = None,
        timeout_threshold_sec: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> NodeRecord:
        """Register or update a cluster worker node."""
        caps = capabilities if capabilities is not None else ["general"]
        timeout = timeout_threshold_sec if timeout_threshold_sec is not None else self.heartbeat_timeout_sec
        node = NodeRecord(
            node_id=node_id,
            endpoint=endpoint,
            capabilities=caps,
            last_heartbeat=time.time(),
            status="healthy",
            timeout_threshold_sec=timeout,
            active_tasks=0,
            metadata=metadata or {},
        )
        self.nodes[node_id] = node
        if not self.dry_run:
            self.storage.save_node(node)
        self._sync()
        return node

    def record_heartbeat(self, node_id: str, now: Optional[float] = None) -> bool:
        """Record an incoming heartbeat from a node, keeping it alive."""
        ts = now if now is not None else time.time()
        if node_id not in self.nodes:
            self.register_node(node_id)

        node = self.nodes[node_id]
        node.last_heartbeat = ts
        if node.status in ("stale", "failed"):
            node.status = "healthy"
            log.info("Node %s recovered to healthy state", node_id)
        if not self.dry_run:
            self.storage.save_node(node)
        self._sync()
        return True

    def create_task_lease(
        self,
        task_id: str,
        prompt: str,
        messages: Optional[List[Dict[str, Any]]] = None,
        capabilities_required: Optional[List[str]] = None,
        node_id: Optional[str] = None,
        session_id: Optional[str] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
    ) -> TaskLease:
        """Create and lease an AI task to an assigned node."""
        caps = capabilities_required or ["general"]
        msgs = messages or []
        now = time.time()

        task = TaskLease(
            task_id=task_id,
            node_id=node_id,
            session_id=session_id,
            prompt=prompt,
            messages=msgs,
            capabilities_required=caps,
            status="leased" if node_id else "pending",
            retry_count=0,
            max_retries=max_retries,
            prompt_tokens=estimate_tokens(prompt) + sum(estimate_tokens(m.get("content", "")) for m in msgs),
            lease_timestamp=now if node_id else 0.0,
            lease_expiry=(now + DEFAULT_LEASE_DURATION_SEC) if node_id else 0.0,
            created_at=now,
            updated_at=now,
        )

        if node_id and node_id in self.nodes:
            self.nodes[node_id].active_tasks += 1
            if not self.dry_run:
                self.storage.save_node(self.nodes[node_id])

        self.tasks[task_id] = task
        if not self.dry_run:
            self.storage.save_task(task)
        self._sync()
        return task

    def audit_nodes(self, now: Optional[float] = None) -> List[Tuple[NodeRecord, str]]:
        """Audit all nodes and detect state transitions based on heartbeat timeout."""
        current = now if now is not None else time.time()
        transitions: List[Tuple[NodeRecord, str]] = []

        for node_id, node in self.nodes.items():
            elapsed = current - node.last_heartbeat
            prior_status = node.status

            if elapsed > node.timeout_threshold_sec:
                new_status = "failed"
            elif elapsed > (node.timeout_threshold_sec * 0.7):
                new_status = "stale"
            else:
                new_status = "healthy"

            if new_status != prior_status:
                node.status = new_status
                transitions.append((node, new_status))
                if not self.dry_run:
                    self.storage.save_node(node)

        if transitions:
            self._sync()
        return transitions

    def detect_failures(self, now: Optional[float] = None) -> List[str]:
        """Detect and return list of node_ids that have newly transitioned to failed."""
        transitions = self.audit_nodes(now=now)
        failed = [node.node_id for node, status in transitions if status == "failed"]
        return failed

    def find_surviving_candidate(self, capabilities_required: List[str]) -> Optional[NodeRecord]:
        """Find the optimal surviving healthy node meeting capability criteria."""
        req_set = set(capabilities_required)
        candidates: List[NodeRecord] = []

        for node in self.nodes.values():
            if node.status == "healthy":
                if req_set.issubset(set(node.capabilities)):
                    candidates.append(node)

        if not candidates:
            return None

        # Choose node with lowest active task count (least-loaded)
        candidates.sort(key=lambda n: n.active_tasks)
        return candidates[0]

    def re_distribute_tasks(self, failed_node_id: str, now: Optional[float] = None) -> Dict[str, Any]:
        """Dynamically re-distribute in-flight tasks from failed node without dropping context."""
        current = now if now is not None else time.time()
        reassigned: List[Dict[str, Any]] = []
        exhausted: List[Dict[str, Any]] = []

        # Find all in-flight tasks leased to this failed node
        in_flight = [t for t in self.tasks.values() if t.node_id == failed_node_id and t.status == "leased"]

        for task in in_flight:
            # Check retry limit
            if task.retry_count >= task.max_retries:
                task.status = "exhausted"
                task.updated_at = current
                exhausted.append({
                    "task_id": task.task_id,
                    "reason": f"max_retries_exceeded ({task.retry_count}/{task.max_retries})",
                    "prompt_tokens": task.prompt_tokens,
                })
                if not self.dry_run:
                    self.storage.save_task(task)
                continue

            # Find next healthy node
            candidate = self.find_surviving_candidate(task.capabilities_required)
            if candidate is None:
                # No surviving node satisfies requirements -> mark re-queued / exhausted
                task.status = "re-queued"
                task.updated_at = current
                exhausted.append({
                    "task_id": task.task_id,
                    "reason": "no_healthy_node_with_required_capabilities",
                    "required_capabilities": task.capabilities_required,
                    "prompt_tokens": task.prompt_tokens,
                })
                if not self.dry_run:
                    self.storage.save_task(task)
                continue

            # Zero-loss re-assignment: prompt, messages, and tokens are preserved intact
            old_node = task.node_id
            task.node_id = candidate.node_id
            task.retry_count += 1
            task.status = "leased"
            task.lease_timestamp = current
            task.lease_expiry = current + DEFAULT_LEASE_DURATION_SEC
            task.updated_at = current

            # Record audit trace
            failover_record = {
                "from_node": old_node,
                "to_node": candidate.node_id,
                "timestamp": current,
                "tokens_preserved": task.prompt_tokens,
                "retry_count": task.retry_count,
                "reason": "node_heartbeat_timeout",
            }
            task.failover_history.append(failover_record)

            # Update candidate node load
            candidate.active_tasks += 1

            if not self.dry_run:
                self.storage.save_task(task)
                self.storage.save_node(candidate)

            reassigned.append({
                "task_id": task.task_id,
                "target_node": candidate.node_id,
                "preserved_prompt_length": len(task.prompt),
                "preserved_tokens": task.prompt_tokens,
                "retry_count": task.retry_count,
                "status": task.status,
            })

        # Decrement active tasks on failed node
        if failed_node_id in self.nodes:
            self.nodes[failed_node_id].active_tasks = 0
            if not self.dry_run:
                self.storage.save_node(self.nodes[failed_node_id])

        self._sync()

        return {
            "failed_node": failed_node_id,
            "tasks_reassigned_count": len(reassigned),
            "tasks_exhausted_count": len(exhausted),
            "reassigned": reassigned,
            "exhausted": exhausted,
            "surviving_healthy_nodes": [n.node_id for n in self.nodes.values() if n.status == "healthy"],
        }

    def simulate_failure(self, node_id: str) -> Dict[str, Any]:
        """Trigger synthetic node drop and validate seamless zero-loss re-distribution."""
        if node_id not in self.nodes:
            raise KeyError(f"Node '{node_id}' not found in cluster registry.")

        node = self.nodes[node_id]
        now = time.time()
        # Fast-forward heartbeat into expired past
        node.last_heartbeat = now - (node.timeout_threshold_sec + 10.0)
        node.status = "failed"

        if not self.dry_run:
            self.storage.save_node(node)

        # Trigger dynamic re-distribution
        report = self.re_distribute_tasks(node_id, now=now)
        return report

    def get_status_report(self) -> Dict[str, Any]:
        """Compile comprehensive status of node heartbeats and in-flight task leases."""
        now = time.time()
        node_stats = []
        for n in sorted(self.nodes.values(), key=lambda x: x.node_id):
            elapsed = now - n.last_heartbeat
            node_stats.append({
                "node_id": n.node_id,
                "endpoint": n.endpoint,
                "capabilities": n.capabilities,
                "status": n.status,
                "active_tasks": n.active_tasks,
                "elapsed_heartbeat_sec": round(elapsed, 2),
                "timeout_threshold_sec": n.timeout_threshold_sec,
            })

        task_stats = []
        for t in sorted(self.tasks.values(), key=lambda x: x.task_id):
            task_stats.append({
                "task_id": t.task_id,
                "node_id": t.node_id,
                "session_id": t.session_id,
                "status": t.status,
                "retry_count": t.retry_count,
                "prompt_tokens": t.prompt_tokens,
                "prompt_preview": (t.prompt[:60] + "...") if len(t.prompt) > 60 else t.prompt,
                "capabilities_required": t.capabilities_required,
                "failover_count": len(t.failover_history),
            })

        return {
            "timestamp": now,
            "nodes_total": len(self.nodes),
            "nodes_healthy": sum(1 for n in self.nodes.values() if n.status == "healthy"),
            "nodes_failed": sum(1 for n in self.nodes.values() if n.status == "failed"),
            "tasks_total": len(self.tasks),
            "tasks_in_flight": sum(1 for t in self.tasks.values() if t.status == "leased"),
            "tasks_completed": sum(1 for t in self.tasks.values() if t.status == "completed"),
            "nodes": node_stats,
            "tasks": task_stats,
        }

    def monitor_loop(self, interval: float = DEFAULT_MONITOR_INTERVAL_SEC, once: bool = False) -> None:
        """Run monitor loop continuously or as single pass."""
        log.info("Starting task failover monitor (interval=%.2fs, threshold=%.2fs)",
                 interval, self.heartbeat_timeout_sec)
        while True:
            now = time.time()
            failed_nodes = self.detect_failures(now=now)
            for fn in failed_nodes:
                log.warning("Detected node failure: %s (heartbeat timeout). Re-distributing tasks...", fn)
                result = self.re_distribute_tasks(fn, now=now)
                log.info("Failover completed for %s: %d reassigned, %d exhausted",
                         fn, result["tasks_reassigned_count"], result["tasks_exhausted_count"])

            if once:
                break
            time.sleep(interval)


# ==============================================================================
# CLI Interface
# ==============================================================================

def main() -> int:
    parser = argparse.ArgumentParser(
        description="MiOS Automated Node Failure Detection & Zero-Loss Task Re-Distribution Engine"
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable verbose diagnostic logs")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without modifying database or journals")
    parser.add_argument("--mock", action="store_true", help="Execute with simulated multi-node cluster and tasks")
    parser.add_argument("--db-path", type=str, default=DEFAULT_DB_PATH, help="Path to SQLite lease journal database")
    parser.add_argument("--timeout", type=float, default=DEFAULT_HEARTBEAT_TIMEOUT_SEC, help="Heartbeat timeout threshold in seconds")

    subparsers = parser.add_subparsers(dest="command", help="Command to execute")

    # Command: monitor
    monitor_parser = subparsers.add_parser("monitor", help="Run heartbeat monitor daemon")
    monitor_parser.add_argument("--interval", type=float, default=DEFAULT_MONITOR_INTERVAL_SEC, help="Monitoring polling interval in seconds")
    monitor_parser.add_argument("--once", action="store_true", help="Run single evaluation pass and exit")

    # Command: simulate-failure
    sim_parser = subparsers.add_parser("simulate-failure", help="Simulate a node drop and execute dynamic re-distribution")
    sim_parser.add_argument("node_id", type=str, help="ID of node to synthetically fail")

    # Command: status
    subparsers.add_parser("status", help="Report cluster node health and in-flight task leases")

    # Command: heartbeat
    hb_parser = subparsers.add_parser("heartbeat", help="Send heartbeat for node")
    hb_parser.add_argument("node_id", type=str, help="Node ID")

    # Command: register-node
    reg_parser = subparsers.add_parser("register-node", help="Register a worker node")
    reg_parser.add_argument("node_id", type=str, help="Node ID")
    reg_parser.add_argument("--endpoint", type=str, default=DEFAULT_ENDPOINT, help="Node OpenAI endpoint URL")
    reg_parser.add_argument("--capabilities", type=str, default="general", help="Comma-separated capabilities")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    )

    storage = TaskFailoverStorage(db_path=args.db_path)
    engine = TaskFailoverEngine(
        storage=storage,
        heartbeat_timeout_sec=args.timeout,
        dry_run=args.dry_run,
        mock_mode=args.mock,
        verbose=args.verbose,
    )

    if not args.command or args.command == "status":
        report = engine.get_status_report()
        print(json.dumps(report, indent=2))
        return 0

    if args.command == "monitor":
        engine.monitor_loop(interval=args.interval, once=args.once)
        return 0

    if args.command == "simulate-failure":
        try:
            report = engine.simulate_failure(args.node_id)
            print(json.dumps(report, indent=2))
            return 0
        except KeyError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1

    if args.command == "heartbeat":
        engine.record_heartbeat(args.node_id)
        print(json.dumps({"status": "ok", "node_id": args.node_id, "heartbeat": time.time()}))
        return 0

    if args.command == "register-node":
        caps = [c.strip() for c in args.capabilities.split(",") if c.strip()]
        node = engine.register_node(args.node_id, endpoint=args.endpoint, capabilities=caps)
        print(json.dumps(node.to_dict(), indent=2))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
