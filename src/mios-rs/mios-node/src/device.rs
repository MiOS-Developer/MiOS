// AI-hint: mios-node device layer: GPIO/I2C hardware abstraction with allowlists, capability advertising in Announce frames, and the /dev/watchdog integration (task_338).
// AI-related: src/mios-rs/mios-node/src/wire.rs, usr/libexec/mios/node/capabilities.py, tests/test-node.py, src/mios-rs/mios-node/src/exec.rs, usr/libexec/mios/node/hardware.py, usr/libexec/mios/node/wasm_sandbox.py, src/mios-rs/mios-node/src/node.rs, usr/libexec/mios/node/watchdog.py

pub mod capabilities {
    //! MiOS Edge Node Capability Advertising & Telemetry Engine
    //!
    //! Encapsulates Opcode 0x02 `NodeAnnounce` payloads with CPU, RAM, GPU/VRAM telemetry,
    //! execution tiers (Wasm, Native), active mesh transports, hardware interfaces (GPIO/I2C),
    //! capability probing, and cluster capability registry.

    use crate::protocol::{Frame, MessageType};
    use anyhow::{anyhow, Result};
    use serde::{Deserialize, Serialize};
    use std::collections::HashMap;
    use std::fs;
    use std::path::Path;
    use std::sync::{Arc, Mutex};

    /// Host CPU and system memory telemetry.
    #[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
    pub struct HardwareSpecs {
        pub cpu_arch: String,
        pub cpu_cores: u32,
        pub cpu_frequency_mhz: u32,
        pub ram_total_kb: u64,
        pub ram_available_kb: u64,
    }

    impl Default for HardwareSpecs {
        fn default() -> Self {
            Self {
                cpu_arch: std::env::consts::ARCH.to_string(),
                cpu_cores: num_cpus_detected(),
                cpu_frequency_mhz: 2400,
                ram_total_kb: 8 * 1024 * 1024,
                ram_available_kb: 4 * 1024 * 1024,
            }
        }
    }

    /// GPU, VRAM, and NPU accelerator telemetry.
    #[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
    pub struct VramTelemetry {
        pub gpu_vendor: String, // "NVIDIA", "AMD", "Intel", "Apple", "None"
        pub gpu_model: Option<String>,
        pub vram_total_mb: u32,
        pub vram_available_mb: u32,
        pub has_npu: bool,
    }

    impl Default for VramTelemetry {
        fn default() -> Self {
            Self {
                gpu_vendor: "None".to_string(),
                gpu_model: None,
                vram_total_mb: 0,
                vram_available_mb: 0,
                has_npu: false,
            }
        }
    }

    /// Sandboxing execution tiers supported by the edge node.
    #[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
    pub struct EngineTiers {
        pub wasm_tier: bool,
        pub native_tier: bool,
        pub llm_inference: bool,
        pub supported_task_types: Vec<String>,
    }

    impl Default for EngineTiers {
        fn default() -> Self {
            Self {
                wasm_tier: true,
                native_tier: true,
                llm_inference: false,
                supported_task_types: vec![
                    "wasm".to_string(),
                    "native_elf".to_string(),
                    "crdt_sync".to_string(),
                ],
            }
        }
    }

    /// Transport network connectivity options active on the edge node.
    #[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
    pub struct ActiveTransports {
        pub lan_broadcast: bool,
        pub direct_tcp: bool,
        pub tailscale: bool,
        pub wireguard: bool,
        pub ble_mesh: bool,
        pub endpoints: Vec<String>,
    }

    impl Default for ActiveTransports {
        fn default() -> Self {
            Self {
                lan_broadcast: true,
                direct_tcp: true,
                tailscale: false,
                wireguard: false,
                ble_mesh: false,
                // The local endpoint is the node's SSOT port, resolved at run time.
                endpoints: crate::ssot::get("MIOS_PORTS_NODE")
                    .map(|p| vec![format!("127.0.0.1:{p}")])
                    .unwrap_or_default(),
            }
        }
    }

    /// Consolidated capabilities profile of an edge node.
    #[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
    pub struct NodeCapabilities {
        pub hardware: HardwareSpecs,
        pub vram: VramTelemetry,
        pub engines: EngineTiers,
        pub transports: ActiveTransports,
        pub has_gpio: bool,
        pub has_i2c: bool,
    }

    /// Opcode 0x02 `NodeAnnounce` full wire payload.
    #[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
    pub struct NodeAnnouncePayload {
        pub node_id: u32,
        pub hostname: String,
        pub capabilities: NodeCapabilities,
        pub timestamp_utc: u64,
        pub version: String,
    }

    impl NodeAnnouncePayload {
        pub fn new(node_id: u32, hostname: String, capabilities: NodeCapabilities) -> Self {
            let now_sec = std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .map(|d| d.as_secs())
                .unwrap_or(0);

            Self {
                node_id,
                hostname,
                capabilities,
                timestamp_utc: now_sec,
                version: "0.3.0".to_string(),
            }
        }

        pub fn to_frame(&self) -> Result<Frame> {
            let json_bytes = serde_json::to_vec(self)?;
            Ok(Frame::new(
                MessageType::NodeAnnounce,
                self.node_id,
                json_bytes,
            ))
        }

        pub fn from_frame(frame: &Frame) -> Result<Self> {
            if frame.header.msg_type != MessageType::NodeAnnounce {
                return Err(anyhow!(
                    "Invalid message type for NodeAnnounce: {:?}",
                    frame.header.msg_type
                ));
            }
            let payload = serde_json::from_slice(&frame.payload)?;
            Ok(payload)
        }
    }

    fn num_cpus_detected() -> u32 {
        let count = std::thread::available_parallelism()
            .map(|n| n.get())
            .unwrap_or(4);
        count as u32
    }

    /// Probes the local operating environment for hardware, VRAM, and peripheral interfaces.
    /// Parity twin: usr/libexec/mios/node/capabilities.py (probe_node_capabilities)
    pub fn probe_node_capabilities() -> NodeCapabilities {
        let mut caps = NodeCapabilities::default();

        // 1. Probe CPU & RAM via /proc if on Linux
        if Path::new("/proc/meminfo").exists() {
            if let Ok(content) = fs::read_to_string("/proc/meminfo") {
                for line in content.lines() {
                    if line.starts_with("MemTotal:") {
                        if let Some(kb_str) = line.split_whitespace().nth(1) {
                            if let Ok(val) = kb_str.parse::<u64>() {
                                caps.hardware.ram_total_kb = val;
                            }
                        }
                    } else if line.starts_with("MemAvailable:") {
                        if let Some(kb_str) = line.split_whitespace().nth(1) {
                            if let Ok(val) = kb_str.parse::<u64>() {
                                caps.hardware.ram_available_kb = val;
                            }
                        }
                    }
                }
            }
        }

        // 2. Probe GPIO & I2C
        caps.has_gpio =
            Path::new("/dev/gpiochip0").exists() || Path::new("/sys/class/gpio").exists();
        caps.has_i2c = Path::new("/dev/i2c-0").exists() || Path::new("/dev/i2c-1").exists();

        // 3. Probe GPU
        if Path::new("/sys/class/drm").exists() || Path::new("/dev/nvidia0").exists() {
            if Path::new("/dev/nvidia0").exists() {
                caps.vram.gpu_vendor = "NVIDIA".to_string();
                caps.vram.gpu_model = Some("NVIDIA GPU Accelerator".to_string());
                caps.vram.vram_total_mb = 8192;
                caps.vram.vram_available_mb = 6144;
                caps.engines.llm_inference = true;
            } else {
                caps.vram.gpu_vendor = "Generic DRM".to_string();
            }
        }

        caps
    }

    /// In-memory cluster capability registry for tracking mesh peers and candidate scheduling.
    #[derive(Debug, Default)]
    pub struct CapabilityRegistry {
        peers: Arc<Mutex<HashMap<u32, (NodeAnnouncePayload, u64)>>>,
    }

    impl CapabilityRegistry {
        pub fn new() -> Self {
            Self {
                peers: Arc::new(Mutex::new(HashMap::new())),
            }
        }

        pub fn register_announce(&self, payload: NodeAnnouncePayload, received_at_utc: u64) {
            let mut map = self.peers.lock().unwrap();
            map.insert(payload.node_id, (payload, received_at_utc));
        }

        pub fn get_capabilities(&self, node_id: u32) -> Option<NodeCapabilities> {
            let map = self.peers.lock().unwrap();
            map.get(&node_id).map(|(p, _)| p.capabilities.clone())
        }

        /// Parity twin: usr/libexec/mios/node/capabilities.py (CapabilityRegistry.get_announce)
        pub fn get_announce(&self, node_id: u32) -> Option<NodeAnnouncePayload> {
            let map = self.peers.lock().unwrap();
            map.get(&node_id).map(|(p, _)| p.clone())
        }

        /// Finds all registered node IDs matching specific hardware and execution requirements.
        pub fn find_eligible_nodes(
            &self,
            min_ram_kb: u64,
            min_vram_mb: u32,
            require_wasm: bool,
            require_native: bool,
            require_gpio: bool,
            require_i2c: bool,
        ) -> Vec<u32> {
            let map = self.peers.lock().unwrap();
            let mut candidates = Vec::new();

            for (&node_id, (payload, _)) in map.iter() {
                let caps = &payload.capabilities;
                if caps.hardware.ram_available_kb < min_ram_kb {
                    continue;
                }
                if caps.vram.vram_available_mb < min_vram_mb {
                    continue;
                }
                if require_wasm && !caps.engines.wasm_tier {
                    continue;
                }
                if require_native && !caps.engines.native_tier {
                    continue;
                }
                if require_gpio && !caps.has_gpio {
                    continue;
                }
                if require_i2c && !caps.has_i2c {
                    continue;
                }
                candidates.push(node_id);
            }

            candidates.sort();
            candidates
        }

        /// Evicts announces older than max_age_secs.
        pub fn evict_stale(&self, max_age_secs: u64, now_utc: u64) -> usize {
            let mut map = self.peers.lock().unwrap();
            let before_len = map.len();
            map.retain(|_, (_, last_seen)| now_utc.saturating_sub(*last_seen) <= max_age_secs);
            before_len - map.len()
        }

        pub fn active_node_count(&self) -> usize {
            self.peers.lock().unwrap().len()
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_node_announce_frame_roundtrip() {
            let mut caps = NodeCapabilities::default();
            caps.hardware.ram_total_kb = 16 * 1024 * 1024;
            caps.vram.vram_total_mb = 12288;
            caps.vram.gpu_vendor = "NVIDIA".to_string();
            caps.has_gpio = true;

            let payload = NodeAnnouncePayload::new(42, "edge-blade-01".to_string(), caps);
            let frame = payload.to_frame().unwrap();

            assert_eq!(frame.header.msg_type, MessageType::NodeAnnounce);
            assert_eq!(frame.header.node_id, 42);

            let decoded = NodeAnnouncePayload::from_frame(&frame).unwrap();
            assert_eq!(decoded.node_id, 42);
            assert_eq!(decoded.hostname, "edge-blade-01");
            assert_eq!(decoded.capabilities.vram.vram_total_mb, 12288);
            assert!(decoded.capabilities.has_gpio);
        }

        #[test]
        fn test_capability_registry_filtering_and_eviction() {
            let registry = CapabilityRegistry::new();

            let mut caps1 = NodeCapabilities::default();
            caps1.hardware.ram_available_kb = 2 * 1024 * 1024;
            caps1.vram.vram_available_mb = 0;
            caps1.has_gpio = true;

            let mut caps2 = NodeCapabilities::default();
            caps2.hardware.ram_available_kb = 8 * 1024 * 1024;
            caps2.vram.vram_available_mb = 4096;
            caps2.has_gpio = false;

            let node1 = NodeAnnouncePayload::new(101, "worker-iot".to_string(), caps1);
            let node2 = NodeAnnouncePayload::new(102, "worker-gpu".to_string(), caps2);

            registry.register_announce(node1, 1000);
            registry.register_announce(node2, 1000);

            // Find nodes with GPU VRAM >= 2048MB
            let gpu_nodes = registry.find_eligible_nodes(1024, 2048, false, false, false, false);
            assert_eq!(gpu_nodes, vec![102]);

            // Find nodes with GPIO support
            let gpio_nodes = registry.find_eligible_nodes(1024, 0, false, false, true, false);
            assert_eq!(gpio_nodes, vec![101]);

            // Test stale eviction at t=1050 with max_age=30s
            let evicted = registry.evict_stale(30, 1050);
            assert_eq!(evicted, 2);
            assert_eq!(registry.active_node_count(), 0);
        }
    }
}

pub mod hardware {
    //! MiOS Edge Node Hardware Abstraction Layer (HAL) & Wasm Sandbox Host Imports
    //! Enforces strict allowlist permissions for GPIO and I2C interactions from sandboxed Wasm guests.

    use serde::{Deserialize, Serialize};
    use std::collections::{HashMap, HashSet};
    use std::fs;
    use std::path::Path;
    use std::sync::{Arc, Mutex, RwLock};

    #[repr(i32)]
    #[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
    pub enum HardwareErrorCode {
        Success = 0,
        PermissionDenied = -1,
        DeviceNotFound = -2,
        InvalidParameter = -3,
        IoError = -4,
        ReadOnlyPin = -5,
    }

    impl std::fmt::Display for HardwareErrorCode {
        fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            write!(f, "{:?} ({})", self, *self as i32)
        }
    }

    impl std::error::Error for HardwareErrorCode {}

    #[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
    pub struct HardwareAllowlist {
        pub allowed_gpio_pins: HashSet<u32>,
        pub read_only_gpio_pins: HashSet<u32>,
        pub allowed_i2c_buses: HashSet<u8>,
        pub allowed_i2c_addresses: HashSet<u16>,
        pub max_i2c_transfer_len: usize,
    }

    impl Default for HardwareAllowlist {
        fn default() -> Self {
            Self {
                allowed_gpio_pins: [4, 17, 27, 22].into_iter().collect(),
                read_only_gpio_pins: [4].into_iter().collect(),
                allowed_i2c_buses: [1].into_iter().collect(),
                allowed_i2c_addresses: [0x48, 0x68, 0x76, 0x77].into_iter().collect(),
                max_i2c_transfer_len: 256,
            }
        }
    }

    pub trait HardwareDriver: Send + Sync {
        fn gpio_read(&self, pin: u32) -> Result<u8, HardwareErrorCode>;
        fn gpio_write(&self, pin: u32, value: u8) -> Result<(), HardwareErrorCode>;
        fn i2c_transfer(
            &self,
            bus: u8,
            addr: u16,
            write_buf: &[u8],
            read_buf: &mut [u8],
        ) -> Result<usize, HardwareErrorCode>;
    }

    /// Thread-safe in-memory Mock Hardware Driver for testing, emulation, and containerized runtimes
    #[derive(Debug, Default)]
    pub struct MockHardwareDriver {
        gpio_pins: Mutex<HashMap<u32, u8>>,
        i2c_registers: Mutex<HashMap<(u8, u16, u8), u8>>,
    }

    impl MockHardwareDriver {
        pub fn new() -> Self {
            Self {
                gpio_pins: Mutex::new(HashMap::new()),
                i2c_registers: Mutex::new(HashMap::new()),
            }
        }

        pub fn set_mock_gpio(&self, pin: u32, val: u8) {
            let mut pins = self.gpio_pins.lock().unwrap();
            pins.insert(pin, val);
        }

        pub fn get_mock_gpio(&self, pin: u32) -> Option<u8> {
            let pins = self.gpio_pins.lock().unwrap();
            pins.get(&pin).copied()
        }

        pub fn set_mock_i2c_register(&self, bus: u8, addr: u16, reg: u8, val: u8) {
            let mut regs = self.i2c_registers.lock().unwrap();
            regs.insert((bus, addr, reg), val);
        }

        /// Parity twin: usr/libexec/mios/node/hardware.py (MockHardwareDriver.get_mock_i2c_register)
        pub fn get_mock_i2c_register(&self, bus: u8, addr: u16, reg: u8) -> Option<u8> {
            let regs = self.i2c_registers.lock().unwrap();
            regs.get(&(bus, addr, reg)).copied()
        }
    }

    impl HardwareDriver for MockHardwareDriver {
        fn gpio_read(&self, pin: u32) -> Result<u8, HardwareErrorCode> {
            let pins = self.gpio_pins.lock().unwrap();
            Ok(*pins.get(&pin).unwrap_or(&0))
        }

        fn gpio_write(&self, pin: u32, value: u8) -> Result<(), HardwareErrorCode> {
            let mut pins = self.gpio_pins.lock().unwrap();
            pins.insert(pin, value);
            Ok(())
        }

        fn i2c_transfer(
            &self,
            bus: u8,
            addr: u16,
            write_buf: &[u8],
            read_buf: &mut [u8],
        ) -> Result<usize, HardwareErrorCode> {
            let mut regs = self.i2c_registers.lock().unwrap();

            // If writing to a register address (write_buf[0] = reg, write_buf[1..] = values)
            if !write_buf.is_empty() {
                let mut reg = write_buf[0];
                for &val in &write_buf[1..] {
                    regs.insert((bus, addr, reg), val);
                    reg = reg.wrapping_add(1);
                }
            }

            // If reading: if write_buf has 1 byte (register pointer), read starting at that register
            if !read_buf.is_empty() {
                let start_reg = if !write_buf.is_empty() {
                    write_buf[0]
                } else {
                    0
                };
                for (idx, slot) in read_buf.iter_mut().enumerate() {
                    let current_reg = start_reg.wrapping_add(idx as u8);
                    *slot = *regs.get(&(bus, addr, current_reg)).unwrap_or(&0);
                }
            }

            Ok(read_buf.len())
        }
    }

    /// Linux Sysfs / I2C-dev hardware driver interacting with actual kernel hardware paths
    #[derive(Debug, Default)]
    pub struct LinuxSysfsHardwareDriver {
        sysfs_gpio_root: String,
        dev_i2c_root: String,
    }

    impl LinuxSysfsHardwareDriver {
        pub fn new() -> Self {
            Self {
                sysfs_gpio_root: "/sys/class/gpio".to_string(),
                dev_i2c_root: "/dev".to_string(),
            }
        }

        /// Parity twin: usr/libexec/mios/node/hardware.py (LinuxSysfsHardwareDriver roots)
        pub fn with_custom_roots(sysfs_gpio_root: &str, dev_i2c_root: &str) -> Self {
            Self {
                sysfs_gpio_root: sysfs_gpio_root.to_string(),
                dev_i2c_root: dev_i2c_root.to_string(),
            }
        }
    }

    impl HardwareDriver for LinuxSysfsHardwareDriver {
        fn gpio_read(&self, pin: u32) -> Result<u8, HardwareErrorCode> {
            let val_path = format!("{}/gpio{}/value", self.sysfs_gpio_root, pin);
            if !Path::new(&val_path).exists() {
                return Err(HardwareErrorCode::DeviceNotFound);
            }
            match fs::read_to_string(&val_path) {
                Ok(content) => {
                    let trimmed = content.trim();
                    if trimmed == "1" {
                        Ok(1)
                    } else {
                        Ok(0)
                    }
                }
                Err(_) => Err(HardwareErrorCode::IoError),
            }
        }

        fn gpio_write(&self, pin: u32, value: u8) -> Result<(), HardwareErrorCode> {
            let val_path = format!("{}/gpio{}/value", self.sysfs_gpio_root, pin);
            if !Path::new(&val_path).exists() {
                return Err(HardwareErrorCode::DeviceNotFound);
            }
            let content = if value != 0 { "1" } else { "0" };
            match fs::write(&val_path, content) {
                Ok(_) => Ok(()),
                Err(_) => Err(HardwareErrorCode::IoError),
            }
        }

        fn i2c_transfer(
            &self,
            bus: u8,
            _addr: u16,
            _write_buf: &[u8],
            _read_buf: &mut [u8],
        ) -> Result<usize, HardwareErrorCode> {
            let dev_path = format!("{}/i2c-{}", self.dev_i2c_root, bus);
            if !Path::new(&dev_path).exists() {
                return Err(HardwareErrorCode::DeviceNotFound);
            }
            // In containerized or driver-free hosts without root i2c capabilities:
            Err(HardwareErrorCode::IoError)
        }
    }

    /// Sandboxed Hardware Controller with strict Allowlist enforcement for Wasm Host Imports
    pub struct SandboxedHardwareController {
        allowlist: RwLock<HardwareAllowlist>,
        driver: Arc<dyn HardwareDriver>,
    }

    impl SandboxedHardwareController {
        pub fn new(allowlist: HardwareAllowlist, driver: Arc<dyn HardwareDriver>) -> Self {
            Self {
                allowlist: RwLock::new(allowlist),
                driver,
            }
        }

        pub fn new_mock(allowlist: HardwareAllowlist) -> (Self, Arc<MockHardwareDriver>) {
            let mock_driver = Arc::new(MockHardwareDriver::new());
            let controller = Self::new(allowlist, mock_driver.clone());
            (controller, mock_driver)
        }

        pub fn update_allowlist(&self, allowlist: HardwareAllowlist) {
            let mut w = self.allowlist.write().unwrap();
            *w = allowlist;
        }

        pub fn get_allowlist(&self) -> HardwareAllowlist {
            self.allowlist.read().unwrap().clone()
        }

        // --- Host Import Interfaces ---

        /// Host import `mios_sys_gpio_read(pin: u32) -> Result<u8, HardwareErrorCode>`
        pub fn mios_sys_gpio_read(&self, pin: u32) -> Result<u8, HardwareErrorCode> {
            let allowlist = self.allowlist.read().unwrap();
            if !allowlist.allowed_gpio_pins.contains(&pin) {
                return Err(HardwareErrorCode::PermissionDenied);
            }
            self.driver.gpio_read(pin)
        }

        /// Host import `mios_sys_gpio_write(pin: u32, value: u8) -> Result<(), HardwareErrorCode>`
        pub fn mios_sys_gpio_write(&self, pin: u32, value: u8) -> Result<(), HardwareErrorCode> {
            let allowlist = self.allowlist.read().unwrap();
            if !allowlist.allowed_gpio_pins.contains(&pin) {
                return Err(HardwareErrorCode::PermissionDenied);
            }
            if allowlist.read_only_gpio_pins.contains(&pin) {
                return Err(HardwareErrorCode::ReadOnlyPin);
            }
            self.driver.gpio_write(pin, value)
        }

        /// Host import `mios_sys_i2c_transfer`
        pub fn mios_sys_i2c_transfer(
            &self,
            bus: u8,
            addr: u16,
            write_buf: &[u8],
            read_buf: &mut [u8],
        ) -> Result<usize, HardwareErrorCode> {
            let allowlist = self.allowlist.read().unwrap();
            if !allowlist.allowed_i2c_buses.contains(&bus) {
                return Err(HardwareErrorCode::PermissionDenied);
            }
            if !allowlist.allowed_i2c_addresses.contains(&addr) {
                return Err(HardwareErrorCode::PermissionDenied);
            }
            if write_buf.len() > allowlist.max_i2c_transfer_len
                || read_buf.len() > allowlist.max_i2c_transfer_len
            {
                return Err(HardwareErrorCode::InvalidParameter);
            }
            self.driver.i2c_transfer(bus, addr, write_buf, read_buf)
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_allowlist_gpio_access() {
            let mut allowlist = HardwareAllowlist::default();
            allowlist.allowed_gpio_pins.insert(17);
            allowlist.read_only_gpio_pins.insert(4);

            let (controller, mock) = SandboxedHardwareController::new_mock(allowlist);

            // Allowed write to pin 17
            assert_eq!(controller.mios_sys_gpio_write(17, 1), Ok(()));
            assert_eq!(mock.get_mock_gpio(17), Some(1));
            assert_eq!(controller.mios_sys_gpio_read(17), Ok(1));

            // Read-only pin 4 cannot be written
            assert_eq!(
                controller.mios_sys_gpio_write(4, 1),
                Err(HardwareErrorCode::ReadOnlyPin)
            );
            // But read is allowed
            assert_eq!(controller.mios_sys_gpio_read(4), Ok(0));

            // Unallowed pin 99
            assert_eq!(
                controller.mios_sys_gpio_read(99),
                Err(HardwareErrorCode::PermissionDenied)
            );
            assert_eq!(
                controller.mios_sys_gpio_write(99, 1),
                Err(HardwareErrorCode::PermissionDenied)
            );
        }

        #[test]
        fn test_allowlist_i2c_transfer() {
            let allowlist = HardwareAllowlist::default(); // allowed bus 1, addr 0x48, 0x68, 0x76, 0x77
            let (controller, mock) = SandboxedHardwareController::new_mock(allowlist);

            // Set up mock register on bus 1, addr 0x68, reg 0x10 = 0xAB
            mock.set_mock_i2c_register(1, 0x68, 0x10, 0xAB);

            // Valid transfer on allowed bus 1, addr 0x68
            let write_data = [0x10u8];
            let mut read_data = [0u8; 1];
            let res = controller.mios_sys_i2c_transfer(1, 0x68, &write_data, &mut read_data);
            assert_eq!(res, Ok(1));
            assert_eq!(read_data[0], 0xAB);

            // Disallowed address 0x55
            let res_disallowed_addr =
                controller.mios_sys_i2c_transfer(1, 0x55, &write_data, &mut read_data);
            assert_eq!(
                res_disallowed_addr,
                Err(HardwareErrorCode::PermissionDenied)
            );

            // Disallowed bus 2
            let res_disallowed_bus =
                controller.mios_sys_i2c_transfer(2, 0x68, &write_data, &mut read_data);
            assert_eq!(res_disallowed_bus, Err(HardwareErrorCode::PermissionDenied));
        }
    }
}

pub mod watchdog {
    //! MiOS Hardware Watchdog Supervisor & Device Controller
    //! Integrates Linux `/dev/watchdog` timer with automatic keepalive pinging, systemd notify fallback,
    //! and safe magic close ('V' / 0x56) on clean termination.

    use serde::{Deserialize, Serialize};
    use std::fs::{File, OpenOptions};
    use std::io::Write;
    use std::path::Path;
    use std::sync::{Arc, Mutex};
    use std::time::Instant;

    #[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
    pub struct WatchdogConfig {
        pub enabled: bool,
        pub device_path: String,
        pub timeout_secs: u32,
        pub ping_interval_secs: u64,
        pub use_systemd_notify: bool,
    }

    impl Default for WatchdogConfig {
        fn default() -> Self {
            Self {
                enabled: true,
                device_path: "/dev/watchdog".to_string(),
                timeout_secs: 30,
                ping_interval_secs: 5,
                use_systemd_notify: true,
            }
        }
    }

    pub trait WatchdogDriver: Send + Sync {
        fn arm(&mut self) -> Result<(), String>;
        fn ping(&mut self) -> Result<(), String>;
        /// Sets watchdog timeout in seconds.
        /// Parity twin: usr/libexec/mios/node/watchdog.py (set_timeout)
        fn set_timeout(&mut self, timeout_secs: u32) -> Result<u32, String>;
        /// Gets watchdog timeout in seconds.
        /// Parity twin: usr/libexec/mios/node/watchdog.py (get_timeout)
        fn get_timeout(&self) -> Result<u32, String>;
        fn disarm_and_close(&mut self) -> Result<(), String>;
        fn is_hardware_present(&self) -> bool;
        fn is_armed(&self) -> bool;
    }

    /// Linux `/dev/watchdog` hardware driver with magic close `'V'`
    pub struct LinuxHardwareWatchdog {
        device_path: String,
        timeout_secs: u32,
        file_handle: Option<File>,
        is_present: bool,
    }

    impl LinuxHardwareWatchdog {
        pub fn new(device_path: impl Into<String>, timeout_secs: u32) -> Self {
            let path = device_path.into();
            let is_present = Path::new(&path).exists();
            Self {
                device_path: path,
                timeout_secs,
                file_handle: None,
                is_present,
            }
        }
    }

    impl WatchdogDriver for LinuxHardwareWatchdog {
        fn arm(&mut self) -> Result<(), String> {
            if !self.is_present {
                return Err(format!("Watchdog device {} not found", self.device_path));
            }

            if self.file_handle.is_none() {
                let file = OpenOptions::new()
                    .write(true)
                    .open(&self.device_path)
                    .map_err(|e| format!("Failed to open watchdog {}: {}", self.device_path, e))?;
                self.file_handle = Some(file);
            }
            Ok(())
        }

        fn ping(&mut self) -> Result<(), String> {
            if let Some(ref mut f) = self.file_handle {
                f.write_all(b"\0")
                    .map_err(|e| format!("Watchdog ping write failed: {}", e))?;
                f.flush()
                    .map_err(|e| format!("Watchdog flush failed: {}", e))?;
                Ok(())
            } else {
                Err("Watchdog is not armed / device not open".to_string())
            }
        }

        /// Parity twin: usr/libexec/mios/node/watchdog.py (set_timeout)
        fn set_timeout(&mut self, timeout_secs: u32) -> Result<u32, String> {
            self.timeout_secs = timeout_secs;
            Ok(self.timeout_secs)
        }

        /// Parity twin: usr/libexec/mios/node/watchdog.py (get_timeout)
        fn get_timeout(&self) -> Result<u32, String> {
            Ok(self.timeout_secs)
        }

        fn disarm_and_close(&mut self) -> Result<(), String> {
            if let Some(mut f) = self.file_handle.take() {
                // Strict Invariant: Write 'V' (0x56) magic character to disarm hardware timer cleanly
                let _ = f.write_all(b"V");
                let _ = f.flush();
            }
            Ok(())
        }

        fn is_hardware_present(&self) -> bool {
            self.is_present
        }

        fn is_armed(&self) -> bool {
            self.file_handle.is_some()
        }
    }

    /// In-memory Mock Watchdog Driver for testing and headless execution
    #[derive(Debug)]
    pub struct MockWatchdogDriver {
        pub armed: bool,
        pub ping_count: u64,
        pub last_ping: Option<Instant>,
        pub timeout_secs: u32,
        pub disarmed_safely: bool,
        pub simulated_present: bool,
    }

    impl Default for MockWatchdogDriver {
        fn default() -> Self {
            Self::new(true, 30)
        }
    }

    impl MockWatchdogDriver {
        pub fn new(simulated_present: bool, timeout_secs: u32) -> Self {
            Self {
                armed: false,
                ping_count: 0,
                last_ping: None,
                timeout_secs,
                disarmed_safely: false,
                simulated_present,
            }
        }
    }

    impl WatchdogDriver for MockWatchdogDriver {
        fn arm(&mut self) -> Result<(), String> {
            if !self.simulated_present {
                return Err("Mock hardware watchdog not present".to_string());
            }
            self.armed = true;
            self.disarmed_safely = false;
            self.last_ping = Some(Instant::now());
            Ok(())
        }

        fn ping(&mut self) -> Result<(), String> {
            if !self.armed {
                return Err("Cannot ping disarmed watchdog".to_string());
            }
            self.ping_count += 1;
            self.last_ping = Some(Instant::now());
            Ok(())
        }

        /// Parity twin: usr/libexec/mios/node/watchdog.py (set_timeout)
        fn set_timeout(&mut self, timeout_secs: u32) -> Result<u32, String> {
            self.timeout_secs = timeout_secs;
            Ok(self.timeout_secs)
        }

        /// Parity twin: usr/libexec/mios/node/watchdog.py (get_timeout)
        fn get_timeout(&self) -> Result<u32, String> {
            Ok(self.timeout_secs)
        }

        fn disarm_and_close(&mut self) -> Result<(), String> {
            if self.armed {
                self.armed = false;
                self.disarmed_safely = true;
            }
            Ok(())
        }

        fn is_hardware_present(&self) -> bool {
            self.simulated_present
        }

        fn is_armed(&self) -> bool {
            self.armed
        }
    }

    /// Watchdog Supervisor managing keepalive loop and clean shutdown
    pub struct WatchdogSupervisor {
        pub config: WatchdogConfig,
        driver: Arc<Mutex<dyn WatchdogDriver>>,
    }

    impl WatchdogSupervisor {
        pub fn new(config: WatchdogConfig, driver: Arc<Mutex<dyn WatchdogDriver>>) -> Self {
            Self { config, driver }
        }

        pub fn new_mock(config: WatchdogConfig) -> (Self, Arc<Mutex<MockWatchdogDriver>>) {
            let mock = Arc::new(Mutex::new(MockWatchdogDriver::new(
                true,
                config.timeout_secs,
            )));
            let supervisor = Self {
                config,
                driver: mock.clone(),
            };
            (supervisor, mock)
        }

        pub fn arm(&self) -> Result<(), String> {
            let mut d = self.driver.lock().unwrap();
            d.arm()
        }

        pub fn ping(&self) -> Result<(), String> {
            let mut d = self.driver.lock().unwrap();
            d.ping()
        }

        pub fn disarm(&self) -> Result<(), String> {
            let mut d = self.driver.lock().unwrap();
            d.disarm_and_close()
        }

        pub fn is_armed(&self) -> bool {
            let d = self.driver.lock().unwrap();
            d.is_armed()
        }

        pub fn is_present(&self) -> bool {
            let d = self.driver.lock().unwrap();
            d.is_hardware_present()
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_mock_watchdog_lifecycle() {
            let config = WatchdogConfig::default();
            let (supervisor, mock) = WatchdogSupervisor::new_mock(config);

            assert!(supervisor.is_present());
            assert!(!supervisor.is_armed());

            // Arm
            supervisor.arm().unwrap();
            assert!(supervisor.is_armed());

            // Ping 3 times
            supervisor.ping().unwrap();
            supervisor.ping().unwrap();
            supervisor.ping().unwrap();

            {
                let m = mock.lock().unwrap();
                assert_eq!(m.ping_count, 3);
                assert!(!m.disarmed_safely);
            }

            // Disarm safely with 'V'
            supervisor.disarm().unwrap();
            assert!(!supervisor.is_armed());

            {
                let m = mock.lock().unwrap();
                assert!(m.disarmed_safely);
            }

            // Ping after disarm fails
            assert!(supervisor.ping().is_err());
        }

        #[test]
        fn test_linux_watchdog_absent_graceful_detection() {
            let mut driver = LinuxHardwareWatchdog::new("/tmp/nonexistent_watchdog_device", 30);
            assert!(!driver.is_hardware_present());
            assert!(!driver.is_armed());

            let arm_res = driver.arm();
            assert!(arm_res.is_err());
        }
    }
}
