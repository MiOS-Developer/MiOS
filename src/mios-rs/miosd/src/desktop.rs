// AI-hint: Event-driven TTY desktop background; applications, dock, and native window geometry remain owned by tmux.
// AI-related: /usr/share/mios/templates/rust, usr/share/mios/mios.toml [keybindings.tmux.desktop], tools/native/mios-unit-gen/src/tmux_desktop.rs
// AI-functions: open, reserve_config, run, config, paint, settle_resize
//! The desktop's anchor pane is a quiet background, not a compositor. It does
//! not poll the process list, animate, resize sibling panes, or capture mouse
//! input. The generated tmux dock and border bindings own those interactions.

use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::fs::{FileTypeExt, MetadataExt, OpenOptionsExt};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};
use std::time::{Duration, Instant};

use mios_resolver::popup::{PopupPolicy, SurfaceKind};
use ratatui::buffer::Buffer;
use ratatui::crossterm::event::{self, Event};
use ratatui::layout::{Alignment, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::Line;
use ratatui::widgets::{Paragraph, Widget, Wrap};
use serde::{Deserialize, Serialize};
use toml::Value;

const OWNER: &str = "@mios-desktop-origin";

#[derive(Debug)]
struct StartConfig {
    root: PathBuf,
    binary: PathBuf,
    socket: PathBuf,
    runtime: PathBuf,
    session_name: String,
    window_name: String,
    columns: u16,
    rows: u16,
    dock: bool,
    dock_rows: u16,
    titles: bool,
    refresh: u16,
    command_timeout: Duration,
    default_shell: String,
    default_command: String,
}

fn string(doc: &Value, key: &str) -> Result<String, String> {
    at(doc, key)?
        .as_str()
        .filter(|s| !s.is_empty() && !s.chars().any(char::is_control))
        .map(str::to_owned)
        .ok_or_else(|| format!("{key} must be nonempty plain text"))
}

fn basename(value: &str) -> bool {
    !value.is_empty()
        && value != "."
        && value != ".."
        && value
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
}

fn shell_policy(doc: &Value) -> Result<(String, String), String> {
    let shell = mios_resolver::terminal_tuis::catalog(doc)?
        .into_iter()
        .find(|app| app.id == "terminal-shell")
        .ok_or("the SSOT terminal-shell catalogue entry is missing")?;
    let executable = shell
        .argv
        .first()
        .ok_or("terminal-shell has no executable")?
        .clone();
    if !Path::new(&executable).is_absolute() {
        return Err("terminal-shell executable must be an absolute shell path".into());
    }
    let command = shell
        .argv
        .iter()
        .map(|arg| format!("'{}'", arg.replace('\'', "'\\''")))
        .collect::<Vec<_>>()
        .join(" ");
    Ok((executable, command))
}

fn control_environment_key(key: &str) -> bool {
    matches!(
        key,
        "HOME" | "PATH" | "TERM" | "LANG" | "USER" | "LOGNAME" | "SHELL" | "TMPDIR" | "TMUX_TMPDIR"
    ) || key.starts_with("LC_")
}

fn command_environment(command: &mut Command, control: bool) {
    if control {
        // A query needs neither application credentials nor the expanded
        // configuration. Keep its IDENTIFY message small, as in the launcher.
        command.env_clear();
        for (key, value) in std::env::vars_os() {
            if control_environment_key(&key.to_string_lossy()) {
                command.env(key, value);
            }
        }
    } else {
        // The server and attached applications need the caller's runtime bus,
        // resolver source paths, and preferences. Remove only nesting state.
        command.env_remove("TMUX").env_remove("TMUX_PANE");
    }
}

impl StartConfig {
    fn read(root: &Path, doc: &Value) -> Result<Self, String> {
        let _ = config(doc)?;
        let section = "keybindings.tmux.desktop";
        let socket_name = string(doc, &format!("{section}.socket_name"))?;
        let session_name = string(doc, &format!("{section}.session_name"))?;
        if !basename(&socket_name) || !basename(&session_name) {
            return Err("desktop socket_name and session_name must be safe basenames".into());
        }
        if at(doc, "keybindings.socket_name")
            .ok()
            .and_then(Value::as_str)
            == Some(socket_name.as_str())
            || at(doc, "agent.socket_name").ok().and_then(Value::as_str)
                == Some(socket_name.as_str())
        {
            return Err(
                "the desktop must use its own socket, separate from human and agent servers".into(),
            );
        }
        let binary = PathBuf::from(string(doc, &format!("{section}.binary"))?);
        let socket_root = PathBuf::from(string(doc, "terminal.socket_root")?);
        if !binary.is_absolute() || !socket_root.is_absolute() {
            return Err("desktop.binary and terminal.socket_root must be absolute".into());
        }
        let binary = binary
            .canonicalize()
            .map_err(|e| format!("desktop binary: {e}"))?;
        let metadata = fs::metadata(&binary).map_err(|e| e.to_string())?;
        if !metadata.is_file() || metadata.mode() & 0o111 == 0 || metadata.mode() & 0o022 != 0 {
            return Err(
                "the desktop binary must be an executable file without group/world writes".into(),
            );
        }
        // SAFETY: getuid has no arguments or memory effects visible to Rust.
        let uid = unsafe { libc::getuid() };
        let socket = socket_root.join(format!("tmux-{uid}")).join(&socket_name);
        let runtime = socket_root
            .join(format!("mios-desktop-{uid}"))
            .join(&socket_name);
        let flag = |name: &str| -> Result<bool, String> {
            at(doc, &format!("{section}.{name}"))?
                .as_bool()
                .ok_or_else(|| format!("{section}.{name} must be boolean"))
        };
        let (default_shell, default_command) = shell_policy(doc)?;
        Ok(Self {
            root: root.canonicalize().map_err(|e| e.to_string())?,
            binary,
            socket,
            runtime,
            session_name,
            window_name: string(doc, &format!("{section}.labels.canvas_title"))?,
            columns: number(doc, "terminal.cols", 1, 4096)?,
            rows: number(doc, "terminal.rows", 1, 4096)?,
            dock: flag("dock")?,
            dock_rows: number(doc, &format!("{section}.dock_rows"), 2, 2)?,
            titles: flag("pane_titles")?,
            refresh: number(doc, &format!("{section}.dock_refresh_seconds"), 1, 3600)?,
            command_timeout: Duration::from_millis(u64::from(number(
                doc,
                &format!("{section}.command_timeout_ms"),
                100,
                60000,
            )?)),
            default_shell,
            default_command,
        })
    }

    fn command(&self) -> Command {
        let mut command = Command::new(&self.binary);
        // Controller discovery recognizes tmux clients by argv[0], independently of the trusted executable path.
        command.arg0("tmux").arg("-S").arg(&self.socket);
        command_environment(&mut command, false);
        command
    }

    fn control_command(&self) -> Command {
        let mut command = self.command();
        command_environment(&mut command, true);
        command
    }

    fn bounded(&self, command: &mut Command, purpose: &str) -> Result<Output, String> {
        crate::sessions::run_bounded(command, Instant::now() + self.command_timeout)
            .map_err(|e| format!("desktop {purpose}: {e}"))
    }

    fn tmux(&self, args: &[&str]) -> Result<String, String> {
        let output = self.bounded(self.control_command().args(args), "tmux control query")?;
        if !output.status.success() {
            return Err(format!(
                "desktop tmux {}: {}",
                args.first().unwrap_or(&"command"),
                String::from_utf8_lossy(&output.stderr).trim()
            ));
        }
        String::from_utf8(output.stdout)
            .map(|s| s.trim_end_matches(['\r', '\n']).to_owned())
            .map_err(|e| e.to_string())
    }
}

fn private_dir(path: &Path) -> Result<(), String> {
    use std::os::unix::fs::DirBuilderExt;
    match fs::DirBuilder::new().mode(0o700).create(path) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(e) => return Err(format!("{}: {e}", path.display())),
    }
    let metadata = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    // SAFETY: getuid is a plain identity syscall.
    if !metadata.is_dir()
        || metadata.uid() != unsafe { libc::getuid() }
        || metadata.mode() & 0o077 != 0
    {
        return Err(format!(
            "{} must be an owned private directory",
            path.display()
        ));
    }
    Ok(())
}

/// An advisory lock serializes creation and receipt publication for this owned
/// namespace. It is released before attach, so another client can attach too.
fn reservation_lock(path: &Path, timeout: Duration) -> Result<File, String> {
    let lock = OpenOptions::new()
        .read(true)
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
        .map_err(|e| e.to_string())?;
    let metadata = lock.metadata().map_err(|e| e.to_string())?;
    if !metadata.is_file() || metadata.mode() & 0o077 != 0 {
        return Err("desktop reservation lock is not a private file".into());
    }
    let deadline = Instant::now() + timeout;
    loop {
        // SAFETY: the file descriptor remains owned by `lock`; flock changes
        // only the advisory lock and never writes through a Rust pointer.
        if unsafe { libc::flock(lock.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0 {
            return Ok(lock);
        }
        let error = std::io::Error::last_os_error();
        if !matches!(
            error.kind(),
            std::io::ErrorKind::WouldBlock | std::io::ErrorKind::Interrupted
        ) {
            return Err(error.to_string());
        }
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return Err("desktop reservation lock timed out".into());
        }
        std::thread::sleep(remaining.min(Duration::from_millis(20)));
    }
}

fn process_start(pid: u32) -> Result<u64, String> {
    let stat = fs::read_to_string(format!("/proc/{pid}/stat")).map_err(|e| e.to_string())?;
    let rest = stat.rsplit_once(')').ok_or("malformed process identity")?.1;
    rest.split_whitespace()
        .nth(19)
        .ok_or("process start time is missing")?
        .parse()
        .map_err(|_| "invalid process start time".into())
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
struct Receipt {
    root: PathBuf,
    binary: PathBuf,
    socket: PathBuf,
    socket_device: u64,
    socket_inode: u64,
    server_pid: u32,
    server_start: u64,
    session: String,
    window: String,
    canvas: String,
    canvas_pid: u32,
    canvas_start: u64,
    origin: String,
}

/// Testable result of reserving a desktop without attaching to it. Only this
/// owned namespace is addressed; the human server and physical bridge survive.
#[derive(Clone, Debug, Serialize)]
pub struct Reservation {
    pub socket: PathBuf,
    pub session: String,
    pub window: String,
    pub canvas: String,
    pub server_pid: u32,
}

impl From<&Receipt> for Reservation {
    fn from(receipt: &Receipt) -> Self {
        Self {
            socket: receipt.socket.clone(),
            session: receipt.session.clone(),
            window: receipt.window.clone(),
            canvas: receipt.canvas.clone(),
            server_pid: receipt.server_pid,
        }
    }
}

fn socket_metadata(path: &Path) -> Result<fs::Metadata, String> {
    let metadata = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    // SAFETY: getuid is a plain identity syscall.
    if !metadata.file_type().is_socket() || metadata.uid() != unsafe { libc::getuid() } {
        return Err("desktop socket is not an owned Unix socket".into());
    }
    Ok(metadata)
}

fn id(value: &str, prefix: char) -> bool {
    value.starts_with(prefix) && value.len() > 1 && value[1..].bytes().all(|b| b.is_ascii_digit())
}

fn verify(cfg: &StartConfig, receipt: &Receipt) -> Result<(), String> {
    if receipt.root != cfg.root || receipt.binary != cfg.binary || receipt.socket != cfg.socket {
        return Err("desktop reservation belongs to a different root, binary, or socket".into());
    }
    if !id(&receipt.session, '$') || !id(&receipt.window, '@') || !id(&receipt.canvas, '%') {
        return Err("desktop reservation contains invalid source IDs".into());
    }
    let socket = socket_metadata(&cfg.socket)?;
    if socket.dev() != receipt.socket_device
        || socket.ino() != receipt.socket_inode
        || process_start(receipt.server_pid)? != receipt.server_start
        || process_start(receipt.canvas_pid)? != receipt.canvas_start
    {
        return Err(
            "desktop socket or process identity changed; existing applications were left intact"
                .into(),
        );
    }
    if cfg.tmux(&["show-options", "-gqv", OWNER])? != receipt.origin {
        return Err("the desktop server origin does not match its private reservation".into());
    }
    let current = cfg.tmux(&[
        "display-message",
        "-p",
        "-t",
        &receipt.canvas,
        "#{pid}\t#{session_id}\t#{window_id}\t#{pane_id}\t#{pane_pid}\t#{@mios-desktop-canvas}",
    ])?;
    let expected = format!(
        "{}\t{}\t{}\t{}\t{}\t1",
        receipt.server_pid, receipt.session, receipt.window, receipt.canvas, receipt.canvas_pid
    );
    if current != expected {
        return Err("the reserved desktop canvas was moved or replaced; existing applications were left intact".into());
    }
    let commands = cfg.tmux(&["list-commands"])?;
    if !commands.lines().any(|line| line.starts_with("float-pane ")) {
        return Err(
            "desktop binary lacks the native float-pane API; the owned server was left intact"
                .into(),
        );
    }
    Ok(())
}

fn scoped_options(cfg: &StartConfig, receipt: &Receipt) -> Result<(), String> {
    let theme = cfg.runtime.join("mios.omp.json");
    if !theme.is_file() {
        return Err("the generated SSOT shell theme is missing".into());
    }
    let theme = theme.to_str().ok_or("desktop theme path must be UTF-8")?;
    // Match mios-terminal's public shell startup. Apply to this owned server
    // and session, including reattach after a policy update; existing shells
    // keep running. The helper prompt belongs to agent workers, not this UI.
    for (name, value) in [
        ("MIOS_OMP_THEME", theme),
        ("MIOS_THEME_PROJECTED", "1"),
        ("MIOS_SESSION_BARE", "1"),
    ] {
        cfg.tmux(&["set-environment", "-g", name, value])?;
        cfg.tmux(&["set-environment", "-t", &receipt.session, name, value])?;
    }
    cfg.tmux(&["set-environment", "-gu", "MIOS_TMUX_HELPER"])?;
    cfg.tmux(&[
        "set-environment",
        "-u",
        "-t",
        &receipt.session,
        "MIOS_TMUX_HELPER",
    ])?;
    cfg.tmux(&[
        "set-option",
        "-t",
        &receipt.session,
        "default-shell",
        &cfg.default_shell,
    ])?;
    cfg.tmux(&[
        "set-option",
        "-t",
        &receipt.session,
        "default-command",
        &cfg.default_command,
    ])?;
    cfg.tmux(&["set-option", "-t", &receipt.session, "@mios-bare", "1"])?;
    cfg.tmux(&[
        "set-option",
        "-w",
        "-t",
        &receipt.window,
        "@mios-desktop-enabled",
        "1",
    ])?;
    cfg.tmux(&[
        "set-option",
        "-p",
        "-t",
        &receipt.canvas,
        "@mios-desktop-canvas",
        "1",
    ])?;
    if cfg.dock {
        let dock = cfg.tmux(&["show-options", "-gqv", "@mios-desktop-dock-format"])?;
        if dock.is_empty() {
            return Err("the generated desktop dock projection is missing".into());
        }
        cfg.tmux(&[
            "set-option",
            "-t",
            &receipt.session,
            "status",
            &cfg.dock_rows.to_string(),
        ])?;
        cfg.tmux(&[
            "set-option",
            "-t",
            &receipt.session,
            "status-position",
            "bottom",
        ])?;
        cfg.tmux(&[
            "set-option",
            "-t",
            &receipt.session,
            "status-interval",
            &cfg.refresh.to_string(),
        ])?;
        cfg.tmux(&[
            "set-option",
            "-t",
            &receipt.session,
            "status-format[1]",
            &dock,
        ])?;
    } else {
        cfg.tmux(&["set-option", "-t", &receipt.session, "status", "on"])?;
    }
    if cfg.titles {
        let border = cfg.tmux(&["show-options", "-gqv", "@mios-desktop-border-format"])?;
        if border.is_empty() {
            return Err("the generated desktop border projection is missing".into());
        }
        // screen-redraw.c puts the border at pane_left+2, with width
        // pane_width-2; a scrollbar would extend that coordinate contract.
        cfg.tmux(&[
            "set-option",
            "-w",
            "-t",
            &receipt.window,
            "pane-scrollbars",
            "off",
        ])?;
        cfg.tmux(&[
            "set-option",
            "-w",
            "-t",
            &receipt.window,
            "pane-border-status",
            "top",
        ])?;
        cfg.tmux(&[
            "set-option",
            "-w",
            "-t",
            &receipt.window,
            "pane-border-format",
            &border,
        ])?;
    } else {
        cfg.tmux(&[
            "set-option",
            "-w",
            "-t",
            &receipt.window,
            "pane-border-status",
            "off",
        ])?;
    }
    Ok(())
}

fn publish(path: &Path, receipt: &Receipt) -> Result<(), String> {
    let temporary = path.with_extension(format!("json.{}", std::process::id()));
    let mut output = OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(&temporary)
        .map_err(|e| e.to_string())?;
    let text = serde_json::to_vec_pretty(receipt).map_err(|e| e.to_string())?;
    output
        .write_all(&text)
        .and_then(|_| output.sync_all())
        .map_err(|e| e.to_string())?;
    fs::rename(&temporary, path).map_err(|e| e.to_string())
}

fn project_theme(cfg: &StartConfig) -> Result<PathBuf, String> {
    let mut command = Command::new("/usr/bin/mios-gen");
    command
        .arg("render-tmux-theme")
        .arg("--root")
        .arg(&cfg.root)
        .arg("--runtime")
        .arg(&cfg.runtime)
        .env_remove("TMUX")
        .env_remove("TMUX_PANE");
    let output = cfg.bounded(&mut command, "theme projection")?;
    if !output.status.success() {
        return Err(format!(
            "desktop theme projection: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    let conf = cfg.runtime.join("tmux.conf");
    // This setting belongs only to the dedicated server. Suppress the AI
    // workspace session-created hook before the first canvas can be created.
    OpenOptions::new()
        .append(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(&conf)
        .and_then(|mut file| writeln!(file, "\nset -g @mios-bare 1\nset -s exit-empty off"))
        .map_err(|e| e.to_string())?;
    Ok(conf)
}

fn reserve_config(cfg: &StartConfig) -> Result<Reservation, String> {
    let runtime_parent = cfg
        .runtime
        .parent()
        .ok_or("desktop runtime has no parent")?;
    private_dir(runtime_parent)?;
    private_dir(&cfg.runtime)?;
    private_dir(cfg.socket.parent().ok_or("desktop socket has no parent")?)?;
    let _lock = reservation_lock(&cfg.runtime.join("reservation.lock"), cfg.command_timeout)?;
    let path = cfg.runtime.join("reservation.json");
    let existing = match fs::symlink_metadata(&path) {
        Ok(metadata) => {
            if !metadata.is_file() || metadata.mode() & 0o077 != 0 {
                return Err("desktop receipt is not a private regular file".into());
            }
            let file = OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_NOFOLLOW)
                .open(&path)
                .map_err(|e| e.to_string())?;
            Some(
                serde_json::from_reader::<_, Receipt>(file)
                    .map_err(|e| format!("desktop receipt: {e}"))?,
            )
        }
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
        Err(e) => return Err(e.to_string()),
    };
    let socket_exists = match fs::symlink_metadata(&cfg.socket) {
        Ok(_) => true,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => false,
        Err(e) => return Err(format!("desktop socket: {e}")),
    };
    if socket_exists {
        let receipt = existing.ok_or(
            "the desktop socket already exists without an owned reservation; nothing was changed",
        )?;
        verify(cfg, &receipt)?;
        let conf = project_theme(cfg)?;
        let conf = conf.to_str().ok_or("desktop runtime path must be UTF-8")?;
        cfg.tmux(&["source-file", conf])?;
        scoped_options(cfg, &receipt)?;
        return Ok(Reservation::from(&receipt));
    }
    if existing
        .as_ref()
        .is_some_and(|receipt| process_start(receipt.server_pid).ok() == Some(receipt.server_start))
    {
        return Err(
            "the reserved desktop server is still alive without its socket; nothing was restarted"
                .into(),
        );
    }
    let conf = project_theme(cfg)?;
    let mut random = [0u8; 16];
    File::open("/dev/urandom")
        .and_then(|mut file| file.read_exact(&mut random))
        .map_err(|e| e.to_string())?;
    let origin = random
        .iter()
        .map(|n| format!("{n:02x}"))
        .collect::<String>();
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let mut command = cfg.command();
    command
        .env("MIOS_OMP_THEME", cfg.runtime.join("mios.omp.json"))
        .env("MIOS_THEME_PROJECTED", "1")
        .env("MIOS_SESSION_BARE", "1")
        .env_remove("MIOS_TMUX_HELPER")
        .arg("-f")
        .arg(&conf)
        .args([
            "new-session",
            "-d",
            "-P",
            "-F",
            "#{session_id}\t#{window_id}\t#{pane_id}\t#{pane_pid}\t#{pid}",
            "-s",
            &cfg.session_name,
            "-n",
            &cfg.window_name,
            "-x",
            &cfg.columns.to_string(),
            "-y",
            &cfg.rows.to_string(),
        ])
        .arg(&exe)
        .args(["desktop", "canvas", "--root"])
        .arg(&cfg.root);
    let output = cfg.bounded(&mut command, "start")?;
    if !output.status.success() {
        return Err(format!(
            "desktop start: {}",
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    let output = String::from_utf8(output.stdout).map_err(|e| e.to_string())?;
    let fields: Vec<&str> = output.trim().split('\t').collect();
    if fields.len() != 5 || !id(fields[0], '$') || !id(fields[1], '@') || !id(fields[2], '%') {
        return Err("desktop start did not return its exact canvas identity".into());
    }
    let canvas_pid = fields[3].parse::<u32>().map_err(|_| "invalid canvas PID")?;
    let server_pid = fields[4]
        .parse::<u32>()
        .map_err(|_| "invalid desktop server PID")?;
    let metadata = socket_metadata(&cfg.socket)?;
    let receipt = Receipt {
        root: cfg.root.clone(),
        binary: cfg.binary.clone(),
        socket: cfg.socket.clone(),
        socket_device: metadata.dev(),
        socket_inode: metadata.ino(),
        server_pid,
        server_start: process_start(server_pid)?,
        session: fields[0].into(),
        window: fields[1].into(),
        canvas: fields[2].into(),
        canvas_pid,
        canvas_start: process_start(canvas_pid)?,
        origin,
    };
    cfg.tmux(&["set-option", "-g", OWNER, &receipt.origin])?;
    // Publish before later capability/style checks. A failed optional setup can
    // be retried with the exact same server and canvas, without replacing apps.
    cfg.tmux(&[
        "set-option",
        "-p",
        "-t",
        &receipt.canvas,
        "@mios-desktop-canvas",
        "1",
    ])?;
    publish(&path, &receipt)?;
    let commands = cfg.tmux(&["list-commands"])?;
    if !commands.lines().any(|line| line.starts_with("float-pane ")) {
        return Err(
            "desktop binary lacks the native float-pane API; the owned server was left intact"
                .into(),
        );
    }
    scoped_options(cfg, &receipt)?;
    verify(cfg, &receipt)?;
    Ok(Reservation::from(&receipt))
}

/// Enter the dedicated desktop. Removing TMUX permits a nested client in the
/// human portal pane; the outer physical input bridge is deliberately reused.
pub fn open(root: &Path, no_attach: bool) -> Result<(), String> {
    let doc = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let cfg = StartConfig::read(root, &doc)?;
    if !no_attach
        && std::env::var_os("TMUX")
            .is_some_and(|value| value.to_string_lossy().split(',').next() == cfg.socket.to_str())
    {
        return Err("this client is already inside the MiOS desktop server".into());
    }
    let reservation = reserve_config(&cfg)?;
    if no_attach {
        println!(
            "{}",
            serde_json::to_string(&reservation).map_err(|e| e.to_string())?
        );
        return Ok(());
    }
    let status = cfg
        .command()
        .args(["attach-session", "-t", &reservation.session])
        .status()
        .map_err(|e| e.to_string())?;
    if status.success() {
        Ok(())
    } else {
        Err(format!("desktop client exited with {status}"))
    }
}

#[derive(Clone, Debug)]
struct Config {
    palette: [Color; 5],
    popup: PopupPolicy,
    topbar: String,
    title: String,
    subtitle: String,
    help: String,
    min_columns: u16,
    min_rows: u16,
    resize_debounce: Duration,
}

fn at<'a>(doc: &'a Value, key: &str) -> Result<&'a Value, String> {
    key.split('.')
        .try_fold(doc, |value, part| value.get(part))
        .ok_or_else(|| format!("missing SSOT key {key}"))
}

fn number(doc: &Value, key: &str, min: i64, max: i64) -> Result<u16, String> {
    at(doc, key)?
        .as_integer()
        .filter(|n| (min..=max).contains(n))
        .map(|n| n as u16)
        .ok_or_else(|| format!("{key} must be an integer in {min}..={max}"))
}

fn config(doc: &Value) -> Result<Config, String> {
    let term = std::env::var("TERM").unwrap_or_default();
    let remote = [
        "SSH_CONNECTION",
        "SSH_CLIENT",
        "SSH_TTY",
        "MIOS_REMOTE_TERMINAL",
    ]
    .iter()
    .any(|key| std::env::var_os(key).is_some_and(|value| !value.is_empty()));
    config_for_terminal(doc, &term, remote)
}

fn config_for_terminal(doc: &Value, term: &str, remote: bool) -> Result<Config, String> {
    const PREFIX: &str = "keybindings.tmux.desktop";
    if at(doc, &format!("{PREFIX}.enabled"))?.as_bool() != Some(true) {
        return Err("the MiOS TTY desktop is disabled in SSOT".into());
    }
    let label = |name: &str| -> Result<String, String> {
        let key = format!("{PREFIX}.labels.{name}");
        at(doc, &key)?
            .as_str()
            .filter(|s| mios_unit_gen::tmux_os::plain_text(s))
            .map(str::to_owned)
            .ok_or_else(|| format!("{key} must be safe printable desktop text"))
    };
    // Use the same TERM/SSH policy as the launcher: truecolour terminals use
    // the resolved SSOT palette; kernel VTs retain the named-colour fallback.
    // Inside tmux, its pane TERM describes the virtual terminal and tmux also
    // adapts the emitted colours separately for each attached physical client.
    let console = mios_unit_gen::tmux_os::console_client(doc, term, remote)?;
    let launcher = crate::launcher::config(doc, console)?;
    Ok(Config {
        palette: launcher.palette,
        popup: launcher.popup_policy,
        topbar: label("topbar")?,
        title: label("canvas_title")?,
        subtitle: label("canvas_subtitle")?,
        help: label("canvas_help")?,
        min_columns: number(doc, &format!("{PREFIX}.min_columns"), 1, 4096)?,
        min_rows: number(doc, &format!("{PREFIX}.min_rows"), 1, 4096)?,
        resize_debounce: Duration::from_millis(u64::from(number(
            doc,
            &format!("{PREFIX}.resize_debounce_ms"),
            1,
            1000,
        )?)),
    })
}

fn card(cfg: &Config, area: Rect) -> Rect {
    let (width, height) = cfg
        .popup
        .size(SurfaceKind::Tooltip, area.width, area.height);
    Rect::new(
        area.x.saturating_add(area.width.saturating_sub(width) / 2),
        area.y
            .saturating_add(area.height.saturating_sub(height) / 2),
        width,
        height,
    )
}

fn paint(cfg: &Config, area: Rect, buf: &mut Buffer) {
    let [bg, fg, accent, subtle, muted] = cfg.palette;
    buf.set_style(area, Style::default().bg(bg).fg(fg));
    if area.is_empty() {
        return;
    }
    let topbar = Rect::new(area.x, area.y, area.width, 1);
    let bar_style = Style::default()
        .bg(accent)
        .fg(bg)
        .add_modifier(Modifier::BOLD);
    buf.set_style(topbar, bar_style);
    buf.set_stringn(
        area.x,
        area.y,
        &cfg.topbar,
        usize::from(area.width),
        bar_style,
    );
    let content = Rect::new(
        area.x,
        area.y.saturating_add(1),
        area.width,
        area.height - 1,
    );
    let panel = card(cfg, content);
    if panel.is_empty() {
        return;
    }
    // ASCII borders work on the physical VT as well as terminal emulators.
    let border = Style::default().fg(subtle).bg(bg);
    for x in panel.x..panel.right() {
        buf.set_stringn(x, panel.y, "-", 1, border);
        buf.set_stringn(x, panel.bottom() - 1, "-", 1, border);
    }
    for y in panel.y..panel.bottom() {
        buf.set_stringn(panel.x, y, "|", 1, border);
        buf.set_stringn(panel.right() - 1, y, "|", 1, border);
    }
    for (x, y) in [
        (panel.x, panel.y),
        (panel.right() - 1, panel.y),
        (panel.x, panel.bottom() - 1),
        (panel.right() - 1, panel.bottom() - 1),
    ] {
        buf.set_stringn(x, y, "+", 1, border);
    }
    let inner = Rect::new(
        panel.x.saturating_add(1),
        panel.y.saturating_add(1),
        panel.width.saturating_sub(2),
        panel.height.saturating_sub(2),
    );
    if inner.is_empty() {
        return;
    }
    let mut lines = vec![Line::styled(
        cfg.title.as_str(),
        Style::default().fg(accent).add_modifier(Modifier::BOLD),
    )];
    if area.width >= cfg.min_columns && area.height >= cfg.min_rows {
        lines.push(Line::styled(cfg.subtitle.as_str(), Style::default().fg(fg)));
    }
    lines.push(Line::styled(cfg.help.as_str(), Style::default().fg(muted)));
    Paragraph::new(lines)
        .alignment(Alignment::Center)
        .wrap(Wrap { trim: true })
        .render(inner, buf);
}

/// Wait for a quiet resize interval, with a four-interval upper bound during a
/// continuous drag. Idle operation blocks in `event::read`; this timer exists
/// only after an actual resize and never drives a periodic repaint.
fn settle_resize(quiet: Duration) -> Result<(), String> {
    let now = Instant::now();
    let hard_deadline = now + quiet.saturating_mul(4);
    let mut quiet_deadline = now + quiet;
    loop {
        let now = Instant::now();
        let deadline = quiet_deadline.min(hard_deadline);
        if now >= deadline {
            return Ok(());
        }
        if !event::poll(deadline.saturating_duration_since(now)).map_err(|e| e.to_string())? {
            return Ok(());
        }
        if matches!(
            event::read().map_err(|e| e.to_string())?,
            Event::Resize(_, _)
        ) {
            quiet_deadline = Instant::now() + quiet;
        }
    }
}

#[derive(Default)]
struct Viewport {
    painted: Option<(u16, u16)>,
}

impl Viewport {
    fn changed(&self, width: u16, height: u16) -> bool {
        width > 0 && height > 0 && self.painted != Some((width, height))
    }

    fn painted(&mut self, area: Rect) {
        self.painted = Some((area.width, area.height));
    }
}

/// Run the persistent background in the desktop's anchor pane. Ordinary keys
/// leave the anchor alive: closing it could remove the final tiled pane while
/// applications still run in floating panes. Session detach/exit remains a
/// tmux action, where the exact client is known and application PIDs survive.
pub fn run(root: &Path) -> Result<(), String> {
    let doc = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let cfg = config(&doc)?;
    let _guard = crate::sessions::TermGuard::arm();
    let mut terminal = ratatui::try_init().map_err(|e| e.to_string())?;
    let mut viewport = Viewport::default();
    terminal
        .draw(|frame| {
            let area = frame.area();
            paint(&cfg, area, frame.buffer_mut());
            viewport.painted(area);
        })
        .map_err(|e| e.to_string())?;
    loop {
        if !matches!(
            event::read().map_err(|e| e.to_string())?,
            Event::Resize(_, _)
        ) {
            continue;
        }
        settle_resize(cfg.resize_debounce)?;
        let size = terminal.size().map_err(|e| e.to_string())?;
        if !viewport.changed(size.width, size.height) {
            continue;
        }
        terminal
            .draw(|frame| {
                let area = frame.area();
                paint(&cfg, area, frame.buffer_mut());
                viewport.painted(area);
            })
            .map_err(|e| e.to_string())?;
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::panic)]
mod tests {
    use super::*;
    use std::os::unix::fs::{symlink, PermissionsExt};

    fn vendor() -> Value {
        let mut doc: Value = include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap();
        let desktop: Value = r#"
enabled = true
min_columns = 34
min_rows = 10
resize_debounce_ms = 40
[labels]
topbar = "MiOS | Apps | Windows | System"
canvas_title = "MiOS Desktop"
canvas_subtitle = "Terminal applications in floating windows"
canvas_help = "Apps: Alt-Space | Window actions: right-click a border"
"#
        .parse()
        .unwrap();
        doc["keybindings"]["tmux"]
            .as_table_mut()
            .unwrap()
            .insert("desktop".into(), desktop);
        doc
    }

    fn text(buf: &Buffer) -> String {
        buf.content.iter().map(|cell| cell.symbol()).collect()
    }

    #[test]
    fn background_uses_existing_bounds_and_does_not_paint_outside_tiny_viewports() {
        let cfg = config(&vendor()).unwrap();
        let wide = Rect::new(0, 0, 240, 67);
        let panel = card(&cfg, wide);
        assert_eq!((panel.width, panel.height), (60, 6));
        assert_eq!(panel.x * 2 + panel.width, wide.width);
        assert!(panel.y * 2 + panel.height <= wide.height);
        let mut buf = Buffer::empty(wide);
        paint(&cfg, wide, &mut buf);
        assert!(text(&buf).contains("MiOS Desktop"));
        assert!(text(&buf).contains("Alt-Space"));
        assert!(text(&buf).starts_with("MiOS | Apps | Windows | System"));
        for width in 0..45 {
            for height in 0..12 {
                let area = Rect::new(7, 3, width, height);
                let panel = card(&cfg, area);
                assert!(panel.x >= area.x && panel.y >= area.y);
                assert!(panel.right() <= area.right() && panel.bottom() <= area.bottom());
                paint(&cfg, area, &mut Buffer::empty(area));
            }
        }
    }

    #[test]
    fn unchanged_and_zero_resizes_do_not_schedule_a_frame() {
        let mut viewport = Viewport::default();
        assert!(!viewport.changed(0, 20));
        assert!(viewport.changed(80, 24));
        viewport.painted(Rect::new(0, 0, 80, 24));
        for _ in 0..1000 {
            assert!(!viewport.changed(80, 24));
        }
        assert!(!viewport.changed(80, 0));
        assert!(viewport.changed(81, 24));
    }

    #[test]
    fn missing_timing_or_format_injection_is_rejected_before_entering_raw_mode() {
        let mut doc = vendor();
        doc["keybindings"]["tmux"]["desktop"]["resize_debounce_ms"] = Value::Integer(0);
        assert!(config(&doc).unwrap_err().contains("resize_debounce_ms"));
        let mut doc = vendor();
        doc["keybindings"]["tmux"]["desktop"]["labels"]["canvas_title"] =
            Value::String("#{pane_id}".into());
        assert!(config(&doc).unwrap_err().contains("canvas_title"));
    }

    #[test]
    fn canvas_palette_uses_shared_terminal_capability_policy() {
        let doc = vendor();
        let vt = config_for_terminal(&doc, "linux", false).unwrap();
        assert_eq!(
            vt.palette,
            crate::launcher::config(&doc, true).unwrap().palette
        );
        for (term, remote) in [
            ("xterm-256color", true),
            ("tmux-256color", false),
            ("linux", true),
        ] {
            let cfg = config_for_terminal(&doc, term, remote).unwrap();
            assert_eq!(
                cfg.palette,
                crate::launcher::config(&doc, false).unwrap().palette
            );
        }
    }

    #[test]
    fn default_shell_uses_the_existing_ssot_shell_catalogue() {
        let mut doc = vendor();
        let (shell, command) = shell_policy(&doc).unwrap();
        assert_eq!(shell, "/bin/bash");
        assert!(command.contains("'--noprofile'"));
        assert!(command.contains("'/usr/share/mios/tmux/mios-bashrc'"));
        let row = doc["terminal"]["tuis"]
            .as_array_mut()
            .unwrap()
            .iter_mut()
            .find(|row| row.get("id").and_then(Value::as_str) == Some("terminal-shell"))
            .unwrap();
        row["argv"] = Value::Array(
            [
                "/bin/bash",
                "--noprofile",
                "--rcfile",
                "/private/rc with spaces",
            ]
            .into_iter()
            .map(|arg| Value::String(arg.into()))
            .collect(),
        );
        assert!(shell_policy(&doc)
            .unwrap()
            .1
            .contains("'/private/rc with spaces'"));
    }

    #[test]
    fn lifecycle_refuses_human_namespace_and_derives_a_private_dedicated_socket() {
        let root = tempfile::tempdir().unwrap();
        let mut doc = vendor();
        let desktop = doc["keybindings"]["tmux"]["desktop"]
            .as_table_mut()
            .unwrap();
        for (key, value) in [
            ("binary", Value::String("/usr/bin/true".into())),
            ("socket_name", Value::String("mios-desktop-test".into())),
            ("session_name", Value::String("MiOS-Desktop".into())),
            ("dock", Value::Boolean(true)),
            ("pane_titles", Value::Boolean(true)),
            ("dock_rows", Value::Integer(2)),
            ("dock_refresh_seconds", Value::Integer(1)),
            ("command_timeout_ms", Value::Integer(3000)),
        ] {
            desktop.insert(key.into(), value);
        }
        let cfg = StartConfig::read(root.path(), &doc).unwrap();
        assert_eq!(cfg.socket.file_name().unwrap(), "mios-desktop-test");
        assert!(cfg
            .socket
            .parent()
            .unwrap()
            .file_name()
            .unwrap()
            .to_string_lossy()
            .starts_with("tmux-"));
        assert_ne!(cfg.socket, cfg.runtime);
        doc["keybindings"]["tmux"]["desktop"]["socket_name"] =
            doc["keybindings"]["socket_name"].clone();
        assert!(StartConfig::read(root.path(), &doc)
            .unwrap_err()
            .contains("own socket"));
    }

    #[test]
    fn lifecycle_does_not_adopt_symlinked_or_shared_state_directories() {
        let root = tempfile::tempdir().unwrap();
        let owned = root.path().join("owned");
        private_dir(&owned).unwrap();
        private_dir(&owned).unwrap();
        let shared = root.path().join("shared");
        fs::create_dir(&shared).unwrap();
        fs::set_permissions(&shared, fs::Permissions::from_mode(0o755)).unwrap();
        assert!(private_dir(&shared).is_err());
        let link = root.path().join("link");
        symlink(&owned, &link).unwrap();
        assert!(private_dir(&link).is_err());
        assert!(owned.is_dir());
    }

    #[test]
    fn control_queries_do_not_forward_expanded_config_or_credentials() {
        for key in ["HOME", "PATH", "TERM", "LANG", "LC_ALL", "TMUX_TMPDIR"] {
            assert!(control_environment_key(key));
        }
        for key in [
            "MIOS_CONFIG_JSON",
            "MIOS_AGENT_TOKEN",
            "OPENAI_API_KEY",
            "TMUX",
            "TMUX_PANE",
            "PYTHONPATH",
        ] {
            assert!(!control_environment_key(key));
        }
    }

    #[test]
    fn command_identity_keeps_the_trusted_backend_and_server_environment() {
        // GNU sort accepts -S as its buffer size. Holding stdin keeps this owned
        // fixture alive while /proc witnesses the command's real executable and
        // argv[0], without starting a tmux server or borrowing an operator pane.
        let cfg = StartConfig {
            root: "/".into(),
            binary: "/usr/bin/sort".into(),
            socket: "1M".into(),
            runtime: "/private/mios-desktop-identity-proof".into(),
            session_name: "MiOS-Desktop-Proof".into(),
            window_name: "MiOS Desktop".into(),
            columns: 80,
            rows: 24,
            dock: true,
            dock_rows: 2,
            titles: true,
            refresh: 1,
            command_timeout: Duration::from_secs(2),
            default_shell: "/bin/sh".into(),
            default_command: "/bin/sh".into(),
        };
        let mut backend = cfg.command();
        assert_eq!(backend.get_program(), cfg.binary.as_os_str());
        let mut child = backend
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn()
            .unwrap();
        let process = PathBuf::from(format!("/proc/{}", child.id()));
        // Linux closes the exec-error pipe before ELF initialization publishes
        // mm->arg_start/end. spawn may therefore succeed while /proc/cmdline is
        // briefly empty. Retry only that transient state, never a wrong argv.
        let deadline = Instant::now() + cfg.command_timeout;
        let observed = loop {
            match child.try_wait() {
                Ok(None) => (),
                Ok(Some(status)) => {
                    break Err(format!(
                        "identity fixture exited before publishing argv: {status}"
                    ))
                }
                Err(error) => break Err(format!("cannot inspect identity fixture: {error}")),
            }
            match fs::read(process.join("cmdline")) {
                Ok(argv) if !argv.is_empty() => {
                    break fs::read_link(process.join("exe"))
                        .map(|executable| (argv, executable))
                        .map_err(|error| format!("cannot inspect identity executable: {error}"));
                }
                Ok(_) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(1)),
                Ok(_) => {
                    break Err(
                        "identity fixture did not publish argv before its deadline".to_owned()
                    )
                }
                Err(error) => break Err(format!("cannot inspect identity argv: {error}")),
            }
        };
        drop(child.stdin.take());
        assert!(child.wait().unwrap().success());
        let (argv, executable) = observed.unwrap();
        assert_eq!(executable, cfg.binary.canonicalize().unwrap());
        let argv: Vec<&[u8]> = argv
            .split(|byte| *byte == 0)
            .filter(|arg| !arg.is_empty())
            .collect();
        assert_eq!(
            argv,
            [b"tmux".as_slice(), b"-S".as_slice(), b"1M".as_slice()]
        );

        let probe = |control: bool| {
            let mut command = Command::new("/usr/bin/printenv");
            command
                .env("XDG_RUNTIME_DIR", "/private/mios-desktop-runtime-proof")
                .env(
                    "DBUS_SESSION_BUS_ADDRESS",
                    "unix:path=/private/mios-desktop-bus-proof",
                )
                .env(
                    "MIOS_VENDOR_TOML",
                    "/private/mios-desktop-catalogue-proof.toml",
                )
                .env("TMUX", "/private/outer-server,123,$0")
                .env("TMUX_PANE", "%123");
            command_environment(&mut command, control);
            command.args([
                "XDG_RUNTIME_DIR",
                "DBUS_SESSION_BUS_ADDRESS",
                "MIOS_VENDOR_TOML",
                "TMUX",
                "TMUX_PANE",
            ]);
            crate::sessions::run_bounded(&mut command, Instant::now() + Duration::from_secs(2))
                .unwrap()
        };
        let server = probe(false);
        // printenv returns 1 because only the two nesting keys are absent.
        assert_eq!(server.status.code(), Some(1));
        assert_eq!(
            String::from_utf8(server.stdout).unwrap(),
            "/private/mios-desktop-runtime-proof\nunix:path=/private/mios-desktop-bus-proof\n/private/mios-desktop-catalogue-proof.toml\n"
        );
        let control = probe(true);
        assert_eq!(control.status.code(), Some(1));
        assert!(control.stdout.is_empty());
    }

    #[test]
    fn contended_startup_lock_returns_at_its_deadline() {
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("reservation.lock");
        let _first = reservation_lock(&path, Duration::from_secs(1)).unwrap();
        let started = Instant::now();
        let error = reservation_lock(&path, Duration::from_millis(5)).unwrap_err();
        assert!(error.contains("timed out"));
        assert!(started.elapsed() < Duration::from_secs(1));
    }
}
