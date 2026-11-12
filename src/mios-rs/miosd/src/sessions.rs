// AI-hint: miosd sessions: global tmux session selector, the default entry of every interactive MiOS login (local and tailnet nodes), with the shared Apps and TUIs launcher section; keyboard, controller vocabulary and mouse driven, Linux-console safe.
// AI-related: usr/share/mios/mios.toml, usr/libexec/mios/mios-terminal, tools/native/mios-resolver/src/terminal_tuis.rs
//! Global tmux session selector (`miosd sessions`): the default entry of every interactive
//! MiOS login. Lists [keybindings].socket_name sessions on this node and on tailnet MiOS
//! nodes ([terminal.sessions]), starts new bare Tmux-OS sessions, opens the MiOS AI workspace
//! and the TUIs of [[terminal.tuis]] in Tmux-OS. The complete native desktop catalogue and\n//! freshly witnessed local/registered execution targets are searchable from the first screen.

use std::io::Read;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Output, Stdio};
use std::sync::mpsc;
use std::sync::OnceLock;
use std::time::{Duration, Instant};

use ratatui::crossterm::event::{
    self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyEventKind, MouseButton,
    MouseEventKind,
};
use ratatui::crossterm::execute;
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Borders, List, ListItem, ListState, Paragraph};
use ratatui::Frame;
use toml::Value;

const TERMINAL: &str = "/usr/libexec/mios/mios-terminal";
const FORMAT: &str = "#{session_name}\t#{session_windows}\t#{session_attached}";

// Bounds on remote discovery. The selector runs with the terminal in raw mode, so no peer
// query may block it: [terminal.sessions] has no dedicated overall-deadline key, so the
// deadline for the whole tailnet sweep is ssh_timeout_s (the SSOT connect timeout) plus
// PEER_DEADLINE_GRACE for the remote `tmux list-sessions` round trip. Every ssh child still
// running at the deadline is killed (its whole process group) and reported as timed out.
const PEER_DEADLINE_GRACE: Duration = Duration::from_secs(2);
/// `tailscale status --json` is local IPC; it gets this long (within the overall deadline).
const TAILSCALE_DEADLINE: Duration = Duration::from_secs(2);
/// Local `tmux list-sessions` bound (a wedged local tmux server must not freeze the selector).
const LOCAL_DEADLINE: Duration = Duration::from_secs(2);
/// ssh keepalive: a peer that stops answering mid-session is dropped after
/// SERVER_ALIVE_INTERVAL_S * SERVER_ALIVE_COUNT_MAX seconds instead of TCP's minutes.
const SERVER_ALIVE_INTERVAL_S: u32 = 1;
const SERVER_ALIVE_COUNT_MAX: u32 = 2;

#[derive(Clone)]
struct Config {
    socket: String,
    socket_root: String,
    default_session: String,
    node_os: Vec<String>,
    node_glob: String,
    ssh_user: String,
    ssh_port: i64,
    ssh_timeout: i64,
    palette: [Color; 5],
    /// Session-name prefixes no picker lists (float shells, agent worker parking).
    hidden: Vec<String>,
    /// TUIs installed on this node that this terminal can show ([[terminal.tuis]], console-filtered).
    apps: Vec<App>,
    /// This terminal is a Linux console: named colours, plain borders, ASCII glyphs, argv_vt TUIs.
    console: bool,
    root: PathBuf,
    menu: MenuConfig,
    discovery_timeout_ms: u64,
}

#[derive(Clone, serde::Deserialize)]
struct MenuConfig {
    title: String,
    apps_title: String,
    systems_title: String,
    #[serde(default)]
    tabs: Option<[String; 3]>,
    inventory_program: String,
    launch_program: String,
    probe_program: String,
}

#[derive(Clone, Debug, PartialEq)]
enum MenuView {
    All,
    Apps,
    Systems,
}

#[derive(Clone, Debug, serde::Deserialize, serde::Serialize)]
struct GlobalApp {
    id: String,
    name: String,
    kind: String,
    available: bool,
    #[serde(default)]
    reason: String,
}

#[derive(Clone, Debug, serde::Serialize)]
struct DesktopSystem {
    selector: String,
    label: String,
    ready: bool,
    remote: bool,
    operator: bool,
    error: String,
}

#[derive(Clone, Default, serde::Serialize)]
struct MenuDiscovery {
    apps: Vec<GlobalApp>,
    systems: Vec<DesktopSystem>,
    errors: Vec<String>,
}

#[derive(Clone, Debug, PartialEq, serde::Serialize)]
pub struct App {
    id: String,
    label: String,
    section: String,
    mode: String,
}

#[derive(Clone, Debug, PartialEq, serde::Serialize)]
pub struct Session {
    name: String,
    windows: u32,
    attached: bool,
}

#[derive(Clone, serde::Serialize)]
pub struct Node {
    name: String,
    address: String,
    local: bool,
    online: bool,
    error: Option<String>,
    sessions: Vec<Session>,
}

#[derive(Clone, Debug, PartialEq, serde::Serialize)]
pub struct NestedSession {
    pub seat: String,
    pub user: String,
    pub target: String,
    pub tmux_socket: String,
    pub tmux_root: Option<String>,
    pub sessions: Vec<Session>,
}

#[derive(Clone, Debug, PartialEq)]
enum Choice {
    View(MenuView),
    SelectSystem(String),
    GlobalLaunch {
        name: String,
        session: String,
    },
    Notice(String),
    Attach {
        node: usize,
        session: String,
    },
    AttachNested {
        socket: String,
        session: String,
        tmux_root: Option<String>,
    },
    New {
        node: usize,
    },
    NewNested {
        socket: String,
        tmux_root: Option<String>,
    },
    /// The MiOS AI workspace ([keybindings].terminal_session) when it is not running yet.
    Workspace,
    App {
        id: String,
    },
    Shell,
}

/// Glyphs that the Linux console font cannot draw get ASCII stand-ins there.
struct Glyphs {
    sep: &'static str,
    more: &'static str,
    pick: &'static str,
    help: &'static str,
}

const UNICODE: Glyphs = Glyphs {
    sep: "  \u{b7}  ",
    more: "\u{2026}",
    pick: "\u{203a} ",
    help: "\u{2191}\u{2193} PgUp PgDn select \u{b7} Enter open \u{b7} / search \u{b7} e apps \u{b7} Tab/n new \u{b7} S-Tab section \u{b7} r refresh \u{b7} s shell",
};
const ASCII: Glyphs = Glyphs {
    sep: "  -  ",
    more: "...",
    pick: "> ",
    help: "Up/Down PgUp/PgDn select - Enter open - / search - e apps - Tab/n new - S-Tab section - r refresh - s shell",
};

fn text<'a>(v: &'a Value, path: &str) -> Result<&'a Value, String> {
    path.split('.')
        .try_fold(v, |cur, key| cur.get(key))
        .ok_or_else(|| format!("missing SSOT key {path}"))
}

fn string(v: &Value, path: &str) -> Result<String, String> {
    text(v, path)?
        .as_str()
        .map(str::to_owned)
        .ok_or_else(|| format!("{path} must be a string"))
}

fn integer(v: &Value, path: &str) -> Result<i64, String> {
    text(v, path)?
        .as_integer()
        .ok_or_else(|| format!("{path} must be an integer"))
}

pub(crate) fn color(hex: &str) -> Color {
    let h = hex.trim_start_matches('#');
    let byte = |i: usize| u8::from_str_radix(h.get(i..i + 2).unwrap_or("00"), 16).unwrap_or(0);
    Color::Rgb(byte(0), byte(2), byte(4))
}

fn config(root: &Path) -> Result<Config, String> {
    let v = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let ssh = ["SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"]
        .iter()
        .any(|k| std::env::var(k).is_ok_and(|v| !v.is_empty()));
    let console = mios_unit_gen::tmux_os::console_client(
        &v,
        &std::env::var("TERM").unwrap_or_default(),
        ssh,
    )?;
    let launcher = mios_resolver::terminal_tuis::launcher(&v)?;
    let catalog = mios_resolver::terminal_tuis::catalog(&v)?;
    let apps = if launcher.enabled {
        mios_resolver::terminal_tuis::visible(
            &catalog,
            console,
            mios_resolver::terminal_tuis::is_installed,
        )
        .into_iter()
        .map(|t| App {
            id: t.id.clone(),
            label: t.label.clone(),
            section: t.section.clone(),
            mode: t.mode.as_str().into(),
        })
        .collect()
    } else {
        Vec::new()
    };
    let node_os = text(&v, "terminal.sessions.node_os")?
        .as_array()
        .ok_or("terminal.sessions.node_os must be an array")?
        .iter()
        .filter_map(|o| o.as_str().map(str::to_owned))
        .collect();
    let user = string(&v, "terminal.sessions.ssh_user")?;
    let menu: MenuConfig = text(&v, "terminal.sessions.menu")?
        .clone()
        .try_into()
        .map_err(|e| format!("invalid terminal.sessions.menu: {e}"))?;
    if [
        &menu.title,
        &menu.apps_title,
        &menu.systems_title,
        &menu.inventory_program,
        &menu.launch_program,
        &menu.probe_program,
    ]
    .iter()
    .any(|v| v.trim().is_empty() || v.chars().any(char::is_control))
    {
        return Err("terminal.sessions.menu fields must be nonempty printable strings".into());
    }
    if menu.tabs.as_ref().is_some_and(|tabs| {
        tabs.iter()
            .any(|label| label.trim().is_empty() || label.chars().any(char::is_control))
    }) {
        return Err(
            "terminal.sessions.menu.tabs must contain three nonempty printable labels".into(),
        );
    }
    Ok(Config {
        root: root.to_path_buf(),
        discovery_timeout_ms: integer(&v, "desktop.launch.overall_timeout_ms")?.clamp(1000, 60000)
            as u64,
        menu,
        socket: string(&v, "keybindings.socket_name")?,
        socket_root: string(&v, "terminal.socket_root")?,
        default_session: string(&v, "keybindings.terminal_session")?,
        node_os,
        node_glob: string(&v, "terminal.sessions.node_host_glob")?,
        ssh_user: if user.is_empty() {
            std::env::var("USER").unwrap_or_default()
        } else {
            user
        },
        ssh_port: integer(&v, "terminal.sessions.ssh_port")?,
        ssh_timeout: integer(&v, "terminal.sessions.ssh_timeout_s")?,
        palette: if console {
            // The kernel console has 8/16 colours: RGB would collapse; mios-controls uses the same set.
            [
                Color::Black,
                Color::White,
                Color::Yellow,
                Color::Gray,
                Color::DarkGray,
            ]
        } else {
            [
                color(&string(&v, "colors.bg").unwrap_or_default()),
                color(&string(&v, "colors.fg").unwrap_or_default()),
                color(&string(&v, "colors.cursor").unwrap_or_default()),
                color(&string(&v, "colors.subtle").unwrap_or_default()),
                color(&string(&v, "colors.muted").unwrap_or_default()),
            ]
        },
        hidden: mios_unit_gen::tmux_os::hidden_session_prefixes(&v)?,
        apps,
        console,
    })
}

/// Internal sessions (float shells, worker parking) are never offered for attaching.
fn visible_sessions(c: &Config, sessions: Vec<Session>) -> Vec<Session> {
    sessions
        .into_iter()
        .filter(|s| !mios_unit_gen::tmux_os::is_hidden_session(&c.hidden, &s.name))
        .collect()
}

fn parse_sessions(out: &str) -> Vec<Session> {
    out.lines()
        .filter_map(|l| {
            let mut f = l.split('\t');
            Some(Session {
                name: f.next()?.to_owned(),
                windows: f.next()?.parse().ok()?,
                attached: f.next()? != "0",
            })
        })
        .collect()
}

fn glob(pattern: &str, name: &str) -> bool {
    match pattern.split_once('*') {
        None => pattern.eq_ignore_ascii_case(name),
        Some((pre, post)) => {
            let n = name.to_ascii_lowercase();
            n.len() >= pre.len() + post.len()
                && n.starts_with(&pre.to_ascii_lowercase())
                && n.ends_with(&post.to_ascii_lowercase())
        }
    }
}

/// Run `cmd` to completion or until `deadline`, whichever comes first. The child runs in its
/// own process group (so it never reads the selector's tty and never receives its job-control
/// signals); at the deadline the whole group is SIGKILLed and reaped, and Err("timed out").
pub(crate) fn run_bounded(cmd: &mut Command, deadline: Instant) -> Result<Output, String> {
    let mut child = cmd
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .process_group(0)
        .spawn()
        .map_err(|e| e.to_string())?;
    let drain = |pipe: Option<Box<dyn Read + Send>>| {
        std::thread::spawn(move || {
            let mut buf = Vec::new();
            if let Some(mut p) = pipe {
                let _ = p.read_to_end(&mut buf);
            }
            buf
        })
    };
    let out = drain(
        child
            .stdout
            .take()
            .map(|p| Box::new(p) as Box<dyn Read + Send>),
    );
    let err = drain(
        child
            .stderr
            .take()
            .map(|p| Box::new(p) as Box<dyn Read + Send>),
    );
    let status = loop {
        match child.try_wait() {
            Ok(Some(status)) => break Some(status),
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(20)),
            _ => break None,
        }
    };
    let Some(status) = status else {
        // SAFETY: kill(2) on the child's own process group (pgid == child pid via process_group(0)).
        unsafe { libc::kill(-(child.id() as libc::pid_t), libc::SIGKILL) };
        let _ = child.kill();
        let _ = child.wait();
        return Err("timed out".into());
    };
    Ok(Output {
        status,
        stdout: out.join().unwrap_or_default(),
        stderr: err.join().unwrap_or_default(),
    })
}

/// POSIX shell single-quoting: `'` becomes `'\''`, so any string is one literal word.
fn shell_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', r"'\''"))
}

/// Machine records retain UTF-8 and field separators even for a C-locale caller.
/// Human attach/new-session clients keep their own terminal encoding policy.
fn tmux_query_command() -> Command {
    let mut command = Command::new("tmux");
    command.arg("-u");
    command
}

fn local_sessions(c: &Config) -> Result<Vec<Session>, String> {
    let out = run_bounded(
        tmux_query_command()
            .env("TMUX_TMPDIR", &c.socket_root)
            .args(["-L", &c.socket, "list-sessions", "-F", FORMAT]),
        Instant::now() + LOCAL_DEADLINE,
    )
    .map_err(|e| format!("tmux: {e}"))?;
    // No server yet is an empty list, not an error.
    Ok(visible_sessions(
        c,
        parse_sessions(&String::from_utf8_lossy(&out.stdout)),
    ))
}

fn ssh_args(c: &Config, address: &str) -> Vec<String> {
    vec![
        "-o".into(),
        "BatchMode=yes".into(),
        "-o".into(),
        format!("ConnectTimeout={}", c.ssh_timeout),
        "-o".into(),
        format!("ServerAliveInterval={SERVER_ALIVE_INTERVAL_S}"),
        "-o".into(),
        format!("ServerAliveCountMax={SERVER_ALIVE_COUNT_MAX}"),
        "-p".into(),
        c.ssh_port.to_string(),
        format!("{}@{address}", c.ssh_user),
    ]
}

fn remote_sessions(c: &Config, address: &str, deadline: Instant) -> Result<Vec<Session>, String> {
    let remote = format!(
        "TMUX_TMPDIR={} tmux -u -L {} list-sessions -F {} 2>/dev/null; true",
        shell_quote(&c.socket_root),
        shell_quote(&c.socket),
        shell_quote(FORMAT)
    );
    let out = run_bounded(
        Command::new("ssh").args(ssh_args(c, address)).arg(remote),
        deadline,
    )
    .map_err(|e| format!("ssh: {e}"))?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        return Err(err
            .lines()
            .last()
            .unwrap_or("unreachable")
            .trim()
            .to_owned());
    }
    Ok(visible_sessions(
        c,
        parse_sessions(&String::from_utf8_lossy(&out.stdout)),
    ))
}

/// Deadline for one whole tailnet sweep (see PEER_DEADLINE_GRACE).
fn peer_deadline(c: &Config) -> Duration {
    Duration::from_secs(c.ssh_timeout.clamp(1, 60) as u64) + PEER_DEADLINE_GRACE
}

/// This node only (bounded; shown before any peer answers).
fn local_node(c: &Config) -> Node {
    let host = std::fs::read_to_string("/proc/sys/kernel/hostname")
        .unwrap_or_default()
        .trim()
        .to_owned();
    let (sessions, error) = match local_sessions(c) {
        Ok(s) => (s, None),
        Err(e) => (Vec::new(), Some(e)),
    };
    Node {
        name: host,
        address: String::new(),
        local: true,
        online: true,
        error,
        sessions,
    }
}

fn nested_sessions_dir() -> std::path::PathBuf {
    if let Ok(d) = std::env::var("MIOS_NESTED_SESSIONS_DIR") {
        std::path::PathBuf::from(d)
    } else if let Ok(rt) = std::env::var("XDG_RUNTIME_DIR") {
        std::path::PathBuf::from(rt).join("mios/nested-sessions")
    } else {
        std::path::PathBuf::from(format!("/run/user/{}", unsafe { libc::getuid() }))
            .join("mios/nested-sessions")
    }
}

fn launcher_pid_alive(entry: &serde_json::Value) -> bool {
    let Some(pid) = entry
        .get("launcher_pid")
        .and_then(|v| v.as_u64())
        .map(|p| p as u32)
    else {
        return false;
    };
    if pid == 0 {
        return false;
    }
    if let Some(expected_start) = entry.get("launcher_start").and_then(|v| v.as_u64()) {
        if let Ok(stat) = std::fs::read_to_string(format!("/proc/{pid}/stat")) {
            if let Some((_, rest)) = stat.rsplit_once(')') {
                let actual_start = rest
                    .split_whitespace()
                    .nth(19)
                    .and_then(|f| f.parse::<u64>().ok());
                return actual_start == Some(expected_start);
            }
        }
        false
    } else {
        Path::new(&format!("/proc/{pid}")).exists()
    }
}

fn discover_nested(c: &Config) -> Vec<NestedSession> {
    let dir = nested_sessions_dir();
    let entries = match std::fs::read_dir(&dir) {
        Ok(rd) => rd,
        Err(_) => return Vec::new(),
    };
    let mut out = Vec::new();
    for entry in entries.flatten() {
        let p = entry.path();
        if p.extension().and_then(|ext| ext.to_str()) != Some("json") {
            continue;
        }
        let data = match std::fs::read_to_string(&p) {
            Ok(s) => s,
            Err(_) => continue,
        };
        let val: serde_json::Value = match serde_json::from_str(&data) {
            Ok(v) => v,
            Err(_) => continue,
        };
        if !launcher_pid_alive(&val) {
            continue;
        }
        let seat = val
            .get("seat")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_owned();
        let user = val
            .get("user")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_owned();
        let target = val
            .get("target")
            .or_else(|| val.get("layout"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_owned();
        let tmux_socket = if let Some(sock) = val.get("tmux_socket").and_then(|v| v.as_str()) {
            sock.to_owned()
        } else if let Some(desc_path) = val.get("descriptor").and_then(|v| v.as_str()) {
            std::fs::read_to_string(desc_path)
                .ok()
                .and_then(|s| serde_json::from_str::<serde_json::Value>(&s).ok())
                .and_then(|d| {
                    d.get("tmux_socket")
                        .and_then(|v| v.as_str())
                        .map(str::to_owned)
                })
                .unwrap_or_else(|| {
                    if !seat.is_empty() {
                        format!("mios-nested-{seat}")
                    } else {
                        "mios-nested".to_owned()
                    }
                })
        } else if !seat.is_empty() {
            format!("mios-nested-{seat}")
        } else {
            "mios-nested".to_owned()
        };
        let tmux_root = val
            .get("runtime")
            .and_then(|v| v.as_str())
            .map(|r| format!("{r}/tmux"))
            .filter(|p| std::path::Path::new(p).exists())
            .or_else(|| {
                val.get("tmux_root")
                    .and_then(|v| v.as_str())
                    .map(str::to_owned)
            });

        let root = tmux_root.as_deref().unwrap_or(&c.socket_root);
        let mut cmd = tmux_query_command();
        cmd.env("TMUX_TMPDIR", root)
            .args(["-L", &tmux_socket, "list-sessions", "-F", FORMAT]);
        let sessions = match run_bounded(&mut cmd, Instant::now() + LOCAL_DEADLINE) {
            Ok(output) => {
                visible_sessions(c, parse_sessions(&String::from_utf8_lossy(&output.stdout)))
            }
            Err(_) => Vec::new(),
        };

        out.push(NestedSession {
            seat,
            user,
            target,
            tmux_socket,
            tmux_root,
            sessions,
        });
    }
    out.sort_by(|a, b| a.seat.cmp(&b.seat).then(a.tmux_socket.cmp(&b.tmux_socket)));
    out
}

/// This node first, then tailnet MiOS nodes queried in parallel (bounded by peer_deadline).
fn discover(c: &Config) -> Vec<Node> {
    let mut nodes = vec![local_node(c)];
    nodes.extend(remote_nodes(c, Instant::now() + peer_deadline(c)));
    nodes
}

/// Label a peer by its tailnet machine name (unique) rather than its OS hostname, which
/// repeats across machines (three peers all reporting "fedora").
fn peer_name(dns_name: &str, host_name: &str) -> String {
    dns_name
        .split('.')
        .next()
        .filter(|s| !s.is_empty())
        .unwrap_or(host_name)
        .to_owned()
}

/// Tailnet MiOS nodes, every query concurrent and finished (or killed) by `deadline`.
fn remote_nodes(c: &Config, deadline: Instant) -> Vec<Node> {
    let status = run_bounded(
        Command::new("tailscale").args(["status", "--json"]),
        deadline.min(Instant::now() + TAILSCALE_DEADLINE),
    );
    let peers: Vec<serde_json::Value> = status
        .ok()
        .and_then(|o| serde_json::from_slice::<serde_json::Value>(&o.stdout).ok())
        .and_then(|j| j.get("Peer").and_then(|p| p.as_object().cloned()))
        .map(|m| m.into_iter().map(|(_, p)| p).collect())
        .unwrap_or_default();
    let field =
        |p: &serde_json::Value, k: &str| p.get(k).and_then(|v| v.as_str()).unwrap_or("").to_owned();
    let mut remote: Vec<Node> = peers
        .iter()
        .filter(|p| {
            c.node_os.iter().any(|o| *o == field(p, "OS"))
                && glob(&c.node_glob, &field(p, "HostName"))
        })
        .map(|p| Node {
            name: peer_name(&field(p, "DNSName"), &field(p, "HostName")),
            address: field(p, "DNSName").trim_end_matches('.').to_owned(),
            local: false,
            online: p.get("Online").and_then(|v| v.as_bool()).unwrap_or(false),
            error: None,
            sessions: Vec::new(),
        })
        .collect();
    std::thread::scope(|s| {
        for node in remote.iter_mut().filter(|n| n.online) {
            s.spawn(move || match remote_sessions(c, &node.address, deadline) {
                Ok(list) => node.sessions = list,
                Err(e) => node.error = Some(e),
            });
        }
    });
    remote.sort_by(|a, b| b.online.cmp(&a.online).then(a.name.cmp(&b.name)));
    remote
}

/// Query the tailnet on a worker thread; the selector polls the receiver between frames.
fn spawn_remote(c: &Config) -> mpsc::Receiver<Vec<Node>> {
    let (tx, rx) = mpsc::channel();
    let c = c.clone();
    std::thread::spawn(move || {
        let _ = tx.send(remote_nodes(&c, Instant::now() + peer_deadline(&c)));
    });
    rx
}

fn menu_text(value: &str, console: bool) -> String {
    value
        .chars()
        .filter(|ch| !ch.is_control())
        .map(|ch| if console && !ch.is_ascii() { '?' } else { ch })
        .collect()
}

fn target_label(value: &serde_json::Value) -> Option<String> {
    let target = &value["execution_target"];
    let fields: Option<Vec<&str>> = ["id", "platform", "session_id", "backend"]
        .iter()
        .map(|key| {
            target[*key]
                .as_str()
                .filter(|v| !v.is_empty() && !v.chars().any(char::is_control))
        })
        .collect();
    let fields = fields?;
    Some(format!("{} / {} ({})", fields[1], fields[2], fields[3]))
}

fn menu_inventory(c: &Config) -> MenuDiscovery {
    let mut discovery = MenuDiscovery::default();
    let command = |mode: &str| {
        let until =
            Instant::now() + Duration::from_millis(c.discovery_timeout_ms) + PEER_DEADLINE_GRACE;
        let mut cmd = Command::new(&c.menu.inventory_program);
        cmd.args(["app-preflight", mode, "--root"])
            .arg(&c.root)
            .env_remove("MIOS_EXECUTION_SESSION");
        run_bounded(&mut cmd, until)
            .ok()
            .filter(|r| r.status.success())
            .and_then(|r| serde_json::from_slice::<serde_json::Value>(&r.stdout).ok())
            .filter(|j| j["ok"] == true)
    };
    if let Some(value) = command("--catalog") {
        if let Ok(apps) = serde_json::from_value::<Vec<GlobalApp>>(value["apps"].clone()) {
            discovery.apps = apps
                .into_iter()
                .filter(|a| {
                    !a.id.is_empty()
                        && !a.id.starts_with('-')
                        && !a.id.chars().any(char::is_control)
                })
                .collect();
        } else {
            discovery
                .errors
                .push("Application catalogue returned an invalid receipt".into());
        }
    } else {
        discovery
            .errors
            .push("Application catalogue unavailable; refresh to retry".into());
    }
    if let Some(value) = command("--sessions") {
        for row in value["sessions"].as_array().into_iter().flatten() {
            if let (Some(label), Some(selector)) =
                (target_label(row), row["execution_target"]["id"].as_str())
            {
                discovery.systems.push(DesktopSystem {
                    selector: selector.into(),
                    label,
                    ready: row["available"] == true && row["probed"] == true,
                    remote: false,
                    operator: row["scope"] == "operator",
                    error: String::new(),
                });
            }
        }
        for row in value["registered_endpoints"]
            .as_array()
            .into_iter()
            .flatten()
        {
            let Some(id) = row["registration_id"]
                .as_str()
                .filter(|id| !id.chars().any(char::is_control))
            else {
                continue;
            };
            discovery.systems.push(DesktopSystem {
                selector: id.into(),
                label: id.into(),
                ready: false,
                remote: true,
                operator: false,
                error: if row["endpoint"].as_str().is_some() {
                    "probe required".into()
                } else {
                    "unavailable: invalid registered endpoint".into()
                },
            });
        }
        for row in value["unavailable"].as_array().into_iter().flatten() {
            discovery.systems.push(DesktopSystem {
                selector: String::new(),
                label: row["session_id"].as_str().unwrap_or("Local desktop").into(),
                ready: false,
                remote: false,
                operator: false,
                error: "unavailable: native session probe failed".into(),
            });
        }
        if value["truncated"] == true {
            discovery
                .errors
                .push("System discovery incomplete; native safety bound reached".into());
        }
    } else {
        discovery
            .errors
            .push("Desktop system inventory unavailable; refresh to retry".into());
    }
    discovery
}

fn spawn_menu(c: &Config) -> mpsc::Receiver<MenuDiscovery> {
    let (tx, rx) = mpsc::channel();
    let c = c.clone();
    std::thread::spawn(move || {
        let _ = tx.send(menu_inventory(&c));
    });
    rx
}

type SystemProbeResult = Result<(String, String), String>;
struct PendingSystemProbe {
    selector: String,
    receiver: mpsc::Receiver<SystemProbeResult>,
}

fn probe_system(c: &Config, selector: &str) -> SystemProbeResult {
    let output = run_bounded(
        Command::new(&c.menu.probe_program)
            .args(["window-list", "--json"])
            .env("MIOS_EXECUTION_SESSION", selector),
        Instant::now() + peer_deadline(c),
    )?;
    if !output.status.success() {
        return Err("Session unavailable; executor did not confirm the requested target".into());
    }
    let receipt: serde_json::Value =
        serde_json::from_slice(&output.stdout).map_err(|_| "Invalid session probe receipt")?;
    if receipt["windows"].as_array().is_none()
        || ["ok", "success", "available"]
            .iter()
            .any(|k| receipt.get(*k).is_some_and(|v| v != true))
    {
        return Err("Session probe did not confirm availability".into());
    }
    let label = target_label(&receipt)
        .ok_or_else(|| "Session probe did not identify its execution target".to_owned())?;
    let selector = receipt["execution_target"]["id"]
        .as_str()
        .ok_or_else(|| "Session probe did not identify its execution target".to_owned())?;
    Ok((label, selector.to_owned()))
}

struct MenuContext<'a> {
    c: &'a Config,
    nodes: &'a [Node],
    querying: bool,
    nested: &'a [NestedSession],
    discovery: &'a MenuDiscovery,
    view: &'a MenuView,
    query: &'a str,
    selected: Option<&'a DesktopSystem>,
    g: &'a Glyphs,
}

fn menu_rows(context: MenuContext<'_>) -> Vec<(Option<Choice>, Line<'static>, Style)> {
    let MenuContext {
        c,
        nodes,
        querying,
        nested,
        discovery,
        view,
        query,
        selected,
        g,
    } = context;
    let bold = Style::default().add_modifier(Modifier::BOLD);
    let mut out = vec![
        (
            Some(Choice::View(MenuView::Apps)),
            Line::from(format!(
                "[ {} ]  {} local desktop / {} TUI",
                c.menu.apps_title,
                discovery.apps.len(),
                c.apps.len()
            )),
            bold,
        ),
        (
            Some(Choice::View(MenuView::Systems)),
            Line::from(format!(
                "[ {} ]  {} desktops / {} nodes",
                c.menu.systems_title,
                discovery.systems.len(),
                nodes.len()
            )),
            bold,
        ),
        (
            Some(Choice::View(MenuView::All)),
            Line::from("[ All apps and sessions ]"),
            bold,
        ),
    ];
    let mut body = Vec::new();
    if *view != MenuView::Apps {
        body = rows(nodes, querying, &[], nested, Some(&c.default_session), g);
        body.push((None, Line::from(c.menu.systems_title.clone()), bold));
        if discovery.systems.is_empty() {
            body.push((
                Some(Choice::Notice("No ready desktop systems discovered".into())),
                Line::from(
                    "    No ready desktop systems; configured guests are not assumed running",
                ),
                Style::default().add_modifier(Modifier::DIM),
            ));
        }
        for system in &discovery.systems {
            let status = if system.ready { "ready" } else { &system.error };
            let action = if system.ready || (system.remote && system.error == "probe required") {
                Choice::SelectSystem(system.selector.clone())
            } else {
                Choice::Notice(format!("{}: {status}", system.label))
            };
            body.push((
                Some(action),
                Line::from(format!(
                    "    {} [{}]",
                    menu_text(&system.label, c.console),
                    status
                )),
                Style::default(),
            ));
        }
    }
    if *view != MenuView::Systems {
        body.push((
            None,
            Line::from(format!("{} - local catalogue", c.menu.apps_title)),
            bold,
        ));
        for app in &discovery.apps {
            let remote_target = selected.is_some_and(|s| s.ready && s.remote);
            let choice = if app.available || remote_target {
                selected
                    .filter(|s| s.ready)
                    .map(|s| Choice::GlobalLaunch {
                        name: if s.remote {
                            app.name.clone()
                        } else {
                            app.id.clone()
                        },
                        session: s.selector.clone(),
                    })
                    .unwrap_or_else(|| {
                        Choice::Notice(format!(
                            "{}: select a ready desktop system to launch this app",
                            app.id
                        ))
                    })
            } else {
                Choice::Notice(format!("{}: {}", app.id, app.reason))
            };
            body.push((
                Some(choice),
                Line::from(format!(
                    "    {:<30} {}{}",
                    menu_text(&app.name, c.console),
                    app.kind,
                    if app.available {
                        ""
                    } else if remote_target {
                        " [not installed locally; resolve remotely]"
                    } else {
                        " [unavailable]"
                    }
                )),
                Style::default(),
            ));
        }
        body.push((None, Line::from("TUI apps - this terminal"), bold));
        for app in &c.apps {
            body.push((
                Some(Choice::App { id: app.id.clone() }),
                Line::from(format!(
                    "    {:<30} {} {}",
                    app.label, app.section, app.mode
                )),
                Style::default(),
            ));
        }
    }
    let query_lower = query.to_lowercase();
    let mut matches = 0;
    for row in body {
        if query.is_empty() || row.1.to_string().to_lowercase().contains(&query_lower) {
            matches += usize::from(row.0.is_some());
            out.push(row);
        }
    }
    if !query.is_empty() && *view != MenuView::Systems {
        if matches == 0 {
            out.push((
                None,
                Line::from("No catalogue match; request an app by name below"),
                Style::default(),
            ));
        }
        let choice = selected
            .filter(|s| s.ready)
            .map(|s| Choice::GlobalLaunch {
                name: query.into(),
                session: s.selector.clone(),
            })
            .unwrap_or_else(|| {
                Choice::Notice("Select a ready system before requesting an app".into())
            });
        out.push((
            Some(choice),
            Line::from(format!("Launch by name: {}", menu_text(query, c.console))),
            bold,
        ));
    }
    for error in &discovery.errors {
        out.push((
            Some(Choice::Notice(error.clone())),
            Line::from(error.clone()),
            Style::default().add_modifier(Modifier::DIM),
        ));
    }
    out
}

// ---- terminal restoration on every exit path -------------------------------------------
// ratatui::init installs a panic hook that restores the terminal; TermGuard's Drop covers
// early `?` returns; the signal handlers cover SIGINT/SIGTERM/SIGHUP/SIGQUIT (raw mode
// disables ISIG, but a parent, `kill` or a hung-up ssh/tty still delivers them).

static SAVED_TERMIOS: OnceLock<libc::termios> = OnceLock::new();
const RESTORE_SIGNALS: [libc::c_int; 4] =
    [libc::SIGINT, libc::SIGTERM, libc::SIGHUP, libc::SIGQUIT];
/// Leave the alternate screen, show the cursor (what ratatui::restore does, async-signal-safe).
const RESTORE_SEQ: &[u8] = b"\x1b[?1049l\x1b[?25h";

extern "C" fn restore_on_signal(sig: libc::c_int) {
    // Only async-signal-safe calls: tcsetattr, write, signal, raise.
    if let Some(t) = SAVED_TERMIOS.get() {
        // SAFETY: plain syscalls on fds 0/1 with a termios captured before raw mode.
        unsafe { libc::tcsetattr(0, libc::TCSANOW, t) };
    }
    unsafe {
        libc::write(1, RESTORE_SEQ.as_ptr().cast(), RESTORE_SEQ.len());
        libc::signal(sig, libc::SIG_DFL);
        libc::raise(sig);
    }
}

pub(crate) struct TermGuard {
    previous: Vec<(libc::c_int, libc::sigaction)>,
}

impl TermGuard {
    /// Capture cooked-mode termios and arm the signal handlers BEFORE entering raw mode.
    pub(crate) fn arm() -> Self {
        let mut t: libc::termios = unsafe { std::mem::zeroed() };
        // SAFETY: tcgetattr writes into a valid termios; failure (no tty) leaves SAVED unset.
        if unsafe { libc::tcgetattr(0, &mut t) } == 0 {
            let _ = SAVED_TERMIOS.set(t);
        }
        let mut previous = Vec::new();
        for sig in RESTORE_SIGNALS {
            // SAFETY: installing a handler that only performs async-signal-safe calls.
            unsafe {
                let mut new: libc::sigaction = std::mem::zeroed();
                new.sa_sigaction = restore_on_signal as *const () as usize;
                libc::sigemptyset(&mut new.sa_mask);
                let mut old: libc::sigaction = std::mem::zeroed();
                if libc::sigaction(sig, &new, &mut old) == 0 {
                    previous.push((sig, old));
                }
            }
        }
        TermGuard { previous }
    }
}

impl Drop for TermGuard {
    fn drop(&mut self) {
        // ratatui::restore leaves mouse reporting on; the exec'd tmux or shell must get a clean terminal.
        let _ = execute!(std::io::stdout(), DisableMouseCapture);
        ratatui::restore();
        for (sig, old) in &self.previous {
            // SAFETY: reinstating the disposition sigaction returned earlier.
            unsafe { libc::sigaction(*sig, old, std::ptr::null_mut()) };
        }
    }
}

fn rows(
    nodes: &[Node],
    querying: bool,
    apps: &[App],
    nested: &[NestedSession],
    workspace: Option<&str>,
    g: &Glyphs,
) -> Vec<(Option<Choice>, Line<'static>, Style)> {
    let mut out = Vec::new();
    for (i, n) in nodes.iter().enumerate() {
        let state = if n.local {
            "this node".to_owned()
        } else if !n.online {
            "offline".to_owned()
        } else if let Some(e) = &n.error {
            format!("unreachable: {e}")
        } else {
            n.address.clone()
        };
        out.push((
            None,
            Line::from(format!("{}{}{state}", n.name, g.sep)),
            Style::default().add_modifier(Modifier::BOLD),
        ));
        for s in &n.sessions {
            let mark = if s.attached { "attached" } else { "detached" };
            out.push((
                Some(Choice::Attach {
                    node: i,
                    session: s.name.clone(),
                }),
                Line::from(format!("    {}   {} window(s), {mark}", s.name, s.windows)),
                Style::default(),
            ));
        }
        if i == 0 {
            if let Some(name) = workspace.filter(|w| !n.sessions.iter().any(|s| s.name == *w)) {
                out.push((
                    Some(Choice::Workspace),
                    Line::from(format!("    + MiOS AI workspace ({name})")),
                    Style::default(),
                ));
            }
        }
        if n.online && n.error.is_none() {
            out.push((
                Some(Choice::New { node: i }),
                Line::from("    + new bare Tmux-OS session"),
                Style::default(),
            ));
        }
        // This node's TUIs follow its own block at once, so tailnet rows arriving later never move them.
        if i == 0 && !apps.is_empty() {
            out.push((
                None,
                Line::from(format!("TUI apps{}this node", g.sep)),
                Style::default().add_modifier(Modifier::BOLD),
            ));
            for a in apps {
                out.push((
                    Some(Choice::App { id: a.id.clone() }),
                    Line::from(format!("    {:<28} {} {}", a.label, a.section, a.mode)),
                    Style::default(),
                ));
            }
        }
    }
    if !nested.is_empty() {
        out.push((
            None,
            Line::from(format!("Nested sessions{}this node", g.sep)),
            Style::default().add_modifier(Modifier::BOLD),
        ));
        for n in nested {
            let label = if !n.seat.is_empty() {
                format!("seat {}", n.seat)
            } else {
                format!("socket {}", n.tmux_socket)
            };
            let mut meta = Vec::new();
            if !n.target.is_empty() {
                meta.push(n.target.clone());
            }
            if !n.user.is_empty() {
                meta.push(format!("user:{}", n.user));
            }
            let info = if meta.is_empty() {
                label
            } else {
                format!("{label} ({})", meta.join(", "))
            };
            out.push((
                None,
                Line::from(format!("    {info}")),
                Style::default().add_modifier(Modifier::DIM),
            ));
            for s in &n.sessions {
                let mark = if s.attached { "attached" } else { "detached" };
                out.push((
                    Some(Choice::AttachNested {
                        socket: n.tmux_socket.clone(),
                        session: s.name.clone(),
                        tmux_root: n.tmux_root.clone(),
                    }),
                    Line::from(format!(
                        "        {}   {} window(s), {mark}",
                        s.name, s.windows
                    )),
                    Style::default(),
                ));
            }
            out.push((
                Some(Choice::NewNested {
                    socket: n.tmux_socket.clone(),
                    tmux_root: n.tmux_root.clone(),
                }),
                Line::from("        + new bare Tmux-OS session"),
                Style::default(),
            ));
        }
    }
    if querying {
        out.push((
            None,
            Line::from(format!("tailnet nodes{}querying{}", g.sep, g.more)),
            Style::default().add_modifier(Modifier::DIM),
        ));
    }
    out.push((
        Some(Choice::Shell),
        Line::from("Plain shell (no tmux)"),
        Style::default(),
    ));
    out
}

/// Index of the selected row for the next frame: the remembered choice where it still is (rows may shift
/// when tailnet peers arrive), else the default session, else the first selectable row.
fn select_index(
    table: &[(Option<Choice>, Line<'static>, Style)],
    remembered: Option<&Choice>,
    default_session: &str,
) -> Option<usize> {
    remembered
        .and_then(|want| table.iter().position(|r| r.0.as_ref() == Some(want)))
        .or_else(|| table.iter().position(|r| matches!(&r.0, Some(Choice::Attach { session, .. }) if session == default_session)))
        .or_else(|| table.iter().position(|r| r.0.is_some()))
}

/// Keyboard/controller navigation over selectable rows: arrows, PgUp/PgDn, Home/End, BackTab (next section;
/// Tab is "new session" so the controller's West button reaches it, FINAL.md C-33), and `e` (first TUI app).
fn navigate(
    table: &[(Option<Choice>, Line<'static>, Style)],
    cur: usize,
    key: KeyCode,
    page: usize,
) -> usize {
    if table
        .get(cur)
        .is_some_and(|row| matches!(row.0, Some(Choice::View(_))))
    {
        match key {
            KeyCode::Left => return cur.saturating_sub(1),
            KeyCode::Right => return (cur + 1).min(2),
            KeyCode::Down => {
                return table
                    .iter()
                    .enumerate()
                    .skip(3)
                    .find(|(_, row)| row.0.is_some())
                    .map_or(cur, |(index, _)| index)
            }
            _ => {}
        }
    }
    let selectable: Vec<usize> = table
        .iter()
        .enumerate()
        .filter(|r| r.1 .0.is_some())
        .map(|r| r.0)
        .collect();
    let first_after = |i: usize| selectable.iter().copied().find(|&s| s > i);
    let last_before = |i: usize| selectable.iter().rev().copied().find(|&s| s < i);
    let section_starts: Vec<usize> = table
        .iter()
        .enumerate()
        .filter(|r| r.1 .0.is_none())
        .filter_map(|r| first_after(r.0))
        .chain(selectable.last().copied())
        .collect();
    let pos = selectable.iter().position(|&s| s == cur).unwrap_or(0);
    match key {
        KeyCode::Down | KeyCode::Char('j') => first_after(cur).unwrap_or(cur),
        KeyCode::Up | KeyCode::Char('k') => last_before(cur).unwrap_or(cur),
        KeyCode::PageDown => selectable
            .get((pos + page).min(selectable.len().saturating_sub(1)))
            .copied()
            .unwrap_or(cur),
        KeyCode::PageUp => selectable
            .get(pos.saturating_sub(page))
            .copied()
            .unwrap_or(cur),
        KeyCode::Home => selectable.first().copied().unwrap_or(cur),
        KeyCode::End => selectable.last().copied().unwrap_or(cur),
        KeyCode::BackTab => section_starts
            .iter()
            .copied()
            .find(|&s| s > cur)
            .or_else(|| section_starts.first().copied())
            .unwrap_or(cur),
        KeyCode::Char('e') => table
            .iter()
            .position(|r| matches!(r.0, Some(Choice::App { .. } | Choice::GlobalLaunch { .. })))
            .unwrap_or(cur),
        _ => cur,
    }
}

/// Layout and hit targets share physical terminal cells, including after resize.
#[derive(Clone, Copy, Default)]
struct SelectorHitboxes {
    viewport: Rect,
    navigation: [Rect; 3],
    content: Rect,
    content_start: usize,
    footer: Rect,
}

impl SelectorHitboxes {
    fn row_at(&self, x: u16, y: u16, offset: usize) -> Option<usize> {
        let position = ratatui::layout::Position::new(x, y);
        if self.footer.contains(position) {
            return None;
        }
        self.navigation
            .iter()
            .position(|area| area.contains(position))
            .or_else(|| {
                self.content.contains(position).then(|| {
                    self.content_start + offset + usize::from(y.saturating_sub(self.content.y))
                })
            })
    }
}

#[derive(Clone, Copy)]
struct SelectorFrame<'a> {
    c: &'a Config,
    table: &'a [(Option<Choice>, Line<'static>, Style)],
    view: &'a MenuView,
    target: Option<&'a DesktopSystem>,
    query: &'a str,
    searching: bool,
    status: &'a str,
    g: &'a Glyphs,
}

/// Width is measured in terminal cells, not UTF-8 bytes. A search shows its tail
/// so the text being edited remains visible even on a narrow remote terminal.
fn clip_cells(value: &str, width: u16, tail: bool) -> String {
    let mut chars = Vec::new();
    let mut used = 0;
    let add = |ch: char, chars: &mut Vec<char>, used: &mut usize| {
        let cells = Line::from(ch.to_string()).width();
        if used.saturating_add(cells) > usize::from(width) {
            return false;
        }
        *used += cells;
        chars.push(ch);
        true
    };
    if tail {
        for ch in value.chars().rev() {
            if !add(ch, &mut chars, &mut used) {
                break;
            }
        }
        chars.reverse();
    } else {
        for ch in value.chars() {
            if !add(ch, &mut chars, &mut used) {
                break;
            }
        }
    }
    chars.into_iter().collect()
}

fn selector_footer(
    context: SelectorFrame<'_>,
    state: &ListState,
    area: Rect,
) -> Vec<Line<'static>> {
    let SelectorFrame {
        c,
        table,
        target,
        query,
        searching,
        status,
        g,
        ..
    } = context;
    let help = if area.width >= 180 {
        g.help
    } else if area.width >= 58 {
        "Enter open  / Find  Tab new  PgUp/Dn page  Esc shell"
    } else if area.width >= 34 {
        "Enter open  / Find  Esc shell"
    } else if area.width >= 24 {
        "Enter open /Find Esc"
    } else {
        "Enter open Esc"
    };
    if area.height < 2 {
        return vec![Line::from(clip_cells(help, area.width, false))];
    }
    let summary = if searching || !query.is_empty() {
        let prefix = if searching { "/ " } else { "Find: " };
        format!(
            "{prefix}{}",
            clip_cells(
                &menu_text(query, c.console),
                area.width.saturating_sub(prefix.len() as u16),
                true
            )
        )
    } else {
        let selected = state.selected().and_then(|index| table.get(index));
        let position = state.selected().map_or(0, |index| {
            table
                .iter()
                .take(index.saturating_add(1))
                .filter(|row| row.0.is_some())
                .count()
        });
        let total = table.iter().filter(|row| row.0.is_some()).count();
        let note = if !matches!(
            status,
            "Discovering apps and running systems"
                | "Select Apps or Systems; / searches the complete menu"
        ) {
            menu_text(status, c.console)
        } else {
            target
                .filter(|system| {
                    selected.is_some_and(|row| {
                        matches!(&row.0,
                Some(Choice::SelectSystem(selector)) if selector == &system.selector)
                    })
                })
                .map_or_else(String::new, |system| {
                    if system.ready {
                        "Ready".into()
                    } else {
                        "Unavailable".into()
                    }
                })
        };
        if note.is_empty() {
            format!("{position}/{total}")
        } else {
            format!("{position}/{total} | {note}")
        }
    };
    vec![
        Line::from(clip_cells(&summary, area.width, false)),
        Line::from(clip_cells(help, area.width, false)),
    ]
}

/// The system chooser is a full-viewport surface. Other MiOS popups still use
/// their shared launch policy; reserving their compact rectangle here hid the
/// selectable systems underneath four persistent metadata/help rows.
fn paint_selector(
    frame: &mut Frame,
    context: SelectorFrame<'_>,
    state: &mut ListState,
) -> SelectorHitboxes {
    let SelectorFrame {
        c, table, view, g, ..
    } = context;
    let [bg, fg, accent, subtle, muted] = c.palette;
    let viewport = frame.area();
    let footer_rows = if viewport.height >= 6 {
        2
    } else {
        u16::from(viewport.height >= 4)
    };
    let [body, footer] =
        Layout::vertical([Constraint::Min(1), Constraint::Length(footer_rows)]).areas(viewport);
    let block = Block::default()
        .borders(if body.width >= 4 && body.height >= 3 {
            Borders::ALL
        } else {
            Borders::NONE
        })
        .border_type(if c.console {
            BorderType::Plain
        } else {
            BorderType::Rounded
        })
        .border_style(Style::default().fg(subtle))
        .title(Span::styled(
            format!(" {} ", c.menu.title),
            Style::default().fg(accent).add_modifier(Modifier::BOLD),
        ))
        .style(Style::default().bg(bg).fg(fg));
    let inner = block.inner(body);
    frame.render_widget(block, body);
    let tabs_visible = inner.height >= 2 && inner.width >= 12;
    let [navigation, content] = Layout::vertical([
        Constraint::Length(u16::from(tabs_visible)),
        Constraint::Min(1),
    ])
    .areas(inner);
    let navigation = if tabs_visible {
        Layout::horizontal([Constraint::Ratio(1, 3); 3]).areas(navigation)
    } else {
        [Rect::default(); 3]
    };
    let content_start = if tabs_visible { 3 } else { 0 };
    let highlight = Style::default()
        .bg(accent)
        .fg(bg)
        .add_modifier(Modifier::BOLD);
    if tabs_visible {
        let labels = c
            .menu
            .tabs
            .as_ref()
            .map(|tabs| tabs.each_ref().map(String::as_str))
            .unwrap_or([
                c.menu.apps_title.as_str(),
                c.menu.systems_title.as_str(),
                "All",
            ]);
        let active = match view {
            MenuView::Apps => 0,
            MenuView::Systems => 1,
            MenuView::All => 2,
        };
        for (index, area) in navigation.iter().enumerate() {
            let style = if state.selected() == Some(index) {
                highlight
            } else if index == active {
                Style::default()
                    .fg(accent)
                    .bg(bg)
                    .add_modifier(Modifier::BOLD)
            } else {
                Style::default().fg(fg).bg(bg)
            };
            frame.render_widget(
                Paragraph::new(clip_cells(labels[index], area.width, false)).style(style),
                *area,
            );
        }
    }
    let items: Vec<ListItem> = table
        .iter()
        .skip(content_start)
        .map(|(choice, line, style)| {
            let value = menu_text(&line.to_string(), c.console);
            let indent = if value.starts_with(' ') { "  " } else { "" };
            let value = format!(
                "{indent}{}",
                value.split_whitespace().collect::<Vec<_>>().join(" ")
            );
            ListItem::new(clip_cells(
                &value,
                content
                    .width
                    .saturating_sub(Line::from(g.pick).width() as u16),
                false,
            ))
            .style(if choice.is_none() {
                style.fg(accent)
            } else {
                style.fg(fg)
            })
        })
        .collect();
    let mut content_state = ListState::default();
    *content_state.offset_mut() = state.offset();
    content_state.select(
        state
            .selected()
            .filter(|index| *index >= content_start)
            .map(|index| index - content_start),
    );
    frame.render_stateful_widget(
        List::new(items)
            .highlight_style(highlight)
            .highlight_symbol(g.pick),
        content,
        &mut content_state,
    );
    *state.offset_mut() = content_state.offset();
    if footer.height > 0 {
        frame.render_widget(
            Paragraph::new(selector_footer(context, state, footer))
                .style(Style::default().fg(muted).bg(bg)),
            footer,
        );
    }
    SelectorHitboxes {
        viewport,
        navigation,
        content,
        content_start,
        footer,
    }
}

fn pick(c: &Config) -> Result<(Choice, Vec<Node>), String> {
    let guard = TermGuard::arm();
    let mut terminal = ratatui::init();
    let _ = execute!(std::io::stdout(), EnableMouseCapture);
    // This node renders immediately; tailnet peers arrive from a worker when they answer
    // (or time out), so a slow peer never holds the raw-mode terminal.
    let mut nodes = vec![local_node(c)];
    let mut nested = discover_nested(c);
    let mut pending = Some(spawn_remote(c));
    let mut menu_pending = Some(spawn_menu(c));
    let mut discovery = MenuDiscovery::default();
    let mut view = MenuView::All;
    let mut query = String::new();
    let mut searching = false;
    let mut focus_content = false;
    let mut selected = std::env::var("MIOS_EXECUTION_SESSION").unwrap_or_default();
    let mut status = "Discovering apps and running systems".to_owned();
    let mut probe_pending: Option<PendingSystemProbe> = None;
    let mut state = ListState::default();
    let mut remembered: Option<Choice> = None;
    let mut last_click: Option<(usize, Instant)> = None;
    let g = if c.console { &ASCII } else { &UNICODE };
    let result = loop {
        if let Some(rx) = &pending {
            match rx.try_recv() {
                Ok(remote) => {
                    nodes.truncate(1);
                    nodes.extend(remote);
                    pending = None;
                }
                Err(mpsc::TryRecvError::Disconnected) => pending = None,
                Err(mpsc::TryRecvError::Empty) => {}
            }
        }
        if let Some(rx) = &menu_pending {
            if let Ok(result) = rx.try_recv() {
                discovery = result;
                if selected.is_empty() {
                    let operators: Vec<_> = discovery
                        .systems
                        .iter()
                        .filter(|s| s.operator && s.ready)
                        .collect();
                    if operators.len() == 1 {
                        selected = operators[0].selector.clone();
                    }
                }
                status = "Select Apps or Systems; / searches the complete menu".into();
                menu_pending = None;
            }
        }
        if let Some(probe) = &probe_pending {
            let selector = &probe.selector;
            if let Ok(result) = probe.receiver.try_recv() {
                if let Some(system) = discovery
                    .systems
                    .iter_mut()
                    .find(|s| &s.selector == selector && selected == *selector)
                {
                    match result {
                        Ok((label, target)) => {
                            system.label = format!("{label} via {selector}");
                            system.selector = target.clone();
                            system.ready = true;
                            system.error.clear();
                            selected = target;
                            view = MenuView::Apps;
                            query.clear();
                            focus_content = true;
                        }
                        Err(error) => {
                            system.ready = false;
                            system.error = error.clone();
                            status = error;
                        }
                    }
                }
                probe_pending = None;
            }
        }
        let target = discovery.systems.iter().find(|s| s.selector == selected);
        let table = menu_rows(MenuContext {
            c,
            nodes: &nodes,
            querying: pending.is_some(),
            nested: &nested,
            discovery: &discovery,
            view: &view,
            query: &query,
            selected: target,
            g,
        });
        state.select(
            remembered
                .as_ref()
                .and_then(|want| table.iter().position(|r| r.0.as_ref() == Some(want)))
                .or_else(|| {
                    if query.is_empty() && !focus_content {
                        select_index(&table, None, "")
                    } else {
                        table
                            .iter()
                            .enumerate()
                            .skip(3)
                            .find(|(_, r)| r.0.is_some())
                            .map(|(i, _)| i)
                    }
                }),
        );
        focus_content = false;
        let context = SelectorFrame {
            c,
            table: &table,
            view: &view,
            target,
            query: &query,
            searching,
            status: &status,
            g,
        };
        let mut hitboxes = SelectorHitboxes::default();
        terminal
            .draw(|frame| hitboxes = paint_selector(frame, context, &mut state))
            .map_err(|error| error.to_string())?;
        if !event::poll(Duration::from_millis(250)).map_err(|e| e.to_string())? {
            continue;
        }
        let cur = state.selected().unwrap_or(0);
        let input = event::read().map_err(|e| e.to_string())?;
        if matches!(input, Event::Mouse(mouse) if mouse.kind == MouseEventKind::Down(MouseButton::Left))
            && terminal.size().map_err(|error| error.to_string())? != hitboxes.viewport.as_size()
        {
            terminal
                .draw(|frame| hitboxes = paint_selector(frame, context, &mut state))
                .map_err(|error| error.to_string())?;
        }
        let page = hitboxes.content.height.max(1) as usize;
        let mut activate = false;
        let next = match input {
            Event::Key(key) if key.kind == KeyEventKind::Press && searching => {
                match key.code {
                    KeyCode::Esc => {
                        searching = false;
                        query.clear();
                        remembered = None;
                    }
                    KeyCode::Backspace => {
                        query.pop();
                        remembered = None;
                    }
                    KeyCode::Enter => {
                        searching = false;
                        activate = true;
                    }
                    KeyCode::Char(ch) if !ch.is_control() && query.len() < 4096 => {
                        query.push(ch);
                        remembered = None;
                    }
                    code => {
                        let next = navigate(&table, cur, code, page);
                        remembered = table.get(next).and_then(|r| r.0.clone());
                    }
                }
                if !activate {
                    continue;
                }
                cur
            }
            Event::Key(key) if key.kind == KeyEventKind::Press => match key.code {
                KeyCode::Char('/') => {
                    searching = true;
                    cur
                }
                KeyCode::Enter => {
                    activate = true;
                    cur
                }
                KeyCode::Char('n') | KeyCode::Tab => break Ok(Choice::New { node: 0 }),
                KeyCode::Char('r') => {
                    nodes[0] = local_node(c);
                    nested = discover_nested(c);
                    if menu_pending.is_none() {
                        menu_pending = Some(spawn_menu(c));
                    }
                    if pending.is_none() {
                        pending = Some(spawn_remote(c));
                    }
                    cur
                }
                KeyCode::Char('s') | KeyCode::Char('q') | KeyCode::Esc => break Ok(Choice::Shell),
                code => navigate(&table, cur, code, page),
            },
            Event::Mouse(m) => match m.kind {
                MouseEventKind::ScrollDown => navigate(&table, cur, KeyCode::Down, page),
                MouseEventKind::ScrollUp => navigate(&table, cur, KeyCode::Up, page),
                MouseEventKind::Down(MouseButton::Left) => {
                    let row = hitboxes.row_at(m.column, m.row, state.offset());
                    let inside = row.is_some();
                    let row = row.unwrap_or(cur);
                    if inside && table.get(row).is_some_and(|r| r.0.is_some()) {
                        // A second click on the same row within half a second opens it.
                        activate = row < hitboxes.content_start
                            || last_click.is_some_and(|(r, t)| {
                                r == row && t.elapsed() < Duration::from_millis(500)
                            });
                        last_click = Some((row, Instant::now()));
                        row
                    } else {
                        cur
                    }
                }
                _ => cur,
            },
            _ => cur,
        };
        remembered = table.get(next).and_then(|r| r.0.clone());
        if activate {
            if let Some(choice) = remembered.clone() {
                match choice {
                    Choice::View(next) => {
                        view = next;
                        query.clear();
                        focus_content = true;
                        remembered = None;
                    }
                    Choice::Notice(message) => {
                        status = message;
                    }
                    Choice::SelectSystem(selector) => {
                        if discovery
                            .systems
                            .iter()
                            .any(|s| s.selector == selector && s.ready)
                        {
                            selected = selector;
                            view = MenuView::Apps;
                            query.clear();
                            focus_content = true;
                            remembered = None;
                        } else if probe_pending.is_none() {
                            selected = selector.clone();
                            let (tx, rx) = mpsc::channel();
                            let config = c.clone();
                            let requested = selector.clone();
                            std::thread::spawn(move || {
                                let _ = tx.send(probe_system(&config, &requested));
                            });
                            status =
                                "Probing the selected system; no application has been launched"
                                    .into();
                            probe_pending = Some(PendingSystemProbe {
                                selector,
                                receiver: rx,
                            });
                        }
                    }
                    other => break Ok(other),
                }
            }
        }
    };
    drop(guard);
    result.map(|choice| (choice, nodes))
}

/// A session window change affects every attached client of that session. Bind
/// in-pane TUI launches to the sole attached client of the exact invoking pane;
/// never infer an operator client from the configured discovery server.
fn unique_pane_client(pane: &str, session: &str, rows: &str) -> Result<String, String> {
    let mut clients = Vec::new();
    for row in rows.lines() {
        let columns: Vec<_> = row.split('\t').collect();
        if columns.len() != 3 {
            return Err("Malformed tmux client identity".into());
        }
        if columns[1] == session {
            if !crate::launcher::client_ok(columns[0]) {
                return Err("Invalid tmux client identity".into());
            }
            clients.push((columns[0].to_owned(), columns[2]));
        }
    }
    match clients.as_slice() {
        [(client, current)] if *current == pane => Ok(client.clone()),
        [] => Err("No attached tmux client owns this selector pane".into()),
        [_] => Err("The attached tmux client is viewing another pane".into()),
        _ => Err("Multiple attached tmux clients share this selector session".into()),
    }
}

fn current_tmux_client() -> Result<Option<String>, String> {
    use std::os::unix::fs::{FileTypeExt, MetadataExt};

    let Some(witness) = std::env::var_os("TMUX") else {
        return Ok(None);
    };
    let witness = witness.to_str().ok_or("Invalid TMUX identity")?;
    let socket = witness.split(',').next().unwrap_or_default();
    if !socket.starts_with('/') || socket.chars().any(char::is_control) {
        return Err("Invalid TMUX socket identity".into());
    }
    let metadata = std::fs::symlink_metadata(socket).map_err(|e| format!("TMUX socket: {e}"))?;
    if !metadata.file_type().is_socket() || metadata.uid() != unsafe { libc::geteuid() } {
        return Err("TMUX socket is not owned by this selector user".into());
    }
    let pane = std::env::var("TMUX_PANE").map_err(|_| "TMUX_PANE is required inside tmux")?;
    if !pane.starts_with('%') || pane.len() < 2 || !pane[1..].bytes().all(|c| c.is_ascii_digit()) {
        return Err("Invalid TMUX_PANE identity".into());
    }
    let deadline = Instant::now() + LOCAL_DEADLINE;
    let query = |args: &[&str]| -> Result<String, String> {
        let output = run_bounded(
            tmux_query_command().args(["-S", socket]).args(args),
            deadline,
        )?;
        if !output.status.success() {
            return Err(format!(
                "Cannot verify selector client: {}",
                String::from_utf8_lossy(&output.stderr).trim()
            ));
        }
        String::from_utf8(output.stdout)
            .map(|value| value.trim_end_matches('\n').to_owned())
            .map_err(|_| "Invalid tmux identity encoding".into())
    };
    let identity = query(&[
        "display-message",
        "-p",
        "-t",
        &pane,
        "#{pane_id}\t#{session_id}",
    ])?;
    let Some((actual_pane, session)) = identity.split_once('\t') else {
        return Err("Cannot verify the selector pane session".into());
    };
    if actual_pane != pane
        || !session.starts_with('$')
        || session.len() < 2
        || !session[1..].bytes().all(|c| c.is_ascii_digit())
    {
        return Err("Selector pane session identity changed".into());
    }
    let rows = query(&[
        "list-clients",
        "-F",
        "#{client_name}\t#{session_id}\t#{pane_id}",
    ])?;
    unique_pane_client(&pane, session, &rows).map(Some)
}

fn app_command(id: &str, console: bool) -> Result<Command, String> {
    let mut command = Command::new(TERMINAL);
    command.args(["--app", id, "--console", if console { "1" } else { "0" }]);
    if let Some(client) = current_tmux_client()? {
        command.args(["--client", &client]);
    }
    Ok(command)
}

fn exec(choice: Choice, nodes: &[Node], c: &Config) -> String {
    let err = match choice {
        Choice::GlobalLaunch { name, session } => Command::new(&c.menu.launch_program)
            .arg(name)
            .env("MIOS_EXECUTION_SESSION", session)
            .exec(),
        Choice::View(_) | Choice::SelectSystem(_) | Choice::Notice(_) => {
            return "Menu selection is not an execution request".into()
        }
        Choice::Attach { node, session } if nodes[node].local => {
            Command::new(TERMINAL).args(["--session", &session]).exec()
        }
        Choice::AttachNested {
            socket,
            session,
            tmux_root,
        } => {
            let mut cmd = Command::new("tmux");
            let root = tmux_root.as_deref().unwrap_or(&c.socket_root);
            cmd.env("TMUX_TMPDIR", root)
                .args(["-L", &socket, "attach-session", "-t", &session])
                .exec()
        }
        Choice::Workspace => Command::new(TERMINAL).args(["--action", "ai"]).exec(),
        Choice::App { id } => match app_command(&id, c.console) {
            Ok(mut command) => command.exec(),
            Err(error) => return error,
        },
        Choice::New { node } if nodes[node].local => Command::new(TERMINAL).arg("--new").exec(),
        Choice::NewNested { socket, tmux_root } => {
            let mut cmd = Command::new("tmux");
            let root = tmux_root.as_deref().unwrap_or(&c.socket_root);
            cmd.env("TMUX_TMPDIR", root)
                .args(["-L", &socket, "new-session"])
                .exec()
        }
        Choice::Attach { node, session } => Command::new("ssh")
            .arg("-t")
            .args(ssh_args(c, &nodes[node].address))
            .arg(format!("{TERMINAL} --session {}", shell_quote(&session)))
            .exec(),
        Choice::New { node } => Command::new("ssh")
            .arg("-t")
            .args(ssh_args(c, &nodes[node].address))
            .arg(format!("{TERMINAL} --new"))
            .exec(),
        Choice::Shell => {
            let shell = std::env::var("SHELL").unwrap_or_else(|_| "/bin/bash".into());
            Command::new(shell)
                .arg("-l")
                .env("MIOS_SKIP_MOTD", "1")
                .exec()
        }
    };
    format!("exec failed: {err}")
}

/// `list` prints the discovery as JSON (scripts, Portal); otherwise run the selector and exec the choice.
pub fn run(root: &Path, list: bool) -> Result<(), String> {
    let c = config(root)?;
    if list {
        let nested = discover_nested(&c);
        let out = serde_json::json!({ "nodes": discover(&c), "nested": nested, "apps": c.apps,
            "global": menu_inventory(&c) });
        println!(
            "{}",
            serde_json::to_string_pretty(&out).map_err(|e| e.to_string())?
        );
        return Ok(());
    }
    let (choice, nodes) = pick(&c)?;
    Err(exec(choice, &nodes, &c))
}

#[cfg(test)]
mod tests {

    fn selector_table(c: &Config) -> Vec<(Option<Choice>, Line<'static>, Style)> {
        let names: Vec<_> = (0..80).map(|index| format!("session-{index:03}")).collect();
        let names: Vec<_> = names.iter().map(String::as_str).collect();
        menu_rows(MenuContext {
            c,
            nodes: &[local(&names)],
            querying: false,
            nested: &[],
            discovery: &MenuDiscovery::default(),
            view: &MenuView::All,
            query: "",
            selected: None,
            g: &ASCII,
        })
    }

    #[test]
    fn selector_uses_full_viewport_and_only_two_footer_rows_at_all_launch_sizes() {
        use ratatui::{backend::TestBackend, Terminal};
        let mut c = cfg(3);
        c.console = true;
        let table = selector_table(&c);
        for (width, height) in [(20, 6), (34, 10), (80, 24), (200, 60)] {
            let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
            let mut state = ListState::default();
            state.select(Some(3));
            let context = SelectorFrame {
                c: &c,
                table: &table,
                view: &MenuView::All,
                target: None,
                query: "",
                searching: false,
                status: "Select Apps or Systems; / searches the complete menu",
                g: &ASCII,
            };
            let mut hit = SelectorHitboxes::default();
            terminal
                .draw(|frame| hit = paint_selector(frame, context, &mut state))
                .unwrap();
            assert_eq!(hit.viewport, Rect::new(0, 0, width, height));
            assert_eq!(hit.footer, Rect::new(0, height - 2, width, 2));
            assert_eq!(hit.content.width, width - 2);
            assert!(hit.content.height >= 1);
            assert_eq!(hit.navigation[0].y, hit.navigation[2].y);
            assert_eq!(hit.navigation[0].height, 1);
            assert_eq!(terminal.backend().buffer()[(0, 0)].symbol(), "┌");
            assert_eq!(
                terminal.backend().buffer()[(width - 1, height - 3)].symbol(),
                "┘"
            );
            assert!(
                hit.row_at(0, height - 1, state.offset()).is_none(),
                "footer cannot select a hidden row"
            );
        }
    }

    #[test]
    fn selector_last_choice_survives_scroll_resize_and_current_cell_hit_testing() {
        use ratatui::{backend::TestBackend, Terminal};
        let c = cfg(3);
        let table = selector_table(&c);
        assert_eq!(navigate(&table, 0, KeyCode::Right, 1), 1);
        assert_eq!(navigate(&table, 1, KeyCode::Left, 1), 0);
        let first_content = navigate(&table, 0, KeyCode::Down, 1);
        assert!(first_content >= 3 && table[first_content].0.is_some());
        let wanted = Choice::Attach {
            node: 0,
            session: "session-079".into(),
        };
        let index = table
            .iter()
            .position(|row| row.0.as_ref() == Some(&wanted))
            .unwrap();
        let mut state = ListState::default();
        state.select(Some(index));
        let mut terminal = Terminal::new(TestBackend::new(80, 24)).unwrap();
        for (width, height) in [(80, 24), (20, 6), (34, 10), (200, 60)] {
            terminal.backend_mut().resize(width, height);
            terminal.resize(Rect::new(0, 0, width, height)).unwrap();
            let context = SelectorFrame {
                c: &c,
                table: &table,
                view: &MenuView::All,
                target: None,
                query: "",
                searching: false,
                status: "",
                g: &ASCII,
            };
            let mut hit = SelectorHitboxes::default();
            terminal
                .draw(|frame| hit = paint_selector(frame, context, &mut state))
                .unwrap();
            assert_eq!(state.selected(), Some(index));
            let row = index - hit.content_start - state.offset();
            assert!(
                row < usize::from(hit.content.height),
                "selected last row must remain painted"
            );
            let clicked = hit
                .row_at(
                    hit.content.x + 2,
                    hit.content.y + row as u16,
                    state.offset(),
                )
                .unwrap();
            assert_eq!(
                table[clicked].0.as_ref(),
                Some(&wanted),
                "physical cells resolve the same choice after resize"
            );
            let line = (0..width)
                .map(|x| terminal.backend().buffer()[(x, hit.content.y + row as u16)].symbol())
                .collect::<String>();
            assert!(line.contains("session-079"), "{width}x{height}: {line}");
            for (tab, area) in hit.navigation.iter().enumerate() {
                assert_eq!(hit.row_at(area.x, area.y, state.offset()), Some(tab));
            }
            let end = navigate(
                &table,
                clicked,
                KeyCode::End,
                usize::from(hit.content.height),
            );
            assert!(table[end].0.is_some());
        }
    }

    #[test]
    fn selector_footer_keeps_search_tail_and_omits_long_target_identity() {
        let c = cfg(3);
        let table = selector_table(&c);
        let target = DesktopSystem {
            selector: "private-target".into(),
            label: "release-with-an-enormous-session-identifier".repeat(10),
            ready: false,
            remote: true,
            operator: false,
            error: "offline".into(),
        };
        let mut state = ListState::default();
        state.select(Some(3));
        for width in [20, 34, 80] {
            let context = SelectorFrame {
                c: &c,
                table: &table,
                view: &MenuView::All,
                target: Some(&target),
                query: "",
                searching: false,
                status: "Select Apps or Systems; / searches the complete menu",
                g: &ASCII,
            };
            let footer = selector_footer(context, &state, Rect::new(0, 0, width, 2));
            assert_eq!(footer.len(), 2);
            assert!(footer.iter().all(|line| line.width() <= usize::from(width)));
            assert!(!footer
                .iter()
                .any(|line| line.to_string().contains("release-")));
            assert!(footer[1].to_string().contains("Esc"));
            let query = format!("{}tail-final", "wide Ω界 ".repeat(12));
            let search = selector_footer(
                SelectorFrame {
                    query: &query,
                    searching: true,
                    ..context
                },
                &state,
                Rect::new(0, 0, width, 2),
            );
            assert!(search[0].to_string().ends_with("tail-final"));
            assert!(search.iter().all(|line| line.width() <= usize::from(width)));
        }
        assert!(Line::from(clip_cells("你好世界", 5, false)).width() <= 5);
    }

    #[test]
    fn selector_tiny_viewport_retains_a_selectable_list_without_footer_overlap() {
        use ratatui::{backend::TestBackend, Terminal};
        let c = cfg(3);
        let table = selector_table(&c);
        let mut terminal = Terminal::new(TestBackend::new(8, 3)).unwrap();
        let mut state = ListState::default();
        state.select(Some(0));
        let mut hit = SelectorHitboxes::default();
        terminal
            .draw(|frame| {
                hit = paint_selector(
                    frame,
                    SelectorFrame {
                        c: &c,
                        table: &table,
                        view: &MenuView::All,
                        target: None,
                        query: "",
                        searching: false,
                        status: "",
                        g: &ASCII,
                    },
                    &mut state,
                )
            })
            .unwrap();
        assert_eq!(hit.content_start, 0);
        assert_eq!(hit.footer.height, 0);
        assert!(hit.content.height >= 1);
        let row = hit
            .row_at(hit.content.x, hit.content.y, state.offset())
            .unwrap();
        assert_eq!(row, 0);
        assert!(table[row].0.is_some());
    }

    #[test]
    fn tui_client_requires_exact_pane_session_and_unique_attached_client() {
        assert_eq!(
            unique_pane_client("%7", "$2", "/dev/pts/8\t$2\t%7\n/dev/pts/9\t$3\t%8").unwrap(),
            "/dev/pts/8"
        );
        assert!(unique_pane_client("%7", "$2", "").is_err());
        assert!(unique_pane_client("%7", "$2", "/dev/pts/9\t$3\t%7").is_err());
        assert!(unique_pane_client("%7", "$2", "/dev/pts/8\t$2\t%8").is_err());
        assert!(unique_pane_client("%7", "$2", "/dev/pts/8\t$2\t%7\n/dev/pts/9\t$2\t%7").is_err());
        assert!(unique_pane_client("%7", "$2", "/dev/pts/8\t$2\t%7\n/dev/pts/9\t$2\t%8").is_err());
        assert!(unique_pane_client("%7", "$2", "untrusted\t$2\t%7").is_err());
        assert!(unique_pane_client("%7", "$2", "/dev/pts/8\t$2").is_err());
    }

    #[test]
    fn menu_inventory_gives_each_native_discovery_its_own_budget() {
        use std::os::unix::fs::PermissionsExt;
        let temp = tempfile::tempdir().unwrap();
        let program = temp.path().join("inventory");
        std::fs::write(&program, "#!/bin/sh\nsleep 1.1\ncase \"$2\" in\n--catalog) echo '{\"ok\":true,\"apps\":[]}' ;;\n--sessions) echo '{\"ok\":true,\"sessions\":[],\"registered_endpoints\":[],\"unavailable\":[]}' ;;\nesac\n").unwrap();
        std::fs::set_permissions(&program, std::fs::Permissions::from_mode(0o755)).unwrap();
        let mut c = cfg(1);
        c.discovery_timeout_ms = 0; // private fixture uses only the shared two-second grace per child
        c.menu.inventory_program = program.to_string_lossy().into_owned();
        let result = menu_inventory(&c);
        assert!(result.errors.is_empty(), "{:?}", result.errors);
    }

    #[test]
    fn global_menu_first_screen_exposes_apps_and_systems_and_preserves_targets() {
        let mut c = cfg(3);
        c.apps = vec![app("registry-tui")];
        let system = DesktopSystem {
            selector: "exact-local-target".into(),
            label: "linux session".into(),
            ready: true,
            remote: false,
            operator: true,
            error: String::new(),
        };
        let discovery = MenuDiscovery {
            apps: vec![GlobalApp {
                id: "org.fixture.Any".into(),
                name: "Any fixture application".into(),
                kind: "flatpak".into(),
                available: true,
                reason: String::new(),
            }],
            systems: vec![system.clone()],
            errors: vec![],
        };
        let table = menu_rows(MenuContext {
            c: &c,
            nodes: &[local(&["mios"])],
            querying: false,
            nested: &[],
            discovery: &discovery,
            view: &MenuView::All,
            query: "",
            selected: Some(&system),
            g: &ASCII,
        });
        assert_eq!(table[0].0, Some(Choice::View(MenuView::Apps)));
        assert_eq!(table[1].0, Some(Choice::View(MenuView::Systems)));
        assert!(table.iter().any(|r| r.0
            == Some(Choice::GlobalLaunch {
                name: "org.fixture.Any".into(),
                session: "exact-local-target".into()
            })));
        assert!(table.iter().any(|r| r.0 == Some(Choice::New { node: 0 })));
        assert!(table.iter().any(|r| r.0
            == Some(Choice::App {
                id: "registry-tui".into()
            })));
        let blocked = menu_rows(MenuContext {
            c: &c,
            nodes: &[local(&[])],
            querying: false,
            nested: &[],
            discovery: &discovery,
            view: &MenuView::Apps,
            query: "",
            selected: None,
            g: &ASCII,
        });
        assert!(
            !blocked
                .iter()
                .any(|r| matches!(r.0, Some(Choice::GlobalLaunch { .. }))),
            "a catalogue entry without a ready target must never dispatch"
        );
    }

    #[test]
    fn global_menu_search_and_registered_targets_are_explicit() {
        let c = cfg(3);
        let system = DesktopSystem {
            selector: "os_control.nodes.fixture".into(),
            label: "windows / fixture".into(),
            ready: true,
            remote: true,
            operator: false,
            error: String::new(),
        };
        let discovery = MenuDiscovery {
            apps: vec![GlobalApp {
                id: "org.fixture.Any".into(),
                name: "Any fixture application".into(),
                kind: "native".into(),
                available: true,
                reason: String::new(),
            }],
            systems: vec![system.clone()],
            errors: vec![],
        };
        let table = menu_rows(MenuContext {
            c: &c,
            nodes: &[local(&[])],
            querying: false,
            nested: &[],
            discovery: &discovery,
            view: &MenuView::Apps,
            query: "fixture application",
            selected: Some(&system),
            g: &ASCII,
        });
        assert!(table.iter().any(|r| r.0
            == Some(Choice::GlobalLaunch {
                name: "Any fixture application".into(),
                session: system.selector.clone()
            })));
        let requested = menu_rows(MenuContext {
            c: &c,
            nodes: &[local(&[])],
            querying: false,
            nested: &[],
            discovery: &discovery,
            view: &MenuView::Apps,
            query: "Another requested application",
            selected: Some(&system),
            g: &ASCII,
        });
        assert!(requested.iter().any(|r| r.0
            == Some(Choice::GlobalLaunch {
                name: "Another requested application".into(),
                session: system.selector.clone()
            })));
        let mut unavailable = system.clone();
        unavailable.ready = false;
        unavailable.error = "unavailable".into();
        let blocked = menu_rows(MenuContext {
            c: &c,
            nodes: &[local(&[])],
            querying: false,
            nested: &[],
            discovery: &discovery,
            view: &MenuView::Apps,
            query: "Any",
            selected: Some(&unavailable),
            g: &ASCII,
        });
        assert!(!blocked
            .iter()
            .any(|r| matches!(r.0, Some(Choice::GlobalLaunch { .. }))));
        assert!(target_label(&serde_json::json!({"execution_target":{"id":"unproven"}})).is_none());
        assert_eq!(
            menu_text("Fixture\u{1b}[31m \u{03a9}", true),
            "Fixture[31m ?"
        );
    }

    #[test]
    fn peer_name_uses_unique_tailnet_name() {
        assert_eq!(
            super::peer_name("fedora-1.tail1234.ts.net.", "fedora"),
            "fedora-1"
        );
        assert_eq!(
            super::peer_name("fedora-2.tail1234.ts.net.", "fedora"),
            "fedora-2"
        );
    }

    #[test]
    fn peer_name_falls_back_to_hostname_without_dns_name() {
        assert_eq!(super::peer_name("", "fedora"), "fedora");
    }

    use super::*;

    #[test]
    fn real_tmux_session_records_survive_a_c_locale_query() {
        struct OwnedServer(std::path::PathBuf);
        impl Drop for OwnedServer {
            fn drop(&mut self) {
                let _ = tmux_query_command()
                    .arg("-S")
                    .arg(&self.0)
                    .arg("kill-server")
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status();
            }
        }
        let root = tempfile::tempdir().unwrap();
        let server = OwnedServer(root.path().join("tmux"));
        let start = tmux_query_command()
            .arg("-S")
            .arg(&server.0)
            .args([
                "-f",
                "/dev/null",
                "new-session",
                "-d",
                "-s",
                "MiOS-\u{03c6}|chat",
                "sleep 30",
            ])
            .env_remove("TMUX")
            .env_remove("TMUX_PANE")
            .output()
            .unwrap();
        assert!(
            start.status.success(),
            "{}",
            String::from_utf8_lossy(&start.stderr)
        );
        let reply = tmux_query_command()
            .arg("-S")
            .arg(&server.0)
            .args(["list-sessions", "-F", FORMAT])
            .env("LANG", "C")
            .env("LC_ALL", "C")
            .env_remove("TMUX")
            .env_remove("TMUX_PANE")
            .output()
            .unwrap();
        assert!(reply.status.success());
        let rows = parse_sessions(std::str::from_utf8(&reply.stdout).unwrap());
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0].name, "MiOS-\u{03c6}|chat");
        assert_eq!(rows[0].windows, 1);
        assert!(!rows[0].attached);
    }

    #[test]
    fn parses_tmux_session_lines_and_skips_malformed_ones() {
        let s = parse_sessions("mios\t3\t1\ntmux-os\t1\t0\nbroken\n");
        assert_eq!(s.len(), 2);
        assert!(s[0].attached && s[0].windows == 3);
        assert!(!s[1].attached && s[1].name == "tmux-os");
    }

    #[test]
    fn host_glob_matches_prefix_suffix_and_any() {
        assert!(glob("*", "fedora"));
        assert!(glob("mios-*", "MIOS-blade1"));
        assert!(!glob("mios-*", "fedora"));
        assert!(glob("fedora", "Fedora"));
    }

    fn cfg(ssh_timeout: i64) -> Config {
        Config {
            socket: "mios".into(),
            socket_root: "/tmp".into(),
            default_session: "mios".into(),
            node_os: vec!["linux".into()],
            node_glob: "*".into(),
            ssh_user: "u".into(),
            ssh_port: 22,
            ssh_timeout,
            palette: [Color::Reset; 5],
            hidden: vec!["mios-float-".into(), "mios-workspace-".into()],
            apps: vec![],
            console: false,
            root: PathBuf::from("/"),
            discovery_timeout_ms: 15000,
            menu: MenuConfig {
                title: "MiOS systems and apps".into(),
                apps_title: "Global apps".into(),
                systems_title: "Systems and sessions".into(),
                tabs: Some(["Apps".into(), "Systems".into(), "All".into()]),
                inventory_program: "mios-gen".into(),
                launch_program: "/usr/libexec/mios/mios-launch".into(),
                probe_program: "/usr/libexec/mios/mios-computer-use".into(),
            },
        }
    }

    #[test]
    fn bounded_run_returns_output_of_a_fast_child() {
        let out = run_bounded(
            Command::new("sh").args(["-c", "echo hi; echo err >&2"]),
            Instant::now() + Duration::from_secs(5),
        )
        .expect("fast child completes");
        assert!(out.status.success());
        assert_eq!(out.stdout, b"hi\n");
        assert_eq!(out.stderr, b"err\n");
    }

    #[test]
    fn bounded_run_kills_a_hung_peer_at_the_deadline() {
        // A peer that never answers (sh holding a grandchild sleep) is killed, group and all.
        let start = Instant::now();
        let r = run_bounded(
            Command::new("sh").args(["-c", "sleep 30; echo late"]),
            start + Duration::from_millis(300),
        );
        assert_eq!(r.unwrap_err(), "timed out");
        assert!(
            start.elapsed() < Duration::from_secs(3),
            "deadline not honoured: {:?}",
            start.elapsed()
        );
    }

    #[test]
    fn ssh_queries_carry_connect_and_keepalive_bounds() {
        let a = ssh_args(&cfg(3), "peer.ts.net").join(" ");
        assert!(a.contains("BatchMode=yes") && a.contains("ConnectTimeout=3"));
        assert!(a.contains("ServerAliveInterval=1") && a.contains("ServerAliveCountMax=2"));
        // Overall sweep deadline derives from the SSOT connect timeout and is clamped.
        assert_eq!(peer_deadline(&cfg(3)), Duration::from_secs(5));
        assert_eq!(peer_deadline(&cfg(0)), Duration::from_secs(3));
        assert_eq!(peer_deadline(&cfg(100_000)), Duration::from_secs(62));
    }

    fn through_sh(word: &str) -> String {
        let out = Command::new("sh")
            .args(["-c", &format!("printf %s {word}")])
            .output()
            .unwrap();
        String::from_utf8(out.stdout).unwrap()
    }

    #[test]
    fn shell_quote_keeps_ordinary_session_names_literal() {
        assert_eq!(shell_quote("tmux-os-2"), "'tmux-os-2'");
        assert_eq!(through_sh(&shell_quote("my session")), "my session");
    }

    #[test]
    fn shell_quote_neutralises_hostile_remote_session_names() {
        let canary =
            std::env::temp_dir().join(format!("mios-sessions-canary-{}", std::process::id()));
        let hostile = format!("x'; touch {}; echo '$(id)`id`", canary.display());
        assert_eq!(through_sh(&shell_quote(&hostile)), hostile);
        assert!(!canary.exists(), "quoted session name executed a command");
        // The old interpolation would have run it (proves the control is meaningful).
        let _ = through_sh(&format!("'{hostile}'"));
        assert!(canary.exists());
        let _ = std::fs::remove_file(&canary);
    }

    #[test]
    fn term_guard_arms_restore_handlers_and_reinstates_previous_dispositions() {
        let disp = |sig| unsafe {
            let mut cur: libc::sigaction = std::mem::zeroed();
            libc::sigaction(sig, std::ptr::null(), &mut cur);
            cur.sa_sigaction
        };
        let before = disp(libc::SIGHUP);
        let guard = TermGuard::arm();
        assert_eq!(disp(libc::SIGHUP), restore_on_signal as *const () as usize);
        assert_eq!(disp(libc::SIGTERM), restore_on_signal as *const () as usize);
        drop(guard);
        assert_eq!(disp(libc::SIGHUP), before);
    }

    #[test]
    fn rows_show_a_querying_marker_while_peers_are_pending() {
        let local = Node {
            name: "here".into(),
            address: String::new(),
            local: true,
            online: true,
            error: None,
            sessions: vec![],
        };
        assert!(
            rows(std::slice::from_ref(&local), true, &[], &[], None, &UNICODE)
                .iter()
                .any(|r| r.1.to_string().contains("querying"))
        );
        assert!(!rows(&[local], false, &[], &[], None, &UNICODE)
            .iter()
            .any(|r| r.1.to_string().contains("querying")));
    }

    #[test]
    fn rows_offer_new_session_only_on_reachable_nodes_and_always_a_shell() {
        let node = |online: bool, error: Option<String>| Node {
            name: "n".into(),
            address: "n.ts".into(),
            local: false,
            online,
            error,
            sessions: vec![],
        };
        let table = rows(
            &[
                node(true, None),
                node(false, None),
                node(true, Some("denied".into())),
            ],
            false,
            &[],
            &[],
            None,
            &UNICODE,
        );
        let news = table
            .iter()
            .filter(|r| matches!(r.0, Some(Choice::New { .. })))
            .count();
        assert_eq!(news, 1);
        assert!(matches!(table.last().unwrap().0, Some(Choice::Shell)));
    }

    fn app(id: &str) -> App {
        App {
            id: id.into(),
            label: id.into(),
            section: "system".into(),
            mode: "window".into(),
        }
    }

    fn local(sessions: &[&str]) -> Node {
        Node {
            name: "here".into(),
            address: String::new(),
            local: true,
            online: true,
            error: None,
            sessions: sessions
                .iter()
                .map(|n| Session {
                    name: (*n).into(),
                    windows: 1,
                    attached: false,
                })
                .collect(),
        }
    }

    fn peer(name: &str) -> Node {
        Node {
            name: name.into(),
            address: format!("{name}.ts"),
            local: false,
            online: true,
            error: None,
            sessions: vec![],
        }
    }

    #[test]
    fn app_rows_follow_this_node_and_never_move_when_peers_arrive() {
        let apps = [app("btop"), app("top")];
        let before = rows(
            &[local(&["mios"])],
            true,
            &apps,
            &[],
            Some("mios"),
            &UNICODE,
        );
        let after = rows(
            &[local(&["mios"]), peer("a"), peer("b")],
            false,
            &apps,
            &[],
            Some("mios"),
            &UNICODE,
        );
        let pos = |t: &[(Option<Choice>, Line<'static>, Style)], id: &str| {
            t.iter()
                .position(|r| r.0 == Some(Choice::App { id: id.into() }))
                .unwrap()
        };
        assert_eq!(pos(&before, "btop"), pos(&after, "btop"));
        assert!(
            pos(&after, "top")
                < after
                    .iter()
                    .position(|r| matches!(r.0, Some(Choice::New { node: 1 })))
                    .unwrap()
        );
        assert!(matches!(after.last().unwrap().0, Some(Choice::Shell)));
    }

    #[test]
    fn selection_identity_survives_a_row_shift() {
        let apps = [app("btop")];
        let before = rows(&[local(&[])], true, &apps, &[], None, &UNICODE);
        let shell = Choice::Shell;
        let after = rows(
            &[local(&[]), peer("a"), peer("b")],
            false,
            &apps,
            &[],
            None,
            &UNICODE,
        );
        let i = select_index(&after, Some(&shell), "mios").unwrap();
        assert_eq!(after[i].0, Some(Choice::Shell));
        assert_ne!(
            select_index(&before, Some(&shell), "mios"),
            Some(i),
            "the row index did shift"
        );
    }

    #[test]
    fn hidden_sessions_are_filtered_and_the_ai_workspace_is_offered_when_missing() {
        let c = cfg(3);
        let visible = visible_sessions(
            &c,
            parse_sessions(
                "mios\t2\t1\nmios-float-3\t1\t0\nmios-workspace-4\t1\t0\ntmux-os\t1\t0\n",
            ),
        );
        assert_eq!(
            visible.iter().map(|s| s.name.as_str()).collect::<Vec<_>>(),
            ["mios", "tmux-os"]
        );
        let without = rows(
            &[local(&["tmux-os"])],
            false,
            &[],
            &[],
            Some("mios"),
            &UNICODE,
        );
        assert!(without.iter().any(|r| r.0 == Some(Choice::Workspace)));
        let with = rows(&[local(&["mios"])], false, &[], &[], Some("mios"), &UNICODE);
        assert!(!with.iter().any(|r| r.0 == Some(Choice::Workspace)));
    }

    #[test]
    fn controller_vocabulary_navigates_rows_and_sections() {
        let apps = [app("btop"), app("top"), app("vim")];
        let t = rows(
            &[local(&["mios", "tmux-os"])],
            false,
            &apps,
            &[],
            Some("mios"),
            &UNICODE,
        );
        let at = |c: Choice| t.iter().position(|r| r.0.as_ref() == Some(&c)).unwrap();
        let mios = at(Choice::Attach {
            node: 0,
            session: "mios".into(),
        });
        assert_eq!(
            navigate(&t, mios, KeyCode::Down, 5),
            at(Choice::Attach {
                node: 0,
                session: "tmux-os".into()
            })
        );
        assert_eq!(
            navigate(&t, mios, KeyCode::Up, 5),
            mios,
            "Up at the top stays"
        );
        assert_eq!(navigate(&t, mios, KeyCode::End, 5), at(Choice::Shell));
        assert_eq!(navigate(&t, at(Choice::Shell), KeyCode::Home, 5), mios);
        assert_eq!(
            navigate(&t, mios, KeyCode::Char('e'), 5),
            at(Choice::App { id: "btop".into() })
        );
        assert_eq!(
            navigate(&t, mios, KeyCode::BackTab, 5),
            at(Choice::App { id: "btop".into() }),
            "Shift-Tab jumps to the next section"
        );
        assert_eq!(
            navigate(
                &t,
                at(Choice::App { id: "btop".into() }),
                KeyCode::BackTab,
                5
            ),
            at(Choice::Shell)
        );
        assert_eq!(
            navigate(&t, at(Choice::Shell), KeyCode::BackTab, 5),
            mios,
            "wraps to the first section"
        );
        assert_eq!(
            navigate(&t, mios, KeyCode::Tab, 5),
            mios,
            "Tab is handled by pick() as new session"
        );
        assert_eq!(
            navigate(&t, mios, KeyCode::PageDown, 2),
            at(Choice::New { node: 0 })
        );
        assert_eq!(navigate(&t, at(Choice::Shell), KeyCode::PageUp, 100), mios);
        for r in &t {
            assert!(r.0.is_some() || r.1.to_string().contains("this node"));
        }
    }

    #[test]
    fn console_rows_and_help_are_ascii() {
        let t = rows(
            &[local(&["mios"]), peer("a")],
            true,
            &[app("btop")],
            &[],
            Some("mios"),
            &ASCII,
        );
        for r in &t {
            assert!(r.1.to_string().is_ascii(), "{}", r.1);
        }
        assert!(ASCII.help.is_ascii() && ASCII.pick.is_ascii());
        assert!(!UNICODE.help.is_ascii());
    }

    #[test]
    fn discover_nested_parses_attributes_and_filters_dead_pids() {
        let dir = std::env::temp_dir().join(format!("mios-nested-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let my_pid = std::process::id();
        let live_json = serde_json::json!({
            "seat": "seat-1",
            "user": "mios",
            "target": "pip",
            "tmux_socket": "mios-nested-seat-1",
            "launcher_pid": my_pid,
        });
        std::fs::write(
            dir.join("seat-1.json"),
            serde_json::to_string(&live_json).unwrap(),
        )
        .unwrap();
        let dead_json = serde_json::json!({
            "seat": "seat-dead",
            "user": "mios",
            "target": "output",
            "tmux_socket": "mios-nested-seat-dead",
            "launcher_pid": 99999999,
        });
        std::fs::write(
            dir.join("seat-dead.json"),
            serde_json::to_string(&dead_json).unwrap(),
        )
        .unwrap();

        std::env::set_var("MIOS_NESTED_SESSIONS_DIR", dir.to_str().unwrap());
        let c = cfg(3);
        let nested = discover_nested(&c);
        let _ = std::fs::remove_dir_all(&dir);
        std::env::remove_var("MIOS_NESTED_SESSIONS_DIR");

        assert_eq!(nested.len(), 1, "only live launcher pid is discovered");
        assert_eq!(nested[0].seat, "seat-1");
        assert_eq!(nested[0].user, "mios");
        assert_eq!(nested[0].target, "pip");
        assert_eq!(nested[0].tmux_socket, "mios-nested-seat-1");
    }

    #[test]
    fn rows_shows_nested_sessions_section_with_attach_and_new() {
        let nested = [NestedSession {
            seat: "p1".into(),
            user: "mios".into(),
            target: "pip".into(),
            tmux_socket: "mios-nested-p1".into(),
            tmux_root: None,
            sessions: vec![Session {
                name: "main".into(),
                windows: 1,
                attached: true,
            }],
        }];
        let t = rows(
            &[local(&["mios"])],
            false,
            &[],
            &nested,
            Some("mios"),
            &UNICODE,
        );
        assert!(
            t.iter()
                .any(|r| r.1.to_string().contains("Nested sessions")),
            "Nested sessions header present"
        );
        assert!(
            t.iter().any(|r| r.1.to_string().contains("seat p1")),
            "seat p1 metadata present"
        );
        assert!(t.iter().any(|r| matches!(&r.0, Some(Choice::AttachNested { socket, session, .. }) if socket == "mios-nested-p1" && session == "main")));
        assert!(t.iter().any(
            |r| matches!(&r.0, Some(Choice::NewNested { socket, .. }) if socket == "mios-nested-p1")
        ));
    }
}
