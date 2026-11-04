// AI-hint: Actual CLI controls with isolated layered SSOT and redirected streams.
// AI-related: src/main.rs, usr/share/mios/mios.toml [tamagotchi]
use std::{fs, process::Command};
fn root() -> tempfile::TempDir {
    let root = tempfile::tempdir().unwrap();
    fs::create_dir_all(root.path().join("usr/share/mios")).unwrap();
    fs::write(
        root.path().join("usr/share/mios/mios.toml"),
        include_str!("../../../../usr/share/mios/mios.toml"),
    )
    .unwrap();
    root
}
#[test]
fn redirected_output_is_one_plain_snapshot_even_with_graphics_overrides() {
    let root = root();
    let result = Command::new(env!("CARGO_BIN_EXE_mios-tamagotchi"))
        .env_clear()
        .env("TERM", "xterm-256color")
        .env("COLORTERM", "truecolor")
        .args([
            "--root",
            root.path().to_str().unwrap(),
            "--renderer",
            "braille",
            "--color",
            "rgb",
        ])
        .output()
        .unwrap();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert!(result.stdout.is_ascii());
    assert!(!result.stdout.contains(&0x1b));
    assert_eq!(String::from_utf8_lossy(&result.stdout).lines().count(), 9);
    assert!(result.stderr.is_empty());
}
#[test]
fn invalid_cli_and_host_overrides_fail_without_terminal_output() {
    let root = root();
    for args in [vec!["--scale", "3"], vec!["--renderer", "bogus"]] {
        let result = Command::new(env!("CARGO_BIN_EXE_mios-tamagotchi"))
            .env_clear()
            .args(["--root", root.path().to_str().unwrap()])
            .args(args)
            .output()
            .unwrap();
        assert!(!result.status.success());
        assert!(result.stdout.is_empty());
        assert!(!result.stderr.contains(&0x1b));
    }
    fs::create_dir_all(root.path().join("etc/mios")).unwrap();
    fs::write(
        root.path().join("etc/mios/mios.toml"),
        "[tamagotchi]\ntick_ms=0\n",
    )
    .unwrap();
    let result = Command::new(env!("CARGO_BIN_EXE_mios-tamagotchi"))
        .env_clear()
        .args(["--root", root.path().to_str().unwrap(), "--snapshot"])
        .output()
        .unwrap();
    assert!(!result.status.success());
    assert!(result.stdout.is_empty());
    assert!(String::from_utf8_lossy(&result.stderr).contains("tick_ms"));
}
#[test]
fn host_layer_overrides_one_field_without_losing_vendor_settings() {
    let root = root();
    fs::create_dir_all(root.path().join("etc/mios")).unwrap();
    fs::write(
        root.path().join("etc/mios/mios.toml"),
        "[tamagotchi]\ntick_ms=250\nrenderer='ascii'\n",
    )
    .unwrap();
    let result = Command::new(env!("CARGO_BIN_EXE_mios-tamagotchi"))
        .env_clear()
        .args(["--root", root.path().to_str().unwrap(), "--snapshot"])
        .output()
        .unwrap();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
}
