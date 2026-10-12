// AI-hint: Shared daemon, relay, and service helpers for MiOS native binaries.
// AI-related: tools/native/Cargo.toml, usr/share/mios/mios.toml, tools/native/mios-launch, tools/native/mios-agent-relay, usr/libexec/mios/mios-mcp-server, tools/native/mios-resolver

pub mod dashboard;
pub mod host_tmux {
    use std::fs::{self, OpenOptions};
    use std::io::Write;
    use std::path::{Path, PathBuf};

    const HEADER: &str = "# Generated from layered MiOS SSOT by native Rust.";

    fn owned(text: &str) -> bool {
        text.starts_with(HEADER)
            || text.starts_with("# AI-hint: MiOS Windows Native Tmux Configuration")
    }

    fn publish(path: &Path, bytes: &[u8]) -> Result<(), String> {
        let parent = path.parent().ok_or("tmux projection has no parent")?;
        fs::create_dir_all(parent).map_err(|e| format!("{}: {e}", parent.display()))?;
        let pending = parent.join(format!(".mios-tmux-{}.tmp", std::process::id()));
        let result = (|| {
            let mut file = OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&pending)
                .map_err(|e| format!("{}: {e}", pending.display()))?;
            file.write_all(bytes)
                .and_then(|_| file.sync_all())
                .map_err(|e| e.to_string())?;
            fs::rename(&pending, path).map_err(|e| format!("{}: {e}", path.display()))
        })();
        if result.is_err() {
            let _ = fs::remove_file(&pending);
        }
        result
    }

    /// Render before publication. Replace legacy copies only when absent or marked
    /// as MiOS-owned; save their exact old bytes before replacing them.
    /// Returns the paths of pre-existing owned files, used to scope live reloads.
    pub fn stage(
        config: &serde_json::Value,
        canonical: &Path,
        legacy: &[PathBuf],
        font_verified: bool,
    ) -> Result<Vec<PathBuf>, String> {
        let rendered = crate::launcher::host_tmux_config_with_font(config, font_verified)?;
        let mut previous = Vec::new();
        for path in std::iter::once(canonical).chain(legacy.iter().map(PathBuf::as_path)) {
            match fs::symlink_metadata(path) {
                Ok(meta) if !meta.is_file() || meta.file_type().is_symlink() => {
                    return Err(format!(
                        "{}: refusing non-regular tmux projection",
                        path.display()
                    ))
                }
                Ok(_) => {
                    let bytes = fs::read(path).map_err(|e| format!("{}: {e}", path.display()))?;
                    let managed = std::str::from_utf8(&bytes).map(owned).unwrap_or(false);
                    if path == canonical && !managed {
                        return Err(format!(
                            "{}: canonical tmux projection is not MiOS-owned",
                            path.display()
                        ));
                    }
                    previous.push((path.to_path_buf(), Some(bytes), managed));
                }
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                    previous.push((path.to_path_buf(), None, false))
                }
                Err(e) => return Err(format!("{}: {e}", path.display())),
            }
        }
        let mut reload = Vec::new();
        for (path, bytes, managed) in previous {
            if bytes.is_some() && !managed {
                continue;
            }
            if managed {
                reload.push(path.clone());
            }
            if bytes.as_deref() == Some(rendered.as_bytes()) {
                continue;
            }
            if let Some(bytes) = bytes {
                let stamp = std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .map_err(|e| e.to_string())?
                    .as_nanos();
                let backup = path.with_file_name(format!(
                    "{}.pre-native-{stamp}",
                    path.file_name()
                        .ok_or("tmux filename missing")?
                        .to_string_lossy()
                ));
                let mut file = OpenOptions::new()
                    .write(true)
                    .create_new(true)
                    .open(&backup)
                    .map_err(|e| e.to_string())?;
                file.write_all(&bytes)
                    .and_then(|_| file.sync_all())
                    .map_err(|e| e.to_string())?;
            }
            publish(&path, rendered.as_bytes())?;
        }
        Ok(reload)
    }
}
pub mod launcher;
pub mod process {
    use std::fs::{File, OpenOptions};
    use std::path::Path;

    /// Bound direct control clients which own their output pipes. Kill only the
    /// client we started on timeout; an existing server is never a timeout target.
    pub fn output_timeout(
        command: &mut std::process::Command,
        timeout: std::time::Duration,
    ) -> Result<std::process::Output, String> {
        use std::io::Read;
        use std::process::Stdio;
        use std::time::{Duration, Instant};
        if timeout.is_zero() {
            return Err("Control client timeout must be positive".into());
        }
        configure_hidden(command);
        command
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        let mut child = command
            .spawn()
            .map_err(|e| format!("Control client start: {e}"))?;
        let stdout = child.stdout.take().ok_or("Control stdout missing")?;
        let stderr = child.stderr.take().ok_or("Control stderr missing")?;
        let reader = |stream: Box<dyn Read + Send>| {
            std::thread::spawn(move || {
                let mut data = Vec::new();
                stream
                    .take(1024 * 1024 + 1)
                    .read_to_end(&mut data)
                    .map(|_| data)
            })
        };
        let out = reader(Box::new(stdout));
        let err = reader(Box::new(stderr));
        let start = Instant::now();
        let status = loop {
            match child.try_wait() {
                Ok(Some(status)) => break Ok(status),
                Ok(None) if start.elapsed() < timeout => {
                    std::thread::sleep(Duration::from_millis(10))
                }
                Ok(None) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    break Err(format!(
                        "Control client timed out after {}ms",
                        timeout.as_millis()
                    ));
                }
                Err(e) => {
                    let _ = child.kill();
                    let _ = child.wait();
                    break Err(format!("Control client wait: {e}"));
                }
            }
        };
        let stdout = out
            .join()
            .map_err(|_| "Control stdout reader panicked")?
            .map_err(|e| e.to_string())?;
        let stderr = err
            .join()
            .map_err(|_| "Control stderr reader panicked")?
            .map_err(|e| e.to_string())?;
        let status = status?;
        if stdout.len() > 1024 * 1024 || stderr.len() > 1024 * 1024 {
            return Err("Control client output exceeds 1 MiB per stream".into());
        }
        Ok(std::process::Output {
            status,
            stdout,
            stderr,
        })
    }

    #[cfg(all(test, unix))]
    mod timeout_tests {
        use super::*;
        #[test]
        fn bounded_clients_preserve_status_diagnostics_and_terminate_only_their_own_process(
        ) -> Result<(), String> {
            let output = output_timeout(
                std::process::Command::new("sh")
                    .args(["-c", "printf result; printf diagnostic >&2; exit 7"]),
                std::time::Duration::from_secs(1),
            )?;
            assert_eq!(output.status.code(), Some(7));
            assert_eq!(output.stdout, b"result");
            assert_eq!(output.stderr, b"diagnostic");
            let started = std::time::Instant::now();
            let failure = output_timeout(
                std::process::Command::new("sleep").arg("5"),
                std::time::Duration::from_millis(30),
            );
            assert!(failure.is_err_and(|e| e.contains("timed out")));
            assert!(started.elapsed() < std::time::Duration::from_secs(1));
            Ok(())
        }
    }

    /// Win32 CREATE_NO_WINDOW creation flag (0x0800_0000).
    pub const CREATE_NO_WINDOW: u32 = 0x0800_0000;

    #[derive(Debug, thiserror::Error)]
    pub enum ProcessError {
        #[error("Missing socket parent directory")]
        MissingParent,

        #[error("Unsafe workspace lock: {0}")]
        UnsafeLock(String),

        #[error("Workspace lock belongs to another user")]
        PermissionDenied,

        #[error("Lock acquisition error: {0}")]
        LockFailed(String),

        #[error("IO error: {0}")]
        Io(#[from] std::io::Error),
    }

    /// Applies the hidden window creation flag (CREATE_NO_WINDOW) to a Command on Windows.
    #[cfg(windows)]
    pub fn configure_hidden(command: &mut std::process::Command) -> &mut std::process::Command {
        use std::os::windows::process::CommandExt;
        command.creation_flags(CREATE_NO_WINDOW)
    }

    /// Non-Windows no-op pass-through for configure_hidden.
    #[cfg(not(windows))]
    pub fn configure_hidden(command: &mut std::process::Command) -> &mut std::process::Command {
        command
    }

    /// Checks whether a given Win32 creation flag contains CREATE_NO_WINDOW.
    pub fn is_hidden_flag(flag: u32) -> bool {
        (flag & CREATE_NO_WINDOW) != 0
    }

    /// Acquires an exclusive file lock in the socket parent directory with security checks.
    pub fn workspace_lock(path: &Path, name: &str) -> Result<File, ProcessError> {
        let parent = path.parent().ok_or(ProcessError::MissingParent)?;
        let lock_path = parent.join(name);
        if lock_path.is_symlink() {
            return Err(ProcessError::UnsafeLock("symlink lock path".into()));
        }
        let lock = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(&lock_path)?;
        if !crate::socket::owned_path(&lock_path) {
            return Err(ProcessError::PermissionDenied);
        }
        lock.lock()
            .map_err(|e| ProcessError::LockFailed(e.to_string()))?;
        Ok(lock)
    }
}
pub mod socket {
    use std::fs;
    use std::path::{Path, PathBuf};

    /// Linux sockaddr_un limit is 108 bytes including trailing null byte.
    pub const MAX_SOCKET_PATH_LEN: usize = 107;

    #[derive(Debug, thiserror::Error)]
    pub enum SocketError {
        #[error("Socket path length {len} exceeds sockaddr_un limit of {max}: {path}")]
        PathTooLong {
            path: String,
            len: usize,
            max: usize,
        },

        #[error("Unsafe socket path: {0}")]
        UnsafePath(String),

        #[error("Socket path does not exist: {0}")]
        NotFound(String),

        #[error("Path is not a valid socket: {0}")]
        NotASocket(String),

        #[error("No active socket found among candidates")]
        NoActiveSocket,

        #[error("Socket parent directory is missing")]
        MissingParent,

        #[error("IO error: {0}")]
        Io(#[from] std::io::Error),
    }

    /// Enforces the 108-byte sockaddr_un path length limit on UNIX domain socket paths.
    pub fn check_socket_path_length(path: &Path) -> Result<(), SocketError> {
        let len = path.as_os_str().len();
        if len >= 108 {
            return Err(SocketError::PathTooLong {
                path: path.to_string_lossy().to_string(),
                len,
                max: MAX_SOCKET_PATH_LEN,
            });
        }
        Ok(())
    }

    /// Verifies that a path is not a symlink and is owned by the current caller process.
    pub fn owned_path(path: &Path) -> bool {
        let Ok(meta) = fs::symlink_metadata(path) else {
            return false;
        };
        if meta.file_type().is_symlink() {
            return false;
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::MetadataExt;
            let Ok(caller) = fs::metadata("/proc/self") else {
                return false;
            };
            if meta.uid() != caller.uid() {
                return false;
            }
        }
        true
    }

    /// Verifies that a socket path exists and is owned by the current user.
    pub fn verify_socket_owner(path: &Path) -> Result<bool, SocketError> {
        if !path.exists() {
            return Err(SocketError::NotFound(path.to_string_lossy().to_string()));
        }
        Ok(owned_path(path))
    }

    /// Discovers candidate tmux sockets within a root directory up to a bounded recursion depth.
    pub fn socket_candidates(root: &Path, human: &str, depth: usize) -> Vec<PathBuf> {
        let mut sockets = Vec::new();
        if !owned_path(root) || !root.is_dir() {
            return sockets;
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::FileTypeExt;
            for leaf in [human, "mcp-headless"] {
                let socket = root.join(leaf);
                if owned_path(&socket)
                    && fs::symlink_metadata(&socket).is_ok_and(|m| m.file_type().is_socket())
                {
                    sockets.push(socket);
                }
            }
        }
        #[cfg(not(unix))]
        {
            for leaf in [human, "mcp-headless"] {
                let socket = root.join(leaf);
                if owned_path(&socket) && socket.exists() {
                    sockets.push(socket);
                }
            }
        }
        let Ok(entries) = fs::read_dir(root) else {
            return sockets;
        };
        for entry in entries.flatten().take(128) {
            let path = entry.path();
            if !owned_path(&path) || !path.is_dir() {
                continue;
            }
            let name = entry.file_name().to_string_lossy().into_owned();
            if name.starts_with("tmux-") {
                for leaf in [human, "mcp-headless"] {
                    let socket = path.join(leaf);
                    #[cfg(unix)]
                    {
                        use std::os::unix::fs::FileTypeExt;
                        if owned_path(&socket)
                            && fs::symlink_metadata(&socket)
                                .is_ok_and(|m| m.file_type().is_socket())
                        {
                            sockets.push(socket);
                        }
                    }
                    #[cfg(not(unix))]
                    {
                        if owned_path(&socket) && socket.exists() {
                            sockets.push(socket);
                        }
                    }
                }
            } else if depth < 2 && (name.starts_with("uid-") || name.starts_with("mios-tmux-")) {
                sockets.extend(socket_candidates(&path, human, depth + 1));
            }
            if sockets.len() >= 16 {
                break;
            }
        }
        sockets.truncate(16);
        sockets
    }

    /// Discovers the first active, existing socket among candidates, enforcing path bounds.
    pub fn find_active_socket<P: AsRef<Path>>(
        candidates: &[P],
    ) -> Result<Option<PathBuf>, SocketError> {
        for c in candidates {
            let p = c.as_ref();
            // Path bounds check failure propagates.
            check_socket_path_length(p)?;
            if p.exists() {
                return Ok(Some(p.to_path_buf()));
            }
        }
        Ok(None)
    }

    /// Discovers the first active socket or returns `SocketError::NoActiveSocket`.
    pub fn find_active_socket_required<P: AsRef<Path>>(
        candidates: &[P],
    ) -> Result<PathBuf, SocketError> {
        find_active_socket(candidates)?.ok_or(SocketError::NoActiveSocket)
    }

    /// Validates workspace socket path for security, length, symlinks, and ownership.
    pub fn validate_workspace_socket(path: &Path, human_socket: &str) -> Result<(), SocketError> {
        let file_name = path.file_name().and_then(|s| s.to_str()).unwrap_or("");
        let is_tmux_env = std::env::var("TMUX")
            .ok()
            .and_then(|t| t.split(',').next().map(|s| s.to_string()))
            .as_deref()
            == path.to_str();
        let is_valid_name = file_name == human_socket
            || file_name == "default"
            || file_name.starts_with("tmux-")
            || file_name.starts_with("mios-")
            || is_tmux_env;

        let is_canonical = if cfg!(windows) {
            if let Ok(_c) = path.canonicalize() {
                !path.is_symlink() && path.parent().map(|p| !p.is_symlink()).unwrap_or(true)
            } else {
                false
            }
        } else {
            path.canonicalize().ok().as_deref() == Some(path)
        };

        if !path.is_absolute()
            || !is_canonical
            || path.as_os_str().len() >= 104
            || !owned_path(path)
            || !path.parent().map(owned_path).unwrap_or(false)
            || !is_valid_name
        {
            return Err(SocketError::UnsafePath(
                "unsafe native workspace socket".into(),
            ));
        }

        #[cfg(unix)]
        {
            use std::os::unix::fs::{FileTypeExt, PermissionsExt};
            if !fs::symlink_metadata(path)?.file_type().is_socket() {
                return Err(SocketError::NotASocket(
                    "workspace path is not a socket".into(),
                ));
            }
            if fs::symlink_metadata(path.parent().ok_or(SocketError::MissingParent)?)?
                .permissions()
                .mode()
                & 0o077
                != 0
            {
                return Err(SocketError::UnsafePath(
                    "workspace socket parent is not private".into(),
                ));
            }
        }
        Ok(())
    }
}
pub mod ssot {
    use std::path::{Path, PathBuf};

    pub const FORBIDDEN_CLOUD_URLS: &[&str] = &[
        "api.openai.com",
        "generativelanguage.googleapis.com",
        "api.anthropic.com",
    ];

    #[derive(Debug, thiserror::Error)]
    pub enum ConfigError {
        #[error("SSOT file not found: {0}")]
        NotFound(String),

        #[error("Failed to parse SSOT TOML: {0}")]
        ParseError(String),

        #[error("Missing required SSOT key: {0}")]
        MissingKey(String),

        #[error("Invalid port value for key '{key}': {value}")]
        InvalidPort { key: String, value: String },

        #[error("Forbidden vendor cloud endpoint detected in violation of Law 5: {0}")]
        ForbiddenCloudEndpoint(String),

        #[error("IO error: {0}")]
        Io(#[from] std::io::Error),
    }

    /// Asserts that a URL does not reference prohibited vendor cloud APIs (Law 5).
    pub fn validate_no_cloud_endpoints(url: &str) -> Result<(), ConfigError> {
        for forbidden in FORBIDDEN_CLOUD_URLS {
            if url.contains(forbidden) {
                return Err(ConfigError::ForbiddenCloudEndpoint(forbidden.to_string()));
            }
        }
        Ok(())
    }

    /// Discovers the path to `usr/share/mios/mios.toml` using environment variables and search heuristics.
    pub fn find_ssot_path() -> Result<PathBuf, ConfigError> {
        if let Ok(p) = std::env::var("MIOS_SSOT_PATH") {
            let path = PathBuf::from(p);
            if path.is_file() {
                return Ok(path);
            }
        }
        if let Ok(root) = std::env::var("MIOS_ROOT") {
            let path = PathBuf::from(root).join("usr/share/mios/mios.toml");
            if path.is_file() {
                return Ok(path);
            }
        }
        let fhs = Path::new("/usr/share/mios/mios.toml");
        if fhs.is_file() {
            return Ok(fhs.to_path_buf());
        }
        if let Ok(cwd) = std::env::current_dir() {
            let mut cur = cwd.as_path();
            loop {
                let candidate = cur.join("usr/share/mios/mios.toml");
                if candidate.is_file() {
                    return Ok(candidate);
                }
                match cur.parent() {
                    Some(p) => cur = p,
                    None => break,
                }
            }
        }
        if let Ok(exe) = std::env::current_exe() {
            let mut cur = exe.as_path();
            while let Some(parent) = cur.parent() {
                let candidate = parent.join("usr/share/mios/mios.toml");
                if candidate.is_file() {
                    return Ok(candidate);
                }
                cur = parent;
            }
        }
        let win_fallback = Path::new(r"C:\MiOS\usr\share\mios\mios.toml");
        if win_fallback.is_file() {
            return Ok(win_fallback.to_path_buf());
        }
        Err(ConfigError::NotFound(
            "usr/share/mios/mios.toml not found".into(),
        ))
    }

    /// Loads and parses the raw SSOT TOML table.
    pub fn load_ssot_table() -> Result<toml::Table, ConfigError> {
        let path = find_ssot_path()?;
        let content = std::fs::read_to_string(&path)
            .map_err(|e| ConfigError::NotFound(format!("{}: {}", path.display(), e)))?;
        content
            .parse::<toml::Table>()
            .map_err(|e| ConfigError::ParseError(e.to_string()))
    }

    /// Resolves a typed port (1..=65535) dynamically from environment or `[ports]` SSOT table.
    pub fn require_port(key: &str) -> Result<u16, ConfigError> {
        // 1. Environment variable override (e.g. MIOS_PORTS_NODE or MIOS_PORTS_HEADSCALE)
        let lookup_key = key
            .strip_prefix("MIOS_PORTS_")
            .or_else(|| key.strip_prefix("MIOS_PORT_"))
            .unwrap_or(key)
            .trim_start_matches("ports.")
            .replace(['.', '-'], "_");
        let suffix = lookup_key.to_uppercase();
        let input = std::env::var(format!("MIOS_PORTS_{suffix}"))
            .ok()
            .filter(|v| !v.trim().is_empty())
            .or_else(|| std::env::var(format!("MIOS_PORT_{suffix}")).ok());
        if let Some(val) = input {
            if !val.trim().is_empty() {
                let parsed: i64 = val.trim().parse().map_err(|_| ConfigError::InvalidPort {
                    key: key.to_string(),
                    value: val.clone(),
                })?;
                if (1..=65535).contains(&parsed) {
                    return Ok(parsed as u16);
                } else {
                    return Err(ConfigError::InvalidPort {
                        key: key.to_string(),
                        value: val,
                    });
                }
            }
        }

        // 2. Read from mios.toml [ports]
        let table = load_ssot_table()?;
        let ports = table
            .get("ports")
            .and_then(|v| v.as_table())
            .ok_or_else(|| ConfigError::MissingKey("ports".into()))?;

        let lookup_key = lookup_key.to_lowercase();

        let val = ports.get(&lookup_key).or_else(|| ports.get(key));

        let raw_port = match val {
            Some(toml::Value::Integer(i)) => *i,
            Some(toml::Value::String(s)) => {
                s.parse::<i64>().map_err(|_| ConfigError::InvalidPort {
                    key: key.to_string(),
                    value: s.clone(),
                })?
            }
            Some(other) => {
                return Err(ConfigError::InvalidPort {
                    key: key.to_string(),
                    value: other.to_string(),
                });
            }
            None => {
                return Err(ConfigError::MissingKey(key.to_string()));
            }
        };

        let stack_id = ports
            .get("stack_id")
            .and_then(|v| v.as_integer())
            .unwrap_or(0);
        let effective_port = raw_port + (stack_id * 10000);

        if !(1..=65535).contains(&effective_port) {
            return Err(ConfigError::InvalidPort {
                key: key.to_string(),
                value: effective_port.to_string(),
            });
        }

        Ok(effective_port as u16)
    }

    /// Resolves a string configuration value from environment or dotted TOML path.
    pub fn require_str(key: &str) -> Result<String, ConfigError> {
        let env_var = format!("MIOS_{}", key.to_uppercase().replace(['.', '-'], "_"));
        if let Ok(v) = std::env::var(&env_var) {
            if !v.is_empty() {
                return Ok(v);
            }
        }
        if let Ok(v) = std::env::var(key) {
            if !v.is_empty() {
                return Ok(v);
            }
        }

        let table = load_ssot_table()?;
        let parts: Vec<&str> = key.split('.').collect();
        let mut current: &toml::Value = &toml::Value::Table(table);

        for part in parts {
            match current {
                toml::Value::Table(t) => {
                    current = t
                        .get(part)
                        .ok_or_else(|| ConfigError::MissingKey(key.to_string()))?;
                }
                _ => return Err(ConfigError::MissingKey(key.to_string())),
            }
        }

        match current {
            toml::Value::String(s) => Ok(s.clone()),
            other => Ok(other.to_string()),
        }
    }

    /// Resolves the unified local AI endpoint, strictly enforcing Law 5.
    pub fn resolve_ai_endpoint() -> Result<String, ConfigError> {
        if let Ok(ep) = std::env::var("MIOS_AI_ENDPOINT") {
            if !ep.trim().is_empty() {
                validate_no_cloud_endpoints(&ep)?;
                return Ok(ep.trim().to_string());
            }
        }
        let port = require_port("llm_light").unwrap_or(8500);
        let endpoint = format!("http://127.0.0.1:{port}/v1");
        validate_no_cloud_endpoints(&endpoint)?;
        Ok(endpoint)
    }

    /// Resolves a general endpoint, ensuring zero cloud URLs.
    pub fn resolve_endpoint(key: &str) -> Result<String, ConfigError> {
        if key == "ai" || key == "MIOS_AI_ENDPOINT" {
            return resolve_ai_endpoint();
        }
        let endpoint = require_str(key)?;
        validate_no_cloud_endpoints(&endpoint)?;
        Ok(endpoint)
    }
}
pub mod tmux_theme;

pub use process::{
    configure_hidden, is_hidden_flag, workspace_lock, ProcessError, CREATE_NO_WINDOW,
};
pub use socket::{
    check_socket_path_length, find_active_socket, find_active_socket_required, owned_path,
    socket_candidates, validate_workspace_socket, verify_socket_owner, SocketError,
    MAX_SOCKET_PATH_LEN,
};
pub use ssot::{
    find_ssot_path, require_port, require_str, resolve_ai_endpoint, resolve_endpoint, ConfigError,
};
