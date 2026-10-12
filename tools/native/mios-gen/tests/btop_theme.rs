// AI-hint: Two-sided integration test suite for mios-gen render-btop-theme (ADR-0021, Law 14).
// AI-doc: usr/share/doc/mios/manual/tools.md
// AI-related: tools/native/mios-gen/src/btop_theme.rs, automation/98-drift-checks.sh

use std::fs;
use std::process::Command;

fn fixture() -> tempfile::TempDir {
    let temp = tempfile::tempdir().expect("temporary fixture");
    let vendor = temp.path().join("usr/share/mios");
    fs::create_dir_all(&vendor).unwrap();
    fs::write(
        vendor.join("mios.toml"),
        r##"[colors]
bg = "#282262"
fg = "#E7DFD3"
accent = "#1A407F"
cursor = "#F35C15"
success = "#3E7765"
warning = "#F35C15"
error = "#DC271B"
muted = "#948E8E"
subtle = "#B7C9D7"
ansi_12_bright_blue = "#3D6BA8"
[dotfiles.registry.btop]
template = "usr/share/mios/theme/templates/btop-mios.theme.tmpl"
target = "etc/btop/themes/mios.theme"
"##,
    )
    .unwrap();
    let templates = vendor.join("theme/templates");
    fs::create_dir_all(&templates).unwrap();
    fs::write(
        templates.join("btop-mios.theme.tmpl"),
        include_str!("../../../../usr/share/mios/theme/templates/btop-mios.theme.tmpl"),
    )
    .unwrap();
    temp
}

fn command(binary: impl AsRef<std::ffi::OsStr>, root: &std::path::Path) -> Command {
    let mut command = Command::new(binary);
    command
        .env("MIOS_VENDOR_TOML", root.join("usr/share/mios/mios.toml"))
        .env("MIOS_HOST_TOML", root.join("etc/mios/mios.toml"))
        .env("MIOS_USER_TOML", root.join("user.toml"));
    for key in ["MIOS_VENDOR_TOML_D", "MIOS_HOST_TOML_D", "MIOS_USER_TOML_D"] {
        command.env(key, root.join("absent-fragments"));
    }
    command
}

fn bin() -> std::path::PathBuf {
    std::env::var_os("MIOS_TEST_GEN_BIN")
        .map(std::path::PathBuf::from)
        .unwrap_or_else(|| env!("CARGO_BIN_EXE_mios-gen").into())
}

struct TempCleaner {
    target: std::path::PathBuf,
}

impl Drop for TempCleaner {
    fn drop(&mut self) {
        let _ = fs::remove_file(&self.target);
    }
}

#[test]
fn test_render_btop_theme_cli_e2e() {
    let temp = fixture();
    let root = temp.path();
    let bin_path = bin();

    assert!(command(&bin_path, root)
        .args(["render-btop-theme", "--root"])
        .arg(root)
        .status()
        .unwrap()
        .success());

    // 1. Positive control: standard check mode passes with exit code 0
    let output = command(&bin_path, root)
        .arg("render-btop-theme")
        .arg("--root")
        .arg(root)
        .arg("--check")
        .output()
        .expect("Failed to execute mios-gen render-btop-theme --check");

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
        stdout.contains("[btop-theme] btop theme matches SSOT"),
        "stdout should confirm match: {}",
        stdout
    );

    // 2. Positive control: JSON output contains valid schema
    let json_output = command(&bin_path, root)
        .arg("--format")
        .arg("json")
        .arg("render-btop-theme")
        .arg("--root")
        .arg(root)
        .arg("--check")
        .output()
        .expect("Failed to execute mios-gen --format json render-btop-theme --check");

    assert!(
        json_output.status.success(),
        "Expected exit code 0 in JSON mode"
    );
    let json_str = String::from_utf8_lossy(&json_output.stdout);
    assert!(
        json_str.contains("\"violations\": 0"),
        "JSON output should indicate 0 violations: {}",
        json_str
    );

    // 3. Positive control: render to custom output path
    let tmp_out = root.join("tests/golden/btop-test-output.theme");
    let _cleaner = TempCleaner {
        target: tmp_out.clone(),
    };

    let render_output = command(&bin_path, root)
        .arg("render-btop-theme")
        .arg("--root")
        .arg(root)
        .arg("--out")
        .arg(&tmp_out)
        .output()
        .expect("Failed to execute mios-gen render-btop-theme --out");

    assert!(
        render_output.status.success(),
        "Render to file should succeed"
    );
    assert!(tmp_out.is_file(), "Rendered target file must exist");
    let content = fs::read_to_string(&tmp_out).unwrap();
    assert!(
        content.contains("theme[main_bg]="),
        "Content must contain main_bg"
    );
    assert!(
        content.contains("theme[cpu_box]="),
        "Content must contain cpu_box"
    );

    // 4. Negative control: invalid theme syntax triggers exit code 1
    let corrupted_target = root.join("tests/golden/btop-corrupted.theme");
    fs::write(&corrupted_target, "theme[main_bg]=\"INVALID_HEX\"\n").unwrap();
    let _cleaner2 = TempCleaner {
        target: corrupted_target.clone(),
    };

    let fail_output = command(&bin_path, root)
        .arg("render-btop-theme")
        .arg("--root")
        .arg(root)
        .arg("--check")
        .arg("--out")
        .arg(&corrupted_target)
        .output()
        .expect("Failed to execute check on corrupted theme");

    assert_eq!(
        fail_output.status.code(),
        Some(1),
        "Expected exit code 1 on corrupted target file"
    );
}

#[test]
fn check_rejects_valid_color_drift_without_writing() {
    let temp = fixture();
    let root = temp.path();
    assert!(command(bin(), temp.path())
        .args(["render-btop-theme", "--root"])
        .arg(root)
        .status()
        .unwrap()
        .success());
    let target = root.join("etc/btop/themes/mios.theme");
    let rendered = fs::read_to_string(&target).unwrap();
    let changed = rendered.replace("theme[main_bg]=\"#282262\"", "theme[main_bg]=\"#010203\"");
    assert_ne!(
        changed, rendered,
        "negative control must change a real theme key"
    );
    fs::write(&target, &changed).unwrap();
    let result = command(bin(), temp.path())
        .args(["render-btop-theme", "--check", "--root"])
        .arg(root)
        .output()
        .unwrap();
    assert_eq!(result.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&result.stderr).contains("drifted from SSOT"));
    assert_eq!(
        fs::read_to_string(&target).unwrap(),
        changed,
        "check must not repair its subject"
    );
    fs::write(&target, rendered.replace('\n', "\r\n")).unwrap();
    assert!(
        command(bin(), temp.path())
            .args(["render-btop-theme", "--check", "--root"])
            .arg(root)
            .status()
            .unwrap()
            .success(),
        "CRLF-only difference is normalized"
    );
}

#[test]
fn check_rejects_missing_target_without_creating_it() {
    let temp = fixture();
    for target in [
        temp.path().join("etc/btop/themes/mios.theme"),
        temp.path().join("custom.theme"),
    ] {
        let result = command(bin(), temp.path())
            .args(["render-btop-theme", "--check", "--root"])
            .arg(temp.path())
            .arg("--out")
            .arg(&target)
            .output()
            .unwrap();
        assert_eq!(result.status.code(), Some(1));
        assert!(String::from_utf8_lossy(&result.stderr).contains("does not exist for verification"));
        assert!(
            !target.exists(),
            "check must not generate a missing subject"
        );
    }
}

#[test]
fn layered_palette_template_and_target_are_authoritative() {
    let temp = fixture();
    let root = temp.path();
    let host = root.join("etc/mios");
    fs::create_dir_all(&host).unwrap();
    fs::write(host.join("mios.toml"), "[colors]\naccent = \"#010203\"\n").unwrap();
    fs::write(root.join("user.toml"), "[colors]\naccent = \"#040506\"\n[dotfiles.registry.btop]\ntemplate = \"custom.tmpl\"\ntarget = \"custom.theme\"\n").unwrap();
    let template = include_str!("../../../../usr/share/mios/theme/templates/btop-mios.theme.tmpl")
        .replace("@MIOS:cursor@", "@MIOS:accent@");
    fs::write(root.join("custom.tmpl"), template).unwrap();
    assert!(command(bin(), root)
        .args(["render-btop-theme", "--root"])
        .arg(root)
        .status()
        .unwrap()
        .success());
    let target = root.join("custom.theme");
    let rendered = fs::read_to_string(&target).unwrap();
    assert!(rendered.contains("theme[hi_fg]=\"#040506\""));
    assert!(rendered.contains("theme[cpu_box]=\"#040506\""));
    assert!(!root.join("etc/btop/themes/mios.theme").exists());
    assert!(command(bin(), root)
        .args(["render-btop-theme", "--check", "--root"])
        .arg(root)
        .status()
        .unwrap()
        .success());
    fs::remove_file(root.join("user.toml")).unwrap();
    assert!(command(bin(), root)
        .args(["render-btop-theme", "--root"])
        .arg(root)
        .status()
        .unwrap()
        .success());
    assert!(fs::read_to_string(root.join("etc/btop/themes/mios.theme"))
        .unwrap()
        .contains("theme[cpu_box]=\"#010203\""));
}

#[test]
fn invalid_templates_fail_without_overwriting_the_target() {
    let temp = fixture();
    let root = temp.path();
    let template = root.join("usr/share/mios/theme/templates/btop-mios.theme.tmpl");
    let original = fs::read_to_string(&template).unwrap();
    assert!(command(bin(), root)
        .args(["render-btop-theme", "--root"])
        .arg(root)
        .status()
        .unwrap()
        .success());
    let target = root.join("etc/btop/themes/mios.theme");
    let expected = fs::read(&target).unwrap();
    for (text, diagnosis) in [
        (
            original.replace("@MIOS:accent@", "@MIOS:absent@"),
            "Unknown btop theme color token",
        ),
        (
            original.replace("@MIOS:accent@", "@MIOS:accent"),
            "Malformed btop theme token",
        ),
        (
            format!("{original}theme[main_fg]=\"#010203\"\n"),
            "duplicate theme key",
        ),
        (
            original.replace("theme[main_bg]", "garbage"),
            "Invalid syntax format",
        ),
        (
            original.replace(
                "theme[main_bg]=\"@MIOS:bg@\"",
                "theme[main_bg]=\"#010203\" trailing",
            ),
            "Invalid syntax format",
        ),
    ] {
        fs::write(&template, text).unwrap();
        let result = command(bin(), root)
            .args(["render-btop-theme", "--root"])
            .arg(root)
            .output()
            .unwrap();
        assert_eq!(result.status.code(), Some(1));
        assert!(String::from_utf8_lossy(&result.stderr).contains(diagnosis));
        assert_eq!(fs::read(&target).unwrap(), expected);
    }
    fs::remove_file(&template).unwrap();
    let result = command(bin(), root)
        .args(["render-btop-theme", "--root"])
        .arg(root)
        .output()
        .unwrap();
    assert_eq!(result.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&result.stderr).contains("Failed to read"));
    assert_eq!(fs::read(&target).unwrap(), expected);
    fs::write(template, original).unwrap();
    assert!(command(bin(), root)
        .args(["render-btop-theme", "--check", "--root"])
        .arg(root)
        .status()
        .unwrap()
        .success());
}
