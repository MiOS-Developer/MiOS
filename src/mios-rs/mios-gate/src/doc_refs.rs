// AI-hint: Asserts every path named in an AI-related/AI-doc header or a markdown link resolves in the repository; the corpus is the TRACKED tree, so the verdict never moves with a developer's untracked working copies.
// AI-related: usr/share/mios/mios.toml, automation/98-drift-checks.sh, tests/drift-gate-negatives.sh

use crate::Report;
use regex::Regex;
use std::path::{Path, PathBuf};
use std::process::Command;

const CHECK: &str = "doc-refs-resolve";
const SSOT: &str = "usr/share/mios/mios.toml";
const BASELINE_FILE: &str = "usr/share/mios/reference/stale-refs-baseline.tsv";
const SCAN_EXT: [&str; 16] = [
    ".py",
    ".sh",
    ".bash",
    ".toml",
    ".ps1",
    ".psm1",
    ".rs",
    ".service",
    ".container",
    ".timer",
    ".socket",
    ".target",
    ".conf",
    ".yml",
    ".yaml",
    ".md",
];
/// Files whose references are deliberately outside the check: task registers
/// name planned paths, and the negative-test harness plants paths on purpose.
const SKIP_BASENAME: [&str; 3] = [
    "tasks.jsonl",
    "doc-generative-documentation.md",
    "drift-gate-negatives.sh",
];
/// Upstream code the repository carries but does not author.
const SKIP_SEGMENT: &str = "vendored/";
/// Runtime and system trees, never repository paths.
const RUNTIME_PREFIX: [&str; 6] = ["/etc/", "/var/", "/tmp/", "/proc/", "/sys/", "/run/"];
const URL_PREFIX: [&str; 3] = ["http://", "https://", "localhost"];
const REF_SUFFIX: [&str; 8] = [
    ".sh", ".py", ".toml", ".ps1", ".json", ".yaml", ".yml", ".md",
];
const LINK_SUFFIX: [&str; 8] = [
    ".md", ".sh", ".py", ".toml", ".json", ".txt", ".png", ".svg",
];

fn report(ok: bool, summary: String, findings: Vec<String>) -> Report {
    Report {
        check: CHECK.to_string(),
        ok,
        could_not_run: None,
        summary,
        findings,
    }
}

fn cannot_run(why: impl Into<String>) -> Report {
    Report {
        check: CHECK.to_string(),
        ok: false,
        could_not_run: Some(why.into()),
        summary: String::new(),
        findings: Vec::new(),
    }
}

struct Res {
    header: Regex,
    link: Regex,
    line_suffix: Regex,
    parens: Regex,
    section: Regex,
}

impl Res {
    fn new() -> Option<Res> {
        Some(Res {
            header: Regex::new(
                r"(?m)^[^\S\n]*(?:#|//)[^\S\n]*AI-(?:related|doc):[^\S\n]*(.+)$|<!--\s*AI-(?:related|doc):\s*(.*?)\s*-->",
            )
            .ok()?,
            link: Regex::new(r"\[([^\]]+)\]\(([^)]+)\)").ok()?,
            line_suffix: Regex::new(r":\d+.*$").ok()?,
            parens: Regex::new(r"\s*\([^)]*\)").ok()?,
            section: Regex::new(r"\s*\[[^\]]*\]\s*$").ok()?,
        })
    }
}

/// The tracked corpus. A filesystem walk counted every git worktree checked out
/// under the repository, reporting one reference once per working copy, and its
/// skip list matched ".git" as a substring so all of .github/ was never read.
fn corpus(root: &Path) -> Option<Vec<String>> {
    let out = Command::new("git")
        .arg("-C")
        .arg(root)
        .arg("ls-files")
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&out.stdout);
    Some(
        text.lines()
            .filter(|f| SCAN_EXT.iter().any(|e| f.ends_with(e)))
            .filter(|f| !f.contains(SKIP_SEGMENT))
            .filter(|f| {
                let base = f.rsplit('/').next().unwrap_or(f);
                !SKIP_BASENAME.contains(&base)
            })
            .map(|f| f.to_string())
            .collect(),
    )
}

/// The candidate roots a reference may be written against, in the order the
/// gate has always tried them: beside the file, one and two directories up,
/// the agent-pipe tree, and the repository root.
fn resolves(root: &Path, dir: &Path, rel: &str) -> bool {
    let rel = rel.trim_start_matches('/');
    let mut cands: Vec<PathBuf> = vec![dir.join(rel)];
    if let Some(p) = dir.parent() {
        cands.push(p.join(rel));
        if let Some(pp) = p.parent() {
            cands.push(pp.join(rel));
        }
    }
    cands.push(root.join("usr/lib/mios/agent-pipe").join(rel));
    cands.push(root.join(rel));
    cands.iter().any(|c| c.exists())
}

/// One AI-header token, reduced to the path it names.
fn clean(tok: &str, re: &Res) -> String {
    let t = tok.trim().trim_end_matches(',').trim();
    let t = re.line_suffix.replace(t, "");
    let t = re.parens.replace_all(t.trim(), "");
    // "file.toml [section]" is a file plus a section, not a path.
    re.section.replace(t.trim(), "").trim().to_string()
}

fn header_ref_is_path(t: &str) -> bool {
    if t.is_empty() || t.starts_with('[') || t.starts_with("@@") || t.starts_with('<') {
        return false;
    }
    if !(t.contains('/') || REF_SUFFIX.iter().any(|e| t.ends_with(e))) {
        return false;
    }
    if RUNTIME_PREFIX.iter().any(|p| t.starts_with(p)) {
        return false;
    }
    !URL_PREFIX.iter().any(|p| t.starts_with(p))
}

fn link_is_path(t: &str) -> bool {
    if t.is_empty() {
        return false;
    }
    if t.starts_with("http://")
        || t.starts_with("https://")
        || t.starts_with("mailto:")
        || t.starts_with('#')
        || t.starts_with("file://")
    {
        return false;
    }
    LINK_SUFFIX.iter().any(|e| t.ends_with(e)) || t.ends_with(".jpg") || t.contains('/')
}

fn glob_to_regex_str(pat: &str) -> String {
    let mut s = String::from("^");
    for c in pat.chars() {
        match c {
            '*' => s.push_str(".*"),
            '?' => s.push('.'),
            '.' | '+' | '(' | ')' | '[' | ']' | '{' | '}' | '^' | '$' | '|' | '\\' => {
                s.push('\\');
                s.push(c);
            }
            _ => s.push(c),
        }
    }
    s.push('$');
    s
}

fn is_allowlisted(t: &str, allowlist: &[String]) -> bool {
    let t_trim = t.trim_start_matches('/');
    for a in allowlist {
        let a_trim = a.trim_start_matches('/');
        if t == a || t_trim == a_trim {
            return true;
        }
        if (a.ends_with('/') || a.ends_with('-') || a.ends_with('_'))
            && (t.starts_with(a) || t_trim.starts_with(a_trim))
        {
            return true;
        }
        if a.contains('*') || a.contains('?') {
            let pat = glob_to_regex_str(a_trim);
            if let Ok(re) = Regex::new(&pat) {
                if re.is_match(t_trim) {
                    return true;
                }
            }
        }
    }
    false
}

fn slugify_heading(h: &str) -> String {
    let mut clean = h.trim();
    if let Some(pos) = clean.rfind("{#") {
        if clean.ends_with('}') {
            clean = clean[..pos].trim();
        }
    }
    let mut without_html = String::new();
    let mut in_tag = false;
    for c in clean.chars() {
        if c == '<' {
            in_tag = true;
        } else if c == '>' {
            in_tag = false;
        } else if !in_tag {
            without_html.push(c);
        }
    }
    let s = without_html.to_lowercase();
    let mut slug = String::new();
    for c in s.chars() {
        if c.is_alphanumeric() || c == '_' || c == '-' {
            slug.push(c);
        } else if c.is_whitespace() {
            slug.push('-');
        }
    }
    slug
}

fn collapse_hyphens(s: &str) -> String {
    let mut out = String::new();
    let mut last_was_hyphen = false;
    for c in s.chars() {
        if c == '-' {
            if !last_was_hyphen {
                out.push(c);
                last_was_hyphen = true;
            }
        } else {
            out.push(c);
            last_was_hyphen = false;
        }
    }
    out
}

fn extract_markdown_anchors(text: &str) -> Vec<String> {
    let mut anchors = Vec::new();
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.starts_with('#') {
            let without_hashes = trimmed.trim_start_matches('#');
            if without_hashes.starts_with(' ') || without_hashes.starts_with('\t') {
                let h = without_hashes.trim();
                let slug = slugify_heading(h);
                if !slug.is_empty() {
                    let collapsed = collapse_hyphens(&slug);
                    if collapsed != slug {
                        anchors.push(collapsed);
                    }
                    anchors.push(slug);
                }
                if let Some(start) = h.rfind("{#") {
                    if let Some(end) = h[start..].find('}') {
                        let cid = &h[start + 2..start + end];
                        if !cid.is_empty() {
                            anchors.push(cid.to_string());
                        }
                    }
                }
            }
        }
    }
    for part in text.split('<') {
        if let Some(gt) = part.find('>') {
            let tag = &part[..gt];
            for attr in ["name=", "id="] {
                if let Some(pos) = tag.find(attr) {
                    let rest = &tag[pos + attr.len()..];
                    if let Some(quote) = rest.chars().next() {
                        if quote == '"' || quote == '\'' {
                            if let Some(end_quote) = rest[1..].find(quote) {
                                let val = &rest[1..1 + end_quote];
                                if !val.is_empty() {
                                    anchors.push(val.to_string());
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    anchors
}

#[derive(Debug, Clone, PartialEq, Eq)]
#[allow(dead_code)]
pub enum RenameSuggestion {
    None,
    Exact(String),
    Ambiguous(Vec<String>),
}

/// Suggests a replacement for a stale reference using git rename history
/// (`git log -1 --format=%H` + `git show -M --diff-filter=R --name-status`) and
/// basename fallback across tracked files. Emits exactly one suggestion when
/// unambiguous, or lists candidates without guessing when multiple match.
#[allow(dead_code)]
pub fn suggest_rename(root: &Path, stale_path: &str) -> RenameSuggestion {
    let stale = stale_path.trim_start_matches('/');

    // 1. Try git commit rename history
    if let Ok(out) = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(["log", "-1", "--format=%H", "--"])
        .arg(stale)
        .output()
    {
        if out.status.success() {
            let commit = String::from_utf8_lossy(&out.stdout).trim().to_string();
            if !commit.is_empty() {
                if let Ok(diff_out) = Command::new("git")
                    .arg("-C")
                    .arg(root)
                    .args([
                        "show",
                        "-M",
                        "--diff-filter=R",
                        "--name-status",
                        "--format=",
                    ])
                    .arg(&commit)
                    .output()
                {
                    if diff_out.status.success() {
                        let diff_text = String::from_utf8_lossy(&diff_out.stdout);
                        let mut rename_targets = Vec::new();
                        for line in diff_text.lines() {
                            let parts: Vec<&str> = line.split('\t').collect();
                            if parts.len() >= 3 && parts[0].starts_with('R') {
                                let src = parts[1].trim();
                                let dst = parts[2].trim();
                                if src == stale {
                                    rename_targets.push(dst.to_string());
                                }
                            }
                        }
                        if rename_targets.len() == 1 {
                            let dst = &rename_targets[0];
                            if root.join(dst).exists() {
                                return RenameSuggestion::Exact(dst.clone());
                            }
                        } else if rename_targets.len() > 1 {
                            return RenameSuggestion::Ambiguous(rename_targets);
                        }
                    }
                }
            }
        }
    }

    // 2. Basename matching fallback across tracked files
    let base = stale.rsplit('/').next().unwrap_or(stale);
    if let Some(files) = corpus(root) {
        let mut matches: Vec<String> = files
            .into_iter()
            .filter(|f| {
                let f_base = f.rsplit('/').next().unwrap_or(f);
                f_base == base && f.as_str() != stale
            })
            .collect();
        matches.sort();

        if matches.len() == 1 {
            return RenameSuggestion::Exact(matches.remove(0));
        } else if matches.len() > 1 {
            return RenameSuggestion::Ambiguous(matches);
        }
        if let Some(only) = matches.pop() {
            return RenameSuggestion::Exact(only);
        }
    }

    RenameSuggestion::None
}

fn load_baseline(root: &Path) -> Option<std::collections::HashSet<String>> {
    let path = root.join(BASELINE_FILE);
    if !path.is_file() {
        return None;
    }
    let text = std::fs::read_to_string(&path).ok()?;
    let mut set = std::collections::HashSet::new();
    for line in text.lines() {
        let trimmed = line.trim();
        if trimmed.is_empty() || trimmed.starts_with('#') {
            continue;
        }
        if let Some((f, rest)) = trimmed.split_once('\t') {
            let tok = rest.split('\t').next().unwrap_or(rest).trim();
            set.insert(format!("{}: {}", f.trim(), tok));
        } else if let Some((f, tok)) = trimmed.split_once(':') {
            set.insert(format!("{}: {}", f.trim(), tok.trim()));
        } else {
            set.insert(trimmed.to_string());
        }
    }
    Some(set)
}

/// A `rel: target` finding, with the rename suggest_rename finds for its target appended.
fn with_rename_hint(root: &Path, finding: &str) -> String {
    let target = finding.split_once(": ").map_or(finding, |(_, t)| t);
    let target = target.split('#').next().unwrap_or(target);
    match suggest_rename(root, target) {
        RenameSuggestion::Exact(dst) => format!("{finding} (renamed: {dst})"),
        RenameSuggestion::Ambiguous(c) => format!("{finding} (candidates: {})", c.join(", ")),
        RenameSuggestion::None => finding.to_string(),
    }
}

/// What both entry points read from [docs]: the ceiling and the allowlist.
fn docs_policy(root: &Path) -> Result<(i64, Vec<String>), String> {
    let ssot = root.join(SSOT);
    let Ok(text) = std::fs::read_to_string(&ssot) else {
        return Err(format!("{} is unreadable", ssot.display()));
    };
    let Ok(data) = text.parse::<toml::Value>() else {
        return Err(format!("{} did not parse", ssot.display()));
    };
    let docs = data.get("docs").and_then(|d| d.as_table());
    let max_stale = docs
        .and_then(|d| d.get("max_stale_doc_refs"))
        .and_then(|v| v.as_integer())
        .unwrap_or(0);
    let allowlist: Vec<String> = docs
        .and_then(|d| d.get("ref_allowlist"))
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str())
                .map(|s| s.to_string())
                .collect()
        })
        .unwrap_or_default();
    Ok((max_stale, allowlist))
}

/// Every path one file's AI-related/AI-doc headers name that does not resolve.
fn header_stale(root: &Path, dir: &Path, body: &str, re: &Res, allow: &[String]) -> Vec<String> {
    let mut out = Vec::new();
    for c in re.header.captures_iter(body) {
        let raw = c
            .get(1)
            .or_else(|| c.get(2))
            .map(|m| m.as_str())
            .unwrap_or("");
        for tok in raw.split(',') {
            let t = clean(tok, re);
            if header_ref_is_path(&t) && !is_allowlisted(&t, allow) && !resolves(root, dir, &t) {
                out.push(t);
            }
        }
    }
    out
}

/// `doc-refs-headers`: every unresolved header path as `rel<TAB>target`, with
/// no baseline, so the comment census can mark the blocks that name one (R7).
pub fn headers(root: &Path) -> Report {
    const NAME: &str = "doc-refs-headers";
    let named = |mut r: Report| {
        r.check = NAME.to_string();
        r
    };
    let Some(re) = Res::new() else {
        return named(cannot_run("the reference patterns did not compile"));
    };
    let allowlist = match docs_policy(root) {
        Ok((_, a)) => a,
        Err(why) => return named(cannot_run(why)),
    };
    // The doc-refs-resolve corpus, so the two verdicts can never disagree.
    let Some(files) = corpus(root) else {
        return named(cannot_run("git ls-files failed, so no file was scanned"));
    };
    if files.is_empty() {
        return named(cannot_run(
            "the tracked corpus is empty, so this check proved nothing",
        ));
    }
    let mut findings = Vec::new();
    for rel in &files {
        let fpath = root.join(rel);
        let dir = fpath.parent().unwrap_or(root).to_path_buf();
        let body = match std::fs::read(&fpath) {
            Ok(b) => String::from_utf8_lossy(&b).into_owned(),
            Err(e) => return named(cannot_run(format!("{rel} is unreadable: {e}"))),
        };
        for t in header_stale(root, &dir, &body, &re, &allowlist) {
            findings.push(format!("{rel}\t{t}"));
        }
    }
    let summary = format!(
        "{} unresolved AI header reference(s) across {} tracked file(s)",
        findings.len(),
        files.len()
    );
    named(report(findings.is_empty(), summary, findings))
}

pub fn check(root: &Path) -> Report {
    let Some(re) = Res::new() else {
        return cannot_run("the reference patterns did not compile");
    };
    let (max_stale, allowlist) = match docs_policy(root) {
        Ok(p) => p,
        Err(why) => return cannot_run(why),
    };

    let Some(files) = corpus(root) else {
        return cannot_run("git ls-files failed, so no file was scanned");
    };
    if files.is_empty() {
        return cannot_run("the tracked corpus is empty, so this check proved nothing");
    }

    let mut stale: Vec<String> = Vec::new();
    let mut unreadable: Vec<String> = Vec::new();
    for rel in &files {
        let fpath = root.join(rel);
        let dir = fpath.parent().unwrap_or(root).to_path_buf();
        let base = rel.rsplit('/').next().unwrap_or(rel);
        let body = match std::fs::read(&fpath) {
            Ok(b) => String::from_utf8_lossy(&b).into_owned(),
            // A read failure used to be swallowed, dropping the file from the
            // corpus while the check still reported success over the rest.
            Err(e) => {
                unreadable.push(format!("{rel}: {e}"));
                continue;
            }
        };
        for t in header_stale(root, &dir, &body, &re, &allowlist) {
            stale.push(format!("{rel}: {t}"));
        }
        if base.ends_with(".md") {
            for c in re.link.captures_iter(&body) {
                let raw_link = c.get(2).map(|m| m.as_str()).unwrap_or("").trim();
                if raw_link.is_empty()
                    || raw_link.starts_with("http://")
                    || raw_link.starts_with("https://")
                    || raw_link.starts_with("mailto:")
                    || raw_link.starts_with("file://")
                {
                    continue;
                }
                let (target_file, fragment) = match raw_link.split_once('#') {
                    Some((f, frag)) => (f.trim(), Some(frag.trim())),
                    None => (raw_link, None),
                };
                if !target_file.is_empty() && !link_is_path(target_file) {
                    continue;
                }
                if is_allowlisted(raw_link, &allowlist)
                    || (!target_file.is_empty() && is_allowlisted(target_file, &allowlist))
                {
                    continue;
                }
                if let Some(frag) = fragment {
                    if is_allowlisted(frag, &allowlist) {
                        continue;
                    }
                }
                let resolved_path = if target_file.is_empty() {
                    Some(fpath.clone())
                } else {
                    let rel_t = target_file.trim_start_matches('/');
                    let mut cand = None;
                    if dir.join(rel_t).exists() {
                        cand = Some(dir.join(rel_t));
                    } else if root.join(rel_t).exists() {
                        cand = Some(root.join(rel_t));
                    } else if let Some(p) = dir.parent() {
                        if p.join(rel_t).exists() {
                            cand = Some(p.join(rel_t));
                        }
                    }
                    cand
                };

                let Some(tpath) = resolved_path else {
                    stale.push(format!("{rel}: {target_file}"));
                    continue;
                };

                if let Some(frag) = fragment {
                    if !frag.is_empty() && tpath.extension().is_some_and(|ext| ext == "md") {
                        let target_content = if tpath == fpath {
                            body.clone()
                        } else {
                            match std::fs::read(&tpath) {
                                Ok(b) => String::from_utf8_lossy(&b).into_owned(),
                                Err(_) => String::new(),
                            }
                        };
                        let anchors = extract_markdown_anchors(&target_content);
                        let frag_lower = frag.to_lowercase();
                        let frag_slug = slugify_heading(frag);
                        let frag_collapsed = collapse_hyphens(&frag_slug);
                        let found = anchors.iter().any(|a| {
                            a == frag
                                || a.to_lowercase() == frag_lower
                                || *a == frag_slug
                                || *a == frag_collapsed
                        });
                        if !found {
                            stale.push(format!("{rel}: {raw_link}"));
                        }
                    }
                }
            }
        }
    }

    if !unreadable.is_empty() {
        let n = unreadable.len();
        unreadable.truncate(10);
        return report(
            false,
            String::new(),
            std::iter::once(format!(
                "{n} tracked file(s) unreadable, so their references went unchecked"
            ))
            .chain(unreadable)
            .collect(),
        );
    }

    let baseline = load_baseline(root);
    if let Some(ref base_set) = baseline {
        let new_breaks: Vec<&String> = stale.iter().filter(|f| !base_set.contains(*f)).collect();
        if !new_breaks.is_empty() {
            let mut findings = vec![format!(
                "{} new stale reference(s) not in baseline ({}):",
                new_breaks.len(),
                BASELINE_FILE
            )];
            for nb in new_breaks {
                findings.push(with_rename_hint(root, nb));
            }
            return report(false, String::new(), findings);
        }
    }

    let n = stale.len() as i64;
    let allowed_ceiling = if baseline.is_some() {
        baseline.as_ref().map_or(max_stale, |b| b.len() as i64)
    } else {
        max_stale
    };
    if n > allowed_ceiling {
        let mut findings = vec![format!(
            "{n} stale reference(s) across {} tracked file(s) (max allowed {allowed_ceiling}):",
            files.len()
        )];
        // Every one: this list is the backlog someone has to work through, and a
        // silent cap hid 93 of 103 from the people deciding them (task_999).
        findings.extend(stale.iter().map(|f| with_rename_hint(root, f)));
        return report(false, String::new(), findings);
    }
    // The pass line states the count it measured, in the same form as the
    // failure line. Without it a clean tree printed no number, and anything
    // reading the count (the negative test's baseline) saw nothing to compare.
    // Under a non-zero ceiling it also stops a pass from claiming every path
    // resolves while some are only tolerated.
    let verdict = if n == 0 {
        "every path named in an AI header or a markdown link resolves"
    } else {
        "the remainder is tolerated by the ceiling, not resolved"
    };
    report(
        true,
        format!(
            "{n} stale reference(s) across {} tracked file(s) (max allowed {allowed_ceiling}); {verdict}",
            files.len()
        ),
        Vec::new(),
    )
}

#[cfg(test)]
mod tests {
    // Test code: a panic here IS the assertion. Scoped so production code stays under the lint.
    #![allow(clippy::panic)]

    use super::*;
    use std::fs;

    /// A failed fixture must FAIL the test, never return early and be counted
    /// as a pass -- a silently skipped setup is the defect this suite hunts.
    fn repo() -> tempfile::TempDir {
        let d = match tempfile::tempdir() {
            Ok(d) => d,
            Err(e) => unreachable!("fixture tempdir: {e}"),
        };
        let r = d.path().to_path_buf();
        if let Err(e) = fs::create_dir_all(r.join("usr/share/mios")) {
            unreachable!("fixture mkdir: {e}");
        }
        if let Err(e) = fs::write(
            r.join(SSOT),
            "[docs]\nmax_stale_doc_refs = 0\nref_allowlist = [\"some/allowed-token/path.py\"]\n",
        ) {
            unreachable!("fixture ssot: {e}");
        }
        for a in [
            vec!["init", "-q"],
            vec!["config", "user.email", "g@example.invalid"],
            vec!["config", "user.name", "g"],
            // The fixture commits; a caller's global signing setup must not reach it.
            vec!["config", "commit.gpgsign", "false"],
        ] {
            match Command::new("git").arg("-C").arg(&r).args(&a).output() {
                Ok(o) if o.status.success() => {}
                Ok(o) => unreachable!("fixture git {a:?}: {}", o.status),
                Err(e) => unreachable!("fixture git {a:?}: {e}"),
            }
        }
        d
    }

    /// Stage everything, and assert the index is non-empty afterwards: a
    /// corpus that silently stayed empty would make every arm below vacuous.
    fn track(r: &Path) {
        match Command::new("git")
            .arg("-C")
            .arg(r)
            .args(["add", "-A"])
            .output()
        {
            Ok(o) if o.status.success() => {}
            Ok(o) => unreachable!("fixture git add: {}", o.status),
            Err(e) => unreachable!("fixture git add: {e}"),
        }
        let files = corpus(r).unwrap_or_default();
        assert!(
            !files.is_empty(),
            "fixture staged nothing, so the arm would prove nothing"
        );
    }

    #[test]
    fn a_header_naming_a_missing_file_is_stale() {
        let d = repo();
        let r = d.path();
        let _ = fs::write(r.join("a.py"), "# AI-related: tools/does-not-exist.py\n");
        track(r);
        let rep = check(r);
        assert!(!rep.ok, "a missing target must be reported");
        assert!(
            rep.findings.iter().any(|f| f.contains("does-not-exist.py")),
            "the finding must name the missing target: {:?}",
            rep.findings
        );
    }

    #[test]
    fn headers_lists_every_unresolved_header() {
        let d = repo();
        let r = d.path();
        let _ = fs::write(
            r.join("a.py"),
            "# AI-related: a.py, tools/gone.py
",
        );
        let _ = fs::write(
            r.join("b.sh"),
            "# AI-doc: tools/also-gone.sh
",
        );
        track(r);
        let rep = headers(r);
        assert_eq!(rep.check, "doc-refs-headers");
        assert!(!rep.ok, "unresolved headers must be listed");
        assert_eq!(
            rep.findings,
            ["a.py	tools/gone.py", "b.sh	tools/also-gone.sh"]
        );
        let _ = fs::write(
            r.join("a.py"),
            "# AI-related: a.py
",
        );
        let _ = fs::write(
            r.join("b.sh"),
            "true
",
        );
        assert!(headers(r).ok, "resolving headers must list nothing");
    }

    #[test]
    fn every_stale_reference_is_listed_not_the_first_ten() {
        let d = repo();
        let r = d.path();
        for i in 0..25 {
            let _ = fs::write(
                r.join(format!("f{i}.py")),
                format!("# AI-related: tools/missing-{i}.py\n"),
            );
        }
        track(r);
        let rep = check(r);
        let listed = (0..25)
            .filter(|i| {
                rep.findings
                    .iter()
                    .any(|f| f.ends_with(&format!("missing-{i}.py")))
            })
            .count();
        assert_eq!(
            listed, 25,
            "a capped list hides the rest: {:?}",
            rep.findings
        );
    }

    #[test]
    fn a_header_naming_a_present_file_is_clean() {
        let d = repo();
        let r = d.path();
        let _ = fs::write(r.join("b.py"), "# AI-related: a.py\n");
        let _ = fs::write(r.join("a.py"), "x = 1\n");
        track(r);
        let rep = check(r);
        assert!(rep.ok, "a resolving target must not be reported");
        // The pass states its measurement, so a reader can compare counts.
        assert!(
            rep.summary.starts_with("0 stale reference(s) across "),
            "the pass line must carry the count: {}",
            rep.summary
        );
    }

    #[test]
    fn an_untracked_file_is_outside_the_corpus() {
        let d = repo();
        let r = d.path();
        let _ = fs::write(r.join("kept.py"), "# AI-related: kept.py\n");
        track(r);
        // Written AFTER `git add`, so it is on disk but not in the index. A
        // filesystem walk would read it; the tracked corpus must not.
        let _ = fs::write(r.join("stray.py"), "# AI-related: tools/absent.py\n");
        let rep = check(r);
        assert!(
            rep.ok,
            "an untracked file must not move the verdict: {:?}",
            rep.findings
        );
    }

    #[test]
    fn a_worktree_copy_does_not_multiply_a_finding() {
        let d = repo();
        let r = d.path();
        let _ = fs::write(r.join("a.py"), "# AI-related: tools/absent.py\n");
        track(r);
        let before = check(r).findings.len();
        // A second full copy of the tree under the repo, untracked, is exactly
        // what a `git worktree` looks like to a filesystem walk.
        let _ = fs::create_dir_all(r.join(".worktrees/lane"));
        let _ = fs::write(
            r.join(".worktrees/lane/a.py"),
            "# AI-related: tools/absent.py\n",
        );
        assert_eq!(
            before,
            check(r).findings.len(),
            "a working copy under the repo must not be counted again"
        );
    }

    #[test]
    fn the_allowlist_exempts_a_matching_token() {
        let d = repo();
        let r = d.path();
        let _ = fs::write(r.join("c.py"), "# AI-related: some/allowed-token/path.py\n");
        track(r);
        assert!(check(r).ok, "an allowlisted token must be exempt");
    }

    #[test]
    fn a_token_merely_containing_an_allowlist_entry_is_stale() {
        let d = repo();
        let r = d.path();
        let _ = fs::write(
            r.join("d.py"),
            "# AI-related: prefix/some/allowed-token/path.py/suffix.py\n",
        );
        track(r);
        let rep = check(r);
        assert!(
            !rep.ok && rep.findings.iter().any(|f| f.contains("prefix/some/allowed-token/path.py/suffix.py")),
            "a token merely containing an allowlist entry without matching must be reported stale: {:?}",
            rep.findings
        );
    }

    #[test]
    fn a_dot_github_file_is_inside_the_corpus() {
        let d = repo();
        let r = d.path();
        let _ = fs::create_dir_all(r.join(".github"));
        let _ = fs::write(
            r.join(".github/notes.md"),
            "[x](../tools/definitely-absent.py)\n",
        );
        track(r);
        let rep = check(r);
        assert!(
            !rep.ok && rep.findings.iter().any(|f| f.contains("definitely-absent")),
            "the old skip list matched '.git' as a substring and hid .github/: {:?}",
            rep.findings
        );
    }

    #[test]
    fn an_empty_corpus_cannot_run_rather_than_pass() {
        let d = repo();
        let r = d.path();
        // Deliberately NOT calling track(): the index stays empty.
        let rep = check(r);
        assert!(
            rep.could_not_run.is_some(),
            "an empty corpus must not pass: {:?}",
            rep.summary
        );
    }

    #[test]
    fn a_link_naming_an_existing_file_with_a_nonexistent_heading_is_stale() {
        let d = repo();
        let r = d.path();
        let _ = fs::write(r.join("target.md"), "# Real Heading\n\nContent\n");
        let _ = fs::write(r.join("doc.md"), "[link](target.md#nonexistent-heading)\n");
        track(r);
        let rep = check(r);
        assert!(
            !rep.ok
                && rep
                    .findings
                    .iter()
                    .any(|f| f.contains("nonexistent-heading")),
            "a link to an existing file with a nonexistent fragment must be reported stale: {:?}",
            rep.findings
        );
    }

    #[test]
    fn a_link_naming_an_existing_file_with_an_existing_heading_is_clean() {
        let d = repo();
        let r = d.path();
        let _ = fs::write(r.join("target.md"), "# Real Heading\n\nContent\n");
        let _ = fs::write(r.join("doc.md"), "[link](target.md#real-heading)\n");
        track(r);
        let rep = check(r);
        assert!(
            rep.ok,
            "a link to an existing heading must pass: {:?}",
            rep.findings
        );
    }

    #[test]
    fn a_rust_file_header_is_scanned() {
        let d = repo();
        let r = d.path();
        let _ = fs::write(r.join("lib.rs"), "// AI-related: nonexistent/missing.rs\n");
        track(r);
        let rep = check(r);
        assert!(
            !rep.ok && rep.findings.iter().any(|f| f.contains("missing.rs")),
            "a rust file with a stale header ref must be reported stale: {:?}",
            rep.findings
        );
    }

    #[test]
    fn a_toml_file_header_is_scanned() {
        let d = repo();
        let r = d.path();
        let _ = fs::write(
            r.join("config.toml"),
            "# AI-related: nonexistent/missing.toml\n",
        );
        track(r);
        let rep = check(r);
        assert!(
            !rep.ok && rep.findings.iter().any(|f| f.contains("missing.toml")),
            "a toml file with a stale header ref must be reported stale: {:?}",
            rep.findings
        );
    }

    #[test]
    fn a_service_file_header_is_scanned() {
        let d = repo();
        let r = d.path();
        let _ = fs::write(
            r.join("app.service"),
            "# AI-related: nonexistent/missing.service\n",
        );
        track(r);
        let rep = check(r);
        assert!(
            !rep.ok && rep.findings.iter().any(|f| f.contains("missing.service")),
            "a service file with a stale header ref must be reported stale: {:?}",
            rep.findings
        );
    }

    #[test]
    fn when_a_stale_path_was_renamed_once_the_system_shall_emit_exactly_one_suggestion() {
        let d = repo();
        let r = d.path();
        let _ = fs::write(r.join("old_module.py"), "# Old module\n");
        track(r);
        let _ = Command::new("git")
            .arg("-C")
            .arg(r)
            .args(["commit", "-m", "init old module"])
            .output();

        let _ = fs::rename(r.join("old_module.py"), r.join("new_module.py"));
        track(r);
        let _ = Command::new("git")
            .arg("-C")
            .arg(r)
            .args(["commit", "-m", "rename to new module"])
            .output();

        let suggestion = suggest_rename(r, "old_module.py");
        assert_eq!(
            suggestion,
            RenameSuggestion::Exact("new_module.py".to_string()),
            "a renamed path must produce exactly one suggestion: {:?}",
            suggestion
        );
    }

    #[test]
    fn when_two_or_more_files_match_the_system_shall_emit_no_suggestion_and_list_the_candidates() {
        let d = repo();
        let r = d.path();
        let _ = fs::create_dir_all(r.join("dir_a"));
        let _ = fs::create_dir_all(r.join("dir_b"));
        let _ = fs::write(r.join("dir_a/target.py"), "# A\n");
        let _ = fs::write(r.join("dir_b/target.py"), "# B\n");
        track(r);

        let suggestion = suggest_rename(r, "target.py");
        match suggestion {
            RenameSuggestion::Ambiguous(cands) => {
                assert_eq!(cands.len(), 2);
                assert!(cands.contains(&"dir_a/target.py".to_string()));
                assert!(cands.contains(&"dir_b/target.py".to_string()));
            }
            other => panic!("expected Ambiguous candidates, got {:?}", other),
        }
    }

    #[test]
    fn when_a_diff_fixes_one_reference_and_introduces_a_different_one_at_the_same_total_the_system_shall_fail_the_gate(
    ) {
        let d = repo();
        let r = d.path();
        // Setup baseline directory and file containing a known stale ref for a.py
        let _ = fs::create_dir_all(r.join("usr/share/mios/reference"));
        let _ = fs::write(
            r.join(BASELINE_FILE),
            "a.py\ttools/missing_a.py\tdangling\n",
        );
        // a.py is fixed (points to present.py)
        let _ = fs::write(r.join("present.py"), "x = 1\n");
        let _ = fs::write(r.join("a.py"), "# AI-related: present.py\n");
        // b.py introduces a new stale ref (points to tools/missing_b.py)
        let _ = fs::write(r.join("b.py"), "# AI-related: tools/missing_b.py\n");
        track(r);

        // Total stale refs count is 1 (equal to baseline count of 1)
        let rep = check(r);
        assert!(
            !rep.ok,
            "introducing a different reference at the same total must fail the gate: {:?}",
            rep.findings
        );
        assert!(
            rep.findings
                .iter()
                .any(|f| f.contains("not in baseline") || f.contains("missing_b.py")),
            "the findings must name the new break: {:?}",
            rep.findings
        );
    }
}
