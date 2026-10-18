// AI-hint: miosd placement: float-first window placement; new windows open floating and centered at one SSOT-derived size, moved windows tile, Super+V toggles.
// AI-related: usr/share/mios/mios.toml [desktop.hyprland.placement], usr/lib/systemd/user/mios-placement.service
//! Float-first window placement (`miosd placement`): new windows open floating and centered at
//! one SSOT-derived size, a placed window that the operator moves drops into the tiling layout,
//! and Super+V toggles the active window through the same sizing function.
//!
//! Flow: the session Lua (mios-gen render-hyprland) carries one static window rule built from
//! [desktop.hyprland.placement] that floats, centers and sizes every new window and tags it
//! `<open_tag>`. `serve` watches the Hyprland event socket; on `openwindow` it accepts a window
//! only when that rule actually placed it (rule tag present and the window has the rule's size;
//! Hyprland does not size parented dialogs or fixed-size windows, and later rules such as the
//! PiP rule override the size), applies the exact geometry from [`geometry`] (reserved areas and
//! physical size included), and gives it the static tag `<managed_tag>`. Managed windows are
//! polled; a position change with an unchanged size that settles with no mouse button held
//! (evdev EVIOCGKEY when readable) unsets floating so the layout tiles the window at the drop
//! point. Dialogs, modals, PiP and anything the operator floated are never tagged, so they are
//! never re-tiled.
//!
//! Never dispatch a resize from a Lua `window.open_early` handler: the window has no layout
//! target yet and Hyprland 0.56.2 segfaults in Layout::CSpace::resizeTarget.
// AI-related: usr/share/mios/mios.toml [desktop.hyprland.placement] [desktop.hyprland].window_rules,
// AI-related: tools/native/mios-gen/src/render.rs, usr/lib/systemd/user/mios-placement.service

use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::net::UnixStream;
use std::path::{Path, PathBuf};
use std::sync::mpsc;
use std::time::{Duration, Instant};

use regex::Regex;
use serde_json::Value as Json;
use toml::Value;

#[derive(clap::Subcommand, Debug)]
pub enum PlacementCommand {
    /// Watch the Hyprland event socket: size and center rule-floated windows, tile them once moved
    Serve {
        /// Only manage windows whose class matches this regex (testing on a live desktop)
        #[arg(long)]
        class_filter: Option<String>,
    },
    /// Float, size and center the window (active window by default), or tile it when floating
    Toggle {
        /// Hyprland window address (0x...)
        #[arg(long)]
        window: Option<String>,
    },
    /// Print the computed floating geometry for every monitor as JSON
    Geometry,
    /// Print the window-rule size expression the session config must carry
    RuleSize,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Scope {
    All,
    EmptyWorkspace,
}

#[derive(Clone, Debug)]
pub struct Config {
    pub enabled: bool,
    pub scope: Scope,
    pub width_ratio: f64,
    pub aspect: f64,
    pub min_width: f64,
    pub min_height: f64,
    pub max_width: f64,
    pub max_width_ratio: f64,
    pub max_height_ratio: f64,
    pub max_width_mm: f64,
    pub tile_on_move: bool,
    pub move_threshold: f64,
    pub settle: Duration,
    pub poll: Duration,
    pub size_tolerance: f64,
    pub open_tag: String,
    pub managed_tag: String,
    pub excludes: Vec<Exclusion>,
}

/// One SSOT window rule (other than placement) reduced to the match keys a client JSON carries.
#[derive(Clone, Debug)]
pub struct Exclusion {
    name: String,
    regexes: Vec<(&'static str, Regex, bool)>,
    bools: Vec<(&'static str, bool)>,
}

fn get<'a>(v: &'a Value, path: &str) -> Option<&'a Value> {
    path.split('.').try_fold(v, |cur, key| cur.get(key))
}

fn num(t: &Value, key: &str) -> Result<f64, String> {
    match t.get(key) {
        Some(Value::Float(f)) => Ok(*f),
        Some(Value::Integer(i)) => Ok(*i as f64),
        _ => Err(format!("desktop.hyprland.placement.{key} must be a number")),
    }
}

fn boolean(t: &Value, key: &str) -> Result<bool, String> {
    t.get(key)
        .and_then(Value::as_bool)
        .ok_or_else(|| format!("desktop.hyprland.placement.{key} must be a boolean"))
}

fn string(t: &Value, key: &str) -> Result<String, String> {
    t.get(key)
        .and_then(Value::as_str)
        .filter(|s| {
            !s.is_empty()
                && s.bytes()
                    .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
        })
        .map(str::to_owned)
        .ok_or_else(|| format!("desktop.hyprland.placement.{key} must be a [A-Za-z0-9_-] string"))
}

impl Config {
    pub fn from_ssot(doc: &Value) -> Result<Self, String> {
        let t = get(doc, "desktop.hyprland.placement")
            .filter(|v| v.is_table())
            .ok_or("missing [desktop.hyprland.placement]")?;
        let scope = match t.get("scope").and_then(Value::as_str) {
            Some("all") => Scope::All,
            Some("empty_workspace") => Scope::EmptyWorkspace,
            _ => return Err("desktop.hyprland.placement.scope must be all|empty_workspace".into()),
        };
        let cfg = Config {
            enabled: boolean(t, "enabled")?,
            scope,
            width_ratio: num(t, "width_ratio")?,
            aspect: num(t, "aspect")?,
            min_width: num(t, "min_width_px")?,
            min_height: num(t, "min_height_px")?,
            max_width: num(t, "max_width_px")?,
            max_width_ratio: num(t, "max_width_ratio")?,
            max_height_ratio: num(t, "max_height_ratio")?,
            max_width_mm: num(t, "max_width_mm")?,
            tile_on_move: boolean(t, "tile_on_move")?,
            move_threshold: num(t, "move_threshold_px")?,
            settle: Duration::from_millis(num(t, "settle_ms")?.max(0.0) as u64),
            poll: Duration::from_millis(num(t, "poll_ms")?.max(20.0) as u64),
            size_tolerance: num(t, "size_tolerance_px")?,
            open_tag: string(t, "open_tag")?,
            managed_tag: string(t, "managed_tag")?,
            excludes: exclusions(doc)?,
        };
        let ratio = |v: f64| v > 0.0 && v <= 1.0;
        if !ratio(cfg.width_ratio) || !ratio(cfg.max_width_ratio) || !ratio(cfg.max_height_ratio) {
            return Err("placement ratios must be in (0, 1]".into());
        }
        if cfg.aspect < 1.0
            || cfg.min_width < 1.0
            || cfg.min_height < 1.0
            || cfg.max_width < cfg.min_width
        {
            return Err(
                "placement aspect must be >= 1 and min/max sizes positive with max >= min".into(),
            );
        }
        if cfg.max_width_mm < 0.0 || cfg.move_threshold < 1.0 || cfg.size_tolerance < 0.0 {
            return Err(
                "placement max_width_mm/size_tolerance_px must be >= 0, move_threshold_px >= 1"
                    .into(),
            );
        }
        Ok(cfg)
    }

    pub fn load() -> Result<Self, String> {
        let root = std::env::var_os("MIOS_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("/"));
        let doc = mios_resolver::resolve_merged(Some(&root), false).map_err(|e| e.to_string())?;
        Self::from_ssot(&doc)
    }
}

fn exclusions(doc: &Value) -> Result<Vec<Exclusion>, String> {
    let mut out = Vec::new();
    let Some(rules) = get(doc, "desktop.hyprland.window_rules").and_then(Value::as_array) else {
        return Ok(out);
    };
    'rule: for rule in rules {
        let name = rule
            .get("name")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_owned();
        let Some(m) = rule.get("match").and_then(Value::as_table) else {
            continue;
        };
        let mut ex = Exclusion {
            name,
            regexes: Vec::new(),
            bools: Vec::new(),
        };
        for (key, val) in m {
            match (key.as_str(), val) {
                (k @ ("class" | "title" | "initial_class" | "initial_title"), Value::String(s)) => {
                    let field: &'static str = match k {
                        "class" => "class",
                        "title" => "title",
                        "initial_class" => "initialClass",
                        _ => "initialTitle",
                    };
                    let (neg, pat) = match s.strip_prefix("negative:") {
                        Some(p) => (true, p),
                        None => (false, s.as_str()),
                    };
                    let re =
                        Regex::new(pat).map_err(|e| format!("window rule {key} regex: {e}"))?;
                    ex.regexes.push((field, re, neg));
                }
                ("xwayland", Value::Boolean(b)) => ex.bools.push(("xwayland", *b)),
                ("pin", Value::Boolean(b)) => ex.bools.push(("pinned", *b)),
                // modal/content/workspace/... are not in the client JSON: such windows are
                // dialogs Hyprland floats without applying the placement size, so the size
                // check already rejects them.
                _ => continue 'rule,
            }
        }
        if !ex.regexes.is_empty() || !ex.bools.is_empty() {
            out.push(ex);
        }
    }
    Ok(out)
}

impl Exclusion {
    fn matches(&self, c: &Json) -> bool {
        self.regexes.iter().all(|(field, re, neg)| {
            let v = c.get(*field).and_then(Json::as_str).unwrap_or("");
            re.is_match(v) != *neg
        }) && self
            .bools
            .iter()
            .all(|(field, want)| c.get(*field).and_then(Json::as_bool) == Some(*want))
    }
}

/// Logical monitor description used by the sizing function.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct MonitorBox {
    /// Logical position and size (pixel mode / scale, rotated by transform).
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
    /// Reserved logical px: left, top, right, bottom.
    pub reserved: [f64; 4],
    /// Physical width in mm along the logical x axis (0 when unknown).
    pub width_mm: f64,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Geometry {
    pub x: i64,
    pub y: i64,
    pub w: i64,
    pub h: i64,
}

fn clamp(v: f64, lo: f64, hi: f64) -> f64 {
    v.max(lo).min(hi)
}

/// The one sizing function. The long axis of the usable area gets `width_ratio` of it, the short
/// axis follows `aspect` (long/short); both are clamped to the SSOT pixel bounds and to the usable
/// area, the width additionally to `max_width_mm` of physical screen. Logical pixels already
/// carry the monitor scale (DPI); the millimetre cap keeps windows sane on large panels.
pub fn size_for(cfg: &Config, usable_w: f64, usable_h: f64, px_per_mm: Option<f64>) -> (f64, f64) {
    let mut cap_w = (usable_w * cfg.max_width_ratio).min(cfg.max_width);
    if let Some(ppm) = px_per_mm.filter(|p| *p > 0.0 && cfg.max_width_mm > 0.0) {
        cap_w = cap_w.min(cfg.max_width_mm * ppm);
    }
    let cap_h = usable_h * cfg.max_height_ratio;
    let (w, h) = if usable_w >= usable_h {
        let w = clamp(usable_w * cfg.width_ratio, cfg.min_width, cap_w);
        (w, clamp(w / cfg.aspect, cfg.min_height, cap_h))
    } else {
        let h = clamp(usable_h * cfg.width_ratio, cfg.min_height, cap_h);
        (clamp(h / cfg.aspect, cfg.min_width, cap_w), h)
    };
    (
        w.min(usable_w).max(1.0).round(),
        h.min(usable_h).max(1.0).round(),
    )
}

/// Centered geometry inside the monitor's usable (non-reserved) area.
pub fn geometry(cfg: &Config, m: &MonitorBox) -> Geometry {
    let [rl, rt, rr, rb] = m.reserved;
    let uw = (m.w - rl - rr).max(1.0);
    let uh = (m.h - rt - rb).max(1.0);
    let ppm = (m.width_mm > 0.0).then(|| m.w / m.width_mm);
    let (w, h) = size_for(cfg, uw, uh, ppm);
    Geometry {
        x: (m.x + rl + (uw - w) / 2.0).round() as i64,
        y: (m.y + rt + (uh - h) / 2.0).round() as i64,
        w: w as i64,
        h: h as i64,
    }
}

/// The size the static window rule produces: the same function over the whole monitor
/// (rule expressions see neither reserved areas nor physical size).
pub fn rule_size(cfg: &Config, monitor_w: f64, monitor_h: f64) -> (f64, f64) {
    size_for(cfg, monitor_w, monitor_h, None)
}

fn lit(v: f64) -> String {
    let s = format!("{v:.6}");
    let s = s.trim_end_matches('0').trim_end_matches('.');
    if s.is_empty() {
        "0".into()
    } else {
        s.to_owned()
    }
}

/// muParser expression pair ("X Y", no other spaces) equal to [`rule_size`]; the session config
/// projection in tools/native/mios-gen/src/render.rs emits the identical string.
pub fn rule_size_expression(cfg: &Config) -> String {
    let (r, a) = (lit(cfg.width_ratio), lit(cfg.aspect));
    let (minw, minh, maxw) = (lit(cfg.min_width), lit(cfg.min_height), lit(cfg.max_width));
    let (mwr, mhr) = (lit(cfg.max_width_ratio), lit(cfg.max_height_ratio));
    let cap_w = format!("min(monitor_w*{mwr},{maxw})");
    let cap_h = format!("monitor_h*{mhr}");
    let land_w = format!("min(max(monitor_w*{r},{minw}),{cap_w})");
    let land_h = format!("min(max(({land_w})/{a},{minh}),{cap_h})");
    let port_h = format!("min(max(monitor_h*{r},{minh}),{cap_h})");
    let port_w = format!("min(max(({port_h})/{a},{minw}),{cap_w})");
    let w = format!("(monitor_w>=monitor_h?min({land_w},monitor_w):min({port_w},monitor_w))");
    let h = format!("(monitor_w>=monitor_h?min({land_h},monitor_h):min({port_h},monitor_h))");
    format!("{w} {h}")
}

// ---------------------------------------------------------------- Hyprland IPC

fn runtime_dir() -> PathBuf {
    std::env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(format!("/run/user/{}", unsafe { libc::getuid() })))
}

/// The live Hyprland instance: the env signature when its socket answers, else the newest
/// instance directory whose request socket accepts a connection (the compositor may have
/// restarted since the user manager imported its environment).
fn instance_dir() -> Result<PathBuf, String> {
    let base = runtime_dir().join("hypr");
    let alive = |d: &Path| UnixStream::connect(d.join(".socket.sock")).is_ok();
    if let Some(sig) = std::env::var_os("HYPRLAND_INSTANCE_SIGNATURE") {
        let d = base.join(sig);
        if alive(&d) {
            return Ok(d);
        }
    }
    let mut dirs: Vec<(std::time::SystemTime, PathBuf)> = std::fs::read_dir(&base)
        .map_err(|e| format!("{}: {e}", base.display()))?
        .flatten()
        .filter_map(|e| Some((e.metadata().ok()?.modified().ok()?, e.path())))
        .collect();
    dirs.sort_by_key(|d| std::cmp::Reverse(d.0));
    dirs.into_iter()
        .map(|(_, d)| d)
        .find(|d| alive(d))
        .ok_or_else(|| "no live Hyprland instance socket".into())
}

pub struct Hypr {
    dir: PathBuf,
}

impl Hypr {
    pub fn connect() -> Result<Self, String> {
        Ok(Hypr {
            dir: instance_dir()?,
        })
    }

    fn request(&self, body: &str) -> Result<String, String> {
        let mut s =
            UnixStream::connect(self.dir.join(".socket.sock")).map_err(|e| e.to_string())?;
        s.set_read_timeout(Some(Duration::from_secs(3)))
            .map_err(|e| e.to_string())?;
        s.write_all(body.as_bytes()).map_err(|e| e.to_string())?;
        let mut out = String::new();
        s.read_to_string(&mut out).map_err(|e| e.to_string())?;
        Ok(out)
    }

    pub fn json(&self, what: &str) -> Result<Json, String> {
        let raw = self.request(&format!("j/{what}"))?;
        serde_json::from_str(&raw).map_err(|e| format!("{what}: {e}"))
    }

    /// Run Lua in the compositor. Callers only pass dispatches on mapped windows.
    pub fn eval(&self, lua: &str) -> Result<(), String> {
        let out = self.request(&format!("eval {lua}"))?;
        if out.trim() == "ok" {
            Ok(())
        } else {
            Err(out.trim().to_owned())
        }
    }

    fn events(&self) -> Result<UnixStream, String> {
        UnixStream::connect(self.dir.join(".socket2.sock")).map_err(|e| e.to_string())
    }
}

fn monitor_boxes(monitors: &Json) -> HashMap<i64, MonitorBox> {
    let mut out = HashMap::new();
    for m in monitors.as_array().into_iter().flatten() {
        let f = |k: &str| m.get(k).and_then(Json::as_f64).unwrap_or(0.0);
        let scale = Some(f("scale")).filter(|s| *s > 0.0).unwrap_or(1.0);
        let rotated = m.get("transform").and_then(Json::as_i64).unwrap_or(0) % 2 == 1;
        let (pw, ph) = if rotated {
            (f("height"), f("width"))
        } else {
            (f("width"), f("height"))
        };
        let mm = if rotated {
            f("physicalHeight")
        } else {
            f("physicalWidth")
        };
        let mut reserved = [0.0; 4];
        if let Some(r) = m.get("reserved").and_then(Json::as_array) {
            for (i, v) in r.iter().take(4).enumerate() {
                reserved[i] = v.as_f64().unwrap_or(0.0);
            }
        }
        if let Some(id) = m.get("id").and_then(Json::as_i64) {
            out.insert(
                id,
                MonitorBox {
                    x: f("x"),
                    y: f("y"),
                    w: pw / scale,
                    h: ph / scale,
                    reserved,
                    width_mm: mm,
                },
            );
        }
    }
    out
}

fn addr(a: &str) -> String {
    if a.starts_with("0x") {
        a.to_owned()
    } else {
        format!("0x{a}")
    }
}

fn lua_str(s: &str) -> String {
    format!("'{}'", s.replace('\\', "\\\\").replace('\'', "\\'"))
}

/// Float (Hyprland 0.56 takes 'enable'/'disable'; any other action string toggles), size and
/// move; `tag` marks the window as managed so a later drag re-tiles it. Super+V floats pass no
/// tag: a window the operator floated stays floating wherever it is dragged.
fn place_lua(address: &str, g: &Geometry, tag: Option<&str>) -> String {
    let w = lua_str(&format!("address:{address}"));
    let tag = tag
        .map(|t| {
            format!(
                "; hl.dispatch(hl.dsp.window.tag({{tag={}, window=w}}))",
                lua_str(&format!("+{t}"))
            )
        })
        .unwrap_or_default();
    format!(
        "local w={w}; hl.dispatch(hl.dsp.window.float({{action='enable', window=w}})); \
         hl.dispatch(hl.dsp.window.resize({{x={}, y={}, window=w}})); \
         hl.dispatch(hl.dsp.window.move({{x={}, y={}, window=w}})){tag}",
        g.w, g.h, g.x, g.y
    )
}

fn tile_lua(address: &str, tag: &str) -> String {
    let w = lua_str(&format!("address:{address}"));
    format!(
        "local w={w}; hl.dispatch(hl.dsp.window.tag({{tag={}, window=w}})); \
         hl.dispatch(hl.dsp.window.float({{action='disable', window=w}}))",
        lua_str(&format!("-{tag}"))
    )
}

fn tags(c: &Json) -> Vec<String> {
    c.get("tags")
        .and_then(Json::as_array)
        .map(|a| {
            a.iter()
                .filter_map(Json::as_str)
                .map(|s| s.trim_end_matches('*').to_owned())
                .collect()
        })
        .unwrap_or_default()
}

fn pair(c: &Json, key: &str) -> Option<(f64, f64)> {
    let a = c.get(key)?.as_array()?;
    Some((a.first()?.as_f64()?, a.get(1)?.as_f64()?))
}

// ---------------------------------------------------------------- evdev button state

/// Reads mouse button state with EVIOCGKEY on every input device exposing BTN_LEFT. Read-only
/// ioctls: nothing is grabbed and no events are consumed. Unavailable without the input group.
struct Buttons {
    fds: Vec<std::fs::File>,
    scanned: Instant,
    /// Directory scanned for event nodes (/dev/input; a temp dir in tests).
    dir: PathBuf,
    /// inotify on `dir` (IN_CREATE | IN_ATTRIB): a hot-plugged pointer appears as a new
    /// eventN node, and udev grants the session ACL just after (IN_ATTRIB). None when inotify
    /// is unavailable; the periodic RESCAN_EVERY fallback still finds new devices then.
    watch: Option<std::os::fd::OwnedFd>,
}

/// Fallback rescan interval when inotify is unavailable or missed an event.
const RESCAN_EVERY: Duration = Duration::from_secs(30);
/// Retry interval while no pointer is readable at all.
const RESCAN_EMPTY_EVERY: Duration = Duration::from_secs(10);

/// Whether the cached pointer fds should be rebuilt.
fn should_rescan(fds_empty: bool, since_scan: Duration, hotplug: bool) -> bool {
    hotplug
        || since_scan > RESCAN_EVERY
        || (fds_empty && since_scan > RESCAN_EMPTY_EVERY)
}

fn inotify_watch(dir: &Path) -> Option<std::os::fd::OwnedFd> {
    use std::os::fd::FromRawFd;
    use std::os::unix::ffi::OsStrExt;
    let path = std::ffi::CString::new(dir.as_os_str().as_bytes()).ok()?;
    // SAFETY: inotify_init1 returns a fresh fd we own; inotify_add_watch reads a valid C path.
    unsafe {
        let fd = libc::inotify_init1(libc::IN_NONBLOCK | libc::IN_CLOEXEC);
        if fd < 0 {
            return None;
        }
        let owned = std::os::fd::OwnedFd::from_raw_fd(fd);
        if libc::inotify_add_watch(fd, path.as_ptr(), libc::IN_CREATE | libc::IN_ATTRIB) < 0 {
            return None;
        }
        Some(owned)
    }
}

const BTN_LEFT: usize = 0x110;
const KEY_BYTES: usize = 96;

fn ioc_read(nr: u64, len: usize) -> u64 {
    (2u64 << 30) | ((len as u64) << 16) | (0x45u64 << 8) | nr
}

fn bit(buf: &[u8; KEY_BYTES], n: usize) -> bool {
    buf[n / 8] & (1 << (n % 8)) != 0
}

impl Buttons {
    fn new() -> Self {
        Self::with_dir(Path::new("/dev/input"))
    }

    fn with_dir(dir: &Path) -> Self {
        let mut b = Buttons {
            fds: Vec::new(),
            scanned: Instant::now(),
            dir: dir.to_path_buf(),
            watch: inotify_watch(dir),
        };
        b.scan();
        b
    }

    /// Drain the inotify queue; true when an event node was created or changed permissions.
    fn hotplugged(&mut self) -> bool {
        use std::os::fd::AsRawFd;
        let Some(fd) = &self.watch else {
            return false;
        };
        let mut seen = false;
        let mut buf = [0u8; 4096];
        loop {
            // SAFETY: non-blocking read into a stack buffer of the stated length.
            let n = unsafe { libc::read(fd.as_raw_fd(), buf.as_mut_ptr().cast(), buf.len()) };
            if n <= 0 {
                break;
            }
            seen = true;
        }
        seen
    }

    fn scan(&mut self) {
        use std::os::fd::AsRawFd;
        use std::os::unix::fs::OpenOptionsExt;
        self.fds.clear();
        self.scanned = Instant::now();
        let Ok(dir) = std::fs::read_dir(&self.dir) else {
            return;
        };
        for e in dir.flatten() {
            if !e.file_name().to_string_lossy().starts_with("event") {
                continue;
            }
            let Ok(f) = std::fs::OpenOptions::new()
                .read(true)
                .custom_flags(libc::O_NONBLOCK)
                .open(e.path())
            else {
                continue;
            };
            let mut caps = [0u8; KEY_BYTES];
            // EVIOCGBIT(EV_KEY): nr 0x20 + EV_KEY(1)
            let rc = unsafe {
                libc::ioctl(
                    f.as_raw_fd(),
                    ioc_read(0x21, KEY_BYTES) as _,
                    caps.as_mut_ptr(),
                )
            };
            if rc >= 0 && bit(&caps, BTN_LEFT) {
                self.fds.push(f);
            }
        }
    }

    /// Some(true) while any pointer button is held, None when no device is readable.
    fn down(&mut self) -> Option<bool> {
        use std::os::fd::AsRawFd;
        let hotplug = self.hotplugged();
        if should_rescan(self.fds.is_empty(), self.scanned.elapsed(), hotplug) {
            self.scan();
        }
        if self.fds.is_empty() {
            return None;
        }
        let mut any = false;
        let mut failed = false;
        for f in &self.fds {
            let mut keys = [0u8; KEY_BYTES];
            let rc = unsafe {
                libc::ioctl(
                    f.as_raw_fd(),
                    ioc_read(0x18, KEY_BYTES) as _,
                    keys.as_mut_ptr(),
                )
            };
            if rc < 0 {
                failed = true;
                continue;
            }
            any |= (BTN_LEFT..BTN_LEFT + 5).any(|b| bit(&keys, b));
        }
        if failed {
            // a device went away (unplug); rescan next time
            self.fds.clear();
            self.scanned = Instant::now()
                .checked_sub(RESCAN_EMPTY_EVERY + Duration::from_secs(1))
                .unwrap_or_else(Instant::now);
        }
        Some(any)
    }
}

// ---------------------------------------------------------------- serve

#[derive(Debug, Clone, Copy)]
enum Track {
    /// Waiting for the geometry to settle (our own resize/move, workspace changes).
    Arming {
        last: (f64, f64, f64, f64),
        since: Instant,
    },
    Armed {
        home: (f64, f64, f64, f64),
    },
    Moving {
        home: (f64, f64, f64, f64),
        last: (f64, f64),
        since: Instant,
    },
}

enum Event {
    Line(String),
    Lost(String),
}

fn spawn_events(tx: mpsc::Sender<Event>) {
    std::thread::spawn(move || loop {
        let stream = Hypr::connect().and_then(|h| h.events());
        match stream {
            Ok(s) => {
                for line in BufReader::new(s).lines() {
                    match line {
                        Ok(l) => {
                            if tx.send(Event::Line(l)).is_err() {
                                return;
                            }
                        }
                        Err(_) => break,
                    }
                }
                if tx.send(Event::Lost("event socket closed".into())).is_err() {
                    return;
                }
            }
            Err(e) => {
                if tx.send(Event::Lost(e)).is_err() {
                    return;
                }
            }
        }
        std::thread::sleep(Duration::from_secs(1));
    });
}

/// on_open attempts before a pending window is given up on (errors count as attempts).
const PENDING_MAX_ATTEMPTS: u8 = 10;

/// Record one on_open attempt for `a`; true when the window leaves the pending set (decided,
/// or out of attempts). Failed attempts age the entry too, so a window whose lookup keeps
/// erroring cannot stay pending (and keep the poll loop hot) forever.
fn record_attempt(pending: &mut HashMap<String, u8>, a: &str, outcome: &Result<bool, String>) -> bool {
    let n = pending.get(a).copied().unwrap_or(0).saturating_add(1);
    let done = matches!(outcome, Ok(true)) || n >= PENDING_MAX_ATTEMPTS;
    if done {
        pending.remove(a);
    } else {
        pending.insert(a.to_owned(), n);
    }
    done
}

struct Server {
    cfg: Config,
    class_filter: Option<Regex>,
    hypr: Hypr,
    tracked: HashMap<String, Track>,
    pending: HashMap<String, u8>,
    buttons: Buttons,
}

fn geom_of(c: &Json) -> Option<(f64, f64, f64, f64)> {
    let (x, y) = pair(c, "at")?;
    let (w, h) = pair(c, "size")?;
    Some((x, y, w, h))
}

impl Server {
    fn log(&self, msg: &str) {
        eprintln!("[miosd placement] {msg}");
    }

    fn clients(&self) -> Result<Vec<Json>, String> {
        Ok(self
            .hypr
            .json("clients")?
            .as_array()
            .cloned()
            .unwrap_or_default())
    }

    fn filtered(&self, c: &Json) -> bool {
        match &self.class_filter {
            Some(re) => re.is_match(c.get("class").and_then(Json::as_str).unwrap_or("")),
            None => true,
        }
    }

    /// Adopt windows already carrying the managed tag (daemon restart).
    fn adopt(&mut self) -> Result<(), String> {
        for c in self.clients()? {
            let Some(a) = c.get("address").and_then(Json::as_str) else {
                continue;
            };
            if self.filtered(&c)
                && c.get("floating").and_then(Json::as_bool) == Some(true)
                && tags(&c).contains(&self.cfg.managed_tag)
            {
                if let Some(g) = geom_of(&c) {
                    self.tracked.insert(
                        a.to_owned(),
                        Track::Arming {
                            last: g,
                            since: Instant::now(),
                        },
                    );
                }
            }
        }
        Ok(())
    }

    /// Decide on a freshly mapped window. Returns false while it should be retried.
    fn on_open(&mut self, address: &str) -> Result<bool, String> {
        let clients = self.clients()?;
        let Some(c) = clients
            .iter()
            .find(|c| c.get("address").and_then(Json::as_str) == Some(address))
        else {
            return Ok(false);
        };
        if !self.filtered(c) || !tags(c).contains(&self.cfg.open_tag) {
            return Ok(true);
        }
        let floating = c.get("floating").and_then(Json::as_bool) == Some(true);
        let fullscreen = c.get("fullscreen").and_then(Json::as_i64).unwrap_or(0) != 0;
        let pinned = c.get("pinned").and_then(Json::as_bool) == Some(true);
        let Some((_, _, w, h)) = geom_of(c) else {
            return Ok(false);
        };
        if w < 1.0 || h < 1.0 {
            return Ok(false);
        }
        if !floating || fullscreen || pinned {
            return Ok(true);
        }
        if let Some(ex) = self.cfg.excludes.iter().find(|e| e.matches(c)) {
            self.log(&format!(
                "{address}: matches window rule {}, left alone",
                ex.name
            ));
            return Ok(true);
        }
        let monitors = monitor_boxes(&self.hypr.json("monitors")?);
        let mon_id = c.get("monitor").and_then(Json::as_i64).unwrap_or(0);
        let Some(m) = monitors.get(&mon_id) else {
            return Ok(false);
        };
        // The rule's size proves the placement rule (not a dialog heuristic or a later
        // rule) decided this window's geometry. Expression variables may be logical or
        // pixel-mode sizes depending on Hyprland internals, so accept either basis.
        let tol = self.cfg.size_tolerance;
        let fits = |(rw, rh): (f64, f64)| (rw - w).abs() <= tol && (rh - h).abs() <= tol;
        let scale = monitors_scale(&self.hypr, mon_id);
        if !fits(rule_size(&self.cfg, m.w, m.h))
            && !fits(rule_size(&self.cfg, m.w * scale, m.h * scale))
        {
            self.log(&format!(
                "{address}: floated at {w}x{h}, not the placement size; left alone"
            ));
            return Ok(true);
        }
        if self.cfg.scope == Scope::EmptyWorkspace {
            let ws = c.pointer("/workspace/id").and_then(Json::as_i64);
            let busy = clients.iter().any(|o| {
                o.get("address").and_then(Json::as_str) != Some(address)
                    && o.pointer("/workspace/id").and_then(Json::as_i64) == ws
                    && o.get("floating").and_then(Json::as_bool) == Some(false)
            });
            if busy {
                self.hypr.eval(&tile_lua(address, &self.cfg.managed_tag))?;
                return Ok(true);
            }
        }
        let g = geometry(&self.cfg, m);
        self.hypr
            .eval(&place_lua(address, &g, Some(&self.cfg.managed_tag)))?;
        self.log(&format!(
            "{address}: placed {}x{}+{}+{}",
            g.w, g.h, g.x, g.y
        ));
        let last = (g.x as f64, g.y as f64, g.w as f64, g.h as f64);
        self.tracked.insert(
            address.to_owned(),
            Track::Arming {
                last,
                since: Instant::now(),
            },
        );
        Ok(true)
    }

    fn rearm_all(&mut self) {
        let now = Instant::now();
        for t in self.tracked.values_mut() {
            let last = match *t {
                Track::Arming { last, .. } => last,
                Track::Armed { home } | Track::Moving { home, .. } => home,
            };
            *t = Track::Arming { last, since: now };
        }
    }

    fn handle(&mut self, line: &str) -> Result<(), String> {
        let (name, data) = line.split_once(">>").unwrap_or((line, ""));
        match name {
            "openwindow" => {
                let a = addr(data.split(',').next().unwrap_or(""));
                let outcome = self.on_open(&a);
                record_attempt(&mut self.pending, &a, &outcome);
                outcome?;
            }
            "closewindow" => {
                let a = addr(data);
                self.tracked.remove(&a);
                self.pending.remove(&a);
            }
            "changefloatingmode" => {
                let mut it = data.split(',');
                let a = addr(it.next().unwrap_or(""));
                if it.next() == Some("0") {
                    self.tracked.remove(&a);
                } else if !self.tracked.contains_key(&a) {
                    // a managed tag present on a re-floated window (daemon restart races)
                    let c = self.clients()?;
                    if let Some(c) = c
                        .iter()
                        .find(|c| c.get("address").and_then(Json::as_str) == Some(a.as_str()))
                    {
                        if self.filtered(c) && tags(c).contains(&self.cfg.managed_tag) {
                            if let Some(g) = geom_of(c) {
                                self.tracked.insert(
                                    a,
                                    Track::Arming {
                                        last: g,
                                        since: Instant::now(),
                                    },
                                );
                            }
                        }
                    }
                }
            }
            "movewindow" | "movewindowv2" => {
                let a = addr(data.split(',').next().unwrap_or(""));
                if let Some(t) = self.tracked.get_mut(&a) {
                    let last = match *t {
                        Track::Arming { last, .. } => last,
                        Track::Armed { home } | Track::Moving { home, .. } => home,
                    };
                    *t = Track::Arming {
                        last,
                        since: Instant::now(),
                    };
                }
            }
            "workspace" | "workspacev2" | "focusedmon" | "focusedmonv2" | "fullscreen"
            | "monitoradded" | "monitoraddedv2" | "monitorremoved" | "monitorremovedv2"
            | "configreloaded" | "activespecial" | "activespecialv2" | "moveworkspace"
            | "moveworkspacev2" => self.rearm_all(),
            _ => {}
        }
        Ok(())
    }

    fn tick(&mut self) -> Result<(), String> {
        let pending: Vec<String> = self.pending.keys().cloned().collect();
        let mut first_err: Option<String> = None;
        for a in pending {
            let outcome = self.on_open(&a);
            record_attempt(&mut self.pending, &a, &outcome);
            if let Err(e) = outcome {
                first_err.get_or_insert(format!("{a}: {e}"));
            }
        }
        if let Some(e) = first_err {
            // Attempts were counted above; report (marks the connection stale) and retry next tick.
            return Err(e);
        }
        if self.tracked.is_empty() || !self.cfg.tile_on_move {
            return Ok(());
        }
        let clients = self.clients()?;
        let now = Instant::now();
        let settle = self.cfg.settle;
        let thr = self.cfg.move_threshold;
        let mut tile = Vec::new();
        let mut drop = Vec::new();
        let mut held: Option<Option<bool>> = None;
        let addrs: Vec<String> = self.tracked.keys().cloned().collect();
        for a in addrs {
            let Some(c) = clients
                .iter()
                .find(|c| c.get("address").and_then(Json::as_str) == Some(a.as_str()))
            else {
                drop.push(a);
                continue;
            };
            if c.get("floating").and_then(Json::as_bool) != Some(true)
                || !tags(c).contains(&self.cfg.managed_tag)
            {
                drop.push(a);
                continue;
            }
            let Some(g) = geom_of(c) else { continue };
            let hidden = c.get("hidden").and_then(Json::as_bool) == Some(true)
                || c.get("fullscreen").and_then(Json::as_i64).unwrap_or(0) != 0;
            let Some(t) = self.tracked.get_mut(&a) else {
                continue;
            };
            if hidden {
                *t = Track::Arming {
                    last: g,
                    since: now,
                };
                continue;
            }
            let moved =
                |home: (f64, f64, f64, f64)| (g.0 - home.0).abs().max((g.1 - home.1).abs()) > thr;
            let resized = |home: (f64, f64, f64, f64)| {
                (g.2 - home.2).abs() > 2.0 || (g.3 - home.3).abs() > 2.0
            };
            *t = match *t {
                Track::Arming { last, since } => {
                    if last != g {
                        Track::Arming {
                            last: g,
                            since: now,
                        }
                    } else if now.duration_since(since) >= settle {
                        Track::Armed { home: g }
                    } else {
                        Track::Arming { last, since }
                    }
                }
                Track::Armed { home } => {
                    if resized(home) {
                        Track::Arming {
                            last: g,
                            since: now,
                        }
                    } else if moved(home) {
                        Track::Moving {
                            home,
                            last: (g.0, g.1),
                            since: now,
                        }
                    } else {
                        Track::Armed { home }
                    }
                }
                Track::Moving { home, last, since } => {
                    if resized(home) {
                        Track::Arming {
                            last: g,
                            since: now,
                        }
                    } else if (g.0, g.1) != last {
                        Track::Moving {
                            home,
                            last: (g.0, g.1),
                            since: now,
                        }
                    } else if now.duration_since(since) < settle {
                        Track::Moving { home, last, since }
                    } else if !moved(home) {
                        Track::Armed { home }
                    } else {
                        let down = *held.get_or_insert_with(|| self.buttons.down());
                        if down == Some(true) {
                            Track::Moving { home, last, since }
                        } else {
                            tile.push(a.clone());
                            Track::Moving { home, last, since }
                        }
                    }
                }
            };
        }
        for a in drop {
            self.tracked.remove(&a);
        }
        for a in tile {
            self.tracked.remove(&a);
            self.log(&format!("{a}: moved by the operator, tiling"));
            self.hypr.eval(&tile_lua(&a, &self.cfg.managed_tag))?;
        }
        Ok(())
    }
}

fn monitors_scale(h: &Hypr, id: i64) -> f64 {
    h.json("monitors")
        .ok()
        .and_then(|m| {
            m.as_array()?
                .iter()
                .find(|x| x.get("id").and_then(Json::as_i64) == Some(id))?
                .get("scale")?
                .as_f64()
        })
        .filter(|s| *s > 0.0)
        .unwrap_or(1.0)
}

fn serve(cfg: Config, class_filter: Option<String>) -> Result<(), String> {
    if !cfg.enabled {
        eprintln!("[miosd placement] disabled by [desktop.hyprland.placement].enabled");
        return Ok(());
    }
    let class_filter = class_filter
        .map(|p| Regex::new(&p).map_err(|e| e.to_string()))
        .transpose()?;
    let (tx, rx) = mpsc::channel();
    spawn_events(tx);
    let mut srv = Server {
        cfg,
        class_filter,
        hypr: Hypr::connect()?,
        tracked: HashMap::new(),
        pending: HashMap::new(),
        buttons: Buttons::new(),
    };
    srv.log(&format!(
        "serving on {} (pointer buttons {})",
        srv.hypr.dir.display(),
        if srv.buttons.fds.is_empty() {
            "unreadable: settle timing only"
        } else {
            "via evdev"
        }
    ));
    srv.adopt()?;
    // Ticks run on a clock, not on receive timeouts: follow_mouse floods the stream with
    // activewindow events while the pointer moves, which must not starve drag detection.
    let mut next_tick = Instant::now();
    let mut stale = false;
    loop {
        let idle = srv.tracked.is_empty() && srv.pending.is_empty();
        let period = if idle {
            Duration::from_secs(5)
        } else {
            srv.cfg.poll
        };
        let wait = next_tick
            .saturating_duration_since(Instant::now())
            .min(period);
        match rx.recv_timeout(wait) {
            Ok(Event::Line(l)) => {
                if let Err(e) = srv.handle(&l) {
                    srv.log(&format!("{l}: {e}"));
                    stale = true;
                }
            }
            Ok(Event::Lost(e)) => {
                srv.log(&format!("event stream: {e}; reconnecting"));
                stale = true;
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => return Err("event thread exited".into()),
        }
        if stale {
            // The compositor may have restarted under a new instance signature.
            match Hypr::connect() {
                Ok(h) if h.dir != srv.hypr.dir => {
                    srv.log(&format!("reconnected to {}", h.dir.display()));
                    srv.hypr = h;
                    srv.tracked.clear();
                    srv.pending.clear();
                    let _ = srv.adopt();
                    stale = false;
                }
                Ok(_) => stale = false,
                Err(e) => srv.log(&e),
            }
        }
        let now = Instant::now();
        if now >= next_tick {
            if let Err(e) = srv.tick() {
                srv.log(&format!("poll: {e}"));
                stale = true;
            }
            let idle = srv.tracked.is_empty() && srv.pending.is_empty();
            next_tick = now
                + if idle {
                    Duration::from_secs(5)
                } else {
                    srv.cfg.poll
                };
        } else if !srv.pending.is_empty() || !srv.tracked.is_empty() {
            next_tick = next_tick.min(now + srv.cfg.poll);
        }
    }
}

fn toggle(cfg: &Config, window: Option<String>) -> Result<(), String> {
    let hypr = Hypr::connect()?;
    let c = match &window {
        Some(a) => hypr
            .json("clients")?
            .as_array()
            .and_then(|v| {
                v.iter()
                    .find(|c| c.get("address").and_then(Json::as_str) == Some(addr(a).as_str()))
                    .cloned()
            })
            .ok_or_else(|| format!("no window {a}"))?,
        None => hypr.json("activewindow")?,
    };
    let a = c
        .get("address")
        .and_then(Json::as_str)
        .ok_or("no active window")?
        .to_owned();
    if c.get("floating").and_then(Json::as_bool) == Some(true) {
        return hypr.eval(&tile_lua(&a, &cfg.managed_tag));
    }
    let mon_id = c.get("monitor").and_then(Json::as_i64).unwrap_or(0);
    let monitors = monitor_boxes(&hypr.json("monitors")?);
    let m = monitors.get(&mon_id).ok_or("window monitor not found")?;
    hypr.eval(&place_lua(&a, &geometry(cfg, m), None))
}

pub fn run(cmd: &PlacementCommand) -> Result<(), String> {
    let cfg = Config::load()?;
    match cmd {
        PlacementCommand::Serve { class_filter } => serve(cfg, class_filter.clone()),
        PlacementCommand::Toggle { window } => toggle(&cfg, window.clone()),
        PlacementCommand::RuleSize => {
            println!("{}", rule_size_expression(&cfg));
            Ok(())
        }
        PlacementCommand::Geometry => {
            let hypr = Hypr::connect()?;
            let monitors = hypr.json("monitors")?;
            let boxes = monitor_boxes(&monitors);
            let mut out = Vec::new();
            for m in monitors.as_array().into_iter().flatten() {
                let id = m.get("id").and_then(Json::as_i64).unwrap_or(-1);
                if let Some(b) = boxes.get(&id) {
                    let g = geometry(&cfg, b);
                    let (rw, rh) = rule_size(&cfg, b.w, b.h);
                    out.push(serde_json::json!({
                        "monitor": m.get("name"),
                        "logical": [b.w, b.h],
                        "reserved": b.reserved,
                        "width_mm": b.width_mm,
                        "geometry": {"x": g.x, "y": g.y, "w": g.w, "h": g.h},
                        "rule_size": [rw, rh],
                    }));
                }
            }
            println!(
                "{}",
                serde_json::to_string_pretty(&out).map_err(|e| e.to_string())?
            );
            Ok(())
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn cfg() -> Config {
        let doc: Value = toml::from_str(
            r#"
[desktop.hyprland]
window_rules = [
  { name = "pip", match = { class = "^(zen)$", title = "^(Picture-in-Picture)$" }, float = true },
  { name = "modal", match = { modal = true }, float = true },
]
[desktop.hyprland.placement]
enabled = true
scope = "all"
width_ratio = 0.618
aspect = 1.6
min_width_px = 640
min_height_px = 400
max_width_px = 1800
max_width_ratio = 0.9
max_height_ratio = 0.9
max_width_mm = 420
tile_on_move = true
move_threshold_px = 24
settle_ms = 220
poll_ms = 100
size_tolerance_px = 4
open_tag = "mios-place-new"
managed_tag = "mios-placed"
"#,
        )
        .expect("toml");
        Config::from_ssot(&doc).expect("config")
    }

    #[test]
    fn pending_window_is_placed_on_success() {
        let mut p = HashMap::new();
        assert!(!record_attempt(&mut p, "0xa", &Ok(false)));
        assert_eq!(p.get("0xa"), Some(&1));
        assert!(record_attempt(&mut p, "0xa", &Ok(true)));
        assert!(p.is_empty());
    }

    #[test]
    fn erroring_pending_window_ages_out() {
        // Previously an Err skipped the counter: the window stayed pending forever.
        let mut p = HashMap::new();
        let err: Result<bool, String> = Err("hyprctl: socket gone".into());
        let mut left = false;
        for i in 1..=PENDING_MAX_ATTEMPTS {
            left = record_attempt(&mut p, "0xb", &err);
            if i < PENDING_MAX_ATTEMPTS {
                assert!(!left && p.get("0xb") == Some(&i), "attempt {i} not counted");
            }
        }
        assert!(left && p.is_empty(), "error-only window never aged out");
    }

    #[test]
    fn rescan_policy() {
        let s = Duration::from_secs;
        assert!(!should_rescan(false, s(1), false), "steady state: no rescan");
        assert!(!should_rescan(true, s(1), false));
        assert!(should_rescan(false, s(1), true), "hotplug event forces a rescan");
        assert!(should_rescan(false, s(31), false), "periodic fallback with devices present");
        assert!(should_rescan(true, s(11), false));
    }

    #[test]
    fn inotify_reports_hotplugged_event_nodes() {
        let dir = tempfile::tempdir().expect("tempdir");
        let mut b = Buttons::with_dir(dir.path());
        assert!(b.watch.is_some(), "inotify unavailable");
        assert!(!b.hotplugged(), "no node created yet");
        std::fs::write(dir.path().join("event42"), b"").unwrap();
        assert!(b.hotplugged(), "new eventN node not noticed");
        assert!(!b.hotplugged(), "queue not drained");
        // A rescan then runs against the watched dir (regular files are not pointers).
        assert_eq!(b.down(), None);
        assert!(b.scanned.elapsed() < Duration::from_secs(5));
    }

    #[test]
    fn fhd_with_bar_and_dock() {
        let m = MonitorBox {
            x: 0.0,
            y: 0.0,
            w: 1920.0,
            h: 1080.0,
            reserved: [0.0, 44.0, 0.0, 72.0],
            width_mm: 530.0,
        };
        let g = geometry(&cfg(), &m);
        assert_eq!((g.w, g.h), (1187, 742));
        assert_eq!((g.x, g.y), (367, 155));
    }

    #[test]
    fn hidpi_large_panel_hits_mm_cap() {
        // 32" 4K at scale 1.5: logical 2560 wide over 697 mm
        let m = MonitorBox {
            x: 0.0,
            y: 0.0,
            w: 2560.0,
            h: 1440.0,
            reserved: [0.0; 4],
            width_mm: 697.0,
        };
        let g = geometry(&cfg(), &m);
        assert_eq!(g.w, (420.0 * 2560.0 / 697.0_f64).round() as i64);
        assert!(g.h < g.w);
    }

    #[test]
    fn portrait_uses_long_axis() {
        let m = MonitorBox {
            x: 0.0,
            y: 0.0,
            w: 1080.0,
            h: 1920.0,
            reserved: [0.0; 4],
            width_mm: 0.0,
        };
        let g = geometry(&cfg(), &m);
        assert_eq!((g.w, g.h), (742, 1187));
    }

    #[test]
    fn small_screen_clamps_to_usable() {
        let m = MonitorBox {
            x: 0.0,
            y: 0.0,
            w: 800.0,
            h: 480.0,
            reserved: [0.0, 40.0, 0.0, 0.0],
            width_mm: 0.0,
        };
        let g = geometry(&cfg(), &m);
        assert!(g.w <= 720 && g.h <= 396, "{g:?}");
        assert!(g.x >= 0 && g.y >= 40);
    }

    #[test]
    fn expression_has_one_space_and_mirrors_rule_size() {
        let e = rule_size_expression(&cfg());
        assert_eq!(e.matches(' ').count(), 1);
        assert!(e.contains("monitor_w*0.618"));
        assert_eq!(rule_size(&cfg(), 1920.0, 1080.0), (1187.0, 742.0));
    }

    #[test]
    fn exclusions_skip_unevaluable_keys() {
        let c = cfg();
        assert_eq!(c.excludes.len(), 1);
        let pip: Json = serde_json::json!({"class": "zen", "title": "Picture-in-Picture"});
        let other: Json = serde_json::json!({"class": "zen", "title": "Zen"});
        assert!(c.excludes[0].matches(&pip));
        assert!(!c.excludes[0].matches(&other));
    }

    #[test]
    fn lua_quotes_are_escaped() {
        assert_eq!(lua_str("a'b"), "'a\\'b'");
        assert!(place_lua(
            "0x1",
            &Geometry {
                x: 1,
                y: 2,
                w: 3,
                h: 4
            },
            Some("mios-placed")
        )
        .contains("tag='+mios-placed'"));
        let user = place_lua(
            "0x1",
            &Geometry {
                x: 1,
                y: 2,
                w: 3,
                h: 4,
            },
            None,
        );
        assert!(!user.contains("tag") && user.contains("action='enable'"));
    }
}
