// AI-hint: miosd framerate, pure part: Hyprland monitors -> per-output mechanism (VRR cap, advertised-mode fallback,
//          uncapped, excluded), nearest advertised mode, the one `hyprctl eval` chunk that sets the cap / glass / Hypr3D
//          rates, the state file, the gaming exit hold and the modeset hysteresis. No I/O: unit-tested on fixtures.
// AI-related: src/mios-rs/miosd/src/framerate.rs, tools/native/mios-resolver/src/framerate.rs, usr/share/mios/mios.toml [desktop.framerate]

use std::collections::{BTreeMap, BTreeSet};
use std::time::{Duration, Instant};

use mios_resolver::framerate::{whole_hz, Inputs, Mechanism, OutputState, Policy, Rates, State, STATE_VERSION};
use serde_json::Value as Json;

/// One output as `hyprctl monitors all -j` reports it.
#[derive(Clone, Debug, PartialEq)]
pub struct Monitor {
    pub name: String,
    pub width: u32,
    pub height: u32,
    pub refresh: f64,
    /// advertised (EDID) modes: (width, height, refresh Hz)
    pub modes: Vec<(u32, u32, f64)>,
    /// adaptive sync active on the output now
    pub vrr: bool,
    pub disabled: bool,
    pub dpms: bool,
    pub mirror: bool,
    pub x: i64,
    pub y: i64,
    pub scale: f64,
    pub transform: i64,
    /// make|model|serial: the panel identity bad modes are keyed by
    pub identity: String,
}

/// "1920x1080@143.998Hz" / "1920x1080@60.00" -> (1920, 1080, 143.998)
pub fn parse_mode(s: &str) -> Option<(u32, u32, f64)> {
    let s = s.trim().trim_end_matches("Hz");
    let (size, hz) = s.split_once('@')?;
    let (w, h) = size.split_once('x')?;
    let hz: f64 = hz.parse().ok()?;
    (hz.is_finite() && hz > 0.0).then_some((w.parse().ok()?, h.parse().ok()?, hz))
}

pub fn mode_string(w: u32, h: u32, hz: f64) -> String {
    format!("{w}x{h}@{hz:.2}")
}

pub fn parse_monitors(v: &Json) -> Vec<Monitor> {
    let s = |m: &Json, k: &str| m.get(k).and_then(Json::as_str).unwrap_or("").to_owned();
    let f = |m: &Json, k: &str| m.get(k).and_then(Json::as_f64).unwrap_or(0.0);
    let b = |m: &Json, k: &str| m.get(k).and_then(Json::as_bool).unwrap_or(false);
    v.as_array()
        .into_iter()
        .flatten()
        .filter_map(|m| {
            let name = s(m, "name");
            if name.is_empty() {
                return None;
            }
            let mirror = !matches!(m.get("mirrorOf").and_then(Json::as_str), None | Some("none") | Some(""));
            Some(Monitor {
                width: f(m, "width") as u32,
                height: f(m, "height") as u32,
                refresh: f(m, "refreshRate"),
                modes: m
                    .get("availableModes")
                    .and_then(Json::as_array)
                    .into_iter()
                    .flatten()
                    .filter_map(|x| x.as_str().and_then(parse_mode))
                    .collect(),
                vrr: b(m, "vrr"),
                disabled: b(m, "disabled"),
                dpms: m.get("dpmsStatus").and_then(Json::as_bool).unwrap_or(true),
                mirror,
                x: f(m, "x") as i64,
                y: f(m, "y") as i64,
                scale: m.get("scale").and_then(Json::as_f64).unwrap_or(1.0),
                transform: f(m, "transform") as i64,
                identity: format!("{}|{}|{}", s(m, "make"), s(m, "model"), s(m, "serial")),
                name,
            })
        })
        .collect()
}

impl Monitor {
    /// Enabled, not mirroring, and not on the exclusion list: the outputs the policy holds.
    pub fn local(&self, policy: &Policy) -> bool {
        !self.disabled && !self.mirror && !policy.excluded(&self.name)
    }

    /// The largest advertised refresh at the current resolution (the current refresh when nothing is advertised).
    pub fn max_hz(&self) -> f64 {
        self.modes
            .iter()
            .filter(|(w, h, _)| *w == self.width && *h == self.height)
            .map(|m| m.2)
            .fold(self.refresh, f64::max)
    }
}

/// How D is held on one output.
#[derive(Clone, Debug, PartialEq)]
pub enum Mech {
    /// the pacing plugin caps commits at `cap` Hz (0 = no cap) on an output running VRR
    VrrCap,
    /// switch to this advertised mode (WxH@Hz)
    Mode(String),
    /// nothing holds D here (no VRR, mechanism = "cap", no plugin, or a pinned mode)
    Uncapped(&'static str),
    /// a remote/virtual output: its own pacing
    Excluded,
}

impl Mech {
    pub fn name(&self) -> &'static str {
        match self {
            Mech::VrrCap => "vrr-cap",
            Mech::Mode(_) => "mode",
            Mech::Uncapped(_) => "uncapped",
            Mech::Excluded => "excluded",
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct OutputPlan {
    pub mech: Mech,
    pub cap_hz: u32,
    pub max_hz: u32,
    pub refresh_hz: f64,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Plan {
    pub rates: Rates,
    pub max_hz: Option<u32>,
    pub outputs: BTreeMap<String, OutputPlan>,
}

/// What the plan may use on this compositor.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Env {
    /// plugin:mios_pacing:* options exist (the plugin is loaded)
    pub pacing: bool,
    /// [desktop.hyprland.mios-pacing.config].require_vrr
    pub require_vrr: bool,
    /// outputs whose mode the operator pinned ([desktop.hyprland.monitors.<name>].mode = "WxH@Hz")
    pub pinned: BTreeSet<String>,
    /// identity -> modes that failed verification once
    pub bad: BTreeMap<String, BTreeSet<String>>,
    /// panel identities whose last automatic modeset the operator did not confirm in time: no more automatic modesets
    /// there (the output goes back to its baseline) until `miosd framerate confirm-mode --reset`
    pub declined: BTreeSet<String>,
}

/// The advertised mode at the output's resolution nearest `target` Hz (ties: the higher rate), never a blocklisted one.
pub fn nearest_mode(m: &Monitor, target: f64, bad: Option<&BTreeSet<String>>) -> Option<String> {
    m.modes
        .iter()
        .filter(|(w, h, _)| *w == m.width && *h == m.height)
        .map(|(w, h, hz)| (mode_string(*w, *h, *hz), *hz))
        .filter(|(s, _)| !bad.is_some_and(|b| b.contains(s)))
        .min_by(|a, b| {
            let da = (a.1 - target).abs();
            let db = (b.1 - target).abs();
            da.partial_cmp(&db).unwrap_or(std::cmp::Ordering::Equal).then(b.1.partial_cmp(&a.1).unwrap_or(std::cmp::Ordering::Equal))
        })
        .map(|(s, _)| s)
}

/// The plan for these inputs on these outputs. D and the cap come from the policy (D capped at the largest local
/// refresh); per output: excluded -> left alone; VRR active (or require_vrr off) with the plugin loaded -> the cap;
/// else, unless mechanism = "cap" or the operator pinned the mode, the advertised mode nearest D.
pub fn plan(policy: &Policy, inputs: &Inputs, monitors: &[Monitor], env: &Env) -> Plan {
    let locals: Vec<&Monitor> = monitors.iter().filter(|m| m.local(policy)).collect();
    let max_hz = locals.iter().map(|m| whole_hz(m.max_hz())).max().filter(|m| *m > 0);
    let rates = policy.plan(inputs, max_hz);
    let mut outputs = BTreeMap::new();
    for m in monitors.iter().filter(|m| !m.disabled && !m.mirror) {
        let max = whole_hz(m.max_hz());
        let refresh_hz = (m.refresh * 1000.0).round() / 1000.0;
        if policy.excluded(&m.name) {
            outputs.insert(m.name.clone(), OutputPlan { mech: Mech::Excluded, cap_hz: 0, max_hz: max, refresh_hz });
            continue;
        }
        let cappable = env.pacing && (m.vrr || !env.require_vrr);
        let target = if rates.cap_hz == 0 { m.max_hz() } else { f64::from(rates.cap_hz) };
        let mech = match policy.mechanism {
            Mechanism::Cap if cappable => Mech::VrrCap,
            Mechanism::Cap => Mech::Uncapped(if env.pacing { "no active VRR" } else { "pacing plugin not loaded" }),
            Mechanism::Auto if cappable => Mech::VrrCap,
            _ if env.pinned.contains(&m.name) => Mech::Uncapped("mode pinned by the operator"),
            _ if env.declined.contains(&m.identity) => Mech::Uncapped("mode switch not confirmed"),
            _ => match nearest_mode(m, target, env.bad.get(&m.identity)) {
                Some(mode) => Mech::Mode(mode),
                None => Mech::Uncapped("no advertised mode"),
            },
        };
        let cap_hz = if mech == Mech::VrrCap { rates.cap_hz } else { 0 };
        outputs.insert(m.name.clone(), OutputPlan { mech, cap_hz, max_hz: max, refresh_hz });
    }
    Plan { rates, max_hz, outputs }
}

/// Which rate consumers are loaded in the compositor (each part of the chunk is sent only to a loaded one, so a
/// missing plugin never raises a config error).
#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Consumers {
    pub pacing: bool,
    /// the pacing plugin can hold the wallpaper's frame callbacks (its wallpaper_fps option exists and its
    /// CWLSurfaceResource::frame hook is live)
    pub pacing_wallpaper: bool,
    pub hyprglass: bool,
    pub hypr3d: bool,
}

/// The compositor presents the wallpaper at W (Host.qml then steps once per presented frame).
pub fn wallpaper_paced(r: &Rates, c: Consumers) -> bool {
    c.pacing && c.pacing_wallpaper && r.wallpaper_fps > 0
}

/// plugin:mios_pacing:wallpaper_fps for this plan: W while paced, else 0 (no throttle).
pub fn wallpaper_throttle(r: &Rates, c: Consumers) -> u32 {
    if wallpaper_paced(r, c) {
        r.wallpaper_fps
    } else {
        0
    }
}

/// Hypr3D's room pump rate: D, or 0 (every output frame) when nothing is capped (unlocked).
pub fn hypr3d_max_fps(r: &Rates) -> u32 {
    if r.cap_hz == 0 {
        0
    } else {
        r.desktop_hz
    }
}

/// ONE `hyprctl eval` chunk for every rate consumer, each wrapped in a pcall and feature-detected at run time.
pub fn rates_lua(plan: &Plan, policy: &Policy, c: Consumers) -> String {
    let r = &plan.rates;
    let mut body = String::new();
    if c.pacing {
        // wallpaper_fps: the wallpaper's frame callbacks at W; 0 (no throttle) while W is unknown, so a previous tier's
        // throttle never outlives the state that told Host.qml to follow it. An older plugin build gets no key at all.
        let wallpaper = if c.pacing_wallpaper { format!(", wallpaper_fps = {}", wallpaper_throttle(r, c)) } else { String::new() };
        body += &format!(
            " pcall(hl.config, {{plugin = {{mios_pacing = {{max_fps = {}, exclude_prefixes = {}{wallpaper}}}}}}})\n",
            r.cap_hz,
            crate::placement::lua_str(&policy.exclude_prefixes.join(","))
        );
    }
    if c.hyprglass {
        body += &format!(
            " if hl.plugin.hyprglass then pcall(hl.plugin.hyprglass.config, {{layers = {{live_resample_fps = {g}}}, windows = {{live_resample_fps = {g}}}}}) end\n",
            g = r.glass_fps
        );
    }
    if c.hypr3d {
        body += &format!(
            " if hl.plugin.hypr3d then pcall(hl.plugin.hypr3d.config, {{render = {{max_fps = {}, glass_fps = {}}}, world = {{live_fps = {}}}}}) end\n",
            hypr3d_max_fps(r),
            r.glass_fps,
            r.wallpaper_fps
        );
    }
    format!("(function()\n{body}end)()")
}

/// `hl.monitor` for an advertised-mode switch, keeping the output's position, scale and transform.
pub fn monitor_lua(m: &Monitor, mode: &str) -> String {
    let scale = format!("{:.6}", m.scale);
    format!(
        "hl.monitor({{output = {}, mode = {}, position = {}, scale = {}, transform = {}}})",
        crate::placement::lua_str(&m.name),
        crate::placement::lua_str(mode),
        crate::placement::lua_str(&format!("{}x{}", m.x, m.y)),
        scale.trim_end_matches('0').trim_end_matches('.'),
        m.transform
    )
}

/// Two "WxH@Hz" strings name the same mode (same size, refresh within 0.5 Hz: 143.998 and 144.00 are one mode).
pub fn same_mode(a: &str, b: &str) -> bool {
    match (parse_mode(a), parse_mode(b)) {
        (Some((aw, ah, az)), Some((bw, bh, bz))) => aw == bw && ah == bh && (az - bz).abs() < 0.5,
        _ => false,
    }
}

/// Panel identity -> modes (the operator's confirmed modes; the blocklist).
pub type ModeSets = BTreeMap<String, BTreeSet<String>>;

/// Whether an automatic switch of panel `identity` to `mode` must be confirmed by the operator within `confirm_s`
/// seconds ([desktop.framerate].mode_confirm_s) or be reverted: always, except with confirmation off (0), for the
/// output's baseline (the mode it showed before the daemon first switched it: going back is the revert itself) and
/// for a mode the operator already confirmed on this panel.
pub fn needs_confirm(confirm_s: u64, identity: &str, mode: &str, baseline: Option<&str>, confirmed: &ModeSets) -> bool {
    confirm_s > 0
        && !baseline.is_some_and(|b| same_mode(b, mode))
        && !confirmed.get(identity).is_some_and(|set| set.iter().any(|c| same_mode(c, mode)))
}

/// A verified automatic modeset waiting for the operator's confirmation; at `deadline` the previous mode comes back.
#[derive(Clone, Debug, PartialEq)]
pub struct PendingMode {
    pub output: String,
    pub identity: String,
    pub mode: String,
    pub previous: String,
    /// `hl.monitor` restoring `previous` with the output's layout at the time of the switch
    pub restore_lua: String,
    pub deadline: Instant,
}

impl PendingMode {
    /// Seconds left before the revert (0 when due).
    #[allow(dead_code)]
    pub fn left(&self, now: Instant) -> u64 {
        self.deadline.saturating_duration_since(now).as_secs()
    }

    pub fn due(&self, now: Instant) -> bool {
        now >= self.deadline
    }
}

/// Test hook (MIOS_FRAMERATE_TEST_MODES = "NAME=WxH@Hz,WxH@Hz;NAME2=..."): extra advertised modes for VIRTUAL
/// outputs (HEADLESS-* only: their modes are custom anyway, and a headless output lists only the custom mode it was
/// last given), so the nested test exercises the modeset, confirmation and revert path. A real panel's (EDID) list is
/// never touched.
pub fn inject_test_modes(monitors: &mut [Monitor], spec: &str) {
    for entry in spec.split(';') {
        let Some((name, modes)) = entry.split_once('=') else { continue };
        let name = name.trim();
        if !name.starts_with("HEADLESS-") {
            continue;
        }
        if let Some(m) = monitors.iter_mut().find(|m| m.name == name) {
            for x in modes.split(',').filter_map(parse_mode) {
                if !m.modes.iter().any(|y| y.0 == x.0 && y.1 == x.1 && (y.2 - x.2).abs() < 0.5) {
                    m.modes.push(x);
                }
            }
        }
    }
}

/// True when output `m` shows `mode` and is usable (present, enabled, DPMS on, same size, refresh within 0.5 Hz).
pub fn verified(m: Option<&Monitor>, mode: &str) -> bool {
    let (Some(m), Some((w, h, hz))) = (m, parse_mode(mode)) else { return false };
    !m.disabled && m.dpms && m.width == w && m.height == h && (m.refresh - hz).abs() < 0.5
}

pub fn state(plan: &Plan, policy: &Policy, inputs: &Inputs, c: Consumers, pid: u32, seq: u64) -> State {
    let r = &plan.rates;
    State {
        version: STATE_VERSION,
        pid,
        seq,
        tier: r.tier.clone(),
        desktop_hz: r.desktop_hz,
        cap_hz: r.cap_hz,
        glass_fps: r.glass_fps,
        wallpaper_fps: r.wallpaper_fps,
        max_hz: plan.max_hz.unwrap_or(0),
        unlocked: policy.unlocked,
        wallpaper_paced: wallpaper_paced(r, c),
        inputs: inputs.clone(),
        outputs: plan
            .outputs
            .iter()
            .map(|(name, o)| {
                (
                    name.clone(),
                    OutputState {
                        mechanism: o.mech.name().into(),
                        cap_hz: o.cap_hz,
                        max_hz: o.max_hz,
                        mode: match &o.mech {
                            Mech::Mode(mode) => Some(mode.clone()),
                            _ => None,
                        },
                        refresh_hz: o.refresh_hz,
                    },
                )
            })
            .collect(),
    }
}

/// GameMode clients come and go while a launcher re-registers: gaming stays on for `hold` after the last one left.
#[derive(Clone, Debug, Default)]
pub struct GamingHold {
    on: bool,
    off_since: Option<Instant>,
}

impl GamingHold {
    /// The held value for the raw signal at `now`, and when it would drop (to wake up then).
    pub fn update(&mut self, raw: bool, now: Instant, hold: Duration) -> (bool, Option<Instant>) {
        if raw {
            self.on = true;
            self.off_since = None;
            return (true, None);
        }
        if !self.on {
            return (false, None);
        }
        let since = *self.off_since.get_or_insert(now);
        if now.duration_since(since) >= hold {
            self.on = false;
            self.off_since = None;
            (false, None)
        } else {
            (true, Some(since + hold))
        }
    }
}

/// Modeset hysteresis per output: a wanted mode must hold for `hold` and modesets are `min_interval` apart.
#[derive(Clone, Debug, Default)]
pub struct ModesetGate {
    pending: BTreeMap<String, (String, Instant)>,
    last: BTreeMap<String, Instant>,
}

impl ModesetGate {
    /// Ok(true) = switch `output` to `mode` now; Ok(false) with a wake time = not yet. `current` is what it shows.
    pub fn decide(&mut self, output: &str, mode: &str, current_matches: bool, now: Instant, hold: Duration, min_interval: Duration) -> (bool, Option<Instant>) {
        if current_matches {
            self.pending.remove(output);
            return (false, None);
        }
        let since = match self.pending.get(output) {
            Some((m, t)) if m == mode => *t,
            _ => {
                self.pending.insert(output.into(), (mode.into(), now));
                now
            }
        };
        let mut due = since + hold;
        if let Some(last) = self.last.get(output) {
            due = due.max(*last + min_interval);
        }
        if now >= due {
            self.pending.remove(output);
            self.last.insert(output.into(), now);
            (true, None)
        } else {
            (false, Some(due))
        }
    }

    pub fn forget(&mut self, output: &str) {
        self.pending.remove(output);
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn policy() -> Policy {
        let text = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../usr/share/mios/mios.toml")).unwrap();
        let doc: toml::Value = text.parse().unwrap();
        Policy::from_ssot(&doc).unwrap().expect("the SSOT carries [desktop.framerate] (the staged framerate edit)")
    }

    /// DP-3 on this host (EDID modes from hyprctl) and the RDP virtual output.
    fn monitors(vrr: bool) -> Vec<Monitor> {
        let j: Json = serde_json::from_str(&format!(r#"[
          {{"name":"DP-3","make":"LG Electronics","model":"LG 24GL600F","serial":"X","width":1920,"height":1080,"refreshRate":143.998,
           "x":0,"y":0,"scale":1.0,"transform":0,"dpmsStatus":true,"vrr":{vrr},"disabled":false,"mirrorOf":"none",
           "availableModes":["1920x1080@144.00Hz","1920x1080@119.98Hz","1920x1080@119.88Hz","1920x1080@100.00Hz","1920x1080@99.93Hz","1920x1080@74.99Hz","1920x1080@60.00Hz","1920x1080@59.94Hz","1920x1080@50.00Hz","1280x720@60.00Hz"]}},
          {{"name":"HEADLESS-MIOS-RDP","make":"","model":"","serial":"","width":1170,"height":2532,"refreshRate":90.0,"x":1920,"y":0,"scale":2.0,
           "transform":0,"dpmsStatus":true,"vrr":false,"disabled":false,"mirrorOf":"none","availableModes":[]}}
        ]"#)).unwrap();
        parse_monitors(&j)
    }

    fn env(pacing: bool) -> Env {
        Env { pacing, require_vrr: true, ..Default::default() }
    }

    fn inputs(profile: &str, battery: bool, gaming: bool) -> Inputs {
        Inputs { gaming, on_battery: battery, profile: Some(profile.into()) }
    }

    #[test]
    fn modes_parse_from_hyprctl_strings() {
        assert_eq!(parse_mode("1920x1080@143.998Hz"), Some((1920, 1080, 143.998)));
        assert_eq!(parse_mode("1920x1080@60.00"), Some((1920, 1080, 60.0)));
        assert_eq!(parse_mode("1920x1080"), None);
        assert_eq!(parse_mode("axb@60"), None);
        let m = &monitors(true)[0];
        assert_eq!((m.max_hz(), m.modes.len(), m.identity.as_str()), (144.0, 10, "LG Electronics|LG 24GL600F|X"));
    }

    #[test]
    fn vrr_outputs_are_capped_and_remote_outputs_left_alone() {
        let p = policy();
        let pl = plan(&p, &inputs("performance", false, false), &monitors(true), &env(true));
        assert_eq!((pl.rates.desktop_hz, pl.rates.cap_hz, pl.rates.glass_fps, pl.rates.wallpaper_fps), (90, 90, 30, 45));
        assert_eq!(pl.outputs["DP-3"].mech, Mech::VrrCap);
        assert_eq!(pl.outputs["DP-3"].cap_hz, 90);
        assert_eq!(pl.outputs["HEADLESS-MIOS-RDP"].mech, Mech::Excluded, "the RDP output keeps [remote.rdp] pacing");
        assert_eq!(pl.outputs["HEADLESS-MIOS-RDP"].cap_hz, 0);
        assert_eq!(pl.max_hz, Some(144), "the excluded output's rate never sets G/W");
    }

    #[test]
    fn without_vrr_the_nearest_advertised_mode_is_used_never_a_custom_one() {
        let p = policy();
        let mons = monitors(false);
        let mode = |profile: &str, battery: bool, gaming: bool, e: &Env| plan(&p, &inputs(profile, battery, gaming), &mons, e).outputs["DP-3"].mech.clone();
        assert_eq!(mode("performance", false, false, &env(true)), Mech::Mode("1920x1080@99.93".into()), "90 -> 99.93 (no 90 Hz EDID mode)");
        assert_eq!(mode("balanced", false, false, &env(true)), Mech::Mode("1920x1080@60.00".into()));
        assert_eq!(mode("balanced", false, true, &env(true)), Mech::Mode("1920x1080@119.98".into()));
        assert_eq!(mode("balanced", false, false, &env(false)), Mech::Mode("1920x1080@60.00".into()), "no plugin: modes");
        let mut u = p.clone();
        u.unlocked = true;
        assert_eq!(plan(&u, &Inputs::default(), &mons, &env(true)).outputs["DP-3"].mech, Mech::Mode("1920x1080@144.00".into()));
        // a mode that failed verification is never chosen again
        let mut e = env(true);
        e.bad.insert("LG Electronics|LG 24GL600F|X".into(), ["1920x1080@99.93".to_string()].into());
        assert_eq!(mode("performance", false, false, &e), Mech::Mode("1920x1080@100.00".into()));
        // negative controls: pinned mode, mechanism = cap
        let mut e = env(true);
        e.pinned.insert("DP-3".into());
        assert!(matches!(mode("performance", false, false, &e), Mech::Uncapped(_)));
        let mut c = p.clone();
        c.mechanism = Mechanism::Cap;
        assert!(matches!(plan(&c, &inputs("performance", false, false), &mons, &env(true)).outputs["DP-3"].mech, Mech::Uncapped(_)));
        // mechanism = mode switches even with VRR
        let mut m = p.clone();
        m.mechanism = Mechanism::Mode;
        assert_eq!(plan(&m, &inputs("performance", false, false), &monitors(true), &env(true)).outputs["DP-3"].mech, Mech::Mode("1920x1080@99.93".into()));
    }

    #[test]
    fn nearest_mode_breaks_ties_upwards_and_stays_at_the_resolution() {
        let m = &monitors(false)[0];
        assert_eq!(nearest_mode(m, 90.0, None).as_deref(), Some("1920x1080@99.93"));
        assert_eq!(nearest_mode(m, 1000.0, None).as_deref(), Some("1920x1080@144.00"));
        assert_eq!(nearest_mode(m, 1.0, None).as_deref(), Some("1920x1080@50.00"), "never the 720p mode");
        let mut two = m.clone();
        two.modes = vec![(1920, 1080, 60.0), (1920, 1080, 120.0), (1280, 720, 90.0)];
        assert_eq!(nearest_mode(&two, 90.0, None).as_deref(), Some("1920x1080@120.00"), "a tie goes to the higher rate");
        two.modes.retain(|x| x.0 == 1280);
        assert_eq!(nearest_mode(&two, 90.0, None), None, "no mode at the current resolution: no switch");
    }

    #[test]
    fn eval_chunk_sends_only_to_loaded_consumers() {
        let p = policy();
        let pl = plan(&p, &inputs("power-saver", false, false), &monitors(true), &env(true));
        let all = rates_lua(&pl, &p, Consumers { pacing: true, pacing_wallpaper: true, hyprglass: true, hypr3d: true });
        assert!(all.starts_with("(function()\n") && all.ends_with("end)()"), "{all}");
        assert!(all.contains("mios_pacing = {max_fps = 60, exclude_prefixes = 'HEADLESS-', wallpaper_fps = 30}"), "{all}");
        assert!(all.contains("layers = {live_resample_fps = 20}, windows = {live_resample_fps = 20}"), "{all}");
        assert!(all.contains("render = {max_fps = 60, glass_fps = 20}, world = {live_fps = 30}"), "{all}");
        assert_eq!(all.matches("pcall(").count(), 3);
        let none = rates_lua(&pl, &p, Consumers::default());
        assert!(!none.contains("pcall"), "nothing loaded: nothing sent: {none}");
        // negative control: a plugin build without the wallpaper hook never gets (or is said to apply) wallpaper_fps
        let old = Consumers { pacing: true, pacing_wallpaper: false, hyprglass: true, hypr3d: true };
        let s = rates_lua(&pl, &p, old);
        assert!(s.contains("mios_pacing = {max_fps = 60, exclude_prefixes = 'HEADLESS-'}") && !s.contains("wallpaper_fps"), "{s}");
        assert!(!wallpaper_paced(&pl.rates, old) && wallpaper_paced(&pl.rates, Consumers { pacing_wallpaper: true, ..old }));
        // W unknown (unlocked before any output is known): the throttle is lifted explicitly, never left at a stale W
        let mut u = p.clone();
        u.unlocked = true;
        let none = plan(&u, &Inputs::default(), &[], &env(true));
        let s = rates_lua(&none, &u, Consumers { pacing_wallpaper: true, ..old });
        assert!(none.rates.wallpaper_fps == 0 && s.contains("wallpaper_fps = 0"), "{s}");
        let mut u = p.clone();
        u.unlocked = true;
        let pl = plan(&u, &Inputs::default(), &monitors(true), &env(true));
        let s = rates_lua(&pl, &u, Consumers { pacing: true, pacing_wallpaper: true, hyprglass: true, hypr3d: true });
        assert!(s.contains("max_fps = 0, exclude") && s.contains("wallpaper_fps = 144") && s.contains("live_resample_fps = 72") && s.contains("render = {max_fps = 0, glass_fps = 72}, world = {live_fps = 144}"), "{s}");
    }

    #[test]
    fn monitor_rule_keeps_the_layout_and_verification_checks_the_mode() {
        let mut m = monitors(false).remove(0);
        m.scale = 1.25;
        m.x = -1920;
        assert_eq!(monitor_lua(&m, "1920x1080@60.00"), "hl.monitor({output = 'DP-3', mode = '1920x1080@60.00', position = '-1920x0', scale = 1.25, transform = 0})");
        m.refresh = 60.0;
        assert!(verified(Some(&m), "1920x1080@60.00"));
        assert!(!verified(Some(&m), "1920x1080@99.93"));
        m.dpms = false;
        assert!(!verified(Some(&m), "1920x1080@60.00"), "DPMS off is not verified");
        assert!(!verified(None, "1920x1080@60.00"), "a vanished output is not verified");
    }

    #[test]
    fn state_carries_the_flat_mode_keys_for_moded_outputs() {
        let p = policy();
        let pl = plan(&p, &inputs("balanced", false, true), &monitors(false), &env(false));
        let s = state(&pl, &p, &inputs("balanced", false, true), Consumers::default(), 42, 1);
        let text = s.to_json();
        assert!(text.contains("\"mode.DP-3\": \"1920x1080@119.98\"") && text.contains("\"tier\": \"gaming\""), "{text}");
        assert!(!text.contains("mode.HEADLESS"), "{text}");
        assert!(text.contains("\"wallpaper_paced\": false"), "no pacing plugin: the wallpaper keeps its timer: {text}");
        let c = Consumers { pacing: true, pacing_wallpaper: true, ..Default::default() };
        assert!(state(&pl, &p, &inputs("balanced", false, true), c, 42, 2).wallpaper_paced);
    }

    #[test]
    fn gaming_holds_after_the_last_client_leaves() {
        let t0 = Instant::now();
        let hold = Duration::from_millis(3000);
        let mut g = GamingHold::default();
        assert_eq!(g.update(false, t0, hold), (false, None));
        assert_eq!(g.update(true, t0, hold), (true, None));
        let (on, wake) = g.update(false, t0 + Duration::from_millis(100), hold);
        assert!(on && wake == Some(t0 + Duration::from_millis(3100)));
        assert_eq!(g.update(true, t0 + Duration::from_millis(1000), hold), (true, None), "re-registered within the hold");
        assert!(g.update(false, t0 + Duration::from_millis(2000), hold).0);
        assert_eq!(g.update(false, t0 + Duration::from_millis(5000), hold), (false, None));
    }

    #[test]
    fn automatic_modesets_need_confirmation_except_back_to_the_baseline_or_a_confirmed_mode() {
        let id = "LG Electronics|LG 24GL600F|X";
        let mut confirmed = ModeSets::new();
        assert!(needs_confirm(15, id, "1920x1080@99.93", Some("1920x1080@144.00"), &confirmed), "a new mode: confirm or revert");
        assert!(needs_confirm(15, id, "1920x1080@99.93", None, &confirmed));
        assert!(!needs_confirm(15, id, "1920x1080@144.00", Some("1920x1080@143.998"), &confirmed), "back to the baseline is the revert");
        assert!(!needs_confirm(0, id, "1920x1080@99.93", None, &confirmed), "confirmation off");
        confirmed.entry(id.into()).or_default().insert("1920x1080@99.93".into());
        assert!(!needs_confirm(15, id, "1920x1080@99.93", None, &confirmed), "confirmed on this panel before");
        assert!(needs_confirm(15, "Other|Panel|Y", "1920x1080@99.93", None, &confirmed), "a confirmation is per panel");
        assert!(needs_confirm(15, id, "1920x1080@60.00", None, &confirmed), "and per mode");
        assert!(same_mode("1920x1080@143.998", "1920x1080@144.00Hz") && !same_mode("1920x1080@60", "1280x720@60") && !same_mode("x", "x"));
        let t0 = Instant::now();
        let p = PendingMode { output: "DP-3".into(), identity: id.into(), mode: "1920x1080@99.93".into(), previous: "1920x1080@144.00".into(),
                              restore_lua: String::new(), deadline: t0 + Duration::from_secs(15) };
        assert!(!p.due(t0 + Duration::from_secs(14)) && p.due(t0 + Duration::from_secs(15)));
        assert_eq!((p.left(t0), p.left(t0 + Duration::from_secs(20))), (15, 0));
    }

    #[test]
    fn a_declined_panel_gets_no_more_automatic_modesets() {
        let p = policy();
        let mut e = env(true);
        e.declined.insert("LG Electronics|LG 24GL600F|X".into());
        let pl = plan(&p, &inputs("performance", false, false), &monitors(false), &e);
        assert_eq!(pl.outputs["DP-3"].mech, Mech::Uncapped("mode switch not confirmed"));
        // the VRR cap needs no modeset, so a declined panel keeps it
        assert_eq!(plan(&p, &inputs("performance", false, false), &monitors(true), &e).outputs["DP-3"].mech, Mech::VrrCap);
    }

    #[test]
    fn test_modes_reach_only_virtual_outputs() {
        let mut mons = monitors(false);
        mons[1].modes.clear();
        inject_test_modes(&mut mons, "DP-3=1920x1080@30;HEADLESS-MIOS-RDP=1170x2532@60,1170x2532@120,bad; HEADLESS-X=1x1@1");
        assert_eq!(mons[0].modes.len(), 10, "a real panel's EDID list is never replaced");
        assert_eq!(mons[1].modes, vec![(1170, 2532, 60.0), (1170, 2532, 120.0)]);
        let mut again = mons.clone();
        inject_test_modes(&mut again, "HEADLESS-MIOS-RDP=1170x2532@30,1170x2532@60.001");
        assert_eq!(again[1].modes, vec![(1170, 2532, 60.0), (1170, 2532, 120.0), (1170, 2532, 30.0)], "added once, the listed ones kept");
    }

    #[test]
    fn modesets_wait_for_a_stable_tier_and_are_spaced() {
        let t0 = Instant::now();
        let (hold, gap) = (Duration::from_secs(5), Duration::from_secs(30));
        let mut g = ModesetGate::default();
        assert_eq!(g.decide("DP-3", "a", false, t0, hold, gap), (false, Some(t0 + hold)));
        assert_eq!(g.decide("DP-3", "b", false, t0 + Duration::from_secs(3), hold, gap).1, Some(t0 + Duration::from_secs(8)), "a new target restarts the hold");
        assert_eq!(g.decide("DP-3", "b", false, t0 + Duration::from_secs(8), hold, gap), (true, None));
        let (now, wake) = g.decide("DP-3", "c", false, t0 + Duration::from_secs(14), hold, gap);
        assert!(!now && wake == Some(t0 + Duration::from_secs(38)), "at least 30 s after the last modeset");
        assert_eq!(g.decide("DP-3", "c", true, t0 + Duration::from_secs(15), hold, gap), (false, None), "already showing it");
    }
}
