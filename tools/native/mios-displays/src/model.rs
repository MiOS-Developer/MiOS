// AI-hint: Pure model for mios-displays -- parses `hyprctl -j monitors all`, validates a requested monitor change against the live output (mode list, logical-size scale rule, transform, position, last-enabled-output guard), and renders the Hyprland Lua rule and the SSOT drop-in TOML.
// AI-related: tools/native/mios-displays/src/main.rs, tools/native/mios-gen/src/render.rs (render_hyprland::monitor_rules), usr/share/mios/mios.toml [desktop.hyprland.monitors]
// AI-functions: Monitor::from_json, parse_mode, Monitor::modes, Monitor::current_mode, Change::validate, lua_rule, fragment_toml

use serde_json::Value;

/// One output as Hyprland reports it.
#[derive(Debug, Clone, PartialEq)]
pub struct Monitor {
    pub name: String,
    pub description: String,
    pub width: u32,
    pub height: u32,
    pub refresh: f64,
    pub x: i64,
    pub y: i64,
    pub scale: f64,
    pub transform: i64,
    pub disabled: bool,
    pub focused: bool,
    pub available: Vec<String>,
    /// EDID physical size; 0 for virtual/headless outputs and projectors without one.
    pub physical_w_mm: u32,
    pub physical_h_mm: u32,
}

/// A parsed `WxH@Hz` mode.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct Mode {
    pub width: u32,
    pub height: u32,
    pub refresh: f64,
}

impl Mode {
    pub fn render(&self) -> String {
        format!("{}x{}@{:.2}", self.width, self.height, self.refresh)
    }
}

/// Mode names Hyprland resolves itself.
pub const NAMED_MODES: [&str; 4] = ["preferred", "highres", "highrr", "maxwidth"];

/// Parse `1920x1080@144`, `1920x1080@144.00Hz` (hyprctl form) or `1920x1080`.
pub fn parse_mode(s: &str) -> Option<(u32, u32, Option<f64>)> {
    let s = s.trim().trim_end_matches("Hz");
    let (size, hz) = match s.split_once('@') {
        Some((size, hz)) => (size, Some(hz.parse::<f64>().ok().filter(|r| r.is_finite() && *r > 0.0)?)),
        None => (s, None),
    };
    let (w, h) = size.split_once('x')?;
    let (w, h) = (w.parse::<u32>().ok()?, h.parse::<u32>().ok()?);
    (w > 0 && h > 0).then_some((w, h, hz))
}

impl Monitor {
    pub fn from_json(v: &Value) -> Result<Monitor, String> {
        let s = |k: &str| v.get(k).and_then(Value::as_str).unwrap_or_default().to_string();
        let n = |k: &str| v.get(k).and_then(Value::as_f64);
        let name = s("name");
        if name.is_empty() {
            return Err("hyprctl monitor without a name".into());
        }
        Ok(Monitor {
            description: s("description"),
            width: n("width").unwrap_or(0.0) as u32,
            height: n("height").unwrap_or(0.0) as u32,
            refresh: n("refreshRate").unwrap_or(0.0),
            x: n("x").unwrap_or(0.0) as i64,
            y: n("y").unwrap_or(0.0) as i64,
            scale: n("scale").unwrap_or(1.0),
            transform: n("transform").unwrap_or(0.0) as i64,
            disabled: v.get("disabled").and_then(Value::as_bool).unwrap_or(false),
            focused: v.get("focused").and_then(Value::as_bool).unwrap_or(false),
            physical_w_mm: n("physicalWidth").unwrap_or(0.0).max(0.0) as u32,
            physical_h_mm: n("physicalHeight").unwrap_or(0.0).max(0.0) as u32,
            available: v
                .get("availableModes")
                .and_then(Value::as_array)
                .map(|a| a.iter().filter_map(Value::as_str).map(str::to_string).collect())
                .unwrap_or_default(),
            name,
        })
    }

    /// Distinct modes, in Hyprland's order (preferred first).
    pub fn modes(&self) -> Vec<Mode> {
        let mut out: Vec<Mode> = Vec::new();
        for m in &self.available {
            if let Some((width, height, Some(refresh))) = parse_mode(m) {
                let mode = Mode { width, height, refresh };
                if !out.iter().any(|o| o.render() == mode.render()) {
                    out.push(mode);
                }
            }
        }
        out
    }

    pub fn current_mode(&self) -> Mode {
        Mode { width: self.width, height: self.height, refresh: self.refresh }
    }

    pub fn position(&self) -> String {
        format!("{}x{}", self.x, self.y)
    }

    /// The JSON the UI reads for this output.
    pub fn to_json(&self) -> Value {
        serde_json::json!({
            "name": self.name,
            "description": self.description,
            "width": self.width,
            "height": self.height,
            "refresh": self.refresh,
            "mode": self.current_mode().render(),
            "x": self.x,
            "y": self.y,
            "position": self.position(),
            "scale": self.scale,
            "transform": self.transform,
            "disabled": self.disabled,
            "focused": self.focused,
            "modes": self.modes().iter().map(Mode::render).collect::<Vec<_>>(),
            "physical_mm": [self.physical_w_mm, self.physical_h_mm],
        })
    }
}

/// The first-logon recommendation for one output: its native (largest) resolution at the
/// highest refresh the output advertises at that size, and a scale derived from the EDID
/// physical size. Nothing here is hardware-specific: it is computed from what the output
/// reports, so it is the same on Intel, AMD, NVIDIA and virtual GPUs.
#[derive(Debug, Clone, PartialEq)]
pub struct Recommendation {
    pub output: String,
    pub mode: Mode,
    pub scale: f64,
    pub dpi: Option<f64>,
}

/// `scales` are the offered presets ([desktop.displays].scales); `reference_dpi` is the
/// pixel density that maps to scale 1 ([desktop.displays.setup].reference_dpi).
pub fn recommend(m: &Monitor, scales: &[f64], reference_dpi: f64) -> Option<Recommendation> {
    let modes = m.modes();
    let native = modes.iter().max_by_key(|a| (a.width as u64 * a.height as u64, a.width))?;
    let mode = modes
        .iter()
        .filter(|a| a.width == native.width && a.height == native.height)
        .max_by(|a, b| a.refresh.total_cmp(&b.refresh))
        .copied()?;
    // EDID sizes of 0 (virtual GPUs, headless) or nonsense (projectors report e.g. 160x90 cm
    // or 1x1) give no usable density: stay at scale 1 there.
    let dpi = (m.physical_w_mm >= 100 && m.physical_w_mm <= 2000)
        .then(|| mode.width as f64 / (m.physical_w_mm as f64 / 25.4));
    let wanted = dpi.map(|d| d / reference_dpi).unwrap_or(1.0).max(1.0);
    let scale = scales
        .iter()
        .copied()
        .filter(|s| *s >= 1.0 && scale_ok(mode.width, mode.height, *s))
        .min_by(|a, b| (a - wanted).abs().total_cmp(&(b - wanted).abs()))
        .unwrap_or(1.0);
    Some(Recommendation { output: m.name.clone(), mode, scale, dpi })
}

/// A requested change to one output; `None` keeps the live value.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct Change {
    pub output: String,
    pub mode: Option<String>,
    pub position: Option<String>,
    pub scale: Option<f64>,
    pub transform: Option<i64>,
    pub disabled: Option<bool>,
}

/// A fully resolved monitor rule, ready to project.
#[derive(Debug, Clone, PartialEq)]
pub struct Rule {
    pub output: String,
    pub mode: String,
    pub position: String,
    pub scale: f64,
    pub transform: i64,
    pub disabled: bool,
}

impl Rule {
    pub fn of(m: &Monitor) -> Rule {
        Rule {
            output: m.name.clone(),
            mode: m.current_mode().render(),
            position: m.position(),
            scale: m.scale,
            transform: m.transform,
            disabled: m.disabled,
        }
    }
}

fn scale_ok(width: u32, height: u32, scale: f64) -> bool {
    // Hyprland needs integer logical pixels; it silently picks another scale otherwise.
    let whole = |px: u32| {
        let l = px as f64 / scale;
        (l - l.round()).abs() < 0.01
    };
    whole(width) && whole(height)
}

fn position_ok(p: &str) -> bool {
    if p == "auto" || p.strip_prefix("auto-").is_some_and(|d| ["left", "right", "up", "down"].contains(&d)) {
        return true;
    }
    p.split_once('x')
        .is_some_and(|(x, y)| x.parse::<i64>().is_ok() && y.parse::<i64>().is_ok())
}

impl Change {
    /// Resolve against the live outputs; every refusal leaves the compositor untouched.
    pub fn validate(&self, monitors: &[Monitor]) -> Result<Rule, String> {
        let m = monitors
            .iter()
            .find(|m| m.name == self.output)
            .ok_or_else(|| format!("unknown output {:?} (known: {})", self.output, names(monitors)))?;
        let mut rule = Rule::of(m);
        // A disabled output reports 0x0; give it its preferred mode when re-enabled.
        if m.disabled || m.width == 0 {
            rule.mode = "preferred".into();
        }
        let mut size = (m.width, m.height);
        if let Some(mode) = &self.mode {
            if NAMED_MODES.contains(&mode.as_str()) {
                rule.mode = mode.clone();
                if let Some(first) = m.modes().first() {
                    size = (first.width, first.height);
                }
            } else {
                let (w, h, hz) =
                    parse_mode(mode).ok_or_else(|| format!("invalid mode {mode:?}: expected WxH@Hz or one of {}", NAMED_MODES.join("|")))?;
                let found = m
                    .modes()
                    .into_iter()
                    .find(|a| a.width == w && a.height == h && hz.is_none_or(|r| (a.refresh - r).abs() < 0.01))
                    .ok_or_else(|| format!("{} does not offer mode {mode:?}", m.name))?;
                rule.mode = found.render();
                size = (w, h);
            }
        }
        if let Some(p) = &self.position {
            if !position_ok(p) {
                return Err(format!("invalid position {p:?}: expected XxY or auto[-left|-right|-up|-down]"));
            }
            rule.position = p.clone();
        }
        if let Some(t) = self.transform {
            if !(0..=7).contains(&t) {
                return Err(format!("invalid transform {t}: expected 0..7"));
            }
            rule.transform = t;
        }
        if let Some(s) = self.scale {
            if !(s.is_finite() && (0.25..=4.0).contains(&s)) {
                return Err(format!("invalid scale {s}: expected 0.25..4"));
            }
            rule.scale = s;
        }
        if size.0 > 0 && !scale_ok(size.0, size.1, rule.scale) {
            return Err(format!(
                "scale {} gives a fractional logical size for {}x{} (Hyprland would pick another scale)",
                rule.scale, size.0, size.1
            ));
        }
        if let Some(d) = self.disabled {
            if d && !monitors.iter().any(|o| o.name != m.name && !o.disabled) {
                return Err(format!("refusing to disable {}: it is the only enabled output", m.name));
            }
            rule.disabled = d;
        }
        Ok(rule)
    }
}

pub fn names(monitors: &[Monitor]) -> String {
    monitors.iter().map(|m| m.name.as_str()).collect::<Vec<_>>().join(", ")
}

fn lua_str(s: &str) -> String {
    serde_json::to_string(s).unwrap_or_else(|_| "\"\"".into())
}

fn num(f: f64) -> String {
    let s = format!("{f:.6}");
    s.trim_end_matches('0').trim_end_matches('.').to_string()
}

/// `hl.monitor({...})` for `hyprctl eval`, same field names as the mios-gen projection.
pub fn lua_rule(r: &Rule) -> String {
    if r.disabled {
        return format!("hl.monitor({{output = {}, disabled = true}})", lua_str(&r.output));
    }
    format!(
        "hl.monitor({{output = {}, mode = {}, position = {}, scale = {}, transform = {}}})",
        lua_str(&r.output),
        lua_str(&r.mode),
        lua_str(&r.position),
        num(r.scale),
        r.transform
    )
}

/// The [desktop.hyprland.monitors.<output>] drop-in mios-displays owns.
pub fn fragment_toml(rules: &[Rule], primary: Option<&str>) -> Result<String, String> {
    let mut monitors = toml::map::Map::new();
    for r in rules {
        let mut t = toml::map::Map::new();
        if r.disabled {
            t.insert("disabled".into(), toml::Value::Boolean(true));
        } else {
            t.insert("mode".into(), toml::Value::String(r.mode.clone()));
            t.insert("position".into(), toml::Value::String(r.position.clone()));
            let scale = num(r.scale);
            t.insert(
                "scale".into(),
                match scale.parse::<i64>() {
                    Ok(i) => toml::Value::Integer(i),
                    Err(_) => toml::Value::Float(r.scale),
                },
            );
            t.insert("transform".into(), toml::Value::Integer(r.transform));
        }
        if primary == Some(r.output.as_str()) {
            t.insert("primary".into(), toml::Value::Boolean(true));
        }
        monitors.insert(r.output.clone(), toml::Value::Table(t));
    }
    let mut hyprland = toml::map::Map::new();
    hyprland.insert("monitors".into(), toml::Value::Table(monitors));
    let mut desktop = toml::map::Map::new();
    desktop.insert("hyprland".into(), toml::Value::Table(hyprland));
    let mut root = toml::map::Map::new();
    root.insert("desktop".into(), toml::Value::Table(desktop));
    let body = toml::to_string(&toml::Value::Table(root)).map_err(|e| e.to_string())?;
    Ok(format!(
        "# AI-hint: Operator monitor layout written by mios-displays save (MiOS Displays app); a mios.d drop-in over [desktop.hyprland.monitors], re-projected by mios-gen render-hyprland.\n# AI-related: /usr/share/mios/mios.toml, /usr/bin/mios-displays, /usr/bin/mios-gen\n\n{body}"
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hdmi() -> Vec<Monitor> {
        let v: Value = serde_json::from_str(
            r#"[{"name":"HDMI-A-1","description":"LG","width":1920,"height":1080,"refreshRate":60.0,"x":0,"y":0,"scale":1,"transform":0,"disabled":false,"focused":true,
                "availableModes":["1920x1080@60.00Hz","1920x1080@144.00Hz","1920x1080@60.00Hz","1280x720@60.00Hz"]}]"#,
        )
        .unwrap();
        v.as_array().unwrap().iter().map(|m| Monitor::from_json(m).unwrap()).collect()
    }

    fn change(f: impl FnOnce(&mut Change)) -> Change {
        let mut c = Change { output: "HDMI-A-1".into(), ..Default::default() };
        f(&mut c);
        c
    }

    #[test]
    fn modes_are_parsed_and_deduplicated() {
        let m = &hdmi()[0];
        let modes: Vec<String> = m.modes().iter().map(Mode::render).collect();
        assert_eq!(modes, ["1920x1080@60.00", "1920x1080@144.00", "1280x720@60.00"]);
        assert_eq!(parse_mode("1920x1080@144.00Hz"), Some((1920, 1080, Some(144.0))));
        assert_eq!(parse_mode("1920x1080"), Some((1920, 1080, None)));
        assert_eq!(parse_mode("0x1080@60"), None);
        assert_eq!(parse_mode("huge"), None);
    }

    #[test]
    fn valid_changes_resolve_to_a_full_rule() {
        let r = change(|c| c.scale = Some(1.25)).validate(&hdmi()).unwrap();
        assert_eq!(r.mode, "1920x1080@60.00");
        assert_eq!(r.scale, 1.25);
        assert_eq!(
            lua_rule(&r),
            "hl.monitor({output = \"HDMI-A-1\", mode = \"1920x1080@60.00\", position = \"0x0\", scale = 1.25, transform = 0})"
        );
        let r = change(|c| c.mode = Some("1920x1080@144".into())).validate(&hdmi()).unwrap();
        assert_eq!(r.mode, "1920x1080@144.00");
        let r = change(|c| {
            c.mode = Some("1280x720@60".into());
            c.scale = Some(1.25);
            c.transform = Some(1);
            c.position = Some("auto-right".into());
        })
        .validate(&hdmi())
        .unwrap();
        assert_eq!((r.mode.as_str(), r.transform, r.position.as_str()), ("1280x720@60.00", 1, "auto-right"));
    }

    #[test]
    fn recommendations_follow_the_reported_hardware() {
        let mut m = hdmi()[0].clone();
        // 24" 1080p (530 mm): ~92 dpi -> scale 1, highest refresh at native size.
        m.physical_w_mm = 530;
        let r = recommend(&m, &[1.0, 1.25, 1.5, 1.75, 2.0], 110.0).unwrap();
        assert_eq!((r.mode.render(), r.scale), ("1920x1080@144.00".to_string(), 1.0));
        // 27" 4K (597 mm): ~163 dpi -> 1.5.
        m.available = vec!["3840x2160@60.00Hz".into(), "2560x1440@144.00Hz".into(), "3840x2160@30.00Hz".into()];
        m.physical_w_mm = 597;
        let r = recommend(&m, &[1.0, 1.25, 1.5, 1.75, 2.0], 110.0).unwrap();
        assert_eq!((r.mode.render(), r.scale), ("3840x2160@60.00".to_string(), 1.5));
        // Virtual GPU / headless: no physical size -> scale 1.
        m.physical_w_mm = 0;
        assert_eq!(recommend(&m, &[1.0, 1.5, 2.0], 110.0).unwrap().scale, 1.0);
        // No modes (disconnected) -> nothing to recommend.
        m.available.clear();
        assert!(recommend(&m, &[1.0], 110.0).is_none());
    }

    #[test]
    fn invalid_changes_are_refused() {
        let mons = hdmi();
        for c in [
            change(|c| c.mode = Some("9999x9999@60".into())),
            change(|c| c.mode = Some("1920x1080@75".into())),
            change(|c| c.mode = Some("garbage".into())),
            change(|c| c.scale = Some(1.33)),
            change(|c| c.scale = Some(9.0)),
            change(|c| c.transform = Some(8)),
            change(|c| c.position = Some("left".into())),
            change(|c| c.disabled = Some(true)),
            change(|c| c.output = "DP-9".into()),
        ] {
            assert!(c.validate(&mons).is_err(), "{c:?}");
        }
    }

    #[test]
    fn fragment_round_trips_into_the_ssot_shape() {
        let mut r = change(|c| c.scale = Some(1.25)).validate(&hdmi()).unwrap();
        let text = fragment_toml(&[r.clone()], Some("HDMI-A-1")).unwrap();
        let v: toml::Value = text.parse().unwrap();
        let m = &v["desktop"]["hyprland"]["monitors"]["HDMI-A-1"];
        assert_eq!(m["mode"].as_str(), Some("1920x1080@60.00"));
        assert_eq!(m["scale"].as_float(), Some(1.25));
        assert_eq!(m["primary"].as_bool(), Some(true));
        r.scale = 1.0;
        let v: toml::Value = fragment_toml(&[r], None).unwrap().parse().unwrap();
        assert_eq!(v["desktop"]["hyprland"]["monitors"]["HDMI-A-1"]["scale"].as_integer(), Some(1));
    }
}
