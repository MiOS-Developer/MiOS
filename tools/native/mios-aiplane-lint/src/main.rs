// AI-hint: Rust AI-plane linting tool for [agent_pipe] budget keys and recursion/width bounds.
use regex::Regex;
use std::fs;
use std::path::{Path, PathBuf};

/// Every scalar leaf under `[agent_pipe]` and `[dispatch]`, read from SSOT.
///
/// This was a hardcoded list of nine names. The tables hold 128, so the check
/// walked 7% of its own subject and announced "all [agent_pipe] budget
/// variables have code consumers" over the other 93%. Adding an unconsumed key
/// to either table passed at rc=0, which is the failure the message denies.
/// It was also a registry of operator-tunable names living in Rust source
/// rather than in SSOT (Law 7).
pub fn budget_keys(toml_val: &toml::Value) -> Vec<String> {
    fn leaves(val: &toml::Value, out: &mut Vec<String>) {
        if let Some(table) = val.as_table() {
            for (k, v) in table {
                if v.is_table() {
                    leaves(v, out);
                } else {
                    out.push(k.clone());
                }
            }
        }
    }
    let mut out = Vec::new();
    for t in ["agent_pipe", "dispatch"] {
        if let Some(v) = toml_val.get(t) {
            leaves(v, &mut out);
        }
    }
    out.sort();
    out.dedup();
    out
}

fn key_in_toml_value(val: &toml::Value, target_key: &str) -> bool {
    if let Some(table) = val.as_table() {
        if table.contains_key(target_key) {
            return true;
        }
        for (_k, v) in table {
            if key_in_toml_value(v, target_key) {
                return true;
            }
        }
    }
    false
}

pub fn validate_budgets(
    toml_val: &toml::Value,
    code_contents: &str,
    budget_keys: &[&str],
) -> Result<(), Vec<String>> {
    let agent_pipe = toml_val.get("agent_pipe");
    let dispatch = toml_val.get("dispatch");

    let mut missing = Vec::new();

    for &key in budget_keys {
        let in_agent_pipe = agent_pipe.is_some_and(|v| key_in_toml_value(v, key));
        let in_dispatch = dispatch.is_some_and(|v| key_in_toml_value(v, key));

        if !in_agent_pipe && !in_dispatch {
            missing.push(format!("{} (missing from mios.toml)", key));
            continue;
        }

        let quoted_pattern = format!(r#"['"]{}['"]"#, regex::escape(key));
        let re = Regex::new(&quoted_pattern).unwrap();

        if !re.is_match(code_contents) && !code_contents.contains(key) {
            missing.push(key.to_string());
        }
    }

    if missing.is_empty() {
        Ok(())
    } else {
        Err(missing)
    }
}

/// The directories a budget key can legitimately be consumed from.
///
/// This used to be `usr/lib/mios/agent-pipe` alone, which is narrower than the
/// consumer surface: `[dispatch].gpu_profile` is read by
/// `usr/libexec/mios/mios-swarm-pack-firstboot` and would have been reported
/// dead. A false "unconsumed" is as damaging as a missed one -- it sends
/// someone to delete a key that is load-bearing.
const CONSUMER_DIRS: [&str; 4] = [
    "usr/lib/mios",
    "usr/libexec/mios",
    "src/mios-rs",
    "tools/native",
];

/// Source that could read a key: Python, shell, Rust, and the extensionless
/// libexec verbs. Build artefacts and vendored venvs are not source.
///
/// Two exclusions are not incidental. This lint's OWN crate is skipped because
/// it necessarily spells budget keys -- its tests contain them as literals --
/// and a check that reads itself will find every key it looks for. Test files
/// are skipped for the same reason one directory out: naming a key is not
/// consuming it, which is exactly why `reflexion_limit` and `tool_loop_limit`
/// are on the unconsumed register despite appearing in tools/drift-checks.py.
/// Verified rather than assumed: the residue is 9 with or without either
/// exclusion, so neither costs a real consumer.
fn is_consumer_source(path: &Path) -> bool {
    let s = path.to_string_lossy();
    if s.contains("/target/") || s.contains("/.venv/") || s.contains("/node_modules/") {
        return false;
    }
    if s.contains("mios-aiplane-lint") {
        return false;
    }
    // The native drift checks are gates: check_structured asserts the budget
    // keys are PRESENT, the same naming-without-reading that kept
    // tools/drift-checks.py from counting. A gate is never a consumer.
    if s.replace('\\', "/").contains("/miosd/src/drift/") {
        return false;
    }
    let name = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
    if s.contains("/tests/") || name.starts_with("test_") || name.starts_with("test-") {
        return false;
    }
    match path.extension().and_then(|e| e.to_str()) {
        Some("py") | Some("sh") | Some("rs") => true,
        // A libexec verb has no extension; a .md or .json beside it is not code.
        None => true,
        _ => false,
    }
}

fn read_consumer_sources(root: &Path) -> std::io::Result<String> {
    let mut code = String::new();
    for rel in CONSUMER_DIRS {
        let dir = root.join(rel);
        if !dir.is_dir() {
            continue;
        }
        for entry in walkdir::WalkDir::new(&dir)
            .into_iter()
            .filter_map(|e| e.ok())
        {
            let path = entry.path();
            if path.is_file() && is_consumer_source(path) {
                if let Ok(c) = fs::read_to_string(path) {
                    code.push_str(&c);
                    code.push('\n');
                }
            }
        }
    }
    Ok(code)
}

fn main() {
    let root_str = std::env::var("MIOS_DRIFT_ROOT").unwrap_or_else(|_| {
        std::env::current_dir()
            .unwrap()
            .to_string_lossy()
            .to_string()
    });
    let root = PathBuf::from(&root_str);

    let toml_path = root.join("usr/share/mios/mios.toml");
    if !toml_path.exists() {
        eprintln!("    Missing mios.toml at {}", toml_path.display());
        std::process::exit(1);
    }

    let toml_content = match fs::read_to_string(&toml_path) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("    Failed to read mios.toml: {}", e);
            std::process::exit(1);
        }
    };

    let toml_val: toml::Value = match toml::from_str(&toml_content) {
        Ok(v) => v,
        Err(e) => {
            eprintln!("    Failed to parse mios.toml: {}", e);
            std::process::exit(1);
        }
    };

    let code_contents = match read_consumer_sources(&root) {
        Ok(c) => c,
        Err(e) => {
            eprintln!("    Failed to read consumer sources: {}", e);
            std::process::exit(1);
        }
    };

    // A key REMOVED from SSOT is invisible to enumeration -- it is simply not
    // in the set, so nothing is missing from it. The hardcoded list this
    // replaced anchored a floor as a side effect of being hardcoded; that floor
    // now lives in SSOT and is checked explicitly.
    let required = required_keys(&toml_val);
    if required.is_empty() {
        eprintln!(
            "    [drift.budget_keys].required is empty or absent -- a floor of zero keys \
             cannot catch a key being deleted from SSOT"
        );
        std::process::exit(1);
    }
    let present: std::collections::BTreeSet<String> = budget_keys(&toml_val).into_iter().collect();
    let absent: Vec<&String> = required.iter().filter(|r| !present.contains(*r)).collect();
    if !absent.is_empty() {
        eprintln!(
            "    {} required budget key(s) missing from [agent_pipe]/[dispatch]: {:?}",
            absent.len(),
            absent
        );
        std::process::exit(1);
    }

    let keys = budget_keys(&toml_val);
    if keys.is_empty() {
        // An empty subject is the Empty-Set Pass this rewrite exists to remove:
        // zero keys would otherwise "all" be consumed.
        eprintln!("    [agent_pipe] and [dispatch] declare no keys -- nothing was checked");
        std::process::exit(1);
    }
    let key_refs: Vec<&str> = keys.iter().map(|s| s.as_str()).collect();

    // Itemised, not a count: a bare ceiling lets one dead key swap for another.
    let registered = registered_unconsumed(&toml_val);
    let ceiling = unconsumed_ceiling(&toml_val);

    match validate_budgets(&toml_val, &code_contents, &key_refs) {
        Ok(()) => {
            if !registered.is_empty() {
                eprintln!(
                    "    {} key(s) are on [drift.budget_keys].unconsumed but now HAVE a consumer -- remove them and lower the ceiling: {:?}",
                    registered.len(),
                    registered
                );
                std::process::exit(1);
            }
            println!(
                "[mios-aiplane-lint] PASS: all {} [agent_pipe]/[dispatch] keys defined and consumed",
                keys.len()
            );
            std::process::exit(0);
        }
        Err(missing) => {
            let unregistered: Vec<&String> =
                missing.iter().filter(|m| !registered.contains(m)).collect();
            let stale: Vec<&String> = registered.iter().filter(|r| !missing.contains(r)).collect();
            if !unregistered.is_empty() {
                eprintln!(
                    "    {} [agent_pipe]/[dispatch] key(s) have no consumer and are not registered -- wire them or add them to [drift.budget_keys].unconsumed with a reason: {:?}",
                    unregistered.len(),
                    unregistered
                );
                std::process::exit(1);
            }
            if !stale.is_empty() {
                eprintln!(
                    "    {} registered key(s) now HAVE a consumer -- remove them and lower the ceiling: {:?}",
                    stale.len(),
                    stale
                );
                std::process::exit(1);
            }
            if missing.len() as i64 > ceiling {
                eprintln!(
                    "    {} unconsumed key(s) exceeds the ceiling of {} -- shrink-only",
                    missing.len(),
                    ceiling
                );
                std::process::exit(1);
            }
            if (missing.len() as i64) < ceiling {
                eprintln!(
                    "    ceiling is {} but only {} key(s) are unconsumed -- lower it; a ceiling above the measurement is slack",
                    ceiling,
                    missing.len()
                );
                std::process::exit(1);
            }
            println!(
                "[mios-aiplane-lint] PASS: {} of {} [agent_pipe]/[dispatch] keys consumed, {} registered unconsumed (ceiling {})",
                keys.len() - missing.len(),
                keys.len(),
                missing.len(),
                ceiling
            );
            std::process::exit(0);
        }
    }
}

/// Keys that must exist in [agent_pipe] or [dispatch]. The floor enumeration
/// cannot provide: a deleted key is absent from the enumerated set, not missing
/// from it.
fn required_keys(toml_val: &toml::Value) -> Vec<String> {
    toml_val
        .get("drift")
        .and_then(|d| d.get("budget_keys"))
        .and_then(|b| b.get("required"))
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default()
}

/// The itemised shrink-only register of keys SSOT declares that nothing reads.
fn registered_unconsumed(toml_val: &toml::Value) -> Vec<String> {
    toml_val
        .get("drift")
        .and_then(|d| d.get("budget_keys"))
        .and_then(|b| b.get("unconsumed"))
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(|s| s.to_string()))
                .collect()
        })
        .unwrap_or_default()
}

fn unconsumed_ceiling(toml_val: &toml::Value) -> i64 {
    toml_val
        .get("drift")
        .and_then(|d| d.get("budget_keys"))
        .and_then(|b| b.get("max_unconsumed"))
        .and_then(|v| v.as_integer())
        .unwrap_or(0)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The subject is the tables, not a list in this file.
    #[test]
    fn test_budget_keys_enumerates_both_tables_and_nested_ones() {
        let toml_val: toml::Value = toml::from_str(
            r#"
            [agent_pipe]
            tool_max_iters = 15
            [agent_pipe.quality]
            min_length = 4
            [dispatch]
            default_hop_budget = 2
            [dispatch.autonomy]
            max_dispatch_depth = 2
            [unrelated]
            not_a_budget = 1
        "#,
        )
        .unwrap();
        let keys = budget_keys(&toml_val);
        assert_eq!(
            keys,
            vec![
                "default_hop_budget",
                "max_dispatch_depth",
                "min_length",
                "tool_max_iters"
            ],
            "nested tables count, and a table that is not a budget table does not"
        );
    }

    /// A key DELETED from SSOT is absent from the enumeration, not missing from
    /// it, so enumeration alone cannot see it go. The required floor can.
    #[test]
    fn test_required_floor_sees_a_deleted_key() {
        let toml_val: toml::Value = toml::from_str(
            r#"
            [agent_pipe]
            tool_max_iters = 15
            [drift.budget_keys]
            required = ["tool_max_iters", "swarm_max_width"]
        "#,
        )
        .unwrap();
        let present: std::collections::BTreeSet<String> =
            budget_keys(&toml_val).into_iter().collect();
        let required = required_keys(&toml_val);
        let absent: Vec<&String> = required.iter().filter(|r| !present.contains(*r)).collect();
        assert_eq!(
            absent,
            vec!["swarm_max_width"],
            "a required key missing from both tables must be reported"
        );
    }

    /// This lint's own source spells budget keys -- the test above this one
    /// contains "swarm_max_width" as a literal. Scanning itself would make
    /// every key it looks for appear consumed.
    #[test]
    fn test_own_crate_is_not_a_consumer() {
        assert!(!is_consumer_source(Path::new(
            "tools/native/mios-aiplane-lint/src/main.rs"
        )));
        assert!(!is_consumer_source(Path::new(
            "usr/lib/mios/agent-pipe/test_mios_x.py"
        )));
        assert!(!is_consumer_source(Path::new(
            "src/mios-rs/miosd/tests/x.rs"
        )));
        // A real consumer is still one.
        assert!(is_consumer_source(Path::new(
            "usr/libexec/mios/mios-swarm-pack-firstboot"
        )));
        assert!(is_consumer_source(Path::new(
            "usr/lib/mios/agent-pipe/router.py"
        )));
    }

    /// Neither table present must yield nothing, so main() can refuse rather
    /// than report that all zero keys are consumed.
    #[test]
    fn test_budget_keys_empty_when_tables_absent() {
        let toml_val: toml::Value = toml::from_str("[other]\nx = 1\n").unwrap();
        assert!(budget_keys(&toml_val).is_empty());
    }

    /// The regression this rewrite exists for: a key added to the table with no
    /// consumer must be reported. Under the hardcoded nine-name list it was not.
    #[test]
    fn test_unconsumed_key_in_table_is_caught() {
        let toml_val: toml::Value = toml::from_str(
            r#"
            [agent_pipe]
            tool_max_iters = 15
            planted_unconsumed = 99
        "#,
        )
        .unwrap();
        let code = r#"iter_limit = config.get("tool_max_iters", 15)"#;
        let keys = budget_keys(&toml_val);
        let refs: Vec<&str> = keys.iter().map(|s| s.as_str()).collect();
        let errs = validate_budgets(&toml_val, code, &refs).unwrap_err();
        assert_eq!(errs, vec!["planted_unconsumed"]);
    }

    #[test]
    fn test_validate_budgets_pass() {
        let toml_str = r#"
            [agent_pipe]
            tool_max_iters = 15
            replan_max = 5

            [dispatch.autonomy]
            max_dispatch_depth = 2

            [dispatch]
            default_hop_budget = 2
        "#;
        let toml_val: toml::Value = toml::from_str(toml_str).unwrap();
        let code = r#"
            iter_limit = config.get("tool_max_iters", 15)
            max_replan = config.get("replan_max", 5)
            depth = config.get("max_dispatch_depth", 2)
            hop = config.get("default_hop_budget", 2)
        "#;
        let keys = vec![
            "tool_max_iters",
            "replan_max",
            "max_dispatch_depth",
            "default_hop_budget",
        ];
        assert!(validate_budgets(&toml_val, code, &keys).is_ok());
    }

    #[test]
    fn test_validate_budgets_missing_toml() {
        let toml_str = r#"
            [agent_pipe]
            tool_max_iters = 15
        "#;
        let toml_val: toml::Value = toml::from_str(toml_str).unwrap();
        let code = r#"
            iter_limit = config.get("tool_max_iters", 15)
            max_replan = config.get("replan_max", 5)
        "#;
        let keys = vec!["tool_max_iters", "replan_max"];
        let res = validate_budgets(&toml_val, code, &keys);
        assert!(res.is_err());
        let errs = res.unwrap_err();
        assert_eq!(errs.len(), 1);
        assert!(errs[0].contains("replan_max (missing from mios.toml)"));
    }

    #[test]
    fn test_validate_budgets_missing_code() {
        let toml_str = r#"
            [agent_pipe]
            tool_max_iters = 15
            replan_max = 5
        "#;
        let toml_val: toml::Value = toml::from_str(toml_str).unwrap();
        let code = r#"
            iter_limit = config.get("tool_max_iters", 15)
        "#;
        let keys = vec!["tool_max_iters", "replan_max"];
        let res = validate_budgets(&toml_val, code, &keys);
        assert!(res.is_err());
        let errs = res.unwrap_err();
        assert_eq!(errs.len(), 1);
        assert_eq!(errs[0], "replan_max");
    }
}
