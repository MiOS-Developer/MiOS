// AI-hint: Witnessed native tmux focus and logical input ownership; no device reader or compositor IPC.
// AI-related: /usr/share/mios/templates/rust, runtime::OwnershipWorker, console::run
// AI-functions: observe, transaction, leaf_client, frontend
use crate::config::GlobalConfig;
use crate::runtime::{run_bounded, TmuxClient};
use serde::{Deserialize, Serialize};
use std::os::unix::fs::MetadataExt;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

const OWNER: &str = "@mios_inputd_owner";
const FORMAT: &str = "#{client_pid}|#{client_tty}|#{session_id}|#{window_id}|#{pane_id}|#{pane_pid}|#{window_zoomed_flag}|#{pid}|#{session_name}|#{client_instance}|#{client_window_id}|#{client_pane_id}|#{client_pane_pid}|#{client_window_zoomed_flag}|#{client_flags}|#{client_window_action_version}";
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Target {
    pub uid: u32,
    pub generation: u64,
    pub login_generation: u64,
    pub socket: PathBuf,
    pub socket_dev: u64,
    pub socket_ino: u64,
    pub server: u32,
    pub server_start: u64,
    pub client: u32,
    pub client_start: u64,
    #[serde(default)]
    pub client_instance: u64,
    pub tty: String,
    pub session: String,
    pub session_name: String,
    pub window: String,
    pub pane: String,
    pub pane_pid: u32,
    pub pane_start: u64,
    pub foreground: u32,
    pub foreground_start: u64,
    pub zoomed: bool,
}
#[derive(Clone, Debug, Serialize, Deserialize, PartialEq, Eq)]
struct Owner {
    active: bool,
    target: Target,
    /// Only undo zoom introduced by this capture, never an operator's existing zoom.
    zoom_owned: bool,
}
impl Target {
    pub fn client(&self) -> TmuxClient {
        TmuxClient {
            pid: self.client,
            start: self.client_start,
            instance: self.client_instance,
            socket_dev: self.socket_dev,
            socket_ino: self.socket_ino,
            tty: self.tty.clone(),
            socket: self.socket.clone(),
        }
    }
    pub fn address(&self) -> String {
        format!(
            "tmux:{}:{}:{}:{}:{}:{}:{}:{}:{}:{}:{}:{}:{}:{}:{}",
            self.uid,
            self.socket_dev,
            self.socket_ino,
            self.server,
            self.server_start,
            self.client,
            self.client_start,
            self.client_instance,
            self.session,
            self.window,
            self.pane,
            self.foreground,
            self.foreground_start,
            self.generation,
            self.login_generation
        )
    }
    pub(crate) fn matches(&self, other: &Self) -> bool {
        self.same(other)
            && self.generation == other.generation
            && self.login_generation == other.login_generation
    }
    fn same(&self, other: &Self) -> bool {
        let mut a = self.clone();
        let mut b = other.clone();
        a.zoomed = false;
        b.zoomed = false;
        a.generation = 0;
        b.generation = 0;
        a.login_generation = 0;
        b.login_generation = 0;
        a == b
    }
}
fn start(pid: u32) -> Result<u64, String> {
    use std::os::unix::fs::MetadataExt;
    let path = PathBuf::from(format!("/proc/{pid}"));
    if pid == 0
        || std::fs::metadata(&path).map_err(|e| e.to_string())?.uid() != unsafe { libc::getuid() }
    {
        return Err("foreign or missing tmux process".into());
    }
    crate::nested::start_time(Path::new("/proc"), pid)
        .ok_or("process start identity missing".into())
}
fn foreground(pid: u32) -> Result<u32, String> {
    let stat = std::fs::read_to_string(format!("/proc/{pid}/stat")).map_err(|e| e.to_string())?;
    stat[stat.rfind(')').ok_or("malformed pane process")? + 1..]
        .split_whitespace()
        .nth(5)
        .and_then(|s| s.parse().ok())
        .filter(|p| *p > 0)
        .ok_or("pane has no foreground process".into())
}
fn query(client: &TmuxClient, rest: &[&str], timeout: Duration) -> Result<String, String> {
    let mut argv = vec![
        "/usr/bin/tmux".into(),
        "-S".into(),
        client.socket.to_string_lossy().into_owned(),
    ];
    argv.extend(rest.iter().map(|s| (*s).to_owned()));
    run_bounded(&argv, timeout)
}
fn id(s: &str, prefix: char) -> bool {
    s.strip_prefix(prefix)
        .is_some_and(|s| !s.is_empty() && s.bytes().all(|b| b.is_ascii_digit()))
}
/// Dedicated fields use server_client_get_pane(), including CLIENT_ACTIVEPANE.
/// Older servers remain usable only without a per-client pane override. New
/// atomic window actions always require the actual-focus capability.
fn focused_fields<'a>(
    v: &[&'a str],
    require_actual: bool,
) -> Result<(&'a str, &'a str, &'a str, &'a str), String> {
    if v.len() != 16 {
        return Err("incomplete native client focus record".into());
    }
    if v[15] == "1" && v[10..14].iter().all(|value| !value.is_empty()) {
        if !id(v[10], '@') || !id(v[11], '%') {
            return Err("malformed actual client focus".into());
        }
        return Ok((v[10], v[11], v[12], v[13]));
    }
    if require_actual
        || !v[15].is_empty()
        || v[10..14].iter().any(|value| !value.is_empty())
        || v[14].split(',').any(|flag| flag == "active-pane")
    {
        return Err("tmux server lacks witnessed per-client pane focus".into());
    }
    Ok((v[3], v[4], v[5], v[6]))
}
/// list-clients establishes each client's own session context; display-message -c alone does not.
pub fn observe(client: &TmuxClient, timeout: Duration) -> Result<Target, String> {
    observe_inner(client, timeout, false)
}
fn observe_inner(
    client: &TmuxClient,
    timeout: Duration,
    require_actual: bool,
) -> Result<Target, String> {
    let client_start = start(client.pid)?;
    if client.start != 0 && client_start != client.start {
        return Err("tmux client process start changed".into());
    }
    let meta = std::fs::symlink_metadata(&client.socket).map_err(|e| e.to_string())?;
    use std::os::unix::fs::FileTypeExt;
    if !meta.file_type().is_socket() || meta.uid() != unsafe { libc::getuid() } {
        return Err("untrusted tmux socket".into());
    }
    if client.socket_ino != 0 && (meta.dev(), meta.ino()) != (client.socket_dev, client.socket_ino)
    {
        return Err("tmux client socket identity changed".into());
    }
    let rows = query(client, &["list-clients", "-F", FORMAT], timeout)?;
    let mut found = rows.lines().filter_map(|line| {
        let v: Vec<_> = line.split('|').collect();
        (v.len() == 16 && v[0].parse::<u32>().ok() == Some(client.pid) && v[1] == client.tty)
            .then_some(v)
    });
    let v = found.next().ok_or("tmux client is no longer attached")?;
    let (window, pane, pane_pid, zoomed) = focused_fields(&v, require_actual)?;
    if found.next().is_some() || !id(v[2], '$') || !id(window, '@') || !id(pane, '%') {
        return Err("ambiguous tmux client or malformed pane identity".into());
    }
    let pane_pid = pane_pid.parse::<u32>().map_err(|_| "invalid pane PID")?;
    let server = v[7].parse::<u32>().map_err(|_| "invalid server PID")?;
    let foreground = foreground(pane_pid)?;
    let target = Target {
        uid: unsafe { libc::getuid() },
        generation: 0,
        login_generation: 0,
        socket: client.socket.clone(),
        socket_dev: meta.dev(),
        socket_ino: meta.ino(),
        server,
        server_start: start(server)?,
        client: client.pid,
        client_start,
        client_instance: if v[9].is_empty() {
            0
        } else {
            v[9].parse().map_err(|_| "invalid native client instance")?
        },
        tty: client.tty.clone(),
        session: v[2].into(),
        session_name: v[8].into(),
        window: window.into(),
        pane: pane.into(),
        pane_pid,
        pane_start: start(pane_pid)?,
        foreground,
        foreground_start: start(foreground)?,
        zoomed: match zoomed {
            "0" => false,
            "1" => true,
            _ => return Err("invalid zoom state".into()),
        },
    };
    if client.instance != 0 && target.client_instance != client.instance {
        return Err("tmux client instance changed".into());
    }
    if start(client.pid)? != client_start {
        return Err("tmux client was replaced".into());
    }
    let now = std::fs::symlink_metadata(&client.socket).map_err(|e| e.to_string())?;
    if (now.dev(), now.ino()) != (target.socket_dev, target.socket_ino) {
        return Err("tmux server socket was replaced".into());
    }
    Ok(target)
}
fn zoom_argv(target: &Target) -> Result<Vec<String>, String> {
    if target.client_instance == 0
        || target.client == 0
        || target.pane_pid == 0
        || !target.tty.starts_with("/dev/")
        || target.tty.chars().any(char::is_control)
        || !id(&target.session, '$')
        || !id(&target.window, '@')
        || !id(&target.pane, '%')
    {
        return Err("zoom requires a complete native client identity".into());
    }
    Ok(vec![
        "/usr/bin/tmux".into(),
        "-S".into(),
        target.socket.to_string_lossy().into_owned(),
        "client-window-action".into(),
        "-t".into(),
        target.tty.clone(),
        "-C".into(),
        target.client.to_string(),
        "-I".into(),
        target.client_instance.to_string(),
        "-s".into(),
        target.session.clone(),
        "-w".into(),
        target.window.clone(),
        "-p".into(),
        target.pane.clone(),
        "-P".into(),
        target.pane_pid.to_string(),
        "zoom".into(),
    ])
}
/// Independent from capture ownership: zoom never acquires/releases the pad or
/// changes an ownership generation. The final focus comparison and layout
/// change happen together inside the native tmux server event loop.
pub(crate) fn zoom(
    target: &Target,
    timeout: Duration,
    valid: impl FnOnce() -> bool,
) -> Result<(), String> {
    let deadline = Instant::now() + timeout;
    if !live_target(target, false) {
        return Err("zoom focus or login generation expired".into());
    }
    let actual = observe_inner(&target.client(), timeout, true)?;
    if !actual.same(target) {
        return Err("zoom client focus changed".into());
    }
    if !valid() {
        return Err("zoom focus visit or login changed during verification".into());
    }
    let reply = run_bounded(
        &zoom_argv(target)?,
        deadline.saturating_duration_since(Instant::now()),
    )?;
    if reply.trim() == "ok" {
        Ok(())
    } else {
        Err("native focused-window zoom was not acknowledged".into())
    }
}
pub(crate) fn app_process(target: &Target, timeout: Duration) -> Result<u32, String> {
    if !live_target(target, false) {
        return Err("app input focus expired".into());
    }
    let actual = observe(&target.client(), timeout)?;
    if !actual.same(target) {
        return Err("app input client focus changed".into());
    }
    Ok(actual.foreground)
}
fn encode(owner: &Owner) -> Result<String, String> {
    Ok(serde_json::to_vec(owner)
        .map_err(|e| e.to_string())?
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect())
}
fn decode(s: &str) -> Option<Owner> {
    if s.len() > 8192 || !s.len().is_multiple_of(2) || !s.bytes().all(|b| b.is_ascii_hexdigit()) {
        return None;
    }
    let bytes: Option<Vec<u8>> = (0..s.len())
        .step_by(2)
        .map(|i| u8::from_str_radix(&s[i..i + 2], 16).ok())
        .collect();
    serde_json::from_slice(&bytes?).ok()
}
fn owner(client: &TmuxClient, timeout: Duration) -> Result<(String, Option<Owner>), String> {
    let value = query(client, &["show-options", "-gqv", OWNER], timeout)?
        .trim()
        .to_owned();
    let parsed = decode(&value);
    if !value.is_empty() && parsed.is_none() {
        return Err("invalid native capture owner".into());
    }
    Ok((value, parsed))
}
/// A compare-and-set within one tmux command prevents a late release from clearing another pane's capture.
fn change(
    client: &TmuxClient,
    prior: &str,
    next: &str,
    extra: &[String],
    timeout: Duration,
) -> Result<(), String> {
    if !prior.bytes().all(|b| b.is_ascii_hexdigit()) || !next.bytes().all(|b| b.is_ascii_hexdigit())
    {
        return Err("invalid owner encoding".into());
    }
    let mut commands = vec![format!("set-option -g {OWNER} '{next}'")];
    commands.extend_from_slice(extra);
    let reply = query(
        client,
        &[
            "if-shell",
            "-F",
            "-t",
            &client.tty,
            &format!("#{{==:#{{{OWNER}}},{prior}}}"),
            &commands.join(" ; "),
            "display-message -p capture-conflict",
        ],
        timeout,
    )?;
    if reply.contains("capture-conflict") {
        Err("capture ownership changed".into())
    } else {
        Ok(())
    }
}
/// The sole logical owner is tied to the actual focused client/pane/process. A focus loss consumes the old lease.
pub fn current(client: &TmuxClient, timeout: Duration) -> Result<(Target, bool), String> {
    let mut target = observe(client, timeout)?;
    let (value, prior) = owner(client, timeout)?;
    if let Some(prior) = prior.filter(|p| p.active) {
        if prior.target.same(&target) {
            target.generation = prior.target.generation;
            return Ok((target, true));
        }
        // Losing focus invalidates capture even when the original pane remains alive. Never steal focus back.
        release(client, &value, &prior, timeout)?;
    }
    Ok((target, false))
}
fn release(
    client: &TmuxClient,
    value: &str,
    prior: &Owner,
    timeout: Duration,
) -> Result<(), String> {
    // Pane IDs are server-local. Validate the old socket/server witnesses before touching its owned zoom.
    let meta = std::fs::symlink_metadata(&client.socket).map_err(|e| e.to_string())?;
    let mut extra = vec![];
    if prior.target.socket == client.socket
        && (meta.dev(), meta.ino()) == (prior.target.socket_dev, prior.target.socket_ino)
        && start(prior.target.server).ok() == Some(prior.target.server_start)
        && prior.zoom_owned
    {
        extra.push(format!(
            "if-shell -F -t {} '#{{window_zoomed_flag}}' 'resize-pane -Z -t {}'",
            prior.target.pane, prior.target.pane
        ));
    }
    let mut released = prior.clone();
    released.active = false;
    released.zoom_owned = false;
    change(client, value, &encode(&released)?, &extra, timeout)
}
pub(crate) fn release_target(target: &Target, timeout: Duration) -> Result<bool, String> {
    let (value, prior) = owner(&target.client(), timeout)?;
    let Some(prior) = prior
        .filter(|p| p.active && p.target.same(target) && p.target.generation == target.generation)
    else {
        return Ok(false);
    };
    release(&target.client(), &value, &prior, timeout)?;
    Ok(true)
}
/// Same semantic modes as the compositor backend; `on` leaves the floating rectangle untouched.
pub fn transaction(
    client: &TmuxClient,
    mode: &str,
    expected: Option<&str>,
    timeout: Duration,
) -> Result<Target, String> {
    let mut target = observe(client, timeout)?;
    let (mut value, mut prior) = owner(client, timeout)?;
    if let Some(p) = prior
        .as_ref()
        .filter(|p| p.active && p.target.same(&target))
    {
        target.generation = p.target.generation;
    }
    if let Some(expected) = expected {
        if expected != target.address() {
            return Err("focused native pane changed".into());
        }
    }
    if mode == "inspect" {
        if prior
            .as_ref()
            .is_none_or(|p| !p.active || !p.target.same(&target))
        {
            return Err("pane has no focused capture lease".into());
        }
        return Ok(target);
    }
    if mode == "off" {
        if let Some(prior) = prior.as_ref().filter(|p| p.active) {
            release(client, &value, prior, timeout)?;
        }
        return Ok(target);
    }
    if mode != "on" && mode != "fullscreen" {
        return Err("invalid native capture mode".into());
    }
    if let Some(p) = prior
        .as_ref()
        .filter(|p| p.active && !p.target.same(&target))
    {
        release(client, &value, p, timeout)?;
        (value, prior) = owner(client, timeout)?;
    }
    let zoom_owned = prior
        .as_ref()
        .is_some_and(|p| p.active && p.target.same(&target) && p.zoom_owned);
    if mode == "fullscreen"
        && prior
            .as_ref()
            .is_none_or(|p| !p.active || !p.target.same(&target))
    {
        return Err("fullscreen requires the confirmed focused owner".into());
    }
    let zoom = mode == "fullscreen" && !target.zoomed;
    if mode == "on"
        && prior
            .as_ref()
            .is_none_or(|p| !p.active || !p.target.same(&target))
    {
        target.generation = prior
            .as_ref()
            .map_or(0, |p| p.target.generation)
            .checked_add(1)
            .ok_or("native ownership generation exhausted")?;
    }
    let next = encode(&Owner {
        active: true,
        target: target.clone(),
        zoom_owned: zoom_owned || zoom,
    })?;
    let extra = if zoom {
        vec![format!("resize-pane -Z -t {}", target.pane)]
    } else {
        vec![]
    };
    change(client, &value, &next, &extra, timeout)?;
    // Reject a focus race after the transaction as well; the next poll also invalidates the receipt.
    let mut actual = observe(client, timeout)?;
    if !actual.same(&target) {
        let _ = current(client, timeout);
        return Err("pane changed during capture".into());
    }
    actual.generation = target.generation;
    Ok(actual)
}
/// Follow only the active pane's foreground process tree, never a different pane or a global arbitrary tmux client.
pub fn leaf_client(
    cfg: &GlobalConfig,
    first: &TmuxClient,
    timeout: Duration,
) -> Result<TmuxClient, String> {
    let deadline = Instant::now() + timeout;
    let mut client = first.clone();
    let mut seen = std::collections::BTreeSet::new();
    for _ in 0..4 {
        if !seen.insert(client.pid) {
            return Err("nested tmux client cycle".into());
        }
        let target = observe(&client, deadline.saturating_duration_since(Instant::now()))?;
        let mut pending = vec![target.foreground];
        let mut visited = std::collections::BTreeSet::new();
        let mut next = None;
        while let Some(pid) = pending.pop() {
            if Instant::now() >= deadline || visited.len() >= 128 {
                return Err("nested tmux focus discovery exceeded its bound".into());
            }
            if !visited.insert(pid) || start(pid).is_err() {
                continue;
            }
            if let Some(found) = crate::runtime::tmux_client_at(
                Path::new("/proc"),
                pid,
                &cfg.controller.nav.tmux_sockets,
                unsafe { libc::getuid() },
            ) {
                if found.pid != client.pid {
                    next = Some(found);
                    break;
                }
            }
            if let Ok(children) =
                std::fs::read_to_string(format!("/proc/{pid}/task/{pid}/children"))
            {
                pending.extend(
                    children
                        .split_whitespace()
                        .filter_map(|s| s.parse::<u32>().ok()),
                );
            }
        }
        match next {
            Some(next) => client = next,
            None => return Ok(target.client()),
        }
    }
    Err("nested tmux focus depth exceeded".into())
}

/// Select an attached client only when this process's inherited pane is actually focused there.
/// A multi-client ambiguity requires an explicit client; it never chooses the first arbitrary row.
pub fn environment_client(cfg: &GlobalConfig) -> Result<TmuxClient, String> {
    let tmux = std::env::var("TMUX").map_err(|_| "native capture requires TMUX")?;
    let mut parts = tmux.rsplitn(3, ',');
    parts
        .next()
        .and_then(|s| s.parse::<u32>().ok())
        .ok_or("invalid TMUX session")?;
    let server = parts
        .next()
        .and_then(|s| s.parse::<u32>().ok())
        .filter(|p| *p > 0)
        .ok_or("invalid TMUX server")?;
    let socket = PathBuf::from(parts.next().ok_or("missing TMUX socket")?);
    if !socket.is_absolute() {
        return Err("TMUX socket is not absolute".into());
    }
    let pane = std::env::var("TMUX_PANE").map_err(|_| "missing TMUX_PANE")?;
    if !id(&pane, '%') {
        return Err("invalid TMUX_PANE".into());
    }
    let probe = TmuxClient {
        pid: server,
        start: 0,
        instance: 0,
        socket_dev: 0,
        socket_ino: 0,
        tty: String::new(),
        socket: socket.clone(),
    };
    let timeout = Duration::from_millis(cfg.controller.context_stale_ms);
    let rows = query(&probe, &["list-clients", "-F", FORMAT], timeout)?;
    let mut found = vec![];
    for row in rows.lines() {
        let v: Vec<_> = row.split('|').collect();
        let Ok((_, actual_pane, _, _)) = focused_fields(&v, false) else {
            continue;
        };
        if actual_pane != pane {
            continue;
        }
        let Some(pid) = v[0].parse::<u32>().ok() else {
            continue;
        };
        let Some(client) = crate::runtime::tmux_client_at(
            Path::new("/proc"),
            pid,
            &cfg.controller.nav.tmux_sockets,
            unsafe { libc::getuid() },
        ) else {
            continue;
        };
        if client.socket != socket || client.tty != v[1] {
            continue;
        }
        let observed = observe(&client, timeout)?;
        if observed.server == server && observed.pane == pane {
            found.push(observed.client());
        }
    }
    if found.len() != 1 {
        return Err("native pane has no unique focused client".into());
    }
    Ok(found.remove(0))
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Frontend {
    pub target: Target,
    pub outer_client: u32,
    pub outer_start: u64,
    /// Four resolved RS camera actions, in left/right/up/down order. No second binding table.
    pub camera: [String; 4],
    pub rate_ms: [u64; 4],
}
impl Frontend {
    pub fn motion(&self, dx: i32, dy: i32) -> Vec<(usize, u8)> {
        [(dx, 0, 1), (dy, 2, 3)]
            .into_iter()
            .filter(|(d, _, _)| *d != 0)
            .map(|(delta, negative, positive)| {
                (
                    if delta < 0 { negative } else { positive },
                    delta.unsigned_abs().min(8) as u8,
                )
            })
            .collect()
    }
}
/// Consume only the existing input owner's fresh private manifest, never reopen/grab a physical device.
fn read_manifest(runtime: &Path) -> Option<serde_json::Value> {
    use std::io::Read;
    use std::os::unix::fs::OpenOptionsExt;
    let path = runtime.join("mios-inputd-active.json");
    let mut file = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(path)
        .ok()?;
    let meta = file.metadata().ok()?;
    if !meta.is_file()
        || meta.uid() != unsafe { libc::getuid() }
        || meta.mode() & 0o077 != 0
        || meta.len() > 65536
    {
        return None;
    }
    let mut data = vec![];
    file.by_ref().take(65537).read_to_end(&mut data).ok()?;
    if data.len() > 65536 {
        return None;
    }
    let value: serde_json::Value = serde_json::from_slice(&data).ok()?;
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .ok()?
        .as_millis() as u64;
    let stamp = value["timestamp_ms"].as_u64()?;
    let stale = value["frontend_stale_ms"]
        .as_u64()
        .filter(|ms| (20..=60_000).contains(ms))?;
    if now.abs_diff(stamp) >= stale || stale == 0 {
        return None;
    }
    let pid = u32::try_from(value["bridge_pid"].as_u64()?).ok()?;
    if start(pid).ok() != value["bridge_start"].as_u64() {
        return None;
    }
    Some(value)
}
pub fn focused_target() -> Option<(Target, bool, bool)> {
    let runtime = std::env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(format!("/run/user/{}", unsafe { libc::getuid() })));
    let value = read_manifest(&runtime)?;
    let target: Target = serde_json::from_value(value["native_focus"].clone()).ok()?;
    if target.uid != unsafe { libc::getuid() }
        || start(target.client).ok() != Some(target.client_start)
        || start(target.server).ok() != Some(target.server_start)
        || start(target.foreground).ok() != Some(target.foreground_start)
    {
        return None;
    }
    Some((
        target,
        value["native_owned"].as_bool()?,
        value["native_remote"].as_bool()?,
    ))
}
pub fn read_frontend(runtime: &Path, outer_client: u32, outer_start: u64) -> Option<Frontend> {
    let value = read_manifest(runtime)?;
    let front: Frontend = serde_json::from_value(value["frontend"].clone()).ok()?;
    if front.target.uid != unsafe { libc::getuid() }
        || front.outer_client != outer_client
        || front.outer_start != outer_start
        || start(outer_client).ok() != Some(outer_start)
        || start(front.target.foreground).ok() != Some(front.target.foreground_start)
        || start(front.target.client).ok() != Some(front.target.client_start)
        || front.camera.iter().any(|a| {
            a.len() > 128
                || a.strip_prefix("key:")
                    .and_then(|k| crate::nav::parse_key_chord(k).ok())
                    .is_none()
        })
        || front.rate_ms.iter().any(|ms| *ms == 0 || *ms > 60_000)
    {
        return None;
    }
    Some(front)
}

/// Output is pinned to its original logical generation. The context worker alone queries active tmux focus;
/// key/mouse forwarding checks its cached lease and cheap process witnesses, with no per-motion focus command.
pub(crate) fn live_target(target: &Target, owned_only: bool) -> bool {
    let runtime = std::env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(format!("/run/user/{}", unsafe { libc::getuid() })));
    let Some(value) = read_manifest(&runtime) else {
        return false;
    };
    if owned_only && value["native_owned"].as_bool() != Some(true) {
        return false;
    }
    let Ok(current) = serde_json::from_value::<Target>(value["native_focus"].clone()) else {
        return false;
    };
    current.matches(target)
        && target.uid == unsafe { libc::getuid() }
        && start(target.client).ok() == Some(target.client_start)
        && start(target.server).ok() == Some(target.server_start)
        && start(target.foreground).ok() == Some(target.foreground_start)
        && foreground(target.pane_pid).ok() == Some(target.foreground)
        && std::fs::symlink_metadata(&target.socket)
            .ok()
            .is_some_and(|m| (m.dev(), m.ino()) == (target.socket_dev, target.socket_ino))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn target() -> Target {
        Target {
            uid: 1000,
            generation: 7,
            login_generation: 0,
            socket: "/run/user/1000/tmux/mios-desktop".into(),
            socket_dev: 1,
            socket_ino: 2,
            server: 10,
            server_start: 11,
            client: 20,
            client_start: 21,
            client_instance: 0,
            tty: "/dev/pts/30".into(),
            session: "$1".into(),
            session_name: "owned-desktop".into(),
            window: "@2".into(),
            pane: "%3".into(),
            pane_pid: 40,
            pane_start: 41,
            foreground: 50,
            foreground_start: 51,
            zoomed: false,
        }
    }
    #[test]
    fn actual_client_fields_override_the_shared_active_pane_and_legacy_overrides_fail_closed() {
        let row = "20|/dev/pts/30|$1|@2|%3|40|0|10|owned-desktop|9|@4|%5|60|1|active-pane|1";
        let mut fields = row.split('|').collect::<Vec<_>>();
        assert_eq!(
            focused_fields(&fields, true).unwrap(),
            ("@4", "%5", "60", "1")
        );
        fields[11] = "";
        assert!(
            focused_fields(&fields, false).is_err(),
            "an R8 missing focused pane is invalid, never a legacy fallback"
        );
        fields[10] = "";
        fields[12] = "";
        fields[13] = "";
        fields[15] = "";
        assert!(
            focused_fields(&fields, false).is_err(),
            "R7 active-pane clients cannot use the shared active pane"
        );
        fields[14] = "";
        assert_eq!(
            focused_fields(&fields, false).unwrap(),
            ("@2", "%3", "40", "0")
        );
        assert!(
            focused_fields(&fields, true).is_err(),
            "zoom always requires the R8 capability"
        );
        fields[15] = "2";
        assert!(
            focused_fields(&fields, false).is_err(),
            "unknown capabilities do not downgrade"
        );
    }
    #[test]
    fn focused_zoom_uses_one_atomic_command_with_all_client_and_pane_witnesses() {
        let mut target = target();
        assert!(
            zoom_argv(&target).is_err(),
            "old client instances cannot zoom"
        );
        target.client_instance = 99;
        assert_eq!(
            zoom_argv(&target).unwrap(),
            vec![
                "/usr/bin/tmux",
                "-S",
                "/run/user/1000/tmux/mios-desktop",
                "client-window-action",
                "-t",
                "/dev/pts/30",
                "-C",
                "20",
                "-I",
                "99",
                "-s",
                "$1",
                "-w",
                "@2",
                "-p",
                "%3",
                "-P",
                "40",
                "zoom"
            ]
        );
        target.pane = "%3;run-shell bad".into();
        assert!(zoom_argv(&target).is_err());
    }
    #[test]
    fn captured_outputs_cannot_cross_user_login_or_capture_generations() {
        let a = target();
        let mut b = target();
        b.uid = 1001;
        b.socket = "/run/user/1001/tmux/mios-desktop".into();
        let b_before = b.clone();
        let mut a_next = a.clone();
        a_next.client = 22;
        a_next.client_start = 23;
        a_next.pane = "%4".into();
        a_next.foreground = 60;
        a_next.foreground_start = 61;
        assert!(
            !a.matches(&a_next),
            "queued A events stay pinned to A's old login"
        );
        assert!(
            !a.matches(&b),
            "a peer UID cannot retarget another user's assignment"
        );
        assert!(
            b.matches(&b_before),
            "moving A has no effect on B's witnessed target"
        );
        let mut recaptured = a.clone();
        recaptured.generation += 1;
        assert!(
            !a.matches(&recaptured),
            "release/recapture on the same pane is a new logical generation"
        );
        assert_ne!(a.address(), recaptured.address());
        let mut new_login = a.clone();
        new_login.login_generation += 1;
        assert!(!a.matches(&new_login),"old-generation events cannot follow the same leaf pane into another authenticated login");
        assert_ne!(a.address(), new_login.address());
        let mut zoomed = a.clone();
        zoomed.zoomed = true;
        assert!(
            a.matches(&zoomed),
            "fullscreen is an upgrade of the existing lease"
        );
        let owner = Owner {
            active: true,
            target: a.clone(),
            zoom_owned: false,
        };
        let encoded = encode(&owner).unwrap();
        assert_eq!(decode(&encoded), Some(owner));
        let released = Owner {
            active: false,
            target: a,
            zoom_owned: false,
        };
        assert!(!decode(&encode(&released).unwrap()).unwrap().active);
        assert!(
            b.matches(&b_before),
            "logging out A releases only A's owner record"
        );
    }
    #[test]
    fn relative_motion_is_bounded_and_uses_resolved_camera_directions() {
        let front = Frontend {
            target: target(),
            outer_client: 1,
            outer_start: 2,
            camera: [
                "key:ALT+7".into(),
                "key:ALT+9".into(),
                "key:ALT+1".into(),
                "key:ALT+3".into(),
            ],
            rate_ms: [100; 4],
        };
        assert!(front.motion(0, 0).is_empty());
        assert_eq!(front.motion(i32::MIN, i32::MAX), vec![(0, 8), (3, 8)]);
        assert_eq!(front.motion(3, -2), vec![(1, 3), (2, 2)]);
    }
    #[test]
    fn frontend_manifest_never_follows_a_symlink_or_public_record() {
        use std::os::unix::fs::{symlink, PermissionsExt};
        let dir = tempfile::tempdir().unwrap();
        let outside = dir.path().join("outside");
        std::fs::write(&outside, b"{}").unwrap();
        symlink(&outside, dir.path().join("mios-inputd-active.json")).unwrap();
        assert!(read_manifest(dir.path()).is_none());
        std::fs::remove_file(dir.path().join("mios-inputd-active.json")).unwrap();
        std::fs::write(dir.path().join("mios-inputd-active.json"), b"{}").unwrap();
        std::fs::set_permissions(
            dir.path().join("mios-inputd-active.json"),
            std::fs::Permissions::from_mode(0o644),
        )
        .unwrap();
        assert!(read_manifest(dir.path()).is_none());
    }
    #[test]
    fn owner_encoding_is_format_inert_and_rejects_malformed_values() {
        assert!(decode("#{pane_id}").is_none());
        assert!(decode("a").is_none());
        assert!(decode(&"ab".repeat(4097)).is_none());
        assert!(!id("%1;kill-pane", '%'));
        assert!(!id("%", '%'));
        assert!(id("%12", '%'));
    }
}
