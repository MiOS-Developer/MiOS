// AI-hint: miosd shaderglass state: per-class ShaderGlass profiles and the pane/window-state store shared by every session (reload-modify-write under a lock; the attached record per session) that survives pane restarts, supervisor restarts and every 2D/3D presentation mode -- the WineCap command channel (save/next/menu inside the ShaderGlass process), the crash-safe default-profile swap that turns ShaderGlass' "Set as default" into a per-class profile, and the attached-pane record a restarted supervisor resumes.
// AI-related: src/mios-rs/miosd/src/shaderglass.rs, usr/share/mios/shaderglass/patches/winecap-mios-portal.patch, usr/share/mios/mios.toml [desktop.shaderglass.state]
// AI-functions: StateCfg, Store, Attached, Command, update_json, sanitize_profile, profile_shader, windows_path, class_slug, save_profile, launch_profile, recover_default, recover_default_when_idle

//! ShaderGlass keeps its live state (shader, parameters, pixel size, output scale, frame skip, flips, ...) only
//! in memory; the one profile it writes without a file dialog is `%LOCALAPPDATA%\ShaderGlassDefault.sgp`
//! ("Processing > Set as default"). The patched WineCap runs inside the ShaderGlass process and executes menu
//! commands on request (a command file it polls; [`Command`]), so a save is: back the operator's default up,
//! remove it, ask the pane for "Set as default", take the file it wrote as the class profile (minus the
//! capture-source keys: miosd chooses the target), restore the operator's default. The backup makes the swap
//! crash safe ([`recover_default`] puts it back on the next start). A pane launches with its class profile as
//! ShaderGlass' last argument (a profile it loads after the default), so a re-attach, a crash relaunch, a
//! nested or primary session and every presentation mode the target goes through see the same shader.

use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use serde_json::{json, Value as Json};

/// [desktop.shaderglass.state]
#[derive(Clone, Debug, PartialEq)]
pub struct StateCfg {
    pub persist: bool,
    /// Relative to $XDG_STATE_HOME.
    pub directory: String,
    pub save_timeout: Duration,
    pub restart_limit: u32,
    pub resume: bool,
    pub strip_keys: Vec<String>,
    /// The session's pane summary ("active=<0|1>\ntargets=<a,b>"), relative to $XDG_RUNTIME_DIR.
    pub runtime_state: String,
    /// The session's Hypr3D room request queue (one "<verb> <address>" per line), relative to $XDG_RUNTIME_DIR.
    pub room_requests: String,
}

/// The file names inside the state directory.
pub const STORE: &str = "state.json";
pub const PROFILES: &str = "profiles";
pub const DEFAULT_BACKUP: &str = "default-backup.sgp";
/// Marks "there was no operator default" for [`recover_default`].
pub const DEFAULT_ABSENT: &str = "default-backup.absent";
pub const SAVE_LOCK: &str = "save.lock";
/// The supervisor session of the user manager's own (primary) desktop; a nested session is
/// "overlay-<hash of its runtime directory>" (`unit_instance`).
pub const PRIMARY_SESSION: &str = "overlay";
/// How long a reload-modify-write of a shared JSON document may wait for another session's (it holds the
/// lock only to read, change and rename one small file).
const JSON_LOCK_TIMEOUT: Duration = Duration::from_secs(2);

/// A ShaderGlass window class (or any class) as a file name: [a-z0-9._-]; anything else becomes '_' and adds a
/// hash of the original, so two classes never share a profile.
pub fn class_slug(class: &str) -> String {
    let mut changed = class.is_empty();
    let mut s: String = class
        .chars()
        .map(|c| {
            let l = c.to_ascii_lowercase();
            if l.is_ascii_alphanumeric() || l == '.' || l == '_' || l == '-' {
                if l != c {
                    changed = true;
                }
                l
            } else {
                changed = true;
                '_'
            }
        })
        .take(64)
        .collect();
    if s.starts_with('.') {
        s.replace_range(0..1, "_");
        changed = true;
    }
    if changed || class.chars().count() > 64 {
        // FNV-1a over the exact class.
        let mut h: u64 = 0xcbf29ce484222325;
        for b in class.as_bytes() {
            h ^= u64::from(*b);
            h = h.wrapping_mul(0x100000001b3);
        }
        s = format!("{s}-{:08x}", h as u32);
    }
    s
}

/// A Unix path as Wine sees it through its Z: drive.
pub fn windows_path(p: &Path) -> Option<String> {
    let s = p.to_str()?;
    if !s.starts_with('/') || s.contains('"') || s.chars().any(char::is_control) {
        return None;
    }
    Some(format!("Z:{}", s.replace('/', "\\")))
}

/// One `Key "value"` profile line (std::quoted value), or None.
fn parse_line(line: &str) -> Option<(String, String)> {
    let line = line.trim_end_matches(['\r', '\n']).trim_start();
    let (key, rest) = line.split_once(char::is_whitespace)?;
    let rest = rest.trim_start();
    let inner = rest.strip_prefix('"')?;
    let mut out = String::new();
    let mut it = inner.chars();
    loop {
        match it.next()? {
            '\\' => out.push(it.next()?),
            '"' => break,
            c => out.push(c),
        }
    }
    Some((key.to_owned(), out))
}

/// A profile ShaderGlass wrote, reduced to what belongs to a class: ProfileVersion 1.x first, every
/// `strip` key (capture source, input/crop rectangles) removed, CRLF lines like ShaderGlass writes them.
/// None for anything that is not a ShaderGlass 1.x profile (an empty or half-written file included).
pub fn sanitize_profile(text: &str, strip: &[String]) -> Option<String> {
    let mut lines = Vec::new();
    for raw in text.lines() {
        if raw.trim().is_empty() {
            continue;
        }
        let (k, v) = parse_line(raw)?;
        if lines.is_empty() && !(k == "ProfileVersion" && v.starts_with("1.")) {
            return None;
        }
        if strip.iter().any(|s| s == &k) {
            continue;
        }
        lines.push((k, v));
    }
    let has_shader = lines.iter().any(|(k, _)| k == "ShaderName" || k == "ShaderPath");
    (lines.len() > 1 && has_shader).then(|| super::wine::profile_text(&lines))
}

/// The shader a profile selects ("crt/crt-geom", or the imported path).
pub fn profile_shader(text: &str) -> Option<String> {
    let mut cat = None;
    let mut name = None;
    for l in text.lines() {
        match parse_line(l) {
            Some((k, v)) if k == "ShaderCategory" => cat = Some(v),
            Some((k, v)) if k == "ShaderName" => name = Some(v),
            Some((k, v)) if k == "ShaderPath" => return Some(v),
            _ => {}
        }
    }
    name.map(|n| match cat {
        Some(c) if !c.is_empty() => format!("{c}/{n}"),
        _ => n,
    })
}

/// A pane the supervisor ran, recorded so a supervisor that died with it attached can put it back.
#[derive(Clone, Debug, PartialEq)]
pub struct Attached {
    pub address: String,
    pub pid: i64,
    pub stable_id: String,
    pub class: String,
    pub setup: bool,
}

impl Attached {
    fn to_json(&self) -> Json {
        json!({"address": self.address, "pid": self.pid, "stable_id": self.stable_id, "class": self.class, "setup": self.setup})
    }
    fn from_json(v: &Json) -> Option<Self> {
        Some(Self {
            address: v.get("address")?.as_str()?.to_owned(),
            pid: v.get("pid")?.as_i64()?,
            stable_id: v.get("stable_id").and_then(Json::as_str).unwrap_or("").to_owned(),
            class: v.get("class").and_then(Json::as_str).unwrap_or("").to_owned(),
            setup: v.get("setup").and_then(Json::as_bool).unwrap_or(false),
        })
    }
}

/// A shared JSON document (state.json, restore-tokens.json) changed by reload-modify-write: an exclusive flock on
/// `<file>.lock`, the document read from disk, `change` applied, and the result renamed into place -- so two
/// supervisors (the primary and a nested session share $XDG_STATE_HOME) never write back a stale copy over each
/// other's changes. `change` returns whether it changed anything; nothing is written otherwise.
pub fn update_json(path: &Path, change: impl FnOnce(&mut Json) -> bool) -> Result<bool, String> {
    let dir = path.parent().ok_or_else(|| format!("{}: no parent", path.display()))?;
    let name = path.file_name().and_then(|n| n.to_str()).ok_or_else(|| format!("{}: no file name", path.display()))?;
    let _lock = Lock::take(dir, &format!("{name}.lock"), true, JSON_LOCK_TIMEOUT)?;
    let mut doc = read_json(path).unwrap_or(Json::Null);
    if !change(&mut doc) {
        return Ok(false);
    }
    super::write_private(path, serde_json::to_string_pretty(&doc).map_err(|e| e.to_string())?.as_bytes())?;
    Ok(true)
}

fn read_json(path: &Path) -> Option<Json> {
    std::fs::read(path).ok().and_then(|b| serde_json::from_slice::<Json>(&b).ok())
}

/// state.json as this version keeps it: {"version": 1, "classes": {class: {profile, shader, saved_ms}},
/// "sessions": {session: {"runtime": dir, "attached": [...]}}}. Anything unreadable starts empty; the
/// single "attached" list of the first store version belonged to the primary session.
fn normalize(doc: Json) -> Json {
    let mut doc = match doc {
        Json::Object(m) if m.get("version").and_then(Json::as_u64) == Some(1) => Json::Object(m),
        _ => json!({"version": 1}),
    };
    if !doc.get("classes").is_some_and(Json::is_object) {
        doc["classes"] = json!({});
    }
    if !doc.get("sessions").is_some_and(Json::is_object) {
        doc["sessions"] = json!({});
    }
    if let Some(old) = doc.as_object_mut().and_then(|m| m.remove("attached")) {
        if old.as_array().is_some_and(|a| !a.is_empty()) && doc["sessions"].get(PRIMARY_SESSION).is_none() {
            doc["sessions"][PRIMARY_SESSION] = json!({"runtime": "", "attached": old});
        }
    }
    doc
}

/// Drop the record of every nested session whose runtime directory is gone (it can never resume).
fn prune_sessions(doc: &mut Json) {
    if let Some(m) = doc.get_mut("sessions").and_then(Json::as_object_mut) {
        m.retain(|k, v| {
            let rt = v.get("runtime").and_then(Json::as_str).unwrap_or("");
            k == PRIMARY_SESSION || rt.is_empty() || Path::new(rt).is_dir()
        });
    }
}

/// $XDG_STATE_HOME/<directory>/state.json, 0600 in a 0700 directory. Shared by every session of the user:
/// the class profiles are common, the attached-pane record is per session ([`PRIMARY_SESSION`] or a nested
/// session's key). Nothing is cached: every read is the file on disk, every write a reload-modify-write
/// ([`update_json`]), so one session never undoes another's save or clears another's record.
#[derive(Debug)]
pub struct Store {
    pub dir: PathBuf,
    session: String,
    runtime: String,
    /// The attached record this session last wrote (a supervisor records it every tick: written on change only).
    written: Option<Vec<Attached>>,
}

impl Store {
    /// The store in `dir` for `session` (its supervisor runtime directory is `runtime`).
    pub fn open(dir: &Path, session: &str, runtime: &Path) -> Store {
        Store { dir: dir.to_owned(), session: session.to_owned(), runtime: runtime.display().to_string(), written: None }
    }

    fn path(&self) -> PathBuf {
        self.dir.join(STORE)
    }

    fn doc(&self) -> Json {
        normalize(read_json(&self.path()).unwrap_or(Json::Null))
    }

    fn update(&self, change: impl FnOnce(&mut Json) -> bool) -> Result<bool, String> {
        super::ensure_private_dir(&self.dir)?;
        update_json(&self.path(), |raw| {
            let mut doc = normalize(raw.take());
            let changed = change(&mut doc);
            prune_sessions(&mut doc);
            *raw = doc;
            changed
        })
    }

    pub fn profile_path(&self, class: &str) -> PathBuf {
        self.dir.join(PROFILES).join(format!("{}.sgp", class_slug(class)))
    }

    /// The class profile a pane launches with: recorded (by any session) and still a valid profile on disk.
    pub fn saved_profile(&self, class: &str) -> Option<PathBuf> {
        self.doc().get("classes")?.get(class)?;
        let p = self.profile_path(class);
        let text = std::fs::read_to_string(&p).ok()?;
        sanitize_profile(&text, &[]).map(|_| p)
    }

    #[cfg(test)]
    pub fn class_entry(&self, class: &str) -> Option<Json> {
        self.doc().get("classes")?.get(class).cloned()
    }

    /// Write `profile` (already sanitized) as the class profile and record it.
    pub fn record_profile(&mut self, class: &str, profile: &str) -> Result<PathBuf, String> {
        let dir = self.dir.join(PROFILES);
        super::ensure_private_dir(&self.dir)?;
        super::ensure_private_dir(&dir)?;
        let path = self.profile_path(class);
        super::write_private(&path, profile.as_bytes())?;
        let entry = json!({
            "profile": path.file_name().and_then(|n| n.to_str()).unwrap_or(""),
            "shader": profile_shader(profile),
            "saved_ms": super::picker::now_ms(),
        });
        self.update(|doc| {
            doc["classes"][class] = entry;
            true
        })?;
        Ok(path)
    }

    /// The panes this session left attached.
    pub fn attached(&self) -> Vec<Attached> {
        self.doc()["sessions"]
            .get(&self.session)
            .and_then(|s| s.get("attached"))
            .and_then(Json::as_array)
            .into_iter()
            .flatten()
            .filter_map(Attached::from_json)
            .collect()
    }

    /// Replace this session's attached record (an empty list removes it); writes only on change. Other
    /// sessions' records are kept.
    pub fn set_attached(&mut self, list: &[Attached]) -> Result<bool, String> {
        if self.written.as_deref() == Some(list) {
            return Ok(false);
        }
        let (session, runtime) = (self.session.clone(), self.runtime.clone());
        let new = Json::Array(list.iter().map(Attached::to_json).collect());
        let wrote = self.update(|doc| {
            let sessions = doc["sessions"].as_object_mut().expect("normalized");
            let cur = sessions.get(&session).and_then(|s| s.get("attached")).cloned().unwrap_or(json!([]));
            if cur == new {
                return false;
            }
            if list.is_empty() {
                sessions.remove(&session);
            } else {
                sessions.insert(session.clone(), json!({"runtime": runtime, "attached": new}));
            }
            true
        })?;
        self.written = Some(list.to_vec());
        Ok(wrote)
    }

    pub fn to_status(&self) -> Json {
        let doc = self.doc();
        json!({
            "dir": self.dir,
            "session": self.session,
            "classes": doc["classes"],
            "attached": doc["sessions"].get(&self.session).and_then(|s| s.get("attached")).cloned().unwrap_or(json!([])),
            "sessions": doc["sessions"].as_object().map(|m| m.keys().cloned().collect::<Vec<_>>()).unwrap_or_default(),
        })
    }
}

// ---------------------------------------------------------------- WineCap command channel

/// The command file WineCap polls inside one ShaderGlass process (MIOS_WINECAP_COMMAND_FILE): the supervisor
/// writes "<seq> <verb>\n" atomically; WineCap claims it, sends the menu command to the ShaderGlass main
/// window (SendMessageTimeout: done when ShaderGlass handled it) and writes "<seq> ok menu=<0|1>" (whether
/// ShaderGlass' menu bar is shown afterwards) or "<seq> error ..." to `<file>.ack`. WineCap creates `<file>.ready` when its channel runs: an older WineCap without it is
/// detected at once, never waited for.
#[derive(Debug)]
pub struct Command {
    pub file: PathBuf,
    seq: u64,
}

/// The verbs WineCap knows: ShaderGlass' "Set as default", IDM_SHADER_NEXT, and the menu shown / hidden
/// (IDM_TOGGLEMENU only when its state differs: setup mode and the menu never drift apart, unlike a key).
pub const VERBS: &[&str] = &["save", "next", "menu-on", "menu-off"];

/// Whether ShaderGlass' menu bar is shown, from the detail of an answer ("menu=1"); None from an older answer.
pub fn menu_shown(detail: &str) -> Option<bool> {
    detail.split_whitespace().find_map(|w| match w {
        "menu=1" => Some(true),
        "menu=0" => Some(false),
        _ => None,
    })
}

fn with_suffix(p: &Path, suffix: &str) -> PathBuf {
    let mut s = p.as_os_str().to_owned();
    s.push(suffix);
    PathBuf::from(s)
}

impl Command {
    pub fn new(file: PathBuf) -> Self {
        Command { file, seq: 0 }
    }
    pub fn ready_file(&self) -> PathBuf {
        with_suffix(&self.file, ".ready")
    }
    pub fn ack_file(&self) -> PathBuf {
        with_suffix(&self.file, ".ack")
    }
    /// Whether this pane's WineCap runs the channel.
    pub fn ready(&self) -> bool {
        self.ready_file().is_file()
    }
    /// Run `verb` in the pane; Ok(the answer's detail, e.g. "menu=0") once ShaderGlass handled it.
    pub fn run(&mut self, verb: &str, timeout: Duration) -> Result<String, String> {
        if !VERBS.contains(&verb) {
            return Err(format!("unknown WineCap command {verb:?}"));
        }
        if !self.ready() {
            return Err("this WineCap has no MiOS command channel (reinstall: mios-shaderglass install --force)".into());
        }
        self.seq += 1;
        let seq = self.seq;
        let ack = self.ack_file();
        let _ = std::fs::remove_file(&ack);
        super::write_private(&self.file, format!("{seq} {verb}\n").as_bytes())?;
        let until = Instant::now() + timeout;
        loop {
            if let Ok(text) = std::fs::read_to_string(&ack) {
                let line = text.trim();
                if let Some(rest) = line.strip_prefix(&format!("{seq} ")) {
                    let _ = std::fs::remove_file(&ack);
                    return match rest.strip_prefix("ok") {
                        Some(detail) if detail.is_empty() || detail.starts_with(' ') => Ok(detail.trim().to_owned()),
                        _ => Err(format!("WineCap {verb}: {rest}")),
                    };
                }
            }
            if Instant::now() >= until {
                let _ = std::fs::remove_file(&self.file);
                return Err(format!("WineCap {verb}: no answer within {timeout:?}"));
            }
            std::thread::sleep(Duration::from_millis(20));
        }
    }
    pub fn cleanup(&self) {
        for f in [self.file.clone(), self.ready_file(), self.ack_file()] {
            let _ = std::fs::remove_file(f);
        }
    }
}

// ---------------------------------------------------------------- the default-profile swap

/// Put the operator's default profile back after a save that did not finish (supervisor killed mid-swap).
pub fn recover_default(state_dir: &Path, default_path: &Path) -> Result<bool, String> {
    let backup = state_dir.join(DEFAULT_BACKUP);
    let absent = state_dir.join(DEFAULT_ABSENT);
    if backup.is_file() {
        let data = std::fs::read(&backup).map_err(|e| format!("{}: {e}", backup.display()))?;
        super::write_private(default_path, &data)?;
        let _ = std::fs::remove_file(&backup);
        let _ = std::fs::remove_file(&absent);
        return Ok(true);
    }
    if absent.is_file() {
        let _ = std::fs::remove_file(default_path);
        let _ = std::fs::remove_file(&absent);
        return Ok(true);
    }
    Ok(false)
}

/// An flock on `<dir>/<name>`: exclusive (`save.lock` around a default-profile swap, `<file>.lock` around a
/// reload-modify-write) or shared (a pane launch reading the operator's default while no swap runs). A
/// primary and a nested supervisor share the prefix and $XDG_STATE_HOME.
struct Lock(std::fs::File);

impl Lock {
    fn take(dir: &Path, name: &str, exclusive: bool, timeout: Duration) -> Result<Lock, String> {
        use std::os::unix::fs::OpenOptionsExt;
        use std::os::unix::io::AsRawFd;
        super::ensure_private_dir(dir)?;
        let path = dir.join(name);
        let f = std::fs::OpenOptions::new().create(true).truncate(false).write(true).mode(0o600).open(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        let op = if exclusive { libc::LOCK_EX } else { libc::LOCK_SH };
        let until = Instant::now() + timeout;
        loop {
            // SAFETY: flock on an fd we own.
            if unsafe { libc::flock(f.as_raw_fd(), op | libc::LOCK_NB) } == 0 {
                return Ok(Lock(f));
            }
            if Instant::now() >= until {
                return Err(format!("{}: held by another ShaderGlass supervisor", path.display()));
            }
            std::thread::sleep(Duration::from_millis(20));
        }
    }
}

impl Drop for Lock {
    fn drop(&mut self) {
        use std::os::unix::io::AsRawFd;
        // SAFETY: as above.
        unsafe {
            libc::flock(self.0.as_raw_fd(), libc::LOCK_UN);
        }
    }
}

/// [`recover_default`] at a supervisor's start, only while no save runs (another session's swap in progress
/// holds `save.lock`, and its own recovery puts the default back): Ok(false) when that save holds it.
pub fn recover_default_when_idle(state_dir: &Path, default_path: &Path, timeout: Duration) -> Result<bool, String> {
    match Lock::take(state_dir, SAVE_LOCK, true, timeout) {
        Ok(_lock) => recover_default(state_dir, default_path),
        Err(_) => Ok(false),
    }
}

/// Save the pane's live state as `class`'s profile through ShaderGlass' "Set as default" (`run_save` asks the
/// pane and returns once ShaderGlass wrote `default_path`), leaving the operator's own default as it was.
/// Returns the sanitized profile that was recorded.
pub fn save_profile(
    store: &mut Store,
    cfg: &StateCfg,
    class: &str,
    default_path: &Path,
    run_save: impl FnOnce() -> Result<(), String>,
) -> Result<String, String> {
    let _lock = Lock::take(&store.dir, SAVE_LOCK, true, cfg.save_timeout)?;
    recover_default(&store.dir, default_path)?;
    let backup = store.dir.join(DEFAULT_BACKUP);
    let absent = store.dir.join(DEFAULT_ABSENT);
    match std::fs::read(default_path) {
        Ok(old) => super::write_private(&backup, &old)?,
        Err(_) => super::write_private(&absent, b"")?,
    }
    let _ = std::fs::remove_file(default_path);
    let ran = run_save();
    let written = std::fs::read_to_string(default_path);
    // Whatever happened, the operator's default goes back.
    recover_default(&store.dir, default_path)?;
    ran?;
    let text = written.map_err(|_| "ShaderGlass wrote no profile".to_string())?;
    let profile = sanitize_profile(&text, &cfg.strip_keys).ok_or_else(|| "ShaderGlass wrote an unreadable profile".to_string())?;
    store.record_profile(class, &profile)?;
    Ok(profile)
}

/// The profile a pane of `class` launches with (ShaderGlass' last argument, loaded after its default): the
/// class profile when one is saved (and `persist` is on), else a snapshot of the operator's default written to
/// `snapshot`. The snapshot is taken under a shared `save.lock`, i.e. never while a save in any session has
/// the default swapped out, and ShaderGlass reads it instead of the live default -- so a save another session
/// starts while this pane's Wine is still starting cannot leave the pane without the operator's default. When
/// a save holds the lock past `save_timeout`, the backup it made is the operator's default. None: there is no
/// operator default (ShaderGlass' built-in one applies).
pub fn launch_profile(store: &Store, cfg: &StateCfg, class: &str, default_path: &Path, snapshot: &Path) -> Result<Option<PathBuf>, String> {
    if cfg.persist {
        if let Some(p) = store.saved_profile(class) {
            return Ok(Some(p));
        }
    }
    let backup = store.dir.join(DEFAULT_BACKUP);
    let text = match Lock::take(&store.dir, SAVE_LOCK, false, cfg.save_timeout) {
        // No save runs: a backup (or the "absent" mark) is what a killed save left, and it -- not the file
        // ShaderGlass wrote meanwhile -- is the operator's default ([`recover_default`] puts it back).
        Ok(_shared) if backup.is_file() => std::fs::read_to_string(&backup).ok(),
        Ok(_shared) if store.dir.join(DEFAULT_ABSENT).is_file() => None,
        Ok(_shared) => std::fs::read_to_string(default_path).ok(),
        Err(_) => std::fs::read_to_string(&backup).ok(),
    };
    match text.as_deref().and_then(|t| sanitize_profile(t, &cfg.strip_keys)) {
        Some(p) => {
            super::write_private(snapshot, p.as_bytes())?;
            Ok(Some(snapshot.to_owned()))
        }
        None => Ok(None),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cfg() -> StateCfg {
        StateCfg {
            persist: true,
            directory: "mios/shaderglass".into(),
            save_timeout: Duration::from_millis(500),
            restart_limit: 2,
            resume: true,
            strip_keys: ["CaptureWindow", "CaptureDesktop", "InputArea", "CroppedArea"].iter().map(|s| (*s).to_owned()).collect(),
            runtime_state: "mios/shaderglass/active.state".into(),
            room_requests: "mios/shaderglass/room-requests.txt".into(),
        }
    }

    fn store(dir: &Path) -> Store {
        Store::open(dir, PRIMARY_SESSION, Path::new("/"))
    }

    const WRITTEN: &str = "ProfileVersion \"1.1\"\r\nPixelSize \"2\"\r\nShaderCategory \"crt\"\r\nShaderName \"crt-lottes\"\r\nInputArea \"0 0 0 0\"\r\nCroppedArea \"0 0 0 0\"\r\nCaptureWindow \"Choose...\"\r\nParam-1-hardScan \"-6.000000\"\r\n";

    #[test]
    fn slugs_are_file_names_and_never_collide() {
        assert_eq!(class_slug("kitty"), "kitty");
        assert_eq!(class_slug("org.mozilla.firefox"), "org.mozilla.firefox");
        assert_ne!(class_slug("a/b"), class_slug("a_b"), "a sanitized class carries a hash");
        assert_ne!(class_slug("Kitty"), class_slug("kitty"));
        assert!(!class_slug("..").contains('/') && !class_slug("..").starts_with('.'));
        assert!(class_slug("").starts_with('-') || !class_slug("").is_empty());
        assert!(class_slug(&"x".repeat(300)).len() < 80);
    }

    #[test]
    fn windows_paths_go_through_the_z_drive() {
        assert_eq!(windows_path(Path::new("/home/u/.local/state/p.sgp")).unwrap(), "Z:\\home\\u\\.local\\state\\p.sgp");
        assert!(windows_path(Path::new("relative.sgp")).is_none());
        assert!(windows_path(Path::new("/a\"b")).is_none());
    }

    #[test]
    fn sanitizing_strips_the_capture_source_and_rejects_non_profiles() {
        let p = sanitize_profile(WRITTEN, &cfg().strip_keys).unwrap();
        assert!(p.starts_with("ProfileVersion \"1.1\"\r\n"));
        assert!(p.contains("ShaderName \"crt-lottes\"") && p.contains("Param-1-hardScan \"-6.000000\""));
        for k in ["CaptureWindow", "InputArea", "CroppedArea"] {
            assert!(!p.contains(k), "{k} kept");
        }
        assert_eq!(profile_shader(&p).as_deref(), Some("crt/crt-lottes"));
        // Negative controls.
        assert!(sanitize_profile("", &[]).is_none(), "empty");
        assert!(sanitize_profile("ProfileVersion \"2.0\"\r\nShaderName \"x\"\r\n", &[]).is_none(), "future major version");
        assert!(sanitize_profile("ShaderName \"x\"\r\nProfileVersion \"1.1\"\r\n", &[]).is_none(), "version not first");
        assert!(sanitize_profile("ProfileVersion \"1.1\"\r\nShaderName \"cut", &[]).is_none(), "half-written");
        assert!(sanitize_profile("ProfileVersion \"1.1\"\r\nPixelSize \"1\"\r\n", &[]).is_none(), "no shader");
        let quoted = sanitize_profile("ProfileVersion \"1.1\"\r\nShaderPath \"C:\\\\a \\\"b\\\".slang\"\r\n", &[]).unwrap();
        assert_eq!(profile_shader(&quoted).as_deref(), Some("C:\\a \"b\".slang"));
    }

    #[test]
    fn the_store_round_trips_profiles_and_attached_panes() {
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().join("st");
        let mut s = store(&dir);
        assert!(s.saved_profile("kitty").is_none());
        let p = sanitize_profile(WRITTEN, &cfg().strip_keys).unwrap();
        let path = s.record_profile("kitty", &p).unwrap();
        let a = Attached { address: "0xabc".into(), pid: 7, stable_id: "9".into(), class: "kitty".into(), setup: true };
        assert!(s.set_attached(std::slice::from_ref(&a)).unwrap());
        assert!(!s.set_attached(std::slice::from_ref(&a)).unwrap(), "unchanged: no write");
        let s2 = store(&dir);
        assert_eq!(s2.saved_profile("kitty"), Some(path.clone()));
        assert_eq!(s2.class_entry("kitty").unwrap()["shader"], "crt/crt-lottes");
        assert_eq!(s2.attached(), vec![a]);
        assert!(s2.saved_profile("foot").is_none(), "another class has none");
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        assert_eq!(std::fs::metadata(&dir).unwrap().permissions().mode() & 0o777, 0o700);
        // A truncated (0-byte) profile on disk is never handed to ShaderGlass (it would show a modal error).
        std::fs::write(&path, b"").unwrap();
        assert!(store(&dir).saved_profile("kitty").is_none());
        // A corrupt store starts empty instead of failing.
        std::fs::write(dir.join(STORE), b"{not json").unwrap();
        assert!(store(&dir).attached().is_empty());
    }

    #[test]
    fn two_sessions_sharing_the_store_never_lose_each_others_updates() {
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().join("st");
        let nested_rt = d.path().join("nested-run");
        std::fs::create_dir(&nested_rt).unwrap();
        // Both supervisors run at once: each opened the store before the other wrote anything.
        let mut primary = store(&dir);
        let mut nested = Store::open(&dir, "overlay-0badf00d", &nested_rt);
        let p = sanitize_profile(WRITTEN, &cfg().strip_keys).unwrap();
        let a = |addr: &str, class: &str| Attached { address: addr.into(), pid: 1, stable_id: "1".into(), class: class.into(), setup: false };
        primary.record_profile("kitty", &p).unwrap();
        primary.set_attached(&[a("0x1", "kitty")]).unwrap();
        nested.record_profile("foot", &p).unwrap();
        nested.set_attached(&[a("0x2", "foot")]).unwrap();
        primary.record_profile("kitty", &p).unwrap();
        // Each sees the other's class profile, and its own attached record only.
        assert!(primary.saved_profile("foot").is_some(), "the nested session's save is seen by the primary");
        assert!(nested.saved_profile("kitty").is_some());
        assert_eq!(primary.attached(), vec![a("0x1", "kitty")]);
        assert_eq!(nested.attached(), vec![a("0x2", "foot")]);
        // An explicit stop in the nested session clears its own record, never the primary's.
        nested.set_attached(&[]).unwrap();
        assert!(nested.attached().is_empty());
        assert_eq!(store(&dir).attached(), vec![a("0x1", "kitty")], "the primary can still resume");
        assert!(store(&dir).class_entry("foot").is_some() && store(&dir).class_entry("kitty").is_some());
        // Negative control: the old whole-document write-back (load once, write the in-memory copy) loses
        // the update another session made in between.
        let stale = std::fs::read(dir.join(STORE)).unwrap();
        nested.record_profile("alacritty", &p).unwrap();
        std::fs::write(dir.join(STORE), &stale).unwrap();
        assert!(store(&dir).class_entry("alacritty").is_none(), "a stale write-back drops it (what reload-modify-write prevents)");
    }

    #[test]
    fn concurrent_writers_keep_every_class() {
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().join("st");
        let p = sanitize_profile(WRITTEN, &cfg().strip_keys).unwrap();
        let threads: Vec<_> = (0..4)
            .map(|t| {
                let (dir, p) = (dir.clone(), p.clone());
                std::thread::spawn(move || {
                    let mut s = Store::open(&dir, &format!("overlay-{t:08x}"), Path::new("/"));
                    for i in 0..10 {
                        s.record_profile(&format!("class-{t}-{i}"), &p).unwrap();
                    }
                })
            })
            .collect();
        for t in threads {
            t.join().unwrap();
        }
        let doc = store(&dir).doc();
        assert_eq!(doc["classes"].as_object().unwrap().len(), 40, "no lost update under contention");
    }

    #[test]
    fn the_first_store_version_migrates_and_dead_sessions_are_pruned() {
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().join("st");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join(STORE), r#"{"version": 1, "classes": {}, "attached": [{"address": "0x1", "pid": 3, "stable_id": "", "class": "kitty", "setup": false}]}"#).unwrap();
        assert_eq!(store(&dir).attached().len(), 1, "the single list was the primary's");
        let gone = d.path().join("gone-run");
        std::fs::create_dir(&gone).unwrap();
        let mut n = Store::open(&dir, "overlay-00000001", &gone);
        n.set_attached(&[Attached { address: "0x9".into(), pid: 1, stable_id: "".into(), class: "foot".into(), setup: false }]).unwrap();
        assert_eq!(store(&dir).to_status()["sessions"].as_array().unwrap().len(), 2);
        std::fs::remove_dir(&gone).unwrap();
        // The next write anywhere drops the session whose runtime directory is gone; the primary is kept.
        store(&dir).record_profile("kitty", &sanitize_profile(WRITTEN, &[]).unwrap()).unwrap();
        let st = store(&dir).to_status();
        assert_eq!(st["sessions"], json!([PRIMARY_SESSION]));
        assert!(store(&dir).doc().get("attached").is_none(), "migrated, not kept twice");
    }

    #[test]
    fn a_save_keeps_the_operators_default_and_records_the_pane_state() {
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().join("st");
        let default = d.path().join("ShaderGlassDefault.sgp");
        std::fs::write(&default, "ProfileVersion \"1.1\"\r\nShaderName \"operator\"\r\n").unwrap();
        let mut s = store(&dir);
        let c = cfg();
        let p = save_profile(&mut s, &c, "kitty", &default, || {
            std::fs::write(&default, WRITTEN).map_err(|e| e.to_string())
        })
        .unwrap();
        assert!(p.contains("crt-lottes") && !p.contains("CaptureWindow"));
        assert_eq!(std::fs::read_to_string(&default).unwrap(), "ProfileVersion \"1.1\"\r\nShaderName \"operator\"\r\n", "operator default restored");
        assert!(!dir.join(DEFAULT_BACKUP).exists() && !dir.join(DEFAULT_ABSENT).exists());
        // Negative control: a pane that answers without writing must not record the operator's default as its state.
        let mut s = store(&dir);
        let before = std::fs::read_to_string(s.profile_path("foot")).ok();
        assert!(save_profile(&mut s, &c, "foot", &default, || Ok(())).is_err());
        assert_eq!(std::fs::read_to_string(s.profile_path("foot")).ok(), before);
        assert!(default.is_file(), "operator default still there");
        // No operator default: none afterwards either.
        std::fs::remove_file(&default).unwrap();
        save_profile(&mut s, &c, "kitty", &default, || std::fs::write(&default, WRITTEN).map_err(|e| e.to_string())).unwrap();
        assert!(!default.exists(), "no default appeared");
        // A failing pane leaves everything as it was.
        assert!(save_profile(&mut s, &c, "kitty", &default, || Err("boom".into())).is_err());
        assert!(!default.exists());
    }

    #[test]
    fn an_interrupted_swap_is_recovered() {
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().join("st");
        std::fs::create_dir_all(&dir).unwrap();
        let default = d.path().join("ShaderGlassDefault.sgp");
        // Killed after backing up and removing the default.
        std::fs::write(dir.join(DEFAULT_BACKUP), "ProfileVersion \"1.1\"\r\nShaderName \"operator\"\r\n").unwrap();
        assert!(recover_default(&dir, &default).unwrap());
        assert!(std::fs::read_to_string(&default).unwrap().contains("operator"));
        assert!(!recover_default(&dir, &default).unwrap(), "nothing left to recover");
        // Killed after ShaderGlass wrote a pane state where there was no default.
        std::fs::write(dir.join(DEFAULT_ABSENT), b"").unwrap();
        std::fs::write(&default, WRITTEN).unwrap();
        assert!(recover_default(&dir, &default).unwrap());
        assert!(!default.exists());
    }

    #[test]
    fn a_pane_launch_never_starts_without_the_operators_default() {
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().join("st");
        let default = d.path().join("ShaderGlassDefault.sgp");
        let snap = d.path().join("pane.default.sgp");
        const OPERATOR: &str = "ProfileVersion \"1.1\"\r\nShaderName \"operator\"\r\nCaptureWindow \"x\"\r\n";
        std::fs::write(&default, OPERATOR).unwrap();
        let c = cfg();
        let mut s = store(&dir);
        // No class profile: a sanitized snapshot of the operator's default.
        let p = launch_profile(&s, &c, "kitty", &default, &snap).unwrap().unwrap();
        assert_eq!(p, snap);
        let text = std::fs::read_to_string(&snap).unwrap();
        assert!(text.contains("operator") && !text.contains("CaptureWindow"), "{text}");
        // Another session is mid-save (default swapped out, save.lock held): the snapshot is still the
        // operator's default, from the backup, not the missing file or the pane state ShaderGlass writes.
        let (tx, rx) = std::sync::mpsc::channel();
        let (dir2, default2) = (dir.clone(), default.clone());
        let saver = std::thread::spawn(move || {
            let mut other = Store::open(&dir2, "overlay-00000002", Path::new("/"));
            save_profile(&mut other, &cfg(), "foot", &default2, || {
                tx.send(()).unwrap();
                std::thread::sleep(Duration::from_millis(900));
                std::fs::write(&default2, WRITTEN).map_err(|e| e.to_string())
            })
            .unwrap();
        });
        rx.recv().unwrap();
        assert!(!default.exists(), "the swap removed the operator's default");
        let short = StateCfg { save_timeout: Duration::from_millis(200), ..c.clone() };
        let _ = std::fs::remove_file(&snap);
        launch_profile(&s, &short, "kitty", &default, &snap).unwrap();
        assert!(std::fs::read_to_string(&snap).unwrap().contains("operator"), "the backup is the operator's default");
        // Negative control: reading the live default at that moment (what a launch without the lock did) gets nothing.
        assert!(std::fs::read_to_string(&default).is_err());
        saver.join().unwrap();
        assert!(std::fs::read_to_string(&default).unwrap().contains("operator"), "restored after the save");
        // A saved class profile wins.
        let path = s.record_profile("kitty", &sanitize_profile(WRITTEN, &c.strip_keys).unwrap()).unwrap();
        assert_eq!(launch_profile(&s, &c, "kitty", &default, &snap).unwrap(), Some(path));
        // persist off: the default, never the class profile.
        let off = StateCfg { persist: false, ..c.clone() };
        assert_eq!(launch_profile(&s, &off, "kitty", &default, &snap).unwrap(), Some(snap.clone()));
        // A killed save left its backup: it is the operator's default, not what ShaderGlass wrote.
        std::fs::write(dir.join(DEFAULT_BACKUP), OPERATOR).unwrap();
        std::fs::write(&default, WRITTEN).unwrap();
        launch_profile(&s, &off, "foot", &default, &snap).unwrap();
        assert!(std::fs::read_to_string(&snap).unwrap().contains("operator"));
        // No operator default at all: ShaderGlass' built-in default.
        std::fs::remove_file(dir.join(DEFAULT_BACKUP)).unwrap();
        std::fs::remove_file(&default).unwrap();
        assert_eq!(launch_profile(&s, &off, "foot", &default, &snap).unwrap(), None);
    }

    #[test]
    fn a_starting_supervisor_never_recovers_under_another_sessions_save() {
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().join("st");
        std::fs::create_dir_all(&dir).unwrap();
        let default = d.path().join("ShaderGlassDefault.sgp");
        std::fs::write(dir.join(DEFAULT_BACKUP), "ProfileVersion \"1.1\"\r\nShaderName \"operator\"\r\n").unwrap();
        let held = Lock::take(&dir, SAVE_LOCK, true, Duration::from_millis(50)).unwrap();
        assert!(!recover_default_when_idle(&dir, &default, Duration::from_millis(100)).unwrap(), "a save runs: left to it");
        assert!(!default.exists() && dir.join(DEFAULT_BACKUP).exists());
        drop(held);
        assert!(recover_default_when_idle(&dir, &default, Duration::from_millis(100)).unwrap(), "a killed save's backup is restored");
        assert!(std::fs::read_to_string(&default).unwrap().contains("operator"));
    }

    #[test]
    fn the_command_channel_needs_a_ready_wine_cap_and_matches_sequence_numbers() {
        let d = tempfile::tempdir().unwrap();
        let file = d.path().join("p.cmd");
        let mut c = Command::new(file.clone());
        let e = c.run("save", Duration::from_millis(50)).unwrap_err();
        assert!(e.contains("no MiOS command channel"), "{e}");
        assert!(!file.exists(), "nothing written for an old WineCap");
        assert!(c.run("frobnicate", Duration::from_millis(50)).is_err());
        std::fs::write(c.ready_file(), b"1").unwrap();
        // A fake WineCap: answers the request it finds.
        let f2 = file.clone();
        let ack = c.ack_file();
        let t = std::thread::spawn(move || {
            for _ in 0..200 {
                if let Ok(s) = std::fs::read_to_string(&f2) {
                    let seq = s.split_whitespace().next().unwrap().to_owned();
                    std::fs::remove_file(&f2).unwrap();
                    // A stale answer first: must be ignored.
                    std::fs::write(&ack, "0 ok\n").unwrap();
                    std::thread::sleep(Duration::from_millis(30));
                    std::fs::write(&ack, format!("{seq} ok menu=1\n")).unwrap();
                    return;
                }
                std::thread::sleep(Duration::from_millis(5));
            }
        });
        assert_eq!(menu_shown(&c.run("menu-on", Duration::from_secs(2)).unwrap()), Some(true));
        t.join().unwrap();
        assert_eq!(menu_shown("menu=0"), Some(false));
        assert_eq!(menu_shown(""), None, "an older WineCap answers without the menu state");
        // An error answer and an "okay"-looking prefix are failures.
        let f3 = file.clone();
        let ack3 = c.ack_file();
        let t = std::thread::spawn(move || {
            for _ in 0..200 {
                if let Ok(s) = std::fs::read_to_string(&f3) {
                    let seq = s.split_whitespace().next().unwrap().to_owned();
                    std::fs::remove_file(&f3).unwrap();
                    std::fs::write(&ack3, format!("{seq} okay\n")).unwrap();
                    return;
                }
                std::thread::sleep(Duration::from_millis(5));
            }
        });
        assert!(c.run("next", Duration::from_secs(2)).is_err(), "\"okay\" is not \"ok\"");
        t.join().unwrap();
        assert!(c.run("menu", Duration::from_millis(50)).unwrap_err().contains("unknown"), "the relative toggle is gone");
        assert!(c.run("save", Duration::from_millis(100)).unwrap_err().contains("no answer"), "nobody answers");
        assert!(!file.exists(), "an unanswered request is withdrawn");
        c.cleanup();
        assert!(!c.ready());
    }
}
