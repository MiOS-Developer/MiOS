// AI-hint: Terminal projections for mios-gen: the Fastfetch config and the tmux theme fixture with its layered --runtime render, all from SSOT (ADR-0021 gen category).
// AI-doc: usr/share/doc/mios/manual/tools.md
// AI-related: usr/share/mios/mios.toml, usr/share/mios/fastfetch/config.jsonc, automation/98-drift-checks.sh

pub mod fastfetch {
    #![forbid(unsafe_code)]

    use regex::Regex;
    use serde_json::json;
    use std::collections::BTreeMap;
    use std::fs;
    use std::path::{Path, PathBuf};

    /// Persisted, deterministic (`--mock`) render this projector is verified against.
    /// `usr/share/mios/fastfetch/config.jsonc` is NOT this generator's output: it is
    /// rendered from a template by `mios-dotfiles-render` and intentionally differs.
    pub const FASTFETCH_FIXTURE: &str = "tests/golden/fastfetch/mock.jsonc";

    #[derive(Debug, Clone)]
    pub struct FastfetchEngine {
        pub logo_type: String,
        pub mock: bool,
        pub dry_run: bool,
        #[allow(dead_code)]
        pub palette: BTreeMap<String, String>,
    }

    #[derive(Debug, Clone)]
    #[allow(dead_code)]
    pub struct RenderFastfetchResult {
        pub status: String,
        pub target: Option<PathBuf>,
        pub jsonc_len: usize,
        pub lines_count: usize,
        pub logo_type: String,
        pub mock: bool,
        pub dry_run: bool,
    }

    impl FastfetchEngine {
        pub fn new(
            doc: Option<&toml::Value>,
            logo_type: Option<&str>,
            mock: bool,
            dry_run: bool,
        ) -> Result<Self, String> {
            let logo = logo_type.unwrap_or("small").to_string();
            if !["small", "auto", "none", "raw"].contains(&logo.as_str()) {
                return Err(format!(
                    "Invalid logo_type '{logo}', must be one of: small, auto, none, raw"
                ));
            }

            let mut palette = BTreeMap::new();
            if let Some(doc) = doc {
                if let Some(colors_tbl) = doc.get("colors").and_then(|v| v.as_table()) {
                    let hex_re = match Regex::new(r"^#[0-9a-fA-F]{6}$") {
                        Ok(re) => re,
                        Err(e) => return Err(format!("Regex compilation failed: {e}")),
                    };
                    for (k, v) in colors_tbl {
                        if let Some(s) = v.as_str() {
                            if !hex_re.is_match(s) {
                                return Err(format!("[colors].{k}: expected #rrggbb hex color"));
                            }
                            palette.insert(k.clone(), s.to_string());
                        } else {
                            return Err(format!("[colors].{k}: expected #rrggbb string"));
                        }
                    }
                }
            }

            // Fill default palette if missing
            let defaults = [
                ("bg", "#282262"),
                ("fg", "#E7DFD3"),
                ("accent", "#1A407F"),
                ("cursor", "#F35C15"),
                ("success", "#3E7765"),
                ("warning", "#F35C15"),
                ("error", "#DC271B"),
                ("info", "#1A407F"),
                ("muted", "#948E8E"),
                ("subtle", "#B7C9D7"),
            ];
            for (k, v) in defaults {
                palette
                    .entry(k.to_string())
                    .or_insert_with(|| v.to_string());
            }

            Ok(Self {
                logo_type: logo,
                mock,
                dry_run,
                palette,
            })
        }

        pub fn inspect_system_metadata(&self, root: &Path) -> BTreeMap<String, String> {
            if self.mock {
                let mut map = BTreeMap::new();
                map.insert(
                    "os_name".to_string(),
                    "MiOS Linux (bootc/OCI workstation)".to_string(),
                );
                map.insert(
                    "ai_engine".to_string(),
                    "mios-llm-light (llama-swap :8500)".to_string(),
                );
                map.insert(
                    "active_model".to_string(),
                    "Qwen2.5-Coder-7B-Instruct-GGUF".to_string(),
                );
                map.insert(
                    "bootc_image".to_string(),
                    "ghcr.io/mios-dev/mios:latest (sha256:7f8a91b2c3d4)".to_string(),
                );
                map.insert("mesh_nodes".to_string(), "1 (Local Node)".to_string());
                map.insert("version".to_string(), "2026.1".to_string());
                return map;
            }

            let mut os_name = "MiOS Linux".to_string();
            let os_rel_paths = [
                PathBuf::from("/etc/os-release"),
                root.join("etc/os-release"),
            ];
            for p in &os_rel_paths {
                if let Ok(content) = fs::read_to_string(p) {
                    for line in content.lines() {
                        if let Some(val) = line.strip_prefix("PRETTY_NAME=") {
                            let trimmed = val.trim().trim_matches('"');
                            if !trimmed.is_empty() {
                                os_name = trimmed.to_string();
                                break;
                            }
                        }
                    }
                    break;
                }
            }

            let mut bootc_img = "ghcr.io/mios-dev/mios:latest".to_string();
            let ver_path = root.join("usr/share/mios/VERSION");
            if let Ok(ver) = fs::read_to_string(&ver_path) {
                let t = ver.trim();
                if !t.is_empty() {
                    bootc_img = format!("ghcr.io/mios-dev/mios:{t}");
                }
            }

            let mut map = BTreeMap::new();
            map.insert("os_name".to_string(), os_name);
            map.insert(
                "ai_engine".to_string(),
                "mios-llm-light (llama-swap :8500)".to_string(),
            );
            map.insert(
                "active_model".to_string(),
                "Qwen2.5-Coder-7B-Instruct-GGUF".to_string(),
            );
            map.insert("bootc_image".to_string(), bootc_img);
            map.insert("mesh_nodes".to_string(), "1 (Local Node)".to_string());
            map.insert("version".to_string(), "2026.1".to_string());
            map
        }

        pub fn generate_jsonc(&self, root: &Path) -> Result<String, String> {
            let meta = self.inspect_system_metadata(root);

            let os_name = meta
                .get("os_name")
                .cloned()
                .unwrap_or_else(|| "MiOS Linux".to_string());
            let ai_engine = meta
                .get("ai_engine")
                .cloned()
                .unwrap_or_else(|| "mios-llm-light".to_string());
            let active_model = meta
                .get("active_model")
                .cloned()
                .unwrap_or_else(|| "Qwen2.5-Coder-7B-Instruct".to_string());
            let mesh_nodes = meta
                .get("mesh_nodes")
                .cloned()
                .unwrap_or_else(|| "1".to_string());
            let bootc_image = meta
                .get("bootc_image")
                .cloned()
                .unwrap_or_else(|| "ghcr.io/mios-dev/mios:latest".to_string());

            let config = json!({
                "$schema": "https://github.com/fastfetch-cli/fastfetch/raw/dev/doc/json_schema.json",
                "logo": {
                    "type": self.logo_type,
                    "padding": {
                        "top": 1,
                        "left": 2,
                        "right": 2
                    }
                },
                "display": {
                    "separator": " 󰄾 ",
                    "color": {
                        "keys": "blue",
                        "title": "cyan",
                        "separator": "yellow"
                    }
                },
                "modules": [
                    {"type": "title"},
                    {"type": "separator"},
                    {"type": "os", "key": "OS", "format": os_name},
                    {"type": "host", "key": "Host"},
                    {"type": "kernel", "key": "Kernel"},
                    {"type": "uptime", "key": "Uptime"},
                    {"type": "packages", "key": "Packages"},
                    {"type": "shell", "key": "Shell"},
                    {"type": "display", "key": "Display"},
                    {"type": "wm", "key": "WM"},
                    {"type": "cpu", "key": "CPU"},
                    {"type": "gpu", "key": "GPU"},
                    {"type": "memory", "key": "Memory"},
                    {"type": "disk", "key": "Disk"},
                    {"type": "break"},
                    {"type": "custom", "key": "AI Engine", "format": ai_engine},
                    {"type": "custom", "key": "Active Model", "format": active_model},
                    {"type": "custom", "key": "Mesh Nodes", "format": mesh_nodes},
                    {"type": "custom", "key": "Bootc Image", "format": bootc_image},
                    {"type": "break"},
                    {"type": "colors", "symbol": "circle"}
                ]
            });

            // Legacy parity: Python `json.dumps(config, indent=2)` escapes every non-ASCII
            // code point as lowercase \uXXXX (UTF-16 surrogate pairs above the BMP) and
            // emits no trailing newline. Structural JSON is ASCII, so escaping the pretty
            // string char-by-char only ever touches string contents.
            serde_json::to_string_pretty(&config)
                .map(|s| escape_non_ascii(&s))
                .map_err(|e| format!("Failed to serialize fastfetch JSONC: {e}"))
        }
    }

    fn escape_non_ascii(s: &str) -> String {
        let mut out = String::with_capacity(s.len());
        for c in s.chars() {
            if c.is_ascii() {
                out.push(c);
            } else {
                let mut buf = [0u16; 2];
                for unit in c.encode_utf16(&mut buf) {
                    out.push_str(&format!("\\u{:04x}", unit));
                }
            }
        }
        out
    }

    pub fn run_render_fastfetch(
        root: &Path,
        check: bool,
        out: Option<&Path>,
        logo_type: Option<&str>,
        mock: bool,
        dry_run: bool,
    ) -> Result<RenderFastfetchResult, String> {
        let toml_path = root.join("usr/share/mios/mios.toml");
        if !toml_path.is_file() {
            return Err(format!("Missing SSOT toml file: {}", toml_path.display()));
        }
        let doc_val = {
            let content = fs::read_to_string(&toml_path)
                .map_err(|e| format!("Failed to read {}: {e}", toml_path.display()))?;
            let val: toml::Value = toml::from_str(&content)
                .map_err(|e| format!("Failed to parse {}: {e}", toml_path.display()))?;
            Some(val)
        };

        let engine = FastfetchEngine::new(doc_val.as_ref(), logo_type, mock, dry_run)?;
        let jsonc = engine.generate_jsonc(root)?;
        let lines_count = jsonc.lines().count();
        let jsonc_len = jsonc.len();

        let target_path = out.map(PathBuf::from);

        if check {
            // Peer-lane audit (2026-10-07): without a subject, `--check` used to return
            // success having compared nothing. A check reads and compares a real artifact
            // or it fails; `--mock` only pins the metadata the render is built from.
            let target = target_path.as_ref().ok_or_else(|| {
                "fastfetch --check requires --out <artifact> to verify; nothing was compared"
                    .to_string()
            })?;
            if !target.is_file() {
                return Err(format!(
                    "fastfetch target does not exist for verification: {}",
                    target.display()
                ));
            }
            let existing = fs::read_to_string(target)
                .map_err(|e| format!("Failed to read {}: {e}", target.display()))?;
            let to_write = jsonc.replace("\r\n", "\n");
            if existing != to_write {
                return Err(format!(
                    "fastfetch config drifted from SSOT projection at {}",
                    target.display()
                ));
            }
        } else if let Some(target) = &target_path {
            let to_write = jsonc.replace("\r\n", "\n");
            // `--dry-run` is the no-write switch. `--mock` pins metadata only, so an
            // explicit `--out` fixture can be regenerated deterministically.
            if !dry_run {
                if let Some(parent) = target.parent() {
                    if !parent.as_os_str().is_empty() && !parent.exists() {
                        fs::create_dir_all(parent).map_err(|e| {
                            format!("Failed to create parent dir {}: {e}", parent.display())
                        })?;
                    }
                }
                fs::write(target, &to_write)
                    .map_err(|e| format!("Failed to write {}: {e}", target.display()))?;
            }
        }

        Ok(RenderFastfetchResult {
            status: "success".to_string(),
            target: target_path,
            jsonc_len,
            lines_count,
            logo_type: engine.logo_type,
            mock: engine.mock,
            dry_run: engine.dry_run,
        })
    }
}

pub mod tmux_theme {
    pub use mios_service_core::tmux_theme::{is_remote_terminal, TmuxThemeEngine};
    use std::fs;
    use std::path::{Path, PathBuf};
    pub const GOLDEN: &str = "usr/share/mios/tmux/mios-theme.tmux.conf";

    #[allow(dead_code)]
    pub struct RenderTmuxThemeResult {
        pub style: String,
        pub status_position: String,
        pub config_lines: usize,
        pub output_path: Option<PathBuf>,
    }

    pub fn run_render_tmux_theme(
        root: &Path,
        check: bool,
        override_style: Option<&str>,
        override_position: Option<&str>,
        out_path: Option<&Path>,
    ) -> Result<RenderTmuxThemeResult, String> {
        let toml_path = root.join("usr/share/mios/mios.toml");
        if !toml_path.is_file() {
            return Err(format!("Missing SSOT toml file: {}", toml_path.display()));
        }

        let toml_str = fs::read_to_string(&toml_path)
            .map_err(|e| format!("Failed to read {}: {}", toml_path.display(), e))?;

        let doc: toml::Value = toml_str
            .parse()
            .map_err(|e| format!("Failed to parse {}: {}", toml_path.display(), e))?;

        let engine = TmuxThemeEngine::from_toml(&doc, override_style, override_position)?;
        let rendered = engine.generate_config()?;

        let target_path = if let Some(p) = out_path {
            p.to_path_buf()
        } else {
            root.join(GOLDEN)
        };

        if check {
            if !target_path.is_file() {
                return Err(format!(
                    "Target fixture does not exist: {}",
                    target_path.display()
                ));
            }

            let disk_content = fs::read_to_string(&target_path)
                .map_err(|e| format!("Failed to read {}: {}", target_path.display(), e))?;

            let to_write = rendered.replace("\r\n", "\n");
            if disk_content != to_write {
                return Err(format!(
                "{}: out of sync with mios.toml [theme.tmux] projection (run 'mios-gen render-tmux-theme')",
                GOLDEN
            ));
            }
        } else {
            let to_write = rendered.replace("\r\n", "\n");
            if let Some(parent) = target_path.parent() {
                fs::create_dir_all(parent).map_err(|e| {
                    format!("Failed to create parent dir {}: {}", parent.display(), e)
                })?;
            }

            fs::write(&target_path, to_write.as_bytes())
                .map_err(|e| format!("Failed to write {}: {}", target_path.display(), e))?;
        }

        let config_lines = rendered.lines().count();

        Ok(RenderTmuxThemeResult {
            style: engine.style,
            status_position: engine.status_position,
            config_lines,
            output_path: Some(target_path),
        })
    }

    // `render-tmux-theme --runtime DIR`: tmux.conf and mios.omp.json for a caller-owned directory, from every SSOT tier.
    pub mod tmux_runtime {
        #![forbid(unsafe_code)]

        use super::{is_remote_terminal, TmuxThemeEngine};
        use std::env;
        use std::fs;
        use std::io::Write;
        use std::path::{Path, PathBuf};
        use std::process::Command;

        const KEYS_PATH: &str = "usr/share/mios/tmux/mios-keys.tmux.conf";

        fn env_nonempty(key: &str) -> Option<String> {
            env::var(key).ok().filter(|v| !v.trim().is_empty())
        }

        /// Sorted `*.toml` drop-ins of `dir` (systemd `.d` ordering by basename).
        fn fragments(dir: &Path) -> Vec<PathBuf> {
            let mut out: Vec<PathBuf> = match fs::read_dir(dir) {
                Ok(rd) => rd
                    .filter_map(|e| e.ok().map(|e| e.path()))
                    .filter(|p| p.is_file() && p.extension().map(|x| x == "toml").unwrap_or(false))
                    .collect(),
                Err(_) => Vec::new(),
            };
            out.sort_by(|a, b| a.file_name().cmp(&b.file_name()));
            out
        }

        /// Layer paths, lowest precedence first (mirrors `mios_toml.layer_paths`):
        /// tier-major vendor < host < user, each tier's monolith then its `mios.d/*.toml`.
        pub fn layer_paths(root: &Path) -> Vec<PathBuf> {
            let toml_root = env_nonempty("MIOS_TOML_ROOT");
            let vendor = env_nonempty("MIOS_VENDOR_TOML")
                .or_else(|| env_nonempty("MIOS_TOML"))
                .map(PathBuf::from)
                .unwrap_or_else(|| {
                    if let Some(r) = &toml_root {
                        Path::new(r).join("usr/share/mios/mios.toml")
                    } else if Path::new("/usr/share/mios/mios.toml").exists() {
                        PathBuf::from("/usr/share/mios/mios.toml")
                    } else {
                        root.join("usr/share/mios/mios.toml")
                    }
                });
            let host = PathBuf::from(
                env::var("MIOS_HOST_TOML").unwrap_or_else(|_| "/etc/mios/mios.toml".to_string()),
            );
            let user = env_nonempty("MIOS_USER_TOML")
                .map(PathBuf::from)
                .unwrap_or_else(|| {
                    let cfg = env_nonempty("XDG_CONFIG_HOME")
                        .map(PathBuf::from)
                        .or_else(|| {
                            env_nonempty("HOME")
                                .or_else(|| env_nonempty("USERPROFILE"))
                                .map(|h| Path::new(&h).join(".config"))
                        })
                        .unwrap_or_else(|| PathBuf::from(".config"));
                    cfg.join("mios/mios.toml")
                });
            let vendor_d = env_nonempty("MIOS_VENDOR_TOML_D")
                .map(PathBuf::from)
                .unwrap_or_else(|| match &toml_root {
                    Some(r) => Path::new(r).join("usr/lib/mios/mios.d"),
                    None => PathBuf::from("/usr/lib/mios/mios.d"),
                });
            let host_d = env_nonempty("MIOS_HOST_TOML_D")
                .map(PathBuf::from)
                .unwrap_or_else(|| host.parent().unwrap_or(Path::new("")).join("mios.d"));
            let user_d = env_nonempty("MIOS_USER_TOML_D")
                .map(PathBuf::from)
                .unwrap_or_else(|| user.parent().unwrap_or(Path::new("")).join("mios.d"));

            let mut layers = vec![vendor];
            layers.extend(fragments(&vendor_d));
            layers.push(host);
            layers.extend(fragments(&host_d));
            layers.push(user);
            layers.extend(fragments(&user_d));
            layers
        }

        /// Recursive merge: later wins; an empty string never overrides a non-empty value below it.
        pub fn deep_merge(dst: &mut toml::Value, src: toml::Value) {
            match (dst, src) {
                (toml::Value::Table(d), toml::Value::Table(s)) => {
                    for (k, v) in s {
                        match d.get_mut(&k) {
                            Some(existing) if existing.is_table() && v.is_table() => {
                                deep_merge(existing, v)
                            }
                            Some(existing) => {
                                let empty = v.as_str().map(|x| x.is_empty()).unwrap_or(false);
                                let below_set = !matches!(existing.as_str(), Some("") | None);
                                if !(empty && below_set) {
                                    *existing = v;
                                }
                            }
                            None => {
                                d.insert(k, v);
                            }
                        }
                    }
                }
                (d, s) => *d = s,
            }
        }

        /// Full layered SSOT. The vendor tier is mandatory; a missing or unreadable host/user
        /// overlay is skipped (a broken overlay must not crash a reader, as in `mios_toml`).
        pub fn load_layered(root: &Path) -> Result<toml::Value, String> {
            let layers = layer_paths(root);
            let vendor = &layers[0];
            if !vendor.is_file() {
                return Err(format!("Missing SSOT toml file: {}", vendor.display()));
            }
            let mut merged = toml::Value::Table(toml::map::Map::new());
            for (i, path) in layers.iter().enumerate() {
                if !path.is_file() {
                    continue;
                }
                let parsed = fs::read_to_string(path)
                    .map_err(|e| e.to_string())
                    .and_then(|s| s.parse::<toml::Value>().map_err(|e| e.to_string()));
                match parsed {
                    Ok(v) => deep_merge(&mut merged, v),
                    Err(e) if i == 0 => {
                        return Err(format!(
                            "Failed to load vendor SSOT {}: {}",
                            path.display(),
                            e
                        ))
                    }
                    Err(_) => {}
                }
            }
            Ok(merged)
        }

        fn exe_name(base: &str) -> String {
            if cfg!(windows) {
                format!("{base}.exe")
            } else {
                base.to_string()
            }
        }

        fn find_unit_gen(root: &Path) -> Option<PathBuf> {
            let name = exe_name("mios-unit-gen");
            let mut cands: Vec<PathBuf> = Vec::new();
            if let Some(p) = env_nonempty("MIOS_UNIT_GEN") {
                cands.push(PathBuf::from(p));
            }
            if let Ok(me) = env::current_exe() {
                if let Some(dir) = me.parent() {
                    cands.push(dir.join(&name));
                }
            }
            for profile in ["release", "debug"] {
                cands.push(root.join("tools/native/target").join(profile).join(&name));
            }
            cands.push(PathBuf::from("/usr/libexec/mios").join(&name));
            cands.into_iter().find(|p| p.is_file())
        }

        fn find_theme_sync(root: &Path) -> Option<PathBuf> {
            let mut cands: Vec<PathBuf> = Vec::new();
            if let Some(p) = env_nonempty("MIOS_THEME_SYNC") {
                cands.push(PathBuf::from(p));
            }
            cands.push(root.join("usr/libexec/mios/ux/theme_sync.py"));
            cands.push(PathBuf::from("/usr/libexec/mios/ux/theme_sync.py"));
            cands.into_iter().find(|p| p.is_file())
        }

        /// Create the projection directory 0700, refuse symlinks and (on Linux) directories
        /// the caller does not own. `/proc/self` is owned by the process uid, which gives a
        /// safe-Rust `getuid()` without `unsafe`.
        fn prepare_dir(dir: &Path) -> Result<(), String> {
            #[cfg(unix)]
            {
                use std::os::unix::fs::{DirBuilderExt, MetadataExt, PermissionsExt};
                let mut b = fs::DirBuilder::new();
                b.recursive(true).mode(0o700);
                b.create(dir)
                    .map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
                let meta = fs::symlink_metadata(dir).map_err(|e| e.to_string())?;
                if meta.file_type().is_symlink() || !meta.is_dir() {
                    return Err("tmux projection directory must be a real directory".to_string());
                }
                if let Ok(me) = fs::metadata("/proc/self") {
                    if meta.uid() != me.uid() {
                        return Err(
                            "tmux projection directory must belong to the caller".to_string()
                        );
                    }
                }
                let _ = fs::set_permissions(dir, fs::Permissions::from_mode(0o700));
            }
            #[cfg(not(unix))]
            {
                fs::create_dir_all(dir)
                    .map_err(|e| format!("cannot create {}: {e}", dir.display()))?;
            }
            Ok(())
        }

        fn write_atomic(path: &Path, text: &str) -> Result<(), String> {
            let tmp = path.with_extension("tmp-mios");
            {
                let mut f =
                    fs::File::create(&tmp).map_err(|e| format!("{}: {e}", tmp.display()))?;
                f.write_all(text.as_bytes())
                    .map_err(|e| format!("{}: {e}", tmp.display()))?;
            }
            fs::rename(&tmp, path).map_err(|e| {
                let _ = fs::remove_file(&tmp);
                format!("{}: {e}", path.display())
            })
        }

        fn render_keys(root: &Path, dir: &Path, merged: &toml::Value) -> Result<String, String> {
            let kb = merged
                .get("keybindings")
                .ok_or_else(|| "Missing [keybindings] section in layered SSOT".to_string())?;
            let unit_gen = find_unit_gen(root).ok_or_else(|| {
                "mios-unit-gen not found (set MIOS_UNIT_GEN or build it)".to_string()
            })?;
            let src = dir.join(".keybindings.in.json");
            // The console menu variants read [theme.tmux.console]; nothing else outside [keybindings].
            let console = merged
                .get("theme")
                .and_then(|t| t.get("tmux"))
                .and_then(|t| t.get("console"))
                .ok_or_else(|| "Missing [theme.tmux.console] section in layered SSOT".to_string())?;
            let payload = serde_json::json!({ "keybindings": kb, "theme": { "tmux": { "console": console } } });
            fs::write(&src, payload.to_string()).map_err(|e| format!("{}: {e}", src.display()))?;
            let out = Command::new(&unit_gen)
                .args(["keybindings", "--from-json"])
                .arg(&src)
                .arg("--emit-json")
                .output();
            let _ = fs::remove_file(&src);
            let out = out.map_err(|e| format!("{}: {e}", unit_gen.display()))?;
            if !out.status.success() {
                return Err(format!(
                    "mios-unit-gen keybindings failed: {}",
                    String::from_utf8_lossy(&out.stderr).trim()
                ));
            }
            let parsed: serde_json::Value = serde_json::from_slice(&out.stdout)
                .map_err(|e| format!("mios-unit-gen keybindings emitted invalid JSON: {e}"))?;
            parsed
                .get(KEYS_PATH)
                .and_then(|v| v.as_str())
                .map(|s| s.to_string())
                .ok_or_else(|| format!("mios-unit-gen keybindings output lacks {KEYS_PATH}"))
        }

        fn render_prompt(root: &Path) -> Result<String, String> {
            let script = find_theme_sync(root)
                .ok_or_else(|| "theme_sync.py not found (set MIOS_THEME_SYNC)".to_string())?;
            let interpreters: &[&str] = if cfg!(windows) {
                &["python", "python3"]
            } else {
                &["python3", "python"]
            };
            let mut last_err = String::from("no python interpreter found");
            for py in interpreters {
                let mut cmd = Command::new(py);
                cmd.arg(&script).arg("--render-prompt");
                if is_remote_terminal() {
                    cmd.arg("--remote");
                }
                match cmd.output() {
                    Ok(o) if o.status.success() => {
                        return String::from_utf8(o.stdout)
                            .map_err(|e| format!("prompt renderer emitted non-UTF-8: {e}"))
                    }
                    Ok(o) => {
                        return Err(format!(
                            "prompt renderer failed: {}",
                            String::from_utf8_lossy(&o.stderr).trim()
                        ))
                    }
                    Err(e) => last_err = format!("{py}: {e}"),
                }
            }
            Err(last_err)
        }

        /// Project `tmux.conf` (theme + keybindings) and `mios.omp.json` from all SSOT tiers
        /// into `dir`. Nothing is written until every artifact has rendered, so a failed
        /// render never leaves a half-projected directory.
        pub fn project_runtime(root: &Path, dir: &Path) -> Result<(), String> {
            let merged = load_layered(root)?;
            let theme = TmuxThemeEngine::from_toml(&merged, None, None)?.generate_config()?;
            prepare_dir(dir)?;
            let keys = render_keys(root, dir, &merged)?;
            let omp = render_prompt(root)?;
            let body = format!("{theme}\n{keys}");
            // A pane shell re-sources the human server only when this digest differs from the server's.
            let digest = {
                use sha2::{Digest, Sha256};
                Sha256::digest(body.as_bytes()).iter().map(|b| format!("{b:02x}")).collect::<String>()
            };
            let tmux_conf = format!("{body}set -g @mios-conf-digest {digest}\n");
            write_atomic(&dir.join("mios.omp.json"), &omp)?;
            write_atomic(&dir.join("tmux.conf"), &tmux_conf)?;
            Ok(())
        }

        #[cfg(test)]
        mod tests {
            use super::*;

            fn parse(s: &str) -> toml::Value {
                s.parse().unwrap()
            }

            #[test]
            fn higher_layer_wins_and_tables_merge() {
                let mut d = parse("[a]\nx = 1\ny = 2\n");
                deep_merge(&mut d, parse("[a]\ny = 3\n[b]\nz = 4\n"));
                assert_eq!(d["a"]["x"].as_integer(), Some(1));
                assert_eq!(d["a"]["y"].as_integer(), Some(3));
                assert_eq!(d["b"]["z"].as_integer(), Some(4));
            }

            #[test]
            fn empty_string_never_overrides_set_value() {
                let mut d = parse("[a]\nk = \"keep\"\nn = \"\"\n");
                deep_merge(&mut d, parse("[a]\nk = \"\"\nn = \"fill\"\n"));
                assert_eq!(d["a"]["k"].as_str(), Some("keep"));
                assert_eq!(d["a"]["n"].as_str(), Some("fill"));
            }
        }
    }
}

/// Kernel-console and terminal palettes share the layered SSOT ANSI slots.
pub mod console {
    use std::{fs, path::Path};
    use toml::Value;
    fn palette(data: &Value) -> Result<Vec<String>, String> {
        let colors = data
            .get("colors")
            .and_then(Value::as_table)
            .ok_or("Missing colors")?;
        (0..16)
            .map(|i| {
                let prefix = format!("ansi_{i}_");
                let mut found = colors.iter().filter(|(key, _)| key.starts_with(&prefix));
                let (_, value) = found
                    .next()
                    .ok_or_else(|| format!("Missing {prefix} color"))?;
                if found.next().is_some() {
                    return Err(format!("Ambiguous {prefix} color"));
                }
                let s = value.as_str().ok_or("Color must be a string")?;
                if s.len() != 7
                    || !s.starts_with('#')
                    || !s[1..].bytes().all(|c| c.is_ascii_hexdigit())
                {
                    return Err(format!("Invalid {prefix} color"));
                }
                Ok(s.to_string())
            })
            .collect()
    }
    pub fn rgb(data: &Value) -> Result<String, String> {
        let colors = palette(data)?;
        let mut rows = Vec::new();
        for channel in 0..3 {
            rows.push(
                colors
                    .iter()
                    .map(|color| {
                        u8::from_str_radix(&color[1 + channel * 2..3 + channel * 2], 16)
                            .map(|n| n.to_string())
                            .map_err(|e| e.to_string())
                    })
                    .collect::<Result<Vec<_>, _>>()?
                    .join(","),
            );
        }
        Ok(rows.join("\n") + "\n")
    }
    pub fn escapes(data: &Value, term: &str) -> Result<String, String> {
        if term.is_empty() || term == "dumb" {
            return Ok(String::new());
        }
        let colors = palette(data)?;
        let mut out = String::new();
        for (i, color) in colors.iter().enumerate() {
            if term == "linux" {
                out += &format!("\x1b]P{i:x}{}", &color[1..]);
            } else {
                out += &format!("\x1b]4;{i};{color}\x07");
            }
        }
        if term == "linux" {
            out += "\x1b[37;40m";
        } else {
            for (code, key) in [(10, "fg"), (11, "bg"), (12, "cursor")] {
                let color = data["colors"]
                    .get(key)
                    .and_then(Value::as_str)
                    .ok_or_else(|| format!("Missing {key}"))?;
                if color.len() != 7
                    || !color.starts_with('#')
                    || !color[1..].bytes().all(|c| c.is_ascii_hexdigit())
                {
                    return Err(format!("Invalid {key} color"));
                }
                out += &format!("\x1b]{code};{color}\x07");
            }
        }
        Ok(out)
    }
    pub fn run(root: &Path, check: bool, out: Option<&Path>) -> Result<String, String> {
        let data = super::tmux_theme::tmux_runtime::load_layered(root)?;
        let text = rgb(&data)?;
        let path = out
            .map(Path::to_path_buf)
            .unwrap_or_else(|| root.join("usr/share/mios/theme/console-palette"));
        if check {
            if fs::read_to_string(&path).map_err(|e| e.to_string())? != text {
                return Err("Console palette drift".into());
            }
        } else {
            fs::create_dir_all(path.parent().ok_or("No palette parent")?)
                .map_err(|e| e.to_string())?;
            fs::write(&path, text).map_err(|e| e.to_string())?;
        }
        Ok(format!(
            "Console palette matches SSOT: {}\n",
            path.display()
        ))
    }
    #[cfg(test)]
    mod tests {
        use super::*;
        fn data() -> Value {
            let colors = (0..16)
                .map(|i| format!("ansi_{i}_slot = '#123456'\n"))
                .collect::<String>();
            format!("[colors]\nfg='#abcdef'\nbg='#123456'\ncursor='#654321'\n{colors}")
                .parse()
                .unwrap()
        }
        #[test]
        fn rgb_rows_and_terminal_protocols_are_distinct() {
            let data = data();
            let rows = rgb(&data).unwrap();
            assert_eq!(rows.lines().count(), 3);
            assert!(rows.starts_with("18,18,18"));
            assert!(escapes(&data, "linux")
                .unwrap()
                .starts_with("\x1b]P0123456"));
            assert!(escapes(&data, "xterm")
                .unwrap()
                .contains("\x1b]10;#abcdef\x07"));
            assert!(escapes(&data, "dumb").unwrap().is_empty());
        }
        #[test]
        fn malformed_or_ambiguous_slots_fail_before_output() {
            let mut first = data();
            first["colors"]["ansi_0_slot"] = Value::String("#12bad".into());
            assert!(rgb(&first).is_err());
            let mut second = data();
            second["colors"]
                .as_table_mut()
                .unwrap()
                .insert("ansi_0_other".into(), Value::String("#abcdef".into()));
            assert!(rgb(&second).unwrap_err().contains("Ambiguous"));
        }
    }
}
