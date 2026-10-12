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
        })
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
            "set -as terminal-features \",xterm*:RGB\"".to_string(),
            "set -as terminal-overrides \",xterm*:Tc\"".to_string(),
            "".to_string(),
            "# Pane Borders".to_string(),
            format!("set -g pane-border-style \"fg={}\"", muted),
            format!("set -g pane-active-border-style \"fg={}\"", cursor),
            "set -g pane-border-lines heavy".to_string(),
            "".to_string(),
            "# Selection & Copy Mode".to_string(),
            format!("set -g mode-style \"bg={},fg={}\"", accent, fg),
            "".to_string(),
            "# Message & Command Prompt".to_string(),
            format!("set -g message-style \"bg={},fg={}\"", accent, fg),
            format!("set -g message-command-style \"bg={},fg={}\"", bg, cursor),
            "".to_string(),
        ];

        if self.style == "powerline" {
            lines.extend(vec![
                "# Powerline Segment Formatting".to_string(),
                "set -g status-left-length 40".to_string(),
                format!("set -g status-left \"#[fg={},bg={},bold] #S #[fg={},bg={},nobold] \"", fg, accent, accent, status_bg),
                format!("set -g window-status-format \"#[fg={},bg={}] #I:#W \"", muted, status_bg),
                format!("set -g window-status-current-format \"#[fg={},bg={}]#[fg={},bg={},bold] #I:#W #[fg={},bg={},nobold]\"", status_bg, accent, fg, accent, accent, status_bg),
                "set -g status-right-length 80".to_string(),
                format!("set -g status-right \"#[fg={},bg={}]#[fg={},bg={}] %Y-%m-%d %H:%M #[fg={},bg={}]#[fg={},bg={},bold] #H \"", accent, status_bg, fg, accent, cursor, accent, bg, cursor),
            ]);
        } else if self.style == "rounded" {
            lines.extend(vec![
                "# Rounded Glyph Formatting & Oh-My-Posh Graphics".to_string(),
                "set -g status-left-length 50".to_string(),
                format!("set -g status-left \"#[fg={},bg={}]#[fg={},bg={},bold]  MiOS #[fg={},bg={}]#[fg={},bg={},bold]  #S #[fg={},bg={}] \"", accent, status_bg, fg, accent, accent, success, bg, success, success, status_bg),
                format!("set -g window-status-format \"#[fg={},bg={}]  #I  #W  \"", muted, status_bg),
                format!("set -g window-status-current-format \"#[fg={},bg={}]#[fg={},bg={},bold] #I  #W #[fg={},bg={}]\"", cursor, status_bg, bg, cursor, cursor, status_bg),
                "set -g status-right-length 100".to_string(),
                format!("set -g status-right \"#[fg={},bg={}]#[fg={},bg={}]  %H:%M #[fg={},bg={}]#[fg={},bg={},bold]  %Y-%m-%d #[fg={},bg={}]#[fg={},bg={},bold]  #H #[fg={},bg={}]\"", accent, status_bg, fg, accent, accent, success, bg, success, success, cursor, bg, cursor, cursor, status_bg),
            ]);
        } else {
            // minimal / plain
            lines.extend(vec![
                "# Minimal Status Line Formatting".to_string(),
                "set -g status-left-length 30".to_string(),
                format!("set -g status-left \"#[fg={},bold][#S] \"", accent),
                format!("set -g window-status-format \"#[fg={}]#I:#W\"", muted),
                format!(
                    "set -g window-status-current-format \"#[fg={},bold][#I:#W]\"",
                    cursor
                ),
                "set -g status-right-length 60".to_string(),
                format!(
                    "set -g status-right \"#[fg={}]%Y-%m-%d %H:%M #[fg={},bold]#H\"",
                    subtle, fg
                ),
            ]);
        }

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
