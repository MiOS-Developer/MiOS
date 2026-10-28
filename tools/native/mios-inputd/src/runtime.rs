// AI-hint: Native controller service, bounded compositor IPC and named action dispatch.
// AI-related: keybindings.global, desktop.hyprland.hypr3d.controller, mios-inputd.service
use crate::config::{GlobalConfig, LegacyConfig};
use crate::linux::{self, Device, VirtualDevice};
use crate::router::{Output, PadState, Router, EV_SYN, SYN_DROPPED, SYN_REPORT};
use std::collections::BTreeMap;
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
struct ContextSnapshot { name: String, address: Option<String>, pid: Option<u64> }
fn context(cfg: &GlobalConfig) -> ContextSnapshot {
    let window = ipc("j/activewindow").ok().and_then(|v| serde_json::from_str::<serde_json::Value>(&v).ok());
    let class = window.as_ref().and_then(|v| v.get("class")).and_then(|v| v.as_str());
    let room = ipc("/eval error('MIOS_INPUT_ROOM:' .. tostring(hl.plugin.hypr3d.active()), 0)")
        .is_ok_and(|v| v.contains("MIOS_INPUT_ROOM:true"));
    ContextSnapshot {
        name: class.map_or_else(|| "terminal".into(), |class| cfg.classify(class, room)),
        address: window.as_ref().and_then(|v| v.get("address")).and_then(|v|v.as_str()).map(str::to_owned),
        pid: window.as_ref().and_then(|v|v.get("pid")).and_then(|v|v.as_u64()),
    }
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
    let mut focus = ContextSnapshot { name: opt.context.clone().unwrap_or_else(|| "terminal".into()), address:None, pid:None };
    let shared_focus=std::sync::Arc::new(std::sync::Mutex::new(focus.clone()));
    let shared_policy=std::sync::Arc::new(std::sync::Mutex::new(global.clone()));
    let context_stop=std::sync::Arc::new(AtomicBool::new(false));
    let context_worker=if opt.context.is_none() && !opt.fixture_only {
        let cache=shared_focus.clone(); let policy=shared_policy.clone(); let stop=context_stop.clone();
        Some(std::thread::spawn(move || {
            while !stop.load(Ordering::Relaxed) {
                let cfg=policy.lock().unwrap().clone();
                let snapshot=context(&cfg); // IPC never holds the event-loop lock.
                *cache.lock().unwrap()=snapshot;
                std::thread::sleep(Duration::from_millis(250));
            }
        }))
    } else {None};
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
                            for dev in devices.values_mut() {
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
                            }
                            *shared_policy.lock().map_err(|_| "context policy lock failed")?=next.clone();
                            global = next;
                            legacy = next_legacy;
                            eprintln!("input policy reloaded from layered SSOT");
                        }
                        Err(e) => {
                            eprintln!("input policy reload rejected; keeping validated policy: {e}")
                        }
                    }
                }
                let paths = if let Some(p) = &opt.device {
                    vec![p.clone()]
                } else {
                    linux::discover().map_err(|e| e.to_string())?
                };
                for path in paths {
                    if devices.contains_key(&path) {
                        continue;
                    }
                    let mut device = match Device::open(&path, opt.fixture_only) {
                        Ok(d) => d,
                        Err(e) => {
                            if opt.device.is_some() {
                                return Err(e.to_string());
                            }
                            continue;
                        }
                    };
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
            }
            if let Ok(snapshot)=shared_focus.try_lock() { focus=snapshot.clone(); ctx=focus.name.clone(); }
            let paths: Vec<_> = devices.keys().cloned().collect();
            for path in paths {
                let dev = devices.get_mut(&path).unwrap();
                let transition = dev.router.set_context(&ctx);
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
                        if selected.as_ref() == Some(&path) {
                            selected = None;
                        }
                    }
                }
            }
            let now = Instant::now();
            let dt = now.duration_since(last_tick).as_secs_f64().min(0.05);
            last_tick = now;
            for dev in devices.values_mut() {
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

