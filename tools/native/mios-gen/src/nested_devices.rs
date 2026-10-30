// AI-hint: Nested-session device assignment ([desktop.nested.devices]): resolve CLI/SSOT device specs to evdev nodes, classify them from sysfs capabilities, apply the policy (kinds, virtual nodes, exclusivity across live sessions) and keep the per-session registry the input daemon reads.
// AI-related: tools/native/mios-gen/src/nested_session.rs, usr/share/mios/mios.toml [desktop.nested.devices] [desktop.nested.seats], tests/test-nested-multiuse.py, tests/test-nested-multiuse-live.py
// AI-functions: resolve, classify, Claim, live_sessions, bind_args
//! A session's /dev/input is an empty directory unless devices are assigned. Each assigned node (and, by policy, its
//! legacy joystick sibling) is bind-mounted into the session's namespace at its own path, so applications and an
//! in-session input bridge open exactly those nodes. The registry `$XDG_RUNTIME_DIR/<registry_dir>/<seat>.json` is the
//! contract with mios-inputd: it lists the live session, its compositor socket and its assigned nodes, so the bridge
//! can stop routing them to the operator's desktop and forward keyboard/pointer input into the session.
use serde_json::{json, Value as Json};
use std::{
    fs,
    io::Write,
    os::unix::fs::{FileTypeExt, MetadataExt},
    path::{Path, PathBuf},
    thread,
    time::{Duration, Instant},
};
use toml::Value;

/// evdev input major number (Documentation/admin-guide/devices.txt).
const INPUT_MAJOR: u64 = 13;
const EV_KEY: usize = 0x01;
const EV_REL: usize = 0x02;
const EV_ABS: usize = 0x03;
const BTN_LEFT: usize = 0x110;
const BTN_JOYSTICK: usize = 0x120;
const BTN_GAMEPAD: usize = 0x130;
const BTN_TOUCH: usize = 0x14a;
const KEY_Q: usize = 16;
const KEY_P: usize = 25;
const KEY_A: usize = 30;
const KEY_L: usize = 38;
const KEY_Z: usize = 44;
const KEY_M: usize = 50;

#[derive(Debug, Clone, PartialEq)]
pub struct Device {
    /// Canonical node, always /dev/input/eventN.
    pub path: PathBuf,
    /// The spec it was resolved from (CLI or SSOT text).
    pub spec: String,
    pub name: String,
    pub kind: String,
    pub virtual_node: bool,
    /// Legacy joystick siblings (/dev/input/jsN) bound with it.
    pub siblings: Vec<PathBuf>,
}

impl Device {
    pub fn to_json(&self) -> Json {
        json!({"path": self.path, "spec": self.spec, "name": self.name, "kind": self.kind, "virtual": self.virtual_node, "siblings": self.siblings})
    }
    pub fn nodes(&self) -> impl Iterator<Item = &PathBuf> {
        std::iter::once(&self.path).chain(self.siblings.iter())
    }
}

#[derive(Debug, Clone, PartialEq)]
pub struct Policy {
    pub default: Vec<String>,
    pub allow_kinds: Vec<String>,
    pub allow_virtual: bool,
    pub exclusive: bool,
    pub include_joystick: bool,
    pub virtual_pad_name: String,
    pub virtual_pad_wait_ms: u64,
    pub registry_dir: String,
}

pub const KINDS: [&str; 4] = ["controller", "keyboard", "pointer", "other"];

fn strings(v: Option<&Value>, key: &str) -> Result<Vec<String>, String> {
    match v {
        None => Ok(Vec::new()),
        Some(Value::Array(a)) => a
            .iter()
            .map(|s| s.as_str().filter(|s| !s.is_empty() && !s.chars().any(char::is_control)).map(str::to_owned).ok_or(format!("{key} entries must be nonempty strings")))
            .collect(),
        Some(_) => Err(format!("{key} must be an array of strings")),
    }
}

impl Policy {
    /// [desktop.nested.devices] (every key required, see the SSOT comments) and [desktop.nested].registry_dir.
    pub fn from_ssot(nested: &Value) -> Result<Policy, String> {
        let d = nested.get("devices").filter(|v| v.is_table()).ok_or("desktop.nested.devices table missing")?;
        let flag = |k: &str| d.get(k).and_then(Value::as_bool).ok_or(format!("desktop.nested.devices.{k} must be a boolean"));
        let allow_kinds = strings(d.get("allow_kinds"), "desktop.nested.devices.allow_kinds")?;
        if let Some(bad) = allow_kinds.iter().find(|k| !KINDS.contains(&k.as_str())) {
            return Err(format!("desktop.nested.devices.allow_kinds: unknown kind {bad:?} (controller, keyboard, pointer, other)"));
        }
        let virtual_pad_name = d.get("virtual_pad_name").and_then(Value::as_str).filter(|s| !s.trim().is_empty() && !s.chars().any(char::is_control)).ok_or("desktop.nested.devices.virtual_pad_name must be a nonempty string")?.to_owned();
        let virtual_pad_wait_ms = d.get("virtual_pad_wait_ms").and_then(Value::as_integer).filter(|v| (0..=60000).contains(v)).ok_or("desktop.nested.devices.virtual_pad_wait_ms must be 0..60000")? as u64;
        let registry_dir = nested.get("registry_dir").and_then(Value::as_str).filter(|s| valid_relative(s)).ok_or("desktop.nested.registry_dir must be a relative path without '..'")?.to_owned();
        Ok(Policy {
            default: strings(d.get("default"), "desktop.nested.devices.default")?,
            allow_kinds,
            allow_virtual: flag("allow_virtual")?,
            exclusive: flag("exclusive")?,
            include_joystick: flag("include_joystick")?,
            virtual_pad_name,
            virtual_pad_wait_ms,
            registry_dir,
        })
    }
}

fn valid_relative(s: &str) -> bool {
    !s.is_empty() && !s.starts_with('/') && !s.chars().any(char::is_control) && Path::new(s).components().all(|c| matches!(c, std::path::Component::Normal(_)))
}

/// A seat label is an identifier: tmux socket suffix, registry file name and environment value.
pub fn valid_seat(s: &str) -> bool {
    !s.is_empty() && s.len() <= 32 && s.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-') && !s.starts_with('-')
}

/// Capability bitmap as sysfs prints it: space-separated hex words, most significant first, each word one `long`.
pub fn bitmap(text: &str) -> Vec<bool> {
    let mut bits = Vec::new();
    for (i, word) in text.split_whitespace().rev().enumerate() {
        let w = u64::from_str_radix(word, 16).unwrap_or(0);
        bits.resize((i + 1) * 64, false);
        for b in 0..64 {
            bits[i * 64 + b] = w >> b & 1 == 1;
        }
    }
    bits
}

fn has(bits: &[bool], n: usize) -> bool {
    bits.get(n).copied().unwrap_or(false)
}

/// The device kind from its capability bitmaps (sysfs `capabilities/{ev,key,rel,abs}`).
pub fn classify(ev: &str, key: &str, rel: &str, abs: &str) -> &'static str {
    let (ev, key, rel, abs) = (bitmap(ev), bitmap(key), bitmap(rel), bitmap(abs));
    let keys = has(&ev, EV_KEY);
    if keys && has(&ev, EV_ABS) && ((BTN_GAMEPAD..BTN_GAMEPAD + 15).any(|b| has(&key, b)) || (BTN_JOYSTICK..BTN_JOYSTICK + 16).any(|b| has(&key, b))) {
        return "controller";
    }
    let letters = [KEY_Q..=KEY_P, KEY_A..=KEY_L, KEY_Z..=KEY_M].into_iter().flatten().filter(|k| has(&key, *k)).count();
    if keys && letters >= 20 {
        return "keyboard";
    }
    let relative = has(&ev, EV_REL) && has(&rel, 0) && has(&rel, 1);
    let touch = has(&ev, EV_ABS) && has(&abs, 0) && has(&abs, 1) && has(&key, BTN_TOUCH);
    if keys && (relative && has(&key, BTN_LEFT) || touch) {
        return "pointer";
    }
    "other"
}

fn event_name(path: &Path) -> Option<String> {
    let n = path.file_name()?.to_str()?;
    (n.strip_prefix("event")?.bytes().all(|b| b.is_ascii_digit()) && n.len() > 5).then(|| n.to_owned())
}

fn read_trim(p: &Path) -> String {
    fs::read_to_string(p).map(|s| s.trim().to_owned()).unwrap_or_default()
}

/// Every evdev node the host lists, with its sysfs name (stable order).
fn event_nodes(sys: &Path) -> Vec<(String, String)> {
    let mut v: Vec<(String, String)> = fs::read_dir(sys).into_iter().flatten().flatten()
        .filter_map(|e| { let n = e.file_name().to_string_lossy().into_owned(); event_name(Path::new(&n))?; Some((n.clone(), read_trim(&sys.join(&n).join("device/name")))) })
        .collect();
    v.sort_by_key(|(n, _)| n.trim_start_matches("event").parse::<u64>().unwrap_or(u64::MAX));
    v
}

pub struct Host<'a> {
    pub dev: &'a Path,
    pub sys: &'a Path,
    /// Device nodes must be character devices with the input major; fixtures turn this off.
    pub check_node: bool,
}

pub fn host() -> Host<'static> {
    Host { dev: Path::new("/dev/input"), sys: Path::new("/sys/class/input"), check_node: true }
}

/// Resolve one spec: a /dev/input path (by-id/by-path links followed), `name:<exact evdev name>` or `virtual-pad`.
pub fn resolve_one(host: &Host, spec: &str, seat: &str, policy: &Policy) -> Result<Device, String> {
    let wait = |name: &str, ms: u64| -> Result<String, String> {
        let end = Instant::now() + Duration::from_millis(ms);
        loop {
            let found: Vec<String> = event_nodes(host.sys).into_iter().filter(|(_, n)| n == name).map(|(e, _)| e).collect();
            match found.len() {
                1 => return Ok(found[0].clone()),
                0 if Instant::now() < end => thread::sleep(Duration::from_millis(100)),
                0 => return Err(format!("device {spec:?}: no input device is named {name:?}")),
                _ => return Err(format!("device {spec:?}: {} input devices are named {name:?}; assign one by path", found.len())),
            }
        }
    };
    let node = if let Some(name) = spec.strip_prefix("name:") {
        wait(name, 0)?
    } else if spec == "virtual-pad" {
        wait(&policy.virtual_pad_name.replace("{seat}", seat), policy.virtual_pad_wait_ms)?
    } else {
        let p = Path::new(spec);
        if !p.is_absolute() || !p.starts_with(host.dev) {
            return Err(format!("device {spec:?}: expected a {} path, name:<device name> or virtual-pad", host.dev.display()));
        }
        let canon = fs::canonicalize(p).map_err(|e| format!("device {spec:?}: {e}"))?;
        if canon.parent() != Some(host.dev) {
            return Err(format!("device {spec:?}: {} is not an input event node", canon.display()));
        }
        event_name(&canon).ok_or(format!("device {spec:?}: {} is not an input event node", canon.display()))?
    };
    let path = host.dev.join(&node);
    let meta = fs::metadata(&path).map_err(|e| format!("device {spec:?}: {}: {e}", path.display()))?;
    if host.check_node {
        let major = (meta.rdev() >> 8 & 0xfff) | (meta.rdev() >> 32 & !0xfff);
        if !meta.file_type().is_char_device() || major != INPUT_MAJOR {
            return Err(format!("device {spec:?}: {} is not an input character device", path.display()));
        }
    }
    fs::File::open(&path).map_err(|e| format!("device {spec:?}: cannot open {}: {e} (input group or a logind ACL is required)", path.display()))?;
    let sysdev = host.sys.join(&node).join("device");
    let cap = |k: &str| read_trim(&sysdev.join("capabilities").join(k));
    let kind = classify(&cap("ev"), &cap("key"), &cap("rel"), &cap("abs")).to_owned();
    let virtual_node = fs::canonicalize(&sysdev).map(|p| p.components().any(|c| c.as_os_str() == "virtual")).unwrap_or(false);
    let mut siblings = Vec::new();
    if policy.include_joystick {
        for e in fs::read_dir(&sysdev).into_iter().flatten().flatten() {
            let n = e.file_name().to_string_lossy().into_owned();
            if n.strip_prefix("js").is_some_and(|d| !d.is_empty() && d.bytes().all(|b| b.is_ascii_digit())) && host.dev.join(&n).exists() {
                siblings.push(host.dev.join(n));
            }
        }
        siblings.sort();
    }
    let d = Device { path, spec: spec.to_owned(), name: read_trim(&sysdev.join("name")), kind, virtual_node, siblings };
    if !policy.allow_kinds.contains(&d.kind) {
        return Err(format!("device {spec:?} ({}, {}) is a {} device; desktop.nested.devices.allow_kinds permits {:?}", d.path.display(), d.name, d.kind, policy.allow_kinds));
    }
    if d.virtual_node && !policy.allow_virtual {
        return Err(format!("device {spec:?} ({}) is a virtual (uinput) node and desktop.nested.devices.allow_virtual is false", d.path.display()));
    }
    Ok(d)
}

/// Resolve every spec; duplicates (two specs naming one node) collapse to the first.
pub fn resolve(host: &Host, specs: &[String], seat: &str, policy: &Policy) -> Result<Vec<Device>, String> {
    let mut out: Vec<Device> = Vec::new();
    for s in specs {
        let d = resolve_one(host, s, seat, policy)?;
        if !out.iter().any(|o| o.path == d.path) {
            out.push(d);
        }
    }
    Ok(out)
}

/// bwrap arguments that expose exactly `devices` under the masked /dev/input. Mount points are created in `input`
/// (the session's private, otherwise empty directory) before the read-only bind, so the order is: mask, then nodes.
pub fn bind_args(input: &Path, devices: &[Device]) -> Result<Vec<String>, String> {
    let mut args = vec!["--ro-bind".to_owned(), input.to_string_lossy().into_owned(), "/dev/input".to_owned()];
    for d in devices {
        for node in d.nodes() {
            let name = node.file_name().ok_or("device node without a name")?;
            fs::write(input.join(name), b"").map_err(|e| format!("device mount point {}: {e}", input.join(name).display()))?;
            let p = node.to_string_lossy().into_owned();
            args.extend(["--dev-bind".to_owned(), p.clone(), format!("/dev/input/{}", name.to_string_lossy())]);
        }
    }
    Ok(args)
}

/// Process start time (clock ticks since boot): a PID plus its start time names one process, never a recycled PID.
pub fn start_time(pid: u32) -> Option<u64> {
    let stat = fs::read_to_string(format!("/proc/{pid}/stat")).ok()?;
    stat.rsplit_once(')')?.1.split_whitespace().nth(19)?.parse().ok()
}

fn alive(entry: &Json) -> bool {
    let (Some(pid), Some(st)) = (entry["launcher_pid"].as_u64(), entry["launcher_start"].as_u64()) else { return false };
    start_time(pid as u32) == Some(st)
}

/// Live registry entries (stale files from a killed launcher are ignored, and removed when `prune`).
pub fn live_sessions(dir: &Path, prune: bool) -> Vec<(PathBuf, Json)> {
    let mut v = Vec::new();
    for e in fs::read_dir(dir).into_iter().flatten().flatten() {
        let p = e.path();
        if p.extension().is_none_or(|x| x != "json") {
            continue;
        }
        match fs::read(&p).ok().and_then(|b| serde_json::from_slice::<Json>(&b).ok()) {
            Some(j) if alive(&j) => v.push((p, j)),
            _ if prune => { let _ = fs::remove_file(&p); }
            _ => {}
        }
    }
    v.sort_by(|a, b| a.0.cmp(&b.0));
    v
}

/// This launcher's registry entry: created exclusively, rewritten atomically, removed on drop.
#[derive(Debug)]
pub struct Claim {
    pub path: PathBuf,
    entry: Json,
}

struct Lock(PathBuf);
impl Drop for Lock {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.0);
    }
}
fn lock(dir: &Path) -> Result<Lock, String> {
    let path = dir.join(".claim.lock");
    let end = Instant::now() + Duration::from_secs(5);
    loop {
        match fs::OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(mut f) => {
                let me = std::process::id();
                let _ = writeln!(f, "{me} {}", start_time(me).unwrap_or(0));
                return Ok(Lock(path));
            }
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {
                let holder = read_trim(&path);
                let mut it = holder.split_whitespace().map(|v| v.parse::<u64>().ok());
                let stale = match (it.next().flatten(), it.next().flatten()) {
                    (Some(pid), Some(st)) => start_time(pid as u32) != Some(st),
                    _ => fs::metadata(&path).and_then(|m| m.modified()).ok().and_then(|t| t.elapsed().ok()).is_some_and(|d| d > Duration::from_secs(5)),
                };
                if stale {
                    let _ = fs::remove_file(&path);
                } else if Instant::now() > end {
                    return Err(format!("nested-session registry {} is locked by {holder}", dir.display()));
                } else {
                    thread::sleep(Duration::from_millis(50));
                }
            }
            Err(e) => return Err(format!("nested-session registry lock {}: {e}", path.display())),
        }
    }
}

impl Claim {
    /// Claim `seat` and `devices`: refused when a live session holds the seat or (with `exclusive`) any node. An
    /// unlabelled session (`seat` empty) never conflicts by seat; its entry is named after the launcher PID.
    pub fn take(dir: &Path, seat: &str, devices: &[Device], exclusive: bool, entry: Json) -> Result<Claim, String> {
        fs::create_dir_all(dir).map_err(|e| format!("nested-session registry {}: {e}", dir.display()))?;
        let _ = fs::set_permissions(dir, std::os::unix::fs::PermissionsExt::from_mode(0o700));
        let _lock = lock(dir)?;
        for (p, other) in live_sessions(dir, true) {
            if !seat.is_empty() && other["seat"].as_str() == Some(seat) {
                return Err(format!("seat {seat:?} is already in use by the session launched by PID {} ({})", other["launcher_pid"], p.display()));
            }
            if exclusive {
                let held: Vec<&str> = other["devices"].as_array().into_iter().flatten().flat_map(|d| std::iter::once(&d["path"]).chain(d["siblings"].as_array().into_iter().flatten())).filter_map(Json::as_str).collect();
                if let Some(d) = devices.iter().find(|d| d.nodes().any(|n| held.contains(&n.to_string_lossy().as_ref()))) {
                    return Err(format!("device {} ({}) is assigned to seat {:?} (launcher PID {}); desktop.nested.devices.exclusive refuses a second owner", d.path.display(), d.name, other["seat"].as_str().unwrap_or("?"), other["launcher_pid"]));
                }
            }
        }
        let me = std::process::id();
        let mut entry = entry;
        entry["seat"] = json!(seat);
        entry["launcher_pid"] = json!(me);
        entry["launcher_start"] = json!(start_time(me).ok_or("own process start time unavailable")?);
        entry["devices"] = Json::Array(devices.iter().map(Device::to_json).collect());
        let path = dir.join(if seat.is_empty() { format!("pid-{me}.json") } else { format!("{seat}.json") });
        fs::OpenOptions::new().write(true).create_new(true).open(&path)
            .and_then(|mut f| f.write_all(format!("{entry}\n").as_bytes()))
            .map_err(|e| format!("nested-session registry {}: {e}", path.display()))?;
        Ok(Claim { path, entry })
    }
    /// Merge `fields` into the entry and rewrite it atomically (readers never see a partial file).
    pub fn update(&mut self, fields: Json) -> Result<(), String> {
        if let (Some(e), Some(f)) = (self.entry.as_object_mut(), fields.as_object()) {
            for (k, v) in f {
                e.insert(k.clone(), v.clone());
            }
        }
        let tmp = self.path.with_extension("json.tmp");
        fs::write(&tmp, format!("{}\n", self.entry)).and_then(|_| fs::rename(&tmp, &self.path)).map_err(|e| format!("nested-session registry {}: {e}", self.path.display()))
    }
}
impl Drop for Claim {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.path);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn policy() -> Policy {
        Policy { default: vec![], allow_kinds: vec!["controller".into(), "keyboard".into(), "pointer".into()], allow_virtual: true, exclusive: true, include_joystick: true, virtual_pad_name: "MiOS Nested Pad {seat}".into(), virtual_pad_wait_ms: 0, registry_dir: "mios/nested-sessions".into() }
    }

    /// A fixture tree: <root>/dev/input/eventN (plain files) and <root>/sys/class/input/eventN/device/{name,capabilities}.
    fn fixture(root: &Path, n: u32, name: &str, caps: [&str; 4], virtual_node: bool, js: Option<u32>) {
        let dev = root.join("dev/input");
        fs::create_dir_all(&dev).unwrap();
        fs::write(dev.join(format!("event{n}")), b"").unwrap();
        let real = root.join(if virtual_node { "sys/devices/virtual/input" } else { "sys/devices/pci0000:00/usb1/input" }).join(format!("input{n}"));
        fs::create_dir_all(real.join("capabilities")).unwrap();
        fs::write(real.join("name"), format!("{name}\n")).unwrap();
        for (k, v) in ["ev", "key", "rel", "abs"].iter().zip(caps) {
            fs::write(real.join("capabilities").join(k), format!("{v}\n")).unwrap();
        }
        if let Some(j) = js {
            fs::create_dir_all(real.join(format!("js{j}"))).unwrap();
            fs::write(dev.join(format!("js{j}")), b"").unwrap();
        }
        let sys = root.join("sys/class/input").join(format!("event{n}"));
        fs::create_dir_all(&sys).unwrap();
        std::os::unix::fs::symlink(&real, sys.join("device")).unwrap();
    }

    // Real capability strings (64-bit words, most significant first) of an Xbox pad, a keyboard and a mouse.
    const PAD: [&str; 4] = ["20000b", "7cdb000000000000 0 0 0 0", "0", "3003f"];
    const KBD: [&str; 4] = ["120013", "1000000000007 ff9f207ac14057ff febeffdfffefffff fffffffffffffffe", "0", "0"];
    const MOUSE: [&str; 4] = ["17", "1f0000 0 0 0 0", "903", "0"];

    #[test]
    fn capabilities_classify_real_devices() {
        assert_eq!(classify(PAD[0], PAD[1], PAD[2], PAD[3]), "controller");
        assert_eq!(classify(KBD[0], KBD[1], KBD[2], KBD[3]), "keyboard");
        assert_eq!(classify(MOUSE[0], MOUSE[1], MOUSE[2], MOUSE[3]), "pointer");
        assert_eq!(classify("3", "10000000000000 0", "0", "0"), "other", "a power button is no keyboard");
        assert_eq!(classify("0", "", "", ""), "other");
        // a bitmap word is a long: bit 0x130 (BTN_SOUTH) is word 4 bit 48
        assert!(has(&bitmap("1000000000000 0 0 0 0"), 0x130));
        assert!(!has(&bitmap("1000000000000 0 0 0"), 0x130));
    }

    #[test]
    fn specs_resolve_classify_and_follow_policy() {
        let t = tempfile::tempdir().unwrap();
        fixture(t.path(), 3, "Xbox Wireless Controller", PAD, false, Some(0));
        fixture(t.path(), 7, "AT Keyboard", KBD, false, None);
        fixture(t.path(), 9, "MiOS Nested Pad p2", PAD, true, None);
        fixture(t.path(), 11, "Twin", MOUSE, false, None);
        fixture(t.path(), 12, "Twin", MOUSE, false, None);
        let (dev, sys) = (t.path().join("dev/input"), t.path().join("sys/class/input"));
        let host = Host { dev: &dev, sys: &sys, check_node: false };
        std::fs::create_dir_all(dev.join("by-id")).unwrap();
        std::os::unix::fs::symlink("../event3", dev.join("by-id/usb-pad-event-joystick")).unwrap();
        let p = policy();
        let pad = resolve_one(&host, &dev.join("by-id/usb-pad-event-joystick").to_string_lossy(), "p2", &p).unwrap();
        assert_eq!((pad.path.clone(), pad.kind.as_str(), pad.virtual_node), (dev.join("event3"), "controller", false));
        assert_eq!(pad.siblings, vec![dev.join("js0")], "the legacy joystick node rides along");
        assert_eq!(resolve_one(&host, "name:AT Keyboard", "p2", &p).unwrap().kind, "keyboard");
        let vp = resolve_one(&host, "virtual-pad", "p2", &p).unwrap();
        assert_eq!((vp.path.clone(), vp.virtual_node), (dev.join("event9"), true));
        // negative controls
        assert!(resolve_one(&host, "virtual-pad", "p3", &p).unwrap_err().contains("no input device is named \"MiOS Nested Pad p3\""));
        assert!(resolve_one(&host, "name:Twin", "p2", &p).unwrap_err().contains("2 input devices"));
        assert!(resolve_one(&host, "/etc/passwd", "p2", &p).unwrap_err().contains("expected a"));
        assert!(resolve_one(&host, "event3", "p2", &p).is_err(), "relative paths are refused");
        assert!(resolve_one(&host, &dev.join("js0").to_string_lossy(), "p2", &p).unwrap_err().contains("not an input event node"));
        let mut kb_off = p.clone();
        kb_off.allow_kinds.retain(|k| k != "keyboard");
        assert!(resolve_one(&host, "name:AT Keyboard", "p2", &kb_off).unwrap_err().contains("is a keyboard device"));
        let mut no_virtual = p.clone();
        no_virtual.allow_virtual = false;
        assert!(resolve_one(&host, "virtual-pad", "p2", &no_virtual).unwrap_err().contains("allow_virtual is false"));
        let mut no_js = p.clone();
        no_js.include_joystick = false;
        assert!(resolve_one(&host, "name:Xbox Wireless Controller", "p2", &no_js).unwrap().siblings.is_empty());
        let both = resolve(&host, &["name:Xbox Wireless Controller".into(), dev.join("event3").to_string_lossy().into_owned()], "p2", &p).unwrap();
        assert_eq!(both.len(), 1, "two specs for one node collapse");
    }

    #[test]
    fn real_nodes_must_be_input_character_devices() {
        let t = tempfile::tempdir().unwrap();
        fixture(t.path(), 1, "Pad", PAD, false, None);
        let (dev, sys) = (t.path().join("dev/input"), t.path().join("sys/class/input"));
        let strict = Host { dev: &dev, sys: &sys, check_node: true };
        assert!(resolve_one(&strict, &dev.join("event1").to_string_lossy(), "s", &policy()).unwrap_err().contains("not an input character device"));
    }

    #[test]
    fn bind_args_mask_first_then_expose_each_node() {
        let t = tempfile::tempdir().unwrap();
        let d = Device { path: "/dev/input/event5".into(), spec: "x".into(), name: "Pad".into(), kind: "controller".into(), virtual_node: false, siblings: vec!["/dev/input/js1".into()] };
        let a = bind_args(t.path(), &[d]).unwrap();
        assert_eq!(a[..3], ["--ro-bind".to_owned(), t.path().to_string_lossy().into_owned(), "/dev/input".to_owned()]);
        assert_eq!(a[3..], ["--dev-bind", "/dev/input/event5", "/dev/input/event5", "--dev-bind", "/dev/input/js1", "/dev/input/js1"].map(String::from));
        assert!(t.path().join("event5").is_file() && t.path().join("js1").is_file(), "mount points exist before the read-only mask");
        assert_eq!(bind_args(t.path(), &[]).unwrap().len(), 3, "no devices: the empty mask only");
    }

    #[test]
    fn registry_refuses_a_second_owner_and_a_used_seat() {
        let t = tempfile::tempdir().unwrap();
        let dir = t.path().join("reg");
        let pad = Device { path: "/dev/input/event5".into(), spec: "x".into(), name: "Pad".into(), kind: "controller".into(), virtual_node: false, siblings: vec!["/dev/input/js1".into()] };
        let kb = Device { path: "/dev/input/event6".into(), siblings: vec![], ..pad.clone() };
        let js_only = Device { path: "/dev/input/event8".into(), siblings: vec!["/dev/input/js1".into()], ..pad.clone() };
        let mut a = Claim::take(&dir, "p1", std::slice::from_ref(&pad), true, json!({"target": "output"})).unwrap();
        assert!(Claim::take(&dir, "p1", &[], true, json!({})).unwrap_err().contains("already in use"));
        assert!(Claim::take(&dir, "p2", std::slice::from_ref(&pad), true, json!({})).unwrap_err().contains("exclusive refuses"));
        assert!(Claim::take(&dir, "p2", &[js_only], true, json!({})).unwrap_err().contains("exclusive refuses"), "a shared sibling is a shared device");
        let b = Claim::take(&dir, "p2", std::slice::from_ref(&kb), true, json!({})).unwrap();
        assert_eq!(live_sessions(&dir, false).len(), 2);
        let c = Claim::take(&dir, "p3", std::slice::from_ref(&pad), false, json!({})).unwrap();
        drop(c);
        a.update(json!({"state": "running"})).unwrap();
        let on_disk: Json = serde_json::from_slice(&fs::read(&a.path).unwrap()).unwrap();
        assert_eq!((on_disk["state"].as_str(), on_disk["target"].as_str(), on_disk["devices"][0]["path"].as_str()), (Some("running"), Some("output"), Some("/dev/input/event5")));
        drop(a);
        drop(b);
        assert!(live_sessions(&dir, false).is_empty(), "a claim is removed with its launcher");
        // a stale file (dead launcher) never blocks a seat or a device
        fs::write(dir.join("p1.json"), json!({"seat": "p1", "launcher_pid": 1, "launcher_start": 1, "devices": [pad.to_json()]}).to_string()).unwrap();
        assert!(Claim::take(&dir, "p1", std::slice::from_ref(&pad), true, json!({})).is_ok());
    }

    #[test]
    fn unlabelled_sessions_coexist_but_still_own_their_devices() {
        let t = tempfile::tempdir().unwrap();
        let dir = t.path().join("reg");
        let pad = Device { path: "/dev/input/event5".into(), spec: "x".into(), name: "Pad".into(), kind: "controller".into(), virtual_node: false, siblings: vec![] };
        let a = Claim::take(&dir, "", std::slice::from_ref(&pad), true, json!({})).unwrap();
        assert!(a.path.file_name().unwrap().to_string_lossy().starts_with("pid-"));
        // a second unlabelled launch from another process is modelled by a live foreign entry
        let me = std::process::id();
        fs::write(dir.join("pid-other.json"), json!({"seat": "", "launcher_pid": me, "launcher_start": start_time(me).unwrap(), "devices": []}).to_string()).unwrap();
        assert!(Claim::take(&dir, "p9", &[], true, json!({})).is_ok(), "an unlabelled session blocks no seat");
        assert!(Claim::take(&dir, "p8", &[pad], true, json!({})).unwrap_err().contains("exclusive refuses"));
        drop(a);
    }

    #[test]
    fn seat_labels_are_identifiers() {
        for ok in ["p2", "player-2", "remote1"] {
            assert!(valid_seat(ok), "{ok}");
        }
        for bad in ["", "-x", "P2", "a b", "a/b", "../x", &"x".repeat(33)] {
            assert!(!valid_seat(bad), "{bad}");
        }
    }
}
