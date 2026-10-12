// AI-hint: Integration tests for mios-service-core: dashboard catalog, probe and layout controls, SSOT host tmux projection, and the shared SSOT, port and process helpers.
// AI-related: /usr/share/mios/templates/rust, tools/native/mios-service-core/src/dashboard.rs, tools/native/mios-service-core, usr/share/mios/mios.toml

use std::sync::{Mutex, MutexGuard};

/// One test at a time: several modules edit real-tree files under a restore guard.
fn tree_lock() -> MutexGuard<'static, ()> {
    static LOCK: Mutex<()> = Mutex::new(());
    LOCK.lock().unwrap_or_else(|e| e.into_inner())
}

mod dashboard {
    use mios_service_core::dashboard::{catalog, probe, render};
    use serde_json::{json, Value};
    use std::net::TcpListener;
    use std::time::Duration;

    fn config() -> Value {
        json!({"meta":{"mios_version":"test-version"},"theme":{"font":{"family":"Operator Font","size":17}},
        "dashboard":{"title":"Operator dashboard","show_title":true,"show_services":true,"show_verb_hints":true,"verb_hint":"operator verbs","rows":[["cpu","ram"],["version","font"]]},
        "ports":{"categories":{"agent":{"members":["agent_pipe",""],"pinned":{"dns":53}},"webui":{"members":["open_webui"]}},"agent_pipe":17400,"dns":53,"open_webui":18200}})
    }

    #[test]
    fn every_category_member_and_pin_survives_width_and_operator_mutations() -> Result<(), String> {
        let _tree = super::tree_lock();
        let mut config = config();
        config["ports"]["categories"]["agent"]["members"] =
            json!(["agent_pipe", "operator_service"]);
        config["ports"]["operator_service"] = json!(19500);
        let endpoints = catalog(&config)?;
        assert_eq!(endpoints.len(), 4);
        for width in [24, 59, 60, 80, 141] {
            let text = render(
                &config,
                &json!([{ "type":"CPU", "result":{"cpu":"Long processor 漢字 name with all its information intact"}}]),
                &endpoints,
                width,
            )?;
            assert!(text
                .lines()
                .all(|line| unicode_width::UnicodeWidthStr::width(line) == width));
            let joined = text.replace(['\n', '|', ' '], "");
            for value in [
                "operator_service",
                "19500",
                "dns",
                "53",
                "open_webui",
                "18200",
                "test-version",
                "OperatorFont",
                "17pt",
            ] {
                assert!(
                    joined.contains(value),
                    "missing {value} at width {width}: {text}"
                );
            }
            let left: String = text
                .lines()
                .filter_map(|line| line.split('|').nth(1))
                .collect();
            assert!(left
                .replace(' ', "")
                .contains("processor漢字namewithallitsinformationintact"));
        }
        Ok(())
    }

    #[test]
    fn invalid_or_empty_catalogs_and_metrics_fail_instead_of_rendering_old_defaults() {
        let _tree = super::tree_lock();
        for patch in [json!({}), json!({"empty":{"members":[]}})] {
            let mut doc = config();
            doc["ports"]["categories"] = patch;
            assert!(catalog(&doc).is_err());
        }
        let mut doc = config();
        doc["ports"]["agent_pipe"] = json!(70000);
        assert!(catalog(&doc).is_err());
        doc = config();
        doc["ports"]["categories"]["webui"]["members"] = json!(["agent_pipe"]);
        assert!(catalog(&doc).is_err());
        doc = config();
        doc["dashboard"]["rows"] = json!([["new_unknown_metric"]]);
        assert!(render(&doc, &json!([]), &catalog(&doc).expect("valid fixture"), 80).is_err());
        doc = config();
        assert!(render(&doc, &json!([]), &[], 80).is_err());
        assert!(render(&doc, &json!([]), &[], 0).is_err());
    }

    #[test]
    fn stack_offset_applies_to_the_same_catalog_and_rejects_overflow() -> Result<(), String> {
        let _tree = super::tree_lock();
        let mut doc = config();
        doc["ports"]["stack_id"] = json!(1);
        let endpoints = catalog(&doc)?;
        assert_eq!(
            endpoints
                .iter()
                .find(|e| e.name == "agent_pipe")
                .map(|e| e.port),
            Some(27400)
        );
        doc["ports"]["stack_id"] = json!(7);
        assert!(catalog(&doc).is_err());
        Ok(())
    }

    #[test]
    fn real_listener_is_open_and_unused_socket_is_closed() -> Result<(), Box<dyn std::error::Error>>
    {
        let _tree = super::tree_lock();
        let listener = TcpListener::bind("127.0.0.1:0")?;
        let unused = TcpListener::bind("127.0.0.1:0")?;
        let closed = unused.local_addr()?.port();
        drop(unused);
        let mut doc = config();
        doc["ports"]["agent_pipe"] = json!(listener.local_addr()?.port());
        doc["ports"]["open_webui"] = json!(closed);
        let mut endpoints = catalog(&doc)?;
        probe(&mut endpoints, Duration::from_millis(100))?;
        assert_eq!(
            endpoints
                .iter()
                .find(|e| e.name == "agent_pipe")
                .map(|e| e.state.as_str()),
            Some("open")
        );
        assert_eq!(
            endpoints
                .iter()
                .find(|e| e.name == "open_webui")
                .map(|e| e.state.as_str()),
            Some("closed")
        );
        Ok(())
    }

    #[test]
    fn structured_hardware_keeps_gpu_types_and_sums_swap_devices_without_inventing_missing_disk_values(
    ) -> Result<(), String> {
        let _tree = super::tree_lock();
        let facts = json!([
            {"type":"GPU","result":[{"type":"Integrated","name":"Integrated GPU"},{"type":"Discrete","name":"Discrete GPU"}]},
            {"type":"Swap","result":[{"used":1073741824_u64,"total":2147483648_u64},{"used":0,"total":2147483648_u64}]},
            {"type":"Disk","result":[{"mountpoint":"C:\\","bytes":{"total":1073741824_u64}}]}
        ]);
        let values = mios_service_core::dashboard::metrics(&config(), &facts)?;
        assert_eq!(
            values.get("gpu_discrete").map(String::as_str),
            Some("Discrete GPU")
        );
        assert_eq!(
            values.get("gpu_integrated").map(String::as_str),
            Some("Integrated GPU")
        );
        assert_eq!(
            values.get("swap").map(String::as_str),
            Some("1.0 / 4.0 GiB (25%)")
        );
        assert_eq!(
            values.get("disk_c").map(String::as_str),
            Some("unavailable")
        );
        Ok(())
    }
}

mod host_tmux {
    use mios_service_core::{host_tmux, launcher::host_tmux_config_with_font};
    use serde_json::{json, Value};
    use std::fs;

    fn config() -> Value {
        let doc: toml::Value =
            toml::from_str(include_str!("../../../../usr/share/mios/mios.toml")).unwrap();
        serde_json::to_value(doc).unwrap()
    }

    #[test]
    fn renders_real_palette_keys_shell_and_safe_glyph_fallback() {
        let _tree = super::tree_lock();
        let mut config = config();
        config["terminal"]["windows"]["glyph_mode"] = json!("auto");
        let rendered = host_tmux_config_with_font(&config, false).unwrap();
        assert!(rendered.contains(config["colors"]["fg"].as_str().unwrap()));
        assert!(rendered.contains("set -g status-position bottom"));
        assert!(rendered.contains("bind-key h select-pane -L"));
        assert!(rendered.contains("mios.cmd ai"));
        assert!(rendered.contains("mios.cmd agents --watch"));
        assert!(!rendered.contains("/usr/libexec/"));
        assert!(rendered.contains("set -g history-limit 9000"));
        assert!(rendered.contains("set -g default-shell \"cmd.exe\""));
        assert!(
            rendered.is_ascii(),
            "unknown font must not emit private-use glyphs"
        );
        assert!(
            rendered.lines().count() > 55,
            "must render the complete configuration"
        );
    }

    #[test]
    fn changed_operator_values_reach_theme_and_native_shell() {
        let _tree = super::tree_lock();
        let mut config = config();
        config["colors"]["fg"] = json!("#ABCDEF");
        config["theme"]["tmux"]["status_position"] = json!("top");
        config["theme"]["tmux"]["status_interval_s"] = json!(7);
        config["keybindings"]["mouse"] = json!(false);
        config["terminal"]["scrollback_rows"] = json!(1234);
        config["terminal"]["windows"]["shell"] = json!(r"C:\Program Files\PowerShell\7\pwsh.exe");
        let rendered = host_tmux_config_with_font(&config, false).unwrap();
        for expected in [
            "#ABCDEF",
            "set -g status-position top",
            "set -g status-interval 7",
            "set -g mouse off",
            "set -g history-limit 1234",
            r"C:\\Program Files\\PowerShell\\7\\pwsh.exe",
        ] {
            assert!(rendered.contains(expected), "missing override: {expected}");
        }
        config["terminal"]["windows"]["glyph_mode"] = json!("nerd");
        config["theme"]["tmux"]["glyph_mode"] = json!("nerd");
        assert!(host_tmux_config_with_font(&config, true)
            .unwrap()
            .contains(''));
    }

    #[test]
    fn invalid_policy_and_config_injection_fail_before_publication() {
        let _tree = super::tree_lock();
        for (path, value, error) in [
            ("/colors/fg", json!("red"), "colors"),
            ("/theme/tmux/status_interval_s", json!(0), "positive"),
            (
                "/terminal/windows/glyph_mode",
                json!("mystery"),
                "glyph_mode",
            ),
            (
                "/terminal/windows/shell",
                json!("cmd.exe\nrun-shell evil"),
                "argument",
            ),
            ("/keybindings/tmux_prefix", json!("C-\n"), "tmux_prefix"),
        ] {
            let mut config = config();
            *config.pointer_mut(path).unwrap() = value;
            let tmp = tempfile::tempdir().unwrap();
            let target = tmp.path().join("host.conf");
            fs::write(
                &target,
                "# Generated from layered MiOS SSOT by native Rust.\nprevious",
            )
            .unwrap();
            let old = fs::read(&target).unwrap();
            let failure = host_tmux::stage(&config, &target, &[], false).unwrap_err();
            assert!(failure.contains(error), "{path}: {failure}");
            assert_eq!(fs::read(&target).unwrap(), old);
            assert_eq!(fs::read_dir(tmp.path()).unwrap().count(), 1);
        }
    }

    #[test]
    fn publication_backs_up_owned_legacy_and_preserves_operator_config() {
        let _tree = super::tree_lock();
        let tmp = tempfile::tempdir().unwrap();
        let canonical = tmp.path().join("terminal/host.conf");
        let legacy = tmp.path().join(".tmux.conf");
        let operator = tmp.path().join("operator.conf");
        let old = "# AI-hint: MiOS Windows Native Tmux Configuration\nold theme\n";
        fs::write(&legacy, old).unwrap();
        fs::write(&operator, "# Operator custom settings\nset -g prefix C-a\n").unwrap();
        let reload = host_tmux::stage(
            &config(),
            &canonical,
            &[legacy.clone(), operator.clone()],
            false,
        )
        .unwrap();
        assert!(reload.contains(&legacy));
        assert!(!reload.contains(&operator));
        assert_eq!(fs::read(&canonical).unwrap(), fs::read(&legacy).unwrap());
        assert!(fs::read_to_string(&operator).unwrap().contains("C-a"));
        let backups: Vec<_> = fs::read_dir(tmp.path())
            .unwrap()
            .flatten()
            .filter(|e| e.file_name().to_string_lossy().contains("pre-native"))
            .collect();
        assert_eq!(backups.len(), 1);
        assert_eq!(fs::read_to_string(backups[0].path()).unwrap(), old);
        host_tmux::stage(&config(), &canonical, &[legacy, operator], false).unwrap();
        assert_eq!(
            fs::read_dir(tmp.path()).unwrap().count(),
            4,
            "identical re-stage must not add backups"
        );
    }

    #[test]
    fn non_regular_destination_rejected_before_any_write() {
        let _tree = super::tree_lock();
        let tmp = tempfile::tempdir().unwrap();
        let canonical = tmp.path().join("host.conf");
        let legacy = tmp.path().join("directory.conf");
        fs::create_dir(&legacy).unwrap();
        assert!(host_tmux::stage(&config(), &canonical, &[legacy], false)
            .unwrap_err()
            .contains("non-regular"));
        assert!(!canonical.exists());
    }

    #[test]
    fn console_palette_uses_named_defaults_and_windows_slot_order() {
        let _tree = super::tree_lock();
        let mut config = config();
        config["colors"]["bg"] = json!("#123456");
        config["colors"]["fg"] = json!("#ABCDEF");
        config["colors"]["ansi_4_blue"] = json!("#010203");
        let palette = mios_service_core::launcher::windows_console_palette(&config).unwrap();
        assert_eq!(palette[0], 0x563412);
        assert_eq!(palette[7], 0xEFCDAB);
        assert_eq!(palette[1], 0x030201);
        config["colors"]["ansi_4_blue"] = json!("invalid");
        assert!(
            mios_service_core::launcher::windows_console_palette(&config)
                .unwrap_err()
                .contains("ansi_4_blue")
        );
    }
}

mod test_service_core {
    use mios_service_core::{
        check_socket_path_length, configure_hidden, find_active_socket,
        find_active_socket_required, is_hidden_flag, require_port, require_str,
        resolve_ai_endpoint, socket_candidates, validate_workspace_socket, verify_socket_owner,
        workspace_lock, ConfigError, SocketError, CREATE_NO_WINDOW, MAX_SOCKET_PATH_LEN,
    };
    use std::fs::{self, File};
    use std::path::PathBuf;
    use std::process::Command;
    use tempfile::tempdir;

    #[test]
    fn test_socket_path_length_bounds() {
        let _tree = super::tree_lock();
        let short_path = PathBuf::from("/run/mios-tmux/human.sock");
        assert!(check_socket_path_length(&short_path).is_ok());

        let exact_limit = PathBuf::from(format!("/tmp/{}", "a".repeat(MAX_SOCKET_PATH_LEN - 5)));
        assert_eq!(exact_limit.as_os_str().len(), MAX_SOCKET_PATH_LEN);
        assert!(check_socket_path_length(&exact_limit).is_ok());

        let long_path = PathBuf::from(format!("/run/mios-tmux/{}", "x".repeat(120)));
        assert!(long_path.as_os_str().len() >= 108);
        match check_socket_path_length(&long_path) {
            Err(SocketError::PathTooLong { len, max, .. }) => {
                assert!(len >= 108);
                assert_eq!(max, 107);
            }
            other => panic!("Expected PathTooLong, got {:?}", other),
        }
    }

    #[test]
    fn test_find_active_socket_first_match() {
        let _tree = super::tree_lock();
        let dir = tempdir().unwrap();
        let s1 = dir.path().join("missing.sock");
        let s2 = dir.path().join("active.sock");
        let s3 = dir.path().join("later.sock");

        File::create(&s2).unwrap();
        File::create(&s3).unwrap();

        let candidates = vec![&s1, &s2, &s3];
        let found = find_active_socket(&candidates).unwrap();
        assert_eq!(found, Some(s2.clone()));

        let required = find_active_socket_required(&candidates).unwrap();
        assert_eq!(required, s2);
    }

    #[test]
    fn test_find_active_socket_none_found() {
        let _tree = super::tree_lock();
        let dir = tempdir().unwrap();
        let s1 = dir.path().join("missing1.sock");
        let s2 = dir.path().join("missing2.sock");

        let candidates = vec![&s1, &s2];
        assert_eq!(find_active_socket(&candidates).unwrap(), None);

        assert!(matches!(
            find_active_socket_required(&candidates),
            Err(SocketError::NoActiveSocket)
        ));

        let empty: Vec<&PathBuf> = Vec::new();
        assert_eq!(find_active_socket(&empty).unwrap(), None);
    }

    #[test]
    fn test_verify_socket_owner() {
        let _tree = super::tree_lock();
        let dir = tempdir().unwrap();
        let sock = dir.path().join("valid.sock");
        File::create(&sock).unwrap();

        assert!(verify_socket_owner(&sock).unwrap());

        let missing = dir.path().join("nonexistent.sock");
        assert!(matches!(
            verify_socket_owner(&missing),
            Err(SocketError::NotFound(_))
        ));
    }

    #[cfg(unix)]
    fn create_test_socket_fixture(
        path: &std::path::Path,
    ) -> Option<std::os::unix::net::UnixListener> {
        let _ = std::fs::remove_file(path);
        Some(std::os::unix::net::UnixListener::bind(path).expect("failed to bind test unix socket"))
    }

    #[cfg(not(unix))]
    fn create_test_socket_fixture(path: &std::path::Path) -> Option<()> {
        File::create(path).expect("failed to create test socket file");
        None
    }

    #[test]
    fn test_socket_candidates_discovery() {
        let _tree = super::tree_lock();
        let dir = tempdir().unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(dir.path(), std::fs::Permissions::from_mode(0o700));
        }
        #[cfg(unix)]
        let root = dir
            .path()
            .canonicalize()
            .unwrap_or_else(|_| dir.path().to_path_buf());
        #[cfg(not(unix))]
        let root = dir.path().to_path_buf();

        let human_sock = root.join("human");
        let _l1 = create_test_socket_fixture(&human_sock);

        let mcp_sock = root.join("mcp-headless");
        let _l2 = create_test_socket_fixture(&mcp_sock);

        let sub_dir = root.join("tmux-1000");
        fs::create_dir(&sub_dir).unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(&sub_dir, std::fs::Permissions::from_mode(0o700));
        }
        let sub_human = sub_dir.join("human");
        let _l3 = create_test_socket_fixture(&sub_human);

        let candidates = socket_candidates(&root, "human", 0);
        assert!(!candidates.is_empty());
        assert!(candidates.contains(&human_sock));
    }

    #[test]
    fn test_validate_workspace_socket() {
        let _tree = super::tree_lock();
        let dir = tempdir().unwrap();
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = std::fs::set_permissions(dir.path(), std::fs::Permissions::from_mode(0o700));
        }
        #[cfg(unix)]
        let root = dir
            .path()
            .canonicalize()
            .unwrap_or_else(|_| dir.path().to_path_buf());
        #[cfg(not(unix))]
        let root = dir.path().to_path_buf();

        let sock = root.join("mios-test.sock");
        let _l = create_test_socket_fixture(&sock);

        // Absolute, valid name starting with "mios-"
        let res = validate_workspace_socket(&sock, "human");
        assert!(res.is_ok());

        // Relative path should fail
        let rel = PathBuf::from("mios-test.sock");
        assert!(validate_workspace_socket(&rel, "human").is_err());
    }

    #[test]
    fn test_require_port_ssot_reading() {
        let _tree = super::tree_lock();
        // Read headscale port from SSOT
        let headscale_port = require_port("headscale").unwrap();
        assert_eq!(headscale_port, 8085);

        // Read llm_light port from SSOT
        let llm_port = require_port("llm_light").unwrap();
        assert_eq!(llm_port, 8500);

        // Read with MIOS_PORT_ prefix format
        let llm_port_prefix = require_port("MIOS_PORTS_LLM_LIGHT").unwrap();
        assert_eq!(llm_port_prefix, 8500);
    }

    #[test]
    fn test_require_port_missing_key() {
        let _tree = super::tree_lock();
        let err = require_port("nonexistent_service_port_xyz").unwrap_err();
        assert!(matches!(err, ConfigError::MissingKey(_)));
    }

    #[test]
    fn test_require_port_bounds_and_env_override() {
        let _tree = super::tree_lock();
        std::env::set_var("MIOS_PORT_TEST_BOUNDS", "9999");
        assert_eq!(require_port("test_bounds").unwrap(), 9999);

        std::env::set_var("MIOS_PORTS_TEST_BOUNDS", "10000");
        for key in [
            "test_bounds",
            "ports.test_bounds",
            "MIOS_PORT_TEST_BOUNDS",
            "MIOS_PORTS_TEST_BOUNDS",
        ] {
            assert_eq!(require_port(key).unwrap(), 10000);
        }
        std::env::set_var("MIOS_PORTS_TEST_BOUNDS", "70000");
        assert!(matches!(
            require_port("test_bounds"),
            Err(ConfigError::InvalidPort { .. })
        ));
        std::env::remove_var("MIOS_PORTS_TEST_BOUNDS");

        std::env::set_var("MIOS_PORT_TEST_BOUNDS", "0");
        assert!(matches!(
            require_port("test_bounds").unwrap_err(),
            ConfigError::InvalidPort { .. }
        ));

        std::env::set_var("MIOS_PORT_TEST_BOUNDS", "70000");
        assert!(matches!(
            require_port("test_bounds").unwrap_err(),
            ConfigError::InvalidPort { .. }
        ));

        std::env::set_var("MIOS_PORT_TEST_BOUNDS", "not_a_number");
        assert!(matches!(
            require_port("test_bounds").unwrap_err(),
            ConfigError::InvalidPort { .. }
        ));

        std::env::remove_var("MIOS_PORT_TEST_BOUNDS");
    }

    #[test]
    fn test_resolve_ai_endpoint_law_5() {
        let _tree = super::tree_lock();
        let ep = resolve_ai_endpoint().unwrap();
        assert!(!ep.is_empty());
        assert!(!ep.contains("api.openai.com"));
        assert!(!ep.contains("generativelanguage.googleapis.com"));
        assert!(!ep.contains("api.anthropic.com"));
        assert!(ep.contains("127.0.0.1") || ep.contains("localhost"));
    }

    #[test]
    fn test_resolve_endpoint_prohibits_cloud() {
        let _tree = super::tree_lock();
        assert!(matches!(
            mios_service_core::ssot::validate_no_cloud_endpoints("https://api.openai.com/v1"),
            Err(ConfigError::ForbiddenCloudEndpoint(_))
        ));
        assert!(matches!(
            mios_service_core::ssot::validate_no_cloud_endpoints(
                "https://generativelanguage.googleapis.com/v1"
            ),
            Err(ConfigError::ForbiddenCloudEndpoint(_))
        ));
        assert!(matches!(
            mios_service_core::ssot::validate_no_cloud_endpoints("https://api.anthropic.com/v1"),
            Err(ConfigError::ForbiddenCloudEndpoint(_))
        ));
        assert!(
            mios_service_core::ssot::validate_no_cloud_endpoints("http://127.0.0.1:8500/v1")
                .is_ok()
        );
    }

    #[test]
    fn test_require_str_reading() {
        let _tree = super::tree_lock();
        let version = require_str("meta.mios_version").unwrap();
        assert_eq!(version, "0.3.0");
    }

    #[test]
    fn test_process_hidden_window_flag() {
        let _tree = super::tree_lock();
        assert_eq!(CREATE_NO_WINDOW, 0x0800_0000);
        assert!(is_hidden_flag(CREATE_NO_WINDOW));
        assert!(is_hidden_flag(CREATE_NO_WINDOW | 0x0000_0001));
        assert!(!is_hidden_flag(0));
        assert!(!is_hidden_flag(0x0000_0001));

        let mut cmd = Command::new("cargo");
        configure_hidden(&mut cmd);
    }

    #[test]
    fn test_workspace_locking() {
        let _tree = super::tree_lock();
        let dir = tempdir().unwrap();
        let sock = dir.path().join("test.sock");
        File::create(&sock).unwrap();

        let lock_file = workspace_lock(&sock, "test.lock").unwrap();
        assert!(dir.path().join("test.lock").exists());
        drop(lock_file);
    }
}
