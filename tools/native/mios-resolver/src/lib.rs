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
pub mod palette;
pub mod ports;
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
pub fn resolve_projection(root: &Path) -> Result<Value, ResolverError> {
    let mut layer_paths = Vec::new();
    for (file, directory) in [
        ("usr/share/mios/mios.toml", "usr/lib/mios/mios.d"),
        ("etc/mios/mios.toml", "etc/mios/mios.d"),
    ] {
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
