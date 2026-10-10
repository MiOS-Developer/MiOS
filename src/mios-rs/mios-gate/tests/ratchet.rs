// AI-hint: Integration tests for mios-gate's ratchet-direction check against throwaway git repos -- the HEAD comparison is the whole behaviour and cannot be unit-tested.
// AI-related: src/mios-rs/mios-gate/src/ratchet.rs, usr/share/mios/mios.toml

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

/// A repo whose HEAD holds `head_toml`, with `work_toml` in the worktree.
fn repo(dir: &Path, head_toml: &str, work_toml: &str) {
    fs::create_dir_all(dir.join("usr/share/mios")).unwrap();
    let ssot = dir.join("usr/share/mios/mios.toml");
    fs::write(&ssot, head_toml).unwrap();
    git(dir, &["init", "-q"]);
    git(dir, &["config", "user.email", "t@example.invalid"]);
    // A caller's global commit signing must not reach the fixture.
    git(dir, &["config", "commit.gpgsign", "false"]);
    git(dir, &["config", "user.name", "t"]);
    git(dir, &["add", "-A"]);
    git(dir, &["commit", "-qm", "base"]);
    fs::write(&ssot, work_toml).unwrap();
}

fn run(dir: &Path) -> (i32, String) {
    // CI exports MIOS_RATCHET_BASE for the real repo; the fixture repo must use its own merge base.
    let out = Command::new(bin())
        .args(["ratchet-direction", "--root"])
        .arg(dir)
        // CI exports the PR base for the real repo; a throwaway repo must use its own HEAD.
        .env_remove("MIOS_RATCHET_BASE")
        .output()
        .unwrap();
    let mut text = String::from_utf8_lossy(&out.stdout).to_string();
    text.push_str(&String::from_utf8_lossy(&out.stderr));
    (out.status.code().unwrap_or(-1), text)
}

const BASE: &str = "[docs]\nmax_a = 5\nmax_b = 9\n";

#[test]
fn total_file_migration_counts_the_immutable_tree_and_still_rejects_growth() {
    let d = tempfile::tempdir().unwrap();
    fs::write(d.path().join("generated-output"), "tracked too\n").unwrap();
    let old = "[legibility]\nmax_tracked_files=1\n[drift.generated_ceilings]\n\"legibility.max_tracked_files\"=\"legacy generated budget\"\n";
    let total =
        "[legibility]\nmax_tracked_files=2\n[legibility.file_budget]\ntarget_tracked_files=500\n";
    repo(d.path(), old, total);
    let (code, text) = run(d.path());
    assert_eq!(code, 0, "{text}");
    assert!(
        text.contains("TOTAL file baseline measured from Git: 2"),
        "{text}"
    );

    // A new tracked output cannot inflate the immutable baseline.
    fs::write(d.path().join("new-output"), "new\n").unwrap();
    git(d.path(), &["add", "new-output"]);
    fs::write(
        d.path().join("usr/share/mios/mios.toml"),
        total.replace("files=2", "files=3"),
    )
    .unwrap();
    let (code, text) = run(d.path());
    assert_eq!(code, 1, "{text}");
    assert!(text.contains("INCREASED from 2 to 3"), "{text}");

    // Reintroducing the generated exemption must not silence TOTAL growth.
    fs::write(
        d.path().join("usr/share/mios/mios.toml"),
        format!("{total}[drift.generated_ceilings]\n\"legibility.max_tracked_files\"=\"bypass\"\n"),
    )
    .unwrap();
    let (code, text) = run(d.path());
    assert_eq!(code, 1, "{text}");
    assert!(
        text.contains("cannot be a generated-budget exemption"),
        "{text}"
    );
}

#[test]
fn a_total_baseline_or_nonexempt_legacy_ceiling_never_gets_remeasured() {
    for base in [
        "[legibility]\nmax_tracked_files=1\n",
        "[legibility]\nmax_tracked_files=1\n[legibility.file_budget]\ntarget_tracked_files=500\n",
    ] {
        let d = tempfile::tempdir().unwrap();
        fs::write(d.path().join("extra"), "tracked\n").unwrap();
        repo(d.path(), base, "[legibility]\nmax_tracked_files=2\n[legibility.file_budget]\ntarget_tracked_files=500\n");
        let (code, text) = run(d.path());
        assert_eq!(code, 1, "{text}");
        assert!(text.contains("INCREASED from 1 to 2"), "{text}");
        assert!(!text.contains("TOTAL file baseline measured"), "{text}");
    }
}

#[test]
fn a_lowered_ceiling_is_clean_and_a_raised_one_is_not() {
    let d = tempfile::tempdir().unwrap();
    repo(d.path(), BASE, "[docs]\nmax_a = 4\nmax_b = 9\n");
    let (code, text) = run(d.path());
    assert_eq!(code, 0, "{text}");

    let d2 = tempfile::tempdir().unwrap();
    repo(d2.path(), BASE, "[docs]\nmax_a = 6\nmax_b = 9\n");
    let (code, text) = run(d2.path());
    assert_eq!(code, 1, "{text}");
    assert!(text.contains("docs.max_a"), "{text}");
}

#[test]
fn a_generated_budget_may_rise_only_when_itemised_with_a_reason() {
    let raised = "[docs]\nmax_a = 6\nmax_b = 9\n";
    let exempt_ok = format!(
        "{raised}[drift.generated_ceilings]\n\"docs.max_a\" = \"emitted by a generator\"\n"
    );
    let exempt_bare = format!("{raised}[drift.generated_ceilings]\n\"docs.max_a\" = \"\"\n");

    let d = tempfile::tempdir().unwrap();
    repo(d.path(), BASE, &exempt_ok);
    let (code, text) = run(d.path());
    assert_eq!(code, 0, "{text}");
    assert!(
        text.contains("1 declared generated budget(s) of which 1 rose"),
        "{text}"
    );

    let d2 = tempfile::tempdir().unwrap();
    repo(d2.path(), BASE, &exempt_bare);
    let (code, text) = run(d2.path());
    assert_eq!(code, 1, "{text}");
    assert!(text.contains("carries no `reason`"), "{text}");
}

#[test]
fn an_exemption_for_something_that_is_not_a_ceiling_is_a_finding() {
    let work = format!("{BASE}[drift.generated_ceilings]\n\"docs.no_such\" = \"stale\"\n");
    let d = tempfile::tempdir().unwrap();
    repo(d.path(), BASE, &work);
    let (code, text) = run(d.path());
    assert_eq!(code, 1, "{text}");
    assert!(text.contains("is not a ceiling"), "{text}");
}

/// Both guards the predecessor carried, because a comparison over an empty set
/// is true for every input.
#[test]
fn an_empty_ceiling_set_on_either_side_cannot_run() {
    let d = tempfile::tempdir().unwrap();
    repo(d.path(), BASE, "[docs]\nenabled = true\n");
    assert_eq!(run(d.path()).0, 2);

    let d2 = tempfile::tempdir().unwrap();
    repo(d2.path(), "[docs]\nenabled = true\n", BASE);
    assert_eq!(run(d2.path()).0, 2);
}

#[test]
fn a_tree_that_is_not_a_checkout_has_no_head_to_compare() {
    let d = tempfile::tempdir().unwrap();
    fs::create_dir_all(d.path().join("usr/share/mios")).unwrap();
    fs::write(d.path().join("usr/share/mios/mios.toml"), BASE).unwrap();
    let (code, text) = run(d.path());
    assert_eq!(code, 0, "{text}");
    assert!(text.contains("not a checkout"), "{text}");
}

#[test]
fn a_head_ssot_that_does_not_parse_cannot_run() {
    let d = tempfile::tempdir().unwrap();
    repo(d.path(), "[docs\nmax_a = 5\n", BASE);
    let (code, text) = run(d.path());
    assert_eq!(code, 2, "{text}");
    assert!(text.contains("does not parse"), "{text}");
}
