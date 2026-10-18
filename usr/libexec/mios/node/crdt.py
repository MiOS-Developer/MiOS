#!/usr/bin/env python3
# AI-hint: CRDT LWW-Element-Set and Vector Clock state synchronization engine for edge mesh nodes.
# AI-related: src/mios-rs/mios-node/src/state_sync.rs, tests/test-node.py, usr/share/doc/mios/adr/0020-edge-node-mesh-protocol-and-dual-tier-execution.md
"""
MiOS Distributed Lock-Free State Synchronization Engine.
Implements Last-Write-Wins Element-Set (LWW-Element-Set) CRDT, Vector Clock Causality,
and Disk-Backed Persistence (Snapshot & Append-Only Log).
"""

from __future__ import annotations

import json
import os
import time
from typing import Dict, List, Optional, Tuple

class VectorClock:
    """Vector clock causality tracker for distributed mesh nodes."""

    def __init__(self, clocks: Optional[Dict[int, int]] = None) -> None:
        self.clocks: Dict[int, int] = dict(clocks) if clocks else {}

    def increment(self, node_id: int) -> None:
        self.clocks[node_id] = self.clocks.get(node_id, 0) + 1

    def merge(self, other: VectorClock) -> None:
        for node_id, remote_val in other.clocks.items():
            local_val = self.clocks.get(node_id, 0)
            self.clocks[node_id] = max(local_val, remote_val)

    def to_dict(self) -> Dict[str, int]:
        return {str(k): v for k, v in self.clocks.items()}

    @classmethod
    def from_dict(cls, d: Dict[str, int]) -> VectorClock:
        return cls({int(k): v for k, v in d.items()})

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, VectorClock):
            return False
        return self.clocks == other.clocks

class StateElement:
    """Individual state register with LWW timestamp, originator, and deletion tombstone."""

    def __init__(
        self,
        key: str,
        value: bytes,
        timestamp_ns: int,
        originating_node_id: int,
        is_deleted: bool = False,
    ) -> None:
        self.key = key
        self.value = value
        self.timestamp_ns = timestamp_ns
        self.originating_node_id = originating_node_id
        self.is_deleted = is_deleted

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "value_hex": self.value.hex(),
            "timestamp_ns": self.timestamp_ns,
            "originating_node_id": self.originating_node_id,
            "is_deleted": self.is_deleted,
        }

    @classmethod
    def from_dict(cls, d: dict) -> StateElement:
        return cls(
            key=d["key"],
            value=bytes.fromhex(d.get("value_hex", "")),
            timestamp_ns=d["timestamp_ns"],
            originating_node_id=d["originating_node_id"],
            is_deleted=d.get("is_deleted", False),
        )

    def is_newer_than(self, other: StateElement) -> bool:
        if self.timestamp_ns != other.timestamp_ns:
            return self.timestamp_ns > other.timestamp_ns
        return self.originating_node_id > other.originating_node_id

class StateStore:
    """LWW-Element-Set CRDT state store with snapshotting and vector clock tracking."""

    def __init__(self, node_id: int, persistence_path: Optional[str] = None) -> None:
        self.node_id = node_id
        self.vector_clock = VectorClock()
        self.elements: Dict[str, StateElement] = {}
        self.persistence_path = persistence_path
        if persistence_path and os.path.exists(persistence_path):
            self.load_from_disk(persistence_path)

    def set(self, key: str, value: bytes) -> None:
        now_ns = time.time_ns()
        self.vector_clock.increment(self.node_id)
        elem = StateElement(
            key=key,
            value=value,
            timestamp_ns=now_ns,
            originating_node_id=self.node_id,
            is_deleted=False,
        )
        self.elements[key] = elem
        self._append_log(elem)

    def delete(self, key: str) -> None:
        now_ns = time.time_ns()
        self.vector_clock.increment(self.node_id)
        elem = StateElement(
            key=key,
            value=b"",
            timestamp_ns=now_ns,
            originating_node_id=self.node_id,
            is_deleted=True,
        )
        self.elements[key] = elem
        self._append_log(elem)

    def get(self, key: str) -> Optional[bytes]:
        elem = self.elements.get(key)
        if elem and not elem.is_deleted:
            return elem.value
        return None

    def replicable_elements(self) -> List[StateElement]:
        return list(self.elements.values())

    def merge_remote_store(
        self, remote_clock: VectorClock, remote_elements: List[StateElement]
    ) -> int:
        """Merges remote elements into local store; returns count of applied mutations."""
        self.vector_clock.merge(remote_clock)
        applied = 0
        for rem in remote_elements:
            loc = self.elements.get(rem.key)
            if loc is None or rem.is_newer_than(loc):
                self.elements[rem.key] = rem
                self._append_log(rem)
                applied += 1
        return applied

    def _append_log(self, elem: StateElement) -> None:
        if not self.persistence_path:
            return
        log_file = self.persistence_path + ".log"
        os.makedirs(os.path.dirname(os.path.abspath(log_file)), exist_ok=True)
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(elem.to_dict()) + "\n")

    def save_to_disk(self, path: Optional[str] = None) -> None:
        out_path = path or self.persistence_path
        if not out_path:
            return
        os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
        data = {
            "node_id": self.node_id,
            "vector_clock": self.vector_clock.to_dict(),
            "elements": [e.to_dict() for e in self.elements.values()],
        }
        tmp_path = out_path + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, out_path)

    def count_tombstones(self) -> int:
        return sum(1 for e in self.elements.values() if e.is_deleted)

    def total_elements_count(self) -> int:
        return len(self.elements)

    def compact_tombstones(
        self, ttl_s: float = 86400.0, current_time_s: Optional[float] = None
    ) -> Dict[str, int]:
        """
        Compacts tombstone entries older than `ttl_s`.
        Strict invariant: Never purges fresh tombstones within the TTL horizon or active keys.
        """
        now_ns = int(current_time_s * 1e9) if current_time_s is not None else time.time_ns()
        ttl_ns = int(ttl_s * 1e9)

        initial_count = len(self.elements)
        keys_to_purge = []
        active_count = 0
        tombstones_retained = 0

        for key, elem in self.elements.items():
            if elem.is_deleted:
                age_ns = max(0, now_ns - elem.timestamp_ns)
                if age_ns > ttl_ns:
                    keys_to_purge.append(key)
                else:
                    tombstones_retained += 1
            else:
                active_count += 1

        for k in keys_to_purge:
            del self.elements[k]

        return {
            "initial_elements": initial_count,
            "active_elements": active_count,
            "tombstones_purged": len(keys_to_purge),
            "tombstones_retained": tombstones_retained,
            "remaining_elements": len(self.elements),
        }

    def compact_disk_storage(
        self, ttl_s: float = 86400.0, current_time_s: Optional[float] = None
    ) -> Dict[str, int]:
        """
        Compacts tombstones and flushes an atomic snapshot to disk while truncating the append WAL.
        """
        stats = self.compact_tombstones(ttl_s=ttl_s, current_time_s=current_time_s)
        if self.persistence_path:
            # 1. Save clean snapshot
            self.save_to_disk(self.persistence_path)

            # 2. Truncate append log and write remaining state
            log_file = self.persistence_path + ".log"
            tmp_log = self.persistence_path + ".log.tmp"
            os.makedirs(os.path.dirname(os.path.abspath(tmp_log)), exist_ok=True)
            with open(tmp_log, "w", encoding="utf-8") as f:
                for elem in self.elements.values():
                    f.write(json.dumps(elem.to_dict()) + "\n")
            os.replace(tmp_log, log_file)

        return stats

    def load_from_disk(self, path: str) -> None:
        if not os.path.exists(path):
            return
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        self.node_id = data["node_id"]
        self.vector_clock = VectorClock.from_dict(data["vector_clock"])
        self.elements = {}
        for ed in data.get("elements", []):
            elem = StateElement.from_dict(ed)
            self.elements[elem.key] = elem
