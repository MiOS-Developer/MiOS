// AI-hint: Shared native application preflight: validate desktop entries, recover only a missing document-portal mount under a user lock, and probe installed Flatpak sandboxes with bounded commands.
// AI-related: usr/share/mios/mios.toml [desktop.launch], usr/share/mios/quickshell/AppLaunch.qml, usr/libexec/mios/mios-flatpak-preflight
// AI-functions: run, check, ensure_portal, bounded, portal_mounted

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
    terminal_command: Vec<String>,
    application_dirs: Vec<String>,
}
impl Policy {
    fn read(root: &Path) -> Result<Self, String> {
        let ssot = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
        let p: Self = serde_json::from_value(
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
            || [&p.flatpak_program, &p.systemctl_program, &p.signal_program]
                .iter()
                .any(|s| s.is_empty() || s.chars().any(char::is_control))
        {
            return Err("invalid desktop.launch programs or application directories".into());
        }
        Ok(p)
    }
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
            terminal_command: vec!["true".into()],
            application_dirs: vec!["/usr/share/applications".into()],
        }
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
