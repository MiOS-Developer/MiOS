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

            // Fedora belongs to [versions], the canonical SSOT version registry.
            match root_table.get("versions") {
                Some(toml::Value::Table(versions)) => {
                    checked_sections.push("versions".to_string());
                    Self::check_non_empty_str(versions, "versions.fedora", &mut errors);
                }
                Some(value) => errors.push(ValidationError::InvalidType {
                    field: "versions".into(),
                    expected: "table".into(),
                    got: value.type_str().into(),
                }),
                None => errors.push(ValidationError::MissingSection("versions".into())),
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
                    let stack_offset = match ports_table.get("stack_id") {
                        None => 0,
                        Some(value) => match value.as_integer() {
                            Some(n) if n >= 0 && n.checked_mul(10000).is_some() => n * 10000,
                            _ => {
                                errors.push(ValidationError::InvalidType {
                                    field: "ports.stack_id".into(),
                                    expected: "nonnegative integer with representable port offset"
                                        .into(),
                                    got: value.type_str().into(),
                                });
                                0
                            }
                        },
                    };
                    for (key, val) in ports_table {
                        let field = format!("ports.{key}");
                        match key.as_str() {
                            "stack_id" => continue,
                            "stale_fallbacks" | "unbound" => {
                                if !val.as_array().is_some_and(|items| {
                                    items.iter().all(|item| {
                                        item.as_str().is_some_and(|s| !s.trim().is_empty())
                                    })
                                }) {
                                    errors.push(ValidationError::InvalidType {
                                        field,
                                        expected: "array of nonempty strings".into(),
                                        got: val.type_str().into(),
                                    });
                                }
                                continue;
                            }
                            "categories" => {
                                if !val.as_table().is_some_and(|categories| {
                                    categories.values().all(|category| category.is_table())
                                }) {
                                    errors.push(ValidationError::InvalidType {
                                        field,
                                        expected: "table of category tables".into(),
                                        got: val.type_str().into(),
                                    });
                                }
                                continue;
                            }
                            _ => {}
                        }
                        match val.as_integer() {
                            Some(p) => {
                                // Match the resolver: DNS remains protocol-pinned at 53.
                                let resolved = if p == 53 {
                                    Some(p)
                                } else {
                                    p.checked_add(stack_offset)
                                };
                                if !(1..=65535).contains(&p)
                                    || !resolved.is_some_and(|port| (1..=65535).contains(&port))
                                {
                                    errors.push(ValidationError::PortOutOfRange {
                                        key: field,
                                        port: resolved.unwrap_or(i64::MAX),
                                    });
                                } else {
                                    let port_u16 = resolved.expect("range checked") as u16;
                                    if let Some(existing_key) = port_map.get(&port_u16) {
                                        errors.push(ValidationError::PortCollision {
                                            key1: existing_key.clone(),
                                            key2: field,
                                            port: port_u16,
                                        });
                                    } else {
                                        port_map.insert(port_u16, field);
                                    }
                                }
                            }
                            None => errors.push(ValidationError::InvalidType {
                                field,
                                expected: "integer (1..65535)".into(),
                                got: val.type_str().into(),
                            }),
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
            // Empty vendor defaults are declared optional values, not Law 7 violations.
            // Derive their exact paths from the SSOT instead of another exemption list.
            static VENDOR: std::sync::OnceLock<toml::Value> = std::sync::OnceLock::new();
            let vendor = VENDOR.get_or_init(|| {
                include_str!("../../../../usr/share/mios/mios.toml")
                    .parse()
                    .expect("canonical vendor TOML must parse")
            });
            Self::scan_law7_empty_strings(root_table, "", vendor, &mut errors);

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
            vendor: &toml::Value,
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
                        let declared_optional = s.is_empty()
                            && full_path
                                .split('.')
                                .try_fold(vendor, |value, part| value.get(part))
                                .and_then(toml::Value::as_str)
                                == Some("");
                        if s.trim().is_empty()
                            && !declared_optional
                            && (full_path.starts_with("identity.")
                                || full_path.starts_with("meta.")
                                || full_path.starts_with("security.")
                                || full_path.starts_with("network."))
                        {
                            errors.push(ValidationError::Law7EmptyString { field: full_path });
                        }
                    }
                    toml::Value::Table(sub) => {
                        Self::scan_law7_empty_strings(sub, &full_path, vendor, errors);
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
[versions]
fedora = "44"

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
[versions]
fedora = "44"

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
[versions]
fedora = "44"

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
[versions]
fedora = "44"

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

        fn vendor() -> toml::Value {
            include_str!("../../../../usr/share/mios/mios.toml")
                .parse()
                .unwrap()
        }

        fn report(value: &toml::Value) -> ValidationReport {
            MiosValidator::validate_str(&toml::to_string(value).unwrap())
        }

        #[test]
        fn canonical_vendor_ssot_passes() {
            let report = report(&vendor());
            assert!(report.is_valid, "{:?}", report.errors);
            assert!(report.checked_sections.contains(&"versions".into()));
            assert!(report.checked_sections.contains(&"ports".into()));
        }

        #[test]
        fn resolved_vendor_host_user_layers_pass() {
            let dir = tempfile::Builder::new()
                .prefix("mios-config-layers")
                .tempdir()
                .unwrap();
            let vendor_path = dir.path().join("vendor.toml");
            let host_path = dir.path().join("host.toml");
            let user_path = dir.path().join("user.toml");
            std::fs::write(
                &vendor_path,
                include_str!("../../../../usr/share/mios/mios.toml"),
            )
            .unwrap();
            std::fs::write(
                &host_path,
                "[identity]\nhostname = \"layered-host\"\n[ports]\nstack_id = 1\n",
            )
            .unwrap();
            std::fs::write(
                &user_path,
                "[identity]\nfullname = \"Layered Operator\"\nlocation = \"\"\n",
            )
            .unwrap();
            let merged =
                mios_resolver::layers::merge_layer_files(&[vendor_path, host_path, user_path])
                    .unwrap();
            assert_eq!(
                merged["identity"]["hostname"].as_str(),
                Some("layered-host")
            );
            assert_eq!(
                merged["identity"]["fullname"].as_str(),
                Some("Layered Operator")
            );
            let report = report(&merged);
            assert!(report.is_valid, "{:?}", report.errors);
        }

        #[test]
        fn canonical_version_is_required_and_typed() {
            for value in [
                None,
                Some(toml::Value::Integer(44)),
                Some(toml::Value::String(" ".into())),
            ] {
                let mut config = vendor();
                config["versions"].as_table_mut().unwrap().remove("fedora");
                if let Some(value) = value.clone() {
                    config["versions"]
                        .as_table_mut()
                        .unwrap()
                        .insert("fedora".into(), value);
                }
                let report = report(&config);
                assert!(!report.is_valid);
                assert!(
                    report.errors.iter().any(|error| match (&value, error) {
                        (None, ValidationError::MissingField(field)) => field == "versions.fedora",
                        (
                            Some(toml::Value::Integer(_)),
                            ValidationError::InvalidType { field, .. },
                        ) => field == "versions.fedora",
                        (
                            Some(toml::Value::String(_)),
                            ValidationError::Law7EmptyString { field },
                        ) => field == "versions.fedora",
                        _ => false,
                    }),
                    "{:?}",
                    report.errors
                );
            }
        }

        #[test]
        fn invalid_port_types_and_metadata_fail() {
            for (key, value) in [
                ("hermes", toml::Value::String("8720".into())),
                ("stack_id", toml::Value::Integer(-1)),
                ("stack_id", toml::Value::String("0".into())),
                ("stack_id", toml::Value::Integer(i64::MAX)),
                ("stale_fallbacks", toml::Value::Integer(0)),
                ("unbound", toml::Value::Array(vec![toml::Value::Integer(2)])),
                ("categories", toml::Value::String("invalid".into())),
                (
                    "categories",
                    toml::Value::Table(
                        [("bad".into(), toml::Value::Integer(1))]
                            .into_iter()
                            .collect(),
                    ),
                ),
            ] {
                let mut config = vendor();
                config["ports"][key] = value;
                let report = report(&config);
                assert!(!report.is_valid);
                assert!(report.errors.iter().any(|error| matches!(
                    error, ValidationError::InvalidType { field, .. } if field == &format!("ports.{key}")
                )), "{key}: {:?}", report.errors);
            }
        }

        #[test]
        fn invalid_base_and_shifted_ports_fail() {
            for (base, stack_id) in [
                (0, 0),
                (-1, 0),
                (65536, 0),
                (65000, 1),
                (65000, i64::MAX / 10000),
            ] {
                let mut config = vendor();
                config["ports"]["hermes"] = toml::Value::Integer(base);
                config["ports"]["stack_id"] = toml::Value::Integer(stack_id);
                let report = report(&config);
                assert!(!report.is_valid);
                assert!(
                    report.errors.iter().any(|error| matches!(
                        error, ValidationError::PortOutOfRange { key, .. } if key == "ports.hermes"
                    )),
                    "{:?}",
                    report.errors
                );
            }
        }

        #[test]
        fn service_port_collisions_fail_with_metadata_present() {
            let mut config = vendor();
            config["ports"]["hermes"] = config["ports"]["agent_pipe"].clone();
            let report = report(&config);
            assert!(!report.is_valid);
            assert!(
                report.errors.iter().any(|error| matches!(
                    error, ValidationError::PortCollision { key1, key2, .. }
                    if [key1.as_str(), key2.as_str()].contains(&"ports.hermes")
                        && [key1.as_str(), key2.as_str()].contains(&"ports.agent_pipe")
                )),
                "{:?}",
                report.errors
            );
        }

        #[test]
        fn vendor_optional_values_do_not_allow_new_empty_or_whitespace_fields() {
            for (section, key, value) in [
                ("identity", "undeclared", ""),
                ("security", "undeclared", ""),
                ("network", "undeclared", ""),
                ("identity", "location", " "),
                ("security", "arbiter_allow", "\t"),
                ("identity", "username", ""),
                ("meta", "mios_version", ""),
            ] {
                let mut config = vendor();
                config[section]
                    .as_table_mut()
                    .unwrap()
                    .insert(key.into(), toml::Value::String(value.into()));
                let report = report(&config);
                assert!(!report.is_valid);
                assert!(report.errors.iter().any(|error| matches!(
                    error, ValidationError::Law7EmptyString { field } if field == &format!("{section}.{key}")
                )), "{section}.{key}: {:?}", report.errors);
            }
        }

        #[test]
        fn syntax_and_missing_file_fail() {
            assert!(matches!(
                MiosValidator::validate_str("[invalid").errors.as_slice(),
                [ValidationError::Syntax(_)]
            ));
            let dir = tempfile::Builder::new()
                .prefix("mios-config-missing")
                .tempdir()
                .unwrap();
            let report = MiosValidator::validate_file(dir.path().join("missing.toml"));
            assert!(!report.is_valid);
            assert!(matches!(
                report.errors.as_slice(),
                [ValidationError::Syntax(_)]
            ));
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
    /// Compatibility view resolved from [versions].fedora by MiosConfig.
    /// Read the old field only to detect conflicting input; never emit a second SSOT.
    #[serde(default, skip_serializing)]
    pub fedora_version: String,
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
#[serde(try_from = "MiosConfigInput")]
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

// Keep the public typed view stable while resolving cross-section values once,
// after Figment has merged the complete vendor/host/user input.
#[derive(Deserialize)]
struct MiosConfigInput {
    #[serde(default)]
    meta: MetaConfig,
    #[serde(default)]
    identity: IdentityConfig,
    #[serde(default)]
    build: BuildConfig,
    #[serde(default)]
    node: NodeConfig,
    #[serde(flatten)]
    raw: toml::Table,
}

impl TryFrom<MiosConfigInput> for MiosConfig {
    type Error = String;

    fn try_from(mut input: MiosConfigInput) -> Result<Self, Self::Error> {
        let fedora = input.raw.get("versions")
            .and_then(toml::Value::as_table)
            .and_then(|versions| versions.get("fedora"))
            .and_then(toml::Value::as_str)
            .filter(|version| !version.trim().is_empty())
            .ok_or("versions.fedora must be a nonempty string; no legacy or host-environment fallback is supported")?;
        if !input.meta.fedora_version.is_empty() && input.meta.fedora_version != fedora {
            return Err("meta.fedora_version conflicts with canonical versions.fedora; remove the legacy field".into());
        }
        input.meta.fedora_version = fedora.to_string();
        Ok(Self {
            meta: input.meta,
            identity: input.identity,
            build: input.build,
            node: input.node,
            raw: input.raw,
        })
    }
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

        assert_eq!(
            config.meta.fedora_version,
            config.get_string("versions.fedora").unwrap()
        );
        assert!(config.section("packages").is_some());
        assert!(config.get_value("security.cosign.policy_mode").is_some());
    }

    #[test]
    fn test_arbitrary_section_zero_recompile() {
        let sample_toml = r#"
[meta]
mios_version = "0.3.0"

[versions]
fedora = "fixture-release"

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
    fn typed(sample: &str) -> Result<MiosConfig, figment::Error> {
        Figment::new().merge(Toml::string(sample)).extract()
    }

    #[test]
    fn canonical_fedora_resolves_after_layer_merge() {
        let dir = tempfile::Builder::new()
            .prefix("mios-config-version")
            .tempdir()
            .unwrap();
        let vendor = dir.path().join("vendor.toml");
        let host = dir.path().join("host.toml");
        let user = dir.path().join("user.toml");
        std::fs::write(&vendor, "[versions]\nfedora = \"vendor-release\"\n").unwrap();
        std::fs::write(&host, "[versions]\nfedora = \"host-release\"\n").unwrap();
        std::fs::write(&user, "[versions]\nfedora = \"user-release\"\n").unwrap();
        let config: MiosConfig = mios_resolver::layers::figment_of(&[vendor, host, user])
            .extract()
            .unwrap();
        assert_eq!(config.meta.fedora_version, "user-release");
        assert_eq!(
            config.get_string("versions.fedora").as_deref(),
            Some("user-release")
        );
    }

    #[test]
    fn typed_file_loading_uses_canonical_version() {
        let dir = tempfile::Builder::new()
            .prefix("mios-config-version-file")
            .tempdir()
            .unwrap();
        let path = dir.path().join("mios.toml");
        std::fs::write(&path, "[versions]\nfedora = \"changed-release\"\n").unwrap();
        let config = MiosConfig::load_from_path(&path).unwrap();
        assert_eq!(config.meta.fedora_version, "changed-release");
        assert_eq!(
            config.get_string("versions.fedora").as_deref(),
            Some("changed-release")
        );
    }

    #[test]
    fn missing_malformed_or_legacy_only_fedora_cannot_default() {
        assert!(MetaConfig::default().fedora_version.is_empty());
        for sample in [
            "",
            "[meta]\nfedora_version = \"legacy-release\"",
            "[versions]\nfedora = \"\"",
            "[versions]\nfedora = \"  \"",
            "[versions]\nfedora = 44",
            "[versions]\nfedora = false",
            "versions = \"invalid\"",
        ] {
            let error = typed(sample).expect_err("invalid canonical version must fail");
            assert!(error.to_string().contains("versions.fedora"), "{error}");
        }
    }

    #[test]
    fn legacy_fedora_conflict_fails_and_matching_legacy_is_not_emitted() {
        let error = typed("[meta]\nfedora_version = \"legacy-release\"\n[versions]\nfedora = \"canonical-release\"")
            .expect_err("legacy mismatch must not silently override canonical version");
        assert!(
            error
                .to_string()
                .contains("meta.fedora_version conflicts with canonical versions.fedora"),
            "{error}"
        );
        let config = typed("[meta]\nfedora_version = \"canonical-release\"\n[versions]\nfedora = \"canonical-release\"").unwrap();
        assert_eq!(config.meta.fedora_version, "canonical-release");
        let serialized = toml::to_string(&config).unwrap();
        let document: toml::Value = serialized.parse().unwrap();
        assert!(document["meta"].get("fedora_version").is_none());
        assert_eq!(
            document["versions"]["fedora"].as_str(),
            Some("canonical-release")
        );
        let roundtrip = typed(&serialized).unwrap();
        assert_eq!(roundtrip.meta.fedora_version, config.meta.fedora_version);
    }
}
