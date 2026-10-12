// AI-hint: Asserts [verbs.*] boolean fields carry canonical TOML booleans, not stringly-typed lookalikes -- hidden, sensitive, params[].required, and params[].default (when type=boolean) must parse as real bools.
// AI-related: automation/98-drift-checks.sh, automation/98-drift-checks.sh, usr/share/mios/mios.toml

use crate::Report;
use std::path::Path;

const CHECK: &str = "canonical-bools";

fn report(ok: bool, summary: String, findings: Vec<String>) -> Report {
    Report {
        check: CHECK.to_string(),
        ok,
        could_not_run: None,
        summary,
        findings,
    }
}

/// Render a value the way the strangled Python gate did in its diagnostics:
/// bare words for bools/numbers, quoted for strings.
fn show(v: &toml::Value) -> String {
    match v {
        toml::Value::String(s) => format!("'{s}'"),
        other => other.to_string(),
    }
}

pub fn check(root: &Path) -> Report {
    // The bash caller pins MIOS_TOML; honour it the way the Python gate did.
    let toml_path = std::env::var("MIOS_TOML")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|_| root.join("usr/share/mios/mios.toml"));

    if !toml_path.is_file() {
        // A tracked deliverable. Its absence is the anomaly, not a reason to
        // report success -- parity with the strangled Python gate.
        return report(
            false,
            String::new(),
            vec![format!(
                "a required SSOT file is missing ({}), so nothing was compared",
                toml_path.display()
            )],
        );
    }
    let text = match std::fs::read_to_string(&toml_path) {
        Ok(t) => t,
        Err(e) => {
            return report(
                false,
                String::new(),
                vec![format!("cannot read {}: {e}", toml_path.display())],
            )
        }
    };
    let parsed: toml::Value = match text.parse() {
        Ok(v) => v,
        Err(e) => {
            return report(
                false,
                String::new(),
                vec![format!("mios.toml does not parse: {e}")],
            )
        }
    };

    let mut findings: Vec<String> = Vec::new();
    let mut verb_count = 0usize;

    if let Some(verbs) = parsed.get("verbs").and_then(|v| v.as_table()) {
        for (vname, vcfg) in verbs {
            if vname == "_defaults" {
                continue;
            }
            let Some(cfg) = vcfg.as_table() else {
                continue;
            };
            verb_count += 1;

            for field in ["hidden", "sensitive"] {
                if let Some(val) = cfg.get(field) {
                    if !val.is_bool() {
                        findings.push(format!(
                            "non-canonical {} value in verb '{}': {} (must be true/false)",
                            field,
                            vname,
                            show(val)
                        ));
                    }
                }
            }

            if let Some(params) = cfg.get("params").and_then(|p| p.as_table()) {
                for (p_name, p_cfg) in params {
                    let Some(p) = p_cfg.as_table() else {
                        continue;
                    };
                    if let Some(req) = p.get("required") {
                        if !req.is_bool() {
                            findings.push(format!(
                                "non-canonical required value in verb '{}' param '{}': {} (must be true/false)",
                                vname,
                                p_name,
                                show(req)
                            ));
                        }
                    }
                    if p.get("type").and_then(|t| t.as_str()) == Some("boolean") {
                        if let Some(d) = p.get("default") {
                            if !d.is_bool() {
                                findings.push(format!(
                                    "non-canonical default boolean value in verb '{}' param '{}': {} (must be true/false)",
                                    vname,
                                    p_name,
                                    show(d)
                                ));
                            }
                        }
                    }
                }
            }
        }
    }

    let summary = if findings.is_empty() {
        format!("{verb_count} verb(s) carry canonical bool literals in [verbs.*]")
    } else {
        format!(
            "{} non-canonical bool literal(s) in [verbs.*]",
            findings.len()
        )
    };
    report(findings.is_empty(), summary, findings)
}
