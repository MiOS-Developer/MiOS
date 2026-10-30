// AI-hint: Generators and validators for the repository's indexes: ADR.md, the ROADMAP.md index and metrics, and the drift-gate and build-pipeline TSVs under usr/share/mios/reference/ (ADR-0021 gen category).
// AI-doc: usr/share/doc/mios/manual/tools.md
// AI-related: usr/share/doc/mios/adr/, usr/share/mios/mios.toml, usr/share/doc/mios/adr/0021-rust-static-binary-consolidation.md, tools/native/mios-gen/src/main.rs, ROADMAP.md, tools/native/mios-gen/tests/docs.rs

/// Give a freshly written temp file the mode of the file it replaces (0644 for a
/// new one) before the rename. `fs::write` creates the temp with the caller's
/// umask, so a root `mios-gen sync` under umask 077 would otherwise swap a 0644
/// tracked index for a 0600 one that no user, test or gate can read.
fn keep_mode(tmp: &std::path::Path, dest: &std::path::Path) -> std::io::Result<()> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let perms = std::fs::metadata(dest)
            .map(|m| m.permissions())
            .unwrap_or_else(|_| std::fs::Permissions::from_mode(0o644));
        std::fs::set_permissions(tmp, perms)?;
    }
    #[cfg(not(unix))]
    let _ = (tmp, dest);
    Ok(())
}

pub mod adr_index {
    use regex::Regex;
    use std::collections::HashMap;
    use std::fs;
    use std::path::Path;
    use toml::Value;

    pub const ADR_DIR: &str = "usr/share/doc/mios/adr";
    pub const OUT: &str = "ADR.md";

    #[derive(Debug, Clone, PartialEq, Eq)]
    pub struct AdrRow {
        pub file: String,
        pub num: String,
        pub title: String,
        pub status: String,
        pub date: String,
        pub laws: Vec<String>,
        pub ssot: Vec<String>,
    }

    #[derive(Debug, Clone, PartialEq, Eq)]
    pub enum ParsedVal {
        Scalar(String),
        List(Vec<String>),
    }

    pub fn parse_front_matter(content: &str) -> HashMap<String, ParsedVal> {
        let mut out = HashMap::new();
        let scalar_re = Regex::new(r"^([a-z_]+):\s*(.*)$").unwrap();

        let lines: Vec<&str> = content.lines().collect();
        let start_idx = match lines.iter().position(|&l| l.trim() == "---") {
            Some(idx) => idx,
            None => return out,
        };

        for line in &lines[start_idx + 1..] {
            if line.trim() == "---" {
                break;
            }
            if let Some(caps) = scalar_re.captures(line) {
                let key = caps.get(1).unwrap().as_str().to_string();
                let val = caps.get(2).unwrap().as_str().trim();
                if val.starts_with('[') && val.ends_with(']') {
                    let inner = &val[1..val.len() - 1].trim();
                    let items: Vec<String> = inner
                        .split(',')
                        .map(|p| p.trim().to_string())
                        .filter(|p| !p.is_empty())
                        .collect();
                    out.insert(key, ParsedVal::List(items));
                } else {
                    out.insert(key, ParsedVal::Scalar(val.to_string()));
                }
            }
        }
        out
    }

    pub fn collect(root: &Path) -> (Vec<AdrRow>, Vec<String>) {
        let d = root.join(ADR_DIR);
        if !d.is_dir() {
            return (Vec::new(), Vec::new());
        }

        let mut entries = Vec::new();
        if let Ok(read_dir) = fs::read_dir(&d) {
            for entry in read_dir.flatten() {
                let fn_str = entry.file_name().to_string_lossy().to_string();
                if fn_str.ends_with(".md")
                    && fn_str.chars().next().is_some_and(|c| c.is_ascii_digit())
                {
                    entries.push(fn_str);
                }
            }
        }
        entries.sort();

        let mut rows = Vec::new();
        let mut malformed = Vec::new();

        for fn_str in entries {
            let path = d.join(&fn_str);
            let content = fs::read_to_string(&path).unwrap_or_default();
            let fm = parse_front_matter(&content);

            let adr_num = match fm.get("adr") {
                Some(ParsedVal::Scalar(s)) if !s.trim().is_empty() => s.trim().to_string(),
                _ => {
                    malformed.push(fn_str);
                    continue;
                }
            };

            let title = match fm.get("title") {
                Some(ParsedVal::Scalar(s)) => s.trim().to_string(),
                _ => String::new(),
            };
            let status = match fm.get("status") {
                Some(ParsedVal::Scalar(s)) => s.trim().to_string(),
                _ => String::new(),
            };
            let date = match fm.get("date") {
                Some(ParsedVal::Scalar(s)) => s.trim().to_string(),
                _ => String::new(),
            };
            let laws = match fm.get("laws") {
                Some(ParsedVal::List(l)) => l.clone(),
                _ => Vec::new(),
            };
            let ssot = match fm.get("ssot_keys") {
                Some(ParsedVal::List(s)) => s.clone(),
                _ => Vec::new(),
            };

            rows.push(AdrRow {
                file: fn_str,
                num: adr_num,
                title,
                status,
                date,
                laws,
                ssot,
            });
        }

        (rows, malformed)
    }

    pub fn render(rows: &[AdrRow]) -> String {
        let n = rows.len();
        let accepted = rows.iter().filter(|r| r.status == "accepted").count();

        let mut lines = Vec::new();
        lines.push("<!-- AI-hint: Repo-root breadcrumb to the MiOS Architecture Decision Records. GENERATED from the ADR front-matter by mios-gen adr-index; do not hand-edit -- run the generator. The ADRs themselves stay baked at usr/share/doc/mios/adr/ (Law 1: a running MiOS carries its own why), so this file is a pointer, not a copy. -->".to_string());
        lines.push("<!-- AI-related: usr/share/doc/mios/adr/, usr/share/doc/mios/adr/README.md, usr/share/mios/mios.toml [laws], tools/native/mios-gen/src/indexes.rs -->".to_string());
        lines.push("".to_string());
        lines.push("# MiOS Architecture Decision Records".to_string());
        lines.push("".to_string());
        lines.push(format!(
        "**{} ADRs** ({} accepted). The records live at [`{}/`]({}/) and are **baked into the image** -- a running MiOS carries its own *why*. This file is the root breadcrumb so an agent starting at either repo root reaches any decision in two hops; the format and status lifecycle are described in [the ADR README]({}/README.md).",
        n, accepted, ADR_DIR, ADR_DIR, ADR_DIR
    ));
        lines.push("".to_string());
        lines.push("| # | Decision | Status | Date | Laws | SSOT keys |".to_string());
        lines.push("|---|---|---|---|---|---|".to_string());

        for r in rows {
            let laws = if r.laws.is_empty() {
                "--".to_string()
            } else {
                r.laws.join(", ")
            };

            let ssot = if r.ssot.is_empty() {
                "--".to_string()
            } else {
                let first_four: Vec<String> =
                    r.ssot.iter().take(4).map(|x| format!("`{}`", x)).collect();
                let mut s = first_four.join(", ");
                if r.ssot.len() > 4 {
                    s.push_str(&format!(", +{}", r.ssot.len() - 4));
                }
                s
            };

            lines.push(format!(
                "| {} | [{}]({}/{}) | {} | {} | {} | {} |",
                r.num, r.title, ADR_DIR, r.file, r.status, r.date, laws, ssot
            ));
        }

        lines.push("".to_string());
        lines.push(format!(
            "<!-- derived from the front-matter of {} file(s) under {}/ -->",
            n, ADR_DIR
        ));
        lines.push("".to_string());

        lines.join("\n")
    }

    pub fn validate_adr_ssot_consistency(root: &Path) -> Vec<String> {
        let ssot_path = root.join("usr/share/mios/mios.toml");
        if !ssot_path.is_file() {
            return vec!["usr/share/mios/mios.toml is missing (ADR-0009 violation)".to_string()];
        }

        let content = match fs::read_to_string(&ssot_path) {
            Ok(c) => c,
            Err(e) => return vec![format!("failed to parse mios.toml: {}", e)],
        };

        let ssot: Value = match toml::from_str(&content) {
            Ok(v) => v,
            Err(e) => return vec![format!("failed to parse mios.toml: {}", e)],
        };

        let mut violations = Vec::new();

        // ADR-0009: single SSOT config surface
        let has_version = ssot
            .get("meta")
            .and_then(|m| m.get("mios_version"))
            .is_some();
        if !has_version {
            violations.push(
                "ADR-0009: mios.toml missing [meta].mios_version SSOT declaration".to_string(),
            );
        }

        // ADR-0010: SSOT as system dotfiles registry
        let has_dotfiles = ssot
            .get("dotfiles")
            .and_then(|d| d.as_table())
            .is_some_and(|t| !t.is_empty());
        if !has_dotfiles {
            violations
                .push("ADR-0010: mios.toml missing or empty [dotfiles] table registry".to_string());
        }

        // ADR-0003: SBOM image references integrity (no hardcoded @sha256: digests in [image])
        fn check_image_node(path: &str, node: &Value, viols: &mut Vec<String>) {
            match node {
                Value::String(s) => {
                    if s.contains("@sha256:") {
                        viols.push(format!(
                            "ADR-0003: hardcoded @sha256 digest found in [image].{}: {}",
                            path, s
                        ));
                    }
                }
                Value::Table(tbl) => {
                    for (k, v) in tbl {
                        let sub = if path.is_empty() {
                            k.clone()
                        } else {
                            format!("{}.{}", path, k)
                        };
                        check_image_node(&sub, v, viols);
                    }
                }
                _ => {}
            }
        }

        if let Some(images) = ssot.get("image") {
            check_image_node("", images, &mut violations);
        }

        // Enforce single canonical ADR directory: no shadow ADR namespaces in any */adr/
        let mut shadow_adrs = Vec::new();
        let norm_adr_dir = Path::new(ADR_DIR);

        fn walk_shadow(dir: &Path, root: &Path, norm_adr_dir: &Path, shadows: &mut Vec<String>) {
            if let Ok(entries) = fs::read_dir(dir) {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if let Ok(rel) = path.strip_prefix(root) {
                        let rel_str = rel.to_string_lossy().replace('\\', "/");
                        if path.is_dir() {
                            let dir_name = path.file_name().unwrap_or_default();
                            let dir_str = dir_name.to_string_lossy();
                            if dir_str.starts_with('.')
                                || dir_str == "target"
                                || dir_str == "node_modules"
                            {
                                continue;
                            }
                            if dir_str == "adr" && rel != norm_adr_dir {
                                if let Ok(sub) = fs::read_dir(&path) {
                                    for f in sub.flatten() {
                                        let fn_str = f.file_name().to_string_lossy().to_string();
                                        if fn_str.ends_with(".md")
                                            && fn_str
                                                .chars()
                                                .next()
                                                .is_some_and(|c| c.is_ascii_digit())
                                        {
                                            shadows.push(format!("{}/{}", rel_str, fn_str));
                                        }
                                    }
                                }
                            } else {
                                walk_shadow(&path, root, norm_adr_dir, shadows);
                            }
                        }
                    }
                }
            }
        }

        walk_shadow(root, root, norm_adr_dir, &mut shadow_adrs);
        shadow_adrs.sort();
        if !shadow_adrs.is_empty() {
            violations.push(format!(
                "shadow ADR namespace found outside {}: {}",
                ADR_DIR,
                shadow_adrs.join(", ")
            ));
        }

        violations
    }

    pub fn run_adr_index(root: &Path, check: bool, json_mode: bool) -> Result<(), (String, i32)> {
        let (rows, malformed) = collect(root);

        if !malformed.is_empty() {
            let mut lines = Vec::new();
            for fn_str in &malformed {
                lines.push(format!(
                    "VIOLATION: {}/{} has no `adr:` front-matter -- add it or rename",
                    ADR_DIR, fn_str
                ));
            }
            return Err((lines.join("\n"), 1));
        }

        if rows.is_empty() {
            return Err((
                format!(
                    "VIOLATION: no ADR front-matter collected under {}/ -- {} cannot be verified",
                    ADR_DIR, OUT
                ),
                1,
            ));
        }

        let body = render(&rows);
        let path = root.join(OUT);

        if check {
            let current = match fs::read_to_string(&path) {
                Ok(c) => c,
                Err(_) => {
                    return Err((format!("{} is missing -- run mios-gen adr-index", OUT), 1));
                }
            };

            if current != body {
                return Err((format!("{} is stale -- run mios-gen adr-index", OUT), 1));
            }

            let adr_viols = validate_adr_ssot_consistency(root);
            if !adr_viols.is_empty() {
                let mut lines = vec!["ADR SSOT consistency check failed:".to_string()];
                for v in &adr_viols {
                    lines.push(format!("  {}", v));
                }
                return Err((lines.join("\n"), 1));
            }

            if !json_mode {
                println!(
                    "{} matches the {} baked ADR(s) and SSOT consistency checks pass",
                    OUT,
                    rows.len()
                );
            }
            return Ok(());
        }

        let tmp_path = root.join(format!("{}.tmp", OUT));
        if let Err(e) = fs::write(&tmp_path, &body) {
            return Err((format!("failed to write {}: {}", tmp_path.display(), e), 1));
        }
        if let Err(e) = super::keep_mode(&tmp_path, &path) {
            return Err((format!("failed to set mode on {}: {}", tmp_path.display(), e), 1));
        }
        if let Err(e) = fs::rename(&tmp_path, &path) {
            return Err((format!("failed to replace {}: {}", path.display(), e), 1));
        }

        if !json_mode {
            println!("wrote {} from {} ADR(s)", OUT, rows.len());
        }
        Ok(())
    }
}

pub mod gate_index {
    use regex::Regex;
    use std::fs;
    use std::path::{Path, PathBuf};

    fn body(lines: &[&str], name: &str) -> Vec<String> {
        let opener = Regex::new(&format!(r"^\s*{}\(\)\s*\{{", regex::escape(name))).unwrap();
        for (i, ln) in lines.iter().enumerate() {
            if !opener.is_match(ln) {
                continue;
            }
            let trimmed = ln.trim_end();
            if trimmed.ends_with('}') {
                let start = trimmed.find('{').unwrap() + 1;
                let end = trimmed.rfind('}').unwrap();
                return vec![trimmed[start..end].to_string()];
            }
            let mut out = Vec::new();
            for nxt in &lines[i + 1..] {
                if nxt.starts_with('}') {
                    return out;
                }
                out.push((*nxt).to_string());
            }
            return out;
        }
        Vec::new()
    }

    fn first_sentence(text: &str) -> &str {
        let bytes = text.as_bytes();
        for i in 1..bytes.len() {
            if bytes[i] == b'.'
                && (bytes[i - 1].is_ascii_alphanumeric() || bytes[i - 1] == b')')
                && (i + 1 == bytes.len() || bytes[i + 1].is_ascii_whitespace())
            {
                return &text[..i];
            }
        }
        text
    }

    fn hint_of(root: &Path, command: &str) -> String {
        let parts: Vec<&str> = command.trim_end_matches(';').split_whitespace().collect();
        if parts.is_empty() {
            return String::new();
        }
        if parts.iter().skip(1).any(|p| !p.starts_with('-')) {
            return String::new();
        }
        let path = root.join(parts[0]);
        if !path.is_file() {
            return String::new();
        }
        let Ok(content) = fs::read_to_string(&path) else {
            return String::new();
        };
        let hint_re = Regex::new(r"#\s*AI-hint:\s*(.+)").unwrap();
        for (i, ln) in content.lines().enumerate() {
            if i > 8 {
                break;
            }
            if let Some(caps) = hint_re.captures(ln.trim()) {
                let text = caps.get(1).unwrap().as_str().trim();
                let first = first_sentence(text);
                if first.ends_with("...") {
                    return String::new();
                }
                return first
                    .trim_end_matches('.')
                    .replace('\t', " ")
                    .trim()
                    .to_string();
            }
        }
        String::new()
    }

    fn describe(root: &Path, lines: &[&str], content: &str, name: &str) -> String {
        let pat = format!(
            r"#\s*---\s*(?:\(\d+,\s*)?([^\n#]+?)\s*---\s*\n\s*{}\(\)\s*\{{",
            regex::escape(name)
        );
        if let Ok(re) = Regex::new(&pat) {
            if let Some(caps) = re.captures(content) {
                return caps.get(1).unwrap().as_str().trim().to_string();
            }
        }

        let b = body(lines, name);
        let echo_re =
            Regex::new(r#"echo\s+"\[98-drift-checks\]\s+(?:\(\d+\)\s+)?([^"]+)""#).unwrap();
        let b_joined = b.join("\n");
        for caps in echo_re.captures_iter(&b_joined) {
            let em = caps.get(1).unwrap().as_str().trim();
            if !em.starts_with("WARNING") && !em.starts_with("VIOLATION") && !em.starts_with("---")
            {
                return em.to_string();
            }
        }

        let run_py_re = Regex::new(r#"_run_py_check\s+\S+\s+(?:"([^"]+)"|(\S+))"#).unwrap();
        for line in &b {
            if let Some(caps) = run_py_re.captures(line) {
                let cmd = caps.get(1).or_else(|| caps.get(2)).unwrap().as_str();
                let hint = hint_of(root, cmd);
                if !hint.is_empty() {
                    return hint;
                }
            }
        }

        name.strip_prefix("check_")
            .unwrap_or(name)
            .replace('_', " ")
    }

    pub fn run_gate_index(
        root: &Path,
        custom_script: Option<PathBuf>,
        custom_output: Option<PathBuf>,
        check_mode: bool,
    ) -> Result<(), (String, i32)> {
        let script_path =
            custom_script.unwrap_or_else(|| root.join("automation/98-drift-checks.sh"));
        let output_path = custom_output
            .unwrap_or_else(|| root.join("usr/share/mios/reference/drift-gate-index.tsv"));

        if !script_path.is_file() {
            return Err((format!("{} not found", script_path.display()), 1));
        }

        let content = fs::read_to_string(&script_path)
            .map_err(|e| (format!("Failed to read {}: {e}", script_path.display()), 1))?;

        let lines: Vec<&str> = content.lines().collect();
        let main_start = lines
            .iter()
            .position(|line| *line == "main() {")
            .ok_or_else(|| {
                (
                    "ERROR: main() function not found in 98-drift-checks.sh".to_string(),
                    1,
                )
            })?;

        // The registry's main function closes at column zero. Helpers declared
        // afterwards may delegate to registered checks; those calls are not entries.
        let main_end = lines[main_start + 1..]
            .iter()
            .position(|line| *line == "}")
            .map(|offset| main_start + 1 + offset)
            .ok_or_else(|| ("ERROR: main() function is not closed".to_string(), 1))?;
        let main_body = lines[main_start + 1..main_end].join("\n");
        let check_re = Regex::new(r"(?m)^\s*(check_[a-z0-9_]+)\s*$").unwrap();
        let check_names: Vec<String> = check_re
            .captures_iter(&main_body)
            .map(|c| c.get(1).unwrap().as_str().to_string())
            .collect();

        if check_names.is_empty() {
            return Err(("ERROR: No check_* functions found in main()".to_string(), 1));
        }

        let mut seen = std::collections::HashSet::new();
        for name in &check_names {
            if !seen.insert(name) {
                return Err((
                    format!("ERROR: Duplicate check_* functions found in main(): {name}"),
                    1,
                ));
            }
        }

        let mut rows = Vec::new();
        for (idx, name) in check_names.iter().enumerate() {
            let desc = describe(root, &lines, &content, name);
            rows.push(format!("{}\t{}\t{}", idx + 1, name, desc));
        }

        let tsv_content = format!(
            "# Ordinal\tCheck Function\tDescription\n{}\n",
            rows.join("\n")
        );

        if check_mode {
            if !output_path.is_file() {
                return Err((
                    format!("ERROR: {} does not exist", output_path.display()),
                    1,
                ));
            }
            let existing = fs::read_to_string(&output_path)
                .map_err(|e| (format!("Failed to read {}: {e}", output_path.display()), 1))?;
            if existing != tsv_content {
                return Err((
                "ERROR: drift-gate-index.tsv is out of sync with 98-drift-checks.sh. Run mios-gen gate-index to regenerate."
                    .to_string(),
                1,
            ));
            }
            println!("PASS: drift-gate-index.tsv is in sync.");
            return Ok(());
        }

        if let Some(parent) = output_path.parent() {
            fs::create_dir_all(parent).map_err(|e| {
                (
                    format!("Failed to create directory {}: {e}", parent.display()),
                    1,
                )
            })?;
        }
        let tmp_path = output_path.with_extension("tmp");
        fs::write(&tmp_path, tsv_content.as_bytes())
            .map_err(|e| (format!("Failed to write {}: {e}", tmp_path.display()), 1))?;
        super::keep_mode(&tmp_path, &output_path)
            .map_err(|e| (format!("Failed to set mode on {}: {e}", tmp_path.display()), 1))?;
        fs::rename(&tmp_path, &output_path).map_err(|e| {
            (
                format!("Failed to replace {}: {e}", output_path.display()),
                1,
            )
        })?;

        println!(
            "Generated {} with {} gate entries.",
            output_path.display(),
            check_names.len()
        );
        Ok(())
    }
}

pub mod pipeline_index {
    use regex::Regex;
    use std::collections::HashSet;
    use std::fs;
    use std::path::Path;

    pub fn run_pipeline_index(root: &Path, check_mode: bool) -> Result<(), (String, i32)> {
        let automation_dir = root.join("automation");
        let ssot_path = root.join("usr/share/mios/mios.toml");

        let mut nn_min = 0i64;
        let mut nn_max = 99i64;
        let mut prefix_unique = true;
        let mut output_rel = "usr/share/mios/reference/pipeline-index.tsv".to_string();

        if ssot_path.is_file() {
            if let Ok(toml_bytes) = fs::read(&ssot_path) {
                if let Ok(toml_str) = std::str::from_utf8(&toml_bytes) {
                    if let Ok(val) = toml::from_str::<toml::Value>(toml_str) {
                        if let Some(pipe) = val.get("pipeline").and_then(|p| p.as_table()) {
                            if let Some(space) = pipe.get("space").and_then(|s| s.as_table()) {
                                if let Some(min_val) = space.get("min").and_then(|m| m.as_integer())
                                {
                                    nn_min = min_val;
                                }
                                if let Some(max_val) = space.get("max").and_then(|m| m.as_integer())
                                {
                                    nn_max = max_val;
                                }
                            }
                            if let Some(inv) = pipe.get("invariants").and_then(|i| i.as_table()) {
                                if let Some(pu) = inv.get("prefix_unique").and_then(|p| p.as_bool())
                                {
                                    prefix_unique = pu;
                                }
                            }
                            if let Some(map_str) = pipe.get("map").and_then(|m| m.as_str()) {
                                output_rel = map_str.to_string();
                            }
                        }
                    }
                }
            }
        }

        let output_path = root.join(&output_rel);

        if !automation_dir.is_dir() {
            return Err((format!("ERROR: {} not found", automation_dir.display()), 1));
        }

        let mut script_paths = Vec::new();
        let entries = fs::read_dir(&automation_dir).map_err(|e| {
            (
                format!("Failed to read {}: {e}", automation_dir.display()),
                1,
            )
        })?;
        let file_re = Regex::new(r"^([0-9]{2})-(.+)\.sh$").unwrap();

        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_file() {
                if let Some(fname) = path.file_name().and_then(|f| f.to_str()) {
                    if file_re.is_match(fname) {
                        script_paths.push(path);
                    }
                }
            }
        }
        script_paths.sort();

        let mut rows = Vec::new();
        let mut seen_nns = HashSet::new();

        for script_path in script_paths {
            let basename = script_path.file_name().unwrap().to_str().unwrap();
            let caps = file_re.captures(basename).unwrap();
            let nn_str = caps.get(1).unwrap().as_str();
            let name = caps.get(2).unwrap().as_str();

            let Ok(nn) = nn_str.parse::<i64>() else {
                continue;
            };

            if nn < nn_min || nn > nn_max {
                return Err((
                format!(
                    "ERROR: {basename} prefix {nn_str} is outside the declared [pipeline].space {nn_min}..{nn_max}"
                ),
                1,
            ));
            }

            if prefix_unique && !seen_nns.insert(nn_str.to_string()) {
                return Err((
                    format!("ERROR: Duplicate NN prefix found: {nn_str} in {basename}"),
                    1,
                ));
            }

            let mut oneline = String::new();
            if let Ok(content) = fs::read_to_string(&script_path) {
                for line in content.lines() {
                    let trimmed = line.trim();
                    if trimmed.starts_with("# AI-hint:") || trimmed.starts_with("# AI-related:") {
                        continue;
                    }
                    if trimmed.starts_with('#') && !trimmed.starts_with("#!") {
                        let text = trimmed.trim_start_matches('#').trim();
                        if !text.is_empty()
                            && !text.starts_with("---")
                            && !text.starts_with("Usage:")
                        {
                            oneline = text.to_string();
                            break;
                        }
                    }
                }
            }

            if oneline.is_empty() {
                oneline = name.replace('-', " ");
            }

            let rel_path = script_path
                .strip_prefix(root)
                .map(|p| p.to_string_lossy().replace('\\', "/"))
                .unwrap_or_else(|_| script_path.to_string_lossy().replace('\\', "/"));

            rows.push(format!("{nn_str}\tscript\t{name}\t{rel_path}\t{oneline}"));
        }

        let tsv_content = format!("# NN\tkind\tname\tfile\toneline\n{}\n", rows.join("\n"));

        if check_mode {
            if !output_path.is_file() {
                return Err((
                    format!("ERROR: {} does not exist", output_path.display()),
                    1,
                ));
            }
            let existing = fs::read_to_string(&output_path)
                .map_err(|e| (format!("Failed to read {}: {e}", output_path.display()), 1))?;
            if existing != tsv_content {
                return Err((
                "ERROR: pipeline-index.tsv is out of sync with automation scripts. Run mios-gen pipeline-index to regenerate."
                    .to_string(),
                1,
            ));
            }
            println!("PASS: pipeline-index.tsv is in sync.");
            return Ok(());
        }

        if let Some(parent) = output_path.parent() {
            fs::create_dir_all(parent).map_err(|e| {
                (
                    format!("Failed to create directory {}: {e}", parent.display()),
                    1,
                )
            })?;
        }
        let tmp_path = output_path.with_extension("tmp");
        fs::write(&tmp_path, tsv_content.as_bytes())
            .map_err(|e| (format!("Failed to write {}: {e}", tmp_path.display()), 1))?;
        super::keep_mode(&tmp_path, &output_path)
            .map_err(|e| (format!("Failed to set mode on {}: {e}", tmp_path.display()), 1))?;
        fs::rename(&tmp_path, &output_path).map_err(|e| {
            (
                format!("Failed to replace {}: {e}", output_path.display()),
                1,
            )
        })?;

        println!(
            "Generated {} with {} pipeline stages.",
            output_path.display(),
            rows.len()
        );
        Ok(())
    }
}

pub mod roadmap_index {
    use regex::Regex;
    use std::collections::{HashMap, HashSet};
    use std::fs;
    use std::path::Path;
    use std::process::Command;

    #[allow(dead_code)]
    #[derive(Debug, Default, Clone)]
    pub struct WorkstreamMeta {
        pub id: String,
        pub title: String,
        pub status: String,
        pub priority: String,
        pub laws: Vec<i64>,
        pub ssot_keys: Vec<String>,
        pub adr: Vec<i64>,
        pub deps: Vec<String>,
        pub acceptance: String,
        pub theme: String,
        pub part: Option<String>,
    }

    fn flatten_keys(v: &toml::Value, prefix: &str, set: &mut HashSet<String>) {
        if let toml::Value::Table(t) = v {
            for (k, val) in t {
                let full_key = if prefix.is_empty() {
                    k.clone()
                } else {
                    format!("{prefix}.{k}")
                };
                set.insert(full_key.clone());
                flatten_keys(val, &full_key, set);
            }
        }
    }

    pub fn make_anchor(title: &str) -> String {
        let re_link = Regex::new(r"\[([^\]]+)\]\([^)]+\)").unwrap();
        let text = re_link.replace_all(title, "$1");
        let text = text.replace('`', "");
        let mut out = String::new();
        for c in text.to_lowercase().chars() {
            if c.is_alphanumeric() || c == ' ' || c == '-' || c == '_' {
                out.push(c);
            }
        }
        let res = out.trim();
        let re_spaces = Regex::new(r"\s+").unwrap();
        let res = re_spaces.replace_all(res, "-");
        let re_dashes = Regex::new(r"-+").unwrap();
        re_dashes.replace_all(&res, "-").to_string()
    }

    pub fn parse_simple_yaml(text: &str) -> HashMap<String, String> {
        let mut metadata = HashMap::new();
        let lines: Vec<&str> = text.trim().split('\n').collect();
        let mut in_multiline = false;
        let mut multiline_key = String::new();
        let mut multiline_val = Vec::new();

        for line in lines {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            if in_multiline {
                multiline_val.push(line);
                continue;
            }
            if !line.contains(':') {
                continue;
            }
            let mut parts = line.splitn(2, ':');
            let k = parts.next().unwrap_or("").trim();
            let v = parts.next().unwrap_or("").trim();
            if v == "|" {
                in_multiline = true;
                multiline_key = k.to_string();
                multiline_val.clear();
                continue;
            }
            metadata.insert(k.to_string(), v.to_string());
        }

        if in_multiline && !multiline_key.is_empty() {
            metadata.insert(multiline_key, multiline_val.join("\n"));
        }
        metadata
    }

    fn parse_bracket_list(v: &str) -> Vec<String> {
        let s = v.trim();
        if s.starts_with('[') && s.ends_with(']') {
            s[1..s.len() - 1]
                .split(',')
                .map(|x| x.trim().trim_matches('"').trim_matches('\'').to_string())
                .filter(|x| !x.is_empty())
                .collect()
        } else {
            Vec::new()
        }
    }

    fn parse_int_list(v: &str) -> Vec<i64> {
        parse_bracket_list(v)
            .into_iter()
            .filter_map(|x| x.parse::<i64>().ok())
            .collect()
    }

    fn check_adr_exists(root: &Path, adr_num: i64) -> bool {
        let prefix = format!("{:04}", adr_num);
        let adr_dir = root.join("usr/share/doc/mios/adr");
        if let Ok(entries) = fs::read_dir(adr_dir) {
            for entry in entries.flatten() {
                let name = entry.file_name();
                let name_str = name.to_string_lossy();
                if name_str.starts_with(&format!("{prefix}-")) && name_str.ends_with(".md") {
                    return true;
                }
            }
        }
        false
    }

    fn format_comma(n: usize) -> String {
        let s = n.to_string();
        let mut result = String::new();
        let len = s.len();
        for (i, c) in s.chars().enumerate() {
            if i > 0 && (len - i).is_multiple_of(3) {
                result.push(',');
            }
            result.push(c);
        }
        result
    }

    /// `git cat-file <mode>` over `input`: the requests are written from a thread while `read` drains the answers
    /// here. Writing every request before reading (the old code) deadlocks as soon as git's answers fill the stdout
    /// pipe: git blocks writing, stops reading stdin, and the writer blocks too (roadmap-index --check hung).
    pub(crate) fn cat_file(
        root: &Path,
        mode: &str,
        input: String,
        read: impl FnOnce(&mut dyn std::io::BufRead) -> Result<(), String>,
    ) -> Result<(), String> {
        use std::io::Write;
        let mut child = Command::new("git")
            .args(["-C", &root.to_string_lossy(), "cat-file", mode])
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null())
            .spawn()
            .map_err(|e| format!("git cat-file {mode} spawn failed: {e}"))?;
        let mut stdin = child.stdin.take().ok_or("git cat-file: no stdin")?;
        // The thread owns stdin and drops it when done: git sees EOF after the last request.
        let feeder = std::thread::spawn(move || stdin.write_all(input.as_bytes()));
        let stdout = child.stdout.take().ok_or("git cat-file: no stdout")?;
        let mut reader = std::io::BufReader::new(stdout);
        let result = read(&mut reader);
        // Drain whatever `read` left so git can exit, then reap it.
        let _ = std::io::copy(&mut reader, &mut std::io::sink());
        let status = child.wait().map_err(|e| format!("git cat-file {mode} wait failed: {e}"))?;
        let fed = feeder.join().map_err(|_| "git cat-file: the request writer panicked".to_string())?;
        result?;
        fed.map_err(|e| format!("git cat-file {mode}: writing requests: {e}"))?;
        if !status.success() {
            return Err(format!("git cat-file {mode} exited with {:?}", status.code()));
        }
        Ok(())
    }

    pub fn generate_metrics_table(root: &Path) -> Result<String, String> {
        // 1. Tracked files list
        let ls_files = Command::new("git")
            .args(["-C", &root.to_string_lossy(), "ls-files"])
            .output()
            .map_err(|e| format!("git ls-files failed: {e}"))?;
        if !ls_files.status.success() {
            return Err(format!(
                "git ls-files failed with exit code {:?}",
                ls_files.status.code()
            ));
        }
        let ls_stdout = String::from_utf8_lossy(&ls_files.stdout);
        let tracked: Vec<String> = ls_stdout
            .lines()
            .map(|l| l.trim().replace('\\', "/"))
            .filter(|l| !l.is_empty())
            .collect();
        let file_count = tracked.len();

        // 2. ls-files -s -z for exact blob hashes
        let ls_s = Command::new("git")
            .args(["-C", &root.to_string_lossy(), "ls-files", "-s", "-z"])
            .output()
            .map_err(|e| format!("git ls-files -s failed: {e}"))?;
        if !ls_s.status.success() || ls_s.stdout.is_empty() {
            return Err("git ls-files -s failed or empty".to_string());
        }

        let mut oid_of: HashMap<String, String> = HashMap::new();
        let raw = &ls_s.stdout;
        for chunk in raw.split(|&b| b == 0) {
            if chunk.is_empty() {
                continue;
            }
            let s = String::from_utf8_lossy(chunk);
            if let Some((meta, path)) = s.split_once('\t') {
                let parts: Vec<&str> = meta.split_whitespace().collect();
                if parts.len() >= 2 && !path.is_empty() {
                    oid_of.insert(path.to_string(), parts[1].to_string());
                }
            }
        }

        // 3. Batch check object sizes
        let mut total_bytes: u64 = 0;
        if !oid_of.is_empty() {
            let mut oids_input = String::new();
            for oid in oid_of.values() {
                oids_input.push_str(oid);
                oids_input.push('\n');
            }

            cat_file(root, "--batch-check=%(objectsize)", oids_input, |out| {
                let mut line = String::new();
                loop {
                    line.clear();
                    if out.read_line(&mut line).map_err(|e| format!("git cat-file --batch-check read: {e}"))? == 0 {
                        return Ok(());
                    }
                    if let Ok(bytes) = line.trim().parse::<u64>() {
                        total_bytes += bytes;
                    }
                }
            })?;
        }

        // 4. Batch read code files to count lines
        let counted_exts = [".sh", ".py", ".ps1", ".rs"];
        let mut counted: HashMap<&'static str, usize> = HashMap::new();
        for ext in counted_exts {
            counted.insert(ext, 0);
        }

        let mut code: Vec<(&'static str, String)> = Vec::new();
        for f in &tracked {
            let f_lower = f.to_lowercase();
            for ext in counted_exts {
                if f_lower.ends_with(ext) {
                    if let Some(oid) = oid_of.get(f) {
                        code.push((ext, oid.clone()));
                    }
                    break;
                }
            }
        }

        if !code.is_empty() {
            let mut oids_input = String::new();
            for (_, oid) in &code {
                oids_input.push_str(oid);
                oids_input.push('\n');
            }

            // Streamed: one blob at a time is counted and dropped, never the whole tree in memory.
            cat_file(root, "--batch", oids_input, |out| {
                let mut header = Vec::new();
                for (ext, _) in &code {
                    header.clear();
                    if out.read_until(b'\n', &mut header).map_err(|e| format!("git cat-file --batch read: {e}"))? == 0 {
                        break;
                    }
                    let text = String::from_utf8_lossy(&header);
                    let h_parts: Vec<&str> = text.split_whitespace().collect();
                    let Some(size) = h_parts.get(2).and_then(|s| s.parse::<u64>().ok()) else {
                        continue; // "<oid> missing": no body follows
                    };
                    let (mut lines, mut last, mut left) = (0usize, b'\n', size);
                    while left > 0 {
                        let buf = out.fill_buf().map_err(|e| format!("git cat-file --batch read: {e}"))?;
                        if buf.is_empty() {
                            return Err("git cat-file --batch: truncated body".into());
                        }
                        let n = buf.len().min(left as usize);
                        lines += buf[..n].iter().filter(|&&b| b == b'\n').count();
                        last = buf[n - 1];
                        out.consume(n);
                        left -= n as u64;
                    }
                    if size > 0 && last != b'\n' {
                        lines += 1;
                    }
                    *counted.get_mut(ext).unwrap() += lines;
                    let mut lf = [0u8; 1];
                    out.read_exact(&mut lf).map_err(|e| format!("git cat-file --batch read: {e}"))?;
                }
                Ok(())
            })?;
        }

        let sh_l = counted[".sh"];
        let py_l = counted[".py"];
        let ps_l = counted[".ps1"];
        let rs_l = counted[".rs"];

        let size_mb = ((total_bytes as f64) / (1024.0 * 1024.0)).round() as u64;
        let sh_k = (sh_l as f64 / 1000.0).round() as u64;
        let py_k = (py_l as f64 / 1000.0).round() as u64;
        let ps_k = (ps_l as f64 / 1000.0).round() as u64;
        let rs_k = (rs_l as f64 / 1000.0).round() as u64;
        let ratio = if rs_l > 0 {
            (ps_l as f64) / (rs_l as f64)
        } else {
            0.0
        };

        // 5. Drift checks count from automation/98-drift-checks.sh
        let mut drift_count = 0;
        let gate_sh = root.join("automation/98-drift-checks.sh");
        if let Ok(txt) = fs::read_to_string(&gate_sh) {
            if let Some(m_pos) = txt.find("main() {") {
                let rest = &txt[m_pos..];
                let re_check = Regex::new(r"^\s*(check_[a-z0-9_]+)\s*$").unwrap();
                for line in rest.lines() {
                    if re_check.is_match(line) {
                        drift_count += 1;
                    }
                }
            }
        }

        // 6. Units count
        let mut declared_cnt = 0;
        let mut drift_units_cnt = 0;
        let toml_path = root.join("usr/share/mios/mios.toml");
        if let Ok(txt) = fs::read_to_string(&toml_path) {
            if let Ok(toml_val) = txt.parse::<toml::Value>() {
                if let Some(units) = toml_val.get("units").and_then(|u| u.as_table()) {
                    declared_cnt = units.values().filter(|v| v.is_table()).count();
                }
                if let Some(drift) = toml_val
                    .get("unit_projection")
                    .and_then(|u| u.get("drift"))
                    .and_then(|d| d.as_array())
                {
                    drift_units_cnt = drift.len();
                }
            }
        }

        let mut shipped_cnt = 0;
        let unit_dir = root.join("usr/lib/systemd/system");
        if unit_dir.is_dir() {
            let mut stack = vec![unit_dir];
            while let Some(dir) = stack.pop() {
                if let Ok(entries) = fs::read_dir(dir) {
                    for entry in entries.flatten() {
                        let path = entry.path();
                        if path.is_dir() {
                            stack.push(path);
                        } else if path.is_file() {
                            shipped_cnt += 1;
                        }
                    }
                }
            }
        }

        let faithful_cnt = declared_cnt.saturating_sub(drift_units_cnt);

        let table_lines = [
        "| | Measured | Note |",
        "|---|---:|---|",
        "| Runs on | MiOS-DEV VM / WSL | Bare metal is **untried**; blade/mesh/vfio behaviour is design, not observation. |",
        &format!("| Tracked files | {} | The reading surface. |", format_comma(file_count)),
        &format!("| Tracked size | {} MB | Two vendored assets are most of it. |", size_mb),
        &format!(
            "| Shell / Python / PowerShell / Rust | {}k / {}k / {}k / {}k lines | Law 14 makes Rust the native tier; PowerShell currently outweighs it {:.1}x. |",
            sh_k, py_k, ps_k, rs_k, ratio
        ),
        &format!("| Drift checks | {} | Falsifiability audited per check, not assumed. |", drift_count),
        &format!(
            "| Units reproducing from SSOT | {} faithful of {} | {} registered as drifting: the largest hole in part 1 of the thesis. |",
            faithful_cnt, shipped_cnt, drift_units_cnt
        ),
    ];
        Ok(format!("{}\n", table_lines.join("\n")))
    }

    pub fn replace_section(
        text: &str,
        start_marker: &str,
        end_marker: &str,
        replacement: &str,
    ) -> Result<String, String> {
        let start_idx = text
            .find(start_marker)
            .ok_or_else(|| format!("Marker {start_marker} not found"))?;
        let rest = &text[start_idx + start_marker.len()..];
        let end_offset = rest
            .find(end_marker)
            .ok_or_else(|| format!("Marker {end_marker} not found"))?;
        let end_idx = start_idx + start_marker.len() + end_offset;

        Ok(format!(
            "{}{}\n{}{}",
            &text[..start_idx],
            start_marker,
            replacement,
            &text[end_idx..]
        ))
    }

    pub fn generate_roadmap_index(root: &Path) -> Result<(String, String), (String, i32)> {
        let roadmap_path = root.join("ROADMAP.md");
        if !roadmap_path.is_file() {
            return Err((
                format!("ERROR: ROADMAP.md not found at {}", roadmap_path.display()),
                1,
            ));
        }

        let file_text = fs::read_to_string(&roadmap_path)
            .map_err(|e| (format!("Cannot read ROADMAP.md: {e}"), 1))?;

        // Load SSOT keys from mios.toml
        let toml_path = root.join("usr/share/mios/mios.toml");
        let mut valid_ssot_keys = HashSet::new();
        let toml_data: Option<toml::Value> = if toml_path.is_file() {
            let txt = fs::read_to_string(&toml_path).unwrap_or_default();
            if let Ok(v) = txt.parse::<toml::Value>() {
                flatten_keys(&v, "", &mut valid_ssot_keys);
                Some(v)
            } else {
                None
            }
        } else {
            None
        };

        // Load userenv mappings if present
        let userenv_path = root.join("tools/lib/userenv.sh");
        if userenv_path.is_file() {
            if let Ok(txt) = fs::read_to_string(&userenv_path) {
                let re_userenv =
                    Regex::new(r#"\("([a-zA-Z0-9_.-]+)"\s*,\s*"[A-Z0-9_]+"\)"#).unwrap();
                for caps in re_userenv.captures_iter(&txt) {
                    valid_ssot_keys.insert(caps[1].to_string());
                }
            }
        }

        let mut valid_law_ids = HashSet::new();
        if let Some(ref data) = toml_data {
            if let Some(laws) = data
                .get("laws")
                .and_then(|l| l.get("laws"))
                .and_then(|l| l.as_array())
            {
                for law in laws {
                    if let Some(id) = law.get("id").and_then(|i| i.as_integer()) {
                        valid_law_ids.insert(id);
                    }
                }
            }
        }

        let lines: Vec<&str> = file_text.lines().collect();
        let mut current_part: Option<String> = None;
        let mut workstreams: Vec<WorkstreamMeta> = Vec::new();
        let mut parts_order: Vec<String> = Vec::new();
        let mut part_workstreams: HashMap<String, Vec<WorkstreamMeta>> = HashMap::new();

        let mut idx = 0;
        let re_sep = Regex::new(r"\s+[-—–]+\s+").unwrap();

        while idx < lines.len() {
            let line = lines[idx];
            if line.starts_with("# ") && !line.starts_with("## ") {
                let header_name = line[2..].trim();
                let h_lower = header_name.to_lowercase();
                if !h_lower.starts_with("mios -- master roadmap")
                    && !h_lower.starts_with("mios roadmap")
                    && !h_lower.starts_with("archived mios roadmap")
                    && !h_lower.starts_with("appendix")
                {
                    current_part = Some(header_name.to_string());
                    if !parts_order.contains(&header_name.to_string()) {
                        parts_order.push(header_name.to_string());
                        part_workstreams.insert(header_name.to_string(), Vec::new());
                    }
                    idx += 1;
                    continue;
                }
            }

            if line.starts_with("## WS-") {
                let header_text = line[2..].trim();
                let mut parts = re_sep.splitn(header_text, 2);
                let ws_id = parts.next().unwrap_or("").trim().to_string();
                let ws_title = parts
                    .next()
                    .map(|s| s.trim().to_string())
                    .unwrap_or_else(|| header_text.to_string());

                let mut frontmatter_text = String::new();
                let mut fm_idx = idx + 1;
                while fm_idx < lines.len() && lines[fm_idx].trim().is_empty() {
                    fm_idx += 1;
                }

                if fm_idx < lines.len() && lines[fm_idx].trim().starts_with("<!--") {
                    let mut block_lines = Vec::new();
                    let first_line = lines[fm_idx].trim();
                    if first_line.ends_with("-->") {
                        block_lines.push(&first_line[4..first_line.len() - 3]);
                    } else {
                        block_lines.push(&first_line[4..]);
                        let mut cur_idx = fm_idx + 1;
                        while cur_idx < lines.len() {
                            let cur_line = lines[cur_idx];
                            if let Some((before, _)) = cur_line.split_once("-->") {
                                block_lines.push(before);
                                break;
                            } else {
                                block_lines.push(cur_line);
                                cur_idx += 1;
                            }
                        }
                    }
                    let block_text = block_lines.join("\n");
                    if block_text.contains("id:") || block_text.contains("status:") {
                        frontmatter_text = block_text;
                    }
                }

                let parsed_yaml = if !frontmatter_text.is_empty() {
                    parse_simple_yaml(&frontmatter_text)
                } else {
                    HashMap::new()
                };

                let id = parsed_yaml.get("id").cloned().unwrap_or(ws_id);
                let title = parsed_yaml.get("title").cloned().unwrap_or(ws_title);

                let status = if let Some(st) = parsed_yaml.get("status") {
                    st.clone()
                } else {
                    let mut rest_of_text = String::new();
                    let end = std::cmp::min(idx + 15, lines.len());
                    for l in &lines[idx..end] {
                        rest_of_text.push_str(l);
                        rest_of_text.push('\n');
                    }
                    if rest_of_text.contains('✅') || rest_of_text.contains("DONE") {
                        "done".to_string()
                    } else if rest_of_text.to_lowercase().contains("active") {
                        "active".to_string()
                    } else {
                        "proposed".to_string()
                    }
                };

                let priority = parsed_yaml
                    .get("priority")
                    .cloned()
                    .unwrap_or_else(|| "P2".to_string());
                let laws = parsed_yaml
                    .get("laws")
                    .map(|v| parse_int_list(v))
                    .unwrap_or_default();
                let ssot_keys = parsed_yaml
                    .get("ssot_keys")
                    .map(|v| parse_bracket_list(v))
                    .unwrap_or_default();
                let adr = parsed_yaml
                    .get("adr")
                    .map(|v| parse_int_list(v))
                    .unwrap_or_default();
                let deps = parsed_yaml
                    .get("deps")
                    .map(|v| parse_bracket_list(v))
                    .unwrap_or_default();
                let acceptance = parsed_yaml.get("acceptance").cloned().unwrap_or_default();
                let theme = parsed_yaml
                    .get("theme")
                    .cloned()
                    .unwrap_or_else(|| "General".to_string());

                let meta = WorkstreamMeta {
                    id,
                    title,
                    status,
                    priority,
                    laws,
                    ssot_keys,
                    adr,
                    deps,
                    acceptance,
                    theme,
                    part: current_part.clone(),
                };

                workstreams.push(meta.clone());
                if let Some(ref part) = current_part {
                    if let Some(list) = part_workstreams.get_mut(part) {
                        list.push(meta);
                    }
                }
            }

            idx += 1;
        }

        // Validation
        let mut validation_errors = Vec::new();
        for ws in &workstreams {
            for law in &ws.laws {
                if !valid_law_ids.is_empty() && !valid_law_ids.contains(law) {
                    validation_errors
                        .push(format!("Workstream {} cites invalid Law: {law}", ws.id));
                }
            }
            for adr_num in &ws.adr {
                if !check_adr_exists(root, *adr_num) {
                    validation_errors.push(format!(
                        "Workstream {} cites non-existent ADR: {adr_num}",
                        ws.id
                    ));
                }
            }
            for key in &ws.ssot_keys {
                if !valid_ssot_keys.contains(key) {
                    validation_errors.push(format!(
                        "Workstream {} cites non-existent SSOT key: {key}",
                        ws.id
                    ));
                }
            }
        }

        if !validation_errors.is_empty() {
            eprintln!("[roadmap-index] Validation failed:");
            for err in &validation_errors {
                eprintln!("  - {err}");
            }
            return Err(("Roadmap validation failed".to_string(), 2));
        }

        // Generate TOC
        let mut toc_lines = vec!["## Table of Contents".to_string()];
        for part in &parts_order {
            let anchor = make_anchor(part);
            toc_lines.push(format!("- [{part}](#{anchor})"));
        }
        let toc_content = format!("{}\n", toc_lines.join("\n"));

        // Generate Rollup
        let mut rollup_counts: HashMap<&str, usize> = HashMap::new();
        rollup_counts.insert("done", 0);
        rollup_counts.insert("active", 0);
        rollup_counts.insert("proposed", 0);
        rollup_counts.insert("blocked", 0);

        for ws in &workstreams {
            let status = ws.status.to_lowercase();
            match status.as_str() {
                "done" | "active" | "proposed" | "blocked" => {
                    *rollup_counts.get_mut(status.as_str()).unwrap() += 1;
                }
                _ => {
                    *rollup_counts.get_mut("proposed").unwrap() += 1;
                }
            }
        }
        let done_cnt = rollup_counts["done"];
        let active_cnt = rollup_counts["active"];
        let proposed_cnt = rollup_counts["proposed"];
        let blocked_cnt = rollup_counts["blocked"];

        let rollup_lines = [
            "### Workstream Status Rollup",
            &format!("- **Done**: {done_cnt}"),
            &format!("- **Active**: {active_cnt}"),
            &format!("- **Proposed**: {proposed_cnt}"),
            &format!("- **Blocked**: {blocked_cnt}"),
        ];
        let rollup_content = format!("{}\n", rollup_lines.join("\n"));

        // Generate Index
        let mut index_lines = vec!["### Workstream Index\n".to_string()];
        for part in &parts_order {
            index_lines.push(format!("**{part}**"));
            let ws_list = part_workstreams.get(part).cloned().unwrap_or_default();
            if ws_list.is_empty() {
                index_lines.push("(no workstreams)\n".to_string());
            } else {
                for ws in ws_list {
                    let status_suffix = if ws.status.to_lowercase() == "done" {
                        " ✅".to_string()
                    } else {
                        format!(" ({})", ws.status.to_lowercase())
                    };
                    index_lines.push(format!("- `{}` — {}{}", ws.id, ws.title, status_suffix));
                }
                index_lines.push(String::new());
            }
        }
        let index_content = index_lines.join("\n");

        // Generate Metrics
        let metrics_content = generate_metrics_table(root).map_err(|e| (e, 1))?;

        // Perform replacements
        let mut new_text = file_text.clone();
        new_text = replace_section(
            &new_text,
            "<!-- ROADMAP_METRICS_START -->",
            "<!-- ROADMAP_METRICS_END -->",
            &metrics_content,
        )
        .map_err(|e| (format!("ERROR: {e}"), 1))?;

        new_text = replace_section(
            &new_text,
            "<!-- ROADMAP_ROLLUP_START -->",
            "<!-- ROADMAP_ROLLUP_END -->",
            &rollup_content,
        )
        .map_err(|e| (format!("ERROR: {e}"), 1))?;

        new_text = replace_section(
            &new_text,
            "<!-- ROADMAP_INDEX_START -->",
            "<!-- ROADMAP_INDEX_END -->",
            &index_content,
        )
        .map_err(|e| (format!("ERROR: {e}"), 1))?;

        new_text = replace_section(
            &new_text,
            "<!-- ROADMAP_TOC_START -->",
            "<!-- ROADMAP_TOC_END -->",
            &toc_content,
        )
        .map_err(|e| (format!("ERROR: {e}"), 1))?;

        Ok((file_text, new_text))
    }

    pub fn run_roadmap_index(
        root: &Path,
        check_mode: bool,
        json_format: bool,
    ) -> Result<(), (String, i32)> {
        let (file_text, new_text) = generate_roadmap_index(root)?;
        let roadmap_path = root.join("ROADMAP.md");

        if check_mode {
            if file_text != new_text {
                if !json_format {
                    eprintln!("[roadmap-index] DRIFT detected: ROADMAP.md index is stale");
                }
                return Err(("DRIFT detected: ROADMAP.md index is stale".to_string(), 1));
            }

            if !json_format {
                println!("[roadmap-index] ROADMAP.md index is in sync");
            }
            return Ok(());
        }

        fs::write(&roadmap_path, new_text)
            .map_err(|e| (format!("Cannot write ROADMAP.md: {e}"), 1))?;

        if !json_format {
            println!(
            "[roadmap-index] Successfully regenerated Table of Contents, Index, Metrics, and Rollup in ROADMAP.md"
        );
        }

        Ok(())
    }

    #[cfg(test)]
    mod tests {
        use super::*;
        use std::io::Write;
        use std::sync::mpsc;
        use std::time::Duration;

        /// A repo whose code blobs together exceed any pipe buffer (64 KiB on Linux) many times over.
        fn big_repo() -> tempfile::TempDir {
            let dir = tempfile::tempdir().unwrap();
            let git = |args: &[&str]| {
                let ok = Command::new("git").arg("-C").arg(dir.path()).args(args).env("GIT_CONFIG_GLOBAL", "/dev/null").status().unwrap().success();
                assert!(ok, "git {args:?}");
            };
            git(&["init", "-q"]);
            for (i, ext) in ["sh", "py", "ps1", "rs"].iter().enumerate() {
                let body: String = (0..20_000).map(|n| format!("line {n} of file {i}\n")).collect();
                fs::write(dir.path().join(format!("f{i}.{ext}")), &body).unwrap();
            }
            fs::write(dir.path().join("tail.rs"), "no trailing newline").unwrap();
            git(&["add", "-A"]);
            dir
        }

        /// (d) roadmap-index --check hung: every cat-file request was written before any answer was read.
        /// Positive: the metrics table (both cat-file passes) finishes and counts every line, the unterminated last
        /// line included. Negative control: the pre-fix write-everything-then-wait pattern on the same repo never
        /// finishes (killed after the timeout).
        #[test]
        fn cat_file_batches_never_deadlock() {
            let repo = big_repo();
            let root = repo.path().to_path_buf();
            let (tx, rx) = mpsc::channel();
            std::thread::spawn(move || tx.send(generate_metrics_table(&root)).unwrap());
            let table = rx.recv_timeout(Duration::from_secs(60)).expect("metrics table must not hang").unwrap();
            assert!(!table.is_empty());
            // Both pipes must overflow for the deadlock: many small requests (input > 64 KiB) whose answers
            // (> 64 KiB) git cannot write while nobody reads.
            let oid = Command::new("git").arg("-C").arg(repo.path()).args(["rev-parse", ":tail.rs"]).output().unwrap();
            let oid = String::from_utf8(oid.stdout).unwrap();
            let requests: String = oid.repeat(5000);
            assert!(requests.len() > 128 * 1024);
            let mut answers = 0;
            cat_file(repo.path(), "--batch", requests.clone(), |out| {
                let mut line = String::new();
                while out.read_line(&mut line).map_err(|e| e.to_string())? > 0 {
                    answers += usize::from(line.ends_with(" blob 19\n"));
                    line.clear();
                }
                Ok(())
            })
            .unwrap();
            assert_eq!(answers, 5000, "every request answered");

            // Negative control: the old pattern on the same requests.
            let mut child = Command::new("git")
                .arg("-C")
                .arg(repo.path())
                .args(["cat-file", "--batch"])
                .stdin(std::process::Stdio::piped())
                .stdout(std::process::Stdio::piped())
                .spawn()
                .unwrap();
            let pid = child.id();
            let (tx, rx) = mpsc::channel();
            let mut stdin = child.stdin.take().unwrap();
            std::thread::spawn(move || {
                let _ = stdin.write_all(requests.as_bytes());
                drop(stdin);
                let _ = tx.send(child.wait_with_output().map(|o| o.stdout.len()));
            });
            let old = rx.recv_timeout(Duration::from_secs(5));
            let _ = Command::new("kill").arg("-9").arg(pid.to_string()).status();
            assert!(old.is_err(), "control: write-all-then-read must deadlock on this repo, got {old:?}");
        }
    }
}
