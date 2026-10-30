// AI-hint: Native tmux-os workspace layout hook (`mios-agent-relay --workspace-hook ensure|resize [HEAD SOCKET]`): the client-attached/session-changed/resized/active and window-resized hooks run this instead of a Python adapter. Single-flight per window (non-blocking lock; concurrent events coalesce into one rerun) and idempotent (a window already laid out for its current size is skipped), so the workspace's own pane reshaping can never re-trigger itself into a process flood.
// AI-related: tools/native/mios-agent-relay/src/main.rs (workspace), tools/native/mios-service-core/src/tmux_theme.rs (hook projection), usr/share/mios/mios.toml [mcp.tmux.workspace], usr/libexec/mios/mios-mcp-server (legacy shim)
// AI-functions: run, context, Flight::acquire, applied_key
use super::{workspace, workspace_tmux};
use serde_json::{json, Value};
use std::fs::{self, File, OpenOptions};
use std::io::Write;
use std::os::unix::fs::{DirBuilderExt, OpenOptionsExt};
use std::path::{Path, PathBuf};

/// Command the workspace observer pane runs; resize uses it to rebuild a dead observer.
pub const OBSERVER_COMMAND: &str = "/usr/bin/mios agents --watch";
/// tmux window option recording the size the last successful pass laid the window out for.
pub const APPLIED_OPTION: &str = "@mios-workspace-applied";
/// A pass plus at most this many coalesced reruns per invocation.
const MAX_PASSES: usize = 3;

pub fn applied_key(width: &str, height: &str) -> String {
    format!("{width}x{height}")
}

fn pane_id(s: &str) -> bool {
    s.len() > 1 && s.starts_with('%') && s[1..].bytes().all(|b| b.is_ascii_digit())
}

fn window_id(s: &str) -> bool {
    s.len() > 1 && s.starts_with('@') && s[1..].bytes().all(|b| b.is_ascii_digit())
}

/// Per-window single flight. Holding `lock` means this process lays the window out; anyone arriving meanwhile only
/// touches `dirty` and leaves, and the holder reruns once for them.
pub struct Flight {
    _lock: File,
    dirty: PathBuf,
}

impl Flight {
    /// None when another pass holds the window (the caller has been recorded as a pending rerun).
    pub fn acquire(dir: &Path, key: &str) -> Result<Option<Flight>, String> {
        fs::DirBuilder::new().recursive(true).mode(0o700).create(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
        let lock_path = dir.join(format!("{key}.lock"));
        let dirty = dir.join(format!("{key}.dirty"));
        let lock = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .mode(0o600)
            .open(&lock_path)
            .map_err(|e| format!("{}: {e}", lock_path.display()))?;
        match lock.try_lock() {
            Ok(()) => Ok(Some(Flight { _lock: lock, dirty })),
            Err(fs::TryLockError::WouldBlock) => {
                OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(&dirty).map_err(|e| e.to_string())?;
                Ok(None)
            }
            Err(fs::TryLockError::Error(e)) => Err(e.to_string()),
        }
    }

    /// Consume the pending-rerun mark; true when some event arrived during the last pass.
    pub fn take_dirty(&self) -> bool {
        fs::remove_file(&self.dirty).is_ok()
    }
}

pub struct Ui {
    pub socket: String,
    pub pane: String,
    pub window: String,
    pub session: String,
}

/// The witnessed human pane the hook fired for (port of mios-mcp-server `_human_tmux_context`): a pane on the native
/// human socket, or the pane tmux named in the hook's own TMUX/TMUX_PANE; compact views re-enter their main window.
pub fn context(socket: &str, pane: &str, human_socket: &str, terminal_session: &str) -> Result<Ui, String> {
    let path = Path::new(socket);
    let ambient = std::env::var("TMUX").unwrap_or_default();
    let parts: Vec<&str> = ambient.rsplitn(3, ',').collect();
    let witnessed = parts.len() == 3
        && parts[2] == socket
        && parts[1].bytes().all(|b| b.is_ascii_digit())
        && std::env::var("TMUX_PANE").ok().as_deref() == Some(pane);
    let native = path.file_name().and_then(|n| n.to_str()) == Some(human_socket);
    if !path.is_absolute() || socket.len() >= 104 || !(native || witnessed) || !pane_id(pane) {
        return Err("invalid native MiOS desktop tmux binding".into());
    }
    let tmux = |args: &[&str]| workspace_tmux(socket, args);
    let row = tmux(&["display-message", "-p", "-t", pane, "#{socket_path}|#{session_name}|#{pane_id}|#{window_id}|#{pid}"])?;
    let mut fields: Vec<String> = row.split('|').map(str::to_owned).collect();
    if fields.len() == 5 && fields[1].starts_with("mios-workspace-") {
        let tags: Vec<String> = tmux(&["display-message", "-p", "-t", pane, "#{@mios-workspace-head}|#{@mios-workspace-worker}|#{@mios-workspace-window}"])?
            .split('|')
            .map(str::to_owned)
            .collect();
        let head = if !tags.is_empty() && !tags[0].is_empty() { tags[0].clone() } else { tags.get(1).cloned().unwrap_or_default() };
        let mut main = tags.get(2).filter(|w| window_id(w)).cloned().unwrap_or_default();
        if main.is_empty() && pane_id(&head) {
            main = tmux(&["display-message", "-p", "-t", &head, "#{@mios-workspace-window}"])?;
        }
        if window_id(&main) {
            let witness: Vec<String> = tmux(&["display-message", "-p", "-t", &main, "#{session_name}|#{@mios-workspace-head}"])?
                .split('|')
                .map(str::to_owned)
                .collect();
            if witness.len() == 2 && (head.is_empty() || witness[1] == head) {
                fields[1] = witness[0].clone();
                fields[3] = main;
            }
        }
    }
    if fields.len() != 5
        || fields[0] != socket
        || fields[2] != pane
        || !window_id(&fields[3])
        || !fields[4].bytes().all(|b| b.is_ascii_digit())
        || (witnessed && fields[4] != parts[1])
        || fields[1].starts_with("mios-workspace-")
        || (!native && !witnessed && fields[1] != terminal_session)
    {
        return Err("desktop pane is not in the native MiOS session".into());
    }
    Ok(Ui { socket: socket.into(), pane: pane.into(), window: fields[3].clone(), session: fields[1].clone() })
}

fn log(msg: &str) {
    // run-shell shows a hook's output in the pane: never print, keep a small private log instead.
    let Some(dir) = std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from) else { return };
    let path = dir.join("mios-workspace-hook").join("hook.log");
    if fs::metadata(&path).map(|m| m.len() > 256 * 1024).unwrap_or(false) {
        let _ = fs::rename(&path, path.with_extension("log.1"));
    }
    if let Ok(mut f) = OpenOptions::new().create(true).append(true).mode(0o600).open(&path) {
        let _ = writeln!(f, "{msg}");
    }
}

fn get<'a>(v: &'a toml::Value, path: &str) -> Option<&'a toml::Value> {
    path.split('.').try_fold(v, |v, k| v.get(k))
}

/// `--workspace-hook EVENT [HEAD SOCKET]`. Returns a short outcome for tests; the CLI prints nothing.
pub fn run(event: &str, head: Option<&str>, socket: Option<&str>) -> Result<&'static str, String> {
    if !matches!(event, "ensure" | "resize") {
        return Err("workspace hook event must be ensure or resize".into());
    }
    if std::env::var("MIOS_TMUX_HELPER").ok().as_deref() == Some("1") {
        return Ok("helper");
    }
    let ambient = std::env::var("TMUX").unwrap_or_default();
    let socket = socket.map(str::to_owned).unwrap_or_else(|| ambient.rsplitn(3, ',').last().unwrap_or("").to_owned());
    let pane = head.map(str::to_owned).or_else(|| std::env::var("TMUX_PANE").ok()).unwrap_or_default();
    if socket.is_empty() || !pane_id(&pane) {
        return Ok("unbound");
    }
    let tmux = |args: &[&str]| workspace_tmux(&socket, args);
    // Cheapest checks first: one tmux query decides most events (our own reshaping) before any SSOT parse.
    let probe = tmux(&["display-message", "-p", "-t", &pane, &format!("#{{window_id}}|#{{window_width}}|#{{window_height}}|#{{{APPLIED_OPTION}}}")])?;
    let f: Vec<&str> = probe.split('|').collect();
    if f.len() != 4 || !window_id(f[0]) {
        return Err(format!("unexpected window probe {probe:?}"));
    }
    let window = f[0].to_owned();
    // Idempotent: the window is already laid out for this size (the usual case for events caused by our own reshaping).
    if f[3] == applied_key(f[1], f[2]) {
        return Ok("unchanged");
    }
    let dir = std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from).unwrap_or_else(std::env::temp_dir).join("mios-workspace-hook");
    let key = &super::digest(&format!("{socket}|{window}"))[..24];
    let Some(flight) = Flight::acquire(&dir, key)? else {
        return Ok("coalesced");
    };
    // Bare Tmux-OS sessions opt out (their session environment carries MIOS_SESSION_BARE=1).
    if tmux(&["show-environment", "-t", &pane, "MIOS_SESSION_BARE"]).ok().as_deref() == Some("MIOS_SESSION_BARE=1") {
        return Ok("bare");
    }
    let merged = mios_resolver::resolve_merged(None, false).map_err(|e| e.to_string())?;
    let ws = get(&merged, "mcp.tmux.workspace").ok_or("missing [mcp.tmux.workspace]")?;
    if event == "ensure" && get(ws, "global_sessions").and_then(toml::Value::as_bool) != Some(true) {
        return Ok("disabled");
    }
    let str_at = |p: &str| get(&merged, p).and_then(toml::Value::as_str).unwrap_or("").to_owned();
    let human_socket = str_at("keybindings.socket_name");
    let terminal_session = str_at("keybindings.terminal_session");
    let ws_json = serde_json::to_value(ws).map_err(|e| e.to_string())?;
    let start = get(&merged, "terminal.start_directory").and_then(toml::Value::as_str).filter(|d| Path::new(d).is_dir()).unwrap_or("/").to_owned();
    let mut outcome = "applied";
    for pass in 0..MAX_PASSES {
        let _ = flight.take_dirty();
        let ui = context(&socket, &pane, &human_socket, &terminal_session)?;
        let request = json!({
            "action": event,
            "native_hook": true,
            "workspace": ws_json,
            "adapter": "/usr/libexec/mios/mios-mcp-server",
            "adapter_python": str_at("mcp.python"),
            "human_socket": human_socket,
            "session": ui.session,
            "socket": ui.socket,
            "head": ui.pane,
            "observer_command": OBSERVER_COMMAND,
            "directory": start,
        });
        let result: Value = workspace(&request)?;
        let _ = result;
        // Record the size this pass produced, so the window-resized events our own reshaping caused are no-ops.
        let size = tmux(&["display-message", "-p", "-t", &ui.window, "#{window_width}|#{window_height}"])?;
        if let Some((w, h)) = size.split_once('|') {
            tmux(&["set-option", "-w", "-t", &ui.window, APPLIED_OPTION, &applied_key(w, h)])?;
        }
        if !flight.take_dirty() {
            break;
        }
        outcome = if pass + 1 == MAX_PASSES { "applied-capped" } else { "applied-rerun" };
    }
    Ok(outcome)
}

/// CLI entry: never prints (tmux would show it in the pane); failures go to the private hook log.
pub fn main(args: &[String]) -> ! {
    let r = run(&args[2], args.get(3).map(String::as_str), args.get(4).map(String::as_str));
    match r {
        Ok(_) => std::process::exit(0),
        Err(e) => {
            log(&format!("workspace-hook {}: {e}", args.get(2).map(String::as_str).unwrap_or("")));
            std::process::exit(0)
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn ids_and_keys() {
        assert!(pane_id("%12") && !pane_id("%") && !pane_id("@1") && !pane_id("%1a"));
        assert!(window_id("@3") && !window_id("%3"));
        assert_eq!(applied_key("212", "57"), "212x57");
    }

    #[test]
    fn single_flight_coalesces_concurrent_events_into_one_rerun() {
        let d = tempfile::tempdir().unwrap();
        let first = Flight::acquire(d.path(), "w").unwrap().expect("first holds the window");
        for _ in 0..50 {
            assert!(Flight::acquire(d.path(), "w").unwrap().is_none(), "concurrent events never start a second pass");
        }
        assert!(first.take_dirty(), "the holder sees one pending rerun for all 50 events");
        assert!(!first.take_dirty(), "and only one");
        drop(first);
        assert!(Flight::acquire(d.path(), "w").unwrap().is_some(), "released after the pass");
        assert!(Flight::acquire(d.path(), "other").unwrap().is_some(), "windows are independent");
    }

    #[test]
    fn unknown_event_is_rejected_and_unbound_hooks_do_nothing() {
        assert!(run("bogus", None, None).is_err());
        // SAFETY of the test: TMUX/TMUX_PANE unset -> nothing to bind, no tmux call, no SSOT read
        std::env::remove_var("TMUX");
        std::env::remove_var("TMUX_PANE");
        assert_eq!(run("ensure", None, None).unwrap(), "unbound");
    }
}
