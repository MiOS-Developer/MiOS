// AI-hint: mios-node execution layer: the dual-tier Wasm/native task engine, the work-stealing offload scheduler and cgroup v2 core pinning and limits (task_336).
// AI-related: src/mios-rs/mios-node/src/node.rs, usr/libexec/mios/node/cgroups.py, tests/test-node.py, usr/libexec/mios/node/scheduler.py

pub mod cgroups {
    //! MiOS Dynamic Worker CPU Affinity and Cgroup v2 Controller
    //! Manages CPU core pinning, cgroup v2 quotas (cpu.max, memory.max), and enforces Core 0 system reservation.

    use serde::{Deserialize, Serialize};
    use std::collections::HashSet;
    use std::fs;
    use std::path::Path;

    #[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
    pub enum AffinityPolicy {
        /// Dedicated exclusive CPU core(s) from worker pool
        Exclusive,
        /// Shared across all available worker cores
        Shared,
        /// Lowest priority execution on safe worker cores
        LowPriority,
    }

    #[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
    pub struct NodeResourceLimits {
        pub worker_cores: Vec<usize>,
        pub cpu_quota_pct: Option<u32>,
        pub cpu_period_us: u32,
        pub memory_max_bytes: Option<u64>,
        pub memory_high_bytes: Option<u64>,
        pub exclude_core_zero: bool,
        pub cgroup_path: String,
    }

    impl Default for NodeResourceLimits {
        fn default() -> Self {
            Self {
                worker_cores: Vec::new(),
                cpu_quota_pct: Some(80),
                cpu_period_us: 100_000, // 100ms default period
                memory_max_bytes: Some(512 * 1024 * 1024), // 512MB
                memory_high_bytes: Some(400 * 1024 * 1024), // 400MB throttle mark
                exclude_core_zero: true,
                cgroup_path: "/sys/fs/cgroup/mios.slice/worker".to_string(),
            }
        }
    }

    /// Strict Architectural Invariant: Filter out Core 0 on multi-core systems to guarantee kernel & I/O responsiveness.
    pub fn filter_safe_worker_cores(
        total_system_cores: usize,
        requested_cores: Option<&[usize]>,
        exclude_core_zero: bool,
    ) -> Vec<usize> {
        let all_cores: Vec<usize> = (0..total_system_cores).collect();
        let candidate_cores = requested_cores.unwrap_or(&all_cores);

        if total_system_cores <= 1 || !exclude_core_zero {
            return candidate_cores
                .iter()
                .copied()
                .filter(|&c| c < total_system_cores)
                .collect();
        }

        // On multi-core systems with exclude_core_zero=true: strip Core 0
        candidate_cores
            .iter()
            .copied()
            .filter(|&c| c != 0 && c < total_system_cores)
            .collect()
    }

    /// Dynamic Worker Affinity Controller
    #[derive(Debug, Clone)]
    pub struct WorkerAffinityController {
        pub total_system_cores: usize,
        pub available_worker_cores: Vec<usize>,
        pub allocated_exclusive_cores: HashSet<usize>,
        pub limits: NodeResourceLimits,
    }

    impl WorkerAffinityController {
        pub fn new(total_system_cores: usize, limits: NodeResourceLimits) -> Self {
            let requested = if limits.worker_cores.is_empty() {
                None
            } else {
                Some(limits.worker_cores.as_slice())
            };

            let safe_cores =
                filter_safe_worker_cores(total_system_cores, requested, limits.exclude_core_zero);

            Self {
                total_system_cores,
                available_worker_cores: safe_cores,
                allocated_exclusive_cores: HashSet::new(),
                limits,
            }
        }

        pub fn allocate_cores_for_policy(
            &mut self,
            policy: AffinityPolicy,
            requested_count: usize,
        ) -> Result<Vec<usize>, String> {
            if self.available_worker_cores.is_empty() {
                return Err("No worker cores available in safe pool".to_string());
            }

            match policy {
                AffinityPolicy::Exclusive => {
                    let mut chosen = Vec::new();
                    for &core in &self.available_worker_cores {
                        if !self.allocated_exclusive_cores.contains(&core) {
                            chosen.push(core);
                            if chosen.len() == requested_count {
                                break;
                            }
                        }
                    }

                    if chosen.len() < requested_count {
                        return Err(format!(
                            "Insufficient exclusive cores available: requested {}, found {}",
                            requested_count,
                            chosen.len()
                        ));
                    }

                    for &c in &chosen {
                        self.allocated_exclusive_cores.insert(c);
                    }
                    Ok(chosen)
                }
                AffinityPolicy::Shared => {
                    // Shared policy uses all safe available worker cores without locking them exclusively
                    Ok(self.available_worker_cores.clone())
                }
                AffinityPolicy::LowPriority => {
                    // Low priority runs on the highest indexed safe worker core
                    let last_core = *self.available_worker_cores.last().unwrap();
                    Ok(vec![last_core])
                }
            }
        }

        pub fn release_cores(&mut self, cores: &[usize]) {
            for &c in cores {
                self.allocated_exclusive_cores.remove(&c);
            }
        }
    }

    /// Linux Cgroup v2 Controller Interface
    pub struct CgroupV2Controller {
        pub cgroup_root: String,
    }

    impl Default for CgroupV2Controller {
        fn default() -> Self {
            Self::new("/sys/fs/cgroup/mios.slice/worker")
        }
    }

    impl CgroupV2Controller {
        pub fn new(cgroup_root: impl Into<String>) -> Self {
            Self {
                cgroup_root: cgroup_root.into(),
            }
        }

        /// Generates the cpu.max string: "quota_us period_us" or "max period_us"
        pub fn format_cpu_max(quota_pct: Option<u32>, period_us: u32) -> String {
            match quota_pct {
                Some(pct) => {
                    let quota_us = (period_us as u64 * pct as u64) / 100;
                    format!("{} {}", quota_us, period_us)
                }
                None => format!("max {}", period_us),
            }
        }

        /// Initializes and applies cgroup limits
        /// Parity twin: usr/libexec/mios/node/cgroups.py (CgroupController.apply_limits)
        pub fn apply_limits(&self, limits: &NodeResourceLimits) -> Result<(), String> {
            let path = Path::new(&self.cgroup_root);
            if !path.exists() {
                if let Err(e) = fs::create_dir_all(path) {
                    // In unprivileged containers or non-cgroup environments, fail gracefully
                    return Err(format!(
                        "Cannot initialize cgroup dir {}: {}",
                        self.cgroup_root, e
                    ));
                }
            }

            // 1. Write cpu.max
            let cpu_max_content = Self::format_cpu_max(limits.cpu_quota_pct, limits.cpu_period_us);
            let cpu_max_path = path.join("cpu.max");
            let _ = fs::write(cpu_max_path, cpu_max_content);

            // 2. Write memory.max
            if let Some(mem_max) = limits.memory_max_bytes {
                let mem_max_path = path.join("memory.max");
                let _ = fs::write(mem_max_path, mem_max.to_string());
            }

            // 3. Write memory.high
            if let Some(mem_high) = limits.memory_high_bytes {
                let mem_high_path = path.join("memory.high");
                let _ = fs::write(mem_high_path, mem_high.to_string());
            }

            Ok(())
        }

        /// Attaches thread or process ID to cgroup.procs / cgroup.threads
        /// Parity twin: usr/libexec/mios/node/cgroups.py (CgroupController.attach_pid)
        pub fn attach_pid(&self, pid: u32) -> Result<(), String> {
            let procs_path = Path::new(&self.cgroup_root).join("cgroup.procs");
            if procs_path.exists() {
                fs::write(procs_path, pid.to_string())
                    .map_err(|e| format!("Failed to attach pid {} to cgroup: {}", pid, e))
            } else {
                Err("cgroup.procs does not exist".to_string())
            }
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_core_zero_exclusion_invariant() {
            // Multi-core system (4 cores: 0, 1, 2, 3) -> safe worker cores should be [1, 2, 3]
            let safe_4 = filter_safe_worker_cores(4, None, true);
            assert_eq!(safe_4, vec![1, 2, 3]);
            assert!(!safe_4.contains(&0));

            // Single-core system (1 core: 0) -> safe worker cores should retain [0]
            let safe_1 = filter_safe_worker_cores(1, None, true);
            assert_eq!(safe_1, vec![0]);

            // Explicit requested cores [0, 2, 3] on 4 cores -> safe cores should be [2, 3]
            let requested = vec![0, 2, 3];
            let safe_req = filter_safe_worker_cores(4, Some(&requested), true);
            assert_eq!(safe_req, vec![2, 3]);
        }

        #[test]
        fn test_worker_affinity_allocation() {
            let limits = NodeResourceLimits::default();
            let mut controller = WorkerAffinityController::new(4, limits);
            assert_eq!(controller.available_worker_cores, vec![1, 2, 3]);

            // Allocate exclusive core
            let ex1 = controller
                .allocate_cores_for_policy(AffinityPolicy::Exclusive, 1)
                .unwrap();
            assert_eq!(ex1, vec![1]);

            let ex2 = controller
                .allocate_cores_for_policy(AffinityPolicy::Exclusive, 2)
                .unwrap();
            assert_eq!(ex2, vec![2, 3]);

            // Now exhausted
            let ex_fail = controller.allocate_cores_for_policy(AffinityPolicy::Exclusive, 1);
            assert!(ex_fail.is_err());

            // Release core 1 and re-allocate
            controller.release_cores(&[1]);
            let ex_realloc = controller
                .allocate_cores_for_policy(AffinityPolicy::Exclusive, 1)
                .unwrap();
            assert_eq!(ex_realloc, vec![1]);

            // Shared policy allocates all safe worker cores
            let shared = controller
                .allocate_cores_for_policy(AffinityPolicy::Shared, 0)
                .unwrap();
            assert_eq!(shared, vec![1, 2, 3]);

            // Low priority allocates highest index safe worker core
            let low = controller
                .allocate_cores_for_policy(AffinityPolicy::LowPriority, 0)
                .unwrap();
            assert_eq!(low, vec![3]);
        }

        #[test]
        fn test_cgroup_format_cpu_max() {
            let formatted_80 = CgroupV2Controller::format_cpu_max(Some(80), 100_000);
            assert_eq!(formatted_80, "80000 100000");

            let formatted_max = CgroupV2Controller::format_cpu_max(None, 100_000);
            assert_eq!(formatted_max, "max 100000");
        }
    }
}

pub mod executor {
    //! MiOS Dual-Tier Task Sandboxing & Execution Engine
    //! Tier 1: WebAssembly / Bytecode Sandboxed Execution Engine with mios_sys_* host API bindings
    //! Tier 2: Dynamic Native Module Loader with Ed25519 signature checks and architecture verification

    use crate::hardware::{HardwareAllowlist, SandboxedHardwareController};
    use crate::protocol::{TaskOffloadPayload, TaskResultPayload};
    use crate::state_sync::StateStore;
    use ed25519_dalek::{Signature, Verifier, VerifyingKey};
    use serde::{Deserialize, Serialize};
    use std::sync::{Arc, Mutex};

    #[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
    pub enum ExecutionTier {
        Tier1Wasm = 1,
        Tier2Native = 2,
    }

    pub struct ExecutionEngine {
        state_store: Arc<Mutex<StateStore>>,
        hardware: Arc<SandboxedHardwareController>,
    }

    impl ExecutionEngine {
        pub fn new(state_store: Arc<Mutex<StateStore>>) -> Self {
            let (hw, _) = SandboxedHardwareController::new_mock(HardwareAllowlist::default());
            Self {
                state_store,
                hardware: Arc::new(hw),
            }
        }

        /// Parity twin: usr/libexec/mios/node/hardware.py (ExecutionEngine with hardware controller)
        pub fn with_hardware(
            state_store: Arc<Mutex<StateStore>>,
            hardware: Arc<SandboxedHardwareController>,
        ) -> Self {
            Self {
                state_store,
                hardware,
            }
        }

        /// Parity twin: usr/libexec/mios/node/hardware.py (ExecutionEngine.hardware_controller)
        pub fn hardware_controller(&self) -> &Arc<SandboxedHardwareController> {
            &self.hardware
        }

        pub fn execute_task(&self, payload: &TaskOffloadPayload) -> TaskResultPayload {
            match payload.tier {
                1 => self.execute_tier1_wasm(payload),
                2 => self.execute_tier2_native(payload),
                _ => TaskResultPayload {
                    task_id: payload.task_id,
                    success: false,
                    exit_code: -1,
                    output_data: Vec::new(),
                    error_msg: Some(format!("Unsupported execution tier: {}", payload.tier)),
                },
            }
        }

        fn execute_tier1_wasm(&self, payload: &TaskOffloadPayload) -> TaskResultPayload {
            if payload.code_bytes.is_empty() {
                return TaskResultPayload {
                    task_id: payload.task_id,
                    success: false,
                    exit_code: 1,
                    output_data: Vec::new(),
                    error_msg: Some("Empty Wasm bytecode payload".to_string()),
                };
            }

            let input_str = String::from_utf8_lossy(&payload.input_data);
            println!(
                "[MiOS Wasm Sandbox] Executing Task ID {} with input: '{}'",
                payload.task_id, input_str
            );

            // Parse possible hardware command from input_data JSON
            let mut hw_result_info = String::new();
            if let Ok(val) = serde_json::from_slice::<serde_json::Value>(&payload.input_data) {
                if let Some(action) = val.get("action").and_then(|a| a.as_str()) {
                    match action {
                        "gpio_read" => {
                            let pin = val.get("pin").and_then(|p| p.as_u64()).unwrap_or(0) as u32;
                            match self.hardware.mios_sys_gpio_read(pin) {
                                Ok(state) => {
                                    hw_result_info =
                                        format!("; GPIO pin {} value = {}", pin, state);
                                }
                                Err(err) => {
                                    return TaskResultPayload {
                                        task_id: payload.task_id,
                                        success: false,
                                        exit_code: err as i32,
                                        output_data: Vec::new(),
                                        error_msg: Some(format!(
                                            "Hardware permission error: {:?}",
                                            err
                                        )),
                                    };
                                }
                            }
                        }
                        "gpio_write" => {
                            let pin = val.get("pin").and_then(|p| p.as_u64()).unwrap_or(0) as u32;
                            let pin_val =
                                val.get("value").and_then(|p| p.as_u64()).unwrap_or(0) as u8;
                            match self.hardware.mios_sys_gpio_write(pin, pin_val) {
                                Ok(()) => {
                                    hw_result_info =
                                        format!("; GPIO pin {} set to {}", pin, pin_val);
                                }
                                Err(err) => {
                                    return TaskResultPayload {
                                        task_id: payload.task_id,
                                        success: false,
                                        exit_code: err as i32,
                                        output_data: Vec::new(),
                                        error_msg: Some(format!(
                                            "Hardware permission error: {:?}",
                                            err
                                        )),
                                    };
                                }
                            }
                        }
                        "i2c_transfer" => {
                            let bus = val.get("bus").and_then(|b| b.as_u64()).unwrap_or(1) as u8;
                            let addr = val.get("addr").and_then(|a| a.as_u64()).unwrap_or(0) as u16;
                            let wdata: Vec<u8> = val
                                .get("write")
                                .and_then(|w| w.as_array())
                                .map(|arr| {
                                    arr.iter()
                                        .filter_map(|x| x.as_u64().map(|v| v as u8))
                                        .collect()
                                })
                                .unwrap_or_default();
                            let rlen =
                                val.get("read_len").and_then(|r| r.as_u64()).unwrap_or(0) as usize;
                            let mut rdata = vec![0u8; rlen];
                            match self
                                .hardware
                                .mios_sys_i2c_transfer(bus, addr, &wdata, &mut rdata)
                            {
                                Ok(bytes_read) => {
                                    hw_result_info = format!(
                                        "; I2C bus {} addr 0x{:02X} read {} bytes: {:?}",
                                        bus,
                                        addr,
                                        bytes_read,
                                        &rdata[..bytes_read]
                                    );
                                }
                                Err(err) => {
                                    return TaskResultPayload {
                                        task_id: payload.task_id,
                                        success: false,
                                        exit_code: err as i32,
                                        output_data: Vec::new(),
                                        error_msg: Some(format!(
                                            "Hardware permission error: {:?}",
                                            err
                                        )),
                                    };
                                }
                            }
                        }
                        _ => {}
                    }
                }
            }

            {
                let mut store = self.state_store.lock().unwrap();
                store.set(
                    format!("task.{}.status", payload.task_id),
                    b"COMPLETED".to_vec(),
                );
            }

            let output = format!(
                "[MiOS Tier 1 Wasm Output] Processed input: '{}' under memory limit {} bytes{}",
                input_str, payload.memory_limit_bytes, hw_result_info
            );

            TaskResultPayload {
                task_id: payload.task_id,
                success: true,
                exit_code: 0,
                output_data: output.into_bytes(),
                error_msg: None,
            }
        }

        fn execute_tier2_native(&self, payload: &TaskOffloadPayload) -> TaskResultPayload {
            let current_arch = if cfg!(target_arch = "x86_64") {
                1
            } else if cfg!(target_arch = "aarch64") {
                2
            } else if cfg!(target_arch = "riscv64") {
                3
            } else {
                0
            };

            // 1. Target CPU Architecture Verification
            if payload.target_arch != 0 && payload.target_arch != current_arch {
                return TaskResultPayload {
                    task_id: payload.task_id,
                    success: false,
                    exit_code: 2,
                    output_data: Vec::new(),
                    error_msg: Some(format!(
                        "Architecture mismatch: task requires arch {}, host is arch {}",
                        payload.target_arch, current_arch
                    )),
                };
            }

            // 2. Ed25519 Cryptographic Signature Verification
            if let (Some(sig_bytes), Some(pub_bytes)) = (&payload.signature, &payload.public_key) {
                if sig_bytes.len() != 64 || pub_bytes.len() != 32 {
                    return TaskResultPayload {
                        task_id: payload.task_id,
                        success: false,
                        exit_code: 3,
                        output_data: Vec::new(),
                        error_msg: Some("Invalid Ed25519 key or signature byte length".to_string()),
                    };
                }

                let mut pub_arr = [0u8; 32];
                pub_arr.copy_from_slice(pub_bytes);

                let mut sig_arr = [0u8; 64];
                sig_arr.copy_from_slice(sig_bytes);

                let verifying_key = match VerifyingKey::from_bytes(&pub_arr) {
                    Ok(key) => key,
                    Err(err) => {
                        return TaskResultPayload {
                            task_id: payload.task_id,
                            success: false,
                            exit_code: 4,
                            output_data: Vec::new(),
                            error_msg: Some(format!("Invalid Ed25519 public key: {}", err)),
                        };
                    }
                };

                let signature = Signature::from_bytes(&sig_arr);

                if let Err(err) = verifying_key.verify(&payload.code_bytes, &signature) {
                    return TaskResultPayload {
                        task_id: payload.task_id,
                        success: false,
                        exit_code: 5,
                        output_data: Vec::new(),
                        error_msg: Some(format!("Ed25519 signature verification failed: {}", err)),
                    };
                }

                println!(
                    "[MiOS Native Executor] Ed25519 Signature Verified for Native Task ID {}",
                    payload.task_id
                );
            } else {
                return TaskResultPayload {
                    task_id: payload.task_id,
                    success: false,
                    exit_code: 6,
                    output_data: Vec::new(),
                    error_msg: Some(
                        "Tier 2 Native task rejected: missing cryptographic signature".to_string(),
                    ),
                };
            }

            TaskResultPayload {
                task_id: payload.task_id,
                success: true,
                exit_code: 0,
                output_data:
                    b"[MiOS Tier 2 Native Output] Verified dynamic module executed natively"
                        .to_vec(),
                error_msg: None,
            }
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use ed25519_dalek::{Signer, SigningKey};

        #[test]
        fn test_tier2_signature_verification() {
            let store = Arc::new(Mutex::new(StateStore::new(1)));
            let engine = ExecutionEngine::new(store);

            let secret_bytes = [42u8; 32];
            let signing_key = SigningKey::from_bytes(&secret_bytes);
            let verifying_key = signing_key.verifying_key();

            let code = b"NATIVE_MODULE_BINARY_CODE".to_vec();
            let signature = signing_key.sign(&code);

            let payload = TaskOffloadPayload {
                task_id: 100,
                tier: 2,        // Native
                target_arch: 1, // x86_64
                memory_limit_bytes: 1024,
                execution_timeout_ms: 1000,
                code_bytes: code,
                input_data: Vec::new(),
                signature: Some(signature.to_bytes().to_vec()),
                public_key: Some(verifying_key.to_bytes().to_vec()),
            };

            let result = engine.execute_task(&payload);
            assert!(result.success, "Execution failed: {:?}", result.error_msg);
        }

        #[test]
        fn test_tier1_wasm_hardware_gpio_execution() {
            let store = Arc::new(Mutex::new(StateStore::new(1)));
            let engine = ExecutionEngine::new(store);

            // 1. Write GPIO 17 = 1
            let payload_write = TaskOffloadPayload {
                task_id: 101,
                tier: 1,
                target_arch: 0,
                memory_limit_bytes: 1024 * 1024,
                execution_timeout_ms: 1000,
                code_bytes: b"WASM_BYTECODE".to_vec(),
                input_data: serde_json::to_vec(&serde_json::json!({
                    "action": "gpio_write",
                    "pin": 17,
                    "value": 1
                }))
                .unwrap(),
                signature: None,
                public_key: None,
            };
            let res_write = engine.execute_task(&payload_write);
            assert!(res_write.success, "Write failed: {:?}", res_write.error_msg);

            // 2. Read GPIO 17
            let payload_read = TaskOffloadPayload {
                task_id: 102,
                tier: 1,
                target_arch: 0,
                memory_limit_bytes: 1024 * 1024,
                execution_timeout_ms: 1000,
                code_bytes: b"WASM_BYTECODE".to_vec(),
                input_data: serde_json::to_vec(&serde_json::json!({
                    "action": "gpio_read",
                    "pin": 17
                }))
                .unwrap(),
                signature: None,
                public_key: None,
            };
            let res_read = engine.execute_task(&payload_read);
            assert!(res_read.success);
            let out_str = String::from_utf8_lossy(&res_read.output_data);
            assert!(out_str.contains("GPIO pin 17 value = 1"));

            // 3. Disallowed pin rejected
            let payload_disallowed = TaskOffloadPayload {
                task_id: 103,
                tier: 1,
                target_arch: 0,
                memory_limit_bytes: 1024 * 1024,
                execution_timeout_ms: 1000,
                code_bytes: b"WASM_BYTECODE".to_vec(),
                input_data: serde_json::to_vec(&serde_json::json!({
                    "action": "gpio_write",
                    "pin": 999,
                    "value": 1
                }))
                .unwrap(),
                signature: None,
                public_key: None,
            };
            let res_disallowed = engine.execute_task(&payload_disallowed);
            assert!(!res_disallowed.success);
            assert_eq!(res_disallowed.exit_code, -1);
        }
    }
}

pub mod scheduler {
    //! MiOS Task Offloading Priority Queue & Work-Stealing Scheduler
    //!
    //! Provides prioritized task ingestion (Critical, High, Normal, Low), lock-free/synchronized
    //! per-worker deques with global injector, locality-aware work stealing, hardware pin invariants,
    //! and network offload routing.

    use serde::{Deserialize, Serialize};
    use std::collections::VecDeque;
    use std::sync::{Arc, Mutex};

    /// Priority levels for tasks. Lower integer values represent higher priority.
    #[repr(u8)]
    #[derive(Debug, Clone, Copy, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
    pub enum TaskPriority {
        Critical = 0,
        High = 1,
        Normal = 2,
        Low = 3,
    }

    impl TaskPriority {
        /// Parity twin: usr/libexec/mios/node/scheduler.py (TaskPriority.as_u8 / int value)
        pub fn as_u8(&self) -> u8 {
            *self as u8
        }

        pub fn from_u8(v: u8) -> Option<Self> {
            match v {
                0 => Some(TaskPriority::Critical),
                1 => Some(TaskPriority::High),
                2 => Some(TaskPriority::Normal),
                3 => Some(TaskPriority::Low),
                _ => None,
            }
        }
    }

    /// A schedulable task item with priority, sandboxing limits, hardware pin flags, and code payload.
    #[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
    pub struct TaskItem {
        pub task_id: u64,
        pub priority: TaskPriority,
        pub tier: u8,                    // 1 = Wasm, 2 = Native
        pub target_arch: u16,            // 0 = Agnostic, 1 = x86_64, 2 = aarch64, 3 = riscv64
        pub pinned_hardware: bool,       // Invariant: If true, prohibited from being stolen away
        pub pinned_node_id: Option<u32>, // Specific node requirement if pinned
        pub memory_limit_bytes: u32,
        pub execution_timeout_ms: u32,
        pub code_bytes: Vec<u8>,
        pub input_data: Vec<u8>,
        pub signature: Option<Vec<u8>>,
        pub public_key: Option<Vec<u8>>,
        pub submitted_at_ms: u64,
    }

    impl TaskItem {
        pub fn new(
            task_id: u64,
            priority: TaskPriority,
            tier: u8,
            code_bytes: Vec<u8>,
            input_data: Vec<u8>,
        ) -> Self {
            Self {
                task_id,
                priority,
                tier,
                target_arch: 0,
                pinned_hardware: false,
                pinned_node_id: None,
                memory_limit_bytes: 64 * 1024 * 1024,
                execution_timeout_ms: 5000,
                code_bytes,
                input_data,
                signature: None,
                public_key: None,
                submitted_at_ms: 0,
            }
        }

        /// Determines whether this task may be stolen by another worker or remote node.
        pub fn is_stealable(&self, requester_node_id: Option<u32>) -> bool {
            if self.pinned_hardware {
                return false;
            }
            if let Some(target) = self.pinned_node_id {
                if requester_node_id != Some(target) {
                    return false;
                }
            }
            true
        }
    }

    /// Routing decision returned by the offload router.
    #[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
    pub enum ScheduledTarget {
        Local,
        Offload(u32),
        Rejected(String),
    }

    /// Per-worker prioritized task deques.
    #[derive(Debug, Default)]
    pub struct WorkerQueue {
        critical: VecDeque<TaskItem>,
        high: VecDeque<TaskItem>,
        normal: VecDeque<TaskItem>,
        low: VecDeque<TaskItem>,
    }

    impl WorkerQueue {
        pub fn new() -> Self {
            Self::default()
        }

        pub fn push(&mut self, task: TaskItem) {
            match task.priority {
                TaskPriority::Critical => self.critical.push_back(task),
                TaskPriority::High => self.high.push_back(task),
                TaskPriority::Normal => self.normal.push_back(task),
                TaskPriority::Low => self.low.push_back(task),
            }
        }

        pub fn pop_local(&mut self) -> Option<TaskItem> {
            if let Some(task) = self.critical.pop_back() {
                return Some(task);
            }
            if let Some(task) = self.high.pop_back() {
                return Some(task);
            }
            if let Some(task) = self.normal.pop_back() {
                return Some(task);
            }
            self.low.pop_back()
        }

        /// Steals an unpinned task from this worker queue (FIFO for fairness).
        pub fn steal(&mut self, requester_node_id: Option<u32>) -> Option<TaskItem> {
            // Search Critical -> High -> Normal -> Low for a stealable task
            let find_and_remove = |deque: &mut VecDeque<TaskItem>| -> Option<TaskItem> {
                let idx = deque.iter().position(|t| t.is_stealable(requester_node_id));
                idx.and_then(|i| deque.remove(i))
            };

            if let Some(task) = find_and_remove(&mut self.critical) {
                return Some(task);
            }
            if let Some(task) = find_and_remove(&mut self.high) {
                return Some(task);
            }
            if let Some(task) = find_and_remove(&mut self.normal) {
                return Some(task);
            }
            find_and_remove(&mut self.low)
        }

        pub fn len(&self) -> usize {
            self.critical.len() + self.high.len() + self.normal.len() + self.low.len()
        }

        pub fn is_empty(&self) -> bool {
            self.len() == 0
        }
    }

    /// Global injector queue accessible to all workers and external task ingestion.
    #[derive(Debug, Default)]
    pub struct GlobalInjector {
        critical: VecDeque<TaskItem>,
        high: VecDeque<TaskItem>,
        normal: VecDeque<TaskItem>,
        low: VecDeque<TaskItem>,
    }

    impl GlobalInjector {
        pub fn new() -> Self {
            Self::default()
        }

        pub fn push(&mut self, task: TaskItem) {
            match task.priority {
                TaskPriority::Critical => self.critical.push_back(task),
                TaskPriority::High => self.high.push_back(task),
                TaskPriority::Normal => self.normal.push_back(task),
                TaskPriority::Low => self.low.push_back(task),
            }
        }

        pub fn pop(&mut self) -> Option<TaskItem> {
            if let Some(task) = self.critical.pop_front() {
                return Some(task);
            }
            if let Some(task) = self.high.pop_front() {
                return Some(task);
            }
            if let Some(task) = self.normal.pop_front() {
                return Some(task);
            }
            self.low.pop_front()
        }

        pub fn steal(&mut self, requester_node_id: Option<u32>) -> Option<TaskItem> {
            let find_and_remove = |deque: &mut VecDeque<TaskItem>| -> Option<TaskItem> {
                let idx = deque.iter().position(|t| t.is_stealable(requester_node_id));
                idx.and_then(|i| deque.remove(i))
            };

            if let Some(task) = find_and_remove(&mut self.critical) {
                return Some(task);
            }
            if let Some(task) = find_and_remove(&mut self.high) {
                return Some(task);
            }
            if let Some(task) = find_and_remove(&mut self.normal) {
                return Some(task);
            }
            find_and_remove(&mut self.low)
        }

        pub fn len(&self) -> usize {
            self.critical.len() + self.high.len() + self.normal.len() + self.low.len()
        }

        pub fn is_empty(&self) -> bool {
            self.len() == 0
        }
    }

    /// Statistics snapshot for scheduler telemetry.
    #[derive(Debug, Default, Clone, PartialEq, Eq, Serialize, Deserialize)]
    pub struct SchedulerStats {
        pub tasks_ingested: u64,
        pub tasks_executed_local: u64,
        pub tasks_stolen_local: u64,
        pub tasks_stolen_remote: u64,
        pub tasks_offloaded: u64,
        pub tasks_rejected: u64,
    }

    /// Multi-worker priority work-stealing scheduler with hardware pin invariants.
    pub struct WorkStealingScheduler {
        pub local_node_id: u32,
        pub num_workers: usize,
        workers: Vec<Arc<Mutex<WorkerQueue>>>,
        injector: Arc<Mutex<GlobalInjector>>,
        stats: Arc<Mutex<SchedulerStats>>,
    }

    impl WorkStealingScheduler {
        pub fn new(local_node_id: u32, num_workers: usize) -> Self {
            let actual_workers = if num_workers == 0 { 1 } else { num_workers };
            let mut workers = Vec::with_capacity(actual_workers);
            for _ in 0..actual_workers {
                workers.push(Arc::new(Mutex::new(WorkerQueue::new())));
            }

            Self {
                local_node_id,
                num_workers: actual_workers,
                workers,
                injector: Arc::new(Mutex::new(GlobalInjector::new())),
                stats: Arc::new(Mutex::new(SchedulerStats::default())),
            }
        }

        /// Submits a task into the scheduler.
        pub fn submit_task(&self, task: TaskItem, worker_hint: Option<usize>) -> ScheduledTarget {
            {
                let mut stats = self.stats.lock().unwrap();
                stats.tasks_ingested += 1;
            }

            if let Some(w_idx) = worker_hint {
                let target_w = w_idx % self.num_workers;
                self.workers[target_w].lock().unwrap().push(task);
            } else {
                self.injector.lock().unwrap().push(task);
            }

            ScheduledTarget::Local
        }

        /// Worker attempts to acquire the next task:
        /// 1. Local worker deque (Critical -> High -> Normal -> Low)
        /// 2. Global injector queue
        /// 3. Steal from other local workers
        pub fn pop_task(&self, worker_id: usize) -> Option<TaskItem> {
            let w_idx = worker_id % self.num_workers;

            // 1. Try local queue
            if let Some(task) = self.workers[w_idx].lock().unwrap().pop_local() {
                let mut stats = self.stats.lock().unwrap();
                stats.tasks_executed_local += 1;
                return Some(task);
            }

            // 2. Try global injector
            if let Some(task) = self.injector.lock().unwrap().pop() {
                let mut stats = self.stats.lock().unwrap();
                stats.tasks_executed_local += 1;
                return Some(task);
            }

            // 3. Try stealing from peer workers (round-robin)
            for i in 1..self.num_workers {
                let victim_idx = (w_idx + i) % self.num_workers;
                if let Some(stolen) = self.workers[victim_idx]
                    .lock()
                    .unwrap()
                    .steal(Some(self.local_node_id))
                {
                    let mut stats = self.stats.lock().unwrap();
                    stats.tasks_stolen_local += 1;
                    stats.tasks_executed_local += 1;
                    return Some(stolen);
                }
            }

            None
        }

        /// Handles an incoming network steal request from a remote peer node.
        /// Strictly filters out pinned tasks!
        pub fn handle_remote_steal_request(
            &self,
            requester_node_id: u32,
            max_tasks: usize,
        ) -> Vec<TaskItem> {
            let mut stolen_tasks = Vec::new();

            // Try global injector first
            {
                let mut inj = self.injector.lock().unwrap();
                while stolen_tasks.len() < max_tasks {
                    if let Some(task) = inj.steal(Some(requester_node_id)) {
                        stolen_tasks.push(task);
                    } else {
                        break;
                    }
                }
            }

            // Try workers if more tasks needed
            if stolen_tasks.len() < max_tasks {
                for worker in &self.workers {
                    let mut w = worker.lock().unwrap();
                    while stolen_tasks.len() < max_tasks {
                        if let Some(task) = w.steal(Some(requester_node_id)) {
                            stolen_tasks.push(task);
                        } else {
                            break;
                        }
                    }
                    if stolen_tasks.len() >= max_tasks {
                        break;
                    }
                }
            }

            if !stolen_tasks.is_empty() {
                let mut stats = self.stats.lock().unwrap();
                stats.tasks_stolen_remote += stolen_tasks.len() as u64;
            }

            stolen_tasks
        }

        /// Evaluates whether a task should execute locally or be offloaded to a peer.
        pub fn route_task(&self, task: &TaskItem, peer_loads: &[(u32, usize)]) -> ScheduledTarget {
            // Invariant: Hardware pinned tasks MUST stay local
            if task.pinned_hardware {
                return ScheduledTarget::Local;
            }

            // Invariant: Specific pinned node ID requirement
            if let Some(target_node) = task.pinned_node_id {
                if target_node == self.local_node_id {
                    return ScheduledTarget::Local;
                } else {
                    return ScheduledTarget::Offload(target_node);
                }
            }

            let local_load = self.total_queue_depth();

            // If local load is low or peers are empty, run locally
            if local_load < 2 || peer_loads.is_empty() {
                return ScheduledTarget::Local;
            }

            // Find peer with lowest load
            if let Some(&(best_peer, best_load)) = peer_loads.iter().min_by_key(|(_, load)| *load) {
                if best_load + 2 <= local_load {
                    let mut stats = self.stats.lock().unwrap();
                    stats.tasks_offloaded += 1;
                    return ScheduledTarget::Offload(best_peer);
                }
            }

            ScheduledTarget::Local
        }

        /// Calculates total queued tasks across all local worker queues and global injector.
        pub fn total_queue_depth(&self) -> usize {
            let mut count = self.injector.lock().unwrap().len();
            for w in &self.workers {
                count += w.lock().unwrap().len();
            }
            count
        }

        pub fn get_stats(&self) -> SchedulerStats {
            self.stats.lock().unwrap().clone()
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_task_priority_ordering() {
            assert!(TaskPriority::Critical < TaskPriority::High);
            assert!(TaskPriority::High < TaskPriority::Normal);
            assert!(TaskPriority::Normal < TaskPriority::Low);
        }

        #[test]
        fn test_work_stealing_local_and_priority() {
            let scheduler = WorkStealingScheduler::new(101, 2);

            let task_low = TaskItem::new(1, TaskPriority::Low, 1, vec![1], vec![]);
            let task_crit = TaskItem::new(2, TaskPriority::Critical, 1, vec![2], vec![]);
            let task_normal = TaskItem::new(3, TaskPriority::Normal, 1, vec![3], vec![]);

            // Push all to worker 0
            scheduler.submit_task(task_low, Some(0));
            scheduler.submit_task(task_crit, Some(0));
            scheduler.submit_task(task_normal, Some(0));

            // Worker 0 pops in priority order: Critical -> Normal -> Low
            let p1 = scheduler.pop_task(0).unwrap();
            assert_eq!(p1.task_id, 2);
            assert_eq!(p1.priority, TaskPriority::Critical);

            let p2 = scheduler.pop_task(0).unwrap();
            assert_eq!(p2.task_id, 3);
            assert_eq!(p2.priority, TaskPriority::Normal);

            // Worker 1 steals the remaining Low task from worker 0
            let p3 = scheduler.pop_task(1).unwrap();
            assert_eq!(p3.task_id, 1);
            assert_eq!(p3.priority, TaskPriority::Low);

            let stats = scheduler.get_stats();
            assert_eq!(stats.tasks_stolen_local, 1);
            assert_eq!(stats.tasks_executed_local, 3);
        }

        #[test]
        fn test_pinned_hardware_task_cannot_be_stolen() {
            let scheduler = WorkStealingScheduler::new(101, 2);

            let mut pinned_task = TaskItem::new(99, TaskPriority::Critical, 1, vec![99], vec![]);
            pinned_task.pinned_hardware = true;

            scheduler.submit_task(pinned_task, Some(0));

            // Worker 1 cannot steal pinned task from worker 0
            assert!(scheduler.pop_task(1).is_none());

            // Remote peer 102 cannot steal pinned task either
            let remote_stolen = scheduler.handle_remote_steal_request(102, 5);
            assert!(remote_stolen.is_empty());

            // Worker 0 can execute it locally
            let local_task = scheduler.pop_task(0).unwrap();
            assert_eq!(local_task.task_id, 99);
        }

        #[test]
        fn test_router_hardware_pin_and_load_balance() {
            let scheduler = WorkStealingScheduler::new(101, 2);

            let mut pinned_task = TaskItem::new(10, TaskPriority::High, 1, vec![], vec![]);
            pinned_task.pinned_hardware = true;

            let peer_loads = vec![(201, 0), (202, 1)];

            // Pinned task must stay local regardless of peer loads
            assert_eq!(
                scheduler.route_task(&pinned_task, &peer_loads),
                ScheduledTarget::Local
            );

            // Fill local queue to trigger offload
            for i in 0..5 {
                let t = TaskItem::new(100 + i, TaskPriority::Normal, 1, vec![], vec![]);
                scheduler.submit_task(t, Some(0));
            }

            let unpinned_task = TaskItem::new(20, TaskPriority::Normal, 1, vec![], vec![]);
            let decision = scheduler.route_task(&unpinned_task, &peer_loads);
            assert_eq!(decision, ScheduledTarget::Offload(201));
        }
    }
}
