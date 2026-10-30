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
    pub opening: Option<mios_resolver::window_opening::Policy>,
    /// [desktop.hyprland.placement.games]: game windows open on the focused output (see [`game_plan`]).
    pub games: Option<mios_resolver::window_opening::Games>,
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
            opening: mios_resolver::window_opening::Policy::from_ssot(doc)?,
            games: mios_resolver::window_opening::Games::from_ssot(doc)?,
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
    if let Some(opening) = &cfg.opening { return opening.size(usable_w, usable_h, px_per_mm); }
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
    let [rl, rt, rr, rb] = cfg.opening.as_ref().map(|p| p.insets(m.reserved)).unwrap_or(m.reserved);
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
    if let Some(opening) = &cfg.opening {
        let [l,t,r,b] = opening.safe_insets;
        return opening.size((monitor_w-l-r).max(1.0), (monitor_h-t-b).max(1.0), None);
    }
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
    if let Some(opening) = &cfg.opening { return opening.rule_expression(); }
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

pub(crate) fn runtime_dir() -> PathBuf {
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

    /// The instance directory this connection talks to.
    pub(crate) fn dir(&self) -> &Path {
        &self.dir
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

    /// `hyprctl notify`: an on-screen notification (icon: 0 warning, 1 info, 2 hint, 3 error, 4 confused, 5 ok).
    pub(crate) fn notify(&self, icon: i32, ms: u32, msg: &str) -> Result<(), String> {
        let line: String = msg.chars().map(|c| if c.is_control() { ' ' } else { c }).collect();
        let out = self.request(&format!("notify {icon} {ms} 0 {line}"))?;
        if out.trim() == "ok" {
            Ok(())
        } else {
            Err(out.trim().to_owned())
        }
    }

    pub(crate) fn events(&self) -> Result<UnixStream, String> {
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

pub(crate) fn addr(a: &str) -> String {
    if a.starts_with("0x") {
        a.to_owned()
    } else {
        format!("0x{a}")
    }
}

pub(crate) fn lua_str(s: &str) -> String {
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

pub(crate) fn tags(c: &Json) -> Vec<String> {
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

pub(crate) fn pair(c: &Json, key: &str) -> Option<(f64, f64)> {
    let a = c.get(key)?.as_array()?;
    Some((a.first()?.as_f64()?, a.get(1)?.as_f64()?))
}

// ---------------------------------------------------------------- game windows

/// Which kind of game a window is. Decided from what the client declares, never from its class or float state
/// alone: an XWayland window is Fixed when WM_NORMAL_HINTS min == max (Wine non-resizable, SDL without
/// RESIZABLE), and a transient (WM_TRANSIENT_FOR), modal or non-NORMAL-typed X11 window is a dialog, not a game,
/// even when its class matches (every Wine window's class is `<app>.exe`).
#[derive(Clone, Copy, Debug, PartialEq)]
pub enum GameKind {
    Resizable,
    Fixed,
}

/// What XWayland says about a window, read with the rules Hyprland itself applies when it floats an X11 window.
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct X11Props {
    /// WM_NORMAL_HINTS min == max (both > 0): the one size the client renders at.
    pub fixed: Option<(f64, f64)>,
    /// WM_TRANSIENT_FOR set, _NET_WM_STATE_MODAL, or a first _NET_WM_WINDOW_TYPE other than NORMAL.
    pub dialog: bool,
}

/// min == max from a WM_SIZE_HINTS property (flags, x, y, w, h, min_w, min_h, max_w, max_h, ...), the raw values
/// Hyprland's float heuristic compares.
pub fn fixed_from_hints(h: &[u32]) -> Option<(f64, f64)> {
    let v = |i: usize| h.get(i).map(|x| *x as i32).unwrap_or(0);
    let (min_w, min_h, max_w, max_h) = (v(5), v(6), v(7), v(8));
    (min_w > 0 && min_h > 0 && min_w == max_w && min_h == max_h).then_some((min_w as f64, min_h as f64))
}

pub fn game_kind(cfg: &Config, c: &Json, x11: Option<&X11Props>) -> Option<GameKind> {
    let g = cfg.games.as_ref()?;
    let t = tags(c);
    let (resizable, fixed) = (t.contains(&g.tag), t.contains(&g.fixed_tag) || t.contains(&g.candidate_tag));
    if !resizable && !fixed {
        return None;
    }
    if c.get("xwayland").and_then(Json::as_bool) == Some(true) {
        return match x11 {
            Some(p) if p.dialog => None,
            Some(p) if p.fixed.is_some() => Some(GameKind::Fixed),
            // Hyprland did not float it (the resizable rule matched), so it is neither fixed-size nor a dialog.
            _ if resizable => Some(GameKind::Resizable),
            // Floated for a reason the client did not declare to us (or XWayland unreadable): not ours to move.
            _ => None,
        };
    }
    // Wayland: a resizable rule match, or a fixed-size surface that declared content type "game".
    if resizable {
        Some(GameKind::Resizable)
    } else if c.get("contentType").and_then(Json::as_str) == Some("game") {
        Some(GameKind::Fixed)
    } else {
        None
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct GamePlan {
    pub monitor: i64,
    pub geometry: Geometry,
    pub resized: bool,
    /// Active workspace of the focused output when the window opened elsewhere (X11 games are put on the output
    /// their requested position falls in, e.g. 0,0 of the XWayland root = the leftmost output).
    pub workspace: Option<i64>,
    pub lua: String,
}

/// Lua pinning a fixed-size game's min_size/max_size (drags clamp to it). Refreshed whenever the game changes its
/// own size: Hyprland's ConfigureRequest path ignores both, so the pin never blocks a resolution change.
fn pin_lua(address: &str, w: i64, h: i64) -> String {
    let win = lua_str(&format!("address:{address}"));
    ["min_size", "max_size"]
        .iter()
        .map(|p| format!("hl.dispatch(hl.dsp.window.set_prop({{prop='{p}', value='{w} {h}', window={win}}}))"))
        .collect::<Vec<_>>()
        .join("; ")
}

/// Geometry for a game window: the focused output (the operator launched it there; the window's own monitor only
/// when no output reports focus), centred in its usable area, resizable games at the opening size and fixed-size
/// games at `fixed` (their declared size; [`mios_resolver::window_opening::game_geometry`]). `managed` tags a
/// resizable game so a drag tiles it like any placed window; fixed-size games are never tiled (that would resize
/// them). `pin` adds min_size/max_size = the placed size for a fixed one.
pub fn game_plan(
    cfg: &Config, c: &Json, monitors: &Json, kind: GameKind, fixed: Option<(f64, f64)>, managed: Option<&str>, pin: bool,
) -> Option<GamePlan> {
    let address = c.get("address").and_then(Json::as_str)?;
    let boxes = monitor_boxes(monitors);
    let all = monitors.as_array()?;
    let focused = all.iter().find(|m| m.get("focused").and_then(Json::as_bool) == Some(true));
    let own = c.get("monitor").and_then(Json::as_i64).unwrap_or(0);
    let mon = focused.and_then(|m| m.get("id").and_then(Json::as_i64)).unwrap_or(own);
    let m = boxes.get(&mon)?;
    let active_ws = all
        .iter()
        .find(|x| x.get("id").and_then(Json::as_i64) == Some(mon))
        .and_then(|x| x.pointer("/activeWorkspace/id"))
        .and_then(Json::as_i64);
    let [rl, rt, rr, rb] = cfg.opening.as_ref().map(|p| p.insets(m.reserved)).unwrap_or(m.reserved);
    let usable = (m.x + rl, m.y + rt, (m.w - rl - rr).max(1.0), (m.h - rt - rb).max(1.0));
    let p = geometry(cfg, m);
    let policy = (p.x as f64, p.y as f64, p.w as f64, p.h as f64);
    let fixed = match kind {
        GameKind::Fixed => Some(fixed.or_else(|| pair(c, "size"))?),
        GameKind::Resizable => None,
    };
    let oversize = cfg.games.as_ref().map(|g| g.oversize).unwrap_or(mios_resolver::window_opening::Oversize::Fit);
    let (x, y, w, h, resized) =
        mios_resolver::window_opening::game_geometry(fixed, usable, (m.x, m.y, m.w, m.h), policy, oversize);
    let g = Geometry { x: x as i64, y: y as i64, w: w as i64, h: h as i64 };
    let ws = c.pointer("/workspace/id").and_then(Json::as_i64);
    let workspace = active_ws.filter(|a| *a > 0 && ws.is_some_and(|w| w > 0) && ws != Some(*a));
    let win = lua_str(&format!("address:{address}"));
    let mut lua = format!("local w={win}; ");
    if let Some(id) = workspace {
        lua += &format!("hl.dispatch(hl.dsp.window.move({{workspace='{id}', follow=false, window=w}})); ");
    }
    lua += "hl.dispatch(hl.dsp.window.float({action='enable', window=w})); ";
    // A fixed game whose declared size differs from what it has (a dispatch grew it, or oversize = fit) is sized.
    let current = pair(c, "size");
    let size_it = resized || (kind == GameKind::Fixed && current.is_some_and(|(cw, ch)| (cw - w).abs() > 2.0 || (ch - h).abs() > 2.0));
    if size_it {
        lua += &format!("hl.dispatch(hl.dsp.window.resize({{x={}, y={}, window=w}})); ", g.w, g.h);
    }
    lua += &format!("hl.dispatch(hl.dsp.window.move({{x={}, y={}, window=w}}))", g.x, g.y);
    if kind == GameKind::Fixed && pin {
        lua += "; ";
        lua += &pin_lua(address, g.w, g.h);
    }
    if let (GameKind::Resizable, Some(t)) = (kind, managed) {
        lua += &format!("; hl.dispatch(hl.dsp.window.tag({{tag={}, window=w}}))", lua_str(&format!("+{t}")));
    }
    if let (GameKind::Fixed, Some(g)) = (kind, cfg.games.as_ref()) {
        // The rule only tagged a candidate; this window is a fixed-size game (static tag: other rules may match it).
        lua += &format!("; hl.dispatch(hl.dsp.window.tag({{tag={}, window=w}}))", lua_str(&format!("+{}", g.fixed_tag)));
    }
    Some(GamePlan { monitor: mon, geometry: g, resized, workspace, lua })
}

/// Corrections per watched game over its lifetime (refused remaps + size restores); then the game is left alone
/// instead of fought (another tool, or a game that insists on its mode).
pub const GAME_CORRECTIONS: u8 = 8;

/// A fixed-size game that something other than the game grew or shrank (Hyprland's `window.resize` dispatch
/// ignores min_size/max_size and X11 size hints): Lua that puts it back at `target` around the same centre, or
/// None while it has that size or is fullscreen (fullscreen is the game's own mode switch).
pub fn pin_restore(c: &Json, target: (f64, f64)) -> Option<String> {
    let address = c.get("address").and_then(Json::as_str)?;
    if c.get("fullscreen").and_then(Json::as_i64).unwrap_or(0) != 0 {
        return None;
    }
    let (x, y) = pair(c, "at")?;
    let (w, h) = pair(c, "size")?;
    if (w - target.0).abs() <= 2.0 && (h - target.1).abs() <= 2.0 {
        return None;
    }
    let (nx, ny) = ((x + (w - target.0) / 2.0).round(), (y + (h - target.1) / 2.0).round());
    Some(format!(
        "local w={}; hl.dispatch(hl.dsp.window.resize({{x={}, y={}, window=w}})); hl.dispatch(hl.dsp.window.move({{x={}, y={}, window=w}}))",
        lua_str(&format!("address:{address}")), target.0 as i64, target.1 as i64, nx as i64, ny as i64
    ))
}

/// A game's own geometry change that is refused: the XWayland root (the bounding box of every output, where
/// Wine/Proton put a borderless "fullscreen" window: recorded Forza Horizon 6 at 3840x1080+0+0 over two outputs)
/// or a box no single output contains (larger than an output, straddling two, or partly off-screen). Any other
/// size or position a game asks for (a windowed resolution change) is its own business.
pub fn is_remap(g: (f64, f64, f64, f64), monitors: &HashMap<i64, MonitorBox>) -> bool {
    if monitors.is_empty() {
        return false;
    }
    let (mut x0, mut y0, mut x1, mut y1) = (f64::MAX, f64::MAX, f64::MIN, f64::MIN);
    for m in monitors.values() {
        x0 = x0.min(m.x);
        y0 = y0.min(m.y);
        x1 = x1.max(m.x + m.w);
        y1 = y1.max(m.y + m.h);
    }
    let near = |a: f64, b: f64| (a - b).abs() <= 2.0;
    let root = near(g.0, x0) && near(g.1, y0) && near(g.2, x1 - x0) && near(g.3, y1 - y0);
    let contained = monitors.values().any(|m| {
        g.0 >= m.x - 1.0 && g.1 >= m.y - 1.0 && g.0 + g.2 <= m.x + m.w + 1.0 && g.1 + g.3 <= m.y + m.h + 1.0
    });
    root || !contained
}

/// One placed game the daemon keeps honest.
#[derive(Clone, Debug, PartialEq)]
pub struct GameWatch {
    pub kind: GameKind,
    /// The last geometry the daemon placed or accepted; None while tiled/fullscreen/hidden (taken as-is on return).
    pub ok: Option<(f64, f64, f64, f64)>,
    pub workspace: Option<i64>,
    /// Fixed + pinned: the size the game renders (its declared size, or the fitted size for oversize = fit).
    pub target: Option<(f64, f64)>,
    /// The declared size the target was taken from; a different declaration is the game changing its size.
    pub declared: Option<(f64, f64)>,
    /// The declaration that came with a refused remap (ignored as a size change).
    pub refused: Option<(f64, f64)>,
    pub since: Option<Instant>,
    pub corrections: u8,
}

#[derive(Clone, Debug, PartialEq)]
pub enum GameStep {
    Nothing,
    /// Refuse a remap: back to the accepted geometry (and workspace).
    Refuse(String),
    /// Restore a fixed game's size around its centre.
    Restore(String),
    /// The game changed its own declared size; re-pin to it.
    Repin(String),
    GiveUp,
}

/// One poll of a watched game: what to do about its current client JSON. `declared` is the current X11 min == max
/// size (None for Wayland or a client that dropped the hints), `held` whether a pointer button is down (a drag is
/// never fought), `settle` how long a fixed game's size may disagree with its declaration before it is restored
/// (Wine re-issues the hints right after the ConfigureNotify of a resize it asked for).
pub fn game_step(
    w: &mut GameWatch, c: &Json, monitors: &HashMap<i64, MonitorBox>, declared: Option<(f64, f64)>, refuse: bool,
    held: bool, settle: Duration, now: Instant,
) -> GameStep {
    let Some(address) = c.get("address").and_then(Json::as_str) else { return GameStep::Nothing };
    let Some(g) = geom_of(c) else { return GameStep::Nothing };
    let floating = c.get("floating").and_then(Json::as_bool) == Some(true);
    let fullscreen = c.get("fullscreen").and_then(Json::as_i64).unwrap_or(0) != 0;
    let special = c.pointer("/workspace/name").and_then(Json::as_str).is_some_and(|n| n.starts_with("special:"));
    let hidden = c.get("hidden").and_then(Json::as_bool) == Some(true);
    if !floating || fullscreen || special || hidden {
        w.ok = None;
        w.since = None;
        return GameStep::Nothing;
    }
    let ws = c.pointer("/workspace/id").and_then(Json::as_i64);
    let Some(ok) = w.ok else {
        w.ok = Some(g);
        w.workspace = ws;
        return GameStep::Nothing;
    };
    let same = |a: (f64, f64, f64, f64), b: (f64, f64, f64, f64)| {
        (a.0 - b.0).abs() <= 1.0 && (a.1 - b.1).abs() <= 1.0 && (a.2 - b.2).abs() <= 1.0 && (a.3 - b.3).abs() <= 1.0
    };
    if same(g, ok) {
        w.since = None;
        return GameStep::Nothing;
    }
    if held {
        return GameStep::Nothing;
    }
    let budget = |w: &mut GameWatch| {
        w.corrections = w.corrections.saturating_add(1);
        w.corrections <= GAME_CORRECTIONS
    };
    if refuse && c.get("xwayland").and_then(Json::as_bool) == Some(true) && is_remap(g, monitors) {
        if !budget(w) {
            return GameStep::GiveUp;
        }
        w.refused = declared.filter(|d| Some(*d) != w.declared);
        let win = lua_str(&format!("address:{address}"));
        let mut lua = format!("local w={win}; ");
        if let Some(id) = w.workspace.filter(|id| *id > 0 && ws != Some(*id)) {
            lua += &format!("hl.dispatch(hl.dsp.window.move({{workspace='{id}', follow=false, window=w}})); ");
        }
        lua += &format!(
            "hl.dispatch(hl.dsp.window.resize({{x={}, y={}, window=w}})); hl.dispatch(hl.dsp.window.move({{x={}, y={}, window=w}}))",
            ok.2 as i64, ok.3 as i64, ok.0 as i64, ok.1 as i64
        );
        return GameStep::Refuse(lua);
    }
    let Some(target) = w.target else {
        // Resizable (or unpinned): whatever the game or the operator chose inside an output stands.
        w.ok = Some(g);
        w.workspace = ws;
        return GameStep::Nothing;
    };
    // The game declared a new fixed size (a windowed resolution change): that is the size it renders now.
    let new_target = match declared {
        Some(d) if Some(d) != w.declared && Some(d) != w.refused => Some(d),
        _ => None,
    };
    let want = new_target.unwrap_or(target);
    let fits = (g.2 - want.0).abs() <= 2.0 && (g.3 - want.1).abs() <= 2.0;
    if fits {
        w.ok = Some(g);
        w.workspace = ws;
        w.since = None;
        if let Some(d) = new_target {
            w.declared = Some(d);
            w.refused = None;
            w.target = Some(d);
            return GameStep::Repin(pin_lua(address, d.0 as i64, d.1 as i64));
        }
        return GameStep::Nothing;
    }
    let since = *w.since.get_or_insert(now);
    if now.duration_since(since) < settle {
        return GameStep::Nothing;
    }
    w.since = None;
    if !budget(w) {
        return GameStep::GiveUp;
    }
    match pin_restore(c, want) {
        Some(lua) => GameStep::Restore(lua),
        None => GameStep::Nothing,
    }
}

// ---------------------------------------------------------------- XWayland properties

/// Reads the X11 properties behind [`X11Props`] from XWayland (DISPLAY, incl. the `unix:<socket>` form nested
/// sessions publish). A Hyprland client is matched to its X11 window through _NET_CLIENT_LIST by WM_CLASS, then
/// _NET_WM_PID, title and geometry; the window id is cached per address. Every failure reconnects next time.
pub struct X11 {
    conn: Option<(x11rb::rust_connection::RustConnection, u32, HashMap<&'static str, u32>)>,
    ids: HashMap<String, u32>,
}

const X11_ATOMS: [&str; 7] = [
    "_NET_CLIENT_LIST", "_NET_WM_PID", "_NET_WM_NAME", "_NET_WM_STATE", "_NET_WM_STATE_MODAL", "_NET_WM_WINDOW_TYPE",
    "_NET_WM_WINDOW_TYPE_NORMAL",
];

impl X11 {
    pub fn new() -> Self {
        X11 { conn: None, ids: HashMap::new() }
    }

    fn connect(&mut self) -> Option<()> {
        use x11rb::connection::Connection as _;
        use x11rb::protocol::xproto::ConnectionExt as _;
        if self.conn.is_some() {
            return Some(());
        }
        let display = std::env::var("DISPLAY").ok().filter(|d| !d.is_empty())?;
        // x11rb 0.14 parses `unix:/path` but then connects to /tmp/.X11-unix/X0 (another XWayland, or none): a socket
        // path is connected directly (no auth, as XWayland under Hyprland serves its own clients).
        let (c, screen) = match x11_socket_path(&display) {
            Some((path, screen)) => {
                let s = std::os::unix::net::UnixStream::connect(&path).ok()?;
                let (stream, _) = x11rb::rust_connection::DefaultStream::from_unix_stream(s).ok()?;
                (x11rb::rust_connection::RustConnection::connect_to_stream(stream, screen).ok()?, screen)
            }
            None => x11rb::connect(Some(&display)).ok()?,
        };
        let root = c.setup().roots.get(screen)?.root;
        let mut atoms = HashMap::new();
        for name in X11_ATOMS {
            atoms.insert(name, c.intern_atom(false, name.as_bytes()).ok()?.reply().ok()?.atom);
        }
        self.conn = Some((c, root, atoms));
        Some(())
    }

    fn prop32(&self, win: u32, atom: u32) -> Option<Vec<u32>> {
        use x11rb::protocol::xproto::{AtomEnum, ConnectionExt as _};
        let (c, _, _) = self.conn.as_ref()?;
        let r = c.get_property(false, win, atom, AtomEnum::ANY, 0, 1024).ok()?.reply().ok()?;
        Some(r.value32().map(|v| v.collect()).unwrap_or_default())
    }

    fn text(&self, win: u32, atom: u32) -> Option<Vec<u8>> {
        use x11rb::protocol::xproto::{AtomEnum, ConnectionExt as _};
        let (c, _, _) = self.conn.as_ref()?;
        Some(c.get_property(false, win, atom, AtomEnum::ANY, 0, 1024).ok()?.reply().ok()?.value)
    }

    fn window_for(&mut self, c: &Json) -> Option<u32> {
        use x11rb::protocol::xproto::{AtomEnum, ConnectionExt as _};
        let address = c.get("address").and_then(Json::as_str)?.to_owned();
        self.connect()?;
        let atoms = self.conn.as_ref()?.2.clone();
        let root = self.conn.as_ref()?.1;
        let Some(list) = self.prop32(root, atoms["_NET_CLIENT_LIST"]) else {
            self.conn = None;
            self.ids.clear();
            return None;
        };
        if let Some(id) = self.ids.get(&address) {
            if list.contains(id) {
                return Some(*id);
            }
            self.ids.remove(&address);
        }
        let class = c.get("class").and_then(Json::as_str).unwrap_or("");
        let title = c.get("title").and_then(Json::as_str).unwrap_or("");
        let pid = c.get("pid").and_then(Json::as_u64);
        let at = geom_of(c);
        let mut best: Option<(f64, u32)> = None;
        for win in list {
            let wm_class = self.text(win, AtomEnum::WM_CLASS.into()).unwrap_or_default();
            if !wm_class.split(|b| *b == 0).any(|s| !s.is_empty() && s == class.as_bytes()) {
                continue;
            }
            let wpid = self.prop32(win, atoms["_NET_WM_PID"]).and_then(|v| v.first().copied());
            if let (Some(a), Some(b)) = (pid, wpid) {
                if a != b as u64 {
                    continue;
                }
            }
            let name = self
                .text(win, atoms["_NET_WM_NAME"])
                .filter(|v| !v.is_empty())
                .or_else(|| self.text(win, AtomEnum::WM_NAME.into()))
                .unwrap_or_default();
            let mut score = if name == title.as_bytes() { 0.0 } else { 1e6 };
            if let (Some(g), Some(r)) = (at, self.conn.as_ref().and_then(|(cn, _, _)| cn.get_geometry(win).ok()?.reply().ok())) {
                score += (g.0 - r.x as f64).abs() + (g.1 - r.y as f64).abs() + (g.2 - r.width as f64).abs() + (g.3 - r.height as f64).abs();
            }
            if best.map_or(true, |(s, _)| score < s) {
                best = Some((score, win));
            }
        }
        let (_, id) = best?;
        self.ids.insert(address, id);
        Some(id)
    }

    /// None when XWayland is unreachable or the window is not (yet) in its client list.
    pub fn props(&mut self, c: &Json) -> Option<X11Props> {
        use x11rb::protocol::xproto::AtomEnum;
        let win = self.window_for(c)?;
        let atoms = self.conn.as_ref()?.2.clone();
        let hints = self.prop32(win, AtomEnum::WM_NORMAL_HINTS.into());
        let Some(hints) = hints else {
            self.conn = None;
            self.ids.clear();
            return None;
        };
        let transient = self.prop32(win, AtomEnum::WM_TRANSIENT_FOR.into()).unwrap_or_default();
        let state = self.prop32(win, atoms["_NET_WM_STATE"]).unwrap_or_default();
        let types = self.prop32(win, atoms["_NET_WM_WINDOW_TYPE"]).unwrap_or_default();
        Some(props_from(&hints, &transient, &state, &types, atoms["_NET_WM_STATE_MODAL"], atoms["_NET_WM_WINDOW_TYPE_NORMAL"]))
    }

    pub fn forget(&mut self, address: &str) {
        self.ids.remove(address);
    }
}

/// The socket a path-form DISPLAY names (`unix:/run/.../X1`, `/run/.../X1`, optionally `.screen`), or None for
/// the `[host]:N` forms x11rb resolves itself.
pub fn x11_socket_path(display: &str) -> Option<(String, usize)> {
    let p = display.strip_prefix("unix:").unwrap_or(display);
    if !p.starts_with('/') {
        return None;
    }
    if let Some((path, screen)) = p.rsplit_once('.') {
        if let Ok(n) = screen.parse::<usize>() {
            if !path.ends_with('/') && !Path::new(p).exists() {
                return Some((path.to_owned(), n));
            }
        }
    }
    Some((p.to_owned(), 0))
}

/// [`X11Props`] from the raw property values (WM_NORMAL_HINTS, WM_TRANSIENT_FOR, _NET_WM_STATE, _NET_WM_WINDOW_TYPE).
pub fn props_from(hints: &[u32], transient: &[u32], state: &[u32], types: &[u32], modal: u32, normal: u32) -> X11Props {
    X11Props {
        fixed: fixed_from_hints(hints),
        dialog: transient.first().is_some_and(|w| *w != 0)
            || state.contains(&modal)
            || types.first().is_some_and(|t| *t != normal),
    }
}

// ---------------------------------------------------------------- evdev button state

/// Reads mouse button state with EVIOCGKEY on every input device exposing BTN_LEFT. Read-only
/// ioctls: nothing is grabbed and no events are consumed. Unavailable without the input group.
pub(crate) struct Buttons {
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
    pub(crate) fn new() -> Self {
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
    pub(crate) fn down(&mut self) -> Option<bool> {
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
    /// Placed XWayland games ([`game_step`]): remaps refused, fixed sizes kept, own size changes followed.
    games: HashMap<String, GameWatch>,
    x11: X11,
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
        if tags(c).contains(&"mios-pip-managed".to_string()) { return false; }
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
            // Games placed before a restart: watched again at their current geometry, fixed ones at their declared
            // size (the daemon's per-window state is not persisted; X11 still has the hints).
            if self.filtered(&c) && c.get("xwayland").and_then(Json::as_bool) == Some(true) {
                let x11 = self.x11.props(&c);
                if let Some(kind) = game_kind(&self.cfg, &c, x11.as_ref()) {
                    let declared = x11.and_then(|p| p.fixed);
                    let pin = kind == GameKind::Fixed && self.cfg.games.as_ref().is_some_and(|x| x.pin_fixed_size);
                    // A fitted oversize game keeps the size it has; any other fixed game the size it declares.
                    let target = pin.then(|| declared.filter(|d| d.0 * d.1 <= geom_of(&c).map_or(0.0, |g| g.2 * g.3) + 4.0)
                        .or_else(|| pair(&c, "size"))).flatten();
                    self.games.insert(a.to_owned(), GameWatch {
                        kind, ok: None, workspace: None, target, declared, refused: None, since: None, corrections: 0,
                    });
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
        if !self.filtered(c) {
            return Ok(true);
        }
        let game_tags: Vec<String> = self
            .cfg
            .games
            .as_ref()
            .map(|g| [g.tag.clone(), g.fixed_tag.clone(), g.candidate_tag.clone()].into_iter().filter(|t| tags(c).contains(t)).collect())
            .unwrap_or_default();
        let xwayland = c.get("xwayland").and_then(Json::as_bool) == Some(true);
        let x11 = if xwayland && !game_tags.is_empty() { self.x11.props(c) } else { None };
        if xwayland && !game_tags.is_empty() && x11.is_none() && std::env::var_os("DISPLAY").is_some()
            && self.pending.get(address).copied().unwrap_or(0) + 1 < PENDING_MAX_ATTEMPTS
        {
            // Not in XWayland's client list yet: ask again (the last attempt decides without X11).
            return Ok(false);
        }
        if xwayland && !game_tags.is_empty() && x11.is_none() {
            self.log(&format!("{address}: XWayland properties unreadable (DISPLAY {:?}); deciding from tags alone", std::env::var("DISPLAY").ok()));
        }
        let game = game_kind(&self.cfg, c, x11.as_ref());
        if game.is_none() && !game_tags.is_empty() && x11.is_some() {
            // A transient/modal/dialog-typed (or floated, not fixed-size) window of a game class: not a game.
            self.log(&format!("{address}: game class but a dialog or not fixed-size ({x11:?}); left alone"));
        }
        if game.is_none() && !tags(c).contains(&self.cfg.open_tag) {
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
        if let Some(kind) = game {
            // No size gate: a game's own geometry is exactly what is being corrected.
            let declared = x11.and_then(|p| p.fixed);
            let pin = kind == GameKind::Fixed && xwayland && self.cfg.games.as_ref().is_some_and(|x| x.pin_fixed_size);
            let Some(plan) =
                game_plan(&self.cfg, c, &self.hypr.json("monitors")?, kind, declared, Some(&self.cfg.managed_tag), pin)
            else {
                return Ok(false);
            };
            self.hypr.eval(&plan.lua)?;
            let g = plan.geometry;
            self.log(&format!(
                "{address}: game ({kind:?}) placed {}x{}+{}+{} on monitor {}{}",
                g.w, g.h, g.x, g.y, plan.monitor,
                if plan.resized { "" } else { " at its own size" }
            ));
            // XWayland only: X11 games position and size themselves with ConfigureRequests (root remaps are refused,
            // their own size changes followed). A Wayland game cannot move itself and sizes its own buffers.
            if xwayland {
                let ws = plan.workspace.or_else(|| c.pointer("/workspace/id").and_then(Json::as_i64));
                self.games.insert(address.to_owned(), GameWatch {
                    kind,
                    ok: Some((g.x as f64, g.y as f64, g.w as f64, g.h as f64)),
                    workspace: ws,
                    target: pin.then_some((g.w as f64, g.h as f64)),
                    declared,
                    refused: None,
                    since: None,
                    corrections: 0,
                });
            }
            if kind == GameKind::Resizable {
                let last = (g.x as f64, g.y as f64, g.w as f64, g.h as f64);
                self.tracked.insert(address.to_owned(), Track::Arming { last, since: Instant::now() });
            }
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
                self.games.remove(&a);
                self.x11.forget(&a);
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

    /// Placed XWayland games: one poll of [`game_step`] each. Monitors, buttons and X11 hints are read only for a
    /// game whose geometry moved off the accepted one.
    fn watch_games(&mut self) -> Result<(), String> {
        let clients = self.clients()?;
        let refuse = self.cfg.games.as_ref().is_some_and(|g| g.suppress_client_geometry);
        let settle = self.cfg.settle;
        let mut monitors: Option<HashMap<i64, MonitorBox>> = None;
        let mut held: Option<bool> = None;
        let addrs: Vec<String> = self.games.keys().cloned().collect();
        for a in addrs {
            let Some(c) = clients.iter().find(|c| c.get("address").and_then(Json::as_str) == Some(a.as_str())) else {
                self.games.remove(&a);
                self.x11.forget(&a);
                continue;
            };
            let mut w = self.games[&a].clone();
            let moved = match (w.ok, geom_of(c)) {
                (Some(ok), Some(g)) => [ok.0 - g.0, ok.1 - g.1, ok.2 - g.2, ok.3 - g.3].iter().any(|d| d.abs() > 1.0),
                _ => false,
            };
            let (declared, h) = if moved {
                if monitors.is_none() {
                    monitors = Some(monitor_boxes(&self.hypr.json("monitors")?));
                }
                let declared = if w.kind == GameKind::Fixed { self.x11.props(c).and_then(|p| p.fixed) } else { None };
                (declared, *held.get_or_insert_with(|| self.buttons.down() == Some(true)))
            } else {
                (None, false)
            };
            let empty = HashMap::new();
            let step = game_step(&mut w, c, monitors.as_ref().unwrap_or(&empty), declared, refuse, h, settle, Instant::now());
            match step {
                GameStep::Nothing => {}
                GameStep::Refuse(lua) => {
                    self.hypr.eval(&lua)?;
                    let ok = w.ok.unwrap_or_default();
                    self.log(&format!("{a}: game remapped itself to {:?}; refused, back at {}x{}+{}+{}", geom_of(c), ok.2, ok.3, ok.0, ok.1));
                }
                GameStep::Restore(lua) => {
                    self.hypr.eval(&lua)?;
                    self.log(&format!("{a}: fixed-size game resized from outside; restored {:?}", w.target));
                }
                GameStep::Repin(lua) => {
                    self.hypr.eval(&lua)?;
                    self.log(&format!("{a}: game changed its own size to {:?}; followed", w.target));
                }
                GameStep::GiveUp => {
                    self.games.remove(&a);
                    self.log(&format!("{a}: game keeps changing its geometry; left alone after {GAME_CORRECTIONS} corrections"));
                    continue;
                }
            }
            self.games.insert(a, w);
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
        if !self.games.is_empty() {
            self.watch_games()?;
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
            if !self.filtered(c) || c.get("floating").and_then(Json::as_bool) != Some(true)
                || !tags(c).contains(&self.cfg.managed_tag)
            {
                drop.push(a);
                continue;
            }
            let Some(g) = geom_of(c) else { continue };
            let on_special = c
                .get("workspace")
                .and_then(|w| w.get("name"))
                .and_then(Json::as_str)
                .is_some_and(|n| n.starts_with("special:"));
            let hidden = on_special
                || c.get("hidden").and_then(Json::as_bool) == Some(true)
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
        games: HashMap::new(),
        x11: X11::new(),
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
        let idle = srv.tracked.is_empty() && srv.pending.is_empty() && srv.games.is_empty();
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
                    srv.games.clear();
                    srv.x11 = X11::new();
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
            let idle = srv.tracked.is_empty() && srv.pending.is_empty() && srv.games.is_empty();
            next_tick = now
                + if idle {
                    Duration::from_secs(5)
                } else {
                    srv.cfg.poll
                };
        } else if !srv.pending.is_empty() || !srv.tracked.is_empty() || !srv.games.is_empty() {
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
    let xwayland = c.get("xwayland").and_then(Json::as_bool) == Some(true);
    let x11 = if xwayland { X11::new().props(&c) } else { None };
    if game_kind(cfg, &c, x11.as_ref()) == Some(GameKind::Fixed) {
        // Tiling resizes; a fixed-size game cannot re-render at another size. Re-centre it at its declared size.
        let pin = xwayland && cfg.games.as_ref().is_some_and(|g| g.pin_fixed_size);
        let plan = game_plan(cfg, &c, &hypr.json("monitors")?, GameKind::Fixed, x11.and_then(|p| p.fixed), None, pin)
            .ok_or("game window monitor not found")?;
        return hypr.eval(&plan.lua);
    }
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
width_ratio = 0.56
aspect = 1.414
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
        assert_eq!((g.w, g.h), (1075, 760));
        assert_eq!((g.x, g.y), (423, 146));
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
        let mut c=cfg();c.width_ratio=0.8; // request enough width to actually reach the physical cap
        let g = geometry(&c, &m);
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
        assert_eq!((g.w, g.h), (760, 1075));
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
        assert!(e.contains("monitor_w*0.56"));
        assert_eq!(rule_size(&cfg(), 1920.0, 1080.0), (1075.0, 760.0));
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

    fn cfg_games(oversize: &str) -> Config {
        let doc: Value = toml::from_str(&format!(
            r#"
[keybindings.global.contexts]
game = ["steam_app_.*", "gamescope"]
[desktop.hyprland.placement]
enabled = true
scope = "all"
width_ratio = 0.56
aspect = 1.414
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
[desktop.hyprland.placement.games]
enabled = true
class = "keybindings.global.contexts.game"
extra_class = ['.+\.exe']
content_game = true
suppress_client_geometry = true
pin_fixed_size = true
oversize = "{oversize}"
tag = "mios-place-game"
fixed_tag = "mios-place-game-fixed"
candidate_tag = "mios-place-game-candidate"
"#
        ))
        .expect("toml");
        Config::from_ssot(&doc).expect("config")
    }

    /// The recorded layout: two 1920x1080 outputs, HUD reserving 44 top / 72 bottom, DP-3 (right) focused.
    fn two_outputs() -> Json {
        serde_json::json!([
            {"id": 0, "name": "HDMI-A-1", "x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1.0, "transform": 0,
             "reserved": [0, 44, 0, 72], "physicalWidth": 530, "physicalHeight": 300, "focused": false, "activeWorkspace": {"id": 1}},
            {"id": 1, "name": "DP-3", "x": 1920, "y": 0, "width": 1920, "height": 1080, "scale": 1.0, "transform": 0,
             "reserved": [0, 44, 0, 72], "physicalWidth": 530, "physicalHeight": 300, "focused": true, "activeWorkspace": {"id": 2}}
        ])
    }

    fn game(class: &str, at: (i64, i64), size: (i64, i64), tags: &[&str]) -> Json {
        serde_json::json!({"address": "0x55c311fcf040", "class": class, "at": [at.0, at.1], "size": [size.0, size.1],
            "monitor": 0, "workspace": {"id": 1, "name": "1"}, "floating": true, "fullscreen": 0, "pinned": false,
            "xwayland": true, "tags": tags})
    }

    #[test]
    fn forza_root_remap_opens_on_the_focused_output() {
        // Journal: steam_app_2483190 mapped 1920x1080, then 3840x1080 at 0,0 (the XWayland root, both outputs).
        let c = cfg_games("fit");
        let w = game("steam_app_2483190", (0, 0), (3840, 1080), &["mios-place-game-fixed*"]);
        let x = X11Props { fixed: Some((3840.0, 1080.0)), dialog: false };
        assert_eq!(game_kind(&c, &w, Some(&x)), Some(GameKind::Fixed));
        let p = game_plan(&c, &w, &two_outputs(), GameKind::Fixed, x.fixed, Some("mios-placed"), true).unwrap();
        assert_eq!(p.monitor, 1, "the focused output, not the root origin's");
        assert_eq!(p.workspace, Some(2), "moved to the focused output's workspace");
        assert!(p.resized, "larger than the output: fit to the opening size");
        let g = p.geometry;
        assert!(g.x >= 1920 && g.x + g.w <= 3840 && g.y >= 44 && g.y + g.h <= 1080 - 72, "{g:?}");
        assert!(p.lua.contains("workspace='2', follow=false") && p.lua.contains("hl.dsp.window.resize"));
        assert!(!p.lua.contains("mios-placed"), "fixed-size games are never tiled on move");
        assert!(p.lua.contains("tag='+mios-place-game-fixed'"), "a confirmed fixed game gets the fixed tag: {}", p.lua);
        assert!(p.lua.contains(&format!("prop='max_size', value='{} {}'", g.w, g.h)), "fit pins the fitted size");
        // keep: centred on the output at its own size (cropped), never resized.
        let k = game_plan(&cfg_games("keep"), &w, &two_outputs(), GameKind::Fixed, x.fixed, None, true).unwrap();
        assert!(!k.resized && !k.lua.contains("window.resize"));
        assert_eq!((k.geometry.x, k.geometry.w), (1920 + (1920 - 3840) / 2, 3840));
    }

    #[test]
    fn fixed_size_game_is_centred_at_its_declared_size() {
        // The offscreen spawn: a 640x480 non-resizable window that asked for 700,500 of another output.
        let c = cfg_games("fit");
        let w = game("steam_app_12", (700, 500), (640, 480), &["mios-place-game-fixed*"]);
        let p = game_plan(&c, &w, &two_outputs(), GameKind::Fixed, Some((640.0, 480.0)), Some("mios-placed"), true).unwrap();
        assert!(!p.resized && !p.lua.contains("window.resize"), "never resized: no black regrown area");
        assert!(p.lua.contains("prop='min_size', value='640 480'") && p.lua.contains("prop='max_size', value='640 480'"),
                "pinned to its own size: {}", p.lua);
        assert_eq!((p.geometry.w, p.geometry.h), (640, 480));
        assert_eq!((p.geometry.x, p.geometry.y), (1920 + 640, 44 + (1080 - 44 - 72 - 480) / 2));
        // Unpinned (Wayland, or pin_fixed_size off): placed the same, no size props.
        let u = game_plan(&c, &w, &two_outputs(), GameKind::Fixed, None, None, false).unwrap();
        assert!(!u.lua.contains("set_prop") && u.geometry == p.geometry);
        // A fixed game something grew (840x600) is put back to its declared size by Super+V's re-centre.
        let grown = game("steam_app_12", (100, 100), (840, 600), &["mios-place-game-fixed*"]);
        let g = game_plan(&c, &grown, &two_outputs(), GameKind::Fixed, Some((640.0, 480.0)), None, true).unwrap();
        assert!(g.lua.contains("resize({x=640, y=480") && (g.geometry.w, g.geometry.h) == (640, 480), "{}", g.lua);
        // 1920x1080 fits the output but not the HUD-free area: whole output, still not resized.
        let full = game("steam_app_12", (0, 0), (1920, 1080), &["mios-place-game-fixed*"]);
        let p = game_plan(&c, &full, &two_outputs(), GameKind::Fixed, Some((1920.0, 1080.0)), None, true).unwrap();
        assert_eq!((p.geometry.x, p.geometry.y, p.resized), (1920, 0, false));
    }

    #[test]
    fn resizable_game_takes_the_opening_size_on_the_focused_output() {
        let c = cfg_games("fit");
        let w = game("steam_app_11", (0, 0), (1920, 1080), &["mios-place-game*", "mios-place-new*"]);
        assert_eq!(game_kind(&c, &w, Some(&X11Props::default())), Some(GameKind::Resizable));
        let p = game_plan(&c, &w, &two_outputs(), GameKind::Resizable, None, Some("mios-placed"), true).unwrap();
        let mut focused = MonitorBox { x: 1920.0, y: 0.0, w: 1920.0, h: 1080.0, reserved: [0.0, 44.0, 0.0, 72.0], width_mm: 530.0 };
        assert_eq!(p.geometry, geometry(&c, &focused));
        assert!(p.resized && p.lua.contains("tag='+mios-placed'"));
        assert!(!p.lua.contains("set_prop"), "resizable games re-render at any size: never pinned");
        // No focus reported: the window's own output.
        let mut mons = two_outputs();
        mons[1]["focused"] = Json::Bool(false);
        let p = game_plan(&c, &w, &mons, GameKind::Resizable, None, None, false).unwrap();
        focused.x = 0.0;
        assert_eq!((p.monitor, p.workspace, p.geometry), (0, None, geometry(&c, &focused)));
    }

    #[test]
    fn games_are_classified_by_what_the_client_declares_not_by_class_or_float() {
        let c = cfg_games("fit");
        let floated = game("setup.exe", (436, 274), (420, 300), &["mios-place-game-candidate*"]);
        let fixed = X11Props { fixed: Some((420.0, 300.0)), dialog: false };
        assert_eq!(game_kind(&c, &floated, Some(&fixed)), Some(GameKind::Fixed), "min == max: a fixed-size game");
        // A resizable WM_TRANSIENT_FOR/modal dialog of a Wine app (class setup.exe): Hyprland floats it, the class
        // matches, but it is not a game. The pre-fix classifier (tag only) returned Fixed here.
        let dialog = X11Props { fixed: None, dialog: true };
        assert_eq!(game_kind(&c, &floated, Some(&dialog)), None);
        assert_eq!(game_kind(&c, &floated, Some(&X11Props { fixed: Some((420.0, 300.0)), dialog: true })), None,
                   "a fixed-size dialog is still a dialog");
        assert_eq!(game_kind(&c, &floated, Some(&X11Props::default())), None, "floated, not fixed-size: left alone");
        assert_eq!(game_kind(&c, &floated, None), None, "XWayland unreadable: a floated window is not moved");
        // A fixed game tiled and floated again carries both tags; its hints still make it Fixed (Super+V re-centres
        // it instead of tiling it).
        let both = game("steam_app_9", (0, 0), (480, 320), &["mios-place-game*", "mios-place-game-fixed*"]);
        assert_eq!(game_kind(&c, &both, Some(&X11Props { fixed: Some((480.0, 320.0)), dialog: false })), Some(GameKind::Fixed));
        assert_eq!(game_kind(&c, &both, None), Some(GameKind::Resizable));
        // Wayland: fixed only with content type game.
        let mut wl = game("steam_app_9", (0, 0), (480, 320), &["mios-place-game-fixed*"]);
        wl["xwayland"] = Json::Bool(false);
        assert_eq!(game_kind(&c, &wl, None), None);
        wl["contentType"] = Json::String("game".into());
        assert_eq!(game_kind(&c, &wl, None), Some(GameKind::Fixed));
    }

    #[test]
    fn x11_hints_and_dialog_markers_are_read_like_hyprland() {
        // WM_SIZE_HINTS: flags, x, y, w, h, min_w, min_h, max_w, max_h, ...
        assert_eq!(fixed_from_hints(&[48, 0, 0, 0, 0, 480, 320, 480, 320, 0, 0]), Some((480.0, 320.0)));
        assert_eq!(fixed_from_hints(&[48, 0, 0, 0, 0, 200, 100, 4000, 3000]), None, "min != max: resizable");
        assert_eq!(fixed_from_hints(&[0, 0, 0, 0, 0, 0, 0, 0, 0]), None);
        assert_eq!(fixed_from_hints(&[16, 0, 0]), None, "short property");
        let (modal, normal, dialog_type) = (11, 20, 21);
        let hints = [48, 0, 0, 0, 0, 420, 300, 420, 300];
        assert_eq!(props_from(&hints, &[], &[], &[normal], modal, normal), X11Props { fixed: Some((420.0, 300.0)), dialog: false });
        assert!(props_from(&hints, &[0x400001], &[], &[normal], modal, normal).dialog, "WM_TRANSIENT_FOR");
        assert!(!props_from(&hints, &[0], &[], &[], modal, normal).dialog, "transient-for None");
        assert!(props_from(&hints, &[], &[3, modal], &[], modal, normal).dialog, "_NET_WM_STATE_MODAL");
        assert!(props_from(&hints, &[], &[], &[dialog_type, normal], modal, normal).dialog, "first window type DIALOG");
    }

    #[test]
    fn path_form_displays_name_their_socket() {
        // Nested sessions publish DISPLAY=unix:<runtime>/x11/X1; x11rb alone would dial /tmp/.X11-unix/X0.
        assert_eq!(x11_socket_path("unix:/run/user/1000/mios-nested-desktop-7/x11/X1"),
                   Some(("/run/user/1000/mios-nested-desktop-7/x11/X1".into(), 0)));
        assert_eq!(x11_socket_path("/run/x/X1.2"), Some(("/run/x/X1".into(), 2)));
        assert_eq!(x11_socket_path(":0"), None);
        assert_eq!(x11_socket_path("localhost:1.0"), None);
    }

    fn mons2() -> HashMap<i64, MonitorBox> {
        monitor_boxes(&two_outputs())
    }

    #[test]
    fn only_root_and_off_output_geometry_is_a_remap() {
        let m = mons2();
        assert!(is_remap((0.0, 0.0, 3840.0, 1080.0), &m), "the XWayland root (Forza)");
        assert!(is_remap((1800.0, 100.0, 480.0, 320.0), &m), "straddles two outputs");
        assert!(is_remap((3600.0, 900.0, 480.0, 320.0), &m), "partly off-screen");
        assert!(!is_remap((2000.0, 100.0, 640.0, 400.0), &m), "a resolution change inside the output");
        assert!(!is_remap((1920.0, 0.0, 1920.0, 1080.0), &m), "one whole output is not the root of two");
        let one = monitor_boxes(&serde_json::json!([{"id": 0, "x": 0, "y": 0, "width": 1392, "height": 1002, "scale": 1.0}]));
        assert!(is_remap((0.0, 0.0, 1392.0, 1002.0), &one), "single output: the root is the output");
        assert!(!is_remap((100.0, 100.0, 640.0, 400.0), &one));
    }

    fn watch(kind: GameKind, ok: (f64, f64, f64, f64), pin: bool) -> GameWatch {
        GameWatch {
            kind, ok: Some(ok), workspace: Some(2),
            target: pin.then_some((ok.2, ok.3)), declared: pin.then_some((ok.2, ok.3)),
            refused: None, since: None, corrections: 0,
        }
    }

    fn at(c: &mut Json, g: (i64, i64, i64, i64)) {
        c["at"] = serde_json::json!([g.0, g.1]);
        c["size"] = serde_json::json!([g.2, g.3]);
    }

    #[test]
    fn a_fixed_game_changes_its_own_resolution() {
        // Defect: every ConfigureRequest was suppressed, so a game applying 640x400 from its menu stayed 480x320 and
        // was cropped. Now the request is honoured and the pin follows the new declaration.
        let (m, s, t0) = (mons2(), Duration::from_millis(220), Instant::now());
        let mut w = watch(GameKind::Fixed, (2640.0, 400.0, 480.0, 320.0), true);
        let mut c = game("steam_app_4201", (2640, 400), (480, 320), &["mios-place-game-fixed*"]);
        c["workspace"] = serde_json::json!({"id": 2, "name": "2"});
        assert_eq!(game_step(&mut w, &c, &m, Some((480.0, 320.0)), true, false, s, t0), GameStep::Nothing);
        at(&mut c, (2640, 400, 640, 400));
        let step = game_step(&mut w, &c, &m, Some((640.0, 400.0)), true, false, s, t0);
        assert!(matches!(&step, GameStep::Repin(l) if l.contains("value='640 400'")), "{step:?}");
        assert_eq!((w.target, w.ok), (Some((640.0, 400.0)), Some((2640.0, 400.0, 640.0, 400.0))));
        // Wine order: the ConfigureNotify first, the new hints right after. Within settle nothing is restored.
        let mut w = watch(GameKind::Fixed, (2640.0, 400.0, 480.0, 320.0), true);
        assert_eq!(game_step(&mut w, &c, &m, Some((480.0, 320.0)), true, false, s, t0), GameStep::Nothing);
        let step = game_step(&mut w, &c, &m, Some((640.0, 400.0)), true, false, s, t0 + Duration::from_millis(100));
        assert!(matches!(step, GameStep::Repin(_)), "{step:?}");
    }

    #[test]
    fn a_fixed_game_grown_from_outside_is_restored_after_settle() {
        let (m, s, t0) = (mons2(), Duration::from_millis(220), Instant::now());
        let mut w = watch(GameKind::Fixed, (2640.0, 400.0, 480.0, 320.0), true);
        let mut c = game("steam_app_4106", (2640, 400), (480, 320), &["mios-place-game-fixed*"]);
        c["workspace"] = serde_json::json!({"id": 2, "name": "2"});
        at(&mut c, (2460, 260, 840, 600));
        assert_eq!(game_step(&mut w, &c, &m, Some((480.0, 320.0)), true, true, s, t0), GameStep::Nothing, "a drag is never fought");
        assert_eq!(game_step(&mut w, &c, &m, Some((480.0, 320.0)), true, false, s, t0), GameStep::Nothing, "settling");
        let step = game_step(&mut w, &c, &m, Some((480.0, 320.0)), true, false, s, t0 + s);
        assert!(matches!(&step, GameStep::Restore(l) if l.contains("resize({x=480, y=320") && l.contains("move({x=2640, y=400")), "{step:?}");
        // Unpinned games are never restored.
        let mut u = watch(GameKind::Fixed, (2640.0, 400.0, 480.0, 320.0), false);
        assert_eq!(game_step(&mut u, &c, &m, Some((480.0, 320.0)), true, false, s, t0 + s), GameStep::Nothing);
        assert_eq!(u.ok, Some((2460.0, 260.0, 840.0, 600.0)));
    }

    #[test]
    fn a_root_remap_is_refused_and_its_declaration_ignored() {
        let (m, s, t0) = (mons2(), Duration::from_millis(220), Instant::now());
        let mut w = watch(GameKind::Fixed, (2640.0, 400.0, 480.0, 320.0), true);
        let mut c = game("steam_app_2483190", (2640, 400), (480, 320), &["mios-place-game-fixed*"]);
        at(&mut c, (0, 0, 3840, 1080));
        c["workspace"] = serde_json::json!({"id": 1, "name": "1"});
        let step = game_step(&mut w, &c, &m, Some((3840.0, 1080.0)), true, false, s, t0);
        assert!(matches!(&step, GameStep::Refuse(l) if l.contains("workspace='2'") && l.contains("resize({x=480, y=320") && l.contains("move({x=2640, y=400")), "{step:?}");
        assert_eq!(w.refused, Some((3840.0, 1080.0)));
        // Back at the accepted geometry with the refused hints still set: nothing to follow.
        at(&mut c, (2640, 400, 480, 320));
        assert_eq!(game_step(&mut w, &c, &m, Some((3840.0, 1080.0)), true, false, s, t0), GameStep::Nothing);
        // suppress_client_geometry = false: the remap stands.
        let mut off = watch(GameKind::Resizable, (2400.0, 200.0, 960.0, 692.0), false);
        at(&mut c, (0, 0, 3840, 1080));
        assert_eq!(game_step(&mut off, &c, &m, None, false, false, s, t0), GameStep::Nothing);
        assert_eq!(off.ok, Some((0.0, 0.0, 3840.0, 1080.0)));
        // A resizable game changing its own size inside the output is accepted; a remap is refused.
        let mut r = watch(GameKind::Resizable, (2400.0, 200.0, 960.0, 692.0), false);
        at(&mut c, (2400, 200, 1280, 720));
        assert_eq!(game_step(&mut r, &c, &m, None, true, false, s, t0), GameStep::Nothing);
        assert_eq!(r.ok, Some((2400.0, 200.0, 1280.0, 720.0)));
        at(&mut c, (0, 0, 3840, 1080));
        assert!(matches!(game_step(&mut r, &c, &m, None, true, false, s, t0), GameStep::Refuse(_)));
    }

    #[test]
    fn corrections_are_bounded_and_fullscreen_is_the_games_own() {
        let (m, s, t0) = (mons2(), Duration::from_millis(220), Instant::now());
        let mut w = watch(GameKind::Fixed, (2640.0, 400.0, 480.0, 320.0), true);
        let mut c = game("steam_app_7", (0, 0), (3840, 1080), &["mios-place-game-fixed*"]);
        for _ in 0..GAME_CORRECTIONS {
            assert!(matches!(game_step(&mut w, &c, &m, Some((3840.0, 1080.0)), true, false, s, t0), GameStep::Refuse(_)));
        }
        assert_eq!(game_step(&mut w, &c, &m, Some((3840.0, 1080.0)), true, false, s, t0), GameStep::GiveUp);
        let mut w = watch(GameKind::Fixed, (2640.0, 400.0, 480.0, 320.0), true);
        c["fullscreen"] = serde_json::json!(2);
        assert_eq!(game_step(&mut w, &c, &m, Some((3840.0, 1080.0)), true, false, s, t0), GameStep::Nothing);
        assert_eq!(w.ok, None, "fullscreen: nothing accepted or refused");
        c["fullscreen"] = serde_json::json!(0);
        at(&mut c, (2640, 400, 480, 320));
        assert_eq!(game_step(&mut w, &c, &m, Some((480.0, 320.0)), true, false, s, t0), GameStep::Nothing);
        assert_eq!(w.ok, Some((2640.0, 400.0, 480.0, 320.0)), "taken as-is when it returns");
    }

    #[test]
    fn pinned_fixed_game_is_restored_around_its_centre() {
        let mut w = game("steam_app_4106", (220, 86), (840, 600), &["mios-place-game-fixed*"]);
        let lua = pin_restore(&w, (480.0, 320.0)).expect("grown: restore");
        assert!(lua.contains("resize({x=480, y=320") && lua.contains("move({x=400, y=226"), "{lua}");
        // Negative controls: at its pinned size (within 2 px) or fullscreen, nothing is dispatched.
        w["size"] = serde_json::json!([481, 320]);
        assert_eq!(pin_restore(&w, (480.0, 320.0)), None);
        w["size"] = serde_json::json!([1920, 1080]);
        w["fullscreen"] = serde_json::json!(2);
        assert_eq!(pin_restore(&w, (480.0, 320.0)), None);
    }

    #[test]
    fn without_the_games_table_a_game_is_left_where_it_asked() {
        // Negative control: the previous configuration has no game tags, so on_open falls through to the size gate
        // ("floated at 3840x1080, not the placement size; left alone" in the recorded journal).
        let w = game("steam_app_2483190", (0, 0), (3840, 1080), &["mios-place-game-fixed*"]);
        let x = X11Props { fixed: Some((3840.0, 1080.0)), dialog: false };
        assert_eq!(game_kind(&cfg(), &w, Some(&x)), None);
        assert!(cfg().games.is_none());
        let other = game("Alacritty", (0, 0), (640, 480), &["mios-place-new*"]);
        assert_eq!(game_kind(&cfg_games("fit"), &other, Some(&x)), None, "ordinary windows keep the general path");
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
