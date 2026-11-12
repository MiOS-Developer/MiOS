// AI-hint: MiOS TTY desktop window actions preserve app processes and PTYs and verify the explicit socket, client, server and pane before mutation.
// AI-related: usr/share/mios/mios.toml [keybindings.tmux.desktop], tools/native/mios-resolver/src/popup.rs, tools/native/mios-gen/src/tmux_os.rs
// AI-functions: run, geometry, context, minimize, restore, dock_state, desktop_app, launch_app
// Scaffolded by native miosd scaffold rust mios-gen/src/tmux_desktop; canonical template SHA256 2a9bf43dc4c1e0af3fd0b26f3b42217740a5dea7c86a7e619254f8cb62683203.

#![forbid(unsafe_code)]

use mios_resolver::popup::{PopupPolicy, SurfaceKind};
use mios_unit_gen::tmux_os::{AGENT_PANE, MANAGED_PANE};
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::fs;
use std::io::Read;
use std::os::unix::fs::{FileTypeExt, MetadataExt};
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Duration;
use toml::Value;

const RECORD: &str = "@mios-desktop-window";
const CANVAS: &str = "@mios-desktop-canvas";
const CLOSE: &str = "@mios-desktop-close";

fn at<'a>(v: &'a Value, key: &str) -> Result<&'a Value, String> {
    key.split('.')
        .try_fold(v, |v, k| v.get(k))
        .ok_or_else(|| format!("missing SSOT key {key}"))
}
fn text(v: &Value, key: &str) -> Result<String, String> {
    at(v, key)?
        .as_str()
        .filter(|s| !s.is_empty() && !s.chars().any(char::is_control))
        .map(str::to_owned)
        .ok_or_else(|| format!("{key} must be nonempty plain text"))
}
fn number(v: &Value, key: &str) -> Result<u32, String> {
    at(v, key)?
        .as_integer()
        .and_then(|n| u32::try_from(n).ok())
        .filter(|n| *n <= 4096)
        .ok_or_else(|| format!("invalid {key}"))
}
fn identifier(s: &str, prefix: char) -> bool {
    s.starts_with(prefix) && s.len() > 1 && s[1..].bytes().all(|b| b.is_ascii_digit())
}
fn safe_label(s: &str) -> String {
    s.chars().filter(|c| !c.is_control()).take(96).collect()
}
fn tmux_label(s: &str) -> String {
    safe_label(s).replace('#', "##")
}
fn quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', "'\\''"))
}

/// Control clients need locale and executable lookup, not the launching agent's entire environment.
fn minimal_environment(command: &mut Command) {
    let allowed = std::env::vars_os().filter(|(key, _)| {
        key.to_str().is_some_and(|key| {
            matches!(
                key,
                "HOME"
                    | "PATH"
                    | "TERM"
                    | "LANG"
                    | "USER"
                    | "LOGNAME"
                    | "SHELL"
                    | "TMPDIR"
                    | "TMUX_TMPDIR"
            ) || key.starts_with("LC_")
        })
    });
    command.env_clear().envs(allowed);
}

#[derive(Default, Debug)]
struct Args {
    verb: String,
    socket: PathBuf,
    client: String,
    pane: Option<String>,
    expected_pid: Option<u32>,
    expected_window: Option<String>,
    nonce: Option<String>,
    double: bool,
    format: Option<String>,
    item: Option<String>,
    mouse_x: Option<u32>,
    mouse_y: Option<u32>,
    section: Option<String>,
}
fn parse(args: &[String]) -> Result<Args, String> {
    let mut a = Args {
        verb: args.first().ok_or("desktop needs an action")?.clone(),
        ..Args::default()
    };
    let mut i = 1;
    while i < args.len() {
        let key = &args[i];
        if key == "--double" {
            a.double = true;
            i += 1;
            continue;
        }
        let val = args
            .get(i + 1)
            .ok_or_else(|| format!("missing value for {key}"))?;
        match key.as_str() {
            "--socket" => a.socket = PathBuf::from(val),
            "--client" => a.client = crate::tmux_os::client_arg(val)?,
            "--pane" if identifier(val, '%') => a.pane = Some(val.clone()),
            "--expected-pid" => {
                a.expected_pid = Some(val.parse().map_err(|_| "invalid expected pane PID")?)
            }
            "--expected-window" if identifier(val, '@') => a.expected_window = Some(val.clone()),
            "--nonce" if val.len() == 32 && val.bytes().all(|b| b.is_ascii_hexdigit()) => {
                a.nonce = Some(val.clone())
            }
            "--format" if matches!(val.as_str(), "json" | "tmux") => a.format = Some(val.clone()),
            "--item"
                if val == "apps" || val.strip_prefix("p:").is_some_and(|p| identifier(p, '%')) =>
            {
                a.item = Some(val.clone())
            }
            "--x" => a.mouse_x = Some(val.parse().map_err(|_| "invalid mouse x")?),
            "--y" => a.mouse_y = Some(val.parse().map_err(|_| "invalid mouse y")?),
            "--section" if matches!(val.as_str(), "snap" | "corners") => {
                a.section = Some(val.clone())
            }
            _ => return Err(format!("invalid desktop argument {key:?}")),
        }
        i += 2;
    }
    if !a.socket.is_absolute()
        || a.socket
            .to_str()
            .is_none_or(|s| s.chars().any(char::is_control))
    {
        return Err("desktop requires an explicit absolute --socket".into());
    }
    if a.client.is_empty() {
        return Err("desktop requires an explicit --client".into());
    }
    Ok(a)
}

#[derive(Clone, Copy, Debug, Serialize, Deserialize, PartialEq, Eq)]
struct Rect {
    x: u32,
    y: u32,
    w: u32,
    h: u32,
}
impl Rect {
    fn bounded(self, width: u32, height: u32, min_w: u32, min_h: u32) -> Result<Self, String> {
        if width < min_w || height < min_h {
            return Err("desktop viewport is smaller than its configured minimum window".into());
        }
        let w = self.w.clamp(min_w, width);
        let h = self.h.clamp(min_h, height);
        Ok(Self {
            x: self.x.min(width - w),
            y: self.y.min(height - h),
            w,
            h,
        })
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum TitleControl {
    Close,
    Minimize,
    Maximize,
}

/// Native pane status starts at body-left + 2, on the row immediately above the body.
/// Keep these nine cells aligned with the projection's left-side [x][_][+] controls.
fn title_control(rect: Rect, x: u32, y: u32) -> Option<TitleControl> {
    if rect.w < 12 || y.checked_add(1) != Some(rect.y) {
        return None;
    }
    let column = x.checked_sub(rect.x.checked_add(2)?)?;
    match column {
        0..=2 => Some(TitleControl::Close),
        3..=5 => Some(TitleControl::Minimize),
        6..=8 => Some(TitleControl::Maximize),
        _ => None,
    }
}
struct Settings {
    binary: PathBuf,
    min_w: u32,
    min_h: u32,
    margin: u32,
    dock_columns: u32,
    prefix: String,
    popup: PopupPolicy,
    labels: BTreeMap<String, String>,
    command_timeout: Duration,
}
impl Settings {
    fn read(v: &Value) -> Result<Self, String> {
        if at(v, "keybindings.tmux.desktop.enabled")?.as_bool() != Some(true) {
            return Err("MiOS TTY desktop is disabled".into());
        }
        let binary = PathBuf::from(text(v, "keybindings.tmux.desktop.binary")?);
        if !binary.is_absolute() {
            return Err("desktop.binary must be absolute".into());
        }
        let min_w = number(v, "keybindings.tmux.desktop.min_columns")?;
        let min_h = number(v, "keybindings.tmux.desktop.min_rows")?;
        if min_w < 2 || min_h < 2 {
            return Err("desktop minimum dimensions must be >=2".into());
        }
        let prefix = text(v, "keybindings.tmux.float.session_prefix")?;
        if !prefix
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"_-".contains(&b))
        {
            return Err("invalid float session prefix".into());
        }
        let labels = at(v, "keybindings.tmux.desktop.labels")?
            .as_table()
            .ok_or("desktop labels must be a table")?
            .iter()
            .map(|(k, v)| {
                let label = v
                    .as_str()
                    .filter(|s| !s.is_empty() && !s.chars().any(char::is_control))
                    .ok_or("invalid desktop label")?;
                Ok((k.clone(), label.to_owned()))
            })
            .collect::<Result<_, String>>()?;
        let dock_columns = number(v, "keybindings.tmux.desktop.dock_item_columns")?;
        if dock_columns < 4 {
            return Err("desktop dock_item_columns must be >=4".into());
        }
        let timeout = at(v, "keybindings.tmux.desktop.command_timeout_ms")?
            .as_integer()
            .and_then(|v| u64::try_from(v).ok())
            .filter(|v| (1..=30000).contains(v))
            .ok_or("invalid desktop command_timeout_ms")?;
        Ok(Self {
            binary,
            min_w,
            min_h,
            margin: number(v, "keybindings.tmux.desktop.snap_margin")?,
            dock_columns,
            prefix,
            popup: PopupPolicy::from_value(v)?,
            labels,
            command_timeout: Duration::from_millis(timeout),
        })
    }
    fn label(&self, key: &str) -> Result<&str, String> {
        self.labels
            .get(key)
            .map(String::as_str)
            .ok_or_else(|| format!("missing desktop label {key}"))
    }
}

/// Pure geometry keeps every snap inside the canvas, including odd dimensions and border gaps.
fn geometry(action: &str, width: u32, height: u32, s: &Settings) -> Result<Rect, String> {
    let m = s.margin;
    let w = width
        .checked_sub(m.saturating_mul(2))
        .ok_or("viewport has no desktop area")?;
    let h = height
        .checked_sub(m.saturating_mul(2))
        .ok_or("viewport has no desktop area")?;
    let (x, y, rw, rh) = match action {
        "center" | "float" => {
            let (pw, ph) = s.popup.size(
                SurfaceKind::Window,
                u16::try_from(w).map_err(|_| "viewport too wide")?,
                u16::try_from(h).map_err(|_| "viewport too tall")?,
            );
            let rw = u32::from(pw).max(s.min_w).min(w);
            let rh = u32::from(ph).max(s.min_h).min(h);
            (m + (w - rw) / 2, m + (h - rh) / 2, rw, rh)
        }
        "snap-left" => (m, m, w / 2, h),
        "snap-right" => (m + w / 2, m, w - w / 2, h),
        "snap-top" => (m, m, w, h / 2),
        "snap-bottom" => (m, m + h / 2, w, h - h / 2),
        "snap-top-left" => (m, m, w / 2, h / 2),
        "snap-top-right" => (m + w / 2, m, w - w / 2, h / 2),
        "snap-bottom-left" => (m, m + h / 2, w / 2, h - h / 2),
        "snap-bottom-right" => (m + w / 2, m + h / 2, w - w / 2, h - h / 2),
        _ => return Err(format!("unsupported geometry action {action}")),
    };
    if rw < s.min_w || rh < s.min_h {
        return Err("snap would make a window smaller than its configured minimum".into());
    }
    Rect { x, y, w: rw, h: rh }.bounded(width, height, s.min_w, s.min_h)
}

#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
struct Process {
    pid: u32,
    start: u64,
}
fn process(pid: u32) -> Result<Process, String> {
    let meta = fs::metadata(format!("/proc/{pid}")).map_err(|_| "pane process has exited")?;
    if meta.uid() != rustix::process::getuid().as_raw() {
        return Err("pane process belongs to another user".into());
    }
    let stat = fs::read_to_string(format!("/proc/{pid}/stat")).map_err(|e| e.to_string())?;
    let end = stat.rfind(')').ok_or("malformed process identity")?;
    let start = stat[end + 1..]
        .split_whitespace()
        .nth(19)
        .ok_or("missing process start identity")?
        .parse()
        .map_err(|_| "invalid process start identity")?;
    Ok(Process { pid, start })
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
struct Identity {
    socket_dev: u64,
    socket_ino: u64,
    server: Process,
}
struct Server {
    binary: PathBuf,
    socket: PathBuf,
    dev: u64,
    ino: u64,
    identity: Identity,
    command_timeout: Duration,
}
impl Server {
    fn socket_metadata(path: &Path) -> Result<fs::Metadata, String> {
        let meta = fs::symlink_metadata(path).map_err(|e| format!("desktop socket: {e}"))?;
        if !meta.file_type().is_socket() || meta.uid() != rustix::process::getuid().as_raw() {
            return Err("desktop socket must be a caller-owned Unix socket, not a symlink".into());
        }
        Ok(meta)
    }
    fn open(a: &Args, s: &Settings) -> Result<Self, String> {
        let meta = Self::socket_metadata(&a.socket)?;
        let socket = fs::canonicalize(&a.socket).map_err(|e| e.to_string())?;
        let dummy = Identity {
            socket_dev: meta.dev(),
            socket_ino: meta.ino(),
            server: Process { pid: 0, start: 0 },
        };
        let mut server = Self {
            binary: s.binary.clone(),
            socket,
            dev: meta.dev(),
            ino: meta.ino(),
            identity: dummy,
            command_timeout: s.command_timeout,
        };
        let info = server.cmd(&["display-message", "-p", "#{pid}\t#{socket_path}"])?;
        let (pid, sock) = info
            .split_once('\t')
            .ok_or("missing tmux server identity")?;
        if Path::new(sock) != server.socket {
            return Err("tmux server socket identity mismatch".into());
        }
        server.identity.server = process(pid.parse().map_err(|_| "invalid tmux server PID")?)?;
        Ok(server)
    }
    fn cmd(&self, args: &[&str]) -> Result<String, String> {
        let meta = Self::socket_metadata(&self.socket)?;
        if (meta.dev(), meta.ino()) != (self.dev, self.ino) {
            return Err("desktop socket was replaced".into());
        }
        if self.identity.server.pid != 0
            && process(self.identity.server.pid)? != self.identity.server
        {
            return Err("desktop server identity has changed".into());
        }
        let mut command = Command::new(&self.binary);
        minimal_environment(&mut command);
        // These short-lived RPC clients exchange tab-delimited UTF-8 records,
        // independently of the interactive client's locale or terminal modes.
        command.arg("-u").arg("-S").arg(&self.socket).args(args);
        let out = if args.first() == Some(&"display-menu") {
            // Native menus own a human interaction, rather than a bounded control query.
            command.output().map_err(|e| format!("desktop menu: {e}"))?
        } else {
            mios_service_core::process::output_timeout(&mut command, self.command_timeout)?
        };
        if !out.status.success() {
            return Err(format!(
                "desktop {}: {}",
                args.first().copied().unwrap_or("command"),
                String::from_utf8_lossy(&out.stderr).trim()
            ));
        }
        Ok(String::from_utf8_lossy(&out.stdout)
            .trim_end_matches('\n')
            .to_owned())
    }
    fn set(&self, pane: &str, key: &str, val: &str) -> Result<(), String> {
        self.cmd(&["set-option", "-p", "-t", pane, key, val])
            .map(|_| ())
    }
    fn option(&self, pane: &str, key: &str) -> Result<String, String> {
        self.cmd(&["show-options", "-pqv", "-t", pane, key])
    }
    fn require_float(&self) -> Result<(), String> {
        self.cmd(&["list-commands", "float-pane"]).map(|_| ()).map_err(|_| "this tmux server lacks safe float-pane geometry; open the configured MiOS desktop server".into())
    }
    fn pane(&self, id: &str) -> Result<Pane, String> {
        if !identifier(id, '%') {
            return Err("invalid pane ID".into());
        }
        let format = format!(
            "{}\t{}",
            "#{pane_id}\t#{pane_pid}\t#{pane_tty}\t#{session_id}\t#{window_id}\t#{pane_left}\t#{pane_top}\t#{pane_width}\t#{pane_height}\t#{window_width}\t#{window_height}\t#{pane_floating_flag}\t#{window_zoomed_flag}\t#{pane_active}\t#{@mios-workspace-head}\t#{@mios-mcp-owner}\t#{@mios-workspace-worker}\t#{E:@mios-managed-pane}\t#{@mios-desktop-canvas}\t#{@mios-desktop-panel}\t#{@mios-desktop-chat}\t#{@mios-desktop-metrics}\t#{@mios-managed-pane}",
            AGENT_PANE
        );
        let row = self.cmd(&["display-message", "-p", "-t", id, &format])?;
        let c: Vec<&str> = row.split('\t').collect();
        if c.len() != 24 || c[0] != id || !identifier(c[3], '$') || !identifier(c[4], '@') {
            return Err("pane target identity mismatch".into());
        }
        let n = |i: usize| {
            c[i].parse::<u32>()
                .map_err(|_| "invalid pane geometry/identity".to_owned())
        };
        let pid = n(1)?;
        let canvas = c[18] == "1";
        let proc = if pid == 0 && canvas {
            Process { pid, start: 0 }
        } else {
            process(pid)?
        };
        Ok(Pane {
            id: id.into(),
            process: proc,
            tty: c[2].into(),
            session: c[3].into(),
            window: c[4].into(),
            rect: Rect {
                x: n(5)?,
                y: n(6)?,
                w: n(7)?,
                h: n(8)?,
            },
            width: n(9)?,
            height: n(10)?,
            floating: c[11] == "1",
            zoomed: c[12] == "1",
            protection: pane_protection(c[23], c[22], c[17], &c[18..22]),
            canvas,
        })
    }
    fn panes(&self) -> Result<Vec<Pane>, String> {
        self.cmd(&["list-panes", "-a", "-F", "#{pane_id}"])?
            .lines()
            .map(|id| self.pane(id))
            .collect()
    }
    fn client(&self, name: &str) -> Result<Client, String> {
        let rows = self.cmd(&[
            "list-clients",
            "-F",
            "#{client_name}\t#{client_pid}\t#{client_session}",
        ])?;
        let c = rows
            .lines()
            .filter_map(|r| {
                let v: Vec<&str> = r.split('\t').collect();
                (v.len() == 3 && v[0] == name).then_some(v)
            })
            .collect::<Vec<_>>();
        if c.len() != 1 {
            return Err("desktop client is missing or ambiguous".into());
        }
        let proc = process(c[0][1].parse().map_err(|_| "invalid client PID")?)?;
        let sessions = self.cmd(&["list-sessions", "-F", "#{session_name}\t#{session_id}"])?;
        let sid = sessions
            .lines()
            .find_map(|r| {
                r.split_once('\t')
                    .filter(|(n, _)| *n == c[0][2])
                    .map(|(_, id)| id.to_owned())
            })
            .ok_or("client session vanished")?;
        let window = self.cmd(&[
            "display-message",
            "-p",
            "-c",
            name,
            "-t",
            &format!("{sid}:"),
            "#{window_id}",
        ])?;
        if !identifier(&sid, '$') || !identifier(&window, '@') {
            return Err("invalid client window identity".into());
        }
        Ok(Client {
            name: name.into(),
            process: proc,
            session: sid,
            window,
        })
    }
    fn witness(&self, before: &Pane) -> Result<Pane, String> {
        let after = self.pane(&before.id)?;
        if after.process != before.process || after.tty != before.tty {
            return Err("pane process or PTY changed during desktop action".into());
        }
        Ok(after)
    }
}
#[derive(Clone)]
struct Client {
    name: String,
    process: Process,
    session: String,
    window: String,
}
#[derive(Clone)]
struct Pane {
    id: String,
    process: Process,
    tty: String,
    session: String,
    window: String,
    rect: Rect,
    width: u32,
    height: u32,
    floating: bool,
    zoomed: bool,
    protection: PaneProtection,
    canvas: bool,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum PaneProtection {
    Application,
    DesktopRole,
    Managed,
}

fn marked(value: &str) -> bool {
    !value.is_empty() && value != "0"
}

/// The shared generated predicate protects both desktop roles and agents from
/// ordinary layout keys. That does not make a desktop role an agent workspace.
/// Explicit agents and manual/unknown managed predicates always retain priority.
fn pane_protection(
    agent: &str,
    managed_expression: &str,
    managed_value: &str,
    desktop_roles: &[&str],
) -> PaneProtection {
    if marked(agent)
        || desktop_roles
            .iter()
            .any(|role| marked(role) && *role != "1")
        || (marked(managed_value) && managed_expression != MANAGED_PANE)
    {
        return PaneProtection::Managed;
    }
    if desktop_roles.contains(&"1") {
        if managed_expression == MANAGED_PANE && !marked(managed_value) {
            return PaneProtection::Managed;
        }
        return PaneProtection::DesktopRole;
    }
    if marked(managed_value) {
        PaneProtection::Managed
    } else {
        PaneProtection::Application
    }
}

fn guard_protection(
    target: PaneProtection,
    action: &str,
    observed: &[PaneProtection],
    native_count: usize,
) -> Result<(), String> {
    let launch = desktop_app(action).is_some();
    if target == PaneProtection::Managed
        || (target == PaneProtection::DesktopRole && !launch && action != "focus")
    {
        return Err(
            "managed agents and desktop roles cannot be rearranged by desktop actions".into(),
        );
    }
    if observed.contains(&PaneProtection::Managed) {
        return Err("managed agent workspace layout is protected".into());
    }
    // Some native list-panes views omit floating panes. The explicit target is
    // added by guard(), but any remaining unobserved pane could be an agent.
    if observed.len() != native_count {
        return Err("desktop window contains unobserved panes; nothing changed".into());
    }
    Ok(())
}

/// Apps may dock against the desktop's blank canvas, but never against the
/// panel/sidebar roles or an agent that also advertises a canvas marker.
fn app_anchor(protection: PaneProtection, canvas: bool) -> bool {
    protection == PaneProtection::Application
        || (protection == PaneProtection::DesktopRole && canvas)
}
#[derive(Clone, Serialize, Deserialize)]
struct Record {
    version: u8,
    identity: Identity,
    pane: String,
    process: Process,
    tty: String,
    origin_session: String,
    origin_window: String,
    rect: Rect,
    restore_rect: Option<Rect>,
    minimized: bool,
    store: Option<String>,
}
impl Record {
    fn new(server: &Server, p: &Pane) -> Self {
        Self {
            version: 1,
            identity: server.identity.clone(),
            pane: p.id.clone(),
            process: p.process.clone(),
            tty: p.tty.clone(),
            origin_session: p.session.clone(),
            origin_window: p.window.clone(),
            rect: p.rect,
            restore_rect: None,
            minimized: false,
            store: None,
        }
    }
    fn valid(&self, server: &Server, p: &Pane) -> Result<(), String> {
        if self.version != 1
            || self.identity != server.identity
            || self.pane != p.id
            || self.process != p.process
            || self.tty != p.tty
        {
            return Err("stale or cross-server desktop window identity".into());
        }
        Ok(())
    }
    fn save(&self, server: &Server) -> Result<(), String> {
        server.set(
            &self.pane,
            RECORD,
            &serde_json::to_string(self).map_err(|e| e.to_string())?,
        )
    }
}
fn record(server: &Server, p: &Pane) -> Result<Option<Record>, String> {
    let value = server.option(&p.id, RECORD)?;
    if value.is_empty() {
        return Ok(None);
    }
    let record: Record = serde_json::from_str(&value).map_err(|_| "invalid desktop pane record")?;
    record.valid(server, p)?;
    Ok(Some(record))
}
fn guard(
    server: &Server,
    c: &Client,
    p: &Pane,
    a: &Args,
    allow_hidden: bool,
) -> Result<(), String> {
    if a.expected_pid.is_some_and(|pid| pid != p.process.pid)
        || a.expected_window.as_ref().is_some_and(|w| w != &p.window)
    {
        return Err("stale desktop pane callback".into());
    }
    let hidden = if allow_hidden {
        record(server, p)?.is_some_and(|r| r.minimized && r.origin_session == c.session)
    } else {
        false
    };
    if !hidden && (p.session != c.session || (p.window != c.window && a.verb != "focus")) {
        return Err("pane is outside the explicit client's active desktop window".into());
    }
    let mut panes: BTreeMap<String, Pane> = server
        .panes()?
        .into_iter()
        .map(|pane| (pane.id.clone(), pane))
        .collect();
    // The target may be a native float absent from list-panes. Querying it
    // directly already proved its process, PTY, session and window above.
    panes.insert(p.id.clone(), p.clone());
    let focused = server.cmd(&[
        "display-message",
        "-p",
        "-c",
        &c.name,
        "-t",
        &c.window,
        "#{client_pid}\t#{client_window_id}\t#{client_pane_id}",
    ])?;
    let focused: Vec<_> = focused.split('\t').collect();
    if focused.len() != 3
        || focused[0].parse::<u32>().ok() != Some(c.process.pid)
        || focused[1] != c.window
    {
        return Err("client focus changed during desktop action".into());
    }
    if identifier(focused[2], '%') && !panes.contains_key(focused[2]) {
        let pane = server.pane(focused[2])?;
        if pane.window != c.window || pane.session != c.session {
            return Err("focused pane moved during desktop action".into());
        }
        panes.insert(pane.id.clone(), pane);
    }
    let mut windows = vec![&p.window];
    if c.window != p.window {
        windows.push(&c.window);
    }
    for window in windows {
        let info = server.cmd(&[
            "display-message",
            "-p",
            "-t",
            window,
            "#{window_id}\t#{window_panes}\t#{pane_id}",
        ])?;
        let info: Vec<_> = info.split('\t').collect();
        if info.len() != 3 || info[0] != window || !identifier(info[2], '%') {
            return Err("native desktop window identity changed".into());
        }
        if !panes.contains_key(info[2]) {
            let pane = server.pane(info[2])?;
            if &pane.window != window {
                return Err("default pane moved during desktop action".into());
            }
            panes.insert(pane.id.clone(), pane);
        }
        let count = info[1].parse().map_err(|_| "invalid native pane count")?;
        let observed: Vec<_> = panes
            .values()
            .filter(|pane| &pane.window == window)
            .map(|pane| pane.protection)
            .collect();
        guard_protection(p.protection, &a.verb, &observed, count)?;
    }
    let now = server.client(&c.name)?;
    if now.process != c.process || now.session != c.session || now.window != c.window {
        return Err("client changed during desktop action".into());
    }
    server.witness(p).map(|_| ())
}

/// A tiled root prevents floating or minimizing the last app from removing the desktop window.
fn ensure_anchor(server: &Server, p: &Pane) -> Result<(), String> {
    if !p.floating
        && server
            .panes()?
            .iter()
            .filter(|q| q.window == p.window && !q.floating)
            .count()
            <= 1
    {
        let id = server.cmd(&[
            "new-pane",
            "-L",
            "-E",
            "-d",
            "-P",
            "-F",
            "#{pane_id}",
            "-t",
            &p.id,
            "-l",
            "1",
        ])?;
        if !identifier(&id, '%') {
            return Err("invalid desktop canvas identity".into());
        }
        server.set(&id, CANVAS, "1")?;
    }
    Ok(())
}
fn float_rect(server: &Server, c: &Client, p: &Pane, rect: Rect) -> Result<(), String> {
    server.require_float()?;
    let rect = rect.bounded(p.width, p.height, 2, 2)?;
    if p.zoomed {
        server.cmd(&["resize-pane", "-Z", "-t", &p.id])?;
    }
    ensure_anchor(server, &server.witness(p)?)?;
    server.cmd(&[
        "float-pane",
        "-d",
        "-t",
        &p.id,
        "-x",
        &rect.w.to_string(),
        "-y",
        &rect.h.to_string(),
        "-X",
        &rect.x.to_string(),
        "-Y",
        &rect.y.to_string(),
    ])?;
    let after = server.witness(p)?;
    if !after.floating || after.rect != rect || after.session != c.session {
        return Err("float-pane did not apply the witnessed geometry".into());
    }
    server.cmd(&["select-pane", "-t", &p.id])?;
    Ok(())
}
fn focus(server: &Server, c: &Client, p: &Pane) -> Result<(), String> {
    if p.session != c.session {
        return Err("focus cannot switch an unrelated session".into());
    }
    server.cmd(&["select-window", "-t", &p.window])?;
    server.cmd(&["select-pane", "-t", &p.id])?;
    server.witness(p).map(|_| ())
}
fn minimize(
    server: &Server,
    c: &Client,
    p: &Pane,
    settings: &Settings,
    mut r: Record,
) -> Result<(), String> {
    if r.minimized {
        return Ok(());
    }
    server.require_float()?; // The corresponding restore must be supported before hiding an app.
    if p.zoomed {
        server.cmd(&["resize-pane", "-Z", "-t", &p.id])?;
    }
    let p = server.witness(p)?;
    r.rect = p.rect;
    r.origin_session = c.session.clone();
    r.origin_window = p.window.clone();
    let name = format!(
        "{}desktop-{}",
        settings.prefix,
        p.id.trim_start_matches('%')
    );
    if server
        .cmd(&["has-session", "-t", &format!("={name}")])
        .is_ok()
    {
        return Err("minimize store already exists; refusing to reuse foreign state".into());
    }
    ensure_anchor(server, &p)?;
    // The owned wait client is removed after the original app has moved successfully.
    let anchor = server.cmd(&[
        "new-session",
        "-d",
        "-s",
        &name,
        "-x",
        &p.width.to_string(),
        "-y",
        &p.height.to_string(),
        "-P",
        "-F",
        "#{pane_id}",
        "--",
        server
            .binary
            .to_str()
            .ok_or("invalid desktop binary path")?,
        "-S",
        server
            .socket
            .to_str()
            .ok_or("invalid desktop socket path")?,
        "wait-for",
        &name,
    ])?;
    if !identifier(&anchor, '%') {
        return Err("invalid minimize anchor".into());
    }
    let anchor_pane = server.pane(&anchor)?;
    server.cmd(&[
        "set-option",
        "-t",
        &format!("={name}:"),
        "destroy-unattached",
        "off",
    ])?;
    r.minimized = true;
    r.store = Some(name.clone());
    r.save(server)?;
    if let Err(e) = server.cmd(&["break-pane", "-d", "-s", &p.id, "-t", &format!("={name}:")]) {
        r.minimized = false;
        r.store = None;
        let _ = r.save(server);
        if server.witness(&anchor_pane).is_ok() {
            let _ = server.cmd(&["kill-pane", "-t", &anchor]);
        }
        return Err(e);
    }
    let moved = server.witness(&p)?;
    if server.cmd(&["display-message", "-p", "-t", &moved.id, "#{session_name}"])? != name {
        return Err("minimize target session mismatch".into());
    }
    server.witness(&anchor_pane)?;
    server.cmd(&["kill-pane", "-t", &anchor])?;
    Ok(())
}
fn restore(
    server: &Server,
    c: &Client,
    p: &Pane,
    settings: &Settings,
    mut r: Record,
) -> Result<(), String> {
    if r.minimized {
        server.require_float()?;
        let expected = format!(
            "{}desktop-{}",
            settings.prefix,
            p.id.trim_start_matches('%')
        );
        if r.origin_session != c.session || r.store.as_deref() != Some(&expected) {
            return Err("minimized pane belongs to another desktop".into());
        }
        let store = server.cmd(&["display-message", "-p", "-t", &p.id, "#{session_name}"])?;
        if store != expected
            || server
                .panes()?
                .iter()
                .filter(|q| q.session == p.session)
                .count()
                != 1
        {
            return Err("minimize store is no longer exclusively owned".into());
        }
        let target = server
            .panes()?
            .into_iter()
            .filter(|q| {
                q.session == c.session
                    && q.window == r.origin_window
                    && !q.floating
                    && app_anchor(q.protection, q.canvas)
            })
            .max_by_key(|q| u64::from(q.rect.w) * u64::from(q.rect.h))
            .ok_or("original desktop window is no longer available")?;
        let rect = r
            .rect
            .bounded(target.width, target.height, settings.min_w, settings.min_h)?;
        server.cmd(&["join-pane", "-d", "-h", "-s", &p.id, "-t", &target.id])?;
        let moved = server.witness(p)?;
        r.minimized = false;
        r.store = None;
        r.save(server)?; // Recovery remains visible if float fails.
        float_rect(server, c, &moved, rect)?;
        focus(server, c, &server.witness(p)?)?;
        return Ok(());
    }
    if p.zoomed {
        server.cmd(&["resize-pane", "-Z", "-t", &p.id])?;
        server.witness(p)?;
        return Ok(());
    }
    if let Some(rect) = r.restore_rect.take() {
        let rect = rect.bounded(p.width, p.height, settings.min_w, settings.min_h)?;
        float_rect(server, c, p, rect)?;
        r.rect = rect;
        r.save(server)?;
    } else {
        focus(server, c, p)?;
    }
    Ok(())
}

fn callback(
    root: &Path,
    server: &Server,
    c: &Client,
    p: &Pane,
    verb: &str,
    nonce: Option<&str>,
) -> Result<String, String> {
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let (verb, section) = match verb {
        "context-snap" => ("context", Some("snap")),
        "context-corners" => ("context", Some("corners")),
        other => (other, None),
    };
    let mut args = vec![
        exe.to_string_lossy().into_owned(),
        "tmux-os".into(),
        "--root".into(),
        root.to_string_lossy().into_owned(),
        "desktop".into(),
        verb.into(),
        "--socket".into(),
        server.socket.to_string_lossy().into_owned(),
        "--client".into(),
        c.name.clone(),
        "--pane".into(),
        p.id.clone(),
        "--expected-pid".into(),
        p.process.pid.to_string(),
        "--expected-window".into(),
        p.window.clone(),
    ];
    if let Some(n) = nonce {
        args.extend(["--nonce".into(), n.into()]);
    }
    if let Some(section) = section {
        args.extend(["--section".into(), section.into()]);
    }
    let shell = args.iter().map(|s| quote(s)).collect::<Vec<_>>().join(" ");
    Ok(format!("run-shell -b {}", quote(&shell)))
}
fn context(
    root: &Path,
    server: &Server,
    c: &Client,
    p: &Pane,
    settings: &Settings,
    section: Option<&str>,
) -> Result<(), String> {
    let (columns, _) = settings.popup.size(
        SurfaceKind::Menu,
        u16::try_from(p.width).unwrap_or(u16::MAX),
        u16::try_from(p.height).unwrap_or(u16::MAX),
    );
    let title = format!(
        "{:^width$}",
        settings.label("context_title")?,
        width = usize::from(columns.saturating_sub(4))
    );
    let mut args = vec![
        "display-menu".to_owned(),
        "-M".into(),
        "-c".into(),
        c.name.clone(),
        "-t".into(),
        p.id.clone(),
        "-x".into(),
        "C".into(),
        "-y".into(),
        "C".into(),
        "-T".into(),
        title,
    ];
    let minimized = record(server, p)?.is_some_and(|r| r.minimized);
    let rows: &[(&str, &str, &str)] = if minimized {
        &[("restore", "r", "restore"), ("close", "x", "close")]
    } else if section == Some("snap") {
        &[
            ("snap_left", "Left", "snap-left"),
            ("snap_right", "Right", "snap-right"),
            ("snap_top", "Up", "snap-top"),
            ("snap_bottom", "Down", "snap-bottom"),
            ("corners", "c", "context-corners"),
            ("back", "Escape", "context"),
        ]
    } else if section == Some("corners") {
        &[
            ("snap_top_left", "1", "snap-top-left"),
            ("snap_top_right", "2", "snap-top-right"),
            ("snap_bottom_left", "3", "snap-bottom-left"),
            ("snap_bottom_right", "4", "snap-bottom-right"),
            ("back", "Escape", "context-snap"),
        ]
    } else {
        &[
            ("center", "c", "center"),
            ("maximize", "m", "maximize"),
            ("restore", "r", "restore"),
            ("snap", "s", "context-snap"),
            ("tile", "t", "tile"),
            ("float", "f", "float"),
            ("minimize", "n", "minimize"),
            ("close", "x", "close"),
        ]
    };
    for (label, key, verb) in rows {
        args.extend([
            settings.label(label)?.into(),
            (*key).into(),
            callback(root, server, c, p, verb, None)?,
        ]);
    }
    server.cmd(&args.iter().map(String::as_str).collect::<Vec<_>>())?;
    Ok(())
}
#[derive(Serialize, Deserialize)]
struct CloseChallenge {
    identity: Identity,
    process: Process,
    client: Process,
    session: String,
    pane: String,
    window: String,
    nonce: String,
}
fn close(
    root: &Path,
    server: &Server,
    c: &Client,
    p: &Pane,
    settings: &Settings,
    a: &Args,
) -> Result<(), String> {
    if a.verb == "close-confirm" {
        let challenge: CloseChallenge = serde_json::from_str(&server.option(&p.id, CLOSE)?)
            .map_err(|_| "no valid close confirmation is pending")?;
        if challenge.identity != server.identity
            || challenge.process != p.process
            || challenge.client != c.process
            || challenge.session != c.session
            || challenge.pane != p.id
            || challenge.window != p.window
            || a.nonce.as_deref() != Some(&challenge.nonce)
        {
            return Err("stale close confirmation".into());
        }
        server.set(&p.id, CLOSE, "")?;
        server.witness(p)?;
        if p.session == c.session {
            ensure_anchor(server, p)?;
        }
        server.cmd(&["kill-pane", "-t", &p.id])?;
        return Ok(());
    }
    let mut random = [0u8; 16];
    fs::File::open("/dev/urandom")
        .and_then(|mut f| f.read_exact(&mut random))
        .map_err(|e| e.to_string())?;
    let nonce = random
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect::<String>();
    let challenge = CloseChallenge {
        identity: server.identity.clone(),
        process: p.process.clone(),
        client: c.process.clone(),
        session: c.session.clone(),
        pane: p.id.clone(),
        window: p.window.clone(),
        nonce: nonce.clone(),
    };
    server.set(
        &p.id,
        CLOSE,
        &serde_json::to_string(&challenge).map_err(|e| e.to_string())?,
    )?;
    server.cmd(&[
        "display-menu",
        "-M",
        "-c",
        &c.name,
        "-t",
        &p.id,
        "-x",
        "C",
        "-y",
        "C",
        "-T",
        settings.label("confirm_close")?,
        settings.label("cancel")?,
        "Escape",
        "",
        settings.label("close")?,
        "y",
        &callback(root, server, c, p, "close-confirm", Some(&nonce))?,
    ])?;
    Ok(())
}
#[derive(Serialize)]
struct DockWindow {
    pane: String,
    pid: u32,
    window: String,
    session: String,
    title: String,
    floating: bool,
    minimized: bool,
    active: bool,
    managed: bool,
}
fn dock_state(
    server: &Server,
    c: &Client,
    settings: &Settings,
    format: Option<&str>,
) -> Result<String, String> {
    let mut result = Vec::new();
    // One RPC snapshots titles and ownership metadata. Dock refreshes never launch a process per pane.
    let snapshot = server.cmd(&["list-panes", "-a", "-F", "#{pane_id}\t#{pane_pid}\t#{pane_tty}\t#{session_id}\t#{window_id}\t#{pane_floating_flag}\t#{pane_active}\t#{@mios-desktop-canvas}\t#{@mios-workspace-head}\t#{@mios-mcp-owner}\t#{@mios-workspace-worker}\t#{E:@mios-managed-pane}\t#{@mios-desktop-window}\t#{pane_title}"])?;
    for row in snapshot.lines() {
        let f: Vec<&str> = row.splitn(14, '\t').collect();
        if f.len() != 14
            || !identifier(f[0], '%')
            || !identifier(f[3], '$')
            || !identifier(f[4], '@')
            || f[7] == "1"
        {
            continue;
        }
        let saved = serde_json::from_str::<Record>(f[12]).ok();
        let candidate = saved
            .as_ref()
            .is_some_and(|r| r.minimized && r.origin_session == c.session);
        if f[3] != c.session && !candidate {
            continue;
        }
        let Ok(pid) = f[1].parse::<u32>() else {
            continue;
        };
        let Ok(live) = process(pid) else {
            continue;
        };
        let minimized = saved.as_ref().is_some_and(|r| {
            candidate
                && r.version == 1
                && r.identity == server.identity
                && r.pane == f[0]
                && r.process == live
                && r.tty == f[2]
        });
        if f[3] != c.session && !minimized {
            continue;
        }
        result.push(DockWindow {
            pane: f[0].into(),
            pid,
            window: f[4].into(),
            session: f[3].into(),
            title: safe_label(f[13]),
            floating: f[5] == "1",
            minimized,
            active: f[6] == "1" && f[4] == c.window && !minimized,
            managed: f[8..12].iter().any(|v| !v.is_empty() && *v != "0"),
        });
    }
    if format == Some("tmux") {
        let mut dock = format!(
            "#[range=user|apps] {} #[norange]",
            tmux_label(settings.label("apps")?)
        );
        for p in result {
            // tmux user ranges have a 15-byte limit. A u32 pane ID fits p:%4294967295.
            let range = format!("p:{}", p.pane);
            if range.len() > 15 {
                continue;
            }
            let marker = if p.minimized {
                "_"
            } else if p.active {
                "+"
            } else {
                ""
            };
            let title = tmux_label(
                &p.title
                    .chars()
                    .take(settings.dock_columns as usize)
                    .collect::<String>(),
            );
            dock.push_str(&format!(" #[range=user|{range}]{marker}{title}#[norange]"));
        }
        Ok(dock)
    } else {
        serde_json::to_string(&result).map_err(|e| e.to_string())
    }
}

fn client_ssh(client: &Client) -> Result<bool, String> {
    if process(client.process.pid)? != client.process {
        return Err("drawer client process changed".into());
    }
    let environment = fs::read(format!("/proc/{}/environ", client.process.pid))
        .map_err(|e| format!("drawer client environment: {e}"))?;
    let ssh = environment.split(|b| *b == 0).any(|entry| {
        entry
            .strip_prefix(b"SSH_CONNECTION=")
            .or_else(|| entry.strip_prefix(b"SSH_CLIENT="))
            .is_some_and(|value| !value.is_empty())
    });
    if process(client.process.pid)? != client.process {
        return Err("drawer client process changed".into());
    }
    Ok(ssh)
}

fn apps(root: &Path, merged: &Value, server: &Server, client: &Client) -> Result<(), String> {
    let info = server.cmd(&[
        "display-message",
        "-p",
        "-c",
        &client.name,
        "-t",
        &client.window,
        "#{pane_id}\t#{client_height}\t#{client_termname}",
    ])?;
    let fields: Vec<&str> = info.split('\t').collect();
    if fields.len() != 3 || !identifier(fields[0], '%') {
        return Err("drawer client geometry is unavailable".into());
    }
    let pane = server.pane(fields[0])?;
    if pane.session != client.session || pane.window != client.window {
        return Err("drawer pane is outside the explicit client window".into());
    }
    let now = server.client(&client.name)?;
    if now.process != client.process || now.session != client.session || now.window != client.window
    {
        return Err("drawer client changed".into());
    }
    let console = mios_unit_gen::tmux_os::console_client(merged, fields[2], client_ssh(client)?)?;
    let enabled = match merged
        .get("keybindings")
        .and_then(|v| v.get("tmux"))
        .and_then(|v| v.get("launcher"))
    {
        None => false,
        Some(launcher) => launcher
            .get("enabled")
            .and_then(Value::as_bool)
            .ok_or("keybindings.tmux.launcher.enabled must be boolean")?,
    };
    if enabled {
        let executable = std::env::current_exe()
            .map_err(|e| e.to_string())?
            .with_file_name("miosd");
        let session = client
            .session
            .strip_prefix('$')
            .filter(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
            .ok_or("invalid drawer session identity")?;
        let socket = server.socket.to_str().ok_or("invalid drawer socket path")?;
        let mut command = Command::new(executable);
        // The application retains explicit resolver layers and desktop services; its own tmux RPCs are minimized.
        command
            .args(["launcher", "open", "--root"])
            .arg(root)
            .args([
                "--client",
                &client.name,
                "--console",
                if console { "1" } else { "0" },
                "--start",
                "apps",
            ])
            .env(
                "TMUX",
                format!("{socket},{},{session}", server.identity.server.pid),
            )
            .env("TMUX_PANE", &pane.id);
        server.witness(&pane)?;
        let status = command
            .status()
            .map_err(|e| format!("MiOS Apps launcher: {e}"))?;
        if !status.success() {
            return Err(format!("MiOS Apps launcher failed: {status}"));
        }
        return Ok(());
    }
    let rows = fields[1]
        .parse::<u32>()
        .map_err(|_| "invalid drawer height")?;
    let menu = crate::tmux_os::menu_argv(
        merged,
        console,
        rows,
        None,
        mios_resolver::terminal_tuis::is_installed,
    )?
    .ok_or("the Apps drawer does not fit this client")?;
    let mut args = vec![
        menu[0].clone(),
        "-c".into(),
        client.name.clone(),
        "-t".into(),
        fields[0].into(),
    ];
    args.extend(menu.into_iter().skip(1));
    server.cmd(&args.iter().map(String::as_str).collect::<Vec<_>>())?;
    Ok(())
}

/// The semantic desktop actions select catalog IDs, leaving executable arguments and launch geometry in SSOT.
fn desktop_app(verb: &str) -> Option<&'static str> {
    match verb {
        "new" => Some("terminal-shell"),
        "monitor" => Some("btop"),
        _ => None,
    }
}

fn launch_app(
    root: &Path,
    merged: &Value,
    server: &Server,
    client: &Client,
    pane: &Pane,
    app: &str,
) -> Result<(), String> {
    server.require_float()?;
    let term = server.cmd(&[
        "display-message",
        "-p",
        "-c",
        &client.name,
        "-t",
        &pane.id,
        "#{client_termname}",
    ])?;
    let console = mios_unit_gen::tmux_os::console_client(merged, &term, client_ssh(client)?)?;
    let session = client
        .session
        .strip_prefix('$')
        .filter(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
        .ok_or("invalid application session identity")?;
    let socket = server
        .socket
        .to_str()
        .ok_or("invalid application socket path")?;
    let mut command = Command::new(std::env::current_exe().map_err(|e| e.to_string())?);
    // Preserve application services and explicit resolver layers for the existing SSOT application executor.
    command
        .args(["tmux-os", "--root"])
        .arg(root)
        .args([
            "app",
            app,
            "--client",
            &client.name,
            "--console",
            if console { "1" } else { "0" },
            "--mode",
            "float",
        ])
        .env(
            "TMUX",
            format!("{socket},{},{session}", server.identity.server.pid),
        )
        .env("TMUX_PANE", &pane.id);
    let now = server.client(&client.name)?;
    if now.process != client.process || now.session != client.session || now.window != client.window
    {
        return Err("application client changed before launch".into());
    }
    let current = server.witness(pane)?;
    if current.session != client.session || current.window != client.window {
        return Err("application source pane moved before launch".into());
    }
    let status = command
        .status()
        .map_err(|e| format!("MiOS desktop application: {e}"))?;
    if !status.success() {
        return Err(format!("MiOS desktop application failed: {status}"));
    }
    Ok(())
}

/// Called only with an explicit tmux socket and verified client. Never restarts an app or server.
pub fn run(root: &Path, args: &[String]) -> Result<u8, String> {
    let mut a = parse(args)?;
    let merged = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let settings = Settings::read(&merged)?;
    let server = Server::open(&a, &settings)?;
    let client = server.client(&a.client)?;
    if server.cmd(&[
        "show-options",
        "-wqv",
        "-t",
        &client.window,
        "@mios-desktop-enabled",
    ])? != "1"
    {
        return Err("the explicit client window is not an enabled MiOS desktop".into());
    }
    if a.verb == "dock-state" {
        println!(
            "{}",
            dock_state(&server, &client, &settings, a.format.as_deref())?
        );
        return Ok(0);
    }
    if a.verb == "dock-click" {
        match a
            .item
            .as_deref()
            .ok_or("dock-click requires --item apps|p:%ID")?
        {
            "apps" => {
                apps(root, &merged, &server, &client)?;
                return Ok(0);
            }
            item => {
                a.pane = Some(item.strip_prefix("p:").ok_or("invalid dock item")?.into());
                a.verb = "focus".into();
            }
        }
    }
    let pane = server.pane(
        a.pane
            .as_deref()
            .ok_or("desktop action requires --pane %ID")?,
    )?;
    let hidden = matches!(
        a.verb.as_str(),
        "restore" | "focus" | "context" | "close" | "close-confirm"
    );
    let app = desktop_app(&a.verb);
    guard(&server, &client, &pane, &a, hidden)?;
    if let Some(app) = app {
        launch_app(root, &merged, &server, &client, &pane, app)?;
        return Ok(0);
    }
    if a.verb == "toggle-float" {
        a.verb = if pane.floating { "tile" } else { "float" }.into();
    }
    let mut r = record(&server, &pane)?.unwrap_or_else(|| Record::new(&server, &pane));
    match a.verb.as_str() {
        "center" | "float" | "snap-left" | "snap-right" | "snap-top" | "snap-bottom"
        | "snap-top-left" | "snap-top-right" | "snap-bottom-left" | "snap-bottom-right" => {
            let rect = geometry(&a.verb, pane.width, pane.height, &settings)?;
            if r.restore_rect.is_none() {
                r.restore_rect = Some(pane.rect);
            }
            r.save(&server)?;
            float_rect(&server, &client, &pane, rect)?;
            r.rect = rect;
            r.save(&server)?;
        }
        "maximize" => {
            r.save(&server)?;
            server.cmd(&["resize-pane", "-Z", "-t", &pane.id])?;
            server.witness(&pane)?;
        }
        "restore" => restore(&server, &client, &pane, &settings, r)?,
        "minimize" => minimize(&server, &client, &pane, &settings, r)?,
        "focus" => {
            if r.minimized {
                restore(&server, &client, &pane, &settings, r)?;
            } else {
                focus(&server, &client, &pane)?;
            }
        }
        "title-click" => {
            let control = a
                .mouse_x
                .zip(a.mouse_y)
                .and_then(|(x, y)| title_control(pane.rect, x, y));
            match control {
                Some(TitleControl::Close) => close(root, &server, &client, &pane, &settings, &a)?,
                Some(TitleControl::Minimize) => minimize(&server, &client, &pane, &settings, r)?,
                Some(TitleControl::Maximize) => {
                    server.cmd(&["resize-pane", "-Z", "-t", &pane.id])?;
                    server.witness(&pane)?;
                }
                _ => {
                    focus(&server, &client, &pane)?;
                    if a.double {
                        server.cmd(&["resize-pane", "-Z", "-t", &pane.id])?;
                        server.witness(&pane)?;
                    }
                }
            }
        }
        "tile" => {
            if pane.floating {
                let target = server
                    .panes()?
                    .into_iter()
                    .filter(|p| {
                        p.window == pane.window
                            && p.id != pane.id
                            && !p.floating
                            && app_anchor(p.protection, p.canvas)
                    })
                    .max_by_key(|p| u64::from(p.rect.w) * u64::from(p.rect.h))
                    .ok_or("no safe tiled target")?;
                if pane.zoomed {
                    server.cmd(&["resize-pane", "-Z", "-t", &pane.id])?;
                }
                server.cmd(&["join-pane", "-d", "-h", "-s", &pane.id, "-t", &target.id])?;
                let after = server.witness(&pane)?;
                if after.floating || after.window != pane.window || after.session != pane.session {
                    return Err(
                        "join-pane did not tile the witnessed pane in its original window".into(),
                    );
                }
                focus(&server, &client, &server.pane(&pane.id)?)?;
            }
        }
        "context" => context(
            root,
            &server,
            &client,
            &pane,
            &settings,
            a.section.as_deref(),
        )?,
        "close" | "close-confirm" => close(root, &server, &client, &pane, &settings, &a)?,
        other => return Err(format!("unknown desktop action {other:?}")),
    }
    Ok(0)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn desktop_roles() -> Vec<PaneProtection> {
        (0..4)
            .map(|index| {
                let mut flags = [""; 4];
                flags[index] = "1";
                pane_protection("0", MANAGED_PANE, "1", &flags)
            })
            .collect()
    }

    #[test]
    fn desktop_roles_allow_catalog_launches_and_ordinary_app_actions() {
        let mut window = desktop_roles();
        assert_eq!(window, vec![PaneProtection::DesktopRole; 4]);
        for role in &window {
            for action in ["new", "monitor", "focus"] {
                assert!(guard_protection(*role, action, &window, 4).is_ok());
            }
        }
        let app = pane_protection("0", MANAGED_PANE, "0", &[""; 4]);
        assert_eq!(app, PaneProtection::Application);
        window.push(app);
        for action in [
            "new",
            "monitor",
            "center",
            "float",
            "tile",
            "focus",
            "maximize",
            "minimize",
            "restore",
            "snap-left",
            "close",
            "close-confirm",
        ] {
            assert!(guard_protection(app, action, &window, 5).is_ok());
        }
        assert!(app_anchor(app, false));
        assert!(app_anchor(PaneProtection::DesktopRole, true));
        assert!(!app_anchor(PaneProtection::DesktopRole, false));
    }

    #[test]
    fn protected_roles_reject_layout_and_close_without_misclassifying_agents() {
        let roles = desktop_roles();
        for role in &roles {
            for action in [
                "center",
                "float",
                "tile",
                "minimize",
                "maximize",
                "restore",
                "snap-left",
                "title-click",
                "close",
                "close-confirm",
            ] {
                assert!(guard_protection(*role, action, &roles, 4).is_err());
            }
        }
        // All three real agent flags are evaluated by the same expression
        // emitted into the generated bindings. A forged desktop flag loses.
        for flag in [
            "@mios-workspace-head",
            "@mios-mcp-owner",
            "@mios-workspace-worker",
        ] {
            assert!(AGENT_PANE.contains(flag));
            let agent = pane_protection("1", MANAGED_PANE, "1", &["1", "", "", ""]);
            assert_eq!(agent, PaneProtection::Managed);
            assert!(!app_anchor(agent, true));
            let mut window = roles.clone();
            window.push(agent);
            for action in ["new", "monitor", "focus", "center", "close"] {
                assert!(guard_protection(agent, action, &window, 5).is_err());
                assert!(guard_protection(PaneProtection::Application, action, &window, 5).is_err());
            }
        }
    }

    #[test]
    fn manual_unknown_and_inconsistent_managed_markers_stay_protected() {
        for expression in ["1", "manual-manager", "#{@operator-managed}"] {
            assert_eq!(
                pane_protection("0", expression, "1", &["1", "", "", ""]),
                PaneProtection::Managed
            );
        }
        assert_eq!(
            pane_protection("0", MANAGED_PANE, "1", &["", "", "", ""]),
            PaneProtection::Managed
        );
        assert_eq!(
            pane_protection("0", MANAGED_PANE, "0", &["1", "", "", ""]),
            PaneProtection::Managed
        );
        assert_eq!(
            pane_protection("0", MANAGED_PANE, "1", &["", "unexpected", "", ""]),
            PaneProtection::Managed
        );
    }

    #[test]
    fn omitted_floating_panes_cannot_hide_a_managed_workspace() {
        let roles = desktop_roles();
        assert!(guard_protection(roles[0], "new", &roles, 5).is_err());
        // guard() adds the directly witnessed app target even when the native
        // list omits that float. An additional unobserved float stays a refusal.
        let mut with_target = roles;
        with_target.push(PaneProtection::Application);
        assert!(guard_protection(PaneProtection::Application, "center", &with_target, 5).is_ok());
        assert!(guard_protection(PaneProtection::Application, "center", &with_target, 6).is_err());
        assert!(guard_protection(PaneProtection::Application, "center", &with_target, 4).is_err());
    }

    #[test]
    fn native_desktop_app_actions_select_only_existing_catalog_entries() {
        let merged: Value = include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap();
        let catalog = mios_resolver::terminal_tuis::catalog(&merged).unwrap();
        for verb in ["new", "monitor"] {
            let app = desktop_app(verb).unwrap();
            assert!(catalog.iter().any(|entry| entry.id == app && entry.vt_ok));
        }
        for verb in ["float", "toggle-float", "exec:sh", "new;kill-server"] {
            assert_eq!(desktop_app(verb), None);
        }
    }
    #[test]
    fn left_title_controls_match_native_border_origin_and_reject_body_and_old_right_side() {
        let rect = Rect {
            x: 20,
            y: 10,
            w: 94,
            h: 33,
        };
        for (start, control) in [
            (22, TitleControl::Close),
            (25, TitleControl::Minimize),
            (28, TitleControl::Maximize),
        ] {
            for x in start..start + 3 {
                assert_eq!(title_control(rect, x, 9), Some(control));
            }
        }
        for (x, y) in [
            (19, 9),
            (20, 9),
            (21, 9),
            (31, 9),
            (110, 9),
            (22, 10),
            (25, 11),
            (28, 8),
        ] {
            assert_eq!(title_control(rect, x, y), None);
        }
        assert_eq!(title_control(Rect { w: 11, ..rect }, 22, 9), None);
        assert_eq!(
            title_control(
                Rect {
                    x: u32::MAX,
                    ..rect
                },
                u32::MAX,
                9
            ),
            None
        );
        assert_eq!(title_control(Rect { y: 0, ..rect }, 22, u32::MAX), None);
    }
    fn settings() -> Settings {
        let v: Value = include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap();
        Settings {
            binary: "/usr/libexec/mios/mios-tmux".into(),
            min_w: 34,
            min_h: 10,
            margin: 2,
            dock_columns: 18,
            prefix: "mios-float-".into(),
            popup: PopupPolicy::from_value(&v).unwrap(),
            labels: BTreeMap::new(),
            command_timeout: Duration::from_millis(3000),
        }
    }
    #[test]
    fn launch_size_centers_and_snap_halves_partition_odd_canvas() {
        let s = settings();
        assert_eq!(
            geometry("center", 240, 67, &s).unwrap(),
            Rect {
                x: 73,
                y: 17,
                w: 94,
                h: 33
            }
        );
        let left = geometry("snap-left", 201, 61, &s).unwrap();
        let right = geometry("snap-right", 201, 61, &s).unwrap();
        assert_eq!(left.x + left.w, right.x);
        assert_eq!(right.x + right.w, 199);
        assert_eq!(left.h, 57);
    }
    #[test]
    fn geometry_never_exceeds_viewport_and_rejects_unusable_snaps() {
        let s = settings();
        for w in [20, 34, 60, 80, 201, 240] {
            for h in [8, 10, 20, 33, 61, 67] {
                for action in [
                    "center",
                    "snap-left",
                    "snap-right",
                    "snap-top",
                    "snap-bottom",
                    "snap-top-left",
                    "snap-top-right",
                    "snap-bottom-left",
                    "snap-bottom-right",
                ] {
                    if let Ok(r) = geometry(action, w, h, &s) {
                        assert!(
                            r.x + r.w <= w && r.y + r.h <= h && r.w >= s.min_w && r.h >= s.min_h
                        );
                    }
                }
            }
        }
        assert!(geometry("snap-top-left", 60, 20, &s).is_err());
        assert!(geometry("center", 20, 8, &s).is_err());
    }
    #[test]
    fn explicit_targets_and_callback_arguments_reject_ambiguity() {
        let args = |s: &[&str]| s.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert!(parse(&args(&[
            "center",
            "--client",
            "/dev/pts/3",
            "--socket",
            "/tmp/a",
            "--pane",
            "%4"
        ]))
        .is_ok());
        for bad in ["%4:2", "%4;kill-server", "4", "%"] {
            assert!(parse(&args(&[
                "center",
                "--client",
                "/dev/pts/3",
                "--socket",
                "/tmp/a",
                "--pane",
                bad
            ]))
            .is_err());
        }
        assert!(parse(&args(&[
            "center",
            "--client",
            "../client",
            "--socket",
            "/tmp/a"
        ]))
        .is_err());
        assert!(parse(&args(&[
            "center", "--client", "client-2", "--socket", "relative"
        ]))
        .is_err());
        assert_eq!(quote("a'b"), "'a'\\''b'");
        assert_eq!(tmux_label("x#[bad]\n\u{1b}y"), "x##[bad]y");
    }
    #[test]
    fn restored_geometry_clamps_after_viewport_shrinks() {
        assert_eq!(
            Rect {
                x: 180,
                y: 50,
                w: 94,
                h: 33
            }
            .bounded(80, 25, 34, 10)
            .unwrap(),
            Rect {
                x: 0,
                y: 0,
                w: 80,
                h: 25
            }
        );
        assert!(Rect {
            x: 0,
            y: 0,
            w: 10,
            h: 5
        }
        .bounded(20, 8, 34, 10)
        .is_err());
    }
}
