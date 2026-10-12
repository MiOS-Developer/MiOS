// AI-hint: Integration tests for mios-gate's rust-categories check -- the binary's exit-code and diagnostic contract over throwaway git repos, per the tests/<check>.rs convention.
// AI-related: src/mios-rs/mios-gate/src/rust_categories.rs, usr/share/mios/mios.toml, tests/drift-gate-negatives.sh

use std::fs;
use std::path::Path;
use std::process::Command;

fn bin() -> &'static str {
    env!("CARGO_BIN_EXE_mios-gate")
}

fn git(dir: &Path, args: &[&str]) {
    let out = Command::new("git")
        .arg("-C")
        .arg(dir)
        .args(args)
        .output()
        .expect("git must be available for these tests");
    assert!(
        out.status.success(),
        "git {args:?}: {}",
        String::from_utf8_lossy(&out.stderr)
    );
}

const VALID: &str = "[rust.categories]\nbinaries = [\"mios-serve\"]\nmax_unowned = 0\nuniverse = [\"usr/libexec/mios/\"]\n[rust.categories.owners]\nport-lane = [\"serve\"]\n[rust.categories.roles]\nservices = [\"serve\"]\n[rust.categories.serve]\nbinary = \"mios-serve\"\nscope = [\"usr/libexec/mios/db/*.py\"]\n[build.native.categories.services]\ninstall_dir = \"/usr/libexec/mios\"\n";

fn repo(dir: &Path, toml: &str) {
    fs::create_dir_all(dir.join("usr/share/mios")).unwrap();
    fs::create_dir_all(dir.join("usr/libexec/mios/db")).unwrap();
    fs::create_dir_all(dir.join("tools/native/mios-serve")).unwrap();
    fs::write(
        dir.join("tools/native/mios-serve/Cargo.toml"),
        "[package]\n",
    )
    .unwrap();
    fs::write(dir.join("usr/libexec/mios/db/backup.py"), "#!/bin/sh\n").unwrap();
    fs::write(dir.join("usr/share/mios/mios.toml"), toml).unwrap();
    git(dir, &["init", "-q"]);
    git(dir, &["config", "user.email", "t@example.invalid"]);
    git(dir, &["config", "commit.gpgsign", "false"]);
    git(dir, &["config", "user.name", "t"]);
    git(dir, &["add", "-A"]);
    git(dir, &["commit", "-qm", "base"]);
}

fn run(dir: &Path) -> (i32, String) {
    let out = Command::new(bin())
        .args(["rust-categories", "--root"])
        .arg(dir)
        .output()
        .unwrap();
    let mut text = String::from_utf8_lossy(&out.stdout).to_string();
    text.push_str(&String::from_utf8_lossy(&out.stderr));
    (out.status.code().unwrap_or(-1), text)
}

#[test]
fn valid_registry_exits_zero() {
    let dir = tempfile::tempdir().unwrap();
    repo(dir.path(), VALID);
    let (code, text) = run(dir.path());
    assert_eq!(code, 0, "output: {text}");
    assert!(text.contains("porting-owned"), "summary missing: {text}");
}

#[test]
fn ownerless_category_exits_one_and_names_it() {
    let dir = tempfile::tempdir().unwrap();
    // serve keeps its owner; a second porting category arrives without one.
    let toml = format!("{VALID}\n[rust.categories.orphan]\nbinary = \"mios-serve\"\n");
    repo(dir.path(), &toml);
    let (code, text) = run(dir.path());
    assert_eq!(code, 1, "output: {text}");
    assert!(
        text.contains("'orphan' has no owner"),
        "diagnostic must name the planted defect: {text}"
    );
}

#[test]
fn phantom_replaces_exits_one_and_names_the_script() {
    let dir = tempfile::tempdir().unwrap();
    let toml = VALID.replace(
        "scope = [\"usr/libexec/mios/db/*.py\"]",
        "scope = [\"usr/libexec/mios/db/*.py\"]\nreplaces = [\"usr/libexec/mios/db/backup.py\"]",
    );
    repo(dir.path(), &toml);
    let (code, text) = run(dir.path());
    assert_eq!(code, 1, "output: {text}");
    assert!(
        text.contains("backup.py") && text.contains("still exists on disk"),
        "diagnostic must name the still-present script: {text}"
    );
}

#[test]
fn missing_registry_exits_one_not_two() {
    let dir = tempfile::tempdir().unwrap();
    repo(dir.path(), "meta = 1\n");
    let (code, text) = run(dir.path());
    // A deleted registry is a violation, not could-not-run: the gate must not
    // pass vacuously over an SSOT that lost its port plan.
    assert_eq!(code, 1, "output: {text}");
    assert!(text.contains("table is missing"), "output: {text}");
}
