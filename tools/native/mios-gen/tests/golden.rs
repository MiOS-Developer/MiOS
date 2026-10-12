// AI-hint: Executes every console case in tests/golden/README.md against the built mios-gen: exit code and combined output, line for line, with [..] and [CWD] (task_1707).
// AI-related: tests/golden/README.md, tests/golden/fastfetch/mock.jsonc, tools/native/mios-gen/src/main.rs

use std::io::Read;
use std::path::PathBuf;
use std::process::Command;

fn repo_root() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .ancestors()
        .nth(3)
        .expect("repo root above tools/native/mios-gen")
        .to_path_buf()
}

#[derive(Debug)]
struct Case {
    line: usize,
    command: String,
    code: i32,
    expected: Vec<String>,
}

/// The `console` blocks of a markdown file, as trycmd reads them.
fn parse(markdown: &str) -> Vec<Case> {
    let (mut cases, mut in_block) = (Vec::<Case>::new(), false);
    for (n, line) in markdown.lines().enumerate() {
        if !in_block {
            in_block = line.trim_end() == "```console";
            continue;
        }
        if line.trim_end() == "```" {
            in_block = false;
        } else if let Some(cmd) = line.strip_prefix("$ ") {
            let (line, command) = (n + 1, cmd.trim().to_string());
            cases.push(Case {
                line,
                command,
                code: 0,
                expected: Vec::new(),
            });
        } else if let Some(case) = cases.last_mut() {
            match line.strip_prefix("? ") {
                Some(code) if case.expected.is_empty() => {
                    case.code = code.trim().parse().expect("? <exit code>");
                }
                _ => case.expected.push(line.to_string()),
            }
        } else {
            panic!("line {}: output before any `$` command", n + 1);
        }
    }
    assert!(!in_block, "unterminated console block");
    for case in &mut cases {
        while case.expected.last().is_some_and(|l| l.trim().is_empty()) {
            case.expected.pop();
        }
    }
    cases
}

/// One expected line against one actual line; `[..]` matches any run of text.
fn line_matches(expected: &str, actual: &str) -> bool {
    let mut parts = expected.split("[..]");
    let first = parts.next().unwrap_or("");
    let Some(mut rest) = actual.strip_prefix(first) else {
        return false;
    };
    let tail: Vec<&str> = parts.collect();
    let Some((last, middle)) = tail.split_last() else {
        return rest.is_empty();
    };
    for part in middle {
        match rest.find(part) {
            Some(i) => rest = &rest[i + part.len()..],
            None => return false,
        }
    }
    rest.len() >= last.len() && rest.ends_with(last)
}

fn output_matches(expected: &[String], actual: &str) -> bool {
    let actual: Vec<&str> = actual.trim_end().lines().collect();
    actual.len() == expected.len()
        && expected
            .iter()
            .zip(&actual)
            .all(|(e, a)| line_matches(e, a))
}

/// Runs one case from the repository root with stdout and stderr on one pipe.
fn run(case: &Case, root: &str) -> (i32, String) {
    let words: Vec<String> = case
        .command
        .split_whitespace()
        .map(|w| w.replace("[CWD]", root))
        .collect();
    assert_eq!(
        words.first().map(String::as_str),
        Some("mios-gen"),
        "line {}: a golden case runs mios-gen, nothing else",
        case.line
    );
    let (mut reader, writer) = std::io::pipe().expect("pipe");
    let mut cmd = Command::new(env!("CARGO_BIN_EXE_mios-gen"));
    cmd.args(&words[1..]).current_dir(root);
    for (key, _) in std::env::vars_os() {
        if key.to_string_lossy().starts_with("MIOS_") {
            cmd.env_remove(key);
        }
    }
    cmd.stdout(writer.try_clone().expect("pipe clone"))
        .stderr(writer);
    let mut child = cmd.spawn().expect("spawn mios-gen");
    drop(cmd);
    let mut out = String::new();
    reader.read_to_string(&mut out).expect("read output");
    let status = child.wait().expect("wait mios-gen");
    (status.code().unwrap_or(-1), out)
}

#[test]
fn every_golden_case_in_the_readme_holds() {
    let root = repo_root();
    let readme = root.join("tests/golden/README.md");
    let cases = parse(&std::fs::read_to_string(&readme).expect("tests/golden/README.md"));
    let root = root.to_string_lossy().into_owned();
    assert!(cases.iter().any(|c| c.code == 0), "no positive control");
    assert!(cases.iter().any(|c| c.code != 0), "no negative control");
    let mut failures = Vec::new();
    for case in &cases {
        let (code, out) = run(case, &root);
        let want: Vec<String> = case
            .expected
            .iter()
            .map(|l| l.replace("[CWD]", &root))
            .collect();
        if code != case.code || !output_matches(&want, &out) {
            failures.push(format!(
                "README.md:{} $ {}\n  want ? {}\n{}\n  got ? {}\n{}",
                case.line,
                case.command,
                case.code,
                want.join("\n"),
                code,
                out.trim_end()
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} golden case(s) failed:\n{}",
        failures.len(),
        cases.len(),
        failures.join("\n\n")
    );
}

#[test]
fn the_matcher_rejects_what_it_should() {
    assert!(line_matches("a [..] c", "a bb c"));
    assert!(line_matches("x[..]", "x"));
    assert!(!line_matches("a [..] c", "a bb d"));
    assert!(!line_matches("exact", "exact plus"));
    assert!(!line_matches("[..]ab[..]ab", "ab"));
    assert!(!output_matches(&["one".into()], "one\ntwo"));
    let cases = parse("```console\n$ mios-gen v --check\n? 2\nboom\n\n```\n");
    assert_eq!(
        (cases.len(), cases[0].code, cases[0].expected.len()),
        (1, 2, 1)
    );
}
