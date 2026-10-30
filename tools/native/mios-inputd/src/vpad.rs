// AI-hint: The agent virtual controller (I4): a per-session uinput gamepad held by one `mios-inputd vpad hold` process and driven over a private control socket with eased analog ramps (sticks/triggers never jump), button taps, the HAT d-pad, release-to-rest, and assignment to a nested session (it then runs that session's `bridge --nested <seat>`).
// AI-related: usr/share/mios/mios.toml [keybindings.global.controller.agent_pad] [desktop.nested.devices] [verbs.virtual_controller], tools/native/mios-inputd/src/nested.rs, tools/native/mios-gen/src/nested_devices.rs (virtual-pad), tests/test-inputd-platform.py, tests/test-inputd-vpad-live.py
// AI-functions: Ease::at, Channel::step, Model::command, Model::tick, hold, create, request, list
use crate::config::{button_code, AgentPadConfig, GlobalConfig, EASES};
use crate::router::{Event, EV_ABS, EV_KEY, EV_SYN, SYN_REPORT};
use serde_json::{json, Value as Json};
use std::collections::{BTreeSet, VecDeque};
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

/// Easing curves. `in-out` is the cubic in/out curve: zero velocity at both ends, so a stick neither jerks into
/// motion nor stops dead.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Ease {
    Linear,
    In,
    Out,
    InOut,
}
impl Ease {
    pub fn parse(s: &str) -> Result<Self, String> {
        Ok(match s {
            "linear" => Ease::Linear,
            "in" => Ease::In,
            "out" => Ease::Out,
            "in-out" => Ease::InOut,
            _ => return Err(format!("ease {s:?}: one of {}", EASES.join(", "))),
        })
    }
    pub fn name(self) -> &'static str {
        match self {
            Ease::Linear => "linear",
            Ease::In => "in",
            Ease::Out => "out",
            Ease::InOut => "in-out",
        }
    }
    /// Progress 0..=1 at time fraction t (0..=1); monotone, at(0)=0, at(1)=1.
    pub fn at(self, t: f64) -> f64 {
        let t = t.clamp(0.0, 1.0);
        match self {
            Ease::Linear => t,
            Ease::In => t * t * t,
            Ease::Out => 1.0 - (1.0 - t).powi(3),
            Ease::InOut => if t < 0.5 { 4.0 * t * t * t } else { 1.0 - (-2.0 * t + 2.0).powi(3) / 2.0 },
        }
    }
}

/// One ramp of a channel: from the value the channel has when the ramp starts, to `to`, over `ms`.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Segment {
    pub to: f64,
    pub ms: f64,
    pub ease: Ease,
}
/// One analog channel (a stick axis or a trigger). A new command replaces the queue and starts from the current
/// value, so an interrupted ramp continues without a jump.
#[derive(Clone, Debug, Default)]
pub struct Channel {
    pub value: f64,
    from: f64,
    elapsed: f64,
    queue: VecDeque<Segment>,
}
impl Channel {
    pub fn set(&mut self, segments: Vec<Segment>) {
        self.queue = segments.into();
        self.from = self.value;
        self.elapsed = 0.0;
    }
    pub fn busy(&self) -> bool {
        !self.queue.is_empty()
    }
    pub fn step(&mut self, mut dt_ms: f64) {
        while let Some(seg) = self.queue.front().copied() {
            let remain = seg.ms - self.elapsed;
            if dt_ms < remain {
                self.elapsed += dt_ms;
                self.value = self.from + (seg.to - self.from) * seg.ease.at(self.elapsed / seg.ms);
                return;
            }
            dt_ms -= remain;
            self.value = seg.to;
            self.from = seg.to;
            self.elapsed = 0.0;
            self.queue.pop_front();
        }
    }
}

/// The pad's evdev layout: a DualSense-like gamepad (positional face buttons, digital + analog triggers, HAT d-pad).
pub const KEYS: [u16; 13] = [0x130, 0x131, 0x133, 0x134, 0x136, 0x137, 0x138, 0x139, 0x13a, 0x13b, 0x13c, 0x13d, 0x13e];
/// lx, ly, rx, ry (-1..1) and lt, rt (0..1), in ABS code order 0, 1, 3, 4, 2, 5.
const AXES: [u16; 6] = [0, 1, 3, 4, 2, 5];

/// The pure pad state machine: commands in, evdev frames out. No device, no clock.
pub struct Model {
    pub axes: [Channel; 6],
    pub hat: (i32, i32),
    pub buttons: BTreeSet<u16>,
    taps: Vec<(u16, f64)>,
    hat_tap: Option<f64>,
    last: [i32; 6],
    last_hat: (i32, i32),
    last_buttons: BTreeSet<u16>,
    digital: [bool; 2],
    cfg: AgentPadConfig,
}
impl Model {
    pub fn new(cfg: AgentPadConfig) -> Self {
        Self { axes: Default::default(), hat: (0, 0), buttons: BTreeSet::new(), taps: vec![], hat_tap: None, last: [0; 6], last_hat: (0, 0), last_buttons: BTreeSet::new(), digital: [false; 2], cfg }
    }
    pub fn busy(&self) -> bool {
        self.axes.iter().any(Channel::busy) || !self.taps.is_empty() || self.hat_tap.is_some()
    }
    pub fn at_rest(&self) -> bool {
        !self.busy() && self.axes.iter().all(|c| c.value == 0.0) && self.buttons.is_empty() && self.hat == (0, 0)
    }
    fn raw(i: usize, v: f64) -> i32 {
        if i < 4 { (v.clamp(-1.0, 1.0) * 32767.0).round() as i32 } else { (v.clamp(0.0, 1.0) * 255.0).round() as i32 }
    }
    fn ms(&self, req: &Json, key: &str, default: u64) -> Result<f64, String> {
        let v = match req.get(key) {
            None | Some(Json::Null) => default,
            Some(v) => v.as_u64().ok_or_else(|| format!("{key}: milliseconds"))?,
        };
        Ok(v.clamp(self.cfg.min_ms, self.cfg.max_ms) as f64)
    }
    fn ease(&self, req: &Json) -> Result<Ease, String> {
        Ease::parse(req.get("ease").and_then(Json::as_str).unwrap_or(&self.cfg.ease))
    }
    fn number(req: &Json, key: &str, default: f64) -> Result<f64, String> {
        match req.get(key) {
            None | Some(Json::Null) => Ok(default),
            Some(v) => v.as_f64().filter(|f| f.is_finite()).ok_or_else(|| format!("{key}: a number")),
        }
    }
    /// Ramp to `targets` (channel, value), optionally hold, then ease back to rest over the same length.
    fn ramp(&mut self, req: &Json, targets: &[(usize, f64)]) -> Result<Json, String> {
        let ms = self.ms(req, "ms", self.cfg.ramp_ms)?;
        let ease = self.ease(req)?;
        let hold = match req.get("hold_ms") {
            None | Some(Json::Null) => None,
            Some(v) => Some(v.as_u64().ok_or("hold_ms: milliseconds")?.min(self.cfg.max_ms) as f64),
        };
        for (i, to) in targets {
            let mut segs = vec![Segment { to: *to, ms, ease }];
            if let Some(h) = hold {
                segs.push(Segment { to: *to, ms: h.max(1.0), ease: Ease::Linear });
                segs.push(Segment { to: 0.0, ms, ease });
            }
            self.axes[*i].set(segs);
        }
        let total = ms + hold.map_or(0.0, |h| h.max(1.0) + ms);
        Ok(json!({"ok": true, "ramp_ms": ms, "ease": ease.name(), "until_ms": total}))
    }
    pub fn command(&mut self, req: &Json) -> Result<Json, String> {
        let op = req.get("op").and_then(Json::as_str).ok_or("request needs op")?;
        match op {
            "status" => Ok(self.status()),
            "stick" => {
                let right = match req.get("stick").and_then(Json::as_str).unwrap_or("left") {
                    "left" => false,
                    "right" => true,
                    s => return Err(format!("stick {s:?}: left or right")),
                };
                let (mut x, mut y) = (Self::number(req, "x", 0.0)?, Self::number(req, "y", 0.0)?);
                // A stick lives in the unit disk: a (1, 1) request is the same direction at full deflection.
                let m = x.hypot(y);
                if m > 1.0 {
                    x /= m;
                    y /= m;
                }
                let base = if right { 2 } else { 0 };
                self.ramp(req, &[(base, x), (base + 1, y)])
            }
            "trigger" => {
                let i = match req.get("side").and_then(Json::as_str).unwrap_or("right") {
                    "left" => 4,
                    "right" => 5,
                    s => return Err(format!("trigger side {s:?}: left or right")),
                };
                let v = Self::number(req, "value", 1.0)?.clamp(0.0, 1.0);
                self.ramp(req, &[(i, v)])
            }
            "button" => {
                let name = req.get("button").and_then(Json::as_str).ok_or("button needs a name (BTN_SOUTH ...)")?;
                let code = button_code(name)?;
                if !KEYS.contains(&code) || [0x138, 0x139].contains(&code) {
                    return Err(format!("{name}: not a button of this pad (triggers are analog: op trigger)"));
                }
                match req.get("action").and_then(Json::as_str).unwrap_or("tap") {
                    "press" => { self.buttons.insert(code); }
                    "release" => { self.buttons.remove(&code); self.taps.retain(|(c, _)| *c != code); }
                    "tap" => {
                        let ms = req.get("ms").and_then(Json::as_u64).unwrap_or(self.cfg.tap_ms).clamp(10, self.cfg.max_ms) as f64;
                        self.buttons.insert(code);
                        self.taps.retain(|(c, _)| *c != code);
                        self.taps.push((code, ms));
                    }
                    a => return Err(format!("button action {a:?}: press, release or tap")),
                }
                Ok(json!({"ok": true, "button": name}))
            }
            "dpad" => {
                let dir = req.get("dir").and_then(Json::as_str).unwrap_or("center");
                let hat = match dir {
                    "left" => (-1, 0),
                    "right" => (1, 0),
                    "up" => (0, -1),
                    "down" => (0, 1),
                    "center" => (0, 0),
                    d => return Err(format!("dpad dir {d:?}: left, right, up, down or center")),
                };
                self.hat = hat;
                self.hat_tap = match req.get("action").and_then(Json::as_str).unwrap_or("tap") {
                    "tap" if hat != (0, 0) => Some(req.get("ms").and_then(Json::as_u64).unwrap_or(self.cfg.tap_ms).clamp(10, self.cfg.max_ms) as f64),
                    "tap" | "press" | "release" => None,
                    a => return Err(format!("dpad action {a:?}: press or tap")),
                };
                if req.get("action").and_then(Json::as_str) == Some("release") {
                    self.hat = (0, 0);
                }
                Ok(json!({"ok": true, "dpad": dir}))
            }
            "release" => {
                let ms = self.ms(req, "ms", self.cfg.ramp_ms)?;
                let ease = self.ease(req)?;
                for c in &mut self.axes {
                    c.set(vec![Segment { to: 0.0, ms, ease }]);
                }
                self.buttons.clear();
                self.taps.clear();
                self.hat = (0, 0);
                self.hat_tap = None;
                Ok(json!({"ok": true, "release_ms": ms}))
            }
            _ => Err(format!("unknown op {op}")),
        }
    }
    pub fn status(&self) -> Json {
        let v: Vec<f64> = self.axes.iter().map(|c| (c.value * 1e4).round() / 1e4).collect();
        json!({"left": [v[0], v[1]], "right": [v[2], v[3]], "triggers": [v[4], v[5]], "dpad": [self.hat.0, self.hat.1],
               "buttons": self.buttons.iter().map(|c| crate::nav::Input::Button(*c).name()).collect::<Vec<_>>(), "busy": self.busy()})
    }
    /// Advance `dt_ms` and return the evdev frame of what changed (empty when nothing did).
    pub fn tick(&mut self, dt_ms: f64) -> Vec<Event> {
        let mut out = vec![];
        for c in &mut self.axes {
            c.step(dt_ms);
        }
        let mut expired = vec![];
        for (code, left) in &mut self.taps {
            *left -= dt_ms;
            if *left <= 0.0 {
                expired.push(*code);
            }
        }
        self.taps.retain(|(_, left)| *left > 0.0);
        for c in expired {
            self.buttons.remove(&c);
        }
        if let Some(left) = &mut self.hat_tap {
            *left -= dt_ms;
            if *left <= 0.0 {
                self.hat = (0, 0);
                self.hat_tap = None;
            }
        }
        for (i, c) in self.axes.iter().enumerate() {
            let raw = Self::raw(i, c.value);
            if raw != self.last[i] {
                self.last[i] = raw;
                out.push(Event::new(EV_ABS, AXES[i], raw));
            }
        }
        // The trigger buttons follow the analog value with hysteresis, as a DualSense reports them.
        for (k, code) in [(0usize, 0x138u16), (1, 0x139)] {
            let v = self.axes[4 + k].value;
            let on = if self.digital[k] { v >= 0.4 } else { v >= 0.5 };
            if on != self.digital[k] {
                self.digital[k] = on;
                out.push(Event::new(EV_KEY, code, i32::from(on)));
            }
        }
        if self.hat != self.last_hat {
            if self.hat.0 != self.last_hat.0 { out.push(Event::new(EV_ABS, 16, self.hat.0)); }
            if self.hat.1 != self.last_hat.1 { out.push(Event::new(EV_ABS, 17, self.hat.1)); }
            self.last_hat = self.hat;
        }
        if self.buttons != self.last_buttons {
            for c in self.last_buttons.difference(&self.buttons) { out.push(Event::new(EV_KEY, *c, 0)); }
            for c in self.buttons.difference(&self.last_buttons) { out.push(Event::new(EV_KEY, *c, 1)); }
            self.last_buttons = self.buttons.clone();
        }
        if !out.is_empty() {
            out.push(Event::new(EV_SYN, SYN_REPORT, 0));
        }
        out
    }
}

/// A pad key: the seat label or the pad id (also the socket and state file name).
pub fn valid_key(k: &str) -> bool {
    !k.is_empty() && k.len() <= 32 && k.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-') && !k.starts_with('-')
}
pub fn dir(cfg: &AgentPadConfig) -> Result<PathBuf, String> {
    let rt = std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from).ok_or("XDG_RUNTIME_DIR missing")?;
    Ok(rt.join(&cfg.socket_dir))
}
fn private_dir(d: &Path) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::create_dir_all(d).map_err(|e| format!("{}: {e}", d.display()))?;
    std::fs::set_permissions(d, std::fs::Permissions::from_mode(0o700)).map_err(|e| e.to_string())
}
/// The evdev name a new pad gets: a seat's pad ([desktop.nested.devices].virtual_pad_name), an unseated agent pad
/// ([agent_pad].name), or on the fixture tier `mios-input-fixture-vpad-<key>` (only fixture bridges open that name).
pub fn pad_name(global: &GlobalConfig, key: &str, seat: bool, fixture: bool) -> String {
    if fixture {
        format!("mios-input-fixture-vpad-{key}")
    } else if seat {
        global.seat_pad_name.replace("{seat}", key)
    } else {
        global.controller.agent_pad.name.replace("{id}", key)
    }
}

static STOP: AtomicBool = AtomicBool::new(false);
extern "C" fn stop(_: libc::c_int) {
    STOP.store(true, Ordering::Relaxed);
}

/// The holder: creates the pad, serves its control socket, samples every ramp at rate_hz, and runs the assigned
/// nested session's bridge as its child. It exits on `destroy`, SIGTERM, or idle_exit_s at rest without a command;
/// the pad (and the bridge) go with it.
pub fn hold(global: GlobalConfig, root: Option<PathBuf>, key: &str, seat: bool, fixture: bool) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;
    let cfg = global.controller.agent_pad.clone();
    if !cfg.enabled {
        return Err("agent_pad.enabled is false".into());
    }
    if !valid_key(key) {
        return Err(format!("pad {key:?}: 1..32 of a-z, 0-9 and '-'"));
    }
    let d = dir(&cfg)?;
    private_dir(&d)?;
    let sock = d.join(format!("{key}.sock"));
    if UnixStream::connect(&sock).is_ok() {
        return Err(format!("pad {key} already exists ({})", sock.display()));
    }
    let _ = std::fs::remove_file(&sock);
    let name = pad_name(&global, key, seat, fixture);
    let (vendor, product) = cfg.usb()?;
    let mut pad = crate::linux::VirtualDevice::agent_pad(&name, vendor, product).map_err(|e| format!("uinput pad: {e}"))?;
    let mut path = None;
    for _ in 0..300 {
        if let Ok(p) = pad.event_path() {
            path = Some(p);
            break;
        }
        std::thread::sleep(Duration::from_millis(10));
    }
    let path = path.ok_or("the pad did not enumerate")?;
    let listener = UnixListener::bind(&sock).map_err(|e| format!("{}: {e}", sock.display()))?;
    std::fs::set_permissions(&sock, std::fs::Permissions::from_mode(0o600)).map_err(|e| e.to_string())?;
    listener.set_nonblocking(true).map_err(|e| e.to_string())?;
    let me = std::process::id();
    let state = json!({"pad": key, "name": name, "path": path, "seat": if seat { Some(key) } else { None }, "fixture": fixture,
                       "socket": sock, "pid": me, "start": crate::nested::start_time(Path::new("/proc"), me)});
    let state_file = d.join(format!("{key}.json"));
    std::fs::write(&state_file, format!("{state}\n")).map_err(|e| e.to_string())?;
    // SAFETY: the handlers only store to an atomic.
    unsafe {
        libc::signal(libc::SIGTERM, stop as *const () as libc::sighandler_t);
        libc::signal(libc::SIGINT, stop as *const () as libc::sighandler_t);
        libc::signal(libc::SIGHUP, libc::SIG_IGN);
    }
    println!("{state}");
    std::io::stdout().flush().map_err(|e| e.to_string())?;
    // The creator read the ready line; nothing else is written to its pipe.
    if let Ok(null) = std::fs::OpenOptions::new().write(true).open("/dev/null") {
        use std::os::fd::AsRawFd;
        // SAFETY: dup2 onto stdout with a valid descriptor; the File is dropped afterwards.
        unsafe { libc::dup2(null.as_raw_fd(), 1) };
    }
    let mut model = Model::new(cfg.clone());
    let mut waiters: Vec<(UnixStream, Instant)> = vec![];
    let mut bridge: Option<(String, std::process::Child)> = None;
    let mut last_cmd = Instant::now();
    let mut last_tick = Instant::now();
    let period = Duration::from_secs_f64(1.0 / f64::from(cfg.rate_hz));
    let result = (|| -> Result<(), String> {
        while !STOP.load(Ordering::Relaxed) {
            loop {
                match listener.accept() {
                    Ok((mut s, _)) => {
                        last_cmd = Instant::now();
                        let _ = s.set_nonblocking(false);
                        let _ = s.set_read_timeout(Some(Duration::from_millis(200)));
                        let mut line = String::new();
                        if BufReader::new(&s).take(65536).read_line(&mut line).is_err() {
                            continue;
                        }
                        let req: Json = match serde_json::from_str(line.trim()) {
                            Ok(v) => v,
                            Err(e) => { let _ = writeln!(s, "{}", json!({"ok": false, "error": e.to_string()})); continue; }
                        };
                        let op = req.get("op").and_then(Json::as_str).unwrap_or("");
                        let reply = match op {
                            "destroy" => { let _ = writeln!(s, "{}", json!({"ok": true, "destroyed": key})); return Ok(()); }
                            "wait" => { waiters.push((s, Instant::now() + Duration::from_millis(cfg.max_ms * 3 + 1000))); continue; }
                            "info" => Ok(json!({"ok": true, "pad": state, "assigned": bridge.as_ref().map(|(s, c)| json!({"seat": s, "bridge_pid": c.id()}))})),
                            "assign" => assign(&global, root.as_deref(), &path, fixture, req.get("seat").and_then(Json::as_str).unwrap_or(key), &d, key, &mut bridge),
                            "unassign" => { if let Some((_, mut c)) = bridge.take() { stop_child(&mut c); } Ok(json!({"ok": true, "assigned": null})) }
                            _ => model.command(&req),
                        };
                        let reply = reply.unwrap_or_else(|e| json!({"ok": false, "error": e}));
                        let _ = writeln!(s, "{reply}");
                    }
                    Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => break,
                    Err(e) => return Err(e.to_string()),
                }
            }
            let now = Instant::now();
            let dt = now.duration_since(last_tick).as_secs_f64().min(0.1) * 1000.0;
            last_tick = now;
            let frame = model.tick(dt);
            if !frame.is_empty() {
                pad.write(&frame).map_err(|e| e.to_string())?;
            }
            waiters.retain_mut(|(s, until)| {
                if !model.busy() || Instant::now() > *until {
                    let _ = writeln!(s, "{}", json!({"ok": true, "idle": !model.busy(), "state": model.status()}));
                    false
                } else {
                    true
                }
            });
            if let Some((seat, c)) = &mut bridge {
                if let Ok(Some(status)) = c.try_wait() {
                    eprintln!("{}", json!({"bridge": "exited", "seat": seat, "status": status.code()}));
                    bridge = None;
                }
            }
            if model.at_rest() && bridge.is_none() && last_cmd.elapsed() > Duration::from_secs(cfg.idle_exit_s) {
                eprintln!("{}", json!({"pad": key, "exit": "idle"}));
                return Ok(());
            }
            std::thread::sleep(if model.busy() || !waiters.is_empty() { period } else { period.max(Duration::from_millis(20)) });
        }
        Ok(())
    })();
    // Back to rest before the device goes, so a reader never keeps a deflected stick.
    let mut rest = Model::new(cfg);
    rest.last = model.last;
    rest.last_hat = model.last_hat;
    rest.last_buttons = model.last_buttons.clone();
    rest.digital = model.digital;
    let _ = pad.write(&rest.tick(0.0));
    if let Some((_, mut c)) = bridge.take() {
        stop_child(&mut c);
    }
    let _ = std::fs::remove_file(&sock);
    let _ = std::fs::remove_file(&state_file);
    drop(pad);
    result
}
fn stop_child(c: &mut std::process::Child) {
    // SAFETY: the PID is our own still-unreaped child.
    unsafe { libc::kill(c.id() as i32, libc::SIGTERM) };
    let end = Instant::now() + Duration::from_secs(3);
    while Instant::now() < end {
        if matches!(c.try_wait(), Ok(Some(_))) {
            return;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
    let _ = c.kill();
    let _ = c.wait();
}
/// Route the pad into a nested session: the session must hold this pad's node (launched with `--device virtual-pad`
/// or `--device name:<pad name>`); the holder then runs that session's `bridge --nested <seat>`.
#[allow(clippy::too_many_arguments)]
fn assign(global: &GlobalConfig, root: Option<&Path>, node: &Path, fixture: bool, seat: &str, d: &Path, key: &str, bridge: &mut Option<(String, std::process::Child)>) -> Result<Json, String> {
    let registry = std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from).ok_or("XDG_RUNTIME_DIR missing")?.join(&global.nested_registry);
    let s = crate::nested::session(&registry, Path::new("/proc"), seat)?;
    if !s.controllers.iter().any(|p| p == node) {
        return Err(format!("the session for seat {seat:?} does not hold this pad ({}); launch it with --seat {seat} --device virtual-pad", node.display()));
    }
    if let Some((_, mut c)) = bridge.take() {
        stop_child(&mut c);
    }
    let log = std::fs::OpenOptions::new().create(true).append(true).open(d.join(format!("{key}-bridge.log"))).map_err(|e| e.to_string())?;
    let mut cmd = std::process::Command::new(std::env::current_exe().map_err(|e| e.to_string())?);
    if let Some(r) = root {
        cmd.arg("--root").arg(r);
    }
    cmd.args(["bridge", "--nested", seat]);
    if fixture {
        cmd.arg("--fixture-only");
    }
    let child = cmd.stdin(std::process::Stdio::null()).stdout(log.try_clone().map_err(|e| e.to_string())?).stderr(log)
        .spawn().map_err(|e| format!("nested bridge: {e}"))?;
    let pid = child.id();
    *bridge = Some((seat.to_owned(), child));
    Ok(json!({"ok": true, "assigned": seat, "bridge_pid": pid, "session": s.entry, "pad_state": s.pad_state}))
}

/// Start a holder for `key` and return its ready line (the pad's name, node and socket).
pub fn create(global: &GlobalConfig, root: Option<&Path>, key: &str, seat: bool, fixture: bool) -> Result<Json, String> {
    use std::os::unix::process::CommandExt;
    let cfg = &global.controller.agent_pad;
    if !cfg.enabled {
        return Err("agent_pad.enabled is false".into());
    }
    if !valid_key(key) {
        return Err(format!("pad {key:?}: 1..32 of a-z, 0-9 and '-'"));
    }
    let d = dir(cfg)?;
    private_dir(&d)?;
    let log = std::fs::OpenOptions::new().create(true).write(true).truncate(true).open(d.join(format!("{key}.log"))).map_err(|e| e.to_string())?;
    let mut cmd = std::process::Command::new(std::env::current_exe().map_err(|e| e.to_string())?);
    if let Some(r) = root {
        cmd.arg("--root").arg(r);
    }
    cmd.args(["vpad", "hold", "--pad", key]);
    if seat {
        cmd.arg("--seat-pad");
    }
    if fixture {
        cmd.arg("--fixture");
    }
    // SAFETY: setsid only detaches the child from our session (the holder outlives this CLI call).
    unsafe {
        cmd.pre_exec(|| {
            libc::setsid();
            Ok(())
        });
    }
    let mut child = cmd.stdin(std::process::Stdio::null()).stdout(std::process::Stdio::piped()).stderr(log).spawn().map_err(|e| e.to_string())?;
    let stdout = child.stdout.take().ok_or("holder stdout missing")?;
    let (tx, rx) = std::sync::mpsc::channel();
    std::thread::spawn(move || {
        let mut line = String::new();
        let _ = BufReader::new(stdout).read_line(&mut line);
        let _ = tx.send(line);
    });
    match rx.recv_timeout(Duration::from_secs(10)) {
        Ok(line) if !line.trim().is_empty() => serde_json::from_str(line.trim()).map_err(|e| e.to_string()),
        _ => {
            let _ = child.kill();
            let _ = child.wait();
            let log = std::fs::read_to_string(d.join(format!("{key}.log"))).unwrap_or_default();
            Err(format!("pad {key} did not start: {}", log.trim()))
        }
    }
}

/// One request to a pad's holder; `wait` blocks until every ramp has finished.
pub fn request(global: &GlobalConfig, key: &str, req: &Json) -> Result<Json, String> {
    let cfg = &global.controller.agent_pad;
    if !valid_key(key) {
        return Err(format!("pad {key:?}: 1..32 of a-z, 0-9 and '-'"));
    }
    let sock = dir(cfg)?.join(format!("{key}.sock"));
    let mut s = UnixStream::connect(&sock).map_err(|e| format!("pad {key}: {e} ({}); create it with `mios-inputd vpad create`", sock.display()))?;
    let wait = req.get("op").and_then(Json::as_str) == Some("wait");
    s.set_read_timeout(Some(Duration::from_millis(if wait { cfg.max_ms * 3 + 3000 } else { 5000 }))).map_err(|e| e.to_string())?;
    writeln!(s, "{req}").map_err(|e| e.to_string())?;
    let mut line = String::new();
    BufReader::new(&s).take(1 << 20).read_line(&mut line).map_err(|e| e.to_string())?;
    let v: Json = serde_json::from_str(line.trim()).map_err(|e| format!("pad {key}: {e}"))?;
    if v.get("ok") == Some(&Json::Bool(false)) {
        return Err(v["error"].as_str().unwrap_or("pad request failed").to_owned());
    }
    Ok(v)
}

/// Live pads of this session (state files whose holder still runs).
pub fn list(global: &GlobalConfig) -> Result<Json, String> {
    let d = dir(&global.controller.agent_pad)?;
    let mut out = vec![];
    for e in std::fs::read_dir(&d).into_iter().flatten().flatten() {
        let p = e.path();
        if p.extension().is_none_or(|x| x != "json") {
            continue;
        }
        if let Some(j) = std::fs::read(&p).ok().and_then(|b| serde_json::from_slice::<Json>(&b).ok()) {
            let live = j["pid"].as_u64().and_then(|p| u32::try_from(p).ok()).and_then(|p| crate::nested::start_time(Path::new("/proc"), p)) == j["start"].as_u64();
            if live {
                out.push(j);
            }
        }
    }
    Ok(Json::Array(out))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn model() -> Model {
        Model::new(AgentPadConfig::default())
    }
    /// Sample a command at 250 Hz until idle; returns the ABS values of `code` per frame.
    fn run(m: &mut Model, code: u16, max_ms: f64) -> Vec<i32> {
        let mut out = vec![];
        let mut t = 0.0;
        while t <= max_ms {
            for e in m.tick(4.0) {
                if e.kind == EV_ABS && e.code == code {
                    out.push(e.value);
                }
            }
            t += 4.0;
            if !m.busy() {
                break;
            }
        }
        out
    }
    #[test]
    fn curves_are_monotone_and_eased_at_both_ends() {
        for e in [Ease::Linear, Ease::In, Ease::Out, Ease::InOut] {
            assert_eq!(e.at(0.0), 0.0);
            assert!((e.at(1.0) - 1.0).abs() < 1e-12);
            let mut prev = 0.0;
            for i in 1..=100 {
                let v = e.at(f64::from(i) / 100.0);
                assert!(v >= prev, "{e:?} monotone");
                prev = v;
            }
        }
        // in-out: slow start and slow finish (zero velocity), fastest in the middle.
        let d = |t: f64| (Ease::InOut.at(t + 0.01) - Ease::InOut.at(t)) / 0.01;
        assert!(d(0.0) < 0.01 && d(0.99) < 0.05 && d(0.495) > 2.5);
    }
    #[test]
    fn a_stick_ramp_never_jumps_and_reaches_its_target() {
        let mut m = model();
        m.command(&json!({"op": "stick", "stick": "left", "x": 0.0, "y": -1.0, "ms": 400, "ease": "in-out"})).unwrap();
        let ys = run(&mut m, 1, 1000.0);
        assert_eq!(*ys.last().unwrap(), -32767, "reaches full deflection");
        assert!(ys.len() >= 80, "sampled at 250 Hz over 400 ms: {}", ys.len());
        let steps: Vec<i32> = std::iter::once(0).chain(ys.iter().copied()).collect::<Vec<_>>().windows(2).map(|w| (w[1] - w[0]).abs()).collect();
        // Peak slope of the cubic in/out curve is 3x linear: <= 3 * 32767 * 4/400 per 4 ms sample (+rounding).
        assert!(steps.iter().all(|s| *s <= 990), "no jump: max step {}", steps.iter().max().unwrap());
        assert!(steps[0] < 50 && *steps.last().unwrap() < 50, "eased: small first and last steps {steps:?}");
    }
    #[test]
    fn interrupting_a_ramp_continues_from_where_the_stick_is() {
        let mut m = model();
        m.command(&json!({"op": "stick", "x": 1.0, "ms": 400})).unwrap();
        for _ in 0..50 { m.tick(4.0); }
        let mid = m.axes[0].value;
        assert!(mid > 0.2 && mid < 0.8, "{mid}");
        m.command(&json!({"op": "stick", "x": -1.0, "ms": 400})).unwrap();
        let f = m.tick(4.0);
        let v = f.iter().find(|e| e.code == 0).map(|e| e.value).unwrap();
        assert!((f64::from(v) / 32767.0 - mid).abs() < 0.02, "no teleport on a new command: {v} after {mid}");
    }
    #[test]
    fn ms_zero_is_stretched_to_the_floor_and_hold_eases_back() {
        let mut m = model();
        let r = m.command(&json!({"op": "stick", "stick": "right", "x": 1.0, "ms": 0, "hold_ms": 100})).unwrap();
        assert_eq!(r["ramp_ms"], 80.0, "min_ms floor: nothing ever teleports");
        let xs = run(&mut m, 3, 2000.0);
        assert!(xs.len() > 30 && xs.contains(&32767) && *xs.last().unwrap() == 0, "{xs:?}");
        assert!(m.at_rest());
    }
    #[test]
    fn triggers_report_digital_edges_and_buttons_tap() {
        let mut m = model();
        m.command(&json!({"op": "trigger", "side": "right", "value": 1.0, "ms": 100})).unwrap();
        let mut keys = vec![];
        for _ in 0..40 { keys.extend(m.tick(4.0).into_iter().filter(|e| e.kind == EV_KEY)); }
        assert_eq!(keys, vec![Event::new(EV_KEY, 0x139, 1)]);
        m.command(&json!({"op": "button", "button": "BTN_SOUTH", "action": "tap", "ms": 40})).unwrap();
        let mut keys = vec![];
        for _ in 0..20 { keys.extend(m.tick(4.0).into_iter().filter(|e| e.kind == EV_KEY)); }
        assert_eq!(keys, vec![Event::new(EV_KEY, 0x130, 1), Event::new(EV_KEY, 0x130, 0)]);
        assert!(m.command(&json!({"op": "button", "button": "BTN_TL2"})).is_err(), "triggers are analog");
        assert!(m.command(&json!({"op": "stick", "ease": "bounce"})).is_err());
        assert!(m.command(&json!({"op": "warp"})).is_err());
    }
    #[test]
    fn dpad_taps_and_release_returns_everything_to_rest() {
        let mut m = model();
        m.command(&json!({"op": "dpad", "dir": "left", "ms": 40})).unwrap();
        let f = m.tick(4.0);
        assert!(f.contains(&Event::new(EV_ABS, 16, -1)));
        for _ in 0..20 { m.tick(4.0); }
        assert_eq!(m.hat, (0, 0));
        m.command(&json!({"op": "stick", "x": 0.7, "y": 0.7})).unwrap();
        m.command(&json!({"op": "button", "button": "BTN_EAST", "action": "press"})).unwrap();
        for _ in 0..200 { m.tick(4.0); }
        let r = m.command(&json!({"op": "release", "ms": 200})).unwrap();
        assert_eq!(r["ok"], true);
        for _ in 0..100 { m.tick(4.0); }
        assert!(m.at_rest(), "{}", m.status());
        // (1, 1) is clamped to the unit disk, not past full deflection.
        let mut m = model();
        m.command(&json!({"op": "stick", "x": 1.0, "y": 1.0, "ms": 80})).unwrap();
        for _ in 0..40 { m.tick(4.0); }
        assert!((m.axes[0].value - std::f64::consts::FRAC_1_SQRT_2).abs() < 1e-9);
    }
    #[test]
    fn keys_and_names() {
        assert!(valid_key("agent-1") && !valid_key("") && !valid_key("A") && !valid_key("../x") && !valid_key("-x"));
        let v: toml::Value = include_str!("../../../../usr/share/mios/mios.toml").parse().unwrap();
        let (g, _) = GlobalConfig::from_value(&v).unwrap();
        assert_eq!(pad_name(&g, "p2", true, false), "MiOS Nested Pad p2", "the name mios-gen's virtual-pad spec waits for");
        assert_eq!(pad_name(&g, "agent", false, false), "MiOS Agent Pad agent");
        assert_eq!(pad_name(&g, "t1", true, true), "mios-input-fixture-vpad-t1", "a fixture pad no live bridge opens");
        assert!(crate::nested::seat_pad_name_matches(&g.seat_pad_name, &pad_name(&g, "p2", true, false)));
        assert!(!crate::nested::seat_pad_name_matches(&g.seat_pad_name, &pad_name(&g, "agent", false, false)), "an unseated pad stays routable");
    }
}
