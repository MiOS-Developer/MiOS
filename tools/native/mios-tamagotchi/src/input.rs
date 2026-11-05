// AI-hint: Keyboard and Inputd projected-key actions; releases never act and explicit repeat events only navigate.
// AI-related: game.rs, ui.rs, tools/native/mios-inputd/src/nav.rs
use crate::game::{Care, Direction};
use crossterm::event::{KeyCode, KeyEvent, KeyEventKind, KeyModifiers};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Action {
    Quit,
    Cancel,
    Pause,
    Previous,
    Next,
    Accept,
    Save,
    Reset,
    Confirm,
    Menu,
    Talk,
    Roster,
    Habitat,
    Training,
    Status,
    Graphics,
    ZoomIn,
    ZoomOut,
    ZoomReset,
    Camera,
    Help,
    Detail,
    Direction(Direction),
    Care(Care),
    Number(usize),
}
pub fn action(key: KeyEvent) -> Option<Action> {
    if key.kind == KeyEventKind::Release {
        return None;
    }
    if key.modifiers.contains(KeyModifiers::CONTROL) {
        return (key.code == KeyCode::Char('c')).then_some(Action::Quit);
    }
    if key.modifiers.intersects(
        KeyModifiers::ALT | KeyModifiers::SUPER | KeyModifiers::HYPER | KeyModifiers::META,
    ) {
        return None;
    }
    // Legends use capitals; both cases address the same unmodified shortcut.
    // Ctrl+C and other modified chords were handled before this normalization.
    let code = match key.code {
        KeyCode::Char(c) => KeyCode::Char(c.to_ascii_lowercase()),
        code => code,
    };
    let direction = match code {
        KeyCode::Up => Some(Direction::Up),
        KeyCode::Down => Some(Direction::Down),
        KeyCode::Left => Some(Direction::Left),
        KeyCode::Right => Some(Direction::Right),
        _ => None,
    };
    if let Some(d) = direction {
        return Some(Action::Direction(d));
    }
    match code {
        KeyCode::Tab => Some(Action::Next),
        KeyCode::BackTab => Some(Action::Previous),
        _ if key.kind != KeyEventKind::Press => None,
        KeyCode::Char(c @ '1'..='9') => Some(Action::Number((c as u8 - b'1') as usize)),
        KeyCode::Char('q') => Some(Action::Quit),
        KeyCode::Esc => Some(Action::Cancel),
        KeyCode::Enter | KeyCode::Char(' ') => Some(Action::Accept),
        KeyCode::Char('p') => Some(Action::Pause),
        KeyCode::Char('s') => Some(Action::Save),
        KeyCode::Char('n') => Some(Action::Reset),
        KeyCode::Char('y') => Some(Action::Confirm),
        KeyCode::Char('o') | KeyCode::F(1) => Some(Action::Menu),
        KeyCode::Char('i') => Some(Action::Status),
        KeyCode::Char('t') => Some(Action::Talk),
        KeyCode::Char('b') => Some(Action::Roster),
        KeyCode::Char('h') => Some(Action::Habitat),
        KeyCode::Char('l') => Some(Action::Training),
        KeyCode::Char('v') => Some(Action::Graphics),
        KeyCode::Char('+') | KeyCode::Char('=') => Some(Action::ZoomIn),
        KeyCode::Char('-') => Some(Action::ZoomOut),
        KeyCode::Char('0') => Some(Action::ZoomReset),
        KeyCode::Char('z') => Some(Action::Camera),
        KeyCode::Char('?') => Some(Action::Help),
        KeyCode::Char('d') => Some(Action::Detail),
        KeyCode::Char('f') => Some(Action::Care(Care::Feed)),
        KeyCode::Char('g') => Some(Action::Care(Care::Play)),
        KeyCode::Char('c') => Some(Action::Care(Care::Clean)),
        KeyCode::Char('r') => Some(Action::Care(Care::Rest)),
        KeyCode::Char('m') => Some(Action::Care(Care::Medicine)),
        _ => None,
    }
}

// Controller queries run off the render thread. Only these read-only native
// contracts are consumed: inputd context/map, gen nested-session --list-devices,
// tmux list-clients and the caller's own nested descriptor/registry.
use serde_json::Value as Json;
use std::{
    collections::{BTreeMap, BTreeSet},
    fs::{self, File},
    io::{Read, Seek, SeekFrom},
    os::unix::{
        fs::{MetadataExt, OpenOptionsExt},
        process::CommandExt,
    },
    path::{Path, PathBuf},
    process::{Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver, SyncSender},
        Arc,
    },
    time::{Duration, Instant},
};

#[derive(Clone, Debug)]
pub struct ControllerPrompts {
    pub family: String,
    pub navigate: String,
    pub accept: String,
    pub back: String,
    pub next: String,
    pub release: String,
    pub quit: Option<String>,
}

#[derive(Debug)]
struct Sample {
    // Age includes the entire query, never just delivery to the UI.
    started: Instant,
    prompts: Option<ControllerPrompts>,
    reason: &'static str,
}

pub struct ControllerMonitor {
    request: SyncSender<()>,
    result: Receiver<Sample>,
    stop: Arc<AtomicBool>,
    next_poll: Instant,
    interval: Duration,
    stale: Duration,
    cached: Option<Sample>,
    enabled: bool,
}

impl ControllerMonitor {
    pub fn new(config: &crate::config::Config) -> Self {
        let (request, requests) = mpsc::sync_channel(1);
        let (results, result) = mpsc::sync_channel(1);
        let stop = Arc::new(AtomicBool::new(false));
        let enabled = config.controller_prompts;
        if enabled {
            let stop = stop.clone();
            let timeout = Duration::from_millis(config.controller_timeout_ms);
            let max_bytes = config.controller_max_bytes;
            let identity = Identity::from_env();
            let _ = std::thread::Builder::new()
                .name("mios-controller-prompts".into())
                .spawn(move || {
                    while requests.recv().is_ok() {
                        if stop.load(Ordering::Relaxed) {
                            break;
                        }
                        let started = Instant::now();
                        let query = Query {
                            deadline: started + timeout * 6,
                            timeout,
                            max_bytes,
                            stop: &stop,
                        };
                        let detected = identity
                            .as_ref()
                            .ok_or("Inputd: no verified local session (remote/console).")
                            .and_then(|id| detect(id, &query));
                        let (prompts, reason) = match detected {
                            Ok(prompts) => (Some(prompts), "Inputd: assigned pad focused here."),
                            Err(reason) => (None, reason),
                        };
                        if results
                            .send(Sample {
                                started,
                                prompts,
                                reason,
                            })
                            .is_err()
                        {
                            break;
                        }
                    }
                });
        }
        Self {
            request,
            result,
            stop,
            next_poll: Instant::now(),
            interval: Duration::from_millis(config.controller_poll_ms),
            stale: Duration::from_millis(config.controller_stale_ms),
            cached: None,
            enabled,
        }
    }

    /// Returns the current usable prompts on every call; None means keyboard
    /// hints. Never waits for a command, channel, worker or filesystem operation.
    pub fn poll(&mut self) -> Option<ControllerPrompts> {
        if !self.enabled {
            return None;
        }
        while let Ok(sample) = self.result.try_recv() {
            self.cached = Some(sample);
        }
        let now = Instant::now();
        if now >= self.next_poll {
            let _ = self.request.try_send(());
            self.next_poll = now + self.interval;
        }
        self.cached
            .as_ref()
            .filter(|s| now.saturating_duration_since(s.started) < self.stale)
            .and_then(|s| s.prompts.clone())
    }

    /// Diagnostic only: never grants ownership or enables controller prompts.
    pub fn status(&self) -> &str {
        if !self.enabled {
            return "Inputd: controller hints disabled in settings.";
        }
        match &self.cached {
            None => "Inputd: checking local focus and assigned pad.",
            Some(sample) if sample.started.elapsed() >= self.stale => {
                "Inputd: focus/device evidence stale; keyboard active."
            }
            Some(sample) => sample.reason,
        }
    }
}

impl Drop for ControllerMonitor {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        // Deliberately do not join the worker from the render thread.
    }
}

struct Identity {
    runtime: PathBuf,
    seat: Option<String>,
    instance: Option<String>,
    tmux: Option<(PathBuf, String)>,
    pid: u32,
}
impl Identity {
    fn from_env() -> Option<Self> {
        let runtime = PathBuf::from(std::env::var_os("XDG_RUNTIME_DIR")?);
        if !runtime.is_absolute() {
            return None;
        }
        let tmux = match (std::env::var("TMUX"), std::env::var("TMUX_PANE")) {
            (Err(std::env::VarError::NotPresent), Err(std::env::VarError::NotPresent)) => None,
            (Ok(socket), Ok(pane)) => Some(parse_tmux(&socket, &pane)?),
            _ => return None,
        };
        Some(Self {
            runtime,
            seat: std::env::var("MIOS_NESTED_SEAT").ok(),
            instance: std::env::var("HYPRLAND_INSTANCE_SIGNATURE").ok(),
            tmux,
            pid: std::process::id(),
        })
    }
}

fn parse_tmux(value: &str, pane: &str) -> Option<(PathBuf, String)> {
    let mut parts = value.rsplitn(3, ',');
    parts.next()?.parse::<u32>().ok()?;
    let server = parts.next()?.parse::<u32>().ok()?;
    let path = PathBuf::from(parts.next()?);
    if server == 0
        || !path.is_absolute()
        || !pane.strip_prefix('%')?.bytes().all(|b| b.is_ascii_digit())
        || pane.len() < 2
    {
        return None;
    }
    Some((path, pane.into()))
}

struct Query<'a> {
    deadline: Instant,
    timeout: Duration,
    max_bytes: usize,
    stop: &'a AtomicBool,
}
impl Query<'_> {
    fn active(&self) -> bool {
        !self.stop.load(Ordering::Relaxed) && Instant::now() < self.deadline
    }
    fn command(&self, program: &str, args: &[&str], runtime: Option<&Path>) -> Option<Vec<u8>> {
        if !self.active() {
            return None;
        }
        // Regular-file capture avoids pipe-reader hangs when a descendant keeps
        // stdout open. Bounds apply before parsing and while the child runs.
        let mut output = tempfile::tempfile().ok()?;
        let mut command = Command::new(program);
        command
            .args(args)
            .stdin(Stdio::null())
            .stderr(Stdio::null())
            .stdout(Stdio::from(output.try_clone().ok()?))
            .process_group(0);
        if let Some(runtime) = runtime {
            command.env("XDG_RUNTIME_DIR", runtime);
        }
        let mut child = command.spawn().ok()?;
        let deadline = self.deadline.min(Instant::now() + self.timeout);
        let status = loop {
            if !self.active()
                || Instant::now() >= deadline
                || output
                    .metadata()
                    .map_or(true, |m| m.len() > self.max_bytes as u64)
            {
                kill_query(&mut child, self.timeout);
                return None;
            }
            match child.try_wait() {
                Ok(Some(status)) => break status,
                Ok(None) => std::thread::sleep(Duration::from_millis(2)),
                Err(_) => {
                    kill_query(&mut child, self.timeout);
                    return None;
                }
            }
        };
        // Also terminate descendants after a successful direct child exit.
        kill_group(child.id(), self.timeout);
        if !status.success() || !self.active() {
            return None;
        }
        output.seek(SeekFrom::Start(0)).ok()?;
        let mut bytes = Vec::new();
        output
            .take(self.max_bytes as u64 + 1)
            .read_to_end(&mut bytes)
            .ok()?;
        (bytes.len() <= self.max_bytes).then_some(bytes)
    }
    fn json(&self, program: &str, args: &[&str], runtime: Option<&Path>) -> Option<Json> {
        serde_json::from_slice(&self.command(program, args, runtime)?).ok()
    }
    fn file(&self, path: &Path) -> Option<Json> {
        if !self.active() {
            return None;
        }
        let mut bytes = Vec::new();
        let file = File::options()
            .read(true)
            .custom_flags(0x800)
            .open(path)
            .ok()?;
        if !file.metadata().ok()?.is_file() {
            return None;
        }
        file.take(self.max_bytes as u64 + 1)
            .read_to_end(&mut bytes)
            .ok()?;
        if bytes.len() > self.max_bytes || !self.active() {
            return None;
        }
        serde_json::from_slice(&bytes).ok()
    }
}

// The child's fresh process group confines cancellation to this query and its
// descendants. No controller node or desktop process is signalled.
fn kill_group(pid: u32, timeout: Duration) {
    // Use the native signal utility: this crate forbids unsafe code. The only
    // target is the fresh group we created for this one read-only query.
    let Ok(mut signal) = Command::new("kill")
        .args(["-KILL", "--", &format!("-{pid}")])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
    else {
        return;
    };
    let deadline = Instant::now() + timeout;
    loop {
        match signal.try_wait() {
            Ok(Some(_)) => return,
            Ok(None) if Instant::now() < deadline => std::thread::sleep(Duration::from_millis(2)),
            _ => {
                let _ = signal.kill();
                let _ = signal.wait();
                return;
            }
        }
    }
}
fn kill_query(child: &mut std::process::Child, timeout: Duration) {
    kill_group(child.id(), timeout);
    let _ = child.kill();
    let _ = child.wait();
}

struct Seat {
    parent: PathBuf,
    entry: Json,
}
fn own_seat(id: &Identity, query: &Query<'_>) -> Option<Option<Seat>> {
    let descriptor_path = id.runtime.join("session.json");
    if id.seat.is_none() && !descriptor_path.exists() {
        return Some(None);
    }
    let descriptor = query.file(&descriptor_path)?;
    let registry = PathBuf::from(descriptor["registry"].as_str()?);
    let entry = query.file(&registry)?;
    let parent = PathBuf::from(entry["parent_runtime"].as_str()?);
    let runtime = fs::canonicalize(&id.runtime).ok()?;
    let seat = entry["seat"].as_str()?;
    let uid = fs::metadata("/proc/self").ok()?.uid();
    if !parent.is_absolute()
        || !registry.is_absolute()
        || !registry.starts_with(&parent)
        || fs::metadata(&registry).ok()?.uid() != uid
        || fs::metadata(&descriptor_path).ok()?.uid() != uid
        || fs::metadata(&runtime).ok()?.uid() != uid
        || entry["state"] != "running"
        || descriptor["seat"].as_str()? != seat
        || id.seat.as_ref().is_some_and(|s| s != seat)
        || fs::canonicalize(entry["runtime"].as_str()?).ok()? != runtime
        || fs::canonicalize(descriptor["runtime"].as_str()?).ok()? != runtime
        || fs::canonicalize(entry["descriptor"].as_str()?).ok()?
            != fs::canonicalize(&descriptor_path).ok()?
        || id.instance.as_deref()? != entry["instance"].as_str()?
        || descriptor["instance"]["instance"] != entry["instance"]
        || descriptor["instance"]["pid"] != entry["compositor_pid"]
        || proc_stat(entry["launcher_pid"].as_u64()?.try_into().ok()?)?.1
            != entry["launcher_start"].as_u64()?
        || proc_stat(entry["compositor_pid"].as_u64()?.try_into().ok()?).is_none()
    {
        return None;
    }
    Some(Some(Seat { parent, entry }))
}

fn proc_stat(pid: u32) -> Option<(u32, u64)> {
    let stat = fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    parse_stat(&stat)
}
fn parse_stat(stat: &str) -> Option<(u32, u64)> {
    let fields: Vec<_> = stat.rsplit_once(')')?.1.split_whitespace().collect();
    Some((fields.get(1)?.parse().ok()?, fields.get(19)?.parse().ok()?))
}
fn descendant(mut pid: u32, ancestor: u32) -> bool {
    let mut visited = BTreeSet::new();
    while pid > 0 && visited.len() < 128 && visited.insert(pid) {
        if pid == ancestor {
            return true;
        }
        let Some((parent, _)) = proc_stat(pid) else {
            return false;
        };
        pid = parent;
    }
    false
}

fn focused_client(
    clients: &str,
    pane: &str,
    focused: u32,
    mut is_descendant: impl FnMut(u32, u32) -> bool,
) -> bool {
    clients.lines().any(|line| {
        let Some((pid, active_pane)) = line.split_once('|') else {
            return false;
        };
        active_pane == pane
            && pid
                .parse::<u32>()
                .ok()
                .is_some_and(|pid| pid > 0 && is_descendant(pid, focused))
    })
}

fn focus(id: &Identity, context: &Json, query: &Query<'_>) -> Option<bool> {
    let pid: u32 = context["pid"].as_u64()?.try_into().ok()?;
    if pid == 0 || context["address"].as_str()?.is_empty() {
        return None;
    }
    if let Some((socket, pane)) = &id.tmux {
        let bytes = query.command(
            "tmux",
            &[
                "-S",
                socket.to_str()?,
                "list-clients",
                "-F",
                "#{client_pid}|#{pane_id}",
            ],
            None,
        )?;
        let clients = std::str::from_utf8(&bytes).ok()?;
        focused_client(clients, pane, pid, descendant).then_some(true)
    } else {
        descendant(id.pid, pid).then_some(false)
    }
}

fn family(name: &str) -> &'static str {
    let name = name.to_ascii_lowercase();
    if ["sony", "dualsense", "dualshock", "playstation"]
        .iter()
        .any(|s| name.contains(s))
    {
        "Sony"
    } else if ["xbox", "microsoft", "x-input", "xinput"]
        .iter()
        .any(|s| name.contains(s))
    {
        "Xbox"
    } else if ["nintendo", "switch pro", "joy-con"]
        .iter()
        .any(|s| name.contains(s))
    {
        "Nintendo"
    } else {
        "Controller"
    }
}

fn connected(devices: &Json, seat: Option<&Seat>) -> Option<BTreeSet<&'static str>> {
    // Registry structure is session evidence; malformed individual discovery
    // rows are not. Reject an invalid registry, but do not let one bad or foreign
    // device hide a different, fully verified controller later in the list.
    let owned = if let Some(seat) = seat {
        let name = seat.entry["seat"].as_str()?;
        if name.is_empty() {
            return None;
        }
        Some((name, seat.entry["devices"].as_array()?))
    } else {
        None
    };
    let mut families = BTreeSet::new();
    for device in devices.as_array()? {
        if device["kind"] != "controller" {
            continue;
        }
        let (Some(assignment), Some(name), Some(path), Some(virtual_pad)) = (
            device.get("assigned_to"),
            device["name"].as_str(),
            device["path"].as_str(),
            device["virtual"].as_bool(),
        ) else {
            continue;
        };
        let Some(event) = path.strip_prefix("/dev/input/event") else {
            continue;
        };
        if name.is_empty() || event.is_empty() || !event.bytes().all(|b| b.is_ascii_digit()) {
            continue;
        }
        let eligible = if let Some((seat, devices)) = owned {
            assignment.as_str() == Some(seat)
                && devices.iter().any(|owned| {
                    owned["path"].as_str() == Some(path)
                        && owned["name"].as_str() == Some(name)
                        && owned["kind"] == "controller"
                        && owned["virtual"].as_bool() == Some(virtual_pad)
                        && (!virtual_pad || owned["spec"] == "virtual-pad")
                })
        } else {
            !virtual_pad && assignment.is_null()
        };
        if eligible {
            families.insert(family(name));
        }
    }
    (!families.is_empty()).then_some(families)
}

fn detect(id: &Identity, query: &Query<'_>) -> Result<ControllerPrompts, &'static str> {
    let seat = own_seat(id, query).ok_or("Inputd: nested ownership could not be verified.")?;
    // An empty owned-device list is conclusive. Never borrow a parent pad.
    if seat.as_ref().is_some_and(|s| {
        s.entry["devices"]
            .as_array()
            .is_some_and(|devices| !devices.iter().any(|d| d["kind"] == "controller"))
    }) {
        return Err("Inputd: no controller assigned to this nested session.");
    }
    let context = query
        .json("mios-inputd", &["context"], None)
        .ok_or("Inputd: local context unavailable (remote/console).")?;
    let tmux = focus(id, &context, query)
        .ok_or("Inputd: this terminal pane is not verifiably focused.")?;
    let map = query
        .json("mios-inputd", &["map"], None)
        .ok_or("Inputd: binding map unavailable.")?;
    let devices = query
        .json(
            "mios-gen",
            &["nested-session", "--list-devices"],
            Some(
                seat.as_ref()
                    .map_or(id.runtime.as_path(), |s| s.parent.as_path()),
            ),
        )
        .ok_or("Inputd: connected-device ownership unavailable.")?;
    let families = connected(&devices, seat.as_ref())
        .ok_or("Inputd: no connected controller assigned to this session.")?;
    let prompts = resolve_connected_prompts(&map, &context, tmux, &families)
        .ok_or("Inputd: current context has no usable terminal bindings.")?;
    // Refuse a focus transition during the multi-command sample.
    let after = query
        .json("mios-inputd", &["context"], None)
        .ok_or("Inputd: focus recheck unavailable.")?;
    if context != after || focus(id, &after, query) != Some(tmux) || !query.active() {
        return Err("Inputd: focus changed; keyboard active.");
    }
    Ok(prompts)
}

type Bindings = BTreeMap<String, Json>;

fn inherited(nav: &Json, name: &str, seen: &mut BTreeSet<String>) -> Option<Bindings> {
    if seen.len() >= 32 || !seen.insert(name.into()) {
        return None;
    }
    let profile = nav.get(name)?.as_object()?;
    let mut bindings = if let Some(parent) = profile.get("inherits") {
        inherited(nav, parent.as_str()?, seen)?
    } else {
        BTreeMap::new()
    };
    for (input, value) in profile {
        if input == "inherits" {
            continue;
        }
        if input != "TOGGLE_CHORD" {
            button_label(input, "Controller")?;
        }
        match value {
            Json::String(_) => {}
            Json::Array(a) if a.len() == 2 && a.iter().all(Json::is_string) => {}
            _ => return None,
        }
        bindings.insert(input.clone(), value.clone());
    }
    Some(bindings)
}

fn button_label(input: &str, family: &str) -> Option<String> {
    let face = match input {
        "BTN_SOUTH" => ["Cross", "A", "B", "South"],
        "BTN_EAST" => ["Circle", "B", "A", "East"],
        "BTN_WEST" => ["Square", "X", "Y", "West"],
        "BTN_NORTH" => ["Triangle", "Y", "X", "North"],
        _ => {
            return Some(
                match input {
                    "BTN_THUMBL" => "L3",
                    "BTN_THUMBR" => "R3",
                    "BTN_TL" => match family {
                        "Sony" => "L1",
                        "Xbox" => "LB",
                        _ => "L",
                    },
                    "BTN_TR" => match family {
                        "Sony" => "R1",
                        "Xbox" => "RB",
                        _ => "R",
                    },
                    "BTN_TL2" => match family {
                        "Sony" => "L2",
                        "Nintendo" => "ZL",
                        _ => "LT",
                    },
                    "BTN_TR2" => match family {
                        "Sony" => "R2",
                        "Nintendo" => "ZR",
                        _ => "RT",
                    },
                    "BTN_START" => "Start",
                    "BTN_SELECT" => "Select",
                    "BTN_MODE" => "Guide",
                    "BTN_DPAD_UP" => "D-pad Up",
                    "BTN_DPAD_DOWN" => "D-pad Down",
                    "BTN_DPAD_LEFT" => "D-pad Left",
                    "BTN_DPAD_RIGHT" => "D-pad Right",
                    "LS_UP" => "LS Up",
                    "LS_DOWN" => "LS Down",
                    "LS_LEFT" => "LS Left",
                    "LS_RIGHT" => "LS Right",
                    "RS_UP" => "RS Up",
                    "RS_DOWN" => "RS Down",
                    "RS_LEFT" => "RS Left",
                    "RS_RIGHT" => "RS Right",
                    "TOGGLE_CHORD" => return None,
                    _ => return None,
                }
                .into(),
            );
        }
    };
    Some(
        face[match family {
            "Sony" => 0,
            "Xbox" => 1,
            "Nintendo" => 2,
            _ => 3,
        }]
        .into(),
    )
}

fn binding_label(bindings: &Bindings, target: &str, family: &str) -> Option<String> {
    // Stable physical preference, while always inspecting the resolved action.
    // A remapped East/West is never printed as South merely due to its family.
    const ORDER: [&str; 24] = [
        "BTN_SOUTH",
        "BTN_EAST",
        "BTN_WEST",
        "BTN_NORTH",
        "BTN_DPAD_UP",
        "BTN_DPAD_DOWN",
        "BTN_DPAD_LEFT",
        "BTN_DPAD_RIGHT",
        "BTN_TL",
        "BTN_TR",
        "BTN_TL2",
        "BTN_TR2",
        "BTN_START",
        "BTN_SELECT",
        "BTN_MODE",
        "BTN_THUMBL",
        "BTN_THUMBR",
        "LS_UP",
        "LS_DOWN",
        "LS_LEFT",
        "LS_RIGHT",
        "RS_UP",
        "RS_DOWN",
        "RS_LEFT",
    ];
    for input in ORDER.into_iter().chain(std::iter::once("RS_RIGHT")) {
        let Some(value) = bindings.get(input) else {
            continue;
        };
        let label = button_label(input, family)?;
        if value.as_str() == Some(target) {
            return Some(label);
        }
        if let Some(actions) = value.as_array() {
            if actions.first()?.as_str() == Some(target) {
                return Some(format!("Tap {label}"));
            }
            if actions.get(1)?.as_str() == Some(target) {
                return Some(format!("Hold {label}"));
            }
        }
    }
    None
}

fn resolve_prompts(
    map: &Json,
    context: &Json,
    tmux: bool,
    family: &str,
) -> Option<ControllerPrompts> {
    let global = &map["global"];
    let controller = &global["controller"];
    let nav = &controller["nav"];
    let released = context["released"].as_bool()?;
    let room_capture = context["room_capture"].as_bool()?;
    if global["enabled"] != true || controller["enabled"] != true || nav["enabled"] != true {
        return None;
    }
    let name = context["context"].as_str()?;
    let context_name = match (name, context["controller_owner"].as_str()?) {
        ("terminal", "mios") if !room_capture => "terminal",
        ("app-capture", "app")
            if !released
                && controller["capture_navigation_contexts"]
                    .as_array()?
                    .iter()
                    .any(|v| v == "terminal") =>
        {
            "terminal"
        }
        _ => return None,
    };
    let profile = nav["contexts"][context_name].as_str()?;
    if profile == "vpad" {
        return None;
    }
    let selected = if nav.get(profile).is_some_and(Json::is_object) {
        profile.to_owned()
    } else {
        format!("{profile}_{}", if tmux { "tmux" } else { "plain" })
    };
    let mut seen = BTreeSet::new();
    let bindings = inherited(nav, &selected, &mut seen)?;
    let arrows = [
        ("BTN_DPAD_UP", "key:UP"),
        ("BTN_DPAD_DOWN", "key:DOWN"),
        ("BTN_DPAD_LEFT", "key:LEFT"),
        ("BTN_DPAD_RIGHT", "key:RIGHT"),
    ];
    let navigate = if arrows
        .iter()
        .all(|(input, target)| bindings.get(*input).and_then(Json::as_str) == Some(*target))
    {
        "D-pad".into()
    } else {
        arrows
            .iter()
            .map(|(_, target)| binding_label(&bindings, target, family))
            .collect::<Option<Vec<_>>>()?
            .join(" / ")
    };
    let chord = map["hypr3d"]["toggle_chord"].as_array()?;
    let unique: BTreeSet<_> = chord.iter().filter_map(Json::as_str).collect();
    if chord.len() < 2 || chord.len() != unique.len() {
        return None;
    }
    let release = chord
        .iter()
        .map(|b| {
            let input = b.as_str()?;
            // The universal release is a real-button chord, never a stick flick.
            input.strip_prefix("BTN_")?;
            button_label(input, family)
        })
        .collect::<Option<Vec<_>>>()?
        .join("+");
    Some(ControllerPrompts {
        family: family.into(),
        navigate,
        accept: binding_label(&bindings, "key:ENTER", family)?,
        back: binding_label(&bindings, "key:ESC", family)?,
        next: binding_label(&bindings, "key:TAB", family)?,
        release: format!("Hold {release}"),
        quit: binding_label(&bindings, "key:CTRL+c", family),
    })
}

// There is no active-device evidence in discovery. Resolve the actual map for
// every eligible family and merge per action, never by choosing a preferred pad.
fn resolve_connected_prompts(
    map: &Json,
    context: &Json,
    tmux: bool,
    families: &BTreeSet<&str>,
) -> Option<ControllerPrompts> {
    let prompts = families
        .iter()
        .map(|family| resolve_prompts(map, context, tmux, family))
        .collect::<Option<Vec<_>>>()?;
    if prompts.is_empty() {
        return None;
    }
    let merged = |label: fn(&ControllerPrompts) -> &str| {
        merge_prompt_labels(prompts.iter().map(|p| (p.family.as_str(), label(p))))
    };
    Some(ControllerPrompts {
        family: prompts
            .iter()
            .map(|p| p.family.as_str())
            .collect::<Vec<_>>()
            .join(" + "),
        navigate: merged(|p| &p.navigate)?,
        accept: merged(|p| &p.accept)?,
        back: merged(|p| &p.back)?,
        next: merged(|p| &p.next)?,
        release: merged(|p| &p.release)?,
        quit: merge_prompt_labels(
            prompts
                .iter()
                .filter_map(|p| p.quit.as_deref().map(|q| (p.family.as_str(), q))),
        ),
    })
}

fn merge_prompt_labels<'a>(labels: impl IntoIterator<Item = (&'a str, &'a str)>) -> Option<String> {
    let mut groups: Vec<(Vec<&str>, &str)> = Vec::new();
    for (family, label) in labels {
        if let Some((families, _)) = groups.iter_mut().find(|(_, previous)| *previous == label) {
            families.push(family);
        } else {
            groups.push((vec![family], label));
        }
    }
    match groups.len() {
        0 => None,
        1 => Some(groups[0].1.into()),
        _ => Some(
            groups
                .into_iter()
                .map(|(families, label)| format!("{}: {label}", families.join("+")))
                .collect::<Vec<_>>()
                .join(" / "),
        ),
    }
}

/// An explicitly inherited, uniquely attached human client for native dialogues.
/// A compositor session also proves the selected pane's client under the focused
/// terminal. Console/SSH clients have no compositor and require one attached owner.
#[derive(Clone, Debug)]
pub struct DialogTarget {
    pub socket: PathBuf,
    pub pane: String,
    pub client: String,
    pub width: u16,
    pub height: u16,
}
fn unique_dialog_client<'a>(text: &'a str, session: &str) -> Option<Vec<&'a str>> {
    let mut clients = text.lines().filter_map(|line| {
        let fields: Vec<_> = line.split('|').collect();
        (fields.len() == 6 && fields[5] == session).then_some(fields)
    });
    let first = clients.next()?;
    if clients.next().is_some() {
        None
    } else {
        Some(first)
    }
}
pub fn attached_dialog_target(
    timeout_ms: u64,
    max_bytes: usize,
    min_width: u16,
    min_height: u16,
) -> Option<DialogTarget> {
    let id = Identity::from_env()?;
    let (socket, pane) = id.tmux.as_ref()?;
    let stop = AtomicBool::new(false);
    let timeout = Duration::from_millis(timeout_ms);
    let query = Query {
        deadline: Instant::now() + timeout * 3,
        timeout,
        max_bytes,
        stop: &stop,
    };
    let session = query.command(
        "tmux",
        &[
            "-S",
            socket.to_str()?,
            "display-message",
            "-p",
            "-t",
            pane,
            "#{session_id}",
        ],
        None,
    )?;
    let session = std::str::from_utf8(&session).ok()?.trim();
    if session.len() < 2
        || !session.starts_with('$')
        || !session[1..].bytes().all(|b| b.is_ascii_digit())
    {
        return None;
    }
    let bytes = query.command(
        "tmux",
        &[
            "-S",
            socket.to_str()?,
            "list-clients",
            "-F",
            "#{client_pid}|#{client_tty}|#{pane_id}|#{client_width}|#{client_height}|#{session_id}",
        ],
        None,
    )?;
    let text = std::str::from_utf8(&bytes).ok()?;
    let fields = unique_dialog_client(text, session)?;
    if fields[2] != pane
        || !fields[1].starts_with("/dev/")
        || !fields[1].bytes().all(|b| b.is_ascii_graphic())
    {
        return None;
    }
    let pid: u32 = fields[0].parse().ok()?;
    let width: u16 = fields[3].parse().ok()?;
    let height: u16 = fields[4].parse().ok()?;
    if pid == 0 || width < min_width || height < min_height {
        return None;
    }
    if id.instance.is_some() {
        let context = query.json("mios-inputd", &["context"], None)?;
        let focused: u32 = context["pid"].as_u64()?.try_into().ok()?;
        if !descendant(pid, focused) {
            return None;
        }
    } else if std::env::var_os("WAYLAND_DISPLAY").is_some() || std::env::var_os("DISPLAY").is_some()
    {
        return None;
    }
    Some(DialogTarget {
        socket: socket.clone(),
        pane: pane.clone(),
        client: fields[1].into(),
        width,
        height,
    })
}

#[cfg(test)]
mod controller_tests {
    use super::*;
    #[test]
    fn dialogue_popup_counts_every_attached_session_client_including_tiny_foreign_panes() {
        let one = "10|/dev/pts/1|%0|80|24|$0";
        assert!(unique_dialog_client(one, "$0").is_some());
        assert!(unique_dialog_client(&format!("{one}\n11|/dev/pts/2|%4|10|5|$0"), "$0").is_none());
        assert!(unique_dialog_client(&format!("{one}\n11|/dev/pts/2|%0|10|5|$0"), "$0").is_none());
        assert!(unique_dialog_client(&format!("{one}\n11|/dev/pts/2|%4|80|24|$1"), "$0").is_some());
        assert!(unique_dialog_client(one, "$2").is_none());
    }
    use serde_json::json;

    fn context() -> Json {
        json!({"context":"terminal","address":"0xabc","pid":42,
            "controller_owner":"mios","released":false,"room_capture":false})
    }
    fn map() -> Json {
        json!({
            "global":{"enabled":true,"controller":{
                "enabled":true,"capture_navigation_contexts":["terminal"],
                "nav":{"enabled":true,"contexts":{"terminal":"terminal"},
                    "desktop":{
                        "BTN_SOUTH":["key:ENTER","key:SHIFT+ENTER"],
                        "BTN_EAST":["key:ESC","key:ALT+LEFT"],
                        "BTN_WEST":["key:TAB","key:SHIFT+TAB"],
                        "BTN_DPAD_UP":"key:UP","BTN_DPAD_DOWN":"key:DOWN",
                        "BTN_DPAD_LEFT":"key:LEFT","BTN_DPAD_RIGHT":"key:RIGHT",
                        "LS_UP":"pointer:wheel-up","LS_DOWN":"pointer:wheel-down",
                        "LS_LEFT":"pointer:hwheel-left","LS_RIGHT":"pointer:hwheel-right"
                    },
                    "terminal_tmux":{"inherits":"desktop",
                        "BTN_EAST":["key:ESC","key:CTRL+c"],
                        "RS_UP":"tmux:focus up"},
                    "terminal_plain":{"inherits":"terminal_tmux"}
                }
            }},
            "hypr3d":{"toggle_chord":["BTN_THUMBL","BTN_THUMBR"]}
        })
    }

    #[test]
    fn actual_profile_inheritance_and_family_labels() {
        for (name, family_name, accept, back, next) in [
            (
                "Sony DualSense Wireless Controller",
                "Sony",
                "Cross",
                "Circle",
                "Square",
            ),
            ("Microsoft Xbox Controller", "Xbox", "A", "B", "X"),
            ("Nintendo Switch Pro Controller", "Nintendo", "B", "A", "Y"),
            (
                "MiOS Nested Pad test",
                "Controller",
                "South",
                "East",
                "West",
            ),
        ] {
            assert_eq!(family(name), family_name);
            for tmux in [false, true] {
                let p = resolve_prompts(&map(), &context(), tmux, family_name).unwrap();
                assert_eq!(p.family, family_name);
                assert_eq!(p.navigate, "D-pad");
                assert_eq!(p.accept, format!("Tap {accept}"));
                assert_eq!(p.back, format!("Tap {back}"));
                assert_eq!(p.next, format!("Tap {next}"));
                assert_eq!(p.release, "Hold L3+R3");
                assert_eq!(p.quit.as_deref(), Some(format!("Hold {back}").as_str()));
            }
        }
    }

    #[test]
    fn remapped_actions_follow_actual_buttons_and_hold_semantics() {
        let mut map = map();
        let profile = &mut map["global"]["controller"]["nav"]["terminal_tmux"];
        profile["BTN_SOUTH"] = json!("none");
        profile["BTN_EAST"] = json!("key:ENTER");
        profile["BTN_WEST"] = json!(["none", "key:ESC"]);
        profile["BTN_TR"] = json!("key:TAB");
        let p = resolve_prompts(&map, &context(), true, "Sony").unwrap();
        assert_eq!(p.accept, "Circle");
        assert_eq!(p.back, "Hold Square");
        assert_eq!(p.next, "R1");
        assert!(p.quit.is_none());
        assert!(!p.navigate.contains("LS"));
    }

    #[test]
    fn directions_never_invent_left_stick_arrows() {
        let mut map = map();
        map["global"]["controller"]["nav"]["desktop"]["BTN_DPAD_UP"] = json!("none");
        assert!(resolve_prompts(&map, &context(), true, "Controller").is_none());
        map["global"]["controller"]["nav"]["terminal_tmux"]["BTN_TR"] = json!("key:UP");
        let p = resolve_prompts(&map, &context(), true, "Controller").unwrap();
        assert!(p.navigate.starts_with("R / "));
        assert!(!p.navigate.contains("LS"));
    }

    #[test]
    fn cycles_malformed_profiles_and_unbound_actions_fail_closed() {
        for bad in [json!(false), json!(["key:ENTER"]), json!([1, 2])] {
            let mut map = map();
            map["global"]["controller"]["nav"]["desktop"]["BTN_SOUTH"] = bad;
            assert!(resolve_prompts(&map, &context(), true, "Sony").is_none());
        }
        let mut map = map();
        map["global"]["controller"]["nav"]["desktop"]["inherits"] = json!("terminal_tmux");
        assert!(resolve_prompts(&map, &context(), true, "Sony").is_none());
        let mut map = self::map();
        map["global"]["controller"]["nav"]["terminal_tmux"]["BTN_SOUTH"] = json!("none");
        assert!(resolve_prompts(&map, &context(), true, "Sony").is_none());
    }

    #[test]
    fn foreign_context_and_disabled_or_released_navigation_fail_closed() {
        for (field, value) in [
            ("room_capture", json!(true)),
            ("controller_owner", json!("foreign")),
            ("context", json!("game")),
        ] {
            let mut c = context();
            c[field] = value;
            assert!(resolve_prompts(&map(), &c, true, "Sony").is_none());
        }
        let mut c = context();
        c["released"] = json!(true);
        assert!(resolve_prompts(&map(), &c, true, "Sony").is_some());
        c["released"] = json!(false);
        c["context"] = json!("app-capture");
        c["controller_owner"] = json!("app");
        assert!(resolve_prompts(&map(), &c, true, "Sony").is_some());
        c["room_capture"] = json!(true);
        assert!(resolve_prompts(&map(), &c, true, "Sony").is_some());
        let mut map = map();
        map["global"]["controller"]["capture_navigation_contexts"] = json!([]);
        assert!(resolve_prompts(&map, &c, true, "Sony").is_none());
        map["global"]["controller"]["nav"]["enabled"] = json!(false);
        assert!(resolve_prompts(&map, &context(), true, "Sony").is_none());
    }

    fn pad(path: &str, name: &str, virtual_pad: bool, assigned: Json) -> Json {
        json!({"path":path,"name":name,"kind":"controller",
            "virtual":virtual_pad,"assigned_to":assigned})
    }
    fn seat() -> Seat {
        Seat {
            parent: PathBuf::from("/verified-runtime"),
            entry: json!({"seat":"own-seat","devices":[{
                "path":"/dev/input/event6","name":"MiOS Nested Pad own-seat",
                "kind":"controller","virtual":true,"spec":"virtual-pad"}]}),
        }
    }

    #[test]
    fn nested_pad_requires_own_live_registry_assignment_not_host_or_clone() {
        let host = pad("/dev/input/event19", "Sony DualSense", false, Json::Null);
        let clone = pad(
            "/dev/input/event7",
            "MiOS virtual clone",
            true,
            json!("own-seat"),
        );
        let own = pad(
            "/dev/input/event6",
            "MiOS Nested Pad own-seat",
            true,
            json!("own-seat"),
        );
        assert!(connected(&json!([host.clone(), clone.clone()]), Some(&seat())).is_none());
        assert_eq!(
            connected(&json!([host.clone(), clone, own.clone()]), Some(&seat())),
            Some(BTreeSet::from(["Controller"]))
        );
        assert_eq!(
            connected(&json!([host, own.clone()]), None),
            Some(BTreeSet::from(["Sony"]))
        );
        assert!(connected(&json!([own]), None).is_none());
        let foreign = pad(
            "/dev/input/event6",
            "MiOS Nested Pad own-seat",
            true,
            json!("other-seat"),
        );
        assert!(connected(&json!([foreign]), Some(&seat())).is_none());
        assert!(connected(&json!([]), Some(&seat())).is_none());
    }

    #[test]
    fn tmux_requires_exact_inherited_socket_pane_and_client_ancestry() {
        assert_eq!(
            parse_tmux("/verified/socket,123,0", "%7"),
            Some((PathBuf::from("/verified/socket"), "%7".into()))
        );
        for (tmux, pane) in [
            ("socket,123,0", "%7"),
            ("/socket,0,0", "%7"),
            ("/socket,123,0", "%"),
            ("/socket,123,0", "%7x"),
        ] {
            assert!(parse_tmux(tmux, pane).is_none());
        }
        let ancestry = |pid, focused| pid == 11 && focused == 42;
        assert!(focused_client("11|%7\n22|%8", "%7", 42, ancestry));
        assert!(!focused_client("11|%8", "%7", 42, ancestry));
        assert!(!focused_client("22|%7", "%7", 42, ancestry));
        assert!(!focused_client("11|%7", "%7", 99, ancestry));
        assert!(!focused_client("invalid|%7", "%7", 42, ancestry));
        assert!(descendant(std::process::id(), std::process::id()));
        assert!(!descendant(std::process::id(), u32::MAX));
    }

    #[test]
    fn stale_or_failed_samples_clear_prompts_without_waiting() {
        let (request, _requests) = mpsc::sync_channel(1);
        let (results, result) = mpsc::sync_channel(1);
        let now = Instant::now();
        let mut monitor = ControllerMonitor {
            request,
            result,
            stop: Arc::new(AtomicBool::new(false)),
            next_poll: now + Duration::from_secs(1),
            interval: Duration::from_millis(750),
            stale: Duration::from_secs(2),
            cached: Some(Sample {
                started: now - Duration::from_secs(3),
                prompts: resolve_prompts(&map(), &context(), true, "Sony"),
                reason: "Inputd: assigned pad focused here.",
            }),
            enabled: true,
        };
        assert!(monitor.poll().is_none());
        assert!(monitor.status().contains("stale"));
        results
            .send(Sample {
                started: Instant::now(),
                prompts: resolve_prompts(&map(), &context(), true, "Sony"),
                reason: "Inputd: assigned pad focused here.",
            })
            .unwrap();
        assert!(monitor.poll().is_some());
        assert!(monitor.status().contains("assigned pad focused"));
        results
            .send(Sample {
                started: Instant::now(),
                prompts: None,
                reason: "Inputd: no controller assigned to this nested session.",
            })
            .unwrap();
        assert!(monitor.poll().is_none());
        assert!(monitor.status().contains("no controller assigned"));
    }

    #[test]
    fn bounded_queries_reject_hangs_oversize_failed_and_malformed_output() {
        let stop = AtomicBool::new(false);
        let query = Query {
            deadline: Instant::now() + Duration::from_secs(2),
            timeout: Duration::from_millis(40),
            max_bytes: 32,
            stop: &stop,
        };
        let start = Instant::now();
        assert!(query.command("/bin/sh", &["-c", "sleep 5"], None).is_none());
        assert!(start.elapsed() < Duration::from_secs(1));
        assert!(query
            .command("/bin/sh", &["-c", "printf '%100s' x"], None)
            .is_none());
        assert!(query.command("/bin/sh", &["-c", "exit 1"], None).is_none());
        assert!(query
            .json("/bin/sh", &["-c", "printf '{broken'"], None)
            .is_none());
        assert_eq!(
            query.json("/bin/sh", &["-c", "printf '{\"ok\":true}'"], None),
            Some(json!({"ok":true}))
        );
        stop.store(true, Ordering::Relaxed);
        assert!(query
            .command("/bin/sh", &["-c", "printf ok"], None)
            .is_none());
    }

    #[test]
    fn existing_key_actions_keep_release_repeat_and_modifier_filtering() {
        for code in [
            KeyCode::Enter,
            KeyCode::Esc,
            KeyCode::Char('p'),
            KeyCode::Char('q'),
        ] {
            let mut key = KeyEvent::new(code, KeyModifiers::NONE);
            key.kind = KeyEventKind::Release;
            assert!(action(key).is_none());
            key.kind = KeyEventKind::Repeat;
            assert!(action(key).is_none());
        }
        let mut key = KeyEvent::new(KeyCode::Right, KeyModifiers::NONE);
        key.kind = KeyEventKind::Repeat;
        assert!(matches!(
            action(key),
            Some(Action::Direction(Direction::Right))
        ));
        key.code = KeyCode::Tab;
        assert_eq!(action(key), Some(Action::Next));
        assert_eq!(
            action(KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL)),
            Some(Action::Quit)
        );
        assert!(action(KeyEvent::new(KeyCode::Enter, KeyModifiers::ALT)).is_none());
    }

    #[test]
    fn multiple_assigned_controller_families_stay_inside_their_own_session() {
        let sony = pad(
            "/dev/input/event14",
            "Sony DualSense",
            false,
            json!("own-seat"),
        );
        let xbox = pad(
            "/dev/input/event15",
            "Microsoft Xbox",
            false,
            json!("own-seat"),
        );
        let foreign = pad(
            "/dev/input/event16",
            "Nintendo Switch Pro",
            false,
            json!("other-seat"),
        );
        let mut own = seat();
        own.entry["devices"] = json!([
            {"path":"/dev/input/event14","name":"Sony DualSense","kind":"controller","virtual":false},
            {"path":"/dev/input/event15","name":"Microsoft Xbox","kind":"controller","virtual":false}
        ]);
        assert_eq!(
            connected(
                &json!([sony.clone(), xbox.clone(), foreign.clone()]),
                Some(&own)
            ),
            Some(BTreeSet::from(["Sony", "Xbox"]))
        );
        assert_eq!(
            connected(&json!([sony.clone(), foreign.clone()]), Some(&own)),
            Some(BTreeSet::from(["Sony"]))
        );
        assert!(connected(&json!([foreign.clone()]), Some(&own)).is_none());
        own.entry["devices"] = json!([]);
        assert!(connected(&json!([sony, xbox, foreign]), Some(&own)).is_none());
    }

    #[test]
    fn every_known_and_generic_family_combination_keeps_its_actual_actions() {
        let cases = [
            (
                "Controller",
                "Unidentified gamepad",
                "South",
                "East",
                "West",
            ),
            ("Nintendo", "Nintendo Switch Pro", "B", "A", "Y"),
            ("Sony", "Sony DualSense", "Cross", "Circle", "Square"),
            ("Xbox", "Microsoft Xbox", "A", "B", "X"),
        ];
        for mask in 1usize..(1 << cases.len()) {
            let selected: Vec<_> = cases
                .iter()
                .enumerate()
                .filter(|(i, _)| mask & (1 << *i) != 0)
                .map(|(_, case)| case)
                .collect();
            let devices: Vec<_> = selected
                .iter()
                .enumerate()
                .map(|(i, (_, name, _, _, _))| {
                    pad(&format!("/dev/input/event{i}"), name, false, Json::Null)
                })
                .collect();
            let families = connected(&json!(devices), None).unwrap();
            for tmux in [false, true] {
                let prompts =
                    resolve_connected_prompts(&map(), &context(), tmux, &families).unwrap();
                assert_eq!(
                    prompts.family,
                    selected.iter().map(|c| c.0).collect::<Vec<_>>().join(" + ")
                );
                let expected = |index, gesture: &str| {
                    let labels: Vec<_> = selected
                        .iter()
                        .map(|case| {
                            let button = match index {
                                0 => case.2,
                                1 => case.3,
                                _ => case.4,
                            };
                            if selected.len() == 1 {
                                format!("{gesture} {button}")
                            } else {
                                format!("{}: {gesture} {button}", case.0)
                            }
                        })
                        .collect();
                    labels.join(" / ")
                };
                assert_eq!(prompts.accept, expected(0, "Tap"));
                assert_eq!(prompts.back, expected(1, "Tap"));
                assert_eq!(prompts.next, expected(2, "Tap"));
                assert_eq!(prompts.quit.as_deref(), Some(expected(1, "Hold").as_str()));
                assert_eq!(prompts.navigate, "D-pad");
                assert_eq!(prompts.release, "Hold L3+R3");
            }
        }
    }

    #[test]
    fn discovery_deduplicates_same_family_and_never_prefers_discovery_order() {
        let sony = pad("/dev/input/event1", "Sony DualSense", false, Json::Null);
        let other_sony = pad("/dev/input/event2", "DualShock", false, Json::Null);
        let xbox = pad("/dev/input/event3", "Xbox", false, Json::Null);
        let families = connected(
            &json!([sony.clone(), other_sony.clone(), sony.clone()]),
            None,
        )
        .unwrap();
        assert_eq!(families, BTreeSet::from(["Sony"]));
        let p = resolve_connected_prompts(&map(), &context(), true, &families).unwrap();
        assert_eq!(p.family, "Sony");
        assert_eq!(p.accept, "Tap Cross");
        for devices in [
            json!([sony.clone(), other_sony.clone(), xbox.clone()]),
            json!([xbox, other_sony, sony]),
        ] {
            let families = connected(&devices, None).unwrap();
            let p = resolve_connected_prompts(&map(), &context(), true, &families).unwrap();
            assert_eq!(p.family, "Sony + Xbox");
            assert_eq!(p.accept, "Sony: Tap Cross / Xbox: Tap A");
            assert_eq!(p.back, "Sony: Tap Circle / Xbox: Tap B");
        }
    }

    #[test]
    fn mixed_remaps_keep_tap_hold_and_group_only_identical_action_labels() {
        let families = BTreeSet::from(["Controller", "Nintendo", "Sony", "Xbox"]);
        let mut map = map();
        let profile = &mut map["global"]["controller"]["nav"]["terminal_tmux"];
        profile["BTN_SOUTH"] = json!("none");
        profile["BTN_EAST"] = json!(["none", "key:ENTER"]);
        profile["BTN_WEST"] = json!(["key:ESC", "none"]);
        profile["BTN_TR"] = json!("key:TAB");
        map["hypr3d"]["toggle_chord"] = json!(["BTN_SOUTH", "BTN_EAST"]);
        let p = resolve_connected_prompts(&map, &context(), true, &families).unwrap();
        assert_eq!(
            p.accept,
            "Controller: Hold East / Nintendo: Hold A / Sony: Hold Circle / Xbox: Hold B"
        );
        assert_eq!(
            p.back,
            "Controller: Tap West / Nintendo: Tap Y / Sony: Tap Square / Xbox: Tap X"
        );
        assert_eq!(p.next, "Controller+Nintendo: R / Sony: R1 / Xbox: RB");
        assert_eq!(
            p.release,
            "Controller: Hold South+East / Nintendo: Hold B+A / Sony: Hold Cross+Circle / Xbox: Hold A+B"
        );
        assert!(p.quit.is_none());
        assert_eq!(p.navigate, "D-pad");
    }

    #[test]
    fn mixed_remapped_navigation_uses_real_direction_bindings_and_shared_labels() {
        let families = BTreeSet::from(["Nintendo", "Sony", "Xbox"]);
        let mut map = map();
        let profile = &mut map["global"]["controller"]["nav"]["terminal_tmux"];
        profile["BTN_DPAD_UP"] = json!("none");
        profile["BTN_TR"] = json!(["none", "key:UP"]);
        let p = resolve_connected_prompts(&map, &context(), true, &families).unwrap();
        assert_eq!(
            p.navigate,
            "Nintendo: Hold R / D-pad Down / D-pad Left / D-pad Right / Sony: Hold R1 / D-pad Down / D-pad Left / D-pad Right / Xbox: Hold RB / D-pad Down / D-pad Left / D-pad Right"
        );
        assert!(!p.navigate.contains("LS"));
        map["global"]["controller"]["nav"]["terminal_tmux"]["BTN_TR"] = json!("none");
        assert!(resolve_connected_prompts(&map, &context(), true, &families).is_none());
    }

    #[test]
    fn mixed_families_cannot_bypass_context_capture_or_profile_guards() {
        let families = BTreeSet::from(["Controller", "Nintendo", "Sony", "Xbox"]);
        for (field, value) in [
            ("controller_owner", json!("foreign")),
            ("context", json!("game")),
            ("room_capture", json!(true)),
            ("released", Json::Null),
        ] {
            let mut c = context();
            c[field] = value;
            assert!(resolve_connected_prompts(&map(), &c, true, &families).is_none());
        }
        let mut c = context();
        c["context"] = json!("app-capture");
        c["controller_owner"] = json!("app");
        assert!(resolve_connected_prompts(&map(), &c, true, &families).is_some());
        c["released"] = json!(true);
        assert!(resolve_connected_prompts(&map(), &c, true, &families).is_none());
        for path in [
            vec!["global", "enabled"],
            vec!["global", "controller", "enabled"],
            vec!["global", "controller", "nav", "enabled"],
        ] {
            let mut map = map();
            let mut value = &mut map;
            for key in path {
                value = &mut value[key];
            }
            *value = json!(false);
            assert!(resolve_connected_prompts(&map, &context(), true, &families).is_none());
        }
        let mut map = map();
        map["global"]["controller"]["nav"]["contexts"]["terminal"] = json!("vpad");
        assert!(resolve_connected_prompts(&map, &context(), true, &families).is_none());
        map["global"]["controller"]["nav"]["contexts"]["terminal"] = json!("missing");
        assert!(resolve_connected_prompts(&map, &context(), true, &families).is_none());
        for chord in [
            json!([]),
            json!(["BTN_SOUTH"]),
            json!(["BTN_SOUTH", "BTN_SOUTH"]),
            json!(["LS_UP", "BTN_THUMBR"]),
            json!(["BTN_UNKNOWN", "BTN_THUMBR"]),
        ] {
            let mut map = self::map();
            map["hypr3d"]["toggle_chord"] = chord;
            assert!(resolve_connected_prompts(&map, &context(), true, &families).is_none());
        }
        assert!(
            resolve_connected_prompts(&self::map(), &context(), true, &BTreeSet::new()).is_none()
        );
    }

    #[test]
    fn malformed_discovery_rows_do_not_hide_a_valid_controller() {
        let valid = pad("/dev/input/event0", "Sony DualSense", false, Json::Null);
        let mut malformed = vec![Json::Null, json!(1), json!({}), json!({"kind":"keyboard"})];
        for (field, values) in [
            ("name", vec![Json::Null, json!(3), json!("")]),
            (
                "path",
                vec![
                    Json::Null,
                    json!(3),
                    json!("/dev/input/event"),
                    json!("/dev/input/eventx"),
                    json!("/dev/input/event1/other"),
                    json!("/dev/input/event-1"),
                    json!("/dev/input/event1\n"),
                ],
            ),
            ("virtual", vec![Json::Null, json!("false")]),
            (
                "assigned_to",
                vec![json!(false), json!(0), json!("foreign-seat")],
            ),
        ] {
            for value in values {
                let mut bad = valid.clone();
                bad[field] = value;
                malformed.push(bad);
            }
        }
        let mut missing = valid.clone();
        missing.as_object_mut().unwrap().remove("assigned_to");
        malformed.push(missing);
        assert!(connected(&json!(malformed.clone()), None).is_none());
        for bad in &malformed {
            for devices in [json!([bad, valid.clone()]), json!([valid.clone(), bad])] {
                assert_eq!(connected(&devices, None), Some(BTreeSet::from(["Sony"])));
            }
        }
        let mut devices: Vec<_> = (0..64).flat_map(|_| malformed.iter().cloned()).collect();
        devices.push(valid);
        let families = connected(&json!(devices), None).unwrap();
        assert_eq!(families, BTreeSet::from(["Sony"]));
        assert!(resolve_connected_prompts(&map(), &context(), true, &families).is_some());
        for invalid in [Json::Null, json!({}), json!("devices"), json!([])] {
            assert!(connected(&invalid, None).is_none());
        }
    }

    #[test]
    fn mixed_nested_families_require_each_exact_registry_row_and_assignment() {
        let sony = pad(
            "/dev/input/event14",
            "Sony DualSense",
            false,
            json!("own-seat"),
        );
        let xbox = pad("/dev/input/event15", "Xbox", false, json!("own-seat"));
        let generic = pad(
            "/dev/input/event16",
            "Unidentified pad",
            false,
            json!("own-seat"),
        );
        let mut own = seat();
        own.entry["devices"] = json!([sony.clone(), xbox.clone(), generic.clone()]);
        let mut foreign = pad(
            "/dev/input/event17",
            "Nintendo Switch Pro",
            false,
            json!("foreign"),
        );
        // Even a registry match is insufficient without the live own assignment.
        own.entry["devices"]
            .as_array_mut()
            .unwrap()
            .push(foreign.clone());
        let host = pad("/dev/input/event18", "Nintendo", false, Json::Null);
        let discovered = json!([foreign.clone(), host, sony.clone(), xbox, generic]);
        let families = connected(&discovered, Some(&own)).unwrap();
        assert_eq!(families, BTreeSet::from(["Controller", "Sony", "Xbox"]));
        let p = resolve_connected_prompts(&map(), &context(), true, &families).unwrap();
        assert_eq!(
            p.accept,
            "Controller: Tap South / Sony: Tap Cross / Xbox: Tap A"
        );
        assert!(!p.family.contains("Nintendo"));
        foreign["assigned_to"] = json!("own-seat");
        foreign["name"] = json!("renamed Nintendo");
        assert!(connected(&json!([foreign]), Some(&own)).is_none());
        for (field, value) in [
            ("path", json!("/dev/input/event99")),
            ("name", json!("spoofed Sony")),
            ("virtual", json!(true)),
            ("kind", json!("keyboard")),
        ] {
            let mut mismatch = sony.clone();
            mismatch[field] = value;
            assert!(connected(&json!([mismatch]), Some(&own)).is_none());
        }
        for (field, value) in [
            ("seat", Json::Null),
            ("seat", json!("")),
            ("devices", Json::Null),
        ] {
            let mut invalid = seat();
            invalid.entry[field] = value;
            assert!(connected(&json!([sony.clone()]), Some(&invalid)).is_none());
        }
        own.entry["devices"] = json!([Json::Null, {"kind":"controller"}, sony.clone()]);
        let mut bad = sony.clone();
        bad["name"] = Json::Null;
        assert_eq!(
            connected(&json!([bad, sony]), Some(&own)),
            Some(BTreeSet::from(["Sony"]))
        );
    }

    #[test]
    fn nested_parent_runtime_requires_matching_live_own_registry() {
        let temp = tempfile::tempdir().unwrap();
        let runtime = temp.path().join("nested");
        fs::create_dir(&runtime).unwrap();
        let descriptor_path = runtime.join("session.json");
        let registry = temp.path().join("own-seat.json");
        let pid = std::process::id();
        let descriptor = json!({
            "registry":registry,"runtime":runtime,"seat":"own-seat",
            "instance":{"instance":"own-instance","pid":pid}
        });
        let mut entry = json!({
            "parent_runtime":temp.path(),"runtime":runtime,"seat":"own-seat",
            "state":"running","descriptor":descriptor_path,
            "instance":"own-instance","launcher_pid":pid,
            "launcher_start":proc_stat(pid).unwrap().1,"compositor_pid":pid
        });
        fs::write(&descriptor_path, descriptor.to_string()).unwrap();
        fs::write(&registry, entry.to_string()).unwrap();
        let id = Identity {
            runtime,
            seat: Some("own-seat".into()),
            instance: Some("own-instance".into()),
            tmux: None,
            pid,
        };
        let stop = AtomicBool::new(false);
        let query = Query {
            deadline: Instant::now() + Duration::from_secs(2),
            timeout: Duration::from_millis(40),
            max_bytes: 4096,
            stop: &stop,
        };
        let verified = own_seat(&id, &query).unwrap().unwrap();
        assert_eq!(verified.parent, temp.path());
        entry["launcher_start"] = json!(proc_stat(pid).unwrap().1 + 1);
        fs::write(&registry, entry.to_string()).unwrap();
        assert!(own_seat(&id, &query).is_none());
        entry["launcher_start"] = json!(proc_stat(pid).unwrap().1);
        entry["seat"] = json!("foreign-seat");
        fs::write(&registry, entry.to_string()).unwrap();
        assert!(own_seat(&id, &query).is_none());
        fs::write(&registry, "{broken").unwrap();
        assert!(own_seat(&id, &query).is_none());
    }
}

#[cfg(test)]
mod training_action_tests {
    use super::{action, Action};
    use crossterm::event::{KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
    #[test]
    fn training_opens_on_either_case_without_accepting_other_modifiers() {
        let key = KeyEvent::new(KeyCode::Char('l'), KeyModifiers::NONE);
        assert_eq!(action(key), Some(Action::Training));
        assert_eq!(
            action(KeyEvent::new(KeyCode::Char('L'), KeyModifiers::NONE)),
            Some(Action::Training)
        );
        for kind in [KeyEventKind::Release, KeyEventKind::Repeat] {
            let mut other = key;
            other.kind = kind;
            assert_eq!(action(other), None);
        }
        for modifier in [
            KeyModifiers::CONTROL,
            KeyModifiers::ALT,
            KeyModifiers::SUPER,
            KeyModifiers::HYPER,
            KeyModifiers::META,
        ] {
            assert_eq!(action(KeyEvent::new(KeyCode::Char('l'), modifier)), None);
        }
    }
}

#[cfg(test)]
mod direct_key_regressions {
    use super::*;
    #[test]
    fn displayed_capitals_work_in_both_cases_but_repeat_release_and_foreign_chords_do_not() {
        for (lower, expected) in [
            ('f', Action::Care(Care::Feed)),
            ('g', Action::Care(Care::Play)),
            ('c', Action::Care(Care::Clean)),
            ('r', Action::Care(Care::Rest)),
            ('m', Action::Care(Care::Medicine)),
            ('p', Action::Pause),
            ('s', Action::Save),
            ('n', Action::Reset),
            ('q', Action::Quit),
            ('o', Action::Menu),
            ('z', Action::Camera),
            ('l', Action::Training),
            ('d', Action::Detail),
        ] {
            for character in [lower, lower.to_ascii_uppercase()] {
                let mut key = KeyEvent::new(KeyCode::Char(character), KeyModifiers::NONE);
                assert_eq!(action(key), Some(expected));
                for kind in [KeyEventKind::Repeat, KeyEventKind::Release] {
                    key.kind = kind;
                    assert_eq!(action(key), None);
                }
                key.kind = KeyEventKind::Press;
                key.modifiers = KeyModifiers::ALT;
                assert_eq!(action(key), None);
            }
        }
        for number in '1'..='9' {
            let mut key = KeyEvent::new(KeyCode::Char(number), KeyModifiers::NONE);
            assert!(matches!(action(key), Some(Action::Number(_))));
            key.kind = KeyEventKind::Repeat;
            assert!(action(key).is_none());
        }
        assert_eq!(
            action(KeyEvent::new(KeyCode::Char('0'), KeyModifiers::NONE)),
            Some(Action::ZoomReset)
        );
        assert!(action(KeyEvent::new(KeyCode::Char('p'), KeyModifiers::CONTROL)).is_none());
        assert_eq!(
            action(KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL)),
            Some(Action::Quit)
        );
    }
}
