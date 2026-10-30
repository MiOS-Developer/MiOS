// AI-hint: Native controller service, bounded compositor IPC, named action dispatch, the virtual keyboard/tmux/OSD projections and /proc tmux client discovery.
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
}
struct RunningDevice {
    device: Device,
    output: Option<VirtualDevice>,
    router: Router,
    dropped: bool,
}
pub(crate) fn ipc(request: &str) -> Result<String, String> {
    let runtime = std::env::var("XDG_RUNTIME_DIR").map_err(|_| "XDG_RUNTIME_DIR missing")?;
    let sig = std::env::var("HYPRLAND_INSTANCE_SIGNATURE")
        .map_err(|_| "selected compositor signature missing")?;
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
    /// C-09: the MiOS tmux client running under the focused terminal, if any.
    pub tmux_client: Option<TmuxClient>,
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
        class:class.map(str::to_owned),tmux_client:None}
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
        ["focus",d] => base(&["display-message","-p","-c",&client.tty,"-t",&client.tty,&format!("#{{pane_at_{d}}}")]),
        ["page-up"] | ["page-down"] => base(&["display-message","-p","-c",&client.tty,"-t",&client.tty,"#{pane_id} #{pane_in_mode}"]),
        _ => return Err(format!("tmux:{verb} has no runtime projection yet (U1 menu alias / I3 keymap popup)")),
    })
}
/// Run a tmux verb (C-30), including the two-step ones: `focus` falls through to the compositor at the pane edge.
fn run_tmux(cfg: &GlobalConfig, client: &TmuxClient, verb: &str) -> Result<Vec<String>,String> {
    let t=Duration::from_millis(1000);
    let argv=tmux_argv(cfg,client,verb)?;
    let words:Vec<_>=verb.split_whitespace().collect();
    let mut ran=vec![argv[3..].join(" ")];
    let sock=client.socket.to_string_lossy().into_owned();
    let tmux=|rest:&[&str]| -> Vec<String> { ["/usr/bin/tmux","-S",&sock].into_iter().map(str::to_owned).chain(rest.iter().map(|s|(*s).to_owned())).collect() };
    match words.as_slice() {
        ["focus",d] => {
            let edge=run_bounded(&argv,t)?.trim()=="1";
            if edge {
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
    Tmux { verb: String, client: TmuxClient },
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
                    Job::Tmux{verb,client} => {
                        let r=run_tmux(&cfg,&client,&verb);
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
                    if ![
                        "mode",
                        "view",
                        "crt",
                        "grab",
                        "resize",
                        "fly",
                        "posture",
                        "click",
                        "fullscreen",
                        "jump",
                    ]
                    .contains(&name)
                    {
                        return Err(format!("unsupported room action {name}"));
                    }
                    if ["fullscreen", "jump"].contains(&name) && arg.is_empty() {
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
            let chord = crate::nav::parse_key_chord(chord)?;
            if sinks.execute_actions {
                if sinks.keyboard.is_none() {
                    sinks.keyboard = Some(VirtualDevice::keyboard().map_err(|e| e.to_string())?);
                }
                sinks.keyboard.as_mut().unwrap().write(&crate::nav::stroke(&chord)).map_err(|e| e.to_string())?;
                submitted = true;
            } else if sinks.project {
                if let (Some(worker), Some(window)) = (&sinks.actions, &focus.address) {
                    submitted = worker.submit(Job::Lua { action: action.clone(), lua: key_lua(&chord, window) });
                }
            }
        } else if let Some(verb) = action.strip_prefix("tmux:") {
            // C-30: only against the tmux client discovery found; without one the *_plain profile is active.
            if sinks.execute_actions || sinks.project {
                if let (Some(worker), Some(client)) = (&sinks.actions, &focus.tmux_client) {
                    submitted = worker.submit(Job::Tmux { verb: verb.into(), client: client.clone() });
                }
            }
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
/// logind's own runtime state (users/<uid> DISPLAY=, sessions/<id> SEAT=): read as files, so an idle serve never
/// spawns a process per re-check (no loginctl, no interpreter, no D-Bus round trip).
pub(crate) fn seat_from_logind_state(state: &Path, uid: u32) -> Option<String> {
    let field = |path: PathBuf, key: &str| -> Result<String, String> {
        let text = std::fs::read_to_string(&path).map_err(|e| format!("logind state unavailable ({}): {e}", path.display()))?;
        Ok(text.lines().find_map(|l| l.strip_prefix(key).and_then(|v| v.strip_prefix('='))).unwrap_or("").trim().to_owned())
    };
    let display = match field(state.join("users").join(uid.to_string()), "DISPLAY") { Ok(v) => v, Err(e) => return Some(e) };
    if display.is_empty() || !display.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_') {
        return Some("no graphical session for this user".into());
    }
    match field(state.join("sessions").join(&display), "SEAT") {
        Ok(seat) if seat.is_empty() => Some("the graphical session has no seat".into()),
        Ok(_) => None,
        Err(e) => Some(e),
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
    run(global, legacy, Options { root, bridge: true, fixture_only: false, device: None, context: None, duration: None, pad_path, live_context: false, write_pad: true })
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
    let follow = opt.context.is_none() && (!opt.fixture_only || opt.live_context);
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
    let mut focus = ContextSnapshot { name: opt.context.clone().unwrap_or_else(|| "app-capture".into()), address:None, pid:None, room_capture:false, released:false, sampled:Instant::now(), class:None, tmux_client:None };
    let shared_focus=std::sync::Arc::new(std::sync::Mutex::new(focus.clone()));
    let shared_policy=std::sync::Arc::new(std::sync::Mutex::new(global.clone()));
    let mut sinks = Sinks {
        pointer: None,
        children: vec![],
        ownership: if follow && opt.bridge { Some(OwnershipWorker::spawn(shared_policy.clone())?) } else { None },
        actions: if opt.bridge && (!opt.fixture_only || opt.live_context) { Some(ActionWorker::spawn(shared_policy.clone())?) } else { None },
        keyboard: None,
        bridge: opt.bridge,
        execute_actions: opt.bridge && !opt.fixture_only,
        project: opt.bridge && opt.fixture_only && opt.live_context,
    };
    let context_stop=std::sync::Arc::new(AtomicBool::new(false));
    let context_worker=if follow {
        let cache=shared_focus.clone(); let policy=shared_policy.clone(); let stop=context_stop.clone();
        Some(std::thread::spawn(move || {
            let mut events=event_socket();
            // SAFETY: getuid has no memory/ownership effects.
            let uid=unsafe{libc::getuid()};
            let mut tmux_cache:Option<(u64,Instant,Option<TmuxClient>)>=None;
            while !stop.load(Ordering::Relaxed) {
                let cfg=policy.lock().unwrap().clone();
                // A failed IPC read never invents terminal focus or resets held input.
                if let Ok(mut snapshot)=context(&cfg) {
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
                    let output = if opt.bridge {
                        device
                            .grab()
                            .map_err(|e| format!("{}: cannot own input: {e}", path.display()))?;
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
            for (path, dev) in devices.iter_mut() {
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
        assert_eq!(seat_from_logind_state(&t,1000).as_deref(),Some("no graphical session for this user"),"SSH-only login: idle");
        std::fs::write(t.join("users/1000"),"DISPLAY=../x\n").unwrap();
        assert_eq!(seat_from_logind_state(&t,1000).as_deref(),Some("no graphical session for this user"),"a path-like id is rejected");
        std::fs::write(t.join("users/1000"),"STATE=active\nDISPLAY=3\n").unwrap();
        std::fs::write(t.join("sessions/3"),"TYPE=wayland\nSEAT=\n").unwrap();
        assert_eq!(seat_from_logind_state(&t,1000).as_deref(),Some("the graphical session has no seat"));
        std::fs::write(t.join("sessions/3"),"TYPE=wayland\nSEAT=seat0\n").unwrap();
        assert_eq!(seat_from_logind_state(&t,1000),None,"a seated graphical session reaches the bridge");
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
        assert!(tmux_argv(&cfg, &c, "key q").unwrap_err().contains("prefix-table"), "q is not bound in the SSOT prefix table");
        assert!(tmux_argv(&cfg, &c, "menu").is_err(), "the menu alias is U1's");
        assert!(unsupported_actions(&cfg).is_empty(), "every vendor nav action resolves: {:?}", unsupported_actions(&cfg));
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

