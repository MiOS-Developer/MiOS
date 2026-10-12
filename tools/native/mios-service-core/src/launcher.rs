// AI-hint: Shared native terminal policy and monitor layouts for Windows and Linux consumers.
// AI-related: usr/share/mios/mios.toml, tools/native/mios-resolver/src/emit.rs
use serde_json::Value;

/// One validated terminal policy consumed by Linux and Windows launchers.
pub fn terminal_config(
    config: &Value,
    requested: &str,
    default_action: bool,
) -> Result<[String; 9], String> {
    let identifier = |path: &str, spaces: bool| -> Result<String, String> {
        let value = text(config, path)?;
        if !value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_' || (spaces && c == b' '))
        {
            return Err(format!("Invalid SSOT tmux identifier {path}"));
        }
        Ok(value)
    };
    let selected = if requested.is_empty() && default_action {
        text(config, "/terminal/default_action")?
    } else {
        requested.into()
    };
    let actions = config
        .pointer("/keybindings/actions")
        .and_then(Value::as_array)
        .ok_or("SSOT keybindings.actions must be an array")?;
    let action = |id: &str, key: &str| -> Result<String, String> {
        let item = actions
            .iter()
            .find(|a| a["id"].as_str() == Some(id))
            .ok_or_else(|| format!("Unknown MiOS terminal action: {id}"))?;
        let value = item[key]
            .as_str()
            .filter(|s| !s.is_empty() && !s.chars().any(char::is_control))
            .ok_or_else(|| format!("Invalid terminal action {id}.{key}"))?;
        Ok(value.into())
    };
    let path = |key: &str| -> Result<String, String> {
        let value = text(config, key)?;
        if !value.starts_with('/') {
            return Err(format!("SSOT {key} must be an absolute guest path"));
        }
        Ok(value)
    };
    Ok([
        identifier("/keybindings/socket_name", false)?,
        identifier("/keybindings/terminal_session", false)?,
        identifier("/mcp/agents/observation/window_name", true)?,
        action("agents", "command")?,
        if selected.is_empty() {
            String::new()
        } else {
            action(&selected, "command")?
        },
        if selected.is_empty() {
            String::new()
        } else {
            action(&selected, "label")?
        },
        selected,
        path("/terminal/start_directory")?,
        path("/terminal/socket_root")?,
    ])
}

pub fn host_tmux_args(config: &Value, config_path: &str) -> Result<Vec<String>, String> {
    if text(config, "/terminal/windows/backend")? != "native-tmux" {
        return Err("Unsupported SSOT terminal.windows.backend".into());
    }
    let socket = text(config, "/terminal/windows/socket_name")?;
    let session = text(config, "/terminal/windows/session_name")?;
    for name in [&socket, &session] {
        if !name
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')
        {
            return Err("Invalid native Windows tmux namespace".into());
        }
    }
    Ok(vec![
        "-L".into(),
        socket,
        "-u".into(),
        "-f".into(),
        argument(config_path)?,
        "new-session".into(),
        "-A".into(),
        "-s".into(),
        session,
        text(config, "/terminal/windows/shell")?,
    ])
}

/// Render host policy with conservative glyphs until the outer client font is verified.
pub fn host_tmux_config(config: &Value) -> Result<String, String> {
    host_tmux_config_with_font(config, false)
}

pub fn host_tmux_config_with_font(config: &Value, font_verified: bool) -> Result<String, String> {
    let rows = config
        .pointer("/terminal/scrollback_rows")
        .and_then(Value::as_u64)
        .filter(|n| *n > 0)
        .ok_or("Invalid SSOT terminal.scrollback_rows")?;
    let shell = text(config, "/terminal/windows/shell")?;
    let mut doc: toml::Value =
        toml::Value::try_from(config).map_err(|e| format!("Invalid tmux SSOT: {e}"))?;
    let mut engine = crate::tmux_theme::TmuxThemeEngine::from_toml(&doc, None, None)?;
    let mode = text(config, "/terminal/windows/glyph_mode")?;
    match mode.as_str() {
        "ascii" => engine.style = "minimal".into(),
        "auto" if !font_verified => engine.style = "minimal".into(),
        "auto" | "nerd" => (),
        _ => return Err("Invalid SSOT terminal.windows.glyph_mode".into()),
    }
    let actions = doc
        .get_mut("keybindings")
        .and_then(|v| v.get_mut("actions"))
        .and_then(toml::Value::as_array_mut)
        .ok_or("Missing SSOT keybindings.actions")?;
    for action in actions {
        let windows = action
            .get("tmux_command_windows")
            .and_then(toml::Value::as_str)
            .filter(|s| !s.is_empty() && !s.chars().any(char::is_control))
            .ok_or("Missing or invalid SSOT keybindings.actions.tmux_command_windows")?
            .to_owned();
        action
            .as_table_mut()
            .ok_or("Invalid SSOT keybinding action")?
            .insert("tmux_command".into(), toml::Value::String(windows));
    }
    let bindings = doc
        .get_mut("keybindings")
        .and_then(|v| v.get_mut("tmux"))
        .and_then(|v| v.get_mut("bindings"))
        .and_then(toml::Value::as_array_mut)
        .ok_or("Missing SSOT keybindings.tmux.bindings")?;
    for binding in bindings {
        if let Some(windows) = binding.get("command_windows") {
            let windows = windows
                .as_str()
                .filter(|s| !s.is_empty() && !s.chars().any(char::is_control))
                .ok_or("Invalid SSOT keybindings.tmux.bindings.command_windows")?
                .to_owned();
            binding
                .as_table_mut()
                .ok_or("Invalid tmux binding")?
                .insert("command".into(), toml::Value::String(windows));
        } else if binding
            .get("command")
            .and_then(toml::Value::as_str)
            .is_some_and(|s| s.starts_with("run-shell"))
        {
            return Err(
                "SSOT run-shell binding requires command_windows for host execution".into(),
            );
        }
    }
    let keys =
        mios_unit_gen::render_keybindings(&toml::to_string(&doc).map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())?;
    let keys = keys
        .get("usr/share/mios/tmux/mios-keys.tmux.conf")
        .ok_or("Native keybindings projection omitted tmux")?;
    let shell = shell.replace('\\', "\\\\").replace('"', "\\\"");
    // Theme and keys come from the same engines as the Linux image and runtime.
    // Terminal history wins over the cross-client keyboard history preference.
    Ok(format!("# Generated from layered MiOS SSOT by native Rust.\n{}\n{}\nset -g history-limit {rows}\nset -g default-shell \"{shell}\"\nset -g default-command \"{shell}\"\nset -g automatic-rename on\nset -g automatic-rename-format \"#{{b:pane_current_command}}\"\n", engine.generate_config()?, keys))
}

/// COLORREF values in Windows Console's BGR-indexed ANSI order. Named default
/// foreground/background remain authoritative over their ANSI slot aliases.
pub fn windows_console_palette(config: &Value) -> Result<[u32; 16], String> {
    let keys = [
        "bg",
        "ansi_4_blue",
        "ansi_2_green",
        "ansi_6_cyan",
        "ansi_1_red",
        "ansi_5_magenta",
        "ansi_3_yellow",
        "fg",
        "ansi_8_bright_black",
        "ansi_12_bright_blue",
        "ansi_10_bright_green",
        "ansi_14_bright_cyan",
        "ansi_9_bright_red",
        "ansi_13_bright_magenta",
        "ansi_11_bright_yellow",
        "ansi_15_bright_white",
    ];
    let mut palette = [0; 16];
    for (slot, key) in keys.iter().enumerate() {
        let value = config["colors"][key]
            .as_str()
            .ok_or_else(|| format!("Missing SSOT colors.{key}"))?;
        if value.len() != 7
            || !value.starts_with('#')
            || !value.as_bytes()[1..].iter().all(u8::is_ascii_hexdigit)
        {
            return Err(format!("Invalid SSOT colors.{key}"));
        }
        let rgb = u32::from_str_radix(&value[1..], 16).map_err(|e| e.to_string())?;
        palette[slot] = ((rgb & 255) << 16) | (rgb & 0xff00) | (rgb >> 16);
    }
    Ok(palette)
}

fn argument(value: &str) -> Result<String, String> {
    // WT treats semicolons as command separators even without a shell.
    if value.is_empty() || value.chars().any(|c| c.is_control() || c == ';') {
        return Err("Invalid Windows Terminal argument".into());
    }
    Ok(value.into())
}

fn text(config: &Value, path: &str) -> Result<String, String> {
    argument(
        config
            .pointer(path)
            .and_then(Value::as_str)
            .ok_or_else(|| format!("Missing SSOT {path}"))?,
    )
}

/// All policy (backend, window, geometry, profile, theme and mode) comes from
/// the resolver. The caller supplies only the discovered executable/assets.
pub fn render_monitor(
    config: &Value,
    python: &str,
    script: &str,
    shell: &str,
    portrait: bool,
) -> Result<Vec<String>, String> {
    if text(config, "/terminal/monitor/windows_backend")? != "windows-terminal" {
        return Err("Unsupported SSOT terminal.monitor.windows_backend".into());
    }
    let dimension = |key: &str| {
        config["terminal"]["monitor"][key]
            .as_u64()
            .filter(|n| *n > 0 && *n <= 32767)
            .ok_or_else(|| format!("Invalid SSOT terminal.monitor.{key}"))
    };
    let mut args = vec![
        "-w".into(),
        text(config, "/terminal/monitor/window_name")?,
        "--size".into(),
        format!("{},{}", dimension("cols")?, dimension("rows")?),
    ];
    match text(config, "/theme/launch_mode")?.as_str() {
        "default" => (),
        "focus" => args.push("--focus".into()),
        "maximized" => args.push("--maximized".into()),
        "fullscreen" => args.push("--fullscreen".into()),
        "focusFullscreen" => args.extend(["--fullscreen".into(), "--focus".into()]),
        _ => return Err("Invalid SSOT theme.launch_mode".into()),
    }
    let percent = |table: &Value, path: &str, key: &str| -> Result<u64, String> {
        table[key]
            .as_u64()
            .filter(|v| *v > 0 && *v < 100)
            .ok_or_else(|| format!("Invalid SSOT {path}.{key}"))
    };
    // ONE head share for every head/monitor split, shared with the Linux tmux
    // workspace; the monitor always takes the rest, in both orientations.
    let portrait_head = percent(
        &config["mcp"]["tmux"]["workspace"],
        "mcp.tmux.workspace",
        "head_percent",
    )?;
    let landscape_monitor = 100 - portrait_head;
    let worker_split = (percent(
        &config["terminal"]["monitor"],
        "terminal.monitor",
        "worker_split_percent",
    )? as f64
        / 100.0)
        .to_string();
    let profile = text(config, "/theme/terminal/profile_name")?;
    let scheme = text(config, "/theme/terminal/scheme_name")?;
    let title = text(config, "/terminal/monitor/title")?;
    let shell = argument(shell)?;
    let monitor = vec![argument(python)?, argument(script)?, "--pipeline".into()];
    let pane = |args: &mut Vec<String>, command: &[String], label: &str| {
        args.extend([
            "--profile".into(),
            profile.clone(),
            "--colorScheme".into(),
            scheme.clone(),
            "--title".into(),
            format!("{title}-{label}"),
            "--suppressApplicationTitle".into(),
            "--".into(),
        ]);
        args.extend_from_slice(command);
    };
    let fullscreen = matches!(
        config["theme"]["launch_mode"].as_str(),
        Some("fullscreen" | "focusFullscreen")
    );
    args.push("new-tab".into());
    if portrait && !fullscreen {
        pane(&mut args, &monitor, "monitor");
        args.extend([
            ";".into(),
            "split-pane".into(),
            "-H".into(),
            "-s".into(),
            (portrait_head as f64 / 100.0).to_string(),
        ]);
        pane(&mut args, std::slice::from_ref(&shell), "head");
    } else {
        pane(&mut args, std::slice::from_ref(&shell), "head");
        args.extend([
            ";".into(),
            "split-pane".into(),
            "-V".into(),
            "-s".into(),
            (landscape_monitor as f64 / 100.0).to_string(),
        ]);
        pane(&mut args, &monitor, "monitor");
    }
    if fullscreen {
        for (direction, label) in [("-H", "worker-1"), ("-V", "worker-2")] {
            args.extend([
                ";".into(),
                "split-pane".into(),
                direction.into(),
                "-s".into(),
                worker_split.clone(),
            ]);
            pane(&mut args, std::slice::from_ref(&shell), label);
        }
        args.extend([
            ";".into(),
            "move-focus".into(),
            "up".into(),
            ";".into(),
            "split-pane".into(),
            "-V".into(),
            "-s".into(),
            worker_split,
        ]);
        pane(&mut args, std::slice::from_ref(&shell), "worker-3");
    }
    args.extend([";".into(), "move-focus".into(), "first".into()]);
    Ok(args)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    fn fixture() -> Value {
        json!({"terminal":{"default_action":"ai","start_directory":"/","socket_root":"/tmp","scrollback_rows":9000,
            "windows":{"backend":"native-tmux","socket_name":"host","session_name":"MiOS-WIN","shell":"cmd.exe"},
            "monitor":{"windows_backend":"windows-terminal","window_name":"monitor","title":"Build","cols":80,"rows":20,
                "worker_split_percent":50}},
            "keybindings":{"mouse":true,"socket_name":"guest","terminal_session":"MiOS","actions":[{"id":"agents","command":"mios agents --watch","label":"Agents"},{"id":"ai","command":"mios ai","label":"AI"}]},
            "mcp":{"agents":{"observation":{"window_name":"MiOS Agents"}},"tmux":{"workspace":{"head_percent":38}}},
            "theme":{"launch_mode":"focus","terminal":{"profile_name":"MiOS-WIN","scheme_name":"MiOS"}}})
    }
    #[test]
    fn shared_policy_preserves_host_and_guest_namespaces_and_operator_values() {
        let mut config = fixture();
        assert_eq!(terminal_config(&config, "", true).unwrap()[6], "ai");
        assert_eq!(terminal_config(&config, "", false).unwrap()[4], "");
        config["keybindings"]["socket_name"] = json!("custom-guest");
        config["terminal"]["socket_root"] = json!("/run/user/1000");
        let policy = terminal_config(&config, "agents", false).unwrap();
        assert_eq!(policy[0], "custom-guest");
        assert_eq!(policy[8], "/run/user/1000");
        let host = host_tmux_args(&config, r"C:\Users\user\host.conf").unwrap();
        assert_eq!(host[1], "host");
        assert!(host.contains(&"cmd.exe".into()));
        assert!(!host.contains(&"custom-guest".into()));
        config["keybindings"]["mouse"] = json!(false);
        // Complete renderer policy is covered with real SSOT below.
    }
    #[test]
    fn landscape_portrait_and_fullscreen_keep_existing_panes() {
        let config = fixture();
        let landscape =
            render_monitor(&config, "python.exe", "monitor.py", "cmd.exe", false).unwrap();
        assert_eq!(landscape.iter().filter(|a| *a == "--title").count(), 2);
        assert!(landscape
            .windows(4)
            .any(|w| w == ["split-pane", "-V", "-s", "0.62"]));
        let portrait =
            render_monitor(&config, "python.exe", "monitor.py", "cmd.exe", true).unwrap();
        assert!(portrait
            .windows(4)
            .any(|w| w == ["split-pane", "-H", "-s", "0.38"]));
        let mut config = config;
        config["theme"]["launch_mode"] = json!("fullscreen");
        let full = render_monitor(&config, "python.exe", "monitor.py", "cmd.exe", false).unwrap();
        assert_eq!(full.iter().filter(|a| *a == "--title").count(), 5);
        assert_eq!(full.iter().filter(|a| *a == "monitor.py").count(), 1);
    }
    #[test]
    fn invalid_config_and_command_separators_fail_before_launch() {
        let mut config = fixture();
        assert!(terminal_config(&config, "unknown", false).is_err());
        config["keybindings"]["socket_name"] = json!("bad\nname");
        assert!(terminal_config(&config, "", true).is_err());
        let mut config = fixture();
        config["mcp"]["tmux"]["workspace"]["head_percent"] = json!(100);
        assert!(render_monitor(&config, "python.exe", "monitor.py", "cmd.exe", false).is_err());
        assert!(render_monitor(
            &fixture(),
            "python.exe",
            "monitor.py; new-tab cmd.exe",
            "cmd.exe",
            false
        )
        .is_err());
        config = fixture();
        config["terminal"]["windows"]["socket_name"] = json!("bad socket");
        assert!(host_tmux_args(&config, "host.conf").is_err());
    }
}
