// AI-hint: Two-sided integration tests for the mios-gen documentation verbs: adr-index, metal-vs-hosted, render-manpages, roadmap-index, standardize-docs and sync-wiki (ADR-0021, Law 14).
// AI-doc: usr/share/doc/mios/manual/tools.md
// AI-related: tools/native/mios-gen/src/indexes.rs, usr/share/doc/mios/adr/, docs/design/doc-rust-static-port.md, tools/native/mios-gen/src/metal_vs_hosted.rs, usr/share/doc/mios/reference/metal-vs-hosted.md, tools/native/mios-gen/src/render.rs, automation/98-drift-checks.sh, tests/drift-gate-negatives.sh, ROADMAP.md, tools/native/mios-gen/src/docs.rs

use std::sync::{Mutex, MutexGuard};

/// One test at a time: several modules edit real-tree files under a restore guard.
fn tree_lock() -> MutexGuard<'static, ()> {
    static LOCK: Mutex<()> = Mutex::new(());
    LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

mod adr_index {
    use std::fs;
    use std::process::Command;

    fn bin() -> &'static str {
        env!("CARGO_BIN_EXE_mios-gen")
    }

    fn write_test_ssot(root: &std::path::Path, meta: bool, dotfiles: bool, sha256_in_image: bool) {
        let mios_dir = root.join("usr/share/mios");
        fs::create_dir_all(&mios_dir).unwrap();
        let mut toml = String::new();
        if meta {
            toml.push_str("[meta]\nmios_version = \"0.3.0\"\n\n");
        }
        if dotfiles {
            toml.push_str("[dotfiles]\nregistry = [\"bash\", \"git\"]\n\n");
        }
        if sha256_in_image {
            toml.push_str("[image]\nref = \"ghcr.io/example/core@sha256:1234567890abcdef\"\n\n");
        } else {
            toml.push_str("[image]\nref = \"ghcr.io/example/core:latest\"\n\n");
        }
        fs::write(mios_dir.join("mios.toml"), toml).unwrap();
    }

    #[test]
    fn test_adr_index_render_and_check() {
        let _tree = super::tree_lock();
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let adr_dir = root.join("usr/share/doc/mios/adr");
        fs::create_dir_all(&adr_dir).unwrap();
        write_test_ssot(root, true, true, false);

        let adr1 = r#"<!-- AI-hint: ADR-0001 -->
---
adr: 0001
title: First Decision
status: accepted
date: 2026-07-12
laws: [1, 7]
ssot_keys: [build.bake, image.sidecars]
---

# 1. Context
First decision details.
"#;
        let adr2 = r#"<!-- AI-hint: ADR-0002 -->
---
adr: 0002
title: Second Decision
status: proposed
date: 2026-08-01
laws: [5]
ssot_keys: []
---

# 1. Context
Second decision details.
"#;
        fs::write(adr_dir.join("0001-first-decision.md"), adr1).unwrap();
        fs::write(adr_dir.join("0002-second-decision.md"), adr2).unwrap();

        // 1. Generation mode
        let out = Command::new(bin())
            .arg("adr-index")
            .arg("--root")
            .arg(root)
            .output()
            .expect("must execute mios-gen");
        assert_eq!(out.status.code(), Some(0));

        let adr_md = root.join("ADR.md");
        assert!(adr_md.is_file());
        let content = fs::read_to_string(&adr_md).unwrap();
        assert!(content.contains("**2 ADRs** (1 accepted)"));
        assert!(content.contains("| 0001 | [First Decision](usr/share/doc/mios/adr/0001-first-decision.md) | accepted | 2026-07-12 | 1, 7 | `build.bake`, `image.sidecars` |"));
        assert!(content.contains("| 0002 | [Second Decision](usr/share/doc/mios/adr/0002-second-decision.md) | proposed | 2026-08-01 | 5 | -- |"));

        // 2. Check mode - clean
        let out = Command::new(bin())
            .arg("adr-index")
            .arg("--root")
            .arg(root)
            .arg("--check")
            .output()
            .expect("must execute mios-gen");
        assert_eq!(out.status.code(), Some(0));
        let stdout = String::from_utf8_lossy(&out.stdout);
        assert!(stdout.contains("matches the 2 baked ADR(s) and SSOT consistency checks pass"));

        // 3. Check mode - stale after tampering
        fs::write(&adr_md, content + "\n<!-- hand edit -->\n").unwrap();
        let out = Command::new(bin())
            .arg("adr-index")
            .arg("--root")
            .arg(root)
            .arg("--check")
            .output()
            .expect("must execute mios-gen");
        assert_eq!(out.status.code(), Some(1));
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert!(stderr.contains("is stale -- run mios-gen adr-index"));
    }

    #[test]
    fn test_adr_index_negative_missing_front_matter() {
        let _tree = super::tree_lock();
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let adr_dir = root.join("usr/share/doc/mios/adr");
        fs::create_dir_all(&adr_dir).unwrap();
        write_test_ssot(root, true, true, false);

        let malformed_adr = r#"# Missing front matter
No YAML front matter here.
"#;
        fs::write(adr_dir.join("0003-malformed.md"), malformed_adr).unwrap();

        let out = Command::new(bin())
            .arg("adr-index")
            .arg("--root")
            .arg(root)
            .output()
            .expect("must execute mios-gen");
        assert_eq!(out.status.code(), Some(1));
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert!(stderr.contains("VIOLATION: usr/share/doc/mios/adr/0003-malformed.md has no `adr:` front-matter -- add it or rename"));
    }

    #[test]
    fn test_adr_index_negative_ssot_consistency() {
        let _tree = super::tree_lock();
        // 1. Missing meta.mios_version
        {
            let dir = tempfile::tempdir().unwrap();
            let root = dir.path();
            let adr_dir = root.join("usr/share/doc/mios/adr");
            fs::create_dir_all(&adr_dir).unwrap();
            write_test_ssot(root, false, true, false);
            fs::write(
                adr_dir.join("0001-first.md"),
                "---\nadr: 0001\ntitle: X\nstatus: accepted\n---\n",
            )
            .unwrap();

            let _ = Command::new(bin())
                .arg("adr-index")
                .arg("--root")
                .arg(root)
                .output()
                .unwrap();
            let out = Command::new(bin())
                .arg("adr-index")
                .arg("--root")
                .arg(root)
                .arg("--check")
                .output()
                .unwrap();
            assert_eq!(out.status.code(), Some(1));
            let stderr = String::from_utf8_lossy(&out.stderr);
            assert!(
                stderr.contains("ADR-0009: mios.toml missing [meta].mios_version SSOT declaration")
            );
        }

        // 2. Missing dotfiles
        {
            let dir = tempfile::tempdir().unwrap();
            let root = dir.path();
            let adr_dir = root.join("usr/share/doc/mios/adr");
            fs::create_dir_all(&adr_dir).unwrap();
            write_test_ssot(root, true, false, false);
            fs::write(
                adr_dir.join("0001-first.md"),
                "---\nadr: 0001\ntitle: X\nstatus: accepted\n---\n",
            )
            .unwrap();

            let _ = Command::new(bin())
                .arg("adr-index")
                .arg("--root")
                .arg(root)
                .output()
                .unwrap();
            let out = Command::new(bin())
                .arg("adr-index")
                .arg("--root")
                .arg(root)
                .arg("--check")
                .output()
                .unwrap();
            assert_eq!(out.status.code(), Some(1));
            let stderr = String::from_utf8_lossy(&out.stderr);
            assert!(
                stderr.contains("ADR-0010: mios.toml missing or empty [dotfiles] table registry")
            );
        }

        // 3. Hardcoded @sha256: digest in [image]
        {
            let dir = tempfile::tempdir().unwrap();
            let root = dir.path();
            let adr_dir = root.join("usr/share/doc/mios/adr");
            fs::create_dir_all(&adr_dir).unwrap();
            write_test_ssot(root, true, true, true);
            fs::write(
                adr_dir.join("0001-first.md"),
                "---\nadr: 0001\ntitle: X\nstatus: accepted\n---\n",
            )
            .unwrap();

            let _ = Command::new(bin())
                .arg("adr-index")
                .arg("--root")
                .arg(root)
                .output()
                .unwrap();
            let out = Command::new(bin())
                .arg("adr-index")
                .arg("--root")
                .arg(root)
                .arg("--check")
                .output()
                .unwrap();
            assert_eq!(out.status.code(), Some(1));
            let stderr = String::from_utf8_lossy(&out.stderr);
            assert!(stderr.contains("ADR-0003: hardcoded @sha256 digest found in [image].ref"));
        }

        // 4. Shadow ADR directory outside usr/share/doc/mios/adr
        {
            let dir = tempfile::tempdir().unwrap();
            let root = dir.path();
            let adr_dir = root.join("usr/share/doc/mios/adr");
            let shadow_dir = root.join("docs/adr");
            fs::create_dir_all(&adr_dir).unwrap();
            fs::create_dir_all(&shadow_dir).unwrap();
            write_test_ssot(root, true, true, false);
            fs::write(
                adr_dir.join("0001-first.md"),
                "---\nadr: 0001\ntitle: X\nstatus: accepted\n---\n",
            )
            .unwrap();
            fs::write(shadow_dir.join("0099-shadow.md"), "# Shadow").unwrap();

            let _ = Command::new(bin())
                .arg("adr-index")
                .arg("--root")
                .arg(root)
                .output()
                .unwrap();
            let out = Command::new(bin())
                .arg("adr-index")
                .arg("--root")
                .arg(root)
                .arg("--check")
                .output()
                .unwrap();
            assert_eq!(out.status.code(), Some(1));
            let stderr = String::from_utf8_lossy(&out.stderr);
            assert!(stderr.contains("shadow ADR namespace found outside usr/share/doc/mios/adr"));
        }
    }

    #[test]
    fn test_adr_index_json_format() {
        let _tree = super::tree_lock();
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        let adr_dir = root.join("usr/share/doc/mios/adr");
        fs::create_dir_all(&adr_dir).unwrap();
        write_test_ssot(root, true, true, false);

        fs::write(
            adr_dir.join("0001-first.md"),
            "---\nadr: 0001\ntitle: X\nstatus: accepted\n---\n",
        )
        .unwrap();

        let out = Command::new(bin())
            .arg("--format")
            .arg("json")
            .arg("adr-index")
            .arg("--root")
            .arg(root)
            .output()
            .expect("must execute mios-gen");
        assert_eq!(out.status.code(), Some(0));
        let stdout = String::from_utf8_lossy(&out.stdout);
        let v: serde_json::Value = serde_json::from_str(&stdout).unwrap();
        assert_eq!(v["status"], "clean");
        assert_eq!(v["subcommand"], "adr-index");
        assert_eq!(v["target"], "ADR.md");
        assert_eq!(v["violations"], 0);
    }
}

mod metal_vs_hosted {
    use std::fs;
    use std::process::Command;

    fn bin() -> &'static str {
        env!("CARGO_BIN_EXE_mios-gen")
    }

    fn write_synthetic_ssot(root: &std::path::Path) {
        let mios_dir = root.join("usr/share/mios");
        fs::create_dir_all(&mios_dir).unwrap();

        let toml = r#"
[blade.hardware]
min_interfaces = 1
min_ap_capable = 0
max_radios = 1

[blade.cluster]
k3s_servers = 3
control_plane_ha = true
localhost_hosts = 3

[blade.fencing]
method = "sbd"
diskless = true

[blade.storage]
replication = "all"
at_rest = "dmcrypt"

[blade.uplink]
failover = ["local", "peer"]

[blade.mesh]
blocks_boot = false
federate = "native"

[blade.archetypes]
endpoint = []
hybrid = ["service-plane", "gpu-serving", "controller"]

[blade.requires]
mios-llm-light = ["service-plane"]
mios-pgvector = ["service-plane"]
mios-hermes = ["service-plane"]
mios-k3s = ["controller", "service-plane"]

[blade.seat_side]
seat_side = ["mios-agent-pipe", "hermes-dashboard"]

[blade.planes.ai]
owner = "either"
role = "the OpenAI-compatible front door and the lanes behind it"
markers = []
wired_by = "usr/share/containers/systemd/mios-llm-light.container"

[blade.planes.router]
owner = "mini"
role = "the uplink"
markers = ["firewalld"]
wired_by = "usr/lib/sysctl.d/99-mios-vmhost.conf"

[greenboot]
critical_services = ["agent-pipe", "llm-light"]
blade_reachability_critical = false

[greenboot.probe.agent_pipe]
unit = "mios-agent-pipe.service"

[greenboot.probe.llm_light]
unit = "mios-llm-light.service"

[packages.core]
pkgs = ["firewalld"]

[llamacpp]
bake_models = "test-model.gguf = example/test:model.gguf"

[ai.vllm]
bake_model = ""
enable = false
"#;
        fs::write(mios_dir.join("mios.toml"), toml).unwrap();
    }

    #[test]
    fn test_metal_vs_hosted_render_and_check() {
        let _tree = super::tree_lock();
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        write_synthetic_ssot(root);

        // 1. Generate the file
        let out = Command::new(bin())
            .arg("metal-vs-hosted")
            .arg("--root")
            .arg(root)
            .output()
            .expect("must run mios-gen");
        assert_eq!(out.status.code(), Some(0));

        let doc_path = root.join("usr/share/doc/mios/reference/metal-vs-hosted.md");
        assert!(doc_path.exists());
        let doc_content = fs::read_to_string(&doc_path).unwrap();
        assert!(doc_content.contains("# MiOS-Metal vs hosted MiOS — the products, then the modes"));
        assert!(doc_content.contains("## Part 1 — the two products"));
        assert!(doc_content.contains("## Part 2 — the two modes"));

        // 2. Run --check mode on identical file
        let check_out = Command::new(bin())
            .arg("metal-vs-hosted")
            .arg("--root")
            .arg(root)
            .arg("--check")
            .output()
            .expect("must run mios-gen --check");
        assert_eq!(check_out.status.code(), Some(0));
        let stdout = String::from_utf8_lossy(&check_out.stdout);
        assert!(stdout.contains("matches the SSOT"));

        // 3. Plant defect (drift)
        fs::write(&doc_path, "tampered content\n").unwrap();
        let drift_out = Command::new(bin())
            .arg("metal-vs-hosted")
            .arg("--root")
            .arg(root)
            .arg("--check")
            .output()
            .expect("must run mios-gen --check");
        assert_eq!(drift_out.status.code(), Some(1));
        let stderr = String::from_utf8_lossy(&drift_out.stderr);
        assert!(stderr.contains("has drifted from the SSOT"));
    }

    #[test]
    fn test_metal_vs_hosted_json_format() {
        let _tree = super::tree_lock();
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();
        write_synthetic_ssot(root);

        let out = Command::new(bin())
            .arg("--format")
            .arg("json")
            .arg("metal-vs-hosted")
            .arg("--root")
            .arg(root)
            .output()
            .expect("must run mios-gen");
        assert_eq!(out.status.code(), Some(0));

        let stdout = String::from_utf8_lossy(&out.stdout);
        let v: serde_json::Value = serde_json::from_str(&stdout).unwrap();
        assert_eq!(v["status"], "clean");
        assert_eq!(v["subcommand"], "metal-vs-hosted");
        assert_eq!(
            v["target"],
            "usr/share/doc/mios/reference/metal-vs-hosted.md"
        );
        assert_eq!(v["violations"], 0);
    }

    #[test]
    fn test_metal_vs_hosted_negative_missing_ssot() {
        let _tree = super::tree_lock();
        let dir = tempfile::tempdir().unwrap();
        let root = dir.path();

        let out = Command::new(bin())
            .arg("metal-vs-hosted")
            .arg("--root")
            .arg(root)
            .arg("--check")
            .output()
            .expect("must run mios-gen --check");
        assert_eq!(out.status.code(), Some(1));
        let stderr = String::from_utf8_lossy(&out.stderr);
        assert!(stderr.contains("cannot read the SSOT"));
    }
}

mod render_manpages {
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
    fn test_render_manpages_cli_e2e() {
        let _tree = super::tree_lock();
        let root = get_repo_root();

        let td = tempfile::tempdir().expect("create tempdir");
        let td_root = td.path();

        let toml_src = root.join("usr/share/mios/mios.toml");
        let toml_dst = td_root.join("usr/share/mios/mios.toml");
        fs::create_dir_all(toml_dst.parent().unwrap()).unwrap();
        fs::copy(&toml_src, &toml_dst).unwrap();

        let ver_src = root.join("VERSION");
        let ver_dst = td_root.join("VERSION");
        fs::copy(&ver_src, &ver_dst).unwrap();

        let ch01_src = root.join("usr/share/doc/mios/manual/ch01-introduction-and-core-concepts.md");
        let ch01_dst = td_root.join("usr/share/doc/mios/manual/ch01-introduction-and-core-concepts.md");
        fs::create_dir_all(ch01_dst.parent().unwrap()).unwrap();
        if ch01_src.exists() {
            fs::copy(&ch01_src, &ch01_dst).unwrap();
        }

        // Initial render into temp root
        let render_init = Command::new(bin())
            .args(["render-manpages", "--root", &td_root.to_string_lossy()])
            .output()
            .expect("Failed to execute mios-gen render-manpages");
        assert!(
            render_init.status.success(),
            "Initial render failed: {}",
            String::from_utf8_lossy(&render_init.stderr)
        );

        // 1. Positive control: check clean repository with --check and --validate
        let output = Command::new(bin())
            .args([
                "render-manpages",
                "--root",
                &td_root.to_string_lossy(),
                "--check",
                "--validate",
            ])
            .output()
            .expect("Failed to execute mios-gen render-manpages");

        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);

        assert!(
            output.status.success(),
            "Expected exit 0 for render-manpages --check --validate on clean tree, got: {:?}\nstderr: {}",
            output.status.code(),
            stderr
        );
        assert!(
            stdout.contains("page(s) rendered and validated") || stdout.contains("page(s) validated"),
            "Expected validation confirmation message in stdout, got:\n{stdout}"
        );
        assert!(
            stdout.contains("page(s) verified"),
            "Expected verified confirmation message in stdout, got:\n{stdout}"
        );

        // 2. Positive control: JSON format check
        let output_json = Command::new(bin())
            .args([
                "--format",
                "json",
                "render-manpages",
                "--root",
                &td_root.to_string_lossy(),
                "--check",
                "--validate",
            ])
            .output()
            .expect("Failed to execute mios-gen --format json render-manpages");

        assert!(
            output_json.status.success(),
            "Expected exit 0 for json output"
        );
        let stdout_json = String::from_utf8_lossy(&output_json.stdout);
        let parsed_json: serde_json::Value =
            serde_json::from_str(&stdout_json).expect("Expected valid json output");
        assert_eq!(parsed_json["status"], "clean");
        assert_eq!(parsed_json["subcommand"], "render-manpages");
        assert_eq!(parsed_json["violations"], 0);

        // 3. Negative control: inject content mutation into an existing manpage
        let target_manpage = td_root.join("usr/share/man/man1/mios.1");
        assert!(
            target_manpage.exists(),
            "Expected usr/share/man/man1/mios.1 to exist at {:?}",
            target_manpage
        );
        {
            let _restorer = Restorer::new(&target_manpage);

            let original_content = fs::read_to_string(&target_manpage).expect("Read mios.1");
            let mutated_content =
                format!("{original_content}\n.PP\nan edit the SSOT does not describe\n");
            fs::write(&target_manpage, &mutated_content).expect("Write mutated mios.1");

            let output_neg = Command::new(bin())
                .args([
                    "render-manpages",
                    "--root",
                    &td_root.to_string_lossy(),
                    "--check",
                ])
                .output()
                .expect("Failed to execute mios-gen on mutated manpage");

            let stderr_neg = String::from_utf8_lossy(&output_neg.stderr);
            let stdout_neg = String::from_utf8_lossy(&output_neg.stdout);

            assert!(
                !output_neg.status.success(),
                "Expected failure on mutated manpage, but succeeded:\nstdout: {stdout_neg}\nstderr: {stderr_neg}"
            );
            assert!(
                stderr_neg.contains("man pages out of sync")
                    || stderr_neg.contains("usr/share/man/man1/mios.1"),
                "Expected drift error in stderr, got:\n{stderr_neg}"
            );
        }

        // 4. Negative control: inject an orphan manpage
        let orphan_manpage = td_root.join("usr/share/man/man1/mios-rogue-orphan-test.1");
        {
            let _deleter = FileDeleter::new(&orphan_manpage);
            fs::write(
                &orphan_manpage,
                ".TH MIOS-ROGUE 1 \"\" \"MiOS 0.3.0\" \"MiOS Verbs\"\n.SH NAME\nmios-rogue\n.SH DESCRIPTION\nRogue\n",
            )
            .expect("Write orphan manpage");

            let output_orphan = Command::new(bin())
                .args([
                    "render-manpages",
                    "--root",
                    &td_root.to_string_lossy(),
                    "--check",
                ])
                .output()
                .expect("Failed to execute mios-gen on orphan manpage tree");

            let stderr_orp = String::from_utf8_lossy(&output_orphan.stderr);
            let stdout_orp = String::from_utf8_lossy(&output_orphan.stdout);

            assert!(
                !output_orphan.status.success(),
                "Expected failure on orphan manpage, but succeeded:\nstdout: {stdout_orp}\nstderr: {stderr_orp}"
            );
            assert!(
                stderr_orp.contains("no verb declares it")
                    || stderr_orp.contains("man pages out of sync"),
                "Expected orphan error in stderr, got:\n{stderr_orp}"
            );
        }

        // 5. Positive control for Finding 2: foreign manpage survives check and write mode
        let foreign_manpage = td_root.join("usr/share/man/man1/ls.1");
        {
            let _deleter = FileDeleter::new(&foreign_manpage);
            fs::write(&foreign_manpage, ".TH LS 1 \"\" \"Linux\" \"Linux Manual\"\n.SH NAME\nls\n.SH DESCRIPTION\nList directory\n")
                .expect("Write foreign manpage");

            let check_foreign = Command::new(bin())
                .args([
                    "render-manpages",
                    "--root",
                    &td_root.to_string_lossy(),
                    "--check",
                ])
                .output()
                .expect("Failed to execute mios-gen check on foreign manpage tree");

            assert!(
                check_foreign.status.success(),
                "Foreign manpage must not cause --check to fail: {}",
                String::from_utf8_lossy(&check_foreign.stderr)
            );

            let write_foreign = Command::new(bin())
                .args([
                    "render-manpages",
                    "--root",
                    &td_root.to_string_lossy(),
                ])
                .output()
                .expect("Failed to execute mios-gen write on foreign manpage tree");

            assert!(
                write_foreign.status.success(),
                "Foreign manpage write mode must succeed: {}",
                String::from_utf8_lossy(&write_foreign.stderr)
            );
            assert!(
                foreign_manpage.exists(),
                "Foreign manpage must survive write mode without being deleted as an orphan"
            );
        }

        // 6. Negative control for Finding 3: planted date header must fail --check
        {
            let _restorer = Restorer::new(&target_manpage);
            let date_content = ".TH MIOS 1 \"2026-10-10\" \"MiOS 0.3.0\" \"MiOS Manual\"\n.SH NAME\nmios \\- dispatcher\n.SH DESCRIPTION\nPlanted date\n";
            fs::write(&target_manpage, date_content).expect("Write planted date manpage");

            let output_date = Command::new(bin())
                .args([
                    "render-manpages",
                    "--root",
                    &td_root.to_string_lossy(),
                    "--check",
                ])
                .output()
                .expect("Failed to execute mios-gen check on planted date");

            assert!(
                !output_date.status.success(),
                "Expected planted date to fail --check, but it succeeded"
            );
        }
    }
}

mod roadmap_index {
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
            let out = Command::new("git")
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

        let out = Command::new(bin())
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
        let _tree = super::tree_lock();
        let fixture = fixture_root();
        let roadmap_path = fixture.path().join("ROADMAP.md");

        // 1. Positive check on the self-consistent fixture
        let output = Command::new(bin())
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
        let original =
            fs::read_to_string(&roadmap_path).expect("Failed to read fixture ROADMAP.md");
        let mutated = original.replace("- **Done**:", "- **Done**: 99999");
        assert_ne!(original, mutated, "Mutation must change content");
        fs::write(&roadmap_path, mutated).expect("Failed to write mutated ROADMAP.md");

        let output_neg = Command::new(bin())
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
        let _tree = super::tree_lock();
        let fixture = fixture_root();

        let output = Command::new(bin())
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
        let _tree = super::tree_lock();
        let temp_empty = tempfile::tempdir().expect("Failed to create empty tempdir");

        let output = Command::new(bin())
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
}

mod standardize_docs {
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
    fn test_standardize_docs_cli_e2e() {
        let _tree = super::tree_lock();
        let root = get_repo_root();
        let bin_path = bin();
        let spec_file = root.join("specs/engineering/2026-04-26-Artifact-ENG-002-Scripts-Index.md");

        assert!(spec_file.exists(), "Target spec file must exist");
        let restorer = Restorer::new(&spec_file);

        // Ensure the spec file is standardized first
        let setup = Command::new(bin_path)
            .arg("standardize-docs")
            .arg("--root")
            .arg(&root)
            .output()
            .expect("Failed to standardize docs before test");
        assert!(setup.status.success());

        // 1. Positive control: standard check mode passes with exit code 0
        let output = Command::new(bin_path)
            .arg("standardize-docs")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute mios-gen standardize-docs --check");

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
            stdout.contains("[standardize-docs] specs/ markdown documentation is standardized"),
            "stdout should confirm standardized documentation: {}",
            stdout
        );

        // 2. Positive control: structured JSON check mode
        let json_output = Command::new(bin_path)
            .arg("--format")
            .arg("json")
            .arg("standardize-docs")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute mios-gen --format json standardize-docs --check");

        assert!(
            json_output.status.success(),
            "Expected JSON check to exit 0"
        );
        let json_str = String::from_utf8_lossy(&json_output.stdout);
        let parsed: serde_json::Value =
            serde_json::from_str(&json_str).expect("Output should be valid JSON");
        assert_eq!(parsed["status"], "clean");
        assert_eq!(parsed["subcommand"], "standardize-docs");
        assert_eq!(parsed["violations"], 0);

        // 3. Negative control: mutated spec file triggers UNSTANDARDIZED error with exit code 1
        let original = fs::read_to_string(&spec_file).expect("Failed to read spec file");
        let corrupted = format!("{}\n\n<!-- Corrupted extra trailing line -->\n", original);
        fs::write(&spec_file, corrupted.as_bytes()).expect("Failed to corrupt spec file");

        let neg_output = Command::new(bin_path)
            .arg("standardize-docs")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute negative check");

        let _neg_stdout = String::from_utf8_lossy(&neg_output.stdout);
        let neg_stderr = String::from_utf8_lossy(&neg_output.stderr);
        assert!(
            !neg_output.status.success(),
            "Mutated spec must fail check mode"
        );
        assert!(
            neg_stderr.contains("UNSTANDARDIZED") || neg_stderr.contains("require standardization"),
            "stderr should mention unstandardized file: {}",
            neg_stderr
        );

        // 4. Negative control: structured JSON reports violation
        let neg_json_output = Command::new(bin_path)
            .arg("--format")
            .arg("json")
            .arg("standardize-docs")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute negative JSON check");

        assert!(
            !neg_json_output.status.success(),
            "Mutated spec must fail JSON check mode"
        );
        let neg_json_str = String::from_utf8_lossy(&neg_json_output.stdout);
        let neg_parsed: serde_json::Value =
            serde_json::from_str(&neg_json_str).expect("Negative output should be valid JSON");
        assert_eq!(neg_parsed["status"], "violation");
        assert_eq!(neg_parsed["violations"], 1);

        // 5. Positive control: write mode standardizes and heals the file
        let heal_output = Command::new(bin_path)
            .arg("standardize-docs")
            .arg("--root")
            .arg(&root)
            .output()
            .expect("Failed to run heal in write mode");
        assert!(heal_output.status.success());

        let post_heal_output = Command::new(bin_path)
            .arg("standardize-docs")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to run post-heal check");
        assert!(post_heal_output.status.success());

        // Clean up via drop
        drop(restorer);

        // Final clean check
        let clean_output = Command::new(bin_path)
            .arg("standardize-docs")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to run clean final check");
        assert!(clean_output.status.success());
    }
}

mod sync_wiki {
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
    fn test_sync_wiki_cli_e2e() {
        let _tree = super::tree_lock();
        let root = get_repo_root();
        let bin_path = bin();
        let spec_file = root.join("specs/engineering/2026-04-26-Artifact-ENG-002-Scripts-Index.md");

        assert!(spec_file.exists(), "Target spec file must exist");
        let restorer = Restorer::new(&spec_file);

        // Ensure the spec file is synced first
        let setup = Command::new(bin_path)
            .arg("sync-wiki")
            .arg("--root")
            .arg(&root)
            .output()
            .expect("Failed to sync wiki before test");
        assert!(setup.status.success());

        // 1. Positive control: standard check mode passes with exit code 0
        let output = Command::new(bin_path)
            .arg("sync-wiki")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute mios-gen sync-wiki --check");

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
            stdout.contains("[sync-wiki] documentation embeds are in sync"),
            "stdout should confirm synced documentation: {}",
            stdout
        );

        // 2. Positive control: structured JSON check mode
        let json_output = Command::new(bin_path)
            .arg("--format")
            .arg("json")
            .arg("sync-wiki")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute mios-gen --format json sync-wiki --check");

        assert!(
            json_output.status.success(),
            "Expected JSON check to exit 0"
        );
        let json_str = String::from_utf8_lossy(&json_output.stdout);
        let parsed: serde_json::Value =
            serde_json::from_str(&json_str).expect("Output should be valid JSON");
        assert_eq!(parsed["status"], "clean");
        assert_eq!(parsed["subcommand"], "sync-wiki");
        assert_eq!(parsed["violations"], 0);

        // 3. Negative control: mutated spec file with stale version triggers STALE error with exit code 1
        let original = fs::read_to_string(&spec_file).expect("Failed to read spec file");
        let corrupted = original.replace(r#""version": "0.3.0""#, r#""version": "0.0.1""#);
        assert_ne!(original, corrupted, "Replacement must modify the spec file");
        fs::write(&spec_file, corrupted.as_bytes()).expect("Failed to corrupt spec file");

        let neg_output = Command::new(bin_path)
            .arg("sync-wiki")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute negative check");

        let _neg_stdout = String::from_utf8_lossy(&neg_output.stdout);
        let neg_stderr = String::from_utf8_lossy(&neg_output.stderr);
        assert!(
            !neg_output.status.success(),
            "Mutated spec must fail check mode"
        );
        assert!(
            neg_stderr.contains("STALE"),
            "stderr should mention STALE file: {}",
            neg_stderr
        );

        // 4. Negative control: structured JSON reports violation
        let neg_json_output = Command::new(bin_path)
            .arg("--format")
            .arg("json")
            .arg("sync-wiki")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute negative JSON check");

        assert!(
            !neg_json_output.status.success(),
            "Mutated spec must fail JSON check mode"
        );
        let neg_json_str = String::from_utf8_lossy(&neg_json_output.stdout);
        let neg_parsed: serde_json::Value =
            serde_json::from_str(&neg_json_str).expect("Negative output should be valid JSON");
        assert_eq!(neg_parsed["status"], "violation");
        assert_eq!(neg_parsed["violations"], 1);

        // 5. Positive control: write mode synchronizes and heals the file
        let heal_output = Command::new(bin_path)
            .arg("sync-wiki")
            .arg("--root")
            .arg(&root)
            .output()
            .expect("Failed to run heal in write mode");
        assert!(heal_output.status.success());

        let post_heal_output = Command::new(bin_path)
            .arg("sync-wiki")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to run post-heal check");
        assert!(post_heal_output.status.success());

        // Clean up via drop
        drop(restorer);

        // Final clean check
        let clean_output = Command::new(bin_path)
            .arg("sync-wiki")
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to run clean final check");
        assert!(clean_output.status.success());
    }
}
