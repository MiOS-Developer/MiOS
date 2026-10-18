// AI-hint: Compiled Rust implementation of template conformance checker; --llms-txt instead validates <root>/llms.txt against the llmstxt.org format.
// AI-related: /usr/libexec/mios/check-template-conformance, /usr/share/mios/mios.toml, llms.txt

use regex::Regex;
use std::collections::{HashMap, HashSet};
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use walkdir::WalkDir;

/// Template-governed source files are text; anything larger is reported, never read whole.
const MAX_CHECKED_FILE_BYTES: u64 = 8 * 1024 * 1024;

struct CompiledTemplate {
    name: String,
    pattern: Regex,
    required_header: bool,
    required_markers: Vec<String>,
    required_ordered: Vec<String>,
}

fn load_grandfathered(root: &Path) -> HashSet<String> {
    let gf_path = root.join("usr/share/mios/templates/conformance-grandfathered.list");
    if let Ok(content) = fs::read_to_string(&gf_path) {
        content
            .lines()
            .map(|l| l.trim().to_string())
            .filter(|l| !l.is_empty() && !l.starts_with('#'))
            .collect()
    } else {
        HashSet::new()
    }
}

const USAGE: &str = "usage: mios-template-conform [--root DIR] [--max-unconforming N | --llms-txt]";

#[derive(Debug, PartialEq)]
struct Cli {
    root: String,
    ceiling: Option<usize>,
    llms_txt: bool,
}

// An unknown argument is an error, not a no-op: when this loop ignored them,
// `--llms-txt` ran the template walk instead and exited 0 on any tree, so a
// mode that did not exist read exactly like a passing one.
fn parse_cli(args: &[String], default_root: String) -> Result<Cli, String> {
    let mut cli = Cli {
        root: default_root,
        ceiling: None,
        llms_txt: false,
    };
    let mut it = args.iter();
    while let Some(arg) = it.next() {
        match arg.as_str() {
            "--root" => cli.root = it.next().ok_or("--root needs a directory")?.clone(),
            "--max-unconforming" => {
                let n = it.next().ok_or("--max-unconforming needs a number")?;
                cli.ceiling = Some(
                    n.parse()
                        .map_err(|_| format!("--max-unconforming: {n:?} is not a number"))?,
                );
            }
            "--llms-txt" => cli.llms_txt = true,
            other => return Err(format!("unknown argument {other:?}")),
        }
    }
    if cli.llms_txt && cli.ceiling.is_some() {
        return Err("--max-unconforming has no meaning with --llms-txt".to_string());
    }
    Ok(cli)
}

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    if args.iter().any(|a| a == "-h" || a == "--help") {
        println!("{USAGE}");
        return;
    }
    let default_root = env::var("MIOS_THEME_ROOT").unwrap_or_else(|_| ".".to_string());
    let cli = match parse_cli(&args, default_root) {
        Ok(cli) => cli,
        Err(e) => {
            eprintln!("mios-template-conform: {e}");
            eprintln!("{USAGE}");
            std::process::exit(2);
        }
    };

    let root_path = PathBuf::from(&cli.root);
    if cli.llms_txt {
        std::process::exit(run_llms_txt(&root_path));
    }
    match run_conformance(&root_path, cli.ceiling) {
        Ok(_) => std::process::exit(0),
        Err(e) => {
            eprintln!("FAIL: {e}");
            std::process::exit(1);
        }
    }
}

/// Files the conformance check considers. In a git work tree that is exactly what the tree tracks
/// plus untracked-but-not-ignored files (the repository may be checked out at `/`, where a walk
/// would cover the whole operating system); elsewhere a walk that stays on the root's filesystem.
fn candidate_files(root: &Path, ignore_dirs: &HashSet<&str>) -> Vec<PathBuf> {
    let toplevel = std::process::Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["rev-parse", "--show-toplevel"])
        .stderr(std::process::Stdio::null())
        .output()
        .ok()
        .filter(|o| o.status.success())
        .map(|o| PathBuf::from(String::from_utf8_lossy(&o.stdout).trim()));
    let is_toplevel = match (toplevel, fs::canonicalize(root)) {
        (Some(top), Ok(r)) => fs::canonicalize(&top).map(|t| t == r).unwrap_or(false),
        _ => false,
    };
    let git = std::process::Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["ls-files", "-z", "--cached", "--others", "--exclude-standard"])
        .stderr(std::process::Stdio::null())
        .output();
    if let (true, Ok(out)) = (is_toplevel, git) {
        if out.status.success() {
            return out
                .stdout
                .split(|b| *b == 0)
                .filter(|r| !r.is_empty())
                .map(|r| root.join(String::from_utf8_lossy(r).as_ref()))
                .collect();
        }
    }
    WalkDir::new(root)
        .same_file_system(true)
        .into_iter()
        .filter_entry(|e| !ignore_dirs.contains(e.file_name().to_str().unwrap_or("")))
        .filter_map(|e| e.ok())
        .filter(|e| e.file_type().is_file())
        .map(|e| e.into_path())
        .collect()
}

fn run_conformance(
    root_path: &Path,
    cli_ceiling: Option<usize>,
) -> Result<(usize, usize, usize), String> {
    let config_path = root_path.join("usr/share/mios/mios.toml");
    let content = fs::read_to_string(&config_path)
        .map_err(|e| format!("cannot read {}: {e}", config_path.display()))?;
    let val: toml::Value = toml::from_str(&content)
        .map_err(|e| format!("cannot parse {}: {e}", config_path.display()))?;

    let mut default_ceiling: usize = 0;
    if let Some(ai_tag) = val.get("ai_tag") {
        if let Some(max_u) = ai_tag.get("max_unconforming").and_then(|v| v.as_integer()) {
            default_ceiling = max_u as usize;
        }
    }

    let tmpls = match val.get("templates").and_then(|v| v.as_table()) {
        Some(t) if !t.is_empty() => t,
        _ => {
            return Err(format!(
                "[templates] section in {} is missing or empty",
                config_path.display()
            ))
        }
    };

    let mut compiled_templates: Vec<CompiledTemplate> = Vec::new();
    for (tname, tval) in tmpls {
        if tname == "placeholders" {
            continue;
        }
        let tcfg = serde_json::to_value(tval)
            .map_err(|e| format!("template '{tname}' serialization error: {e}"))?;
        let match_str = match tcfg.get("match").and_then(|v| v.as_str()) {
            Some(s) if !s.is_empty() => s,
            _ => return Err(format!("template '{tname}' is missing a non-empty 'match' regex")),
        };
        let re = Regex::new(match_str)
            .map_err(|e| format!("template '{tname}' has invalid regex {match_str:?}: {e}"))?;
        let required_header = tcfg
            .get("required_header")
            .and_then(|v| v.as_bool())
            .unwrap_or(true);
        let mut required_markers = Vec::new();
        if let Some(arr) = tcfg.get("required_markers").and_then(|v| v.as_array()) {
            for m in arr {
                if let Some(s) = m.as_str() {
                    required_markers.push(s.to_string());
                }
            }
        }
        let mut required_ordered = Vec::new();
        if let Some(arr) = tcfg.get("required_ordered").and_then(|v| v.as_array()) {
            for m in arr {
                if let Some(s) = m.as_str() {
                    required_ordered.push(s.to_string());
                }
            }
        }
        compiled_templates.push(CompiledTemplate {
            name: tname.clone(),
            pattern: re,
            required_header,
            required_markers,
            required_ordered,
        });
    }

    if compiled_templates.is_empty() {
        return Err("no template compiled from [templates]".to_string());
    }

    let ceiling = cli_ceiling.unwrap_or(default_ceiling);
    let grandfathered = load_grandfathered(root_path);

    let mut unconforming: Vec<(String, String)> = Vec::new();
    let mut checked_count = 0;

    let ignore_dirs: HashSet<&str> = [
        ".git",
        "node_modules",
        "target",
        ".venv",
        "dist",
        "build",
        ".system_generated",
    ]
    .iter()
    .cloned()
    .collect();

    // Never read the whole operating system: candidate_files stays within the repository (or the
    // root's filesystem), and only regular files a template governs are read, within a size bound.
    // Reading /proc/kcore or VM images whole once drove this tool to 34 GB and got the operator's
    // session OOM-killed.
    for p in candidate_files(root_path, &ignore_dirs) {
        let p = p.as_path();
        let rel_path = match p.strip_prefix(root_path) {
            Ok(rel) => rel.to_string_lossy().replace('\\', "/"),
            Err(_) => continue,
        };

        if grandfathered.contains(&rel_path) {
            continue;
        }
        let meta = match fs::symlink_metadata(p) {
            Ok(m) if m.file_type().is_file() => m,
            _ => continue, // deleted in the work tree, a symlink, or not a regular file
        };

        checked_count += 1;
        if !compiled_templates.iter().any(|ct| ct.pattern.is_match(&rel_path)) {
            continue;
        }
        if meta.len() > MAX_CHECKED_FILE_BYTES {
            unconforming.push((
                rel_path.clone(),
                format!("File too large to check ({} bytes > {MAX_CHECKED_FILE_BYTES})", meta.len()),
            ));
            continue;
        }
        let content = match fs::read_to_string(p) {
            Ok(c) => c,
            Err(_) => continue,
        };

        let head_end = content
            .char_indices()
            .map(|(i, _)| i)
            .chain(std::iter::once(content.len()))
            .take_while(|i| *i <= 8192)
            .last()
            .unwrap_or(0);
        let head = &content[..head_end];

        for ct in &compiled_templates {
            if ct.pattern.is_match(&rel_path) {
                if ct.required_header && !head.contains("AI-hint:") {
                    unconforming.push((
                        rel_path.clone(),
                        format!("Missing AI-hint header (required by {})", ct.name),
                    ));
                    break;
                }
                let mut marker_failed = false;
                for marker in &ct.required_markers {
                    if !content.contains(marker) {
                        unconforming.push((
                            rel_path.clone(),
                            format!(
                                "Missing required body marker {:?} (required by {})",
                                marker, ct.name
                            ),
                        ));
                        marker_failed = true;
                        break;
                    }
                }
                if marker_failed {
                    break;
                }
                let mut last_pos = 0;
                for marker in &ct.required_ordered {
                    if let Some(pos) = content[last_pos..].find(marker) {
                        last_pos += pos + marker.len();
                    } else {
                        unconforming.push((
                            rel_path.clone(),
                            format!(
                                "Out-of-order or missing required ordered marker {:?} (required by {})",
                                marker, ct.name
                            ),
                        ));
                        break;
                    }
                }
                break;
            }
        }
    }

    if checked_count == 0 {
        return Err(format!("no files checked in {} (checked=0)", root_path.display()));
    }

    println!(
        "[conformance] checked={} unconforming={} ceiling={}",
        checked_count,
        unconforming.len(),
        ceiling
    );

    if !unconforming.is_empty() {
        eprintln!("Non-conforming files:");
        for (p, err) in &unconforming {
            eprintln!("    {}: {}", p, err);
        }
    }

    if unconforming.len() > ceiling {
        return Err(format!(
            "{} unconforming files exceeds ceiling {}",
            unconforming.len(),
            ceiling
        ));
    }

    Ok((checked_count, unconforming.len(), ceiling))
}

// --- llms.txt (https://llmstxt.org/) ---
// Checked as the reference parser reads it: a section starts at any line
// beginning `##`, even inside a fence, and each line under an H2 must be a
// `- [name](url)` item. Stricter than the spec: the summary is required and
// relative links must exist.

#[derive(Clone, Copy, PartialEq)]
enum LlmsPhase {
    BeforeH1,
    AfterH1,
    Summary,
    Details,
    Sections,
}

struct LlmsSection {
    name: String,
    line: usize,
    items: usize,
    bad: Vec<(usize, &'static str)>,
}

#[derive(Debug)]
struct LlmsTxtSummary {
    title: String,
    sections: usize,
    links: usize,
}

/// Leading spaces when there are at most three, CommonMark's block indent.
fn block_indent(line: &str) -> Option<usize> {
    let n = line.len() - line.trim_start_matches(' ').len();
    (n <= 3).then_some(n)
}

/// An ATX heading as (indent, level, text).
fn atx_heading(line: &str) -> Option<(usize, usize, String)> {
    let indent = block_indent(line)?;
    let rest = &line[indent..];
    let level = rest.len() - rest.trim_start_matches('#').len();
    let after = &rest[level..];
    if level == 0 || level > 6 || !(after.is_empty() || after.starts_with([' ', '\t'])) {
        return None;
    }
    let mut text = after.trim();
    let open = text.trim_end_matches('#');
    if open.len() < text.len() && (open.is_empty() || open.ends_with([' ', '\t'])) {
        text = open.trim_end();
    }
    Some((indent, level, text.to_string()))
}

/// An opening code fence as (fence character, run length).
fn fence_open(line: &str) -> Option<(char, usize)> {
    let rest = &line[block_indent(line)?..];
    let ch = rest.chars().next().filter(|c| *c == '`' || *c == '~')?;
    let run = rest.len() - rest.trim_start_matches(ch).len();
    (run >= 3 && !(ch == '`' && rest[run..].contains('`'))).then_some((ch, run))
}

fn fence_closes(line: &str, ch: char, run: usize) -> bool {
    block_indent(line).is_some_and(|i| {
        let rest = &line[i..];
        let n = rest.len() - rest.trim_start_matches(ch).len();
        n >= run && rest[n..].trim().is_empty()
    })
}

/// `Some(closed on the same line)` when the line opens an HTML comment block.
fn comment_open(line: &str) -> Option<bool> {
    let rest = &line[block_indent(line)?..];
    rest.starts_with("<!--").then(|| rest[2..].contains("-->"))
}

fn blockquote_line(line: &str) -> bool {
    block_indent(line).is_some_and(|i| line[i..].starts_with('>'))
}

fn list_item_start(line: &str) -> bool {
    let Some(i) = block_indent(line) else {
        return false;
    };
    let rest = &line[i..];
    let marker = if rest.starts_with(['-', '*', '+']) {
        1
    } else {
        let digits = rest.len() - rest.trim_start_matches(|c: char| c.is_ascii_digit()).len();
        if digits == 0 || digits > 9 || !rest[digits..].starts_with(['.', ')']) {
            return false;
        }
        digits + 1
    };
    rest[marker..].is_empty() || rest[marker..].starts_with([' ', '\t'])
}

fn setext_underline(line: &str) -> bool {
    block_indent(line).is_some_and(|i| {
        let rest = line[i..].trim_end();
        !rest.is_empty() && (rest.bytes().all(|b| b == b'=') || rest.bytes().all(|b| b == b'-'))
    })
}

/// `AI-hint` for a heading that reads `AI-hint: ...` -- a source-header tag
/// that a `#` comment prefix turned into an H1.
fn header_tag(text: &str) -> Option<&str> {
    let (tag, _) = text.split_once(':')?;
    (tag.starts_with("AI-") && !tag.contains(char::is_whitespace)).then_some(tag)
}

/// A file-list item, `- [name](url)` optionally followed by `: notes`, as (name, url).
fn parse_link_item(line: &str) -> Result<(&str, &str), &'static str> {
    if line.starts_with([' ', '\t']) {
        return Err("file-list items start at column 0 with `- `");
    }
    let Some(rest) = line
        .strip_prefix('-')
        .filter(|r| r.starts_with([' ', '\t']))
    else {
        return Err("not a `- [name](url)` file-list item");
    };
    let Some(rest) = rest.trim_start().strip_prefix('[') else {
        return Err("the list item does not open with a `[name](url)` link");
    };
    let (name, rest) = rest
        .split_once(']')
        .ok_or("the link name has no closing `]`")?;
    let rest = rest
        .strip_prefix('(')
        .ok_or("`[name]` is not followed by `(url)`")?;
    let (url, tail) = rest
        .split_once(')')
        .ok_or("the link URL has no closing `)`")?;
    if name.trim().is_empty() {
        return Err("the link name is empty");
    }
    if url.is_empty() || url.contains(char::is_whitespace) {
        return Err("the link URL is empty or contains whitespace");
    }
    let tail = tail.trim_end();
    if !tail.is_empty() {
        match tail.strip_prefix(':') {
            Some(notes) if !notes.trim().is_empty() => {}
            Some(_) => return Err("the `:` after the link has no notes"),
            None => return Err("text after the link must follow a `:`"),
        }
    }
    Ok((name, url))
}

/// A relative link must name a path under the root. A URL with a scheme is not
/// fetched; a root-absolute path, or one climbing out with `..`, leaves the
/// repository wherever the file is served from.
fn check_link_target(url: &str, root: &Path) -> Result<(), String> {
    let has_scheme = url.split_once(':').is_some_and(|(s, _)| {
        s.starts_with(|c: char| c.is_ascii_alphabetic())
            && s.chars()
                .all(|c| c.is_ascii_alphanumeric() || "+-.".contains(c))
    });
    if has_scheme || url.starts_with('#') {
        return Ok(());
    }
    if url.starts_with('/') {
        return Err(format!(
            "link `{url}` is root-absolute; use a repo-relative path or a full URL"
        ));
    }
    let path = url.split(['#', '?']).next().unwrap_or_default();
    if path.split('/').any(|c| c == "..") {
        return Err(format!("link `{url}` climbs out of the tree with `..`"));
    }
    if !root.join(path).exists() {
        return Err(format!("link target `{path}` does not exist"));
    }
    Ok(())
}

/// The format, in order: optional BOM, the H1 naming the project, a `>`
/// summary, details holding no heading, then H2 file lists.
fn check_llms_txt(text: &str, root: &Path) -> Result<LlmsTxtSummary, Vec<String>> {
    let text = text.strip_prefix('\u{feff}').unwrap_or(text);
    let mut v: Vec<String> = Vec::new();
    let mut phase = LlmsPhase::BeforeH1;
    let mut fence: Option<(char, usize)> = None;
    let mut in_comment = false;
    // A setext underline only counts under paragraph text that did not open as
    // a list item or blockquote; under those it is a thematic break.
    let mut plain_para = false;
    let mut container_para = false;
    let mut h1s: Vec<(usize, String)> = Vec::new();
    let mut stray_reported = false;
    let mut sections: Vec<LlmsSection> = Vec::new();
    let mut links = 0;

    for (idx, line) in text.lines().enumerate() {
        let n = idx + 1;
        let blank = line.trim().is_empty();

        if phase == LlmsPhase::Sections {
            if line.starts_with('#') {
                if let Some((0, 2, name)) = atx_heading(line).filter(|h| !h.2.is_empty()) {
                    sections.push(LlmsSection {
                        name,
                        line: n,
                        items: 0,
                        bad: Vec::new(),
                    });
                    continue;
                }
            }
            if blank {
                continue;
            }
            let Some(sec) = sections.last_mut() else {
                continue;
            };
            if line.starts_with('#') {
                sec.bad
                    .push((n, "only `## Name` headings belong among the file lists"));
                continue;
            }
            match parse_link_item(line) {
                Ok((_, url)) => {
                    sec.items += 1;
                    links += 1;
                    if let Err(e) = check_link_target(url, root) {
                        v.push(format!("line {n}: {e}"));
                    }
                }
                Err(why) => sec.bad.push((n, why)),
            }
            continue;
        }

        // Fence and comment interiors are opaque to Markdown, but a
        // line-oriented parser still splits a section at `##` inside them.
        if let Some((ch, run)) = fence {
            if line.starts_with("##") {
                v.push(format!(
                    "line {n}: starts with `##` inside a code fence; llms.txt parsers split a section there"
                ));
            }
            if fence_closes(line, ch, run) {
                fence = None;
            }
            continue;
        }
        if in_comment {
            if line.starts_with("##") {
                v.push(format!(
                    "line {n}: starts with `##` inside an HTML comment; llms.txt parsers split a section there"
                ));
            }
            in_comment = !line.contains("-->");
            continue;
        }

        match phase {
            LlmsPhase::BeforeH1 => {
                if blank {
                    continue;
                }
                if let Some(closed) = comment_open(line) {
                    in_comment = !closed;
                    continue;
                }
                if let Some((indent, 1, text)) = atx_heading(line) {
                    if indent > 0 {
                        v.push(format!(
                            "line {n}: the H1 is indented; llms.txt parsers read `# ` at column 0 only"
                        ));
                    }
                    if text.is_empty() {
                        v.push(format!("line {n}: the H1 is empty; it names the project"));
                    }
                    h1s.push((n, text));
                    phase = LlmsPhase::AfterH1;
                    continue;
                }
                if !stray_reported {
                    v.push(format!(
                        "line {n}: content before the H1; only blank lines and HTML comments may precede it"
                    ));
                    stray_reported = true;
                }
                if let Some(f) = fence_open(line) {
                    fence = Some(f);
                }
                continue;
            }
            LlmsPhase::AfterH1 => {
                if blank {
                    continue;
                }
                if blockquote_line(line) {
                    phase = LlmsPhase::Summary;
                    continue;
                }
                v.push(format!(
                    "line {n}: the H1 must be followed by a `>` blockquote summary"
                ));
                phase = LlmsPhase::Details;
            }
            LlmsPhase::Summary => {
                if blockquote_line(line) {
                    continue;
                }
                phase = LlmsPhase::Details;
            }
            LlmsPhase::Details | LlmsPhase::Sections => {}
        }

        if blank {
            plain_para = false;
            container_para = false;
            continue;
        }
        if let Some(f) = fence_open(line) {
            fence = Some(f);
            plain_para = false;
            container_para = false;
            continue;
        }
        if let Some(closed) = comment_open(line) {
            in_comment = !closed;
            plain_para = false;
            container_para = false;
            continue;
        }
        if line.starts_with("##") {
            match atx_heading(line) {
                Some((0, 2, name)) if !name.is_empty() => {
                    phase = LlmsPhase::Sections;
                    sections.push(LlmsSection {
                        name,
                        line: n,
                        items: 0,
                        bad: Vec::new(),
                    });
                }
                Some((_, level, _)) if level > 2 => v.push(format!(
                    "line {n}: an H{level} heading; llms.txt has no headings below H2 (write a **bold lead** paragraph)"
                )),
                _ => v.push(format!(
                    "line {n}: starts with `##` but is not a `## Name` heading; llms.txt parsers split a section there"
                )),
            }
            plain_para = false;
            container_para = false;
            continue;
        }
        if let Some((_, level, text)) = atx_heading(line) {
            if level == 1 {
                h1s.push((n, text));
            } else {
                v.push(format!(
                    "line {n}: an indented H{level} heading; the details before the first H2 hold no headings"
                ));
            }
            plain_para = false;
            container_para = false;
            continue;
        }
        if plain_para && setext_underline(line) {
            v.push(format!(
                "line {n}: this underline makes line {} a setext heading; the details before the first H2 hold no headings (leave a blank line before a thematic break)",
                n - 1
            ));
            plain_para = false;
            continue;
        }
        if list_item_start(line) || blockquote_line(line) {
            container_para = true;
            plain_para = false;
        } else {
            let rest = line.trim_start();
            plain_para = !container_para && !rest.starts_with('<') && !rest.starts_with('|');
        }
    }

    if fence.is_some() {
        v.push("the file ends inside a code fence".to_string());
    }
    if in_comment {
        v.push("the file ends inside an HTML comment".to_string());
    }
    match phase {
        LlmsPhase::BeforeH1 => {
            v.push("no H1: `# Name` naming the project is the one required part".to_string())
        }
        LlmsPhase::AfterH1 => {
            v.push("the H1 must be followed by a `>` blockquote summary".to_string())
        }
        _ => {}
    }
    for (n, text) in &h1s {
        if let Some(tag) = header_tag(text) {
            v.push(format!(
                "line {n}: `# {tag}:` is a source-header tag, not a heading; header tags go in a leading `<!-- ... -->` comment"
            ));
        }
    }
    if h1s.len() > 1 {
        let at: Vec<String> = h1s.iter().map(|(n, _)| n.to_string()).collect();
        v.push(format!(
            "{} H1 headings (lines {}); the project name is the only H1",
            h1s.len(),
            at.join(", ")
        ));
    }

    let mut first_seen: HashMap<&str, usize> = HashMap::new();
    for s in &sections {
        match first_seen.get(s.name.as_str()) {
            Some(first) => v.push(format!(
                "line {}: section {:?} repeats the one at line {first}; parsers keep only one of them",
                s.line, s.name
            )),
            None => {
                first_seen.insert(&s.name, s.line);
            }
        }
        if !s.bad.is_empty() {
            v.push(format!(
                "section {:?} (line {}): {} line(s) are not `- [name](url): notes` items; an H2 section holds a file list only, and prose belongs above the first H2 as a **bold lead** paragraph",
                s.name,
                s.line,
                s.bad.len()
            ));
            for (n, why) in s.bad.iter().take(3) {
                v.push(format!("line {n}: {why}"));
            }
        } else if s.items == 0 {
            v.push(format!(
                "line {}: section {:?} lists no files",
                s.line, s.name
            ));
        }
    }

    if v.is_empty() {
        Ok(LlmsTxtSummary {
            title: h1s.into_iter().next().map(|(_, t)| t).unwrap_or_default(),
            sections: sections.len(),
            links,
        })
    } else {
        Err(v)
    }
}

/// Validates `<root>/llms.txt`. A missing file fails: this mode is asked for by
/// name, so there is no tree in which skipping it would be correct.
fn run_llms_txt(root: &Path) -> i32 {
    let path = root.join("llms.txt");
    let text = match fs::read_to_string(&path) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("FAIL: cannot read {}: {e}", path.display());
            return 1;
        }
    };
    match check_llms_txt(&text, root) {
        Ok(s) => {
            println!(
                "[llms-txt] {} conforms to https://llmstxt.org/: H1 {:?}, {} sections, {} links",
                path.display(),
                s.title,
                s.sections,
                s.links
            );
            0
        }
        Err(violations) => {
            println!(
                "[llms-txt] {}: {} violation(s)",
                path.display(),
                violations.len()
            );
            for v in &violations {
                eprintln!("    {v}");
            }
            eprintln!(
                "FAIL: {} does not follow the llms.txt format (https://llmstxt.org/)",
                path.display()
            );
            1
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_load_grandfathered_nonexistent() {
        let root = Path::new("/nonexistent_dir_12345");
        let gf = load_grandfathered(root);
        assert!(gf.is_empty());
    }

    #[test]
    fn test_load_grandfathered_mock() {
        let dir = std::env::temp_dir().join("mios_tmpl_conform_test");
        let gf_dir = dir.join("usr/share/mios/templates");
        let _ = fs::create_dir_all(&gf_dir);
        let gf_path = gf_dir.join("conformance-grandfathered.list");
        fs::write(&gf_path, "file1.py\nfile2.sh\n# comment\n\nfile3.rs\n").unwrap();

        let gf = load_grandfathered(&dir);
        assert!(gf.contains("file1.py"));
        assert!(gf.contains("file2.sh"));
        assert!(gf.contains("file3.rs"));
        assert!(!gf.contains("# comment"));

        let _ = fs::remove_file(gf_path);
        let _ = fs::remove_dir_all(dir);
    }

    fn args(list: &[&str]) -> Vec<String> {
        list.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn parse_cli_reads_known_arguments() {
        let cli = parse_cli(&args(&["--root", "/r", "--llms-txt"]), ".".into()).unwrap();
        assert_eq!(
            cli,
            Cli {
                root: "/r".into(),
                ceiling: None,
                llms_txt: true,
            }
        );
        let cli = parse_cli(&args(&["--max-unconforming", "4"]), "/d".into()).unwrap();
        assert_eq!(cli.ceiling, Some(4));
        assert_eq!(cli.root, "/d");
    }

    #[test]
    fn parse_cli_rejects_what_it_used_to_ignore() {
        let err = |a: &[&str]| parse_cli(&args(a), ".".into()).unwrap_err();
        assert!(err(&["--llm-txt"]).contains("unknown argument \"--llm-txt\""));
        assert!(err(&["--root"]).contains("--root needs a directory"));
        assert!(err(&["--max-unconforming"]).contains("needs a number"));
        assert!(err(&["--max-unconforming", "x"]).contains("is not a number"));
        assert!(err(&["--llms-txt", "--max-unconforming", "1"]).contains("no meaning"));
    }

    #[test]
    fn walk_reads_only_governed_files_within_the_size_bound() {
        // Regression: a live `--root /` once read /proc/kcore and VM images whole (34 GB, OOM).
        let dir = std::env::temp_dir().join(format!("mios_conform_bound_{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(dir.join("usr/share/mios")).unwrap();
        fs::create_dir_all(dir.join("src")).unwrap();
        fs::create_dir_all(dir.join("data")).unwrap();
        fs::write(
            dir.join("usr/share/mios/mios.toml"),
            "[ai_tag]\nmax_unconforming = 10\n[templates.py]\nmatch = '^src/.*\\.py$'\n",
        )
        .unwrap();
        fs::write(dir.join("src/ok.py"), "# AI-hint: fine\n").unwrap();
        // ungoverned 4 GiB sparse file: must never be read (it would take 4 GiB of memory)
        fs::File::create(dir.join("data/disk.img")).unwrap().set_len(4 << 30).unwrap();
        // governed but oversized sparse file: reported, not read
        fs::File::create(dir.join("src/huge.py")).unwrap().set_len(9 << 20).unwrap();
        let (checked, unconforming, _) = run_conformance(&dir, None).unwrap();
        assert_eq!(checked, 4, "every regular file is counted");
        assert_eq!(unconforming, 1, "only the oversized governed file is flagged");
        let _ = fs::remove_dir_all(&dir);
    }

    /// A scratch root holding `files`; a trailing `/` makes a directory.
    fn fixture(tag: &str, files: &[&str]) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("mios_llms_{tag}_{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        for f in files {
            let p = dir.join(f);
            if f.ends_with('/') {
                fs::create_dir_all(&p).unwrap();
            } else {
                fs::create_dir_all(p.parent().unwrap()).unwrap();
                fs::write(&p, "x").unwrap();
            }
        }
        dir
    }

    const GOOD: &str = "\
<!-- AI-hint: a demo index
     AI-related: demo -->
# demo

> The one-line summary.
> It may wrap.

**Lead.** Details may hold prose, lists and fences:

- a plain list item
1. a numbered one

```bash
# a comment, not a heading
```

---

## Key files

- [a.sh](a.sh): the entry point
- [Upstream](https://example.org/x)

## Optional

- [docs](docs/): a directory
";

    /// The violations for `text` with `GOOD`'s link targets present.
    fn violations(tag: &str, text: &str) -> String {
        let root = fixture(tag, &["a.sh", "docs/"]);
        let out = check_llms_txt(text, &root).err().unwrap_or_default();
        let _ = fs::remove_dir_all(root);
        out.join("\n")
    }

    #[test]
    fn llms_txt_conforming_file_passes() {
        let root = fixture("good", &["a.sh", "docs/"]);
        let s = check_llms_txt(GOOD, &root).unwrap();
        assert_eq!((s.title.as_str(), s.sections, s.links), ("demo", 2, 3));
        let with_bom = format!("\u{feff}{GOOD}");
        assert!(check_llms_txt(&with_bom, &root).is_ok());
        let _ = fs::remove_dir_all(root);
    }

    #[test]
    fn llms_txt_header_tags_as_h1s_fail() {
        let text = GOOD.replace(
            "<!-- AI-hint: a demo index\n     AI-related: demo -->\n",
            "# AI-hint: a demo index\n# AI-related: demo\n",
        );
        let v = violations("tags", &text);
        assert!(v.contains("3 H1 headings (lines 1, 2, 3)"), "{v}");
        assert!(
            v.contains("line 1: `# AI-hint:` is a source-header tag"),
            "{v}"
        );
        assert!(
            v.contains("line 2: `# AI-related:` is a source-header tag"),
            "{v}"
        );
    }

    #[test]
    fn llms_txt_prose_and_fences_under_an_h2_fail() {
        let text = GOOD.replace(
            "## Optional\n",
            "## Optional\n\nProse here.\n\n```\ncode\n```\n",
        );
        let v = violations("prose", &text);
        assert!(
            v.contains("section \"Optional\" (line 24): 4 line(s)"),
            "{v}"
        );
        assert!(
            v.contains("line 26: not a `- [name](url)` file-list item"),
            "{v}"
        );
    }

    #[test]
    fn llms_txt_summary_is_required() {
        let v = violations(
            "nosum",
            &GOOD.replace("> The one-line summary.\n> It may wrap.\n", ""),
        );
        assert!(
            v.contains("must be followed by a `>` blockquote summary"),
            "{v}"
        );
        let v = violations("eof", "# demo\n");
        assert!(
            v.contains("must be followed by a `>` blockquote summary"),
            "{v}"
        );
    }

    #[test]
    fn llms_txt_headings_in_the_details_fail() {
        let v = violations("h3", &GOOD.replace("**Lead.**", "### Lead\n\n**Lead.**"));
        assert!(v.contains("an H3 heading"), "{v}");
        let v = violations("h1", &GOOD.replace("**Lead.**", "# Second\n\n**Lead.**"));
        assert!(v.contains("2 H1 headings"), "{v}");
        let v = violations(
            "setext",
            &GOOD.replace("**Lead.**", "Title\n=====\n\n**Lead.**"),
        );
        assert!(v.contains("a setext heading"), "{v}");
        let v = violations(
            "hashword",
            &GOOD.replace("**Lead.**", "##Lead\n\n**Lead.**"),
        );
        assert!(v.contains("is not a `## Name` heading"), "{v}");
    }

    #[test]
    fn llms_txt_hash_hash_inside_a_fence_or_comment_fails() {
        let v = violations("fence", &GOOD.replace("# a comment", "## a split"));
        assert!(v.contains("inside a code fence"), "{v}");
        let v = violations(
            "comment",
            &GOOD.replace("AI-related: demo -->", "AI-related: demo\n## split -->"),
        );
        assert!(v.contains("inside an HTML comment"), "{v}");
    }

    #[test]
    fn llms_txt_preamble_and_structure_fail() {
        let v = violations("stray", &format!("stray\n{GOOD}"));
        assert!(v.contains("line 1: content before the H1"), "{v}");
        assert!(violations("noh1", "> just a quote\n").contains("no H1"));
        assert!(violations("opencomment", "<!-- never closed\n").contains("inside an HTML comment"));
        let v = violations("openfence", "# demo\n\n> s\n\n```\nnever closed\n");
        assert!(v.contains("ends inside a code fence"), "{v}");
    }

    #[test]
    fn llms_txt_sections_must_be_distinct_and_non_empty() {
        let v = violations("dup", &GOOD.replace("## Optional", "## Key files"));
        assert!(
            v.contains("section \"Key files\" repeats the one at line 19"),
            "{v}"
        );
        let v = violations("empty", &format!("{GOOD}\n## Empty\n"));
        assert!(v.contains("section \"Empty\" lists no files"), "{v}");
    }

    #[test]
    fn llms_txt_link_item_shapes() {
        assert_eq!(parse_link_item("- [a](b): notes"), Ok(("a", "b")));
        assert_eq!(parse_link_item("- [a](b)"), Ok(("a", "b")));
        let err = |l| parse_link_item(l).unwrap_err();
        assert_eq!(
            err("  - [a](b)"),
            "file-list items start at column 0 with `- `"
        );
        assert_eq!(err("* [a](b)"), "not a `- [name](url)` file-list item");
        assert_eq!(
            err("- `a` -- notes"),
            "the list item does not open with a `[name](url)` link"
        );
        assert_eq!(err("- [a]b"), "`[name]` is not followed by `(url)`");
        assert_eq!(
            err("- [a](b c)"),
            "the link URL is empty or contains whitespace"
        );
        assert_eq!(err("- [ ](b)"), "the link name is empty");
        assert_eq!(
            err("- [a](b) -- notes"),
            "text after the link must follow a `:`"
        );
        assert_eq!(err("- [a](b):"), "the `:` after the link has no notes");
    }

    #[test]
    fn llms_txt_relative_links_must_resolve_under_the_root() {
        let item = "- [a.sh](a.sh): the entry point";
        let v = violations("missing", &GOOD.replace(item, "- [gone](gone.md): moved"));
        assert!(v.contains("link target `gone.md` does not exist"), "{v}");
        let v = violations("dotdot", &GOOD.replace(item, "- [up](../a.sh): out"));
        assert!(v.contains("climbs out of the tree"), "{v}");
        let v = violations("abs", &GOOD.replace(item, "- [abs](/etc/x): host"));
        assert!(v.contains("is root-absolute"), "{v}");
        let ok = GOOD.replace(
            item,
            "- [frag](a.sh#top): anchor\n- [mail](mailto:x@example.org)",
        );
        assert_eq!(violations("fragment", &ok), "");
    }

    fn test_dir(tag: &str) -> PathBuf {
        let now = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let dir = std::env::temp_dir().join(format!("mios_tmpl_{tag}_{}_{now}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn conformance_missing_ssot_fails() {
        let dir = test_dir("nossot");
        let err = run_conformance(&dir, None).unwrap_err();
        assert!(err.contains("cannot read"), "{err}");
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn conformance_missing_or_empty_templates_fails_t2() {
        let dir = test_dir("t2");
        let ssot_dir = dir.join("usr/share/mios");
        fs::create_dir_all(&ssot_dir).unwrap();
        // T2: [templates.*] renamed to [xtemplates.*]
        fs::write(
            ssot_dir.join("mios.toml"),
            "[xtemplates.test]\nmatch = '.*\\.sh'\n",
        )
        .unwrap();
        let err = run_conformance(&dir, None).unwrap_err();
        assert!(err.contains("[templates] section in"), "{err}");

        // Empty [templates]
        fs::write(ssot_dir.join("mios.toml"), "[templates]\n").unwrap();
        let err2 = run_conformance(&dir, None).unwrap_err();
        assert!(err2.contains("[templates] section in"), "{err2}");
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn conformance_invalid_regex_fails() {
        let dir = test_dir("badregex");
        let ssot_dir = dir.join("usr/share/mios");
        fs::create_dir_all(&ssot_dir).unwrap();
        fs::write(
            ssot_dir.join("mios.toml"),
            "[templates.bad]\nmatch = '[unclosed'\n",
        )
        .unwrap();
        let err = run_conformance(&dir, None).unwrap_err();
        assert!(err.contains("has invalid regex"), "{err}");
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn conformance_zero_checked_files_fails() {
        let dir = test_dir("zerochecked");
        let ssot_dir = dir.join("usr/share/mios");
        fs::create_dir_all(&ssot_dir).unwrap();
        fs::write(
            ssot_dir.join("mios.toml"),
            "[templates.test]\nmatch = '.*\\.sh'\n",
        )
        .unwrap();
        let gf_dir = dir.join("usr/share/mios/templates");
        fs::create_dir_all(&gf_dir).unwrap();
        fs::write(
            gf_dir.join("conformance-grandfathered.list"),
            "usr/share/mios/mios.toml\nusr/share/mios/templates/conformance-grandfathered.list\n",
        )
        .unwrap();
        let err = run_conformance(&dir, None).unwrap_err();
        assert!(err.contains("checked=0"), "{err}");
        let _ = fs::remove_dir_all(dir);
    }

    #[test]
    fn conformance_valid_tree_passes() {
        let dir = test_dir("valid");
        let ssot_dir = dir.join("usr/share/mios");
        fs::create_dir_all(&ssot_dir).unwrap();
        fs::write(
            ssot_dir.join("mios.toml"),
            "[templates.sh]\nmatch = '.*\\.sh$'\nrequired_header = true\n",
        )
        .unwrap();
        let script = dir.join("test.sh");
        fs::write(&script, "#!/bin/sh\n# AI-hint: test script\necho hi\n").unwrap();
        let res = run_conformance(&dir, None);
        assert!(res.is_ok(), "{:?}", res);
        let _ = fs::remove_dir_all(dir);
    }
}
