// AI-hint: MiOS-Tmux (tmux-os) executor behind mios-terminal --menu/--app/--float/--session-cycle/--new: the SSOT Apps and TUIs menu, TUI launch by mode, the per-session float shell (toggle, lift, dock, reap) and hidden-session-aware cycling. Every tmux call names its client and pane explicitly.
// AI-related: usr/libexec/mios/mios-terminal, tools/native/mios-resolver/src/terminal_tuis.rs, tools/native/mios-unit-gen/src/tmux_os.rs, /usr/share/mios/mios.toml [keybindings.tmux.float] [terminal.tui_launcher] [[terminal.tuis]]
// AI-functions: run, menu_argv, tui_spec, client_arg

#![forbid(unsafe_code)]

use mios_resolver::terminal_tuis::{self, Mode, Tui};
use mios_unit_gen::tmux_os as policy;
use std::os::unix::process::CommandExt;
use std::path::Path;
use std::process::Command;
use toml::Value;

/// Exit code for "this TUI is hidden here or not installed" (tui-spec, app).
pub const HIDDEN: u8 = 3;
const TERMINAL: &str = "/usr/libexec/mios/mios-terminal";
/// Menu shortcut keys handed to TUI rows in order (actions keep their own prefix keys).
const SHORTCUTS: &str = "1234567890bcdhijklnopqrsuvwxyz";
const MENU_X: &str = "#{?mouse_x,#{popup_mouse_centre_x},#{popup_centre_x}}";
const MENU_Y: &str = "#{?mouse_y,#{popup_mouse_y},#{popup_centre_y}}";

fn get<'a>(v: &'a Value, path: &str) -> Result<&'a Value, String> {
    path.split('.')
        .try_fold(v, |cur, key| cur.get(key))
        .ok_or_else(|| format!("missing SSOT key {path}"))
}

fn text(v: &Value, path: &str) -> Result<String, String> {
    let s = get(v, path)?.as_str().ok_or_else(|| format!("{path} must be a string"))?;
    if !policy::plain_text(s) {
        return Err(format!("{path} must be plain ASCII text"));
    }
    Ok(s.to_owned())
}

fn string(v: &Value, path: &str) -> Result<String, String> {
    get(v, path)?.as_str().map(str::to_owned).ok_or_else(|| format!("{path} must be a string"))
}

/// A tmux client name as `#{q:client_name}` delivers it: a tty path, or a `client-PID` control client.
pub fn client_arg(c: &str) -> Result<String, String> {
    let tty = c.strip_prefix("/dev/").is_some_and(|t| !t.is_empty() && !t.contains("..") && t.bytes().all(|b| b.is_ascii_alphanumeric() || b"/._-".contains(&b)));
    let ctl = c.strip_prefix("client-").is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()));
    if tty || ctl {
        Ok(c.to_owned())
    } else {
        Err(format!("invalid tmux client {c:?}"))
    }
}

fn pane_env() -> Result<String, String> {
    let p = std::env::var("TMUX_PANE").unwrap_or_default();
    if p.len() > 1 && p.starts_with('%') && p[1..].bytes().all(|b| b.is_ascii_digit()) {
        Ok(p)
    } else {
        Err("TMUX_PANE must name the pane (run-shell jobs get none or a stale one)".into())
    }
}

fn socket_env() -> Result<String, String> {
    let t = std::env::var("TMUX").map_err(|_| "not inside tmux ($TMUX is unset)".to_string())?;
    let sock = t.split(',').next().unwrap_or("");
    if sock.starts_with('/') && !sock.chars().any(char::is_control) {
        Ok(sock.to_owned())
    } else {
        Err("invalid $TMUX socket".into())
    }
}

fn tmux(args: &[&str]) -> Result<String, String> {
    let out = Command::new("tmux").args(args).output().map_err(|e| format!("tmux: {e}"))?;
    if out.status.success() {
        Ok(String::from_utf8_lossy(&out.stdout).trim_end_matches('\n').to_owned())
    } else {
        Err(format!("tmux {}: {}", args.first().unwrap_or(&""), String::from_utf8_lossy(&out.stderr).trim()))
    }
}

fn message(client: &str, msg: &str) -> Result<(), String> {
    tmux(&["display-message", "-c", client, "-l", msg]).map(|_| ())
}

fn exec_tmux(args: &[String]) -> String {
    format!("exec tmux: {}", Command::new("tmux").args(args).exec())
}

fn helper(args: &str) -> String {
    format!("run-shell -b -c '#{{pane_current_path}}' \"TMUX_PANE=#{{pane_id}} {TERMINAL} {args}\"")
}

/// `[theme.tmux.console]` menu/popup flags for a Linux-console client.
fn console_flags(merged: &Value, menu: bool) -> Result<Vec<String>, String> {
    let mut out = vec!["-b".to_owned(), string(merged, "theme.tmux.console.border_lines")?];
    if menu {
        out.extend(["-s".into(), string(merged, "theme.tmux.console.menu_style")?]);
        out.extend(["-S".into(), string(merged, "theme.tmux.console.menu_border_style")?]);
        out.extend(["-H".into(), string(merged, "theme.tmux.console.menu_selected_style")?]);
    }
    Ok(out)
}

fn capitalised(s: &str) -> String {
    let mut c = s.chars();
    c.next().map(|f| f.to_ascii_uppercase().to_string() + c.as_str()).unwrap_or_default()
}

/// `display-menu` argv (without `tmux`) for the Apps and TUIs menu. `rows` is the client height; when the flat
/// list does not fit, the menu pages by section. `None` means even the paged menu does not fit.
pub fn menu_argv(
    merged: &Value,
    console: bool,
    rows: u32,
    section: Option<&str>,
    is_exec: impl Fn(&str) -> bool,
) -> Result<Option<Vec<String>>, String> {
    let l = terminal_tuis::launcher(merged)?;
    let cat = terminal_tuis::catalog(merged)?;
    let tuis = if l.enabled { terminal_tuis::visible(&cat, console, &is_exec) } else { Vec::new() };
    let cflag = if console { 1 } else { 0 };
    let mut actions: Vec<(String, String, String)> = Vec::new();
    for a in get(merged, "keybindings.actions")?.as_array().ok_or("keybindings.actions must be an array")? {
        let label = text(a, "label")?;
        let key = string(a, "key")?;
        actions.push((label, key, string(a, "tmux_command")?));
    }
    let item = |t: &Tui, key: String| -> [String; 3] {
        [t.label.clone(), key, helper(&format!("--app {} --client #{{q:client_name}} --console {cflag}", t.id))]
    };
    let mut items: Vec<[String; 3]> = Vec::new();
    let sep = || [String::new(), String::new(), String::new()];
    let mut title = l.title.clone();
    let mut keys = SHORTCUTS.chars().map(String::from);
    if let Some(sec) = section {
        if !l.sections.iter().any(|s| s == sec) {
            return Err(format!("unknown section {sec:?}"));
        }
        title = format!("{} > {}", l.title, capitalised(sec));
        for t in tuis.iter().filter(|t| t.section == sec) {
            items.push(item(t, keys.next().unwrap_or_default()));
        }
        items.push(sep());
        items.push(["< Back".into(), "BSpace".into(), helper(&format!("--menu apps --client #{{q:client_name}} --console {cflag} --rows #{{client_height}}"))]);
    } else {
        for (label, key, command) in &actions {
            items.push([label.clone(), key.clone(), command.clone()]);
        }
        let groups: Vec<(&String, Vec<&&Tui>)> = l
            .sections
            .iter()
            .map(|s| (s, tuis.iter().filter(|t| &t.section == s).collect::<Vec<_>>()))
            .filter(|(_, g)| !g.is_empty())
            .collect();
        let flat_rows = actions.len() + tuis.len() + groups.len() + 2;
        if flat_rows as u32 <= rows {
            for (_, g) in &groups {
                items.push(sep());
                for t in g {
                    items.push(item(t, keys.next().unwrap_or_default()));
                }
            }
        } else {
            items.push(sep());
            for (s, g) in &groups {
                items.push([
                    format!("{} ({}) >", capitalised(s), g.len()),
                    keys.next().unwrap_or_default(),
                    helper(&format!("--menu apps --section {s} --client #{{q:client_name}} --console {cflag} --rows #{{client_height}}")),
                ]);
            }
        }
        if tuis.is_empty() && actions.is_empty() {
            return Ok(None);
        }
    }
    while items.last().is_some_and(|i| i[0].is_empty()) {
        items.pop();
    }
    let height = items.len() as u32 + 2;
    if height > rows {
        return Ok(None);
    }
    // No -M: it would make tmux ignore -C, and Enter alone must choose the first item (controller vocabulary).
    let mut argv: Vec<String> = vec!["display-menu".into(), "-T".into(), title, "-C".into(), "0".into(), "-x".into(), MENU_X.into(), "-y".into(), MENU_Y.into()];
    if console {
        argv.extend(console_flags(merged, true)?);
    }
    for [label, key, command] in items {
        if label.is_empty() {
            argv.push(String::new());
        } else {
            argv.extend([label, key, command]);
        }
    }
    Ok(Some(argv))
}

/// NUL-separated spec fields of a launchable TUI: mode, label, width, height, hold (0|1), then the argv.
pub fn tui_spec(merged: &Value, id: &str, console: bool, is_exec: impl Fn(&str) -> bool) -> Result<Option<Vec<String>>, String> {
    let l = terminal_tuis::launcher(merged)?;
    if !l.enabled {
        return Ok(None);
    }
    let cat = terminal_tuis::catalog(merged)?;
    let Some(t) = terminal_tuis::visible(&cat, console, is_exec).into_iter().find(|t| t.id == id) else {
        return Ok(None);
    };
    let mut out = vec![t.mode.as_str().to_owned(), t.label.clone(), l.popup_width.clone(), l.popup_height.clone(), if t.hold { "1".into() } else { "0".into() }];
    out.extend(t.argv_for(console).iter().cloned());
    Ok(Some(out))
}

struct Args {
    verb: String,
    sub: Option<String>,
    client: Option<String>,
    console: bool,
    rows: u32,
    section: Option<String>,
    session: Option<String>,
    socket: Option<String>,
    id: Option<String>,
}

fn parse(argv: &[String]) -> Result<Args, String> {
    let mut a = Args { verb: String::new(), sub: None, client: None, console: false, rows: 24, section: None, session: None, socket: None, id: None };
    let mut it = argv.iter();
    a.verb = it.next().cloned().ok_or("tmux-os: missing verb")?;
    while let Some(arg) = it.next() {
        let mut val = || it.next().cloned().ok_or_else(|| format!("{arg} requires a value"));
        match arg.as_str() {
            "--client" => a.client = Some(client_arg(&val()?)?),
            "--console" => {
                a.console = match val()?.as_str() {
                    "1" => true,
                    "0" => false,
                    other => return Err(format!("--console must be 0 or 1, not {other:?}")),
                }
            }
            "--rows" => a.rows = val()?.parse().ok().filter(|r| (1..=1000).contains(r)).ok_or("--rows must be 1..1000")?,
            "--section" => a.section = Some(val()?),
            "--session" => a.session = Some(val()?),
            "--socket" => a.socket = Some(val()?),
            s if !s.starts_with('-') && a.sub.is_none() => a.sub = Some(s.to_owned()),
            s if !s.starts_with('-') && a.id.is_none() => a.id = Some(s.to_owned()),
            other => return Err(format!("tmux-os: unexpected argument {other:?}")),
        }
    }
    Ok(a)
}

fn need_client(a: &Args) -> Result<String, String> {
    a.client.clone().ok_or_else(|| "--client is required".into())
}

fn float_cfg(merged: &Value) -> Result<&Value, String> {
    let f = get(merged, "keybindings.tmux.float")?;
    if f.get("enabled").and_then(Value::as_bool) != Some(true) {
        return Err("floating panes are disabled ([keybindings.tmux.float].enabled)".into());
    }
    Ok(f)
}

fn float_name(merged: &Value, session_id: &str) -> Result<String, String> {
    let prefix = string(merged, "keybindings.tmux.float.session_prefix")?;
    let digits = session_id.trim_start_matches('$');
    if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return Err(format!("invalid session id {session_id:?}"));
    }
    Ok(format!("{prefix}{digits}"))
}

fn ensure_float(merged: &Value, name: &str, parent: &str, cwd: &str) -> Result<(), String> {
    // Session options take a pane target: `=NAME:` names the session exactly (`=NAME` alone does not resolve).
    let target = format!("={name}:");
    if tmux(&["has-session", "-t", &format!("={name}")]).is_err() {
        tmux(&["new-session", "-d", "-s", name, "-c", cwd, "-e", "MIOS_SESSION_BARE=1"])?;
        tmux(&["set-option", "-t", &target, "@mios-bare", "1"])?;
        tmux(&["set-option", "-t", &target, "@mios-float-parent", parent])?;
        let status = string(merged, "keybindings.tmux.float.shell_status")?;
        if !matches!(status.as_str(), "on" | "off") {
            return Err("[keybindings.tmux.float].shell_status must be on or off".into());
        }
        tmux(&["set-option", "-t", &target, "status", &status])?;
    }
    Ok(())
}

/// Kill float shells whose parent session is gone. Run on every float open; the session-closed hook does the same
/// inside tmux without a process (mios-unit-gen tmux_os::float_reaper).
fn reap(merged: &Value) -> Result<(), String> {
    let prefix = string(merged, "keybindings.tmux.float.session_prefix")?;
    let rows = tmux(&["list-sessions", "-F", "#{session_id}\t#{session_name}\t#{@mios-float-parent}"]).unwrap_or_default();
    let ids: Vec<&str> = rows.lines().filter_map(|l| l.split('\t').next()).collect();
    for line in rows.lines() {
        let f: Vec<&str> = line.split('\t').collect();
        if f.len() == 3 && f[1].starts_with(&prefix) && !f[2].is_empty() && !ids.contains(&f[2]) {
            let _ = tmux(&["kill-session", "-t", &format!("={}", f[1])]);
        }
    }
    Ok(())
}

fn open_popup(merged: &Value, client: &str, pane: &str, name: &str, cwd: &str, console: bool) -> Result<String, String> {
    let f = float_cfg(merged)?;
    let sock = socket_env()?;
    let me = std::env::current_exe().map_err(|e| e.to_string())?;
    let mut args: Vec<String> = vec![
        "display-popup".into(),
        "-c".into(),
        client.into(),
        "-t".into(),
        pane.into(),
        "-E".into(),
        "-w".into(),
        string(f, "shell_width")?,
        "-h".into(),
        string(f, "shell_height")?,
        "-T".into(),
        string(f, "shell_title")?,
        "-d".into(),
        cwd.into(),
    ];
    if console {
        args.extend(console_flags(merged, false)?);
    }
    args.push("--".into());
    args.extend([me.display().to_string(), "tmux-os".into(), "float".into(), "attach".into(), "--session".into(), name.into(), "--socket".into(), sock, "--console".into(), if console { "1".into() } else { "0".into() }]);
    Ok(exec_tmux(&args))
}

fn info(client: &str, pane: &str, fmt: &str) -> Result<Vec<String>, String> {
    Ok(tmux(&["display-message", "-p", "-c", client, "-t", pane, fmt])?.split('\t').map(str::to_owned).collect())
}

fn float(merged: &Value, a: &Args) -> Result<String, String> {
    let sub = a.sub.as_deref().unwrap_or("");
    if sub == "reap" {
        return reap(merged).map(|_| String::new());
    }
    let prefix = string(float_cfg(merged)?, "session_prefix")?;
    if sub == "attach" {
        // Inside the popup: record this inner client's tty so the toggle key closes only this popup, give a
        // console client its console TERM, then attach to the float session on the same server.
        let name = a.session.clone().ok_or("--session is required")?;
        if !name.starts_with(&prefix) {
            return Err(format!("{name:?} is not a float session"));
        }
        let sock = a.socket.clone().ok_or("--socket is required")?;
        let tty = std::fs::read_link("/proc/self/fd/0").map_err(|e| format!("popup tty: {e}"))?;
        let target = format!("={name}:");
        let mut set = Command::new("tmux");
        set.args(["-S", &sock, "set-option", "-t", &target, "@mios-float-client"]).arg(&tty).env_remove("TMUX");
        set.output().map_err(|e| e.to_string())?;
        let mut cmd = Command::new("tmux");
        cmd.args(["-S", &sock, "attach-session", "-t", &target]).env_remove("TMUX").env_remove("TMUX_PANE");
        if a.console {
            let term = policy::console_terms(merged)?.into_iter().find(|t| !t.contains(['*', '?'])).unwrap_or_else(|| "linux".into());
            cmd.env("TERM", term);
        }
        return Err(format!("exec tmux attach: {}", cmd.exec()));
    }
    let client = need_client(a)?;
    let pane = pane_env()?;
    let cwd = std::env::current_dir().map(|d| d.display().to_string()).unwrap_or_else(|_| "/".into());
    let f = info(&client, &pane, "#{session_name}\t#{session_id}\t#{client_tty}\t#{pane_floating_flag}\t#{window_id}\t#{window_panes}\t#{@mios-float-origin}\t#{E:@mios-managed-pane}")?;
    if f.len() != 8 {
        return Err("unexpected tmux format reply".into());
    }
    let (session, sid, tty, floating, window, panes, origin, managed) = (&f[0], &f[1], &f[2], f[3] == "1", &f[4], &f[5], &f[6], f[7] == "1");
    let in_float = session.starts_with(&prefix);
    let recorded = || tmux(&["show-option", "-qv", "-t", &format!("={session}:"), "@mios-float-client"]).unwrap_or_default();
    match sub {
        "shell" => {
            if in_float {
                if recorded() == *tty {
                    tmux(&["detach-client", "-t", &client])?;
                } else if tmux(&["switch-client", "-c", &client, "-l"]).is_err() {
                    tmux(&["detach-client", "-t", &client])?;
                }
                return Ok(String::new());
            }
            let name = float_name(merged, sid)?;
            let _ = reap(merged);
            ensure_float(merged, &name, sid, &cwd)?;
            Err(open_popup(merged, &client, &pane, &name, &cwd, a.console)?)
        }
        "lift" => {
            if managed {
                message(&client, &text(merged, "keybindings.tmux.managed_refusal_message")?)?;
                return Ok(String::new());
            }
            if in_float && !origin.is_empty() {
                // Dock a lifted pane back into the window it came from, then close this popup.
                if tmux(&["display-message", "-p", "-t", origin, "#{window_id}"]).is_err() {
                    message(&client, &text(merged, "keybindings.tmux.float.origin_gone_message")?)?;
                    return Ok(String::new());
                }
                tmux(&["join-pane", "-h", "-s", &pane, "-t", origin])?;
                tmux(&["set-option", "-p", "-u", "-t", &pane, "@mios-float-origin"])?;
                if recorded() == *tty {
                    let _ = tmux(&["detach-client", "-t", &client]);
                }
                return Ok(String::new());
            }
            if floating {
                // Dock a native floating pane into the largest tiled pane of its window.
                let rows = tmux(&["list-panes", "-t", window, "-F", "#{pane_id}\t#{pane_floating_flag}\t#{e|*:#{pane_width},#{pane_height}}"])?;
                let target = rows
                    .lines()
                    .filter_map(|l| {
                        let c: Vec<&str> = l.split('\t').collect();
                        (c.len() == 3 && c[1] != "1").then(|| (c[2].parse::<u64>().unwrap_or(0), c[0].to_owned()))
                    })
                    .max();
                match target {
                    Some((_, t)) => tmux(&["join-pane", "-h", "-s", &pane, "-t", &t])?,
                    None => return message(&client, &text(merged, "keybindings.tmux.float.single_pane_message")?).map(|_| String::new()),
                };
                return Ok(String::new());
            }
            if panes.parse::<u32>().unwrap_or(0) < 2 || in_float {
                message(&client, &text(merged, "keybindings.tmux.float.single_pane_message")?)?;
                return Ok(String::new());
            }
            let name = float_name(merged, sid)?;
            ensure_float(merged, &name, sid, &cwd)?;
            tmux(&["break-pane", "-d", "-s", &pane, "-t", &format!("={name}:")])?;
            tmux(&["set-option", "-p", "-t", &pane, "@mios-float-origin", window])?;
            tmux(&["select-window", "-t", &pane])?;
            Err(open_popup(merged, &client, &pane, &name, &cwd, a.console)?)
        }
        other => Err(format!("tmux-os float: unknown action {other:?}")),
    }
}

fn session_cycle(merged: &Value, a: &Args) -> Result<(), String> {
    let client = need_client(a)?;
    let dir = a.sub.as_deref().unwrap_or("");
    if !matches!(dir, "next" | "prev") {
        return Err("session-cycle takes next or prev".into());
    }
    let hidden = policy::hidden_session_prefixes(merged)?;
    // #{client_session}: without -t, #{session_name} would be the server's most recently used session, not the client's.
    let current = tmux(&["display-message", "-p", "-c", &client, "#{client_session}"])?;
    let all: Vec<String> = tmux(&["list-sessions", "-F", "#{session_name}"])?.lines().map(str::to_owned).collect();
    let Some(at) = all.iter().position(|s| *s == current) else { return Ok(()) };
    let n = all.len();
    let order: Vec<usize> = if dir == "next" { (1..n).map(|i| (at + i) % n).collect() } else { (1..n).map(|i| (at + n - i) % n).collect() };
    if let Some(i) = order.into_iter().find(|&i| !policy::is_hidden_session(&hidden, &all[i])) {
        tmux(&["switch-client", "-c", &client, "-t", &format!("={}", all[i])])?;
    }
    Ok(())
}

fn new_session(merged: &Value, a: &Args) -> Result<(), String> {
    let client = need_client(a)?;
    let base = string(merged, "terminal.sessions.new_session_name")?;
    if base.is_empty() || !base.bytes().all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b)) {
        return Err("[terminal.sessions].new_session_name must be [A-Za-z0-9._-]".into());
    }
    let cwd = std::env::current_dir().map(|d| d.display().to_string()).unwrap_or_else(|_| "/".into());
    let mut name = base.clone();
    let mut n = 2;
    while tmux(&["has-session", "-t", &format!("={name}")]).is_ok() {
        name = format!("{base}-{n}");
        n += 1;
    }
    tmux(&["new-session", "-d", "-s", &name, "-c", &cwd, "-e", "MIOS_SESSION_BARE=1"])?;
    tmux(&["set-option", "-t", &format!("={name}:"), "@mios-bare", "1"])?;
    tmux(&["switch-client", "-c", &client, "-t", &format!("={name}")])?;
    Ok(())
}

/// The popup scope can start a process here (installed, and e.g. a reachable user manager on SSH or a VT):
/// a scope that fails would flash the popup shut, so the caller opens a window instead.
fn scope_works(scope: &[String]) -> bool {
    terminal_tuis::is_installed(&scope[0])
        && Command::new(&scope[0])
            .args(&scope[1..])
            .arg("true")
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .status()
            .is_ok_and(|s| s.success())
}

fn app(merged: &Value, a: &Args) -> Result<String, String> {
    let client = need_client(a)?;
    let pane = pane_env()?;
    let id = a.sub.clone().ok_or("app needs a TUI id")?;
    let cat = terminal_tuis::catalog(merged)?;
    let l = terminal_tuis::launcher(merged)?;
    let visible = if l.enabled { terminal_tuis::visible(&cat, a.console, terminal_tuis::is_installed) } else { Vec::new() };
    let Some(t) = visible.into_iter().find(|t| t.id == id) else {
        message(&client, &l.empty_message)?;
        return Ok(String::new());
    };
    let cwd = std::env::current_dir().map(|d| d.display().to_string()).unwrap_or_else(|_| "/".into());
    let argv = t.argv_for(a.console).to_vec();
    let mut mode = t.mode;
    if mode == Mode::Popup && !scope_works(&l.popup_scope) {
        mode = Mode::Window; // never run a popup TUI in the tmux server's own cgroup
    }
    if mode == Mode::Float && tmux(&["list-commands", "new-pane"]).is_err() {
        mode = Mode::Window;
    }
    let session = tmux(&["display-message", "-p", "-t", &pane, "#{session_id}"])?;
    let mut args: Vec<String> = match mode {
        Mode::Popup => {
            let mut v: Vec<String> = vec!["display-popup".into(), "-c".into(), client.clone(), "-t".into(), pane.clone()];
            if !t.hold {
                v.push("-E".into());
            }
            v.extend(["-w".into(), l.popup_width.clone(), "-h".into(), l.popup_height.clone(), "-T".into(), format!(" {} ", t.label), "-d".into(), cwd]);
            if a.console {
                v.extend(console_flags(merged, false)?);
            }
            v.push("--".into());
            v.extend(l.popup_scope.iter().cloned());
            v
        }
        Mode::Window => vec!["new-window".into(), "-t".into(), format!("{session}:"), "-n".into(), t.label.clone(), "-c".into(), cwd, "--".into()],
        Mode::Float => {
            let f = get(merged, "keybindings.tmux.float")?;
            let pct = |k: &str| -> Result<u32, String> {
                string(f, k)?.trim_end_matches('%').parse::<u32>().ok().filter(|n| (1..=100).contains(n)).ok_or_else(|| format!("[keybindings.tmux.float].{k} must be a percentage"))
            };
            let (w, h) = (pct("pane_width")?, pct("pane_height")?);
            vec!["new-pane".into(), "-t".into(), pane.clone(), "-x".into(), format!("{w}%"), "-y".into(), format!("{h}%"), "-X".into(), format!("{}%", (100 - w) / 2), "-Y".into(), format!("{}%", (100 - h) / 2), "-c".into(), cwd, "--".into()]
        }
    };
    args.extend(argv);
    Err(exec_tmux(&args))
}

/// `mios-gen tmux-os VERB ...`: runs inside a tmux job (TMUX and an explicit TMUX_PANE set by the binding).
/// Ok(()) after a command that returns; exec-ing verbs never return on success.
pub fn run(root: &Path, argv: &[String]) -> Result<u8, String> {
    let a = parse(argv)?;
    let merged = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    match a.verb.as_str() {
        "menu" => {
            if a.sub.as_deref() != Some("apps") {
                return Err("tmux-os menu: only the apps menu is dynamic".into());
            }
            let client = need_client(&a)?;
            let pane = pane_env()?;
            match menu_argv(&merged, a.console, a.rows, a.section.as_deref(), terminal_tuis::is_installed)? {
                None => {
                    let l = terminal_tuis::launcher(&merged)?;
                    let msg = if l.enabled { text(&merged, "keybindings.tmux.menu_too_small_message")? } else { l.empty_message };
                    message(&client, &msg)?;
                    Ok(0)
                }
                Some(m) => {
                    let mut args = vec![m[0].clone(), "-c".into(), client, "-t".into(), pane];
                    args.extend(m[1..].iter().cloned());
                    Err(exec_tmux(&args))
                }
            }
        }
        "app" => app(&merged, &a).map(|_| 0),
        "float" => float(&merged, &a).map(|_| 0),
        "session-cycle" => session_cycle(&merged, &a).map(|_| 0),
        "new" => new_session(&merged, &a).map(|_| 0),
        other => Err(format!("tmux-os: unknown verb {other:?}")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ssot() -> Value {
        include_str!("../../../../usr/share/mios/mios.toml").parse().unwrap()
    }

    #[test]
    fn client_names_are_validated() {
        assert!(client_arg("/dev/pts/3").is_ok() && client_arg("client-123").is_ok());
        for bad in ["", "/dev/../etc", "/tmp/x", "client-", "x;rm", "/dev/pts/3 -t"] {
            assert!(client_arg(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn apps_menu_lists_actions_first_then_installed_rows_only() {
        let d = ssot();
        let installed = |a: &str| matches!(a, "btop" | "top" | "/usr/bin/mios-fanctl");
        let m = menu_argv(&d, false, 60, None, installed).unwrap().unwrap();
        assert_eq!(m[0], "display-menu");
        assert!(m.windows(2).any(|w| w[0] == "-C" && w[1] == "0") && !m.iter().any(|a| a == "-M"), "{m:?}");
        let ai = m.iter().position(|a| a == "MiOS AI").unwrap();
        let btop = m.iter().position(|a| a == "btop").unwrap();
        assert!(ai < btop);
        assert!(!m.iter().any(|a| a == "htop" || a == "Midnight Commander"), "uninstalled rows are hidden");
        // Only validated ids reach the item commands, each naming its pane and client.
        let cmd = &m[btop + 2];
        assert!(cmd.contains("TMUX_PANE=#{pane_id}") && cmd.contains("--app btop --client #{q:client_name} --console 0"), "{cmd}");
    }

    #[test]
    fn console_menu_uses_argv_vt_rows_and_console_flags() {
        let d = ssot();
        let installed = |a: &str| matches!(a, "btop" | "yazi");
        let m = menu_argv(&d, true, 60, None, installed).unwrap().unwrap();
        assert!(m.windows(2).any(|w| w[0] == "-b" && w[1] == "single"));
        assert!(m.iter().any(|a| a == "btop") && !m.iter().any(|a| a == "yazi files"), "vt_ok = false rows are hidden");
        let spec = tui_spec(&d, "btop", true, installed).unwrap().unwrap();
        assert_eq!(&spec[..5], ["window", "btop", "90%", "80%", "0"]);
        assert_eq!(&spec[5..7], ["btop", "-t"]);
        assert!(tui_spec(&d, "yazi", true, installed).unwrap().is_none());
        assert!(tui_spec(&d, "htop", false, installed).unwrap().is_none());
    }

    #[test]
    fn small_clients_page_by_section_or_get_nothing() {
        let d = ssot();
        let all = |_: &str| true;
        let flat = menu_argv(&d, false, 60, None, all).unwrap().unwrap();
        assert!(flat.iter().any(|a| a == "btop"));
        let paged = menu_argv(&d, false, 16, None, all).unwrap().unwrap();
        assert!(!paged.iter().any(|a| a == "btop") && paged.iter().any(|a| a.starts_with("System (")), "{paged:?}");
        assert!(paged.iter().any(|a| a.contains("--menu apps --section system")));
        let page = menu_argv(&d, false, 16, Some("system"), all).unwrap().unwrap();
        assert!(page.iter().any(|a| a == "btop") && page.iter().any(|a| a == "< Back"));
        assert!(menu_argv(&d, false, 4, None, all).unwrap().is_none());
        assert!(menu_argv(&d, false, 60, Some("nope"), all).is_err());
    }

    #[test]
    fn float_session_names_follow_the_session_id() {
        let d = ssot();
        assert_eq!(float_name(&d, "$12").unwrap(), "mios-float-12");
        assert!(float_name(&d, "$x").is_err() && float_name(&d, "").is_err());
    }

    #[test]
    fn executor_arguments_parse_strictly() {
        let a = parse(&["menu".into(), "apps".into(), "--client".into(), "/dev/pts/1".into(), "--console".into(), "1".into(), "--rows".into(), "20".into()]).unwrap();
        assert!(a.console && a.rows == 20 && a.sub.as_deref() == Some("apps"));
        assert!(parse(&["menu".into(), "--console".into(), "2".into()]).is_err());
        assert!(parse(&["menu".into(), "--client".into(), "/etc/passwd".into()]).is_err());
        assert!(parse(&["menu".into(), "--rows".into(), "0".into()]).is_err());
    }
}
