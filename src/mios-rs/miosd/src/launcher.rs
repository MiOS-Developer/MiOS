// AI-hint: miosd launcher: the tmux-os launcher for everything -- one organised, centred popup (grouped sections with headers, an aligned key column, two columns where wide enough) over running instances, AI and agents, every installed TUI and the pane/window/session menu rows; keyboard, mouse (right-click = options) and controller vocabulary; instance management (go to, pop out to its own desktop window, float/tile, move to window/session, rename, close); opened by the prefix keys, the controller's tmux:menu and the desktop CTRL+SUPER chord.
// AI-related: usr/share/mios/mios.toml, tools/native/mios-unit-gen/src/tmux_os.rs, tools/native/mios-gen/src/tmux_os.rs, usr/libexec/mios/mios-terminal, src/mios-rs/miosd/src/sessions.rs
//! `miosd launcher`: the tmux-os launcher.
//!
//! `open` runs on the tmux server (a `run-shell -b` job of the prefix keys) or from the desktop chord: it sizes and
//! opens a centred `display-popup` on one client, running `pick` inside it. `pick` is the TUI; it changes nothing
//! itself. It writes the chosen steps (tmux commands, an executor to exec, a desktop window to spawn) to a private
//! result file, exits (the popup closes), and `open` runs them on the same client. So a choice that opens a popup or
//! a menu (a popup TUI, the float shell, a kill confirmation) works, and every step names its client explicitly.

use std::collections::{BTreeMap, BTreeSet};
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use ratatui::buffer::Buffer;
use ratatui::crossterm::event::{
    self, EnableMouseCapture, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers, MouseButton,
    MouseEventKind,
};
use ratatui::crossterm::execute;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use serde::{Deserialize, Serialize};
use toml::Value;

use mios_resolver::terminal_tuis;
use mios_unit_gen::tmux_os::{self as policy, ItemTarget};

/// Resolved desktop application label and command arguments.
type AppInvocation = (String, Vec<String>);

const TERMINAL: &str = "/usr/libexec/mios/mios-terminal";
/// Never a launcher key: closes the launcher, as in every tmux menu.
const CLOSE_KEY: char = 'q';
/// Keys handed to rows without a key of their own (apps), in order.
const POOL: &str = "1234567890abcdefghijklnoprsuvwxyz";
/// Everything `list-panes -a` reports for the running section.
const PANES: &str = "#{session_id}\t#{session_name}\t#{window_id}\t#{window_index}\t#{window_name}\t#{window_panes}\t#{pane_id}\t#{pane_active}\t#{pane_floating_flag}\t#{pane_current_command}\t#{@mios-float-parent}\t#{@mios-float-origin}\t#{E:@mios-managed-pane}\t#{session_attached}";

#[derive(clap::Subcommand, Debug)]
pub enum LauncherCommand {
    /// Open the launcher popup on a tmux client (the prefix keys run this with TMUX and TMUX_PANE set)
    Open {
        #[arg(long, default_value = "/")]
        root: String,
        /// The tmux client, as #{q:client_name} gives it (a tty path or client-PID)
        #[arg(long)]
        client: String,
        /// 1 on a Linux console client (named colours, ASCII glyphs, argv_vt TUIs)
        #[arg(long, default_value = "0")]
        console: String,
        /// The section to start on (a [keybindings.tmux.launcher].sections group id, or an apps section)
        #[arg(long)]
        start: Option<String>,
    },
    /// The launcher itself, inside the popup (started by `open`)
    Pick {
        #[arg(long, default_value = "/")]
        root: String,
        #[arg(long)]
        client: String,
        #[arg(long)]
        pane: String,
        #[arg(long, default_value = "0")]
        console: String,
        #[arg(long)]
        start: Option<String>,
        /// Where the chosen steps go (created 0600 by `open`)
        #[arg(long)]
        result: PathBuf,
    },
    /// The desktop chord (CTRL+SUPER): the focused terminal's tmux-os client, else a new terminal that opens it
    Desktop {
        #[arg(long, default_value = "/")]
        root: String,
    },
    /// Print the launcher's sections and rows for a client as JSON (scripts, tests); changes nothing
    List {
        #[arg(long, default_value = "/")]
        root: String,
        #[arg(long)]
        client: Option<String>,
        #[arg(long)]
        pane: Option<String>,
        #[arg(long, default_value = "0")]
        console: String,
        /// Lay out for this many columns and print the rendered screen too
        #[arg(long)]
        width: Option<u16>,
        #[arg(long, default_value = "30")]
        height: u16,
    },
}

// ---- configuration -------------------------------------------------------------------------------------------

#[derive(Clone, Debug)]
pub struct Cfg {
    pub enabled: bool,
    pub command_timeout: Duration,
    pub popup_policy: mios_resolver::popup::PopupPolicy,
    pub title: String,
    pub popup_w: u32,
    pub popup_h: u32,
    pub min_w: u16,
    pub min_h: u16,
    pub two_col: u16,
    pub gap: u16,
    pub key_w: u16,
    pub detail_min: u16,
    pub instance_limit: usize,
    pub start_section: String,
    pub popout_prefix: String,
    pub desktop_terminal: Vec<String>,
    pub sections: Vec<String>,
    pub menu_sections: Vec<String>,
    pub titles: BTreeMap<String, String>,
    pub skip_keys: BTreeSet<String>,
    pub empty_running: String,
    pub help: String,
    pub options_title: String,
    pub confirm_close: String,
    pub move_window_title: String,
    pub move_session_title: String,
    pub new_session_label: String,
    pub labels: BTreeMap<String, String>,
    pub popped_message: String,
    pub too_small: String,
    pub prefix: String,
    pub hidden: Vec<String>,
    pub float_prefix: String,
    pub new_session_name: String,
    pub socket: String,
    pub socket_root: String,
    pub console: bool,
    /// bg, fg, accent (selection), subtle (borders, rules), muted (help, disabled)
    pub palette: [Color; 5],
}

fn at<'a>(v: &'a Value, path: &str) -> Result<&'a Value, String> {
    path.split('.')
        .try_fold(v, |cur, k| cur.get(k))
        .ok_or_else(|| format!("missing SSOT key {path}"))
}

fn s(v: &Value, path: &str) -> Result<String, String> {
    at(v, path)?
        .as_str()
        .map(str::to_owned)
        .ok_or_else(|| format!("{path} must be a string"))
}

/// Shown text: printable ASCII, no format or quoting characters (the same rule as every tmux-shown SSOT string).
fn text(v: &Value, path: &str) -> Result<String, String> {
    let t = s(v, path)?;
    if !policy::plain_text(&t) {
        return Err(format!("{path} must be plain ASCII text"));
    }
    Ok(t)
}

fn int(v: &Value, path: &str, range: std::ops::RangeInclusive<i64>) -> Result<i64, String> {
    at(v, path)?
        .as_integer()
        .filter(|n| range.contains(n))
        .ok_or_else(|| {
            format!(
                "{path} must be an integer in {}..={}",
                range.start(),
                range.end()
            )
        })
}

fn strings(v: &Value, path: &str) -> Result<Vec<String>, String> {
    at(v, path)?
        .as_array()
        .ok_or_else(|| format!("{path} must be an array"))?
        .iter()
        .map(|x| {
            x.as_str()
                .map(str::to_owned)
                .ok_or_else(|| format!("{path} must hold strings"))
        })
        .collect()
}

fn percent(v: &Value, path: &str) -> Result<u32, String> {
    let t = s(v, path)?;
    t.strip_suffix('%')
        .and_then(|n| n.parse::<u32>().ok())
        .filter(|n| (1..=100).contains(n))
        .ok_or_else(|| format!("{path} must be a percentage 1%..100%"))
}

fn ident(x: &str) -> bool {
    !x.is_empty()
        && x.len() <= 32
        && x.bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
}

pub const LABEL_KEYS: &[&str] = &[
    "go",
    "popout",
    "float",
    "dock",
    "move_window",
    "move_session",
    "rename",
    "close",
    "yes",
    "no",
    "open",
    "open_popup",
    "open_window",
    "open_float",
    "open_desktop",
    "new_window",
    "run",
    "cancel",
    "find",
    "find_hint",
    "find_help",
    "no_matches",
    "help_compact",
    "options_help",
];

pub fn config(v: &Value, console: bool) -> Result<Cfg, String> {
    let l = "keybindings.tmux.launcher";
    let titles = at(v, &format!("{l}.section_titles"))?
        .as_table()
        .ok_or("keybindings.tmux.launcher.section_titles must be a table")?
        .iter()
        .map(|(k, t)| {
            t.as_str()
                .filter(|t| policy::plain_text(t))
                .map(|t| (k.clone(), t.to_owned()))
                .ok_or_else(|| format!("{l}.section_titles.{k} must be plain ASCII text"))
        })
        .collect::<Result<BTreeMap<_, _>, _>>()?;
    let labels_t = at(v, &format!("{l}.labels"))?
        .as_table()
        .ok_or("keybindings.tmux.launcher.labels must be a table")?;
    let mut labels = BTreeMap::new();
    for k in LABEL_KEYS {
        let t = labels_t
            .get(*k)
            .and_then(Value::as_str)
            .filter(|t| policy::plain_text(t))
            .ok_or_else(|| format!("{l}.labels.{k} must be plain ASCII text"))?;
        labels.insert((*k).to_owned(), t.to_owned());
    }
    let sections = strings(v, &format!("{l}.sections"))?;
    if sections.is_empty()
        || sections
            .iter()
            .any(|x| !["running", "ai", "apps", "menus"].contains(&x.as_str()))
    {
        return Err(format!(
            "{l}.sections may list only running, ai, apps and menus"
        ));
    }
    let menu_sections = strings(v, &format!("{l}.menu_sections"))?;
    if menu_sections.iter().any(|m| !ident(m)) {
        return Err(format!("{l}.menu_sections must be menu ids"));
    }
    let desktop_terminal = strings(v, &format!("{l}.desktop_terminal"))?;
    if desktop_terminal.is_empty()
        || desktop_terminal
            .iter()
            .any(|a| a.is_empty() || a.chars().any(char::is_control))
    {
        return Err(format!("{l}.desktop_terminal must be a non-empty argv"));
    }
    let popout_prefix = s(v, &format!("{l}.popout_session_prefix"))?;
    if popout_prefix.is_empty()
        || popout_prefix.len() > 16
        || !popout_prefix
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
    {
        return Err(format!(
            "{l}.popout_session_prefix must be 1-16 of [A-Za-z0-9._-]"
        ));
    }
    let new_session_name = s(v, "terminal.sessions.new_session_name")?;
    if new_session_name.is_empty()
        || !new_session_name
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
    {
        return Err("[terminal.sessions].new_session_name must be [A-Za-z0-9._-]".into());
    }
    let prefix = s(v, "keybindings.tmux_prefix")?;
    if prefix.is_empty()
        || !prefix
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-')
    {
        return Err("[keybindings].tmux_prefix is not a tmux key name".into());
    }
    let palette = if console {
        [
            Color::Black,
            Color::White,
            Color::Yellow,
            Color::Gray,
            Color::DarkGray,
        ]
    } else {
        let c = |k: &str| crate::sessions::color(&s(v, &format!("colors.{k}")).unwrap_or_default());
        [c("bg"), c("fg"), c("cursor"), c("subtle"), c("muted")]
    };
    let cfg = Cfg {
        command_timeout: Duration::from_millis(int(
            v,
            "keybindings.tmux.command_timeout_ms",
            100..=60000,
        )? as u64),
        popup_policy: mios_resolver::popup::PopupPolicy::from_value(v)?,
        enabled: at(v, &format!("{l}.enabled"))?
            .as_bool()
            .ok_or("keybindings.tmux.launcher.enabled must be a boolean")?,
        title: text(v, &format!("{l}.title"))?,
        popup_w: percent(v, &format!("{l}.popup_width"))?,
        popup_h: percent(v, &format!("{l}.popup_height"))?,
        min_w: int(v, &format!("{l}.min_width"), 20..=500)? as u16,
        min_h: int(v, &format!("{l}.min_height"), 6..=500)? as u16,
        two_col: int(v, &format!("{l}.two_column_min_width"), 40..=1000)? as u16,
        gap: int(v, &format!("{l}.column_gap"), 1..=16)? as u16,
        key_w: int(v, &format!("{l}.key_width"), 1..=8)? as u16,
        detail_min: int(v, &format!("{l}.detail_min_width"), 10..=1000)? as u16,
        instance_limit: int(v, &format!("{l}.instance_limit"), 1..=1000)? as usize,
        start_section: s(v, &format!("{l}.start_section"))?,
        popout_prefix,
        desktop_terminal,
        sections,
        menu_sections,
        titles,
        skip_keys: strings(v, &format!("{l}.skip_menu_keys"))?
            .into_iter()
            .collect(),
        empty_running: text(v, &format!("{l}.empty_running"))?,
        help: text(
            v,
            &format!("{l}.{}", if console { "help_vt" } else { "help" }),
        )?,
        options_title: text(v, &format!("{l}.options_title"))?,
        confirm_close: text(v, &format!("{l}.confirm_close"))?,
        move_window_title: text(v, &format!("{l}.move_window_title"))?,
        move_session_title: text(v, &format!("{l}.move_session_title"))?,
        new_session_label: text(v, &format!("{l}.new_session_label"))?,
        labels,
        popped_message: text(v, &format!("{l}.popped_message"))?,
        too_small: text(v, "keybindings.tmux.menu_too_small_message")?,
        prefix,
        hidden: policy::hidden_session_prefixes(v)?,
        float_prefix: s(v, "keybindings.tmux.float.session_prefix")?,
        new_session_name,
        socket: s(v, "keybindings.socket_name")?,
        socket_root: s(v, "terminal.socket_root")?,
        console,
        palette,
    };
    if cfg.min_w > cfg.two_col {
        return Err(format!(
            "{l}.min_width must not exceed two_column_min_width"
        ));
    }
    Ok(cfg)
}

impl Cfg {
    fn label(&self, k: &str) -> String {
        self.labels.get(k).cloned().unwrap_or_else(|| k.to_owned())
    }
    fn title_of(&self, id: &str) -> String {
        self.titles.get(id).cloned().unwrap_or_else(|| {
            let mut c = id.chars();
            c.next()
                .map(|f| f.to_ascii_uppercase().to_string() + c.as_str())
                .unwrap_or_default()
        })
    }
}

// ---- model ---------------------------------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Kind {
    /// A window of a visible session (its active tiled pane carries the pane-level options).
    Window,
    /// A native floating pane (tmux 3.7) in a visible session.
    Floating,
    /// A pane inside a session's float shell (a hidden mios-float-* session).
    FloatShell,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Instance {
    pub kind: Kind,
    pub session_id: String,
    pub session: String,
    pub window_id: String,
    pub window_index: String,
    pub window: String,
    pub pane_id: String,
    pub panes: u32,
    pub command: String,
    pub managed: bool,
    /// A float-shell pane lifted from a tiled window (it docks back there).
    pub origin: bool,
    /// The parent session id of a float-shell pane.
    pub parent: String,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
#[serde(tag = "type", rename_all = "snake_case")]
pub enum Target {
    Instance(Instance),
    App {
        id: String,
    },
    /// A prefix-table key, run as if typed on the client.
    Prefix {
        key: String,
    },
    /// A row of a generated menu, run through its key table.
    MenuRow {
        menu: String,
        key: String,
    },
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Item {
    pub label: String,
    pub detail: String,
    pub key: Option<char>,
    pub target: Target,
    pub enabled: bool,
}

#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Section {
    pub id: String,
    pub title: String,
    pub items: Vec<Item>,
    /// Shown (dim) when the section has no rows.
    pub empty: Option<String>,
}

/// The launching pane's state (it decides which menu rows are enabled).
#[derive(Clone, Debug, Default, PartialEq)]
pub struct PaneState {
    pub copy_mode: bool,
    pub managed: bool,
    pub session_id: String,
}

fn clean(t: &str, max: usize) -> String {
    let t: String = t
        .chars()
        .map(|c| if c.is_control() { ' ' } else { c })
        .collect();
    let t = t.trim();
    if t.chars().count() > max {
        t.chars()
            .take(max.saturating_sub(1))
            .chain(std::iter::once('~'))
            .collect()
    } else {
        t.to_owned()
    }
}

/// Running instances from one `list-panes -a -F PANES` reply: windows of visible sessions (each with its active tiled
/// pane), native floating panes, and float-shell panes. Hidden sessions other than float shells never appear.
pub fn parse_running(out: &str, cfg: &Cfg) -> Vec<Item> {
    struct Row<'a> {
        sid: &'a str,
        session: &'a str,
        wid: &'a str,
        widx: &'a str,
        wname: &'a str,
        panes: u32,
        pid: &'a str,
        active: bool,
        floating: bool,
        cmd: &'a str,
        parent: &'a str,
        origin: bool,
        managed: bool,
    }
    let rows: Vec<Row> = out
        .lines()
        .filter_map(|l| {
            let f: Vec<&str> = l.split('\t').collect();
            (f.len() >= 13).then(|| Row {
                sid: f[0],
                session: f[1],
                wid: f[2],
                widx: f[3],
                wname: f[4],
                panes: f[5].parse().unwrap_or(1),
                pid: f[6],
                active: f[7] == "1",
                floating: f[8] == "1",
                cmd: f[9],
                parent: f[10],
                origin: !f[11].is_empty(),
                managed: f[12] == "1",
            })
        })
        .filter(|r| r.pid.starts_with('%') && r.wid.starts_with('@') && r.sid.starts_with('$'))
        .collect();
    let names: BTreeMap<&str, &str> = rows.iter().map(|r| (r.sid, r.session)).collect();
    let inst = |r: &Row, kind: Kind| Instance {
        kind,
        session_id: r.sid.into(),
        session: r.session.into(),
        window_id: r.wid.into(),
        window_index: r.widx.into(),
        window: clean(r.wname, 40),
        pane_id: r.pid.into(),
        panes: r.panes,
        command: clean(r.cmd, 24),
        managed: r.managed,
        origin: r.origin,
        parent: r.parent.into(),
    };
    let mut items = Vec::new();
    let mut seen_windows = BTreeSet::new();
    for r in &rows {
        let float_shell = r.session.starts_with(&cfg.float_prefix);
        if float_shell {
            let parent = names.get(r.parent).copied().unwrap_or("?");
            items.push(Item {
                label: clean(r.cmd, 40),
                detail: format!("float shell of {}", clean(parent, 20)),
                key: None,
                target: Target::Instance(inst(r, Kind::FloatShell)),
                enabled: true,
            });
            continue;
        }
        if policy::is_hidden_session(&cfg.hidden, r.session) {
            continue;
        }
        if r.floating {
            items.push(Item {
                label: clean(r.cmd, 40),
                detail: format!("floating in {}:{}", clean(r.session, 20), r.widx),
                key: None,
                target: Target::Instance(inst(r, Kind::Floating)),
                enabled: true,
            });
            continue;
        }
        if seen_windows.contains(r.wid) {
            continue;
        }
        // The window's row carries its active tiled pane (the first tiled pane when a floating pane is active).
        let tiled: Vec<&Row> = rows
            .iter()
            .filter(|x| x.wid == r.wid && !x.floating)
            .collect();
        let pane = tiled
            .iter()
            .find(|x| x.active)
            .or_else(|| tiled.first())
            .copied()
            .unwrap_or(r);
        seen_windows.insert(r.wid);
        let tiled_n = tiled.len() as u32;
        let more = if tiled_n > 1 {
            format!("  {tiled_n} panes")
        } else {
            String::new()
        };
        let mut i = inst(pane, Kind::Window);
        i.panes = tiled_n.max(1);
        items.push(Item {
            label: clean(r.wname, 40),
            detail: format!("{}:{}{more}", clean(r.session, 20), r.widx),
            key: None,
            target: Target::Instance(i),
            enabled: true,
        });
    }
    items.truncate(cfg.instance_limit);
    items
}

/// The rows of the AI section: [[keybindings.actions]] (label, prefix key).
pub fn ai_items(v: &Value) -> Result<Vec<Item>, String> {
    let mut out = Vec::new();
    for a in at(v, "keybindings.actions")?
        .as_array()
        .ok_or("keybindings.actions must be an array")?
    {
        let label = a
            .get("label")
            .and_then(Value::as_str)
            .filter(|t| policy::plain_text(t))
            .ok_or("keybindings.actions label must be plain text")?;
        let key = a
            .get("key")
            .and_then(Value::as_str)
            .filter(|k| policy::valid_key(k))
            .ok_or("keybindings.actions key is not a tmux key")?;
        out.push(Item {
            label: label.into(),
            detail: String::new(),
            key: single(key),
            target: Target::Prefix { key: key.into() },
            enabled: true,
        });
    }
    Ok(out)
}

fn single(key: &str) -> Option<char> {
    let mut c = key.chars();
    match (c.next(), c.next()) {
        (Some(ch), None) if ch.is_ascii_graphic() => Some(ch),
        _ => None,
    }
}

/// One section per installed-and-visible TUI section, in [terminal.tui_launcher].sections order.
pub fn app_sections(
    v: &Value,
    cfg: &Cfg,
    is_exec: impl Fn(&str) -> bool,
) -> Result<Vec<Section>, String> {
    let l = terminal_tuis::launcher(v)?;
    if !l.enabled {
        return Ok(Vec::new());
    }
    let cat = terminal_tuis::catalog(v)?;
    let tuis = terminal_tuis::visible(&cat, cfg.console, is_exec);
    Ok(l.sections
        .iter()
        .filter_map(|sec| {
            let items: Vec<Item> = tuis
                .iter()
                .filter(|t| &t.section == sec)
                .map(|t| Item {
                    label: t.label.clone(),
                    detail: t.mode.as_str().into(),
                    key: None,
                    target: Target::App { id: t.id.clone() },
                    enabled: true,
                })
                .collect();
            (!items.is_empty()).then(|| Section {
                id: sec.clone(),
                title: cfg.title_of(sec),
                items,
                empty: None,
            })
        })
        .collect())
}

/// The menu sections: the rows of each generated menu named in menu_sections, as the key tables bind them.
pub fn menu_sections(v: &Value, cfg: &Cfg, pane: &PaneState) -> Result<Vec<Section>, String> {
    let menus = policy::launcher_menus(v, "linux").map_err(|e| e.to_string())?;
    let mut out = Vec::new();
    // A row several menus share (Detach, New window) is listed once, in the first section that has it.
    let mut shown: BTreeSet<(String, String)> = BTreeSet::new();
    for id in &cfg.menu_sections {
        let m = menus
            .iter()
            .find(|m| &m.id == id)
            .ok_or_else(|| format!("keybindings.tmux.launcher.menu_sections: no menu {id:?}"))?;
        let items: Vec<Item> = m
            .items
            .iter()
            .filter(|it| !matches!(it.target, ItemTarget::Separator | ItemTarget::Header))
            .filter(|it| !cfg.skip_keys.contains(&it.key))
            .filter(|it| shown.insert((it.label.clone(), it.key.clone())))
            .filter(|it| !matches!(&it.target, ItemTarget::Command(c) if c.is_empty()))
            .map(|it| Item {
                label: it.label.trim_end_matches(" >").to_owned(),
                detail: String::new(),
                key: single(&it.key),
                target: Target::MenuRow {
                    menu: id.clone(),
                    key: it.key.clone(),
                },
                enabled: (!it.copy_only || pane.copy_mode) && !(it.managed_disable && pane.managed),
            })
            .collect();
        if !items.is_empty() {
            out.push(Section {
                id: id.clone(),
                title: cfg.title_of(id),
                items,
                empty: None,
            });
        }
    }
    Ok(out)
}

/// The launcher's sections in [keybindings.tmux.launcher].sections order, with unique keys: prefix and menu keys
/// keep their own key where it is still free, apps take the next free key of POOL, `q` is never a key.
pub fn build(
    cfg: &Cfg,
    running: Vec<Item>,
    ai: Vec<Item>,
    apps: Vec<Section>,
    menus: Vec<Section>,
) -> Vec<Section> {
    let mut out = Vec::new();
    let (mut ai, mut apps, mut menus, mut running) =
        (Some(ai), Some(apps), Some(menus), Some(running));
    for g in &cfg.sections {
        match g.as_str() {
            "running" => {
                if let Some(r) = running.take() {
                    out.push(Section {
                        id: "running".into(),
                        title: cfg.title_of("running"),
                        items: r,
                        empty: Some(cfg.empty_running.clone()),
                    })
                }
            }
            "ai" => {
                if let Some(a) = ai.take().filter(|a| !a.is_empty()) {
                    out.push(Section {
                        id: "ai".into(),
                        title: cfg.title_of("ai"),
                        items: a,
                        empty: None,
                    })
                }
            }
            "apps" => out.extend(apps.take().unwrap_or_default()),
            "menus" => out.extend(menus.take().unwrap_or_default()),
            _ => {}
        }
    }
    let mut used: BTreeSet<char> = BTreeSet::from([CLOSE_KEY, '/']);
    // Own keys first, in order, so the familiar prefix keys win; then the pool for the rest.
    for sec in out.iter_mut() {
        for it in sec.items.iter_mut() {
            if let Some(k) = it.key {
                if !used.insert(k) {
                    it.key = None;
                }
            }
        }
    }
    let mut pool = POOL
        .chars()
        .filter(|c| !used.contains(c))
        .collect::<Vec<_>>()
        .into_iter();
    for sec in out.iter_mut() {
        for it in sec
            .items
            .iter_mut()
            .filter(|i| matches!(i.target, Target::App { .. }))
        {
            if it.key.is_none() {
                it.key = pool.next();
            }
        }
    }
    out
}

// ---- layout --------------------------------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Line {
    Header(usize),
    Item(usize, usize),
    Note(usize),
    Blank,
}

#[derive(Clone, Debug)]
pub struct Layout {
    pub cols: Vec<Vec<Line>>,
    pub col_w: u16,
    pub col_x: Vec<u16>,
    /// Top row of the section area and its height.
    pub top: u16,
    pub height: u16,
}

fn block(si: usize, s: &Section) -> Vec<Line> {
    let mut v = vec![Line::Header(si)];
    if s.items.is_empty() {
        if s.empty.is_some() {
            v.push(Line::Note(si));
        }
    } else {
        v.extend((0..s.items.len()).map(|i| Line::Item(si, i)));
    }
    v
}

/// Sections flow into two columns once the area is two_column_min_width wide; a section is never split, and the
/// split point is the section boundary that balances the two columns best.
pub fn layout(sections: &[Section], area: Rect, cfg: &Cfg) -> Layout {
    let blocks: Vec<Vec<Line>> = sections
        .iter()
        .enumerate()
        .map(|(i, s)| block(i, s))
        .filter(|b| b.len() > 1)
        .collect();
    let join = |bs: &[Vec<Line>]| -> Vec<Line> {
        let mut v = Vec::new();
        for (i, b) in bs.iter().enumerate() {
            if i > 0 {
                v.push(Line::Blank);
            }
            v.extend(b.iter().copied());
        }
        v
    };
    let inner_w = area.width.saturating_sub(2);
    let top = area.y + 2;
    let height = area.height.saturating_sub(4).max(1);
    if inner_w >= cfg.two_col && blocks.len() > 1 {
        let total: usize = blocks.iter().map(Vec::len).sum::<usize>() + blocks.len() - 1;
        let mut best = (usize::MAX, 1);
        for split in 1..blocks.len() {
            let left = join(&blocks[..split]).len();
            let right = total - left - 1;
            let tall = left.max(right);
            if tall < best.0 {
                best = (tall, split);
            }
        }
        let col_w = (inner_w - cfg.gap) / 2;
        Layout {
            cols: vec![join(&blocks[..best.1]), join(&blocks[best.1..])],
            col_w,
            col_x: vec![area.x + 1, area.x + 1 + col_w + cfg.gap],
            top,
            height,
        }
    } else {
        Layout {
            cols: vec![join(&blocks)],
            col_w: inner_w,
            col_x: vec![area.x + 1],
            top,
            height,
        }
    }
}

/// Selectable rows in reading order: (column, line).
pub fn order(sections: &[Section], lay: &Layout) -> Vec<(usize, usize)> {
    let mut v = Vec::new();
    for (c, col) in lay.cols.iter().enumerate() {
        for (l, line) in col.iter().enumerate() {
            if let Line::Item(si, ii) = line {
                if sections[*si].items[*ii].enabled {
                    v.push((c, l));
                }
            }
        }
    }
    v
}

// ---- input ---------------------------------------------------------------------------------------------------

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Input {
    Up,
    Down,
    Left,
    Right,
    PageUp,
    PageDown,
    Home,
    End,
    NextSection,
    Activate,
    Options,
    Back,
    Search,
    Query(char),
    EraseQuery,
    ClearQuery,
    Key(char),
    Click { x: u16, y: u16, right: bool },
    Wheel { up: bool },
    Nothing,
}

/// One key event as launcher input. The controller's nav projection arrives as these keys: d-pad = arrows, A =
/// Enter (held: Shift+Enter = options), B = Esc (held: Ctrl+C), X/West = Tab (options) / Shift+Tab (next section),
/// the triggers = the prefix plus p / n (tmux:key p|n, paging here), Guide = the prefix plus menu_key (closes).
pub fn map_key(k: KeyEvent, prefix: &str, pending: &mut bool) -> Input {
    let ctrl = k.modifiers.contains(KeyModifiers::CONTROL);
    let prefix_char = prefix.strip_prefix("C-").and_then(single);
    if *pending {
        *pending = false;
        return match k.code {
            KeyCode::Char('p') | KeyCode::PageUp => Input::PageUp,
            KeyCode::Char('n') | KeyCode::PageDown => Input::PageDown,
            KeyCode::Char(' ') | KeyCode::Char('e') | KeyCode::Esc => Input::Back,
            _ => Input::Nothing,
        };
    }
    match k.code {
        KeyCode::Char(c) if ctrl && Some(c) == prefix_char => {
            *pending = true;
            Input::Nothing
        }
        KeyCode::Char('c') if ctrl => Input::Back,
        KeyCode::Char('f') if ctrl => Input::Search,
        KeyCode::Char('/') if !ctrl => Input::Search,
        KeyCode::Up => Input::Up,
        KeyCode::Down => Input::Down,
        KeyCode::Left => Input::Left,
        KeyCode::Right => Input::Right,
        KeyCode::PageUp => Input::PageUp,
        KeyCode::PageDown => Input::PageDown,
        KeyCode::Home => Input::Home,
        KeyCode::End => Input::End,
        KeyCode::BackTab => Input::NextSection,
        KeyCode::Tab | KeyCode::Menu => Input::Options,
        KeyCode::F(10) if k.modifiers.contains(KeyModifiers::SHIFT) => Input::Options,
        KeyCode::Enter
            if k.modifiers
                .intersects(KeyModifiers::SHIFT | KeyModifiers::ALT) =>
        {
            Input::Options
        }
        KeyCode::Enter | KeyCode::Char(' ') => Input::Activate,
        KeyCode::Esc => Input::Back,
        KeyCode::Char(c) if c == CLOSE_KEY && !ctrl => Input::Back,
        KeyCode::Char(c)
            if !ctrl && !k.modifiers.contains(KeyModifiers::ALT) && c.is_ascii_graphic() =>
        {
            Input::Key(c)
        }
        _ => Input::Nothing,
    }
}

// ---- steps ---------------------------------------------------------------------------------------------------

/// What `open` runs on the client once the popup has closed.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
pub enum Op {
    /// `tmux -S SOCKET ARGS...`
    Tmux { args: Vec<String> },
    /// exec ARGV with TMUX_PANE=pane (a MiOS-Tmux executor that may open a popup itself); always the last step.
    Exec { argv: Vec<String>, pane: String },
    /// A new desktop terminal attached to `session` (desktop_terminal + --session); without a desktop, a message.
    Desktop { session: String },
}

fn tmux_op<S: AsRef<str>>(args: &[S]) -> Op {
    Op::Tmux {
        args: args.iter().map(|a| a.as_ref().to_owned()).collect(),
    }
}

#[derive(Clone, Debug)]
pub struct Ctx {
    pub client: String,
    pub pane: String,
    pub console: bool,
    pub state: PaneState,
    /// Visible sessions: (id, name).
    pub sessions: Vec<(String, String)>,
    /// Every session name (hidden ones too): pop-out names must be new.
    pub all_session_names: Vec<String>,
    pub running: Vec<Instance>,
}

impl Ctx {
    fn console_flag(&self) -> &'static str {
        if self.console {
            "1"
        } else {
            "0"
        }
    }
}

/// A free session name: base (sanitised), then base-2, base-3, ...
pub fn fresh_name(base: &str, taken: &[String]) -> String {
    let mut b: String = base
        .chars()
        .map(|c| {
            if c.is_ascii_alphanumeric() || "._-".contains(c) {
                c.to_ascii_lowercase()
            } else {
                '-'
            }
        })
        .collect();
    b = b.trim_matches('-').chars().take(28).collect();
    if b.is_empty() {
        b = "instance".into();
    }
    let mut name = b.clone();
    let mut n = 2;
    while taken.contains(&name) {
        name = format!("{b}-{n}");
        n += 1;
    }
    name
}

/// Moves an instance into a new session NAME: a placeholder window holds the session open for the move and is closed
/// right after it, so NAME ends up holding only the instance.
fn into_new_session(i: &Instance, name: &str) -> Vec<Op> {
    let moved = if i.kind == Kind::Window {
        i.window_id.as_str()
    } else {
        i.pane_id.as_str()
    };
    let mut v = vec![tmux_op(&[
        "new-session",
        "-d",
        "-s",
        name,
        "-e",
        "MIOS_SESSION_BARE=1",
        "sleep",
        "30",
    ])];
    if i.kind == Kind::Window {
        v.push(tmux_op(&[
            "move-window",
            "-s",
            &i.window_id,
            "-t",
            &format!("={name}:"),
        ]));
    } else {
        v.push(tmux_op(&[
            "break-pane",
            "-d",
            "-s",
            &i.pane_id,
            "-t",
            &format!("={name}:"),
        ]));
    }
    v.push(tmux_op(&["kill-window", "-a", "-t", moved]));
    v.push(tmux_op(&[
        "set-option",
        "-t",
        &format!("={name}:"),
        "@mios-bare",
        "1",
    ]));
    v
}

fn exec_terminal(args: &[&str], pane: &str) -> Op {
    Op::Exec {
        argv: std::iter::once(TERMINAL)
            .chain(args.iter().copied())
            .map(str::to_owned)
            .collect(),
        pane: pane.to_owned(),
    }
}

/// What Enter (or a click) does on a row.
pub fn activate(t: &Target, cfg: &Cfg, ctx: &Ctx) -> Vec<Op> {
    match t {
        Target::Prefix { key } => vec![tmux_op(&[
            "send-keys",
            "-c",
            &ctx.client,
            "-K",
            &cfg.prefix,
            key,
        ])],
        Target::MenuRow { menu, key } => vec![
            tmux_op(&[
                "switch-client",
                "-c",
                &ctx.client,
                "-T",
                &policy::menu_table(menu),
            ]),
            tmux_op(&["send-keys", "-c", &ctx.client, "-K", key]),
        ],
        Target::App { id } => vec![exec_terminal(
            &[
                "--app",
                id,
                "--client",
                &ctx.client,
                "--console",
                ctx.console_flag(),
            ],
            &ctx.pane,
        )],
        Target::Instance(i) => go_to(i, ctx),
    }
}

fn go_to(i: &Instance, ctx: &Ctx) -> Vec<Op> {
    match i.kind {
        Kind::FloatShell => {
            // Show the float shell of the pane's parent session: switch there first unless the client is in it.
            let parent_pane = ctx
                .running
                .iter()
                .find(|r| r.kind == Kind::Window && r.session_id == i.parent)
                .map(|r| r.pane_id.clone());
            let mut v = Vec::new();
            let pane = if i.parent == ctx.state.session_id || parent_pane.is_none() {
                ctx.pane.clone()
            } else {
                let p = parent_pane.unwrap_or_default();
                v.push(tmux_op(&["switch-client", "-c", &ctx.client, "-t", &p]));
                p
            };
            v.push(exec_terminal(
                &[
                    "--float",
                    "shell",
                    "--client",
                    &ctx.client,
                    "--console",
                    ctx.console_flag(),
                ],
                &pane,
            ));
            v
        }
        _ => vec![tmux_op(&[
            "switch-client",
            "-c",
            &ctx.client,
            "-t",
            &i.pane_id,
        ])],
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum Choice {
    Ops(Vec<Op>),
    /// Open another overlay (close confirmation, move targets).
    Confirm(Instance),
    MoveWindow(Instance),
    MoveSession(Instance),
    Cancel,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Entry {
    pub label: String,
    pub enabled: bool,
    pub choice: Choice,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Overlay {
    pub title: String,
    pub entries: Vec<Entry>,
    pub sel: usize,
    /// First entry shown: a list longer than the popup scrolls with the selection.
    pub offset: usize,
}

impl Overlay {
    fn new(title: String, entries: Vec<Entry>, start: usize) -> Self {
        let sel = (start..entries.len())
            .chain(0..start)
            .find(|&i| entries[i].enabled)
            .unwrap_or(0);
        Overlay {
            title,
            entries,
            sel,
            offset: 0,
        }
    }
    fn step(&mut self, down: bool) {
        let n = self.entries.len();
        if n == 0 {
            return;
        }
        let mut i = self.sel;
        for _ in 0..n {
            i = if down { (i + 1) % n } else { (i + n - 1) % n };
            if self.entries[i].enabled {
                self.sel = i;
                return;
            }
        }
    }
}

fn entry(label: String, enabled: bool, choice: Choice) -> Entry {
    Entry {
        label,
        enabled,
        choice,
    }
}

/// The options of a row (right-click, Tab, Shift+Enter): instance management, or how to open an app.
pub fn options(it: &Item, cfg: &Cfg, ctx: &Ctx, app_argv: Option<(&str, Vec<String>)>) -> Overlay {
    let l = |k: &str| cfg.label(k);
    let title = format!("{}: {}", cfg.options_title, clean(&it.label, 30));
    match &it.target {
        Target::Instance(i) => {
            let pane_level = matches!(i.kind, Kind::Floating | Kind::FloatShell);
            let mut e = vec![entry(l("go"), true, Choice::Ops(go_to(i, ctx)))];
            let name = fresh_name(
                &format!(
                    "{}{}",
                    cfg.popout_prefix,
                    if i.kind == Kind::Window {
                        &i.window
                    } else {
                        &i.command
                    }
                ),
                &ctx.all_session_names,
            );
            let mut pop = into_new_session(i, &name);
            pop.push(Op::Desktop { session: name });
            e.push(entry(l("popout"), !i.managed, Choice::Ops(pop)));
            if pane_level {
                let dock = if i.kind == Kind::FloatShell && !i.origin {
                    // A shell started in the float shell has no window to return to: it joins the launching pane's.
                    vec![tmux_op(&[
                        "join-pane",
                        "-h",
                        "-s",
                        &i.pane_id,
                        "-t",
                        &ctx.pane,
                    ])]
                } else {
                    vec![exec_terminal(
                        &[
                            "--float",
                            "lift",
                            "--client",
                            &ctx.client,
                            "--console",
                            ctx.console_flag(),
                        ],
                        &i.pane_id,
                    )]
                };
                e.push(entry(l("dock"), !i.managed, Choice::Ops(dock)));
            } else {
                // Lifting the only pane of a window would close the window: offered once it has a second pane.
                let lift = vec![exec_terminal(
                    &[
                        "--float",
                        "lift",
                        "--client",
                        &ctx.client,
                        "--console",
                        ctx.console_flag(),
                    ],
                    &i.pane_id,
                )];
                e.push(entry(
                    l("float"),
                    !i.managed && i.panes > 1,
                    Choice::Ops(lift),
                ));
            }
            e.push(entry(
                l("move_window"),
                !i.managed,
                Choice::MoveWindow(i.clone()),
            ));
            e.push(entry(
                l("move_session"),
                !i.managed && i.kind == Kind::Window,
                Choice::MoveSession(i.clone()),
            ));
            if i.kind == Kind::Window {
                let rename = format!("rename-window -t {} -- '%%'", i.window_id);
                e.push(entry(
                    l("rename"),
                    true,
                    Choice::Ops(vec![tmux_op(&[
                        "command-prompt",
                        "-t",
                        &ctx.client,
                        "-I",
                        &i.window,
                        &rename,
                    ])]),
                ));
            }
            e.push(entry(l("close"), true, Choice::Confirm(i.clone())));
            Overlay::new(title, e, 0)
        }
        Target::App { id } => {
            let open = |mode: Option<&str>| {
                let mut a = vec![
                    "--app",
                    id.as_str(),
                    "--client",
                    ctx.client.as_str(),
                    "--console",
                    ctx.console_flag(),
                ];
                if let Some(m) = mode {
                    a.extend(["--mode", m]);
                }
                Choice::Ops(vec![exec_terminal(&a, &ctx.pane)])
            };
            let mut e = vec![
                entry(l("open"), true, open(None)),
                entry(l("open_popup"), true, open(Some("popup"))),
                entry(l("open_window"), true, open(Some("window"))),
                entry(l("open_float"), true, open(Some("float"))),
            ];
            let desktop = app_argv.map(|(label, argv)| {
                let name = fresh_name(
                    &format!("{}{id}", cfg.popout_prefix),
                    &ctx.all_session_names,
                );
                let mut args: Vec<String> = [
                    "new-session",
                    "-d",
                    "-s",
                    &name,
                    "-n",
                    label,
                    "-e",
                    "MIOS_SESSION_BARE=1",
                    "--",
                ]
                .iter()
                .map(|s| s.to_string())
                .collect();
                args.extend(argv);
                vec![
                    Op::Tmux { args },
                    tmux_op(&["set-option", "-t", &format!("={name}:"), "@mios-bare", "1"]),
                    Op::Desktop { session: name },
                ]
            });
            e.push(entry(
                l("open_desktop"),
                desktop.is_some(),
                desktop.map(Choice::Ops).unwrap_or(Choice::Cancel),
            ));
            e.push(entry(l("cancel"), true, Choice::Cancel));
            Overlay::new(title, e, 0)
        }
        t => Overlay::new(
            title,
            vec![
                entry(l("run"), true, Choice::Ops(activate(t, cfg, ctx))),
                entry(l("cancel"), true, Choice::Cancel),
            ],
            0,
        ),
    }
}

/// The close confirmation: No first (the default, as in the kill menus).
pub fn confirm(i: &Instance, cfg: &Cfg) -> Overlay {
    let kill = if i.kind == Kind::Window {
        tmux_op(&["kill-window", "-t", &i.window_id])
    } else {
        tmux_op(&["kill-pane", "-t", &i.pane_id])
    };
    let what = if i.kind == Kind::Window {
        i.window.clone()
    } else {
        i.command.clone()
    };
    Overlay::new(
        format!("{} {}", clean(&what, 30), cfg.confirm_close),
        vec![
            entry(cfg.label("no"), true, Choice::Cancel),
            entry(cfg.label("yes"), true, Choice::Ops(vec![kill])),
        ],
        0,
    )
}

/// Windows the instance's pane can join (its own window excluded), then a new window of its session.
pub fn move_window(i: &Instance, cfg: &Cfg, ctx: &Ctx) -> Overlay {
    let mut e: Vec<Entry> = ctx
        .running
        .iter()
        .filter(|r| r.kind == Kind::Window && r.window_id != i.window_id && !r.managed)
        .map(|r| {
            entry(
                format!(
                    "{}:{} {}",
                    clean(&r.session, 16),
                    r.window_index,
                    clean(&r.window, 24)
                ),
                true,
                Choice::Ops(vec![tmux_op(&[
                    "join-pane",
                    "-s",
                    &i.pane_id,
                    "-t",
                    &r.pane_id,
                ])]),
            )
        })
        .collect();
    let home = if i.kind == Kind::FloatShell {
        ctx.state.session_id.clone()
    } else {
        i.session_id.clone()
    };
    e.push(entry(
        cfg.label("new_window"),
        i.kind != Kind::Window || i.panes > 1,
        Choice::Ops(vec![tmux_op(&[
            "break-pane",
            "-s",
            &i.pane_id,
            "-t",
            &format!("{home}:"),
        ])]),
    ));
    e.push(entry(cfg.label("cancel"), true, Choice::Cancel));
    Overlay::new(cfg.move_window_title.clone(), e, 0)
}

/// Sessions the instance's window can move to (its own excluded), then a new session.
pub fn move_session(i: &Instance, cfg: &Cfg, ctx: &Ctx) -> Overlay {
    let mut e: Vec<Entry> = ctx
        .sessions
        .iter()
        .filter(|(id, _)| *id != i.session_id)
        .map(|(_, name)| {
            entry(
                clean(name, 30),
                true,
                Choice::Ops(vec![tmux_op(&[
                    "move-window",
                    "-s",
                    &i.window_id,
                    "-t",
                    &format!("={name}:"),
                ])]),
            )
        })
        .collect();
    let name = fresh_name(&cfg.new_session_name, &ctx.all_session_names);
    e.push(entry(
        cfg.new_session_label.clone(),
        true,
        Choice::Ops(into_new_session(i, &name)),
    ));
    e.push(entry(cfg.label("cancel"), true, Choice::Cancel));
    Overlay::new(cfg.move_session_title.clone(), e, 0)
}

// ---- the TUI -------------------------------------------------------------------------------------------------

struct Ui<'a> {
    cfg: &'a Cfg,
    sections: Vec<Section>,
    source_sections: Vec<Section>,
    search: Option<String>,
    search_anchor: Option<(Target, usize)>,
    sel: usize,
    offset: usize,
    overlay: Vec<Overlay>,
    pending: bool,
    lay: Layout,
    sel_order: Vec<(usize, usize)>,
    overlay_rect: Rect,
    area: Rect,
}

#[derive(Debug, PartialEq)]
pub enum Outcome {
    Stay,
    Close,
    Done(Vec<Op>),
}

impl<'a> Ui<'a> {
    fn new(cfg: &'a Cfg, sections: Vec<Section>, area: Rect, start: Option<&str>) -> Self {
        let lay = layout(&sections, area, cfg);
        let sel_order = order(&sections, &lay);
        let mut ui = Ui {
            cfg,
            source_sections: sections.clone(),
            sections,
            search: None,
            search_anchor: None,
            sel: 0,
            offset: 0,
            overlay: Vec::new(),
            pending: false,
            lay,
            sel_order,
            overlay_rect: Rect::default(),
            area,
        };
        ui.start(start);
        ui
    }

    fn key(&mut self, key: KeyEvent) -> Input {
        if self.search.is_some() && self.overlay.is_empty() && !self.pending {
            match key.code {
                KeyCode::Char('u') if key.modifiers.contains(KeyModifiers::CONTROL) => {
                    return Input::ClearQuery
                }
                KeyCode::Backspace => return Input::EraseQuery,
                KeyCode::Char(c)
                    if !key
                        .modifiers
                        .intersects(KeyModifiers::CONTROL | KeyModifiers::ALT)
                        && !c.is_control() =>
                {
                    return Input::Query(c)
                }
                _ => {}
            }
        }
        map_key(key, &self.cfg.prefix, &mut self.pending)
    }

    fn begin_search(&mut self) {
        if self.search.is_none() {
            self.search_anchor = self
                .selected()
                .map(|item| (item.target.clone(), self.offset));
            self.search = Some(String::new());
        }
    }

    fn filter(&mut self) {
        let keep = self.selected().map(|item| item.target.clone());
        let query = self.search.as_deref().unwrap_or("").to_lowercase();
        let words: Vec<&str> = query.split_whitespace().collect();
        self.sections = self
            .source_sections
            .iter()
            .filter_map(|section| {
                let mut result = section.clone();
                result.items.retain(|item| {
                    let target = match &item.target {
                        Target::App { id } => id.as_str(),
                        Target::Instance(instance) => instance.command.as_str(),
                        _ => "",
                    };
                    let haystack = format!(
                        "{} {} {} {}",
                        section.title, item.label, item.detail, target
                    )
                    .to_lowercase();
                    words.iter().all(|word| haystack.contains(word))
                });
                (words.is_empty() || !result.items.is_empty()).then_some(result)
            })
            .collect();
        self.lay = layout(&self.sections, self.area, self.cfg);
        self.sel_order = order(&self.sections, &self.lay);
        self.sel = keep
            .and_then(|target| {
                self.sel_order
                    .iter()
                    .position(|&pos| self.item_at(pos).is_some_and(|item| item.target == target))
            })
            .unwrap_or(0);
        self.offset = 0;
    }

    fn cancel_search(&mut self) {
        self.search = None;
        self.filter();
        if let Some((target, offset)) = self.search_anchor.take() {
            if let Some(index) = self
                .sel_order
                .iter()
                .position(|&pos| self.item_at(pos).is_some_and(|item| item.target == target))
            {
                self.sel = index;
                self.offset = offset;
            }
        }
    }

    fn item_at(&self, pos: (usize, usize)) -> Option<&Item> {
        match self.lay.cols.get(pos.0)?.get(pos.1)? {
            Line::Item(si, ii) => self.sections.get(*si)?.items.get(*ii),
            _ => None,
        }
    }

    fn section_of(&self, pos: (usize, usize)) -> Option<usize> {
        match self.lay.cols.get(pos.0)?.get(pos.1)? {
            Line::Item(si, _) => Some(*si),
            _ => None,
        }
    }

    fn start(&mut self, start: Option<&str>) {
        let want = start.unwrap_or(&self.cfg.start_section).to_owned();
        let found = self.sel_order.iter().position(|&p| {
            let si = self.section_of(p).unwrap_or(usize::MAX);
            let s = &self.sections[si];
            s.id == want
                || (want == "apps"
                    && s.items
                        .iter()
                        .any(|i| matches!(i.target, Target::App { .. })))
        });
        self.sel = found.unwrap_or(0);
    }

    fn relayout(&mut self, area: Rect) {
        let keep = self
            .sel_order
            .get(self.sel)
            .and_then(|&p| match self.lay.cols[p.0][p.1] {
                Line::Item(si, ii) => Some((si, ii)),
                _ => None,
            });
        self.area = area;
        self.lay = layout(&self.sections, area, self.cfg);
        self.sel_order = order(&self.sections, &self.lay);
        if let Some(k) = keep {
            if let Some(i) = self
                .sel_order
                .iter()
                .position(|&p| self.lay.cols[p.0][p.1] == Line::Item(k.0, k.1))
            {
                self.sel = i;
            }
        }
        self.sel = self.sel.min(self.sel_order.len().saturating_sub(1));
    }

    fn selected(&self) -> Option<&Item> {
        self.sel_order.get(self.sel).and_then(|&p| self.item_at(p))
    }

    fn move_col(&mut self, right: bool) {
        let Some(&(c, l)) = self.sel_order.get(self.sel) else {
            return;
        };
        if self.lay.cols.len() > 1 {
            let target = if right {
                (c + 1).min(self.lay.cols.len() - 1)
            } else {
                c.saturating_sub(1)
            };
            if target == c {
                return;
            }
            if let Some(i) = self
                .sel_order
                .iter()
                .enumerate()
                .filter(|(_, p)| p.0 == target)
                .min_by_key(|(_, p)| (p.1 as i64 - l as i64).abs())
                .map(|(i, _)| i)
            {
                self.sel = i;
            }
        } else {
            self.jump_section(right);
        }
    }

    fn jump_section(&mut self, forward: bool) {
        let starts: Vec<usize> = (0..self.sel_order.len())
            .filter(|&i| {
                i == 0
                    || self.section_of(self.sel_order[i]) != self.section_of(self.sel_order[i - 1])
            })
            .collect();
        let Some(here) = starts.iter().rposition(|&st| st <= self.sel) else {
            return;
        };
        self.sel = if forward {
            starts[(here + 1) % starts.len()]
        } else if self.sel > starts[here] {
            starts[here]
        } else {
            starts[(here + starts.len() - 1) % starts.len()]
        };
    }

    fn page(&mut self, down: bool) {
        let Some(&(c, l)) = self.sel_order.get(self.sel) else {
            return;
        };
        let h = self.lay.height as usize;
        let target = if down { l + h } else { l.saturating_sub(h) };
        let same: Vec<usize> = (0..self.sel_order.len())
            .filter(|&i| self.sel_order[i].0 == c)
            .collect();
        let pick = if down {
            same.iter()
                .copied()
                .rfind(|&i| self.sel_order[i].1 <= target)
        } else {
            same.iter()
                .copied()
                .find(|&i| self.sel_order[i].1 >= target)
        };
        if let Some(p) = pick {
            self.sel = p;
        }
    }

    fn hit(&self, x: u16, y: u16) -> Option<usize> {
        if y < self.lay.top || y >= self.lay.top + self.lay.height {
            return None;
        }
        let line = self.offset + (y - self.lay.top) as usize;
        let col = self
            .lay
            .col_x
            .iter()
            .rposition(|&cx| x >= cx && x < cx + self.lay.col_w)?;
        self.sel_order.iter().position(|&p| p == (col, line))
    }

    fn open_overlay(&mut self, ov: Overlay) {
        self.overlay.push(ov);
    }

    fn choose(&mut self, c: Choice, ctx: &Ctx) -> Outcome {
        match c {
            Choice::Ops(ops) => Outcome::Done(ops),
            Choice::Cancel => {
                self.overlay.pop();
                Outcome::Stay
            }
            Choice::Confirm(i) => {
                self.open_overlay(confirm(&i, self.cfg));
                Outcome::Stay
            }
            Choice::MoveWindow(i) => {
                self.open_overlay(move_window(&i, self.cfg, ctx));
                Outcome::Stay
            }
            Choice::MoveSession(i) => {
                self.open_overlay(move_session(&i, self.cfg, ctx));
                Outcome::Stay
            }
        }
    }

    /// One input step. `app_argv` resolves an app's label and argv for its desktop pop-out.
    fn input(
        &mut self,
        inp: Input,
        ctx: &Ctx,
        app_argv: &dyn Fn(&str) -> Option<AppInvocation>,
    ) -> Outcome {
        if let Some(ov) = self.overlay.last_mut() {
            match inp {
                Input::Up | Input::Left | Input::PageUp | Input::Wheel { up: true } => {
                    ov.step(false)
                }
                Input::Down
                | Input::Right
                | Input::PageDown
                | Input::NextSection
                | Input::Wheel { up: false } => ov.step(true),
                Input::Home => ov.sel = ov.entries.iter().position(|e| e.enabled).unwrap_or(0),
                Input::End => ov.sel = ov.entries.iter().rposition(|e| e.enabled).unwrap_or(0),
                Input::Back => {
                    self.overlay.pop();
                }
                Input::Activate | Input::Options => {
                    let e = ov.entries.get(ov.sel).cloned();
                    if let Some(e) = e.filter(|e| e.enabled) {
                        return self.choose(e.choice, ctx);
                    }
                }
                Input::Click { x, y, .. } => {
                    let r = self.overlay_rect;
                    if x > r.x && x + 1 < r.x + r.width && y > r.y && y + 1 < r.y + r.height {
                        let i = ov.offset + (y - r.y - 1) as usize;
                        if let Some(e) = ov.entries.get(i).cloned().filter(|e| e.enabled) {
                            ov.sel = i;
                            return self.choose(e.choice, ctx);
                        }
                    } else {
                        self.overlay.pop();
                    }
                }
                _ => {}
            }
            return Outcome::Stay;
        }
        let n = self.sel_order.len();
        match inp {
            Input::Up | Input::Wheel { up: true } => self.sel = self.sel.saturating_sub(1),
            Input::Down | Input::Wheel { up: false } => {
                self.sel = (self.sel + 1).min(n.saturating_sub(1))
            }
            Input::Left => self.move_col(false),
            Input::Right => self.move_col(true),
            Input::PageUp => self.page(false),
            Input::PageDown => self.page(true),
            Input::Home => self.sel = 0,
            Input::End => self.sel = n.saturating_sub(1),
            Input::NextSection => self.jump_section(true),
            Input::Back if self.search.is_some() => self.cancel_search(),
            Input::Back => return Outcome::Close,
            Input::Search => self.begin_search(),
            Input::Query(c) => {
                if let Some(query) = &mut self.search {
                    query.push(c);
                    self.filter();
                }
            }
            Input::EraseQuery => {
                if let Some(query) = &mut self.search {
                    query.pop();
                    self.filter();
                }
            }
            Input::ClearQuery => {
                if let Some(query) = &mut self.search {
                    query.clear();
                    self.filter();
                }
            }
            Input::Activate => {
                if let Some(it) = self.selected() {
                    return Outcome::Done(activate(&it.target, self.cfg, ctx));
                }
            }
            Input::Options => {
                if let Some(it) = self.selected().cloned() {
                    let argv = if let Target::App { id } = &it.target {
                        app_argv(id)
                    } else {
                        None
                    };
                    self.open_overlay(options(
                        &it,
                        self.cfg,
                        ctx,
                        argv.as_ref().map(|(l, a)| (l.as_str(), a.clone())),
                    ));
                }
            }
            Input::Key(c) => {
                let found = self
                    .sel_order
                    .iter()
                    .position(|&p| self.item_at(p).is_some_and(|i| i.key == Some(c)));
                if let Some(i) = found {
                    self.sel = i;
                    if let Some(it) = self.selected() {
                        return Outcome::Done(activate(&it.target, self.cfg, ctx));
                    }
                }
            }
            Input::Click { y, .. } if y == self.area.y + 1 => self.begin_search(),
            Input::Click { x, y, right } => {
                if let Some(i) = self.hit(x, y) {
                    self.sel = i;
                    return self.input(
                        if right {
                            Input::Options
                        } else {
                            Input::Activate
                        },
                        ctx,
                        app_argv,
                    );
                }
            }
            Input::Nothing => {}
        }
        Outcome::Stay
    }

    fn scroll(&mut self) {
        let Some(&(_, l)) = self.sel_order.get(self.sel) else {
            return;
        };
        let h = self.lay.height as usize;
        // Keep the section header above the first row of a section in view.
        let want_top = l.saturating_sub(1);
        if want_top < self.offset {
            self.offset = want_top;
        } else if l >= self.offset + h {
            self.offset = l + 1 - h;
        }
    }

    fn draw(&mut self, buf: &mut Buffer, area: Rect) {
        let [bg, fg, accent, subtle, muted] = self.cfg.palette;
        let base = Style::default().bg(bg).fg(fg);
        buf.set_style(area, base);
        let console = self.cfg.console;
        let rule = if console { '-' } else { '\u{2500}' };
        // Title row, centred.
        let title = format!(" {} ", self.cfg.title);
        let tx = area.x + area.width.saturating_sub(title.len() as u16) / 2;
        buf.set_stringn(
            tx,
            area.y,
            &title,
            area.width as usize,
            Style::default()
                .fg(accent)
                .bg(bg)
                .add_modifier(Modifier::BOLD),
        );
        let search_line = match &self.search {
            Some(query) => format!("{} {}", self.cfg.label("find"), query),
            None => self.cfg.label("find_hint"),
        };
        buf.set_stringn(
            area.x + 1,
            area.y + 1,
            search_line,
            area.width.saturating_sub(2) as usize,
            Style::default()
                .fg(if self.search.is_some() { accent } else { muted })
                .bg(bg),
        );
        self.scroll();
        let selected = self.sel_order.get(self.sel).copied();
        for (c, col) in self.lay.cols.iter().enumerate() {
            let x0 = self.lay.col_x[c];
            let w = self.lay.col_w as usize;
            for row in 0..self.lay.height {
                let li = self.offset + row as usize;
                let Some(line) = col.get(li) else { break };
                let y = self.lay.top + row;
                match *line {
                    Line::Blank => {}
                    Line::Header(si) => {
                        let t = &self.sections[si].title;
                        let head = format!("{t} ");
                        buf.set_stringn(
                            x0,
                            y,
                            &head,
                            w,
                            Style::default()
                                .fg(accent)
                                .bg(bg)
                                .add_modifier(Modifier::BOLD),
                        );
                        let used = head.chars().count();
                        if used < w {
                            buf.set_stringn(
                                x0 + used as u16,
                                y,
                                rule.to_string().repeat(w - used),
                                w - used,
                                Style::default().fg(subtle).bg(bg),
                            );
                        }
                    }
                    Line::Note(si) => {
                        let t = self.sections[si].empty.clone().unwrap_or_default();
                        buf.set_stringn(
                            x0 + self.cfg.key_w + 2,
                            y,
                            &t,
                            w.saturating_sub(self.cfg.key_w as usize + 2),
                            Style::default()
                                .fg(muted)
                                .bg(bg)
                                .add_modifier(Modifier::ITALIC),
                        );
                    }
                    Line::Item(si, ii) => {
                        let it = &self.sections[si].items[ii];
                        let is_sel = selected == Some((c, li));
                        let st = if is_sel {
                            Style::default()
                                .bg(accent)
                                .fg(bg)
                                .add_modifier(Modifier::BOLD)
                        } else if !it.enabled {
                            Style::default()
                                .fg(muted)
                                .bg(bg)
                                .add_modifier(Modifier::DIM)
                        } else {
                            base
                        };
                        let kw = self.cfg.key_w as usize;
                        let key = it.key.map(|k| k.to_string()).unwrap_or_default();
                        let left = format!("{key:>kw$}  {}", it.label);
                        let detail =
                            if self.lay.col_w >= self.cfg.detail_min && !it.detail.is_empty() {
                                it.detail.clone()
                            } else {
                                String::new()
                            };
                        let room = w.saturating_sub(left.chars().count() + 1);
                        let detail: String = if detail.chars().count() <= room {
                            detail
                        } else {
                            String::new()
                        };
                        let pad = w.saturating_sub(left.chars().count() + detail.chars().count());
                        let text = format!("{left}{}{detail}", " ".repeat(pad));
                        buf.set_stringn(x0, y, &text, w, st);
                        if !is_sel && it.enabled && !key.is_empty() {
                            buf.set_stringn(
                                x0 + (kw - key.len().min(kw)) as u16,
                                y,
                                &key,
                                kw,
                                Style::default()
                                    .fg(accent)
                                    .bg(bg)
                                    .add_modifier(Modifier::BOLD),
                            );
                        }
                        if !is_sel && !detail.is_empty() {
                            buf.set_stringn(
                                x0 + (w - detail.chars().count()) as u16,
                                y,
                                &detail,
                                w,
                                Style::default().fg(muted).bg(bg),
                            );
                        }
                    }
                }
            }
        }
        let context = if let Some(item) = self.selected() {
            let pos = self.sel_order[self.sel];
            let section = self
                .section_of(pos)
                .and_then(|index| self.sections.get(index))
                .map(|section| section.title.as_str())
                .unwrap_or("");
            let more_above = self.offset > 0;
            let more_below = self
                .lay
                .cols
                .iter()
                .any(|col| col.len() > self.offset + self.lay.height as usize);
            format!(
                "{} {}/{} {}{}{} | {}",
                section,
                self.sel + 1,
                self.sel_order.len(),
                if more_above { "^" } else { "" },
                if more_below { "v" } else { "" },
                if more_above || more_below {
                    " more"
                } else {
                    ""
                },
                if item.detail.is_empty() {
                    item.label.as_str()
                } else {
                    item.detail.as_str()
                }
            )
        } else if self.search.is_some() {
            self.cfg.label("no_matches")
        } else {
            String::new()
        };
        buf.set_stringn(
            area.x + 1,
            area.y + area.height.saturating_sub(2),
            context,
            area.width.saturating_sub(2) as usize,
            Style::default().fg(muted).bg(bg),
        );
        let help = if !self.overlay.is_empty() {
            self.cfg.label("options_help")
        } else if self.search.is_some() {
            self.cfg.label("find_help")
        } else if self.cfg.help.chars().count() > area.width as usize {
            self.cfg.label("help_compact")
        } else {
            self.cfg.help.clone()
        };
        let hx = area.x + area.width.saturating_sub(help.chars().count() as u16) / 2;
        buf.set_stringn(
            hx,
            area.y + area.height.saturating_sub(1),
            help,
            area.width as usize,
            Style::default().fg(muted).bg(bg),
        );
        if let Some(ov) = self.overlay.last_mut() {
            let w = ov
                .entries
                .iter()
                .map(|e| e.label.chars().count())
                .chain(std::iter::once(ov.title.chars().count()))
                .max()
                .unwrap_or(10) as u16
                + 6;
            let (cap_w, cap_h) = self.cfg.popup_policy.size(
                mios_resolver::popup::SurfaceKind::Menu,
                area.width,
                area.height,
            );
            let w = w.min(cap_w);
            let h = (ov.entries.len() as u16 + 2).min(cap_h);
            let r = Rect {
                x: area.x + (area.width - w) / 2,
                y: area.y + (area.height - h) / 2,
                width: w,
                height: h,
            };
            self.overlay_rect = r;
            buf.set_style(r, Style::default().bg(bg).fg(fg));
            let (tl, tr, bl, br, hz, vt) = if console {
                ('+', '+', '+', '+', '-', '|')
            } else {
                (
                    '\u{256d}', '\u{256e}', '\u{2570}', '\u{256f}', '\u{2500}', '\u{2502}',
                )
            };
            let bs = Style::default().fg(accent).bg(bg);
            for x in r.x..r.x + r.width {
                buf.set_stringn(x, r.y, hz.to_string(), 1, bs);
                buf.set_stringn(x, r.y + r.height - 1, hz.to_string(), 1, bs);
            }
            for y in r.y..r.y + r.height {
                buf.set_stringn(r.x, y, vt.to_string(), 1, bs);
                buf.set_stringn(r.x + r.width - 1, y, vt.to_string(), 1, bs);
            }
            for (x, y, ch) in [
                (r.x, r.y, tl),
                (r.x + r.width - 1, r.y, tr),
                (r.x, r.y + r.height - 1, bl),
                (r.x + r.width - 1, r.y + r.height - 1, br),
            ] {
                buf.set_stringn(x, y, ch.to_string(), 1, bs);
            }
            let t = format!(" {} ", ov.title);
            let tx = r.x + r.width.saturating_sub(t.chars().count() as u16) / 2;
            buf.set_stringn(
                tx,
                r.y,
                &t,
                r.width as usize,
                bs.add_modifier(Modifier::BOLD),
            );
            // Keep the selection in view when the list is taller than the popup.
            let rows = r.height.saturating_sub(2).max(1) as usize;
            if ov.sel < ov.offset {
                ov.offset = ov.sel;
            } else if ov.sel >= ov.offset + rows {
                ov.offset = ov.sel + 1 - rows;
            }
            for (i, e) in ov.entries.iter().enumerate().skip(ov.offset) {
                let y = r.y + 1 + (i - ov.offset) as u16;
                if y + 1 >= r.y + r.height {
                    break;
                }
                let st = if i == ov.sel {
                    Style::default()
                        .bg(accent)
                        .fg(bg)
                        .add_modifier(Modifier::BOLD)
                } else if !e.enabled {
                    Style::default()
                        .fg(muted)
                        .bg(bg)
                        .add_modifier(Modifier::DIM)
                } else {
                    Style::default().fg(fg).bg(bg)
                };
                let line = format!(
                    " {:<w$} ",
                    e.label,
                    w = (r.width as usize).saturating_sub(4)
                );
                buf.set_stringn(r.x + 1, y, &line, r.width as usize - 2, st);
            }
        }
    }
}

// ---- tmux plumbing -------------------------------------------------------------------------------------------

/// A tmux client name as #{q:client_name} gives it: a tty path or a client-PID control client.
pub fn client_ok(c: &str) -> bool {
    let tty = c.strip_prefix("/dev/").is_some_and(|t| {
        !t.is_empty()
            && !t.contains("..")
            && t.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"/._-".contains(&b))
    });
    let ctl = c
        .strip_prefix("client-")
        .is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()));
    tty || ctl
}

fn pane_ok(p: &str) -> bool {
    p.len() > 1 && p.starts_with('%') && p[1..].bytes().all(|b| b.is_ascii_digit())
}

fn console_arg(c: &str) -> Result<bool, String> {
    match c {
        "1" => Ok(true),
        "0" => Ok(false),
        o => Err(format!("--console must be 0 or 1, not {o:?}")),
    }
}

/// The server socket of this tmux job ($TMUX), so every later call reaches the same server.
fn socket_from_env() -> Result<String, String> {
    let t = std::env::var("TMUX").map_err(|_| "not inside tmux ($TMUX is unset)".to_string())?;
    let sock = t.split(',').next().unwrap_or("");
    if sock.starts_with('/') && !sock.chars().any(char::is_control) {
        Ok(sock.to_owned())
    } else {
        Err("invalid $TMUX socket".into())
    }
}

fn control_environment_key(key: &str) -> bool {
    matches!(
        key,
        "HOME" | "PATH" | "TERM" | "LANG" | "USER" | "LOGNAME" | "SHELL" | "TMPDIR" | "TMUX_TMPDIR"
    ) || key.starts_with("LC_")
}

fn tmux(timeout: Duration, sock: &str, args: &[&str]) -> Result<String, String> {
    // A control query does not need the expanded MiOS configuration environment.
    // Sending it all during tmux IDENTIFY can fill IPC buffers before the query
    // is even dispatched. The target server/client retains its own app state.
    let mut command = Command::new("tmux");
    command.arg("-S").arg(sock).args(args).env_clear();
    for (key, value) in std::env::vars_os() {
        if control_environment_key(&key.to_string_lossy()) {
            command.env(key, value);
        }
    }
    command.stdin(Stdio::null());
    let interactive = matches!(
        args.first().copied(),
        Some("display-popup" | "display-menu" | "command-prompt")
    );
    let out = if interactive {
        command.output().map_err(|e| format!("tmux: {e}"))?
    } else {
        crate::sessions::run_bounded(&mut command, Instant::now() + timeout)
            .map_err(|e| format!("tmux control query: {e}"))?
    };
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout)
            .trim_end_matches('\n')
            .to_owned())
    } else {
        Err(format!(
            "tmux {}: {}",
            args.first().unwrap_or(&""),
            String::from_utf8_lossy(&out.stderr).trim()
        ))
    }
}

fn pane_state(cfg: &Cfg, sock: &str, pane: &str) -> PaneState {
    let f = tmux(
        cfg.command_timeout,
        sock,
        &[
            "display-message",
            "-p",
            "-t",
            pane,
            "#{pane_in_mode}\t#{pane_mode}\t#{E:@mios-managed-pane}\t#{session_id}",
        ],
    )
    .unwrap_or_default();
    let f: Vec<&str> = f.split('\t').collect();
    PaneState {
        copy_mode: f.first() == Some(&"1")
            && f.get(1)
                .is_some_and(|m| m.contains("copy") || m.contains("view")),
        managed: f.get(2) == Some(&"1"),
        session_id: f.get(3).unwrap_or(&"").to_string(),
    }
}

fn context(cfg: &Cfg, sock: &str, client: &str, pane: &str, console: bool) -> (Ctx, String) {
    let listing = tmux(
        cfg.command_timeout,
        sock,
        &["list-panes", "-a", "-F", PANES],
    )
    .unwrap_or_default();
    let sessions_out = tmux(
        cfg.command_timeout,
        sock,
        &["list-sessions", "-F", "#{session_id}\t#{session_name}"],
    )
    .unwrap_or_default();
    let all: Vec<(String, String)> = sessions_out
        .lines()
        .filter_map(|l| l.split_once('\t'))
        .map(|(a, b)| (a.to_owned(), b.to_owned()))
        .collect();
    let running = parse_running(&listing, cfg)
        .into_iter()
        .filter_map(|i| {
            if let Target::Instance(x) = i.target {
                Some(x)
            } else {
                None
            }
        })
        .collect();
    let ctx = Ctx {
        client: client.into(),
        pane: pane.into(),
        console,
        state: pane_state(cfg, sock, pane),
        sessions: all
            .iter()
            .filter(|(_, n)| !policy::is_hidden_session(&cfg.hidden, n))
            .cloned()
            .collect(),
        all_session_names: all.into_iter().map(|(_, n)| n).collect(),
        running,
    };
    (ctx, listing)
}

fn sections_for(v: &Value, cfg: &Cfg, ctx: &Ctx, listing: &str) -> Result<Vec<Section>, String> {
    Ok(build(
        cfg,
        parse_running(listing, cfg),
        ai_items(v)?,
        app_sections(v, cfg, terminal_tuis::is_installed)?,
        menu_sections(v, cfg, &ctx.state)?,
    ))
}

fn app_argv_fn<'v>(
    v: &'v Value,
    console: bool,
) -> impl Fn(&str) -> Option<(String, Vec<String>)> + 'v {
    move |id: &str| {
        let cat = terminal_tuis::catalog(v).ok()?;
        let t = terminal_tuis::visible(&cat, console, terminal_tuis::is_installed)
            .into_iter()
            .find(|t| t.id == id)?;
        Some((t.label.clone(), t.argv_for(console).to_vec()))
    }
}

fn pick(
    root: &Path,
    client: &str,
    pane: &str,
    console: bool,
    start: Option<&str>,
    result: &Path,
) -> Result<(), String> {
    let v = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let cfg = config(&v, console)?;
    let sock = socket_from_env()?;
    let (ctx, listing) = context(&cfg, &sock, client, pane, console);
    let sections = sections_for(&v, &cfg, &ctx, &listing)?;
    let app_argv = app_argv_fn(&v, console);
    let guard = crate::sessions::TermGuard::arm();
    let mut terminal = ratatui::init();
    let _ = execute!(std::io::stdout(), EnableMouseCapture);
    let size = terminal.size().map_err(|e| e.to_string())?;
    let mut ui = Ui::new(
        &cfg,
        sections,
        Rect::new(0, 0, size.width, size.height),
        start,
    );
    let outcome = loop {
        terminal
            .draw(|f| {
                let area = f.area();
                if area != ui.area {
                    ui.relayout(area);
                }
                ui.draw(f.buffer_mut(), area);
            })
            .map_err(|e| e.to_string())?;
        if !event::poll(Duration::from_millis(500)).map_err(|e| e.to_string())? {
            continue;
        }
        let inp = match event::read().map_err(|e| e.to_string())? {
            Event::Key(k) if k.kind == KeyEventKind::Press => ui.key(k),
            Event::Mouse(m) => match m.kind {
                MouseEventKind::Down(MouseButton::Left) => Input::Click {
                    x: m.column,
                    y: m.row,
                    right: false,
                },
                MouseEventKind::Down(MouseButton::Right) => Input::Click {
                    x: m.column,
                    y: m.row,
                    right: true,
                },
                MouseEventKind::ScrollUp => Input::Wheel { up: true },
                MouseEventKind::ScrollDown => Input::Wheel { up: false },
                _ => Input::Nothing,
            },
            _ => Input::Nothing,
        };
        match ui.input(inp, &ctx, &app_argv) {
            Outcome::Stay => {}
            Outcome::Close => break Vec::new(),
            Outcome::Done(ops) => break ops,
        }
    };
    drop(guard);
    let json = serde_json::to_string(&outcome).map_err(|e| e.to_string())?;
    // The file was created 0600 by `open`; never follow a link put in its place.
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .truncate(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(result)
        .map_err(|e| format!("{}: {e}", result.display()))?;
    f.write_all(json.as_bytes()).map_err(|e| e.to_string())
}

fn result_file() -> Result<PathBuf, String> {
    let base = std::env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .filter(|p| p.is_dir())
        .unwrap_or_else(std::env::temp_dir);
    let dir = base.join("mios-launcher");
    match std::fs::create_dir(&dir) {
        Ok(()) => {}
        Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
        Err(e) => return Err(format!("{}: {e}", dir.display())),
    }
    use std::os::unix::fs::{MetadataExt, PermissionsExt};
    let meta = std::fs::symlink_metadata(&dir).map_err(|e| e.to_string())?;
    if !meta.is_dir() || meta.uid() != unsafe { libc::getuid() } {
        return Err(format!("{} is not this user's directory", dir.display()));
    }
    std::fs::set_permissions(&dir, std::fs::Permissions::from_mode(0o700))
        .map_err(|e| e.to_string())?;
    for n in 0..1000 {
        let p = dir.join(format!("{}-{n}.json", std::process::id()));
        match std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .mode(0o600)
            .open(&p)
        {
            Ok(_) => return Ok(p),
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(e) => return Err(e.to_string()),
        }
    }
    Err("no free launcher result file".into())
}

/// The popup size for a client: the SSOT percentage, at least min_* and at most the client.
pub fn popup_size(cfg: &Cfg, cw: u16, ch: u16) -> Option<(u16, u16)> {
    if cw < cfg.min_w || ch < cfg.min_h {
        return None;
    }
    let w = ((cw as u32 * cfg.popup_w) / 100) as u16;
    let h = ((ch as u32 * cfg.popup_h) / 100) as u16;
    Some(cfg.popup_policy.size(
        mios_resolver::popup::SurfaceKind::Window,
        w.max(cfg.min_w).min(cw),
        h.max(cfg.min_h).min(ch),
    ))
}

fn desktop_session() -> bool {
    std::env::var_os("WAYLAND_DISPLAY").is_some_and(|v| !v.is_empty())
        || std::env::var_os("DISPLAY").is_some_and(|v| !v.is_empty())
}

/// Run the steps `pick` chose, on the same server and client.
pub fn run_ops(cfg: &Cfg, sock: &str, client: &str, ops: Vec<Op>) -> Result<(), String> {
    for op in ops {
        match op {
            Op::Tmux { args } => {
                let a: Vec<&str> = args.iter().map(String::as_str).collect();
                if let Err(e) = tmux(cfg.command_timeout, sock, &a) {
                    let _ = tmux(
                        cfg.command_timeout,
                        sock,
                        &["display-message", "-c", client, "-l", &e],
                    );
                    return Err(e);
                }
            }
            Op::Desktop { session } => {
                if desktop_session() {
                    let mut argv = cfg.desktop_terminal.clone();
                    argv.extend(["--session".into(), session]);
                    let mut c = Command::new(&argv[0]);
                    c.args(&argv[1..])
                        .stdin(Stdio::null())
                        .stdout(Stdio::null())
                        .stderr(Stdio::null())
                        .process_group(0);
                    c.spawn().map_err(|e| format!("{}: {e}", argv[0]))?;
                } else {
                    tmux(
                        cfg.command_timeout,
                        sock,
                        &[
                            "display-message",
                            "-c",
                            client,
                            "-l",
                            &format!("{} {session}", cfg.popped_message),
                        ],
                    )?;
                }
            }
            Op::Exec { argv, pane } => {
                if !pane_ok(&pane) || argv.first().map(String::as_str) != Some(TERMINAL) {
                    return Err("launcher exec must be the MiOS-Tmux executor on a pane".into());
                }
                let tmux_env = std::env::var("TMUX").unwrap_or_else(|_| format!("{sock},0,0"));
                return Err(format!(
                    "exec {}: {}",
                    argv[0],
                    Command::new(&argv[0])
                        .args(&argv[1..])
                        .env("TMUX", tmux_env)
                        .env("TMUX_PANE", pane)
                        .exec()
                ));
            }
        }
    }
    Ok(())
}

/// Open the launcher popup on `client` (blocks until it closes), then run what was chosen.
pub fn open_on(
    root: &Path,
    sock: &str,
    client: &str,
    pane: &str,
    console: bool,
    start: Option<&str>,
) -> Result<(), String> {
    if !client_ok(client) || !pane_ok(pane) {
        return Err("invalid tmux client or pane".into());
    }
    let v = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let cfg = config(&v, console)?;
    let size = tmux(
        cfg.command_timeout,
        sock,
        &[
            "display-message",
            "-p",
            "-c",
            client,
            "#{client_width}\t#{client_height}",
        ],
    )?;
    let (cw, ch) = size
        .split_once('\t')
        .map(|(a, b)| (a.parse().unwrap_or(0), b.parse().unwrap_or(0)))
        .unwrap_or((0, 0));
    let Some((w, h)) = popup_size(&cfg, cw, ch) else {
        return tmux(
            cfg.command_timeout,
            sock,
            &["display-message", "-c", client, "-l", &cfg.too_small],
        )
        .map(|_| ());
    };
    let result = result_file()?;
    let me = std::env::current_exe().map_err(|e| e.to_string())?;
    let (w, h) = (w.to_string(), h.to_string());
    let mut args: Vec<String> = [
        "display-popup",
        "-c",
        client,
        "-t",
        pane,
        "-E",
        "-w",
        &w,
        "-h",
        &h,
        "-x",
        "C",
        "-y",
        "C",
    ]
    .iter()
    .map(|s| s.to_string())
    .collect();
    if console {
        args.extend(["-b".into(), s(&v, "theme.tmux.console.border_lines")?]);
    }
    args.push("--".into());
    args.extend([
        me.display().to_string(),
        "launcher".into(),
        "pick".into(),
        "--root".into(),
        root.display().to_string(),
        "--client".into(),
        client.into(),
        "--pane".into(),
        pane.into(),
    ]);
    args.extend([
        "--console".into(),
        if console { "1".into() } else { "0".into() },
        "--result".into(),
        result.display().to_string(),
    ]);
    if let Some(st) = start {
        args.extend(["--start".into(), st.into()]);
    }
    let a: Vec<&str> = args.iter().map(String::as_str).collect();
    let shown = tmux(cfg.command_timeout, sock, &a);
    let chosen = std::fs::read_to_string(&result).unwrap_or_default();
    let _ = std::fs::remove_file(&result);
    shown?;
    if chosen.trim().is_empty() {
        return Ok(());
    }
    let ops: Vec<Op> =
        serde_json::from_str(&chosen).map_err(|e| format!("launcher result: {e}"))?;
    run_ops(&cfg, sock, client, ops)
}

// ---- the desktop chord ---------------------------------------------------------------------------------------

/// A tmux client process below `pid` (the focused window) on a server named `socket` under `socket_root`
/// (TMUX_TMPDIR of that client, else the SSOT root): (socket path, client tty). /proc only.
pub fn tmux_client_under(
    proc_root: &Path,
    pid: u32,
    socket: &str,
    socket_root: &str,
) -> Option<(String, String)> {
    let mut children: BTreeMap<u32, Vec<u32>> = BTreeMap::new();
    for e in std::fs::read_dir(proc_root).ok()?.flatten() {
        let Some(p) = e.file_name().to_str().and_then(|n| n.parse::<u32>().ok()) else {
            continue;
        };
        let Ok(stat) = std::fs::read_to_string(e.path().join("stat")) else {
            continue;
        };
        let Some(ppid) = stat
            .rsplit_once(") ")
            .and_then(|(_, r)| r.split(' ').nth(1))
            .and_then(|x| x.parse::<u32>().ok())
        else {
            continue;
        };
        children.entry(ppid).or_default().push(p);
    }
    let uid = unsafe { libc::getuid() };
    let mut stack = vec![pid];
    let mut seen = BTreeSet::new();
    while let Some(p) = stack.pop() {
        if !seen.insert(p) || seen.len() > 4096 {
            continue;
        }
        let dir = proc_root.join(p.to_string());
        let args: Vec<String> = std::fs::read(dir.join("cmdline"))
            .unwrap_or_default()
            .split(|b| *b == 0)
            .filter(|a| !a.is_empty())
            .map(|a| String::from_utf8_lossy(a).into_owned())
            .collect();
        if args
            .first()
            .and_then(|a| Path::new(a).file_name())
            .and_then(|n| n.to_str())
            == Some("tmux")
        {
            let named = args.windows(2).any(|w| w[0] == "-L" && w[1] == socket);
            let attach = args.iter().any(|a| {
                matches!(
                    a.as_str(),
                    "attach" | "attach-session" | "a" | "new" | "new-session"
                )
            });
            // A detached `new-session -d` is the server being started, never a client of a terminal.
            let detached = args.iter().any(|a| {
                a == "-d"
                    || (a.starts_with('-')
                        && !a.starts_with("--")
                        && a.len() > 2
                        && a[1..].contains('d')
                        && a[1..].bytes().all(|b| b.is_ascii_alphabetic()))
            });
            if named && attach && !detached {
                let env = std::fs::read(dir.join("environ")).unwrap_or_default();
                let tmpdir = env
                    .split(|b| *b == 0)
                    .filter_map(|kv| std::str::from_utf8(kv).ok())
                    .find_map(|kv| kv.strip_prefix("TMUX_TMPDIR="))
                    .unwrap_or(socket_root)
                    .to_owned();
                // A client's stdin is its terminal: a pty or a VT, never /dev/null or a file.
                let tty = std::fs::read_link(dir.join("fd/0"))
                    .ok()
                    .map(|t| t.display().to_string())
                    .filter(|t| {
                        client_ok(t) && (t.starts_with("/dev/pts/") || t.starts_with("/dev/tty"))
                    });
                if let Some(tty) = tty {
                    return Some((
                        format!("{}/tmux-{uid}/{socket}", tmpdir.trim_end_matches('/')),
                        tty,
                    ));
                }
            }
        }
        stack.extend(children.get(&p).into_iter().flatten().copied());
    }
    None
}

fn focused_pid() -> Option<u32> {
    let out = Command::new("hyprctl")
        .args(["-j", "activewindow"])
        .stdin(Stdio::null())
        .output()
        .ok()?;
    let v: serde_json::Value = serde_json::from_slice(&out.stdout).ok()?;
    v.get("pid")
        .and_then(serde_json::Value::as_u64)
        .map(|p| p as u32)
        .filter(|p| *p > 1)
}

fn desktop(root: &Path) -> Result<(), String> {
    let v = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let cfg = config(&v, false)?;
    if !cfg.enabled {
        return Err("the launcher is disabled ([keybindings.tmux.launcher].enabled)".into());
    }
    if let Some((sock, tty)) = focused_pid()
        .and_then(|p| tmux_client_under(Path::new("/proc"), p, &cfg.socket, &cfg.socket_root))
    {
        let clients = tmux(
            cfg.command_timeout,
            &sock,
            &["list-clients", "-F", "#{client_name}"],
        )
        .unwrap_or_default();
        if clients.lines().any(|c| c == tty) {
            let pane = tmux(
                cfg.command_timeout,
                &sock,
                &["display-message", "-p", "-c", &tty, "#{pane_id}"],
            )?;
            let console = tmux(
                cfg.command_timeout,
                &sock,
                &[
                    "display-message",
                    "-p",
                    "-c",
                    &tty,
                    "#{?#{E:@mios-console-client},1,0}",
                ],
            )
            .unwrap_or_default()
                == "1";
            return open_on(root, &sock, &tty, &pane, console, None);
        }
    }
    // No tmux-os client is focused: a new terminal enters tmux-os and opens the launcher there.
    let mut argv = cfg.desktop_terminal.clone();
    argv.push("--launcher".into());
    Err(format!(
        "exec {}: {}",
        argv[0],
        Command::new(&argv[0]).args(&argv[1..]).exec()
    ))
}

// ---- entry ---------------------------------------------------------------------------------------------------

pub fn run(cmd: &LauncherCommand) -> Result<(), String> {
    match cmd {
        LauncherCommand::Open {
            root,
            client,
            console,
            start,
        } => {
            let pane = std::env::var("TMUX_PANE").unwrap_or_default();
            if !pane_ok(&pane) {
                return Err("TMUX_PANE must name the launching pane".into());
            }
            open_on(
                Path::new(root),
                &socket_from_env()?,
                client,
                &pane,
                console_arg(console)?,
                start.as_deref(),
            )
        }
        LauncherCommand::Pick {
            root,
            client,
            pane,
            console,
            start,
            result,
        } => {
            if !client_ok(client) || !pane_ok(pane) {
                return Err("invalid tmux client or pane".into());
            }
            pick(
                Path::new(root),
                client,
                pane,
                console_arg(console)?,
                start.as_deref(),
                result,
            )
        }
        LauncherCommand::Desktop { root } => desktop(Path::new(root)),
        LauncherCommand::List {
            root,
            client,
            pane,
            console,
            width,
            height,
        } => {
            let console = console_arg(console)?;
            let v = mios_resolver::resolve_merged(Some(Path::new(root)), false)
                .map_err(|e| e.to_string())?;
            let cfg = config(&v, console)?;
            let (ctx, listing) = match (socket_from_env(), client, pane) {
                (Ok(sock), Some(c), Some(p)) if client_ok(c) && pane_ok(p) => {
                    context(&cfg, &sock, c, p, console)
                }
                _ => (
                    Ctx {
                        client: String::new(),
                        pane: String::new(),
                        console,
                        state: PaneState::default(),
                        sessions: vec![],
                        all_session_names: vec![],
                        running: vec![],
                    },
                    String::new(),
                ),
            };
            let sections = sections_for(&v, &cfg, &ctx, &listing)?;
            let mut out = serde_json::json!({ "sections": sections });
            if let Some(w) = width {
                out["screen"] = serde_json::Value::String(render_text(
                    &cfg,
                    sections.clone(),
                    *w,
                    *height,
                    None,
                ));
            }
            println!(
                "{}",
                serde_json::to_string_pretty(&out).map_err(|e| e.to_string())?
            );
            Ok(())
        }
    }
}

/// The launcher drawn into a plain-text screen (tests, `list --width`).
pub fn render_text(
    cfg: &Cfg,
    sections: Vec<Section>,
    w: u16,
    h: u16,
    start: Option<&str>,
) -> String {
    let area = Rect::new(0, 0, w, h);
    let mut ui = Ui::new(cfg, sections, area, start);
    let mut buf = Buffer::empty(area);
    ui.draw(&mut buf, area);
    buf_text(&buf)
}

fn buf_text(buf: &Buffer) -> String {
    let a = buf.area;
    let mut out = String::new();
    for y in a.y..a.y + a.height {
        let mut line = String::new();
        for x in a.x..a.x + a.width {
            line.push_str(buf[(x, y)].symbol());
        }
        out.push_str(line.trim_end());
        out.push('\n');
    }
    out
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::panic)]
mod tests {
    use super::*;

    fn ssot() -> Value {
        include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap()
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

    fn cfg() -> Cfg {
        config(&ssot(), false).unwrap()
    }

    const LISTING: &str = "$1\ttmux-os\t@1\t0\tbash\t2\t%1\t1\t0\tbash\t\t\t0\t1
$1\ttmux-os\t@1\t0\tbash\t2\t%2\t0\t0\tvim\t\t\t0\t1
$1\ttmux-os\t@1\t0\tbash\t2\t%5\t0\t1\thtop\t\t\t0\t1
$1\ttmux-os\t@2\t1\tbtop\t1\t%3\t1\t0\tbtop\t\t\t0\t1
$2\tmios\t@3\t0\tMiOS AI\t1\t%4\t1\t0\tagy\t\t\t1\t0
$3\tmios-float-1\t@4\t0\tbash\t1\t%6\t1\t0\tless\t$1\t@1\t0\t0
$4\tmios-workspace-%4\t@5\t0\tw\t1\t%7\t1\t0\tworker\t\t\t1\t0
";

    fn running() -> Vec<Item> {
        parse_running(LISTING, &cfg())
    }

    fn inst(items: &[Item], label: &str) -> Instance {
        items
            .iter()
            .find_map(|i| match &i.target {
                Target::Instance(x) if i.label == label => Some(x.clone()),
                _ => None,
            })
            .unwrap()
    }

    fn ctx() -> Ctx {
        Ctx {
            client: "/dev/pts/7".into(),
            pane: "%1".into(),
            console: false,
            state: PaneState {
                copy_mode: false,
                managed: false,
                session_id: "$1".into(),
            },
            sessions: vec![
                ("$1".into(), "tmux-os".into()),
                ("$2".into(), "mios".into()),
            ],
            all_session_names: vec![
                "tmux-os".into(),
                "mios".into(),
                "mios-float-1".into(),
                "pop-bash".into(),
            ],
            running: running()
                .into_iter()
                .filter_map(|i| {
                    if let Target::Instance(x) = i.target {
                        Some(x)
                    } else {
                        None
                    }
                })
                .collect(),
        }
    }

    fn installed(a: &str) -> bool {
        matches!(
            a,
            "btop"
                | "top"
                | "yazi"
                | "/usr/bin/mios-fanctl"
                | "/usr/bin/mios-controls"
                | "/usr/libexec/mios/mios-dashboard"
        )
    }

    fn sections() -> Vec<Section> {
        let v = ssot();
        let c = cfg();
        build(
            &c,
            running(),
            ai_items(&v).unwrap(),
            app_sections(&v, &c, installed).unwrap(),
            menu_sections(&v, &c, &PaneState::default()).unwrap(),
        )
    }

    #[test]
    fn running_lists_windows_floats_and_float_shells_but_never_hidden_sessions() {
        let r = running();
        let labels: Vec<&str> = r.iter().map(|i| i.label.as_str()).collect();
        assert_eq!(labels, ["bash", "htop", "btop", "MiOS AI", "less"], "{r:?}");
        let w = inst(&r, "bash");
        assert_eq!(
            (w.kind, w.pane_id.as_str(), w.panes),
            (Kind::Window, "%1", 2),
            "the window row carries its active tiled pane"
        );
        assert_eq!(r[0].detail, "tmux-os:0  2 panes");
        assert_eq!(inst(&r, "htop").kind, Kind::Floating);
        let f = inst(&r, "less");
        assert!(f.kind == Kind::FloatShell && f.origin && f.parent == "$1");
        assert_eq!(r[4].detail, "float shell of tmux-os");
        assert!(inst(&r, "MiOS AI").managed);
        assert!(!labels.contains(&"w"), "agent worker parking stays hidden");
        let mut c = cfg();
        c.instance_limit = 2;
        assert_eq!(parse_running(LISTING, &c).len(), 2);
        assert!(parse_running("garbage\nx\ty\n", &cfg()).is_empty());
    }

    #[test]
    fn sections_are_grouped_in_ssot_order_with_unique_keys() {
        let s = sections();
        let ids: Vec<&str> = s.iter().map(|x| x.id.as_str()).collect();
        assert_eq!(
            ids,
            ["running", "ai", "mios", "system", "files", "root", "pane", "window", "session"],
            "{ids:?}"
        );
        let keys: Vec<char> = s
            .iter()
            .flat_map(|x| x.items.iter().filter_map(|i| i.key))
            .collect();
        let unique: BTreeSet<char> = keys.iter().copied().collect();
        assert_eq!(
            keys.len(),
            unique.len(),
            "every key opens exactly one row: {keys:?}"
        );
        assert!(!unique.contains(&'q'), "q always closes");
        let ai = &s[1].items;
        assert_eq!(
            (ai[0].label.as_str(), ai[0].key),
            ("MiOS Terminal", Some('t')),
            "prefix keys are kept"
        );
        assert!(
            s.iter()
                .filter(|x| ["mios", "system", "files"].contains(&x.id.as_str()))
                .flat_map(|x| &x.items)
                .all(|i| i.key.is_some()),
            "every app has a key"
        );
        // The launcher is the root menu and the apps menu: their own rows and the submenu links are not repeated.
        let root = s.iter().find(|x| x.id == "root").unwrap();
        assert!(
            root.items
                .iter()
                .all(|i| !["Apps and TUIs...", "Pane", "Window", "Session"]
                    .contains(&i.label.as_str())),
            "{root:?}"
        );
        assert!(root.items.iter().any(|i| i.label == "Float shell"));
        let window = s.iter().find(|x| x.id == "window").unwrap();
        assert!(!window.items.iter().any(|i| i.label == "< Back"));
    }

    #[test]
    fn menu_rows_follow_the_launching_pane_state() {
        let v = ssot();
        let c = cfg();
        let off = menu_sections(&v, &c, &PaneState::default()).unwrap();
        let row = |s: &[Section], l: &str| {
            s.iter()
                .flat_map(|x| &x.items)
                .find(|i| i.label == l)
                .cloned()
                .unwrap()
        };
        assert!(
            !row(&off, "Go to top").enabled,
            "copy-mode rows need copy mode"
        );
        assert!(row(&off, "Float / dock").enabled);
        let on = menu_sections(
            &v,
            &c,
            &PaneState {
                copy_mode: true,
                managed: true,
                session_id: String::new(),
            },
        )
        .unwrap();
        assert!(row(&on, "Go to top").enabled);
        assert!(
            !row(&on, "Float / dock").enabled,
            "managed AI panes refuse layout changes"
        );
    }

    #[test]
    fn every_launcher_menu_row_is_bound_in_its_generated_key_table() {
        let conf =
            mios_unit_gen::render_keybindings(include_str!("../../../../usr/share/mios/mios.toml"))
                .unwrap()["usr/share/mios/tmux/mios-keys.tmux.conf"]
                .clone();
        for s in menu_sections(&ssot(), &cfg(), &PaneState::default()).unwrap() {
            for it in &s.items {
                let Target::MenuRow { menu, key } = &it.target else {
                    panic!()
                };
                let needle = format!("-T {} {} ", policy::menu_table(menu), policy::tmux_key(key));
                assert!(
                    conf.lines()
                        .any(|l| l.starts_with("bind-key -N") && l.contains(&needle)),
                    "{needle} missing for {}",
                    it.label
                );
            }
        }
    }

    #[test]
    fn two_columns_from_the_ssot_width_and_never_a_split_section() {
        let c = cfg();
        let s = sections();
        let wide = layout(&s, Rect::new(0, 0, 120, 40), &c);
        assert_eq!(wide.cols.len(), 2);
        assert_eq!(wide.col_x[1] - wide.col_x[0], wide.col_w + c.gap);
        for col in &wide.cols {
            assert!(
                matches!(col[0], Line::Header(_)),
                "a column starts with a section header"
            );
        }
        let left: BTreeSet<usize> = wide.cols[0]
            .iter()
            .filter_map(|l| {
                if let Line::Item(si, _) = l {
                    Some(*si)
                } else {
                    None
                }
            })
            .collect();
        let right: BTreeSet<usize> = wide.cols[1]
            .iter()
            .filter_map(|l| {
                if let Line::Item(si, _) = l {
                    Some(*si)
                } else {
                    None
                }
            })
            .collect();
        assert!(left.is_disjoint(&right), "no section spans both columns");
        let (a, b) = (wide.cols[0].len() as i64, wide.cols[1].len() as i64);
        let biggest = s.iter().map(|x| x.items.len() as i64 + 2).max().unwrap();
        assert!((a - b).abs() <= biggest, "balanced: {a} vs {b}");
        let narrow = layout(&s, Rect::new(0, 0, 60, 40), &c);
        assert_eq!(narrow.cols.len(), 1, "a phone-width popup keeps one column");
        // Negative control: the old single list is what a narrow width still gets.
        assert!(narrow.cols[0].len() > a as usize);
    }

    #[test]
    fn rendering_has_headers_an_aligned_key_column_and_two_columns() {
        let c = cfg();
        let screen = render_text(&c, sections(), 110, 44, None);
        let lines: Vec<&str> = screen.lines().collect();
        assert!(lines[0].trim() == "MiOS", "centred title: {:?}", lines[0]);
        let ai = lines.iter().find(|l| l.contains("AI and agents")).unwrap();
        assert!(
            ai.contains("\u{2500}\u{2500}"),
            "headers carry a rule: {ai}"
        );
        assert!(
            lines
                .iter()
                .any(|l| l.contains("Running") && l.contains("Pane"))
                || lines.iter().any(|l| l.contains("Pane \u{2500}")),
            "{screen}"
        );
        // The key column: a key right-aligned in key_width, two spaces, the label.
        let t = lines.iter().find(|l| l.contains("MiOS Terminal")).unwrap();
        let at = t.find("MiOS Terminal").unwrap();
        assert_eq!(&t[at - 3..at], "t  ", "{t}");
        let split = lines.iter().find(|l| l.contains("Split below")).unwrap();
        let sat = split.find("Split below").unwrap();
        assert_eq!(&split[sat - 3..sat], "s  ");
        let col2 = c.gap as usize + (108 - c.gap as usize) / 2;
        assert!(
            lines.iter().any(|l| l.chars().count() > col2 + 4),
            "something sits in the second column:\n{screen}"
        );
        assert!(lines.last().unwrap().contains("Tab options"), "help line");
        let vt = render_text(&config(&ssot(), true).unwrap(), sections(), 110, 44, None);
        assert!(
            vt.lines().all(|l| l.is_ascii()),
            "console rendering is ASCII:\n{vt}"
        );
    }

    #[test]
    fn controller_and_keyboard_vocabulary() {
        let k = |code: KeyCode, m: KeyModifiers| KeyEvent::new(code, m);
        let mut p = false;
        let none = KeyModifiers::NONE;
        assert_eq!(map_key(k(KeyCode::Down, none), "C-b", &mut p), Input::Down);
        assert_eq!(
            map_key(k(KeyCode::Enter, none), "C-b", &mut p),
            Input::Activate
        );
        assert_eq!(
            map_key(k(KeyCode::Enter, KeyModifiers::SHIFT), "C-b", &mut p),
            Input::Options,
            "A held"
        );
        assert_eq!(
            map_key(k(KeyCode::Tab, none), "C-b", &mut p),
            Input::Options,
            "West"
        );
        assert_eq!(
            map_key(k(KeyCode::BackTab, KeyModifiers::SHIFT), "C-b", &mut p),
            Input::NextSection
        );
        assert_eq!(map_key(k(KeyCode::Esc, none), "C-b", &mut p), Input::Back);
        assert_eq!(
            map_key(k(KeyCode::Char('c'), KeyModifiers::CONTROL), "C-b", &mut p),
            Input::Back,
            "B held"
        );
        assert_eq!(
            map_key(k(KeyCode::Char('q'), none), "C-b", &mut p),
            Input::Back
        );
        assert_eq!(
            map_key(k(KeyCode::Char('b'), KeyModifiers::CONTROL), "C-b", &mut p),
            Input::Nothing
        );
        assert_eq!(
            map_key(k(KeyCode::Char('n'), none), "C-b", &mut p),
            Input::PageDown,
            "trigger: tmux:key n"
        );
        map_key(k(KeyCode::Char('b'), KeyModifiers::CONTROL), "C-b", &mut p);
        assert_eq!(
            map_key(k(KeyCode::Char('p'), none), "C-b", &mut p),
            Input::PageUp,
            "trigger: tmux:key p"
        );
        map_key(k(KeyCode::Char('b'), KeyModifiers::CONTROL), "C-b", &mut p);
        assert_eq!(
            map_key(k(KeyCode::Char(' '), none), "C-b", &mut p),
            Input::Back,
            "Guide: tmux:menu closes"
        );
        assert_eq!(
            map_key(k(KeyCode::Char('t'), none), "C-b", &mut p),
            Input::Key('t')
        );
        assert_eq!(
            map_key(k(KeyCode::Char(' '), none), "C-b", &mut p),
            Input::Activate
        );
    }

    fn ui_for(c: &Cfg, w: u16) -> Ui<'_> {
        Ui::new(c, sections(), Rect::new(0, 0, w, 40), None)
    }

    #[test]
    fn navigation_moves_between_rows_columns_and_sections() {
        let c = cfg();
        let x = ctx();
        let none = |_: &str| None;
        let mut ui = ui_for(&c, 120);
        assert_eq!(
            ui.selected().unwrap().label,
            "bash",
            "starts on the running section"
        );
        ui.input(Input::Down, &x, &none);
        assert_eq!(ui.selected().unwrap().label, "htop");
        ui.input(Input::Wheel { up: true }, &x, &none);
        assert_eq!(
            ui.selected().unwrap().label,
            "bash",
            "the wheel (left stick) moves the selection"
        );
        ui.input(Input::Right, &x, &none);
        assert_eq!(
            ui.sel_order[ui.sel].0, 1,
            "Right moves to the second column"
        );
        ui.input(Input::Left, &x, &none);
        assert_eq!(ui.sel_order[ui.sel].0, 0);
        ui.input(Input::Home, &x, &none);
        ui.input(Input::NextSection, &x, &none);
        assert_eq!(ui.selected().unwrap().label, "MiOS Terminal");
        ui.input(Input::End, &x, &none);
        let last = ui.sel;
        ui.input(Input::Down, &x, &none);
        assert_eq!(ui.sel, last, "Down at the end stays");
        ui.input(Input::Home, &x, &none);
        assert_eq!(ui.sel, 0);
        let mut apps = Ui::new(&c, sections(), Rect::new(0, 0, 120, 40), Some("apps"));
        assert!(
            matches!(apps.selected().unwrap().target, Target::App { .. }),
            "prefix + launcher_key starts on the apps"
        );
        assert_eq!(apps.input(Input::Back, &x, &none), Outcome::Close);
        // One column: Left/Right jump between sections instead.
        let mut one = ui_for(&c, 60);
        one.input(Input::Right, &x, &none);
        assert_eq!(one.selected().unwrap().label, "MiOS Terminal");
    }

    #[test]
    fn activation_routes_every_row_kind_to_the_client() {
        let c = cfg();
        let x = ctx();
        assert_eq!(
            activate(&Target::Prefix { key: "a".into() }, &c, &x),
            vec![Op::Tmux {
                args: ["send-keys", "-c", "/dev/pts/7", "-K", "C-b", "a"]
                    .map(String::from)
                    .to_vec()
            }]
        );
        assert_eq!(
            activate(
                &Target::MenuRow {
                    menu: "pane".into(),
                    key: "v".into()
                },
                &c,
                &x
            ),
            vec![
                Op::Tmux {
                    args: ["switch-client", "-c", "/dev/pts/7", "-T", "mios-menu-pane"]
                        .map(String::from)
                        .to_vec()
                },
                Op::Tmux {
                    args: ["send-keys", "-c", "/dev/pts/7", "-K", "v"]
                        .map(String::from)
                        .to_vec()
                }
            ]
        );
        assert_eq!(
            activate(&Target::App { id: "btop".into() }, &c, &x),
            vec![Op::Exec {
                argv: [
                    TERMINAL,
                    "--app",
                    "btop",
                    "--client",
                    "/dev/pts/7",
                    "--console",
                    "0"
                ]
                .map(String::from)
                .to_vec(),
                pane: "%1".into()
            }]
        );
        let r = running();
        assert_eq!(
            activate(&Target::Instance(inst(&r, "btop")), &c, &x),
            vec![Op::Tmux {
                args: ["switch-client", "-c", "/dev/pts/7", "-t", "%3"]
                    .map(String::from)
                    .to_vec()
            }]
        );
        // A float-shell pane opens its parent's float shell.
        let f = activate(&Target::Instance(inst(&r, "less")), &c, &x);
        assert!(
            matches!(&f[..], [Op::Exec { argv, pane }] if argv.contains(&"shell".to_string()) && pane == "%1"),
            "{f:?}"
        );
    }

    fn choose(ov: &Overlay, label: &str) -> Choice {
        ov.entries
            .iter()
            .find(|e| e.label == label)
            .unwrap()
            .choice
            .clone()
    }

    #[test]
    fn instance_management_options() {
        let c = cfg();
        let x = ctx();
        let r = running();
        let item = r.iter().find(|i| i.label == "bash").unwrap();
        let ov = options(item, &c, &x, None);
        let labels: Vec<&str> = ov.entries.iter().map(|e| e.label.as_str()).collect();
        assert_eq!(
            labels,
            [
                "Go to",
                "Pop out to its own window",
                "Float the pane",
                "Move to window...",
                "Move to session...",
                "Rename...",
                "Close..."
            ]
        );
        let Choice::Ops(pop) = choose(&ov, "Pop out to its own window") else {
            panic!()
        };
        assert_eq!(
            pop[0],
            Op::Tmux {
                args: [
                    "new-session",
                    "-d",
                    "-s",
                    "pop-bash-2",
                    "-e",
                    "MIOS_SESSION_BARE=1",
                    "sleep",
                    "30"
                ]
                .map(String::from)
                .to_vec()
            },
            "a fresh name"
        );
        assert_eq!(
            pop[1],
            Op::Tmux {
                args: ["move-window", "-s", "@1", "-t", "=pop-bash-2:"]
                    .map(String::from)
                    .to_vec()
            }
        );
        assert_eq!(
            pop[2],
            Op::Tmux {
                args: ["kill-window", "-a", "-t", "@1"].map(String::from).to_vec()
            },
            "only the instance stays"
        );
        assert_eq!(
            pop.last().unwrap(),
            &Op::Desktop {
                session: "pop-bash-2".into()
            }
        );
        let Choice::Ops(lift) = choose(&ov, "Float the pane") else {
            panic!()
        };
        assert!(
            matches!(&lift[..], [Op::Exec { argv, pane }] if argv[1..4] == ["--float".to_string(), "lift".into(), "--client".into()] && pane == "%1")
        );
        // A single-pane window cannot float its only pane; a managed AI window refuses layout changes.
        let btop = options(r.iter().find(|i| i.label == "btop").unwrap(), &c, &x, None);
        assert!(
            !btop
                .entries
                .iter()
                .find(|e| e.label == "Float the pane")
                .unwrap()
                .enabled
        );
        let ai = options(
            r.iter().find(|i| i.label == "MiOS AI").unwrap(),
            &c,
            &x,
            None,
        );
        for l in [
            "Pop out to its own window",
            "Float the pane",
            "Move to window...",
            "Move to session...",
        ] {
            assert!(
                !ai.entries.iter().find(|e| e.label == l).unwrap().enabled,
                "{l}"
            );
        }
        // Floating panes and float-shell panes tile (dock) instead.
        let fl = options(r.iter().find(|i| i.label == "htop").unwrap(), &c, &x, None);
        let Choice::Ops(dock) = choose(&fl, "Tile the pane (dock it)") else {
            panic!()
        };
        assert!(matches!(&dock[..], [Op::Exec { pane, .. }] if pane == "%5"));
        let Choice::Ops(pop) = choose(&fl, "Pop out to its own window") else {
            panic!()
        };
        assert_eq!(
            pop[1],
            Op::Tmux {
                args: ["break-pane", "-d", "-s", "%5", "-t", "=pop-htop:"]
                    .map(String::from)
                    .to_vec()
            }
        );
    }

    #[test]
    fn close_confirms_with_no_first_and_moves_list_real_targets() {
        let c = cfg();
        let x = ctx();
        let r = running();
        let b = inst(&r, "bash");
        let cf = confirm(&b, &c);
        assert_eq!(cf.entries[cf.sel].label, "No", "No is the default");
        assert_eq!(
            choose(&cf, "Yes, close it"),
            Choice::Ops(vec![Op::Tmux {
                args: ["kill-window", "-t", "@1"].map(String::from).to_vec()
            }])
        );
        let pane_cf = confirm(&inst(&r, "htop"), &c);
        assert_eq!(
            choose(&pane_cf, "Yes, close it"),
            Choice::Ops(vec![Op::Tmux {
                args: ["kill-pane", "-t", "%5"].map(String::from).to_vec()
            }])
        );
        let mw = move_window(&b, &c, &x);
        let labels: Vec<&str> = mw.entries.iter().map(|e| e.label.as_str()).collect();
        assert_eq!(
            labels,
            ["tmux-os:1 btop", "New window", "Cancel"],
            "own window and managed windows are no targets"
        );
        assert_eq!(
            choose(&mw, "tmux-os:1 btop"),
            Choice::Ops(vec![Op::Tmux {
                args: ["join-pane", "-s", "%1", "-t", "%3"]
                    .map(String::from)
                    .to_vec()
            }])
        );
        let ms = move_session(&b, &c, &x);
        assert_eq!(ms.entries[0].label, "mios");
        assert_eq!(
            choose(&ms, "mios"),
            Choice::Ops(vec![Op::Tmux {
                args: ["move-window", "-s", "@1", "-t", "=mios:"]
                    .map(String::from)
                    .to_vec()
            }])
        );
        let Choice::Ops(new) = choose(&ms, "New session") else {
            panic!()
        };
        assert!(
            matches!(&new[0], Op::Tmux { args } if args[3] == "tmux-os-2"),
            "{new:?}"
        );
    }

    #[test]
    fn app_options_open_every_way_and_pop_out_to_a_desktop_window() {
        let c = cfg();
        let x = ctx();
        let it = Item {
            label: "btop".into(),
            detail: "window".into(),
            key: Some('1'),
            target: Target::App { id: "btop".into() },
            enabled: true,
        };
        let ov = options(
            &it,
            &c,
            &x,
            Some((
                "btop",
                vec![
                    "btop".into(),
                    "--config".into(),
                    "/etc/btop/btop.conf".into(),
                ],
            )),
        );
        let Choice::Ops(f) = choose(&ov, "Open as a floating pane") else {
            panic!()
        };
        assert!(
            matches!(&f[..], [Op::Exec { argv, .. }] if argv.ends_with(&["--mode".to_string(), "float".into()]))
        );
        let Choice::Ops(d) = choose(&ov, "Open in its own desktop window") else {
            panic!()
        };
        assert_eq!(
            d[0],
            Op::Tmux {
                args: [
                    "new-session",
                    "-d",
                    "-s",
                    "pop-btop",
                    "-n",
                    "btop",
                    "-e",
                    "MIOS_SESSION_BARE=1",
                    "--",
                    "btop",
                    "--config",
                    "/etc/btop/btop.conf"
                ]
                .map(String::from)
                .to_vec()
            }
        );
        assert_eq!(
            d.last().unwrap(),
            &Op::Desktop {
                session: "pop-btop".into()
            }
        );
        let without = options(&it, &c, &x, None);
        assert!(
            !without
                .entries
                .iter()
                .find(|e| e.label == "Open in its own desktop window")
                .unwrap()
                .enabled
        );
    }

    #[test]
    fn mouse_left_click_opens_and_right_click_shows_options() {
        let c = cfg();
        let x = ctx();
        let none = |_: &str| None;
        let mut ui = ui_for(&c, 120);
        let mut buf = Buffer::empty(Rect::new(0, 0, 120, 40));
        ui.draw(&mut buf, Rect::new(0, 0, 120, 40));
        let text = buf_text(&buf);
        let col =
            |line: &str, needle: &str| line[..line.find(needle).unwrap()].chars().count() as u16;
        let (y, line) = text
            .lines()
            .enumerate()
            .find(|(_, l)| l.contains("Split right"))
            .unwrap();
        let xpos = col(line, "Split right");
        let out = ui.input(
            Input::Click {
                x: xpos,
                y: y as u16,
                right: false,
            },
            &x,
            &none,
        );
        assert_eq!(
            out,
            Outcome::Done(activate(
                &Target::MenuRow {
                    menu: "pane".into(),
                    key: "v".into()
                },
                &c,
                &x
            ))
        );
        let (y, line) = text
            .lines()
            .enumerate()
            .find(|(_, l)| l.contains("btop") && !l.contains("System"))
            .unwrap();
        let xpos = col(line, "btop");
        assert_eq!(
            ui.input(
                Input::Click {
                    x: xpos,
                    y: y as u16,
                    right: true
                },
                &x,
                &none
            ),
            Outcome::Stay
        );
        assert!(
            ui.overlay
                .last()
                .is_some_and(|o| o.title.starts_with("Options")),
            "right-click = options"
        );
        assert_eq!(ui.input(Input::Back, &x, &none), Outcome::Stay);
        assert!(ui.overlay.is_empty(), "B backs out of the options first");
        // Clicking the header or a blank line chooses nothing.
        assert_eq!(
            ui.input(
                Input::Click {
                    x: 1,
                    y: 2,
                    right: false
                },
                &x,
                &none
            ),
            Outcome::Stay
        );
    }

    #[test]
    fn options_flow_by_keys_reaches_a_confirmed_close() {
        let c = cfg();
        let x = ctx();
        let none = |_: &str| None;
        let mut ui = ui_for(&c, 120);
        assert_eq!(ui.input(Input::Options, &x, &none), Outcome::Stay);
        ui.input(Input::End, &x, &none);
        assert_eq!(
            ui.input(Input::Activate, &x, &none),
            Outcome::Stay,
            "Close... asks first"
        );
        assert_eq!(ui.overlay.len(), 2);
        assert_eq!(
            ui.input(Input::Activate, &x, &none),
            Outcome::Stay,
            "Enter on the default (No) cancels"
        );
        assert_eq!(ui.overlay.len(), 1);
        ui.input(Input::Activate, &x, &none);
        ui.input(Input::Down, &x, &none);
        assert_eq!(
            ui.input(Input::Activate, &x, &none),
            Outcome::Done(vec![Op::Tmux {
                args: ["kill-window", "-t", "@1"].map(String::from).to_vec()
            }])
        );
    }

    #[test]
    fn popup_size_follows_the_ssot_and_the_client() {
        let c = cfg();
        assert_eq!(popup_size(&c, 200, 50), Some((94, 33)), "shared window cap");
        assert_eq!(
            popup_size(&c, 40, 12),
            Some((34, 10)),
            "small client bounds"
        );
        let mut legacy = c.clone();
        legacy.popup_policy =
            mios_resolver::popup::PopupPolicy::from_value(&Value::Table(Default::default()))
                .unwrap();
        assert_eq!(
            popup_size(&legacy, 200, 50),
            Some((172, 42)),
            "explicit compatibility mode"
        );
        assert_eq!(
            popup_size(&c, 30, 20),
            None,
            "too narrow: the too-small message instead"
        );
        assert!(
            client_ok("/dev/pts/3")
                && client_ok("client-12")
                && !client_ok("/etc/passwd")
                && !client_ok("/dev/../x")
        );
        assert!(pane_ok("%3") && !pane_ok("%") && !pane_ok("3"));
    }

    #[test]
    fn fresh_names_never_reuse_a_session() {
        let taken = vec!["pop-btop".to_string(), "pop-btop-2".into()];
        assert_eq!(fresh_name("pop-btop", &taken), "pop-btop-3");
        assert_eq!(fresh_name("pop-My Window;rm", &[]), "pop-my-window-rm");
        assert_eq!(fresh_name("", &[]), "instance");
    }

    #[test]
    fn a_tmux_client_is_found_under_the_focused_window() {
        let d = tempfile::tempdir().unwrap();
        let p = d.path();
        let mk = |pid: u32, ppid: u32, cmd: &[&str], env: &str, tty: Option<&Path>| {
            let dir = p.join(pid.to_string());
            std::fs::create_dir_all(dir.join("fd")).unwrap();
            std::fs::write(dir.join("stat"), format!("{pid} (x) S {ppid} 0 0")).unwrap();
            std::fs::write(dir.join("cmdline"), cmd.join("\0")).unwrap();
            std::fs::write(dir.join("environ"), env).unwrap();
            if let Some(t) = tty {
                std::os::unix::fs::symlink(t, dir.join("fd/0")).unwrap();
            }
        };
        mk(100, 1, &["alacritty"], "", None);
        mk(
            101,
            100,
            &["bash", "/usr/libexec/mios/mios-terminal"],
            "",
            None,
        );
        mk(
            102,
            101,
            &[
                "tmux",
                "-L",
                "mios-human",
                "attach-session",
                "-t",
                "=tmux-os",
            ],
            "TMUX_TMPDIR=/tmp\0",
            Some(Path::new("/dev/pts/9")),
        );
        mk(
            200,
            1,
            &["tmux", "-L", "mios-human", "new-session", "-d"],
            "",
            Some(Path::new("/dev/null")),
        );
        let uid = unsafe { libc::getuid() };
        assert_eq!(
            tmux_client_under(p, 100, "mios-human", "/tmp"),
            Some((format!("/tmp/tmux-{uid}/mios-human"), "/dev/pts/9".into()))
        );
        assert_eq!(
            tmux_client_under(p, 100, "other", "/tmp"),
            None,
            "another server is not tmux-os"
        );
        assert_eq!(
            tmux_client_under(p, 200, "mios-human", "/tmp"),
            None,
            "the server process is no client"
        );
        // A client whose stdin is no terminal (redirected) is not the focused window's client either.
        mk(
            300,
            1,
            &["tmux", "-L", "mios-human", "attach"],
            "",
            Some(Path::new("/dev/null")),
        );
        assert_eq!(
            tmux_client_under(p, 300, "mios-human", "/tmp"),
            None,
            "stdin /dev/null is no client terminal"
        );
        mk(
            400,
            1,
            &["tmux", "-L", "mios-human", "new-session", "-As", "x"],
            "",
            Some(Path::new("/dev/tty3")),
        );
        assert_eq!(
            tmux_client_under(p, 400, "mios-human", "/tmp"),
            Some((format!("/tmp/tmux-{uid}/mios-human"), "/dev/tty3".into())),
            "a VT client"
        );
    }

    #[test]
    fn steps_round_trip_through_the_result_file() {
        let ops = vec![
            Op::Tmux {
                args: vec!["a".into()],
            },
            Op::Desktop {
                session: "s".into(),
            },
            Op::Exec {
                argv: vec![TERMINAL.into()],
                pane: "%1".into(),
            },
        ];
        let j = serde_json::to_string(&ops).unwrap();
        assert!(j.contains("\"op\":\"tmux\"") && j.contains("\"op\":\"exec\""));
        assert_eq!(serde_json::from_str::<Vec<Op>>(&j).unwrap(), ops);
    }

    #[test]
    fn a_long_options_list_scrolls_with_its_selection_and_clicks_hit_the_row_shown() {
        let c = cfg();
        let mut ui = Ui::new(&c, sections(), Rect::new(0, 0, 60, 10), None);
        let pick =
            |n: usize| Choice::Ops(vec![tmux_op(&["display-message", &format!("target {n}")])]);
        ui.open_overlay(Overlay::new(
            "Move".into(),
            (0..20)
                .map(|n| entry(format!("target {n}"), true, pick(n)))
                .collect(),
            0,
        ));
        for _ in 0..15 {
            assert_eq!(ui.input(Input::Down, &ctx(), &|_| None), Outcome::Stay);
        }
        let area = ui.area;
        let mut buf = Buffer::empty(area);
        ui.draw(&mut buf, area);
        let text = buf_text(&buf);
        // Negative control: without the scroll offset the popup drew entries 0..7 and the selection (15) was off-screen.
        assert!(
            text.contains("target 15") && !text.contains("target 0 "),
            "{text}"
        );
        let ov = ui.overlay.last().unwrap();
        assert!(ov.offset > 0 && ov.sel == 15);
        let (first, r) = (ov.offset, ui.overlay_rect);
        match ui.input(
            Input::Click {
                x: r.x + 2,
                y: r.y + 1,
                right: false,
            },
            &ctx(),
            &|_| None,
        ) {
            Outcome::Done(ops) => assert_eq!(
                ops,
                vec![tmux_op(&["display-message", &format!("target {first}")])],
                "the row shown, not entries[0]"
            ),
            o => panic!("{o:?}"),
        }
    }
    fn find_sections() -> Vec<Section> {
        vec![
            Section {
                id: "edit".into(),
                title: "Editors".into(),
                empty: None,
                items: vec![Item {
                    label: "Quiet editor".into(),
                    detail: "Floating editor".into(),
                    key: Some('1'),
                    target: Target::App {
                        id: "quiet-edit".into(),
                    },
                    enabled: true,
                }],
            },
            Section {
                id: "system".into(),
                title: "System".into(),
                empty: None,
                items: vec![
                    Item {
                        label: "Process monitor".into(),
                        detail: "Main session".into(),
                        key: Some('2'),
                        target: Target::App { id: "btop".into() },
                        enabled: true,
                    },
                    Item {
                        label: "Copy selection".into(),
                        detail: "Copy mode required".into(),
                        key: Some('3'),
                        target: Target::MenuRow {
                            menu: "pane".into(),
                            key: "y".into(),
                        },
                        enabled: false,
                    },
                ],
            },
        ]
    }

    fn enter_query(ui: &mut Ui<'_>, query: &str) {
        assert_eq!(ui.input(Input::Search, &ctx(), &|_| None), Outcome::Stay);
        for character in query.chars() {
            let input = ui.key(KeyEvent::new(KeyCode::Char(character), KeyModifiers::NONE));
            assert_eq!(
                ui.input(input, &ctx(), &|_| None),
                Outcome::Stay,
                "typing must never activate a row or close the launcher"
            );
        }
    }

    #[test]
    fn launcher_find_keeps_quit_space_and_shortcuts_literal() {
        let c = cfg();
        let mut ui = Ui::new(&c, find_sections(), Rect::new(0, 0, 94, 33), None);
        let find = ui.key(KeyEvent::new(KeyCode::Char('f'), KeyModifiers::CONTROL));
        assert_eq!(find, Input::Search);
        ui.input(find, &ctx(), &|_| None);
        enter_query(&mut ui, "2");
        assert!(
            ui.selected().is_none(),
            "a typed row shortcut must be search text"
        );
        ui.input(Input::ClearQuery, &ctx(), &|_| None);
        enter_query(&mut ui, "qUIET editor");
        assert_eq!(ui.sel_order.len(), 1);
        assert_eq!(
            ui.selected().unwrap().target,
            Target::App {
                id: "quiet-edit".into()
            }
        );
        assert_eq!(
            ui.input(Input::Activate, &ctx(), &|_| None),
            Outcome::Done(activate(
                &Target::App {
                    id: "quiet-edit".into()
                },
                &c,
                &ctx()
            ))
        );
    }

    #[test]
    fn launcher_find_uses_section_and_detail_and_preserves_controller_paging() {
        let c = cfg();
        let mut ui = Ui::new(&c, find_sections(), Rect::new(0, 0, 60, 18), None);
        enter_query(&mut ui, "system main");
        assert_eq!(
            ui.selected().unwrap().target,
            Target::App { id: "btop".into() }
        );
        assert_eq!(
            ui.key(KeyEvent::new(KeyCode::Char('b'), KeyModifiers::CONTROL)),
            Input::Nothing
        );
        assert_eq!(
            ui.key(KeyEvent::new(KeyCode::Char('n'), KeyModifiers::NONE)),
            Input::PageDown
        );
        assert_eq!(ui.search.as_deref(), Some("system main"));
        let erase = ui.key(KeyEvent::new(KeyCode::Backspace, KeyModifiers::NONE));
        ui.input(erase, &ctx(), &|_| None);
        assert_eq!(ui.search.as_deref(), Some("system mai"));
        let clear = ui.key(KeyEvent::new(KeyCode::Char('u'), KeyModifiers::CONTROL));
        ui.input(clear, &ctx(), &|_| None);
        assert_eq!(ui.search.as_deref(), Some(""));
        assert_eq!(ui.sel_order.len(), 2);
    }

    #[test]
    fn launcher_find_empty_results_cannot_launch_and_escape_restores_focus_after_resize() {
        let c = cfg();
        let mut ui = Ui::new(&c, find_sections(), Rect::new(0, 0, 94, 33), None);
        ui.input(Input::End, &ctx(), &|_| None);
        let anchor = ui.selected().unwrap().target.clone();
        enter_query(&mut ui, "no such application");
        assert!(ui.selected().is_none());
        assert_eq!(ui.input(Input::Activate, &ctx(), &|_| None), Outcome::Stay);
        let area = Rect::new(5, 3, 34, 10);
        ui.relayout(area);
        let mut buffer = Buffer::empty(area);
        ui.draw(&mut buffer, area);
        let text = buf_text(&buffer);
        assert!(text.contains("No available matches"), "{text}");
        assert!(text.contains("Esc clear"), "{text}");
        assert_eq!(ui.input(Input::Back, &ctx(), &|_| None), Outcome::Stay);
        assert_eq!(ui.selected().unwrap().target, anchor);
        assert_eq!(ui.input(Input::Back, &ctx(), &|_| None), Outcome::Close);
    }

    #[test]
    fn launcher_find_keeps_disabled_actions_inert_and_clicks_target_filtered_rows() {
        let c = cfg();
        let area = Rect::new(7, 4, 60, 18);
        let mut ui = Ui::new(&c, find_sections(), area, None);
        enter_query(&mut ui, "copy");
        assert!(ui.selected().is_none());
        assert_eq!(ui.input(Input::Activate, &ctx(), &|_| None), Outcome::Stay);
        assert_eq!(ui.input(Input::Key('3'), &ctx(), &|_| None), Outcome::Stay);
        ui.input(Input::Back, &ctx(), &|_| None);
        assert_eq!(
            ui.input(
                Input::Click {
                    x: area.x + 2,
                    y: area.y + 1,
                    right: false
                },
                &ctx(),
                &|_| None
            ),
            Outcome::Stay
        );
        assert!(ui.search.is_some());
        enter_query(&mut ui, "monitor");
        let mut buffer = Buffer::empty(area);
        ui.draw(&mut buffer, area);
        let (column, line) = ui.sel_order[0];
        assert_eq!(
            ui.input(
                Input::Click {
                    x: ui.lay.col_x[column] + 4,
                    y: ui.lay.top + line as u16,
                    right: false
                },
                &ctx(),
                &|_| None
            ),
            Outcome::Done(activate(&Target::App { id: "btop".into() }, &c, &ctx()))
        );
    }

    #[test]
    fn launcher_compact_help_and_options_back_remain_visible() {
        let c = cfg();
        let mut ui = Ui::new(&c, find_sections(), Rect::new(0, 0, 34, 10), None);
        let mut buffer = Buffer::empty(ui.area);
        ui.draw(&mut buffer, ui.area);
        let text = buf_text(&buffer);
        assert!(text.contains("Esc close"), "{text}");
        ui.input(Input::Options, &ctx(), &|_| None);
        let mut buffer = Buffer::empty(ui.area);
        ui.draw(&mut buffer, ui.area);
        let text = buf_text(&buffer);
        assert!(text.contains("Esc back"), "{text}");
    }
}
