// AI-hint: miosd shaderglass state: per-class ShaderGlass profiles and the pane/window-state store that survives pane restarts, supervisor restarts and every 2D/3D presentation mode -- the WineCap command channel (save/next/menu inside the ShaderGlass process), the crash-safe default-profile swap that turns ShaderGlass' "Set as default" into a per-class profile, and the attached-pane record a restarted supervisor resumes.
// AI-related: src/mios-rs/miosd/src/shaderglass.rs, usr/share/mios/shaderglass/patches/winecap-mios-portal.patch, usr/share/mios/mios.toml [desktop.shaderglass.state]
// AI-functions: StateCfg, Store, Attached, Command, sanitize_profile, profile_shader, windows_path, class_slug, save_profile, recover_default

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
}

/// The file names inside the state directory.
pub const STORE: &str = "state.json";
pub const PROFILES: &str = "profiles";
pub const DEFAULT_BACKUP: &str = "default-backup.sgp";
/// Marks "there was no operator default" for [`recover_default`].
pub const DEFAULT_ABSENT: &str = "default-backup.absent";
pub const SAVE_LOCK: &str = "save.lock";

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

/// $XDG_STATE_HOME/<directory>/state.json: {"version": 1, "classes": {class: {profile, shader, saved_ms}},
/// "attached": [...]}, 0600 in a 0700 directory, written atomically.
#[derive(Debug)]
pub struct Store {
    pub dir: PathBuf,
    doc: Json,
}

impl Store {
    pub fn load(dir: &Path) -> Store {
        let doc = std::fs::read(dir.join(STORE))
            .ok()
            .and_then(|b| serde_json::from_slice::<Json>(&b).ok())
            .filter(|d| d.get("version").and_then(Json::as_u64) == Some(1))
            .unwrap_or_else(|| json!({"version": 1, "classes": {}, "attached": []}));
        Store { dir: dir.to_owned(), doc }
    }

    pub fn save(&self) -> Result<(), String> {
        super::ensure_private_dir(&self.dir)?;
        super::write_private(&self.dir.join(STORE), serde_json::to_string_pretty(&self.doc).map_err(|e| e.to_string())?.as_bytes())
    }

    pub fn profile_path(&self, class: &str) -> PathBuf {
        self.dir.join(PROFILES).join(format!("{}.sgp", class_slug(class)))
    }

    /// The class profile a pane launches with: recorded and still a valid profile on disk.
    pub fn saved_profile(&self, class: &str) -> Option<PathBuf> {
        self.doc.pointer(&format!("/classes/{}", json_pointer_escape(class)))?;
        let p = self.profile_path(class);
        let text = std::fs::read_to_string(&p).ok()?;
        sanitize_profile(&text, &[]).map(|_| p)
    }

    #[cfg(test)]
    pub fn class_entry(&self, class: &str) -> Option<&Json> {
        self.doc.get("classes")?.get(class)
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
        if !self.doc.get("classes").is_some_and(Json::is_object) {
            self.doc["classes"] = json!({});
        }
        self.doc["classes"][class] = entry;
        self.save()?;
        Ok(path)
    }

    pub fn attached(&self) -> Vec<Attached> {
        self.doc.get("attached").and_then(Json::as_array).into_iter().flatten().filter_map(Attached::from_json).collect()
    }

    /// Replace the attached record; writes only on change.
    pub fn set_attached(&mut self, list: &[Attached]) -> Result<bool, String> {
        let new = Json::Array(list.iter().map(Attached::to_json).collect());
        if self.doc.get("attached") == Some(&new) {
            return Ok(false);
        }
        self.doc["attached"] = new;
        self.save()?;
        Ok(true)
    }

    pub fn to_status(&self) -> Json {
        json!({"dir": self.dir, "classes": self.doc.get("classes").cloned().unwrap_or(json!({})), "attached": self.doc.get("attached").cloned().unwrap_or(json!([]))})
    }
}

fn json_pointer_escape(s: &str) -> String {
    s.replace('~', "~0").replace('/', "~1")
}

// ---------------------------------------------------------------- WineCap command channel

/// The command file WineCap polls inside one ShaderGlass process (MIOS_WINECAP_COMMAND_FILE): the supervisor
/// writes "<seq> <verb>\n" atomically; WineCap claims it, sends the menu command to the ShaderGlass main
/// window (SendMessageTimeout: done when ShaderGlass handled it) and writes "<seq> ok" (or "<seq> error ...")
/// to `<file>.ack`. WineCap creates `<file>.ready` when its channel runs: an older WineCap without it is
/// detected at once, never waited for.
#[derive(Debug)]
pub struct Command {
    pub file: PathBuf,
    seq: u64,
}

/// The verbs WineCap knows: ShaderGlass' "Set as default", IDM_SHADER_NEXT, IDM_TOGGLEMENU.
pub const VERBS: &[&str] = &["save", "next", "menu"];

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
    /// Run `verb` in the pane; Ok once ShaderGlass handled it.
    pub fn run(&mut self, verb: &str, timeout: Duration) -> Result<(), String> {
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
                    return if rest == "ok" { Ok(()) } else { Err(format!("WineCap {verb}: {rest}")) };
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

/// An exclusive flock on `<state dir>/save.lock` (a primary and a nested supervisor share the prefix).
struct Lock(std::fs::File);

impl Lock {
    fn take(dir: &Path, timeout: Duration) -> Result<Lock, String> {
        use std::os::unix::fs::OpenOptionsExt;
        use std::os::unix::io::AsRawFd;
        super::ensure_private_dir(dir)?;
        let path = dir.join(SAVE_LOCK);
        let f = std::fs::OpenOptions::new().create(true).truncate(false).write(true).mode(0o600).open(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        let until = Instant::now() + timeout;
        loop {
            // SAFETY: flock on an fd we own.
            if unsafe { libc::flock(f.as_raw_fd(), libc::LOCK_EX | libc::LOCK_NB) } == 0 {
                return Ok(Lock(f));
            }
            if Instant::now() >= until {
                return Err(format!("{}: another ShaderGlass save holds the lock", path.display()));
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
    let _lock = Lock::take(&store.dir, cfg.save_timeout)?;
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
        }
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
        let mut s = Store::load(&dir);
        assert!(s.saved_profile("kitty").is_none());
        let p = sanitize_profile(WRITTEN, &cfg().strip_keys).unwrap();
        let path = s.record_profile("kitty", &p).unwrap();
        let a = Attached { address: "0xabc".into(), pid: 7, stable_id: "9".into(), class: "kitty".into(), setup: true };
        assert!(s.set_attached(std::slice::from_ref(&a)).unwrap());
        assert!(!s.set_attached(std::slice::from_ref(&a)).unwrap(), "unchanged: no write");
        let s2 = Store::load(&dir);
        assert_eq!(s2.saved_profile("kitty"), Some(path.clone()));
        assert_eq!(s2.class_entry("kitty").unwrap()["shader"], "crt/crt-lottes");
        assert_eq!(s2.attached(), vec![a]);
        assert!(s2.saved_profile("foot").is_none(), "another class has none");
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o600);
        assert_eq!(std::fs::metadata(&dir).unwrap().permissions().mode() & 0o777, 0o700);
        // A truncated (0-byte) profile on disk is never handed to ShaderGlass (it would show a modal error).
        std::fs::write(&path, b"").unwrap();
        assert!(Store::load(&dir).saved_profile("kitty").is_none());
        // A corrupt store starts empty instead of failing.
        std::fs::write(dir.join(STORE), b"{not json").unwrap();
        assert!(Store::load(&dir).attached().is_empty());
    }

    #[test]
    fn a_save_keeps_the_operators_default_and_records_the_pane_state() {
        let d = tempfile::tempdir().unwrap();
        let dir = d.path().join("st");
        let default = d.path().join("ShaderGlassDefault.sgp");
        std::fs::write(&default, "ProfileVersion \"1.1\"\r\nShaderName \"operator\"\r\n").unwrap();
        let mut s = Store::load(&dir);
        let c = cfg();
        let p = save_profile(&mut s, &c, "kitty", &default, || {
            std::fs::write(&default, WRITTEN).map_err(|e| e.to_string())
        })
        .unwrap();
        assert!(p.contains("crt-lottes") && !p.contains("CaptureWindow"));
        assert_eq!(std::fs::read_to_string(&default).unwrap(), "ProfileVersion \"1.1\"\r\nShaderName \"operator\"\r\n", "operator default restored");
        assert!(!dir.join(DEFAULT_BACKUP).exists() && !dir.join(DEFAULT_ABSENT).exists());
        // Negative control: a pane that answers without writing must not record the operator's default as its state.
        let mut s = Store::load(&dir);
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
                    std::fs::write(&ack, format!("{seq} ok\n")).unwrap();
                    return;
                }
                std::thread::sleep(Duration::from_millis(5));
            }
        });
        c.run("next", Duration::from_secs(2)).unwrap();
        t.join().unwrap();
        assert!(c.run("save", Duration::from_millis(100)).unwrap_err().contains("no answer"), "nobody answers");
        assert!(!file.exists(), "an unanswered request is withdrawn");
        c.cleanup();
        assert!(!c.ready());
    }
}
