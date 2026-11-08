// AI-hint: Bounded native tmux dialogues with private replies and explicit inline fallback.
// AI-related: input.rs, config.rs, ui.rs, task_3717
#![forbid(unsafe_code)]

use clap::ValueEnum;
use crossterm::{
    event::{self, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers},
    execute,
    terminal::{disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen},
};
use ratatui::{
    backend::CrosstermBackend,
    layout::{Constraint, Layout},
    style::{Modifier, Style},
    widgets::{Block, BorderType, Borders, List, ListItem, ListState, Paragraph, Wrap},
    Terminal,
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

const PREFIX: &str = "mios-tamagotchi-dialog-";
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
    if menu {
        // This command is launched from the game, not a tmux mouse key binding.
        args.extend([
            "-M".into(),
            "-O".into(),
            "-x".into(),
            "M".into(),
            "-y".into(),
            "M".into(),
        ]);
        for (index, choice) in request.choices.iter().enumerate() {
            args.push(choice.label().into());
            args.push(format!("{:x}", index + 1).into());
            args.push(callback(exe, reply, *choice)?.into());
        }
    } else {
        let runtime = reply.parent().ok_or("Missing runtime directory")?;
        let mut environment = std::ffi::OsString::from("XDG_RUNTIME_DIR=");
        environment.push(runtime);
        args.extend([
            "-e".into(),
            environment,
            "-E".into(),
            "-w".into(),
            target
                .width
                .saturating_sub(4)
                .clamp(20, 90)
                .to_string()
                .into(),
            "-h".into(),
            popup_height(target, request).to_string().into(),
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
    let args = command_args(&target, &exe, payload.path(), reply.path(), &request, menu)?;
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
        let _ = execute!(io::stdout(), LeaveAlternateScreen);
    }
}
#[derive(Debug, PartialEq, Eq)]
enum HelperKey {
    Previous,
    Next,
    ScrollUp,
    ScrollDown,
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
        _ if key.kind != KeyEventKind::Press => None,
        KeyCode::Enter | KeyCode::Char(' ') => Some(HelperKey::Accept),
        KeyCode::Esc | KeyCode::Char('q') => Some(HelperKey::Back),
        _ => None,
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
    execute!(io::stdout(), EnterAlternateScreen).map_err(|e| e.to_string())?;
    let mut terminal =
        Terminal::new(CrosstermBackend::new(io::stdout())).map_err(|e| e.to_string())?;
    terminal.clear().map_err(|e| e.to_string())?;
    let request = &payload.request;
    let mut selected = 0;
    let mut scroll = 0u16;
    let mut state = ListState::default();
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
        state.select(Some(selected));
        terminal
            .draw(|frame| {
                let mut block = Block::default()
                    .borders(Borders::ALL)
                    .border_type(if request.ascii {
                        BorderType::Plain
                    } else {
                        BorderType::Rounded
                    })
                    .title(visible(&request.title, request.ascii));
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
                frame.render_widget(block, frame.area());
                let choice_height = (request.choices.len() as u16).min((area.height / 2).max(1));
                let rows = Layout::vertical([
                    Constraint::Min(1),
                    Constraint::Length(choice_height),
                    Constraint::Length(1),
                ])
                .split(area);
                frame.render_widget(
                    Paragraph::new(visible(&request.text, request.ascii))
                        .wrap(Wrap { trim: false })
                        .scroll((scroll, 0)),
                    rows[0],
                );
                let choices: Vec<_> = request
                    .choices
                    .iter()
                    .map(|c| ListItem::new(c.label()))
                    .collect();
                frame.render_stateful_widget(
                    List::new(choices)
                        .highlight_symbol(if request.ascii { "> " } else { "› " })
                        .highlight_style(Style::default().add_modifier(Modifier::REVERSED)),
                    rows[1],
                    &mut state,
                );
                let accept = if request.accept_hint.trim().is_empty() {
                    "Enter"
                } else {
                    &request.accept_hint
                };
                let back = if request.back_hint.trim().is_empty() {
                    "Esc"
                } else {
                    &request.back_hint
                };
                frame.render_widget(
                    Paragraph::new(visible(
                        &format!("{accept}: choose  {back}: back  PgUp/PgDn: text"),
                        request.ascii,
                    )),
                    rows[2],
                );
            })
            .map_err(|e| e.to_string())?;
        if !event::poll(Duration::from_millis(50)).map_err(|e| e.to_string())? {
            continue;
        }
        if let Event::Key(key) = event::read().map_err(|e| e.to_string())? {
            match helper_key(key) {
                Some(HelperKey::Previous) => {
                    selected = (selected + request.choices.len() - 1) % request.choices.len()
                }
                Some(HelperKey::Next) => selected = (selected + 1) % request.choices.len(),
                Some(HelperKey::ScrollUp) => scroll = scroll.saturating_sub(4),
                Some(HelperKey::ScrollDown) => scroll = scroll.saturating_add(4),
                Some(HelperKey::Accept) => {
                    break reply_helper(&payload.reply, request.choices[selected]);
                }
                Some(HelperKey::Back) => break reply_helper(&payload.reply, Choice::Back),
                None => {}
            }
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
