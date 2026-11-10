// AI-hint: MiOS-Tmux (tmux-os) executor behind mios-terminal --menu/--app/--float/--session-cycle/--new: the SSOT Apps and TUIs menu, TUI launch by mode, the per-session float shell (toggle, lift, dock, reap) and hidden-session-aware cycling. Every tmux call names its client and pane explicitly.
// AI-related: usr/libexec/mios/mios-terminal, tools/native/mios-resolver/src/terminal_tuis.rs, tools/native/mios-unit-gen/src/tmux_os.rs, usr/share/mios/mios.toml [keybindings.tmux.float]
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
const MENU_X: &str = "C";
const MENU_Y: &str = "C";

fn get<'a>(v: &'a Value, path: &str) -> Result<&'a Value, String> {
    path.split('.')
        .try_fold(v, |cur, key| cur.get(key))
        .ok_or_else(|| format!("missing SSOT key {path}"))
}

fn text(v: &Value, path: &str) -> Result<String, String> {
    let s = get(v, path)?
        .as_str()
        .ok_or_else(|| format!("{path} must be a string"))?;
    if !policy::plain_text(s) {
        return Err(format!("{path} must be plain ASCII text"));
    }
    Ok(s.to_owned())
}

fn string(v: &Value, path: &str) -> Result<String, String> {
    get(v, path)?
        .as_str()
        .map(str::to_owned)
        .ok_or_else(|| format!("{path} must be a string"))
}

/// A tmux client name as `#{q:client_name}` delivers it: a tty path, or a `client-PID` control client.
pub fn client_arg(c: &str) -> Result<String, String> {
    let tty = c.strip_prefix("/dev/").is_some_and(|t| {
        !t.is_empty()
            && !t.contains("..")
            && t.bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"/._-".contains(&b))
    });
    let ctl = c
        .strip_prefix("client-")
        .is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()));
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
    let out = Command::new("tmux")
        .args(args)
        .output()
        .map_err(|e| format!("tmux: {e}"))?;
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
    let mut out = vec![
        "-b".to_owned(),
        string(merged, "theme.tmux.console.border_lines")?,
    ];
    if menu {
        out.extend([
            "-s".into(),
            string(merged, "theme.tmux.console.menu_style")?,
        ]);
        out.extend([
            "-S".into(),
            string(merged, "theme.tmux.console.menu_border_style")?,
        ]);
        out.extend([
            "-H".into(),
            string(merged, "theme.tmux.console.menu_selected_style")?,
        ]);
    }
    Ok(out)
}

fn capitalised(s: &str) -> String {
    let mut c = s.chars();
    c.next()
        .map(|f| f.to_ascii_uppercase().to_string() + c.as_str())
        .unwrap_or_default()
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
    menu_page_argv(merged, console, rows, u16::MAX, section, 0, is_exec)
}

fn menu_page_argv(
    merged: &Value,
    console: bool,
    rows: u32,
    columns: u16,
    section: Option<&str>,
    page: usize,
    is_exec: impl Fn(&str) -> bool,
) -> Result<Option<Vec<String>>, String> {
    let popup = mios_resolver::popup::PopupPolicy::from_value(merged)?;
    let (menu_columns, menu_rows) = popup.size(
        mios_resolver::popup::SurfaceKind::Menu,
        columns,
        u16::try_from(rows).unwrap_or(u16::MAX),
    );
    let rows = u32::from(menu_rows);
    if menu_columns < 10 || rows < 6 {
        return Ok(None);
    }
    let l = terminal_tuis::launcher(merged)?;
    let cat = terminal_tuis::catalog(merged)?;
    let tuis = if l.enabled {
        terminal_tuis::visible(&cat, console, &is_exec)
    } else {
        Vec::new()
    };
    let cflag = if console { 1 } else { 0 };
    let mut actions: Vec<(String, String, String)> = Vec::new();
    for a in get(merged, "keybindings.actions")?
        .as_array()
        .ok_or("keybindings.actions must be an array")?
    {
        let label = text(a, "label")?;
        let key = string(a, "key")?;
        actions.push((label, key, string(a, "tmux_command")?));
    }
    let item = |t: &Tui, key: String| -> [String; 3] {
        [
            t.label.clone(),
            key,
            helper(&format!(
                "--app {} --client #{{q:client_name}} --console {cflag}",
                t.id
            )),
        ]
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
        let group: Vec<_> = tuis.iter().filter(|t| t.section == sec).collect();
        let paged = group.len() as u32 + 4 > rows;
        let capacity = if paged {
            rows.saturating_sub(6).max(1) as usize
        } else {
            group.len().max(1)
        };
        let pages = group.len().max(1).div_ceil(capacity);
        let page = page.min(pages - 1);
        if pages > 1 {
            title = format!("{title} {}/{}", page + 1, pages);
        }
        for t in group.iter().skip(page * capacity).take(capacity) {
            items.push(item(t, keys.next().unwrap_or_default()));
        }
        // A six-row console fits one app plus both navigation keys and Back.
        // Its decorative separator must not make middle pages unreachable.
        if rows > 6 {
            items.push(sep());
        }
        let open_page = |page: usize| {
            helper(&format!("--menu apps --section {sec} --page {page} --client #{{q:client_name}} --console {cflag} --rows #{{client_height}}"))
        };
        if page > 0 {
            items.push([
                "< Previous page".into(),
                "PPage".into(),
                open_page(page - 1),
            ]);
        }
        if page + 1 < pages {
            items.push(["Next page >".into(), "NPage".into(), open_page(page + 1)]);
        }
        items.push(["< Back".into(), "BSpace".into(), helper(&format!("--menu apps --client #{{q:client_name}} --console {cflag} --rows #{{client_height}}"))]);
    } else {
        for (label, key, command) in &actions {
            items.push([label.clone(), key.clone(), command.clone()]);
        }
        let groups: Vec<(&String, Vec<&&Tui>)> = l
            .sections
            .iter()
            .map(|s| {
                (
                    s,
                    tuis.iter().filter(|t| &t.section == s).collect::<Vec<_>>(),
                )
            })
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
    let label_columns = usize::from(menu_columns.saturating_sub(6));
    if title.chars().count() > label_columns {
        title = format!(
            "{}...",
            title
                .chars()
                .take(label_columns.saturating_sub(3))
                .collect::<String>()
        );
    }
    if popup.enabled {
        title = format!(
            "{:^width$}",
            title,
            width = usize::from(menu_columns.saturating_sub(4))
        );
    }
    for item in &mut items {
        let width =
            usize::from(menu_columns.saturating_sub(4)).saturating_sub(item[1].chars().count() + 3);
        if item[0].chars().count() > width {
            item[0] = format!(
                "{}...",
                item[0]
                    .chars()
                    .take(width.saturating_sub(3))
                    .collect::<String>()
            );
        }
    }
    // No -M: it would make tmux ignore -C, and Enter alone must choose the first item (controller vocabulary).
    let mut argv: Vec<String> = vec![
        "display-menu".into(),
        "-T".into(),
        title,
        "-C".into(),
        "0".into(),
        "-x".into(),
        MENU_X.into(),
        "-y".into(),
        MENU_Y.into(),
    ];
    if console {
        argv.extend(console_flags(merged, true)?);
    }
    // End of flags: a disabled row ("-<label>") first would otherwise be parsed as a flag.
    argv.push("--".into());
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
pub fn tui_spec(
    merged: &Value,
    id: &str,
    console: bool,
    is_exec: impl Fn(&str) -> bool,
) -> Result<Option<Vec<String>>, String> {
    let l = terminal_tuis::launcher(merged)?;
    if !l.enabled {
        return Ok(None);
    }
    let cat = terminal_tuis::catalog(merged)?;
    let Some(t) = terminal_tuis::visible(&cat, console, is_exec)
        .into_iter()
        .find(|t| t.id == id)
    else {
        return Ok(None);
    };
    let mut out = vec![
        t.mode.as_str().to_owned(),
        t.label.clone(),
        l.popup_width.clone(),
        l.popup_height.clone(),
        if t.hold { "1".into() } else { "0".into() },
    ];
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
    page: usize,
    session: Option<String>,
    socket: Option<String>,
    id: Option<String>,
    /// `app --mode popup|window|float`: open the TUI this way instead of its [[terminal.tuis]] mode (the launcher's
    /// options).
    mode: Option<Mode>,
}

fn parse(argv: &[String]) -> Result<Args, String> {
    let mut a = Args {
        verb: String::new(),
        sub: None,
        client: None,
        console: false,
        rows: 24,
        section: None,
        page: 0,
        session: None,
        socket: None,
        id: None,
        mode: None,
    };
    let mut it = argv.iter();
    a.verb = it.next().cloned().ok_or("tmux-os: missing verb")?;
    while let Some(arg) = it.next() {
        let mut val = || {
            it.next()
                .cloned()
                .ok_or_else(|| format!("{arg} requires a value"))
        };
        match arg.as_str() {
            "--client" => a.client = Some(client_arg(&val()?)?),
            "--console" => {
                a.console = match val()?.as_str() {
                    "1" => true,
                    "0" => false,
                    other => return Err(format!("--console must be 0 or 1, not {other:?}")),
                }
            }
            "--rows" => {
                a.rows = val()?
                    .parse()
                    .ok()
                    .filter(|r| (1..=1000).contains(r))
                    .ok_or("--rows must be 1..1000")?
            }
            "--section" => a.section = Some(val()?),
            "--page" => {
                a.page = val()?
                    .parse()
                    .map_err(|_| "--page must be a nonnegative integer")?
            }
            "--session" => a.session = Some(val()?),
            "--socket" => a.socket = Some(val()?),
            "--mode" => {
                a.mode = Some(match val()?.as_str() {
                    "popup" => Mode::Popup,
                    "window" => Mode::Window,
                    "float" => Mode::Float,
                    other => {
                        return Err(format!(
                            "--mode must be popup, window or float, not {other:?}"
                        ))
                    }
                })
            }
            s if !s.starts_with('-') && a.sub.is_none() => a.sub = Some(s.to_owned()),
            s if !s.starts_with('-') && a.id.is_none() => a.id = Some(s.to_owned()),
            other => return Err(format!("tmux-os: unexpected argument {other:?}")),
        }
    }
    Ok(a)
}

fn need_client(a: &Args) -> Result<String, String> {
    a.client
        .clone()
        .ok_or_else(|| "--client is required".into())
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
        tmux(&[
            "new-session",
            "-d",
            "-s",
            name,
            "-c",
            cwd,
            "-e",
            "MIOS_SESSION_BARE=1",
        ])?;
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
    let rows = tmux(&[
        "list-sessions",
        "-F",
        "#{session_id}\t#{session_name}\t#{@mios-float-parent}",
    ])
    .unwrap_or_default();
    let ids: Vec<&str> = rows.lines().filter_map(|l| l.split('\t').next()).collect();
    for line in rows.lines() {
        let f: Vec<&str> = line.split('\t').collect();
        if f.len() == 3 && f[1].starts_with(&prefix) && !f[2].is_empty() && !ids.contains(&f[2]) {
            let _ = tmux(&["kill-session", "-t", &format!("={}", f[1])]);
        }
    }
    Ok(())
}

/// Every runtime popup resolves the same per-type SSOT cap on its actual client.
fn popup_dimensions(
    merged: &Value,
    client: &str,
    pane: &str,
    kind: mios_resolver::popup::SurfaceKind,
) -> Result<Option<(u16, u16)>, String> {
    let policy = mios_resolver::popup::PopupPolicy::from_value(merged)?;
    if !policy.enabled {
        return Ok(None);
    }
    let dimensions = info(client, pane, "#{client_width}\t#{client_height}")?;
    let width = dimensions
        .first()
        .and_then(|v| v.parse().ok())
        .ok_or("invalid popup client width")?;
    let height = dimensions
        .get(1)
        .and_then(|v| v.parse().ok())
        .ok_or("invalid popup client height")?;
    Ok(Some(policy.size(kind, width, height)))
}

/// Native floating-pane positions are numeric window cells; tmux does not
/// accept display-popup's C token for new-pane -X/-Y.
fn native_float_geometry(
    merged: &Value,
    client: &str,
    pane: &str,
) -> Result<Option<(u16, u16, u16, u16)>, String> {
    let p = mios_resolver::popup::PopupPolicy::from_value(merged)?;
    if !p.enabled {
        return Ok(None);
    }
    let size = info(client, pane, "#{window_width}\t#{window_height}")?;
    let w: u16 = size
        .first()
        .and_then(|s| s.parse().ok())
        .ok_or("invalid floating window width")?;
    let h: u16 = size
        .get(1)
        .and_then(|s| s.parse().ok())
        .ok_or("invalid floating window height")?;
    let (pw, ph) = p.size(mios_resolver::popup::SurfaceKind::Window, w, h);
    Ok(Some((
        pw,
        ph,
        w.saturating_sub(pw) / 2,
        h.saturating_sub(ph) / 2,
    )))
}

fn open_popup(
    merged: &Value,
    client: &str,
    pane: &str,
    name: &str,
    cwd: &str,
    console: bool,
) -> Result<String, String> {
    let f = float_cfg(merged)?;
    let dimensions = popup_dimensions(
        merged,
        client,
        pane,
        mios_resolver::popup::SurfaceKind::Window,
    )?;
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
        dimensions.map_or_else(|| string(f, "shell_width"), |(w, _)| Ok(w.to_string()))?,
        "-h".into(),
        dimensions.map_or_else(|| string(f, "shell_height"), |(_, h)| Ok(h.to_string()))?,
        "-x".into(),
        "C".into(),
        "-y".into(),
        "C".into(),
        "-T".into(),
        string(f, "shell_title")?,
        "-d".into(),
        cwd.into(),
    ];
    if console {
        args.extend(console_flags(merged, false)?);
    }
    args.push("--".into());
    args.extend([
        me.display().to_string(),
        "tmux-os".into(),
        "float".into(),
        "attach".into(),
        "--session".into(),
        name.into(),
        "--socket".into(),
        sock,
        "--console".into(),
        if console { "1".into() } else { "0".into() },
    ]);
    Ok(exec_tmux(&args))
}

fn info(client: &str, pane: &str, fmt: &str) -> Result<Vec<String>, String> {
    Ok(
        tmux(&["display-message", "-p", "-c", client, "-t", pane, fmt])?
            .split('\t')
            .map(str::to_owned)
            .collect(),
    )
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
        set.args([
            "-S",
            &sock,
            "set-option",
            "-t",
            &target,
            "@mios-float-client",
        ])
        .arg(&tty)
        .env_remove("TMUX");
        set.output().map_err(|e| e.to_string())?;
        let mut cmd = Command::new("tmux");
        cmd.args(["-S", &sock, "attach-session", "-t", &target])
            .env_remove("TMUX")
            .env_remove("TMUX_PANE");
        if a.console {
            let term = policy::console_terms(merged)?
                .into_iter()
                .find(|t| !t.contains(['*', '?']))
                .unwrap_or_else(|| "linux".into());
            cmd.env("TERM", term);
        }
        return Err(format!("exec tmux attach: {}", cmd.exec()));
    }
    let client = need_client(a)?;
    let pane = pane_env()?;
    let cwd = std::env::current_dir()
        .map(|d| d.display().to_string())
        .unwrap_or_else(|_| "/".into());
    let f = info(&client, &pane, "#{session_name}\t#{session_id}\t#{client_tty}\t#{pane_floating_flag}\t#{window_id}\t#{window_panes}\t#{@mios-float-origin}\t#{E:@mios-managed-pane}")?;
    if f.len() != 8 {
        return Err("unexpected tmux format reply".into());
    }
    let (session, sid, tty, floating, window, panes, origin, managed) = (
        &f[0],
        &f[1],
        &f[2],
        f[3] == "1",
        &f[4],
        &f[5],
        &f[6],
        f[7] == "1",
    );
    let in_float = session.starts_with(&prefix);
    let recorded = || {
        tmux(&[
            "show-option",
            "-qv",
            "-t",
            &format!("={session}:"),
            "@mios-float-client",
        ])
        .unwrap_or_default()
    };
    match sub {
        "new" => {
            if managed {
                message(
                    &client,
                    &text(merged, "keybindings.tmux.managed_refusal_message")?,
                )?;
                return Ok(String::new());
            }
            let f = get(merged, "keybindings.tmux.float")?;
            let pct = |k: &str| -> Result<u32, String> {
                string(f, k)?
                    .trim_end_matches('%')
                    .parse::<u32>()
                    .ok()
                    .filter(|n| (1..=100).contains(n))
                    .ok_or_else(|| format!("[keybindings.tmux.float].{k} must be a percentage"))
            };
            let (w, h, x, y) =
                if let Some((w, h, x, y)) = native_float_geometry(merged, &client, &pane)? {
                    (w.to_string(), h.to_string(), x.to_string(), y.to_string())
                } else {
                    let (w, h) = (pct("pane_width")?, pct("pane_height")?);
                    (
                        format!("{w}%"),
                        format!("{h}%"),
                        format!("{}%", (100 - w) / 2),
                        format!("{}%", (100 - h) / 2),
                    )
                };
            Err(exec_tmux(&[
                "new-pane".into(),
                "-t".into(),
                pane,
                "-x".into(),
                w,
                "-y".into(),
                h,
                "-X".into(),
                x,
                "-Y".into(),
                y,
                "-c".into(),
                cwd,
                "--".into(),
            ]))
        }
        "shell" => {
            if in_float {
                if recorded() == *tty || tmux(&["switch-client", "-c", &client, "-l"]).is_err() {
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
                message(
                    &client,
                    &text(merged, "keybindings.tmux.managed_refusal_message")?,
                )?;
                return Ok(String::new());
            }
            if in_float && !origin.is_empty() {
                // Dock a lifted pane back into the window it came from, then close this popup.
                if tmux(&["display-message", "-p", "-t", origin, "#{window_id}"]).is_err() {
                    message(
                        &client,
                        &text(merged, "keybindings.tmux.float.origin_gone_message")?,
                    )?;
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
                let rows = tmux(&[
                    "list-panes",
                    "-t",
                    window,
                    "-F",
                    "#{pane_id}\t#{pane_floating_flag}\t#{e|*:#{pane_width},#{pane_height}}",
                ])?;
                let target = rows
                    .lines()
                    .filter_map(|l| {
                        let c: Vec<&str> = l.split('\t').collect();
                        (c.len() == 3 && c[1] != "1")
                            .then(|| (c[2].parse::<u64>().unwrap_or(0), c[0].to_owned()))
                    })
                    .max();
                match target {
                    Some((_, t)) => tmux(&["join-pane", "-h", "-s", &pane, "-t", &t])?,
                    None => {
                        return message(
                            &client,
                            &text(merged, "keybindings.tmux.float.single_pane_message")?,
                        )
                        .map(|_| String::new())
                    }
                };
                return Ok(String::new());
            }
            if panes.parse::<u32>().unwrap_or(0) < 2 || in_float {
                message(
                    &client,
                    &text(merged, "keybindings.tmux.float.single_pane_message")?,
                )?;
                return Ok(String::new());
            }
            let name = float_name(merged, sid)?;
            ensure_float(merged, &name, sid, &cwd)?;
            tmux(&["break-pane", "-d", "-s", &pane, "-t", &format!("={name}:")])?;
            tmux(&[
                "set-option",
                "-p",
                "-t",
                &pane,
                "@mios-float-origin",
                window,
            ])?;
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
    let all: Vec<String> = tmux(&["list-sessions", "-F", "#{session_name}"])?
        .lines()
        .map(str::to_owned)
        .collect();
    let Some(at) = all.iter().position(|s| *s == current) else {
        return Ok(());
    };
    let n = all.len();
    let order: Vec<usize> = if dir == "next" {
        (1..n).map(|i| (at + i) % n).collect()
    } else {
        (1..n).map(|i| (at + n - i) % n).collect()
    };
    if let Some(i) = order
        .into_iter()
        .find(|&i| !policy::is_hidden_session(&hidden, &all[i]))
    {
        tmux(&[
            "switch-client",
            "-c",
            &client,
            "-t",
            &format!("={}", all[i]),
        ])?;
    }
    Ok(())
}

fn new_session(merged: &Value, a: &Args) -> Result<(), String> {
    let client = need_client(a)?;
    let base = string(merged, "terminal.sessions.new_session_name")?;
    if base.is_empty()
        || !base
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
    {
        return Err("[terminal.sessions].new_session_name must be [A-Za-z0-9._-]".into());
    }
    let cwd = std::env::current_dir()
        .map(|d| d.display().to_string())
        .unwrap_or_else(|_| "/".into());
    let mut name = base.clone();
    let mut n = 2;
    while tmux(&["has-session", "-t", &format!("={name}")]).is_ok() {
        name = format!("{base}-{n}");
        n += 1;
    }
    tmux(&[
        "new-session",
        "-d",
        "-s",
        &name,
        "-c",
        &cwd,
        "-e",
        "MIOS_SESSION_BARE=1",
    ])?;
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
    let visible = if l.enabled {
        terminal_tuis::visible(&cat, a.console, terminal_tuis::is_installed)
    } else {
        Vec::new()
    };
    let Some(t) = visible.into_iter().find(|t| t.id == id) else {
        message(&client, &l.empty_message)?;
        return Ok(String::new());
    };
    let cwd = std::env::current_dir()
        .map(|d| d.display().to_string())
        .unwrap_or_else(|_| "/".into());
    let argv = t.argv_for(a.console).to_vec();
    let dimensions = popup_dimensions(
        merged,
        &client,
        &pane,
        mios_resolver::popup::SurfaceKind::Window,
    )?;
    let desktop = tmux(&[
        "display-message",
        "-p",
        "-t",
        &pane,
        "#{@mios-desktop-enabled}",
    ])? == "1";
    let mut mode = a.mode.unwrap_or_else(|| {
        if (dimensions.is_some() || desktop) && t.mode == Mode::Window {
            Mode::Float
        } else {
            t.mode
        }
    });
    if mode == Mode::Popup && !scope_works(&l.popup_scope) {
        // Keep the desktop visible when the app's scoped popup cannot start.
        mode = if desktop { Mode::Float } else { Mode::Window };
    }
    if mode == Mode::Float && tmux(&["list-commands", "new-pane"]).is_err() {
        mode = Mode::Window;
    }
    let session = tmux(&["display-message", "-p", "-t", &pane, "#{session_id}"])?;
    let mut args: Vec<String> = match mode {
        Mode::Popup => {
            let mut v: Vec<String> = vec![
                "display-popup".into(),
                "-c".into(),
                client.clone(),
                "-t".into(),
                pane.clone(),
            ];
            if !t.hold {
                v.push("-E".into());
            }
            v.extend([
                "-w".into(),
                dimensions.map_or_else(|| l.popup_width.clone(), |(w, _)| w.to_string()),
                "-h".into(),
                dimensions.map_or_else(|| l.popup_height.clone(), |(_, h)| h.to_string()),
                "-x".into(),
                "C".into(),
                "-y".into(),
                "C".into(),
                "-T".into(),
                format!(" {} ", t.label),
                "-d".into(),
                cwd,
            ]);
            if a.console {
                v.extend(console_flags(merged, false)?);
            }
            v.push("--".into());
            v.extend(l.popup_scope.iter().cloned());
            v
        }
        Mode::Window => vec![
            "new-window".into(),
            "-t".into(),
            format!("{session}:"),
            "-n".into(),
            t.label.clone(),
            "-c".into(),
            cwd,
            "--".into(),
        ],
        Mode::Float => {
            let f = get(merged, "keybindings.tmux.float")?;
            let pct = |k: &str| -> Result<u32, String> {
                string(f, k)?
                    .trim_end_matches('%')
                    .parse::<u32>()
                    .ok()
                    .filter(|n| (1..=100).contains(n))
                    .ok_or_else(|| format!("[keybindings.tmux.float].{k} must be a percentage"))
            };
            let (w, h, x, y) =
                if let Some((w, h, x, y)) = native_float_geometry(merged, &client, &pane)? {
                    (w.to_string(), h.to_string(), x.to_string(), y.to_string())
                } else {
                    let (w, h) = (pct("pane_width")?, pct("pane_height")?);
                    (
                        format!("{w}%"),
                        format!("{h}%"),
                        format!("{}%", (100 - w) / 2),
                        format!("{}%", (100 - h) / 2),
                    )
                };
            vec![
                "new-pane".into(),
                "-t".into(),
                pane.clone(),
                "-x".into(),
                w,
                "-y".into(),
                h,
                "-X".into(),
                x,
                "-Y".into(),
                y,
                "-c".into(),
                cwd,
                "--".into(),
            ]
        }
    };
    args.extend(argv);
    Err(exec_tmux(&args))
}

/// `mios-gen tmux-os VERB ...`: runs inside a tmux job (TMUX and an explicit TMUX_PANE set by the binding).
/// Ok(()) after a command that returns; exec-ing verbs never return on success.
pub fn run(root: &Path, argv: &[String]) -> Result<u8, String> {
    if argv.first().is_some_and(|arg| arg == "desktop") {
        return crate::tmux_desktop::run(root, &argv[1..]);
    }
    let a = parse(argv)?;
    let merged = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    match a.verb.as_str() {
        "menu" => {
            if a.sub.as_deref() != Some("apps") {
                return Err("tmux-os menu: only the apps menu is dynamic".into());
            }
            let client = need_client(&a)?;
            let pane = pane_env()?;
            let columns = tmux(&["display-message", "-p", "-c", &client, "#{client_width}"])?
                .parse::<u16>()
                .map_err(|_| "tmux client width is not a cell count")?;
            match menu_page_argv(
                &merged,
                a.console,
                a.rows,
                columns,
                a.section.as_deref(),
                a.page,
                terminal_tuis::is_installed,
            )? {
                None => {
                    let l = terminal_tuis::launcher(&merged)?;
                    let msg = if l.enabled {
                        text(&merged, "keybindings.tmux.menu_too_small_message")?
                    } else {
                        l.empty_message
                    };
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
        include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap()
    }

    #[test]
    fn client_names_are_validated() {
        assert!(client_arg("/dev/pts/3").is_ok() && client_arg("client-123").is_ok());
        for bad in [
            "",
            "/dev/../etc",
            "/tmp/x",
            "client-",
            "x;rm",
            "/dev/pts/3 -t",
        ] {
            assert!(client_arg(bad).is_err(), "{bad}");
        }
    }

    #[test]
    fn apps_menu_lists_actions_first_then_installed_rows_only() {
        let d = ssot();
        let installed = |a: &str| matches!(a, "btop" | "top" | "/usr/bin/mios-fanctl");
        let m = menu_argv(&d, false, 60, None, installed).unwrap().unwrap();
        assert_eq!(m[0], "display-menu");
        assert!(
            m.windows(2).any(|w| w[0] == "-C" && w[1] == "0") && !m.iter().any(|a| a == "-M"),
            "{m:?}"
        );
        let end = m
            .iter()
            .position(|a| a == "--")
            .expect("the flags end before the items");
        assert!(
            end < m.iter().position(|a| a == "MiOS AI").unwrap(),
            "{m:?}"
        );
        let ai = m.iter().position(|a| a == "MiOS AI").unwrap();
        let btop = m.iter().position(|a| a == "btop").unwrap();
        assert!(ai < btop);
        assert!(
            !m.iter().any(|a| a == "htop" || a == "Midnight Commander"),
            "uninstalled rows are hidden"
        );
        // Only validated ids reach the item commands, each naming its pane and client.
        let cmd = &m[btop + 2];
        assert!(
            cmd.contains("TMUX_PANE=#{pane_id}")
                && cmd.contains("--app btop --client #{q:client_name} --console 0"),
            "{cmd}"
        );
    }

    #[test]
    fn console_menu_uses_argv_vt_rows_and_console_flags() {
        let d = ssot();
        let installed = |a: &str| matches!(a, "btop" | "yazi");
        let m = menu_argv(&d, true, 60, None, installed).unwrap().unwrap();
        assert!(m.windows(2).any(|w| w[0] == "-b" && w[1] == "single"));
        assert!(
            m.iter().any(|a| a == "btop") && !m.iter().any(|a| a == "yazi files"),
            "vt_ok = false rows are hidden"
        );
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
        let mut legacy = d.clone();
        legacy["terminal"]["popups"]["enabled"] = Value::Boolean(false);
        let flat = menu_argv(&legacy, false, 60, None, all).unwrap().unwrap();
        assert!(flat.iter().any(|a| a == "btop"));
        let compact = menu_argv(&d, false, 240, None, all).unwrap().unwrap();
        assert!(compact.iter().any(|a| a.starts_with("System (")));
        assert!(
            !compact.iter().any(|a| a == "btop"),
            "a tall viewport still uses compact sections"
        );
        let paged = menu_argv(&d, false, 16, None, all).unwrap().unwrap();
        assert!(
            !paged.iter().any(|a| a == "btop") && paged.iter().any(|a| a.starts_with("System (")),
            "{paged:?}"
        );
        assert!(paged
            .iter()
            .any(|a| a.contains("--menu apps --section system")));
        let page = menu_argv(&d, false, 16, Some("system"), all)
            .unwrap()
            .unwrap();
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
        let a = parse(&[
            "menu".into(),
            "apps".into(),
            "--client".into(),
            "/dev/pts/1".into(),
            "--console".into(),
            "1".into(),
            "--rows".into(),
            "20".into(),
        ])
        .unwrap();
        assert!(a.console && a.rows == 20 && a.sub.as_deref() == Some("apps"));
        assert!(parse(&["menu".into(), "--console".into(), "2".into()]).is_err());
        assert!(parse(&["menu".into(), "--client".into(), "/etc/passwd".into()]).is_err());
        assert!(parse(&["menu".into(), "--rows".into(), "0".into()]).is_err());
        let a = parse(&["app".into(), "btop".into(), "--mode".into(), "float".into()]).unwrap();
        assert!(a.mode == Some(Mode::Float) && a.sub.as_deref() == Some("btop"));
        assert!(
            parse(&[
                "app".into(),
                "btop".into(),
                "--mode".into(),
                "desktop".into()
            ])
            .is_err(),
            "desktop is the launcher's own pop-out"
        );
    }
    fn menu_rows(argv: &[String]) -> Vec<[String; 3]> {
        let mut result = Vec::new();
        let mut index = argv.iter().position(|arg| arg == "--").unwrap() + 1;
        while index < argv.len() {
            if argv[index].is_empty() {
                result.push([String::new(), String::new(), String::new()]);
                index += 1;
            } else {
                result.push([
                    argv[index].clone(),
                    argv[index + 1].clone(),
                    argv[index + 2].clone(),
                ]);
                index += 3;
            }
        }
        result
    }

    #[test]
    fn compact_apps_pages_reach_every_item_without_exceeding_ssot_bounds() {
        let mut data = ssot();
        let template = data["terminal"]["tuis"].as_array().unwrap()[0].clone();
        data["terminal"]["tuis"] = Value::Array(
            (0..37)
                .map(|index| {
                    let mut item = template.clone();
                    item["id"] = Value::String(format!("proof{index}"));
                    item["label"] = Value::String(format!("Proof application {index}"));
                    item["section"] = Value::String("system".into());
                    item
                })
                .collect(),
        );
        let mut found = std::collections::BTreeSet::new();
        let mut page = 0;
        loop {
            let menu = menu_page_argv(&data, false, 67, 240, Some("system"), page, |_| true)
                .unwrap()
                .unwrap();
            let rows = menu_rows(&menu);
            assert!(rows.len() + 2 <= 18, "{rows:?}");
            assert!(rows.iter().all(|row| row[0].chars().count() <= 54));
            assert!(rows.iter().any(|row| row[0] == "< Back"));
            for row in &rows {
                if row[0].starts_with("Proof application") {
                    assert!(found.insert(row[0].clone()), "duplicate {row:?}");
                }
            }
            if !rows.iter().any(|row| row[0] == "Next page >") {
                break;
            }
            assert!(rows
                .iter()
                .any(|row| row[2].contains(&format!("--page {}", page + 1))));
            page += 1;
            assert!(page < 10);
        }
        assert_eq!(found.len(), 37);
        assert!(page > 0);
        let last = menu_page_argv(&data, false, 67, 240, Some("system"), usize::MAX, |_| true)
            .unwrap()
            .unwrap();
        assert!(!menu_rows(&last).iter().any(|row| row[0] == "Next page >"));
        assert!(parse(&["menu".into(), "--page".into(), "-1".into()]).is_err());
    }

    #[test]
    fn six_row_apps_menu_keeps_middle_pages_and_back_reachable() {
        let mut data = ssot();
        let template = data["terminal"]["tuis"].as_array().unwrap()[0].clone();
        data["terminal"]["tuis"] = Value::Array(
            (0..5)
                .map(|index| {
                    let mut app = template.clone();
                    app["id"] = Value::String(format!("proof{index}"));
                    app["label"] = Value::String(format!("Proof {index}"));
                    app["section"] = Value::String("system".into());
                    app
                })
                .collect(),
        );
        for page in 0..5 {
            let menu = menu_page_argv(&data, true, 6, 20, Some("system"), page, |_| true)
                .unwrap()
                .expect("every tiny page must fit");
            let rows = menu_rows(&menu);
            assert!(rows.len() + 2 <= 6);
            assert!(rows.iter().any(|row| row[0] == format!("Proof {page}")));
            assert!(rows.iter().any(|row| row[0] == "< Back"));
            assert_eq!(rows.iter().any(|row| row[1] == "PPage"), page > 0);
            assert_eq!(rows.iter().any(|row| row[1] == "NPage"), page < 4);
        }
    }

    #[test]
    fn compact_apps_labels_and_title_fit_narrow_clients() {
        let mut data = ssot();
        data["terminal"]["tuis"].as_array_mut().unwrap()[0]["label"] =
            Value::String("An installed application with a longer menu label".into());
        let menu = menu_page_argv(&data, false, 40, 34, Some("mios"), 0, |_| true)
            .unwrap()
            .unwrap();
        let policy = mios_resolver::popup::PopupPolicy::from_value(&data).unwrap();
        let (width, height) = policy.size(mios_resolver::popup::SurfaceKind::Menu, 34, 40);
        assert!(menu_rows(&menu).len() + 2 <= usize::from(height));
        assert!(menu_rows(&menu)
            .iter()
            .all(|row| row[0].chars().count() + 6 <= usize::from(width)));
        let title = menu.iter().position(|arg| arg == "-T").unwrap() + 1;
        assert!(menu[title].chars().count() <= usize::from(width));
    }
}
