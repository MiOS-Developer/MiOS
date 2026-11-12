// AI-hint: Isolated PTY greetd/tuigreet portal; real PAM owns authentication and session lifetime, with no physical seat or VT.
// AI-related: /usr/share/mios/templates/rust, usr/lib/pam.d/mios-remote-login, usr/share/mios/pam/remote-login.conf, usr/share/mios/mios.toml [terminal.remote_login]
// AI-functions: run, open, entry, worker, greeter, prepare_portal, proxy_request

use std::collections::BTreeMap;
use std::ffi::{CStr, CString};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::os::fd::{AsRawFd, FromRawFd};
use std::os::unix::fs::{DirBuilderExt, FileTypeExt, MetadataExt, OpenOptionsExt, PermissionsExt};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::Arc;
use std::time::{Duration, Instant};

use mios_resolver::popup::{PopupPolicy, SurfaceKind};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::{UnixListener, UnixStream};
use toml::Value;

const PHASE: &str = "@mios-remote-login-phase";

#[derive(clap::Subcommand, Debug, Clone)]
pub enum Action {
    /// Open a real remote login in a new, isolated portal, or this client's pane.
    Open {
        #[arg(long, default_value = "/")]
        root: PathBuf,
        #[arg(long)]
        client: Option<String>,
    },
    /// Internal unprivileged pane entry; no authentication authority.
    #[command(hide = true)]
    Entry {
        #[arg(long, default_value = "/")]
        root: PathBuf,
        #[arg(long)]
        client: String,
    },
    /// Narrow sudo entry. Every argument is a caller-owned terminal witness.
    #[command(hide = true)]
    Worker {
        #[arg(long)]
        socket: PathBuf,
        #[arg(long)]
        pane: String,
        #[arg(long)]
        client: String,
        #[arg(long)]
        entry_pid: u32,
        #[arg(long)]
        entry_start: u64,
    },
    /// Fixed greetd default-session command, always run as the SSH caller.
    #[command(hide = true)]
    Greeter {
        #[arg(long)]
        proxy: PathBuf,
        #[arg(long)]
        geometry: PathBuf,
    },
    /// Fixed authenticated-user PTY bridge; always runs without privilege.
    #[command(hide = true)]
    Session {
        #[arg(long)]
        geometry: PathBuf,
    },
    /// Quiet anchor for an isolated, unauthenticated portal.
    #[command(hide = true)]
    Canvas,
}

pub async fn run(action: Action) -> Result<(), String> {
    match action {
        Action::Open { root, client } => open(&root, client.as_deref()),
        Action::Entry { root, client } => entry(&root, &client).await,
        Action::Worker {
            socket,
            pane,
            client,
            entry_pid,
            entry_start,
        } => worker(socket, pane, client, entry_pid, entry_start).await,
        Action::Greeter { proxy, geometry } => greeter(&proxy, &geometry).await,
        Action::Session { geometry } => session(&geometry).await,
        Action::Canvas => canvas().await,
    }
}

#[derive(Clone)]
struct Policy {
    tmux: PathBuf,
    sudo: PathBuf,
    runuser: PathBuf,
    daemon: PathBuf,
    daemon_sha256: String,
    daemon_revision: String,
    scope_runner: PathBuf,
    greeter: PathBuf,
    executable: PathBuf,
    state: PathBuf,
    pam_service: String,
    greeter_pam_service: String,
    session: Vec<String>,
    language: String,
    min_uid: u32,
    max_uid: u32,
    command_timeout: Duration,
    startup_timeout: Duration,
    auth_timeout: Duration,
    limit: usize,
    popup: PopupPolicy,
    greeter_theme: String,
    greeter_width: u16,
    greeter_padding: [u16; 3],
    title: String,
    greeting: String,
    background: String,
    failure_return: String,
}

fn at<'a>(doc: &'a Value, key: &str) -> Result<&'a Value, String> {
    key.split('.')
        .try_fold(doc, |v, p| v.get(p))
        .ok_or_else(|| format!("remote login policy is missing {key}"))
}
fn text(doc: &Value, key: &str) -> Result<String, String> {
    at(doc, key)?
        .as_str()
        .filter(|s| !s.is_empty() && !s.chars().any(char::is_control))
        .map(str::to_owned)
        .ok_or_else(|| format!("{key} must be nonempty plain text"))
}
fn session_language(doc: &Value) -> Result<String, String> {
    let language = text(doc, "locale.language")?;
    if language.len() > 128
        || !language
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_.@-".contains(&b))
    {
        return Err("locale.language must be a bounded POSIX locale name".into());
    }
    Ok(language)
}

fn integer(doc: &Value, key: &str, min: u64, max: u64) -> Result<u64, String> {
    at(doc, key)?
        .as_integer()
        .and_then(|n| u64::try_from(n).ok())
        .filter(|n| (min..=max).contains(n))
        .ok_or_else(|| format!("invalid {key}"))
}
fn safe_name(s: &str) -> bool {
    !s.is_empty()
        && s.len() <= 128
        && !s.starts_with('-')
        && s.bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
}
fn id(s: &str, prefix: char) -> bool {
    s.starts_with(prefix) && s.len() > 1 && s[1..].bytes().all(|b| b.is_ascii_digit())
}
fn quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}
fn shell_command(args: &[String]) -> String {
    args.iter().map(|s| quote(s)).collect::<Vec<_>>().join(" ")
}

/// Policy is never read from the invoking user's layer, loader pointers or cwd.
/// Validate both the source layers and every privileged executable's ancestors.
fn trusted_document() -> Result<Value, String> {
    for (file, fragments) in [
        ("/usr/share/mios/mios.toml", "/usr/lib/mios/mios.d"),
        ("/etc/mios/mios.toml", "/etc/mios/mios.d"),
    ] {
        if Path::new(file).exists() {
            trusted(Path::new(file), false)?;
        }
        if Path::new(fragments).exists() {
            trusted_directory(Path::new(fragments))?;
            for item in fs::read_dir(fragments).map_err(|_| "cannot inspect policy fragments")? {
                let p = item.map_err(|_| "cannot inspect policy fragment")?.path();
                if p.extension().is_some_and(|e| e == "toml") {
                    trusted(&p, false)?;
                }
            }
        }
    }
    let doc = mios_resolver::resolve_projection(Path::new("/"))
        .map_err(|_| "administrator remote login policy is unreadable or invalid")?;
    Ok(doc)
}
fn remote_enabled() -> Result<bool, String> {
    at(&trusted_document()?, "terminal.remote_login.enabled")?
        .as_bool()
        .ok_or_else(|| "terminal.remote_login.enabled must be boolean".into())
}
fn policy() -> Result<Policy, String> {
    let doc = trusted_document()?;
    if at(&doc, "terminal.remote_login.enabled")?.as_bool() != Some(true) {
        return Err("remote login is disabled in the administrator policy".into());
    }
    let path = |name: &str| -> Result<PathBuf, String> {
        let p = PathBuf::from(text(&doc, &format!("terminal.remote_login.{name}"))?);
        if !p.is_absolute() {
            return Err(format!("remote login {name} must be absolute"));
        }
        Ok(p)
    };
    let session = at(&doc, "terminal.remote_login.session_command")?
        .as_array()
        .ok_or("remote login session_command must be an argument array")?
        .iter()
        .map(|v| {
            v.as_str()
                .filter(|s| !s.chars().any(char::is_control))
                .map(str::to_owned)
                .ok_or_else(|| "invalid remote login session argument".to_owned())
        })
        .collect::<Result<Vec<_>, _>>()?;
    if session.is_empty() || session.len() > 32 || session.iter().any(|s| s.len() > 4096) {
        return Err("invalid remote login session command".into());
    }
    trusted(Path::new(&session[0]), true)?;
    let pam_service = text(&doc, "terminal.remote_login.pam_service")?;
    if !safe_name(&pam_service) {
        return Err("invalid remote PAM service name".into());
    }
    // A host override must also be trusted. Never rewrite the physical greetd policy.
    let vendor_pam = PathBuf::from("/usr/lib/pam.d").join(&pam_service);
    trusted(&vendor_pam, false)?;
    let host_pam = PathBuf::from("/etc/pam.d").join(&pam_service);
    if host_pam.exists() {
        trusted(&host_pam, false)?;
    }
    let greeter_pam_service = text(&doc, "terminal.remote_login.greeter_pam_service")?;
    if !safe_name(&greeter_pam_service) || greeter_pam_service == pam_service {
        return Err("remote greeter needs its own dedicated PAM service".into());
    }
    trusted(
        &PathBuf::from("/usr/lib/pam.d").join(&greeter_pam_service),
        false,
    )?;
    let host_greeter_pam = PathBuf::from("/etc/pam.d").join(&greeter_pam_service);
    if host_greeter_pam.exists() {
        trusted(&host_greeter_pam, false)?;
    }
    let pam_environment = path("pam_environment")?;
    trusted(&pam_environment, false)?;
    let min_uid = integer(
        &doc,
        "terminal.remote_login.minimum_uid",
        1000,
        u32::MAX as u64,
    )? as u32;
    let max_uid = integer(
        &doc,
        "terminal.remote_login.maximum_uid",
        min_uid as u64,
        u32::MAX as u64,
    )? as u32;
    let p = Policy {
        tmux: PathBuf::from(text(&doc, "keybindings.tmux.desktop.binary")?),
        sudo: path("sudo")?,
        runuser: path("runuser")?,
        daemon: path("greetd")?,
        daemon_sha256: text(&doc, "terminal.remote_login.greetd_sha256")?,
        daemon_revision: text(&doc, "terminal.remote_login.greetd_source_revision")?,
        scope_runner: path("systemd_run")?,
        greeter: path("tuigreet")?,
        executable: path("worker")?,
        state: path("state_directory")?,
        pam_service,
        greeter_pam_service,
        session,
        language: session_language(&doc)?,
        min_uid,
        max_uid,
        command_timeout: Duration::from_millis(integer(
            &doc,
            "terminal.remote_login.command_timeout_ms",
            100,
            60_000,
        )?),
        startup_timeout: Duration::from_millis(integer(
            &doc,
            "terminal.remote_login.startup_timeout_ms",
            1000,
            60_000,
        )?),
        auth_timeout: Duration::from_secs(integer(
            &doc,
            "terminal.remote_login.auth_timeout_seconds",
            30,
            3600,
        )?),
        limit: integer(
            &doc,
            "terminal.remote_login.message_limit_bytes",
            4096,
            1_048_576,
        )? as usize,
        popup: PopupPolicy::from_value(&doc)?,
        greeter_theme: at(&doc, "desktop.greeter.tuigreet_theme")?
            .as_table()
            .ok_or("desktop greeter theme must be a table")?
            .iter()
            .map(|(name, value)| {
                let color = value
                    .as_str()
                    .filter(|s| {
                        !s.is_empty() && s.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'#')
                    })
                    .ok_or("invalid desktop greeter color")?;
                if !safe_name(name) {
                    return Err("invalid desktop greeter theme entry");
                }
                Ok(format!("{name}={color}"))
            })
            .collect::<Result<Vec<_>, &str>>()?
            .join(";"),
        greeter_width: integer(&doc, "desktop.greeter.width", 8, 4096)? as u16,
        greeter_padding: [
            integer(&doc, "desktop.greeter.window_padding", 0, 16)? as u16,
            integer(&doc, "desktop.greeter.container_padding", 0, 16)? as u16,
            integer(&doc, "desktop.greeter.prompt_padding", 0, 16)? as u16,
        ],
        title: text(&doc, "terminal.remote_login.labels.title")?,
        greeting: text(&doc, "terminal.remote_login.labels.greeting")?,
        background: text(&doc, "terminal.remote_login.labels.background")?,
        failure_return: text(&doc, "terminal.remote_login.labels.failure_return")?,
    };
    for bin in [
        &p.tmux,
        &p.sudo,
        &p.runuser,
        &p.daemon,
        &p.scope_runner,
        &p.greeter,
        &p.executable,
    ] {
        trusted(bin, true)?;
    }
    // Even an unprivileged caller must run the installed, reviewed worker.
    let current = fs::metadata(std::env::current_exe().map_err(|_| "cannot identify worker")?)
        .map_err(|_| "cannot identify worker")?;
    let declared = fs::metadata(&p.executable).map_err(|_| "cannot inspect installed worker")?;
    if (current.dev(), current.ino()) != (declared.dev(), declared.ino()) {
        return Err("remote login must use the installed, trusted worker".into());
    }
    Ok(p)
}

fn trusted_directory(path: &Path) -> Result<(), String> {
    // Validate the spelling as well as the resolved destination. A caller-owned
    // symlink to /usr/bin must not turn a later root exec into a TOCTOU escape.
    for component in path.ancestors() {
        let m =
            fs::symlink_metadata(component).map_err(|_| "cannot inspect trusted path component")?;
        if m.uid() != 0 || (!m.file_type().is_symlink() && (!m.is_dir() || m.mode() & 0o022 != 0)) {
            return Err("trusted path traverses a caller-writable component".into());
        }
    }
    let canonical = path
        .canonicalize()
        .map_err(|_| format!("unavailable trusted path {}", path.display()))?;
    for parent in canonical.ancestors() {
        let m = fs::symlink_metadata(parent).map_err(|_| "cannot inspect trusted path")?;
        if !m.is_dir() || m.uid() != 0 || m.mode() & 0o022 != 0 {
            return Err(format!(
                "remote login requires a root-owned, non-writable directory: {}",
                parent.display()
            ));
        }
    }
    Ok(())
}
fn trusted(path: &Path, executable: bool) -> Result<(), String> {
    if !path.is_absolute() {
        return Err("trusted paths must be absolute".into());
    }
    trusted_directory(path.parent().ok_or("trusted file has no parent")?)?;
    let original =
        fs::symlink_metadata(path).map_err(|_| "cannot inspect trusted file spelling")?;
    if original.uid() != 0 {
        return Err("trusted file spelling is caller-owned".into());
    }
    let p = path
        .canonicalize()
        .map_err(|_| format!("missing trusted file {}", path.display()))?;
    let m = fs::metadata(&p).map_err(|_| "cannot inspect trusted file")?;
    if !m.is_file()
        || m.uid() != 0
        || m.mode() & 0o022 != 0
        || (executable && m.mode() & 0o111 == 0)
    {
        return Err(format!(
            "remote login requires a trusted {}",
            path.display()
        ));
    }
    trusted_directory(p.parent().ok_or("trusted file has no parent")?)
}
fn clean_command(executable: &Path) -> Command {
    let mut cmd = Command::new(executable);
    cmd.env_clear().env("PATH", "/usr/bin:/usr/sbin");
    for name in ["TERM", "LANG"] {
        if let Ok(s) = std::env::var(name) {
            if s.len() <= 128 && !s.chars().any(char::is_control) {
                cmd.env(name, s);
            }
        }
    }
    cmd
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct BackendCapabilities {
    protocol: String,
    source_revision: String,
    pam_rhost_before_auth: bool,
    pam_rhost_before_account: bool,
    pam_rhost_before_session: bool,
    remote_required_option: bool,
    default_session_service: bool,
    debug_logging: bool,
}
fn validate_backend_capabilities(bytes: &[u8], revision: &str) -> Result<(), String> {
    let c: BackendCapabilities =
        serde_json::from_slice(bytes).map_err(|_| "invalid private remote backend capabilities")?;
    if c.protocol != "mios-greetd-remote-v1"
        || c.source_revision != revision
        || !c.pam_rhost_before_auth
        || !c.pam_rhost_before_account
        || !c.pam_rhost_before_session
        || !c.remote_required_option
        || !c.default_session_service
        || c.debug_logging
    {
        return Err("private remote backend lacks the reviewed remote PAM capability".into());
    }
    Ok(())
}
fn backend_digest(path: &Path) -> Result<String, String> {
    trusted(path, true)?;
    let canonical = path
        .canonicalize()
        .map_err(|_| "private backend disappeared")?;
    let mut file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(&canonical)
        .map_err(|_| "cannot open private remote backend")?;
    let before = file
        .metadata()
        .map_err(|_| "cannot identify private backend")?;
    if before.uid() != 0
        || !before.is_file()
        || before.mode() & 0o7777 != 0o755
        || before.len() > 64 * 1024 * 1024
    {
        return Err("private backend must be a bounded root-owned mode0755 regular file".into());
    }
    let mut hash = Sha256::new();
    let mut buffer = [0u8; 16384];
    loop {
        let n = file
            .read(&mut buffer)
            .map_err(|_| "cannot hash private backend")?;
        if n == 0 {
            break;
        }
        hash.update(&buffer[..n]);
    }
    let after = fs::metadata(&canonical).map_err(|_| "private backend disappeared")?;
    let stamp = |m: &fs::Metadata| {
        (
            m.dev(),
            m.ino(),
            m.size(),
            m.mtime(),
            m.mtime_nsec(),
            m.ctime(),
            m.ctime_nsec(),
        )
    };
    if stamp(&before) != stamp(&after) {
        return Err("private backend changed during verification".into());
    }
    Ok(format!("{:x}", hash.finalize()))
}
fn verify_backend(p: &Policy) -> Result<(), String> {
    if p.daemon_sha256.len() != 64
        || !p.daemon_sha256.bytes().all(|b| b.is_ascii_hexdigit())
        || p.daemon_revision.len() != 40
        || !p.daemon_revision.bytes().all(|b| b.is_ascii_hexdigit())
        || backend_digest(&p.daemon)? != p.daemon_sha256
    {
        return Err("private backend differs from the administrator's reviewed digest".into());
    }
    // The exact root-owned binary is hashed before its self-description is run.
    // A replacement executable cannot authorize itself with a matching JSON claim.
    let output = bounded(
        clean_command(&p.daemon).arg("--mios-remote-capabilities"),
        p,
    )?;
    validate_backend_capabilities(output.as_bytes(), &p.daemon_revision)?;
    if backend_digest(&p.daemon)? != p.daemon_sha256 {
        return Err("private backend changed during capability verification".into());
    }
    Ok(())
}

fn bounded_identity_text(mut file: File) -> Result<String, String> {
    let mut data = Vec::new();
    (&mut file)
        .take(16385)
        .read_to_end(&mut data)
        .map_err(|_| "cannot read login identity")?;
    if data.len() > 16384 {
        return Err("login identity exceeds its bound".into());
    }
    String::from_utf8(data).map_err(|_| "invalid login identity encoding".into())
}
#[derive(Clone)]
struct LoginProcess {
    id: ProcessIdentity,
    state: char,
    cgroup: String,
}
fn login_process(pid: u32) -> Result<LoginProcess, String> {
    let before = process_identity(pid).ok_or("login process ended")?;
    let read = |name: &str| -> Result<String, String> {
        bounded_identity_text(
            OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
                .open(format!("/proc/{pid}/{name}"))
                .map_err(|_| "login process disappeared")?,
        )
    };
    let stat = read("stat")?;
    let state = stat
        .rsplit_once(')')
        .and_then(|(_, s)| s.split_whitespace().next())
        .and_then(|s| s.chars().next())
        .ok_or("invalid login process state")?;
    let cgroup = read("cgroup")?;
    let after = process_identity(pid).ok_or("login process ended")?;
    if (before.uid, before.start) != (after.uid, after.start) {
        return Err("login process changed during inspection".into());
    }
    Ok(LoginProcess {
        id: before,
        state,
        cgroup,
    })
}
fn login_scope(cgroup: &str, uid: u32) -> Result<String, String> {
    let mut found = std::collections::BTreeSet::new();
    let prefix = format!("/user.slice/user-{uid}.slice/");
    for line in cgroup.lines() {
        let path = line.splitn(3, ':').nth(2).ok_or("invalid login cgroup")?;
        let Some(rest) = path.strip_prefix(&prefix) else {
            continue;
        };
        let Some(id) = rest
            .split('/')
            .next()
            .and_then(|s| s.strip_prefix("session-"))
            .and_then(|s| s.strip_suffix(".scope"))
        else {
            continue;
        };
        if !safe_name(id) {
            return Err("invalid login scope name".into());
        }
        found.insert(id.to_owned());
    }
    if found.len() != 1 {
        return Err("client must belong to one authenticated SSH session".into());
    }
    found
        .into_iter()
        .next()
        .ok_or("missing login session".into())
}
fn logind_record(id: &str) -> Result<BTreeMap<String, String>, String> {
    if !safe_name(id) {
        return Err("invalid login session name".into());
    }
    let directory = Path::new("/run/systemd/sessions");
    trusted_directory(directory)?;
    let path = directory.join(id);
    trusted(&path, false)?;
    let before = fs::symlink_metadata(&path).map_err(|_| "login session disappeared")?;
    if !before.is_file() || before.file_type().is_symlink() {
        return Err("login session must be a regular root-owned record".into());
    }
    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(&path)
        .map_err(|_| "cannot open login session record")?;
    let held = file
        .metadata()
        .map_err(|_| "cannot inspect login session record")?;
    if (before.dev(), before.ino()) != (held.dev(), held.ino()) {
        return Err("login session record changed".into());
    }
    let contents = bounded_identity_text(file)?;
    let after = fs::symlink_metadata(&path).map_err(|_| "login session disappeared")?;
    let stamp = |m: &fs::Metadata| (m.dev(), m.ino(), m.mtime(), m.mtime_nsec(), m.size());
    if stamp(&before) != stamp(&after) {
        return Err("login session changed during inspection".into());
    }
    let mut fields = BTreeMap::new();
    for line in contents
        .lines()
        .filter(|s| !s.is_empty() && !s.starts_with('#'))
    {
        let (key, value) = line.split_once('=').ok_or("invalid login session record")?;
        if fields
            .insert(key.to_owned(), value.trim().to_owned())
            .is_some()
        {
            return Err("duplicate login session field".into());
        }
    }
    Ok(fields)
}
fn valid_remote_host(host: &str) -> bool {
    !host.is_empty()
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
fn validate_remote_session_facts(
    uid: u32,
    client: &LoginProcess,
    leader: &LoginProcess,
    session: &str,
    fields: &BTreeMap<String, String>,
    service: &str,
) -> Result<String, String> {
    let field = |key: &str| fields.get(key).map(String::as_str);
    let scope = format!("session-{session}.scope");
    if client.id.uid != uid
        || (leader.id.uid != 0 && leader.id.uid != uid)
        || client.id.start == 0
        || leader.id.start == 0
        || [client.state, leader.state]
            .iter()
            .any(|s| matches!(s, 'Z' | 'X' | 'x'))
        || field("UID").and_then(|s| s.parse::<u32>().ok()) != Some(uid)
        || field("LEADER").and_then(|s| s.parse::<u32>().ok()) != Some(leader.id.pid)
        || field("REMOTE") != Some("1")
        || field("SERVICE") != Some(service)
        || !matches!(field("SEAT"), None | Some(""))
        || !matches!(field("VTNR"), None | Some("" | "0"))
        || !matches!(field("STATE"), Some("active" | "online"))
        || !matches!(
            field("CLASS"),
            Some("user" | "user-early" | "user-light" | "user-early-light")
        )
        || field("SCOPE") != Some(scope.as_str())
        || login_scope(&client.cgroup, uid)? != session
        || login_scope(&leader.cgroup, uid)? != session
    {
        return Err(
            "logind does not confirm the witnessed account's seatless remote session".into(),
        );
    }
    field("REMOTE_HOST")
        .filter(|host| valid_remote_host(host))
        .map(str::to_owned)
        .ok_or_else(|| "login record has no valid remote host".into())
}
fn validate_ssh_origin(
    uid: u32,
    client: &LoginProcess,
    leader: &LoginProcess,
    session: &str,
    fields: &BTreeMap<String, String>,
) -> Result<String, String> {
    validate_remote_session_facts(uid, client, leader, session, fields, "sshd")
}
fn authenticated_ssh_origin(w: &Witness) -> Result<String, String> {
    let client = login_process(w.client_pid)?;
    if client.id.start != w.client_start {
        return Err("SSH client identity changed".into());
    }
    let session = login_scope(&client.cgroup, w.caller.uid)?;
    let fields = logind_record(&session)?;
    let leader_pid = fields
        .get("LEADER")
        .and_then(|s| s.parse::<u32>().ok())
        .filter(|pid| *pid > 1)
        .ok_or("SSH session has no live leader")?;
    let leader = login_process(leader_pid)?;
    let host = validate_ssh_origin(w.caller.uid, &client, &leader, &session, &fields)?;
    let after = login_process(w.client_pid)?;
    if (after.id.start, after.id.uid, after.cgroup)
        != (client.id.start, client.id.uid, client.cgroup)
        || start_time(leader_pid)? != leader.id.start
    {
        return Err("SSH session moved during verification".into());
    }
    Ok(host)
}
fn authenticated_selected_session(p: &Policy, uid: u32) -> Result<(), String> {
    let current = login_process(std::process::id())?;
    let session = login_scope(&current.cgroup, uid)?;
    let fields = logind_record(&session)?;
    let leader_pid = fields
        .get("LEADER")
        .and_then(|s| s.parse::<u32>().ok())
        .filter(|pid| *pid > 1)
        .ok_or("selected session has no live leader")?;
    let leader = login_process(leader_pid)?;
    validate_remote_session_facts(uid, &current, &leader, &session, &fields, &p.pam_service)?;
    if std::env::var("XDG_SESSION_ID").ok().as_deref() != Some(session.as_str())
        || ["XDG_SEAT", "XDG_VTNR"]
            .into_iter()
            .any(|key| std::env::var_os(key).is_some_and(|v| !v.is_empty()))
    {
        return Err("PAM did not supply the selected seatless logind session".into());
    }
    let runtime = PathBuf::from(format!("/run/user/{uid}"));
    if std::env::var_os("XDG_RUNTIME_DIR").as_deref() != Some(runtime.as_os_str()) {
        return Err("PAM did not supply the selected account's runtime directory".into());
    }
    trusted_directory(runtime.parent().ok_or("runtime directory has no parent")?)?;
    let before =
        fs::symlink_metadata(&runtime).map_err(|_| "selected runtime directory disappeared")?;
    if !before.is_dir()
        || before.file_type().is_symlink()
        || before.uid() != uid
        || before.mode() & 0o7777 != 0o700
    {
        return Err("selected runtime directory must be account-owned mode0700".into());
    }
    let held = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(&runtime)
        .map_err(|_| "cannot pin selected runtime directory")?;
    let metadata = held
        .metadata()
        .map_err(|_| "cannot identify selected runtime directory")?;
    if (
        metadata.dev(),
        metadata.ino(),
        metadata.uid(),
        metadata.mode(),
    ) != (before.dev(), before.ino(), before.uid(), before.mode())
        || start_time(current.id.pid)? != current.id.start
        || start_time(leader_pid)? != leader.id.start
        || login_process(current.id.pid)?.cgroup != current.cgroup
    {
        return Err("selected authenticated session changed during admission".into());
    }
    Ok(())
}
fn drain_control_pipe(
    fd: i32,
    bytes: &mut Vec<u8>,
    limit: usize,
    deadline: Instant,
) -> Result<bool, String> {
    let mut buffer = [0u8; 4096];
    loop {
        // A continuously writing untrusted server must not monopolize this
        // synchronous poll or allocate past the shared message ceiling.
        if Instant::now() >= deadline {
            return Err("terminal command deadline expired".into());
        }
        let count = unsafe { libc::read(fd, buffer.as_mut_ptr().cast(), buffer.len()) };
        if count > 0 {
            let count = count as usize;
            if bytes.len().saturating_add(count) > limit {
                return Err("terminal command output exceeded policy".into());
            }
            bytes.extend_from_slice(&buffer[..count]);
        } else if count == 0 {
            return Ok(true);
        } else {
            let error = std::io::Error::last_os_error();
            match error.kind() {
                std::io::ErrorKind::WouldBlock => return Ok(false),
                std::io::ErrorKind::Interrupted => continue,
                _ => return Err("terminal command output disconnected".into()),
            }
        }
    }
}
fn bounded(cmd: &mut Command, p: &Policy) -> Result<String, String> {
    let deadline = Instant::now() + p.command_timeout;
    let mut child = cmd
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0)
        .spawn()
        .map_err(|_| "cannot start terminal control command")?;
    // The direct child remains unreaped until try_wait below, so its PID
    // cannot be recycled during setup. Record kernel start ticks for kills.
    let identity = process_identity(child.id());
    let stdout = child
        .stdout
        .take()
        .ok_or("terminal command stdout unavailable")?;
    let stderr = child
        .stderr
        .take()
        .ok_or("terminal command stderr unavailable")?;
    let mut out = Vec::new();
    let mut err = Vec::new();
    let mut out_eof = false;
    let mut err_eof = false;
    let result = (|| {
        for fd in [stdout.as_raw_fd(), stderr.as_raw_fd()] {
            let flags = unsafe { libc::fcntl(fd, libc::F_GETFL) };
            if flags < 0 || unsafe { libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0
            {
                return Err("cannot bound terminal command output".to_owned());
            }
        }
        loop {
            if !out_eof {
                out_eof = drain_control_pipe(stdout.as_raw_fd(), &mut out, p.limit, deadline)?;
            }
            if !err_eof {
                err_eof = drain_control_pipe(stderr.as_raw_fd(), &mut err, p.limit, deadline)?;
            }
            if let Some(status) = child
                .try_wait()
                .map_err(|_| "terminal command status unavailable")?
            {
                // Capture bytes queued before direct-child exit, then close
                // readers. Descendants cannot hold a privileged EOF join open.
                if !out_eof {
                    drain_control_pipe(stdout.as_raw_fd(), &mut out, p.limit, deadline)?;
                }
                if !err_eof {
                    drain_control_pipe(stderr.as_raw_fd(), &mut err, p.limit, deadline)?;
                }
                return Ok(status);
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                return Err("terminal command deadline expired".into());
            }
            let mut fds = [
                libc::pollfd {
                    fd: if out_eof { -1 } else { stdout.as_raw_fd() },
                    events: libc::POLLIN,
                    revents: 0,
                },
                libc::pollfd {
                    fd: if err_eof { -1 } else { stderr.as_raw_fd() },
                    events: libc::POLLIN,
                    revents: 0,
                },
            ];
            let milliseconds = remaining.min(Duration::from_millis(20)).as_millis().max(1) as i32;
            if unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as libc::nfds_t, milliseconds) } < 0
                && std::io::Error::last_os_error().kind() != std::io::ErrorKind::Interrupted
            {
                return Err("terminal command poll failed".into());
            }
        }
    })();
    drop(stdout);
    drop(stderr);
    let status = match result {
        Ok(status) => status,
        Err(error) => {
            if matches!(child.try_wait(), Ok(None)) {
                if identity.is_some_and(|id| start_time(id.pid).ok() == Some(id.start)) {
                    let pid = child.id() as i32;
                    if unsafe { libc::getpgid(pid) } == pid {
                        unsafe {
                            libc::kill(-pid, libc::SIGKILL);
                        }
                    }
                    // kill only this still-unreaped, directly owned process.
                    let _ = child.kill();
                }
                // Reap independently; neither a pipe holder nor a rare D-state
                // child can block the authentication/cleanup caller's thread.
                let _ = std::thread::Builder::new()
                    .name("mios-terminal-reap".into())
                    .spawn(move || {
                        let _ = child.wait();
                    });
            }
            return Err(error);
        }
    };
    if !status.success() {
        return Err("remote login terminal command failed".into());
    }
    String::from_utf8(out)
        .map(|s| s.trim_end_matches(['\r', '\n']).to_owned())
        .map_err(|_| "invalid terminal control response".into())
}

fn tmux(p: &Policy, socket: &Path, args: &[&str]) -> Result<String, String> {
    bounded(clean_command(&p.tmux).arg("-S").arg(socket).args(args), p)
}
fn nonce() -> Result<String, String> {
    let mut b = [0; 16];
    File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut b))
        .map_err(|_| "random source unavailable")?;
    Ok(b.iter().map(|n| format!("{n:02x}")).collect())
}
fn start_time(pid: u32) -> Result<u64, String> {
    let stat = fs::read_to_string(format!("/proc/{pid}/stat"))
        .map_err(|_| "process is no longer running")?;
    stat.rsplit_once(')')
        .and_then(|(_, s)| s.split_whitespace().nth(19))
        .and_then(|s| s.parse().ok())
        .ok_or_else(|| "invalid process identity".into())
}
fn tty(fd: i32) -> Result<PathBuf, String> {
    let mut b = [0i8; 4096];
    // SAFETY: b is writable storage of the provided length.
    if unsafe { libc::ttyname_r(fd, b.as_mut_ptr(), b.len()) } != 0 {
        return Err("remote login needs an interactive PTY".into());
    }
    // SAFETY: successful ttyname_r NUL terminates b.
    Ok(PathBuf::from(
        unsafe { CStr::from_ptr(b.as_ptr()) }
            .to_string_lossy()
            .into_owned(),
    ))
}
fn socket_owned(path: &Path, uid: u32) -> Result<fs::Metadata, String> {
    let m = fs::symlink_metadata(path).map_err(|_| "remote terminal socket unavailable")?;
    if !m.file_type().is_socket() || m.uid() != uid || m.mode() & 0o007 != 0 {
        return Err("remote terminal must have a private caller-owned socket".into());
    }
    if m.mode() & 0o070 != 0 {
        let parent = path.parent().ok_or("terminal socket has no parent")?;
        let directory =
            fs::metadata(parent).map_err(|_| "terminal socket directory unavailable")?;
        if !directory.is_dir() || directory.uid() != uid || directory.mode() & 0o077 != 0 {
            return Err(
                "group-accessible terminal socket needs a private caller-owned directory".into(),
            );
        }
    }
    Ok(m)
}
fn tmux_environment() -> Result<(PathBuf, String), String> {
    let value = std::env::var("TMUX").map_err(|_| "not inside a MiOS terminal")?;
    let socket = value
        .rsplitn(3, ',')
        .nth(2)
        .ok_or("malformed tmux environment")?;
    let pane = std::env::var("TMUX_PANE").map_err(|_| "terminal pane is missing")?;
    if !id(&pane, '%') || !Path::new(socket).is_absolute() {
        return Err("invalid tmux target".into());
    }
    Ok((PathBuf::from(socket), pane))
}

const CLIENT_FORMAT: &str = "#{client_name}|#{client_pid}|#{session_id}|#{window_id}|#{client_tty}";
struct ClientInfo {
    pid: u32,
    session: String,
    window: String,
    tty: PathBuf,
}
fn exact_client(snapshot: &str, name: &str) -> Result<ClientInfo, String> {
    let mut matches = snapshot.lines().filter_map(|line| {
        let fields: Vec<_> = line.split('|').collect();
        (fields.first().copied() == Some(name)).then_some(fields)
    });
    let fields = matches
        .next()
        .ok_or("the named remote client is not attached")?;
    if matches.next().is_some()
        || fields.len() != 5
        || !id(fields[2], '$')
        || !id(fields[3], '@')
        || fields[4] != name
        || !fields[4].starts_with("/dev/pts/")
    {
        return Err("remote client snapshot is ambiguous or invalid".into());
    }
    let pid = fields[1]
        .parse::<u32>()
        .ok()
        .filter(|p| *p > 1)
        .ok_or("invalid remote client PID")?;
    Ok(ClientInfo {
        pid,
        session: fields[2].into(),
        window: fields[3].into(),
        tty: fields[4].into(),
    })
}

struct Portal {
    socket: PathBuf,
    session: String,
    anchor: String,
    server: u32,
    start: u64,
    dev: u64,
    ino: u64,
}
impl Portal {
    fn cleanup(&self, p: &Policy) {
        let identity = socket_owned(&self.socket, unsafe { libc::getuid() });
        if identity.is_ok_and(|m| (m.dev(), m.ino()) == (self.dev, self.ino))
            && start_time(self.server).ok() == Some(self.start)
        {
            let _ = tmux(p, &self.socket, &["kill-server"]);
        }
    }
}
/// Small isolated reservation: no desktop app drawer, dock, caller startup
/// config, global server or other account's socket is loaded before login.
fn prepare_portal(p: &Policy, namespace: &Path) -> Result<Portal, String> {
    let socket = namespace.join("terminal.sock");
    let (columns, rows) = terminal_size();
    let output = bounded(
        clean_command(&p.tmux)
            .arg("-S")
            .arg(&socket)
            .args([
                "-f",
                "/dev/null",
                "new-session",
                "-d",
                "-P",
                "-F",
                "#{session_id}|#{window_id}|#{pane_id}|#{pid}",
                "-s",
                "remote-login",
                "-n",
                &p.title,
                "-x",
                &columns.to_string(),
                "-y",
                &rows.to_string(),
            ])
            .arg(&p.executable)
            .args(["remote-login", "canvas"]),
        p,
    )?;
    let f: Vec<_> = output.split('|').collect();
    if f.len() != 4 || !id(f[0], '$') || !id(f[1], '@') || !id(f[2], '%') {
        return Err("portal reservation returned invalid IDs".into());
    }
    let server = f[3].parse().map_err(|_| "invalid portal PID")?;
    let m = socket_owned(&socket, unsafe { libc::getuid() })?;
    let portal = Portal {
        socket,
        session: f[0].into(),
        anchor: f[2].into(),
        server,
        start: start_time(server)?,
        dev: m.dev(),
        ino: m.ino(),
    };
    let setup = (|| {
        tmux(p, &portal.socket, &["set-option", "-g", "status", "off"])?;
        tmux(p, &portal.socket, &["set-option", "-g", "mouse", "on"])?;
        tmux(
            p,
            &portal.socket,
            &["set-option", "-g", "exit-unattached", "off"],
        )?;
        // Creation precedes the first client. Arm automatic cleanup only when
        // it attaches, so a lost SSH client cannot strand this private server
        // and its canvas when the opener is terminated by SIGHUP/SIGKILL.
        tmux(
            p,
            &portal.socket,
            &[
                "set-hook",
                "-g",
                "client-attached",
                "set-option -g exit-unattached on",
            ],
        )?;
        tmux(
            p,
            &portal.socket,
            &["set-option", "-g", "update-environment", ""],
        )?;
        for table in ["root", "prefix"] {
            tmux(p, &portal.socket, &["unbind-key", "-a", "-T", table])?;
        }
        tmux(
            p,
            &portal.socket,
            &["select-pane", "-t", &portal.anchor, "-T", &p.title],
        )?;
        let commands = tmux(p, &portal.socket, &["list-commands"])?;
        if !commands.lines().any(|l| l.starts_with("float-pane ")) {
            return Err("MiOS terminal lacks the native floating pane API".into());
        }
        Ok(())
    })();
    if let Err(e) = setup {
        portal.cleanup(p);
        return Err(e);
    }
    Ok(portal)
}
fn terminal_size() -> (u16, u16) {
    // SAFETY: winsize is plain C data; ioctl writes precisely this struct.
    let mut s: libc::winsize = unsafe { std::mem::zeroed() };
    if unsafe { libc::ioctl(0, libc::TIOCGWINSZ, &mut s) } == 0 && s.ws_col > 0 && s.ws_row > 0 {
        (s.ws_col, s.ws_row)
    } else {
        (80, 24)
    }
}
fn open(root: &Path, client: Option<&str>) -> Result<(), String> {
    if root
        .canonicalize()
        .map_err(|_| "remote login root unavailable")?
        != Path::new("/")
    {
        return Err("remote authentication runs only against the installed system".into());
    }
    if unsafe { libc::getuid() } == 0 {
        return Err("open remote login as the authenticated SSH user".into());
    }
    // The external-SSH shim delegates this gate to the trusted native reader;
    // a user-layer flag or loader pointer cannot bypass an enabled greeter.
    if !remote_enabled()? {
        return crate::desktop::open(root, false);
    }
    let p = policy()?;
    if std::env::var_os("TMUX").is_some() {
        let (socket, source) = tmux_environment()?;
        socket_owned(&socket, unsafe { libc::getuid() })?;
        let client = match client {
            Some(s) => s.to_owned(),
            None => {
                let clients = tmux(
                    &p,
                    &socket,
                    &["list-clients", "-F", "#{client_name}|#{pane_id}"],
                )?;
                let names: Vec<_> = clients
                    .lines()
                    .filter_map(|l| l.split_once('|'))
                    .filter(|(_, pane)| *pane == source)
                    .map(|(name, _)| name)
                    .collect();
                if names.len() != 1 {
                    return Err("select the exact attached client for remote login".into());
                }
                names[0].to_owned()
            }
        };
        spawn_entry(&p, &socket, &source, &client)?;
        return Ok(());
    }
    let parent = PathBuf::from(format!("/run/user/{}", unsafe { libc::getuid() }));
    let m = fs::symlink_metadata(&parent)
        .map_err(|_| "the SSH user's runtime directory is unavailable")?;
    if !m.is_dir() || m.uid() != unsafe { libc::getuid() } || m.mode() & 0o077 != 0 {
        return Err("the SSH runtime directory must be private and caller-owned".into());
    }
    let namespace = parent.join(format!("mios-remote-login-{}", nonce()?));
    fs::DirBuilder::new()
        .mode(0o700)
        .create(&namespace)
        .map_err(|_| "cannot reserve remote portal")?;
    let portal = prepare_portal(&p, &namespace)?;
    let result = (|| {
        let client = tty(0)?
            .to_str()
            .ok_or("client PTY must be UTF-8")?
            .to_owned();
        spawn_entry(&p, &portal.socket, &portal.anchor, &client)?;
        let status = clean_command(&p.tmux)
            .arg("-S")
            .arg(&portal.socket)
            .args(["attach-session", "-t", &portal.session])
            .status()
            .map_err(|_| "cannot attach remote login portal")?;
        if status.success() {
            Ok(())
        } else {
            Err("remote login portal disconnected".into())
        }
    })();
    portal.cleanup(&p);
    // Only remove an empty directory allocated by this invocation.
    let _ = fs::remove_dir(&namespace);
    result
}
fn spawn_entry(p: &Policy, socket: &Path, source: &str, client: &str) -> Result<(), String> {
    if !client.starts_with("/dev/pts/") || client.chars().any(char::is_control) {
        return Err("remote client must be a PTY".into());
    }
    let pane = bounded(
        clean_command(&p.tmux)
            .arg("-S")
            .arg(socket)
            .args([
                "new-pane",
                "-d",
                "-t",
                source,
                "-P",
                "-F",
                "#{pane_id}",
                "--",
            ])
            .arg(&p.executable)
            .args(["remote-login", "entry", "--root", "/", "--client", client]),
        p,
    )?;
    if !id(&pane, '%') {
        return Err("remote entry pane identity is invalid".into());
    }
    let result = (|| {
        tmux(
            p,
            socket,
            &["set-option", "-p", "-t", &pane, PHASE, "login"],
        )?;
        tmux(p, socket, &["select-pane", "-t", &pane, "-T", &p.title])?;
        // -T changes only the title; native tmux returns before selecting.
        // The floating greeter must own keyboard input, not the canvas below it.
        tmux(p, socket, &["select-pane", "-t", &pane])?;
        resize(p, socket, &pane, false, None)?;
        Ok(())
    })();
    if result.is_err() {
        let _ = tmux(p, socket, &["kill-pane", "-t", &pane]);
    }
    result
}
fn resize(
    p: &Policy,
    socket: &Path,
    pane: &str,
    session: bool,
    user: Option<&str>,
) -> Result<(), String> {
    let query = [
        "display-message",
        "-p",
        "-t",
        pane,
        "#{window_width}|#{window_height}",
    ];
    let run = |args: &[&str]| match user {
        Some(user) => bounded(
            clean_command(&p.runuser)
                .args(["-u", user, "--"])
                .arg(&p.tmux)
                .arg("-S")
                .arg(socket)
                .args(args),
            p,
        ),
        None => tmux(p, socket, args),
    };
    let q = run(&query)?;
    let (width, height) = q.split_once('|').ok_or("invalid portal dimensions")?;
    let w: u16 = width.parse().map_err(|_| "invalid portal width")?;
    let h: u16 = height.parse().map_err(|_| "invalid portal height")?;
    if w == 0 || h == 0 {
        return Err("empty portal viewport".into());
    }
    let (pw, ph) = if session {
        (w, h)
    } else {
        p.popup.size(SurfaceKind::Dialog, w, h)
    };
    run(&[
        "float-pane",
        "-t",
        pane,
        "-x",
        &pw.to_string(),
        "-y",
        &ph.to_string(),
        "-X",
        &((w - pw) / 2).to_string(),
        "-Y",
        &((h - ph) / 2).to_string(),
    ])?;
    Ok(())
}

async fn canvas() -> Result<(), String> {
    let p = policy()?;
    let mut winch = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::window_change())
        .map_err(|_| "cannot observe viewport")?;
    loop {
        let (w, h) = terminal_size();
        let label: String = p
            .background
            .chars()
            .take(w.saturating_sub(4) as usize)
            .collect();
        print!(
            "\x1b[2J\x1b[H\x1b[?25l\x1b[{};{}H{}",
            (h / 2).max(1),
            ((w.saturating_sub(label.len() as u16)) / 2).max(1),
            label
        );
        std::io::stdout()
            .flush()
            .map_err(|_| "portal output closed")?;
        if winch.recv().await.is_none() {
            break;
        }
    }
    Ok(())
}

async fn entry(root: &Path, client: &str) -> Result<(), String> {
    if root != Path::new("/") || unsafe { libc::getuid() } == 0 {
        return Err("invalid unprivileged remote entry".into());
    }
    let p = policy()?;
    let (socket, pane) = tmux_environment()?;
    socket_owned(&socket, unsafe { libc::getuid() })?;
    let pid = std::process::id();
    let start = start_time(pid)?;
    let deadline = Instant::now() + p.startup_timeout;
    loop {
        if tmux(&p, &socket, &["list-clients", "-F", CLIENT_FORMAT])
            .is_ok_and(|s| exact_client(&s, client).is_ok())
        {
            break;
        }
        if Instant::now() >= deadline {
            return Err("remote terminal client did not attach".into());
        }
        tokio::time::sleep(Duration::from_millis(100)).await;
    }
    // A newly attached client needs the entry selected after its attachment as
    // well. Startup created the pane before that client existed.
    tmux(&p, &socket, &["select-pane", "-t", &pane])?;
    let mut observer = geometry_observer(&p, &socket, &pane).await?;
    let mut command = clean_command(&p.sudo);
    command
        .args(["-n", "--"])
        .arg(&p.executable)
        .args(["remote-login", "worker", "--socket"])
        .arg(&socket)
        .args([
            "--pane",
            &pane,
            "--client",
            client,
            "--entry-pid",
            &pid.to_string(),
            "--entry-start",
            &start.to_string(),
        ]);
    // sudo denial must be a clear error, never a decorative successful login.
    let mut child = tokio::process::Command::from(command)
        .kill_on_drop(true)
        .spawn()
        .map_err(|_| "cannot start the remote authentication broker")?;
    let result = child
        .wait()
        .await
        .map_err(|_| "remote authentication broker disconnected");
    observer.abort();
    let _ = (&mut observer).await;
    match result { Ok(s) if s.success() => Ok(()), _ => Err("Remote login could not start or was cancelled. The administrator must install the dedicated MiOS remote-login PAM and sudo policy.".into()) }
}

/// A single, output-suppressed control connection observes server formats at
/// most once a second. It never polls processes or constrains terminal size.
async fn geometry_observer(
    p: &Policy,
    socket: &Path,
    pane: &str,
) -> Result<tokio::task::JoinHandle<()>, String> {
    let session = tmux(
        p,
        socket,
        &["display-message", "-p", "-t", pane, "#{session_id}"],
    )?;
    if !id(&session, '$') {
        return Err("invalid remote portal session".into());
    }
    let mut cmd = clean_command(&p.tmux);
    cmd.arg("-S").arg(socket).args([
        "-C",
        "attach-session",
        "-f",
        "ignore-size,no-output",
        "-t",
        &session,
    ]);
    let mut child = tokio::process::Command::from(cmd)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .kill_on_drop(true)
        .spawn()
        .map_err(|_| "cannot observe remote viewport")?;
    let mut input = child.stdin.take().ok_or("observer input is unavailable")?;
    let output = child
        .stdout
        .take()
        .ok_or("observer output is unavailable")?;
    input.write_all(format!("refresh-client -B 'mios-login:{pane}:#{{window_width}}|#{{window_height}}|#{{{PHASE}}}'\n").as_bytes())
        .await.map_err(|_| "cannot subscribe to remote viewport")?;
    let p = p.clone();
    let pane = pane.to_owned();
    Ok(tokio::spawn(async move {
        use tokio::io::{AsyncBufReadExt, BufReader};
        let mut lines = BufReader::new(output).lines();
        let mut last = None;
        while let Ok(Some(line)) = lines.next_line().await {
            if line.starts_with("%exit") {
                break;
            }
            if !line.starts_with("%subscription-changed mios-login ") {
                continue;
            }
            let Some((_, value)) = line.split_once(" : ") else {
                continue;
            };
            let fields: Vec<_> = value.split('|').collect();
            if fields.len() != 3 {
                continue;
            }
            let (Ok(w), Ok(h)) = (fields[0].parse::<u16>(), fields[1].parse::<u16>()) else {
                continue;
            };
            if w == 0 || h == 0 {
                continue;
            }
            let session = fields[2] == "session";
            if last == Some((w, h, session)) {
                continue;
            }
            last = Some((w, h, session));
            let (pw, ph) = if session {
                (w, h)
            } else {
                p.popup.size(SurfaceKind::Dialog, w, h)
            };
            let update = format!(
                "float-pane -d -t {pane} -x {pw} -y {ph} -X {} -Y {}\n",
                (w - pw) / 2,
                (h - ph) / 2
            );
            if input.write_all(update.as_bytes()).await.is_err() {
                break;
            }
        }
        let _ = child.kill().await;
        let _ = child.wait().await;
    }))
}

#[derive(Clone)]
struct Account {
    uid: u32,
    gid: u32,
    name: String,
    home: PathBuf,
}
fn account(uid: u32) -> Result<Account, String> {
    let mut buffer = vec![0u8; 65_536];
    // SAFETY: all pointers refer to valid storage throughout getpwuid_r.
    let mut passwd: libc::passwd = unsafe { std::mem::zeroed() };
    let mut result = std::ptr::null_mut();
    if unsafe {
        libc::getpwuid_r(
            uid,
            &mut passwd,
            buffer.as_mut_ptr().cast(),
            buffer.len(),
            &mut result,
        )
    } != 0
        || result.is_null()
        || passwd.pw_name.is_null()
        || passwd.pw_dir.is_null()
    {
        return Err("remote account is unavailable".into());
    }
    // SAFETY: successful getpwuid_r returns NUL-terminated fields in buffer.
    let name = unsafe { CStr::from_ptr(passwd.pw_name) }
        .to_str()
        .map_err(|_| "invalid account name")?
        .to_owned();
    let home = unsafe { CStr::from_ptr(passwd.pw_dir) }
        .to_str()
        .map_err(|_| "invalid account home")?
        .into();
    if !safe_name(&name) {
        return Err("unsupported remote account name".into());
    }
    Ok(Account {
        uid,
        gid: passwd.pw_gid,
        name,
        home,
    })
}
fn named_account(name: &str) -> Result<Account, String> {
    if !safe_name(name) {
        return Err("unsupported remote account".into());
    }
    let c = CString::new(name).map_err(|_| "invalid account")?;
    let mut buffer = vec![0u8; 65_536];
    let mut passwd: libc::passwd = unsafe { std::mem::zeroed() };
    let mut result = std::ptr::null_mut();
    // SAFETY: pointers remain valid for the call and strings are NUL terminated.
    if unsafe {
        libc::getpwnam_r(
            c.as_ptr(),
            &mut passwd,
            buffer.as_mut_ptr().cast(),
            buffer.len(),
            &mut result,
        )
    } != 0
        || result.is_null()
    {
        return Err("remote account is unavailable".into());
    }
    let found = account(passwd.pw_uid)?;
    if found.name != name {
        return Err("ambiguous remote account".into());
    }
    Ok(found)
}
#[derive(Clone)]
struct Witness {
    caller: Account,
    remote_host: String,
    socket: PathBuf,
    socket_dev: u64,
    socket_ino: u64,
    pane: String,
    client: String,
    client_pid: u32,
    client_start: u64,
    client_tty_identity: (u64, u64, u64),
    entry_pid: u32,
    entry_start: u64,
    tty: PathBuf,
}
impl Witness {
    fn control(&self, p: &Policy, args: &[&str]) -> Result<String, String> {
        bounded(
            clean_command(&p.runuser)
                .args(["-u", &self.caller.name, "--"])
                .arg(&p.tmux)
                .arg("-S")
                .arg(&self.socket)
                .args(args),
            p,
        )
    }
    fn verify_process(&self, p: &Policy) -> Result<(), String> {
        if start_time(self.entry_pid)? != self.entry_start {
            return Err("remote entry identity changed".into());
        }
        let proc_dir =
            fs::metadata(format!("/proc/{}", self.entry_pid)).map_err(|_| "remote entry ended")?;
        let proc_exe = fs::metadata(format!("/proc/{}/exe", self.entry_pid))
            .map_err(|_| "remote entry ended")?;
        let exe = fs::metadata(&p.executable).map_err(|_| "installed worker disappeared")?;
        if proc_dir.uid() != self.caller.uid
            || (proc_exe.dev(), proc_exe.ino()) != (exe.dev(), exe.ino())
        {
            return Err("remote entry executable or caller changed".into());
        }
        let m = socket_owned(&self.socket, self.caller.uid)?;
        if (m.dev(), m.ino()) != (self.socket_dev, self.socket_ino) {
            return Err("remote socket identity changed".into());
        }
        if start_time(self.client_pid)? != self.client_start {
            return Err("remote client ended or was replaced".into());
        }
        let process = fs::metadata(format!("/proc/{}", self.client_pid))
            .map_err(|_| "remote client ended")?;
        let actual = fs::metadata(format!("/proc/{}/exe", self.client_pid))
            .map_err(|_| "remote client ended")?;
        let expected = fs::metadata(&p.tmux).map_err(|_| "installed terminal unavailable")?;
        let tty = fs::metadata(format!("/proc/{}/fd/0", self.client_pid))
            .map_err(|_| "remote client terminal closed")?;
        if process.uid() != self.caller.uid
            || (actual.dev(), actual.ino()) != (expected.dev(), expected.ino())
            || !tty.file_type().is_char_device()
            || (tty.dev(), tty.ino(), tty.rdev()) != self.client_tty_identity
        {
            return Err("remote client executable, caller or terminal changed".into());
        }
        if authenticated_ssh_origin(self)? != self.remote_host {
            return Err("remote client's authenticated SSH origin changed".into());
        }
        Ok(())
    }
    fn verify(&self, p: &Policy) -> Result<(), String> {
        self.verify_process(p)?;
        let pane = self.control(
            p,
            &[
                "display-message",
                "-p",
                "-t",
                &self.pane,
                "#{pane_id}|#{pane_pid}|#{pane_tty}|#{session_id}|#{window_id}|#{pane_dead}",
            ],
        )?;
        let f: Vec<_> = pane.split('|').collect();
        if f.len() != 6
            || f[0] != self.pane
            || f[1].parse::<u32>().ok() != Some(self.entry_pid)
            || Path::new(f[2]) != self.tty
            || !id(f[3], '$')
            || !id(f[4], '@')
            || f[5] != "0"
        {
            return Err("remote pane no longer matches its PTY witness".into());
        }
        let snapshot = self.control(p, &["list-clients", "-F", CLIENT_FORMAT])?;
        let client = exact_client(&snapshot, &self.client)?;
        if client.pid != self.client_pid || client.session != f[3] || client.window != f[4] {
            return Err("remote login client moved to another window".into());
        }
        Ok(())
    }
    fn phase(&self, p: &Policy, session: bool) -> Result<(), String> {
        self.verify(p)?;
        self.control(
            p,
            &[
                "set-option",
                "-p",
                "-t",
                &self.pane,
                PHASE,
                if session { "session" } else { "login" },
            ],
        )?;
        resize(
            p,
            &self.socket,
            &self.pane,
            session,
            Some(&self.caller.name),
        )
    }
}
fn original_caller(
    p: &Policy,
    socket: PathBuf,
    pane: String,
    client: String,
    entry_pid: u32,
    entry_start: u64,
) -> Result<Witness, String> {
    if unsafe { libc::geteuid() } != 0 || unsafe { libc::getuid() } != 0 {
        return Err("remote login worker requires its narrow sudo rule".into());
    }
    let uid = std::env::var("SUDO_UID")
        .ok()
        .and_then(|s| s.parse::<u32>().ok())
        .filter(|u| (p.min_uid..=p.max_uid).contains(u))
        .ok_or("a human sudo caller is required")?;
    let caller = account(uid)?;
    if std::env::var("SUDO_USER").ok().as_deref() != Some(caller.name.as_str()) {
        return Err("sudo caller identity does not match NSS".into());
    }
    if !id(&pane, '%')
        || entry_pid <= 1
        || entry_start == 0
        || !socket.is_absolute()
        || !client.starts_with("/dev/pts/")
        || client.chars().any(char::is_control)
    {
        return Err("invalid remote terminal witness".into());
    }
    let terminal = PathBuf::from(
        std::env::var_os("SUDO_TTY").ok_or("sudo did not witness the caller's terminal")?,
    );
    let pts = terminal
        .strip_prefix("/dev/pts")
        .ok()
        .and_then(|s| s.to_str())
        .filter(|s| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()))
        .ok_or("remote login only accepts PTYs")?;
    let _ = pts;
    let m = fs::symlink_metadata(&terminal).map_err(|_| "caller PTY disappeared")?;
    if !m.file_type().is_char_device() || m.uid() != uid {
        return Err("remote PTY must belong to the sudo caller".into());
    }
    let current_tty = tty(0)?;
    if !current_tty.starts_with("/dev/pts") {
        return Err("remote authentication cannot own a physical console".into());
    }
    let sm = socket_owned(&socket, uid)?;
    let mut witness = Witness {
        caller,
        remote_host: String::new(),
        socket,
        socket_dev: sm.dev(),
        socket_ino: sm.ino(),
        pane,
        client,
        client_pid: 0,
        client_start: 0,
        client_tty_identity: (0, 0, 0),
        entry_pid,
        entry_start,
        tty: terminal,
    };
    let snapshot = witness.control(p, &["list-clients", "-F", CLIENT_FORMAT])?;
    let info = exact_client(&snapshot, &witness.client)?;
    witness.client_pid = info.pid;
    witness.client_start = start_time(info.pid)?;
    let client_tty =
        fs::symlink_metadata(&info.tty).map_err(|_| "remote client PTY disappeared")?;
    if !client_tty.file_type().is_char_device() || client_tty.uid() != uid {
        return Err("remote client PTY must belong to caller".into());
    }
    witness.client_tty_identity = (client_tty.dev(), client_tty.ino(), client_tty.rdev());
    witness.remote_host = authenticated_ssh_origin(&witness)?;
    witness.verify(p)?;
    // The unprivileged entry must really be an ancestor, not an unrelated
    // executable somebody nominated by PID. sudo may insert monitor processes.
    let mut ancestor = std::process::id();
    let mut found = false;
    for _ in 0..16 {
        let stat = fs::read_to_string(format!("/proc/{ancestor}/stat"))
            .map_err(|_| "cannot verify broker ancestry")?;
        let parent = stat
            .rsplit_once(')')
            .and_then(|(_, s)| s.split_whitespace().nth(1))
            .and_then(|s| s.parse::<u32>().ok())
            .ok_or("invalid broker ancestry")?;
        if parent == entry_pid {
            found = true;
            break;
        }
        if parent <= 1 {
            break;
        }
        ancestor = parent;
    }
    if !found {
        return Err("broker is not a child of its witnessed entry".into());
    }
    Ok(witness)
}

struct PrivateState {
    directory: PathBuf,
    proxy: PathBuf,
    config: PathBuf,
    runfile: PathBuf,
    greeter_geometry: PathBuf,
    session_geometry: PathBuf,
    geometry_identities: Vec<(PathBuf, u64, u64)>,
    proxy_identity: Option<(u64, u64)>,
}
/// mkdir's requested mode is masked by the worker's umask. Apply the
/// search-only mode explicitly, through a no-follow descriptor, only after
/// checking ownership; keep config/runfiles and the socket independently private.
fn set_search_directory_permissions(path: &Path, expected_uid: u32) -> Result<(), String> {
    let directory = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_DIRECTORY | libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)
        .map_err(|_| "cannot open owned remote state directory")?;
    let before = directory
        .metadata()
        .map_err(|_| "cannot inspect remote state directory")?;
    if !before.is_dir() || before.uid() != expected_uid || before.mode() & 0o022 != 0 {
        return Err("remote state directory ownership or permissions are unsafe".into());
    }
    directory
        .set_permissions(fs::Permissions::from_mode(0o711))
        .map_err(|_| "cannot make remote state searchable")?;
    let after = directory
        .metadata()
        .map_err(|_| "cannot verify remote state permissions")?;
    let named = fs::symlink_metadata(path).map_err(|_| "remote state directory disappeared")?;
    if !after.is_dir()
        || after.uid() != expected_uid
        || after.mode() & 0o7777 != 0o711
        || !named.is_dir()
        || named.file_type().is_symlink()
        || (named.dev(), named.ino()) != (after.dev(), after.ino())
        || named.uid() != expected_uid
        || named.mode() & 0o7777 != 0o711
    {
        return Err("remote state directory identity or search mode changed".into());
    }
    Ok(())
}

impl PrivateState {
    fn create(p: &Policy) -> Result<Self, String> {
        let parent = p.state.parent().ok_or("remote state has no parent")?;
        trusted_directory(parent)?;
        match fs::DirBuilder::new().mode(0o711).create(&p.state) {
            Ok(()) => (),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => (),
            Err(_) => return Err("cannot reserve privileged remote state".into()),
        }
        trusted_directory(&p.state)?;
        set_search_directory_permissions(&p.state, 0)?;
        let directory = p.state.join(nonce()?);
        fs::DirBuilder::new()
            .mode(0o711)
            .create(&directory)
            .map_err(|_| "cannot reserve private greetd state")?;
        set_search_directory_permissions(&directory, 0)?;
        Ok(Self {
            proxy: directory.join("greeter.sock"),
            config: directory.join("greetd.conf"),
            runfile: directory.join("greetd.run"),
            greeter_geometry: directory.join("greeter-size.sock"),
            session_geometry: directory.join("session-size.sock"),
            geometry_identities: Vec::new(),
            directory,
            proxy_identity: None,
        })
    }
    fn configure(&self, p: &Policy, w: &Witness) -> Result<(), String> {
        let command = shell_command(&[
            p.runuser.to_string_lossy().into_owned(),
            "-u".into(),
            w.caller.name.clone(),
            "--".into(),
            p.executable.to_string_lossy().into_owned(),
            "remote-login".into(),
            "greeter".into(),
            "--proxy".into(),
            self.proxy.to_string_lossy().into_owned(),
            "--geometry".into(),
            self.greeter_geometry.to_string_lossy().into_owned(),
        ]);
        // greetd's private config is written by root, never supplied by caller.
        let literal = |s: &str| Value::String(s.to_owned()).to_string();
        let config = format!("[terminal]\nvt = \"none\"\nswitch = false\n[general]\nsource_profile = false\nrunfile = {}\nservice = {}\nremote_host = {}\n[default_session]\nuser = {}\nservice = {}\ncommand = {}\n",
            literal(self.runfile.to_str().ok_or("state path must be UTF-8")?), literal(&p.pam_service), literal(&w.remote_host),
            literal("root"), literal(&p.greeter_pam_service), literal(&command));
        let mut file = OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW)
            .open(&self.config)
            .map_err(|_| "cannot create private greetd configuration")?;
        file.write_all(config.as_bytes())
            .map_err(|_| "cannot write private greetd configuration")?;
        Ok(())
    }
    fn listen(&mut self, w: &Witness) -> Result<UnixListener, String> {
        let listener =
            UnixListener::bind(&self.proxy).map_err(|_| "cannot bind private greeter channel")?;
        let m = fs::symlink_metadata(&self.proxy).map_err(|_| "cannot identify greeter channel")?;
        self.proxy_identity = Some((m.dev(), m.ino()));
        fs::set_permissions(&self.proxy, fs::Permissions::from_mode(0o600))
            .map_err(|_| "cannot protect greeter channel")?;
        let path = CString::new(self.proxy.as_os_str().as_encoded_bytes())
            .map_err(|_| "invalid greeter channel")?;
        // SAFETY: path is a live NUL-terminated CString, IDs came from NSS.
        if unsafe { libc::chown(path.as_ptr(), w.caller.uid, w.caller.gid) } != 0 {
            return Err("cannot assign private greeter channel".into());
        }
        Ok(listener)
    }
}
impl Drop for PrivateState {
    fn drop(&mut self) {
        if let Some(identity) = self.proxy_identity {
            if fs::symlink_metadata(&self.proxy).is_ok_and(|m| (m.dev(), m.ino()) == identity) {
                let _ = fs::remove_file(&self.proxy);
            }
        }
        for (path, dev, ino) in &self.geometry_identities {
            if fs::symlink_metadata(path)
                .is_ok_and(|m| m.file_type().is_socket() && (m.dev(), m.ino()) == (*dev, *ino))
            {
                let _ = fs::remove_file(path);
            }
        }
        // No recursive deletion, symlink following or unrelated path cleanup.
        for file in [&self.config, &self.runfile] {
            if fs::symlink_metadata(file).is_ok_and(|m| m.is_file() && m.uid() == 0) {
                let _ = fs::remove_file(file);
            }
        }
        let _ = fs::remove_dir(&self.directory);
    }
}

#[derive(Clone, Copy)]
struct ProcessIdentity {
    pid: u32,
    start: u64,
    uid: u32,
}
fn process_identity(pid: u32) -> Option<ProcessIdentity> {
    Some(ProcessIdentity {
        pid,
        start: start_time(pid).ok()?,
        uid: fs::metadata(format!("/proc/{pid}")).ok()?.uid(),
    })
}
fn signal_owned(identity: ProcessIdentity, signal: i32) {
    if process_identity(identity.pid)
        .is_some_and(|now| now.start == identity.start && now.uid == identity.uid)
    {
        // SAFETY: positive PID; UID and kernel start ticks were witnessed.
        unsafe {
            libc::kill(identity.pid as i32, signal);
        }
    }
}
fn owned_descendants(root: ProcessIdentity) -> Vec<ProcessIdentity> {
    let same = |id: ProcessIdentity| {
        process_identity(id.pid).is_some_and(|p| p.start == id.start && p.uid == id.uid)
    };
    if !same(root) {
        return Vec::new();
    }
    let mut children = Vec::new();
    let Ok(entries) = fs::read_dir("/proc") else {
        return children;
    };
    let rows: Vec<_> = entries
        .flatten()
        .filter_map(|e| e.file_name().to_str()?.parse::<u32>().ok())
        .filter_map(|pid| {
            let stat = fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
            let parent: u32 = stat
                .rsplit_once(')')?
                .1
                .split_whitespace()
                .nth(1)?
                .parse()
                .ok()?;
            Some((pid, parent))
        })
        .collect();
    let mut parents = vec![root];
    for _ in 0..32 {
        let next: Vec<_> = rows
            .iter()
            .filter(|(_, parent)| parents.iter().any(|p| p.pid == *parent && same(*p)))
            .filter_map(|(pid, parent)| {
                let child = process_identity(*pid)?;
                let stat = fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
                let now_parent: u32 = stat
                    .rsplit_once(')')?
                    .1
                    .split_whitespace()
                    .nth(1)?
                    .parse()
                    .ok()?;
                if now_parent == *parent {
                    Some(child)
                } else {
                    None
                }
            })
            .collect();
        if next.is_empty() {
            break;
        }
        parents = next.clone();
        children.extend(next);
    }
    if same(root) {
        children
    } else {
        Vec::new()
    }
}

struct Cancellation {
    hup: tokio::signal::unix::Signal,
    term: tokio::signal::unix::Signal,
    interrupt: tokio::signal::unix::Signal,
}
impl Cancellation {
    fn new() -> Result<Self, String> {
        use tokio::signal::unix::{signal, SignalKind};
        Ok(Self {
            hup: signal(SignalKind::hangup()).map_err(|_| "cannot watch terminal lifetime")?,
            term: signal(SignalKind::terminate()).map_err(|_| "cannot watch broker lifetime")?,
            interrupt: signal(SignalKind::interrupt())
                .map_err(|_| "cannot watch broker cancellation")?,
        })
    }
    async fn cancelled(&mut self) {
        tokio::select! { _ = self.hup.recv() => (), _ = self.term.recv() => (), _ = self.interrupt.recv() => () }
    }
}
async fn worker(
    socket: PathBuf,
    pane: String,
    client: String,
    entry_pid: u32,
    entry_start: u64,
) -> Result<(), String> {
    let p = policy()?;
    let witness = original_caller(&p, socket, pane, client, entry_pid, entry_start)?;
    verify_backend(&p)?;
    // Restrictive umask applies only to this owned worker and descendants.
    unsafe {
        libc::umask(0o077);
    }
    let mut cancellation = Cancellation::new()?;
    let mut state = PrivateState::create(&p)?;
    state.configure(&p, &witness)?;
    let listener = state.listen(&witness)?;
    let (geometry, mut geometry_tasks) = state.geometry_services(&p, &witness)?;
    let mut resize_signal =
        tokio::signal::unix::signal(tokio::signal::unix::SignalKind::window_change())
            .map_err(|_| "cannot watch remote viewport")?;
    let unit = format!(
        "mios-remote-login-{}.scope",
        state
            .directory
            .file_name()
            .and_then(|s| s.to_str())
            .ok_or("invalid private scope name")?
    );
    let mut command = clean_command(&p.scope_runner);
    command
        .args([
            "--scope",
            "--collect",
            "--quiet",
            "--no-ask-password",
            "--expand-environment=no",
            "--unit",
            &unit,
            "--",
        ])
        .arg(&p.daemon)
        .args(["--mios-remote-required", "--vt", "none", "--config"])
        .arg(&state.config)
        .stdin(Stdio::inherit())
        .stdout(Stdio::inherit())
        .stderr(Stdio::inherit());
    let parent_pid = std::process::id();
    // SAFETY: this closure contains only async-signal-safe syscalls, before exec.
    unsafe {
        command.pre_exec(move || {
            if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            if libc::getppid() as u32 != parent_pid {
                return Err(std::io::Error::from_raw_os_error(libc::ECHILD));
            }
            Ok(())
        });
    }
    let mut daemon = tokio::process::Command::from(command)
        .kill_on_drop(true)
        .spawn()
        .map_err(|_| "cannot start private greetd")?;
    let Some(daemon_id) = daemon
        .id()
        .and_then(process_identity)
        .filter(|id| id.uid == 0)
    else {
        let _ = daemon.kill().await;
        let _ = daemon.wait().await;
        return Err("private greetd identity unavailable".into());
    };
    let backend = PathBuf::from(format!("/run/greetd-{}.sock", daemon_id.pid));
    let deadline = Instant::now() + p.startup_timeout;
    let backend_metadata = loop {
        if let Ok(m) = fs::symlink_metadata(&backend) {
            if m.file_type().is_socket() && m.uid() == 0 && m.mode() & 0o7777 == 0o700 {
                let actual = fs::metadata(format!("/proc/{}/exe", daemon_id.pid));
                let expected = fs::metadata(&p.daemon);
                let cgroup = fs::read_to_string(format!("/proc/{}/cgroup", daemon_id.pid));
                if !matches!((actual,expected),(Ok(a),Ok(b)) if (a.dev(),a.ino()) == (b.dev(),b.ino()))
                    || !cgroup.is_ok_and(|text| {
                        text.lines()
                            .any(|line| line == format!("0::/system.slice/{unit}"))
                    })
                {
                    break Err("private greetd did not enter its isolated system scope");
                }
                break Ok((m.dev(), m.ino()));
            }
        }
        match daemon.try_wait() {
            Ok(None) => (),
            _ => break Err("private greetd could not initialize the remote PAM service"),
        }
        if Instant::now() >= deadline {
            break Err("private greetd channel timed out");
        }
        tokio::select! {
            _ = cancellation.cancelled() => break Err("remote login was cancelled"),
            _ = tokio::time::sleep(Duration::from_millis(25)) => (),
        }
    };
    let result = match backend_metadata {
        Ok(_) => {
            supervise(
                &p,
                &witness,
                &mut daemon,
                daemon_id,
                &mut cancellation,
                BrokerIo {
                    listener,
                    backend: &backend,
                    geometry: &geometry,
                    geometry_tasks: &mut geometry_tasks,
                    resize_signal: &mut resize_signal,
                },
            )
            .await
        }
        Err(reason) => Err(reason.to_owned()),
    };
    // Ask greetd to close the authenticated PAM session and reap its workers.
    let descendants = owned_descendants(daemon_id);
    signal_owned(daemon_id, libc::SIGTERM);
    if tokio::time::timeout(Duration::from_secs(5), daemon.wait())
        .await
        .is_err()
    {
        signal_owned(daemon_id, libc::SIGKILL);
        let _ = daemon.wait().await;
    }
    for identity in descendants.iter().rev() {
        signal_owned(*identity, libc::SIGTERM);
    }
    tokio::time::sleep(Duration::from_millis(100)).await;
    for identity in descendants.iter().rev() {
        signal_owned(*identity, libc::SIGKILL);
    }
    if let Ok(identity) = backend_metadata {
        if fs::symlink_metadata(&backend).is_ok_and(|m| (m.dev(), m.ino()) == identity) {
            let _ = fs::remove_file(&backend);
        }
    }
    result
}

struct BrokerIo<'a> {
    listener: UnixListener,
    backend: &'a Path,
    geometry: &'a GeometryControl,
    geometry_tasks: &'a mut GeometryTasks,
    resize_signal: &'a mut tokio::signal::unix::Signal,
}

async fn supervise(
    p: &Policy,
    w: &Witness,
    daemon: &mut tokio::process::Child,
    identity: ProcessIdentity,
    cancellation: &mut Cancellation,
    io: BrokerIo<'_>,
) -> Result<(), String> {
    let BrokerIo {
        listener,
        backend,
        geometry,
        geometry_tasks,
        resize_signal,
    } = io;
    let p2 = p.clone();
    let w2 = w.clone();
    let backend = backend.to_owned();
    let geometry2 = geometry.clone();
    let mut proxy = tokio::spawn(async move {
        serve_proxy(&p2, &w2, identity, listener, &backend, &geometry2).await
    });
    let mut health = tokio::time::interval(Duration::from_secs(1));
    health.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut proxy_completed = false;
    let result = loop {
        tokio::select! {
            _ = cancellation.cancelled() => break Ok(()),
            _ = resize_signal.recv() => geometry.publish(),
            _ = geometry_tasks.failed() => break Err("private geometry service stopped".into()),
            result = &mut proxy => {
                proxy_completed = true;
                break result.map_err(|_| "private greeter channel stopped".to_owned()).and_then(|r| r);
            },
            _ = health.tick() => {
                if w.verify_process(p).is_err() { break Ok(()); }
                match daemon.try_wait() {
                    Ok(Some(s)) if s.success() => break Ok(()),
                    Ok(Some(_)) | Err(_) => break Err("private greetd stopped unexpectedly".into()),
                    Ok(None) => (),
                }
            }
        }
    };
    proxy.abort();
    if !proxy_completed {
        let _ = proxy.await;
    }
    result
}

async fn greeter(proxy: &Path, geometry: &Path) -> Result<(), String> {
    let p = policy()?;
    let user = account(unsafe { libc::getuid() })?;
    if !(p.min_uid..=p.max_uid).contains(&user.uid) {
        return Err("remote greeter requires its unprivileged SSH caller".into());
    }
    if proxy.file_name().and_then(|s| s.to_str()) != Some("greeter.sock")
        || proxy.parent().and_then(Path::parent) != Some(p.state.as_path())
        || geometry != proxy.with_file_name("greeter-size.sock")
    {
        return Err("remote greeter channel is outside its private state".into());
    }
    trusted_directory(proxy.parent().ok_or("greeter state is missing")?)?;
    socket_owned(proxy, user.uid)?;
    let size = geometry_connection(&p, geometry, "greeter-size.sock").await?;
    let (outer_width, outer_height) = terminal_size();
    // On logout the new greeter can start before the broker finishes shrinking
    // the outer float. Derive its fixed flags from the target dialog bounds,
    // so a previous fullscreen session cannot inflate padding or width.
    let (width, height) = p.popup.size(SurfaceKind::Dialog, outer_width, outer_height);
    let padding_cap = height.saturating_sub(10) / 6;
    let mut command = clean_command(&p.greeter);
    command
        .env("GREETD_SOCK", proxy)
        .env("HOME", &user.home)
        .env("USER", &user.name)
        .env("LOGNAME", &user.name)
        .args([
            "--user-menu",
            "--user-menu-min-uid",
            &p.min_uid.to_string(),
            "--user-menu-max-uid",
            &p.max_uid.to_string(),
            "--asterisks",
            "--greeting",
            &p.greeting,
            "--width",
            &p.greeter_width
                .min(width.saturating_sub(4).max(8))
                .to_string(),
            "--theme",
            &p.greeter_theme,
            "--window-padding",
            &p.greeter_padding[0].min(padding_cap).to_string(),
            "--container-padding",
            &p.greeter_padding[1].min(padding_cap).to_string(),
            "--prompt-padding",
            &p.greeter_padding[2].min(padding_cap).to_string(),
            "--cmd",
            &shell_command(&p.session),
        ]);
    owned_pty_bridge(command, size, p.command_timeout).await
}

async fn session(geometry: &Path) -> Result<(), String> {
    let p = policy()?;
    let failure_deadline = tokio::time::Instant::now() + p.auth_timeout;
    let uid = unsafe { libc::getuid() };
    if unsafe { libc::geteuid() } != uid || !(p.min_uid..=p.max_uid).contains(&uid) {
        return Err("remote session bridge must run as its authenticated account".into());
    }
    // Authentication may succeed while an optional PAM session module fails.
    // Do not admit Desktop or signal the fullscreen transition without genuine
    // selected-account remote logind state and the correct runtime directory.
    authenticated_selected_session(&p, uid)?;
    let mut cancellation = Cancellation::new()?;
    let mut geometry_lifetime = None;
    let result = async {
        let dimensions = tokio::select! {
            result = geometry_connection(&p, geometry, "session-size.sock") => result?,
            _ = cancellation.cancelled() => return Ok(()),
        };
        // Keep this admitted channel alive until a startup error is acknowledged.
        // Only after the bridge has joined its relays may the ACK watch read it.
        geometry_lifetime = Some(duplicate_geometry(&dimensions)?);
        // The environment comes from genuine greetd/PAM, including the selected
        // account's runtime directory. No caller TMUX or SSH state was forwarded.
        let mut command = Command::new(&p.session[0]);
        command.args(&p.session[1..]);
        owned_pty_bridge_with_cancellation(
            command,
            dimensions,
            p.command_timeout,
            &mut cancellation,
        )
        .await
    }
    .await;
    if result.is_err() {
        // This is after genuine selected-session proof, including failures before
        // a child could start. Never acquire a foreign TTY or replace the original
        // error when this inherited terminal is no longer safe to acknowledge on.
        if let Ok(terminal) = SessionFailureTerminal::pin() {
            let ack = SessionFailureAck {
                hint: p.failure_return,
                deadline: failure_deadline,
            };
            return hold_session_failure(
                result,
                &terminal.input,
                &terminal.output,
                &ack,
                session_failure_cancelled(cancellation.cancelled(), geometry_lifetime),
            )
            .await;
        }
    }
    result
}

/// Secret response strings are never deserialized, formatted or persisted.
/// A frame is wiped on success, cancellation, timeout and task abort alike.
struct Frame(Vec<u8>);
impl Drop for Frame {
    fn drop(&mut self) {
        for byte in &mut self.0 {
            // SAFETY: unique mutable references into this Vec; volatile stores
            // prevent dead-store removal of credential-buffer clearing.
            unsafe {
                std::ptr::write_volatile(byte, 0);
            }
        }
        std::sync::atomic::compiler_fence(std::sync::atomic::Ordering::SeqCst);
    }
}
enum FrameReadError {
    HeaderIo,
    Length,
    BodyIo,
}
async fn read_frame_inner(
    stream: &mut UnixStream,
    limit: usize,
) -> Result<Option<Frame>, FrameReadError> {
    let mut header = [0u8; 4];
    match stream.read_exact(&mut header).await {
        Ok(_) => (),
        Err(e) if e.kind() == std::io::ErrorKind::UnexpectedEof => return Ok(None),
        Err(_) => return Err(FrameReadError::HeaderIo),
    }
    let length = u32::from_ne_bytes(header) as usize;
    if length == 0 || length > limit {
        return Err(FrameReadError::Length);
    }
    let mut frame = Frame(vec![0; length]);
    stream
        .read_exact(&mut frame.0)
        .await
        .map_err(|_| FrameReadError::BodyIo)?;
    Ok(Some(frame))
}
async fn read_frame(stream: &mut UnixStream, limit: usize) -> Result<Option<Frame>, String> {
    read_frame_inner(stream, limit)
        .await
        .map_err(|error| match error {
            FrameReadError::HeaderIo => "private greeter read failed".into(),
            FrameReadError::Length => "private greeter message exceeded policy".into(),
            FrameReadError::BodyIo => "private greeter message was interrupted".into(),
        })
}
async fn read_frontend_frame(
    stream: &mut UnixStream,
    limit: usize,
) -> Result<Option<Frame>, String> {
    match read_frame_inner(stream, limit).await {
        Ok(frame) => Ok(frame),
        Err(FrameReadError::HeaderIo | FrameReadError::BodyIo) => Ok(None),
        Err(FrameReadError::Length) => Err("private greeter message exceeded policy".into()),
    }
}
async fn write_frame(stream: &mut UnixStream, frame: &[u8]) -> Result<(), String> {
    let length = u32::try_from(frame.len()).map_err(|_| "private greeter message too large")?;
    stream
        .write_all(&length.to_ne_bytes())
        .await
        .map_err(|_| "private greeter write failed")?;
    stream
        .write_all(frame)
        .await
        .map_err(|_| "private greeter write failed")?;
    stream
        .flush()
        .await
        .map_err(|_| "private greeter flush failed".into())
}
#[derive(Deserialize)]
struct Envelope<'a> {
    #[serde(rename = "type", borrow)]
    kind: &'a str,
    #[serde(default, borrow)]
    username: Option<&'a str>,
    // The parser validates, then discards these without allocating a String.
    #[serde(default)]
    response: Option<serde::de::IgnoredAny>,
}
#[derive(Clone, Copy, PartialEq, Eq)]
enum Phase {
    Idle,
    Challenge,
    Authenticated,
    Starting,
}

struct AuthState {
    phase: Phase,
    target: Option<Account>,
    deadline: Option<tokio::time::Instant>,
}
impl AuthState {
    fn new(deadline: Option<tokio::time::Instant>) -> Self {
        Self {
            phase: Phase::Idle,
            target: None,
            deadline,
        }
    }
}

async fn before_auth_deadline<T>(
    deadline: Option<tokio::time::Instant>,
    future: impl std::future::Future<Output = Result<T, String>>,
) -> Result<T, String> {
    match deadline {
        Some(deadline) => tokio::time::timeout_at(deadline, future)
            .await
            .map_err(|_| "remote authentication deadline expired")?,
        None => future.await,
    }
}
fn descendant_of(peer: ProcessIdentity, root: ProcessIdentity) -> bool {
    let mut pid = peer.pid;
    for _ in 0..32 {
        if pid == root.pid {
            return start_time(root.pid).ok() == Some(root.start)
                && start_time(peer.pid).ok() == Some(peer.start);
        }
        let Ok(stat) = fs::read_to_string(format!("/proc/{pid}/stat")) else {
            return false;
        };
        let Some(parent) = stat
            .rsplit_once(')')
            .and_then(|(_, s)| s.split_whitespace().nth(1))
            .and_then(|s| s.parse::<u32>().ok())
        else {
            return false;
        };
        if parent <= 1 {
            return false;
        }
        pid = parent;
    }
    false
}
fn auth_admission(deadline: Option<tokio::time::Instant>) -> Result<(), String> {
    if deadline.is_some_and(|limit| tokio::time::Instant::now() >= limit) {
        return Err("remote authentication admission deadline expired".into());
    }
    Ok(())
}
async fn forward_admitted_frame(
    deadline: Option<tokio::time::Instant>,
    backend: &mut UnixStream,
    frame: &[u8],
) -> Result<(), String> {
    // Synchronous terminal work may outlive a Tokio timeout poll. Recheck at
    // the actual privileged IPC boundary after that bounded work returns.
    auth_admission(deadline)?;
    write_frame(backend, frame).await
}

fn take_greeter_generation(
    events: &mut tokio::sync::watch::Receiver<Option<ProcessIdentity>>,
    handled: &mut Option<ProcessIdentity>,
) -> Option<ProcessIdentity> {
    let latest = (*events.borrow_and_update())?;
    if handled.is_some_and(|old| old.pid == latest.pid && old.start == latest.start) {
        return None;
    }
    *handled = Some(latest);
    Some(latest)
}
fn apply_greeter_generation(
    p: &Policy,
    w: &Witness,
    geometry: &GeometryControl,
    events: &mut tokio::sync::watch::Receiver<Option<ProcessIdentity>>,
    handled: &mut Option<ProcessIdentity>,
    state: &mut AuthState,
) -> Result<Option<ProcessIdentity>, String> {
    let AuthState {
        phase,
        target,
        deadline,
    } = state;
    if take_greeter_generation(events, handled).is_some() {
        if *phase == Phase::Starting {
            *deadline = Some(tokio::time::Instant::now() + p.auth_timeout);
        }
        *phase = Phase::Idle;
        *target = None;
        auth_admission(*deadline)?;
        geometry.session_owner(None)?;
        w.phase(p, false)?;
        auth_admission(*deadline)?;
    }
    Ok(*events.borrow())
}

async fn serve_proxy(
    p: &Policy,
    w: &Witness,
    daemon: ProcessIdentity,
    listener: UnixListener,
    backend: &Path,
    geometry: &GeometryControl,
) -> Result<(), String> {
    let mut state = AuthState::new(Some(tokio::time::Instant::now() + p.auth_timeout));
    let mut greeter_pid = None;
    // One absolute admission deadline spans accepts, all conversation frames, writes and
    // reconnects. Only a genuine running session suspends the admission clock; terminal
    // cleanup retains its independently bounded command timeout.
    let mut greeter_started = geometry.greeter_started.subscribe();
    let mut handled_greeter = None;
    let mut session_started = geometry.session_started.subscribe();
    loop {
        let accepted = tokio::select! {
            result = before_auth_deadline(state.deadline, async {
                listener.accept().await.map_err(|_| "private greeter accept failed".to_owned())
            }) => result,
            changed = greeter_started.changed() => {
                changed.map_err(|_| "greeter lifecycle channel stopped")?;
                apply_greeter_generation(p, w, geometry, &mut greeter_started, &mut handled_greeter,
                    &mut state)?;
                continue;
            },
            changed = session_started.changed(), if state.phase == Phase::Starting && state.deadline.is_some() => {
                changed.map_err(|_| "session lifecycle channel stopped")?;
                if session_started.borrow_and_update().is_some() { state.deadline = None; }
                continue;
            },
        };
        let (mut frontend, _) = accepted?;
        let cred = frontend
            .peer_cred()
            .map_err(|_| "private greeter peer unavailable")?;
        if cred.uid() != w.caller.uid {
            continue;
        }
        let Some(pid) = cred.pid().and_then(|pid| u32::try_from(pid).ok()) else {
            continue;
        };
        let actual = fs::metadata(format!("/proc/{pid}/exe"));
        let expected = fs::metadata(&p.greeter);
        if !matches!((actual, expected), (Ok(a), Ok(b)) if (a.dev(),a.ino()) == (b.dev(),b.ino())) {
            continue;
        }
        let Some(peer) = process_identity(pid) else {
            continue;
        };
        if !descendant_of(peer, daemon) {
            continue;
        }
        // accept and the startup watch may both be ready. Consume/apply the
        // current bridge generation before any protocol frame, so it cannot
        // later masquerade as a logout event after StartSession succeeds.
        let bridge = apply_greeter_generation(
            p,
            w,
            geometry,
            &mut greeter_started,
            &mut handled_greeter,
            &mut state,
        )?;
        if !bridge.is_some_and(|bridge| descendant_of(peer, bridge)) {
            continue;
        }
        let peer_identity = (pid, peer.start);
        if greeter_pid.is_some_and(|previous| previous != peer_identity) {
            if state.phase == Phase::Starting {
                state.deadline = Some(tokio::time::Instant::now() + p.auth_timeout);
            }
            state.phase = Phase::Idle;
            state.target = None;
            auth_admission(state.deadline)?;
            geometry.session_owner(None)?;
            w.phase(p, false)?;
            auth_admission(state.deadline)?;
        }
        greeter_pid = Some(peer_identity);
        let mut daemon_stream = before_auth_deadline(state.deadline, async {
            UnixStream::connect(backend)
                .await
                .map_err(|_| "private greetd channel disconnected".to_owned())
        })
        .await?;
        let backend_cred = daemon_stream
            .peer_cred()
            .map_err(|_| "cannot verify private greetd")?;
        if backend_cred.uid() != 0
            || backend_cred.pid().and_then(|pid| u32::try_from(pid).ok()) != Some(daemon.pid)
            || start_time(daemon.pid).ok() != Some(daemon.start)
        {
            return Err("private greetd process identity changed".into());
        }
        serve_frontend(
            p,
            w,
            &mut state,
            &mut frontend,
            &mut daemon_stream,
            geometry,
        )
        .await?;
    }
}
async fn serve_frontend(
    p: &Policy,
    w: &Witness,
    state: &mut AuthState,
    frontend: &mut UnixStream,
    backend: &mut UnixStream,
    geometry: &GeometryControl,
) -> Result<(), String> {
    loop {
        let Some(frame) =
            before_auth_deadline(state.deadline, read_frontend_frame(frontend, p.limit)).await?
        else {
            break;
        };
        let delivered = before_auth_deadline(
            state.deadline,
            proxy_request(p, w, state, frontend, backend, &frame.0, geometry),
        )
        .await?;
        if !delivered {
            break;
        }
    }
    // The frontend is disposable: TUIGREET sends CancelSession then reconnects
    // without reading its reply. Preserve the broker and a running session;
    // cancel any abandoned authentication before admitting another frontend.
    if matches!(state.phase, Phase::Challenge | Phase::Authenticated) {
        before_auth_deadline(
            state.deadline,
            forward_admitted_frame(state.deadline, backend, br#"{"type":"cancel_session"}"#),
        )
        .await?;
        let response = before_auth_deadline(state.deadline, read_frame(backend, p.limit))
            .await?
            .ok_or("private greetd disconnected while cancelling authentication")?;
        let reply: Envelope<'_> = serde_json::from_slice(&response.0)
            .map_err(|_| "invalid private greetd cancellation response")?;
        if reply.kind != "success" {
            return Err("private greetd did not cancel disconnected authentication".into());
        }
        state.phase = Phase::Idle;
        state.target = None;
    }
    Ok(())
}
async fn reject(stream: &mut UnixStream) -> Result<bool, String> {
    Ok(write_frame(stream, br#"{"type":"error","error_type":"error","description":"Remote login request refused by administrator policy"}"#).await.is_ok())
}
// PAM owns identity and the session command; only the trusted SSOT language
// and a bounded terminal type cross this post-authentication boundary. Never
// forward the greeter's requested environment, caller locale or loader flags.
fn fixed_session_environment(language: &str, term: Option<&str>) -> Vec<String> {
    let mut env = vec![format!("LANG={language}")];
    if let Some(term) = term.filter(|s| s.len() <= 128 && !s.chars().any(char::is_control)) {
        env.push(format!("TERM={term}"));
    }
    env
}
fn fixed_session_request(
    p: &Policy,
    geometry: &Path,
    request: &Envelope<'_>,
    term: Option<&str>,
) -> Result<Vec<u8>, String> {
    if request.kind != "start_session" {
        return Err("fixed remote session requires start_session".into());
    }
    let command = vec![
        p.executable.to_string_lossy().into_owned(),
        "remote-login".into(),
        "session".into(),
        "--geometry".into(),
        geometry.to_string_lossy().into_owned(),
    ];
    let env = fixed_session_environment(&p.language, term);
    serde_json::to_vec(
        &serde_json::json!({"type":"start_session", "cmd":[shell_command(&command)], "env":env}),
    )
    .map_err(|_| "cannot serialize fixed remote session".into())
}

async fn proxy_request(
    p: &Policy,
    w: &Witness,
    state: &mut AuthState,
    frontend: &mut UnixStream,
    backend: &mut UnixStream,
    raw: &[u8],
    geometry: &GeometryControl,
) -> Result<bool, String> {
    let AuthState {
        phase,
        target,
        deadline,
    } = state;
    let deadline = *deadline;
    auth_admission(deadline)?;
    let request: Envelope<'_> =
        serde_json::from_slice(raw).map_err(|_| "invalid private greeter request")?;
    let _ = &request.response;
    let operation = request.kind;
    let accepted = match operation {
        "create_session" if *phase == Phase::Idle => {
            let account = request
                .username
                .and_then(|u| named_account(u).ok())
                .filter(|a| (p.min_uid..=p.max_uid).contains(&a.uid));
            if let Some(account) = account {
                *target = Some(account);
                true
            } else {
                false
            }
        }
        "post_auth_message_response" => *phase == Phase::Challenge,
        "cancel_session" => *phase != Phase::Starting,
        "start_session" => *phase == Phase::Authenticated && target.is_some(),
        _ => false,
    };
    if !accepted {
        return reject(frontend).await;
    }
    if operation == "create_session" {
        w.phase(p, false)?;
        auth_admission(deadline)?;
    }
    let fixed;
    let outgoing = if operation == "start_session" {
        let account = target
            .as_ref()
            .ok_or("authenticated target account unavailable")?;
        geometry.session_owner(Some(account))?;
        w.phase(p, true)?;
        auth_admission(deadline)?;
        // greetd/PAM selects the UID. Its fixed unprivileged wrapper creates a
        // fresh UID-owned controlling PTY before executing administrator argv.
        let term = std::env::var("TERM").ok();
        fixed = fixed_session_request(p, &geometry.session_path, &request, term.as_deref())?;
        fixed.as_slice()
    } else {
        raw
    };
    forward_admitted_frame(deadline, backend, outgoing).await?;
    let response = read_frame(backend, p.limit)
        .await?
        .ok_or("private greetd conversation disconnected")?;
    let reply: Envelope<'_> =
        serde_json::from_slice(&response.0).map_err(|_| "invalid private greetd response")?;
    match (operation, reply.kind) {
        ("create_session" | "post_auth_message_response", "auth_message") => {
            *phase = Phase::Challenge
        }
        ("create_session" | "post_auth_message_response", "success") => {
            *phase = Phase::Authenticated
        }
        ("cancel_session", "success") => {
            *phase = Phase::Idle;
            *target = None;
        }
        ("start_session", "success") => *phase = Phase::Starting,
        ("start_session", "error") => {
            *phase = Phase::Authenticated;
            geometry.session_owner(None)?;
            w.phase(p, false)?;
            auth_admission(deadline)?;
        }
        (_, "error") => {
            *phase = Phase::Idle;
            *target = None;
        }
        _ => return Err("private greetd returned an unexpected state".into()),
    }
    // Only this caller-owned frontend may disappear normally. Backend writes,
    // replies, framing policy and the admission deadline remain fatal errors.
    Ok(write_frame(frontend, &response.0).await.is_ok())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn post_auth_session_locale_comes_from_trusted_ssot() {
        for name in ["en_US.UTF-8", "C.UTF-8", "C", "sr_RS.UTF-8@latin"] {
            let doc: Value = toml::from_str(&format!("[locale]\nlanguage = {name:?}\n")).unwrap();
            assert_eq!(session_language(&doc).unwrap(), name);
        }
        for name in [
            "".to_owned(),
            "a".repeat(129),
            "en_US.UTF-8\nLD_PRELOAD=x".into(),
            "LANG=en_US.UTF-8".into(),
            "en US.UTF-8".into(),
        ] {
            let mut doc = Value::Table(Default::default());
            doc.as_table_mut()
                .unwrap()
                .insert("locale".into(), Value::Table(Default::default()));
            doc["locale"]
                .as_table_mut()
                .unwrap()
                .insert("language".into(), Value::String(name));
            assert!(session_language(&doc).is_err());
        }
    }

    #[test]
    fn post_auth_environment_keeps_ssot_lang_when_term_is_absent_or_unsafe() {
        assert_eq!(
            fixed_session_environment("en_US.UTF-8", None),
            ["LANG=en_US.UTF-8"]
        );
        for term in ["xterm\nLD_PRELOAD=x".to_owned(), "a".repeat(129)] {
            assert_eq!(
                fixed_session_environment("en_US.UTF-8", Some(&term)),
                ["LANG=en_US.UTF-8"]
            );
        }
        assert_eq!(
            fixed_session_environment("en_US.UTF-8", Some("tmux-256color")),
            ["LANG=en_US.UTF-8", "TERM=tmux-256color"]
        );
    }

    #[test]
    fn post_auth_request_replaces_attacker_command_and_environment_with_fixed_policy() {
        let (p, _) = fixture();
        let attacker: Envelope<'_> = serde_json::from_slice(br#"{"type":"start_session","cmd":["/bin/sh","-c","untrusted-command"],"env":["LANG=C","LC_ALL=C","LD_PRELOAD=/untrusted.so","TERM=attacker","FIXTURE_SECRET=must-not-cross"]}"#).unwrap();
        let ordinary: Envelope<'_> =
            serde_json::from_slice(br#"{"type":"start_session"}"#).unwrap();
        let geometry = Path::new("/unused/session-size.sock");
        let actual = fixed_session_request(&p, geometry, &attacker, Some("tmux-256color")).unwrap();
        assert_eq!(
            actual,
            fixed_session_request(&p, geometry, &ordinary, Some("tmux-256color")).unwrap()
        );
        let outgoing: serde_json::Value = serde_json::from_slice(&actual).unwrap();
        assert_eq!(
            outgoing,
            serde_json::json!({
                "type":"start_session",
                "cmd":["'/unused/miosd' 'remote-login' 'session' '--geometry' '/unused/session-size.sock'"],
                "env":["LANG=en_US.UTF-8", "TERM=tmux-256color"]
            })
        );
        let unrelated: Envelope<'_> =
            serde_json::from_slice(br#"{"type":"cancel_session"}"#).unwrap();
        assert!(fixed_session_request(&p, geometry, &unrelated, Some("tmux-256color")).is_err());
    }

    #[tokio::test]
    async fn failed_child_stderr_stays_visible_until_a_new_explicit_ack() {
        let (mut master, slave) = input_boundary_test_pty();
        let _terminal = TerminalRaw::arm_fd(slave.as_raw_fd()).unwrap();
        let mut child = Command::new("/bin/sh")
            .args(["-c", "printf 'fixture-desktop-failure' >&2; exit 17"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::from(slave.try_clone().unwrap()))
            .spawn()
            .unwrap();
        let status = child.wait().unwrap();
        assert_eq!(status.code(), Some(17));
        master.write_all(b"\r").unwrap();
        assert!(input_boundary_ready(slave.as_raw_fd(), 1000));
        let input = BridgeFd::new(slave.try_clone().unwrap(), false).unwrap();
        let output = BridgeFd::new(slave, false).unwrap();
        let original = format!("owned PTY child exited with {status}");
        let expected = original.clone();
        let task = tokio::spawn(async move {
            let ack = SessionFailureAck {
                hint: "fixture-return-hint".into(),
                deadline: tokio::time::Instant::now() + Duration::from_secs(3),
            };
            hold_session_failure(Err(original), &input, &output, &ack, std::future::pending()).await
        });
        let master = BridgeFd::new(master, false).unwrap();
        let visible = tokio::time::timeout(Duration::from_secs(1), async {
            let mut visible = Vec::new();
            while !visible.windows(b"fixture-return-hint".len()).any(|v| v == b"fixture-return-hint") {
                let mut bytes = [0; 256];
                let count = master.read(&mut bytes).await.unwrap();
                assert!(count > 0);
                visible.extend_from_slice(&bytes[..count]);
            }
            visible
        }).await.unwrap();
        assert!(visible.windows(b"fixture-desktop-failure".len()).any(|v| v == b"fixture-desktop-failure"));
        assert!(!task.is_finished(), "old desktop input must not acknowledge the failure");
        master.write_all(b"x").await.unwrap();
        tokio::task::yield_now().await;
        assert!(!task.is_finished(), "unknown new keys must not acknowledge the failure");
        master.write_all(b"\r").await.unwrap();
        assert_eq!(tokio::time::timeout(Duration::from_secs(1), task).await.unwrap().unwrap(), Err(expected));
    }

    #[tokio::test]
    async fn pre_child_geometry_error_waits_for_ack_without_rendering_private_details() {
        let (p, _) = fixture();
        let error = geometry_connection(&p, Path::new("/outside/session-size.sock"), "session-size.sock")
            .await.unwrap_err();
        let original = format!("{error}: fixture-private-detail");
        let expected = original.clone();
        let (master, slave) = input_boundary_test_pty();
        let _terminal = TerminalRaw::arm_fd(slave.as_raw_fd()).unwrap();
        let input = BridgeFd::new(slave.try_clone().unwrap(), false).unwrap();
        let output = BridgeFd::new(slave, false).unwrap();
        let task = tokio::spawn(async move {
            let ack = SessionFailureAck {
                hint: "fixture-generic-startup-failure".into(),
                deadline: tokio::time::Instant::now() + Duration::from_secs(3),
            };
            hold_session_failure(Err(original), &input, &output, &ack, std::future::pending()).await
        });
        let master = BridgeFd::new(master, false).unwrap();
        let visible = tokio::time::timeout(Duration::from_secs(1), async {
            let mut bytes = [0; 256];
            let count = master.read(&mut bytes).await.unwrap();
            bytes[..count].to_vec()
        }).await.unwrap();
        assert!(visible.windows(b"fixture-generic-startup-failure".len()).any(|v| v == b"fixture-generic-startup-failure"));
        assert!(!visible.windows(b"fixture-private-detail".len()).any(|v| v == b"fixture-private-detail"));
        assert!(!task.is_finished());
        master.write_all(b"\x1b").await.unwrap();
        assert_eq!(tokio::time::timeout(Duration::from_secs(1), task).await.unwrap().unwrap(), Err(expected));
    }

    #[tokio::test]
    async fn blocked_failure_hint_cancels_with_live_geometry_or_geometry_eof() {
        for close_geometry in [false, true] {
            let (master, slave) = input_boundary_test_pty();
            let _terminal = TerminalRaw::arm_fd(slave.as_raw_fd()).unwrap();
            let input = BridgeFd::new(slave.try_clone().unwrap(), false).unwrap();
            let output = BridgeFd::new(slave, false).unwrap();
            let bytes = [b'x'; 4096];
            let mut total = 0;
            loop {
                let count = unsafe { libc::write(output.io.as_raw_fd(), bytes.as_ptr().cast(), bytes.len()) };
                if count < 0 {
                    assert_eq!(std::io::Error::last_os_error().kind(), std::io::ErrorKind::WouldBlock);
                    break;
                }
                total += count as usize;
                assert!(total <= 1024 * 1024, "fixture output must become backpressured");
            }
            let (geometry, peer) = UnixStream::pair().unwrap();
            let (cancel, cancelled) = tokio::sync::oneshot::channel::<()>();
            let task = tokio::spawn(async move {
                let ack = SessionFailureAck {
                    hint: "fixture-return-hint".into(),
                    deadline: tokio::time::Instant::now() + Duration::from_secs(5),
                };
                let stopped = session_failure_cancelled(async { let _ = cancelled.await; }, Some(geometry));
                acknowledge_session_failure(&input, &output, &ack, stopped).await;
            });
            tokio::task::yield_now().await;
            assert!(!task.is_finished());
            if close_geometry {
                drop(peer);
                tokio::time::timeout(Duration::from_secs(1), task).await.unwrap().unwrap();
            } else {
                cancel.send(()).unwrap();
                tokio::time::timeout(Duration::from_secs(1), task).await.unwrap().unwrap();
                // Geometry remained alive while cancellation ended the blocked write.
                assert!(peer.peer_cred().is_ok());
            }
            drop(master);
        }
    }

    #[tokio::test]
    async fn failure_geometry_watch_tolerates_a_previously_part_read_size_frame() {
        let (geometry, mut peer) = UnixStream::pair().unwrap();
        let mut task = tokio::spawn(session_failure_cancelled(std::future::pending(), Some(geometry)));
        // The old size relay can be cancelled after consuming a frame header or
        // part of its payload. The ACK observes EOF, not a fresh size-frame parse.
        peer.write_all(b"remaining-size-payload").await.unwrap();
        assert!(tokio::time::timeout(Duration::from_millis(20), &mut task).await.is_err());
        peer.shutdown().await.unwrap();
        tokio::time::timeout(Duration::from_secs(1), task).await.unwrap().unwrap();
    }

    #[tokio::test]
    async fn failure_ack_keeps_one_absolute_deadline_and_success_skips_ack() {
        let (master, slave) = input_boundary_test_pty();
        let _terminal = TerminalRaw::arm_fd(slave.as_raw_fd()).unwrap();
        let input = BridgeFd::new(slave.try_clone().unwrap(), false).unwrap();
        let output = BridgeFd::new(slave, false).unwrap();
        let expired = SessionFailureAck {
            hint: "must-not-be-presented".into(),
            deadline: tokio::time::Instant::now() - Duration::from_secs(1),
        };
        assert_eq!(hold_session_failure(Err("original-error".into()), &input, &output, &expired, std::future::pending()).await, Err("original-error".into()));
        assert!(!input_boundary_ready(master.as_raw_fd(), 0));
        let live = SessionFailureAck {
            hint: "must-not-be-presented".into(),
            deadline: tokio::time::Instant::now() + Duration::from_secs(5),
        };
        assert_eq!(tokio::time::timeout(Duration::from_secs(1), hold_session_failure(Ok(()), &input, &output, &live, std::future::pending())).await.unwrap(), Ok(()));
        assert!(!input_boundary_ready(master.as_raw_fd(), 0));
        let short = SessionFailureAck {
            hint: "fixture-deadline".into(),
            deadline: tokio::time::Instant::now() + Duration::from_millis(30),
        };
        tokio::time::timeout(Duration::from_secs(1), acknowledge_session_failure(&input, &output, &short, std::future::pending())).await.unwrap();
    }

    #[tokio::test]
    async fn failure_terminal_pins_one_tty_and_restores_shared_descriptor_flags() {
        let (_master, slave) = input_boundary_test_pty();
        let (_other_master, other_slave) = input_boundary_test_pty();
        let fd = slave.as_raw_fd();
        let flags = unsafe { libc::fcntl(fd, libc::F_GETFL) };
        let mut original: libc::termios = unsafe { std::mem::zeroed() };
        assert_eq!(unsafe { libc::tcgetattr(fd, &mut original) }, 0);
        {
            let _terminal = SessionFailureTerminal::pin_fds(fd, fd).unwrap();
            assert_ne!(unsafe { libc::fcntl(fd, libc::F_GETFL) } & libc::O_NONBLOCK, 0);
        }
        assert_eq!(unsafe { libc::fcntl(fd, libc::F_GETFL) }, flags);
        let mut restored: libc::termios = unsafe { std::mem::zeroed() };
        assert_eq!(unsafe { libc::tcgetattr(fd, &mut restored) }, 0);
        assert_eq!((original.c_iflag, original.c_oflag, original.c_cflag, original.c_lflag),
                   (restored.c_iflag, restored.c_oflag, restored.c_cflag, restored.c_lflag));
        let other_flags = unsafe { libc::fcntl(other_slave.as_raw_fd(), libc::F_GETFL) };
        assert!(SessionFailureTerminal::pin_fds(fd, other_slave.as_raw_fd()).is_err());
        assert_eq!(unsafe { libc::fcntl(fd, libc::F_GETFL) }, flags);
        assert_eq!(unsafe { libc::fcntl(other_slave.as_raw_fd(), libc::F_GETFL) }, other_flags);
    }

    #[tokio::test]
    async fn exited_child_terminal_tail_is_forwarded_before_teardown() {
        let (child_output, bridge_input) = std::os::unix::net::UnixStream::pair().unwrap();
        let (bridge_output, observed) = std::os::unix::net::UnixStream::pair().unwrap();
        let child_output = File::from(std::os::fd::OwnedFd::from(child_output));
        let mut child = Command::new("/bin/sh")
            .args(["-c", "printf 'desktop startup failed\\n' >&2; exit 7"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::from(child_output))
            .spawn()
            .unwrap();
        assert_eq!(child.wait().unwrap().code(), Some(7));
        // The child has exited before this relay is polled. Its terminal tail
        // must still reach the client, rather than disappear during teardown.
        let input = Arc::new(
            BridgeFd::new(File::from(std::os::fd::OwnedFd::from(bridge_input)), false).unwrap(),
        );
        let output = Arc::new(
            BridgeFd::new(File::from(std::os::fd::OwnedFd::from(bridge_output)), false).unwrap(),
        );
        let mut relay = tokio::spawn(relay_terminal(input, output, true));
        assert!(drain_terminal_tail(&mut relay, Duration::from_secs(1)).await);
        observed.set_nonblocking(true).unwrap();
        let mut observed = UnixStream::from_std(observed).unwrap();
        let mut tail = Vec::new();
        tokio::time::timeout(Duration::from_secs(1), observed.read_to_end(&mut tail))
            .await
            .unwrap()
            .unwrap();
        assert_eq!(tail, b"desktop startup failed\n");
    }

    #[tokio::test]
    async fn retained_terminal_writer_cannot_extend_output_drain_deadline() {
        let mut relay = tokio::spawn(async {
            std::future::pending::<()>().await;
            Ok::<(), String>(())
        });
        assert!(!drain_terminal_tail(&mut relay, Duration::from_millis(10)).await);
        assert!(!relay.is_finished());
        relay.abort();
        assert!(relay.await.unwrap_err().is_cancelled());
    }

    #[test]
    fn floating_entry_selects_keyboard_focus_separately_from_setting_its_title() {
        let temporary = tempfile::tempdir().unwrap();
        let tmux = temporary.path().join("tmux");
        // Native cmd-select-pane returns immediately for -T. Model that
        // externally visible behavior with a real child process, so the old
        // title-only startup fails this regression without a focus side effect.
        fs::write(
            &tmux,
            r#"#!/bin/sh
[ "$1" = "-S" ] && [ "$#" -ge 3 ] || exit 2
shift 2
case "$1" in
  new-pane) printf '%s\n' '%7' ;;
  select-pane)
    target=''
    shift
    while [ "$#" -gt 0 ]; do
      case "$1" in
        -T) exit 0 ;;
        -t) shift; target="$1" ;;
      esac
      shift
    done
    printf '%s' "$target" > "${0}.focus"
    ;;
  display-message) printf '%s\n' '100|32' ;;
esac
"#,
        )
        .unwrap();
        fs::set_permissions(&tmux, fs::Permissions::from_mode(0o755)).unwrap();
        let (mut policy, _) = fixture();
        policy.tmux = tmux.clone();
        spawn_entry(&policy, Path::new("/unused/socket"), "%0", "/dev/pts/99").unwrap();
        let mut focus_file = tmux.into_os_string();
        focus_file.push(".focus");
        assert_eq!(fs::read_to_string(PathBuf::from(focus_file)).unwrap(), "%7");
    }

    #[test]
    fn private_backend_requires_all_reviewed_capabilities_and_no_debug_logging() {
        let revision = "08efe60feceea8c81f9571c666880ff1e1c2e3ff";
        let valid = serde_json::json!({
            "protocol":"mios-greetd-remote-v1", "source_revision":revision,
            "pam_rhost_before_auth":true, "pam_rhost_before_account":true,
            "pam_rhost_before_session":true, "remote_required_option":true,
            "default_session_service":true, "debug_logging":false
        });
        assert!(
            validate_backend_capabilities(&serde_json::to_vec(&valid).unwrap(), revision).is_ok()
        );
        for key in [
            "pam_rhost_before_auth",
            "pam_rhost_before_account",
            "pam_rhost_before_session",
            "remote_required_option",
            "default_session_service",
        ] {
            let mut value = valid.clone();
            value[key] = false.into();
            assert!(
                validate_backend_capabilities(&serde_json::to_vec(&value).unwrap(), revision)
                    .is_err(),
                "missing capability {key}"
            );
        }
        for (key, value) in [
            ("debug_logging", serde_json::json!(true)),
            ("source_revision", serde_json::json!("unreviewed")),
            ("protocol", serde_json::json!("unreviewed")),
            ("extra_capability", serde_json::json!(true)),
        ] {
            let mut capabilities = valid.clone();
            capabilities[key] = value;
            assert!(validate_backend_capabilities(
                &serde_json::to_vec(&capabilities).unwrap(),
                revision
            )
            .is_err());
        }
    }

    #[test]
    fn ssh_origin_requires_correlated_logind_process_and_remote_record_facts() {
        let session = "142";
        let client = LoginProcess {
            id: ProcessIdentity {
                pid: 123,
                start: 900,
                uid: 1000,
            },
            state: 'S',
            cgroup: "0::/user.slice/user-1000.slice/session-142.scope\n".into(),
        };
        let leader = LoginProcess {
            id: ProcessIdentity {
                pid: 122,
                start: 899,
                uid: 0,
            },
            state: 'S',
            cgroup: client.cgroup.clone(),
        };
        let fields = [
            ("UID", "1000"),
            ("LEADER", "122"),
            ("REMOTE", "1"),
            ("SERVICE", "sshd"),
            ("STATE", "active"),
            ("CLASS", "user"),
            ("SCOPE", "session-142.scope"),
            ("REMOTE_HOST", "192.0.2.44"),
        ]
        .into_iter()
        .map(|(key, value)| (key.to_owned(), value.to_owned()))
        .collect::<BTreeMap<_, _>>();
        assert_eq!(
            validate_ssh_origin(1000, &client, &leader, session, &fields).unwrap(),
            "192.0.2.44"
        );
        for (key, value) in [
            ("UID", "1001"),
            ("LEADER", "999"),
            ("REMOTE", "0"),
            ("SERVICE", "greetd"),
            ("STATE", "closing"),
            ("CLASS", "greeter"),
            ("SCOPE", "session-143.scope"),
            ("SEAT", "seat0"),
            ("VTNR", "1"),
            ("REMOTE_HOST", "localhost."),
        ] {
            let mut changed = fields.clone();
            changed.insert(key.into(), value.into());
            assert!(
                validate_ssh_origin(1000, &client, &leader, session, &changed).is_err(),
                "foreign field {key}"
            );
        }
        let mut different = client.clone();
        different.id.uid = 1001;
        assert!(validate_ssh_origin(1000, &different, &leader, session, &fields).is_err());
        different = client.clone();
        different.cgroup = "0::/user.slice/user-1000.slice/session-143.scope\n".into();
        assert!(validate_ssh_origin(1000, &different, &leader, session, &fields).is_err());
        let mut dead_leader = leader.clone();
        dead_leader.state = 'Z';
        assert!(validate_ssh_origin(1000, &client, &dead_leader, session, &fields).is_err());
        dead_leader = leader.clone();
        dead_leader.id.start = 0;
        assert!(validate_ssh_origin(1000, &client, &dead_leader, session, &fields).is_err());
        // The selected account uses its genuine dedicated PAM service, never
        // an SSH service alias or an environment assertion of remote status.
        let mut selected = fields.clone();
        selected.insert("SERVICE".into(), "mios-remote-login".into());
        assert!(validate_remote_session_facts(
            1000,
            &client,
            &leader,
            session,
            &selected,
            "mios-remote-login"
        )
        .is_ok());
        assert!(validate_ssh_origin(1000, &client, &leader, session, &selected).is_err());
        selected.insert("REMOTE".into(), "0".into());
        assert!(validate_remote_session_facts(
            1000,
            &client,
            &leader,
            session,
            &selected,
            "mios-remote-login"
        )
        .is_err());
        let mut missing = fields.clone();
        missing.remove("REMOTE_HOST");
        assert!(validate_ssh_origin(1000, &client, &leader, session, &missing).is_err());
        for host in [
            "",
            "localhost",
            "127.0.0.1",
            "::",
            "::1",
            "bad\nhost",
            "bad host",
            "\"192.0.2.44\"",
        ] {
            assert!(!valid_remote_host(host));
        }
    }

    fn fixture() -> (Policy, Witness) {
        let doc: Value = include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .map_err(|_| "vendor SSOT fixture must be valid TOML")
            .unwrap();
        let p = Policy {
            tmux: "/unused/tmux".into(),
            sudo: "/unused/sudo".into(),
            runuser: "/unused/runuser".into(),
            daemon: "/unused/greetd".into(),
            daemon_sha256: "0".repeat(64),
            daemon_revision: "0".repeat(40),
            scope_runner: "/unused/systemd-run".into(),
            greeter: "/unused/tuigreet".into(),
            executable: "/unused/miosd".into(),
            state: "/unused/state".into(),
            pam_service: "mios-remote-login".into(),
            greeter_pam_service: "mios-remote-greeter".into(),
            session: vec![
                "/unused/miosd".into(),
                "sessions".into(),
                "--root".into(),
                "/".into(),
            ],
            language: "en_US.UTF-8".into(),
            min_uid: 1000,
            max_uid: 60000,
            command_timeout: Duration::from_millis(500),
            startup_timeout: Duration::from_secs(1),
            auth_timeout: Duration::from_secs(1),
            limit: 65536,
            popup: PopupPolicy::from_value(&doc).unwrap(),
            greeter_theme: "text=white".into(),
            greeter_width: 60,
            greeter_padding: [1, 1, 1],
            title: "Fixture login".into(),
            greeting: "Fixture greeting".into(),
            background: "Fixture portal".into(),
            failure_return: "Fixture desktop did not start. Enter or Esc to return.".into(),
        };
        let w = Witness {
            caller: Account {
                uid: 1000,
                gid: 1000,
                name: "fixture-user".into(),
                home: "/unused/home".into(),
            },
            remote_host: "192.0.2.44".into(),
            socket: "/unused/socket".into(),
            socket_dev: 0,
            socket_ino: 0,
            pane: "%99".into(),
            client: "/dev/pts/99".into(),
            client_pid: 99998,
            client_start: 0,
            client_tty_identity: (0, 0, 0),
            entry_pid: 99999,
            entry_start: 0,
            tty: "/dev/pts/99".into(),
        };
        (p, w)
    }

    fn input_boundary_test_pty() -> (File, File) {
        let (mut master, mut slave) = (-1, -1);
        assert_eq!(
            unsafe {
                libc::openpty(
                    &mut master,
                    &mut slave,
                    std::ptr::null_mut(),
                    std::ptr::null(),
                    std::ptr::null(),
                )
            },
            0
        );
        for fd in [master, slave] {
            assert_eq!(
                unsafe { libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC) },
                0
            );
        }
        unsafe { (File::from_raw_fd(master), File::from_raw_fd(slave)) }
    }

    fn input_boundary_ready(fd: i32, timeout_ms: i32) -> bool {
        let mut poll = libc::pollfd {
            fd,
            events: libc::POLLIN,
            revents: 0,
        };
        let ready = unsafe { libc::poll(&mut poll, 1, timeout_ms) };
        assert!(ready >= 0);
        ready == 1 && poll.revents & libc::POLLIN != 0
    }

    #[test]
    fn new_terminal_input_owner_discards_only_prearm_typeahead() {
        let (mut master, slave) = input_boundary_test_pty();
        master.write_all(b"stale-login\n").unwrap();
        assert!(input_boundary_ready(slave.as_raw_fd(), 1000));
        let _owner = TerminalRaw::arm_fd(slave.as_raw_fd()).unwrap();
        assert!(!input_boundary_ready(slave.as_raw_fd(), 0));
    }

    #[test]
    fn new_terminal_input_owner_keeps_input_typed_after_ready() {
        let (mut master, mut slave) = input_boundary_test_pty();
        let _owner = TerminalRaw::arm_fd(slave.as_raw_fd()).unwrap();
        master.write_all(b"r").unwrap();
        assert!(input_boundary_ready(slave.as_raw_fd(), 1000));
        let mut actual = [0];
        slave.read_exact(&mut actual).unwrap();
        assert_eq!(&actual, b"r");
        assert!(!input_boundary_ready(slave.as_raw_fd(), 0));
    }

    #[test]
    fn remote_state_search_mode_survives_restrictive_child_umask() {
        const CHILD: &str = "MIOS_REMOTE_LOGIN_MODE_TEST_CHILD";
        if std::env::var_os(CHILD).as_deref() == Some(std::ffi::OsStr::new("1")) {
            let temporary = tempfile::tempdir().unwrap();
            let state = temporary.path().join("remote-state");
            let nonce = state.join("private-instance");
            fs::DirBuilder::new().mode(0o711).create(&state).unwrap();
            fs::DirBuilder::new().mode(0o711).create(&nonce).unwrap();
            // This proves the child actually inherited umask077; it does not
            // merely assert the requested mode or mock chmod's outcome.
            for directory in [&state, &nonce] {
                assert_eq!(fs::metadata(directory).unwrap().mode() & 0o7777, 0o700);
            }
            let uid = unsafe { libc::getuid() };
            for directory in [&state, &nonce] {
                set_search_directory_permissions(directory, uid).unwrap();
                assert_eq!(fs::metadata(directory).unwrap().mode() & 0o7777, 0o711);
            }
            let config = nonce.join("greetd.conf");
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .open(&config)
                .unwrap();
            assert_eq!(fs::metadata(&config).unwrap().mode() & 0o7777, 0o600);
            assert!(set_search_directory_permissions(&nonce, uid.wrapping_add(1)).is_err());
            let link = temporary.path().join("state-link");
            std::os::unix::fs::symlink(&nonce, &link).unwrap();
            assert!(set_search_directory_permissions(&link, uid).is_err());
            return;
        }
        let mut child = Command::new(std::env::current_exe().unwrap());
        child
            .args([
                "--exact",
                "remote_login::tests::remote_state_search_mode_survives_restrictive_child_umask",
                "--nocapture",
            ])
            .env(CHILD, "1");
        // SAFETY: umask is async-signal-safe and executes only in the owned
        // forked child, before exec; parallel tests retain their original mask.
        unsafe {
            child.pre_exec(|| {
                libc::umask(0o077);
                Ok(())
            });
        }
        let output = child.output().unwrap();
        assert!(
            output.status.success(),
            "mode-proof child failed: {}",
            String::from_utf8_lossy(&output.stderr)
        );
        assert!(
            String::from_utf8_lossy(&output.stdout).contains("1 passed"),
            "mode-proof child did not run its exact test"
        );
    }

    #[test]
    fn successful_control_parent_cannot_wait_for_descendant_pipe_eof() {
        let (mut p, _) = fixture();
        p.command_timeout = Duration::from_secs(1);
        let temporary = tempfile::tempdir().unwrap();
        let receipt = temporary.path().join("holder.identity");
        let mut command = Command::new("/bin/sh");
        command.args(["-c", r#"receipt=$1; /usr/bin/sleep 30 & holder=$!; set -- $(/usr/bin/cat /proc/$holder/stat); shift 21; printf '%s %s\n' "$holder" "$1" >"$receipt"; /usr/bin/cat "$receipt""#, "mios-pipe-holder"])
            .arg(&receipt);
        let began = Instant::now();
        let result = bounded(&mut command, &p);
        let elapsed = began.elapsed();
        // Cleanup is independent of capture's success, including a failed
        // regression that times out after the direct shell already exited.
        if let Ok(identity) = fs::read_to_string(&receipt) {
            let fields: Vec<_> = identity.split_whitespace().collect();
            if fields.len() == 2 {
                if let (Ok(pid), Ok(start)) = (fields[0].parse(), fields[1].parse()) {
                    signal_owned(
                        ProcessIdentity {
                            pid,
                            start,
                            uid: unsafe { libc::getuid() },
                        },
                        libc::SIGKILL,
                    );
                }
            }
        }
        let output = result.unwrap();
        assert_eq!(output.split_whitespace().count(), 2);
        assert!(
            elapsed < Duration::from_secs(2),
            "capture waited for inherited EOF"
        );
    }

    #[tokio::test]
    async fn synchronous_phase_overrun_cannot_forward_privileged_frame() {
        let deadline = tokio::time::Instant::now() + Duration::from_millis(10);
        let (mut writer, reader) = UnixStream::pair().unwrap();
        // No yield: a timeout alone cannot interrupt this terminal phase.
        std::thread::sleep(Duration::from_millis(20));
        assert!(forward_admitted_frame(
            Some(deadline),
            &mut writer,
            br#"{"type":"start_session","cmd":["fixture"]}"#
        )
        .await
        .is_err());
        let mut byte = [0];
        assert_eq!(
            reader.try_read(&mut byte).unwrap_err().kind(),
            std::io::ErrorKind::WouldBlock
        );
    }

    #[tokio::test]
    async fn accept_first_consumes_startup_generation_before_authenticated_phase() {
        let (events, mut startup) = tokio::sync::watch::channel(None);
        let first = ProcessIdentity {
            pid: 41,
            start: 100,
            uid: 1000,
        };
        events.send_replace(Some(first));
        let mut handled = None;
        // Both accept and changed are ready; the accept branch consumes the
        // same helper used by the lifecycle branch before processing login.
        assert!(take_greeter_generation(&mut startup, &mut handled).is_some());
        assert!(!startup.has_changed().unwrap());
        let mut phase = Phase::Starting;
        // An already queued/repeated startup notification is not a new login.
        events.send_replace(Some(first));
        startup.changed().await.unwrap();
        if take_greeter_generation(&mut startup, &mut handled).is_some() {
            phase = Phase::Idle;
        }
        assert!(phase == Phase::Starting);
        // PID reuse with a different kernel start time is a real generation.
        events.send_replace(Some(ProcessIdentity {
            start: 200,
            ..first
        }));
        startup.changed().await.unwrap();
        if take_greeter_generation(&mut startup, &mut handled).is_some() {
            phase = Phase::Idle;
        }
        assert!(phase == Phase::Idle);
    }

    async fn session_geometry_grant_and_revoke(regrant_same_account: bool) {
        let temporary = tempfile::tempdir().unwrap();
        let path = temporary.path().join("session-size.sock");
        let listener = UnixListener::bind(&path).unwrap();
        let uid = unsafe { libc::getuid() };
        let broker = process_identity(std::process::id()).unwrap();
        let first = Dimensions {
            columns: 80,
            rows: 24,
        };
        let next = Dimensions {
            columns: 100,
            rows: 32,
        };
        let (owner, allowed) = tokio::sync::watch::channel(None);
        let (dimensions, changes) = tokio::sync::watch::channel(first);
        let (connected, _) = tokio::sync::watch::channel(None);
        // The authenticated account is granted after listener creation, before
        // its real socket connection; this notification is still unseen.
        owner.send_replace(Some(uid));
        assert!(allowed.has_changed().unwrap());
        let server = tokio::spawn(serve_geometry(
            listener,
            allowed,
            changes,
            std::env::current_exe().unwrap(),
            Duration::from_secs(2),
            broker,
            connected,
        ));
        let mut stream = UnixStream::connect(&path).await.unwrap();
        let frame = tokio::time::timeout(Duration::from_secs(2), read_frame(&mut stream, 1024))
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        assert_eq!(
            serde_json::from_slice::<Dimensions>(&frame.0).unwrap(),
            first
        );
        dimensions.send_replace(next);
        let frame = tokio::time::timeout(Duration::from_secs(2), read_frame(&mut stream, 1024))
            .await
            .unwrap()
            .unwrap();
        // The old code closes here from its already-unseen grant notification.
        assert!(frame.is_some());
        let frame = frame.unwrap();
        assert_eq!(
            serde_json::from_slice::<Dimensions>(&frame.0).unwrap(),
            next
        );
        // Every subsequent ownership generation revokes the old connection,
        // including another grant to the same UID. No sleeps or grace period.
        owner.send_replace(regrant_same_account.then_some(uid));
        assert!(
            tokio::time::timeout(Duration::from_secs(2), read_frame(&mut stream, 1024))
                .await
                .unwrap()
                .unwrap()
                .is_none()
        );
        server.abort();
        let _ = server.await;
    }

    #[tokio::test]
    async fn session_geometry_initial_grant_survives_updates_then_logout_revokes() {
        session_geometry_grant_and_revoke(false).await;
    }

    #[tokio::test]
    async fn session_geometry_same_account_regrant_revokes_the_previous_lease() {
        session_geometry_grant_and_revoke(true).await;
    }

    #[test]
    fn explicit_client_snapshot_keeps_sessions_separate() {
        let rows = "/dev/pts/7|71|$2|@8|/dev/pts/7\n/dev/pts/3|31|$1|@4|/dev/pts/3";
        let client = exact_client(rows, "/dev/pts/7").unwrap();
        assert_eq!(
            (client.pid, client.session.as_str(), client.window.as_str()),
            (71, "$2", "@8")
        );
        let reordered = "/dev/pts/3|31|$1|@4|/dev/pts/3\n/dev/pts/7|71|$2|@8|/dev/pts/7";
        assert_eq!(exact_client(reordered, "/dev/pts/7").unwrap().window, "@8");
        assert!(exact_client(
            &format!("{rows}\n/dev/pts/7|72|$3|@9|/dev/pts/7"),
            "/dev/pts/7"
        )
        .is_err());
    }

    #[tokio::test]
    async fn absolute_auth_deadline_spans_reconnect_and_bounds_accept_and_write() {
        let deadline = tokio::time::Instant::now() + Duration::from_millis(500);
        before_auth_deadline(Some(deadline), async {
            tokio::time::sleep(Duration::from_millis(10)).await;
            Ok(())
        })
        .await
        .unwrap();
        let (mut reconnect, _nonwriting_peer) = UnixStream::pair().unwrap();
        assert!(
            before_auth_deadline(Some(deadline), read_frame(&mut reconnect, 4096))
                .await
                .is_err()
        );
        assert!(tokio::time::Instant::now() >= deadline);
        let temporary = tempfile::tempdir().unwrap();
        let listener = UnixListener::bind(temporary.path().join("login.sock")).unwrap();
        assert!(before_auth_deadline(
            Some(tokio::time::Instant::now() + Duration::from_millis(20)),
            async {
                listener
                    .accept()
                    .await
                    .map_err(|_| "fixture accept failed".to_owned())
            }
        )
        .await
        .is_err());
        let (mut output, _nonreading_peer) = UnixStream::pair().unwrap();
        let bytes = vec![0; 4 * 1024 * 1024];
        assert!(before_auth_deadline(
            Some(tokio::time::Instant::now() + Duration::from_millis(20)),
            write_frame(&mut output, &bytes)
        )
        .await
        .is_err());
    }

    #[tokio::test]
    async fn wire_frames_use_greetd_native_length_and_reject_oversized_input() {
        let (mut a, mut b) = UnixStream::pair().unwrap();
        write_frame(&mut a, br#"{"type":"cancel_session"}"#)
            .await
            .unwrap();
        let f = read_frame(&mut b, 4096).await.unwrap().unwrap();
        assert_eq!(f.0, br#"{"type":"cancel_session"}"#);
        a.write_all(&4097u32.to_ne_bytes()).await.unwrap();
        assert!(read_frame(&mut b, 4096).await.is_err());
    }

    #[tokio::test]
    async fn reconnect_rejected_root_cancel_unread_reply_keeps_service_available() {
        let (p, w) = fixture();
        let temporary = tempfile::tempdir().unwrap();
        let path = temporary.path().join("frontend.sock");
        let listener = UnixListener::bind(&path).unwrap();
        let (mut backend_one, mut daemon_one) = UnixStream::pair().unwrap();
        let (mut backend_two, mut daemon_two) = UnixStream::pair().unwrap();
        let (closed, after_close) = tokio::sync::oneshot::channel();
        let daemon = tokio::spawn(async move {
            let cancel = read_frame(&mut daemon_one, 4096).await.unwrap().unwrap();
            assert_eq!(cancel.0, br#"{"type":"cancel_session"}"#);
            // Actual TUIGREET cancels then closes without reading this reply.
            after_close.await.unwrap();
            write_frame(&mut daemon_one, br#"{"type":"success"}"#)
                .await
                .unwrap();
            let cancel = read_frame(&mut daemon_two, 4096).await.unwrap().unwrap();
            assert_eq!(cancel.0, br#"{"type":"cancel_session"}"#);
            write_frame(&mut daemon_two, br#"{"type":"success"}"#)
                .await
                .unwrap();
        });
        let service = tokio::spawn(async move {
            let geometry = GeometryControl::fixture();
            let mut state =
                AuthState::new(Some(tokio::time::Instant::now() + Duration::from_secs(5)));
            let (mut frontend, _) = listener.accept().await.unwrap();
            let first = serve_frontend(
                &p,
                &w,
                &mut state,
                &mut frontend,
                &mut backend_one,
                &geometry,
            )
            .await;
            assert!(first.is_ok());
            assert!(state.phase == Phase::Idle && state.target.is_none());
            let (mut frontend, _) = listener.accept().await.unwrap();
            serve_frontend(
                &p,
                &w,
                &mut state,
                &mut frontend,
                &mut backend_two,
                &geometry,
            )
            .await
            .unwrap();
            assert!(state.phase == Phase::Idle && state.target.is_none());
        });
        let mut first = UnixStream::connect(&path).await.unwrap();
        write_frame(
            &mut first,
            br#"{"type":"create_session","username":"root"}"#,
        )
        .await
        .unwrap();
        let refused = read_frame(&mut first, 4096).await.unwrap().unwrap();
        let reply: serde_json::Value = serde_json::from_slice(&refused.0).unwrap();
        assert_eq!(reply["error_type"], "error");
        write_frame(&mut first, br#"{"type":"cancel_session"}"#)
            .await
            .unwrap();
        drop(first);
        closed.send(()).unwrap();
        let mut second = UnixStream::connect(&path).await.unwrap();
        write_frame(&mut second, br#"{"type":"cancel_session"}"#)
            .await
            .unwrap();
        let ready = tokio::time::timeout(Duration::from_secs(2), read_frame(&mut second, 4096))
            .await
            .unwrap()
            .unwrap()
            .unwrap();
        let reply: serde_json::Value = serde_json::from_slice(&ready.0).unwrap();
        assert_eq!(reply["type"], "success");
        drop(second);
        tokio::time::timeout(Duration::from_secs(2), service)
            .await
            .unwrap()
            .unwrap();
        daemon.await.unwrap();
    }

    #[tokio::test]
    async fn reconnect_frontend_reset_is_local_but_backend_and_length_errors_stay_fatal() {
        let (mut frontend, client) = UnixStream::pair().unwrap();
        write_frame(&mut frontend, br#"{"type":"success"}"#)
            .await
            .unwrap();
        drop(client);
        assert!(read_frontend_frame(&mut frontend, 4096)
            .await
            .unwrap()
            .is_none());
        let (mut backend, client) = UnixStream::pair().unwrap();
        write_frame(&mut backend, br#"{"type":"success"}"#)
            .await
            .unwrap();
        drop(client);
        assert!(read_frame(&mut backend, 4096).await.is_err());
        let (mut frontend, mut client) = UnixStream::pair().unwrap();
        client.write_all(&4097u32.to_ne_bytes()).await.unwrap();
        assert!(read_frontend_frame(&mut frontend, 4096).await.is_err());
    }

    #[tokio::test]
    async fn reconnect_backend_failure_is_not_a_disposable_frontend() {
        let (p, w) = fixture();
        let (mut frontend, mut client) = UnixStream::pair().unwrap();
        let (mut backend, daemon) = UnixStream::pair().unwrap();
        drop(daemon);
        write_frame(&mut client, br#"{"type":"cancel_session"}"#)
            .await
            .unwrap();
        let mut state = AuthState::new(None);
        assert!(serve_frontend(
            &p,
            &w,
            &mut state,
            &mut frontend,
            &mut backend,
            &GeometryControl::fixture()
        )
        .await
        .is_err());
    }

    #[tokio::test]
    async fn reconnect_running_session_survives_its_greeter_frontend_exit() {
        let (p, w) = fixture();
        let (mut frontend, client) = UnixStream::pair().unwrap();
        let (mut backend, daemon) = UnixStream::pair().unwrap();
        drop(client);
        let mut state = AuthState::new(None);
        state.phase = Phase::Starting;
        serve_frontend(
            &p,
            &w,
            &mut state,
            &mut frontend,
            &mut backend,
            &GeometryControl::fixture(),
        )
        .await
        .unwrap();
        assert!(state.phase == Phase::Starting);
        let mut byte = [0];
        assert_eq!(
            daemon.try_read(&mut byte).unwrap_err().kind(),
            std::io::ErrorKind::WouldBlock
        );
    }

    #[tokio::test]
    async fn reconnect_abandoned_challenge_is_cancelled_before_another_frontend() {
        let (p, w) = fixture();
        let (mut frontend, client) = UnixStream::pair().unwrap();
        let (mut backend, mut daemon) = UnixStream::pair().unwrap();
        drop(client);
        let cancelled = tokio::spawn(async move {
            let request = read_frame(&mut daemon, 4096).await.unwrap().unwrap();
            assert_eq!(request.0, br#"{"type":"cancel_session"}"#);
            write_frame(&mut daemon, br#"{"type":"success"}"#)
                .await
                .unwrap();
        });
        let mut state = AuthState::new(Some(tokio::time::Instant::now() + Duration::from_secs(2)));
        state.phase = Phase::Challenge;
        serve_frontend(
            &p,
            &w,
            &mut state,
            &mut frontend,
            &mut backend,
            &GeometryControl::fixture(),
        )
        .await
        .unwrap();
        assert!(state.phase == Phase::Idle && state.target.is_none());
        cancelled.await.unwrap();
    }

    #[tokio::test]
    async fn unauthenticated_start_and_unsolicited_response_never_reach_backend() {
        let (p, w) = fixture();
        for raw in [
            br#"{"type":"start_session","cmd":["/bin/sh"],"env":["XDG_SEAT=seat0"]}"#.as_slice(),
            br#"{"type":"post_auth_message_response","response":"fixture-input"}"#.as_slice(),
            br#"{"type":"create_session","username":"root"}"#.as_slice(),
        ] {
            let (mut frontend, mut ui) = UnixStream::pair().unwrap();
            let (mut backend, attempted_backend) = UnixStream::pair().unwrap();
            let mut state = AuthState::new(None);
            proxy_request(
                &p,
                &w,
                &mut state,
                &mut frontend,
                &mut backend,
                raw,
                &GeometryControl::fixture(),
            )
            .await
            .unwrap();
            let reply = read_frame(&mut ui, p.limit).await.unwrap().unwrap();
            let json: serde_json::Value = serde_json::from_slice(&reply.0).unwrap();
            assert_eq!(
                json.get("type").and_then(serde_json::Value::as_str),
                Some("error")
            );
            let mut byte = [0];
            assert_eq!(
                attempted_backend.try_read(&mut byte).unwrap_err().kind(),
                std::io::ErrorKind::WouldBlock
            );
            assert!(state.phase == Phase::Idle);
        }
    }

    #[tokio::test]
    async fn malformed_protocol_is_not_forwarded_or_echoed() {
        let (p, w) = fixture();
        let (mut frontend, _) = UnixStream::pair().unwrap();
        let (mut backend, attempted_backend) = UnixStream::pair().unwrap();
        let error = proxy_request(
            &p,
            &w,
            &mut AuthState::new(None),
            &mut frontend,
            &mut backend,
            br#"{"type":12,"response":"fixture-private-input"}"#,
            &GeometryControl::fixture(),
        )
        .await
        .unwrap_err();
        assert!(!error.contains("fixture-private-input"));
        let mut byte = [0];
        assert_eq!(
            attempted_backend.try_read(&mut byte).unwrap_err().kind(),
            std::io::ErrorKind::WouldBlock
        );
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
struct Dimensions {
    columns: u16,
    rows: u16,
}
impl Dimensions {
    fn current() -> Self {
        let (columns, rows) = terminal_size();
        Self { columns, rows }
    }
    fn valid(self) -> bool {
        self.columns > 0 && self.rows > 0 && self.columns <= 4096 && self.rows <= 4096
    }
}

#[derive(Clone)]
struct GeometryControl {
    session_path: PathBuf,
    session_identity: (u64, u64),
    session_uid: tokio::sync::watch::Sender<Option<u32>>,
    dimensions: tokio::sync::watch::Sender<Dimensions>,
    greeter_started: tokio::sync::watch::Sender<Option<ProcessIdentity>>,
    session_started: tokio::sync::watch::Sender<Option<ProcessIdentity>>,
}
impl GeometryControl {
    fn publish(&self) {
        let dimensions = Dimensions::current();
        if *self.dimensions.borrow() != dimensions {
            self.dimensions.send_replace(dimensions);
        }
    }
    fn session_owner(&self, account: Option<&Account>) -> Result<(), String> {
        let m = fs::symlink_metadata(&self.session_path)
            .map_err(|_| "session geometry channel disappeared")?;
        if !m.file_type().is_socket() || (m.dev(), m.ino()) != self.session_identity {
            return Err("session geometry channel identity changed".into());
        }
        let (uid, gid) = account.map(|a| (a.uid, a.gid)).unwrap_or((0, 0));
        let path = CString::new(self.session_path.as_os_str().as_encoded_bytes())
            .map_err(|_| "invalid session geometry path")?;
        // The pathname is beneath a verified root-owned, non-writable directory;
        // no caller can replace this witnessed socket while root changes owner.
        if unsafe { libc::chown(path.as_ptr(), uid, gid) } != 0 {
            return Err("cannot assign session geometry channel".into());
        }
        fs::set_permissions(&self.session_path, fs::Permissions::from_mode(0o600))
            .map_err(|_| "cannot protect session geometry channel")?;
        let after = fs::symlink_metadata(&self.session_path)
            .map_err(|_| "session geometry channel disappeared")?;
        if (after.dev(), after.ino()) != self.session_identity
            || after.uid() != uid
            || after.mode() & 0o7777 != 0o600
        {
            return Err("session geometry ownership changed unexpectedly".into());
        }
        self.session_started.send_replace(None);
        self.session_uid.send_replace(account.map(|a| a.uid));
        Ok(())
    }
    #[cfg(test)]
    fn fixture() -> Self {
        let (session_uid, _) = tokio::sync::watch::channel(None);
        let (dimensions, _) = tokio::sync::watch::channel(Dimensions {
            columns: 80,
            rows: 24,
        });
        let (greeter_started, _) = tokio::sync::watch::channel(None);
        let (session_started, _) = tokio::sync::watch::channel(None);
        Self {
            session_path: "/unused/geometry".into(),
            session_identity: (0, 0),
            session_uid,
            dimensions,
            greeter_started,
            session_started,
        }
    }
}
struct GeometryTasks {
    greeter: tokio::task::JoinHandle<Result<(), String>>,
    session: tokio::task::JoinHandle<Result<(), String>>,
}
impl GeometryTasks {
    async fn failed(&mut self) {
        tokio::select! { _ = &mut self.greeter => (), _ = &mut self.session => () }
    }
}
impl Drop for GeometryTasks {
    fn drop(&mut self) {
        self.greeter.abort();
        self.session.abort();
    }
}
impl PrivateState {
    fn geometry_services(
        &mut self,
        p: &Policy,
        w: &Witness,
    ) -> Result<(GeometryControl, GeometryTasks), String> {
        let greeter_path = self.greeter_geometry.clone();
        let session_path = self.session_geometry.clone();
        let mut bind = |path: &Path, uid: u32, gid: u32| -> Result<UnixListener, String> {
            let listener =
                UnixListener::bind(path).map_err(|_| "cannot bind private geometry channel")?;
            let m = fs::symlink_metadata(path).map_err(|_| "cannot identify geometry channel")?;
            self.geometry_identities
                .push((path.to_owned(), m.dev(), m.ino()));
            let c = CString::new(path.as_os_str().as_encoded_bytes())
                .map_err(|_| "invalid geometry path")?;
            if unsafe { libc::chown(c.as_ptr(), uid, gid) } != 0 {
                return Err("cannot assign geometry channel".into());
            }
            fs::set_permissions(path, fs::Permissions::from_mode(0o600))
                .map_err(|_| "cannot protect geometry channel")?;
            Ok(listener)
        };
        let greeter = bind(&greeter_path, w.caller.uid, w.caller.gid)?;
        let session = bind(&session_path, 0, 0)?;
        let m = fs::symlink_metadata(&self.session_geometry)
            .map_err(|_| "cannot identify session geometry")?;
        let (session_uid, session_allowed) = tokio::sync::watch::channel(None);
        let (greeter_uid, greeter_allowed) = tokio::sync::watch::channel(Some(w.caller.uid));
        let (dimensions, _) = tokio::sync::watch::channel(Dimensions::current());
        let (greeter_started, _) = tokio::sync::watch::channel(None);
        let (session_started, _) = tokio::sync::watch::channel(None);
        let control = GeometryControl {
            session_path: self.session_geometry.clone(),
            session_identity: (m.dev(), m.ino()),
            session_uid,
            dimensions,
            greeter_started,
            session_started,
        };
        let broker =
            process_identity(std::process::id()).ok_or("geometry broker identity unavailable")?;
        let executable = p.executable.clone();
        let changes = control.dimensions.subscribe();
        let timeout = p.command_timeout;
        let connected = control.greeter_started.clone();
        let greeter_task = tokio::spawn(async move {
            // Keep the fixed caller lease alive for the listener's lifetime.
            let _lease = greeter_uid;
            serve_geometry(
                greeter,
                greeter_allowed,
                changes,
                executable,
                timeout,
                broker,
                connected,
            )
            .await
        });
        let executable = p.executable.clone();
        let changes = control.dimensions.subscribe();
        let connected = control.session_started.clone();
        let session_task = tokio::spawn(async move {
            serve_geometry(
                session,
                session_allowed,
                changes,
                executable,
                timeout,
                broker,
                connected,
            )
            .await
        });
        Ok((
            control,
            GeometryTasks {
                greeter: greeter_task,
                session: session_task,
            },
        ))
    }
}
async fn serve_geometry(
    listener: UnixListener,
    allowed: tokio::sync::watch::Receiver<Option<u32>>,
    changes: tokio::sync::watch::Receiver<Dimensions>,
    executable: PathBuf,
    timeout: Duration,
    broker: ProcessIdentity,
    connected: tokio::sync::watch::Sender<Option<ProcessIdentity>>,
) -> Result<(), String> {
    let mut clients = tokio::task::JoinSet::new();
    loop {
        tokio::select! {
            accepted = listener.accept() => {
                let (mut stream, _) = accepted.map_err(|_| "geometry listener stopped")?;
                // There is one live bridge per role; allow a brief handover,
                // but do not grow privileged tasks from repeated connections.
                if clients.len() >= 2 { continue; }
                let peer = stream.peer_cred().map_err(|_| "geometry peer unavailable")?;
                // Consume the grant witnessed at admission, so its old unseen
                // notification cannot immediately revoke this new connection.
                // Later changes (even to the same UID) still close the lease.
                let mut allowed = allowed.clone();
                if Some(peer.uid()) != *allowed.borrow_and_update() { continue; }
                let Some(pid) = peer.pid().and_then(|p| u32::try_from(p).ok()) else { continue; };
                let actual = fs::metadata(format!("/proc/{pid}/exe")); let expected = fs::metadata(&executable);
                if !matches!((actual, expected), (Ok(a), Ok(b)) if (a.dev(), a.ino()) == (b.dev(), b.ino())) { continue; }
                let Some(identity) = process_identity(pid) else { continue; };
                if !descendant_of(identity, broker) { continue; }
                connected.send_replace(Some(identity));
                let mut changes = changes.clone(); let uid = peer.uid();
                clients.spawn(async move {
                    loop {
                        if *allowed.borrow() != Some(uid) { return; }
                        let size = *changes.borrow_and_update();
                        let Ok(frame) = serde_json::to_vec(&size) else { return; };
                        if !matches!(tokio::time::timeout(timeout, write_frame(&mut stream, &frame)).await, Ok(Ok(()))) { return; }
                        tokio::select! {
                            r = changes.changed() => if r.is_err() { return; },
                            _ = allowed.changed() => return,
                            ready = stream.readable() => {
                                if ready.is_err() { return; }
                                let mut byte = [0];
                                match stream.try_read(&mut byte) {
                                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => (),
                                    // This is a send-only protocol. EOF and
                                    // any unsolicited bytes close the lease.
                                    _ => return,
                                }
                            },
                        }
                    }
                });
            },
            _ = clients.join_next(), if !clients.is_empty() => (),
        }
    }
}
async fn geometry_connection(p: &Policy, path: &Path, name: &str) -> Result<UnixStream, String> {
    if path.file_name().and_then(|s| s.to_str()) != Some(name)
        || path.parent().and_then(Path::parent) != Some(p.state.as_path())
    {
        return Err("geometry channel is outside its private state".into());
    }
    trusted_directory(path.parent().ok_or("geometry state unavailable")?)?;
    let m = socket_owned(path, unsafe { libc::getuid() })?;
    if m.mode() & 0o7777 != 0o600 {
        return Err("geometry channel must be private to this account".into());
    }
    let stream = tokio::time::timeout(p.startup_timeout, UnixStream::connect(path))
        .await
        .map_err(|_| "geometry channel timed out")?
        .map_err(|_| "geometry channel unavailable")?;
    if stream
        .peer_cred()
        .map_err(|_| "geometry broker unavailable")?
        .uid()
        != 0
    {
        return Err("geometry broker must be root-owned".into());
    }
    Ok(stream)
}

struct TerminalRaw {
    terminal: File,
    saved: libc::termios,
}
impl TerminalRaw {
    fn arm() -> Result<Self, String> {
        Self::arm_fd(0)
    }
    fn arm_fd(fd: i32) -> Result<Self, String> {
        // Pin only this bridge's inherited terminal. Tests use a fresh PTY fd;
        // neither path replaces global stdin or touches another session's TTY.
        let held = unsafe { libc::fcntl(fd, libc::F_DUPFD_CLOEXEC, 3) };
        if held < 0 {
            return Err("remote terminal descriptor unavailable".into());
        }
        let terminal = unsafe { File::from_raw_fd(held) };
        let mut saved: libc::termios = unsafe { std::mem::zeroed() };
        if unsafe { libc::tcgetattr(held, &mut saved) } != 0 {
            return Err("remote stream is not a terminal".into());
        }
        let mut raw = saved;
        unsafe {
            libc::cfmakeraw(&mut raw);
        }
        if unsafe { libc::tcsetattr(held, libc::TCSANOW, &raw) } != 0 {
            return Err("cannot prepare remote terminal stream".into());
        }
        let armed = Self { terminal, saved };
        // This is one ownership boundary, before the new greeter or selected
        // desktop child is spawned. Discard unread typeahead left for the prior
        // owner, without flushing output or changing any active password field.
        // TCSANOW + TCIFLUSH avoids waiting for output flow-control to drain.
        if unsafe { libc::tcflush(held, libc::TCIFLUSH) } != 0 {
            return Err("cannot clear input from the previous terminal owner".into());
        }
        Ok(armed)
    }
}
impl Drop for TerminalRaw {
    fn drop(&mut self) {
        unsafe {
            libc::tcsetattr(self.terminal.as_raw_fd(), libc::TCSANOW, &self.saved);
        }
    }
}
struct BridgeFd {
    io: tokio::io::unix::AsyncFd<File>,
    previous_flags: Option<i32>,
}
impl BridgeFd {
    fn inherited(fd: i32) -> Result<Self, String> {
        let duplicated = unsafe { libc::fcntl(fd, libc::F_DUPFD_CLOEXEC, 3) };
        if duplicated < 0 {
            return Err("remote stream descriptor unavailable".into());
        }
        let file = unsafe { File::from_raw_fd(duplicated) };
        Self::new(file, true)
    }
    fn new(file: File, restore: bool) -> Result<Self, String> {
        let flags = unsafe { libc::fcntl(file.as_raw_fd(), libc::F_GETFL) };
        if flags < 0
            || unsafe { libc::fcntl(file.as_raw_fd(), libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0
        {
            return Err("cannot prepare nonblocking remote stream".into());
        }
        let io = tokio::io::unix::AsyncFd::new(file)
            .map_err(|_| "remote stream readiness unavailable")?;
        Ok(Self {
            io,
            previous_flags: restore.then_some(flags),
        })
    }
    async fn read(&self, buffer: &mut [u8]) -> std::io::Result<usize> {
        loop {
            let mut ready = self.io.readable().await?;
            match ready.try_io(|fd| {
                let count =
                    unsafe { libc::read(fd.as_raw_fd(), buffer.as_mut_ptr().cast(), buffer.len()) };
                if count < 0 {
                    Err(std::io::Error::last_os_error())
                } else {
                    Ok(count as usize)
                }
            }) {
                Ok(result) => return result,
                Err(_) => continue,
            }
        }
    }
    async fn write_all(&self, mut bytes: &[u8]) -> std::io::Result<()> {
        while !bytes.is_empty() {
            let mut ready = self.io.writable().await?;
            match ready.try_io(|fd| {
                let count =
                    unsafe { libc::write(fd.as_raw_fd(), bytes.as_ptr().cast(), bytes.len()) };
                if count < 0 {
                    Err(std::io::Error::last_os_error())
                } else {
                    Ok(count as usize)
                }
            }) {
                Ok(Ok(0)) => return Err(std::io::Error::from(std::io::ErrorKind::WriteZero)),
                Ok(Ok(count)) => bytes = &bytes[count..],
                Ok(Err(error)) => return Err(error),
                Err(_) => (),
            }
        }
        Ok(())
    }
    fn dimensions(&self, dimensions: Dimensions) -> Result<(), String> {
        let size = libc::winsize {
            ws_row: dimensions.rows,
            ws_col: dimensions.columns,
            ws_xpixel: 0,
            ws_ypixel: 0,
        };
        if unsafe { libc::ioctl(self.io.as_raw_fd(), libc::TIOCSWINSZ, &size) } != 0 {
            return Err("cannot resize owned remote PTY".into());
        }
        Ok(())
    }
}
impl Drop for BridgeFd {
    fn drop(&mut self) {
        if let Some(flags) = self.previous_flags {
            unsafe {
                libc::fcntl(self.io.as_raw_fd(), libc::F_SETFL, flags);
            }
        }
    }
}
async fn relay_terminal(
    source: Arc<BridgeFd>,
    target: Arc<BridgeFd>,
    master_read: bool,
) -> Result<(), String> {
    let mut buffer = Frame(vec![0; 8192]);
    loop {
        let count = match source.read(&mut buffer.0).await {
            Ok(0) => return Ok(()),
            Ok(n) => n,
            Err(e) if master_read && e.raw_os_error() == Some(libc::EIO) => return Ok(()),
            Err(_) => return Err("remote terminal stream disconnected".into()),
        };
        target
            .write_all(&buffer.0[..count])
            .await
            .map_err(|_| "remote terminal stream closed")?;
        for byte in &mut buffer.0[..count] {
            unsafe {
                std::ptr::write_volatile(byte, 0);
            }
        }
    }
}
async fn relay_geometry(mut stream: UnixStream, master: Arc<BridgeFd>) -> Result<(), String> {
    let mut previous = None;
    loop {
        let frame = read_frame(&mut stream, 1024)
            .await?
            .ok_or("geometry broker disconnected")?;
        let size: Dimensions =
            serde_json::from_slice(&frame.0).map_err(|_| "invalid remote viewport dimensions")?;
        if !size.valid() {
            return Err("remote viewport dimensions exceeded bounds".into());
        }
        if previous != Some(size) {
            master.dimensions(size)?;
            previous = Some(size);
        }
    }
}
fn signal_owned_group(identity: ProcessIdentity, signal: i32) {
    if process_identity(identity.pid)
        .is_some_and(|p| p.start == identity.start && p.uid == identity.uid)
        && unsafe { libc::getpgid(identity.pid as i32) } == identity.pid as i32
    {
        unsafe {
            libc::kill(-(identity.pid as i32), signal);
        }
    }
}
async fn drain_terminal_tail(
    output: &mut tokio::task::JoinHandle<Result<(), String>>,
    timeout: Duration,
) -> bool {
    tokio::time::timeout(timeout, output).await.is_ok()
}

struct SessionFailureTerminal {
    input: BridgeFd,
    output: BridgeFd,
    _terminal: TerminalRaw,
}
impl SessionFailureTerminal {
    fn pin() -> Result<Self, String> {
        Self::pin_fds(0, 1)
    }
    fn pin_fds(input_fd: i32, output_fd: i32) -> Result<Self, String> {
        // stdin/stdout may share one open file description. Capture both original
        // flag sets before either BridgeFd enables nonblocking IO.
        let output_flags = unsafe { libc::fcntl(output_fd, libc::F_GETFL) };
        if output_flags < 0 {
            return Err("selected session output is unavailable".into());
        }
        let input = BridgeFd::inherited(input_fd)?;
        let mut output = BridgeFd::inherited(output_fd)?;
        output.previous_flags = Some(output_flags);
        let i = input.io.get_ref().metadata().map_err(|_| "selected session input ended")?;
        let o = output.io.get_ref().metadata().map_err(|_| "selected session output ended")?;
        if !i.file_type().is_char_device()
            || !o.file_type().is_char_device()
            || (i.dev(), i.ino(), i.rdev()) != (o.dev(), o.ino(), o.rdev())
        {
            return Err("failure acknowledgement requires one inherited terminal".into());
        }
        // Pin this inherited descriptor; never reopen a TTY by a caller path.
        // Its owner can be the original SSH account when PAM selected another
        // user. Genuine selected-session admission was checked before this call.
        let terminal = TerminalRaw::arm_fd(input.io.as_raw_fd())?;
        Ok(Self { input, output, _terminal: terminal })
    }
}

struct SessionFailureAck {
    hint: String,
    deadline: tokio::time::Instant,
}

fn duplicate_geometry(stream: &UnixStream) -> Result<UnixStream, String> {
    let fd = unsafe { libc::fcntl(stream.as_raw_fd(), libc::F_DUPFD_CLOEXEC, 3) };
    if fd < 0 {
        return Err("cannot retain selected session geometry lifetime".into());
    }
    let stream = unsafe { std::os::unix::net::UnixStream::from_raw_fd(fd) };
    UnixStream::from_std(stream).map_err(|_| "cannot observe selected session geometry lifetime".into())
}

async fn session_failure_cancelled(
    cancelled: impl std::future::Future<Output = ()>,
    geometry: Option<UnixStream>,
) {
    tokio::pin!(cancelled);
    let Some(mut geometry) = geometry else {
        cancelled.await;
        return;
    };
    tokio::select! {
        _ = &mut cancelled => (),
        _ = async {
            // The old relay may have stopped halfway through a size frame, so
            // observe stream lifetime, not a new protocol boundary. No size
            // bytes are interpreted or forwarded during this acknowledgement.
            let mut bytes = Frame(vec![0; 1024]);
            while let Ok(count) = geometry.read(&mut bytes.0).await {
                if count == 0 { break; }
            }
        } => (),
    }
}

async fn acknowledge_session_failure(
    input: &BridgeFd,
    output: &BridgeFd,
    ack: &SessionFailureAck,
    cancelled: impl std::future::Future<Output = ()>,
) {
    if tokio::time::Instant::now() >= ack.deadline {
        return;
    }
    // The failed child and its relays are already stopped. Discard only its old
    // input; unknown new keys cannot acknowledge a different terminal owner.
    if unsafe { libc::tcflush(input.io.as_raw_fd(), libc::TCIFLUSH) } != 0 {
        return;
    }
    tokio::pin!(cancelled);
    // Keep existing child stderr on screen and add only the SSOT generic failure
    // label. Never format arbitrary startup errors, paths or credentials here.
    let hint = format!("\r\n{}\r\n", ack.hint);
    let written = tokio::select! {
        biased;
        _ = &mut cancelled => return,
        _ = tokio::time::sleep_until(ack.deadline) => return,
        result = output.write_all(hint.as_bytes()) => result,
    };
    if written.is_err() {
        return;
    }
    loop {
        let mut bytes = Frame(vec![0; 64]);
        tokio::select! {
            biased;
            _ = &mut cancelled => return,
            _ = tokio::time::sleep_until(ack.deadline) => return,
            received = input.read(&mut bytes.0) => {
                let Ok(count) = received else { return; };
                if count == 0 || bytes.0[..count].iter().any(|b| matches!(b, b'\r' | b'\n' | 0x1b)) {
                    return;
                }
            },
        }
    }
}

async fn hold_session_failure(
    result: Result<(), String>,
    input: &BridgeFd,
    output: &BridgeFd,
    ack: &SessionFailureAck,
    cancelled: impl std::future::Future<Output = ()>,
) -> Result<(), String> {
    if result.is_err() {
        acknowledge_session_failure(input, output, ack, cancelled).await;
    }
    result
}

async fn owned_pty_bridge(
    command: Command,
    geometry: UnixStream,
    shutdown_timeout: Duration,
) -> Result<(), String> {
    let mut cancellation = Cancellation::new()?;
    owned_pty_bridge_with_cancellation(command, geometry, shutdown_timeout, &mut cancellation).await
}

async fn owned_pty_bridge_with_cancellation(
    mut command: Command,
    geometry: UnixStream,
    shutdown_timeout: Duration,
    cancellation: &mut Cancellation,
) -> Result<(), String> {
    let uid = unsafe { libc::getuid() };
    if uid == 0 || unsafe { libc::geteuid() } != uid {
        return Err("remote PTY bridge must be unprivileged".into());
    }
    let mut master_fd = -1;
    let mut slave_fd = -1;
    let dimensions = Dimensions::current();
    let size = libc::winsize {
        ws_row: dimensions.rows,
        ws_col: dimensions.columns,
        ws_xpixel: 0,
        ws_ypixel: 0,
    };
    // This allocation belongs to the authenticated UID. Existing SSH, sudo,
    // tmux and physical terminal device ownership is never changed.
    if unsafe {
        libc::openpty(
            &mut master_fd,
            &mut slave_fd,
            std::ptr::null_mut(),
            std::ptr::null(),
            &size,
        )
    } != 0
    {
        return Err("cannot allocate authenticated account PTY".into());
    }
    let master = unsafe { File::from_raw_fd(master_fd) };
    let slave = unsafe { File::from_raw_fd(slave_fd) };
    for fd in [master_fd, slave_fd] {
        if unsafe { libc::fcntl(fd, libc::F_SETFD, libc::FD_CLOEXEC) } < 0 {
            return Err("cannot protect owned PTY descriptors".into());
        }
    }
    let slave_metadata = slave.metadata().map_err(|_| "cannot inspect owned PTY")?;
    if !slave_metadata.file_type().is_char_device() || slave_metadata.uid() != uid {
        return Err("new PTY belongs to a different account".into());
    }
    slave
        .set_permissions(fs::Permissions::from_mode(0o600))
        .map_err(|_| "cannot protect owned PTY")?;
    command
        .stdin(Stdio::from(
            slave.try_clone().map_err(|_| "PTY input unavailable")?,
        ))
        .stdout(Stdio::from(
            slave.try_clone().map_err(|_| "PTY output unavailable")?,
        ))
        .stderr(Stdio::from(
            slave.try_clone().map_err(|_| "PTY errors unavailable")?,
        ));
    let parent = std::process::id();
    unsafe {
        command.pre_exec(move || {
            if libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGHUP) != 0
                || libc::getppid() as u32 != parent
            {
                return Err(std::io::Error::from_raw_os_error(libc::ECHILD));
            }
            if libc::setsid() < 0 || libc::ioctl(0, libc::TIOCSCTTY, 0) != 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let _terminal = TerminalRaw::arm()?;
    let input = Arc::new(BridgeFd::inherited(0)?);
    let output = Arc::new(BridgeFd::inherited(1)?);
    let master = Arc::new(BridgeFd::new(master, false)?);
    let mut child = tokio::process::Command::from(command)
        .kill_on_drop(true)
        .spawn()
        .map_err(|_| "cannot start owned PTY session")?;
    drop(slave);
    let Some(identity) = child
        .id()
        .and_then(process_identity)
        .filter(|p| p.uid == uid)
    else {
        let _ = child.kill().await;
        let _ = child.wait().await;
        return Err("owned PTY child identity unavailable".into());
    };
    let mut input_task = tokio::spawn(relay_terminal(input.clone(), master.clone(), false));
    let mut output_task = tokio::spawn(relay_terminal(master.clone(), output.clone(), true));
    let mut geometry_task = tokio::spawn(relay_geometry(geometry, master.clone()));
    enum End {
        Child(Result<std::process::ExitStatus, std::io::Error>),
        Input,
        Output,
        Geometry,
        Cancel,
    }
    let end = tokio::select! {
        result = child.wait() => End::Child(result),
        _ = &mut input_task => End::Input,
        _ = &mut output_task => End::Output,
        _ = &mut geometry_task => End::Geometry,
        _ = cancellation.cancelled() => End::Cancel,
    };
    let result = match &end {
        End::Child(Ok(status)) if status.success() => Ok(()),
        End::Child(Ok(status)) => Err(format!("owned PTY child exited with {status}")),
        End::Output => match tokio::time::timeout(Duration::from_secs(1), child.wait()).await {
            Ok(Ok(status)) if status.success() => Ok(()),
            Ok(Ok(status)) => Err(format!("owned PTY child exited with {status}")),
            _ => Err("owned PTY session stopped".into()),
        },
        End::Cancel | End::Input => Ok(()),
        _ => Err("owned PTY session or geometry stream failed".into()),
    };
    // waitpid can become ready while the child's final terminal output still
    // sits in the PTY. Forward that tail before aborting the relay, including
    // the desktop startup error that explains a return to the login screen.
    // A descendant retaining the PTY cannot extend the SSOT shutdown deadline.
    let output_joined = if matches!(end, End::Child(_)) {
        drain_terminal_tail(&mut output_task, shutdown_timeout).await
    } else {
        matches!(end, End::Output)
    };
    signal_owned_group(identity, libc::SIGHUP);
    if tokio::time::timeout(Duration::from_secs(2), child.wait())
        .await
        .is_err()
    {
        signal_owned_group(identity, libc::SIGKILL);
        let _ = child.wait().await;
    }
    input_task.abort();
    output_task.abort();
    geometry_task.abort();
    if !matches!(end, End::Input) {
        let _ = input_task.await;
    }
    if !output_joined {
        let _ = output_task.await;
    }
    if !matches!(end, End::Geometry) {
        let _ = geometry_task.await;
    }
    // Master closes after relays stop; kernel releases this owned PTY and its
    // controlling-terminal lease. The outer pane and greetd session survive.
    result
}
