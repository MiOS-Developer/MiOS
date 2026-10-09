// AI-hint: mios-node mesh transports: BLE offline bootstrap and the Tailscale/WireGuard overlay fallback when LAN broadcast is partitioned (T-395).
// AI-related: src/mios-rs/mios-node/src/crypto.rs, usr/libexec/mios/node/ble.py, tests/test-node.py, src/mios-rs/mios-node/src/heartbeat.rs, usr/libexec/mios/node/overlay.py

pub mod ble {
    //! MiOS BLE Beaconing & Offline Local Mesh Bootstrap Engine
    //!
    //! Implements GATT service/characteristic definitions for headless edge blades,
    //! ephemeral X25519 Diffie-Hellman key exchange, HKDF-SHA256 key derivation,
    //! ChaCha20-Poly1305 AEAD encrypted credential provisioning, and mockable hardware adapter.

    use crate::crypto::{
        chacha20_poly1305_decrypt, chacha20_poly1305_encrypt, hkdf_sha256, random_bytes, x25519,
        x25519_public_key,
    };
    use anyhow::{anyhow, Result};
    use byteorder::{BigEndian, ByteOrder};
    use serde::{Deserialize, Serialize};
    use sha2::{Digest, Sha256};
    use std::collections::HashMap;
    use std::sync::{Arc, Mutex};

    pub const BLE_SERVICE_UUID: &str = "4D494F53-0001-1000-8000-00805F9B34FB";
    pub const BLE_CHAR_IDENTITY_UUID: &str = "4D494F53-0002-1000-8000-00805F9B34FB";
    pub const BLE_CHAR_ECDH_UUID: &str = "4D494F53-0003-1000-8000-00805F9B34FB";
    pub const BLE_CHAR_PROVISION_UUID: &str = "4D494F53-0004-1000-8000-00805F9B34FB";

    pub const BLE_HKDF_INFO: &[u8] = b"wifi-provisioning";
    pub const BLE_AEAD_AAD: &[u8] = b"mios-ble-v2";

    /// Bootstrap lifecycle states for headless edge blades.
    #[repr(u8)]
    #[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
    pub enum BleBootstrapState {
        Unprovisioned = 0,
        Handshaking = 1,
        Provisioning = 2,
        Provisioned = 3,
        Failed = 4,
    }

    impl TryFrom<u8> for BleBootstrapState {
        type Error = anyhow::Error;

        fn try_from(v: u8) -> Result<Self> {
            match v {
                0 => Ok(BleBootstrapState::Unprovisioned),
                1 => Ok(BleBootstrapState::Handshaking),
                2 => Ok(BleBootstrapState::Provisioning),
                3 => Ok(BleBootstrapState::Provisioned),
                4 => Ok(BleBootstrapState::Failed),
                _ => Err(anyhow!("Invalid BLE bootstrap state: {}", v)),
            }
        }
    }

    /// Encrypted Wi-Fi and mesh cluster join credentials.
    #[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
    pub struct ProvisioningPayload {
        pub ssid: String,
        pub psk: String,
        pub cluster_token: String,
        pub coordinator_endpoint: String,
        pub mesh_network_key: Option<Vec<u8>>,
        pub timestamp_utc: u64,
    }

    impl ProvisioningPayload {
        pub fn new(
            ssid: String,
            psk: String,
            cluster_token: String,
            coordinator_endpoint: String,
        ) -> Self {
            let now_sec = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0);

            Self {
                ssid,
                psk,
                cluster_token,
                coordinator_endpoint,
                mesh_network_key: None,
                timestamp_utc: now_sec,
            }
        }
    }

    /// Hardware abstraction interface for Bluetooth Low Energy GATT operations.
    pub trait BleAdapter: Send + Sync {
        fn start_advertising(&self, service_uuid: &str, node_id: u32) -> Result<()>;
        fn stop_advertising(&self) -> Result<()>;
        fn is_advertising(&self) -> bool;
        fn set_characteristic_value(&self, char_uuid: &str, data: Vec<u8>) -> Result<()>;
        fn get_characteristic_value(&self, char_uuid: &str) -> Result<Vec<u8>>;
    }

    /// In-memory mock BLE adapter for headless and deterministic CI testing.
    #[derive(Debug, Default)]
    pub struct MockBleAdapter {
        advertising: Arc<Mutex<bool>>,
        characteristics: Arc<Mutex<HashMap<String, Vec<u8>>>>,
    }

    impl MockBleAdapter {
        pub fn new() -> Self {
            Self::default()
        }
    }

    impl BleAdapter for MockBleAdapter {
        fn start_advertising(&self, _service_uuid: &str, _node_id: u32) -> Result<()> {
            let mut adv = self.advertising.lock().unwrap();
            *adv = true;
            Ok(())
        }

        fn stop_advertising(&self) -> Result<()> {
            let mut adv = self.advertising.lock().unwrap();
            *adv = false;
            Ok(())
        }

        fn is_advertising(&self) -> bool {
            *self.advertising.lock().unwrap()
        }

        fn set_characteristic_value(&self, char_uuid: &str, data: Vec<u8>) -> Result<()> {
            let mut map = self.characteristics.lock().unwrap();
            map.insert(char_uuid.to_string(), data);
            Ok(())
        }

        fn get_characteristic_value(&self, char_uuid: &str) -> Result<Vec<u8>> {
            let map = self.characteristics.lock().unwrap();
            map.get(char_uuid)
                .cloned()
                .ok_or_else(|| anyhow!("Characteristic {} not found", char_uuid))
        }
    }

    /// Orchestrator for headless node BLE bootstrap advertising and encrypted provisioning.
    pub struct BleMeshBootstrap {
        pub node_id: u32,
        adapter: Arc<dyn BleAdapter>,
        state: Arc<Mutex<BleBootstrapState>>,
        local_priv_key: [u8; 32],
        local_pub_key: [u8; 32],
        shared_key: Arc<Mutex<Option<[u8; 32]>>>,
        provisioned_credentials: Arc<Mutex<Option<ProvisioningPayload>>>,
    }

    impl BleMeshBootstrap {
        pub fn new(node_id: u32, adapter: Arc<dyn BleAdapter>) -> Result<Self> {
            let seed = random_bytes::<32>()?;

            let pub_key = x25519_public_key(&seed);

            Ok(Self {
                node_id,
                adapter,
                state: Arc::new(Mutex::new(BleBootstrapState::Unprovisioned)),
                local_priv_key: seed,
                local_pub_key: pub_key,
                shared_key: Arc::new(Mutex::new(None)),
                provisioned_credentials: Arc::new(Mutex::new(None)),
            })
        }

        /// Starts advertising GATT service and initializes identity + ECDH characteristics.
        pub fn start(&self) -> Result<()> {
            // 1. Initialize Char 1: Identity (4B node_id + 1B state)
            let mut id_buf = vec![0u8; 5];
            BigEndian::write_u32(&mut id_buf[0..4], self.node_id);
            id_buf[4] = BleBootstrapState::Unprovisioned as u8;
            self.adapter
                .set_characteristic_value(BLE_CHAR_IDENTITY_UUID, id_buf)?;

            // 2. Initialize Char 2: Local X25519 Public Key (32B)
            self.adapter
                .set_characteristic_value(BLE_CHAR_ECDH_UUID, self.local_pub_key.to_vec())?;

            // 3. Start advertising
            self.adapter
                .start_advertising(BLE_SERVICE_UUID, self.node_id)?;
            *self.state.lock().unwrap() = BleBootstrapState::Unprovisioned;

            Ok(())
        }

        /// Handles peer ECDH key exchange write to Characteristic 2.
        pub fn handle_ecdh_exchange(&self, peer_pub_key_bytes: &[u8]) -> Result<()> {
            if peer_pub_key_bytes.len() != 32 {
                return Err(anyhow!(
                    "Invalid X25519 public key length: {}",
                    peer_pub_key_bytes.len()
                ));
            }

            let mut peer_pub = [0u8; 32];
            peer_pub.copy_from_slice(peer_pub_key_bytes);

            // Compute X25519 shared secret
            let shared_secret = x25519(&self.local_priv_key, &peer_pub);

            if shared_secret.iter().all(|b| *b == 0) {
                return Err(anyhow!("non-contributory X25519 peer key"));
            }
            // Bind the key to both fresh public keys in node/provisioner order (RFC 5869 §3.1).
            let salt =
                Sha256::digest([self.local_pub_key.as_slice(), peer_pub.as_slice()].concat());
            let derived_bytes = hkdf_sha256(&salt, &shared_secret, BLE_HKDF_INFO, 32);
            let mut key = [0u8; 32];
            key.copy_from_slice(&derived_bytes[0..32]);

            *self.shared_key.lock().unwrap() = Some(key);
            *self.state.lock().unwrap() = BleBootstrapState::Handshaking;

            // Update Char 1 state
            let mut id_buf = vec![0u8; 5];
            BigEndian::write_u32(&mut id_buf[0..4], self.node_id);
            id_buf[4] = BleBootstrapState::Handshaking as u8;
            self.adapter
                .set_characteristic_value(BLE_CHAR_IDENTITY_UUID, id_buf)?;

            Ok(())
        }

        /// Handles encrypted credential write to Characteristic 3 and completes provisioning.
        pub fn handle_provisioning_write(
            &self,
            encrypted_payload: &[u8],
        ) -> Result<ProvisioningPayload> {
            if encrypted_payload.len() < 12 + crate::crypto::TAG_SIZE {
                return Err(anyhow!(
                    "provisioning packet must contain a nonce and authentication tag"
                ));
            }
            let mut shared = self.shared_key.lock().unwrap();
            let key = shared
                .as_ref()
                .ok_or_else(|| anyhow!("ECDH handshake not completed or already consumed"))?;
            let nonce: &[u8; 12] = encrypted_payload[..12].try_into().expect("length checked");
            let decrypted_bytes =
                chacha20_poly1305_decrypt(key, nonce, BLE_AEAD_AAD, &encrypted_payload[12..])?;
            let creds: ProvisioningPayload = serde_json::from_slice(&decrypted_bytes)?;
            shared.take();

            *self.provisioned_credentials.lock().unwrap() = Some(creds.clone());
            *self.state.lock().unwrap() = BleBootstrapState::Provisioned;

            // Update Char 1 state to Provisioned and stop advertising
            let mut id_buf = vec![0u8; 5];
            BigEndian::write_u32(&mut id_buf[0..4], self.node_id);
            id_buf[4] = BleBootstrapState::Provisioned as u8;
            self.adapter
                .set_characteristic_value(BLE_CHAR_IDENTITY_UUID, id_buf)?;
            self.adapter.stop_advertising()?;

            Ok(creds)
        }

        pub fn state(&self) -> BleBootstrapState {
            *self.state.lock().unwrap()
        }

        /// Parity twin: usr/libexec/mios/node/ble.py (BleProvisioningSession.get_credentials)
        pub fn get_credentials(&self) -> Option<ProvisioningPayload> {
            self.provisioned_credentials.lock().unwrap().clone()
        }

        pub fn local_public_key(&self) -> [u8; 32] {
            self.local_pub_key
        }
    }

    /// Provisioner helper that discovers, handshakes, and securely configures an offline edge blade.
    pub fn provision_remote_node(
        adapter: &dyn BleAdapter,
        payload: &ProvisioningPayload,
    ) -> Result<()> {
        // 1. Read node identity from Char 1
        let id_bytes = adapter.get_characteristic_value(BLE_CHAR_IDENTITY_UUID)?;
        if id_bytes.len() < 5 {
            return Err(anyhow!("Invalid identity characteristic length"));
        }

        // 2. Read node public key from Char 2
        let node_pub_bytes = adapter.get_characteristic_value(BLE_CHAR_ECDH_UUID)?;
        if node_pub_bytes.len() != 32 {
            return Err(anyhow!("Invalid node public key length"));
        }
        let mut node_pub = [0u8; 32];
        node_pub.copy_from_slice(&node_pub_bytes);

        // 3. Generate provisioner ephemeral key
        let priv_key = random_bytes::<32>()?;
        let prov_pub = x25519_public_key(&priv_key);

        // 4. Write provisioner public key to Char 2
        adapter.set_characteristic_value(BLE_CHAR_ECDH_UUID, prov_pub.to_vec())?;

        // 5. Compute shared key
        let ss = x25519(&priv_key, &node_pub);
        if ss.iter().all(|b| *b == 0) {
            return Err(anyhow!("non-contributory X25519 node key"));
        }
        let salt = Sha256::digest([node_pub.as_slice(), prov_pub.as_slice()].concat());
        let derived = hkdf_sha256(&salt, &ss, BLE_HKDF_INFO, 32);
        let mut key = [0u8; 32];
        key.copy_from_slice(&derived[0..32]);

        // 6. Encrypt credentials
        let json_bytes = serde_json::to_vec(payload)?;
        let nonce = random_bytes::<12>()?;
        let mut encrypted = nonce.to_vec();
        encrypted.extend(chacha20_poly1305_encrypt(
            &key,
            &nonce,
            BLE_AEAD_AAD,
            &json_bytes,
        ));

        // 7. Write encrypted payload to Char 3
        adapter.set_characteristic_value(BLE_CHAR_PROVISION_UUID, encrypted)?;

        Ok(())
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn fresh_keys_nonce_packets_tamper_and_replay() {
            let adapter: Arc<dyn BleAdapter> = Arc::new(MockBleAdapter::new());
            let node = BleMeshBootstrap::new(42, Arc::clone(&adapter)).unwrap();
            let other = BleMeshBootstrap::new(42, Arc::new(MockBleAdapter::new())).unwrap();
            assert_ne!(node.local_public_key(), other.local_public_key());
            node.start().unwrap();
            assert!(node.handle_ecdh_exchange(&[0; 32]).is_err());
            assert!(node.handle_provisioning_write(&[0; 27]).is_err());
            let payload = ProvisioningPayload::new(
                "ssid".into(),
                "psk".into(),
                "join".into(),
                "endpoint".into(),
            );
            provision_remote_node(adapter.as_ref(), &payload).unwrap();
            let first_peer = adapter
                .get_characteristic_value(BLE_CHAR_ECDH_UUID)
                .unwrap();
            let first = adapter
                .get_characteristic_value(BLE_CHAR_PROVISION_UUID)
                .unwrap();
            node.start().unwrap();
            provision_remote_node(adapter.as_ref(), &payload).unwrap();
            let second_peer = adapter
                .get_characteristic_value(BLE_CHAR_ECDH_UUID)
                .unwrap();
            let second = adapter
                .get_characteristic_value(BLE_CHAR_PROVISION_UUID)
                .unwrap();
            assert_ne!(first_peer, second_peer, "provisioner keys must be fresh");
            assert_ne!(
                first[..12],
                second[..12],
                "each packet carries a fresh nonce"
            );
            node.handle_ecdh_exchange(&second_peer).unwrap();
            assert!(
                node.handle_provisioning_write(&first).is_err(),
                "a different handshake must not decrypt"
            );
            let mut tampered = second.clone();
            tampered[0] ^= 1;
            assert!(
                node.handle_provisioning_write(&tampered).is_err(),
                "nonce tampering must be authenticated"
            );
            assert_eq!(node.handle_provisioning_write(&second).unwrap(), payload);
            assert!(
                node.handle_provisioning_write(&second).is_err(),
                "a consumed handshake must reject replay"
            );
        }

        #[test]
        fn test_ble_bootstrap_full_encrypted_provisioning_flow() {
            let adapter: Arc<dyn BleAdapter> = Arc::new(MockBleAdapter::new());
            let bootstrap = BleMeshBootstrap::new(42, Arc::clone(&adapter)).unwrap();

            // 1. Start offline node BLE beaconing
            bootstrap.start().unwrap();
            assert!(adapter.is_advertising());
            assert_eq!(bootstrap.state(), BleBootstrapState::Unprovisioned);

            // 2. Provisioner prepares Wi-Fi payload
            let creds = ProvisioningPayload::new(
                "MiOS-Mesh-WiFi".to_string(),
                "Sup3rS3cur3P@ss".to_string(),
                "tok_cluster_9988".to_string(),
                "192.168.1.1:8650".to_string(),
            );

            // 3. Provisioner executes provisioning handshake
            provision_remote_node(adapter.as_ref(), &creds).unwrap();

            // 4. Node processes peer ECDH key from Char 2
            let peer_pub = adapter
                .get_characteristic_value(BLE_CHAR_ECDH_UUID)
                .unwrap();
            bootstrap.handle_ecdh_exchange(&peer_pub).unwrap();
            assert_eq!(bootstrap.state(), BleBootstrapState::Handshaking);

            // 5. Node processes encrypted credentials from Char 3
            let enc_payload = adapter
                .get_characteristic_value(BLE_CHAR_PROVISION_UUID)
                .unwrap();
            let provisioned = bootstrap.handle_provisioning_write(&enc_payload).unwrap();

            assert_eq!(provisioned.ssid, "MiOS-Mesh-WiFi");
            assert_eq!(provisioned.psk, "Sup3rS3cur3P@ss");
            assert_eq!(provisioned.cluster_token, "tok_cluster_9988");
            assert_eq!(bootstrap.state(), BleBootstrapState::Provisioned);
            assert!(!adapter.is_advertising()); // Advertising stopped upon successful bootstrap
        }
    }
}

pub mod overlay {
    //! MiOS Multi-Transport Router & LAN Partition Overlay Failover Engine
    //!
    //! Implements multi-transport routing across Direct LAN, WireGuard overlay, Tailscale mesh,
    //! and Direct TCP, featuring 3-strike LAN partition failure detection and asymmetric
    //! anti-flap recovery hysteresis (120s recovery dwell from `[blade.collapse]`).

    use anyhow::{anyhow, Result};
    use serde::{Deserialize, Serialize};
    use std::collections::HashMap;
    use std::sync::{Arc, Mutex};

    /// Supported network transport types in order of preferred priority.
    #[repr(u8)]
    #[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
    pub enum TransportType {
        LanBroadcast = 1, // Tier 1: Local subnet UDP broadcast / direct TCP (<2ms)
        WireGuard = 2,    // Tier 2: Encrypted kernel WireGuard overlay (<10ms)
        Tailscale = 3,    // Tier 3: Tailscale tailnet mesh / DERP relay (<30ms)
        DirectTcp = 4,    // Tier 4: Fallback direct TCP / remote coordinator (<50ms)
    }

    impl TransportType {
        pub fn as_str(&self) -> &'static str {
            match self {
                TransportType::LanBroadcast => "lan_broadcast",
                TransportType::WireGuard => "wireguard",
                TransportType::Tailscale => "tailscale",
                TransportType::DirectTcp => "direct_tcp",
            }
        }
    }

    /// Dynamic health telemetry for an individual transport link.
    #[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
    pub struct TransportHealth {
        pub consecutive_success: u32,
        pub consecutive_misses: u32,
        pub last_success_ms: u64,
        pub last_miss_ms: u64,
        pub latency_ms: u32,
        pub is_healthy: bool,
    }

    impl Default for TransportHealth {
        fn default() -> Self {
            Self {
                consecutive_success: 0,
                consecutive_misses: 0,
                last_success_ms: 0,
                last_miss_ms: 0,
                latency_ms: 0,
                is_healthy: true,
            }
        }
    }

    /// Routing state and failover tracking for a specific peer node.
    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct PeerRoute {
        pub node_id: u32,
        pub endpoints: HashMap<TransportType, String>,
        pub active_transport: TransportType,
        pub transport_health: HashMap<TransportType, TransportHealth>,
        pub is_lan_partitioned: bool,
        pub last_failover_ms: u64,
        pub last_lan_recovery_start_ms: Option<u64>,
    }

    impl PeerRoute {
        pub fn new(node_id: u32, endpoints: HashMap<TransportType, String>) -> Self {
            let mut health = HashMap::new();
            for &t in endpoints.keys() {
                health.insert(t, TransportHealth::default());
            }

            let active = if endpoints.contains_key(&TransportType::LanBroadcast) {
                TransportType::LanBroadcast
            } else if endpoints.contains_key(&TransportType::WireGuard) {
                TransportType::WireGuard
            } else if endpoints.contains_key(&TransportType::Tailscale) {
                TransportType::Tailscale
            } else {
                TransportType::DirectTcp
            };

            Self {
                node_id,
                endpoints,
                active_transport: active,
                transport_health: health,
                is_lan_partitioned: false,
                last_failover_ms: 0,
                last_lan_recovery_start_ms: None,
            }
        }
    }

    /// Anti-flap hysteresis configuration parameters (aligning with `[blade.collapse]`).
    #[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
    pub struct HysteresisConfig {
        pub fail_strikes_threshold: u32, // Failover after N consecutive missed heartbeats (e.g. 3)
        pub recovery_dwell_ms: u64,      // Revert dwell time in ms (e.g. 120_000 ms = 120s)
        pub recovery_strikes_threshold: u32, // Consecutive healthy probes required during recovery (e.g. 3)
    }

    impl Default for HysteresisConfig {
        fn default() -> Self {
            Self {
                fail_strikes_threshold: 3,
                recovery_dwell_ms: 120_000,
                recovery_strikes_threshold: 3,
            }
        }
    }

    /// Route summary snapshot for peer inspection.
    #[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
    pub struct RouteSummary {
        pub node_id: u32,
        pub active_transport: TransportType,
        pub active_endpoint: String,
        pub is_lan_partitioned: bool,
        pub latency_ms: u32,
    }

    /// Multi-transport routing controller with automated WAN overlay failover.
    pub struct MultiTransportRouter {
        peers: Arc<Mutex<HashMap<u32, PeerRoute>>>,
        config: HysteresisConfig,
    }

    impl MultiTransportRouter {
        pub fn new(config: Option<HysteresisConfig>) -> Self {
            Self {
                peers: Arc::new(Mutex::new(HashMap::new())),
                config: config.unwrap_or_default(),
            }
        }

        /// Registers a peer with its configured network endpoints across transport tiers.
        pub fn register_peer(&self, node_id: u32, endpoints: HashMap<TransportType, String>) {
            let mut map = self.peers.lock().unwrap();
            map.insert(node_id, PeerRoute::new(node_id, endpoints));
        }

        /// Records a successful heartbeat received from a peer on a given transport.
        pub fn record_heartbeat(
            &self,
            node_id: u32,
            transport: TransportType,
            latency_ms: u32,
            now_ms: u64,
        ) {
            let mut map = self.peers.lock().unwrap();
            if let Some(peer) = map.get_mut(&node_id) {
                let h = peer.transport_health.entry(transport).or_default();
                h.consecutive_success += 1;
                h.consecutive_misses = 0;
                h.last_success_ms = now_ms;
                h.latency_ms = latency_ms;
                h.is_healthy = true;

                // Asymmetric Anti-Flap Recovery: If LAN was partitioned and LAN heartbeat recovered
                if transport == TransportType::LanBroadcast && peer.is_lan_partitioned {
                    if peer.last_lan_recovery_start_ms.is_none() {
                        peer.last_lan_recovery_start_ms = Some(now_ms);
                    }

                    let recovery_start = peer.last_lan_recovery_start_ms.unwrap_or(now_ms);
                    let elapsed_dwell = now_ms.saturating_sub(recovery_start);

                    // Both dwell timer AND consecutive success threshold must be satisfied
                    if elapsed_dwell >= self.config.recovery_dwell_ms
                        && h.consecutive_success >= self.config.recovery_strikes_threshold
                    {
                        peer.is_lan_partitioned = false;
                        peer.active_transport = TransportType::LanBroadcast;
                        peer.last_lan_recovery_start_ms = None;
                    }
                }
            }
        }

        /// Records a missed heartbeat or connect failure for a peer on a given transport.
        pub fn record_missed_heartbeat(&self, node_id: u32, transport: TransportType, now_ms: u64) {
            let mut map = self.peers.lock().unwrap();
            if let Some(peer) = map.get_mut(&node_id) {
                let h = peer.transport_health.entry(transport).or_default();
                h.consecutive_misses += 1;
                h.consecutive_success = 0;
                h.last_miss_ms = now_ms;

                if h.consecutive_misses >= self.config.fail_strikes_threshold {
                    h.is_healthy = false;

                    // If LAN failed, trigger automated WAN overlay failover
                    if transport == TransportType::LanBroadcast && !peer.is_lan_partitioned {
                        peer.is_lan_partitioned = true;
                        peer.last_failover_ms = now_ms;
                        peer.last_lan_recovery_start_ms = None;

                        // Select next available transport tier: WireGuard -> Tailscale -> DirectTcp
                        if peer.endpoints.contains_key(&TransportType::WireGuard) {
                            peer.active_transport = TransportType::WireGuard;
                        } else if peer.endpoints.contains_key(&TransportType::Tailscale) {
                            peer.active_transport = TransportType::Tailscale;
                        } else if peer.endpoints.contains_key(&TransportType::DirectTcp) {
                            peer.active_transport = TransportType::DirectTcp;
                        }
                    }
                }
            }
        }

        /// Queries the currently active transport and endpoint for routing frames to a peer.
        pub fn select_route(&self, node_id: u32) -> Result<(TransportType, String)> {
            let map = self.peers.lock().unwrap();
            let peer = map
                .get(&node_id)
                .ok_or_else(|| anyhow!("Peer node {} not registered in router", node_id))?;

            let endpoint = peer
                .endpoints
                .get(&peer.active_transport)
                .cloned()
                .ok_or_else(|| {
                    anyhow!(
                        "No endpoint available for active transport {:?} to node {}",
                        peer.active_transport,
                        node_id
                    )
                })?;

            Ok((peer.active_transport, endpoint))
        }

        pub fn is_peer_partitioned(&self, node_id: u32) -> bool {
            let map = self.peers.lock().unwrap();
            map.get(&node_id).is_some_and(|p| p.is_lan_partitioned)
        }

        pub fn get_route_summary(&self, node_id: u32) -> Option<RouteSummary> {
            let map = self.peers.lock().unwrap();
            let peer = map.get(&node_id)?;
            let endpoint = peer.endpoints.get(&peer.active_transport).cloned()?;
            let latency = peer
                .transport_health
                .get(&peer.active_transport)
                .map_or(0, |h| h.latency_ms);

            Some(RouteSummary {
                node_id,
                active_transport: peer.active_transport,
                active_endpoint: endpoint,
                is_lan_partitioned: peer.is_lan_partitioned,
                latency_ms: latency,
            })
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_lan_partition_failover_to_wireguard() {
            let config = HysteresisConfig {
                fail_strikes_threshold: 3,
                recovery_dwell_ms: 10_000,
                recovery_strikes_threshold: 3,
            };
            let router = MultiTransportRouter::new(Some(config));

            let mut endpoints = HashMap::new();
            endpoints.insert(TransportType::LanBroadcast, "192.168.1.50:8650".to_string());
            endpoints.insert(TransportType::WireGuard, "10.0.0.50:8650".to_string());
            endpoints.insert(TransportType::Tailscale, "100.64.0.50:8650".to_string());

            router.register_peer(201, endpoints);

            // Initial state: LanBroadcast
            let (t1, ep1) = router.select_route(201).unwrap();
            assert_eq!(t1, TransportType::LanBroadcast);
            assert_eq!(ep1, "192.168.1.50:8650");

            // 2 misses on LAN: should still remain LAN
            router.record_missed_heartbeat(201, TransportType::LanBroadcast, 1000);
            router.record_missed_heartbeat(201, TransportType::LanBroadcast, 2000);
            assert!(!router.is_peer_partitioned(201));
            assert_eq!(
                router.select_route(201).unwrap().0,
                TransportType::LanBroadcast
            );

            // 3rd miss on LAN: triggers partition and switches to WireGuard
            router.record_missed_heartbeat(201, TransportType::LanBroadcast, 3000);
            assert!(router.is_peer_partitioned(201));
            let (t2, ep2) = router.select_route(201).unwrap();
            assert_eq!(t2, TransportType::WireGuard);
            assert_eq!(ep2, "10.0.0.50:8650");
        }

        #[test]
        fn test_asymmetric_anti_flap_recovery_dwell() {
            let config = HysteresisConfig {
                fail_strikes_threshold: 3,
                recovery_dwell_ms: 5000, // 5s dwell for test
                recovery_strikes_threshold: 3,
            };
            let router = MultiTransportRouter::new(Some(config));

            let mut endpoints = HashMap::new();
            endpoints.insert(TransportType::LanBroadcast, "192.168.1.50:8650".to_string());
            endpoints.insert(TransportType::Tailscale, "100.64.0.50:8650".to_string());

            router.register_peer(202, endpoints);

            // Fail over to Tailscale
            router.record_missed_heartbeat(202, TransportType::LanBroadcast, 1000);
            router.record_missed_heartbeat(202, TransportType::LanBroadcast, 2000);
            router.record_missed_heartbeat(202, TransportType::LanBroadcast, 3000);
            assert_eq!(
                router.select_route(202).unwrap().0,
                TransportType::Tailscale
            );

            // LAN probes resume at t=4000
            router.record_heartbeat(202, TransportType::LanBroadcast, 2, 4000);
            router.record_heartbeat(202, TransportType::LanBroadcast, 2, 5000);
            router.record_heartbeat(202, TransportType::LanBroadcast, 2, 6000);

            // 3 strikes achieved, but dwell elapsed is only 2000ms (< 5000ms) -> Still Tailscale!
            assert_eq!(
                router.select_route(202).unwrap().0,
                TransportType::Tailscale
            );

            // Probe at t=9500 (dwell elapsed = 5500ms >= 5000ms) -> Restores LAN!
            router.record_heartbeat(202, TransportType::LanBroadcast, 2, 9500);
            assert_eq!(
                router.select_route(202).unwrap().0,
                TransportType::LanBroadcast
            );
            assert!(!router.is_peer_partitioned(202));
        }
    }
}
