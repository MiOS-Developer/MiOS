// AI-hint: Fast native static CLI enforcement gate for the NO-HARDCODE law (Architectural Law 7).
// AI-related: usr/libexec/mios/mios-hardcode-lint, automation/98-drift-checks.sh, usr/share/mios/mios.toml

use std::fs;
use std::path::{Component, Path, PathBuf};
use std::process::{Command, ExitCode};

use regex::Regex;
use walkdir::WalkDir;

const BOM: &[u8] = b"\xef\xbb\xbf";
const EXEMPT: &[&str] = &[
    "/var/lib/mios/",
    "/.git/",
    "/__pycache__/",
    "/node_modules/",
    "/usr/share/mios/knowledge/",
    "/.claude/",
    "/.agents/",
];
const UNIT_EXT: &[&str] = &[".service", ".socket", ".container", ".timer", ".conf"];
const PORT_IP_EXT: &[&str] = &[
    ".py",
    ".sh",
    ".bash",
    ".ps1",
    ".psm1",
    ".service",
    ".socket",
    ".container",
    ".timer",
    ".conf",
];
const CODE_EXT: &[&str] = &[
    ".py",
    ".sh",
    ".bash",
    ".toml",
    ".yml",
    ".yaml",
    ".ps1",
    ".psm1",
    ".service",
    ".socket",
    ".container",
    ".timer",
    ".conf",
];

struct Allowlist {
    exempt_files: Vec<Regex>,
    exempt_patterns: Vec<String>,
    exempt_patterns_rx: Vec<Regex>,
}

fn load_allowlist(roots: &[String]) -> Allowlist {
    let mut exempt_files = Vec::new();
    let mut exempt_patterns = Vec::new();
    let mut exempt_patterns_rx = Vec::new();

    for r in roots {
        let toml_path = Path::new(r).join("usr/share/mios/mios.toml");
        if toml_path.is_file() {
            if let Ok(content) = fs::read_to_string(&toml_path) {
                if let Ok(data) = toml::from_str::<toml::Value>(&content) {
                    if let Some(sec) = data.get("security").and_then(|s| s.get("nohc_allowlist")) {
                        if let Some(files) = sec.get("exempt_files").and_then(|f| f.as_array()) {
                            for f in files {
                                if let Some(pat) = f.as_str() {
                                    if let Ok(rx) = Regex::new(pat) {
                                        exempt_files.push(rx);
                                    }
                                }
                            }
                        }
                        if let Some(patterns) =
                            sec.get("exempt_patterns").and_then(|p| p.as_array())
                        {
                            for p in patterns {
                                if let Some(pat) = p.as_str() {
                                    let clean_pat = pat
                                        .replace(r"(?![0-9])", "")
                                        .replace(r"(?!\d)", "")
                                        .replace(r"(?<![0-9])", "")
                                        .replace(r"(?<!\d)", "");
                                    if let Ok(rx) = Regex::new(&clean_pat) {
                                        exempt_patterns.push(pat.to_string());
                                        exempt_patterns_rx.push(rx);
                                    }
                                }
                            }
                        }
                        break;
                    }
                }
            }
        }
    }

    Allowlist {
        exempt_files,
        exempt_patterns,
        exempt_patterns_rx,
    }
}

fn is_line_exempt(line: &str, allowlist: &Allowlist) -> bool {
    for (pat, rx) in allowlist
        .exempt_patterns
        .iter()
        .zip(&allowlist.exempt_patterns_rx)
    {
        for m in rx.find_iter(line) {
            let start = m.start();
            let end = m.end();
            let prefix_ok =
                if !pat.starts_with('^') && !pat.starts_with(r"\b") && !pat.starts_with("(?<!") {
                    start == 0
                        || !line[..start]
                            .chars()
                            .next_back()
                            .is_some_and(|c| c.is_ascii_digit())
                } else {
                    true
                };
            let suffix_ok = if !pat.ends_with('$') && !pat.ends_with(r"\b") {
                end == line.len()
                    || !line[end..]
                        .chars()
                        .next()
                        .is_some_and(|c| c.is_ascii_digit())
            } else {
                true
            };
            if prefix_ok && suffix_ok {
                return true;
            }
        }
    }
    false
}

fn truncate_chars(s: &str, max_chars: usize) -> &str {
    match s.char_indices().nth(max_chars) {
        Some((idx, _)) => &s[..idx],
        None => s,
    }
}

fn is_routable_ip(ip_str: &str) -> bool {
    if ip_str == "127.0.0.1" || ip_str == "0.0.0.0" {
        return false;
    }
    let parts: Vec<&str> = ip_str.split('.').collect();
    if parts.len() != 4 {
        return false;
    }
    let (p1, p2) = match (parts[0].parse::<u8>(), parts[1].parse::<u8>()) {
        (Ok(a), Ok(b)) => (a, b),
        _ => return false,
    };
    if p1 == 10 {
        return false;
    }
    if p1 == 172 && (16..=31).contains(&p2) {
        return false;
    }
    if p1 == 192 && p2 == 168 {
        return false;
    }
    if p1 == 100 && (64..=127).contains(&p2) {
        return false;
    }
    true
}

fn header_risk(raw: &[u8], filename: &str) -> Option<&'static str> {
    if filename.ends_with(".ps1") && !raw.starts_with(BOM) {
        let check_len = std::cmp::min(256, raw.len());
        if raw[..check_len].windows(3).any(|w| w == BOM) {
            return Some("UTF-8 BOM stranded in PS1 head (must be byte 0 -- breaks irm|iex parse)");
        }
    }
    if filename.ends_with(".sh") || filename.ends_with(".bash") {
        let head_str = String::from_utf8_lossy(raw);
        let head_lines: Vec<&str> = head_str.split('\n').take(40).collect();
        let sb: Vec<usize> = head_lines
            .iter()
            .enumerate()
            .filter(|(_, l)| l.starts_with("#!"))
            .map(|(i, _)| i)
            .collect();
        if !sb.is_empty() && sb[0] != 0 {
            return Some("shebang not on line 1 (a header/comment is above it)");
        }
    }
    None
}

fn comment_start(line: &str) -> Option<usize> {
    let mut in_s = false;
    let mut in_d = false;
    for (i, ch) in line.char_indices() {
        if ch == '\'' && !in_d {
            in_s = !in_s;
        } else if ch == '"' && !in_s {
            in_d = !in_d;
        } else if !in_s && !in_d && ch == '#' {
            if line.starts_with("#!") {
                return None;
            }
            return Some(i);
        }
    }
    None
}

struct PyToken {
    text: String,
    start_line: usize,
    end_line: usize,
    is_comment: bool,
    is_string: bool,
    is_triple: bool,
    is_docstring: bool,
}

fn tokenize_python(text: &str) -> Vec<PyToken> {
    let mut tokens = Vec::new();
    let bytes = text.as_bytes();
    let len = bytes.len();
    let mut i = 0;
    let mut line = 1;

    let mut module_docstring_allowed = true;
    let mut expect_suite_docstring = false;
    let mut paren_depth: usize = 0;
    let mut after_colon = false;

    while i < len {
        let b = bytes[i];

        // Whitespace and newlines
        if b == b'\n' {
            line += 1;
            if paren_depth == 0 && after_colon {
                expect_suite_docstring = true;
                after_colon = false;
            }
            i += 1;
            continue;
        }
        if b == b'\r' || b == b' ' || b == b'\t' {
            i += 1;
            continue;
        }

        // Comments
        if b == b'#' {
            let start_line = line;
            let start = i;
            while i < len && bytes[i] != b'\n' {
                i += 1;
            }
            let comment_text = &text[start..i];
            tokens.push(PyToken {
                text: comment_text.to_string(),
                start_line,
                end_line: line,
                is_comment: true,
                is_string: false,
                is_triple: false,
                is_docstring: false,
            });
            continue;
        }

        // Check for string literal (with optional prefix: r, u, f, b, rf, fr, etc.)
        let mut prefix_len = 0;
        let mut p = i;
        while p < len
            && (bytes[p] == b'r'
                || bytes[p] == b'R'
                || bytes[p] == b'u'
                || bytes[p] == b'U'
                || bytes[p] == b'f'
                || bytes[p] == b'F'
                || bytes[p] == b'b'
                || bytes[p] == b'B')
        {
            p += 1;
        }
        if p < len && (bytes[p] == b'\'' || bytes[p] == b'"') && p - i <= 3 {
            prefix_len = p - i;
        }

        let quote_pos = i + prefix_len;
        if quote_pos < len && (bytes[quote_pos] == b'\'' || bytes[quote_pos] == b'"') {
            let quote_char = bytes[quote_pos];
            let is_triple = quote_pos + 2 < len
                && bytes[quote_pos + 1] == quote_char
                && bytes[quote_pos + 2] == quote_char;

            let start_line = line;
            let start_idx = i;

            if is_triple {
                let delim = [quote_char, quote_char, quote_char];
                i = quote_pos + 3;
                while i < len {
                    if i + 3 <= len
                        && bytes[i] == delim[0]
                        && bytes[i + 1] == delim[1]
                        && bytes[i + 2] == delim[2]
                    {
                        i += 3;
                        break;
                    }
                    let ch = text[i..].chars().next().unwrap();
                    if ch == '\\' {
                        let ch_len = ch.len_utf8();
                        i += ch_len;
                        if i < len {
                            let esc = text[i..].chars().next().unwrap();
                            if esc == '\n' {
                                line += 1;
                            }
                            i += esc.len_utf8();
                        }
                        continue;
                    }
                    if ch == '\n' {
                        line += 1;
                    }
                    i += ch.len_utf8();
                }
            } else {
                i = quote_pos + 1;
                while i < len {
                    let ch = text[i..].chars().next().unwrap();
                    if ch == '\n' {
                        break;
                    }
                    if ch == '\\' {
                        let ch_len = ch.len_utf8();
                        i += ch_len;
                        if i < len {
                            let esc = text[i..].chars().next().unwrap();
                            if esc == '\n' {
                                line += 1;
                            }
                            i += esc.len_utf8();
                        }
                        continue;
                    }
                    i += ch.len_utf8();
                    if ch == quote_char as char {
                        break;
                    }
                }
            }

            let mut safe_end = std::cmp::min(i, len);
            while safe_end > start_idx && !text.is_char_boundary(safe_end) {
                safe_end -= 1;
            }
            let str_token = &text[start_idx..safe_end];
            let prefix = &text[start_idx..quote_pos];
            let is_fstring = prefix.contains('f') || prefix.contains('F');
            let is_doc = (module_docstring_allowed || expect_suite_docstring) && !is_fstring;
            if module_docstring_allowed || expect_suite_docstring {
                module_docstring_allowed = false;
                expect_suite_docstring = false;
            }

            tokens.push(PyToken {
                text: str_token.to_string(),
                start_line,
                end_line: line,
                is_comment: false,
                is_string: true,
                is_triple,
                is_docstring: is_doc,
            });
            continue;
        }

        // Parentheses tracking
        if b == b'(' || b == b'[' || b == b'{' {
            paren_depth += 1;
            i += 1;
            continue;
        }
        if b == b')' || b == b']' || b == b'}' {
            paren_depth = paren_depth.saturating_sub(1);
            i += 1;
            continue;
        }

        // Colon tracking
        if b == b':' {
            if paren_depth == 0 {
                after_colon = true;
            }
            i += 1;
            continue;
        }

        // Words / identifiers / operators
        let start = i;
        if b.is_ascii_alphabetic() || b == b'_' {
            while i < len && (bytes[i].is_ascii_alphanumeric() || bytes[i] == b'_') {
                i += 1;
            }
            let word = &text[start..i];
            if word == "def" || word == "class" || word == "async" {
                // Beginning of definition
                expect_suite_docstring = false;
            } else {
                module_docstring_allowed = false;
                if expect_suite_docstring {
                    expect_suite_docstring = false;
                }
            }
            tokens.push(PyToken {
                text: word.to_string(),
                start_line: line,
                end_line: line,
                is_comment: false,
                is_string: false,
                is_triple: false,
                is_docstring: false,
            });
        } else {
            // Operator / symbol
            module_docstring_allowed = false;
            if expect_suite_docstring {
                expect_suite_docstring = false;
            }
            let ch = text[i..].chars().next().unwrap();
            i += ch.len_utf8();
        }
    }

    tokens
}

fn is_unit_directive_line(line: &str, in_multiline: bool) -> (bool, bool) {
    let stripped = line.trim();
    if stripped.starts_with('#') || stripped.starts_with(';') || stripped.starts_with("//") {
        return (false, in_multiline);
    }
    let lower = stripped.to_ascii_lowercase();
    let is_start = lower.starts_with("listenstream=")
        || lower.starts_with("listendatagram=")
        || lower.starts_with("execstart=")
        || lower.starts_with("execstartpre=")
        || lower.starts_with("execstartpost=")
        || lower.starts_with("execstop=")
        || lower.starts_with("exec=")
        || lower.starts_with("environment=");
    let has_bind = stripped.contains("--bind");
    let is_directive = is_start || in_multiline || has_bind;
    let next_multiline = is_directive && stripped.ends_with('\\');
    (is_directive, next_multiline)
}

fn check_ports_ips_generic(
    text: &str,
    is_unit: bool,
    allowlist: &Allowlist,
    port_patterns: &[Regex],
    ip_pattern: &Regex,
) -> Vec<(usize, String)> {
    let mut out = Vec::new();
    let mut in_multiline = false;

    for (idx, line) in text.split('\n').enumerate() {
        let ln = idx + 1;
        let stripped = line.trim();
        if stripped.starts_with('#') || stripped.starts_with("//") || stripped.starts_with(';') {
            continue;
        }

        if is_unit {
            let (is_directive, next_multiline) = is_unit_directive_line(line, in_multiline);
            in_multiline = next_multiline;
            if !is_directive {
                continue;
            }
        }

        if is_line_exempt(line, allowlist) {
            continue;
        }
        let mut code_part = line;
        let mut in_s = false;
        let mut in_d = false;
        for (i, ch) in line.char_indices() {
            if ch == '\'' && !in_d {
                in_s = !in_s;
            } else if ch == '"' && !in_s {
                in_d = !in_d;
            } else if !in_s && !in_d && ch == '#' {
                code_part = &line[..i];
                break;
            }
        }
        for pat in port_patterns {
            for caps in pat.captures_iter(code_part) {
                let m = caps.get(0).unwrap();
                let port_str = match caps.get(1) {
                    Some(p) => p.as_str(),
                    None => continue,
                };
                let _port_num = match port_str.parse::<u32>() {
                    Ok(n) if (1..=65535).contains(&n) => n,
                    _ => continue,
                };
                let start = m.start();
                let end = m.end();
                if end < code_part.len() && code_part.as_bytes()[end] == b']' {
                    continue;
                }
                if start > 0
                    && ['-', '+', ':', '['].contains(&(code_part.as_bytes()[start - 1] as char))
                {
                    continue;
                }
                out.push((ln, m.as_str().to_string()));
            }
        }
        for m in ip_pattern.find_iter(code_part) {
            let ip_str = m.as_str();
            if is_routable_ip(ip_str) {
                out.push((ln, ip_str.to_string()));
            }
        }
    }
    out
}

fn check_ports_ips_py(
    tokens: &[PyToken],
    lines: &[&str],
    allowlist: &Allowlist,
    port_patterns: &[Regex],
    ip_pattern: &Regex,
) -> Vec<(usize, String)> {
    let mut out = Vec::new();
    for t in tokens {
        if t.is_comment {
            continue;
        }
        if t.is_string && t.is_triple {
            let s = t.text.as_str();
            // Only skip docstring-eligible triple quotes ("""", '''', r"""", r'''', u"""", u'''').
            // Do NOT skip if prefix contains 'f' or 'F' (e.g. f"""...""", f'''...''').
            if s.starts_with("\"\"\"")
                || s.starts_with("'''")
                || s.starts_with("r\"\"\"")
                || s.starts_with("r'''")
                || s.starts_with("R\"\"\"")
                || s.starts_with("R'''")
                || s.starts_with("u\"\"\"")
                || s.starts_with("u'''")
                || s.starts_with("U\"\"\"")
                || s.starts_with("U'''")
            {
                continue;
            }
        }
        let ln = t.start_line;
        let line = if ln >= 1 && ln <= lines.len() {
            lines[ln - 1]
        } else {
            ""
        };
        if is_line_exempt(line, allowlist) {
            continue;
        }
        let s = &t.text;
        for pat in port_patterns {
            for caps in pat.captures_iter(s) {
                let m = caps.get(0).unwrap();
                let port_str = match caps.get(1) {
                    Some(p) => p.as_str(),
                    None => continue,
                };
                let _port_num = match port_str.parse::<u32>() {
                    Ok(n) if (1..=65535).contains(&n) => n,
                    _ => continue,
                };
                let start = m.start();
                let end = m.end();
                if end < s.len() && s.as_bytes()[end] == b']' {
                    continue;
                }
                if start > 0
                    && ['[', '-', '+', ':', '/'].contains(&(s.as_bytes()[start - 1] as char))
                {
                    continue;
                }
                out.push((ln, m.as_str().to_string()));
            }
        }
        for m in ip_pattern.find_iter(s) {
            let ip_str = m.as_str();
            if is_routable_ip(ip_str) {
                out.push((ln, ip_str.to_string()));
            }
        }
    }
    out
}

// A deployed source checkout can be the filesystem root. Never recurse through
// its runtime mounts: the Git index is the source census, not the live disk.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum SourceScope {
    SourceTree,
    DeployedRoot,
}

fn source_scope(root: &Path) -> Result<SourceScope, String> {
    let canonical = fs::canonicalize(root)
        .map_err(|e| format!("cannot resolve source root {}: {e}", root.display()))?;
    Ok(if canonical.parent().is_none() {
        SourceScope::DeployedRoot
    } else {
        SourceScope::SourceTree
    })
}

fn source_git(root: &Path) -> Command {
    let mut command = Command::new("git");
    command
        .arg("-C")
        .arg(root)
        .env("GIT_OPTIONAL_LOCKS", "0")
        .env("GIT_TERMINAL_PROMPT", "0");
    for name in [
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_COMMON_DIR",
        "GIT_INDEX_FILE",
    ] {
        command.env_remove(name);
    }
    command
}

fn tracked_source_files(root: &Path) -> Result<Vec<PathBuf>, String> {
    let root =
        fs::canonicalize(root).map_err(|e| format!("cannot resolve deployed source root: {e}"))?;
    if !root.join(".git").exists() {
        return Err(format!(
            "refusing filesystem-root traversal: no Git checkout at {}",
            root.display()
        ));
    }
    let output = source_git(&root)
        .args(["ls-files", "--cached", "--full-name", "-z", "--"])
        .output()
        .map_err(|e| format!("cannot enumerate tracked source in {}: {e}", root.display()))?;
    if !output.status.success() {
        return Err(format!(
            "tracked source census failed in {} ({}): {}",
            root.display(),
            output.status,
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    let listing = String::from_utf8(output.stdout)
        .map_err(|e| format!("tracked source paths are not UTF-8: {e}"))?;
    if listing.is_empty() {
        return Err("tracked source census is empty; refusing filesystem-root traversal".into());
    }
    let mut files = Vec::new();
    'candidate: for name in listing.split('\0').filter(|name| !name.is_empty()) {
        let relative = Path::new(name);
        let mut path = root.clone();
        let mut regular = false;
        for component in relative.components() {
            let Component::Normal(part) = component else {
                return Err("tracked source census contains a non-relative path".into());
            };
            path.push(part);
            match fs::symlink_metadata(&path) {
                Ok(metadata) => {
                    if metadata.file_type().is_symlink() {
                        continue 'candidate;
                    }
                    regular = metadata.is_file();
                }
                Err(e)
                    if matches!(
                        e.kind(),
                        std::io::ErrorKind::NotFound | std::io::ErrorKind::NotADirectory
                    ) =>
                {
                    continue 'candidate
                }
                Err(e) => {
                    return Err(format!(
                        "cannot inspect tracked source {}: {e}",
                        path.display()
                    ))
                }
            }
        }
        if regular {
            files.push(path);
        }
    }
    files.sort();
    files.dedup();
    Ok(files)
}

fn source_files(root: &Path, scope: SourceScope) -> Result<Vec<PathBuf>, String> {
    if scope == SourceScope::DeployedRoot {
        return tracked_source_files(root);
    }
    // Ordinary source trees include untracked files, but discovery/read errors
    // must never turn an incomplete scan into a pass (even in advisory mode).
    WalkDir::new(root)
        .into_iter()
        .filter_entry(|entry| {
            if entry.file_type().is_dir() {
                let name = entry.file_name().to_string_lossy();
                if name == ".git" || name == "__pycache__" || name == "node_modules" {
                    return false;
                }
            }
            true
        })
        .filter_map(|entry| match entry {
            Ok(entry) if entry.file_type().is_file() => Some(Ok(entry.into_path())),
            Ok(_) => None,
            Err(error) => Some(Err(format!("source discovery failed: {error}"))),
        })
        .collect()
}

fn read_source_file(path: &Path) -> Result<Vec<u8>, String> {
    fs::read(path).map_err(|error| format!("cannot read source {}: {error}", path.display()))
}

type Violation = (String, usize, String);
type ScanOutput = (Vec<Violation>, usize);

fn scan(roots: &[String]) -> Result<ScanOutput, String> {
    let mut violations = Vec::new();
    let mut scanned = 0;

    let allowlist = load_allowlist(roots);
    let date_rx = Regex::new(r"\b20[0-9]{2}-[01][0-9]-[0-3][0-9]\b").unwrap();
    let port_patterns = vec![
        Regex::new(r"localhost:(\d+)").unwrap(),
        Regex::new(r"127\.0\.0\.1:(\d+)").unwrap(),
        Regex::new(r":(\d{4,5})\b").unwrap(),
    ];
    let ip_pattern = Regex::new(
        r"\b(?:[1-9]|\d{2}|1\d{2}|2[0-4]\d|25[0-5])\.(?:\d|[1-9]\d|1\d{2}|2[0-4]\d|25[0-5])\.(?:\d|[1-9]\d|1\d{2}|2[0-4]\d|25[0-5])\.(?:\d|[1-9]\d|1\d{2}|2[0-4]\d|25[0-5])\b",
    )
    .unwrap();
    let chpasswd_rx =
        Regex::new(r#"echo\s+["'][a-zA-Z0-9_-]+:[a-zA-Z0-9_-]+["']\s*\|\s*chpasswd"#).unwrap();

    for root in roots {
        let root = Path::new(root);
        for path in source_files(root, source_scope(root)?)? {
            let p = path.as_path();
            let rel = p.to_string_lossy().replace('\\', "/");
            if EXEMPT.iter().any(|x| rel.contains(x)) {
                continue;
            }
            let Some(file_name) = p.file_name() else {
                continue;
            };
            let fn_str = file_name.to_string_lossy();
            if !CODE_EXT.iter().any(|ext| fn_str.ends_with(ext)) {
                continue;
            }
            let raw = read_source_file(p)?;
            if raw.is_empty() {
                continue;
            }
            scanned += 1;

            let p_str = p.to_string_lossy().to_string();

            if let Some(hr) = header_risk(&raw, &fn_str) {
                violations.push((p_str.clone(), 0, format!("HEADER: {}", hr)));
            }

            let raw_slice = if raw.starts_with(BOM) {
                &raw[3..]
            } else {
                &raw[..]
            };
            let text = String::from_utf8_lossy(raw_slice).replace("\r\n", "\n");
            let lines: Vec<&str> = text.split('\n').collect();

            let is_file_exempt = allowlist.exempt_files.iter().any(|rx| rx.is_match(&rel));

            let is_unit = UNIT_EXT.iter().any(|ext| fn_str.ends_with(ext));
            if PORT_IP_EXT.iter().any(|ext| fn_str.ends_with(ext)) && !is_file_exempt {
                if fn_str.ends_with(".py") {
                    let tokens = tokenize_python(&text);
                    for (ln, snip) in
                        check_ports_ips_py(&tokens, &lines, &allowlist, &port_patterns, &ip_pattern)
                    {
                        violations.push((
                            p_str.clone(),
                            ln,
                            format!("HARDCODED-PORT/IP: {}", snip),
                        ));
                    }
                } else {
                    for (ln, snip) in check_ports_ips_generic(
                        &text,
                        is_unit,
                        &allowlist,
                        &port_patterns,
                        &ip_pattern,
                    ) {
                        violations.push((
                            p_str.clone(),
                            ln,
                            format!("HARDCODED-PORT/IP: {}", snip),
                        ));
                    }
                }
            }

            if rel.contains("usr/share/mios/ventoy/autorun") {
                for (idx, line) in lines.iter().enumerate() {
                    let ln = idx + 1;
                    if line.contains("chpasswd") && chpasswd_rx.is_match(line) {
                        violations.push((
                            p_str.clone(),
                            ln,
                            format!("PLAINTEXT-CHPASSWD: {}", truncate_chars(line.trim(), 80)),
                        ));
                    }
                }
            }

            let banner = lines.iter().take(4).cloned().collect::<Vec<_>>().join("\n");
            let is_generated = banner.contains("GENERATED") && banner.contains("DO NOT EDIT");

            if !is_generated {
                if fn_str.ends_with(".py") {
                    let tokens = tokenize_python(&text);
                    // 1. _date_in_comment_py: Comments and Docstrings
                    for t in &tokens {
                        if t.is_comment {
                            if date_rx.is_match(&t.text) {
                                violations.push((
                                    p_str.clone(),
                                    t.start_line,
                                    format!(
                                        "DATE-IN-COMMENT: {}",
                                        truncate_chars(t.text.trim(), 80)
                                    ),
                                ));
                            }
                        } else if t.is_docstring {
                            for ln in t.start_line..=t.end_line {
                                if ln >= 1 && ln <= lines.len() {
                                    let l = lines[ln - 1];
                                    if date_rx.is_match(l) {
                                        violations.push((
                                            p_str.clone(),
                                            ln,
                                            format!(
                                                "DATE-IN-COMMENT: {}",
                                                truncate_chars(l.trim(), 80)
                                            ),
                                        ));
                                    }
                                }
                            }
                        }
                    }
                    // 2. _date_in_string_py: Dated attributions in string literals
                    for t in &tokens {
                        if t.is_string {
                            let s = &t.text;
                            for m in date_rx.find_iter(s) {
                                let i = m.start();
                                if i == 0
                                    || !s[..i]
                                        .chars()
                                        .next_back()
                                        .is_some_and(|c| c.is_whitespace())
                                {
                                    continue;
                                }
                                let nl_count = s[..i].matches('\n').count();
                                let ln = t.start_line + nl_count;
                                let snip = if ln >= 1 && ln <= lines.len() {
                                    truncate_chars(lines[ln - 1].trim(), 80)
                                } else {
                                    m.as_str()
                                };
                                violations.push((
                                    p_str.clone(),
                                    ln,
                                    format!("DATE-IN-STRING: {}", snip),
                                ));
                            }
                        }
                    }
                } else if !is_unit {
                    for (idx, line) in lines.iter().enumerate() {
                        let ln = idx + 1;
                        if let Some(cs) = comment_start(line) {
                            if date_rx.is_match(&line[cs..]) {
                                let snip = truncate_chars(line[cs..].trim(), 80);
                                violations.push((
                                    p_str.clone(),
                                    ln,
                                    format!("DATE-IN-COMMENT: {}", snip),
                                ));
                            }
                        }
                    }
                }
            }
        }
    }

    Ok((violations, scanned))
}

fn main() -> ExitCode {
    let args: Vec<String> = std::env::args().collect();
    let roots: Vec<String> = args[1..]
        .iter()
        .filter(|a| !a.starts_with("--"))
        .cloned()
        .collect();
    let roots = if roots.is_empty() {
        vec![".".to_string()]
    } else {
        roots
    };
    let soft = std::env::var("MIOS_HARDCODE_LINT_SOFT")
        .map(|v| v == "1")
        .unwrap_or(false);

    let mut missing = Vec::new();
    for r in &roots {
        if !Path::new(r).exists() {
            missing.push(r.clone());
        }
    }
    if !missing.is_empty() {
        eprintln!(
            "[mios-hardcode-lint] FAIL: root(s) do not exist: {}",
            missing.join(", ")
        );
        return ExitCode::from(1);
    }

    let (violations, scanned) = match scan(&roots) {
        Ok(result) => result,
        Err(error) => {
            eprintln!("[mios-hardcode-lint] FAIL: source discovery: {error}");
            return ExitCode::from(1);
        }
    };

    if scanned == 0 {
        eprintln!(
            "[mios-hardcode-lint] FAIL: scanned 0 files under {} -- nothing was linted, so this is not a pass",
            roots.join(", ")
        );
        return ExitCode::from(1);
    }

    if violations.is_empty() {
        println!(
            "[mios-hardcode-lint] PASS: {} file(s) scanned; no date-in-comment/string / header crash-risk / port-IP hardcode.",
            scanned
        );
        return ExitCode::SUCCESS;
    }

    let cap = 60;
    for (p, ln, msg) in violations.iter().take(cap) {
        eprintln!("    {}:{}: {}", p, ln, msg);
    }
    if violations.len() > cap {
        eprintln!("    ... and {} more", violations.len() - cap);
    }
    eprintln!(
        "[mios-hardcode-lint] FAIL: {} NO-HARDCODE violation(s) (strip dates/ports/IPs -> timeless comment/string / env vars; move a header below the shebang/BOM).",
        violations.len()
    );
    if soft {
        eprintln!("[mios-hardcode-lint] (MIOS_HARDCODE_LINT_SOFT=1 -> advisory, exit 0)");
        return ExitCode::SUCCESS;
    }

    ExitCode::from(1)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::atomic::{AtomicUsize, Ordering};

    struct SourceFixture(PathBuf);

    impl SourceFixture {
        fn new(git: bool) -> Self {
            static NEXT: AtomicUsize = AtomicUsize::new(0);
            let path = std::env::temp_dir().join(format!(
                "mios-hardcode-root-scope-{}-{}",
                std::process::id(),
                NEXT.fetch_add(1, Ordering::Relaxed)
            ));
            fs::create_dir(&path).expect("fixture path must be new, never overwritten");
            let fixture = Self(path);
            if git {
                fixture.git(&["init", "--quiet"]);
            }
            fixture
        }

        fn git(&self, args: &[&str]) {
            let out = source_git(&self.0).args(args).output().unwrap();
            assert!(
                out.status.success(),
                "git fixture failed: {}",
                String::from_utf8_lossy(&out.stderr)
            );
        }

        fn file(&self, name: &str, text: &str) -> PathBuf {
            let path = self.0.join(name);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(&path, text).unwrap();
            path
        }

        fn deployed_files(&self) -> Vec<PathBuf> {
            source_files(&self.0, SourceScope::DeployedRoot).unwrap()
        }
    }

    impl Drop for SourceFixture {
        fn drop(&mut self) {
            fs::remove_dir_all(&self.0).expect("remove only this newly created owned fixture");
        }
    }

    #[cfg(unix)]
    #[test]
    fn filesystem_root_aliases_select_tracked_scope() {
        for root in ["/", "//", "///", "/."] {
            assert_eq!(
                source_scope(Path::new(root)).unwrap(),
                SourceScope::DeployedRoot
            );
        }
    }

    #[test]
    fn normal_directories_and_files_retain_tree_scope() {
        let root = SourceFixture::new(false);
        let file = root.file("source.py", "print('safe')\n");
        assert_eq!(source_scope(&root.0).unwrap(), SourceScope::SourceTree);
        assert_eq!(source_scope(&file).unwrap(), SourceScope::SourceTree);
        assert_eq!(
            source_files(&file, SourceScope::SourceTree).unwrap(),
            vec![file]
        );
    }

    #[test]
    fn deployed_scope_uses_only_index_and_never_enters_untracked_runtime_tree() {
        let root = SourceFixture::new(true);
        let tracked = root.file("src/tracked script.sh", "#!/bin/sh\necho safe\n");
        root.git(&["add", "--", "src/tracked script.sh"]);
        let outside = root.file("mnt/host/runtime.py", "service = 'localhost:12345'\n");
        let before = fs::read(root.0.join(".git/index")).unwrap();
        assert_eq!(
            root.deployed_files(),
            vec![fs::canonicalize(tracked).unwrap()]
        );
        assert_eq!(fs::read(root.0.join(".git/index")).unwrap(), before);
        assert_eq!(
            fs::read_to_string(outside).unwrap(),
            "service = 'localhost:12345'\n"
        );
    }

    #[test]
    fn tracked_working_edits_are_linted_without_rewriting_file_or_index() {
        let root = SourceFixture::new(true);
        let path = root.file("source.py", "print('safe')\n");
        root.git(&["add", "--", "source.py"]);
        let changed = "service = 'localhost:12345'\n";
        fs::write(&path, changed).unwrap();
        let before_index = fs::read(root.0.join(".git/index")).unwrap();
        let roots: Vec<String> = root
            .deployed_files()
            .iter()
            .map(|p| p.to_string_lossy().into_owned())
            .collect();
        let (violations, scanned) = scan(&roots).unwrap();
        assert_eq!(scanned, 1);
        assert!(violations
            .iter()
            .any(|v| v.2.starts_with("HARDCODED-PORT/IP:")));
        assert_eq!(fs::read_to_string(path).unwrap(), changed);
        assert_eq!(fs::read(root.0.join(".git/index")).unwrap(), before_index);
    }

    #[test]
    fn tracked_header_violations_still_fail_real_scanner() {
        let root = SourceFixture::new(true);
        root.file("source.sh", "# misplaced header\n#!/bin/sh\necho safe\n");
        root.git(&["add", "--", "source.sh"]);
        let roots: Vec<String> = root
            .deployed_files()
            .iter()
            .map(|p| p.to_string_lossy().into_owned())
            .collect();
        let (violations, scanned) = scan(&roots).unwrap();
        assert_eq!(scanned, 1);
        assert!(violations.iter().any(|v| v.2.starts_with("HEADER:")));
    }

    #[test]
    fn deployed_scope_without_git_refuses_instead_of_walking() {
        let root = SourceFixture::new(false);
        root.file("untracked.py", "print('safe')\n");
        assert!(source_files(&root.0, SourceScope::DeployedRoot)
            .unwrap_err()
            .contains("no Git checkout"));
    }

    #[test]
    fn deployed_scope_with_broken_git_refuses_instead_of_walking() {
        let root = SourceFixture::new(false);
        root.file(".git", "invalid git metadata\n");
        root.file("untracked.py", "print('safe')\n");
        assert!(source_files(&root.0, SourceScope::DeployedRoot)
            .unwrap_err()
            .contains("tracked source census failed"));
    }

    #[test]
    fn empty_git_index_refuses_untracked_runtime_fallback() {
        let root = SourceFixture::new(true);
        root.file("runtime.py", "print('safe')\n");
        assert!(source_files(&root.0, SourceScope::DeployedRoot)
            .unwrap_err()
            .contains("census is empty"));
    }

    #[cfg(unix)]
    #[test]
    fn nul_delimited_git_names_preserve_spaces_and_newlines() {
        let root = SourceFixture::new(true);
        let name = "src/space and\nnewline.sh";
        let path = root.file(name, "#!/bin/sh\necho safe\n");
        root.git(&["add", "--", name]);
        assert_eq!(root.deployed_files(), vec![fs::canonicalize(path).unwrap()]);
    }

    #[cfg(unix)]
    #[test]
    fn deployed_scope_never_follows_tracked_file_symlink() {
        let root = SourceFixture::new(true);
        let outside = SourceFixture::new(false);
        let path = root.file("source.py", "print('safe')\n");
        let target = outside.file("outside.py", "service = 'localhost:12345'\n");
        root.git(&["add", "--", "source.py"]);
        fs::remove_file(&path).unwrap();
        std::os::unix::fs::symlink(&target, &path).unwrap();
        assert!(root.deployed_files().is_empty());
        assert_eq!(
            fs::read_to_string(target).unwrap(),
            "service = 'localhost:12345'\n"
        );
    }

    #[cfg(unix)]
    #[test]
    fn deployed_scope_never_follows_tracked_directory_replaced_by_symlink() {
        let root = SourceFixture::new(true);
        let outside = SourceFixture::new(false);
        root.file("src/source.py", "print('safe')\n");
        outside.file("source.py", "service = 'localhost:12345'\n");
        root.git(&["add", "--", "src/source.py"]);
        fs::rename(root.0.join("src"), root.0.join("saved-source")).unwrap();
        std::os::unix::fs::symlink(&outside.0, root.0.join("src")).unwrap();
        assert!(root.deployed_files().is_empty());
    }

    #[test]
    fn deleted_tracked_files_do_not_fall_back_to_runtime_walk() {
        let root = SourceFixture::new(true);
        let deleted = root.file("deleted.py", "print('safe')\n");
        let kept = root.file("kept.py", "print('safe')\n");
        root.git(&["add", "--", "deleted.py", "kept.py"]);
        fs::remove_file(deleted).unwrap();
        root.file("runtime.py", "service = 'localhost:12345'\n");
        assert_eq!(root.deployed_files(), vec![fs::canonicalize(kept).unwrap()]);
    }

    #[test]
    fn ordinary_tree_still_lints_untracked_sources_and_prunes_legacy_exempt_dirs() {
        let root = SourceFixture::new(false);
        root.file("source.py", "service = 'localhost:12345'\n");
        root.file("node_modules/ignored.py", "service = 'localhost:12345'\n");
        root.file("__pycache__/ignored.py", "service = 'localhost:12345'\n");
        let (violations, scanned) = scan(&[root.0.to_string_lossy().into_owned()]).unwrap();
        assert_eq!(scanned, 1);
        assert!(violations
            .iter()
            .any(|v| v.2.starts_with("HARDCODED-PORT/IP:")));
    }

    #[test]
    fn source_discovery_errors_are_not_swallowed() {
        let root = SourceFixture::new(false);
        assert!(source_files(&root.0.join("missing"), SourceScope::SourceTree).is_err());
    }

    #[test]
    fn source_read_errors_are_not_swallowed() {
        let root = SourceFixture::new(false);
        assert!(read_source_file(&root.0.join("missing.py")).is_err());
    }

    #[test]
    fn test_header_risk_ps1() {
        let mut stranded = vec![0u8; 100];
        stranded[10..13].copy_from_slice(BOM);
        assert!(header_risk(&stranded, "test.ps1").is_some());

        let mut valid = vec![0u8; 100];
        valid[0..3].copy_from_slice(BOM);
        assert!(header_risk(&valid, "test.ps1").is_none());

        let no_bom = b"Write-Host 'hello'";
        assert!(header_risk(no_bom, "test.ps1").is_none());
    }

    #[test]
    fn test_header_risk_sh() {
        let bad_sh = b"# Header\n#!/bin/bash\necho ok";
        assert_eq!(
            header_risk(bad_sh, "script.sh"),
            Some("shebang not on line 1 (a header/comment is above it)")
        );

        let good_sh = b"#!/bin/bash\n# Header\necho ok";
        assert!(header_risk(good_sh, "script.sh").is_none());

        let no_shebang = b"# Sourced library\necho ok";
        assert!(header_risk(no_shebang, "script.sh").is_none());
    }

    #[test]
    fn test_routable_ip() {
        assert!(is_routable_ip("8.8.8.8"));
        assert!(is_routable_ip("1.1.1.1"));
        assert!(is_routable_ip("198.51.100.1"));

        // Loopback and zero
        assert!(!is_routable_ip("127.0.0.1"));
        assert!(!is_routable_ip("0.0.0.0"));

        // Private RFC 1918
        assert!(!is_routable_ip("10.0.0.1"));
        assert!(!is_routable_ip("10.255.255.255"));
        assert!(!is_routable_ip("172.16.0.1"));
        assert!(!is_routable_ip("172.31.255.255"));
        assert!(!is_routable_ip("192.168.1.1"));
        assert!(!is_routable_ip("192.168.0.100"));

        // CGNAT RFC 6598
        assert!(!is_routable_ip("100.64.0.1"));
        assert!(!is_routable_ip("100.127.255.255"));
        assert!(is_routable_ip("100.128.0.1")); // Outside CGNAT
    }

    #[test]
    fn test_comment_start() {
        assert_eq!(comment_start("# comment"), Some(0));
        assert_eq!(comment_start("echo 'hello' # comment"), Some(13));
        assert_eq!(comment_start("echo \"#not_comment\" # comment"), Some(20));
        assert_eq!(comment_start("#!/bin/bash"), None);
    }

    #[test]
    fn test_python_date_attribution_vs_value() {
        let text = r#"
# Valid value: quote-led
VERSION = "2026-10-06"
SLUG = "release/2026-10-06"
PREFIXED = "date-2026-10-06"

# Invalid: prose attribution preceded by whitespace
MSG = "Modified on 2026-10-06 by team"
"#;
        let tokens = tokenize_python(text);
        let lines: Vec<&str> = text.split('\n').collect();
        let date_rx = Regex::new(r"\b20[0-9]{2}-[01][0-9]-[0-3][0-9]\b").unwrap();

        let mut string_violations = Vec::new();
        for t in &tokens {
            if t.is_string {
                let s = &t.text;
                for m in date_rx.find_iter(s) {
                    let i = m.start();
                    if i > 0
                        && s[..i]
                            .chars()
                            .next_back()
                            .is_some_and(|c| c.is_whitespace())
                    {
                        let nl_count = s[..i].matches('\n').count();
                        let ln = t.start_line + nl_count;
                        let snip = lines[ln - 1].trim();
                        string_violations.push((ln, snip.to_string()));
                    }
                }
            }
        }

        assert_eq!(string_violations.len(), 1);
        assert!(string_violations[0]
            .1
            .contains("Modified on 2026-10-06 by team"));
    }

    #[test]
    fn test_python_docstring_detection() {
        let text = r#"
"""Module docstring 2026-10-06."""

def compute():
    """Function docstring 2026-10-06."""
    return 42

class Runner:
    """Class docstring 2026-10-06."""
    pass
"#;
        let tokens = tokenize_python(text);
        let docstrings: Vec<&PyToken> = tokens.iter().filter(|t| t.is_docstring).collect();
        assert_eq!(docstrings.len(), 3);
        assert!(docstrings[0].text.contains("Module docstring"));
        assert!(docstrings[1].text.contains("Function docstring"));
        assert!(docstrings[2].text.contains("Class docstring"));
    }

    #[test]
    fn test_port_patterns() {
        let allowlist = Allowlist {
            exempt_files: Vec::new(),
            exempt_patterns: Vec::new(),
            exempt_patterns_rx: Vec::new(),
        };
        let port_patterns = vec![
            Regex::new(r"localhost:(\d+)").unwrap(),
            Regex::new(r"127\.0\.0\.1:(\d+)").unwrap(),
            Regex::new(r":(\d{4,5})\b").unwrap(),
        ];
        let ip_pattern = Regex::new(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b").unwrap();

        let text = "nc -l :8080\ncurl http://localhost:9000/api\n";
        let violations =
            check_ports_ips_generic(text, false, &allowlist, &port_patterns, &ip_pattern);
        assert!(!violations.is_empty());
        assert!(violations.iter().any(|(_, s)| s == ":8080"));
        assert!(violations.iter().any(|(_, s)| s == "localhost:9000"));

        // Bracketed IPv6 should not trigger port violation
        let v_bracketed = check_ports_ips_generic(
            "[::1]:8080]",
            false,
            &allowlist,
            &port_patterns,
            &ip_pattern,
        );
        assert!(v_bracketed.is_empty());

        // Systemd unit directives vs comments
        let unit_bad = "[Service]\nExecStart=/usr/bin/daemon :8080\n";
        let v_unit =
            check_ports_ips_generic(unit_bad, true, &allowlist, &port_patterns, &ip_pattern);
        assert!(!v_unit.is_empty());

        let unit_comment =
            "# Historical note: previously ran on :8080\n[Service]\nExecStart=/usr/bin/daemon\n";
        let v_comment =
            check_ports_ips_generic(unit_comment, true, &allowlist, &port_patterns, &ip_pattern);
        assert!(v_comment.is_empty());
    }

    #[test]
    fn test_ventoy_chpasswd() {
        let chpasswd_rx =
            Regex::new(r#"echo\s+["'][a-zA-Z0-9_-]+:[a-zA-Z0-9_-]+["']\s*\|\s*chpasswd"#).unwrap();
        let bad = "echo 'admin:secret123' | chpasswd";
        assert!(chpasswd_rx.is_match(bad));

        let good = "chpasswd < /tmp/file";
        assert!(!chpasswd_rx.is_match(good));
    }

    #[test]
    fn test_unclosed_multibyte_string_no_panic() {
        let text = "s = \"\"\"😀\n";
        let tokens = tokenize_python(text);
        assert!(!tokens.is_empty());
        let str_tok = tokens.iter().find(|t| t.is_string);
        assert!(str_tok.is_some());
        assert_eq!(str_tok.unwrap().text, "\"\"\"😀\n");
    }

    #[test]
    fn test_fstring_triple_quote_port_flagged() {
        let text = "def f(): return f'''http://localhost:9090'''\n";
        let tokens = tokenize_python(text);
        let lines: Vec<&str> = text.split('\n').collect();
        let allowlist = Allowlist {
            exempt_files: Vec::new(),
            exempt_patterns: Vec::new(),
            exempt_patterns_rx: Vec::new(),
        };
        let port_patterns = vec![
            Regex::new(r"localhost:(\d+)").unwrap(),
            Regex::new(r"127\.0\.0\.1:(\d+)").unwrap(),
            Regex::new(r":(\d{4,5})\b").unwrap(),
        ];
        let ip_pattern = Regex::new(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b").unwrap();
        let violations =
            check_ports_ips_py(&tokens, &lines, &allowlist, &port_patterns, &ip_pattern);
        assert!(!violations.is_empty());
        assert!(violations.iter().any(|(_, s)| s.contains("9090")));
    }
}
