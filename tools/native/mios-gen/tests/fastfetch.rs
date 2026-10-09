// AI-hint: Hermetic fastfetch CLI controls for real default output, SSOT changes, no-write modes and failure propagation.
// AI-related: tools/native/mios-gen/src/fastfetch.rs, usr/share/mios/mios.toml
use std::fs;
use std::path::Path;
use std::process::Command;

fn fixture() -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir_all(root.path().join("usr/share/mios")).unwrap();
    fs::write(
        root.path().join("usr/share/mios/mios.toml"),
        include_str!("../../../../usr/share/mios/mios.toml"),
    )
    .unwrap();
    root
}
fn command(root: &Path) -> Command {
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_mios-gen"));
    cmd.args(["render-fastfetch", "--root"])
        .arg(root)
        .env("MIOS_VENDOR_TOML", root.join("usr/share/mios/mios.toml"))
        .env("MIOS_USER_TOML", root.join("absent-user.toml"))
        .env("MIOS_HOST_TOML", root.join("absent-host.toml"))
        .env("XDG_CONFIG_HOME", root.join("xdg"))
        .env_remove("MIOS_TOML")
        .env_remove("MIOS_TOML_ROOT");
    cmd
}
#[test]
fn default_check_requires_real_artifact_and_detects_drift_without_writing() {
    let fixture = fixture();
    let root = fixture.path();
    let target = root.join("usr/share/mios/fastfetch/telemetry.jsonc");
    let missing = command(root).arg("--check").output().unwrap();
    assert_eq!(missing.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&missing.stderr).contains("missing or unreadable"));
    assert!(!target.exists());
    assert!(command(root).status().unwrap().success());
    assert!(command(root).arg("--check").status().unwrap().success());
    let clean = fs::read_to_string(&target).unwrap();
    let json: serde_json::Value = serde_json::from_str(&clean).unwrap();
    assert!(json["modules"]
        .as_array()
        .unwrap()
        .iter()
        .any(|m| m["format"] == "granite4.1:8b"));
    fs::write(&target, "{}\n").unwrap();
    let drift = command(root).arg("--check").output().unwrap();
    assert_eq!(drift.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&drift.stderr).contains("drifted from SSOT"));
    assert_eq!(fs::read_to_string(&target).unwrap(), "{}\n");
    for flag in ["--mock", "--dry-run"] {
        assert!(command(root).arg(flag).status().unwrap().success());
        assert_eq!(fs::read_to_string(&target).unwrap(), "{}\n");
    }
    fs::write(&target, clean.replace('\n', "\r\n")).unwrap();
    assert!(command(root).arg("--check").status().unwrap().success());
    assert!(!root.join("usr/share/mios/fastfetch/config.jsonc").exists());
}
#[test]
fn ssot_changes_flow_to_projection_and_missing_input_fails() {
    let fixture = fixture();
    let root = fixture.path();
    let ssot = root.join("usr/share/mios/mios.toml");
    assert!(command(root).status().unwrap().success());
    let original = fs::read_to_string(&ssot).unwrap();
    fs::write(&ssot, original.replace("granite4.1:8b", "fixture-model")).unwrap();
    assert_eq!(
        command(root).arg("--check").status().unwrap().code(),
        Some(1)
    );
    assert!(command(root).status().unwrap().success());
    assert!(
        fs::read_to_string(root.join("usr/share/mios/fastfetch/telemetry.jsonc"))
            .unwrap()
            .contains("fixture-model")
    );
    fs::write(&ssot, original.replace("#282262", "INVALID_HEX")).unwrap();
    let invalid = command(root).arg("--mock").output().unwrap();
    assert_eq!(invalid.status.code(), Some(1));
    assert!(String::from_utf8_lossy(&invalid.stderr).contains("expected #rrggbb"));
    fs::remove_file(&ssot).unwrap();
    assert_eq!(
        command(root).arg("--mock").status().unwrap().code(),
        Some(1)
    );
}
#[test]
fn custom_output_logo_and_json_cli_contracts() {
    let fixture = fixture();
    let root = fixture.path();
    let target = root.join("custom path with spaces.jsonc");
    assert!(command(root)
        .arg("--out")
        .arg(&target)
        .arg("--logo-type")
        .arg("auto")
        .status()
        .unwrap()
        .success());
    let json: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(&target).unwrap()).unwrap();
    assert_eq!(json["logo"]["type"], "auto");
    assert!(command(root)
        .arg("--out")
        .arg(&target)
        .args(["--logo-type", "auto", "--check"])
        .status()
        .unwrap()
        .success());
    assert_eq!(
        command(root)
            .args(["--logo-type", "invalid"])
            .status()
            .unwrap()
            .code(),
        Some(1)
    );
    let result = command(root)
        .args(["--format", "json", "--mock"])
        .output()
        .unwrap();
    assert!(result.status.success());
    let report: serde_json::Value = serde_json::from_slice(&result.stdout).unwrap();
    assert_eq!(report["violations"], 0);
}
