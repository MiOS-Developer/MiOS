#!/usr/bin/env python3
# AI-hint: Automated fallback to Tailscale and WireGuard overlay when LAN broadcast is partitioned (task_340 / task_2573).
# AI-related: usr/libexec/mios/node/wire.py, tests/test-node.py
"""
MiOS Multi-Transport Router & LAN Partition Overlay Failover Engine.
Provides multi-transport routing (LanBroadcast=1, WireGuard=2, Tailscale=3, DirectTcp=4),
3-strike LAN partition failure detection, and asymmetric anti-flap recovery hysteresis (120s dwell).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
import os
import sys
import threading
import time
from typing import Dict, List, Optional, Tuple

_NODE_DIR = os.path.dirname(os.path.abspath(__file__))
if _NODE_DIR not in sys.path:
    sys.path.insert(0, _NODE_DIR)

class TransportType(IntEnum):
    LAN_BROADCAST = 1
    WIREGUARD = 2
    TAILSCALE = 3
    DIRECT_TCP = 4

@dataclass
class TransportHealth:
    consecutive_success: int = 0
    consecutive_misses: int = 0
    last_success_ms: int = 0
    last_miss_ms: int = 0
    latency_ms: int = 0
    is_healthy: bool = True

@dataclass
class PeerRoute:
    node_id: int
    endpoints: Dict[TransportType, str]
    active_transport: TransportType
    transport_health: Dict[TransportType, TransportHealth] = field(default_factory=dict)
    is_lan_partitioned: bool = False
    last_failover_ms: int = 0
    last_lan_recovery_start_ms: Optional[int] = None

    @classmethod
    def create(cls, node_id: int, endpoints: Dict[TransportType, str]) -> PeerRoute:
        health = {t: TransportHealth() for t in endpoints.keys()}
        if TransportType.LAN_BROADCAST in endpoints:
            active = TransportType.LAN_BROADCAST
        elif TransportType.WIREGUARD in endpoints:
            active = TransportType.WIREGUARD
        elif TransportType.TAILSCALE in endpoints:
            active = TransportType.TAILSCALE
        else:
            active = TransportType.DIRECT_TCP

        return cls(
            node_id=node_id,
            endpoints=endpoints,
            active_transport=active,
            transport_health=health,
            is_lan_partitioned=False,
            last_failover_ms=0,
            last_lan_recovery_start_ms=None,
        )

@dataclass
class HysteresisConfig:
    fail_strikes_threshold: int = 3  # Failover on 3 consecutive missed probes
    recovery_dwell_ms: int = 120_000  # 120s dwell time before recovering LAN
    recovery_strikes_threshold: int = 3  # 3 consecutive successful probes during dwell

@dataclass
class RouteSummary:
    node_id: int
    active_transport: TransportType
    active_endpoint: str
    is_lan_partitioned: bool
    latency_ms: int

class MultiTransportRouter:
    """Multi-transport routing controller with automated WAN overlay failover and anti-flap hysteresis."""

    def __init__(self, config: Optional[HysteresisConfig] = None) -> None:
        self.config = config or HysteresisConfig()
        self._lock = threading.Lock()
        self._peers: Dict[int, PeerRoute] = {}

    def register_peer(self, node_id: int, endpoints: Dict[TransportType, str]) -> None:
        with self._lock:
            self._peers[node_id] = PeerRoute.create(node_id, endpoints)

    def record_heartbeat(
        self,
        node_id: int,
        transport: TransportType,
        latency_ms: int = 0,
        now_ms: Optional[int] = None,
    ) -> None:
        ts = now_ms if now_ms is not None else int(time.time() * 1000)
        with self._lock:
            peer = self._peers.get(node_id)
            if not peer:
                return

            h = peer.transport_health.setdefault(transport, TransportHealth())
            h.consecutive_success += 1
            h.consecutive_misses = 0
            h.last_success_ms = ts
            h.latency_ms = latency_ms
            h.is_healthy = True

            # Anti-flap recovery logic for LAN
            if transport == TransportType.LAN_BROADCAST and peer.is_lan_partitioned:
                if peer.last_lan_recovery_start_ms is None:
                    peer.last_lan_recovery_start_ms = ts

                elapsed_dwell = ts - peer.last_lan_recovery_start_ms
                if (
                    elapsed_dwell >= self.config.recovery_dwell_ms
                    and h.consecutive_success >= self.config.recovery_strikes_threshold
                ):
                    peer.is_lan_partitioned = False
                    peer.active_transport = TransportType.LAN_BROADCAST
                    peer.last_lan_recovery_start_ms = None

    def record_missed_heartbeat(
        self,
        node_id: int,
        transport: TransportType,
        now_ms: Optional[int] = None,
    ) -> None:
        ts = now_ms if now_ms is not None else int(time.time() * 1000)
        with self._lock:
            peer = self._peers.get(node_id)
            if not peer:
                return

            h = peer.transport_health.setdefault(transport, TransportHealth())
            h.consecutive_misses += 1
            h.consecutive_success = 0
            h.last_miss_ms = ts

            if h.consecutive_misses >= self.config.fail_strikes_threshold:
                h.is_healthy = False

                if transport == TransportType.LAN_BROADCAST and not peer.is_lan_partitioned:
                    peer.is_lan_partitioned = True
                    peer.last_failover_ms = ts
                    peer.last_lan_recovery_start_ms = None

                    # Failover order: WireGuard -> Tailscale -> DirectTcp
                    if TransportType.WIREGUARD in peer.endpoints:
                        peer.active_transport = TransportType.WIREGUARD
                    elif TransportType.TAILSCALE in peer.endpoints:
                        peer.active_transport = TransportType.TAILSCALE
                    elif TransportType.DIRECT_TCP in peer.endpoints:
                        peer.active_transport = TransportType.DIRECT_TCP

    def select_route(self, node_id: int) -> Tuple[TransportType, str]:
        with self._lock:
            peer = self._peers.get(node_id)
            if not peer:
                raise KeyError(f"Peer node {node_id} not registered")

            endpoint = peer.endpoints.get(peer.active_transport)
            if not endpoint:
                raise KeyError(
                    f"No endpoint for active transport {peer.active_transport} to node {node_id}"
                )

            return (peer.active_transport, endpoint)

    def is_peer_partitioned(self, node_id: int) -> bool:
        with self._lock:
            peer = self._peers.get(node_id)
            return peer.is_lan_partitioned if peer else False

    def get_route_summary(self, node_id: int) -> Optional[RouteSummary]:
        with self._lock:
            peer = self._peers.get(node_id)
            if not peer:
                return None
            endpoint = peer.endpoints.get(peer.active_transport, "")
            latency = (
                peer.transport_health.get(peer.active_transport, TransportHealth()).latency_ms
            )
            return RouteSummary(
                node_id=node_id,
                active_transport=peer.active_transport,
                active_endpoint=endpoint,
                is_lan_partitioned=peer.is_lan_partitioned,
                latency_ms=latency,
            )
