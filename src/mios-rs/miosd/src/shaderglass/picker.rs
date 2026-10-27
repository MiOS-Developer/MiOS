// AI-hint: miosd shaderglass picker: the xdg-desktop-portal-hyprland custom picker (answers the supervisor's one-shot request with the target window, execs the stock picker for every other screencast), the one-shot request files, the per-class restore-token store and the xdph.conf projection.
// AI-related: src/mios-rs/miosd/src/shaderglass.rs, usr/libexec/mios/mios-share-picker, usr/share/mios/mios.toml [desktop.hyprland.xdph] [desktop.shaderglass.capture]
//! xdph runs `custom_picker_binary` for every ScreenCast SelectSources that has no valid restore data,
//! with the window list in XDPH_WINDOW_SHARING_LIST (`<handle>[HC>]<class>[HT>]<title>[HE>]<hyprland
//! window address, decimal>[HA>]` per window) and reads `[SELECTION]<flags>/window:<handle>` from stdout
//! (flag `r` = the selection may be restored by token). xdph does not say who asked, so a request is a
//! file the supervisor writes just before it starts ShaderGlass: claimed by atomic rename, used once,
//! valid for request_ttl_ms and only while the supervisor that wrote it is alive. The supervisor starts
//! one pane at a time, so a claimed request always belongs to the ShaderGlass that is asking.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::time::{SystemTime, UNIX_EPOCH};

use serde_json::{json, Value as Json};

pub fn now_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_millis() as u64)
        .unwrap_or(0)
}

/// One window of XDPH_WINDOW_SHARING_LIST.
#[derive(Clone, Debug, PartialEq)]
pub struct ShareWindow {
    pub handle: u64,
    pub class: String,
    pub title: String,
    /// Hyprland window address (0 when xdph has no toplevel mapping).
    pub address: u64,
}

pub fn parse_sharing_list(s: &str) -> Vec<ShareWindow> {
    s.split("[HA>]")
        .filter_map(|entry| {
            let (handle, rest) = entry.split_once("[HC>]")?;
            let (class, rest) = rest.split_once("[HT>]")?;
            let (title, address) = rest.split_once("[HE>]")?;
            Some(ShareWindow {
                handle: handle.trim().parse().ok()?,
                class: class.to_owned(),
                title: title.to_owned(),
                address: parse_address(address.trim())?,
            })
        })
        .collect()
}

/// A Hyprland window address as hyprctl prints it (0x hex) or as xdph lists it (decimal).
pub fn parse_address(a: &str) -> Option<u64> {
    match a.strip_prefix("0x") {
        Some(hex) => u64::from_str_radix(hex, 16).ok(),
        None => a.parse().ok(),
    }
}

/// A one-shot picker request.
#[derive(Clone, Debug, PartialEq)]
pub struct Request {
    pub target: String,
    pub expires_ms: u64,
    pub supervisor_pid: u32,
}

impl Request {
    pub fn to_json(&self) -> Json {
        json!({"target": self.target, "expires_ms": self.expires_ms, "supervisor_pid": self.supervisor_pid})
    }

    pub fn from_json(v: &Json) -> Option<Request> {
        Some(Request {
            target: v.get("target")?.as_str()?.to_owned(),
            expires_ms: v.get("expires_ms")?.as_u64()?,
            supervisor_pid: u32::try_from(v.get("supervisor_pid")?.as_u64()?).ok()?,
        })
    }
}

/// The selection line for `req`, or None (expired, writer gone, target not offered).
pub fn answer(req: &Request, windows: &[ShareWindow], now: u64, writer_alive: bool, allow_token: bool) -> Option<String> {
    if now > req.expires_ms || !writer_alive {
        return None;
    }
    let want = parse_address(&req.target)?;
    let w = windows.iter().find(|w| w.address == want && w.address != 0)?;
    Some(format!("[SELECTION]{}/window:{}", if allow_token { "r" } else { "" }, w.handle))
}

pub fn pid_alive(pid: u32) -> bool {
    pid != 0 && Path::new(&format!("/proc/{pid}")).exists()
}

pub fn requests_dir(runtime: &Path) -> PathBuf {
    runtime.join("picks")
}

/// Write a request atomically (0600), named so the oldest sorts first.
pub fn write_request(runtime: &Path, req: &Request) -> Result<PathBuf, String> {
    let dir = requests_dir(runtime);
    super::ensure_private_dir(&dir)?;
    let path = dir.join(format!("{:020}-{}.json", now_ms(), std::process::id()));
    super::write_private(&path, &serde_json::to_vec(&req.to_json()).map_err(|e| e.to_string())?)?;
    Ok(path)
}

/// Claim the oldest request that answers this picker run; stale or unusable ones are removed on the way.
pub fn claim(runtime: &Path, windows: &[ShareWindow], allow_token: bool) -> Option<String> {
    let dir = requests_dir(runtime);
    let mut names: Vec<PathBuf> = std::fs::read_dir(&dir)
        .ok()?
        .flatten()
        .map(|e| e.path())
        .filter(|p| p.extension().is_some_and(|x| x == "json"))
        .collect();
    names.sort();
    for path in names {
        let claimed = path.with_extension("claimed");
        if std::fs::rename(&path, &claimed).is_err() {
            continue; // another picker run took it
        }
        let req = std::fs::read(&claimed)
            .ok()
            .and_then(|b| serde_json::from_slice::<Json>(&b).ok())
            .and_then(|v| Request::from_json(&v));
        let _ = std::fs::remove_file(&claimed);
        if let Some(req) = req {
            if let Some(line) = answer(&req, windows, now_ms(), pid_alive(req.supervisor_pid), allow_token) {
                return Some(line);
            }
        }
    }
    None
}

/// File identity changes when WineCap atomically saves a completed portal response,
/// even if a restored session returns exactly the same token bytes.
pub fn token_generation(path: &Path) -> Option<(u64, u64)> {
    use std::os::unix::fs::MetadataExt;
    let metadata = std::fs::metadata(path).ok()?;
    (metadata.is_file() && metadata.len() > 0).then(|| (metadata.dev(), metadata.ino()))
}

/// A mapped window is not capture readiness. A picker consumes its request; a
/// restored session bypasses the picker and saves a new token instead. Retire
/// that unused request before another capture can claim it. Cancellation uses
/// the same cleanup path, and a cleanup failure keeps the launch gate closed.
pub fn retire_request(
    request: &Path,
    token_file: Option<&Path>,
    token_seed: Option<(u64, u64)>,
    cancel: bool,
) -> Result<bool, String> {
    if !request.try_exists().map_err(|e| format!("{}: {e}", request.display()))? {
        return Ok(true);
    }
    let restored = token_file.and_then(token_generation)
        .is_some_and(|generation| Some(generation) != token_seed);
    if !cancel && !restored {
        return Ok(false);
    }
    match std::fs::remove_file(request) {
        Ok(()) => Ok(true),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => Ok(true),
        Err(error) => Err(format!("{}: {error}", request.display())),
    }
}

/// The restore token for a target: the stored one for its class when it was issued for this very window,
/// or when the class has only this one window (xdph restores a window by handle, else by class).
pub fn choose_token(store: &Json, class: &str, target: &str, windows_of_class: usize) -> Option<String> {
    let e = store.get(class)?;
    let token = e.get("token")?.as_str().filter(|t| !t.is_empty() && t.len() < 512)?;
    let same = e.get("address").and_then(Json::as_str) == Some(target);
    (same || windows_of_class == 1).then(|| token.to_owned())
}

pub fn remember_token(store: &mut Json, class: &str, target: &str, token: &str) -> bool {
    if token.is_empty() || token.len() >= 512 || token.contains('\n') {
        return false;
    }
    if !store.is_object() {
        *store = json!({});
    }
    let changed = store.get(class).and_then(|e| e.get("token")).and_then(Json::as_str) != Some(token)
        || store.get(class).and_then(|e| e.get("address")).and_then(Json::as_str) != Some(target);
    if let Some(map) = store.as_object_mut() {
        map.insert(class.to_owned(), json!({"token": token, "address": target, "saved_ms": now_ms()}));
    }
    changed
}

/// xdph.conf from [desktop.hyprland.xdph] (hyprlang).
pub fn xdph_conf(allow_token_by_default: bool, custom_picker_binary: &str) -> Result<String, String> {
    if custom_picker_binary.chars().any(|c| c.is_control() || c == '#' || c == '{' || c == '}') {
        return Err("desktop.hyprland.xdph.custom_picker_binary must be a plain path".into());
    }
    let mut out = String::from(
        "# AI-hint: Generated by `miosd shaderglass portal-config --write` from usr/share/mios/mios.toml [desktop.hyprland.xdph]; edit the SSOT.\n\
         # AI-related: src/mios-rs/miosd/src/shaderglass/picker.rs, usr/libexec/mios/mios-share-picker\n\
         screencopy {\n",
    );
    out += &format!("    allow_token_by_default = {allow_token_by_default}\n");
    if !custom_picker_binary.is_empty() {
        out += &format!("    custom_picker_binary = {custom_picker_binary}\n");
    }
    out += "}\n";
    Ok(out)
}

/// Parse the picker's own arguments (xdph passes `--allow-token` when allow_token_by_default is set).
pub fn picker_args(args: &[String]) -> (bool, bool) {
    let probe = args.iter().any(|a| a == "--probe");
    let allow = args.iter().any(|a| a == "--allow-token");
    (probe, allow)
}

pub fn load_store(path: &Path) -> Json {
    std::fs::read(path)
        .ok()
        .and_then(|b| serde_json::from_slice(&b).ok())
        .filter(Json::is_object)
        .unwrap_or_else(|| json!({}))
}

/// Windows of each class, for choose_token.
pub fn class_counts(clients: &[Json]) -> BTreeMap<String, usize> {
    let mut out = BTreeMap::new();
    for c in clients {
        if let Some(class) = c.get("class").and_then(Json::as_str) {
            *out.entry(class.to_owned()).or_insert(0) += 1;
        }
    }
    out
}

#[cfg(test)]
mod tests {
    use super::*;

    const LIST: &str = "12345[HC>]kitty[HT>]~/src[HE>]94211842195456[HA>]678[HC>]zen[HT>]Docs [HE>]0[HA>]";

    #[test]
    fn parses_xdph_window_list() {
        let w = parse_sharing_list(LIST);
        assert_eq!(w.len(), 2);
        assert_eq!(w[0], ShareWindow { handle: 12345, class: "kitty".into(), title: "~/src".into(), address: 94211842195456 });
        assert_eq!(w[1].address, 0);
        assert!(parse_sharing_list("").is_empty());
        assert!(parse_sharing_list("garbage[HA>]1[HC>]x[HA>]").is_empty());
        assert_eq!(parse_address("0x55aa"), Some(0x55aa));
        assert_eq!(parse_address("21930"), Some(21930));
        assert_eq!(parse_address("0xzz"), None);
    }

    #[test]
    fn answers_only_a_live_unexpired_request_for_an_offered_window() {
        let w = parse_sharing_list(LIST);
        let target = format!("0x{:x}", 94211842195456u64);
        let req = Request { target: target.clone(), expires_ms: 1_000, supervisor_pid: 1 };
        assert_eq!(answer(&req, &w, 500, true, true).as_deref(), Some("[SELECTION]r/window:12345"));
        assert_eq!(answer(&req, &w, 500, true, false).as_deref(), Some("[SELECTION]/window:12345"));
        assert_eq!(answer(&req, &w, 1_001, true, true), None, "expired");
        assert_eq!(answer(&req, &w, 500, false, true), None, "writer gone");
        let other = Request { target: "0x1".into(), ..req.clone() };
        assert_eq!(answer(&other, &w, 500, true, true), None, "target not offered");
        // An address xdph could not map (0) never matches.
        let zero = Request { target: "0".into(), ..req };
        assert_eq!(answer(&zero, &w, 500, true, true), None);
    }

    #[test]
    fn claim_is_one_shot_and_drops_stale_requests() {
        let dir = tempfile::tempdir().unwrap();
        let w = parse_sharing_list(LIST);
        let me = std::process::id();
        // A stale request first (expired), then a good one: the good one answers, both are consumed.
        let stale = Request { target: "0x1".into(), expires_ms: 1, supervisor_pid: me };
        write_request(dir.path(), &stale).unwrap();
        std::thread::sleep(std::time::Duration::from_millis(2));
        let good = Request { target: format!("0x{:x}", 94211842195456u64), expires_ms: now_ms() + 60_000, supervisor_pid: me };
        let p = write_request(dir.path(), &good).unwrap();
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(std::fs::metadata(&p).unwrap().permissions().mode() & 0o777, 0o600);
        assert_eq!(claim(dir.path(), &w, true).as_deref(), Some("[SELECTION]r/window:12345"));
        assert_eq!(claim(dir.path(), &w, true), None, "used once");
        assert_eq!(std::fs::read_dir(requests_dir(dir.path())).unwrap().count(), 0);
        // No request directory at all: nothing to answer (the stock picker runs).
        let empty = tempfile::tempdir().unwrap();
        assert_eq!(claim(empty.path(), &w, true), None);
    }

    #[test]
    fn tokens_are_reused_only_for_the_same_window_or_a_single_window_class() {
        let mut store = json!({});
        assert!(remember_token(&mut store, "kitty", "0xa", "tok-1"));
        assert!(!remember_token(&mut store, "kitty", "0xa", "tok-1"), "unchanged");
        assert_eq!(choose_token(&store, "kitty", "0xa", 3).as_deref(), Some("tok-1"), "same window");
        assert_eq!(choose_token(&store, "kitty", "0xb", 1).as_deref(), Some("tok-1"), "only window of its class");
        assert_eq!(choose_token(&store, "kitty", "0xb", 2), None, "ambiguous: the portal would restore another window");
        assert_eq!(choose_token(&store, "zen", "0xa", 1), None);
        assert!(!remember_token(&mut store, "kitty", "0xa", "bad\ntoken"));
        let mut junk = json!([1, 2]);
        assert!(remember_token(&mut junk, "c", "0x1", "t"));
        assert!(junk.is_object());
    }

    #[test]
    fn xdph_conf_projection_and_picker_args() {
        let c = xdph_conf(true, "/usr/libexec/mios/mios-share-picker").unwrap();
        assert!(c.contains("screencopy {\n    allow_token_by_default = true\n    custom_picker_binary = /usr/libexec/mios/mios-share-picker\n}\n"), "{c}");
        assert!(!xdph_conf(false, "").unwrap().contains("custom_picker_binary"));
        assert!(xdph_conf(true, "/x}\nscreencopy {").is_err());
        let a = |v: &[&str]| picker_args(&v.iter().map(|s| s.to_string()).collect::<Vec<_>>());
        assert_eq!(a(&["--allow-token"]), (false, true));
        assert_eq!(a(&["--probe"]), (true, false));
        assert_eq!(a(&[]), (false, false));
    }

    #[test]
    fn restored_capture_retires_request_after_atomic_token_replacement() {
        let dir = tempfile::tempdir().unwrap();
        let request = dir.path().join("mios-pick.json");
        let token = dir.path().join("mios-token");
        let replacement = dir.path().join("mios-token-next");
        std::fs::write(&request, "{}").unwrap();
        std::fs::write(&token, "same-token").unwrap();
        let seed = token_generation(&token);
        assert!(seed.is_some());
        assert!(!retire_request(&request, Some(&token), seed, false).unwrap(),
                "mapping alone must not release an unconsumed request");
        std::fs::write(&replacement, "same-token").unwrap();
        std::fs::rename(&replacement, &token).unwrap();
        assert_ne!(token_generation(&token), seed);
        assert!(retire_request(&request, Some(&token), seed, false).unwrap());
        assert!(!request.exists(), "a later picker must not claim the restored session's request");
    }

    #[test]
    fn request_gate_handles_consumption_cancellation_and_cleanup_errors() {
        let dir = tempfile::tempdir().unwrap();
        let request = dir.path().join("mios-pick.json");
        std::fs::write(&request, "{}").unwrap();
        assert!(!retire_request(&request, None, None, false).unwrap(),
                "without tokens only picker consumption can release a live request");
        assert!(retire_request(&request, None, None, true).unwrap(),
                "closing a pane cancels its request before the next launch");
        assert!(!request.exists());
        assert!(retire_request(&request, None, None, false).unwrap(),
                "a consumed request opens the gate");
        std::fs::create_dir(&request).unwrap();
        assert!(retire_request(&request, None, None, true).is_err(),
                "cleanup failure must hold the launch gate closed");
        assert!(request.is_dir());
    }
}
