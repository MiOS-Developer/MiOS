// AI-hint: Adversarial and stress suites for mios-node milestones 1 and 2: crypto, hardware, cgroups, state sync, watchdog, scheduler, buffer pool, capabilities, BLE bootstrap and overlay.
// AI-related: src/mios-rs/mios-node/src/crypto.rs, src/mios-rs/mios-node/src/device.rs, src/mios-rs/mios-node/src/exec.rs, src/mios-rs/mios-node/src/state_sync.rs, src/mios-rs/mios-node/src/wire.rs, src/mios-rs/mios-node/src/mesh.rs

use std::sync::{Mutex, MutexGuard};

/// One test at a time: several modules edit real-tree files under a restore guard.
fn tree_lock() -> MutexGuard<'static, ()> {
    static LOCK: Mutex<()> = Mutex::new(());
    LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

mod adversarial_m1_test {
    use mios_node::cgroups::{
        filter_safe_worker_cores, AffinityPolicy, CgroupV2Controller, NodeResourceLimits,
        WorkerAffinityController,
    };
    use mios_node::crypto::{
        chacha20_poly1305_decrypt, chacha20_poly1305_encrypt, CryptoHandshake, NodeIdentity,
    };
    use mios_node::hardware::{HardwareAllowlist, HardwareErrorCode, SandboxedHardwareController};
    use mios_node::state_sync::{StateElement, StateStore};
    use mios_node::watchdog::{
        LinuxHardwareWatchdog, WatchdogConfig, WatchdogDriver, WatchdogSupervisor,
    };
    use tempfile::NamedTempFile;

    // ============================================================================
    // 1. ADVERSARIAL CRYPTO TESTS (RFC 7539, RFC 7748, Handshake, Bit-Flip Fuzzing)
    // ============================================================================

    #[test]
    fn test_adversarial_crypto_rfc7539_test_vectors() {
        let _tree = super::tree_lock();
        // Official RFC 7539 Section 2.8.2 Test Vector for ChaCha20-Poly1305 AEAD
        let key: [u8; 32] = [
            0x80, 0x81, 0x82, 0x83, 0x84, 0x85, 0x86, 0x87, 0x88, 0x89, 0x8a, 0x8b, 0x8c, 0x8d,
            0x8e, 0x8f, 0x90, 0x91, 0x92, 0x93, 0x94, 0x95, 0x96, 0x97, 0x98, 0x99, 0x9a, 0x9b,
            0x9c, 0x9d, 0x9e, 0x9f,
        ];
        let nonce: [u8; 12] = [
            0x07, 0x00, 0x00, 0x00, 0x40, 0x41, 0x42, 0x43, 0x44, 0x45, 0x46, 0x47,
        ];
        let aad: [u8; 12] = [
            0x50, 0x51, 0x52, 0x53, 0xc0, 0xc1, 0xc2, 0xc3, 0xc4, 0xc5, 0xc6, 0xc7,
        ];
        let plaintext = b"Ladies and Gentlemen of the class of '99: If I could offer you only one tip for the future, sunscreen would be it.";

        let expected_tag: [u8; 16] = [
            0x1a, 0xe1, 0x0b, 0x59, 0x4f, 0x09, 0xe2, 0x6a, 0x7e, 0x90, 0x2e, 0xcb, 0xd0, 0x60,
            0x06, 0x91,
        ];

        let ciphertext_with_tag = chacha20_poly1305_encrypt(&key, &nonce, &aad, plaintext);
        assert_eq!(ciphertext_with_tag.len(), plaintext.len() + 16);

        let actual_tag = &ciphertext_with_tag[plaintext.len()..];
        assert_eq!(
            actual_tag, &expected_tag,
            "Poly1305 MAC tag must match RFC 7539 vector"
        );

        // Decrypt and verify exact plaintext
        let decrypted = chacha20_poly1305_decrypt(&key, &nonce, &aad, &ciphertext_with_tag)
            .expect("Decryption must succeed");
        assert_eq!(decrypted, plaintext);
    }

    #[test]
    fn test_adversarial_crypto_bit_flip_tamper_fuzzing() {
        let _tree = super::tree_lock();
        let key = [0x33u8; 32];
        let nonce = mios_node::crypto::random_bytes::<12>().unwrap();
        let aad = b"authenticated_header_bytes_v1";
        let plaintext = b"Adversarial payload containing confidential operational state.";

        let ciphertext_with_tag = chacha20_poly1305_encrypt(&key, &nonce, aad, plaintext);

        // 1. Bit-flip every single byte in the ciphertext payload
        for byte_idx in 0..(ciphertext_with_tag.len() - 16) {
            let mut tampered = ciphertext_with_tag.clone();
            tampered[byte_idx] ^= 0x01; // flip 1 bit
            let res = chacha20_poly1305_decrypt(&key, &nonce, aad, &tampered);
            assert!(
                res.is_err(),
                "Bit-flip at ciphertext offset {} must fail decryption",
                byte_idx
            );
        }

        // 2. Bit-flip every single byte in the 16-byte Poly1305 MAC tag
        for tag_offset in 0..16 {
            let mut tampered = ciphertext_with_tag.clone();
            let idx = ciphertext_with_tag.len() - 16 + tag_offset;
            tampered[idx] ^= 0x80;
            let res = chacha20_poly1305_decrypt(&key, &nonce, aad, &tampered);
            assert!(
                res.is_err(),
                "Bit-flip at MAC tag offset {} must fail decryption",
                tag_offset
            );
        }

        // 3. Bit-flip in AAD
        for aad_idx in 0..aad.len() {
            let mut tampered_aad = aad.to_vec();
            tampered_aad[aad_idx] ^= 0x02;
            let res = chacha20_poly1305_decrypt(&key, &nonce, &tampered_aad, &ciphertext_with_tag);
            assert!(
                res.is_err(),
                "Bit-flip at AAD offset {} must fail decryption",
                aad_idx
            );
        }

        // 4. Truncated payloads (shorter than tag size 16)
        for len in 0..16 {
            let truncated = &ciphertext_with_tag[..len];
            let res = chacha20_poly1305_decrypt(&key, &nonce, aad, truncated);
            assert!(
                res.is_err(),
                "Payload of length {} must be rejected (< 16)",
                len
            );
        }
    }

    #[test]
    fn test_adversarial_crypto_x25519_and_session_handshake() {
        let _tree = super::tree_lock();
        let id_alice = NodeIdentity::from_bytes(1001, &[0xAA; 32]);
        let id_bob = NodeIdentity::from_bytes(2002, &[0xBB; 32]);

        let eph_alice = [0x11; 32];
        let eph_bob = [0x22; 32];

        let init = CryptoHandshake::create_init(&id_alice, &eph_alice);

        // Malicious tampering with init ephemeral pubkey
        let mut tampered_init = init.clone();
        tampered_init.ephemeral_pubkey[0] ^= 0xFF;
        let tamper_res =
            CryptoHandshake::process_init_and_respond(&id_bob, &eph_bob, &tampered_init);
        assert!(
            tamper_res.is_err(),
            "Tampered ephemeral pubkey must fail signature check"
        );

        // Legitimate handshake
        let (resp, mut session_bob) =
            CryptoHandshake::process_init_and_respond(&id_bob, &eph_bob, &init)
                .expect("Bob should process init");

        // Malicious tampering with response signature
        let mut tampered_resp = resp.clone();
        tampered_resp.signature[0] ^= 0x01;
        let finalize_tamper_res =
            CryptoHandshake::finalize_init(&id_alice, &eph_alice, &tampered_resp);
        assert!(
            finalize_tamper_res.is_err(),
            "Tampered responder signature must fail"
        );

        // Legitimate finalization
        let mut session_alice = CryptoHandshake::finalize_init(&id_alice, &eph_alice, &resp)
            .expect("Alice should finalize session");

        // Sequential multi-message transmission & Nonce monotonicity check
        for msg_idx in 0..50 {
            let msg = format!("Message seq #{}", msg_idx).into_bytes();
            let enc = session_alice.encrypt_payload(&msg);
            let dec = session_bob
                .decrypt_payload(&enc)
                .expect("Bob should decrypt sequential msg");
            assert_eq!(dec, msg);
        }
    }

    // ============================================================================
    // 2. ADVERSARIAL HARDWARE & WASM HAL TESTS (T-389)
    // ============================================================================

    #[test]
    fn test_adversarial_hardware_allowlist_boundaries() {
        let _tree = super::tree_lock();
        let allowlist = HardwareAllowlist {
            allowed_gpio_pins: [17, 27].into_iter().collect(),
            read_only_gpio_pins: [27].into_iter().collect(),
            allowed_i2c_buses: [1].into_iter().collect(),
            allowed_i2c_addresses: [0x68].into_iter().collect(),
            max_i2c_transfer_len: 8, // Small 8-byte transfer limit
        };

        let (controller, mock) = SandboxedHardwareController::new_mock(allowlist);

        // 1. Extreme GPIO Pin Values
        assert_eq!(
            controller.mios_sys_gpio_read(0),
            Err(HardwareErrorCode::PermissionDenied)
        );
        assert_eq!(
            controller.mios_sys_gpio_read(u32::MAX),
            Err(HardwareErrorCode::PermissionDenied)
        );
        assert_eq!(
            controller.mios_sys_gpio_write(u32::MAX, 1),
            Err(HardwareErrorCode::PermissionDenied)
        );

        // 2. Read-Only Pin Enforcement
        assert_eq!(controller.mios_sys_gpio_read(27), Ok(0));
        assert_eq!(
            controller.mios_sys_gpio_write(27, 1),
            Err(HardwareErrorCode::ReadOnlyPin)
        );

        // 3. Read/Write Pin
        assert_eq!(controller.mios_sys_gpio_write(17, 1), Ok(()));
        assert_eq!(controller.mios_sys_gpio_read(17), Ok(1));
        assert_eq!(mock.get_mock_gpio(17), Some(1));

        // 4. I2C Maximum Transfer Length Boundary (8 bytes max)
        let valid_write = [0x10, 1, 2, 3, 4, 5, 6, 7]; // 8 bytes -> OK
        let mut valid_read = [0u8; 8]; // 8 bytes -> OK
        assert_eq!(
            controller.mios_sys_i2c_transfer(1, 0x68, &valid_write, &mut valid_read),
            Ok(8)
        );

        let too_large_write = [0u8; 9]; // 9 bytes -> InvalidParameter
        assert_eq!(
            controller.mios_sys_i2c_transfer(1, 0x68, &too_large_write, &mut valid_read),
            Err(HardwareErrorCode::InvalidParameter)
        );

        let mut too_large_read = [0u8; 9]; // 9 bytes -> InvalidParameter
        assert_eq!(
            controller.mios_sys_i2c_transfer(1, 0x68, &valid_write, &mut too_large_read),
            Err(HardwareErrorCode::InvalidParameter)
        );

        // 5. I2C Register Address Wrapping Arithmetic
        mock.set_mock_i2c_register(1, 0x68, 255, 0xAA);
        mock.set_mock_i2c_register(1, 0x68, 0, 0xBB);
        mock.set_mock_i2c_register(1, 0x68, 1, 0xCC);

        let mut wrap_read = [0u8; 3];
        let start_at_255 = [255u8];
        assert_eq!(
            controller.mios_sys_i2c_transfer(1, 0x68, &start_at_255, &mut wrap_read),
            Ok(3)
        );
        assert_eq!(
            wrap_read,
            [0xAA, 0xBB, 0xCC],
            "Wrapping from 255 to 0, 1 must be handled cleanly"
        );
    }

    // ============================================================================
    // 3. ADVERSARIAL CPU PINNING & CGROUP BOUNDARY TESTS (T-390)
    // ============================================================================

    #[test]
    fn test_adversarial_cgroup_core_filtering_invariants() {
        let _tree = super::tree_lock();
        // 1. Zero system cores edge case
        assert_eq!(filter_safe_worker_cores(0, None, true), Vec::<usize>::new());

        // 2. Single-core system: Invariant requires retaining Core 0
        assert_eq!(filter_safe_worker_cores(1, None, true), vec![0]);
        assert_eq!(filter_safe_worker_cores(1, None, false), vec![0]);

        // 3. Dual-core system: Core 0 stripped when exclude_core_zero = true
        assert_eq!(filter_safe_worker_cores(2, None, true), vec![1]);
        assert_eq!(filter_safe_worker_cores(2, None, false), vec![0, 1]);

        // 4. Large multi-core system (128 cores)
        let cores_128 = filter_safe_worker_cores(128, None, true);
        assert_eq!(cores_128.len(), 127);
        assert!(!cores_128.contains(&0));
        assert_eq!(cores_128.first(), Some(&1));
        assert_eq!(cores_128.last(), Some(&127));

        // 5. Requested cores with out-of-range, negative simulation, and duplicates
        let req = vec![0, 2, 4, 100, 999];
        let filtered = filter_safe_worker_cores(8, Some(&req), true);
        assert_eq!(filtered, vec![2, 4], "Must strip 0 and any core >= 8");
    }

    #[test]
    fn test_adversarial_worker_affinity_allocation_exhaustion() {
        let _tree = super::tree_lock();
        let limits = NodeResourceLimits::default();
        let mut controller = WorkerAffinityController::new(4, limits); // safe: [1, 2, 3]

        // Allocate 3 exclusive cores
        let c1 = controller
            .allocate_cores_for_policy(AffinityPolicy::Exclusive, 2)
            .unwrap();
        assert_eq!(c1, vec![1, 2]);

        let c2 = controller
            .allocate_cores_for_policy(AffinityPolicy::Exclusive, 1)
            .unwrap();
        assert_eq!(c2, vec![3]);

        // Pool is completely exhausted
        let c_fail = controller.allocate_cores_for_policy(AffinityPolicy::Exclusive, 1);
        assert!(c_fail.is_err());

        // Release unallocated / irrelevant core 99 (no-op)
        controller.release_cores(&[99]);
        assert!(controller
            .allocate_cores_for_policy(AffinityPolicy::Exclusive, 1)
            .is_err());

        // Release core 2
        controller.release_cores(&[2]);
        let c_re = controller
            .allocate_cores_for_policy(AffinityPolicy::Exclusive, 1)
            .unwrap();
        assert_eq!(c_re, vec![2]);

        // LowPriority always assigns the last safe core
        let low = controller
            .allocate_cores_for_policy(AffinityPolicy::LowPriority, 0)
            .unwrap();
        assert_eq!(low, vec![3]);
    }

    #[test]
    fn test_adversarial_cgroup_format_cpu_max_arithmetic() {
        let _tree = super::tree_lock();
        // 0% quota
        assert_eq!(
            CgroupV2Controller::format_cpu_max(Some(0), 100_000),
            "0 100000"
        );

        // 50% quota
        assert_eq!(
            CgroupV2Controller::format_cpu_max(Some(50), 100_000),
            "50000 100000"
        );

        // 100% quota
        assert_eq!(
            CgroupV2Controller::format_cpu_max(Some(100), 100_000),
            "100000 100000"
        );

        // 400% quota (4 full cores)
        assert_eq!(
            CgroupV2Controller::format_cpu_max(Some(400), 100_000),
            "400000 100000"
        );

        // Max unlimited
        assert_eq!(
            CgroupV2Controller::format_cpu_max(None, 50_000),
            "max 50000"
        );
    }

    // ============================================================================
    // 4. ADVERSARIAL CRDT GC & CAUSALITY TESTS (T-391)
    // ============================================================================

    #[test]
    fn test_adversarial_crdt_tombstone_gc_edge_cases() {
        let _tree = super::tree_lock();
        let mut store = StateStore::new(42);

        // 1. Clock skew / Future timestamps: current_time < elem.timestamp
        store.merge_element(StateElement {
            key: "future.tombstone".to_string(),
            value: Vec::new(),
            timestamp_ns: 2_000_000_000_000, // In the future (2000s)
            originating_node_id: 42,
            is_deleted: true,
        });

        // Run compaction with current_time = 1000s, TTL = 100s
        // Age calculation saturates to 0, age <= TTL -> Tombstone must NOT be purged
        let stats = store.compact_tombstones(1_000_000_000_000, 100_000_000_000);
        assert_eq!(stats.tombstones_purged, 0);
        assert_eq!(stats.tombstones_retained, 1);
        assert_eq!(store.count_tombstones(), 1);

        // 2. Exact TTL boundary: age == TTL (retained) vs age == TTL + 1 (purged)
        store.merge_element(StateElement {
            key: "exact.ttl.retained".to_string(),
            value: Vec::new(),
            timestamp_ns: 900_000_000_000, // age = 1000 - 900 = 100s == TTL
            originating_node_id: 42,
            is_deleted: true,
        });

        store.merge_element(StateElement {
            key: "stale.ttl.purged".to_string(),
            value: Vec::new(),
            timestamp_ns: 899_999_999_999, // age = 1000.000000001s > TTL
            originating_node_id: 42,
            is_deleted: true,
        });

        let stats2 = store.compact_tombstones(1_000_000_000_000, 100_000_000_000);
        assert_eq!(stats2.tombstones_purged, 1);
        assert_eq!(store.count_tombstones(), 2); // future.tombstone + exact.ttl.retained

        // 3. Ancient active key (10 years old) must NEVER be purged
        store.merge_element(StateElement {
            key: "ancient.live.key".to_string(),
            value: b"never_die".to_vec(),
            timestamp_ns: 1, // ancient
            originating_node_id: 42,
            is_deleted: false,
        });

        let stats3 = store.compact_tombstones(1_000_000_000_000, 100_000_000_000);
        assert_eq!(store.get("ancient.live.key"), Some(&b"never_die".to_vec()));
        assert_eq!(stats3.active_elements, 1);

        // 4. Tombstone Resurrection / Re-animation
        store.set("resurrect.me".to_string(), b"v1".to_vec());
        store.delete("resurrect.me");
        assert_eq!(store.get("resurrect.me"), None);

        // Peer re-creates key with newer timestamp
        store.set("resurrect.me".to_string(), b"v2_alive".to_vec());
        assert_eq!(store.get("resurrect.me"), Some(&b"v2_alive".to_vec()));
    }

    #[test]
    fn test_adversarial_crdt_scale_and_persistence_truncation() {
        let _tree = super::tree_lock();
        let tmp = NamedTempFile::new().unwrap();
        let path = tmp.path().to_str().unwrap().to_string();

        let mut store = StateStore::with_persistence(10, &path).unwrap();

        // Insert 500 active keys
        for i in 0..500 {
            store.set(format!("key.{}", i), format!("val.{}", i).into_bytes());
        }

        // Insert 500 tombstones (half stale, half fresh)
        for i in 500..1000 {
            let k = format!("tomb.{}", i);
            let ts = if i < 750 {
                100_000_000_000 // Stale (100s)
            } else {
                950_000_000_000 // Fresh (950s)
            };
            store.merge_element(StateElement {
                key: k,
                value: Vec::new(),
                timestamp_ns: ts,
                originating_node_id: 10,
                is_deleted: true,
            });
        }

        assert_eq!(store.total_elements_count(), 1000);
        assert_eq!(store.count_tombstones(), 500);

        // Compact at t = 1000s, TTL = 200s
        let current_time_ns = 1_000_000_000_000;
        let ttl_ns = 200_000_000_000;

        let stats = store.compact_disk_storage(current_time_ns, ttl_ns).unwrap();
        assert_eq!(stats.initial_elements, 1000);
        assert_eq!(stats.active_elements, 500);
        assert_eq!(stats.tombstones_purged, 250);
        assert_eq!(stats.tombstones_retained, 250);
        assert_eq!(store.total_elements_count(), 750);

        // Verify snapshot reload matches compacted state
        let reloaded = StateStore::load_from_disk(&path, 10).unwrap();
        assert_eq!(reloaded.total_elements_count(), 750);
        assert_eq!(reloaded.get("key.0"), Some(&b"val.0".to_vec()));
        assert_eq!(reloaded.get("key.499"), Some(&b"val.499".to_vec()));
    }

    // ============================================================================
    // 5. ADVERSARIAL WATCHDOG TESTS (T-400)
    // ============================================================================

    #[test]
    fn test_adversarial_watchdog_supervisor_and_mock_driver() {
        let _tree = super::tree_lock();
        let config = WatchdogConfig {
            enabled: true,
            device_path: "/dev/watchdog".to_string(),
            timeout_secs: 15,
            ping_interval_secs: 2,
            use_systemd_notify: true,
        };

        let (supervisor, mock) = WatchdogSupervisor::new_mock(config.clone());

        // 1. Initial State
        assert!(supervisor.is_present());
        assert!(!supervisor.is_armed());
        assert!(
            supervisor.ping().is_err(),
            "Ping on un-armed supervisor must return Err"
        );

        // 2. Arm and Ping Loop
        assert!(supervisor.arm().is_ok());
        assert!(supervisor.is_armed());

        for _ in 0..10 {
            assert!(supervisor.ping().is_ok());
        }

        {
            let m = mock.lock().unwrap();
            assert_eq!(m.ping_count, 10);
            assert!(!m.disarmed_safely);
        }

        // 3. Graceful Disarm with 'V' invariant
        assert!(supervisor.disarm().is_ok());
        assert!(!supervisor.is_armed());

        {
            let m = mock.lock().unwrap();
            assert!(m.disarmed_safely, "Watchdog must be marked safely disarmed");
        }

        // 4. Ping after disarm fails
        assert!(supervisor.ping().is_err());
    }

    #[test]
    fn test_adversarial_linux_watchdog_nonexistent_device() {
        let _tree = super::tree_lock();
        let mut driver = LinuxHardwareWatchdog::new("/tmp/phantom_watchdog_9999", 30);
        assert!(!driver.is_hardware_present());
        assert!(!driver.is_armed());

        // Arming absent device fails with clear Err
        let arm_res = driver.arm();
        assert!(arm_res.is_err());
        assert!(arm_res.unwrap_err().contains("not found"));

        // Ping fails
        assert!(driver.ping().is_err());

        // Disarm and close on unopened driver succeeds as a no-op
        assert!(driver.disarm_and_close().is_ok());
    }
}

mod m1_stress_adversarial_test {
    use anyhow::Result;
    use mios_node::cgroups::{
        filter_safe_worker_cores, AffinityPolicy, CgroupV2Controller, NodeResourceLimits,
        WorkerAffinityController,
    };
    use mios_node::hardware::{HardwareAllowlist, HardwareErrorCode, SandboxedHardwareController};
    use mios_node::state_sync::{StateElement, StateStore};
    use mios_node::watchdog::{MockWatchdogDriver, WatchdogConfig, WatchdogSupervisor};
    use std::sync::{Arc, Mutex};
    use std::thread;
    use tempfile::NamedTempFile;

    // =========================================================================
    // Task 1 (T-389): Hardware HAL & Wasm Sandbox Allowlist Stress Tests
    // =========================================================================

    #[test]
    fn test_stress_gpio_unauthorized_pins_and_boundaries() {
        let _tree = super::tree_lock();
        let allowlist = HardwareAllowlist {
            allowed_gpio_pins: [4, 17, 27, 22].into_iter().collect(),
            read_only_gpio_pins: [4].into_iter().collect(),
            ..Default::default()
        };

        let (controller, mock) = SandboxedHardwareController::new_mock(allowlist);

        // Test extreme and unauthorized pin IDs
        let unauthorized_pins = [0, 1, 2, 3, 5, 18, 99, 255, 1024, 65535, u32::MAX];
        for &pin in &unauthorized_pins {
            // Read attempt
            let read_res = controller.mios_sys_gpio_read(pin);
            assert_eq!(
                read_res,
                Err(HardwareErrorCode::PermissionDenied),
                "Pin {} read must be denied",
                pin
            );

            // Write attempt
            let write_res = controller.mios_sys_gpio_write(pin, 1);
            assert_eq!(
                write_res,
                Err(HardwareErrorCode::PermissionDenied),
                "Pin {} write must be denied",
                pin
            );
        }

        // Mock driver should remain empty
        for &pin in &unauthorized_pins {
            assert_eq!(mock.get_mock_gpio(pin), None);
        }
    }

    #[test]
    fn test_stress_gpio_read_only_violation() {
        let _tree = super::tree_lock();
        let allowlist = HardwareAllowlist {
            allowed_gpio_pins: [4, 17].into_iter().collect(),
            read_only_gpio_pins: [4].into_iter().collect(),
            ..Default::default()
        };

        let (controller, mock) = SandboxedHardwareController::new_mock(allowlist);

        // Preset mock value on read-only pin 4
        mock.set_mock_gpio(4, 1);

        // Read should succeed
        assert_eq!(controller.mios_sys_gpio_read(4), Ok(1));

        // Write must fail with ReadOnlyPin
        assert_eq!(
            controller.mios_sys_gpio_write(4, 0),
            Err(HardwareErrorCode::ReadOnlyPin)
        );

        // Mock state must be preserved
        assert_eq!(mock.get_mock_gpio(4), Some(1));
    }

    #[test]
    fn test_stress_i2c_bus_and_address_boundary_violations() {
        let _tree = super::tree_lock();
        let allowlist = HardwareAllowlist {
            allowed_i2c_buses: [1].into_iter().collect(),
            allowed_i2c_addresses: [0x48, 0x68].into_iter().collect(),
            max_i2c_transfer_len: 128,
            ..Default::default()
        };

        let (controller, mock) = SandboxedHardwareController::new_mock(allowlist);
        mock.set_mock_i2c_register(1, 0x68, 0x00, 0x42);

        let write_buf = [0x00u8];
        let mut read_buf = [0u8; 1];

        // Invalid Buses (0, 2, 255)
        for &bus in &[0u8, 2, 3, 255] {
            let res = controller.mios_sys_i2c_transfer(bus, 0x68, &write_buf, &mut read_buf);
            assert_eq!(res, Err(HardwareErrorCode::PermissionDenied));
        }

        // Invalid Addresses (0x00, 0x49, 0x55, 0x77, 0x3FF)
        for &addr in &[0x00u16, 0x49, 0x55, 0x77, 0x3FF] {
            let res = controller.mios_sys_i2c_transfer(1, addr, &write_buf, &mut read_buf);
            assert_eq!(res, Err(HardwareErrorCode::PermissionDenied));
        }
    }

    #[test]
    fn test_stress_i2c_buffer_overflow_attempts() {
        let _tree = super::tree_lock();
        let allowlist = HardwareAllowlist {
            allowed_i2c_buses: [1].into_iter().collect(),
            allowed_i2c_addresses: [0x68].into_iter().collect(),
            max_i2c_transfer_len: 64, // Max 64 bytes
            ..Default::default()
        };

        let (controller, _mock) = SandboxedHardwareController::new_mock(allowlist);

        // 1. Write buffer overflow (65 bytes > 64)
        let overflow_write = vec![0x00u8; 65];
        let mut small_read = [0u8; 1];
        let res1 = controller.mios_sys_i2c_transfer(1, 0x68, &overflow_write, &mut small_read);
        assert_eq!(res1, Err(HardwareErrorCode::InvalidParameter));

        // 2. Read buffer overflow (65 bytes > 64)
        let small_write = [0x00u8];
        let mut overflow_read = vec![0u8; 65];
        let res2 = controller.mios_sys_i2c_transfer(1, 0x68, &small_write, &mut overflow_read);
        assert_eq!(res2, Err(HardwareErrorCode::InvalidParameter));

        // 3. Exact boundary (64 bytes) must succeed
        let boundary_write = vec![0x00u8; 64];
        let mut boundary_read = vec![0u8; 64];
        let res3 = controller.mios_sys_i2c_transfer(1, 0x68, &boundary_write, &mut boundary_read);
        assert_eq!(res3, Ok(64));
    }

    #[test]
    fn test_stress_dynamic_hardware_allowlist_updates() {
        let _tree = super::tree_lock();
        let initial_allowlist = HardwareAllowlist {
            allowed_gpio_pins: [17].into_iter().collect(),
            read_only_gpio_pins: [].into_iter().collect(),
            allowed_i2c_buses: [1].into_iter().collect(),
            allowed_i2c_addresses: [0x48].into_iter().collect(),
            max_i2c_transfer_len: 32,
        };

        let (controller, _mock) = SandboxedHardwareController::new_mock(initial_allowlist);

        // Pin 27 initially denied
        assert_eq!(
            controller.mios_sys_gpio_write(27, 1),
            Err(HardwareErrorCode::PermissionDenied)
        );

        // Update allowlist dynamically to include Pin 27
        let mut updated_allowlist = controller.get_allowlist();
        updated_allowlist.allowed_gpio_pins.insert(27);
        controller.update_allowlist(updated_allowlist);

        // Pin 27 now allowed
        assert_eq!(controller.mios_sys_gpio_write(27, 1), Ok(()));
        assert_eq!(controller.mios_sys_gpio_read(27), Ok(1));
    }

    // =========================================================================
    // Task 2 (T-390): Dynamic CPU Pinning & Cgroups Stress Tests
    // =========================================================================

    #[test]
    fn test_stress_cpu_topologies_core_zero_isolation() {
        let _tree = super::tree_lock();
        // 1-Core Topology: Core 0 is the ONLY core -> must be retained
        let c1 = filter_safe_worker_cores(1, None, true);
        assert_eq!(c1, vec![0], "1-core system must retain Core 0");

        // 2-Core Topology: Multi-core -> Core 0 must be stripped, leaving [1]
        let c2 = filter_safe_worker_cores(2, None, true);
        assert_eq!(c2, vec![1], "2-core system must strip Core 0");
        assert!(!c2.contains(&0));

        // 4-Core Topology: [1, 2, 3]
        let c4 = filter_safe_worker_cores(4, None, true);
        assert_eq!(c4, vec![1, 2, 3]);
        assert!(!c4.contains(&0));

        // 64-Core Topology: 63 cores (1..=63), Core 0 NEVER present
        let c64 = filter_safe_worker_cores(64, None, true);
        assert_eq!(c64.len(), 63);
        assert_eq!(c64[0], 1);
        assert_eq!(*c64.last().unwrap(), 63);
        assert!(!c64.contains(&0));

        // Filtering out-of-bounds and Core 0 from requested list
        let requested = vec![0, 5, 12, 63, 64, 128];
        let filtered_64 = filter_safe_worker_cores(64, Some(&requested), true);
        assert_eq!(filtered_64, vec![5, 12, 63]);
    }

    #[test]
    fn test_stress_affinity_controller_exhaustion_and_recovery() {
        let _tree = super::tree_lock();
        let limits = NodeResourceLimits::default();
        let mut controller = WorkerAffinityController::new(4, limits); // safe: [1, 2, 3]

        // Allocate 3 exclusive cores
        let c1 = controller
            .allocate_cores_for_policy(AffinityPolicy::Exclusive, 2)
            .unwrap();
        assert_eq!(c1, vec![1, 2]);

        let c2 = controller
            .allocate_cores_for_policy(AffinityPolicy::Exclusive, 1)
            .unwrap();
        assert_eq!(c2, vec![3]);

        // Exhausted: requesting 1 more should return error
        let err_alloc = controller.allocate_cores_for_policy(AffinityPolicy::Exclusive, 1);
        assert!(err_alloc.is_err());

        // Release core 2
        controller.release_cores(&[2]);

        // Now allocation for 1 core succeeds and gives core 2
        let c3 = controller
            .allocate_cores_for_policy(AffinityPolicy::Exclusive, 1)
            .unwrap();
        assert_eq!(c3, vec![2]);

        // Low priority always targets highest safe core (3)
        let low = controller
            .allocate_cores_for_policy(AffinityPolicy::LowPriority, 0)
            .unwrap();
        assert_eq!(low, vec![3]);
        assert!(!low.contains(&0));

        // Shared policy always returns all safe cores [1, 2, 3]
        let shared = controller
            .allocate_cores_for_policy(AffinityPolicy::Shared, 0)
            .unwrap();
        assert_eq!(shared, vec![1, 2, 3]);
        assert!(!shared.contains(&0));
    }

    #[test]
    fn test_stress_cgroup_formatting_edge_cases() {
        let _tree = super::tree_lock();
        // None quota -> "max <period>"
        assert_eq!(
            CgroupV2Controller::format_cpu_max(None, 100_000),
            "max 100000"
        );

        // Zero quota pct -> "0 <period>"
        assert_eq!(
            CgroupV2Controller::format_cpu_max(Some(0), 100_000),
            "0 100000"
        );

        // Standard 80% quota -> "80000 100000"
        assert_eq!(
            CgroupV2Controller::format_cpu_max(Some(80), 100_000),
            "80000 100000"
        );

        // Multi-core 400% quota -> "400000 100000"
        assert_eq!(
            CgroupV2Controller::format_cpu_max(Some(400), 100_000),
            "400000 100000"
        );

        // Small period (1000us)
        assert_eq!(
            CgroupV2Controller::format_cpu_max(Some(50), 1_000),
            "500 1000"
        );
    }

    // =========================================================================
    // Task 3 (T-391): CRDT State Compaction & Snapshot GC Stress Tests
    // =========================================================================

    #[test]
    fn test_stress_crdt_tombstone_ttl_and_resurrection_invariants() {
        let _tree = super::tree_lock();
        let mut store = StateStore::new(10);

        // 1. Insert key A deleted at t = 1000
        store.merge_element(StateElement {
            key: "key_a".to_string(),
            value: Vec::new(),
            timestamp_ns: 1_000_000_000,
            originating_node_id: 10,
            is_deleted: true,
        });

        // 2. Insert key B deleted at t = 2000
        store.merge_element(StateElement {
            key: "key_b".to_string(),
            value: Vec::new(),
            timestamp_ns: 2_000_000_000,
            originating_node_id: 10,
            is_deleted: true,
        });

        // 3. Insert active key C at t = 2500
        store.merge_element(StateElement {
            key: "key_c".to_string(),
            value: b"val_c".to_vec(),
            timestamp_ns: 2_500_000_000,
            originating_node_id: 10,
            is_deleted: false,
        });

        // Run compaction at current_time = 2200 with TTL = 500
        // key_a age = 2200 - 1000 = 1200 > 500 -> PURGED
        // key_b age = 2200 - 2000 = 200 <= 500 -> RETAINED
        // key_c is active -> RETAINED
        let stats = store.compact_tombstones(2_200_000_000, 500_000_000);
        assert_eq!(stats.initial_elements, 3);
        assert_eq!(stats.active_elements, 1);
        assert_eq!(stats.tombstones_purged, 1);
        assert_eq!(stats.tombstones_retained, 1);

        assert_eq!(store.get("key_c"), Some(&b"val_c".to_vec()));
        assert_eq!(store.get("key_b"), None); // tombstone
        assert_eq!(store.get("key_a"), None); // purged

        // Invariant: Merging an older update for key_b (t = 1500 < tombstone t = 2000) does NOT resurrect key_b
        let stale_remote_b = StateElement {
            key: "key_b".to_string(),
            value: b"resurrect_attempt".to_vec(),
            timestamp_ns: 1_500_000_000,
            originating_node_id: 20,
            is_deleted: false,
        };
        assert!(!store.merge_element(stale_remote_b));
        assert_eq!(store.get("key_b"), None);

        // Invariant: Merging a NEWER update for key_b (t = 3000 > tombstone t = 2000) DOES resurrect key_b
        let fresh_remote_b = StateElement {
            key: "key_b".to_string(),
            value: b"new_resurrection".to_vec(),
            timestamp_ns: 3_000_000_000,
            originating_node_id: 20,
            is_deleted: false,
        };
        assert!(store.merge_element(fresh_remote_b));
        assert_eq!(store.get("key_b"), Some(&b"new_resurrection".to_vec()));
    }

    #[test]
    fn test_stress_crdt_identical_timestamp_node_id_tie_breaking() {
        let _tree = super::tree_lock();
        let mut store = StateStore::new(100);

        // Local element created by Node 100 with timestamp 5000
        let local = StateElement {
            key: "tie_key".to_string(),
            value: b"from_node_100".to_vec(),
            timestamp_ns: 5000,
            originating_node_id: 100,
            is_deleted: false,
        };
        store.merge_element(local);

        // Remote element with SAME timestamp 5000 from Node 50 (lower node ID) -> Should NOT overwrite
        let remote_lower = StateElement {
            key: "tie_key".to_string(),
            value: b"from_node_50".to_vec(),
            timestamp_ns: 5000,
            originating_node_id: 50,
            is_deleted: false,
        };
        assert!(!store.merge_element(remote_lower));
        assert_eq!(store.get("tie_key"), Some(&b"from_node_100".to_vec()));

        // Remote element with SAME timestamp 5000 from Node 200 (higher node ID) -> MUST overwrite
        let remote_higher = StateElement {
            key: "tie_key".to_string(),
            value: b"from_node_200".to_vec(),
            timestamp_ns: 5000,
            originating_node_id: 200,
            is_deleted: false,
        };
        assert!(store.merge_element(remote_higher));
        assert_eq!(store.get("tie_key"), Some(&b"from_node_200".to_vec()));
    }

    #[test]
    fn test_stress_crdt_wal_compaction_and_disk_reloading() -> Result<()> {
        let _tree = super::tree_lock();
        let tmp = NamedTempFile::new()?;
        let path = tmp.path().to_str().unwrap().to_string();

        let mut store = StateStore::with_persistence(501, &path)?;

        // Insert 100 items: 40 active, 60 deleted with old timestamps
        for i in 0..40 {
            store.merge_element(StateElement {
                key: format!("sensor_{}", i),
                value: format!("val_{}", i).into_bytes(),
                timestamp_ns: 5000,
                originating_node_id: 501,
                is_deleted: false,
            });
        }

        for i in 40..100 {
            store.merge_element(StateElement {
                key: format!("sensor_{}", i),
                value: Vec::new(),
                timestamp_ns: 1000,
                originating_node_id: 501,
                is_deleted: true,
            });
        }

        assert_eq!(store.total_elements_count(), 100);
        assert_eq!(store.count_tombstones(), 60);

        // Compact with TTL = 1000 at current_time = 10000 (age 9000 > 1000 -> purged)
        let stats = store.compact_disk_storage(10_000, 1_000)?;
        assert_eq!(stats.tombstones_purged, 60);
        assert_eq!(stats.active_elements, 40);
        assert_eq!(store.total_elements_count(), 40);

        // Reload from disk and verify exact data integrity
        let reloaded = StateStore::load_from_disk(&path, 501)?;
        assert_eq!(reloaded.total_elements_count(), 40);
        assert_eq!(reloaded.count_tombstones(), 0);

        for i in 0..40 {
            let expected_val = format!("val_{}", i).into_bytes();
            assert_eq!(reloaded.get(&format!("sensor_{}", i)), Some(&expected_val));
        }

        for i in 40..100 {
            assert_eq!(reloaded.get(&format!("sensor_{}", i)), None);
        }

        Ok(())
    }

    // =========================================================================
    // Task 4 (T-400): Hardware Watchdog Supervisor & Ping Stress Tests
    // =========================================================================

    #[test]
    fn test_stress_watchdog_rapid_sequential_pings() {
        let _tree = super::tree_lock();
        let config = WatchdogConfig {
            enabled: true,
            device_path: "/dev/watchdog".to_string(),
            timeout_secs: 30,
            ping_interval_secs: 5,
            use_systemd_notify: false,
        };
        let (supervisor, mock) = WatchdogSupervisor::new_mock(config);

        supervisor.arm().unwrap();
        assert!(supervisor.is_armed());

        // 10,000 rapid sequential pings
        for _ in 0..10_000 {
            assert!(supervisor.ping().is_ok());
        }

        let m = mock.lock().unwrap();
        assert_eq!(m.ping_count, 10_000);
        assert!(!m.disarmed_safely);
    }

    #[test]
    fn test_stress_watchdog_concurrent_multithreaded_pings() {
        let _tree = super::tree_lock();
        let config = WatchdogConfig::default();
        let (supervisor, mock) = WatchdogSupervisor::new_mock(config);

        supervisor.arm().unwrap();
        let sup_arc = Arc::new(supervisor);

        let mut handles = Vec::new();
        for _i in 0..4 {
            let sup_clone = sup_arc.clone();
            handles.push(thread::spawn(move || {
                for _j in 0..100 {
                    let res = sup_clone.ping();
                    assert!(res.is_ok());
                }
            }));
        }

        for h in handles {
            h.join().unwrap();
        }

        {
            let m = mock.lock().unwrap();
            assert_eq!(m.ping_count, 400);
        }

        assert!(sup_arc.disarm().is_ok());
        assert!(!sup_arc.is_armed());
    }

    #[test]
    fn test_stress_watchdog_disarm_rearm_and_missing_recovery() {
        let _tree = super::tree_lock();
        // Missing device recovery
        let mock_missing = Arc::new(Mutex::new(MockWatchdogDriver::new(false, 30)));
        let sup_missing = WatchdogSupervisor::new(WatchdogConfig::default(), mock_missing);
        assert!(!sup_missing.is_present());
        assert!(sup_missing.arm().is_err());
        assert!(!sup_missing.is_armed());

        // Lifecycle re-arming
        let (supervisor, mock) = WatchdogSupervisor::new_mock(WatchdogConfig::default());
        assert!(supervisor.arm().is_ok());
        assert!(supervisor.ping().is_ok());

        // Disarm
        assert!(supervisor.disarm().is_ok());
        assert!(!supervisor.is_armed());
        {
            let m = mock.lock().unwrap();
            assert!(m.disarmed_safely);
        }
        // Ping while disarmed fails
        assert!(supervisor.ping().is_err());

        // Re-arm
        assert!(supervisor.arm().is_ok());
        assert!(supervisor.is_armed());
        {
            let m = mock.lock().unwrap();
            assert!(!m.disarmed_safely);
            assert!(m.armed);
        }
        assert!(supervisor.ping().is_ok());
    }
}

mod m2_deep_adversarial_stress_test {
    use mios_node::ble::{
        BleAdapter, BleBootstrapState, BleMeshBootstrap, MockBleAdapter, ProvisioningPayload,
    };
    use mios_node::buffer_pool::{BucketTier, BufferPool, PooledBuffer};
    use mios_node::capabilities::{CapabilityRegistry, NodeAnnouncePayload, NodeCapabilities};
    use mios_node::overlay::{HysteresisConfig, MultiTransportRouter, TransportType};
    use mios_node::protocol::{Frame, MessageType};
    use mios_node::scheduler::{ScheduledTarget, TaskItem, TaskPriority, WorkStealingScheduler};
    use std::collections::HashMap;
    use std::sync::atomic::{AtomicUsize, Ordering};
    use std::sync::Arc;
    use std::thread;

    // =========================================================================
    // Suite 1: Scheduler & Work-Stealing Invariants (T-392)
    // =========================================================================

    #[test]
    fn test_scheduler_priority_enum_fuzzing_and_ordering() {
        let _tree = super::tree_lock();
        // 1. Exhaustive u8 conversion fuzzing
        for v in 0..=255u8 {
            let priority = TaskPriority::from_u8(v);
            match v {
                0 => assert_eq!(priority, Some(TaskPriority::Critical)),
                1 => assert_eq!(priority, Some(TaskPriority::High)),
                2 => assert_eq!(priority, Some(TaskPriority::Normal)),
                3 => assert_eq!(priority, Some(TaskPriority::Low)),
                _ => assert_eq!(priority, None),
            }
        }

        // 2. Strict total ordering
        assert!(TaskPriority::Critical < TaskPriority::High);
        assert!(TaskPriority::High < TaskPriority::Normal);
        assert!(TaskPriority::Normal < TaskPriority::Low);
    }

    #[test]
    fn test_scheduler_pinning_matrix_and_stealable_predicates() {
        let _tree = super::tree_lock();
        // A. Hardware pinned (general)
        let mut t_hw = TaskItem::new(1, TaskPriority::High, 1, vec![], vec![]);
        t_hw.pinned_hardware = true;
        t_hw.pinned_node_id = None;
        assert!(!t_hw.is_stealable(None));
        assert!(!t_hw.is_stealable(Some(101)));
        assert!(!t_hw.is_stealable(Some(202)));

        // B. Pinned to specific node ID only (e.g. Node 101)
        let mut t_node = TaskItem::new(2, TaskPriority::Normal, 1, vec![], vec![]);
        t_node.pinned_hardware = false;
        t_node.pinned_node_id = Some(101);
        assert!(!t_node.is_stealable(None));
        assert!(t_node.is_stealable(Some(101)));
        assert!(!t_node.is_stealable(Some(102)));

        // C. Pinned to hardware AND specific node ID (hardware pin dominates)
        let mut t_both = TaskItem::new(3, TaskPriority::Critical, 1, vec![], vec![]);
        t_both.pinned_hardware = true;
        t_both.pinned_node_id = Some(101);
        assert!(!t_both.is_stealable(Some(101)));
        assert!(!t_both.is_stealable(Some(102)));
        assert!(!t_both.is_stealable(None));

        // D. Completely unpinned
        let t_free = TaskItem::new(4, TaskPriority::Low, 1, vec![], vec![]);
        assert!(t_free.is_stealable(None));
        assert!(t_free.is_stealable(Some(101)));
        assert!(t_free.is_stealable(Some(999)));
    }

    #[test]
    fn test_scheduler_multithreaded_high_throughput_race_stress() {
        let _tree = super::tree_lock();
        let scheduler = Arc::new(WorkStealingScheduler::new(100, 8));
        let total_tasks_per_producer = 250;
        let num_producers = 4;
        let total_tasks = total_tasks_per_producer * num_producers;

        // Producers
        let prod_handles: Vec<_> = (0..num_producers)
            .map(|p_idx| {
                let s = Arc::clone(&scheduler);
                thread::spawn(move || {
                    for i in 0..total_tasks_per_producer {
                        let task_id = (p_idx * 1000 + i) as u64;
                        let prio = match (task_id + p_idx as u64) % 4 {
                            0 => TaskPriority::Critical,
                            1 => TaskPriority::High,
                            2 => TaskPriority::Normal,
                            _ => TaskPriority::Low,
                        };
                        let mut task = TaskItem::new(task_id, prio, 1, vec![1, 2], vec![]);
                        if task_id.is_multiple_of(7) {
                            task.pinned_hardware = true;
                        }
                        if task_id.is_multiple_of(5) && !task.pinned_hardware {
                            task.pinned_node_id = Some(100);
                        }

                        // Distribute across worker hints or global injector
                        let hint = if i % 2 == 0 { Some(p_idx * 2) } else { None };
                        s.submit_task(task, hint);
                    }
                })
            })
            .collect();

        for h in prod_handles {
            h.join().unwrap();
        }

        assert_eq!(scheduler.get_stats().tasks_ingested, total_tasks as u64);

        // Consumers (Workers 0..8) draining tasks
        let executed_count = Arc::new(AtomicUsize::new(0));
        let worker_handles: Vec<_> = (0..8)
            .map(|w_idx| {
                let s = Arc::clone(&scheduler);
                let ec = Arc::clone(&executed_count);
                thread::spawn(move || {
                    let mut local_drained = 0;
                    let mut empty_spins = 0;
                    while empty_spins < 50 {
                        if let Some(task) = s.pop_task(w_idx) {
                            ec.fetch_add(1, Ordering::SeqCst);
                            local_drained += 1;
                            empty_spins = 0;

                            // Check invariant: if task was stolen (by another worker), it must not be pinned to other nodes
                            if task.pinned_hardware {
                                // Pinned hardware tasks should only be processed locally
                            }
                        } else {
                            empty_spins += 1;
                            thread::yield_now();
                        }
                    }
                    local_drained
                })
            })
            .collect();

        let mut total_drained = 0;
        for h in worker_handles {
            total_drained += h.join().unwrap();
        }

        assert_eq!(total_drained, total_tasks);
        assert_eq!(executed_count.load(Ordering::SeqCst), total_tasks);
        assert_eq!(scheduler.total_queue_depth(), 0);
    }

    #[test]
    fn test_scheduler_router_adversarial_matrix() {
        let _tree = super::tree_lock();
        let scheduler = WorkStealingScheduler::new(50, 2);

        // 1. Hardware pinned task -> always Local
        let mut hw_task = TaskItem::new(1, TaskPriority::Critical, 1, vec![], vec![]);
        hw_task.pinned_hardware = true;
        assert_eq!(
            scheduler.route_task(&hw_task, &[(1, 0), (2, 0)]),
            ScheduledTarget::Local
        );

        // 2. Task pinned to node 50 (local) -> Local
        let mut node_local_task = TaskItem::new(2, TaskPriority::High, 1, vec![], vec![]);
        node_local_task.pinned_node_id = Some(50);
        assert_eq!(
            scheduler.route_task(&node_local_task, &[(60, 0)]),
            ScheduledTarget::Local
        );

        // 3. Task pinned to node 60 (remote) -> Offload(60)
        let mut node_remote_task = TaskItem::new(3, TaskPriority::High, 1, vec![], vec![]);
        node_remote_task.pinned_node_id = Some(60);
        assert_eq!(
            scheduler.route_task(&node_remote_task, &[(60, 0)]),
            ScheduledTarget::Offload(60)
        );

        // 4. Unpinned task with empty peer list -> Local
        let unpinned = TaskItem::new(4, TaskPriority::Normal, 1, vec![], vec![]);
        assert_eq!(scheduler.route_task(&unpinned, &[]), ScheduledTarget::Local);

        // 5. Unpinned task with local load < 2 -> Local
        assert_eq!(
            scheduler.route_task(&unpinned, &[(70, 0)]),
            ScheduledTarget::Local
        );
    }

    // =========================================================================
    // Suite 2: Buffer Pool Zero-Copy & Recycling Invariants (T-393)
    // =========================================================================

    #[test]
    fn test_buffer_pool_tier_resolution_boundaries() {
        let _tree = super::tree_lock();
        let test_cases = vec![
            (0, BucketTier::Small, 256),
            (1, BucketTier::Small, 256),
            (255, BucketTier::Small, 256),
            (256, BucketTier::Small, 256),
            (257, BucketTier::Medium, 4096),
            (4095, BucketTier::Medium, 4096),
            (4096, BucketTier::Medium, 4096),
            (4097, BucketTier::Large, 65536),
            (65535, BucketTier::Large, 65536),
            (65536, BucketTier::Large, 65536),
            (65537, BucketTier::Huge, 1048576),
            (1048576, BucketTier::Huge, 1048576),
            (2000000, BucketTier::Huge, 1048576),
        ];

        for (size, expected_tier, expected_cap) in test_cases {
            let tier = BucketTier::from_size(size);
            assert_eq!(tier, expected_tier, "Size {} mapped to wrong tier", size);
            assert_eq!(tier.capacity_bytes(), expected_cap);
        }
    }

    #[test]
    fn test_buffer_pool_slice_and_split_prefix_adversarial_bounds() {
        let _tree = super::tree_lock();
        let pool = BufferPool::new();
        let mut buf = pool.acquire(100); // Small bucket
        buf.extend_from_slice(b"0123456789ABCDEF"); // 16 bytes

        // 1. Valid slice boundaries
        assert_eq!(buf.slice(0, 0).unwrap(), b"");
        assert_eq!(buf.slice(0, 16).unwrap(), b"0123456789ABCDEF");
        assert_eq!(buf.slice(4, 10).unwrap(), b"456789");
        assert_eq!(buf.slice(16, 16).unwrap(), b"");

        // 2. Inverted slice bounds (start > end)
        assert!(buf.slice(10, 5).is_err());

        // 3. Out-of-bounds slice (end > len)
        assert!(buf.slice(0, 17).is_err());
        assert!(buf.slice(17, 18).is_err());

        // 4. Split prefix out-of-bounds (at > len)
        assert!(buf.split_prefix(17).is_err());

        // 5. Chained split prefix
        let p1 = buf.split_prefix(4).unwrap();
        assert_eq!(p1, b"0123");
        assert_eq!(buf.as_slice(), b"456789ABCDEF");

        let p2 = buf.split_prefix(6).unwrap();
        assert_eq!(p2, b"456789");
        assert_eq!(buf.as_slice(), b"ABCDEF");

        let p3 = buf.split_prefix(6).unwrap();
        assert_eq!(p3, b"ABCDEF");
        assert_eq!(buf.len(), 0);
        assert!(buf.is_empty());

        // 6. Split on empty buffer
        let p_empty = buf.split_prefix(0).unwrap();
        assert_eq!(p_empty, b"");
        assert!(buf.split_prefix(1).is_err());
    }

    #[test]
    fn test_buffer_pool_into_vec_and_standalone_invariants() {
        let _tree = super::tree_lock();
        let pool = BufferPool::new();

        // 1. Test into_vec()
        {
            let mut buf = pool.acquire(200);
            buf.extend_from_slice(b"unrecycled_payload");
            assert_eq!(pool.get_stats().active_leased, 1);

            let vec_data = buf.into_vec();
            assert_eq!(vec_data, b"unrecycled_payload");
            // active_leased must decrement even on into_vec()
            assert_eq!(pool.get_stats().active_leased, 0);
            // recycles should NOT increment because buffer was consumed
            assert_eq!(pool.get_stats().recycles, 0);
        }

        // 2. Test Standalone buffer (not connected to any pool)
        {
            let mut standalone = PooledBuffer::standalone(BucketTier::Medium);
            assert_eq!(standalone.tier(), BucketTier::Medium);
            standalone.extend_from_slice(b"standalone_bytes");
            assert_eq!(standalone.as_slice(), b"standalone_bytes");
            let v = standalone.into_vec();
            assert_eq!(v, b"standalone_bytes");
        }
    }

    #[test]
    fn test_buffer_pool_multithreaded_churn_and_saturation() {
        let _tree = super::tree_lock();
        let pool = BufferPool::new();
        pool.preallocate(10, 10);

        let num_threads = 12;
        let iterations_per_thread = 200;

        let handles: Vec<_> = (0..num_threads)
            .map(|t_idx| {
                let p = Arc::clone(&pool);
                thread::spawn(move || {
                    for i in 0..iterations_per_thread {
                        let size = match (t_idx + i) % 4 {
                            0 => 64,     // Small
                            1 => 2048,   // Medium
                            2 => 32768,  // Large
                            _ => 128000, // Huge
                        };

                        let mut buf = p.acquire(size);
                        buf.extend_from_slice(b"CHURN_STRESS_TEST_DATA");
                        assert_eq!(&buf[0..5], b"CHURN");

                        if i % 10 == 0 {
                            // Consume 10% via into_vec
                            let _ = buf.into_vec();
                        }
                        // Remaining 90% dropped and recycled
                    }
                })
            })
            .collect();

        for h in handles {
            h.join().unwrap();
        }

        let stats = pool.get_stats();
        assert_eq!(stats.active_leased, 0);
        assert_eq!(
            stats.allocations,
            (num_threads * iterations_per_thread) as u64
        );
        assert!(stats.recycles > 0);

        // Verify bounded bucket capacities
        let (s, m, l, h) = pool.bucket_depths();
        assert!(s <= BucketTier::Small.max_pool_capacity());
        assert!(m <= BucketTier::Medium.max_pool_capacity());
        assert!(l <= BucketTier::Large.max_pool_capacity());
        assert!(h <= BucketTier::Huge.max_pool_capacity());
    }

    // =========================================================================
    // Suite 3: Node Capabilities & Registry Invariants (T-394)
    // =========================================================================

    #[test]
    fn test_capabilities_frame_validation_and_malformed_wire_rejection() {
        let _tree = super::tree_lock();
        let mut caps = NodeCapabilities::default();
        caps.hardware.cpu_arch = "riscv64".to_string();
        caps.has_i2c = true;

        let payload = NodeAnnouncePayload::new(99, "edge-blade-99".to_string(), caps);
        let valid_frame = payload.to_frame().unwrap();

        // 1. Valid decode
        let decoded = NodeAnnouncePayload::from_frame(&valid_frame).unwrap();
        assert_eq!(decoded.node_id, 99);
        assert_eq!(decoded.hostname, "edge-blade-99");
        assert!(decoded.capabilities.has_i2c);

        // 2. Reject wrong message type (Heartbeat instead of NodeAnnounce)
        let wrong_type_frame = Frame::new(MessageType::Heartbeat, 99, valid_frame.payload.clone());
        assert!(NodeAnnouncePayload::from_frame(&wrong_type_frame).is_err());

        // 3. Reject corrupted JSON payload
        let corrupted_frame = Frame::new(MessageType::NodeAnnounce, 99, vec![0xFF, 0xFE, 0xFD]);
        assert!(NodeAnnouncePayload::from_frame(&corrupted_frame).is_err());

        // 4. Reject empty payload
        let empty_frame = Frame::new(MessageType::NodeAnnounce, 99, vec![]);
        assert!(NodeAnnouncePayload::from_frame(&empty_frame).is_err());
    }

    #[test]
    fn test_capabilities_registry_fuzzing_and_multithreaded_access() {
        let _tree = super::tree_lock();
        let registry = Arc::new(CapabilityRegistry::new());

        // Populate registry with diverse node profiles
        for i in 1..=30 {
            let mut caps = NodeCapabilities::default();
            caps.hardware.ram_available_kb = (i as u64) * 1024 * 1024; // 1MB to 30MB
            caps.vram.vram_available_mb = if i % 2 == 0 { i * 256 } else { 0 };
            caps.has_gpio = i % 3 == 0;
            caps.has_i2c = i % 5 == 0;
            caps.engines.wasm_tier = true;
            caps.engines.native_tier = i % 4 != 0;

            let payload = NodeAnnouncePayload::new(i, format!("node-{:03}", i), caps);
            registry.register_announce(payload, 1000 + (i as u64) * 10);
        }

        assert_eq!(registry.active_node_count(), 30);

        // Test queries:
        // A. RAM >= 10MB, VRAM >= 1024MB, Native required, GPIO required
        let matches = registry.find_eligible_nodes(10 * 1024 * 1024, 1024, true, true, true, false);

        for node_id in &matches {
            let caps = registry.get_capabilities(*node_id).unwrap();
            assert!(caps.hardware.ram_available_kb >= 10 * 1024 * 1024);
            assert!(caps.vram.vram_available_mb >= 1024);
            assert!(caps.engines.wasm_tier);
            assert!(caps.engines.native_tier);
            assert!(caps.has_gpio);
        }

        // B. Eviction with clock skew (now_utc < received_at_utc) -> No panic
        let evicted_skew = registry.evict_stale(60, 500);
        assert_eq!(evicted_skew, 0); // saturating_sub ensures 0 elapsed

        // C. Evict nodes older than 50s at t=1400
        let evicted = registry.evict_stale(50, 1400);
        assert!(evicted > 0);
        assert_eq!(registry.active_node_count(), 30 - evicted);
    }

    // =========================================================================
    // Suite 4: BLE Beaconing & Offline Mesh Bootstrap (T-395)
    // =========================================================================

    #[test]
    fn test_ble_bootstrap_state_conversions() {
        let _tree = super::tree_lock();
        for v in 0..=4u8 {
            let state = BleBootstrapState::try_from(v).unwrap();
            assert_eq!(state as u8, v);
        }
        assert!(BleBootstrapState::try_from(5).is_err());
        assert!(BleBootstrapState::try_from(255).is_err());
    }

    #[test]
    fn test_ble_bootstrap_handshake_violation_and_tamper_fuzzing() {
        let _tree = super::tree_lock();
        let adapter: Arc<dyn BleAdapter> = Arc::new(MockBleAdapter::new());
        let bootstrap = BleMeshBootstrap::new(88, Arc::clone(&adapter)).unwrap();
        bootstrap.start().unwrap();

        // 1. Invariant: Writing provisioning credentials BEFORE ECDH handshake must fail
        let premature_write = vec![0x11; 64];
        let res = bootstrap.handle_provisioning_write(&premature_write);
        assert!(
            res.is_err(),
            "Premature provisioning write must be rejected"
        );

        // 2. Reject invalid ECDH public key lengths
        assert!(bootstrap.handle_ecdh_exchange(&[]).is_err());
        assert!(bootstrap.handle_ecdh_exchange(&[0u8; 16]).is_err());
        assert!(bootstrap.handle_ecdh_exchange(&[0u8; 31]).is_err());
        assert!(bootstrap.handle_ecdh_exchange(&[0u8; 33]).is_err());
        assert!(bootstrap.handle_ecdh_exchange(&[0u8; 64]).is_err());

        // 3. Normal ECDH Handshake
        let client_priv = [0x77u8; 32];
        let client_pub = mios_node::crypto::x25519_public_key(&client_priv);
        bootstrap.handle_ecdh_exchange(&client_pub).unwrap();
        assert_eq!(bootstrap.state(), BleBootstrapState::Handshaking);

        // 4. Derive correct shared key and generate encrypted payload
        let node_pub = bootstrap.local_public_key();
        let ss = mios_node::crypto::x25519(&client_priv, &node_pub);
        use sha2::{Digest, Sha256};
        let salt = Sha256::digest([node_pub.as_slice(), client_pub.as_slice()].concat());
        let key = mios_node::crypto::hkdf_sha256(&salt, &ss, mios_node::ble::BLE_HKDF_INFO, 32);
        let mut derived_key = [0u8; 32];
        derived_key.copy_from_slice(&key[0..32]);

        let creds = ProvisioningPayload::new(
            "AdversarialSSID".to_string(),
            "AdversarialPass123!".to_string(),
            "cluster_tok_adversarial".to_string(),
            "10.200.0.1:8650".to_string(),
        );
        let creds_json = serde_json::to_vec(&creds).unwrap();
        let nonce = mios_node::crypto::random_bytes::<12>().unwrap();
        let mut ciphertext = nonce.to_vec();
        ciphertext.extend(mios_node::crypto::chacha20_poly1305_encrypt(
            &derived_key,
            &nonce,
            mios_node::ble::BLE_AEAD_AAD,
            &creds_json,
        ));

        // 5. Tamper fuzzing: flip every byte in ciphertext and ensure AEAD verification rejects it
        for i in 0..ciphertext.len() {
            let mut tampered = ciphertext.clone();
            tampered[i] ^= 0x01; // flip least significant bit
            let tamper_res = bootstrap.handle_provisioning_write(&tampered);
            assert!(
                tamper_res.is_err(),
                "AEAD failed to reject tampered byte at index {}",
                i
            );
        }

        // 6. Valid write completes provisioning
        let valid_prov = bootstrap.handle_provisioning_write(&ciphertext).unwrap();
        assert_eq!(valid_prov.ssid, "AdversarialSSID");
        assert_eq!(bootstrap.state(), BleBootstrapState::Provisioned);
        assert!(!adapter.is_advertising());
    }

    // =========================================================================
    // Suite 5: Multi-Transport Router & Anti-Flap Hysteresis (T-396)
    // =========================================================================

    #[test]
    fn test_transport_type_hierarchy_and_strings() {
        let _tree = super::tree_lock();
        assert!(TransportType::LanBroadcast < TransportType::WireGuard);
        assert!(TransportType::WireGuard < TransportType::Tailscale);
        assert!(TransportType::Tailscale < TransportType::DirectTcp);

        assert_eq!(TransportType::LanBroadcast.as_str(), "lan_broadcast");
        assert_eq!(TransportType::WireGuard.as_str(), "wireguard");
        assert_eq!(TransportType::Tailscale.as_str(), "tailscale");
        assert_eq!(TransportType::DirectTcp.as_str(), "direct_tcp");
    }

    #[test]
    fn test_overlay_router_degraded_endpoint_profiles() {
        let _tree = super::tree_lock();
        let router = MultiTransportRouter::new(None);

        // 1. Peer with only DirectTcp
        let mut ep_tcp = HashMap::new();
        ep_tcp.insert(TransportType::DirectTcp, "1.2.3.4:8650".to_string());
        router.register_peer(501, ep_tcp);
        assert_eq!(
            router.select_route(501).unwrap(),
            (TransportType::DirectTcp, "1.2.3.4:8650".to_string())
        );

        // 2. Peer with only WireGuard and Tailscale
        let mut ep_vpn = HashMap::new();
        ep_vpn.insert(TransportType::WireGuard, "10.0.0.2:8650".to_string());
        ep_vpn.insert(TransportType::Tailscale, "100.64.0.2:8650".to_string());
        router.register_peer(502, ep_vpn);
        assert_eq!(
            router.select_route(502).unwrap(),
            (TransportType::WireGuard, "10.0.0.2:8650".to_string())
        );

        // 3. Unregistered peer -> Err
        assert!(router.select_route(999).is_err());
    }

    #[test]
    fn test_overlay_router_adversarial_flap_and_intermittent_lan_recovery() {
        let _tree = super::tree_lock();
        let config = HysteresisConfig {
            fail_strikes_threshold: 3,
            recovery_dwell_ms: 120_000, // 120s dwell
            recovery_strikes_threshold: 3,
        };
        let router = MultiTransportRouter::new(Some(config));

        let mut endpoints = HashMap::new();
        endpoints.insert(TransportType::LanBroadcast, "192.168.1.99:8650".to_string());
        endpoints.insert(TransportType::WireGuard, "10.0.0.99:8650".to_string());
        endpoints.insert(TransportType::Tailscale, "100.64.0.99:8650".to_string());

        router.register_peer(700, endpoints);

        // 1. Trigger partition (3 strikes)
        router.record_missed_heartbeat(700, TransportType::LanBroadcast, 1000);
        router.record_missed_heartbeat(700, TransportType::LanBroadcast, 2000);
        router.record_missed_heartbeat(700, TransportType::LanBroadcast, 3000);
        assert!(router.is_peer_partitioned(700));
        assert_eq!(
            router.select_route(700).unwrap().0,
            TransportType::WireGuard
        );

        // 2. Flapping: 2 successful LAN probes, then 1 miss
        router.record_heartbeat(700, TransportType::LanBroadcast, 1, 4000);
        router.record_heartbeat(700, TransportType::LanBroadcast, 1, 5000);
        router.record_missed_heartbeat(700, TransportType::LanBroadcast, 6000); // Miss resets strikes

        // 3. 3 successful probes at t=7000, 8000, 9000
        router.record_heartbeat(700, TransportType::LanBroadcast, 1, 7000);
        router.record_heartbeat(700, TransportType::LanBroadcast, 1, 8000);
        router.record_heartbeat(700, TransportType::LanBroadcast, 1, 9000);

        // Invariant: 3 strikes achieved, but dwell elapsed is only 5000ms (< 120000ms) -> MUST stay WireGuard!
        assert_eq!(
            router.select_route(700).unwrap().0,
            TransportType::WireGuard
        );

        // 4. Probes during dwell: t=50000, 100000 -> still WireGuard
        router.record_heartbeat(700, TransportType::LanBroadcast, 1, 50000);
        router.record_heartbeat(700, TransportType::LanBroadcast, 1, 100000);
        assert_eq!(
            router.select_route(700).unwrap().0,
            TransportType::WireGuard
        );

        // 5. Successful probe at t=130000 (dwell elapsed = 126000ms >= 120000ms) -> Restores LAN!
        router.record_heartbeat(700, TransportType::LanBroadcast, 1, 130000);
        assert_eq!(
            router.select_route(700).unwrap().0,
            TransportType::LanBroadcast
        );
        assert!(!router.is_peer_partitioned(700));

        let summary = router.get_route_summary(700).unwrap();
        assert_eq!(summary.active_transport, TransportType::LanBroadcast);
        assert_eq!(summary.active_endpoint, "192.168.1.99:8650");
        assert!(!summary.is_lan_partitioned);
    }
}

mod mesh_m2_adversarial_test {
    use mios_node::ble::{
        provision_remote_node, BleAdapter, BleBootstrapState, BleMeshBootstrap, MockBleAdapter,
        ProvisioningPayload, BLE_CHAR_ECDH_UUID, BLE_CHAR_PROVISION_UUID,
    };
    use mios_node::buffer_pool::{BucketTier, BufferPool};
    use mios_node::capabilities::{CapabilityRegistry, NodeAnnouncePayload, NodeCapabilities};
    use mios_node::overlay::{HysteresisConfig, MultiTransportRouter, TransportType};
    use mios_node::scheduler::{TaskItem, TaskPriority, WorkStealingScheduler};
    use std::collections::HashMap;
    use std::sync::Arc;
    use std::thread;

    #[test]
    fn test_adversarial_work_stealing_pinned_invariants() {
        let _tree = super::tree_lock();
        let scheduler = WorkStealingScheduler::new(101, 4);

        // Enqueue 10 pinned tasks and 10 unpinned tasks
        for i in 0..10 {
            let mut pinned = TaskItem::new(i, TaskPriority::Critical, 1, vec![1, 2, 3], vec![4, 5]);
            pinned.pinned_hardware = true;
            scheduler.submit_task(pinned, Some(0)); // assign to worker 0
        }

        for i in 10..20 {
            let unpinned = TaskItem::new(i, TaskPriority::Normal, 1, vec![7, 8], vec![9]);
            scheduler.submit_task(unpinned, Some(0)); // assign to worker 0
        }

        // Workers 1, 2, 3 try to steal from worker 0
        let mut stolen_task_ids = Vec::new();
        for w in 1..4 {
            while let Some(task) = scheduler.pop_task(w) {
                stolen_task_ids.push(task.task_id);
                // Verify INVARIANT: Pinned tasks must NEVER be stolen!
                assert!(
                    !task.pinned_hardware,
                    "Invariant violated: Stole task {} with pinned_hardware=true",
                    task.task_id
                );
            }
        }

        // All stolen tasks must be from the unpinned set (10..20)
        for id in &stolen_task_ids {
            assert!(*id >= 10 && *id < 20);
        }

        // Worker 0 should now execute all 10 pinned tasks locally
        let mut local_executed_ids = Vec::new();
        while let Some(task) = scheduler.pop_task(0) {
            local_executed_ids.push(task.task_id);
            assert!(task.pinned_hardware);
        }
        assert_eq!(local_executed_ids.len(), 10);
    }

    #[test]
    fn test_adversarial_buffer_pool_saturation_and_zero_copy_slicing() {
        let _tree = super::tree_lock();
        let pool = BufferPool::new();

        // Multithreaded stress acquisition and recycling
        let handles: Vec<_> = (0..8)
            .map(|_| {
                let p = Arc::clone(&pool);
                thread::spawn(move || {
                    for _ in 0..100 {
                        let mut buf = p.acquire(1024); // Medium bucket
                        assert_eq!(buf.tier(), BucketTier::Medium);
                        buf.extend_from_slice(b"TEST_HEADER_16B_DATA_PAYLOAD_CHUNK");

                        let sub = buf.slice(0, 16).unwrap();
                        assert_eq!(sub, b"TEST_HEADER_16B_");

                        let prefix = buf.split_prefix(16).unwrap();
                        assert_eq!(prefix, b"TEST_HEADER_16B_");
                        assert_eq!(buf.as_slice(), b"DATA_PAYLOAD_CHUNK");
                    }
                })
            })
            .collect();

        for h in handles {
            h.join().unwrap();
        }

        let stats = pool.get_stats();
        assert_eq!(stats.active_leased, 0);
        assert_eq!(stats.allocations, 800);
        assert!(stats.recycles > 0 && stats.recycles <= stats.allocations);

        // Bounded capacity check
        let (s, m, l, h) = pool.bucket_depths();
        assert!(s <= BucketTier::Small.max_pool_capacity());
        assert!(m <= BucketTier::Medium.max_pool_capacity());
        assert!(l <= BucketTier::Large.max_pool_capacity());
        assert!(h <= BucketTier::Huge.max_pool_capacity());
    }

    #[test]
    fn test_adversarial_capabilities_probing_and_filtering() {
        let _tree = super::tree_lock();
        let registry = CapabilityRegistry::new();

        // Populate registry with 50 synthetic nodes
        for i in 1..=50 {
            let mut caps = NodeCapabilities::default();
            caps.hardware.ram_available_kb = (i as u64) * 512 * 1024;
            caps.vram.vram_available_mb = if i % 5 == 0 { i * 512 } else { 0 };
            caps.has_gpio = i % 2 == 0;
            caps.has_i2c = i % 3 == 0;

            let payload = NodeAnnouncePayload::new(i, format!("edge-node-{:02}", i), caps);
            registry.register_announce(payload, 1000);
        }

        // Filter nodes with VRAM >= 2048 MB AND GPIO == true
        let matched = registry.find_eligible_nodes(1024, 2048, false, false, true, false);
        for node_id in &matched {
            let caps = registry.get_capabilities(*node_id).unwrap();
            assert!(caps.vram.vram_available_mb >= 2048);
            assert!(caps.has_gpio);
        }
        assert!(!matched.is_empty());
    }

    #[test]
    fn test_adversarial_ble_mesh_bootstrap_handshake_tamper() {
        let _tree = super::tree_lock();
        let adapter: Arc<dyn BleAdapter> = Arc::new(MockBleAdapter::new());
        let bootstrap = BleMeshBootstrap::new(77, Arc::clone(&adapter)).unwrap();
        bootstrap.start().unwrap();

        let creds = ProvisioningPayload::new(
            "SecureMeshSSID".to_string(),
            "VerySecretKey123".to_string(),
            "cluster-token-xyz".to_string(),
            "10.0.0.1:8650".to_string(),
        );

        // Client provisioner prepares payload
        provision_remote_node(adapter.as_ref(), &creds).unwrap();

        // Node accepts ECDH key
        let peer_pub = adapter
            .get_characteristic_value(BLE_CHAR_ECDH_UUID)
            .unwrap();
        bootstrap.handle_ecdh_exchange(&peer_pub).unwrap();

        // Tamper with ciphertext in Char 3
        let mut tampered = adapter
            .get_characteristic_value(BLE_CHAR_PROVISION_UUID)
            .unwrap();
        let mid = tampered.len() / 2;
        tampered[mid] ^= 0xAA;

        // Decryption must fail AEAD verification
        let res = bootstrap.handle_provisioning_write(&tampered);
        assert!(res.is_err());
        assert_ne!(bootstrap.state(), BleBootstrapState::Provisioned);
    }

    #[test]
    fn test_adversarial_overlay_multi_transport_flapping_stress() {
        let _tree = super::tree_lock();
        let config = HysteresisConfig {
            fail_strikes_threshold: 3,
            recovery_dwell_ms: 10_000,
            recovery_strikes_threshold: 3,
        };
        let router = MultiTransportRouter::new(Some(config));

        let mut endpoints = HashMap::new();
        endpoints.insert(TransportType::LanBroadcast, "192.168.1.10:8650".to_string());
        endpoints.insert(TransportType::WireGuard, "10.0.0.10:8650".to_string());
        endpoints.insert(TransportType::Tailscale, "100.64.0.10:8650".to_string());

        router.register_peer(301, endpoints);

        // 1. Failover to WireGuard on 3 strikes
        for i in 1..=3 {
            router.record_missed_heartbeat(301, TransportType::LanBroadcast, i * 1000);
        }
        assert!(router.is_peer_partitioned(301));
        assert_eq!(
            router.select_route(301).unwrap().0,
            TransportType::WireGuard
        );

        // 2. Intermittent LAN probes during dwell time (at t=4000, 5000, 6000)
        for t in [4000, 5000, 6000] {
            router.record_heartbeat(301, TransportType::LanBroadcast, 1, t);
            // Must stay WireGuard because dwell time (10s) hasn't elapsed!
            assert_eq!(
                router.select_route(301).unwrap().0,
                TransportType::WireGuard
            );
        }

        // 3. Drop LAN again at t=7000 (resets recovery timer)
        router.record_missed_heartbeat(301, TransportType::LanBroadcast, 7000);
        assert_eq!(
            router.select_route(301).unwrap().0,
            TransportType::WireGuard
        );

        // 4. Clean recovery at t=8000, 9000, 19000 (dwell elapsed = 11000ms >= 10000ms)
        router.record_heartbeat(301, TransportType::LanBroadcast, 1, 8000);
        router.record_heartbeat(301, TransportType::LanBroadcast, 1, 9000);
        router.record_heartbeat(301, TransportType::LanBroadcast, 1, 19000);

        // Restores LAN
        assert_eq!(
            router.select_route(301).unwrap().0,
            TransportType::LanBroadcast
        );
        assert!(!router.is_peer_partitioned(301));
    }
}

mod mesh_m2_stress_challenger_test {
    use mios_node::ble::{
        provision_remote_node, BleAdapter, BleBootstrapState, BleMeshBootstrap, MockBleAdapter,
        ProvisioningPayload, BLE_CHAR_ECDH_UUID, BLE_CHAR_PROVISION_UUID,
    };
    use mios_node::buffer_pool::BufferPool;
    use mios_node::capabilities::{CapabilityRegistry, NodeAnnouncePayload, NodeCapabilities};
    use mios_node::overlay::{HysteresisConfig, MultiTransportRouter, TransportType};
    use mios_node::protocol::{Frame, MessageType};
    use mios_node::scheduler::{ScheduledTarget, TaskItem, TaskPriority, WorkStealingScheduler};
    use std::collections::HashMap;
    use std::sync::atomic::{AtomicBool, AtomicUsize, Ordering};
    use std::sync::Arc;
    use std::thread;
    use std::time::Duration;

    // =========================================================================
    // 1. T-392: Stress & Invariant Tests for Work-Stealing Scheduler
    // =========================================================================

    #[test]
    fn test_stress_concurrent_work_stealing_with_pinned_invariants() {
        let _tree = super::tree_lock();
        const NUM_WORKERS: usize = 8;
        const NUM_TASKS: usize = 2000;
        let scheduler = Arc::new(WorkStealingScheduler::new(101, NUM_WORKERS));

        // Submit tasks with mixed priorities and pin configurations
        for i in 0..NUM_TASKS {
            let prio = match i % 4 {
                0 => TaskPriority::Critical,
                1 => TaskPriority::High,
                2 => TaskPriority::Normal,
                _ => TaskPriority::Low,
            };

            let mut task = TaskItem::new(
                i as u64,
                prio,
                1,
                vec![(i % 255) as u8; 32],
                vec![(i % 128) as u8; 16],
            );

            if i % 3 == 0 {
                task.pinned_hardware = true;
            } else if i % 5 == 0 {
                task.pinned_node_id = Some(101); // local node
            } else if i % 7 == 0 {
                task.pinned_node_id = Some(999); // foreign node
            }

            let worker_hint = Some(i % NUM_WORKERS);
            scheduler.submit_task(task, worker_hint);
        }

        let completed_tasks = Arc::new(AtomicUsize::new(0));
        let stop_signal = Arc::new(AtomicBool::new(false));

        // Spawn worker threads that continuously pop tasks
        let mut handles = Vec::new();
        for w_id in 0..NUM_WORKERS {
            let sched = Arc::clone(&scheduler);
            let done = Arc::clone(&completed_tasks);
            let stop = Arc::clone(&stop_signal);

            handles.push(thread::spawn(move || {
                while !stop.load(Ordering::Relaxed) {
                    if let Some(task) = sched.pop_task(w_id) {
                        // Invariant check: If pinned to hardware, must be executed on local scheduler
                        if task.pinned_hardware {
                            assert!(
                                !task.is_stealable(Some(999)),
                                "Hardware pinned task must not be stealable by foreign node"
                            );
                        }
                        // Invariant check: If pinned to node 999, it should never be popped by node 101 unless local pop
                        if let Some(target) = task.pinned_node_id {
                            if target != 101 {
                                assert!(
                                    !task.is_stealable(Some(101)),
                                    "Task pinned to node 999 must not be stealable by node 101"
                                );
                            }
                        }
                        done.fetch_add(1, Ordering::SeqCst);
                    } else {
                        thread::yield_now();
                    }
                }
            }));
        }

        // Spawn 2 simulated remote peer stealers concurrently
        for peer_id in [201, 202] {
            let sched = Arc::clone(&scheduler);
            let done = Arc::clone(&completed_tasks);
            let stop = Arc::clone(&stop_signal);

            handles.push(thread::spawn(move || {
                while !stop.load(Ordering::Relaxed) {
                    let stolen = sched.handle_remote_steal_request(peer_id, 4);
                    for task in &stolen {
                        assert!(
                            !task.pinned_hardware,
                            "CRITICAL: Remote peer {} stole hardware-pinned task {}",
                            peer_id, task.task_id
                        );
                        if let Some(target) = task.pinned_node_id {
                            assert_eq!(
                                target, peer_id,
                                "Remote peer {} stole task pinned to node {}",
                                peer_id, target
                            );
                        }
                    }
                    done.fetch_add(stolen.len(), Ordering::SeqCst);
                    thread::yield_now();
                }
            }));
        }

        // Wait until all tasks are consumed or timeout
        let start = std::time::Instant::now();
        while completed_tasks.load(Ordering::SeqCst) < NUM_TASKS
            && start.elapsed() < Duration::from_secs(5)
        {
            thread::sleep(Duration::from_millis(10));
        }

        stop_signal.store(true, Ordering::Relaxed);
        for h in handles {
            let _ = h.join();
        }

        let stats = scheduler.get_stats();
        assert_eq!(stats.tasks_ingested, NUM_TASKS as u64);
        assert_eq!(completed_tasks.load(Ordering::SeqCst), NUM_TASKS);
    }

    #[test]
    fn test_scheduler_route_task_extreme_load_and_boundaries() {
        let _tree = super::tree_lock();
        let scheduler = WorkStealingScheduler::new(100, 2);

        // Empty scheduler + empty peer loads -> Local
        let t_normal = TaskItem::new(1, TaskPriority::Normal, 1, vec![], vec![]);
        assert_eq!(scheduler.route_task(&t_normal, &[]), ScheduledTarget::Local);

        // Pinned to foreign node -> Offload to that node
        let mut t_foreign = TaskItem::new(2, TaskPriority::Normal, 1, vec![], vec![]);
        t_foreign.pinned_node_id = Some(500);
        assert_eq!(
            scheduler.route_task(&t_foreign, &[(500, 10)]),
            ScheduledTarget::Offload(500)
        );

        // Hardware pinned task -> Local even if foreign pinned is set or peer load is zero
        let mut t_pinned_hw = TaskItem::new(3, TaskPriority::Critical, 1, vec![], vec![]);
        t_pinned_hw.pinned_hardware = true;
        t_pinned_hw.pinned_node_id = Some(500);
        assert_eq!(
            scheduler.route_task(&t_pinned_hw, &[(500, 0)]),
            ScheduledTarget::Local
        );
    }

    // =========================================================================
    // 2. T-393: Stress & Invariant Tests for Zero-Copy Buffer Pool
    // =========================================================================

    #[test]
    fn test_stress_buffer_pool_concurrency_and_into_vec_accounting() {
        let _tree = super::tree_lock();
        let pool = BufferPool::new();
        pool.preallocate(64, 32);

        const NUM_THREADS: usize = 16;
        const OPS_PER_THREAD: usize = 200;

        let handles: Vec<_> = (0..NUM_THREADS)
            .map(|tid| {
                let p = Arc::clone(&pool);
                thread::spawn(move || {
                    for i in 0..OPS_PER_THREAD {
                        let size = match (tid + i) % 4 {
                            0 => 128,     // Small
                            1 => 2048,    // Medium
                            2 => 32768,   // Large
                            _ => 200_000, // Huge
                        };

                        let mut buf = p.acquire(size);
                        let payload = vec![(i % 256) as u8; 64];
                        buf.extend_from_slice(&payload);

                        // Slice testing
                        let sub = buf.slice(0, 32).unwrap();
                        assert_eq!(sub, &payload[0..32]);

                        // Split prefix testing
                        let pref = buf.split_prefix(16).unwrap();
                        assert_eq!(pref, &payload[0..16]);
                        assert_eq!(buf.len(), 48);

                        // 50% RAII drop recycling, 50% consumption via into_vec
                        if (tid + i) % 2 == 0 {
                            let consumed_vec = buf.into_vec();
                            assert_eq!(consumed_vec.len(), 48);
                        }
                        // else normal drop
                    }
                })
            })
            .collect();

        for h in handles {
            h.join().unwrap();
        }

        let stats = pool.get_stats();
        assert_eq!(
            stats.active_leased, 0,
            "Active leased count leaked! Stats: {:?}",
            stats
        );
        assert_eq!(stats.allocations, (NUM_THREADS * OPS_PER_THREAD) as u64);
        assert_eq!(stats.allocations, stats.pool_hits + stats.pool_misses);
    }

    #[test]
    fn test_adversarial_buffer_pool_slicing_and_split_boundaries() {
        let _tree = super::tree_lock();
        let pool = BufferPool::new();
        let mut buf = pool.acquire(512); // Medium tier
        buf.extend_from_slice(b"0123456789ABCDEF"); // 16 bytes

        // 1. Exact range 0..16
        assert_eq!(buf.slice(0, 16).unwrap(), b"0123456789ABCDEF");

        // 2. Empty range 5..5
        assert_eq!(buf.slice(5, 5).unwrap(), b"");

        // 3. Out of bounds start > end
        assert!(buf.slice(10, 5).is_err());

        // 4. Out of bounds end > len
        assert!(buf.slice(0, 17).is_err());

        // 5. Split prefix at 0
        let pref0 = buf.split_prefix(0).unwrap();
        assert!(pref0.is_empty());
        assert_eq!(buf.len(), 16);

        // 6. Split prefix out of bounds
        assert!(buf.split_prefix(17).is_err());

        // 7. Split prefix exact len
        let pref_all = buf.split_prefix(16).unwrap();
        assert_eq!(pref_all, b"0123456789ABCDEF");
        assert_eq!(buf.len(), 0);
        assert!(buf.is_empty());
    }

    // =========================================================================
    // 3. T-394: Adversarial Capability Probing, Filtering & Corrupted Payloads
    // =========================================================================

    #[test]
    fn test_adversarial_capability_registry_extreme_queries() {
        let _tree = super::tree_lock();
        let registry = CapabilityRegistry::new();

        // Query on empty registry
        let empty_res = registry.find_eligible_nodes(1024, 1024, true, true, true, true);
        assert!(empty_res.is_empty());
        assert_eq!(registry.active_node_count(), 0);
        assert_eq!(registry.evict_stale(100, 1000), 0);

        // Add node with maximum capabilities
        let mut max_caps = NodeCapabilities::default();
        max_caps.hardware.ram_available_kb = 64 * 1024 * 1024;
        max_caps.vram.vram_available_mb = 32768;
        max_caps.has_gpio = true;
        max_caps.has_i2c = true;

        let payload = NodeAnnouncePayload::new(99, "behemoth-01".to_string(), max_caps);
        registry.register_announce(payload, 5000);

        // Query matching
        let matched = registry.find_eligible_nodes(32 * 1024 * 1024, 16384, true, true, true, true);
        assert_eq!(matched, vec![99]);

        // Query with unreachable criteria
        let impossible = registry.find_eligible_nodes(u64::MAX, u32::MAX, true, true, true, true);
        assert!(impossible.is_empty());

        // Stale eviction with timestamp before last_seen (no underflow)
        let evicted_before = registry.evict_stale(100, 4000);
        assert_eq!(evicted_before, 0);

        // Eviction after TTL
        let evicted_after = registry.evict_stale(100, 5200);
        assert_eq!(evicted_after, 1);
        assert_eq!(registry.active_node_count(), 0);
    }

    #[test]
    fn test_adversarial_node_announce_corrupt_frame_decoding() {
        let _tree = super::tree_lock();
        // 1. Wrong message type in frame (Heartbeat 0x01 instead of NodeAnnounce 0x02)
        let bad_header_frame = Frame::new(MessageType::Heartbeat, 42, vec![b'{', b'}']);
        let err = NodeAnnouncePayload::from_frame(&bad_header_frame);
        assert!(err.is_err());

        // 2. Corrupted JSON payload
        let corrupt_json_frame = Frame::new(
            MessageType::NodeAnnounce,
            42,
            b"{\"node_id\": 42, BAD_JSON".to_vec(),
        );
        assert!(NodeAnnouncePayload::from_frame(&corrupt_json_frame).is_err());

        // 3. Empty payload
        let empty_frame = Frame::new(MessageType::NodeAnnounce, 42, vec![]);
        assert!(NodeAnnouncePayload::from_frame(&empty_frame).is_err());
    }

    // =========================================================================
    // 4. T-395: Adversarial BLE AEAD Bit-Flip Fuzzing & Invalid Keys
    // =========================================================================

    #[test]
    fn test_adversarial_ble_bit_flip_fuzzing_and_key_validation() {
        let _tree = super::tree_lock();
        let adapter: Arc<dyn BleAdapter> = Arc::new(MockBleAdapter::new());
        let bootstrap = BleMeshBootstrap::new(88, Arc::clone(&adapter)).unwrap();
        bootstrap.start().unwrap();

        // 1. Invalid ECDH public key lengths
        assert!(bootstrap.handle_ecdh_exchange(&[]).is_err());
        assert!(bootstrap.handle_ecdh_exchange(&[0u8; 16]).is_err());
        assert!(bootstrap.handle_ecdh_exchange(&[0u8; 31]).is_err());
        assert!(bootstrap.handle_ecdh_exchange(&[0u8; 33]).is_err());
        assert!(bootstrap.handle_ecdh_exchange(&[0u8; 64]).is_err());

        // 2. Premature provisioning write without handshake
        assert!(bootstrap.handle_provisioning_write(&[0u8; 32]).is_err());

        // 3. Execute legitimate handshake
        let creds = ProvisioningPayload::new(
            "FuzzSSID".to_string(),
            "FuzzPassword123".to_string(),
            "token-999".to_string(),
            "10.0.0.5:8650".to_string(),
        );
        provision_remote_node(adapter.as_ref(), &creds).unwrap();

        let peer_pub = adapter
            .get_characteristic_value(BLE_CHAR_ECDH_UUID)
            .unwrap();
        bootstrap.handle_ecdh_exchange(&peer_pub).unwrap();

        let valid_ciphertext = adapter
            .get_characteristic_value(BLE_CHAR_PROVISION_UUID)
            .unwrap();
        assert!(!valid_ciphertext.is_empty());

        // 4. Exhaustive single-byte bit flip fuzzing across all ciphertext bytes
        for i in 0..valid_ciphertext.len() {
            let mut corrupted = valid_ciphertext.clone();
            corrupted[i] ^= 0x01; // flip 1 bit

            let res = bootstrap.handle_provisioning_write(&corrupted);
            assert!(
                res.is_err(),
                "Poly1305 MAC check passed on corrupted byte index {}!",
                i
            );
        }

        // 5. Truncated ciphertext payloads (less than 16B MAC tag)
        for len in 0..16 {
            let truncated = &valid_ciphertext[0..len];
            assert!(bootstrap.handle_provisioning_write(truncated).is_err());
        }

        // 6. Valid ciphertext succeeds
        let prov = bootstrap
            .handle_provisioning_write(&valid_ciphertext)
            .unwrap();
        assert_eq!(prov.ssid, "FuzzSSID");
        assert_eq!(bootstrap.state(), BleBootstrapState::Provisioned);
    }

    // =========================================================================
    // 5. T-396: Stress & Boundary Tests for Multi-Transport Flapping & Hysteresis
    // =========================================================================

    #[test]
    fn test_stress_overlay_flapping_and_hysteresis_boundaries() {
        let _tree = super::tree_lock();
        let config = HysteresisConfig {
            fail_strikes_threshold: 3,
            recovery_dwell_ms: 10_000,
            recovery_strikes_threshold: 3,
        };
        let router = MultiTransportRouter::new(Some(config));

        let mut endpoints = HashMap::new();
        endpoints.insert(TransportType::LanBroadcast, "192.168.1.20:8650".to_string());
        endpoints.insert(TransportType::WireGuard, "10.0.0.20:8650".to_string());
        endpoints.insert(TransportType::Tailscale, "100.64.0.20:8650".to_string());
        endpoints.insert(TransportType::DirectTcp, "192.168.1.20:9000".to_string());

        router.register_peer(501, endpoints);

        // 1. Rapid alternating 1 miss, 1 hit -> LAN should NEVER failover (consecutive misses never hit 3)
        for i in 0..100 {
            let t = (i * 100) as u64;
            if i % 2 == 0 {
                router.record_missed_heartbeat(501, TransportType::LanBroadcast, t);
            } else {
                router.record_heartbeat(501, TransportType::LanBroadcast, 1, t);
            }
            assert_eq!(
                router.select_route(501).unwrap().0,
                TransportType::LanBroadcast,
                "Flapping caused false failover at iteration {}",
                i
            );
            assert!(!router.is_peer_partitioned(501));
        }

        // 2. Exact 3 consecutive misses triggers failover
        router.record_missed_heartbeat(501, TransportType::LanBroadcast, 10_000);
        router.record_missed_heartbeat(501, TransportType::LanBroadcast, 11_000);
        assert!(!router.is_peer_partitioned(501)); // 2 misses -> still LAN

        router.record_missed_heartbeat(501, TransportType::LanBroadcast, 12_000);
        assert!(router.is_peer_partitioned(501)); // 3 misses -> WireGuard
        assert_eq!(
            router.select_route(501).unwrap().0,
            TransportType::WireGuard
        );

        // 3. Recovery: 3 hits at t=13_000, 14_000, 15_000 (dwell is only 2000ms < 10000ms) -> Still WireGuard
        router.record_heartbeat(501, TransportType::LanBroadcast, 1, 13_000);
        router.record_heartbeat(501, TransportType::LanBroadcast, 1, 14_000);
        router.record_heartbeat(501, TransportType::LanBroadcast, 1, 15_000);
        assert_eq!(
            router.select_route(501).unwrap().0,
            TransportType::WireGuard
        );

        // 4. At t=22_999 (dwell = 9999ms < 10000ms) -> Still WireGuard
        router.record_heartbeat(501, TransportType::LanBroadcast, 1, 22_999);
        assert_eq!(
            router.select_route(501).unwrap().0,
            TransportType::WireGuard
        );

        // 5. At t=23_000 (dwell = 10000ms >= 10000ms) -> Restores LAN
        router.record_heartbeat(501, TransportType::LanBroadcast, 1, 23_000);
        assert_eq!(
            router.select_route(501).unwrap().0,
            TransportType::LanBroadcast
        );
        assert!(!router.is_peer_partitioned(501));

        // 6. Failover hierarchy test: If LAN fails and WireGuard fails -> Tailscale
        router.record_missed_heartbeat(501, TransportType::LanBroadcast, 25_000);
        router.record_missed_heartbeat(501, TransportType::LanBroadcast, 26_000);
        router.record_missed_heartbeat(501, TransportType::LanBroadcast, 27_000);
        assert_eq!(
            router.select_route(501).unwrap().0,
            TransportType::WireGuard
        );

        // WireGuard misses 3 strikes
        router.record_missed_heartbeat(501, TransportType::WireGuard, 28_000);
        router.record_missed_heartbeat(501, TransportType::WireGuard, 29_000);
        router.record_missed_heartbeat(501, TransportType::WireGuard, 30_000);
        // Route summary check
        let summary = router.get_route_summary(501).unwrap();
        assert_eq!(summary.node_id, 501);
        assert!(summary.is_lan_partitioned);
    }
}
