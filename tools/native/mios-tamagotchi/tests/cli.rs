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
    assert!(String::from_utf8_lossy(&result.stdout).contains("Snapshot only"));
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

#[test]
fn sample_and_demo_need_no_live_configuration_or_state_home() {
    let dir = tempfile::tempdir().unwrap();
    for flag in ["--sample", "--demo"] {
        let result = Command::new(env!("CARGO_BIN_EXE_mios-tamagotchi"))
            .env_clear()
            .env("XDG_STATE_HOME", dir.path())
            .current_dir(dir.path())
            .arg(flag)
            .output()
            .unwrap();
        assert!(
            result.status.success(),
            "{}",
            String::from_utf8_lossy(&result.stderr)
        );
        let text = String::from_utf8_lossy(&result.stdout);
        assert!(text.contains(if flag == "--demo" {
            "memory wins 1"
        } else {
            "Snapshot only"
        }));
        assert!(result.stdout.is_ascii());
        assert!(!result.stdout.contains(&0x1b));
        assert_eq!(fs::read_dir(dir.path()).unwrap().count(), 0);
    }
}

#[test]
fn snapshot_of_existing_pet_is_read_only_and_corrupt_save_is_preserved() {
    use mios_tamagotchi::{config::Config, game::Game, save::Store};
    let root = root();
    let path = root.path().join("pet.json");
    let store = Store::open(&path).unwrap();
    let mut game = Game::new(&Config::sample().unwrap().game, 7);
    game.age_ticks = 12;
    game.coins = 42;
    store.save(&game).unwrap();
    drop(store);
    for bytes in [fs::read(&path).unwrap(), b"corrupt".to_vec()] {
        fs::write(&path, &bytes).unwrap();
        let result = Command::new(env!("CARGO_BIN_EXE_mios-tamagotchi"))
            .env_clear()
            .args([
                "--root",
                root.path().to_str().unwrap(),
                "--save",
                path.to_str().unwrap(),
                "--snapshot",
            ])
            .output()
            .unwrap();
        assert_eq!(fs::read(&path).unwrap(), bytes);
        if bytes == b"corrupt" {
            assert!(!result.status.success());
            assert!(result.stdout.is_empty());
        } else {
            assert!(result.status.success());
            assert!(String::from_utf8_lossy(&result.stdout).contains("coins 42"));
        }
    }
}

#[test]
fn redirected_sample_does_not_create_an_explicit_save() {
    let dir = tempfile::tempdir().unwrap();
    let path = dir.path().join("pet.json");
    let result = Command::new(env!("CARGO_BIN_EXE_mios-tamagotchi"))
        .env_clear()
        .args(["--sample", "--save", path.to_str().unwrap()])
        .output()
        .unwrap();
    assert!(
        result.status.success(),
        "{}",
        String::from_utf8_lossy(&result.stderr)
    );
    assert!(!path.exists());
}

#[test]
fn voxel_and_pixel_headless_dialogues_are_ascii_read_only_without_display() {
    for view in ["voxel", "pixel"] {
        let output = std::process::Command::new(env!("CARGO_BIN_EXE_mios-tamagotchi"))
            .args([
                "--sample",
                "--snapshot",
                "--view",
                view,
                "--hud",
                "minimal",
                "--dialogs",
                "native",
            ])
            .env_remove("DISPLAY")
            .env_remove("WAYLAND_DISPLAY")
            .env_remove("HYPRLAND_INSTANCE_SIGNATURE")
            .env_remove("TMUX")
            .output()
            .unwrap();
        assert!(output.status.success(), "{:?}", output.stderr);
        assert!(output.stdout.is_ascii());
        assert!(!output.stdout.contains(&27));
        let text = String::from_utf8(output.stdout).unwrap();
        assert!(text.contains(" thinks: "));
        assert!(text.contains(" says: "));
    }
}

fn normal_command(root: &std::path::Path) -> Command {
    let mut command = Command::new(env!("CARGO_BIN_EXE_mios-tamagotchi"));
    command
        .env_clear()
        .env("HOME", root.join("private-home"))
        .env("XDG_STATE_HOME", root.join("state"))
        .env("MIOS_VENDOR_TOML", root.join("usr/share/mios/mios.toml"))
        .env("MIOS_VENDOR_TOML_D", root.join("vendor.d"))
        .env("MIOS_HOST_TOML", root.join("etc/mios/mios.toml"))
        .env("MIOS_HOST_TOML_D", root.join("host.d"))
        .env("MIOS_USER_TOML", root.join("user.toml"))
        .env("MIOS_USER_TOML_D", root.join("user.d"));
    command
}

#[test]
fn normal_default_empty_user_and_old_vendor_invocations_are_read_only() {
    let root = root();
    let vendor = root.path().join("usr/share/mios/mios.toml");
    // An older OS may have no game section at all, or only the original settings.
    for old_vendor in [
        "[identity]\nusername='fixture'\n",
        "[tamagotchi]\nrenderer='ascii'\ntick_ms=250\n[tamagotchi.game]\nstarting_coins=17\n",
    ] {
        fs::write(&vendor, old_vendor).unwrap();
        for user in ["", "\n# no overrides\n", "[tamagotchi]\ndialog_mode=''\n"] {
            fs::write(root.path().join("user.toml"), user).unwrap();
            for args in [
                vec![],
                vec!["--snapshot"],
                vec![
                    "--root",
                    root.path().to_str().unwrap(),
                    "--ephemeral",
                    "--snapshot",
                ],
            ] {
                let result = normal_command(root.path()).args(&args).output().unwrap();
                assert!(
                    result.status.success(),
                    "{args:?}: {}",
                    String::from_utf8_lossy(&result.stderr)
                );
                let text = String::from_utf8_lossy(&result.stdout);
                assert!(text.contains("Snapshot only") && text.contains(" thinks: "));
                assert!(result.stdout.is_ascii() && !result.stdout.contains(&27));
                assert!(result.stderr.is_empty());
                assert!(!root.path().join("state").exists());
                assert!(!root.path().join("private-home").exists());
                assert_eq!(fs::read_to_string(&vendor).unwrap(), old_vendor);
                assert_eq!(
                    fs::read_to_string(root.path().join("user.toml")).unwrap(),
                    user
                );
            }
        }
    }
}

#[test]
fn backward_profile_layers_preserve_operator_choices_and_reject_corruption() {
    let root = root();
    fs::write(
        root.path().join("usr/share/mios/mios.toml"),
        "[tamagotchi]\nrenderer='ascii'\n[tamagotchi.game]\nstarting_coins=17\n",
    )
    .unwrap();
    fs::create_dir_all(root.path().join("etc/mios")).unwrap();
    fs::write(
        root.path().join("etc/mios/mios.toml"),
        "[tamagotchi]\ndialog_mode='inline'\n[tamagotchi.game]\nstarting_coins=23\n",
    )
    .unwrap();
    fs::write(
        root.path().join("user.toml"),
        "[tamagotchi]\ntick_ms=''\n[tamagotchi.game]\nstarting_coins=42\n",
    )
    .unwrap();
    let output = normal_command(root.path())
        .arg("--snapshot")
        .output()
        .unwrap();
    assert!(output.status.success(), "{:?}", output.stderr);
    assert!(String::from_utf8_lossy(&output.stdout).contains("coins 42"));
    for corrupt in [
        "[tamagotchi",
        "[tamagotchi]\ndialog_mode='unknown'",
        "[tamagotchi]\ntick_ms=0",
        "[tamagotchi]\nunknown_setting=true",
        "[tamagotchi]\ncharacters=[]",
    ] {
        fs::write(root.path().join("user.toml"), corrupt).unwrap();
        let failed = normal_command(root.path())
            .arg("--snapshot")
            .output()
            .unwrap();
        assert!(!failed.status.success(), "{corrupt}");
        assert!(failed.stdout.is_empty() && !failed.stderr.contains(&27));
        assert_eq!(
            fs::read_to_string(root.path().join("user.toml")).unwrap(),
            corrupt
        );
        assert!(!root.path().join("state").exists());
    }
}
