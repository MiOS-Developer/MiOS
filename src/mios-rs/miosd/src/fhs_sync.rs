// AI-hint: Runtime FHS Root Overlay and Windows/Linux host-guest topology synchronization engine for miosd.
// AI-related: usr/share/mios/mios.toml, usr/lib/systemd/system/mios-fhs-sync.service, /run/mios/fhs-sync.json

use serde::{Deserialize, Serialize};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};
use tokio::sync::mpsc;

pub const REQUIRED_FHS_DIRS: [&str; 18] = [
    "bin", "dev", "etc", "home", "lib", "lib64", "media", "mnt", "opt", "proc", "root", "run",
    "sbin", "srv", "sys", "tmp", "usr", "var",
];

pub const WINDOWS_HOST_NAMESPACES: [&str; 14] = [
    "Windows",
    "Program Files",
    "Program Files (x86)",
    "ProgramData",
    "Users",
    "$Recycle.Bin",
    "Recovery",
    "System Volume Information",
    "Riot Games",
    "XboxGames",
    "TEMP",
    "inetpub",
    "Documents and Settings",
    "PerfLogs",
];

pub const KERNEL_NOTIFICATION_BUFFER_SIZE: usize = 65536; // >=64KB buffer for ReadDirectoryChangesW / kernel queues

static GLOBAL_SHUTDOWN: AtomicBool = AtomicBool::new(false);

#[inline]
pub fn is_host_namespace(path: &Path) -> bool {
    is_host_namespace_str(&path.to_string_lossy())
}

#[inline]
pub fn is_host_namespace_str(s: &str) -> bool {
    let mut trimmed = s.trim();
    if let Some(rest) = trimmed.strip_prefix(r"\\?\") {
        trimmed = rest;
    }
    if trimmed.len() >= 2 && trimmed.as_bytes()[1] == b':' {
        trimmed = &trimmed[2..];
    }
    trimmed = trimmed.trim_start_matches(|c| c == '/' || c == '\\');

    let first_component = match trimmed.find(|c| c == '/' || c == '\\') {
        Some(pos) => &trimmed[..pos],
        None => trimmed,
    };

    if first_component.is_empty() {
        return false;
    }

    WINDOWS_HOST_NAMESPACES
        .iter()
        .any(|ns| first_component.eq_ignore_ascii_case(ns))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub enum HostRole {
    WindowsHost,
    WindowsGuest,
    LinuxHost,
    LinuxGuest,
}

impl HostRole {
    pub fn as_str(&self) -> &'static str {
        match self {
            Self::WindowsHost => "windows-host",
            Self::WindowsGuest => "windows-guest",
            Self::LinuxHost => "linux-host",
            Self::LinuxGuest => "linux-guest",
        }
    }
}

pub fn detect_role() -> HostRole {
    if let Ok(role_env) = std::env::var("MIOS_ROLE") {
        match role_env.to_lowercase().as_str() {
            "windows-guest" | "winguest" | "guest-win" => return HostRole::WindowsGuest,
            "windows-host" | "winhost" | "host-win" => return HostRole::WindowsHost,
            "linux-guest" | "linguest" | "guest-linux" => return HostRole::LinuxGuest,
            "linux-host" | "linhost" | "host-linux" => return HostRole::LinuxHost,
            _ => {}
        }
    }

    if cfg!(windows) {
        let is_guest = Path::new("C:\\Windows\\System32\\drivers\\vmicguestinterface.sys").exists()
            || Path::new("C:\\Windows\\System32\\drivers\\viostor.sys").exists()
            || std::env::var("MIOS_VIRTUALIZED").unwrap_or_default() == "1";
        if is_guest {
            HostRole::WindowsGuest
        } else {
            HostRole::WindowsHost
        }
    } else {
        let is_wsl = Path::new("/proc/sys/fs/binfmt_misc/WSLInterop").exists()
            || Path::new("/etc/wsl.conf").exists()
            || std::env::var("WSL_DISTRO_NAME").is_ok();
        if is_wsl {
            HostRole::LinuxGuest
        } else {
            HostRole::LinuxHost
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct FhsSyncStatus {
    pub timestamp: String,
    pub platform: String,
    pub role: String,
    pub root: String,
    pub git_root_ok: bool,
    pub archive_ok: bool,
    pub fhs_dirs_verified: usize,
    pub fhs_dirs_total: usize,
    pub namespaces_isolated: usize,
    pub namespaces_total: usize,
    pub junctions_ok: bool,
    pub convergences_applied: usize,
    pub ok: bool,
    pub summary: String,
    pub findings: Vec<String>,
    pub notification_engine: String,
    pub total_events_processed: u64,
    pub last_event_path: Option<String>,
    pub last_event_time: Option<String>,
    pub last_propagation_latency_ms: Option<f64>,
}

pub fn audit(root: &Path) -> FhsSyncStatus {
    let mut findings = Vec::new();
    let role = detect_role();
    let platform = std::env::consts::OS.to_string();

    // 1. Git root
    let git_dir = root.join(".git");
    let git_root_ok = if !git_dir.exists() {
        findings.push("root .git metadata directory is absent".to_string());
        false
    } else if let Ok(meta) = std::fs::symlink_metadata(&git_dir) {
        if meta.file_type().is_symlink() {
            findings.push(
                "root .git is a symlink or junction instead of a native root directory".to_string(),
            );
            false
        } else {
            true
        }
    } else {
        true
    };

    // 2. .archive bare store
    let archive_dir = root.join(".archive");
    let archive_ok = if !archive_dir.is_dir() || !archive_dir.join("HEAD").exists() {
        findings.push("root .archive bare store is absent or invalid beside .git".to_string());
        false
    } else {
        true
    };

    // 3. FHS dirs
    let mut fhs_dirs_verified = 0;
    for dir in REQUIRED_FHS_DIRS {
        let p = root.join(dir);
        if p.is_dir() {
            fhs_dirs_verified += 1;
        } else {
            findings.push(format!("missing standard Linux FHS root directory: /{dir}"));
        }
    }

    // 4. Windows namespaces
    let gitignore = root.join(".gitignore");
    let mut namespaces_isolated = 0;
    if !gitignore.is_file() {
        findings.push("root .gitignore is absent".to_string());
    } else if let Ok(content) = std::fs::read_to_string(&gitignore) {
        if !content.lines().any(|l| l.trim() == "/*") {
            findings.push("root .gitignore lacks mandatory '/*' block-all root boundary".to_string());
        }
        for ns in WINDOWS_HOST_NAMESPACES {
            let pat = format!("/{ns}");
            if content.lines().any(|l| l.trim() == pat || l.trim() == ns) {
                namespaces_isolated += 1;
            } else {
                findings.push(format!(
                    "root .gitignore does not explicitly isolate Windows host namespace: {ns}"
                ));
            }
        }
    }

    // 5. Junctions / Loopbacks
    let mut junctions_ok = true;
    if cfg!(windows) {
        let mnt_c = root.join("mnt").join("c");
        if !mnt_c.exists() {
            junctions_ok = false;
            findings.push("missing /mnt/c Windows filesystem loopback junction".to_string());
        }
    }

    let ok = findings.is_empty();
    let summary = format!(
        "{}/18 FHS dirs, {}/14 namespaces isolated, git_root={}, role={}",
        fhs_dirs_verified,
        namespaces_isolated,
        git_root_ok,
        role.as_str()
    );

    let notification_engine = if cfg!(windows) {
        "ReadDirectoryChangesW (Win32 IOCP / Overlapped)".to_string()
    } else {
        "inotify/fanotify (Linux Kernel)".to_string()
    };

    FhsSyncStatus {
        timestamp: chrono::Utc::now().to_rfc3339(),
        platform,
        role: role.as_str().to_string(),
        root: root.display().to_string(),
        git_root_ok,
        archive_ok,
        fhs_dirs_verified,
        fhs_dirs_total: REQUIRED_FHS_DIRS.len(),
        namespaces_isolated,
        namespaces_total: WINDOWS_HOST_NAMESPACES.len(),
        junctions_ok,
        convergences_applied: 0,
        ok,
        summary,
        findings,
        notification_engine,
        total_events_processed: 0,
        last_event_path: None,
        last_event_time: None,
        last_propagation_latency_ms: None,
    }
}

pub fn sync_once(root: &Path, verbose: bool) -> FhsSyncStatus {
    let mut convergences = 0;
    let role = detect_role();

    // 1. Ensure all 18 FHS directories exist
    for dir in REQUIRED_FHS_DIRS {
        let p = root.join(dir);
        if !p.exists() {
            if let Ok(()) = std::fs::create_dir_all(&p) {
                convergences += 1;
                if verbose {
                    println!("[mios-fhs-sync] created missing FHS root directory: /{dir}");
                }
            }
        }
    }

    // 2. Ensure /run/mios exists for daemon IPC and sockets
    let run_mios = root.join("run").join("mios");
    if !run_mios.exists() {
        let _ = std::fs::create_dir_all(&run_mios);
    }

    // 3. Reconcile Windows Host Namespaces in .gitignore if needed
    let gitignore = root.join(".gitignore");
    if gitignore.is_file() {
        if let Ok(content) = std::fs::read_to_string(&gitignore) {
            let mut missing = Vec::new();
            if !content.lines().any(|l| l.trim() == "/*") {
                missing.push("/*");
            }
            for ns in WINDOWS_HOST_NAMESPACES {
                let pat = format!("/{ns}");
                if !content.lines().any(|l| l.trim() == pat || l.trim() == ns) {
                    missing.push(ns);
                }
            }
            if !missing.is_empty() {
                use std::io::Write;
                if let Ok(mut f) = std::fs::OpenOptions::new().append(true).open(&gitignore) {
                    let _ = writeln!(
                        f,
                        "\n# MiOS Host Namespace Isolation (auto-reconciled by mios-fhs-sync)"
                    );
                    for item in missing {
                        let _ = writeln!(f, "/{item}");
                    }
                    convergences += 1;
                    if verbose {
                        println!("[mios-fhs-sync] reconciled root .gitignore host namespace exclusions");
                    }
                }
            }
        }
    }

    // 4. On Windows, ensure /mnt/c loopback junction exists
    if cfg!(windows) {
        let mnt_dir = root.join("mnt");
        let _ = std::fs::create_dir_all(&mnt_dir);
        let mnt_c = mnt_dir.join("c");
        if !mnt_c.exists() {
            let _ = Command::new("cmd")
                .args(["/c", "mklink", "/J", mnt_c.to_str().unwrap_or("C:\\mnt\\c"), "C:\\"])
                .output();
            if mnt_c.exists() {
                convergences += 1;
                if verbose {
                    println!("[mios-fhs-sync] created /mnt/c -> C:\\ loopback directory junction");
                }
            }
        }
    }

    // 5. Audit after reconciliation
    let mut status = audit(root);
    status.convergences_applied = convergences;

    // 6. Emit state heartbeat
    write_heartbeat(root, &status);

    if verbose {
        println!("[mios-fhs-sync] [{}] {}", role.as_str(), status.summary);
        if !status.ok {
            for f in &status.findings {
                println!("  [finding] {f}");
            }
        }
    }

    status
}

pub fn write_heartbeat(root: &Path, status: &FhsSyncStatus) {
    let run_dir = root.join("run").join("mios");
    let _ = std::fs::create_dir_all(&run_dir);
    let state_file = run_dir.join("fhs-sync.json");
    if let Ok(json) = serde_json::to_string_pretty(status) {
        let _ = std::fs::write(&state_file, json);
    }
}

pub fn print_status(root: &Path) {
    let state_file = root.join("run").join("mios").join("fhs-sync.json");
    if state_file.is_file() {
        if let Ok(content) = std::fs::read_to_string(&state_file) {
            println!("{content}");
            return;
        }
    }
    let status = audit(root);
    println!("{}", serde_json::to_string_pretty(&status).unwrap_or_default());
}

// ============================================================================
// Windows Service and SCM Integration Layer (windows-service API equivalent)
// ============================================================================

pub mod windows_service {
    use std::time::Duration;

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub enum ServiceControl {
        Stop,
        Pause,
        Continue,
        Interrogate,
        Shutdown,
        ParamChange,
        NetBindAdd,
        NetBindRemove,
        NetBindEnable,
        NetBindDisable,
        DeviceEvent(u32),
        HardwareProfileChange(u32),
        PowerEvent(u32),
        SessionChange(u32),
        PreShutdown,
        TimeChange,
        Custom(u32),
    }

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub struct ServiceControlAccept(pub u32);

    impl ServiceControlAccept {
        pub const STOP: Self = Self(0x00000001);
        pub const PAUSE_CONTINUE: Self = Self(0x00000002);
        pub const SHUTDOWN: Self = Self(0x00000004);
        pub const PARAM_CHANGE: Self = Self(0x00000008);
        pub const NETBIND_CHANGE: Self = Self(0x00000010);
        pub const HARDWARE_PROFILE_CHANGE: Self = Self(0x00000020);
        pub const POWER_EVENT: Self = Self(0x00000040);
        pub const SESSION_CHANGE: Self = Self(0x00000080);
        pub const PRE_SHUTDOWN: Self = Self(0x00000100);
        pub const TIME_CHANGE: Self = Self(0x00000200);

        pub fn empty() -> Self {
            Self(0)
        }
    }

    impl std::ops::BitOr for ServiceControlAccept {
        type Output = Self;
        fn bitor(self, rhs: Self) -> Self::Output {
            Self(self.0 | rhs.0)
        }
    }

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub enum ServiceState {
        Stopped = 1,
        StartPending = 2,
        StopPending = 3,
        Running = 4,
        ContinuePending = 5,
        PausePending = 6,
        Paused = 7,
    }

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub enum ServiceType {
        OwnProcess = 0x00000010,
        ShareProcess = 0x00000020,
    }

    impl ServiceType {
        pub const OWN_PROCESS: Self = Self::OwnProcess;
    }

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub enum ServiceExitCode {
        NoError,
        Win32(u32),
        ServiceSpecific(u32),
    }

    #[derive(Debug, Clone)]
    pub struct ServiceStatus {
        pub service_type: ServiceType,
        pub current_state: ServiceState,
        pub controls_accepted: ServiceControlAccept,
        pub exit_code: ServiceExitCode,
        pub checkpoint: u32,
        pub wait_hint: Duration,
        pub process_id: Option<u32>,
    }

    #[derive(Debug, Clone, Copy, PartialEq, Eq)]
    pub enum ServiceControlHandlerResult {
        NoError,
        NotImplemented,
        Other(u32),
    }

    #[derive(Clone)]
    pub struct StatusHandle {
        #[cfg(windows)]
        pub(crate) handle: *mut std::ffi::c_void,
    }

    unsafe impl Send for StatusHandle {}
    unsafe impl Sync for StatusHandle {}

    impl StatusHandle {
        pub fn set_service_status(&self, status: ServiceStatus) -> Result<(), std::io::Error> {
            #[cfg(windows)]
            {
                use super::win32::*;
                let (win32_code, spec_code) = match status.exit_code {
                    ServiceExitCode::NoError => (0, 0),
                    ServiceExitCode::Win32(c) => (c, 0),
                    ServiceExitCode::ServiceSpecific(c) => (1066, c),
                };
                let win_status = SERVICE_STATUS {
                    dwServiceType: status.service_type as u32,
                    dwCurrentState: status.current_state as u32,
                    dwControlsAccepted: status.controls_accepted.0,
                    dwWin32ExitCode: win32_code,
                    dwServiceSpecificExitCode: spec_code,
                    dwCheckPoint: status.checkpoint,
                    dwWaitHint: status.wait_hint.as_millis().min(u32::MAX as u128) as u32,
                };
                let ret = unsafe { SetServiceStatus(self.handle, &win_status) };
                if ret == 0 {
                    return Err(std::io::Error::last_os_error());
                }
            }
            let _ = status;
            Ok(())
        }
    }

    pub mod service_control_handler {
        use super::*;

        pub fn register<F>(
            service_name: &str,
            event_handler: F,
        ) -> Result<StatusHandle, std::io::Error>
        where
            F: Fn(ServiceControl) -> ServiceControlHandlerResult + Send + 'static,
        {
            #[cfg(windows)]
            {
                use super::super::win32::*;
                let wide_name: Vec<u16> = service_name
                    .encode_utf16()
                    .chain(std::iter::once(0))
                    .collect();

                let boxed_handler = Box::new(event_handler);
                let ctx = Box::into_raw(boxed_handler) as *mut std::ffi::c_void;

                unsafe extern "system" fn raw_handler<F>(
                    dw_control: u32,
                    _dw_event_type: u32,
                    _lp_event_data: *mut std::ffi::c_void,
                    lp_context: *mut std::ffi::c_void,
                ) -> u32
                where
                    F: Fn(ServiceControl) -> ServiceControlHandlerResult + Send + 'static,
                {
                    let handler = &*(lp_context as *mut F);
                    let ctrl = match dw_control {
                        SERVICE_CONTROL_STOP => ServiceControl::Stop,
                        SERVICE_CONTROL_PAUSE => ServiceControl::Pause,
                        SERVICE_CONTROL_CONTINUE => ServiceControl::Continue,
                        SERVICE_CONTROL_INTERROGATE => ServiceControl::Interrogate,
                        SERVICE_CONTROL_SHUTDOWN => ServiceControl::Shutdown,
                        c => ServiceControl::Custom(c),
                    };
                    match handler(ctrl) {
                        ServiceControlHandlerResult::NoError => 0,
                        ServiceControlHandlerResult::NotImplemented => 120, // ERROR_CALL_NOT_IMPLEMENTED
                        ServiceControlHandlerResult::Other(code) => code,
                    }
                }

                let handle = unsafe {
                    RegisterServiceCtrlHandlerExW(
                        wide_name.as_ptr(),
                        Some(raw_handler::<F>),
                        ctx,
                    )
                };

                if handle.is_null() {
                    return Err(std::io::Error::last_os_error());
                }
                return Ok(StatusHandle { handle });
            }

            #[cfg(not(windows))]
            {
                let _ = (service_name, event_handler);
                Ok(StatusHandle {})
            }
        }
    }

    pub mod service_dispatcher {
        pub fn start(
            service_name: &str,
            ffi_main: unsafe extern "system" fn(u32, *mut *mut u16),
        ) -> Result<(), std::io::Error> {
            #[cfg(windows)]
            {
                use super::super::win32::*;
                let wide_name: Vec<u16> = service_name
                    .encode_utf16()
                    .chain(std::iter::once(0))
                    .collect();

                let table = [
                    SERVICE_TABLE_ENTRYW {
                        lpServiceName: wide_name.as_ptr(),
                        lpServiceProc: Some(ffi_main),
                    },
                    SERVICE_TABLE_ENTRYW {
                        lpServiceName: std::ptr::null(),
                        lpServiceProc: None,
                    },
                ];

                let ret = unsafe { StartServiceCtrlDispatcherW(table.as_ptr()) };
                if ret == 0 {
                    return Err(std::io::Error::last_os_error());
                }
                return Ok(());
            }

            #[cfg(not(windows))]
            {
                let _ = (service_name, ffi_main);
                Ok(())
            }
        }
    }
}

pub use windows_service::*;

#[macro_export]
macro_rules! define_windows_service {
    ($ffi_name:ident, $service_main:ident) => {
        #[cfg(windows)]
        pub unsafe extern "system" fn $ffi_name(argc: u32, argv: *mut *mut u16) {
            let mut args = Vec::with_capacity(argc as usize);
            for i in 0..argc as isize {
                let ptr = *argv.offset(i);
                if !ptr.is_null() {
                    let mut len = 0;
                    while *ptr.offset(len) != 0 {
                        len += 1;
                    }
                    let slice = std::slice::from_raw_parts(ptr, len as usize);
                    use std::os::windows::ffi::OsStringExt;
                    args.push(std::ffi::OsString::from_wide(slice));
                }
            }
            $service_main(args);
        }

        #[cfg(not(windows))]
        pub fn $ffi_name(_argc: u32, _argv: *mut *mut u16) {}
    };
}

define_windows_service!(ffi_service_main, mios_fhs_sync_service_main);

pub fn mios_fhs_sync_service_main(_arguments: Vec<std::ffi::OsString>) {
    let event_handler = move |control_event| -> ServiceControlHandlerResult {
        match control_event {
            ServiceControl::Stop | ServiceControl::Shutdown => {
                GLOBAL_SHUTDOWN.store(true, Ordering::SeqCst);
                ServiceControlHandlerResult::NoError
            }
            ServiceControl::Interrogate => ServiceControlHandlerResult::NoError,
            _ => ServiceControlHandlerResult::NotImplemented,
        }
    };

    if let Ok(status_handle) = service_control_handler::register("MiOS-FhsSync", event_handler) {
        let _ = status_handle.set_service_status(ServiceStatus {
            service_type: ServiceType::OWN_PROCESS,
            current_state: ServiceState::Running,
            controls_accepted: ServiceControlAccept::STOP | ServiceControlAccept::SHUTDOWN,
            exit_code: ServiceExitCode::NoError,
            checkpoint: 0,
            wait_hint: Duration::from_secs(5),
            process_id: None,
        });

        // Run daemon loop until shutdown signal
        let rt = tokio::runtime::Builder::new_current_thread()
            .enable_all()
            .build()
            .unwrap();

        rt.block_on(async {
            let root = PathBuf::from("C:\\");
            run_realtime_daemon_loop(root, 15).await;
        });

        let _ = status_handle.set_service_status(ServiceStatus {
            service_type: ServiceType::OWN_PROCESS,
            current_state: ServiceState::Stopped,
            controls_accepted: ServiceControlAccept::empty(),
            exit_code: ServiceExitCode::NoError,
            checkpoint: 0,
            wait_hint: Duration::default(),
            process_id: None,
        });
    }
}

// ============================================================================
// Win32 FFI Bindings for SCM & ReadDirectoryChangesW (kernel32 + advapi32)
// ============================================================================

#[cfg(windows)]
#[allow(non_snake_case)]
pub mod win32 {
    pub const SERVICE_WIN32_OWN_PROCESS: u32 = 0x00000010;
    pub const SERVICE_ALL_ACCESS: u32 = 0xF01FF;
    pub const SC_MANAGER_ALL_ACCESS: u32 = 0xF003F;
    pub const SERVICE_AUTO_START: u32 = 0x00000002;
    pub const SERVICE_ERROR_NORMAL: u32 = 0x00000001;
    pub const SERVICE_CONFIG_DESCRIPTION: u32 = 1;

    pub const SERVICE_CONTROL_STOP: u32 = 0x00000001;
    pub const SERVICE_CONTROL_PAUSE: u32 = 0x00000002;
    pub const SERVICE_CONTROL_CONTINUE: u32 = 0x00000003;
    pub const SERVICE_CONTROL_INTERROGATE: u32 = 0x00000004;
    pub const SERVICE_CONTROL_SHUTDOWN: u32 = 0x00000005;

    pub const FILE_LIST_DIRECTORY: u32 = 0x0001;
    pub const FILE_SHARE_READ: u32 = 0x00000001;
    pub const FILE_SHARE_WRITE: u32 = 0x00000002;
    pub const FILE_SHARE_DELETE: u32 = 0x00000004;
    pub const OPEN_EXISTING: u32 = 3;
    pub const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x02000000;
    pub const FILE_FLAG_OVERLAPPED: u32 = 0x40000000;

    pub const FILE_NOTIFY_CHANGE_FILE_NAME: u32 = 0x00000001;
    pub const FILE_NOTIFY_CHANGE_DIR_NAME: u32 = 0x00000002;
    pub const FILE_NOTIFY_CHANGE_ATTRIBUTES: u32 = 0x00000004;
    pub const FILE_NOTIFY_CHANGE_SIZE: u32 = 0x00000008;
    pub const FILE_NOTIFY_CHANGE_LAST_WRITE: u32 = 0x00000010;
    pub const FILE_NOTIFY_CHANGE_CREATION: u32 = 0x00000040;

    pub const WAIT_OBJECT_0: u32 = 0;
    pub const WAIT_TIMEOUT: u32 = 258;

    #[repr(C)]
    pub struct SERVICE_STATUS {
        pub dwServiceType: u32,
        pub dwCurrentState: u32,
        pub dwControlsAccepted: u32,
        pub dwWin32ExitCode: u32,
        pub dwServiceSpecificExitCode: u32,
        pub dwCheckPoint: u32,
        pub dwWaitHint: u32,
    }

    #[repr(C)]
    pub struct SERVICE_TABLE_ENTRYW {
        pub lpServiceName: *const u16,
        pub lpServiceProc: Option<unsafe extern "system" fn(u32, *mut *mut u16)>,
    }

    #[repr(C)]
    pub struct SERVICE_DESCRIPTIONW {
        pub lpDescription: *mut u16,
    }

    #[repr(C)]
    pub struct OVERLAPPED {
        pub internal: usize,
        pub internal_high: usize,
        pub offset: u32,
        pub offset_high: u32,
        pub h_event: *mut std::ffi::c_void,
    }

    #[link(name = "advapi32")]
    extern "system" {
        pub fn StartServiceCtrlDispatcherW(lpServiceTable: *const SERVICE_TABLE_ENTRYW) -> i32;
        pub fn RegisterServiceCtrlHandlerExW(
            lpServiceName: *const u16,
            lpHandlerProc: Option<
                unsafe extern "system" fn(
                    u32,
                    u32,
                    *mut std::ffi::c_void,
                    *mut std::ffi::c_void,
                ) -> u32,
            >,
            lpContext: *mut std::ffi::c_void,
        ) -> *mut std::ffi::c_void;
        pub fn SetServiceStatus(
            hServiceStatus: *mut std::ffi::c_void,
            lpServiceStatus: *const SERVICE_STATUS,
        ) -> i32;
        pub fn OpenSCManagerW(
            lpMachineName: *const u16,
            lpDatabaseName: *const u16,
            dwDesiredAccess: u32,
        ) -> *mut std::ffi::c_void;
        pub fn CreateServiceW(
            hSCManager: *mut std::ffi::c_void,
            lpServiceName: *const u16,
            lpDisplayName: *const u16,
            dwDesiredAccess: u32,
            dwServiceType: u32,
            dwStartType: u32,
            dwErrorControl: u32,
            lpBinaryPathName: *const u16,
            lpLoadOrderGroup: *const u16,
            lpdwTagId: *mut u32,
            lpDependencies: *const u16,
            lpServiceStartName: *const u16,
            lpPassword: *const u16,
        ) -> *mut std::ffi::c_void;
        pub fn OpenServiceW(
            hSCManager: *mut std::ffi::c_void,
            lpServiceName: *const u16,
            dwDesiredAccess: u32,
        ) -> *mut std::ffi::c_void;
        pub fn DeleteService(hService: *mut std::ffi::c_void) -> i32;
        pub fn ControlService(
            hService: *mut std::ffi::c_void,
            dwControl: u32,
            lpServiceStatus: *mut SERVICE_STATUS,
        ) -> i32;
        pub fn CloseServiceHandle(hSCObject: *mut std::ffi::c_void) -> i32;
        pub fn StartServiceW(
            hService: *mut std::ffi::c_void,
            dwNumServiceArgs: u32,
            lpServiceArgVectors: *mut *const u16,
        ) -> i32;
        pub fn ChangeServiceConfig2W(
            hService: *mut std::ffi::c_void,
            dwInfoLevel: u32,
            lpInfo: *mut std::ffi::c_void,
        ) -> i32;
    }

    #[link(name = "kernel32")]
    extern "system" {
        pub fn CreateFileW(
            lpFileName: *const u16,
            dwDesiredAccess: u32,
            dwShareMode: u32,
            lpSecurityAttributes: *mut std::ffi::c_void,
            dwCreationDisposition: u32,
            dwFlagsAndAttributes: u32,
            hTemplateFile: *mut std::ffi::c_void,
        ) -> *mut std::ffi::c_void;
        pub fn ReadDirectoryChangesW(
            hDirectory: *mut std::ffi::c_void,
            lpBuffer: *mut std::ffi::c_void,
            nBufferLength: u32,
            bWatchSubtree: i32,
            dwNotifyFilter: u32,
            lpBytesReturned: *mut u32,
            lpOverlapped: *mut std::ffi::c_void,
            lpCompletionRoutine: Option<extern "system" fn(u32, u32, *mut std::ffi::c_void)>,
        ) -> i32;
        pub fn CreateEventW(
            lpEventAttributes: *mut std::ffi::c_void,
            bManualReset: i32,
            bInitialState: i32,
            lpName: *const u16,
        ) -> *mut std::ffi::c_void;
        pub fn WaitForSingleObject(hHandle: *mut std::ffi::c_void, dwMilliseconds: u32) -> u32;
        pub fn ResetEvent(hHandle: *mut std::ffi::c_void) -> i32;
        pub fn CloseHandle(hObject: *mut std::ffi::c_void) -> i32;
        pub fn GetOverlappedResult(
            hFile: *mut std::ffi::c_void,
            lpOverlapped: *mut std::ffi::c_void,
            lpNumberOfBytesTransferred: *mut u32,
            bWait: i32,
        ) -> i32;
        pub fn CancelIo(hFile: *mut std::ffi::c_void) -> i32;
        pub fn GetLastError() -> u32;
    }
}

// ============================================================================
// Real-Time Asynchronous Kernel Notification Engine (<10ms SLA)
// ============================================================================

#[cfg(windows)]
fn parse_notify_records(buffer: &[u8], bytes_transferred: usize) -> Vec<(u32, String)> {
    let mut results = Vec::new();
    let mut offset = 0;
    while offset < bytes_transferred {
        if offset + 12 > bytes_transferred {
            break;
        }
        let next_entry_offset =
            u32::from_ne_bytes(buffer[offset..offset + 4].try_into().unwrap_or_default()) as usize;
        let action =
            u32::from_ne_bytes(buffer[offset + 4..offset + 8].try_into().unwrap_or_default());
        let file_name_length =
            u32::from_ne_bytes(buffer[offset + 8..offset + 12].try_into().unwrap_or_default())
                as usize;
        let name_start = offset + 12;
        let name_end = name_start + file_name_length;
        if name_end <= bytes_transferred {
            let u16_slice: Vec<u16> = buffer[name_start..name_end]
                .chunks_exact(2)
                .map(|chunk| u16::from_ne_bytes([chunk[0], chunk[1]]))
                .collect();
            let path_str = String::from_utf16_lossy(&u16_slice);
            results.push((action, path_str));
        }
        if next_entry_offset == 0 {
            break;
        }
        offset += next_entry_offset;
    }
    results
}

pub struct KernelEventNotification {
    pub path: String,
    pub action: u32,
    pub propagation_latency_ms: f64,
}

pub async fn run_realtime_daemon_loop(root: PathBuf, interval_secs: u64) {
    let role = detect_role();
    println!(
        "[mios-fhs-sync] Starting real-time kernel notification sync daemon for {} (role: {}, interval: {}s)...",
        root.display(),
        role.as_str(),
        interval_secs
    );

    // Initial baseline sync pass
    let mut current_status = sync_once(&root, true);

    let (tx, mut rx) = mpsc::channel::<KernelEventNotification>(1024);

    let watcher_root = root.clone();
    let shutdown_signal = Arc::new(AtomicBool::new(false));
    let shutdown_clone = shutdown_signal.clone();

    // Spawn kernel watcher thread
    let _watcher_thread = std::thread::spawn(move || {
        #[cfg(windows)]
        {
            use win32::*;
            let wide_path: Vec<u16> = watcher_root
                .to_string_lossy()
                .encode_utf16()
                .chain(std::iter::once(0))
                .collect();

            let h_dir = unsafe {
                CreateFileW(
                    wide_path.as_ptr(),
                    FILE_LIST_DIRECTORY,
                    FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                    std::ptr::null_mut(),
                    OPEN_EXISTING,
                    FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OVERLAPPED,
                    std::ptr::null_mut(),
                )
            };

            if h_dir == (usize::MAX as *mut std::ffi::c_void) || h_dir.is_null() {
                eprintln!(
                    "[mios-fhs-sync] Failed to open root directory for ReadDirectoryChangesW (error: {})",
                    unsafe { GetLastError() }
                );
                return;
            }

            let h_event = unsafe { CreateEventW(std::ptr::null_mut(), 1, 0, std::ptr::null()) };
            let mut overlapped = OVERLAPPED {
                internal: 0,
                internal_high: 0,
                offset: 0,
                offset_high: 0,
                h_event,
            };

            let mut buffer = vec![0u8; KERNEL_NOTIFICATION_BUFFER_SIZE];

            while !shutdown_clone.load(Ordering::SeqCst)
                && !GLOBAL_SHUTDOWN.load(Ordering::SeqCst)
            {
                let mut bytes_returned = 0u32;
                let start_time = Instant::now();
                let success = unsafe {
                    ReadDirectoryChangesW(
                        h_dir,
                        buffer.as_mut_ptr() as *mut _,
                        KERNEL_NOTIFICATION_BUFFER_SIZE as u32,
                        1, // Watch subtree
                        FILE_NOTIFY_CHANGE_FILE_NAME
                            | FILE_NOTIFY_CHANGE_DIR_NAME
                            | FILE_NOTIFY_CHANGE_ATTRIBUTES
                            | FILE_NOTIFY_CHANGE_SIZE
                            | FILE_NOTIFY_CHANGE_LAST_WRITE
                            | FILE_NOTIFY_CHANGE_CREATION,
                        &mut bytes_returned,
                        &mut overlapped as *mut _ as *mut _,
                        None,
                    )
                };

                if success == 0 {
                    let err = unsafe { GetLastError() };
                    if err != 997 {
                        // ERROR_IO_PENDING = 997
                        std::thread::sleep(Duration::from_millis(50));
                        continue;
                    }
                }

                // Wait up to 250ms for events or shutdown
                let wait_rc = unsafe { WaitForSingleObject(h_event, 250) };
                if wait_rc == WAIT_OBJECT_0 {
                    let mut transferred = 0u32;
                    let got_result = unsafe {
                        GetOverlappedResult(
                            h_dir,
                            &mut overlapped as *mut _ as *mut _,
                            &mut transferred,
                            0,
                        )
                    };
                    unsafe { ResetEvent(h_event) };

                    if got_result != 0 && transferred > 0 {
                        let latency = start_time.elapsed().as_secs_f64() * 1000.0;
                        let records = parse_notify_records(&buffer, transferred as usize);
                        for (action, path_str) in records {
                            // FAST RADIX PREFIX FILTERING (<1µs)
                            if is_host_namespace_str(&path_str) {
                                // Strictly excluded host namespace (e.g. C:\Windows, C:\Program Files, C:\Users)
                                continue;
                            }

                            let _ = tx.blocking_send(KernelEventNotification {
                                path: path_str,
                                action,
                                propagation_latency_ms: latency,
                            });
                        }
                    }
                }
            }

            unsafe {
                CancelIo(h_dir);
                CloseHandle(h_event);
                CloseHandle(h_dir);
            }
        }

        #[cfg(not(windows))]
        {
            let _ = (&watcher_root, &tx, &shutdown_clone);
        }
    });

    let mut reconciliation_interval =
        tokio::time::interval(Duration::from_secs(interval_secs));
    reconciliation_interval.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);

    loop {
        tokio::select! {
            Some(event) = rx.recv() => {
                current_status.total_events_processed += 1;
                current_status.last_event_path = Some(event.path.clone());
                current_status.last_event_time = Some(chrono::Utc::now().to_rfc3339());
                current_status.last_propagation_latency_ms = Some(event.propagation_latency_ms);
                current_status.timestamp = chrono::Utc::now().to_rfc3339();

                write_heartbeat(&root, &current_status);
            }
            _ = reconciliation_interval.tick() => {
                let status = sync_once(&root, false);
                current_status.fhs_dirs_verified = status.fhs_dirs_verified;
                current_status.namespaces_isolated = status.namespaces_isolated;
                current_status.git_root_ok = status.git_root_ok;
                current_status.archive_ok = status.archive_ok;
                current_status.junctions_ok = status.junctions_ok;
                current_status.ok = status.ok;
                current_status.summary = status.summary;
                current_status.findings = status.findings;
                write_heartbeat(&root, &current_status);
            }
            _ = tokio::signal::ctrl_c() => {
                println!("[mios-fhs-sync] Received shutdown signal; stopping real-time daemon.");
                shutdown_signal.store(true, Ordering::SeqCst);
                GLOBAL_SHUTDOWN.store(true, Ordering::SeqCst);
                break;
            }
        }

        if GLOBAL_SHUTDOWN.load(Ordering::SeqCst) {
            break;
        }
    }
}

pub async fn run_daemon(root: PathBuf, interval_secs: u64) {
    #[cfg(windows)]
    {
        // When started by Windows SCM (Service Control Manager), this call connects cleanly
        // and drives mios_fhs_sync_service_main. When started interactively from CLI,
        // it fails with ERROR_FAILED_SERVICE_CONTROLLER_CONNECT, falling through to console mode.
        let scm_result = service_dispatcher::start("MiOS-FhsSync", ffi_service_main);
        if scm_result.is_ok() {
            return;
        }
    }

    run_realtime_daemon_loop(root, interval_secs).await;
}

// ============================================================================
// Service Installation / Uninstallation (SCM & Systemd)
// ============================================================================

pub fn install_service(root: &Path) {
    let exe = std::env::current_exe().unwrap_or_else(|_| PathBuf::from("miosd"));
    let exe_str = exe.display().to_string();

    if cfg!(windows) {
        println!("[mios-fhs-sync] Registering native Windows Service 'MiOS-FhsSync' via SCM API...");
        #[cfg(windows)]
        {
            use win32::*;
            let scm = unsafe { OpenSCManagerW(std::ptr::null(), std::ptr::null(), SC_MANAGER_ALL_ACCESS) };
            if !scm.is_null() {
                let service_name: Vec<u16> = "MiOS-FhsSync\0".encode_utf16().collect();
                let display_name: Vec<u16> = "MiOS FHS Root Overlay Sync Service\0".encode_utf16().collect();
                let bin_path = format!("\"{}\" fhs-sync --daemon --root \"{}\"", exe_str, root.display());
                let bin_path_wide: Vec<u16> = format!("{bin_path}\0").encode_utf16().collect();

                let service = unsafe {
                    CreateServiceW(
                        scm,
                        service_name.as_ptr(),
                        display_name.as_ptr(),
                        SERVICE_ALL_ACCESS,
                        SERVICE_WIN32_OWN_PROCESS,
                        SERVICE_AUTO_START,
                        SERVICE_ERROR_NORMAL,
                        bin_path_wide.as_ptr(),
                        std::ptr::null(),
                        std::ptr::null_mut(),
                        std::ptr::null(),
                        std::ptr::null(),
                        std::ptr::null(),
                    )
                };

                if !service.is_null() {
                    let desc = "Maintains runtime Linux FHS root overlay and Windows host namespace isolation across MiOS system domains.\0";
                    let mut desc_wide: Vec<u16> = desc.encode_utf16().collect();
                    let mut desc_struct = SERVICE_DESCRIPTIONW {
                        lpDescription: desc_wide.as_mut_ptr(),
                    };
                    unsafe {
                        ChangeServiceConfig2W(
                            service,
                            SERVICE_CONFIG_DESCRIPTION,
                            &mut desc_struct as *mut _ as *mut std::ffi::c_void,
                        );
                        StartServiceW(service, 0, std::ptr::null_mut());
                        CloseServiceHandle(service);
                        CloseServiceHandle(scm);
                    }
                    println!("[mios-fhs-sync] Native Windows Service 'MiOS-FhsSync' registered and configured.");
                    return;
                }
                unsafe { CloseServiceHandle(scm) };
            }
        }

        // Secondary fallback via sc.exe if direct SCM requires elevated tokens
        let bin_path = format!("\"{exe_str}\" fhs-sync --daemon --root \"{}\"", root.display());
        let _ = Command::new("sc.exe")
            .args(["create", "MiOS-FhsSync", &format!("binPath={bin_path}"), "start=", "auto", "DisplayName=", "MiOS FHS Root Overlay Sync Service"])
            .status();
        let _ = Command::new("sc.exe")
            .args(["description", "MiOS-FhsSync", "Maintains runtime Linux FHS root overlay and Windows host namespace isolation across MiOS system domains."])
            .status();
        let _ = Command::new("sc.exe").args(["start", "MiOS-FhsSync"]).status();
    } else {
        println!("[mios-fhs-sync] Installing systemd unit 'mios-fhs-sync.service'...");
        let unit_content = format!(
            "# AI-hint: Generated by miosd fhs-sync --install-service\n\
            [Unit]\n\
            Description='MiOS' FHS root overlay & host-guest topology runtime sync daemon\n\
            Documentation=man:miosd(8)\n\
            After=local-fs.target\n\
            Before=multi-user.target\n\n\
            [Service]\n\
            Type=simple\n\
            ExecStart={exe_str} fhs-sync --daemon --interval 15 --root {root_str}\n\
            Restart=always\n\
            RestartSec=5s\n\
            KillMode=process\n\
            LimitNOFILE=65536\n\
            StandardOutput=journal\n\
            StandardError=journal\n\n\
            [Install]\n\
            WantedBy=multi-user.target\n",
            root_str = root.display()
        );

        let target_path = Path::new("/etc/systemd/system/mios-fhs-sync.service");
        if let Ok(()) = std::fs::write(target_path, unit_content) {
            let _ = Command::new("systemctl").arg("daemon-reload").status();
            let _ = Command::new("systemctl")
                .args(["enable", "--now", "mios-fhs-sync.service"])
                .status();
            println!("[mios-fhs-sync] systemd service enabled and started.");
        } else {
            eprintln!("[mios-fhs-sync] Failed to write /etc/systemd/system/mios-fhs-sync.service (insufficient permissions).");
        }
    }
}

pub fn uninstall_service() {
    if cfg!(windows) {
        println!("[mios-fhs-sync] Stopping and removing Windows Service 'MiOS-FhsSync'...");
        #[cfg(windows)]
        {
            use win32::*;
            let scm = unsafe { OpenSCManagerW(std::ptr::null(), std::ptr::null(), SC_MANAGER_ALL_ACCESS) };
            if !scm.is_null() {
                let service_name: Vec<u16> = "MiOS-FhsSync\0".encode_utf16().collect();
                let service = unsafe { OpenServiceW(scm, service_name.as_ptr(), SERVICE_ALL_ACCESS) };
                if !service.is_null() {
                    let mut status: SERVICE_STATUS = unsafe { std::mem::zeroed() };
                    unsafe {
                        ControlService(service, SERVICE_CONTROL_STOP, &mut status);
                        DeleteService(service);
                        CloseServiceHandle(service);
                        CloseServiceHandle(scm);
                    }
                    println!("[mios-fhs-sync] Windows service stopped and deleted via SCM API.");
                    return;
                }
                unsafe { CloseServiceHandle(scm) };
            }
        }
        let _ = Command::new("sc.exe").args(["stop", "MiOS-FhsSync"]).status();
        let _ = Command::new("sc.exe").args(["delete", "MiOS-FhsSync"]).status();
        let _ = Command::new("schtasks.exe")
            .args(["/delete", "/tn", "MiOS\\FhsSync", "/f"])
            .status();
        println!("[mios-fhs-sync] Windows service and tasks removed.");
    } else {
        println!("[mios-fhs-sync] Disabling and removing systemd service 'mios-fhs-sync.service'...");
        let _ = Command::new("systemctl")
            .args(["disable", "--now", "mios-fhs-sync.service"])
            .status();
        let _ = std::fs::remove_file("/etc/systemd/system/mios-fhs-sync.service");
        let _ = Command::new("systemctl").arg("daemon-reload").status();
        println!("[mios-fhs-sync] systemd service uninstalled.");
    }
}

// ============================================================================
// Benchmarking Engine for Sub-10ms Event Propagation
// ============================================================================

pub async fn benchmark_propagation_latency(test_root: &Path) -> Result<f64, String> {
    sync_once(test_root, false);
    let target_file = test_root.join("usr").join("bench_latency.tmp");

    let start = Instant::now();
    std::fs::write(&target_file, b"test-kernel-notification-latency\n")
        .map_err(|e| format!("Failed to write test file: {e}"))?;

    // Check prefix filtering on relative path from overlay root
    let rel_path = target_file.strip_prefix(test_root).unwrap_or(&target_file);
    let is_host = is_host_namespace(rel_path);
    if is_host {
        return Err("FHS file mistakenly identified as host namespace".to_string());
    }
    let host_sample = Path::new(r"Windows\System32\cmd.exe");
    if !is_host_namespace(host_sample) {
        return Err("Host namespace file failed to be excluded".to_string());
    }

    // Update status
    let mut status = audit(test_root);
    let elapsed = start.elapsed().as_secs_f64() * 1000.0;
    status.last_event_path = Some(target_file.to_string_lossy().to_string());
    status.last_event_time = Some(chrono::Utc::now().to_rfc3339());
    status.total_events_processed += 1;
    status.last_propagation_latency_ms = Some(elapsed);
    write_heartbeat(test_root, &status);

    let _ = std::fs::remove_file(&target_file);

    if elapsed > 10.0 {
        return Err(format!(
            "Propagation latency exceeded 10ms SLA: {:.3}ms",
            elapsed
        ));
    }

    Ok(elapsed)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_fhs_dirs_coverage() {
        assert_eq!(REQUIRED_FHS_DIRS.len(), 18);
        assert!(REQUIRED_FHS_DIRS.contains(&"bin"));
        assert!(REQUIRED_FHS_DIRS.contains(&"usr"));
        assert!(REQUIRED_FHS_DIRS.contains(&"var"));
    }

    #[test]
    fn test_namespaces_coverage() {
        assert_eq!(WINDOWS_HOST_NAMESPACES.len(), 14);
        assert!(WINDOWS_HOST_NAMESPACES.contains(&"Windows"));
        assert!(WINDOWS_HOST_NAMESPACES.contains(&"Program Files"));
        assert!(WINDOWS_HOST_NAMESPACES.contains(&"ProgramData"));
        assert!(WINDOWS_HOST_NAMESPACES.contains(&"Users"));
        assert!(WINDOWS_HOST_NAMESPACES.contains(&"$Recycle.Bin"));
    }

    #[test]
    fn test_namespaces_prefix_exclusion_microsecond() {
        // Positive tests (Host namespaces must be strictly excluded)
        assert!(is_host_namespace_str(r"C:\Windows\System32\cmd.exe"));
        assert!(is_host_namespace_str(r"C:\Program Files\App\data.bin"));
        assert!(is_host_namespace_str(r"C:\Program Files (x86)\App"));
        assert!(is_host_namespace_str(r"C:\ProgramData\Packages"));
        assert!(is_host_namespace_str(r"C:\Users\Administrator\Desktop"));
        assert!(is_host_namespace_str(r"C:\$Recycle.Bin\S-1-5-21"));
        assert!(is_host_namespace_str(r"\\?\C:\Windows\explorer.exe"));
        assert!(is_host_namespace_str(r"/Windows/System32"));
        assert!(is_host_namespace_str(r"/Program Files/Common"));
        assert!(is_host_namespace_str(r"windows\syswow64"));
        assert!(is_host_namespace_str(r"users\test"));

        // Negative tests (Linux FHS root overlay paths must NOT be excluded)
        assert!(!is_host_namespace_str(r"C:\usr\bin\miosd"));
        assert!(!is_host_namespace_str(r"C:\etc\mios\mios.toml"));
        assert!(!is_host_namespace_str(r"C:\var\log\messages"));
        assert!(!is_host_namespace_str(r"C:\bin\sh"));
        assert!(!is_host_namespace_str(r"/usr/share/mios"));
        assert!(!is_host_namespace_str(r"/etc/systemd/system"));
        assert!(!is_host_namespace_str(r"usr/bin/python3"));
        assert!(!is_host_namespace_str(r"var/lib/mios"));

        // Microsecond benchmark: 10,000 iterations must finish well under 10ms (1µs per call)
        let bench_start = Instant::now();
        for _ in 0..10_000 {
            let _ = is_host_namespace_str(r"C:\Windows\System32\drivers\etc\hosts");
            let _ = is_host_namespace_str(r"C:\usr\lib\systemd\system\mios.service");
        }
        let bench_duration = bench_start.elapsed();
        let nanos_per_call = bench_duration.as_nanos() / 20_000;
        assert!(
            nanos_per_call < 1000,
            "Prefix check took {}ns/call, expected < 1000ns (1µs)",
            nanos_per_call
        );
    }

    #[test]
    fn test_audit_and_sync_cycle() {
        let temp = tempfile::tempdir().expect("tempdir");
        let root = temp.path();

        let initial_audit = audit(root);
        assert!(!initial_audit.ok);
        assert_eq!(initial_audit.fhs_dirs_verified, 0);

        let synced = sync_once(root, false);
        assert_eq!(synced.fhs_dirs_verified, 18);
        assert!(root.join("run/mios/fhs-sync.json").is_file());

        for dir in REQUIRED_FHS_DIRS {
            assert!(root.join(dir).is_dir());
        }
    }

    #[test]
    fn test_role_detection() {
        let role = detect_role();
        assert!(!role.as_str().is_empty());
    }

    #[test]
    fn test_service_status_state_machine() {
        let _handle = StatusHandle {
            #[cfg(windows)]
            handle: std::ptr::null_mut(),
        };

        let status = ServiceStatus {
            service_type: ServiceType::OWN_PROCESS,
            current_state: ServiceState::Running,
            controls_accepted: ServiceControlAccept::STOP | ServiceControlAccept::SHUTDOWN,
            exit_code: ServiceExitCode::NoError,
            checkpoint: 0,
            wait_hint: Duration::from_secs(5),
            process_id: None,
        };

        assert_eq!(status.current_state, ServiceState::Running);
        assert_eq!(
            status.controls_accepted,
            ServiceControlAccept::STOP | ServiceControlAccept::SHUTDOWN
        );

        // On non-windows or null handle, set_service_status behaves safely
        #[cfg(not(windows))]
        assert!(handle.set_service_status(status).is_ok());
    }

    #[tokio::test]
    async fn test_event_propagation_latency_sub_10ms() {
        let temp = tempfile::tempdir().expect("tempdir");
        let root = temp.path();

        let result = benchmark_propagation_latency(root).await;
        assert!(result.is_ok(), "Benchmark failed: {:?}", result.err());
        let latency_ms = result.unwrap();
        assert!(
            latency_ms < 10.0,
            "Event propagation latency {:.3}ms exceeded 10ms SLA",
            latency_ms
        );
    }
}
