// AI-hint: Crate root for mios-resolver -- the native layered mios.toml resolver that subsumes mios_toml.py / userenv.sh / globals.ps1.
// AI-related: usr/lib/mios/mios_toml.py, usr/lib/mios/userenv.sh, tools/native/mios-ssot-walk, usr/share/mios/mios.toml
pub mod names;
// Public compatibility path; all name definitions live in the same module.
pub use names as aliases;
pub mod db_overlay;
pub mod emit;
pub use emit::{emit_build, emit_install_env, emit_json, emit_ps, emit_repos, emit_shell};
pub mod error {
    use miette::Diagnostic;
    use thiserror::Error;

    #[derive(Error, Debug, Diagnostic)]
    pub enum ResolverError {
        #[error("Failed to parse TOML layer at {path}: {source}")]
        #[diagnostic(
            code(mios_resolver::layer_parse),
            help("Check TOML syntax in layer file")
        )]
        LayerParse {
            path: String,
            #[source]
            source: toml::de::Error,
        },

        #[error("Type shape or schema mismatch: {msg}")]
        #[diagnostic(code(mios_resolver::type_shape))]
        TypeShape { msg: String },

        #[error("Missing expected configuration layer: {path}")]
        #[diagnostic(code(mios_resolver::missing_layer))]
        MissingLayer { path: String },

        #[error("Invalid hex color format: '{value}' under [colors].{key}")]
        #[diagnostic(
            code(mios_resolver::invalid_color_hex),
            help("Colors must be valid 6-digit or 3-digit hex strings (e.g. #FFFFFF or #FFF)")
        )]
        InvalidColorHex { key: String, value: String },

        #[error("Invalid non-integer port specification under [ports].{key}: '{value}'")]
        #[diagnostic(
            code(mios_resolver::invalid_port),
            help("Port values must be valid 16-bit unsigned integers between 1 and 65535")
        )]
        InvalidPortValue { key: String, value: String },
    }
}
pub mod expand;
pub mod layers;
pub mod keybindings;
pub mod merge;
pub mod model {
    use serde::{Deserialize, Serialize};
    use std::collections::HashMap;

    #[derive(Debug, Clone, Serialize, Deserialize, Default)]
    pub struct IdentityConfig {
        pub username: Option<String>,
        pub hostname: Option<String>,
        pub domain: Option<String>,
        pub email: Option<String>,
        pub role: Option<String>,
    }

    #[derive(Debug, Clone, Serialize, Deserialize, Default)]
    pub struct LocaleConfig {
        pub lang: Option<String>,
        pub timezone: Option<String>,
        pub keymap: Option<String>,
    }

    #[derive(Debug, Clone, Serialize, Deserialize, Default)]
    pub struct PortsConfig {
        pub stack_id: Option<i32>,
        pub vllm: Option<u16>,
        pub sglang: Option<u16>,
        pub searxng: Option<u16>,
        pub crawl4ai: Option<u16>,
        pub firecrawl: Option<u16>,
        pub open_webui: Option<u16>,
        pub guacamole_web: Option<u16>,
        pub pgvector: Option<u16>,
        pub agent_pipe: Option<u16>,
    }

    #[derive(Debug, Clone, Serialize, Deserialize, Default)]
    pub struct ImageConfig {
        pub name: Option<String>,
        pub tag: Option<String>,
        pub base: Option<String>,
        pub sidecars: Option<HashMap<String, String>>,
    }

    #[derive(Debug, Clone, Serialize, Deserialize, Default)]
    pub struct AiConfig {
        pub model: Option<String>,
        pub embed_model: Option<String>,
        pub endpoint: Option<String>,
        pub ram_floor_gb: Option<u32>,
        pub dir: Option<String>,
    }

    #[derive(Debug, Clone, Serialize, Deserialize, Default)]
    pub struct NetworkConfig {
        pub listen_host: Option<String>,
        pub loopback_host: Option<String>,
    }

    #[derive(Debug, Clone, Serialize, Deserialize, Default)]
    pub struct MiosModel {
        pub identity: Option<IdentityConfig>,
        pub locale: Option<LocaleConfig>,
        pub ports: Option<PortsConfig>,
        pub colors: Option<HashMap<String, String>>,
        pub image: Option<ImageConfig>,
        pub ai: Option<AiConfig>,
        pub network: Option<NetworkConfig>,
        pub env: Option<HashMap<String, String>>,
    }
}
pub mod edid_timing;
pub mod framerate;
pub mod palette;
pub mod ports;
pub mod sigstore;
pub mod terminal_tuis;
pub mod walk;

use crate::error::ResolverError;
use crate::model::MiosModel;
use std::collections::BTreeMap;
use std::path::Path;
use toml::Value;

pub fn load_model(root_dir: Option<&Path>) -> Result<MiosModel, ResolverError> {
    let fig = layers::create_figment(root_dir);
    fig.extract::<MiosModel>()
        .map_err(|e| ResolverError::TypeShape { msg: e.to_string() })
}

/// stack_offset = [ports].stack_id * 10000 -- the multi-stack port shift the
/// Python resolver applies in process_val(). Absent stack_id means no shift.
pub fn stack_offset_of(merged: &Value) -> i64 {
    merged
        .get("ports")
        .and_then(|p| p.get("stack_id"))
        .and_then(|v| {
            v.as_integer()
                .or_else(|| v.as_str().and_then(|s| s.parse::<i64>().ok()))
        })
        .map(|id| id * 10000)
        .unwrap_or(0)
}

/// The merged SSOT every emitter renders: all six tiers, the DB overlay when
/// active or forced, and [ports] derived from [ports.categories] last so an
/// override in any tier re-derives.
pub fn resolve_merged(root_dir: Option<&Path>, db_overlay: bool) -> Result<Value, ResolverError> {
    let mut merged = layers::merge_layer_files(&layers::resolve_layer_paths(root_dir))?;
    db_overlay::maybe_apply_db_overlay(&mut merged, db_overlay);
    ports::derive_ports(&mut merged);
    Ok(merged)
}

/// The merge a user-tier write sits on: vendor through host.d, with [ports]
/// derived the same way resolve_merged derives them, so a derived value the
/// configurator echoes back is not frozen into the user tier.
pub fn resolve_below_user(root_dir: Option<&Path>) -> Result<Value, ResolverError> {
    let mut merged = layers::merge_layer_files(&layers::resolve_layer_paths_below_user(root_dir))?;
    ports::derive_ports(&mut merged);
    Ok(merged)
}

/// Deterministic source projections use only the selected root's vendor and
/// host layers. Process loader pointers and the developer's home cannot alter them.
/// (Policy readers such as mios-gate host_parity rely on the host layer being included;
/// generators that write tracked files use [`resolve_vendor_projection`].)
pub fn resolve_projection(root: &Path) -> Result<Value, ResolverError> {
    projection_of(root, &[("usr/share/mios/mios.toml", "usr/lib/mios/mios.d"), ("etc/mios/mios.toml", "etc/mios/mios.d")])
}

/// The VENDOR tier only (usr/share/mios/mios.toml + usr/lib/mios/mios.d), for generators
/// that write tracked/shipped files (render-globals, man pages, policy.json). On a deployed
/// host the repo root is `/`, so root/etc/mios is the live admin tier -- e.g. the per-output
/// monitor layout mios-displays saves -- and must never be baked into a shipped projection.
pub fn resolve_vendor_projection(root: &Path) -> Result<Value, ResolverError> {
    projection_of(root, &[("usr/share/mios/mios.toml", "usr/lib/mios/mios.d")])
}

fn projection_of(root: &Path, tiers: &[(&str, &str)]) -> Result<Value, ResolverError> {
    let mut layer_paths = Vec::new();
    for (file, directory) in tiers {
        let path = root.join(file);
        if path.is_file() {
            layer_paths.push(path);
        }
        let directory = root.join(directory);
        if directory.exists() {
            let mut paths = std::fs::read_dir(&directory)
                .map_err(|e| ResolverError::TypeShape {
                    msg: format!("{}: {e}", directory.display()),
                })?
                .map(|entry| entry.map(|entry| entry.path()))
                .collect::<Result<Vec<_>, _>>()
                .map_err(|e| ResolverError::TypeShape { msg: e.to_string() })?;
            paths.retain(|path| path.is_file() && path.extension().is_some_and(|e| e == "toml"));
            paths.sort();
            layer_paths.extend(paths);
        }
    }
    let mut merged = layers::merge_layer_files(&layer_paths)?;
    ports::derive_ports(&mut merged);
    Ok(merged)
}

/// The user tier file (MIOS_USER_TOML, else $XDG_CONFIG_HOME or
/// $HOME/.config + /mios/mios.toml) -- where operator saves land.
pub fn user_toml_path(root_dir: Option<&Path>) -> std::path::PathBuf {
    layers::resolve_tier_dirs(root_dir).4
}

/// The resolved MIOS_* environment, ${MIOS_*} references expanded -- the same
/// values `mios-resolver --emit=json` prints. Native programs read the SSOT
/// through this at run time instead of carrying their own copies of it.
pub fn resolve_env(root_dir: Option<&Path>) -> Result<BTreeMap<String, String>, ResolverError> {
    let merged = resolve_merged(root_dir, false)?;
    let mut exports = emit::build_exports_map(&merged, stack_offset_of(&merged));
    emit::resolve_cross_references(&mut exports);
    Ok(exports)
}

/// Run-time SSOT lookup for native programs: the process environment (a
/// unit's Environment=, the operator) wins, then the resolved six-tier
/// mios.toml. A name neither provides is an error that names it -- callers
/// never substitute a compiled-in value.
pub mod runtime {
    use crate::error::ResolverError;
    use std::collections::BTreeMap;
    use std::sync::OnceLock;

    static RESOLVED: OnceLock<Result<BTreeMap<String, String>, String>> = OnceLock::new();

    /// `name` from `env`, else from `resolved`; empty strings count as unset.
    pub fn lookup_in(
        name: &str,
        env: impl Fn(&str) -> Option<String>,
        resolved: &BTreeMap<String, String>,
    ) -> Option<String> {
        env(name)
            .filter(|v| !v.is_empty())
            .or_else(|| resolved.get(name).filter(|v| !v.is_empty()).cloned())
    }

    fn resolved() -> &'static Result<BTreeMap<String, String>, String> {
        RESOLVED.get_or_init(|| {
            let result = (|| {
                let mut merged = crate::resolve_merged(None, false)?;
                crate::names::overlay_inputs(&mut merged, |key| std::env::var(key).ok())
                    .map_err(|msg| ResolverError::TypeShape { msg })?;
                let mut exports =
                    crate::emit::build_exports_map(&merged, crate::stack_offset_of(&merged));
                crate::emit::resolve_cross_references(&mut exports);
                Ok::<_, ResolverError>(exports)
            })();
            result.map_err(|error| error.to_string())
        })
    }

    pub fn get(name: &str) -> Option<String> {
        if let Ok(value) = std::env::var(name) {
            if !value.is_empty() {
                return Some(value);
            }
        }
        let resolved = resolved().as_ref().ok()?;
        lookup_in(name, |n| std::env::var(n).ok(), resolved)
    }

    fn missing(name: &str) -> ResolverError {
        ResolverError::TypeShape {
            msg: format!("{name} is unset and the layered mios.toml does not resolve it"),
        }
    }

    pub fn require(name: &str) -> Result<String, ResolverError> {
        if let Ok(value) = std::env::var(name) {
            if !value.is_empty() {
                return Ok(value);
            }
        }
        if let Err(msg) = resolved() {
            return Err(ResolverError::TypeShape { msg: msg.clone() });
        }
        get(name).ok_or_else(|| missing(name))
    }

    pub fn require_port(name: &str) -> Result<u16, ResolverError> {
        let raw = require(name)?;
        raw.parse::<u16>()
            .map_err(|_| ResolverError::InvalidPortValue {
                key: name.to_string(),
                value: raw,
            })
    }

    /// Replace every `${MIOS_*}` in `text` through `lookup`; an unresolved
    /// reference is an error rather than an empty string.
    pub fn expand_refs_with(
        text: &str,
        lookup: impl Fn(&str) -> Option<String>,
    ) -> Result<String, ResolverError> {
        let mut out = String::with_capacity(text.len());
        let mut rest = text;
        while let Some(start) = rest.find("${") {
            out.push_str(&rest[..start]);
            let after = &rest[start + 2..];
            let Some(end) = after.find('}') else {
                out.push_str(&rest[start..]);
                return Ok(out);
            };
            let name = &after[..end];
            out.push_str(&lookup(name).ok_or_else(|| missing(name))?);
            rest = &after[end + 1..];
        }
        out.push_str(rest);
        Ok(out)
    }

    pub fn expand_refs(text: &str) -> Result<String, ResolverError> {
        expand_refs_with(text, get)
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        fn ssot() -> BTreeMap<String, String> {
            BTreeMap::from([("MIOS_PORTS_NODE".to_string(), "8650".to_string())])
        }

        #[test]
        fn environment_wins_over_the_resolved_ssot() {
            let env = |_: &str| Some("9100".to_string());
            assert_eq!(
                lookup_in("MIOS_PORTS_NODE", env, &ssot()).as_deref(),
                Some("9100")
            );
        }

        #[test]
        fn empty_environment_falls_back_to_the_ssot() {
            let env = |_: &str| Some(String::new());
            assert_eq!(
                lookup_in("MIOS_PORTS_NODE", env, &ssot()).as_deref(),
                Some("8650")
            );
        }

        #[test]
        fn a_name_nothing_provides_is_none() {
            assert_eq!(lookup_in("MIOS_PORT_MISSING", |_| None, &ssot()), None);
        }

        #[test]
        fn references_expand_and_unresolved_ones_fail() {
            let look = |n: &str| (n == "MIOS_PORT_A").then(|| "8900".to_string());
            assert_eq!(
                expand_refs_with("http://localhost:${MIOS_PORT_A}/", look).unwrap(),
                "http://localhost:8900/"
            );
            assert!(expand_refs_with("http://localhost:${MIOS_PORT_B}/", look).is_err());
            assert_eq!(expand_refs_with("no refs", look).unwrap(), "no refs");
        }
    }
}

#[cfg(test)]
mod projection_tests {
    use super::{resolve_projection, resolve_vendor_projection};
    use std::fs;

    #[test]
    fn vendor_projection_reads_only_the_vendor_tier() {
        let root = tempfile::tempdir().unwrap();
        let p = |rel: &str, body: &str| {
            let path = root.path().join(rel);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(path, body).unwrap();
        };
        p("usr/share/mios/mios.toml", "[a]\nv = 1\n");
        p("usr/lib/mios/mios.d/10-x.toml", "[a]\nfrag = true\n");
        // A deployed host's admin tier (e.g. a saved monitor layout) must not leak in.
        p("etc/mios/mios.toml", "[a]\nv = 2\n");
        p("etc/mios/mios.d/50-displays.toml", "[desktop.hyprland.monitors.HDMI-A-1]\nmode = \"1920x1080@144\"\n");
        let merged = resolve_vendor_projection(root.path()).unwrap();
        assert_eq!(merged["a"]["v"].as_integer(), Some(1));
        assert_eq!(merged["a"]["frag"].as_bool(), Some(true));
        assert!(merged.get("desktop").is_none(), "{merged:?}");
        // The policy projection keeps the host layer.
        let layered = resolve_projection(root.path()).unwrap();
        assert_eq!(layered["a"]["v"].as_integer(), Some(2));
        assert!(layered.get("desktop").is_some());
    }
}

// AI-hint: Shared output-aware opening geometry for native placement and the room.
// AI-related: mios-gen/src/hypr3d_spawn.rs, miosd/src/placement.rs
pub mod window_opening {
    use toml::Value;

    #[derive(Clone, Debug, PartialEq)]
    pub struct Policy {
        pub landscape: (f64, f64),
        pub portrait: (f64, f64),
        pub font_scale: f64,
        pub safe_insets: [f64; 4],
        pub margin: f64,
        pub min_width: f64,
        pub min_height: f64,
        pub max_width: f64,
        pub max_width_ratio: f64,
        pub max_height_ratio: f64,
        pub max_width_mm: f64,
        pub paper_long_edge_mm: f64,
    }

    fn at<'a>(doc: &'a Value, key: &str) -> Option<&'a Value> {
        key.split('.').try_fold(doc, |v, k| v.get(k))
    }
    fn numeric(v: &Value) -> Option<f64> {
        v.as_float().or_else(|| v.as_integer().map(|n| n as f64)).filter(|n| n.is_finite())
    }
    fn number(table: &Value, key: &str, low: f64, high: f64) -> Result<f64, String> {
        table.get(key).and_then(numeric).filter(|v| *v >= low && *v <= high)
            .ok_or_else(|| format!("window opening {key} must be finite in {low}..={high}"))
    }

    impl Policy {
        /// Retina's text-cell policy is shared when float-first placement is enabled.
        /// Legacy/fixed policies keep their existing independent sizing behavior.
        pub fn from_ssot(doc: &Value) -> Result<Option<Self>, String> {
            let Some(spawn) = at(doc, "desktop.hyprland.hypr3d.spawn") else { return Ok(None) };
            if spawn.get("enabled").and_then(Value::as_bool) != Some(true)
                || spawn.get("criterion").and_then(Value::as_str) != Some("retina") { return Ok(None); }
            let Some(placement) = at(doc, "desktop.hyprland.placement") else { return Ok(None) };
            if placement.get("enabled").and_then(Value::as_bool) != Some(true) { return Ok(None); }
            let source = spawn.get("text_size_source").and_then(Value::as_str).ok_or("window opening text_size_source missing")?;
            let font = at(doc, source).ok_or("window opening font source missing")?;
            let font_size = number(font, "size", 4.0, 200.0)?;
            let reference = match spawn.get("text_reference_px") {
                Some(v) => numeric(v).filter(|v| *v >= 4.0 && *v <= 200.0).ok_or("window opening text_reference_px must be 4..=200")?,
                None => font_size,
            };
            let font_scale = font_size / reference;
            let cell_w = number(font, "cell_w_px", 1.0, 200.0)? * font_scale;
            let cell_h = number(font, "cell_h_px", 1.0, 400.0)? * font_scale;
            let chrome_w = number(font, "chrome_w_px", 0.0, 2000.0)?;
            let chrome_h = number(font, "chrome_h_px", 0.0, 2000.0)?;
            let cells = |key: &str| -> Result<(f64, f64), String> {
                let pair = spawn.get(key).and_then(Value::as_array).filter(|v| v.len() == 2).ok_or_else(|| format!("window opening {key} must contain two cell counts"))?;
                let c = numeric(&pair[0]).filter(|n| (1.0..=1000.0).contains(n)).ok_or("window opening invalid columns")?;
                let r = numeric(&pair[1]).filter(|n| (1.0..=1000.0).contains(n)).ok_or("window opening invalid rows")?;
                Ok((c * cell_w + chrome_w, r * cell_h + chrome_h))
            };
            let shell = at(doc, "theme.shell").ok_or("window opening shell metrics missing")?;
            let edge = number(shell, "edge_margin_px", 0.0, 512.0)?;
            let top = number(shell, "top_height_px", 0.0, 512.0)? + 2.0 * edge;
            let bottom = number(shell, "dock_height_px", 0.0, 512.0)? + 2.0 * edge;
            let margin = match spawn.get("fit_margin_px") {
                Some(Value::String(path)) => at(doc, path).and_then(numeric),
                Some(v) => numeric(v),
                None => None,
            }.filter(|v| *v >= 0.0 && *v <= 512.0).ok_or("window opening fit_margin_px is invalid")?;
            let paper_long_edge_mm = match spawn.get("paper_long_edge_mm") {
                Some(v) => numeric(v).filter(|v| *v >= 0.0 && *v <= 2000.0).ok_or("window opening paper_long_edge_mm must be 0..=2000")?,
                None => 0.0,
            };
            Ok(Some(Self {
                landscape: cells("landscape_cells")?, portrait: cells("portrait_cells")?, font_scale,
                safe_insets: [0.0, top, 0.0, bottom], margin,
                min_width: number(placement, "min_width_px", 1.0, 10000.0)?,
                min_height: number(placement, "min_height_px", 1.0, 10000.0)?,
                max_width: number(placement, "max_width_px", 1.0, 10000.0)?,
                max_width_ratio: number(placement, "max_width_ratio", 0.01, 1.0)?,
                max_height_ratio: number(placement, "max_height_ratio", 0.01, 1.0)?,
                max_width_mm: number(placement, "max_width_mm", 0.0, 2000.0)?,
                paper_long_edge_mm,
            }))
        }

        pub fn insets(&self, reserved: [f64; 4]) -> [f64; 4] {
            std::array::from_fn(|i| reserved[i].max(self.safe_insets[i]))
        }

        /// Uniform fit preserves the chosen paper/text-grid aspect. Workarea
        /// and physical caps take precedence over minimum client dimensions.
        pub fn size(&self, usable_w: f64, usable_h: f64, logical_px_per_mm: Option<f64>) -> (f64, f64) {
            let portrait = usable_h > usable_w;
            let (w, h) = if portrait { self.portrait } else { self.landscape };
            let mut cap_w = (usable_w * self.max_width_ratio).min(self.max_width).min((usable_w - 2.0 * self.margin).max(1.0));
            let mut cap_h = (usable_h * self.max_height_ratio).min((usable_h - 2.0 * self.margin).max(1.0));
            if let Some(ppm) = logical_px_per_mm.filter(|v| v.is_finite() && *v > 0.0) {
                if self.max_width_mm > 0.0 { cap_w = cap_w.min(self.max_width_mm * ppm); }
                if self.paper_long_edge_mm > 0.0 {
                    if portrait { cap_h = cap_h.min(self.paper_long_edge_mm * ppm); }
                    else { cap_w = cap_w.min(self.paper_long_edge_mm * ppm); }
                }
            }
            let scale = (self.min_width / w).max(self.min_height / h).max(1.0)
                .min(cap_w / w).min(cap_h / h);
            ((w * scale).floor().max(1.0), (h * scale).floor().max(1.0))
        }

        /// The first configure cannot query EDID, but uses identical text,
        /// orientation and HUD bounds. The native mapper applies physical caps.
        pub fn rule_expression(&self) -> String {
            let n = |v: f64| format!("{v:.6}").trim_end_matches('0').trim_end_matches('.').to_owned();
            let uw = format!("max(monitor_w-{},1)", n(self.safe_insets[0] + self.safe_insets[2]));
            let uh = format!("max(monitor_h-{},1)", n(self.safe_insets[1] + self.safe_insets[3]));
            let cap_w = format!("min(min(({uw})*{},{}),max(({uw})-{},1))",n(self.max_width_ratio),n(self.max_width),n(2.0*self.margin));
            let cap_h = format!("min(({uh})*{},max(({uh})-{},1))",n(self.max_height_ratio),n(2.0*self.margin));
            let expr = |(w,h): (f64,f64)| {
                let scale = format!("min(min({},({cap_w})/{}),({cap_h})/{})",n((self.min_width/w).max(self.min_height/h).max(1.0)),n(w),n(h));
                (format!("{}*({scale})",n(w)),format!("{}*({scale})",n(h)))
            };
            let (lw,lh)=expr(self.landscape);let (pw,ph)=expr(self.portrait);
            format!("(({uw})>=({uh})?{lw}:{pw}) (({uw})>=({uh})?{lh}:{ph})")
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        fn doc() -> Value {
            toml::from_str(r#"
[theme.font]
size=12
cell_w_px=10
cell_h_px=20
chrome_w_px=20
chrome_h_px=12
[theme.shell]
edge_margin_px=8
top_height_px=28
dock_height_px=56
[desktop.hyprland.placement]
enabled=true
min_width_px=640
min_height_px=400
max_width_px=1800
max_width_ratio=0.9
max_height_ratio=0.9
max_width_mm=420
[desktop.hyprland.hypr3d.spawn]
enabled=true
criterion="retina"
text_size_source="theme.font"
text_reference_px=12
paper_long_edge_mm=297
landscape_cells=[94,34]
portrait_cells=[66,46]
fit_margin_px="theme.shell.edge_margin_px"
"#).unwrap()
        }
        #[test]
        fn opening_is_one_text_grid_across_output_resolutions() {
            let p=Policy::from_ssot(&doc()).unwrap().unwrap();
            assert_eq!(p.size(1920.0,964.0,Some(1920.0/530.0)),(960.0,692.0));
            assert_eq!(p.size(2560.0,1324.0,Some(2560.0/697.0)),(960.0,692.0));
            assert_eq!(p.size(1080.0,1804.0,Some(1080.0/299.0)),(680.0,932.0));
        }
        #[test]
        fn output_workarea_and_mobile_bounds_win_over_client_minimum() {
            let p=Policy::from_ssot(&doc()).unwrap().unwrap();
            let (w,h)=p.size(390.0,728.0,None);
            assert!(w<=351.0 && h<=655.0,"{w}x{h}");
            assert!((w/h-680.0/932.0).abs()<0.003);
            assert_eq!(p.insets([0.0;4]),[0.0,44.0,0.0,72.0]);
            assert_eq!(p.insets([20.0,60.0,30.0,90.0]),[20.0,60.0,30.0,90.0]);
        }
        #[test]
        fn font_size_changes_grow_the_opening_cells_and_physical_cap_is_uniform() {
            let mut data=doc();data["theme"]["font"]["size"]=Value::Integer(18);
            let p=Policy::from_ssot(&data).unwrap().unwrap();
            assert_eq!(p.font_scale,1.5);
            assert_eq!(p.landscape,(1430.0,1032.0));
            let (w,h)=p.size(2560.0,1324.0,Some(2560.0/697.0));
            assert!(w<=297.0*2560.0/697.0 && w>1080.0);
            assert!((w/h-1430.0/1032.0).abs()<0.003);
        }
        #[test]
        fn invalid_text_reference_is_rejected_and_fixed_mode_keeps_legacy_sizing() {
            let mut data=doc();data["desktop"]["hyprland"]["hypr3d"]["spawn"]["text_reference_px"]=Value::Integer(0);
            assert!(Policy::from_ssot(&data).is_err());
            data["desktop"]["hyprland"]["hypr3d"]["spawn"]["criterion"]=Value::String("fixed".into());
            assert!(Policy::from_ssot(&data).unwrap().is_none());
        }
    }

}
