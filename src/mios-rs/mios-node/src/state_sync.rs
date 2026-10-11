// AI-hint: CRDT LWW-Element-Set state sync engine with append-log persistence for mios-node.
// AI-related: src/mios-rs/mios-node/src/node.rs
//! MiOS Distributed Lock-Free State Synchronization Engine
//! Implements Last-Write-Wins Element-Set (LWW-Element-Set) CRDT, Vector Clock Causality,
//! and Disk-Backed Persistence (Snapshot & Append-Only Log)

use anyhow::{Context, Result};
use chrono::Utc;
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use std::fs::{self, OpenOptions};
use std::io::Write;
use std::path::Path;

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct VectorClock {
    pub clocks: HashMap<u32, u64>,
}

impl Default for VectorClock {
    fn default() -> Self {
        Self::new()
    }
}

impl VectorClock {
    pub fn new() -> Self {
        Self {
            clocks: HashMap::new(),
        }
    }

    pub fn increment(&mut self, node_id: u32) {
        let counter = self.clocks.entry(node_id).or_insert(0);
        *counter += 1;
    }

    pub fn merge(&mut self, other: &VectorClock) {
        for (&node_id, &remote_clock) in &other.clocks {
            let local_clock = self.clocks.entry(node_id).or_insert(0);
            *local_clock = (*local_clock).max(remote_clock);
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct StateElement {
    pub key: String,
    pub value: Vec<u8>,
    pub timestamp_ns: u64,
    pub originating_node_id: u32,
    pub is_deleted: bool,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct StateStore {
    pub node_id: u32,
    pub vector_clock: VectorClock,
    elements: HashMap<String, StateElement>,
    #[serde(skip)]
    persistence_path: Option<String>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct CompactionConfig {
    pub tombstone_ttl_ns: u64, // default: 24 * 3600 * 1_000_000_000 (24h)
    pub min_compaction_interval_ns: u64, // default: 3600 * 1_000_000_000 (1h)
    pub max_tombstones_threshold: usize, // e.g. 1000 tombstones triggers compaction
}

impl Default for CompactionConfig {
    fn default() -> Self {
        Self {
            tombstone_ttl_ns: 24 * 3600 * 1_000_000_000,
            min_compaction_interval_ns: 3600 * 1_000_000_000,
            max_tombstones_threshold: 1000,
        }
    }
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq, Eq)]
pub struct CompactionStats {
    pub initial_elements: usize,
    pub active_elements: usize,
    pub tombstones_purged: usize,
    pub tombstones_retained: usize,
    pub disk_bytes_reclaimed: u64,
    pub duration_us: u64,
}

impl StateStore {
    pub fn new(node_id: u32) -> Self {
        Self {
            node_id,
            vector_clock: VectorClock::new(),
            elements: HashMap::new(),
            persistence_path: None,
        }
    }

    pub fn with_persistence(node_id: u32, path: impl Into<String>) -> Result<Self> {
        let path_str = path.into();
        let mut store = if Path::new(&path_str).exists() {
            Self::load_from_disk(&path_str, node_id).unwrap_or_else(|_| Self::new(node_id))
        } else {
            Self::new(node_id)
        };

        store.persistence_path = Some(path_str);
        Ok(store)
    }

    pub fn count_tombstones(&self) -> usize {
        self.elements.values().filter(|e| e.is_deleted).count()
    }

    pub fn total_elements_count(&self) -> usize {
        self.elements.len()
    }

    /// Compacts tombstones older than `ttl_ns` relative to `current_time_ns`.
    /// Retains active elements and fresh unexpired tombstones.
    pub fn compact_tombstones(&mut self, current_time_ns: u64, ttl_ns: u64) -> CompactionStats {
        let start = std::time::Instant::now();
        let initial_elements = self.elements.len();

        let mut keys_to_purge = Vec::new();
        let mut active_count = 0;
        let mut tombstones_retained = 0;

        for (key, elem) in &self.elements {
            if elem.is_deleted {
                let age = current_time_ns.saturating_sub(elem.timestamp_ns);
                if age > ttl_ns {
                    keys_to_purge.push(key.clone());
                } else {
                    tombstones_retained += 1;
                }
            } else {
                active_count += 1;
            }
        }

        let tombstones_purged = keys_to_purge.len();
        for key in keys_to_purge {
            self.elements.remove(&key);
        }

        CompactionStats {
            initial_elements,
            active_elements: active_count,
            tombstones_purged,
            tombstones_retained,
            disk_bytes_reclaimed: 0,
            duration_us: start.elapsed().as_micros() as u64,
        }
    }

    /// Compacts in-memory tombstones and flushes clean snapshot to disk while truncating append WAL log.
    pub fn compact_disk_storage(
        &mut self,
        current_time_ns: u64,
        ttl_ns: u64,
    ) -> Result<CompactionStats> {
        let mut stats = self.compact_tombstones(current_time_ns, ttl_ns);

        if let Some(ref path_str) = self.persistence_path {
            let log_path = format!("{}.log", path_str);
            let initial_log_size = fs::metadata(&log_path).map(|m| m.len()).unwrap_or(0);

            // 1. Save clean snapshot
            self.save_to_disk(path_str)?;

            // 2. Truncate log file and write current state elements
            let tmp_log_path = format!("{}.log.tmp", path_str);
            {
                let mut file = OpenOptions::new()
                    .create(true)
                    .write(true)
                    .truncate(true)
                    .open(&tmp_log_path)?;
                for elem in self.elements.values() {
                    let line = serde_json::to_string(elem)?;
                    writeln!(file, "{}", line)?;
                }
            }
            fs::rename(tmp_log_path, &log_path)?;

            let new_log_size = fs::metadata(&log_path).map(|m| m.len()).unwrap_or(0);
            stats.disk_bytes_reclaimed = initial_log_size.saturating_sub(new_log_size);
        }

        Ok(stats)
    }

    pub fn set(&mut self, key: String, value: Vec<u8>) {
        let timestamp_ns = Utc::now().timestamp_nanos_opt().unwrap_or(0) as u64;
        self.vector_clock.increment(self.node_id);

        let elem = StateElement {
            key: key.clone(),
            value,
            timestamp_ns,
            originating_node_id: self.node_id,
            is_deleted: false,
        };

        self.elements.insert(key, elem.clone());
        self.persist_element(&elem);
    }

    pub fn delete(&mut self, key: &str) {
        let timestamp_ns = Utc::now().timestamp_nanos_opt().unwrap_or(0) as u64;
        self.vector_clock.increment(self.node_id);

        let elem = if let Some(elem) = self.elements.get_mut(key) {
            elem.is_deleted = true;
            elem.timestamp_ns = timestamp_ns;
            elem.originating_node_id = self.node_id;
            elem.clone()
        } else {
            let elem = StateElement {
                key: key.to_string(),
                value: Vec::new(),
                timestamp_ns,
                originating_node_id: self.node_id,
                is_deleted: true,
            };
            self.elements.insert(key.to_string(), elem.clone());
            elem
        };

        self.persist_element(&elem);
    }

    pub fn get(&self, key: &str) -> Option<&Vec<u8>> {
        if let Some(elem) = self.elements.get(key) {
            if !elem.is_deleted {
                return Some(&elem.value);
            }
        }
        None
    }

    pub fn merge_element(&mut self, remote_elem: StateElement) -> bool {
        let key = remote_elem.key.clone();

        let updated = match self.elements.get(&key) {
            Some(local_elem) => {
                if remote_elem.timestamp_ns > local_elem.timestamp_ns {
                    self.elements.insert(key, remote_elem.clone());
                    true
                } else if remote_elem.timestamp_ns == local_elem.timestamp_ns {
                    if remote_elem.originating_node_id > local_elem.originating_node_id {
                        self.elements.insert(key, remote_elem.clone());
                        true
                    } else {
                        false
                    }
                } else {
                    false
                }
            }
            None => {
                self.elements.insert(key, remote_elem.clone());
                true
            }
        };

        if updated {
            self.persist_element(&remote_elem);
        }
        updated
    }

    pub fn merge_remote_store(
        &mut self,
        remote_clock: VectorClock,
        remote_elements: Vec<StateElement>,
    ) -> usize {
        self.vector_clock.merge(&remote_clock);
        let mut updated_count = 0;
        for elem in remote_elements {
            if self.merge_element(elem) {
                updated_count += 1;
            }
        }
        updated_count
    }

    /// Everything a peer needs to converge, tombstones included.
    ///
    /// An LWW-Element-Set converges only if the remove-set travels with the
    /// add-set. Replicating `active_elements` instead means a delete is visible
    /// on the node that made it and nowhere else: the peer never hears that the
    /// key died, keeps its own live copy, and the two disagree forever.
    pub fn replicable_elements(&self) -> Vec<StateElement> {
        self.elements.values().cloned().collect()
    }

    /// The live view, for reading and display. NOT for replication.
    pub fn active_elements(&self) -> Vec<StateElement> {
        self.elements
            .values()
            .filter(|e| !e.is_deleted)
            .cloned()
            .collect()
    }

    pub fn save_to_disk(&self, path: &str) -> Result<()> {
        if let Some(parent) = Path::new(path).parent() {
            fs::create_dir_all(parent)?;
        }
        let serialized = serde_json::to_string_pretty(self)?;
        fs::write(path, serialized).context("Failed to write state snapshot to disk")?;
        Ok(())
    }

    pub fn load_from_disk(path: &str, node_id: u32) -> Result<Self> {
        let content = fs::read_to_string(path)?;
        let mut store: StateStore = serde_json::from_str(&content)?;
        store.node_id = node_id;
        Ok(store)
    }

    fn persist_element(&self, elem: &StateElement) {
        if let Some(ref path_str) = self.persistence_path {
            let log_path = format!("{}.log", path_str);
            if let Ok(line) = serde_json::to_string(elem) {
                if let Ok(mut file) = OpenOptions::new().create(true).append(true).open(&log_path) {
                    let _ = writeln!(file, "{}", line);
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::NamedTempFile;

    #[test]
    fn test_crdt_lww_convergence() {
        let mut node_a = StateStore::new(1);
        let mut node_b = StateStore::new(2);

        node_a.set("config.timeout".to_string(), b"30s".to_vec());

        node_b.merge_remote_store(node_a.vector_clock.clone(), node_a.replicable_elements());
        assert_eq!(node_b.get("config.timeout"), Some(&b"30s".to_vec()));

        node_b.set("config.timeout".to_string(), b"60s".to_vec());
        node_a.merge_remote_store(node_b.vector_clock.clone(), node_b.replicable_elements());
        assert_eq!(node_a.get("config.timeout"), Some(&b"60s".to_vec()));
    }

    #[test]
    fn test_crdt_tombstone_deletion() {
        let mut store = StateStore::new(101);
        store.set("temp.key".to_string(), b"val".to_vec());
        assert_eq!(store.get("temp.key"), Some(&b"val".to_vec()));

        store.delete("temp.key");
        assert_eq!(store.get("temp.key"), None);
        assert_eq!(store.active_elements().len(), 0);
    }

    #[test]
    fn test_state_store_with_persistence() {
        let tmp = NamedTempFile::new().unwrap();
        let path = tmp.path().to_str().unwrap().to_string();

        let mut store = StateStore::with_persistence(101, &path).unwrap();
        store.set("persistent.key".to_string(), b"data".to_vec());
        store.save_to_disk(&path).unwrap();

        let loaded = StateStore::load_from_disk(&path, 101).unwrap();
        assert_eq!(loaded.get("persistent.key"), Some(&b"data".to_vec()));
    }

    #[test]
    fn test_crdt_tombstone_compaction_ttl() {
        let mut store = StateStore::new(101);

        // 1. Active key: should never be purged
        store.set("active.key".to_string(), b"live_value".to_vec());

        // 2. Fresh tombstone: deleted at t = 1000s
        store.set("fresh.deleted".to_string(), b"val".to_vec());
        store.delete("fresh.deleted");
        if let Some(elem) = store.elements.get_mut("fresh.deleted") {
            elem.timestamp_ns = 1_000_000_000_000; // 1000s
        }

        // 3. Stale tombstone: deleted at t = 100s
        store.set("stale.deleted".to_string(), b"val".to_vec());
        store.delete("stale.deleted");
        if let Some(elem) = store.elements.get_mut("stale.deleted") {
            elem.timestamp_ns = 100_000_000_000; // 100s
        }

        assert_eq!(store.total_elements_count(), 3);
        assert_eq!(store.count_tombstones(), 2);

        // Run compaction at current_time = 1050s with TTL = 200s
        // Stale tombstone age = 1050 - 100 = 950s > 200s -> purged
        // Fresh tombstone age = 1050 - 1000 = 50s <= 200s -> retained
        let current_time_ns = 1_050_000_000_000;
        let ttl_ns = 200_000_000_000;

        let stats = store.compact_tombstones(current_time_ns, ttl_ns);
        assert_eq!(stats.initial_elements, 3);
        assert_eq!(stats.active_elements, 1);
        assert_eq!(stats.tombstones_purged, 1);
        assert_eq!(stats.tombstones_retained, 1);

        // Verify remaining store contents
        assert_eq!(store.total_elements_count(), 2);
        assert_eq!(store.get("active.key"), Some(&b"live_value".to_vec()));
        assert!(store.elements.contains_key("fresh.deleted"));
        assert!(!store.elements.contains_key("stale.deleted"));
    }

    #[test]
    fn test_crdt_compact_disk_storage() {
        let tmp = NamedTempFile::new().unwrap();
        let path = tmp.path().to_str().unwrap().to_string();

        let mut store = StateStore::with_persistence(101, &path).unwrap();
        store.set("k1".to_string(), b"v1".to_vec());
        store.set("k2".to_string(), b"v2".to_vec());
        store.delete("k2");

        if let Some(elem) = store.elements.get_mut("k2") {
            elem.timestamp_ns = 10_000_000_000; // old timestamp
        }

        let current_time_ns = 1_000_000_000_000;
        let ttl_ns = 100_000_000_000;

        let stats = store.compact_disk_storage(current_time_ns, ttl_ns).unwrap();
        assert_eq!(stats.tombstones_purged, 1);
        assert_eq!(store.total_elements_count(), 1);

        let reloaded = StateStore::load_from_disk(&path, 101).unwrap();
        assert_eq!(reloaded.get("k1"), Some(&b"v1".to_vec()));
        assert_eq!(reloaded.total_elements_count(), 1);
    }

    #[test]
    fn test_crdt_multi_peer_partition_reorder_convergence() {
        // 3-peer topology: Node A (1), Node B (2), Node C (3)
        let mut node_a = StateStore::new(1);
        let mut node_b = StateStore::new(2);
        let mut node_c = StateStore::new(3);

        // 1. Initial convergence: Node A creates "service.status" = "running", syncs to B and C
        node_a.set("service.status".to_string(), b"running".to_vec());
        node_b.merge_remote_store(node_a.vector_clock.clone(), node_a.replicable_elements());
        node_c.merge_remote_store(node_a.vector_clock.clone(), node_a.replicable_elements());

        assert_eq!(node_a.get("service.status"), Some(&b"running".to_vec()));
        assert_eq!(node_b.get("service.status"), Some(&b"running".to_vec()));
        assert_eq!(node_c.get("service.status"), Some(&b"running".to_vec()));

        // Capture a stale out-of-order packet from Node A before deletion
        let stale_packet = (node_a.vector_clock.clone(), node_a.replicable_elements());

        // 2. Partition phase: Node C is partitioned from Node A and Node B.
        // Node B deletes "service.status", producing a tombstone with timestamp > initial
        std::thread::sleep(std::time::Duration::from_millis(5));
        node_b.delete("service.status");
        assert_eq!(node_b.get("service.status"), None);

        // Node B adds a new active key
        node_b.set("node_b.alive".to_string(), b"true".to_vec());

        // Node C mutates its local state while partitioned
        node_c.set("node_c.counter".to_string(), b"1".to_vec());

        // Node A and B sync while C is partitioned; Node A learns of the deletion
        node_a.merge_remote_store(node_b.vector_clock.clone(), node_b.replicable_elements());
        assert_eq!(node_a.get("service.status"), None);

        // Node A updates "cluster.epoch" to "2"
        std::thread::sleep(std::time::Duration::from_millis(5));
        node_a.set("cluster.epoch".to_string(), b"2".to_vec());
        node_b.merge_remote_store(node_a.vector_clock.clone(), node_a.replicable_elements());

        // 3. Partition heals: packets delivered with reordering, duplicate deliveries, and stale packets.
        // Step A: Node B transmits state (with tombstone) to Node C
        node_c.merge_remote_store(node_b.vector_clock.clone(), node_b.replicable_elements());
        // Step B: Duplicate delivery of Node B's packet to Node C
        node_c.merge_remote_store(node_b.vector_clock.clone(), node_b.replicable_elements());

        // Step C: Stale delayed packet from Node A (captured before delete) arrives at Node C out-of-order
        node_c.merge_remote_store(stale_packet.0.clone(), stale_packet.1.clone());
        // Step D: Duplicate delivery of the stale delayed packet
        node_c.merge_remote_store(stale_packet.0, stale_packet.1);

        // Step E: Node A transmits latest state (with epoch 2) to Node C
        node_c.merge_remote_store(node_a.vector_clock.clone(), node_a.replicable_elements());

        // Step F: Node C syncs its state back to Node A and Node B
        node_a.merge_remote_store(node_c.vector_clock.clone(), node_c.replicable_elements());
        node_b.merge_remote_store(node_c.vector_clock.clone(), node_c.replicable_elements());

        // 4. Convergence verification:
        // All 3 peers must converge on deletion (tombstone preserved, no resurrection)
        assert_eq!(node_a.get("service.status"), None);
        assert_eq!(node_b.get("service.status"), None);
        assert_eq!(node_c.get("service.status"), None);

        // All 3 peers must have identical active state
        assert_eq!(node_a.get("cluster.epoch"), Some(&b"2".to_vec()));
        assert_eq!(node_b.get("cluster.epoch"), Some(&b"2".to_vec()));
        assert_eq!(node_c.get("cluster.epoch"), Some(&b"2".to_vec()));

        assert_eq!(node_a.get("node_b.alive"), Some(&b"true".to_vec()));
        assert_eq!(node_b.get("node_b.alive"), Some(&b"true".to_vec()));
        assert_eq!(node_c.get("node_b.alive"), Some(&b"true".to_vec()));

        assert_eq!(node_a.get("node_c.counter"), Some(&b"1".to_vec()));
        assert_eq!(node_b.get("node_c.counter"), Some(&b"1".to_vec()));
        assert_eq!(node_c.get("node_c.counter"), Some(&b"1".to_vec()));

        // Active element counts must match across all 3 nodes
        assert_eq!(node_a.active_elements().len(), 3);
        assert_eq!(node_b.active_elements().len(), 3);
        assert_eq!(node_c.active_elements().len(), 3);

        // Tombstone must be present in all 3 stores
        assert!(node_a.elements.get("service.status").unwrap().is_deleted);
        assert!(node_b.elements.get("service.status").unwrap().is_deleted);
        assert!(node_c.elements.get("service.status").unwrap().is_deleted);
    }

    #[test]
    fn test_crdt_active_elements_dropping_tombstones_fails_convergence() {
        // Negative control: demonstrates that using active_elements() instead of
        // replicable_elements() drops tombstones, causing the deleted key to persist
        // on the partitioned peer and failing convergence.
        let mut node_a = StateStore::new(1);
        let mut node_b = StateStore::new(2);
        let mut node_c = StateStore::new(3);

        // Initial sync
        node_a.set("resurrect.target".to_string(), b"val".to_vec());
        node_b.merge_remote_store(node_a.vector_clock.clone(), node_a.replicable_elements());
        node_c.merge_remote_store(node_a.vector_clock.clone(), node_a.replicable_elements());

        // Partition Node C; Node B deletes the key
        std::thread::sleep(std::time::Duration::from_millis(5));
        node_b.delete("resurrect.target");

        // Defective replication: sending only active_elements() (dropping tombstones)
        node_c.merge_remote_store(node_b.vector_clock.clone(), node_b.active_elements());

        // Node C NEVER learned of the tombstone because active_elements dropped it!
        // The key is resurrected/persists on Node C instead of being None.
        assert_eq!(node_c.get("resurrect.target"), Some(&b"val".to_vec()));
        assert_ne!(
            node_c.get("resurrect.target"),
            node_b.get("resurrect.target")
        );
    }
}
