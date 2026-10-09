// AI-hint: Node heartbeat monitor, 3-strike dead peer detection, and routing table eviction for mios-node.
// AI-related: src/mios-rs/mios-node/src/node.rs, src/mios-rs/mios-node/src/lib.rs, tests/test-node-mesh.py
//! MiOS Node Heartbeat Monitor & Dead-Peer Eviction Engine (task_331 / task_2564)
//!
//! Enforces the 5s heartbeat interval, 3-strike failure rule (15s eviction threshold),
//! degraded status transitions at 2 strikes (10s), lock-free table pruning, and event dispatch.

use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::net::SocketAddr;

pub const DEFAULT_HEARTBEAT_INTERVAL_SECS: u64 = 5;
pub const DEFAULT_DEGRADED_THRESHOLD_SECS: u64 = 10; // 2 strikes (2 * 5s)
pub const DEFAULT_EVICTION_THRESHOLD_SECS: u64 = 15; // 3 strikes (3 * 5s)

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum PeerHealth {
    Healthy,
    Degraded,
    Dead,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct PeerRoutingEntry {
    pub node_id: u32,
    pub addr: SocketAddr,
    pub last_seen_secs: u64,
    pub missed_strikes: u32,
    pub status: PeerHealth,
    pub uptime_secs: u64,
    pub cpu_load_pct: u8,
    pub mem_available_kb: u32,
}

impl PeerRoutingEntry {
    pub fn new(
        node_id: u32,
        addr: SocketAddr,
        last_seen_secs: u64,
        uptime_secs: u64,
        cpu_load_pct: u8,
        mem_available_kb: u32,
    ) -> Self {
        Self {
            node_id,
            addr,
            last_seen_secs,
            missed_strikes: 0,
            status: PeerHealth::Healthy,
            uptime_secs,
            cpu_load_pct,
            mem_available_kb,
        }
    }

    pub fn is_active(&self) -> bool {
        self.status != PeerHealth::Dead
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct EvictionNotice {
    pub node_id: u32,
    pub reason: String,
    pub timestamp_secs: u64,
    pub missed_strikes: u32,
    pub elapsed_secs: u64,
}

#[derive(Debug, Clone, Default, Serialize, Deserialize)]
pub struct HeartbeatSweepResult {
    pub healthy_peers: Vec<u32>,
    pub degraded_peers: Vec<u32>,
    pub evicted_peers: Vec<EvictionNotice>,
}

/// Tracks peer node health and executes 3-strike dead peer routing table evictions.
pub struct HeartbeatMonitor {
    pub local_node_id: u32,
    pub heartbeat_interval_secs: u64,
    pub degraded_threshold_secs: u64,
    pub eviction_threshold_secs: u64,
    pub routing_table: HashMap<u32, PeerRoutingEntry>,
}

impl HeartbeatMonitor {
    pub fn new(local_node_id: u32) -> Self {
        Self {
            local_node_id,
            heartbeat_interval_secs: DEFAULT_HEARTBEAT_INTERVAL_SECS,
            degraded_threshold_secs: DEFAULT_DEGRADED_THRESHOLD_SECS,
            eviction_threshold_secs: DEFAULT_EVICTION_THRESHOLD_SECS,
            routing_table: HashMap::new(),
        }
    }

    /// Parity twin: usr/libexec/mios/node/discovery.py (HeartbeatMonitor.__init__ thresholds)
    pub fn with_thresholds(
        local_node_id: u32,
        heartbeat_interval_secs: u64,
        degraded_threshold_secs: u64,
        eviction_threshold_secs: u64,
    ) -> Self {
        Self {
            local_node_id,
            heartbeat_interval_secs,
            degraded_threshold_secs,
            eviction_threshold_secs,
            routing_table: HashMap::new(),
        }
    }

    /// Records or updates a peer upon receiving a Heartbeat frame (Opcode 0x01).
    /// Resets missed strikes to 0 and marks peer Healthy (re-admitting if previously evicted).
    pub fn record_heartbeat(
        &mut self,
        node_id: u32,
        addr: SocketAddr,
        uptime_secs: u64,
        cpu_load_pct: u8,
        mem_available_kb: u32,
        current_time_secs: u64,
    ) {
        if node_id == self.local_node_id {
            return;
        }

        let entry = self.routing_table.entry(node_id).or_insert_with(|| {
            PeerRoutingEntry::new(
                node_id,
                addr,
                current_time_secs,
                uptime_secs,
                cpu_load_pct,
                mem_available_kb,
            )
        });

        entry.addr = addr;
        entry.last_seen_secs = current_time_secs;
        entry.missed_strikes = 0;
        entry.status = PeerHealth::Healthy;
        entry.uptime_secs = uptime_secs;
        entry.cpu_load_pct = cpu_load_pct;
        entry.mem_available_kb = mem_available_kb;
    }

    /// Records a peer upon receiving an Announce frame (Opcode 0x02).
    /// Parity twin: usr/libexec/mios/node/discovery.py (HeartbeatMonitor.record_announce)
    pub fn record_announce(&mut self, node_id: u32, addr: SocketAddr, current_time_secs: u64) {
        if node_id == self.local_node_id {
            return;
        }

        let entry = self
            .routing_table
            .entry(node_id)
            .or_insert_with(|| PeerRoutingEntry::new(node_id, addr, current_time_secs, 0, 0, 0));

        entry.addr = addr;
        entry.last_seen_secs = current_time_secs;
        entry.missed_strikes = 0;
        entry.status = PeerHealth::Healthy;
    }

    /// Evaluates peer health and strike count given the elapsed seconds since last heartbeat.
    /// Parity twin: usr/libexec/mios/node/discovery.py (HeartbeatMonitor.assess_peer_health)
    pub fn assess_peer_health(&self, elapsed_secs: u64) -> (PeerHealth, u32) {
        Self::calculate_peer_health(
            elapsed_secs,
            self.heartbeat_interval_secs,
            self.degraded_threshold_secs,
            self.eviction_threshold_secs,
        )
    }

    pub fn calculate_peer_health(
        elapsed_secs: u64,
        interval_secs: u64,
        degraded_threshold_secs: u64,
        eviction_threshold_secs: u64,
    ) -> (PeerHealth, u32) {
        let strikes = elapsed_secs.checked_div(interval_secs).unwrap_or(0) as u32;

        if elapsed_secs >= eviction_threshold_secs {
            (PeerHealth::Dead, strikes.max(3))
        } else if elapsed_secs >= degraded_threshold_secs {
            (PeerHealth::Degraded, strikes.max(2))
        } else {
            (PeerHealth::Healthy, strikes)
        }
    }

    /// Periodic sweep of routing table. Transitions degraded peers, evicts dead peers (3 strikes),
    /// and returns sweep telemetry.
    pub fn sweep(&mut self, current_time_secs: u64) -> HeartbeatSweepResult {
        let mut healthy = Vec::new();
        let mut degraded = Vec::new();
        let mut to_evict = Vec::new();

        let interval = self.heartbeat_interval_secs;
        let deg_thresh = self.degraded_threshold_secs;
        let evict_thresh = self.eviction_threshold_secs;

        for (node_id, peer) in self.routing_table.iter_mut() {
            let elapsed = current_time_secs.saturating_sub(peer.last_seen_secs);
            let (health, strikes) =
                Self::calculate_peer_health(elapsed, interval, deg_thresh, evict_thresh);

            peer.missed_strikes = strikes;
            peer.status = health;

            match health {
                PeerHealth::Healthy => healthy.push(*node_id),
                PeerHealth::Degraded => degraded.push(*node_id),
                PeerHealth::Dead => {
                    to_evict.push(EvictionNotice {
                        node_id: *node_id,
                        reason: format!(
                            "3-strike timeout: {}s elapsed without heartbeat (threshold: {}s)",
                            elapsed, evict_thresh
                        ),
                        timestamp_secs: current_time_secs,
                        missed_strikes: strikes,
                        elapsed_secs: elapsed,
                    });
                }
            }
        }

        // Prune dead peers from active routing table
        for notice in &to_evict {
            self.routing_table.remove(&notice.node_id);
        }

        HeartbeatSweepResult {
            healthy_peers: healthy,
            degraded_peers: degraded,
            evicted_peers: to_evict,
        }
    }

    /// Manually evicts a peer from the routing table.
    /// Parity twin: usr/libexec/mios/node/discovery.py (HeartbeatMonitor.evict_peer)
    pub fn evict_peer(
        &mut self,
        node_id: u32,
        reason: &str,
        current_time_secs: u64,
    ) -> Option<EvictionNotice> {
        if let Some(peer) = self.routing_table.remove(&node_id) {
            let elapsed = current_time_secs.saturating_sub(peer.last_seen_secs);
            Some(EvictionNotice {
                node_id,
                reason: reason.to_string(),
                timestamp_secs: current_time_secs,
                missed_strikes: peer.missed_strikes,
                elapsed_secs: elapsed,
            })
        } else {
            None
        }
    }

    pub fn get_peer(&self, node_id: u32) -> Option<&PeerRoutingEntry> {
        self.routing_table.get(&node_id)
    }

    /// Parity twin: usr/libexec/mios/node/discovery.py (HeartbeatMonitor.get_active_peers)
    pub fn active_peers(&self) -> Vec<&PeerRoutingEntry> {
        self.routing_table
            .values()
            .filter(|p| p.is_active())
            .collect()
    }

    pub fn peer_count(&self) -> usize {
        self.routing_table.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_heartbeat_monitor_lifecycle() {
        let mut monitor = HeartbeatMonitor::new(100);
        let peer_addr: SocketAddr = "127.0.0.1:8651".parse().unwrap();

        // 1. Record fresh heartbeat at T=0
        monitor.record_heartbeat(201, peer_addr, 3600, 10, 1024000, 0);
        assert_eq!(monitor.peer_count(), 1);
        let peer = monitor.get_peer(201).unwrap();
        assert_eq!(peer.status, PeerHealth::Healthy);
        assert_eq!(peer.missed_strikes, 0);

        // 2. Sweep at T=5 (1 strike / within interval -> Healthy)
        let sweep1 = monitor.sweep(5);
        assert_eq!(sweep1.healthy_peers, vec![201]);
        assert!(sweep1.degraded_peers.is_empty());
        assert!(sweep1.evicted_peers.is_empty());

        // 3. Sweep at T=10 (2 strikes / 10s threshold -> Degraded)
        let sweep2 = monitor.sweep(10);
        assert!(sweep2.healthy_peers.is_empty());
        assert_eq!(sweep2.degraded_peers, vec![201]);
        assert!(sweep2.evicted_peers.is_empty());

        // 4. Sweep at T=15 (3 strikes / 15s threshold -> Evicted)
        let sweep3 = monitor.sweep(15);
        assert!(sweep3.healthy_peers.is_empty());
        assert!(sweep3.degraded_peers.is_empty());
        assert_eq!(sweep3.evicted_peers.len(), 1);
        assert_eq!(sweep3.evicted_peers[0].node_id, 201);
        assert_eq!(monitor.peer_count(), 0);

        // 5. Re-admission upon receiving fresh heartbeat at T=20
        monitor.record_heartbeat(201, peer_addr, 3620, 12, 1024000, 20);
        assert_eq!(monitor.peer_count(), 1);
        let re_admitted = monitor.get_peer(201).unwrap();
        assert_eq!(re_admitted.status, PeerHealth::Healthy);
        assert_eq!(re_admitted.missed_strikes, 0);
    }
}
