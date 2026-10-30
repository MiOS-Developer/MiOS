// AI-hint: miosd shaderglass: ShaderGlass (mausimus, GPL-3.0) under Wine as a Hyprglass-decorated overlay pane pinned over one target window -- install the newest verified release, one supervisor user unit running every pane, follow the target, setup mode, the xdph custom picker and portal restore tokens.
// AI-related: usr/share/mios/mios.toml [desktop.shaderglass] [desktop.hyprland.xdph], tools/native/mios-gen/src/render.rs (shaderglass_lua), usr/bin/mios-shaderglass, usr/libexec/mios/mios-share-picker, src/mios-rs/miosd/src/placement.rs
//! `miosd shaderglass` (glue: /usr/bin/mios-shaderglass):
//!
//! - `install` resolves the newest release of [desktop.shaderglass].channel, accepts the zip only against
//!   GitHub's sha256 digest, builds WineCap from the same tag with the MiOS patches and installs the tree
//!   with INSTALLED.json (release.rs). Nothing is pinned; the record is the only place a version lives.
//! - `attach` (the keybind's `toggle`) creates the Wine prefix once, makes sure the supervisor runs (a
//!   transient user unit `app-mios-<app_id>@overlay.service`, so every pane, Wine and wineserver share one
//!   cgroup, one MemoryMax and one journal) and asks it for a pane over the target.
//! - The supervisor (`serve`) launches ShaderGlass one pane at a time: it writes the restore token it has
//!   for the target and a one-shot request the MiOS picker answers with the target window, so the portal
//!   asks nobody. The pane maps under the projected rules (float + pin, no focus steal, no_anim, glass
//!   preset tag); the supervisor tags it passthrough (no_focus: input falls to the target), then keeps it
//!   on the target from the event socket plus polling (follow.rs), hides it while the target is off
//!   screen, closes it with the target and saves the restore token WineCap wrote.
//! - `pick` is xdg-desktop-portal-hyprland's custom picker; `portal-config` renders xdph.conf.
//!
//! Never dispatches on an unmapped window, never registers compositor hooks.

use std::collections::VecDeque;
use std::io::{BufRead, BufReader, Write};
use std::os::unix::net::{UnixListener, UnixStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc;
use std::time::{Duration, Instant};

use regex::Regex;
use serde_json::{json, Value as Json};
use toml::Value;

use crate::placement::{addr, lua_str, Buttons, Hypr};

mod follow;
mod picker;
mod release;
mod state;
mod wine;
mod zip;

use follow::{Follower, Mon, OnMove, Policy, Win};
use state::{Attached, StateCfg, Store};

#[derive(clap::Subcommand, Debug)]
pub enum ShaderglassCommand {
    /// Install or update ShaderGlass from its newest verified release (run as the desktop user)
    #[command(alias = "update")]
    Install {
        /// newest | stable (default: [desktop.shaderglass].channel)
        #[arg(long)]
        channel: Option<String>,
        /// A GitHub /releases JSON document to resolve from instead of the API
        #[arg(long)]
        releases_json: Option<PathBuf>,
        /// The release zip, already downloaded (still checked against GitHub's digest)
        #[arg(long)]
        archive: Option<PathBuf>,
        /// A WineCap.dll built elsewhere instead of building one
        #[arg(long)]
        winecap: Option<PathBuf>,
        /// Install into this directory instead of [desktop.shaderglass].install_dir
        #[arg(long)]
        dest: Option<PathBuf>,
        /// Install a tree an earlier run staged (the root step: no network, no builder)
        #[arg(long, conflicts_with_all = ["channel", "releases_json", "archive", "winecap", "stage_only", "dry_run"])]
        from_stage: Option<PathBuf>,
        /// Stop after staging
        #[arg(long)]
        stage_only: bool,
        /// Resolve and print the plan only
        #[arg(long)]
        dry_run: bool,
        /// Reinstall even when INSTALLED.json records this release already
        #[arg(long)]
        force: bool,
    },
    /// Print the release `install` would take, as JSON (no download)
    Resolve {
        #[arg(long)]
        channel: Option<String>,
        #[arg(long)]
        releases_json: Option<PathBuf>,
    },
    /// Overlay a window (the active one by default) with a ShaderGlass pane
    #[command(alias = "start")]
    Attach {
        /// Hyprland window address (0x...)
        #[arg(long)]
        window: Option<String>,
        /// Start the pane in setup mode (focusable, menu shown)
        #[arg(long)]
        setup: bool,
    },
    /// Remove the pane from a window (the active window or pane by default)
    Detach {
        #[arg(long)]
        window: Option<String>,
    },
    /// Attach to the active window, or detach when it already has a pane or is one
    Toggle,
    /// Switch a pane between overlay (click-through) and setup (focusable, ShaderGlass menu shown)
    Setup {
        #[arg(long)]
        window: Option<String>,
    },
    /// Next ShaderGlass shader on a pane (the active window or pane by default); the choice is saved for the class
    #[command(alias = "next")]
    Cycle {
        #[arg(long)]
        window: Option<String>,
    },
    /// Save a pane's live ShaderGlass state as its class profile now (it is also saved on setup exit, cycle and close)
    Save {
        #[arg(long)]
        window: Option<String>,
    },
    /// Start the supervisor and re-attach the panes a supervisor that died left attached
    Resume,
    /// Close every pane and stop the supervisor
    Stop,
    /// The installed release and the running panes, as JSON
    Status,
    /// The supervisor (started by `attach` as a transient user unit)
    Serve,
    /// xdg-desktop-portal-hyprland custom picker ([desktop.hyprland.xdph].custom_picker_binary)
    Pick {
        #[arg(trailing_var_arg = true, allow_hyphen_values = true)]
        args: Vec<String>,
    },
    /// Render $XDG_CONFIG_HOME/<config> from [desktop.hyprland.xdph]
    PortalConfig {
        #[arg(long)]
        check: bool,
        #[arg(long)]
        write: bool,
    },
    /// Create the Wine prefix (headless) and seed the default profile
    Prefix,
}

// ---------------------------------------------------------------- SSOT

#[derive(Clone, Debug, PartialEq)]
pub enum Channel {
    Newest,
    Stable,
}

impl Channel {
    fn parse(s: &str) -> Result<Self, String> {
        match s {
            "newest" => Ok(Channel::Newest),
            "stable" => Ok(Channel::Stable),
            _ => Err(format!("channel {s:?} must be newest or stable")),
        }
    }
    pub fn as_str(&self) -> &'static str {
        match self {
            Channel::Newest => "newest",
            Channel::Stable => "stable",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum WinecapSource {
    Build,
    Release,
    Auto,
}

impl WinecapSource {
    pub fn as_str(&self) -> &'static str {
        match self {
            WinecapSource::Build => "build",
            WinecapSource::Release => "release",
            WinecapSource::Auto => "auto",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub enum Driver {
    X11,
    Wayland,
}

#[derive(Clone, Debug, PartialEq)]
pub enum D3d {
    Dxvk,
    Wined3d,
}

/// Which GPU DXVK renders on.
#[derive(Clone, Debug, PartialEq)]
pub enum Gpu {
    /// The vendor of the compositor's primary DRM device (DXVK_FILTER_DEVICE_NAME, derived at run time).
    Compositor,
    /// DXVK's own choice.
    Any,
}

#[derive(Clone, Debug, PartialEq)]
pub enum PickerMode {
    Mios,
    Stock,
}

#[derive(Clone, Debug)]
pub struct WineCfg {
    pub binary: String,
    pub wineboot: String,
    pub prefix: String,
    pub driver: Driver,
    pub d3d: D3d,
    pub gpu: Gpu,
    pub debug: String,
    pub dll_overrides: String,
}

#[derive(Clone, Debug)]
pub struct WindowCfg {
    pub class: Regex,
    pub title: Regex,
    /// The main window once ShaderGlass started (its dialogs and message boxes keep the bare title):
    /// the only window the supervisor adopts as a pane.
    pub main_title: Regex,
    pub tools: Regex,
    pub pane_tag: String,
    pub passthrough_tag: String,
}

#[derive(Clone, Debug)]
pub struct CaptureCfg {
    pub source_types: String,
    pub cursor: String,
    pub picker: PickerMode,
    pub request_ttl: Duration,
    pub restore_tokens: bool,
}

#[derive(Clone, Debug)]
pub struct BuilderCfg {
    pub image: String,
    pub containerfile_dir: PathBuf,
    pub base_image: String,
    pub packages: Vec<String>,
    pub work_root: PathBuf,
    pub heavy_lock: String,
    pub scope_memory_max: String,
    pub container_memory: String,
    pub container_cpus: u32,
}

#[derive(Clone, Debug)]
pub struct WinecapCfg {
    pub source: WinecapSource,
    pub patches: Vec<String>,
    pub builder: BuilderCfg,
}

#[derive(Clone, Debug)]
pub struct XdphCfg {
    pub config: String,
    pub allow_token_by_default: bool,
    pub custom_picker_binary: String,
    pub stock_picker: String,
}

#[derive(Clone, Debug)]
pub struct Config {
    pub enabled: bool,
    pub source_repository: String,
    pub github_api: String,
    pub channel: Channel,
    pub asset_pattern: Regex,
    pub install_dir: PathBuf,
    pub exe: String,
    pub install_files: Vec<String>,
    pub license_markers: Vec<String>,
    pub app_id: String,
    pub memory_max: String,
    pub idle_exit: Duration,
    pub map_timeout: Duration,
    pub wine: WineCfg,
    pub launch_args: Vec<String>,
    pub setup_shortcut: String,
    pub cycle_shortcut: String,
    pub profile: Vec<(String, String)>,
    pub window: WindowCfg,
    pub follow: Policy,
    pub hide_in_hypr3d: bool,
    pub capture: CaptureCfg,
    pub winecap: WinecapCfg,
    pub xdph: XdphCfg,
    pub state: StateCfg,
}

fn get<'a>(v: &'a Value, path: &str) -> Option<&'a Value> {
    path.split('.').try_fold(v, |cur, key| cur.get(key))
}

struct Section<'a> {
    v: &'a Value,
    name: String,
}

impl<'a> Section<'a> {
    fn of(doc: &'a Value, path: &str) -> Result<Self, String> {
        get(doc, path)
            .filter(|v| v.is_table())
            .map(|v| Section { v, name: path.to_owned() })
            .ok_or_else(|| format!("missing [{path}]"))
    }
    fn sub(&self, key: &str) -> Result<Section<'a>, String> {
        self.v
            .get(key)
            .filter(|v| v.is_table())
            .map(|v| Section { v, name: format!("{}.{key}", self.name) })
            .ok_or_else(|| format!("missing [{}.{key}]", self.name))
    }
    fn err(&self, key: &str, what: &str) -> String {
        format!("{}.{key} must be {what}", self.name)
    }
    fn any_str(&self, key: &str) -> Result<String, String> {
        self.v.get(key).and_then(Value::as_str).map(str::to_owned).ok_or_else(|| self.err(key, "a string"))
    }
    fn str(&self, key: &str) -> Result<String, String> {
        self.any_str(key).and_then(|s| if s.is_empty() { Err(self.err(key, "a nonempty string")) } else { Ok(s) })
    }
    fn word(&self, key: &str) -> Result<String, String> {
        self.str(key).and_then(|s| {
            if s.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_') {
                Ok(s)
            } else {
                Err(self.err(key, "a [A-Za-z0-9_-] word"))
            }
        })
    }
    fn path(&self, key: &str) -> Result<String, String> {
        self.str(key).and_then(|s| {
            if s.starts_with('/') && !s.contains("..") && !s.chars().any(char::is_control) {
                Ok(s)
            } else {
                Err(self.err(key, "an absolute path"))
            }
        })
    }
    fn boolean(&self, key: &str) -> Result<bool, String> {
        self.v.get(key).and_then(Value::as_bool).ok_or_else(|| self.err(key, "a boolean"))
    }
    fn num(&self, key: &str) -> Result<f64, String> {
        match self.v.get(key) {
            Some(Value::Integer(i)) if *i >= 0 => Ok(*i as f64),
            Some(Value::Float(f)) if f.is_finite() && *f >= 0.0 => Ok(*f),
            _ => Err(self.err(key, "a number >= 0")),
        }
    }
    fn millis(&self, key: &str, min: u64) -> Result<Duration, String> {
        let n = self.num(key)?;
        if n < min as f64 {
            return Err(self.err(key, &format!(">= {min}")));
        }
        Ok(Duration::from_millis(n as u64))
    }
    fn list(&self, key: &str) -> Result<Vec<String>, String> {
        self.v
            .get(key)
            .and_then(Value::as_array)
            .and_then(|a| a.iter().map(|x| x.as_str().map(str::to_owned)).collect::<Option<Vec<_>>>())
            .ok_or_else(|| self.err(key, "a list of strings"))
    }
    fn regex(&self, key: &str) -> Result<Regex, String> {
        let p = self.str(key)?;
        if !(p.starts_with('^') && p.ends_with('$')) {
            return Err(self.err(key, "an anchored (^...$) pattern"));
        }
        Regex::new(&p).map_err(|e| format!("{}.{key}: {e}", self.name))
    }
}

fn size_value(s: &str) -> bool {
    let digits = s.trim_end_matches(|c: char| "KMGTkmgt".contains(c));
    !digits.is_empty() && digits.bytes().all(|c| c.is_ascii_digit()) && s.len() - digits.len() <= 1
}

impl Config {
    pub fn from_ssot(doc: &Value) -> Result<Self, String> {
        let t = Section::of(doc, "desktop.shaderglass")?;
        let w = t.sub("wine")?;
        let l = t.sub("launch")?;
        let win = t.sub("window")?;
        let f = t.sub("follow")?;
        let c = t.sub("capture")?;
        let wc = t.sub("winecap")?;
        let b = wc.sub("builder")?;
        let st = t.sub("state")?;
        let x = Section::of(doc, "desktop.hyprland.xdph")?;
        let app_id = t.str("app_id")?;
        if !app_id.contains('.') || !app_id.bytes().all(|ch| ch.is_ascii_alphanumeric() || ch == b'.' || ch == b'_') {
            return Err("desktop.shaderglass.app_id must be a reverse-DNS application id without dashes".into());
        }
        let memory_max = t.str("memory_max")?;
        if !size_value(&memory_max) {
            return Err("desktop.shaderglass.memory_max must be a size like 4G".into());
        }
        let exe = t.str("exe")?;
        let install_files = t.list("install_files")?;
        if !install_files.contains(&exe) || !install_files.iter().any(|n| n == "LICENSE") {
            return Err("desktop.shaderglass.install_files must list exe and LICENSE".into());
        }
        if install_files.iter().any(|n| n.contains('/') || n.starts_with('.') || n == "WineCap.dll" || n == "INSTALLED.json") {
            return Err("desktop.shaderglass.install_files are plain root names (WineCap.dll and INSTALLED.json are written by install)".into());
        }
        let profile = f_profile(&t.sub("profile")?)?;
        let enum_of = |s: &Section, key: &str, allowed: &[&str]| -> Result<String, String> {
            let v = s.str(key)?;
            if allowed.contains(&v.as_str()) {
                Ok(v)
            } else {
                Err(s.err(key, &format!("one of {allowed:?}")))
            }
        };
        let cfg = Config {
            enabled: t.boolean("enabled")?,
            source_repository: t.str("source_repository")?,
            github_api: t.str("github_api")?,
            channel: Channel::parse(&t.str("channel")?)?,
            asset_pattern: t.regex("asset_pattern")?,
            install_dir: PathBuf::from(t.path("install_dir")?),
            exe,
            install_files,
            license_markers: t.list("license_markers")?,
            app_id,
            memory_max,
            idle_exit: Duration::from_secs(t.num("idle_exit_s")? as u64),
            map_timeout: Duration::from_secs(t.num("map_timeout_s")?.max(5.0) as u64),
            wine: WineCfg {
                binary: w.path("binary")?,
                wineboot: w.path("wineboot")?,
                prefix: {
                    let p = w.str("prefix")?;
                    if p.starts_with('/') || p.split('/').any(|c| c.is_empty() || c == "..") {
                        return Err("desktop.shaderglass.wine.prefix must be a relative path under $XDG_DATA_HOME".into());
                    }
                    p
                },
                driver: match enum_of(&w, "driver", &["x11", "wayland"])?.as_str() {
                    "x11" => Driver::X11,
                    _ => Driver::Wayland,
                },
                d3d: match enum_of(&w, "d3d", &["dxvk", "wined3d"])?.as_str() {
                    "dxvk" => D3d::Dxvk,
                    _ => D3d::Wined3d,
                },
                gpu: match enum_of(&w, "gpu", &["compositor", "any"])?.as_str() {
                    "compositor" => Gpu::Compositor,
                    _ => Gpu::Any,
                },
                debug: w.any_str("debug")?,
                dll_overrides: w.any_str("dll_overrides")?,
            },
            launch_args: l.list("args")?,
            setup_shortcut: l.str("setup_shortcut")?,
            cycle_shortcut: l.str("cycle_shortcut")?,
            profile,
            window: WindowCfg {
                class: win.regex("class")?,
                title: win.regex("title")?,
                main_title: win.regex("main_title")?,
                tools: win.regex("tools_title")?,
                pane_tag: win.word("pane_tag")?,
                passthrough_tag: win.word("passthrough_tag")?,
            },
            follow: Policy {
                follow_hz: f.num("follow_hz")?,
                idle: f.millis("idle_poll_ms", 10)?,
                settle: f.millis("settle_ms", 0)?,
                on_move: match enum_of(&f, "on_move", &["follow", "hide"])?.as_str() {
                    "follow" => OnMove::Follow,
                    _ => OnMove::Hide,
                },
                close_with_target: f.boolean("close_with_target")?,
            },
            hide_in_hypr3d: f.boolean("hide_in_hypr3d")?,
            capture: CaptureCfg {
                source_types: {
                    let v = c.str("source_types")?;
                    if v.split(',').any(|s| !["monitor", "window", "virtual"].contains(&s.trim())) {
                        return Err("desktop.shaderglass.capture.source_types is a comma list of monitor,window,virtual".into());
                    }
                    v
                },
                cursor: enum_of(&c, "cursor", &["hidden", "embedded", "metadata"])?,
                picker: match enum_of(&c, "picker", &["mios", "stock"])?.as_str() {
                    "mios" => PickerMode::Mios,
                    _ => PickerMode::Stock,
                },
                request_ttl: c.millis("request_ttl_ms", 1000)?,
                restore_tokens: c.boolean("restore_tokens")?,
            },
            winecap: WinecapCfg {
                source: match enum_of(&wc, "source", &["build", "release", "auto"])?.as_str() {
                    "build" => WinecapSource::Build,
                    "release" => WinecapSource::Release,
                    _ => WinecapSource::Auto,
                },
                patches: {
                    let p = wc.list("patches")?;
                    if p.iter().any(|x| x.starts_with('/') || x.contains("..")) {
                        return Err("desktop.shaderglass.winecap.patches are repository-relative paths".into());
                    }
                    p
                },
                builder: BuilderCfg {
                    image: b.str("image")?,
                    containerfile_dir: PathBuf::from(b.path("containerfile_dir")?),
                    base_image: b.str("base_image")?,
                    packages: b.list("packages")?,
                    work_root: PathBuf::from(b.path("work_root")?),
                    heavy_lock: b.path("heavy_lock")?,
                    scope_memory_max: {
                        let v = b.str("scope_memory_max")?;
                        if !size_value(&v) {
                            return Err(b.err("scope_memory_max", "a size like 4G"));
                        }
                        v
                    },
                    container_memory: b.str("container_memory")?,
                    container_cpus: (b.num("container_cpus")? as u32).max(1),
                },
            },
            xdph: XdphCfg {
                config: {
                    let p = x.str("config")?;
                    if p.starts_with('/') || p.split('/').any(|s| s.is_empty() || s == "..") {
                        return Err("desktop.hyprland.xdph.config must be relative to $XDG_CONFIG_HOME".into());
                    }
                    p
                },
                allow_token_by_default: x.boolean("allow_token_by_default")?,
                custom_picker_binary: x.any_str("custom_picker_binary")?,
                stock_picker: x.str("stock_picker")?,
            },
            state: StateCfg {
                persist: st.boolean("persist")?,
                directory: {
                    let p = st.str("directory")?;
                    if p.starts_with('/') || p.split('/').any(|c| c.is_empty() || c == "..") {
                        return Err("desktop.shaderglass.state.directory must be a relative path under $XDG_STATE_HOME".into());
                    }
                    p
                },
                save_timeout: st.millis("save_timeout_ms", 100)?,
                restart_limit: {
                    let n = st.num("restart_limit")?;
                    if n > 10.0 || n.fract() != 0.0 {
                        return Err(st.err("restart_limit", "an integer 0..10"));
                    }
                    n as u32
                },
                resume: st.boolean("resume")?,
                strip_keys: {
                    let k = st.list("strip_keys")?;
                    if k.iter().any(|x| x.is_empty() || !x.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')) {
                        return Err("desktop.shaderglass.state.strip_keys are ShaderGlass profile mnemonics".into());
                    }
                    for need in ["CaptureWindow", "CaptureDesktop", "CaptureDevice", "CaptureFormat"] {
                        if !k.iter().any(|x| x == need) {
                            return Err(format!("desktop.shaderglass.state.strip_keys must strip {need} (miosd chooses the capture source)"));
                        }
                    }
                    k
                },
            },
        };
        if cfg.cycle_shortcut == cfg.setup_shortcut {
            return Err("desktop.shaderglass.launch cycle_shortcut and setup_shortcut must differ".into());
        }
        if cfg.window.pane_tag == cfg.window.passthrough_tag {
            return Err("desktop.shaderglass.window pane_tag and passthrough_tag must differ".into());
        }
        if cfg.capture.restore_tokens && cfg.winecap.source == WinecapSource::Release {
            eprintln!("[miosd shaderglass] capture.restore_tokens needs the patched WineCap (winecap.source = build): tokens stay unused");
        }
        Ok(cfg)
    }

    pub fn load() -> Result<Self, String> {
        let doc = mios_resolver::resolve_merged(Some(&mios_root()), false).map_err(|e| e.to_string())?;
        Self::from_ssot(&doc)
    }
}

fn f_profile(p: &Section) -> Result<Vec<(String, String)>, String> {
    let table = p.v.as_table().ok_or("desktop.shaderglass.profile must be a table")?;
    let mut out = Vec::new();
    for (k, v) in table {
        if !k.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_') {
            return Err(format!("desktop.shaderglass.profile key {k:?} is not a ShaderGlass profile mnemonic"));
        }
        if k == "CaptureWindow" {
            return Err("desktop.shaderglass.profile must not name a CaptureWindow (miosd chooses the target)".into());
        }
        let v = v.as_str().filter(|s| !s.chars().any(char::is_control)).ok_or(format!("desktop.shaderglass.profile.{k} must be a string"))?;
        out.push((k.clone(), v.to_owned()));
    }
    // ProfileVersion first, as ShaderGlass writes it.
    out.sort_by_key(|(k, _)| (k != "ProfileVersion", k.clone()));
    Ok(out)
}

// ---------------------------------------------------------------- paths and files

pub(crate) fn home() -> PathBuf {
    std::env::var_os("HOME").map(PathBuf::from).filter(|p| p.is_absolute()).unwrap_or_else(|| PathBuf::from("/"))
}

pub(crate) fn cache_home() -> PathBuf {
    std::env::var_os("XDG_CACHE_HOME").map(PathBuf::from).filter(|p| p.is_absolute()).unwrap_or_else(|| home().join(".cache"))
}

fn state_home() -> PathBuf {
    std::env::var_os("XDG_STATE_HOME").map(PathBuf::from).filter(|p| p.is_absolute()).unwrap_or_else(|| home().join(".local/state"))
}

fn config_home() -> PathBuf {
    std::env::var_os("XDG_CONFIG_HOME").map(PathBuf::from).filter(|p| p.is_absolute()).unwrap_or_else(|| home().join(".config"))
}

pub(crate) fn mios_root() -> PathBuf {
    std::env::var_os("MIOS_ROOT").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/"))
}

/// Create `dir` (and its parents) and make `dir` itself 0700.
pub(crate) fn ensure_private_dir(dir: &Path) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;
    std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    std::fs::set_permissions(dir, std::fs::Permissions::from_mode(0o700)).map_err(|e| format!("{}: {e}", dir.display()))
}

/// Write `path` atomically, mode 0600.
pub(crate) fn write_private(path: &Path, data: &[u8]) -> Result<(), String> {
    write_atomic(path, data, 0o600)
}

fn write_atomic(path: &Path, data: &[u8], mode: u32) -> Result<(), String> {
    use std::os::unix::fs::OpenOptionsExt;
    let dir = path.parent().ok_or_else(|| format!("{}: no parent", path.display()))?;
    let tmp = dir.join(format!(
        ".{}.tmp-{}",
        path.file_name().and_then(|n| n.to_str()).unwrap_or("f"),
        std::process::id()
    ));
    let mut f = std::fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(mode)
        .open(&tmp)
        .map_err(|e| format!("{}: {e}", tmp.display()))?;
    f.write_all(data).and_then(|_| f.sync_all()).map_err(|e| format!("{}: {e}", tmp.display()))?;
    {
        use std::os::unix::fs::PermissionsExt;
        std::fs::set_permissions(&tmp, std::fs::Permissions::from_mode(mode)).map_err(|e| e.to_string())?;
    }
    std::fs::rename(&tmp, path).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        format!("{}: {e}", path.display())
    })
}

pub(crate) fn runtime_root() -> Result<PathBuf, String> {
    let dir = crate::placement::runtime_dir().join("mios/shaderglass");
    ensure_private_dir(&dir)?;
    Ok(dir)
}

/// The 2D/3D hand-over files: /tmp/<name> for the primary session (the Hypr3D tools read them there); a
/// nested session keeps its own under its private runtime directory, so it never rewrites the primary's.
fn session_file(name: &str) -> PathBuf {
    if primary_session() {
        PathBuf::from("/tmp").join(name)
    } else {
        runtime_root().map(|d| d.join(name)).unwrap_or_else(|_| crate::placement::runtime_dir().join(name))
    }
}

fn control_socket() -> Result<PathBuf, String> {
    Ok(runtime_root()?.join("control.sock"))
}

fn token_store_path() -> PathBuf {
    state_home().join("mios/shaderglass/restore-tokens.json")
}

/// $XDG_STATE_HOME/<[desktop.shaderglass.state].directory>
pub(crate) fn state_dir(cfg: &Config) -> PathBuf {
    state_home().join(&cfg.state.directory)
}

/// ShaderGlass' own default profile in the prefix (%LOCALAPPDATA%\ShaderGlassDefault.sgp).
fn default_profile_path(cfg: &Config) -> Option<PathBuf> {
    wine::local_appdata(&wine::prefix_dir(cfg)).map(|d| d.join("ShaderGlassDefault.sgp"))
}

/// Whether this process runs in the user manager's own session (XDG_RUNTIME_DIR = /run/user/<uid>), not in a
/// nested MiOS session (which has a private runtime directory and compositor of its own).
pub(crate) fn primary_session() -> bool {
    crate::placement::runtime_dir() == PathBuf::from(format!("/run/user/{}", unsafe { libc::getuid() }))
}

/// The instance of the supervisor unit: "overlay" in the primary session, "overlay-<hash of the runtime
/// directory>" in a nested one (each session runs its own supervisor against its own compositor).
pub fn unit_instance(primary: bool, runtime: &Path) -> String {
    if primary {
        return "overlay".into();
    }
    let mut h: u64 = 0xcbf29ce484222325;
    for b in runtime.as_os_str().as_encoded_bytes() {
        h ^= u64::from(*b);
        h = h.wrapping_mul(0x100000001b3);
    }
    format!("overlay-{:08x}", h as u32)
}

pub fn unit_name(cfg: &Config) -> String {
    format!("app-mios-{}@{}.service", cfg.app_id, unit_instance(primary_session(), &crate::placement::runtime_dir()))
}

/// The environment the supervisor unit carries over from the caller: the compositor, the SSOT tiers a test or
/// a nested session points at, the XDG homes, and -- outside the primary session -- the private runtime
/// directory (the user manager would otherwise hand the supervisor the primary's).
pub const SUPERVISOR_ENV: &[&str] = &[
    "WAYLAND_DISPLAY", "DISPLAY", "HYPRLAND_INSTANCE_SIGNATURE", "XDG_CURRENT_DESKTOP", "AQ_DRM_DEVICES", "MIOS_ROOT",
    "MIOS_VENDOR_TOML", "MIOS_HOST_TOML", "MIOS_USER_TOML", "MIOS_USER_TOML_D", "XDG_DATA_HOME", "XDG_STATE_HOME",
    "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "PIPEWIRE_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS",
];

fn installed(cfg: &Config) -> Option<Json> {
    std::fs::read(cfg.install_dir.join("INSTALLED.json")).ok().and_then(|b| serde_json::from_slice(&b).ok())
}

fn require_installed(cfg: &Config) -> Result<(), String> {
    if cfg.install_dir.join(&cfg.exe).is_file() && cfg.install_dir.join("WineCap.dll").is_file() {
        Ok(())
    } else {
        Err(format!(
            "ShaderGlass is not installed in {}: run `mios-shaderglass install`",
            cfg.install_dir.display()
        ))
    }
}

fn require_enabled(cfg: &Config) -> Result<(), String> {
    if cfg.enabled {
        Ok(())
    } else {
        Err("the ShaderGlass overlay is disabled ([desktop.shaderglass].enabled = false)".into())
    }
}

// ---------------------------------------------------------------- client side

fn control(req: &Json) -> Result<Json, String> {
    let path = control_socket()?;
    let mut s = UnixStream::connect(&path).map_err(|e| format!("the ShaderGlass supervisor is not running ({e})"))?;
    s.set_read_timeout(Some(Duration::from_secs(15))).map_err(|e| e.to_string())?;
    s.write_all(format!("{req}\n").as_bytes()).map_err(|e| e.to_string())?;
    let mut line = String::new();
    BufReader::new(s).read_line(&mut line).map_err(|e| format!("supervisor: {e}"))?;
    let v: Json = serde_json::from_str(&line).map_err(|e| format!("supervisor reply: {e}"))?;
    if v.get("ok") == Some(&Json::Bool(true)) {
        Ok(v)
    } else {
        Err(v.get("error").and_then(Json::as_str).unwrap_or("the supervisor refused").to_owned())
    }
}

fn supervisor_running() -> bool {
    control(&json!({"cmd": "ping"})).is_ok()
}

/// The arguments of the transient unit that runs the supervisor.
pub fn supervisor_unit_args(cfg: &Config, exe: &Path, env: &[(String, String)]) -> Vec<String> {
    let mut a: Vec<String> = [
        "--user", "--collect", "--quiet", "--unit",
    ]
    .iter()
    .map(|s| (*s).to_owned())
    .collect();
    a.push(unit_name(cfg));
    a.push("--description=MiOS ShaderGlass overlay panes".into());
    for p in [
        format!("MemoryMax={}", cfg.memory_max),
        "OOMPolicy=continue".into(),
        "KillMode=mixed".into(),
        "TimeoutStopSec=15".into(),
    ] {
        a.push("-p".into());
        a.push(p);
    }
    // A supervisor that crashed (a panic, an abort) comes back and resumes its panes ([desktop.shaderglass.state]
    // resume); systemd's start limit bounds it, and a stop, the idle exit or a clean exit is never restarted.
    if cfg.state.resume {
        for p in ["Restart=on-failure", "RestartSec=2", "StartLimitIntervalSec=120", "StartLimitBurst=3"] {
            a.push("-p".into());
            a.push(p.into());
        }
    }
    for (k, v) in env {
        a.push(format!("--setenv={k}={v}"));
    }
    a.push("--".into());
    a.push(exe.display().to_string());
    a.push("shaderglass".into());
    a.push("serve".into());
    a
}

fn start_supervisor(cfg: &Config) -> Result<(), String> {
    if supervisor_running() {
        return Ok(());
    }
    let exe = std::env::current_exe().map_err(|e| e.to_string())?;
    let mut env: Vec<(String, String)> = SUPERVISOR_ENV
        .iter()
        .filter_map(|k| std::env::var(k).ok().filter(|v| !v.is_empty()).map(|v| ((*k).to_owned(), v)))
        .collect();
    if !primary_session() {
        env.push(("XDG_RUNTIME_DIR".into(), crate::placement::runtime_dir().display().to_string()));
    }
    let st = Command::new("systemd-run")
        .args(supervisor_unit_args(cfg, &exe, &env))
        .stdin(Stdio::null())
        .status()
        .map_err(|e| format!("systemd-run: {e}"))?;
    if !st.success() {
        return Err(format!(
            "could not start {} ({st}); `mios-shaderglass stop` clears a stale one",
            unit_name(cfg)
        ));
    }
    let until = Instant::now() + Duration::from_secs(10);
    while Instant::now() < until {
        if supervisor_running() {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(100));
    }
    Err(format!("{} did not answer (journalctl --user -u {})", unit_name(cfg), unit_name(cfg)))
}

fn resolve_window(hypr: &Hypr, window: Option<&str>) -> Result<Json, String> {
    match window {
        Some(a) => {
            let a = addr(a);
            hypr.json("clients")?
                .as_array()
                .and_then(|v| v.iter().find(|c| c.get("address").and_then(Json::as_str) == Some(a.as_str())).cloned())
                .ok_or_else(|| format!("no window {a}"))
        }
        None => {
            let c = hypr.json("activewindow")?;
            if c.get("address").and_then(Json::as_str).is_some_and(|a| !a.is_empty()) {
                Ok(c)
            } else {
                Err("no active window".into())
            }
        }
    }
}

fn is_pane(cfg: &Config, c: &Json) -> bool {
    cfg.window.class.is_match(c.get("class").and_then(Json::as_str).unwrap_or(""))
}

/// The ShaderGlass main window: the pane class and the main-window title. Its message boxes and input
/// dialog share the class and the bare "ShaderGlass" title, so they are never adopted (nor made
/// click-through): only the window ShaderGlass retitled once it started.
pub fn is_main_window(w: &WindowCfg, class: &str, title: &str) -> bool {
    w.class.is_match(class) && w.main_title.is_match(title) && w.title.is_match(title) && !w.tools.is_match(title)
}

/// Which launching pane (index, process id) a new main window of process `pid` belongs to: the pane whose
/// process it is, else -- when that process runs in the supervisor's own unit (`ours`: Wine may hand the
/// window to another process it started) -- the only launching pane. A ShaderGlass the operator runs
/// outside the supervisor is never taken.
pub fn pane_for_window(waiting: &[(usize, i64)], pid: i64, ours: bool) -> Option<usize> {
    waiting
        .iter()
        .find(|(_, p)| *p == pid)
        .map(|(i, _)| *i)
        .or_else(|| if ours && waiting.len() == 1 { Some(waiting[0].0) } else { None })
}

/// Whether process `pid` runs in this process' cgroup (the supervisor unit holds only its own Wine processes).
fn in_my_cgroup(pid: i64) -> bool {
    let read = |p: &str| std::fs::read_to_string(p).ok().filter(|s| !s.trim().is_empty());
    pid > 0 && matches!((read("/proc/self/cgroup"), read(&format!("/proc/{pid}/cgroup"))), (Some(a), Some(b)) if a == b)
}

fn query_available_source_types() -> u32 {
    let parse = |out: std::process::Output| -> Option<u32> {
        if !out.status.success() {
            return None;
        }
        let text = String::from_utf8_lossy(&out.stdout);
        text.split_whitespace().filter_map(|w| w.parse::<u32>().ok()).last()
    };

    if let Ok(out) = Command::new("busctl")
        .args([
            "--user",
            "get-property",
            "org.freedesktop.portal.Desktop",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.portal.ScreenCast",
            "AvailableSourceTypes",
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
    {
        if let Some(val) = parse(out) {
            if val != 0 {
                return val;
            }
        }
    }

    if let Ok(out) = Command::new("busctl")
        .args([
            "--user",
            "get-property",
            "org.freedesktop.impl.portal.desktop.hyprland",
            "/org/freedesktop/portal/desktop",
            "org.freedesktop.impl.portal.ScreenCast",
            "AvailableSourceTypes",
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .output()
    {
        if let Some(val) = parse(out) {
            return val;
        }
    }

    0
}

/// Ensure xdg-desktop-portal-hyprland.service is running when inside a live Hyprland session,
/// so WineCap sees AvailableSourceTypes != 0 on org.freedesktop.portal.ScreenCast even if the
/// portal backend crashed earlier in the session.
fn ensure_portal_backend() {
    // A nested session's compositor is not the one the user manager's portal serves: never import its
    // environment into, or restart a service of, the operator's session.
    if !primary_session() {
        return;
    }
    let _sig = match std::env::var("HYPRLAND_INSTANCE_SIGNATURE").ok().filter(|s| !s.is_empty()) {
        Some(s) => s,
        None => match Hypr::connect() {
            Ok(hypr) => {
                if let Some(s) = hypr.dir().file_name().and_then(|n| n.to_str()) {
                    let sig_str = s.to_string();
                    std::env::set_var("HYPRLAND_INSTANCE_SIGNATURE", &sig_str);
                    sig_str
                } else {
                    return;
                }
            }
            Err(_) => return,
        },
    };

    if std::env::var("WAYLAND_DISPLAY").map_or(true, |s| s.is_empty()) {
        let rt = crate::placement::runtime_dir();
        if rt.join("wayland-1").exists() {
            std::env::set_var("WAYLAND_DISPLAY", "wayland-1");
        } else if rt.join("wayland-0").exists() {
            std::env::set_var("WAYLAND_DISPLAY", "wayland-0");
        }
    }
    if std::env::var("XDG_CURRENT_DESKTOP").map_or(true, |s| s.is_empty()) {
        std::env::set_var("XDG_CURRENT_DESKTOP", "Hyprland");
    }

    let _ = Command::new("systemctl")
        .args([
            "--user",
            "import-environment",
            "WAYLAND_DISPLAY",
            "HYPRLAND_INSTANCE_SIGNATURE",
            "XDG_CURRENT_DESKTOP",
        ])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status();

    let active = Command::new("systemctl")
        .args(["--user", "is-active", "--quiet", "xdg-desktop-portal-hyprland.service"])
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok_and(|s| s.success());

    let mut sources = if active { query_available_source_types() } else { 0 };

    if !active || sources == 0 {
        let _ = Command::new("systemctl")
            .args(["--user", "restart", "xdg-desktop-portal-hyprland.service"])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status();

        let deadline = Instant::now() + Duration::from_secs(5);
        while Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(100));
            sources = query_available_source_types();
            if sources != 0 {
                break;
            }
        }
    }
}

fn attach(cfg: &Config, window: Option<&str>, setup: bool) -> Result<String, String> {
    require_enabled(cfg)?;
    require_installed(cfg)?;
    ensure_portal_backend();
    let hypr = Hypr::connect()?;
    let t = resolve_window(&hypr, window)?;
    let target = t.get("address").and_then(Json::as_str).unwrap_or("").to_owned();
    if is_pane(cfg, &t) {
        return Err(format!("{target} is a ShaderGlass window: a pane never captures itself (detach it instead)"));
    }
    if t.get("mapped").and_then(Json::as_bool) != Some(true) {
        return Err(format!("{target} is not mapped"));
    }
    if wine::ensure_prefix(cfg)? {
        eprintln!("[miosd shaderglass] created the Wine prefix {}", wine::prefix_dir(cfg).display());
    }
    if let Some(p) = wine::seed_profile(cfg)? {
        eprintln!("[miosd shaderglass] seeded the default profile {}", p.display());
    }
    start_supervisor(cfg)?;
    Ok(control(&json!({"cmd": "attach", "target": target, "setup": setup}))?.to_string())
}

fn pane_or_target(cfg: &Config, window: Option<&str>) -> Result<Json, String> {
    let hypr = Hypr::connect()?;
    let w = resolve_window(&hypr, window)?;
    let a = w.get("address").and_then(Json::as_str).unwrap_or("").to_owned();
    Ok(if is_pane(cfg, &w) { json!({"pane": a}) } else { json!({"target": a}) })
}

fn with_cmd(mut sel: Json, cmd: &str) -> Json {
    if let Some(m) = sel.as_object_mut() {
        m.insert("cmd".into(), json!(cmd));
    }
    sel
}

fn detach(cfg: &Config, window: Option<&str>) -> Result<String, String> {
    if !supervisor_running() {
        return Ok("no ShaderGlass pane is running".into());
    }
    Ok(control(&with_cmd(pane_or_target(cfg, window)?, "detach"))?.to_string())
}

fn toggle(cfg: &Config) -> Result<String, String> {
    let sel = pane_or_target(cfg, None)?;
    if sel.get("pane").is_some() {
        return detach(cfg, sel["pane"].as_str());
    }
    let target = sel["target"].as_str().unwrap_or("").to_owned();
    if supervisor_running() {
        let st = control(&json!({"cmd": "status"}))?;
        let has = st["panes"].as_array().into_iter().flatten().chain(st["queued"].as_array().into_iter().flatten())
            .any(|p| p.get("target").and_then(Json::as_str) == Some(target.as_str()));
        if has {
            return Ok(control(&json!({"cmd": "detach", "target": target}))?.to_string());
        }
    }
    attach(cfg, Some(&target), false)
}

fn setup(cfg: &Config, window: Option<&str>) -> Result<String, String> {
    if !supervisor_running() {
        return Err("no ShaderGlass pane is running".into());
    }
    Ok(control(&with_cmd(pane_or_target(cfg, window)?, "setup"))?.to_string())
}

/// Next shader on a pane, saved for its class.
fn cycle(cfg: &Config, window: Option<&str>) -> Result<String, String> {
    if !supervisor_running() {
        return Err("no ShaderGlass pane is running".into());
    }
    Ok(control(&with_cmd(pane_or_target(cfg, window)?, "cycle"))?.to_string())
}

fn save(cfg: &Config, window: Option<&str>) -> Result<String, String> {
    if !supervisor_running() {
        return Err("no ShaderGlass pane is running".into());
    }
    Ok(control(&with_cmd(pane_or_target(cfg, window)?, "save"))?.to_string())
}

/// Start the supervisor (it resumes what a dead one left attached) and report.
fn resume(cfg: &Config) -> Result<String, String> {
    require_enabled(cfg)?;
    require_installed(cfg)?;
    if supervisor_running() {
        return Ok(json!({"resumed": false, "reason": "the supervisor is running"}).to_string());
    }
    let pending = Store::load(&state_dir(cfg)).attached();
    if pending.is_empty() {
        return Ok(json!({"resumed": false, "reason": "nothing was left attached"}).to_string());
    }
    ensure_portal_backend();
    wine::ensure_prefix(cfg)?;
    start_supervisor(cfg)?;
    Ok(json!({"resumed": true, "attached": pending.len(), "supervisor": control(&json!({"cmd": "status"})).ok()}).to_string())
}

fn stop(cfg: &Config) -> Result<String, String> {
    let _ = std::fs::write(session_file("mios-shaderglass.state"), "active=0\ntargets=\n");
    if supervisor_running() {
        control(&json!({"cmd": "stop"}))?;
        return Ok("stopping every ShaderGlass pane".into());
    }
    // Nothing to resume after an explicit stop.
    let mut store = Store::load(&state_dir(cfg));
    if !store.attached().is_empty() {
        store.set_attached(&[])?;
    }
    // A unit whose supervisor no longer answers.
    let _ = Command::new("systemctl").args(["--user", "stop", &unit_name(cfg)]).stderr(Stdio::null()).status();
    Ok("no ShaderGlass pane is running".into())
}

fn status(cfg: &Config) -> Json {
    json!({
        "enabled": cfg.enabled,
        "install_dir": cfg.install_dir,
        "installed": installed(cfg),
        "unit": unit_name(cfg),
        "supervisor": control(&json!({"cmd": "status"})).ok(),
        "prefix": wine::prefix_dir(cfg),
        "state": Store::load(&state_dir(cfg)).to_status(),
    })
}

// ---------------------------------------------------------------- picker and portal config

fn pick(cfg: Result<Config, String>, args: &[String]) -> Result<(), String> {
    use std::os::unix::process::CommandExt;
    let (probe, _allow) = picker::picker_args(args);
    if probe {
        println!("ok");
        return Ok(());
    }
    let stock = cfg.as_ref().map(|c| c.xdph.stock_picker.clone()).unwrap_or_else(|_| "hyprland-share-picker".into());
    if let Ok(cfg) = &cfg {
        if cfg.capture.picker == PickerMode::Mios {
            let windows = picker::parse_sharing_list(&std::env::var("XDPH_WINDOW_SHARING_LIST").unwrap_or_default());
            if let Ok(rt) = runtime_root() {
                if let Some(line) = picker::claim(&rt, &windows, cfg.capture.restore_tokens) {
                    println!("{line}");
                    return Ok(());
                }
            }
        }
    }
    let err = Command::new(&stock).args(args).exec();
    Err(format!("{stock}: {err}"))
}

fn portal_config(cfg: &Config, check: bool, write: bool) -> Result<String, String> {
    let path = config_home().join(&cfg.xdph.config);
    let text = picker::xdph_conf(cfg.xdph.allow_token_by_default, &cfg.xdph.custom_picker_binary)?;
    if check {
        let cur = std::fs::read_to_string(&path).unwrap_or_default();
        return if cur == text { Ok(format!("{} matches [desktop.hyprland.xdph]", path.display())) } else { Err(format!("{} differs from [desktop.hyprland.xdph] (portal-config --write)", path.display())) };
    }
    if write {
        if let Some(dir) = path.parent() {
            std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
        }
        write_atomic(&path, text.as_bytes(), 0o644)?;
        return Ok(format!(
            "wrote {} (xdg-desktop-portal-hyprland reads it when it starts: it applies at the next login or service restart)",
            path.display()
        ));
    }
    Ok(text)
}

// ---------------------------------------------------------------- supervisor

static STOP: AtomicBool = AtomicBool::new(false);
/// STOP came from a signal, not from `mios-shaderglass stop`.
static SIGNALLED: AtomicBool = AtomicBool::new(false);

extern "C" fn on_signal(_: libc::c_int) {
    SIGNALLED.store(true, Ordering::SeqCst);
    STOP.store(true, Ordering::SeqCst);
}

/// Whether a pane whose ShaderGlass exited with a failure is relaunched: only when nobody asked for the close
/// (a crash, an OOM kill, a killed Wine), the pane had mapped (it worked before), the relaunch budget is not
/// spent and the supervisor is not stopping. A clean exit (File > Exit) is the operator's choice.
fn relaunch_after_exit(p: &Pane, limit: u32, stopping: bool) -> bool {
    relaunch_decision(p.requested_close, p.pane_identity.is_some(), p.restarts, limit, stopping)
}

pub(crate) fn relaunch_decision(requested_close: bool, had_mapped: bool, restarts: u32, limit: u32, stopping: bool) -> bool {
    !requested_close && had_mapped && restarts < limit && !stopping
}

enum Msg {
    Line(String),
    Lost(String),
    Control(Json, mpsc::Sender<Json>),
}

fn spawn_events(tx: mpsc::Sender<Msg>) {
    std::thread::spawn(move || loop {
        match Hypr::connect().and_then(|h| h.events()) {
            Ok(s) => {
                for line in BufReader::new(s).lines() {
                    let Ok(l) = line else { break };
                    if tx.send(Msg::Line(l)).is_err() {
                        return;
                    }
                }
                if tx.send(Msg::Lost("event socket closed".into())).is_err() {
                    return;
                }
            }
            Err(e) => {
                if tx.send(Msg::Lost(e)).is_err() {
                    return;
                }
            }
        }
        std::thread::sleep(Duration::from_secs(1));
    });
}

fn spawn_control(listener: UnixListener, tx: mpsc::Sender<Msg>) {
    std::thread::spawn(move || {
        for stream in listener.incoming() {
            let Ok(s) = stream else { continue };
            let tx = tx.clone();
            std::thread::spawn(move || {
                let _ = s.set_read_timeout(Some(Duration::from_secs(5)));
                let mut line = String::new();
                let Ok(reader) = s.try_clone() else { return };
                if BufReader::new(reader).read_line(&mut line).is_err() {
                    return;
                }
                let req = serde_json::from_str::<Json>(&line).unwrap_or(Json::Null);
                let (rtx, rrx) = mpsc::channel();
                let reply = if tx.send(Msg::Control(req, rtx)).is_ok() {
                    rrx.recv_timeout(Duration::from_secs(12)).unwrap_or_else(|_| json!({"ok": false, "error": "supervisor busy"}))
                } else {
                    json!({"ok": false, "error": "supervisor stopping"})
                };
                let mut s = s;
                let _ = s.write_all(format!("{reply}\n").as_bytes());
            });
        }
    });
}

/// Native identity survives mode/workspace changes, unlike geometry or title.
/// Address reuse after a close must not transfer an overlay to a different app.
#[derive(Clone, Debug, PartialEq, Eq)]
struct WindowIdentity {
    address: String,
    pid: i64,
    stable_id: String,
}

impl WindowIdentity {
    fn from_window(window: &Win) -> Self {
        Self { address: window.address.clone(), pid: window.pid, stable_id: window.stable_id.clone() }
    }
    fn matches(&self, window: &Win) -> bool {
        self.address == window.address && self.pid == window.pid
            && (self.stable_id.is_empty() || self.stable_id == window.stable_id)
    }
}

/// Float and pin a pane the static rules missed. The rules match the title, and winewayland maps the window
/// before ShaderGlass titles it, so under the Wayland driver the pane mapped tiled: the layout then owned its
/// geometry, every placement was ignored and WineCap's client fit fought the tile forever. Empty when the
/// pane already floats and is pinned.
pub(crate) fn pane_layer_lua(address: &str, client: &Json) -> String {
    let w = lua_str(&format!("address:{address}"));
    let mut out = String::new();
    if client.get("floating").and_then(Json::as_bool) != Some(true) {
        out += &format!("; pcall(function() hl.dispatch(hl.dsp.window.float({{window={w},action='enable'}})) end)");
    }
    if client.get("pinned").and_then(Json::as_bool) != Some(true) {
        out += &format!("; pcall(function() hl.dispatch(hl.dsp.window.pin({{window={w},action='enable'}})) end)");
    }
    out
}

fn binding_lua(pane: &str, target: &str, on: bool, target_alive: bool) -> String {
    let sign = if on { "+" } else { "-" };
    let mut actions = vec![format!("hl.dispatch(hl.dsp.window.tag({{tag={},window={}}}))",
        lua_str(&format!("{sign}mios-shaderglass-target:{target}")), lua_str(&format!("address:{pane}")))];
    if target_alive {
        actions.push(format!("hl.dispatch(hl.dsp.window.tag({{tag={},window={}}}))",
            lua_str(&format!("{sign}mios-shaderglass-pane:{pane}")), lua_str(&format!("address:{target}"))));
    }
    actions.into_iter().map(|a| format!("pcall(function() {a} end)")).collect::<Vec<_>>().join("; ")
}

/// A pane waiting for its launch: the target's native identity, setup or overlay, and how many times it was
/// relaunched after ShaderGlass died on its own.
#[derive(Clone, Debug)]
struct Queued {
    identity: WindowIdentity,
    setup: bool,
    restarts: u32,
}

struct Pane {
    id: String,
    target: String,
    target_identity: WindowIdentity,
    pane_identity: Option<WindowIdentity>,
    class: String,
    child: Child,
    window: Option<String>,
    launched: Instant,
    setup: bool,
    follower: Follower,
    token_file: Option<PathBuf>,
    token_seed: Option<(u64, u64)>,
    size_file: Option<PathBuf>,
    request: Option<PathBuf>,
    closing: Option<Instant>,
    /// The supervisor (detach, target closed, stop) asked for the close: never relaunched.
    requested_close: bool,
    term_sent: bool,
    /// The WineCap command channel of this process (MIOS_WINECAP_COMMAND_FILE).
    command: Option<state::Command>,
    /// The class profile ShaderGlass was started with.
    profile: Option<PathBuf>,
    restarts: u32,
    /// The last profile save of this pane: Ok(shader) or Err(reason).
    saved: Option<Result<String, String>>,
    /// Whether ShaderGlass' menu bar is shown, as its WineCap last reported (None: never asked / no channel).
    menu: Option<bool>,
}

impl Pane {
    fn state(&self) -> &'static str {
        match (&self.window, self.closing) {
            (_, Some(_)) => "closing",
            (None, None) => "launching",
            (Some(_), None) if self.setup => "setup",
            _ => "overlay",
        }
    }
}

struct Supervisor {
    cfg: Config,
    hypr: Hypr,
    panes: Vec<Pane>,
    queue: VecDeque<Queued>,
    clients: Vec<Json>,
    clients_at: Option<Instant>,
    mons: Vec<Mon>,
    mons_at: Option<Instant>,
    active: String,
    buttons: Buttons,
    hypr3d: bool,
    hypr3d_at: Option<Instant>,
    store: Json,
    urgent: bool,
    fast: bool,
    /// ShaderGlass windows that belong to no pane (logged once).
    ignored: std::collections::HashSet<String>,
    /// Profiles and the attached-pane record ([desktop.shaderglass.state]).
    state: Store,
    /// Stopped by a signal (unit stop, logout, upgrade): the attached record stays for the next start to resume.
    keep_attached: bool,
}

/// Grace periods for a closing pane: WM_CLOSE, then SIGTERM, then SIGKILL.
const CLOSE_GRACE: Duration = Duration::from_secs(3);
const TERM_GRACE: Duration = Duration::from_secs(6);

impl Supervisor {
    fn log(&self, msg: &str) {
        eprintln!("[miosd shaderglass] {msg}");
    }

    fn eval(&self, lua: &str) {
        if let Err(e) = self.hypr.eval(lua) {
            self.log(&format!("eval: {e}"));
        }
    }

    fn refresh_clients(&mut self) -> Result<(), String> {
        self.clients = self.hypr.json("clients")?.as_array().cloned().unwrap_or_default();
        self.clients_at = Some(Instant::now());
        Ok(())
    }

    fn client(&self, a: &str) -> Option<&Json> {
        self.clients.iter().find(|c| c.get("address").and_then(Json::as_str) == Some(a))
    }

    fn poke_all(&mut self) {
        let now = Instant::now();
        for p in &mut self.panes {
            p.follower.poke(now);
        }
        self.urgent = true;
    }

    fn write_state(&mut self) {
        let mut targets: Vec<String> = self
            .panes
            .iter()
            .filter(|p| p.closing.is_none())
            .map(|p| p.target.clone())
            .collect();
        for q in &self.queue {
            if !targets.contains(&q.identity.address) {
                targets.push(q.identity.address.clone());
            }
        }
        let active = if targets.is_empty() { 0 } else { 1 };
        let _ = std::fs::write(
            session_file("mios-shaderglass.state"),
            format!("active={active}\ntargets={}\n", targets.join(",")),
        );
        self.record_attached();
    }

    /// The panes a restarted supervisor would resume (live and queued ones; never while a signal stops us).
    fn record_attached(&mut self) {
        if self.keep_attached || !self.cfg.state.resume {
            return;
        }
        let mut list: Vec<Attached> = self
            .panes
            .iter()
            .filter(|p| !p.requested_close)
            .map(|p| Attached {
                address: p.target_identity.address.clone(),
                pid: p.target_identity.pid,
                stable_id: p.target_identity.stable_id.clone(),
                class: p.class.clone(),
                setup: p.setup,
            })
            .collect();
        for q in &self.queue {
            if !list.iter().any(|a| a.address == q.identity.address) {
                let class = self.client(&q.identity.address).and_then(|c| c.get("class")).and_then(Json::as_str).unwrap_or("").to_owned();
                list.push(Attached { address: q.identity.address.clone(), pid: q.identity.pid, stable_id: q.identity.stable_id.clone(), class, setup: q.setup });
            }
        }
        if let Err(e) = self.state.set_attached(&list) {
            self.log(&format!("state: {e}"));
        }
    }

    /// Save pane `i`'s live ShaderGlass state as its class profile (WineCap runs "Set as default"; the
    /// operator's own default is put back). Needs a mapped pane whose WineCap has the command channel.
    fn save_pane(&mut self, i: usize) -> Result<String, String> {
        if !self.cfg.state.persist {
            return Err("profile persistence is off ([desktop.shaderglass.state].persist = false)".into());
        }
        let Some(default) = default_profile_path(&self.cfg) else {
            return Err("the Wine prefix has no user profile directory".into());
        };
        let timeout = self.cfg.state.save_timeout;
        let Supervisor { panes, state, cfg, .. } = self;
        let p = &mut panes[i];
        if p.window.is_none() || p.closing.is_some() {
            return Err("that pane is not running".into());
        }
        let Some(cmd) = p.command.as_mut() else {
            return Err("that pane has no WineCap command channel".into());
        };
        if !cmd.ready() {
            return Err("this WineCap has no MiOS command channel (reinstall: mios-shaderglass install --force)".into());
        }
        let class = p.class.clone();
        let r = state::save_profile(state, &cfg.state, &class, &default, || cmd.run("save", timeout).map(|_| ()))
            .map(|prof| state::profile_shader(&prof).unwrap_or_default());
        p.saved = Some(r.clone());
        match &r {
            Ok(shader) => eprintln!("[miosd shaderglass] {}: saved the ShaderGlass profile of class {class} ({shader})", p.target),
            Err(e) => eprintln!("[miosd shaderglass] {}: profile not saved: {e}", p.target),
        }
        r
    }

    /// Show (`on`) or hide ShaderGlass' menu in pane `i` through its WineCap command channel. Some(shown
    /// afterwards, as ShaderGlass reports it) when the channel answered; None when it is not available or
    /// failed (the caller sends ShaderGlass' key instead, a relative toggle).
    fn set_menu_in_pane(&mut self, i: usize, on: bool) -> Option<bool> {
        let timeout = self.cfg.state.save_timeout;
        let c = self.panes[i].command.as_mut().filter(|c| c.ready())?;
        match c.run(if on { "menu-on" } else { "menu-off" }, timeout) {
            Ok(detail) => {
                // An answer without the state (it cannot happen with this patch) counts as done.
                let shown = state::menu_shown(&detail).unwrap_or(on);
                self.panes[i].menu = Some(shown);
                if shown != on {
                    let target = self.panes[i].target.clone();
                    self.log(&format!("{target}: ShaderGlass kept its menu {}", if shown { "shown" } else { "hidden" }));
                }
                Some(shown)
            }
            Err(e) => {
                let target = self.panes[i].target.clone();
                self.log(&format!("{target}: menu through WineCap: {e}; sending the key"));
                None
            }
        }
    }

    fn sync_hypr3d(&self, target: &str, on: bool) {
        let arg = lua_str(&format!("sync2d:{target}:{}", if on { 1 } else { 0 }));
        let _ = self.hypr.eval(&format!(
            "if hl.plugin and hl.plugin.hypr3d and hl.plugin.hypr3d.crt then pcall(hl.plugin.hypr3d.crt, {arg}) end"
        ));
    }

    fn drain_3d_requests(&mut self) {
        let req_file = session_file("mios-shaderglass-3d-request.txt");
        let req_path = req_file.as_path();
        let Ok(content) = std::fs::read_to_string(req_path) else { return };
        let _ = std::fs::remove_file(req_path);
        for raw in content.lines() {
            let line = raw.trim();
            if line.is_empty() {
                continue;
            }
            let (verb, target_raw) = line.split_once(char::is_whitespace).unwrap_or(("toggle", line));
            match verb {
                "off" | "stop" | "clear" => {
                    self.queue.clear();
                    for i in 0..self.panes.len() {
                        self.close_pane(i);
                    }
                    self.urgent = true;
                }
                "attach" | "on" => {
                    let t = addr(target_raw.trim());
                    if !t.is_empty() {
                        let _ = self.on_control(&json!({"cmd": "attach", "target": t, "setup": false}));
                    }
                }
                "detach" => {
                    let t = addr(target_raw.trim());
                    if !t.is_empty() {
                        let _ = self.on_control(&json!({"cmd": "detach", "target": t}));
                    }
                }
                _ => {
                    let t = addr(if verb.starts_with("0x") { verb } else { target_raw.trim() });
                    if !t.is_empty() {
                        let has = self.panes.iter().any(|p| p.target == t && p.closing.is_none())
                            || self.queue.iter().any(|q| q.identity.address == t);
                        let cmd = if has { "detach" } else { "attach" };
                        let _ = self.on_control(&json!({"cmd": cmd, "target": t, "setup": false}));
                    }
                }
            }
        }
        self.write_state();
    }

    fn on_event(&mut self, line: &str) {
        let (name, data) = line.split_once(">>").unwrap_or((line, ""));
        match name {
            "openwindow" => {
                let mut it = data.splitn(4, ',');
                let a = addr(it.next().unwrap_or(""));
                let _ws = it.next();
                let class = it.next().unwrap_or("");
                if self.cfg.window.class.is_match(class) {
                    self.on_open(&a);
                }
                self.poke_all();
            }
            "closewindow" => {
                let a = addr(data);
                self.on_close(&a);
                self.poke_all();
            }
            "activewindowv2" => {
                if !data.is_empty() {
                    self.active = addr(data);
                }
                self.poke_all();
            }
            "activewindow" => {
                self.poke_all();
            }
            "configreloaded" => {
                // Re-send every pane's geometry, opacity and passthrough tag.
                for p in &mut self.panes {
                    p.follower.reset();
                }
                let tags: Vec<(String, bool)> = self.panes.iter().filter_map(|p| p.window.clone().map(|w| (w, !p.setup))).collect();
                for (w, on) in tags {
                    if self.client(&w).and_then(|c| c.get("mapped")).and_then(Json::as_bool) == Some(true) {
                        self.eval(&follow::passthrough_lua(&w, &self.cfg.window.passthrough_tag, on));
                    }
                }
                self.mons_at = None;
                self.poke_all();
            }
            "workspace" | "workspacev2" | "focusedmon" | "focusedmonv2" | "activespecial" | "activespecialv2"
            | "monitoradded" | "monitoraddedv2" | "monitorremoved" | "monitorremovedv2" | "moveworkspace"
            | "moveworkspacev2" => {
                self.mons_at = None;
                self.poke_all();
            }
            "movewindow" | "movewindowv2" | "changefloatingmode" | "fullscreen" | "minimized" | "pin" => self.poke_all(),
            // A launching ShaderGlass retitles its main window ("ShaderGlass (...)") right after it maps.
            "windowtitle" | "windowtitlev2" => {
                self.adopt_fresh();
                self.poke_all();
            }
            _ => {}
        }
    }

    fn on_open(&mut self, a: &str) {
        if self.panes.iter().any(|p| p.window.as_deref() == Some(a)) {
            return;
        }
        if let Err(e) = self.refresh_clients() {
            self.log(&format!("clients: {e}"));
            return;
        }
        self.adopt();
    }

    fn launching(&self) -> bool {
        self.panes.iter().any(|p| p.window.is_none() && p.closing.is_none())
    }

    /// While a pane is launching: refresh the client list and adopt.
    fn adopt_fresh(&mut self) {
        if self.launching() && self.refresh_clients().is_ok() {
            self.adopt();
        }
    }

    /// Bind every launching pane to its ShaderGlass main window (from the current client list).
    fn adopt(&mut self) {
        if !self.launching() {
            return;
        }
        let taken: Vec<String> = self.panes.iter().filter_map(|p| p.window.clone()).collect();
        let found: Vec<(String, i64)> = self
            .clients
            .iter()
            .filter_map(|c| {
                let a = c.get("address").and_then(Json::as_str)?;
                let class = c.get("class").and_then(Json::as_str).unwrap_or("");
                let title = c.get("title").and_then(Json::as_str).unwrap_or("");
                let mapped = c.get("mapped").and_then(Json::as_bool) == Some(true);
                (mapped && !taken.iter().any(|t| t == a) && is_main_window(&self.cfg.window, class, title))
                    .then(|| (a.to_owned(), c.get("pid").and_then(Json::as_i64).unwrap_or(-1)))
            })
            .collect();
        for (a, pid) in found {
            let waiting: Vec<(usize, i64)> = (0..self.panes.len())
                .filter(|&i| self.panes[i].window.is_none() && self.panes[i].closing.is_none())
                .map(|i| (i, i64::from(self.panes[i].child.id())))
                .collect();
            match pane_for_window(&waiting, pid, in_my_cgroup(pid)) {
                Some(i) => self.bind(i, &a),
                None => {
                    if self.ignored.insert(a.clone()) {
                        self.log(&format!("{a}: a ShaderGlass window no pane is waiting for (pid {pid}); left alone"));
                    }
                }
            }
        }
    }

    fn bind(&mut self, i: usize, a: &str) {
        let identity = self.panes[i].target_identity.clone();
        if !self.client(&identity.address).and_then(Win::from_json).is_some_and(|w| identity.matches(&w)) {
            self.close_pane(i);
            return;
        }
        let pane_identity = self.client(a).and_then(Win::from_json).map(|w| WindowIdentity::from_window(&w));
        let tag = self.cfg.window.passthrough_tag.clone();
        let p = &mut self.panes[i];
        p.window = Some(a.to_owned());
        p.pane_identity = pane_identity;
        p.follower.reset();
        // Keep the picker request: WineCap opens its ScreenCast session only after this window maps, so
        // deleting it here sent every pick to the stock picker. The MiOS picker consumes it (one-shot), it
        // expires after request_ttl_ms, and finish() removes whatever is left when the pane ends.
        let setup = p.setup;
        let target = p.target.clone();
        self.log(&format!("{a}: pane for {target} mapped ({})", if setup { "setup" } else { "overlay" }));
        let lua = if setup {
            // A pane launched in setup: the menu through the WineCap channel when it is already up, else the key.
            let menu = self.set_menu_in_pane(i, true);
            follow::setup_lua(a, &tag, true, menu.is_none().then_some(self.cfg.setup_shortcut.as_str()))
        } else {
            follow::passthrough_lua(a, &tag, true)
        };
        let layer = self.client(a).map(|c| pane_layer_lua(a, c)).unwrap_or_default();
        self.eval(&format!("{}; {lua}{layer}", binding_lua(a, &target, true, true)));
        self.urgent = true;
    }

    fn on_close(&mut self, a: &str) {
        let close = self.cfg.follow.close_with_target;
        let mut to_close = Vec::new();
        let mut unbind = Vec::new();
        for (i, p) in self.panes.iter_mut().enumerate() {
            if p.window.as_deref() == Some(a) {
                unbind.push((p.target.clone(), p.target_identity.clone()));
                p.window = None;
                p.closing.get_or_insert_with(Instant::now);
            } else if p.target == a && close {
                to_close.push(i);
            }
        }
        for (target, identity) in unbind {
            let alive = self.client(&target).and_then(Win::from_json)
                .is_some_and(|w| identity.matches(&w));
            self.eval(&binding_lua(a, &target, false, alive));
        }
        for i in to_close {
            self.close_pane(i);
        }
        self.clients.retain(|c| c.get("address").and_then(Json::as_str) != Some(a));
        self.ignored.remove(a);
        self.write_state();
    }

    fn close_pane(&mut self, i: usize) {
        if self.panes[i].closing.is_some() {
            return;
        }
        self.panes[i].requested_close = true;
        // Keep what the operator chose in this pane for the next one on this class.
        if self.panes[i].window.is_some() && self.cfg.state.persist {
            let _ = self.save_pane(i);
        }
        let w = self.panes[i].window.clone();
        let target = self.panes[i].target.clone();
        let target_alive = self.client(&target).and_then(Win::from_json)
            .is_some_and(|t| self.panes[i].target_identity.matches(&t));
        if let Some(pane) = &w {
            self.eval(&binding_lua(pane, &target, false, target_alive));
        }
        let mapped = w.as_deref().and_then(|w| self.client(w)).and_then(|c| c.get("mapped")).and_then(Json::as_bool) == Some(true);
        match w {
            Some(w) if mapped => self.eval(&follow::actions_lua(&w, &target, &[follow::Action::Close])),
            _ => {
                let _ = self.panes[i].child.kill();
            }
        }
        self.panes[i].closing = Some(Instant::now());
        self.write_state();
    }

    fn find(&self, req: &Json) -> Option<usize> {
        if let Some(t) = req.get("target").and_then(Json::as_str) {
            return self.panes.iter().position(|p| p.target == addr(t));
        }
        let w = addr(req.get("pane").and_then(Json::as_str)?);
        self.panes.iter().position(|p| p.window.as_deref() == Some(w.as_str()))
    }

    fn on_control(&mut self, req: &Json) -> Json {
        let err = |m: String| json!({"ok": false, "error": m});
        match req.get("cmd").and_then(Json::as_str).unwrap_or("") {
            "ping" => json!({"ok": true}),
            "status" => json!({
                "ok": true,
                "unit": unit_name(&self.cfg),
                "panes": self.panes.iter().map(|p| json!({"id": p.id, "target": p.target, "class": p.class, "window": p.window,
                    "pid": p.child.id(), "state": p.state(), "profile": p.profile, "restarts": p.restarts,
                    "command_channel": p.command.as_ref().is_some_and(state::Command::ready), "menu": p.menu, "placement_gave_up": p.follower.gave_up(),
                    "saved": p.saved.as_ref().map(|r| match r { Ok(s) => json!({"ok": true, "shader": s}), Err(e) => json!({"ok": false, "error": e}) })})).collect::<Vec<_>>(),
                "queued": self.queue.iter().map(|q| json!({"target": q.identity.address, "setup": q.setup, "restarts": q.restarts})).collect::<Vec<_>>(),
                // Whether the supervisor sees the Hypr3D room (panes hidden, processes and profiles kept).
                "hypr3d_room": self.hypr3d,
                "state": self.state.to_status(),
            }),
            "attach" => {
                let Some(t) = req.get("target").and_then(Json::as_str).filter(|t| !t.is_empty()).map(addr) else {
                    return err("attach needs a target".into());
                };
                if let Some(reply) = self.panes.iter().find(|p| p.target == t && p.closing.is_none())
                    .map(|p| json!({"ok": true, "pane": p.id, "state": p.state()})) {
                    self.write_state();
                    self.sync_hypr3d(&t, true);
                    return reply;
                }
                if self.queue.iter().any(|q| q.identity.address == t) {
                    self.write_state();
                    self.sync_hypr3d(&t, true);
                    return json!({"ok": true, "state": "queued"});
                }
                if let Err(e) = self.refresh_clients() {
                    return err(e);
                }
                match self.client(&t) {
                    None => return err(format!("no window {t}")),
                    Some(c) if is_pane(&self.cfg, c) => return err(format!("{t} is a ShaderGlass window")),
                    Some(_) => {}
                }
                let Some(window) = self.client(&t).and_then(Win::from_json).filter(|w| w.mapped) else {
                    return err("the target is not mapped".into());
                };
                self.queue.push_back(Queued {
                    identity: WindowIdentity::from_window(&window),
                    setup: req.get("setup").and_then(Json::as_bool).unwrap_or(false),
                    restarts: 0,
                });
                self.urgent = true;
                self.write_state();
                self.sync_hypr3d(&t, true);
                json!({"ok": true, "state": "queued"})
            }
            "detach" => {
                let mut detached_target = req.get("target").and_then(Json::as_str).map(addr);
                if let Some(ref t) = detached_target {
                    self.queue.retain(|q| &q.identity.address != t);
                }
                let res = match self.find(req) {
                    Some(i) => {
                        if detached_target.is_none() {
                            detached_target = Some(self.panes[i].target.clone());
                        }
                        self.close_pane(i);
                        json!({"ok": true, "state": "closing"})
                    }
                    None => json!({"ok": true, "state": "none"}),
                };
                self.write_state();
                if let Some(t) = detached_target {
                    self.sync_hypr3d(&t, false);
                }
                res
            }
            "setup" => {
                let Some(i) = self.find(req) else { return err("no pane for that window".into()) };
                let Some(w) = self.panes[i].window.clone() else { return err("that pane has not mapped yet".into()) };
                let entering = !self.panes[i].setup;
                self.panes[i].setup = entering;
                self.panes[i].follower.reset();
                // The menu is shown / hidden inside ShaderGlass (WineCap channel) when it can; ShaderGlass' key
                // (a relative toggle) otherwise.
                let menu = self.set_menu_in_pane(i, entering);
                let key = menu.is_none().then_some(self.cfg.setup_shortcut.as_str());
                self.eval(&follow::setup_lua(&w, &self.cfg.window.passthrough_tag, entering, key));
                self.urgent = true;
                self.write_state();
                // Leaving setup is when the operator is done choosing: keep it for the class.
                let saved = if entering || !self.cfg.state.persist { None } else { Some(self.save_pane(i)) };
                json!({"ok": true, "setup": entering, "menu": menu, "saved": saved.map(|r| r.is_ok())})
            }
            "cycle" => {
                let Some(i) = self.find(req) else { return err("no pane for that window".into()) };
                let Some(w) = self.panes[i].window.clone() else { return err("that pane has not mapped yet".into()) };
                let timeout = self.cfg.state.save_timeout;
                let via = match self.panes[i].command.as_mut().filter(|c| c.ready()) {
                    Some(c) => match c.run("next", timeout) {
                        Ok(_) => "winecap",
                        Err(e) => return err(e),
                    },
                    None => {
                        // An older WineCap: ShaderGlass' own accelerator, sent to the pane (best effort, unsaved).
                        self.eval(&follow::key_press_lua(&w, &self.cfg.cycle_shortcut));
                        "shortcut"
                    }
                };
                let saved = if via == "winecap" && self.cfg.state.persist { self.save_pane(i).ok() } else { None };
                json!({"ok": true, "via": via, "shader": saved})
            }
            "save" => {
                let Some(i) = self.find(req) else { return err("no pane for that window".into()) };
                match self.save_pane(i) {
                    Ok(shader) => json!({"ok": true, "shader": shader, "profile": self.state.profile_path(&self.panes[i].class)}),
                    Err(e) => err(e),
                }
            }
            "stop" => {
                // An explicit stop: nothing to resume afterwards.
                self.keep_attached = false;
                let _ = self.state.set_attached(&[]);
                STOP.store(true, Ordering::SeqCst);
                json!({"ok": true})
            }
            other => err(format!("unknown command {other:?}")),
        }
    }

    /// Serialize capture selection, including sessions restored without a picker.
    fn launch_next(&mut self) {
        for i in 0..self.panes.len() {
            let p = &self.panes[i];
            let Some(request) = p.request.as_deref() else { continue };
            match picker::retire_request(request, p.token_file.as_deref(), p.token_seed, p.closing.is_some()) {
                Ok(true) => self.panes[i].request = None,
                Ok(false) => {
                    if p.launched.elapsed() >= self.cfg.capture.request_ttl {
                        self.log(&format!("{}: capture selection did not complete before request expiry; closing pane", p.target));
                        self.close_pane(i);
                    }
                    return;
                }
                Err(error) => {
                    self.log(&format!("capture request cleanup: {error}"));
                    return;
                }
            }
        }
        if self.panes.iter().any(|p| p.window.is_none() && p.closing.is_none()) {
            return;
        }
        let Some(q) = self.queue.pop_front() else { return };
        if let Err(e) = self.launch(&q.identity, q.setup, q.restarts) {
            self.log(&format!("{}: {e}", q.identity.address));
            self.write_state();
        }
    }

    fn launch(&mut self, identity: &WindowIdentity, setup: bool, restarts: u32) -> Result<(), String> {
        ensure_portal_backend();
        self.refresh_clients()?;
        let target = identity.address.as_str();
        if !self.client(target).and_then(Win::from_json).is_some_and(|w| identity.matches(&w)) {
            return Err("the queued target window changed before capture launch".into());
        }
        let class = self
            .client(target)
            .and_then(|c| c.get("class"))
            .and_then(Json::as_str)
            .ok_or_else(|| "the target window is gone".to_string())?
            .to_owned();
        let id = target.trim_start_matches("0x").to_owned();
        let rt = runtime_root()?;
        let panes_dir = rt.join("panes");
        ensure_private_dir(&panes_dir)?;
        let token_file = if self.cfg.capture.restore_tokens {
            let counts = picker::class_counts(&self.clients);
            let token = picker::choose_token(&self.store, &class, target, counts.get(&class).copied().unwrap_or(0));
            let f = panes_dir.join(format!("{id}.restore-token"));
            write_private(&f, token.as_deref().unwrap_or("").as_bytes())?;
            if token.is_some() {
                self.log(&format!("{target}: reusing the restore token for class {class}"));
            }
            Some(f)
        } else {
            None
        };
        let token_seed = token_file.as_deref().and_then(picker::token_generation);
        let size_file = {
            let f = panes_dir.join(format!("{id}.size"));
            if let Some(t) = self.client(target).and_then(Win::from_json) {
                let _ = write_private(&f, format!("{} {}\n", t.rect.w.round() as i64, t.rect.h.round() as i64).as_bytes());
            }
            Some(f)
        };
        let request = if self.cfg.capture.picker == PickerMode::Mios {
            let req = picker::Request {
                target: target.to_owned(),
                expires_ms: picker::now_ms() + self.cfg.capture.request_ttl.as_millis() as u64,
                supervisor_pid: std::process::id(),
            };
            Some(picker::write_request(&rt, &req)?)
        } else {
            None
        };
        // The class profile this pane starts with (ShaderGlass loads its last argument as a profile, after its
        // default): what the operator last chose for this class, in any session and presentation mode.
        let profile = if self.cfg.state.persist { self.state.saved_profile(&class) } else { None };
        let profile_arg = profile.as_deref().and_then(state::windows_path);
        // The WineCap command channel: next shader and the setup menu always, the profile save when persisting.
        let command = {
            let c = state::Command::new(panes_dir.join(format!("{id}.cmd")));
            c.cleanup();
            Some(c)
        };
        let (set, remove) = wine::env(&self.cfg, token_file.as_deref());
        let mut cmd = Command::new(&self.cfg.wine.binary);
        cmd.arg(self.cfg.install_dir.join(&self.cfg.exe))
            .args(&self.cfg.launch_args)
            .args(profile_arg.iter())
            .envs(set)
            .current_dir(wine::prefix_dir(&self.cfg))
            .stdin(Stdio::null());
        if let Some(sf) = &size_file {
            cmd.env("MIOS_WINECAP_SIZE_FILE", sf);
        }
        if let Some(c) = &command {
            cmd.env("MIOS_WINECAP_COMMAND_FILE", &c.file);
        }
        for k in remove {
            cmd.env_remove(k);
        }
        let child = match cmd.spawn() {
            Ok(c) => c,
            Err(e) => {
                if let Some(r) = &request {
                    let _ = std::fs::remove_file(r);
                }
                if let Some(s) = &size_file {
                    let _ = std::fs::remove_file(s);
                }
                return Err(format!("{}: {e}", self.cfg.wine.binary));
            }
        };
        self.log(&format!(
            "{target}: ShaderGlass started (pid {}) for class {class}{}{}",
            child.id(),
            profile.as_ref().map(|p| format!(" with profile {}", p.display())).unwrap_or_default(),
            if restarts > 0 { format!(" (relaunch {restarts})") } else { String::new() }
        ));
        self.panes.push(Pane {
            id,
            target: target.to_owned(),
            target_identity: identity.clone(),
            pane_identity: None,
            class,
            child,
            window: None,
            launched: Instant::now(),
            setup,
            follower: Follower::new(),
            token_file,
            token_seed,
            size_file,
            request,
            closing: None,
            requested_close: false,
            term_sent: false,
            command,
            profile,
            restarts,
            saved: None,
            menu: None,
        });
        self.write_state();
        Ok(())
    }

    /// Children that exited, closing panes past their grace, panes that never mapped.
    fn reap(&mut self) {
        let mut done = Vec::new();
        for (i, p) in self.panes.iter_mut().enumerate() {
            match p.child.try_wait() {
                Ok(Some(st)) => {
                    eprintln!("[miosd shaderglass] {}: ShaderGlass exited ({st})", p.target);
                    done.push((i, !st.success()));
                    continue;
                }
                Ok(None) => {}
                Err(_) => {
                    done.push((i, false));
                    continue;
                }
            }
            if p.window.is_none() && p.closing.is_none() && p.launched.elapsed() > self.cfg.map_timeout {
                eprintln!("[miosd shaderglass] {}: no ShaderGlass window within {:?}; stopping it", p.target, self.cfg.map_timeout);
                p.closing = Some(Instant::now().checked_sub(CLOSE_GRACE).unwrap_or_else(Instant::now));
            }
            if let Some(since) = p.closing {
                let waited = since.elapsed();
                if waited > TERM_GRACE {
                    let _ = p.child.kill();
                } else if waited > CLOSE_GRACE && !p.term_sent {
                    unsafe {
                        libc::kill(p.child.id() as libc::pid_t, libc::SIGTERM);
                    }
                    p.term_sent = true;
                }
            }
        }
        for (i, failed) in done.into_iter().rev() {
            let p = self.panes.remove(i);
            let relaunch = failed && relaunch_after_exit(&p, self.cfg.state.restart_limit, self.stopping());
            let item = Queued { identity: p.target_identity.clone(), setup: p.setup, restarts: p.restarts + 1 };
            self.finish(p);
            if relaunch {
                let alive = self.refresh_clients().is_ok()
                    && self.client(&item.identity.address).and_then(Win::from_json).is_some_and(|w| item.identity.matches(&w) && w.mapped);
                if alive && !self.queue.iter().any(|q| q.identity.address == item.identity.address) {
                    self.log(&format!("{}: ShaderGlass died on its own; relaunching it ({}/{}) with the saved profile",
                        item.identity.address, item.restarts, self.cfg.state.restart_limit));
                    self.queue.push_back(item);
                    self.write_state();
                }
            }
        }
    }

    fn stopping(&self) -> bool {
        STOP.load(Ordering::SeqCst)
    }

    /// Queue the panes a supervisor that died (or was stopped by a signal) left attached, when the very same
    /// native windows are still there.
    fn resume(&mut self) {
        if !self.cfg.state.resume {
            return;
        }
        let list = self.state.attached();
        if list.is_empty() {
            return;
        }
        if let Err(e) = self.refresh_clients() {
            self.log(&format!("resume: {e}"));
            return;
        }
        for a in list {
            let identity = WindowIdentity { address: a.address.clone(), pid: a.pid, stable_id: a.stable_id.clone() };
            let live = self.client(&a.address).and_then(Win::from_json).filter(|w| identity.matches(w) && w.mapped);
            match live {
                Some(w) if !self.cfg.window.class.is_match(&w.class) => {
                    self.log(&format!("{}: resuming the ShaderGlass pane ({}, {})", a.address, a.class, if a.setup { "setup" } else { "overlay" }));
                    self.queue.push_back(Queued { identity, setup: a.setup, restarts: 0 });
                }
                _ => self.log(&format!("{}: not resumed (the window is gone or is another window now)", a.address)),
            }
        }
        self.write_state();
    }

    /// Save the restore token WineCap wrote, drop the pane's files.
    fn finish(&mut self, p: Pane) {
        if let Some(pane) = &p.window {
            let target_alive = self.client(&p.target).and_then(Win::from_json)
                .is_some_and(|w| p.target_identity.matches(&w));
            self.eval(&binding_lua(pane, &p.target, false, target_alive));
        }
        if let Some(r) = &p.request {
            let _ = std::fs::remove_file(r);
        }
        if let Some(s) = &p.size_file {
            let _ = std::fs::remove_file(s);
        }
        if let Some(c) = &p.command {
            c.cleanup();
        }
        if let Some(f) = &p.token_file {
            let token = std::fs::read_to_string(f).unwrap_or_default();
            if picker::remember_token(&mut self.store, &p.class, &p.target, token.trim()) {
                let path = token_store_path();
                let saved = path
                    .parent()
                    .ok_or_else(|| "token store has no parent".to_string())
                    .and_then(ensure_private_dir)
                    .and_then(|_| write_private(&path, self.store.to_string().as_bytes()));
                if let Err(e) = saved {
                    self.log(&format!("restore tokens: {e}"));
                } else {
                    self.log(&format!("{}: restore token saved for class {}", p.target, p.class));
                }
            }
            let _ = std::fs::remove_file(f);
        }
        self.write_state();
    }

    fn hypr3d_active(&mut self, now: Instant) -> bool {
        if !self.cfg.hide_in_hypr3d {
            return false;
        }
        if self.hypr3d_at.is_some_and(|t| now.saturating_duration_since(t) < Duration::from_millis(100)) {
            return self.hypr3d;
        }
        self.hypr3d_at = Some(now);
        let prev = self.hypr3d;
        let active = if let Ok(rt) = runtime_root() {
            let f = rt.join(format!("hypr3d-active-{}", std::process::id()));
            let _ = std::fs::remove_file(&f);
            let lua = format!(
                "local p = hl.plugin and hl.plugin.hypr3d; local a = false; if p then local probe = p.effect_in_room or p.active; if probe then local ok, v = pcall(probe); a = ok and v and true or false end end; local f = io.open({}, 'w'); if f then f:write(a and '1' or '0'); f:close() end",
                lua_str(&f.display().to_string())
            );
            let value = self.hypr.eval(&lua).ok().and_then(|_| std::fs::read_to_string(&f).ok())
                .map(|v| v.trim() == "1").unwrap_or(prev);
            let _ = std::fs::remove_file(&f);
            value
        } else {
            prev
        };
        self.hypr3d = active;
        if prev != active {
            for p in &mut self.panes {
                p.follower.reset();
                p.follower.poke(now);
            }
            self.urgent = true;
        }
        self.hypr3d
    }

    /// Follow every mapped pane; returns the delay until the next tick.
    fn tick(&mut self) -> Duration {
        let now = Instant::now();
        self.drain_3d_requests();
        self.write_state();
        // A main window the event stream did not hand over (title set before the map event, events lost).
        self.adopt_fresh();
        let live: Vec<usize> = (0..self.panes.len()).filter(|&i| self.panes[i].window.is_some() && self.panes[i].closing.is_none()).collect();
        if live.is_empty() {
            return self.cfg.follow.idle;
        }
        // Always refresh the full client list so both the target window and the click-through ShaderGlass
        // pane (which has `no_focus` and is never `activewindow`) have fresh geometries on every frame.
        if let Err(e) = self.refresh_clients() {
            self.log(&format!("clients: {e}"));
            return self.cfg.follow.idle;
        }
        if self.active.is_empty() {
            if let Ok(c) = self.hypr.json("activewindow") {
                if let Some(a) = c.get("address").and_then(Json::as_str).filter(|a| !a.is_empty()) {
                    self.active = addr(a);
                }
            }
        }
        if self.mons_at.is_none_or(|t| now.saturating_duration_since(t) > Duration::from_secs(1)) {
            match self.hypr.json("monitors") {
                Ok(m) => {
                    self.mons = follow::monitors_from_json(&m);
                    self.mons_at = Some(now);
                }
                Err(e) => self.log(&format!("monitors: {e}")),
            }
        }
        let suppressed = self.hypr3d_active(now);
        // Any held pointer button (Super+LMB drag on pane or target, or hyprbars title drag): poll all panes at frame rate.
        let pointer_down = self.buttons.down() == Some(true);
        if pointer_down {
            for &i in &live {
                self.panes[i].follower.poke(now);
            }
        }
        let mut next = self.cfg.follow.idle;
        let mut evals = Vec::new();
        let mut gone = Vec::new();
        for &i in &live {
            let w = self.panes[i].window.clone().unwrap_or_default();
            let target_addr = self.panes[i].target.clone();
            let target = self.client(&target_addr).and_then(Win::from_json)
                .filter(|t| self.panes[i].target_identity.matches(t));
            let pane = self.client(&w).and_then(Win::from_json)
                .filter(|w| self.panes[i].pane_identity.as_ref().is_some_and(|id| id.matches(w)));
            if let Some(layer) = self.client(&w).map(|c| pane_layer_lua(&w, c)).filter(|l| !l.is_empty()) {
                evals.push(layer.trim_start_matches("; ").to_owned());
            }
            let dragging = self.panes[i].setup && pointer_down;
            self.panes[i].follower.set_dragging(dragging);
            let step = self.panes[i].follower.step(&self.cfg.follow, target.as_ref(), pane.as_ref(), &self.mons, suppressed, now);
            if !step.actions.is_empty() {
                if let (Some(sf), Some(t)) = (&self.panes[i].size_file, &target) {
                    if let Some(r) = step.actions.iter().find_map(|a| match a {
                        follow::Action::Place(r) | follow::Action::PlaceTarget(r) => Some(*r),
                        _ => None,
                    }) {
                        let _ = write_private(sf, format!("{} {}\n", r.w.round() as i64, r.h.round() as i64).as_bytes());
                    } else {
                        let _ = t;
                    }
                }
                evals.push(follow::actions_lua(&w, &target_addr, &step.actions));
            }
            if step.outcome != follow::Outcome::Continue {
                gone.push(i);
            }
            next = next.min(step.next);
        }
        for lua in evals {
            self.eval(&lua);
        }
        for i in gone {
            self.panes[i].requested_close = true;
            self.panes[i].closing.get_or_insert(now);
        }
        self.fast = next < self.cfg.follow.idle;
        next
    }

    fn shutdown(&mut self) {
        // A signal (unit stop, logout, miosd upgrade) keeps the attached record for the next start.
        self.record_attached();
        if !self.keep_attached {
            let _ = self.state.set_attached(&[]);
        }
        self.queue.clear();
        for i in 0..self.panes.len() {
            self.close_pane(i);
        }
        let until = Instant::now() + TERM_GRACE + Duration::from_secs(1);
        while !self.panes.is_empty() && Instant::now() < until {
            self.reap();
            std::thread::sleep(Duration::from_millis(100));
        }
        for mut p in std::mem::take(&mut self.panes) {
            let _ = p.child.kill();
            let _ = p.child.wait();
            self.finish(p);
        }
        self.write_state();
    }
}

fn serve(cfg: Config) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;
    require_installed(&cfg)?;
    let sock = control_socket()?;
    if UnixStream::connect(&sock).is_ok() {
        return Err("another ShaderGlass supervisor is running".into());
    }
    let _ = std::fs::remove_file(&sock);
    let listener = UnixListener::bind(&sock).map_err(|e| format!("{}: {e}", sock.display()))?;
    std::fs::set_permissions(&sock, std::fs::Permissions::from_mode(0o600)).map_err(|e| e.to_string())?;
    // SAFETY: the handler only stores to an atomic.
    unsafe {
        libc::signal(libc::SIGTERM, on_signal as extern "C" fn(libc::c_int) as libc::sighandler_t);
        libc::signal(libc::SIGINT, on_signal as extern "C" fn(libc::c_int) as libc::sighandler_t);
    }
    let hypr = Hypr::connect()?;
    let active = hypr
        .json("activewindow")
        .ok()
        .and_then(|c| c.get("address").and_then(Json::as_str).map(addr))
        .unwrap_or_default();
    let (tx, rx) = mpsc::channel();
    spawn_events(tx.clone());
    spawn_control(listener, tx);
    let idle_exit = cfg.idle_exit;
    let store = Store::load(&state_dir(&cfg));
    let mut sv = Supervisor {
        store: picker::load_store(&token_store_path()),
        cfg,
        hypr,
        panes: Vec::new(),
        queue: VecDeque::new(),
        clients: Vec::new(),
        clients_at: None,
        mons: Vec::new(),
        mons_at: None,
        active,
        buttons: Buttons::new(),
        hypr3d: false,
        hypr3d_at: None,
        urgent: false,
        fast: false,
        ignored: std::collections::HashSet::new(),
        state: store,
        keep_attached: false,
    };
    if let Some(default) = default_profile_path(&sv.cfg) {
        match state::recover_default(&sv.state.dir, &default) {
            Ok(true) => sv.log("restored the operator's ShaderGlass default profile after an interrupted save"),
            Ok(false) => {}
            Err(e) => sv.log(&format!("default profile recovery: {e}")),
        }
    }
    sv.resume();
    sv.write_state();
    sv.log(&format!("serving on {} for {}", sock.display(), sv.hypr.dir().display()));
    let mut next = Instant::now();
    let mut empty_since: Option<Instant> = Some(Instant::now());
    loop {
        let wait = next.saturating_duration_since(Instant::now()).min(Duration::from_millis(250));
        match rx.recv_timeout(wait) {
            Ok(Msg::Line(l)) => sv.on_event(&l),
            Ok(Msg::Control(req, reply)) => {
                let r = sv.on_control(&req);
                let _ = reply.send(r);
            }
            Ok(Msg::Lost(e)) => {
                sv.log(&format!("event stream: {e}; reconnecting"));
                if let Ok(h) = Hypr::connect() {
                    sv.hypr = h;
                    for p in &mut sv.panes {
                        p.follower.reset();
                    }
                }
            }
            Err(mpsc::RecvTimeoutError::Timeout) => {}
            Err(mpsc::RecvTimeoutError::Disconnected) => break,
        }
        if STOP.load(Ordering::SeqCst) {
            sv.log("stopping");
            sv.shutdown();
            break;
        }
        if SIGNALLED.load(Ordering::SeqCst) {
            sv.keep_attached = true;
        }
        sv.reap();
        sv.launch_next();
        let now = Instant::now();
        if now >= next || sv.urgent {
            sv.urgent = false;
            next = now + sv.tick();
        }
        if sv.panes.is_empty() && sv.queue.is_empty() {
            let since = *empty_since.get_or_insert(now);
            if now.saturating_duration_since(since) > idle_exit {
                sv.log("no panes left; exiting");
                break;
            }
        } else {
            empty_since = None;
        }
    }
    sv.write_state();
    let _ = std::fs::remove_file(&sock);
    Ok(())
}

// ---------------------------------------------------------------- entry

pub fn run(cmd: &ShaderglassCommand) -> Result<(), String> {
    if let ShaderglassCommand::Pick { args } = cmd {
        return pick(Config::load(), args);
    }
    let cfg = Config::load()?;
    let print = |s: String| -> Result<(), String> {
        println!("{s}");
        Ok(())
    };
    match cmd {
        ShaderglassCommand::Install { channel, releases_json, archive, winecap, dest, from_stage, stage_only, dry_run, force } => {
            if let Some(stage) = from_stage {
                return print(release::install_stage(stage, dest.as_deref().unwrap_or(&cfg.install_dir))?);
            }
            let o = release::Opts {
                channel: channel.as_deref().map(Channel::parse).transpose()?,
                releases_json: releases_json.clone(),
                archive: archive.clone(),
                winecap: winecap.clone(),
                dest: dest.clone(),
                stage_only: *stage_only,
                dry_run: *dry_run,
                force: *force,
            };
            print(release::install(&cfg, &o)?)
        }
        ShaderglassCommand::Resolve { channel, releases_json } => {
            let channel = channel.as_deref().map(Channel::parse).transpose()?.unwrap_or_else(|| cfg.channel.clone());
            let releases = match releases_json {
                Some(p) => serde_json::from_slice(&std::fs::read(p).map_err(|e| format!("{}: {e}", p.display()))?)
                    .map_err(|e| format!("{}: {e}", p.display()))?,
                None => {
                    let dir = tempfile_dir()?;
                    let r = release::fetch_releases(&cfg, &dir);
                    let _ = std::fs::remove_dir_all(&dir);
                    r?
                }
            };
            let repo = release::github_repo(&cfg.source_repository)?;
            let rel = release::select(&releases, &channel, &cfg.asset_pattern, &repo)?;
            print(serde_json::to_string_pretty(&rel.to_json()).map_err(|e| e.to_string())?)
        }
        ShaderglassCommand::Attach { window, setup } => print(attach(&cfg, window.as_deref(), *setup)?),
        ShaderglassCommand::Detach { window } => print(detach(&cfg, window.as_deref())?),
        ShaderglassCommand::Toggle => print(toggle(&cfg)?),
        ShaderglassCommand::Setup { window } => print(setup(&cfg, window.as_deref())?),
        ShaderglassCommand::Cycle { window } => print(cycle(&cfg, window.as_deref())?),
        ShaderglassCommand::Save { window } => print(save(&cfg, window.as_deref())?),
        ShaderglassCommand::Resume => print(resume(&cfg)?),
        ShaderglassCommand::Stop => print(stop(&cfg)?),
        ShaderglassCommand::Status => print(serde_json::to_string_pretty(&status(&cfg)).map_err(|e| e.to_string())?),
        ShaderglassCommand::Serve => serve(cfg),
        ShaderglassCommand::PortalConfig { check, write } => print(portal_config(&cfg, *check, *write)?),
        ShaderglassCommand::Prefix => {
            let created = wine::ensure_prefix(&cfg)?;
            let seeded = wine::seed_profile(&cfg)?;
            print(json!({"prefix": wine::prefix_dir(&cfg), "created": created, "seeded_profile": seeded}).to_string())
        }
        ShaderglassCommand::Pick { .. } => Ok(()),
    }
}

/// A private scratch directory under $XDG_RUNTIME_DIR (resolve only).
fn tempfile_dir() -> Result<PathBuf, String> {
    let d = runtime_root()?.join(format!("resolve-{}", std::process::id()));
    ensure_private_dir(&d)?;
    Ok(d)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn vendor() -> Value {
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../usr/share/mios/mios.toml");
        std::fs::read_to_string(path).unwrap().parse().unwrap()
    }

    #[test]
    fn native_window_identity_rejects_address_reuse_but_accepts_mode_changes() {
        let json = json!({"address":"0x123","pid":11,"stableId":"42","at":[0,0],"size":[800,600],"mapped":true});
        let original = Win::from_json(&json).unwrap();
        let identity = WindowIdentity::from_window(&original);
        let mut changed = original.clone();
        changed.rect = follow::Rect { x: 0.0, y: 0.0, w: 1920.0, h: 1080.0 };
        changed.workspace_id = 4;
        changed.monitor = 2;
        assert!(identity.matches(&changed));
        changed.stable_id = "43".into();
        assert!(!identity.matches(&changed), "a new native window reusing an address must not inherit capture");
        changed.stable_id = "42".into();changed.pid = 12;
        assert!(!identity.matches(&changed), "another process is not the original target");
    }

    #[test]
    fn a_pane_the_rules_missed_is_floated_and_pinned_once() {
        let tiled = json!({"address": "0xp", "floating": false, "pinned": false});
        let l = pane_layer_lua("0xp", &tiled);
        assert!(l.contains("window.float({window=\"address:0xp\",action='enable'})") || l.contains("window.float({window='address:0xp',action='enable'})"), "{l}");
        assert!(l.contains("window.pin("), "{l}");
        assert_eq!(pane_layer_lua("0xp", &json!({"floating": true, "pinned": true})), "", "a ruled pane is left alone");
        assert!(!pane_layer_lua("0xp", &json!({"floating": true, "pinned": false})).contains("float("));
    }

    #[test]
    fn native_association_tags_bind_both_windows_and_cleanup_is_independent() {
        let bound = binding_lua("0xabc", "0xdef", true, true);
        assert!(bound.contains("+mios-shaderglass-target:0xdef"));
        assert!(bound.contains("+mios-shaderglass-pane:0xabc"));
        assert_eq!(bound.matches("pcall(function()").count(), 2);
        let gone_target = binding_lua("0xabc", "0xdef", false, false);
        assert!(gone_target.contains("-mios-shaderglass-target:0xdef"));
        assert!(!gone_target.contains("address:0xdef"), "never tag an address-reused target");
    }

    /// Run only in an isolated compositor with three owned, floated fixture windows.
    /// This dispatches the same follower actions as the supervisor against native snapshots.
    #[test]
    #[ignore = "requires explicit owned native compositor fixtures"]
    fn live_native_association_and_mode_roundtrips() {
        use std::thread::sleep;
        use follow::Action;
        let hypr = Hypr::connect().expect("explicit native compositor");
        let target = std::env::var("MIOS_SHADER_TARGET").expect("owned target address");
        let pane = std::env::var("MIOS_SHADER_PANE").expect("owned overlay address");
        let control = std::env::var("MIOS_SHADER_CONTROL").expect("owned negative control address");
        let read = |address: &str| -> Win {
            let clients = hypr.json("clients").unwrap();
            let v = clients.as_array().unwrap().iter()
                .find(|v| v["address"].as_str() == Some(address)).expect("fixture mapped");
            let w = Win::from_json(v).unwrap();
            assert!(w.title.starts_with("MiOS shader-state check "), "owned fixtures only");
            w
        };
        let tags = |address: &str| -> Vec<String> {
            let clients = hypr.json("clients").unwrap();
            clients.as_array().unwrap().iter().find(|v| v["address"].as_str() == Some(address)).unwrap()
                ["tags"].as_array().unwrap().iter().filter_map(|t| t.as_str().map(str::to_owned)).collect()
        };
        let original = read(&target);
        let identity = WindowIdentity::from_window(&original);
        let wrong = read(&control);
        assert!(!identity.matches(&wrong), "wrong-window negative control");
        hypr.eval(&binding_lua(&pane, &target, true, true)).unwrap();
        assert!(tags(&target).contains(&format!("mios-shaderglass-pane:{pane}")));
        assert!(tags(&pane).contains(&format!("mios-shaderglass-target:{target}")));
        assert!(!tags(&control).iter().any(|t| t.starts_with("mios-shaderglass-")));

        let mut follower = Follower::new();
        let policy = follow::Policy {
            follow_hz: 0.0, idle: Duration::from_millis(100),
            settle: Duration::from_millis(50), on_move: follow::OnMove::Follow, close_with_target: true,
        };
        let mut synchronize = |suppressed: bool| {
            for _ in 0..5 {
                let t = read(&target);
                let p = read(&pane);
                assert!(identity.matches(&t), "native identity persists across presentation modes");
                let monitors = follow::monitors_from_json(&hypr.json("monitors").unwrap());
                let step = follower.step(&policy, Some(&t), Some(&p), &monitors, suppressed, Instant::now());
                assert!(!step.actions.iter().any(|a| matches!(a, Action::PlaceTarget(_))),
                    "mode/reflow must never write application geometry: {:?}", step.actions);
                if !step.actions.is_empty() {
                    hypr.eval(&follow::actions_lua(&pane, &target, &step.actions)).unwrap();
                }
                sleep(Duration::from_millis(80));
            }
            if !suppressed {
                let t = read(&target);
                let p = read(&pane);
                assert!(t.rect.same(&p.rect), "overlay follows native application geometry exactly: {:?} {:?}", t.rect, p.rect);
                assert_eq!(t.monitor, p.monitor, "overlay follows native output ownership");
            }
        };
        synchronize(false);
        let before = read(&target).rect;
        hypr.eval(&follow::actions_lua(&pane, &target, &[Action::Place(follow::Rect {
            x: before.x + 31.0, y: before.y + 29.0, w: before.w - 47.0, h: before.h - 41.0,
        })])).unwrap();
        sleep(Duration::from_millis(200));
        synchronize(false);
        assert!(read(&target).rect.close_to(&before), "pane-only native reflow did not resize target");
        println!("native pane reflow: target unchanged, overlay reconciled");

        for mode in ["maximized", "fullscreen"] {
            for action in ["set", "unset"] {
                hypr.eval(&format!("hl.dispatch(hl.dsp.window.fullscreen({{window={},mode={},action={}}}))",
                    lua_str(&format!("address:{target}")), lua_str(mode), lua_str(action))).unwrap();
                sleep(Duration::from_millis(400));
                synchronize(false);
                assert!(tags(&target).contains(&format!("mios-shaderglass-pane:{pane}")));
                assert!(!tags(&control).iter().any(|t| t.starts_with("mios-shaderglass-")));
                println!("native mode {mode} {action}: identity/tags/output/geometry retained");
            }
        }
        synchronize(true);
        synchronize(false);
        assert!(read(&target).rect.close_to(&before), "suppression return preserves application rect");

        hypr.eval(&format!("hl.dispatch(hl.dsp.window.move({{window={},workspace='special:mios-shader-state-minimized',follow=false}}))",
            lua_str(&format!("address:{target}")))).unwrap();
        sleep(Duration::from_millis(200));
        let hidden = read(&target);
        assert!(!follow::on_screen(&hidden, &follow::monitors_from_json(&hypr.json("monitors").unwrap())));
        // No pane->target writes are allowed when the associated target is minimized.
        let step = Follower::new().step(&policy, Some(&hidden), Some(&read(&pane)),
            &follow::monitors_from_json(&hypr.json("monitors").unwrap()), false, Instant::now());
        assert!(step.actions.contains(&Action::Hide));
        hypr.eval(&follow::actions_lua(&pane, &target, &step.actions)).unwrap();
        hypr.eval(&format!("hl.dispatch(hl.dsp.window.move({{window={},workspace={},follow=false}}))",
            lua_str(&format!("address:{target}")), lua_str(&original.workspace_name))).unwrap();
        sleep(Duration::from_millis(200));
        synchronize(false);
        hypr.eval(&binding_lua(&pane, &target, false, true)).unwrap();
        assert!(!tags(&target).iter().any(|t| t.starts_with("mios-shaderglass-")));
        assert!(!tags(&pane).iter().any(|t| t.starts_with("mios-shaderglass-")));
        assert_eq!(read(&control), wrong, "negative control untouched through all modes");
        println!("native minimize/restore/suppression and wrong-window control passed");
    }

    #[test]
    fn vendor_ssot_parses_and_enables_the_installed_overlay() {
        let cfg = Config::from_ssot(&vendor()).unwrap();
        assert!(cfg.enabled, "the installed vendor overlay is enabled");
        assert_eq!(cfg.channel, Channel::Newest);
        assert!(cfg.asset_pattern.is_match("ShaderGlass-1.3.1-beta-win-x64.zip"));
        assert!(cfg.asset_pattern.is_match("ShaderGlass-1.2.3.1-win-x64.zip"));
        assert!(!cfg.asset_pattern.is_match("ShaderGlass-1.3.1-win-arm64.zip"));
        assert!(cfg.window.class.is_match("shaderglass.exe") && !cfg.window.class.is_match("xshaderglass.exe"));
        assert!(cfg.window.title.is_match("ShaderGlass") && cfg.window.title.is_match("ShaderGlass (kitty, crt-geom, 1px, Free, ~1:1, 60fps)"));
        assert!(!cfg.window.title.is_match("Shader Parameters"));
        assert!(cfg.window.tools.is_match("Choose Shader") && cfg.window.tools.is_match("Shader Parameters: crt-geom"));
        assert_eq!(cfg.launch_args, ["-f", "-input", "Choose..."]);
        assert_eq!(cfg.profile.first().map(|(k, _)| k.as_str()), Some("ProfileVersion"));
        assert_eq!(cfg.capture.source_types, "window");
        assert_eq!(unit_name(&cfg), "app-mios-org.mios.ShaderGlass@overlay.service");
        assert_eq!(cfg.xdph.custom_picker_binary, "/usr/libexec/mios/mios-share-picker");
        assert_eq!((cfg.wine.d3d.clone(), cfg.wine.gpu.clone()), (D3d::Dxvk, Gpu::Compositor));
    }

    #[test]
    fn invalid_ssot_values_are_rejected() {
        let base = vendor();
        let set = |path: &[&str], v: Value| {
            let mut d = base.clone();
            let mut cur = &mut d;
            for p in &path[..path.len() - 1] {
                cur = &mut cur[*p];
            }
            cur.as_table_mut().unwrap().insert(path[path.len() - 1].to_owned(), v);
            d
        };
        let s = |x: &str| Value::String(x.into());
        let sg = |k: &'static str| vec!["desktop", "shaderglass", k];
        let cases: Vec<(Vec<&str>, Value, &str)> = vec![
            (sg("channel"), s("nightly"), "channel"),
            (sg("asset_pattern"), s("ShaderGlass.*zip"), "anchored"),
            (sg("install_dir"), s("usr/lib/x"), "absolute"),
            (sg("app_id"), s("org-mios-sg"), "reverse-DNS"),
            (sg("memory_max"), s("lots"), "memory_max"),
            (sg("install_files"), Value::Array(vec![s("LICENSE")]), "exe and LICENSE"),
            (sg("install_files"), Value::Array(vec![s("ShaderGlass.exe"), s("LICENSE"), s("../evil")]), "plain root"),
            (vec!["desktop", "shaderglass", "wine", "prefix"], s("/abs/prefix"), "relative"),
            (vec!["desktop", "shaderglass", "wine", "driver"], s("gdi"), "driver"),
            (vec!["desktop", "shaderglass", "wine", "gpu"], s("card2"), "gpu"),
            (vec!["desktop", "shaderglass", "window", "main_title"], s("ShaderGlass ("), "anchored"),
            (vec!["desktop", "shaderglass", "window", "class"], s("shaderglass"), "anchored"),
            (vec!["desktop", "shaderglass", "window", "passthrough_tag"], s("mios-shaderglass"), "differ"),
            (vec!["desktop", "shaderglass", "follow", "on_move"], s("jump"), "on_move"),
            (vec!["desktop", "shaderglass", "follow", "idle_poll_ms"], Value::Integer(1), "idle_poll_ms"),
            (vec!["desktop", "shaderglass", "capture", "source_types"], s("window,screen"), "source_types"),
            (vec!["desktop", "shaderglass", "capture", "picker"], s("auto"), "picker"),
            (vec!["desktop", "shaderglass", "winecap", "patches"], Value::Array(vec![s("/etc/x.patch")]), "repository-relative"),
            (vec!["desktop", "shaderglass", "profile", "CaptureWindow"], s("kitty"), "CaptureWindow"),
            (vec!["desktop", "hyprland", "xdph", "config"], s("/etc/xdph.conf"), "XDG_CONFIG_HOME"),
            (vec!["desktop", "shaderglass", "state", "directory"], s("/var/lib/sg"), "relative path under $XDG_STATE_HOME"),
            (vec!["desktop", "shaderglass", "state", "directory"], s("mios/../x"), "relative path under $XDG_STATE_HOME"),
            (vec!["desktop", "shaderglass", "state", "save_timeout_ms"], Value::Integer(10), "save_timeout_ms"),
            (vec!["desktop", "shaderglass", "state", "restart_limit"], Value::Integer(99), "restart_limit"),
            (vec!["desktop", "shaderglass", "state", "persist"], s("yes"), "persist"),
            (vec!["desktop", "shaderglass", "state", "strip_keys"], Value::Array(vec![s("InputArea")]), "CaptureWindow"),
            (vec!["desktop", "shaderglass", "state", "strip_keys"], Value::Array(vec![s("Capture Window")]), "mnemonics"),
            (vec!["desktop", "shaderglass", "launch", "cycle_shortcut"], s("m"), "must differ"),
        ];
        for (path, v, needle) in cases {
            let e = Config::from_ssot(&set(&path, v)).expect_err(needle);
            assert!(e.contains(needle), "{path:?}: expected {needle:?} in {e}");
        }
        let mut missing = base.clone();
        missing["desktop"].as_table_mut().unwrap().remove("shaderglass");
        assert!(Config::from_ssot(&missing).unwrap_err().contains("desktop.shaderglass"));
    }

    #[test]
    fn vendor_state_policy_persists_profiles_and_strips_the_capture_source() {
        let cfg = Config::from_ssot(&vendor()).unwrap();
        assert!(cfg.state.persist && cfg.state.resume);
        assert_eq!(cfg.state.directory, "mios/shaderglass");
        assert_eq!(cfg.state.restart_limit, 2);
        for k in ["CaptureWindow", "CaptureDesktop", "CaptureDevice", "CaptureFormat", "InputArea", "CroppedArea"] {
            assert!(cfg.state.strip_keys.iter().any(|x| x == k), "{k}");
        }
        assert_eq!((cfg.setup_shortcut.as_str(), cfg.cycle_shortcut.as_str()), ("m", "n"));
        let mut missing = vendor();
        missing["desktop"]["shaderglass"].as_table_mut().unwrap().remove("state");
        assert!(Config::from_ssot(&missing).unwrap_err().contains("desktop.shaderglass.state"));
    }

    #[test]
    fn only_a_pane_that_died_on_its_own_is_relaunched() {
        // requested_close, had_mapped, restarts, limit, stopping
        assert!(relaunch_decision(false, true, 0, 2, false), "a crash of a working pane relaunches");
        assert!(relaunch_decision(false, true, 1, 2, false));
        assert!(!relaunch_decision(false, true, 2, 2, false), "budget spent");
        assert!(!relaunch_decision(true, true, 0, 2, false), "detach / target closed / stop never relaunch");
        assert!(!relaunch_decision(false, false, 0, 2, false), "a pane that never mapped is not retried forever");
        assert!(!relaunch_decision(false, true, 0, 2, true), "never while the supervisor stops");
        assert!(!relaunch_decision(false, true, 0, 0, false), "restart_limit 0");
    }

    #[test]
    fn each_session_runs_its_own_supervisor_unit() {
        assert_eq!(unit_instance(true, Path::new("/run/user/1000")), "overlay");
        let a = unit_instance(false, Path::new("/run/user/1000/mios-nested-desktop-1"));
        let b = unit_instance(false, Path::new("/run/user/1000/mios-nested-desktop-2"));
        assert!(a.starts_with("overlay-") && a != b && a != "overlay");
        assert_eq!(a, unit_instance(false, Path::new("/run/user/1000/mios-nested-desktop-1")), "stable");
        for k in ["MIOS_VENDOR_TOML", "XDG_STATE_HOME", "XDG_DATA_HOME", "PIPEWIRE_RUNTIME_DIR", "DBUS_SESSION_BUS_ADDRESS", "HYPRLAND_INSTANCE_SIGNATURE"] {
            assert!(SUPERVISOR_ENV.contains(&k), "{k}");
        }
        assert!(!SUPERVISOR_ENV.contains(&"XDG_RUNTIME_DIR"), "added only outside the primary session");
    }

    #[test]
    fn only_the_retitled_main_window_becomes_a_pane() {
        let cfg = Config::from_ssot(&vendor()).unwrap();
        let w = &cfg.window;
        assert!(is_main_window(w, "shaderglass.exe", "ShaderGlass (no input selected)"));
        assert!(is_main_window(w, "shaderglass.exe", "ShaderGlass (kitty, crt-geom, 1px, free, ~1:1, 60fps)"));
        // Negative controls: ShaderGlass' message boxes and input dialog keep the bare title, its tool
        // windows have their own, and any other class is never a pane.
        assert!(!is_main_window(w, "shaderglass.exe", "ShaderGlass"));
        assert!(!is_main_window(w, "shaderglass.exe", "Choose Shader"));
        assert!(!is_main_window(w, "shaderglass.exe", "Shader Parameters: crt-geom"));
        assert!(!is_main_window(w, "kitty", "ShaderGlass (no input selected)"));
        // The static pane rules match the bare title too: they apply at map time, before the retitle.
        assert!(w.title.is_match("ShaderGlass"));
    }

    #[test]
    fn a_main_window_goes_to_the_pane_whose_process_it_is() {
        assert_eq!(pane_for_window(&[(0, 100), (1, 200)], 200, false), Some(1));
        assert_eq!(pane_for_window(&[(3, 100)], 999, true), Some(3), "another process of our unit, one pane launching");
        assert_eq!(pane_for_window(&[(3, 100)], 999, false), None, "a ShaderGlass outside the supervisor is never taken");
        assert_eq!(pane_for_window(&[(0, 100), (1, 200)], 999, true), None, "ambiguous");
        assert_eq!(pane_for_window(&[], 100, true), None);
        assert!(!in_my_cgroup(-1) && !in_my_cgroup(0));
        assert!(in_my_cgroup(i64::from(std::process::id())));
    }

    #[test]
    fn dxvk_renders_on_the_compositors_gpu_unless_the_ssot_says_otherwise() {
        let d = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(d.path().join("card2/device")).unwrap();
        std::fs::write(d.path().join("card2/device/vendor"), "0x10de\n").unwrap();
        let mut cfg = Config::from_ssot(&vendor()).unwrap();
        let aq = Some("/dev/dri/card2:/dev/dri/card1");
        assert_eq!(wine::dxvk_filter(&cfg, aq, d.path()), Some("NVIDIA"));
        assert_eq!(wine::dxvk_filter(&cfg, None, d.path()), None, "no AQ_DRM_DEVICES: DXVK chooses");
        cfg.wine.gpu = Gpu::Any;
        assert_eq!(wine::dxvk_filter(&cfg, aq, d.path()), None);
        cfg.wine.gpu = Gpu::Compositor;
        cfg.wine.d3d = D3d::Wined3d;
        assert_eq!(wine::dxvk_filter(&cfg, aq, d.path()), None, "wined3d has no device filter");
    }

    #[test]
    fn supervisor_unit_is_one_capped_app_service() {
        let cfg = Config::from_ssot(&vendor()).unwrap();
        let a = supervisor_unit_args(&cfg, Path::new("/usr/bin/miosd"), &[("DISPLAY".into(), ":0".into())]);
        let joined = a.join(" ");
        assert!(joined.starts_with(&format!("--user --collect --quiet --unit {}", unit_name(&cfg))), "{joined}");
        assert!(unit_name(&cfg).starts_with("app-mios-org.mios.ShaderGlass@overlay"), "{}", unit_name(&cfg));
        for p in ["-p MemoryMax=4G", "-p KillMode=mixed", "-p OOMPolicy=continue", "--setenv=DISPLAY=:0"] {
            assert!(joined.contains(p), "{p}: {joined}");
        }
        assert!(joined.contains("-p Restart=on-failure"), "a crashed supervisor comes back to resume: {joined}");
        assert!(joined.contains("-p StartLimitBurst=3"), "a supervisor that cannot start is not restarted forever: {joined}");
        let mut no_resume = cfg.clone();
        no_resume.state.resume = false;
        assert!(!supervisor_unit_args(&no_resume, Path::new("/usr/bin/miosd"), &[]).join(" ").contains("Restart="), "resume = false: never restarted");
        assert!(joined.ends_with("-- /usr/bin/miosd shaderglass serve"), "{joined}");
    }

    #[test]
    fn cli_arguments_parse_as_the_keybinds_and_xdph_pass_them() {
        use clap::Parser;
        #[derive(clap::Parser, Debug)]
        struct Cli {
            #[command(subcommand)]
            cmd: ShaderglassCommand,
        }
        let p = |v: &[&str]| Cli::try_parse_from(std::iter::once("shaderglass").chain(v.iter().copied()));
        assert!(matches!(p(&["toggle"]).unwrap().cmd, ShaderglassCommand::Toggle));
        assert!(matches!(p(&["setup"]).unwrap().cmd, ShaderglassCommand::Setup { window: None }));
        assert!(matches!(p(&["cycle"]).unwrap().cmd, ShaderglassCommand::Cycle { window: None }));
        assert!(matches!(p(&["next", "--window", "0x2"]).unwrap().cmd, ShaderglassCommand::Cycle { window: Some(_) }));
        assert!(matches!(p(&["save"]).unwrap().cmd, ShaderglassCommand::Save { window: None }));
        assert!(matches!(p(&["resume"]).unwrap().cmd, ShaderglassCommand::Resume));
        assert!(matches!(p(&["start", "--window", "0xab"]).unwrap().cmd, ShaderglassCommand::Attach { window: Some(ref w), setup: false } if w == "0xab"));
        assert!(matches!(p(&["attach", "--setup"]).unwrap().cmd, ShaderglassCommand::Attach { setup: true, .. }));
        assert!(matches!(p(&["detach", "--window", "0x1"]).unwrap().cmd, ShaderglassCommand::Detach { window: Some(_) }));
        match p(&["pick", "--allow-token"]).unwrap().cmd {
            ShaderglassCommand::Pick { args } => assert_eq!(args, ["--allow-token"]),
            other => panic!("{other:?}"),
        }
        assert!(matches!(p(&["pick"]).unwrap().cmd, ShaderglassCommand::Pick { ref args } if args.is_empty()));
        assert!(matches!(p(&["update", "--dry-run"]).unwrap().cmd, ShaderglassCommand::Install { dry_run: true, .. }));
        assert!(p(&["install", "--from-stage", "/x", "--archive", "/y"]).is_err(), "the root step takes no network inputs");
        assert!(p(&["attach", "--window"]).is_err());
        assert!(p(&["frobnicate"]).is_err());
    }

    #[test]
    fn atomic_private_writes() {
        use std::os::unix::fs::PermissionsExt;
        let d = tempfile::tempdir().unwrap();
        let f = d.path().join("x");
        write_private(&f, b"one").unwrap();
        write_private(&f, b"two").unwrap();
        assert_eq!(std::fs::read(&f).unwrap(), b"two");
        assert_eq!(std::fs::metadata(&f).unwrap().permissions().mode() & 0o777, 0o600);
        assert_eq!(std::fs::read_dir(d.path()).unwrap().count(), 1, "no temp file left behind");
    }
}
