// AI-hint: Deterministic SSOT fastfetch telemetry projection; the standard dashboard config remains owned by dotfiles.registry.fastfetch.
// AI-doc: usr/share/doc/mios/manual/tools.md
// AI-related: usr/share/mios/mios.toml, tools/sync-generated.sh, automation/98-drift-checks.sh
#![forbid(unsafe_code)]
use serde_json::json;
use std::fs;
use std::path::{Component, Path, PathBuf};
pub const DEFAULT_FASTFETCH_TARGET: &str = "theme.fastfetch.telemetry_target";
#[derive(Debug)]
pub struct RenderFastfetchResult {
    pub target: Option<PathBuf>,
    pub jsonc_len: usize,
    pub lines_count: usize,
}
fn text<'a>(doc: &'a toml::Value, path: &str) -> Result<&'a str, String> {
    let mut value = doc;
    for key in path.split('.') {
        value = value
            .get(key)
            .ok_or_else(|| format!("missing SSOT {path}"))?;
    }
    value
        .as_str()
        .filter(|v| !v.trim().is_empty())
        .ok_or_else(|| format!("SSOT {path} must be a nonempty string"))
}
pub fn run_render_fastfetch(
    root: &Path,
    check: bool,
    out: Option<&Path>,
    logo_type: Option<&str>,
    mock: bool,
    dry_run: bool,
) -> Result<RenderFastfetchResult, String> {
    let vendor = root.join("usr/share/mios/mios.toml");
    let _: toml::Value = fs::read_to_string(&vendor)
        .map_err(|e| format!("{}: {e}", vendor.display()))?
        .parse()
        .map_err(|e| format!("{}: {e}", vendor.display()))?;
    let doc = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let colors = doc
        .get("colors")
        .and_then(|v| v.as_table())
        .ok_or("missing SSOT [colors]")?;
    for (key, value) in colors {
        let color = value
            .as_str()
            .ok_or_else(|| format!("[colors].{key}: expected #rrggbb string"))?;
        if color.len() != 7
            || !color.starts_with('#')
            || !color.as_bytes()[1..].iter().all(u8::is_ascii_hexdigit)
        {
            return Err(format!("[colors].{key}: expected #rrggbb hex color"));
        }
    }
    let logo = logo_type.unwrap_or("small");
    if !matches!(logo, "small" | "auto" | "none" | "raw") {
        return Err(format!("invalid logo_type {logo:?}"));
    }
    let mut exports =
        mios_resolver::emit::build_exports_map(&doc, mios_resolver::stack_offset_of(&doc));
    mios_resolver::emit::resolve_cross_references(&mut exports);
    let endpoint = exports
        .get("MIOS_AI_ENDPOINT")
        .filter(|v| !v.is_empty() && !v.contains("${"))
        .ok_or("missing or unresolved MIOS_AI_ENDPOINT")?;
    let config = json!({
        "$schema": "https://github.com/fastfetch-cli/fastfetch/raw/dev/doc/json_schema.json",
        "logo": {"type": logo},
        "display": {"separator": "  ", "color": {
            "keys": text(&doc, "theme.fastfetch.keys_color")?,
            "title": text(&doc, "theme.fastfetch.title_color")?,
            "output": text(&doc, "theme.fastfetch.output_color")?
        }},
        "modules": [
            {"type":"title"}, {"type":"separator"},
            {"type":"os", "key":"OS"}, {"type":"host", "key":"Host"},
            {"type":"kernel", "key":"Kernel"}, {"type":"uptime", "key":"Uptime"},
            {"type":"packages", "key":"Packages"}, {"type":"shell", "key":"Shell"},
            {"type":"display", "key":"Display"}, {"type":"wm", "key":"WM"},
            {"type":"cpu", "key":"CPU"}, {"type":"gpu", "key":"GPU"},
            {"type":"memory", "key":"Memory"}, {"type":"disk", "key":"Disk"},
            {"type":"break"},
            {"type":"custom", "key":"AI Engine", "format": endpoint},
            {"type":"custom", "key":"Active Model", "format": text(&doc, "ai.model")?},
            {"type":"custom", "key":"Bootc Image", "format": format!("{}:{}", text(&doc, "image.name")?, text(&doc, "image.tag")?)},
            {"type":"break"}, {"type":"colors", "symbol":"circle"}
        ]
    });
    let rendered = serde_json::to_string_pretty(&config).map_err(|e| e.to_string())? + "\n";
    let target = match out {
        Some(path) => path.to_path_buf(),
        None => {
            let relative = Path::new(text(&doc, DEFAULT_FASTFETCH_TARGET)?);
            if relative
                .components()
                .any(|c| !matches!(c, Component::Normal(_)))
                || relative.to_string_lossy().contains([':', '\\'])
            {
                return Err(
                    "fastfetch telemetry_target must be a safe repository-relative path".into(),
                );
            }
            root.join(relative)
        }
    };
    if check {
        let existing = fs::read_to_string(&target).map_err(|e| {
            format!(
                "fastfetch target missing or unreadable {}: {e}",
                target.display()
            )
        })?;
        if existing.replace("\r\n", "\n") != rendered {
            return Err(format!(
                "fastfetch config drifted from SSOT projection at {}",
                target.display()
            ));
        }
    } else if !dry_run && !mock {
        if let Some(parent) = target.parent().filter(|p| !p.as_os_str().is_empty()) {
            fs::create_dir_all(parent).map_err(|e| e.to_string())?;
        }
        fs::write(&target, &rendered).map_err(|e| e.to_string())?;
    }
    Ok(RenderFastfetchResult {
        target: Some(target),
        jsonc_len: rendered.len(),
        lines_count: rendered.lines().count(),
    })
}
