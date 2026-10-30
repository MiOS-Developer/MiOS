// AI-hint: Native controller service, bounded compositor IPC with live socket rediscovery, the logind seat view (graphical / console / another session), named action dispatch, the virtual keyboard/tmux/OSD/VT projections and /proc tmux client discovery (focused terminal or active VT).
// AI-related: keybindings.global, desktop.hyprland.hypr3d.controller, mios-inputd.service
use crate::config::{GlobalConfig, LegacyConfig};
use crate::linux::{self, Device, VirtualDevice};
use crate::router::{Output, PadState, Router, EV_SYN, SYN_DROPPED, SYN_REPORT};
use std::collections::{BTreeMap,BTreeSet};
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};
static STOP: AtomicBool = AtomicBool::new(false);
extern "C" fn stop_signal(_: libc::c_int) {
    STOP.store(true, Ordering::Relaxed);
}
pub struct Options {
    pub root: Option<PathBuf>,
    pub bridge: bool,
    pub fixture_only: bool,
    pub device: Option<PathBuf>,
    pub context: Option<String>,
    pub duration: Option<Duration>,
    pub pad_path: PathBuf,
    /// Fixture tier only: follow the selected compositor (HYPRLAND_INSTANCE_SIGNATURE, e.g. an owned nested session),
    /// run its ownership transactions there and project nav output (key:, hypr:, tmux:) into it. quickshell:, exec: and
    /// mios: actions stay printed and are never executed; no uinput keyboard or pointer is created.
    pub live_context: bool,
    /// Write the room packet. The physical bridge always does; a fixture only to an explicit private path.
    pub write_pad: bool,
    /// The logind runtime state the seat view reads (/run/systemd for the physical bridge; a private copy for tests).
    /// None: no seat view (the fixture tier without --logind-state follows its compositor only).
    pub logind_state: Option<PathBuf>,
    /// Fixture tier: the tty that stands in for the active VT (/dev/tty<VTNr> otherwise), e.g. a test pty.
    pub console_tty: Option<PathBuf>,
    /// Fixture tier: console-context key: strokes are written to this inherited fd as the bytes the Linux console
    /// keymap would deliver (a pty master standing in for the VT). Never a uinput keyboard.
    pub console_sink_fd: Option<i32>,
    /// Re-select the compositor when the signature's socket dies (the physical bridge always does; a fixture only in
    /// a private XDG_RUNTIME_DIR it names with --rediscover).
    pub rediscover: bool,
}
struct RunningDevice {
    device: Device,
    output: Option<VirtualDevice>,
    router: Router,
    dropped: bool,
    /// INV-8: another session owns the seat; the grab is released and nothing is routed until it returns.
    suspended: bool,
}
/// The compositor this process follows. Starts as HYPRLAND_INSTANCE_SIGNATURE; rediscovery (C-16) replaces a stale one
/// after a compositor restart, so the user unit never needs a restart.
static SIGNATURE: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);
fn signature() -> Result<String, String> {
    if let Some(s) = SIGNATURE.lock().ok().and_then(|s| s.clone()) {
        return Ok(s);
    }
    std::env::var("HYPRLAND_INSTANCE_SIGNATURE").map_err(|_| "selected compositor signature missing".into())
}
fn proc_start(proc_root: &Path, pid: u32) -> Option<(u32, u64)> {
    let stat = std::fs::read_to_string(proc_root.join(pid.to_string()).join("stat")).ok()?;
    let rest = &stat[stat.rfind(')')? + 1..];
    let f: Vec<&str> = rest.split_whitespace().collect();
    Some((f.get(1)?.parse().ok()?, f.get(19)?.parse().ok()?))
}
/// C-16: the live compositor instances in `runtime/hypr` (lock pid alive, socket present), newest first, without the
/// ones nested inside another listed instance. An instance whose process is gone is never selected.
pub fn live_instances(runtime: &Path, proc_root: &Path) -> Vec<(String, u32)> {
    use std::os::unix::fs::FileTypeExt;
    let mut found = vec![];
    for entry in std::fs::read_dir(runtime.join("hypr")).into_iter().flatten().filter_map(Result::ok) {
        let Some(sig) = entry.file_name().to_str().map(str::to_owned) else { continue };
        if sig.contains('/') || sig.starts_with('.') {
            continue;
        }
        let Some(pid) = std::fs::read_to_string(entry.path().join("hyprland.lock")).ok().and_then(|t| t.lines().next()?.trim().parse::<u32>().ok()) else { continue };
        let Some((_, start)) = proc_start(proc_root, pid) else { continue };
        if !std::fs::metadata(entry.path().join(".socket.sock")).is_ok_and(|m| m.file_type().is_socket()) {
            continue;
        }
        found.push((sig, pid, start));
    }
    let pids: BTreeSet<u32> = found.iter().map(|(_, p, _)| *p).collect();
    let nested = |pid: u32| {
        let mut p = pid;
        for _ in 0..64 {
            match proc_start(proc_root, p) {
                Some((parent, _)) if parent > 1 && parent != p => {
                    if pids.contains(&parent) {
                        return true;
                    }
                    p = parent;
                }
                _ => return false,
            }
        }
        false
    };
    let mut live: Vec<_> = found.into_iter().filter(|(_, p, _)| !nested(*p)).collect();
    live.sort_by(|a, b| b.2.cmp(&a.2).then(b.0.cmp(&a.0)));
    live.into_iter().map(|(s, p, _)| (s, p)).collect()
}
/// C-16: keep the followed signature while its instance lives; otherwise the newest live top-level instance.
pub fn rediscover(runtime: &Path, proc_root: &Path, current: Option<&str>) -> Option<String> {
    let live = live_instances(runtime, proc_root);
    if let Some(c) = current.filter(|c| live.iter().any(|(s, _)| s == c)) {
        return Some(c.to_owned());
    }
    live.into_iter().next().map(|(s, _)| s)
}
/// Where this user's active seat session is (logind runtime state, read as files: no spawn per check).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Seat {
    /// This user's graphical session is active on the seat (or no seat view: the fixture tier following a compositor).
    Graphical,
    /// This user's tty session is active: its VT number and tty device (C-16 `console`).
    Console { vt: u32, tty: PathBuf },
    /// The greeter, another user, or nothing is active: nothing is synthesized and the grab is released (INV-8).
    Foreign(String),
}
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct SeatView {
    pub seat: Seat,
    /// The VT of this user's graphical session (`mios:graphical-vt` switches to it), if one exists on the seat.
    pub graphical_vt: Option<u32>,
}
fn logind_fields(path: &Path) -> Option<BTreeMap<String, String>> {
    let text = std::fs::read_to_string(path).ok()?;
    Some(text.lines().filter_map(|l| l.split_once('=')).map(|(k, v)| (k.to_owned(), v.trim().to_owned())).collect())
}
fn logind_id_ok(id: &str) -> bool {
    !id.is_empty() && id.len() <= 64 && id.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}
const GRAPHICAL: [&str; 3] = ["wayland", "x11", "mir"];
/// C-16 / INV-8: the seat view of `uid` from logind's runtime state (users/<uid>, sessions/<id>, seats/<seat>).
pub fn seat_view(state: &Path, uid: u32) -> SeatView {
    let foreign = |r: &str| SeatView { seat: Seat::Foreign(r.into()), graphical_vt: None };
    let Some(user) = logind_fields(&state.join("users").join(uid.to_string())) else { return foreign("no logind record for this user") };
    let mut ids: Vec<String> = user.get("DISPLAY").into_iter().cloned().collect();
    ids.extend(user.get("SESSIONS").map(|s| s.split_whitespace().map(str::to_owned).collect::<Vec<_>>()).unwrap_or_default());
    let sessions: Vec<(String, BTreeMap<String, String>)> = ids.iter().filter(|i| logind_id_ok(i)).take(32)
        .filter_map(|i| logind_fields(&state.join("sessions").join(i)).map(|f| (i.clone(), f))).collect();
    let seated = |f: &BTreeMap<String, String>| f.get("SEAT").is_some_and(|s| logind_id_ok(s));
    let Some(seat) = sessions.iter().find(|(_, f)| seated(f)).and_then(|(_, f)| f.get("SEAT").cloned()) else { return foreign("no seated session for this user") };
    let graphical_vt = sessions.iter()
        .find(|(_, f)| f.get("SEAT") == Some(&seat) && f.get("TYPE").is_some_and(|t| GRAPHICAL.contains(&t.as_str())) && f.get("UID").and_then(|u| u.parse().ok()) == Some(uid))
        .and_then(|(_, f)| f.get("VTNR")?.parse::<u32>().ok()).filter(|v| *v > 0);
    let Some(active) = logind_fields(&state.join("seats").join(&seat)).and_then(|f| f.get("ACTIVE").cloned()).filter(|a| logind_id_ok(a)) else {
        return SeatView { seat: Seat::Foreign(format!("no active session on {seat}")), graphical_vt };
    };
    let Some(f) = logind_fields(&state.join("sessions").join(&active)) else { return SeatView { seat: Seat::Foreign("active session unreadable".into()), graphical_vt } };
    if f.get("UID").and_then(|u| u.parse::<u32>().ok()) != Some(uid) || f.get("CLASS").is_some_and(|c| !["user", "user-early"].contains(&c.as_str())) {
        return SeatView { seat: Seat::Foreign(format!("another session is active on {seat}")), graphical_vt };
    }
    let seat = match f.get("TYPE").map(String::as_str) {
        Some("tty") => match f.get("VTNR").and_then(|v| v.parse::<u32>().ok()).filter(|v| (1..=63).contains(v)) {
            Some(vt) => {
                let tty = f.get("TTY").filter(|t| t.starts_with("tty") && t[3..].bytes().all(|b| b.is_ascii_digit()) && t.len() > 3)
                    .map_or_else(|| PathBuf::from(format!("/dev/tty{vt}")), |t| Path::new("/dev").join(t));
                Seat::Console { vt, tty }
            }
            None => Seat::Foreign("the active tty session has no VT".into()),
        },
        Some(t) if GRAPHICAL.contains(&t) => Seat::Graphical,
        _ => Seat::Foreign("the active session is neither graphical nor a VT".into()),
    };
    SeatView { seat, graphical_vt }
}
pub(crate) fn ipc(request: &str) -> Result<String, String> {
    let runtime = std::env::var("XDG_RUNTIME_DIR").map_err(|_| "XDG_RUNTIME_DIR missing")?;
    let sig = signature()?;
    if sig.contains('/') || sig.contains("..") {
        return Err("invalid compositor signature".into());
    }
    let mut stream = UnixStream::connect(
        Path::new(&runtime)
            .join("hypr")
            .join(sig)
            .join(".socket.sock"),
    )
    .map_err(|e| e.to_string())?;
    stream
        .set_read_timeout(Some(Duration::from_millis(150)))
        .map_err(|e| e.to_string())?;
    stream
        .set_write_timeout(Some(Duration::from_millis(150)))
        .map_err(|e| e.to_string())?;
    stream
        .write_all(request.as_bytes())
        .map_err(|e| e.to_string())?;
    let mut out = String::new();
    stream
        .take(1 << 20)
        .read_to_string(&mut out)
        .map_err(|e| e.to_string())?;
    Ok(out)
}
#[derive(Clone, Debug)]
pub struct ContextSnapshot {
    pub name: String, pub address: Option<String>, pub pid: Option<u64>,
    pub room_capture: bool, pub released: bool, sampled: Instant,
    pub class: Option<String>,
    /// C-09: the MiOS tmux client running under the focused terminal (or attached to the active VT), if any.
    pub tmux_client: Option<TmuxClient>,
    /// INV-8: why nothing may be routed now (another session owns the seat); None while this user's session is active.
    pub suspended: Option<String>,
    /// C-17: the VT of this user's graphical session (the console's TOGGLE_CHORD switches to it).
    pub graphical_vt: Option<u32>,
}
impl ContextSnapshot {
    fn new(name: &str) -> Self {
        Self { name: name.into(), address: None, pid: None, room_capture: false, released: false, sampled: Instant::now(), class: None, tmux_client: None, suspended: None, graphical_vt: None }
    }
}
/// A tmux client found under the focused window's process tree, by /proc alone (no spawn per focus change).
#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize)]
pub struct TmuxClient {
    pub pid: u32,
    /// The client's tty: the `-c target-client` name.
    pub tty: String,
    /// The server socket (TMUX_TMPDIR, -L or -S resolved), so a verb reaches exactly the server that client uses.
    pub socket: PathBuf,
}
fn proc_parent(stat: &str) -> Option<u32> {
    let rest = &stat[stat.rfind(')')? + 1..];
    rest.split_whitespace().nth(1)?.parse().ok()
}
/// C-09: the first tmux client below `pid` whose server socket name is one of `sockets`.
pub fn tmux_client_under(proc_root: &Path, pid: u32, sockets: &[String], uid: u32) -> Option<TmuxClient> {
    let mut children: BTreeMap<u32, Vec<u32>> = BTreeMap::new();
    for entry in std::fs::read_dir(proc_root).ok()?.filter_map(Result::ok) {
        let Some(child) = entry.file_name().to_str().and_then(|n| n.parse::<u32>().ok()) else { continue };
        if let Some(parent) = std::fs::read_to_string(entry.path().join("stat")).ok().as_deref().and_then(proc_parent) {
            children.entry(parent).or_default().push(child);
        }
    }
    let mut queue = std::collections::VecDeque::from([(pid, 0u8)]);
    let mut visited = 0;
    while let Some((p, depth)) = queue.pop_front() {
        visited += 1;
        if visited > 4096 {
            break;
        }
        if p != pid {
            if let Some(c) = tmux_client_at(proc_root, p, sockets, uid) {
                return Some(c);
            }
        }
        if depth < 8 {
            for c in children.get(&p).into_iter().flatten() {
                queue.push_back((*c, depth + 1));
            }
        }
    }
    None
}
fn tmux_client_at(proc_root: &Path, pid: u32, sockets: &[String], uid: u32) -> Option<TmuxClient> {
    let dir = proc_root.join(pid.to_string());
    let cmdline = std::fs::read(dir.join("cmdline")).ok()?;
    let args: Vec<String> = cmdline.split(|b| *b == 0).filter(|a| !a.is_empty()).map(|a| String::from_utf8_lossy(a).into_owned()).collect();
    if Path::new(args.first()?).file_name()?.to_str()? != "tmux" {
        return None;
    }
    let comm = std::fs::read_to_string(dir.join("comm")).unwrap_or_default();
    if comm.trim().ends_with("server") {
        return None;
    }
    let (mut name, mut path) = ("default".to_owned(), None);
    let mut i = 1;
    while i < args.len() {
        let a = &args[i];
        if !a.starts_with('-') || a == "--" {
            break;
        }
        for (flag, slot) in [("-L", 0), ("-S", 1)] {
            if let Some(v) = a.strip_prefix(flag) {
                let v = if v.is_empty() { i += 1; args.get(i).cloned().unwrap_or_default() } else { v.to_owned() };
                if slot == 0 { name = v } else { path = Some(PathBuf::from(v)) }
            }
        }
        if a == "-f" || a == "-c" || a == "-T" {
            i += 1;
        }
        i += 1;
    }
    let socket = match path {
        Some(p) => {
            name = p.file_name()?.to_str()?.to_owned();
            p
        }
        None => {
            let env = std::fs::read(dir.join("environ")).unwrap_or_default();
            let tmpdir = env.split(|b| *b == 0).find_map(|kv| kv.strip_prefix(b"TMUX_TMPDIR=")).map(|v| String::from_utf8_lossy(v).into_owned())
                .filter(|v| !v.is_empty()).unwrap_or_else(|| "/tmp".into());
            Path::new(&tmpdir).join(format!("tmux-{uid}")).join(&name)
        }
    };
    if !sockets.contains(&name) {
        return None;
    }
    use std::os::unix::fs::FileTypeExt;
    if !std::fs::metadata(&socket).ok()?.file_type().is_socket() {
        return None;
    }
    let tty = std::fs::read_link(dir.join("fd/0")).ok()?.to_str()?.to_owned();
    if !(tty.starts_with("/dev/pts/") || tty.starts_with("/dev/tty")) {
        return None;
    }
    Some(TmuxClient { pid, tty, socket })
}
fn snapshot(cfg:&GlobalConfig,v:&serde_json::Value) -> ContextSnapshot {
    let class=v.get("class").and_then(|v|v.as_str()).filter(|s|!s.is_empty());
    let room=v.get("room").and_then(|v|v.as_bool()).unwrap_or(false);
    let captured=v.get("captured").and_then(|v|v.as_bool()).unwrap_or(false);
    ContextSnapshot {name:cfg.classify_window(class,room,captured),
        address:v.get("address").and_then(|v|v.as_str()).filter(|s|!s.is_empty()).map(str::to_owned),
        pid:v.get("pid").and_then(|v|v.as_u64()).filter(|p|*p>0),
        room_capture:captured && (room || v.get("handler").and_then(|v|v.as_str())==Some("hypr3d")),
        released:v.get("released").and_then(|v|v.as_bool()).unwrap_or(false),sampled:Instant::now(),
        class:class.map(str::to_owned),tmux_client:None,suspended:None,graphical_vt:None}
}
/// C-09 on a VT: the MiOS tmux client attached to `tty` (directly, or below a process on that tty such as the console
/// bridge, whose child client runs on a pty). /proc only.
pub fn tmux_client_on_tty(proc_root: &Path, tty: &Path, sockets: &[String], uid: u32) -> Option<TmuxClient> {
    // Another user's /proc/<pid>/fd is unreadable, and only a listed server under this uid's socket dir counts.
    let mut on_tty: Vec<u32> = vec![];
    for entry in std::fs::read_dir(proc_root).ok()?.filter_map(Result::ok) {
        let Some(pid) = entry.file_name().to_str().and_then(|n| n.parse::<u32>().ok()) else { continue };
        if std::fs::read_link(entry.path().join("fd/0")).ok().as_deref() == Some(tty) {
            on_tty.push(pid);
        }
        if on_tty.len() > 256 {
            break;
        }
    }
    on_tty.sort_unstable();
    for pid in &on_tty {
        if let Some(c) = tmux_client_at(proc_root, *pid, sockets, uid) {
            return Some(c);
        }
    }
    on_tty.iter().find_map(|pid| tmux_client_under(proc_root, *pid, sockets, uid))
}
/// Whether this context's nav mapping chooses between `<name>_tmux` and `<name>_plain`, so discovery is worth a scan.
fn wants_tmux(cfg:&GlobalConfig,context:&str) -> bool {
    let nav=&cfg.controller.nav;
    nav.enabled && nav.contexts.get(context).is_some_and(|p| !nav.profiles.contains_key(p) && nav.profiles.contains_key(&format!("{p}_tmux")))
}
/// The capture predicate (INV-4), identical for every window kind: fullscreen==2 (when capture_fullscreen),
/// capture_tag or the room's input_owner capture a window, and release_tag cancels all three. When the generated
/// session exposes MIOS_INPUT_POLICY.captured (G1) that one predicate is used, so Lua and inputd cannot disagree.
pub fn context_lua(cfg: &GlobalConfig) -> String {
    let template=r#"local function q(s) return '"'..tostring(s or ''):gsub('[%z\1-\31\\"]',function(c) return string.format('\\u%04x',c:byte()) end)..'"' end
local w=hl.get_active_window(); local room=false; local p=hl.plugin and hl.plugin.hypr3d
if p and p.active then local ok,v=pcall(p.active); room=ok and v and true or false end
local function has(t) for _,tag in pairs(w and w.tags or {}) do if tostring(tag):gsub('%*$','')==t then return true end end return false end
local captured,released=false,false
if w then
 released=has(RELEASE_TAG)
 local okp,policy=pcall(function() return MIOS_INPUT_POLICY end)
 if okp and type(policy)=='table' and type(policy.captured)=='function' then local ok,v=pcall(policy.captured,w); captured=ok and v==true
 else
  if CAPTURE_FULLSCREEN and tonumber(w.fullscreen)==2 then captured=true end
  if has(CAPTURE_TAG) then captured=true end
  if p and p.input_owner then local ok,id=pcall(p.input_owner); if ok and id~=0 and tonumber(w.address)==id then captured=true end end
  if released then captured=false end
 end
end
error('MIOS_INPUT_CONTEXT:'..'{"class":'..q(w and w.class)..',"address":'..q(w and w.address)..',"pid":'..tostring(w and w.pid or 0)..',"fullscreen":'..tostring(w and w.fullscreen or 0)..',"handler":'..q(w and w.fullscreen_handler)..',"room":'..tostring(room)..',"captured":'..tostring(captured)..',"released":'..tostring(released)..'}',0)"#;
    template.replace("CAPTURE_FULLSCREEN",if cfg.controller.capture_fullscreen {"true"}else{"false"})
        .replace("CAPTURE_TAG",&serde_json::to_string(&cfg.controller.capture_tag).unwrap())
        .replace("RELEASE_TAG",&serde_json::to_string(&cfg.controller.release_tag).unwrap())
}
pub fn context(cfg: &GlobalConfig) -> Result<ContextSnapshot,String> {
    let reply=ipc(&format!("/eval {}",context_lua(cfg)))?;
    let json=reply.split_once("MIOS_INPUT_CONTEXT:").and_then(|(_,v)|v.lines().next()).ok_or("compositor context response missing")?;
    let value:serde_json::Value=serde_json::from_str(json).map_err(|e|format!("invalid native context: {e}"))?;
    Ok(snapshot(cfg,&value))
}
/// The ownership transaction for `input:window on|fullscreen|off` (C-02), the same for every window kind.
/// on/fullscreen: remove release_tag, add capture_tag; fullscreen also sets fullscreen and marks it with
/// capture_fullscreen_tag when the bridge set it. off: remove capture_tag, add release_tag, and unset a fullscreen only
/// when capture_fullscreen_tag says the bridge set it. In the room the plugin selects the aimed window and owns its
/// fullscreen. `expect` (the focused window address the router saw) guards a 2D capture against a focus race.
pub fn window_lua(mode: &str, cfg: &GlobalConfig, expect: Option<&str>) -> Result<String,String> {
    if !["on","off","fullscreen"].contains(&mode) {return Err("unsupported window ownership mode".into());}
    let s=|v:&str| serde_json::to_string(v).unwrap();
    let c=&cfg.controller;
    Ok(format!(r#"local p=hl.plugin and hl.plugin.hypr3d;local mode={mode};local CAP={cap};local REL={rel};local FS={fs};local EXPECT={expect}
local function has(w,t) for _,x in pairs(w.tags or {{}}) do if tostring(x):gsub('%*$','')==t then return true end end return false end
local function tag(w,t,on) if has(w,t)~=on then hl.dispatch(hl.dsp.window.tag({{window=w,tag=(on and '+' or '-')..t}})) end end
local function own(w,room)
 if mode=='off' then
  tag(w,CAP,false);tag(w,REL,true)
  if has(w,FS) then tag(w,FS,false);if not room and tonumber(w.fullscreen)~=0 then hl.dispatch(hl.dsp.window.fullscreen({{window=w,mode='fullscreen',action='unset'}})) end end
 else
  tag(w,REL,false);tag(w,CAP,true)
  if mode=='fullscreen' and not room and tonumber(w.fullscreen)~=2 then tag(w,FS,true);hl.dispatch(hl.dsp.window.fullscreen({{window=w,mode='fullscreen',action='set'}})) end
 end
end
local target=nil;local ok,room=false,false
if p and p.active then ok,room=pcall(p.active) end
if ok and room then
 assert(p.input_capture and p.input_owner,'room input ownership unavailable')
 local id=p.input_owner()
 assert(p.input_capture(mode),'no selected room window')
 if mode~='off' then id=p.input_owner() end
 for _,w in ipairs(hl.get_windows()) do if tonumber(w.address)==id then target=w;break end end
 if not target and mode=='off' then target=hl.get_active_window() end
 if target then own(target,true) end
else
 target=hl.get_active_window()
 if not target then error('MIOS_INPUT_WINDOW_FAIL: no focused window',0) end
 if EXPECT~='' and tostring(target.address)~=EXPECT then error('MIOS_INPUT_WINDOW_FAIL: controller focus changed; ownership unchanged',0) end
 own(target,false)
end
local okp,policy=pcall(function() return MIOS_INPUT_POLICY end)
if okp and type(policy)=='table' and type(policy.refresh)=='function' then pcall(policy.refresh) end
error('MIOS_INPUT_WINDOW_OK:'..tostring(target and target.address or '')..' '..tostring(target and target.class or ''),0)"#,
        mode=s(mode),cap=s(&c.capture_tag),rel=s(&c.release_tag),fs=s(&c.capture_fullscreen_tag),expect=s(expect.unwrap_or(""))))
}
/// Run one ownership transaction against the selected compositor; Ok((address, class)) only on the success marker.
pub fn window_transaction(mode: &str, cfg: &GlobalConfig, expect: Option<&str>) -> Result<(String,String),String> {
    let reply=ipc(&format!("/eval {}",window_lua(mode,cfg,expect)?))?;
    match reply.split_once("MIOS_INPUT_WINDOW_OK:") {
        Some((_,rest)) => {
            let line=rest.lines().next().unwrap_or("").trim();
            let (addr,class)=line.split_once(' ').unwrap_or((line,""));
            Ok((addr.to_owned(),class.trim().to_owned()))
        }
        None => Err(reply.trim().chars().take(400).collect()),
    }
}
/// C-32: the capture OSD text. The release chord is named from toggle_chord, so a remap never shows a stale label.
pub fn osd_message(mode: &str, class: &str, toggle_chord: &[String]) -> String {
    let chord=toggle_chord.iter().map(|b| match b.as_str() {"BTN_THUMBL"=>"L3".to_owned(),"BTN_THUMBR"=>"R3".to_owned(),
        o=>o.trim_start_matches("BTN_").to_owned()}).collect::<Vec<_>>().join("+");
    let app=if class.is_empty() {"the focused window"} else {class};
    if mode=="off" { format!("Controller -> MiOS. {app} released.") } else { format!("Controller -> {app}. Hold {chord} to release.") }
}
/// Run a short-lived helper with a hard deadline; its stdout (bounded) is the result.
fn run_bounded(argv: &[String], timeout: Duration) -> Result<String,String> {
    let (program,rest)=argv.split_first().ok_or("empty argv")?;
    // TMUX/TMUX_PANE would make the tmux CLI resolve "current" against the caller's own pane, never the target client.
    let mut child=Command::new(program).args(rest).env_remove("TMUX").env_remove("TMUX_PANE").stdin(Stdio::null()).stdout(Stdio::piped()).stderr(Stdio::null()).spawn().map_err(|e|e.to_string())?;
    let deadline=Instant::now()+timeout;
    loop {
        if let Some(status)=child.try_wait().map_err(|e|e.to_string())? {
            let mut out=String::new();
            if let Some(s)=child.stdout.take() { let _=s.take(1<<16).read_to_string(&mut out); }
            return if status.success() {Ok(out)} else {Err(format!("{program} exited {status}"))};
        }
        if Instant::now()>=deadline { let _=child.kill(); let _=child.wait(); return Err(format!("{program} timed out")); }
        std::thread::sleep(Duration::from_millis(5));
    }
}
/// C-30: argv of a tmux verb that needs no reply, against the exact server and client discovery found.
pub fn tmux_argv(cfg: &GlobalConfig, client: &TmuxClient, verb: &str) -> Result<Vec<String>,String> {
    crate::nav::validate_tmux(verb,&cfg.tmux_prefix_keys)?;
    let base=|rest:&[&str]| -> Vec<String> {
        ["/usr/bin/tmux","-S"].into_iter().map(str::to_owned).chain([client.socket.to_string_lossy().into_owned()])
            .chain(rest.iter().map(|s|(*s).to_owned())).collect()
    };
    let words:Vec<_>=verb.split_whitespace().collect();
    Ok(match words.as_slice() {
        ["key",k] => base(&["send-keys","-c",&client.tty,"-K",&cfg.tmux_prefix,k]),
        ["session-prev"] => base(&["switch-client","-c",&client.tty,"-p"]),
        ["session-next"] => base(&["switch-client","-c",&client.tty,"-n"]),
        // U1: prefix + [keybindings.tmux].menu_key opens the generated MiOS menu on exactly this client.
        ["menu"] => match &cfg.tmux_menu_key {
            Some(k) => base(&["send-keys","-c",&client.tty,"-K",&cfg.tmux_prefix,k]),
            None => return Err("tmux:menu needs [keybindings.tmux].menu_key (the generated MiOS menu)".into()),
        },
        ["focus",d] => base(&["display-message","-p","-c",&client.tty,"-t",&client.tty,&format!("#{{pane_at_{d}}}")]),
        ["page-up"] | ["page-down"] => base(&["display-message","-p","-c",&client.tty,"-t",&client.tty,"#{pane_id} #{pane_in_mode}"]),
        // R-21: the controller map in a popup on exactly this client; argv form, so tmux runs no shell.
        ["keymap"] => {
            let file=keymap_path().to_string_lossy().into_owned();
            let mut v=base(&["display-popup","-c",&client.tty,"-E","-w","90%","-h","90%","-T"," MiOS controller "]);
            v.extend(cfg.controller.console.keymap_argv.iter().map(|a| a.replace("{file}",&file)));
            v
        }
        _ => return Err(format!("tmux:{verb} has no runtime projection")),
    })
}
/// The rendered controller map the tmux:keymap popup pages (`mios-inputd map --text`), private to this user.
pub fn keymap_path() -> PathBuf {
    // SAFETY: getuid has no memory/ownership effects.
    let runtime=std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from).unwrap_or_else(|| PathBuf::from(format!("/run/user/{}",unsafe{libc::getuid()})));
    runtime.join("mios-inputd-keymap.txt")
}
fn write_private(path: &Path, text: &str) -> Result<(),String> {
    use std::os::unix::fs::OpenOptionsExt;
    let tmp=path.with_extension(format!("tmp-{}",std::process::id()));
    let _=std::fs::remove_file(&tmp);
    let mut f=std::fs::OpenOptions::new().write(true).create_new(true).mode(0o600).custom_flags(libc::O_NOFOLLOW|libc::O_CLOEXEC).open(&tmp).map_err(|e|e.to_string())?;
    let r=f.write_all(text.as_bytes()).and_then(|_| std::fs::rename(&tmp,path));
    if r.is_err() { let _=std::fs::remove_file(&tmp); }
    r.map_err(|e|e.to_string())
}
/// Run a tmux verb (C-30), including the two-step ones: `focus` falls through to the compositor at the pane edge (on
/// the desktop; on a VT it stops there), `keymap` renders the map file first.
fn run_tmux(cfg: &GlobalConfig, client: &TmuxClient, verb: &str, console: bool) -> Result<Vec<String>,String> {
    let t=Duration::from_millis(1000);
    let argv=tmux_argv(cfg,client,verb)?;
    let words:Vec<_>=verb.split_whitespace().collect();
    let mut ran=vec![argv[3..].join(" ")];
    let sock=client.socket.to_string_lossy().into_owned();
    let tmux=|rest:&[&str]| -> Vec<String> { ["/usr/bin/tmux","-S",&sock].into_iter().map(str::to_owned).chain(rest.iter().map(|s|(*s).to_owned())).collect() };
    match words.as_slice() {
        ["keymap"] => {
            write_private(&keymap_path(),&cfg.controller.nav.text(cfg.controller.capture_hold_ms))?;
            run_bounded(&argv,t)?;
        }
        ["focus",d] => {
            let edge=run_bounded(&argv,t)?.trim()=="1";
            if edge && console {
                ran.push("edge: a VT has no compositor focus".into());
            } else if edge {
                let lua=command_for(&format!("hypr:movefocus {d}"))?.remove(2);
                let reply=ipc(&format!("/eval {lua}"))?;
                ran.push(format!("hypr:movefocus {d} -> {}",reply.trim()));
            } else {
                let k=match *d {"left"=>"h","down"=>"j","up"=>"k",_=>"l"};
                let step=tmux(&["send-keys","-c",&client.tty,"-K",&cfg.tmux_prefix,k]);
                run_bounded(&step,t)?;
                ran.push(step[3..].join(" "));
            }
        }
        ["page-up"] | ["page-down"] => {
            let out=run_bounded(&argv,t)?;
            let mut it=out.split_whitespace();
            let (pane,mode)=(it.next().unwrap_or("").to_owned(),it.next()==Some("1"));
            if !pane.starts_with('%') { return Err("tmux did not report the client's pane".into()); }
            let step=if words[0]=="page-up" { tmux(&["copy-mode","-eu","-t",&pane]) } else if mode { tmux(&["send-keys","-t",&pane,"-X","page-down"]) } else { return Ok(ran) };
            run_bounded(&step,t)?;
            ran.push(step[3..].join(" "));
        }
        _ => { run_bounded(&argv,t)?; }
    }
    Ok(ran)
}
/// Jobs that must not block the forwarding loop: tmux verbs, the capture OSD, and (fixture tier with --live-context)
/// the compositor projections of key: and hypr: into the followed (nested) compositor.
enum Job {
    Tmux { verb: String, client: TmuxClient, console: bool },
    Lua { action: String, lua: String },
}
struct ActionWorker {
    jobs: Option<std::sync::mpsc::SyncSender<Job>>,
    worker: Option<std::thread::JoinHandle<()>>,
}
impl ActionWorker {
    fn spawn(policy: std::sync::Arc<std::sync::Mutex<GlobalConfig>>) -> Result<Self,String> {
        let (jobs,inbox)=std::sync::mpsc::sync_channel::<Job>(16);
        let worker=std::thread::Builder::new().name("mios-actions".into()).spawn(move || {
            while let Ok(job)=inbox.recv() {
                let cfg=match policy.lock() { Ok(c)=>c.clone(), Err(_)=>break };
                match job {
                    Job::Tmux{verb,client,console} => {
                        let r=run_tmux(&cfg,&client,&verb,console);
                        eprintln!("{}",serde_json::json!({"tmux":verb,"client":client.tty,"ok":r.is_ok(),"ran":r.as_ref().ok(),"error":r.as_ref().err()}));
                    }
                    Job::Lua{action,lua} => {
                        let r=ipc(&format!("/eval {lua}"));
                        eprintln!("{}",serde_json::json!({"projected":action,"ok":r.as_ref().is_ok_and(|v|v.trim()=="ok"),"reply":r.as_ref().map(|v|v.trim().chars().take(200).collect::<String>()).unwrap_or_else(|e|e.clone())}));
                    }
                }
            }
        }).map_err(|e|e.to_string())?;
        Ok(Self{jobs:Some(jobs),worker:Some(worker)})
    }
    fn submit(&self, job: Job) -> bool {
        self.jobs.as_ref().is_some_and(|j| j.try_send(job).is_ok())
    }
}
impl Drop for ActionWorker {
    fn drop(&mut self) {
        self.jobs=None;
        if let Some(w)=self.worker.take() { let _=w.join(); }
    }
}
/// The compositor projection of one key: stroke (a nested session's focused window): down then up.
fn key_lua(chord: &crate::nav::KeyChord, window: &str) -> String {
    let (sym,mods)=crate::nav::keysym(chord);
    let q=|v:&str| serde_json::to_string(v).unwrap();
    let w=q(&format!("address:{window}"));
    format!("hl.dispatch(hl.dsp.send_key_state({{mods={m},key={k},state='down',window={w}}}))\nhl.dispatch(hl.dsp.send_key_state({{mods={m},key={k},state='up',window={w}}}))",
        m=q(&mods),k=q(&sym))
}
fn event_socket() -> Option<UnixStream> {
    let rt=std::env::var_os("XDG_RUNTIME_DIR")?;
    let sig=std::env::var("HYPRLAND_INSTANCE_SIGNATURE").ok()?;
    if sig.contains('/') || sig.contains("..") {return None;}
    let stream=UnixStream::connect(Path::new(&rt).join("hypr").join(sig).join(".socket2.sock")).ok()?;
    stream.set_nonblocking(true).ok()?;Some(stream)
}
fn wait_context_event(stream:&mut Option<UnixStream>,millis:u64) {
    use std::os::fd::AsRawFd;
    if stream.is_none() {*stream=event_socket();}
    let Some(s)=stream.as_mut() else {std::thread::sleep(Duration::from_millis(millis));return;};
    let mut poll=libc::pollfd {fd:s.as_raw_fd(),events:libc::POLLIN,revents:0};
    // SAFETY: one initialized pollfd references a live owned stream.
    let ready=unsafe{libc::poll(&mut poll,1,millis.min(i32::MAX as u64) as i32)};
    if ready>0 {
        let mut bytes=[0u8;8192];
        match s.read(&mut bytes) {Ok(0)=>*stream=None,Err(e) if e.kind()!=std::io::ErrorKind::WouldBlock=>*stream=None,_=>{}}
    }
    std::thread::sleep(Duration::from_millis(5));
}
fn policy_fingerprint(global:&GlobalConfig,legacy:&LegacyConfig) -> Result<Vec<u8>,String> {
    serde_json::to_vec(&(global,legacy,&global.resolved_actions)).map_err(|e|e.to_string())
}
/// Action strings are policy data, never shell source. Compositor arguments are
/// encoded as Lua string literals; unsupported verbs fail rather than eval shell.
pub fn command_for(action: &str) -> Result<Vec<String>, String> {
    let (namespace, verb) = action.split_once(':').ok_or("missing action namespace")?;
    let words: Vec<_> = verb.split_whitespace().collect();
    match namespace {
        "hypr" => {
            let (name, arg) = words.split_first().ok_or("empty compositor action")?;
            let arg = arg.join(" ");
            let q = serde_json::to_string(&arg).unwrap();
            let lua = match *name {
                "workspace" => format!("hl.dispatch(hl.dsp.focus({{workspace={q}}}))"),
                "movetoworkspace" => {
                    format!("hl.dispatch(hl.dsp.window.move({{workspace={q},follow=true}}))")
                }
                "fullscreen" => {
                    "hl.dispatch(hl.dsp.window.fullscreen({mode='fullscreen',action='toggle'}))"
                        .into()
                }
                "killactive" => "hl.dispatch(hl.dsp.window.close())".into(),
                "togglefloating" => "hl.dispatch(hl.dsp.window.float({action='toggle'}))".into(),
                "movefocus" => format!("hl.dispatch(hl.dsp.focus({{direction={q}}}))"),
                name if name.starts_with("hypr3d:") => {
                    let name = &name[7..];
                    if !crate::config::ROOM_VERBS.contains(&name) {
                        return Err(format!("unsupported room action {name}"));
                    }
                    // A bare verb takes the plugin's default argument (cancel/recenter have none; sprint toggles).
                    if ["fullscreen", "jump", "cancel", "sprint", "recenter"].contains(&name) && arg.is_empty() {
                        format!("hl.plugin.hypr3d.{name}()")
                    } else {
                        format!("hl.plugin.hypr3d.{name}({q})")
                    }
                }
                _ => return Err(format!("unsupported compositor action {verb}")),
            };
            Ok(vec!["/usr/bin/hyprctl".into(), "eval".into(), lua])
        }
        "mios" => {
            // C-17: a native projection (the uinput keyboard's Ctrl+Alt+F<VTNr>), resolved by the bridge at fire time.
            if verb == "graphical-vt" {
                return Ok(vec![]);
            }
            if words.len() == 1 && ["toggle_vm_passthrough", "vm-capture-toggle", "vm-fullscreen"].contains(&words[0]) {
                return Ok(vec!["/usr/libexec/mios/mios-inputd".into(), "vm-action".into(), words[0].into()]);
            }
            if words.first() == Some(&"hyprland-3d-toggle") {
                return Ok(vec![
                    "/usr/bin/mios-gen".into(),
                    "hyprland-3d-toggle".into(),
                ]);
            }
            if words.first() == Some(&"shaderglass")
                && words.len() == 2
                && ["toggle", "cycle"].contains(&words[1])
            {
                return Ok(vec!["/usr/bin/mios-shaderglass".into(), words[1].into()]);
            }
            if words.first() == Some(&"launch-browser") {
                return Ok(vec!["/usr/bin/mios-browser".into()]);
            }
            if words.first() == Some(&"placement-toggle") {
                return Ok(vec![
                    "/usr/bin/miosd".into(),
                    "placement".into(),
                    "toggle".into(),
                ]);
            }
            Err(format!("MiOS action has no installed dispatcher: {verb}"))
        }
        "quickshell" => {
            let target = match verb {
                "launcher" | "drawer-toggle" => "drawer",
                "search-toggle" => "search",
                "notifications-toggle" => "notifications",
                "keymap-toggle" => "keymap",
                _ => return Err(format!("shell action has no installed IPC handler: {verb}")),
            };
            Ok([
                "/usr/bin/quickshell",
                "ipc",
                "--path",
                "/usr/share/mios/quickshell/Config.qml",
                "--any-display",
                "call",
                target,
                "toggle",
            ]
            .into_iter()
            .map(str::to_owned)
            .collect())
        }
        "exec" if verb == "mios-terminal" => {
            Err("terminal launch must resolve its SSOT desktop action".into())
        }
        "exec" => Err(
            "exec actions require a registered mios: verb; raw command strings are not executed"
                .into(),
        ),
        "tmux" | "readline" => {
            Err("application-local action is owned by its native terminal projection".into())
        }
        "input" => Ok(vec![]),
        _ => Err(format!("unknown action namespace {namespace}")),
    }
}
/// Ownership transactions run off the forwarding loop; their result reverts a pending capture/release at once when the
/// compositor refused it (C-03), instead of leaving the app or the room holding the pad.
type OwnershipRequest = (u64, String, Option<String>, Vec<String>);
struct OwnershipWorker {
    requests: Option<std::sync::mpsc::Sender<OwnershipRequest>>,
    results: std::sync::mpsc::Receiver<(u64, String, Result<String, String>)>,
    last: u64,
    worker: Option<std::thread::JoinHandle<()>>,
}
impl OwnershipWorker {
    fn spawn(policy: std::sync::Arc<std::sync::Mutex<GlobalConfig>>) -> Result<Self, String> {
        let (requests, inbox) = std::sync::mpsc::channel::<OwnershipRequest>();
        let (outbox, results) = std::sync::mpsc::channel();
        let worker = std::thread::Builder::new().name("mios-ownership".into()).spawn(move || {
            while let Ok((seq, mode, expect, toggle)) = inbox.recv() {
                let cfg = policy.lock().map(|c| c.clone());
                let result = match &cfg {
                    Ok(cfg) => window_transaction(&mode, cfg, expect.as_deref()),
                    Err(_) => Err("ownership policy lock failed".into()),
                };
                // C-32: one notice per confirmed capture or release, never for a refused transaction or a focus change.
                if let (Ok(cfg), Ok((_, class))) = (&cfg, &result) {
                    if cfg.controller.capture_osd {
                        let message = osd_message(&mode, class, &toggle);
                        let argv: Vec<String> = cfg.controller.capture_osd_argv.iter().map(|a| a.replace("{message}", &message)).collect();
                        let r = run_bounded(&argv, Duration::from_millis(2000));
                        eprintln!("{}", serde_json::json!({"osd": message, "mode": mode, "ok": r.is_ok(), "error": r.err()}));
                    }
                }
                if outbox.send((seq, mode, result.map(|(a, _)| a))).is_err() { break; }
            }
        }).map_err(|e| e.to_string())?;
        Ok(Self { requests: Some(requests), results, last: 0, worker: Some(worker) })
    }
    fn submit(&mut self, mode: &str, expect: Option<String>, toggle: Vec<String>) -> Result<(), String> {
        self.last += 1;
        self.requests.as_ref().ok_or("ownership worker stopped")?
            .send((self.last, mode.into(), expect, toggle)).map_err(|_| "ownership worker stopped".to_string())
    }
}
impl Drop for OwnershipWorker {
    fn drop(&mut self) {
        self.requests = None;
        if let Some(w) = self.worker.take() { let _ = w.join(); }
    }
}
struct Sinks {
    pointer: Option<VirtualDevice>,
    keyboard: Option<VirtualDevice>,
    children: Vec<(Instant, Child)>,
    ownership: Option<OwnershipWorker>,
    actions: Option<ActionWorker>,
    bridge: bool,
    execute_actions: bool,
    /// Fixture tier with --live-context: project nav output (key:, hypr:, tmux:) into the followed compositor
    /// instead of the uinput keyboard; quickshell:, exec: and mios: stay printed.
    project: bool,
    /// Fixture tier with --console-sink-fd: console-context key: strokes as Linux console bytes into this fd.
    console_sink: Option<std::fs::File>,
}
impl Sinks {
    /// tmux verbs run for the physical bridge and for a fixture that projects somewhere (a nested compositor or a VT pty).
    fn runs_tmux(&self) -> bool {
        self.execute_actions || self.project || self.console_sink.is_some()
    }
    fn stroke(&mut self, chord: &crate::nav::KeyChord) -> Result<(), String> {
        if self.keyboard.is_none() {
            self.keyboard = Some(VirtualDevice::keyboard().map_err(|e| e.to_string())?);
        }
        self.keyboard.as_mut().unwrap().write(&crate::nav::stroke(chord)).map_err(|e| e.to_string())
    }
}
/// C-17: the stroke that switches to this user's graphical VT (Ctrl+Alt+F<VTNr>; the kernel keymap maps it to
/// Console_<n>). None without a graphical session, or on a VT that no F-key reaches.
pub fn graphical_vt_chord(vt: Option<u32>) -> Option<String> {
    vt.filter(|v| (1..=12).contains(v)).map(|v| format!("CTRL+ALT+F{v}"))
}
/// Returns the ownership change this output requested (`input:window on|fullscreen` = true, `off` = false), which the
/// caller applies to every other controller of the session.
fn emit(output: Output, device: &mut RunningDevice, sinks: &mut Sinks, focus: &ContextSnapshot) -> Result<Option<bool>, String> {
    if sinks.bridge {
        if let Some(virtual_pad) = &mut device.output {
            virtual_pad.write(&output.app).map_err(|e| e.to_string())?;
        }
        if sinks.execute_actions && !output.pointer.is_empty() {
            if sinks.pointer.is_none() {
                sinks.pointer = Some(VirtualDevice::pointer().map_err(|e| e.to_string())?);
            }
            sinks.pointer
                .as_mut()
                .unwrap()
                .write(&output.pointer)
                .map_err(|e| e.to_string())?;
        }
    }
    let mut ownership = None;
    for action in output.actions {
        let mut submitted = false;
        if let Some(mode) = action.strip_prefix("input:window ") {
            ownership = Some(mode != "off");
            if let Some(worker) = &mut sinks.ownership {
                worker.submit(mode, focus.address.clone(), device.router.legacy_toggle_chord())?;
                submitted = true;
            }
        } else if let Some(chord) = action.strip_prefix("key:") {
            // C-07: an atomic stroke on the mios-vctl-keyboard virtual keyboard (US layout), or, on the fixture tier
            // following a nested compositor, the same keysym sent to its focused window.
            // On a VT the kernel console reads the same uinput keyboard (C-16); the fixture tier writes the bytes the
            // console keymap would deliver into the pty that stands in for the VT.
            let chord = crate::nav::parse_key_chord(chord)?;
            if sinks.execute_actions {
                sinks.stroke(&chord)?;
                submitted = true;
            } else if let (true, Some(sink)) = (device.router.console(), &mut sinks.console_sink) {
                if let Some(bytes) = crate::nav::vt_bytes(&chord) {
                    sink.write_all(&bytes).map_err(|e| e.to_string())?;
                    submitted = true;
                }
            } else if sinks.project {
                if let (Some(worker), Some(window)) = (&sinks.actions, &focus.address) {
                    submitted = worker.submit(Job::Lua { action: action.clone(), lua: key_lua(&chord, window) });
                }
            }
        } else if let Some(verb) = action.strip_prefix("tmux:") {
            // C-30: only against the tmux client discovery found; without one the *_plain profile is active.
            if sinks.runs_tmux() {
                if let (Some(worker), Some(client)) = (&sinks.actions, &focus.tmux_client) {
                    submitted = worker.submit(Job::Tmux { verb: verb.into(), client: client.clone(), console: device.router.console() });
                }
            }
        } else if action == "mios:graphical-vt" {
            // C-17: no graphical session (or a VT beyond F12) sends nothing.
            let chord = graphical_vt_chord(focus.graphical_vt);
            if let Some(c) = &chord {
                let c = crate::nav::parse_key_chord(c)?;
                if sinks.execute_actions {
                    sinks.stroke(&c)?;
                    submitted = true;
                } else if sinks.console_sink.is_some() {
                    submitted = true; // a VT switch: the console keymap delivers no bytes
                }
            }
            println!("{}", serde_json::json!({"vt_switch": chord, "graphical_vt": focus.graphical_vt, "submitted": submitted}));
        } else if sinks.project && action.starts_with("hypr:") {
            if let Some(worker) = &sinks.actions {
                let lua = configured_command(&action, device.router.policy())?.remove(2);
                submitted = worker.submit(Job::Lua { action: action.clone(), lua });
            }
        } else if sinks.execute_actions {
            let args = configured_command(&action, device.router.policy())?;
            if !args.is_empty() {
                // Cap/reap subprocesses; a stalled compositor cannot fill the host
                // with an unbounded dispatch queue while a controller is held.
                if sinks.children.len() >= 8 {
                    return Err("action dispatch queue full".into());
                }
                let child = Command::new(&args[0])
                    .args(&args[1..])
                    .env("MIOS_INPUT_CONTEXT", &device.router.context)
                    .env("MIOS_INPUT_WINDOW", focus.address.as_deref().unwrap_or(""))
                    .env("MIOS_INPUT_WINDOW_PID", focus.pid.unwrap_or(0).to_string())
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::inherit())
                    .spawn()
                    .map_err(|e| e.to_string())?;
                sinks.children.push((Instant::now(), child));
                submitted = true;
            }
        }
        println!(
            "{}",
            serde_json::json!({"device":device.device.path,"context":device.router.context,"action":action,"submitted":submitted,"executed":false})
        );
    }
    Ok(ownership)
}
fn atomic_packet(path: &Path, pad: &PadState) -> Result<(), String> {
    use std::os::unix::fs::OpenOptionsExt;
    // Unique O_EXCL staging avoids following a hostile shared-/tmp symlink.
    let tmp = path.with_extension(format!("inputd-{}", std::process::id()));
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(&tmp)
        .map_err(|e| e.to_string())?;
    let result = f
        .write_all(&pad.packet(linux::monotonic_ns()))
        .and_then(|_| std::fs::rename(&tmp, path));
    if result.is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
    result.map_err(|e| e.to_string())
}
// evdev close() waits for a kernel grace period. Probing rejected devices in the
// forwarding thread paused physical input for hundreds of milliseconds per scan.
type DeviceCandidates=std::sync::mpsc::Receiver<Result<Device,String>>;
type Discovery=(DeviceCandidates,std::thread::JoinHandle<()>);
fn discovery_worker(
    explicit:Option<PathBuf>,fixture_only:bool,
    policy:std::sync::Arc<std::sync::Mutex<GlobalConfig>>,
    known:std::sync::Arc<std::sync::Mutex<BTreeSet<PathBuf>>>,
    stop:std::sync::Arc<AtomicBool>,
)->Result<Discovery,String> {
    use std::os::unix::fs::MetadataExt;
    let (sender,receiver)=std::sync::mpsc::sync_channel(16);
    let worker=std::thread::Builder::new().name("mios-devices".into()).spawn(move || {
        let mut rejected=BTreeMap::new();let mut sent=BTreeMap::new();let mut previous=BTreeSet::new();
        while !stop.load(Ordering::Relaxed) {
            let current=known.lock().unwrap().clone();
            for path in previous.difference(&current) {sent.remove(path);}
            previous=current.clone();
            let paths=if let Some(path)=&explicit {Ok(vec![path.clone()])}else{linux::discover().map_err(|e|e.to_string())};
            let paths=match paths {Ok(v)=>v,Err(e)=>{let _=sender.try_send(Err(e));break}};
            rejected.retain(|path,_|paths.contains(path));sent.retain(|path,_|paths.contains(path));
            for path in paths {
                if stop.load(Ordering::Relaxed) {break;}
                if current.contains(&path) {continue;}
                let identity=std::fs::metadata(&path).ok().map(|m|(m.rdev(),m.ino()));
                if identity.is_some_and(|id|rejected.get(&path)==Some(&id)||sent.get(&path)==Some(&id)) {continue;}
                match Device::open(&path,fixture_only) {
                    Ok(device)=> {if sender.try_send(Ok(device)).is_ok() {if let Some(id)=identity {sent.insert(path,id);}}}
                    Err(e)=> {if explicit.is_some() {let _=sender.try_send(Err(e.to_string()));return;}
                        if let Some(id)=identity {rejected.insert(path,id);}}
                }
            }
            let delay=Duration::from_millis(policy.lock().unwrap().controller.scan_ms);
            let until=Instant::now()+delay;
            while !stop.load(Ordering::Relaxed)&&Instant::now()<until {
                std::thread::sleep(until.saturating_duration_since(Instant::now()).min(Duration::from_millis(20)));
            }
        }
    }).map_err(|e|e.to_string())?;
    Ok((receiver,worker))
}
fn install_stop_handlers() {
    // SAFETY: signal handlers only store to a lock-free atomic boolean. File
    // descriptors are dropped in normal control flow; SIGKILL closes them in-kernel.
    unsafe {
        libc::signal(
            libc::SIGTERM,
            stop_signal as *const () as libc::sighandler_t,
        );
        libc::signal(libc::SIGINT, stop_signal as *const () as libc::sighandler_t);
    }
}
/// Why the physical bridge cannot own pads in this session, or None when it can.
/// A user manager without a seat (SSH-only, a container) or without /dev/uinput access must idle, not crash-loop.
pub fn seat_unavailable() -> Option<String> {
    use std::os::unix::fs::OpenOptionsExt;
    if let Err(e) = std::fs::OpenOptions::new().write(true).custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC).open("/dev/uinput") {
        return Some(format!("no /dev/uinput access: {e}"));
    }
    if let Err(e) = std::fs::read_dir("/dev/input") {
        return Some(format!("no /dev/input access: {e}"));
    }
    // SAFETY: getuid has no memory/ownership effects.
    let uid = unsafe { libc::getuid() };
    seat_from_logind_state(Path::new("/run/systemd"), uid)
}
/// logind's own runtime state (users/<uid>, sessions/<id>, seats/<seat>): read as files, so an idle serve never
/// spawns a process per re-check (no loginctl, no interpreter, no D-Bus round trip). I3: any seated session of this
/// user starts the bridge, a VT login included; an SSH-only login idles. Another session being active on the seat does
/// not stop serve: the bridge then holds no grab and routes nothing until this user's session returns (INV-8).
pub(crate) fn seat_from_logind_state(state: &Path, uid: u32) -> Option<String> {
    let user = state.join("users").join(uid.to_string());
    if let Err(e) = std::fs::metadata(&user) {
        return Some(format!("logind state unavailable ({}): {e}", user.display()));
    }
    match seat_view(state, uid).seat {
        Seat::Foreign(r) if r.starts_with("no seated session") || r.starts_with("no logind record") => Some(r),
        _ => None,
    }
}
/// The user-unit entry point (C-05). Disabled by SSOT: exit 0. No seat or no device access: idle until it appears
/// (SIGTERM exits 0) and never start a second pad owner. A bridge error: exit non-zero, so systemd restarts it
/// within the unit's start limit. There is no fallback process that could drive a game and the room at once.
pub fn serve(mut global: GlobalConfig, mut legacy: LegacyConfig, root: Option<PathBuf>) -> Result<(), String> {
    let disabled = |g: &GlobalConfig| !g.enabled || !g.controller.enabled || !g.controller.exclusive_grab;
    if disabled(&global) {
        eprintln!("{}", serde_json::json!({"serve":"disabled","reason":"SSOT disables the controller bridge","exit":0}));
        return Ok(());
    }
    // SAFETY: geteuid has no memory/ownership effects.
    if unsafe { libc::geteuid() } == 0 {
        return Err("real input bridge must run as the active desktop user".into());
    }
    install_stop_handlers();
    STOP.store(false, Ordering::Relaxed);
    let mut stamp = config_fingerprint(root.as_deref());
    let mut reported: Option<String> = None;
    loop {
        if STOP.load(Ordering::Relaxed) {
            return Ok(());
        }
        let now = config_fingerprint(root.as_deref());
        if now != stamp {
            stamp = now;
            match GlobalConfig::load(root.as_deref()) {
                Ok((g, l)) => { global = g; legacy = l; }
                Err(e) => eprintln!("input policy reload rejected; keeping validated policy: {e}"),
            }
            if disabled(&global) {
                eprintln!("{}", serde_json::json!({"serve":"disabled","reason":"SSOT disabled the controller bridge","exit":0}));
                return Ok(());
            }
        }
        match seat_unavailable() {
            None => break,
            Some(reason) => {
                if reported.as_deref() != Some(reason.as_str()) {
                    eprintln!("{}", serde_json::json!({"serve":"idle","reason":reason}));
                    reported = Some(reason);
                }
                let until = Instant::now() + Duration::from_millis(global.controller.scan_ms);
                while !STOP.load(Ordering::Relaxed) && Instant::now() < until {
                    std::thread::sleep(Duration::from_millis(20));
                }
            }
        }
    }
    if reported.is_some() {
        eprintln!("{}", serde_json::json!({"serve":"bridge","reason":"seat and device access available"}));
    }
    let pad_path = global.controller.legacy_pad_state_path.clone();
    run(global, legacy, Options { root, bridge: true, fixture_only: false, device: None, context: None, duration: None, pad_path, live_context: false, write_pad: true,
        logind_state: Some(PathBuf::from("/run/systemd")), console_tty: None, console_sink_fd: None, rediscover: true })
}
pub fn run(mut global: GlobalConfig, mut legacy: LegacyConfig, opt: Options) -> Result<(), String> {
    if opt.bridge && !opt.fixture_only {
        // SAFETY: geteuid has no memory/ownership effects.
        if unsafe { libc::geteuid() } == 0 {
            return Err("real input bridge must run as the active desktop user".into());
        }
        if !global.controller.exclusive_grab {
            return Err("exclusive_grab=false: physical capture is not enabled by SSOT".into());
        }
        let missing = unsupported_actions(&global);
        if !missing.is_empty() {
            return Err(format!(
                "physical bridge has unresolved actions: {}",
                missing.join(", ")
            ));
        }
        if legacy_owner_running() {
            return Err(
                "legacy controller daemon is still running; refuse duplicate ownership".into(),
            );
        }
    }
    if opt.fixture_only && opt.device.is_none() {
        return Err("fixture mode requires one explicit owned device".into());
    }
    if opt.live_context && !opt.fixture_only {
        return Err("--live-context is a fixture-tier option (the physical bridge always follows its compositor)".into());
    }
    if !opt.fixture_only && (opt.console_tty.is_some() || opt.console_sink_fd.is_some()) {
        return Err("--console-tty and --console-sink-fd are fixture-tier options".into());
    }
    if opt.fixture_only && opt.write_pad && opt.pad_path == global.controller.legacy_pad_state_path {
        return Err("a fixture never writes the live room packet; pass a private --pad-path".into());
    }
    let runtime = std::env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .ok_or("XDG_RUNTIME_DIR missing")?;
    let lockname = if opt.fixture_only {
        "mios-inputd-fixture.lock"
    } else {
        "mios-inputd.lock"
    };
    let _lock = linux::singleton(&runtime.join(lockname)).map_err(|e| e.to_string())?;
    install_stop_handlers();
    STOP.store(false, Ordering::Relaxed);
    let mut devices: BTreeMap<PathBuf, RunningDevice> = BTreeMap::new();
    let follow = opt.context.is_none() && (!opt.fixture_only || opt.live_context || opt.logind_state.is_some());
    let mut selected: Option<PathBuf> = None;
    let mut seed = PadState::default();
    if opt.bridge && opt.write_pad {
        if let Ok(bytes) = std::fs::read(&opt.pad_path) {
            seed.seed_sequences(&bytes);
        }
    }
    let start = Instant::now();
    let mut scan = start - Duration::from_secs(2);
    let mut last_tick = start;
    let mut last_reconcile = start;
    let mut focus = ContextSnapshot::new(opt.context.as_deref().unwrap_or("app-capture"));
    // INV-8: with a seat view, nothing is grabbed or routed before the first view says this user's session is active.
    if opt.logind_state.is_some() && follow { focus.suspended = Some("seat not read yet".into()); }
    let console_sink = match opt.console_sink_fd {
        // SAFETY: the fixture caller passed this inherited descriptor for exactly this use; it is owned from here on.
        Some(fd) if fd > 2 => Some(unsafe { <std::fs::File as std::os::fd::FromRawFd>::from_raw_fd(fd) }),
        Some(_) => return Err("--console-sink-fd must be an inherited descriptor above 2".into()),
        None => None,
    };
    let shared_focus=std::sync::Arc::new(std::sync::Mutex::new(focus.clone()));
    let shared_policy=std::sync::Arc::new(std::sync::Mutex::new(global.clone()));
    let mut sinks = Sinks {
        pointer: None,
        children: vec![],
        ownership: if follow && opt.bridge { Some(OwnershipWorker::spawn(shared_policy.clone())?) } else { None },
        actions: if opt.bridge && (!opt.fixture_only || opt.live_context || console_sink.is_some()) { Some(ActionWorker::spawn(shared_policy.clone())?) } else { None },
        keyboard: None,
        bridge: opt.bridge,
        execute_actions: opt.bridge && !opt.fixture_only,
        project: opt.bridge && opt.fixture_only && opt.live_context,
        console_sink,
    };
    let context_stop=std::sync::Arc::new(AtomicBool::new(false));
    let context_worker=if follow {
        let cache=shared_focus.clone(); let policy=shared_policy.clone(); let stop=context_stop.clone();
        let (logind, console_tty, rediscover_on) = (opt.logind_state.clone(), opt.console_tty.clone(), opt.rediscover);
        Some(std::thread::spawn(move || {
            let mut events=event_socket();
            // SAFETY: getuid has no memory/ownership effects.
            let uid=unsafe{libc::getuid()};
            let mut tmux_cache:Option<(u64,Instant,Option<TmuxClient>)>=None;
            let mut vt_cache:Option<(PathBuf,Instant,Option<TmuxClient>)>=None;
            let mut last_rediscover=Instant::now()-Duration::from_secs(1);
            let mut compositor_lost:Option<Instant>=None;
            let runtime=std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from);
            let mut reported:Option<String>=None;
            while !stop.load(Ordering::Relaxed) {
                let cfg=policy.lock().unwrap().clone();
                let view=logind.as_deref().map(|state| seat_view(state,uid));
                let graphical_vt=view.as_ref().and_then(|v| v.graphical_vt);
                // C-16: the console context (a VT) when this user's tty session is active, or when the graphical
                // session's compositor is gone for good (no live instance at all); INV-8: suspended when another
                // session is active. The tty path may be a test pty on the fixture tier.
                let mut console:Option<PathBuf>=None;
                match view.as_ref().map(|v| &v.seat) {
                    Some(Seat::Foreign(reason)) => {
                        let mut snap=ContextSnapshot::new("console");
                        snap.suspended=Some(reason.clone()); snap.graphical_vt=graphical_vt;
                        if reported.as_deref()!=Some(reason.as_str()) { eprintln!("{}",serde_json::json!({"seat":"foreign","reason":reason})); reported=Some(reason.clone()); }
                        *cache.lock().unwrap()=snap;
                        std::thread::sleep(Duration::from_millis(cfg.controller.context_poll_ms.max(20)));
                        continue;
                    }
                    Some(Seat::Console{tty,..}) => console=Some(console_tty.clone().unwrap_or_else(|| tty.clone())),
                    _ => {}
                }
                let result=if console.is_some() { Err("console".to_string()) } else { context(&cfg) };
                if console.is_none() && result.is_err() && rediscover_on && last_rediscover.elapsed()>=Duration::from_millis(250) {
                    last_rediscover=Instant::now();
                    if let Some(rt)=&runtime {
                        let current=signature().ok();
                        match rediscover(rt,Path::new("/proc"),current.as_deref()) {
                            Some(sig) => {
                                compositor_lost=None;
                                if current.as_deref()!=Some(sig.as_str()) {
                                    eprintln!("{}",serde_json::json!({"compositor":"rediscovered","signature":sig,"previous":current}));
                                    if let Ok(mut g)=SIGNATURE.lock() { *g=Some(sig); }
                                    events=None;
                                }
                            }
                            None => { compositor_lost.get_or_insert_with(Instant::now); }
                        }
                    }
                }
                if console.is_none() && result.is_err() && view.is_some()
                    && compositor_lost.is_some_and(|t| t.elapsed()>=Duration::from_millis(cfg.controller.context_stale_ms)) {
                    if let Some(vt)=graphical_vt { console=Some(console_tty.clone().unwrap_or_else(|| PathBuf::from(format!("/dev/tty{vt}")))); }
                }
                if let Some(tty)=console {
                    let mut snap=ContextSnapshot::new("console");
                    snap.graphical_vt=graphical_vt;
                    let fresh=vt_cache.as_ref().is_some_and(|(t,at,_)| *t==tty && at.elapsed()<Duration::from_millis(1000));
                    if !fresh {
                        let found=if wants_tmux(&cfg,"console") { tmux_client_on_tty(Path::new("/proc"),&tty,&cfg.controller.nav.tmux_sockets,uid) } else { None };
                        vt_cache=Some((tty.clone(),Instant::now(),found));
                    }
                    snap.tmux_client=vt_cache.as_ref().and_then(|(_,_,c)|c.clone());
                    if reported.as_deref()!=Some("console") { eprintln!("{}",serde_json::json!({"seat":"console","tty":tty})); reported=Some("console".into()); }
                    *cache.lock().unwrap()=snap;
                    std::thread::sleep(Duration::from_millis(cfg.controller.context_poll_ms.max(20)));
                    continue;
                }
                if result.is_ok() { compositor_lost=None; }
                if view.is_some() && result.is_ok() && reported.as_deref()!=Some("graphical") { eprintln!("{}",serde_json::json!({"seat":"graphical"})); reported=Some("graphical".into()); }
                // A failed IPC read never invents terminal focus or resets held input.
                if let Ok(mut snapshot)=result {
                    snapshot.graphical_vt=graphical_vt;
                    // C-09: /proc only, at most once a second per focused terminal (tmux may start inside it later).
                    if let (Some(pid),true)=(snapshot.pid,wants_tmux(&cfg,&snapshot.name)) {
                        let fresh=tmux_cache.as_ref().is_some_and(|(p,t,_)| *p==pid && t.elapsed()<Duration::from_millis(1000));
                        if !fresh {
                            let found=u32::try_from(pid).ok().and_then(|p| tmux_client_under(Path::new("/proc"),p,&cfg.controller.nav.tmux_sockets,uid));
                            tmux_cache=Some((pid,Instant::now(),found));
                        }
                        snapshot.tmux_client=tmux_cache.as_ref().and_then(|(_,_,c)|c.clone());
                    }
                    *cache.lock().unwrap()=snapshot;
                }
                wait_context_event(&mut events,cfg.controller.context_poll_ms);
            }
        }))
    } else {None};
    let known_devices=std::sync::Arc::new(std::sync::Mutex::new(BTreeSet::new()));
    let (new_devices,discovery)=discovery_worker(opt.device.clone(),opt.fixture_only,shared_policy.clone(),known_devices.clone(),context_stop.clone())?;
    let mut ctx=focus.name.clone();
    let mut config_stamp = config_fingerprint(opt.root.as_deref());
    let result = (|| -> Result<(), String> {
        while !STOP.load(Ordering::Relaxed) && opt.duration.is_none_or(|d| start.elapsed() < d) {
            if scan.elapsed() >= Duration::from_millis(global.controller.scan_ms) {
                scan = Instant::now();
                let stamp = config_fingerprint(opt.root.as_deref());
                if stamp != config_stamp {
                    config_stamp = stamp;
                    match GlobalConfig::load(opt.root.as_deref()) {
                        Ok((next, next_legacy)) => {
                            if opt.bridge
                                && (!next.enabled
                                    || !next.controller.enabled
                                    || (!opt.fixture_only && !next.controller.exclusive_grab))
                            {
                                eprintln!("{}", serde_json::json!({"serve":"disabled","reason":"SSOT disabled the controller bridge","exit":0}));
                                break;
                            }
                            if policy_fingerprint(&global,&legacy)? != policy_fingerprint(&next,&next_legacy)? {
                            for dev in devices.values_mut() {
                                let cursor=dev.router.cursor;
                                let out = dev.router.reset();
                                emit(out, dev, &mut sinks, &focus)?;
                                let seq = dev.router.pad.sequences;
                                dev.router = Router::new(
                                    next.clone(),
                                    next_legacy.clone(),
                                    dev.device.snapshot().map_err(|e| e.to_string())?.1,
                                );
                                dev.router.pad.sequences = seq;
                                dev.router.analog_triggers = dev.device.analog_triggers();
                                dev.router.tmux = focus.tmux_client.is_some();
                                dev.router.set_context(&ctx);
                                let (keys, axes) =
                                    dev.device.snapshot().map_err(|e| e.to_string())?;
                                dev.router.resync(keys, axes);
                                dev.router.cursor=cursor;
                            }
                            *shared_policy.lock().map_err(|_| "context policy lock failed")?=next.clone();
                            global = next;
                            legacy = next_legacy;
                            eprintln!("input policy reloaded from layered SSOT");
                            }
                        }
                        Err(e) => {
                            eprintln!("input policy reload rejected; keeping validated policy: {e}")
                        }
                    }
                }
            }
            // Only ready, validated handles cross this channel. No evdev probes or
            // rejected-node closes occur on the forwarding loop.
            while let Ok(candidate)=new_devices.try_recv() {
                    let mut device=candidate?;
                    let path=device.path.clone();
                    let mut router =
                        Router::new(global.clone(), legacy.clone(), device.axes.clone());
                    router.pad.sequences = seed.sequences;
                    router.analog_triggers = device.analog_triggers();
                    router.tmux = focus.tmux_client.is_some();
                    router.set_context(&ctx);
                    if let Ok((keys, axes)) = device.snapshot() {
                        router.resync(keys, axes);
                    }
                    // A pad that joins while a capture is pending follows the session's ownership at once.
                    if let Some(owned) = devices.values().find_map(|d| d.router.ownership()) {
                        router.adopt_ownership(owned);
                    }
                    let suspended = focus.suspended.is_some();
                    let output = if opt.bridge {
                        if !suspended {
                            device
                                .grab()
                                .map_err(|e| format!("{}: cannot own input: {e}", path.display()))?;
                        }
                        Some(VirtualDevice::clone_gamepad(&device).map_err(|e| e.to_string())?)
                    } else {
                        None
                    };
                    eprintln!(
                        "{}",
                        serde_json::json!({"mode":if opt.bridge{"bridge"}else{"observe"},"device":path,"name":device.name})
                    );
                    known_devices.lock().unwrap().insert(path.clone());
                    devices.insert(
                        path,
                        RunningDevice {
                            device,
                            output,
                            router,
                            dropped: false,
                            suspended,
                        },
                    );
            }
            if let Ok(snapshot)=shared_focus.try_lock() { focus=snapshot.clone(); }
            if follow && focus.sampled.elapsed()>Duration::from_millis(global.controller.context_stale_ms) {
                focus.name="app-capture".into(); focus.room_capture=false;
            }
            ctx=focus.name.clone();
            // Ownership is per session: a capture/release requested by one pad applies to every pad (INV-7).
            let mut adopt: Vec<(PathBuf, bool)> = vec![];
            let paths: Vec<_> = devices.keys().cloned().collect();
            for path in paths {
                let dev = devices.get_mut(&path).unwrap();
                // INV-8: another session (or the greeter) owns the seat: release the grab so its session gets the pad,
                // neutralize every output, and route nothing until this user's session is active again.
                match (&focus.suspended, dev.suspended) {
                    (Some(reason), false) => {
                        let out = dev.router.reset();
                        emit(out, dev, &mut sinks, &focus)?;
                        dev.device.release();
                        dev.suspended = true;
                        eprintln!("{}", serde_json::json!({"seat":"released","device":path,"reason":reason}));
                    }
                    (None, true) => {
                        if opt.bridge {
                            dev.device.grab().map_err(|e| format!("{}: cannot own input: {e}", path.display()))?;
                        }
                        let (keys, axes) = dev.device.snapshot().map_err(|e| e.to_string())?;
                        let out = dev.router.resync(keys, axes);
                        emit(out, dev, &mut sinks, &focus)?;
                        dev.suspended = false;
                        eprintln!("{}", serde_json::json!({"seat":"owned","device":path}));
                    }
                    _ => {}
                }
                if dev.suspended {
                    if dev.device.read_events().is_err() {
                        eprintln!("input disconnected {}", path.display());
                        devices.remove(&path);
                        known_devices.lock().unwrap().remove(&path);
                        if selected.as_ref() == Some(&path) { selected = None; }
                    }
                    continue;
                }
                let target=focus.address.clone().zip(focus.pid);
                let mut transition = dev.router.set_tmux(focus.tmux_client.is_some());
                transition.extend(dev.router.set_focus(&ctx,target,focus.room_capture));
                emit(transition, dev, &mut sinks, &focus)?;
                match dev.device.read_events() {
                    Ok(events) => {
                        if events.iter().any(|e| {
                            e.kind == crate::router::EV_KEY || e.kind == crate::router::EV_ABS
                        }) {
                            selected = Some(path.clone());
                        }
                        for event in events {
                            if event.kind == EV_SYN && event.code == SYN_DROPPED {
                                dev.dropped = true;
                            }
                            if dev.dropped && event.kind == EV_SYN && event.code == SYN_REPORT {
                                let (keys, axes) =
                                    dev.device.snapshot().map_err(|e| e.to_string())?;
                                let out = dev.router.resync(keys, axes);
                                dev.dropped = false;
                                emit(out, dev, &mut sinks, &focus)?;
                            } else {
                                let out = dev
                                    .router
                                    .event(event, (linux::monotonic_ns() / 1_000_000) as u64);
                                if let Some(owned) = emit(out, dev, &mut sinks, &focus)? { adopt.push((path.clone(), owned)); }
                            }
                        }
                    }
                    Err(e) => {
                        eprintln!("input disconnected {}: {e}", path.display());
                        let out = dev.router.reset();
                        emit(out, dev, &mut sinks, &focus)?;
                        devices.remove(&path);
                        known_devices.lock().unwrap().remove(&path);
                        if selected.as_ref() == Some(&path) {
                            selected = None;
                        }
                    }
                }
            }
            let now = Instant::now();
            let dt = now.duration_since(last_tick).as_secs_f64().min(0.05);
            last_tick = now;
            let reconcile_due = now.duration_since(last_reconcile) >= Duration::from_millis(global.controller.context_poll_ms);
            if reconcile_due { last_reconcile = now; }
            for (path, dev) in devices.iter_mut().filter(|(_, d)| !d.suspended) {
                if reconcile_due && !dev.dropped {
                    if let Ok(held) = dev.device.held_keys() {
                        let out = dev.router.reconcile(&held, (linux::monotonic_ns()/1_000_000) as u64);
                        if let Some(owned) = emit(out, dev, &mut sinks, &focus)? { adopt.push((path.clone(), owned)); }
                    }
                }
                let out = dev.router.tick(dt);
                if let Some(owned) = emit(out, dev, &mut sinks, &focus)? { adopt.push((path.clone(), owned)); }
            }
            for (source, owned) in adopt {
                for (path, dev) in devices.iter_mut() {
                    if *path != source {
                        let out = dev.router.adopt_ownership(owned);
                        emit(out, dev, &mut sinks, &focus)?;
                    }
                }
            }
            let mut failed = vec![];
            if let Some(worker) = &sinks.ownership {
                while let Ok((seq, mode, result)) = worker.results.try_recv() {
                    match result {
                        Ok(address) => eprintln!("{}", serde_json::json!({"ownership":mode,"window":address,"ok":true})),
                        Err(error) => {
                            eprintln!("{}", serde_json::json!({"ownership":mode,"ok":false,"error":error,"reverted":seq==worker.last}));
                            // Only the latest request decides; an older failure must not undo a newer capture.
                            if seq == worker.last { failed.push(mode); }
                        }
                    }
                }
            }
            if !failed.is_empty() {
                for dev in devices.values_mut() {
                    let out = dev.router.ownership_failed();
                    emit(out, dev, &mut sinks, &focus)?;
                }
            }
            if opt.bridge && opt.write_pad {
                let pad = selected
                    .as_ref()
                    .and_then(|p| devices.get(p))
                    .map(|d| d.router.pad.clone())
                    .unwrap_or_else(|| {
                        let mut p = seed.clone();
                        p.neutralize();
                        p
                    });
                seed = pad.clone();
                atomic_packet(&opt.pad_path, &pad)?;
                // All controllers start future counters from the shared stream;
                // switching devices cannot rewind/replay an old event sequence.
                for dev in devices.values_mut() {
                    dev.router.pad.sequences = seed.sequences;
                }
            }
            let mut i = 0;
            while i < sinks.children.len() {
                let expired = sinks.children[i].0.elapsed() > Duration::from_secs(2);
                if expired {
                    let _ = sinks.children[i].1.kill();
                }
                if expired
                    || sinks.children[i]
                        .1
                        .try_wait()
                        .map_err(|e| e.to_string())?
                        .is_some()
                {
                    let (_, mut child) = sinks.children.swap_remove(i);
                    let _ = child.wait();
                } else {
                    i += 1;
                }
            }
            std::thread::sleep(Duration::from_secs_f64(
                1.0 / f64::from(global.controller.pointer_hz),
            ));
        }
        Ok(())
    })();
    for dev in devices.values_mut() {
        let out = dev.router.reset();
        let _ = emit(out, dev, &mut sinks, &focus);
        dev.device.release();
    }
    for (_, mut child) in std::mem::take(&mut sinks.children) {
        let _ = child.kill();
        let _ = child.wait();
    }
    if opt.bridge && opt.write_pad {
        seed.neutralize();
        let _ = atomic_packet(&opt.pad_path, &seed);
    }
    context_stop.store(true, Ordering::Relaxed);
    if let Some(worker)=context_worker { let _=worker.join(); }
    let _=discovery.join();
    drop(sinks);
    result
}
fn legacy_owner_running() -> bool {
    let uid = unsafe { libc::geteuid() };
    std::fs::read_dir("/proc")
        .into_iter()
        .flatten()
        .filter_map(Result::ok)
        .any(|entry| {
            use std::os::unix::fs::MetadataExt;
            if entry.metadata().is_ok_and(|m| m.uid() != uid) {
                return false;
            }
            std::fs::read(entry.path().join("cmdline")).is_ok_and(|b| {
                b.split(|c| *c == 0)
                    .any(|s| s == b"/usr/libexec/mios/mios-gamepad-hypr3d")
            })
        })
}

fn config_fingerprint(root: Option<&Path>) -> Vec<(PathBuf, u64, Option<std::time::SystemTime>)> {
    mios_resolver::layers::resolve_layer_paths(root)
        .into_iter()
        .map(|p| {
            let m = std::fs::metadata(&p).ok();
            let len = m.as_ref().map_or(0, |m| m.len());
            let modified = m.and_then(|m| m.modified().ok());
            (p, len, modified)
        })
        .collect()
}

pub fn configured_command(action: &str, global: &GlobalConfig) -> Result<Vec<String>, String> {
    if let Some(mode)=action.strip_prefix("input:window ") {
        return Ok(vec!["/usr/bin/hyprctl".into(),"eval".into(),window_lua(mode,global,None)?]);
    }
    if global.planned_actions.iter().any(|a|a==action) { return Err(format!("action is planned, not implemented: {action}")); }
    global
        .resolved_actions
        .get(action)
        .cloned()
        .map(Ok)
        .unwrap_or_else(|| command_for(action))
}
pub fn unsupported_actions(global: &GlobalConfig) -> Vec<String> {
    let probe = TmuxClient { pid: 0, tty: "/dev/pts/0".into(), socket: PathBuf::from("/tmp/probe") };
    let nav = global.controller.nav.actions();
    let room: Vec<String> = global
        .controller
        .room
        .values()
        .flat_map(|v| {
            let (tap, hold) = v.parts();
            std::iter::once(tap.to_owned()).chain(hold.map(str::to_owned))
        })
        .filter(|a| a != "none")
        .collect();
    global
        .controller
        .system_layer
        .values()
        .chain(
            global
                .bindings
                .iter()
                .filter(|b| b.scope == "host" && b.chord.starts_with("BTN_"))
                .map(|b| &b.action),
        )
        .chain(nav.iter())
        .chain(room.iter())
        .filter(|a| match a.split_once(':').map_or("", |(n, _)| n) {
            _ if a.as_str() == "none" => false,
            "key" => crate::nav::parse_key_chord(&a[4..]).is_err(),
            "pointer" => false,
            "tmux" => tmux_argv(global, &probe, &a[5..]).is_err(),
            _ => configured_command(a, global).is_err(),
        })
        .cloned()
        .collect()
}

/// Query once, then validate identity again in the compositor transaction.
/// Sending the native escape is observable; the remote client's capture state is not.
pub fn vm_action(action: &str, global: &GlobalConfig) -> Result<(), String> {
    let window: serde_json::Value = serde_json::from_str(&ipc("j/activewindow")?).map_err(|e| e.to_string())?;
    let address=window.get("address").and_then(|v| v.as_str()).ok_or("no focused VM address")?;
    let pid=window.get("pid").and_then(|v| v.as_u64()).ok_or("no focused VM process")?;
    let class=window.get("class").and_then(|v| v.as_str()).ok_or("no focused VM class")?;
    if let Ok(expected)=std::env::var("MIOS_INPUT_WINDOW") {
        let expected_pid=std::env::var("MIOS_INPUT_WINDOW_PID").ok().and_then(|v|v.parse::<u64>().ok());
        if expected != address || expected_pid != Some(pid) { return Err("controller focus changed; no VM input sent".into()); }
    }
    let lua=global.vm.lua(action,address,pid,class)?;
    let reply=ipc(&format!("/eval {lua}"))?;
    if !reply.contains("MIOS_VM_SENT") { return Err(reply); }
    println!("{}",serde_json::json!({"action":action,"address":address,"native_command_sent":true,"client_capture_state":"unknown"}));
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn native_context_captures_arbitrary_apps_and_keeps_an_unfocused_room() {
        let (cfg,_)=crate::config::tests::fixture();
        let v=serde_json::json!({"class":"org.any.Flatpak","address":"0xabc","pid":42,"room":false,"captured":true,"handler":"hypr3d"});
        let snap=snapshot(&cfg,&v);assert_eq!(snap.name,"app-capture");assert!(snap.room_capture);
        let empty=serde_json::json!({"room":true,"captured":false});assert_eq!(snapshot(&cfg,&empty).name,"desktop-3d");
    }
    #[test]
    fn current_room_passthrough_uses_native_fullscreen_state() {
        let (cfg,_)=crate::config::tests::fixture();
        let mut v=serde_json::json!({"class":"org.any.Flatpak","address":"0xabc","pid":42,"room":true,"fullscreen":2,"captured":true,"handler":"default"});
        assert!(snapshot(&cfg,&v).room_capture,"the installed room uses the compositor default fullscreen handler");
        v["room"]=serde_json::json!(false);assert!(!snapshot(&cfg,&v).room_capture,"outside the room a fullscreen capture is a 2D capture; the held chord releases it to the desktop");
        v["room"]=serde_json::json!(true);v["fullscreen"]=serde_json::json!(0);assert!(snapshot(&cfg,&v).room_capture,"windowed room capture must support the held release chord");
    }
    #[test]
    fn unrelated_ssot_changes_do_not_reset_controller_policy() {
        let mut doc:toml::Value=include_str!("../../../../usr/share/mios/mios.toml").parse().unwrap();
        let (cfg,legacy)=GlobalConfig::from_value(&doc).unwrap();let before=policy_fingerprint(&cfg,&legacy).unwrap();
        doc["theme"]["font"]["size"]=toml::Value::Integer(18);
        let (cfg,legacy)=GlobalConfig::from_value(&doc).unwrap();assert_eq!(policy_fingerprint(&cfg,&legacy).unwrap(),before);
    }
    #[test]
    fn release_tag_is_reported_and_class_never_captures() {
        let (cfg,_)=crate::config::tests::fixture();
        let v=serde_json::json!({"class":"steam_app_570","address":"0xabc","pid":42,"room":false,"captured":false,"released":true});
        let snap=snapshot(&cfg,&v);assert_eq!(snap.name,"game");assert!(snap.released);
        assert!(!crate::router::Router::owns_controller(&snap.name),"an uncaptured game window is an ordinary window");
        let lua=context_lua(&cfg);
        assert!(lua.contains("\"mios-input-released\"") && lua.contains("if released then captured=false end"));
        assert!(lua.contains("policy.captured"),"the generated MIOS_INPUT_POLICY.captured predicate is preferred when present");
    }
    #[test]
    fn seat_check_reads_logind_state_files_without_spawning() {
        let t=std::env::temp_dir().join(format!("mios-inputd-seat-{}",std::process::id()));
        let _=std::fs::remove_dir_all(&t);
        std::fs::create_dir_all(t.join("users")).unwrap();std::fs::create_dir_all(t.join("sessions")).unwrap();
        assert!(seat_from_logind_state(&t,1000).unwrap().contains("logind state unavailable"),"no user record: idle");
        std::fs::write(t.join("users/1000"),"STATE=active\nSESSIONS=4\n").unwrap();
        std::fs::write(t.join("sessions/4"),"UID=1000\nTYPE=tty\nREMOTE=1\nTTY=pts/3\n").unwrap();
        assert_eq!(seat_from_logind_state(&t,1000).as_deref(),Some("no seated session for this user"),"SSH-only login: idle");
        std::fs::write(t.join("users/1000"),"DISPLAY=../x\n").unwrap();
        assert_eq!(seat_from_logind_state(&t,1000).as_deref(),Some("no seated session for this user"),"a path-like id is rejected");
        std::fs::write(t.join("users/1000"),"STATE=active\nDISPLAY=3\n").unwrap();
        std::fs::write(t.join("sessions/3"),"TYPE=wayland\nSEAT=\n").unwrap();
        assert_eq!(seat_from_logind_state(&t,1000).as_deref(),Some("no seated session for this user"));
        std::fs::write(t.join("sessions/3"),"TYPE=wayland\nSEAT=seat0\n").unwrap();
        assert_eq!(seat_from_logind_state(&t,1000),None,"a seated graphical session reaches the bridge");
        std::fs::write(t.join("users/1000"),"STATE=active\nSESSIONS=5\n").unwrap();
        std::fs::write(t.join("sessions/5"),"UID=1000\nTYPE=tty\nSEAT=seat0\nVTNR=3\nTTY=tty3\n").unwrap();
        assert_eq!(seat_from_logind_state(&t,1000),None,"I3: a VT login reaches the bridge too");
        std::fs::remove_dir_all(&t).unwrap();
    }
    #[test]
    fn window_transaction_tags_release_and_unsets_only_bridge_fullscreen() {
        let (cfg,_)=crate::config::tests::fixture();
        let off=window_lua("off",&cfg,None).unwrap();
        assert!(off.contains("tag(w,CAP,false);tag(w,REL,true)"));
        assert!(off.contains("if has(w,FS) then tag(w,FS,false)") && off.contains("action='unset'"));
        let full=window_lua("fullscreen",&cfg,Some("0xabc")).unwrap();
        assert!(full.contains("tag(w,REL,false);tag(w,CAP,true)") && full.contains("tag(w,FS,true)"));
        assert!(full.contains("local EXPECT=\"0xabc\""));
        assert!(off.contains("pcall(policy.refresh)"),"the keyboard policy re-reads the tags in the same transaction");
        assert!(window_lua("toggle",&cfg,None).is_err());
        assert_eq!(configured_command("input:window off",&cfg).unwrap()[2],off);
    }
    fn fake_proc(t: &Path, pid: u32, ppid: u32, comm: &str, argv: &[&str], env: &str, tty: &str) {
        let d = t.join(pid.to_string());
        std::fs::create_dir_all(d.join("fd")).unwrap();
        std::fs::write(d.join("stat"), format!("{pid} ({comm}) S {ppid} 1 1 0")).unwrap();
        std::fs::write(d.join("comm"), format!("{comm}\n")).unwrap();
        std::fs::write(d.join("cmdline"), argv.iter().map(|a| format!("{a}\0")).collect::<String>()).unwrap();
        std::fs::write(d.join("environ"), env).unwrap();
        if !tty.is_empty() { std::os::unix::fs::symlink(tty, d.join("fd/0")).unwrap(); }
    }
    #[test]
    fn tmux_client_discovery_reads_proc_and_honours_the_socket_list() {
        let t = std::env::temp_dir().join(format!("mios-inputd-proc-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&t);
        let sockdir = t.join("sock/tmux-1000");
        std::fs::create_dir_all(&sockdir).unwrap();
        let _human = std::os::unix::net::UnixListener::bind(sockdir.join("mios-human")).unwrap();
        let _other = std::os::unix::net::UnixListener::bind(sockdir.join("scratch")).unwrap();
        let tmpdir = format!("TMUX_TMPDIR={}\0", t.join("sock").display());
        let proc_root = t.join("proc");
        fake_proc(&proc_root, 100, 1, "alacritty", &["alacritty"], "", "");
        fake_proc(&proc_root, 101, 100, "mios-terminal", &["/bin/bash", "/usr/libexec/mios/mios-terminal"], "", "/dev/pts/7");
        fake_proc(&proc_root, 102, 101, "tmux: client", &["tmux", "-L", "mios-human", "attach", "-t", "=mios"], &tmpdir, "/dev/pts/7");
        fake_proc(&proc_root, 200, 1, "foot", &["foot"], "", "");
        fake_proc(&proc_root, 201, 200, "tmux: client", &["tmux", "-Lscratch", "new"], &tmpdir, "/dev/pts/8");
        fake_proc(&proc_root, 300, 1, "kitty", &["kitty"], "", "");
        fake_proc(&proc_root, 301, 300, "bash", &["bash"], "", "/dev/pts/9");
        let socks = vec!["mios-human".to_owned(), "mios-nested".to_owned()];
        let c = tmux_client_under(&proc_root, 100, &socks, 1000).expect("the mios-human client under the terminal");
        assert_eq!((c.pid, c.tty.as_str(), c.socket.clone()), (102, "/dev/pts/7", sockdir.join("mios-human")));
        assert!(tmux_client_under(&proc_root, 200, &socks, 1000).is_none(), "a server outside tmux_sockets selects the plain profile");
        assert!(tmux_client_under(&proc_root, 300, &socks, 1000).is_none(), "a bare shell has no client");
        assert!(tmux_client_under(&proc_root, 102, &socks, 1000).is_none(), "the focused pid itself is the terminal, not a child");
        std::fs::remove_dir_all(&t).unwrap();
    }
    #[test]
    fn tmux_verbs_target_the_discovered_client_and_validate_keys() {
        let (cfg, _) = crate::config::tests::fixture_nav();
        let c = TmuxClient { pid: 1, tty: "/dev/pts/7".into(), socket: "/run/t/mios-human".into() };
        assert_eq!(tmux_argv(&cfg, &c, "key p").unwrap(), ["/usr/bin/tmux", "-S", "/run/t/mios-human", "send-keys", "-c", "/dev/pts/7", "-K", "C-b", "p"]);
        assert_eq!(tmux_argv(&cfg, &c, "focus left").unwrap()[3..], ["display-message", "-p", "-c", "/dev/pts/7", "-t", "/dev/pts/7", "#{pane_at_left}"]);
        assert_eq!(tmux_argv(&cfg, &c, "session-next").unwrap()[3..], ["switch-client", "-c", "/dev/pts/7", "-n"]);
        assert!(tmux_argv(&cfg, &c, "key r").unwrap_err().contains("prefix-table"), "r is not bound in the SSOT prefix table");
        for k in ["e", "*", "`", "F", "Space", "Enter"] { assert!(tmux_argv(&cfg, &c, &format!("key {k}")).is_ok(), "{k}: a U1 launcher/float/menu/accept prefix key"); }
        assert_eq!(tmux_argv(&cfg, &c, "menu").unwrap()[3..], ["send-keys", "-c", "/dev/pts/7", "-K", "C-b", "Space"], "U1's menu_key");
        let mut no_menu = cfg.clone(); no_menu.tmux_menu_key = None;
        assert!(tmux_argv(&no_menu, &c, "menu").is_err(), "without the U1 menu key there is no menu projection");
        let popup = tmux_argv(&cfg, &c, "keymap").unwrap();
        assert_eq!(popup[3..10], ["display-popup", "-c", "/dev/pts/7", "-E", "-w", "90%", "-h"], "I3: the popup on exactly this client");
        assert_eq!(popup[popup.len() - 3..], ["/usr/bin/less".to_owned(), "-R".to_owned(), keymap_path().to_string_lossy().into_owned()], "argv form: tmux runs no shell");
        assert!(unsupported_actions(&cfg).is_empty(), "every vendor nav action resolves: {:?}", unsupported_actions(&cfg));
    }
    fn logind(t: &Path, files: &[(&str, &str)]) {
        let _ = std::fs::remove_dir_all(t);
        for d in ["users", "sessions", "seats"] { std::fs::create_dir_all(t.join(d)).unwrap(); }
        for (p, body) in files { std::fs::write(t.join(p), body).unwrap(); }
    }
    #[test]
    fn seat_view_selects_console_graphical_or_foreign() {
        let t = std::env::temp_dir().join(format!("mios-inputd-seatview-{}", std::process::id()));
        let user = ("users/1000", "DISPLAY=3\nSESSIONS=3 7 9\n");
        let gfx = ("sessions/3", "UID=1000\nTYPE=wayland\nCLASS=user\nSEAT=seat0\nTTY=tty1\nVTNR=1\n");
        let vt = ("sessions/7", "UID=1000\nTYPE=tty\nCLASS=user\nSEAT=seat0\nTTY=tty3\nVTNR=3\n");
        let ssh = ("sessions/9", "UID=1000\nTYPE=tty\nCLASS=user\nREMOTE=1\nTTY=pts/4\n");
        logind(&t, &[user, gfx, vt, ssh, ("seats/seat0", "ACTIVE=3\n")]);
        assert_eq!(seat_view(&t, 1000), SeatView { seat: Seat::Graphical, graphical_vt: Some(1) });
        std::fs::write(t.join("seats/seat0"), "ACTIVE=7\n").unwrap();
        assert_eq!(seat_view(&t, 1000), SeatView { seat: Seat::Console { vt: 3, tty: "/dev/tty3".into() }, graphical_vt: Some(1) });
        // INV-8: the greeter or another user owns the seat.
        std::fs::write(t.join("sessions/8"), "UID=980\nTYPE=wayland\nCLASS=greeter\nSEAT=seat0\nVTNR=1\n").unwrap();
        std::fs::write(t.join("seats/seat0"), "ACTIVE=8\n").unwrap();
        assert!(matches!(seat_view(&t, 1000).seat, Seat::Foreign(r) if r.contains("another session")));
        std::fs::write(t.join("sessions/8"), "UID=1000\nTYPE=wayland\nCLASS=lock-screen\nSEAT=seat0\n").unwrap();
        assert!(matches!(seat_view(&t, 1000).seat, Seat::Foreign(_)), "a lock screen is not this user's input target");
        std::fs::write(t.join("seats/seat0"), "ACTIVE=\n").unwrap();
        assert!(matches!(seat_view(&t, 1000).seat, Seat::Foreign(r) if r.contains("no active session")));
        // A VT-only login has no graphical VT to switch to (C-17 negative).
        logind(&t, &[("users/1000", "SESSIONS=7\n"), vt, ("seats/seat0", "ACTIVE=7\n")]);
        assert_eq!(seat_view(&t, 1000).graphical_vt, None);
        assert_eq!(graphical_vt_chord(None), None);
        assert_eq!(graphical_vt_chord(Some(2)).as_deref(), Some("CTRL+ALT+F2"));
        assert_eq!(graphical_vt_chord(Some(13)), None, "no F-key reaches VT 13");
        // SSH only: nothing seated.
        logind(&t, &[("users/1000", "SESSIONS=9\n"), ssh]);
        assert!(matches!(seat_view(&t, 1000).seat, Seat::Foreign(r) if r.contains("no seated session")));
        std::fs::remove_dir_all(&t).unwrap();
    }
    #[test]
    fn rediscovery_follows_a_restarted_compositor_and_skips_nested_ones() {
        let t = std::env::temp_dir().join(format!("mios-inputd-rediscover-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&t);
        let (rt, proc_root) = (t.join("run"), t.join("proc"));
        let inst = |sig: &str, pid: u32| {
            let d = rt.join("hypr").join(sig);
            std::fs::create_dir_all(&d).unwrap();
            std::fs::write(d.join("hyprland.lock"), format!("{pid}\nwayland-1\n")).unwrap();
            std::os::unix::net::UnixListener::bind(d.join(".socket.sock")).unwrap()
        };
        let stat = |pid: u32, ppid: u32, start: u64| {
            std::fs::create_dir_all(proc_root.join(pid.to_string())).unwrap();
            // Fields after the comm: state, ppid, 17 more, then starttime (field 22 of proc_pid_stat(5)).
            std::fs::write(proc_root.join(pid.to_string()).join("stat"), format!("{pid} (Hyprland) S {ppid} {} {start}", ["0"; 17].join(" "))).unwrap();
        };
        let _old = inst("old", 100);
        stat(100, 50, 1000);
        assert_eq!(rediscover(&rt, &proc_root, Some("old")).as_deref(), Some("old"), "a live followed instance is kept");
        let _new = inst("new", 200);
        stat(200, 50, 2000);
        assert_eq!(rediscover(&rt, &proc_root, Some("old")).as_deref(), Some("old"));
        assert_eq!(rediscover(&rt, &proc_root, Some("gone")).as_deref(), Some("new"), "the newest live instance (start time), not the name order");
        std::fs::remove_dir_all(proc_root.join("100")).unwrap();
        assert_eq!(rediscover(&rt, &proc_root, Some("old")).as_deref(), Some("new"), "the compositor restarted: follow the new socket");
        let _nested = inst("nested", 300);
        stat(300, 250, 3000);
        stat(250, 200, 2500);
        assert_eq!(live_instances(&rt, &proc_root).iter().map(|(s, _)| s.as_str()).collect::<Vec<_>>(), ["new"], "a session nested in a listed instance is never selected");
        std::fs::remove_dir_all(proc_root.join("200")).unwrap();
        std::fs::remove_dir_all(proc_root.join("300")).unwrap();
        assert_eq!(rediscover(&rt, &proc_root, Some("new")), None, "no live compositor at all");
        std::fs::remove_dir_all(&t).unwrap();
    }
    #[test]
    fn vt_tmux_client_is_found_through_the_console_bridge() {
        let t = std::env::temp_dir().join(format!("mios-inputd-vtproc-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&t);
        let sockdir = t.join("sock/tmux-1000");
        std::fs::create_dir_all(&sockdir).unwrap();
        let _human = std::os::unix::net::UnixListener::bind(sockdir.join("mios-human")).unwrap();
        let tmpdir = format!("TMUX_TMPDIR={}\0", t.join("sock").display());
        let p = t.join("proc");
        fake_proc(&p, 10, 1, "bash", &["-bash"], "", "/dev/tty3");
        fake_proc(&p, 11, 10, "mios-inputd", &["/usr/libexec/mios/mios-inputd", "console-bridge", "--", "tmux"], "", "/dev/tty3");
        fake_proc(&p, 12, 11, "tmux: client", &["tmux", "-L", "mios-human", "attach"], &tmpdir, "/dev/pts/9");
        fake_proc(&p, 20, 1, "bash", &["-bash"], "", "/dev/tty4");
        let socks = vec!["mios-human".to_owned()];
        let c = tmux_client_on_tty(&p, Path::new("/dev/tty3"), &socks, 1000).expect("the client below the bridge on tty3");
        assert_eq!((c.pid, c.tty.as_str()), (12, "/dev/pts/9"));
        assert!(tmux_client_on_tty(&p, Path::new("/dev/tty4"), &socks, 1000).is_none(), "a bare VT shell: console_plain");
        fake_proc(&p, 21, 20, "tmux: client", &["tmux", "-L", "mios-human", "attach"], &tmpdir, "/dev/tty4");
        assert_eq!(tmux_client_on_tty(&p, Path::new("/dev/tty4"), &socks, 1000).map(|c| c.tty).as_deref(), Some("/dev/tty4"), "a client attached directly");
        std::fs::remove_dir_all(&t).unwrap();
    }
    #[test]
    fn capture_osd_names_the_app_and_the_release_chord() {
        let chord = vec!["BTN_THUMBL".to_owned(), "BTN_THUMBR".to_owned()];
        assert_eq!(osd_message("on", "org.gnome.Nautilus", &chord), "Controller -> org.gnome.Nautilus. Hold L3+R3 to release.");
        assert_eq!(osd_message("fullscreen", "steam_app_570", &chord), "Controller -> steam_app_570. Hold L3+R3 to release.");
        assert_eq!(osd_message("off", "steam_app_570", &chord), "Controller -> MiOS. steam_app_570 released.");
        assert_eq!(osd_message("on", "x", &["BTN_TL".into(), "BTN_TR".into()]), "Controller -> x. Hold TL+TR to release.");
        let vendor: toml::Value = include_str!("../../../../usr/share/mios/mios.toml").parse().unwrap();
        let (cfg, _) = GlobalConfig::from_value(&vendor).unwrap();
        assert!(cfg.controller.capture_osd && cfg.controller.capture_osd_argv.iter().any(|a| a == "{message}"));
        let lua = window_lua("on", &cfg, None).unwrap();
        assert!(lua.contains("..' '..tostring(target and target.class or '')"), "the transaction reports the class it captured");
    }
    #[test]
    fn key_projection_into_a_followed_compositor_is_quoted() {
        let lua = key_lua(&crate::nav::parse_key_chord("CTRL+SHIFT+TAB").unwrap(), "0xabc\"x");
        assert!(lua.contains("mods=\"CTRL SHIFT\",key=\"Tab\",state='down',window=\"address:0xabc\\\"x\""), "{lua}");
        assert_eq!(lua.matches("send_key_state").count(), 2);
    }
    #[test]
    fn room_verbs_reach_the_plugin_lua_api() {
        // spec P1 (C-14a/b): the room table's verbs become hl.plugin.hypr3d.<verb>(...) in the room's compositor
        assert_eq!(command_for("hypr:hypr3d:cancel").unwrap()[2], "hl.plugin.hypr3d.cancel()");
        assert_eq!(command_for("hypr:hypr3d:sprint").unwrap()[2], "hl.plugin.hypr3d.sprint()");
        assert_eq!(command_for("hypr:hypr3d:sprint on").unwrap()[2], "hl.plugin.hypr3d.sprint(\"on\")");
        assert_eq!(command_for("hypr:hypr3d:recenter").unwrap()[2], "hl.plugin.hypr3d.recenter()");
        assert_eq!(command_for("hypr:hypr3d:view").unwrap()[2], "hl.plugin.hypr3d.view(\"\")");
        assert!(command_for("hypr:hypr3d:unknown").unwrap_err().contains("unsupported room action"));
        // every vendor room action has an installed dispatcher
        let doc: toml::Value = include_str!("../../../../usr/share/mios/mios.toml").parse().unwrap();
        let (cfg, _) = GlobalConfig::from_value(&doc).unwrap();
        for v in cfg.controller.room.values() {
            let (tap, hold) = v.parts();
            for a in std::iter::once(tap).chain(hold).filter(|a| *a != "none") {
                assert!(configured_command(a, &cfg).is_ok(), "room action without a dispatcher: {a}");
            }
        }
    }
    #[test]
    fn compositor_actions_use_current_lua_api() {
        let v = command_for("hypr:workspace e-1").unwrap();
        assert!(v[2].contains("hl.dsp.focus"));
        assert!(!v[2].contains("workspace.switch"));
    }
    #[test]
    fn lua_parameters_are_quoted_not_source() {
        let v = command_for("hypr:workspace x\"); os.execute('bad'); --").unwrap();
        assert!(v[2].contains("x\\\""));
    }
    #[test]
    fn raw_shell_and_unknown_actions_fail() {
        for a in ["exec:sh -c bad", "hypr:unknown foo", "readline:kill-line"] {
            assert!(command_for(a).is_err());
        }
    }
    #[test]
    fn mode_toggle_uses_real_native_verb() {
        assert_eq!(
            command_for("mios:hyprland-3d-toggle").unwrap(),
            vec!["/usr/bin/mios-gen", "hyprland-3d-toggle"]
        );
    }
}

