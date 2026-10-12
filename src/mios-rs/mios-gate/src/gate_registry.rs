// AI-hint: Verifies gate definitions, bounded main registrations and drift-tool references without mutating sources.
// AI-related: automation/98-drift-checks.sh, tests/drift-gate-negatives.sh

use crate::Report;
use regex::Regex;
use std::collections::BTreeMap;
use std::path::Path;

const CHECK: &str = "gate-registry";

fn inspect(root: &Path) -> Result<Report, String> {
    let script = std::fs::read_to_string(root.join("automation/98-drift-checks.sh"))
        .map_err(|e| format!("cannot read automation/98-drift-checks.sh: {e}"))?;
    let definition = Regex::new(r"^(check_[a-z0-9_]+)\s*\(\)\s*\{").map_err(|e| e.to_string())?;
    let call =
        Regex::new(r"^\s*(check_[a-z0-9_]+)\s*($|#|;|\|\||&&)").map_err(|e| e.to_string())?;
    let function = Regex::new(r"^[a-zA-Z_][a-zA-Z0-9_]*\s*\(\)\s*\{").map_err(|e| e.to_string())?;
    let mut definitions = BTreeMap::<String, usize>::new();
    let mut calls = BTreeMap::<String, usize>::new();
    let mut in_main = false;
    let mut mains = 0;
    let mut closed = false;
    for line in script.lines() {
        let clean = line.split('#').next().unwrap_or("").trim_end();
        if clean == "main() {" {
            mains += 1;
            in_main = true;
            continue;
        }
        // This source contract uses column-zero function delimiters. Do not
        // count helper calls after main's closing delimiter as registrations.
        if in_main && clean == "}" {
            in_main = false;
            closed = true;
            continue;
        }
        if in_main && function.is_match(clean) {
            return Err("function definition encountered before main() closed".into());
        }
        if let Some(found) = definition.captures(line) {
            *definitions.entry(found[1].into()).or_default() += 1;
        }
        if in_main {
            if let Some(found) = call.captures(clean) {
                *calls.entry(found[1].into()).or_default() += 1;
            }
        }
    }
    if mains != 1 || !closed || in_main {
        return Err("expected one closed column-zero main() function".into());
    }
    if definitions.is_empty() || calls.is_empty() {
        return Err("gate definitions or main() registrations are empty".into());
    }
    let mut findings = Vec::new();
    for (name, count) in &definitions {
        if *count > 1 {
            findings.push(format!(
                "duplicate function definition: {name} ({count} times)"
            ));
        }
        match calls.get(name).copied().unwrap_or(0) {
            0 => findings.push(format!("defined check is not registered in main(): {name}")),
            1 => {}
            count => findings.push(format!(
                "check called multiple times in main(): {name} ({count} times)"
            )),
        }
    }
    for name in calls.keys() {
        if !definitions.contains_key(name) {
            findings.push(format!("main() calls undefined check: {name}"));
        }
    }
    let tools = std::fs::read_dir(root.join("tools"))
        .map_err(|e| format!("cannot enumerate tools: {e}"))?;
    for entry in tools {
        let entry = entry.map_err(|e| format!("cannot read tools entry: {e}"))?;
        let name = entry
            .file_name()
            .into_string()
            .map_err(|_| "tools entry name is not UTF-8")?;
        if !name.starts_with("check-") || !name.ends_with(".py") || script.contains(&name) {
            continue;
        }
        let text = std::fs::read_to_string(entry.path())
            .map_err(|e| format!("cannot read tools/{name}: {e}"))?;
        let hint = text
            .lines()
            .take(3)
            .collect::<Vec<_>>()
            .join("\n")
            .to_lowercase();
        if hint.contains("drift check") || hint.contains("drift-check") {
            findings.push(format!("tools/{name} claims drift-check identity but is not referenced in 98-drift-checks.sh"));
        }
    }
    findings.sort();
    Ok(Report {
        check: CHECK.into(),
        ok: findings.is_empty(),
        could_not_run: None,
        summary: format!(
            "{} gate definitions registered exactly once in main()",
            definitions.len()
        ),
        findings,
    })
}

pub fn check(root: &Path) -> Report {
    inspect(root).unwrap_or_else(|error| Report {
        check: CHECK.into(),
        ok: false,
        could_not_run: Some(error),
        summary: String::new(),
        findings: Vec::new(),
    })
}

#[cfg(test)]
mod tests {
    #![allow(clippy::unwrap_used, clippy::expect_used, clippy::panic)]
    use super::*;
    use std::fs;

    const SOURCE: &str = "check_alpha() { :; }\nmain() {\n    check_alpha\n    check_beta || return 1\n}\ncheck_beta() { :; }\n_alias() {\n    check_alpha\n}\n";

    fn fixture(source: &str) -> tempfile::TempDir {
        let tree = tempfile::tempdir().unwrap();
        fs::create_dir_all(tree.path().join("automation")).unwrap();
        fs::create_dir(tree.path().join("tools")).unwrap();
        fs::write(tree.path().join("automation/98-drift-checks.sh"), source).unwrap();
        tree
    }

    #[test]
    fn helper_alias_is_outside_main_and_late_definition_is_registered() {
        let tree = fixture(SOURCE);
        let report = check(tree.path());
        assert_eq!(report.code(), 0, "{}", report.render_text());
        assert!(report.summary.starts_with("2 gate definitions"));
        assert_eq!(
            fs::read_to_string(tree.path().join("automation/98-drift-checks.sh")).unwrap(),
            SOURCE
        );
    }

    #[test]
    fn duplicate_definition_is_named_and_preserved() {
        let planted = format!("{SOURCE}check_alpha() {{ :; }}\n");
        let tree = fixture(&planted);
        let report = check(tree.path());
        assert_eq!(report.code(), 1);
        assert!(report
            .render_text()
            .contains("duplicate function definition: check_alpha"));
        assert_eq!(
            fs::read_to_string(tree.path().join("automation/98-drift-checks.sh")).unwrap(),
            planted
        );
        fs::write(tree.path().join("automation/98-drift-checks.sh"), SOURCE).unwrap();
        assert_eq!(check(tree.path()).code(), 0);
    }

    #[test]
    fn missing_repeated_and_undefined_registrations_are_distinct() {
        for (source, expected) in [
            (
                SOURCE.replace("    check_alpha\n", ""),
                "not registered in main(): check_alpha",
            ),
            (
                SOURCE.replacen("    check_alpha\n", "    check_alpha\n    check_alpha\n", 1),
                "called multiple times in main(): check_alpha",
            ),
            (
                SOURCE.replacen(
                    "    check_alpha\n",
                    "    check_unknown\n    check_alpha\n",
                    1,
                ),
                "main() calls undefined check: check_unknown",
            ),
        ] {
            let tree = fixture(&source);
            let report = check(tree.path());
            assert_eq!(report.code(), 1);
            assert!(
                report.render_text().contains(expected),
                "{}",
                report.render_text()
            );
        }
    }

    #[test]
    fn malformed_empty_and_unreadable_inputs_cannot_pass() {
        for source in [
            "",
            "main() {\n}\n",
            "check_alpha() { :; }\nmain() {\n check_alpha\n",
            "check_alpha() { :; }\nmain() {\n check_alpha\n_alias() {\n check_alpha\n}\n",
            "check_alpha() { :; }\nmain() {\n check_alpha\n}\nmain() {\n}\n",
        ] {
            assert_eq!(check(fixture(source).path()).code(), 2);
        }
        let tree = fixture(SOURCE);
        let path = tree.path().join("automation/98-drift-checks.sh");
        fs::write(&path, [0xff]).unwrap();
        assert_eq!(check(tree.path()).code(), 2);
        fs::remove_file(path).unwrap();
        assert_eq!(check(tree.path()).code(), 2);
    }

    #[test]
    fn unreferenced_tool_identity_is_named_and_restoration_passes() {
        let tree = fixture(SOURCE);
        let path = tree.path().join("tools/check-fixture.py");
        fs::write(&path, "# AI-hint: A drift check.\npass\n").unwrap();
        let report = check(tree.path());
        assert_eq!(report.code(), 1);
        assert!(report
            .render_text()
            .contains("tools/check-fixture.py claims drift-check identity"));
        fs::write(
            tree.path().join("automation/98-drift-checks.sh"),
            format!("# check-fixture.py\n{SOURCE}"),
        )
        .unwrap();
        assert_eq!(check(tree.path()).code(), 0);
        fs::write(&path, "# A general tool\npass\n").unwrap();
        fs::write(tree.path().join("automation/98-drift-checks.sh"), SOURCE).unwrap();
        assert_eq!(check(tree.path()).code(), 0);
    }

    #[test]
    fn absent_tools_and_invalid_tool_text_fail_closed() {
        let tree = fixture(SOURCE);
        fs::write(tree.path().join("tools/check-fixture.py"), [0xff]).unwrap();
        let report = check(tree.path());
        assert_eq!(report.code(), 2);
        assert!(report
            .render_text()
            .contains("cannot read tools/check-fixture.py"));
        fs::remove_file(tree.path().join("tools/check-fixture.py")).unwrap();
        fs::remove_dir(tree.path().join("tools")).unwrap();
        assert_eq!(check(tree.path()).code(), 2);
    }
}
