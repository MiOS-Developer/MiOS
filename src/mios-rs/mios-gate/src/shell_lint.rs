// AI-hint: ShellCheck gate over the SSOT [drift.lint] shell subjects: repo-wide at shell_severity, files changed since the ratchet base at shell_changed_severity; a missing tool fails closed.
// AI-related: automation/lint-shell.sh, usr/share/mios/mios.toml, src/mios-rs/miosd/src/drift/security.rs
use crate::Report;
use std::path::Path;
use std::process::Command;

const CHECK: &str = "shell-lint";
const SEVERITIES: [&str; 4] = ["error", "warning", "info", "style"];

struct Policy {
    tool: String,
    severity: String,
    changed_severity: String,
    bases: Vec<String>,
    directories: Vec<String>,
    exclude: Vec<String>,
}

fn policy(root: &Path) -> Result<Policy, String> {
    let text = std::fs::read_to_string(root.join("usr/share/mios/mios.toml"))
        .map_err(|_| "usr/share/mios/mios.toml is unreadable".to_string())?;
    let doc: toml::Value =
        toml::from_str(&text).map_err(|e| format!("mios.toml does not parse: {e}"))?;
    let lint = doc
        .get("drift")
        .and_then(|d| d.get("lint"))
        .ok_or("mios.toml has no [drift.lint]")?;
    let word = |key: &str| {
        lint.get(key)
            .and_then(toml::Value::as_str)
            .filter(|v| !v.trim().is_empty())
            .map(str::to_string)
            .ok_or(format!("[drift.lint].{key} is missing or empty"))
    };
    let list = |key: &str| -> Result<Vec<String>, String> {
        lint.get(key)
            .and_then(toml::Value::as_array)
            .ok_or(format!("[drift.lint].{key} is missing"))?
            .iter()
            .map(|v| v.as_str().map(str::to_string))
            .collect::<Option<Vec<_>>>()
            .ok_or(format!("[drift.lint].{key} holds a non-string"))
    };
    let p = Policy {
        tool: word("shellcheck")?,
        severity: word("shell_severity")?,
        changed_severity: word("shell_changed_severity")?,
        bases: list("ratchet_bases")?,
        directories: list("shell_directories")?,
        exclude: list("shell_exclude")?,
    };
    for s in [&p.severity, &p.changed_severity] {
        if !SEVERITIES.contains(&s.as_str()) {
            return Err(format!(
                "[drift.lint] severity {s:?} is not a ShellCheck level"
            ));
        }
    }
    for path in p.directories.iter().chain(&p.exclude) {
        if path.starts_with('/') || path.contains('\\') || path.split('/').any(|x| x == "..") {
            return Err(format!("unconfined [drift.lint] path: {path}"));
        }
    }
    Ok(p)
}

fn git(root: &Path, args: &[&str]) -> Result<String, String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(root)
        .args(args)
        .output()
        .map_err(|_| "git is unavailable".to_string())?;
    if !out.status.success() {
        return Err(format!("git {} failed", args.join(" ")));
    }
    String::from_utf8(out.stdout).map_err(|_| "git printed a non-UTF-8 path".to_string())
}

/// Tracked shell sources in an SSOT directory: `*.sh` or a bash/sh shebang.
fn subjects(root: &Path, p: &Policy) -> Result<Vec<String>, String> {
    let mut files = Vec::new();
    for path in git(root, &["ls-files", "-z"])?
        .split('\0')
        .filter(|s| !s.is_empty())
    {
        if p.exclude.iter().any(|e| e == path) {
            continue;
        }
        let parent = path.rsplit_once('/').map_or("", |(d, _)| d);
        if !p.directories.iter().any(|d| d == parent) {
            continue;
        }
        let absolute = root.join(path);
        // A tracked symlink is linted where its target lives (a systemd mask points at /dev/null).
        if std::fs::symlink_metadata(&absolute).is_ok_and(|m| m.file_type().is_symlink()) {
            continue;
        }
        let data =
            std::fs::read(&absolute).map_err(|_| format!("tracked subject unreadable: {path}"))?;
        let first = data.split(|b| *b == b'\n').next().unwrap_or_default();
        let shebang = String::from_utf8_lossy(first);
        let shell = shebang.starts_with("#!")
            && (shebang.contains("bash") || shebang.ends_with("/sh") || shebang.ends_with(" sh"));
        if path.ends_with(".sh") || shell {
            files.push(path.to_string());
        }
    }
    if files.is_empty() {
        return Err(
            "no shell subject in the [drift.lint] directories; an empty census is not a pass"
                .into(),
        );
    }
    Ok(files)
}

/// One ShellCheck pass; a finding per line (gcc format), an unrunnable tool is an error.
fn shellcheck(
    root: &Path,
    tool: &str,
    severity: &str,
    files: &[String],
) -> Result<Vec<String>, String> {
    if files.is_empty() {
        return Ok(Vec::new());
    }
    let out = Command::new(tool)
        .arg(format!("--severity={severity}"))
        .arg("--format=gcc")
        .arg("--")
        .args(files)
        .current_dir(root)
        .output()
        .map_err(|_| format!("required ShellCheck is unavailable: {tool}"))?;
    match out.status.code() {
        Some(0) => Ok(Vec::new()),
        Some(1) => Ok(String::from_utf8_lossy(&out.stdout)
            .lines()
            .filter(|l| !l.trim().is_empty())
            .map(|l| format!("{severity}: {l}"))
            .collect()),
        code => Err(format!("ShellCheck could not run (exit {code:?})")),
    }
}

/// The first ratchet base that names a commit; an explicit override that does not is an error.
fn base(root: &Path, p: &Policy, explicit: Option<String>) -> Result<Option<String>, String> {
    let names = |r: &str| {
        git(
            root,
            &[
                "rev-parse",
                "--verify",
                "--quiet",
                &format!("{r}^{{commit}}"),
            ],
        )
        .is_ok()
    };
    if let Some(b) = explicit.filter(|b| !b.is_empty()) {
        return if names(&b) {
            Ok(Some(b))
        } else {
            Err(format!(
                "MIOS_RATCHET_BASE={b} does not name a commit in this checkout"
            ))
        };
    }
    Ok(p.bases.iter().find(|b| names(b)).cloned())
}

fn run(root: &Path, explicit_base: Option<String>) -> Result<Report, String> {
    let p = policy(root)?;
    let all = subjects(root, &p)?;
    let mut findings = shellcheck(root, &p.tool, &p.severity, &all)?;
    let summary = match base(root, &p, explicit_base)? {
        Some(b) => {
            let changed: Vec<String> = git(
                root,
                &["diff", "--name-only", "-z", "--diff-filter=ACMRT", &b],
            )?
            .split('\0')
            .filter(|f| all.iter().any(|s| s == f))
            .map(str::to_string)
            .collect();
            findings.extend(shellcheck(root, &p.tool, &p.changed_severity, &changed)?);
            format!(
                "{} shell subject(s) clean at {}; {} changed since {b} clean at {}",
                all.len(),
                p.severity,
                changed.len(),
                p.changed_severity
            )
        }
        None => format!(
            "{} shell subject(s) clean at {}; changed-file pass NOT run: no ratchet base resolves",
            all.len(),
            p.severity
        ),
    };
    Ok(Report {
        check: CHECK.into(),
        ok: findings.is_empty(),
        could_not_run: None,
        summary,
        findings,
    })
}

pub fn check(root: &Path) -> Report {
    run(root, std::env::var("MIOS_RATCHET_BASE").ok()).unwrap_or_else(|why| Report {
        check: CHECK.into(),
        ok: false,
        could_not_run: Some(why),
        summary: String::new(),
        findings: Vec::new(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    const POLICY: &str = "[drift.lint]\nshellcheck = \"shellcheck\"\nshell_severity = \"error\"\n\
                          shell_changed_severity = \"warning\"\nratchet_bases = [\"HEAD~1\"]\n\
                          shell_directories = [\"\", \"bin\"]\nshell_exclude = [\"bin/skip.sh\"]\n";

    fn repo(policy: &str) -> Result<tempfile::TempDir, Box<dyn std::error::Error>> {
        let dir = tempfile::tempdir()?;
        let r = dir.path();
        std::fs::create_dir_all(r.join("usr/share/mios"))?;
        std::fs::create_dir_all(r.join("bin"))?;
        std::fs::create_dir_all(r.join("other"))?;
        std::fs::write(r.join("usr/share/mios/mios.toml"), policy)?;
        std::fs::write(r.join("top.sh"), "#!/bin/bash\necho ok\n")?;
        std::fs::write(r.join("bin/tool"), "#!/usr/bin/env bash\necho ok\n")?;
        std::fs::write(r.join("bin/skip.sh"), "#!/bin/bash\nif [ ; then\n")?;
        std::fs::write(r.join("bin/data.txt"), "not shell\n")?;
        std::fs::write(r.join("other/elsewhere.sh"), "#!/bin/bash\nif [ ; then\n")?;
        for args in [
            &["init", "-q"][..],
            &["add", "."],
            &[
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@t",
                "commit",
                "-qm",
                "a",
            ],
        ] {
            Command::new("git").arg("-C").arg(r).args(args).output()?;
        }
        Ok(dir)
    }

    #[test]
    fn selects_the_ssot_directories_and_rejects_what_it_should(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let dir = repo(POLICY)?;
        let p = policy(dir.path())?;
        assert_eq!(
            subjects(dir.path(), &p)?,
            vec!["bin/tool".to_string(), "top.sh".to_string()]
        );
        let clean = run(dir.path(), None)?;
        assert!(clean.ok, "{:?}", clean.findings);
        std::fs::write(dir.path().join("top.sh"), "#!/bin/bash\nif [ ; then\n")?;
        let broken = run(dir.path(), None)?;
        assert!(!broken.ok && broken.findings.iter().any(|f| f.contains("top.sh")));
        assert!(run(dir.path(), Some("no-such-base".into())).is_err());
        let missing = POLICY.replace(
            "shellcheck = \"shellcheck\"",
            "shellcheck = \"missing-mios-shellcheck\"",
        );
        assert!(run(repo(&missing)?.path(), None).is_err());
        let empty = POLICY.replace("[\"\", \"bin\"]", "[\"nowhere\"]");
        assert!(run(repo(&empty)?.path(), None).is_err());
        let unconfined = POLICY.replace("[\"\", \"bin\"]", "[\"../outside\"]");
        assert!(policy(repo(&unconfined)?.path()).is_err());
        Ok(())
    }
}
