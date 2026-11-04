// AI-hint: Shared native application preflight: validate desktop entries, recover only a missing document-portal mount under a user lock, and probe installed Flatpak sandboxes with bounded commands.
// AI-related: usr/share/mios/mios.toml [desktop.launch], usr/share/mios/quickshell/AppLaunch.qml, usr/libexec/mios/mios-flatpak-preflight
// AI-functions: resolve, resolve_desktop, desktop_directories, run, check, ensure_portal, bounded, portal_mounted

use serde::Deserialize;
use serde_json::{json, Value};
use std::{
    env, fs,
    io::{Read, Seek, SeekFrom},
    os::unix::{
        fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
        process::CommandExt,
    },
    path::{Path, PathBuf},
    process::{Command, Stdio},
    thread,
    time::{Duration, Instant},
};

#[derive(Clone, Deserialize)]
struct Policy {
    overall_timeout_ms: u64,
    probe_timeout_ms: u64,
    recover_document_portal: bool,
    document_portal_subdir: String,
    document_portal_unit: String,
    recovery_lock: String,
    flatpak_program: String,
    systemctl_program: String,
    signal_program: String,
    scope_program: String,
    scope_slice: String,
    scope_prefix: String,
    activation_program: String,
    activation_fallback_program: String,
    terminal_command: Vec<String>,
    application_dirs: Vec<String>,
    #[serde(skip)]
    flatpak_launchers: std::collections::BTreeMap<String, FlatpakLauncher>,
}
impl Policy {
    fn read(root: &Path) -> Result<Self, String> {
        let ssot = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
        let mut p: Self = serde_json::from_value(
            serde_json::to_value(
                ssot.get("desktop")
                    .and_then(|d| d.get("launch"))
                    .ok_or("desktop.launch is missing")?,
            )
            .map_err(|e| e.to_string())?,
        )
        .map_err(|e| e.to_string())?;
        if !(1000..=60000).contains(&p.overall_timeout_ms)
            || !(100..=30000).contains(&p.probe_timeout_ms)
            || p.probe_timeout_ms > p.overall_timeout_ms
        {
            return Err("invalid desktop.launch timeouts".into());
        }
        for name in [
            &p.document_portal_subdir,
            &p.document_portal_unit,
            &p.recovery_lock,
        ] {
            if name.is_empty()
                || name.contains('/')
                || name.contains('\\')
                || name == "."
                || name == ".."
                || name.chars().any(char::is_control)
            {
                return Err("invalid desktop.launch portal name".into());
            }
        }
        if p.application_dirs.is_empty()
            || [
                &p.flatpak_program,
                &p.systemctl_program,
                &p.signal_program,
                &p.scope_program,
                &p.activation_program,
                &p.activation_fallback_program,
            ]
            .iter()
            .any(|s| s.is_empty() || s.chars().any(char::is_control))
        {
            return Err("invalid desktop.launch programs or application directories".into());
        }
        for name in [&p.scope_slice, &p.scope_prefix] {
            if name.is_empty()
                || !name
                    .chars()
                    .all(|c| c.is_ascii_alphanumeric() || "._-".contains(c))
            {
                return Err("Invalid desktop scope name".into());
            }
        }
        if !p.scope_slice.ends_with(".slice") {
            return Err("Desktop scope slice must end in .slice".into());
        }
        p.flatpak_launchers = flatpak_launchers(&ssot);
        Ok(p)
    }
}

#[derive(Clone)]
struct FlatpakLauncher {
    app_id: String,
    exec: String,
}

fn flatpak_launchers(ssot: &toml::Value) -> std::collections::BTreeMap<String, FlatpakLauncher> {
    ssot.get("desktop")
        .and_then(|d| d.get("launchers"))
        .and_then(toml::Value::as_table)
        .into_iter()
        .flatten()
        .filter_map(|(id, launcher)| {
            let app = launcher.get("flatpak_app_id")?.as_str()?;
            let exec = launcher.get("exec_cmd")?.as_str()?;
            (valid_id(id) && valid_id(app) && !exec.is_empty()).then(|| {
                (
                    id.clone(),
                    FlatpakLauncher {
                        app_id: app.to_owned(),
                        exec: exec.to_owned(),
                    },
                )
            })
        })
        .collect()
}

// Only the trusted configured wrapper prefix and the configured Flatpak's
// "run" prefix differ. Compare the complete remaining Exec text conservatively:
// branch, architecture, command, field codes and application arguments stay
// significant. Unsupported quoting/spacing remains ambiguous, never executed.
fn equivalent_flatpak_exec(policy: &Policy, wrapper: &str, export: &str) -> bool {
    let Some((wrapper_program, wrapper_args)) = wrapper.split_once(' ') else {
        return false;
    };
    if !Path::new(wrapper_program).is_absolute() || wrapper_program.contains(['"', '\\', '\'']) {
        return false;
    }
    let Some((program, args)) = export.split_once(' ') else {
        return false;
    };
    if args.strip_prefix("run ") != Some(wrapper_args) {
        return false;
    }
    if program == policy.flatpak_program {
        return true;
    }
    // Exported files use an absolute executable, while policy may use PATH.
    // Resolve that configured program rather than trusting any matching basename.
    let configured = Path::new(&policy.flatpak_program);
    let configured = if configured.is_absolute() {
        Some(configured.to_owned())
    } else {
        env::split_paths(&env::var_os("PATH").unwrap_or_default())
            .map(|base| base.join(configured))
            .find(|path| path.is_absolute() && path.is_file())
    };
    configured
        .and_then(|path| fs::canonicalize(path).ok())
        .zip(fs::canonicalize(program).ok())
        .is_some_and(|(configured, exported)| configured == exported)
}

fn bounded(
    policy: &Policy,
    program: &str,
    args: &[String],
    deadline: Instant,
) -> Result<(i32, String), String> {
    let remaining = deadline
        .saturating_duration_since(Instant::now())
        .min(Duration::from_millis(policy.probe_timeout_ms));
    if remaining.is_zero() {
        return Err("Application startup timed out".into());
    }
    let mut output = tempfile::tempfile().map_err(|e| e.to_string())?;
    let mut command = Command::new(program);
    if program == policy.flatpak_program {
        for (key, _) in env::vars_os().filter(|(key, _)| key.to_string_lossy().starts_with("MIOS_"))
        {
            command.env_remove(key);
        }
    }
    command
        .args(args)
        .stdin(Stdio::null())
        .stdout(output.try_clone().map_err(|e| e.to_string())?)
        .stderr(output.try_clone().map_err(|e| e.to_string())?)
        .process_group(0);
    let mut child = command
        .spawn()
        .map_err(|e| format!("Cannot start {program}: {e}"))?;
    let until = Instant::now() + remaining;
    let status = loop {
        if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
            break status;
        }
        if Instant::now() >= until {
            // This group contains only this probe/recovery command and its own
            // children. Never signal an application, compositor or user target.
            let _ = Command::new(&policy.signal_program)
                .args(["-KILL", "--", &format!("-{}", child.id())])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status();
            let _ = child.kill();
            let reap = Instant::now() + Duration::from_millis(200);
            while Instant::now() < reap {
                if child.try_wait().ok().flatten().is_some() {
                    break;
                }
                thread::sleep(Duration::from_millis(5));
            }
            return Err("Application startup timed out".into());
        }
        thread::sleep(Duration::from_millis(10));
    };
    output.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
    let mut text = String::new();
    output
        .take(8192)
        .read_to_string(&mut text)
        .map_err(|e| e.to_string())?;
    Ok((status.code().unwrap_or(1), text))
}

fn unescape_mount(s: &str) -> String {
    s.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\012", "\n")
        .replace("\\134", "\\")
}
fn portal_mounted(mounts: &str, path: &Path) -> bool {
    mounts.lines().any(|line| {
        let Some((left, right)) = line.split_once(" - ") else {
            return false;
        };
        let Some(mount) = left.split_whitespace().nth(4) else {
            return false;
        };
        right.split_whitespace().next() == Some("fuse.portal")
            && Path::new(&unescape_mount(mount)) == path
    })
}
fn healthy(runtime: &Path, policy: &Policy) -> bool {
    let mount = runtime.join(&policy.document_portal_subdir);
    fs::read_to_string("/proc/self/mountinfo").is_ok_and(|s| portal_mounted(&s, &mount))
        && mount.join("by-app").is_dir()
}
fn ensure_portal(runtime: &Path, policy: &Policy, deadline: Instant) -> Result<bool, String> {
    if healthy(runtime, policy) {
        return Ok(false);
    }
    if !policy.recover_document_portal {
        return Err("Application document services are unavailable".into());
    }
    let path = runtime.join(&policy.recovery_lock);
    if fs::symlink_metadata(&path).is_ok_and(|m| m.file_type().is_symlink()) {
        return Err("Unsafe application recovery lock".into());
    }
    let lock = fs::OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .open(&path)
        .map_err(|e| e.to_string())?;
    let owner = fs::metadata(runtime).map_err(|e| e.to_string())?.uid();
    if lock.metadata().map_err(|e| e.to_string())?.uid() != owner {
        return Err("Application recovery lock has another owner".into());
    }
    loop {
        match lock.try_lock() {
            Ok(()) => break,
            Err(fs::TryLockError::WouldBlock) => {
                if healthy(runtime, policy) {
                    return Ok(false);
                }
                if Instant::now() >= deadline {
                    return Err("Application service recovery timed out".into());
                }
                thread::sleep(Duration::from_millis(20));
            }
            Err(error) => return Err(error.to_string()),
        }
    }
    if healthy(runtime, policy) {
        return Ok(false);
    }
    let (code, details) = bounded(
        policy,
        &policy.systemctl_program,
        &[
            "--user".into(),
            "restart".into(),
            policy.document_portal_unit.clone(),
        ],
        deadline,
    )?;
    if code != 0 {
        eprintln!("Document portal recovery: {}", details.trim());
        return Err("Application document services could not recover".into());
    }
    while Instant::now() < deadline {
        if healthy(runtime, policy) {
            return Ok(true);
        }
        thread::sleep(Duration::from_millis(20));
    }
    Err("Application document services did not become ready".into())
}
fn valid_id(id: &str) -> bool {
    !id.is_empty()
        && id != "."
        && id != ".."
        && !id.starts_with('-')
        && !id.contains('/')
        && !id.contains('\\')
        && !id.chars().any(char::is_control)
}
fn expand(path: &str) -> Result<PathBuf, String> {
    let app_home = env::var_os("HOME")
        .map(PathBuf::from)
        .ok_or("User home is unavailable")?;
    let data = env::var_os("XDG_DATA_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| app_home.join(".local/share"));
    let p = if let Some(rest) = path.strip_prefix("$XDG_DATA_HOME/") {
        data.join(rest)
    } else if let Some(rest) = path.strip_prefix("~/") {
        app_home.join(rest)
    } else {
        PathBuf::from(path)
    };
    if !p.is_absolute() {
        return Err("Application directories must be absolute or user-relative".into());
    }
    Ok(p)
}
fn desktop_path(policy: &Policy, id: &str) -> Result<PathBuf, String> {
    if !valid_id(id) {
        return Err("Invalid application ID".into());
    }
    let name = if id.ends_with(".desktop") {
        id.into()
    } else {
        format!("{id}.desktop")
    };
    let mut directories: Vec<PathBuf> = policy
        .application_dirs
        .iter()
        .map(|d| expand(d))
        .collect::<Result<_, _>>()?;
    for base in env::split_paths(&env::var_os("XDG_DATA_DIRS").unwrap_or_default()) {
        let directory = base.join("applications");
        if directory.is_absolute() && !directories.contains(&directory) {
            directories.push(directory);
        }
    }
    for directory in directories {
        let direct = directory.join(&name);
        if direct.is_file() {
            return Ok(direct);
        }
        // Desktop IDs flatten subdirectories with '-'. Honor that standard
        // without accepting a caller-controlled filesystem path.
        for entry in walkdir::WalkDir::new(&directory)
            .max_depth(5)
            .follow_links(false)
            .into_iter()
            .filter_map(Result::ok)
        {
            if entry.file_type().is_file()
                && entry.path().extension().is_some_and(|s| s == "desktop")
            {
                let relative = entry
                    .path()
                    .strip_prefix(&directory)
                    .map_err(|e| e.to_string())?
                    .to_string_lossy()
                    .replace('/', "-");
                if relative == name {
                    return Ok(entry.path().into());
                }
            }
        }
    }
    Err("Application desktop entry is no longer installed".into())
}

fn desktop_directories(policy: &Policy, home: Option<&Path>) -> Result<Vec<PathBuf>, String> {
    let mut directories = Vec::new();
    for configured in &policy.application_dirs {
        let directory = match home {
            Some(home) if configured.starts_with("$XDG_DATA_HOME/") => home
                .join(".local/share")
                .join(configured.trim_start_matches("$XDG_DATA_HOME/")),
            Some(home) if configured.starts_with("~/") => home.join(&configured[2..]),
            _ => expand(configured)?,
        };
        if !directory.is_absolute() {
            return Err("Application directories must be absolute or user-relative".into());
        }
        if !directories.contains(&directory) {
            directories.push(directory);
        }
    }
    for base in env::split_paths(&env::var_os("XDG_DATA_DIRS").unwrap_or_default()) {
        let directory = base.join("applications");
        if directory.is_absolute() && !directories.contains(&directory) {
            directories.push(directory);
        }
    }
    Ok(directories)
}

type DesktopEntry = (String, PathBuf, std::collections::BTreeMap<String, String>);

fn desktop_entries(policy: &Policy, home: Option<&Path>) -> Result<Vec<DesktopEntry>, String> {
    let mut seen = std::collections::BTreeSet::new();
    let mut entries = Vec::new();
    for directory in desktop_directories(policy, home)? {
        for entry in walkdir::WalkDir::new(&directory)
            .max_depth(5)
            .follow_links(false)
            .sort_by_file_name()
            .into_iter()
            .filter_map(Result::ok)
        {
            let path = entry.path();
            if !path.is_file() || path.extension().is_none_or(|s| s != "desktop") {
                continue;
            }
            let id = path
                .strip_prefix(&directory)
                .map_err(|e| e.to_string())?
                .to_string_lossy()
                .replace('/', "-")
                .trim_end_matches(".desktop")
                .to_owned();
            if !seen.insert(id.clone()) {
                continue;
            }
            let mut source = String::new();
            if fs::File::open(path)
                .and_then(|file| file.take(1024 * 1024 + 1).read_to_string(&mut source))
                .is_err()
                || source.len() > 1024 * 1024
            {
                continue;
            }
            let fields = desktop_fields(&source);
            if fields.get("Hidden").is_some_and(|s| s == "true")
                || fields.get("Type").is_some_and(|s| s != "Application")
                || (!fields.get("Exec").is_some_and(|s| !s.is_empty())
                    && !fields.get("DBusActivatable").is_some_and(|s| s == "true"))
            {
                continue;
            }
            entries.push((id, path.to_owned(), fields));
        }
    }
    Ok(entries)
}

/// Resolve only registry metadata; an Exec value is never evaluated as shell input.
fn resolve_desktop(policy: &Policy, query: &str, home: Option<&Path>) -> Result<Value, String> {
    let query = query.trim();
    if !valid_id(query) {
        return Err("Invalid application name or desktop ID".into());
    }
    let normalized = query.trim_end_matches(".desktop").to_lowercase();
    let mut matches = Vec::new();
    for (id, path, fields) in desktop_entries(policy, home)? {
        let name = fields.get("Name").cloned().unwrap_or_default();
        let score = if id == query.trim_end_matches(".desktop") {
            Some(0)
        } else if id.to_lowercase() == normalized {
            Some(1)
        } else if fields.iter().any(|(key, value)| {
            (key == "Name" || (key.starts_with("Name[") && key.ends_with(']')))
                && value.to_lowercase() == normalized
        }) {
            Some(2)
        } else if fields
            .get("StartupWMClass")
            .is_some_and(|v| v.to_lowercase() == normalized)
        {
            Some(3)
        } else if id
            .rsplit('.')
            .next()
            .is_some_and(|v| v.to_lowercase() == normalized)
        {
            Some(4)
        } else {
            None
        };
        if let Some(score) = score {
            matches.push((score, id, path, name, fields));
        }
    }
    matches.sort_by(|a, b| (a.0, &a.1).cmp(&(b.0, &b.1)));
    let Some(best) = matches.first() else {
        return Err("Application desktop entry is not installed".into());
    };
    let tied: Vec<_> = matches.iter().filter(|item| item.0 == best.0).collect();
    // The SSOT launcher and Flatpak export have different desktop IDs but
    // represent the same application. Prefer the declared launcher only when
    // every tied entry proves that identity. Other launch variants, unbound
    // duplicates and distinct applications still require an explicit ID.
    let best = if tied.len() == 1 {
        best
    } else {
        let canonical: Vec<_> = tied
            .iter()
            .copied()
            .filter(|item| {
                policy
                    .flatpak_launchers
                    .get(&item.1)
                    .is_some_and(|launcher| {
                        item.4.get("X-Flatpak") == Some(&launcher.app_id)
                            && item.4.get("Exec") == Some(&launcher.exec)
                            && tied.iter().all(|other| {
                                other.4.get("X-Flatpak") == Some(&launcher.app_id)
                                    && (other.1 == item.1
                                        || (other.1 == launcher.app_id
                                            && other.4.get("Exec").is_some_and(|exec| {
                                                equivalent_flatpak_exec(
                                                    policy,
                                                    &launcher.exec,
                                                    exec,
                                                )
                                            })))
                            })
                    })
            })
            .collect();
        if canonical.len() == 1 {
            canonical[0]
        } else {
            return Err(format!(
                "Ambiguous desktop application: {}",
                tied.iter()
                    .map(|item| item.1.as_str())
                    .collect::<Vec<_>>()
                    .join(", ")
            ));
        }
    };
    Ok(
        json!({"ok":true,"verb":"app_resolve","query":query,"desktop_id":best.1,"desktop_file":best.2,"name":best.3,"overall_timeout_ms":policy.overall_timeout_ms}),
    )
}

fn operator_lookup(ssot: &toml::Value) -> Result<String, String> {
    if let Some(value) = ssot
        .get("remote")
        .and_then(|v| v.get("rdp"))
        .and_then(|v| v.get("operator_uid"))
    {
        return value
            .as_integer()
            .filter(|uid| *uid > 0 && *uid <= u32::MAX as i64)
            .map(|uid| uid.to_string())
            .ok_or_else(|| "Configured desktop operator UID is invalid".into());
    }
    ssot.get("identity")
        .and_then(|v| v.get("username"))
        .and_then(toml::Value::as_str)
        .filter(|v| {
            !v.is_empty()
                && !v.starts_with('-')
                && v.chars()
                    .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '.' | '-'))
        })
        .map(String::from)
        .ok_or_else(|| "Configured operator is unavailable".into())
}

fn operator_account(root: &Path, policy: &Policy) -> Result<(String, u32, PathBuf), String> {
    let ssot = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let lookup = operator_lookup(&ssot)?;
    let (code, account) = bounded(
        policy,
        "getent",
        &["passwd".into(), lookup],
        Instant::now() + Duration::from_millis(policy.overall_timeout_ms),
    )?;
    if code != 0 {
        return Err("Configured desktop operator account is not installed".into());
    }
    let fields: Vec<_> = account.trim().split(':').collect();
    let operator = fields
        .first()
        .copied()
        .filter(|v| !v.is_empty())
        .ok_or("Configured operator is unavailable")?;
    let uid = fields
        .get(2)
        .and_then(|v| v.parse::<u32>().ok())
        .filter(|uid| *uid > 0)
        .ok_or("Desktop operator must be an unprivileged account")?;
    let home = fields
        .get(5)
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .ok_or("Configured operator home is unavailable")?;
    Ok((operator.to_owned(), uid, home))
}

/// Read-only installed desktop catalogue. Launch still goes through the generic
/// dispatcher and its fresh per-session preflight, never through parsed Exec text.
fn catalog_entries(policy: &Policy, home: Option<&Path>) -> Result<Vec<Value>, String> {
    let mut apps = Vec::new();
    for (id, path, fields) in desktop_entries(policy, home)? {
        if fields.get("NoDisplay").is_some_and(|v| v == "true") {
            continue;
        }
        let name = fields
            .get("Name")
            .filter(|v| !v.trim().is_empty())
            .cloned()
            .unwrap_or_else(|| id.clone());
        let terminal = fields.get("Terminal").is_some_and(|v| v == "true");
        let kind = if fields.contains_key("X-Flatpak") {
            "flatpak"
        } else if terminal {
            "terminal"
        } else {
            "native"
        };
        let available = fields
            .get("TryExec")
            .is_none_or(|program| executable(program));
        apps.push(json!({"id":id,"name":name,"kind":kind,"desktop_file":path,
            "terminal":terminal,"available":available,
            "reason":if available { "" } else { "Application executable is not installed" }}));
    }
    apps.sort_by_key(|a| {
        (
            a["name"].as_str().unwrap_or("").to_lowercase(),
            a["id"].as_str().unwrap_or("").to_owned(),
        )
    });
    Ok(apps)
}

pub fn catalog(root: &Path) -> Result<(), String> {
    let policy = Policy::read(root)?;
    let (_, uid, home) = operator_account(root, &policy)?;
    let apps = catalog_entries(&policy, Some(&home))?;
    println!(
        "{}",
        json!({"ok":true,"verb":"app_catalog","operator_uid":uid,"apps":apps})
    );
    Ok(())
}

pub fn resolve(root: &Path, query: &str) -> Result<(), String> {
    let policy = Policy::read(root)?;
    let (operator, uid, home) = operator_account(root, &policy)?;
    match resolve_desktop(&policy, query, Some(&home)) {
        Ok(mut value) => {
            value["operator_user"] = json!(operator);
            value["operator_uid"] = json!(uid);
            println!("{value}");
            Ok(())
        }
        Err(error) => {
            let status = if error.starts_with("Ambiguous") {
                "ambiguous"
            } else if error.starts_with("Invalid") {
                "invalid"
            } else {
                "not_found"
            };
            println!(
                "{}",
                json!({"ok":false,"verb":"app_resolve","status":status,"query":query,"error":error})
            );
            Err(error)
        }
    }
}

// Invocation-time inventory: local entries need a real, owned compositor reply.
// Registered remote endpoints are candidates until their existing executor probe
// returns an authoritative execution_target. A configured VM is not a session.
fn inventory_name(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 160
        && value
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || matches!(c, '_' | '-' | '.'))
        && value != "."
        && value != ".."
}
fn inventory_owned(path: &Path, uid: u32, socket: bool) -> bool {
    use std::os::unix::fs::FileTypeExt;
    fs::symlink_metadata(path).is_ok_and(|m| {
        m.uid() == uid
            && !m.file_type().is_symlink()
            && if socket {
                m.file_type().is_socket()
            } else {
                m.is_dir() && m.mode() & 0o022 == 0
            }
    })
}
fn inventory_version(socket: &Path, uid: u32, deadline: Instant) -> Result<(), String> {
    use rustix::net::{socket_with, AddressFamily, SocketAddrUnix, SocketFlags, SocketType};
    use std::io::Write;
    use std::os::unix::net::UnixStream;
    if Instant::now() >= deadline || !inventory_owned(socket, uid, true) {
        return Err("Compositor socket is unavailable or not owned by the operator".into());
    }
    let fd = socket_with(
        AddressFamily::UNIX,
        SocketType::STREAM,
        SocketFlags::CLOEXEC | SocketFlags::NONBLOCK,
        None,
    )
    .map_err(|_| "Cannot create compositor probe")?;
    let address = SocketAddrUnix::new(socket).map_err(|_| "Invalid compositor socket path")?;
    // A saturated listener is unavailable now; never block in connect().
    rustix::net::connect(&fd, &address).map_err(|_| "Compositor socket is not accepting probes")?;
    #[cfg(target_os = "linux")]
    if rustix::net::sockopt::socket_peercred(&fd)
        .map_err(|_| "Cannot establish compositor owner")?
        .uid
        .as_raw()
        != uid
    {
        return Err("Compositor peer is not the operator".into());
    }
    let mut stream = UnixStream::from(fd);
    let request = b"j/version";
    let mut sent = 0;
    let mut response = Vec::new();
    loop {
        if Instant::now() >= deadline {
            return Err("Compositor probe timed out".into());
        }
        if sent < request.len() {
            match stream.write(&request[sent..]) {
                Ok(0) => return Err("Compositor probe could not be sent".into()),
                Ok(n) => sent += n,
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {}
                Err(_) => return Err("Compositor probe write failed".into()),
            }
        } else {
            let mut buffer = [0u8; 4096];
            match stream.read(&mut buffer) {
                Ok(0) => break,
                Ok(n) => {
                    response.extend_from_slice(&buffer[..n]);
                    if response.len() > 65536 {
                        return Err("Compositor probe response exceeds its bound".into());
                    }
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {}
                Err(_) => return Err("Compositor probe read failed".into()),
            }
        }
        thread::sleep(Duration::from_millis(5));
    }
    let value: Value =
        serde_json::from_slice(&response).map_err(|_| "Invalid compositor probe response")?;
    if value
        .get("version")
        .and_then(Value::as_str)
        .is_none_or(str::is_empty)
        && value
            .get("tag")
            .and_then(Value::as_str)
            .is_none_or(str::is_empty)
    {
        return Err("Compositor did not identify its version".into());
    }
    Ok(())
}
fn inventory_endpoint(value: &str) -> Option<String> {
    let value = value.trim().trim_end_matches('/');
    let rest = value
        .strip_prefix("http://")
        .or_else(|| value.strip_prefix("https://"))?;
    let authority = rest.split('/').next()?;
    if authority.is_empty()
        || value.len() > 4096
        || value.chars().any(|c| {
            c.is_control() || c.is_whitespace() || matches!(c, '@' | '?' | '#' | '\\' | '"' | '\'')
        })
    {
        return None;
    }
    let port = if let Some(ipv6) = authority.strip_prefix('[') {
        let (host, suffix) = ipv6.split_once(']')?;
        host.parse::<std::net::Ipv6Addr>().ok()?;
        if suffix.is_empty() {
            None
        } else {
            Some(suffix.strip_prefix(':')?)
        }
    } else {
        let (host, port) = authority
            .rsplit_once(':')
            .map_or((authority, None), |(host, port)| (host, Some(port)));
        if host.is_empty()
            || host
                .chars()
                .any(|c| !c.is_ascii_alphanumeric() && !matches!(c, '.' | '-' | '_'))
        {
            return None;
        }
        port
    };
    if let Some(port) = port {
        if port.parse::<u16>().ok().is_none_or(|p| p == 0) {
            return None;
        }
    }
    Some(value.to_owned())
}
fn inventory_endpoints(ssot: &toml::Value) -> Vec<Value> {
    let mut endpoints = Vec::new();
    for section in ["os_control", "computer_use"] {
        let Some(table) = ssot.get(section) else {
            continue;
        };
        let mut registered = Vec::new();
        if let Some(endpoint) = table.get("executor_endpoint").and_then(toml::Value::as_str) {
            registered.push((format!("{section}.executor_endpoint"), endpoint));
        }
        if let Some(nodes) = table.get("nodes").and_then(toml::Value::as_table) {
            for (name, node) in nodes {
                if let Some(endpoint) = node.get("endpoint").and_then(toml::Value::as_str) {
                    registered.push((format!("{section}.nodes.{name}"), endpoint));
                }
            }
        }
        for (registration, raw) in registered {
            if raw.trim().is_empty() {
                continue;
            }
            match inventory_endpoint(raw) {
                Some(endpoint) => endpoints.push(json!({
                    "registration_id": registration, "endpoint": endpoint,
                    "probed": false, "available": Value::Null,
                    "execution_target": Value::Null,
                    "reason": "An authoritative live executor window receipt is required"
                })),
                None => endpoints.push(json!({
                    "registration_id": registration, "probed": false,
                    "available": false, "execution_target": Value::Null,
                    "reason": "Invalid registered executor endpoint"
                })),
            }
        }
    }
    endpoints
}
fn inventory_local(
    ssot: &toml::Value,
    runtime: &Path,
    uid: u32,
    policy: &Policy,
    deadline: Instant,
) -> Value {
    let mut sessions = Vec::new();
    let mut unavailable = Vec::new();
    let mut candidates = std::collections::BTreeSet::new();
    let mut pending = std::collections::VecDeque::from([runtime.to_owned()]);
    let mut seen = std::collections::BTreeSet::new();
    let mut truncated = false;
    let relative = ssot
        .get("desktop")
        .and_then(|d| d.get("nested"))
        .and_then(|d| d.get("registry_dir"))
        .and_then(toml::Value::as_str)
        .filter(|r| {
            !r.is_empty()
                && Path::new(r)
                    .components()
                    .all(|c| matches!(c, std::path::Component::Normal(_)))
        });
    while let Some(directory) = pending.pop_front() {
        if seen.contains(&directory) {
            continue;
        }
        if seen.len() >= 64 || Instant::now() >= deadline {
            truncated = true;
            break;
        }
        seen.insert(directory.clone());
        if !inventory_owned(&directory, uid, false) {
            continue;
        }
        let hypr = directory.join("hypr");
        if inventory_owned(&hypr, uid, false) {
            let mut entries: Vec<_> = fs::read_dir(hypr)
                .into_iter()
                .flatten()
                .flatten()
                .take(65)
                .collect();
            truncated |= entries.len() > 64;
            entries.sort_by_key(|entry| entry.file_name());
            for entry in entries.into_iter().take(64) {
                if candidates.len() >= 128 {
                    truncated = true;
                    break;
                }
                if let Some(signature) = entry.file_name().to_str().filter(|s| inventory_name(s)) {
                    if inventory_owned(&entry.path(), uid, false) {
                        candidates.insert((directory.clone(), signature.to_owned()));
                    }
                }
            }
        }
        let Some(relative) = relative else {
            continue;
        };
        let registry = directory.join(relative);
        if !inventory_owned(&registry, uid, false) {
            continue;
        }
        let files: Vec<_> = fs::read_dir(&registry)
            .into_iter()
            .flatten()
            .flatten()
            .take(129)
            .collect();
        let bounded = files.len() <= 128
            && files.iter().all(|entry| {
                entry.path().extension().is_none_or(|x| x != "json")
                    || fs::symlink_metadata(entry.path()).is_ok_and(|m| {
                        m.is_file()
                            && !m.file_type().is_symlink()
                            && m.uid() == uid
                            && m.len() <= 1024 * 1024
                    })
            });
        if !bounded {
            truncated = true;
            unavailable.push(json!({"available":false,"probed":false,
                "reason":"Nested session registry exceeds its safety bounds"}));
            continue;
        }
        // Reuse the producer's launcher PID/start proof, including recursively
        // nested sessions whose runtime moved back under the outermost runtime.
        let live = crate::nested_devices::live_sessions(&registry, false);
        truncated |= live.len() > 64;
        for (_, entry) in live.into_iter().take(64) {
            if entry["state"] != "running" {
                continue;
            }
            if let Some(nested) = entry["runtime"].as_str().map(PathBuf::from).filter(|p| {
                p.starts_with(runtime) && p != runtime && p.canonicalize().is_ok_and(|c| c == *p)
            }) {
                if pending.len() < 64 {
                    pending.push_back(nested);
                } else {
                    truncated = true;
                }
            }
        }
    }
    for (directory, signature) in candidates {
        let socket = directory.join("hypr").join(&signature).join(".socket.sock");
        let until = deadline.min(Instant::now() + Duration::from_millis(policy.probe_timeout_ms));
        match inventory_version(&socket, uid, until) {
            Ok(()) => sessions.push(json!({
                "execution_target":{"id":socket,"platform":"linux","session_id":signature,"backend":"hyprland"},
                "runtime":directory, "available":true, "probed":true,
                "capabilities":["compositor-query"],
                "scope": if directory == runtime { "operator" } else { "nested" }
            })),
            Err(reason) => {
                truncated |= Instant::now() >= deadline;
                unavailable.push(json!({"session_id":signature,"runtime":directory,
                    "available":false,"probed":true,"reason":reason}));
            }
        }
    }
    json!({"ok":true,"verb":"runtime_sessions","discovery":"local-and-registered",
        "operator_uid":uid,"sessions":sessions,"unavailable":unavailable,"truncated":truncated,
        "registered_endpoints":inventory_endpoints(ssot)})
}
pub fn sessions(root: &Path) -> Result<(), String> {
    let policy = Policy::read(root)?;
    let ssot = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let deadline = Instant::now() + Duration::from_millis(policy.overall_timeout_ms);
    let lookup = operator_lookup(&ssot)?;
    let (code, account) = bounded(&policy, "getent", &["passwd".into(), lookup], deadline)?;
    if code != 0 {
        return Err("Configured desktop operator account is not installed".into());
    }
    let uid = account
        .trim()
        .split(':')
        .nth(2)
        .and_then(|v| v.parse::<u32>().ok())
        .filter(|uid| *uid > 0)
        .ok_or("Invalid desktop operator UID")?;
    let runtime = PathBuf::from(format!("/run/user/{uid}"));
    println!(
        "{}",
        inventory_local(&ssot, &runtime, uid, &policy, deadline)
    );
    Ok(())
}

fn desktop_fields(source: &str) -> std::collections::BTreeMap<String, String> {
    let mut active = false;
    let mut result = std::collections::BTreeMap::new();
    for line in source.lines().map(str::trim) {
        if line.starts_with('[') {
            active = line == "[Desktop Entry]";
            continue;
        }
        if !active || line.starts_with('#') {
            continue;
        }
        if let Some((key, value)) = line.split_once('=') {
            result.insert(key.trim().into(), value.trim().into());
        }
    }
    result
}
fn executable(program: &str) -> bool {
    let test = |p: &Path| {
        fs::metadata(p).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0)
    };
    if program.contains('/') {
        test(Path::new(program))
    } else {
        env::split_paths(&env::var_os("PATH").unwrap_or_default())
            .any(|dir| test(&dir.join(program)))
    }
}
fn check(
    policy: &Policy,
    runtime: &Path,
    desktop: Option<&str>,
    flatpak: Option<&str>,
    portal_only: bool,
) -> Result<Value, String> {
    let deadline = Instant::now() + Duration::from_millis(policy.overall_timeout_ms);
    let mut app = flatpak.map(String::from);
    let mut file = None;
    if let Some(id) = desktop {
        let path = desktop_path(policy, id)?;
        let mut source = String::new();
        fs::File::open(&path)
            .map_err(|e| e.to_string())?
            .take(1024 * 1024 + 1)
            .read_to_string(&mut source)
            .map_err(|e| e.to_string())?;
        if source.len() > 1024 * 1024 {
            return Err("Application desktop entry exceeds its size bound".into());
        }
        let fields = desktop_fields(&source);
        if fields.get("Hidden").is_some_and(|s| s == "true") {
            return Err("Application desktop entry is disabled".into());
        }
        if !fields.get("Exec").is_some_and(|s| !s.is_empty())
            && !fields.get("DBusActivatable").is_some_and(|s| s == "true")
        {
            return Err("Application has no launch command".into());
        }
        if fields.get("TryExec").is_some_and(|s| !executable(s)) {
            return Err("Application executable is not installed".into());
        }
        if fields.get("Terminal").is_some_and(|s| s == "true")
            && !policy
                .terminal_command
                .first()
                .is_some_and(|p| executable(p))
        {
            return Err("The configured application terminal is not installed".into());
        }
        app = fields.get("X-Flatpak").cloned();
        file = Some(path);
    }
    if app.as_deref().is_some_and(|id| !valid_id(id)) {
        return Err("Invalid Flatpak application ID".into());
    }
    // Recover the common infrastructure for wrapper entries too. A plain
    // native app can still launch if an optional sandbox service is absent.
    let recovery = ensure_portal(runtime, policy, deadline);
    let recovered = match recovery {
        Ok(value) => value,
        Err(error) if app.is_none() && !portal_only => {
            eprintln!("{error}");
            false
        }
        Err(error) => return Err(error),
    };
    if let Some(id) = &app {
        let (code, details) = bounded(
            policy,
            &policy.flatpak_program,
            &["run".into(), "--command=true".into(), id.clone()],
            deadline,
        )?;
        if code != 0 {
            eprintln!("Flatpak sandbox preflight for {id}: {}", details.trim());
            return Err(if details.contains("not installed") {
                "Application or its runtime is not installed"
            } else {
                "Application sandbox could not start"
            }
            .into());
        }
    }
    let unset_environment: Vec<_> = if app.is_some() && desktop.is_some() {
        env::vars_os()
            .filter_map(|(key, _)| {
                key.into_string()
                    .ok()
                    .filter(|key| key.starts_with("MIOS_"))
            })
            .collect()
    } else {
        vec![]
    };
    Ok(
        json!({"ok":true,"verb":if flatpak.is_some(){"flatpak_preflight"}else{"app_preflight"},"app":app.or_else(||desktop.map(String::from)),"desktop_file":file,"document_portal_recovered":recovered,"unset_environment":unset_environment,"exit_code":0}),
    )
}
pub fn run(
    root: &Path,
    desktop: Option<&str>,
    flatpak: Option<&str>,
    all: bool,
    portal_only: bool,
) -> Result<(), String> {
    let policy = Policy::read(root)?;
    let runtime = env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .ok_or("Desktop runtime is unavailable")?;
    if all {
        let (code, list) = bounded(
            &policy,
            &policy.flatpak_program,
            &[
                "list".into(),
                "--app".into(),
                "--columns=application".into(),
            ],
            Instant::now() + Duration::from_millis(policy.overall_timeout_ms),
        )?;
        if code != 0 {
            return Err("Cannot list installed Flatpak applications".into());
        }
        let ids: std::collections::BTreeSet<_> = list.lines().filter(|id| valid_id(id)).collect();
        if ids.is_empty() {
            return Err("No installed Flatpak applications were checked".into());
        }
        let results: Vec<_> = ids
            .iter()
            .map(|id| match check(&policy, &runtime, None, Some(id), false) {
                Ok(value) => value,
                Err(error) => json!({"ok":false,"app":id,"error":error}),
            })
            .collect();
        let passed = results.iter().all(|v| v["ok"] == true);
        println!(
            "{}",
            json!({"ok":passed,"checked":results.len(),"results":results})
        );
        return if passed {
            Ok(())
        } else {
            Err("Some application sandboxes failed their launch checks".into())
        };
    }
    match check(&policy, &runtime, desktop, flatpak, portal_only) {
        Ok(value) => {
            println!("{value}");
            Ok(())
        }
        Err(error) => {
            println!(
                "{}",
                json!({"ok":false,"verb":"app_preflight","app":flatpak.or(desktop),"error":error,"exit_code":1})
            );
            Err(error)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn policy() -> Policy {
        Policy {
            overall_timeout_ms: 1000,
            probe_timeout_ms: 200,
            recover_document_portal: false,
            document_portal_subdir: "doc".into(),
            document_portal_unit: "xdg-document-portal.service".into(),
            recovery_lock: "mios-test.lock".into(),
            flatpak_program: "flatpak".into(),
            systemctl_program: "systemctl".into(),
            signal_program: "kill".into(),
            scope_program: "systemd-run".into(),
            scope_slice: "app.slice".into(),
            scope_prefix: "mios-app-".into(),
            activation_program: "gio".into(),
            activation_fallback_program: "gtk-launch".into(),
            terminal_command: vec!["true".into()],
            application_dirs: vec!["/usr/share/applications".into()],
            flatpak_launchers: Default::default(),
        }
    }

    #[test]
    fn desktop_exec_uses_argv_not_shell_and_expands_only_defined_codes() {
        let p = policy();
        let path = Path::new("/apps/fixture.desktop");
        let fields = desktop_fields("[Desktop Entry]\nName=Fixture\\sApplication\nIcon=fixture\nExec=/bin/echo \"two words\" --literal=$HOME %% %c %k %i %U\n");
        assert_eq!(
            desktop_argv(
                &p,
                path,
                &fields,
                &[
                    "file with spaces".into(),
                    "https://example.invalid/a".into()
                ]
            )
            .unwrap(),
            vec![
                "/bin/echo",
                "two words",
                "--literal=$HOME",
                "%",
                "Fixture Application",
                "/apps/fixture.desktop",
                "--icon",
                "fixture",
                "file with spaces",
                "https://example.invalid/a"
            ]
        );
        let fields = desktop_fields(
            "[Desktop Entry]\nExec=/bin/echo \"\" \"$(touch /tmp/not-executed)\" ; whoami\n",
        );
        assert_eq!(
            desktop_argv(&p, path, &fields, &[]).unwrap(),
            vec!["/bin/echo", "", "$(touch /tmp/not-executed)", ";", "whoami"]
        );
    }

    #[test]
    fn malformed_desktop_exec_never_becomes_a_shell_command() {
        for raw in [
            "/bin/echo \"unfinished",
            "/bin/echo %x",
            "/bin/echo prefix%U",
            "/bin/echo %U %F",
            "",
            "A=x /bin/echo",
        ] {
            let fields = desktop_fields(&format!("[Desktop Entry]\nExec={raw}\n"));
            assert!(
                desktop_argv(&policy(), Path::new("/apps/f.desktop"), &fields, &[]).is_err(),
                "{raw}"
            );
        }
        let fields = desktop_fields("[Desktop Entry]\nExec=/bin/echo %f\n");
        assert!(desktop_argv(
            &policy(),
            Path::new("/apps/f.desktop"),
            &fields,
            &["one".into(), "two".into()]
        )
        .is_err());
    }

    #[test]
    fn desktop_entry_terminal_path_and_keyfile_escaping_are_preserved() {
        let mut p = policy();
        p.terminal_command = vec![
            "terminal".into(),
            "--title".into(),
            "{name}".into(),
            "-e".into(),
        ];
        let fields = desktop_fields(
            "[Desktop Entry]\nName=App\nExec=/bin/echo \"a\\\\\\\\b\" %d\nTerminal=true\n",
        );
        assert_eq!(
            desktop_argv(&p, Path::new("/apps/f.desktop"), &fields, &[]).unwrap(),
            vec!["terminal", "--title", "App", "-e", "/bin/echo", "a\\b"]
        );
        assert!(desktop_string(r"bad\q").is_err());
        assert!(exec_words("/bin/echo \\\\").is_err());
    }

    #[test]
    fn desktop_exec_preserves_env_wrappers_and_explicit_shell_argv() {
        for (raw, wanted) in [
            (
                r#"env "APP_HOME=/a b" /usr/bin/example --no-sandbox %U"#,
                vec![
                    "env",
                    "APP_HOME=/a b",
                    "/usr/bin/example",
                    "--no-sandbox",
                    "https://example.invalid/",
                ],
            ),
            (
                r#"/usr/bin/terminal -e env -u TMUX -u TMUX_PANE /usr/bin/menu"#,
                vec![
                    "/usr/bin/terminal",
                    "-e",
                    "env",
                    "-u",
                    "TMUX",
                    "-u",
                    "TMUX_PANE",
                    "/usr/bin/menu",
                    "https://example.invalid/",
                ],
            ),
            (
                r#"/bin/sh -c "printf '%%s' \\"literal $HOME\\"" fixture"#,
                vec![
                    "/bin/sh",
                    "-c",
                    "printf '%s' \"literal $HOME\"",
                    "fixture",
                    "https://example.invalid/",
                ],
            ),
            (
                r#"handler --template "https://example.invalid/?lat=<LAT>&lon=<LON>" %u"#,
                vec![
                    "handler",
                    "--template",
                    "https://example.invalid/?lat=<LAT>&lon=<LON>",
                    "https://example.invalid/",
                ],
            ),
        ] {
            let fields = desktop_fields(&format!("[Desktop Entry]\nExec={raw}\n"));
            assert_eq!(
                desktop_argv(
                    &policy(),
                    Path::new("/apps/f.desktop"),
                    &fields,
                    &["https://example.invalid/".into()]
                )
                .unwrap(),
                wanted,
                "{raw}"
            );
        }
    }

    #[test]
    fn actual_exec_failure_has_no_success_receipt() {
        let entry = (
            "fixture".into(),
            PathBuf::from("/apps/fixture.desktop"),
            desktop_fields("[Desktop Entry]\nExec=/mios-fixture-no-executable\n"),
        );
        let error = dispatch_exec(&policy(), &entry, &[], Value::Null).unwrap_err();
        assert!(
            error.contains("Cannot execute desktop application"),
            "{error}"
        );
    }

    #[test]
    fn actual_exec_ack_does_not_wait_for_the_application_lifetime() {
        let entry = (
            "fixture".into(),
            PathBuf::from("/apps/fixture.desktop"),
            desktop_fields("[Desktop Entry]\nExec=/bin/sleep 30\n"),
        );
        let start = Instant::now();
        let receipt = dispatch_exec(&policy(), &entry, &[], json!({"id":"owned-target"})).unwrap();
        assert!(start.elapsed() < Duration::from_secs(1));
        assert_eq!(receipt["method"], "exec");
        assert_eq!(receipt["execution_target"]["id"], "owned-target");
        let pid = receipt["pid"].as_u64().unwrap().to_string();
        assert!(Path::new("/proc").join(&pid).is_dir());
        assert!(Command::new("kill")
            .args(["--", &pid])
            .status()
            .unwrap()
            .success());
    }

    #[test]
    fn catalog_reuses_registry_without_executing_apps_and_hides_private_entries() {
        let t = tempfile::tempdir().unwrap();
        let canary = t.path().join("executed");
        for (id, extra) in [
            ("mios-test-native", ""),
            ("mios-test-flatpak", "X-Flatpak=org.example.Fixture\n"),
            ("mios-test-terminal", "Terminal=true\n"),
            ("mios-test-hidden", "Hidden=true\n"),
            ("mios-test-private", "NoDisplay=true\n"),
            ("mios-test-missing", "TryExec=/mios-fixture-absent\n"),
        ] {
            fs::write(
                t.path().join(format!("{id}.desktop")),
                format!(
                    "[Desktop Entry]\nType=Application\nName={id}\nExec=touch {}\n{extra}",
                    canary.display()
                ),
            )
            .unwrap();
        }
        let mut p = policy();
        p.application_dirs = vec![t.path().to_string_lossy().into_owned()];
        let apps = catalog_entries(&p, None).unwrap();
        let app = |id: &str| apps.iter().find(|r| r["id"] == id);
        assert_eq!(app("mios-test-native").unwrap()["kind"], "native");
        assert_eq!(app("mios-test-flatpak").unwrap()["kind"], "flatpak");
        assert_eq!(app("mios-test-terminal").unwrap()["kind"], "terminal");
        assert_eq!(app("mios-test-missing").unwrap()["available"], false);
        assert!(app("mios-test-hidden").is_none() && app("mios-test-private").is_none());
        assert!(
            !canary.exists(),
            "inventory must never execute desktop Exec text"
        );
        let found = resolve_desktop(&p, "mios-test-native", None).unwrap();
        assert_eq!(found["desktop_id"], "mios-test-native");
    }

    fn inventory_listener(
        socket: &Path,
        response: &'static [u8],
        delay: Duration,
    ) -> thread::JoinHandle<()> {
        use std::io::Write;
        use std::os::unix::net::UnixListener;
        let listener = UnixListener::bind(socket).unwrap();
        listener.set_nonblocking(true).unwrap();
        thread::spawn(move || {
            let deadline = Instant::now() + Duration::from_secs(2);
            let mut stream = loop {
                match listener.accept() {
                    Ok((stream, _)) => break stream,
                    Err(error)
                        if error.kind() == std::io::ErrorKind::WouldBlock
                            && Instant::now() < deadline =>
                    {
                        thread::sleep(Duration::from_millis(5))
                    }
                    Err(error) => panic!("Expected bounded compositor probe: {error}"),
                }
            };
            stream
                .set_read_timeout(Some(Duration::from_secs(1)))
                .unwrap();
            let mut request = [0; 9];
            stream.read_exact(&mut request).unwrap();
            assert_eq!(&request, b"j/version");
            thread::sleep(delay);
            let _ = stream.write_all(response);
        })
    }
    fn inventory_socket(runtime: &Path, name: &str) -> PathBuf {
        let dir = runtime.join("hypr").join(name);
        fs::create_dir_all(&dir).unwrap();
        dir.join(".socket.sock")
    }
    #[test]
    fn runtime_inventory_requires_live_owned_compositors_and_preserves_identity() {
        use std::os::unix::net::UnixListener;
        let runtime = tempfile::tempdir().unwrap();
        let socket = inventory_socket(runtime.path(), "mios-live");
        let uid = fs::metadata(runtime.path()).unwrap().uid();
        let listener = inventory_listener(&socket, br#"{"version":"0.56"}"#, Duration::ZERO);
        let stale = inventory_socket(runtime.path(), "mios-stale");
        drop(UnixListener::bind(stale).unwrap());
        let result = inventory_local(
            &toml::Table::new().into(),
            runtime.path(),
            uid,
            &policy(),
            Instant::now() + Duration::from_secs(1),
        );
        listener.join().unwrap();
        assert_eq!(result["sessions"].as_array().unwrap().len(), 1);
        let row = &result["sessions"][0];
        assert_eq!(
            row["execution_target"]["id"],
            socket.to_string_lossy().as_ref()
        );
        assert_eq!(row["execution_target"]["session_id"], "mios-live");
        assert_eq!(row["execution_target"]["platform"], "linux");
        assert_eq!(row["available"], true);
        assert_eq!(result["unavailable"].as_array().unwrap().len(), 1);
        // Same paths attributed to a different owner are never queried/available.
        let other = inventory_local(
            &toml::Table::new().into(),
            runtime.path(),
            uid + 1,
            &policy(),
            Instant::now() + Duration::from_secs(1),
        );
        assert!(other["sessions"].as_array().unwrap().is_empty());
    }
    #[test]
    fn runtime_inventory_follows_the_existing_live_nested_registry_without_pruning() {
        let runtime = tempfile::tempdir().unwrap();
        let nested = runtime.path().join("mios-nested-desktop-123");
        fs::create_dir(&nested).unwrap();
        let socket = inventory_socket(&nested, "mios-nested");
        let uid = fs::metadata(runtime.path()).unwrap().uid();
        let listener = inventory_listener(&socket, br#"{"tag":"v0.56"}"#, Duration::ZERO);
        let registry = runtime.path().join("mios/nested-sessions");
        let _claim = crate::nested_devices::Claim::take(
            &registry,
            "mios-seat",
            &[],
            false,
            json!({"state":"running","runtime":nested,"instance":"mios-nested"}),
        )
        .unwrap();
        fs::write(
            registry.join("stale.json"),
            r#"{"state":"running","launcher_pid":4294967295,"launcher_start":1}"#,
        )
        .unwrap();
        let ssot: toml::Value =
            toml::from_str("[desktop.nested]\nregistry_dir='mios/nested-sessions'\n").unwrap();
        let result = inventory_local(
            &ssot,
            runtime.path(),
            uid,
            &policy(),
            Instant::now() + Duration::from_secs(1),
        );
        listener.join().unwrap();
        assert_eq!(result["sessions"].as_array().unwrap().len(), 1);
        assert_eq!(result["sessions"][0]["scope"], "nested");
        assert_eq!(
            result["sessions"][0]["execution_target"]["id"],
            socket.to_string_lossy().as_ref()
        );
        assert!(
            registry.join("stale.json").exists(),
            "read-only inventory must not prune the producer registry"
        );
        let live_record = fs::read(registry.join("mios-seat.json")).unwrap();
        for index in 0..65 {
            fs::write(registry.join(format!("extra-{index}.json")), &live_record).unwrap();
        }
        let bounded = inventory_local(
            &ssot,
            runtime.path(),
            uid,
            &policy(),
            Instant::now() + Duration::from_secs(1),
        );
        assert_eq!(
            bounded["truncated"], true,
            "registry cap must be reported honestly"
        );
    }
    #[test]
    fn runtime_inventory_times_out_and_rejects_non_compositor_replies() {
        let runtime = tempfile::tempdir().unwrap();
        let uid = fs::metadata(runtime.path()).unwrap().uid();
        let socket = inventory_socket(runtime.path(), "mios-timeout");
        let listener = inventory_listener(
            &socket,
            br#"{"version":"late"}"#,
            Duration::from_millis(150),
        );
        let start = Instant::now();
        assert!(
            inventory_version(&socket, uid, start + Duration::from_millis(25))
                .unwrap_err()
                .contains("timed out")
        );
        assert!(start.elapsed() < Duration::from_millis(100));
        listener.join().unwrap();
        fs::remove_file(&socket).unwrap();
        let listener = inventory_listener(&socket, br#"{"ok":true}"#, Duration::ZERO);
        assert!(inventory_version(&socket, uid, Instant::now() + Duration::from_secs(1)).is_err());
        listener.join().unwrap();
    }

    #[test]
    fn runtime_inventory_discovers_sessions_nested_beyond_one_level() {
        let runtime = tempfile::tempdir().unwrap();
        let uid = fs::metadata(runtime.path()).unwrap().uid();
        let child = runtime.path().join("mios-nested-desktop-123");
        let grandchild = runtime.path().join("mios-nested-desktop-456");
        fs::create_dir(&child).unwrap();
        fs::create_dir(&grandchild).unwrap();
        let child_socket = inventory_socket(&child, "mios-child");
        let grandchild_socket = inventory_socket(&grandchild, "mios-grandchild");
        let first = inventory_listener(&child_socket, br#"{"tag":"v0.56"}"#, Duration::ZERO);
        let second = inventory_listener(&grandchild_socket, br#"{"tag":"v0.56"}"#, Duration::ZERO);
        let _child = crate::nested_devices::Claim::take(
            &runtime.path().join("registry"),
            "child",
            &[],
            false,
            json!({"state":"running","runtime":child,"instance":"mios-child"}),
        )
        .unwrap();
        let _grandchild = crate::nested_devices::Claim::take(
            &child.join("registry"),
            "grandchild",
            &[],
            false,
            json!({"state":"running","runtime":grandchild,"instance":"mios-grandchild"}),
        )
        .unwrap();
        let ssot: toml::Value =
            toml::from_str("[desktop.nested]\nregistry_dir='registry'\n").unwrap();
        let result = inventory_local(
            &ssot,
            runtime.path(),
            uid,
            &policy(),
            Instant::now() + Duration::from_secs(1),
        );
        first.join().unwrap();
        second.join().unwrap();
        assert_eq!(result["sessions"].as_array().unwrap().len(), 2);
        assert_eq!(result["truncated"], false);
        assert!(result["sessions"]
            .as_array()
            .unwrap()
            .iter()
            .any(|r| r["execution_target"]["session_id"] == "mios-grandchild"));
    }

    #[test]
    fn runtime_inventory_remote_candidates_are_refreshed_unprobed_and_secret_free() {
        let mut data: toml::Value = toml::from_str(
            "[os_control]\nexecutor_endpoint='http://localhost:9000/'\n[os_control.nodes.console]\nendpoint='https://example.test/control'\n[computer_use.nodes.mobile]\nendpoint='http://localhost:9001'\n[computer_use.nodes.invalid]\nendpoint='http://user:password@example.test/private'\n").unwrap();
        let rows = inventory_endpoints(&data);
        assert_eq!(rows.len(), 4);
        assert!(rows
            .iter()
            .all(|r| r["execution_target"].is_null() && r["probed"] == false));
        assert!(rows
            .iter()
            .filter(|r| r.get("endpoint").is_some())
            .all(|r| r["available"].is_null()));
        assert!(!serde_json::to_string(&rows).unwrap().contains("password"));
        data["os_control"]["executor_endpoint"] =
            toml::Value::String("http://localhost:9999".into());
        assert!(inventory_endpoints(&data)
            .iter()
            .any(|r| r["endpoint"] == "http://localhost:9999"));
        for endpoint in [
            "http://",
            "file:///tmp/socket",
            "https://host/?token=secret",
            "https://host/#secret",
            "http://host/ bad",
            "http://host/\\path",
            "http://host:0",
            "http://host:999999",
            "http://[bad]:80",
        ] {
            assert!(inventory_endpoint(endpoint).is_none(), "{endpoint}");
        }
    }
    #[test]
    fn runtime_inventory_refuses_redirected_or_oversized_registry_without_losing_the_operator() {
        use std::os::unix::fs::symlink;
        let runtime = tempfile::tempdir().unwrap();
        let uid = fs::metadata(runtime.path()).unwrap().uid();
        let socket = inventory_socket(runtime.path(), "mios-primary");
        let listener = inventory_listener(&socket, br#"{"tag":"v0.56"}"#, Duration::ZERO);
        let registry = runtime.path().join("registry");
        fs::create_dir(&registry).unwrap();
        fs::write(registry.join("large.json"), vec![b' '; 1024 * 1024 + 1]).unwrap();
        let ssot: toml::Value =
            toml::from_str("[desktop.nested]\nregistry_dir='registry'\n").unwrap();
        let result = inventory_local(
            &ssot,
            runtime.path(),
            uid,
            &policy(),
            Instant::now() + Duration::from_secs(1),
        );
        listener.join().unwrap();
        assert_eq!(result["sessions"].as_array().unwrap().len(), 1);
        assert!(result["unavailable"]
            .as_array()
            .unwrap()
            .iter()
            .any(|r| r["reason"].as_str().unwrap().contains("safety bounds")));
        let redirect = runtime.path().join("redirect");
        symlink(runtime.path(), &redirect).unwrap();
        assert!(!inventory_owned(&redirect, uid, false));
    }

    #[test]
    fn desktop_operator_uses_existing_live_uid_policy_not_an_installer_placeholder() {
        let ssot: toml::Value =
            toml::from_str("[identity]\nusername='user'\n[remote.rdp]\noperator_uid=1000\n")
                .unwrap();
        assert_eq!(operator_lookup(&ssot).unwrap(), "1000");
        let fallback: toml::Value =
            toml::from_str("[identity]\nusername='operator-name'\n").unwrap();
        assert_eq!(operator_lookup(&fallback).unwrap(), "operator-name");
        for value in ["0", "-1", "'1000'"] {
            let ssot: toml::Value = toml::from_str(&format!(
                "[identity]\nusername='user'\n[remote.rdp]\noperator_uid={value}\n"
            ))
            .unwrap();
            assert!(operator_lookup(&ssot).is_err());
        }
    }

    #[test]
    fn registry_resolves_labels_ids_and_window_class_without_executing_exec() {
        let dir = tempfile::tempdir().unwrap();
        let proof = dir.path().join("must-not-exist");
        fs::write(dir.path().join("org.example.Recorder.desktop"),
            format!("[Desktop Entry]\nType=Application\nName=Example Studio\nName[fr]=Studio Exemple\nStartupWMClass=recorder\nExec=example \"$(touch {})\" %U\n[Desktop Action Other]\nName=Wrong Action\n",proof.display())).unwrap();
        let mut p = policy();
        p.application_dirs = vec![dir.path().display().to_string()];
        for query in [
            "Example Studio",
            "example studio",
            "Studio Exemple",
            "org.example.Recorder",
            "org.example.recorder.desktop",
            "recorder",
        ] {
            let value = resolve_desktop(&p, query, None).unwrap();
            assert_eq!(value["desktop_id"], "org.example.Recorder");
            assert_eq!(
                value["desktop_file"],
                dir.path()
                    .join("org.example.Recorder.desktop")
                    .display()
                    .to_string()
            );
        }
        assert!(resolve_desktop(&p, "Wrong Action", None).is_err());
        assert!(
            !proof.exists(),
            "Exec metadata must never execute during resolution"
        );
    }

    #[test]
    fn registry_rejects_missing_ambiguous_and_path_queries() {
        let dir = tempfile::tempdir().unwrap();
        let mut p = policy();
        p.application_dirs = vec![dir.path().display().to_string()];
        for id in ["org.example.One", "org.example.Two"] {
            fs::write(
                dir.path().join(format!("{id}.desktop")),
                "[Desktop Entry]\nType=Application\nName=Shared Editor\nExec=true\n",
            )
            .unwrap();
        }
        assert!(resolve_desktop(&p, "Shared Editor", None)
            .unwrap_err()
            .contains("Ambiguous"));
        assert!(resolve_desktop(&p, "Missing Editor", None)
            .unwrap_err()
            .contains("not installed"));
        for query in ["../escape", "/bin/sh", "bad\napp", "-option", ""] {
            assert!(resolve_desktop(&p, query, None).is_err());
        }
        assert_eq!(
            resolve_desktop(&p, "org.example.One", None).unwrap()["desktop_id"],
            "org.example.One"
        );
    }

    #[test]
    fn registry_honors_hidden_overrides_and_flatpak_export_symlinks() {
        let dir = tempfile::tempdir().unwrap();
        let first = dir.path().join("first");
        let second = dir.path().join("second");
        fs::create_dir(&first).unwrap();
        fs::create_dir(&second).unwrap();
        fs::write(
            first.join("org.example.Hidden.desktop"),
            "[Desktop Entry]\nHidden=true\n",
        )
        .unwrap();
        fs::write(
            second.join("org.example.Hidden.desktop"),
            "[Desktop Entry]\nName=Masked App\nExec=true\n",
        )
        .unwrap();
        let target = dir.path().join("export.desktop");
        fs::write(&target,"[Desktop Entry]\nName=Export App\nExec=flatpak run org.example.Export\nX-Flatpak=org.example.Export\n").unwrap();
        std::os::unix::fs::symlink(&target, second.join("org.example.Export.desktop")).unwrap();
        let mut p = policy();
        p.application_dirs = vec![first.display().to_string(), second.display().to_string()];
        assert!(resolve_desktop(&p, "Masked App", None).is_err());
        assert_eq!(
            resolve_desktop(&p, "Export App", None).unwrap()["desktop_id"],
            "org.example.Export"
        );
    }

    fn flatpak_registry_fixture() -> (tempfile::TempDir, Policy) {
        let dir = tempfile::tempdir().unwrap();
        let mut p = policy();
        p.application_dirs = vec![dir.path().display().to_string()];
        let ssot: toml::Value = toml::from_str(
            "[desktop.launchers.mios-browser]\nflatpak_app_id='org.example.Browser'\nexec_cmd='/usr/libexec/mios/mios-flatpak-run --branch=stable --arch=x86_64 --command=launch-script.sh --file-forwarding org.example.Browser @@u %u @@'\n",
        )
        .unwrap();
        p.flatpak_launchers = flatpak_launchers(&ssot);
        p.flatpak_program = "/usr/bin/flatpak".into();
        fs::write(dir.path().join("mios-browser.desktop"),
            "[Desktop Entry]\nType=Application\nName=Example Browser\nStartupWMClass=browser\nX-Flatpak=org.example.Browser\nExec=/usr/libexec/mios/mios-flatpak-run --branch=stable --arch=x86_64 --command=launch-script.sh --file-forwarding org.example.Browser @@u %u @@\n").unwrap();
        fs::write(dir.path().join("org.example.Browser.desktop"),
            "[Desktop Entry]\nType=Application\nName=Example Browser\nStartupWMClass=browser\nX-Flatpak=org.example.Browser\nExec=/usr/bin/flatpak run --branch=stable --arch=x86_64 --command=launch-script.sh --file-forwarding org.example.Browser @@u %u @@\n").unwrap();
        (dir, p)
    }

    #[test]
    fn registry_prefers_ssot_launcher_for_the_same_flatpak_identity() {
        let (_dir, p) = flatpak_registry_fixture();
        for query in ["browser", "Example Browser", "example browser"] {
            assert_eq!(
                resolve_desktop(&p, query, None).unwrap()["desktop_id"],
                "mios-browser"
            );
        }
        for id in ["org.example.Browser", "mios-browser"] {
            assert_eq!(resolve_desktop(&p, id, None).unwrap()["desktop_id"], id);
        }
        assert_eq!(
            resolve_desktop(&p, "org.example.Browser.desktop", None).unwrap()["desktop_id"],
            "org.example.Browser"
        );
    }

    #[test]
    fn registry_preserves_ambiguity_for_distinct_flatpaks_and_undeclared_variants() {
        let (dir, mut p) = flatpak_registry_fixture();
        let export = dir.path().join("org.example.Browser.desktop");
        let source = fs::read_to_string(&export).unwrap();
        // A coincident label/window class does not make different Flatpaks equal.
        fs::write(
            &export,
            source.replace(
                "X-Flatpak=org.example.Browser",
                "X-Flatpak=org.example.Other",
            ),
        )
        .unwrap();
        assert!(resolve_desktop(&p, "browser", None)
            .unwrap_err()
            .contains("Ambiguous"));
        fs::write(&export, &source).unwrap();
        // An unknown custom launch variant is not silently discarded.
        let variant = dir.path().join("private-browser.desktop");
        fs::write(&variant, source.replace(" @@u", " --private-window @@u")).unwrap();
        assert!(resolve_desktop(&p, "browser", None)
            .unwrap_err()
            .contains("Ambiguous"));
        // Two declared launchers for one Flatpak are also a real choice.
        let launcher = p.flatpak_launchers.get("mios-browser").unwrap().clone();
        p.flatpak_launchers
            .insert("private-browser".into(), launcher.clone());
        assert!(resolve_desktop(&p, "browser", None)
            .unwrap_err()
            .contains("Ambiguous"));
        fs::remove_file(variant).unwrap();
        // A stale/missing SSOT identity never becomes a prefix-based preference.
        p.flatpak_launchers.clear();
        assert!(resolve_desktop(&p, "Example Browser", None)
            .unwrap_err()
            .contains("Ambiguous"));
        p.flatpak_launchers.insert(
            "mios-browser".into(),
            FlatpakLauncher {
                app_id: "org.example.Other".into(),
                exec: launcher.exec.clone(),
            },
        );
        assert!(resolve_desktop(&p, "browser", None)
            .unwrap_err()
            .contains("Ambiguous"));
        // Metadata is required on both entries; shared executable text is insufficient.
        p.flatpak_launchers.insert("mios-browser".into(), launcher);
        fs::write(
            &export,
            source.replace("X-Flatpak=org.example.Browser\n", ""),
        )
        .unwrap();
        assert!(resolve_desktop(&p, "browser", None)
            .unwrap_err()
            .contains("Ambiguous"));
    }

    #[test]
    fn registry_preserves_flatpak_exec_flags_and_rejects_changed_wrappers() {
        let (dir, p) = flatpak_registry_fixture();
        let export = dir.path().join("org.example.Browser.desktop");
        let wrapper = dir.path().join("mios-browser.desktop");
        let original_export = fs::read_to_string(&export).unwrap();
        let original_wrapper = fs::read_to_string(&wrapper).unwrap();
        for (old, new) in [
            ("--branch=stable", "--branch=beta"),
            ("--arch=x86_64", "--arch=aarch64"),
            ("--command=launch-script.sh", "--command=other-command"),
            ("--file-forwarding ", ""),
            (" @@u", " --private-window @@u"),
            (" %u ", " %U "),
            ("/usr/bin/flatpak run", "/tmp/flatpak run"),
            ("/usr/bin/flatpak run", "/usr/bin/flatpak install"),
            ("Exec=/usr/bin/flatpak", "Exec=unrelated-program"),
        ] {
            fs::write(&export, original_export.replace(old, new)).unwrap();
            assert!(
                resolve_desktop(&p, "browser", None)
                    .unwrap_err()
                    .contains("Ambiguous"),
                "{old} -> {new}"
            );
        }
        fs::write(&export, &original_export).unwrap();
        // Even if metadata still matches, a changed user/system wrapper is not
        // the trusted launcher declaration.
        for (old, new) in [
            ("mios-flatpak-run", "other-wrapper"),
            ("--branch=stable", "--branch=beta"),
            (" @@u", " --private-window @@u"),
        ] {
            fs::write(&wrapper, original_wrapper.replace(old, new)).unwrap();
            assert!(
                resolve_desktop(&p, "browser", None)
                    .unwrap_err()
                    .contains("Ambiguous"),
                "{old} -> {new}"
            );
        }
        fs::write(&wrapper, original_wrapper).unwrap();
        assert_eq!(
            resolve_desktop(&p, "browser", None).unwrap()["desktop_id"],
            "mios-browser"
        );
    }

    #[test]
    fn registry_preserves_hidden_and_user_overrides_when_coalescing_flatpaks() {
        let (dir, mut p) = flatpak_registry_fixture();
        let user_dir = tempfile::tempdir().unwrap();
        let overrides = user_dir.path();
        p.application_dirs
            .insert(0, overrides.display().to_string());
        fs::write(
            overrides.join("mios-browser.desktop"),
            "[Desktop Entry]\nHidden=true\n",
        )
        .unwrap();
        assert_eq!(
            resolve_desktop(&p, "browser", None).unwrap()["desktop_id"],
            "org.example.Browser"
        );
        assert!(resolve_desktop(&p, "mios-browser", None).is_err());
        fs::write(
            overrides.join("mios-browser.desktop"),
            "[Desktop Entry]\nName=Example Browser\nStartupWMClass=browser\nExec=custom-browser\n",
        )
        .unwrap();
        assert!(resolve_desktop(&p, "browser", None)
            .unwrap_err()
            .contains("Ambiguous"));
        let source = fs::read_to_string(dir.path().join("mios-browser.desktop")).unwrap();
        fs::write(overrides.join("mios-browser.desktop"), source).unwrap();
        assert_eq!(
            resolve_desktop(&p, "browser", None).unwrap()["desktop_file"],
            overrides.join("mios-browser.desktop").display().to_string()
        );
        fs::write(
            overrides.join("org.example.Browser.desktop"),
            "[Desktop Entry]\nHidden=true\n",
        )
        .unwrap();
        assert_eq!(
            resolve_desktop(&p, "browser", None).unwrap()["desktop_id"],
            "mios-browser"
        );
        fs::write(
            overrides.join("mios-browser.desktop"),
            "[Desktop Entry]\nHidden=true\n",
        )
        .unwrap();
        assert!(resolve_desktop(&p, "browser", None)
            .unwrap_err()
            .contains("not installed"));
    }

    #[test]
    fn bounded_probe_reports_real_failure_and_kills_its_timed_out_group() {
        let policy = policy();
        let failed = bounded(
            &policy,
            "sh",
            &["-c".into(), "printf 'probe failure' >&2; exit 7".into()],
            Instant::now() + Duration::from_secs(1),
        )
        .unwrap();
        assert_eq!(failed.0, 7);
        assert!(failed.1.contains("probe failure"));
        let started = Instant::now();
        let result = bounded(
            &policy,
            "sh",
            &["-c".into(), "sleep 30 & wait".into()],
            started + Duration::from_secs(1),
        );
        assert!(result.unwrap_err().contains("timed out"));
        assert!(started.elapsed() < Duration::from_secs(1));
    }
    #[test]
    fn file_capture_does_not_deadlock_when_probe_fills_a_pipe_buffer() {
        let mut policy = policy();
        policy.probe_timeout_ms = 1000;
        let (code, text) = bounded(
            &policy,
            "sh",
            &[
                "-c".into(),
                "head -c 100000 /dev/zero | tr '\\000' x".into(),
            ],
            Instant::now() + Duration::from_secs(2),
        )
        .unwrap();
        assert_eq!(code, 0);
        assert_eq!(text.len(), 8192);
    }
    #[test]
    fn active_daemon_is_not_a_substitute_for_the_actual_portal_mount() {
        let point = Path::new("/run/user/1000/doc");
        assert!(portal_mounted(
            "57 1 0:99 / /run/user/1000/doc rw - fuse.portal portal rw\n",
            point
        ));
        assert!(!portal_mounted(
            "57 1 0:99 / /run/user/1000 rw - tmpfs tmpfs rw\n",
            point
        ));
        assert!(!portal_mounted(
            "57 1 0:99 / /run/user/1000/doc2 rw - fuse.portal portal rw\n",
            point
        ));
    }
    #[test]
    fn application_ids_cannot_escape_desktop_search_roots() {
        for id in ["../spotify", "/bin/sh", "bad\napp", "-option", ".", ".."] {
            assert!(!valid_id(id));
        }
        for id in [
            "com.spotify.Client",
            "org.example.App.desktop",
            "mios-terminal",
        ] {
            assert!(valid_id(id));
        }
    }
    #[test]
    fn desktop_actions_do_not_override_the_main_application() {
        let fields=desktop_fields("[Desktop Entry]\nExec=main %U\nX-Flatpak=org.example.App\n[Desktop Action Other]\nExec=other\n");
        assert_eq!(fields.get("Exec").map(String::as_str), Some("main %U"));
        assert_eq!(
            fields.get("X-Flatpak").map(String::as_str),
            Some("org.example.App")
        );
    }
}

// Dispatch acknowledges exec, never a window's lifetime or visibility. The finite
// scope child owns the actual executable-start handshake.
fn dispatch_target(deadline: Instant) -> Result<Value, String> {
    let expected = env::var("MIOS_DESKTOP_DISPATCH_TARGET")
        .ok()
        .map(|raw| serde_json::from_str::<Value>(&raw))
        .transpose()
        .map_err(|_| "Invalid requested desktop target")?;
    let signature = env::var("HYPRLAND_INSTANCE_SIGNATURE").unwrap_or_default();
    let target = if signature.is_empty() {
        if !env::var_os("DISPLAY").is_some_and(|s| !s.is_empty())
            && !env::var_os("WAYLAND_DISPLAY").is_some_and(|s| !s.is_empty())
        {
            return Err("Graphical session is unavailable".into());
        }
        Value::Null
    } else {
        if !inventory_name(&signature) {
            return Err("Invalid compositor instance".into());
        }
        let uid = rustix::process::getuid().as_raw();
        let runtime =
            PathBuf::from(env::var_os("XDG_RUNTIME_DIR").ok_or("Missing desktop runtime")?);
        if !runtime.is_absolute()
            || !inventory_owned(&runtime, uid, false)
            || fs::canonicalize(&runtime).ok().as_ref() != Some(&runtime)
        {
            return Err("Desktop runtime ownership changed".into());
        }
        let control = runtime.join("hypr").join(&signature).join(".socket.sock");
        if fs::canonicalize(&control).ok().as_ref() != Some(&control) {
            return Err("Compositor socket path changed".into());
        }
        let display =
            env::var("WAYLAND_DISPLAY").map_err(|_| "Missing selected Wayland display")?;
        if !inventory_name(&display) || !inventory_owned(&runtime.join(display), uid, true) {
            return Err("Selected display ownership changed".into());
        }
        inventory_version(&control, uid, deadline)?;
        json!({"id":control,"platform":"linux","session_id":signature,"backend":"hyprland"})
    };
    if expected.is_some_and(|expected| expected != target) {
        return Err("Selected desktop target changed before dispatch".into());
    }
    Ok(target)
}

fn desktop_string(raw: &str) -> Result<String, String> {
    let mut chars = raw.chars();
    let mut value = String::new();
    while let Some(c) = chars.next() {
        if c != '\\' {
            value.push(c);
            continue;
        }
        value.push(match chars.next() {
            Some('s') => ' ',
            Some('n') => '\n',
            Some('t') => '\t',
            Some('r') => '\r',
            Some('\\') => '\\',
            _ => return Err("Invalid desktop entry string escape".into()),
        });
    }
    if value.contains('\0') {
        return Err("NUL in desktop entry".into());
    }
    Ok(value)
}

fn exec_words(raw: &str) -> Result<Vec<String>, String> {
    let decoded = desktop_string(raw)?;
    let mut chars = decoded.chars();
    let mut words = Vec::new();
    let mut word = String::new();
    let mut quoted = false;
    let mut started = false;
    while let Some(c) = chars.next() {
        match c {
            '"' => {
                quoted = !quoted;
                started = true;
            }
            '\\' => {
                let next = chars.next().ok_or("Unfinished desktop Exec escape")?;
                if quoted && !matches!(next, '"' | '$' | '\\') && next as u32 != 96 {
                    return Err("Invalid quoted desktop Exec escape".into());
                }
                word.push(next);
                started = true;
            }
            c if !quoted && c.is_whitespace() => {
                if started {
                    words.push(std::mem::take(&mut word));
                    started = false;
                }
            }
            c => {
                word.push(c);
                started = true;
            }
        }
    }
    if quoted {
        return Err("Unclosed desktop Exec quote".into());
    }
    if started {
        words.push(word);
    }
    if words
        .first()
        .is_none_or(|word| word.is_empty() || word.contains('='))
    {
        return Err("Missing or invalid desktop Exec program".into());
    }
    Ok(words)
}

fn desktop_argv(
    policy: &Policy,
    path: &Path,
    fields: &std::collections::BTreeMap<String, String>,
    args: &[String],
) -> Result<Vec<String>, String> {
    let words = exec_words(fields.get("Exec").ok_or("Desktop entry has no Exec")?)?;
    let mut argv = Vec::new();
    let mut resource_field = false;
    for word in words {
        if matches!(word.as_str(), "%F" | "%U" | "%f" | "%u") {
            if resource_field {
                return Err("Repeated desktop resource field".into());
            }
            resource_field = true;
            if matches!(word.as_str(), "%f" | "%u") && args.len() > 1 {
                return Err(
                    "Single-resource desktop entry cannot launch multiple resources once".into(),
                );
            }
            argv.extend(args.iter().cloned());
            continue;
        }
        if word == "%i" {
            if let Some(icon) = fields.get("Icon").filter(|s| !s.is_empty()) {
                argv.extend(["--icon".into(), desktop_string(icon)?]);
            }
            continue;
        }
        let mut expanded = String::new();
        let mut chars = word.chars();
        while let Some(c) = chars.next() {
            if c != '%' {
                expanded.push(c);
                continue;
            }
            match chars.next() {
                Some('%') => expanded.push('%'),
                Some('c') => expanded.push_str(&desktop_string(
                    fields.get("Name").map_or("", String::as_str),
                )?),
                Some('k') => expanded.push_str(path.to_str().ok_or("Non-UTF8 desktop path")?),
                Some('d' | 'D' | 'n' | 'N' | 'v' | 'm') => {}
                _ => return Err("Invalid or embedded desktop Exec field code".into()),
            }
        }
        if !expanded.is_empty() || word.is_empty() {
            argv.push(expanded);
        }
    }
    if !resource_field {
        argv.extend_from_slice(args);
    }
    if argv.first().is_none_or(String::is_empty) {
        return Err("Empty desktop command".into());
    }
    if fields.get("Terminal").is_some_and(|s| s == "true") {
        if policy.terminal_command.is_empty() {
            return Err("Terminal command is not configured".into());
        }
        let name = desktop_string(fields.get("Name").map_or("", String::as_str))?;
        let mut terminal: Vec<String> = policy
            .terminal_command
            .iter()
            .map(|s| s.replace("{name}", &name))
            .collect();
        terminal.extend(argv);
        argv = terminal;
    }
    Ok(argv)
}

fn dispatch_entry(policy: &Policy, query: &str) -> Result<DesktopEntry, String> {
    let requested = Path::new(query);
    let requested_path = requested
        .is_absolute()
        .then(|| fs::canonicalize(requested))
        .transpose()
        .map_err(|e| format!("Cannot resolve desktop entry: {e}"))?;
    let mut matches = desktop_entries(policy, None)?
        .into_iter()
        .filter(|(id, path, _)| {
            if let Some(requested) = &requested_path {
                fs::canonicalize(path).is_ok_and(|path| &path == requested)
            } else {
                id == query.trim_end_matches(".desktop")
            }
        });
    let entry = matches
        .next()
        .ok_or("Desktop entry is not in the installed registry")?;
    if matches.next().is_some() {
        return Err("Ambiguous installed desktop entry".into());
    }
    Ok(entry)
}

fn multiple_single_resources(
    fields: &std::collections::BTreeMap<String, String>,
    args: &[String],
) -> Result<bool, String> {
    if args.len() <= 1 {
        return Ok(false);
    }
    fields
        .get("Exec")
        .map(|raw| {
            exec_words(raw).map(|words| {
                words
                    .iter()
                    .any(|word| matches!(word.as_str(), "%f" | "%u"))
            })
        })
        .transpose()
        .map(|value| value.unwrap_or(false))
}

fn dispatch_exec(
    policy: &Policy,
    entry: &DesktopEntry,
    args: &[String],
    target: Value,
) -> Result<Value, String> {
    let (id, path, fields) = entry;
    if let Some(program) = fields.get("TryExec").filter(|s| !s.is_empty()) {
        if !executable(&desktop_string(program)?) {
            return Err("Desktop TryExec is unavailable".into());
        }
    }
    if !fields.get("Exec").is_some_and(|s| !s.is_empty())
        || multiple_single_resources(fields, args)?
    {
        return dispatch_activation(policy, entry, args, target);
    }
    let argv = desktop_argv(policy, path, fields, args)?;
    let mut command = Command::new(&argv[0]);
    command
        .args(&argv[1..])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .process_group(0);
    for (key, _) in env::vars_os().filter(|(key, _)| key.to_string_lossy().starts_with("MIOS_")) {
        command.env_remove(key);
    }
    if let Some(directory) = fields.get("Path").filter(|s| !s.is_empty()) {
        let directory = desktop_string(directory)?;
        if !Path::new(&directory).is_absolute() {
            return Err("Desktop working directory is not absolute".into());
        }
        command.current_dir(directory);
    }
    command.env("GIO_LAUNCHED_DESKTOP_FILE", path);
    // std's exec-error pipe closes only after exec succeeds, including PATH
    // resolution and interpreter errors. Dropping Child never waits for the app.
    let child = command
        .spawn()
        .map_err(|e| format!("Cannot execute desktop application: {e}"))?;
    Ok(
        json!({"dispatched":true,"confirmed":false,"method":"exec","pid":child.id(),
        "desktop_id":id,"desktop_file":path,"execution_target":target}),
    )
}

// D-Bus-only entries have no executable to acknowledge. GIO's successful
// activation reply is reported explicitly, without inventing an application PID.
fn dispatch_activation(
    policy: &Policy,
    entry: &DesktopEntry,
    args: &[String],
    target: Value,
) -> Result<Value, String> {
    let mut command = if executable(&policy.activation_program) {
        let mut command = Command::new(&policy.activation_program);
        command.arg("launch").arg(&entry.1);
        command
    } else if executable(&policy.activation_fallback_program) {
        let mut command = Command::new(&policy.activation_fallback_program);
        command.arg(&entry.0);
        command
    } else {
        return Err("Desktop activation executables are unavailable".into());
    };
    command
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .process_group(0);
    for (key, _) in env::vars_os().filter(|(key, _)| key.to_string_lossy().starts_with("MIOS_")) {
        command.env_remove(key);
    }
    let mut child = command
        .spawn()
        .map_err(|e| format!("Cannot start desktop activation: {e}"))?;
    let deadline = Instant::now() + Duration::from_millis(policy.overall_timeout_ms);
    loop {
        if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
            if !status.success() {
                return Err("Desktop activation was rejected".into());
            }
            return Ok(
                json!({"dispatched":true,"confirmed":false,"method":"activation","pid":null,
                "desktop_id":entry.0,"desktop_file":entry.1,"execution_target":target}),
            );
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err("Desktop activation outcome unavailable; refusing duplicate launch".into());
        }
        thread::sleep(Duration::from_millis(5));
    }
}

fn scope_receipt(
    policy: &Policy,
    root: &Path,
    entry: &DesktopEntry,
    args: &[String],
    target: &Value,
) -> Result<Value, String> {
    let mut output = tempfile::tempfile().map_err(|e| e.to_string())?;
    let mut errors = tempfile::tempfile().map_err(|e| e.to_string())?;
    let own = env::current_exe().map_err(|e| e.to_string())?;
    let token = tempfile::Builder::new()
        .prefix(&policy.scope_prefix)
        .tempfile()
        .map_err(|e| e.to_string())?;
    let unit = token
        .path()
        .file_name()
        .and_then(|s| s.to_str())
        .ok_or("Invalid scope token")?;
    let mut command = Command::new(&policy.scope_program);
    command
        .args([
            "--user",
            "--scope",
            "--collect",
            "--quiet",
            "--expand-environment=no",
        ])
        .arg(format!("--slice={}", policy.scope_slice))
        .arg(format!("--unit={unit}"))
        .arg("--")
        .arg(own)
        .args(["desktop-dispatch", "--in-scope", "--root"])
        .arg(root)
        .arg("--")
        .arg(&entry.1)
        .args(args)
        .env("MIOS_DESKTOP_DISPATCH_TARGET", target.to_string())
        .stdin(Stdio::null())
        .stdout(output.try_clone().map_err(|e| e.to_string())?)
        .stderr(errors.try_clone().map_err(|e| e.to_string())?)
        .process_group(0);
    let mut child = command
        .spawn()
        .map_err(|e| format!("Cannot start desktop scope: {e}"))?;
    let deadline = Instant::now() + Duration::from_millis(policy.overall_timeout_ms);
    let status = loop {
        if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
            break status;
        }
        if Instant::now() >= deadline {
            // The app may already be alive: reap only our finite waiter. No
            // process-group signal, duplicate launch or fallback is permitted.
            let _ = child.kill();
            let _ = child.wait();
            return Err("Desktop dispatch outcome unavailable; refusing duplicate launch".into());
        }
        thread::sleep(Duration::from_millis(5));
    };
    output.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
    errors.seek(SeekFrom::Start(0)).map_err(|e| e.to_string())?;
    let mut text = String::new();
    let mut error = String::new();
    output
        .take(65537)
        .read_to_string(&mut text)
        .map_err(|e| e.to_string())?;
    errors
        .take(8192)
        .read_to_string(&mut error)
        .map_err(|e| e.to_string())?;
    if !status.success() {
        return Err(format!("Desktop scope failed: {error}"));
    }
    let receipt: Value =
        serde_json::from_str(&text).map_err(|_| "Desktop scope returned no valid exec receipt")?;
    if receipt["dispatched"] != true
        || !matches!(receipt["method"].as_str(), Some("exec" | "activation"))
        || (receipt["method"] == "exec" && receipt["pid"].as_u64().is_none_or(|pid| pid == 0))
        || receipt["desktop_file"] != json!(entry.1)
        || receipt["desktop_id"] != entry.0
        || &receipt["execution_target"] != target
    {
        return Err(
            "Desktop scope exec receipt does not match the selected application and target".into(),
        );
    }
    Ok(receipt)
}

pub fn dispatch(root: &Path, desktop: &str, args: &[String], in_scope: bool) -> Result<(), String> {
    let policy = Policy::read(root)?;
    let deadline = Instant::now() + Duration::from_millis(policy.probe_timeout_ms);
    let target = dispatch_target(deadline)?;
    let entry = dispatch_entry(&policy, desktop)?;
    // Parse before asking the manager to create a scope; invalid entries never fork.
    if entry.2.get("Exec").is_some_and(|s| !s.is_empty()) {
        // A single-resource desktop entry may intentionally ask GIO to
        // activate one instance per resource. That is one acknowledged
        // activation request, never a retry of an unknown dispatch.
        let validation_args = if multiple_single_resources(&entry.2, args)? {
            &args[..1]
        } else {
            args
        };
        let _ = desktop_argv(&policy, &entry.1, &entry.2, validation_args)?;
    }
    let receipt = if in_scope {
        dispatch_exec(&policy, &entry, args, target)?
    } else if executable(&policy.scope_program)
        && bounded(
            &policy,
            &policy.systemctl_program,
            &["--user".into(), "show-environment".into()],
            deadline,
        )
        .is_ok_and(|(code, _)| code == 0)
    {
        scope_receipt(&policy, root, &entry, args, &target)?
    } else {
        dispatch_exec(&policy, &entry, args, target)?
    };
    println!(
        "{}",
        serde_json::to_string(&receipt).map_err(|e| e.to_string())?
    );
    Ok(())
}
