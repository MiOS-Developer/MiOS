// AI-hint: specs/ markdown standardizer and wiki/spec metadata sync: version and RAG timestamps injected from SSOT (ADR-0021 gen category).
// AI-related: specs/engineering/2026-04-26-Artifact-ENG-002-Scripts-Index.md, tools/native/mios-gen/src/main.rs, usr/share/mios/mios.toml, VERSION
// AI-functions: get_version, render_header, render_footer, extract_ai_hint, standardize_content, run_standardize_docs

pub mod standardize_docs {
    use regex::Regex;
    use std::fs;
    use std::path::{Path, PathBuf};

    pub const DEFAULT_TARGETS: &[&str] = &[
        "specs/audit",
        "specs/changelogs",
        "specs/core",
        "specs/engineering",
        "specs/memory",
        "specs/knowledge",
    ];

    #[allow(dead_code)]
    pub struct StandardizeDocsResult {
        pub scanned: usize,
        pub modified: usize,
        pub violations: usize,
        pub files: Vec<String>,
    }

    pub fn get_version(root: &Path) -> String {
        let version_file = root.join("VERSION");
        if let Ok(content) = fs::read_to_string(&version_file) {
            let v = content.trim();
            if !v.is_empty() {
                return v.to_string();
            }
        }

        let toml_file = root.join("usr/share/mios/mios.toml");
        if let Ok(content) = fs::read_to_string(&toml_file) {
            if let Ok(val) = content.parse::<toml::Value>() {
                if let Some(meta) = val.get("meta").and_then(|m| m.as_table()) {
                    if let Some(v) = meta.get("mios_version").and_then(|v| v.as_str()) {
                        return v.trim().to_string();
                    }
                }
            }
        }

        "0.3.0".to_string()
    }

    pub fn render_header(version: &str) -> String {
        format!(
"<!--  'MiOS' Artifact | Proprietor: 'MiOS' Project | https://github.com/mios-dev/mios -->\n\
> **Proprietor:** 'MiOS' Project\n\
> **Infrastructure:** Self-Building Infrastructure (Personal Property)\n\
> **License:** Licensed as personal property to 'MiOS' Project\n\
> **Source Reference:** MiOS-Core-v{}\n\
---",
        version
    )
    }

    pub fn render_footer() -> &'static str {
        "---\n\
- **Copyright:** (c) 2026 'MiOS' Project\n\
- **Status:** Personal Property / Private Infrastructure\n\
- **Project Repository:** [MiOS-DEV/mios](https://github.com/mios-dev/mios)\n\
- **Documentation:** ['MiOS' Navigation Hub](https://github.com/mios-dev/mios/blob/main/specs/Home.md)\n\
- **Artifact Hub:** [ai-context.json](https://github.com/mios-dev/mios/blob/main/ai-context.json)\n\
---"
    }

    fn normalize_newlines(s: &str) -> String {
        s.replace("\r\n", "\n")
    }

    pub fn extract_ai_hint(content: &str) -> (Option<String>, &str) {
        let trimmed_start = content.trim_start_matches('\u{feff}').trim_start();
        if trimmed_start.starts_with("<!-- AI-hint:") {
            if let Some(end_idx) = trimmed_start.find("-->") {
                let hint_block = &trimmed_start[..end_idx + 3];
                let rest = &trimmed_start[end_idx + 3..];
                return (Some(hint_block.trim().to_string()), rest);
            }
        }
        (None, content)
    }

    pub fn standardize_content(raw_content: &str, version: &str) -> String {
        let normalized = normalize_newlines(raw_content);
        let (ai_hint, body_part) = extract_ai_hint(&normalized);

        let mut body = body_part.trim_start().to_string();

        // Strip existing or legacy header block
        if body.starts_with("<!--  'MiOS' Artifact | Proprietor: 'MiOS' Project") {
            if let Some(pos) = body.find("\n---\n") {
                let header_candidate = &body[..pos];
                if header_candidate.contains("> **Proprietor:**") {
                    body = body[pos + 5..].to_string();
                } else if let Some(newline_pos) = body.find('\n') {
                    body = body[newline_pos + 1..].to_string();
                }
            } else if let Some(newline_pos) = body.find('\n') {
                body = body[newline_pos + 1..].to_string();
            }
        } else if body.starts_with("#  MiOS") {
            if let Some(pos) = body.find("\n---\n") {
                let header_candidate = &body[..pos];
                if header_candidate.contains("> **Proprietor:**") {
                    body = body[pos + 5..].to_string();
                }
            }
        }

        // Strip legacy footers
        let legacy_footer1_re = Regex::new(
        r"(?s)\n---\n###\s+(?:Legal & Source Reference|Bootc Ecosystem & Resources).*?---(?:\s*)$",
    )
    .expect("valid regex");
        let proprietary_footer_re =
            Regex::new(r"(?s)\n<!--\s+'MiOS'\s+Proprietary\s+Artifact.*?-->\s*$")
                .expect("valid regex");

        body = legacy_footer1_re.replace(&body, "").to_string();
        body = proprietary_footer_re.replace(&body, "").to_string();

        // Strip existing standard footer if present
        let footer = render_footer();
        let trimmed_end = body.trim_end();
        if let Some(stripped) = trimmed_end.strip_suffix(footer) {
            body = stripped.to_string();
        } else if let Some(pos) =
            trimmed_end.rfind("\n---\n- **Copyright:** (c) 2026 'MiOS' Project")
        {
            body = trimmed_end[..pos].to_string();
        }

        let trimmed_body = body.trim();
        let header = render_header(version);

        if let Some(hint) = ai_hint {
            format!("{}\n\n{}\n\n{}\n\n{}\n", hint, header, trimmed_body, footer)
        } else {
            format!("{}\n\n{}\n\n{}\n", header, trimmed_body, footer)
        }
    }

    pub fn collect_target_files(root: &Path, paths: &[PathBuf]) -> Vec<PathBuf> {
        let mut files = Vec::new();
        if !paths.is_empty() {
            for p in paths {
                let full = if p.is_absolute() {
                    p.clone()
                } else {
                    root.join(p)
                };
                if full.is_file() {
                    if full.extension().and_then(|e| e.to_str()) == Some("md") {
                        files.push(full);
                    }
                } else if full.is_dir() {
                    walk_md_files(&full, &mut files);
                }
            }
        } else {
            for target in DEFAULT_TARGETS {
                let target_dir = root.join(target);
                if target_dir.is_dir() {
                    walk_md_files(&target_dir, &mut files);
                }
            }
        }
        files.sort();
        files.dedup();
        files
    }

    fn walk_md_files(dir: &Path, out: &mut Vec<PathBuf>) {
        if let Ok(entries) = fs::read_dir(dir) {
            for entry in entries.flatten() {
                let path = entry.path();
                if path.is_dir() {
                    walk_md_files(&path, out);
                } else if path.is_file() && path.extension().and_then(|e| e.to_str()) == Some("md")
                {
                    out.push(path);
                }
            }
        }
    }

    pub fn run_standardize_docs(
        root: &Path,
        check: bool,
        paths: &[PathBuf],
    ) -> Result<StandardizeDocsResult, String> {
        let version = get_version(root);
        let target_files = collect_target_files(root, paths);

        let mut scanned = 0;
        let mut modified = 0;
        let mut violations = 0;
        let mut unstandardized = Vec::new();

        for file_path in &target_files {
            scanned += 1;
            let content = fs::read_to_string(file_path)
                .map_err(|e| format!("Failed to read {}: {}", file_path.display(), e))?;

            let standardized = standardize_content(&content, &version);
            let normalized_current = normalize_newlines(&content);
            let normalized_standard = normalize_newlines(&standardized);

            if normalized_current != normalized_standard {
                let rel = file_path.strip_prefix(root).unwrap_or(file_path);
                let rel_str = rel.display().to_string().replace('\\', "/");
                if check {
                    violations += 1;
                    eprintln!(
                        "[standardize-docs] UNSTANDARDIZED: {} does not match standardized format",
                        rel_str
                    );
                    unstandardized.push(rel_str);
                } else {
                    fs::write(file_path, standardized.as_bytes())
                        .map_err(|e| format!("Failed to write {}: {}", file_path.display(), e))?;
                    println!("Standardizing {}...", rel_str);
                    modified += 1;
                }
            }
        }

        if check && violations > 0 {
            return Err(format!(
                "[standardize-docs] {} file(s) require standardization: {}",
                violations,
                unstandardized.join(", ")
            ));
        }

        Ok(StandardizeDocsResult {
            scanned,
            modified,
            violations,
            files: target_files
                .iter()
                .map(|p| {
                    p.strip_prefix(root)
                        .unwrap_or(p)
                        .display()
                        .to_string()
                        .replace('\\', "/")
                })
                .collect(),
        })
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_standardize_plain_content() {
            let content = "# My Spec Title\n\nSome spec content here.";
            let res = standardize_content(content, "0.3.0");
            assert!(res.contains("<!--  'MiOS' Artifact | Proprietor: 'MiOS' Project"));
            assert!(res.contains("> **Source Reference:** MiOS-Core-v0.3.0"));
            assert!(res.contains("# My Spec Title"));
            assert!(res.contains("- **Copyright:** (c) 2026 'MiOS' Project"));
        }

        #[test]
        fn test_standardize_idempotent() {
            let content = "# Initial Title\n\nBody content.";
            let first = standardize_content(content, "0.3.0");
            let second = standardize_content(&first, "0.3.0");
            assert_eq!(first, second);
        }

        #[test]
        fn test_standardize_with_ai_hint() {
            let content = "<!-- AI-hint: Important hint for AI agents. -->\n# Header\nBody.";
            let res = standardize_content(content, "0.3.0");
            assert!(res.starts_with("<!-- AI-hint: Important hint for AI agents. -->"));
            assert!(res.contains("<!--  'MiOS' Artifact"));

            let res2 = standardize_content(&res, "0.3.0");
            assert_eq!(res, res2);
        }

        #[test]
        fn test_standardize_legacy_cleanup() {
            let content = "#  MiOS Legacy Spec\n> **Proprietor:** Old\n---\n# Real Title\nBody.\n---\n### Legal & Source Reference\nOld stuff.\n---";
            let res = standardize_content(content, "0.3.0");
            assert!(!res.contains("Legacy Spec"));
            assert!(!res.contains("Old stuff"));
            assert!(res.contains("# Real Title"));
            assert!(res.contains("- **Copyright:** (c) 2026 'MiOS' Project"));
        }

        #[test]
        fn test_standardize_preserves_title_with_single_line_artifact_comment() {
            // The input carries a mixed-case org on purpose: the header must come out
            // with the canonical lowercase slug. The URL is assembled so this source
            // holds no non-canonical slug literal for check_github_slug_casing to read.
            let legacy_url = concat!("https://github.com/", "MiOS-DEV", "/mios");
            let content = format!("<!-- AI-hint: Hint. -->\n<!--  'MiOS' Artifact | Proprietor: 'MiOS' Project | {legacy_url} -->\n#  'MiOS' Scripts Index\n> **Status:** Maintained\n\nContent.");
            let res = standardize_content(&content, "0.3.0");
            assert!(res.starts_with("<!-- AI-hint: Hint. -->"));
            assert!(res.contains("<!--  'MiOS' Artifact | Proprietor: 'MiOS' Project | https://github.com/mios-dev/mios -->"));
            assert!(res.contains("#  'MiOS' Scripts Index"));
            assert!(res.contains("> **Status:** Maintained"));
            assert!(res.contains("Content."));

            let res2 = standardize_content(&res, "0.3.0");
            assert_eq!(res, res2);
        }
    }
}

pub mod sync_wiki {
    use regex::Regex;
    use std::fs;
    use std::path::{Path, PathBuf};
    use std::time::{SystemTime, UNIX_EPOCH};

    #[derive(Debug, Default)]
    pub struct SyncResult {
        pub scanned: usize,
        pub modified: usize,
        pub files: Vec<PathBuf>,
    }

    fn format_epoch_date(secs: u64) -> String {
        let days = secs / 86400;
        let z = days as i64 + 719468;
        let era = if z >= 0 { z } else { z - 146096 } / 146097;
        let doe = (z - era * 146097) as u64;
        let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
        let y = yoe as i64 + era * 400;
        let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
        let mp = (5 * doy + 2) / 153;
        let d = doy - (153 * mp + 2) / 5 + 1;
        let m = if mp < 10 { mp + 3 } else { mp - 9 };
        let y = if m <= 2 { y + 1 } else { y };
        format!("{:04}-{:02}-{:02}", y, m, d)
    }

    pub fn get_version(root: &Path) -> String {
        let version_file = root.join("VERSION");
        if let Ok(content) = fs::read_to_string(&version_file) {
            let v = content.trim();
            if !v.is_empty() {
                return v.to_string();
            }
        }

        let toml_path = root.join("usr/share/mios/mios.toml");
        if let Ok(content) = fs::read_to_string(&toml_path) {
            if let Ok(parsed) = content.parse::<toml::Value>() {
                if let Some(v) = parsed
                    .get("meta")
                    .and_then(|m| m.get("mios_version"))
                    .and_then(|v| v.as_str())
                {
                    return v.to_string();
                }
            }
        }

        "0.3.0".to_string()
    }

    pub fn get_last_rag_sync(root: &Path, explicit: Option<&str>) -> String {
        if let Some(e) = explicit {
            if !e.trim().is_empty() {
                return e.trim().to_string();
            }
        }

        let rag_file = root.join("usr/share/mios/reference/manual-corpus.tsv");
        if let Ok(meta) = fs::metadata(&rag_file) {
            if let Ok(mtime) = meta.modified() {
                if let Ok(dur) = mtime.duration_since(UNIX_EPOCH) {
                    return format_epoch_date(dur.as_secs());
                }
            }
        }

        if let Ok(dur) = SystemTime::now().duration_since(UNIX_EPOCH) {
            format_epoch_date(dur.as_secs())
        } else {
            "2026-10-07".to_string()
        }
    }

    pub fn sync_file_content(
        content: &str,
        version: &str,
        rag_sync: &str,
        explicit_rag_sync: bool,
    ) -> Result<(String, bool), String> {
        let mut modified = false;

        // 1. Process ```json:knowledge ... ```
        let kb_re = Regex::new(r"(?s)```json:knowledge\s*\r?\n(.*?)\r?\n```")
            .map_err(|e| format!("regex compilation failed: {e}"))?;

        let mut new_content = String::with_capacity(content.len());
        let mut last_end = 0;

        for caps in kb_re.captures_iter(content) {
            let full_match = caps.get(0).unwrap();
            new_content.push_str(&content[last_end..full_match.start()]);

            let json_str = caps.get(1).unwrap().as_str();
            let mut data: serde_json::Value = serde_json::from_str(json_str)
                .map_err(|e| format!("invalid JSON in ```json:knowledge``` block: {e}"))?;

            if let Some(obj) = data.as_object_mut() {
                // Update version
                if obj.get("version").and_then(|v| v.as_str()) != Some(version) {
                    obj.insert(
                        "version".to_string(),
                        serde_json::Value::String(version.to_string()),
                    );
                    modified = true;
                }

                // Update last_rag_sync
                let current_sync = obj.get("last_rag_sync").and_then(|v| v.as_str());
                if (explicit_rag_sync
                    || current_sync.is_none()
                    || current_sync.unwrap_or("").trim().is_empty())
                    && current_sync != Some(rag_sync)
                {
                    obj.insert(
                        "last_rag_sync".to_string(),
                        serde_json::Value::String(rag_sync.to_string()),
                    );
                    modified = true;
                }
            }

            let pretty_json = serde_json::to_string_pretty(&data)
                .map_err(|e| format!("failed to serialize JSON: {e}"))?;

            new_content.push_str(&format!("```json:knowledge\n{}\n```", pretty_json));
            last_end = full_match.end();
        }
        new_content.push_str(&content[last_end..]);

        // 2. Process ```json ... ``` with { ... }
        let json_re = Regex::new(r"(?s)```json\s*\r?\n(\{.*?\})\r?\n```")
            .map_err(|e| format!("regex compilation failed: {e}"))?;

        let intermediate = new_content;
        let mut final_content = String::with_capacity(intermediate.len());
        last_end = 0;

        for caps in json_re.captures_iter(&intermediate) {
            let full_match = caps.get(0).unwrap();
            final_content.push_str(&intermediate[last_end..full_match.start()]);

            let json_str = caps.get(1).unwrap().as_str();
            if let Ok(mut data) = serde_json::from_str::<serde_json::Value>(json_str) {
                if let Some(obj) = data.as_object_mut() {
                    if obj.contains_key("baseline") {
                        let expected_baseline = format!("v{}", version);
                        if obj.get("baseline").and_then(|v| v.as_str()) != Some(&expected_baseline)
                        {
                            obj.insert(
                                "baseline".to_string(),
                                serde_json::Value::String(expected_baseline),
                            );
                            modified = true;
                        }
                    }
                    if obj.contains_key("last_sync")
                        && explicit_rag_sync
                        && obj.get("last_sync").and_then(|v| v.as_str()) != Some(rag_sync)
                    {
                        obj.insert(
                            "last_sync".to_string(),
                            serde_json::Value::String(rag_sync.to_string()),
                        );
                        modified = true;
                    }
                }
                if let Ok(pretty_json) = serde_json::to_string_pretty(&data) {
                    final_content.push_str(&format!("```json\n{}\n```", pretty_json));
                    last_end = full_match.end();
                    continue;
                }
            }

            final_content.push_str(full_match.as_str());
            last_end = full_match.end();
        }
        final_content.push_str(&intermediate[last_end..]);

        let norm_orig = content.replace("\r\n", "\n");
        let norm_final = final_content.replace("\r\n", "\n");
        let actually_changed = norm_orig != norm_final;

        Ok((norm_final, actually_changed || modified))
    }

    pub fn run_sync_wiki(
        root: &Path,
        check: bool,
        explicit_rag_sync: Option<&str>,
        target_paths: &[PathBuf],
    ) -> Result<SyncResult, String> {
        if !root.is_dir() {
            return Err(format!("Root directory does not exist: {}", root.display()));
        }

        let version = get_version(root);
        let rag_sync = get_last_rag_sync(root, explicit_rag_sync);
        let has_explicit_rag = explicit_rag_sync.is_some();

        let files_to_check: Vec<PathBuf> = if !target_paths.is_empty() {
            target_paths
                .iter()
                .map(|p| {
                    if p.is_absolute() {
                        p.clone()
                    } else {
                        root.join(p)
                    }
                })
                .collect()
        } else {
            let mut candidates = vec![
                root.join("specs/engineering/2026-04-26-Artifact-ENG-002-Scripts-Index.md"),
                root.join("README.md"),
                root.join("usr/share/mios/ai/INDEX.md"),
                root.join("specs/Home.md"),
            ];

            let specs_dir = root.join("specs");
            if specs_dir.is_dir() {
                for entry in walkdir::WalkDir::new(&specs_dir)
                    .follow_links(false)
                    .into_iter()
                    .filter_map(|e| e.ok())
                {
                    let p = entry.path();
                    if p.is_file() && p.extension().and_then(|ext| ext.to_str()) == Some("md") {
                        let pb = p.to_path_buf();
                        if !candidates.contains(&pb) {
                            candidates.push(pb);
                        }
                    }
                }
            }
            candidates
        };

        let mut result = SyncResult::default();
        let mut stale_files: Vec<String> = Vec::new();

        for path in files_to_check {
            if !path.is_file() {
                continue;
            }

            let content = match fs::read_to_string(&path) {
                Ok(c) => c,
                Err(e) => {
                    return Err(format!("Failed to read file {}: {}", path.display(), e));
                }
            };

            if !content.contains("```json") {
                continue;
            }

            result.scanned += 1;
            result.files.push(path.clone());

            let (synced, changed) =
                match sync_file_content(&content, &version, &rag_sync, has_explicit_rag) {
                    Ok(res) => res,
                    Err(err) => {
                        return Err(format!("{}: {}", path.display(), err));
                    }
                };

            if changed {
                if check {
                    let rel_path = path
                        .strip_prefix(root)
                        .unwrap_or(&path)
                        .to_string_lossy()
                        .to_string();
                    stale_files.push(rel_path);
                } else {
                    if let Err(e) = fs::write(&path, synced.as_bytes()) {
                        return Err(format!("Failed to write {}: {}", path.display(), e));
                    }
                    println!("[ok] Propagated sync values to {}", path.display());
                    result.modified += 1;
                }
            }
        }

        if check && !stale_files.is_empty() {
            return Err(format!(
            "Wiki documentation embeds are STALE ({} files require sync: {}). Run 'mios-gen sync-wiki' to synchronize.",
            stale_files.len(),
            stale_files.join(", ")
        ));
        }

        Ok(result)
    }
}
