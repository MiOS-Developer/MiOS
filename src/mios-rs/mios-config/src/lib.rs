// AI-hint: Typed config resolver deserializing mios.toml via Figment into typed models + generic TOML table.
// AI-related: usr/share/mios/mios.toml, /etc/mios/mios.toml, tools/lib/userenv.sh, tools/drift-checks.py

use figment::{
    providers::{Env, Format, Toml},
    Figment,
};
use miette::Diagnostic;
use serde::{Deserialize, Serialize};
use std::path::Path;
use thiserror::Error;

pub mod validator {
    use serde::{Deserialize, Serialize};
    use std::collections::HashMap;
    use std::path::Path;
    use std::time::Instant;

    #[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
    #[serde(tag = "type", content = "details")]
    pub enum ValidationError {
        Syntax(String),
        MissingSection(String),
        MissingField(String),
        InvalidType {
            field: String,
            expected: String,
            got: String,
        },
        PortOutOfRange {
            key: String,
            port: i64,
        },
        PortCollision {
            key1: String,
            key2: String,
            port: u16,
        },
        Law7EmptyString {
            field: String,
        },
        RatchetViolation {
            name: String,
            current: usize,
            bound: usize,
            message: String,
        },
    }

    impl std::fmt::Display for ValidationError {
        fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
            match self {
                ValidationError::Syntax(msg) => write!(f, "Syntax Error: {}", msg),
                ValidationError::MissingSection(sec) => {
                    write!(f, "Missing required section: [{}]", sec)
                }
                ValidationError::MissingField(field) => {
                    write!(f, "Missing required field: {}", field)
                }
                ValidationError::InvalidType {
                    field,
                    expected,
                    got,
                } => {
                    write!(
                        f,
                        "Type Mismatch on '{}': expected {}, got {}",
                        field, expected, got
                    )
                }
                ValidationError::PortOutOfRange { key, port } => {
                    write!(
                        f,
                        "Port Out of Range on '{}': {} (must be 1..65535)",
                        key, port
                    )
                }
                ValidationError::PortCollision { key1, key2, port } => {
                    write!(
                        f,
                        "Port Collision: port {} is assigned to both '{}' and '{}'",
                        port, key1, key2
                    )
                }
                ValidationError::Law7EmptyString { field } => {
                    write!(
                    f,
                    "Law 7 Violation (Empty String Literal): '{}' must not be empty or whitespace",
                    field
                )
                }
                ValidationError::RatchetViolation {
                    name,
                    current,
                    bound,
                    message,
                } => {
                    write!(
                        f,
                        "Ratchet Violation on '{}': current={} bound={} ({})",
                        name, current, bound, message
                    )
                }
            }
        }
    }

    #[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
    pub struct ValidationWarning {
        pub code: String,
        pub message: String,
    }

    #[derive(Debug, Clone, Serialize, Deserialize)]
    pub struct ValidationReport {
        pub is_valid: bool,
        pub errors: Vec<ValidationError>,
        pub warnings: Vec<ValidationWarning>,
        pub duration_ms: f64,
        pub checked_sections: Vec<String>,
    }

    impl ValidationReport {
        pub fn success(checked_sections: Vec<String>, duration_ms: f64) -> Self {
            Self {
                is_valid: true,
                errors: Vec::new(),
                warnings: Vec::new(),
                duration_ms,
                checked_sections,
            }
        }

        pub fn failure(
            errors: Vec<ValidationError>,
            warnings: Vec<ValidationWarning>,
            checked_sections: Vec<String>,
            duration_ms: f64,
        ) -> Self {
            Self {
                is_valid: errors.is_empty(),
                errors,
                warnings,
                duration_ms,
                checked_sections,
            }
        }

        pub fn to_json(&self) -> String {
            serde_json::to_string_pretty(self).unwrap_or_else(|_| "{}".to_string())
        }

        pub fn format_human(&self) -> String {
            let mut out = String::new();
            if self.is_valid {
                out.push_str(&format!(
                    "✓ SSOT Validation PASSED in {:.2}ms ({} sections verified)\n",
                    self.duration_ms,
                    self.checked_sections.len()
                ));
            } else {
                out.push_str(&format!(
                    "✗ SSOT Validation FAILED in {:.2}ms ({} errors, {} warnings)\n",
                    self.duration_ms,
                    self.errors.len(),
                    self.warnings.len()
                ));
                for (idx, err) in self.errors.iter().enumerate() {
                    out.push_str(&format!("  [{}] {}\n", idx + 1, err));
                }
            }
            for (idx, warn) in self.warnings.iter().enumerate() {
                out.push_str(&format!(
                    "  [WARN {}] {}: {}\n",
                    idx + 1,
                    warn.code,
                    warn.message
                ));
            }
            out
        }
    }

    pub struct MiosValidator;

    impl MiosValidator {
        pub fn validate_file<P: AsRef<Path>>(path: P) -> ValidationReport {
            let start = Instant::now();
            let content = match std::fs::read_to_string(path.as_ref()) {
                Ok(c) => c,
                Err(e) => {
                    let dur = start.elapsed().as_secs_f64() * 1000.0;
                    return ValidationReport::failure(
                        vec![ValidationError::Syntax(format!(
                            "Could not read file {}: {}",
                            path.as_ref().display(),
                            e
                        ))],
                        Vec::new(),
                        Vec::new(),
                        dur,
                    );
                }
            };
            Self::validate_str(&content)
        }

        pub fn validate_str(content: &str) -> ValidationReport {
            let start = Instant::now();
            let mut errors = Vec::new();
            let mut warnings = Vec::new();
            let mut checked_sections = Vec::new();

            let parsed: toml::Value = match content.parse::<toml::Value>() {
                Ok(v) => v,
                Err(e) => {
                    let dur = start.elapsed().as_secs_f64() * 1000.0;
                    return ValidationReport::failure(
                        vec![ValidationError::Syntax(e.to_string())],
                        Vec::new(),
                        Vec::new(),
                        dur,
                    );
                }
            };

            let root_table = match parsed.as_table() {
                Some(t) => t,
                None => {
                    let dur = start.elapsed().as_secs_f64() * 1000.0;
                    return ValidationReport::failure(
                        vec![ValidationError::Syntax(
                            "Root of TOML document must be a table".into(),
                        )],
                        Vec::new(),
                        Vec::new(),
                        dur,
                    );
                }
            };

            // 1. Check [meta]
            if let Some(meta) = root_table.get("meta") {
                checked_sections.push("meta".to_string());
                if let Some(meta_table) = meta.as_table() {
                    Self::check_non_empty_str(meta_table, "meta.mios_version", &mut errors);
                    Self::check_non_empty_str(meta_table, "meta.fedora_version", &mut errors);
                } else {
                    errors.push(ValidationError::InvalidType {
                        field: "meta".to_string(),
                        expected: "table".to_string(),
                        got: meta.type_str().to_string(),
                    });
                }
            } else {
                errors.push(ValidationError::MissingSection("meta".to_string()));
            }

            // 2. Check [identity]
            if let Some(id) = root_table.get("identity") {
                checked_sections.push("identity".to_string());
                if let Some(id_table) = id.as_table() {
                    Self::check_non_empty_str(id_table, "identity.username", &mut errors);
                    Self::check_non_empty_str(id_table, "identity.fullname", &mut errors);
                    Self::check_non_empty_str(id_table, "identity.hostname", &mut errors);
                    Self::check_non_empty_str(id_table, "identity.shell", &mut errors);
                    if let Some(user_val) = id_table.get("username").and_then(|u| u.as_str()) {
                        if user_val == "root" {
                            warnings.push(ValidationWarning {
                            code: "ADVISORY_ROOT_USER".to_string(),
                            message: "Primary user set to 'root'; MiOS standard expects uid 1000 'mios' non-root operator.".to_string(),
                        });
                        }
                    }
                } else {
                    errors.push(ValidationError::InvalidType {
                        field: "identity".to_string(),
                        expected: "table".to_string(),
                        got: id.type_str().to_string(),
                    });
                }
            } else {
                errors.push(ValidationError::MissingSection("identity".to_string()));
            }

            // 3. Check [ports] (Collision & Range Detection)
            if let Some(ports) = root_table.get("ports") {
                checked_sections.push("ports".to_string());
                if let Some(ports_table) = ports.as_table() {
                    let mut port_map: HashMap<u16, String> = HashMap::new();
                    for (key, val) in ports_table {
                        match val.as_integer() {
                            Some(p) => {
                                if p <= 0 || p > 65535 {
                                    errors.push(ValidationError::PortOutOfRange {
                                        key: format!("ports.{}", key),
                                        port: p,
                                    });
                                } else {
                                    let port_u16 = p as u16;
                                    if let Some(existing_key) = port_map.get(&port_u16) {
                                        errors.push(ValidationError::PortCollision {
                                            key1: existing_key.clone(),
                                            key2: format!("ports.{}", key),
                                            port: port_u16,
                                        });
                                    } else {
                                        port_map.insert(port_u16, format!("ports.{}", key));
                                    }
                                }
                            }
                            None => {
                                errors.push(ValidationError::InvalidType {
                                    field: format!("ports.{}", key),
                                    expected: "integer (1..65535)".to_string(),
                                    got: val.type_str().to_string(),
                                });
                            }
                        }
                    }
                } else {
                    errors.push(ValidationError::InvalidType {
                        field: "ports".to_string(),
                        expected: "table".to_string(),
                        got: ports.type_str().to_string(),
                    });
                }
            }

            // 4. Check [build]
            if let Some(build) = root_table.get("build") {
                checked_sections.push("build".to_string());
                if let Some(build_table) = build.as_table() {
                    if let Some(ratchet) = build_table.get("ratchet") {
                        if let Some(r_table) = ratchet.as_table() {
                            if let Some(max_phase) = r_table.get("max_phase_scripts") {
                                if let Some(limit) = max_phase.as_integer() {
                                    if limit < 71 {
                                        errors.push(ValidationError::RatchetViolation {
                                        name: "build.ratchet.max_phase_scripts".to_string(),
                                        current: limit as usize,
                                        bound: 71,
                                        message: "floor value cannot be reduced below established baseline 71".to_string(),
                                    });
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // 5. Check [node]
            if let Some(node) = root_table.get("node") {
                checked_sections.push("node".to_string());
                if let Some(node_table) = node.as_table() {
                    if let Some(nid) = node_table.get("node_id") {
                        if nid.as_integer().map(|i| i <= 0).unwrap_or(true) {
                            errors.push(ValidationError::MissingField(
                                "node.node_id must be a positive integer".to_string(),
                            ));
                        }
                    }
                    if let Some(p) = node_table.get("port") {
                        if let Some(port_num) = p.as_integer() {
                            if port_num <= 0 || port_num > 65535 {
                                errors.push(ValidationError::PortOutOfRange {
                                    key: "node.port".to_string(),
                                    port: port_num,
                                });
                            }
                        }
                    }
                }
            }

            // 6. Check [ci] (if present)
            if let Some(ci) = root_table.get("ci") {
                checked_sections.push("ci".to_string());
                if let Some(ci_table) = ci.as_table() {
                    if let Some(exempt) = ci_table.get("max_exempt_suites") {
                        if let Some(ex_val) = exempt.as_integer() {
                            if ex_val > 6 {
                                errors.push(ValidationError::RatchetViolation {
                                    name: "ci.max_exempt_suites".to_string(),
                                    current: ex_val as usize,
                                    bound: 6,
                                    message: "exemption limit cannot exceed ratchet ceiling 6"
                                        .to_string(),
                                });
                            }
                        }
                    }
                }
            }

            // 7. General Law 7 Scan across top-level string fields
            Self::scan_law7_empty_strings(root_table, "", &mut errors);

            let dur = start.elapsed().as_secs_f64() * 1000.0;
            ValidationReport::failure(errors, warnings, checked_sections, dur)
        }

        fn check_non_empty_str(
            table: &toml::map::Map<String, toml::Value>,
            field_path: &str,
            errors: &mut Vec<ValidationError>,
        ) {
            let field_name = field_path.split('.').next_back().unwrap_or(field_path);
            match table.get(field_name) {
                Some(val) => match val.as_str() {
                    Some(s) if s.trim().is_empty() => {
                        errors.push(ValidationError::Law7EmptyString {
                            field: field_path.to_string(),
                        });
                    }
                    Some(_) => {}
                    None => {
                        errors.push(ValidationError::InvalidType {
                            field: field_path.to_string(),
                            expected: "string".to_string(),
                            got: val.type_str().to_string(),
                        });
                    }
                },
                None => {
                    errors.push(ValidationError::MissingField(field_path.to_string()));
                }
            }
        }

        fn scan_law7_empty_strings(
            table: &toml::map::Map<String, toml::Value>,
            prefix: &str,
            errors: &mut Vec<ValidationError>,
        ) {
            for (key, val) in table {
                let full_path = if prefix.is_empty() {
                    key.clone()
                } else {
                    format!("{}.{}", prefix, key)
                };
                match val {
                    toml::Value::String(s) => {
                        // Check if critical configuration key is empty string
                        if s.trim().is_empty()
                            && (full_path.starts_with("identity.")
                                || full_path.starts_with("meta.")
                                || full_path.starts_with("security.")
                                || full_path.starts_with("network."))
                        {
                            errors.push(ValidationError::Law7EmptyString { field: full_path });
                        }
                    }
                    toml::Value::Table(sub) => {
                        Self::scan_law7_empty_strings(sub, &full_path, errors);
                    }
                    _ => {}
                }
            }
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_valid_ssot_toml() {
            let sample = r#"
[meta]
mios_version = "0.3.0"
fedora_version = "44"

[identity]
username = "mios"
fullname = "MiOS Operator"
hostname = "mios"
shell = "/bin/bash"

[ports]
hermes = 8642
agent_pipe = 8640
llm_light = 11450

[build.ratchet]
max_phase_scripts = 72

[node]
node_id = 101
port = 8650
db_path = "/var/lib/mios/state.json"
"#;
            let report = MiosValidator::validate_str(sample);
            assert!(
                report.is_valid,
                "Report should be valid: {:?}",
                report.errors
            );
            assert!(report.duration_ms < 100.0, "Validation should be fast");
        }

        #[test]
        fn test_port_collision_detection() {
            let sample = r#"
[meta]
mios_version = "0.3.0"
fedora_version = "44"

[identity]
username = "mios"
fullname = "MiOS Operator"
hostname = "mios"
shell = "/bin/bash"

[ports]
service_a = 8080
service_b = 8080
"#;
            let report = MiosValidator::validate_str(sample);
            assert!(!report.is_valid);
            assert!(report
                .errors
                .iter()
                .any(|e| matches!(e, ValidationError::PortCollision { .. })));
        }

        #[test]
        fn test_law7_empty_string_rejection() {
            let sample = r#"
[meta]
mios_version = ""
fedora_version = "44"

[identity]
username = "mios"
fullname = "   "
hostname = "mios"
shell = "/bin/bash"
"#;
            let report = MiosValidator::validate_str(sample);
            assert!(!report.is_valid);
            assert!(report
                .errors
                .iter()
                .any(|e| matches!(e, ValidationError::Law7EmptyString { .. })));
        }

        #[test]
        fn test_ratchet_violation() {
            let sample = r#"
[meta]
mios_version = "0.3.0"
fedora_version = "44"

[identity]
username = "mios"
fullname = "Operator"
hostname = "mios"
shell = "/bin/bash"

[build.ratchet]
max_phase_scripts = 50
"#;
            let report = MiosValidator::validate_str(sample);
            assert!(!report.is_valid);
            assert!(report
                .errors
                .iter()
                .any(|e| matches!(e, ValidationError::RatchetViolation { .. })));
        }
    }
}
pub use validator::{MiosValidator, ValidationError, ValidationReport, ValidationWarning};

#[derive(Error, Debug, Diagnostic)]
#[error("Failed to load MiOS configuration: {message}")]
pub struct ConfigError {
    pub message: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
pub struct IdentityConfig {
    #[serde(default = "default_username")]
    pub username: String,
    #[serde(default = "default_fullname")]
    pub fullname: String,
    #[serde(default = "default_hostname")]
    pub hostname: String,
    #[serde(default = "default_shell")]
    pub shell: String,
    #[serde(default)]
    pub email: String,
    #[serde(default)]
    pub location: String,
}

fn default_username() -> String {
    "mios".into()
}
fn default_fullname() -> String {
    "MiOS Operator".into()
}
fn default_hostname() -> String {
    "mios".into()
}
fn default_shell() -> String {
    "/bin/bash".into()
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
pub struct MetaConfig {
    #[serde(default = "default_version")]
    pub mios_version: String,
    #[serde(default = "default_fedora_version")]
    pub fedora_version: String,
}

fn default_fedora_version() -> String {
    if let Ok(ver) = std::env::var("FEDORA_VERSION") {
        if !ver.is_empty() {
            return ver;
        }
    }
    "44".into()
}

fn default_version() -> String {
    "0.3.0".into()
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
pub struct PhaseItem {
    pub ordinal: String,
    pub name: String,
    pub script: String,
    pub fatal: bool,
    pub apply_class: String,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
pub struct BuildPhases {
    #[serde(default)]
    pub list: Vec<PhaseItem>,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
pub struct BuildRatchet {
    #[serde(default = "default_max_phase_scripts")]
    pub max_phase_scripts: usize,
}

fn default_max_phase_scripts() -> usize {
    77
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq, Default)]
pub struct BuildConfig {
    #[serde(default)]
    pub phases: BuildPhases,
    #[serde(default)]
    pub ratchet: BuildRatchet,
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct NodeConfig {
    #[serde(default = "default_node_id")]
    pub node_id: u32,
    #[serde(default = "default_port")]
    pub port: u16,
    #[serde(default = "default_db_path")]
    pub db_path: String,
    #[serde(default = "default_true")]
    pub discovery_enabled: bool,
}

impl Default for NodeConfig {
    fn default() -> Self {
        Self {
            node_id: default_node_id(),
            port: default_port(),
            db_path: default_db_path(),
            discovery_enabled: default_true(),
        }
    }
}

fn default_node_id() -> u32 {
    101
}
fn default_port() -> u16 {
    8650
}
fn default_db_path() -> String {
    "/var/lib/mios/state.json".into()
}
fn default_true() -> bool {
    true
}

#[derive(Debug, Clone, Default, Serialize, Deserialize, PartialEq)]
pub struct MiosConfig {
    #[serde(default)]
    pub meta: MetaConfig,
    #[serde(default)]
    pub identity: IdentityConfig,
    #[serde(default)]
    pub build: BuildConfig,
    #[serde(default)]
    pub node: NodeConfig,

    /// Raw TOML table capturing all dynamic/operator-defined sections (e.g. packages, repos, security, network, power, compliance, etc.)
    #[serde(flatten)]
    pub raw: toml::Table,
}

impl MiosConfig {
    pub fn load_from_path<P: AsRef<Path>>(path: P) -> Result<Self, ConfigError> {
        Figment::new()
            .merge(Toml::file(path.as_ref()))
            .merge(Env::prefixed("MIOS_"))
            .extract()
            .map_err(|e| ConfigError {
                message: e.to_string(),
            })
    }

    /// The six-tier SSOT (vendor < vendor.d < host < host.d < user < user.d)
    /// through mios-resolver, rooted at MIOS_ROOT when set and at the
    /// installed FHS tiers otherwise, then MIOS_* environment overrides.
    pub fn load_default() -> Result<Self, ConfigError> {
        let root = std::env::var("MIOS_ROOT").ok().filter(|r| !r.is_empty());
        mios_resolver::layers::create_figment(root.as_deref().map(Path::new))
            .merge(Env::prefixed("MIOS_"))
            .extract()
            .map_err(|e| ConfigError {
                message: e.to_string(),
            })
    }

    pub fn section(&self, name: &str) -> Option<&toml::Value> {
        self.raw.get(name)
    }

    pub fn get_value(&self, key: &str) -> Option<&toml::Value> {
        let parts: Vec<&str> = key.split('.').collect();
        if parts.is_empty() {
            return None;
        }
        let mut val = self.raw.get(parts[0])?;
        for part in &parts[1..] {
            match val {
                toml::Value::Table(map) => {
                    val = map.get(*part)?;
                }
                _ => return None,
            }
        }
        Some(val)
    }

    pub fn get_string(&self, key: &str) -> Option<String> {
        self.get_value(key)
            .and_then(|v| v.as_str().map(|s| s.to_string()))
    }

    pub fn get_bool(&self, key: &str) -> Option<bool> {
        self.get_value(key).and_then(|v| v.as_bool())
    }

    pub fn get_i64(&self, key: &str) -> Option<i64> {
        self.get_value(key).and_then(|v| v.as_integer())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_deserialize_real_mios_toml() {
        let config = MiosConfig::load_from_path("../../../usr/share/mios/mios.toml")
            .expect("should deserialize real mios.toml");
        assert_eq!(config.identity.username, "user");
        assert!(config.build.ratchet.max_phase_scripts > 0);
        assert!(!config.build.phases.list.is_empty());
        assert_eq!(config.node.port, 8650);

        assert!(config.section("packages").is_some());
        assert!(config.get_value("security.cosign.policy_mode").is_some());
    }

    #[test]
    fn test_arbitrary_section_zero_recompile() {
        let sample_toml = r#"
[meta]
mios_version = "0.3.0"

[node]
node_id = 202
port = 8655

[foo.bar]
custom_setting = "test_value"
number_key = 42
flag = true
"#;
        let config: MiosConfig = figment::Figment::new()
            .merge(figment::providers::Toml::string(sample_toml))
            .extract()
            .expect("should parse arbitrary custom sections generically");

        assert_eq!(config.node.node_id, 202);
        assert_eq!(config.node.port, 8655);
        assert_eq!(
            config.get_string("foo.bar.custom_setting").as_deref(),
            Some("test_value")
        );
        assert_eq!(config.get_i64("foo.bar.number_key"), Some(42));
        assert_eq!(config.get_bool("foo.bar.flag"), Some(true));
    }
}
