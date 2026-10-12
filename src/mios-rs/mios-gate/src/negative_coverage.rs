// AI-hint: Asserts every drift check dispatched from main() has a negative test in tests/drift-gate-negatives.sh or sits in the [testing.negative_coverage_exempt] register -- a gate that cannot fail is a phantom, and this keeps phantoms at zero.
// AI-related: automation/98-drift-checks.sh, automation/98-drift-checks.sh, tests/drift-gate-negatives.sh, usr/share/mios/mios.toml

use crate::Report;
use regex::Regex;
use std::collections::BTreeSet;
use std::path::Path;

const CHECK: &str = "negative-coverage";

fn report(ok: bool, summary: String, findings: Vec<String>) -> Report {
    Report {
        check: CHECK.to_string(),
        ok,
        could_not_run: None,
        summary,
        findings,
    }
}

pub fn check(root: &Path) -> Report {
    let checks_sh = root.join("automation/98-drift-checks.sh");
    let negatives_sh = root.join("tests/drift-gate-negatives.sh");
    let toml_path = root.join("usr/share/mios/mios.toml");

    // A tracked deliverable. Its absence is the anomaly, not a reason to
    // report success -- parity with the strangled Python gate.
    if !(checks_sh.is_file() && negatives_sh.is_file() && toml_path.is_file()) {
        return report(
            false,
            String::new(),
            vec!["a required SSOT file is missing (98-drift-checks.sh, drift-gate-negatives.sh or mios.toml), so nothing was compared".to_string()],
        );
    }

    let toml_text = match std::fs::read_to_string(&toml_path) {
        Ok(t) => t,
        Err(e) => {
            return report(
                false,
                String::new(),
                vec![format!("cannot read {}: {e}", toml_path.display())],
            )
        }
    };
    let parsed: toml::Value = match toml_text.parse() {
        Ok(v) => v,
        Err(e) => {
            return report(
                false,
                String::new(),
                vec![format!("mios.toml does not parse: {e}")],
            )
        }
    };
    let exempt: BTreeSet<String> = parsed
        .get("testing")
        .and_then(|t| t.get("negative_coverage_exempt"))
        .and_then(|n| n.get("exempt"))
        .and_then(|e| e.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();

    let c_content = std::fs::read_to_string(&checks_sh).unwrap_or_default();
    let n_content = std::fs::read_to_string(&negatives_sh).unwrap_or_default();

    // Dispatched = the check_* names invoked inside main()'s body; the scan
    // starts at the LAST "main() {" the way the Python gate did, so helper
    // definitions above main never inflate the dispatched set.
    let main_body = match c_content.rfind("main() {") {
        Some(idx) => &c_content[idx..],
        None => c_content.as_str(),
    };
    // Static patterns, but a gate never panics: a compile failure is a
    // finding, not a crash.
    let (dispatch_re, cover_re) = match (
        Regex::new(r"(?m)^\s*(check_[a-z0-9_]+)\b"),
        Regex::new(r"check_[a-z0-9_]+\b"),
    ) {
        (Ok(d), Ok(c)) => (d, c),
        _ => {
            return report(
                false,
                String::new(),
                vec!["internal: coverage regexes failed to compile".to_string()],
            )
        }
    };

    let mut dispatched: BTreeSet<String> = BTreeSet::new();
    for caps in dispatch_re.captures_iter(main_body) {
        if let Some(name) = caps.get(1) {
            dispatched.insert(name.as_str().to_string());
        }
    }
    let mut covered: BTreeSet<String> = BTreeSet::new();
    for caps in cover_re.captures_iter(&n_content) {
        covered.insert(
            caps.get(0)
                .map(|m| m.as_str().to_string())
                .unwrap_or_default(),
        );
    }

    let uncovered: Vec<&String> = dispatched
        .iter()
        .filter(|d| !covered.contains(*d) && !exempt.contains(*d))
        .collect();

    if !uncovered.is_empty() {
        let names: Vec<&str> = uncovered.iter().map(|s| s.as_str()).collect();
        return report(
            false,
            format!(
                "{} dispatched check(s) lack negative coverage",
                uncovered.len()
            ),
            vec![format!(
                "dispatched drift checks lacking negative test coverage and not exempt: [{}]",
                names
                    .iter()
                    .map(|n| format!("'{n}'"))
                    .collect::<Vec<_>>()
                    .join(", ")
            )],
        );
    }

    report(
        true,
        format!(
            "{} dispatched check(s) all covered by negatives or exempt ({} exempt)",
            dispatched.len(),
            exempt.len()
        ),
        Vec::new(),
    )
}
