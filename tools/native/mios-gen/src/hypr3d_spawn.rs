// AI-hint: Hypr3D "Retina" window opening ([desktop.hyprland.hypr3d.spawn]): per output, the window_scale /
// spawn_distance / spawn_size / fit keys `mios-gen hyprland-3d-toggle` sends so a window opens in the room at the
// flat desktop's size (magnification M = 1: one snapshot texel per output pixel, every client's glyphs at their 2D
// size). Inputs: the SSOT policy, [theme.font] (text cells, legibility floor), [theme.shell] (margin), the target
// output from `hyprctl -j monitors` (mode, scale, transform, reserved bars) and its [desktop.mobile] verdict
// ($XDG_RUNTIME_DIR/mios-mobile-outputs.json, else display_scale::compute). Mirrors Hypr3D's kFovDeg (65) and
// LOGICAL_PX_PER_UNIT (100); hypr3d-mios-retina-spawn.patch static_asserts both. CLI: mios-gen hyprland-3d-spawn.
// AI-related: usr/share/mios/mios.toml, usr/share/mios/hyprland/patches/hypr3d-mios-retina-spawn.patch, usr/share/mios/hyprland/patches/hypr3d-mios-transform.patch, tools/native/mios-gen/src/render.rs, tools/native/mios-gen/src/display_scale.rs, tests/test-hypr3d-retina-spawn.py
//! Hypr3D Retina window opening.
//!
//! For a camera-facing window in the room, M (on-screen size / 2D size) = window_scale x d1 / distance, where
//! d1 = (logical height / 100 / 2) / tan(65 deg / 2) is the distance at which a window at scale 1 covers exactly
//! its 2D pixels. Texel:pixel >= 1 needs M <= 1 and the 2D glyph size needs M = 1, so the policy keeps the reading
//! distance a world constant and derives window_scale = distance / d1 for the output. Windows larger than the
//! work area minus the margin are fitted (resized, or pushed back when the client refuses), never cropped.

use serde_json::{json, Value as Json};
use toml::Value;

/// Hypr3D `src/Render/GLScene.hpp` kFovDeg (vertical field of view, degrees).
pub const FOV_DEG: f64 = 65.0;
/// Hypr3D `src/World/World3D.hpp` LOGICAL_PX_PER_UNIT (1 logical px = 1 cm).
pub const PX_PER_UNIT: f64 = 100.0;

#[derive(Debug, Clone, Copy, PartialEq)]
pub enum Fit {
    Off,
    Resize,
    Distance,
}

impl Fit {
    pub fn as_str(self) -> &'static str {
        match self {
            Fit::Off => "off",
            Fit::Resize => "resize",
            Fit::Distance => "distance",
        }
    }
}

/// Text metrics from the SSOT text-size source ([theme.font]): the font size (px) and the cell grid.
#[derive(Debug, Clone, PartialEq)]
pub struct Text {
    pub size_px: f64,
    pub cell: (f64, f64),
    pub chrome: (f64, f64),
}

#[derive(Debug, Clone, PartialEq)]
pub struct Policy {
    pub reading_distance_m: f64,
    pub eyeline_offset_m: f64,
    pub tilt_angle_deg: f64,
    pub open_effect_3d: String,
    pub close_effect_3d: String,
    pub fit: Fit,
    pub fit_margin_px: f64,
    /// Minimum visible shell clearance on each edge when Hyprland reports no reserved area.
    pub safe_insets_px: [f64; 4],
    pub min_text_px: f64,
    pub text: Text,
    pub landscape_cells: (i64, i64),
    pub portrait_cells: (i64, i64),
    pub mobile_fill: bool,
    pub cascade_px: f64,
    pub pixel_snap: bool,
    pub opening: Option<mios_resolver::window_opening::Policy>,
}

fn num(v: &Value) -> Option<f64> {
    v.as_float().or_else(|| v.as_integer().map(|i| i as f64)).filter(|f| f.is_finite())
}

/// A dotted SSOT path ("theme.shell.edge_margin_px").
fn lookup<'a>(data: &'a Value, path: &str) -> Option<&'a Value> {
    path.split('.').try_fold(data, |v, k| v.get(k))
}

impl Policy {
    /// `[desktop.hyprland.hypr3d.spawn]`, validated. `None`: absent, disabled, or criterion "fixed" (the plugin's
    /// own window_scale / spawn_distance / spawn_size, or explicit `[config.windows]` keys).
    pub fn from_ssot(data: &Value) -> Result<Option<Policy>, String> {
        let Some(t) = lookup(data, "desktop.hyprland.hypr3d.spawn") else { return Ok(None) };
        let t = t.as_table().ok_or("desktop.hyprland.hypr3d.spawn must be a table")?;
        let key = |k: &str| format!("desktop.hyprland.hypr3d.spawn.{k}");
        let enabled = t.get("enabled").and_then(Value::as_bool).ok_or_else(|| format!("{} must be a boolean", key("enabled")))?;
        let criterion = t.get("criterion").and_then(Value::as_str)
            .ok_or_else(|| format!("{} must be \"retina\" or \"fixed\"", key("criterion")))?;
        match criterion {
            "retina" => {}
            "fixed" => return Ok(None),
            _ => return Err(format!("{} must be \"retina\" or \"fixed\"", key("criterion"))),
        }
        if !enabled {
            return Ok(None);
        }
        let number = |k: &str, lo: f64, hi: f64| -> Result<f64, String> {
            t.get(k).and_then(num).filter(|v| *v >= lo && *v <= hi)
                .ok_or_else(|| format!("{} must be a number in {lo}..={hi}", key(k)))
        };
        // a number, or the SSOT key it follows
        let follow = |k: &str, lo: f64, hi: f64| -> Result<f64, String> {
            let v = t.get(k).ok_or_else(|| format!("{} must be a number or an SSOT key", key(k)))?;
            let resolved = match v {
                Value::String(path) => lookup(data, path)
                    .and_then(num)
                    .ok_or_else(|| format!("{} follows {path}, which is not a number", key(k)))?,
                other => num(other).ok_or_else(|| format!("{} must be a number or an SSOT key", key(k)))?,
            };
            if (lo..=hi).contains(&resolved) {
                Ok(resolved)
            } else {
                Err(format!("{} must be in {lo}..={hi} (got {resolved})", key(k)))
            }
        };
        let cells = |k: &str| -> Result<(i64, i64), String> {
            let a = t.get(k).and_then(Value::as_array).filter(|a| a.len() == 2)
                .ok_or_else(|| format!("{} must be [columns, rows]", key(k)))?;
            let c = a[0].as_integer().filter(|v| (1..=1000).contains(v));
            let r = a[1].as_integer().filter(|v| (1..=1000).contains(v));
            c.zip(r).ok_or_else(|| format!("{} must be [columns, rows] in 1..=1000", key(k)))
        };
        let fit = match t.get("fit").and_then(Value::as_str) {
            Some("resize") => Fit::Resize,
            Some("distance") => Fit::Distance,
            Some("off") => Fit::Off,
            _ => return Err(format!("{} must be \"resize\", \"distance\" or \"off\"", key("fit"))),
        };
        let mobile_fill = match t.get("mobile").and_then(Value::as_str) {
            Some("fill") => true,
            Some("cells") => false,
            _ => return Err(format!("{} must be \"fill\" or \"cells\"", key("mobile"))),
        };
        let source = t.get("text_size_source").and_then(Value::as_str)
            .ok_or_else(|| format!("{} must name the SSOT font table", key("text_size_source")))?;
        let font = lookup(data, source).and_then(Value::as_table)
            .ok_or_else(|| format!("{}: {source} is not a table", key("text_size_source")))?;
        let metric = |k: &str, lo: f64, hi: f64| -> Result<f64, String> {
            font.get(k).and_then(num).filter(|v| *v >= lo && *v <= hi)
                .ok_or_else(|| format!("{source}.{k} must be a number in {lo}..={hi} (text_size_source)"))
        };
        let text = Text {
            size_px: metric("size", 4.0, 200.0)?,
            cell: (metric("cell_w_px", 1.0, 200.0)?, metric("cell_h_px", 1.0, 400.0)?),
            chrome: (metric("chrome_w_px", 0.0, 2000.0)?, metric("chrome_h_px", 0.0, 2000.0)?),
        };
        let shell = lookup(data, "theme.shell").and_then(Value::as_table)
            .ok_or("theme.shell must be a table for Hypr3D safe insets")?;
        let shell_num = |k: &str| shell.get(k).and_then(num).filter(|v| *v >= 0.0 && *v <= 512.0)
            .ok_or_else(|| format!("theme.shell.{k} must be a number in 0..=512 for Hypr3D safe insets"));
        let top = shell_num("top_height_px")?;
        let dock = shell_num("dock_height_px")?;
        let edge = shell_num("edge_margin_px")?;
        let opt_number = |k: &str, default: f64, lo: f64, hi: f64| -> Result<f64, String> {
            match t.get(k) {
                None => Ok(default),
                Some(v) => num(v).filter(|x| *x >= lo && *x <= hi)
                    .ok_or_else(|| format!("{} must be a number in {lo}..={hi}", key(k))),
            }
        };
        let opt_str = |k: &str, default: &str, allowed: &[&str]| -> Result<String, String> {
            match t.get(k) {
                None => Ok(default.to_owned()),
                Some(v) => {
                    let s = v.as_str().ok_or_else(|| format!("{} must be a string", key(k)))?;
                    if allowed.contains(&s) {
                        Ok(s.to_owned())
                    } else {
                        Err(format!("{} must be one of {:?}", key(k), allowed))
                    }
                }
            }
        };
        Ok(Some(Policy {
            reading_distance_m: number("reading_distance_m", 0.3, 100.0)?,
            eyeline_offset_m: opt_number("eyeline_offset_m", 0.0, -2.0, 2.0)?,
            tilt_angle_deg: opt_number("tilt_angle_deg", 0.0, -60.0, 60.0)?,
            open_effect_3d: opt_str("open_effect_3d", "magic-lamp", &["magic-lamp", "none"])?,
            close_effect_3d: opt_str("close_effect_3d", "glass-dissolve", &["glass-dissolve", "magic-lamp", "none"])?,
            fit,
            fit_margin_px: follow("fit_margin_px", 0.0, 512.0)?,
            safe_insets_px: [0.0, top + 2.0 * edge, 0.0, dock + 2.0 * edge],
            min_text_px: number("min_text_px", 1.0, 200.0)?,
            text,
            landscape_cells: cells("landscape_cells")?,
            portrait_cells: cells("portrait_cells")?,
            mobile_fill,
            cascade_px: number("cascade_px", 0.0, 512.0)?,
            pixel_snap: t.get("pixel_snap").and_then(Value::as_bool)
                .ok_or_else(|| format!("{} must be a boolean", key("pixel_snap")))?,
            opening: mios_resolver::window_opening::Policy::from_ssot(data)?,
        }))
    }
}

/// One output as `hyprctl -j monitors` reports it (mode px unrotated, reserved [left, top, right, bottom]).
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Output {
    pub name: String,
    pub width: i64,
    pub height: i64,
    pub scale: f64,
    pub transform: i64,
    pub reserved: [f64; 4],
    pub phys_mm: (f64, f64),
    pub focused: bool,
    pub mobile: bool,
}

impl Output {
    pub fn from_hyprctl(m: &Json) -> Option<Output> {
        let f = |k: &str| m.get(k).and_then(Json::as_f64);
        let reserved = m.get("reserved").and_then(Json::as_array)
            .map(|a| a.iter().filter_map(Json::as_f64).collect::<Vec<_>>())
            .filter(|a| a.len() == 4)
            .map(|a| [a[0], a[1], a[2], a[3]])
            .unwrap_or([0.0; 4]);
        let o = Output {
            name: m.get("name")?.as_str()?.to_owned(),
            width: f("width")? as i64,
            height: f("height")? as i64,
            scale: f("scale").filter(|s| *s > 0.0)?,
            transform: f("transform").unwrap_or(0.0) as i64,
            reserved,
            phys_mm: (f("physicalWidth").unwrap_or(0.0), f("physicalHeight").unwrap_or(0.0)),
            focused: m.get("focused").and_then(Json::as_bool).unwrap_or(false),
            mobile: false,
        };
        (o.width > 0 && o.height > 0).then_some(o)
    }

    /// Hypr3D's `m_size`: the transformed mode / scale (logical px).
    pub fn logical(&self) -> (f64, f64) {
        let (w, h) = if self.transform.rem_euclid(2) == 1 { (self.height, self.width) } else { (self.width, self.height) };
        (w as f64 / self.scale, h as f64 / self.scale)
    }
}

/// tan(fov / 2) at rest zoom.
pub fn tan_half() -> f64 {
    (FOV_DEG.to_radians() / 2.0).tan()
}

/// The 1:1 distance (world units) for a logical output height.
pub fn d1(h_log: f64) -> f64 {
    (h_log / PX_PER_UNIT / 2.0) / tan_half()
}

/// M for a camera-facing window: on-screen size / 2D size.
pub fn magnification(window_scale: f64, distance: f64, h_log: f64) -> f64 {
    window_scale * d1(h_log) / distance
}

/// The per-output placement.
#[derive(Debug, Clone, PartialEq)]
pub struct Plan {
    pub output: String,
    pub logical: (f64, f64),
    /// output px per logical px (the monitor scale)
    pub scale: f64,
    pub orientation: &'static str,
    pub mobile: bool,
    pub d1: f64,
    pub window_scale: f64,
    pub spawn_distance: f64,
    pub fit_box: (f64, f64),
    pub spawn_box: (i64, i64),
    pub render_scale: f64,
    pub m_min: f64,
}

pub fn plan(p: &Policy, o: &Output, distance: f64, render_scale: f64) -> Plan {
    let (wl, hl) = o.logical();
    let [l, t, r, b] = o.reserved;
    let l = l.max(p.safe_insets_px[0]);
    let t = t.max(p.safe_insets_px[1]);
    let r = r.max(p.safe_insets_px[2]);
    let b = b.max(p.safe_insets_px[3]);
    let m = p.fit_margin_px;
    let fit_box = ((wl - l - r - 2.0 * m).max(16.0), (hl - t - b - 2.0 * m).max(16.0));
    let portrait = hl > wl;
    let spawn_box = if o.mobile && p.mobile_fill {
        (fit_box.0.floor() as i64, fit_box.1.floor() as i64)
    } else if let Some(opening) = &p.opening {
        let width_mm = if o.transform.rem_euclid(2) == 1 { o.phys_mm.1 } else { o.phys_mm.0 };
        let ppm = (width_mm > 0.0).then_some(wl / width_mm);
        let (w,h) = opening.size((wl-l-r).max(1.0), (hl-t-b).max(1.0), ppm);
        (w as i64,h as i64)
    } else {
        let (cols, rows) = if portrait { p.portrait_cells } else { p.landscape_cells };
        let w = cols as f64 * p.text.cell.0 + p.text.chrome.0;
        let h = rows as f64 * p.text.cell.1 + p.text.chrome.1;
        (w.min(fit_box.0).floor() as i64, h.min(fit_box.1).floor() as i64)
    };
    let r = render_scale.max(1.0);
    Plan {
        output: o.name.clone(),
        logical: (wl, hl),
        scale: o.scale,
        orientation: if portrait { "portrait" } else { "landscape" },
        mobile: o.mobile,
        d1: d1(hl),
        window_scale: distance / d1(hl),
        spawn_distance: distance,
        fit_box,
        spawn_box,
        render_scale: r,
        m_min: (1.0 / r).max(p.min_text_px / p.text.size_px).min(1.0),
    }
}

/// How a window of logical size w x h (min: the client's minimum) opens on the planned output.
#[derive(Debug, Clone, PartialEq)]
pub struct Placement {
    pub box_px: (f64, f64),
    pub m: f64,
    pub resized: bool,
    pub pushed: bool,
    pub small: bool,
}

pub fn place(plan: &Plan, p: &Policy, w: f64, h: f64, min: Option<(f64, f64)>) -> Placement {
    let (fw, fh) = plan.fit_box;
    let mut bw = w;
    let mut bh = h;
    let mut resized = false;
    if p.fit == Fit::Resize && (w > fw || h > fh) {
        let (mw, mh) = min.unwrap_or((1.0, 1.0));
        bw = w.min(fw).max(mw);
        bh = h.min(fh).max(mh);
        resized = bw != w || bh != h;
    }
    let m = if p.fit == Fit::Off { 1.0 } else { (fw / bw).min(fh / bh).min(1.0) };
    Placement { box_px: (bw, bh), m, resized, pushed: m < 1.0, small: m < plan.m_min - 1e-9 }
}

/// The plugin keys for the planned output (hypr3d-mios-retina-spawn.patch; an unpatched plugin reads only
/// window_scale / spawn_distance / spawn_size and ignores the rest).
pub fn windows_table(plan: &Plan, p: &Policy) -> toml::map::Map<String, Value> {
    let r6 = |v: f64| Value::Float((v * 1e6).round() / 1e6);
    let r4 = |v: f64| Value::Float((v * 1e4).round() / 1e4);
    let mut t = toml::map::Map::new();
    t.insert("window_scale".into(), r6(plan.window_scale));
    t.insert("spawn_distance".into(), r6(plan.spawn_distance));
    let mut size = toml::map::Map::new();
    size.insert("x".into(), Value::Integer(plan.spawn_box.0));
    size.insert("y".into(), Value::Integer(plan.spawn_box.1));
    t.insert("spawn_size".into(), Value::Table(size));
    t.insert("pixel_exact".into(), Value::Boolean(true));
    t.insert("fit".into(), Value::String(p.fit.as_str().into()));
    t.insert("fit_margin".into(), r4(p.fit_margin_px));
    t.insert("safe_top".into(), r4(p.safe_insets_px[1]));
    t.insert("safe_bottom".into(), r4(p.safe_insets_px[3]));
    t.insert("pixel_snap".into(), Value::Boolean(p.pixel_snap));
    t.insert("cascade".into(), r4(p.cascade_px));
    t.insert("min_magnification".into(), r4(plan.m_min));
    if p.eyeline_offset_m.abs() > 1e-6 {
        t.insert("eyeline_offset".into(), r4(p.eyeline_offset_m));
    }
    if p.tilt_angle_deg.abs() > 1e-6 {
        t.insert("tilt_angle_deg".into(), r4(p.tilt_angle_deg));
    }
    if p.eyeline_offset_m.abs() > 1e-6 || p.tilt_angle_deg.abs() > 1e-6 {
        t.insert("open_effect_3d".into(), Value::String(p.open_effect_3d.clone()));
        t.insert("close_effect_3d".into(), Value::String(p.close_effect_3d.clone()));
    }
    t
}

/// The output the room is built for: `world.monitor`, else the focused one (Hypr3D targetMonitor()).
pub fn target(outputs: &[Output], wanted: Option<&str>) -> Option<usize> {
    if let Some(name) = wanted.filter(|n| !n.is_empty()) {
        if let Some(i) = outputs.iter().position(|o| o.name == name) {
            return Some(i);
        }
    }
    outputs.iter().position(|o| o.focused).or(if outputs.is_empty() { None } else { Some(0) })
}

/// The [desktop.mobile] verdict for an output: the handler's published file, else the same rule applied to the
/// output's actual logical size (display_scale::compute with its current scale as the explicit scale).
pub fn mobile_verdict(data: &Value, o: &Output, published: Option<&Json>) -> bool {
    if let Some(v) = published.and_then(|j| j.get("outputs")).and_then(|o2| o2.get(&o.name)).and_then(|v| v.get("mobile")).and_then(Json::as_bool) {
        return v;
    }
    let Some(mobile) = lookup(data, "desktop.mobile").and_then(Value::as_table) else { return false };
    if mobile.get("enabled").and_then(Value::as_bool) != Some(true) {
        return false;
    }
    let prefixes: Vec<&str> = mobile.get("remote_name_prefixes").and_then(Value::as_array)
        .map(|a| a.iter().filter_map(Value::as_str).collect()).unwrap_or_default();
    let remote = prefixes.iter().any(|p| o.name.starts_with(p));
    let origin = if remote { "remote" } else { "local" };
    if let Some(applies) = mobile.get("applies_to").and_then(Value::as_array) {
        if !applies.iter().any(|a| a.as_str() == Some(origin)) {
            return false;
        }
    }
    let Ok(policy) = crate::display_scale::Policy::from_ssot(data) else { return false };
    let input = crate::display_scale::Input {
        width: o.width,
        height: o.height,
        phys_mm: Some(o.phys_mm),
        dpi: None,
        transform: o.transform,
        remote,
        explicit_scale: Some(o.scale),
    };
    crate::display_scale::compute(&policy, &input).mobile
}

fn config_windows(config: &Value) -> Option<&toml::map::Map<String, Value>> {
    config.get("windows").and_then(Value::as_table)
}

/// The toggle overlay: plan the target output and add the windows keys the SSOT config does not set explicitly.
/// An explicit `[config.windows].window_scale` is an operator-fixed scale: pixel_exact is then not sent.
/// Returns the plan (None: the policy is off, or no output was found).
pub fn overlay(data: &Value, config: &mut Value, monitors: &Json, published: Option<&Json>) -> Result<Option<Plan>, String> {
    let Some(policy) = Policy::from_ssot(data)? else { return Ok(None) };
    let mut outputs: Vec<Output> = monitors.as_array().map(|a| a.iter().filter_map(Output::from_hyprctl).collect()).unwrap_or_default();
    let wanted = config.get("world").and_then(|w| w.get("monitor")).and_then(Value::as_str).map(str::to_owned);
    let Some(i) = target(&outputs, wanted.as_deref()) else { return Ok(None) };
    outputs[i].mobile = mobile_verdict(data, &outputs[i], published);
    let explicit = config_windows(config).cloned().unwrap_or_default();
    let distance = match explicit.get("spawn_distance") {
        Some(v) => num(v).filter(|d| *d > 0.0).ok_or("desktop.hyprland.hypr3d.config.windows.spawn_distance must be a positive number")?,
        None => policy.reading_distance_m,
    };
    let render_scale = config.get("render").and_then(|r| r.get("scale")).and_then(num).unwrap_or(1.0);
    let plan = plan(&policy, &outputs[i], distance, render_scale);
    let mut keys = windows_table(&plan, &policy);
    if explicit.contains_key("window_scale") {
        keys.remove("pixel_exact");
    }
    let table = config.as_table_mut().ok_or("desktop.hyprland.hypr3d.config must be a table")?
        .entry("windows").or_insert_with(|| Value::Table(Default::default()))
        .as_table_mut().ok_or("desktop.hyprland.hypr3d.config.windows must be a table")?;
    for (k, v) in keys {
        table.entry(k).or_insert(v);
    }
    Ok(Some(plan))
}

pub fn plan_json(plan: &Plan, p: &Policy) -> Json {
    let r = |v: f64, n: i32| (v * 10f64.powi(n)).round() / 10f64.powi(n);
    let stock = magnification(0.5, 5.0, plan.logical.1);
    let m = magnification(plan.window_scale, plan.spawn_distance, plan.logical.1);
    // the SSOT text size on screen (output px): flat desktop, the room as planned, the plugin default
    let font = p.text.size_px * plan.scale;
    json!({
        "output": plan.output,
        "logical": [r(plan.logical.0, 3), r(plan.logical.1, 3)],
        "orientation": plan.orientation,
        "mobile": plan.mobile,
        "d1": r(plan.d1, 4),
        "window_scale": r(plan.window_scale, 6),
        "spawn_distance": r(plan.spawn_distance, 4),
        "magnification": r(magnification(plan.window_scale, plan.spawn_distance, plan.logical.1), 6),
        "texel_per_px": r(1.0 / magnification(plan.window_scale, plan.spawn_distance, plan.logical.1), 6),
        "stock_magnification": r(stock, 4),
        "fit_box": [r(plan.fit_box.0, 3), r(plan.fit_box.1, 3)],
        "spawn_box": [plan.spawn_box.0, plan.spawn_box.1],
        "render_scale": plan.render_scale,
        "min_magnification": r(plan.m_min, 4),
        "text_px": {"font": p.text.size_px, "flat": r(font, 3), "room": r(font * m, 3), "stock_room": r(font * stock, 3)},
        "fit": p.fit.as_str(),
        "cascade_px": p.cascade_px,
        "pixel_snap": p.pixel_snap,
    })
}

/// `mios-gen hyprland-3d-spawn`: the plan for every output (or one), from a monitors file or the compositor.
pub fn run_cli(data: &Value, monitors: &Json, published: Option<&Json>, only: Option<&str>, render_scale: Option<f64>,
               windows: &[(f64, f64)]) -> Result<String, String> {
    let policy = Policy::from_ssot(data)?.ok_or("desktop.hyprland.hypr3d.spawn is off (absent, disabled or criterion \"fixed\")")?;
    let outputs: Vec<Output> = monitors.as_array().map(|a| a.iter().filter_map(Output::from_hyprctl).collect()).unwrap_or_default();
    if outputs.is_empty() {
        return Err("no outputs in the monitors list".into());
    }
    let r = render_scale.or_else(|| lookup(data, "desktop.hyprland.hypr3d.config.render.scale").and_then(num)).unwrap_or(1.0);
    let mut rows = Vec::new();
    for o in outputs.iter().filter(|o| only.is_none_or(|n| n == o.name)) {
        let mut o = o.clone();
        o.mobile = mobile_verdict(data, &o, published);
        let plan = plan(&policy, &o, policy.reading_distance_m, r);
        let mut row = plan_json(&plan, &policy);
        row["windows"] = Json::Array(windows.iter().map(|&(w, h)| {
            let pl = place(&plan, &policy, w, h, None);
            json!({"request": [w, h], "box": [pl.box_px.0, pl.box_px.1], "magnification": (pl.m * 1e6).round() / 1e6,
                   "resized": pl.resized, "pushed": pl.pushed, "small": pl.small})
        }).collect());
        let keys = windows_table(&plan, &policy);
        row["plugin_keys"] = serde_json::to_value(&keys).map_err(|e| e.to_string())?;
        rows.push(row);
    }
    if rows.is_empty() {
        return Err(format!("no output named {}", only.unwrap_or("")));
    }
    serde_json::to_string_pretty(&json!({"version": 1, "fov_deg": FOV_DEG, "px_per_unit": PX_PER_UNIT, "outputs": rows}))
        .map_err(|e| e.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ssot() -> Value {
        toml::from_str(r#"
[theme.font]
size = 12
cell_w_px = 10
cell_h_px = 20
chrome_w_px = 20
chrome_h_px = 12
[theme.shell]
edge_margin_px = 8
top_height_px = 28
dock_height_px = 56
[desktop.hyprland.hypr3d.config.render]
scale = 2.0
[desktop.hyprland.hypr3d.spawn]
enabled = true
criterion = "retina"
reading_distance_m = 5.0
fit = "resize"
fit_margin_px = "theme.shell.edge_margin_px"
min_text_px = 10
text_size_source = "theme.font"
landscape_cells = [94, 34]
portrait_cells = [66, 46]
mobile = "fill"
cascade_px = 32
pixel_snap = true
[desktop.mobile]
enabled = true
applies_to = ["local", "remote"]
remote_name_prefixes = ["HEADLESS-"]
[desktop.scaling]
enabled = true
apply_to = ["local", "remote"]
target_logical_dpi = 96.0
handheld_logical_dpi = 160.0
handheld_max_diagonal_in = 7.0
desktop_min_diagonal_in = 15.0
min_scale = 1.0
max_scale = 3.0
snap_step = 0.25
min_logical_short_px = 360
mobile_short_side_px = 600
mobile_max_long_side_px = 1400
portrait_is_mobile = false
min_physical_diagonal_mm = 60.0
edid_aspect_tolerance = 0.12
fallback_desktop_diagonal_in = 24.0
fallback_handheld_dpi = 460.0
handheld_shape_min_aspect = 1.6
handheld_shape_max_short_px = 1600
"#).unwrap()
    }

    const BARS: [f64; 4] = [0.0, 44.0, 0.0, 72.0];

    fn out(name: &str, w: i64, h: i64, s: f64, t: i64) -> Output {
        Output { name: name.into(), width: w, height: h, scale: s, transform: t, reserved: BARS, phys_mm: (0.0, 0.0), focused: false, mobile: false }
    }

    fn close(a: f64, b: f64, eps: f64) -> bool {
        (a - b).abs() <= eps
    }

    #[test]
    fn retina_scale_gives_magnification_one_on_every_output() {
        let p = Policy::from_ssot(&ssot()).unwrap().unwrap();
        // (output, logical, d1, window_scale) -- spawn_math.py / DESIGN.md section 4
        let cases = [
            (out("DP-1", 1920, 1080, 1.0, 0), (1920.0, 1080.0), 8.4763, 0.589880),
            (out("DP-2", 1920, 1080, 1.0, 1), (1080.0, 1920.0), 15.0690, 0.331807),
            (out("DP-3", 2560, 1440, 1.25, 0), (2048.0, 1152.0), 9.0414, 0.553012),
            (out("HEADLESS-PHONE", 1170, 2532, 3.0, 0), (390.0, 844.0), 6.6241, 0.754823),
        ];
        for (o, logical, want_d1, want_s) in cases {
            let pl = plan(&p, &o, 5.0, 2.0);
            assert_eq!(pl.logical, logical, "{}", o.name);
            assert!(close(pl.d1, want_d1, 1e-4), "{}: d1 {}", o.name, pl.d1);
            assert!(close(pl.window_scale, want_s, 1e-6), "{}: S {}", o.name, pl.window_scale);
            // the criterion: M = 1, one texel per output pixel
            assert!(close(magnification(pl.window_scale, pl.spawn_distance, logical.1), 1.0, 1e-12));
            // negative control: the stock 0.5 / 5.0 is NOT 1:1 on any of these outputs
            assert!((magnification(0.5, 5.0, logical.1) - 1.0).abs() > 0.05, "{}", o.name);
        }
        // the stock values the nest measured (landscape 0.8476, phone 0.6625)
        assert!(close(magnification(0.5, 5.0, 1080.0), 0.8476, 5e-4));
        assert!(close(magnification(0.5, 5.0, 844.0), 0.6625, 5e-4));
        // the edge-quality test's HEAD_ON (window_scale 0.5 at 4.238 m) is the same 1:1 point
        assert!(close(0.5 * d1(1080.0), 4.238, 1e-3));
    }

    #[test]
    fn fit_box_spawn_box_and_orientation_follow_the_output() {
        let p = Policy::from_ssot(&ssot()).unwrap().unwrap();
        let land = plan(&p, &out("DP-1", 1920, 1080, 1.0, 0), 5.0, 2.0);
        assert_eq!((land.fit_box, land.spawn_box, land.orientation), ((1904.0, 948.0), (960, 692), "landscape"));
        let mut no_bars = out("DP-1-no-reservations", 1920, 1080, 1.0, 0);
        no_bars.reserved = [0.0; 4];
        assert_eq!(plan(&p, &no_bars, 5.0, 2.0).fit_box, (1904.0, 948.0),
                   "SSOT topbar and dock insets still protect outputs with zero layer-shell reservations");
        let port = plan(&p, &out("DP-2", 1920, 1080, 1.0, 1), 5.0, 2.0);
        assert_eq!((port.fit_box, port.spawn_box, port.orientation), ((1064.0, 1788.0), (680, 932), "portrait"));
        let qhd = plan(&p, &out("DP-3", 2560, 1440, 1.25, 0), 5.0, 1.5);
        assert_eq!((qhd.fit_box, qhd.spawn_box), ((2032.0, 1020.0), (960, 692)));
        assert!(close(qhd.m_min, 10.0 / 12.0, 1e-12)); // the text floor dominates 1 / 1.5
        let mut tiny = p.clone();
        tiny.min_text_px = 4.0;
        let qt = plan(&tiny, &out("DP-3", 2560, 1440, 1.25, 0), 5.0, 1.5);
        assert!(close(qt.m_min, 1.0 / 1.5, 1e-12)); // igpu render.scale 1.5 sets the floor
        let mut phone = out("HEADLESS-PHONE", 1170, 2532, 3.0, 0);
        phone.mobile = true;
        let ph = plan(&p, &phone, 5.0, 2.0);
        assert_eq!((ph.fit_box, ph.spawn_box, ph.orientation), ((374.0, 712.0), (374, 712), "portrait"));
        // negative control: the same phone NOT mobile gets the (clamped) portrait cells, not the whole box
        phone.mobile = false;
        let pn = plan(&p, &phone, 5.0, 2.0);
        assert_eq!(pn.spawn_box, (374, 712)); // clamped by the fit box: 680x932 does not fit 374x712
        let mut cells = p.clone();
        cells.portrait_cells = (30, 20);
        assert_eq!(plan(&cells, &phone, 5.0, 2.0).spawn_box, (320, 412));
        phone.mobile = true;
        assert_eq!(plan(&cells, &phone, 5.0, 2.0).spawn_box, (374, 712), "mobile fill ignores the cells");
        // the legibility floor: max(1 / render.scale, 10 / 12)
        assert!(close(land.m_min, 10.0 / 12.0, 1e-12));
    }

    #[test]
    fn small_windows_open_at_one_large_windows_are_fitted_never_cropped() {
        let p = Policy::from_ssot(&ssot()).unwrap().unwrap();
        let land = plan(&p, &out("DP-1", 1920, 1080, 1.0, 0), 5.0, 2.0);
        // small: untouched, M = 1
        let s = place(&land, &p, 640.0, 400.0, None);
        assert_eq!((s.box_px, s.m, s.resized, s.pushed), ((640.0, 400.0), 1.0, false, false));
        // large: resized into the fit box, M stays 1
        let l = place(&land, &p, 2400.0, 1400.0, None);
        assert_eq!((l.box_px, l.m, l.resized, l.pushed), ((1904.0, 948.0), 1.0, true, false));
        // a client whose minimum is larger: pushed back to fit, never cropped
        let m = place(&land, &p, 2400.0, 1400.0, Some((2000.0, 1000.0)));
        assert_eq!(m.box_px, (2000.0, 1000.0));
        assert!(close(m.m, 0.948, 1e-12) && m.pushed && !m.small);
        assert!(m.box_px.0 * m.m <= land.fit_box.0 + 1e-9 && m.box_px.1 * m.m <= land.fit_box.1 + 1e-9);
        // far too large: below the legibility floor -> flagged
        let f = place(&land, &p, 4000.0, 3000.0, Some((4000.0, 3000.0)));
        assert!(f.pushed && f.small && f.m < land.m_min);
        // "distance": push back only (negative control for resize)
        let mut d = p.clone();
        d.fit = Fit::Distance;
        let pd = place(&land, &d, 2400.0, 1400.0, None);
        assert_eq!(pd.box_px, (2400.0, 1400.0));
        assert!(close(pd.m, (1904.0f64 / 2400.0).min(948.0 / 1400.0), 1e-12));
        // phone: a desktop-sized window is fitted to the phone's box
        let mut phone = out("HEADLESS-PHONE", 1170, 2532, 3.0, 0);
        phone.mobile = true;
        let ph = plan(&p, &phone, 5.0, 2.0);
        assert_eq!(place(&ph, &p, 960.0, 540.0, None).box_px, (374.0, 540.0));
    }

    #[test]
    fn policy_validation_and_text_source() {
        let data = ssot();
        let p = Policy::from_ssot(&data).unwrap().unwrap();
        assert_eq!(p.fit_margin_px, 8.0, "follows theme.shell.edge_margin_px");
        assert_eq!(p.safe_insets_px, [0.0, 44.0, 0.0, 72.0]);
        assert_eq!(p.text, Text { size_px: 12.0, cell: (10.0, 20.0), chrome: (20.0, 12.0) });
        // the margin follows its source
        let mut m = data.clone();
        m["theme"]["shell"]["edge_margin_px"] = Value::Integer(20);
        assert_eq!(Policy::from_ssot(&m).unwrap().unwrap().fit_margin_px, 20.0);
        m["theme"]["shell"]["dock_height_px"] = Value::Integer(72);
        assert_eq!(Policy::from_ssot(&m).unwrap().unwrap().safe_insets_px[3], 112.0);
        // off: absent, disabled, criterion fixed
        let mut off = data.clone();
        off["desktop"]["hyprland"]["hypr3d"].as_table_mut().unwrap().remove("spawn");
        assert!(Policy::from_ssot(&off).unwrap().is_none());
        for (k, v) in [("enabled", Value::Boolean(false)), ("criterion", Value::String("fixed".into()))] {
            let mut d = data.clone();
            d["desktop"]["hyprland"]["hypr3d"]["spawn"][k] = v;
            assert!(Policy::from_ssot(&d).unwrap().is_none(), "{k}");
        }
        // invalid values are rejected naming the key
        let bad = [
            ("criterion", Value::String("crisp".into())),
            ("reading_distance_m", Value::Float(0.0)),
            ("fit", Value::String("crop".into())),
            ("fit_margin_px", Value::String("theme.shell.nope".into())),
            ("fit_margin_px", Value::Float(-1.0)),
            ("landscape_cells", Value::Array(vec![Value::Integer(94)])),
            ("portrait_cells", Value::Array(vec![Value::Integer(0), Value::Integer(10)])),
            ("mobile", Value::String("full".into())),
            ("text_size_source", Value::String("theme.nofont".into())),
            ("cascade_px", Value::Float(1000.0)),
            ("pixel_snap", Value::String("yes".into())),
        ];
        for (k, v) in bad {
            let mut d = data.clone();
            d["desktop"]["hyprland"]["hypr3d"]["spawn"][k] = v.clone();
            let e = Policy::from_ssot(&d).unwrap_err();
            assert!(e.contains(&format!("spawn.{k}")), "{k} = {v:?}: {e}");
        }
        let mut nosize = data.clone();
        nosize["theme"]["font"].as_table_mut().unwrap().remove("cell_h_px");
        assert!(Policy::from_ssot(&nosize).unwrap_err().contains("theme.font.cell_h_px"));
    }

    fn monitors() -> Json {
        json!([
            {"name": "DP-1", "width": 1920, "height": 1080, "scale": 1.0, "transform": 0, "reserved": [0, 44, 0, 72],
             "physicalWidth": 530, "physicalHeight": 300, "focused": false},
            {"name": "HEADLESS-PHONE", "width": 1170, "height": 2532, "scale": 3.0, "transform": 0, "reserved": [0, 44, 0, 72],
             "physicalWidth": 0, "physicalHeight": 0, "focused": true}
        ])
    }

    #[test]
    fn toggle_overlay_targets_the_room_monitor_and_keeps_explicit_keys() {
        let data = ssot();
        // the focused output (the phone, mobile by its logical size 390x844)
        let mut config: Value = toml::from_str("[render]\nscale = 2.0\n").unwrap();
        let pl = overlay(&data, &mut config, &monitors(), None).unwrap().unwrap();
        assert_eq!((pl.output.as_str(), pl.mobile, pl.spawn_box), ("HEADLESS-PHONE", true, (374, 712)));
        let w = &config["windows"];
        assert_eq!(w["window_scale"].as_float(), Some(0.754823));
        assert_eq!(w["spawn_distance"].as_float(), Some(5.0));
        assert_eq!((w["spawn_size"]["x"].as_integer(), w["spawn_size"]["y"].as_integer()), (Some(374), Some(712)));
        assert_eq!(w["safe_top"].as_float(), Some(44.0));
        assert_eq!(w["safe_bottom"].as_float(), Some(72.0));
        assert_eq!((w["pixel_exact"].as_bool(), w["fit"].as_str(), w["cascade"].as_float()), (Some(true), Some("resize"), Some(32.0)));
        assert_eq!(w["min_magnification"].as_float(), Some(0.8333));
        // world.monitor wins over focus; the published verdict wins over the computed one
        let mut config: Value = toml::from_str("[world]\nmonitor = \"DP-1\"\n").unwrap();
        let published = json!({"outputs": {"DP-1": {"mobile": true}}});
        let pl = overlay(&data, &mut config, &monitors(), Some(&published)).unwrap().unwrap();
        assert_eq!((pl.output.as_str(), pl.mobile, pl.spawn_box), ("DP-1", true, (1904, 948)));
        let mut config: Value = toml::from_str("[world]\nmonitor = \"DP-1\"\n").unwrap();
        let pl = overlay(&data, &mut config, &monitors(), None).unwrap().unwrap();
            assert_eq!((pl.mobile, pl.spawn_box), (false, (960, 692)));
        assert_eq!(config["windows"]["window_scale"].as_float(), Some(0.58988));
        // explicit [config.windows] keys win; an explicit window_scale turns pixel_exact off
        let mut config: Value = toml::from_str("[world]\nmonitor = \"DP-1\"\n[windows]\nwindow_scale = 0.5\nspawn_distance = 4.0\n").unwrap();
        overlay(&data, &mut config, &monitors(), None).unwrap().unwrap();
        let w = &config["windows"];
        assert_eq!((w["window_scale"].as_float(), w["spawn_distance"].as_float()), (Some(0.5), Some(4.0)));
        assert!(w.get("pixel_exact").is_none());
        let mut config: Value = toml::from_str("[world]\nmonitor = \"DP-1\"\n[windows]\nspawn_distance = 4.0\n").unwrap();
        let pl = overlay(&data, &mut config, &monitors(), None).unwrap().unwrap();
        assert!(close(pl.window_scale, 4.0 / d1(1080.0), 1e-12));
        // negative controls: policy off sends nothing; no outputs sends nothing
        let mut off = data.clone();
        off["desktop"]["hyprland"]["hypr3d"]["spawn"]["criterion"] = Value::String("fixed".into());
        let mut config: Value = toml::from_str("[render]\nscale = 2.0\n").unwrap();
        assert!(overlay(&off, &mut config, &monitors(), None).unwrap().is_none());
        assert!(config.get("windows").is_none());
        assert!(overlay(&data, &mut config, &json!([]), None).unwrap().is_none());
    }

    #[test]
    fn cli_plan_lists_every_output() {
        let out = run_cli(&ssot(), &monitors(), None, None, None, &[(2400.0, 1400.0), (640.0, 400.0)]).unwrap();
        let j: Json = serde_json::from_str(&out).unwrap();
        let rows = j["outputs"].as_array().unwrap();
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0]["magnification"].as_f64(), Some(1.0));
        assert_eq!(rows[0]["stock_magnification"].as_f64(), Some(0.8476));
        // the SSOT 12 px text: 12 output px flat and in the room (1080p, scale 1); 36 px on the phone at scale 3
        assert_eq!(rows[0]["text_px"], json!({"font": 12.0, "flat": 12.0, "room": 12.0, "stock_room": 10.172}));
        assert_eq!((rows[1]["text_px"]["flat"].as_f64(), rows[1]["text_px"]["room"].as_f64()), (Some(36.0), Some(36.0)));
        assert!(rows[1]["text_px"]["stock_room"].as_f64().unwrap() < 24.0); // the plugin default shrinks it to 2/3
        assert_eq!(rows[0]["windows"][0]["box"], json!([1904.0, 948.0]));
        assert_eq!(rows[1]["mobile"].as_bool(), Some(true));
        assert!(run_cli(&ssot(), &monitors(), None, Some("NOPE"), None, &[]).unwrap_err().contains("NOPE"));
    }
}
