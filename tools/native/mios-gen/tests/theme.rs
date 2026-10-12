// AI-hint: Two-sided integration tests for the mios-gen desktop and terminal verbs: render-fastfetch, render-desktop and render-tmux-theme with its host and runtime layers (ADR-0021, Law 14).
// AI-doc: usr/share/doc/mios/manual/tools.md
// AI-related: tools/native/mios-gen/src/terminal.rs, automation/98-drift-checks.sh, tools/native/mios-gen/src/render.rs, tests/drift-gate-negatives.sh, usr/libexec/mios/ux/theme_sync.py, etc/profile.d/mios-prompt.sh, usr/libexec/mios/mios-terminal

use std::sync::{Mutex, MutexGuard};

/// One test at a time: several modules edit real-tree files under a restore guard.
fn tree_lock() -> MutexGuard<'static, ()> {
    static LOCK: Mutex<()> = Mutex::new(());
    LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

mod fastfetch {
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::process::{Command, Output};

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

    fn run(root: &Path, args: &[&str]) -> Output {
        Command::new(bin())
            .arg("render-fastfetch")
            .arg("--root")
            .arg(root)
            .args(args)
            .output()
            .expect("Failed to execute mios-gen render-fastfetch")
    }

    fn text(o: &Output) -> String {
        format!(
            "{}{}",
            String::from_utf8_lossy(&o.stdout),
            String::from_utf8_lossy(&o.stderr)
        )
    }

    #[test]
    fn test_render_fastfetch_cli_e2e() {
        let _tree = super::tree_lock();
        let root = get_repo_root();
        let td = tempfile::tempdir().unwrap();
        let out_path = td.path().join("fastfetch.jsonc");
        let out_s = out_path.to_string_lossy().to_string();

        // 1. Positive control: mock execution generates a valid configuration, writes nothing.
        let o = run(&root, &["--mock"]);
        assert!(o.status.success(), "{}", text(&o));
        assert!(text(&o).contains("[fastfetch] SUCCESS: Generated Fastfetch config"));

        // 2. Positive control: logo-type flag and JSON output mode.
        assert!(run(&root, &["--mock", "--logo-type", "auto"])
            .status
            .success());
        let j = Command::new(bin())
            .args(["--format", "json", "render-fastfetch", "--mock", "--root"])
            .arg(&root)
            .output()
            .unwrap();
        assert!(j.status.success());
        assert!(String::from_utf8_lossy(&j.stdout).contains("\"violations\": 0"));

        // 3. Legacy byte parity: json.dumps(indent=2) => ASCII-escaped, no trailing newline.
        let o = run(&root, &["--mock", "--out", &out_s]);
        assert!(o.status.success(), "{}", text(&o));
        let bytes = fs::read(&out_path).unwrap();
        assert!(
            bytes.is_ascii(),
            "non-ASCII must be \\u-escaped like Python ensure_ascii"
        );
        assert!(
            !bytes.ends_with(b"\n"),
            "legacy output has no trailing newline"
        );
        let body = String::from_utf8(bytes).unwrap();
        assert!(
            body.contains("\"separator\": \" \\udb80\\udd3e \""),
            "surrogate-pair escape"
        );
        assert_eq!(body.lines().count(), 106);
        assert_eq!(body.len(), 1766, "legacy mock render is 1766 bytes");

        // 4. Check mode passes on a matching artifact (mock render compares too).
        let o = run(&root, &["--check", "--mock", "--out", &out_s]);
        assert!(o.status.success(), "{}", text(&o));

        // 5. --dry-run never writes.
        let dry = td.path().join("dry.jsonc");
        let o = run(
            &root,
            &["--mock", "--dry-run", "--out", &dry.to_string_lossy()],
        );
        assert!(o.status.success());
        assert!(!dry.exists(), "dry-run must not write");

        // 6. Negative: a corrupted artifact fails in check mode (also with --mock).
        fs::write(&out_path, "{\"corrupted\": true}\n").unwrap();
        for extra in [
            &["--check", "--out", out_s.as_str()][..],
            &["--check", "--mock", "--out", out_s.as_str()][..],
        ] {
            let o = run(&root, extra);
            assert_eq!(o.status.code(), Some(1), "{}", text(&o));
            assert!(text(&o).contains("drifted"));
        }
    }

    #[test]
    fn check_without_subject_or_with_missing_artifact_fails() {
        let _tree = super::tree_lock();
        let root = get_repo_root();
        let td = tempfile::tempdir().unwrap();

        // The old default `--check` compared nothing and exited 0.
        for extra in [&["--check"][..], &["--check", "--mock"][..]] {
            let o = run(&root, extra);
            assert_eq!(o.status.code(), Some(1), "{}", text(&o));
            assert!(text(&o).contains("requires --out"), "{}", text(&o));
        }

        // A missing artifact is not a pass.
        let missing = td.path().join("absent.jsonc");
        let o = run(
            &root,
            &["--check", "--mock", "--out", &missing.to_string_lossy()],
        );
        assert_eq!(o.status.code(), Some(1));
        assert!(text(&o).contains("does not exist"));
    }

    #[test]
    fn missing_ssot_input_is_an_error_even_with_mock() {
        let _tree = super::tree_lock();
        let td = tempfile::tempdir().unwrap();
        let o = run(td.path(), &["--mock"]);
        assert_eq!(o.status.code(), Some(1), "{}", text(&o));
        assert!(text(&o).contains("Missing SSOT toml file"));
    }

    #[test]
    fn committed_golden_fixture_matches_deterministic_render() {
        let _tree = super::tree_lock();
        let root = get_repo_root();
        let fixture = root.join("tests/golden/fastfetch/mock.jsonc");
        assert!(fixture.is_file(), "golden fixture is committed");
        let o = run(
            &root,
            &["--check", "--mock", "--out", &fixture.to_string_lossy()],
        );
        assert!(o.status.success(), "{}", text(&o));
    }
}

mod host_tmux {
    use std::{fs, path::PathBuf, process::Command};

    fn root() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .unwrap()
            .parent()
            .unwrap()
            .parent()
            .unwrap()
            .into()
    }
    fn run(host: &str, user: &str) -> std::process::Output {
        let tmp = tempfile::tempdir().unwrap();
        let host_path = tmp.path().join("host.toml");
        let user_path = tmp.path().join("user.toml");
        fs::write(&host_path, host).unwrap();
        fs::write(&user_path, user).unwrap();
        Command::new(env!("CARGO_BIN_EXE_mios-gen"))
            .args(["render-host-tmux", "--root"])
            .arg(root())
            .env("MIOS_VENDOR_TOML", root().join("usr/share/mios/mios.toml"))
            .env("MIOS_HOST_TOML", host_path)
            .env("MIOS_USER_TOML", user_path)
            .env("MIOS_VENDOR_TOML_D", tmp.path().join("missing-vendor.d"))
            .env("MIOS_HOST_TOML_D", tmp.path().join("missing-host.d"))
            .env("MIOS_USER_TOML_D", tmp.path().join("missing-user.d"))
            .output()
            .unwrap()
    }
    #[test]
    fn host_cli_renders_layered_overrides_without_shell_or_python() {
        let _tree = super::tree_lock();
        let output = run(
            "[theme.tmux]\nstatus_position='top'\n[colors]\nfg='#112233'\n",
            "[colors]\nfg='#FEDCBA'\n[terminal]\nscrollback_rows=4567\n",
        );
        assert!(
            output.status.success(),
            "{}",
            String::from_utf8_lossy(&output.stderr)
        );
        let text = String::from_utf8(output.stdout).unwrap();
        for expected in [
            "#FEDCBA",
            "set -g status-position top",
            "set -g history-limit 4567",
            "bind-key h select-pane -L",
        ] {
            assert!(text.contains(expected), "{expected}");
        }
        assert!(!text.contains("#112233"));
        assert!(text.lines().count() > 55);
    }
    #[test]
    fn host_cli_rejects_invalid_effective_color_without_output() {
        let _tree = super::tree_lock();
        let output = run("", "[colors]\nfg='invalid-host-color'\n");
        assert!(!output.status.success());
        assert!(output.stdout.is_empty());
        assert!(String::from_utf8_lossy(&output.stderr).contains("[colors].fg"));
    }
}

mod render_desktop {
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

    struct FileDeleter {
        target: PathBuf,
    }

    impl FileDeleter {
        fn new(path: &Path) -> Self {
            Self {
                target: path.to_path_buf(),
            }
        }
    }

    impl Drop for FileDeleter {
        fn drop(&mut self) {
            if self.target.exists() {
                let _ = fs::remove_file(&self.target);
            }
        }
    }

    #[test]
    fn test_render_desktop_cli_e2e() {
        let _tree = super::tree_lock();
        let root = get_repo_root();

        // 1. Positive control: check clean repository
        let output = Command::new(bin())
            .args([
                "render-desktop",
                "--root",
                &root.to_string_lossy(),
                "--check",
            ])
            .output()
            .expect("Failed to execute mios-gen render-desktop");

        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);

        assert!(
            output.status.success(),
            "Expected exit 0 for render-desktop --check on clean tree, got: {:?}\nstderr: {}",
            output.status.code(),
            stderr
        );
        assert!(
            stdout.contains("All .desktop launchers match SSOT"),
            "Expected confirmation message in stdout, got:\n{stdout}"
        );

        // 2. Positive control: JSON format check
        let output_json = Command::new(bin())
            .args([
                "--format",
                "json",
                "render-desktop",
                "--root",
                &root.to_string_lossy(),
                "--check",
            ])
            .output()
            .expect("Failed to execute mios-gen --format json render-desktop");

        assert!(
            output_json.status.success(),
            "Expected exit 0 for json output"
        );
        let stdout_json = String::from_utf8_lossy(&output_json.stdout);
        let parsed_json: serde_json::Value =
            serde_json::from_str(&stdout_json).expect("Expected valid json output");
        assert_eq!(parsed_json["status"], "clean");
        assert_eq!(parsed_json["subcommand"], "render-desktop");
        assert_eq!(parsed_json["violations"], 0);

        // 3. Negative control: inject content mutation into an existing .desktop file
        let target_desktop = root.join("usr/share/applications/mios-svc-searxng.desktop");
        assert!(
            target_desktop.exists(),
            "Expected mios-svc-searxng.desktop to exist at {:?}",
            target_desktop
        );
        {
            let _restorer = Restorer::new(&target_desktop);

            let original_content = fs::read_to_string(&target_desktop).expect("Read desktop file");
            let mutated_content =
                original_content.replace("Name=MiOS Search (SearXNG)", "Name=CorruptedSearXNG");
            assert_ne!(original_content, mutated_content);
            fs::write(&target_desktop, &mutated_content).expect("Write mutated desktop file");

            let output_neg = Command::new(bin())
                .args([
                    "render-desktop",
                    "--root",
                    &root.to_string_lossy(),
                    "--check",
                ])
                .output()
                .expect("Failed to execute mios-gen on mutated desktop");

            let stderr_neg = String::from_utf8_lossy(&output_neg.stderr);
            let stdout_neg = String::from_utf8_lossy(&output_neg.stdout);

            assert!(
            !output_neg.status.success(),
            "Expected failure on mutated desktop file, but succeeded:\nstdout: {stdout_neg}\nstderr: {stderr_neg}"
        );
            assert!(
                stderr_neg.contains("content drifted") || stderr_neg.contains("DRIFT:"),
                "Expected drift error in stderr, got:\n{stderr_neg}"
            );
        }

        // 4. Negative control: inject unmanaged .desktop file
        let unmanaged_desktop =
            root.join("usr/share/applications/mios-unmanaged-rogue-test.desktop");
        {
            let _deleter = FileDeleter::new(&unmanaged_desktop);
            fs::write(
                &unmanaged_desktop,
                "[Desktop Entry]\nName=Rogue\nType=Application\n",
            )
            .expect("Write unmanaged desktop file");

            let output_unmanaged = Command::new(bin())
                .args([
                    "render-desktop",
                    "--root",
                    &root.to_string_lossy(),
                    "--check",
                ])
                .output()
                .expect("Failed to execute mios-gen on unmanaged desktop tree");

            let stderr_unm = String::from_utf8_lossy(&output_unmanaged.stderr);
            let stdout_unm = String::from_utf8_lossy(&output_unmanaged.stdout);

            assert!(
            !output_unmanaged.status.success(),
            "Expected failure on unmanaged desktop file, but succeeded:\nstdout: {stdout_unm}\nstderr: {stderr_unm}"
        );
            assert!(
                stderr_unm.contains("ships but no") || stderr_unm.contains("DRIFT:"),
                "Expected unmanaged error in stderr, got:\n{stderr_unm}"
            );
        }
    }
}

mod tmux_runtime {
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::process::{Command, Output};

    fn repo_root() -> PathBuf {
        PathBuf::from(env!("CARGO_MANIFEST_DIR"))
            .parent()
            .and_then(|p| p.parent())
            .and_then(|p| p.parent())
            .expect("repo root")
            .to_path_buf()
    }

    fn exe(name: &str) -> String {
        if cfg!(windows) {
            format!("{name}.exe")
        } else {
            name.to_string()
        }
    }

    /// mios-unit-gen is a sibling workspace member the projector shells to; build it once
    /// if the workspace has not produced it yet so the positive control is never skipped.
    fn unit_gen() -> PathBuf {
        // Cargo places integration-test executables under <profile>/deps, next to
        // the binaries it built. This follows --target-dir/CARGO_TARGET_DIR and
        // cross-target profiles without guessing a checkout-local target tree.
        let profile = std::env::current_exe()
            .expect("integration test executable")
            .parent()
            .and_then(Path::parent)
            .expect("Cargo profile directory")
            .to_path_buf();
        let sibling = profile.join(exe("mios-unit-gen"));
        if sibling.is_file() {
            return sibling;
        }
        let native = repo_root().join("tools/native");
        let found = |p: &PathBuf| p.is_file();
        for profile in ["debug", "release"] {
            let p = native
                .join("target")
                .join(profile)
                .join(exe("mios-unit-gen"));
            if found(&p) {
                return p;
            }
        }
        let status = Command::new(std::env::var("CARGO").unwrap_or_else(|_| "cargo".into()))
            .args(["build", "-p", "mios-unit-gen", "--manifest-path"])
            .arg(native.join("Cargo.toml"))
            .status()
            .expect("spawn cargo build -p mios-unit-gen");
        assert!(status.success(), "building mios-unit-gen failed");
        let p = sibling;
        assert!(p.is_file(), "mios-unit-gen missing after build");
        p
    }

    fn python() -> &'static str {
        if cfg!(windows) {
            "python"
        } else {
            "python3"
        }
    }

    fn run(dir: &Path, user_toml: Option<&str>, extra: &[&str]) -> (Output, PathBuf) {
        let tmp = dir.to_path_buf();
        let user = tmp.join("user.toml");
        match user_toml {
            Some(s) => fs::write(&user, s).unwrap(),
            None => {
                let _ = fs::remove_file(&user);
            }
        }
        let out_dir = tmp.join("runtime");
        let root = repo_root();
        let output = Command::new(env!("CARGO_BIN_EXE_mios-gen"))
            .arg("render-tmux-theme")
            .arg("--root")
            .arg(&root)
            .args(extra)
            .env("MIOS_TOML_ROOT", &root)
            .env("MIOS_HOST_TOML", tmp.join("absent-host.toml"))
            .env("MIOS_USER_TOML", &user)
            .env("MIOS_RESOLVER_NATIVE", "0")
            .env("MIOS_UNIT_GEN", unit_gen())
            .env(
                "MIOS_THEME_SYNC",
                root.join("usr/libexec/mios/ux/theme_sync.py"),
            )
            .env_remove("SSH_CONNECTION")
            .env_remove("SSH_CLIENT")
            .env_remove("SSH_TTY")
            .env_remove("MIOS_REMOTE_TERMINAL")
            .output()
            .expect("run mios-gen");
        (output, out_dir)
    }

    fn have_python() -> bool {
        Command::new(python())
            .arg("--version")
            .output()
            .map(|o| o.status.success())
            .unwrap_or(false)
    }

    #[test]
    fn runtime_projects_layered_tmux_conf_and_prompt() {
        let _tree = super::tree_lock();
        assert!(have_python(), "python is required for the prompt renderer");
        let td = tempfile::tempdir().unwrap();

        // Baseline: vendor tier only.
        let dir_s = td.path().join("runtime").to_string_lossy().to_string();
        let args = vec!["--runtime", dir_s.as_str()];
        let (o, d) = run(td.path(), None, &args);
        assert!(
            o.status.success(),
            "baseline failed: {}",
            String::from_utf8_lossy(&o.stderr)
        );
        let conf = fs::read_to_string(d.join("tmux.conf")).unwrap();
        assert!(
            conf.contains("set -g status-position bottom"),
            "vendor default"
        );
        assert!(
            conf.contains("unbind-key -a -T prefix"),
            "keybindings appended"
        );
        let omp: serde_json::Value =
            serde_json::from_str(&fs::read_to_string(d.join("mios.omp.json")).unwrap()).unwrap();
        assert!(omp.get("blocks").is_some(), "omp has blocks");
        assert!(
            o.stdout.is_empty(),
            "runtime mode must be silent on success (login shells)"
        );

        // Layer proof: the USER tier must win over the vendor tier.
        let (o2, d2) = run(
            td.path(),
            Some("[theme.tmux]\nstatus_position = \"top\"\n"),
            &args,
        );
        assert!(
            o2.status.success(),
            "{}",
            String::from_utf8_lossy(&o2.stderr)
        );
        let conf2 = fs::read_to_string(d2.join("tmux.conf")).unwrap();
        assert!(
            conf2.contains("set -g status-position top"),
            "user layer wins"
        );
        assert!(!conf2.contains("set -g status-position bottom"));

        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let mode = fs::metadata(&d2).unwrap().permissions().mode() & 0o777;
            assert_eq!(mode, 0o700, "projection directory must be private");
        }
    }

    #[test]
    fn runtime_rejects_invalid_layer_and_writes_nothing() {
        let _tree = super::tree_lock();
        let td = tempfile::tempdir().unwrap();
        let dir = td.path().join("runtime");
        let dir_s = dir.to_string_lossy().to_string();
        let (o, d) = run(
            td.path(),
            Some("[theme.tmux]\nstyle = \"bogus\"\n"),
            &["--runtime", &dir_s],
        );
        assert!(!o.status.success(), "invalid user-tier style must fail");
        assert!(String::from_utf8_lossy(&o.stderr).contains("style must be"));
        assert!(!d.join("tmux.conf").exists(), "no half-projected tmux.conf");
        assert!(
            !d.join("mios.omp.json").exists(),
            "no half-projected prompt"
        );
    }

    #[test]
    fn runtime_conflicts_with_check_modes() {
        let _tree = super::tree_lock();
        let td = tempfile::tempdir().unwrap();
        let dir_s = td.path().join("runtime").to_string_lossy().to_string();
        let (o, _) = run(td.path(), None, &["--runtime", &dir_s, "--check"]);
        assert_eq!(o.status.code(), Some(2), "clap usage error expected");
    }

    #[cfg(unix)]
    #[test]
    fn runtime_refuses_symlinked_directory() {
        let _tree = super::tree_lock();
        let td = tempfile::tempdir().unwrap();
        let real = td.path().join("real");
        fs::create_dir(&real).unwrap();
        let link = td.path().join("link");
        std::os::unix::fs::symlink(&real, &link).unwrap();
        let link_s = link.to_string_lossy().to_string();
        let (o, _) = run(td.path(), None, &["--runtime", &link_s]);
        assert!(
            !o.status.success(),
            "symlinked projection dir must be refused"
        );
        assert!(!real.join("tmux.conf").exists());
    }
}

mod tmux_theme {
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
    fn test_render_tmux_theme_cli_e2e() {
        let _tree = super::tree_lock();
        let root = get_repo_root();
        let bin_path = bin();

        // 1. Positive control: standard check mode passes with exit code 0
        let output = Command::new(bin_path)
            .arg("render-tmux-theme")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute mios-gen render-tmux-theme --check");

        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(
            output.status.success(),
            "Expected exit code 0, got: {:?}\nstdout: {}\nstderr: {}",
            output.status.code(),
            stdout,
            stderr
        );
        assert!(
            stdout.contains("[tmux-theme] tmux theme matches SSOT (rounded)"),
            "stdout should confirm match: {}",
            stdout
        );

        // 2. Positive control: legacy alias --check-fixture passes with exit code 0
        let fixture_output = Command::new(bin_path)
            .arg("render-tmux-theme")
            .arg("--check-fixture")
            .arg(&root)
            .output()
            .expect("Failed to execute mios-gen render-tmux-theme --check-fixture");

        assert!(
            fixture_output.status.success(),
            "Expected --check-fixture to exit 0"
        );

        // 3. Positive control: JSON output format
        let json_output = Command::new(bin_path)
            .arg("--format")
            .arg("json")
            .arg("render-tmux-theme")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute mios-gen --format json render-tmux-theme --check");

        assert!(
            json_output.status.success(),
            "Expected json check to exit 0"
        );
        let json_str = String::from_utf8_lossy(&json_output.stdout);
        assert!(
            json_str.contains("\"status\": \"clean\""),
            "JSON output should indicate clean status: {}",
            json_str
        );
        assert!(
            json_str.contains("\"violations\": 0"),
            "JSON output should indicate 0 violations: {}",
            json_str
        );

        // 4. Negative control: planted modification in target file triggers error exit code 1
        let target = root.join("usr/share/mios/tmux/mios-theme.tmux.conf");
        assert!(target.is_file(), "Target mios-theme.tmux.conf must exist");

        {
            let _guard = Restorer::new(&target);

            // Mutate target file
            let mut corrupted = fs::read_to_string(&target).unwrap();
            corrupted.push_str("\n# DEVLOOP-PLANTED-MUTATION\nset -g status off\n");
            fs::write(&target, corrupted.as_bytes()).unwrap();

            let fail_output = Command::new(bin_path)
                .arg("render-tmux-theme")
                .arg("--root")
                .arg(&root)
                .arg("--check")
                .output()
                .expect("Failed to execute corrupted check");

            assert_eq!(
                fail_output.status.code(),
                Some(1),
                "Expected exit code 1 on mutated target file"
            );
            let fail_stderr = String::from_utf8_lossy(&fail_output.stderr);
            assert!(
                fail_stderr.contains("mios-theme.tmux.conf: out of sync"),
                "stderr should name the out of sync file: {}",
                fail_stderr
            );
        }

        // 5. Verification after restore
        let restored_output = Command::new(bin_path)
            .arg("render-tmux-theme")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute check after restore");

        assert!(
            restored_output.status.success(),
            "Target must pass check after guard restored original content"
        );
    }
}
