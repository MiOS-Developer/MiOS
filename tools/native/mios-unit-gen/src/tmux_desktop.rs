// AI-hint: Project the opt-in native TTY desktop dock, painted title controls, and guarded mouse callbacks from SSOT.
// AI-related: /usr/share/mios/templates/rust, usr/share/mios/mios.toml [keybindings.tmux.desktop], tools/native/mios-gen/src/tmux_desktop.rs, src/mios-rs/miosd/src/desktop.rs
// AI-functions: render
//! Appended after the ordinary tmux-os projection. These are format definitions
//! and conditional bindings, never a global desktop layout. The desktop owner
//! applies the formats only to its own session/window and marks that scope
//! `@mios-desktop-enabled`; ordinary terminals and managed agents keep theirs.

use crate::tmux_os::{plain_text, quote};
use crate::UnitGenError;
use toml::Value;

const PREFIX: &str = "keybindings.tmux.desktop";
const DESKTOP: &str = "#{@mios-desktop-enabled}";
// Use the existing expanded managed-pane SSOT projection, not an incomplete
// parallel list of relay ownership flags. The canvas is a persistent anchor.
const MUTABLE: &str =
    "#{?#{@mios-desktop-enabled},#{?#{E:@mios-managed-pane},0,#{?#{@mios-desktop-canvas},0,1}},0}";
const DOCK_ITEM: &str = "#{m/r:^(apps|p:%[0-9]+)$,#{mouse_status_range}}";
const TOPBAR: &str = "#{?#{@mios-desktop-enabled},#{?#{E:@mios-managed-pane},0,#{&&:#{@mios-desktop-canvas},#{==:#{mouse_y},0}}},0}";

fn error(message: impl Into<String>) -> UnitGenError {
    UnitGenError::GoldenMaster(message.into())
}

fn at<'a>(doc: &'a Value, path: &str) -> Option<&'a Value> {
    path.split('.').try_fold(doc, |value, key| value.get(key))
}

fn required<'a>(table: &'a Value, key: &str) -> Result<&'a Value, UnitGenError> {
    table
        .get(key)
        .ok_or_else(|| error(format!("missing [{PREFIX}].{key}")))
}

fn boolean(table: &Value, key: &str) -> Result<bool, UnitGenError> {
    required(table, key)?
        .as_bool()
        .ok_or_else(|| error(format!("[{PREFIX}].{key} must be boolean")))
}

fn number(table: &Value, key: &str, min: i64, max: i64) -> Result<i64, UnitGenError> {
    required(table, key)?
        .as_integer()
        .filter(|n| (min..=max).contains(n))
        .ok_or_else(|| error(format!("[{PREFIX}].{key} must be in {min}..={max}")))
}

fn text(table: &Value, key: &str) -> Result<String, UnitGenError> {
    required(table, key)?
        .as_str()
        .filter(|s| plain_text(s))
        .map(str::to_owned)
        .ok_or_else(|| error(format!("[{PREFIX}].{key} must be safe printable text")))
}

fn label(table: &Value, key: &str) -> Result<String, UnitGenError> {
    text(required(table, "labels")?, key)
}

fn callback(verb: &str, mouse_pane: bool, extra: &str) -> String {
    let target = if mouse_pane { " -t =" } else { "" };
    let pane = if mouse_pane {
        " --pane #{q:mouse_pane} --expected-pid #{q:pane_pid} --expected-window #{q:window_id}"
    } else {
        ""
    };
    // All interpolation is a witnessed tmux format with shell quoting. No pane
    // title, command, app label, or most-recent-client lookup becomes shell code.
    format!(
        "run-shell -b{target} {}",
        quote(&format!(
            "TMUX_PANE=#{{pane_id}} /usr/bin/mios-gen tmux-os desktop {verb} --socket #{{q:socket_path}} --client #{{q:client_name}}{pane}{extra}"
        ))
    )
}

fn bind(out: &mut String, key: &str, guard: &str, action: &str, fallback: &str, pane: bool) {
    let target = if pane { " -t =" } else { "" };
    out.push_str(&format!(
        "bind-key -T root {key} if-shell -F{target} {} {} {}\n",
        quote(guard),
        quote(action),
        quote(fallback),
    ));
}

/// Desktop format definitions and scoped callbacks. A missing or explicitly
/// disabled desktop table is a no-op, allowing an ordinary tmux-os install.
pub fn render(doc: &Value) -> Result<String, UnitGenError> {
    let Some(table) = at(doc, PREFIX) else {
        return Ok(String::new());
    };
    if !boolean(table, "enabled")? {
        return Ok(String::new());
    }
    let dock = boolean(table, "dock")?;
    let titles = boolean(table, "pane_titles")?;
    let dock_rows = number(table, "dock_rows", 2, 2)?;
    let min_columns = number(table, "min_columns", 1, 4096)?;
    let min_rows = number(table, "min_rows", 1, 4096)?;
    let snap_margin = number(table, "snap_margin", 0, 64)?;
    let refresh = number(table, "dock_refresh_seconds", 1, 3600)?;
    let _resize_debounce = number(table, "resize_debounce_ms", 1, 1000)?;
    let binary = text(table, "binary")?;
    if !binary.starts_with('/')
        || binary.split('/').any(|part| part == ".." || part == ".")
        || !binary
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"/._-".contains(&b))
    {
        return Err(error(format!(
            "[{PREFIX}].binary must be an absolute executable path"
        )));
    }
    let socket = text(table, "socket_name")?;
    if !socket
        .bytes()
        .all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
    {
        return Err(error(format!(
            "[{PREFIX}].socket_name must be a socket basename"
        )));
    }
    // Runtime key projection deliberately forwards only the popup switch and
    // resolved menu width. Geometry consumers resolve the complete policy;
    // this format-only projection must also accept that minimal payload.
    mios_resolver::popup::PopupPolicy::enabled_from_value(doc).map_err(error)?;

    let mut out = String::from("\n# MiOS native TTY desktop (opt-in session/window scope)\n");
    for (name, value) in [
        ("policy-enabled", "1".to_owned()),
        ("dock", u8::from(dock).to_string()),
        ("pane-titles", u8::from(titles).to_string()),
        ("dock-rows", dock_rows.to_string()),
        ("min-columns", min_columns.to_string()),
        ("min-rows", min_rows.to_string()),
        ("snap-margin", snap_margin.to_string()),
        ("dock-refresh", refresh.to_string()),
        ("binary", binary),
        ("socket-name", socket),
    ] {
        out.push_str(&format!("set -g @mios-desktop-{name} {}\n", quote(&value)));
    }

    let dock_format = if dock {
        let _apps = label(table, "apps")?;
        // Stable socket/client argv keeps the tmux #() job cache independent of
        // viewport geometry. Resizing must not spawn a new process per frame.
        // The engine emits the Apps range first and sanitizes all app titles;
        // tmux clips overflow while the Apps control remains first and visible.
        "#(/usr/bin/mios-gen tmux-os desktop dock-state --socket #{q:socket_path} --client #{q:client_name} --format tmux)".to_owned()
    } else {
        String::new()
    };
    out.push_str(&format!(
        "set -g @mios-desktop-dock-format {}\n",
        quote(&dock_format)
    ));

    // The fixed control cells have a runtime coordinate contract with the
    // title-click handler. The engine verifies the source PID/window again
    // before acting; managed/canvas titles never advertise window controls.
    let border_format = if titles {
        "#{?#{E:@mios-managed-pane},#{pane_id},#{?#{@mios-desktop-canvas},,#{?#{>=:#{pane_width},12},#[align=left][x][_][+] #{pane_current_command},#{pane_id}}}}"
    } else {
        ""
    };
    out.push_str(&format!(
        "set -g @mios-desktop-border-format {}\n",
        quote(border_format)
    ));

    if at(doc, "keybindings.mouse").and_then(Value::as_bool) != Some(true) {
        return Ok(out);
    }
    // mouse_y is pane-relative and available for Pane events. Only the quiet
    // canvas's painted first row is a global menu button. Every other pane
    // retains native focus and mouse forwarding, including application modes.
    bind(
        &mut out,
        "MouseDown1Pane",
        TOPBAR,
        &callback("dock-click", true, " --item apps"),
        "select-pane -t = ; send-keys -M",
        true,
    );
    if dock {
        let guard = format!("#{{&&:{DESKTOP},{DOCK_ITEM}}}");
        bind(
            &mut out,
            "MouseDown1Status",
            &guard,
            &callback("dock-click", false, " --item #{q:mouse_status_range}"),
            "select-window -t =",
            false,
        );
    }
    if titles {
        bind(
            &mut out,
            "MouseUp1Border",
            MUTABLE,
            &callback(
                "title-click",
                true,
                " --x #{mouse_window_x} --y #{mouse_window_y}",
            ),
            "",
            true,
        );
        bind(
            &mut out,
            "MouseUp3Border",
            MUTABLE,
            &callback("context", true, ""),
            "",
            true,
        );
    }
    // Native mouse resizing/moving of floats is a separate safe path. Ordinary
    // resize-pane -x/-y modifies the tiled tree in stock 3.7c and is never used.
    // No external callback/process is dispatched for mouse-motion events.
    bind(
        &mut out,
        "MouseDown1Border",
        MUTABLE,
        "select-pane -t =",
        "select-pane -t =",
        true,
    );
    bind(
        &mut out,
        "MouseDrag1Border",
        &format!("#{{?{DESKTOP},{MUTABLE},1}}"),
        "resize-pane -M",
        "",
        true,
    );
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn doc() -> Value {
        r#"
[keybindings]
mouse = true
[keybindings.tmux.desktop]
enabled = true
dock = true
pane_titles = true
dock_rows = 2
min_columns = 34
min_rows = 10
snap_margin = 2
dock_refresh_seconds = 1
resize_debounce_ms = 40
binary = "/usr/libexec/mios/mios-tmux"
socket_name = "mios-desktop"
[keybindings.tmux.desktop.labels]
apps = "Apps"
windows = "Windows"
"#
        .parse()
        .unwrap()
    }

    #[test]
    fn missing_or_disabled_desktop_leaves_ordinary_tmux_projection_untouched() {
        assert!(render(&Value::Table(Default::default()))
            .unwrap()
            .is_empty());
        let disabled: Value = "[keybindings.tmux.desktop]\nenabled=false".parse().unwrap();
        assert!(render(&disabled).unwrap().is_empty());
    }

    #[test]
    fn dock_ranges_have_explicit_socket_client_and_no_resize_dependent_job_key() {
        let out = render(&doc()).unwrap();
        let dock_guard = format!("#{{&&:{DESKTOP},{DOCK_ITEM}}}");
        assert!(out.contains(&quote(&dock_guard)));
        assert!(out.contains("--item #{q:mouse_status_range}"));
        assert!(out.contains("--socket #{q:socket_path} --client #{q:client_name}"));
        let job = out
            .lines()
            .find(|line| line.contains("#(/usr/bin/mios-gen"))
            .unwrap();
        assert!(job.contains("dock-state"));
        assert!(!job.contains("client_width") && !job.contains("client_height"));
        assert!(out.contains("select-window -t ="));
        assert!(!out.contains("set -g status") && !out.contains("set -g pane-border"));
        assert!(!out.contains("set -g @mios-desktop-enabled"));
    }

    #[test]
    fn title_controls_and_border_callbacks_share_desktop_and_ownership_guards() {
        let out = render(&doc()).unwrap();
        assert!(out.contains("#[align=left][x][_][+] #{pane_current_command}"));
        assert!(!out.contains("#{pane_title}"));
        assert!(out.contains("#{E:@mios-managed-pane}"));
        assert!(out.contains("#{@mios-desktop-canvas}"));
        assert!(out.contains("title-click"));
        assert!(out.contains("--pane #{q:mouse_pane}"));
        assert!(out.contains("--expected-pid #{q:pane_pid} --expected-window #{q:window_id}"));
        let drag = out
            .lines()
            .find(|line| line.contains("MouseDrag1Border"))
            .unwrap();
        assert!(drag.contains("resize-pane -M"));
        assert!(!drag.contains("run-shell"));
        let topbar = out
            .lines()
            .find(|line| line.contains("MouseDown1Pane"))
            .unwrap();
        assert!(topbar.contains(TOPBAR));
        assert!(topbar.contains("send-keys -M"));
        assert!(!out.contains("MouseUp1Pane"));
        assert!(!out.contains("MouseDown3Pane") && !out.contains("MouseUp3Pane"));
        assert!(!out.contains("resize-pane -x") && !out.contains("resize-pane -y"));
        assert!(!out.contains("select-pane -M"));
    }

    #[test]
    fn display_toggles_and_global_mouse_switch_do_not_emit_dead_bindings() {
        let mut value = doc();
        value["keybindings"]["tmux"]["desktop"]["dock"] = Value::Boolean(false);
        value["keybindings"]["tmux"]["desktop"]["pane_titles"] = Value::Boolean(false);
        let out = render(&value).unwrap();
        assert!(!out.contains("dock-state") && !out.contains("title-click"));
        value["keybindings"]["mouse"] = Value::Boolean(false);
        assert!(!render(&value).unwrap().contains("bind-key"));
    }

    #[test]
    fn runtime_key_payload_needs_no_duplicate_popup_geometry() {
        let mut value = doc();
        let popups: Value = "[popups]\nenabled=true\n_menu_columns=60".parse().unwrap();
        value
            .as_table_mut()
            .unwrap()
            .insert("terminal".into(), popups);
        assert!(render(&value)
            .unwrap()
            .contains("@mios-desktop-dock-format"));
    }

    #[test]
    fn shell_format_and_numeric_injection_fail_before_projection() {
        for (key, bad) in [
            ("binary", "/usr/bin/$(touch /tmp/bad)"),
            ("binary", "/usr/../bin/tmux"),
            ("socket_name", "mios;bad"),
        ] {
            let mut value = doc();
            value["keybindings"]["tmux"]["desktop"][key] = Value::String(bad.into());
            assert!(render(&value).is_err());
        }
        let mut value = doc();
        value["keybindings"]["tmux"]["desktop"]["labels"]["apps"] =
            Value::String("#{pane_id}".into());
        assert!(render(&value).is_err());
        for key in ["dock_rows", "min_columns", "min_rows", "resize_debounce_ms"] {
            let mut value = doc();
            value["keybindings"]["tmux"]["desktop"][key] = Value::Integer(0);
            assert!(render(&value).is_err());
        }
    }
}
