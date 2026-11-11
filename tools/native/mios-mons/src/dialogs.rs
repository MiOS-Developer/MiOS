// AI-hint: Bounded native tmux dialogues with private replies and explicit inline fallback.
// AI-related: input.rs, config.rs, ui.rs, task_3717
#![forbid(unsafe_code)]

use clap::ValueEnum;
use crossterm::{
    event::{
        self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyEvent, KeyEventKind,
        KeyModifiers, MouseButton, MouseEventKind,
    },
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{
    backend::CrosstermBackend,
    layout::{Constraint, Layout, Rect},
    style::{Modifier, Style},
    text::{Line, Text},
    widgets::{Block, BorderType, Borders, List, ListItem, ListState, Paragraph, Wrap},
    Frame, Terminal,
};
use serde::{Deserialize, Serialize};
use std::{
    fs::{self, File, OpenOptions},
    io::{self, IsTerminal, Read, Write},
    os::unix::{
        fs::{MetadataExt, OpenOptionsExt, PermissionsExt},
        process::CommandExt,
    },
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{
        atomic::{AtomicBool, Ordering},
        mpsc::{self, Receiver, Sender},
        Arc,
    },
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};
use tempfile::{Builder, NamedTempFile};

const PREFIX: &str = "mios-mons-dialog-";
const HARD_MAX_BYTES: usize = 1024 * 1024;
// Linux open(2) flags, used only through the safe standard-library interface.
const PRIVATE_OPEN_FLAGS: i32 = 0x20000 | 0x800; // O_NOFOLLOW | O_NONBLOCK

#[derive(Clone, Copy, Debug, PartialEq, Eq, Deserialize, ValueEnum)]
#[serde(rename_all = "kebab-case")]
#[value(rename_all = "kebab-case")]
pub enum DialogMode {
    Auto,
    Inline,
    Native,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize, ValueEnum)]
#[serde(rename_all = "kebab-case")]
#[value(rename_all = "kebab-case")]
pub enum Choice {
    Listen,
    Feed,
    Play,
    Clean,
    Rest,
    Medicine,
    Status,
    Graphics,
    Roster,
    Pause,
    Save,
    Quit,
    Back,
    Habitat,
    Training,
}
impl Choice {
    pub fn label(&self) -> &str {
        match self {
            Self::Listen => "Listen",
            Self::Feed => "Feed",
            Self::Play => "Play",
            Self::Clean => "Clean",
            Self::Rest => "Rest",
            Self::Medicine => "Medicine",
            Self::Status => "Status",
            Self::Graphics => "Graphics",
            Self::Roster => "Roster",
            Self::Pause => "Pause / resume",
            Self::Save => "Save",
            Self::Quit => "Quit",
            Self::Back => "Back",
            Self::Habitat => "Habitat / items",
            Self::Training => "Training / stats",
        }
    }
    fn argument(self) -> &'static str {
        match self {
            Self::Listen => "listen",
            Self::Feed => "feed",
            Self::Play => "play",
            Self::Clean => "clean",
            Self::Rest => "rest",
            Self::Medicine => "medicine",
            Self::Status => "status",
            Self::Graphics => "graphics",
            Self::Roster => "roster",
            Self::Pause => "pause",
            Self::Save => "save",
            Self::Quit => "quit",
            Self::Back => "back",
            Self::Habitat => "habitat",
            Self::Training => "training",
        }
    }
}
#[derive(Clone, Debug, Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct DialogRequest {
    pub title: String,
    pub text: String,
    pub choices: Vec<Choice>,
    pub accept_hint: String,
    pub back_hint: String,
    pub ascii: bool,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum DialogEvent {
    Opened,
    Selected(Choice),
    Closed,
    Inline,
}

#[derive(Clone)]
struct Settings {
    mode: DialogMode,
    timeout: Duration,
    query_ms: u64,
    max_bytes: usize,
    min_width: u16,
    min_height: u16,
    popup_policy: Option<mios_resolver::popup::PopupPolicy>,
}
struct Flight {
    events: Receiver<DialogEvent>,
    stop: Arc<AtomicBool>,
    worker: Option<std::thread::JoinHandle<()>>,
}
pub struct NativeDialogs {
    settings: Settings,
    flight: Option<Flight>,
}
impl NativeDialogs {
    pub fn new(config: &crate::config::Config) -> Self {
        Self {
            settings: Settings {
                mode: config.dialog_mode,
                timeout: Duration::from_millis(config.dialog_timeout_ms.clamp(1, 86_400_000)),
                query_ms: config.dialog_query_timeout_ms.clamp(1, 2000),
                max_bytes: config.dialog_max_bytes.clamp(1, HARD_MAX_BYTES),
                min_width: config.popup_min_width.max(20),
                min_height: config.popup_min_height.max(8),
                popup_policy: config.popup_policy.clone(),
            },
            flight: None,
        }
    }
    /// Queued means the asynchronous safety query started; Inline can follow.
    pub fn request(&mut self, request: DialogRequest, context_menu: bool) -> bool {
        if self.active() || self.settings.mode == DialogMode::Inline {
            return false;
        }
        let (sender, events) = mpsc::channel();
        let stop = Arc::new(AtomicBool::new(false));
        let cancel = stop.clone();
        let settings = self.settings.clone();
        let Ok(worker) = std::thread::Builder::new()
            .name("mios-native-dialog".into())
            .spawn(move || {
                let result = run_dialog(&settings, request, context_menu, &cancel, &sender);
                if !cancel.load(Ordering::Relaxed) {
                    let _ = sender.send(result.unwrap_or(DialogEvent::Inline));
                }
            })
        else {
            return false;
        };
        self.flight = Some(Flight {
            events,
            stop,
            worker: Some(worker),
        });
        true
    }
    pub fn poll(&mut self) -> Option<DialogEvent> {
        let flight = self.flight.as_ref()?;
        let event = match flight.events.try_recv() {
            Ok(event) => event,
            Err(mpsc::TryRecvError::Empty) => return None,
            Err(mpsc::TryRecvError::Disconnected) => DialogEvent::Inline,
        };
        if event != DialogEvent::Opened {
            self.flight = None;
        }
        Some(event)
    }
    pub fn active(&self) -> bool {
        self.flight.is_some()
    }
}
impl Drop for NativeDialogs {
    fn drop(&mut self) {
        if let Some(mut flight) = self.flight.take() {
            flight.stop.store(true, Ordering::Relaxed);
            // Exit alone waits for cleanup: the target query has a three-query
            // deadline plus one bounded kill utility; our child kill adds 100ms.
            // Poll and request never join or wait. The worker removes its private
            // files before finishing, causing a server-spawned helper to close.
            if let Some(worker) = flight.worker.take() {
                let deadline =
                    Instant::now() + Duration::from_millis(self.settings.query_ms * 4 + 250);
                while !worker.is_finished() && Instant::now() < deadline {
                    std::thread::sleep(Duration::from_millis(2));
                }
                if worker.is_finished() {
                    let _ = worker.join();
                }
            }
        }
    }
}

#[derive(Serialize, Deserialize)]
#[serde(deny_unknown_fields)]
struct Payload {
    request: DialogRequest,
    reply: PathBuf,
    expires_ms: u64,
    max_bytes: usize,
}

fn now_ms() -> Result<u64, String> {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_millis()
        .try_into()
        .map_err(|_| "Clock out of range".into())
}
fn own_uid() -> Result<u32, String> {
    fs::metadata("/proc/self")
        .map(|m| m.uid())
        .map_err(|e| e.to_string())
}
fn runtime_dir() -> Result<PathBuf, String> {
    let path =
        PathBuf::from(std::env::var_os("XDG_RUNTIME_DIR").ok_or("Missing runtime directory")?);
    let meta = fs::symlink_metadata(&path).map_err(|e| e.to_string())?;
    if !path.is_absolute()
        || !meta.is_dir()
        || meta.uid() != own_uid()?
        || meta.permissions().mode() & 0o077 != 0
        || fs::canonicalize(&path).map_err(|e| e.to_string())? != path
    {
        return Err("Runtime directory is not private and canonical".into());
    }
    Ok(path)
}
fn private_file(path: &Path, write: bool) -> Result<File, String> {
    private_file_at(path, write, &runtime_dir()?)
}
fn private_file_at(path: &Path, write: bool, runtime: &Path) -> Result<File, String> {
    if path.parent() != Some(runtime)
        || !path
            .file_name()
            .and_then(|v| v.to_str())
            .is_some_and(|v| v.starts_with(PREFIX) && v.len() > PREFIX.len())
    {
        return Err("Not a dialogue runtime file".into());
    }
    let before = fs::symlink_metadata(path).map_err(|e| e.to_string())?;
    let file = OpenOptions::new()
        .read(true)
        .write(write)
        .custom_flags(PRIVATE_OPEN_FLAGS)
        .open(path)
        .map_err(|e| e.to_string())?;
    let meta = file.metadata().map_err(|e| e.to_string())?;
    if !meta.is_file()
        || meta.uid() != own_uid()?
        || meta.nlink() != 1
        || meta.permissions().mode() & 0o7777 != 0o600
        || meta.dev() != before.dev()
        || meta.ino() != before.ino()
    {
        return Err("Dialogue file is not a private owned regular file".into());
    }
    Ok(file)
}
fn bounded_read(file: &mut File, limit: usize) -> Result<Vec<u8>, String> {
    let mut bytes = Vec::new();
    file.take(limit.saturating_add(1) as u64)
        .read_to_end(&mut bytes)
        .map_err(|e| e.to_string())?;
    if bytes.len() > limit {
        return Err("Dialogue exceeds byte limit".into());
    }
    Ok(bytes)
}
fn temporary(dir: &Path) -> Result<NamedTempFile, String> {
    let file = Builder::new()
        .prefix(PREFIX)
        .tempfile_in(dir)
        .map_err(|e| e.to_string())?;
    file.as_file()
        .set_permissions(fs::Permissions::from_mode(0o600))
        .map_err(|e| e.to_string())?;
    Ok(file)
}
fn helper_exe() -> Result<PathBuf, String> {
    let exe = fs::canonicalize(std::env::current_exe().map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    let m = fs::metadata(&exe).map_err(|e| e.to_string())?;
    if !m.is_file()
        || (m.uid() != 0 && m.uid() != own_uid()?)
        || m.permissions().mode() & 0o022 != 0
    {
        return Err("Helper executable is not trusted".into());
    }
    Ok(exe)
}
fn validate_request(request: &DialogRequest, limit: usize) -> Result<(), String> {
    if request.choices.is_empty()
        || request.choices.len() > Choice::value_variants().len()
        || request
            .choices
            .iter()
            .enumerate()
            .any(|(index, choice)| request.choices[..index].contains(choice))
        || serde_json::to_vec(request)
            .map_err(|e| e.to_string())?
            .len()
            > limit
    {
        return Err("Invalid dialogue size or choices".into());
    }
    Ok(())
}
fn same_target(a: &crate::input::DialogTarget, b: &crate::input::DialogTarget) -> bool {
    a.socket == b.socket && a.pane == b.pane && a.client == b.client
}

// A direct argv command is used for popups. Only the menu callback crosses a
// tmux parser and a shell; it contains trusted helper/file arguments, never text.
fn shell_quote(value: &str) -> String {
    format!("'{}'", value.replace('\'', "'\\''"))
}
fn tmux_quote(value: &str) -> String {
    let mut out = String::from("\"");
    for c in value.chars() {
        if matches!(c, '\\' | '"' | '$' | '`') {
            out.push('\\');
        }
        out.push(c);
    }
    out.push('"');
    out
}
fn callback(exe: &Path, reply: &Path, choice: Choice) -> Result<String, String> {
    let exe = exe.to_str().ok_or("Non UTF-8 helper path")?;
    let runtime = reply
        .parent()
        .and_then(Path::to_str)
        .ok_or("Non UTF-8 runtime directory")?;
    let reply = reply.to_str().ok_or("Non UTF-8 reply path")?;
    if exe.chars().chain(reply.chars()).any(char::is_control) {
        return Err("Control in callback path".into());
    }
    let shell = format!(
        "(XDG_RUNTIME_DIR={} {} --dialog-reply {} --dialog-choice {}) >/dev/null 2>&1; :",
        shell_quote(runtime),
        shell_quote(exe),
        shell_quote(reply),
        shell_quote(choice.argument())
    );
    // display-menu expands its command format, then run-shell expands again.
    // Four hashes survive both boundaries as one literal hash inside shell quotes.
    Ok(format!(
        "run-shell -b {}",
        tmux_quote(&shell.replace('#', "####"))
    ))
}
fn visible(value: &str, ascii: bool) -> String {
    value
        .chars()
        .filter(|c| !c.is_control() || *c == '\n' || *c == '\t')
        .map(|c| if ascii && !c.is_ascii() { '?' } else { c })
        .collect()
}
fn tmux_title(value: &str, ascii: bool) -> String {
    // A tmux title is a format string. Never let untrusted #() invoke a job.
    visible(value, ascii)
        .replace('#', "?")
        .replace(['\n', '\t'], " ")
}
fn command_args(
    target: &crate::input::DialogTarget,
    exe: &Path,
    payload: &Path,
    reply: &Path,
    request: &DialogRequest,
    menu: bool,
    policy: Option<&mios_resolver::popup::PopupPolicy>,
) -> Result<Vec<std::ffi::OsString>, String> {
    use std::ffi::OsString;
    let mut args: Vec<OsString> = vec![
        "-S".into(),
        target.socket.as_os_str().into(),
        if menu {
            "display-menu".into()
        } else {
            "display-popup".into()
        },
        "-c".into(),
        target.client.clone().into(),
        "-t".into(),
        target.pane.clone().into(),
        "-T".into(),
        tmux_title(&request.title, request.ascii).into(),
    ];
    if request.ascii {
        args.extend(["-b".into(), "simple".into()]);
    }
    args.extend(["-x".into(), "C".into(), "-y".into(), "C".into()]);
    if menu {
        // This command is launched from the game, not a tmux mouse key binding.
        args.extend(["-M".into(), "-O".into()]);
        for (index, choice) in request.choices.iter().enumerate() {
            args.push(choice.label().into());
            args.push(format!("{:x}", index + 1).into());
            args.push(callback(exe, reply, *choice)?.into());
        }
    } else {
        let runtime = reply.parent().ok_or("Missing runtime directory")?;
        let mut environment = std::ffi::OsString::from("XDG_RUNTIME_DIR=");
        environment.push(runtime);
        let (width, max_height) = policy.filter(|p| p.enabled).map_or(
            (
                target.width.saturating_sub(4).clamp(20, 90),
                target.height.saturating_sub(2).clamp(8, 30),
            ),
            |p| {
                p.size(
                    mios_resolver::popup::SurfaceKind::Dialog,
                    target.width,
                    target.height,
                )
            },
        );
        let height = if policy.is_some_and(|p| p.enabled) {
            let text_rows = request
                .text
                .chars()
                .count()
                .div_ceil(usize::from(width.saturating_sub(2).max(1)))
                as u16;
            (request.choices.len() as u16)
                .saturating_add(text_rows)
                .saturating_add(5)
                .min(max_height)
        } else {
            popup_height(target, request)
        };
        args.extend([
            "-e".into(),
            environment,
            "-E".into(),
            "-w".into(),
            width.to_string().into(),
            "-h".into(),
            height.to_string().into(),
            exe.as_os_str().into(),
            "--dialog".into(),
            payload.as_os_str().into(),
        ]);
    }
    Ok(args)
}

fn popup_height(target: &crate::input::DialogTarget, request: &DialogRequest) -> u16 {
    let width = usize::from(
        target
            .width
            .saturating_sub(4)
            .clamp(20, 90)
            .saturating_sub(2),
    )
    .max(1);
    let text_rows = request.text.len().div_ceil(width).clamp(1, 8) as u16;
    (request.choices.len() as u16 + text_rows + 5)
        .clamp(8, target.height.saturating_sub(2).clamp(8, 30))
}

struct OwnedChild(Child);
impl OwnedChild {
    fn cancel(&mut self) {
        // Only this child's freshly created process group is ever signalled.
        if let Ok(mut kill) = Command::new("/usr/bin/kill")
            .args(["-KILL", "--", &format!("-{}", self.0.id())])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn()
        {
            let until = Instant::now() + Duration::from_millis(100);
            loop {
                match kill.try_wait() {
                    Ok(Some(_)) => break,
                    Ok(None) if Instant::now() < until => {
                        std::thread::sleep(Duration::from_millis(2))
                    }
                    _ => {
                        let _ = kill.kill();
                        let _ = kill.wait();
                        break;
                    }
                }
            }
        }
        let _ = self.0.kill();
        let _ = self.0.wait();
    }
}
impl Drop for OwnedChild {
    fn drop(&mut self) {
        // Never signal a recycled process-group number after the child is reaped.
        if !matches!(self.0.try_wait(), Ok(Some(_))) {
            self.cancel();
        }
    }
}
fn response(path: &Path) -> Result<Option<Choice>, String> {
    response_at(path, &runtime_dir()?)
}
fn response_at(path: &Path, runtime: &Path) -> Result<Option<Choice>, String> {
    let mut file = private_file_at(path, false, runtime)?;
    let bytes = bounded_read(&mut file, 128)?;
    if bytes.is_empty() {
        return Ok(None);
    }
    // A callback may be in the middle of its one bounded write. Recheck next tick.
    match serde_json::from_slice(&bytes) {
        Ok(choice) => Ok(Some(choice)),
        Err(e) if e.is_eof() => Ok(None),
        Err(e) => Err(e.to_string()),
    }
}
fn run_dialog(
    settings: &Settings,
    request: DialogRequest,
    menu: bool,
    stop: &AtomicBool,
    events: &Sender<DialogEvent>,
) -> Result<DialogEvent, String> {
    let deadline = Instant::now() + settings.timeout;
    validate_request(&request, settings.max_bytes)?;
    if stop.load(Ordering::Relaxed) || Instant::now() >= deadline {
        return Err("Cancelled or expired before query".into());
    }
    let target = crate::input::attached_dialog_target(
        settings.query_ms,
        settings.max_bytes,
        settings.min_width,
        settings.min_height,
    )
    .ok_or("No verified attached client")?;
    if stop.load(Ordering::Relaxed) || Instant::now() >= deadline {
        return Err("Cancelled".into());
    }
    let dir = runtime_dir()?;
    let reply = temporary(&dir)?;
    let mut payload = temporary(&dir)?;
    let body = Payload {
        request: request.clone(),
        reply: reply.path().into(),
        expires_ms: now_ms()?.saturating_add(settings.timeout.as_millis() as u64),
        max_bytes: settings.max_bytes,
    };
    let bytes = serde_json::to_vec(&body).map_err(|e| e.to_string())?;
    if bytes.len() > settings.max_bytes {
        return Err("Payload exceeds byte limit".into());
    }
    payload.write_all(&bytes).map_err(|e| e.to_string())?;
    let exe = helper_exe()?;
    let args = command_args(
        &target,
        &exe,
        payload.path(),
        reply.path(),
        &request,
        menu,
        settings.popup_policy.as_ref(),
    )?;
    let stderr = temporary(&dir)?;
    let child = Command::new("tmux")
        .args(args)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(stderr.reopen().map_err(|e| e.to_string())?)
        .process_group(0)
        .spawn()
        .map_err(|e| e.to_string())?;
    let mut child = OwnedChild(child);
    let _ = events.send(DialogEvent::Opened);
    let mut next_verify = Instant::now() + Duration::from_millis(settings.query_ms.max(100));
    let mut finished = None;
    loop {
        if stop.load(Ordering::Relaxed) || Instant::now() >= deadline {
            return Err("Dialogue cancelled or timed out".into());
        }
        if stderr
            .as_file()
            .metadata()
            .map_err(|e| e.to_string())?
            .len()
            > settings.max_bytes as u64
        {
            return Err("tmux error output exceeds byte limit".into());
        }
        if let Some(choice) = response(reply.path())? {
            if choice != Choice::Back && !request.choices.contains(&choice) {
                return Err("Unrequested choice".into());
            }
            return Ok(if choice == Choice::Back {
                DialogEvent::Closed
            } else {
                DialogEvent::Selected(choice)
            });
        }
        if let Some(status) = child.0.try_wait().map_err(|e| e.to_string())? {
            if !status.success() {
                return Err("tmux rejected native dialogue".into());
            }
            // run-shell -b menu replies can arrive just after display-menu exits.
            let exited = *finished.get_or_insert_with(Instant::now);
            if exited.elapsed() >= Duration::from_millis(settings.query_ms.max(100)) {
                return Ok(DialogEvent::Closed);
            }
        }
        if Instant::now() >= next_verify {
            let current = crate::input::attached_dialog_target(
                settings.query_ms,
                settings.max_bytes,
                settings.min_width,
                settings.min_height,
            )
            .ok_or("Client detached or focus changed")?;
            if !same_target(&target, &current) {
                return Err("Client changed".into());
            }
            next_verify = Instant::now() + Duration::from_millis(settings.query_ms.max(100));
        }
        std::thread::sleep(Duration::from_millis(10));
    }
}

/// The hidden CLI branch calls this before opening any configuration or save.
pub fn reply_helper(path: &Path, choice: Choice) -> Result<(), String> {
    reply_at(path, choice, &runtime_dir()?)
}
fn reply_at(path: &Path, choice: Choice, runtime: &Path) -> Result<(), String> {
    let mut file = private_file_at(path, true, runtime)?;
    file.try_lock().map_err(|e| e.to_string())?;
    if file.metadata().map_err(|e| e.to_string())?.len() != 0 {
        return Err("Reply already written".into());
    }
    let bytes = serde_json::to_vec(&choice).map_err(|e| e.to_string())?;
    file.write_all(&bytes).map_err(|e| e.to_string())?;
    file.flush().map_err(|e| e.to_string())
}
fn load_payload(path: &Path) -> Result<Payload, String> {
    load_payload_at(path, &runtime_dir()?)
}
fn load_payload_at(path: &Path, runtime: &Path) -> Result<Payload, String> {
    let mut file = private_file_at(path, false, runtime)?;
    let bytes = bounded_read(&mut file, HARD_MAX_BYTES)?;
    let payload: Payload = serde_json::from_slice(&bytes).map_err(|e| e.to_string())?;
    if payload.max_bytes == 0
        || payload.max_bytes > HARD_MAX_BYTES
        || bytes.len() > payload.max_bytes
    {
        return Err("Invalid payload byte limit".into());
    }
    validate_request(&payload.request, payload.max_bytes)?;
    let now = now_ms()?;
    if payload.expires_ms <= now || payload.expires_ms.saturating_sub(now) > 86_400_000 {
        return Err("Expired dialogue".into());
    }
    let reply = private_file_at(&payload.reply, true, runtime)?;
    if reply.metadata().map_err(|e| e.to_string())?.len() != 0 || payload.reply == path {
        return Err("Reply must be empty and separate".into());
    }
    Ok(payload)
}
struct TerminalGuard;
impl Drop for TerminalGuard {
    fn drop(&mut self) {
        let _ = disable_raw_mode();
        let _ = execute!(io::stdout(), DisableMouseCapture, LeaveAlternateScreen);
    }
}
#[derive(Debug, PartialEq, Eq)]
enum HelperKey {
    Previous,
    Next,
    ScrollUp,
    ScrollDown,
    ScrollStart,
    ScrollEnd,
    Accept,
    Back,
}
fn helper_key(key: KeyEvent) -> Option<HelperKey> {
    if key.kind == KeyEventKind::Release {
        return None;
    }
    if key.modifiers.contains(KeyModifiers::CONTROL) {
        return (key.kind == KeyEventKind::Press && key.code == KeyCode::Char('c'))
            .then_some(HelperKey::Back);
    }
    if key.modifiers.intersects(
        KeyModifiers::ALT | KeyModifiers::SUPER | KeyModifiers::META | KeyModifiers::HYPER,
    ) {
        return None;
    }
    match key.code {
        KeyCode::Up | KeyCode::Left | KeyCode::BackTab => Some(HelperKey::Previous),
        KeyCode::Down | KeyCode::Right | KeyCode::Tab => Some(HelperKey::Next),
        KeyCode::PageUp => Some(HelperKey::ScrollUp),
        KeyCode::PageDown => Some(HelperKey::ScrollDown),
        KeyCode::Home => Some(HelperKey::ScrollStart),
        KeyCode::End => Some(HelperKey::ScrollEnd),
        _ if key.kind != KeyEventKind::Press => None,
        KeyCode::Enter | KeyCode::Char(' ') => Some(HelperKey::Accept),
        KeyCode::Esc | KeyCode::Char('q') => Some(HelperKey::Back),
        _ => None,
    }
}

#[derive(Clone, Copy, Default)]
struct HelperLayout {
    area: Rect,
    text: Rect,
    choices: Rect,
    accept: Rect,
    back: Rect,
    first_choice: usize,
}

struct HelperUi {
    selected: usize,
    scroll: usize,
    text: Text<'static>,
    text_width: u16,
    wrapped_heights: Vec<usize>,
    text_total: usize,
    list: ListState,
    layout: HelperLayout,
}

impl HelperUi {
    fn new(request: &DialogRequest) -> Self {
        Self {
            selected: 0,
            scroll: 0,
            text: Text::from(visible(&request.text, request.ascii)),
            text_width: 0,
            wrapped_heights: Vec::new(),
            text_total: 0,
            list: ListState::default(),
            layout: HelperLayout::default(),
        }
    }

    fn scroll_limit(&self) -> usize {
        self.text_total
            .saturating_sub(usize::from(self.layout.text.height))
    }

    fn fit_text(&mut self, area: Rect) {
        self.layout.text = area;
        if self.text_width != area.width {
            self.text_width = area.width;
            self.wrapped_heights = self
                .text
                .lines
                .iter()
                .map(|line| {
                    Paragraph::new(line.clone())
                        .wrap(Wrap { trim: false })
                        .line_count(area.width)
                })
                .collect();
            self.text_total = self.wrapped_heights.iter().sum();
        }
        self.scroll = self.scroll.min(self.scroll_limit());
    }

    fn paragraph(&self) -> Paragraph<'static> {
        // Skip whole logical lines first, so a long, newline-rich dialogue is
        // not limited by Paragraph's u16 wrapped-line scroll offset.
        let mut remaining = self.scroll;
        let mut first = 0;
        for height in &self.wrapped_heights {
            if remaining < *height {
                break;
            }
            remaining -= *height;
            first += 1;
        }
        Paragraph::new(self.text.lines[first..].to_vec())
            .wrap(Wrap { trim: false })
            .scroll((remaining.min(usize::from(u16::MAX)) as u16, 0))
    }

    fn caption(hint: &str, fallback: &str, verb: &str, width: u16, ascii: bool) -> String {
        let hint = if hint.trim().is_empty() {
            fallback
        } else {
            hint
        };
        let full = visible(&format!("{hint}: {verb}"), ascii);
        if Line::from(full.as_str()).width() <= usize::from(width) {
            full
        } else {
            format!("{fallback}: {verb}")
        }
    }

    fn paint_caption(frame: &mut Frame, area: Rect, caption: &str) -> Rect {
        let width = Line::from(caption).width().min(usize::from(area.width)) as u16;
        let target = Rect::new(area.x, area.y, width, area.height.min(1));
        frame.render_widget(Paragraph::new(caption), target);
        target
    }

    fn render(&mut self, frame: &mut Frame, request: &DialogRequest) {
        if frame.area().width < 20 || frame.area().height < 8 {
            self.layout = HelperLayout::default();
            return;
        }
        self.selected = self.selected.min(request.choices.len().saturating_sub(1));
        let mut block = Block::default()
            .borders(Borders::ALL)
            .border_type(if request.ascii {
                BorderType::Plain
            } else {
                BorderType::Rounded
            })
            .title(visible(&request.title, request.ascii))
            .title_bottom(format!(
                "Choice {}/{}",
                self.selected + 1,
                request.choices.len()
            ));
        if request.ascii {
            block = block.border_set(ratatui::symbols::border::Set {
                top_left: "+",
                top_right: "+",
                bottom_left: "+",
                bottom_right: "+",
                vertical_left: "|",
                vertical_right: "|",
                horizontal_top: "-",
                horizontal_bottom: "-",
            });
        }
        let area = block.inner(frame.area());
        self.layout.area = frame.area();
        frame.render_widget(block, frame.area());
        let choice_height =
            (request.choices.len() as u16).min((area.height.saturating_sub(2) / 2).max(1));
        let rows = Layout::vertical([
            Constraint::Min(1),
            Constraint::Length(choice_height),
            Constraint::Length(2),
        ])
        .split(area);
        self.fit_text(rows[0]);
        frame.render_widget(self.paragraph(), rows[0]);
        self.layout.choices = rows[1];
        self.list.select(Some(self.selected));
        let choices: Vec<_> = request
            .choices
            .iter()
            .map(|choice| ListItem::new(choice.label()))
            .collect();
        frame.render_stateful_widget(
            List::new(choices)
                .highlight_symbol(if request.ascii { "> " } else { "› " })
                .highlight_style(Style::default().add_modifier(Modifier::REVERSED)),
            rows[1],
            &mut self.list,
        );
        // Ratatui may move the offset to reveal selection, including on resize.
        self.layout.first_choice = self.list.offset();
        let accept_row = Rect::new(rows[2].x, rows[2].y, rows[2].width, 1);
        let back_row = Rect::new(rows[2].x, rows[2].bottom() - 1, rows[2].width, 1);
        let accept = Self::caption(
            &request.accept_hint,
            "Enter",
            "choose",
            accept_row.width,
            request.ascii,
        );
        let back = Self::caption(
            &request.back_hint,
            "Esc",
            "back",
            back_row.width,
            request.ascii,
        );
        self.layout.accept = Self::paint_caption(frame, accept_row, &accept);
        self.layout.back = Self::paint_caption(frame, back_row, &back);
        let start = if self.text_total == 0 {
            0
        } else {
            self.scroll + 1
        };
        let end = (self.scroll + usize::from(rows[0].height)).min(self.text_total);
        let range_x = self.layout.back.right().saturating_add(2);
        let range_width = back_row.right().saturating_sub(range_x);
        for range in [
            format!("Text {start}-{end}/{} PgUp/PgDn", self.text_total),
            format!("{start}-{end}/{}", self.text_total),
        ] {
            if Line::from(range.as_str()).width() <= usize::from(range_width) {
                frame.render_widget(
                    Paragraph::new(range),
                    Rect::new(range_x, back_row.y, range_width, 1),
                );
                break;
            }
        }
    }

    fn input(&mut self, request: &DialogRequest, incoming: Event) -> Option<Choice> {
        let count = request.choices.len();
        if count == 0 {
            return None;
        }
        match incoming {
            Event::Key(key) => match helper_key(key) {
                Some(HelperKey::Previous) => self.selected = (self.selected + count - 1) % count,
                Some(HelperKey::Next) => self.selected = (self.selected + 1) % count,
                Some(HelperKey::ScrollUp) => {
                    self.scroll = self
                        .scroll
                        .saturating_sub(usize::from(self.layout.text.height).max(1));
                }
                Some(HelperKey::ScrollDown) => {
                    self.scroll = self
                        .scroll
                        .saturating_add(usize::from(self.layout.text.height).max(1))
                        .min(self.scroll_limit());
                }
                Some(HelperKey::ScrollStart) => self.scroll = 0,
                Some(HelperKey::ScrollEnd) => self.scroll = self.scroll_limit(),
                Some(HelperKey::Accept) => return request.choices.get(self.selected).copied(),
                Some(HelperKey::Back) => return Some(Choice::Back),
                None => {}
            },
            Event::Mouse(mouse) => {
                let point = (mouse.column, mouse.row).into();
                match mouse.kind {
                    MouseEventKind::Down(MouseButton::Left) => {
                        if self.layout.accept.contains(point) {
                            return request.choices.get(self.selected).copied();
                        }
                        if self.layout.back.contains(point) {
                            return Some(Choice::Back);
                        }
                        if self.layout.choices.contains(point) {
                            let index = self.layout.first_choice
                                + usize::from(mouse.row - self.layout.choices.y);
                            if let Some(choice) = request.choices.get(index) {
                                self.selected = index;
                                return Some(*choice);
                            }
                        }
                    }
                    MouseEventKind::ScrollUp | MouseEventKind::ScrollDown => {
                        let down = mouse.kind == MouseEventKind::ScrollDown;
                        if self.layout.text.contains(point) {
                            self.scroll = if down {
                                self.scroll.saturating_add(1).min(self.scroll_limit())
                            } else {
                                self.scroll.saturating_sub(1)
                            };
                        } else if self.layout.choices.contains(point) {
                            self.selected = if down {
                                (self.selected + 1) % count
                            } else {
                                (self.selected + count - 1) % count
                            };
                        }
                    }
                    _ => {}
                }
            }
            Event::Resize(_, _) => self.layout = HelperLayout::default(),
            _ => {}
        }
        None
    }
}

/// Standalone popup renderer. It can return only a choice; it never controls the pet.
pub fn popup_helper(path: &Path) -> Result<(), String> {
    let payload = load_payload(path)?;
    if !io::stdin().is_terminal() || !io::stdout().is_terminal() {
        return Err("Popup requires a terminal".into());
    }
    let remaining = payload.expires_ms.saturating_sub(now_ms()?);
    let deadline = Instant::now() + Duration::from_millis(remaining);
    enable_raw_mode().map_err(|e| e.to_string())?;
    let guard = TerminalGuard;
    execute!(io::stdout(), EnterAlternateScreen, EnableMouseCapture).map_err(|e| e.to_string())?;
    let mut terminal =
        Terminal::new(CrosstermBackend::new(io::stdout())).map_err(|e| e.to_string())?;
    terminal.clear().map_err(|e| e.to_string())?;
    let request = &payload.request;
    let mut ui = HelperUi::new(request);
    let result = loop {
        if Instant::now() >= deadline {
            break Err("Popup timed out".into());
        }
        // Removal by the parent ends this helper without mutating a newer popup.
        if private_file(path, false).is_err() || private_file(&payload.reply, false).is_err() {
            break Err("Popup owner closed".into());
        }
        let size = terminal.size().map_err(|e| e.to_string())?;
        if size.width < 20 || size.height < 8 {
            break Err("Popup became too small".into());
        }
        terminal
            .draw(|frame| ui.render(frame, request))
            .map_err(|e| e.to_string())?;
        if !event::poll(Duration::from_millis(50)).map_err(|e| e.to_string())? {
            continue;
        }
        let incoming = event::read().map_err(|e| e.to_string())?;
        if matches!(incoming, Event::Mouse(_)) {
            let current = terminal.size().map_err(|e| e.to_string())?;
            if (current.width, current.height) != (ui.layout.area.width, ui.layout.area.height) {
                // A click read before its Resize must not activate a stale row.
                continue;
            }
        }
        if let Some(choice) = ui.input(request, incoming) {
            break reply_helper(&payload.reply, choice);
        }
    };
    drop(terminal);
    drop(guard);
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn popup_height_fits_short_dialogues_and_stays_within_client() {
        let target = crate::input::DialogTarget {
            socket: "/tmp/mios-test.sock".into(),
            pane: "%0".into(),
            client: "/dev/pts/1".into(),
            width: 100,
            height: 40,
        };
        let short = request();
        assert!((8..=12).contains(&popup_height(&target, &short)));
        let mut long = short.clone();
        long.text = "thought ".repeat(200);
        assert!(popup_height(&target, &long) <= 30);
        let tiny = crate::input::DialogTarget {
            height: 18,
            ..target
        };
        assert!(popup_height(&tiny, &long) <= 16);
    }
    fn request() -> DialogRequest {
        DialogRequest {
            title: "MiOS-Mon".into(),
            text: "A quiet thought".into(),
            choices: vec![Choice::Listen, Choice::Back],
            accept_hint: "".into(),
            back_hint: "".into(),
            ascii: true,
        }
    }
    #[test]
    fn all_choices_round_trip_and_have_fixed_arguments() {
        for choice in Choice::value_variants() {
            assert_eq!(
                serde_json::from_slice::<Choice>(&serde_json::to_vec(choice).unwrap()).unwrap(),
                *choice
            );
            assert!(choice.argument().bytes().all(|b| b.is_ascii_lowercase()));
            assert!(!choice.label().contains('#'));
        }
    }
    #[test]
    fn key_releases_repeats_and_control_filter() {
        let enter = KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE);
        assert_eq!(helper_key(enter), Some(HelperKey::Accept));
        assert_eq!(
            helper_key(KeyEvent::new(KeyCode::Char(' '), KeyModifiers::NONE)),
            Some(HelperKey::Accept)
        );
        assert_eq!(
            helper_key(KeyEvent {
                kind: KeyEventKind::Repeat,
                ..enter
            }),
            None
        );
        assert_eq!(
            helper_key(KeyEvent {
                kind: KeyEventKind::Release,
                ..enter
            }),
            None
        );
        assert_eq!(
            helper_key(KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL)),
            Some(HelperKey::Back)
        );
        assert_eq!(
            helper_key(KeyEvent::new(KeyCode::Char('q'), KeyModifiers::ALT)),
            None
        );
        assert_eq!(
            helper_key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE)),
            Some(HelperKey::Next)
        );
    }
    #[test]
    fn text_cannot_inject_terminal_or_tmux_commands() {
        assert_eq!(
            tmux_title("#(touch /tmp/no)\x1b[31m\n", true),
            "?(touch /tmp/no)[31m "
        );
        assert_eq!(visible("a\0b\x1bc猫", true), "abc?");
    }
    #[test]
    fn shell_quotes_are_literal_for_spaces_quotes_dollars_and_backticks() {
        let value = "spaces ' double\" $HOME $(exit 9) `exit 8` ; #{pane_id} \\";
        let output = Command::new("/bin/sh")
            .args(["-c", &format!("printf '%s' {}", shell_quote(value))])
            .output()
            .unwrap();
        assert!(output.status.success());
        assert_eq!(output.stdout, value.as_bytes());
        assert!(callback(
            Path::new("/own helper/'$`#(job)"),
            Path::new("/reply ' $`"),
            Choice::Feed
        )
        .unwrap()
        .contains("####(job)"));
        assert!(callback(Path::new("/bad\nhelper"), Path::new("/reply"), Choice::Feed).is_err());
    }
    #[test]
    fn popup_plan_uses_argv_and_exact_target_without_shell_text() {
        let target = crate::input::DialogTarget {
            socket: "/own socket".into(),
            pane: "%7".into(),
            client: "/dev/pts/8".into(),
            width: 80,
            height: 24,
        };
        let mut req = request();
        req.title = "$(exit 3) #()".into();
        req.text = "NEVER IN ARGV".into();
        let args = command_args(
            &target,
            Path::new("/helper ' $`"),
            Path::new("/payload $`"),
            Path::new("/reply"),
            &req,
            false,
            None,
        )
        .unwrap();
        let args: Vec<_> = args.iter().map(|v| v.to_str().unwrap()).collect();
        assert_eq!(
            &args[0..7],
            &[
                "-S",
                "/own socket",
                "display-popup",
                "-c",
                "/dev/pts/8",
                "-t",
                "%7"
            ]
        );
        assert_eq!(
            &args[args.len() - 3..],
            &["/helper ' $`", "--dialog", "/payload $`"]
        );
        assert!(!args.contains(&"NEVER IN ARGV"));
        assert!(!args.contains(&"sh"));
    }
    #[test]
    fn native_dialog_uses_global_dimensions_and_centered_client_placement() {
        let config = crate::config::Config::sample().unwrap();
        let target = crate::input::DialogTarget {
            socket: "/s".into(),
            pane: "%1".into(),
            client: "/dev/pts/1".into(),
            width: 240,
            height: 67,
        };
        let args = command_args(
            &target,
            Path::new("/helper"),
            Path::new("/payload"),
            Path::new("/reply"),
            &request(),
            false,
            config.popup_policy.as_ref(),
        )
        .unwrap();
        let args: Vec<_> = args.iter().map(|a| a.to_str().unwrap()).collect();
        for flag in ["-x", "-y"] {
            let i = args.iter().position(|a| *a == flag).unwrap();
            assert_eq!(args[i + 1], "C");
        }
        let w = args[args.iter().position(|a| *a == "-w").unwrap() + 1]
            .parse::<u16>()
            .unwrap();
        let h = args[args.iter().position(|a| *a == "-h").unwrap() + 1]
            .parse::<u16>()
            .unwrap();
        assert_eq!(w, 60);
        assert!(h <= 18);
    }

    #[test]
    fn menu_callbacks_only_reply_and_have_unique_shortcuts() {
        let target = crate::input::DialogTarget {
            socket: "/s".into(),
            pane: "%1".into(),
            client: "/dev/pts/1".into(),
            width: 80,
            height: 24,
        };
        let args = command_args(
            &target,
            Path::new("/helper"),
            Path::new("/payload"),
            Path::new("/reply"),
            &request(),
            true,
            None,
        )
        .unwrap();
        let text = format!("{args:?}");
        assert!(text.contains("display-menu"));
        assert!(text.contains("-M"));
        assert!(text.contains("-O"));
        assert!(text.contains("simple"));
        assert!(text.contains("--dialog-reply"));
        assert!(!text.contains("send-keys"));
        assert!(!text.contains("bind-key"));
        assert!(!text.contains("set-option"));
    }
    #[test]
    fn invalid_and_oversized_requests_rejected() {
        let mut req = request();
        assert!(validate_request(&req, 1024).is_ok());
        assert!(validate_request(&req, 1).is_err());
        req.choices.clear();
        assert!(validate_request(&req, 1024).is_err());
    }
    #[test]
    fn private_paths_reject_foreign_symlink_hardlink_and_public_files() {
        let runtime = tempfile::tempdir().unwrap();
        let dir = runtime.path();
        assert!(private_file_at(Path::new("/etc/passwd"), true, dir).is_err());
        let file = temporary(dir).unwrap();
        assert!(private_file_at(file.path(), false, dir).is_ok());
        file.as_file()
            .set_permissions(fs::Permissions::from_mode(0o644))
            .unwrap();
        assert!(private_file_at(file.path(), true, dir).is_err());
        file.as_file()
            .set_permissions(fs::Permissions::from_mode(0o600))
            .unwrap();
        let link = temporary(dir).unwrap();
        let path = link.into_temp_path();
        fs::remove_file(&path).unwrap();
        std::os::unix::fs::symlink(file.path(), &path).unwrap();
        assert!(private_file_at(&path, true, dir).is_err());
        fs::remove_file(&path).unwrap();
        fs::hard_link(file.path(), &path).unwrap();
        assert!(private_file_at(file.path(), true, dir).is_err());
    }
    #[test]
    fn replies_are_single_use_bounded_json() {
        let runtime = tempfile::tempdir().unwrap();
        let dir = runtime.path();
        let mut file = temporary(dir).unwrap();
        reply_at(file.path(), Choice::Feed, dir).unwrap();
        assert_eq!(response_at(file.path(), dir).unwrap(), Some(Choice::Feed));
        assert!(reply_at(file.path(), Choice::Quit, dir).is_err());
        file.as_file_mut().set_len(0).unwrap();
        file.write_all(&[b'x'; 129]).unwrap();
        assert!(response_at(file.path(), dir).is_err());
    }
    #[test]
    fn payload_requires_empty_reply_and_unexpired_deadline() {
        let runtime = tempfile::tempdir().unwrap();
        let dir = runtime.path();
        let reply = temporary(dir).unwrap();
        let mut file = temporary(dir).unwrap();
        let mut payload = Payload {
            request: request(),
            reply: reply.path().into(),
            expires_ms: now_ms().unwrap() + 10000,
            max_bytes: 4096,
        };
        file.write_all(&serde_json::to_vec(&payload).unwrap())
            .unwrap();
        assert!(load_payload_at(file.path(), dir).is_ok());
        reply_at(reply.path(), Choice::Back, dir).unwrap();
        assert!(load_payload_at(file.path(), dir).is_err());
        let mut expired = temporary(dir).unwrap();
        payload.expires_ms = 1;
        expired
            .write_all(&serde_json::to_vec(&payload).unwrap())
            .unwrap();
        assert!(load_payload_at(expired.path(), dir).is_err());
    }
    #[test]
    fn deadline_and_cancellation_fail_before_any_native_query() {
        let (tx, _rx) = mpsc::channel();
        let stop = AtomicBool::new(false);
        let mut settings = Settings {
            mode: DialogMode::Native,
            timeout: Duration::ZERO,
            query_ms: 1,
            max_bytes: 4096,
            min_width: 60,
            min_height: 18,
            popup_policy: None,
        };
        let started = Instant::now();
        assert!(run_dialog(&settings, request(), false, &stop, &tx).is_err());
        assert!(started.elapsed() < Duration::from_millis(100));
        settings.timeout = Duration::from_secs(1);
        stop.store(true, Ordering::Relaxed);
        assert!(run_dialog(&settings, request(), true, &stop, &tx).is_err());
    }
    #[test]
    fn drop_waits_for_worker_to_remove_owned_files() {
        let runtime = tempfile::tempdir().unwrap();
        let file = temporary(runtime.path()).unwrap();
        let path = file.path().to_owned();
        let stop = Arc::new(AtomicBool::new(false));
        let cancel = stop.clone();
        let (_tx, events) = mpsc::channel();
        let worker = std::thread::spawn(move || {
            while !cancel.load(Ordering::Relaxed) {
                std::thread::sleep(Duration::from_millis(1));
            }
            drop(file);
        });
        let config = crate::config::Config::sample().unwrap();
        let mut dialogs = NativeDialogs::new(&config);
        dialogs.flight = Some(Flight {
            events,
            stop: stop.clone(),
            worker: Some(worker),
        });
        let started = Instant::now();
        drop(dialogs);
        assert!(stop.load(Ordering::Relaxed));
        assert!(!path.exists());
        assert!(started.elapsed() < Duration::from_secs(1));
    }
    #[test]
    fn inline_and_poll_are_nonblocking_and_no_duplicate_requests() {
        let mut config = crate::config::Config::sample().unwrap();
        config.dialog_mode = DialogMode::Inline;
        let mut dialogs = NativeDialogs::new(&config);
        assert!(!dialogs.request(request(), false));
        assert_eq!(dialogs.poll(), None);
        let (tx, rx) = mpsc::channel();
        dialogs.flight = Some(Flight {
            events: rx,
            stop: Arc::new(AtomicBool::new(false)),
            worker: None,
        });
        assert_eq!(dialogs.poll(), None);
        assert!(!dialogs.request(request(), false));
        tx.send(DialogEvent::Opened).unwrap();
        assert_eq!(dialogs.poll(), Some(DialogEvent::Opened));
        assert!(dialogs.active());
        tx.send(DialogEvent::Inline).unwrap();
        assert_eq!(dialogs.poll(), Some(DialogEvent::Inline));
        assert!(!dialogs.active());
    }
}

#[cfg(test)]
mod habitat_context_tests {
    use super::*;

    #[test]
    fn all_declared_context_actions_fit_but_duplicate_actions_are_rejected() {
        let mut request = DialogRequest {
            title: "Pet".into(),
            text: "Thought".into(),
            choices: Choice::value_variants().to_vec(),
            accept_hint: "Enter".into(),
            back_hint: "Escape".into(),
            ascii: true,
        };
        assert!(request.choices.contains(&Choice::Habitat));
        assert!(validate_request(&request, 4096).is_ok());
        request.choices[1] = request.choices[0];
        assert!(validate_request(&request, 4096).is_err());
    }
}

#[cfg(test)]
mod helper_navigation_tests {
    use super::*;
    use crossterm::event::MouseEvent;
    use ratatui::{backend::TestBackend, buffer::Buffer};

    fn request() -> DialogRequest {
        DialogRequest {
            title: "Owned conversation".into(),
            text: "alpha beta 猫猫 e\u{301}  gamma\n\ndelta epsilon zeta\n".repeat(10)
                + "VISIBLE_END",
            choices: Choice::value_variants().to_vec(),
            accept_hint: "Hold D-pad Right / Tap Triangle".into(),
            back_hint: "Hold Circle / Hold Xbox B".into(),
            ascii: false,
        }
    }

    fn draw(ui: &mut HelperUi, request: &DialogRequest, width: u16, height: u16) -> Buffer {
        let mut terminal = Terminal::new(TestBackend::new(width, height)).unwrap();
        terminal.draw(|frame| ui.render(frame, request)).unwrap();
        terminal.backend().buffer().clone()
    }

    fn painted(buffer: &Buffer, area: Rect) -> String {
        (area.y..area.bottom())
            .map(|y| {
                (area.x..area.right())
                    .map(|x| buffer[(x, y)].symbol())
                    .collect::<String>()
            })
            .collect::<Vec<_>>()
            .join("\n")
    }

    fn key(code: KeyCode) -> Event {
        Event::Key(KeyEvent::new(code, KeyModifiers::NONE))
    }

    fn mouse(kind: MouseEventKind, column: u16, row: u16) -> Event {
        Event::Mouse(MouseEvent {
            kind,
            column,
            row,
            modifiers: KeyModifiers::NONE,
        })
    }

    #[test]
    fn wrapped_text_paging_stops_at_real_bounds_and_clamps_after_resize() {
        let request = request();
        let mut ui = HelperUi::new(&request);
        draw(&mut ui, &request, 20, 8);
        let expected = Paragraph::new(visible(&request.text, request.ascii))
            .wrap(Wrap { trim: false })
            .line_count(ui.layout.text.width);
        assert_eq!(ui.text_total, expected);
        assert!(ui.scroll_limit() > 0);
        let selected = ui.selected;
        let rows = usize::from(ui.layout.text.height);
        assert_eq!(ui.input(&request, key(KeyCode::PageDown)), None);
        assert_eq!(ui.scroll, rows.min(ui.scroll_limit()));
        for _ in 0..expected {
            assert_eq!(ui.input(&request, key(KeyCode::PageDown)), None);
        }
        assert_eq!(ui.scroll, ui.scroll_limit());
        let buffer = draw(&mut ui, &request, 20, 8);
        assert!(painted(&buffer, ui.layout.text).contains("VISIBLE_END"));
        let narrow_limit = ui.scroll_limit();
        let buffer = draw(&mut ui, &request, 80, 24);
        assert!(ui.scroll_limit() < narrow_limit);
        assert_eq!(ui.scroll, ui.scroll_limit());
        assert!(painted(&buffer, ui.layout.text).contains("VISIBLE_END"));
        assert_eq!(ui.selected, selected);
        assert_eq!(ui.input(&request, key(KeyCode::Home)), None);
        assert_eq!(ui.scroll, 0);
        assert_eq!(ui.input(&request, key(KeyCode::PageUp)), None);
        assert_eq!(ui.scroll, 0);
        assert_eq!(ui.input(&request, key(KeyCode::End)), None);
        assert_eq!(ui.scroll, ui.scroll_limit());
    }

    #[test]
    fn newline_rich_text_can_reach_the_end_beyond_u16_scroll_offsets() {
        let mut request = request();
        request.text = "x\n".repeat(usize::from(u16::MAX) + 8) + "FINAL_LINE";
        validate_request(&request, 262_144).unwrap();
        let mut ui = HelperUi::new(&request);
        draw(&mut ui, &request, 20, 8);
        assert!(ui.scroll_limit() > usize::from(u16::MAX));
        assert_eq!(ui.input(&request, key(KeyCode::End)), None);
        let buffer = draw(&mut ui, &request, 20, 8);
        assert!(painted(&buffer, ui.layout.text).contains("FINAL_LINE"));
    }

    #[test]
    fn mouse_choice_rows_follow_the_rendered_offset_and_resize_discards_old_targets() {
        let request = request();
        let mut ui = HelperUi::new(&request);
        ui.selected = request.choices.len() - 1;
        let buffer = draw(&mut ui, &request, 20, 8);
        assert!(ui.layout.first_choice > 0);
        let first = ui.layout.first_choice;
        let choices = ui.layout.choices;
        assert!(
            painted(&buffer, Rect::new(choices.x, choices.y, choices.width, 1))
                .contains(request.choices[first].label())
        );
        assert_eq!(
            ui.input(
                &request,
                mouse(
                    MouseEventKind::Down(MouseButton::Left),
                    choices.x,
                    choices.y
                )
            ),
            Some(request.choices[first])
        );
        assert_eq!(ui.selected, first);
        assert_eq!(ui.input(&request, Event::Resize(60, 20)), None);
        assert_eq!(
            ui.input(
                &request,
                mouse(
                    MouseEventKind::Down(MouseButton::Left),
                    choices.x,
                    choices.y
                )
            ),
            None,
            "a resize invalidates hit boxes until a new frame is painted"
        );
        let buffer = draw(&mut ui, &request, 60, 20);
        let choices = ui.layout.choices;
        let first = ui.layout.first_choice;
        for row in 0..choices.height {
            let index = first + usize::from(row);
            let expected = request.choices.get(index).copied();
            if let Some(choice) = expected {
                assert!(painted(
                    &buffer,
                    Rect::new(choices.x, choices.y + row, choices.width, 1)
                )
                .contains(choice.label()));
            }
            assert_eq!(
                ui.input(
                    &request,
                    mouse(
                        MouseEventKind::Down(MouseButton::Left),
                        choices.x,
                        choices.y + row
                    )
                ),
                expected
            );
        }
    }

    #[test]
    fn wheel_only_navigates_its_painted_region_and_outside_or_release_events_are_inert() {
        let request = request();
        let mut ui = HelperUi::new(&request);
        draw(&mut ui, &request, 20, 8);
        let text = ui.layout.text;
        let choices = ui.layout.choices;
        assert_eq!(
            ui.input(&request, mouse(MouseEventKind::ScrollDown, text.x, text.y)),
            None
        );
        assert_eq!(ui.scroll, 1);
        assert_eq!(ui.selected, 0);
        assert_eq!(
            ui.input(
                &request,
                mouse(MouseEventKind::ScrollDown, choices.x, choices.y)
            ),
            None
        );
        assert_eq!(ui.selected, 1);
        assert_eq!(ui.scroll, 1);
        let before = (ui.selected, ui.scroll);
        for event in [
            mouse(MouseEventKind::Down(MouseButton::Left), 0, 0),
            mouse(MouseEventKind::ScrollDown, 0, 0),
            mouse(MouseEventKind::Moved, choices.x, choices.y),
            mouse(
                MouseEventKind::Drag(MouseButton::Left),
                choices.x,
                choices.y,
            ),
            mouse(MouseEventKind::Up(MouseButton::Left), choices.x, choices.y),
            Event::Key(KeyEvent {
                kind: KeyEventKind::Repeat,
                ..KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE)
            }),
            Event::Key(KeyEvent {
                kind: KeyEventKind::Release,
                ..KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE)
            }),
        ] {
            assert_eq!(ui.input(&request, event), None);
            assert_eq!((ui.selected, ui.scroll), before);
        }
        assert_eq!(
            ui.input(&request, mouse(MouseEventKind::ScrollUp, text.x, text.y)),
            None
        );
        assert_eq!(ui.scroll, 0);
        assert_eq!(
            ui.input(
                &request,
                mouse(MouseEventKind::ScrollUp, choices.x, choices.y)
            ),
            None
        );
        assert_eq!(ui.selected, 0);
    }

    #[test]
    fn mapped_captions_keep_truthful_choose_and_back_on_distinct_clickable_rows() {
        let request = request();
        let mut ui = HelperUi::new(&request);
        ui.selected = 3;
        let buffer = draw(&mut ui, &request, 20, 8);
        let accept = ui.layout.accept;
        let back = ui.layout.back;
        assert_ne!(accept.y, back.y);
        assert!(accept.intersection(back).is_empty());
        assert_eq!(painted(&buffer, accept), "Enter: choose");
        assert_eq!(painted(&buffer, back), "Esc: back");
        assert_eq!(
            ui.input(
                &request,
                mouse(MouseEventKind::Down(MouseButton::Left), accept.x, accept.y)
            ),
            Some(request.choices[3])
        );
        assert_eq!(
            ui.input(&request, key(KeyCode::Enter)),
            Some(request.choices[3])
        );
        assert_eq!(
            ui.input(
                &request,
                mouse(MouseEventKind::Down(MouseButton::Left), back.x, back.y)
            ),
            Some(Choice::Back)
        );
        assert_eq!(ui.input(&request, key(KeyCode::Esc)), Some(Choice::Back));
        draw(&mut ui, &request, 80, 24);
        let accept = ui.layout.accept;
        let back = ui.layout.back;
        assert!(
            accept.width > 13 && back.width > 9,
            "wide popups retain full mapped hints"
        );
    }
}
