// AI-hint: SSOT btop theme renderer mapping exact RGB hex colors from mios.toml [colors] (ADR-0021 gen category).
// AI-doc: usr/share/doc/mios/manual/tools.md
// AI-related: usr/share/mios/mios.toml, etc/btop/themes/mios.theme, automation/98-drift-checks.sh

#![forbid(unsafe_code)]

use regex::Regex;
use std::collections::{BTreeMap, BTreeSet};
use std::fs;
use std::path::{Path, PathBuf};

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

    pub fn render_theme_text(&self, template: &str) -> Result<String, String> {
        let token = Regex::new(r"@MIOS:([a-z0-9_.-]+)@")
            .map_err(|e| format!("Failed to compile theme token matcher: {e}"))?;
        let mut rendered = String::with_capacity(template.len());
        let mut offset = 0;
        for capture in token.captures_iter(template) {
            let matched = capture.get(0).ok_or("Missing theme token match")?;
            let name = &capture[1];
            let value = self
                .palette
                .get(name)
                .ok_or_else(|| format!("Unknown btop theme color token @MIOS:{name}@"))?;
            rendered.push_str(&template[offset..matched.start()]);
            rendered.push_str(value);
            offset = matched.end();
        }
        rendered.push_str(&template[offset..]);
        if rendered.contains("@MIOS:") {
            return Err("Malformed btop theme token".to_string());
        }
        Self::validate_theme_content(&rendered).map_err(|errors| errors.join("; "))?;
        Ok(rendered)
    }

    pub fn validate_theme_content(content: &str) -> Result<(), Vec<String>> {
        let mut errors = Vec::new();
        let mut found_keys = BTreeSet::new();
        let line_re = Regex::new(r#"^theme\[([a-zA-Z0-9_]+)\]\s*=\s*"([^"]*)"\s*$"#)
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
                if !found_keys.insert(key.to_string()) {
                    errors.push(format!("Line {}: duplicate theme key '{key}'", idx + 1));
                }

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
    let doc = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let surface = doc
        .get("dotfiles")
        .and_then(|v| v.get("registry"))
        .and_then(|v| v.get("btop"))
        .ok_or("Missing [dotfiles.registry.btop] in layered mios.toml")?;
    let declared_path = |key: &str| -> Result<PathBuf, String> {
        let path = surface
            .get(key)
            .and_then(|v| v.as_str())
            .filter(|path| !path.is_empty())
            .ok_or_else(|| format!("Missing [dotfiles.registry.btop].{key}"))?;
        Ok(root.join(path))
    };
    let template_path = declared_path("template")?;
    let template = fs::read_to_string(&template_path)
        .map_err(|e| format!("Failed to read {}: {e}", template_path.display()))?;
    let engine = BtopThemeEngine::from_toml(&doc)?;
    let rendered = engine.render_theme_text(&template)?;
    let target_path = match out_path {
        Some(path) => path.to_path_buf(),
        None => declared_path("target")?,
    };

    let to_write = rendered.replace("\r\n", "\n");
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
        if disk_content != to_write {
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
        fs::write(&target_path, to_write.as_bytes())
            .map_err(|e| format!("Failed to write {}: {}", target_path.display(), e))?;
    }

    Ok(RenderBtopThemeResult {
        status: "success".to_string(),
        target: target_path,
        theme_len: rendered.len(),
        keys_count: rendered
            .lines()
            .filter(|line| line.trim_start().starts_with("theme["))
            .count(),
    })
}
