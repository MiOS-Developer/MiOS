// AI-hint: Native controller service, bounded compositor IPC with live socket rediscovery, the logind seat view (graphical / console / another session), named action dispatch, the virtual keyboard/tmux/OSD/VT projections and /proc tmux client discovery (focused terminal or active VT).
// AI-related: keybindings.global, desktop.hyprland.hypr3d.controller, mios-inputd.service
use crate::config::{GlobalConfig, LegacyConfig};
use crate::linux::{self, Device, VirtualDevice};
use crate::router::{Output, PadState, Router, EV_SYN, SYN_DROPPED, SYN_REPORT};
use std::collections::{BTreeMap, BTreeSet};
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
    /// Explicit devices (the fixture tier, `--device`, or the nodes a nested session holds); empty = discover all.
    pub devices: Vec<PathBuf>,
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
    /// I4: `bridge --nested <seat>`: follow that nested session (its compositor via the environment main set, its
    /// private room packet) and own only the controller nodes assigned to it. Key and compositor actions project into
    /// the session; there is no virtual pad clone: a captured app in the session reads the assigned node itself (the
    /// grab is released while it owns the pad).
    pub nested: Option<crate::nested::Session>,
}
struct RunningDevice {
    device: Device,
    output: Option<VirtualDevice>,
    router: Router,
    dropped: bool,
    /// An explicit controller-to-session route never shares the host's keyboard, mouse or raw pad.
    session: Option<crate::sessions::SessionRoute>,
    session_error: Option<String>,
    session_worker: Option<SessionWorker>,
    capture: Option<CaptureBinding>,
    remote_input: Option<RemoteInput>,
    login_generation: u64,
    capture_recovery_at: Instant,
    /// INV-8: another session owns the seat; the grab is released and nothing is routed until it returns.
    suspended: bool,
    /// Deflected stick suppression until neutral zero-crossing.
    neutral_rearming: bool,
}
#[derive(Clone)]
struct CapturedTmux {
    client: TmuxClient,
    start: u64,
    session: String,
    pane: String,
    identity: (u32, String),
}
#[derive(Clone)]
struct CaptureReceipt {
    native: Option<crate::tmux_capture::Target>,
    address: String,
    class: String,
    pid: u32,
    tmux: Option<CapturedTmux>,
    error: Option<String>,
}
struct RemoteInput {
    target: crate::tmux_capture::Target,
    keys: SessionWorker,
}
#[derive(Default)]
struct PointerHolds {
    sources: BTreeMap<u16, BTreeSet<PathBuf>>,
    suppressed: BTreeMap<u16, BTreeSet<PathBuf>>,
}
impl PointerHolds {
    fn project(
        &mut self,
        source: &Path,
        events: &[crate::router::Event],
        enabled: bool,
    ) -> Vec<crate::router::Event> {
        let mut out = vec![];
        for event in events {
            if event.kind == crate::router::EV_KEY && (0x110..=0x112).contains(&event.code) {
                let suppressed = self.suppressed.entry(event.code).or_default();
                let held = self.sources.entry(event.code).or_default();
                if event.value > 0 {
                    if !enabled {
                        suppressed.insert(source.to_owned());
                    }
                    if suppressed.contains(source) {
                        continue;
                    }
                    let first = held.is_empty();
                    held.insert(source.to_owned());
                    if first {
                        out.push(*event);
                    }
                } else {
                    suppressed.remove(source);
                    let removed = held.remove(source);
                    if removed && held.is_empty() {
                        out.push(*event);
                    }
                }
            } else if enabled {
                out.push(*event);
            }
        }
        out
    }
    /// Cancellation is a native hide/end boundary, never a synthetic application/chrome button-up.
    /// Remaining real holds must reach release before they can press again; never replay their stale downs.
    fn cancel(&mut self) {
        for (button, sources) in &mut self.sources {
            self.suppressed.entry(*button).or_default().append(sources);
        }
    }
    fn retire_source(&mut self, source: &Path) -> bool {
        if self.sources.values().any(|held| held.contains(source)) {
            self.cancel();
            true
        } else {
            false
        }
    }
    fn retain(&mut self, mut live: impl FnMut(&Path) -> bool) -> bool {
        for sources in self.suppressed.values_mut() {
            sources.retain(|p| live(p));
        }
        let mut cancel = false;
        for sources in self.sources.values_mut() {
            let held = !sources.is_empty();
            sources.retain(|p| live(p));
            cancel |= held && sources.is_empty();
        }
        cancel
    }
}
struct RemotePointerBus {
    target: crate::tmux_capture::Target,
    pointer: Option<crate::tmux_pointer::PointerWorker>,
    cursors: BTreeSet<PathBuf>,
    holds: PointerHolds,
    visible: bool,
    revoked: bool,
    retry: Instant,
}
fn same_pointer_scope(a: &crate::tmux_capture::Target, b: &crate::tmux_capture::Target) -> bool {
    a.uid == b.uid
        && a.socket == b.socket
        && (a.socket_dev, a.socket_ino) == (b.socket_dev, b.socket_ino)
        && (a.server, a.server_start) == (b.server, b.server_start)
        && (a.client, a.client_start, a.client_instance)
            == (b.client, b.client_start, b.client_instance)
        && a.login_generation == b.login_generation
}
struct CaptureBinding {
    address: String,
    pid: u32,
    worker: Option<CaptureDispatcher>,
    error: Option<String>,
}
enum CaptureDispatcher {
    Tmux(SessionWorker),
    Plain(PlainCaptureWorker),
}
impl CaptureDispatcher {
    fn submit(&self, action: String) -> bool {
        match self {
            Self::Tmux(worker) => worker.submit(action),
            Self::Plain(worker) => worker.submit(action),
        }
    }
}
struct PlainCaptureWorker {
    jobs: Option<std::sync::mpsc::SyncSender<SessionJob>>,
    stop: std::sync::Arc<AtomicBool>,
    worker: Option<std::thread::JoinHandle<()>>,
}
impl PlainCaptureWorker {
    fn spawn(address: String, pid: u32, cfg: GlobalConfig) -> Result<Self, String> {
        let (jobs, inbox) = std::sync::mpsc::sync_channel::<SessionJob>(16);
        let stop = std::sync::Arc::new(AtomicBool::new(false));
        let cancelled = stop.clone();
        let worker=std::thread::Builder::new().name("mios-terminal-input".into()).spawn(move || {
            while let Ok(job)=inbox.recv() {
                if cancelled.load(Ordering::Relaxed) {break;}
                if job.queued.elapsed()>=Duration::from_millis(cfg.controller.context_stale_ms) {continue;}
                let Some(chord)=job.action.strip_prefix("key:") else {continue};
                let result=crate::nav::parse_key_chord(chord).and_then(|c|ipc(&format!("/eval {}",captured_key_lua(&c,&address,pid,&cfg))));
                eprintln!("{}",serde_json::json!({"captured_window":address,"action":job.action,"ok":result.as_ref().is_ok_and(|v|v.trim()=="ok"),"error":result.err()}));
            }
        }).map_err(|e|e.to_string())?;
        Ok(Self {
            jobs: Some(jobs),
            stop,
            worker: Some(worker),
        })
    }
    fn submit(&self, action: String) -> bool {
        action.starts_with("key:")
            && self.jobs.as_ref().is_some_and(|tx| {
                tx.try_send(SessionJob {
                    repeat: 1,
                    queued: Instant::now(),
                    app_control: None,
                    action,
                })
                .is_ok()
            })
    }
}
impl Drop for PlainCaptureWorker {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        self.jobs = None;
        self.worker.take();
    }
}
fn capture_navigation_context(cfg: &GlobalConfig, class: Option<&str>) -> Option<String> {
    let base = if class == Some("console") {
        "console".into()
    } else {
        cfg.classify_window(class, false, false)
    };
    cfg.controller
        .capture_navigation_contexts
        .contains(&base)
        .then_some(base)
}
fn captured_client_argv(client: &TmuxClient) -> Vec<String> {
    vec![
        "/usr/bin/tmux".into(),
        "-S".into(),
        client.socket.to_string_lossy().into_owned(),
        "display-message".into(),
        "-p".into(),
        "-c".into(),
        client.tty.clone(),
        "-t".into(),
        client.tty.clone(),
        crate::sessions::IDENTITY_FORMAT.into(),
    ]
}
fn captured_tmux(cfg: &GlobalConfig, pid: u32) -> Result<Option<CapturedTmux>, String> {
    let uid = unsafe { libc::getuid() };
    let Some(client) = tmux_client_under(
        Path::new("/proc"),
        pid,
        &cfg.controller.nav.tmux_sockets,
        uid,
    ) else {
        return Ok(None);
    };
    let start = proc_start(Path::new("/proc"), client.pid)
        .ok_or("captured terminal client vanished")?
        .1;
    let reply = run_bounded(
        &captured_client_argv(&client),
        Duration::from_millis(cfg.controller.context_stale_ms),
    )?;
    let observed = crate::sessions::parse_identity(&reply)?;
    if proc_start(Path::new("/proc"), client.pid).map(|v| v.1) != Some(start) {
        return Err("captured client was replaced".into());
    }
    Ok(Some(CapturedTmux {
        client,
        start,
        session: observed.session.into(),
        pane: observed.pane.into(),
        identity: (observed.pid, observed.window.into()),
    }))
}
fn native_receipt(target: crate::tmux_capture::Target) -> CaptureReceipt {
    CaptureReceipt {
        native: Some(target.clone()),
        address: target.address(),
        class: "console".into(),
        pid: target.foreground,
        tmux: Some(CapturedTmux {
            client: target.client(),
            start: target.client_start,
            session: target.session_name,
            pane: target.pane,
            identity: (target.server, target.window),
        }),
        error: None,
    }
}
fn native_snapshot(cfg: &GlobalConfig, client: &TmuxClient) -> Result<ContextSnapshot, String> {
    let timeout = Duration::from_millis(cfg.controller.context_stale_ms);
    let deadline = Instant::now() + timeout;
    let outer = (
        client.pid,
        crate::nested::start_time(Path::new("/proc"), client.pid).ok_or("outer client vanished")?,
    );
    let client = crate::tmux_capture::leaf_client(cfg, client, timeout)?;
    let (target, owned) =
        crate::tmux_capture::current(&client, deadline.saturating_duration_since(Instant::now()))?;
    let mut snap = ContextSnapshot::new(if owned { "app-capture" } else { "console" });
    snap.address = Some(target.address());
    snap.pid = Some(u64::from(target.foreground));
    snap.class = Some("console".into());
    snap.tmux_client = Some(target.client());
    snap.native_target = Some(target);
    snap.native_outer = Some(outer);
    Ok(snap)
}
fn capture_binding(
    receipt: &CaptureReceipt,
    device: &Path,
    cfg: &GlobalConfig,
) -> Result<CaptureBinding, String> {
    let worker = if let Some(target) = &receipt.tmux {
        let route = crate::sessions::SessionRoute {
            name: "captured-terminal".into(),
            controllers: vec![device.to_owned()],
            socket: target.client.socket.clone(),
            session: target.session.clone(),
            pane: target.pane.clone(),
            ssh_host: None,
        };
        Some(CaptureDispatcher::Tmux(SessionWorker::spawn_checked(
            route,
            cfg.clone(),
            Some((target.client.clone(), target.start)),
            Some(target.identity.clone()),
            receipt.native.clone().map(|t| (t, true)),
            None,
        )?))
    } else if receipt.error.is_none() && receipt.pid > 0 {
        Some(CaptureDispatcher::Plain(PlainCaptureWorker::spawn(
            receipt.address.clone(),
            receipt.pid,
            cfg.clone(),
        )?))
    } else {
        None
    };
    Ok(CaptureBinding {
        address: receipt.address.clone(),
        pid: receipt.pid,
        worker,
        error: receipt.error.clone(),
    })
}
/// Each controller has its own bounded dispatcher. A stalled SSH target cannot block another player's input.
/// Queue age is checked before execution, so reconnect never replays stale button presses.
struct SessionJob {
    repeat: u8,
    queued: Instant,
    action: String,
    app_control: Option<LocalAppControl>,
}
/// P3/P8: an explicit route's verified foreground app (its current pane only) and when it was verified.
type RouteApp = std::sync::Arc<std::sync::Mutex<(Option<String>, Option<Instant>)>>;
#[derive(Clone, Debug, PartialEq, Eq)]
struct LocalAppIdentity {
    route: crate::sessions::SessionRoute,
    socket: (u64, u64),
    server: u32,
    server_start: u64,
    window: String,
    pane_pid: u32,
    pane_start: u64,
    app: String,
    process: mios_resolver::app_input::Process,
}
#[derive(Clone)]
struct LocalAppControl {
    identity: LocalAppIdentity,
    focus: mios_resolver::app_input::Focus,
    at: Instant,
}
#[derive(Default)]
struct LocalAppState {
    epoch: u64,
    channel: String,
    current: Option<LocalAppControl>,
}
type LocalApps = std::sync::Arc<std::sync::Mutex<LocalAppState>>;
fn local_app_identity(
    route: &crate::sessions::SessionRoute,
    cfg: &GlobalConfig,
    timeout: Duration,
    stop: &AtomicBool,
) -> Result<LocalAppIdentity, String> {
    use std::os::unix::fs::{FileTypeExt, MetadataExt};
    if route.ssh_host.is_some() {
        return Err("cross-host app IPC requires future authenticated transport".into());
    }
    let uid = unsafe { libc::getuid() };
    let meta = std::fs::symlink_metadata(&route.socket).map_err(|e| e.to_string())?;
    let private_parent = route
        .socket
        .parent()
        .and_then(|p| std::fs::symlink_metadata(p).ok())
        .is_some_and(|p| p.is_dir() && p.uid() == uid && p.mode() & 0o077 == 0);
    if !meta.file_type().is_socket()
        || meta.uid() != uid
        || (meta.mode() & 0o077 != 0 && !private_parent)
    {
        return Err("local app route has no private same-user socket authority".into());
    }
    let deadline = Instant::now() + timeout;
    let run = |argv: Vec<String>| {
        run_bounded_cancel(
            &argv,
            deadline.saturating_duration_since(Instant::now()),
            Some(stop),
        )
    };
    let inspected = run(crate::sessions::inspect_argv(route)?)?;
    let (server, window) = session_identity(route, &inspected)?;
    let server_start = mios_resolver::app_input::start(server)
        .ok_or("local route server identity missing or foreign")?;
    let pane_pid = run(crate::sessions::pane_pid_argv(route)?)?
        .trim()
        .parse::<u32>()
        .map_err(|_| "invalid local pane process")?;
    let pane_start = mios_resolver::app_input::start(pane_pid)
        .ok_or("local pane process identity missing or foreign")?;
    let (_, pid) =
        proc_groups(Path::new("/proc"), pane_pid).ok_or("local pane has no foreground process")?;
    let app = app_of_pid(
        Path::new("/proc"),
        pid,
        &cfg.controller.nav.apps,
        uid,
        &lstat_facts,
    )
    .ok_or("local pane has no registered foreground app")?;
    let process = mios_resolver::app_input::Process {
        pid,
        start: mios_resolver::app_input::start(pid)
            .ok_or("local app process identity missing or foreign")?,
    };
    let after = std::fs::symlink_metadata(&route.socket).map_err(|e| e.to_string())?;
    if (meta.dev(), meta.ino()) != (after.dev(), after.ino())
        || mios_resolver::app_input::start(server) != Some(server_start)
    {
        return Err("local route authority changed during verification".into());
    }
    Ok(LocalAppIdentity {
        route: route.clone(),
        socket: (meta.dev(), meta.ino()),
        server,
        server_start,
        window,
        pane_pid,
        pane_start,
        app,
        process,
    })
}
fn update_local_app(
    state: &mut LocalAppState,
    identity: Option<LocalAppIdentity>,
    channel: &str,
    at: Instant,
) -> Option<LocalAppControl> {
    state.channel = channel.into();
    if state.current.as_ref().map(|control| &control.identity) != identity.as_ref() {
        state.epoch = state.epoch.saturating_add(1);
    }
    state.current = identity.map(|identity| {
        let address = format!(
            "route:{}:{}:{}:{}:{}:{}:{}:{}",
            identity.socket.0,
            identity.socket.1,
            identity.server,
            identity.server_start,
            identity.window,
            identity.route.pane,
            identity.pane_pid,
            identity.pane_start
        );
        let focus = mios_resolver::app_input::Focus {
            channel: channel.into(),
            epoch: state.epoch,
            address,
            window_pid: u64::from(identity.process.pid),
            generation: 0,
            app: identity.app.clone(),
            process: identity.process.clone(),
        };
        LocalAppControl {
            identity,
            focus,
            at,
        }
    });
    state.current.clone()
}
fn local_control_current(state: &LocalAppState, control: &LocalAppControl, stale_ms: u64) -> bool {
    state
        .current
        .as_ref()
        .is_some_and(|now| now.focus == control.focus && now.identity == control.identity)
        && control.at.elapsed() < Duration::from_millis(stale_ms)
}
fn app_request(action: &str) -> Option<(&str, mios_resolver::app_input::Action)> {
    let (app, key) = action.strip_prefix("input:app-key ")?.split_once(' ')?;
    Some((app, mios_resolver::app_input::Action::from_key(key)?))
}
pub(crate) struct SessionWorker {
    jobs: Option<std::sync::mpsc::SyncSender<SessionJob>>,
    worker: Option<std::thread::JoinHandle<()>>,
    stop: std::sync::Arc<AtomicBool>,
    app: RouteApp,
    local_apps: LocalApps,
    app_stale_ms: u64,
    verifier: Option<std::thread::JoinHandle<()>>,
}
impl SessionWorker {
    fn spawn(route: crate::sessions::SessionRoute, cfg: GlobalConfig) -> Result<Self, String> {
        Self::spawn_client(route, cfg, None, None)
    }
    fn spawn_client(
        route: crate::sessions::SessionRoute,
        cfg: GlobalConfig,
        client: Option<(TmuxClient, u64)>,
        pin: Option<(u32, String)>,
    ) -> Result<Self, String> {
        Self::spawn_checked(route, cfg, client, pin, None, None)
    }
    pub(crate) fn frontend(
        front: &crate::tmux_capture::Frontend,
        cfg: GlobalConfig,
    ) -> Result<Self, String> {
        let t = &front.target;
        let route = crate::sessions::SessionRoute {
            name: "native-mouse".into(),
            controllers: vec![],
            socket: t.socket.clone(),
            session: t.session_name.clone(),
            pane: t.pane.clone(),
            ssh_host: None,
        };
        Self::spawn_checked(
            route,
            cfg,
            Some((t.client(), t.client_start)),
            Some((t.server, t.window.clone())),
            Some((t.clone(), true)),
            None,
        )
    }
    fn login(
        target: crate::tmux_capture::Target,
        device: &Path,
        cfg: GlobalConfig,
        selection: SharedLogin,
    ) -> Result<Self, String> {
        let generation = target.login_generation;
        let route = crate::sessions::SessionRoute {
            name: "login-target".into(),
            controllers: vec![device.to_owned()],
            socket: target.socket.clone(),
            session: target.session_name.clone(),
            pane: target.pane.clone(),
            ssh_host: None,
        };
        Self::spawn_checked(
            route,
            cfg,
            Some((target.client(), target.client_start)),
            Some((target.server, target.window.clone())),
            Some((target, false)),
            Some((selection, generation)),
        )
    }
    fn spawn_checked(
        route: crate::sessions::SessionRoute,
        cfg: GlobalConfig,
        client: Option<(TmuxClient, u64)>,
        pin: Option<(u32, String)>,
        native: Option<(crate::tmux_capture::Target, bool)>,
        login: Option<(SharedLogin, u64)>,
    ) -> Result<Self, String> {
        let (jobs, inbox) = std::sync::mpsc::sync_channel::<SessionJob>(16);
        let stop = std::sync::Arc::new(AtomicBool::new(false));
        let cancelled = stop.clone();
        let app: RouteApp = std::sync::Arc::new(std::sync::Mutex::new((None, None)));
        let local_apps: LocalApps = Default::default();
        let app_stale_ms = cfg.controller.context_stale_ms;
        let current = std::sync::Arc::new(std::sync::Mutex::new(route.clone()));
        // P3: only an explicit local route verifies its pane; a captured terminal's app comes from the focus snapshot.
        let verifier = if client.is_none()
            && route.ssh_host.is_none()
            && !cfg.controller.nav.apps.is_empty()
        {
            Some(Self::spawn_verifier(
                &route.name,
                cfg.clone(),
                current.clone(),
                app.clone(),
                local_apps.clone(),
                stop.clone(),
            )?)
        } else {
            None
        };
        let (moved, pane_app) = (current, app.clone());
        let accepted_apps = local_apps.clone();
        let worker=std::thread::Builder::new().name(format!("mios-pad-{}",route.name)).spawn(move || {
            let timeout=Duration::from_millis(cfg.controller.context_stale_ms);
            let mut route=route;
            let authorized=|| -> Result<(),String> {
                if let Some((selection,generation))=&login {
                    let state=selection.lock().map_err(|_|"session login authority lock failed")?;
                    if *generation==0 || state.generation!=*generation || state.remote.as_ref().is_none_or(|remote|remote.generation!=*generation) {
                        return Err("session login generation changed".into());
                    }
                }
                Ok(())
            };
            let mut identity:Option<(u32,String)>=pin;
            while let Ok(job)=inbox.recv() {
                #[cfg(test)] acceptance_observer::effect_boundary("session-before-job");
                if cancelled.load(Ordering::Relaxed) { break; }
                if job.queued.elapsed()>=timeout {
                    eprintln!("{}",serde_json::json!({"session":route.name,"action":job.action,"dropped":"expired"}));
                    continue;
                }
                let run=|argv:Vec<String>| -> Result<String,String> {
                    authorized()?;
                    if cancelled.load(Ordering::Relaxed) || job.queued.elapsed()>=timeout {return Err("session navigation expired or cancelled".into());}
                    let reply=run_bounded_cancel(&argv,timeout.saturating_sub(job.queued.elapsed()),Some(&cancelled))?;
                    #[cfg(test)] acceptance_observer::effect_boundary("session-after-step");
                    Ok(reply)
                };
                let result=(|| -> Result<(),String> {
                    authorized()?;
                    if job.action.starts_with("input:app-key ") {
                        let (app,semantic)=app_request(&job.action).ok_or("unsupported local app control")?;
                        let control=job.app_control.as_ref().ok_or("local app control has no accepted process witness")?;
                        if app!=control.identity.app || !accepted_apps.lock().is_ok_and(|state|local_control_current(&state,control,cfg.controller.context_stale_ms)) {return Err("local app focus visit expired".into());}
                        let actual=local_app_identity(&route,&cfg,timeout.saturating_sub(job.queued.elapsed()),&cancelled)?;
                        if actual!=control.identity {return Err("local app target changed since acceptance".into());}
                        authorized()?;
                        let state=accepted_apps.lock().map_err(|_|"local app authority lock failed")?;
                        if cancelled.load(Ordering::Relaxed) || job.queued.elapsed()>=timeout || !local_control_current(&state,control,cfg.controller.context_stale_ms) {return Err("local app request cancelled".into());}
                        let runtime=std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from).ok_or("local app runtime missing")?;
                        return mios_resolver::app_input::send(&runtime,control.identity.process.pid,control.identity.process.start,control.focus.clone(),semantic).map_err(|e|e.to_string());
                    }
                    if let Some((client,start))=&client {
                        if proc_start(Path::new("/proc"),client.pid).map(|v|v.1)!=Some(*start) {return Err("captured client was replaced or detached".into())}
                    }
                    if let Some((target,owned_only))=&native {
                        if !crate::tmux_capture::live_target(target,*owned_only) {return Err("native input focus, owner generation or foreground identity changed".into());}
                        if !owned_only {
                            if let Some(verb)=job.action.strip_prefix("tmux:") {
                                run_tmux_guarded(&cfg,&target.client(),verb,true,None,|| {
                                    authorized()?;
                                    if cancelled.load(Ordering::Relaxed) || job.queued.elapsed()>=timeout {
                                        return Err("session navigation expired or cancelled".into());
                                    }
                                    if !crate::tmux_capture::live_target(target,*owned_only) {
                                        return Err("session navigation target changed".into());
                                    }
                                    Ok(timeout.saturating_sub(job.queued.elapsed()))
                                })?;return Ok(());
                            }
                        }
                    }
                    // A mouse-only frontend has no physical controller assignment. Its existing owner lease
                    // authorizes only key projection to this witnessed pane; never fabricate a controller node.
                    let frontend=native.is_some() && route.controllers.is_empty();
                    let native_argv=|rest:Vec<String>| {
                        let mut argv=vec!["/usr/bin/tmux".into(),"-S".into(),route.socket.to_string_lossy().into_owned()];
                        argv.extend(rest);argv
                    };
                    let inspected=run(if frontend {native_argv(vec!["display-message".into(),"-p".into(),"-t".into(),route.pane.clone(),crate::sessions::IDENTITY_FORMAT.into()])}
                        else {crate::sessions::inspect_argv(&route)?})?;
                    let current=session_identity(&route,&inspected)?;
                    if identity.as_ref().is_some_and(|p|*p!=current) {
                        return Err("tmux server or window identity changed; rebind this controller".into());
                    }
                    identity=Some(current.clone());
                    let plan=(if frontend {
                        job.action.strip_prefix("key:").ok_or_else(||"mouse frontend permits key projection only".to_string())
                            .and_then(crate::sessions::tmux_key).map(|key|native_argv(vec!["send-keys".into(),"-t".into(),route.pane.clone(),key]))
                    } else {crate::sessions::plan(&route,&job.action,&cfg)}).map(|mut argv|{
                        if job.repeat>1 && job.action.starts_with("key:") {
                            if let Some(key)=argv.last().cloned() {argv.extend(std::iter::repeat_n(key,usize::from(job.repeat-1)));}
                        }
                        argv
                    });
                    let reply=if let (Err(_),Some((client,_)),Some(verb))=(&plan,&client,job.action.strip_prefix("tmux:")) {
                        // These UI verbs require the captured client, which must still select our controller's pane.
                        let switching=verb=="session-prev" || verb=="session-next";
                        if !(verb=="menu" || verb=="keymap" || verb.starts_with("key ") || switching) {
                            return Err("unsupported captured-client action".into());
                        }
                        let selected=run(captured_client_argv(client))?;
                        session_identity(&route,&selected)?;
                        if verb=="keymap" {write_private(&keymap_path(),&cfg.controller.nav.text(cfg.controller.capture_hold_ms))?;}
                        let reply=run(tmux_argv(&cfg,client,verb)?)?;
                        if switching {
                            let selected=run(captured_client_argv(client))?;
                            let observed=crate::sessions::parse_identity(&selected)?;
                            let mut next=route.clone();next.session=observed.session.into();next.pane=observed.pane.into();
                            let next_identity=session_identity(&next,&selected)?;
                            if next_identity.0!=current.0 {return Err("captured client server changed during session switch".into());}
                            identity=Some(next_identity);route=next;
                        }
                        reply
                    } else {run(plan?)?};
                    if job.action.starts_with("tmux:focus ") {
                        let next=crate::sessions::focus_target(&route,&job.action,&reply)?;
                        let next_identity=session_identity(&next,&run(crate::sessions::inspect_argv(&next)?)?)?;
                        if next_identity!=current { return Err("focus target left the assigned session/window".into()); }
                        run(crate::sessions::select_argv(&next)?)?;
                        if next.pane!=route.pane {
                            // P8: an app verified for the previous pane never applies to the next one.
                            *pane_app.lock().unwrap()=(None,None);
                            let channel={let mut state=accepted_apps.lock().unwrap();let channel=state.channel.clone();update_local_app(&mut state,None,&channel,Instant::now());channel};
                            if !channel.is_empty() {publish_app_lease(app_authority().as_ref(),&channel,None,Instant::now(),cfg.controller.context_stale_ms);}
                            *moved.lock().unwrap()=next.clone();
                        }
                        route=next;
                    }
                    Ok(())
                })();
                #[cfg(test)] if login.is_some() {acceptance_observer::record_result("session-login",&result);}
                eprintln!("{}",serde_json::json!({"session":route.name,"pane":route.pane,"action":job.action,
                    "ok":result.is_ok(),"error":result.err()}));
            }
        }).map_err(|e|e.to_string())?;
        Ok(Self {
            jobs: Some(jobs),
            worker: Some(worker),
            stop,
            app,
            local_apps,
            app_stale_ms,
            verifier,
        })
    }
    /// P3 for an explicit local route: every app_poll_ms, the foreground group leader of the route's current pane. A
    /// result whose pane changed while it was verified is discarded; any error is no app.
    fn spawn_verifier(
        name: &str,
        cfg: GlobalConfig,
        current: std::sync::Arc<std::sync::Mutex<crate::sessions::SessionRoute>>,
        app: RouteApp,
        local_apps: LocalApps,
        stop: std::sync::Arc<AtomicBool>,
    ) -> Result<std::thread::JoinHandle<()>, String> {
        std::thread::Builder::new()
            .name(format!("mios-pad-app-{name}"))
            .spawn(move || {
                static NEXT: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(1);
                let channel = format!("route:{}", NEXT.fetch_add(1, Ordering::Relaxed));
                let authority = app_authority();
                let nav = &cfg.controller.nav;
                let timeout = Duration::from_millis(nav.app_query_timeout_ms);
                while !stop.load(Ordering::Relaxed) {
                    let route = current.lock().unwrap().clone();
                    let identity = local_app_identity(&route, &cfg, timeout, &stop).ok();
                    if *current.lock().unwrap() == route {
                        let at = Instant::now();
                        let control = update_local_app(
                            &mut local_apps.lock().unwrap(),
                            identity,
                            &channel,
                            at,
                        );
                        *app.lock().unwrap() =
                            (control.as_ref().map(|c| c.identity.app.clone()), Some(at));
                        publish_app_lease(
                            authority.as_ref(),
                            &channel,
                            control.map(|c| c.focus),
                            at,
                            cfg.controller.context_stale_ms,
                        );
                    }
                    let until = Instant::now() + Duration::from_millis(nav.app_poll_ms);
                    while Instant::now() < until && !stop.load(Ordering::Relaxed) {
                        std::thread::sleep(Duration::from_millis(10));
                    }
                }
                update_local_app(
                    &mut local_apps.lock().unwrap(),
                    None,
                    &channel,
                    Instant::now(),
                );
                publish_app_lease(
                    authority.as_ref(),
                    &channel,
                    None,
                    Instant::now(),
                    cfg.controller.context_stale_ms,
                );
            })
            .map_err(|e| e.to_string())
    }
    /// The route's fresh verified app (older than `stale_ms`, or not verified yet: none).
    fn app(&self, stale_ms: u64) -> Option<String> {
        let (app, at) = self.app.lock().ok()?.clone();
        app.filter(|_| at.is_some_and(|t| t.elapsed() < Duration::from_millis(stale_ms)))
    }
    fn submit(&self, action: String) -> bool {
        self.submit_repeat(action, 1)
    }
    pub(crate) fn submit_repeat(&self, action: String, repeat: u8) -> bool {
        if !(1..=8).contains(&repeat) {
            return false;
        }
        let app_control = if action.starts_with("input:app-key ") {
            let Some((app, _)) = app_request(&action) else {
                return false;
            };
            let Ok(state) = self.local_apps.lock() else {
                return false;
            };
            let Some(control) = state.current.as_ref().filter(|c| {
                c.identity.app == app && local_control_current(&state, c, self.app_stale_ms)
            }) else {
                return false;
            };
            if repeat != 1 {
                return false;
            }
            Some(control.clone())
        } else {
            None
        };
        self.jobs.as_ref().is_some_and(|tx| {
            tx.try_send(SessionJob {
                repeat,
                queued: Instant::now(),
                action,
                app_control,
            })
            .is_ok()
        })
    }
}
impl Drop for SessionWorker {
    fn drop(&mut self) {
        // Disconnect and discard the queue; any already executing command has a hard deadline.
        self.stop.store(true, Ordering::Relaxed);
        self.jobs = None;
        // Cancellation kills the dedicated command process group. The forwarding thread never joins a remote job.
        self.worker.take();
        self.verifier.take();
    }
}
/// The exact target identity is checked before dispatch; no "current session" or ambient TMUX context participates.
fn session_identity(
    route: &crate::sessions::SessionRoute,
    reply: &str,
) -> Result<(u32, String), String> {
    let observed = crate::sessions::parse_identity(reply)?;
    if observed.session != route.session || observed.pane != route.pane {
        return Err("tmux target does not match assigned session/pane".into());
    }
    Ok((observed.pid, observed.window.to_owned()))
}
/// Explicit headless assignments may run without a seated login, but never override a foreign active seat.
fn session_focus(focus: &ContextSnapshot, assigned: bool, error: Option<&str>) -> ContextSnapshot {
    if !assigned && error.is_none() {
        return focus.clone();
    }
    let mut out = ContextSnapshot::new("console");
    // Ambiguous ownership reserves the physical node while output is dropped by emit; it is not a foreign seat.
    out.suspended = focus.suspended.clone().filter(|reason| {
        !reason.starts_with("no seated session") && !reason.starts_with("no logind record")
    });
    out
}
/// The compositor this process follows. Starts as HYPRLAND_INSTANCE_SIGNATURE; rediscovery (C-16) replaces a stale one
/// after a compositor restart, so the user unit never needs a restart.
static SIGNATURE: std::sync::Mutex<Option<String>> = std::sync::Mutex::new(None);
fn signature() -> Result<String, String> {
    if let Some(s) = SIGNATURE.lock().ok().and_then(|s| s.clone()) {
        return Ok(s);
    }
    std::env::var("HYPRLAND_INSTANCE_SIGNATURE")
        .map_err(|_| "selected compositor signature missing".into())
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
    for entry in std::fs::read_dir(runtime.join("hypr"))
        .into_iter()
        .flatten()
        .filter_map(Result::ok)
    {
        let Some(sig) = entry.file_name().to_str().map(str::to_owned) else {
            continue;
        };
        if sig.contains('/') || sig.starts_with('.') {
            continue;
        }
        let Some(pid) = std::fs::read_to_string(entry.path().join("hyprland.lock"))
            .ok()
            .and_then(|t| t.lines().next()?.trim().parse::<u32>().ok())
        else {
            continue;
        };
        let Some((_, start)) = proc_start(proc_root, pid) else {
            continue;
        };
        if !std::fs::metadata(entry.path().join(".socket.sock"))
            .is_ok_and(|m| m.file_type().is_socket())
        {
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
    Some(
        text.lines()
            .filter_map(|l| l.split_once('='))
            .map(|(k, v)| (k.to_owned(), v.trim().to_owned()))
            .collect(),
    )
}
fn logind_id_ok(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 64
        && id
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_')
}
const GRAPHICAL: [&str; 3] = ["wayland", "x11", "mir"];
fn explicit_console(f: &BTreeMap<String, String>) -> bool {
    f.get("DESKTOP").map(String::as_str) == Some("tmux-console")
}
fn console_seat(f: &BTreeMap<String, String>) -> Seat {
    match f
        .get("VTNR")
        .and_then(|v| v.parse::<u32>().ok())
        .filter(|v| (1..=63).contains(v))
    {
        Some(vt) => {
            let tty = f
                .get("TTY")
                .filter(|t| {
                    t.starts_with("tty")
                        && t[3..].bytes().all(|b| b.is_ascii_digit())
                        && t.len() > 3
                })
                .map_or_else(
                    || PathBuf::from(format!("/dev/tty{vt}")),
                    |t| Path::new("/dev").join(t),
                );
            Seat::Console { vt, tty }
        }
        None => Seat::Foreign("the active tty session has no VT".into()),
    }
}
/// C-16 / INV-8: the seat view of `uid` from logind's runtime state (users/<uid>, sessions/<id>, seats/<seat>).
pub fn seat_view(state: &Path, uid: u32) -> SeatView {
    let foreign = |r: &str| SeatView {
        seat: Seat::Foreign(r.into()),
        graphical_vt: None,
    };
    let Some(user) = logind_fields(&state.join("users").join(uid.to_string())) else {
        return foreign("no logind record for this user");
    };
    let mut ids: Vec<String> = user.get("DISPLAY").into_iter().cloned().collect();
    ids.extend(
        user.get("SESSIONS")
            .map(|s| s.split_whitespace().map(str::to_owned).collect::<Vec<_>>())
            .unwrap_or_default(),
    );
    let sessions: Vec<(String, BTreeMap<String, String>)> = ids
        .iter()
        .filter(|i| logind_id_ok(i))
        .take(32)
        .filter_map(|i| logind_fields(&state.join("sessions").join(i)).map(|f| (i.clone(), f)))
        .collect();
    let seated = |f: &BTreeMap<String, String>| f.get("SEAT").is_some_and(|s| logind_id_ok(s));
    let Some(seat) = sessions
        .iter()
        .find(|(_, f)| seated(f))
        .and_then(|(_, f)| f.get("SEAT").cloned())
    else {
        return foreign("no seated session for this user");
    };
    let graphical_vt = sessions
        .iter()
        .find(|(_, f)| {
            f.get("SEAT") == Some(&seat)
                && !explicit_console(f)
                && f.get("TYPE")
                    .is_some_and(|t| GRAPHICAL.contains(&t.as_str()))
                && f.get("UID").and_then(|u| u.parse().ok()) == Some(uid)
        })
        .and_then(|(_, f)| f.get("VTNR")?.parse::<u32>().ok())
        .filter(|v| *v > 0);
    let Some(active) = logind_fields(&state.join("seats").join(&seat))
        .and_then(|f| f.get("ACTIVE").cloned())
        .filter(|a| logind_id_ok(a))
    else {
        return SeatView {
            seat: Seat::Foreign(format!("no active session on {seat}")),
            graphical_vt,
        };
    };
    let Some(f) = logind_fields(&state.join("sessions").join(&active)) else {
        return SeatView {
            seat: Seat::Foreign("active session unreadable".into()),
            graphical_vt,
        };
    };
    if f.get("UID").and_then(|u| u.parse::<u32>().ok()) != Some(uid)
        || f.get("CLASS")
            .is_some_and(|c| !["user", "user-early"].contains(&c.as_str()))
    {
        return SeatView {
            seat: Seat::Foreign(format!("another session is active on {seat}")),
            graphical_vt,
        };
    };
    let seat = match f.get("TYPE").map(String::as_str) {
        Some("tty") => console_seat(&f),
        Some(t) if GRAPHICAL.contains(&t) && !explicit_console(&f) => Seat::Graphical,
        _ if explicit_console(&f) => console_seat(&f),
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
/// One absolute remaining-job deadline for queued compositor connection, write
/// and read. A timeout cannot retract a request already accepted by the peer.
fn ipc_bounded(
    request: &str,
    timeout: Duration,
    authorize: impl Fn() -> Result<Duration, String>,
) -> Result<String, String> {
    let until = Instant::now()
        .checked_add(timeout)
        .ok_or("invalid compositor deadline")?;
    let remaining = || {
        let time = until
            .saturating_duration_since(Instant::now())
            .min(authorize()?);
        if time.is_zero() {
            Err("queued compositor deadline expired".to_owned())
        } else {
            Ok(time.min(Duration::from_millis(150)))
        }
    };
    remaining()?;
    let runtime = std::env::var("XDG_RUNTIME_DIR").map_err(|_| "XDG_RUNTIME_DIR missing")?;
    let sig = signature()?;
    if sig.contains('/') || sig.contains("..") {
        return Err("invalid compositor signature".into());
    }
    let path = Path::new(&runtime)
        .join("hypr")
        .join(sig)
        .join(".socket.sock");
    // Reuse the existing nonblocking-connect/deadline and same-UID peer check.
    let mut stream = crate::tmux_handoff::connect(&path, until)?;
    let mut pending = request.as_bytes();
    while !pending.is_empty() {
        #[cfg(test)]
        acceptance_observer::effect_boundary("compositor-before-write");
        stream
            .set_write_timeout(Some(remaining()?))
            .map_err(|e| e.to_string())?;
        match stream.write(pending) {
            Ok(0) => return Err("compositor closed while writing".into()),
            Ok(n) => pending = &pending[n..],
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
            Err(e) => return Err(e.to_string()),
        }
    }
    let mut out = Vec::new();
    let mut buffer = [0u8; 4096];
    while out.len() < 1 << 20 {
        stream
            .set_read_timeout(Some(remaining()?))
            .map_err(|e| e.to_string())?;
        let available = buffer.len().min((1 << 20) - out.len());
        match stream.read(&mut buffer[..available]) {
            Ok(0) => {
                remaining()?;
                return String::from_utf8(out).map_err(|e| e.to_string());
            }
            Ok(n) => out.extend_from_slice(&buffer[..n]),
            Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
            Err(e) => return Err(e.to_string()),
        }
    }
    Err("compositor response exceeds limit".into())
}
#[derive(Clone, Debug)]
pub struct ContextSnapshot {
    pub name: String,
    pub address: Option<String>,
    pub pid: Option<u64>,
    pub room_capture: bool,
    pub released: bool,
    sampled: Instant,
    pub class: Option<String>,
    /// C-09: the MiOS tmux client running under the focused terminal (or attached to the active VT), if any.
    pub tmux_client: Option<TmuxClient>,
    pub native_target: Option<crate::tmux_capture::Target>,
    pub native_outer: Option<(u32, u64)>,
    pub remote: bool,
    pub routing_generation: u64,
    pub focus_epoch: u64,
    /// INV-8: why nothing may be routed now (another session owns the seat); None while this user's session is active.
    pub suspended: Option<String>,
    /// C-17: the VT of this user's graphical session (the console's TOGGLE_CHORD switches to it).
    pub graphical_vt: Option<u32>,
    pub shell_overlay: bool,
    /// P3: the verified foreground app of the focused terminal or VT, the target it was verified for (the router
    /// applies it only to that target, P8) and when (older than context_stale_ms counts as none).
    pub app: Option<String>,
    pub app_target: Option<(String, u64)>,
    pub app_at: Option<Instant>,
    pub app_process: Option<mios_resolver::app_input::Process>,
}
impl ContextSnapshot {
    fn new(name: &str) -> Self {
        Self {
            name: name.into(),
            address: None,
            pid: None,
            room_capture: false,
            released: false,
            sampled: Instant::now(),
            class: None,
            tmux_client: None,
            native_target: None,
            native_outer: None,
            remote: false,
            routing_generation: 0,
            focus_epoch: 0,
            suspended: None,
            graphical_vt: None,
            shell_overlay: false,
            app: None,
            app_target: None,
            app_at: None,
            app_process: None,
        }
    }
    /// The verified app while it is fresh (P3): an unverified or stale result selects nothing.
    pub fn fresh_app(&self, stale_ms: u64) -> Option<&str> {
        self.app.as_deref().filter(|_| {
            self.app_at
                .is_some_and(|t| t.elapsed() < Duration::from_millis(stale_ms))
        })
    }
}
type AppAuthority = std::sync::Arc<std::sync::Mutex<mios_resolver::app_input::Publisher>>;
fn app_authority() -> Option<AppAuthority> {
    static SHARED: std::sync::OnceLock<
        std::sync::Mutex<std::sync::Weak<std::sync::Mutex<mios_resolver::app_input::Publisher>>>,
    > = std::sync::OnceLock::new();
    let mut slot = SHARED.get_or_init(Default::default).lock().ok()?;
    if let Some(authority) = slot.upgrade() {
        return Some(authority);
    }
    let runtime = std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from)?;
    let authority = std::sync::Arc::new(std::sync::Mutex::new(
        mios_resolver::app_input::Publisher::new(&runtime).ok()?,
    ));
    *slot = std::sync::Arc::downgrade(&authority);
    Some(authority)
}
fn publish_app_lease(
    authority: Option<&AppAuthority>,
    channel: &str,
    focus: Option<mios_resolver::app_input::Focus>,
    sampled: Instant,
    stale_ms: u64,
) {
    if let Some(authority) = authority {
        if let Ok(mut writer) = authority.lock() {
            let sampled_ns = mios_resolver::app_input::monotonic_ns()
                .saturating_sub(sampled.elapsed().as_nanos() as u64);
            let _ = writer.update(channel, focus, sampled_ns, stale_ms);
        }
    }
}
fn advance_focus(previous: &ContextSnapshot, next: &mut ContextSnapshot) {
    let changed = previous.name != next.name
        || previous.address != next.address
        || previous.pid != next.pid
        || previous.routing_generation != next.routing_generation
        || previous.suspended != next.suspended
        || previous.shell_overlay != next.shell_overlay
        || previous.tmux_client != next.tmux_client
        || previous.native_outer != next.native_outer
        || previous.app != next.app
        || previous.app_process != next.app_process;
    next.focus_epoch = if changed {
        previous.focus_epoch.saturating_add(1)
    } else {
        previous.focus_epoch
    };
}
fn publish_focus(
    cache: &std::sync::Arc<std::sync::Mutex<ContextSnapshot>>,
    mut next: ContextSnapshot,
    authority: Option<&AppAuthority>,
    stale_ms: u64,
) {
    let published = {
        let mut old = cache.lock().unwrap();
        advance_focus(&old, &mut next);
        *old = next;
        old.clone()
    };
    publish_app_lease(
        authority,
        "seat",
        app_input_focus(&published, stale_ms),
        published.sampled,
        stale_ms,
    );
}
/// A tmux client found under the focused window's process tree, by /proc alone (no spawn per focus change).
#[derive(Clone, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
pub struct TmuxClient {
    pub pid: u32,
    /// The client's tty: the `-c target-client` name.
    pub tty: String,
    /// The server socket (TMUX_TMPDIR, -L or -S resolved), so a verb reaches exactly the server that client uses.
    pub socket: PathBuf,
    /// Process and socket identities witnessed by asynchronous focus discovery.
    /// Missing legacy serialized witnesses never authorize a queued action.
    #[serde(default)]
    pub start: u64,
    #[serde(default)]
    pub instance: u64,
    #[serde(default)]
    pub socket_dev: u64,
    #[serde(default)]
    pub socket_ino: u64,
}
fn proc_parent(stat: &str) -> Option<u32> {
    let rest = &stat[stat.rfind(')')? + 1..];
    rest.split_whitespace().nth(1)?.parse().ok()
}
/// C-09: the first tmux client below `pid` whose server socket name is one of `sockets`.
pub fn tmux_client_under(
    proc_root: &Path,
    pid: u32,
    sockets: &[String],
    uid: u32,
) -> Option<TmuxClient> {
    let mut children: BTreeMap<u32, Vec<u32>> = BTreeMap::new();
    for entry in std::fs::read_dir(proc_root).ok()?.filter_map(Result::ok) {
        let Some(child) = entry
            .file_name()
            .to_str()
            .and_then(|n| n.parse::<u32>().ok())
        else {
            continue;
        };
        if let Some(parent) = std::fs::read_to_string(entry.path().join("stat"))
            .ok()
            .as_deref()
            .and_then(proc_parent)
        {
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
/// Graphical sampler cache misses enrich /proc discovery with the server's
/// client instance. Genuine older backends may report zero; observe decides
/// compatibility and still pins process/start/socket witnesses.
fn graphical_tmux_client(
    cfg: &GlobalConfig,
    pid: u32,
    uid: u32,
    budget: Duration,
) -> Option<TmuxClient> {
    if budget.is_zero() {
        return None;
    }
    let began = Instant::now();
    let raw = tmux_client_under(
        Path::new("/proc"),
        pid,
        &cfg.controller.nav.tmux_sockets,
        uid,
    )?;
    let remaining = budget.saturating_sub(began.elapsed());
    if remaining.is_zero() {
        return None;
    }
    let target = crate::tmux_capture::observe(&raw, remaining).ok()?;
    (began.elapsed() < budget).then(|| target.client())
}
pub(crate) fn tmux_client_at(
    proc_root: &Path,
    pid: u32,
    sockets: &[String],
    uid: u32,
) -> Option<TmuxClient> {
    let dir = proc_root.join(pid.to_string());
    let cmdline = std::fs::read(dir.join("cmdline")).ok()?;
    let args: Vec<String> = cmdline
        .split(|b| *b == 0)
        .filter(|a| !a.is_empty())
        .map(|a| String::from_utf8_lossy(a).into_owned())
        .collect();
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
                let v = if v.is_empty() {
                    i += 1;
                    args.get(i).cloned().unwrap_or_default()
                } else {
                    v.to_owned()
                };
                if slot == 0 {
                    name = v
                } else {
                    path = Some(PathBuf::from(v))
                }
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
            let tmpdir = env
                .split(|b| *b == 0)
                .find_map(|kv| kv.strip_prefix(b"TMUX_TMPDIR="))
                .map(|v| String::from_utf8_lossy(v).into_owned())
                .filter(|v| !v.is_empty())
                .unwrap_or_else(|| "/tmp".into());
            Path::new(&tmpdir).join(format!("tmux-{uid}")).join(&name)
        }
    };
    if !sockets.contains(&name) {
        return None;
    }
    use std::os::unix::fs::FileTypeExt;
    use std::os::unix::fs::MetadataExt;
    let socket_meta = std::fs::symlink_metadata(&socket).ok()?;
    if !socket_meta.file_type().is_socket() {
        return None;
    }
    let start = crate::nested::start_time(proc_root, pid).filter(|start| *start > 0)?;
    let tty = std::fs::read_link(dir.join("fd/0"))
        .ok()?
        .to_str()?
        .to_owned();
    let alias = tty == "/dev/tty" || tty == "/dev/console";
    if alias {
        // Aliases name this process's tty, never whichever VT is currently active.
        crate::console::vt_from_proc_stat(&std::fs::read_to_string(dir.join("stat")).ok()?)?;
    } else if !(tty.starts_with("/dev/pts/") || tty.starts_with("/dev/tty")) {
        return None;
    }
    Some(TmuxClient {
        pid,
        tty,
        socket,
        start,
        instance: 0,
        socket_dev: socket_meta.dev(),
        socket_ino: socket_meta.ino(),
    })
}
fn snapshot(cfg: &GlobalConfig, v: &serde_json::Value) -> ContextSnapshot {
    let class = v
        .get("class")
        .and_then(|v| v.as_str())
        .filter(|s| !s.is_empty());
    let room = v.get("room").and_then(|v| v.as_bool()).unwrap_or(false);
    let captured = v.get("captured").and_then(|v| v.as_bool()).unwrap_or(false);
    ContextSnapshot {
        name: cfg.classify_window(class, room, captured),
        address: v
            .get("address")
            .and_then(|v| v.as_str())
            .filter(|s| !s.is_empty())
            .map(str::to_owned),
        pid: v.get("pid").and_then(|v| v.as_u64()).filter(|p| *p > 0),
        room_capture: captured
            && (room || v.get("handler").and_then(|v| v.as_str()) == Some("hypr3d")),
        released: v.get("released").and_then(|v| v.as_bool()).unwrap_or(false),
        sampled: Instant::now(),
        class: class.map(str::to_owned),
        tmux_client: None,
        native_target: None,
        native_outer: None,
        remote: false,
        routing_generation: 0,
        focus_epoch: 0,
        suspended: None,
        graphical_vt: None,
        shell_overlay: false,
        app: None,
        app_target: None,
        app_at: None,
        app_process: None,
    }
}
/// C-09 on a VT: the MiOS tmux client attached to `tty` (directly, or below a process on that tty such as the console
/// bridge, whose child client runs on a pty). /proc only.
pub fn tmux_client_on_tty(
    proc_root: &Path,
    tty: &Path,
    sockets: &[String],
    uid: u32,
) -> Option<TmuxClient> {
    // Another user's /proc/<pid>/fd is unreadable, and only a listed server under this uid's socket dir counts.
    let mut on_tty: Vec<u32> = vec![];
    for entry in std::fs::read_dir(proc_root).ok()?.filter_map(Result::ok) {
        let Some(pid) = entry
            .file_name()
            .to_str()
            .and_then(|n| n.parse::<u32>().ok())
        else {
            continue;
        };
        let stdin = std::fs::read_link(entry.path().join("fd/0")).ok();
        let on_alias = stdin
            .as_deref()
            .is_some_and(|p| p == Path::new("/dev/tty") || p == Path::new("/dev/console"))
            && tty
                .to_str()
                .and_then(|p| p.strip_prefix("/dev/tty"))
                .and_then(|n| n.parse::<u32>().ok())
                .filter(|n| (1..=63).contains(n))
                .is_some_and(|vt| {
                    std::fs::read_to_string(entry.path().join("stat"))
                        .ok()
                        .and_then(|s| crate::console::vt_from_proc_stat(&s))
                        == Some(vt)
                });
        if stdin.as_deref() == Some(tty) || on_alias {
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
    on_tty
        .iter()
        .find_map(|pid| tmux_client_under(proc_root, *pid, sockets, uid))
}
/// What a trust decision needs from one path, from lstat (a symlink is reported as such, never followed).
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct FileFacts {
    pub uid: u32,
    pub mode: u32,
    pub dev: u64,
    pub ino: u64,
    pub kind: FileKind,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum FileKind {
    Dir,
    File,
    Other,
}
pub fn lstat_facts(path: &Path) -> Option<FileFacts> {
    use std::os::unix::fs::MetadataExt;
    let m = std::fs::symlink_metadata(path).ok()?;
    let t = m.file_type();
    Some(FileFacts {
        uid: m.uid(),
        mode: m.mode(),
        dev: m.dev(),
        ino: m.ino(),
        kind: if t.is_dir() {
            FileKind::Dir
        } else if t.is_file() {
            FileKind::File
        } else {
            FileKind::Other
        },
    })
}
/// P3 and the root ruling: an exe candidate counts only when it is exactly canonical (no symlink anywhere on its path,
/// so a link can never stand for an unlisted target), a regular file owned by `owner` (root for `exe`, the bridge's
/// uid for `user_exe`) that is neither group- nor world-writable, and every ancestor directory is owned by root or
/// `uid` and not group/world-writable (a root-owned sticky directory such as /tmp lets nobody else replace an entry).
/// Returns the file's facts, whose (dev, ino) must also be the running image. Never a basename or pattern match.
pub fn trusted_candidate(
    path: &Path,
    owner: u32,
    uid: u32,
    lstat: &dyn Fn(&Path) -> Option<FileFacts>,
) -> Option<FileFacts> {
    if !path.is_absolute()
        || path.components().any(|c| {
            !matches!(
                c,
                std::path::Component::RootDir | std::path::Component::Normal(_)
            )
        })
    {
        return None;
    }
    let mut dir = PathBuf::new();
    let mut parts = path.components().peekable();
    while let Some(part) = parts.next() {
        dir.push(part);
        let f = lstat(&dir)?;
        if parts.peek().is_none() {
            return (f.kind == FileKind::File && f.uid == owner && f.mode & 0o022 == 0)
                .then_some(f);
        }
        let sticky_root = f.uid == 0 && f.mode & 0o1000 != 0;
        if f.kind != FileKind::Dir
            || !(f.uid == 0 || f.uid == uid)
            || (f.mode & 0o022 != 0 && !sticky_root)
        {
            return None;
        }
    }
    None
}
/// P3: the app whose registered candidate is exactly the running image of `pid`: /proc/<pid> owned by `uid`, its exe
/// link naming a listed path verbatim (a `(deleted)` image never does), the candidate trusted, and the same inode.
pub fn app_of_pid(
    proc_root: &Path,
    pid: u32,
    apps: &BTreeMap<String, crate::nav::NavApp>,
    uid: u32,
    lstat: &dyn Fn(&Path) -> Option<FileFacts>,
) -> Option<String> {
    use std::os::unix::fs::MetadataExt;
    if pid == 0 {
        return None;
    }
    let dir = proc_root.join(pid.to_string());
    if std::fs::metadata(&dir).ok()?.uid() != uid {
        return None;
    }
    let target = std::fs::read_link(dir.join("exe")).ok()?;
    let running = std::fs::metadata(dir.join("exe")).ok()?;
    for (name, app) in apps {
        for (list, owner) in [(&app.exe, 0), (&app.user_exe, uid)] {
            for candidate in list {
                if Path::new(candidate) != target {
                    continue;
                }
                if trusted_candidate(Path::new(candidate), owner, uid, lstat)
                    .is_some_and(|f| (f.dev, f.ino) == (running.dev(), running.ino()))
                {
                    return Some(name.clone());
                }
            }
        }
    }
    None
}
/// /proc/<pid>/stat: (pgrp, tpgid), the process group and its tty's foreground process group.
fn proc_groups(proc_root: &Path, pid: u32) -> Option<(u32, u32)> {
    let stat = std::fs::read_to_string(proc_root.join(pid.to_string()).join("stat")).ok()?;
    let mut f = stat[stat.rfind(')')? + 1..].split_whitespace();
    let pgrp = f.nth(2)?.parse().ok()?;
    let tpgid: i64 = f.nth(2)?.parse().ok()?;
    Some((pgrp, u32::try_from(tpgid).ok().filter(|t| *t > 0)?))
}
/// P3: the foreground app among `pids`: a process that leads its tty's foreground group (pgrp == tpgid), checked by
/// app_of_pid. The plain terminal passes the focused window's process tree, a VT the processes on its tty.
pub fn foreground_app_among(
    proc_root: &Path,
    pids: &[u32],
    apps: &BTreeMap<String, crate::nav::NavApp>,
    uid: u32,
    lstat: &dyn Fn(&Path) -> Option<FileFacts>,
) -> Option<String> {
    pids.iter().find_map(|pid| {
        proc_groups(proc_root, *pid)
            .filter(|(pgrp, tpgid)| pgrp == tpgid && pgrp == pid)
            .and_then(|_| app_of_pid(proc_root, *pid, apps, uid, lstat))
    })
}
/// P3 tmux: the foreground group leader of a pane (`pane_pid`'s tpgid) checked by app_of_pid.
pub fn pane_app(
    proc_root: &Path,
    pane_pid: u32,
    apps: &BTreeMap<String, crate::nav::NavApp>,
    uid: u32,
    lstat: &dyn Fn(&Path) -> Option<FileFacts>,
) -> Option<String> {
    let (_, tpgid) = proc_groups(proc_root, pane_pid)?;
    app_of_pid(proc_root, tpgid, apps, uid, lstat)
}
/// The processes below `pid` (breadth first, depth 8, at most 4096 nodes), as tmux_client_under walks them.
fn descendants(proc_root: &Path, pid: u32) -> Vec<u32> {
    let mut children: BTreeMap<u32, Vec<u32>> = BTreeMap::new();
    for entry in std::fs::read_dir(proc_root)
        .into_iter()
        .flatten()
        .filter_map(Result::ok)
    {
        let Some(child) = entry
            .file_name()
            .to_str()
            .and_then(|n| n.parse::<u32>().ok())
        else {
            continue;
        };
        if let Some(parent) = std::fs::read_to_string(entry.path().join("stat"))
            .ok()
            .as_deref()
            .and_then(proc_parent)
        {
            children.entry(parent).or_default().push(child);
        }
    }
    let mut out = vec![];
    let mut queue = std::collections::VecDeque::from([(pid, 0u8)]);
    while let Some((p, depth)) = queue.pop_front() {
        if out.len() >= 4096 {
            break;
        }
        if p != pid {
            out.push(p);
        }
        if depth < 8 {
            for c in children.get(&p).into_iter().flatten() {
                queue.push_back((*c, depth + 1));
            }
        }
    }
    out
}
/// The processes whose stdin is `tty` (or a /dev/tty, /dev/console alias on that VT), as tmux_client_on_tty reads them.
fn on_tty(proc_root: &Path, tty: &Path) -> Vec<u32> {
    let mut out: Vec<u32> = vec![];
    let vt = tty
        .to_str()
        .and_then(|p| p.strip_prefix("/dev/tty"))
        .and_then(|n| n.parse::<u32>().ok())
        .filter(|n| (1..=63).contains(n));
    for entry in std::fs::read_dir(proc_root)
        .into_iter()
        .flatten()
        .filter_map(Result::ok)
    {
        let Some(pid) = entry
            .file_name()
            .to_str()
            .and_then(|n| n.parse::<u32>().ok())
        else {
            continue;
        };
        let stdin = std::fs::read_link(entry.path().join("fd/0")).ok();
        let on_alias = stdin
            .as_deref()
            .is_some_and(|p| p == Path::new("/dev/tty") || p == Path::new("/dev/console"))
            && vt.is_some_and(|vt| {
                std::fs::read_to_string(entry.path().join("stat"))
                    .ok()
                    .and_then(|s| crate::console::vt_from_proc_stat(&s))
                    == Some(vt)
            });
        if stdin.as_deref() == Some(tty) || on_alias {
            out.push(pid);
        }
        if out.len() > 256 {
            break;
        }
    }
    out.sort_unstable();
    out
}
/// Where the focused context's foreground app is verified (P3): the identity the result is bound to (P8).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum AppProbe {
    /// A tmux client (under the focused terminal, or on the active VT): its active pane.
    Tmux {
        target: Option<(String, u64)>,
        client: TmuxClient,
    },
    /// A terminal without a MiOS tmux client: the focused window's process tree.
    Plain { target: (String, u64), pid: u32 },
    /// A bare VT: the processes on its tty.
    Vt { tty: PathBuf },
}
impl AppProbe {
    /// The window target the result is verified for (None on a VT).
    pub fn target(&self) -> Option<(String, u64)> {
        match self {
            Self::Tmux { target, .. } => target.clone(),
            Self::Plain { target, .. } => Some(target.clone()),
            Self::Vt { .. } => None,
        }
    }
    #[cfg(test)]
    fn verify(&self, cfg: &GlobalConfig, uid: u32) -> Option<String> {
        self.identity(cfg, uid).map(|(app, _)| app)
    }
    fn identity(
        &self,
        cfg: &GlobalConfig,
        uid: u32,
    ) -> Option<(String, mios_resolver::app_input::Process)> {
        let nav = &cfg.controller.nav;
        let proc_root = Path::new("/proc");
        let pids = match self {
            Self::Tmux { client, .. } => {
                vec![
                    crate::tmux_capture::observe(
                        client,
                        Duration::from_millis(nav.app_query_timeout_ms),
                    )
                    .ok()?
                    .foreground,
                ]
            }
            Self::Plain { pid, .. } => descendants(proc_root, *pid),
            Self::Vt { tty } => on_tty(proc_root, tty),
        };
        pids.into_iter().find_map(|pid| {
            proc_groups(proc_root, pid).filter(|(pgrp, tpgid)| *pgrp == pid && *tpgid == pid)?;
            let name = app_of_pid(proc_root, pid, &nav.apps, uid, &lstat_facts)?;
            Some((
                name,
                mios_resolver::app_input::Process {
                    pid,
                    start: proc_start(proc_root, pid)?.1,
                },
            ))
        })
    }
}
/// P3: one verified result per probe identity. A focus, pid or tmux-client change is a new identity and verifies at
/// once; the same identity re-verifies at most every app_poll_ms. A result is never reused for another identity.
struct AppObservation {
    probe: AppProbe,
    at: Instant,
    identity: Option<(String, mios_resolver::app_input::Process)>,
}
#[derive(Default)]
pub struct AppCache {
    last: Option<(AppProbe, Instant, Option<String>)>,
    instance: Option<AppObservation>,
}
impl AppCache {
    pub fn get(
        &mut self,
        probe: AppProbe,
        poll: Duration,
        verify: impl FnOnce(&AppProbe) -> Option<String>,
    ) -> (Option<String>, Instant) {
        if let Some((p, at, app)) = &self.last {
            if *p == probe && at.elapsed() < poll {
                return (app.clone(), *at);
            }
        }
        let app = verify(&probe);
        let at = Instant::now();
        self.last = Some((probe, at, app.clone()));
        (app, at)
    }
}
/// Whether a context can select an app (P0, P3): nav and an app layer are configured for its base context.
fn wants_app(cfg: &GlobalConfig, context: &str) -> bool {
    let nav = &cfg.controller.nav;
    nav.profile_name(context, false).is_some()
        && nav.apps.values().any(|a| a.contexts.contains_key(context))
}
/// Fill `snapshot.app` for the focused terminal or VT (the context worker; never the forwarding loop).
fn verify_app(
    cfg: &GlobalConfig,
    snapshot: &mut ContextSnapshot,
    nav_context: &str,
    console_tty: Option<&Path>,
    cache: &mut AppCache,
    uid: u32,
) {
    snapshot.app = None;
    snapshot.app_target = None;
    snapshot.app_at = None;
    snapshot.app_process = None;
    if !wants_app(cfg, nav_context) {
        return;
    }
    let target = snapshot.address.clone().zip(snapshot.pid);
    let probe = match (&snapshot.tmux_client, console_tty) {
        (Some(client), _) => AppProbe::Tmux {
            target: target.clone(),
            client: client.clone(),
        },
        (None, Some(tty)) => AppProbe::Vt { tty: tty.into() },
        (None, None) => match (&target, snapshot.pid.and_then(|p| u32::try_from(p).ok())) {
            (Some(t), Some(pid)) => AppProbe::Plain {
                target: t.clone(),
                pid,
            },
            _ => return,
        },
    };
    let bound = probe.target();
    let mut observed = None;
    let (app, at) = cache.get(
        probe.clone(),
        Duration::from_millis(cfg.controller.nav.app_poll_ms),
        |p| {
            let identity = p.identity(cfg, uid);
            let app = identity.as_ref().map(|(name, _)| name.clone());
            observed = Some(identity);
            app
        },
    );
    if let Some(identity) = observed {
        cache.instance = Some(AppObservation {
            probe: probe.clone(),
            at,
            identity,
        });
    }
    snapshot.app_process = cache
        .instance
        .as_ref()
        .filter(|observation| observation.probe == probe && observation.at == at)
        .and_then(|observation| observation.identity.as_ref())
        .filter(|(name, _)| Some(name) == app.as_ref())
        .map(|(_, process)| process.clone());
    snapshot.app = app;
    snapshot.app_target = bound;
    snapshot.app_at = Some(at);
}
/// Whether this context's nav mapping chooses between `<name>_tmux` and `<name>_plain`, so discovery is worth a scan.
fn wants_tmux(cfg: &GlobalConfig, context: &str) -> bool {
    let nav = &cfg.controller.nav;
    nav.enabled
        && nav.contexts.get(context).is_some_and(|p| {
            !nav.profiles.contains_key(p) && nav.profiles.contains_key(&format!("{p}_tmux"))
        })
}
/// The capture predicate (INV-4), identical for every window kind: fullscreen==2 (when capture_fullscreen),
/// capture_tag or the room's input_owner capture a window, and release_tag cancels all three. When the generated
/// session exposes MIOS_INPUT_POLICY.captured (G1) that one predicate is used, so Lua and inputd cannot disagree.
pub fn context_lua(cfg: &GlobalConfig) -> String {
    let template = r#"local function q(s) return '"'..tostring(s or ''):gsub('[%z\1-\31\\"]',function(c) return string.format('\\u%04x',c:byte()) end)..'"' end
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
    template
        .replace(
            "CAPTURE_FULLSCREEN",
            if cfg.controller.capture_fullscreen {
                "true"
            } else {
                "false"
            },
        )
        .replace(
            "CAPTURE_TAG",
            &serde_json::to_string(&cfg.controller.capture_tag).unwrap(),
        )
        .replace(
            "RELEASE_TAG",
            &serde_json::to_string(&cfg.controller.release_tag).unwrap(),
        )
}
fn controller_cursor_lua(cfg: &GlobalConfig, enabled: bool) -> String {
    let enabled = enabled
        && !cfg.controller.cursor_shape.is_empty()
        && !cfg.controller.virtual_pointer.is_empty();
    let args = if enabled {
        format!(
            "true,{},{},{}",
            serde_json::to_string(&cfg.controller.cursor_shape).unwrap(),
            serde_json::to_string(&cfg.controller.virtual_pointer).unwrap(),
            cfg.controller.context_stale_ms
        )
    } else {
        "false".into()
    };
    format!("do local p=hl.plugin and hl.plugin.mios_pacing;if p and p.controller_cursor then pcall(p.controller_cursor,{args}) end end\n")
}
fn context_with_cursor(cfg: &GlobalConfig, enabled: bool) -> Result<ContextSnapshot, String> {
    context_query(
        cfg,
        &format!(
            "{}{}",
            controller_cursor_lua(cfg, enabled),
            context_lua(cfg)
        ),
    )
}
/// Private per-session shell state is a short lease; closed, stale, malformed, symlinked or foreign files attach no input.
fn shell_overlay_open(runtime: &Path, cfg: &GlobalConfig) -> bool {
    use std::os::unix::fs::{MetadataExt, OpenOptionsExt};
    if cfg.shell_input_state_file.is_empty() {
        return false;
    }
    let Ok(mut file) = std::fs::OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(runtime.join(&cfg.shell_input_state_file))
    else {
        return false;
    };
    let Ok(meta) = file.metadata() else {
        return false;
    };
    if !meta.is_file() || meta.uid() != unsafe { libc::getuid() } || meta.len() > 4096 {
        return false;
    }
    let mut bytes = Vec::new();
    if std::io::Read::by_ref(&mut file)
        .take(4097)
        .read_to_end(&mut bytes)
        .is_err()
        || bytes.len() > 4096
    {
        return false;
    }
    let Ok(state) = serde_json::from_slice::<serde_json::Value>(&bytes) else {
        return false;
    };
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0);
    state["version"].as_u64() == Some(1)
        && state["open"].as_bool() == Some(true)
        && matches!(state["surface"].as_str(), Some("drawer" | "search"))
        && state["output"].as_str().is_some_and(|s| !s.is_empty())
        && state["timestamp_ms"]
            .as_u64()
            .is_some_and(|t| now.abs_diff(t) < cfg.controller.context_stale_ms)
}
pub fn context(cfg: &GlobalConfig) -> Result<ContextSnapshot, String> {
    context_query(cfg, &context_lua(cfg))
}
/// `mios-inputd context` (P9): the focused window, plus the tmux client, the verified foreground app (one bounded
/// query, app_query_timeout_ms) and the nav profile a router selects for exactly that snapshot.
pub fn native_console_context(cfg: &GlobalConfig) -> Result<ContextSnapshot, String> {
    let uid = unsafe { libc::getuid() };
    let Seat::Console { tty, .. } = seat_view(Path::new("/run/systemd"), uid).seat else {
        return Err("this user's local console session is not active".into());
    };
    let client = tmux_client_on_tty(
        Path::new("/proc"),
        &tty,
        &cfg.controller.nav.tmux_sockets,
        uid,
    )
    .ok_or("active console has no witnessed MiOS tmux client")?;
    native_snapshot(cfg, &client)
}
pub fn native_console_active() -> bool {
    matches!(
        seat_view(Path::new("/run/systemd"), unsafe { libc::getuid() }).seat,
        Seat::Console { .. }
    )
}
pub fn context_report(
    cfg: &GlobalConfig,
    legacy: &LegacyConfig,
) -> Result<serde_json::Value, String> {
    let uid = unsafe { libc::getuid() };
    let console = matches!(
        seat_view(Path::new("/run/systemd"), uid).seat,
        Seat::Console { .. }
    );
    let mut c = if let Some((target, owned, remote)) = crate::tmux_capture::focused_target() {
        let mut snap = ContextSnapshot::new(if owned { "app-capture" } else { "console" });
        snap.address = Some(target.address());
        snap.pid = Some(u64::from(target.foreground));
        snap.class = Some("console".into());
        snap.tmux_client = Some(target.client());
        snap.routing_generation = target.login_generation;
        snap.native_target = Some(target);
        snap.remote = remote;
        snap
    } else if console {
        native_console_context(cfg)?
    } else {
        context(cfg)?
    };
    let uid = unsafe { libc::getuid() };
    c.shell_overlay = std::env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .is_some_and(|r| shell_overlay_open(&r, cfg));
    let nav_context =
        capture_navigation_context(cfg, c.class.as_deref()).unwrap_or_else(|| c.name.clone());
    if c.native_target.is_none() {
        if let (Some(pid), true) = (c.pid, wants_tmux(cfg, &nav_context)) {
            c.tmux_client = u32::try_from(pid).ok().and_then(|p| {
                tmux_client_under(Path::new("/proc"), p, &cfg.controller.nav.tmux_sockets, uid)
            });
        }
    }
    if !c.shell_overlay && c.name != "desktop-3d" {
        verify_app(
            cfg,
            &mut c,
            &nav_context,
            None,
            &mut AppCache::default(),
            uid,
        );
    }
    let mut router = Router::new(cfg.clone(), legacy.clone(), BTreeMap::new());
    router.set_shell_overlay(c.shell_overlay);
    router.set_capture_navigation_context(
        capture_navigation_context(cfg, c.class.as_deref()).as_deref(),
    );
    router.set_tmux(c.tmux_client.is_some());
    router.set_focus(&c.name, c.address.clone().zip(c.pid), c.room_capture);
    router.set_app(c.app.as_deref(), c.app_target.as_ref());
    Ok(
        serde_json::json!({"context":c.name,"address":c.address,"pid":c.pid,"room_capture":c.room_capture,
        "released":c.released,"controller_owner":if Router::owns_controller(&c.name) {"app"} else {"mios"},
        "tmux":c.tmux_client.is_some(),"native_tmux":c.native_target,"remote":c.remote,"routing_generation":c.routing_generation,"app":router.app(),"nav_profile":router.nav_profile(),"camera":router.camera()}),
    )
}
fn context_query(cfg: &GlobalConfig, lua: &str) -> Result<ContextSnapshot, String> {
    let reply = ipc(&format!("/eval {lua}"))?;
    let json = reply
        .split_once("MIOS_INPUT_CONTEXT:")
        .and_then(|(_, v)| v.lines().next())
        .ok_or("compositor context response missing")?;
    let value: serde_json::Value =
        serde_json::from_str(json).map_err(|e| format!("invalid native context: {e}"))?;
    Ok(snapshot(cfg, &value))
}
/// The ownership transaction for `input:window on|fullscreen|off` (C-02), the same for every window kind.
/// on/fullscreen: remove release_tag, add capture_tag; fullscreen also sets fullscreen and marks it with
/// capture_fullscreen_tag when the bridge set it. off: remove capture_tag, add release_tag, and unset a fullscreen only
/// when capture_fullscreen_tag says the bridge set it. In the room the plugin selects the aimed window and owns its
/// fullscreen. `expect` (the focused window address the router saw) guards a 2D capture against a focus race.
pub fn window_lua(mode: &str, cfg: &GlobalConfig, expect: Option<&str>) -> Result<String, String> {
    if !["on", "off", "fullscreen"].contains(&mode) {
        return Err("unsupported window ownership mode".into());
    }
    let s = |v: &str| serde_json::to_string(v).unwrap();
    let c = &cfg.controller;
    Ok(format!(
        r#"local p=hl.plugin and hl.plugin.hypr3d;local mode={mode};local CAP={cap};local REL={rel};local FS={fs};local EXPECT={expect}
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
 if EXPECT~='' and mode=='fullscreen' and tostring(id)~=EXPECT then error('MIOS_INPUT_WINDOW_FAIL: room ownership changed',0) end
 assert(p.input_capture(mode),'no selected room window')
 if mode~='off' then
  id=p.input_owner()
  if EXPECT~='' and mode=='fullscreen' and tostring(id)~=EXPECT then
   p.input_capture('off');error('MIOS_INPUT_WINDOW_FAIL: room target changed; capture cancelled',0)
  end
 end
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
error('MIOS_INPUT_WINDOW_OK:'..tostring(target and target.address or '')..' '..tostring(target and target.class or '')..'\nMIOS_INPUT_WINDOW_PID:'..tostring(target and target.pid or 0),0)"#,
        mode = s(mode),
        cap = s(&c.capture_tag),
        rel = s(&c.release_tag),
        fs = s(&c.capture_fullscreen_tag),
        expect = s(expect.unwrap_or(""))
    ))
}
/// Run one ownership transaction against the selected compositor; Ok((address, class)) only on the success marker.
pub fn window_transaction(
    mode: &str,
    cfg: &GlobalConfig,
    expect: Option<&str>,
) -> Result<(String, String), String> {
    capture_transaction(mode, cfg, expect).map(|r| (r.address, r.class))
}
fn capture_transaction(
    mode: &str,
    cfg: &GlobalConfig,
    expect: Option<&str>,
) -> Result<CaptureReceipt, String> {
    let reply = ipc(&format!("/eval {}", window_lua(mode, cfg, expect)?))?;
    let rest = reply
        .split_once("MIOS_INPUT_WINDOW_OK:")
        .map(|(_, r)| r)
        .ok_or_else(|| reply.trim().chars().take(400).collect::<String>())?;
    let line = rest.lines().next().unwrap_or("").trim();
    let (address, class) = line.split_once(' ').unwrap_or((line, ""));
    let pid = reply
        .split_once("MIOS_INPUT_WINDOW_PID:")
        .and_then(|(_, r)| r.lines().next()?.trim().parse::<u32>().ok())
        .unwrap_or(0);
    let mut receipt = CaptureReceipt {
        native: None,
        address: address.into(),
        class: class.trim().into(),
        pid,
        tmux: None,
        error: None,
    };
    if mode != "off" && capture_navigation_context(cfg, Some(&receipt.class)).is_some() {
        if pid == 0 {
            receipt.error = Some("captured terminal process identity missing".into());
        } else {
            match captured_tmux(cfg, pid) {
                Ok(target) => receipt.tmux = target,
                Err(error) => receipt.error = Some(error),
            }
        }
    }
    Ok(receipt)
}
fn inspect_capture(cfg: &GlobalConfig, expect: Option<&str>) -> Result<CaptureReceipt, String> {
    let snapshot = context(cfg)?;
    if snapshot.name != "app-capture" || snapshot.address.as_deref() != expect {
        return Err("capture recovery focus changed".into());
    }
    let address = snapshot.address.ok_or("captured window missing")?;
    let pid = snapshot
        .pid
        .and_then(|p| u32::try_from(p).ok())
        .ok_or("captured process missing")?;
    let class = snapshot.class.unwrap_or_default();
    let target = captured_tmux(cfg, pid);
    Ok(CaptureReceipt {
        native: None,
        address,
        class,
        pid,
        tmux: target.as_ref().ok().cloned().flatten(),
        error: target.err(),
    })
}
/// C-32: the capture OSD text. The release chord is named from toggle_chord, so a remap never shows a stale label.
pub fn osd_message(mode: &str, class: &str, toggle_chord: &[String]) -> String {
    let chord = toggle_chord
        .iter()
        .map(|b| match b.as_str() {
            "BTN_THUMBL" => "L3".to_owned(),
            "BTN_THUMBR" => "R3".to_owned(),
            o => o.trim_start_matches("BTN_").to_owned(),
        })
        .collect::<Vec<_>>()
        .join("+");
    let app = if class.is_empty() {
        "the focused window"
    } else {
        class
    };
    if mode == "off" {
        format!("Controller -> MiOS. {app} released.")
    } else {
        format!("Controller -> {app}. Hold {chord} to release.")
    }
}
/// Run a short-lived helper with a hard deadline; its stdout (bounded) is the result.
pub(crate) fn run_bounded(argv: &[String], timeout: Duration) -> Result<String, String> {
    run_bounded_cancel(argv, timeout, None)
}
/// Nonblocking bounded output, with a private process group for cancellation. A descendant retaining stdout cannot
/// extend the deadline or stall the input loop. Killing this group never signals the caller's group or tmux server.
pub(crate) fn control_command(program: &str) -> Command {
    let mut command = Command::new(program);
    if matches!(
        Path::new(program).file_name().and_then(|s| s.to_str()),
        Some("tmux" | "mios-tmux")
    ) {
        // tmux's control protocol has a bounded identify environment; app children keep their own full environment.
        let minimal: Vec<_> = std::env::vars_os()
            .filter(|(k, _)| {
                let k = k.to_string_lossy();
                matches!(
                    k.as_ref(),
                    "HOME"
                        | "PATH"
                        | "TERM"
                        | "LANG"
                        | "USER"
                        | "LOGNAME"
                        | "SHELL"
                        | "TMPDIR"
                        | "TMUX_TMPDIR"
                ) || k.starts_with("LC_")
            })
            .collect();
        command.env_clear().envs(minimal);
    }
    command
}
fn run_bounded_cancel(
    argv: &[String],
    timeout: Duration,
    cancelled: Option<&AtomicBool>,
) -> Result<String, String> {
    use std::os::fd::AsRawFd;
    use std::os::unix::process::CommandExt;
    let (program, rest) = argv.split_first().ok_or("empty argv")?;
    if timeout.is_zero() || cancelled.is_some_and(|c| c.load(Ordering::Relaxed)) {
        return Err("input action expired or cancelled".into());
    }
    let mut command = control_command(program);
    let mut child = command
        .args(rest)
        .env_remove("TMUX")
        .env_remove("TMUX_PANE")
        .process_group(0)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|e| e.to_string())?;
    let group = child.id() as i32;
    let finish = |child: &mut Child| {
        // SAFETY: group is the positive PID of our child, created with process_group(0), never our own group.
        unsafe {
            libc::kill(-group, libc::SIGKILL);
        }
        let _ = child.wait();
    };
    let mut stdout = child.stdout.take().ok_or("command stdout missing")?;
    // SAFETY: stdout owns this valid descriptor; fcntl only changes its nonblocking flag.
    let flags = unsafe { libc::fcntl(stdout.as_raw_fd(), libc::F_GETFL) };
    if flags < 0
        || unsafe { libc::fcntl(stdout.as_raw_fd(), libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0
    {
        let err = std::io::Error::last_os_error().to_string();
        finish(&mut child);
        return Err(err);
    }
    let deadline = Instant::now() + timeout;
    let mut bytes = Vec::new();
    loop {
        if cancelled.is_some_and(|c| c.load(Ordering::Relaxed)) || Instant::now() >= deadline {
            finish(&mut child);
            return Err(format!("{program} timed out or cancelled"));
        }
        let mut chunk = [0u8; 4096];
        loop {
            match stdout.read(&mut chunk) {
                Ok(0) => break,
                Ok(n) => {
                    if bytes.len() + n > 1 << 16 {
                        finish(&mut child);
                        return Err("command output limit exceeded".into());
                    }
                    bytes.extend_from_slice(&chunk[..n]);
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                Err(e) => {
                    finish(&mut child);
                    return Err(e.to_string());
                }
            }
            if Instant::now() >= deadline {
                break;
            }
        }
        match child.try_wait() {
            Ok(Some(status)) => {
                // Drain only immediately available bytes, never wait on descendants' copies of stdout.
                loop {
                    match stdout.read(&mut chunk) {
                        Ok(0) => break,
                        Ok(n) if bytes.len() + n <= 1 << 16 => bytes.extend_from_slice(&chunk[..n]),
                        Ok(_) => {
                            finish(&mut child);
                            return Err("command output limit exceeded".into());
                        }
                        Err(e) if e.kind() == std::io::ErrorKind::Interrupted => continue,
                        Err(_) => break,
                    }
                }
                finish(&mut child);
                return if status.success() {
                    String::from_utf8(bytes).map_err(|e| e.to_string())
                } else {
                    Err(format!("{program} exited {status}"))
                };
            }
            Ok(None) => {}
            Err(e) => {
                finish(&mut child);
                return Err(e.to_string());
            }
        }
        std::thread::sleep(Duration::from_millis(5));
    }
}
/// C-30: argv of a tmux verb that needs no reply, against the exact server and client discovery found.
pub fn tmux_argv(
    cfg: &GlobalConfig,
    client: &TmuxClient,
    verb: &str,
) -> Result<Vec<String>, String> {
    crate::nav::validate_tmux(verb, &cfg.tmux_prefix_keys)?;
    let base = |rest: &[&str]| -> Vec<String> {
        ["/usr/bin/tmux", "-S"]
            .into_iter()
            .map(str::to_owned)
            .chain([client.socket.to_string_lossy().into_owned()])
            .chain(rest.iter().map(|s| (*s).to_owned()))
            .collect()
    };
    let words: Vec<_> = verb.split_whitespace().collect();
    Ok(match words.as_slice() {
        ["key", k] => base(&["send-keys", "-c", &client.tty, "-K", &cfg.tmux_prefix, k]),
        ["desktop", action] => {
            fn quote(s: &str) -> String {
                format!("'{}'", s.replace('\'', "'\"'\"'"))
            }
            // Literal caller values cannot become tmux format code; only these fixed pane witnesses expand.
            let socket = quote(&client.socket.to_string_lossy().replace('#', "##"));
            let name = quote(&client.tty.replace('#', "##"));
            let command=format!("/usr/bin/mios-gen tmux-os desktop {action} --socket {socket} --client {name} --pane '#{{pane_id}}' --expected-pid '#{{pane_pid}}' --expected-window '#{{window_id}}'");
            base(&["run-shell", "-b", "-t", &client.tty, &command])
        }
        ["session-prev"] => base(&["switch-client", "-c", &client.tty, "-p"]),
        ["session-next"] => base(&["switch-client", "-c", &client.tty, "-n"]),
        // U1: prefix + [keybindings.tmux].menu_key opens the generated MiOS menu on exactly this client.
        ["menu"] => match &cfg.tmux_menu_key {
            Some(k) => base(&["send-keys", "-c", &client.tty, "-K", &cfg.tmux_prefix, k]),
            None => {
                return Err(
                    "tmux:menu needs [keybindings.tmux].menu_key (the generated MiOS menu)".into(),
                )
            }
        },
        ["focus", d] => base(&[
            "display-message",
            "-p",
            "-c",
            &client.tty,
            "-t",
            &client.tty,
            &format!("#{{pane_at_{d}}}"),
        ]),
        ["page-up"] | ["page-down"] => base(&[
            "display-message",
            "-p",
            "-c",
            &client.tty,
            "-t",
            &client.tty,
            "#{pane_id} #{pane_in_mode}",
        ]),
        // R-21: the controller map in a popup on exactly this client; argv form, so tmux runs no shell.
        ["keymap"] => {
            let file = keymap_path().to_string_lossy().into_owned();
            let mut v = base(&[
                "display-popup",
                "-c",
                &client.tty,
                "-E",
                "-w",
                "90%",
                "-h",
                "90%",
                "-T",
                " MiOS controller ",
            ]);
            v.extend(
                cfg.controller
                    .console
                    .keymap_argv
                    .iter()
                    .map(|a| a.replace("{file}", &file)),
            );
            v
        }
        _ => return Err(format!("tmux:{verb} has no runtime projection")),
    })
}
/// The rendered controller map the tmux:keymap popup pages (`mios-inputd map --text`), private to this user.
pub fn keymap_path() -> PathBuf {
    // SAFETY: getuid has no memory/ownership effects.
    let runtime = std::env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(format!("/run/user/{}", unsafe { libc::getuid() })));
    runtime.join("mios-inputd-keymap.txt")
}
fn write_private(path: &Path, text: &str) -> Result<(), String> {
    use std::os::unix::fs::OpenOptionsExt;
    let tmp = path.with_extension(format!("tmp-{}", std::process::id()));
    let _ = std::fs::remove_file(&tmp);
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW | libc::O_CLOEXEC)
        .open(&tmp)
        .map_err(|e| e.to_string())?;
    let r = f
        .write_all(text.as_bytes())
        .and_then(|_| std::fs::rename(&tmp, path));
    if r.is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
    r.map_err(|e| e.to_string())
}
/// Run a tmux verb (C-30), including the two-step ones: `focus` falls through to the compositor at the pane edge (on
/// the desktop; on a VT it stops there), `keymap` renders the map file first.
#[cfg(test)]
fn run_tmux(
    cfg: &GlobalConfig,
    client: &TmuxClient,
    verb: &str,
    console: bool,
) -> Result<Vec<String>, String> {
    run_tmux_guarded(cfg, client, verb, console, None, || {
        Ok(Duration::from_secs(1))
    })
}
fn run_tmux_guarded(
    cfg: &GlobalConfig,
    client: &TmuxClient,
    verb: &str,
    console: bool,
    window: Option<&(String, u64)>,
    remaining: impl Fn() -> Result<Duration, String>,
) -> Result<Vec<String>, String> {
    let t = Duration::from_millis(1000);
    remaining()?;
    let run = |argv: &[String]| -> Result<String, String> {
        let began = Instant::now();
        let budget = t.min(remaining()?);
        if client.start == 0 || client.socket_ino == 0 {
            return Err("queued tmux client has no process/socket witness".into());
        }
        // This blocking check runs only in the worker, after authority locks
        // have been released. A cached tty cannot authorize its next occupant.
        crate::tmux_capture::observe(client, budget)?;
        let budget = budget.saturating_sub(began.elapsed()).min(remaining()?);
        if budget.is_zero() {
            return Err("queued tmux action deadline expired during client check".into());
        }
        // Legacy tty-targeted commands have no atomic server-side identity
        // transaction; this is a fresh admission check, not cancellation of an
        // already accepted command or a guarantee across a disconnect race.
        let reply = run_bounded(argv, budget)?;
        #[cfg(test)]
        acceptance_observer::effect_boundary("tmux-after-step");
        Ok(reply)
    };
    let argv = tmux_argv(cfg, client, verb)?;
    let words: Vec<_> = verb.split_whitespace().collect();
    let mut ran = vec![argv[3..].join(" ")];
    let sock = client.socket.to_string_lossy().into_owned();
    let tmux = |rest: &[&str]| -> Vec<String> {
        ["/usr/bin/tmux", "-S", &sock]
            .into_iter()
            .map(str::to_owned)
            .chain(rest.iter().map(|s| (*s).to_owned()))
            .collect()
    };
    match words.as_slice() {
        ["keymap"] => {
            write_private(
                &keymap_path(),
                &cfg.controller.nav.text(cfg.controller.capture_hold_ms),
            )?;
            run(&argv)?;
        }
        ["focus", d] => {
            let edge = run(&argv)?.trim() == "1";
            if edge && console {
                ran.push("edge: a VT has no compositor focus".into());
            } else if edge {
                let action = format!("hypr:movefocus {d}");
                let lua = command_for(&action)?.remove(2);
                let lua = window_scoped_lua(&action, &lua, window)?;
                let reply = ipc_bounded(&format!("/eval {lua}"), remaining()?, &remaining)?;
                if reply.trim() != "ok" {
                    return Err("queued compositor action was not acknowledged".into());
                }
                ran.push(format!("hypr:movefocus {d} -> {}", reply.trim()));
            } else {
                let k = match *d {
                    "left" => "h",
                    "down" => "j",
                    "up" => "k",
                    _ => "l",
                };
                let step = tmux(&["send-keys", "-c", &client.tty, "-K", &cfg.tmux_prefix, k]);
                run(&step)?;
                ran.push(step[3..].join(" "));
            }
        }
        ["page-up"] | ["page-down"] => {
            let out = run(&argv)?;
            let mut it = out.split_whitespace();
            let (pane, mode) = (it.next().unwrap_or("").to_owned(), it.next() == Some("1"));
            if !pane.starts_with('%') {
                return Err("tmux did not report the client's pane".into());
            }
            let step = if words[0] == "page-up" {
                tmux(&["copy-mode", "-eu", "-t", &pane])
            } else if mode {
                tmux(&["send-keys", "-t", &pane, "-X", "page-down"])
            } else {
                return Ok(ran);
            };
            run(&step)?;
            ran.push(step[3..].join(" "));
        }
        _ => {
            run(&argv)?;
        }
    }
    Ok(ran)
}
/// Jobs that must not block the forwarding loop: tmux verbs, the capture OSD, and (fixture tier with --live-context)
/// the compositor projections of key: and hypr: into the followed (nested) compositor.
#[derive(Clone)]
struct ActionScope {
    generation: u64,
    epoch: u64,
    queued: Instant,
    bound: Duration,
    window: Option<(String, u64)>,
}
impl ActionScope {
    fn new(focus: &ContextSnapshot, stale_ms: u64) -> Self {
        Self {
            generation: focus.routing_generation,
            epoch: focus.focus_epoch,
            queued: Instant::now(),
            bound: Duration::from_millis(stale_ms),
            window: focus.address.clone().zip(focus.pid),
        }
    }
    fn current(
        &self,
        selection: &SharedLogin,
        focus: &std::sync::Arc<std::sync::Mutex<ContextSnapshot>>,
        stale_ms: u64,
    ) -> Result<(ContextSnapshot, Duration), String> {
        let bound = self.bound.min(Duration::from_millis(stale_ms));
        if self.queued.elapsed() >= bound {
            return Err("queued input action expired".into());
        }
        // Only clone the authority snapshot under the locks. Process queries,
        // tmux and compositor IPC execute after this operation returns.
        let current = with_login_focus(
            selection,
            focus,
            self.generation,
            self.epoch,
            stale_ms,
            |current| {
                if current.remote || current.routing_generation != self.generation {
                    return Err("local action login scope changed".into());
                }
                Ok(current.clone())
            },
        )?;
        let remaining = bound.saturating_sub(self.queued.elapsed());
        if remaining.is_zero() {
            return Err("queued input action expired".into());
        }
        Ok((current, remaining))
    }
}
enum Job {
    Tmux {
        verb: String,
        client: TmuxClient,
        console: bool,
        scope: ActionScope,
    },
    Lua {
        action: String,
        lua: String,
        scope: ActionScope,
    },
    WindowZoom {
        native: Option<Box<crate::tmux_capture::Target>>,
        window: Option<(String, u64)>,
        generation: u64,
        epoch: u64,
        queued: Instant,
    },
    AppKey {
        app: String,
        action: String,
        focus: Box<ContextSnapshot>,
        queued: Instant,
    },
}
struct ActionWorker {
    jobs: Option<std::sync::mpsc::SyncSender<Job>>,
    worker: Option<std::thread::JoinHandle<()>>,
    stop: std::sync::Arc<AtomicBool>,
}
impl ActionWorker {
    fn spawn(
        policy: std::sync::Arc<std::sync::Mutex<GlobalConfig>>,
        selection: SharedLogin,
        shared_focus: std::sync::Arc<std::sync::Mutex<ContextSnapshot>>,
    ) -> Result<Self, String> {
        let (jobs, inbox) = std::sync::mpsc::sync_channel::<Job>(16);
        let stop = std::sync::Arc::new(AtomicBool::new(false));
        let cancelled = stop.clone();
        let worker=std::thread::Builder::new().name("mios-actions".into()).spawn(move || {
            while let Ok(job)=inbox.recv() {
                #[cfg(test)] acceptance_observer::effect_boundary("action-before-policy");
                if cancelled.load(Ordering::Acquire) {break;}
                let cfg=match policy.lock() { Ok(c)=>c.clone(), Err(_)=>break };
                if cancelled.load(Ordering::Acquire) {break;}
                let running=|| -> Result<(),String> {
                    if cancelled.load(Ordering::Acquire) {Err("input action worker stopped".into())} else {Ok(())}
                };
                match job {
                    Job::Tmux{verb,client,console,scope} => {
                        let remaining=|| -> Result<Duration,String> {
                            running()?;
                            let (current,time)=scope.current(&selection,&shared_focus,cfg.controller.context_stale_ms)?;
                            if current.tmux_client.as_ref()!=Some(&client) { return Err("queued tmux client focus changed".into()); }
                            Ok(time)
                        };
                        let r=run_tmux_guarded(&cfg,&client,&verb,console,scope.window.as_ref(),remaining);
                        #[cfg(test)] acceptance_observer::record_result("legacy-tmux", &r);
                        eprintln!("{}",serde_json::json!({"tmux":verb,"client":client.tty,"ok":r.is_ok(),"ran":r.as_ref().ok(),"error":r.as_ref().err()}));
                    }
                    Job::Lua{action,lua,scope} => {
                        let r=(|| -> Result<String,String> {
                            running()?;
                            let (_,budget)=scope.current(&selection,&shared_focus,cfg.controller.context_stale_ms)?;
                            let lua=window_scoped_lua(&action,&lua,scope.window.as_ref())?;
                            let reply=ipc_bounded(&format!("/eval {lua}"),budget,|| {
                                running()?;
                                scope.current(&selection,&shared_focus,cfg.controller.context_stale_ms).map(|(_,time)|time)
                            })?;
                            if reply.trim()!="ok" {return Err("queued compositor action was not acknowledged".into());}
                            Ok(reply)
                        })();
                        #[cfg(test)] acceptance_observer::record_result("legacy-lua", &r);
                        eprintln!("{}",serde_json::json!({"projected":action,"ok":r.as_ref().is_ok_and(|v|v.trim()=="ok"),"reply":r.as_ref().map(|v|v.trim().chars().take(200).collect::<String>()).unwrap_or_else(|e|e.clone())}));
                    }
                    Job::WindowZoom{native,window,generation,epoch,queued} => {
                        let bound=Duration::from_millis(cfg.controller.context_stale_ms);
                        let result=(|| -> Result<(),String> {
                            let valid=||!cancelled.load(Ordering::Acquire) && queued.elapsed()<bound && with_login_focus(&selection,&shared_focus,generation,epoch,cfg.controller.context_stale_ms,|_|Ok(())).is_ok();
                            if !valid() {
                                return Err("zoom login generation or request expired".into());
                            }
                            if let Some(target)=native {
                                crate::tmux_capture::zoom(&target,bound.saturating_sub(queued.elapsed()),valid)
                            } else if let Some((address,pid))=window {
                                let remaining=|| -> Result<Duration,String> {
                                    if !valid() {return Err("zoom login generation or request expired".into());}
                                    Ok(bound.saturating_sub(queued.elapsed()))
                                };
                                let reply=ipc_bounded(&format!("/eval {}",window_zoom_lua(&address,pid)),remaining()?,remaining)?;
                                if reply.trim()=="ok" {Ok(())} else {Err("focused window zoom was not acknowledged".into())}
                            } else {Err("zoom has no witnessed focused window".into())}
                        })();
                        #[cfg(test)] acceptance_observer::record_result("window-zoom", &result);
                        eprintln!("{}",serde_json::json!({"projected":"input:window-zoom","ok":result.is_ok(),"error":result.err()}));
                    }
                    Job::AppKey{app,action,focus,queued} => {
                        let result=(|| -> Result<(),String> {
                            running()?;
                            let bound=Duration::from_millis(cfg.controller.context_stale_ms);
                            let expected=app_input_focus(&focus,cfg.controller.context_stale_ms).ok_or("app input scope expired")?;
                            if queued.elapsed()>=bound || expected.app!=app {
                                return Err("app input focus or login generation changed".into());
                            }
                            let check=|current:&ContextSnapshot| {
                                if app_input_focus(current,cfg.controller.context_stale_ms).as_ref()==Some(&expected) {Ok(())} else {Err("app input focus scope changed".into())}
                            };
                            with_login_focus(&selection,&shared_focus,focus.routing_generation,focus.focus_epoch,cfg.controller.context_stale_ms,check)?;
                            let semantic=mios_resolver::app_input::Action::from_key(&action).ok_or("unsupported app-owned control")?;
                            let pid=app_input_process(&cfg,&focus,&app,bound.saturating_sub(queued.elapsed()))?;
                            let start=expected.process.start;
                            if queued.elapsed()>=bound {
                                return Err("app input focus changed while verifying process".into());
                            }
                            // Retain the focus lock only through the nonblocking
                            // send, never through process/tmux verification.
                            let runtime=std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from).ok_or("app runtime missing")?;
                            with_login_focus(&selection,&shared_focus,focus.routing_generation,focus.focus_epoch,cfg.controller.context_stale_ms,|current| {
                                running()?;
                                check(current)?;
                                mios_resolver::app_input::send(&runtime,pid,start,expected.clone(),semantic).map_err(|e|e.to_string())
                            })
                        })();
                        #[cfg(test)] acceptance_observer::record_result("app-key", &result);
                        eprintln!("{}",serde_json::json!({"projected":"input:app-key","app":app,"ok":result.is_ok(),"error":result.err()}));
                    }
                }
            }
        }).map_err(|e|e.to_string())?;
        Ok(Self {
            jobs: Some(jobs),
            worker: Some(worker),
            stop,
        })
    }
    fn submit(&self, job: Job) -> bool {
        !self.stop.load(Ordering::Acquire)
            && self.jobs.as_ref().is_some_and(|j| j.try_send(job).is_ok())
    }
    fn cancel(&mut self) {
        self.stop.store(true, Ordering::Release);
        self.jobs = None;
    }
}
fn window_scoped_action(action: &str) -> bool {
    action.starts_with("key:")
        || matches!(
            action.split_whitespace().next(),
            Some(
                "hypr:killactive"
                    | "hypr:fullscreen"
                    | "hypr:togglefloating"
                    | "hypr:movetoworkspace"
                    | "hypr:movewindow"
                    | "hypr:movefocus"
            )
        )
}
fn window_scoped_lua(
    action: &str,
    lua: &str,
    window: Option<&(String, u64)>,
) -> Result<String, String> {
    if !window_scoped_action(action) {
        return Ok(lua.to_owned());
    }
    let (address, pid) = window.ok_or("queued compositor action has no window witness")?;
    let address = serde_json::to_string(address).map_err(|e| e.to_string())?;
    Ok(format!("local w=hl.get_active_window();assert(w and tostring(w.address)=={address} and tonumber(w.pid)=={pid},'queued compositor focus changed');{lua}"))
}
/// A zoom uses the exact focused window and PID, never the compositor's implicit
/// dispatch target. It changes geometry only; capture ownership remains intact.
fn window_zoom_lua(address: &str, pid: u64) -> String {
    let address = serde_json::to_string(address).unwrap();
    format!("local w=hl.get_active_window();assert(w and tostring(w.address)=={address} and tonumber(w.pid)=={pid},'zoom focus changed');hl.dispatch(hl.dsp.window.fullscreen({{window=w,mode='maximized',action='toggle'}}))")
}
fn app_input_focus(
    focus: &ContextSnapshot,
    stale_ms: u64,
) -> Option<mios_resolver::app_input::Focus> {
    if focus.suspended.is_some()
        || focus.shell_overlay
        || focus.sampled.elapsed() >= Duration::from_millis(stale_ms)
    {
        return None;
    }
    let app = focus.fresh_app(stale_ms)?;
    let (address, window_pid) = focus.address.clone().zip(focus.pid)?;
    if focus.app_target.as_ref() != Some(&(address.clone(), window_pid)) {
        return None;
    }
    Some(mios_resolver::app_input::Focus {
        channel: "seat".into(),
        epoch: focus.focus_epoch,
        address,
        window_pid,
        generation: focus.routing_generation,
        app: app.into(),
        process: focus.app_process.clone()?,
    })
}
fn with_login_focus<T>(
    selection: &SharedLogin,
    focus: &std::sync::Arc<std::sync::Mutex<ContextSnapshot>>,
    generation: u64,
    epoch: u64,
    stale_ms: u64,
    operation: impl FnOnce(&ContextSnapshot) -> Result<T, String>,
) -> Result<T, String> {
    // Handoff commits use the same order. Never acquire selection while holding focus.
    let state = selection
        .lock()
        .map_err(|_| "app login authority lock failed")?;
    if state.generation != generation {
        return Err("input login generation changed".into());
    }
    let current = focus.lock().map_err(|_| "app focus lock failed")?;
    if current.focus_epoch != epoch
        || current.suspended.is_some()
        || current.sampled.elapsed() >= Duration::from_millis(stale_ms)
    {
        return Err("input focus visit changed or expired".into());
    }
    operation(&current)
}
fn app_input_process(
    cfg: &GlobalConfig,
    focus: &ContextSnapshot,
    app: &str,
    timeout: Duration,
) -> Result<u32, String> {
    let proc_root = Path::new("/proc");
    let uid = unsafe { libc::getuid() };
    let pinned = focus
        .app_process
        .as_ref()
        .ok_or("app has no accepted process witness")?;
    let candidate = if let Some(target) = &focus.native_target {
        Some(crate::tmux_capture::app_process(target, timeout)?)
    } else if let Some(client) = &focus.tmux_client {
        Some(crate::tmux_capture::observe(client, timeout)?.foreground)
    } else if !focus.remote {
        let pid = focus
            .pid
            .and_then(|pid| u32::try_from(pid).ok())
            .ok_or("app has no focused process tree")?;
        descendants(proc_root, pid)
            .contains(&pinned.pid)
            .then_some(pinned.pid)
    } else {
        None
    };
    let pid = candidate.ok_or("app is no longer foreground")?;
    if pid != pinned.pid
        || proc_start(proc_root, pid).map(|(_, start)| start) != Some(pinned.start)
        || !proc_groups(proc_root, pid).is_some_and(|(pgrp, tpgid)| pgrp == pid && tpgid == pid)
        || app_of_pid(proc_root, pid, &cfg.controller.nav.apps, uid, &lstat_facts).as_deref()
            != Some(app)
    {
        return Err("foreground executable no longer matches app policy".into());
    }
    Ok(pid)
}
impl Drop for ActionWorker {
    fn drop(&mut self) {
        self.cancel();
        if let Some(w) = self.worker.take() {
            let _ = w.join();
        }
    }
}
/// The compositor projection of one key: stroke (a nested session's focused window): down then up.
fn key_lua(chord: &crate::nav::KeyChord, window: &str) -> String {
    let (sym, mods) = crate::nav::keysym(chord);
    let q = |v: &str| serde_json::to_string(v).unwrap();
    let w = q(&format!("address:{window}"));
    format!("hl.dispatch(hl.dsp.send_key_state({{mods={m},key={k},state='down',window={w}}}))\nhl.dispatch(hl.dsp.send_key_state({{mods={m},key={k},state='up',window={w}}}))",
        m=q(&mods),k=q(&sym))
}
fn captured_key_lua(
    chord: &crate::nav::KeyChord,
    address: &str,
    pid: u32,
    cfg: &GlobalConfig,
) -> String {
    let q = |v: &str| serde_json::to_string(v).unwrap();
    format!("local w=hl.get_active_window();assert(w and tostring(w.address)=={address} and tonumber(w.pid)=={pid},'captured terminal focus changed');local cap=false;local released=false;for _,tag in pairs(w.tags or {{}}) do local t=tostring(tag):gsub('%*$','');if t=={cap} then cap=true end;if t=={rel} then released=true end end;assert(cap and not released,'terminal capture released');\n{stroke}",
        address=q(address),pid=pid,cap=q(&cfg.controller.capture_tag),rel=q(&cfg.controller.release_tag),stroke=key_lua(chord,address))
}
fn event_socket() -> Option<UnixStream> {
    let rt = std::env::var_os("XDG_RUNTIME_DIR")?;
    let sig = std::env::var("HYPRLAND_INSTANCE_SIGNATURE").ok()?;
    if sig.contains('/') || sig.contains("..") {
        return None;
    }
    let stream =
        UnixStream::connect(Path::new(&rt).join("hypr").join(sig).join(".socket2.sock")).ok()?;
    stream.set_nonblocking(true).ok()?;
    Some(stream)
}
fn wait_context_event(stream: &mut Option<UnixStream>, millis: u64) {
    use std::os::fd::AsRawFd;
    if stream.is_none() {
        *stream = event_socket();
    }
    let Some(s) = stream.as_mut() else {
        std::thread::sleep(Duration::from_millis(millis));
        return;
    };
    let mut poll = libc::pollfd {
        fd: s.as_raw_fd(),
        events: libc::POLLIN,
        revents: 0,
    };
    // SAFETY: one initialized pollfd references a live owned stream.
    let ready = unsafe { libc::poll(&mut poll, 1, millis.min(i32::MAX as u64) as i32) };
    if ready > 0 {
        let mut bytes = [0u8; 8192];
        match s.read(&mut bytes) {
            Ok(0) => *stream = None,
            Err(e) if e.kind() != std::io::ErrorKind::WouldBlock => *stream = None,
            _ => {}
        }
    }
    std::thread::sleep(Duration::from_millis(5));
}
fn policy_fingerprint(global: &GlobalConfig, legacy: &LegacyConfig) -> Result<Vec<u8>, String> {
    serde_json::to_vec(&(global, legacy, &global.resolved_actions)).map_err(|e| e.to_string())
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
                // C-15b (I4): Guide + right-stick flick moves the focused window (Hyprland 0.56 hl.window.move).
                "movewindow" => {
                    if !["left", "right", "up", "down"].contains(&arg.as_str()) {
                        return Err(format!(
                            "movewindow needs left, right, up or down, not {arg:?}"
                        ));
                    }
                    format!("hl.dispatch(hl.dsp.window.move({{direction={q}}}))")
                }
                name if name.starts_with("hypr3d:") => {
                    let name = &name[7..];
                    if !crate::config::ROOM_VERBS.contains(&name) {
                        return Err(format!("unsupported room action {name}"));
                    }
                    // A bare verb takes the plugin's default argument (cancel/recenter have none; sprint toggles).
                    if ["fullscreen", "jump", "cancel", "sprint", "recenter"].contains(&name)
                        && arg.is_empty()
                    {
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
            if words.len() == 1
                && [
                    "toggle_vm_passthrough",
                    "vm-capture-toggle",
                    "vm-fullscreen",
                ]
                .contains(&words[0])
            {
                return Ok(vec![
                    "/usr/libexec/mios/mios-inputd".into(),
                    "vm-action".into(),
                    words[0].into(),
                ]);
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
#[derive(Clone)]
struct NativeOwnership {
    target: crate::tmux_capture::Target,
    outer: Option<(u32, u64)>,
}
fn native_ownership(
    cfg: &GlobalConfig,
    request: &NativeOwnership,
    mode: &str,
    timeout: Duration,
) -> Result<CaptureReceipt, String> {
    if request.target.login_generation > 0 {
        let (active, _, _) =
            crate::tmux_capture::focused_target().ok_or("native login authority expired")?;
        if !same_pointer_scope(&active, &request.target) {
            return Err("native login target generation changed".into());
        }
    }
    let client = if mode == "on" {
        let (pid, start) = request
            .outer
            .ok_or("native capture has no witnessed outer client")?;
        if crate::nested::start_time(Path::new("/proc"), pid) != Some(start) {
            return Err("native outer client was replaced".into());
        }
        let outer = tmux_client_at(
            Path::new("/proc"),
            pid,
            &cfg.controller.nav.tmux_sockets,
            unsafe { libc::getuid() },
        )
        .ok_or("native outer client is no longer attached")?;
        crate::tmux_capture::leaf_client(cfg, &outer, timeout)?
    } else {
        request.target.client()
    };
    let mut expected = request.target.clone();
    expected.login_generation = 0;
    let expected_address = expected.address();
    let mut target = crate::tmux_capture::transaction(
        &client,
        if mode == "on-pinned" { "on" } else { mode },
        if mode == "on" {
            None
        } else {
            Some(&expected_address)
        },
        timeout,
    )?;
    target.login_generation = request.target.login_generation;
    Ok(native_receipt(target))
}
type OwnershipRequest = (
    u64,
    String,
    Option<String>,
    Vec<String>,
    Option<NativeOwnership>,
    u64,
    Instant,
);
struct OwnershipWorker {
    requests: Option<std::sync::mpsc::SyncSender<OwnershipRequest>>,
    results: std::sync::mpsc::Receiver<(u64, String, Result<CaptureReceipt, String>)>,
    last: u64,
    worker: Option<std::thread::JoinHandle<()>>,
    stop: std::sync::Arc<AtomicBool>,
}
impl OwnershipWorker {
    fn spawn(
        policy: std::sync::Arc<std::sync::Mutex<GlobalConfig>>,
        selection: SharedLogin,
    ) -> Result<Self, String> {
        let (requests, inbox) = std::sync::mpsc::sync_channel::<OwnershipRequest>(16);
        let (outbox, results) = std::sync::mpsc::channel();
        let stop = std::sync::Arc::new(AtomicBool::new(false));
        let cancelled = stop.clone();
        let (notices, notice_inbox) = std::sync::mpsc::sync_channel::<Vec<String>>(16);
        let notice_stop = stop.clone();
        std::thread::Builder::new()
            .name("mios-capture-notice".into())
            .spawn(move || {
                while let Ok(argv) = notice_inbox.recv() {
                    if notice_stop.load(Ordering::Relaxed) {
                        break;
                    }
                    let _ =
                        run_bounded_cancel(&argv, Duration::from_millis(2000), Some(&notice_stop));
                }
            })
            .map_err(|e| e.to_string())?;
        let worker = std::thread::Builder::new().name("mios-ownership".into()).spawn(move || {
            let mut captured:Option<CaptureReceipt>=None;
            while let Ok((seq, mode, expect, toggle, native, generation, queued)) = inbox.recv() {
                if cancelled.load(Ordering::Relaxed) { break; }
                let cfg = policy.lock().map(|c| c.clone());
                let request_mode=mode.clone();
                let pinned=mode=="on-pinned";
                let mode=if pinned {"on".to_owned()} else {mode};
                // North down resolves focus atomically in Lua, rather than rejecting a just-clicked window against
                // the context poll's old address. Its hold upgrade stays pinned to the confirmed capture.
                let target=if mode=="on" && !pinned {None} else if mode=="fullscreen" {captured.as_ref().map(|r|r.address.as_str()).or(expect.as_deref())} else {expect.as_deref()};
                let mut result = match &cfg {
                    Ok(cfg) if queued.elapsed()<Duration::from_millis(cfg.controller.context_stale_ms)
                        && selection.lock().is_ok_and(|s|s.generation==generation) =>
                        if let Some(request)=&native {
                            native_ownership(cfg,request,&request_mode,Duration::from_millis(cfg.controller.context_stale_ms))
                        } else if mode=="inspect" {inspect_capture(cfg,target)} else {capture_transaction(&mode, cfg, target)},
                    Ok(_) => Err("ownership request expired".into()),
                    Err(_) => Err("ownership policy lock failed".into()),
                };
                if mode=="fullscreen" {
                    if let (Ok(receipt),Some(prior))=(&mut result,&captured) {
                        if receipt.address==prior.address && receipt.pid==prior.pid {
                            receipt.tmux=prior.tmux.clone();receipt.error=prior.error.clone();
                        }
                    }
                }
                if let Ok(receipt)=&result { captured=if mode=="off" {None} else {Some(receipt.clone())}; }
                #[cfg(test)] acceptance_observer::record_result("ownership", &result);
                if outbox.send((seq, mode.clone(), result.clone())).is_err() { break; }
                // C-32: one notice per confirmed capture or release, never for a refused transaction or a focus change.
                if let (Ok(cfg), Ok(receipt)) = (&cfg, &result) {
                    if native.is_none() && cfg.controller.capture_osd && mode!="inspect" {
                        let message = osd_message(&mode, &receipt.class, &toggle);
                        let argv: Vec<String> = cfg.controller.capture_osd_argv.iter().map(|a| a.replace("{message}", &message)).collect();
                        let submitted=notices.try_send(argv).is_ok();
                        eprintln!("{}", serde_json::json!({"osd": message, "mode": mode, "submitted":submitted}));
                    }
                }
            }
        }).map_err(|e| e.to_string())?;
        Ok(Self {
            requests: Some(requests),
            results,
            last: 0,
            worker: Some(worker),
            stop,
        })
    }
    fn invalidate(&mut self) {
        self.last = self.last.saturating_add(1);
    }
    fn submit(
        &mut self,
        mode: &str,
        expect: Option<String>,
        toggle: Vec<String>,
        native: Option<NativeOwnership>,
        generation: u64,
    ) -> Result<(), String> {
        self.last += 1;
        self.requests
            .as_ref()
            .ok_or("ownership worker stopped")?
            .try_send((
                self.last,
                mode.into(),
                expect,
                toggle,
                native,
                generation,
                Instant::now(),
            ))
            .map_err(|_| "ownership queue unavailable".to_string())
    }
}
impl Drop for OwnershipWorker {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        self.requests = None;
        self.worker.take();
    }
}
// Inputd's per-user target authority. It changes routing only; physical assignments/Claims remain with run().
#[derive(Clone)]
struct AuthorizedLogin {
    generation: u64,
    witness: crate::tmux_handoff::LoginWitness,
}
#[derive(Default)]
struct LoginSelection {
    generation: u64,
    remote: Option<AuthorizedLogin>,
    local: Option<(TmuxClient, u64)>,
    candidates: Vec<crate::tmux_handoff::LoginWitness>,
}
impl LoginSelection {
    fn detach(&mut self, expected: u64) -> Result<u64, String> {
        if expected != self.generation {
            return Err("stale handoff generation; current login is unchanged".into());
        }
        let generation = self
            .generation
            .checked_add(1)
            .ok_or("handoff generation exhausted")?;
        if let Some(detached) = self.remote.take() {
            self.candidates.retain(|w| {
                w.client != detached.witness.client
                    || w.client_start != detached.witness.client_start
            });
        }
        self.local = None;
        self.generation = generation;
        Ok(generation)
    }

    fn reconcile(
        &mut self,
        local_active: bool,
        mut live: impl FnMut(&crate::tmux_handoff::LoginWitness) -> bool,
    ) -> Option<AuthorizedLogin> {
        self.candidates.retain(|w| live(w));
        let previous = self
            .remote
            .as_ref()
            .map(|r| (r.witness.client.clone(), r.witness.client_start));
        let next = self
            .remote
            .as_ref()
            .and_then(|r| {
                self.candidates
                    .iter()
                    .find(|w| {
                        w.client == r.witness.client && w.client_start == r.witness.client_start
                    })
                    .cloned()
            })
            .or_else(|| {
                (!local_active)
                    .then(|| self.candidates.last().cloned())
                    .flatten()
            });
        let next_identity = next.as_ref().map(|w| (w.client.clone(), w.client_start));
        if previous != next_identity {
            self.generation = self.generation.saturating_add(1);
            self.remote = next.map(|witness| AuthorizedLogin {
                generation: self.generation,
                witness,
            });
        }
        self.remote.clone()
    }
}
fn begin_login_handoff(
    router: &mut Router,
    current: &mut u64,
    next: u64,
    rearming: &mut bool,
) -> Option<Output> {
    if *current == next {
        return None;
    }
    *current = next;
    *rearming = true;
    let neutral = router.reset();
    router.cursor = false;
    Some(neutral)
}
/// Drain only sinks that already belong to the previous login. Generic emit
/// would select the new focus and can create a replacement output device.
pub(crate) fn drain_login_outputs(
    device: &Device,
    neutral: &Output,
    pad: Option<&mut VirtualDevice>,
    pointer: Option<&mut VirtualDevice>,
) -> Result<(), String> {
    let app = pad.map_or(Ok(()), |pad| {
        pad.write(&device.physical_events(&neutral.app))
    });
    // Attempt both releases even if the old gamepad endpoint has already gone.
    let pointer = pointer.map_or(Ok(()), |pointer| pointer.write(&neutral.pointer));
    app.and(pointer).map_err(|e| e.to_string())
}
type SharedLogin = std::sync::Arc<std::sync::Mutex<LoginSelection>>;
struct HandoffService {
    stop: std::sync::Arc<AtomicBool>,
    worker: Option<std::thread::JoinHandle<()>>,
}
impl HandoffService {
    fn spawn(
        selection: SharedLogin,
        focus: std::sync::Arc<std::sync::Mutex<ContextSnapshot>>,
        policy: std::sync::Arc<std::sync::Mutex<GlobalConfig>>,
    ) -> Result<Self, String> {
        Self::spawn_at(
            crate::tmux_handoff::socket_path()?,
            selection,
            focus,
            policy,
        )
    }
    fn spawn_at(
        path: PathBuf,
        selection: SharedLogin,
        focus: std::sync::Arc<std::sync::Mutex<ContextSnapshot>>,
        policy: std::sync::Arc<std::sync::Mutex<GlobalConfig>>,
    ) -> Result<Self, String> {
        use std::os::fd::AsRawFd;
        use std::os::unix::fs::{FileTypeExt, MetadataExt, PermissionsExt};
        if let Ok(meta) = std::fs::symlink_metadata(&path) {
            if !meta.file_type().is_socket() || meta.uid() != unsafe { libc::getuid() } {
                return Err("refusing an untrusted Inputd handoff endpoint".into());
            }
            if crate::tmux_handoff::endpoint_live(&path)? {
                return Err("another Inputd handoff authority is running".into());
            }
            let now = std::fs::symlink_metadata(&path).map_err(|e| e.to_string())?;
            if (now.dev(), now.ino()) != (meta.dev(), meta.ino()) {
                return Err("Inputd handoff endpoint changed during recovery".into());
            }
            std::fs::remove_file(&path).map_err(|e| e.to_string())?;
        }
        let listener = std::os::unix::net::UnixListener::bind(&path).map_err(|e| e.to_string())?;
        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600))
            .map_err(|e| e.to_string())?;
        listener.set_nonblocking(true).map_err(|e| e.to_string())?;
        if unsafe { libc::listen(listener.as_raw_fd(), 16) } != 0 {
            return Err(std::io::Error::last_os_error().to_string());
        }
        let metadata = std::fs::symlink_metadata(&path).map_err(|e| e.to_string())?;
        let identity = (metadata.dev(), metadata.ino());
        let stop = std::sync::Arc::new(AtomicBool::new(false));
        let stopping = stop.clone();
        let worker=std::thread::Builder::new().name("mios-inputd-handoff".into()).spawn(move||{
            while !stopping.load(Ordering::Relaxed) {
                let mut poll=libc::pollfd{fd:listener.as_raw_fd(),events:libc::POLLIN,revents:0};
                if unsafe{libc::poll(&mut poll,1,100)}<=0 {continue;}
                let Ok((mut stream,_))=listener.accept() else {continue;};
                let cfg=policy.lock().unwrap().clone();
                let reply=(||->Result<serde_json::Value,String>{
                    let peer=crate::tmux_handoff::peer_credentials(&stream)?;
                    if peer.0!=unsafe{libc::getuid()} {return Err("handoff peer belongs to another user".into());}
                    let deadline=Instant::now()+Duration::from_millis(cfg.controller.context_stale_ms);
                    let req=crate::tmux_handoff::read_request(&mut stream,deadline.saturating_duration_since(Instant::now()))?;
                    match req.op.as_str() {
                        "attach"=>{
                            let authenticated=crate::tmux_handoff::authenticate(&cfg,&req,peer);
                            let local=if authenticated.is_err() {
                                let actual=native_console_context(&cfg)?;
                                let (pid,start)=actual.native_outer.ok_or("local console outer client missing")?;
                                let client=tmux_client_at(Path::new("/proc"),pid,&cfg.controller.nav.tmux_sockets,peer.0)
                                    .ok_or("local console client was replaced")?;
                                let outer=crate::tmux_capture::observe(&client,Duration::from_millis(cfg.controller.context_stale_ms))?;
                                if req.socket.as_ref()!=Some(&client.socket) || req.client.as_deref()!=Some(client.tty.as_str())
                                    || req.pane.as_ref().is_some_and(|p|&outer.pane!=p) {
                                    return Err("requested local client is not this user's active console".into());
                                }
                                Some((client,start))
                            } else {None};
                            let outer=authenticated.as_ref().map(|w|w.client.clone()).unwrap_or_else(|_|local.as_ref().unwrap().0.clone());
                            let mut snapshot=native_snapshot(&cfg,&outer)?;
                            if Instant::now()>=deadline {return Err("Inputd handoff request expired before commit".into());}
                            let mut state=selection.lock().unwrap();
                            let unchanged=match (&authenticated,&state.remote,&local,&state.local) {
                                (Ok(w),Some(old),_,_)=>old.witness.client==w.client && old.witness.client_start==w.client_start
                                    && old.witness.session_id==w.session_id && old.witness.leader_start==w.leader_start,
                                (Err(_),None,Some(local),Some(old))=>local==old,
                                _=>false,
                            };
                            if !unchanged {
                                if let Ok(old)=focus.lock() {if let Some(target)=&old.native_target {let _=crate::tmux_capture::release_target(target,Duration::from_millis(cfg.controller.context_stale_ms));}}
                                if Instant::now()>=deadline {return Err("Inputd handoff expired while releasing the old capture".into());}
                                state.generation=state.generation.checked_add(1).ok_or("handoff generation exhausted")?;
                                if let Ok(witness)=&authenticated {
                                    state.candidates.retain(|w|w.client!=witness.client || w.client_start!=witness.client_start);
                                    state.candidates.push(witness.clone());
                                }
                                state.remote=authenticated.ok().map(|witness|AuthorizedLogin{generation:state.generation,witness});
                                state.local=local;
                            }
                            let generation=state.generation;
                            snapshot.remote=state.remote.is_some();snapshot.routing_generation=generation;
                            if let Some(target)=&mut snapshot.native_target {target.login_generation=generation;snapshot.address=Some(target.address());}
                            let target=snapshot.native_target.as_ref().ok_or("attached target missing")?;
                            Ok(serde_json::json!({"version":1,"ok":true,"uid":peer.0,"generation":generation,
                                "client_pid":target.client,"client_start":target.client_start,"outer_client_pid":outer.pid,"outer_client_start":snapshot.native_outer.map(|w|w.1),
                                "session":target.session,"session_name":target.session_name,"tmux_session_id":target.session,"tmux_session_name":target.session_name,
                                "logind_session_id":state.remote.as_ref().map(|r|r.witness.session_id.as_str()),
                                "window":target.window,"pane":target.pane,"foreground_pid":target.foreground,"foreground_start":target.foreground_start,
                                "state":if unchanged {"unchanged"}else{"pending-neutral"}}))
                        }
                        "detach"=>{
                            let mut state=selection.lock().unwrap();
                            if req.generation!=Some(state.generation) {return Err("stale handoff generation; current login is unchanged".into());}
                            if let Ok(old)=focus.lock() {if let Some(target)=&old.native_target {let _=crate::tmux_capture::release_target(target,Duration::from_millis(cfg.controller.context_stale_ms));}}
                            let generation=state.detach(req.generation.ok_or("detach generation is missing")?)?;
                            Ok(serde_json::json!({"version":1,"ok":true,"uid":peer.0,"generation":generation,"state":"released"}))
                        }
                        "window"=>{
                            let mut snapshot=focus.lock().unwrap().clone();
                            if snapshot.suspended.is_some() || snapshot.sampled.elapsed()>=Duration::from_millis(cfg.controller.context_stale_ms) {
                                return Err("Inputd has no fresh authorized native target".into());
                            }
                            let mode=req.mode.as_deref().ok_or("missing window mode")?;
                            let state=selection.lock().unwrap();
                            if let Some(remote)=&state.remote {
                                if !crate::tmux_handoff::live(&remote.witness) {return Err("authenticated remote login expired".into());}
                                snapshot=native_snapshot(&cfg,&remote.witness.client)?;
                            } else {snapshot=native_console_context(&cfg)?;}
                            let mut target=snapshot.native_target.ok_or("authorized login has no native target")?;
                            if req.frontend_pid.is_some_and(|p|p!=target.foreground)
                                || req.frontend_start.is_some_and(|s|s!=target.foreground_start) {
                                return Err("frontend is no longer this login's focused foreground process".into());
                            }
                            if Instant::now()>=deadline {return Err("Inputd window request expired before commit".into());}
                            let generation=state.generation;
                            let expected=target.address();
                            target=crate::tmux_capture::transaction(&target.client(),mode,Some(&expected),Duration::from_millis(cfg.controller.context_stale_ms))?;
                            target.login_generation=generation;
                            Ok(serde_json::json!({"version":1,"ok":true,"uid":peer.0,"generation":generation,"window":target.address(),"class":"console","mode":mode}))
                        }
                        _=>Err("unsupported Inputd handoff operation".into()),
                    }
                })();
                let reply=reply.unwrap_or_else(|error|serde_json::json!({"version":1,"ok":false,"error":error}));
                let _=crate::tmux_handoff::write_reply(&mut stream,&reply);
            }
            if std::fs::symlink_metadata(&path).ok().is_some_and(|m|(m.dev(),m.ino())==identity) {let _=std::fs::remove_file(&path);}
        }).map_err(|e|e.to_string())?;
        Ok(Self {
            stop,
            worker: Some(worker),
        })
    }
}
impl Drop for HandoffService {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(worker) = self.worker.take() {
            let _ = worker.join();
        }
    }
}

struct Sinks {
    pointer: Option<VirtualDevice>,
    remote_pointer: Option<RemotePointerBus>,
    ownership_generation: u64,
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
    /// I4 `--nested`: the session reads only its parent window's seat, so key: is projected into its compositor (never
    /// the uinput keyboard, which the host would read) and pointer output is not sent to the host's virtual pointer.
    nested: bool,
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
        self.keyboard
            .as_mut()
            .unwrap()
            .write(&crate::nav::stroke(chord))
            .map_err(|e| e.to_string())
    }
}
/// C-17: the stroke that switches to this user's graphical VT (Ctrl+Alt+F<VTNr>; the kernel keymap maps it to
/// Console_<n>). None without a graphical session, or on a VT that no F-key reaches.
pub fn graphical_vt_chord(vt: Option<u32>) -> Option<String> {
    vt.filter(|v| (1..=12).contains(v))
        .map(|v| format!("CTRL+ALT+F{v}"))
}
/// Returns the ownership change this output requested (`input:window on|fullscreen` = true, `off` = false), which the
/// caller applies to every other controller of the session.
fn emit(
    output: Output,
    device: &mut RunningDevice,
    sinks: &mut Sinks,
    focus: &ContextSnapshot,
) -> Result<Option<bool>, String> {
    // An explicit route is a complete output boundary: never run a host command or emit uinput for its pad.
    if device.session.is_some() || device.session_error.is_some() {
        for action in output.actions {
            let planned = device
                .session
                .as_ref()
                .ok_or("ambiguous controller assignment".to_owned())
                .and_then(|route| {
                    if action.starts_with("input:app-key ") {
                        if route.ssh_host.is_none() && app_request(&action).is_some() {
                            Ok(vec![])
                        } else {
                            Err("app-owned controls require an exact verified local route".into())
                        }
                    } else {
                        crate::sessions::plan(route, &action, device.router.policy())
                    }
                });
            let submitted = planned.is_ok()
                && sinks.execute_actions
                && device
                    .session_worker
                    .as_ref()
                    .is_some_and(|worker| worker.submit(action.clone()));
            println!(
                "{}",
                serde_json::json!({"device":device.device.path,"session":device.session.as_ref().map(|r|&r.name),
                "context":device.router.context,"action":action,"submitted":submitted,"executed":false,
                "error":planned.as_ref().err(),"argv":if sinks.execute_actions {None}else{planned.as_ref().ok()}})
            );
        }
        return Ok(None);
    }
    if sinks.bridge {
        if sinks.execute_actions
            && !sinks.nested
            && !focus.remote
            && device.output.is_none()
            && !device.suspended
        {
            device.output =
                Some(VirtualDevice::clone_gamepad(&device.device).map_err(|e| e.to_string())?);
        }
        if let Some(virtual_pad) = &mut device.output {
            // O-7: the clone reports the driver's own codes (a quirk remapped them for routing only).
            virtual_pad
                .write(&device.device.physical_events(&output.app))
                .map_err(|e| e.to_string())?;
        }
        if sinks.execute_actions
            && !sinks.nested
            && !focus.remote
            && !output.pointer.is_empty()
            && !(device.router.captured_navigation() && !device.router.half_capture())
        {
            if sinks.pointer.is_none() {
                sinks.pointer = Some(
                    if device.router.policy().controller.virtual_pointer.is_empty() {
                        VirtualDevice::pointer()
                    } else {
                        VirtualDevice::pointer_named(
                            &device.router.policy().controller.virtual_pointer,
                        )
                    }
                    .map_err(|e| e.to_string())?,
                );
            }
            sinks
                .pointer
                .as_mut()
                .unwrap()
                .write(&output.pointer)
                .map_err(|e| e.to_string())?;
        }
    }
    if focus.remote && sinks.execute_actions {
        if let Some(target) = focus
            .native_target
            .as_ref()
            .filter(|_| focus.suspended.is_none())
        {
            if sinks
                .remote_pointer
                .as_ref()
                .is_none_or(|p| !same_pointer_scope(&p.target, target))
            {
                sinks.remote_pointer = Some(RemotePointerBus {
                    target: target.clone(),
                    pointer: None,
                    cursors: BTreeSet::new(),
                    holds: PointerHolds::default(),
                    visible: false,
                    revoked: false,
                    retry: Instant::now() - Duration::from_secs(10),
                });
            }
            let bus = sinks.remote_pointer.as_mut().unwrap();
            if device.router.cursor {
                bus.cursors.insert(device.device.path.clone());
            } else {
                bus.cursors.remove(&device.device.path);
            }
            let cursor = !bus.cursors.is_empty();
            let cfg = device.router.policy();
            if bus.pointer.as_ref().is_some_and(|p| !p.alive()) {
                bus.pointer = None;
                bus.visible = false;
                bus.revoked = true;
                bus.holds.cancel();
                device.neutral_rearming = true;
            }
            if output.actions.iter().any(|a| a == "input:cursor on") {
                bus.revoked = false;
            }
            let intent = output.pointer.iter().any(|e| {
                (e.kind == crate::router::EV_REL && e.value != 0)
                    || (e.kind == crate::router::EV_KEY && e.value > 0)
            }) || output.actions.iter().any(|a| a == "input:cursor on");
            if bus.pointer.is_none()
                && intent
                && !bus.revoked
                && bus.retry.elapsed() >= Duration::from_millis(cfg.controller.scan_ms)
            {
                bus.retry = Instant::now();
                bus.pointer = crate::tmux_pointer::PointerWorker::spawn(
                    target.clone(),
                    cfg.controller.console.cell_px,
                    Duration::from_millis(cfg.controller.context_stale_ms),
                )
                .ok();
            }
            // Router cursor/focus release outputs contain normal ups. Cancel the native gesture first,
            // even when another controller keeps the shared cursor visible, so an exit cannot click chrome.
            let cancel_source =
                !device.router.cursor && bus.holds.retire_source(&device.device.path);
            if let Some(pointer) = &bus.pointer {
                if cancel_source && pointer.visible(false) {
                    bus.visible = false;
                }
                if bus.visible != cursor && pointer.visible(cursor) {
                    bus.visible = cursor;
                    if !cursor {
                        bus.holds.cancel();
                    }
                }
                let projected =
                    bus.holds
                        .project(&device.device.path, &output.pointer, device.router.cursor);
                if !projected.is_empty() && !pointer.submit(&projected) {
                    bus.revoked = true;
                    device.neutral_rearming = true;
                }
            }
        }
    }
    let mut ownership = None;
    for action in output.actions {
        let action =
            crate::router::console_action(device.router.policy(), device.router.console(), action);
        if action == "none" {
            continue;
        }
        let mut submitted = false;
        if let Some(request) = action.strip_prefix("input:app-key ") {
            if let Some((app, key)) = request.split_once(' ') {
                if (sinks.execute_actions || sinks.project)
                    && device.router.app() == Some(app)
                    && app_input_focus(focus, device.router.policy().controller.context_stale_ms)
                        .is_some()
                {
                    if let Some(worker) = &sinks.actions {
                        submitted = worker.submit(Job::AppKey {
                            app: app.into(),
                            action: key.into(),
                            focus: Box::new(focus.clone()),
                            queued: Instant::now(),
                        });
                    }
                }
            }
        } else if action == "input:window-zoom" {
            let cfg = device.router.policy();
            if focus.suspended.is_none()
                && focus.sampled.elapsed() < Duration::from_millis(cfg.controller.context_stale_ms)
                && (sinks.execute_actions || sinks.project)
            {
                if let Some(worker) = &sinks.actions {
                    let native = fresh_native_target(focus, cfg).cloned().map(Box::new);
                    let window = if native.is_none() && !focus.remote && !device.router.console() {
                        focus.address.clone().zip(focus.pid)
                    } else {
                        None
                    };
                    if native.is_some() || window.is_some() {
                        submitted = worker.submit(Job::WindowZoom {
                            native,
                            window,
                            generation: focus.routing_generation,
                            epoch: focus.focus_epoch,
                            queued: Instant::now(),
                        });
                    }
                }
            }
        } else if device.router.shell_overlay() && action.starts_with("key:") {
            let chord = crate::nav::parse_key_chord(action.strip_prefix("key:").unwrap())?;
            if sinks.execute_actions && !sinks.nested {
                sinks.stroke(&chord)?;
                submitted = true;
            }
        } else if device.router.captured_navigation()
            && (action.starts_with("key:") || action.starts_with("tmux:"))
        {
            if let Some(binding) = &device.capture {
                let matches = focus.address.as_deref() == Some(binding.address.as_str())
                    && focus.pid == Some(u64::from(binding.pid));
                if matches && binding.error.is_none() {
                    if let Some(worker) = &binding.worker {
                        submitted = sinks.execute_actions && worker.submit(action.clone());
                    }
                }
            }
        } else if device.router.captured_navigation()
            && !action.starts_with("input:")
            && action != device.router.policy().controller.guide_double_press_action
        {
            // Captured terminal navigation never executes host-global commands.
        } else if let Some(mode) = action.strip_prefix("input:window ") {
            if mode == "on" || mode == "on-pinned" || mode == "off" {
                device.capture = None;
            }
            ownership = Some(mode != "off");
            if let Some(worker) = &mut sinks.ownership {
                worker.submit(
                    mode,
                    focus.address.clone(),
                    device.router.legacy_toggle_chord(),
                    focus.native_target.as_ref().map(|t| NativeOwnership {
                        target: t.clone(),
                        outer: focus.native_outer,
                    }),
                    focus.routing_generation,
                )?;
                submitted = true;
            }
        } else if let Some(chord) = action.strip_prefix("key:") {
            // C-07: an atomic stroke on the mios-vctl-keyboard virtual keyboard (US layout), or, on the fixture tier
            // following a nested compositor, the same keysym sent to its focused window.
            // On a VT the kernel console reads the same uinput keyboard (C-16); the fixture tier writes the bytes the
            // console keymap would deliver into the pty that stands in for the VT.
            let chord = crate::nav::parse_key_chord(chord)?;
            if focus.remote {
                submitted = sinks.execute_actions
                    && device
                        .remote_input
                        .as_ref()
                        .is_some_and(|r| r.keys.submit(action.clone()));
            } else if sinks.execute_actions && !sinks.nested {
                sinks.stroke(&chord)?;
                submitted = true;
            } else if let (true, Some(sink)) = (device.router.console(), &mut sinks.console_sink) {
                if let Some(bytes) = crate::nav::vt_bytes(&chord) {
                    sink.write_all(&bytes).map_err(|e| e.to_string())?;
                    submitted = true;
                }
            } else if sinks.project {
                if let (Some(worker), Some(window), Some(_)) =
                    (&sinks.actions, &focus.address, focus.pid)
                {
                    submitted = worker.submit(Job::Lua {
                        action: action.clone(),
                        lua: key_lua(&chord, window),
                        scope: ActionScope::new(
                            focus,
                            device.router.policy().controller.context_stale_ms,
                        ),
                    });
                }
            }
        } else if let Some(verb) = action.strip_prefix("tmux:") {
            if focus.remote {
                submitted = sinks.runs_tmux()
                    && device
                        .remote_input
                        .as_ref()
                        .is_some_and(|r| r.keys.submit(action.clone()));
            } else
            // C-30: only against the tmux client discovery found; without one the *_plain profile is active.
            if sinks.runs_tmux() {
                if let (Some(worker), Some(client)) = (&sinks.actions, &focus.tmux_client) {
                    submitted = worker.submit(Job::Tmux {
                        verb: verb.into(),
                        client: client.clone(),
                        console: device.router.console(),
                        scope: ActionScope::new(
                            focus,
                            device.router.policy().controller.context_stale_ms,
                        ),
                    });
                }
            }
        } else if action == "mios:graphical-vt" {
            // C-17: no graphical session (or a VT beyond F12) sends nothing.
            let chord = graphical_vt_chord(focus.graphical_vt);
            if let (false, Some(c)) = (focus.remote, &chord) {
                let c = crate::nav::parse_key_chord(c)?;
                if sinks.execute_actions {
                    sinks.stroke(&c)?;
                    submitted = true;
                } else if sinks.console_sink.is_some() {
                    submitted = true; // a VT switch: the console keymap delivers no bytes
                }
            }
            println!(
                "{}",
                serde_json::json!({"vt_switch": chord, "graphical_vt": focus.graphical_vt, "submitted": submitted})
            );
        } else if sinks.project && action.starts_with("hypr:") {
            if let Some(worker) = &sinks.actions {
                let lua = configured_command(&action, device.router.policy())?.remove(2);
                if !window_scoped_action(&action) || focus.address.as_ref().zip(focus.pid).is_some()
                {
                    submitted = worker.submit(Job::Lua {
                        action: action.clone(),
                        lua,
                        scope: ActionScope::new(
                            focus,
                            device.router.policy().controller.context_stale_ms,
                        ),
                    });
                }
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
type DeviceCandidates = std::sync::mpsc::Receiver<Result<Device, String>>;
type Discovery = (DeviceCandidates, std::thread::JoinHandle<()>);
/// Nodes the host bridge must leave alone: assigned to a live nested session (path -> seat). Refreshed every scan.
type Assigned = std::sync::Arc<std::sync::Mutex<BTreeMap<PathBuf, String>>>;
type RawConsumers = std::sync::Arc<std::sync::Mutex<BTreeMap<PathBuf, String>>>;
type ReleasedDevices = std::sync::Arc<std::sync::Mutex<BTreeSet<PathBuf>>>;

fn fresh_native_target<'a>(
    focus: &'a ContextSnapshot,
    cfg: &GlobalConfig,
) -> Option<&'a crate::tmux_capture::Target> {
    if focus.suspended.is_some()
        || focus.sampled.elapsed() >= Duration::from_millis(cfg.controller.context_stale_ms)
    {
        None
    } else {
        focus.native_target.as_ref()
    }
}
fn native_owned(focus: &ContextSnapshot, cfg: &GlobalConfig) -> bool {
    focus.name == "app-capture"
        && focus.suspended.is_none()
        && focus.native_target.is_some()
        && focus.sampled.elapsed() < Duration::from_millis(cfg.controller.context_stale_ms)
}
fn native_frontend(
    focus: &ContextSnapshot,
    cfg: &GlobalConfig,
    cursor: bool,
) -> Option<crate::tmux_capture::Frontend> {
    if focus.name != "app-capture"
        || cursor
        || focus.suspended.is_some()
        || focus.sampled.elapsed() >= Duration::from_millis(cfg.controller.context_stale_ms)
    {
        return None;
    }
    let app = focus.fresh_app(cfg.controller.context_stale_ms)?;
    let nav = &cfg.controller.nav;
    if !nav.apps.get(app)?.camera {
        return None;
    }
    let profile = nav.profiles.get(&nav.app_profile(app, "console", true)?)?;
    let mut camera: [String; 4] = Default::default();
    let mut rate_ms = [0u64; 4];
    for (i, dir) in [
        crate::nav::Dir::Left,
        crate::nav::Dir::Right,
        crate::nav::Dir::Up,
        crate::nav::Dir::Down,
    ]
    .into_iter()
    .enumerate()
    {
        let crate::nav::Value::Press(action) =
            profile.get(&crate::nav::Input::Stick { right: true, dir })?
        else {
            return None;
        };
        crate::nav::parse_key_chord(action.strip_prefix("key:")?).ok()?;
        camera[i] = action.clone();
        rate_ms[i] = nav
            .apps
            .get(app)?
            .repeat
            .get(&crate::nav::Input::Stick { right: true, dir })
            .or_else(|| {
                nav.repeat
                    .get(&crate::nav::Input::Stick { right: true, dir })
            })?
            .1;
    }
    let (outer_client, outer_start) = focus.native_outer?;
    Some(crate::tmux_capture::Frontend {
        target: focus.native_target.clone()?,
        outer_client,
        outer_start,
        camera,
        rate_ms,
    })
}
struct ManifestFocus<'a> {
    frontend: Option<&'a crate::tmux_capture::Frontend>,
    stale_ms: u64,
    target: Option<&'a crate::tmux_capture::Target>,
    owned: bool,
    remote: bool,
    focus: Option<&'a ContextSnapshot>,
}
fn update_active_manifest(
    runtime: &Path,
    devices: &BTreeMap<PathBuf, RunningDevice>,
    raw_consumers: &BTreeMap<PathBuf, String>,
    bridge: bool,
    scan_ms: u64,
    native: ManifestFocus<'_>,
) {
    let ManifestFocus {
        frontend,
        stale_ms: frontend_stale_ms,
        target: native_focus,
        owned: native_owned,
        remote: native_remote,
        focus,
    } = native;
    let path = runtime.join("mios-inputd-active.json");
    let me = std::process::id();
    let start_ticks = crate::nested::start_time(Path::new("/proc"), me).unwrap_or(0);
    let now_dur = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    let devs: Vec<serde_json::Value> = devices
        .iter()
        .filter(|(_, d)| !d.suspended && !d.dropped && (!bridge || d.device.grabbed()))
        .map(|(p, d)| {
            serde_json::json!({
                "path": p,
                "name": d.device.name,
                "rearmed": !d.neutral_rearming,
            })
        })
        .collect();
    let raws: Vec<serde_json::Value> = raw_consumers
        .iter()
        .map(|(p, err)| {
            serde_json::json!({
                "path": p,
                "error": err,
            })
        })
        .collect();
    let manifest = serde_json::json!({
        "pid": me,
        "bridge_pid": me,
        "start_time": start_ticks,
        "bridge_start": start_ticks,
        "controller_return_protocol_version": 1,
        "controller_scan_ms": scan_ms,
        "frontend":frontend,
        "frontend_stale_ms":frontend_stale_ms,
        "native_focus":native_focus,
        "native_owned":native_owned,
        "native_remote":native_remote,
        "native_target_protocol_version":1,
        "app_focus":focus.and_then(|f|app_input_focus(f,frontend_stale_ms)),
        "app_focus_ns":focus.map(|f|mios_resolver::app_input::monotonic_ns().saturating_sub(f.sampled.elapsed().as_nanos() as u64)),
        "devices": devs,
        "raw_consumers": raws,
        "timestamp": now_dur.as_secs(),
        "timestamp_ms": now_dur.as_millis() as u64,
    });
    let _ = write_private(&path, &format!("{manifest}\n"));
}

fn socket_peercred_linux(stream: &std::os::unix::net::UnixStream) -> Result<(u32, u32), String> {
    use std::os::fd::AsRawFd;
    let mut cred = libc::ucred {
        pid: 0,
        uid: u32::MAX,
        gid: u32::MAX,
    };
    let mut len = std::mem::size_of::<libc::ucred>() as libc::socklen_t;
    let rc = unsafe {
        libc::getsockopt(
            stream.as_raw_fd(),
            libc::SOL_SOCKET,
            libc::SO_PEERCRED,
            (&mut cred as *mut libc::ucred).cast::<libc::c_void>(),
            &mut len,
        )
    };
    if rc != 0 || len as usize != std::mem::size_of::<libc::ucred>() || cred.pid <= 0 {
        return Err("unauthorized: SO_PEERCRED failed".into());
    }
    Ok((cred.uid, cred.pid as u32))
}

fn read_bounded_control_json(
    stream: &mut std::os::unix::net::UnixStream,
    timeout: Duration,
    max_bytes: usize,
) -> Result<serde_json::Value, String> {
    use std::io::{BufRead, Read};
    stream
        .set_nonblocking(false)
        .map_err(|e| format!("socket nonblocking setup failed: {e}"))?;
    stream
        .set_read_timeout(Some(timeout))
        .map_err(|e| format!("socket read timeout setup failed: {e}"))?;
    stream
        .set_write_timeout(Some(timeout))
        .map_err(|e| format!("socket write timeout setup failed: {e}"))?;
    let mut reader = std::io::BufReader::new(stream.take((max_bytes + 1) as u64));
    let mut line = String::new();
    reader
        .read_line(&mut line)
        .map_err(|e| format!("control socket read failed: {e}"))?;
    if line.is_empty() {
        return Err("empty control request".into());
    }
    if line.len() > max_bytes || !line.ends_with('\n') {
        return Err("control request exceeds maximum length or is unterminated".into());
    }
    if line.trim().is_empty() {
        return Err("blank control request".into());
    }
    serde_json::from_str(&line).map_err(|e| format!("invalid control request json: {e}"))
}

fn extract_control_nonce(req: &serde_json::Value) -> Result<serde_json::Value, String> {
    match req.get("nonce") {
        Some(v) if v.is_string() && !v.as_str().unwrap_or("").trim().is_empty() => Ok(v.clone()),
        Some(v) if v.is_u64() || v.is_i64() => Ok(v.clone()),
        _ => Err("missing or empty nonce".into()),
    }
}

fn paths_match_exact_or_canonical(a: &Path, b: &Path) -> bool {
    if a == b {
        return true;
    }
    match (std::fs::canonicalize(a), std::fs::canonicalize(b)) {
        (Ok(ca), Ok(cb)) => ca == cb,
        _ => false,
    }
}

fn resolve_leased_controller_path(
    req_device: &str,
    session_controllers: &[PathBuf],
    active_devices: &BTreeSet<PathBuf>,
) -> Result<PathBuf, String> {
    if req_device.trim().is_empty() {
        return Err("missing device path".into());
    }
    let target_path = PathBuf::from(req_device);
    if !target_path.is_absolute() {
        return Err(format!(
            "device path {} must be absolute",
            target_path.display()
        ));
    }
    if std::fs::symlink_metadata(&target_path)
        .map(|m| m.file_type().is_symlink())
        .unwrap_or(false)
    {
        return Err(format!(
            "device path {} must not be a symlink",
            target_path.display()
        ));
    }
    if !session_controllers
        .iter()
        .any(|c| paths_match_exact_or_canonical(c, &target_path))
    {
        return Err(format!(
            "device {} is not leased to this nested session",
            target_path.display()
        ));
    }
    active_devices
        .iter()
        .find(|p| paths_match_exact_or_canonical(p, &target_path))
        .cloned()
        .ok_or_else(|| format!("device {} not owned by child inputd", target_path.display()))
}

fn physical_controls_neutral(
    keys: &BTreeSet<u16>,
    axes: &BTreeMap<u16, crate::router::Axis>,
    stick_deadzone: f64,
    trigger_threshold: f64,
) -> Result<(), (Vec<u16>, Vec<u16>)> {
    let buttons: Vec<u16> = keys.iter().copied().collect();
    let mut deflected_axes = Vec::new();
    for (code, axis) in axes {
        if [2, 5].contains(code) {
            if axis.trigger() > trigger_threshold {
                deflected_axes.push(*code);
            }
        } else if axis.centered().abs() > stick_deadzone {
            deflected_axes.push(*code);
        }
    }
    if buttons.is_empty() && deflected_axes.is_empty() {
        Ok(())
    } else {
        Err((buttons, deflected_axes))
    }
}

fn step_neutral_rearming(
    rearming: &mut bool,
    router: &mut Router,
    snapshot: std::io::Result<(BTreeSet<u16>, BTreeMap<u16, crate::router::Axis>)>,
    stick_deadzone: f64,
    trigger_threshold: f64,
) -> bool {
    if !*rearming {
        return false;
    }
    let Ok((keys, axes)) = snapshot else {
        return true;
    };
    // The complete ioctl snapshot supersedes cached axes, including after SYN_DROPPED.
    if physical_controls_neutral(&keys, &axes, stick_deadzone, trigger_threshold).is_ok() {
        *rearming = false;
        let _ = router.reset();
        let _ = router.resync(keys, axes);
        false
    } else {
        true
    }
}

#[allow(clippy::too_many_arguments)]
fn handle_child_control_connection<F>(
    mut stream: std::os::unix::net::UnixStream,
    session: &crate::nested::Session,
    proc_root: &Path,
    expected_uid: u32,
    peer_override: Option<(u32, u32)>,
    active_devices: &BTreeSet<PathBuf>,
    known_devices: &std::sync::Mutex<BTreeSet<PathBuf>>,
    released_devices: &std::sync::Mutex<BTreeSet<PathBuf>>,
    mut inspect_and_release: F,
) -> serde_json::Value
where
    F: FnMut(&Path) -> Result<Result<(), (Vec<u16>, Vec<u16>)>, String>,
{
    use std::io::Write;
    let peer = match peer_override {
        Some(p) => Ok(p),
        None => socket_peercred_linux(&stream),
    };
    let req = match read_bounded_control_json(&mut stream, Duration::from_millis(500), 4096) {
        Ok(v) => v,
        Err(e) => {
            let resp = serde_json::json!({
                "ok": false,
                "error": e,
            });
            let _ = writeln!(stream, "{resp}");
            return resp;
        }
    };
    let nonce = match extract_control_nonce(&req) {
        Ok(n) => n,
        Err(e) => {
            let resp = serde_json::json!({
                "ok": false,
                "error": e,
            });
            let _ = writeln!(stream, "{resp}");
            return resp;
        }
    };
    let (peer_uid, peer_pid) = match peer {
        Ok(p) => p,
        Err(e) => {
            let resp = serde_json::json!({
                "ok": false,
                "nonce": nonce,
                "error": e,
            });
            let _ = writeln!(stream, "{resp}");
            return resp;
        }
    };
    let req_seat = match req.get("seat") {
        Some(v) => match v.as_str() {
            Some(s) if !s.is_empty() => Some(s),
            _ => {
                let resp = serde_json::json!({
                    "ok": false,
                    "nonce": nonce,
                    "error": "invalid seat in control request",
                });
                let _ = writeln!(stream, "{resp}");
                return resp;
            }
        },
        None => None,
    };
    let req_pid = match req.get("launcher_pid") {
        Some(v) => match v.as_u64().and_then(|n| u32::try_from(n).ok()) {
            Some(p) => Some(p),
            None => {
                let resp = serde_json::json!({
                    "ok": false,
                    "nonce": nonce,
                    "error": "invalid launcher_pid in control request",
                });
                let _ = writeln!(stream, "{resp}");
                return resp;
            }
        },
        None => None,
    };
    let req_start = match req.get("launcher_start") {
        Some(v) => match v.as_u64() {
            Some(st) => Some(st),
            None => {
                let resp = serde_json::json!({
                    "ok": false,
                    "nonce": nonce,
                    "error": "invalid launcher_start in control request",
                });
                let _ = writeln!(stream, "{resp}");
                return resp;
            }
        },
        None => None,
    };
    if let Err(e) = session.verify_launcher_peer(
        proc_root,
        expected_uid,
        (peer_uid, peer_pid),
        req_seat,
        req_pid,
        req_start,
    ) {
        let resp = serde_json::json!({
            "ok": false,
            "nonce": nonce,
            "error": e,
        });
        let _ = writeln!(stream, "{resp}");
        return resp;
    }

    match req["action"].as_str() {
        Some("return_capabilities") => {
            let target = match resolve_leased_controller_path(
                req["device"].as_str().unwrap_or(""),
                &session.controllers,
                active_devices,
            ) {
                Ok(path) => path,
                Err(error) => {
                    let resp = serde_json::json!({"ok":false,"nonce":nonce,"seat":session.seat,"error":error});
                    let _ = writeln!(stream, "{resp}");
                    return resp;
                }
            };
            let resp = serde_json::json!({"ok":true,"protocol_version":1,"nonce":nonce,"seat":session.seat,
                "launcher_pid":session.launcher_pid,"launcher_start":session.launcher_start,"device":target});
            let _ = writeln!(stream, "{resp}");
            resp
        }
        Some("release_device") => {
            let dev_req = req["device"].as_str().unwrap_or("");
            let matched =
                match resolve_leased_controller_path(dev_req, &session.controllers, active_devices)
                {
                    Ok(p) => p,
                    Err(e) => {
                        let resp = serde_json::json!({
                            "ok": false,
                            "nonce": nonce,
                            "seat": session.seat,
                            "error": e,
                        });
                        let _ = writeln!(stream, "{resp}");
                        return resp;
                    }
                };
            match inspect_and_release(&matched) {
                Ok(Ok(())) => {
                    {
                        let mut rel = released_devices.lock().unwrap();
                        rel.insert(matched.clone());
                        if let Ok(canon) = std::fs::canonicalize(&matched) {
                            rel.insert(canon);
                        }
                    }
                    known_devices.lock().unwrap().remove(&matched);
                    let resp = serde_json::json!({
                        "ok": true,
                        "nonce": nonce,
                        "seat": session.seat,
                        "launcher_pid": session.launcher_pid,
                        "launcher_start": session.launcher_start,
                        "device": matched.display().to_string(),
                    });
                    let _ = writeln!(stream, "{resp}");
                    resp
                }
                Ok(Err((buttons, axes))) => {
                    let resp = serde_json::json!({
                        "ok": false,
                        "nonce": nonce,
                        "seat": session.seat,
                        "reason": "controls_held",
                        "deferred": true,
                        "buttons": buttons,
                        "axes": axes,
                        "error": "physical buttons, triggers, or sticks currently held; release deferred",
                    });
                    let _ = writeln!(stream, "{resp}");
                    resp
                }
                Err(e) => {
                    let resp = serde_json::json!({
                        "ok": false,
                        "nonce": nonce,
                        "seat": session.seat,
                        "reason": "ioctl_failed",
                        "error": e,
                    });
                    let _ = writeln!(stream, "{resp}");
                    resp
                }
            }
        }
        Some("reacquire_device") | Some("abort_release") => {
            let dev_req = req["device"].as_str().unwrap_or("");
            let target_path = PathBuf::from(dev_req);
            // Rollback may restore a claim after the scan observed its temporary removal.
            let mut current = session.clone();
            if let Err(error) = current.refresh_controllers(proc_root) {
                let resp = serde_json::json!({"ok":false,"nonce":nonce,"error":error});
                let _ = writeln!(stream, "{resp}");
                return resp;
            }
            if !target_path.is_absolute()
                || !current
                    .controllers
                    .iter()
                    .any(|c| paths_match_exact_or_canonical(c, &target_path))
            {
                let resp = serde_json::json!({
                    "ok": false,
                    "nonce": nonce,
                    "seat": session.seat,
                    "error": format!("device {} is not leased to this nested session", target_path.display()),
                });
                let _ = writeln!(stream, "{resp}");
                return resp;
            }
            {
                let mut rel = released_devices.lock().unwrap();
                rel.retain(|p| !paths_match_exact_or_canonical(p, &target_path));
            }
            {
                let mut known = known_devices.lock().unwrap();
                known.retain(|p| !paths_match_exact_or_canonical(p, &target_path));
            }
            let resp = serde_json::json!({
                "ok": true,
                "nonce": nonce,
                "seat": session.seat,
                "device": target_path.display().to_string(),
                "launcher_pid": session.launcher_pid,
                "launcher_start": session.launcher_start,
                "reacquire_armed": true,
            });
            let _ = writeln!(stream, "{resp}");
            resp
        }
        _ => {
            let resp = serde_json::json!({
                "ok": false,
                "nonce": nonce,
                "error": "unsupported control action",
            });
            let _ = writeln!(stream, "{resp}");
            resp
        }
    }
}

/// A neutral listener is not physical-device authority. Only the real host
/// bridge uses this gate; fixtures and nested Claims retain their own rules.
#[derive(Clone)]
struct PhysicalAdmission {
    focus: std::sync::Arc<std::sync::Mutex<ContextSnapshot>>,
    logind_state: PathBuf,
}
impl PhysicalAdmission {
    fn unavailable(
        &self,
        cfg: &GlobalConfig,
        observed: Option<&ContextSnapshot>,
    ) -> Option<String> {
        let snapshot = match observed {
            Some(snapshot) => snapshot.clone(),
            None => match self.focus.lock() {
                Ok(snapshot) => snapshot.clone(),
                Err(_) => return Some("physical focus state unavailable".into()),
            },
        };
        physical_readiness(cfg, &snapshot, || {
            // Re-read active-seat ownership before the original device/uinput
            // probes, so a cached local snapshot cannot admit a greeter pad.
            // SAFETY: getuid only reads the process kernel identity.
            let uid = unsafe { libc::getuid() };
            if let Seat::Foreign(reason) = seat_view(&self.logind_state, uid).seat {
                Some(reason)
            } else {
                seat_unavailable()
            }
        })
    }
}
fn physical_readiness(
    cfg: &GlobalConfig,
    focus: &ContextSnapshot,
    local_access: impl FnOnce() -> Option<String>,
) -> Option<String> {
    // Preserve existing explicit SessionRoute admission. Its own session_focus,
    // target verification and Claim ownership still decide grab/output later.
    if !cfg.controller.sessions.is_empty() {
        return None;
    }
    if focus.remote {
        return if fresh_native_target(focus, cfg).is_some_and(|target| {
            target.login_generation != 0 && target.login_generation == focus.routing_generation
        }) {
            None
        } else {
            Some("waiting for a fresh authenticated remote target".into())
        };
    }
    if let Some(reason) = &focus.suspended {
        return Some(reason.clone());
    }
    if focus.sampled.elapsed() >= Duration::from_millis(cfg.controller.context_stale_ms) {
        return Some("waiting for fresh local seat focus".into());
    }
    local_access()
}
fn retry_device_directory(error: &std::io::Error) -> bool {
    matches!(
        error.kind(),
        std::io::ErrorKind::NotFound | std::io::ErrorKind::PermissionDenied
    )
}
fn discovery_idle(reported: &mut Option<String>, reason: String, stop: &AtomicBool, scan_ms: u64) {
    if reported.as_deref() != Some(reason.as_str()) {
        eprintln!(
            "{}",
            serde_json::json!({"discovery":"idle","reason":reason})
        );
        *reported = Some(reason);
    }
    let until = Instant::now() + Duration::from_millis(scan_ms);
    while !stop.load(Ordering::Relaxed) && Instant::now() < until {
        std::thread::sleep(
            until
                .saturating_duration_since(Instant::now())
                .min(Duration::from_millis(20)),
        );
    }
}

#[allow(clippy::too_many_arguments)]
fn discovery_worker(
    explicit: Vec<PathBuf>,
    fixture_only: bool,
    policy: std::sync::Arc<std::sync::Mutex<GlobalConfig>>,
    known: std::sync::Arc<std::sync::Mutex<BTreeSet<PathBuf>>>,
    stop: std::sync::Arc<AtomicBool>,
    assigned: Assigned,
    nested: bool,
    raw_consumers: RawConsumers,
    released: ReleasedDevices,
    physical: Option<PhysicalAdmission>,
) -> Result<Discovery, String> {
    use std::os::unix::fs::MetadataExt;
    let (sender, receiver) = std::sync::mpsc::sync_channel(16);
    let worker=std::thread::Builder::new().name("mios-devices".into()).spawn(move || {
        let mut rejected=BTreeMap::new();let mut sent=BTreeMap::new();let mut previous=BTreeSet::new();
        let mut skipped:BTreeSet<PathBuf>=BTreeSet::new();
        let mut idle_reason:Option<String>=None;
        // I4 --nested: a session node that vanished (its pad was destroyed) is gone for good: the bridge keeps serving
        // the session's other controllers, and never opens whatever device the kernel later gives the same path.
        let mut first_seen:BTreeMap<PathBuf,(u64,u64)>=BTreeMap::new();
        let mut gone:BTreeSet<PathBuf>=BTreeSet::new();
        while !stop.load(Ordering::Relaxed) {
            let current=known.lock().unwrap().clone();
            for path in previous.difference(&current) {sent.remove(path);}
            previous=current.clone();
            // Candidates declined by the forwarding loop (new nested claim, missing local sink) retry next scan.
            sent.retain(|path,_| current.contains(path));
            // Copy policy before touching focus or logind files: the context
            // and handoff workers also use these locks in separate steps.
            let cfg=policy.lock().unwrap().clone();
            if let Some(admission)=&physical {
                if let Some(reason)=admission.unavailable(&cfg,None) {
                    discovery_idle(&mut idle_reason,reason,&stop,cfg.controller.scan_ms);
                    continue;
                }
            }
            let paths=if !explicit.is_empty() {Ok(explicit.clone())}else{match linux::discover() {
                Ok(paths)=>Ok(paths),
                Err(error) if physical.is_some() && retry_device_directory(&error)=>{
                    discovery_idle(&mut idle_reason,format!("no /dev/input access: {error}"),&stop,cfg.controller.scan_ms);
                    continue;
                }
                Err(error)=>Err(error.to_string()),
            }};
            if idle_reason.take().is_some() {eprintln!("{}",serde_json::json!({"discovery":"ready"}));}
            let held=assigned.lock().map(|a|a.clone()).unwrap_or_default();
            if !nested {
                for h in held.keys() {
                    rejected.remove(h);
                    sent.remove(h);
                    raw_consumers.lock().unwrap().remove(h);
                }
            }
            skipped.retain(|p|held.contains_key(p));
            let paths=match paths {Ok(v)=>v,Err(e)=>{let _=sender.try_send(Err(e));break}};
            rejected.retain(|path,_|paths.contains(path));sent.retain(|path,_|paths.contains(path));
            let released_now = if nested {
                released.lock().map(|r| r.clone()).unwrap_or_default()
            } else {
                BTreeSet::new()
            };
            for path in paths {
                if stop.load(Ordering::Relaxed) {break;}
                if current.contains(&path) {continue;}
                if let Some(admission)=&physical {
                    let cfg=policy.lock().unwrap().clone();
                    if let Some(reason)=admission.unavailable(&cfg,None) {
                        discovery_idle(&mut idle_reason,reason,&stop,cfg.controller.scan_ms);
                        break;
                    }
                }
                if nested && released_now.iter().any(|r| paths_match_exact_or_canonical(r, &path)) {
                    sent.remove(&path);
                    continue;
                }
                // I4: a node a live nested session holds belongs to that session (and its own `bridge --nested`).
                if let Some(seat)=held.get(&path) {
                    if skipped.insert(path.clone()) { eprintln!("{}",serde_json::json!({"nested":"skipped","device":path,"seat":seat})); }
                    continue;
                }
                let identity=std::fs::metadata(&path).ok().map(|m|(m.rdev(),m.ino()));
                if identity.is_some_and(|id|rejected.get(&path)==Some(&id)||sent.get(&path)==Some(&id)) {continue;}
                if nested {
                    if gone.contains(&path) {continue;}
                    let replaced=match (identity,first_seen.get(&path)) {(Some(id),Some(first))=>id!=*first,(None,Some(_))=>true,_=>false};
                    if replaced {
                        gone.insert(path.clone());
                        eprintln!("{}",serde_json::json!({"nested":"gone","device":path}));
                        continue;
                    }
                    if let Some(id)=identity {first_seen.entry(path.clone()).or_insert(id);}
                }
                match Device::open(&path,fixture_only) {
                    // A seat's agent pad ([desktop.nested.devices].virtual_pad_name) is never the host's controller.
                    Ok(device) if !nested && crate::nested::seat_pad_name_matches(&policy.lock().unwrap().seat_pad_name,&device.name) => {
                        eprintln!("{}",serde_json::json!({"nested":"seat-pad","device":path,"name":device.name}));
                        drop(device);
                        if let Some(id)=identity {rejected.insert(path,id);}
                    }
                    Ok(device)=> {
                        raw_consumers.lock().unwrap().remove(&path);
                        if sender.try_send(Ok(device)).is_ok() {if let Some(id)=identity {sent.insert(path,id);}}
                    }
                    Err(e) if nested && first_seen.contains_key(&path) && (e.kind()==std::io::ErrorKind::NotFound || e.raw_os_error()==Some(libc::ENODEV)) => {
                        gone.insert(path.clone());
                        eprintln!("{}",serde_json::json!({"nested":"gone","device":path}));
                    }
                    Err(e)=> {
                        if e.raw_os_error() == Some(libc::EBUSY) {
                            raw_consumers.lock().unwrap().insert(path.clone(), "device held by raw consumer (EBUSY)".into());
                        }
                        if !explicit.is_empty() {let _=sender.try_send(Err(format!("{}: {e}",path.display())));return;}
                        // logind may grant this same node an ACL after the service starts, and EBUSY clears once a raw holder closes; retry both.
                        if e.kind()!=std::io::ErrorKind::PermissionDenied && e.raw_os_error()!=Some(libc::EBUSY) {
                            if let Some(id)=identity {rejected.insert(path,id);}
                        }}
                }
            }
            let delay=Duration::from_millis(policy.lock().unwrap().controller.scan_ms);
            let until=Instant::now()+delay;
            while !stop.load(Ordering::Relaxed)&&Instant::now()<until {
                std::thread::sleep(until.saturating_duration_since(Instant::now()).min(Duration::from_millis(20)));
            }
        }
    }).map_err(|e|e.to_string())?;
    Ok((receiver, worker))
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
fn uinput_unavailable() -> Option<String> {
    use std::os::unix::fs::OpenOptionsExt;
    std::fs::OpenOptions::new()
        .write(true)
        .custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC)
        .open("/dev/uinput")
        .err()
        .map(|e| format!("no /dev/uinput access: {e}"))
}
pub fn seat_unavailable() -> Option<String> {
    if let Some(reason) = uinput_unavailable() {
        return Some(reason);
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
/// user permits local discovery, a VT login included; SSH-only local hardware idles. The authenticated remote
/// route is checked separately. Another session being active on the seat does
/// not stop serve: the bridge then holds no grab and routes nothing until this user's session returns (INV-8).
pub(crate) fn seat_from_logind_state(state: &Path, uid: u32) -> Option<String> {
    let user = state.join("users").join(uid.to_string());
    if let Err(e) = std::fs::metadata(&user) {
        return Some(format!(
            "logind state unavailable ({}): {e}",
            user.display()
        ));
    }
    match seat_view(state, uid).seat {
        Seat::Foreign(r)
            if r.starts_with("no seated session") || r.starts_with("no logind record") =>
        {
            Some(r)
        }
        _ => None,
    }
}
/// The user-unit entry point (C-05). Disabled by SSOT: exit 0. The neutral authority starts without a physical seat;
/// device discovery idles until authorized focus and access are available. A bridge error: exit non-zero, so systemd restarts it
/// within the unit's start limit. There is no fallback process that could drive a game and the room at once.
pub fn serve(
    global: GlobalConfig,
    legacy: LegacyConfig,
    root: Option<PathBuf>,
) -> Result<(), String> {
    let disabled =
        |g: &GlobalConfig| !g.enabled || !g.controller.enabled || !g.controller.exclusive_grab;
    if disabled(&global) {
        eprintln!(
            "{}",
            serde_json::json!({"serve":"disabled","reason":"SSOT disables the controller bridge","exit":0})
        );
        return Ok(());
    }
    // SAFETY: geteuid has no memory/ownership effects.
    if unsafe { libc::geteuid() } == 0 {
        return Err("real input bridge must run as the active desktop user".into());
    }
    // The singleton and neutral handoff authority must be reachable even at
    // the greeter or without /dev/input. Physical discovery and admission are
    // gated inside run; listener startup grants no device access or routing.
    let pad_path = global.controller.legacy_pad_state_path.clone();
    run(
        global,
        legacy,
        Options {
            root,
            bridge: true,
            fixture_only: false,
            devices: vec![],
            context: None,
            duration: None,
            pad_path,
            live_context: false,
            write_pad: true,
            logind_state: Some(PathBuf::from("/run/systemd")),
            console_tty: None,
            console_sink_fd: None,
            rediscover: true,
            nested: None,
        },
    )
}
pub fn run(
    mut global: GlobalConfig,
    mut legacy: LegacyConfig,
    mut opt: Options,
) -> Result<(), String> {
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
    if let Some(e) = &global.controller.nav.app_error {
        // Backward compatibility: the base navigation runs; only the app layer is off.
        eprintln!("{}", serde_json::json!({"nav_apps":"disabled","error":e}));
    }
    if opt.fixture_only && opt.devices.is_empty() && opt.nested.is_none() {
        return Err("fixture mode requires one explicit owned device".into());
    }
    if opt
        .nested
        .as_ref()
        .is_some_and(|n| n.controllers.is_empty())
    {
        return Err("the nested session holds no controller node (launch it with --device virtual-pad or a pad's /dev/input path)".into());
    }
    if opt.live_context && !opt.fixture_only {
        return Err("--live-context is a fixture-tier option (the physical bridge always follows its compositor)".into());
    }
    if !opt.fixture_only && (opt.console_tty.is_some() || opt.console_sink_fd.is_some()) {
        return Err("--console-tty and --console-sink-fd are fixture-tier options".into());
    }
    if opt.fixture_only && opt.write_pad && opt.pad_path == global.controller.legacy_pad_state_path
    {
        return Err(
            "a fixture never writes the live room packet; pass a private --pad-path".into(),
        );
    }
    let runtime = std::env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .ok_or("XDG_RUNTIME_DIR missing")?;
    let lockname = if opt.nested.is_some() {
        "mios-inputd-nested.lock"
    } else if opt.fixture_only {
        "mios-inputd-fixture.lock"
    } else {
        "mios-inputd.lock"
    };
    let _lock = linux::singleton(&runtime.join(lockname)).map_err(|e| e.to_string())?;
    install_stop_handlers();
    STOP.store(false, Ordering::Relaxed);
    let mut devices: BTreeMap<PathBuf, RunningDevice> = BTreeMap::new();
    let follow = opt.context.is_none()
        && (!opt.fixture_only
            || opt.live_context
            || opt.logind_state.is_some()
            || opt.nested.is_some());
    let nested = opt.nested.is_some();
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
    let mut frontend_manifest_at = start;
    let mut last_reconcile = start;
    let mut focus = ContextSnapshot::new(opt.context.as_deref().unwrap_or("app-capture"));
    // INV-8: with a seat view, nothing is grabbed or routed before the first view says this user's session is active.
    if opt.logind_state.is_some() && follow {
        focus.suspended = Some("seat not read yet".into());
    }
    let console_sink = match opt.console_sink_fd {
        // SAFETY: the fixture caller passed this inherited descriptor for exactly this use; it is owned from here on.
        Some(fd) if fd > 2 => {
            Some(unsafe { <std::fs::File as std::os::fd::FromRawFd>::from_raw_fd(fd) })
        }
        Some(_) => return Err("--console-sink-fd must be an inherited descriptor above 2".into()),
        None => None,
    };
    // The placeholder context (app-capture until the compositor answers) is no evidence that an app owns the pad.
    let boot_sample = focus.sampled;
    let shared_focus = std::sync::Arc::new(std::sync::Mutex::new(focus.clone()));
    let shared_policy = std::sync::Arc::new(std::sync::Mutex::new(global.clone()));
    let login_selection: SharedLogin = Default::default();
    let app_authority = app_authority();
    let _handoff_service = if follow && opt.bridge && !nested && !opt.fixture_only {
        Some(HandoffService::spawn(
            login_selection.clone(),
            shared_focus.clone(),
            shared_policy.clone(),
        )?)
    } else {
        None
    };
    let mut sinks = Sinks {
        pointer: None,
        remote_pointer: None,
        ownership_generation: 0,
        children: vec![],
        ownership: if follow && opt.bridge {
            Some(OwnershipWorker::spawn(
                shared_policy.clone(),
                login_selection.clone(),
            )?)
        } else {
            None
        },
        actions: if opt.bridge
            && (!opt.fixture_only || opt.live_context || nested || console_sink.is_some())
        {
            Some(ActionWorker::spawn(
                shared_policy.clone(),
                login_selection.clone(),
                shared_focus.clone(),
            )?)
        } else {
            None
        },
        keyboard: None,
        bridge: opt.bridge,
        execute_actions: opt.bridge && !opt.fixture_only,
        project: opt.bridge && ((opt.fixture_only && opt.live_context) || nested),
        console_sink,
        nested,
    };
    let context_stop = std::sync::Arc::new(AtomicBool::new(false));
    let cursor_requested = std::sync::Arc::new(AtomicBool::new(false));
    let cursor_signal = cursor_requested.clone();
    let context_worker = if follow {
        let authority = app_authority.clone();
        let cache = shared_focus.clone();
        let selection = login_selection.clone();
        let policy = shared_policy.clone();
        let stop = context_stop.clone();
        let (logind, console_tty, rediscover_on) = (
            opt.logind_state.clone(),
            opt.console_tty.clone(),
            opt.rediscover,
        );
        Some(std::thread::spawn(move || {
            let mut events = event_socket();
            // SAFETY: getuid has no memory/ownership effects.
            let uid = unsafe { libc::getuid() };
            let mut tmux_cache: Option<(u64, Instant, Option<TmuxClient>)> = None;
            let mut vt_cache: Option<(PathBuf, Instant, Option<TmuxClient>)> = None;
            let mut app_cache = AppCache::default();
            let mut last_rediscover = Instant::now() - Duration::from_secs(1);
            let mut compositor_lost: Option<Instant> = None;
            let runtime = std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from);
            let mut reported: Option<String> = None;
            while !stop.load(Ordering::Relaxed) {
                let cfg = policy.lock().unwrap().clone();
                let view = logind.as_deref().map(|state| seat_view(state, uid));
                let graphical_vt = view.as_ref().and_then(|v| v.graphical_vt);
                let selected = {
                    let mut state = selection.lock().unwrap();
                    let local_active = matches!(
                        view.as_ref().map(|v| &v.seat),
                        Some(Seat::Console { .. }) | Some(Seat::Graphical)
                    );
                    let remote = state.reconcile(local_active, crate::tmux_handoff::live);
                    (state.generation, remote)
                };
                if let Some(remote) = selected.1 {
                    if let Ok(mut snapshot) = native_snapshot(&cfg, &remote.witness.client) {
                        snapshot.remote = true;
                        snapshot.routing_generation = remote.generation;
                        if let Some(target) = &mut snapshot.native_target {
                            target.login_generation = remote.generation;
                            snapshot.address = Some(target.address());
                        }
                        verify_app(&cfg, &mut snapshot, "console", None, &mut app_cache, uid);
                        publish_focus(
                            &cache,
                            snapshot,
                            authority.as_ref(),
                            cfg.controller.context_stale_ms,
                        );
                    } else {
                        let mut snapshot = ContextSnapshot::new("console");
                        snapshot.remote = true;
                        snapshot.routing_generation = remote.generation;
                        snapshot.suspended =
                            Some("authenticated SSH target is temporarily unavailable".into());
                        publish_focus(
                            &cache,
                            snapshot,
                            authority.as_ref(),
                            cfg.controller.context_stale_ms,
                        );
                    }
                    std::thread::sleep(Duration::from_millis(
                        cfg.controller.context_poll_ms.max(20),
                    ));
                    continue;
                }

                // C-16: the console context (a VT) when this user's tty session is active, or when the graphical
                // session's compositor is gone for good (no live instance at all); INV-8: suspended when another
                // session is active. The tty path may be a test pty on the fixture tier.
                let mut console: Option<PathBuf> = None;
                match view.as_ref().map(|v| &v.seat) {
                    Some(Seat::Foreign(reason)) => {
                        let mut snap = ContextSnapshot::new("console");
                        snap.suspended = Some(reason.clone());
                        snap.routing_generation = selected.0;
                        snap.graphical_vt = graphical_vt;
                        if reported.as_deref() != Some(reason.as_str()) {
                            eprintln!("{}", serde_json::json!({"seat":"foreign","reason":reason}));
                            reported = Some(reason.clone());
                        }
                        publish_focus(
                            &cache,
                            snap,
                            authority.as_ref(),
                            cfg.controller.context_stale_ms,
                        );
                        std::thread::sleep(Duration::from_millis(
                            cfg.controller.context_poll_ms.max(20),
                        ));
                        continue;
                    }
                    Some(Seat::Console { tty, .. }) => {
                        console = Some(console_tty.clone().unwrap_or_else(|| tty.clone()))
                    }
                    _ => {}
                }
                let result = if console.is_some() {
                    Err("console".to_string())
                } else {
                    context_with_cursor(&cfg, cursor_signal.load(Ordering::Relaxed))
                };
                if console.is_none()
                    && result.is_err()
                    && rediscover_on
                    && last_rediscover.elapsed() >= Duration::from_millis(250)
                {
                    last_rediscover = Instant::now();
                    if let Some(rt) = &runtime {
                        let current = signature().ok();
                        match rediscover(rt, Path::new("/proc"), current.as_deref()) {
                            Some(sig) => {
                                compositor_lost = None;
                                if current.as_deref() != Some(sig.as_str()) {
                                    eprintln!(
                                        "{}",
                                        serde_json::json!({"compositor":"rediscovered","signature":sig,"previous":current})
                                    );
                                    if let Ok(mut g) = SIGNATURE.lock() {
                                        *g = Some(sig);
                                    }
                                    events = None;
                                }
                            }
                            None => {
                                compositor_lost.get_or_insert_with(Instant::now);
                            }
                        }
                    }
                }
                if console.is_none()
                    && result.is_err()
                    && view.is_some()
                    && compositor_lost.is_some_and(|t| {
                        t.elapsed() >= Duration::from_millis(cfg.controller.context_stale_ms)
                    })
                {
                    if let Some(vt) = graphical_vt {
                        console = Some(
                            console_tty
                                .clone()
                                .unwrap_or_else(|| PathBuf::from(format!("/dev/tty{vt}"))),
                        );
                    }
                }
                if let Some(tty) = console {
                    let mut snap = ContextSnapshot::new("console");
                    snap.graphical_vt = graphical_vt;
                    let fresh = vt_cache.as_ref().is_some_and(|(t, at, _)| {
                        *t == tty && at.elapsed() < Duration::from_millis(1000)
                    });
                    if !fresh {
                        let found = if wants_tmux(&cfg, "console") {
                            tmux_client_on_tty(
                                Path::new("/proc"),
                                &tty,
                                &cfg.controller.nav.tmux_sockets,
                                uid,
                            )
                        } else {
                            None
                        };
                        vt_cache = Some((tty.clone(), Instant::now(), found));
                    }
                    snap.tmux_client = vt_cache.as_ref().and_then(|(_, _, c)| c.clone());
                    if let Some(client) = &snap.tmux_client {
                        match native_snapshot(&cfg, client) {
                            Ok(mut native) => {
                                native.graphical_vt = graphical_vt;
                                snap = native;
                            }
                            Err(_) => {
                                snap.tmux_client = None;
                                snap.suspended =
                                    Some("native pane focus could not be verified".into());
                            }
                        }
                    }
                    snap.routing_generation = selected.0;
                    if let Some(target) = &mut snap.native_target {
                        target.login_generation = selected.0;
                        snap.address = Some(target.address());
                    }
                    verify_app(&cfg, &mut snap, "console", Some(&tty), &mut app_cache, uid);
                    if reported.as_deref() != Some("console") {
                        eprintln!("{}", serde_json::json!({"seat":"console","tty":tty}));
                        reported = Some("console".into());
                    }
                    publish_focus(
                        &cache,
                        snap,
                        authority.as_ref(),
                        cfg.controller.context_stale_ms,
                    );
                    std::thread::sleep(Duration::from_millis(
                        cfg.controller.context_poll_ms.max(20),
                    ));
                    continue;
                }
                if result.is_ok() {
                    compositor_lost = None;
                }
                if view.is_some() && result.is_ok() && reported.as_deref() != Some("graphical") {
                    eprintln!("{}", serde_json::json!({"seat":"graphical"}));
                    reported = Some("graphical".into());
                }
                // A failed IPC read never invents terminal focus or resets held input.
                if let Ok(mut snapshot) = result {
                    snapshot.graphical_vt = graphical_vt;
                    snapshot.routing_generation = selected.0;
                    snapshot.shell_overlay = runtime
                        .as_ref()
                        .is_some_and(|r| shell_overlay_open(r, &cfg));
                    // C-09: discover and witness at most once a second per focused terminal;
                    // server inspection stays off lock and within this snapshot's remaining stale budget.
                    let nav_context = capture_navigation_context(&cfg, snapshot.class.as_deref())
                        .unwrap_or_else(|| snapshot.name.clone());
                    if let (Some(pid), true) = (snapshot.pid, wants_tmux(&cfg, &nav_context)) {
                        let fresh = tmux_cache.as_ref().is_some_and(|(p, t, _)| {
                            *p == pid && t.elapsed() < Duration::from_millis(1000)
                        });
                        if !fresh {
                            let found = u32::try_from(pid).ok().and_then(|p| {
                                graphical_tmux_client(
                                    &cfg,
                                    p,
                                    uid,
                                    Duration::from_millis(cfg.controller.context_stale_ms)
                                        .saturating_sub(snapshot.sampled.elapsed()),
                                )
                            });
                            tmux_cache = Some((pid, Instant::now(), found));
                        }
                        snapshot.tmux_client = tmux_cache.as_ref().and_then(|(_, _, c)| c.clone());
                    }
                    // P3: the foreground app of exactly this snapshot's window (never the shell overlay or the room).
                    if !snapshot.shell_overlay && snapshot.name != "desktop-3d" {
                        verify_app(&cfg, &mut snapshot, &nav_context, None, &mut app_cache, uid);
                    }
                    publish_focus(
                        &cache,
                        snapshot,
                        authority.as_ref(),
                        cfg.controller.context_stale_ms,
                    );
                } else {
                    // A layer-input lease cannot outlive a failed compositor verification.
                    if let Ok(mut snapshot) = cache.lock() {
                        snapshot.shell_overlay = false;
                    }
                }
                wait_context_event(&mut events, cfg.controller.context_poll_ms);
            }
        }))
    } else {
        None
    };
    let known_devices = std::sync::Arc::new(std::sync::Mutex::new(BTreeSet::new()));
    let raw_consumers: RawConsumers = std::sync::Arc::new(std::sync::Mutex::new(BTreeMap::new()));
    let released_devices: ReleasedDevices =
        std::sync::Arc::new(std::sync::Mutex::new(BTreeSet::new()));
    // The registry lives in the host runtime (a nested bridge already switched XDG_RUNTIME_DIR to its session's).
    let registry = if nested {
        None
    } else {
        std::env::var_os("XDG_RUNTIME_DIR").map(|r| PathBuf::from(r).join(&global.nested_registry))
    };
    let assigned: Assigned = std::sync::Arc::new(std::sync::Mutex::new(
        registry
            .as_deref()
            .map(|r| crate::nested::assigned_nodes(r, Path::new("/proc")))
            .unwrap_or_default(),
    ));
    let explicit = opt
        .nested
        .as_ref()
        .map(|n| n.controllers.clone())
        .unwrap_or_else(|| opt.devices.clone());
    let physical_admission = if opt.bridge && !opt.fixture_only && !nested {
        Some(PhysicalAdmission {
            focus: shared_focus.clone(),
            logind_state: opt
                .logind_state
                .clone()
                .unwrap_or_else(|| PathBuf::from("/run/systemd")),
        })
    } else {
        None
    };
    let (new_devices, discovery) = discovery_worker(
        explicit,
        opt.fixture_only,
        shared_policy.clone(),
        known_devices.clone(),
        context_stop.clone(),
        assigned.clone(),
        nested,
        raw_consumers.clone(),
        released_devices.clone(),
        physical_admission.clone(),
    )?;
    let mut ctx = focus.name.clone();
    let mut config_stamp = config_fingerprint(opt.root.as_deref());
    let mut local_sink_error = if opt.bridge && !opt.fixture_only && !nested {
        uinput_unavailable()
    } else {
        None
    };

    let child_control_sock = opt
        .nested
        .as_ref()
        .map(|n| n.runtime.join("inputd-control.sock"));
    let child_control_listener = if let Some(p) = &child_control_sock {
        let _ = std::fs::remove_file(p);
        match std::os::unix::net::UnixListener::bind(p) {
            Ok(l) => {
                let _ = l.set_nonblocking(true);
                let _ = std::fs::set_permissions(
                    p,
                    std::os::unix::fs::PermissionsExt::from_mode(0o600),
                );
                Some(l)
            }
            Err(e) => {
                eprintln!(
                    "warning: cannot bind child inputd control socket {}: {e}",
                    p.display()
                );
                None
            }
        }
    } else {
        None
    };

    if !nested {
        update_active_manifest(
            &runtime,
            &devices,
            &raw_consumers.lock().unwrap(),
            opt.bridge,
            global.controller.scan_ms,
            ManifestFocus {
                frontend: native_frontend(
                    &focus,
                    &global,
                    cursor_requested.load(Ordering::Relaxed),
                )
                .as_ref(),
                stale_ms: global.controller.context_stale_ms,
                target: fresh_native_target(&focus, &global),
                owned: native_owned(&focus, &global),
                remote: focus.remote,
                focus: Some(&focus),
            },
        );
    }

    let result = (|| -> Result<(), String> {
        while !STOP.load(Ordering::Relaxed) && opt.duration.is_none_or(|d| start.elapsed() < d) {
            if let (Some(ref l), Some(ref nested_session)) = (&child_control_listener, &opt.nested)
            {
                if let Ok((stream, _)) = l.accept() {
                    let active_keys: BTreeSet<PathBuf> = devices.keys().cloned().collect();
                    let expected_uid = unsafe { libc::geteuid() };
                    let _ = handle_child_control_connection(
                        stream,
                        nested_session,
                        Path::new("/proc"),
                        expected_uid,
                        None,
                        &active_keys,
                        &known_devices,
                        &released_devices,
                        |matched_path| {
                            let dev = devices.get(matched_path).ok_or_else(|| {
                                format!(
                                    "device {} not owned by child inputd",
                                    matched_path.display()
                                )
                            })?;
                            let (keys, axes) = dev.device.snapshot().map_err(|e| {
                                format!(
                                    "physical state query failed for {}: {e}",
                                    matched_path.display()
                                )
                            })?;
                            let mut combined_axes = dev.router.axes.clone();
                            for (code, axis) in axes {
                                combined_axes.insert(code, axis);
                            }
                            if let Err(held) = physical_controls_neutral(
                                &keys,
                                &combined_axes,
                                global.controller.stick_deadzone,
                                legacy.trigger_threshold,
                            ) {
                                return Ok(Err(held));
                            }
                            let mut dev = devices.remove(matched_path).unwrap();
                            let out = dev.router.reset();
                            let _ = emit(out, &mut dev, &mut sinks, &focus);
                            dev.device.release();
                            if selected.as_ref() == Some(&matched_path.to_path_buf()) {
                                selected = None;
                            }
                            eprintln!(
                                "{}",
                                serde_json::json!({"nested":"released","device":matched_path,"cooperative":true})
                            );
                            Ok(Ok(()))
                        },
                    );
                }
            }

            if scan.elapsed() >= Duration::from_millis(global.controller.scan_ms) {
                scan = Instant::now();
                if opt.bridge && !opt.fixture_only && !nested {
                    local_sink_error = uinput_unavailable();
                }
                if let Some(n) = &mut opt.nested {
                    if !n.alive(Path::new("/proc")) {
                        eprintln!("{}", serde_json::json!({"nested":"ended","seat":n.seat}));
                        break;
                    }
                    if let Ok(current_controllers) = n.refresh_controllers(Path::new("/proc")) {
                        let removed: Vec<PathBuf> = devices
                            .keys()
                            .filter(|p| !current_controllers.contains(*p))
                            .cloned()
                            .collect();
                        for path in removed {
                            {
                                let mut rel = released_devices.lock().unwrap();
                                rel.insert(path.clone());
                                if let Ok(canon) = std::fs::canonicalize(&path) {
                                    rel.insert(canon);
                                }
                            }
                            if let Some(mut dev) = devices.remove(&path) {
                                let out = dev.router.reset();
                                emit(out, &mut dev, &mut sinks, &focus)?;
                                dev.device.release();
                                known_devices.lock().unwrap().remove(&path);
                                if selected.as_ref() == Some(&path) {
                                    selected = None;
                                }
                                eprintln!(
                                    "{}",
                                    serde_json::json!({"nested":"released","device":path,"seat":n.seat,"refreshed":true})
                                );
                            }
                        }
                    }
                }
                if !nested {
                    update_active_manifest(
                        &runtime,
                        &devices,
                        &raw_consumers.lock().unwrap(),
                        opt.bridge,
                        global.controller.scan_ms,
                        ManifestFocus {
                            frontend: native_frontend(
                                &focus,
                                &global,
                                cursor_requested.load(Ordering::Relaxed),
                            )
                            .as_ref(),
                            stale_ms: global.controller.context_stale_ms,
                            target: fresh_native_target(&focus, &global),
                            owned: native_owned(&focus, &global),
                            remote: focus.remote,
                            focus: Some(&focus),
                        },
                    );
                }
                if let Some(r) = &registry {
                    let now = crate::nested::assigned_nodes(r, Path::new("/proc"));
                    // A node a session claimed after this bridge owned it: hand it over (outputs to rest, grab released).
                    let taken: Vec<PathBuf> = devices
                        .keys()
                        .filter(|p| now.contains_key(*p))
                        .cloned()
                        .collect();
                    for path in taken {
                        if let Some(mut dev) = devices.remove(&path) {
                            let out = dev.router.reset();
                            emit(out, &mut dev, &mut sinks, &focus)?;
                            dev.device.release();
                            known_devices.lock().unwrap().remove(&path);
                            if selected.as_ref() == Some(&path) {
                                selected = None;
                            }
                            eprintln!(
                                "{}",
                                serde_json::json!({"nested":"released","device":path,"seat":now[&path]})
                            );
                        }
                    }
                    if !nested {
                        update_active_manifest(
                            &runtime,
                            &devices,
                            &raw_consumers.lock().unwrap(),
                            opt.bridge,
                            global.controller.scan_ms,
                            ManifestFocus {
                                frontend: native_frontend(
                                    &focus,
                                    &global,
                                    cursor_requested.load(Ordering::Relaxed),
                                )
                                .as_ref(),
                                stale_ms: global.controller.context_stale_ms,
                                target: fresh_native_target(&focus, &global),
                                owned: native_owned(&focus, &global),
                                remote: focus.remote,
                                focus: Some(&focus),
                            },
                        );
                    }
                    if let Ok(mut a) = assigned.lock() {
                        *a = now;
                    }
                }
                // Resolve stable aliases on each discovery scan; ambiguity reserves the device and routes nothing.
                for dev in devices.values_mut() {
                    if nested {
                        continue;
                    }
                    let selection = crate::sessions::route_for_device(
                        &global.controller.sessions,
                        &dev.device.path,
                    );
                    let next_session = selection.as_ref().ok().cloned().flatten();
                    let next_error = selection.err();
                    if next_session != dev.session || next_error != dev.session_error {
                        let out = dev.router.reset();
                        emit(out, dev, &mut sinks, &focus)?;
                        // Neutralize any previous raw clone before changing the output boundary.
                        if let Some(pad) = &mut dev.output {
                            pad.write(&dev.device.physical_events(&dev.router.reset().app))
                                .map_err(|e| e.to_string())?;
                        }
                        dev.session_worker = None;
                        dev.capture = None;
                        dev.session = next_session;
                        dev.session_error = next_error;
                        if dev.session.is_some() || dev.session_error.is_some() {
                            dev.router.cursor = false;
                        }
                        dev.session_worker = if sinks.execute_actions && !dev.suspended {
                            dev.session
                                .clone()
                                .map(|r| SessionWorker::spawn(r, global.clone()))
                                .transpose()?
                        } else {
                            None
                        };
                        eprintln!(
                            "{}",
                            serde_json::json!({"device":dev.device.path,"session":dev.session.as_ref().map(|r|&r.name),"error":dev.session_error})
                        );
                    }
                }
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
                                eprintln!(
                                    "{}",
                                    serde_json::json!({"serve":"disabled","reason":"SSOT disabled the controller bridge","exit":0})
                                );
                                break;
                            }
                            if policy_fingerprint(&global, &legacy)?
                                != policy_fingerprint(&next, &next_legacy)?
                            {
                                for dev in devices.values_mut() {
                                    let cursor = dev.router.cursor;
                                    let out = dev.router.reset();
                                    emit(out, dev, &mut sinks, &focus)?;
                                    let seq = dev.router.pad.sequences;
                                    dev.router = Router::new(
                                        next.clone(),
                                        next_legacy.clone(),
                                        dev.device.snapshot().map_err(|e| e.to_string())?.1,
                                    );
                                    dev.router.pad.sequences = seq;
                                    if let Err(e) = dev.device.apply_quirks(&next.controller.quirks)
                                    {
                                        eprintln!("{}", serde_json::json!({"quirks_refused":e}));
                                    }
                                    dev.router.analog_triggers = dev.device.analog_triggers();
                                    let selection = if nested {
                                        Ok(None)
                                    } else {
                                        crate::sessions::route_for_device(
                                            &next.controller.sessions,
                                            &dev.device.path,
                                        )
                                    };
                                    dev.session = selection.as_ref().ok().cloned().flatten();
                                    dev.session_error = selection.err();
                                    dev.session_worker = if sinks.execute_actions && !dev.suspended
                                    {
                                        dev.session
                                            .clone()
                                            .map(|r| SessionWorker::spawn(r, next.clone()))
                                            .transpose()?
                                    } else {
                                        None
                                    };
                                    let target_focus = session_focus(
                                        &focus,
                                        dev.session.is_some(),
                                        dev.session_error.as_deref(),
                                    );
                                    dev.router.tmux =
                                        dev.session.is_some() || target_focus.tmux_client.is_some();
                                    dev.router.set_context(&target_focus.name);
                                    let (keys, axes) =
                                        dev.device.snapshot().map_err(|e| e.to_string())?;
                                    dev.router.resync(keys, axes);
                                    dev.router.set_capture_navigation_context(
                                        capture_navigation_context(
                                            &next,
                                            target_focus.class.as_deref(),
                                        )
                                        .as_deref(),
                                    );
                                    dev.capture = None;
                                    dev.router.cursor = cursor
                                        && dev.session.is_none()
                                        && dev.session_error.is_none();
                                }
                                if next.controller.virtual_pointer
                                    != global.controller.virtual_pointer
                                {
                                    sinks.pointer = None;
                                }
                                *shared_policy
                                    .lock()
                                    .map_err(|_| "context policy lock failed")? = next.clone();
                                global = next;
                                legacy = next_legacy;
                                eprintln!("input policy reloaded from layered SSOT");
                                if let Some(e) = &global.controller.nav.app_error {
                                    eprintln!(
                                        "{}",
                                        serde_json::json!({"nav_apps":"disabled","error":e})
                                    );
                                }
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
            while let Ok(candidate) = new_devices.try_recv() {
                let mut device = candidate?;
                let path = device.path.clone();
                if devices.contains_key(&path) {
                    continue;
                }
                // A handle queued under one login may arrive after focus or
                // seat ownership changed. Recheck before any device ioctl,
                // grab, local clone, or session sink is admitted.
                let candidate_focus = if let Some(admission) = &physical_admission {
                    let observed = match admission.focus.lock() {
                        Ok(snapshot) => snapshot.clone(),
                        Err(_) => continue,
                    };
                    if admission.unavailable(&global, Some(&observed)).is_some() {
                        continue;
                    }
                    observed
                } else {
                    focus.clone()
                };
                if nested
                    && released_devices
                        .lock()
                        .map(|r| r.iter().any(|p| paths_match_exact_or_canonical(p, &path)))
                        .unwrap_or(true)
                {
                    continue;
                }
                // A nested claim can arrive while discovery has a validated handle queued.
                if !nested
                    && assigned
                        .lock()
                        .map(|a| a.contains_key(&path))
                        .unwrap_or(true)
                {
                    eprintln!(
                        "{}",
                        serde_json::json!({"nested":"queued-candidate-released","device":path})
                    );
                    continue;
                }
                let quirks = device
                    .apply_quirks(&global.controller.quirks)
                    .unwrap_or_else(|e| {
                        eprintln!("{}", serde_json::json!({"quirks_refused":e}));
                        vec![]
                    });
                if !quirks.is_empty() {
                    eprintln!(
                        "{}",
                        serde_json::json!({"quirks":quirks,"device":path,"driver":device.driver(),"usb_id":format!("{:04x}:{:04x}",device.usb_id().0,device.usb_id().1)})
                    );
                }
                let mut router = Router::new(global.clone(), legacy.clone(), device.axes.clone());
                router.pad.sequences = seed.sequences;
                router.analog_triggers = device.analog_triggers();
                let selection = if nested {
                    Ok(None)
                } else {
                    crate::sessions::route_for_device(&global.controller.sessions, &path)
                };
                let session = selection.as_ref().ok().cloned().flatten();
                let session_error = selection.err();
                let target_focus = session_focus(
                    &candidate_focus,
                    session.is_some(),
                    session_error.as_deref(),
                );
                router.set_capture_navigation_context(
                    capture_navigation_context(&global, target_focus.class.as_deref()).as_deref(),
                );
                router.tmux = session.is_some() || target_focus.tmux_client.is_some();
                router.set_context(&target_focus.name);
                let neutral_rearming = match device.snapshot() {
                    Ok((keys, axes)) => {
                        let not_neutral = physical_controls_neutral(
                            &keys,
                            &axes,
                            global.controller.stick_deadzone,
                            legacy.trigger_threshold,
                        )
                        .is_err();
                        if !nested && not_neutral {
                            true
                        } else {
                            router.resync(keys, axes);
                            false
                        }
                    }
                    Err(_) => !nested,
                };
                // A pad that joins while a capture is pending follows the session's ownership at once.
                if session.is_none() && session_error.is_none() {
                    if let Some(owned) = devices
                        .values()
                        .filter(|d| d.session.is_none() && d.session_error.is_none())
                        .find_map(|d| d.router.ownership())
                    {
                        router.adopt_ownership(owned);
                    }
                }
                let suspended = target_focus.suspended.is_some();
                if physical_admission.as_ref().is_some_and(|admission| {
                    admission
                        .unavailable(&global, Some(&target_focus))
                        .is_some()
                }) {
                    continue;
                }
                if opt.bridge
                    && !opt.fixture_only
                    && !nested
                    && !suspended
                    && !target_focus.remote
                    && session.is_none()
                    && session_error.is_none()
                {
                    if let Some(reason) = &local_sink_error {
                        eprintln!(
                            "{}",
                            serde_json::json!({"device":path,"local_sink":"unavailable","reason":reason})
                        );
                        continue;
                    }
                }
                let output = if opt.bridge {
                    if !suspended {
                        if let Err(e) = device.grab() {
                            if e.raw_os_error() == Some(libc::EBUSY) {
                                raw_consumers.lock().unwrap().insert(
                                    path.clone(),
                                    format!("device held by raw consumer (EBUSY): {e}"),
                                );
                                if !nested {
                                    update_active_manifest(
                                        &runtime,
                                        &devices,
                                        &raw_consumers.lock().unwrap(),
                                        opt.bridge,
                                        global.controller.scan_ms,
                                        ManifestFocus {
                                            frontend: native_frontend(
                                                &focus,
                                                &global,
                                                cursor_requested.load(Ordering::Relaxed),
                                            )
                                            .as_ref(),
                                            stale_ms: global.controller.context_stale_ms,
                                            target: fresh_native_target(&focus, &global),
                                            owned: native_owned(&focus, &global),
                                            remote: focus.remote,
                                            focus: Some(&focus),
                                        },
                                    );
                                }
                                // A raw holder may outlive abort_release. Keep the
                                // child alive and retry discovery after it lets go.
                                continue;
                            }
                            return Err(format!("{}: cannot own input: {e}", path.display()));
                        }
                        raw_consumers.lock().unwrap().remove(&path);
                    }
                    // A nested session sees only its own /dev/input: no clone (it would appear on the host).
                    if nested
                        || target_focus.remote
                        || session.is_some()
                        || session_error.is_some()
                        || suspended
                    {
                        None
                    } else {
                        Some(VirtualDevice::clone_gamepad(&device).map_err(|e| e.to_string())?)
                    }
                } else {
                    None
                };
                eprintln!(
                    "{}",
                    serde_json::json!({"mode":if opt.bridge{"bridge"}else{"observe"},"device":path,"name":device.name})
                );
                known_devices.lock().unwrap().insert(path.clone());
                devices.insert(
                    path.clone(),
                    RunningDevice {
                        device,
                        output,
                        router,
                        dropped: false,
                        session_worker: if sinks.execute_actions && !suspended {
                            session
                                .clone()
                                .map(|r| SessionWorker::spawn(r, global.clone()))
                                .transpose()?
                        } else {
                            None
                        },
                        session,
                        session_error,
                        capture: None,
                        remote_input: None,
                        login_generation: target_focus.routing_generation,
                        capture_recovery_at: Instant::now() - Duration::from_secs(10),
                        suspended,
                        neutral_rearming,
                    },
                );
                if !nested {
                    update_active_manifest(
                        &runtime,
                        &devices,
                        &raw_consumers.lock().unwrap(),
                        opt.bridge,
                        global.controller.scan_ms,
                        ManifestFocus {
                            frontend: native_frontend(
                                &focus,
                                &global,
                                cursor_requested.load(Ordering::Relaxed),
                            )
                            .as_ref(),
                            stale_ms: global.controller.context_stale_ms,
                            target: fresh_native_target(&focus, &global),
                            owned: native_owned(&focus, &global),
                            remote: focus.remote,
                            focus: Some(&focus),
                        },
                    );
                }
            }
            if let Ok(snapshot) = shared_focus.try_lock() {
                focus = snapshot.clone();
            }
            if follow
                && focus.sampled.elapsed()
                    > Duration::from_millis(global.controller.context_stale_ms)
            {
                focus.name = "app-capture".into();
                focus.room_capture = false;
                focus.shell_overlay = false;
                focus.app = None;
            }
            ctx = focus.name.clone();
            if sinks.ownership_generation != focus.routing_generation {
                if let Some(worker) = &mut sinks.ownership {
                    worker.invalidate();
                }
                sinks.ownership_generation = focus.routing_generation;
            }
            if !focus.remote
                || focus.suspended.is_some()
                || focus.native_target.as_ref().is_none_or(|t| {
                    sinks
                        .remote_pointer
                        .as_ref()
                        .is_some_and(|p| !same_pointer_scope(&p.target, t))
                })
            {
                sinks.remote_pointer = None;
            }

            // Ownership is per session: a capture/release requested by one pad applies to every pad (INV-7).
            let mut adopt: Vec<(PathBuf, bool)> = vec![];
            let mut manifest_dirty = false;
            let paths: Vec<_> = devices.keys().cloned().collect();
            for path in paths {
                let dev = devices.get_mut(&path).unwrap();
                let mut target_focus =
                    session_focus(&focus, dev.session.is_some(), dev.session_error.as_deref());
                if !focus.remote
                    && dev.session.is_none()
                    && dev.session_error.is_none()
                    && dev.output.is_none()
                {
                    if let Some(reason) = &local_sink_error {
                        target_focus.suspended = Some(reason.clone());
                    }
                }
                let focus = &target_focus;
                if dev.session.is_none() && dev.session_error.is_none() {
                    if let Some(neutral) = begin_login_handoff(
                        &mut dev.router,
                        &mut dev.login_generation,
                        focus.routing_generation,
                        &mut dev.neutral_rearming,
                    ) {
                        // Cancel old queued strokes, then release existing local
                        // outputs before destroying their devices. No new focus
                        // or lazy sink creation participates in this drain.
                        dev.capture = None;
                        dev.remote_input = None;
                        let drained = drain_login_outputs(
                            &dev.device,
                            &neutral,
                            dev.output.as_mut(),
                            sinks.pointer.as_mut(),
                        );
                        dev.output = None;
                        // The pointer is shared: disconnect also clears holds
                        // from other old pads before they individually reset.
                        sinks.pointer = None;
                        sinks.keyboard = None;
                        drained?;
                    }
                    if focus.remote && focus.suspended.is_none() {
                        if let Some(target) = &focus.native_target {
                            let changed = dev
                                .remote_input
                                .as_ref()
                                .is_none_or(|r| !r.target.matches(target));
                            if changed {
                                dev.remote_input = Some(RemoteInput {
                                    target: target.clone(),
                                    keys: SessionWorker::login(
                                        target.clone(),
                                        &dev.device.path,
                                        global.clone(),
                                        login_selection.clone(),
                                    )?,
                                });
                            }
                        }
                    } else {
                        dev.remote_input = None;
                    }
                }
                // INV-8: another session (or the greeter) owns the seat: release the grab so its session gets the pad,
                // neutralize every output, and route nothing until this user's session is active again.
                // A temporarily unavailable authenticated SSH target retains its already assigned grab while
                // output is neutral; it must not expose that user's pad to the physical greeter.
                if focus.suspended.is_some()
                    && !focus.remote
                    && dev.suspended
                    && dev.device.grabbed()
                {
                    dev.device.release();
                    manifest_dirty = true;
                }
                match (&focus.suspended, dev.suspended) {
                    (Some(reason), false) => {
                        // Suppress lazy local clone creation while resetting a pad whose sink is unavailable.
                        dev.suspended = true;
                        let out = dev.router.reset();
                        emit(out, dev, &mut sinks, focus)?;
                        dev.session_worker = None;
                        dev.capture = None;
                        dev.remote_input = None;
                        if !focus.remote {
                            dev.device.release();
                        }
                        dev.suspended = true;
                        manifest_dirty = true;
                        eprintln!(
                            "{}",
                            serde_json::json!({"seat":if focus.remote {"remote-paused"}else{"released"},"device":path,"reason":reason,"grab_retained":dev.device.grabbed()})
                        );
                    }
                    (None, true) => {
                        if opt.bridge {
                            if let Err(e) = if dev.device.grabbed() {
                                Ok(())
                            } else {
                                dev.device.grab()
                            } {
                                if !nested && e.raw_os_error() == Some(libc::EBUSY) {
                                    raw_consumers.lock().unwrap().insert(
                                        path.clone(),
                                        format!("device held by raw consumer (EBUSY): {e}"),
                                    );
                                    manifest_dirty = true;
                                    continue;
                                }
                                return Err(format!("{}: cannot own input: {e}", path.display()));
                            }
                            raw_consumers.lock().unwrap().remove(&path);
                        }
                        let (keys, axes) = dev.device.snapshot().map_err(|e| e.to_string())?;
                        if !nested
                            && physical_controls_neutral(
                                &keys,
                                &axes,
                                global.controller.stick_deadzone,
                                legacy.trigger_threshold,
                            )
                            .is_err()
                        {
                            dev.neutral_rearming = true;
                        } else {
                            let out = dev.router.resync(keys, axes);
                            emit(out, dev, &mut sinks, focus)?;
                        }
                        dev.suspended = false;
                        manifest_dirty = true;
                        dev.session_worker = if sinks.execute_actions && !dev.suspended {
                            dev.session
                                .clone()
                                .map(|r| SessionWorker::spawn(r, global.clone()))
                                .transpose()?
                        } else {
                            None
                        };
                        eprintln!("{}", serde_json::json!({"seat":"owned","device":path}));
                    }
                    _ => {}
                }
                if dev.suspended {
                    if dev.device.read_events().is_err() {
                        eprintln!("input disconnected {}", path.display());
                        devices.remove(&path);
                        known_devices.lock().unwrap().remove(&path);
                        if selected.as_ref() == Some(&path) {
                            selected = None;
                        }
                        manifest_dirty = true;
                    }
                    continue;
                }
                if focus.shell_overlay {
                    dev.capture = None;
                }
                let target = focus.address.clone().zip(focus.pid);
                let mut transition = dev.router.set_shell_overlay(focus.shell_overlay);
                transition.extend(dev.router.set_capture_navigation_context(
                    capture_navigation_context(&global, focus.class.as_deref()).as_deref(),
                ));
                transition.extend(
                    dev.router
                        .set_tmux(dev.session.is_some() || focus.tmux_client.is_some()),
                );
                transition.extend(
                    dev.router
                        .set_focus(&focus.name, target, focus.room_capture),
                );
                // P4/P8: an explicit route verifies its own pane; the host bridge takes the snapshot's app, bound to the
                // window it was verified for (a result for a window that lost focus selects nothing).
                let (app, verified_for) = match (&dev.session, &dev.session_worker) {
                    (Some(_), Some(worker)) => {
                        (worker.app(global.controller.context_stale_ms), None)
                    }
                    (Some(_), None) => (None, None),
                    _ => (
                        focus
                            .fresh_app(global.controller.context_stale_ms)
                            .map(str::to_owned),
                        focus.app_target.clone(),
                    ),
                };
                transition.extend(dev.router.set_app(app.as_deref(), verified_for.as_ref()));
                emit(transition, dev, &mut sinks, focus)?;
                let mismatch = dev.capture.as_ref().is_some_and(|b| {
                    focus.address.as_deref() != Some(b.address.as_str())
                        || focus.pid != Some(u64::from(b.pid))
                });
                if !dev.router.app_owned() || (mismatch && dev.router.ownership().is_none()) {
                    dev.capture = None;
                }
                if !dev.router.shell_overlay()
                    && dev.router.captured_navigation()
                    && dev.router.ownership().is_none()
                    && dev.capture.is_none()
                    && dev.capture_recovery_at.elapsed()
                        >= Duration::from_millis(global.controller.scan_ms)
                {
                    if let Some(worker) = &mut sinks.ownership {
                        let _ = worker.submit(
                            "inspect",
                            focus.address.clone(),
                            vec![],
                            focus.native_target.as_ref().map(|t| NativeOwnership {
                                target: t.clone(),
                                outer: focus.native_outer,
                            }),
                            focus.routing_generation,
                        );
                        dev.capture_recovery_at = Instant::now();
                    }
                }
                match dev.device.read_events() {
                    Ok(events) => {
                        if events.iter().any(|e| {
                            e.kind == crate::router::EV_KEY || e.kind == crate::router::EV_ABS
                        }) && dev.session.is_none()
                            && dev.session_error.is_none()
                        {
                            selected = Some(path.clone());
                        }
                        for event in events {
                            if event.kind == EV_SYN && event.code == SYN_DROPPED {
                                dev.dropped = true;
                                manifest_dirty = true;
                            }
                            if dev.dropped && event.kind == EV_SYN && event.code == SYN_REPORT {
                                let (keys, axes) =
                                    dev.device.snapshot().map_err(|e| e.to_string())?;
                                let out = dev.router.resync(keys, axes);
                                dev.dropped = false;
                                manifest_dirty = true;
                                emit(out, dev, &mut sinks, focus)?;
                            } else {
                                if dev.neutral_rearming {
                                    if event.kind == crate::router::EV_ABS {
                                        if let Some(a) = dev.router.axes.get_mut(&event.code) {
                                            a.value = event.value;
                                        }
                                    }
                                    let still_rearming = step_neutral_rearming(
                                        &mut dev.neutral_rearming,
                                        &mut dev.router,
                                        dev.device.snapshot(),
                                        global.controller.stick_deadzone,
                                        legacy.trigger_threshold,
                                    );
                                    if !still_rearming {
                                        eprintln!(
                                            "{}",
                                            serde_json::json!({"rearmed": true, "device": path})
                                        );
                                        manifest_dirty = true;
                                    }
                                    continue;
                                }
                                let out = dev
                                    .router
                                    .event(event, (linux::monotonic_ns() / 1_000_000) as u64);
                                if let Some(owned) = emit(out, dev, &mut sinks, focus)? {
                                    adopt.push((path.clone(), owned));
                                }
                            }
                        }
                    }
                    Err(e) => {
                        eprintln!("input disconnected {}: {e}", path.display());
                        let out = dev.router.reset();
                        emit(out, dev, &mut sinks, focus)?;
                        devices.remove(&path);
                        known_devices.lock().unwrap().remove(&path);
                        if selected.as_ref() == Some(&path) {
                            selected = None;
                        }
                        manifest_dirty = true;
                    }
                }
            }
            if manifest_dirty && !nested {
                update_active_manifest(
                    &runtime,
                    &devices,
                    &raw_consumers.lock().unwrap(),
                    opt.bridge,
                    global.controller.scan_ms,
                    ManifestFocus {
                        frontend: native_frontend(
                            &focus,
                            &global,
                            cursor_requested.load(Ordering::Relaxed),
                        )
                        .as_ref(),
                        stale_ms: global.controller.context_stale_ms,
                        target: fresh_native_target(&focus, &global),
                        owned: native_owned(&focus, &global),
                        remote: focus.remote,
                        focus: Some(&focus),
                    },
                );
                manifest_dirty = false;
            }
            let now = Instant::now();
            let dt = now.duration_since(last_tick).as_secs_f64().min(0.05);
            last_tick = now;
            let reconcile_due = now.duration_since(last_reconcile)
                >= Duration::from_millis(global.controller.context_poll_ms);
            if reconcile_due {
                last_reconcile = now;
            }
            for (path, dev) in devices.iter_mut().filter(|(_, d)| !d.suspended) {
                if dev.neutral_rearming {
                    let still_rearming = step_neutral_rearming(
                        &mut dev.neutral_rearming,
                        &mut dev.router,
                        dev.device.snapshot(),
                        global.controller.stick_deadzone,
                        legacy.trigger_threshold,
                    );
                    if !still_rearming {
                        eprintln!("{}", serde_json::json!({"rearmed": true, "device": path}));
                        manifest_dirty = true;
                    } else {
                        continue;
                    }
                }
                let target_focus =
                    session_focus(&focus, dev.session.is_some(), dev.session_error.as_deref());
                let focus = &target_focus;
                if reconcile_due && !dev.dropped {
                    if let Ok(held) = dev.device.held_keys() {
                        let out = dev
                            .router
                            .reconcile(&held, (linux::monotonic_ns() / 1_000_000) as u64);
                        if let Some(owned) = emit(out, dev, &mut sinks, focus)? {
                            adopt.push((path.clone(), owned));
                        }
                    }
                }
                let out = dev.router.tick(dt);
                if let Some(owned) = emit(out, dev, &mut sinks, focus)? {
                    adopt.push((path.clone(), owned));
                }
                // I4 --nested: a raw captured app reads its assigned node itself.
                // Captured terminal navigation remains projected by this bridge,
                // including MiOS-Mons camera input, so it keeps the sole grab.
                // Until the session compositor has answered once, the bridge keeps the grab: the placeholder context
                // would otherwise release the node for a moment at start, where any host reader could see it.
                if nested && opt.bridge {
                    let want =
                        dev.router.bridge_owns_input() || (follow && focus.sampled == boot_sample);
                    if want && !dev.device.grabbed() {
                        dev.device
                            .grab()
                            .map_err(|e| format!("{}: cannot own input: {e}", path.display()))?;
                        eprintln!("{}", serde_json::json!({"nested":"grab","device":path}));
                    } else if !want && dev.device.grabbed() {
                        dev.device.release();
                        eprintln!(
                            "{}",
                            serde_json::json!({"nested":"passthrough","device":path})
                        );
                    }
                }
            }
            if manifest_dirty && !nested {
                update_active_manifest(
                    &runtime,
                    &devices,
                    &raw_consumers.lock().unwrap(),
                    opt.bridge,
                    global.controller.scan_ms,
                    ManifestFocus {
                        frontend: native_frontend(
                            &focus,
                            &global,
                            cursor_requested.load(Ordering::Relaxed),
                        )
                        .as_ref(),
                        stale_ms: global.controller.context_stale_ms,
                        target: fresh_native_target(&focus, &global),
                        owned: native_owned(&focus, &global),
                        remote: focus.remote,
                        focus: Some(&focus),
                    },
                );
            }
            for (source, owned) in adopt {
                for (path, dev) in devices.iter_mut() {
                    if *path != source && dev.session.is_none() && dev.session_error.is_none() {
                        dev.capture = None;
                        let out = dev.router.adopt_ownership(owned);
                        emit(out, dev, &mut sinks, &focus)?;
                    }
                }
            }
            if let Some(bus) = &mut sinks.remote_pointer {
                let live = |path: &Path| {
                    devices.get(path).is_some_and(|d| {
                        !d.suspended
                            && d.session.is_none()
                            && d.session_error.is_none()
                            && d.router.cursor
                    })
                };
                bus.cursors.retain(|p| live(p));
                let cancel = bus.holds.retain(live);
                if let Some(pointer) = &bus.pointer {
                    let visible = !bus.cursors.is_empty();
                    // Hide is the native neutral boundary: a normal up here could activate a close/menu binding.
                    if cancel || (bus.visible && !visible) {
                        bus.holds.cancel();
                        if pointer.visible(false) {
                            bus.visible = false;
                        }
                    }
                    if bus.visible != visible && pointer.visible(visible) {
                        bus.visible = visible;
                    }
                }
            }
            let cursor_active = !nested
                && focus.suspended.is_none()
                && focus.name != "desktop-3d"
                && !focus.room_capture
                && focus.sampled.elapsed()
                    < Duration::from_millis(global.controller.context_stale_ms)
                && devices.values().any(|d| {
                    !d.suspended
                        && d.session.is_none()
                        && d.session_error.is_none()
                        && d.router.cursor
                        && !d.router.shell_overlay()
                });
            cursor_requested.store(cursor_active, Ordering::Relaxed);
            // Refresh the logical frontend lease at the existing context cadence, including with no pad attached.
            if !nested
                && frontend_manifest_at.elapsed()
                    >= Duration::from_millis(global.controller.context_poll_ms)
            {
                frontend_manifest_at = Instant::now();
                update_active_manifest(
                    &runtime,
                    &devices,
                    &raw_consumers.lock().unwrap(),
                    opt.bridge,
                    global.controller.scan_ms,
                    ManifestFocus {
                        frontend: native_frontend(&focus, &global, cursor_active).as_ref(),
                        stale_ms: global.controller.context_stale_ms,
                        target: fresh_native_target(&focus, &global),
                        owned: native_owned(&focus, &global),
                        remote: focus.remote,
                        focus: Some(&focus),
                    },
                );
            }
            let mut failed = vec![];
            if let Some(worker) = &sinks.ownership {
                while let Ok((seq, mode, result)) = worker.results.try_recv() {
                    match result {
                        Ok(receipt) => {
                            if seq == worker.last
                                && receipt
                                    .native
                                    .as_ref()
                                    .is_none_or(|t| t.login_generation == focus.routing_generation)
                            {
                                for dev in devices.values_mut().filter(|d| {
                                    d.session.is_none() && d.session_error.is_none() && !d.suspended
                                }) {
                                    if mode != "off" {
                                        dev.router.confirm_capture_target(&receipt.address);
                                    }
                                    if mode == "off" {
                                        dev.capture = None;
                                    } else if capture_navigation_context(
                                        &global,
                                        Some(&receipt.class),
                                    )
                                    .is_some()
                                    {
                                        let same = dev.capture.as_ref().is_some_and(|b| {
                                            b.address == receipt.address && b.pid == receipt.pid
                                        });
                                        if mode == "on" || !same {
                                            dev.capture = Some(capture_binding(
                                                &receipt,
                                                &dev.device.path,
                                                &global,
                                            )?);
                                        }
                                    }
                                }
                            }
                            eprintln!(
                                "{}",
                                serde_json::json!({"ownership":mode,"window":receipt.address,"pid":receipt.pid,
                                "pane":receipt.tmux.as_ref().map(|t|&t.pane),"delivery_error":receipt.error,"ok":true})
                            );
                        }
                        Err(error) => {
                            eprintln!(
                                "{}",
                                serde_json::json!({"ownership":mode,"ok":false,"error":error,"reverted":seq==worker.last})
                            );
                            // Only the latest request decides; an older failure must not undo a newer capture.
                            if seq == worker.last && mode != "inspect" {
                                failed.push(mode);
                            }
                        }
                    }
                }
            }
            if !failed.is_empty() {
                for dev in devices
                    .values_mut()
                    .filter(|d| d.session.is_none() && d.session_error.is_none())
                {
                    dev.capture = None;
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
    // Stop queued/effect admission while the sampler still publishes authority.
    drop(sinks.actions.take());
    cursor_requested.store(false, Ordering::Relaxed);
    if !nested {
        let _ = ipc(&format!("/eval {}", controller_cursor_lua(&global, false)));
    }
    sinks.ownership = None;
    for dev in devices.values_mut() {
        dev.session_worker = None;
        dev.capture = None;
        dev.remote_input = None;
        dev.suspended = true;
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
    if let Some(worker) = context_worker {
        let _ = worker.join();
    }
    let _ = discovery.join();
    publish_app_lease(
        app_authority.as_ref(),
        "seat",
        None,
        Instant::now(),
        global.controller.context_stale_ms,
    );
    if !nested {
        let _ = std::fs::remove_file(runtime.join("mios-inputd-active.json"));
    }
    if let Some(p) = &child_control_sock {
        let _ = std::fs::remove_file(p);
    }
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
    if let Some(mode) = action.strip_prefix("input:window ") {
        return Ok(vec![
            "/usr/bin/hyprctl".into(),
            "eval".into(),
            window_lua(mode, global, None)?,
        ]);
    }
    if global.planned_actions.iter().any(|a| a == action) {
        return Err(format!("action is planned, not implemented: {action}"));
    }
    global
        .resolved_actions
        .get(action)
        .cloned()
        .map(Ok)
        .unwrap_or_else(|| command_for(action))
}
/// Check the same action grammar for configuration loading, reload and bridge startup.
/// This resolves argv only; it never executes an action or queries a live terminal.
pub(crate) fn action_supported(global: &GlobalConfig, action: &str) -> bool {
    let probe = TmuxClient {
        pid: 0,
        start: 0,
        instance: 0,
        socket_dev: 0,
        socket_ino: 0,
        tty: "/dev/pts/0".into(),
        socket: PathBuf::from("/tmp/probe"),
    };
    match action
        .split_once(':')
        .map_or("", |(namespace, _)| namespace)
    {
        _ if action == "none" => true,
        "key" => crate::nav::parse_key_chord(&action[4..]).is_ok(),
        "pointer" => true,
        "tmux" => tmux_argv(global, &probe, &action[5..]).is_ok(),
        _ => configured_command(action, global).is_ok(),
    }
}
pub fn unsupported_actions(global: &GlobalConfig) -> Vec<String> {
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
        .filter(|action| !action_supported(global, action))
        .cloned()
        .collect()
}

/// Query once, then validate identity again in the compositor transaction.
/// Sending the native escape is observable; the remote client's capture state is not.
pub fn vm_action(action: &str, global: &GlobalConfig) -> Result<(), String> {
    let window: serde_json::Value =
        serde_json::from_str(&ipc("j/activewindow")?).map_err(|e| e.to_string())?;
    let address = window
        .get("address")
        .and_then(|v| v.as_str())
        .ok_or("no focused VM address")?;
    let pid = window
        .get("pid")
        .and_then(|v| v.as_u64())
        .ok_or("no focused VM process")?;
    let class = window
        .get("class")
        .and_then(|v| v.as_str())
        .ok_or("no focused VM class")?;
    if let Ok(expected) = std::env::var("MIOS_INPUT_WINDOW") {
        let expected_pid = std::env::var("MIOS_INPUT_WINDOW_PID")
            .ok()
            .and_then(|v| v.parse::<u64>().ok());
        if expected != address || expected_pid != Some(pid) {
            return Err("controller focus changed; no VM input sent".into());
        }
    }
    let lua = global.vm.lua(action, address, pid, class)?;
    let reply = ipc(&format!("/eval {lua}"))?;
    if !reply.contains("MIOS_VM_SENT") {
        return Err(reply);
    }
    println!(
        "{}",
        serde_json::json!({"action":action,"address":address,"native_command_sent":true,"client_capture_state":"unknown"})
    );
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn shell_input_requires_a_fresh_valid_local_surface_lease() {
        let (mut cfg, _) = crate::config::tests::fixture();
        let dir = tempfile::tempdir().unwrap();
        cfg.shell_input_state_file = "mios-shell-input.json".into();
        let path = dir.path().join(&cfg.shell_input_state_file);
        assert!(!shell_overlay_open(dir.path(), &cfg));
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_millis() as u64;
        let valid = serde_json::json!({"version":1,"open":true,"surface":"drawer","output":"test-output","timestamp_ms":now});
        std::fs::write(&path, valid.to_string()).unwrap();
        assert!(shell_overlay_open(dir.path(), &cfg));
        for (field, value) in [
            ("open", serde_json::json!(false)),
            ("version", serde_json::json!(2)),
            ("surface", serde_json::json!("unknown")),
            ("output", serde_json::json!("")),
            ("timestamp_ms", serde_json::json!(now - 10000)),
            ("timestamp_ms", serde_json::json!(now + 10000)),
        ] {
            let mut invalid = valid.clone();
            invalid[field] = value;
            std::fs::write(&path, invalid.to_string()).unwrap();
            assert!(!shell_overlay_open(dir.path(), &cfg), "{field}");
        }
        std::fs::write(&path, "{broken").unwrap();
        assert!(!shell_overlay_open(dir.path(), &cfg));
        std::fs::remove_file(&path).unwrap();
        let elsewhere = dir.path().join("elsewhere");
        std::fs::write(&elsewhere, valid.to_string()).unwrap();
        std::os::unix::fs::symlink(&elsewhere, &path).unwrap();
        assert!(
            !shell_overlay_open(dir.path(), &cfg),
            "a symlink does not own shell input"
        );
    }
    #[test]
    fn controller_cursor_lease_uses_the_native_lua_contract() {
        let (mut cfg, _) = crate::config::tests::fixture();
        cfg.controller.cursor_shape = "crosshair".into();
        cfg.controller.virtual_pointer = "mios-vctl-pointer".into();
        let script=format!("local calls={{}};hl={{plugin={{mios_pacing={{controller_cursor=function(...) table.insert(calls,table.pack(...)) end}}}}}};{}{}assert(#calls==2 and calls[1].n==4 and calls[1][1]==true and calls[1][2]=='crosshair' and calls[1][3]=='mios-vctl-pointer' and calls[1][4]=={} and calls[2].n==1 and calls[2][1]==false)",
            controller_cursor_lua(&cfg,true),controller_cursor_lua(&cfg,false),cfg.controller.context_stale_ms);
        let mut child = Command::new("/usr/bin/lua")
            .arg("-")
            .stdin(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        child
            .stdin
            .take()
            .unwrap()
            .write_all(script.as_bytes())
            .unwrap();
        let output = child.wait_with_output().unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
    #[test]
    fn app_input_scope_expires_and_never_survives_overlay_focus_or_login_changes() {
        let mut focus = ContextSnapshot::new("terminal");
        focus.address = Some("0xapp".into());
        focus.pid = Some(42);
        focus.app = Some("mon".into());
        focus.app_target = Some(("0xapp".into(), 42));
        focus.app_at = Some(Instant::now());
        focus.routing_generation = 7;
        focus.app_process = Some(mios_resolver::app_input::Process { pid: 40, start: 41 });
        let original = app_input_focus(&focus, 1000).unwrap();
        focus.routing_generation = 8;
        assert_ne!(app_input_focus(&focus, 1000).unwrap(), original);
        focus.shell_overlay = true;
        assert!(app_input_focus(&focus, 1000).is_none());
        focus.shell_overlay = false;
        focus.address = Some("0xother".into());
        assert!(app_input_focus(&focus, 1000).is_none());
        focus.address = Some("0xapp".into());
        focus.suspended = Some("other user".into());
        assert!(app_input_focus(&focus, 1000).is_none());
        focus.suspended = None;
        focus.app_at = Some(Instant::now() - Duration::from_secs(2));
        assert!(app_input_focus(&focus, 1000).is_none());
    }
    #[test]
    fn observed_focus_aba_and_replacement_processes_never_revalidate_old_jobs() {
        let mut a = ContextSnapshot::new("terminal");
        a.address = Some("0xa".into());
        a.pid = Some(10);
        a.app = Some("mon".into());
        a.app_target = Some(("0xa".into(), 10));
        a.app_at = Some(Instant::now());
        a.app_process = Some(mios_resolver::app_input::Process { pid: 40, start: 41 });
        a.focus_epoch = 1;
        let accepted = app_input_focus(&a, 1000).unwrap();
        let mut b = a.clone();
        b.address = Some("0xb".into());
        advance_focus(&a, &mut b);
        assert_eq!(b.focus_epoch, 2);
        let mut returned = a.clone();
        advance_focus(&b, &mut returned);
        assert_eq!(returned.focus_epoch, 3);
        assert_ne!(app_input_focus(&returned, 1000).unwrap(), accepted);
        let shared = std::sync::Arc::new(std::sync::Mutex::new(returned.clone()));
        let selection: SharedLogin = Default::default();
        assert!(
            with_login_focus(&selection, &shared, 0, accepted.epoch, 1000, |_| Ok(())).is_err(),
            "old app and zoom jobs stay cancelled after returning"
        );
        let mut replaced = returned.clone();
        replaced.app_process = Some(mios_resolver::app_input::Process { pid: 50, start: 51 });
        advance_focus(&returned, &mut replaced);
        assert_eq!(replaced.focus_epoch, 4);
        assert_ne!(
            app_input_focus(&replaced, 1000).unwrap().process,
            accepted.process
        );
        let mut refreshed = replaced.clone();
        refreshed.sampled = Instant::now();
        advance_focus(&replaced, &mut refreshed);
        assert_eq!(
            refreshed.focus_epoch, 4,
            "ordinary refresh is not a new visit"
        );
    }
    #[test]
    fn app_checks_wait_for_login_selection_without_holding_focus_or_deadlocking_handoff() {
        let selection: SharedLogin = Default::default();
        let focus = std::sync::Arc::new(std::sync::Mutex::new(ContextSnapshot::new("terminal")));
        let selected = selection.lock().unwrap();
        let (started, ready) = std::sync::mpsc::channel();
        let (done, finished) = std::sync::mpsc::channel();
        let worker_selection = selection.clone();
        let worker_focus = focus.clone();
        let worker = std::thread::spawn(move || {
            started.send(()).unwrap();
            let result = with_login_focus(&worker_selection, &worker_focus, 0, 0, 1000, |_| Ok(()));
            done.send(result).unwrap();
        });
        ready.recv_timeout(Duration::from_millis(200)).unwrap();
        let until = Instant::now() + Duration::from_millis(50);
        let mut focus_blocked = false;
        while Instant::now() < until {
            if focus.try_lock().is_err() {
                focus_blocked = true;
                break;
            }
            std::thread::yield_now();
        }
        // This is the handoff's selection->focus order while the app request
        // waits. Release selection before asserting so a bad old order fails
        // the test without leaving a permanently blocked worker.
        drop(selected);
        finished
            .recv_timeout(Duration::from_millis(500))
            .unwrap()
            .unwrap();
        worker.join().unwrap();
        assert!(
            !focus_blocked,
            "an app request must not retain focus while waiting for selection"
        );
    }
    #[test]
    fn exact_local_route_state_rejects_aba_replacement_and_remote_app_transport() {
        let route = crate::sessions::SessionRoute {
            name: "own".into(),
            controllers: vec![],
            socket: "/private/own.sock".into(),
            session: "own".into(),
            pane: "%1".into(),
            ssh_host: None,
        };
        let identity = LocalAppIdentity {
            route: route.clone(),
            socket: (1, 2),
            server: 3,
            server_start: 4,
            window: "@1".into(),
            pane_pid: 5,
            pane_start: 6,
            app: "mon".into(),
            process: mios_resolver::app_input::Process { pid: 40, start: 41 },
        };
        let mut state = LocalAppState::default();
        let accepted = update_local_app(
            &mut state,
            Some(identity.clone()),
            "route:1",
            Instant::now(),
        )
        .unwrap();
        assert!(local_control_current(&state, &accepted, 1000));
        update_local_app(&mut state, None, "route:1", Instant::now());
        update_local_app(
            &mut state,
            Some(identity.clone()),
            "route:1",
            Instant::now(),
        );
        assert!(
            !local_control_current(&state, &accepted, 1000),
            "returning to the same pane cannot revive a queued job"
        );
        let current = state.current.clone().unwrap();
        let mut replaced = identity;
        replaced.process = mios_resolver::app_input::Process { pid: 50, start: 51 };
        update_local_app(&mut state, Some(replaced), "route:1", Instant::now());
        assert!(!local_control_current(&state, &current, 1000));
        let mut remote = route;
        remote.ssh_host = Some("future-transport".into());
        let (cfg, _) = crate::config::tests::fixture();
        assert!(local_app_identity(
            &remote,
            &cfg,
            Duration::from_millis(1),
            &AtomicBool::new(false)
        )
        .unwrap_err()
        .contains("future authenticated transport"));
    }
    #[test]
    fn compositor_zoom_refuses_other_window_or_reused_pid_without_dispatching() {
        let lua = window_zoom_lua("0xapp", 42);
        let script=format!("local calls=0;local w={{address='0xapp',pid=42}};hl={{get_active_window=function() return w end,dsp={{window={{fullscreen=function(x) assert(x.window==w and x.mode=='maximized' and x.action=='toggle');return x end}}}},dispatch=function(x) calls=calls+1 end}};local function zoom() {lua} end;zoom();assert(calls==1);w={{address='0xother',pid=42}};assert(not pcall(zoom) and calls==1);w={{address='0xapp',pid=43}};assert(not pcall(zoom) and calls==1)");
        let mut child = Command::new("/usr/bin/lua")
            .arg("-")
            .stdin(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()
            .unwrap();
        child
            .stdin
            .take()
            .unwrap()
            .write_all(script.as_bytes())
            .unwrap();
        let output = child.wait_with_output().unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
    }
    #[test]
    fn native_context_captures_arbitrary_apps_and_keeps_an_unfocused_room() {
        let (cfg, _) = crate::config::tests::fixture();
        let v = serde_json::json!({"class":"org.any.Flatpak","address":"0xabc","pid":42,"room":false,"captured":true,"handler":"hypr3d"});
        let snap = snapshot(&cfg, &v);
        assert_eq!(snap.name, "app-capture");
        assert!(snap.room_capture);
        let empty = serde_json::json!({"room":true,"captured":false});
        assert_eq!(snapshot(&cfg, &empty).name, "desktop-3d");
    }
    #[test]
    fn current_room_passthrough_uses_native_fullscreen_state() {
        let (cfg, _) = crate::config::tests::fixture();
        let mut v = serde_json::json!({"class":"org.any.Flatpak","address":"0xabc","pid":42,"room":true,"fullscreen":2,"captured":true,"handler":"default"});
        assert!(
            snapshot(&cfg, &v).room_capture,
            "the installed room uses the compositor default fullscreen handler"
        );
        v["room"] = serde_json::json!(false);
        assert!(!snapshot(&cfg,&v).room_capture,"outside the room a fullscreen capture is a 2D capture; the held chord releases it to the desktop");
        v["room"] = serde_json::json!(true);
        v["fullscreen"] = serde_json::json!(0);
        assert!(
            snapshot(&cfg, &v).room_capture,
            "windowed room capture must support the held release chord"
        );
    }
    #[test]
    fn unrelated_ssot_changes_do_not_reset_controller_policy() {
        let mut doc: toml::Value = include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap();
        let (cfg, legacy) = GlobalConfig::from_value(&doc).unwrap();
        let before = policy_fingerprint(&cfg, &legacy).unwrap();
        doc["theme"]["font"]["size"] = toml::Value::Integer(18);
        let (cfg, legacy) = GlobalConfig::from_value(&doc).unwrap();
        assert_eq!(policy_fingerprint(&cfg, &legacy).unwrap(), before);
    }
    #[test]
    fn release_tag_is_reported_and_class_never_captures() {
        let (cfg, _) = crate::config::tests::fixture();
        let v = serde_json::json!({"class":"steam_app_570","address":"0xabc","pid":42,"room":false,"captured":false,"released":true});
        let snap = snapshot(&cfg, &v);
        assert_eq!(snap.name, "game");
        assert!(snap.released);
        assert!(
            !crate::router::Router::owns_controller(&snap.name),
            "an uncaptured game window is an ordinary window"
        );
        let lua = context_lua(&cfg);
        assert!(
            lua.contains("\"mios-input-released\"")
                && lua.contains("if released then captured=false end")
        );
        assert!(
            lua.contains("policy.captured"),
            "the generated MIOS_INPUT_POLICY.captured predicate is preferred when present"
        );
    }
    #[test]
    fn seat_check_reads_logind_state_files_without_spawning() {
        let t = std::env::temp_dir().join(format!("mios-inputd-seat-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&t);
        std::fs::create_dir_all(t.join("users")).unwrap();
        std::fs::create_dir_all(t.join("sessions")).unwrap();
        assert!(
            seat_from_logind_state(&t, 1000)
                .unwrap()
                .contains("logind state unavailable"),
            "no user record: idle"
        );
        std::fs::write(t.join("users/1000"), "STATE=active\nSESSIONS=4\n").unwrap();
        std::fs::write(
            t.join("sessions/4"),
            "UID=1000\nTYPE=tty\nREMOTE=1\nTTY=pts/3\n",
        )
        .unwrap();
        assert_eq!(
            seat_from_logind_state(&t, 1000).as_deref(),
            Some("no seated session for this user"),
            "SSH-only login: idle"
        );
        std::fs::write(t.join("users/1000"), "DISPLAY=../x\n").unwrap();
        assert_eq!(
            seat_from_logind_state(&t, 1000).as_deref(),
            Some("no seated session for this user"),
            "a path-like id is rejected"
        );
        std::fs::write(t.join("users/1000"), "STATE=active\nDISPLAY=3\n").unwrap();
        std::fs::write(t.join("sessions/3"), "TYPE=wayland\nSEAT=\n").unwrap();
        assert_eq!(
            seat_from_logind_state(&t, 1000).as_deref(),
            Some("no seated session for this user")
        );
        std::fs::write(t.join("sessions/3"), "TYPE=wayland\nSEAT=seat0\n").unwrap();
        assert_eq!(
            seat_from_logind_state(&t, 1000),
            None,
            "a seated graphical session reaches the bridge"
        );
        std::fs::write(t.join("users/1000"), "STATE=active\nSESSIONS=5\n").unwrap();
        std::fs::write(
            t.join("sessions/5"),
            "UID=1000\nTYPE=tty\nSEAT=seat0\nVTNR=3\nTTY=tty3\n",
        )
        .unwrap();
        assert_eq!(
            seat_from_logind_state(&t, 1000),
            None,
            "I3: a VT login reaches the bridge too"
        );
        std::fs::remove_dir_all(&t).unwrap();
    }
    #[test]
    fn window_transaction_tags_release_and_unsets_only_bridge_fullscreen() {
        let (cfg, _) = crate::config::tests::fixture();
        let off = window_lua("off", &cfg, None).unwrap();
        assert!(off.contains("tag(w,CAP,false);tag(w,REL,true)"));
        assert!(
            off.contains("if has(w,FS) then tag(w,FS,false)") && off.contains("action='unset'")
        );
        let full = window_lua("fullscreen", &cfg, Some("0xabc")).unwrap();
        assert!(
            full.contains("tag(w,REL,false);tag(w,CAP,true)") && full.contains("tag(w,FS,true)")
        );
        assert!(full.contains("local EXPECT=\"0xabc\""));
        assert!(
            off.contains("pcall(policy.refresh)"),
            "the keyboard policy re-reads the tags in the same transaction"
        );
        assert!(window_lua("toggle", &cfg, None).is_err());
        assert_eq!(
            configured_command("input:window off", &cfg).unwrap()[2],
            off
        );
    }
    fn fake_proc(t: &Path, pid: u32, ppid: u32, comm: &str, argv: &[&str], env: &str, tty: &str) {
        let d = t.join(pid.to_string());
        std::fs::create_dir_all(d.join("fd")).unwrap();
        std::fs::write(
            d.join("stat"),
            format!("{pid} ({comm}) S {ppid} 1 1 0{} {pid}", " 0".repeat(14)),
        )
        .unwrap();
        std::fs::write(d.join("comm"), format!("{comm}\n")).unwrap();
        std::fs::write(
            d.join("cmdline"),
            argv.iter().map(|a| format!("{a}\0")).collect::<String>(),
        )
        .unwrap();
        std::fs::write(d.join("environ"), env).unwrap();
        if !tty.is_empty() {
            std::os::unix::fs::symlink(tty, d.join("fd/0")).unwrap();
        }
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
        fake_proc(
            &proc_root,
            101,
            100,
            "mios-terminal",
            &["/bin/bash", "/usr/libexec/mios/mios-terminal"],
            "",
            "/dev/pts/7",
        );
        fake_proc(
            &proc_root,
            102,
            101,
            "tmux: client",
            &["tmux", "-L", "mios-human", "attach", "-t", "=mios"],
            &tmpdir,
            "/dev/pts/7",
        );
        fake_proc(&proc_root, 200, 1, "foot", &["foot"], "", "");
        fake_proc(
            &proc_root,
            201,
            200,
            "tmux: client",
            &["tmux", "-Lscratch", "new"],
            &tmpdir,
            "/dev/pts/8",
        );
        fake_proc(&proc_root, 300, 1, "kitty", &["kitty"], "", "");
        fake_proc(&proc_root, 301, 300, "bash", &["bash"], "", "/dev/pts/9");
        let socks = vec!["mios-human".to_owned(), "mios-nested".to_owned()];
        let c = tmux_client_under(&proc_root, 100, &socks, 1000)
            .expect("the mios-human client under the terminal");
        assert_eq!(
            (c.pid, c.tty.as_str(), c.socket.clone()),
            (102, "/dev/pts/7", sockdir.join("mios-human"))
        );
        assert!(
            tmux_client_under(&proc_root, 200, &socks, 1000).is_none(),
            "a server outside tmux_sockets selects the plain profile"
        );
        assert!(
            tmux_client_under(&proc_root, 300, &socks, 1000).is_none(),
            "a bare shell has no client"
        );
        assert!(
            tmux_client_under(&proc_root, 102, &socks, 1000).is_none(),
            "the focused pid itself is the terminal, not a child"
        );
        std::fs::remove_dir_all(&t).unwrap();
    }
    #[test]
    fn tmux_verbs_target_the_discovered_client_and_validate_keys() {
        let (cfg, _) = crate::config::tests::fixture_nav();
        let c = TmuxClient {
            pid: 1,
            start: 0,
            instance: 0,
            socket_dev: 0,
            socket_ino: 0,
            tty: "/dev/pts/7".into(),
            socket: "/run/t/mios-human".into(),
        };
        assert_eq!(
            tmux_argv(&cfg, &c, "key p").unwrap(),
            [
                "/usr/bin/tmux",
                "-S",
                "/run/t/mios-human",
                "send-keys",
                "-c",
                "/dev/pts/7",
                "-K",
                "C-b",
                "p"
            ]
        );
        assert_eq!(
            tmux_argv(&cfg, &c, "focus left").unwrap()[3..],
            [
                "display-message",
                "-p",
                "-c",
                "/dev/pts/7",
                "-t",
                "/dev/pts/7",
                "#{pane_at_left}"
            ]
        );
        assert_eq!(
            tmux_argv(&cfg, &c, "session-next").unwrap()[3..],
            ["switch-client", "-c", "/dev/pts/7", "-n"]
        );
        assert!(
            tmux_argv(&cfg, &c, "key r")
                .unwrap_err()
                .contains("prefix-table"),
            "r is not bound in the SSOT prefix table"
        );
        for k in ["e", "*", "`", "F", "Space", "Enter"] {
            assert!(
                tmux_argv(&cfg, &c, &format!("key {k}")).is_ok(),
                "{k}: a U1 launcher/float/menu/accept prefix key"
            );
        }
        assert_eq!(
            tmux_argv(&cfg, &c, "menu").unwrap()[3..],
            ["send-keys", "-c", "/dev/pts/7", "-K", "C-b", "Space"],
            "U1's menu_key"
        );
        let mut no_menu = cfg.clone();
        no_menu.tmux_menu_key = None;
        assert!(
            tmux_argv(&no_menu, &c, "menu").is_err(),
            "without the U1 menu key there is no menu projection"
        );
        let popup = tmux_argv(&cfg, &c, "keymap").unwrap();
        assert_eq!(
            popup[3..10],
            ["display-popup", "-c", "/dev/pts/7", "-E", "-w", "90%", "-h"],
            "I3: the popup on exactly this client"
        );
        assert_eq!(
            popup[popup.len() - 3..],
            [
                "/usr/bin/less".to_owned(),
                "-R".to_owned(),
                keymap_path().to_string_lossy().into_owned()
            ],
            "argv form: tmux runs no shell"
        );
        assert!(
            unsupported_actions(&cfg).is_empty(),
            "every vendor nav action resolves: {:?}",
            unsupported_actions(&cfg)
        );
    }
    fn logind(t: &Path, files: &[(&str, &str)]) {
        let _ = std::fs::remove_dir_all(t);
        for d in ["users", "sessions", "seats"] {
            std::fs::create_dir_all(t.join(d)).unwrap();
        }
        for (p, body) in files {
            std::fs::write(t.join(p), body).unwrap();
        }
    }
    #[test]
    fn seat_view_selects_console_graphical_or_foreign() {
        let t = std::env::temp_dir().join(format!("mios-inputd-seatview-{}", std::process::id()));
        let user = ("users/1000", "DISPLAY=3\nSESSIONS=3 7 9\n");
        let gfx = (
            "sessions/3",
            "UID=1000\nTYPE=wayland\nCLASS=user\nSEAT=seat0\nTTY=tty1\nVTNR=1\n",
        );
        let vt = (
            "sessions/7",
            "UID=1000\nTYPE=tty\nCLASS=user\nSEAT=seat0\nTTY=tty3\nVTNR=3\n",
        );
        let ssh = (
            "sessions/9",
            "UID=1000\nTYPE=tty\nCLASS=user\nREMOTE=1\nTTY=pts/4\n",
        );
        logind(&t, &[user, gfx, vt, ssh, ("seats/seat0", "ACTIVE=3\n")]);
        assert_eq!(
            seat_view(&t, 1000),
            SeatView {
                seat: Seat::Graphical,
                graphical_vt: Some(1)
            }
        );
        std::fs::write(t.join("seats/seat0"), "ACTIVE=7\n").unwrap();
        assert_eq!(
            seat_view(&t, 1000),
            SeatView {
                seat: Seat::Console {
                    vt: 3,
                    tty: "/dev/tty3".into()
                },
                graphical_vt: Some(1)
            }
        );
        // INV-8: the greeter or another user owns the seat.
        std::fs::write(
            t.join("sessions/8"),
            "UID=980\nTYPE=wayland\nCLASS=greeter\nSEAT=seat0\nVTNR=1\n",
        )
        .unwrap();
        std::fs::write(t.join("seats/seat0"), "ACTIVE=8\n").unwrap();
        assert!(
            matches!(seat_view(&t, 1000).seat, Seat::Foreign(r) if r.contains("another session"))
        );
        std::fs::write(
            t.join("sessions/8"),
            "UID=1000\nTYPE=wayland\nCLASS=lock-screen\nSEAT=seat0\n",
        )
        .unwrap();
        assert!(
            matches!(seat_view(&t, 1000).seat, Seat::Foreign(_)),
            "a lock screen is not this user's input target"
        );
        std::fs::write(t.join("seats/seat0"), "ACTIVE=\n").unwrap();
        assert!(
            matches!(seat_view(&t, 1000).seat, Seat::Foreign(r) if r.contains("no active session"))
        );
        // A VT-only login has no graphical VT to switch to (C-17 negative).
        logind(
            &t,
            &[
                ("users/1000", "SESSIONS=7\n"),
                vt,
                ("seats/seat0", "ACTIVE=7\n"),
            ],
        );
        assert_eq!(seat_view(&t, 1000).graphical_vt, None);
        assert_eq!(graphical_vt_chord(None), None);
        assert_eq!(graphical_vt_chord(Some(2)).as_deref(), Some("CTRL+ALT+F2"));
        assert_eq!(graphical_vt_chord(Some(13)), None, "no F-key reaches VT 13");
        // SSH only: nothing seated.
        logind(&t, &[("users/1000", "SESSIONS=9\n"), ssh]);
        assert!(
            matches!(seat_view(&t, 1000).seat, Seat::Foreign(r) if r.contains("no seated session"))
        );
        // Actual Blade physical VT tmux-os state (session 345): TYPE=wayland with explicit DESKTOP=tmux-console
        // must be classified as Seat::Console and excluded from graphical_vt return discovery.
        let blade_console = (
            "sessions/345",
            "UID=1000\nACTIVE=1\nIS_DISPLAY=1\nSTATE=active\nREMOTE=0\nUSER=mios\nTYPE=wayland\nORIGINAL_TYPE=wayland\nCLASS=user\nSCOPE=session-345.scope\nSEAT=seat0\nTTY=tty1\nTTY_VALIDITY=from-pam\nSERVICE=greetd\nDESKTOP=tmux-console\nVTNR=1\n",
        );
        logind(
            &t,
            &[
                ("users/1000", "DISPLAY=345\nSESSIONS=345\n"),
                blade_console,
                ("seats/seat0", "ACTIVE=345\n"),
            ],
        );
        assert_eq!(
            seat_view(&t, 1000),
            SeatView {
                seat: Seat::Console {
                    vt: 1,
                    tty: "/dev/tty1".into()
                },
                graphical_vt: None
            }
        );
        // When both an explicit tmux-console session (VT 1) and a graphical session (VT 2) exist,
        // graphical_vt discovers the real graphical VT (2), never the explicit console VT (1).
        let hypr_vt2 = (
            "sessions/3",
            "UID=1000\nTYPE=wayland\nDESKTOP=Hyprland\nCLASS=user\nSEAT=seat0\nTTY=tty2\nVTNR=2\n",
        );
        logind(
            &t,
            &[
                ("users/1000", "DISPLAY=345\nSESSIONS=345 3\n"),
                blade_console,
                hypr_vt2,
                ("seats/seat0", "ACTIVE=345\n"),
            ],
        );
        assert_eq!(
            seat_view(&t, 1000),
            SeatView {
                seat: Seat::Console {
                    vt: 1,
                    tty: "/dev/tty1".into()
                },
                graphical_vt: Some(2)
            }
        );
        // Unrelated graphical session remains Seat::Graphical even with no compositor socket on disk.
        std::fs::write(t.join("seats/seat0"), "ACTIVE=3\n").unwrap();
        assert_eq!(
            seat_view(&t, 1000),
            SeatView {
                seat: Seat::Graphical,
                graphical_vt: Some(2)
            }
        );
        // Foreign UID and non-user class guards still reject explicit DESKTOP=tmux-console sessions.
        std::fs::write(
            t.join("sessions/345"),
            "UID=980\nTYPE=wayland\nDESKTOP=tmux-console\nCLASS=user\nSEAT=seat0\nTTY=tty1\nVTNR=1\n",
        )
        .unwrap();
        std::fs::write(t.join("seats/seat0"), "ACTIVE=345\n").unwrap();
        assert!(
            matches!(seat_view(&t, 1000).seat, Seat::Foreign(r) if r.contains("another session"))
        );
        std::fs::write(
            t.join("sessions/345"),
            "UID=1000\nTYPE=wayland\nDESKTOP=tmux-console\nCLASS=greeter\nSEAT=seat0\nTTY=tty1\nVTNR=1\n",
        )
        .unwrap();
        assert!(
            matches!(seat_view(&t, 1000).seat, Seat::Foreign(r) if r.contains("another session"))
        );
        std::fs::write(
            t.join("sessions/345"),
            "UID=1000\nTYPE=wayland\nDESKTOP=tmux-console\nCLASS=user\nSEAT=seat0\nTTY=pts/0\n",
        )
        .unwrap();
        assert!(matches!(seat_view(&t, 1000).seat, Seat::Foreign(r) if r.contains("no VT")));
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
            std::fs::write(
                proc_root.join(pid.to_string()).join("stat"),
                format!("{pid} (Hyprland) S {ppid} {} {start}", ["0"; 17].join(" ")),
            )
            .unwrap();
        };
        let _old = inst("old", 100);
        stat(100, 50, 1000);
        assert_eq!(
            rediscover(&rt, &proc_root, Some("old")).as_deref(),
            Some("old"),
            "a live followed instance is kept"
        );
        let _new = inst("new", 200);
        stat(200, 50, 2000);
        assert_eq!(
            rediscover(&rt, &proc_root, Some("old")).as_deref(),
            Some("old")
        );
        assert_eq!(
            rediscover(&rt, &proc_root, Some("gone")).as_deref(),
            Some("new"),
            "the newest live instance (start time), not the name order"
        );
        std::fs::remove_dir_all(proc_root.join("100")).unwrap();
        assert_eq!(
            rediscover(&rt, &proc_root, Some("old")).as_deref(),
            Some("new"),
            "the compositor restarted: follow the new socket"
        );
        let _nested = inst("nested", 300);
        stat(300, 250, 3000);
        stat(250, 200, 2500);
        assert_eq!(
            live_instances(&rt, &proc_root)
                .iter()
                .map(|(s, _)| s.as_str())
                .collect::<Vec<_>>(),
            ["new"],
            "a session nested in a listed instance is never selected"
        );
        std::fs::remove_dir_all(proc_root.join("200")).unwrap();
        std::fs::remove_dir_all(proc_root.join("300")).unwrap();
        assert_eq!(
            rediscover(&rt, &proc_root, Some("new")),
            None,
            "no live compositor at all"
        );
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
        fake_proc(
            &p,
            11,
            10,
            "mios-inputd",
            &[
                "/usr/libexec/mios/mios-inputd",
                "console-bridge",
                "--",
                "tmux",
            ],
            "",
            "/dev/tty3",
        );
        fake_proc(
            &p,
            12,
            11,
            "tmux: client",
            &["tmux", "-L", "mios-human", "attach"],
            &tmpdir,
            "/dev/pts/9",
        );
        fake_proc(&p, 20, 1, "bash", &["-bash"], "", "/dev/tty4");
        let socks = vec!["mios-human".to_owned()];
        let c = tmux_client_on_tty(&p, Path::new("/dev/tty3"), &socks, 1000)
            .expect("the client below the bridge on tty3");
        assert_eq!((c.pid, c.tty.as_str()), (12, "/dev/pts/9"));
        assert!(
            tmux_client_on_tty(&p, Path::new("/dev/tty4"), &socks, 1000).is_none(),
            "a bare VT shell: console_plain"
        );
        fake_proc(
            &p,
            21,
            20,
            "tmux: client",
            &["tmux", "-L", "mios-human", "attach"],
            &tmpdir,
            "/dev/tty4",
        );
        assert_eq!(
            tmux_client_on_tty(&p, Path::new("/dev/tty4"), &socks, 1000)
                .map(|c| c.tty)
                .as_deref(),
            Some("/dev/tty4"),
            "a client attached directly"
        );
        std::fs::remove_dir_all(&t).unwrap();
    }
    #[test]
    fn vt_aliases_require_the_requested_controlling_tty() {
        let t = std::env::temp_dir().join(format!("mios-inputd-vt-alias-{}", std::process::id()));
        assert!(!t.exists(), "private fixture path already exists");
        let sockdir = t.join("sock/tmux-1000");
        std::fs::create_dir_all(&sockdir).unwrap();
        let _human = std::os::unix::net::UnixListener::bind(sockdir.join("mios-human")).unwrap();
        let env = format!("TMUX_TMPDIR={}\0", t.join("sock").display());
        let socks = vec!["mios-human".to_owned()];
        for (index, alias) in ["/dev/console", "/dev/tty"].into_iter().enumerate() {
            let p = t.join(format!("proc-{index}"));
            fake_proc(&p, 10, 1, "bash", &["bash"], "", alias);
            fake_proc(
                &p,
                11,
                10,
                "tmux: client",
                &["tmux", "-L", "mios-human", "attach"],
                &env,
                "/dev/pts/9",
            );
            for device in [0, 4 * 256 + 4, 136 * 256 + 3] {
                std::fs::write(
                    p.join("10/stat"),
                    format!("10 (shell ) name) S 1 10 10 {device} 0"),
                )
                .unwrap();
                assert!(
                    tmux_client_on_tty(&p, Path::new("/dev/tty3"), &socks, 1000).is_none(),
                    "{alias}: unrelated controlling tty {device}"
                );
            }
            std::fs::write(p.join("10/stat"), "10 (shell ) name) S 1 10 10 1027 0").unwrap();
            assert_eq!(
                tmux_client_on_tty(&p, Path::new("/dev/tty3"), &socks, 1000).map(|c| c.pid),
                Some(11)
            );
            // A client attached directly through an alias also needs physical VT evidence.
            std::fs::remove_file(p.join("11/fd/0")).unwrap();
            fake_proc(
                &p,
                11,
                10,
                "tmux: client",
                &["tmux", "-L", "mios-human", "attach"],
                &env,
                alias,
            );
            assert!(tmux_client_at(&p, 11, &socks, 1000).is_none());
            std::fs::write(
                p.join("11/stat"),
                format!("11 (tmux: client) S 10 11 11 1027 0{} 11", " 0".repeat(13)),
            )
            .unwrap();
            assert_eq!(
                tmux_client_at(&p, 11, &socks, 1000)
                    .map(|c| c.tty)
                    .as_deref(),
                Some(alias)
            );
        }
        std::fs::remove_dir_all(&t).unwrap();
    }
    #[test]
    fn capture_osd_names_the_app_and_the_release_chord() {
        let chord = vec!["BTN_THUMBL".to_owned(), "BTN_THUMBR".to_owned()];
        assert_eq!(
            osd_message("on", "org.gnome.Nautilus", &chord),
            "Controller -> org.gnome.Nautilus. Hold L3+R3 to release."
        );
        assert_eq!(
            osd_message("fullscreen", "steam_app_570", &chord),
            "Controller -> steam_app_570. Hold L3+R3 to release."
        );
        assert_eq!(
            osd_message("off", "steam_app_570", &chord),
            "Controller -> MiOS. steam_app_570 released."
        );
        assert_eq!(
            osd_message("on", "x", &["BTN_TL".into(), "BTN_TR".into()]),
            "Controller -> x. Hold TL+TR to release."
        );
        let vendor: toml::Value = include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap();
        let (cfg, _) = GlobalConfig::from_value(&vendor).unwrap();
        assert!(
            cfg.controller.capture_osd
                && cfg
                    .controller
                    .capture_osd_argv
                    .iter()
                    .any(|a| a == "{message}")
        );
        let lua = window_lua("on", &cfg, None).unwrap();
        assert!(
            lua.contains("..' '..tostring(target and target.class or '')"),
            "the transaction reports the class it captured"
        );
    }
    #[test]
    fn bounded_command_does_not_wait_for_descendants_stdout() {
        let now = Instant::now();
        let argv = vec![
            "/bin/sh".into(),
            "-c".into(),
            "sleep 10 & printf ready".into(),
        ];
        assert_eq!(
            run_bounded(&argv, Duration::from_millis(300)).unwrap(),
            "ready"
        );
        assert!(now.elapsed() < Duration::from_secs(1));
    }
    #[test]
    fn command_cancellation_revokes_an_inflight_job_promptly() {
        let cancelled = std::sync::Arc::new(AtomicBool::new(false));
        let other = cancelled.clone();
        let worker = std::thread::spawn(move || {
            run_bounded_cancel(
                &["/bin/sh".into(), "-c".into(), "sleep 10".into()],
                Duration::from_secs(10),
                Some(&other),
            )
        });
        std::thread::sleep(Duration::from_millis(30));
        let now = Instant::now();
        cancelled.store(true, Ordering::Relaxed);
        assert!(worker.join().unwrap().unwrap_err().contains("cancelled"));
        assert!(now.elapsed() < Duration::from_millis(500));
    }
    #[test]
    fn assigned_session_focus_is_independent_but_respects_foreign_seats() {
        let mut desktop = ContextSnapshot::new("app-capture");
        desktop.address = Some("123".into());
        desktop.pid = Some(42);
        desktop.room_capture = true;
        let assigned = session_focus(&desktop, true, None);
        assert_eq!(assigned.name, "console");
        assert!(assigned.address.is_none() && assigned.pid.is_none() && !assigned.room_capture);
        assert_eq!(
            session_focus(&desktop, false, None).address,
            desktop.address
        );
        desktop.suspended = Some("no seated session for this user".into());
        assert!(session_focus(&desktop, true, None).suspended.is_none());
        assert!(session_focus(&desktop, false, None).suspended.is_some());
        desktop.suspended = Some("another session is active on seat0".into());
        assert_eq!(
            session_focus(&desktop, true, None).suspended,
            desktop.suspended
        );
        desktop.suspended = None;
        assert!(
            session_focus(&desktop, true, Some("duplicate owner"))
                .suspended
                .is_none(),
            "ambiguous node remains exclusively reserved"
        );
    }

    #[test]
    fn key_projection_into_a_followed_compositor_is_quoted() {
        let lua = key_lua(
            &crate::nav::parse_key_chord("CTRL+SHIFT+TAB").unwrap(),
            "0xabc\"x",
        );
        assert!(
            lua.contains(
                "mods=\"CTRL SHIFT\",key=\"Tab\",state='down',window=\"address:0xabc\\\"x\""
            ),
            "{lua}"
        );
        assert_eq!(lua.matches("send_key_state").count(), 2);
    }
    #[test]
    fn room_verbs_reach_the_plugin_lua_api() {
        // spec P1 (C-14a/b): the room table's verbs become hl.plugin.hypr3d.<verb>(...) in the room's compositor
        assert_eq!(
            command_for("hypr:hypr3d:cancel").unwrap()[2],
            "hl.plugin.hypr3d.cancel()"
        );
        assert_eq!(
            command_for("hypr:hypr3d:sprint").unwrap()[2],
            "hl.plugin.hypr3d.sprint()"
        );
        assert_eq!(
            command_for("hypr:hypr3d:sprint on").unwrap()[2],
            "hl.plugin.hypr3d.sprint(\"on\")"
        );
        assert_eq!(
            command_for("hypr:hypr3d:recenter").unwrap()[2],
            "hl.plugin.hypr3d.recenter()"
        );
        assert_eq!(
            command_for("hypr:hypr3d:view").unwrap()[2],
            "hl.plugin.hypr3d.view(\"\")"
        );
        assert!(command_for("hypr:hypr3d:unknown")
            .unwrap_err()
            .contains("unsupported room action"));
        // every vendor room action has an installed dispatcher
        let doc: toml::Value = include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap();
        let (cfg, _) = GlobalConfig::from_value(&doc).unwrap();
        for v in cfg.controller.room.values() {
            let (tap, hold) = v.parts();
            for a in std::iter::once(tap).chain(hold).filter(|a| *a != "none") {
                assert!(
                    configured_command(a, &cfg).is_ok(),
                    "room action without a dispatcher: {a}"
                );
            }
        }
    }
    #[test]
    fn guide_layer_move_window_and_renamed_monitor_resolve() {
        let v = command_for("hypr:movewindow left").unwrap();
        assert_eq!(
            v[2],
            r#"hl.dispatch(hl.dsp.window.move({direction="left"}))"#
        );
        assert!(command_for("hypr:movewindow sideways").is_err());
        let (g, _) = crate::config::tests::fixture_nav();
        assert!(configured_command("mios:system-monitor", &g)
            .unwrap()
            .iter()
            .any(|a| a == "system"));
        assert!(
            configured_command("quickshell:monitor-toggle", &g)
                .unwrap_err()
                .contains("no installed IPC handler"),
            "C-19: the old alias is gone"
        );
        let v: toml::Value = include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap();
        let (g, _) = GlobalConfig::from_value(&v).unwrap();
        assert_eq!(
            configured_command("mios:volume up", &g).unwrap()[0],
            "/usr/bin/wpctl"
        );
        assert_eq!(
            configured_command("mios:media next", &g).unwrap(),
            vec!["/usr/bin/playerctl", "next"]
        );
        assert!(
            unsupported_actions(&g).is_empty(),
            "{:?}",
            unsupported_actions(&g)
        );
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
    mod session_worker_integration {
        use super::*;
        struct PrivateServer {
            dir: tempfile::TempDir,
            socket: PathBuf,
            stopped: Option<i32>,
        }
        impl PrivateServer {
            fn new() -> Self {
                let dir = tempfile::Builder::new()
                    .prefix("mios-session-worker-")
                    .tempdir()
                    .unwrap();
                let socket = dir.path().join("tmux");
                Self {
                    dir,
                    socket,
                    stopped: None,
                }
            }
            fn tmux(&self, args: &[&str]) -> String {
                let output = Command::new("/usr/bin/tmux")
                    .arg("-S")
                    .arg(&self.socket)
                    .args(["-f", "/dev/null"])
                    .args(args)
                    .env_remove("TMUX")
                    .env_remove("TMUX_PANE")
                    .output()
                    .unwrap();
                assert!(
                    output.status.success(),
                    "private tmux: {}",
                    String::from_utf8_lossy(&output.stderr)
                );
                String::from_utf8(output.stdout).unwrap().trim().to_owned()
            }
            fn receiver(&self, session: &str, file: &str, split: Option<&str>) -> String {
                let path = self.dir.path().join(file);
                let quoted = format!("'{}'", path.to_str().unwrap().replace('\'', "'\\''"));
                let shell = format!("stty raw -echo; exec cat > {quoted}");
                let pane = if let Some(target) = split {
                    self.tmux(&[
                        "split-window",
                        "-h",
                        "-t",
                        target,
                        "-P",
                        "-F",
                        "#{pane_id}",
                        "--",
                        "/bin/sh",
                        "-c",
                        &shell,
                    ])
                } else {
                    self.tmux(&[
                        "new-session",
                        "-d",
                        "-x",
                        "100",
                        "-y",
                        "30",
                        "-s",
                        session,
                        "-P",
                        "-F",
                        "#{pane_id}",
                        "--",
                        "/bin/sh",
                        "-c",
                        &shell,
                    ])
                };
                Self::wait(|| path.exists());
                pane
            }
            fn route(&self, session: &str, pane: &str) -> crate::sessions::SessionRoute {
                crate::sessions::SessionRoute {
                    name: session.into(),
                    controllers: vec!["/dev/input/event0".into()],
                    socket: self.socket.clone(),
                    session: session.into(),
                    pane: pane.into(),
                    ssh_host: None,
                }
            }
            fn bytes(&self, file: &str) -> Vec<u8> {
                std::fs::read(self.dir.path().join(file)).unwrap_or_default()
            }
            fn wait(condition: impl FnMut() -> bool) {
                Self::wait_named("private receiver", condition);
            }
            fn wait_named(name: &str, mut condition: impl FnMut() -> bool) {
                let end = Instant::now() + Duration::from_secs(3);
                while !condition() {
                    assert!(Instant::now() < end, "{name} timed out");
                    std::thread::sleep(Duration::from_millis(5));
                }
            }
            fn is_inspection_child(&self, status: &str, command: &[u8]) -> bool {
                let parent = status
                    .lines()
                    .find_map(|line| line.strip_prefix("PPid:")?.trim().parse::<u32>().ok());
                if parent != Some(std::process::id()) {
                    return false;
                }
                let args = command.split(|b| *b == 0).collect::<Vec<_>>();
                args.contains(&self.socket.as_os_str().as_encoded_bytes())
                    && args.contains(&b"display-message".as_slice())
                    && args.contains(&crate::sessions::IDENTITY_FORMAT.as_bytes())
            }
            fn inspection_started(&self) -> bool {
                // Some container procfs implementations omit task/*/children.
                // PPid still identifies our direct children, including those
                // spawned by worker threads. Require the identity query so a
                // background pane-app verifier cannot satisfy this barrier.
                std::fs::read_dir("/proc")
                    .unwrap()
                    .filter_map(Result::ok)
                    .filter(|entry| {
                        entry.file_name().to_str().is_some_and(|name| {
                            !name.is_empty() && name.bytes().all(|byte| byte.is_ascii_digit())
                        })
                    })
                    .any(|entry| {
                        let status = std::fs::read_to_string(entry.path().join("status"))
                            .unwrap_or_default();
                        let command =
                            std::fs::read(entry.path().join("cmdline")).unwrap_or_default();
                        self.is_inspection_child(&status, &command)
                    })
            }
            fn stop(&mut self) {
                let pid = self
                    .tmux(&["display-message", "-p", "#{pid}"])
                    .parse::<i32>()
                    .unwrap();
                // SAFETY: this PID was returned by this fixture's private socket, never an operator server.
                assert_eq!(unsafe { libc::kill(pid, libc::SIGSTOP) }, 0);
                self.stopped = Some(pid);
            }
            fn resume(&mut self) {
                if let Some(pid) = self.stopped.take() {
                    // SAFETY: paired only with stop() on our private server.
                    assert_eq!(unsafe { libc::kill(pid, libc::SIGCONT) }, 0);
                }
            }
        }
        impl Drop for PrivateServer {
            fn drop(&mut self) {
                self.resume();
                let _ = Command::new("/usr/bin/tmux")
                    .arg("-S")
                    .arg(&self.socket)
                    .arg("kill-server")
                    .env_remove("TMUX")
                    .env_remove("TMUX_PANE")
                    .output();
            }
        }

        struct PrivateClient {
            process: Child,
            stop: std::sync::Arc<AtomicBool>,
            reader: Option<std::thread::JoinHandle<()>>,
        }
        impl PrivateClient {
            fn attach(server: &PrivateServer, session: &str) -> Self {
                Self::attach_reusable(server, session, false)
            }
            fn attach_reusable(server: &PrivateServer, session: &str, reuse: bool) -> Self {
                use std::os::fd::{AsRawFd, FromRawFd};
                use std::os::unix::process::CommandExt;
                let (mut master, mut slave) = (-1, -1);
                let size = libc::winsize {
                    ws_row: 30,
                    ws_col: 100,
                    ws_xpixel: 0,
                    ws_ypixel: 0,
                };
                // SAFETY: openpty initializes two owned descriptors and reads a live winsize.
                assert_eq!(
                    unsafe {
                        libc::openpty(
                            &mut master,
                            &mut slave,
                            std::ptr::null_mut(),
                            std::ptr::null(),
                            &size,
                        )
                    },
                    0
                );
                let (mut master, slave) = unsafe {
                    (
                        std::fs::File::from_raw_fd(master),
                        std::fs::File::from_raw_fd(slave),
                    )
                };
                let flags = unsafe { libc::fcntl(master.as_raw_fd(), libc::F_GETFL) };
                assert_eq!(
                    unsafe {
                        libc::fcntl(master.as_raw_fd(), libc::F_SETFL, flags | libc::O_NONBLOCK)
                    },
                    0
                );
                let mut command = Command::new("/bin/sh");
                command
                    .args([
                        "-c",
                        if reuse {
                            "/usr/bin/tmux \"$@\"; /usr/bin/tmux \"$@\"; wait"
                        } else {
                            "/usr/bin/tmux \"$@\"; wait"
                        },
                        "mios-private-client",
                        "-S",
                    ])
                    .arg(&server.socket)
                    .args(["attach-session", "-t", session])
                    .env("TERM", "xterm-256color")
                    .env_remove("TMUX")
                    .env_remove("TMUX_PANE")
                    .stdin(Stdio::from(slave.try_clone().unwrap()))
                    .stdout(Stdio::from(slave.try_clone().unwrap()))
                    .stderr(Stdio::from(slave));
                // SAFETY: child-only session setup after Command duplicated the private slave onto stdin.
                unsafe {
                    command.pre_exec(|| {
                        if libc::setsid() < 0 || libc::ioctl(0, libc::TIOCSCTTY as _, 0) < 0 {
                            return Err(std::io::Error::last_os_error());
                        }
                        Ok(())
                    });
                }
                let process = command.spawn().unwrap();
                let stop = std::sync::Arc::new(AtomicBool::new(false));
                let cancelled = stop.clone();
                let reader = std::thread::spawn(move || {
                    let mut buffer = [0u8; 4096];
                    while !cancelled.load(Ordering::Relaxed) {
                        match master.read(&mut buffer) {
                            Ok(0) => break,
                            Ok(_) => {}
                            Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                                std::thread::sleep(Duration::from_millis(2))
                            }
                            Err(_) => break,
                        }
                    }
                });
                let client = Self {
                    process,
                    stop,
                    reader: Some(reader),
                };
                PrivateServer::wait(|| {
                    captured_tmux(&capture_config(), client.process.id())
                        .is_ok_and(|target| target.is_some())
                });
                client
            }
        }
        impl Drop for PrivateClient {
            fn drop(&mut self) {
                // SAFETY: setsid made this fixture's shell PID its own process-group ID.
                unsafe {
                    libc::kill(-(self.process.id() as i32), libc::SIGKILL);
                }
                let _ = self.process.wait();
                self.stop.store(true, Ordering::Relaxed);
                if let Some(reader) = self.reader.take() {
                    let _ = reader.join();
                }
            }
        }
        fn capture_config() -> GlobalConfig {
            let mut cfg = config();
            cfg.controller.capture_navigation_contexts = vec!["terminal".into()];
            cfg.controller.nav.tmux_sockets.push("tmux".into());
            cfg
        }
        fn receipt(client: &PrivateClient, address: &str) -> CaptureReceipt {
            let cfg = capture_config();
            CaptureReceipt {
                native: None,
                address: address.into(),
                class: "Alacritty".into(),
                pid: client.process.id(),
                tmux: captured_tmux(&cfg, client.process.id()).unwrap(),
                error: None,
            }
        }
        #[test]
        fn capture_receipt_uses_selected_window_b_instead_of_stale_window_a() {
            const CHILD: &str = "MIOS_PRIVATE_CAPTURE_IPC_TEST";
            if std::env::var_os(CHILD).is_none() {
                let dir = tempfile::Builder::new()
                    .prefix("mios-capture-receipt-")
                    .tempdir()
                    .unwrap();
                let qualified = concat!(
                    module_path!(),
                    "::capture_receipt_uses_selected_window_b_instead_of_stale_window_a"
                );
                let test_name = qualified.split_once("::").unwrap().1;
                let output = Command::new(std::env::current_exe().unwrap())
                    .args(["--exact", test_name, "--nocapture"])
                    .env(CHILD, "1")
                    .env("XDG_RUNTIME_DIR", dir.path())
                    .env("HYPRLAND_INSTANCE_SIGNATURE", "mios-capture")
                    .output()
                    .unwrap();
                assert!(
                    output.status.success(),
                    "private capture subprocess: {}{}",
                    String::from_utf8_lossy(&output.stdout),
                    String::from_utf8_lossy(&output.stderr)
                );
                assert!(
                    String::from_utf8_lossy(&output.stdout).contains("1 passed"),
                    "capture subprocess must execute exactly its requested test"
                );
                return;
            }
            let server = PrivateServer::new();
            let a = server.receiver("alpha", "a", None);
            let b = server.receiver("beta", "b", None);
            let a_client = PrivateClient::attach(&server, "alpha");
            let b_client = PrivateClient::attach(&server, "beta");
            let cached_a = receipt(&a_client, "0xaaa");
            let cfg = capture_config();
            let socket = PathBuf::from(std::env::var_os("XDG_RUNTIME_DIR").unwrap())
                .join("hypr/mios-capture/.socket.sock");
            std::fs::create_dir_all(socket.parent().unwrap()).unwrap();
            let listener = std::os::unix::net::UnixListener::bind(socket).unwrap();
            let actual_pid = b_client.process.id();
            let compositor = std::thread::spawn(move || {
                let (mut stream, _) = listener.accept().unwrap();
                let mut request = [0u8; 16384];
                assert!(stream.read(&mut request).unwrap() > 0);
                write!(
                    stream,
                    "MIOS_INPUT_WINDOW_OK:0xbbb Alacritty\nMIOS_INPUT_WINDOW_PID:{actual_pid}"
                )
                .unwrap();
            });
            let selected_b = capture_transaction("on", &cfg, None).unwrap();
            compositor.join().unwrap();
            assert_eq!(selected_b.address, "0xbbb");
            assert_eq!(selected_b.pid, b_client.process.id());
            assert_eq!(selected_b.tmux.as_ref().unwrap().pane, b);
            assert_eq!(cached_a.tmux.as_ref().unwrap().pane, a);
            let mut binding =
                capture_binding(&selected_b, Path::new("/dev/input/event0"), &cfg).unwrap();
            let worker = match binding.worker.take().unwrap() {
                CaptureDispatcher::Tmux(worker) => worker,
                _ => panic!("expected a pinned tmux dispatcher"),
            };
            assert!(worker.submit("key:B".into()));
            drained(worker);
            PrivateServer::wait(|| server.bytes("b") == b"b");
            assert!(
                server.bytes("a").is_empty(),
                "the stale focused window must receive no captured input"
            );
        }
        #[test]
        fn captured_client_verbs_require_the_original_client_and_its_selected_pane() {
            let server = PrivateServer::new();
            let left = server.receiver("alpha", "left", None);
            let right = server.receiver("alpha", "right", Some(&left));
            server.tmux(&["select-pane", "-t", &left]);
            let client = PrivateClient::attach(&server, "alpha");
            let mut cfg = capture_config();
            cfg.tmux_prefix_keys.extend(["k".into(), "m".into()]);
            cfg.tmux_menu_key = Some("m".into());
            server.tmux(&["set-option", "prefix", &cfg.tmux_prefix]);
            let key_mark = server.dir.path().join("key-mark");
            let menu_mark = server.dir.path().join("menu-mark");
            for (key, mark) in [("k", &key_mark), ("m", &menu_mark)] {
                let script = format!("touch '{}'", mark.to_str().unwrap());
                server.tmux(&["bind-key", "-T", "prefix", key, "run-shell", &script]);
            }
            let captured = receipt(&client, "0xaaa");
            let mut binding =
                capture_binding(&captured, Path::new("/dev/input/event0"), &cfg).unwrap();
            let worker = match binding.worker.take().unwrap() {
                CaptureDispatcher::Tmux(worker) => worker,
                _ => panic!("expected a pinned tmux dispatcher"),
            };
            assert!(worker.submit("tmux:key k".into()));
            assert!(worker.submit("tmux:menu".into()));
            drained(worker);
            PrivateServer::wait(|| key_mark.exists() && menu_mark.exists());
            std::fs::remove_file(&key_mark).unwrap();
            std::fs::remove_file(&menu_mark).unwrap();
            server.tmux(&["select-pane", "-t", &right]);
            let mut binding =
                capture_binding(&captured, Path::new("/dev/input/event0"), &cfg).unwrap();
            let worker = match binding.worker.take().unwrap() {
                CaptureDispatcher::Tmux(worker) => worker,
                _ => panic!("expected a pinned tmux dispatcher"),
            };
            assert!(worker.submit("tmux:key k".into()));
            assert!(worker.submit("tmux:menu".into()));
            assert!(worker.submit("key:A".into()));
            drained(worker);
            PrivateServer::wait(|| server.bytes("left") == b"a");
            assert!(server.bytes("right").is_empty());
            assert!(
                !key_mark.exists() && !menu_mark.exists(),
                "client UI commands must reject a changed selected pane"
            );
            let mut replaced = captured.clone();
            replaced.tmux.as_mut().unwrap().start += 1;
            let mut binding =
                capture_binding(&replaced, Path::new("/dev/input/event0"), &cfg).unwrap();
            let worker = match binding.worker.take().unwrap() {
                CaptureDispatcher::Tmux(worker) => worker,
                _ => panic!("expected a pinned tmux dispatcher"),
            };
            assert!(worker.submit("key:X".into()));
            drained(worker);
            assert_eq!(
                server.bytes("left"),
                b"a",
                "a replaced client must receive no input"
            );
        }
        fn config() -> GlobalConfig {
            crate::config::tests::fixture_nav().0
        }
        fn drained(mut worker: SessionWorker) {
            worker.jobs = None;
            worker.worker.take().unwrap().join().unwrap();
        }
        #[test]
        fn actual_workers_isolate_sessions_and_follow_their_own_pane_cursor() {
            let server = PrivateServer::new();
            let left = server.receiver("alpha", "left", None);
            let right = server.receiver("alpha", "right", Some(&left));
            let beta = server.receiver("beta", "beta", None);
            let a = SessionWorker::spawn(server.route("alpha", &left), config()).unwrap();
            let b = SessionWorker::spawn(server.route("beta", &beta), config()).unwrap();
            assert!(a.submit("key:A".into()));
            assert!(a.submit("tmux:focus right".into()));
            assert!(a.submit("key:C".into()));
            assert!(b.submit("key:B".into()));
            drained(a);
            drained(b);
            PrivateServer::wait(|| {
                server.bytes("left") == b"a"
                    && server.bytes("right") == b"c"
                    && server.bytes("beta") == b"b"
            });
            assert_ne!(left, right);
        }
        #[test]
        fn wrong_session_and_replaced_server_never_receive_dispatch() {
            let server = PrivateServer::new();
            let pane = server.receiver("alpha", "first", None);
            let mut wrong = server.route("alpha", &pane);
            wrong.session = "unrelated".into();
            let worker = SessionWorker::spawn(wrong, config()).unwrap();
            assert!(worker.submit("key:X".into()));
            drained(worker);
            assert!(server.bytes("first").is_empty());
            let worker = SessionWorker::spawn(server.route("alpha", &pane), config()).unwrap();
            assert!(worker.submit("key:A".into()));
            PrivateServer::wait(|| server.bytes("first") == b"a");
            let old_pid = server.tmux(&["display-message", "-p", "#{pid}"]);
            server.tmux(&["kill-server"]);
            PrivateServer::wait(|| {
                std::fs::read_to_string(format!("/proc/{old_pid}/stat"))
                    .ok()
                    .is_none_or(|stat| {
                        stat.rsplit_once(')')
                            .is_some_and(|(_, tail)| tail.split_whitespace().next() == Some("Z"))
                    })
            });
            // tmux may leave its dead socket behind; only this fixture ever used this path.
            if server.socket.exists() {
                std::fs::remove_file(&server.socket).unwrap();
            }
            let replacement = server.receiver("alpha", "replacement", None);
            assert_eq!(
                replacement, pane,
                "server restart reuses the configured pane ID"
            );
            assert!(worker.submit("key:X".into()));
            drained(worker);
            assert!(
                server.bytes("replacement").is_empty(),
                "a new server must require explicit rebinding"
            );
        }
        #[test]
        fn expired_queue_entries_are_dropped_before_valid_input() {
            let server = PrivateServer::new();
            let pane = server.receiver("alpha", "output", None);
            let cfg = config();
            let age = Duration::from_millis(cfg.controller.context_stale_ms + 1);
            let worker = SessionWorker::spawn(server.route("alpha", &pane), cfg).unwrap();
            worker
                .jobs
                .as_ref()
                .unwrap()
                .send(SessionJob {
                    repeat: 1,
                    queued: Instant::now() - age,
                    app_control: None,
                    action: "key:X".into(),
                })
                .unwrap();
            assert!(worker.submit("key:A".into()));
            drained(worker);
            PrivateServer::wait(|| server.bytes("output") == b"a");
        }
        #[test]
        fn inspection_barrier_requires_a_direct_child_and_exact_worker_query() {
            let server = PrivateServer::new();
            let status = format!("Name:\ttmux\nPPid:\t{}\n", std::process::id());
            let command = |socket: &Path, query: &str, action: &str| {
                [
                    "/usr/bin/tmux",
                    "-S",
                    socket.to_str().unwrap(),
                    action,
                    "-p",
                    "-t",
                    "%0",
                    query,
                    "",
                ]
                .join("\0")
                .into_bytes()
            };
            let worker = command(
                &server.socket,
                crate::sessions::IDENTITY_FORMAT,
                "display-message",
            );
            assert!(server.is_inspection_child(&status, &worker));
            assert!(!server.is_inspection_child("Name:\ttmux\nPPid:\t0\n", &worker));
            assert!(!server.is_inspection_child("Name:\ttmux\n", &worker));
            assert!(!server.is_inspection_child(
                &status,
                &command(&server.socket, "#{pane_pid}", "display-message"),
            ));
            assert!(!server.is_inspection_child(
                &status,
                &command(
                    &server.dir.path().join("another-socket"),
                    crate::sessions::IDENTITY_FORMAT,
                    "display-message",
                ),
            ));
            assert!(!server.is_inspection_child(
                &status,
                &command(
                    &server.socket,
                    crate::sessions::IDENTITY_FORMAT,
                    "display-message-unrelated",
                ),
            ));
        }
        #[test]
        fn revocation_cancels_a_stalled_inspection_and_discards_queued_input() {
            let mut server = PrivateServer::new();
            let pane = server.receiver("alpha", "output", None);
            server.stop();
            let mut worker = SessionWorker::spawn(server.route("alpha", &pane), config()).unwrap();
            assert!(worker.submit("key:X".into()));
            assert!(worker.submit("key:Y".into()));
            PrivateServer::wait(|| server.inspection_started());
            let thread = worker.worker.take().unwrap();
            let now = Instant::now();
            drop(worker);
            thread.join().unwrap();
            assert!(
                now.elapsed() < Duration::from_millis(500),
                "revocation must not wait for target recovery"
            );
            server.resume();
            assert!(server.bytes("output").is_empty());
            let fresh = SessionWorker::spawn(server.route("alpha", &pane), config()).unwrap();
            assert!(fresh.submit("key:A".into()));
            drained(fresh);
            PrivateServer::wait(|| server.bytes("output") == b"a");
        }
        /// The P1 app layer with `exe` registered as the per-user candidate (user_exe), as the operator registers
        /// ~/.local/bin/mios-mons-sample in the user tier.
        fn mon_config(exe: &Path) -> GlobalConfig {
            let (mut cfg, _) = crate::config::tests::fixture_mon();
            cfg.controller.nav.apps.get_mut("mon").unwrap().user_exe =
                vec![exe.to_string_lossy().into_owned()];
            cfg
        }
        /// A private, self-owned copy of sleep (named sleep, so a multi-call coreutils still dispatches) that a pane
        /// can run: the stand-in for a per-user Mon build.
        fn private_binary() -> (tempfile::TempDir, PathBuf) {
            use std::os::unix::fs::PermissionsExt;
            let dir = tempfile::Builder::new()
                .prefix("mios-app-")
                .tempdir()
                .unwrap();
            let bin = dir.path().join("bin");
            std::fs::create_dir(&bin).unwrap();
            std::fs::set_permissions(&bin, std::fs::Permissions::from_mode(0o755)).unwrap();
            let exe = bin.join("sleep");
            std::fs::copy("/usr/bin/sleep", &exe).unwrap();
            std::fs::set_permissions(&exe, std::fs::Permissions::from_mode(0o755)).unwrap();
            let exe = std::fs::canonicalize(&exe).unwrap();
            (dir, exe)
        }
        fn new_pane(server: &PrivateServer, session: &str, argv: &[&str]) -> String {
            let mut args = vec![
                "new-session",
                "-d",
                "-x",
                "100",
                "-y",
                "30",
                "-s",
                session,
                "-P",
                "-F",
                "#{pane_id}",
                "--",
            ];
            args.extend_from_slice(argv);
            server.tmux(&args)
        }
        /// INPUTD spec tests 10-12 (routes): each route verifies only its own pane; route B (a shell) never selects
        /// the camera profile while route A runs the app; M-4 and M-- reach exactly B's pane; an ssh route never
        /// verifies.
        #[test]
        fn route_verifiers_select_the_app_only_for_their_own_pane() {
            let server = PrivateServer::new();
            let (_keep, sleep) = private_binary();
            let a = new_pane(&server, "alpha", &[sleep.to_str().unwrap(), "60"]);
            let file = server.dir.path().join("beta");
            let shell = format!("stty raw -echo; cat > '{}'; :", file.display());
            let b = new_pane(&server, "beta", &["/bin/sh", "-c", &shell]);
            PrivateServer::wait_named("beta receiver file", || file.exists());
            let cfg = mon_config(&sleep);
            let stale = cfg.controller.context_stale_ms;
            let wa = SessionWorker::spawn(server.route("alpha", &a), cfg.clone()).unwrap();
            let wb = SessionWorker::spawn(server.route("beta", &b), cfg.clone()).unwrap();
            PrivateServer::wait_named("alpha registered foreground app", || {
                wa.app(stale).as_deref() == Some("mon")
            });
            PrivateServer::wait_named("beta negative app probe completion", || {
                wb.app.lock().unwrap().1.is_some()
            });
            assert_eq!(wb.app(stale), None, "a shell pane selects no app");
            assert_eq!(
                wa.app(0),
                None,
                "a result older than context_stale_ms is none"
            );
            let (_, legacy) = crate::config::tests::fixture_mon();
            let route_router = |app: Option<String>| {
                let mut r =
                    crate::router::Router::new(cfg.clone(), legacy.clone(), BTreeMap::new());
                r.set_context("console");
                r.set_tmux(true);
                r.set_app(app.as_deref(), None);
                r
            };
            let ra = route_router(wa.app(stale));
            let rb = route_router(wb.app(stale));
            assert_eq!(ra.nav_profile().as_deref(), Some("mon_console_tmux"));
            assert!(ra.camera());
            assert_eq!(rb.nav_profile().as_deref(), Some("console_tmux"));
            assert!(!rb.camera());
            // Session tmux_key: ALT+4 = M-4, ALT+MINUS = M-- (ESC-prefixed bytes in the pane).
            assert!(wb.submit("key:ALT+4".into()));
            assert!(wb.submit("key:ALT+MINUS".into()));
            drained(wb);
            PrivateServer::wait_named("beta exact key bytes", || {
                server.bytes("beta") == b"\x1b4\x1b-"
            });
            let mut ssh = server.route("alpha", &a);
            ssh.ssh_host = Some("blade-two".into());
            assert!(crate::sessions::pane_pid_argv(&ssh).is_err());
            assert!(crate::sessions::pane_pid_argv(&server.route("alpha", &a)).is_ok());
        }
        /// INPUTD spec test 10 (tmux): the focused terminal's client, its active pane's foreground leader, and a
        /// stalled server timing out within app_query_timeout_ms (never on the input loop).
        #[test]
        fn a_tmux_client_pane_is_verified_bounded_and_bound_to_its_window() {
            let mut server = PrivateServer::new();
            let (_keep, sleep) = private_binary();
            let pane = new_pane(&server, "alpha", &[sleep.to_str().unwrap(), "60"]);
            let client = PrivateClient::attach(&server, "alpha");
            let mut cfg = mon_config(&sleep);
            cfg.controller.nav.tmux_sockets.push("tmux".into());
            let uid = unsafe { libc::getuid() };
            let found = tmux_client_under(
                Path::new("/proc"),
                client.process.id(),
                &cfg.controller.nav.tmux_sockets,
                uid,
            )
            .unwrap();
            let probe = AppProbe::Tmux {
                target: Some(("0xterm".into(), 7)),
                client: found.clone(),
            };
            assert_eq!(probe.verify(&cfg, uid).as_deref(), Some("mon"));
            let tmux = std::fs::canonicalize("/usr/bin/tmux").unwrap();
            assert_eq!(probe.verify(&mon_config(&tmux), uid), None, "another exe");
            let mut snap = ContextSnapshot::new("terminal");
            snap.address = Some("0xterm".into());
            snap.pid = Some(7);
            snap.tmux_client = Some(found);
            verify_app(
                &cfg,
                &mut snap,
                "terminal",
                None,
                &mut AppCache::default(),
                uid,
            );
            assert_eq!(snap.app.as_deref(), Some("mon"));
            let accepted = snap.clone();
            let accepted_process = accepted
                .app_process
                .clone()
                .expect("context acceptance pins the app process");
            server.tmux(&[
                "respawn-pane",
                "-k",
                "-t",
                &pane,
                sleep.to_str().unwrap(),
                "60",
            ]);
            PrivateServer::wait_named("replacement foreground identity", || {
                probe
                    .identity(&cfg, uid)
                    .is_some_and(|(_, process)| process != accepted_process)
            });
            assert!(
                app_input_process(
                    &cfg,
                    &accepted,
                    "mon",
                    Duration::from_millis(cfg.controller.nav.app_query_timeout_ms)
                )
                .is_err(),
                "queued action cannot choose the replacement app in the same terminal"
            );
            verify_app(
                &cfg,
                &mut snap,
                "terminal",
                None,
                &mut AppCache::default(),
                uid,
            );
            assert_eq!(snap.app.as_deref(), Some("mon"));
            assert_ne!(snap.app_process, Some(accepted_process));
            assert_eq!(
                snap.app_target,
                Some(("0xterm".into(), 7)),
                "bound to this window"
            );
            verify_app(
                &cfg,
                &mut snap,
                "desktop-2d",
                None,
                &mut AppCache::default(),
                uid,
            );
            assert!(snap.app.is_none(), "a context no app refines never queries");
            server.stop();
            let started = Instant::now();
            assert_eq!(probe.verify(&cfg, uid), None, "a stalled server is no app");
            assert!(
                started.elapsed()
                    < Duration::from_millis(cfg.controller.nav.app_query_timeout_ms + 400),
                "bounded by app_query_timeout_ms"
            );
            server.resume();
        }
        mod controller_acceptance {
            include!("mios-controller-runtime-acceptance.rs");
            include!("mios-controller-legacy-client-acceptance.rs");
        }
    }
}

#[cfg(test)]
mod app_detection {
    //! P3 verified foreground detection and the root ruling on per-user candidates: exact, canonical,
    //! ownership-checked matching only. Fake /proc trees point at real candidate files in a private directory.
    use super::*;
    use crate::nav::NavApp;
    use std::os::unix::fs::PermissionsExt;
    #[test]
    fn controller_return_manifest_advertises_supported_protocol() {
        let runtime = tempfile::tempdir().unwrap();
        super::update_active_manifest(
            runtime.path(),
            &std::collections::BTreeMap::new(),
            &std::collections::BTreeMap::new(),
            true,
            2000,
            ManifestFocus {
                frontend: None,
                stale_ms: 750,
                focus: None,
                target: None,
                owned: false,
                remote: false,
            },
        );
        let record: serde_json::Value = serde_json::from_slice(
            &std::fs::read(runtime.path().join("mios-inputd-active.json")).unwrap(),
        )
        .unwrap();
        assert_eq!(record["controller_scan_ms"], 2000);
        assert_eq!(
            record["controller_return_protocol_version"].as_u64(),
            Some(1)
        );
        assert_eq!(
            record["bridge_pid"].as_u64(),
            Some(u64::from(std::process::id()))
        );
        assert!(record["bridge_start"]
            .as_u64()
            .is_some_and(|start| start > 0));
    }

    fn apps(exe: &[&Path], user_exe: &[&Path]) -> BTreeMap<String, NavApp> {
        let s = |v: &[&Path]| v.iter().map(|p| p.to_string_lossy().into_owned()).collect();
        BTreeMap::from([(
            "mon".to_owned(),
            NavApp {
                exe: s(exe),
                user_exe: s(user_exe),
                camera: true,
                contexts: BTreeMap::from([("terminal".into(), "mon".into())]),
                repeat: BTreeMap::new(),
            },
        )])
    }
    /// A /proc/<pid> entry: stat with pgrp and the tty's foreground group, an exe link and an optional stdin tty.
    fn entry(
        proc_root: &Path,
        pid: u32,
        ppid: u32,
        pgrp: u32,
        tpgid: u32,
        exe: &Path,
        tty: Option<&str>,
    ) {
        let d = proc_root.join(pid.to_string());
        let _ = std::fs::remove_dir_all(&d);
        std::fs::create_dir_all(d.join("fd")).unwrap();
        std::fs::write(
            d.join("stat"),
            format!("{pid} (proc {pid}) S {ppid} {pgrp} {pgrp} 1027 {tpgid} 4194304"),
        )
        .unwrap();
        std::os::unix::fs::symlink(exe, d.join("exe")).unwrap();
        if let Some(t) = tty {
            std::os::unix::fs::symlink(t, d.join("fd/0")).unwrap();
        }
    }
    struct Tree {
        dir: tempfile::TempDir,
        proc_root: PathBuf,
        sample: PathBuf,
        elsewhere: PathBuf,
    }
    impl Tree {
        fn new() -> Self {
            let dir = tempfile::tempdir().unwrap();
            let bin = dir.path().join("home/.local/bin");
            let other = dir.path().join("opt/bin");
            for d in [&bin, &other] {
                std::fs::create_dir_all(d).unwrap();
            }
            for d in [
                dir.path().join("home"),
                dir.path().join("home/.local"),
                bin.clone(),
                dir.path().join("opt"),
                other.clone(),
            ] {
                std::fs::set_permissions(&d, std::fs::Permissions::from_mode(0o755)).unwrap();
            }
            let sample = bin.join("mios-mons-sample");
            let elsewhere = other.join("mios-mons-sample");
            for f in [&sample, &elsewhere] {
                std::fs::write(f, b"\x7fELF fixture").unwrap();
                std::fs::set_permissions(f, std::fs::Permissions::from_mode(0o755)).unwrap();
            }
            let proc_root = dir.path().join("proc");
            std::fs::create_dir_all(&proc_root).unwrap();
            Self {
                dir,
                proc_root,
                sample,
                elsewhere,
            }
        }
        /// Real lstat inside the private tree; its ancestors (the build host's tmp) stand in for root-owned 0755.
        fn lstat(&self) -> impl Fn(&Path) -> Option<FileFacts> + '_ {
            move |p: &Path| {
                if self.dir.path().starts_with(p) && p != self.dir.path() {
                    Some(FileFacts {
                        uid: 0,
                        mode: 0o40755,
                        dev: 0,
                        ino: 0,
                        kind: FileKind::Dir,
                    })
                } else {
                    lstat_facts(p)
                }
            }
        }
    }
    fn uid() -> u32 {
        unsafe { libc::getuid() }
    }
    #[test]
    fn trust_rules_are_canonical_owned_and_not_writable_by_others() {
        let me = uid();
        let fs = |over: &[(&str, u32, u32, FileKind)]| {
            let mut m: BTreeMap<PathBuf, FileFacts> = BTreeMap::new();
            for (p, u, mode, kind) in [
                ("/", 0, 0o755, FileKind::Dir),
                ("/home", 0, 0o755, FileKind::Dir),
                ("/home/mios", me, 0o700, FileKind::Dir),
                ("/home/mios/.local", me, 0o755, FileKind::Dir),
                ("/home/mios/.local/bin", me, 0o755, FileKind::Dir),
                (
                    "/home/mios/.local/bin/mios-mons-sample",
                    me,
                    0o755,
                    FileKind::File,
                ),
                ("/tmp", 0, 0o1777, FileKind::Dir),
                ("/tmp/x", me, 0o700, FileKind::Dir),
                ("/tmp/x/mon", me, 0o755, FileKind::File),
            ]
            .into_iter()
            .chain(over.iter().copied())
            {
                m.insert(
                    p.into(),
                    FileFacts {
                        uid: u,
                        mode,
                        dev: 1,
                        ino: 2,
                        kind,
                    },
                );
            }
            move |p: &Path| m.get(p).copied()
        };
        let sample = Path::new("/home/mios/.local/bin/mios-mons-sample");
        let ok = |over: &[(&str, u32, u32, FileKind)], path: &Path, owner: u32| {
            trusted_candidate(path, owner, me, &fs(over)).is_some()
        };
        assert!(ok(&[], sample, me), "the registered per-user binary");
        assert!(
            ok(&[], Path::new("/tmp/x/mon"), me),
            "a root-owned sticky ancestor"
        );
        let file = "/home/mios/.local/bin/mios-mons-sample";
        for (why, over, owner) in [
            (
                "owned by another uid",
                vec![(file, 4242, 0o755, FileKind::File)],
                me,
            ),
            (
                "group-writable",
                vec![(file, me, 0o775, FileKind::File)],
                me,
            ),
            (
                "world-writable",
                vec![(file, me, 0o757, FileKind::File)],
                me,
            ),
            ("a symlink", vec![(file, me, 0o777, FileKind::Other)], me),
            (
                "a world-writable directory",
                vec![("/home/mios/.local/bin", me, 0o777, FileKind::Dir)],
                me,
            ),
            (
                "a foreign directory",
                vec![("/home/mios/.local", 4242, 0o755, FileKind::Dir)],
                me,
            ),
            (
                "a symlinked directory",
                vec![("/home/mios/.local", me, 0o777, FileKind::Other)],
                me,
            ),
            ("a system exe must be root-owned", vec![], 0),
        ] {
            assert!(!ok(&over, sample, owner), "{why}");
        }
        assert!(
            !ok(
                &[("/tmp", 4242, 0o1777, FileKind::Dir)],
                Path::new("/tmp/x/mon"),
                me
            ),
            "a sticky directory another user owns"
        );
        for path in [
            "relative/mon",
            "/home/mios/../mios/.local/bin/mios-mons-sample",
            "/home/mios/.local/bin/missing",
        ] {
            assert!(!ok(&[], Path::new(path), me), "{path}");
        }
    }
    #[test]
    fn exe_matching_is_exact_canonical_owned_and_live() {
        let t = Tree::new();
        let me = uid();
        let lstat = t.lstat();
        let registered = apps(&[], &[&t.sample]);
        entry(&t.proc_root, 10, 1, 10, 10, &t.sample, None);
        assert_eq!(
            app_of_pid(&t.proc_root, 10, &registered, me, &lstat).as_deref(),
            Some("mon"),
            "the per-user candidate, registered explicitly"
        );
        // A same-basename binary elsewhere never matches (no basename matching).
        entry(&t.proc_root, 11, 1, 11, 11, &t.elsewhere, None);
        assert_eq!(app_of_pid(&t.proc_root, 11, &registered, me, &lstat), None);
        // /proc/<pid> owned by another uid.
        assert_eq!(
            app_of_pid(&t.proc_root, 10, &registered, me + 1, &lstat),
            None
        );
        // A candidate owned by another uid (lstat says so), or listed as a root-owned system exe.
        let foreign = |p: &Path| {
            lstat(p).map(|mut f| {
                if p == t.sample {
                    f.uid = 4242;
                }
                f
            })
        };
        assert_eq!(
            app_of_pid(&t.proc_root, 10, &registered, me, &foreign),
            None
        );
        assert_eq!(
            app_of_pid(&t.proc_root, 10, &apps(&[&t.sample], &[]), me, &lstat),
            None,
            "a user-owned file is no system exe"
        );
        // Group- or world-writable candidate, or a world-writable directory.
        for (path, mode) in [
            (&t.sample, 0o775),
            (&t.sample, 0o757),
            (&t.sample.parent().unwrap().to_path_buf(), 0o777),
        ] {
            let before = std::fs::metadata(path).unwrap().permissions();
            std::fs::set_permissions(path, std::fs::Permissions::from_mode(mode)).unwrap();
            assert_eq!(
                app_of_pid(&t.proc_root, 10, &registered, me, &lstat),
                None,
                "{mode:o}"
            );
            std::fs::set_permissions(path, before).unwrap();
        }
        assert!(app_of_pid(&t.proc_root, 10, &registered, me, &lstat).is_some());
        // A registered symlink never matches: not when the kernel names its target, not when it names the link, and
        // not when the link resolves to a target nobody listed.
        let link = t.sample.with_file_name("mios-mons-link");
        std::os::unix::fs::symlink(&t.sample, &link).unwrap();
        let via_link = apps(&[], &[&link]);
        assert_eq!(app_of_pid(&t.proc_root, 10, &via_link, me, &lstat), None);
        entry(&t.proc_root, 12, 1, 12, 12, &link, None);
        assert_eq!(app_of_pid(&t.proc_root, 12, &via_link, me, &lstat), None);
        let stray = t.sample.with_file_name("mios-mons-stray");
        std::os::unix::fs::symlink(&t.elsewhere, &stray).unwrap();
        entry(&t.proc_root, 13, 1, 13, 13, &t.elsewhere, None);
        assert_eq!(
            app_of_pid(&t.proc_root, 13, &apps(&[], &[&stray]), me, &lstat),
            None
        );
        // A deleted image: the kernel's "(deleted)" name, or a candidate removed under a running process.
        let deleted = PathBuf::from(format!("{} (deleted)", t.sample.display()));
        entry(&t.proc_root, 14, 1, 14, 14, &deleted, None);
        assert_eq!(app_of_pid(&t.proc_root, 14, &registered, me, &lstat), None);
        let gone = t.sample.with_file_name("mios-mons-gone");
        std::fs::copy(&t.sample, &gone).unwrap();
        entry(&t.proc_root, 15, 1, 15, 15, &gone, None);
        let gone_app = apps(&[], &[&gone]);
        assert!(app_of_pid(&t.proc_root, 15, &gone_app, me, &lstat).is_some());
        std::fs::remove_file(&gone).unwrap();
        assert_eq!(app_of_pid(&t.proc_root, 15, &gone_app, me, &lstat), None);
        // A missing process.
        assert_eq!(app_of_pid(&t.proc_root, 99, &registered, me, &lstat), None);
    }
    #[test]
    fn a_root_owned_system_binary_matches_and_an_unmapped_owner_fails_closed() {
        let t = Tree::new();
        let me = uid();
        let lstat = t.lstat();
        // The vendor `exe` is root-owned: the private file seen as root's (its dev/ino stay the real ones).
        let as_root = |p: &Path| {
            lstat(p).map(|mut f| {
                f.uid = 0;
                f
            })
        };
        entry(&t.proc_root, 20, 1, 20, 20, &t.sample, None);
        assert_eq!(
            app_of_pid(&t.proc_root, 20, &apps(&[&t.sample], &[]), me, &as_root).as_deref(),
            Some("mon")
        );
        // A system executable and its trusted parents must not hide a foreign process owner.
        // Keeping every candidate fact root-owned isolates the /proc UID check from file ownership.
        assert_eq!(
            app_of_pid(&t.proc_root, 20, &apps(&[&t.sample], &[]), me + 1, &as_root),
            None,
            "a root-owned executable never admits another user's foreground process"
        );
        assert_eq!(
            app_of_pid(&t.proc_root, 20, &apps(&[], &[&t.sample]), me, &as_root),
            None,
            "a root-owned binary is no per-user candidate"
        );
        // The real filesystem: /usr/bin/tmux matches as a system exe exactly when its whole path really is
        // root-owned. A user namespace that shows host root as the overflow uid (a rootless build container) must
        // fail closed, never trust an owner it cannot see.
        let tmux = std::fs::canonicalize("/usr/bin/tmux").unwrap();
        entry(&t.proc_root, 21, 1, 21, 21, &tmux, None);
        let rooted = tmux
            .ancestors()
            .all(|p| lstat_facts(p).is_some_and(|f| f.uid == 0 && f.mode & 0o022 == 0));
        assert_eq!(
            app_of_pid(&t.proc_root, 21, &apps(&[&tmux], &[]), me, &lstat_facts).is_some(),
            rooted
        );
    }
    #[test]
    fn only_the_foreground_group_leader_counts() {
        let t = Tree::new();
        let me = uid();
        let lstat = t.lstat();
        let registered = apps(&[], &[&t.sample]);
        let shell = Path::new("/usr/bin/bash");
        // Plain terminal 100: bash 101 started the Mon 102 in the foreground of its tty.
        entry(&t.proc_root, 100, 1, 100, 0, shell, None);
        entry(&t.proc_root, 101, 100, 101, 102, shell, Some("/dev/pts/4"));
        entry(
            &t.proc_root,
            102,
            101,
            102,
            102,
            &t.sample,
            Some("/dev/pts/4"),
        );
        let tree = descendants(&t.proc_root, 100);
        assert_eq!(tree, [101, 102]);
        assert_eq!(
            foreground_app_among(&t.proc_root, &tree, &registered, me, &lstat).as_deref(),
            Some("mon")
        );
        // Terminal 200: the Mon 202 runs in the background; bash 201 owns the tty.
        entry(&t.proc_root, 200, 1, 200, 0, shell, None);
        entry(&t.proc_root, 201, 200, 201, 201, shell, Some("/dev/pts/5"));
        entry(
            &t.proc_root,
            202,
            201,
            202,
            201,
            &t.sample,
            Some("/dev/pts/5"),
        );
        let tree = descendants(&t.proc_root, 200);
        assert_eq!(
            foreground_app_among(&t.proc_root, &tree, &registered, me, &lstat),
            None
        );
        // A Mon child of a foreground group it does not lead (pgrp != pid).
        entry(
            &t.proc_root,
            203,
            201,
            201,
            201,
            &t.sample,
            Some("/dev/pts/5"),
        );
        let tree = descendants(&t.proc_root, 200);
        assert_eq!(
            foreground_app_among(&t.proc_root, &tree, &registered, me, &lstat),
            None
        );
        // A bare VT: the processes on /dev/tty3.
        entry(&t.proc_root, 300, 1, 300, 301, shell, Some("/dev/tty3"));
        entry(
            &t.proc_root,
            301,
            300,
            301,
            301,
            &t.sample,
            Some("/dev/tty3"),
        );
        assert_eq!(on_tty(&t.proc_root, Path::new("/dev/tty3")), [300, 301]);
        let vt = on_tty(&t.proc_root, Path::new("/dev/tty3"));
        assert_eq!(
            foreground_app_among(&t.proc_root, &vt, &registered, me, &lstat).as_deref(),
            Some("mon")
        );
        assert!(on_tty(&t.proc_root, Path::new("/dev/tty4")).is_empty());
        // A tmux pane: the pane process 400's tty is led by 401.
        entry(&t.proc_root, 400, 1, 400, 401, shell, None);
        entry(&t.proc_root, 401, 400, 401, 401, &t.sample, None);
        assert_eq!(
            pane_app(&t.proc_root, 400, &registered, me, &lstat).as_deref(),
            Some("mon")
        );
        entry(&t.proc_root, 402, 1, 402, 402, shell, None);
        assert_eq!(pane_app(&t.proc_root, 402, &registered, me, &lstat), None);
    }
    #[test]
    fn a_result_is_bound_to_its_probe_identity_and_expires() {
        let calls = std::cell::Cell::new(0);
        let a = AppProbe::Plain {
            target: ("0xa".into(), 1),
            pid: 1,
        };
        let b = AppProbe::Plain {
            target: ("0xb".into(), 2),
            pid: 2,
        };
        let hour = Duration::from_secs(3600);
        let mut cache = AppCache::default();
        let mon = |_: &AppProbe| {
            calls.set(calls.get() + 1);
            Some("mon".to_owned())
        };
        let none = |_: &AppProbe| {
            calls.set(calls.get() + 1);
            None
        };
        assert_eq!(cache.get(a.clone(), hour, mon).0.as_deref(), Some("mon"));
        assert_eq!(cache.get(a.clone(), hour, mon).0.as_deref(), Some("mon"));
        assert_eq!(
            calls.get(),
            1,
            "the same identity re-verifies at most every app_poll_ms"
        );
        assert_eq!(
            cache.get(b.clone(), hour, none).0,
            None,
            "a new focus verifies at once and never reuses another window's result"
        );
        assert_eq!(calls.get(), 2);
        cache.get(b, Duration::ZERO, none);
        assert_eq!(calls.get(), 3, "after app_poll_ms");
        assert_eq!(a.target(), Some(("0xa".into(), 1)));
        assert_eq!(
            AppProbe::Vt {
                tty: "/dev/tty3".into()
            }
            .target(),
            None
        );
        let mut snap = ContextSnapshot::new("terminal");
        snap.app = Some("mon".into());
        snap.app_at = Instant::now().checked_sub(Duration::from_millis(1500));
        assert_eq!(snap.fresh_app(1000), None, "older than context_stale_ms");
        snap.app_at = Some(Instant::now());
        assert_eq!(snap.fresh_app(1000), Some("mon"));
        // P8: a route's focus never carries the host window's app, and a foreign seat suspends it.
        snap.suspended = Some("another session is active".into());
        let routed = session_focus(&snap, true, None);
        assert!(routed.app.is_none() && routed.app_target.is_none() && routed.suspended.is_some());
    }
    fn controller_return_fixture() -> (tempfile::TempDir, crate::nested::Session, PathBuf) {
        let dir = tempfile::tempdir().unwrap();
        let pid = std::process::id();
        let start = crate::nested::start_time(Path::new("/proc"), pid).unwrap();
        let proc_root = dir.path().join("proc");
        std::fs::create_dir_all(proc_root.join(pid.to_string())).unwrap();
        std::fs::copy(
            format!("/proc/{pid}/stat"),
            proc_root.join(pid.to_string()).join("stat"),
        )
        .unwrap();
        let device = dir.path().join("event42");
        std::fs::write(&device, []).unwrap();
        let entry = dir.path().join("seat.json");
        std::fs::write(
            &entry,
            serde_json::json!({"seat":"owned","launcher_pid":pid,"launcher_start":start,
            "devices":[{"path":device,"kind":"controller","siblings":[]}]})
            .to_string(),
        )
        .unwrap();
        let session = crate::nested::Session {
            seat: "owned".into(),
            entry,
            launcher_pid: pid,
            launcher_start: start,
            runtime: dir.path().into(),
            instance: "fixture".into(),
            wayland_display: "fixture".into(),
            pad_state: dir.path().join("packet"),
            controllers: vec![device],
            control_socket: None,
        };
        (dir, session, proc_root)
    }

    fn controller_return_request(
        session: &crate::nested::Session,
        action: &str,
    ) -> serde_json::Value {
        serde_json::json!({"action":action,"nonce":"current-request","seat":session.seat,
            "launcher_pid":session.launcher_pid,"launcher_start":session.launcher_start,"device":session.controllers[0]})
    }

    fn controller_return_call(
        session: &crate::nested::Session,
        proc_root: &Path,
        known: &std::sync::Mutex<BTreeSet<PathBuf>>,
        released: &std::sync::Mutex<BTreeSet<PathBuf>>,
        request: serde_json::Value,
        release: impl FnMut(&Path) -> Result<Result<(), (Vec<u16>, Vec<u16>)>, String>,
    ) -> serde_json::Value {
        let (mut client, server) = std::os::unix::net::UnixStream::pair().unwrap();
        let writer = std::thread::spawn(move || {
            writeln!(client, "{request}").unwrap();
            read_bounded_control_json(&mut client, Duration::from_secs(2), 4096).unwrap()
        });
        let active = session.controllers.iter().cloned().collect();
        let response = handle_child_control_connection(
            server,
            session,
            proc_root,
            unsafe { libc::geteuid() },
            None,
            &active,
            known,
            released,
            release,
        );
        assert_eq!(writer.join().unwrap(), response);
        response
    }

    #[test]
    fn controller_return_release_and_rollback_use_current_lease_and_nonce() {
        let (_dir, session, proc_root) = controller_return_fixture();
        let known = std::sync::Mutex::new(session.controllers.iter().cloned().collect());
        let released = std::sync::Mutex::new(BTreeSet::new());
        let capabilities = controller_return_call(
            &session,
            &proc_root,
            &known,
            &released,
            controller_return_request(&session, "return_capabilities"),
            |_| panic!("capability probes never release devices"),
        );
        assert_eq!(capabilities["protocol_version"], 1);
        assert!(released.lock().unwrap().is_empty());
        assert!(known.lock().unwrap().contains(&session.controllers[0]));
        let reply = controller_return_call(
            &session,
            &proc_root,
            &known,
            &released,
            controller_return_request(&session, "release_device"),
            |_| Ok(Ok(())),
        );
        assert_eq!(reply["ok"], true);
        assert_eq!(reply["nonce"], "current-request");
        assert!(released.lock().unwrap().contains(&session.controllers[0]));
        assert!(known.lock().unwrap().is_empty());
        // A scan may have observed the temporary claim removal. Rollback reads the restored lease.
        let mut stale = session.clone();
        stale.controllers.clear();
        let reply = controller_return_call(
            &stale,
            &proc_root,
            &known,
            &released,
            controller_return_request(&session, "abort_release"),
            |_| panic!("rollback must not release again"),
        );
        assert_eq!(reply["ok"], true);
        assert_eq!(reply["launcher_pid"], session.launcher_pid);
        assert_eq!(reply["launcher_start"], session.launcher_start);
        assert_eq!(reply["reacquire_armed"], true);
        assert!(released.lock().unwrap().is_empty());
    }

    #[test]
    fn controller_return_refuses_bad_nonce_foreign_paths_and_held_controls() {
        let (dir, session, proc_root) = controller_return_fixture();
        let known = std::sync::Mutex::new(session.controllers.iter().cloned().collect());
        let released = std::sync::Mutex::new(BTreeSet::new());
        for field in ["nonce", "seat", "device"] {
            let mut request = controller_return_request(&session, "release_device");
            request[field] = match field {
                "nonce" => serde_json::json!(""),
                "seat" => serde_json::json!("foreign"),
                _ => serde_json::json!(dir.path().join("foreign/event42")),
            };
            let reply =
                controller_return_call(&session, &proc_root, &known, &released, request, |_| {
                    panic!("invalid requests must not query or release a device")
                });
            assert_eq!(reply["ok"], false, "{field}");
            assert!(released.lock().unwrap().is_empty());
            assert!(known.lock().unwrap().contains(&session.controllers[0]));
        }
        let held = controller_return_call(
            &session,
            &proc_root,
            &known,
            &released,
            controller_return_request(&session, "release_device"),
            |_| Ok(Err((vec![304], vec![0, 2]))),
        );
        assert_eq!(held["reason"], "controls_held");
        assert_eq!(held["deferred"], true);
        let failed = controller_return_call(
            &session,
            &proc_root,
            &known,
            &released,
            controller_return_request(&session, "release_device"),
            |_| Err("ioctl failed".into()),
        );
        assert_eq!(failed["reason"], "ioctl_failed");
        assert!(released.lock().unwrap().is_empty());
    }

    #[test]
    fn controller_return_rearms_only_after_authoritative_neutral_snapshot() {
        let (global, legacy) = crate::config::tests::fixture();
        let mut router = Router::new(global, legacy, BTreeMap::new());
        let moving = crate::router::Axis {
            min: -100,
            max: 100,
            value: 80,
        };
        let neutral = crate::router::Axis { value: 0, ..moving };
        let pressed = crate::router::Axis {
            min: 0,
            max: 255,
            value: 200,
        };
        assert!(
            physical_controls_neutral(&BTreeSet::from([304]), &BTreeMap::new(), 0.2, 0.5).is_err()
        );
        assert!(physical_controls_neutral(
            &BTreeSet::new(),
            &BTreeMap::from([(0, moving), (2, pressed)]),
            0.2,
            0.5
        )
        .is_err());
        let mut rearming = true;
        assert!(step_neutral_rearming(
            &mut rearming,
            &mut router,
            Err(std::io::Error::other("ioctl")),
            0.2,
            0.5
        ));
        assert!(step_neutral_rearming(
            &mut rearming,
            &mut router,
            Ok((BTreeSet::new(), BTreeMap::from([(0, moving)]))),
            0.2,
            0.5
        ));
        router.axes.insert(0, moving);
        assert!(!step_neutral_rearming(
            &mut rearming,
            &mut router,
            Ok((BTreeSet::new(), BTreeMap::from([(0, neutral)]))),
            0.2,
            0.5
        ));
        assert!(
            !rearming,
            "a fresh neutral ioctl snapshot replaces stale cached axis state"
        );
        assert_eq!(router.axes[&0].value, 0);
    }
}

#[cfg(test)]
mod login_handoff_contract {
    use super::*;
    use crate::router::{Event, EV_KEY};
    fn witness(uid: u32, client: u32) -> crate::tmux_handoff::LoginWitness {
        crate::tmux_handoff::LoginWitness {
            uid,
            session_id: format!("c{client}"),
            leader: client + 100,
            leader_start: client as u64 + 200,
            client: TmuxClient {
                pid: client,
                start: client as u64 + 300,
                instance: 0,
                socket_dev: 0,
                socket_ino: 0,
                tty: format!("/dev/pts/{client}"),
                socket: format!("/run/user/{uid}/tmux/mios-desktop").into(),
            },
            client_start: client as u64 + 300,
        }
    }
    #[test]
    fn registered_ssh_follows_its_user_after_local_logout_without_touching_another_users_target() {
        let a = witness(1000, 10);
        let b = witness(1001, 20);
        let mut user_a = LoginSelection {
            generation: 3,
            candidates: vec![a.clone()],
            ..Default::default()
        };
        let mut user_b = LoginSelection {
            generation: 7,
            candidates: vec![b.clone()],
            ..Default::default()
        };
        let b_selected = user_b.reconcile(false, |_| true).unwrap();
        assert!(
            user_a.reconcile(true, |_| true).is_none(),
            "active local login retains its target until an explicit handoff or logout"
        );
        let a_selected = user_a.reconcile(false, |_| true).unwrap();
        assert_eq!(a_selected.witness, a);
        assert_eq!(
            a_selected.generation, 4,
            "greetd at seat0 does not revoke an authenticated SSH target"
        );
        assert_eq!(user_b.remote.as_ref().unwrap().witness, b_selected.witness);
        assert_eq!(
            user_b.generation, b_selected.generation,
            "A's handoff never changes B's generation"
        );
        assert_eq!(
            user_a.reconcile(false, |_| true).unwrap().generation,
            4,
            "unchanged focus observations coalesce"
        );
        assert!(
            user_a.reconcile(false, |_| false).is_none(),
            "disconnect releases only the expired login lease"
        );
        assert_eq!(user_a.generation, 5);
        assert_eq!(user_b.remote.as_ref().unwrap().witness, b);
        assert_eq!(user_b.generation, b_selected.generation);
    }
    #[test]
    fn exact_generation_detach_never_reselects_the_released_login_or_removes_a_newer_one() {
        let older = witness(1000, 10);
        let current = witness(1000, 11);
        let mut state = LoginSelection {
            generation: 4,
            candidates: vec![older.clone(), current.clone()],
            remote: Some(AuthorizedLogin {
                generation: 4,
                witness: current.clone(),
            }),
            local: None,
        };
        assert!(state.detach(3).is_err());
        assert_eq!(state.remote.as_ref().unwrap().witness, current);
        assert_eq!(state.detach(4).unwrap(), 5);
        assert!(state.candidates.iter().all(|w| w.client != current.client));
        assert_eq!(
            state.reconcile(false, |_| true).unwrap().witness,
            older,
            "only another still-authorized login can be a fallback"
        );
        let next = state.generation;
        assert!(state.detach(4).is_err());
        assert_eq!(state.generation, next);
    }
    #[test]
    fn shared_client_pointer_keeps_each_controllers_hold_until_its_own_release_or_disconnect() {
        let a = Path::new("/dev/input/mios-fixture-a");
        let b = Path::new("/dev/input/mios-fixture-b");
        let down = Event::new(EV_KEY, 0x110, 1);
        let up = Event::new(EV_KEY, 0x110, 0);
        let mut held = PointerHolds::default();
        assert_eq!(held.project(a, &[down], true), vec![down]);
        assert!(
            held.project(b, &[down], true).is_empty(),
            "one native client lease receives one aggregate press"
        );
        assert!(
            held.project(a, &[up], true).is_empty(),
            "A cannot release B's hold"
        );
        assert_eq!(held.project(b, &[up], true), vec![up]);
        assert_eq!(held.project(a, &[down], true), vec![down]);
        assert!(held.project(b, &[down], true).is_empty());
        assert!(
            !held.retain(|p| p != a),
            "disconnect A preserves B's active hold"
        );
        assert!(
            held.retain(|_| false),
            "final disconnect requests native cancellation, never a synthetic up"
        );
    }
    #[test]
    fn native_pointer_cancel_does_not_complete_chrome_click_or_replay_another_controllers_held_press(
    ) {
        let a = Path::new("/dev/input/mios-fixture-a");
        let b = Path::new("/dev/input/mios-fixture-b");
        let left = Event::new(EV_KEY, 0x110, 1);
        let right = Event::new(EV_KEY, 0x111, 1);
        let up = Event::new(EV_KEY, 0x111, 0);
        let mut held = PointerHolds::default();
        assert_eq!(held.project(a, &[left], true), vec![left]);
        assert_eq!(held.project(b, &[right], true), vec![right]);
        assert!(held.retain(|p| p != a));
        held.cancel(); // Runtime queues native hide, then show if B still requests the cursor.
        assert!(
            held.project(b, &[right], true).is_empty(),
            "B's pre-cancel hold is not replayed"
        );
        assert!(
            held.project(b, &[up], true).is_empty(),
            "no chrome-release action after cancellation"
        );
        assert_eq!(
            held.project(b, &[right], true),
            vec![right],
            "fresh post-neutral press works"
        );
        held.cancel();
        assert!(
            held.project(b, &[up], false).is_empty(),
            "release while hidden clears the rearm barrier"
        );
        assert_eq!(held.project(b, &[right], true), vec![right]);
    }
    #[test]
    fn source_cursor_exit_cancels_before_its_synthetic_up_while_another_cursor_stays_active() {
        let a = Path::new("/dev/input/mios-fixture-a");
        let b = Path::new("/dev/input/mios-fixture-b");
        let down = Event::new(EV_KEY, 0x110, 1);
        let up = Event::new(EV_KEY, 0x110, 0);
        let mut held = PointerHolds::default();
        assert_eq!(held.project(a, &[down], true), vec![down]);
        assert!(
            !held.retire_source(b),
            "another cursor with no hold needs no cancellation"
        );
        assert!(
            held.retire_source(a),
            "native hide must be queued before show/projecting Router release outputs"
        );
        assert!(
            held.project(a, &[up], false).is_empty(),
            "synthetic exit-up cannot complete chrome click"
        );
        assert_eq!(
            held.project(b, &[down], true),
            vec![down],
            "remaining cursor accepts a fresh press"
        );
        assert_eq!(
            held.project(a, &[down], true),
            vec![],
            "aggregate press still has one consumer"
        );
    }
    #[test]
    fn queued_ownership_from_an_old_login_is_rejected_before_any_compositor_or_tmux_transaction() {
        let (cfg, _) = crate::config::tests::fixture();
        let selection: SharedLogin = Default::default();
        selection.lock().unwrap().generation = 2;
        let mut worker =
            OwnershipWorker::spawn(std::sync::Arc::new(std::sync::Mutex::new(cfg)), selection)
                .unwrap();
        worker.submit("on", None, vec![], None, 1).unwrap();
        let (_, _, result) = worker.results.recv_timeout(Duration::from_secs(1)).unwrap();
        assert_eq!(
            result.err().as_deref(),
            Some("ownership request expired"),
            "the generation gate runs before external IPC"
        );
    }
    #[test]
    fn login_generation_change_drains_holds_and_waits_for_complete_neutral_snapshot() {
        let (global, legacy) = crate::config::tests::fixture();
        let mut router = Router::new(global, legacy, BTreeMap::new());
        router.set_focus("app-capture", Some(("old-login".into(), 10)), false);
        let mut generation = 1;
        let mut rearming = false;
        router.cursor = true;
        assert!(router
            .event(Event::new(EV_KEY, 304, 1), 1)
            .app
            .contains(&Event::new(EV_KEY, 304, 1)));
        let neutral = begin_login_handoff(&mut router, &mut generation, 2, &mut rearming).unwrap();
        assert!(
            neutral.app.contains(&Event::new(EV_KEY, 304, 0)),
            "old held controls release before the target changes"
        );
        assert!(neutral
            .app
            .iter()
            .any(|e| e.kind == EV_SYN && e.code == SYN_REPORT));
        assert!(rearming && !router.cursor);
        let moving = crate::router::Axis {
            min: -100,
            max: 100,
            value: 80,
        };
        assert!(step_neutral_rearming(
            &mut rearming,
            &mut router,
            Ok((BTreeSet::from([304]), BTreeMap::from([(0, moving)]))),
            0.2,
            0.5
        ));
        assert!(
            rearming,
            "neither a held button nor a deflected stick may enter the next login"
        );
        assert!(!step_neutral_rearming(
            &mut rearming,
            &mut router,
            Ok((
                BTreeSet::new(),
                BTreeMap::from([(0, crate::router::Axis { value: 0, ..moving })])
            )),
            0.2,
            0.5
        ));
        assert!(!rearming);
        assert!(
            begin_login_handoff(&mut router, &mut generation, 2, &mut rearming).is_none(),
            "the same generation never resets live input again"
        );
    }
    #[test]
    fn cold_greeter_broker_answers_neutrally_without_opening_even_an_explicit_candidate() {
        use std::os::fd::{AsRawFd, FromRawFd, OwnedFd};
        let root = tempfile::tempdir().unwrap();
        let candidate = root.path().join("must-not-open");
        std::fs::write(&candidate, b"private admission sentinel").unwrap();
        // Observe actual file opens; a gate regression reaches Device::open
        // before its evdev ioctls can reject this ordinary private sentinel.
        let monitor = unsafe { libc::inotify_init1(libc::IN_NONBLOCK | libc::IN_CLOEXEC) };
        assert!(monitor >= 0);
        let monitor = unsafe { OwnedFd::from_raw_fd(monitor) };
        let name = std::ffi::CString::new(candidate.as_os_str().as_encoded_bytes()).unwrap();
        assert!(
            unsafe { libc::inotify_add_watch(monitor.as_raw_fd(), name.as_ptr(), libc::IN_OPEN) }
                >= 0
        );
        let (mut cfg, _) = crate::config::tests::fixture();
        cfg.controller.sessions.clear();
        cfg.controller.scan_ms = 20;
        let mut cold = ContextSnapshot::new("console");
        cold.suspended = Some("seat not read yet".into());
        let focus = std::sync::Arc::new(std::sync::Mutex::new(cold));
        let policy = std::sync::Arc::new(std::sync::Mutex::new(cfg));
        let selection: SharedLogin = Default::default();
        let endpoint = root.path().join("neutral.sock");
        let service = HandoffService::spawn_at(
            endpoint.clone(),
            selection.clone(),
            focus.clone(),
            policy.clone(),
        )
        .unwrap();
        let stop = std::sync::Arc::new(AtomicBool::new(false));
        let (_candidates, worker) = discovery_worker(
            vec![candidate],
            false,
            policy,
            Default::default(),
            stop.clone(),
            Default::default(),
            false,
            Default::default(),
            Default::default(),
            Some(PhysicalAdmission {
                focus,
                logind_state: root.path().join("no-logind"),
            }),
        )
        .unwrap();
        let mut client = UnixStream::connect(&endpoint).unwrap();
        client
            .set_read_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        client
            .set_write_timeout(Some(Duration::from_secs(2)))
            .unwrap();
        crate::tmux_handoff::write_reply(
            &mut client,
            &serde_json::json!({
                "version": 1, "op": "window", "mode": "off",
            }),
        )
        .unwrap();
        client.shutdown(std::net::Shutdown::Write).unwrap();
        let mut header = [0u8; 4];
        client.read_exact(&mut header).unwrap();
        let length = u32::from_be_bytes(header) as usize;
        assert!(
            (1..=8192).contains(&length),
            "reply length must satisfy the handoff frame bound"
        );
        let mut bytes = vec![0u8; length];
        client.read_exact(&mut bytes).unwrap();
        let reply: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        assert_eq!(reply["ok"], false);
        assert!(reply["error"]
            .as_str()
            .unwrap()
            .contains("no fresh authorized native target"));
        assert_eq!(
            selection.lock().unwrap().generation,
            0,
            "listener startup grants no login/device ownership"
        );
        std::thread::sleep(Duration::from_millis(45));
        stop.store(true, Ordering::Relaxed);
        worker.join().unwrap();
        let mut events = [0u8; 512];
        let count = unsafe {
            libc::read(
                monitor.as_raw_fd(),
                events.as_mut_ptr().cast(),
                events.len(),
            )
        };
        assert!(
            count < 0,
            "cold discovery must not open the sentinel device"
        );
        assert_eq!(
            std::io::Error::last_os_error().kind(),
            std::io::ErrorKind::WouldBlock
        );
        drop(service);
        assert!(!endpoint.exists());
    }

    fn admission_target(generation: u64) -> crate::tmux_capture::Target {
        crate::tmux_capture::Target {
            uid: 1000,
            generation: 1,
            login_generation: generation,
            socket: "/private/owned/mios-desktop".into(),
            socket_dev: 1,
            socket_ino: 2,
            server: 10,
            server_start: 20,
            client: 30,
            client_start: 40,
            client_instance: 50,
            tty: "/dev/pts/99".into(),
            session: "$1".into(),
            session_name: "private".into(),
            window: "@1".into(),
            pane: "%1".into(),
            pane_pid: 60,
            pane_start: 70,
            foreground: 60,
            foreground_start: 70,
            zoomed: false,
        }
    }

    #[test]
    fn physical_admission_requires_fresh_authority_and_keeps_local_uinput_checks_separate() {
        let (mut cfg, _) = crate::config::tests::fixture();
        cfg.controller.sessions.clear();
        cfg.controller.context_stale_ms = 10_000;
        let mut focus = ContextSnapshot::new("console");
        focus.suspended = Some("physical greeter owns the seat".into());
        assert!(
            physical_readiness(&cfg, &focus, || panic!("no probe before local focus")).is_some()
        );
        focus.remote = true;
        focus.suspended = None;
        focus.routing_generation = 3;
        assert!(
            physical_readiness(&cfg, &focus, || panic!("a remote boolean is not authority"))
                .is_some()
        );
        for wrong in [0, 2, 4] {
            focus.native_target = Some(admission_target(wrong));
            assert!(
                physical_readiness(&cfg, &focus, || panic!("no local sink probe for remote"))
                    .is_some()
            );
        }
        focus.native_target = Some(admission_target(3));
        assert!(physical_readiness(&cfg, &focus, || panic!(
            "authorized remote does not require host uinput"
        ))
        .is_none());
        focus.sampled = Instant::now() - Duration::from_millis(cfg.controller.context_stale_ms + 1);
        assert!(physical_readiness(&cfg, &focus, || None).is_some());
        focus.sampled = Instant::now();
        focus.suspended = Some("authenticated target unavailable".into());
        assert!(physical_readiness(&cfg, &focus, || None).is_some());
        focus.remote = false;
        focus.suspended = None;
        assert_eq!(
            physical_readiness(&cfg, &focus, || Some("no /dev/uinput access".into())).as_deref(),
            Some("no /dev/uinput access")
        );
        assert_eq!(
            physical_readiness(&cfg, &focus, || Some(
                "physical greeter owns the seat".into()
            ))
            .as_deref(),
            Some("physical greeter owns the seat")
        );
        assert!(physical_readiness(&cfg, &focus, || None).is_none());
        assert!(retry_device_directory(&std::io::Error::from(
            std::io::ErrorKind::NotFound
        )));
        assert!(retry_device_directory(&std::io::Error::from(
            std::io::ErrorKind::PermissionDenied
        )));
        assert!(!retry_device_directory(&std::io::Error::from(
            std::io::ErrorKind::InvalidData
        )));
        assert!(!retry_device_directory(&std::io::Error::from(
            std::io::ErrorKind::Other
        )));
        cfg.controller.sessions.push(crate::sessions::SessionRoute {
            name: "private".into(),
            controllers: vec!["/private/device".into()],
            socket: "/private/socket".into(),
            session: "private".into(),
            pane: "%1".into(),
            ssh_host: None,
        });
        focus.suspended = Some("another user owns the seat".into());
        assert!(physical_readiness(&cfg, &focus, || panic!(
            "explicit routes keep their own policy"
        ))
        .is_none());
        assert_eq!(
            session_focus(&focus, true, None).suspended.as_deref(),
            Some("another user owns the seat"),
            "an explicit route never overrides a foreign active seat"
        );
    }

    #[test]
    fn remote_to_local_return_uses_existing_neutral_drain_and_rearm() {
        let (global, legacy) = crate::config::tests::fixture();
        let mut router = Router::new(global, legacy, BTreeMap::new());
        router.set_focus("app-capture", Some(("remote-login".into(), 10)), false);
        let mut generation = 2;
        let mut rearming = false;
        assert!(router
            .event(Event::new(EV_KEY, 304, 1), 1)
            .app
            .contains(&Event::new(EV_KEY, 304, 1)));
        let neutral = begin_login_handoff(&mut router, &mut generation, 3, &mut rearming).unwrap();
        assert!(neutral.app.contains(&Event::new(EV_KEY, 304, 0)));
        assert!(rearming);
        assert!(step_neutral_rearming(
            &mut rearming,
            &mut router,
            Ok((BTreeSet::from([304]), BTreeMap::new())),
            0.2,
            0.5
        ));
        assert!(!step_neutral_rearming(
            &mut rearming,
            &mut router,
            Ok((BTreeSet::new(), BTreeMap::new())),
            0.2,
            0.5
        ));
        assert!(!rearming);
        assert!(begin_login_handoff(&mut router, &mut generation, 3, &mut rearming).is_none());
    }
}

#[cfg(test)]
mod acceptance_observer {
    include!("mios-controller-acceptance-observer.rs");
}
