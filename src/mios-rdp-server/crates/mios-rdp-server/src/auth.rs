//! Authentication (rdp-native-design.md 11.3): Client Info credentials are checked by the root
//! helper mios-rdp-priv over a SOCK_SEQPACKET socket (PAM service "mios-rdp" + membership of the
//! managed group "mios-rdp", uid 0 never admitted). Per-source exponential backoff sits on top of
//! pam_faillock. The helper also hands over the TLS certificate and key as file descriptors, so the
//! key never becomes readable by the operator account.
//!
//! Unsafe: AF_UNIX SOCK_SEQPACKET + SCM_RIGHTS FFI only.
#![allow(unsafe_code)]

use std::collections::HashMap;
use std::fs::File;
use std::io::Read as _;
use std::net::IpAddr;
use std::os::fd::{FromRawFd, OwnedFd, RawFd};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use anyhow::{Result, anyhow, bail};
use ironrdp_server::{CredentialDecision, CredentialValidationError, CredentialValidator, Credentials};
use tracing::{info, warn};

/// The peer of the connection being negotiated (set by the accept loop).
pub type CurrentPeer = Arc<Mutex<Option<IpAddr>>>;

fn seqpacket_connect(path: &Path) -> Result<OwnedFd> {
    // SAFETY: plain socket/connect with a sockaddr_un built from the path bytes.
    unsafe {
        let fd = libc::socket(libc::AF_UNIX, libc::SOCK_SEQPACKET | libc::SOCK_CLOEXEC, 0);
        if fd < 0 {
            bail!("socket: {}", std::io::Error::last_os_error());
        }
        let owned = OwnedFd::from_raw_fd(fd);
        let mut addr: libc::sockaddr_un = std::mem::zeroed();
        addr.sun_family = libc::AF_UNIX as libc::sa_family_t;
        let p = path.as_os_str().as_encoded_bytes();
        if p.len() >= addr.sun_path.len() {
            bail!("socket path too long");
        }
        for (i, b) in p.iter().enumerate() {
            addr.sun_path[i] = *b as libc::c_char;
        }
        let len = (std::mem::size_of::<libc::sa_family_t>() + p.len() + 1) as libc::socklen_t;
        if libc::connect(fd, (&raw const addr).cast(), len) < 0 {
            bail!("connect {}: {}", path.display(), std::io::Error::last_os_error());
        }
        let tv = libc::timeval { tv_sec: 10, tv_usec: 0 };
        libc::setsockopt(fd, libc::SOL_SOCKET, libc::SO_RCVTIMEO, (&raw const tv).cast(), size_of::<libc::timeval>() as u32);
        Ok(owned)
    }
}

fn send(fd: RawFd, msg: &[u8]) -> Result<()> {
    // SAFETY: send on a connected SEQPACKET socket.
    let n = unsafe { libc::send(fd, msg.as_ptr().cast(), msg.len(), libc::MSG_NOSIGNAL) };
    if n < 0 || n as usize != msg.len() {
        bail!("send to mios-rdp-priv: {}", std::io::Error::last_os_error());
    }
    Ok(())
}

/// One reply message plus any passed descriptors.
fn recv(fd: RawFd) -> Result<(Vec<u8>, Vec<OwnedFd>)> {
    let mut buf = vec![0u8; 4096];
    let mut cbuf = vec![0u8; 256];
    // SAFETY: recvmsg with buffers owned here; SCM_RIGHTS descriptors are taken over as OwnedFd.
    unsafe {
        let mut iov = libc::iovec { iov_base: buf.as_mut_ptr().cast(), iov_len: buf.len() };
        let mut mh: libc::msghdr = std::mem::zeroed();
        mh.msg_iov = &mut iov;
        mh.msg_iovlen = 1;
        mh.msg_control = cbuf.as_mut_ptr().cast();
        mh.msg_controllen = cbuf.len();
        let n = libc::recvmsg(fd, &mut mh, libc::MSG_CMSG_CLOEXEC);
        if n < 0 {
            bail!("recv from mios-rdp-priv: {}", std::io::Error::last_os_error());
        }
        if n == 0 {
            bail!("mios-rdp-priv closed the connection");
        }
        let mut fds = Vec::new();
        let mut c = libc::CMSG_FIRSTHDR(&mh);
        while !c.is_null() {
            if (*c).cmsg_level == libc::SOL_SOCKET && (*c).cmsg_type == libc::SCM_RIGHTS {
                let data = libc::CMSG_DATA(c).cast::<RawFd>();
                let count = ((*c).cmsg_len as usize - libc::CMSG_LEN(0) as usize) / size_of::<RawFd>();
                for i in 0..count {
                    fds.push(OwnedFd::from_raw_fd(*data.add(i)));
                }
            }
            c = libc::CMSG_NXTHDR(&mh, c);
        }
        buf.truncate(n as usize);
        Ok((buf, fds))
    }
}

/// AUTH user password peer -> Ok(true) admitted, Ok(false) refused (reason logged by the helper).
pub fn priv_auth(socket: &Path, user: &str, password: &mut String, peer: &str) -> Result<(bool, String)> {
    let fd = seqpacket_connect(socket)?;
    let mut msg = Vec::with_capacity(16 + user.len() + password.len() + peer.len());
    msg.extend_from_slice(b"AUTH\0");
    msg.extend_from_slice(user.as_bytes());
    msg.push(0);
    msg.extend_from_slice(password.as_bytes());
    msg.push(0);
    msg.extend_from_slice(peer.as_bytes());
    let r = send(std::os::fd::AsRawFd::as_raw_fd(&fd), &msg);
    msg.iter_mut().for_each(|b| *b = 0);
    // SAFETY: overwrite the password bytes in place (same length, valid UTF-8 NULs).
    unsafe { password.as_bytes_mut().iter_mut().for_each(|b| *b = 0) };
    r?;
    let (reply, _) = recv(std::os::fd::AsRawFd::as_raw_fd(&fd))?;
    let text = String::from_utf8_lossy(&reply).into_owned();
    Ok((text == "OK", text))
}

struct DumpableGuard;
impl DumpableGuard {
    fn enter() -> Self {
        // SAFETY: mark process non-dumpable while receiving and reading the private TLS key fd.
        unsafe {
            libc::prctl(libc::PR_SET_DUMPABLE, 0, 0, 0, 0);
        }
        Self
    }
}
impl Drop for DumpableGuard {
    fn drop(&mut self) {
        if std::env::var_os("MIOS_RDP_KEEP_NONDUMPABLE").is_none() {
            // SAFETY: restore dumpable flag after key fds are closed.
            unsafe {
                libc::prctl(libc::PR_SET_DUMPABLE, 1, 0, 0, 0);
            }
        }
    }
}

/// TLS-KEY -> (certificate PEM, key PEM), read from the descriptors the helper passed.
pub fn priv_tls(socket: &Path) -> Result<(Vec<u8>, Vec<u8>)> {
    let _nondumpable = DumpableGuard::enter();
    let fd = seqpacket_connect(socket)?;
    send(std::os::fd::AsRawFd::as_raw_fd(&fd), b"TLS-KEY")?;
    let (reply, fds) = recv(std::os::fd::AsRawFd::as_raw_fd(&fd))?;
    if reply != b"OK" || fds.len() != 2 {
        bail!("mios-rdp-priv TLS-KEY: {}", String::from_utf8_lossy(&reply));
    }
    let mut it = fds.into_iter();
    let mut cert = Vec::new();
    let mut key = Vec::new();
    File::from(it.next().expect("cert fd")).read_to_end(&mut cert)?;
    File::from(it.next().expect("key fd")).read_to_end(&mut key)?;
    Ok((cert, key))
}

/// Re-verify on auto-reconnect that `username` exists, has `uid >= 1000`, and is a member of `group_name`.
pub fn check_user_authorized(username: &str, group_name: &str) -> bool {
    let Ok(c_user) = std::ffi::CString::new(username) else {
        return false;
    };
    let Ok(c_group) = std::ffi::CString::new(group_name) else {
        return false;
    };
    // SAFETY: NSS getpwnam / getgrnam lookups; results are inspected before returning.
    unsafe {
        let pwd = libc::getpwnam(c_user.as_ptr());
        if pwd.is_null() {
            return false;
        }
        let (uid, gid) = ((*pwd).pw_uid, (*pwd).pw_gid);
        if uid < 1000 {
            return false;
        }
        let grp = libc::getgrnam(c_group.as_ptr());
        if grp.is_null() {
            return false;
        }
        if gid == (*grp).gr_gid {
            return true;
        }
        let mut gr_mem = (*grp).gr_mem;
        while !gr_mem.is_null() && !(*gr_mem).is_null() {
            let member = std::ffi::CStr::from_ptr(*gr_mem);
            if member.to_bytes() == username.as_bytes() {
                return true;
            }
            gr_mem = gr_mem.add(1);
        }
    }
    false
}

/// Single-use ServerAutoReconnect cookie tracker: a cookie is valid for at most one reconnect
/// attempt before being rotated and invalidated (never retained in a previous-cookie slot).
#[allow(dead_code)]
pub struct SingleUseCookieJar {
    current: Option<ironrdp_pdu::rdp::session_info::ServerAutoReconnect>,
}

#[allow(dead_code)]
impl SingleUseCookieJar {
    pub fn new(initial: Option<ironrdp_pdu::rdp::session_info::ServerAutoReconnect>) -> Self {
        Self { current: initial }
    }

    pub fn current(&self) -> Option<&ironrdp_pdu::rdp::session_info::ServerAutoReconnect> {
        self.current.as_ref()
    }

    pub fn rotate(&mut self, next: ironrdp_pdu::rdp::session_info::ServerAutoReconnect) {
        self.current = Some(next);
    }

    pub fn clear(&mut self) {
        self.current = None;
    }

    /// Consume the current cookie if `client` verifies against it, immediately rotating to `next`.
    /// A second presentation of the same `client` packet always fails.
    pub fn verify_and_consume(
        &mut self,
        client: &ironrdp_pdu::rdp::client_info::ClientAutoReconnect,
        next: ironrdp_pdu::rdp::session_info::ServerAutoReconnect,
    ) -> bool {
        let matched = self.current.as_ref().is_some_and(|c| client.verify(c));
        if matched {
            self.current = Some(next);
        }
        matched
    }
}

#[derive(Debug)]
struct BackendError(String);
impl std::fmt::Display for BackendError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for BackendError {}

pub enum Backend {
    Priv(PathBuf),
    #[cfg(feature = "test-auth")]
    Test { user: String, password: String },
}

pub struct Validator {
    pub backend: Backend,
    pub peer: CurrentPeer,
    pub base_backoff: Duration,
    failures: Mutex<HashMap<IpAddr, (u32, Instant)>>,
    pub last_user: Arc<Mutex<Option<String>>>,
}

impl Validator {
    pub fn new(backend: Backend, peer: CurrentPeer, base_backoff: Duration) -> Self {
        Self { backend, peer, base_backoff, failures: Mutex::default(), last_user: Arc::default() }
    }

    fn backoff_left(&self, ip: IpAddr) -> Option<Duration> {
        let f = self.failures.lock().expect("failures");
        let (n, at) = f.get(&ip)?;
        let wait = (self.base_backoff * 2u32.saturating_pow(n.saturating_sub(1))).min(Duration::from_secs(60));
        let el = at.elapsed();
        (el < wait).then(|| wait - el)
    }

    fn record(&self, ip: Option<IpAddr>, ok: bool) {
        let Some(ip) = ip else { return };
        let mut f = self.failures.lock().expect("failures");
        if ok {
            f.remove(&ip);
        } else {
            let e = f.entry(ip).or_insert((0, Instant::now()));
            e.0 += 1;
            e.1 = Instant::now();
        }
    }
}

#[async_trait::async_trait]
impl CredentialValidator for Validator {
    async fn validate(&self, creds: &Credentials) -> Result<CredentialDecision, CredentialValidationError> {
        let ip = *self.peer.lock().expect("peer");
        let peer = ip.map_or_else(|| "?".to_owned(), |i| i.to_string());
        if creds.username.is_empty() || creds.password.is_empty() {
            warn!(%peer, "refused: empty credentials (the client must send a saved user and password; NLA is not offered)");
            self.record(ip, false);
            return Ok(CredentialDecision::Reject);
        }
        if let Some(left) = ip.and_then(|i| self.backoff_left(i)) {
            warn!(%peer, user = %creds.username, wait_ms = left.as_millis() as u64, "refused: authentication backoff");
            return Ok(CredentialDecision::Reject);
        }
        let user = creds.username.clone();
        let decision = match &self.backend {
            Backend::Priv(sock) => {
                let sock = sock.clone();
                let mut pw = creds.password.clone();
                let (u2, p2) = (user.clone(), peer.clone());
                let r = tokio::task::spawn_blocking(move || priv_auth(&sock, &u2, &mut pw, &p2))
                    .await
                    .map_err(|e| CredentialValidationError::new(BackendError(e.to_string())))?;
                match r {
                    Ok((ok, reason)) => {
                        if !ok {
                            warn!(%peer, user = %user, %reason, "refused by PAM/group check");
                        }
                        ok
                    }
                    Err(e) => {
                        warn!(%peer, user = %user, "mios-rdp-priv unavailable: {e:#}");
                        return Err(CredentialValidationError::new(BackendError(format!("{e:#}"))));
                    }
                }
            }
            #[cfg(feature = "test-auth")]
            Backend::Test { user: tu, password: tp } => {
                // constant-time compare of the per-run test secret
                let a = creds.password.as_bytes();
                let b = tp.as_bytes();
                let mut diff = (a.len() ^ b.len()) as u8;
                for i in 0..a.len().max(b.len()) {
                    diff |= a.get(i).copied().unwrap_or(0) ^ b.get(i).copied().unwrap_or(0);
                }
                creds.username == *tu && diff == 0
            }
        };
        self.record(ip, decision);
        if decision {
            info!(%peer, user = %user, "authenticated");
            *self.last_user.lock().expect("user") = Some(user);
            Ok(CredentialDecision::Accept)
        } else {
            Ok(CredentialDecision::Reject)
        }
    }
}

#[allow(dead_code)]
pub fn read_secret(path: &Path) -> Result<String> {
    let s = std::fs::read_to_string(path).map_err(|e| anyhow!("read {}: {e}", path.display()))?;
    Ok(s.trim_end_matches(['\n', '\r']).to_owned())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backoff_doubles_and_resets() {
        let v = Validator::new(Backend::Priv(PathBuf::from("/nonexistent")), Arc::default(), Duration::from_millis(200));
        let ip: IpAddr = "100.64.0.9".parse().unwrap();
        assert!(v.backoff_left(ip).is_none());
        v.record(Some(ip), false);
        assert!(v.backoff_left(ip).is_some());
        v.record(Some(ip), false);
        let left = v.backoff_left(ip).unwrap();
        assert!(left > Duration::from_millis(200) && left <= Duration::from_millis(400));
        v.record(Some(ip), true);
        assert!(v.backoff_left(ip).is_none());
    }

    #[test]
    fn single_use_cookie_jar_rejects_replay_and_rotates() {
        use ironrdp_pdu::rdp::client_info::ClientAutoReconnect;
        use ironrdp_pdu::rdp::session_info::ServerAutoReconnect;

        let c1 = ServerAutoReconnect { logon_id: 1, random_bits: [0x11; 16] };
        let c2 = ServerAutoReconnect { logon_id: 1, random_bits: [0x22; 16] };
        let c3 = ServerAutoReconnect { logon_id: 1, random_bits: [0x33; 16] };

        let client_pkt_1 = ClientAutoReconnect::from_server_cookie(&c1);
        let client_pkt_2 = ClientAutoReconnect::from_server_cookie(&c2);

        let mut jar = SingleUseCookieJar::new(Some(c1));
        assert!(jar.verify_and_consume(&client_pkt_1, c2));
        assert!(!jar.verify_and_consume(&client_pkt_1, c3.clone()));
        assert!(jar.verify_and_consume(&client_pkt_2, c3));
        assert!(!jar.verify_and_consume(&client_pkt_2, ServerAutoReconnect { logon_id: 1, random_bits: [0x44; 16] }));
    }

    #[test]
    fn check_user_authorized_enforces_uid_and_group() {
        assert!(!check_user_authorized("root", "root"));
        assert!(!check_user_authorized("root", "mios-rdp"));
        assert!(!check_user_authorized("no-such-user-mios", "mios-rdp"));
        assert!(!check_user_authorized("mios", "no-such-group-mios"));
        assert!(check_user_authorized("mios", "mios-rdp"));
    }
}
