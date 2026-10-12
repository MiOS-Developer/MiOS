// AI-hint: Two-sided integration test suite for mios-template-compile (ADR-0021, Law 14).
// AI-doc: usr/share/doc/mios/manual/tools.md
// AI-related: tools/native/mios-template-compile/src/main.rs, automation/98-drift-checks.sh, tests/drift-gate-negatives.sh

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
    env!("CARGO_BIN_EXE_mios-template-compile")
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

struct DropFile {
    target: PathBuf,
}

impl DropFile {
    fn new(path: PathBuf) -> Self {
        Self { target: path }
    }
}

impl Drop for DropFile {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.target);
    }
}

#[test]
fn test_compile_templates_cli_e2e() {
    let root = get_repo_root();
    let bin_path = bin();

    // 1. Positive control: standard check mode passes with exit code 0
    let output = Command::new(bin_path)
        .arg("--root")
        .arg(&root)
        .arg("--check")
        .output()
        .expect("Failed to execute mios-template-compile --check");

    let stdout = String::from_utf8_lossy(&output.stdout);
    let stderr = String::from_utf8_lossy(&output.stderr);

    assert!(
        output.status.success(),
        "mios-template-compile --check failed with code {:?}\nstdout: {}\nstderr: {}",
        output.status.code(),
        stdout,
        stderr
    );
    assert!(
        stdout.contains(
            "[compile-templates] PASS: All 29 templates compiled/validated successfully."
        ),
        "Expected success message not found in stdout: {}",
        stdout
    );

    // 2. Positive control: structured JSON output format
    let output_json = Command::new(bin_path)
        .arg("--root")
        .arg(&root)
        .arg("--check")
        .arg("--format")
        .arg("json")
        .output()
        .expect("Failed to execute mios-template-compile --format json --check");

    assert!(
        output_json.status.success(),
        "mios-template-compile --format json --check failed"
    );
    let json_val: serde_json::Value =
        serde_json::from_slice(&output_json.stdout).expect("Failed to parse JSON output");
    assert_eq!(json_val["status"], "clean");
    assert_eq!(json_val["subcommand"], "compile-templates");
    assert_eq!(json_val["violations"], 0);
    assert_eq!(json_val["templates_count"], 29);

    // 3. Negative control: mutate toml-config with syntax defect
    let toml_tmpl = root.join("usr/share/mios/templates/toml-config");
    {
        let _restorer = Restorer::new(&toml_tmpl);
        let mut mutated = fs::read_to_string(&toml_tmpl).expect("read toml-config");
        mutated.push_str("\nINVALID_SYNTAX_BOGUS {{\n");
        fs::write(&toml_tmpl, mutated.as_bytes()).expect("write mutated toml-config");

        let neg_output = Command::new(bin_path)
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute negative control");

        assert!(
            !neg_output.status.success(),
            "Expected failure on corrupted toml-config, but got exit code 0"
        );
        let neg_stderr = String::from_utf8_lossy(&neg_output.stderr);
        assert!(
            neg_stderr
                .contains("[compile-templates] FAIL: 1 template(s) failed compilation/validation:"),
            "Expected FAIL message in stderr, got: {}",
            neg_stderr
        );
        assert!(
            neg_stderr.contains("toml-config: TOML Parse Error:"),
            "Expected toml-config TOML Parse Error in stderr, got: {}",
            neg_stderr
        );

        // Also test JSON output format on failure
        let neg_json_output = Command::new(bin_path)
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .arg("--format")
            .arg("json")
            .output()
            .expect("Failed to execute negative JSON control");

        assert!(
            !neg_json_output.status.success(),
            "Expected failure on corrupted toml-config with JSON format, but got exit code 0"
        );
        let neg_json: serde_json::Value = serde_json::from_slice(&neg_json_output.stderr)
            .expect("Failed to parse JSON error output");
        assert_eq!(neg_json["status"], "drift");
        assert_eq!(neg_json["violations"], 1);
        assert!(neg_json["failures"]["toml-config"]
            .as_str()
            .unwrap()
            .contains("TOML Parse Error:"));
    }

    // 4. Negative control: inject an unregistered template
    let unreg_tmpl = root.join("usr/share/mios/templates/unregistered-test-template");
    {
        fs::write(&unreg_tmpl, b"valid = \"content\"\n").expect("write unregistered template");
        let _drop_file = DropFile::new(unreg_tmpl);

        let neg_output = Command::new(bin_path)
            .arg("--root")
            .arg(&root)
            .arg("--check")
            .output()
            .expect("Failed to execute unregistered negative control");

        assert!(
            !neg_output.status.success(),
            "Expected failure on unregistered template, but got exit code 0"
        );
        let neg_stderr = String::from_utf8_lossy(&neg_output.stderr);
        assert!(
            neg_stderr
                .contains("unregistered-test-template: Not registered in mios.toml [templates.*]"),
            "Expected unregistered failure in stderr, got: {}",
            neg_stderr
        );
    }

    // 5. Negative control: missing directory
    let missing_output = Command::new(bin_path)
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
        missing_stderr.contains("[compile-templates] Templates directory not found:"),
        "Expected directory not found error in stderr, got: {}",
        missing_stderr
    );

    // 6. Post-restoration check: tree passes cleanly
    let restored_output = Command::new(bin_path)
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
