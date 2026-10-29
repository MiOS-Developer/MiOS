// AI-hint: Native controller service, bounded compositor IPC and named action dispatch.
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
    pub room_capture: bool, sampled: Instant,
}
fn snapshot(cfg:&GlobalConfig,v:&serde_json::Value) -> ContextSnapshot {
    let class=v.get("class").and_then(|v|v.as_str()).filter(|s|!s.is_empty());
    let room=v.get("room").and_then(|v|v.as_bool()).unwrap_or(false);
    let captured=v.get("captured").and_then(|v|v.as_bool()).unwrap_or(false);
    ContextSnapshot {name:cfg.classify_window(class,room,captured),
        address:v.get("address").and_then(|v|v.as_str()).filter(|s|!s.is_empty()).map(str::to_owned),
        pid:v.get("pid").and_then(|v|v.as_u64()).filter(|p|*p>0),
        room_capture:captured && (room || v.get("handler").and_then(|v|v.as_str())==Some("hypr3d")),sampled:Instant::now()}
}
pub fn context(cfg: &GlobalConfig) -> Result<ContextSnapshot,String> {
    let template=r#"local function q(s) return '"'..tostring(s or ''):gsub('[%z\1-\31\\"]',function(c) return string.format('\\u%04x',c:byte()) end)..'"' end
local w=hl.get_active_window(); local room=false; local p=hl.plugin and hl.plugin.hypr3d
if p and p.active then local ok,v=pcall(p.active); room=ok and v and true or false end
local captured=false
if w then
 if CAPTURE_FULLSCREEN and tonumber(w.fullscreen)==2 then captured=true end
 for _,tag in pairs(w.tags or {}) do if tag:gsub('%*$','')==CAPTURE_TAG then captured=true end end
 if p and p.input_owner then local ok,id=pcall(p.input_owner); if ok and id~=0 and tonumber(w.address)==id then captured=true end end
end
error('MIOS_INPUT_CONTEXT:'..'{"class":'..q(w and w.class)..',"address":'..q(w and w.address)..',"pid":'..tostring(w and w.pid or 0)..',"fullscreen":'..tostring(w and w.fullscreen or 0)..',"handler":'..q(w and w.fullscreen_handler)..',"room":'..tostring(room)..',"captured":'..tostring(captured)..'}',0)"#;
    let lua=template.replace("CAPTURE_FULLSCREEN",if cfg.controller.capture_fullscreen {"true"}else{"false"})
        .replace("CAPTURE_TAG",&serde_json::to_string(&cfg.controller.capture_tag).unwrap());
    let reply=ipc(&format!("/eval {lua}"))?;
    let json=reply.split_once("MIOS_INPUT_CONTEXT:").and_then(|(_,v)|v.lines().next()).ok_or("compositor context response missing")?;
    let value:serde_json::Value=serde_json::from_str(json).map_err(|e|format!("invalid native context: {e}"))?;
    Ok(snapshot(cfg,&value))
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
fn emit(
    output: Output,
    device: &mut RunningDevice,
    pointer: &mut Option<VirtualDevice>,
    bridge: bool,
    execute_actions: bool,
    children: &mut Vec<(Instant, Child)>,
    focus: &ContextSnapshot,
) -> Result<(), String> {
    if bridge {
        if let Some(virtual_pad) = &mut device.output {
            virtual_pad.write(&output.app).map_err(|e| e.to_string())?;
        }
        if execute_actions && !output.pointer.is_empty() {
            if pointer.is_none() {
                *pointer = Some(VirtualDevice::pointer().map_err(|e| e.to_string())?);
            }
            pointer
                .as_mut()
                .unwrap()
                .write(&output.pointer)
                .map_err(|e| e.to_string())?;
        }
    }
    for action in output.actions {
        let mut submitted = false;
        if execute_actions {
            let args = configured_command(&action, device.router.policy())?;
            if !args.is_empty() {
                // Cap/reap subprocesses; a stalled compositor cannot fill the host
                // with an unbounded dispatch queue while a controller is held.
                if children.len() >= 8 {
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
                children.push((Instant::now(), child));
                submitted = true;
            }
        }
        println!(
            "{}",
            serde_json::json!({"device":device.device.path,"context":device.router.context,"action":action,"submitted":submitted,"executed":false})
        );
    }
    Ok(())
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
                    Err(e)=> {if explicit.is_some() {let _=sender.try_send(Err(e.to_string()));return;}if let Some(id)=identity {rejected.insert(path,id);}}
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
    let runtime = std::env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .ok_or("XDG_RUNTIME_DIR missing")?;
    let lockname = if opt.fixture_only {
        "mios-inputd-fixture.lock"
    } else {
        "mios-inputd.lock"
    };
    let _lock = linux::singleton(&runtime.join(lockname)).map_err(|e| e.to_string())?;
    // SAFETY: signal handlers only store to a lock-free atomic boolean. File
    // descriptors are dropped in normal control flow; SIGKILL closes them in-kernel.
    unsafe {
        libc::signal(
            libc::SIGTERM,
            stop_signal as *const () as libc::sighandler_t,
        );
        libc::signal(libc::SIGINT, stop_signal as *const () as libc::sighandler_t);
    }
    STOP.store(false, Ordering::Relaxed);
    let mut devices: BTreeMap<PathBuf, RunningDevice> = BTreeMap::new();
    let mut pointer = None;
    let mut children = vec![];
    let mut selected: Option<PathBuf> = None;
    let mut seed = PadState::default();
    if opt.bridge && !opt.fixture_only {
        if let Ok(bytes) = std::fs::read(&opt.pad_path) {
            seed.seed_sequences(&bytes);
        }
    }
    let start = Instant::now();
    let mut scan = start - Duration::from_secs(2);
    let mut last_tick = start;
    let mut last_reconcile = start;
    let mut focus = ContextSnapshot { name: opt.context.clone().unwrap_or_else(|| "app-capture".into()), address:None, pid:None, room_capture:false,sampled:Instant::now() };
    let shared_focus=std::sync::Arc::new(std::sync::Mutex::new(focus.clone()));
    let shared_policy=std::sync::Arc::new(std::sync::Mutex::new(global.clone()));
    let context_stop=std::sync::Arc::new(AtomicBool::new(false));
    let context_worker=if opt.context.is_none() && !opt.fixture_only {
        let cache=shared_focus.clone(); let policy=shared_policy.clone(); let stop=context_stop.clone();
        Some(std::thread::spawn(move || {
            let mut events=event_socket();
            while !stop.load(Ordering::Relaxed) {
                let cfg=policy.lock().unwrap().clone();
                // A failed IPC read never invents terminal focus or resets held input.
                if let Ok(snapshot)=context(&cfg) { *cache.lock().unwrap()=snapshot; }
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
                                break;
                            }
                            if policy_fingerprint(&global,&legacy)? != policy_fingerprint(&next,&next_legacy)? {
                            for dev in devices.values_mut() {
                                let cursor=dev.router.cursor;
                                let out = dev.router.reset();
                                emit(
                                    out,
                                    dev,
                                    &mut pointer,
                                    opt.bridge,
                                    opt.bridge && !opt.fixture_only,
                                    &mut children,
            &focus,
                                )?;
                                let seq = dev.router.pad.sequences;
                                dev.router = Router::new(
                                    next.clone(),
                                    next_legacy.clone(),
                                    dev.device.snapshot().map_err(|e| e.to_string())?.1,
                                );
                                dev.router.pad.sequences = seq;
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
                    router.set_context(&ctx);
                    if let Ok((keys, axes)) = device.snapshot() {
                        router.resync(keys, axes);
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
            if !opt.fixture_only && opt.context.is_none() && focus.sampled.elapsed()>Duration::from_millis(global.controller.context_stale_ms) {
                focus.name="app-capture".into(); focus.room_capture=false;
            }
            ctx=focus.name.clone();
            let paths: Vec<_> = devices.keys().cloned().collect();
            for path in paths {
                let dev = devices.get_mut(&path).unwrap();
                let target=focus.address.clone().zip(focus.pid);
                let transition = dev.router.set_focus(&ctx,target,focus.room_capture);
                emit(
                    transition,
                    dev,
                    &mut pointer,
                    opt.bridge,
                    opt.bridge && !opt.fixture_only,
                    &mut children,
            &focus,
                )?;
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
                                emit(
                                    out,
                                    dev,
                                    &mut pointer,
                                    opt.bridge,
                                    opt.bridge && !opt.fixture_only,
                                    &mut children,
            &focus,
                                )?;
                            } else {
                                let out = dev
                                    .router
                                    .event(event, (linux::monotonic_ns() / 1_000_000) as u64);
                                emit(
                                    out,
                                    dev,
                                    &mut pointer,
                                    opt.bridge,
                                    opt.bridge && !opt.fixture_only,
                                    &mut children,
            &focus,
                                )?;
                            }
                        }
                    }
                    Err(e) => {
                        eprintln!("input disconnected {}: {e}", path.display());
                        let out = dev.router.reset();
                        emit(
                            out,
                            dev,
                            &mut pointer,
                            opt.bridge,
                            opt.bridge && !opt.fixture_only,
                            &mut children,
            &focus,
                        )?;
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
            for dev in devices.values_mut() {
                if reconcile_due && !dev.dropped {
                    if let Ok(held) = dev.device.held_keys() {
                        let out = dev.router.reconcile(&held, (linux::monotonic_ns()/1_000_000) as u64);
                        emit(out,dev,&mut pointer,opt.bridge,opt.bridge && !opt.fixture_only,&mut children,&focus)?;
                    }
                }
                let out = dev.router.tick(dt);
                emit(
                    out,
                    dev,
                    &mut pointer,
                    opt.bridge,
                    opt.bridge && !opt.fixture_only,
                    &mut children,
            &focus,
                )?;
            }
            if opt.bridge && !opt.fixture_only {
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
            while i < children.len() {
                let expired = children[i].0.elapsed() > Duration::from_secs(2);
                if expired {
                    let _ = children[i].1.kill();
                }
                if expired
                    || children[i]
                        .1
                        .try_wait()
                        .map_err(|e| e.to_string())?
                        .is_some()
                {
                    let (_, mut child) = children.swap_remove(i);
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
        let _ = emit(
            out,
            dev,
            &mut pointer,
            opt.bridge,
            opt.bridge && !opt.fixture_only,
            &mut children,
            &focus,
        );
        dev.device.release();
    }
    for (_, mut child) in children {
        let _ = child.kill();
        let _ = child.wait();
    }
    if opt.bridge && !opt.fixture_only {
        seed.neutralize();
        let _ = atomic_packet(&opt.pad_path, &seed);
    }
    context_stop.store(true, Ordering::Relaxed);
    if let Some(worker)=context_worker { let _=worker.join(); }
    let _=discovery.join();
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
        if !["on","off","fullscreen"].contains(&mode) {return Err("unsupported window ownership mode".into());}
        let mode=serde_json::to_string(mode).unwrap();
        let tag=serde_json::to_string(&global.controller.capture_tag).unwrap();
        let lua=format!("local p=hl.plugin and hl.plugin.hypr3d;local mode={mode};local function tag(w) if w then hl.dispatch(hl.dsp.window.tag({{window=w,tag=(mode=='off' and '-' or '+')..{tag}}})) end end;if p and p.active() then assert(p.input_capture and p.input_owner,'room input ownership unavailable');local id=p.input_owner();assert(p.input_capture(mode),'no selected room window');if mode~='off' then id=p.input_owner() end;local found=false;for _,w in ipairs(hl.get_windows()) do if tonumber(w.address)==id then tag(w);found=true;break end end;if not found and mode=='off' then tag(hl.get_active_window()) end else local w=hl.get_active_window();if not w then error('no focused window',0) end;tag(w);if mode=='fullscreen' then hl.dispatch(hl.dsp.window.fullscreen({{window=w,mode='fullscreen',action='set'}})) end end");
        return Ok(vec!["/usr/bin/hyprctl".into(),"eval".into(),lua]);
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
        .filter(|a| configured_command(a, global).is_err())
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
        v["room"]=serde_json::json!(false);assert!(!snapshot(&cfg,&v).room_capture,"a normal fullscreen game keeps its thumbstick chord");
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

