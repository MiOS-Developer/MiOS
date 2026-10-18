// AI-hint: One global display scale policy ([desktop.scaling]) for every output, local and remote: density (client DPI hint > EDID mm > resolution fallback) x viewing-distance class (handheld..desktop by diagonal) x orientation -> a scale Hyprland accepts (1/120 grid, integer logical size) + logical size + mobile flag (logical short side). Rust `compute` and the Lua twin `LUA` (compiled into the Hyprland projection's [desktop.mobile] handler) implement the same arithmetic; a parity test runs both. CLI: mios-gen display-scale.
// AI-related: usr/share/mios/mios.toml [desktop.scaling] [desktop.mobile] [remote.rdp], tools/native/mios-gen/src/render.rs (mobile_profile), usr/share/mios/quickshell/Mobile.qml, usr/libexec/mios/mios-rdp-rfb-bridge
//! Global display scale policy.
//!
//! Inputs per output: mode pixels, transform, physical size (EDID mm; 0 = unknown), an optional
//! client DPI hint (remote displays), whether the output is remote, and an optional explicit
//! scale (operator-saved). Output: scale, logical size, mobile flag, and where the density came
//! from. Every number the policy uses comes from [desktop.scaling]; nothing is keyed to an output
//! name or a GPU vendor.

use serde_json::{json, Value as Json};
use toml::Value;

/// Hyprland 0.56 keeps monitor scales on a 1/120 grid and refuses a scale whose logical size is
/// fractional; the policy works in integer 120ths so Rust and Lua agree bit for bit.
pub const GRID: i64 = 120;

#[derive(Debug, Clone, PartialEq)]
pub struct Policy {
    pub enabled: bool,
    pub apply_to: Vec<String>,
    pub target_logical_dpi: f64,
    pub handheld_logical_dpi: f64,
    pub handheld_max_diagonal_in: f64,
    pub desktop_min_diagonal_in: f64,
    pub min_scale: f64,
    pub max_scale: f64,
    pub snap_step: f64,
    pub min_logical_short_px: i64,
    pub mobile_short_side_px: i64,
    pub mobile_max_long_side_px: i64,
    pub portrait_is_mobile: bool,
    pub min_physical_diagonal_mm: f64,
    pub edid_aspect_tolerance: f64,
    pub fallback_desktop_diagonal_in: f64,
    pub fallback_handheld_dpi: f64,
    pub handheld_shape_min_aspect: f64,
    pub handheld_shape_max_short_px: i64,
}

#[derive(Debug, Clone, Default, PartialEq)]
pub struct Input {
    pub width: i64,
    pub height: i64,
    /// EDID physical size in mm (unrotated, like the mode); None or 0 = unknown.
    pub phys_mm: Option<(f64, f64)>,
    /// Client DPI hint (xrdp journal for RDP clients); None or <= 0 = none.
    pub dpi: Option<f64>,
    pub transform: i64,
    pub remote: bool,
    /// Operator-saved scale ([desktop.hyprland.monitors.<output>].scale): never auto-scaled.
    pub explicit_scale: Option<f64>,
}

#[derive(Debug, Clone, PartialEq)]
pub struct Decision {
    pub scale: f64,
    pub scale_120: i64,
    pub logical_width: i64,
    pub logical_height: i64,
    pub mobile: bool,
    pub explicit: bool,
    pub source: &'static str,
    pub dpi: f64,
    pub diagonal_in: f64,
    pub target_dpi: f64,
    pub auto_apply: bool,
}

impl Policy {
    /// Validate [desktop.scaling] (every key required; ranges documented in the SSOT).
    pub fn from_ssot(data: &Value) -> Result<Policy, String> {
        let t = data
            .get("desktop")
            .and_then(|d| d.get("scaling"))
            .and_then(Value::as_table)
            .ok_or("desktop.scaling must be a table")?;
        let num = |k: &str, lo: f64, hi: f64| -> Result<f64, String> {
            t.get(k)
                .and_then(|v| v.as_float().or_else(|| v.as_integer().map(|i| i as f64)))
                .filter(|v| v.is_finite() && *v >= lo && *v <= hi)
                .ok_or(format!("desktop.scaling.{k} must be a number in {lo}..={hi}"))
        };
        let int = |k: &str, lo: i64, hi: i64| -> Result<i64, String> {
            t.get(k)
                .and_then(Value::as_integer)
                .filter(|v| (lo..=hi).contains(v))
                .ok_or(format!("desktop.scaling.{k} must be an integer in {lo}..={hi}"))
        };
        let flag = |k: &str| -> Result<bool, String> {
            t.get(k).and_then(Value::as_bool).ok_or(format!("desktop.scaling.{k} must be a boolean"))
        };
        let apply_to = t
            .get("apply_to")
            .and_then(Value::as_array)
            .ok_or("desktop.scaling.apply_to must be an array")?
            .iter()
            .map(|v| {
                v.as_str()
                    .filter(|s| *s == "local" || *s == "remote")
                    .map(str::to_owned)
                    .ok_or("desktop.scaling.apply_to entries must be \"local\" or \"remote\"".to_string())
            })
            .collect::<Result<Vec<_>, _>>()?;
        let p = Policy {
            enabled: flag("enabled")?,
            apply_to,
            target_logical_dpi: num("target_logical_dpi", 48.0, 480.0)?,
            handheld_logical_dpi: num("handheld_logical_dpi", 48.0, 480.0)?,
            handheld_max_diagonal_in: num("handheld_max_diagonal_in", 1.0, 100.0)?,
            desktop_min_diagonal_in: num("desktop_min_diagonal_in", 1.0, 200.0)?,
            min_scale: num("min_scale", 0.5, 4.0)?,
            max_scale: num("max_scale", 0.5, 4.0)?,
            snap_step: num("snap_step", 1.0 / 120.0, 1.0)?,
            min_logical_short_px: int("min_logical_short_px", 0, 4096)?,
            mobile_short_side_px: int("mobile_short_side_px", 0, 8192)?,
            mobile_max_long_side_px: int("mobile_max_long_side_px", 0, 16384)?,
            portrait_is_mobile: flag("portrait_is_mobile")?,
            min_physical_diagonal_mm: num("min_physical_diagonal_mm", 0.0, 10000.0)?,
            edid_aspect_tolerance: num("edid_aspect_tolerance", 0.0, 1.0)?,
            fallback_desktop_diagonal_in: num("fallback_desktop_diagonal_in", 1.0, 200.0)?,
            fallback_handheld_dpi: num("fallback_handheld_dpi", 48.0, 1200.0)?,
            handheld_shape_min_aspect: num("handheld_shape_min_aspect", 1.0, 10.0)?,
            handheld_shape_max_short_px: int("handheld_shape_max_short_px", 0, 16384)?,
        };
        if p.min_scale > p.max_scale {
            return Err("desktop.scaling.min_scale must not exceed max_scale".into());
        }
        if p.handheld_max_diagonal_in >= p.desktop_min_diagonal_in {
            return Err("desktop.scaling.handheld_max_diagonal_in must be below desktop_min_diagonal_in".into());
        }
        let step = (p.snap_step * GRID as f64).round();
        if (p.snap_step * GRID as f64 - step).abs() > 1e-6 {
            return Err("desktop.scaling.snap_step must be a multiple of 1/120".into());
        }
        Ok(p)
    }

    /// Lua table literal of the policy for the projection ([`LUA`] reads these fields).
    pub fn lua_table(&self) -> String {
        let f = |v: f64| format!("{v:?}");
        format!(
            "{{enabled = {}, apply_local = {}, apply_remote = {}, target_dpi = {}, handheld_dpi = {}, handheld_diag = {}, desktop_diag = {}, min_scale = {}, max_scale = {}, snap_step = {}, min_short = {}, mobile_short = {}, mobile_long = {}, portrait_mobile = {}, min_phys_mm = {}, edid_tol = {}, fb_desktop_diag = {}, fb_handheld_dpi = {}, hh_aspect = {}, hh_short = {}}}",
            self.enabled,
            self.apply_to.iter().any(|s| s == "local"),
            self.apply_to.iter().any(|s| s == "remote"),
            f(self.target_logical_dpi),
            f(self.handheld_logical_dpi),
            f(self.handheld_max_diagonal_in),
            f(self.desktop_min_diagonal_in),
            f(self.min_scale),
            f(self.max_scale),
            f(self.snap_step),
            self.min_logical_short_px,
            self.mobile_short_side_px,
            self.mobile_max_long_side_px,
            self.portrait_is_mobile,
            f(self.min_physical_diagonal_mm),
            f(self.edid_aspect_tolerance),
            f(self.fallback_desktop_diagonal_in),
            f(self.fallback_handheld_dpi),
            f(self.handheld_shape_min_aspect),
            self.handheld_shape_max_short_px,
        )
    }
}

fn round_i(x: f64) -> i64 {
    (x + 0.5).floor() as i64
}

/// The policy. Keep in step with [`LUA`] (same operations in the same order; parity-tested).
pub fn compute(p: &Policy, i: &Input) -> Decision {
    let g = GRID as f64;
    let (mut w, mut h) = (i.width.max(1), i.height.max(1));
    let (mut pw, mut ph) = i.phys_mm.unwrap_or((0.0, 0.0));
    if i.transform.rem_euclid(2) == 1 {
        std::mem::swap(&mut w, &mut h);
        std::mem::swap(&mut pw, &mut ph);
    }
    let (wf, hf) = (w as f64, h as f64);
    let diag_px = (wf * wf + hf * hf).sqrt();
    let short = w.min(h);
    let long = w.max(h);
    let aspect = long as f64 / short as f64;
    let diag_mm = (pw * pw + ph * ph).sqrt();
    let edid_ok = pw > 0.0
        && ph > 0.0
        && diag_mm >= p.min_physical_diagonal_mm
        && ((pw / ph) / (wf / hf) - 1.0).abs() <= p.edid_aspect_tolerance;
    let hint = i.dpi.filter(|d| d.is_finite() && *d > 0.0);
    let handheld_shape =
        i.remote && short <= p.handheld_shape_max_short_px && (h > w || aspect >= p.handheld_shape_min_aspect);
    let (source, dpi, diag_in) = if let Some(d) = hint {
        ("dpi-hint", d, diag_px / d)
    } else if edid_ok {
        let din = diag_mm / 25.4;
        ("edid", diag_px / din, din)
    } else if handheld_shape {
        ("fallback-handheld", p.fallback_handheld_dpi, diag_px / p.fallback_handheld_dpi)
    } else {
        ("fallback-desktop", diag_px / p.fallback_desktop_diagonal_in, p.fallback_desktop_diagonal_in)
    };
    let target = if diag_in <= p.handheld_max_diagonal_in {
        p.handheld_logical_dpi
    } else if diag_in >= p.desktop_min_diagonal_in {
        p.target_logical_dpi
    } else {
        p.handheld_logical_dpi
            + (p.target_logical_dpi - p.handheld_logical_dpi) * (diag_in - p.handheld_max_diagonal_in)
                / (p.desktop_min_diagonal_in - p.handheld_max_diagonal_in)
    };
    let step = round_i(p.snap_step * g);
    let kmin = round_i(p.min_scale * g);
    let kmax = round_i(p.max_scale * g);
    let mut k = round_i(dpi / target * g / step as f64) * step;
    k = k.min(kmax);
    if p.min_logical_short_px > 0 {
        k = k.min(short * GRID / p.min_logical_short_px);
    }
    k = k.max(kmin);
    let mut valid = GRID;
    let mut c = k;
    while c >= kmin && c >= 1 {
        if (w * GRID) % c == 0 && (h * GRID) % c == 0 {
            valid = c;
            break;
        }
        c -= 1;
    }
    if !p.enabled {
        valid = GRID;
    }
    let explicit = i.explicit_scale.filter(|s| s.is_finite() && *s > 0.0);
    let (scale, k120, source) = match explicit {
        // An operator's explicit scale is kept, but on Hyprland's terms: snapped onto the 1/120 grid
        // and down to the nearest scale with an integral logical size (Hyprland refuses any other
        // and substitutes its own), so `scale` and `scale_120` always describe the same value.
        Some(s) => {
            let mut ke = round_i(s * g).max(1);
            while ke > 1 && !((w * GRID) % ke == 0 && (h * GRID) % ke == 0) {
                ke -= 1;
            }
            (ke as f64 / g, ke, "explicit")
        }
        None if !p.enabled => (1.0, GRID, "disabled"),
        None => (valid as f64 / g, valid, source),
    };
    let lw = round_i(wf / scale);
    let lh = round_i(hf / scale);
    let (ls, ll) = (lw.min(lh), lw.max(lh));
    let mobile =
        (ls < p.mobile_short_side_px && ll < p.mobile_max_long_side_px) || (p.portrait_is_mobile && lh > lw);
    let origin = if i.remote { "remote" } else { "local" };
    Decision {
        scale,
        scale_120: k120,
        logical_width: lw,
        logical_height: lh,
        mobile,
        explicit: explicit.is_some(),
        source,
        dpi,
        diagonal_in: diag_in,
        target_dpi: target,
        auto_apply: p.enabled && explicit.is_none() && p.apply_to.iter().any(|a| a == origin),
    }
}

impl Decision {
    pub fn to_json(&self, input: &Input, attach_below: Option<(i64, i64, i64, i64)>) -> Json {
        let r1 = |x: f64| (x * 10.0).round() / 10.0;
        let mut v = json!({
            "version": 1,
            "scale": (self.scale * 1e6).round() / 1e6,
            "scale_120": self.scale_120,
            "logical_width": self.logical_width,
            "logical_height": self.logical_height,
            "mobile": self.mobile,
            "explicit": self.explicit,
            "auto_apply": self.auto_apply,
            "source": self.source,
            "dpi": r1(self.dpi),
            "diagonal_in": r1(self.diagonal_in),
            "target_dpi": r1(self.target_dpi),
            "orientation": if self.logical_height > self.logical_width { "portrait" } else { "landscape" },
            "origin": if input.remote { "remote" } else { "local" },
        });
        if let Some((x, y, w, h)) = attach_below {
            // Bottom-centre attachment: the output's top edge is flush with the main display's
            // bottom edge, centred on it, so the cursor and windows cross the shared edge.
            v["position"] = json!({"x": x + (w - self.logical_width).div_euclid(2), "y": y + h});
        }
        v
    }
}

/// `mios-gen display-scale` arguments (see the CLI contract in the SSOT [desktop.scaling] comment).
pub struct CliArgs {
    pub width: i64,
    pub height: i64,
    pub phys_mm: Option<String>,
    pub dpi: Option<f64>,
    pub transform: i64,
    pub remote: bool,
    pub output: Option<String>,
    pub explicit_scale: Option<f64>,
    pub attach_below: Option<String>,
}

fn parse_pair(s: &str) -> Result<(f64, f64), String> {
    let (a, b) = s.split_once(['x', 'X']).ok_or(format!("--phys-mm must be WxH, got {s:?}"))?;
    let a: f64 = a.trim().parse().map_err(|_| format!("--phys-mm must be WxH, got {s:?}"))?;
    let b: f64 = b.trim().parse().map_err(|_| format!("--phys-mm must be WxH, got {s:?}"))?;
    if !(a.is_finite() && b.is_finite() && a >= 0.0 && b >= 0.0) {
        return Err(format!("--phys-mm must be non-negative, got {s:?}"));
    }
    Ok((a, b))
}

fn parse_rect(s: &str) -> Result<(i64, i64, i64, i64), String> {
    let v: Vec<i64> = s
        .split(',')
        .map(|x| x.trim().parse::<i64>())
        .collect::<Result<_, _>>()
        .map_err(|_| format!("--attach-below must be X,Y,W,H integers, got {s:?}"))?;
    match v[..] {
        [x, y, w, h] if w > 0 && h > 0 => Ok((x, y, w, h)),
        _ => Err(format!("--attach-below must be X,Y,W,H with W,H > 0, got {s:?}")),
    }
}

/// CLI entry: validate inputs, load the layered SSOT, decide, render JSON (or a text line).
pub fn run_cli(root: std::path::PathBuf, a: CliArgs, json_out: bool) -> Result<String, String> {
    if !(1..=32768).contains(&a.width) || !(1..=32768).contains(&a.height) {
        return Err("--width/--height must be 1..=32768".into());
    }
    if !(0..=7).contains(&a.transform) {
        return Err("--transform must be 0..7".into());
    }
    if let Some(d) = a.dpi {
        if !(d.is_finite() && (0.0..=4000.0).contains(&d)) {
            return Err("--dpi must be 0..=4000 (0 = no hint)".into());
        }
    }
    if let Some(s) = a.explicit_scale {
        if !(s.is_finite() && (0.25..=4.0).contains(&s)) {
            return Err("--explicit-scale must be 0.25..=4".into());
        }
    }
    if let Some(name) = &a.output {
        if name.is_empty() || !name.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_') {
            return Err("--output must be a connector name [A-Za-z0-9_-]".into());
        }
    }
    let phys_mm = a.phys_mm.as_deref().map(parse_pair).transpose()?;
    let attach = a.attach_below.as_deref().map(parse_rect).transpose()?;
    let data = crate::terminal::tmux_theme::tmux_runtime::load_layered(&root)?;
    let policy = Policy::from_ssot(&data)?;
    let explicit = match (a.explicit_scale, &a.output) {
        (Some(s), _) => Some(s),
        (None, Some(name)) => crate::render::render_hyprland::explicit_scales(&data)?
            .get(name)
            .and_then(|v| v.as_float().or_else(|| v.as_integer().map(|i| i as f64))),
        _ => None,
    };
    let input = Input {
        width: a.width,
        height: a.height,
        phys_mm,
        dpi: a.dpi.filter(|d| *d > 0.0),
        transform: a.transform,
        remote: a.remote,
        explicit_scale: explicit,
    };
    let d = compute(&policy, &input);
    let mut v = d.to_json(&input, attach);
    if let Some(s) = explicit {
        if (s - d.scale).abs() > 1e-9 {
            // the requested explicit scale was not one Hyprland accepts for this mode: say so
            v["explicit_requested"] = json!(s);
        }
    }
    if let Some(name) = &a.output {
        v["output"] = Json::String(name.clone());
    }
    if json_out {
        serde_json::to_string(&v).map_err(|e| e.to_string())
    } else {
        Ok(format!(
            "scale={} logical={}x{} mobile={} explicit={} auto_apply={} source={} dpi={:.1}",
            v["scale"], d.logical_width, d.logical_height, d.mobile, d.explicit, d.auto_apply, d.source, d.dpi
        ))
    }
}

/// Lua twin of [`compute`] for the Hyprland projection: `mios_scale_policy(P, m)` with P the
/// [`Policy::lua_table`] literal and m = {width, height, pw, ph, dpi, transform, remote, explicit}.
/// Integer 120ths and the same operation order as Rust; the parity test runs both.
pub const LUA: &str = r#"local function mios_scale_policy(P, m)
 local G = 120
 local function round_i(x) return math.floor(x + 0.5) end
 local w, h = math.max(1, math.floor(tonumber(m.width) or 0)), math.max(1, math.floor(tonumber(m.height) or 0))
 local pw, ph = tonumber(m.pw) or 0, tonumber(m.ph) or 0
 if (tonumber(m.transform) or 0) % 2 == 1 then w, h = h, w; pw, ph = ph, pw end
 local diag_px = math.sqrt(w * w + h * h)
 local short, long = math.min(w, h), math.max(w, h)
 local aspect = long / short
 local diag_mm = math.sqrt(pw * pw + ph * ph)
 local edid_ok = pw > 0 and ph > 0 and diag_mm >= P.min_phys_mm and math.abs((pw / ph) / (w / h) - 1.0) <= P.edid_tol
 local hint = tonumber(m.dpi)
 if hint and not (hint > 0 and hint < math.huge) then hint = nil end
 local handheld_shape = m.remote and short <= P.hh_short and (h > w or aspect >= P.hh_aspect)
 local source, dpi, diag_in
 if hint then source, dpi, diag_in = "dpi-hint", hint, diag_px / hint
 elseif edid_ok then diag_in = diag_mm / 25.4; source, dpi = "edid", diag_px / diag_in
 elseif handheld_shape then source, dpi, diag_in = "fallback-handheld", P.fb_handheld_dpi, diag_px / P.fb_handheld_dpi
 else source, dpi, diag_in = "fallback-desktop", diag_px / P.fb_desktop_diag, P.fb_desktop_diag end
 local target
 if diag_in <= P.handheld_diag then target = P.handheld_dpi
 elseif diag_in >= P.desktop_diag then target = P.target_dpi
 else target = P.handheld_dpi + (P.target_dpi - P.handheld_dpi) * (diag_in - P.handheld_diag) / (P.desktop_diag - P.handheld_diag) end
 local step, kmin, kmax = round_i(P.snap_step * G), round_i(P.min_scale * G), round_i(P.max_scale * G)
 local k = round_i(dpi / target * G / step) * step
 k = math.min(k, kmax)
 if P.min_short > 0 then k = math.min(k, math.floor(short * G / P.min_short)) end
 k = math.max(k, kmin)
 local valid, c = G, k
 while c >= kmin and c >= 1 do
  if (w * G) % c == 0 and (h * G) % c == 0 then valid = c break end
  c = c - 1
 end
 if not P.enabled then valid = G end
 local ex = tonumber(m.explicit)
 if ex and not (ex > 0 and ex < math.huge) then ex = nil end
 local scale, k120
 if ex then
  local ke = math.max(1, round_i(ex * G))
  while ke > 1 and not ((w * G) % ke == 0 and (h * G) % ke == 0) do ke = ke - 1 end
  scale, k120, source = ke / G, ke, "explicit"
 elseif not P.enabled then scale, k120, source = 1.0, G, "disabled"
 else scale, k120 = valid / G, valid end
 local lw, lh = round_i(w / scale), round_i(h / scale)
 local ls, ll = math.min(lw, lh), math.max(lw, lh)
 local mobile = (ls < P.mobile_short and ll < P.mobile_long) or (P.portrait_mobile and lh > lw)
 local auto = P.enabled and not ex and ((m.remote and P.apply_remote) or (not m.remote and P.apply_local))
 return {scale = scale, scale_120 = k120, logical_width = lw, logical_height = lh, mobile = mobile and true or false,
  explicit = ex ~= nil, source = source, dpi = dpi, diagonal_in = diag_in, target_dpi = target, auto_apply = auto and true or false}
end
"#;

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    fn vendor() -> Value {
        let path = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../usr/share/mios/mios.toml");
        std::fs::read_to_string(path).unwrap().parse().unwrap()
    }
    fn policy() -> Policy {
        Policy::from_ssot(&vendor()).expect("vendor [desktop.scaling] validates")
    }
    /// Physical mm of a WxH panel at `dpi`.
    fn mm_at(w: i64, h: i64, dpi: f64) -> (f64, f64) {
        (w as f64 / dpi * 25.4, h as f64 / dpi * 25.4)
    }
    /// Physical mm of a 16:9 panel with diagonal `inches`.
    fn mm_diag(inches: f64) -> (f64, f64) {
        let d = inches * 25.4;
        let k = d / (16f64 * 16.0 + 9.0 * 9.0).sqrt();
        (16.0 * k, 9.0 * k)
    }

    #[test]
    fn phone_568dpi_scales_2_5_to_3_and_is_mobile() {
        let p = policy();
        // EDID path (a local phone panel) and the RDP client-DPI path agree.
        for i in [
            Input { width: 1100, height: 2080, phys_mm: Some(mm_at(1100, 2080, 568.0)), ..Default::default() },
            Input { width: 1100, height: 2080, dpi: Some(568.0), remote: true, ..Default::default() },
        ] {
            let d = compute(&p, &i);
            assert!((2.5..=3.0).contains(&d.scale), "{d:?}");
            assert!(d.logical_width.min(d.logical_height) < p.mobile_short_side_px, "{d:?}");
            assert!(d.mobile, "{d:?}");
            assert_eq!(d.logical_width * d.scale_120, 1100 * GRID, "logical size must be integral: {d:?}");
            assert_eq!(d.logical_height * d.scale_120, 2080 * GRID, "logical size must be integral: {d:?}");
        }
    }

    #[test]
    fn desktop_24in_1080p_is_scale_1_not_mobile() {
        // The operator's LG 24GL600F reports 530x300 mm.
        let d = compute(&policy(), &Input { width: 1920, height: 1080, phys_mm: Some((530.0, 300.0)), ..Default::default() });
        assert_eq!(d.scale, 1.0, "{d:?}");
        assert_eq!((d.logical_width, d.logical_height), (1920, 1080));
        assert!(!d.mobile && d.source == "edid", "{d:?}");
    }

    #[test]
    fn uhd_27in_scales_1_5_to_2_not_mobile() {
        let d = compute(&policy(), &Input { width: 3840, height: 2160, phys_mm: Some(mm_diag(27.0)), ..Default::default() });
        assert!((1.5..=2.0).contains(&d.scale), "{d:?}");
        assert_eq!(d.logical_width * d.scale_120, 3840 * GRID, "{d:?}");
        assert!(!d.mobile, "{d:?}");
    }

    #[test]
    fn portrait_low_dpi_tablet_is_orientation_invariant() {
        let p = policy();
        let portrait = compute(&p, &Input { width: 1080, height: 1920, dpi: Some(150.0), remote: true, ..Default::default() });
        // Same panel scanned out landscape and rotated by the compositor (transform 1).
        let rotated =
            compute(&p, &Input { width: 1920, height: 1080, dpi: Some(150.0), transform: 1, remote: true, ..Default::default() });
        assert_eq!(portrait, rotated);
        assert!((1.0..=1.75).contains(&portrait.scale), "{portrait:?}");
        assert!(portrait.logical_height > portrait.logical_width, "{portrait:?}");
        // Short logical side >= the breakpoint: a tablet, not a phone (portrait_is_mobile = false).
        assert!(!portrait.mobile, "{portrait:?}");
        let mut q = p.clone();
        q.portrait_is_mobile = true;
        assert!(compute(&q, &Input { width: 1080, height: 1920, dpi: Some(150.0), ..Default::default() }).mobile);
    }

    #[test]
    fn unknown_physical_size_falls_back_by_resolution() {
        let p = policy();
        let uhd = compute(&p, &Input { width: 3840, height: 2160, phys_mm: Some((0.0, 0.0)), ..Default::default() });
        assert_eq!(uhd.source, "fallback-desktop");
        assert!(uhd.scale > 1.5 && !uhd.mobile, "{uhd:?}");
        let fhd = compute(&p, &Input { width: 1920, height: 1080, ..Default::default() });
        assert_eq!((fhd.scale, fhd.mobile), (1.0, false), "{fhd:?}");
        // A remote phone-shaped display with no DPI hint (headless: 0 mm) is a handheld.
        let phone = compute(&p, &Input { width: 1100, height: 2080, remote: true, ..Default::default() });
        assert_eq!(phone.source, "fallback-handheld");
        assert!(phone.mobile && phone.scale >= 2.0, "{phone:?}");
        // Negative: the same pixels on a LOCAL output are not guessed to be a phone.
        let local = compute(&p, &Input { width: 1100, height: 2080, ..Default::default() });
        assert_eq!(local.source, "fallback-desktop");
        // Negative: a landscape 1280x720 remote window is a desktop client.
        let rdp = compute(&p, &Input { width: 1280, height: 720, remote: true, ..Default::default() });
        assert_eq!((rdp.scale, rdp.mobile), (1.0, false), "{rdp:?}");
        // Bogus EDID (aspect-only 16x9 cm on a 1080p TV) is rejected, not read as a 7" panel.
        let tv = compute(&p, &Input { width: 1920, height: 1080, phys_mm: Some((16.0, 9.0)), ..Default::default() });
        assert_eq!(tv.source, "fallback-desktop", "{tv:?}");
    }

    #[test]
    fn explicit_scale_is_never_overridden() {
        let p = policy();
        // HDMI-A-1 saved at scale 1; feed inputs that would auto-scale it (phone density).
        let i = Input {
            width: 1920,
            height: 1080,
            phys_mm: Some(mm_at(1920, 1080, 568.0)),
            explicit_scale: Some(1.0),
            ..Default::default()
        };
        let d = compute(&p, &i);
        assert_eq!((d.scale, d.explicit, d.auto_apply, d.source), (1.0, true, false, "explicit"), "{d:?}");
        assert_eq!((d.logical_width, d.logical_height, d.mobile), (1920, 1080, false));
        // Positive control: without the explicit scale the same input IS auto-scaled.
        let auto = compute(&p, &Input { explicit_scale: None, ..i });
        assert!(auto.scale > 1.0 && auto.auto_apply, "{auto:?}");
    }

    #[test]
    fn explicit_scale_snaps_to_a_hyprland_valid_scale() {
        let p = policy();
        let base = Input { width: 1100, height: 2080, phys_mm: None, dpi: None, transform: 0, remote: true, explicit_scale: None };
        for (w, h, ex) in [(1100, 2080, 2.4), (1920, 1080, 1.33), (2560, 1440, 1.7), (1100, 2080, 2.5), (3840, 2160, 1.5)] {
            let d = compute(&p, &Input { width: w, height: h, explicit_scale: Some(ex), ..base });
            assert_eq!(d.source, "explicit");
            assert!(d.explicit && !d.auto_apply, "{d:?}");
            // scale and scale_120 are one value, on the grid, with an integral logical size
            assert_eq!(round_i(d.scale * GRID as f64), d.scale_120, "{d:?}");
            assert!((d.scale * GRID as f64 - d.scale_120 as f64).abs() < 1e-9, "{d:?}");
            assert_eq!((w * GRID) % d.scale_120, 0, "{d:?}");
            assert_eq!((h * GRID) % d.scale_120, 0, "{d:?}");
            // onto the grid (nearest 1/120), then down to a valid one -- never above the grid value
            assert!(d.scale_120 <= round_i(ex * GRID as f64), "{d:?}");
        }
        // Valid explicit scales are kept exactly (positive control) ...
        let d = compute(&p, &Input { explicit_scale: Some(2.5), ..base });
        assert_eq!((d.scale, d.scale_120), (2.5, 300));
        let d = compute(&p, &Input { width: 1920, height: 1080, explicit_scale: Some(1.33), ..base });
        assert_eq!(d.scale_120, 160);
        // ... and an invalid one is never passed through raw (negative control: 2.4 -> 288/120 has a
        // fractional logical size for 1100x2080, which Hyprland would refuse).
        assert_ne!((1100 * GRID) % 288, 0);
        let d = compute(&p, &Input { explicit_scale: Some(2.4), ..base });
        assert_ne!(d.scale, 2.4);
        assert!(d.scale_120 < 288);
    }

    #[test]
    fn remote_auto_apply_follows_apply_to_and_disabled_means_1() {
        let p = policy();
        let d = compute(&p, &Input { width: 1100, height: 2080, dpi: Some(568.0), remote: true, ..Default::default() });
        assert_eq!(d.auto_apply, p.apply_to.iter().any(|a| a == "remote"));
        let mut q = p.clone();
        q.enabled = false;
        let off = compute(&q, &Input { width: 3840, height: 2160, phys_mm: Some(mm_diag(13.0)), ..Default::default() });
        assert_eq!((off.scale, off.source, off.auto_apply), (1.0, "disabled", false));
    }

    #[test]
    fn bad_ssot_values_are_rejected() {
        assert!(Policy::from_ssot(&vendor()).is_ok());
        for (k, v) in [
            ("min_scale", Value::Float(5.0)),
            ("snap_step", Value::Float(0.3333)),
            ("apply_to", Value::Array(vec![Value::String("phone".into())])),
            ("target_logical_dpi", Value::String("96".into())),
            ("handheld_max_diagonal_in", Value::Float(30.0)),
        ] {
            let mut d = vendor();
            d["desktop"]["scaling"].as_table_mut().unwrap().insert(k.into(), v.clone());
            assert!(Policy::from_ssot(&d).is_err(), "{k}={v:?} accepted");
        }
        let mut d = vendor();
        d["desktop"].as_table_mut().unwrap().remove("scaling");
        assert!(Policy::from_ssot(&d).is_err());
    }

    #[test]
    fn attach_below_centres_on_the_main_display_bottom_edge() {
        let p = policy();
        let i = Input { width: 1100, height: 2080, dpi: Some(568.0), remote: true, ..Default::default() };
        let d = compute(&p, &i);
        let j = d.to_json(&i, Some((0, 0, 1920, 1080)));
        assert_eq!(j["position"]["y"], 1080);
        assert_eq!(j["position"]["x"], (1920 - d.logical_width) / 2);
        assert!(d.to_json(&i, None).get("position").is_none());
    }

    /// printf("%.9g") for the parity comparison.
    fn fmt_g(x: f64) -> String {
        let exp = if x == 0.0 { 0 } else { x.abs().log10().floor() as i32 };
        let decimals = (8 - exp).max(0) as usize;
        let s = format!("{x:.decimals$}");
        if s.contains('.') {
            s.trim_end_matches('0').trim_end_matches('.').to_string()
        } else {
            s
        }
    }

    /// The Lua twin returns exactly what Rust returns over a grid of inputs.
    #[test]
    fn lua_twin_matches_rust() {
        let lua = ["/usr/bin/luajit", "/usr/bin/lua", "/usr/sbin/lua"]
            .into_iter()
            .find(|p| Path::new(p).is_file())
            .expect("luajit or lua required");
        let p = policy();
        let mut q = p.clone();
        q.portrait_is_mobile = true;
        q.apply_to = vec!["local".into(), "remote".into()];
        let mut cases = Vec::new();
        for (w, h) in [
            (1100, 2080), (1920, 1080), (3840, 2160), (1080, 1920), (2560, 1440), (2880, 1800),
            (1280, 720), (2400, 1080), (572, 1081), (800, 1280), (5120, 2880),
        ] {
            for phys in [None, Some(mm_diag(6.1)), Some(mm_diag(13.3)), Some(mm_diag(24.0)), Some(mm_diag(32.0)), Some((16.0, 9.0))] {
                for dpi in [None, Some(96.0), Some(150.0), Some(568.0)] {
                    for transform in [0, 1] {
                        for remote in [false, true] {
                            for explicit in [None, Some(1.25), Some(2.4)] {
                                cases.push(Input { width: w, height: h, phys_mm: phys, dpi, transform, remote, explicit_scale: explicit });
                            }
                        }
                    }
                }
            }
        }
        let mut script = format!("{LUA}\nlocal P = {{{}, {}}}\nlocal C = {{\n", p.lua_table(), q.lua_table());
        for c in &cases {
            let (pw, ph) = c.phys_mm.unwrap_or((0.0, 0.0));
            script += &format!(
                "{{width = {}, height = {}, pw = {:?}, ph = {:?}, dpi = {}, transform = {}, remote = {}, explicit = {}}},\n",
                c.width,
                c.height,
                pw,
                ph,
                c.dpi.map(|d| format!("{d:?}")).unwrap_or("nil".into()),
                c.transform,
                c.remote,
                c.explicit_scale.map(|d| format!("{d:?}")).unwrap_or("nil".into())
            );
        }
        script += "}\nfor pi = 1, 2 do for _, c in ipairs(C) do local r = mios_scale_policy(P[pi], c)\n print(string.format('%d %d %d %s %s %s %s %.9g %.9g', r.scale_120, r.logical_width, r.logical_height, tostring(r.mobile), tostring(r.explicit), r.source, tostring(r.auto_apply), r.dpi, r.target_dpi)) end end\n";
        let dir = tempfile::tempdir().unwrap();
        let path = dir.path().join("parity.lua");
        std::fs::write(&path, script).unwrap();
        let out = std::process::Command::new(lua).arg(&path).output().unwrap();
        assert!(out.status.success(), "{}", String::from_utf8_lossy(&out.stderr));
        let got: Vec<String> = String::from_utf8_lossy(&out.stdout).lines().map(str::to_owned).collect();
        let mut want = Vec::new();
        for pol in [&p, &q] {
            for c in &cases {
                let r = compute(pol, c);
                want.push(format!(
                    "{} {} {} {} {} {} {} {} {}",
                    r.scale_120,
                    r.logical_width,
                    r.logical_height,
                    r.mobile,
                    r.explicit,
                    r.source,
                    r.auto_apply,
                    fmt_g(r.dpi),
                    fmt_g(r.target_dpi)
                ));
            }
        }
        assert_eq!(got.len(), want.len());
        for (n, (g, w)) in got.iter().zip(&want).enumerate() {
            assert_eq!(g, w, "case {n}: {:?}", cases[n % cases.len()]);
        }
        // Negative control: a perturbed policy disagrees somewhere, so the comparison has teeth.
        let mut r = p.clone();
        r.target_logical_dpi = 120.0;
        assert!(cases.iter().any(|c| compute(&r, c) != compute(&p, c)));
    }
}
