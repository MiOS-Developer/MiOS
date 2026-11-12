// AI-hint: Event-driven TTY desktop background; applications, dock, and native window geometry remain owned by tmux.
// AI-related: /usr/share/mios/templates/rust, usr/share/mios/mios.toml [keybindings.tmux.desktop], tools/native/mios-unit-gen/src/tmux_desktop.rs
// AI-functions: open, open_with_login_fd, login, reserve_config, ensure_sidebar, desktop_layout_command, run, config, paint, settle_resize
//! The desktop's anchor pane is a quiet background, not a compositor. It does
//! not poll the process list, animate, resize sibling panes, or capture mouse
//! input. The generated tmux dock and border bindings own those interactions.

use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{FileTypeExt, MetadataExt, OpenOptionsExt};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::time::{Duration, Instant};

use mios_resolver::popup::{PopupPolicy, SurfaceKind};
use ratatui::buffer::Buffer;
use ratatui::crossterm::event::{self, Event};
use ratatui::layout::{Alignment, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::Line;
use ratatui::widgets::{Paragraph, Widget, Wrap};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use toml::Value;

const OWNER: &str = "@mios-desktop-origin";
const INPUTD: &str = "/usr/libexec/mios/mios-inputd";
const PANEL_RESIZE_HOOK: &str = "window-layout-changed[94]";

#[derive(Clone, Debug)]
struct PanelPolicy {
    argv: Vec<String>,
    popup: PopupPolicy,
    kind: SurfaceKind,
    min_canvas_rows: u16,
    topbar: String,
}

#[derive(Clone, Debug)]
struct SidebarPolicy {
    chat_argv: Vec<String>,
    metrics_command: String,
    chat_first: bool,
    column_share: f64,
    chat_share: f64,
    monitor_share: f64,
    monitor_min_rows: u16,
    desktop_columns: u16,
    desktop_rows: u16,
    portrait_percent: u16,
    chat_min_rows: u16,
    metrics_min_columns: u16,
    metrics_min_rows: u16,
    storage_title: String,
}

impl SidebarPolicy {
    fn read(doc: &Value) -> Result<Option<Self>, String> {
        let Some(section) = doc
            .get("keybindings")
            .and_then(|v| v.get("tmux"))
            .and_then(|v| v.get("desktop"))
            .and_then(|v| v.get("sidebar"))
        else {
            return Ok(None);
        };
        let enabled = section
            .get("enabled")
            .and_then(Value::as_bool)
            .ok_or("desktop.sidebar.enabled must be boolean")?;
        if !enabled {
            return Ok(None);
        }
        let base = "mcp.tmux.workspace";
        let share = |name: &str| -> Result<f64, String> {
            let key = format!("{base}.layout.{name}");
            at(doc, &key)?
                .as_float()
                .filter(|n| n.is_finite() && (0.1..=0.9).contains(n))
                .ok_or_else(|| format!("{key} must be a fraction from0.1 to0.9"))
        };
        let roles = at(doc, &format!("{base}.layout.left_roles"))?
            .as_array()
            .ok_or("workspace left_roles must be an ordered pair")?;
        let roles: Vec<_> = roles.iter().map(Value::as_str).collect();
        let chat_first = match roles.as_slice() {
            [Some("chat"), Some("metrics")] => true,
            [Some("metrics"), Some("chat")] => false,
            _ => return Err("workspace left_roles must contain chat and metrics once".into()),
        };
        let python = string(doc, "mcp.python")?;
        if !Path::new(&python).is_absolute() {
            return Err("mcp.python must be an absolute executable".into());
        }
        Ok(Some(Self {
            chat_argv: vec![
                python,
                "/usr/libexec/mios/mios-mcp-server".into(),
                "--desktop-chat".into(),
            ],
            metrics_command: string(doc, &format!("{base}.resource_command"))?,
            chat_first,
            column_share: share("column_share")?,
            chat_share: share("chat_share")?,
            monitor_share: share("monitor_share")?,
            monitor_min_rows: number(doc, &format!("{base}.layout.monitor_min_rows"), 3, 40)?,
            desktop_columns: number(doc, &format!("{base}.desktop_min_columns"), 20, 4096)?,
            desktop_rows: number(doc, &format!("{base}.desktop_min_rows"), 12, 4096)?,
            portrait_percent: number(doc, &format!("{base}.portrait_ratio_percent"), 50, 400)?,
            chat_min_rows: number(doc, &format!("{base}.minimum_head_rows"), 1, 4096)?,
            metrics_min_columns: number(doc, &format!("{base}.resource_min_columns"), 1, 4096)?,
            metrics_min_rows: number(doc, &format!("{base}.resource_min_rows"), 1, 4096)?,
            storage_title: string(doc, "keybindings.tmux.desktop.labels.storage_title")?,
        }))
    }

    fn columns(&self) -> String {
        format!(
            "#{{e|*|f|0:#{{e|-:#{{window_width}},1}},{}}}",
            self.column_share
        )
    }

    fn chat_rows(&self) -> String {
        format!(
            "#{{e|*|f|0:#{{e|-:#{{window_height}},2}},{}}}",
            self.chat_share
        )
    }

    fn wide(&self) -> String {
        format!("#{{&&:#{{e|>=:#{{window_width}},{}}},#{{&&:#{{e|>=:#{{window_height}},{}}},#{{e|>=:#{{e|*:#{{window_width}},100}},#{{e|*:#{{window_height}},{}}}}}}}}}",
            self.desktop_columns, self.desktop_rows, self.portrait_percent)
    }

    fn metrics_fit(&self) -> String {
        format!(
            "#{{&&:#{{e|>=:{},{}}},#{{e|>=:#{{e|-:#{{e|-:#{{window_height}},2}},{}}},{}}}}}",
            self.columns(),
            self.metrics_min_columns,
            self.chat_rows(),
            self.metrics_min_rows
        )
    }
}

impl PanelPolicy {
    fn read(doc: &Value) -> Result<Option<Self>, String> {
        let Some(table) = doc
            .get("keybindings")
            .and_then(|v| v.get("tmux"))
            .and_then(|v| v.get("desktop"))
            .and_then(|v| v.get("panel"))
        else {
            return Ok(None);
        };
        if table.get("enabled").and_then(Value::as_bool) == Some(false) {
            return Ok(None);
        }
        if table.get("enabled").and_then(Value::as_bool) != Some(true) {
            return Err("desktop.panel.enabled must be boolean".into());
        }
        let argv = table
            .get("argv")
            .and_then(Value::as_array)
            .ok_or("desktop.panel.argv must be an argument array")?
            .iter()
            .map(|arg| {
                arg.as_str()
                    .filter(|s| !s.is_empty() && !s.chars().any(char::is_control))
                    .map(str::to_owned)
                    .ok_or("invalid desktop panel argument".to_owned())
            })
            .collect::<Result<Vec<_>, _>>()?;
        if argv.is_empty() || !Path::new(&argv[0]).is_absolute() {
            return Err("desktop.panel.argv must start with an absolute executable".into());
        }
        let kind = match table.get("size_kind").and_then(Value::as_str) {
            Some("window") => SurfaceKind::Window,
            Some("menu") => SurfaceKind::Menu,
            Some("dialog") => SurfaceKind::Dialog,
            Some("tooltip") => SurfaceKind::Tooltip,
            _ => return Err("desktop.panel.size_kind must name an existing popup surface".into()),
        };
        let source = table
            .get("min_canvas_rows_source")
            .and_then(Value::as_str)
            .ok_or("desktop.panel.min_canvas_rows_source must name an SSOT key")?;
        let min_canvas_rows = number(doc, source, 1, 4096)?;
        let mut popup = PopupPolicy::from_value(doc)?;
        // A persistent panel uses the shared launch-size scale even when an
        // operator disables transient popup placement elsewhere.
        popup.enabled = true;
        let label = string(doc, "keybindings.tmux.desktop.labels.topbar")?;
        if !mios_unit_gen::tmux_os::plain_text(&label) {
            return Err("desktop topbar label must be printable text".into());
        }
        let style_color = |key: &str| -> Result<String, String> {
            let value = string(doc, key)?;
            if !value
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b == b'#')
            {
                return Err(format!("invalid desktop topbar colour {key}"));
            }
            Ok(value)
        };
        let color = style_color("colors.cursor")?;
        let bg = style_color("colors.bg")?;
        let vt_color = style_color("theme.tmux.console.cursor")?;
        let vt_bg = style_color("theme.tmux.console.bg")?;
        let text = format!(" {label} ");
        let topbar = format!(
            "#{{?#{{E:@mios-console-client}},#[bg={vt_color},fg={vt_bg},bold],#[bg={color},fg={bg},bold]}}#{{p|#{{e|-:#{{pane_width}},2}}|;l:{text}}}"
        );
        Ok(Some(Self {
            argv,
            popup,
            kind,
            min_canvas_rows,
            topbar,
        }))
    }

    fn body_rows(&self, width: u16, height: u16) -> u16 {
        let cap = self.popup.size(self.kind, width, u16::MAX).1.max(1);
        // Two tiled top borders, then room for the minimum floating app and
        // its native top/bottom borders in the lower canvas.
        cap.min(
            height
                .saturating_sub(self.min_canvas_rows.saturating_add(4))
                .max(1),
        )
    }

    fn height_format(&self) -> String {
        let cap_width = self.popup.size(self.kind, u16::MAX, u16::MAX).0;
        let mut groups = Vec::new();
        for width in 1..=cap_width.saturating_add(2).max(1) {
            let height = self.popup.size(self.kind, width, u16::MAX).1.max(1);
            if groups.last().is_none_or(|(_, last)| *last != height) {
                groups.push((width, height));
            }
        }
        let mut preferred = groups
            .last()
            .map(|(_, height)| height.to_string())
            .unwrap_or("1".into());
        for index in (0..groups.len().saturating_sub(1)).rev() {
            preferred = format!(
                "#{{?#{{e|<:#{{pane_width}},{}}},{},{preferred}}}",
                groups[index + 1].0,
                groups[index].1
            );
        }
        let available = format!(
            "#{{e|-:#{{window_height}},{}}}",
            self.min_canvas_rows.saturating_add(4)
        );
        let available = format!("#{{?#{{e|>=:{available},1}},{available},1}}");
        format!("#{{?#{{e|<:{preferred},{available}}},{preferred},{available}}}")
    }
}

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
    console_bridge: bool,
    panel: Option<PanelPolicy>,
    login: Option<LoginIdentity>,
    sidebar: Option<SidebarPolicy>,
}

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq, Eq)]
struct LoginIdentity {
    version: u32,
    namespace_only: bool,
    uid: u32,
    session_id: String,
    scope: String,
    leader: u32,
    leader_start: u64,
    process_pid: u32,
    process_start: u64,
    tty_dev: u64,
    tty_ino: u64,
    tty_rdev: u64,
    service: Option<String>,
    remote: bool,
}

impl LoginIdentity {
    fn key(&self) -> (u32, &str, u32, u64, u64, u64, u64) {
        (
            self.uid,
            &self.session_id,
            self.leader,
            self.leader_start,
            self.tty_dev,
            self.tty_ino,
            self.tty_rdev,
        )
    }

    fn same_login(&self, other: &Self) -> bool {
        self.key() == other.key()
            && self.scope == other.scope
            && self.service == other.service
            && self.remote == other.remote
    }

    fn digest(&self) -> Result<String, String> {
        let bytes = serde_json::to_vec(&self.key()).map_err(|error| error.to_string())?;
        Ok(format!("{:x}", Sha256::digest(bytes)))
    }
}

/// Preserve the original authenticated login terminal through inputd's PTY
/// bridge. It is metadata evidence only, never an input reader or device claim.
struct LoginTty(OwnedFd);

impl LoginTty {
    fn capture(inherited: Option<i32>) -> Result<Self, String> {
        if inherited.is_some_and(|fd| fd < 3) {
            return Err("an inherited login terminal descriptor must be >=3".into());
        }
        let source = inherited.unwrap_or(0);
        // SAFETY: these calls inspect/duplicate an existing descriptor. The
        // new descriptor is owned exactly once and closes on ordinary exec.
        if unsafe { libc::isatty(source) } != 1 {
            return Err("desktop login requires its authenticated terminal".into());
        }
        let fd = unsafe { libc::fcntl(source, libc::F_DUPFD_CLOEXEC, 3) };
        if fd < 0 {
            return Err(std::io::Error::last_os_error().to_string());
        }
        if inherited.is_some() {
            let flags = unsafe { libc::fcntl(source, libc::F_GETFD) };
            if flags < 0
                || unsafe { libc::fcntl(source, libc::F_SETFD, flags | libc::FD_CLOEXEC) } < 0
            {
                unsafe { libc::close(fd) };
                return Err(std::io::Error::last_os_error().to_string());
            }
        }
        Ok(Self(unsafe { OwnedFd::from_raw_fd(fd) }))
    }

    fn inherit_for(&self, command: &mut Command) {
        let fd = self.0.as_raw_fd();
        // SAFETY: the closure performs only fcntl on our still-owned fd in
        // the child before exec; no allocation, locks or input reads occur.
        unsafe {
            command.pre_exec(move || {
                let flags = libc::fcntl(fd, libc::F_GETFD);
                if flags < 0 || libc::fcntl(fd, libc::F_SETFD, flags & !libc::FD_CLOEXEC) < 0 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
    }

    fn identity(&self, cfg: &StartConfig) -> Result<LoginIdentity, String> {
        let mut command = Command::new(INPUTD);
        command.arg("--root").arg(&cfg.root).args([
            "login-identity",
            "--tty-fd",
            &self.0.as_raw_fd().to_string(),
        ]);
        self.inherit_for(&mut command);
        let output = cfg.bounded(&mut command, "authenticated login identity")?;
        if !output.status.success() {
            return Err(format!(
                "desktop login identity: {}",
                String::from_utf8_lossy(&output.stderr).trim()
            ));
        }
        let identity: LoginIdentity =
            serde_json::from_slice(&output.stdout).map_err(|error| error.to_string())?;
        // SAFETY: fstat/getuid inspect metadata only. inputd proves the root
        // logind record and live process/leader scope, not a supplied env label.
        let mut tty: libc::stat = unsafe { std::mem::zeroed() };
        if unsafe { libc::fstat(self.0.as_raw_fd(), &mut tty) } != 0
            || identity.version != 1
            || !identity.namespace_only
            || identity.uid != unsafe { libc::getuid() }
            || !basename(&identity.session_id)
            || identity.scope != format!("session-{}.scope", identity.session_id)
            || identity.leader == 0
            || identity.leader_start == 0
            || identity.process_pid == 0
            || identity.process_start == 0
            || (identity.tty_dev, identity.tty_ino, identity.tty_rdev)
                != (tty.st_dev, tty.st_ino, tty.st_rdev)
            || process_start(identity.leader)? != identity.leader_start
        {
            return Err("inputd login identity does not match the inherited login terminal".into());
        }
        Ok(identity)
    }
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

fn shell_policy(root: &Path, doc: &Value) -> Result<(String, String), String> {
    let selected = mios_resolver::terminal_tuis::catalog(doc)?
        .into_iter()
        .find(|app| app.id == "terminal-shell");
    let shell = match selected {
        Some(shell) => shell,
        None => {
            // An older host/user array can intentionally replace the vendor
            // catalogue. Resolve only this required startup row from vendor
            // policy; do not merge applications into the operator's list.
            let vendor = mios_resolver::resolve_vendor_projection(root)
                .map_err(|error| format!("desktop startup shell vendor policy: {error}"))?;
            mios_resolver::terminal_tuis::catalog(&vendor)?
                .into_iter()
                .find(|app| app.id == "terminal-shell")
                .ok_or("the vendor SSOT terminal-shell startup entry is missing")?
        }
    };
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
        let (default_shell, default_command) = shell_policy(root, doc)?;
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
            console_bridge: at(doc, "keybindings.global.controller.console.bridge")?
                .as_bool()
                .ok_or("console bridge policy must be boolean")?,
            panel: PanelPolicy::read(doc)?,
            login: None,
            sidebar: SidebarPolicy::read(doc)?,
        })
    }

    fn partition_login(&mut self, identity: LoginIdentity) -> Result<(), String> {
        let digest = identity.digest()?;
        let root = self
            .socket
            .parent()
            .and_then(Path::parent)
            .ok_or("desktop socket root is missing")?;
        let namespace = root.join(format!("mios-view-{}-{}", identity.uid, &digest[..32]));
        let socket = namespace
            .join(
                self.socket
                    .parent()
                    .and_then(Path::file_name)
                    .ok_or("socket parent is missing")?,
            )
            .join(
                self.socket
                    .file_name()
                    .ok_or("socket basename is missing")?,
            );
        // SAFETY: sockaddr_un is plain zeroed storage; its field size is the
        // target ABI's actual pathname capacity rather than a policy tunable.
        let address: libc::sockaddr_un = unsafe { std::mem::zeroed() };
        if socket.as_os_str().as_bytes().len() >= address.sun_path.len() {
            return Err("authenticated desktop socket exceeds the Unix address capacity".into());
        }
        private_dir(&namespace)?;
        self.runtime = namespace
            .join(
                self.runtime
                    .parent()
                    .and_then(Path::file_name)
                    .ok_or("runtime parent is missing")?,
            )
            .join(
                self.runtime
                    .file_name()
                    .ok_or("runtime basename is missing")?,
            );
        self.socket = socket;
        self.login = Some(identity);
        Ok(())
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

    fn console_bridge_command(
        &self,
        no_attach: bool,
        login_tty: Option<&LoginTty>,
    ) -> Result<Option<Command>, String> {
        let nested = std::env::var_os("MIOS_CONSOLE_BRIDGE").is_some_and(|value| !value.is_empty());
        // SAFETY: isatty only examines the inherited standard descriptors.
        let interactive = unsafe { libc::isatty(0) == 1 && libc::isatty(1) == 1 };
        if no_attach
            || nested
            || !interactive
            || !self.console_bridge
            || !Path::new(INPUTD).is_file()
        {
            return Ok(None);
        }
        // This authority query must inherit stdin: the generic bounded helper
        // intentionally replaces stdin with /dev/null. console-vt only reads
        // terminal identity; it does not read keys, mouse modes or devices.
        let mut child = match Command::new(INPUTD)
            .arg("--root")
            .arg(&self.root)
            .arg("console-vt")
            .stdin(Stdio::inherit())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
        {
            Ok(child) => child,
            Err(_) => return Ok(None),
        };
        let deadline = Instant::now() + self.command_timeout;
        loop {
            match child.try_wait() {
                Ok(Some(_)) => break,
                Ok(None) if Instant::now() < deadline => {
                    std::thread::sleep(Duration::from_millis(20))
                }
                _ => {
                    let _ = child.kill();
                    let _ = child.wait();
                    return Ok(None);
                }
            }
        }
        let output = child
            .wait_with_output()
            .map_err(|error| error.to_string())?;
        if !output.status.success() {
            return Ok(None); // SSH/terminal PTYs are not physical Linux VTs.
        }
        let vt = String::from_utf8(output.stdout).map_err(|error| error.to_string())?;
        let Some(vt) = vt
            .trim()
            .parse::<u32>()
            .ok()
            .filter(|vt| (1..=63).contains(vt))
        else {
            return Err("inputd returned an invalid physical VT identity".into());
        };
        let mut probe = Command::new(INPUTD);
        probe
            .arg("--root")
            .arg(&self.root)
            .args(["console-bridge", "--help"]);
        if !self
            .bounded(&mut probe, "console bridge capability")
            .is_ok_and(|output| output.status.success())
        {
            return Ok(None);
        }
        let mut command = Command::new(INPUTD);
        command
            .arg("--root")
            .arg(&self.root)
            .args(["console-bridge", "--vt", &vt.to_string(), "--"])
            .arg(std::env::current_exe().map_err(|error| error.to_string())?)
            .args(["desktop", "open", "--root"])
            .arg(&self.root);
        if let Some(tty) = login_tty {
            command.args(["--login-tty-fd", &tty.0.as_raw_fd().to_string()]);
            tty.inherit_for(&mut command);
        }
        // The existing bridge sets MIOS_CONSOLE_BRIDGE=1 for this canonical
        // child. It then retains the trusted tmux argv[0] and all opener guards.
        Ok(Some(command))
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
struct PanelReceipt {
    pane: String,
    pid: u32,
    start: u64,
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
    #[serde(default)]
    panel: Option<PanelReceipt>,
    #[serde(default)]
    login: Option<LoginIdentity>,
    #[serde(default)]
    chat: Option<PanelReceipt>,
    #[serde(default)]
    metrics: Option<PanelReceipt>,
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

#[derive(Deserialize)]
struct HandoffAck {
    version: u32,
    uid: u32,
    generation: u64,
    session: String,
    ok: bool,
}

struct LoginHandoff {
    ack: PathBuf,
    error: PathBuf,
}

impl LoginHandoff {
    fn new(cfg: &StartConfig) -> Result<Self, String> {
        let mut nonce = [0u8; 16];
        File::open("/dev/urandom")
            .and_then(|mut file| file.read_exact(&mut nonce))
            .map_err(|error| error.to_string())?;
        let nonce = nonce
            .iter()
            .map(|value| format!("{value:02x}"))
            .collect::<String>();
        let ack = cfg
            .runtime
            .join(format!("handoff-{}-{nonce}.json", std::process::id()));
        let error = ack.with_extension("error");
        for path in [&ack, &error] {
            OpenOptions::new()
                .write(true)
                .create_new(true)
                .mode(0o600)
                .custom_flags(libc::O_NOFOLLOW)
                .open(path)
                .map_err(|error| error.to_string())?;
        }
        Ok(Self { ack, error })
    }

    fn attach_command(&self, cfg: &StartConfig) -> Result<String, String> {
        let word = |path: &Path| -> Result<String, String> {
            let text = path.to_str().ok_or("handoff path must be UTF-8")?;
            Ok(format!("'{}'", text.replace('\'', "'\\''")))
        };
        // The broker verifies the fresh attached outer client through native
        // peer/logind witnesses. These files are acknowledgements, never an
        // assignment store or an environment-based controller claim.
        Ok(format!(
            "TMUX_PANE=#{{pane_id}} {INPUTD} --root {} handoff attach --socket #{{q:socket_path}} --client #{{q:client_name}} --pane #{{pane_id}} > {} 2> {} ; :",
            word(&cfg.root)?, word(&self.ack)?, word(&self.error)?
        ))
    }

    fn detached(&self, cfg: &StartConfig, session: &str) {
        let deadline = Instant::now() + cfg.command_timeout;
        loop {
            let result = OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_NOFOLLOW)
                .open(&self.ack)
                .ok()
                .and_then(|file| serde_json::from_reader::<_, HandoffAck>(file).ok());
            if let Some(ack) = result {
                // SAFETY: getuid reads the caller's identity only.
                if ack.ok
                    && ack.version == 1
                    && ack.uid == unsafe { libc::getuid() }
                    && ack.session == session
                    && ack.generation > 0
                {
                    let mut command = Command::new(INPUTD);
                    command.arg("--root").arg(&cfg.root).args([
                        "handoff",
                        "detach",
                        "--generation",
                        &ack.generation.to_string(),
                    ]);
                    let _ = cfg.bounded(&mut command, "input handoff detach");
                }
                return;
            }
            if fs::metadata(&self.error).is_ok_and(|metadata| metadata.len() > 0)
                || Instant::now() >= deadline
            {
                return; // Broker rejection cannot arm inputs; client-exit also invalidates its lease.
            }
            std::thread::sleep(Duration::from_millis(20));
        }
    }
}

impl Drop for LoginHandoff {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.ack);
        let _ = fs::remove_file(&self.error);
    }
}

/// Only a real host attach with a kernel/logind-proved login may recover its
/// own enabled service. Private roots and maintenance reservations never start
/// a physical input reader; an inherited console-bridge tty remains eligible.
fn input_service_admitted(
    root: &Path,
    login: Option<&LoginIdentity>,
    no_attach: bool,
    outer: bool,
) -> bool {
    // SAFETY: getuid reads the current account only.
    let uid = unsafe { libc::getuid() };
    root == Path::new("/")
        && !no_attach
        && outer
        && login.is_some_and(|login| {
            login.namespace_only && login.version == 1 && login.uid == uid && uid != 0
        })
}

fn start_enabled_input_service(
    runtime: &Path,
    mut run: impl FnMut(&mut Command, &str) -> Result<Output, String>,
) -> Result<(), String> {
    let command = || {
        let mut command = Command::new("/usr/bin/systemctl");
        command
            .env_clear()
            .env("LANG", "C")
            .env("PATH", "/usr/bin:/usr/sbin:/bin")
            .env("XDG_RUNTIME_DIR", runtime)
            .env(
                "DBUS_SESSION_BUS_ADDRESS",
                format!("unix:path={}/bus", runtime.display()),
            )
            .args(["--user", "--no-pager", "--no-ask-password"]);
        command
    };
    let mut query = command();
    query.args([
        "show",
        "mios-inputd.service",
        "--property=LoadState",
        "--property=UnitFileState",
        "--property=ActiveState",
    ]);
    let result = run(&mut query, "input service status")?;
    if !result.status.success() {
        return Err("the selected account's input service status is unavailable".into());
    }
    let text = std::str::from_utf8(&result.stdout)
        .map_err(|_| "input service status is not valid UTF-8")?;
    let mut fields = std::collections::BTreeMap::new();
    for line in text.lines().filter(|line| !line.is_empty()) {
        let (key, value) = line.split_once('=').ok_or("invalid input service status")?;
        if fields.insert(key, value).is_some() {
            return Err("duplicate input service status".into());
        }
    }
    if fields.len() != 3
        || ["LoadState", "UnitFileState", "ActiveState"]
            .iter()
            .any(|key| !fields.contains_key(key))
    {
        return Err("incomplete input service status".into());
    }
    if fields.get("LoadState").copied() == Some("loaded")
        && matches!(
            fields.get("UnitFileState").copied(),
            Some("enabled" | "enabled-runtime")
        )
        && matches!(
            fields.get("ActiveState").copied(),
            Some("inactive" | "failed" | "activating")
        )
    {
        let result = run(
            command().args(["start", "mios-inputd.service"]),
            "input service start",
        )?;
        if !result.status.success() {
            return Err("the selected account's enabled input service could not start".into());
        }
    }
    Ok(())
}

fn ensure_login_input_service(
    cfg: &StartConfig,
    no_attach: bool,
    outer: bool,
) -> Result<(), String> {
    if !input_service_admitted(&cfg.root, cfg.login.as_ref(), no_attach, outer) {
        return Ok(());
    }
    // PAM owns manager creation. Never create a manager or use an inherited
    // D-Bus address: contact only this account's existing canonical runtime.
    let uid = cfg.login.as_ref().ok_or("missing authenticated login")?.uid;
    let runtime = PathBuf::from(format!("/run/user/{uid}"));
    let directory = match fs::symlink_metadata(&runtime) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(format!("input service runtime: {error}")),
    };
    if !directory.is_dir() || directory.uid() != uid || directory.mode() & 0o7777 != 0o700 {
        return Err("input service runtime must be an account-owned mode0700 directory".into());
    }
    let bus = match fs::symlink_metadata(runtime.join("bus")) {
        Ok(metadata) => metadata,
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(format!("input service bus: {error}")),
    };
    if !bus.file_type().is_socket() || bus.uid() != uid {
        return Err("input service requires the selected account's existing user bus".into());
    }
    start_enabled_input_service(&runtime, |command, purpose| cfg.bounded(command, purpose))
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
    if !match (&cfg.login, &receipt.login) {
        (None, None) => true,
        (Some(current), Some(prior)) => current.same_login(prior),
        _ => false,
    } {
        return Err("desktop reservation belongs to a different authenticated login".into());
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
        "#{pid}|#{session_id}|#{window_id}|#{pane_id}|#{pane_pid}|#{@mios-desktop-canvas}",
    ])?;
    let fields: Vec<_> = current.split('|').collect();
    if fields.len() != 6
        || fields[0] != receipt.server_pid.to_string()
        || fields[1] != receipt.session
        || fields[3] != receipt.canvas
        || fields[4] != receipt.canvas_pid.to_string()
        || fields[5] != "1"
        || !owned_role_window(cfg, receipt, &receipt.canvas, fields[2])?
    {
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

fn owned_role_window(
    cfg: &StartConfig,
    receipt: &Receipt,
    pane: &str,
    window: &str,
) -> Result<bool, String> {
    if window == receipt.window {
        return Ok(true);
    }
    Ok(cfg.tmux(&[
        "show-options",
        "-wqv",
        "-t",
        pane,
        "@mios-desktop-storage-origin",
    ])? == receipt.origin)
}

fn verify_sidebar_role(
    cfg: &StartConfig,
    receipt: &Receipt,
    role: &PanelReceipt,
    name: &str,
) -> Result<(), String> {
    if !id(&role.pane, '%') || process_start(role.pid)? != role.start {
        return Err(format!("the owned desktop {name} process identity changed"));
    }
    let current = cfg.tmux(&["display-message", "-p", "-t", &role.pane,
        "#{session_id}|#{window_id}|#{pane_id}|#{pane_pid}|#{pane_floating_flag}|#{@mios-desktop-role-origin}|#{@mios-desktop-role}"])?;
    let fields: Vec<_> = current.split('|').collect();
    if fields.len() != 7
        || fields[0] != receipt.session
        || fields[2] != role.pane
        || fields[3] != role.pid.to_string()
        || fields[4] != "0"
        || fields[5] != receipt.origin
        || fields[6] != name
        || !owned_role_window(cfg, receipt, &role.pane, fields[1])?
    {
        return Err(format!(
            "the owned desktop {name} pane was moved or replaced"
        ));
    }
    Ok(())
}

fn create_sidebar_role(
    cfg: &StartConfig,
    target: &str,
    argv: &[String],
) -> Result<PanelReceipt, String> {
    let mut args = vec![
        "split-window",
        "-d",
        "-v",
        "-P",
        "-F",
        "#{pane_id}|#{pane_pid}",
        "-t",
        target,
        "--",
    ];
    args.extend(argv.iter().map(String::as_str));
    let created = cfg.tmux(&args)?;
    let (pane, pid) = created
        .split_once('|')
        .ok_or("desktop role creation returned no identity")?;
    if !id(pane, '%') {
        return Err("invalid desktop role pane identity".into());
    }
    let pid = pid.parse::<u32>().map_err(|_| "invalid desktop role PID")?;
    Ok(PanelReceipt {
        pane: pane.into(),
        pid,
        start: process_start(pid)?,
    })
}

fn native_command(pane: &str, command: &str) -> String {
    format!(
        "run-shell -C -t {pane} {}",
        mios_unit_gen::tmux_os::quote(command)
    )
}

fn desktop_layout_command(policy: &SidebarPolicy, receipt: &Receipt) -> Result<String, String> {
    let quote = mios_unit_gen::tmux_os::quote;
    let panel = &receipt
        .panel
        .as_ref()
        .ok_or("sidebar needs its protected monitor panel")?
        .pane;
    let chat = &receipt.chat.as_ref().ok_or("sidebar chat is missing")?.pane;
    let metrics = &receipt
        .metrics
        .as_ref()
        .ok_or("sidebar metrics are missing")?
        .pane;
    let canvas = &receipt.canvas;
    let window = &receipt.window;
    let conditional = |target: &str, test: &str, yes: &str, no: Option<&str>| -> String {
        let mut result = format!("if-shell -F -t {target} {} {}", quote(test), quote(yes));
        if let Some(no) = no {
            result.push(' ');
            result.push_str(&quote(no));
        }
        result
    };
    let park = |source: &str| {
        conditional(source, &format!("#{{==:#{{window_id}},{window}}}"), &format!(
        "break-pane -d -s {source} -n {} ; set-option -w -t {source} @mios-desktop-storage-origin {} ; set-option -w -t {source} @mios-hidden 1",
        quote(&policy.storage_title), quote(&receipt.origin)), None)
    };
    let park_metrics = park(metrics);
    // C0 join-pane splits the destination cell before removing its source.
    // Breaking an owned role out first avoids invalidating that cell when
    // source and destination originally share a layout. No worker is moved.
    let move_role = |target: &str, source: &str, command: &str| {
        format!("{} ; {}", park(source), native_command(target, command))
    };
    let right_columns = format!("#{{e|-:#{{e|-:#{{window_width}},1}},{}}}", policy.columns());
    let metrics_rows = format!(
        "#{{e|-:#{{e|-:#{{window_height}},2}},{}}}",
        policy.chat_rows()
    );
    let join_metrics = move_role(
        chat,
        metrics,
        &format!(
            "join-pane -d -v {} -s {metrics} -t {chat} -l {metrics_rows}",
            if policy.chat_first { "" } else { "-b" }
        ),
    );
    let wide_mode = format!("#{{?{},landscape-metrics,landscape}}", policy.metrics_fit());
    let wide_topology = format!(
        "{} ; {} ; {}",
        move_role(
            chat,
            canvas,
            &format!("join-pane -d -h -s {canvas} -t {chat} -l {right_columns}")
        ),
        move_role(
            canvas,
            panel,
            &format!(
                "join-pane -d -v -b -s {panel} -t {canvas} -l #{{E:@mios-desktop-panel-height}}"
            )
        ),
        conditional(
            chat,
            &policy.metrics_fit(),
            &join_metrics,
            Some(&park_metrics)
        )
    );
    let compact_topology = format!(
        "{park_metrics} ; {} ; {}",
        conditional(
            canvas,
            &format!("#{{==:#{{window_id}},{window}}}"),
            &format!("join-pane -d -v -s {canvas} -t {metrics}"),
            None
        ),
        move_role(
            chat,
            panel,
            &format!(
                "join-pane -d -v -b -s {panel} -t {chat} -l #{{E:@mios-desktop-panel-height}}"
            )
        )
    );
    let wide = format!("set-option -w -t {window} @mios-desktop-layout-compact 0 ; {} ; set-option -wF -t {window} @mios-desktop-layout-mode {} ; set-option -w -t {window} @mios-desktop-workarea-pane {canvas} ; set-option -w -t {window} @mios-desktop-workarea-available 1 ; {} ; {} ; {}",
        conditional(panel, &format!("#{{!=:#{{@mios-desktop-layout-mode}},{wide_mode}}}"), &wide_topology, None), quote(&wide_mode),
        native_command(chat, &format!("resize-pane -t {chat} -x {}", policy.columns())),
        conditional(chat, &policy.metrics_fit(), &native_command(chat, &format!("resize-pane -t {chat} -y {}", policy.chat_rows())), None),
        native_command(panel, &format!("resize-pane -t {panel} -y #{{E:@mios-desktop-panel-height}}")));
    let compact = format!("set-option -w -t {window} @mios-desktop-layout-compact 1 ; {} ; set-option -w -t {window} @mios-desktop-layout-mode compact ; set-option -wu -t {window} @mios-desktop-workarea-pane ; set-option -w -t {window} @mios-desktop-workarea-available 0 ; {}",
        conditional(panel, "#{!=:#{@mios-desktop-layout-mode},compact}", &compact_topology, None),
        native_command(panel, &format!("resize-pane -t {panel} -y #{{E:@mios-desktop-panel-height}}")));
    // Memoize the complete view, before native join/resize notifications. A
    // layout change from a managed app does not replay or move the UI roles.
    let body = format!(
        "set-option -wF -t {window} @mios-desktop-layout-viewport {} ; {}",
        quote("#{window_width}x#{window_height}"),
        conditional(panel, &policy.wide(), &wide, Some(&compact))
    );
    Ok(conditional(panel, "#{&&:#{@mios-desktop-enabled},#{!=:#{@mios-desktop-layout-viewport},#{window_width}x#{window_height}}}", &body, None))
}

fn ensure_sidebar(cfg: &StartConfig, receipt: &mut Receipt, fresh: bool) -> Result<(), String> {
    let Some(policy) = &cfg.sidebar else {
        if receipt.chat.is_some() || receipt.metrics.is_some() {
            return Err("desktop sidebar was disabled while its owned applications remain running; nothing was stopped".into());
        }
        return Ok(());
    };
    if cfg.panel.is_none() {
        return Err("desktop sidebar requires the unified monitor panel".into());
    }
    if let Some(chat) = &receipt.chat {
        verify_sidebar_role(cfg, receipt, chat, "chat")?;
    } else {
        receipt.chat = Some(create_sidebar_role(
            cfg,
            &receipt.canvas,
            &policy.chat_argv,
        )?);
        publish(&cfg.runtime.join("reservation.json"), receipt)?;
    }
    let chat = receipt
        .chat
        .as_ref()
        .ok_or("desktop chat reservation is missing")?
        .clone();
    if let Some(metrics) = &receipt.metrics {
        verify_sidebar_role(cfg, receipt, metrics, "metrics")?;
    } else {
        receipt.metrics = Some(create_sidebar_role(
            cfg,
            &chat.pane,
            std::slice::from_ref(&policy.metrics_command),
        )?);
        publish(&cfg.runtime.join("reservation.json"), receipt)?;
    }
    let metrics = receipt
        .metrics
        .as_ref()
        .ok_or("desktop metrics reservation is missing")?;
    for (name, role) in [("chat", &chat), ("metrics", metrics)] {
        cfg.tmux(&[
            "set-option",
            "-p",
            "-t",
            &role.pane,
            "@mios-desktop-role-origin",
            &receipt.origin,
        ])?;
        cfg.tmux(&[
            "set-option",
            "-p",
            "-t",
            &role.pane,
            "@mios-desktop-role",
            name,
        ])?;
        cfg.tmux(&[
            "set-option",
            "-p",
            "-t",
            &role.pane,
            &format!("@mios-desktop-{name}"),
            "1",
        ])?;
        let topbar = &cfg
            .panel
            .as_ref()
            .ok_or("desktop sidebar requires its panel")?
            .topbar;
        let style = topbar
            .split("#{p|")
            .next()
            .ok_or("desktop topbar style is missing")?;
        let border = format!("{style}#{{p|#{{e|-:#{{pane_width}},2}}|:#{{pane_current_command}}}}");
        cfg.tmux(&[
            "set-option",
            "-p",
            "-t",
            &role.pane,
            "pane-border-format",
            &border,
        ])?;
    }
    cfg.tmux(&[
        "set-option",
        "-w",
        "-t",
        &receipt.window,
        "@mios-desktop-chat-pane",
        &chat.pane,
    ])?;
    cfg.tmux(&[
        "set-option",
        "-w",
        "-t",
        &receipt.window,
        "@mios-desktop-metrics-pane",
        &metrics.pane,
    ])?;
    let cap = cfg
        .panel
        .as_ref()
        .ok_or("desktop sidebar requires the unified monitor panel")?
        .height_format();
    let monitor = format!("#{{e|*|f|0:#{{window_height}},{}}}", policy.monitor_share);
    let monitor = format!(
        "#{{?#{{e|<:{monitor},{}}},{},{monitor}}}",
        policy.monitor_min_rows, policy.monitor_min_rows
    );
    let landscape = format!("#{{?#{{e|<:{cap},{monitor}}},{cap},{monitor}}}");
    let compact_space = format!(
        "#{{e|-:#{{window_height}},{}}}",
        policy.chat_min_rows.saturating_add(2)
    );
    let compact_space = format!("#{{?#{{e|<:{compact_space},1}},1,{compact_space}}}");
    let compact = format!("#{{?#{{e|<:{cap},{compact_space}}},{cap},{compact_space}}}");
    let height = format!("#{{?#{{@mios-desktop-layout-compact}},{compact},{landscape}}}");
    cfg.tmux(&[
        "set-option",
        "-w",
        "-t",
        &receipt.window,
        "@mios-desktop-panel-height",
        &height,
    ])?;
    cfg.tmux(&[
        "set-option",
        "-w",
        "-t",
        &receipt.window,
        "@mios-desktop-layout-mode",
        "",
    ])?;
    cfg.tmux(&[
        "set-option",
        "-w",
        "-t",
        &receipt.window,
        "@mios-desktop-layout-viewport",
        "",
    ])?;
    let layout = desktop_layout_command(policy, receipt)?;
    cfg.tmux(&[
        "set-hook",
        "-t",
        &receipt.session,
        PANEL_RESIZE_HOOK,
        &layout,
    ])?;
    cfg.tmux(&["if-shell", "-F", "-t", &chat.pane, "1", &layout])?;
    if fresh {
        cfg.tmux(&["select-pane", "-t", &chat.pane])?;
    }
    verify_sidebar_role(cfg, receipt, &chat, "chat")?;
    verify_sidebar_role(cfg, receipt, metrics, "metrics")
}

fn verify_panel(cfg: &StartConfig, receipt: &Receipt, panel: &PanelReceipt) -> Result<(), String> {
    if !id(&panel.pane, '%') || process_start(panel.pid)? != panel.start {
        return Err("the owned desktop panel process identity changed".into());
    }
    let current = cfg.tmux(&["display-message", "-p", "-t", &panel.pane,
        "#{session_id}|#{window_id}|#{pane_id}|#{pane_pid}|#{pane_floating_flag}|#{@mios-desktop-panel-origin}"])?;
    let expected = format!(
        "{}|{}|{}|{}|0|{}",
        receipt.session, receipt.window, panel.pane, panel.pid, receipt.origin
    );
    if current != expected {
        return Err(
            "the owned desktop panel was moved or replaced; other work was left intact".into(),
        );
    }
    Ok(())
}

fn panel_resize_command(panel: &str) -> String {
    let quote = mios_unit_gen::tmux_os::quote;
    let memo = "#{!=:#{@mios-desktop-panel-viewport},#{pane_width}x#{window_height}}";
    let height = "#{E:@mios-desktop-panel-height}";
    let guard = format!("#{{?#{{@mios-desktop-panel}},#{{?#{{pane_floating_flag}},0,{memo}}},0}}");
    // -y takes a number, not a format. -C expands and dispatches a native
    // tmux command without starting a shell or child process.
    let resize = format!(
        "run-shell -C -t {panel} {}",
        quote(&format!("resize-pane -t {panel} -y {height}"))
    );
    let body = format!(
        "set-option -wF -t {panel} @mios-desktop-panel-viewport {} ; if-shell -F -t {panel} {} {}",
        quote("#{pane_width}x#{window_height}"),
        quote(&format!("#{{!=:#{{pane_height}},{height}}}")),
        quote(&resize)
    );
    // Record the viewport before resizing: the resulting native layout event
    // sees the memo and exits, including when tmux clamps a tiny viewport.
    format!("if-shell -F -t {panel} {} {}", quote(&guard), quote(&body))
}

fn ensure_panel(cfg: &StartConfig, receipt: &mut Receipt, fresh: bool) -> Result<(), String> {
    let Some(policy) = &cfg.panel else {
        if let Some(panel) = &receipt.panel {
            verify_panel(cfg, receipt, panel)?;
            cfg.tmux(&["set-hook", "-u", "-t", &receipt.session, PANEL_RESIZE_HOOK])?;
            cfg.tmux(&["kill-pane", "-t", &panel.pane])?;
            receipt.panel = None;
        }
        for name in ["@mios-desktop-panel-pane", "@mios-desktop-workarea-pane"] {
            cfg.tmux(&["set-option", "-wu", "-t", &receipt.window, name])?;
        }
        return Ok(());
    };
    if let Some(panel) = &receipt.panel {
        verify_panel(cfg, receipt, panel)?;
    } else {
        let size = cfg.tmux(&[
            "display-message",
            "-p",
            "-t",
            &receipt.canvas,
            "#{window_width}|#{window_height}",
        ])?;
        let (width, height) = size
            .split_once('|')
            .ok_or("desktop panel viewport is unavailable")?;
        let width = width
            .parse::<u16>()
            .map_err(|_| "invalid desktop panel width")?;
        let height = height
            .parse::<u16>()
            .map_err(|_| "invalid desktop panel height")?;
        let rows = policy
            .body_rows(width, height)
            .saturating_add(1)
            .to_string();
        let mut args = vec![
            "split-window",
            "-v",
            "-b",
            "-l",
            &rows,
            "-P",
            "-F",
            "#{pane_id}|#{pane_pid}",
            "-t",
            &receipt.canvas,
        ];
        if !fresh {
            args.push("-d");
        }
        args.push("--");
        args.extend(policy.argv.iter().map(String::as_str));
        let created = cfg.tmux(&args)?;
        let (pane, pid) = created
            .split_once('|')
            .ok_or("desktop panel creation returned no identity")?;
        if !id(pane, '%') {
            return Err("invalid desktop panel pane identity".into());
        }
        let pid = pid
            .parse::<u32>()
            .map_err(|_| "invalid desktop panel PID")?;
        receipt.panel = Some(PanelReceipt {
            pane: pane.into(),
            pid,
            start: process_start(pid)?,
        });
        // Preserve the reservation before later styling/hook RPCs: retries
        // must never start a second collector because a later setup failed.
        publish(&cfg.runtime.join("reservation.json"), receipt)?;
        cfg.tmux(&[
            "set-option",
            "-p",
            "-t",
            pane,
            "@mios-desktop-panel-origin",
            &receipt.origin,
        ])?;
    }
    let panel = receipt
        .panel
        .as_ref()
        .ok_or("desktop panel reservation is missing")?;
    cfg.tmux(&[
        "set-option",
        "-p",
        "-t",
        &panel.pane,
        "@mios-desktop-panel",
        "1",
    ])?;
    cfg.tmux(&[
        "set-option",
        "-p",
        "-t",
        &panel.pane,
        "pane-border-format",
        &policy.topbar,
    ])?;
    cfg.tmux(&[
        "set-option",
        "-w",
        "-t",
        &receipt.window,
        "@mios-desktop-panel-pane",
        &panel.pane,
    ])?;
    cfg.tmux(&[
        "set-option",
        "-w",
        "-t",
        &receipt.window,
        "@mios-desktop-workarea-pane",
        &receipt.canvas,
    ])?;
    cfg.tmux(&[
        "set-option",
        "-w",
        "-t",
        &receipt.window,
        "@mios-desktop-panel-height",
        &policy.height_format(),
    ])?;
    cfg.tmux(&[
        "set-option",
        "-w",
        "-t",
        &receipt.window,
        "@mios-desktop-panel-viewport",
        "",
    ])?;
    let resize = panel_resize_command(&panel.pane);
    cfg.tmux(&[
        "set-hook",
        "-t",
        &receipt.session,
        PANEL_RESIZE_HOOK,
        &resize,
    ])?;
    cfg.tmux(&["if-shell", "-F", "-t", &panel.pane, "1", &resize])?;
    verify_panel(cfg, receipt, panel)
}

fn scoped_options(cfg: &StartConfig, receipt: &mut Receipt, fresh: bool) -> Result<(), String> {
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
    if cfg.titles || cfg.panel.is_some() {
        let border = if cfg.titles {
            cfg.tmux(&["show-options", "-gqv", "@mios-desktop-border-format"])?
        } else {
            String::new()
        };
        if cfg.titles && border.is_empty() {
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
    ensure_panel(cfg, receipt, fresh)?;
    ensure_sidebar(cfg, receipt, fresh)
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
    // These settings belong only to the dedicated server. Suppress the AI
    // workspace session-created hook before the first canvas can be created.
    // Forward requested terminal focus events immediately so applications can
    // release held input on focus loss rather than wait for inputd's polling.
    OpenOptions::new()
        .append(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(&conf)
        .and_then(|mut file| {
            writeln!(
                file,
                "\nset -g @mios-bare 1\nset -s exit-empty off\nset -s focus-events on"
            )
        })
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
        let mut receipt = existing.ok_or(
            "the desktop socket already exists without an owned reservation; nothing was changed",
        )?;
        verify(cfg, &receipt)?;
        let conf = project_theme(cfg)?;
        let conf = conf.to_str().ok_or("desktop runtime path must be UTF-8")?;
        cfg.tmux(&["source-file", conf])?;
        scoped_options(cfg, &mut receipt, false)?;
        publish(&path, &receipt)?;
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
            "#{session_id}|#{window_id}|#{pane_id}|#{pane_pid}|#{pid}",
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
    let fields: Vec<&str> = output.trim().split('|').collect();
    if fields.len() != 5 || !id(fields[0], '$') || !id(fields[1], '@') || !id(fields[2], '%') {
        return Err("desktop start did not return its exact canvas identity".into());
    }
    let canvas_pid = fields[3].parse::<u32>().map_err(|_| "invalid canvas PID")?;
    let server_pid = fields[4]
        .parse::<u32>()
        .map_err(|_| "invalid desktop server PID")?;
    let metadata = socket_metadata(&cfg.socket)?;
    let mut receipt = Receipt {
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
        panel: None,
        login: cfg.login.clone(),
        chat: None,
        metrics: None,
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
    scoped_options(cfg, &mut receipt, true)?;
    publish(&path, &receipt)?;
    verify(cfg, &receipt)?;
    Ok(Reservation::from(&receipt))
}

/// Enter the dedicated desktop. Reuse an existing physical bridge, or ask
/// inputd to own one for a newly authenticated controlling Linux VT.
pub(crate) fn remote_login_enabled_from(trusted: &Value) -> Result<bool, String> {
    let Some(policy) = trusted
        .get("terminal")
        .and_then(|value| value.get("remote_login"))
    else {
        return Ok(false); // Older trusted policy predates the optional portal.
    };
    policy
        .get("enabled")
        .and_then(Value::as_bool)
        .ok_or_else(|| "trusted remote login enabled policy must be boolean".into())
}

/// SSH startup stays independent of an optional authentication module. Only
/// trusted vendor/host policy may enable it; an enabled gate never falls back.
pub fn login(root: &Path) -> Result<(), String> {
    let trusted = mios_resolver::resolve_projection(Path::new("/"))
        .map_err(|error| format!("trusted remote login policy: {error}"))?;
    if remote_login_enabled_from(&trusted)? {
        let error = Command::new(std::env::current_exe().map_err(|error| error.to_string())?)
            .args(["remote-login", "open", "--root"])
            .arg(root)
            .exec();
        return Err(format!("remote login entry: {error}"));
    }
    open(root, false)
}

pub fn open(root: &Path, no_attach: bool) -> Result<(), String> {
    open_with_login_fd(root, no_attach, None)
}

pub fn open_with_login_fd(
    root: &Path,
    no_attach: bool,
    login_tty_fd: Option<i32>,
) -> Result<(), String> {
    let doc = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let mut cfg = StartConfig::read(root, &doc)?;
    // A nesting variable is never authority, but can safely refuse recursive
    // entry before a new inner PTY could partition itself as another view.
    if !no_attach
        && std::env::var_os("TMUX").is_some_and(|value| {
            value
                .to_str()
                .and_then(|text| text.split(',').next())
                .map(Path::new)
                .and_then(Path::file_name)
                == cfg.socket.file_name()
        })
    {
        return Err("this client is already inside the MiOS desktop server".into());
    }
    // Maintenance --no-attach keeps the base namespace. An explicit inherited
    // terminal can reserve its authenticated view without attaching or asking
    // for controller routing, which also permits a private canonical proof.
    let login_tty = if !no_attach || login_tty_fd.is_some() {
        Some(LoginTty::capture(login_tty_fd)?)
    } else {
        None
    };
    if let Some(tty) = &login_tty {
        let identity = tty.identity(&cfg)?;
        cfg.partition_login(identity)?;
    }
    if !no_attach
        && std::env::var_os("TMUX")
            .is_some_and(|value| value.to_string_lossy().split(',').next() == cfg.socket.to_str())
    {
        return Err("this client is already inside the MiOS desktop server".into());
    }
    if let Some(mut bridge) = cfg.console_bridge_command(no_attach, login_tty.as_ref())? {
        return Err(format!("desktop console bridge: {}", bridge.exec()));
    }
    let outer = std::env::var_os("TMUX").is_none_or(|value| value.is_empty());
    if let Err(error) = ensure_login_input_service(&cfg, no_attach, outer) {
        eprintln!("desktop input service: {error}; keyboard input remains available");
    }
    let reservation = reserve_config(&cfg)?;
    if no_attach {
        println!(
            "{}",
            serde_json::to_string(&reservation).map_err(|e| e.to_string())?
        );
        return Ok(());
    }
    // An existing outer tmux route keeps Inputd's nested Claim authority. A
    // fresh login requests its own outer-client handoff after attach supplies
    // the actual client_name; no client is guessed from a shared server.
    let handoff = if outer {
        Some(LoginHandoff::new(&cfg)?)
    } else {
        None
    };
    let mut command = cfg.command();
    command.args(["attach-session", "-t", &reservation.session]);
    if let Some(handoff) = &handoff {
        command.args([";", "run-shell", "-b", &handoff.attach_command(&cfg)?]);
    }
    let status = command.status().map_err(|e| e.to_string())?;
    if let Some(handoff) = &handoff {
        handoff.detached(&cfg, &reservation.session);
    }
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
    panel_enabled: bool,
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
        panel_enabled: PanelPolicy::read(doc)?.is_some(),
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
    if cfg.panel_enabled {
        return; // The protected native panel owns the topbar; the canvas is blank.
    }
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
        let (shell, command) = shell_policy(Path::new("/"), &doc).unwrap();
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
        assert!(shell_policy(
            Path::new("/missing-vendor-must-not-override-explicit-shell"),
            &doc
        )
        .unwrap()
        .1
        .contains("'/private/rc with spaces'"));
    }

    #[test]
    fn older_operator_catalogue_derives_only_startup_shell_from_vendor_policy() {
        let mut vendor_doc = vendor();
        let row = vendor_doc["terminal"]["tuis"]
            .as_array_mut()
            .unwrap()
            .iter_mut()
            .find(|row| row.get("id").and_then(Value::as_str) == Some("terminal-shell"))
            .unwrap();
        row["argv"] = Value::Array(vec![
            Value::String("/bin/sh".into()),
            Value::String("-i".into()),
        ]);
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("usr/share/mios/mios.toml");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, toml::to_string(&vendor_doc).unwrap()).unwrap();
        let mut operator = vendor();
        operator["terminal"]["tuis"]
            .as_array_mut()
            .unwrap()
            .retain(|row| row.get("id").and_then(Value::as_str) != Some("terminal-shell"));
        let before = operator.clone();
        assert_eq!(
            shell_policy(root.path(), &operator).unwrap(),
            ("/bin/sh".into(), "'/bin/sh' '-i'".into())
        );
        assert_eq!(
            operator, before,
            "startup fallback must not rewrite the operator catalogue"
        );
    }

    #[test]
    fn malformed_explicit_shell_is_rejected_instead_of_using_vendor_fallback() {
        let vendor_doc = vendor();
        let root = tempfile::tempdir().unwrap();
        let path = root.path().join("usr/share/mios/mios.toml");
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, toml::to_string(&vendor_doc).unwrap()).unwrap();
        let mut operator = vendor();
        let row = operator["terminal"]["tuis"]
            .as_array_mut()
            .unwrap()
            .iter_mut()
            .find(|row| row.get("id").and_then(Value::as_str) == Some("terminal-shell"))
            .unwrap();
        row["argv"] = Value::Array(Vec::new());
        assert!(shell_policy(root.path(), &operator).is_err());
    }

    #[test]
    fn optional_remote_auth_uses_only_explicit_trusted_boolean_policy() {
        assert!(!remote_login_enabled_from(&Value::Table(Default::default())).unwrap());
        for enabled in [false, true] {
            let doc: Value = format!("[terminal.remote_login]\nenabled={enabled}")
                .parse()
                .unwrap();
            assert_eq!(remote_login_enabled_from(&doc).unwrap(), enabled);
        }
        let malformed: Value = "[terminal.remote_login]\nenabled='false'".parse().unwrap();
        assert!(remote_login_enabled_from(&malformed).is_err());
        let partial: Value = "[terminal.remote_login]\n".parse().unwrap();
        assert!(remote_login_enabled_from(&partial).is_err());
    }

    fn login_witness() -> LoginIdentity {
        LoginIdentity {
            version: 1,
            namespace_only: true,
            uid: unsafe { libc::getuid() },
            session_id: "42".into(),
            scope: "session-42.scope".into(),
            leader: 100,
            leader_start: 200,
            process_pid: 300,
            process_start: 400,
            tty_dev: 5,
            tty_ino: 6,
            tty_rdev: 7,
            service: Some("sshd".into()),
            remote: true,
        }
    }

    #[test]
    fn input_service_start_requires_a_real_host_attach_with_a_verified_login() {
        let login = login_witness();
        assert!(input_service_admitted(
            Path::new("/"),
            Some(&login),
            false,
            true
        ));
        for (root, login, no_attach, outer) in [
            (Path::new("/private/proof"), Some(&login), false, true),
            (Path::new("/"), None, false, true),
            (Path::new("/"), Some(&login), true, true),
            (Path::new("/"), Some(&login), false, false),
        ] {
            assert!(!input_service_admitted(root, login, no_attach, outer));
        }
        let mut foreign = login.clone();
        foreign.uid += 1;
        assert!(!input_service_admitted(
            Path::new("/"),
            Some(&foreign),
            false,
            true
        ));
        let mut console = login;
        console.service = Some("login".into());
        console.remote = false;
        // The physical bridge child retains and reproves the original tty;
        // its read-only login witness must not be rejected as a private proof.
        assert!(input_service_admitted(
            Path::new("/"),
            Some(&console),
            false,
            true
        ));
    }

    fn input_service_output(text: &str, code: i32) -> Output {
        use std::os::unix::process::ExitStatusExt;
        Output {
            status: std::process::ExitStatus::from_raw(code << 8),
            stdout: text.as_bytes().to_vec(),
            stderr: Vec::new(),
        }
    }

    #[test]
    fn enabled_input_service_recovers_without_enabling_restarting_or_using_an_inherited_bus() {
        for (load, enabled, active, expected_start) in [
            ("loaded", "enabled", "inactive", true),
            ("loaded", "enabled-runtime", "failed", true),
            ("loaded", "enabled", "activating", true),
            ("loaded", "enabled", "active", false),
            ("loaded", "enabled", "deactivating", false),
            ("loaded", "disabled", "inactive", false),
            ("masked", "masked", "inactive", false),
            ("not-found", "", "inactive", false),
        ] {
            let mut operations = Vec::new();
            start_enabled_input_service(Path::new("/run/user/1000"), |command, _| {
                assert_eq!(command.get_program(), "/usr/bin/systemctl");
                let arguments = command
                    .get_args()
                    .map(|s| s.to_str().unwrap())
                    .collect::<Vec<_>>();
                assert_eq!(
                    &arguments[..3],
                    ["--user", "--no-pager", "--no-ask-password"]
                );
                let operation = arguments[3];
                operations.push(operation.to_owned());
                let environment = command
                    .get_envs()
                    .map(|(k, v)| (k.to_str().unwrap(), v.unwrap().to_str().unwrap()))
                    .collect::<std::collections::BTreeMap<_, _>>();
                assert_eq!(environment.len(), 4);
                assert_eq!(environment["XDG_RUNTIME_DIR"], "/run/user/1000");
                assert_eq!(
                    environment["DBUS_SESSION_BUS_ADDRESS"],
                    "unix:path=/run/user/1000/bus"
                );
                if operation == "show" {
                    Ok(input_service_output(
                        &format!(
                            "LoadState={load}\nUnitFileState={enabled}\nActiveState={active}\n"
                        ),
                        0,
                    ))
                } else {
                    assert_eq!(&arguments[3..], ["start", "mios-inputd.service"]);
                    Ok(input_service_output("", 0))
                }
            })
            .unwrap();
            assert_eq!(
                operations,
                if expected_start {
                    vec!["show", "start"]
                } else {
                    vec!["show"]
                }
            );
        }
    }

    #[test]
    fn input_service_failures_remain_errors_and_never_trigger_a_reset_or_second_start() {
        for status in [
            input_service_output("", 1),
            input_service_output("LoadState=loaded\n", 0),
            input_service_output("LoadState=loaded\nLoadState=loaded\n", 0),
        ] {
            let mut calls = 0;
            assert!(
                start_enabled_input_service(Path::new("/run/user/1000"), |_, _| {
                    calls += 1;
                    Ok(status.clone())
                })
                .is_err()
            );
            assert_eq!(calls, 1);
        }
        let mut operations = Vec::new();
        assert!(
            start_enabled_input_service(Path::new("/run/user/1000"), |command, _| {
                let operation = command
                    .get_args()
                    .nth(3)
                    .unwrap()
                    .to_str()
                    .unwrap()
                    .to_owned();
                operations.push(operation.clone());
                Ok(if operation == "show" {
                    input_service_output(
                        "LoadState=loaded\nUnitFileState=enabled\nActiveState=failed\n",
                        0,
                    )
                } else {
                    input_service_output("", 1)
                })
            })
            .is_err()
        );
        assert_eq!(operations, ["show", "start"]);
    }

    #[test]
    fn authenticated_view_key_tracks_the_login_and_original_tty_not_query_processes() {
        let original = login_witness();
        let mut child = original.clone();
        child.process_pid += 1;
        child.process_start += 1;
        assert_eq!(original.digest().unwrap(), child.digest().unwrap());
        assert!(
            original.same_login(&child),
            "bridge child reproves the same original login"
        );
        for field in ["tty", "leader", "session"] {
            let mut changed = original.clone();
            match field {
                "tty" => changed.tty_ino += 1,
                "leader" => changed.leader_start += 1,
                _ => {
                    changed.session_id = "43".into();
                    changed.scope = "session-43.scope".into();
                }
            }
            assert_ne!(original.digest().unwrap(), changed.digest().unwrap());
            assert!(!original.same_login(&changed));
        }
        child.service = Some("login".into());
        assert!(
            !original.same_login(&child),
            "reuse compares the complete stable authority witness"
        );
        assert!(LoginTty::capture(Some(-1)).is_err());
    }

    #[test]
    fn authenticated_namespaces_keep_the_exact_socket_basename_and_reject_long_paths() {
        let root = tempfile::tempdir().unwrap();
        let mut doc: Value = include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap();
        doc["terminal"]["socket_root"] = Value::String(root.path().display().to_string());
        doc["keybindings"]["tmux"]["desktop"]["binary"] = Value::String("/usr/bin/true".into());
        let mut first = StartConfig::read(root.path(), &doc).unwrap();
        let mut second = StartConfig::read(root.path(), &doc).unwrap();
        first.partition_login(login_witness()).unwrap();
        let mut different = login_witness();
        different.tty_ino += 1;
        second.partition_login(different).unwrap();
        assert_eq!(
            first.socket.file_name(),
            Some(std::ffi::OsStr::new("mios-desktop"))
        );
        assert_ne!(first.socket, second.socket);
        assert_ne!(first.runtime, second.runtime);
        assert_ne!(first.socket.parent(), second.socket.parent());
        let mut long = StartConfig::read(root.path(), &doc).unwrap();
        long.socket = PathBuf::from(format!("/{}/tmux-1/mios-desktop", "x".repeat(100)));
        assert!(long
            .partition_login(login_witness())
            .unwrap_err()
            .contains("Unix address capacity"));
    }

    #[test]
    fn original_login_descriptor_survives_only_explicit_query_or_bridge_children() {
        let mut master = -1;
        let mut slave = -1;
        // SAFETY: openpty writes two owned descriptors to live storage. No
        // termios/window-size pointer or input reader is supplied.
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
        // SAFETY: successful openpty returned each descriptor exactly once.
        let _master = unsafe { OwnedFd::from_raw_fd(master) };
        let slave = unsafe { OwnedFd::from_raw_fd(slave) };
        let original = LoginTty::capture(Some(slave.as_raw_fd())).unwrap();
        let fd = original.0.as_raw_fd();
        let mut ordinary = Command::new("/usr/bin/test");
        ordinary.args(["-t", &fd.to_string()]);
        let ordinary =
            crate::sessions::run_bounded(&mut ordinary, Instant::now() + Duration::from_secs(2))
                .unwrap();
        assert!(
            !ordinary.status.success(),
            "ordinary applications must not inherit the original login terminal"
        );
        let mut query = Command::new("/usr/bin/test");
        query.args(["-t", &fd.to_string()]);
        original.inherit_for(&mut query);
        let query =
            crate::sessions::run_bounded(&mut query, Instant::now() + Duration::from_secs(2))
                .unwrap();
        assert!(
            query.status.success(),
            "inputd receives the retained descriptor even with stdin set to null"
        );
    }

    #[test]
    fn desktop_sidebar_reuses_workspace_roles_shares_and_the_lean_chooser() {
        let mut doc: Value = include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap();
        let policy = SidebarPolicy::read(&doc).unwrap().unwrap();
        assert!(policy.chat_first);
        assert_eq!(policy.chat_argv[2], "--desktop-chat");
        assert_eq!(
            policy.metrics_command,
            doc["mcp"]["tmux"]["workspace"]["resource_command"]
                .as_str()
                .unwrap()
        );
        assert_eq!(
            policy.column_share,
            doc["mcp"]["tmux"]["workspace"]["layout"]["column_share"]
                .as_float()
                .unwrap()
        );
        doc["mcp"]["tmux"]["workspace"]["layout"]["left_roles"] = Value::Array(vec![
            Value::String("metrics".into()),
            Value::String("chat".into()),
        ]);
        assert!(!SidebarPolicy::read(&doc).unwrap().unwrap().chat_first);
        doc["mcp"]["tmux"]["workspace"]["layout"]["left_roles"] = Value::Array(vec![
            Value::String("chat".into()),
            Value::String("chat".into()),
        ]);
        assert!(SidebarPolicy::read(&doc).is_err());
    }

    #[test]
    fn panel_geometry_uses_shared_launch_scale_and_keeps_a_lower_work_area() {
        let doc: Value = include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap();
        let policy = PanelPolicy::read(&doc).unwrap().unwrap();
        assert_eq!(policy.body_rows(240, 65), 18);
        assert_eq!(policy.body_rows(80, 18), 4);
        for width in 1..200 {
            for height in 4..80 {
                let rows = policy.body_rows(width, height);
                assert!((1..=height - 3).contains(&rows));
                if height >= policy.min_canvas_rows + 5 {
                    assert!(height - rows - 2 >= policy.min_canvas_rows + 2);
                }
            }
        }
        let resize = panel_resize_command("%42");
        assert!(resize.contains("run-shell -C -t %42"));
        assert!(!resize.contains("run-shell -b"));
        assert!(resize.contains("pane_floating_flag"));
        assert!(
            resize.find("@mios-desktop-panel-viewport").unwrap()
                < resize.find("resize-pane").unwrap()
        );
        assert!(!policy.height_format().contains("#("));
    }

    #[test]
    fn new_panel_canvas_is_blank_without_restarting_collectors_on_resize() {
        let doc: Value = include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap();
        let cfg = config_for_terminal(&doc, "tmux-256color", false).unwrap();
        assert!(cfg.panel_enabled);
        for area in [
            Rect::new(0, 0, 80, 24),
            Rect::new(2, 3, 120, 40),
            Rect::new(0, 0, 5, 2),
        ] {
            let mut buffer = Buffer::empty(area);
            paint(&cfg, area, &mut buffer);
            assert!(buffer.content.iter().all(|cell| cell.symbol() == " "));
        }
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
            console_bridge: false,
            panel: None,
            login: None,
            sidebar: None,
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
