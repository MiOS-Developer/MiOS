// AI-hint: Integration tests for the mios-resolver emit bindings: characterization snapshots over vendor and host fixtures, and a proptest differential over arbitrary [ports] tables.
// AI-related: tools/native/mios-resolver/src/emit.rs, tools/check-runtime.py

mod cli {
    use mios_resolver::emit_json::emit_json;
    use mios_resolver::emit_shell::emit_shell;

    #[test]
    fn rooted_cli_honors_explicit_vendor_selectors() {
        let scratch = tempfile::tempdir().unwrap();
        let root = scratch.path();
        let vendor = root.join("usr/share/mios/mios.toml");
        std::fs::create_dir_all(vendor.parent().unwrap()).unwrap();
        std::fs::write(&vendor, "[selector_probe]\nvalue = 'root-default'\n").unwrap();
        let alias = root.join("operator.toml");
        std::fs::write(&alias, "[selector_probe]\nvalue = 'operator-alias'\n").unwrap();
        let canonical = root.join("canonical.toml");
        std::fs::write(&canonical, "[selector_probe]\nvalue = 'canonical-vendor'\n").unwrap();

        // A child environment keeps concurrent tests independent of these inputs.
        for cli_root in [false, true] {
            for selector in ["default", "alias", "canonical", "missing"] {
                let mut command = std::process::Command::new(env!("CARGO_BIN_EXE_mios-resolver"));
                command.env_clear().arg("--emit=json");
                command.env("MIOS_HOST_TOML", root.join("absent-host.toml"));
                command.env("MIOS_USER_TOML", root.join("absent-user.toml"));
                if cli_root {
                    command.arg("--root").arg(root);
                } else {
                    command.env("MIOS_TOML_ROOT", root);
                }
                if selector != "default" {
                    command.env("MIOS_TOML", &alias);
                }
                if selector == "canonical" {
                    command.env("MIOS_VENDOR_TOML", &canonical);
                } else if selector == "missing" {
                    command.env("MIOS_TOML", root.join("absent-operator.toml"));
                }
                let output = command.output().unwrap();
                assert!(
                    output.status.success(),
                    "{cli_root}/{selector}: {:?}",
                    output
                );
                let json: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
                let expected = match selector {
                    "alias" => Some("operator-alias"),
                    "canonical" => Some("canonical-vendor"),
                    "missing" => None,
                    _ => Some("root-default"),
                };
                assert_eq!(
                    json["merged"]["selector_probe"]["value"].as_str(),
                    expected,
                    "{cli_root}/{selector}"
                );
            }
        }
    }

    #[test]
    fn user_empty_string_never_overrides_vendor_endpoint() {
        let scratch = tempfile::tempdir().unwrap();
        let root = scratch.path();
        let vendor = root.join("usr/share/mios/mios.toml");
        std::fs::create_dir_all(vendor.parent().unwrap()).unwrap();
        std::fs::write(&vendor, "[ai]\nendpoint = 'http://vendor:1/v1'\n").unwrap();

        let user = root.join("user.toml");
        std::fs::write(&user, "[ai]\nendpoint = ''\n").unwrap();

        let mut command = std::process::Command::new(env!("CARGO_BIN_EXE_mios-resolver"));
        command.env_clear().arg("--emit=json");
        command.env("MIOS_VENDOR_TOML", &vendor);
        command.env("MIOS_USER_TOML", &user);
        command.env("MIOS_HOST_TOML", root.join("absent-host.toml"));

        let output = command.output().unwrap();
        assert!(output.status.success(), "{:?}", output);
        let json: serde_json::Value = serde_json::from_slice(&output.stdout).unwrap();
        assert_eq!(
            json["merged"]["ai"]["endpoint"].as_str(),
            Some("http://vendor:1/v1"),
            "merged.ai.endpoint must retain vendor URL when user sets empty string"
        );
        assert_eq!(
            json["exports"]["MIOS_AI_ENDPOINT"].as_str(),
            Some("http://vendor:1/v1"),
            "exports.MIOS_AI_ENDPOINT must be emitted with vendor URL"
        );
    }

    #[test]
    fn test_cli_emit_shell_snapshot() {
        let val: toml::Value = toml::from_str(
            r#"
[identity]
role = "mini"
"#,
        )
        .unwrap();
        let shell_out = emit_shell(&val, 0, None);
        // shlex_quote leaves a bare-safe word UNQUOTED, matching Python's
        // shlex.quote("mini") == "mini" (and emit_shell's own test_shlex_quote,
        // which asserts shlex_quote("simple") == "simple"). The previous
        // assertion demanded 'mini' and could never pass.
        assert!(
            shell_out.contains("export MIOS_IDENTITY_ROLE=mini"),
            "got: {shell_out}"
        );
    }

    #[test]
    fn test_cli_emit_json_snapshot() {
        let val: toml::Value = toml::from_str(
            r#"
[identity]
role = "mini"
"#,
        )
        .unwrap();
        let json_out = emit_json(&val, 0);
        assert!(json_out.contains("\"role\": \"mini\""));
    }

    #[test]
    fn test_miette_error_diagnostic_format() {
        use mios_resolver::error::ResolverError;
        let err = ResolverError::InvalidColorHex {
            key: "accent".to_string(),
            value: "not-a-color".to_string(),
        };
        let display_str = format!("{}", err);
        assert!(display_str.contains("Invalid hex color format"));
        assert!(display_str.contains("accent"));
    }

    #[test]
    fn test_characterization_fixtures() {
        let vendor_only_content = include_str!("fixtures/vendor_only.toml");
        let val: toml::Value = toml::from_str(vendor_only_content).unwrap();
        let shell_out = emit_shell(&val, 0, None);
        assert!(shell_out.contains("export MIOS_IDENTITY_ROLE=mini"));
        assert!(shell_out.contains("export MIOS_PORTS_HERMES=8080"));
    }
}

mod differential {
    use mios_resolver::emit_json::emit_json;
    use proptest::prelude::*;

    proptest! {
        #[test]
        fn test_resolver_proptest_valid_ports(
            stack_id in 0i64..10i64,
            port_val in 1000i64..9000i64,
        ) {
            let toml_str = format!(
                "[ports]\nstack_id = {}\nssh = {}\n",
                stack_id, port_val
            );
            let val: toml::Value = toml::from_str(&toml_str).unwrap();
            let json_out = emit_json(&val, stack_id * 10000);
            prop_assert!(json_out.contains("\"ssh\":"));
        }
    }
}
