// AI-hint: Two-sided integration tests for the mios-gen image and supply-chain verbs: bib-configs, cargo-manifests, cosign-policy, egress-firewall and pod-quadlets (ADR-0021, Law 14).
// AI-doc: usr/share/doc/mios/manual/tools.md
// AI-related: tools/native/mios-gen/src/image.rs, automation/98-drift-checks.sh, tests/drift-gate-negatives.sh, tools/native/mios-gen/src/main.rs, usr/share/mios/mios.toml, docs/design/doc-rust-static-port.md, tools/native/mios-gen/src/pod_quadlets.rs

use std::sync::{Mutex, MutexGuard};

/// One test at a time: several modules edit real-tree files under a restore guard.
fn tree_lock() -> MutexGuard<'static, ()> {
    static LOCK: Mutex<()> = Mutex::new(());
    LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

mod bib_configs {
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
    fn test_bib_configs_cli_e2e() {
        let _tree = super::tree_lock();
        let root = get_repo_root();
        let bin_path = bin();

        // 1. Positive control: standard check mode passes with exit code 0
        let output = Command::new(bin_path)
            .arg("bib-configs")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute mios-gen bib-configs --check");

        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);

        assert!(
            output.status.success(),
            "bib-configs --check failed with code {:?}\nstdout: {}\nstderr: {}",
            output.status.code(),
            stdout,
            stderr
        );
        assert!(
            stdout.contains("PASS: BIB artifact configs in sync with mios.toml SSOT."),
            "Expected success message not found in stdout: {}",
            stdout
        );

        // 2. Positive control: structured JSON output format
        let output_json = Command::new(bin_path)
            .arg("--format")
            .arg("json")
            .arg("bib-configs")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute mios-gen --format json bib-configs --check");

        assert!(
            output_json.status.success(),
            "bib-configs --format json --check failed"
        );
        let json_val: serde_json::Value =
            serde_json::from_slice(&output_json.stdout).expect("Failed to parse JSON output");
        assert_eq!(json_val["status"], "clean");
        assert_eq!(json_val["subcommand"], "bib-configs");
        assert_eq!(json_val["violations"], 0);

        // 3. Negative control: mutate config/artifacts/bib.toml minsize
        let bib_path = root.join("config/artifacts/bib.toml");
        {
            let _restorer = Restorer::new(&bib_path);
            let orig = fs::read_to_string(&bib_path).expect("read bib.toml");
            let mutated = orig.replace("80 GiB", "999 GiB");
            fs::write(&bib_path, mutated.as_bytes()).expect("write mutated bib.toml");

            let neg_output = Command::new(bin_path)
                .arg("bib-configs")
                .arg("--root")
                .arg(&root)
                .arg("--check")
                .output()
                .expect("Failed to execute mios-gen bib-configs negative control");

            assert!(
                !neg_output.status.success(),
                "Expected failure on mutated bib.toml, but got exit code 0"
            );
            let neg_stderr = String::from_utf8_lossy(&neg_output.stderr);
            assert!(
                neg_stderr.contains("ERROR: BIB artifact configs out of sync"),
                "Expected out of sync error message in stderr, got: {}",
                neg_stderr
            );
        }

        // 4. Negative control: mutate config/artifacts/iso.toml minsize
        let iso_path = root.join("config/artifacts/iso.toml");
        {
            let _restorer = Restorer::new(&iso_path);
            let orig = fs::read_to_string(&iso_path).expect("read iso.toml");
            let mutated = orig.replace("150 GiB", "999 GiB");
            fs::write(&iso_path, mutated.as_bytes()).expect("write mutated iso.toml");

            let neg_output = Command::new(bin_path)
                .arg("bib-configs")
                .arg("--root")
                .arg(&root)
                .arg("--check")
                .output()
                .expect("Failed to execute mios-gen bib-configs negative control");

            assert!(
                !neg_output.status.success(),
                "Expected failure on mutated iso.toml, but got exit code 0"
            );
            let neg_stderr = String::from_utf8_lossy(&neg_output.stderr);
            assert!(
                neg_stderr.contains("ERROR: BIB artifact configs out of sync"),
                "Expected out of sync error message in stderr, got: {}",
                neg_stderr
            );
        }

        // Verify post-restoration check passes
        let restored_output = Command::new(bin_path)
            .arg("bib-configs")
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

mod cargo_manifests {
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
    fn test_cargo_manifests_cli_e2e() {
        let _tree = super::tree_lock();
        let source = get_repo_root();
        let fixture = tempfile::tempdir().expect("create isolated manifest fixture");
        let root = fixture.path().to_path_buf();
        // Negative controls must never remove a member from the workspace that
        // another build or test is reading. Only manifests participate in this
        // projection, so retain the real inputs in a private temporary tree.
        for relative in [
            "VERSION",
            "usr/share/mios/mios.toml",
            "tools/native/Cargo.toml",
        ] {
            let destination = root.join(relative);
            fs::create_dir_all(destination.parent().expect("fixture parent")).unwrap();
            fs::copy(source.join(relative), destination).expect("copy projection input");
        }
        for entry in fs::read_dir(source.join("tools/native")).expect("native members") {
            let entry = entry.expect("native member");
            let manifest = entry.path().join("Cargo.toml");
            if manifest.is_file() {
                let destination = root.join("tools/native").join(entry.file_name());
                fs::create_dir_all(&destination).expect("fixture member");
                fs::copy(manifest, destination.join("Cargo.toml")).expect("copy member manifest");
            }
        }
        let bin_path = bin();

        // 1. Positive control: standard check mode passes with exit code 0
        let output = Command::new(bin_path)
            .arg("cargo-manifests")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute mios-gen cargo-manifests --check");

        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);

        assert!(
            output.status.success(),
            "cargo-manifests --check failed with code {:?}\nstdout: {}\nstderr: {}",
            output.status.code(),
            stdout,
            stderr
        );
        assert!(
            stdout.contains("PASS: tools/native/Cargo.toml matches its generator projection."),
            "Expected success message not found in stdout: {}",
            stdout
        );

        // 2. Positive control: structured JSON output format
        let output_json = Command::new(bin_path)
            .arg("--format")
            .arg("json")
            .arg("cargo-manifests")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute mios-gen --format json cargo-manifests --check");

        assert!(
            output_json.status.success(),
            "cargo-manifests --format json --check failed"
        );
        let json_val: serde_json::Value =
            serde_json::from_slice(&output_json.stdout).expect("Failed to parse JSON output");
        assert_eq!(json_val["status"], "clean");
        assert_eq!(json_val["subcommand"], "cargo-manifests");
        assert_eq!(json_val["violations"], 0);

        // 3. Negative control: mutate tools/native/Cargo.toml by removing a workspace member
        let manifest_path = root.join("tools/native/Cargo.toml");
        {
            let _restorer = Restorer::new(&manifest_path);
            let orig = fs::read_to_string(&manifest_path).expect("read Cargo.toml");
            assert!(orig.contains("\"xtask\","), "Target member xtask not found");
            let mutated = orig.replace("    \"xtask\",\n", "");
            fs::write(&manifest_path, mutated.as_bytes()).expect("write mutated Cargo.toml");

            let neg_output = Command::new(bin_path)
                .arg("cargo-manifests")
                .arg("--root")
                .arg(&root)
                .arg("--check")
                .output()
                .expect("Failed to execute mios-gen cargo-manifests negative control");

            assert!(
                !neg_output.status.success(),
                "Expected failure on mutated Cargo.toml (dropped member), but got exit code 0"
            );
            let neg_stderr = String::from_utf8_lossy(&neg_output.stderr);
            assert!(
            neg_stderr.contains("[generate-cargo-manifests] FAIL: tools/native/Cargo.toml differs from its projection"),
            "Expected FAIL message in stderr, got: {}",
            neg_stderr
        );
        }

        // 4. Negative control: mutate tools/native/Cargo.toml by altering package version
        {
            let _restorer = Restorer::new(&manifest_path);
            let orig = fs::read_to_string(&manifest_path).expect("read Cargo.toml");
            assert!(
                orig.contains("version = \"0.3.0\""),
                "Target version not found"
            );
            let mutated = orig.replace("version = \"0.3.0\"", "version = \"9.9.9\"");
            fs::write(&manifest_path, mutated.as_bytes()).expect("write mutated Cargo.toml");

            let neg_output = Command::new(bin_path)
                .arg("cargo-manifests")
                .arg("--root")
                .arg(&root)
                .arg("--check")
                .output()
                .expect("Failed to execute mios-gen cargo-manifests negative control");

            assert!(
                !neg_output.status.success(),
                "Expected failure on mutated Cargo.toml (version drift), but got exit code 0"
            );
            let neg_stderr = String::from_utf8_lossy(&neg_output.stderr);
            assert!(
            neg_stderr.contains("[generate-cargo-manifests] FAIL: tools/native/Cargo.toml differs from its projection"),
            "Expected FAIL message in stderr, got: {}",
            neg_stderr
        );
        }

        // Verify post-restoration check passes
        let restored_output = Command::new(bin_path)
            .arg("cargo-manifests")
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

mod cosign_policy {
    use std::fs;
    use std::process::Command;

    fn bin() -> &'static str {
        env!("CARGO_BIN_EXE_mios-gen")
    }

    const SOUND: &str = "[image]\nname = \"ghcr.io/mios-dev/mios\"\n[security.sigstore]\n\
        policy_mode = \"insecureAcceptAnything\"\n[[security.sigstore.scopes]]\ntransport = \"docker\"\n\
        scope = \"ghcr.io/mios-dev\"\ntype = \"sigstoreSigned\"\n\
        key_paths = [\"/usr/share/pki/containers/mios-cosign.pub\"]\n";

    fn cosign(root: &std::path::Path, check: bool) -> std::process::Output {
        let mut c = Command::new(bin());
        c.arg("cosign-policy").arg("--root").arg(root);
        if check {
            c.arg("--check");
        }
        c.output().expect("must execute mios-gen")
    }

    #[test]
    fn test_cosign_policy_render_and_check() {
        let _tree = super::tree_lock();
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let toml_dir = root.join("usr/share/mios");
        fs::create_dir_all(&toml_dir).unwrap();
        fs::write(toml_dir.join("mios.toml"), SOUND).unwrap();

        // 1. Generation mode
        let out = cosign(root, false);
        assert_eq!(out.status.code(), Some(0), "{}", String::from_utf8_lossy(&out.stderr));
        let policy_path = root.join("usr/lib/containers/policy.json");
        let content = fs::read_to_string(&policy_path).unwrap();
        let expected = "{\n  \"default\": [\n    {\n      \"type\": \"insecureAcceptAnything\"\n    }\n  ],\n  \"transports\": {\n    \"docker\": {\n      \"ghcr.io/mios-dev\": [\n        {\n          \"keyPath\": \"/usr/share/pki/containers/mios-cosign.pub\",\n          \"signedIdentity\": {\n            \"type\": \"matchRepository\"\n          },\n          \"type\": \"sigstoreSigned\"\n        }\n      ]\n    }\n  }\n}\n";
        assert_eq!(content, expected);

        // 2. Check mode - clean
        let out = cosign(root, true);
        assert_eq!(out.status.code(), Some(0));
        let stdout = String::from_utf8_lossy(&out.stdout);
        assert!(stdout.contains("[OK] usr/lib/containers/policy.json is in sync with SSOT"));

        // 3. Check mode - tampered
        fs::write(&policy_path, "{}").unwrap();
        let out = cosign(root, true);
        assert_eq!(out.status.code(), Some(1));
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert!(stderr.contains("out of sync"));
    }

    /// The renderer is the first of three refusals (gate, bake): an unsound
    /// SSOT exits 1 and writes nothing, so no policy can ship that names an
    /// invalid type or accepts a MiOS image unsigned.
    #[test]
    fn test_unsound_policies_are_refused_and_not_written() {
        let _tree = super::tree_lock();
        for (label, ssot, needle) in [
            ("invalid type", SOUND.replace("policy_mode = \"insecureAcceptAnything\"", "policy_mode = \"insecureAcceptEverything\""), "insecureAcceptEverything"),
            ("no MiOS scope", "[image]\nname = \"ghcr.io/mios-dev/mios\"\n[security.sigstore]\npolicy_mode = \"insecureAcceptAnything\"\n".to_string(), "MiOS-owned"),
            ("unsigned MiOS scope", SOUND.replace("type = \"sigstoreSigned\"\n", "type = \"insecureAcceptAnything\"\n").replace("key_paths = [\"/usr/share/pki/containers/mios-cosign.pub\"]\n", ""), "MiOS-owned"),
        ] {
            let dir = tempfile::tempdir().unwrap();
            let root = dir.path();
            fs::create_dir_all(root.join("usr/share/mios")).unwrap();
            fs::write(root.join("usr/share/mios/mios.toml"), &ssot).unwrap();
            let out = cosign(root, false);
            let stderr = String::from_utf8_lossy(&out.stderr);
            assert_eq!(out.status.code(), Some(1), "{label}: {stderr}");
            assert!(stderr.contains(needle), "{label}: {stderr}");
            assert!(!root.join("usr/lib/containers/policy.json").exists(), "{label}: an unsound policy was written");
        }
    }

    #[test]
    fn test_missing_sigstore_table_fails() {
        let _tree = super::tree_lock();
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let toml_dir = root.join("usr/share/mios");
        fs::create_dir_all(&toml_dir).unwrap();
        fs::write(toml_dir.join("mios.toml"), "[meta]\nname = \"test\"\n").unwrap();

        let out = Command::new(bin())
            .arg("cosign-policy")
            .arg("--root")
            .arg(root)
            .output()
            .expect("must execute mios-gen");
        assert_eq!(out.status.code(), Some(1));
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert!(stderr.contains("no [security.sigstore] table"));
    }
}

mod egress_firewall {
    use std::fs;
    use std::process::Command;

    fn bin() -> &'static str {
        env!("CARGO_BIN_EXE_mios-gen")
    }

    #[test]
    fn test_egress_firewall_generation_modes() {
        let _tree = super::tree_lock();
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let toml_dir = root.join("usr/share/mios");
        fs::create_dir_all(&toml_dir).unwrap();

        let service_dir = root.join("usr/lib/systemd/system");
        fs::create_dir_all(&service_dir).unwrap();
        fs::write(
            service_dir.join("mios-agent-pipe.service"),
            "[Unit]\nDescription=Agent Pipe\n[Service]\nUser=test-agent\nExecStart=/usr/bin/true\n",
        )
        .unwrap();

        // Mode: enforce with IP allowlist
        let toml_content = r#"
[security.egress]
mode = "enforce"
allow = ["1.1.1.1", "2001:db8::1", "8.8.8.8"]
"#;
        fs::write(toml_dir.join("mios.toml"), toml_content).unwrap();

        let out = Command::new(bin())
            .arg("egress-firewall")
            .arg("--root")
            .arg(root)
            .output()
            .expect("must execute mios-gen");
        assert_eq!(out.status.code(), Some(0));

        let nft_path = root.join("usr/share/mios/security/egress.nft");
        assert!(nft_path.is_file());
        let content = fs::read_to_string(&nft_path).unwrap();

        assert!(content.contains("meta skuid != \"test-agent\" accept"));
        assert!(content.contains("ip daddr { 1.1.1.1, 8.8.8.8 } accept"));
        assert!(content.contains("ip6 daddr { 2001:db8::1 } accept"));
        assert!(content.contains("log prefix \"mios-egress-drop \" drop"));
        assert!(content
            .contains("ENFORCE: the agent's non-allowed external egress is logged + DROPPED."));

        // Mode: audit
        let toml_audit = r#"
[security.egress]
mode = "audit"
"#;
        fs::write(toml_dir.join("mios.toml"), toml_audit).unwrap();
        let out = Command::new(bin())
            .arg("egress-firewall")
            .arg("--root")
            .arg(root)
            .output()
            .expect("must execute mios-gen");
        assert_eq!(out.status.code(), Some(0));

        let content_audit = fs::read_to_string(&nft_path).unwrap();
        assert!(content_audit.contains("log prefix \"mios-egress-audit \" accept"));
        assert!(
            content_audit.contains("AUDIT: the agent's external egress is LOGGED then accepted")
        );

        // Mode: off
        let toml_off = r#"
[security.egress]
mode = "off"
"#;
        fs::write(toml_dir.join("mios.toml"), toml_off).unwrap();
        let out = Command::new(bin())
            .arg("egress-firewall")
            .arg("--root")
            .arg(root)
            .output()
            .expect("must execute mios-gen");
        assert_eq!(out.status.code(), Some(0));

        let content_off = fs::read_to_string(&nft_path).unwrap();
        assert!(content_off.contains("accept   # mode=off -> no-op even if applied"));
        assert!(content_off.contains("OFF: informational ruleset; applying it changes nothing."));
    }

    #[test]
    fn test_malformed_toml_fails() {
        let _tree = super::tree_lock();
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let toml_dir = root.join("usr/share/mios");
        fs::create_dir_all(&toml_dir).unwrap();

        let toml_content = "invalid_toml = [unclosed";
        fs::write(toml_dir.join("mios.toml"), toml_content).unwrap();

        let out = Command::new(bin())
            .arg("egress-firewall")
            .arg("--root")
            .arg(root)
            .output()
            .expect("must execute mios-gen");
        assert_ne!(out.status.code(), Some(0));
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert!(stderr.contains("Failed to parse") || stderr.contains("Error:"));
    }

    #[test]
    fn test_egress_firewall_json_format() {
        let _tree = super::tree_lock();
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let toml_dir = root.join("usr/share/mios");
        fs::create_dir_all(&toml_dir).unwrap();
        let toml_off = r#"
[security.egress]
mode = "off"
"#;
        fs::write(toml_dir.join("mios.toml"), toml_off).unwrap();
        let out = Command::new(bin())
            .arg("--format")
            .arg("json")
            .arg("egress-firewall")
            .arg("--root")
            .arg(root)
            .output()
            .expect("must execute mios-gen");
        assert_eq!(out.status.code(), Some(0));
        let stdout = String::from_utf8_lossy(&out.stdout);
        assert!(stdout.contains("\"status\": \"clean\""));
        assert!(stdout.contains("\"subcommand\": \"egress-firewall\""));
    }

    #[test]
    fn check_detects_drift_and_missing_output_without_writing() {
        let _tree = super::tree_lock();
        let temp = tempfile::tempdir().unwrap();
        let root = temp.path();
        fs::create_dir_all(root.join("usr/share/mios")).unwrap();
        fs::write(
            root.join("usr/share/mios/mios.toml"),
            "[security.egress]\nmode='audit'\n",
        )
        .unwrap();
        let run = |check: bool| {
            let mut command = Command::new(bin());
            command
                .arg("egress-firewall")
                .arg("--root")
                .arg(root)
                .env_remove("MIOS_TOML")
                .env_remove("MIOS_EGRESS_OUT")
                .env_remove("MIOS_AGENT_USER");
            if check {
                command.arg("--check");
            }
            command.output().unwrap()
        };
        let nft = root.join("usr/share/mios/security/egress.nft");
        let missing = run(true);
        assert!(!missing.status.success());
        assert!(String::from_utf8_lossy(&missing.stderr).contains("cannot compare"));
        assert!(!nft.exists());
        assert!(run(false).status.success());
        let clean = fs::read(&nft).unwrap();
        assert!(run(true).status.success());
        assert_eq!(fs::read(&nft).unwrap(), clean);
        fs::write(&nft, "planted firewall drift").unwrap();
        let drift = run(true);
        assert!(!drift.status.success());
        assert!(String::from_utf8_lossy(&drift.stderr).contains("is stale"));
        assert_eq!(fs::read_to_string(&nft).unwrap(), "planted firewall drift");
        assert!(run(false).status.success());
        assert_eq!(fs::read(&nft).unwrap(), clean);
    }
}

mod pod_quadlets {
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
    fn test_pod_quadlets_cli_e2e() {
        let _tree = super::tree_lock();
        let root = get_repo_root();
        let bin_path = bin();

        // 1. Positive control: standard check mode passes with exit code 0
        let output = Command::new(bin_path)
            .arg("pod-quadlets")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute mios-gen pod-quadlets --check");

        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);
        assert!(
            output.status.success(),
            "Expected exit code 0, got: {:?}\nstdout: {}\nstderr: {}",
            output.status.code(),
            stdout,
            stderr
        );
        let doc: toml::Value = fs::read_to_string(root.join("usr/share/mios/mios.toml"))
            .expect("read SSOT")
            .parse()
            .expect("parse SSOT");
        let enabled = &doc["quadlets"]["enable"];
        let expected: usize = ["pods", "containers", "networks", "volumes", "images"]
            .iter()
            .map(|kind| {
                doc.get(kind)
                    .and_then(toml::Value::as_table)
                    .map_or(0, |table| {
                        table
                            .keys()
                            .filter(|name| {
                                *kind != "containers"
                                    || enabled.get(*name).and_then(toml::Value::as_bool)
                                        != Some(false)
                            })
                            .count()
                    })
            })
            .sum();
        assert!(expected > 0, "the SSOT must declare Quadlet subjects");
        assert!(
            stdout.contains(&format!(
                "[pod-gen] all {expected} Quadlet unit(s) match SSOT"
            )),
            "stdout must confirm the SSOT roster of {expected} units: {stdout}"
        );

        // 2. Positive control: list mode outputs the declared system-scope units
        let list_output = Command::new(bin_path)
            .arg("pod-quadlets")
            .arg("--root")
            .arg(&root)
            .arg("--list")
            .output()
            .expect("Failed to execute mios-gen pod-quadlets --list");

        assert!(list_output.status.success(), "Expected list mode to exit 0");
        let list_stdout = String::from_utf8_lossy(&list_output.stdout);
        assert!(
            list_stdout.contains("mios-ai.pod"),
            "list output should contain mios-ai.pod"
        );
        assert!(
            list_stdout.contains("bootc-image-builder.image"),
            "list output should contain bootc-image-builder.image"
        );
        assert!(
            list_stdout.contains("mios.network"),
            "list output should contain mios.network"
        );
        assert!(
            list_stdout.contains("mios-pgvector.container"),
            "list output should contain mios-pgvector.container"
        );

        // 3. Positive control: structured JSON check mode
        let json_output = Command::new(bin_path)
            .arg("--format")
            .arg("json")
            .arg("pod-quadlets")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute mios-gen --format json pod-quadlets --check");

        assert!(
            json_output.status.success(),
            "Expected JSON check to exit 0"
        );
        let json_val: serde_json::Value =
            serde_json::from_slice(&json_output.stdout).expect("Failed to parse JSON output");
        assert_eq!(json_val["status"], "clean");
        assert_eq!(json_val["subcommand"], "pod-quadlets");
        assert_eq!(json_val["violations"], 0);

        // 4. Negative control: mutate a quadlet file and verify check mode detects drift
        let quadlet_path = root.join("usr/share/containers/systemd/mios-pgvector.container");
        assert!(quadlet_path.exists(), "Target quadlet file must exist");
        {
            let _restorer = Restorer::new(&quadlet_path);
            let mut mutated = fs::read_to_string(&quadlet_path).expect("read quadlet");
            mutated.push_str("\n# planted drift line\n");
            fs::write(&quadlet_path, &mutated).expect("write mutated quadlet");

            let drift_output = Command::new(bin_path)
                .arg("pod-quadlets")
                .arg("--root")
                .arg(&root)
                .arg("--check")
                .output()
                .expect("Failed to execute mios-gen pod-quadlets --check");

            assert_eq!(
                drift_output.status.code(),
                Some(1),
                "Expected exit code 1 for drifted quadlet"
            );
            let drift_stderr = String::from_utf8_lossy(&drift_output.stderr);
            assert!(
                drift_stderr.contains("DRIFT") && drift_stderr.contains("mios-pgvector.container"),
                "stderr should report drift on mutated file: {}",
                drift_stderr
            );
        }

        // 5. Negative control: un-generated orphan file in SSOT dir
        let orphan_path = root.join("usr/share/containers/systemd/zz-orphan-test.container");
        fs::write(&orphan_path, "[Container]\nImage=alpine\n").expect("write orphan");
        {
            let orphan_output = Command::new(bin_path)
                .arg("pod-quadlets")
                .arg("--root")
                .arg(&root)
                .arg("--check")
                .output()
                .expect("Failed to execute mios-gen pod-quadlets --check");

            let _ = fs::remove_file(&orphan_path);

            assert_eq!(
                orphan_output.status.code(),
                Some(1),
                "Expected exit code 1 for orphan file"
            );
            let orphan_stderr = String::from_utf8_lossy(&orphan_output.stderr);
            assert!(
                orphan_stderr.contains(
                    "DRIFT: un-generated orphan Quadlet unit in SSOT dir: zz-orphan-test.container"
                ),
                "stderr should report orphan file: {}",
                orphan_stderr
            );
        }

        // 6. Negative control 1: Planted MIOS_PG_PASS fails closed with Law 11 violation
        {
            let secret_output = Command::new(bin_path)
                .arg("pod-quadlets")
                .arg("--root")
                .arg(&root)
                .arg("--check")
                .env("MIOS_PG_PASS", "planted-secret-123")
                .output()
                .expect("Failed to execute mios-gen pod-quadlets with planted MIOS_PG_PASS");

            assert_eq!(
                secret_output.status.code(),
                Some(1),
                "Expected exit code 1 for planted secret"
            );
            let secret_stderr = String::from_utf8_lossy(&secret_output.stderr);
            assert!(
                secret_stderr.contains("Law 11") && secret_stderr.contains("POSTGRES"),
                "stderr should report Law 11 violation for planted secret: {}",
                secret_stderr
            );
        }

        // 7. Negative control 2: Planted off-roster MIOS_ADGUARD_UID=0 fails closed with Law 6 violation
        {
            let u0_output = Command::new(bin_path)
                .arg("pod-quadlets")
                .arg("--root")
                .arg(&root)
                .arg("--check")
                .env("MIOS_ADGUARD_UID", "0")
                .output()
                .expect("Failed to execute mios-gen pod-quadlets with planted MIOS_ADGUARD_UID=0");

            assert_eq!(
                u0_output.status.code(),
                Some(1),
                "Expected exit code 1 for planted off-roster UID=0"
            );
            let u0_stderr = String::from_utf8_lossy(&u0_output.stderr);
            assert!(
                u0_stderr.contains("Law 6") && u0_stderr.contains("User=0"),
                "stderr should report Law 6 violation for planted UID=0: {}",
                u0_stderr
            );
        }

        // 8. Positive control: Planted authorized root UID=0 (MIOS_CEPH_UID=0) passes
        {
            let ceph_output = Command::new(bin_path)
                .arg("pod-quadlets")
                .arg("--root")
                .arg(&root)
                .arg("--check")
                .env("MIOS_CEPH_UID", "0")
                .output()
                .expect("Failed to execute mios-gen pod-quadlets with planted MIOS_CEPH_UID=0");

            assert!(
                ceph_output.status.success(),
                "Expected exit code 0 for authorized root container"
            );
        }
    }
}
