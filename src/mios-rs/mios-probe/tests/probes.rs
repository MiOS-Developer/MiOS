// AI-hint: Integration tests for mios-probe: every threshold comes from SSOT, and an unreadable input never reads as ready.
// AI-related: src/mios-rs/mios-probe/src/main.rs, usr/share/mios/mios.toml

use std::fs;
use std::path::Path;
use std::process::Command;

fn bin() -> &'static str {
    env!("CARGO_BIN_EXE_mios-probe")
}

fn tree(dir: &Path, toml: &str) {
    fs::create_dir_all(dir.join("usr/share/mios")).unwrap();
    fs::write(dir.join("usr/share/mios/mios.toml"), toml).unwrap();
    fs::write(dir.join("VERSION"), "9.9.9\n").unwrap();
}

const FULL: &str = r#"
[preflight]
min_windows_build = 22000
min_disk_free_gb = 1
min_ram_gb = 1
require_virt = true
require_admin = true

[preflight.build]
required_tools = ["sh"]
min_disk_free_gb = 1

[build.images]
order = ["os"]

[build.images.os]
containerfile = "Containerfile"
"#;

fn run(dir: &Path, args: &[&str]) -> (i32, String) {
    let out = Command::new(bin())
        .args(args)
        .arg("--root")
        .arg(dir)
        .arg("--no-color")
        // The caller's own user tier must not leak into a fixture tree.
        .env("MIOS_USER_TOML", dir.join("no-user/mios.toml"))
        .env_remove("MIOS_VENDOR_TOML")
        .env_remove("MIOS_HOST_TOML")
        .output()
        .unwrap();
    let mut s = String::from_utf8_lossy(&out.stdout).to_string();
    s.push_str(&String::from_utf8_lossy(&out.stderr));
    (out.status.code().unwrap_or(-1), s)
}

#[test]
fn a_satisfied_build_host_is_clean() {
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), FULL);
    fs::write(d.path().join("Containerfile"), "FROM scratch\n").unwrap();
    let (code, out) = run(d.path(), &["build"]);
    assert_eq!(0, code, "{out}");
    assert!(out.contains("All pre-flight checks passed"), "{out}");
    assert!(
        out.contains("v9.9.9"),
        "the VERSION file is the title's source: {out}"
    );
}

#[test]
fn a_host_tier_override_is_the_threshold_probed() {
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), FULL);
    fs::write(d.path().join("Containerfile"), "FROM scratch\n").unwrap();
    fs::create_dir_all(d.path().join("etc/mios")).unwrap();
    fs::write(
        d.path().join("etc/mios/mios.toml"),
        "[preflight.build]\nrequired_files = [\"host-only-file\"]\n",
    )
    .unwrap();
    let (code, out) = run(d.path(), &["build"]);
    assert_eq!(1, code, "{out}");
    assert!(out.contains("host-only-file missing"), "{out}");
}

#[test]
fn a_missing_required_file_fails_and_names_it() {
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), FULL);
    let (code, out) = run(d.path(), &["build"]);
    assert_eq!(1, code, "{out}");
    assert!(out.contains("Containerfile missing"), "{out}");
    assert!(out.contains("1 error(s)"), "{out}");
}

#[test]
fn each_image_target_containerfile_is_required_from_its_one_declaration() {
    // Renaming the os target's Containerfile moves the requirement with it;
    // nothing else names the file.
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &FULL.replace(
            "containerfile = \"Containerfile\"",
            "containerfile = \"Containerfile.os\"",
        ),
    );
    fs::write(d.path().join("Containerfile"), "FROM scratch\n").unwrap();
    let (code, out) = run(d.path(), &["build"]);
    assert_eq!(1, code, "{out}");
    assert!(out.contains("Containerfile.os missing"), "{out}");
}

#[test]
fn the_required_tool_list_comes_from_ssot_not_from_code() {
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &FULL.replace(
            r#"required_tools = ["sh"]"#,
            r#"required_tools = ["definitely-not-a-real-tool"]"#,
        ),
    );
    fs::write(d.path().join("Containerfile"), "FROM scratch\n").unwrap();
    let (code, out) = run(d.path(), &["build"]);
    assert_eq!(1, code, "{out}");
    assert!(
        out.contains("definitely-not-a-real-tool not found"),
        "{out}"
    );
}

#[test]
fn the_disk_floor_comes_from_ssot_not_from_code() {
    // The defect this crate exists to fix: the shell probe compared against a
    // literal 20 while [preflight].min_disk_free_gb said 280.
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        &FULL.replace("min_disk_free_gb = 1\n", "min_disk_free_gb = 999999\n"),
    );
    fs::write(d.path().join("Containerfile"), "FROM scratch\n").unwrap();
    let (_code, out) = run(d.path(), &["build"]);
    assert!(
        out.contains("[WARN]"),
        "an unreachable floor must warn: {out}"
    );
}

#[test]
fn a_missing_ssot_cannot_run_and_never_reads_as_ready() {
    let d = tempfile::tempdir().unwrap();
    let (code, out) = run(d.path(), &["build"]);
    assert_eq!(2, code, "{out}");
    assert!(out.contains("is missing"), "{out}");
}

#[test]
fn a_malformed_ssot_cannot_run() {
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), "[[[not toml");
    let (code, out) = run(d.path(), &["build"]);
    assert_eq!(2, code, "{out}");
    assert!(out.contains("did not parse"), "{out}");
}

#[test]
fn a_missing_threshold_cannot_run_rather_than_skipping_it() {
    let d = tempfile::tempdir().unwrap();
    tree(
        d.path(),
        "[preflight]\n[preflight.build]\nrequired_tools = []\n",
    );
    let (code, out) = run(d.path(), &["build"]);
    assert_eq!(2, code, "{out}");
    assert!(out.contains("min_disk_free_gb is missing"), "{out}");
}

#[test]
fn a_platform_inapplicable_threshold_says_so_instead_of_passing() {
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), FULL);
    let (_code, out) = run(d.path(), &["host"]);
    assert!(out.contains("[N/A]") || out.contains("[WARN]"), "{out}");
    assert!(out.contains("Windows build"), "{out}");
    // The point: it must never be silently absent from the report.
    assert!(out.contains("Virtualization"), "{out}");
    assert!(out.contains("Administrator"), "{out}");
}

#[test]
fn json_carries_the_same_verdict_per_probe() {
    let d = tempfile::tempdir().unwrap();
    tree(d.path(), FULL);
    let (code, out) = run(d.path(), &["build", "--format", "json"]);
    assert_eq!(1, code, "{out}");
    let v: serde_json::Value = serde_json::from_str(out.trim()).expect("valid JSON");
    assert_eq!("preflight", v["probe"]);
    assert_eq!("failed", v["status"]);
    let probes = v["probes"].as_array().unwrap();
    assert!(probes
        .iter()
        .any(|p| p["key"] == "file.Containerfile" && p["verdict"] == "fail"));
}

#[test]
fn an_unknown_probe_set_cannot_run() {
    let out = Command::new(bin()).arg("no-such-set").output().unwrap();
    assert_eq!(Some(2), out.status.code());
}
