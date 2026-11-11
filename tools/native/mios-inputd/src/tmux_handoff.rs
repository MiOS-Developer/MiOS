// AI-hint: Same-host authenticated SSH tmux handoff validation and bounded private socket framing; ownership stays in Inputd.
// AI-related: /usr/share/mios/templates/rust, runtime::OwnershipWorker, tmux_capture::Target, /run/systemd/sessions
// AI-functions: authenticate, live, request, read_request, write_reply, socket_path, peer_credentials
// Scaffolded by installed native miosd; canonical template SHA256 2a9bf43dc4c1e0af3fd0b26f3b42217740a5dea7c86a7e619254f8cb62683203.
use crate::config::GlobalConfig;
use crate::runtime::TmuxClient;
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{FileTypeExt, MetadataExt, OpenOptionsExt};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

/// Protocol allocation limits, not assignment policy. Exactly one big-endian length-prefixed JSON frame per request/reply.
const MAX_FRAME: usize = 8192;
const MAX_FACTS: u64 = 65536;

#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HandoffRequest {
    pub version: u32,
    pub op: String,
    #[serde(default)]
    pub socket: Option<PathBuf>,
    #[serde(default)]
    pub client: Option<String>,
    #[serde(default)]
    pub pane: Option<String>,
    #[serde(default)]
    pub generation: Option<u64>,
    #[serde(default)]
    pub mode: Option<String>,
    #[serde(default)]
    pub frontend_pid: Option<u32>,
    #[serde(default)]
    pub frontend_start: Option<u64>,
}
impl Default for HandoffRequest {
    fn default() -> Self {
        Self {
            version: 1,
            op: String::new(),
            socket: None,
            client: None,
            pane: None,
            generation: None,
            mode: None,
            frontend_pid: None,
            frontend_start: None,
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct LoginWitness {
    pub uid: u32,
    pub session_id: String,
    pub leader: u32,
    pub leader_start: u64,
    pub client: TmuxClient,
    pub client_start: u64,
}
#[derive(Clone, Debug, Serialize)]
pub struct LoginLease {
    pub generation: u64,
    pub witness: LoginWitness,
    pub target: crate::tmux_capture::Target,
}
fn uid() -> u32 {
    // SAFETY: getuid only reads the caller's kernel identity.
    unsafe { libc::getuid() }
}
fn identifier(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 64
        && id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}
fn plain(value: &str) -> bool {
    !value.is_empty() && value.len() <= 256 && !value.chars().any(char::is_control)
}
impl HandoffRequest {
    pub fn validate(&self) -> Result<(), String> {
        if self.version != 1 || !matches!(self.op.as_str(), "attach" | "detach" | "window") {
            return Err("unsupported handoff version or operation".into());
        }
        if self
            .socket
            .as_ref()
            .is_some_and(|s| !s.is_absolute() || s.as_os_str().as_bytes().contains(&0))
            || self.client.as_deref().is_some_and(|s| !plain(s))
            || self.pane.as_deref().is_some_and(|s| {
                !s.strip_prefix('%')
                    .is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
            })
            || self.generation == Some(0)
        {
            return Err("invalid handoff target identity".into());
        }
        match (self.frontend_pid, self.frontend_start) {
            (None, None) => {}
            (Some(pid), Some(start)) if pid > 0 && start > 0 => {}
            _ => return Err("frontend PID and start must be a complete live identity".into()),
        }
        if self.op == "attach" && (self.socket.is_none() || self.client.is_none()) {
            return Err("attach requires an explicit outer tmux socket and client".into());
        }
        if self.op == "window" {
            if !matches!(self.mode.as_deref(), Some("on" | "fullscreen" | "off")) {
                return Err("window requires mode on, fullscreen or off".into());
            }
        } else if self.mode.is_some() || self.frontend_pid.is_some() {
            return Err("only window accepts capture mode and frontend identity".into());
        }
        Ok(())
    }
}

/// Return a path only; the existing daemon owns private listener binding, lifetime and generations.
pub fn socket_path() -> Result<PathBuf, String> {
    let root = std::env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(format!("/run/user/{}", uid())));
    if !root.is_absolute() {
        return Err("handoff runtime directory must be absolute".into());
    }
    let meta = fs::symlink_metadata(&root).map_err(|e| format!("handoff runtime: {e}"))?;
    if !meta.is_dir()
        || meta.file_type().is_symlink()
        || meta.uid() != uid()
        || meta.mode() & 0o077 != 0
    {
        return Err("handoff runtime must be a caller-owned private directory".into());
    }
    Ok(root.join("mios-inputd-handoff.sock"))
}
pub fn peer_credentials(stream: &UnixStream) -> Result<(u32, u32), String> {
    let mut cred = libc::ucred {
        pid: 0,
        uid: 0,
        gid: 0,
    };
    let mut len = std::mem::size_of::<libc::ucred>() as libc::socklen_t;
    // SAFETY: getsockopt writes at most len bytes into a live correctly-sized ucred.
    let rc = unsafe {
        libc::getsockopt(
            stream.as_raw_fd(),
            libc::SOL_SOCKET,
            libc::SO_PEERCRED,
            (&mut cred as *mut libc::ucred).cast(),
            &mut len,
        )
    };
    if rc != 0 || len as usize != std::mem::size_of::<libc::ucred>() || cred.pid <= 0 {
        return Err("handoff peer credentials are unavailable".into());
    }
    if cred.uid != uid() {
        return Err("handoff peer belongs to another user".into());
    }
    Ok((cred.uid, cred.pid as u32))
}
fn deadline(timeout: Duration) -> Result<Instant, String> {
    if timeout.is_zero() {
        return Err("handoff deadline expired".into());
    }
    Instant::now()
        .checked_add(timeout)
        .ok_or("invalid handoff timeout".into())
}
fn remaining(until: Instant) -> Result<Duration, String> {
    let value = until.saturating_duration_since(Instant::now());
    if value.is_zero() {
        Err("handoff deadline expired".into())
    } else {
        Ok(value)
    }
}
fn read_exact(stream: &mut UnixStream, mut bytes: &mut [u8], until: Instant) -> Result<(), String> {
    while !bytes.is_empty() {
        stream
            .set_read_timeout(Some(remaining(until)?))
            .map_err(|e| e.to_string())?;
        match stream.read(bytes) {
            Ok(0) => return Err("handoff peer closed an incomplete frame".into()),
            Ok(n) => bytes = &mut bytes[n..],
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
            Err(e) => return Err(format!("handoff read: {e}")),
        }
    }
    Ok(())
}
fn frame(stream: &mut UnixStream, until: Instant) -> Result<Vec<u8>, String> {
    let mut header = [0u8; 4];
    read_exact(stream, &mut header, until)?;
    let len = u32::from_be_bytes(header) as usize;
    if len == 0 || len > MAX_FRAME {
        return Err("handoff frame exceeds protocol bounds".into());
    }
    let mut bytes = vec![0; len];
    read_exact(stream, &mut bytes, until)?;
    Ok(bytes)
}
fn write_frame(stream: &mut UnixStream, bytes: &[u8], until: Instant) -> Result<(), String> {
    if bytes.is_empty() || bytes.len() > MAX_FRAME {
        return Err("handoff frame exceeds protocol bounds".into());
    }
    let mut message = Vec::with_capacity(bytes.len() + 4);
    message.extend((bytes.len() as u32).to_be_bytes());
    message.extend(bytes);
    let mut rest = message.as_slice();
    while !rest.is_empty() {
        stream
            .set_write_timeout(Some(remaining(until)?))
            .map_err(|e| e.to_string())?;
        match stream.write(rest) {
            Ok(0) => return Err("handoff reply could not advance".into()),
            Ok(n) => rest = &rest[n..],
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
            Err(e) => return Err(format!("handoff write: {e}")),
        }
    }
    Ok(())
}
pub fn read_request(stream: &mut UnixStream, timeout: Duration) -> Result<HandoffRequest, String> {
    peer_credentials(stream)?;
    let req: HandoffRequest = serde_json::from_slice(&frame(stream, deadline(timeout)?)?)
        .map_err(|e| format!("invalid handoff request: {e}"))?;
    req.validate()?;
    Ok(req)
}
pub fn write_reply(stream: &mut UnixStream, value: &serde_json::Value) -> Result<(), String> {
    peer_credentials(stream)?;
    let bytes = serde_json::to_vec(value).map_err(|e| e.to_string())?;
    write_frame(stream, &bytes, deadline(Duration::from_secs(1))?)
}
/// Probe only a prevalidated caller-owned socket. A full listener backlog is live, never a stale-unlink signal.
pub(crate) fn endpoint_live(path: &Path) -> Result<bool, String> {
    let bytes = path.as_os_str().as_bytes();
    let mut address: libc::sockaddr_un = unsafe { std::mem::zeroed() };
    if bytes.is_empty() || bytes.len() >= address.sun_path.len() || bytes.contains(&0) {
        return Err("invalid handoff socket path".into());
    }
    address.sun_family = libc::AF_UNIX as libc::sa_family_t;
    for (dst, src) in address.sun_path.iter_mut().zip(bytes) {
        *dst = *src as libc::c_char;
    }
    let fd = unsafe {
        libc::socket(
            libc::AF_UNIX,
            libc::SOCK_STREAM | libc::SOCK_NONBLOCK | libc::SOCK_CLOEXEC,
            0,
        )
    };
    if fd < 0 {
        return Err(std::io::Error::last_os_error().to_string());
    }
    let fd = unsafe { OwnedFd::from_raw_fd(fd) };
    let length =
        (std::mem::offset_of!(libc::sockaddr_un, sun_path) + bytes.len() + 1) as libc::socklen_t;
    if unsafe {
        libc::connect(
            fd.as_raw_fd(),
            (&address as *const libc::sockaddr_un).cast(),
            length,
        )
    } == 0
    {
        return Ok(true);
    }
    match std::io::Error::last_os_error().raw_os_error() {
        Some(libc::ECONNREFUSED | libc::ENOENT) => Ok(false),
        Some(libc::EAGAIN | libc::EINPROGRESS) => Ok(true),
        _ => Err("handoff endpoint could not be safely probed".into()),
    }
}
pub(crate) fn connect(path: &Path, until: Instant) -> Result<UnixStream, String> {
    let bytes = path.as_os_str().as_bytes();
    // SAFETY: zero initializes the POD sockaddr_un; only the initialized length is passed to connect.
    let mut address: libc::sockaddr_un = unsafe { std::mem::zeroed() };
    if bytes.is_empty() || bytes.len() >= address.sun_path.len() || bytes.contains(&0) {
        return Err("invalid handoff socket path".into());
    }
    address.sun_family = libc::AF_UNIX as libc::sa_family_t;
    for (dst, src) in address.sun_path.iter_mut().zip(bytes) {
        *dst = *src as libc::c_char;
    }
    // SAFETY: socket has no borrowed Rust memory; success returns a fresh owned descriptor.
    let fd = unsafe {
        libc::socket(
            libc::AF_UNIX,
            libc::SOCK_STREAM | libc::SOCK_NONBLOCK | libc::SOCK_CLOEXEC,
            0,
        )
    };
    if fd < 0 {
        return Err(std::io::Error::last_os_error().to_string());
    }
    // SAFETY: fd is fresh and is owned exactly once from here.
    let fd = unsafe { OwnedFd::from_raw_fd(fd) };
    let len =
        (std::mem::offset_of!(libc::sockaddr_un, sun_path) + bytes.len() + 1) as libc::socklen_t;
    // SAFETY: address points to initialized AF_UNIX data covering len bytes.
    let rc = unsafe {
        libc::connect(
            fd.as_raw_fd(),
            (&address as *const libc::sockaddr_un).cast(),
            len,
        )
    };
    if rc != 0 {
        let error = std::io::Error::last_os_error();
        // A full Unix listener backlog returns EAGAIN, so fail promptly rather than blocking indefinitely.
        if error.raw_os_error() != Some(libc::EINPROGRESS) {
            return Err(format!("handoff connect: {error}"));
        }
        let mut wait = libc::pollfd {
            fd: fd.as_raw_fd(),
            events: libc::POLLOUT,
            revents: 0,
        };
        loop {
            let millis = remaining(until)?.as_millis().clamp(1, i32::MAX as u128) as i32;
            // SAFETY: poll writes revents in the one valid pollfd.
            let result = unsafe { libc::poll(&mut wait, 1, millis) };
            if result < 0
                && std::io::Error::last_os_error().kind() == std::io::ErrorKind::Interrupted
            {
                continue;
            }
            if result <= 0 {
                return Err("handoff connect failed or timed out".into());
            }
            let mut error = 0i32;
            let mut len = std::mem::size_of::<i32>() as libc::socklen_t;
            // SAFETY: SO_ERROR writes an int into a live int buffer.
            if unsafe {
                libc::getsockopt(
                    fd.as_raw_fd(),
                    libc::SOL_SOCKET,
                    libc::SO_ERROR,
                    (&mut error as *mut i32).cast(),
                    &mut len,
                )
            } != 0
                || error != 0
            {
                return Err("handoff connect was refused".into());
            }
            break;
        }
    }
    let stream = UnixStream::from(fd);
    stream.set_nonblocking(false).map_err(|e| e.to_string())?;
    peer_credentials(&stream)?;
    Ok(stream)
}
/// No assignment state is written by clients; an acknowledged reply comes only from the same UID's Inputd endpoint.
pub fn request(req: &HandoffRequest, timeout: Duration) -> Result<serde_json::Value, String> {
    req.validate()?;
    let until = deadline(timeout)?;
    let path = socket_path()?;
    let before = fs::symlink_metadata(&path).map_err(|e| format!("handoff endpoint: {e}"))?;
    if !before.file_type().is_socket() || before.uid() != uid() || before.mode() & 0o777 != 0o600 {
        return Err("handoff endpoint must be a caller-owned mode 0600 Unix socket".into());
    }
    let mut stream = connect(&path, until)?;
    let after = fs::symlink_metadata(&path).map_err(|e| e.to_string())?;
    if (before.dev(), before.ino()) != (after.dev(), after.ino()) {
        return Err("handoff endpoint changed during connect".into());
    }
    let bytes = serde_json::to_vec(req).map_err(|e| e.to_string())?;
    write_frame(&mut stream, &bytes, until)?;
    serde_json::from_slice(&frame(&mut stream, until)?)
        .map_err(|e| format!("invalid handoff reply: {e}"))
}

// Authentication is deliberately separate from framing: the daemon owns current authority and neutral-state transitions.
#[derive(Clone, Debug)]
struct ProcessFacts {
    uid: u32,
    pid: u32,
    start: u64,
    state: char,
    cgroup: String,
}
#[derive(Clone, Debug)]
struct RecordFacts {
    uid: u32,
    mode: u32,
    regular: bool,
    symlink: bool,
    fields: BTreeMap<String, String>,
}
fn bounded_text(mut file: File) -> Result<String, String> {
    let mut bytes = Vec::new();
    (&mut file)
        .take(MAX_FACTS + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() as u64 > MAX_FACTS {
        return Err("login identity record is too large".into());
    }
    String::from_utf8(bytes).map_err(|_| "login identity record is not UTF-8".into())
}
fn process_facts(pid: u32) -> Result<ProcessFacts, String> {
    if pid == 0 {
        return Err("missing login process".into());
    }
    let root = PathBuf::from(format!("/proc/{pid}"));
    let owner = fs::metadata(&root).map_err(|e| e.to_string())?.uid();
    let read = |name: &str| -> Result<String, String> {
        bounded_text(
            OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
                .open(root.join(name))
                .map_err(|e| e.to_string())?,
        )
    };
    let stat = read("stat")?;
    let fields: Vec<_> = stat[stat.rfind(')').ok_or("malformed login process stat")? + 1..]
        .split_whitespace()
        .collect();
    let start = fields
        .get(19)
        .and_then(|v| v.parse::<u64>().ok())
        .filter(|n| *n > 0)
        .ok_or("login process has no start identity")?;
    let state = fields
        .first()
        .and_then(|s| s.chars().next())
        .ok_or("missing login process state")?;
    let cgroup = read("cgroup")?;
    if crate::nested::start_time(Path::new("/proc"), pid) != Some(start)
        || fs::metadata(&root).map_err(|e| e.to_string())?.uid() != owner
    {
        return Err("login process changed during inspection".into());
    }
    Ok(ProcessFacts {
        uid: owner,
        pid,
        start,
        state,
        cgroup,
    })
}
fn session_scope(cgroup: &str, owner: u32) -> Result<String, String> {
    let mut ids = std::collections::BTreeSet::new();
    for line in cgroup.lines() {
        let path = line.splitn(3, ':').nth(2).ok_or("malformed login cgroup")?;
        let prefix = format!("/user.slice/user-{owner}.slice/");
        let Some(rest) = path.strip_prefix(&prefix) else {
            continue;
        };
        let scope = rest.split('/').next().ok_or("missing login scope")?;
        let Some(id) = scope
            .strip_prefix("session-")
            .and_then(|s| s.strip_suffix(".scope"))
        else {
            continue;
        };
        if !identifier(id) {
            return Err("invalid login session scope".into());
        }
        ids.insert(id.to_owned());
    }
    if ids.len() != 1 {
        return Err("process is not in one authenticated logind session scope".into());
    }
    ids.into_iter().next().ok_or("missing login scope".into())
}
fn trusted_record(id: &str) -> Result<RecordFacts, String> {
    if !identifier(id) {
        return Err("invalid logind session ID".into());
    }
    let mut prefix = PathBuf::from("/");
    for component in ["run", "systemd", "sessions"] {
        prefix.push(component);
        let meta = fs::symlink_metadata(&prefix).map_err(|e| e.to_string())?;
        if !meta.is_dir()
            || meta.file_type().is_symlink()
            || meta.uid() != 0
            || meta.mode() & 0o022 != 0
        {
            return Err("logind runtime directory is not trusted".into());
        }
    }
    let path = prefix.join(id);
    let before = fs::symlink_metadata(&path).map_err(|e| format!("logind login record: {e}"))?;
    if !before.is_file()
        || before.file_type().is_symlink()
        || before.uid() != 0
        || before.mode() & 0o022 != 0
    {
        return Err(
            "logind login record is not a root-owned immutable-to-user regular file".into(),
        );
    }
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(&path)
        .map_err(|e| e.to_string())?;
    let held = file.metadata().map_err(|e| e.to_string())?;
    if (held.dev(), held.ino()) != (before.dev(), before.ino()) {
        return Err("logind record changed while opening".into());
    }
    let text = bounded_text(file)?;
    let after = fs::symlink_metadata(&path).map_err(|e| e.to_string())?;
    if (
        before.dev(),
        before.ino(),
        before.mtime(),
        before.mtime_nsec(),
        before.size(),
    ) != (
        after.dev(),
        after.ino(),
        after.mtime(),
        after.mtime_nsec(),
        after.size(),
    ) {
        return Err("logind record changed during inspection".into());
    }
    let mut fields = BTreeMap::new();
    for line in text
        .lines()
        .filter(|l| !l.is_empty() && !l.starts_with('#'))
    {
        let (key, value) = line
            .split_once('=')
            .ok_or("malformed logind login record")?;
        if fields
            .insert(key.to_owned(), value.trim().to_owned())
            .is_some()
        {
            return Err("duplicate logind identity field".into());
        }
    }
    Ok(RecordFacts {
        uid: held.uid(),
        mode: held.mode(),
        regular: held.is_file(),
        symlink: false,
        fields,
    })
}
// Only administrator projections may add the dedicated selected-account PAM service.
// The user's root, loader pointers, environment and TOML layer never grant login authority.
#[derive(Clone, Debug, PartialEq, Eq)]
struct SelectedPam {
    service: String,
    minimum: u32,
    maximum: u32,
}
#[derive(Clone, Debug, PartialEq, Eq)]
struct PolicyStamp {
    path: PathBuf,
    dev: u64,
    ino: u64,
    size: u64,
    mode: u32,
    uid: u32,
    modified: (i64, i64),
}
fn trusted_policy_path(path: &Path, directory: bool) -> Result<PolicyStamp, String> {
    if !path.is_absolute() {
        return Err("administrator policy path must be absolute".into());
    }
    let parent = if directory {
        path
    } else {
        path.parent().ok_or("policy file has no parent")?
    };
    for p in parent.ancestors() {
        let m =
            fs::symlink_metadata(p).map_err(|_| "administrator policy ancestor is unavailable")?;
        if m.uid() != 0 || (!m.file_type().is_symlink() && (!m.is_dir() || m.mode() & 0o022 != 0)) {
            return Err("administrator policy traverses a writable or foreign ancestor".into());
        }
    }
    let spelling =
        fs::symlink_metadata(path).map_err(|_| "administrator policy spelling is unavailable")?;
    if spelling.uid() != 0 {
        return Err("administrator policy spelling is not root-owned".into());
    }
    let canonical = path
        .canonicalize()
        .map_err(|_| "administrator policy target is unavailable")?;
    let m = fs::metadata(&canonical).map_err(|_| "administrator policy target is unavailable")?;
    if m.uid() != 0
        || m.mode() & 0o022 != 0
        || (directory && !m.is_dir())
        || (!directory && !m.is_file())
    {
        return Err("administrator policy must be root-owned and non-writable".into());
    }
    let parent = if directory {
        canonical.as_path()
    } else {
        canonical.parent().ok_or("policy target has no parent")?
    };
    for p in parent.ancestors() {
        let m = fs::symlink_metadata(p)
            .map_err(|_| "administrator policy target ancestor is unavailable")?;
        if !m.is_dir() || m.uid() != 0 || m.mode() & 0o022 != 0 {
            return Err("administrator policy target ancestor is writable or foreign".into());
        }
    }
    Ok(PolicyStamp {
        path: canonical,
        dev: m.dev(),
        ino: m.ino(),
        size: m.len(),
        mode: m.mode(),
        uid: m.uid(),
        modified: (m.mtime(), m.mtime_nsec()),
    })
}
fn trusted_missing_policy_parent(path: &Path, stamps: &mut Vec<PolicyStamp>) -> Result<(), String> {
    for parent in path.ancestors().skip(1) {
        match fs::symlink_metadata(parent) {
            Ok(_) => {
                stamps.push(trusted_policy_path(parent, true)?);
                return Ok(());
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
            Err(_) => return Err("cannot inspect missing administrator policy parent".into()),
        }
    }
    Err("administrator policy has no trusted parent".into())
}
fn optional_policy_path(
    path: &Path,
    directory: bool,
    stamps: &mut Vec<PolicyStamp>,
) -> Result<(), String> {
    match fs::symlink_metadata(path) {
        Ok(_) => stamps.push(trusted_policy_path(path, directory)?),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
            trusted_missing_policy_parent(path, stamps)?
        }
        Err(_) => return Err("cannot inspect administrator policy".into()),
    }
    Ok(())
}
fn projection_stamp(service: Option<&str>) -> Result<Vec<PolicyStamp>, String> {
    let mut stamps = vec![trusted_policy_path(
        Path::new("/usr/share/mios/mios.toml"),
        false,
    )?];
    optional_policy_path(Path::new("/etc/mios/mios.toml"), false, &mut stamps)?;
    for directory in ["/usr/lib/mios/mios.d", "/etc/mios/mios.d"] {
        let path = Path::new(directory);
        match fs::symlink_metadata(path) {
            Ok(_) => {
                stamps.push(trusted_policy_path(path, true)?);
                let mut files = fs::read_dir(path)
                    .map_err(|_| "cannot inspect administrator fragments")?
                    .map(|p| {
                        p.map(|e| e.path())
                            .map_err(|_| "cannot inspect administrator fragment")
                    })
                    .collect::<Result<Vec<_>, _>>()?;
                files.sort();
                for p in files
                    .into_iter()
                    .filter(|p| p.extension().is_some_and(|e| e == "toml"))
                {
                    stamps.push(trusted_policy_path(&p, false)?);
                }
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => {
                trusted_missing_policy_parent(path, &mut stamps)?
            }
            Err(_) => return Err("cannot inspect administrator fragment directory".into()),
        }
    }
    for directory in ["/usr/lib/pam.d", "/etc/pam.d"] {
        optional_policy_path(Path::new(directory), true, &mut stamps)?;
        if let Some(service) = service {
            optional_policy_path(&Path::new(directory).join(service), false, &mut stamps)?;
        }
    }
    Ok(stamps)
}
fn selected_policy_value(doc: &toml::Value) -> Result<SelectedPam, String> {
    let p = doc
        .get("terminal")
        .and_then(|v| v.get("remote_login"))
        .ok_or("administrator remote login policy is absent")?;
    if p.get("enabled").and_then(toml::Value::as_bool) != Some(true) {
        return Err("administrator remote login policy is disabled".into());
    }
    let name = |key: &str| -> Result<String, String> {
        let v = p
            .get(key)
            .and_then(toml::Value::as_str)
            .ok_or("administrator PAM service is absent")?;
        if v.is_empty()
            || v.len() > 128
            || v.starts_with('-')
            || !v
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
        {
            return Err("administrator PAM service is invalid".into());
        }
        Ok(v.into())
    };
    let service = name("pam_service")?;
    if service == name("greeter_pam_service")? {
        return Err("selected-account PAM service must be distinct from the greeter".into());
    }
    let minimum = p
        .get("minimum_uid")
        .and_then(toml::Value::as_integer)
        .and_then(|n| u32::try_from(n).ok())
        .filter(|n| *n >= 1000)
        .ok_or("invalid administrator minimum UID")?;
    let maximum = p
        .get("maximum_uid")
        .and_then(toml::Value::as_integer)
        .and_then(|n| u32::try_from(n).ok())
        .filter(|n| *n >= minimum)
        .ok_or("invalid administrator maximum UID")?;
    Ok(SelectedPam {
        service,
        minimum,
        maximum,
    })
}
#[derive(Clone)]
struct CachedPam {
    stamp: Vec<PolicyStamp>,
    policy: SelectedPam,
}
fn cached_selected_policy(
    cache: &mut Option<CachedPam>,
    stamp: Vec<PolicyStamp>,
    load: impl FnOnce() -> Result<(SelectedPam, Vec<PolicyStamp>), String>,
) -> Result<SelectedPam, String> {
    if let Some(c) = cache.as_ref().filter(|c| c.stamp == stamp) {
        return Ok(c.policy.clone());
    }
    // Never retain a failed trust result: an administrator may repair a PAM leaf in place
    // without changing its directory's mtime or the projection that names it.
    *cache = None;
    let (policy, stamp) = load()?;
    *cache = Some(CachedPam {
        stamp,
        policy: policy.clone(),
    });
    Ok(policy)
}
fn selected_policy() -> Result<SelectedPam, String> {
    static CACHE: std::sync::OnceLock<std::sync::Mutex<Option<CachedPam>>> =
        std::sync::OnceLock::new();
    let mut cache = CACHE
        .get_or_init(|| std::sync::Mutex::new(None))
        .lock()
        .map_err(|_| "administrator policy cache is unavailable")?;
    let service = cache.as_ref().map(|c| c.policy.service.as_str());
    let stamp = projection_stamp(service)?;
    cached_selected_policy(&mut cache, stamp, || {
        let before = projection_stamp(None)?;
        let doc = mios_resolver::resolve_projection(Path::new("/"))
            .map_err(|_| "administrator policy is invalid")?;
        let policy = selected_policy_value(&doc)?;
        trusted_policy_path(&Path::new("/usr/lib/pam.d").join(&policy.service), false)?;
        let mut host = vec![];
        optional_policy_path(
            &Path::new("/etc/pam.d").join(&policy.service),
            false,
            &mut host,
        )?;
        if before != projection_stamp(None)? {
            return Err("administrator policy changed during inspection".into());
        }
        let stamp = projection_stamp(Some(&policy.service))?;
        Ok((policy, stamp))
    })
}
fn selected_session(record: &RecordFacts, own: u32, policy: &SelectedPam) -> bool {
    let field = |key: &str| record.fields.get(key).map(String::as_str);
    let host = field("REMOTE_HOST").unwrap_or_default();
    (policy.minimum..=policy.maximum).contains(&own)
        && field("SERVICE") == Some(policy.service.as_str())
        && matches!(field("SEAT"), None | Some(""))
        && matches!(field("VTNR"), None | Some("" | "0"))
        && !host.is_empty()
        && host.len() <= 255
        && host
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b".:-%".contains(&b))
        && !matches!(
            host.trim_end_matches('.').to_ascii_lowercase().as_str(),
            "localhost" | "localhost.localdomain"
        )
        && !host
            .parse::<std::net::IpAddr>()
            .is_ok_and(|ip| ip.is_loopback() || ip.is_unspecified())
}
fn validate_login(
    own: u32,
    peer: (u32, u32),
    witness: &LoginWitness,
    client: &ProcessFacts,
    leader: &ProcessFacts,
    record: &RecordFacts,
) -> Result<(), String> {
    let selected = if record.fields.get("SERVICE").map(String::as_str) != Some("sshd") {
        Some(selected_policy()?)
    } else {
        None
    };
    validate_login_with_policy(
        own,
        peer,
        witness,
        client,
        leader,
        record,
        selected.as_ref(),
    )
}
fn validate_login_with_policy(
    own: u32,
    peer: (u32, u32),
    witness: &LoginWitness,
    client: &ProcessFacts,
    leader: &ProcessFacts,
    record: &RecordFacts,
    selected: Option<&SelectedPam>,
) -> Result<(), String> {
    if peer.0 != own
        || peer.1 == 0
        || witness.uid != own
        || client.uid != own
        || client.pid != witness.client.pid
        || client.start != witness.client_start
        || leader.pid != witness.leader
        || leader.start != witness.leader_start
        || (leader.uid != 0 && leader.uid != own)
        || [client.state, leader.state]
            .iter()
            .any(|s| matches!(s, 'Z' | 'X' | 'x'))
        || witness.client_start == 0
        || witness.leader_start == 0
    {
        return Err("stale or foreign authenticated login identity".into());
    }
    if record.uid != 0 || !record.regular || record.symlink || record.mode & 0o022 != 0 {
        return Err("untrusted logind login record".into());
    }
    let f = &record.fields;
    let field = |key: &str| f.get(key).map(String::as_str);
    let scope = format!("session-{}.scope", witness.session_id);
    if field("UID").and_then(|v| v.parse::<u32>().ok()) != Some(own)
        || field("LEADER").and_then(|v| v.parse::<u32>().ok()) != Some(witness.leader)
        || field("REMOTE") != Some("1")
        || !(field("SERVICE") == Some("sshd")
            || selected.is_some_and(|p| selected_session(record, own, p)))
        || !matches!(field("STATE"), Some("active" | "online"))
        || !matches!(
            field("CLASS"),
            Some("user" | "user-early" | "user-light" | "user-early-light")
        )
        || field("SCOPE") != Some(scope.as_str())
    {
        return Err("logind does not confirm an authenticated live remote user session".into());
    }
    if session_scope(&client.cgroup, own)? != witness.session_id
        || session_scope(&leader.cgroup, own)? != witness.session_id
    {
        return Err("tmux client and login leader belong to a different session".into());
    }
    Ok(())
}
/// Read-only authenticated login metadata for namespace partitioning. This receipt grants no controller/device route.
/// A bridge must preserve the original login tty descriptor and reprove it, rather than trusting an environment label.
pub fn login_identity(tty_fd: i32) -> Result<serde_json::Value, String> {
    if tty_fd < 0 || unsafe { libc::isatty(tty_fd) } != 1 {
        return Err("login identity requires an inherited tty descriptor".into());
    }
    let owner = uid();
    let pid = std::process::id();
    let process = process_facts(pid)?;
    if process.uid != owner {
        return Err("login process belongs to another user".into());
    }
    let session_id = session_scope(&process.cgroup, owner)?;
    let record = trusted_record(&session_id)?;
    let field = |name: &str| record.fields.get(name).map(String::as_str);
    let leader = field("LEADER")
        .and_then(|v| v.parse::<u32>().ok())
        .filter(|p| *p > 0)
        .ok_or("login leader is missing")?;
    let leader_facts = process_facts(leader)?;
    let scope = format!("session-{session_id}.scope");
    if field("UID").and_then(|v| v.parse::<u32>().ok()) != Some(owner)
        || field("SCOPE") != Some(scope.as_str())
        || !matches!(field("STATE"), Some("active" | "online"))
        || !matches!(
            field("CLASS"),
            Some("user" | "user-early" | "user-light" | "user-early-light")
        )
        || (leader_facts.uid != 0 && leader_facts.uid != owner)
        || [process.state, leader_facts.state]
            .iter()
            .any(|s| matches!(s, 'Z' | 'X' | 'x'))
        || session_scope(&leader_facts.cgroup, owner)? != session_id
    {
        return Err("root logind state does not confirm this live user login".into());
    }
    let mut tty: libc::stat = unsafe { std::mem::zeroed() };
    if unsafe { libc::fstat(tty_fd, &mut tty) } != 0 || tty.st_mode & libc::S_IFMT != libc::S_IFCHR
    {
        return Err("login tty descriptor identity is unavailable".into());
    }
    let again = process_facts(pid)?;
    let leader_again = process_facts(leader)?;
    if again.start != process.start
        || again.cgroup != process.cgroup
        || leader_again.start != leader_facts.start
        || leader_again.cgroup != leader_facts.cgroup
    {
        return Err("login changed during namespace inspection".into());
    }
    Ok(
        serde_json::json!({"version":1,"namespace_only":true,"uid":owner,"session_id":session_id,"scope":scope,
        "leader":leader,"leader_start":leader_facts.start,"process_pid":pid,"process_start":process.start,
        "tty_dev":tty.st_dev,"tty_ino":tty.st_ino,"tty_rdev":tty.st_rdev,
        "service":field("SERVICE"),"remote":field("REMOTE")==Some("1")}),
    )
}
fn socket_owned(socket: &Path, owner: u32) -> Result<(), String> {
    let meta = fs::symlink_metadata(socket).map_err(|e| e.to_string())?;
    if !socket.is_absolute() || !meta.file_type().is_socket() || meta.uid() != owner {
        return Err("outer tmux socket is not owned by the authenticated user".into());
    }
    Ok(())
}
/// Authenticate the actual outer client. The callback peer may run under the tmux server's different cgroup.
/// This proof does not consult seat0: an authenticated SSH user remains valid while the physical display is a greeter.
pub fn authenticate(
    cfg: &GlobalConfig,
    req: &HandoffRequest,
    peer: (u32, u32),
) -> Result<LoginWitness, String> {
    req.validate()?;
    if peer.0 != uid() || peer.1 == 0 {
        return Err("foreign handoff peer".into());
    }
    let socket = req
        .socket
        .as_deref()
        .ok_or("login proof requires an explicit socket")?;
    let name = req
        .client
        .as_deref()
        .ok_or("login proof requires an explicit client")?;
    socket_owned(socket, uid())?;
    let until = deadline(Duration::from_millis(cfg.controller.context_stale_ms))?;
    let rows = crate::runtime::run_bounded(
        &[
            "/usr/bin/tmux".into(),
            "-S".into(),
            socket.to_string_lossy().into_owned(),
            "list-clients".into(),
            "-F".into(),
            "#{client_name}\t#{client_pid}\t#{client_tty}".into(),
        ],
        remaining(until)?,
    )?;
    let mut rows = rows.lines().filter_map(|line| {
        let fields: Vec<_> = line.split('\t').collect();
        (fields.len() == 3 && fields[0] == name).then_some(fields)
    });
    let row = rows
        .next()
        .ok_or("outer tmux client is no longer attached")?;
    if rows.next().is_some() {
        return Err("outer tmux client is ambiguous".into());
    }
    let pid = row[1]
        .parse::<u32>()
        .ok()
        .filter(|p| *p > 0)
        .ok_or("invalid outer client PID")?;
    let client = crate::runtime::tmux_client_at(
        Path::new("/proc"),
        pid,
        &cfg.controller.nav.tmux_sockets,
        uid(),
    )
    .ok_or("outer client is not a configured native tmux client")?;
    if client.tty != row[2]
        || fs::canonicalize(&client.socket).map_err(|e| e.to_string())?
            != fs::canonicalize(socket).map_err(|e| e.to_string())?
    {
        return Err("outer client socket or tty does not match the explicit callback".into());
    }
    let target = crate::tmux_capture::observe(&client, remaining(until)?)?;
    if req.pane.as_deref().is_some_and(|pane| pane != target.pane) {
        return Err("callback pane is not focused on the authenticated outer client".into());
    }
    let facts = process_facts(pid)?;
    let session_id = session_scope(&facts.cgroup, uid())?;
    let record = trusted_record(&session_id)?;
    let leader = record
        .fields
        .get("LEADER")
        .and_then(|v| v.parse::<u32>().ok())
        .filter(|p| *p > 0)
        .ok_or("logind session has no live leader")?;
    let leader_facts = process_facts(leader)?;
    let witness = LoginWitness {
        uid: uid(),
        session_id,
        leader,
        leader_start: leader_facts.start,
        client,
        client_start: facts.start,
    };
    validate_login(uid(), peer, &witness, &facts, &leader_facts, &record)?;
    let now = crate::tmux_capture::observe(&witness.client, remaining(until)?)?;
    if !target.matches(&now) || !live(&witness) {
        return Err("authenticated login changed during handoff".into());
    }
    Ok(witness)
}
/// Recheck PID starts, root logind state and both session cgroups; no tmux command, compositor or device I/O.
pub fn live(witness: &LoginWitness) -> bool {
    let verify = || -> Result<(), String> {
        if witness.uid != uid() {
            return Err("foreign login witness".into());
        }
        socket_owned(&witness.client.socket, witness.uid)?;
        let client = process_facts(witness.client.pid)?;
        let leader = process_facts(witness.leader)?;
        let record = trusted_record(&witness.session_id)?;
        validate_login(
            uid(),
            (uid(), std::process::id()),
            witness,
            &client,
            &leader,
            &record,
        )
    };
    verify().is_ok()
}

#[cfg(test)]
mod tests {
    use super::*;
    fn facts(
        id: &str,
        client_pid: u32,
        leader_pid: u32,
    ) -> (LoginWitness, ProcessFacts, ProcessFacts, RecordFacts) {
        let cgroup = format!("0::/user.slice/user-1000.slice/session-{id}.scope\n");
        let client = ProcessFacts {
            uid: 1000,
            pid: client_pid,
            start: 12345,
            state: 'S',
            cgroup: cgroup.clone(),
        };
        let leader = ProcessFacts {
            uid: 0,
            pid: leader_pid,
            start: 12000,
            state: 'S',
            cgroup,
        };
        let witness = LoginWitness {
            uid: 1000,
            session_id: id.into(),
            leader: leader_pid,
            leader_start: leader.start,
            client: TmuxClient {
                pid: client_pid,
                start: client.start,
                instance: 0,
                socket_dev: 0,
                socket_ino: 0,
                tty: format!("/dev/pts/{client_pid}"),
                socket: PathBuf::from(format!("/tmp/tmux-1000/mios-{id}")),
            },
            client_start: client.start,
        };
        let fields = format!("UID=1000\nLEADER={leader_pid}\nREMOTE=1\nSERVICE=sshd\nSTATE=online\nCLASS=user\nSCOPE=session-{id}.scope\nACTIVE=0\n")
            .lines().map(|line| { let (k,v)=line.split_once('=').unwrap(); (k.into(),v.into()) }).collect();
        (
            witness,
            client,
            leader,
            RecordFacts {
                uid: 0,
                mode: 0o100644,
                regular: true,
                symlink: false,
                fields,
            },
        )
    }
    fn pam_policy() -> SelectedPam {
        SelectedPam {
            service: "mios-remote-login".into(),
            minimum: 1000,
            maximum: 60000,
        }
    }
    #[test]
    fn configured_selected_pam_requires_exact_service_remote_host_seatless_scope_uid_and_live_witnesses(
    ) {
        let (w, c, l, mut r) = facts("pam37", 200, 100);
        let policy = pam_policy();
        r.fields.insert("SERVICE".into(), policy.service.clone());
        r.fields
            .insert("REMOTE_HOST".into(), "mios-pam-proof.invalid".into());
        assert!(
            validate_login_with_policy(1000, (1000, 999), &w, &c, &l, &r, Some(&policy)).is_ok()
        );
        assert!(
            validate_login_with_policy(1000, (1000, 999), &w, &c, &l, &r, None).is_err(),
            "no implicit PAM allowance"
        );
        for (key, value) in [
            ("SERVICE", "greetd"),
            ("SERVICE", "sshd-lookalike"),
            ("REMOTE", "0"),
            ("REMOTE_HOST", ""),
            ("REMOTE_HOST", "localhost"),
            ("REMOTE_HOST", "127.0.0.1"),
            ("REMOTE_HOST", "0.0.0.0"),
            ("SEAT", "seat0"),
            ("VTNR", "1"),
            ("UID", "1001"),
            ("STATE", "closing"),
            ("CLASS", "greeter"),
            ("SCOPE", "session-wrong.scope"),
        ] {
            let mut bad = r.clone();
            bad.fields.insert(key.into(), value.into());
            assert!(
                validate_login_with_policy(1000, (1000, 999), &w, &c, &l, &bad, Some(&policy))
                    .is_err(),
                "accepted {key}={value}"
            );
        }
        let mut bad = policy.clone();
        bad.minimum = 1001;
        assert!(validate_login_with_policy(1000, (1000, 999), &w, &c, &l, &r, Some(&bad)).is_err());
        let mut stale = c.clone();
        stale.start += 1;
        assert!(
            validate_login_with_policy(1000, (1000, 999), &w, &stale, &l, &r, Some(&policy))
                .is_err()
        );
        let mut foreign = c.clone();
        foreign.cgroup = "0::/user.slice/user-1000.slice/session-other.scope\n".into();
        assert!(
            validate_login_with_policy(1000, (1000, 999), &w, &foreign, &l, &r, Some(&policy))
                .is_err()
        );
    }
    #[test]
    fn failed_pam_leaf_trust_retries_after_in_place_repair_and_changed_policy_cannot_keep_old_approval(
    ) {
        let mut cache = None;
        let stamp = Vec::new();
        let policy = pam_policy();
        assert!(cached_selected_policy(&mut cache, stamp.clone(), || Err(
            "PAM leaf mode0664".into()
        ))
        .is_err());
        assert!(cache.is_none());
        assert_eq!(
            cached_selected_policy(&mut cache, stamp.clone(), || Ok((
                policy.clone(),
                stamp.clone()
            )))
            .unwrap(),
            policy,
            "repair to0644 must retry even when config/directory metadata is unchanged"
        );
        assert_eq!(
            cached_selected_policy(&mut cache, stamp, || panic!(
                "unchanged trusted policy must not be reparsed"
            ))
            .unwrap(),
            policy
        );
        let changed = vec![PolicyStamp {
            path: "/usr/share/mios/mios.toml".into(),
            dev: 1,
            ino: 2,
            size: 3,
            mode: 0o644,
            uid: 0,
            modified: (4, 5),
        }];
        assert!(cached_selected_policy(&mut cache, changed, || Err(
            "administrator disabled remote login".into()
        ))
        .is_err());
        assert!(
            cache.is_none(),
            "a revoked policy never leaves an old approval cached"
        );
    }
    #[test]
    fn selected_service_policy_has_no_disabled_missing_malformed_or_greeter_fallback() {
        let base:toml::Value="[terminal.remote_login]\nenabled=true\npam_service='mios-remote-login'\ngreeter_pam_service='mios-remote-greeter'\nminimum_uid=1000\nmaximum_uid=60000\n".parse().unwrap();
        assert_eq!(selected_policy_value(&base).unwrap(), pam_policy());
        for (key, value) in [
            ("enabled", false.into()),
            ("pam_service", "".into()),
            ("pam_service", "../login".into()),
            ("pam_service", "-login".into()),
            ("pam_service", "mios-remote-greeter".into()),
            ("minimum_uid", 0.into()),
            ("maximum_uid", 999.into()),
        ] {
            let mut bad = base.clone();
            bad["terminal"]["remote_login"][key] = value;
            assert!(selected_policy_value(&bad).is_err(), "accepted {key}");
        }
        let mut absent = base.clone();
        absent["terminal"]["remote_login"]
            .as_table_mut()
            .unwrap()
            .remove("pam_service");
        assert!(selected_policy_value(&absent).is_err());
        let tmp = tempfile::tempdir().unwrap();
        let doc = tmp.path().join("mios.toml");
        fs::write(&doc, "[terminal.remote_login]\nenabled=true").unwrap();
        assert!(
            trusted_policy_path(&doc, false).is_err(),
            "a user-owned apparent vendor/host document never grants authority"
        );
        let mut stamps = vec![];
        assert!(
            optional_policy_path(&tmp.path().join("missing.toml"), false, &mut stamps).is_err(),
            "an absent optional layer cannot hide a writable ancestor from trust checks"
        );
        assert!(trusted_missing_policy_parent(&tmp.path().join("missing.d"), &mut stamps).is_err());
    }
    #[test]
    fn stale_endpoint_probe_is_bounded_and_never_unlinks_a_live_listener() {
        let directory = tempfile::tempdir().unwrap();
        let path = directory.path().join("mios-inputd-handoff.sock");
        let listener = std::os::unix::net::UnixListener::bind(&path).unwrap();
        assert!(endpoint_live(&path).unwrap());
        assert!(path.exists());
        drop(listener);
        assert!(!endpoint_live(&path).unwrap());
        assert!(path.exists(), "the caller owns explicit stale recovery");
    }
    #[test]
    fn authenticated_ssh_is_independent_of_physical_greeter_and_callback_cgroup() {
        let (w, c, l, r) = facts("37", 200, 100);
        // The callback PID is deliberately unrelated to the outer client/login leader; SO_PEERCRED binds its UID.
        assert!(validate_login(1000, (1000, 999), &w, &c, &l, &r).is_ok());
        assert_eq!(r.fields["ACTIVE"], "0");
    }
    #[test]
    fn separate_authenticated_logins_do_not_retarget_each_other() {
        let (a, ca, la, ra) = facts("A", 200, 100);
        let (b, cb, lb, rb) = facts("B", 201, 101);
        assert!(validate_login(1000, (1000, 999), &a, &ca, &la, &ra).is_ok());
        assert!(validate_login(1000, (1000, 999), &b, &cb, &lb, &rb).is_ok());
        assert!(validate_login(1000, (1000, 999), &a, &cb, &lb, &rb).is_err());
        let mut cb = ca.clone();
        cb.cgroup = cb.cgroup.replace("session-A", "session-B");
        assert!(validate_login(1000, (1000, 999), &a, &cb, &la, &ra).is_err());
    }
    #[test]
    fn foreign_closing_forged_and_stale_logins_are_rejected() {
        let (w, c, l, r) = facts("37", 200, 100);
        assert!(validate_login(1000, (1001, 999), &w, &c, &l, &r).is_err());
        for (key, value) in [
            ("UID", "1001"),
            ("STATE", "closing"),
            ("REMOTE", "0"),
            ("CLASS", "greeter"),
            ("SERVICE", "login"),
            ("SCOPE", "session-38.scope"),
            ("LEADER", "101"),
        ] {
            let mut bad = r.clone();
            bad.fields.insert(key.into(), value.into());
            assert!(
                validate_login(1000, (1000, 999), &w, &c, &l, &bad).is_err(),
                "{key}"
            );
        }
        let mut bad = r.clone();
        bad.uid = 1000;
        assert!(validate_login(1000, (1000, 999), &w, &c, &l, &bad).is_err());
        let mut bad = r.clone();
        bad.mode |= 0o020;
        assert!(validate_login(1000, (1000, 999), &w, &c, &l, &bad).is_err());
        let mut bad = r.clone();
        bad.symlink = true;
        assert!(validate_login(1000, (1000, 999), &w, &c, &l, &bad).is_err());
        let mut bad = c.clone();
        bad.start += 1;
        assert!(validate_login(1000, (1000, 999), &w, &bad, &l, &r).is_err());
        let mut bad = l.clone();
        bad.start += 1;
        assert!(validate_login(1000, (1000, 999), &w, &c, &bad, &r).is_err());
        let mut bad = c.clone();
        bad.state = 'Z';
        assert!(validate_login(1000, (1000, 999), &w, &bad, &l, &r).is_err());
    }
    #[test]
    fn user_delegated_lookalike_scopes_and_ambiguous_cgroups_are_rejected() {
        assert!(session_scope(
            "0::/user.slice/user-1000.slice/user@1000.service/app.slice/session-37.scope",
            1000
        )
        .is_err());
        assert!(session_scope("0::/user.slice/user-1000.slice/session-37.scope\n1:name=systemd:/user.slice/user-1000.slice/session-38.scope", 1000).is_err());
        assert!(session_scope("0::/user.slice/user-1001.slice/session-37.scope", 1000).is_err());
    }
    #[test]
    fn protocol_rejects_unknown_fields_invalid_modes_and_partial_frontend_witnesses() {
        assert!(serde_json::from_str::<HandoffRequest>(
            r#"{"version":1,"op":"detach","execute":"sh"}"#
        )
        .is_err());
        for value in [
            serde_json::json!({"version":1,"op":"attach","socket":"relative","client":"/dev/pts/2"}),
            serde_json::json!({"version":1,"op":"window","mode":"shell"}),
            serde_json::json!({"version":1,"op":"window","mode":"on","frontend_pid":42}),
            serde_json::json!({"version":1,"op":"detach","generation":0}),
            serde_json::json!({"version":2,"op":"detach"}),
        ] {
            assert!(serde_json::from_value::<HandoffRequest>(value)
                .unwrap()
                .validate()
                .is_err());
        }
    }
    #[test]
    fn private_protocol_roundtrip_and_truncated_or_oversized_frames() {
        let (mut a, mut b) = UnixStream::pair().unwrap();
        assert_eq!(peer_credentials(&a).unwrap(), (uid(), std::process::id()));
        let req = HandoffRequest {
            op: "window".into(),
            mode: Some("on".into()),
            ..Default::default()
        };
        write_frame(
            &mut a,
            &serde_json::to_vec(&req).unwrap(),
            deadline(Duration::from_secs(1)).unwrap(),
        )
        .unwrap();
        assert_eq!(
            read_request(&mut b, Duration::from_secs(1))
                .unwrap()
                .mode
                .as_deref(),
            Some("on")
        );
        write_reply(&mut b, &serde_json::json!({"ok":true,"generation":7})).unwrap();
        let reply: serde_json::Value = serde_json::from_slice(
            &frame(&mut a, deadline(Duration::from_secs(1)).unwrap()).unwrap(),
        )
        .unwrap();
        assert_eq!(reply["generation"], 7);
        let (mut a, mut b) = UnixStream::pair().unwrap();
        a.write_all(&((MAX_FRAME as u32 + 1).to_be_bytes()))
            .unwrap();
        assert!(read_request(&mut b, Duration::from_millis(20)).is_err());
        let (mut a, mut b) = UnixStream::pair().unwrap();
        a.write_all(&[0, 0, 0, 5, b'{']).unwrap();
        drop(a);
        assert!(read_request(&mut b, Duration::from_millis(20)).is_err());
    }
}
