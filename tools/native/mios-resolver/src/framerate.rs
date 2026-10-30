// AI-hint: Desktop frame-rate policy ([desktop.framerate]) -- ONE pure table + precedence shared by `miosd framerate`
//          (runtime tier), `mios-gen render-hyprland` (login projection, room-toggle overlay) and the tests: inputs ->
//          tier -> desktop rate D (2D compositor + Hypr3D), glass rate G = D / glass_divisor (Hyprglass re-sampling,
//          Hypr3D refraction/reflection), wallpaper rate W = D / wallpaper_divisor (living wallpaper, Hypr3D dome).
//          Also the host readers both runtimes need (tuned-ppd profile map, sysfs mains/battery) and the
//          $XDG_RUNTIME_DIR state file every consumer reads (miosd writes it; Lua, Host.qml, the room toggle read it).
// AI-related: usr/share/mios/mios.toml [desktop.framerate], src/mios-rs/miosd/src/framerate.rs,
// AI-related: tools/native/mios-gen/src/render.rs (framerate_lua, toggle_3d), usr/share/mios/wallpaper/Host.qml
//! Operator policy (2026-10-23 20:15 / 20:20): performance 90, balanced/power-saver/battery 60, gaming 120,
//! unlocked = no cap (the output's own maximum, an explicit opt-in "at risk"); glass at 1/3 of the desktop rate
//! (1/2 for gaming and unlocked); the wallpaper at 1/2 of the desktop rate except unlocked (full rate).
//! Precedence, first match wins: unlocked > gaming > on battery > power profile.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use serde::{Deserialize, Serialize};
use toml::Value;

/// `[desktop.framerate].modes`: only the modes the output advertises (EDID).
pub const MODES_ADVERTISED: &str = "advertised";
/// `[desktop.framerate].modes`: the advertised modes plus extended-vblank timings derived from the EDID.
pub const MODES_EXTENDED_VBLANK: &str = "extended-vblank";

/// Every tier the policy table must define, in precedence order (the power-profile tiers last).
pub const TIERS: [&str; 6] = ["unlocked", "gaming", "battery", "performance", "balanced", "power-saver"];
/// The tiers a power profile may map to: never one of the opt-in or signal tiers.
pub const PROFILE_TIERS: [&str; 3] = ["performance", "balanced", "power-saver"];
/// The power-profiles-daemon profile names (net.hadess.PowerProfiles ActiveProfile).
pub const PPD_PROFILES: [&str; 3] = ["power-saver", "balanced", "performance"];

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "kebab-case")]
pub enum Mechanism {
    /// VRR cap where the output runs VRR and the pacing plugin is loaded, else the nearest advertised mode.
    Auto,
    /// Only ever the compositor cap: an output without active VRR keeps its mode (and is not capped).
    Cap,
    /// Only ever advertised modes (a modeset per tier change).
    Mode,
}

impl Mechanism {
    pub fn as_str(self) -> &'static str {
        match self {
            Mechanism::Auto => "auto",
            Mechanism::Cap => "cap",
            Mechanism::Mode => "mode",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Tier {
    /// The desktop rate in Hz; 0 = the output's own maximum (no cap).
    pub desktop_hz: u32,
    pub glass_divisor: u32,
    pub wallpaper_divisor: u32,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Gaming {
    pub gamemode: bool,
    pub fullscreen_game: bool,
    pub game_class: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Policy {
    pub enabled: bool,
    pub unlocked: bool,
    pub debounce_ms: u64,
    pub self_check_s: u64,
    pub gaming_exit_hold_ms: u64,
    pub mechanism: Mechanism,
    pub modes: String,
    /// Outputs that hold D with a display mode even while VRR and the cap are available (`mechanism = "mode"` for
    /// these outputs only; e.g. DP-3 for a real 90 Hz scanout). Absent = none.
    pub mode_outputs: Vec<String>,
    /// An advertised mode within this many Hz of D is used as it is; further away, `modes = "extended-vblank"` derives
    /// a timing for D, whose rounding must land within it too. Absent = 0.5 (the modeset verification tolerance).
    pub mode_tolerance_hz: f64,
    pub revert_timeout_ms: u64,
    /// A verified modeset to a mode the operator has not confirmed on that panel is restored after this many seconds
    /// unless `miosd framerate confirm-mode` confirms it (0 = no confirmation; the baseline mode never needs one).
    pub mode_confirm_s: u64,
    pub modeset_hold_ms: u64,
    pub modeset_min_interval_s: u64,
    pub exclude_prefixes: Vec<String>,
    pub pacing_plugin: String,
    pub state_file: String,
    pub user_fragment: String,
    /// power profile -> tier
    pub profiles: BTreeMap<String, String>,
    pub unknown_profile: String,
    pub gaming: Gaming,
    pub tiers: BTreeMap<String, Tier>,
}

/// The live signals the tier is chosen from (unlocked is the merged SSOT's `unlocked`).
#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize, Deserialize)]
pub struct Inputs {
    pub gaming: bool,
    pub on_battery: bool,
    /// power-profiles-daemon profile name, None when no profile daemon answers.
    pub profile: Option<String>,
}

/// One resolved plan: the desktop rate D (effective, capped at the output maximum), the cap the compositor plugin
/// holds (0 = none), G and W.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct Rates {
    pub tier: String,
    pub desktop_hz: u32,
    pub cap_hz: u32,
    pub glass_fps: u32,
    pub wallpaper_fps: u32,
}

fn get<'a>(v: &'a Value, path: &str) -> Option<&'a Value> {
    path.split('.').try_fold(v, |cur, key| cur.get(key))
}

fn int(t: &Value, key: &str, lo: i64, hi: i64) -> Result<u64, String> {
    match t.get(key).and_then(Value::as_integer) {
        Some(i) if (lo..=hi).contains(&i) => Ok(i as u64),
        _ => Err(format!("[desktop.framerate] {key} must be an integer in {lo}..={hi}")),
    }
}

fn boolean(t: &Value, key: &str) -> Result<bool, String> {
    t.get(key)
        .and_then(Value::as_bool)
        .ok_or_else(|| format!("[desktop.framerate] {key} must be a boolean"))
}

/// A short file name or relative path without `..`, control bytes or a leading `/`.
fn rel_path(t: &Value, key: &str) -> Result<String, String> {
    t.get(key)
        .and_then(Value::as_str)
        .filter(|s| {
            !s.is_empty()
                && !s.starts_with('/')
                && s.split('/').all(|c| !c.is_empty() && c != "." && c != "..")
                && s.bytes().all(|b| b.is_ascii_alphanumeric() || b"-_./".contains(&b))
        })
        .map(str::to_owned)
        .ok_or_else(|| format!("[desktop.framerate] {key} must be a relative path of [A-Za-z0-9._/-] without .."))
}

impl Policy {
    /// `[desktop.framerate]` of a merged SSOT: Ok(None) when the section is absent, an error naming the key when
    /// any value is out of range (a wrong policy must never half-apply).
    pub fn from_ssot(doc: &Value) -> Result<Option<Policy>, String> {
        let Some(t) = get(doc, "desktop.framerate") else {
            return Ok(None);
        };
        if !t.is_table() {
            return Err("[desktop.framerate] must be a table".into());
        }
        let mechanism = match t.get("mechanism").and_then(Value::as_str) {
            Some("auto") => Mechanism::Auto,
            Some("cap") => Mechanism::Cap,
            Some("mode") => Mechanism::Mode,
            _ => return Err("[desktop.framerate] mechanism must be auto | cap | mode".into()),
        };
        let modes = match t.get("modes").and_then(Value::as_str) {
            Some(m @ (MODES_ADVERTISED | MODES_EXTENDED_VBLANK)) => m.to_owned(),
            _ => return Err("[desktop.framerate] modes must be \"advertised\" (only EDID-advertised modes) or \"extended-vblank\" (also the fastest EDID timing slowed to D by a longer vertical front porch)".into()),
        };
        let none = Value::Array(Vec::new());
        let prefixes = match t.get("exclude_prefixes") {
            Some(v) => v,
            None => get(doc, "desktop.displays.ignore_prefixes").unwrap_or(&none),
        };
        let exclude_prefixes = prefixes
            .as_array()
            .ok_or("[desktop.framerate] exclude_prefixes must be an array of output-name prefixes")?
            .iter()
            .map(|p| {
                p.as_str()
                    .filter(|s| !s.is_empty() && s.bytes().all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b)))
                    .map(str::to_owned)
                    .ok_or_else(|| "[desktop.framerate] exclude_prefixes entries must be [A-Za-z0-9._-] strings".to_string())
            })
            .collect::<Result<Vec<_>, _>>()?;
        let mode_outputs = match t.get("mode_outputs") {
            None => Vec::new(),
            Some(v) => v
                .as_array()
                .ok_or("[desktop.framerate] mode_outputs must be an array of output names")?
                .iter()
                .map(|o| {
                    o.as_str()
                        .filter(|s| !s.is_empty() && s.bytes().all(|b| b.is_ascii_alphanumeric() || b"-_.".contains(&b)))
                        .map(str::to_owned)
                        .ok_or_else(|| "[desktop.framerate] mode_outputs entries must be [A-Za-z0-9._-] output names".to_string())
                })
                .collect::<Result<Vec<_>, _>>()?,
        };
        let mode_tolerance_hz = match t.get("mode_tolerance_hz") {
            None => 0.5,
            Some(v) => v
                .as_float()
                .or_else(|| v.as_integer().map(|i| i as f64))
                .filter(|f| f.is_finite() && (0.001..=5.0).contains(f))
                .ok_or("[desktop.framerate] mode_tolerance_hz must be a number in 0.001..=5")?,
        };
        let pacing_plugin = t
            .get("pacing_plugin")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty() && s.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-'))
            .ok_or("[desktop.framerate] pacing_plugin must name a [desktop.hyprland.<plugin>] entry")?
            .to_owned();
        let mut tiers = BTreeMap::new();
        let table = t.get("tiers").filter(|v| v.is_table()).ok_or("[desktop.framerate.tiers] is missing")?;
        for name in TIERS {
            let tier = table
                .get(name)
                .filter(|v| v.is_table())
                .ok_or_else(|| format!("[desktop.framerate.tiers.{name}] is missing"))?;
            let n = |key: &str, lo: i64, hi: i64| -> Result<u32, String> {
                match tier.get(key).and_then(Value::as_integer) {
                    Some(i) if (lo..=hi).contains(&i) => Ok(i as u32),
                    _ => Err(format!("[desktop.framerate.tiers.{name}] {key} must be an integer in {lo}..={hi}")),
                }
            };
            tiers.insert(
                name.to_owned(),
                Tier {
                    desktop_hz: n("desktop_hz", 0, 1000)?,
                    glass_divisor: n("glass_divisor", 1, 16)?,
                    wallpaper_divisor: n("wallpaper_divisor", 1, 16)?,
                },
            );
        }
        for extra in table.as_table().into_iter().flat_map(|m| m.keys()) {
            if !TIERS.contains(&extra.as_str()) {
                return Err(format!("[desktop.framerate.tiers.{extra}] is not a tier ({})", TIERS.join(", ")));
            }
        }
        // 0 = the output maximum is only meaningful for the opt-in tier; a profile tier must name a rate.
        for name in TIERS.iter().filter(|n| **n != "unlocked") {
            if tiers[*name].desktop_hz == 0 {
                return Err(format!("[desktop.framerate.tiers.{name}] desktop_hz must be 1..=1000 (0 = no cap is the unlocked tier only)"));
            }
        }
        let map = t.get("profiles").and_then(Value::as_table).ok_or("[desktop.framerate.profiles] is missing")?;
        let mut profiles = BTreeMap::new();
        let mut unknown_profile = None;
        for (profile, tier) in map {
            let tier = tier
                .as_str()
                .filter(|s| PROFILE_TIERS.contains(s))
                .ok_or_else(|| format!("[desktop.framerate.profiles] {profile} must map to one of {}", PROFILE_TIERS.join(", ")))?;
            if profile == "unknown" {
                unknown_profile = Some(tier.to_owned());
            } else if PPD_PROFILES.contains(&profile.as_str()) {
                profiles.insert(profile.clone(), tier.to_owned());
            } else {
                return Err(format!("[desktop.framerate.profiles] {profile} is not a power profile ({} or unknown)", PPD_PROFILES.join(", ")));
            }
        }
        for profile in PPD_PROFILES {
            if !profiles.contains_key(profile) {
                return Err(format!("[desktop.framerate.profiles] {profile} is missing"));
            }
        }
        let unknown_profile = unknown_profile.ok_or("[desktop.framerate.profiles] unknown is missing")?;
        let g = t.get("gaming").filter(|v| v.is_table()).ok_or("[desktop.framerate.gaming] is missing")?;
        let game_class = g
            .get("game_class")
            .and_then(Value::as_str)
            .filter(|s| !s.is_empty() && !s.chars().any(char::is_control))
            .ok_or("[desktop.framerate.gaming] game_class must be a non-empty regex")?
            .to_owned();
        let gaming = Gaming {
            gamemode: g.get("gamemode").and_then(Value::as_bool).ok_or("[desktop.framerate.gaming] gamemode must be a boolean")?,
            fullscreen_game: g
                .get("fullscreen_game")
                .and_then(Value::as_bool)
                .ok_or("[desktop.framerate.gaming] fullscreen_game must be a boolean")?,
            game_class,
        };
        Ok(Some(Policy {
            enabled: boolean(t, "enabled")?,
            unlocked: boolean(t, "unlocked")?,
            debounce_ms: int(t, "debounce_ms", 0, 60_000)?,
            self_check_s: int(t, "self_check_s", 5, 3600)?,
            gaming_exit_hold_ms: int(t, "gaming_exit_hold_ms", 0, 60_000)?,
            mechanism,
            modes,
            mode_outputs,
            mode_tolerance_hz,
            revert_timeout_ms: int(t, "revert_timeout_ms", 1000, 60_000)?,
            mode_confirm_s: int(t, "mode_confirm_s", 0, 600)?,
            modeset_hold_ms: int(t, "modeset_hold_ms", 0, 600_000)?,
            modeset_min_interval_s: int(t, "modeset_min_interval_s", 0, 3600)?,
            exclude_prefixes,
            pacing_plugin,
            state_file: rel_path(t, "state_file")?,
            user_fragment: rel_path(t, "user_fragment")
                .and_then(|f| if f.contains('/') || !f.ends_with(".toml") { Err("[desktop.framerate] user_fragment must be a *.toml file name".into()) } else { Ok(f) })?,
            profiles,
            unknown_profile,
            gaming,
            tiers,
        }))
    }

    /// The tier for these inputs. Precedence, first match wins: unlocked > gaming > on battery > power profile
    /// (an unknown or absent profile maps through `profiles.unknown`).
    pub fn tier_for(&self, inputs: &Inputs) -> String {
        if self.unlocked {
            "unlocked".into()
        } else if inputs.gaming {
            "gaming".into()
        } else if inputs.on_battery {
            "battery".into()
        } else {
            inputs
                .profile
                .as_deref()
                .and_then(|p| self.profiles.get(p))
                .unwrap_or(&self.unknown_profile)
                .clone()
        }
    }

    /// The rates of `tier` for outputs whose largest refresh is `max_hz` (None = unknown). D is the tier's rate capped
    /// at the maximum; the unlocked tier (desktop_hz 0) is the maximum itself and caps nothing. G = D / glass_divisor
    /// and W = D / wallpaper_divisor, rounded down. With an unknown maximum the unlocked tier reports 0 for D, G and W:
    /// "every output frame" (the consumers' uncapped value) until the outputs are known.
    pub fn rates(&self, tier: &str, max_hz: Option<u32>) -> Rates {
        let t = self.tiers.get(tier).copied().unwrap_or(self.tiers[&self.unknown_profile]);
        let max = max_hz.filter(|m| *m > 0);
        let desktop = match (t.desktop_hz, max) {
            (0, m) => m.unwrap_or(0),
            (d, Some(m)) => d.min(m),
            (d, None) => d,
        };
        Rates {
            tier: tier.to_owned(),
            desktop_hz: desktop,
            cap_hz: t.desktop_hz,
            glass_fps: desktop / t.glass_divisor,
            wallpaper_fps: desktop / t.wallpaper_divisor,
        }
    }

    pub fn plan(&self, inputs: &Inputs, max_hz: Option<u32>) -> Rates {
        self.rates(&self.tier_for(inputs), max_hz)
    }

    /// `modes = "extended-vblank"`: on the mode path a rate with no advertised mode within the verification
    /// tolerance gets the fastest EDID timing at the output's size with a longer vertical front porch
    /// (crate::edid_timing::extended_vblank), the timing VRR itself produces.
    pub fn extended_vblank(&self) -> bool {
        self.modes == MODES_EXTENDED_VBLANK
    }

    /// `output` holds D with a display mode whatever its VRR state (`mode_outputs`).
    pub fn mode_output(&self, output: &str) -> bool {
        self.mode_outputs.iter().any(|o| o == output)
    }

    /// True when `output` is left alone (a virtual/remote output: no cap, no modeset).
    pub fn excluded(&self, output: &str) -> bool {
        self.exclude_prefixes.iter().any(|p| output.starts_with(p.as_str()))
    }

    pub fn state_path(&self, runtime_dir: &Path) -> PathBuf {
        runtime_dir.join(&self.state_file)
    }
}

/// A refresh rate in Hz as a whole number (143.998 -> 144, 119.98 -> 120, 59.94 -> 60).
pub fn whole_hz(hz: f64) -> u32 {
    if hz.is_finite() && hz > 0.0 {
        hz.round().min(10_000.0) as u32
    } else {
        0
    }
}

// ---------------------------------------------------------------- power profile and battery (host readers)

/// The power-profiles-daemon name tuned-ppd maps a TuneD profile to (/etc/tuned/ppd.conf [profiles], and [battery]
/// for the on-battery variants), None when the profile is not mapped.
pub fn ppd_profile_for_tuned(ppd_conf: &str, tuned_profile: &str) -> Option<String> {
    let mut section = String::new();
    let mut battery = None;
    for line in ppd_conf.lines() {
        let line = line.trim();
        if line.starts_with('#') || line.starts_with(';') || line.is_empty() {
            continue;
        }
        if let Some(name) = line.strip_prefix('[').and_then(|l| l.strip_suffix(']')) {
            section = name.trim().to_owned();
            continue;
        }
        let Some((key, value)) = line.split_once('=') else { continue };
        let (key, value) = (key.trim(), value.trim());
        if value != tuned_profile || !PPD_PROFILES.contains(&key) {
            continue;
        }
        match section.as_str() {
            "profiles" => return Some(key.to_owned()),
            "battery" => battery = battery.or_else(|| Some(key.to_owned())),
            _ => {}
        }
    }
    battery
}

/// The power profile at login without D-Bus: tuned-ppd's persisted base profile
/// (`<root>/etc/tuned/ppd_base_profile`), None when absent or not a PPD name.
pub fn login_profile(root: &Path) -> Option<String> {
    let text = std::fs::read_to_string(root.join("etc/tuned/ppd_base_profile")).ok()?;
    let p = text.trim();
    PPD_PROFILES.contains(&p).then(|| p.to_owned())
}

#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct PowerSupply {
    /// sysfs `type`: Mains, Battery, USB, UPS, ...
    pub kind: String,
    /// sysfs `scope`: System (or absent) for the machine's own supply, Device for peripherals (controllers, mice).
    pub scope: String,
    pub online: Option<bool>,
    pub status: String,
}

/// UPower's OnBattery from sysfs: a system battery is discharging and no mains/USB supply is online. Device-scope
/// batteries (game controllers, mice) never count.
pub fn on_battery(supplies: &[PowerSupply]) -> bool {
    let system = |s: &&PowerSupply| !s.scope.eq_ignore_ascii_case("device");
    let mains_online = supplies
        .iter()
        .filter(system)
        .any(|s| matches!(s.kind.as_str(), "Mains" | "USB" | "UPS") && s.online == Some(true));
    let discharging = supplies
        .iter()
        .filter(system)
        .any(|s| s.kind == "Battery" && s.status.eq_ignore_ascii_case("discharging"));
    discharging && !mains_online
}

/// Every supply under `<root>/sys/class/power_supply`.
pub fn read_power_supplies(root: &Path) -> Vec<PowerSupply> {
    let dir = root.join("sys/class/power_supply");
    let read = |p: PathBuf| std::fs::read_to_string(p).map(|s| s.trim().to_owned()).unwrap_or_default();
    let mut out: Vec<PowerSupply> = std::fs::read_dir(&dir)
        .into_iter()
        .flatten()
        .flatten()
        .map(|e| {
            let p = e.path();
            PowerSupply {
                kind: read(p.join("type")),
                scope: read(p.join("scope")),
                online: match read(p.join("online")).as_str() {
                    "1" => Some(true),
                    "0" => Some(false),
                    _ => None,
                },
                status: read(p.join("status")),
            }
        })
        .collect();
    out.sort_by(|a, b| (&a.kind, &a.scope).cmp(&(&b.kind, &b.scope)));
    out
}

/// The login inputs (no D-Bus yet, gaming never at login): tuned-ppd's base profile and sysfs battery state.
pub fn login_inputs(root: &Path) -> Inputs {
    Inputs {
        gaming: false,
        on_battery: on_battery(&read_power_supplies(root)),
        profile: login_profile(root),
    }
}

// ---------------------------------------------------------------- the runtime state file

/// Per-output part of the state: how D is held there.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct OutputState {
    /// vrr-cap | mode | uncapped | excluded
    pub mechanism: String,
    /// the cap the plugin holds there (0 = none)
    pub cap_hz: u32,
    /// the largest advertised refresh at the output's resolution
    pub max_hz: u32,
    /// the mode the daemon switched it to (mechanism = mode), "WxH@Hz"
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub mode: Option<String>,
    /// the refresh the output reports now
    pub refresh_hz: f64,
}

/// `$XDG_RUNTIME_DIR/<state_file>`: written atomically by `miosd framerate serve` after every apply and removed when
/// it stops. Top-level scalars stay flat (`tier`, `desktop_hz`, `cap_hz`, `glass_fps`, `wallpaper_fps`, `pid`) and the
/// mode of each re-moded output is repeated as a flat `"mode.<output>": "WxH@Hz"` key, so the login Lua and Host.qml
/// read it with plain pattern matches.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct State {
    pub version: u32,
    pub pid: u32,
    pub seq: u64,
    pub tier: String,
    pub desktop_hz: u32,
    pub cap_hz: u32,
    pub glass_fps: u32,
    pub wallpaper_fps: u32,
    pub max_hz: u32,
    pub unlocked: bool,
    /// The compositor holds the wallpaper's frame callbacks to `wallpaper_fps` (mios-pacing wallpaper_fps set and its
    /// frame hook live): Host.qml then steps once per presented frame instead of on its own timer, so each commit
    /// carries a new step (Qt's layer update otherwise commits every step twice).
    #[serde(default)]
    pub wallpaper_paced: bool,
    pub inputs: Inputs,
    pub outputs: BTreeMap<String, OutputState>,
}

pub const STATE_VERSION: u32 = 1;

impl State {
    pub fn to_json(&self) -> String {
        let mut v = serde_json::to_value(self).expect("state serializes");
        if let Some(m) = v.as_object_mut() {
            for (name, o) in &self.outputs {
                if let Some(mode) = &o.mode {
                    m.insert(format!("mode.{name}"), serde_json::Value::String(mode.clone()));
                }
            }
        }
        let mut s = serde_json::to_string_pretty(&v).expect("state serializes");
        s.push('\n');
        s
    }

    pub fn parse(text: &str) -> Result<State, String> {
        let s: State = serde_json::from_str(text).map_err(|e| format!("framerate state: {e}"))?;
        if s.version != STATE_VERSION {
            return Err(format!("framerate state version {} (expected {STATE_VERSION})", s.version));
        }
        Ok(s)
    }

    /// The state at `path` when its writer is still running (a `miosd` process with that pid); a file left by a
    /// daemon that died is stale and ignored.
    pub fn read_live(path: &Path, proc_root: &Path) -> Option<State> {
        let s = State::parse(&std::fs::read_to_string(path).ok()?).ok()?;
        let comm = std::fs::read_to_string(proc_root.join(format!("proc/{}/comm", s.pid))).ok()?;
        (comm.trim() == "miosd").then_some(s)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The vendor section as the SSOT carries it (kept in step with usr/share/mios/mios.toml by the python test).
    pub const VENDOR: &str = r#"
[desktop.displays]
ignore_prefixes = ["HEADLESS-"]
[desktop.framerate]
enabled = true
unlocked = false
debounce_ms = 750
self_check_s = 60
gaming_exit_hold_ms = 3000
mechanism = "auto"
modes = "advertised"
revert_timeout_ms = 5000
mode_confirm_s = 15
modeset_hold_ms = 5000
modeset_min_interval_s = 30
pacing_plugin = "mios-pacing"
state_file = "mios/framerate/state.json"
user_fragment = "50-framerate.toml"
[desktop.framerate.profiles]
power-saver = "power-saver"
balanced = "balanced"
performance = "performance"
unknown = "balanced"
[desktop.framerate.gaming]
gamemode = true
fullscreen_game = false
game_class = '^steam_app_[0-9]+$'
[desktop.framerate.tiers.unlocked]
desktop_hz = 0
glass_divisor = 2
wallpaper_divisor = 1
[desktop.framerate.tiers.gaming]
desktop_hz = 120
glass_divisor = 2
wallpaper_divisor = 2
[desktop.framerate.tiers.battery]
desktop_hz = 60
glass_divisor = 3
wallpaper_divisor = 2
[desktop.framerate.tiers.performance]
desktop_hz = 90
glass_divisor = 3
wallpaper_divisor = 2
[desktop.framerate.tiers.balanced]
desktop_hz = 60
glass_divisor = 3
wallpaper_divisor = 2
[desktop.framerate.tiers.power-saver]
desktop_hz = 60
glass_divisor = 3
wallpaper_divisor = 2
"#;

    fn policy() -> Policy {
        Policy::from_ssot(&VENDOR.parse::<Value>().unwrap()).unwrap().unwrap()
    }

    fn inputs(profile: &str, battery: bool, gaming: bool) -> Inputs {
        Inputs { gaming, on_battery: battery, profile: Some(profile.into()) }
    }

    fn triple(r: &Rates) -> (u32, u32, u32) {
        (r.desktop_hz, r.glass_fps, r.wallpaper_fps)
    }

    #[test]
    fn mode_sources_advertised_and_extended_vblank() {
        assert!(!policy().extended_vblank(), "the vendor default applies only advertised (EDID) modes");
        let doc: Value = VENDOR.replace(r#"modes = "advertised""#, r#"modes = "extended-vblank""#).parse().unwrap();
        assert!(Policy::from_ssot(&doc).unwrap().unwrap().extended_vblank());
    }

    #[test]
    fn mode_outputs_and_tolerance_default_and_parse() {
        let p = policy();
        assert!(p.mode_outputs.is_empty() && !p.mode_output("DP-3"), "absent: no output is forced onto modes");
        assert_eq!(p.mode_tolerance_hz, 0.5);
        let doc: Value = VENDOR
            .replace(r#"modes = "advertised""#, "modes = \"extended-vblank\"\nmode_outputs = [\"DP-3\"]\nmode_tolerance_hz = 0.25")
            .parse()
            .unwrap();
        let q = Policy::from_ssot(&doc).unwrap().unwrap();
        assert!(q.mode_output("DP-3") && !q.mode_output("HDMI-A-1") && !q.mode_output("DP-"));
        assert_eq!(q.mode_tolerance_hz, 0.25);
        for bad in ["mode_outputs = \"DP-3\"", "mode_outputs = [\"\"]", "mode_outputs = [\"DP 3\"]", "mode_tolerance_hz = 0", "mode_tolerance_hz = 9.5", "mode_tolerance_hz = \"1\""] {
            let doc: Value = VENDOR.replace(r#"modes = "advertised""#, &format!("modes = \"advertised\"\n{bad}")).parse().unwrap();
            let e = Policy::from_ssot(&doc).unwrap_err();
            assert!(e.contains(bad.split(' ').next().unwrap()), "{bad}: {e}");
        }
    }

    #[test]
    fn operator_table_on_a_144_hz_output() {
        let p = policy();
        let max = Some(144);
        // tier: (D, G, W) -- DESIGN.md section 0
        assert_eq!(triple(&p.plan(&inputs("power-saver", false, false), max)), (60, 20, 30));
        assert_eq!(triple(&p.plan(&inputs("balanced", false, false), max)), (60, 20, 30));
        assert_eq!(triple(&p.plan(&inputs("performance", false, false), max)), (90, 30, 45));
        assert_eq!(triple(&p.plan(&inputs("performance", true, false), max)), (60, 20, 30));
        assert_eq!(triple(&p.plan(&inputs("power-saver", false, true), max)), (120, 60, 60));
        let mut u = p.clone();
        u.unlocked = true;
        let r = u.plan(&inputs("power-saver", true, true), max);
        assert_eq!((r.tier.as_str(), triple(&r), r.cap_hz), ("unlocked", (144, 72, 144), 0));
    }

    #[test]
    fn wallpaper_is_half_the_desktop_rate_except_unlocked() {
        let mut p = policy();
        for max in [60, 75, 100, 120, 144, 165, 240] {
            for (profile, battery, gaming) in [("power-saver", false, false), ("balanced", false, false), ("performance", false, false), ("balanced", true, false), ("balanced", false, true)] {
                let r = p.plan(&inputs(profile, battery, gaming), Some(max));
                assert_eq!(r.wallpaper_fps, r.desktop_hz / 2, "{profile} battery={battery} gaming={gaming} max={max}");
                assert_ne!(r.wallpaper_fps, r.desktop_hz, "negative control: a locked tier never runs the wallpaper at D");
            }
            p.unlocked = true;
            let r = p.plan(&Inputs::default(), Some(max));
            assert_eq!((r.wallpaper_fps, r.desktop_hz), (max, max), "unlocked: wallpaper at the full desktop rate");
            p.unlocked = false;
        }
    }

    #[test]
    fn glass_is_a_third_except_gaming_and_unlocked_at_half() {
        let mut p = policy();
        let r = p.plan(&inputs("performance", false, false), Some(144));
        assert_eq!(r.glass_fps * 3, r.desktop_hz);
        let r = p.plan(&inputs("performance", false, true), Some(144));
        assert_eq!(r.glass_fps * 2, r.desktop_hz);
        p.unlocked = true;
        let r = p.plan(&Inputs::default(), Some(165));
        assert_eq!((r.desktop_hz, r.glass_fps), (165, 82), "rounded down");
    }

    #[test]
    fn precedence_is_unlocked_gaming_battery_profile() {
        let mut p = policy();
        assert_eq!(p.tier_for(&inputs("performance", true, true)), "gaming");
        assert_eq!(p.tier_for(&inputs("performance", true, false)), "battery");
        assert_eq!(p.tier_for(&inputs("performance", false, false)), "performance");
        assert_eq!(p.tier_for(&Inputs { gaming: false, on_battery: false, profile: None }), "balanced", "no profile daemon");
        assert_eq!(p.tier_for(&inputs("turbo", false, false)), "balanced", "unknown profile");
        p.unlocked = true;
        assert_eq!(p.tier_for(&inputs("power-saver", true, true)), "unlocked");
    }

    #[test]
    fn rates_cap_at_the_output_maximum() {
        let p = policy();
        assert_eq!(triple(&p.plan(&inputs("performance", false, false), Some(60))), (60, 20, 30));
        let r = p.plan(&inputs("power-saver", false, true), Some(75));
        assert_eq!((r.desktop_hz, r.cap_hz), (75, 120), "the cap stays the tier's; D is what the output can show");
        assert_eq!(triple(&p.plan(&inputs("performance", false, false), None)), (90, 30, 45));
        let mut u = p.clone();
        u.unlocked = true;
        assert_eq!(triple(&u.plan(&Inputs::default(), None)), (0, 0, 0), "unknown maximum: every output frame");
        assert_eq!(whole_hz(143.998), 144);
        assert_eq!(whole_hz(119.98), 120);
        assert_eq!(whole_hz(59.94), 60);
        assert_eq!(whole_hz(f64::NAN), 0);
    }

    #[test]
    fn invalid_policies_are_rejected_naming_the_key() {
        let base: Value = VENDOR.parse().unwrap();
        assert!(Policy::from_ssot(&"[desktop]\nx = 1".parse::<Value>().unwrap()).unwrap().is_none(), "absent section");
        let cases: &[(&str, Value, &str)] = &[
            ("tiers.performance.desktop_hz", Value::Integer(0), "desktop_hz"),
            ("tiers.performance.desktop_hz", Value::Integer(1001), "desktop_hz"),
            ("tiers.gaming.glass_divisor", Value::Integer(0), "glass_divisor"),
            ("tiers.battery.wallpaper_divisor", Value::Float(2.0), "wallpaper_divisor"),
            ("mechanism", Value::String("magic".into()), "mechanism"),
            ("modes", Value::String("any".into()), "modes"),
            ("modes", Value::String("custom".into()), "modes"),
            ("profiles.balanced", Value::String("unlocked".into()), "balanced"),
            ("profiles.turbo", Value::String("balanced".into()), "turbo"),
            ("state_file", Value::String("../x".into()), "state_file"),
            ("state_file", Value::String("/run/x".into()), "state_file"),
            ("user_fragment", Value::String("a/b.toml".into()), "user_fragment"),
            ("debounce_ms", Value::Integer(-1), "debounce_ms"),
            ("mode_confirm_s", Value::Integer(601), "mode_confirm_s"),
            ("mode_confirm_s", Value::String("15".into()), "mode_confirm_s"),
            ("exclude_prefixes", Value::Array(vec![Value::String("A B".into())]), "exclude_prefixes"),
            ("unlocked", Value::String("yes".into()), "unlocked"),
        ];
        for (path, value, needle) in cases {
            let mut doc = base.clone();
            let mut cur = doc.get_mut("desktop").unwrap().get_mut("framerate").unwrap();
            let keys: Vec<&str> = path.split('.').collect();
            for k in &keys[..keys.len() - 1] {
                cur = cur.get_mut(*k).unwrap();
            }
            cur.as_table_mut().unwrap().insert(keys[keys.len() - 1].into(), value.clone());
            let err = Policy::from_ssot(&doc).unwrap_err();
            assert!(err.contains(needle), "{path} = {value:?}: {err}");
        }
        let mut doc = base.clone();
        doc["desktop"]["framerate"]["tiers"].as_table_mut().unwrap().remove("battery");
        assert!(Policy::from_ssot(&doc).unwrap_err().contains("tiers.battery"));
        let mut doc = base;
        doc["desktop"]["framerate"]["tiers"].as_table_mut().unwrap().insert("turbo".into(), Value::Table(Default::default()));
        assert!(Policy::from_ssot(&doc).unwrap_err().contains("turbo"));
    }

    #[test]
    fn exclusions_default_to_the_display_ignore_list() {
        let p = policy();
        assert_eq!(p.exclude_prefixes, vec!["HEADLESS-".to_string()]);
        assert!(p.excluded("HEADLESS-MIOS-RDP") && !p.excluded("DP-3"));
        let mut doc: Value = VENDOR.parse().unwrap();
        doc["desktop"]["framerate"].as_table_mut().unwrap().insert("exclude_prefixes".into(), Value::Array(vec![]));
        let p = Policy::from_ssot(&doc).unwrap().unwrap();
        assert!(!p.excluded("HEADLESS-1"), "an explicit empty list excludes nothing (nested tests)");
    }

    #[test]
    fn tuned_profiles_map_back_to_ppd_names() {
        let conf = "[main]\ndefault=balanced\n[profiles]\n# PPD = TuneD\npower-saver=mios-host-powersave\nbalanced=mios-host\nperformance=mios-host-performance\n[battery]\nbalanced=balanced-battery\n";
        assert_eq!(ppd_profile_for_tuned(conf, "mios-host-powersave").as_deref(), Some("power-saver"));
        assert_eq!(ppd_profile_for_tuned(conf, "mios-host").as_deref(), Some("balanced"));
        assert_eq!(ppd_profile_for_tuned(conf, "mios-host-performance").as_deref(), Some("performance"));
        assert_eq!(ppd_profile_for_tuned(conf, "balanced-battery").as_deref(), Some("balanced"));
        assert_eq!(ppd_profile_for_tuned(conf, "virtual-guest"), None);
        assert_eq!(ppd_profile_for_tuned("[main]\ndefault=mios-host\n", "mios-host"), None, "only [profiles]/[battery]");
    }

    #[test]
    fn battery_needs_a_discharging_system_battery_and_no_mains() {
        let s = |kind: &str, scope: &str, online: Option<bool>, status: &str| PowerSupply {
            kind: kind.into(),
            scope: scope.into(),
            online,
            status: status.into(),
        };
        // this host: a game controller's battery only
        assert!(!on_battery(&[s("Battery", "Device", None, "Discharging")]));
        assert!(on_battery(&[s("Battery", "", None, "Discharging"), s("Mains", "", Some(false), "")]));
        assert!(!on_battery(&[s("Battery", "", None, "Discharging"), s("Mains", "", Some(true), "")]));
        assert!(!on_battery(&[s("Battery", "System", None, "Charging"), s("Mains", "", Some(false), "")]));
        assert!(on_battery(&[s("Battery", "System", None, "Discharging")]));
        assert!(!on_battery(&[]));
    }

    #[test]
    fn host_readers_use_the_given_root() {
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        std::fs::create_dir_all(root.join("etc/tuned")).unwrap();
        std::fs::write(root.join("etc/tuned/ppd_base_profile"), "performance").unwrap();
        let bat = root.join("sys/class/power_supply/BAT0");
        std::fs::create_dir_all(&bat).unwrap();
        std::fs::write(bat.join("type"), "Battery\n").unwrap();
        std::fs::write(bat.join("status"), "Discharging\n").unwrap();
        let ac = root.join("sys/class/power_supply/AC");
        std::fs::create_dir_all(&ac).unwrap();
        std::fs::write(ac.join("type"), "Mains\n").unwrap();
        std::fs::write(ac.join("online"), "0\n").unwrap();
        assert_eq!(login_inputs(root), Inputs { gaming: false, on_battery: true, profile: Some("performance".into()) });
        std::fs::write(ac.join("online"), "1\n").unwrap();
        std::fs::write(root.join("etc/tuned/ppd_base_profile"), "custom\n").unwrap();
        assert_eq!(login_inputs(root), Inputs { gaming: false, on_battery: false, profile: None });
    }

    #[test]
    fn state_round_trips_with_flat_keys_and_ignores_stale_writers() {
        let mut outputs = BTreeMap::new();
        outputs.insert(
            "DP-3".to_string(),
            OutputState { mechanism: "mode".into(), cap_hz: 0, max_hz: 144, mode: Some("1920x1080@60.00".into()), refresh_hz: 60.0 },
        );
        let s = State {
            version: STATE_VERSION,
            pid: std::process::id(),
            seq: 3,
            tier: "performance".into(),
            desktop_hz: 90,
            cap_hz: 90,
            glass_fps: 30,
            wallpaper_fps: 45,
            max_hz: 144,
            unlocked: false,
            wallpaper_paced: true,
            inputs: inputs("performance", false, false),
            outputs,
        };
        let text = s.to_json();
        assert!(text.contains("\"desktop_hz\": 90") && text.contains("\"mode.DP-3\": \"1920x1080@60.00\""), "{text}");
        assert!(text.contains("\"wallpaper_paced\": true"), "Host.qml reads the flat key: {text}");
        assert_eq!(State::parse(&text).unwrap(), s);
        // a state file written before the key existed still parses (unpaced)
        let mut v: serde_json::Value = serde_json::from_str(&text).unwrap();
        assert!(v.as_object_mut().unwrap().remove("wallpaper_paced").is_some());
        let old = v.to_string();
        assert!(!old.contains("wallpaper_paced"));
        assert!(!State::parse(&old).unwrap().wallpaper_paced);
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("state.json");
        std::fs::write(&path, &text).unwrap();
        let proc_root = dir.path().join("fake");
        std::fs::create_dir_all(proc_root.join(format!("proc/{}", s.pid))).unwrap();
        std::fs::write(proc_root.join(format!("proc/{}/comm", s.pid)), "miosd\n").unwrap();
        assert!(State::read_live(&path, &proc_root).is_some());
        std::fs::write(proc_root.join(format!("proc/{}/comm", s.pid)), "bash\n").unwrap();
        assert!(State::read_live(&path, &proc_root).is_none(), "a reused pid is not the writer");
        assert!(State::parse(&text.replace("\"version\": 1", "\"version\": 9")).is_err());
    }
}
