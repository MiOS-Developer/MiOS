// AI-hint: Integration tests for the mios-node wire protocol, dual-tier executor and CRDT state sync, plus parity with the usr/libexec/mios/node Python twins (T-1188).
// AI-related: src/mios-rs/mios-node/src/lib.rs, src/mios-rs/mios-node/src/node.rs, usr/libexec/mios/node/

mod node_mesh_test {
    use anyhow::Result;
    use ed25519_dalek::{Signer, SigningKey};
    use mios_node::executor::ExecutionEngine;
    use mios_node::protocol::{Frame, Header, MessageType, TaskOffloadPayload, HEADER_SIZE};
    use mios_node::state_sync::StateStore;
    use std::sync::{Arc, Mutex};
    use tempfile::NamedTempFile;

    #[test]
    fn test_full_protocol_header_and_frame_validation() -> Result<()> {
        let header = Header::new(MessageType::Heartbeat, 202, 128, 0xABCDEF01);
        let mut header_buf = [0u8; HEADER_SIZE];
        header.encode(&mut header_buf)?;

        let decoded_header = Header::decode(&header_buf)?;
        assert_eq!(decoded_header.magic, 0x4D49);
        assert_eq!(decoded_header.version, 0x01);
        assert_eq!(decoded_header.msg_type, MessageType::Heartbeat);
        assert_eq!(decoded_header.node_id, 202);
        assert_eq!(decoded_header.payload_len, 128);
        assert_eq!(decoded_header.checksum, 0xABCDEF01);

        let payload = b"Distributed Edge Task Payload".to_vec();
        let frame = Frame::new(MessageType::TaskOffload, 202, payload.clone());

        let encoded_bytes = frame.encode()?;
        let decoded_frame = Frame::decode(&encoded_bytes)?;
        assert_eq!(decoded_frame.header.node_id, 202);
        assert_eq!(decoded_frame.payload, payload);

        Ok(())
    }

    #[test]
    fn test_protocol_invalid_header_rejection() {
        let mut buf = [0u8; HEADER_SIZE];
        // Invalid magic bytes
        buf[0] = 0xDE;
        buf[1] = 0xAD;

        let res = Header::decode(&buf);
        assert!(res.is_err());
        assert!(res.unwrap_err().to_string().contains("Invalid MiOS magic"));
    }

    #[test]
    fn test_tier1_wasm_sandbox_execution() -> Result<()> {
        let state_store = Arc::new(Mutex::new(StateStore::new(1)));
        let engine = ExecutionEngine::new(state_store.clone());

        let task_payload = TaskOffloadPayload {
            task_id: 5001,
            tier: 1,
            target_arch: 0,
            memory_limit_bytes: 32 * 1024 * 1024,
            execution_timeout_ms: 2000,
            code_bytes: b"WASM_INTERPRETER_BYTECODE".to_vec(),
            input_data: b"temperature=22.4".to_vec(),
            signature: None,
            public_key: None,
        };

        let result = engine.execute_task(&task_payload);
        assert!(result.success);
        assert_eq!(result.exit_code, 0);

        let store = state_store.lock().unwrap();
        assert_eq!(store.get("task.5001.status"), Some(&b"COMPLETED".to_vec()));

        Ok(())
    }

    #[test]
    fn test_tier2_native_ed25519_signature_verification() -> Result<()> {
        let state_store = Arc::new(Mutex::new(StateStore::new(2)));
        let engine = ExecutionEngine::new(state_store.clone());

        let secret_bytes = [42u8; 32];
        let signing_key = SigningKey::from_bytes(&secret_bytes);
        let verifying_key = signing_key.verifying_key();

        let code_binary = b"ELF_NATIVE_EXEC_MODULE".to_vec();
        let signature = signing_key.sign(&code_binary);

        let valid_payload = TaskOffloadPayload {
            task_id: 6001,
            tier: 2,
            target_arch: if cfg!(target_arch = "x86_64") { 1 } else { 0 },
            memory_limit_bytes: 64 * 1024 * 1024,
            execution_timeout_ms: 1000,
            code_bytes: code_binary.clone(),
            input_data: Vec::new(),
            signature: Some(signature.to_bytes().to_vec()),
            public_key: Some(verifying_key.to_bytes().to_vec()),
        };

        let valid_result = engine.execute_task(&valid_payload);
        assert!(valid_result.success);

        let mut tampered_code = code_binary.clone();
        tampered_code[0] ^= 0xFF;

        let invalid_payload = TaskOffloadPayload {
            task_id: 6002,
            tier: 2,
            target_arch: if cfg!(target_arch = "x86_64") { 1 } else { 0 },
            memory_limit_bytes: 64 * 1024 * 1024,
            execution_timeout_ms: 1000,
            code_bytes: tampered_code,
            input_data: Vec::new(),
            signature: Some(signature.to_bytes().to_vec()),
            public_key: Some(verifying_key.to_bytes().to_vec()),
        };

        let invalid_result = engine.execute_task(&invalid_payload);
        assert!(!invalid_result.success);
        assert!(invalid_result
            .error_msg
            .unwrap()
            .contains("verification failed"));

        Ok(())
    }

    #[test]
    fn test_crdt_tombstone_deletion_and_convergence() -> Result<()> {
        let mut node_1 = StateStore::new(101);
        let mut node_2 = StateStore::new(102);

        node_1.set("network.domain".to_string(), b"mios.local".to_vec());
        node_2.merge_remote_store(node_1.vector_clock.clone(), node_1.replicable_elements());
        assert_eq!(node_2.get("network.domain"), Some(&b"mios.local".to_vec()));

        // Node 1 deletes the key
        node_1.delete("network.domain");
        assert_eq!(node_1.get("network.domain"), None);

        // Merge deletion tombstone into Node 2
        node_2.merge_remote_store(node_1.vector_clock.clone(), node_1.replicable_elements());
        assert_eq!(node_2.get("network.domain"), None);

        Ok(())
    }

    #[test]
    fn test_crdt_multi_node_state_convergence_and_persistence() -> Result<()> {
        let mut node_1 = StateStore::new(101);
        let mut node_2 = StateStore::new(102);

        node_1.set("network.domain".to_string(), b"mios.local".to_vec());
        node_2.set("network.domain".to_string(), b"mios.mesh".to_vec());

        node_2.merge_remote_store(node_1.vector_clock.clone(), node_1.replicable_elements());
        assert!(node_2.get("network.domain").is_some());

        let tmp = NamedTempFile::new()?;
        let path = tmp.path().to_str().unwrap();

        node_2.save_to_disk(path)?;
        let restored = StateStore::load_from_disk(path, 102)?;

        assert_eq!(restored.get("network.domain"), node_2.get("network.domain"));

        Ok(())
    }
}

mod parity_stubs_test {
    use anyhow::Result;
    use mios_node::ble::{BleMeshBootstrap, MockBleAdapter};
    use mios_node::capabilities::{
        probe_node_capabilities, CapabilityRegistry, NodeAnnouncePayload,
    };
    use mios_node::cgroups::{CgroupV2Controller, NodeResourceLimits};
    use mios_node::crypto::{CryptoHandshake, NodeIdentity};
    use mios_node::executor::ExecutionEngine;
    use mios_node::hardware::{
        HardwareAllowlist, HardwareDriver, LinuxSysfsHardwareDriver, MockHardwareDriver,
        SandboxedHardwareController,
    };
    use mios_node::heartbeat::{HeartbeatMonitor, PeerHealth};
    use mios_node::protocol::{Frame, MessageType};
    use mios_node::scheduler::TaskPriority;
    use mios_node::state_sync::StateStore;
    use mios_node::watchdog::{LinuxHardwareWatchdog, MockWatchdogDriver, WatchdogDriver};
    use std::net::SocketAddr;
    use std::sync::{Arc, Mutex};
    use tempfile::tempdir;

    #[test]
    fn test_parity_watchdog_timeout_stubs() {
        // Parity twin: usr/libexec/mios/node/watchdog.py (set_timeout, get_timeout)
        let mut mock = MockWatchdogDriver::new(true, 30);
        assert_eq!(mock.get_timeout().unwrap(), 30);
        assert_eq!(mock.set_timeout(45).unwrap(), 45);
        assert_eq!(mock.get_timeout().unwrap(), 45);

        let mut linux_wd = LinuxHardwareWatchdog::new("/dev/watchdog_nonexistent", 30);
        assert_eq!(linux_wd.get_timeout().unwrap(), 30);
        assert_eq!(linux_wd.set_timeout(60).unwrap(), 60);
        assert_eq!(linux_wd.get_timeout().unwrap(), 60);
    }

    #[test]
    fn test_parity_capabilities_stubs() {
        // Parity twin: usr/libexec/mios/node/capabilities.py (probe_node_capabilities, get_announce)
        let caps = probe_node_capabilities();
        let registry = CapabilityRegistry::new();
        let announce = NodeAnnouncePayload::new(42, "node-42".to_string(), caps);
        registry.register_announce(announce.clone(), 1000);
        let retrieved = registry.get_announce(42);
        assert!(retrieved.is_some());
        assert_eq!(retrieved.unwrap().node_id, 42);
        assert!(registry.get_announce(999).is_none());
    }

    #[test]
    fn test_parity_cgroups_stubs() -> Result<()> {
        // Parity twin: usr/libexec/mios/node/cgroups.py (apply_limits, attach_pid)
        let dir = tempdir()?;
        let controller = CgroupV2Controller::new(dir.path().to_str().unwrap());
        let limits = NodeResourceLimits {
            cpu_quota_pct: Some(50),
            cpu_period_us: 100_000,
            memory_high_bytes: Some(1024 * 1024 * 64),
            memory_max_bytes: Some(1024 * 1024 * 128),
            ..Default::default()
        };
        controller
            .apply_limits(&limits)
            .map_err(|e| anyhow::anyhow!(e))?;

        let procs_file = dir.path().join("cgroup.procs");
        std::fs::write(&procs_file, "")?;
        controller
            .attach_pid(1234)
            .map_err(|e| anyhow::anyhow!(e))?;
        let content = std::fs::read_to_string(&procs_file)?;
        assert_eq!(content.trim(), "1234");
        Ok(())
    }

    #[test]
    fn test_parity_heartbeat_stubs() {
        // Parity twin: usr/libexec/mios/node/discovery.py (with_thresholds, record_announce, assess_peer_health, evict_peer, active_peers)
        let mut monitor = HeartbeatMonitor::with_thresholds(1, 5, 10, 20);
        let addr: SocketAddr = "127.0.0.1:8000".parse().unwrap();
        monitor.record_announce(2, addr, 100);

        let active = monitor.active_peers();
        assert_eq!(active.len(), 1);
        assert_eq!(active[0].node_id, 2);

        let (health, strikes) = monitor.assess_peer_health(12);
        assert_eq!(health, PeerHealth::Degraded);
        assert_eq!(strikes, 2);

        let eviction = monitor.evict_peer(2, "manual eviction", 150);
        assert!(eviction.is_some());
        assert_eq!(eviction.unwrap().node_id, 2);
        assert_eq!(monitor.active_peers().len(), 0);
    }

    #[test]
    fn test_parity_crypto_session_cipher_frame_stubs() -> Result<()> {
        // Parity twin: usr/libexec/mios/node/crypto.py (encrypt_frame, decrypt_frame)
        let id_a = NodeIdentity::from_bytes(101, &[1u8; 32]);
        let id_b = NodeIdentity::from_bytes(202, &[2u8; 32]);
        let eph_a = [3u8; 32];
        let eph_b = [4u8; 32];

        let init = CryptoHandshake::create_init(&id_a, &eph_a);
        let (resp, mut session_b) =
            CryptoHandshake::process_init_and_respond(&id_b, &eph_b, &init)?;
        let mut session_a = CryptoHandshake::finalize_init(&id_a, &eph_a, &resp)?;

        let frame = Frame::new(
            MessageType::Heartbeat,
            101,
            b"Payload for parity frame".to_vec(),
        );
        let encrypted = session_a.encrypt_frame(&frame)?;
        let decrypted = session_b.decrypt_frame(&encrypted)?;

        assert_eq!(decrypted.header.msg_type, MessageType::Heartbeat);
        assert_eq!(decrypted.payload, b"Payload for parity frame");
        Ok(())
    }

    #[test]
    fn test_parity_executor_hardware_stubs() {
        // Parity twin: usr/libexec/mios/node/hardware.py (with_hardware, hardware_controller)
        let state_store = Arc::new(Mutex::new(StateStore::new(1)));
        let (hw, _) = SandboxedHardwareController::new_mock(HardwareAllowlist::default());
        let hw = Arc::new(hw);
        let engine = ExecutionEngine::with_hardware(state_store, hw.clone());
        let hw_ref = engine.hardware_controller();
        assert!(Arc::ptr_eq(hw_ref, &hw));
    }

    #[test]
    fn test_parity_hardware_driver_stubs() {
        // Parity twin: usr/libexec/mios/node/hardware.py (with_custom_roots, get_mock_i2c_register)
        let mock_hw = MockHardwareDriver::new();
        mock_hw.set_mock_i2c_register(1, 0x48, 0x05, 42);
        assert_eq!(mock_hw.get_mock_i2c_register(1, 0x48, 0x05), Some(42));
        assert_eq!(mock_hw.get_mock_i2c_register(1, 0x48, 0x06), None);

        let sysfs =
            LinuxSysfsHardwareDriver::with_custom_roots("/custom/sys/gpio", "/custom/dev/i2c");
        let read_res = sysfs.gpio_read(18);
        assert!(read_res.is_err());
    }

    #[test]
    fn test_parity_ble_and_scheduler_stubs() {
        // Parity twin: usr/libexec/mios/node/ble.py (get_credentials)
        let adapter = Arc::new(MockBleAdapter::new());
        let ble_session = BleMeshBootstrap::new(101, adapter).unwrap();
        assert!(ble_session.get_credentials().is_none());

        // Parity twin: usr/libexec/mios/node/scheduler.py (TaskPriority.as_u8)
        assert_eq!(TaskPriority::Critical.as_u8(), 0);
        assert_eq!(TaskPriority::High.as_u8(), 1);
        assert_eq!(TaskPriority::Normal.as_u8(), 2);
        assert_eq!(TaskPriority::Low.as_u8(), 3);
    }
}
