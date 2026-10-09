// AI-hint: Integration tests for mios-gate's canonical-bools check -- exit-code and diagnostic contract over fixture trees (task_953 unit 2).
// AI-related: src/mios-rs/mios-gate/src/canonical_bools.rs, tools/drift-checks.py, automation/98-drift-checks.sh

use std::fs;
use std::path::Path;
use std::process::Command;

fn bin() -> &'static str {
    env!("CARGO_BIN_EXE_mios-gate")
}

fn fixture(dir: &Path, verbs_toml: &str) {
    fs::create_dir_all(dir.join("usr/share/mios")).unwrap();
    fs::write(
        dir.join("usr/share/mios/mios.toml"),
        format!("[verbs]\n{verbs_toml}"),
    )
    .unwrap();
}

fn run(dir: &Path) -> (i32, String) {
    let out = Command::new(bin())
        .args(["canonical-bools", "--root"])
        .arg(dir)
        .output()
        .unwrap();
    let mut text = String::from_utf8_lossy(&out.stdout).to_string();
    text.push_str(&String::from_utf8_lossy(&out.stderr));
    (out.status.code().unwrap_or(-1), text)
}

#[test]
fn canonical_verbs_exit_zero() {
    let dir = tempfile::tempdir().unwrap();
    fixture(
        dir.path(),
        "[verbs.deploy]\nhidden = true\nsensitive = false\n[verbs.deploy.params.force]\nrequired = false\ntype = \"boolean\"\ndefault = true\n",
    );
    let (code, text) = run(dir.path());
    assert_eq!(code, 0, "output: {text}");
}

#[test]
fn stringly_hidden_exits_one_and_names_the_verb() {
    let dir = tempfile::tempdir().unwrap();
    fixture(dir.path(), "[verbs.legacy]\nhidden = \"yes\"\n");
    let (code, text) = run(dir.path());
    assert_eq!(code, 1, "output: {text}");
    assert!(
        text.contains("verb 'legacy'") && text.contains("hidden"),
        "diagnostic must name the verb and field: {text}"
    );
}

#[test]
fn stringly_required_param_exits_one() {
    let dir = tempfile::tempdir().unwrap();
    fixture(
        dir.path(),
        "[verbs.build]\n[verbs.build.params.tag]\nrequired = \"true\"\n",
    );
    let (code, text) = run(dir.path());
    assert_eq!(code, 1, "output: {text}");
    assert!(
        text.contains("param 'tag'") && text.contains("required"),
        "diagnostic must name the param and field: {text}"
    );
}

#[test]
fn boolean_default_must_be_bool_when_typed_boolean() {
    let dir = tempfile::tempdir().unwrap();
    fixture(
        dir.path(),
        "[verbs.scan]\n[verbs.scan.params.deep]\ntype = \"boolean\"\ndefault = \"1\"\n",
    );
    let (code, text) = run(dir.path());
    assert_eq!(code, 1, "output: {text}");
    assert!(
        text.contains("default boolean") && text.contains("param 'deep'"),
        "output: {text}"
    );
}

#[test]
fn non_boolean_default_is_not_flagged() {
    let dir = tempfile::tempdir().unwrap();
    fixture(
        dir.path(),
        "[verbs.pull]\n[verbs.pull.params.ref]\ntype = \"string\"\ndefault = \"latest\"\n",
    );
    let (code, text) = run(dir.path());
    assert_eq!(code, 0, "output: {text}");
}

#[test]
fn missing_ssot_is_a_violation_not_a_pass() {
    let dir = tempfile::tempdir().unwrap();
    fs::create_dir_all(dir.path()).unwrap();
    let (code, text) = run(dir.path());
    assert_eq!(code, 1, "output: {text}");
    assert!(
        text.contains("required SSOT file is missing"),
        "output: {text}"
    );
}
