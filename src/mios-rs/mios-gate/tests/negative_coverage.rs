// AI-hint: Integration tests for mios-gate's negative-coverage check -- the exit-code and diagnostic contract over fixture trees, per the tests/<check>.rs convention (task_953 unit 1).
// AI-related: src/mios-rs/mios-gate/src/negative_coverage.rs, tools/drift-checks.py, tests/drift-gate-negatives.sh

use std::fs;
use std::path::Path;
use std::process::Command;

fn bin() -> &'static str {
    env!("CARGO_BIN_EXE_mios-gate")
}

fn fixture(dir: &Path, dispatched: &[&str], covered: &[&str], exempt: &[&str]) {
    fs::create_dir_all(dir.join("automation")).unwrap();
    fs::create_dir_all(dir.join("tests")).unwrap();
    fs::create_dir_all(dir.join("usr/share/mios")).unwrap();

    let mut main_body = String::from("main() {\n");
    for c in dispatched {
        main_body.push_str(&format!("    {c}\n"));
    }
    main_body.push_str("}\n");
    fs::write(dir.join("automation/98-drift-checks.sh"), main_body).unwrap();

    let mut neg = String::from("#!/usr/bin/env bash\n");
    for c in covered {
        neg.push_str(&format!("test_x() {{ {c}; }}\n"));
    }
    fs::write(dir.join("tests/drift-gate-negatives.sh"), neg).unwrap();

    let mut toml = String::from("[testing.negative_coverage_exempt]\nexempt = [\n");
    for e in exempt {
        toml.push_str(&format!("  \"{e}\",\n"));
    }
    toml.push_str("]\n");
    fs::write(dir.join("usr/share/mios/mios.toml"), toml).unwrap();
}

fn run(dir: &Path) -> (i32, String) {
    let out = Command::new(bin())
        .args(["negative-coverage", "--root"])
        .arg(dir)
        .output()
        .unwrap();
    let mut text = String::from_utf8_lossy(&out.stdout).to_string();
    text.push_str(&String::from_utf8_lossy(&out.stderr));
    (out.status.code().unwrap_or(-1), text)
}

#[test]
fn fully_covered_dispatches_exit_zero() {
    let dir = tempfile::tempdir().unwrap();
    fixture(
        dir.path(),
        &["check_alpha", "check_beta"],
        &["check_alpha", "check_beta"],
        &[],
    );
    let (code, text) = run(dir.path());
    assert_eq!(code, 0, "output: {text}");
}

#[test]
fn uncovered_dispatch_exits_one_and_names_it() {
    let dir = tempfile::tempdir().unwrap();
    fixture(
        dir.path(),
        &["check_alpha", "check_orphan"],
        &["check_alpha"],
        &[],
    );
    let (code, text) = run(dir.path());
    assert_eq!(code, 1, "output: {text}");
    assert!(
        text.contains("'check_orphan'"),
        "diagnostic must name the uncovered check: {text}"
    );
    assert!(
        text.contains("lacking negative test coverage"),
        "output: {text}"
    );
}

#[test]
fn exemption_covers_a_dispatch_without_a_negative() {
    let dir = tempfile::tempdir().unwrap();
    fixture(
        dir.path(),
        &["check_alpha", "check_special"],
        &["check_alpha"],
        &["check_special"],
    );
    let (code, text) = run(dir.path());
    assert_eq!(code, 0, "output: {text}");
}

#[test]
fn missing_deliverable_is_a_violation_not_a_pass() {
    let dir = tempfile::tempdir().unwrap();
    fs::create_dir_all(dir.path().join("automation")).unwrap();
    fs::write(
        dir.path().join("automation/98-drift-checks.sh"),
        "main() {\n}\n",
    )
    .unwrap();
    // negatives + mios.toml absent.
    let (code, text) = run(dir.path());
    assert_eq!(code, 1, "output: {text}");
    assert!(
        text.contains("required SSOT file is missing"),
        "output: {text}"
    );
}
