// AI-hint: SSOT tmux theme renderer deriving active pane styles and status bar formatting from mios.toml [colors] and [theme.tmux] (ADR-0021 gen category).
// AI-doc: usr/share/doc/mios/manual/tools.md
// AI-related: usr/share/mios/mios.toml, usr/share/mios/tmux/mios-theme.tmux.conf, tools/sync-generated.sh, automation/98-drift-checks.sh

#![forbid(unsafe_code)]

use std::collections::BTreeMap;
use std::env;

#[derive(Debug, Clone)]
pub struct PromptSettings {
    pub powerline_left: String,
    pub powerline_right: String,
    pub powerline_right_soft: String,
}

#[derive(Debug, Clone)]
pub struct IconSettings {
    pub icon_os: String,
    pub icon_terminal: String,
    pub icon_time: String,
    pub icon_date: String,
    pub icon_user: String,
}

#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct TmuxThemeEngine {
    pub style: String,
    pub status_position: String,
    pub pane_background: String,
    pub status_background: String,
    pub glyph_mode: String,
    pub font: String,
    pub status_interval_s: i64,
    pub palette: BTreeMap<String, String>,
    pub prompt: PromptSettings,
    pub icons: IconSettings,
    pub is_transparent: bool,
    pub border_options: Vec<(String, String)>,
    pub workspace_startup: Option<String>,
    /// [theme.tmux].truecolor_terms: client TERM patterns granted 24-bit colour (terminal-features RGB, terminal-overrides Tc).
    pub truecolor_terms: Vec<String>,
    /// [theme.tmux].remote_truecolor_terms: TERM patterns a remote (SSH) client attaches as `remote_term` instead.
    pub remote_truecolor_terms: Vec<String>,
    pub remote_term: String,
    /// [theme.tmux].narrow_cols: clients narrower than this get the compact status line (0 = always the wide one).
    pub narrow_cols: i64,
    /// [theme.tmux].narrow_sysmon_cols: column budget of the compact sysmon segment (items past it drop from the end).
    pub narrow_sysmon_cols: i64,
    /// The status line runs /usr/libexec/mios/mios-sysmon-line: Linux only (render-host-tmux clears it).
    pub sysmon: bool,
}

/// The Linux-only status emitter; a host without it (render-host-tmux) renders no sysmon segment.
pub const SYSMON_LINE: &str = "/usr/libexec/mios/mios-sysmon-line";
/// First array index of the MiOS-owned terminal-features / terminal-overrides entries: indexed assignment keeps
/// re-sourcing the theme (every attach) idempotent instead of appending another copy each time.
const TERMINAL_CAPS_INDEX: usize = 90;

/// tmux matches terminal-features / terminal-overrides patterns against the client TERM with fnmatch(3);
/// the SSOT patterns use its `*` and `?` subset.
pub fn term_pattern_matches(pattern: &str, term: &str) -> bool {
    fn go(p: &[u8], t: &[u8]) -> bool {
        match p.split_first() {
            None => t.is_empty(),
            Some((b'*', rest)) => (0..=t.len()).any(|i| go(rest, &t[i..])),
            Some((b'?', rest)) => !t.is_empty() && go(rest, &t[1..]),
            Some((c, rest)) => t.first() == Some(c) && go(rest, &t[1..]),
        }
    }
    go(pattern.as_bytes(), term.as_bytes())
}

/// A TERM name (or pattern) safe inside a tmux option value and a shell assignment: no `,` `:` quotes or space.
fn valid_term(s: &str, pattern: bool) -> bool {
    !s.is_empty()
        && s.len() <= 64
        && s.bytes().all(|b| {
            b.is_ascii_alphanumeric() || b"._+-".contains(&b) || (pattern && b"*?".contains(&b))
        })
}

/// A compiled terminfo entry for `term` exists in one of `dirs` (`x/xterm-256color`, or the hashed `78/xterm-256color`).
pub fn terminfo_exists(term: &str, dirs: &[std::path::PathBuf]) -> bool {
    let Some(first) = term.chars().next() else {
        return false;
    };
    valid_term(term, false)
        && dirs.iter().any(|d| {
            d.join(first.to_string()).join(term).is_file()
                || d.join(format!("{:x}", first as u32)).join(term).is_file()
        })
}

pub fn is_remote_terminal() -> bool {
    [
        "SSH_CONNECTION",
        "SSH_CLIENT",
        "SSH_TTY",
        "MIOS_REMOTE_TERMINAL",
    ]
    .iter()
    .any(|k| env::var(k).map(|v| !v.trim().is_empty()).unwrap_or(false))
}

impl TmuxThemeEngine {
    pub fn from_toml(
        doc: &toml::Value,
        override_style: Option<&str>,
        override_position: Option<&str>,
    ) -> Result<Self, String> {
        let theme_tbl = doc
            .get("theme")
            .and_then(|v| v.as_table())
            .ok_or_else(|| "Missing [theme] section in mios.toml".to_string())?;

        let tmux_tbl = theme_tbl
            .get("tmux")
            .and_then(|v| v.as_table())
            .ok_or_else(|| "Missing [theme.tmux] section in mios.toml".to_string())?;

        let initial_style = override_style
            .map(|s| s.to_string())
            .or_else(|| {
                tmux_tbl
                    .get("style")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string())
            })
            .unwrap_or_else(|| "rounded".to_string());

        if initial_style != "rounded" && initial_style != "powerline" && initial_style != "minimal"
        {
            return Err("[theme.tmux].style must be rounded, powerline or minimal".to_string());
        }

        let status_position = override_position
            .map(|s| s.to_string())
            .or_else(|| {
                tmux_tbl
                    .get("status_position")
                    .and_then(|v| v.as_str())
                    .map(|s| s.to_string())
            })
            .unwrap_or_else(|| "bottom".to_string());

        if status_position != "top" && status_position != "bottom" {
            return Err("[theme.tmux].status_position must be top or bottom".to_string());
        }

        let pane_bg = tmux_tbl
            .get("pane_background")
            .and_then(|v| v.as_str())
            .unwrap_or("terminal")
            .to_string();

        if pane_bg != "terminal" && pane_bg != "theme" {
            return Err("[theme.tmux].pane_background must be terminal or theme".to_string());
        }

        let status_bg_setting = tmux_tbl
            .get("status_background")
            .and_then(|v| v.as_str())
            .unwrap_or("auto")
            .to_string();

        if status_bg_setting != "auto"
            && status_bg_setting != "terminal"
            && status_bg_setting != "theme"
        {
            return Err(
                "[theme.tmux].status_background must be auto, terminal or theme".to_string(),
            );
        }

        let remote = is_remote_terminal();
        let mode_key = if remote {
            "remote_glyph_mode"
        } else {
            "glyph_mode"
        };
        let mode = tmux_tbl
            .get(mode_key)
            .and_then(|v| v.as_str())
            .unwrap_or("auto")
            .to_string();

        if mode != "auto" && mode != "nerd" && mode != "ascii" {
            return Err("[theme.tmux].glyph_mode must be auto, nerd or ascii".to_string());
        }

        let font_family = theme_tbl
            .get("font")
            .and_then(|v| v.as_table())
            .and_then(|f| f.get("family"))
            .and_then(|v| v.as_str())
            .unwrap_or("GeistMono Nerd Font Mono")
            .to_string();

        let mut final_style = initial_style;
        if mode == "ascii"
            || (mode == "auto" && (remote || !font_family.to_lowercase().contains("nerd")))
        {
            final_style = "minimal".to_string();
        }

        let status_interval_s = tmux_tbl
            .get("status_interval_s")
            .and_then(|v| v.as_integer())
            .unwrap_or(2);

        if status_interval_s <= 0 {
            return Err("[theme.tmux].status_interval_s must be positive".to_string());
        }

        let acrylic = theme_tbl
            .get("acrylic")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        let opacity = theme_tbl
            .get("opacity")
            .and_then(|v| v.as_integer())
            .unwrap_or(100);
        let is_transparent = acrylic && opacity < 100;

        // Parse and validate colors
        let colors_tbl = doc
            .get("colors")
            .and_then(|v| v.as_table())
            .ok_or_else(|| "Missing [colors] section in mios.toml".to_string())?;

        let mut palette = BTreeMap::new();
        for (k, v) in colors_tbl {
            if let Some(s) = v.as_str() {
                if s.len() != 7
                    || !s.starts_with('#')
                    || !s.as_bytes()[1..].iter().all(u8::is_ascii_hexdigit)
                {
                    return Err(format!("[colors].{k}: expected #rrggbb"));
                }
                palette.insert(k.clone(), s.to_string());
            } else {
                return Err(format!("[colors].{k}: expected #rrggbb"));
            }
        }

        // Validate required color keys
        for required in &["bg", "fg", "accent", "cursor", "muted", "subtle", "success"] {
            if !palette.contains_key(*required) {
                return Err(format!(
                    "Missing required color [colors].{required} in mios.toml"
                ));
            }
        }

        // Parse [theme.prompt]
        let prompt_tbl = theme_tbl.get("prompt").and_then(|v| v.as_table());

        let powerline_left = prompt_tbl
            .and_then(|p| p.get("powerline_left"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        let powerline_right = prompt_tbl
            .and_then(|p| p.get("powerline_right"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        let powerline_right_soft = prompt_tbl
            .and_then(|p| p.get("powerline_right_soft"))
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_string();

        let prompt = PromptSettings {
            powerline_left,
            powerline_right,
            powerline_right_soft,
        };

        let icons = IconSettings {
            icon_os: tmux_tbl
                .get("icon_os")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            icon_terminal: tmux_tbl
                .get("icon_terminal")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            icon_time: tmux_tbl
                .get("icon_time")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            icon_date: tmux_tbl
                .get("icon_date")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
            icon_user: tmux_tbl
                .get("icon_user")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_string(),
        };

        let mut border_options = Vec::new();
        for (key, option, allowed) in [
            (
                "pane_border_lines",
                "pane-border-lines",
                &[
                    "single", "double", "heavy", "simple", "number", "spaces", "none",
                ][..],
            ),
            (
                "popup_border_lines",
                "popup-border-lines",
                &["single", "double", "heavy", "simple", "rounded", "none"][..],
            ),
            (
                "menu_border_lines",
                "menu-border-lines",
                &["single", "double", "heavy", "simple", "rounded", "none"][..],
            ),
        ] {
            if let Some(value) = tmux_tbl.get(key) {
                let value = value
                    .as_str()
                    .filter(|value| allowed.contains(value))
                    .ok_or_else(|| format!("invalid [theme.tmux].{key}"))?;
                border_options.push((option.to_string(), value.to_string()));
            }
        }

        let workspace_startup = if doc
            .get("mcp")
            .and_then(|v| v.get("tmux"))
            .and_then(|v| v.get("workspace"))
            .and_then(|v| v.get("global_sessions"))
            .and_then(|v| v.as_bool())
            == Some(true)
        {
            // The native relay hook (single-flight, idempotent); never an interpreter per tmux event.
            let relay = doc
                .get("mcp")
                .and_then(|v| v.get("agents"))
                .and_then(|v| v.get("binary"))
                .and_then(|v| v.as_str())
                .ok_or("Missing [mcp.agents].binary for the native workspace hook")?;
            if !relay.starts_with('/') || relay.contains(['\n', '\r', '\0']) {
                return Err("Unsafe [mcp.agents].binary for the workspace hook".into());
            }
            let quoted_relay = format!("'{}'", relay.replace('\'', "'\\''"));
            Some(format!("env TMUX='#{{socket_path}},#{{pid}},#{{session_id}}' TMUX_PANE='#{{pane_id}}' {quoted_relay} --workspace-hook ensure"))
        } else {
            None
        };

        let term_list = |key: &str, default: &[&str]| -> Result<Vec<String>, String> {
            match tmux_tbl.get(key) {
                None => Ok(default.iter().map(|s| s.to_string()).collect()),
                Some(v) => v
                    .as_array()
                    .ok_or_else(|| format!("[theme.tmux].{key} must be an array of TERM patterns"))?
                    .iter()
                    .map(|p| {
                        p.as_str()
                            .filter(|p| valid_term(p, true))
                            .map(str::to_string)
                            .ok_or_else(|| format!("[theme.tmux].{key}: invalid TERM pattern {p}"))
                    })
                    .collect(),
            }
        };
        let truecolor_terms = term_list("truecolor_terms", &["xterm*"])?;
        let remote_truecolor_terms = term_list("remote_truecolor_terms", &[])?;
        let remote_term = match tmux_tbl.get("remote_term") {
            None => "xterm-256color".to_string(),
            Some(v) => v
                .as_str()
                .filter(|s| valid_term(s, false))
                .ok_or("[theme.tmux].remote_term must be a TERM name")?
                .to_string(),
        };
        if !remote_truecolor_terms.is_empty()
            && !truecolor_terms
                .iter()
                .any(|p| term_pattern_matches(p, &remote_term))
        {
            return Err(
                "[theme.tmux].remote_term must match a [theme.tmux].truecolor_terms pattern".into(),
            );
        }
        let cols = |key: &str| -> Result<i64, String> {
            match tmux_tbl.get(key) {
                None => Ok(0),
                Some(v) => v
                    .as_integer()
                    .filter(|n| (0..=1000).contains(n))
                    .ok_or_else(|| format!("[theme.tmux].{key} must be an integer 0..1000")),
            }
        };
        let narrow_cols = cols("narrow_cols")?;
        let narrow_sysmon_cols = cols("narrow_sysmon_cols")?;

        Ok(Self {
            style: final_style,
            status_position,
            pane_background: pane_bg,
            status_background: status_bg_setting,
            glyph_mode: mode,
            font: font_family,
            status_interval_s,
            palette,
            prompt,
            icons,
            is_transparent,
            border_options,
            workspace_startup,
            truecolor_terms,
            remote_truecolor_terms,
            remote_term,
            narrow_cols,
            narrow_sysmon_cols,
            sysmon: true,
        })
    }

    /// The (TERM, COLORTERM) a client must attach with. Only a remote (SSH) client whose TERM matches
    /// [theme.tmux].remote_truecolor_terms (phone apps declare the Linux console) is re-declared as `remote_term`;
    /// a local console, an unlisted TERM, or a TERM already granted RGB stays exactly as it is.
    pub fn remote_client_env(&self, term: &str, remote: bool) -> Option<(String, String)> {
        if !remote
            || term == self.remote_term
            || self
                .truecolor_terms
                .iter()
                .any(|p| term_pattern_matches(p, term))
            || !self
                .remote_truecolor_terms
                .iter()
                .any(|p| term_pattern_matches(p, term))
        {
            return None;
        }
        Some((self.remote_term.clone(), "truecolor".to_string()))
    }

    pub fn generate_config(&self) -> Result<String, String> {
        let p = &self.palette;
        let bg = &p["bg"];
        let fg = &p["fg"];
        let pane_bg = if self.pane_background == "terminal" {
            "default"
        } else {
            bg
        };
        let accent = &p["accent"];
        let cursor = &p["cursor"];
        let muted = &p["muted"];
        let subtle = &p["subtle"];
        let success = &p["success"];

        let status_bg = match self.status_background.as_str() {
            "terminal" => "default",
            "theme" => bg.as_str(),
            _ => {
                if self.is_transparent {
                    "default"
                } else {
                    bg.as_str()
                }
            }
        };

        let mut lines = vec![
            "# AI-hint: tmux theme rendered by native MiOS Rust from mios.toml [colors]; tmux has no outer padding".to_string(),
            "# =====================================================================".to_string(),
            "# MiOS Canonical Tmux Theme".to_string(),
            format!("# Generated from mios.toml SSOT (Style: {})", self.style),
            format!("# Client font: {}; font size is controlled by the SSH/terminal client.", self.font),
            "# =====================================================================".to_string(),
            "".to_string(),
            "# Status Bar Placement & Refresh Interval".to_string(),
            "set -g status on".to_string(),
            format!("set -g status-interval {}", self.status_interval_s),
            format!("set -g status-position {}", self.status_position),
            format!("set -g status-style \"bg={},fg={}\"", status_bg, fg),
            format!("set -g window-style \"bg={},fg={}\"", pane_bg, fg),
            format!("set -g window-active-style \"bg={},fg={}\"", pane_bg, fg),
            "".to_string(),
            "# Window Status Alignment & Separation".to_string(),
            "set -g status-justify left".to_string(),
            "set -g window-status-separator \"\"".to_string(),
            "".to_string(),
            "# Terminal Capabilities & Extended Keys".to_string(),
            "set -g default-terminal \"tmux-256color\"".to_string(),
        ];
        // 24-bit colour for the SSOT client TERMs, at MiOS-owned array indexes (re-sourcing stays idempotent).
        for (i, term) in self.truecolor_terms.iter().enumerate() {
            lines.push(format!(
                "set -s terminal-features[{}] \"{term}:RGB\"",
                TERMINAL_CAPS_INDEX + i
            ));
        }
        for (i, term) in self.truecolor_terms.iter().enumerate() {
            lines.push(format!(
                "set -s terminal-overrides[{}] \"{term}:Tc\"",
                TERMINAL_CAPS_INDEX + i
            ));
        }
        lines.extend(vec![
            "".to_string(),
            "# Pane Borders".to_string(),
            format!("set -g pane-border-style \"fg={}\"", muted),
            format!("set -g pane-active-border-style \"fg={}\"", cursor),
            "".to_string(),
            "# Selection & Copy Mode".to_string(),
            format!("set -g mode-style \"bg={},fg={}\"", accent, fg),
            "".to_string(),
            "# Message & Command Prompt".to_string(),
            format!("set -g message-style \"bg={},fg={}\"", accent, fg),
            format!("set -g message-command-style \"bg={},fg={}\"", bg, cursor),
            "".to_string(),
        ]);

        // Map indexed TUI colors through SSOT rather than the SSH client's palette.
        for i in 0..16 {
            let prefix = format!("ansi_{i}_");
            if let Some((_, color)) = p.iter().find(|(key, _)| key.starts_with(&prefix)) {
                lines.push(format!("set -g pane-colours[{i}] \"{color}\""));
            }
        }

        for (option, value) in &self.border_options {
            lines.push(format!("set -g {option} {value}"));
        }
        lines.push("set -g window-size latest".to_string());

        if let Some(command) = &self.workspace_startup {
            let run = format!(
                "run-shell -b {}",
                serde_json::to_string(command).map_err(|e| e.to_string())?
            );
            let argument = serde_json::to_string(&run).map_err(|e| e.to_string())?;
            for hook in [
                "client-attached",
                "client-session-changed",
                "client-resized",
                "client-active",
            ] {
                lines.push(format!("set-hook -g {hook}[90] {argument}"));
            }
        }

        // Status line: a wide and a narrow (phone) variant per side. A client narrower than [theme.tmux].narrow_cols
        // (#{client_width}) gets the session pill, the window list, the compact sysmon segment ([desktop.topbar.sysmon]
        // mobile_items without watts, cut to narrow_sysmon_cols) and the time; wider clients keep the full line.
        let sysmon = |args: &str| -> String {
            if args.is_empty() {
                format!("#({SYSMON_LINE})")
            } else {
                format!("#({SYSMON_LINE} {args})")
            }
        };
        let compact = |ascii: bool| -> String {
            let budget = self.narrow_sysmon_cols;
            sysmon(&if ascii {
                format!("--compact {budget} --ascii")
            } else {
                format!("--compact {budget}")
            })
        };
        let header: &str;
        let (left_len, right_len): (u32, u32);
        let (left_wide, left_narrow, right_wide, right_narrow): (String, String, String, String);
        let window_formats: Vec<String>;
        if self.style == "powerline" {
            header = "# Powerline Segment Formatting";
            (left_len, right_len) = (40, 160);
            left_wide = format!(
                "#[fg={fg},bg={accent},bold] #S #[fg={accent},bg={status_bg},nobold]\u{e0b0} "
            );
            left_narrow = left_wide.clone();
            window_formats = vec![
                format!("set -g window-status-format \"#[fg={},bg={}] #I:#W \"", muted, status_bg),
                format!("set -g window-status-current-format \"#[fg={},bg={}]#[fg={},bg={},bold] #I:#W #[fg={},bg={},nobold]\"", status_bg, accent, fg, accent, accent, status_bg),
            ];
            let date_on = format!("#[fg={bg},bg={success},bold] %a %-d %b #[fg={accent},bg={success}]\u{e0b2}#[fg={fg},bg={accent},bold] %H:%M #[fg={cursor},bg={accent}]\u{e0b2}#[fg={bg},bg={cursor},bold] #H ");
            let time_on = format!("#[fg={bg},bg={success},bold] %H:%M ");
            if self.sysmon {
                right_wide = format!("#[fg={accent},bg={status_bg}]\u{e0b2}#[fg={fg},bg={accent}] {} #[fg={success},bg={accent}]\u{e0b2}{date_on}", sysmon(""));
                right_narrow = format!("#[fg={accent},bg={status_bg}]\u{e0b2}#[fg={fg},bg={accent}] {} #[fg={success},bg={accent}]\u{e0b2}{time_on}", compact(false));
            } else {
                right_wide = format!("#[fg={success},bg={status_bg}]\u{e0b2}{date_on}");
                right_narrow = format!("#[fg={success},bg={status_bg}]\u{e0b2}{time_on}");
            }
        } else if self.style == "rounded" {
            header = "# Rounded Glyph Formatting & Oh-My-Posh Graphics";
            (left_len, right_len) = (50, 160);
            left_wide = format!("#[fg={accent},bg={status_bg}]\u{e0b6}#[fg={fg},bg={accent},bold] \u{f17c} MiOS #[fg={accent},bg={success}]\u{e0b4}#[fg={bg},bg={success},bold] \u{e795} #S #[fg={success},bg={status_bg}]\u{e0b4} ");
            left_narrow = format!("#[fg={success},bg={status_bg}]\u{e0b6}#[fg={bg},bg={success},bold] \u{e795} #S #[fg={success},bg={status_bg}]\u{e0b4} ");
            window_formats = vec![
                format!("set -g window-status-format \"#[fg={},bg={}]  #I  #W  \"", muted, status_bg),
                format!("set -g window-status-current-format \"#[fg={},bg={}]#[fg={},bg={},bold] #I  #W #[fg={},bg={}]\"", cursor, status_bg, bg, cursor, cursor, status_bg),
            ];
            let date_on = format!("#[fg={bg},bg={success},bold] \u{f073} %a %-d %b #[fg={accent},bg={success}]\u{e0b6}#[fg={fg},bg={accent},bold] \u{f017} %H:%M #[fg={cursor},bg={accent}]\u{e0b6}#[fg={bg},bg={cursor},bold] \u{f007} #H #[fg={cursor},bg={status_bg}]\u{e0b4}");
            let time_on = format!("#[fg={bg},bg={success},bold] \u{f017} %H:%M #[fg={success},bg={status_bg}]\u{e0b4}");
            if self.sysmon {
                right_wide = format!("#[fg={accent},bg={status_bg}]\u{e0b6}#[fg={fg},bg={accent}] {} #[fg={success},bg={accent}]\u{e0b6}{date_on}", sysmon(""));
                right_narrow = format!("#[fg={accent},bg={status_bg}]\u{e0b6}#[fg={fg},bg={accent}] {} #[fg={success},bg={accent}]\u{e0b6}{time_on}", compact(false));
            } else {
                right_wide = format!("#[fg={success},bg={status_bg}]\u{e0b6}{date_on}");
                right_narrow = format!("#[fg={success},bg={status_bg}]\u{e0b6}{time_on}");
            }
        } else {
            // minimal / plain
            header = "# Minimal Status Line Formatting";
            (left_len, right_len) = (30, 120);
            left_wide = format!("#[fg={accent},bold][#S] ");
            left_narrow = left_wide.clone();
            window_formats = vec![
                format!("set -g window-status-format \"#[fg={}]#I:#W\"", muted),
                format!("set -g window-status-current-format \"#[fg={cursor},bold][#I:#W]\""),
            ];
            if self.sysmon {
                right_wide = format!(
                    "#[fg={subtle}]{}  %a %-d %b %H:%M #[fg={fg},bold]#H",
                    sysmon("--ascii")
                );
                right_narrow = format!("#[fg={subtle}]{} #[fg={fg},bold]%H:%M", compact(true));
            } else {
                right_wide = format!("#[fg={subtle}]%a %-d %b %H:%M #[fg={fg},bold]#H");
                right_narrow = format!("#[fg={fg},bold]%H:%M");
            }
        }
        // `#{T:@option}` expands the stored format with strftime(3), as status-left/right themselves are expanded.
        let narrow = format!("#{{e|<:#{{client_width}},{}}}", self.narrow_cols);
        let side = |name: &str, wide: &str, compact: &str| -> Vec<String> {
            if self.narrow_cols > 0 {
                vec![
                    format!("set -g @mios-{name}-wide \"{wide}\""),
                    format!("set -g @mios-{name}-narrow \"{compact}\""),
                    format!("set -g {name} \"#{{?{narrow},#{{T:@mios-{name}-narrow}},#{{T:@mios-{name}-wide}}}}\""),
                ]
            } else {
                vec![format!("set -g {name} \"{wide}\"")]
            }
        };
        lines.push(header.to_string());
        lines.push(format!("set -g status-left-length {left_len}"));
        lines.extend(side("status-left", &left_wide, &left_narrow));
        lines.extend(window_formats);
        lines.push(format!("set -g status-right-length {right_len}"));
        lines.extend(side("status-right", &right_wide, &right_narrow));

        let substitutions: Vec<(char, &str)> = vec![
            ('', &self.prompt.powerline_left),
            ('', &self.prompt.powerline_right),
            ('', &self.prompt.powerline_right_soft),
            ('', &self.prompt.powerline_right),
            ('', &self.prompt.powerline_left),
            ('', &self.icons.icon_os),
            ('', &self.icons.icon_terminal),
            ('', &self.icons.icon_time),
            ('', &self.icons.icon_date),
            ('', &self.icons.icon_user),
        ];

        for (_, val) in &substitutions {
            if val
                .chars()
                .any(|c| c == '\n' || c == '\r' || c == '\0' || c == '"' || c == '\\')
            {
                return Err("[theme.tmux]/[theme.prompt] unsafe tmux glyph".to_string());
            }
        }

        let mut raw = lines.join("\n");
        raw.push('\n');

        let mut translated = String::with_capacity(raw.len());
        for ch in raw.chars() {
            if let Some((_, repl)) = substitutions.iter().find(|(k, _)| *k == ch) {
                translated.push_str(repl);
            } else {
                translated.push(ch);
            }
        }

        Ok(translated)
    }
}

#[cfg(test)]
mod border_policy_tests {
    use super::*;

    #[test]
    fn native_tmux_border_projection_rejects_unsupported_pane_rounding() {
        let mut doc: toml::Value = include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap();
        let config = TmuxThemeEngine::from_toml(&doc, None, None)
            .unwrap()
            .generate_config()
            .unwrap();
        assert!(config.contains("set -g pane-border-lines single"));
        assert!(config.contains("set -g popup-border-lines rounded"));
        assert!(config.contains("set -g menu-border-lines rounded"));
        doc["theme"]["tmux"]["pane_border_lines"] = toml::Value::String("rounded".into());
        assert!(TmuxThemeEngine::from_toml(&doc, None, None)
            .unwrap_err()
            .contains("pane_border_lines"));
    }
}

#[cfg(test)]
mod remote_status_tests {
    use super::*;

    fn ssot() -> toml::Value {
        include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap()
    }
    fn with(mut doc: toml::Value, edits: &[(&str, toml::Value)]) -> toml::Value {
        for (k, v) in edits {
            doc["theme"]["tmux"]
                .as_table_mut()
                .unwrap()
                .insert(k.to_string(), v.clone());
        }
        doc
    }
    fn strs(v: &[&str]) -> toml::Value {
        toml::Value::Array(
            v.iter()
                .map(|s| toml::Value::String(s.to_string()))
                .collect(),
        )
    }
    fn render(doc: &toml::Value, style: &str, sysmon: bool) -> String {
        let mut engine = TmuxThemeEngine::from_toml(doc, Some(style), None).unwrap();
        engine.style = style.to_string();
        engine.sysmon = sysmon;
        engine.generate_config().unwrap()
    }
    /// The quoted value of `set -g <name> "..."` in a rendered config.
    fn value(config: &str, name: &str) -> String {
        let prefix = format!("set -g {name} \"");
        let line = config
            .lines()
            .find(|l| l.starts_with(&prefix))
            .unwrap_or_else(|| panic!("{name} missing"));
        line[prefix.len()..line.len() - 1].to_string()
    }
    fn narrow_doc() -> toml::Value {
        with(
            ssot(),
            &[
                ("narrow_cols", toml::Value::Integer(100)),
                ("narrow_sysmon_cols", toml::Value::Integer(24)),
            ],
        )
    }
    fn legacy_doc() -> toml::Value {
        with(ssot(), &[("narrow_cols", toml::Value::Integer(0))])
    }

    #[test]
    fn wide_status_is_byte_identical_to_the_single_line_status() {
        for style in ["rounded", "powerline", "minimal"] {
            let legacy = render(&legacy_doc(), style, true);
            let responsive = render(&narrow_doc(), style, true);
            for side in ["status-left", "status-right"] {
                assert_eq!(
                    value(&legacy, side),
                    value(&responsive, &format!("@mios-{side}-wide")),
                    "{style} {side}"
                );
                assert_eq!(
                    value(&responsive, side),
                    format!("#{{?#{{e|<:#{{client_width}},100}},#{{T:@mios-{side}-narrow}},#{{T:@mios-{side}-wide}}}}")
                );
            }
            for fmt in ["window-status-format", "window-status-current-format"] {
                assert_eq!(
                    value(&legacy, fmt),
                    value(&responsive, fmt),
                    "{style} {fmt}"
                );
            }
        }
    }

    #[test]
    fn wide_rounded_status_matches_the_pre_responsive_projection() {
        // The exact format the theme rendered before the narrow variant existed (placeholders before substitution).
        let doc = narrow_doc();
        let mut engine = TmuxThemeEngine::from_toml(&doc, Some("rounded"), None).unwrap();
        engine.style = "rounded".into();
        let p = engine.palette.clone();
        let (bg, fg, accent, cursor, success) = (
            &p["bg"],
            &p["fg"],
            &p["accent"],
            &p["cursor"],
            &p["success"],
        );
        let status_bg = match engine.status_background.as_str() {
            "terminal" => "default",
            "theme" => bg.as_str(),
            _ => {
                if engine.is_transparent {
                    "default"
                } else {
                    bg.as_str()
                }
            }
        };
        engine.prompt = PromptSettings {
            powerline_left: "\u{e0b6}".into(),
            powerline_right: "\u{e0b4}".into(),
            powerline_right_soft: "\u{e0b1}".into(),
        };
        engine.icons = IconSettings {
            icon_os: "\u{f17c}".into(),
            icon_terminal: "\u{e795}".into(),
            icon_time: "\u{f017}".into(),
            icon_date: "\u{f073}".into(),
            icon_user: "\u{f007}".into(),
        };
        let config = engine.generate_config().unwrap();
        assert_eq!(
            value(&config, "@mios-status-left-wide"),
            format!("#[fg={},bg={}]\u{e0b6}#[fg={},bg={},bold] \u{f17c} MiOS #[fg={},bg={}]\u{e0b4}#[fg={},bg={},bold] \u{e795} #S #[fg={},bg={}]\u{e0b4} ", accent, status_bg, fg, accent, accent, success, bg, success, success, status_bg)
        );
        assert_eq!(
            value(&config, "@mios-status-right-wide"),
            format!("#[fg={},bg={}]\u{e0b6}#[fg={},bg={}] #(/usr/libexec/mios/mios-sysmon-line) #[fg={},bg={}]\u{e0b6}#[fg={},bg={},bold] \u{f073} %a %-d %b #[fg={},bg={}]\u{e0b6}#[fg={},bg={},bold] \u{f017} %H:%M #[fg={},bg={}]\u{e0b6}#[fg={},bg={},bold] \u{f007} #H #[fg={},bg={}]\u{e0b4}", accent, status_bg, fg, accent, success, accent, bg, success, accent, success, fg, accent, cursor, accent, bg, cursor, cursor, status_bg)
        );
    }

    #[test]
    fn narrow_status_keeps_session_windows_compact_sysmon_and_time() {
        for style in ["rounded", "powerline", "minimal"] {
            let config = render(&narrow_doc(), style, true);
            let left = value(&config, "@mios-status-left-narrow");
            let right = value(&config, "@mios-status-right-narrow");
            assert!(
                left.contains("#S") && !left.contains("MiOS"),
                "{style}: {left}"
            );
            let compact = if style == "minimal" {
                "#(/usr/libexec/mios/mios-sysmon-line --compact 24 --ascii)"
            } else {
                "#(/usr/libexec/mios/mios-sysmon-line --compact 24)"
            };
            assert!(right.contains(compact), "{style}: {right}");
            assert!(right.contains("%H:%M"), "{style}: {right}");
            for dropped in ["%a", "%-d", "%b", "#H"] {
                assert!(!right.contains(dropped), "{style} keeps {dropped}: {right}");
            }
            // the window list is not conditional: narrow clients keep it
            assert!(config.contains("set -g window-status-format \""));
            assert!(config.contains("set -g window-status-current-format \""));
        }
    }

    #[test]
    fn truecolor_terms_render_indexed_rgb_and_tc_entries() {
        let doc = with(
            ssot(),
            &[
                ("truecolor_terms", strs(&["xterm*", "alacritty*"])),
                ("remote_truecolor_terms", strs(&["linux"])),
            ],
        );
        let config = render(&doc, "rounded", true);
        for line in [
            "set -s terminal-features[90] \"xterm*:RGB\"",
            "set -s terminal-features[91] \"alacritty*:RGB\"",
            "set -s terminal-overrides[90] \"xterm*:Tc\"",
            "set -s terminal-overrides[91] \"alacritty*:Tc\"",
        ] {
            assert!(config.lines().any(|l| l == line), "{line}");
        }
        // indexed, never appended: sourcing on every attach must not grow the arrays
        assert!(!config.contains("set -as terminal-"));
        // the Linux console is never granted RGB globally: a local VT keeps its 8 colours
        assert!(!config.contains("\"linux"));
    }

    #[test]
    fn unsafe_or_uncovered_terminal_settings_are_rejected() {
        for (key, v) in [
            ("truecolor_terms", strs(&["xterm*,linux:RGB"])),
            ("truecolor_terms", strs(&["xterm\"*"])),
            ("remote_truecolor_terms", strs(&["linux vt100"])),
            (
                "remote_term",
                toml::Value::String("xterm-256color:RGB".into()),
            ),
            ("narrow_cols", toml::Value::Integer(-1)),
            ("narrow_sysmon_cols", toml::Value::String("24".into())),
        ] {
            let doc = with(ssot(), &[(key, v.clone())]);
            assert!(
                TmuxThemeEngine::from_toml(&doc, None, None).is_err(),
                "{key} = {v}"
            );
        }
        // remote_term must itself be granted RGB, or the upgrade would not reach the palette
        let doc = with(
            ssot(),
            &[
                ("truecolor_terms", strs(&["alacritty*"])),
                ("remote_truecolor_terms", strs(&["linux"])),
                ("remote_term", toml::Value::String("xterm-256color".into())),
            ],
        );
        assert!(TmuxThemeEngine::from_toml(&doc, None, None)
            .unwrap_err()
            .contains("remote_term"));
    }

    #[test]
    fn remote_client_env_upgrades_only_listed_ssh_terms() {
        let doc = with(
            ssot(),
            &[
                ("truecolor_terms", strs(&["xterm*", "alacritty*"])),
                ("remote_truecolor_terms", strs(&["linux", "vt*"])),
                ("remote_term", toml::Value::String("xterm-256color".into())),
            ],
        );
        let engine = TmuxThemeEngine::from_toml(&doc, None, None).unwrap();
        let up = Some(("xterm-256color".to_string(), "truecolor".to_string()));
        assert_eq!(engine.remote_client_env("linux", true), up);
        assert_eq!(engine.remote_client_env("vt220", true), up);
        // a real local Linux VT console (no SSH) is never touched
        assert_eq!(engine.remote_client_env("linux", false), None);
        assert_eq!(engine.remote_client_env("vt220", false), None);
        // an unknown or unlisted TERM is never upgraded, even over SSH
        assert_eq!(engine.remote_client_env("dumb", true), None);
        assert_eq!(engine.remote_client_env("screen", true), None);
        assert_eq!(engine.remote_client_env("linux-16color", true), None);
        assert_eq!(engine.remote_client_env("", true), None);
        // a TERM already granted RGB keeps its own terminfo
        assert_eq!(engine.remote_client_env("xterm-256color", true), None);
        assert_eq!(engine.remote_client_env("alacritty", true), None);
        // no list, no upgrade
        let doc = with(ssot(), &[("remote_truecolor_terms", strs(&[]))]);
        let engine = TmuxThemeEngine::from_toml(&doc, None, None).unwrap();
        assert_eq!(engine.remote_client_env("linux", true), None);
    }

    #[test]
    fn host_guard_drops_the_linux_only_sysmon_path() {
        for style in ["rounded", "powerline", "minimal"] {
            for doc in [narrow_doc(), legacy_doc()] {
                let config = render(&doc, style, false);
                assert!(!config.contains("mios-sysmon-line"), "{style}");
                assert!(!config.contains("#("), "{style}: no shell job on the host");
                assert!(config.contains("%H:%M"), "{style}");
            }
            let config = render(&narrow_doc(), style, false);
            assert!(value(&config, "@mios-status-right-wide").contains("%a %-d %b"));
            assert!(!value(&config, "@mios-status-right-narrow").contains("%a"));
        }
    }

    #[test]
    fn term_patterns_follow_tmux_fnmatch_subset() {
        assert!(term_pattern_matches("xterm*", "xterm-256color"));
        assert!(term_pattern_matches("xterm*", "xterm"));
        assert!(term_pattern_matches("*kitty*", "xterm-kitty"));
        assert!(term_pattern_matches("vt?20", "vt220"));
        assert!(term_pattern_matches("linux", "linux"));
        assert!(!term_pattern_matches("linux", "linux-16color"));
        assert!(!term_pattern_matches("vt*", "xterm"));
        assert!(!term_pattern_matches("", "linux"));
    }

    #[test]
    fn terminfo_lookup_needs_a_compiled_entry() {
        let dir = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(dir.path().join("x")).unwrap();
        std::fs::write(dir.path().join("x/xterm-256color"), b"\x1a\x01").unwrap();
        std::fs::create_dir_all(dir.path().join("66")).unwrap();
        std::fs::write(dir.path().join("66/foot"), b"\x1a\x01").unwrap();
        let dirs = vec![dir.path().to_path_buf()];
        assert!(terminfo_exists("xterm-256color", &dirs));
        assert!(terminfo_exists("foot", &dirs));
        assert!(!terminfo_exists("xterm-kitty", &dirs));
        assert!(!terminfo_exists("../x/xterm-256color", &dirs));
        assert!(!terminfo_exists("", &dirs));
    }
}
