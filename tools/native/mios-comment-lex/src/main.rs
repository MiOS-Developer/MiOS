// AI-hint: Native comment lexer for MiOS -- hot-path Rust lexer matching mios_comments.py.
use clap::Parser;
use regex::Regex;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fs;
use std::sync::OnceLock;

#[derive(Parser, Debug)]
#[command(author, version, about = "Native comment lexer for MiOS", long_about = None)]
struct Args {
    /// File to lex
    #[arg(short, long)]
    file: Option<String>,

    /// Positional file argument
    path: Option<String>,

    /// Check that git diff against base revision modifies comments only
    #[arg(long)]
    check_diff: Option<Option<String>>,

    /// Compare two files directly to prove identical non-comment token streams
    #[arg(long, num_args = 2)]
    diff_files: Option<Vec<String>>,
}

#[derive(Serialize, Deserialize, Debug, Clone, PartialEq)]
pub struct Block {
    pub path: String,
    pub start_line: usize,
    pub end_line: usize,
    pub kind: String,
    pub style: String,
    pub text: String,
    pub norm: String,
    pub sha12: String,
    pub lines: usize,
    pub words: usize,
    pub attach: String,
    pub anchor_code: String,
    pub in_header_block: bool,
    #[serde(default)]
    pub cls: String,
    #[serde(default)]
    pub reason: String,
    #[serde(default)]
    pub stale: bool,
    #[serde(default, rename = "as_")]
    pub as_: String,
}

/// The extension as Python's os.path.splitext sees it: leading dots belong to
/// the name, so `.bashrc` has none.
fn py_ext(path: &str) -> String {
    let base = &path[path.rfind('/').map_or(0, |i| i + 1)..];
    match base.rfind('.') {
        Some(d) if base[..d].chars().any(|c| c != '.') => base[d..].to_lowercase(),
        _ => String::new(),
    }
}

fn style_for(path: &str) -> &'static str {
    match py_ext(path).as_str() {
        ".rs" | ".go" | ".c" | ".h" | ".cs" | ".ts" | ".js" | ".tsx" | ".mjs" => "//",
        ".md" | ".html" | ".xml" => "<!--",
        _ => "#",
    }
}

/// Python's str.isspace(): Unicode White_Space plus U+001C..U+001F.
fn py_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

fn py_strip(s: &str) -> &str {
    s.trim_matches(py_space)
}

fn py_lstrip(s: &str) -> &str {
    s.trim_start_matches(py_space)
}

/// Python's str.splitlines(): every boundary it honours, with "\r\n" as one.
fn py_splitlines(s: &str) -> Vec<&str> {
    let mut out = Vec::new();
    let mut start = 0;
    let mut it = s.char_indices().peekable();
    while let Some((i, c)) = it.next() {
        if !matches!(
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
        ) {
            continue;
        }
        out.push(&s[start..i]);
        start = i + c.len_utf8();
        if c == '\r' {
            if let Some(&(j, '\n')) = it.peek() {
                it.next();
                start = j + 1;
            }
        }
    }
    if start < s.len() {
        out.push(&s[start..]);
    }
    out
}

/// `\s` spelled as Python's: Rust's class lacks U+001C..U+001F.
const WS: &str = r"[\s\x{1c}-\x{1f}]";

struct Res {
    marker: Regex,
    end_marker: Regex,
    html_end: Regex,
    ws_run: Regex,
    word: Regex,
}

fn res() -> &'static Res {
    static RES: OnceLock<Res> = OnceLock::new();
    RES.get_or_init(|| Res {
        marker: Regex::new(&format!(r"^{WS}*(?:#+|//+|;+|--|<!--|\*|/\*){WS}?"))
            .expect("marker pattern"),
        end_marker: Regex::new(&format!(r"{WS}*(?:--!?>|\*/){WS}*$")).expect("end pattern"),
        html_end: Regex::new(r"--!?>").expect("HTML end pattern"),
        ws_run: Regex::new(&format!("{WS}+")).expect("whitespace pattern"),
        word: Regex::new(r"[A-Za-z0-9_][A-Za-z0-9_./:-]*").expect("word pattern"),
    })
}

fn strip_line(line: &str) -> String {
    let re = res();
    let s1 = re.marker.replace(line, "");
    let s2 = re.end_marker.replace(&s1, "");
    s2.trim_end_matches(py_space).to_string()
}

fn ident_len(s: &str) -> usize {
    let b = s.as_bytes();
    if b.is_empty() || !(b[0].is_ascii_alphabetic() || b[0] == b'_') {
        return 0;
    }
    b.iter()
        .take_while(|c| c.is_ascii_alphanumeric() || **c == b'_')
        .count()
}

/// The terminator a `<<TAG` / `<<-'TAG'` / `<<"TAG"` opens, as mios_comments'
/// `_HEREDOC` finds it: the first `<<` at line start or after whitespace.
fn heredoc_tag(raw: &str) -> Option<String> {
    let mut from = 0;
    while let Some(off) = raw[from..].find("<<") {
        let p = from + off;
        from = p + 1;
        if raw[..p].chars().next_back().is_some_and(|c| !py_space(c)) {
            continue;
        }
        let rest = &raw[p + 2..];
        let rest = rest.strip_prefix('-').unwrap_or(rest);
        let rest = rest.trim_start_matches(py_space);
        let tag = match rest.chars().next() {
            Some(q @ ('\'' | '"')) => {
                let body = &rest[1..];
                let n = ident_len(body);
                (n > 0 && body[n..].starts_with(q)).then(|| body[..n].to_string())
            }
            _ => {
                let n = ident_len(rest);
                (n > 0).then(|| rest[..n].to_string())
            }
        };
        if tag.is_some() {
            return tag;
        }
    }
    None
}

/// Where a block sits: grouped so make_block stays inside clippy's
/// seven-argument limit without suppressing the lint.
struct Span<'a> {
    path: &'a str,
    start: usize,
    end: usize,
}

fn make_block(
    at: Span<'_>,
    kind: &str,
    style: &str,
    body_lines: &[String],
    attach: &str,
    anchor: &str,
    in_header: bool,
) -> Block {
    let re = res();
    let text = body_lines.join("\n");
    let lowered = text.to_lowercase();
    let norm = py_strip(&re.ws_run.replace_all(&lowered, " ")).to_string();

    let mut hasher = Sha256::new();
    hasher.update(norm.as_bytes());
    let hash_hex = format!("{:x}", hasher.finalize());
    let sha12 = hash_hex[..12].to_string();
    let words = re.word.find_iter(&text).count();

    Block {
        path: at.path.to_string(),
        start_line: at.start,
        end_line: at.end,
        kind: kind.to_string(),
        style: style.to_string(),
        text,
        norm,
        sha12,
        lines: body_lines.len(),
        words,
        attach: attach.to_string(),
        anchor_code: anchor.to_string(),
        in_header_block: in_header,
        cls: String::new(),
        reason: String::new(),
        stale: false,
        as_: String::new(),
    }
}

/// A run of full-line comments, attached to the first code line after it.
fn finish_run(at: Span<'_>, run: &[String], style: &str, lines: &[&str]) -> Block {
    let re = res();
    let anchor = lines
        .iter()
        .skip(at.end)
        .find(|l| !py_strip(l).is_empty() && !re.marker.is_match(l))
        .map_or("", |l| py_strip(l));
    let attach = if at.start <= 3 {
        "file-header"
    } else if anchor.is_empty() {
        "orphan"
    } else {
        "pre-code"
    };
    let in_header = at.start <= 6
        && run.iter().any(|x| {
            x.contains("AI-hint") || x.contains("AI-related") || x.contains("AI-functions")
        });
    make_block(at, "line", style, run, attach, anchor, in_header)
}

fn markup_block(path: &str, start: usize, end: usize, body: &[String], in_hdr: bool) -> Block {
    let attach = if start <= 3 { "file-header" } else { "orphan" };
    let at = Span { path, start, end };
    make_block(at, "blockcomment", "<!--", body, attach, "", in_hdr)
}

/// mios_comments._lex_generic, line for line: the drift gate holds them equal.
fn lex_generic(path: &str, src: &str, style: &str) -> Vec<Block> {
    let re = res();
    let lines = py_splitlines(src);
    let mut out = Vec::new();
    let mut run: Vec<String> = Vec::new();
    let mut run_start = 0;
    let mut in_block = false;
    let mut block_start = 0;
    let mut block_lines: Vec<String> = Vec::new();
    let mut heredoc_end: Option<String> = None;

    let flush = |out: &mut Vec<Block>, run: &mut Vec<String>, start: usize, end: usize| {
        if !run.is_empty() {
            out.push(finish_run(Span { path, start, end }, run, style, &lines));
            run.clear();
        }
    };

    for (idx, &raw) in lines.iter().enumerate() {
        let i = idx + 1;
        let s = py_strip(raw);

        // A heredoc body is data, not this file's comments.
        if let Some(end) = &heredoc_end {
            if s == end || s.strip_suffix('\'') == Some(end.as_str()) {
                heredoc_end = None;
            }
            continue;
        }
        if !py_lstrip(raw).starts_with(style) {
            if let Some(tag) = heredoc_tag(raw) {
                flush(&mut out, &mut run, run_start, i - 1);
                heredoc_end = Some(tag);
                continue;
            }
        }

        if style == "<!--" {
            if !in_block && s.starts_with("<!--") {
                in_block = true;
                block_start = i;
                block_lines = vec![strip_line(raw)];
                if res().html_end.is_match(s) {
                    in_block = false;
                    let hdr = raw.contains("AI-hint");
                    out.push(markup_block(path, block_start, i, &block_lines, hdr));
                }
            } else if in_block {
                block_lines.push(strip_line(raw));
                if res().html_end.is_match(s) {
                    in_block = false;
                    let hdr = block_lines.iter().any(|x| x.contains("AI-hint"));
                    out.push(markup_block(path, block_start, i, &block_lines, hdr));
                }
            }
            continue;
        }

        if !s.is_empty() && re.marker.is_match(raw) && py_lstrip(raw).starts_with(style) {
            if run.is_empty() {
                run_start = i;
            }
            run.push(strip_line(raw));
            continue;
        }

        // A trailing comment on a code line is inline, never a block.
        if (style == "#" || style == "//") && !py_lstrip(raw).starts_with(style) {
            if let Some(pos) = raw.find(style) {
                if pos > 0 && !py_strip(&raw[..pos]).is_empty() {
                    flush(&mut out, &mut run, run_start, i - 1);
                    let at = Span {
                        path,
                        start: i,
                        end: i,
                    };
                    let body = [strip_line(&raw[pos..])];
                    let anchor = py_strip(&raw[..pos]);
                    out.push(make_block(
                        at, "inline", style, &body, "inline", anchor, false,
                    ));
                    continue;
                }
            }
        }

        flush(&mut out, &mut run, run_start, i - 1);
    }
    flush(&mut out, &mut run, run_start, lines.len());

    out
}

/// Decoded as mios_comments.lex reads a file: lossy UTF-8, BOM dropped, LF.
pub fn lex_file(path: &str) -> Vec<Block> {
    let Ok(bytes) = fs::read(path) else {
        return Vec::new();
    };
    let content = String::from_utf8_lossy(&bytes);
    let src = content
        .strip_prefix('\u{feff}')
        .unwrap_or(&content)
        .replace("\r\n", "\n");
    lex_generic(path, &src, style_for(path))
}

/// Strips comments and docstrings from source text, returning normalized non-comment lines.
pub fn strip_non_code(path: &str, src: &str) -> Vec<String> {
    let lower = path.to_lowercase();
    let is_py = lower.ends_with(".py");
    let is_rs = lower.ends_with(".rs")
        || lower.ends_with(".go")
        || lower.ends_with(".c")
        || lower.ends_with(".h")
        || lower.ends_with(".cs")
        || lower.ends_with(".ts")
        || lower.ends_with(".js");
    let is_md = lower.ends_with(".md") || lower.ends_with(".html");

    let style = style_for(path);
    let lines: Vec<&str> = src.lines().collect();
    let mut out: Vec<String> = Vec::new();
    let mut in_multiline_comment = false;
    let mut in_py_docstring = false;
    let mut py_docstring_delim = "";

    for (idx, &line) in lines.iter().enumerate() {
        let trimmed = line.trim();

        // Python docstrings
        if is_py {
            if in_py_docstring {
                if trimmed.contains(py_docstring_delim) {
                    in_py_docstring = false;
                }
                continue;
            }
            if let Some(rest) = trimmed.strip_prefix("\"\"\"") {
                if !rest.is_empty() && !rest.contains("\"\"\"") {
                    in_py_docstring = true;
                    py_docstring_delim = "\"\"\"";
                }
                continue;
            } else if let Some(rest) = trimmed.strip_prefix("'''") {
                if !rest.is_empty() && !rest.contains("'''") {
                    in_py_docstring = true;
                    py_docstring_delim = "'''";
                }
                continue;
            }
        }

        // HTML / Markdown <!-- -->
        if is_md || style == "<!--" {
            if in_multiline_comment {
                if res().html_end.is_match(trimmed) {
                    in_multiline_comment = false;
                }
                continue;
            }
            if trimmed.starts_with("<!--") {
                if !res().html_end.is_match(trimmed) {
                    in_multiline_comment = true;
                }
                continue;
            }
        }

        // C / Rust style /* */
        if is_rs {
            if in_multiline_comment {
                if trimmed.contains("*/") {
                    in_multiline_comment = false;
                }
                continue;
            }
            if trimmed.starts_with("/*") {
                if !trimmed.contains("*/") {
                    in_multiline_comment = true;
                }
                continue;
            }
        }

        // Full line comment
        if trimmed.starts_with(style) {
            // Keep shebang line
            if idx == 0 && trimmed.starts_with("#!") {
                out.push(line.trim_end().to_string());
            }
            continue;
        }

        if trimmed.is_empty() {
            continue;
        }

        // Inline comment stripping
        if (style == "#" || style == "//") && line.contains(style) {
            let mut in_quote = false;
            let mut quote_char = ' ';
            let mut cut_pos = None;
            let chars: Vec<char> = line.chars().collect();
            let mut i = 0;
            while i < chars.len() {
                let c = chars[i];
                if (c == '"' || c == '\'') && (i == 0 || chars[i - 1] != '\\') {
                    if in_quote && c == quote_char {
                        in_quote = false;
                    } else if !in_quote {
                        in_quote = true;
                        quote_char = c;
                    }
                } else if !in_quote {
                    let hash = style == "#" && c == '#';
                    let slashes =
                        style == "//" && c == '/' && i + 1 < chars.len() && chars[i + 1] == '/';
                    if hash || slashes {
                        cut_pos = Some(i);
                        break;
                    }
                }
                i += 1;
            }
            if let Some(pos) = cut_pos {
                let code_part = line[..pos].trim_end();
                if !code_part.trim().is_empty() {
                    out.push(code_part.to_string());
                }
                continue;
            }
        }

        out.push(line.trim_end().to_string());
    }
    out
}

/// Checks whether the diff between old_src and new_src modifies comments only.
pub fn is_comment_only_diff(path: &str, old_src: &str, new_src: &str) -> (bool, Option<String>) {
    let old_tokens = strip_non_code(path, old_src);
    let new_tokens = strip_non_code(path, new_src);

    if old_tokens == new_tokens {
        return (true, None);
    }

    let max_len = old_tokens.len().max(new_tokens.len());
    for i in 0..max_len {
        let old_line = old_tokens.get(i).map(|s| s.as_str()).unwrap_or("<EOF>");
        let new_line = new_tokens.get(i).map(|s| s.as_str()).unwrap_or("<EOF>");
        if old_line != new_line {
            return (
                false,
                Some(format!(
                    "line {}: expected {:?}, found {:?}",
                    i + 1,
                    old_line,
                    new_line
                )),
            );
        }
    }
    (
        false,
        Some("non-comment token stream length mismatch".to_string()),
    )
}

/// Runs comment-only diff check against git base ref.
pub fn check_git_diff(root: &str, base: &str) -> (bool, Vec<String>) {
    let output = match std::process::Command::new("git")
        .arg("-C")
        .arg(root)
        .arg("diff")
        .arg("--name-only")
        .arg(base)
        .output()
    {
        Ok(o) => o,
        Err(e) => return (false, vec![format!("failed to run git diff: {}", e)]),
    };

    if !output.status.success() {
        return (
            false,
            vec![format!(
                "git diff failed: {}",
                String::from_utf8_lossy(&output.stderr)
            )],
        );
    }

    let stdout = String::from_utf8_lossy(&output.stdout);
    let files: Vec<&str> = stdout
        .lines()
        .map(|l| l.trim())
        .filter(|l| !l.is_empty())
        .collect();
    if files.is_empty() {
        return (true, Vec::new());
    }

    let mut violations = Vec::new();
    for file in files {
        let old_cmd = std::process::Command::new("git")
            .arg("-C")
            .arg(root)
            .arg("show")
            .arg(format!("{}:{}", base, file))
            .output();

        let old_content = match old_cmd {
            Ok(o) if o.status.success() => String::from_utf8_lossy(&o.stdout).to_string(),
            _ => continue, // New untracked or deleted file
        };

        let file_path = std::path::Path::new(root).join(file);
        let new_content = match fs::read_to_string(&file_path) {
            Ok(c) => c,
            Err(_) => continue,
        };

        let (ok, reason) = is_comment_only_diff(file, &old_content, &new_content);
        if !ok {
            violations.push(format!(
                "{}: modified executable code outside comments ({})",
                file,
                reason.unwrap_or_default()
            ));
        }
    }

    (violations.is_empty(), violations)
}

fn main() {
    let args = Args::parse();

    if let Some(ref diff_files) = args.diff_files {
        if diff_files.len() != 2 {
            eprintln!("Usage: mios-comment-lex --diff-files <FILE_A> <FILE_B>");
            std::process::exit(1);
        }
        let src_a = fs::read_to_string(&diff_files[0]).unwrap_or_default();
        let src_b = fs::read_to_string(&diff_files[1]).unwrap_or_default();
        let (ok, reason) = is_comment_only_diff(&diff_files[0], &src_a, &src_b);
        if ok {
            println!("[mios-comment-lex] diff-files clean: comment-only diff holds");
            std::process::exit(0);
        } else {
            eprintln!(
                "[mios-comment-lex] diff-files VIOLATION: {}",
                reason.unwrap_or_default()
            );
            std::process::exit(1);
        }
    }

    if let Some(ref base_opt) = args.check_diff {
        let base = base_opt.as_deref().unwrap_or("main");
        let (ok, violations) = check_git_diff(".", base);
        if ok {
            println!(
                "[mios-comment-lex] comment-only diff clean against {}",
                base
            );
            std::process::exit(0);
        } else {
            for v in &violations {
                eprintln!("[mios-comment-lex] VIOLATION: {}", v);
            }
            std::process::exit(1);
        }
    }

    let target = args.file.or(args.path);
    let path_str = match target {
        Some(p) => p,
        None => {
            eprintln!(
                "Usage: mios-comment-lex <FILE> | --check-diff [BASE] | --diff-files <A> <B>"
            );
            std::process::exit(1);
        }
    };

    let blocks = lex_file(&path_str);
    let json = serde_json::to_string_pretty(&blocks).unwrap();
    println!("{}", json);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_style_for() {
        assert_eq!(style_for("foo.py"), "#");
        assert_eq!(style_for("bar.rs"), "//");
        assert_eq!(style_for("baz.md"), "<!--");
    }

    #[test]
    fn test_strip_line() {
        assert_eq!(strip_line("# Hello world"), "Hello world");
        assert_eq!(strip_line("// Test line"), "Test line");
        assert_eq!(strip_line("<!-- Comment -->"), "Comment");
    }

    #[test]
    fn html_endings_bound_comments_without_swallowing_following_content() {
        for end in ["-->", "--!>"] {
            assert_eq!(strip_line(&format!("<!-- comment {end}")), "comment");
            let blocks = lex_generic(
                "x.md",
                &format!("<!-- first {end}\ntext\n<!-- second {end}\n"),
                "<!--",
            );
            assert_eq!(blocks.len(), 2);
            assert_eq!((blocks[0].start_line, blocks[0].end_line), (1, 1));
            assert_eq!(blocks[1].text, "second");
        }
        let blocks = lex_generic("x.md", "<!-- first --!\nsecond --!>\ntext\n", "<!--");
        assert_eq!(blocks.len(), 1);
        assert_eq!(blocks[0].end_line, 2);
        assert!(blocks[0].text.contains("first --!"));
    }

    #[test]
    fn test_lex_generic() {
        let src = "# Header comment\n# Second line\n\nfn main() {}\n";
        let blocks = lex_generic("test.py", src, "#");
        assert_eq!(blocks.len(), 1);
        assert_eq!(blocks[0].start_line, 1);
        assert_eq!(blocks[0].end_line, 2);
        assert_eq!(blocks[0].lines, 2);
        assert_eq!(blocks[0].kind, "line");
        assert_eq!(blocks[0].attach, "file-header");
    }

    #[test]
    fn heredoc_body_is_not_a_comment() {
        let src = "# outer\ncat <<'EOF' >x\n# inner\nEOF\n# after\n";
        let texts: Vec<String> = lex_generic("t.sh", src, "#")
            .into_iter()
            .map(|b| b.text)
            .collect();
        assert_eq!(texts, ["outer", "after"]);
    }

    #[test]
    fn heredoc_tag_forms() {
        assert_eq!(heredoc_tag("cat <<-\"SQL\"").as_deref(), Some("SQL"));
        assert_eq!(heredoc_tag("x <<EOF").as_deref(), Some("EOF"));
        assert_eq!(heredoc_tag("1<<bit"), None);
        assert_eq!(heredoc_tag("cat <<< \"$x\""), None);
        assert_eq!(heredoc_tag("cat <<'open"), None);
    }

    #[test]
    fn comment_line_never_opens_a_heredoc() {
        let src = "# usage: cat <<EOF\n\n# kept\n";
        assert_eq!(lex_generic("t.sh", src, "#").len(), 2);
    }

    #[test]
    fn splitlines_matches_python() {
        assert_eq!(py_splitlines("a\r\nb\rc\x0cd\n"), ["a", "b", "c", "d"]);
        assert_eq!(py_splitlines("\n"), [""]);
        assert!(py_splitlines("").is_empty());
    }

    #[test]
    fn bom_is_dropped_and_anchor_found() {
        let dir = std::env::temp_dir().join(format!("mios-lex-{}", std::process::id()));
        std::fs::create_dir_all(&dir).expect("tempdir");
        let f = dir.join("a.ps1");
        std::fs::write(
            &f,
            "\u{feff}# AI-hint: x\r\n\r\n\r\n\r\n# note\r\nWrite-Host 1\r\n",
        )
        .expect("fixture");
        let blocks = lex_file(f.to_str().expect("utf-8 path"));
        std::fs::remove_dir_all(&dir).ok();
        assert_eq!(blocks[0].start_line, 1);
        assert!(blocks[0].in_header_block);
        assert_eq!(blocks[1].attach, "pre-code");
        assert_eq!(blocks[1].anchor_code, "Write-Host 1");
    }

    #[test]
    fn extension_follows_splitext() {
        assert_eq!(py_ext("d/.bashrc"), "");
        assert_eq!(py_ext("d/a.b.RS"), ".rs");
        assert_eq!(style_for("d/x.tsx"), "//");
    }

    #[test]
    fn test_comment_only_diff_positive_control() {
        let old_py = "#!/usr/bin/env python3\n# AI-hint: old hint\ndef foo():\n    \"\"\"Old doc.\"\"\"\n    return 42 # inline comment\n";
        let new_py = "#!/usr/bin/env python3\n# AI-hint: updated new hint\ndef foo():\n    \"\"\"New updated docstring.\"\"\"\n    return 42 # changed inline comment\n";
        let (ok, reason) = is_comment_only_diff("test.py", old_py, new_py);
        assert!(ok, "Expected positive control to pass: {:?}", reason);
    }

    #[test]
    fn test_comment_only_diff_negative_control_runtime_edit() {
        let old_py = "#!/usr/bin/env python3\ndef foo():\n    return 42\n";
        let new_py = "#!/usr/bin/env python3\n# AI-hint: added hint\ndef foo():\n    return 43\n";
        let (ok, reason) = is_comment_only_diff("test.py", old_py, new_py);
        assert!(
            !ok,
            "Expected negative control to fail when runtime code changes"
        );
        assert!(reason.unwrap().contains("return 42"));
    }
}
