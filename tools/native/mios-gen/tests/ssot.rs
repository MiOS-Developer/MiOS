// AI-hint: Two-sided integration tests for the mios-gen SSOT projection verbs: ai-manifest, gate-index, pipe-boundaries, pipeline-index, projection-evidence, render-globals, render-ports and sync (ADR-0021, Law 14).
// AI-doc: usr/share/doc/mios/manual/tools.md
// AI-related: tools/native/mios-gen/src/ai.rs, tools/native/mios-gen/src/indexes.rs, automation/98-drift-checks.sh, docs/design/doc-rust-static-port.md, tests/drift-gate-negatives.sh, tools/native/mios-gen/src/main.rs, tools/native/mios-gen/src/render.rs, tools/native/mios-gen/src/sync.rs, usr/share/mios/mios.toml

use std::sync::{Mutex, MutexGuard};

/// One test at a time: several modules edit real-tree files under a restore guard.
fn tree_lock() -> MutexGuard<'static, ()> {
    static LOCK: Mutex<()> = Mutex::new(());
    LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

mod ai_manifest {
    use std::fs;
    use std::path::PathBuf;
    use std::process::Command;

    fn fixture_root() -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        fs::create_dir(dir.path().join("tools")).unwrap();
        fs::write(dir.path().join("README.md"), "# Fixture\n").unwrap();
        fs::write(dir.path().join("tools/run.sh"), "#!/bin/sh\nexit 0\n").unwrap();
        git(dir.path(), &["init", "-q"]);
        git(dir.path(), &["add", "--", "README.md", "tools/run.sh"]);
        let output = Command::new(bin())
            .args(["ai-manifest", "--root"])
            .arg(dir.path())
            .output()
            .unwrap();
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        git(
            dir.path(),
            &["add", "--", "tools/manifest.json", "root-manifest.json"],
        );
        fs::write(dir.path().join("private.rs"), "operator data\n").unwrap();
        dir
    }

    fn git(root: &std::path::Path, args: &[&str]) {
        let mut command = Command::new("git");
        for (key, _) in std::env::vars().filter(|(key, _)| key.starts_with("GIT_")) {
            command.env_remove(key);
        }
        assert!(command
            .arg("-C")
            .arg(root)
            .args(args)
            .status()
            .unwrap()
            .success());
    }

    fn bin() -> &'static str {
        env!("CARGO_BIN_EXE_mios-gen")
    }

    #[test]
    fn test_ai_manifest_render_and_check() {
        let _tree = super::tree_lock();
        let fixture = fixture_root();
        let root = fixture.path().to_path_buf();

        // 1. Positive check on the clean repository
        let output = Command::new(bin())
            .args(["ai-manifest", "--root", &root.to_string_lossy(), "--check"])
            .output()
            .expect("Failed to execute mios-gen ai-manifest");

        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);
        println!("stdout:\n{stdout}");
        println!("stderr:\n{stderr}");

        assert!(
            output.status.success(),
            "Expected clean exit 0 for ai-manifest check, got: {:?}\nstderr: {}",
            output.status.code(),
            stderr
        );
        assert!(
            stdout.contains("[OK] AI repository and tool manifests are in sync"),
            "Expected sync confirmation message, got: {stdout}"
        );
        assert!(!fs::read_to_string(root.join("root-manifest.json"))
            .unwrap()
            .contains("private.rs"));

        // 2. Structured JSON format check
        let output_json = Command::new(bin())
            .args([
                "--format",
                "json",
                "ai-manifest",
                "--root",
                &root.to_string_lossy(),
                "--check",
            ])
            .output()
            .expect("Failed to execute mios-gen ai-manifest with --format json");

        let stdout_json = String::from_utf8_lossy(&output_json.stdout);
        let stderr_json = String::from_utf8_lossy(&output_json.stderr);
        assert!(
            output_json.status.success(),
            "Expected clean exit 0, got {:?}\nstderr: {}",
            output_json.status.code(),
            stderr_json
        );

        let parsed: serde_json::Value =
            serde_json::from_str(&stdout_json).expect("Failed to parse stdout as JSON");
        assert_eq!(parsed.get("status").and_then(|s| s.as_str()), Some("clean"));
        assert_eq!(
            parsed.get("subcommand").and_then(|s| s.as_str()),
            Some("ai-manifest")
        );
        assert_eq!(parsed.get("violations").and_then(|v| v.as_i64()), Some(0));

        // 3. Negative check: intentional mutation in repo tools/manifest.json, restored cleanly via RAII guard
        let target = root.join("tools/manifest.json");
        if target.exists() {
            let original = fs::read_to_string(&target).expect("Failed to read tools/manifest.json");

            struct Restorer(PathBuf, String);
            impl Drop for Restorer {
                fn drop(&mut self) {
                    let _ = fs::write(&self.0, &self.1);
                }
            }

            let _guard = Restorer(target.clone(), original);

            // Plant manifest drift
            fs::write(&target, "{\"drift\":\"injected\"}")
                .expect("Failed to write drift into manifest.json");

            let output_neg = Command::new(bin())
                .args(["ai-manifest", "--root", &root.to_string_lossy(), "--check"])
                .output()
                .expect("Failed to execute mios-gen ai-manifest");

            let stderr_neg = String::from_utf8_lossy(&output_neg.stderr);
            assert!(
                !output_neg.status.success(),
                "Expected non-zero exit code when manifest drift is present"
            );
            assert_eq!(output_neg.status.code(), Some(1));
            assert!(
                stderr_neg.contains("Manifest drift detected: tools/manifest.json"),
                "Expected stderr to report drift on tools/manifest.json, got: {stderr_neg}"
            );
        }
    }

    #[test]
    fn unreadable_index_fails_without_overwriting_manifests() {
        let _tree = super::tree_lock();
        let dir = tempfile::tempdir().unwrap();
        fs::write(dir.path().join(".git"), "gitdir: missing-repository\n").unwrap();
        fs::write(
            dir.path().join("root-manifest.json"),
            "preserve existing artifact\n",
        )
        .unwrap();
        let output = Command::new(bin())
            .args(["ai-manifest", "--root"])
            .arg(dir.path())
            .output()
            .unwrap();
        assert_eq!(output.status.code(), Some(1));
        assert!(
            String::from_utf8_lossy(&output.stderr).contains("cannot read tracked source index")
        );
        assert_eq!(
            fs::read_to_string(dir.path().join("root-manifest.json")).unwrap(),
            "preserve existing artifact\n"
        );
    }
}

mod gate_index {
    use std::fs;
    use std::process::Command;

    fn bin() -> &'static str {
        env!("CARGO_BIN_EXE_mios-gen")
    }

    #[test]
    fn test_gate_index_render_and_check() {
        let _tree = super::tree_lock();
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let auto_dir = root.join("automation");
        let ref_dir = root.join("usr/share/mios/reference");
        fs::create_dir_all(&auto_dir).unwrap();
        fs::create_dir_all(&ref_dir).unwrap();

        let script_content = r#"#!/usr/bin/env bash
# AI-hint: test script
check_alpha() {
    echo "[98-drift-checks] check alpha desc"
}
# --- check beta desc ---
check_beta() {
    true
}
main() {
    check_alpha
    check_beta
}
check_alias() {
    check_alpha
}
"#;
        fs::write(auto_dir.join("98-drift-checks.sh"), script_content).unwrap();

        // 1. Generation mode
        let out = Command::new(bin())
            .arg("gate-index")
            .arg("--root")
            .arg(root)
            .output()
            .expect("must execute mios-gen");
        assert_eq!(out.status.code(), Some(0));

        let tsv_path = ref_dir.join("drift-gate-index.tsv");
        assert!(tsv_path.is_file());
        let content = fs::read_to_string(&tsv_path).unwrap();
        let expected = "# Ordinal\tCheck Function\tDescription\n1\tcheck_alpha\tcheck alpha desc\n2\tcheck_beta\tcheck beta desc\n";
        assert_eq!(content, expected);

        // 2. Check mode - clean
        let out = Command::new(bin())
            .arg("gate-index")
            .arg("--root")
            .arg(root)
            .arg("--check")
            .output()
            .expect("must execute mios-gen");
        assert_eq!(out.status.code(), Some(0));
        let stdout = String::from_utf8_lossy(&out.stdout);
        assert!(stdout.contains("PASS: drift-gate-index.tsv is in sync."));

        // 3. Check mode - tampered
        fs::write(&tsv_path, "# tampered\n").unwrap();
        let out = Command::new(bin())
            .arg("gate-index")
            .arg("--root")
            .arg(root)
            .arg("--check")
            .output()
            .expect("must execute mios-gen");
        assert_eq!(out.status.code(), Some(1));
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert!(stderr.contains("out of sync with 98-drift-checks.sh"));
    }

    #[test]
    fn test_missing_main_fails() {
        let _tree = super::tree_lock();
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let auto_dir = root.join("automation");
        fs::create_dir_all(&auto_dir).unwrap();
        fs::write(auto_dir.join("98-drift-checks.sh"), "# no main\n").unwrap();

        let out = Command::new(bin())
            .arg("gate-index")
            .arg("--root")
            .arg(root)
            .output()
            .expect("must execute mios-gen");
        assert_eq!(out.status.code(), Some(1));
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert!(stderr.contains("main() function not found"));
    }

    #[test]
    fn invalid_main_preserves_existing_index() {
        let _tree = super::tree_lock();
        let dir = tempfile::tempdir().unwrap();
        fs::create_dir_all(dir.path().join("automation")).unwrap();
        fs::create_dir_all(dir.path().join("usr/share/mios/reference")).unwrap();
        let script = dir.path().join("automation/98-drift-checks.sh");
        let index = dir
            .path()
            .join("usr/share/mios/reference/drift-gate-index.tsv");
        fs::write(&index, "previous index\n").unwrap();
        for (source, error) in [
            (
                "main() {\n check_alpha\n check_alpha\n}\n",
                "Duplicate check_* functions found in main(): check_alpha",
            ),
            (
                "main() {\n}\ncheck_alias() {\n check_alpha\n}\n",
                "No check_*",
            ),
            ("main() {\n check_alpha\n", "not closed"),
        ] {
            fs::write(&script, source).unwrap();
            let output = Command::new(bin())
                .arg("gate-index")
                .arg("--root")
                .arg(dir.path())
                .output()
                .unwrap();
            assert_eq!(output.status.code(), Some(1));
            let stderr = String::from_utf8_lossy(&output.stderr);
            assert!(stderr.contains(error), "{stderr}");
            assert_eq!(fs::read_to_string(&index).unwrap(), "previous index\n");
        }
    }
}

mod pipe_boundaries {
    use std::fs;
    use std::path::{Path, PathBuf};
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

    struct Restorer {
        target: PathBuf,
        original_content: String,
    }

    impl Restorer {
        fn new(path: &Path) -> Self {
            let original_content =
                fs::read_to_string(path).expect("Failed to read original content for restorer");
            Self {
                target: path.to_path_buf(),
                original_content,
            }
        }
    }

    impl Drop for Restorer {
        fn drop(&mut self) {
            let _ = fs::write(&self.target, &self.original_content);
        }
    }

    #[test]
    fn test_pipe_boundaries_cli_e2e() {
        let _tree = super::tree_lock();
        let root = get_repo_root();
        let bin_path = bin();

        // 1. Positive control: standard check mode passes with exit code 0
        let output = Command::new(bin_path)
            .arg("pipe-boundaries")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute mios-gen pipe-boundaries --check");

        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);

        assert!(
            output.status.success(),
            "pipe-boundaries --check failed with code {:?}\nstdout: {}\nstderr: {}",
            output.status.code(),
            stdout,
            stderr
        );
        assert!(
            stdout.contains(
                "usr/share/mios/pipe-boundaries.manifest.json matches the tree (108 modules)."
            ),
            "Expected success message not found in stdout: {}",
            stdout
        );

        // 2. Positive control: structured JSON output format
        let output_json = Command::new(bin_path)
            .arg("--format")
            .arg("json")
            .arg("pipe-boundaries")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute mios-gen --format json pipe-boundaries --check");

        assert!(
            output_json.status.success(),
            "pipe-boundaries --format json --check failed"
        );
        let json_val: serde_json::Value =
            serde_json::from_slice(&output_json.stdout).expect("Failed to parse JSON output");
        assert_eq!(json_val["status"], "clean");
        assert_eq!(json_val["subcommand"], "pipe-boundaries");
        assert_eq!(json_val["violations"], 0);

        // 3. Negative control: mutate pipe-boundaries.manifest.json with stale content
        let manifest_path = root.join("usr/share/mios/pipe-boundaries.manifest.json");
        {
            let _restorer = Restorer::new(&manifest_path);
            let mut mutated = fs::read_to_string(&manifest_path).expect("read manifest");
            mutated.push('\n');
            fs::write(&manifest_path, mutated.as_bytes()).expect("write mutated manifest");

            let neg_output = Command::new(bin_path)
                .arg("pipe-boundaries")
                .arg("--root")
                .arg(&root)
                .arg("--check")
                .output()
                .expect("Failed to execute negative control");

            assert!(
                !neg_output.status.success(),
                "Expected failure on corrupted manifest, but got exit code 0"
            );
            let neg_stderr = String::from_utf8_lossy(&neg_output.stderr);
            assert!(
                neg_stderr.contains("[gen-pipe-boundary-manifest] STALE:"),
                "Expected STALE message in stderr, got: {}",
                neg_stderr
            );
        }

        // 4. Negative control: missing manifest file
        {
            let _restorer = Restorer::new(&manifest_path);
            fs::remove_file(&manifest_path).expect("remove manifest");

            let neg_output = Command::new(bin_path)
                .arg("pipe-boundaries")
                .arg("--root")
                .arg(&root)
                .arg("--check")
                .output()
                .expect("Failed to execute missing manifest control");

            assert!(
                !neg_output.status.success(),
                "Expected failure on missing manifest, but got exit code 0"
            );
            let neg_stderr = String::from_utf8_lossy(&neg_output.stderr);
            assert!(
                neg_stderr.contains("MISSING "),
                "Expected MISSING message in stderr, got: {}",
                neg_stderr
            );
        }

        // 5. Negative control: nonexistent root
        let missing_output = Command::new(bin_path)
            .arg("pipe-boundaries")
            .arg("--root")
            .arg(root.join("nonexistent_path_42"))
            .arg("--check")
            .output()
            .expect("Failed to execute missing root control");

        assert!(
            !missing_output.status.success(),
            "Expected failure on nonexistent root, but got exit code 0"
        );
        let missing_stderr = String::from_utf8_lossy(&missing_output.stderr);
        assert!(
            missing_stderr.contains("agent-pipe directory not found:"),
            "Expected directory not found error in stderr, got: {}",
            missing_stderr
        );

        // 6. Post-restoration check: tree passes cleanly
        let restored_output = Command::new(bin_path)
            .arg("pipe-boundaries")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute post-restoration check");

        assert!(
            restored_output.status.success(),
            "Post-restoration check failed"
        );
    }
}

mod pipeline_index {
    use std::fs;
    use std::process::Command;

    fn bin() -> &'static str {
        env!("CARGO_BIN_EXE_mios-gen")
    }

    #[test]
    fn test_pipeline_index_render_and_check() {
        let _tree = super::tree_lock();
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let auto_dir = root.join("automation");
        let ref_dir = root.join("usr/share/mios/reference");
        let ssot_dir = root.join("usr/share/mios");
        fs::create_dir_all(&auto_dir).unwrap();
        fs::create_dir_all(&ref_dir).unwrap();
        fs::create_dir_all(&ssot_dir).unwrap();

        fs::write(
            ssot_dir.join("mios.toml"),
            "[pipeline.space]\nmin = 0\nmax = 99\n[pipeline.invariants]\nprefix_unique = true\n",
        )
        .unwrap();

        let s01 =
            "#!/usr/bin/env bash\n# AI-hint: test hint\n# First stage description\necho stage 01\n";
        let s02 = "#!/usr/bin/env bash\n# Second stage description\necho stage 02\n";
        fs::write(auto_dir.join("01-first.sh"), s01).unwrap();
        fs::write(auto_dir.join("02-second.sh"), s02).unwrap();

        // 1. Generation mode
        let out = Command::new(bin())
            .arg("pipeline-index")
            .arg("--root")
            .arg(root)
            .output()
            .expect("must execute mios-gen");
        assert_eq!(out.status.code(), Some(0));

        let tsv_path = ref_dir.join("pipeline-index.tsv");
        assert!(tsv_path.is_file());
        let content = fs::read_to_string(&tsv_path).unwrap();
        let expected = "# NN\tkind\tname\tfile\toneline\n01\tscript\tfirst\tautomation/01-first.sh\tFirst stage description\n02\tscript\tsecond\tautomation/02-second.sh\tSecond stage description\n";
        assert_eq!(content, expected);

        // 2. Check mode - clean
        let out = Command::new(bin())
            .arg("pipeline-index")
            .arg("--root")
            .arg(root)
            .arg("--check")
            .output()
            .expect("must execute mios-gen");
        assert_eq!(out.status.code(), Some(0));
        let stdout = String::from_utf8_lossy(&out.stdout);
        assert!(stdout.contains("PASS: pipeline-index.tsv is in sync."));

        // 3. Check mode - tampered
        fs::write(&tsv_path, "# tampered\n").unwrap();
        let out = Command::new(bin())
            .arg("pipeline-index")
            .arg("--root")
            .arg(root)
            .arg("--check")
            .output()
            .expect("must execute mios-gen");
        assert_eq!(out.status.code(), Some(1));
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert!(stderr.contains("out of sync with automation scripts"));
    }

    #[test]
    fn test_duplicate_nn_prefix_fails() {
        let _tree = super::tree_lock();
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let auto_dir = root.join("automation");
        fs::create_dir_all(&auto_dir).unwrap();

        fs::write(auto_dir.join("01-first.sh"), "#!/bin/bash\n# desc\n").unwrap();
        fs::write(auto_dir.join("01-duplicate.sh"), "#!/bin/bash\n# desc2\n").unwrap();

        let out = Command::new(bin())
            .arg("pipeline-index")
            .arg("--root")
            .arg(root)
            .output()
            .expect("must execute mios-gen");
        assert_eq!(out.status.code(), Some(1));
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert!(stderr.contains("Duplicate NN prefix found: 01"));
    }
}

mod projection_evidence {
    use std::fs;
    use std::path::Path;
    use std::process::{Command, Output};

    fn write(root: &Path, name: &str, body: &str) {
        let path = root.join(name);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, body).unwrap();
    }

    fn git(root: &Path, args: &[&str]) {
        assert!(Command::new("git")
            .arg("-C")
            .arg(root)
            .args(args)
            .output()
            .unwrap()
            .status
            .success());
    }

    fn evidence(root: &Path, target: &str) -> Output {
        Command::new(env!("CARGO_BIN_EXE_mios-gen"))
            .args(["projection-evidence", "--root"])
            .arg(root)
            .args(["--generator", "cargo-manifests", "--target", target])
            .output()
            .unwrap()
    }

    #[test]
    fn dirty_projection_is_diffed_without_writing_or_staging_caller() {
        let _tree = super::tree_lock();
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path();
        git(root, &["init", "-q"]);
        write(
            root,
            "tools/native/example/Cargo.toml",
            "[package]\nname='example'\n",
        );
        write(root, "tools/native/Cargo.toml", "planted manifest drift\n");
        write(root, "VERSION", "0.3.0\n");
        git(root, &["add", "."]);
        write(
            root,
            "tools/native/Cargo.toml",
            "dirty concurrent contribution\n",
        );
        let indexed = Command::new("git")
            .arg("-C")
            .arg(root)
            .args(["show", ":tools/native/Cargo.toml"])
            .output()
            .unwrap()
            .stdout;
        let output = evidence(root, "tools/native/Cargo.toml");
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let diff = String::from_utf8_lossy(&output.stderr);
        assert!(diff.contains("-dirty concurrent contribution"));
        assert!(diff.contains("+[workspace]"));
        assert_eq!(
            fs::read_to_string(root.join("tools/native/Cargo.toml")).unwrap(),
            "dirty concurrent contribution\n"
        );
        assert_eq!(
            Command::new("git")
                .arg("-C")
                .arg(root)
                .args(["show", ":tools/native/Cargo.toml"])
                .output()
                .unwrap()
                .stdout,
            indexed
        );
    }

    #[test]
    fn missing_git_empty_census_and_untracked_target_fail() {
        let _tree = super::tree_lock();
        let temp = tempfile::tempdir().unwrap();
        assert!(!evidence(temp.path(), "output").status.success());
        git(temp.path(), &["init", "-q"]);
        let empty = evidence(temp.path(), "output");
        assert!(!empty.status.success());
        assert!(String::from_utf8_lossy(&empty.stderr).contains("census is empty"));
        write(temp.path(), "VERSION", "0.3.0\n");
        git(temp.path(), &["add", "."]);
        let untracked = evidence(temp.path(), "../outside");
        assert!(!untracked.status.success());
        assert!(String::from_utf8_lossy(&untracked.stderr).contains("beneath root"));
        let untracked = evidence(temp.path(), "output");
        assert!(!untracked.status.success());
        assert!(String::from_utf8_lossy(&untracked.stderr).contains("not tracked"));
    }

    #[cfg(unix)]
    #[test]
    fn tracked_symlink_cannot_copy_external_inputs() {
        let _tree = super::tree_lock();
        let temp = tempfile::tempdir().unwrap();
        let external = tempfile::tempdir().unwrap();
        write(external.path(), "secret", "outside input");
        git(temp.path(), &["init", "-q"]);
        std::os::unix::fs::symlink(external.path().join("secret"), temp.path().join("linked"))
            .unwrap();
        git(temp.path(), &["add", "."]);
        let output = evidence(temp.path(), "linked");
        assert!(!output.status.success());
        assert!(String::from_utf8_lossy(&output.stderr).contains("escapes root"));
        assert_eq!(
            fs::read_to_string(external.path().join("secret")).unwrap(),
            "outside input"
        );
    }
}

mod render_globals {
    use std::fs;
    use std::path::{Path, PathBuf};
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

    struct Restorer {
        target: PathBuf,
        original_content: Vec<u8>,
    }

    impl Restorer {
        fn new(path: &Path) -> Self {
            let original_content =
                fs::read(path).expect("Failed to read original content for restorer");
            Self {
                target: path.to_path_buf(),
                original_content,
            }
        }
    }

    impl Drop for Restorer {
        fn drop(&mut self) {
            let _ = fs::write(&self.target, &self.original_content);
        }
    }

    #[test]
    fn test_render_globals_cli_e2e() {
        let _tree = super::tree_lock();
        let root = get_repo_root();

        // 1. Positive control: check clean repository with --check
        let output = Command::new(bin())
            .args([
                "render-globals",
                "--root",
                &root.to_string_lossy(),
                "--check",
            ])
            .output()
            .expect("Failed to execute mios-gen render-globals");

        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);

        assert!(
            output.status.success(),
            "Expected exit 0 for render-globals --check on clean tree, got: {:?}\nstderr: {}",
            output.status.code(),
            stderr
        );
        assert!(
            stdout.contains("both resolvers match SSOT"),
            "Expected match confirmation message in stdout, got:\n{stdout}"
        );

        // 2. Positive control: JSON format check
        let output_json = Command::new(bin())
            .args([
                "--format",
                "json",
                "render-globals",
                "--root",
                &root.to_string_lossy(),
                "--check",
            ])
            .output()
            .expect("Failed to execute mios-gen --format json render-globals");

        assert!(
            output_json.status.success(),
            "Expected exit 0 for render-globals --format json --check, got: {:?}",
            output_json.status.code()
        );
        let json_val: serde_json::Value = serde_json::from_slice(&output_json.stdout)
            .expect("Valid JSON output from --format json");
        assert_eq!(json_val["status"], "clean");
        assert_eq!(json_val["subcommand"], "render-globals");
        assert_eq!(json_val["violations"], 0);

        // 3. Negative control 1: Mutate automation/lib/globals.sh
        let sh_path = root.join("automation/lib/globals.sh");
        assert!(sh_path.exists(), "globals.sh must exist");
        {
            let _restorer = Restorer::new(&sh_path);
            let mut mutated = fs::read_to_string(&sh_path).unwrap();
            mutated.push_str("\n# CANARY MUTATION FOR DRIFT TEST\n");
            fs::write(&sh_path, mutated).unwrap();

            let output_neg = Command::new(bin())
                .args([
                    "render-globals",
                    "--root",
                    &root.to_string_lossy(),
                    "--check",
                ])
                .output()
                .expect("Failed to execute mios-gen render-globals on mutated sh");

            assert!(
                !output_neg.status.success(),
                "Expected failure for mutated globals.sh, but succeeded"
            );
            let err_text = String::from_utf8_lossy(&output_neg.stderr);
            assert!(
                err_text.contains("automation/lib/globals.sh"),
                "Expected error naming globals.sh, got:\n{err_text}"
            );
        }

        // 4. Negative control 2: Mutate automation/lib/globals.ps1
        let ps_path = root.join("automation/lib/globals.ps1");
        assert!(ps_path.exists(), "globals.ps1 must exist");
        {
            let _restorer = Restorer::new(&ps_path);
            let mut mutated_bytes = fs::read(&ps_path).unwrap();
            mutated_bytes.extend_from_slice(b"\r\n# CANARY MUTATION FOR DRIFT TEST\r\n");
            fs::write(&ps_path, mutated_bytes).unwrap();

            let output_neg = Command::new(bin())
                .args([
                    "render-globals",
                    "--root",
                    &root.to_string_lossy(),
                    "--check",
                ])
                .output()
                .expect("Failed to execute mios-gen render-globals on mutated ps1");

            assert!(
                !output_neg.status.success(),
                "Expected failure for mutated globals.ps1, but succeeded"
            );
            let err_text = String::from_utf8_lossy(&output_neg.stderr);
            assert!(
                err_text.contains("automation/lib/globals.ps1"),
                "Expected error naming globals.ps1, got:\n{err_text}"
            );
        }
    }
}

mod render_ports {
    use std::fs;
    use std::path::{Path, PathBuf};
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

    struct Restorer {
        target: PathBuf,
        original_content: String,
    }

    impl Restorer {
        fn new(path: &Path) -> Self {
            let original_content =
                fs::read_to_string(path).expect("Failed to read original content for restorer");
            Self {
                target: path.to_path_buf(),
                original_content,
            }
        }
    }

    impl Drop for Restorer {
        fn drop(&mut self) {
            let _ = fs::write(&self.target, &self.original_content);
        }
    }

    #[test]
    fn test_render_ports_cli_e2e() {
        let _tree = super::tree_lock();
        let root = get_repo_root();

        // 1. Positive control: check clean repository
        let output = Command::new(bin())
            .args(["render-ports", "--root", &root.to_string_lossy(), "--check"])
            .output()
            .expect("Failed to execute mios-gen render-ports");

        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);

        assert!(
            output.status.success(),
            "Expected exit 0 for render-ports --check on clean tree, got: {:?}\nstderr: {}",
            output.status.code(),
            stderr
        );
        assert!(
            stdout.contains("ports derive cleanly from"),
            "Expected confirmation message in stdout, got:\n{stdout}"
        );

        // 2. Positive control: --print mode
        let output_print = Command::new(bin())
            .args(["render-ports", "--root", &root.to_string_lossy(), "--print"])
            .output()
            .expect("Failed to execute mios-gen render-ports --print");

        let stdout_print = String::from_utf8_lossy(&output_print.stdout);
        assert!(output_print.status.success(), "Expected exit 0 for --print");
        assert!(
            stdout_print.contains("53  adguard_dns"),
            "Expected adguard_dns port in output, got:\n{stdout_print}"
        );
        assert!(
            stdout_print.contains("8700  agent_pipe"),
            "Expected agent_pipe port in output, got:\n{stdout_print}"
        );

        // 3. Positive control: JSON format check
        let output_json = Command::new(bin())
            .args([
                "--format",
                "json",
                "render-ports",
                "--root",
                &root.to_string_lossy(),
                "--check",
            ])
            .output()
            .expect("Failed to execute mios-gen --format json render-ports");

        assert!(
            output_json.status.success(),
            "Expected exit 0 for json output"
        );
        let stdout_json = String::from_utf8_lossy(&output_json.stdout);
        let parsed_json: serde_json::Value =
            serde_json::from_str(&stdout_json).expect("Expected valid json output");
        assert_eq!(parsed_json["status"], "clean");
        assert_eq!(parsed_json["subcommand"], "render-ports");
        assert_eq!(parsed_json["violations"], 0);

        // 4. Negative control: inject category band collision
        let toml_path = root.join("usr/share/mios/mios.toml");
        let _restorer = Restorer::new(&toml_path);

        let original_toml = fs::read_to_string(&toml_path).expect("Read mios.toml");
        let mutated_toml = original_toml.replace("base    = 8800", "base    = 8700");
        assert_ne!(
            original_toml, mutated_toml,
            "Mutation failed to apply to mios.toml"
        );
        fs::write(&toml_path, &mutated_toml).expect("Write mutated mios.toml");

        let output_neg = Command::new(bin())
            .args(["render-ports", "--root", &root.to_string_lossy(), "--check"])
            .output()
            .expect("Failed to execute mios-gen on mutated tree");

        let stderr_neg = String::from_utf8_lossy(&output_neg.stderr);
        let stdout_neg = String::from_utf8_lossy(&output_neg.stdout);

        assert!(
        !output_neg.status.success(),
        "Expected failure on mutated category base, but succeeded:\nstdout: {stdout_neg}\nstderr: {stderr_neg}"
    );
        assert!(
            stderr_neg.contains("overlap") || stderr_neg.contains("collision"),
            "Expected collision or overlap error in stderr, got:\n{stderr_neg}"
        );

        // RAII Restorer will restore clean mios.toml when dropped at end of scope
    }
}

mod sync {
    use std::fs;
    use std::path::Path;
    use std::process::{Command, Output};

    fn fixture(plan: &str) -> tempfile::TempDir {
        let root = tempfile::tempdir().unwrap();
        fs::create_dir_all(root.path().join("usr/share/mios")).unwrap();
        fs::write(root.path().join("usr/share/mios/mios.toml"), plan).unwrap();
        fs::write(root.path().join("input"), b"projected bytes\n").unwrap();
        fs::write(root.path().join("output"), b"previous bytes\n").unwrap();
        assert!(Command::new("git")
            .args(["init", "-q"])
            .arg(root.path())
            .status()
            .unwrap()
            .success());
        assert!(Command::new("git")
            .current_dir(root.path())
            .args(["add", "."])
            .status()
            .unwrap()
            .success());
        root
    }

    fn invoke(root: &Path, args: &[&str]) -> Output {
        let mut command = Command::new(env!("CARGO_BIN_EXE_mios-gen"));
        command.args(["sync", "--root"]).arg(root).args(args);
        // Prove the selected root governs the plan, even with unrelated loader pointers.
        command.env("MIOS_VENDOR_TOML", root.join("outside-does-not-exist.toml"));
        command.output().unwrap()
    }

    const COPY: &str = "[generation.sync]\nunit_projections=[]\n[[generation.sync.steps]]\nid='copy'\ncopy=['input','output']\n";

    #[cfg(unix)]
    #[test]
    fn linked_output_parent_cannot_escape_the_selected_root() {
        let _tree = super::tree_lock();
        let outside = tempfile::tempdir().unwrap();
        let root = fixture(&format!(
            "{COPY}[[generation.sync.steps]]\nid='escape'\ncopy=['input','linked/output']\n"
        ));
        std::os::unix::fs::symlink(outside.path(), root.path().join("linked")).unwrap();
        let result = invoke(root.path(), &[]);
        assert!(!result.status.success());
        assert!(String::from_utf8_lossy(&result.stderr).contains("escapes the root through a link"));
        assert!(!outside.path().join("output").exists());
        assert_eq!(
            fs::read(root.path().join("output")).unwrap(),
            b"previous bytes\n"
        );
    }

    #[test]
    fn plan_is_read_only_and_copy_is_idempotent() {
        let _tree = super::tree_lock();
        let root = fixture(COPY);
        let index = root.path().join(".git/index");
        let original_index = fs::read(&index).unwrap();
        let receipt = invoke(root.path(), &["--plan"]);
        assert!(
            receipt.status.success(),
            "{}",
            String::from_utf8_lossy(&receipt.stderr)
        );
        let plan: serde_json::Value = serde_json::from_slice(&receipt.stdout).unwrap();
        assert_eq!(plan["steps"].as_array().unwrap().len(), 1);
        assert_eq!(
            fs::read(root.path().join("output")).unwrap(),
            b"previous bytes\n"
        );
        assert_eq!(fs::read(&index).unwrap(), original_index);
        for _ in 0..2 {
            assert!(invoke(root.path(), &[]).status.success());
            assert_eq!(
                fs::read(root.path().join("output")).unwrap(),
                b"projected bytes\n"
            );
            assert_eq!(fs::read(&index).unwrap(), original_index);
        }
        fs::write(root.path().join("input"), b"changed source\n").unwrap();
        assert!(invoke(root.path(), &[]).status.success());
        assert_eq!(
            fs::read(root.path().join("output")).unwrap(),
            b"changed source\n"
        );
    }

    #[test]
    fn late_missing_native_tool_blocks_every_write_and_index_change() {
        let _tree = super::tree_lock();
        let root = fixture(&format!("{COPY}[[generation.sync.steps]]\nid='missing'\ncalls=[{{tool='mios-deliberately-missing-fixture'}}]\n"));
        let index = fs::read(root.path().join(".git/index")).unwrap();
        for args in [&[][..], &["--plan"][..]] {
            let result = invoke(root.path(), args);
            assert!(!result.status.success());
            assert!(String::from_utf8_lossy(&result.stderr)
                .contains("mios-deliberately-missing-fixture"));
            assert_eq!(
                fs::read(root.path().join("output")).unwrap(),
                b"previous bytes\n"
            );
            assert_eq!(fs::read(root.path().join(".git/index")).unwrap(), index);
        }
    }

    #[test]
    fn missing_adapter_and_escaping_paths_are_fatal_before_copy() {
        let _tree = super::tree_lock();
        for action in [
            "calls=[{script='missing-adapter.py'}]",
            "copy=['../outside','output']",
        ] {
            let root = fixture(&format!(
                "{COPY}[[generation.sync.steps]]\nid='bad'\n{action}\n"
            ));
            let result = invoke(root.path(), &[]);
            assert!(!result.status.success());
            let error = String::from_utf8_lossy(&result.stderr);
            assert!(
                error.contains("missing-adapter.py") || error.contains("../outside"),
                "{error}"
            );
            assert_eq!(
                fs::read(root.path().join("output")).unwrap(),
                b"previous bytes\n"
            );
        }
    }

    #[test]
    fn child_failure_stops_later_projection_with_real_status() {
        let _tree = super::tree_lock();
        let root = fixture("[generation.sync]\nunit_projections=[]\n[[generation.sync.steps]]\nid='rejected-call'\ncalls=[{tool='mios-gen',args=['--deliberately-invalid-fixture']} ]\n[[generation.sync.steps]]\nid='after'\ncopy=['input','output']\n");
        let result = invoke(root.path(), &[]);
        assert!(!result.status.success());
        let error = String::from_utf8_lossy(&result.stderr);
        assert!(
            error.contains("rejected-call") && error.contains("exit"),
            "{error}"
        );
        assert_eq!(
            fs::read(root.path().join("output")).unwrap(),
            b"previous bytes\n"
        );
    }

    #[test]
    fn corrupt_index_is_not_rebuilt_or_ignored() {
        let _tree = super::tree_lock();
        let root = fixture(COPY);
        let index = root.path().join(".git/index");
        fs::write(&index, b"planted corrupt index\n").unwrap();
        let result = invoke(root.path(), &[]);
        assert!(!result.status.success());
        assert!(String::from_utf8_lossy(&result.stderr).contains("index"));
        assert_eq!(fs::read(&index).unwrap(), b"planted corrupt index\n");
        assert_eq!(
            fs::read(root.path().join("output")).unwrap(),
            b"previous bytes\n"
        );
    }

    #[test]
    fn explicit_native_directory_never_selects_other_platform_artifacts() {
        let _tree = super::tree_lock();
        let root = fixture(&format!(
        "{COPY}[[generation.sync.steps]]\nid='tool'\ncalls=[{{tool='mios-fixture-platform'}}]\n"
    ));
        let directory = root.path().join("native binaries");
        fs::create_dir(&directory).unwrap();
        let wrong = if cfg!(windows) {
            "mios-fixture-platform"
        } else {
            "mios-fixture-platform.exe"
        };
        fs::write(directory.join(wrong), b"opposite-platform artifact").unwrap();
        let result = Command::new(env!("CARGO_BIN_EXE_mios-gen"))
            .args(["sync", "--root"])
            .arg(root.path())
            .env("MIOS_NATIVE_BIN_DIR", &directory)
            .output()
            .unwrap();
        assert!(!result.status.success());
        let error = String::from_utf8_lossy(&result.stderr);
        assert!(
            error.contains("mios-fixture-platform") && error.contains("MIOS_NATIVE_BIN_DIR"),
            "{error}"
        );
        assert_eq!(
            fs::read(root.path().join("output")).unwrap(),
            b"previous bytes\n"
        );
    }
}
