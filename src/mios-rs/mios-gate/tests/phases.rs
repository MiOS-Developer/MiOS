// AI-hint: Integration tests for mios-gate's phase-registry check against synthetic automation trees.
// AI-related: src/mios-rs/mios-gate/src/phases.rs, usr/share/mios/mios.toml, automation/build.sh

use std::fs;
use std::path::Path;
use std::process::Command;

fn bin() -> &'static str {
    env!("CARGO_BIN_EXE_mios-gate")
}

/// A minimal tree: the phase scripts on disk, the [build.phases] register, and
/// nothing else the check reads.
fn tree(dir: &Path, on_disk: &[&str], listed: &[(&str, &str)], exceptions: &[&str], ceiling: i64) {
    fs::create_dir_all(dir.join("automation")).unwrap();
    fs::create_dir_all(dir.join("usr/share/mios")).unwrap();
    for s in on_disk {
        fs::write(
            dir.join("automation").join(s),
            "#!/usr/bin/env bash\ntrue\n",
        )
        .unwrap();
    }
    let rows: String = listed
        .iter()
        .map(|(ord, script)| {
            format!(
                "  {{ ordinal = \"{ord}\", name = \"x\", script = \"{script}\", fatal = true }},\n"
            )
        })
        .collect();
    let exc: String = exceptions.iter().map(|e| format!("  \"{e}\",\n")).collect();
    fs::write(
        dir.join("usr/share/mios/mios.toml"),
        format!(
            "[build.phases]\nunregistered = [\n{exc}]\nmax_unregistered = {ceiling}\nlist = [\n{rows}]\n"
        ),
    )
    .unwrap();
}

fn run(dir: &Path) -> (i32, String) {
    let out = Command::new(bin())
        .args(["phase-registry", "--root"])
        .arg(dir)
        .output()
        .unwrap();
    let mut text = String::from_utf8_lossy(&out.stdout).to_string();
    text.push_str(&String::from_utf8_lossy(&out.stderr));
    (out.status.code().unwrap_or(-1), text)
}

#[test]
fn a_fully_registered_tree_is_clean() {
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &["01-a.sh", "02-b.sh"],
        &[("01", "01-a.sh"), ("02", "02-b.sh")],
        &[],
        0,
    );
    let (code, out) = run(d.path());
    assert_eq!(code, 0, "{out}");
}

/// The defect this check exists for: a script on disk that build.sh never runs.
#[test]
fn an_unregistered_script_on_disk_fails() {
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &["01-a.sh", "55-native-build.sh"],
        &[("01", "01-a.sh")],
        &[],
        0,
    );
    let (code, out) = run(d.path());
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("55-native-build.sh"), "{out}");
}

/// Naming it on the register is the sanctioned escape, and only then.
#[test]
fn a_registered_exception_is_accepted() {
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &["01-a.sh", "55-native-build.sh"],
        &[("01", "01-a.sh")],
        &["55-native-build.sh"],
        1,
    );
    let (code, out) = run(d.path());
    assert_eq!(code, 0, "{out}");
}

/// Raising the ceiling must never absorb a plant: the file is still missing
/// from the register, which is a finding on its own.
#[test]
fn a_raised_ceiling_does_not_absorb_an_unregistered_script() {
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &["01-a.sh", "55-native-build.sh"],
        &[("01", "01-a.sh")],
        &[],
        999,
    );
    let (code, out) = run(d.path());
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("NOT in [build.phases].list"), "{out}");
}

/// Shrink-only means the ceiling tracks the measurement downward.
#[test]
fn a_ceiling_above_the_measurement_is_itself_a_finding() {
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), &["01-a.sh"], &[("01", "01-a.sh")], &[], 3);
    let (code, out) = run(d.path());
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("lower the ceiling"), "{out}");
}

/// build.sh drops a listed-but-absent script with no else branch, so the
/// pipeline runs one stage short and exits 0.
#[test]
fn a_registered_script_missing_from_disk_fails() {
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &["01-a.sh"],
        &[("01", "01-a.sh"), ("38", "38-selinuxx.sh")],
        &[],
        0,
    );
    let (code, out) = run(d.path());
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("38-selinuxx.sh"), "{out}");
}

#[test]
fn an_ordinal_that_disagrees_with_its_filename_fails() {
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), &["01-a.sh"], &[("99", "01-a.sh")], &[], 0);
    let (code, out) = run(d.path());
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("does not match"), "{out}");
}

/// A stale exception is debt nobody is paying: it must be dropped, not left.
#[test]
fn an_exception_for_a_registered_script_fails() {
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &["01-a.sh"],
        &[("01", "01-a.sh")],
        &["01-a.sh"],
        1,
    );
    let (code, out) = run(d.path());
    assert_eq!(code, 1, "{out}");
}

/// Empty-Set Pass guards. An absent list, an empty list, a missing ceiling and
/// an empty automation/ must each be "could not run", never "clean".
#[test]
fn an_empty_or_absent_register_cannot_run() {
    for body in [
        "[build.phases]\nmax_unregistered = 0\nlist = []\n",
        "[build.phases]\nmax_unregistered = 0\n",
        "[build.phases]\nlist = [\n  { ordinal = \"01\", name = \"x\", script = \"01-a.sh\" },\n]\n",
        "[other]\nk = 1\n",
    ] {
        let d = tempfile::tempdir().unwrap();
        fs::create_dir_all(d.path().join("automation")).unwrap();
        fs::create_dir_all(d.path().join("usr/share/mios")).unwrap();
        fs::write(d.path().join("automation/01-a.sh"), "true\n").unwrap();
        fs::write(d.path().join("usr/share/mios/mios.toml"), body).unwrap();
        let (code, out) = run(d.path());
        assert_eq!(code, 2, "body {body:?} should not run: {out}");
    }
}

#[test]
fn an_automation_dir_with_no_phase_scripts_cannot_run() {
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), &[], &[("01", "01-a.sh")], &[], 0);
    let (code, out) = run(d.path());
    assert_eq!(code, 2, "{out}");
    assert!(out.contains("nothing was compared"), "{out}");
}

#[test]
fn a_missing_ssot_or_automation_dir_cannot_run() {
    let d = tempfile::tempdir().unwrap();
    let (code, _) = run(d.path());
    assert_eq!(code, 2);
}

/// phase-ratchet: `scripts` on disk against `[build.ratchet].max_phase_scripts`.
fn ratchet(dir: &Path, scripts: &[&str], ceiling: Option<i64>) -> (i32, String) {
    fs::create_dir_all(dir.join("automation")).unwrap();
    fs::create_dir_all(dir.join("usr/share/mios")).unwrap();
    for s in scripts {
        fs::write(dir.join("automation").join(s), "true\n").unwrap();
    }
    let body = ceiling.map_or("[other]\nk = 1\n".into(), |c| {
        format!("[build.ratchet]\nmax_phase_scripts = {c}\n")
    });
    fs::write(dir.join("usr/share/mios/mios.toml"), body).unwrap();
    let out = Command::new(bin())
        .args(["phase-ratchet", "--root"])
        .arg(dir)
        .output()
        .unwrap();
    let mut text = String::from_utf8_lossy(&out.stdout).to_string();
    text.push_str(&String::from_utf8_lossy(&out.stderr));
    (out.status.code().unwrap_or(-1), text)
}

/// Both directions of the ratchet, and the exact fit between them. A non-phase
/// name (lib-, no ordinal) is not counted.
#[test]
fn the_phase_ratchet_fails_above_and_below_its_ceiling() {
    let two = ["01-a.sh", "02-b.sh", "lint-x.sh"];
    let (code, out) = ratchet(tempfile::tempdir().unwrap().path(), &two, Some(2));
    assert_eq!(code, 0, "{out}");
    let (code, out) = ratchet(tempfile::tempdir().unwrap().path(), &two, Some(1));
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("fold a phase"), "{out}");
    let (code, out) = ratchet(tempfile::tempdir().unwrap().path(), &two, Some(4));
    assert_eq!(code, 1, "{out}");
    assert!(out.contains("lower the ceiling to 2"), "{out}");
}

/// An absent ceiling is not a default of 71, and no scripts is not zero debt.
#[test]
fn the_phase_ratchet_cannot_run_without_a_ceiling_or_a_subject() {
    let (code, out) = ratchet(tempfile::tempdir().unwrap().path(), &["01-a.sh"], None);
    assert_eq!(code, 2, "{out}");
    let (code, out) = ratchet(tempfile::tempdir().unwrap().path(), &[], Some(0));
    assert_eq!(code, 2, "{out}");
}
