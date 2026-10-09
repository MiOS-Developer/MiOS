// AI-hint: Integration tests for mios-gate's law-enforcers check against synthetic trees -- a comment is never an enforcer.
// AI-related: src/mios-rs/mios-gate/src/laws.rs, usr/share/mios/mios.toml, automation/99-postcheck.sh

use std::fs;
use std::path::Path;
use std::process::Command;

fn bin() -> &'static str {
    env!("CARGO_BIN_EXE_mios-gate")
}

fn tree(dir: &Path, laws: &[(i64, &str, &str)], drift: &str, post: &str) {
    fs::create_dir_all(dir.join("automation")).unwrap();
    fs::create_dir_all(dir.join("usr/share/mios")).unwrap();
    fs::write(dir.join("automation/98-drift-checks.sh"), drift).unwrap();
    fs::write(dir.join("automation/99-postcheck.sh"), post).unwrap();
    let rows: String = laws
        .iter()
        .map(|(id, slug, by)| {
            format!("  {{ id = {id}, slug = \"{slug}\", enforced_by = \"{by}\" }},\n")
        })
        .collect();
    fs::write(
        dir.join("usr/share/mios/mios.toml"),
        format!("[laws]\nlaws = [\n{rows}]\n"),
    )
    .unwrap();
}

fn run(dir: &Path) -> (i32, String) {
    let out = Command::new(bin())
        .args(["law-enforcers", "--root"])
        .arg(dir)
        .output()
        .unwrap();
    let mut text = String::from_utf8_lossy(&out.stdout).to_string();
    text.push_str(&String::from_utf8_lossy(&out.stderr));
    (out.status.code().unwrap_or(-1), text)
}

const DRIFT: &str = "#!/usr/bin/env bash\ncheck_a() {\n    true\n}\ncheck_b() {\n    true\n}\n";
const POST: &str = "#!/usr/bin/env bash\ndie \"SLUG-ONE: broke\"\nexit 0\n";

#[test]
fn live_enforcement_on_both_sides_is_clean() {
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &[
            (1, "A", "98-drift-checks.sh:check_a"),
            (2, "B", "99-postcheck.sh:SLUG-ONE"),
        ],
        DRIFT,
        POST,
    );
    let (code, text) = run(d.path());
    assert_eq!(code, 0, "{text}");
    assert!(text.contains("1 drift-check function(s)"), "{text}");
    assert!(text.contains("1 postcheck marker(s)"), "{text}");
}

/// The exact shape task_986 found: the marker exists in the file, but only in a
/// comment placed after the script's terminating `exit 0`.
#[test]
fn a_marker_only_in_a_comment_after_exit_fails() {
    let d = tempfile::tempdir().unwrap();
    let post = "#!/usr/bin/env bash\ntrue\nexit 0\n\n# References for laws: item17\n";
    tree(
        d.path(),
        &[(11, "SECRETS", "99-postcheck.sh:item17")],
        DRIFT,
        post,
    );
    let (code, text) = run(d.path());
    assert_eq!(code, 1, "{text}");
    assert!(text.contains("only in a comment or after"), "{text}");
}

#[test]
fn a_marker_in_a_comment_before_exit_also_fails() {
    let d = tempfile::tempdir().unwrap();
    let post = "#!/usr/bin/env bash\n# item17 is handled below\ntrue\nexit 0\n";
    tree(
        d.path(),
        &[(11, "SECRETS", "99-postcheck.sh:item17")],
        DRIFT,
        post,
    );
    assert_eq!(run(d.path()).0, 1);
}

#[test]
fn a_marker_that_is_nowhere_fails_without_the_comment_alibi() {
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &[(11, "SECRETS", "99-postcheck.sh:NOWHERE")],
        DRIFT,
        POST,
    );
    let (code, text) = run(d.path());
    assert_eq!(code, 1, "{text}");
    assert!(!text.contains("only in a comment"), "{text}");
}

/// The old reader split on comma first and dropped any piece with no colon, so
/// a second enforcer in a list was never checked.
#[test]
fn a_bare_second_enforcer_inherits_its_file_and_is_checked() {
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &[(12, "BAKE", "98-drift-checks.sh:check_a,check_b")],
        DRIFT,
        POST,
    );
    let (code, text) = run(d.path());
    assert_eq!(code, 0, "{text}");
    assert!(text.contains("2 drift-check function(s)"), "{text}");

    tree(
        d.path(),
        &[(12, "BAKE", "98-drift-checks.sh:check_a,check_missing")],
        DRIFT,
        POST,
    );
    let (code, text) = run(d.path());
    assert_eq!(code, 1, "{text}");
    assert!(text.contains("check_missing"), "{text}");
}

#[test]
fn a_process_enforcer_is_declared_not_silently_dropped() {
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &[(15, "DOUBLE-REPO", "process:CLAUDE.md in both repos")],
        DRIFT,
        POST,
    );
    let (code, text) = run(d.path());
    assert_eq!(code, 0, "{text}");
    assert!(text.contains("1 declared process-enforced"), "{text}");
}

#[test]
fn an_unrecognised_enforcer_kind_is_a_finding() {
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &[(1, "A", "somewhere-else.sh:check_a")],
        DRIFT,
        POST,
    );
    let (code, text) = run(d.path());
    assert_eq!(code, 1, "{text}");
    assert!(text.contains("somewhere-else.sh"), "{text}");
}

#[test]
fn a_call_site_is_not_a_definition() {
    let d = tempfile::tempdir().unwrap();
    let drift = "#!/usr/bin/env bash\nmain() {\n    check_a\n}\n";
    tree(
        d.path(),
        &[(1, "A", "98-drift-checks.sh:check_a")],
        drift,
        POST,
    );
    assert_eq!(run(d.path()).0, 1);
}

#[test]
fn an_empty_register_or_a_missing_file_cannot_run() {
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), &[], DRIFT, POST);
    let (code, text) = run(d.path());
    assert_eq!(code, 2, "{text}");
    assert!(text.contains("nothing was checked"), "{text}");

    // The postcheck script absent must be cannot-run, not a pass: with it gone
    // every marker target is unverifiable.
    tree(
        d.path(),
        &[(1, "A", "98-drift-checks.sh:check_a")],
        DRIFT,
        POST,
    );
    fs::remove_file(d.path().join("automation/99-postcheck.sh")).unwrap();
    assert_eq!(run(d.path()).0, 2);
}
