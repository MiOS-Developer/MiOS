// AI-hint: Persistent authenticated tmux client-pointer actor; consumes Inputd pointer projections without device readers or host uinput fallback.
// AI-related: /usr/share/mios/templates/rust, tmux_capture::focused_target, runtime::control_command, router::Event
// AI-functions: PointerWorker::spawn, PointerWorker::submit, PointerWorker::visible, PointerWorker::alive
// Scaffolded by installed native miosd; canonical template SHA256 2a9bf43dc4c1e0af3fd0b26f3b42217740a5dea7c86a7e619254f8cb62683203.
use crate::router::{Event, EV_KEY, EV_REL};
use crate::tmux_capture::Target;
use std::collections::VecDeque;
use std::io::{Read, Write};
use std::os::fd::{AsRawFd, RawFd};
use std::os::unix::fs::{FileTypeExt, MetadataExt};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex};
use std::time::{Duration, Instant};

const QUEUE_LIMIT: usize = 64;
const LINE_LIMIT: usize = 8192;
const FRAME_LIMIT: usize = 65536;
const POLL_SLICE: Duration = Duration::from_millis(20);
#[derive(Clone, Debug, PartialEq, Eq)]
struct Scope {
    uid: u32,
    socket: PathBuf,
    dev: u64,
    ino: u64,
    server: u32,
    server_start: u64,
    client: u32,
    client_start: u64,
    tty: String,
    instance: u64,
    login_generation: u64,
}
impl Scope {
    fn of(t: &Target) -> Self {
        Self {
            uid: t.uid,
            socket: t.socket.clone(),
            dev: t.socket_dev,
            ino: t.socket_ino,
            server: t.server,
            server_start: t.server_start,
            client: t.client,
            client_start: t.client_start,
            tty: t.tty.clone(),
            instance: t.client_instance,
            login_generation: t.login_generation,
        }
    }
}
/// A pane/capture transition retains the same terminal cursor; changing login/client/server identity revokes it.
fn current(target: &Target) -> Result<(), String> {
    // SAFETY: getuid is a read-only identity query.
    let uid = unsafe { libc::getuid() };
    if target.uid != uid
        || !target.socket.is_absolute()
        || target.client_instance == 0
        || target.session_name.is_empty()
        || target.tty.chars().any(char::is_control)
        || target.tty.is_empty()
    {
        return Err("native pointer requires a witnessed v1 terminal client".into());
    }
    let Some((focused, _, remote)) = crate::tmux_capture::focused_target() else {
        return Err("native pointer focus evidence is missing or stale".into());
    };
    if Scope::of(target) != Scope::of(&focused) || (remote && target.login_generation == 0) {
        return Err("native pointer client or authenticated login generation changed".into());
    }
    let meta = std::fs::symlink_metadata(&target.socket).map_err(|e| e.to_string())?;
    if !meta.file_type().is_socket()
        || meta.uid() != uid
        || (meta.dev(), meta.ino()) != (target.socket_dev, target.socket_ino)
    {
        return Err("native pointer server socket changed".into());
    }
    for (pid, start) in [
        (target.client, target.client_start),
        (target.server, target.server_start),
    ] {
        if pid == 0
            || start == 0
            || crate::nested::start_time(Path::new("/proc"), pid) != Some(start)
            || std::fs::metadata(format!("/proc/{pid}"))
                .map_err(|e| e.to_string())?
                .uid()
                != uid
        {
            return Err("native pointer client or server process changed".into());
        }
    }
    if std::fs::read_link(format!("/proc/{}/fd/0", target.client)).map_err(|e| e.to_string())?
        != Path::new(&target.tty)
    {
        return Err("native pointer client terminal changed".into());
    }
    Ok(())
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Button {
    Left,
    Middle,
    Right,
}
impl Button {
    fn name(self) -> &'static str {
        match self {
            Self::Left => "left",
            Self::Middle => "middle",
            Self::Right => "right",
        }
    }
    fn mask(self) -> u8 {
        match self {
            Self::Left => 1,
            Self::Middle => 2,
            Self::Right => 4,
        }
    }
}
#[derive(Clone, Debug, PartialEq, Eq)]
enum Action {
    Motion(i64, i64),
    Wheel(i64, i64),
    Button(Button, bool),
    Visible(bool),
}
#[derive(Clone, Debug)]
struct Pending {
    action: Action,
    queued: Instant,
}
#[derive(Default)]
struct Queue {
    commands: VecDeque<Pending>,
    remainder: [i64; 2],
    visible: bool,
    buttons: u8,
}
impl Queue {
    fn push(&mut self, action: Action) -> Result<(), String> {
        if let Action::Motion(dx, dy) = action {
            if dx == 0 && dy == 0 {
                return Ok(());
            }
            if let Some(Pending {
                action: Action::Motion(x, y),
                ..
            }) = self.commands.back_mut()
            {
                *x = x.checked_add(dx).ok_or("native pointer motion overflow")?;
                *y = y.checked_add(dy).ok_or("native pointer motion overflow")?;
                return Ok(());
            }
        }
        if self.commands.len() >= QUEUE_LIMIT {
            return Err("native pointer edge queue overflow".into());
        }
        self.commands.push_back(Pending {
            action,
            queued: Instant::now(),
        });
        Ok(())
    }
    fn visibility(&mut self, visible: bool) -> Result<(), String> {
        if self.visible == visible {
            return Ok(());
        }
        self.visible = visible;
        if !visible {
            self.buttons = 0;
            self.remainder = [0, 0];
        }
        self.push(Action::Visible(visible))
    }
    fn events(&mut self, events: &[Event], cell: [u32; 2]) -> Result<(), String> {
        for event in events {
            if !self.visible {
                continue;
            }
            match (event.kind, event.code) {
                (EV_REL, axis @ (0 | 1)) => {
                    let axis = axis as usize;
                    self.remainder[axis] = self.remainder[axis]
                        .checked_add(i64::from(event.value))
                        .ok_or("native pointer pixel overflow")?;
                    let delta = self.remainder[axis] / i64::from(cell[axis]);
                    self.remainder[axis] %= i64::from(cell[axis]);
                    self.push(if axis == 0 {
                        Action::Motion(delta, 0)
                    } else {
                        Action::Motion(0, delta)
                    })?;
                }
                (EV_REL, 8) if event.value != 0 => {
                    self.push(Action::Wheel(0, i64::from(event.value)))?
                }
                (EV_REL, 6) if event.value != 0 => {
                    self.push(Action::Wheel(i64::from(event.value), 0))?
                }
                (EV_KEY, code @ (0x110..=0x112)) if event.value == 0 || event.value == 1 => {
                    let button = match code {
                        0x110 => Button::Left,
                        0x111 => Button::Right,
                        _ => Button::Middle,
                    };
                    let down = event.value == 1;
                    if (self.buttons & button.mask() != 0) == down {
                        continue;
                    }
                    if down {
                        self.buttons |= button.mask();
                    } else {
                        self.buttons &= !button.mask();
                    }
                    self.push(Action::Button(button, down))?;
                }
                _ => {} // SYN and non-pointer events do not create another physical input consumer.
            }
        }
        Ok(())
    }
}
struct Shared {
    queue: Mutex<Queue>,
    wake: Condvar,
    stop: AtomicBool,
    alive: AtomicBool,
}
impl Shared {
    fn new() -> Self {
        Self {
            queue: Mutex::new(Queue::default()),
            wake: Condvar::new(),
            stop: AtomicBool::new(false),
            alive: AtomicBool::new(true),
        }
    }
    fn fail(&self) {
        self.stop.store(true, Ordering::Release);
        self.alive.store(false, Ordering::Release);
        self.wake.notify_all();
    }
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Ack {
    instance: u64,
    x: u32,
    y: u32,
    visible: bool,
    buttons: u8,
}
fn decimal<T: std::str::FromStr>(value: &str) -> Result<T, String> {
    if value.is_empty() || !value.bytes().all(|b| b.is_ascii_digit()) {
        return Err("nondecimal native pointer acknowledgment".into());
    }
    value
        .parse()
        .map_err(|_| "native pointer acknowledgment overflow".into())
}
fn parse_ack(line: &str, instance: u64) -> Result<Ack, String> {
    let v: Vec<_> = line.split_whitespace().collect();
    if v.len() != 6 || v[0] != "pointer-v1" {
        return Err("invalid native pointer acknowledgment".into());
    }
    let ack = Ack {
        instance: decimal(v[1])?,
        x: decimal(v[2])?,
        y: decimal(v[3])?,
        visible: match v[4] {
            "0" => false,
            "1" => true,
            _ => return Err("invalid native pointer visibility".into()),
        },
        buttons: decimal(v[5])?,
    };
    if ack.instance != instance
        || ack.instance == 0
        || ack.buttons > 7
        || ack.x > 65535
        || ack.y > 65535
    {
        return Err("stale or invalid native pointer acknowledgment identity".into());
    }
    Ok(ack)
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct BlockId(u64, u64, u64);
fn header(line: &str, kind: &str) -> Result<BlockId, String> {
    let v: Vec<_> = line.split_whitespace().collect();
    if v.len() != 4 || v[0] != kind {
        return Err("invalid tmux control block header".into());
    }
    Ok(BlockId(decimal(v[1])?, decimal(v[2])?, decimal(v[3])?))
}
struct Block {
    id: BlockId,
    ack: Option<Ack>,
}
struct Parser {
    block: Option<Block>,
    last: Option<u64>,
    instance: Option<u64>,
    bytes: usize,
}
impl Parser {
    fn new(last: Option<u64>, instance: Option<u64>) -> Self {
        Self {
            block: None,
            last,
            instance,
            bytes: 0,
        }
    }
    fn line(&mut self, line: &str) -> Result<Option<Option<Ack>>, String> {
        self.bytes = self
            .bytes
            .checked_add(line.len())
            .ok_or("tmux control frame overflow")?;
        if line.len() > LINE_LIMIT || self.bytes > FRAME_LIMIT {
            return Err("tmux control frame exceeds protocol bounds".into());
        }
        if line == "%exit" || line.starts_with("%exit ") {
            return Err("native pointer control client exited".into());
        }
        if line.starts_with("%begin ") {
            let id = header(line, "%begin")?;
            if self.block.is_some() || self.last.is_some_and(|last| id.1 <= last) {
                return Err("replayed or nested tmux control block".into());
            }
            self.block = Some(Block { id, ack: None });
            return Ok(None);
        }
        if line.starts_with("%end ") || line.starts_with("%error ") {
            let error = line.starts_with("%error ");
            let id = header(line, if error { "%error" } else { "%end" })?;
            let block = self
                .block
                .take()
                .ok_or("tmux control block ended without begin")?;
            if block.id != id {
                return Err("tmux control block identity changed".into());
            }
            if error {
                return Err("native pointer lease was refused or revoked".into());
            }
            if self.instance.is_some() && block.ack.is_none() {
                return Err("native pointer command had no acknowledgment".into());
            }
            self.last = Some(id.1);
            return Ok(Some(block.ack));
        }
        if let Some(block) = &mut self.block {
            if let Some(instance) = self.instance {
                if block.ack.is_some() {
                    return Err("duplicate native pointer acknowledgment".into());
                }
                block.ack = Some(parse_ack(line, instance)?);
            }
        } else if !line.starts_with('%') {
            return Err("unframed tmux control output".into());
        }
        Ok(None)
    }
}
fn nonblocking(fd: RawFd) -> Result<(), String> {
    // SAFETY: fd is an owned child pipe; fcntl only adjusts that pipe's file status flags.
    let flags = unsafe { libc::fcntl(fd, libc::F_GETFL) };
    if flags < 0 || unsafe { libc::fcntl(fd, libc::F_SETFL, flags | libc::O_NONBLOCK) } < 0 {
        return Err(std::io::Error::last_os_error().to_string());
    }
    Ok(())
}
fn until(timeout: Duration) -> Result<Instant, String> {
    if timeout.is_zero() {
        return Err("native pointer timeout must be positive".into());
    }
    Instant::now()
        .checked_add(timeout)
        .ok_or("invalid native pointer deadline".into())
}
fn wait(fd: RawFd, events: i16, deadline: Instant, shared: &Shared) -> Result<(), String> {
    loop {
        if shared.stop.load(Ordering::Acquire) {
            return Err("native pointer stopped".into());
        }
        let left = deadline.saturating_duration_since(Instant::now());
        if left.is_zero() {
            return Err("native pointer control deadline expired".into());
        }
        let mut fd = libc::pollfd {
            fd,
            events,
            revents: 0,
        };
        let millis = left.min(POLL_SLICE).as_millis().max(1) as i32;
        // SAFETY: poll receives one valid writable pollfd for this owned pipe.
        let result = unsafe { libc::poll(&mut fd, 1, millis) };
        if result < 0 {
            let e = std::io::Error::last_os_error();
            if e.kind() == std::io::ErrorKind::Interrupted {
                continue;
            }
            return Err(e.to_string());
        }
        if result > 0 {
            if fd.revents & (libc::POLLNVAL | libc::POLLERR) != 0 {
                return Err("native pointer control pipe failed".into());
            }
            // A readable HUP may still contain the final command block; let the reader drain it.
            if fd.revents & (events | libc::POLLHUP) != 0 {
                return Ok(());
            }
        }
    }
}
fn quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\\''"))
}
fn command(target: &Target, flags: &[String], action: &str) -> String {
    let mut argv = vec![
        "client-pointer".into(),
        "-t".into(),
        target.tty.clone(),
        "-P".into(),
        target.client.to_string(),
        "-I".into(),
        target.client_instance.to_string(),
    ];
    argv.extend_from_slice(flags);
    argv.push(action.into());
    argv.iter().map(|s| quote(s)).collect::<Vec<_>>().join(" ") + "\n"
}
struct Control {
    child: Option<Child>,
    input: Option<ChildStdin>,
    output: ChildStdout,
    buffered: Vec<u8>,
    last: Option<u64>,
}
impl Control {
    fn spawn(target: &Target, timeout: Duration, shared: &Shared) -> Result<Self, String> {
        current(target)?;
        let mut command = crate::runtime::control_command("/usr/bin/tmux");
        command
            .args(["-S"])
            .arg(&target.socket)
            .args([
                "-N",
                "-C",
                "attach-session",
                "-f",
                "ignore-size,read-only,no-output",
                "-t",
            ])
            .arg(format!("={}", target.session_name))
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .process_group(0);
        let mut child = command
            .spawn()
            .map_err(|e| format!("native pointer control spawn: {e}"))?;
        let input = match child.stdin.take() {
            Some(input) => input,
            None => {
                finish_child(child);
                return Err("native pointer control stdin missing".into());
            }
        };
        let output = match child.stdout.take() {
            Some(output) => output,
            None => {
                drop(input);
                finish_child(child);
                return Err("native pointer control stdout missing".into());
            }
        };
        let mut control = Self {
            child: Some(child),
            input: Some(input),
            output,
            buffered: Vec::new(),
            last: None,
        };
        nonblocking(
            control
                .input
                .as_ref()
                .ok_or("native pointer control stopped")?
                .as_raw_fd(),
        )?;
        nonblocking(control.output.as_raw_fd())?;
        control.block(None, until(timeout)?, shared)?;
        let ack = control.exchange(target, &[], "begin", until(timeout)?, shared)?;
        if ack.visible || ack.buttons != 0 {
            return Err("native pointer begin was not hidden and neutral".into());
        }
        Ok(control)
    }
    fn line(&mut self, deadline: Instant, shared: &Shared) -> Result<String, String> {
        loop {
            if shared.stop.load(Ordering::Acquire) {
                return Err("native pointer stopped".into());
            }
            if let Some(end) = self.buffered.iter().position(|b| *b == b'\n') {
                if end > LINE_LIMIT {
                    return Err("tmux control line exceeds protocol bounds".into());
                }
                let mut line: Vec<_> = self.buffered.drain(..=end).collect();
                line.pop();
                if line.last() == Some(&b'\r') {
                    line.pop();
                }
                return String::from_utf8(line)
                    .map_err(|_| "tmux control line is not UTF-8".into());
            }
            if self.buffered.len() > LINE_LIMIT {
                return Err("tmux control line exceeds protocol bounds".into());
            }
            let mut bytes = [0u8; 4096];
            match self.output.read(&mut bytes) {
                Ok(0) => return Err("native pointer control disconnected".into()),
                Ok(n) => self.buffered.extend_from_slice(&bytes[..n]),
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    wait(self.output.as_raw_fd(), libc::POLLIN, deadline, shared)?
                }
                Err(e) => return Err(e.to_string()),
            }
            if Instant::now() >= deadline {
                return Err("native pointer reply deadline expired".into());
            }
        }
    }
    fn block(
        &mut self,
        instance: Option<u64>,
        deadline: Instant,
        shared: &Shared,
    ) -> Result<Option<Ack>, String> {
        let mut parser = Parser::new(self.last, instance);
        loop {
            if let Some(result) = parser.line(&self.line(deadline, shared)?)? {
                self.last = parser.last;
                return Ok(result);
            }
        }
    }
    fn exchange(
        &mut self,
        target: &Target,
        flags: &[String],
        action: &str,
        deadline: Instant,
        shared: &Shared,
    ) -> Result<Ack, String> {
        current(target)?;
        let line = command(target, flags, action);
        if line.len() > 4096 {
            return Err("native pointer command exceeds atomic pipe bound".into());
        }
        let input = self
            .input
            .as_mut()
            .ok_or("native pointer control stopped")?;
        loop {
            if shared.stop.load(Ordering::Acquire) {
                return Err("native pointer stopped".into());
            }
            if Instant::now() >= deadline {
                return Err("native pointer command expired".into());
            }
            match input.write(line.as_bytes()) {
                Ok(n) if n == line.len() => break,
                Ok(_) => return Err("native pointer control command was incomplete".into()),
                Err(e) if e.kind() == std::io::ErrorKind::Interrupted => {}
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {
                    wait(input.as_raw_fd(), libc::POLLOUT, deadline, shared)?
                }
                Err(e) => return Err(e.to_string()),
            }
        }
        self.block(Some(target.client_instance), deadline, shared)?
            .ok_or("missing native pointer acknowledgment".into())
    }
}
fn finish_child(mut child: Child) {
    match child.try_wait() {
        Ok(Some(_)) | Err(_) => return,
        Ok(None) => {}
    }
    // SAFETY: this positive PID belongs to our unreaped child created with process_group(0), never a human server.
    unsafe {
        libc::kill(-(child.id() as i32), libc::SIGKILL);
    }
    let deadline = Instant::now() + Duration::from_millis(200);
    loop {
        match child.try_wait() {
            Ok(Some(_)) | Err(_) => return,
            Ok(None) => {}
        }
        if Instant::now() >= deadline {
            break;
        }
        std::thread::sleep(Duration::from_millis(2));
    }
    // A kernel-uninterruptible child is still ours; a separate owner reaps it without blocking actor/render.
    let _ = std::thread::Builder::new()
        .name("mios-pointer-reaper".into())
        .spawn(move || {
            let _ = child.wait();
        });
}
impl Drop for Control {
    fn drop(&mut self) {
        self.input.take(); // EOF first; native control-disconnect cleanup cancels presses and chrome gestures.
        if let Some(child) = self.child.take() {
            finish_child(child);
        }
    }
}
fn step(
    control: &mut Control,
    target: &Target,
    pending: Pending,
    timeout: Duration,
    shared: &Shared,
) -> Result<(), String> {
    let deadline = pending
        .queued
        .checked_add(timeout)
        .ok_or("invalid native pointer queue deadline")?;
    let mut exchange = |flags: Vec<String>, action: &str| {
        control.exchange(target, &flags, action, deadline, shared)
    };
    match pending.action {
        Action::Visible(visible) => {
            let ack = exchange(vec![], if visible { "show" } else { "hide" })?;
            if ack.visible != visible || (!visible && ack.buttons != 0) {
                return Err("native pointer visibility did not settle neutrally".into());
            }
        }
        Action::Button(button, down) => {
            exchange(
                vec!["-b".into(), button.name().into()],
                if down { "down" } else { "up" },
            )?;
        }
        Action::Motion(mut dx, mut dy) => {
            while dx != 0 || dy != 0 {
                let x = dx.clamp(-64, 64);
                let y = dy.clamp(-64, 64);
                exchange(
                    vec![
                        "-r".into(),
                        "-x".into(),
                        x.to_string(),
                        "-y".into(),
                        y.to_string(),
                    ],
                    "move",
                )?;
                dx -= x;
                dy -= y;
            }
        }
        Action::Wheel(mut dx, mut dy) => {
            while dx != 0 || dy != 0 {
                let x = dx.clamp(-32, 32);
                let y = dy.clamp(-(32 - x.abs()), 32 - x.abs());
                exchange(
                    vec!["-x".into(), x.to_string(), "-y".into(), y.to_string()],
                    "wheel",
                )?;
                dx -= x;
                dy -= y;
            }
        }
    }
    Ok(())
}
fn run(target: Target, timeout: Duration, shared: &Shared) -> Result<(), String> {
    let mut control = Control::spawn(&target, timeout, shared)?;
    while !shared.stop.load(Ordering::Acquire) {
        current(&target)?;
        let next = {
            let mut queue = shared
                .queue
                .lock()
                .map_err(|_| "native pointer queue poisoned")?;
            if queue.commands.is_empty() {
                let result = shared
                    .wake
                    .wait_timeout(queue, timeout.min(Duration::from_millis(100)))
                    .map_err(|_| "native pointer queue poisoned")?;
                queue = result.0;
            }
            queue.commands.pop_front()
        };
        if let Some(next) = next {
            step(&mut control, &target, next, timeout, shared)?;
        }
    }
    Ok(())
}
/// One persistent CONTROL client owns this cursor lease. Closing it is the authoritative native cleanup boundary.
pub(crate) struct PointerWorker {
    shared: Arc<Shared>,
    cell: [u32; 2],
    worker: Option<std::thread::JoinHandle<()>>,
}
impl PointerWorker {
    pub(crate) fn spawn(
        target: Target,
        cell_px: [u32; 2],
        timeout: Duration,
    ) -> Result<Self, String> {
        if cell_px.contains(&0) {
            return Err("native pointer cell dimensions must be positive".into());
        }
        until(timeout)?;
        current(&target)?;
        let shared = Arc::new(Shared::new());
        let state = shared.clone();
        let worker = std::thread::Builder::new()
            .name("mios-native-pointer".into())
            .spawn(move || {
                let result = run(target, timeout, &state);
                let intentional = state.stop.load(Ordering::Acquire);
                state.fail();
                if let Err(error) = result {
                    if !intentional {
                        eprintln!(
                            "{}",
                            serde_json::json!({"native_pointer":"stopped","error":error})
                        );
                    }
                }
            })
            .map_err(|e| e.to_string())?;
        Ok(Self {
            shared,
            cell: cell_px,
            worker: Some(worker),
        })
    }
    pub(crate) fn submit(&self, events: &[Event]) -> bool {
        if !self.alive() {
            return false;
        }
        let result = self
            .shared
            .queue
            .lock()
            .map_err(|_| ())
            .and_then(|mut queue| queue.events(events, self.cell).map_err(|_| ()));
        if result.is_err() {
            self.shared.fail();
            return false;
        }
        self.shared.wake.notify_one();
        self.alive()
    }
    pub(crate) fn visible(&self, visible: bool) -> bool {
        if !self.alive() {
            return false;
        }
        let result = self
            .shared
            .queue
            .lock()
            .map_err(|_| ())
            .and_then(|mut queue| queue.visibility(visible).map_err(|_| ()));
        if result.is_err() {
            self.shared.fail();
            return false;
        }
        self.shared.wake.notify_one();
        self.alive()
    }
    pub(crate) fn alive(&self) -> bool {
        self.shared.alive.load(Ordering::Acquire) && !self.shared.stop.load(Ordering::Acquire)
    }
}
impl Drop for PointerWorker {
    fn drop(&mut self) {
        self.shared.fail();
        // Detach the worker after signaling. Every pipe poll observes stop within 20ms; owned cleanup never joins render.
        self.worker.take();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn target() -> Target {
        serde_json::from_value(serde_json::json!({
            "uid":1000,"generation":8,"login_generation":3,"socket":"/tmp/tmux-1000/mios-desktop",
            "socket_dev":1,"socket_ino":2,"server":40,"server_start":400,"client":50,"client_start":500,
            "client_instance":7,"tty":"/dev/pts/3","session":"$1","session_name":"desk",
            "window":"@2","pane":"%4","pane_pid":60,"pane_start":600,"foreground":70,
            "foreground_start":700,"zoomed":false
        })).unwrap()
    }
    fn actions(queue: &Queue) -> Vec<Action> {
        queue.commands.iter().map(|p| p.action.clone()).collect()
    }
    #[test]
    fn strict_v1_acknowledgments_reject_stale_and_unbounded_values() {
        assert_eq!(
            parse_ack("pointer-v1 7 123 45 1 5", 7).unwrap(),
            Ack {
                instance: 7,
                x: 123,
                y: 45,
                visible: true,
                buttons: 5
            }
        );
        for line in [
            "pointer-v1 8 1 2 1 0",
            "pointer-v1 0 1 2 1 0",
            "pointer-v1 7 -1 2 1 0",
            "pointer-v1 7 65536 2 1 0",
            "pointer-v1 7 1 2 2 0",
            "pointer-v1 7 1 2 1 8",
            "pointer-v1 7 1 2 1 0 extra",
            "pointer-v2 7 1 2 1 0",
            "pointer-v1 +7 1 2 1 0",
        ] {
            assert!(parse_ack(line, 7).is_err(), "{line}");
        }
    }
    #[test]
    fn control_parser_correlates_blocks_and_ignores_only_outside_notifications() {
        let mut initial = Parser::new(None, None);
        assert!(initial.line("%session-changed $1 desk").unwrap().is_none());
        assert!(initial.line("%begin 100 20 0").unwrap().is_none());
        assert_eq!(initial.line("%end 100 20 0").unwrap(), Some(None));
        let mut reply = Parser::new(initial.last, Some(7));
        assert!(reply.line("%window-renamed @2 title").unwrap().is_none());
        assert!(reply.line("%begin 101 21 1").unwrap().is_none());
        assert!(reply.line("pointer-v1 7 80 20 0 0").unwrap().is_none());
        assert_eq!(
            reply.line("%end 101 21 1").unwrap(),
            Some(Some(Ack {
                instance: 7,
                x: 80,
                y: 20,
                visible: false,
                buttons: 0
            }))
        );
        assert_eq!(reply.last, Some(21));
    }
    #[test]
    fn control_errors_replays_duplicate_acks_and_wrong_end_fail_closed() {
        for lines in [
            vec!["%begin 100 21 1", "pointer-v1 7 1 2 0 0", "%end 100 22 1"],
            vec![
                "%begin 100 21 1",
                "pointer-v1 7 1 2 0 0",
                "pointer-v1 7 1 2 0 0",
            ],
            vec!["%begin 100 21 1", "%error 100 21 1"],
            vec!["%begin 100 20 1"],
            vec!["%begin 100 21 1", "%begin 100 22 1"],
            vec!["%begin 100 21 1", "%end 100 21 1"],
            vec!["%exit"],
        ] {
            let mut parser = Parser::new(Some(20), Some(7));
            assert!(lines.into_iter().any(|line| parser.line(line).is_err()));
        }
        let mut parser = Parser::new(None, Some(7));
        assert!(parser.line(&"x".repeat(LINE_LIMIT + 1)).is_err());
    }
    #[test]
    fn pixel_fractions_accumulate_both_signs_and_motion_never_crosses_edges() {
        let mut queue = Queue::default();
        queue.visibility(true).unwrap();
        queue
            .events(
                &[Event::new(EV_REL, 0, 7), Event::new(EV_REL, 1, -15)],
                [8, 16],
            )
            .unwrap();
        assert_eq!(actions(&queue), vec![Action::Visible(true)]);
        queue
            .events(
                &[Event::new(EV_REL, 0, 1), Event::new(EV_REL, 1, -1)],
                [8, 16],
            )
            .unwrap();
        let first = queue.commands.back().unwrap().queued;
        queue.events(&[Event::new(EV_REL, 0, 16)], [8, 16]).unwrap();
        assert_eq!(queue.commands.back().unwrap().queued, first); // Coalescing never renews an old request's deadline.
        queue
            .events(
                &[
                    Event::new(EV_KEY, 0x110, 1),
                    Event::new(EV_REL, 0, -8),
                    Event::new(EV_KEY, 0x110, 0),
                ],
                [8, 16],
            )
            .unwrap();
        assert_eq!(
            actions(&queue),
            vec![
                Action::Visible(true),
                Action::Motion(3, -1),
                Action::Button(Button::Left, true),
                Action::Motion(-1, 0),
                Action::Button(Button::Left, false)
            ]
        );
        assert_eq!(queue.remainder, [0, 0]);
    }
    #[test]
    fn button_mapping_wheel_directions_and_hide_neutralization_preserve_order() {
        let mut queue = Queue::default();
        queue.visibility(true).unwrap();
        queue
            .events(
                &[
                    Event::new(EV_KEY, 0x111, 1),
                    Event::new(EV_KEY, 0x112, 1),
                    Event::new(EV_REL, 8, 1),
                    Event::new(EV_REL, 6, 1),
                    Event::new(crate::router::EV_SYN, 0, 0),
                ],
                [8, 16],
            )
            .unwrap();
        assert_eq!(queue.buttons, Button::Right.mask() | Button::Middle.mask());
        queue.visibility(false).unwrap();
        queue
            .events(
                &[Event::new(EV_KEY, 0x111, 0), Event::new(EV_REL, 0, 100)],
                [8, 16],
            )
            .unwrap();
        assert_eq!(queue.buttons, 0);
        assert_eq!(queue.remainder, [0, 0]);
        assert_eq!(
            actions(&queue),
            vec![
                Action::Visible(true),
                Action::Button(Button::Right, true),
                Action::Button(Button::Middle, true),
                Action::Wheel(0, 1),
                Action::Wheel(1, 0),
                Action::Visible(false)
            ]
        );
    }
    #[test]
    fn edge_overflow_stops_actor_without_dropping_or_reordering_held_edges() {
        let shared = Arc::new(Shared::new());
        {
            let mut queue = shared.queue.lock().unwrap();
            for n in 0..QUEUE_LIMIT {
                queue
                    .push(Action::Button(Button::Left, n % 2 == 0))
                    .unwrap();
            }
        }
        let worker = PointerWorker {
            shared: shared.clone(),
            cell: [8, 16],
            worker: None,
        };
        assert!(!worker.visible(true));
        assert!(!worker.alive());
        let queue = shared.queue.lock().unwrap();
        assert_eq!(queue.commands.len(), QUEUE_LIMIT);
        for (n, command) in queue.commands.iter().enumerate() {
            assert_eq!(command.action, Action::Button(Button::Left, n % 2 == 0));
        }
    }
    #[test]
    fn pointer_scope_survives_pane_capture_change_but_never_login_or_client_reuse() {
        let a = target();
        let mut b = a.clone();
        b.pane = "%9".into();
        b.foreground = 88;
        b.generation += 1;
        b.window = "@9".into();
        assert_eq!(Scope::of(&a), Scope::of(&b));
        for which in 0..8 {
            let mut b = a.clone();
            match which {
                0 => b.uid += 1,
                1 => b.socket_ino += 1,
                2 => b.server_start += 1,
                3 => b.client_start += 1,
                4 => b.client_instance += 1,
                5 => b.login_generation += 1,
                6 => b.client += 1,
                _ => b.socket = "/tmp/another".into(),
            }
            assert_ne!(Scope::of(&a), Scope::of(&b));
        }
    }
    #[test]
    fn pointer_command_quotes_exact_target_and_places_all_flags_before_action() {
        let target = target();
        let line = command(
            &target,
            &[
                "-r".into(),
                "-x".into(),
                "-64".into(),
                "-y".into(),
                "64".into(),
            ],
            "move",
        );
        let words = shlex::split(line.trim()).unwrap();
        assert_eq!(
            words,
            [
                "client-pointer",
                "-t",
                "/dev/pts/3",
                "-P",
                "50",
                "-I",
                "7",
                "-r",
                "-x",
                "-64",
                "-y",
                "64",
                "move"
            ]
        );
        assert!(line.ends_with("'move'\n"));
    }
}
