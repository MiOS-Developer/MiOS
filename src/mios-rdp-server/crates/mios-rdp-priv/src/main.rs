//! mios-rdp-priv: the one root component of the native RDP server (rdp-native-design.md 10/11).
//! Socket-activated per connection (mios-rdp-priv.socket, Accept=yes, SOCK_SEQPACKET): the peer must be
//! the operator uid ([remote.rdp].operator_uid). Requests (NUL-separated fields, one per message):
//!   AUTH user password peer -> "OK" | "DENY <reason>"   PAM service "mios-rdp" (password-auth, so
//!                                pam_faillock applies) + membership of group "mios-rdp"; uid 0 never
//!   TLS-KEY                 -> "OK" + SCM_RIGHTS [cert.pem fd, key.pem fd] from [remote.rdp].cert_dir
//!   FALLBACK reason         -> writes /run/mios/rdp/fallback and starts xrdp.service (no-block)
//! The password is zeroised after use; every AUTH is logged with user, peer and result.
//!
//! Exception E2 of the static-binary policy: glibc + libpam (PAM modules are dlopen'd, NSS by design).
//! Test mode (only when run by hand, never from the socket unit): `mios-rdp-priv --test-auth
//! --service S --group G` reads "user\0password" on stdin and prints the decision.

use std::ffi::{CStr, CString};
use std::io::Read as _;
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};
use std::os::raw::{c_char, c_int, c_void};
use std::time::Duration;

const PAM_SUCCESS: c_int = 0;
const PAM_PROMPT_ECHO_OFF: c_int = 1;
const PAM_PROMPT_ECHO_ON: c_int = 2;
const PAM_RHOST: c_int = 4;
const PAM_SILENT: c_int = 0x8000;
const PAM_DISALLOW_NULL_AUTHTOK: c_int = 0x0001;

#[repr(C)]
struct PamMessage {
    msg_style: c_int,
    msg: *const c_char,
}

#[repr(C)]
struct PamResponse {
    resp: *mut c_char,
    resp_retcode: c_int,
}

#[repr(C)]
struct PamConv {
    conv: Option<unsafe extern "C" fn(c_int, *mut *const PamMessage, *mut *mut PamResponse, *mut c_void) -> c_int>,
    appdata_ptr: *mut c_void,
}

#[link(name = "pam")]
unsafe extern "C" {
    fn pam_start(service: *const c_char, user: *const c_char, conv: *const PamConv, pamh: *mut *mut c_void) -> c_int;
    fn pam_authenticate(pamh: *mut c_void, flags: c_int) -> c_int;
    fn pam_acct_mgmt(pamh: *mut c_void, flags: c_int) -> c_int;
    fn pam_set_item(pamh: *mut c_void, item_type: c_int, item: *const c_void) -> c_int;
    fn pam_end(pamh: *mut c_void, status: c_int) -> c_int;
    fn pam_strerror(pamh: *mut c_void, errnum: c_int) -> *const c_char;
}

/// Conversation: answer every echo-off prompt with the password, echo-on with nothing.
unsafe extern "C" fn conv(n: c_int, msgs: *mut *const PamMessage, resp: *mut *mut PamResponse, data: *mut c_void) -> c_int {
    if n <= 0 || n > 32 {
        return 19; // PAM_CONV_ERR
    }
    // SAFETY: PAM passes n messages and expects n responses allocated with calloc (it frees them).
    unsafe {
        let out = libc::calloc(n as usize, size_of::<PamResponse>()).cast::<PamResponse>();
        if out.is_null() {
            return 5; // PAM_BUF_ERR
        }
        let pw = &*(data.cast::<CString>());
        for i in 0..n as usize {
            let m = *msgs.add(i);
            let style = (*m).msg_style;
            if style == PAM_PROMPT_ECHO_OFF {
                (*out.add(i)).resp = libc::strdup(pw.as_ptr());
            } else if style == PAM_PROMPT_ECHO_ON {
                (*out.add(i)).resp = libc::strdup(c"".as_ptr());
            }
        }
        *resp = out;
    }
    PAM_SUCCESS
}

fn log(msg: &str) {
    eprintln!("mios-rdp-priv: {msg}");
}

fn group_members(group: &str) -> Option<(u32, Vec<String>)> {
    let g = CString::new(group).ok()?;
    // SAFETY: getgrnam returns static storage; copied out immediately (single-threaded process).
    unsafe {
        let gr = libc::getgrnam(g.as_ptr());
        if gr.is_null() {
            return None;
        }
        let mut members = Vec::new();
        let mut p = (*gr).gr_mem;
        while !p.is_null() && !(*p).is_null() {
            members.push(CStr::from_ptr(*p).to_string_lossy().into_owned());
            p = p.add(1);
        }
        Some(((*gr).gr_gid, members))
    }
}

/// (uid, primary gid) of a user.
fn user_ids(user: &str) -> Option<(u32, u32)> {
    let u = CString::new(user).ok()?;
    // SAFETY: getpwnam static storage, copied out immediately.
    unsafe {
        let pw = libc::getpwnam(u.as_ptr());
        if pw.is_null() {
            return None;
        }
        Some(((*pw).pw_uid, (*pw).pw_gid))
    }
}

/// Group check: the user is a member of `group` (supplementary list or primary gid).
pub fn in_group(user: &str, group: &str) -> bool {
    let Some((gid, members)) = group_members(group) else { return false };
    if members.iter().any(|m| m == user) {
        return true;
    }
    user_ids(user).is_some_and(|(_, pg)| pg == gid)
}

fn valid_user(user: &str) -> bool {
    !user.is_empty()
        && user.len() <= 32
        && user.bytes().next().is_some_and(|c| c.is_ascii_lowercase() || c == b'_')
        && user.bytes().all(|c| c.is_ascii_lowercase() || c.is_ascii_digit() || matches!(c, b'_' | b'.' | b'-'))
}

/// The authentication decision. `pam` is injectable for unit tests.
pub fn decide(user: &str, password: &[u8], group: &str, pam: &dyn Fn(&str, &[u8]) -> Result<(), String>) -> Result<(), String> {
    if !valid_user(user) {
        return Err("invalid user name".into());
    }
    if password.is_empty() {
        return Err("empty password".into());
    }
    let Some((uid, _)) = user_ids(user) else { return Err("no such user".into()) };
    if uid == 0 {
        return Err("root is never admitted".into());
    }
    if !in_group(user, group) {
        return Err(format!("not a member of {group}"));
    }
    pam(user, password)
}

fn pam_check(service: &str, user: &str, password: &[u8], rhost: &str) -> Result<(), String> {
    let svc = CString::new(service).map_err(|_| "bad service")?;
    let u = CString::new(user).map_err(|_| "bad user")?;
    let pw = CString::new(password.to_vec()).map_err(|_| "password contains NUL")?;
    let rh = CString::new(rhost).unwrap_or_default();
    let pwbox = Box::new(pw);
    let c = PamConv { conv: Some(conv), appdata_ptr: (&raw const *pwbox).cast_mut().cast() };
    let mut h: *mut c_void = std::ptr::null_mut();
    // SAFETY: standard PAM transaction; pwbox outlives pam_end.
    let res = unsafe {
        let r = pam_start(svc.as_ptr(), u.as_ptr(), &c, &mut h);
        if r != PAM_SUCCESS {
            return Err(format!("pam_start {r}"));
        }
        pam_set_item(h, PAM_RHOST, rh.as_ptr().cast());
        let mut r = pam_authenticate(h, PAM_SILENT | PAM_DISALLOW_NULL_AUTHTOK);
        if r == PAM_SUCCESS {
            r = pam_acct_mgmt(h, PAM_SILENT | PAM_DISALLOW_NULL_AUTHTOK);
        }
        let msg = if r == PAM_SUCCESS {
            Ok(())
        } else {
            let e = pam_strerror(h, r);
            Err(if e.is_null() { format!("PAM error {r}") } else { CStr::from_ptr(e).to_string_lossy().into_owned() })
        };
        pam_end(h, r);
        msg
    };
    // zeroise the password copy
    let mut bytes = (*pwbox).into_bytes();
    bytes.iter_mut().for_each(|b| *b = 0);
    std::hint::black_box(&bytes);
    res
}

fn peer_cred(fd: RawFd) -> Option<libc::ucred> {
    let mut cred = libc::ucred { pid: 0, uid: u32::MAX, gid: 0 };
    let mut len = size_of::<libc::ucred>() as libc::socklen_t;
    // SAFETY: SO_PEERCRED into a local ucred.
    let r = unsafe { libc::getsockopt(fd, libc::SOL_SOCKET, libc::SO_PEERCRED, (&raw mut cred).cast(), &mut len) };
    (r == 0).then_some(cred)
}

fn send(fd: RawFd, msg: &[u8], fds: &[RawFd]) {
    // SAFETY: sendmsg with an optional SCM_RIGHTS control message built in a local buffer.
    unsafe {
        let mut iov = libc::iovec { iov_base: msg.as_ptr() as *mut c_void, iov_len: msg.len() };
        let mut mh: libc::msghdr = std::mem::zeroed();
        mh.msg_iov = &mut iov;
        mh.msg_iovlen = 1;
        let space = libc::CMSG_SPACE((fds.len() * size_of::<RawFd>()) as u32) as usize;
        let mut cbuf = vec![0u8; space.max(1)];
        if !fds.is_empty() {
            mh.msg_control = cbuf.as_mut_ptr().cast();
            mh.msg_controllen = space;
            let c = libc::CMSG_FIRSTHDR(&mh);
            (*c).cmsg_level = libc::SOL_SOCKET;
            (*c).cmsg_type = libc::SCM_RIGHTS;
            (*c).cmsg_len = libc::CMSG_LEN((fds.len() * size_of::<RawFd>()) as u32) as usize;
            std::ptr::copy_nonoverlapping(fds.as_ptr(), libc::CMSG_DATA(c).cast::<RawFd>(), fds.len());
        }
        libc::sendmsg(fd, &mh, libc::MSG_NOSIGNAL);
    }
}

fn toml_get(section: &str, key: &str, default: &str) -> String {
    let out = std::process::Command::new("/usr/libexec/mios/mios-toml-get").args([section, key, default]).output();
    match out {
        Ok(o) if o.status.success() => {
            let s = String::from_utf8_lossy(&o.stdout).trim().to_owned();
            if s.is_empty() { default.to_owned() } else { s }
        }
        _ => default.to_owned(),
    }
}

fn serve(fd: RawFd) -> i32 {
    let op_uid: u32 = toml_get("remote.rdp", "operator_uid", "1000").parse().unwrap_or(1000);
    let cert_dir = toml_get("remote.rdp", "cert_dir", "/var/lib/mios-rdp");
    let Some(cred) = peer_cred(fd) else {
        log("no peer credentials; closed");
        return 1;
    };
    let uid = cred.uid;
    if uid != op_uid {
        log(&format!("peer uid {uid} is not the operator ({op_uid}); closed"));
        send(fd, b"DENY peer", &[]);
        return 1;
    }
    let tv = libc::timeval { tv_sec: 5, tv_usec: 0 };
    // SAFETY: SO_RCVTIMEO on our socket.
    unsafe {
        libc::setsockopt(fd, libc::SOL_SOCKET, libc::SO_RCVTIMEO, (&raw const tv).cast(), size_of::<libc::timeval>() as u32);
    }
    let mut buf = vec![0u8; 4096];
    // SAFETY: recv into a local buffer.
    let n = unsafe { libc::recv(fd, buf.as_mut_ptr().cast(), buf.len(), 0) };
    if n <= 0 {
        return 1;
    }
    buf.truncate(n as usize);
    let mut parts = buf.split(|b| *b == 0);
    let op = parts.next().unwrap_or_default().to_vec();
    let code = match op.as_slice() {
        b"AUTH" => {
            let user = String::from_utf8_lossy(parts.next().unwrap_or_default()).into_owned();
            let mut pw = parts.next().unwrap_or_default().to_vec();
            let peer = String::from_utf8_lossy(parts.next().unwrap_or_default()).into_owned();
            let r = decide(&user, &pw, "mios-rdp", &|u, p| pam_check("mios-rdp", u, p, &peer));
            pw.iter_mut().for_each(|b| *b = 0);
            match r {
                Ok(()) => {
                    log(&format!("AUTH user={user} peer={peer}: OK"));
                    send(fd, b"OK", &[]);
                    0
                }
                Err(why) => {
                    log(&format!("AUTH user={user} peer={peer}: DENY ({why})"));
                    send(fd, format!("DENY {why}").as_bytes(), &[]);
                    0
                }
            }
        }
        b"TLS-KEY" => {
            let pid = cred.pid;
            let expected = std::env::var_os("MIOS_RDP_PRIV_ALLOW_EXE")
                .map(std::path::PathBuf::from)
                .unwrap_or_else(|| std::path::PathBuf::from("/usr/libexec/mios/rdp-native/mios-rdp-server"));
            if let Err(why) = verify_tls_key_caller(pid, &expected) {
                log(&format!("TLS-KEY: unauthorized caller pid {pid} ({why}); DENIED"));
                send(fd, b"DENY unauthorized caller exe", &[]);
                return 1;
            }
            if !std::path::Path::new(&format!("{cert_dir}/key.pem")).exists() {
                // first start with server = "native" and xrdp never rendered: create the host certificate
                let st = std::process::Command::new("/usr/libexec/mios/mios-rdp").arg("cert").status();
                log(&format!("TLS-KEY: no certificate yet; mios-rdp cert: {st:?}"));
            }
            let c = std::fs::File::open(format!("{cert_dir}/cert.pem"));
            let k = std::fs::File::open(format!("{cert_dir}/key.pem"));
            match (c, k) {
                (Ok(c), Ok(k)) => {
                    send(fd, b"OK", &[c.as_raw_fd(), k.as_raw_fd()]);
                    log("TLS-KEY: certificate and key descriptors handed over");
                    0
                }
                _ => {
                    send(fd, b"DENY no certificate (start xrdp once or run mios-rdp cert)", &[]);
                    1
                }
            }
        }
        b"FALLBACK" => {
            let why = String::from_utf8_lossy(parts.next().unwrap_or_default()).into_owned();
            let _ = std::fs::create_dir_all("/run/mios/rdp");
            let tmp = "/run/mios/rdp/.fallback.tmp";
            let ok = std::fs::write(tmp, format!("{why}\n")).and_then(|()| std::fs::rename(tmp, "/run/mios/rdp/fallback")).is_ok();
            let st = std::process::Command::new("systemctl").args(["start", "--no-block", "xrdp.service", "xrdp-sesman.service"]).status();
            log(&format!("FALLBACK ({why}): marker {ok}, xrdp start {st:?}"));
            send(fd, if ok { b"OK" } else { b"DENY marker" }, &[]);
            0
        }
        _ => {
            send(fd, b"DENY unknown op", &[]);
            1
        }
    };
    code
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    if args.iter().any(|a| a == "--test-auth") {
        // manual test path: never reachable through the socket unit (which passes no arguments)
        let get = |k: &str| args.iter().position(|a| a == k).and_then(|i| args.get(i + 1)).cloned();
        let service = get("--service").unwrap_or_else(|| "mios-rdp".into());
        let group = get("--group").unwrap_or_else(|| "mios-rdp".into());
        let mut input = Vec::new();
        let _ = std::io::stdin().read_to_end(&mut input);
        let mut it = input.splitn(2, |b| *b == 0);
        let user = String::from_utf8_lossy(it.next().unwrap_or_default()).into_owned();
        let pw = it.next().unwrap_or_default().to_vec();
        let r = decide(&user, &pw, &group, &|u, p| pam_check(&service, u, p, "test"));
        println!("{}", match r {
            Ok(()) => "OK".to_owned(),
            Err(e) => format!("DENY {e}"),
        });
        std::process::exit(0);
    }
    // systemd socket activation, Accept=yes: connection is fd 3 (or fd 0 if StandardInput=socket)
    let fds: i32 = std::env::var("LISTEN_FDS").ok().and_then(|v| v.parse().ok()).unwrap_or(0);
    let sock = if fds >= 1 {
        unsafe { OwnedFd::from_raw_fd(3) }
    } else {
        let mut stat: libc::stat = unsafe { std::mem::zeroed() };
        if unsafe { libc::fstat(libc::STDIN_FILENO, &mut stat) } == 0
            && (stat.st_mode & libc::S_IFMT) == libc::S_IFSOCK
        {
            unsafe { OwnedFd::from_raw_fd(0) }
        } else {
            log("not socket-activated (LISTEN_FDS missing and stdin is not a socket); refusing to run");
            std::process::exit(2);
        }
    };
    std::thread::spawn(|| {
        std::thread::sleep(Duration::from_secs(15));
        log("watchdog: request took longer than 15 s");
        std::process::exit(3);
    });
    let code = serve(sock.as_raw_fd());
    std::process::exit(code);
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ok_pam(_: &str, _: &[u8]) -> Result<(), String> {
        Ok(())
    }

    #[test]
    fn root_and_bad_names_are_refused_before_pam() {
        assert_eq!(decide("root", b"x", "root", &ok_pam), Err("root is never admitted".into()));
        assert!(decide("Bad;Name", b"x", "mios-rdp", &ok_pam).is_err());
        assert!(decide("", b"x", "mios-rdp", &ok_pam).is_err());
        assert_eq!(decide("nobody", b"", "mios-rdp", &ok_pam), Err("empty password".into()));
    }

    #[test]
    fn group_membership_is_required() {
        // nobody exists everywhere and is not in group "root"
        assert_eq!(decide("nobody", b"x", "root", &ok_pam), Err("not a member of root".into()));
        assert!(decide("no-such-user-mios", b"x", "root", &ok_pam).is_err());
    }

    #[test]
    fn pam_result_is_final() {
        // a member of its own primary group passes the group check; PAM decides
        let deny = |_: &str, _: &[u8]| Err::<(), String>("Authentication failure".into());
        let g = group_members("nobody").map(|_| "nobody").unwrap_or("nogroup");
        if user_ids("nobody").is_some() && group_members(g).is_some() && in_group("nobody", g) {
            assert_eq!(decide("nobody", b"pw", g, &deny), Err("Authentication failure".into()));
            assert_eq!(decide("nobody", b"pw", g, &ok_pam), Ok(()));
        }
    }

    #[test]
    fn caller_binary_check_rejects_unauthorized() {
        let pid = std::process::id() as i32;
        let expected = std::path::Path::new("/usr/libexec/mios/rdp-native/mios-rdp-server");
        assert!(
            verify_tls_key_caller(pid, expected).is_err(),
            "test binary must be rejected by verify_tls_key_caller"
        );
    }

    #[test]
    fn status_and_environ_checks_reject_ptrace_and_ld_preload() {
        assert!(check_status_and_environ("Name:\tmios-rdp-server\nTracerPid:\t0\n", b"PATH=/usr/bin\0USER=mios\0").is_ok());
        assert!(check_status_and_environ("Name:\tmios-rdp-server\nTracerPid:\t1234\n", b"PATH=/usr/bin\0").is_err());
        assert!(check_status_and_environ("Name:\tmios-rdp-server\nTracerPid:\t0\n", b"LD_PRELOAD=/tmp/evil.so\0").is_err());
        assert!(check_status_and_environ("Name:\tmios-rdp-server\nTracerPid:\t0\n", b"LD_AUDIT=/tmp/evil.so\0").is_err());
        assert!(check_status_and_environ("Name:\tmios-rdp-server\nTracerPid:\t0\n", b"LD_LIBRARY_PATH=/tmp\0").is_err());
    }
}

pub fn check_status_and_environ(status: &str, environ: &[u8]) -> Result<(), String> {
    let tracer = status
        .lines()
        .find_map(|l| l.strip_prefix("TracerPid:"))
        .map(str::trim)
        .ok_or_else(|| "missing TracerPid in /proc/pid/status".to_owned())?;
    if tracer != "0" {
        return Err(format!("caller is ptrace-attached (TracerPid={tracer})"));
    }
    for entry in environ.split(|b| *b == 0) {
        if entry.starts_with(b"LD_PRELOAD=")
            || entry.starts_with(b"LD_AUDIT=")
            || entry.starts_with(b"LD_LIBRARY_PATH=")
        {
            return Err("caller has dangerous LD_* environment variable".into());
        }
    }
    Ok(())
}

pub fn verify_tls_key_caller(pid: i32, expected_exe: &std::path::Path) -> Result<(), String> {
    use std::os::unix::fs::MetadataExt as _;
    if pid <= 1 {
        return Err("invalid pid".into());
    }
    let exe_path = std::fs::read_link(format!("/proc/{pid}/exe"))
        .map_err(|e| format!("readlink /proc/{pid}/exe: {e}"))?;
    let exe_matches = if exe_path == expected_exe {
        true
    } else {
        #[cfg(feature = "test-auth")]
        {
            std::fs::metadata("/run/mios/rdp/test-auth-allow").is_ok_and(|m| m.uid() == 0)
        }
        #[cfg(not(feature = "test-auth"))]
        {
            false
        }
    };
    if !exe_matches {
        return Err(format!("exe {:?} != {:?}", exe_path, expected_exe));
    }
    let meta = std::fs::metadata(&exe_path).map_err(|e| format!("stat {:?}: {e}", exe_path))?;
    if meta.uid() != 0 || meta.gid() != 0 {
        return Err(format!("exe {:?} not owned by root:root ({}:{})", exe_path, meta.uid(), meta.gid()));
    }
    if (meta.mode() & 0o022) != 0 {
        return Err(format!("exe {:?} is group/world writable ({:#o})", exe_path, meta.mode()));
    }
    let status = std::fs::read_to_string(format!("/proc/{pid}/status"))
        .map_err(|e| format!("read /proc/{pid}/status: {e}"))?;
    let environ = std::fs::read(format!("/proc/{pid}/environ"))
        .map_err(|e| format!("read /proc/{pid}/environ: {e}"))?;
    check_status_and_environ(&status, &environ)
}
