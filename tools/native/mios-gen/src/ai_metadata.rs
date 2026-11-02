// AI-hint: Native AI header metadata catalog (usr/share/mios/ai/v1/metadata.json): the AI-hint/AI-related/AI-functions/AI-doc headers of every tracked regular file, rendered byte-identically to the Python adapter it replaces on the generation path (json.dumps indent=2, ensure_ascii).
// AI-related: usr/libexec/mios/mios-ai-metadata.py, usr/lib/mios/mios_comments.py (tracked_file_modes), usr/share/mios/ai/v1/metadata.json, usr/share/mios/mios.toml [generation.sync], automation/98-drift-checks.sh (check_ai_metadata_fresh), tests/test-ai-metadata-native-parity.py, tests/golden/README.md
// AI-functions: tracked_file_modes, extract, catalog, render, diff_entries, run
//
// Every reading rule is the Python leg's, restated: the first 8192 characters
// after universal-newline translation, str.splitlines() boundaries, the first
// 60 lines, Python's `\s` and str.strip() whitespace set, and the json module's
// ASCII escaping. tests/test-ai-metadata-native-parity.py diffs the two over
// the real tree and over planted edge cases.

use regex::Regex;
use serde_json::Value;
use std::collections::BTreeMap;
use std::fs;
use std::io::Read;
use std::path::Path;
use std::process::Command;

const CATALOG: &str = "usr/share/mios/ai/v1/metadata.json";
/// mios_comments._SKIP_DIRS: never descended by the index-less walk.
const SKIP_DIRS: &[&str] = &[
    ".git",
    "target",
    "node_modules",
    "__pycache__",
    ".venv",
    ".rustup",
    ".cargo",
    "output",
];

/// Python str.isspace(): Unicode White_Space plus the four information separators.
fn py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

fn py_strip(s: &str) -> &str {
    s.trim_matches(py_space)
}

/// str.splitlines(): no keepends, and a trailing boundary adds no empty line.
fn splitlines(text: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut chars = text.char_indices().peekable();
    while let Some((i, c)) = chars.next() {
        let boundary = matches!(
            c,
            '\n' | '\r'
                | '\u{0b}'
                | '\u{0c}'
                | '\u{1c}'
                | '\u{1d}'
                | '\u{1e}'
                | '\u{85}'
                | '\u{2028}'
                | '\u{2029}'
        );
        if boundary {
            out.push(&text[start..i]);
            let mut end = i + c.len_utf8();
            if c == '\r' {
                if let Some(&(j, '\n')) = chars.peek() {
                    chars.next();
                    end = j + 1;
                }
            }
            start = end;
        }
    }
    if start < text.len() {
        out.push(&text[start..]);
    }
    out
}

/// `open(path, encoding="utf-8", errors="replace").read(8192)`: decoded,
/// CRLF and lone CR translated to LF, then the first 8192 characters.
fn head_text(path: &Path) -> std::io::Result<String> {
    let mut buf = Vec::new();
    // 8192 characters are at most 32768 bytes; a little more keeps a CRLF or a
    // multi-byte sequence at the cut from decoding differently.
    fs::File::open(path)?
        .take(8192 * 4 + 8)
        .read_to_end(&mut buf)?;
    let decoded = String::from_utf8_lossy(&buf)
        .replace("\r\n", "\n")
        .replace('\r', "\n");
    Ok(decoded.chars().take(8192).collect())
}

struct Patterns {
    hint: Regex,
    related: Regex,
    functions: Regex,
    doc: Regex,
}

impl Patterns {
    fn new() -> Self {
        // `\s` is Python's whitespace class, which also holds U+001C..U+001F.
        let p = |tag: &str| {
            Regex::new(&format!(r"(?i)^[#/<!;\-*\s\x1c-\x1f]*{tag}:[\s\x1c-\x1f]*(.+?)(?:[\s\x1c-\x1f]*-->)?[\s\x1c-\x1f]*$"))
                .expect("static AI header pattern")
        };
        Patterns {
            hint: p("AI-hint"),
            related: p("AI-related"),
            functions: p("AI-functions"),
            doc: p("AI-doc"),
        }
    }
}

fn comment_style(rel: &str) -> &'static str {
    let name = rel.rsplit('/').next().unwrap_or(rel);
    // os.path.splitext: a leading dot is not an extension separator.
    let ext = match name.trim_start_matches('.').rfind('.') {
        Some(i) => name[name.len() - name.trim_start_matches('.').len() + i..].to_ascii_lowercase(),
        None => String::new(),
    };
    match ext.as_str() {
        ".md" | ".html" => "xml",
        ".js" | ".ts" | ".rs" | ".qml" => "slash",
        ".sql" => "dash",
        ".ini" | ".cfg" => "semi",
        _ => "hash",
    }
}

fn list(raw: &str) -> Vec<String> {
    py_strip(raw)
        .split(',')
        .map(py_strip)
        .filter(|s| !s.is_empty())
        .map(str::to_string)
        .collect()
}

/// The header entry of one file, or None when it carries no AI-* header.
fn extract(p: &Patterns, content: &str, rel: &str) -> Option<Value> {
    let (mut hint, mut related, mut functions, mut doc): (
        Option<String>,
        Vec<String>,
        Vec<String>,
        Option<String>,
    ) = (None, Vec::new(), Vec::new(), None);
    for line in splitlines(content).into_iter().take(60) {
        // Each tag is taken once, first match wins, and a matched line is not
        // offered to the tags after it -- the Python leg's `continue`.
        if hint.as_deref().is_none_or(str::is_empty) {
            if let Some(m) = p.hint.captures(line) {
                hint = Some(py_strip(&m[1]).to_string());
                continue;
            }
        }
        if related.is_empty() {
            if let Some(m) = p.related.captures(line) {
                related = list(&m[1]);
                continue;
            }
        }
        if functions.is_empty() {
            if let Some(m) = p.functions.captures(line) {
                functions = list(&m[1]);
                continue;
            }
        }
        if doc.as_deref().is_none_or(str::is_empty) {
            if let Some(m) = p.doc.captures(line) {
                doc = Some(py_strip(&m[1]).to_string());
                continue;
            }
        }
    }
    let empty = |s: &Option<String>| s.as_deref().is_none_or(str::is_empty);
    if empty(&hint) && related.is_empty() && functions.is_empty() && empty(&doc) {
        return None;
    }
    let mut entry = serde_json::Map::new();
    entry.insert("path".into(), Value::String(rel.replace('\\', "/")));
    entry.insert("hint".into(), hint.map_or(Value::Null, Value::String));
    entry.insert("related".into(), Value::from(related));
    entry.insert("functions".into(), Value::from(functions));
    entry.insert("doc".into(), doc.map_or(Value::Null, Value::String));
    entry.insert("comment_style".into(), Value::from(comment_style(rel)));
    entry.insert("has_shebang".into(), Value::Bool(content.starts_with("#!")));
    Some(Value::Object(entry))
}

/// mios_comments.tracked_file_modes: the git index (stage 0 only) of a
/// checkout, else a walk of a standalone fixture that never follows links.
pub fn tracked_file_modes(root: &Path) -> Result<BTreeMap<String, String>, String> {
    let mut modes = BTreeMap::new();
    if fs::symlink_metadata(root.join(".git")).is_err() {
        let mut stack = vec![root.to_path_buf()];
        while let Some(dir) = stack.pop() {
            let Ok(entries) = fs::read_dir(&dir) else {
                continue;
            };
            for entry in entries.flatten() {
                let path = entry.path();
                let name = entry.file_name().to_string_lossy().into_owned();
                let Ok(kind) = entry.file_type() else {
                    continue;
                };
                // os.walk files a link to a directory under dirnames and never
                // descends it; every other link is a file named 120000.
                let is_dir = if kind.is_symlink() {
                    path.is_dir()
                } else {
                    kind.is_dir()
                };
                if is_dir {
                    if !kind.is_symlink()
                        && !SKIP_DIRS.contains(&name.as_str())
                        && !name.starts_with('.')
                    {
                        stack.push(path);
                    }
                    continue;
                }
                let rel = path
                    .strip_prefix(root)
                    .map_err(|e| e.to_string())?
                    .to_string_lossy()
                    .replace('\\', "/");
                modes.insert(
                    rel,
                    if kind.is_symlink() {
                        "120000"
                    } else {
                        "100644"
                    }
                    .to_string(),
                );
            }
        }
        return Ok(modes);
    }
    let mut git = Command::new("git");
    git.args([
        "-c",
        "safe.directory=*",
        "-c",
        "core.ignorecase=false",
        "ls-files",
        "--stage",
        "-z",
    ])
    .current_dir(root);
    for (key, _) in std::env::vars_os() {
        if key.to_string_lossy().starts_with("GIT_") {
            git.env_remove(key);
        }
    }
    let out = git
        .output()
        .map_err(|_| "cannot read the tracked source index".to_string())?;
    if !out.status.success() {
        return Err("cannot read the tracked source index".into());
    }
    let text =
        String::from_utf8(out.stdout).map_err(|e| format!("tracked paths are not UTF-8: {e}"))?;
    for record in text.split('\0').filter(|r| !r.is_empty()) {
        let (meta, rel) = record
            .split_once('\t')
            .ok_or_else(|| format!("malformed index record: {record}"))?;
        let mut fields = meta.split_whitespace();
        let (mode, stage) = (fields.next().unwrap_or(""), fields.nth(1).unwrap_or(""));
        if stage != "0" {
            return Err(format!("unmerged source index entry: {rel}"));
        }
        modes.insert(rel.to_string(), mode.to_string());
    }
    Ok(modes)
}

/// The catalog object, keys in the Python leg's order.
pub fn catalog(root: &Path) -> Result<Value, String> {
    let tracked = tracked_file_modes(root)?;
    let patterns = Patterns::new();
    let (mut entries, mut hints, mut funcs, mut related) = (Vec::new(), 0u64, 0u64, 0u64);
    for (rel, mode) in &tracked {
        if mode != "100644" && mode != "100755" {
            continue;
        }
        let full = root.join(rel);
        match fs::symlink_metadata(&full) {
            Ok(m) if m.file_type().is_symlink() => continue,
            Ok(m) if m.is_file() => {}
            _ => continue,
        }
        let Ok(content) = head_text(&full) else {
            continue;
        };
        if let Some(entry) = extract(&patterns, &content, rel) {
            hints += u64::from(entry["hint"].as_str().is_some_and(|h| !h.is_empty()));
            funcs += entry["functions"].as_array().map_or(0, |a| a.len() as u64);
            related += entry["related"].as_array().map_or(0, |a| a.len() as u64);
            entries.push(entry);
        }
    }
    let mut top = serde_json::Map::new();
    top.insert("format".into(), Value::from("openai_strict_schema_v1"));
    top.insert(
        "total_files_scanned".into(),
        Value::from(tracked.len() as u64),
    );
    top.insert(
        "total_metadata_entries".into(),
        Value::from(entries.len() as u64),
    );
    top.insert("total_hints".into(), Value::from(hints));
    top.insert("total_functions_indexed".into(), Value::from(funcs));
    top.insert("total_related_links".into(), Value::from(related));
    top.insert("entries".into(), Value::Array(entries));
    Ok(Value::Object(top))
}

/// json.dumps(v, indent=2) with the default ensure_ascii=True.
pub fn render(v: &Value) -> String {
    let mut out = String::new();
    write_value(&mut out, v, 0);
    out
}

fn write_string(out: &mut String, s: &str) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\r' => out.push_str("\\r"),
            '\t' => out.push_str("\\t"),
            '\u{08}' => out.push_str("\\b"),
            '\u{0c}' => out.push_str("\\f"),
            ' '..='~' => out.push(c),
            _ => {
                let mut units = [0u16; 2];
                for unit in c.encode_utf16(&mut units) {
                    out.push_str(&format!("\\u{unit:04x}"));
                }
            }
        }
    }
    out.push('"');
}

fn write_value(out: &mut String, v: &Value, depth: usize) {
    let pad = |n: usize| "  ".repeat(n);
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => out.push_str(&n.to_string()),
        Value::String(s) => write_string(out, s),
        Value::Array(items) if items.is_empty() => out.push_str("[]"),
        Value::Array(items) => {
            out.push_str("[\n");
            for (i, item) in items.iter().enumerate() {
                if i > 0 {
                    out.push_str(",\n");
                }
                out.push_str(&pad(depth + 1));
                write_value(out, item, depth + 1);
            }
            out.push('\n');
            out.push_str(&pad(depth));
            out.push(']');
        }
        Value::Object(map) if map.is_empty() => out.push_str("{}"),
        Value::Object(map) => {
            out.push_str("{\n");
            for (i, (key, item)) in map.iter().enumerate() {
                if i > 0 {
                    out.push_str(",\n");
                }
                out.push_str(&pad(depth + 1));
                write_string(out, key);
                out.push_str(": ");
                write_value(out, item, depth + 1);
            }
            out.push('\n');
            out.push_str(&pad(depth));
            out.push('}');
        }
    }
}

fn short(v: Option<&Value>) -> String {
    match v {
        None | Some(Value::Null) => "None".into(),
        Some(Value::String(s)) => format!("'{s}'"),
        Some(other) => other.to_string(),
    }
}

/// What differs, entry by entry, so a stale file says which header moved.
pub fn diff_entries(tracked: &Value, fresh: &Value) -> Vec<String> {
    let mut lines = Vec::new();
    let empty = serde_json::Map::new();
    let (t, f) = (
        tracked.as_object().unwrap_or(&empty),
        fresh.as_object().unwrap_or(&empty),
    );
    let keys: std::collections::BTreeSet<&String> = t.keys().chain(f.keys()).collect();
    for key in keys {
        if key != "entries" && t.get(key) != f.get(key) {
            lines.push(format!(
                "field {key}: tracked={} fresh={}",
                short(t.get(key)),
                short(f.get(key))
            ));
        }
    }
    let by_path = |v: Option<&Value>| -> BTreeMap<String, Value> {
        v.and_then(Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(|e| Some((e.get("path")?.as_str()?.to_string(), e.clone())))
            .collect()
    };
    let (old, new) = (by_path(t.get("entries")), by_path(f.get("entries")));
    let paths: std::collections::BTreeSet<&String> = old.keys().chain(new.keys()).collect();
    for path in paths {
        match (old.get(path), new.get(path)) {
            (Some(_), None) => lines.push(format!("entry {path}: in the tracked file but no longer has an AI header (or is untracked)")),
            (None, Some(_)) => lines.push(format!("entry {path}: has an AI header but is missing from the tracked file")),
            (Some(a), Some(b)) if a != b => {
                let mut differing: Vec<&String> = a
                    .as_object()
                    .into_iter()
                    .chain(b.as_object())
                    .flat_map(|m| m.keys())
                    .filter(|k| a.get(k.as_str()) != b.get(k.as_str()))
                    .collect();
                differing.sort();
                differing.dedup();
                let names: Vec<&str> = differing.iter().map(|s| s.as_str()).collect();
                lines.push(format!("entry {path}: differs in {}", names.join(", ")));
            }
            _ => {}
        }
    }
    lines
}

/// `mios-gen ai-metadata`: write the catalog (default path under `root`), or
/// with `check_fresh` compare the committed catalog byte for byte, writing nothing.
pub fn run(root: &Path, export: Option<&Path>, check_fresh: bool) -> Result<String, String> {
    let fresh = catalog(root).map_err(|e| format!("[ai-metadata] FAIL: {e}\n"))?;
    let body = render(&fresh);
    let entries = fresh["total_metadata_entries"].as_u64().unwrap_or(0);
    if check_fresh {
        let path = root.join(CATALOG);
        let on_disk = fs::read(&path).map_err(|e| {
            format!("[ai-metadata] FAIL: {CATALOG} is unreadable ({e}); nothing was compared.\n")
        })?;
        if on_disk == body.as_bytes() {
            return Ok(format!("[ai-metadata] PASS: {CATALOG} is byte-identical to a fresh export ({entries} entries).\n"));
        }
        let mut msg =
            format!("[ai-metadata] FAIL: {CATALOG} is stale -- it differs from a fresh export.\n");
        let details = match serde_json::from_slice::<Value>(&on_disk) {
            Ok(tracked) => diff_entries(&tracked, &fresh),
            Err(e) => vec![format!("tracked file is not valid JSON: {e}")],
        };
        let details = if details.is_empty() {
            vec!["same parsed content, different bytes (formatting drift)".to_string()]
        } else {
            details
        };
        for line in details.iter().take(40) {
            msg.push_str(&format!("    {line}\n"));
        }
        if details.len() > 40 {
            msg.push_str(&format!("    ... and {} more\n", details.len() - 40));
        }
        msg.push_str(
            "  Regenerate with: mios-gen sync --root . (or mios-gen ai-metadata --root .)\n",
        );
        return Err(msg);
    }
    let target = export
        .map(Path::to_path_buf)
        .unwrap_or_else(|| root.join(CATALOG));
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent)
            .map_err(|e| format!("[ai-metadata] FAIL: {}: {e}\n", parent.display()))?;
    }
    crate::greeter::write_atomic(&target, &body)
        .map_err(|e| format!("[ai-metadata] FAIL: {e}\n"))?;
    Ok(format!(
        "[ai-metadata] Exported metadata catalog to {} ({entries} entries)\n",
        target.display()
    ))
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture(files: &[(&str, &str)]) -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        for (rel, body) in files {
            let p = dir.path().join(rel);
            fs::create_dir_all(p.parent().unwrap()).unwrap();
            fs::write(p, body).unwrap();
        }
        dir
    }

    #[test]
    fn json_matches_python_dumps_indent_2() {
        let v: Value =
            serde_json::json!({"a": [], "b": [1, {"c": null}], "d": "é\u{1F600}\u{7f}\"\\\n"});
        assert_eq!(
            render(&v),
            "{\n  \"a\": [],\n  \"b\": [\n    1,\n    {\n      \"c\": null\n    }\n  ],\n  \"d\": \"\\u00e9\\ud83d\\ude00\\u007f\\\"\\\\\\n\"\n}"
        );
    }

    #[test]
    fn splitlines_follows_python() {
        assert_eq!(
            splitlines("a\nb\r\nc\rd\u{0b}e\u{2028}f\n"),
            ["a", "b", "c", "d", "e", "f"]
        );
        assert_eq!(splitlines("a\n\nb"), ["a", "", "b"]);
        assert!(splitlines("").is_empty());
    }

    #[test]
    fn headers_are_read_like_the_python_leg() {
        let p = Patterns::new();
        let sample = format!("#!/bin/sh\n# ai-HINT:  Does a thing.  \n# AI-related: a, , b ,\n<!-- AI-{}: x.md -->\n", "doc");
        let e = extract(&p, &sample, "x/run.sh").unwrap();
        assert_eq!(e["hint"], "Does a thing.");
        assert_eq!(e["related"], serde_json::json!(["a", "b"]));
        assert_eq!(e["doc"], "x.md");
        assert_eq!(e["comment_style"], "hash");
        assert_eq!(e["has_shebang"], true);
        // A header past line 60 is not a header.
        let late = format!("{}# AI-hint: late\n", "\n".repeat(60));
        assert!(extract(&p, &late, "late.sh").is_none());
        // os.path.splitext: leading dots belong to the root, the last dot splits.
        assert_eq!(comment_style(".hidden.md"), "xml");
        assert_eq!(comment_style("a/.cshrc"), "hash");
        assert_eq!(comment_style("a/b.QML"), "slash");
    }

    #[test]
    fn check_fresh_names_the_moved_header_and_writes_nothing() {
        let dir = fixture(&[
            ("a.rs", "// AI-hint: one\n"),
            ("b.md", "<!-- AI-hint: two -->\n"),
        ]);
        // Without a git index the catalog counts itself, so the second export is the stable one.
        run(dir.path(), None, false).unwrap();
        let out = run(dir.path(), None, false).unwrap();
        assert!(out.contains("(2 entries)"), "{out}");
        assert!(run(dir.path(), None, true).unwrap().contains("PASS"));
        let committed = fs::read(dir.path().join(CATALOG)).unwrap();
        fs::write(dir.path().join("a.rs"), "// AI-hint: moved\n").unwrap();
        let err = run(dir.path(), None, true).unwrap_err();
        assert!(err.contains("entry a.rs: differs in hint"), "{err}");
        assert_eq!(
            fs::read(dir.path().join(CATALOG)).unwrap(),
            committed,
            "check-fresh wrote the catalog"
        );
    }
}
