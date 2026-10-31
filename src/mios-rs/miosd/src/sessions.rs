// AI-hint: miosd sessions: global tmux session selector, the default entry of every interactive MiOS login (local and tailnet nodes), with the shared Apps and TUIs launcher section; keyboard, controller vocabulary and mouse driven, Linux-console safe.
// AI-related: usr/share/mios/mios.toml, usr/libexec/mios/mios-terminal, tools/native/mios-resolver/src/terminal_tuis.rs
//! Global tmux session selector (`miosd sessions`): the default entry of every interactive
//! MiOS login. Lists [keybindings].socket_name sessions on this node and on tailnet MiOS
//! nodes ([terminal.sessions]), starts new bare Tmux-OS sessions, opens the MiOS AI workspace
//! and the TUIs of [[terminal.tuis]] in Tmux-OS, and execs the choice.

use std::io::Read;
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::{Command, Output, Stdio};
use std::sync::mpsc;
use std::sync::OnceLock;
use std::time::{Duration, Instant};

use ratatui::crossterm::event::{
    self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyEventKind, MouseButton, MouseEventKind,
};
use ratatui::crossterm::execute;
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, BorderType, Borders, List, ListItem, ListState, Paragraph};
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
}

#[derive(Clone, Debug, PartialEq, serde::Serialize)]
pub struct App {
    id: String,
    label: String,
    section: String,
    mode: String,
}

#[derive(Clone, serde::Serialize)]
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

#[derive(Clone, Debug, PartialEq)]
enum Choice {
    Attach { node: usize, session: String },
    New { node: usize },
    /// The MiOS AI workspace ([keybindings].terminal_session) when it is not running yet.
    Workspace,
    App { id: String },
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
    help: "\u{2191}\u{2193} PgUp PgDn select \u{b7} Enter open \u{b7} e apps \u{b7} Tab/n new \u{b7} S-Tab section \u{b7} r refresh \u{b7} s shell",
};
const ASCII: Glyphs = Glyphs {
    sep: "  -  ",
    more: "...",
    pick: "> ",
    help: "Up/Down PgUp/PgDn select - Enter open - e apps - Tab/n new - S-Tab section - r refresh - s shell",
};

fn text<'a>(v: &'a Value, path: &str) -> Result<&'a Value, String> {
    path.split('.')
        .try_fold(v, |cur, key| cur.get(key))
        .ok_or_else(|| format!("missing SSOT key {path}"))
}

fn string(v: &Value, path: &str) -> Result<String, String> {
    text(v, path)?.as_str().map(str::to_owned).ok_or_else(|| format!("{path} must be a string"))
}

fn integer(v: &Value, path: &str) -> Result<i64, String> {
    text(v, path)?.as_integer().ok_or_else(|| format!("{path} must be an integer"))
}

fn color(hex: &str) -> Color {
    let h = hex.trim_start_matches('#');
    let byte = |i: usize| u8::from_str_radix(h.get(i..i + 2).unwrap_or("00"), 16).unwrap_or(0);
    Color::Rgb(byte(0), byte(2), byte(4))
}

fn config(root: &Path) -> Result<Config, String> {
    let v = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let ssh = ["SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY"].iter().any(|k| std::env::var(k).is_ok_and(|v| !v.is_empty()));
    let console = mios_unit_gen::tmux_os::console_client(&v, &std::env::var("TERM").unwrap_or_default(), ssh)?;
    let launcher = mios_resolver::terminal_tuis::launcher(&v)?;
    let catalog = mios_resolver::terminal_tuis::catalog(&v)?;
    let apps = if launcher.enabled {
        mios_resolver::terminal_tuis::visible(&catalog, console, mios_resolver::terminal_tuis::is_installed)
            .into_iter()
            .map(|t| App { id: t.id.clone(), label: t.label.clone(), section: t.section.clone(), mode: t.mode.as_str().into() })
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
    Ok(Config {
        socket: string(&v, "keybindings.socket_name")?,
        socket_root: string(&v, "terminal.socket_root")?,
        default_session: string(&v, "keybindings.terminal_session")?,
        node_os,
        node_glob: string(&v, "terminal.sessions.node_host_glob")?,
        ssh_user: if user.is_empty() { std::env::var("USER").unwrap_or_default() } else { user },
        ssh_port: integer(&v, "terminal.sessions.ssh_port")?,
        ssh_timeout: integer(&v, "terminal.sessions.ssh_timeout_s")?,
        palette: if console {
            // The kernel console has 8/16 colours: RGB would collapse; mios-controls uses the same set.
            [Color::Black, Color::White, Color::Yellow, Color::Gray, Color::DarkGray]
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
    sessions.into_iter().filter(|s| !mios_unit_gen::tmux_os::is_hidden_session(&c.hidden, &s.name)).collect()
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
fn run_bounded(cmd: &mut Command, deadline: Instant) -> Result<Output, String> {
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
    let out = drain(child.stdout.take().map(|p| Box::new(p) as Box<dyn Read + Send>));
    let err = drain(child.stderr.take().map(|p| Box::new(p) as Box<dyn Read + Send>));
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
    Ok(Output { status, stdout: out.join().unwrap_or_default(), stderr: err.join().unwrap_or_default() })
}

/// POSIX shell single-quoting: `'` becomes `'\''`, so any string is one literal word.
fn shell_quote(s: &str) -> String {
    format!("'{}'", s.replace('\'', r"'\''"))
}

fn local_sessions(c: &Config) -> Result<Vec<Session>, String> {
    let out = run_bounded(
        Command::new("tmux")
            .env("TMUX_TMPDIR", &c.socket_root)
            .args(["-L", &c.socket, "list-sessions", "-F", FORMAT]),
        Instant::now() + LOCAL_DEADLINE,
    )
    .map_err(|e| format!("tmux: {e}"))?;
    // No server yet is an empty list, not an error.
    Ok(visible_sessions(c, parse_sessions(&String::from_utf8_lossy(&out.stdout))))
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
        "TMUX_TMPDIR={} tmux -L {} list-sessions -F {} 2>/dev/null; true",
        shell_quote(&c.socket_root),
        shell_quote(&c.socket),
        shell_quote(FORMAT)
    );
    let out = run_bounded(Command::new("ssh").args(ssh_args(c, address)).arg(remote), deadline)
        .map_err(|e| format!("ssh: {e}"))?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        return Err(err.lines().last().unwrap_or("unreachable").trim().to_owned());
    }
    Ok(visible_sessions(c, parse_sessions(&String::from_utf8_lossy(&out.stdout))))
}

/// Deadline for one whole tailnet sweep (see PEER_DEADLINE_GRACE).
fn peer_deadline(c: &Config) -> Duration {
    Duration::from_secs(c.ssh_timeout.clamp(1, 60) as u64) + PEER_DEADLINE_GRACE
}

/// This node only (bounded; shown before any peer answers).
fn local_node(c: &Config) -> Node {
    let host = std::fs::read_to_string("/proc/sys/kernel/hostname").unwrap_or_default().trim().to_owned();
    let (sessions, error) = match local_sessions(c) {
        Ok(s) => (s, None),
        Err(e) => (Vec::new(), Some(e)),
    };
    Node { name: host, address: String::new(), local: true, online: true, error, sessions }
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
    let field = |p: &serde_json::Value, k: &str| p.get(k).and_then(|v| v.as_str()).unwrap_or("").to_owned();
    let mut remote: Vec<Node> = peers
        .iter()
        .filter(|p| c.node_os.iter().any(|o| *o == field(p, "OS")) && glob(&c.node_glob, &field(p, "HostName")))
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

// ---- terminal restoration on every exit path -------------------------------------------
// ratatui::init installs a panic hook that restores the terminal; TermGuard's Drop covers
// early `?` returns; the signal handlers cover SIGINT/SIGTERM/SIGHUP/SIGQUIT (raw mode
// disables ISIG, but a parent, `kill` or a hung-up ssh/tty still delivers them).

static SAVED_TERMIOS: OnceLock<libc::termios> = OnceLock::new();
const RESTORE_SIGNALS: [libc::c_int; 4] = [libc::SIGINT, libc::SIGTERM, libc::SIGHUP, libc::SIGQUIT];
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

struct TermGuard {
    previous: Vec<(libc::c_int, libc::sigaction)>,
}

impl TermGuard {
    /// Capture cooked-mode termios and arm the signal handlers BEFORE entering raw mode.
    fn arm() -> Self {
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

fn rows(nodes: &[Node], querying: bool, apps: &[App], workspace: Option<&str>, g: &Glyphs) -> Vec<(Option<Choice>, Line<'static>, Style)> {
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
        out.push((None, Line::from(format!("{}{}{state}", n.name, g.sep)), Style::default().add_modifier(Modifier::BOLD)));
        for s in &n.sessions {
            let mark = if s.attached { "attached" } else { "detached" };
            out.push((
                Some(Choice::Attach { node: i, session: s.name.clone() }),
                Line::from(format!("    {}   {} window(s), {mark}", s.name, s.windows)),
                Style::default(),
            ));
        }
        if i == 0 {
            if let Some(name) = workspace.filter(|w| !n.sessions.iter().any(|s| s.name == *w)) {
                out.push((Some(Choice::Workspace), Line::from(format!("    + MiOS AI workspace ({name})")), Style::default()));
            }
        }
        if n.online && n.error.is_none() {
            out.push((Some(Choice::New { node: i }), Line::from("    + new bare Tmux-OS session"), Style::default()));
        }
        // This node's TUIs follow its own block at once, so tailnet rows arriving later never move them.
        if i == 0 && !apps.is_empty() {
            out.push((None, Line::from(format!("TUI apps{}this node", g.sep)), Style::default().add_modifier(Modifier::BOLD)));
            for a in apps {
                out.push((
                    Some(Choice::App { id: a.id.clone() }),
                    Line::from(format!("    {:<28} {} {}", a.label, a.section, a.mode)),
                    Style::default(),
                ));
            }
        }
    }
    if querying {
        out.push((None, Line::from(format!("tailnet nodes{}querying{}", g.sep, g.more)), Style::default().add_modifier(Modifier::DIM)));
    }
    out.push((Some(Choice::Shell), Line::from("Plain shell (no tmux)"), Style::default()));
    out
}

/// Index of the selected row for the next frame: the remembered choice where it still is (rows may shift
/// when tailnet peers arrive), else the default session, else the first selectable row.
fn select_index(table: &[(Option<Choice>, Line<'static>, Style)], remembered: Option<&Choice>, default_session: &str) -> Option<usize> {
    remembered
        .and_then(|want| table.iter().position(|r| r.0.as_ref() == Some(want)))
        .or_else(|| table.iter().position(|r| matches!(&r.0, Some(Choice::Attach { session, .. }) if session == default_session)))
        .or_else(|| table.iter().position(|r| r.0.is_some()))
}

/// Keyboard/controller navigation over selectable rows: arrows, PgUp/PgDn, Home/End, BackTab (next section;
/// Tab is "new session" so the controller's West button reaches it, FINAL.md C-33), and `e` (first TUI app).
fn navigate(table: &[(Option<Choice>, Line<'static>, Style)], cur: usize, key: KeyCode, page: usize) -> usize {
    let selectable: Vec<usize> = table.iter().enumerate().filter(|r| r.1 .0.is_some()).map(|r| r.0).collect();
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
        KeyCode::PageDown => selectable.get((pos + page).min(selectable.len().saturating_sub(1))).copied().unwrap_or(cur),
        KeyCode::PageUp => selectable.get(pos.saturating_sub(page)).copied().unwrap_or(cur),
        KeyCode::Home => selectable.first().copied().unwrap_or(cur),
        KeyCode::End => selectable.last().copied().unwrap_or(cur),
        KeyCode::BackTab => section_starts.iter().copied().find(|&s| s > cur).or_else(|| section_starts.first().copied()).unwrap_or(cur),
        KeyCode::Char('e') => table.iter().position(|r| matches!(r.0, Some(Choice::App { .. }))).unwrap_or(cur),
        _ => cur,
    }
}

fn pick(c: &Config) -> Result<(Choice, Vec<Node>), String> {
    let guard = TermGuard::arm();
    let mut terminal = ratatui::init();
    let _ = execute!(std::io::stdout(), EnableMouseCapture);
    // This node renders immediately; tailnet peers arrive from a worker when they answer
    // (or time out), so a slow peer never holds the raw-mode terminal.
    let mut nodes = vec![local_node(c)];
    let mut pending = Some(spawn_remote(c));
    let mut state = ListState::default();
    let mut remembered: Option<Choice> = None;
    let mut last_click: Option<(usize, Instant)> = None;
    let [bg, fg, accent, subtle, muted] = c.palette;
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
        let table = rows(&nodes, pending.is_some(), &c.apps, Some(&c.default_session), g);
        state.select(select_index(&table, remembered.as_ref(), &c.default_session));
        let items: Vec<ListItem> = table
            .iter()
            .map(|(choice, line, style)| {
                let base = if choice.is_none() { style.fg(accent) } else { style.fg(fg) };
                ListItem::new(line.clone()).style(base)
            })
            .collect();
        let mut list_area = Rect::default();
        terminal
            .draw(|f| {
                let [body, help] = Layout::vertical([Constraint::Min(3), Constraint::Length(1)]).areas(f.area());
                list_area = body;
                let block = Block::default()
                    .borders(Borders::ALL)
                    .border_type(if c.console { BorderType::Plain } else { BorderType::Rounded })
                    .border_style(Style::default().fg(subtle))
                    .title(Span::styled(" MiOS sessions ", Style::default().fg(accent).add_modifier(Modifier::BOLD)))
                    .style(Style::default().bg(bg).fg(fg));
                let list = List::new(items)
                    .block(block)
                    .highlight_style(Style::default().bg(accent).fg(bg).add_modifier(Modifier::BOLD))
                    .highlight_symbol(g.pick);
                f.render_stateful_widget(list, body, &mut state);
                f.render_widget(Paragraph::new(g.help).style(Style::default().fg(muted).bg(bg)), help);
            })
            .map_err(|e| e.to_string())?;
        if !event::poll(Duration::from_millis(250)).map_err(|e| e.to_string())? {
            continue;
        }
        let cur = state.selected().unwrap_or(0);
        let page = list_area.height.saturating_sub(2).max(1) as usize;
        let mut activate = false;
        let next = match event::read().map_err(|e| e.to_string())? {
            Event::Key(key) if key.kind == KeyEventKind::Press => match key.code {
                KeyCode::Enter => {
                    activate = true;
                    cur
                }
                KeyCode::Char('n') | KeyCode::Tab => break Ok(Choice::New { node: 0 }),
                KeyCode::Char('r') => {
                    nodes[0] = local_node(c);
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
                    let inside = m.column > list_area.x && m.row > list_area.y && m.row + 1 < list_area.y + list_area.height;
                    let row = state.offset() + m.row.saturating_sub(list_area.y + 1) as usize;
                    if inside && table.get(row).is_some_and(|r| r.0.is_some()) {
                        // A second click on the same row within half a second opens it.
                        activate = last_click.is_some_and(|(r, t)| r == row && t.elapsed() < Duration::from_millis(500));
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
                break Ok(choice);
            }
        }
    };
    drop(guard);
    result.map(|choice| (choice, nodes))
}

fn exec(choice: Choice, nodes: &[Node], c: &Config) -> String {
    let err = match choice {
        Choice::Attach { node, session } if nodes[node].local => Command::new(TERMINAL).args(["--session", &session]).exec(),
        Choice::Workspace => Command::new(TERMINAL).args(["--action", "ai"]).exec(),
        Choice::App { id } => Command::new(TERMINAL).args(["--app", &id, "--console", if c.console { "1" } else { "0" }]).exec(),
        Choice::New { node } if nodes[node].local => Command::new(TERMINAL).arg("--new").exec(),
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
            Command::new(shell).arg("-l").env("MIOS_SKIP_MOTD", "1").exec()
        }
    };
    format!("exec failed: {err}")
}

/// `list` prints the discovery as JSON (scripts, Portal); otherwise run the selector and exec the choice.
pub fn run(root: &Path, list: bool) -> Result<(), String> {
    let c = config(root)?;
    if list {
        let out = serde_json::json!({ "nodes": discover(&c), "apps": c.apps });
        println!("{}", serde_json::to_string_pretty(&out).map_err(|e| e.to_string())?);
        return Ok(());
    }
    let (choice, nodes) = pick(&c)?;
    Err(exec(choice, &nodes, &c))
}

#[cfg(test)]
mod tests {
    #[test]
    fn peer_name_uses_unique_tailnet_name() {
        assert_eq!(super::peer_name("fedora-1.tail1234.ts.net.", "fedora"), "fedora-1");
        assert_eq!(super::peer_name("fedora-2.tail1234.ts.net.", "fedora"), "fedora-2");
    }

    #[test]
    fn peer_name_falls_back_to_hostname_without_dns_name() {
        assert_eq!(super::peer_name("", "fedora"), "fedora");
    }

    use super::*;

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
        }
    }

    #[test]
    fn bounded_run_returns_output_of_a_fast_child() {
        let out = run_bounded(Command::new("sh").args(["-c", "echo hi; echo err >&2"]), Instant::now() + Duration::from_secs(5))
            .expect("fast child completes");
        assert!(out.status.success());
        assert_eq!(out.stdout, b"hi\n");
        assert_eq!(out.stderr, b"err\n");
    }

    #[test]
    fn bounded_run_kills_a_hung_peer_at_the_deadline() {
        // A peer that never answers (sh holding a grandchild sleep) is killed, group and all.
        let start = Instant::now();
        let r = run_bounded(Command::new("sh").args(["-c", "sleep 30; echo late"]), start + Duration::from_millis(300));
        assert_eq!(r.unwrap_err(), "timed out");
        assert!(start.elapsed() < Duration::from_secs(3), "deadline not honoured: {:?}", start.elapsed());
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
        let out = Command::new("sh").args(["-c", &format!("printf %s {word}")]).output().unwrap();
        String::from_utf8(out.stdout).unwrap()
    }

    #[test]
    fn shell_quote_keeps_ordinary_session_names_literal() {
        assert_eq!(shell_quote("tmux-os-2"), "'tmux-os-2'");
        assert_eq!(through_sh(&shell_quote("my session")), "my session");
    }

    #[test]
    fn shell_quote_neutralises_hostile_remote_session_names() {
        let canary = std::env::temp_dir().join(format!("mios-sessions-canary-{}", std::process::id()));
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
        let local = Node { name: "here".into(), address: String::new(), local: true, online: true, error: None, sessions: vec![] };
        assert!(rows(std::slice::from_ref(&local), true, &[], None, &UNICODE).iter().any(|r| r.1.to_string().contains("querying")));
        assert!(!rows(&[local], false, &[], None, &UNICODE).iter().any(|r| r.1.to_string().contains("querying")));
    }

    #[test]
    fn rows_offer_new_session_only_on_reachable_nodes_and_always_a_shell() {
        let node = |online: bool, error: Option<String>| Node {
            name: "n".into(), address: "n.ts".into(), local: false, online, error, sessions: vec![],
        };
        let table = rows(&[node(true, None), node(false, None), node(true, Some("denied".into()))], false, &[], None, &UNICODE);
        let news = table.iter().filter(|r| matches!(r.0, Some(Choice::New { .. }))).count();
        assert_eq!(news, 1);
        assert!(matches!(table.last().unwrap().0, Some(Choice::Shell)));
    }

    fn app(id: &str) -> App {
        App { id: id.into(), label: id.into(), section: "system".into(), mode: "window".into() }
    }

    fn local(sessions: &[&str]) -> Node {
        Node {
            name: "here".into(),
            address: String::new(),
            local: true,
            online: true,
            error: None,
            sessions: sessions.iter().map(|n| Session { name: (*n).into(), windows: 1, attached: false }).collect(),
        }
    }

    fn peer(name: &str) -> Node {
        Node { name: name.into(), address: format!("{name}.ts"), local: false, online: true, error: None, sessions: vec![] }
    }

    #[test]
    fn app_rows_follow_this_node_and_never_move_when_peers_arrive() {
        let apps = [app("btop"), app("top")];
        let before = rows(&[local(&["mios"])], true, &apps, Some("mios"), &UNICODE);
        let after = rows(&[local(&["mios"]), peer("a"), peer("b")], false, &apps, Some("mios"), &UNICODE);
        let pos = |t: &[(Option<Choice>, Line<'static>, Style)], id: &str| t.iter().position(|r| r.0 == Some(Choice::App { id: id.into() })).unwrap();
        assert_eq!(pos(&before, "btop"), pos(&after, "btop"));
        assert!(pos(&after, "top") < after.iter().position(|r| matches!(r.0, Some(Choice::New { node: 1 }))).unwrap());
        assert!(matches!(after.last().unwrap().0, Some(Choice::Shell)));
    }

    #[test]
    fn selection_identity_survives_a_row_shift() {
        let apps = [app("btop")];
        let before = rows(&[local(&[])], true, &apps, None, &UNICODE);
        let shell = Choice::Shell;
        let after = rows(&[local(&[]), peer("a"), peer("b")], false, &apps, None, &UNICODE);
        let i = select_index(&after, Some(&shell), "mios").unwrap();
        assert_eq!(after[i].0, Some(Choice::Shell));
        assert_ne!(select_index(&before, Some(&shell), "mios"), Some(i), "the row index did shift");
    }

    #[test]
    fn hidden_sessions_are_filtered_and_the_ai_workspace_is_offered_when_missing() {
        let c = cfg(3);
        let visible = visible_sessions(&c, parse_sessions("mios\t2\t1\nmios-float-3\t1\t0\nmios-workspace-4\t1\t0\ntmux-os\t1\t0\n"));
        assert_eq!(visible.iter().map(|s| s.name.as_str()).collect::<Vec<_>>(), ["mios", "tmux-os"]);
        let without = rows(&[local(&["tmux-os"])], false, &[], Some("mios"), &UNICODE);
        assert!(without.iter().any(|r| r.0 == Some(Choice::Workspace)));
        let with = rows(&[local(&["mios"])], false, &[], Some("mios"), &UNICODE);
        assert!(!with.iter().any(|r| r.0 == Some(Choice::Workspace)));
    }

    #[test]
    fn controller_vocabulary_navigates_rows_and_sections() {
        let apps = [app("btop"), app("top"), app("vim")];
        let t = rows(&[local(&["mios", "tmux-os"])], false, &apps, Some("mios"), &UNICODE);
        let at = |c: Choice| t.iter().position(|r| r.0.as_ref() == Some(&c)).unwrap();
        let mios = at(Choice::Attach { node: 0, session: "mios".into() });
        assert_eq!(navigate(&t, mios, KeyCode::Down, 5), at(Choice::Attach { node: 0, session: "tmux-os".into() }));
        assert_eq!(navigate(&t, mios, KeyCode::Up, 5), mios, "Up at the top stays");
        assert_eq!(navigate(&t, mios, KeyCode::End, 5), at(Choice::Shell));
        assert_eq!(navigate(&t, at(Choice::Shell), KeyCode::Home, 5), mios);
        assert_eq!(navigate(&t, mios, KeyCode::Char('e'), 5), at(Choice::App { id: "btop".into() }));
        assert_eq!(navigate(&t, mios, KeyCode::BackTab, 5), at(Choice::App { id: "btop".into() }), "Shift-Tab jumps to the next section");
        assert_eq!(navigate(&t, at(Choice::App { id: "btop".into() }), KeyCode::BackTab, 5), at(Choice::Shell));
        assert_eq!(navigate(&t, at(Choice::Shell), KeyCode::BackTab, 5), mios, "wraps to the first section");
        assert_eq!(navigate(&t, mios, KeyCode::Tab, 5), mios, "Tab is handled by pick() as new session");
        assert_eq!(navigate(&t, mios, KeyCode::PageDown, 2), at(Choice::New { node: 0 }));
        assert_eq!(navigate(&t, at(Choice::Shell), KeyCode::PageUp, 100), mios);
        for r in &t {
            assert!(r.0.is_some() || r.1.to_string().contains("this node"));
        }
    }

    #[test]
    fn console_rows_and_help_are_ascii() {
        let t = rows(&[local(&["mios"]), peer("a")], true, &[app("btop")], Some("mios"), &ASCII);
        for r in &t {
            assert!(r.1.to_string().is_ascii(), "{}", r.1);
        }
        assert!(ASCII.help.is_ascii() && ASCII.pick.is_ascii());
        assert!(!UNICODE.help.is_ascii());
    }
}
