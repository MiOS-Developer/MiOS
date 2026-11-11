// AI-hint: App-owned semantic controller input. Never writes terminal input or reads hardware.
// AI-related: mios-inputd::runtime, mios-mons::input
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::io::{self, Read};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{FileTypeExt, MetadataExt, OpenOptionsExt, PermissionsExt};
use std::os::unix::net::UnixDatagram;
use std::path::{Path, PathBuf};

const VERSION: u32 = 1;
const MAX_PACKET: usize = 2048;
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
#[serde(rename_all = "snake_case")]
pub enum Action {
    Menu,
    Pause,
}
impl Action {
    /// Only the explicitly supported menu and pause shortcuts become IPC.
    pub fn from_key(action: &str) -> Option<Self> {
        match action {
            "key:o" | "key:F1" => Some(Self::Menu),
            "key:p" => Some(Self::Pause),
            _ => None,
        }
    }
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct Process {
    pub pid: u32,
    pub start: u64,
}
#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct Focus {
    pub channel: String,
    pub epoch: u64,
    pub address: String,
    pub window_pid: u64,
    pub generation: u64,
    pub app: String,
    pub process: Process,
}
#[derive(Deserialize, Serialize)]
struct Packet {
    version: u32,
    target: u32,
    target_start: u64,
    source_start: u64,
    created_ns: u64,
    focus: Focus,
    action: Action,
}
fn error(message: &str) -> io::Error {
    io::Error::other(message)
}
pub fn start(pid: u32) -> Option<u64> {
    let path = PathBuf::from(format!("/proc/{pid}"));
    if pid == 0 || std::fs::metadata(&path).ok()?.uid() != unsafe { libc::getuid() } {
        return None;
    }
    let stat = std::fs::read_to_string(path.join("stat")).ok()?;
    stat[stat.rfind(')')? + 1..]
        .split_whitespace()
        .nth(19)?
        .parse()
        .ok()
}
fn foreground(pid: u32) -> bool {
    let Ok(stat) = std::fs::read_to_string(format!("/proc/{pid}/stat")) else {
        return false;
    };
    let Some(end) = stat.rfind(')') else {
        return false;
    };
    let f = stat[end + 1..].split_whitespace().collect::<Vec<_>>();
    f.get(2).and_then(|s| s.parse::<u32>().ok()) == Some(pid)
        && f.get(5).and_then(|s| s.parse::<u32>().ok()) == Some(pid)
}
pub fn monotonic_ns() -> u64 {
    let mut time = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut time) } != 0 {
        return 0;
    }
    (time.tv_sec as u64)
        .saturating_mul(1_000_000_000)
        .saturating_add(time.tv_nsec as u64)
}
fn trusted_runtime(path: &Path) -> io::Result<()> {
    let meta = std::fs::symlink_metadata(path)?;
    if !meta.is_dir() || meta.uid() != unsafe { libc::getuid() } || meta.mode() & 0o077 != 0 {
        return Err(error("app input requires a private user runtime directory"));
    }
    Ok(())
}
fn endpoint(runtime: &Path, pid: u32, start: u64) -> PathBuf {
    runtime.join(format!("mios-app-input-{pid}-{start}.sock"))
}
fn private_file(path: &Path) -> io::Result<std::fs::File> {
    let file = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC | libc::O_NONBLOCK)
        .open(path)?;
    let meta = file.metadata()?;
    if !meta.is_file()
        || meta.uid() != unsafe { libc::getuid() }
        || meta.mode() & 0o077 != 0
        || meta.len() > 131072
    {
        return Err(error("untrusted app input authority"));
    }
    Ok(file)
}
fn authority_path(runtime: &Path, pid: u32, start: u64) -> PathBuf {
    runtime.join(format!("mios-app-authority-{pid}-{start}.json"))
}
fn authority(runtime: &Path, pid: u32, start: u64) -> io::Result<serde_json::Value> {
    let mut bytes = Vec::new();
    private_file(&authority_path(runtime, pid, start))?
        .take(131073)
        .read_to_end(&mut bytes)?;
    if bytes.len() > 131072 {
        return Err(error("oversized app input authority"));
    }
    serde_json::from_slice(&bytes).map_err(io::Error::other)
}
fn authorized(
    packet: &Packet,
    source: u32,
    value: &serde_json::Value,
    now: u64,
    previous: Option<(u32, u64, u64)>,
) -> bool {
    let scope = &value["scopes"][packet.focus.channel.as_str()];
    let Ok(focus) = serde_json::from_value::<Focus>(scope["focus"].clone()) else {
        return false;
    };
    let lease = scope["sampled_ns"].as_u64().unwrap_or(0);
    let bound = scope["stale_ms"]
        .as_u64()
        .filter(|ms| *ms > 0 && *ms <= 10000)
        .unwrap_or(0)
        .saturating_mul(1_000_000);
    value["pid"].as_u64() == Some(source as u64)
        && value["start_time"].as_u64() == Some(packet.source_start)
        && value["version"].as_u64() == Some(VERSION as u64)
        && packet.focus.process.pid == packet.target
        && packet.focus.process.start == packet.target_start
        && bound > 0
        && lease > 0
        && now >= lease
        && now - lease < bound
        && packet.created_ns > 0
        && now >= packet.created_ns
        && now - packet.created_ns < bound
        && packet.focus == focus
        && !previous.is_some_and(|(pid, start, at)| {
            pid == source && start == packet.source_start && packet.created_ns <= at
        })
}
#[derive(Serialize)]
struct Lease {
    focus: Focus,
    sampled_ns: u64,
    stale_ms: u64,
}
/// One authenticated bridge may publish independent seat and explicit-route
/// leases. This file never changes the global controller-return manifest.
pub struct Publisher {
    path: PathBuf,
    pid: u32,
    start: u64,
    scopes: BTreeMap<String, Lease>,
    inode: Option<(u64, u64)>,
    sequence: u64,
    file: Option<std::fs::File>,
}
impl Publisher {
    fn revoke_file(&mut self) {
        // Keep the original inode open: even if its pathname was replaced,
        // invalidating our old lease cannot truncate a replacement file.
        if let Some(file) = &self.file {
            let _ = file.set_len(0);
        }
        if self.inode.is_some_and(|identity| {
            std::fs::symlink_metadata(&self.path)
                .ok()
                .is_some_and(|m| (m.dev(), m.ino()) == identity)
        }) {
            let _ = std::fs::remove_file(&self.path);
        }
        self.inode = None;
        self.file = None;
    }
    pub fn new(runtime: &Path) -> io::Result<Self> {
        trusted_runtime(runtime)?;
        let pid = std::process::id();
        let start = start(pid).ok_or_else(|| error("bridge identity missing"))?;
        Ok(Self {
            path: authority_path(runtime, pid, start),
            pid,
            start,
            scopes: BTreeMap::new(),
            inode: None,
            sequence: 0,
            file: None,
        })
    }
    pub fn update(
        &mut self,
        channel: &str,
        focus: Option<Focus>,
        sampled_ns: u64,
        stale_ms: u64,
    ) -> io::Result<()> {
        if channel.len() > 512 || channel.chars().any(char::is_control) {
            return Err(error("invalid app authority channel"));
        }
        if let Some(focus) = focus {
            if self.scopes.len() >= 64 && !self.scopes.contains_key(channel) {
                return Err(error("app authority scope limit reached"));
            }
            if focus.channel != channel || sampled_ns == 0 || !(1..=10000).contains(&stale_ms) {
                return Err(error("invalid app authority lease"));
            }
            self.scopes.insert(
                channel.into(),
                Lease {
                    focus,
                    sampled_ns,
                    stale_ms,
                },
            );
        } else if self.scopes.remove(channel).is_none() {
            return Ok(());
        }
        self.sequence = self
            .sequence
            .checked_add(1)
            .ok_or_else(|| error("authority sequence exhausted"))?;
        let temp = self.path.with_extension(format!("{}.tmp", self.sequence));
        use std::io::Write;
        let mut file = match std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
            .open(&temp)
        {
            Ok(file) => file,
            Err(error) => {
                self.revoke_file();
                return Err(error);
            }
        };
        let value = serde_json::json!({"version":VERSION,"pid":self.pid,"start_time":self.start,"scopes":self.scopes});
        let result = serde_json::to_writer(&mut file, &value)
            .map_err(io::Error::other)
            .and_then(|_| file.flush())
            .and_then(|_| std::fs::rename(&temp, &self.path));
        if result.is_err() {
            let _ = std::fs::remove_file(&temp);
            self.revoke_file();
        }
        result?;
        let meta = file.metadata()?;
        self.inode = Some((meta.dev(), meta.ino()));
        self.file = Some(file);
        Ok(())
    }
}
impl Drop for Publisher {
    fn drop(&mut self) {
        if self.inode.is_some_and(|identity| {
            std::fs::symlink_metadata(&self.path)
                .ok()
                .is_some_and(|meta| (meta.dev(), meta.ino()) == identity)
        }) {
            let _ = std::fs::remove_file(&self.path);
        }
    }
}
/// A datagram is pinned to this process's socket inode. If the app exits it is
/// discarded by the kernel; neither the sender nor receiver has a PTY fallback.
pub fn send(
    runtime: &Path,
    pid: u32,
    target_start: u64,
    focus: Focus,
    action: Action,
) -> io::Result<()> {
    trusted_runtime(runtime)?;
    if focus.process
        != (Process {
            pid,
            start: target_start,
        })
    {
        return Err(error(
            "app input process differs from accepted focus witness",
        ));
    }
    if start(pid) != Some(target_start) {
        return Err(error("app process was replaced"));
    }
    let path = endpoint(runtime, pid, target_start);
    let before = std::fs::symlink_metadata(&path)?;
    if !before.file_type().is_socket()
        || before.uid() != unsafe { libc::getuid() }
        || before.mode() & 0o077 != 0
    {
        return Err(error("untrusted app input endpoint"));
    }
    let socket = UnixDatagram::unbound()?;
    socket.set_nonblocking(true)?;
    socket.connect(&path)?;
    let after = std::fs::symlink_metadata(&path)?;
    if (before.dev(), before.ino()) != (after.dev(), after.ino())
        || start(pid) != Some(target_start)
    {
        return Err(error("app input endpoint changed"));
    }
    let source_start =
        start(std::process::id()).ok_or_else(|| error("input source identity missing"))?;
    let packet = Packet {
        version: VERSION,
        target: pid,
        target_start,
        source_start,
        created_ns: monotonic_ns(),
        focus,
        action,
    };
    let bytes = serde_json::to_vec(&packet).map_err(io::Error::other)?;
    if bytes.len() > MAX_PACKET {
        return Err(error("app input packet too large"));
    }
    socket.send(&bytes)?;
    Ok(())
}
pub struct Receiver {
    socket: UnixDatagram,
    runtime: PathBuf,
    path: PathBuf,
    inode: (u64, u64),
    pid: u32,
    start: u64,
    previous: Option<(u32, u64, u64)>,
    enabled: bool,
}
impl Receiver {
    pub fn bind(runtime: &Path) -> io::Result<Self> {
        trusted_runtime(runtime)?;
        let pid = std::process::id();
        let start = start(pid).ok_or_else(|| error("app process identity missing"))?;
        let path = endpoint(runtime, pid, start);
        // Never unlink an existing listener or stale entry to obtain ownership.
        let socket = UnixDatagram::bind(&path)?;
        let meta = std::fs::symlink_metadata(&path)?;
        let mut receiver = Self {
            socket,
            runtime: runtime.to_owned(),
            path,
            inode: (meta.dev(), meta.ino()),
            pid,
            start,
            previous: None,
            enabled: true,
        };
        std::fs::set_permissions(&receiver.path, std::fs::Permissions::from_mode(0o600))?;
        receiver.socket.set_nonblocking(true)?;
        let on: libc::c_int = 1;
        if unsafe {
            libc::setsockopt(
                receiver.socket.as_raw_fd(),
                libc::SOL_SOCKET,
                libc::SO_PASSCRED,
                (&on as *const libc::c_int).cast(),
                std::mem::size_of_val(&on) as libc::socklen_t,
            )
        } != 0
        {
            return Err(io::Error::last_os_error());
        }
        receiver.discard();
        Ok(receiver)
    }
    pub fn window(&mut self, enabled: bool) {
        self.enabled = enabled;
        self.discard();
    }
    fn datagram(&self) -> io::Result<Option<(Vec<u8>, libc::ucred)>> {
        let mut bytes = [0u8; MAX_PACKET];
        // usize supplies the alignment required by cmsghdr on Linux.
        let mut control = [0usize; 16];
        let mut iov = libc::iovec {
            iov_base: bytes.as_mut_ptr().cast(),
            iov_len: bytes.len(),
        };
        let mut msg: libc::msghdr = unsafe { std::mem::zeroed() };
        msg.msg_iov = &mut iov;
        msg.msg_iovlen = 1;
        msg.msg_control = control.as_mut_ptr().cast();
        msg.msg_controllen = std::mem::size_of_val(&control) as _;
        let count = unsafe { libc::recvmsg(self.socket.as_raw_fd(), &mut msg, libc::MSG_DONTWAIT) };
        if count < 0 {
            let err = io::Error::last_os_error();
            return if err.kind() == io::ErrorKind::WouldBlock {
                Ok(None)
            } else {
                Err(err)
            };
        }
        if msg.msg_flags & (libc::MSG_TRUNC | libc::MSG_CTRUNC) != 0 {
            return Err(error("truncated app input packet"));
        }
        let mut header = unsafe { libc::CMSG_FIRSTHDR(&msg) };
        while !header.is_null() {
            if unsafe {
                (*header).cmsg_level == libc::SOL_SOCKET
                    && (*header).cmsg_type == libc::SCM_CREDENTIALS
                    && (*header).cmsg_len
                        >= libc::CMSG_LEN(std::mem::size_of::<libc::ucred>() as u32) as _
            } {
                let credentials = unsafe {
                    std::ptr::read_unaligned(libc::CMSG_DATA(header).cast::<libc::ucred>())
                };
                return Ok(Some((bytes[..count as usize].to_vec(), credentials)));
            }
            header = unsafe { libc::CMSG_NXTHDR(&msg, header) };
        }
        Err(error("app input source credentials missing"))
    }
    pub fn discard(&mut self) {
        for _ in 0..32 {
            if !matches!(self.datagram(), Ok(Some(_))) {
                break;
            }
        }
    }
    /// At most 32 nonblocking reads per UI frame. Source credentials must match
    /// the live inputd authority, and its current focus lease must still select
    /// this app. A focus loss, handoff, stale queue or replaced bridge consumes it.
    pub fn poll(&mut self) -> io::Result<Option<Action>> {
        for _ in 0..32 {
            let Some((bytes, cred)) = self.datagram()? else {
                return Ok(None);
            };
            let Ok(packet) = serde_json::from_slice::<Packet>(&bytes) else {
                continue;
            };
            if !self.enabled
                || cred.pid <= 0
                || cred.uid != unsafe { libc::getuid() }
                || packet.version != VERSION
                || packet.target != self.pid
                || packet.target_start != self.start
                || start(cred.pid as u32) != Some(packet.source_start)
                || !foreground(self.pid)
            {
                continue;
            }
            let Ok(value) = authority(&self.runtime, cred.pid as u32, packet.source_start) else {
                continue;
            };
            if !authorized(
                &packet,
                cred.pid as u32,
                &value,
                monotonic_ns(),
                self.previous,
            ) {
                continue;
            }
            self.previous = Some((cred.pid as u32, packet.source_start, packet.created_ns));
            return Ok(Some(packet.action));
        }
        Ok(None)
    }
}
impl Drop for Receiver {
    fn drop(&mut self) {
        if std::fs::symlink_metadata(&self.path)
            .ok()
            .is_some_and(|m| (m.dev(), m.ino()) == self.inode)
        {
            let _ = std::fs::remove_file(&self.path);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn packet() -> Packet {
        Packet {
            version: 1,
            target: 40,
            target_start: 41,
            source_start: 11,
            created_ns: 1_000_000_000,
            focus: Focus {
                channel: "seat".into(),
                epoch: 1,
                address: "tmux:client-instance-7-pane-2".into(),
                window_pid: 40,
                generation: 2,
                app: "mon".into(),
                process: Process { pid: 40, start: 41 },
            },
            action: Action::Pause,
        }
    }
    fn lease(packet: &Packet) -> serde_json::Value {
        serde_json::json!({"version":1,"pid":10,"start_time":11,"scopes":{"seat":{"focus":packet.focus,"sampled_ns":1_000_000_000u64,"stale_ms":1000}}})
    }
    #[test]
    fn focus_handoff_expiry_and_source_replacement_cancel_queued_app_actions() {
        let packet = packet();
        let value = lease(&packet);
        let now = 1_010_000_000;
        assert!(authorized(&packet, 10, &value, now, None));
        for key in ["address", "window_pid", "generation", "app", "epoch"] {
            let mut changed = value.clone();
            changed["scopes"]["seat"]["focus"][key] = if key == "address" || key == "app" {
                "other".into()
            } else {
                99.into()
            };
            assert!(!authorized(&packet, 10, &changed, now, None), "{key}");
        }
        assert!(!authorized(&packet, 12, &value, now, None));
        let mut changed = value.clone();
        changed["start_time"] = 12.into();
        assert!(!authorized(&packet, 10, &changed, now, None));
        assert!(!authorized(&packet, 10, &value, 2_000_000_000, None));
        assert!(!authorized(&packet, 10, &value, 999_999_999, None));
        assert!(!authorized(
            &packet,
            10,
            &value,
            now,
            Some((10, 11, packet.created_ns))
        ));
        let mut returned = value.clone();
        returned["scopes"]["seat"]["focus"]["epoch"] = 3.into();
        assert!(
            !authorized(&packet, 10, &returned, now, None),
            "A-to-B-to-A cannot resurrect an old packet"
        );
        let mut replaced = value.clone();
        replaced["scopes"]["seat"]["focus"]["process"]["pid"] = 42.into();
        assert!(!authorized(&packet, 10, &replaced, now, None));
    }
    #[test]
    fn only_semantic_menu_pause_and_kernel_authenticated_datagrams_are_accepted() {
        assert_eq!(Action::from_key("key:p"), Some(Action::Pause));
        assert_eq!(Action::from_key("key:F1"), Some(Action::Menu));
        for key in ["key:CTRL+z", "key:CTRL+s", "key:ENTER", "tmux:key y", ""] {
            assert_eq!(Action::from_key(key), None);
        }
        let dir = tempfile::tempdir().unwrap();
        let receiver = Receiver::bind(dir.path()).unwrap();
        let mut focus = packet().focus;
        focus.process = Process {
            pid: receiver.pid,
            start: receiver.start,
        };
        send(
            dir.path(),
            receiver.pid,
            receiver.start,
            focus.clone(),
            Action::Menu,
        )
        .unwrap();
        let (bytes, cred) = receiver.datagram().unwrap().unwrap();
        assert_eq!(cred.pid as u32, std::process::id());
        assert_eq!(cred.uid, unsafe { libc::getuid() });
        assert_eq!(
            serde_json::from_slice::<Packet>(&bytes).unwrap().action,
            Action::Menu
        );
        let path = receiver.path.clone();
        drop(receiver);
        assert!(!path.exists());
        assert!(
            send(
                dir.path(),
                std::process::id(),
                start(std::process::id()).unwrap(),
                focus,
                Action::Pause
            )
            .is_err(),
            "app exit has no terminal fallback"
        );
    }
    #[test]
    fn endpoint_cleanup_cannot_remove_a_replacement_and_public_runtime_is_rejected() {
        let dir = tempfile::tempdir().unwrap();
        let receiver = Receiver::bind(dir.path()).unwrap();
        let path = receiver.path.clone();
        std::fs::remove_file(&path).unwrap();
        let replacement = UnixDatagram::bind(&path).unwrap();
        drop(receiver);
        assert!(path.exists());
        drop(replacement);
        std::fs::set_permissions(dir.path(), std::fs::Permissions::from_mode(0o755)).unwrap();
        assert!(Receiver::bind(dir.path()).is_err());
    }
    #[test]
    fn bridge_scope_leases_support_nested_and_local_routes_without_global_manifest_authority() {
        let runtime = tempfile::tempdir().unwrap();
        let mut writer = Publisher::new(runtime.path()).unwrap();
        let mut packet = packet();
        packet.source_start = writer.start;
        packet.created_ns = monotonic_ns();
        writer
            .update("seat", Some(packet.focus.clone()), packet.created_ns, 1000)
            .unwrap();
        let mut route = packet.focus.clone();
        route.channel = "route:9".into();
        route.epoch = 7;
        writer
            .update("route:9", Some(route.clone()), packet.created_ns, 1000)
            .unwrap();
        let value = authority(runtime.path(), writer.pid, writer.start).unwrap();
        assert!(
            !runtime.path().join("mios-inputd-active.json").exists(),
            "the global return manifest is independent"
        );
        assert!(authorized(
            &packet,
            writer.pid,
            &value,
            monotonic_ns(),
            None
        ));
        writer
            .update("seat", None, packet.created_ns, 1000)
            .unwrap();
        let value = authority(runtime.path(), writer.pid, writer.start).unwrap();
        assert!(!authorized(
            &packet,
            writer.pid,
            &value,
            monotonic_ns(),
            None
        ));
        packet.focus = route;
        assert!(authorized(
            &packet,
            writer.pid,
            &value,
            monotonic_ns(),
            None
        ));
        let path = writer.path.clone();
        drop(writer);
        assert!(!path.exists());
    }
    #[test]
    fn private_fifo_authority_is_refused_without_blocking_the_ui_reader() {
        use std::ffi::CString;
        use std::os::unix::ffi::OsStrExt;
        let runtime = tempfile::tempdir().unwrap();
        let path = runtime.path().join("fifo");
        let name = CString::new(path.as_os_str().as_bytes()).unwrap();
        assert_eq!(unsafe { libc::mkfifo(name.as_ptr(), 0o600) }, 0);
        let (send, receive) = std::sync::mpsc::channel();
        std::thread::spawn(move || {
            let _ = send.send(private_file(&path).is_err());
        });
        assert!(receive
            .recv_timeout(std::time::Duration::from_millis(200))
            .unwrap());
    }
    #[test]
    fn publisher_cleanup_preserves_replacements_and_failed_refresh_revokes_its_old_lease() {
        let runtime = tempfile::tempdir().unwrap();
        let mut writer = Publisher::new(runtime.path()).unwrap();
        writer
            .update("seat", Some(packet().focus), monotonic_ns(), 1000)
            .unwrap();
        let path = writer.path.clone();
        std::fs::remove_file(&path).unwrap();
        std::fs::write(&path, "replacement").unwrap();
        drop(writer);
        assert_eq!(std::fs::read_to_string(&path).unwrap(), "replacement");
        std::fs::remove_file(&path).unwrap();
        let mut writer = Publisher::new(runtime.path()).unwrap();
        writer
            .update("seat", Some(packet().focus), monotonic_ns(), 1000)
            .unwrap();
        let conflict = writer
            .path
            .with_extension(format!("{}.tmp", writer.sequence + 1));
        std::fs::write(&conflict, "reserved").unwrap();
        // An occupied staging pathname must not retain fresh authority.
        assert!(writer.update("seat", None, monotonic_ns(), 1000).is_err());
        assert!(!writer.path.exists());
        assert_eq!(std::fs::read_to_string(conflict).unwrap(), "reserved");
    }
}
