// AI-hint: SSOT btop theme renderer mapping exact RGB hex colors from mios.toml [colors] (ADR-0021 gen category).
// AI-doc: usr/share/doc/mios/manual/tools.md
// AI-related: usr/share/mios/mios.toml, etc/btop/themes/mios.theme, automation/98-drift-checks.sh

#![forbid(unsafe_code)]

use regex::Regex;
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

pub const DEFAULT_THEME_PATH: &str = "etc/btop/themes/mios.theme";

#[derive(Debug, Clone)]
pub struct BtopThemeEngine {
    pub palette: BTreeMap<String, String>,
}

#[derive(Debug, Clone)]
#[allow(dead_code)]
pub struct RenderBtopThemeResult {
    pub status: String,
    pub target: PathBuf,
    pub theme_len: usize,
    pub keys_count: usize,
}

impl BtopThemeEngine {
    pub fn from_toml(doc: &toml::Value) -> Result<Self, String> {
        let colors_tbl = doc
            .get("colors")
            .and_then(|v| v.as_table())
            .ok_or_else(|| "Missing [colors] section in mios.toml".to_string())?;

        let hex_re = Regex::new(r"^#[0-9a-fA-F]{6}$")
            .map_err(|e| format!("Failed to compile color validator: {e}"))?;
        let mut palette = BTreeMap::new();
        for (k, v) in colors_tbl {
            if let Some(s) = v.as_str() {
                if !hex_re.is_match(s) {
                    return Err(format!("[colors].{k}: expected #rrggbb"));
                }
                palette.insert(k.clone(), s.to_string());
            } else {
                return Err(format!("[colors].{k}: expected #rrggbb string"));
            }
        }

        // Validate mandatory colors
        for required in &[
            "bg", "fg", "accent", "cursor", "success", "warning", "error", "muted", "subtle",
        ] {
            if !palette.contains_key(*required) {
                return Err(format!(
                    "Missing required color [colors].{required} in mios.toml"
                ));
            }
        }

        Ok(Self { palette })
    }

    pub fn build_theme_mapping(&self) -> BTreeMap<String, String> {
        let p = &self.palette;
        let get = |k: &str, def: &str| p.get(k).cloned().unwrap_or_else(|| def.to_string());

        let bg = get("bg", "#282262");
        let fg = get("fg", "#E7DFD3");
        let accent = get("accent", "#1A407F");
        let cursor = get("cursor", "#F35C15");
        let success = get("success", "#3E7765");
        let warning = get("warning", "#F35C15");
        let error = get("error", "#DC271B");
        let muted = get("muted", "#948E8E");
        let subtle = get("subtle", "#B7C9D7");
        let cyan = get("ansi_12_bright_blue", "#3D6BA8");

        let mut m = BTreeMap::new();
        // Main UI
        m.insert("main_bg".to_string(), bg);
        m.insert("main_fg".to_string(), fg.clone());
        m.insert("title".to_string(), fg.clone());
        m.insert("hi_fg".to_string(), cursor.clone());
        m.insert("selected_bg".to_string(), accent.clone());
        m.insert("selected_fg".to_string(), fg);
        m.insert("inactive_fg".to_string(), muted.clone());
        m.insert("graph_text".to_string(), subtle.clone());
        m.insert("meter_bg".to_string(), muted.clone());
        m.insert("proc_misc".to_string(), subtle.clone());

        // Box outlines
        m.insert("cpu_box".to_string(), accent.clone());
        m.insert("mem_box".to_string(), accent.clone());
        m.insert("net_box".to_string(), accent.clone());
        m.insert("proc_box".to_string(), accent.clone());
        m.insert("div_line".to_string(), muted);

        // Temperature gradient (Cool -> Warm -> Hot)
        m.insert("temp_start".to_string(), success.clone());
        m.insert("temp_mid".to_string(), warning.clone());
        m.insert("temp_end".to_string(), error.clone());

        // CPU gradient
        m.insert("cpu_start".to_string(), success.clone());
        m.insert("cpu_mid".to_string(), warning.clone());
        m.insert("cpu_end".to_string(), error.clone());

        // Memory gradients
        m.insert("free_start".to_string(), success.clone());
        m.insert("free_mid".to_string(), subtle.clone());
        m.insert("free_end".to_string(), cyan.clone());

        m.insert("cached_start".to_string(), accent.clone());
        m.insert("cached_mid".to_string(), cyan.clone());
        m.insert("cached_end".to_string(), subtle.clone());

        m.insert("available_start".to_string(), success.clone());
        m.insert("available_mid".to_string(), subtle.clone());
        m.insert("available_end".to_string(), cyan.clone());

        m.insert("used_start".to_string(), warning.clone());
        m.insert("used_mid".to_string(), cursor.clone());
        m.insert("used_end".to_string(), error.clone());

        // Network gradients
        m.insert("download_start".to_string(), cyan);
        m.insert("download_mid".to_string(), accent);
        m.insert("download_end".to_string(), subtle);

        m.insert("upload_start".to_string(), cursor);
        m.insert("upload_mid".to_string(), warning.clone());
        m.insert("upload_end".to_string(), error.clone());

        // Process meters
        m.insert("process_start".to_string(), success);
        m.insert("process_mid".to_string(), warning);
        m.insert("process_end".to_string(), error);

        m
    }

    pub fn render_theme_text(&self) -> String {
        let mapping = self.build_theme_mapping();
        let mut lines = vec![
            "# MiOS Btop System Monitor Theme".to_string(),
            "# Generated automatically from mios.toml [colors] SSOT".to_string(),
            "# Do NOT edit directly; regenerate using `mios-gen render-btop-theme`".to_string(),
            "".to_string(),
        ];

        for (k, v) in mapping {
            lines.push(format!("theme[{k}]=\"{v}\""));
        }
        lines.push("".to_string());
        lines.join("\n")
    }

    pub fn validate_theme_content(content: &str) -> Result<(), Vec<String>> {
        let mut errors = Vec::new();
        let mut found_keys = BTreeSet::new();
        let line_re = Regex::new(r#"^theme\[([a-zA-Z0-9_]+)\]\s*=\s*"([^"]*)""#)
            .map_err(|e| vec![format!("Failed to compile theme validator: {e}")])?;
        let hex_re = Regex::new(r"^#[0-9a-fA-F]{6}$")
            .map_err(|e| vec![format!("Failed to compile color validator: {e}")])?;

        for (idx, raw_line) in content.lines().enumerate() {
            let line = raw_line.trim();
            if line.is_empty() || line.starts_with('#') {
                continue;
            }

            if let Some(caps) = line_re.captures(line) {
                let key = caps
                    .get(1)
                    .ok_or_else(|| vec!["Theme validator did not capture a key".to_string()])?
                    .as_str();
                let hex_val = caps
                    .get(2)
                    .ok_or_else(|| vec!["Theme validator did not capture a value".to_string()])?
                    .as_str();
                found_keys.insert(key.to_string());

                // Value may be empty string for transparency or valid #rrggbb hex
                if !hex_val.is_empty() && !hex_re.is_match(hex_val) {
                    errors.push(format!(
                        "Line {}: Invalid hex color '{}' for key '{}'",
                        idx + 1,
                        hex_val,
                        key
                    ));
                }
            } else {
                errors.push(format!(
                    "Line {}: Invalid syntax format: '{}'",
                    idx + 1,
                    line
                ));
            }
        }

        let required = [
            "main_bg",
            "main_fg",
            "cpu_box",
            "mem_box",
            "temp_start",
            "cpu_start",
        ];
        for req in &required {
            if !found_keys.contains(*req) {
                errors.push(format!("Missing required btop theme key: '{req}'"));
            }
        }

        if errors.is_empty() {
            Ok(())
        } else {
            Err(errors)
        }
    }
}

pub fn run_render_btop_theme(
    root: &Path,
    check: bool,
    out_path: Option<&Path>,
) -> Result<RenderBtopThemeResult, String> {
    let toml_path = root.join("usr/share/mios/mios.toml");
    let toml_str = fs::read_to_string(&toml_path)
        .map_err(|e| format!("Failed to read {}: {}", toml_path.display(), e))?;

    let doc: toml::Value = toml_str
        .parse()
        .map_err(|e| format!("Failed to parse {}: {}", toml_path.display(), e))?;

    let engine = BtopThemeEngine::from_toml(&doc)?;
    let rendered = engine.render_theme_text();

    let target_path = if let Some(p) = out_path {
        p.to_path_buf()
    } else {
        root.join(DEFAULT_THEME_PATH)
    };

    if check {
        if !target_path.is_file() {
            return Err(format!(
                "btop theme target does not exist for verification: {}",
                target_path.display()
            ));
        }
        let disk_content = fs::read_to_string(&target_path)
            .map_err(|e| format!("Failed to read {}: {}", target_path.display(), e))?;
        BtopThemeEngine::validate_theme_content(&disk_content).map_err(|errs| errs.join("; "))?;
        if disk_content.replace("\r\n", "\n") != rendered.replace("\r\n", "\n") {
            return Err(format!(
                "btop theme drifted from SSOT projection at {}",
                target_path.display()
            ));
        }
    } else {
        if let Some(parent) = target_path.parent() {
            fs::create_dir_all(parent)
                .map_err(|e| format!("Failed to create parent dir {}: {}", parent.display(), e))?;
        }
        fs::write(&target_path, rendered.as_bytes())
            .map_err(|e| format!("Failed to write {}: {}", target_path.display(), e))?;
    }

    Ok(RenderBtopThemeResult {
        status: "success".to_string(),
        target: target_path,
        theme_len: rendered.len(),
        keys_count: engine.build_theme_mapping().len(),
    })
}
