// AI-hint: Two-sided integration test suite for mios-gen roadmap-index (ADR-0021, Law 14) -- hermetic: every case runs against a fixture copy of ROADMAP.md, because a test that checks the LIVE tree's sync state fails on any mid-edit working copy and duplicates what the drift gate already asserts.
// AI-doc: usr/share/doc/mios/manual/tools.md
// AI-related: tools/native/mios-gen/src/roadmap_index.rs, ROADMAP.md, automation/98-drift-checks.sh

use std::fs;
use std::path::PathBuf;
use std::process::Command;

fn get_repo_root() -> PathBuf {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
    manifest_dir
        .parent()
        .and_then(|p| p.parent())
        .and_then(|p| p.parent())
        .expect("Failed to find repo root from CARGO_MANIFEST_DIR")
        .to_path_buf()
}

fn bin() -> &'static str {
    env!("CARGO_BIN_EXE_mios-gen")
}

fn isolated_command(program: &str) -> Command {
    let mut command = Command::new(program);
    for (key, _) in std::env::vars_os().filter(|(key, _)| key.to_string_lossy().starts_with("GIT_"))
    {
        command.env_remove(key);
    }
    command
}

/// A throwaway root whose ROADMAP.md is a self-consistent copy of the real
/// one: render once against the fixture so its index agrees with its body,
/// then every --check below measures the binary, not the working tree.
/// roadmap-index validates workstream citations against the ADR corpus and
/// the SSOT, so those ride along with the copy.
fn fixture_root() -> tempfile::TempDir {
    let dir = tempfile::tempdir().expect("Failed to create fixture tempdir");
    let repo = get_repo_root();

    fs::copy(repo.join("ROADMAP.md"), dir.path().join("ROADMAP.md"))
        .expect("Failed to write fixture ROADMAP.md");

    let ssot_dest = dir.path().join("usr/share/mios");
    fs::create_dir_all(&ssot_dest).expect("Failed to create fixture SSOT dir");
    fs::copy(
        repo.join("usr/share/mios/mios.toml"),
        ssot_dest.join("mios.toml"),
    )
    .expect("Failed to copy fixture SSOT");

    let adr_src = repo.join("usr/share/doc/mios/adr");
    let adr_dest = dir.path().join("usr/share/doc/mios/adr");
    fs::create_dir_all(&adr_dest).expect("Failed to create fixture ADR dir");
    for entry in fs::read_dir(&adr_src).expect("Failed to read ADR corpus") {
        let entry = entry.expect("ADR dir entry");
        if entry.path().is_file() {
            fs::copy(entry.path(), adr_dest.join(entry.file_name()))
                .expect("Failed to copy ADR file");
        }
    }

    // roadmap-index shells out to `git ls-files` for its metrics; a throwaway
    // repo of the fixture satisfies it (the ratchet.rs fixtures do the same).
    for args in [
        ["init", "-q"].as_slice(),
        ["config", "user.email", "t@example.invalid"].as_slice(),
        ["config", "user.name", "t"].as_slice(),
        ["add", "-A"].as_slice(),
    ] {
        let out = isolated_command("git")
            .arg("-C")
            .arg(dir.path())
            .args(args)
            .output()
            .expect("git must be available for this fixture");
        assert!(
            out.status.success(),
            "git {args:?}: {}",
            String::from_utf8_lossy(&out.stderr)
        );
    }

    let out = isolated_command(bin())
        .args(["roadmap-index", "--root"])
        .arg(dir.path())
        .output()
        .expect("Failed to render fixture index");
    assert!(
        out.status.success(),
        "fixture render failed: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    dir
}

#[test]
fn test_roadmap_index_render_and_check() {
    let fixture = fixture_root();
    let roadmap_path = fixture.path().join("ROADMAP.md");

    // 1. Positive check on the self-consistent fixture
    let output = isolated_command(bin())
        .args(["roadmap-index", "--root"])
        .arg(fixture.path())
        .arg("--check")
        .output()
        .expect("Failed to execute mios-gen roadmap-index");

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);

    assert!(
        output.status.success(),
        "Expected clean exit 0 for roadmap-index check, got: {:?}\nstderr: {}",
        output.status.code(),
        stderr
    );
    assert!(
        stdout.contains("ROADMAP.md index is in sync"),
        "Expected sync confirmation message, got: {stdout}"
    );

    // 2. Negative check: mutate the fixture; no restorer needed, it is a copy
    let original = fs::read_to_string(&roadmap_path).expect("Failed to read fixture ROADMAP.md");
    let mutated = original.replace("- **Done**:", "- **Done**: 99999");
    assert_ne!(original, mutated, "Mutation must change content");
    fs::write(&roadmap_path, mutated).expect("Failed to write mutated ROADMAP.md");

    let output_neg = isolated_command(bin())
        .args(["roadmap-index", "--root"])
        .arg(fixture.path())
        .arg("--check")
        .output()
        .expect("Failed to execute mios-gen roadmap-index on mutated copy");

    let stderr_neg = String::from_utf8_lossy(&output_neg.stderr);
    assert!(
        !output_neg.status.success(),
        "Expected failure for mutated ROADMAP.md, got exit code 0"
    );
    assert!(
        stderr_neg.contains("DRIFT detected: ROADMAP.md index is stale"),
        "Expected drift error message, got: {stderr_neg}"
    );
}

#[test]
fn test_roadmap_index_json_format() {
    let fixture = fixture_root();

    let output = isolated_command(bin())
        .args(["--format", "json", "roadmap-index", "--root"])
        .arg(fixture.path())
        .arg("--check")
        .output()
        .expect("Failed to execute mios-gen --format json roadmap-index");

    assert!(
        output.status.success(),
        "Expected exit code 0, got: {:?}\nstderr: {}",
        output.status.code(),
        String::from_utf8_lossy(&output.stderr)
    );

    let stdout = String::from_utf8_lossy(&output.stdout);
    let parsed: serde_json::Value =
        serde_json::from_str(stdout.trim()).expect("Output must be valid JSON");
    assert_eq!(parsed["status"], "clean");
    assert_eq!(parsed["subcommand"], "roadmap-index");
    assert_eq!(parsed["target"], "ROADMAP.md");
    assert_eq!(parsed["violations"], 0);
}

#[test]
fn test_roadmap_index_negative_missing_root() {
    let temp_empty = tempfile::tempdir().expect("Failed to create empty tempdir");

    let output = isolated_command(bin())
        .args(["roadmap-index", "--root"])
        .arg(temp_empty.path())
        .arg("--check")
        .output()
        .expect("Failed to execute mios-gen on empty root");

    assert!(
        !output.status.success(),
        "Expected non-zero exit code for missing ROADMAP.md"
    );
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(
        stderr.contains("ERROR: ROADMAP.md not found"),
        "Expected error message indicating missing ROADMAP.md, got: {stderr}"
    );
}
