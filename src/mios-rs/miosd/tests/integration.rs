// AI-hint: Integration tests for miosd: build progress CLI and adapter, drift-check registry, render-kargs unmanaged-karg preservation, and the resolver inline-table twin (Law 13).
// AI-related: /usr/share/mios/templates/rust, automation/build.sh, src/mios-rs/mios-build/src/progress.rs, src/mios-rs/miosd/src/drift/mod.rs, src/mios-rs/miosd/src/main.rs, automation/75-kargs-render.sh, usr/lib/bootc/kargs.d, usr/share/mios/mios.toml, tools/native/mios-resolver/src/walk.rs, usr/lib/mios/mios_toml.py, tools/check-runtime.py

use std::sync::{Mutex, MutexGuard};

/// One test at a time: several modules edit real-tree files under a restore guard.
fn tree_lock() -> MutexGuard<'static, ()> {
    static LOCK: Mutex<()> = Mutex::new(());
    LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

mod build_progress {
    use std::io::Write;
    use std::path::Path;
    use std::process::{Command, Stdio};

    fn cli(
        root: &Path,
        state: &Path,
        event: &str,
        extra: &[&str],
        input: Option<&str>,
    ) -> Result<std::process::Output, Box<dyn std::error::Error>> {
        let mut child = Command::new(env!("CARGO_BIN_EXE_miosd"))
            .args(["build-progress", "--root"])
            .arg(root)
            .arg("--state")
            .arg(state)
            .args(["--event", event])
            .args(extra)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;
        if let Some(input) = input {
            child
                .stdin
                .take()
                .ok_or("missing CLI stdin")?
                .write_all(input.as_bytes())?;
        }
        drop(child.stdin.take());
        Ok(child.wait_with_output()?)
    }

    fn fixture(root: &Path) -> Result<(), Box<dyn std::error::Error>> {
        std::fs::create_dir_all(root.join("usr/share/mios"))?;
        std::fs::write(
            root.join("usr/share/mios/mios.toml"),
            "[pipeline.console]\nwidth=100\nbar_width=24\n",
        )?;
        Ok(())
    }

    #[test]
    fn actual_cli_is_atomic_rejects_duplicates_and_refuses_incomplete_success(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let _tree = super::tree_lock();
        let temp = tempfile::tempdir()?;
        fixture(temp.path())?;
        let state = temp.path().join("state.json");
        assert!(
            cli(temp.path(), &state, "init", &[], Some("first\nsecond\n"))?
                .status
                .success()
        );
        assert!(!cli(temp.path(), &state, "init", &[], Some("overwrite\n"))?
            .status
            .success());
        assert!(!state.with_extension("lock").exists());
        let before = std::fs::read(&state)?;
        assert!(!cli(
            temp.path(),
            &state,
            "result",
            &["--name", "first", "--status", "pass"],
            None
        )?
        .status
        .success());
        assert_eq!(before, std::fs::read(&state)?);
        assert!(!cli(temp.path(), &state, "finish", &[], None)?
            .status
            .success());
        for name in ["first", "second"] {
            assert!(cli(temp.path(), &state, "start", &["--name", name], None)?
                .status
                .success());
            assert!(cli(
                temp.path(),
                &state,
                "result",
                &["--name", name, "--status", "pass"],
                None
            )?
            .status
            .success());
        }
        let result = cli(temp.path(), &state, "finish", &[], None)?;
        assert!(result.status.success());
        assert!(String::from_utf8(result.stdout)?.contains("2/2 100%"));
        assert!(!state.with_extension("lock").exists());
        Ok(())
    }

    #[test]
    fn actual_ssot_post_plan_preserves_operator_names_and_rejects_missing_or_injected_gates(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let _tree = super::tree_lock();
        let temp = tempfile::tempdir()?;
        fixture(temp.path())?;
        let path = temp.path().join("usr/share/mios/mios.toml");
        let source = include_str!("../../../../usr/share/mios/mios.toml");
        let invoke = || {
            Command::new(env!("CARGO_BIN_EXE_miosd"))
                .args(["build", "--post-list"])
                .env("MIOS_ROOT", temp.path())
                .output()
        };
        std::fs::write(&path, source)?;
        let baseline = invoke()?;
        assert!(
            baseline.status.success(),
            "{}",
            String::from_utf8_lossy(&baseline.stderr)
        );
        assert_eq!(String::from_utf8(baseline.stdout)?.lines().count(), 7);
        std::fs::write(
            &path,
            source.replace(
                "name = \"post-package-health\"",
                "name = \"operator-package-health\"",
            ),
        )?;
        let changed = invoke()?;
        assert!(changed.status.success());
        assert!(
            String::from_utf8(changed.stdout)?.contains("operator-package-health:package_health")
        );
        for modified in [
            source.replace(
                "{ name = \"99-postcheck.sh\", action = \"invariants\" },",
                "",
            ),
            source.replace(
                "action = \"invariants\"",
                "action = \"invariants; touch injected\"",
            ),
            source.replace("name = \"post-package-health\"", "name = \"bad:identity\""),
            source.replace("action = \"image_digests\"", "action = \"invariants\""),
        ] {
            assert_ne!(
                modified, source,
                "a mutation that changes nothing proves nothing"
            );
            std::fs::write(&path, modified)?;
            assert!(!invoke()?.status.success());
        }
        // Ruling Q8: a source-tree gate put back into the bake fails the plan.
        let anchor = "{ name = \"99-postcheck.sh\", action = \"invariants\" },";
        for (name, action) in [
            ("97-ssot-lint.sh", "ssot"),
            ("98-drift-checks.sh", "drift"),
            ("post-agent-tests", "agent_tests"),
        ] {
            let readded = source.replace(
                anchor,
                &format!("{anchor}\n    {{ name = \"{name}\", action = \"{action}\" }},"),
            );
            assert_ne!(readded, source, "the Q8 plant must land");
            std::fs::write(&path, readded)?;
            let refused = invoke()?;
            assert!(
                !refused.status.success(),
                "{action} re-added to the bake was accepted"
            );
            assert!(String::from_utf8_lossy(&refused.stderr).contains("ruling Q8"));
        }
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn real_shell_adapter_cannot_swallow_gate_failures_or_missing_dependencies(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let _tree = super::tree_lock();
        let temp = tempfile::tempdir()?;
        fixture(temp.path())?;
        let source = include_str!("../../../../automation/build.sh");
        let start = source
            .find("_finding() {")
            .ok_or("missing native adapter")?;
        let end = source[start..]
            .find("\n[[ -n \"$_miosd\"")
            .ok_or("missing adapter boundary")?
            + start;
        let adapter = &source[start..end];
        let script = format!(
            r#"set -euo pipefail
_miosd=$1
_mios_root=$2
PROGRESS_DIR=$2
PROGRESS_STATE="$2/state.json"
SCRIPT_COUNT=0
FAIL_LOG=()
WARN_LOG=()
WARNED_JSON=()
declare -A PHASE_FATAL=([warn]=false)
{adapter}
bad_gate() {{ false; echo 'SWALLOWED_FAILURE'; return 0; }}
warn_gate() {{ return 7; }}
missing_gate() {{ _finding missing 'required test interpreter absent'; return 125; }}
printf '%s\n' good bad warn missing | "$_miosd" build-progress --root "$_mios_root" --state "$PROGRESS_STATE" --event init
_run_stage good true
_run_stage bad bad_gate
_run_stage warn warn_gate
_run_stage missing missing_gate
"$_miosd" build-progress --root "$_mios_root" --state "$PROGRESS_STATE" --event finish
"#
        );
        let output = Command::new("bash")
            .args([
                "-c",
                &script,
                "mios-build-control",
                env!("CARGO_BIN_EXE_miosd"),
            ])
            .arg(temp.path())
            .output()?;
        let stdout = String::from_utf8(output.stdout)?;
        assert!(!output.status.success(), "{stdout}");
        assert!(!stdout.contains("SWALLOWED_FAILURE"));
        assert!(stdout.contains("4/4 100%"));
        assert!(
            stdout.contains("PASS 1 FAIL 1 WARN 1 SKIP 0 MISSING 1 PENDING 0"),
            "{stdout}"
        );
        let state: serde_json::Value =
            serde_json::from_slice(&std::fs::read(temp.path().join("state.json"))?)?;
        assert_eq!(
            state["completed"]
                .as_array()
                .ok_or("outcomes omitted")?
                .len(),
            4
        );
        assert_eq!(state["notes"][0][0], "missing");
        Ok(())
    }
}

mod registry_integration {
    use miosd::drift::{Check, DriftCtx, Registry, SSOTParseCheck, Verdict};
    use std::path::PathBuf;

    #[test]
    fn test_full_registry_instantiation() {
        let _tree = super::tree_lock();
        let reg = Registry::new();
        assert!(
            !reg.checks.is_empty(),
            "Registry must contain registered checks"
        );
        // Duplicate ids silently break `miosd drift --only <id>` (the filter would
        // select several checks) and make the run summary ambiguous.
        let mut seen = std::collections::BTreeSet::new();
        for c in &reg.checks {
            assert!(
                !c.id().is_empty(),
                "every registered check needs a non-empty id"
            );
            assert!(
                seen.insert(c.id()),
                "duplicate check id registered: {}",
                c.id()
            );
        }
    }

    #[test]
    fn test_ssot_parse_check_live_repo() {
        let _tree = super::tree_lock();
        // CARGO_MANIFEST_DIR is <repo>/src/mios-rs/miosd, so the repo root is three
        // levels up. It was two, which pointed at <repo>/src -- where mios.toml does
        // not exist, so the guard below skipped the whole assertion and this test
        // could never fail.
        let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"));
        let root = manifest_dir.join("../../../");
        let mios_toml = root.join("usr/share/mios/mios.toml");
        assert!(
            mios_toml.exists(),
            "Live SSOT not found at {} -- this test must exercise the real repo, not skip",
            mios_toml.display()
        );
        let ctx = DriftCtx::new(root, false);
        let check = SSOTParseCheck;
        let verdict = check.run(&ctx);
        assert!(
            matches!(verdict, Verdict::Pass(_)),
            "Live SSOT parse check failed: {:?}",
            verdict
        );
    }

    #[test]
    fn test_drift_check_nonexistent_root_reports_zero_pass() {
        let _tree = super::tree_lock();
        let temp = tempfile::tempdir().expect("tempdir");
        let nonexistent = temp.path().join("definitely_nonexistent_root");

        let output = std::process::Command::new(env!("CARGO_BIN_EXE_miosd"))
            .args(["drift-check", "--root"])
            .arg(&nonexistent)
            .output()
            .expect("execute miosd drift-check on nonexistent root");

        // The process exits non-zero (exit code 1)
        assert!(
            !output.status.success(),
            "drift-check on nonexistent root must not succeed"
        );
        assert_eq!(
            output.status.code(),
            Some(1),
            "drift-check on nonexistent root must exit with code 1"
        );

        let stdout = String::from_utf8_lossy(&output.stdout);
        let stderr = String::from_utf8_lossy(&output.stderr);
        let combined = format!("{stdout}\n{stderr}");

        // Exactly 0 checks report PASS (no hollow constant-pass stubs)
        assert!(
            stdout.contains("PASS 0 "),
            "Summary must explicitly report PASS 0 on nonexistent root: {stdout}"
        );
        assert!(
            !stdout.contains(" PASS +"),
            "No individual check may report PASS on a nonexistent root: {stdout}"
        );

        // Fails with explicit error diagnostic
        assert!(
            combined.contains("INCOMPLETE/FAILED"),
            "Summary status must be INCOMPLETE/FAILED: {combined}"
        );
        assert!(
            stderr.contains("No such file or directory")
                || stderr.contains("missing")
                || stderr.contains("absent"),
            "Must contain explicit error diagnostics for missing targets: {stderr}"
        );

        // Also verify on an existing empty directory to guarantee existence alone does not trigger passes
        let empty_dir = temp.path().join("empty_dir");
        std::fs::create_dir_all(&empty_dir).expect("create empty dir");
        let output_empty = std::process::Command::new(env!("CARGO_BIN_EXE_miosd"))
            .args(["drift-check", "--root"])
            .arg(&empty_dir)
            .output()
            .expect("execute miosd drift-check on empty dir");

        assert_eq!(
            output_empty.status.code(),
            Some(1),
            "empty dir must exit with code 1"
        );
        let stdout_empty = String::from_utf8_lossy(&output_empty.stdout);
        assert!(
            stdout_empty.contains("PASS 0 "),
            "Summary on empty dir must report PASS 0: {stdout_empty}"
        );
        assert!(
            !stdout_empty.contains(" PASS +"),
            "No check may report PASS on empty root: {stdout_empty}"
        );

        // In-process verification: running registry directly against nonexistent root must also fail
        let ctx = DriftCtx::new(nonexistent, false);
        let reg = Registry::new();
        assert!(
            !reg.run_all(&ctx, None),
            "Registry::run_all must return false on nonexistent root"
        );
    }
}

mod render_kargs {
    use std::fs;
    use std::path::PathBuf;
    use std::process::Command;

    const SHIPPED_VFIO: &str = concat!(
    "# AI-hint: Configures kernel arguments for IOMMU, VFIO-PCI, and nested virtualization to enable hardware passthrough and virtualization features in the MiOS boot process.\n",
    "# Generated from mios.toml [kargs] SSOT\n",
    "kargs = [\n",
    "    \"rd.driver.pre=vfio-pci\",\n",
    "    \"kvm-intel.nested=1\",\n",
    "    \"intel_iommu=on\",\n",
    "    \"amd_iommu=on\",\n",
    "    \"iommu=pt\"\n",
    "]\n",
);

    struct Fixture {
        dir: tempfile::TempDir,
    }

    impl Fixture {
        fn new(kargs_table: &str) -> Self {
            let dir = tempfile::tempdir().expect("tempdir");
            fs::write(dir.path().join("01-mios-vfio.toml"), SHIPPED_VFIO).expect("seed vfio");
            fs::write(dir.path().join("mios.toml"), kargs_table).expect("seed toml");
            Fixture { dir }
        }
        fn toml(&self) -> PathBuf {
            self.dir.path().join("mios.toml")
        }
        fn run(&self) -> std::process::Output {
            Command::new(env!("CARGO_BIN_EXE_miosd"))
                .args(["render-kargs", "--toml"])
                .arg(self.toml())
                .arg("--kargs-dir")
                .arg(self.dir.path())
                .output()
                .expect("run miosd")
        }
        fn vfio(&self) -> String {
            fs::read_to_string(self.dir.path().join("01-mios-vfio.toml")).expect("read vfio")
        }
        fn custom_exists(&self) -> bool {
            self.dir.path().join("99-mios-kargs.toml").exists()
        }
    }

    const DEFAULTS: &str = "[kargs]\niommu = \"on\"\nvfio_ids = \"\"\nhugepages = \"\"\nisolcpus = \"\"\nnohz_full = \"\"\nrcu_nocbs = \"\"\nTHP = \"\"\n";

    /// The regression this file exists for. The shipped drop-in,
    /// usr/lib/bootc/kargs.d/01-mios-vfio.toml, is NOT wholly generated:
    /// rd.driver.pre=vfio-pci binds vfio-pci in the initramfs before a
    /// GPU driver can claim the card, and kvm-intel.nested=1 enables nested KVM.
    /// Neither comes from any [kargs] key. A renderer that rebuilds the list from
    /// scratch deletes both from the kernel command line, exits 0, and leaves a
    /// header claiming the file came from SSOT.
    #[test]
    fn unmanaged_kargs_survive_a_render() {
        let _tree = super::tree_lock();
        let f = Fixture::new(DEFAULTS);
        assert!(f.run().status.success());
        let out = f.vfio();
        assert!(
            out.contains("\"rd.driver.pre=vfio-pci\""),
            "rd.driver.pre=vfio-pci was dropped from the kernel command line:\n{out}"
        );
        assert!(
            out.contains("\"kvm-intel.nested=1\""),
            "kvm-intel.nested=1 was dropped from the kernel command line:\n{out}"
        );
        assert_eq!(
            out, SHIPPED_VFIO,
            "the shipped file must round-trip exactly"
        );
    }

    #[test]
    fn iommu_mode_swaps_only_the_managed_trio() {
        let _tree = super::tree_lock();
        let f = Fixture::new(&DEFAULTS.replace("iommu = \"on\"", "iommu = \"amd\""));
        assert!(f.run().status.success());
        let out = f.vfio();
        assert!(out.contains("\"amd_iommu=on\"") && out.contains("\"iommu=pt\""));
        assert!(
            !out.contains("\"intel_iommu=on\""),
            "intel entry not removed:\n{out}"
        );
        assert!(
            out.contains("\"rd.driver.pre=vfio-pci\""),
            "unmanaged karg lost:\n{out}"
        );
    }

    #[test]
    fn vfio_ids_is_replaced_not_accumulated() {
        let _tree = super::tree_lock();
        let f = Fixture::new(&DEFAULTS.replace("vfio_ids = \"\"", "vfio_ids = \"10de:1db6\""));
        assert!(f.run().status.success());
        let first = f.vfio();
        assert!(first.contains("\"vfio-pci.ids=10de:1db6\""));
        assert!(f.run().status.success());
        assert_eq!(first, f.vfio(), "a second render must be a no-op");
    }

    #[test]
    fn custom_kargs_file_is_written_then_removed() {
        let _tree = super::tree_lock();
        let f = Fixture::new(&DEFAULTS.replace("isolcpus = \"\"", "isolcpus = \"2-7\""));
        assert!(f.run().status.success());
        assert!(
            f.custom_exists(),
            "99-mios-kargs.toml should exist when a custom karg is set"
        );
        fs::write(f.toml(), DEFAULTS).expect("rewrite toml");
        assert!(f.run().status.success());
        assert!(
            !f.custom_exists(),
            "stale 99-mios-kargs.toml must be removed"
        );
    }

    /// unwrap_or_default() on the read turned "the SSOT is unreadable" into
    /// "render the defaults anyway". A renderer that invents a kernel command line
    /// for a manifest it could not read is Skip-as-Pass with boot consequences.
    #[test]
    fn an_unreadable_ssot_fails_and_writes_nothing() {
        let _tree = super::tree_lock();
        let f = Fixture::new(DEFAULTS);
        fs::remove_file(f.toml()).expect("remove toml");
        assert!(!f.run().status.success(), "a missing SSOT must not exit 0");
        assert_eq!(f.vfio(), SHIPPED_VFIO, "nothing may be written on failure");
    }

    #[test]
    fn a_malformed_ssot_fails_and_writes_nothing() {
        let _tree = super::tree_lock();
        let f = Fixture::new("[kargs\niommu = \"on\"\n");
        assert!(!f.run().status.success(), "malformed TOML must not exit 0");
        assert_eq!(f.vfio(), SHIPPED_VFIO, "nothing may be written on failure");
    }
}

mod resolver_inline_twin {
    use std::path::Path;
    use std::process::Command;

    /// Keys out of order, a nested table that is not last, a Windows path: every
    /// rule of the inline contract, shaped like [desktop].apps.
    const SSOT: &str = r#"apps = [
  { role = "browser", overrides = { B = 2, A = 1 }, id = "org.example.App", remote = "flathub" },
  { path = 'C:\MiOS\bin', tags = ["x", "y"] },
]
"#;

    fn rust_render() -> String {
        let v: toml::Value = toml::from_str(SSOT).expect("fixture parses");
        mios_resolver::walk::process_val("desktop.apps", v.get("apps").expect("apps"), 0)
    }

    #[test]
    fn nested_tables_render_last_and_keys_sorted() {
        let _tree = super::tree_lock();
        assert_eq!(
            rust_render(),
            concat!(
                r#"{ id = "org.example.App", remote = "flathub", role = "browser", overrides = { A = 1, B = 2 } },"#,
                r#"{ path = 'C:\MiOS\bin', tags = ["x", "y"] }"#,
            )
        );
    }

    #[test]
    fn equals_the_python_twin() {
        let _tree = super::tree_lock();
        let repo = Path::new(env!("CARGO_MANIFEST_DIR")).join("../../..");
        let script = format!(
            "import sys, tomllib; sys.path.insert(0, {lib:?}); import mios_toml; \
         print(mios_toml.process_val('desktop.apps', tomllib.loads(sys.stdin.read())['apps']))",
            lib = repo.join("usr/lib/mios").display().to_string()
        );
        let out = Command::new("python3")
            .args(["-c", &script])
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .spawn()
            .and_then(|mut c| {
                use std::io::Write;
                c.stdin.take().expect("stdin").write_all(SSOT.as_bytes())?;
                c.wait_with_output()
            })
            .expect("python3 runs");
        assert!(out.status.success(), "python twin failed");
        assert_eq!(
            rust_render(),
            String::from_utf8_lossy(&out.stdout).trim_end()
        );
    }
}
