// AI-hint: SSOT projectors for the AI repository and tool manifests and for the agent-pipe module-boundary manifest (ADR-0021 gen category).
// AI-doc: usr/share/doc/mios/manual/tools.md
// AI-related: automation/manifest.json, tools/manifest.json, specs/manifest.json, usr/share/mios/pipe-boundaries.manifest.json, automation/98-drift-checks.sh, tests/drift-gate-negatives.sh

pub mod ai_manifest {
    use flate2::read::GzDecoder;
    use flate2::write::GzEncoder;
    use flate2::Compression;
    use regex::Regex;
    use serde::Serialize;
    use serde_json::Value;
    use std::collections::HashSet;
    use std::fs;
    use std::io::{Read, Write};
    use std::path::{Path, PathBuf};
    use std::process::Command;

    pub const TARGETS: &[(&str, &str, bool)] = &[
        ("specs", "specs/manifest.json", false),
        (
            ".ai/foundation/memories",
            ".ai/foundation/memories/manifest.json",
            false,
        ),
        ("artifacts", "artifacts/manifest.json.gz", false),
        ("automation", "automation/manifest.json", true),
        ("tools", "tools/manifest.json", true),
        ("overlay", "manifest.json", true),
        ("evals", "evals/manifest.json", true),
        ("bib-configs", "bib-configs/manifest.json", true),
        ("agents/research", "agents/research/manifest.json", true),
        (".", "root-manifest.json", false),
    ];

    pub const IGNORE_DIRS: &[&str] = &[
        ".git",
        ".venv",
        "output",
        "__pycache__",
        "agents/research",
        "node_modules",
        "target",
        "dist",
        "build",
        ".system_generated",
        "scratch",
        "logs",
    ];

    #[derive(Serialize)]
    pub struct Manifest {
        pub source_directory: String,
        pub entries: Vec<ManifestEntry>,
    }

    #[derive(Serialize)]
    #[serde(untagged)]
    pub enum ManifestEntry {
        Documentation {
            path: String,
            title: String,
            #[serde(rename = "type")]
            entry_type: &'static str,
            metadata: serde_json::Map<String, Value>,
            knowledge: Value,
            content_preview: String,
            full_content: String,
        },
        StructuredData {
            path: String,
            title: String,
            #[serde(rename = "type")]
            entry_type: &'static str,
            structured_data: Value,
        },
        SourceCode {
            path: String,
            title: String,
            #[serde(rename = "type")]
            entry_type: &'static str,
            full_content: String,
        },
    }

    #[allow(dead_code)]
    impl ManifestEntry {
        pub fn path(&self) -> &str {
            match self {
                ManifestEntry::Documentation { path, .. } => path,
                ManifestEntry::StructuredData { path, .. } => path,
                ManifestEntry::SourceCode { path, .. } => path,
            }
        }
    }

    pub fn parse_markdown_metadata(
        content: &str,
    ) -> (String, serde_json::Map<String, Value>, Value) {
        let title_re = Regex::new(r"(?m)^#\s+(.+)$").expect("valid title regex");
        let title = title_re
            .captures(content)
            .and_then(|caps| caps.get(1))
            .map(|m| m.as_str().trim().to_string())
            .unwrap_or_else(|| "Untitled".to_string());

        let meta_re = Regex::new(r"(?m)^>\s+\*\*(.+?):\*\*\s+(.+)$").expect("valid meta regex");
        let mut metadata = serde_json::Map::new();
        for caps in meta_re.captures_iter(content) {
            if let (Some(k), Some(v)) = (caps.get(1), caps.get(2)) {
                let key = k.as_str().trim().to_lowercase().replace(' ', "_");
                let val = v.as_str().trim().to_string();
                metadata.insert(key, Value::String(val));
            }
        }

        let kb_re =
            Regex::new(r"(?s)```json:knowledge\s*\r?\n(.*?)\r?\n```").expect("valid kb regex");
        let knowledge_block = if let Some(caps) = kb_re.captures(content) {
            caps.get(1)
                .and_then(|m| serde_json::from_str(m.as_str()).ok())
                .unwrap_or_else(|| serde_json::json!({}))
        } else {
            serde_json::json!({})
        };

        (title, metadata, knowledge_block)
    }

    pub fn get_tracked_files(root: &Path) -> Result<Option<HashSet<String>>, String> {
        // A standalone fixture has no index. A checkout with an unreadable index
        // must fail rather than accidentally publishing ignored operator files.
        if !root.join(".git").try_exists().map_err(|e| e.to_string())? {
            return Ok(None);
        }
        let output = Command::new("git")
            .current_dir(root)
            .args(["ls-files"])
            .output()
            .map_err(|e| format!("cannot read tracked source index: {e}"))?;

        if !output.status.success() {
            return Err(format!(
                "cannot read tracked source index: {}",
                String::from_utf8_lossy(&output.stderr).trim()
            ));
        }

        let text = String::from_utf8(output.stdout)
            .map_err(|e| format!("invalid tracked source index paths: {e}"))?;
        let set: HashSet<String> = text
            .lines()
            .map(|l| l.trim().to_string())
            .filter(|s| !s.is_empty())
            .collect();

        if set.is_empty() {
            return Err("tracked source index is empty".into());
        }
        Ok(Some(set))
    }

    fn is_tracked(rel_path: &str, tracked: Option<&HashSet<String>>) -> bool {
        tracked.is_none_or(|set| set.contains(rel_path))
    }

    fn collect_json_entries(
        dir: &Path,
        root: &Path,
        recursive: bool,
        ignore_dirs: &HashSet<&str>,
        output_prefix: &str,
        tracked: Option<&HashSet<String>>,
        entries: &mut Vec<ManifestEntry>,
    ) {
        let read_res = match fs::read_dir(dir) {
            Ok(r) => r,
            Err(_) => return,
        };

        let mut subdirs: Vec<PathBuf> = Vec::new();
        let mut files: Vec<PathBuf> = Vec::new();

        for entry in read_res.flatten() {
            let path = entry.path();
            if path.is_dir() {
                let name = match path.file_name().and_then(|n| n.to_str()) {
                    Some(n) => n,
                    None => continue,
                };
                if !ignore_dirs.contains(name) {
                    subdirs.push(path);
                }
            } else if path.is_file() {
                files.push(path);
            }
        }

        subdirs.sort_by(|a, b| a.file_name().cmp(&b.file_name()));
        files.sort_by(|a, b| a.file_name().cmp(&b.file_name()));

        for file_path in files {
            let file_name = match file_path.file_name().and_then(|n| n.to_str()) {
                Some(n) => n,
                None => continue,
            };

            if file_name.starts_with(output_prefix) || file_name.ends_with(".tmp") {
                continue;
            }

            let rel_path_buf = match file_path.strip_prefix(root) {
                Ok(p) => p,
                Err(_) => continue,
            };
            let rel_path = rel_path_buf.to_string_lossy().replace('\\', "/");

            if !is_tracked(&rel_path, tracked) {
                continue;
            }

            if file_name.ends_with(".md") {
                let raw_bytes = match fs::read(&file_path) {
                    Ok(b) => b,
                    Err(_) => continue,
                };
                let content = match std::str::from_utf8(&raw_bytes) {
                    Ok(s) => s.replace("\r\n", "\n"),
                    Err(_) => continue,
                };

                let (title, metadata, knowledge) = parse_markdown_metadata(&content);
                let char_count = content.chars().count();
                let content_preview = if char_count > 500 {
                    let mut s: String = content.chars().take(500).collect();
                    s.push_str("...");
                    s
                } else {
                    content.clone()
                };

                entries.push(ManifestEntry::Documentation {
                    path: rel_path,
                    title,
                    entry_type: "documentation",
                    metadata,
                    knowledge,
                    content_preview,
                    full_content: content,
                });
            } else if file_name.ends_with(".json") {
                let raw_bytes = match fs::read(&file_path) {
                    Ok(b) => b,
                    Err(_) => continue,
                };
                let text = match std::str::from_utf8(&raw_bytes) {
                    Ok(s) => s,
                    Err(_) => continue,
                };
                let data: Value = match serde_json::from_str(text) {
                    Ok(v) => v,
                    Err(_) => continue,
                };

                let title = if let Value::Object(ref map) = data {
                    map.get("artifact_name")
                        .and_then(|v| v.as_str())
                        .unwrap_or(file_name)
                        .to_string()
                } else {
                    file_name.to_string()
                };

                entries.push(ManifestEntry::StructuredData {
                    path: rel_path,
                    title,
                    entry_type: "structured_data",
                    structured_data: data,
                });
            } else if file_name.ends_with(".sh")
                || file_name.ends_with(".ps1")
                || file_name.ends_with(".py")
                || file_name.ends_with(".toml")
                || file_name.ends_with("Containerfile")
                || file_name.ends_with("Justfile")
            {
                let raw_bytes = match fs::read(&file_path) {
                    Ok(b) => b,
                    Err(_) => continue,
                };
                let content = match std::str::from_utf8(&raw_bytes) {
                    Ok(s) => s.replace("\r\n", "\n"),
                    Err(_) => continue,
                };

                entries.push(ManifestEntry::SourceCode {
                    path: rel_path,
                    title: file_name.to_string(),
                    entry_type: "source_code",
                    full_content: content,
                });
            }
        }

        if recursive {
            for sub in subdirs {
                collect_json_entries(
                    &sub,
                    root,
                    recursive,
                    ignore_dirs,
                    output_prefix,
                    tracked,
                    entries,
                );
            }
        }
    }

    fn collect_gz_entries(
        dir: &Path,
        root: &Path,
        recursive: bool,
        ignore_dirs: &HashSet<&str>,
        output_prefix: &str,
        tracked: Option<&HashSet<String>>,
        entries: &mut Vec<ManifestEntry>,
    ) {
        let read_res = match fs::read_dir(dir) {
            Ok(r) => r,
            Err(_) => return,
        };

        let mut subdirs: Vec<PathBuf> = Vec::new();
        let mut files: Vec<PathBuf> = Vec::new();

        for entry in read_res.flatten() {
            let path = entry.path();
            if path.is_dir() {
                let name = match path.file_name().and_then(|n| n.to_str()) {
                    Some(n) => n,
                    None => continue,
                };
                if !ignore_dirs.contains(name) {
                    subdirs.push(path);
                }
            } else if path.is_file() {
                files.push(path);
            }
        }

        subdirs.sort_by(|a, b| a.file_name().cmp(&b.file_name()));
        files.sort_by(|a, b| a.file_name().cmp(&b.file_name()));

        for file_path in files {
            let file_name = match file_path.file_name().and_then(|n| n.to_str()) {
                Some(n) => n,
                None => continue,
            };

            if file_name.starts_with(output_prefix) || file_name.ends_with(".tmp") {
                continue;
            }

            let rel_path_buf = match file_path.strip_prefix(root) {
                Ok(p) => p,
                Err(_) => continue,
            };
            let rel_path = rel_path_buf.to_string_lossy().replace('\\', "/");

            if !is_tracked(&rel_path, tracked) {
                continue;
            }

            if file_name.ends_with(".json.gz") {
                let file = match fs::File::open(&file_path) {
                    Ok(f) => f,
                    Err(_) => continue,
                };
                let mut decoder = GzDecoder::new(file);
                let mut text = String::new();
                if decoder.read_to_string(&mut text).is_err() {
                    continue;
                }
                let data: Value = match serde_json::from_str(&text) {
                    Ok(v) => v,
                    Err(_) => continue,
                };

                let title = if let Value::Object(ref map) = data {
                    map.get("artifact_name")
                        .and_then(|v| v.as_str())
                        .unwrap_or(file_name)
                        .to_string()
                } else {
                    file_name.to_string()
                };

                entries.push(ManifestEntry::StructuredData {
                    path: rel_path,
                    title,
                    entry_type: "structured_data",
                    structured_data: data,
                });
            } else if file_name.ends_with(".json") {
                let raw_bytes = match fs::read(&file_path) {
                    Ok(b) => b,
                    Err(_) => continue,
                };
                let text = match std::str::from_utf8(&raw_bytes) {
                    Ok(s) => s,
                    Err(_) => continue,
                };
                let data: Value = match serde_json::from_str(text) {
                    Ok(v) => v,
                    Err(_) => continue,
                };

                let title = if let Value::Object(ref map) = data {
                    map.get("artifact_name")
                        .and_then(|v| v.as_str())
                        .unwrap_or(file_name)
                        .to_string()
                } else {
                    file_name.to_string()
                };

                entries.push(ManifestEntry::StructuredData {
                    path: rel_path,
                    title,
                    entry_type: "structured_data",
                    structured_data: data,
                });
            }
        }

        if recursive {
            for sub in subdirs {
                collect_gz_entries(
                    &sub,
                    root,
                    recursive,
                    ignore_dirs,
                    output_prefix,
                    tracked,
                    entries,
                );
            }
        }
    }

    pub fn escape_ascii_json(s: &str) -> String {
        let mut out = String::with_capacity(s.len());
        for c in s.chars() {
            let code = c as u32;
            if code > 0x7F {
                if code <= 0xFFFF {
                    use std::fmt::Write;
                    let _ = write!(out, "\\u{:04x}", code);
                } else {
                    let n = code - 0x10000;
                    let hi = 0xD800 + (n >> 10);
                    let lo = 0xDC00 + (n & 0x3FF);
                    use std::fmt::Write;
                    let _ = write!(out, "\\u{:04x}\\u{:04x}", hi, lo);
                }
            } else {
                out.push(c);
            }
        }
        out
    }

    pub fn generate_manifest(
        root: &Path,
        target_dir: &str,
        output_file: &str,
        recursive: bool,
        tracked: Option<&HashSet<String>>,
    ) -> Result<Vec<u8>, String> {
        let ignore_dirs: HashSet<&str> = IGNORE_DIRS.iter().copied().collect();
        let target_path = if target_dir == "." {
            root.to_path_buf()
        } else {
            root.join(target_dir)
        };

        if !target_path.exists() {
            return Err(format!("Target directory does not exist: {target_dir}"));
        }

        let output_basename = Path::new(output_file)
            .file_name()
            .and_then(|s| s.to_str())
            .unwrap_or("")
            .replace(".tmp", "");

        let mut entries = Vec::new();

        if output_file.ends_with(".gz") {
            collect_gz_entries(
                &target_path,
                root,
                recursive,
                &ignore_dirs,
                &output_basename,
                tracked,
                &mut entries,
            );

            let manifest = Manifest {
                source_directory: target_dir.to_string(),
                entries,
            };

            let json_str = serde_json::to_string_pretty(&manifest)
                .map_err(|e| format!("Failed to format JSON: {e}"))?;
            let escaped = escape_ascii_json(&json_str);

            let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
            encoder
                .write_all(escaped.as_bytes())
                .map_err(|e| format!("Failed to gzip manifest: {e}"))?;
            encoder
                .finish()
                .map_err(|e| format!("Failed to finalize gzip: {e}"))
        } else {
            collect_json_entries(
                &target_path,
                root,
                recursive,
                &ignore_dirs,
                &output_basename,
                tracked,
                &mut entries,
            );

            let manifest = Manifest {
                source_directory: target_dir.to_string(),
                entries,
            };

            let json_str = serde_json::to_string(&manifest)
                .map_err(|e| format!("Failed to format JSON: {e}"))?;
            let escaped = escape_ascii_json(&json_str);

            Ok(escaped.into_bytes())
        }
    }

    pub fn run_ai_manifest(
        root: &Path,
        check: bool,
        json_format: bool,
    ) -> Result<(), (String, i32)> {
        if !root.is_dir() {
            return Err((format!("Repository root not found: {}", root.display()), 1));
        }

        let tracked = get_tracked_files(root).map_err(|e| (e, 1))?;
        let mut has_drift = false;
        let mut first_error = None;

        for &(target_dir, output_file, recursive) in TARGETS {
            let target_path = if target_dir == "." {
                root.to_path_buf()
            } else {
                root.join(target_dir)
            };

            if !target_path.exists() {
                continue;
            }

            // Only GATE manifests that are actually committed in git
            if check && tracked.as_ref().is_some_and(|k| !k.contains(output_file)) {
                continue;
            }

            let full_output_path = root.join(output_file);

            let generated_bytes =
                match generate_manifest(root, target_dir, output_file, recursive, tracked.as_ref())
                {
                    Ok(b) => b,
                    Err(e) => {
                        return Err((e, 2));
                    }
                };

            if check {
                if !full_output_path.exists() {
                    eprintln!("[generate-ai-manifest] Missing manifest file: {output_file}");
                    has_drift = true;
                    if first_error.is_none() {
                        first_error = Some(format!("Missing manifest file: {output_file}"));
                    }
                    continue;
                }

                let matches = if output_file.ends_with(".gz") {
                    // For gz, decompress and compare strings
                    let file = match fs::File::open(&full_output_path) {
                        Ok(f) => f,
                        Err(e) => {
                            eprintln!("[generate-ai-manifest] Cannot read {output_file}: {e}");
                            has_drift = true;
                            continue;
                        }
                    };
                    let mut d1 = String::new();
                    let mut d2 = String::new();
                    let _ = GzDecoder::new(file).read_to_string(&mut d1);
                    let _ = GzDecoder::new(&generated_bytes[..]).read_to_string(&mut d2);
                    d1 == d2
                } else {
                    let existing_bytes = match fs::read(&full_output_path) {
                        Ok(b) => b,
                        Err(e) => {
                            eprintln!("[generate-ai-manifest] Cannot read {output_file}: {e}");
                            has_drift = true;
                            continue;
                        }
                    };
                    existing_bytes == generated_bytes
                };

                if !matches {
                    eprintln!("[generate-ai-manifest] Manifest drift detected: {output_file}");

                    // Surface diff diagnostics matching Python
                    if !output_file.ends_with(".gz") {
                        if let (Ok(v1), Ok(v2)) = (
                            serde_json::from_slice::<Value>(
                                &fs::read(&full_output_path).unwrap_or_default(),
                            ),
                            serde_json::from_slice::<Value>(&generated_bytes),
                        ) {
                            let e1: HashSet<String> = v1
                                .get("entries")
                                .and_then(|e| e.as_array())
                                .map(|arr| {
                                    arr.iter()
                                        .filter_map(|x| {
                                            x.get("path")
                                                .and_then(|p| p.as_str())
                                                .map(|s| s.to_string())
                                        })
                                        .collect()
                                })
                                .unwrap_or_default();
                            let e2: HashSet<String> = v2
                                .get("entries")
                                .and_then(|e| e.as_array())
                                .map(|arr| {
                                    arr.iter()
                                        .filter_map(|x| {
                                            x.get("path")
                                                .and_then(|p| p.as_str())
                                                .map(|s| s.to_string())
                                        })
                                        .collect()
                                })
                                .unwrap_or_default();

                            let mut only1: Vec<&String> = e1.difference(&e2).collect();
                            let mut only2: Vec<&String> = e2.difference(&e1).collect();
                            only1.sort();
                            only2.sort();

                            if !only1.is_empty() {
                                let preview = if only1.len() > 8 {
                                    &only1[..8]
                                } else {
                                    &only1[..]
                                };
                                eprintln!(
                                    "    committed-only entries ({}): {:?}",
                                    only1.len(),
                                    preview
                                );
                            }
                            if !only2.is_empty() {
                                let preview = if only2.len() > 8 {
                                    &only2[..8]
                                } else {
                                    &only2[..]
                                };
                                eprintln!(
                                    "    regenerated-only entries ({}): {:?}",
                                    only2.len(),
                                    preview
                                );
                            }
                        }
                    }

                    has_drift = true;
                    if first_error.is_none() {
                        first_error = Some(format!("Manifest drift detected: {output_file}"));
                    }
                }
            } else {
                if let Some(parent) = full_output_path.parent() {
                    if !parent.exists() {
                        let _ = fs::create_dir_all(parent);
                    }
                }
                if let Err(e) = fs::write(&full_output_path, &generated_bytes) {
                    return Err((
                        format!("Failed to write {}: {}", full_output_path.display(), e),
                        1,
                    ));
                }
                if !json_format {
                    println!("Generated {output_file}");
                }
            }
        }

        if check {
            if has_drift {
                return Err((
                    first_error.unwrap_or_else(|| "Manifest drift detected".to_string()),
                    1,
                ));
            }
            if !json_format {
                println!("[OK] AI repository and tool manifests are in sync");
            }
        }

        Ok(())
    }
}

pub mod pipe_boundaries {
    use regex::Regex;
    use serde::Serialize;
    use std::collections::BTreeMap;
    use std::fs;
    use std::path::{Path, PathBuf};
    use walkdir::WalkDir;

    #[derive(Serialize, Debug, Clone)]
    pub struct ModuleEntry {
        pub configure_kwargs: Vec<String>,
        pub public_symbols: Vec<String>,
    }

    #[derive(Serialize, Debug, Clone)]
    pub struct Manifest {
        pub modules: BTreeMap<String, ModuleEntry>,
    }

    #[derive(Debug, Clone)]
    pub struct PipeBoundaryResult {
        pub modules_count: usize,
    }

    pub fn extract_module_boundaries(content: &str) -> (Vec<String>, Vec<String>) {
        let mut config_kwargs = Vec::new();

        // 1. Parse def configure(...)
        let configure_re =
            Regex::new(r"(?s)def configure\s*\((.*?)\)(?:\s*->\s*[^:]+)?\s*:").unwrap();
        if let Some(caps) = configure_re.captures(content) {
            let args_str = caps.get(1).map(|m| m.as_str()).unwrap_or("");
            let kw_re = Regex::new(r"(?s)(?:^|,)\s*\*(?:[a-zA-Z_0-9]+)?\s*,\s*(.*)").unwrap();
            if let Some(kw_caps) = kw_re.captures(args_str) {
                let raw_kw = kw_caps.get(1).map(|m| m.as_str()).unwrap_or("");
                let mut parts = Vec::new();
                let mut depth = 0;
                let mut cur = String::new();
                for ch in raw_kw.chars() {
                    match ch {
                        '(' | '[' | '{' => {
                            depth += 1;
                            cur.push(ch);
                        }
                        ')' | ']' | '}' => {
                            depth -= 1;
                            cur.push(ch);
                        }
                        ',' if depth == 0 => {
                            let trimmed = cur.trim();
                            if !trimmed.is_empty() {
                                parts.push(trimmed.to_string());
                            }
                            cur.clear();
                        }
                        _ => cur.push(ch),
                    }
                }
                let trimmed = cur.trim();
                if !trimmed.is_empty() {
                    parts.push(trimmed.to_string());
                }

                let ident_re = Regex::new(r"^([a-zA-Z_][a-zA-Z0-9_]*)").unwrap();
                for p in parts {
                    if p.starts_with("**") {
                        continue;
                    }
                    if let Some(icaps) = ident_re.captures(&p) {
                        config_kwargs.push(icaps[1].to_string());
                    }
                }
            }
        }
        config_kwargs.sort();

        // 2. Parse public_symbols (functions and classes not starting with _)
        let mut public_symbols = Vec::new();
        let def_re = Regex::new(r"^\s*def\s+([a-zA-Z_][a-zA-Z0-9_]*)\s*\(").unwrap();
        let cls_re = Regex::new(r"^\s*class\s+([a-zA-Z_][a-zA-Z0-9_]*)\b").unwrap();

        let mut in_multiline_str: Option<&str> = None;
        for line in content.lines() {
            let sline = line.trim();

            if let Some(delim) = in_multiline_str {
                if sline.contains(delim) {
                    in_multiline_str = None;
                }
                continue;
            }

            if sline.contains("\"\"\"") && sline.matches("\"\"\"").count() % 2 == 1 {
                in_multiline_str = Some("\"\"\"");
                continue;
            }
            if sline.contains("'''") && sline.matches("'''").count() % 2 == 1 {
                in_multiline_str = Some("'''");
                continue;
            }

            if let Some(caps) = def_re.captures(line) {
                let name = &caps[1];
                if name != "configure" && !name.starts_with('_') {
                    public_symbols.push(name.to_string());
                }
            } else if let Some(caps) = cls_re.captures(line) {
                let name = &caps[1];
                if !name.starts_with('_') {
                    public_symbols.push(name.to_string());
                }
            }
        }
        public_symbols.sort();

        (config_kwargs, public_symbols)
    }

    pub fn render_manifest(root: &Path) -> Result<(String, usize), String> {
        let pipe_dir = root.join("usr/lib/mios/agent-pipe/mios_pipe");
        if !pipe_dir.is_dir() {
            return Err(format!(
                "[gen-pipe-boundary-manifest] agent-pipe directory not found: {:?}",
                pipe_dir
            ));
        }

        let mut files: Vec<(String, PathBuf)> = Vec::new();
        for entry in WalkDir::new(&pipe_dir)
            .into_iter()
            .filter_map(|e| e.ok())
            .filter(|e| e.file_type().is_file())
        {
            let path = entry.path();
            let fname = path.file_name().and_then(|n| n.to_str()).unwrap_or("");
            if fname.ends_with(".py") && !fname.starts_with("test_") {
                let rel = path
                    .strip_prefix(root)
                    .map_err(|e| format!("Strip prefix failed: {}", e))?;
                let rel_str = rel.to_string_lossy().replace('\\', "/");
                files.push((rel_str, path.to_path_buf()));
            }
        }
        files.sort_by(|a, b| a.0.cmp(&b.0));

        let mut manifest = Manifest {
            modules: BTreeMap::new(),
        };

        for (rel_path, abs_path) in files {
            let content = fs::read_to_string(&abs_path)
                .map_err(|e| format!("Failed to read {}: {}", abs_path.display(), e))?;
            let (configure_kwargs, public_symbols) = extract_module_boundaries(&content);
            if !configure_kwargs.is_empty() || !public_symbols.is_empty() {
                manifest.modules.insert(
                    rel_path,
                    ModuleEntry {
                        configure_kwargs,
                        public_symbols,
                    },
                );
            }
        }

        let modules_count = manifest.modules.len();
        let mut rendered = serde_json::to_string_pretty(&manifest)
            .map_err(|e| format!("Serialization error: {}", e))?;
        rendered.push('\n');

        Ok((rendered, modules_count))
    }

    fn normalize_newlines(s: &str) -> String {
        s.replace("\r\n", "\n")
    }

    pub fn run_pipe_boundaries(root: &Path, check: bool) -> Result<PipeBoundaryResult, String> {
        let out_json = root.join("usr/share/mios/pipe-boundaries.manifest.json");
        let (rendered, modules_count) = render_manifest(root)?;

        if check {
            if !out_json.exists() {
                return Err(format!("MISSING {}", out_json.display()));
            }

            let committed = fs::read_to_string(&out_json)
                .map_err(|e| format!("Failed to read {}: {}", out_json.display(), e))?;

            if normalize_newlines(&committed) != normalize_newlines(&rendered) {
                return Err(format!(
                    "[gen-pipe-boundary-manifest] STALE: {} does not match the tree",
                    out_json.display()
                ));
            }

            Ok(PipeBoundaryResult { modules_count })
        } else {
            if let Some(parent) = out_json.parent() {
                fs::create_dir_all(parent).map_err(|e| {
                    format!("Failed to create directory {}: {}", parent.display(), e)
                })?;
            }

            fs::write(&out_json, rendered.as_bytes())
                .map_err(|e| format!("Failed to write {}: {}", out_json.display(), e))?;

            Ok(PipeBoundaryResult { modules_count })
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_extract_module_boundaries_simple() {
            let content = r#"
class Foo:
    def __init__(self):
        pass
    def bar(self):
        pass

def _private_func():
    pass

def public_func():
    pass

def configure(*, alpha=None, beta=123):
    pass
"#;
            let (kwargs, symbols) = extract_module_boundaries(content);
            assert_eq!(kwargs, vec!["alpha", "beta"]);
            assert_eq!(symbols, vec!["Foo", "bar", "public_func"]);
        }
    }
}
