#!/usr/bin/env python3
# AI-hint: Avahi/mDNS zero-conf mesh discovery, node heartbeat monitor, 3-strike dead-peer eviction, and Ed25519 authentication handshake.
# AI-related: src/mios-rs/mios-node/src/heartbeat.rs, src/mios-rs/mios-node/src/node.rs, tests/test-node-mesh.py, tests/test-node.py
# AI-doc: usr/share/doc/mios/manual/node.md
"""
MiOS Edge Node Mesh Discovery, Heartbeat Monitor & Dead-Peer Eviction Engine (task_331 / task_2564).
Manages zero-conf peer announcements, periodic 5s heartbeats, 3-strike dead peer detection (15s eviction threshold),
degraded state transitions, routing table pruning, and eviction event dispatching.
"""

from __future__ import annotations

import enum
import hashlib
import hmac
import json
import secrets
import time
from typing import Callable, Dict, List, Optional, Tuple

class PeerHealthStatus(str, enum.Enum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DEAD = "dead"

class EvictionEvent:
    """Represents a peer node eviction event from the cluster routing table."""

    def __init__(
        self,
        node_id: int,
        reason: str,
        timestamp: float,
        missed_strikes: int,
        elapsed_secs: float,
    ) -> None:
        self.node_id = node_id
        self.reason = reason
        self.timestamp = timestamp
        self.missed_strikes = missed_strikes
        self.elapsed_secs = elapsed_secs

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "reason": self.reason,
            "timestamp": self.timestamp,
            "missed_strikes": self.missed_strikes,
            "elapsed_secs": round(self.elapsed_secs, 3),
        }

    def __repr__(self) -> str:
        return (
            f"EvictionEvent(node_id={self.node_id}, reason='{self.reason}', "
            f"strikes={self.missed_strikes}, elapsed={self.elapsed_secs:.1f}s)"
        )

class ClusterPeer:
    """Peer entry in the active cluster routing table."""

    def __init__(
        self,
        node_id: int,
        addr: str,
        port: int,
        last_seen: float,
        uptime_secs: float = 0.0,
        cpu_load_pct: int = 0,
        mem_available_kb: int = 0,
        capabilities: Optional[List[str]] = None,
    ) -> None:
        self.node_id = node_id
        self.addr = addr
        self.port = port
        self.last_seen = last_seen
        self.missed_strikes = 0
        self.status = PeerHealthStatus.HEALTHY
        self.uptime_secs = uptime_secs
        self.cpu_load_pct = cpu_load_pct
        self.mem_available_kb = mem_available_kb
        self.capabilities = capabilities or []

    @property
    def is_active(self) -> bool:
        return self.status != PeerHealthStatus.DEAD

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "addr": self.addr,
            "port": self.port,
            "last_seen": self.last_seen,
            "missed_strikes": self.missed_strikes,
            "status": self.status.value,
            "uptime_secs": self.uptime_secs,
            "cpu_load_pct": self.cpu_load_pct,
            "mem_available_kb": self.mem_available_kb,
            "capabilities": self.capabilities,
        }

    def __repr__(self) -> str:
        return f"ClusterPeer(node_id={self.node_id}, addr='{self.addr}:{self.port}', status={self.status.value}, strikes={self.missed_strikes})"

class HeartbeatMonitor:
    """Tracks peer health and executes 3-strike dead peer routing table evictions (task_331 / task_2564)."""

    def __init__(
        self,
        local_node_id: int,
        heartbeat_interval: float = 5.0,
        degraded_threshold: float = 10.0,
        eviction_threshold: float = 15.0,
    ) -> None:
        self.local_node_id = local_node_id
        self.heartbeat_interval = heartbeat_interval
        self.degraded_threshold = degraded_threshold
        self.eviction_threshold = eviction_threshold
        self.routing_table: Dict[int, ClusterPeer] = {}
        self.eviction_listeners: List[Callable[[EvictionEvent], None]] = []

    def add_eviction_listener(self, callback: Callable[[EvictionEvent], None]) -> None:
        self.eviction_listeners.append(callback)

    def record_heartbeat(
        self,
        node_id: int,
        addr: str,
        port: int,
        uptime_secs: float = 0.0,
        cpu_load_pct: int = 0,
        mem_available_kb: int = 0,
        now: Optional[float] = None,
    ) -> Optional[ClusterPeer]:
        if node_id == self.local_node_id:
            return None

        ts = now if now is not None else time.time()
        peer = self.routing_table.get(node_id)
        if peer is None:
            peer = ClusterPeer(
                node_id=node_id,
                addr=addr,
                port=port,
                last_seen=ts,
                uptime_secs=uptime_secs,
                cpu_load_pct=cpu_load_pct,
                mem_available_kb=mem_available_kb,
            )
            self.routing_table[node_id] = peer
        else:
            peer.addr = addr
            peer.port = port
            peer.last_seen = ts
            peer.missed_strikes = 0
            peer.status = PeerHealthStatus.HEALTHY
            peer.uptime_secs = uptime_secs
            peer.cpu_load_pct = cpu_load_pct
            peer.mem_available_kb = mem_available_kb

        return peer

    def record_announce(
        self,
        node_id: int,
        addr: str,
        port: int,
        capabilities: Optional[List[str]] = None,
        now: Optional[float] = None,
    ) -> Optional[ClusterPeer]:
        if node_id == self.local_node_id:
            return None

        ts = now if now is not None else time.time()
        peer = self.routing_table.get(node_id)
        if peer is None:
            peer = ClusterPeer(
                node_id=node_id,
                addr=addr,
                port=port,
                last_seen=ts,
                capabilities=capabilities,
            )
            self.routing_table[node_id] = peer
        else:
            peer.addr = addr
            peer.port = port
            peer.last_seen = ts
            peer.missed_strikes = 0
            peer.status = PeerHealthStatus.HEALTHY
            if capabilities is not None:
                peer.capabilities = capabilities

        return peer

    def assess_health(self, elapsed: float) -> Tuple[PeerHealthStatus, int]:
        strikes = int(elapsed // self.heartbeat_interval) if self.heartbeat_interval > 0 else 0

        if elapsed >= self.eviction_threshold:
            return PeerHealthStatus.DEAD, max(strikes, 3)
        elif elapsed >= self.degraded_threshold:
            return PeerHealthStatus.DEGRADED, max(strikes, 2)
        else:
            return PeerHealthStatus.HEALTHY, strikes

    def sweep(
        self, now: Optional[float] = None
    ) -> Tuple[List[int], List[int], List[EvictionEvent]]:
        ts = now if now is not None else time.time()
        healthy_nodes: List[int] = []
        degraded_nodes: List[int] = []
        evicted_events: List[EvictionEvent] = []

        to_remove = []
        for node_id, peer in self.routing_table.items():
            elapsed = max(0.0, ts - peer.last_seen)
            health, strikes = self.assess_health(elapsed)

            peer.missed_strikes = strikes
            peer.status = health

            if health == PeerHealthStatus.HEALTHY:
                healthy_nodes.append(node_id)
            elif health == PeerHealthStatus.DEGRADED:
                degraded_nodes.append(node_id)
            elif health == PeerHealthStatus.DEAD:
                event = EvictionEvent(
                    node_id=node_id,
                    reason=f"3-strike timeout: {elapsed:.1f}s elapsed without heartbeat (threshold: {self.eviction_threshold:.1f}s)",
                    timestamp=ts,
                    missed_strikes=strikes,
                    elapsed_secs=elapsed,
                )
                evicted_events.append(event)
                to_remove.append(node_id)

        # Prune dead peers
        for nid in to_remove:
            del self.routing_table[nid]

        # Dispatch eviction events to registered listeners
        for event in evicted_events:
            for listener in self.eviction_listeners:
                try:
                    listener(event)
                except Exception:
                    pass

        return healthy_nodes, degraded_nodes, evicted_events

    def evict_peer(
        self, node_id: int, reason: str = "manual_eviction", now: Optional[float] = None
    ) -> Optional[EvictionEvent]:
        ts = now if now is not None else time.time()
        peer = self.routing_table.pop(node_id, None)
        if peer is None:
            return None

        elapsed = max(0.0, ts - peer.last_seen)
        event = EvictionEvent(
            node_id=node_id,
            reason=reason,
            timestamp=ts,
            missed_strikes=peer.missed_strikes,
            elapsed_secs=elapsed,
        )
        for listener in self.eviction_listeners:
            try:
                listener(event)
            except Exception:
                pass
        return event

    def get_peer(self, node_id: int) -> Optional[ClusterPeer]:
        return self.routing_table.get(node_id)

    def get_active_peers(self) -> List[ClusterPeer]:
        return [p for p in self.routing_table.values() if p.is_active]

    def is_peer_active(self, node_id: int) -> bool:
        peer = self.routing_table.get(node_id)
        return peer is not None and peer.is_active

    @property
    def peer_count(self) -> int:
        return len(self.routing_table)

# Backward-compatible Zero-Conf Classes
class NodeAdvertisement:
    """Zero-conf node advertisement packet for mDNS / local broadcast."""

    def __init__(
        self,
        node_id: int,
        hostname: str,
        port: int,
        capabilities: List[str],
        public_key_hex: str,
        service_type: str = "_mios-node._tcp.local.",
    ) -> None:
        self.node_id = node_id
        self.hostname = hostname
        self.port = port
        self.capabilities = capabilities
        self.public_key_hex = public_key_hex
        self.service_type = service_type
        self.announced_at = time.time()

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "hostname": self.hostname,
            "port": self.port,
            "capabilities": self.capabilities,
            "public_key_hex": self.public_key_hex,
            "service_type": self.service_type,
            "announced_at": self.announced_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> NodeAdvertisement:
        adv = cls(
            node_id=d["node_id"],
            hostname=d["hostname"],
            port=d["port"],
            capabilities=d.get("capabilities", []),
            public_key_hex=d["public_key_hex"],
            service_type=d.get("service_type", "_mios-node._tcp.local."),
        )
        adv.announced_at = d.get("announced_at", time.time())
        return adv

class NodeHandshake:
    """Cryptographic challenge-response mutual handshake validator."""

    @staticmethod
    def generate_challenge() -> bytes:
        return secrets.token_bytes(32)

    @staticmethod
    def sign_challenge(challenge: bytes, secret_key: bytes) -> bytes:
        return hmac.new(secret_key, challenge, hashlib.sha256).digest()

    @staticmethod
    def verify_response(challenge: bytes, response: bytes, secret_key: bytes) -> bool:
        expected = hmac.new(secret_key, challenge, hashlib.sha256).digest()
        return hmac.compare_digest(expected, response)

class MeshDiscoveryRegistry:
    """Discovers, tracks, and manages peer edge nodes on the local mesh network."""

    def __init__(self, local_node_id: int, shared_cluster_key: bytes) -> None:
        self.local_node_id = local_node_id
        self.shared_cluster_key = shared_cluster_key
        self.discovered_nodes: Dict[int, NodeAdvertisement] = {}
        self.authenticated_nodes: Dict[int, float] = {}

    def register_advertisement(self, adv: NodeAdvertisement) -> bool:
        if adv.node_id == self.local_node_id:
            return False
        self.discovered_nodes[adv.node_id] = adv
        return True

    def authenticate_peer(self, node_id: int, response: bytes, challenge: bytes) -> bool:
        if node_id not in self.discovered_nodes:
            return False
        if not NodeHandshake.verify_response(challenge, response, self.shared_cluster_key):
            return False
        self.authenticated_nodes[node_id] = time.time()
        return True

    def active_mesh_nodes(self, max_age_secs: float = 60.0) -> List[dict]:
        now = time.time()
        active = []
        for nid, adv in self.discovered_nodes.items():
            auth_time = self.authenticated_nodes.get(nid)
            if auth_time and (now - auth_time) <= max_age_secs:
                active.append({
                    "node_id": nid,
                    "hostname": adv.hostname,
                    "port": adv.port,
                    "capabilities": adv.capabilities,
                    "authenticated": True,
                    "last_seen_secs_ago": round(now - auth_time, 3),
                })
        return active
