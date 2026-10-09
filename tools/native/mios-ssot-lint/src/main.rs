// AI-hint: Rust implementation of SSOT lint checks for mios.toml structure and invariants.
// tools/native/mios-ssot-lint/src/main.rs
// Rust port of 97-ssot-lint.sh (task_1201)
use std::collections::BTreeSet;
use std::env;
use std::fs;
use std::path::{Path, PathBuf};
use std::process;

fn find_root() -> PathBuf {
    if let Ok(override_root) = env::var("MIOS_SSOT_LINT_ROOT") {
        if !override_root.is_empty() {
            return PathBuf::from(override_root);
        }
    }
    if let Ok(override_root) = env::var("MIOS_DRIFT_ROOT") {
        if !override_root.is_empty() {
            return PathBuf::from(override_root);
        }
    }
    if let Ok(exe) = env::current_exe() {
        if let Some(parent) = exe.parent() {
            if parent.ends_with("tools/native/target/debug")
                || parent.ends_with("tools/native/target/release")
            {
                if let Some(r) = parent.ancestors().nth(4) {
                    return r.to_path_buf();
                }
            }
        }
    }
    env::current_dir().unwrap_or_else(|_| PathBuf::from("."))
}

fn walk_dir_files(dir: &Path, files: &mut Vec<PathBuf>) {
    if let Ok(entries) = fs::read_dir(dir) {
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() {
                walk_dir_files(&path, files);
            } else if path.is_file() {
                files.push(path);
            }
        }
    }
}

fn is_directive_line(line: &str) -> bool {
    line.starts_with("Exec=")
        || line.starts_with("ExecStart=")
        || line.starts_with("ExecStartPre=")
        || line.starts_with("ExecStartPost=")
        || line.starts_with("Environment=")
}

fn extract_placeholders(line: &str, refs: &mut BTreeSet<String>) {
    let mut rest = line;
    while let Some(start) = rest.find("${MIOS_") {
        let after = &rest[start + 2..]; // starts with "MIOS_"
        if let Some(end) = after.find('}') {
            let token = &after[..end]; // e.g. "MIOS_FOO" or "MIOS_FOO:-default"
            let var_name = if let Some(colon_pos) = token.find(":-") {
                &token[..colon_pos]
            } else {
                token
            };
            if var_name
                .chars()
                .all(|c| c.is_ascii_alphanumeric() || c == '_')
            {
                refs.insert(var_name.to_string());
            }
            rest = &after[end + 1..];
        } else {
            break;
        }
    }
}

fn filter_non_comments(content: &str) -> Vec<&str> {
    content
        .lines()
        .filter(|line| !line.trim_start().starts_with('#'))
        .collect()
}

fn in_userenv(v: &str, userenv_lines: &[&str]) -> bool {
    let slot_pattern_1 = format!("\"{}\")", v);
    let slot_pattern_2 = format!("\"{}\",", v);
    let slot_pattern_3 = format!("\"{}\" )", v);
    let slot_pattern_4 = format!("\"{}\" ,", v);
    let slot_pattern_5 = format!("\"{}\"", v);

    let assign_pattern_1 = format!("export {}=", v);
    let assign_pattern_2 = format!("{}=", v);

    for line in userenv_lines {
        // (a) typed-slot target: a double-quoted "MIOS_X" token followed by space/)/,
        if line.contains(&slot_pattern_1)
            || line.contains(&slot_pattern_2)
            || line.contains(&slot_pattern_3)
            || line.contains(&slot_pattern_4)
        {
            return true;
        }
        if line.contains(&slot_pattern_5) {
            let mut idx = 0;
            while let Some(pos) = line[idx..].find(&slot_pattern_5) {
                let absolute_pos = idx + pos;
                let tail = &line[absolute_pos + slot_pattern_5.len()..];
                let trimmed = tail.trim_start();
                if trimmed.starts_with(')') || trimmed.starts_with(',') || trimmed.is_empty() {
                    return true;
                }
                idx = absolute_pos + slot_pattern_5.len();
            }
        }

        // (b) explicit export / bare assignment: export MIOS_X= | MIOS_X=
        if line.contains(&assign_pattern_1) {
            return true;
        }
        if let Some(pos) = line.find(&assign_pattern_2) {
            if pos == 0
                || line.as_bytes()[pos - 1] == b' '
                || line.as_bytes()[pos - 1] == b'\t'
                || line.as_bytes()[pos - 1] == b';'
            {
                return true;
            }
        }

        // (c) named verbatim in a legacy for-loop var list (word-boundary)
        if let Some(pos) = line.find(v) {
            let valid_before = pos == 0 || matches!(line.as_bytes()[pos - 1], b' ' | b'\t' | b';');
            let end = pos + v.len();
            let valid_after =
                end == line.len() || matches!(line.as_bytes()[end], b' ' | b'\t' | b';' | b'$');
            if valid_before && valid_after {
                return true;
            }
        }
    }
    false
}

/// Anchored `^NAME=`, matching the bash twin. A substring match would also hit
/// a name mentioned inside another variable's VALUE -- env-baseline is full of
/// those -- and would report a name as emitted when it is only referenced.
fn in_render(v: &str, render_lines: &[&str]) -> bool {
    render_lines.iter().any(|line| {
        line.strip_prefix(v)
            .map(|rest| rest.starts_with('='))
            .unwrap_or(false)
    })
}

fn main() {
    let root = find_root();
    let userenv_path = root.join("tools/lib/userenv.sh");
    // The renderer has no allowlist since task_977; "renderable" now means the
    // resolver emits the name, so the twin reads the generated env baseline.
    let render_path = root.join("usr/share/mios/reference/env-baseline.txt");
    let quadlet_dir = root.join("usr/share/containers/systemd");

    let soft_mode = env::var("MIOS_SSOT_LINT_SOFT").unwrap_or_default() == "1";

    if !userenv_path.is_file() {
        println!(
            "[97-ssot-lint] FATAL: userenv.sh not found at {}",
            userenv_path.display()
        );
        process::exit(2);
    }
    if !render_path.is_file() {
        println!(
            "[97-ssot-lint] FATAL: env baseline not found at {}",
            render_path.display()
        );
        process::exit(2);
    }
    if !quadlet_dir.is_dir() {
        println!(
            "[97-ssot-lint] No Quadlet dir at {} -- nothing to lint (PASS).",
            quadlet_dir.display()
        );
        process::exit(0);
    }

    println!("[97-ssot-lint] SSOT-render conformance lint");
    println!("[97-ssot-lint]   quadlets: {}", quadlet_dir.display());
    println!("[97-ssot-lint]   userenv:  {}", userenv_path.display());
    println!("[97-ssot-lint]   render:   {}", render_path.display());

    let mut quadlet_files = Vec::new();
    walk_dir_files(&quadlet_dir, &mut quadlet_files);
    quadlet_files.sort();

    let mut refs = BTreeSet::new();

    for file in &quadlet_files {
        if let Ok(content) = fs::read_to_string(file) {
            for line in content.lines() {
                if is_directive_line(line) {
                    extract_placeholders(line, &mut refs);
                }
            }
        }
    }

    if refs.is_empty() {
        println!(
            "[97-ssot-lint] No ${{MIOS_*}} placeholders in any Exec=/Environment= line (PASS)."
        );
        process::exit(0);
    }

    let userenv_content = fs::read_to_string(&userenv_path).unwrap_or_default();
    let render_content = fs::read_to_string(&render_path).unwrap_or_default();

    let userenv_lines = filter_non_comments(&userenv_content);
    let render_lines = filter_non_comments(&render_content);

    let mut orphans = 0;
    let mut checked = 0;

    for v in &refs {
        checked += 1;
        let in_ue = in_userenv(v, &userenv_lines);
        let in_rq = in_render(v, &render_lines);

        if in_ue && in_rq {
            continue;
        }

        orphans += 1;
        let mut miss = String::new();
        if !in_ue {
            miss.push_str("userenv.sh slot/export");
        }
        if !in_rq {
            if !miss.is_empty() {
                miss.push_str(" + resolver emission (env-baseline)");
            } else {
                miss.push_str("resolver emission (env-baseline)");
            }
        }
        eprintln!(
            "[97-ssot-lint] ERROR: dead key ${} -- referenced in a Quadlet Exec=/Environment= line but MISSING from: {}",
            v, miss
        );
    }

    println!("[97-ssot-lint] ---------------------------------------------------------");
    println!(
        "[97-ssot-lint] checked {} placeholder(s); {} orphan(s).",
        checked, orphans
    );

    if orphans == 0 {
        println!("[97-ssot-lint] PASS: every ${{MIOS_*}} placeholder is wired on both ends.");
        process::exit(0);
    }

    eprintln!(
        "[97-ssot-lint] FAIL: {} orphaned key(s) above are un-tunable (collapse to their inline :-default).",
        orphans
    );
    eprintln!("[97-ssot-lint]   Fix each by (a) adding a typed slot in tools/lib/userenv.sh AND");
    eprintln!(
        "[97-ssot-lint]   (b) ensuring the resolver emits it (it then renders without an allowlist)."
    );

    if soft_mode {
        println!("[97-ssot-lint] (MIOS_SSOT_LINT_SOFT=1 -> advisory mode, exiting 0)");
        process::exit(0);
    }
    process::exit(1);
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_is_directive_line() {
        assert!(is_directive_line("ExecStart=/usr/bin/foo"));
        assert!(is_directive_line("Environment=MIOS_PORT=80"));
        assert!(!is_directive_line("Description=Test"));
    }

    #[test]
    fn test_extract_placeholders() {
        let line = "ExecStart=/bin/app --port=${MIOS_PORT:-8080} --host=${MIOS_HOST}";
        let mut refs = BTreeSet::new();
        extract_placeholders(line, &mut refs);
        assert!(refs.contains("MIOS_PORT"));
        assert!(refs.contains("MIOS_HOST"));
        assert_eq!(refs.len(), 2);
    }
}
