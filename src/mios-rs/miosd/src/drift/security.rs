// AI-hint: Security, shellcheck, and eval safety checks for miosd drift runner.
// AI-related: automation/lint-python.sh, automation/98-drift-checks.sh, usr/libexec/mios

use super::audit;
use super::{Check, DriftCtx, Verdict};
use regex::Regex;
use std::process::Command;

fn compiler_lint(ctx: &DriftCtx, python: bool) -> audit::Audit {
    let config = audit::ssot(ctx)?;
    let tool_key = if python {
        "drift.lint.python"
    } else {
        "drift.lint.shellcheck"
    };
    let tool = audit::at(&config, tool_key)?
        .as_str()
        .filter(|s| !s.trim().is_empty())
        .ok_or_else(|| format!("Invalid SSOT {tool_key}"))?;
    let excluded = audit::strings(
        &config,
        if python {
            "drift.lint.python_exclude"
        } else {
            "drift.lint.shell_exclude"
        },
    )?;
    let directories = audit::strings(&config, "drift.lint.shell_directories")?;
    for path in excluded.iter().chain(directories.iter()) {
        if path.starts_with('/') || path.contains('\\') || path.split('/').any(|part| part == "..")
        {
            return Err(format!("Unconfined lint policy path: {path}"));
        }
    }
    let severity = audit::at(&config, "drift.lint.shell_severity")?
        .as_str()
        .filter(|s| matches!(*s, "error" | "warning" | "info" | "style"))
        .ok_or("Invalid SSOT drift.lint.shell_severity")?;
    let mut subjects = 0;
    let mut errors = Vec::new();
    for path in audit::tracked(ctx)? {
        if excluded.contains(&path) {
            continue;
        }
        let absolute = ctx.root.join(&path);
        // A tracked symlink is not source: its target is linted where it lives,
        // and a systemd mask points at /dev/null.
        if std::fs::symlink_metadata(&absolute).is_ok_and(|m| m.file_type().is_symlink()) {
            continue;
        }
        if !absolute.is_file() {
            return Err(format!("Tracked lint subject missing: {path}"));
        }
        let data = std::fs::read(&absolute).map_err(|e| format!("{path}: {e}"))?;
        let first = data.split(|b| *b == b'\n').next().unwrap_or_default();
        let shebang = std::str::from_utf8(first).unwrap_or("");
        let selected = if python {
            path.ends_with(".py") || (shebang.starts_with("#!") && shebang.contains("python"))
        } else {
            let parent = path
                .rsplit_once('/')
                .map(|(parent, _)| parent)
                .unwrap_or("");
            directories.iter().any(|directory| directory == parent)
                && (path.ends_with(".sh")
                    || (shebang.starts_with("#!")
                        && (shebang.contains("bash")
                            || shebang.ends_with("/sh")
                            || shebang.ends_with(" sh"))))
        };
        if !selected {
            continue;
        }
        subjects += 1;
        let mut command = Command::new(tool);
        if python {
            // Python is the language compiler, not the gate implementation.
            // Parsing bytes preserves encoding cookies; no module executes and
            // no __pycache__ is written into the shared source tree.
            command.args(["-c", "import sys; compile(open(sys.argv[1], 'rb').read(), sys.argv[1], 'exec', dont_inherit=True)"]);
        } else {
            command.arg(format!("--severity={severity}")).arg("--");
        }
        let output = command
            .arg(&absolute)
            .current_dir(&ctx.root)
            .output()
            .map_err(|e| format!("Required lint compiler {tool}: {e}"))?;
        if !output.status.success() {
            errors.push(format!(
                "{path}: {tool} {}\n{}{}",
                output.status,
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr)
            ));
        }
    }
    audit::finish(
        subjects,
        errors,
        if python {
            "Python syntax compiler"
        } else {
            "ShellCheck"
        },
    )
}

pub struct CLIEvalSafetyCheck;
impl Check for CLIEvalSafetyCheck {
    fn id(&self) -> &'static str {
        "check_cli_eval_safety"
    }
    fn describe(&self) -> &'static str {
        "Assert no unsafe eval-on-agent-args pattern exists in CLI verb scripts"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict(eval_safety(ctx))
    }
}

const VERB_DIR: &str = "usr/libexec/mios";
// Legacy floor: fewer shell verb backends than this means the corpus is wrong
// (wrong root, gutted tree), so an empty violation list is not a pass.
const MIN_SHELL_BACKENDS: usize = 20;

/// A shell verb backend: a `.sh` file or one whose shebang runs a POSIX-family
/// shell (directly or through `env`). The legacy test was `"sh" in first_line`,
/// which also swept in PowerShell files whose first comment says "netsh" or
/// "PowerShell"; `eval` means nothing there.
fn shell_backend(name: &str, first: &str) -> bool {
    if name.ends_with(".sh") {
        return true;
    }
    let Some(command) = first.strip_prefix("#!") else {
        return false;
    };
    let mut words = command.split_whitespace();
    let mut interpreter = words
        .next()
        .unwrap_or_default()
        .rsplit('/')
        .next()
        .unwrap_or_default();
    if interpreter == "env" {
        interpreter = words
            .find(|w| !w.starts_with('-') && !w.contains('='))
            .unwrap_or_default()
            .rsplit('/')
            .next()
            .unwrap_or_default();
    }
    matches!(
        interpreter,
        "sh" | "bash" | "dash" | "ash" | "ksh" | "mksh" | "zsh"
    )
}

/// TD-1: a shell verb must not `eval` (agent-controlled input becomes code).
/// TD-2: no file under the verb tree may call os.system() (shell-string exec).
/// Both scan the whole tree recursively, skipping dot-directories as the legacy
/// os.walk did. A reviewed eval of non-agent input is accepted only with the
/// annotation the legacy gate's own remedy text prescribes on the line directly
/// above it; that exemption was dropped from the legacy code while its message
/// kept telling authors to add it.
fn eval_safety(ctx: &DriftCtx) -> audit::Audit {
    let eval = Regex::new(r"\beval\b").map_err(|e| e.to_string())?;
    let system = Regex::new(r"\bos\.system\s*\(").map_err(|e| e.to_string())?;
    let attested = Regex::new(r"^#\s*TD-1:\s*eval-safe,\s*input=.+,\s*not agent-controlled")
        .map_err(|e| e.to_string())?;
    let (mut shells, mut files, mut errors) = (0, 0, Vec::new());
    for path in audit::files(&ctx.root, VERB_DIR)? {
        let (parent, name) = path.rsplit_once('/').unwrap_or(("", path.as_str()));
        if parent.split('/').any(|part| part.starts_with('.')) {
            continue;
        }
        let bytes = std::fs::read(ctx.root.join(&path)).map_err(|e| format!("{path}: {e}"))?;
        let text = String::from_utf8_lossy(&bytes);
        let lines: Vec<&str> = text.lines().collect();
        // Comment-only lines are skipped; a trailing comment is cut at the first '#'.
        let code = |line: &str| {
            if line.trim().starts_with('#') {
                None
            } else {
                Some(line.split('#').next().unwrap_or_default().trim().to_owned())
            }
        };
        let excluded = |suffixes: &[&str]| suffixes.iter().any(|suffix| name.ends_with(suffix));
        if !excluded(&[".py", ".pyc", ".json", ".generated"])
            && shell_backend(name, lines.first().copied().unwrap_or_default())
        {
            shells += 1;
            for (index, line) in lines.iter().enumerate() {
                let reviewed = index > 0
                    && lines
                        .get(index - 1)
                        .is_some_and(|above| attested.is_match(above.trim()));
                if code(line).is_some_and(|code| eval.is_match(&code)) && !reviewed {
                    errors.push(format!("{path}:{}: eval in a shell verb backend: {} -- verbs must not eval agent-controlled input; a reviewed eval of non-agent input needs `# TD-1: eval-safe, input=<source>, not agent-controlled` on the line above", index + 1, line.trim()));
                }
            }
        }
        if !name.starts_with("test_") && !excluded(&[".pyc", ".json", ".generated", ".png", ".jpg"])
        {
            files += 1;
            for (index, line) in lines.iter().enumerate() {
                if code(line).is_some_and(|code| system.is_match(&code)) {
                    errors.push(format!("{path}:{}: os.system() is forbidden under {VERB_DIR} (TD-2): {} -- use subprocess.run([...], check=...) with an argv list", index + 1, line.trim()));
                }
            }
        }
    }
    if shells < MIN_SHELL_BACKENDS {
        return Err(format!("{VERB_DIR}: only {shells} shell verb backend(s) read (floor {MIN_SHELL_BACKENDS}) -- the corpus is wrong, so an empty result is not a pass"));
    }
    audit::finish(
        files,
        errors,
        &format!("{shells} shell verb backend(s) eval-safe; os.system absent from {VERB_DIR}"),
    )
}

pub struct ShellcheckLintCheck;
impl Check for ShellcheckLintCheck {
    fn id(&self) -> &'static str {
        "check_shellcheck"
    }
    fn describe(&self) -> &'static str {
        "Assert all shell scripts pass shellcheck lint"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict(compiler_lint(ctx, false))
    }
}

pub struct PythonCompileLintCheck;
impl Check for PythonCompileLintCheck {
    fn id(&self) -> &'static str {
        "check_python_lint"
    }
    fn describe(&self) -> &'static str {
        "Assert all python scripts compile without syntax errors"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict(compiler_lint(ctx, true))
    }
}

#[cfg(all(test, unix))]
mod tests {
    use super::*;
    const POLICY: &str = "[drift.lint]\npython='python3'\nshellcheck='shellcheck'\nshell_severity='error'\nshell_directories=['']\nshell_exclude=[]\npython_exclude=[]\n";

    #[test]
    fn real_compilers_detect_source_mutations_without_executing_python_and_refuse_empty_or_missing_tools(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let temp = tempfile::tempdir()?;
        std::fs::create_dir_all(temp.path().join("usr/share/mios"))?;
        let policy = temp.path().join("usr/share/mios/mios.toml");
        std::fs::write(&policy, POLICY)?;
        for args in [vec!["init", "-q"], vec!["add", "usr/share/mios/mios.toml"]] {
            assert!(Command::new("git")
                .arg("-C")
                .arg(temp.path())
                .args(args)
                .status()?
                .success());
        }
        let ctx = DriftCtx::new(temp.path().into(), false);
        assert!(compiler_lint(&ctx, true).is_err());
        assert!(compiler_lint(&ctx, false).is_err());
        let python = temp.path().join("subject.py");
        let shell = temp.path().join("subject.sh");
        std::fs::write(
            &python,
            "open('SHOULD-NOT-EXECUTE', 'w').write('executed')\n",
        )?;
        std::fs::write(&shell, "#!/bin/bash\necho \"$HOME\"\n")?;
        assert!(Command::new("git")
            .arg("-C")
            .arg(temp.path())
            .args(["add", "subject.py", "subject.sh"])
            .status()?
            .success());
        assert!(compiler_lint(&ctx, true)?.contains("1 subject"));
        assert!(!temp.path().join("SHOULD-NOT-EXECUTE").exists());
        assert!(!temp.path().join("__pycache__").exists());
        assert!(compiler_lint(&ctx, false)?.contains("1 subject"));
        std::fs::write(&python, "def malformed(:\n")?;
        assert!(compiler_lint(&ctx, true)
            .is_err_and(|e| e.contains("subject.py") && e.contains("SyntaxError")));
        std::fs::write(&shell, "#!/bin/bash\nif then\n")?;
        assert!(compiler_lint(&ctx, false).is_err_and(|e| e.contains("subject.sh")));
        std::fs::write(
            &policy,
            POLICY.replace(
                "shellcheck='shellcheck'",
                "shellcheck='missing-mios-lint-compiler'",
            ),
        )?;
        assert!(compiler_lint(&ctx, false).is_err_and(|e| e.contains("Required lint compiler")));
        std::fs::write(
            &policy,
            POLICY.replace("shell_directories=['']", "shell_directories=['../outside']"),
        )?;
        assert!(compiler_lint(&ctx, false).is_err_and(|e| e.contains("Unconfined")));
        Ok(())
    }
}

#[cfg(test)]
mod eval_tests {
    use super::*;

    #[test]
    fn eval_safety_flags_eval_and_os_system_with_location_honours_td1_and_fails_thin_or_absent_corpus(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let temp = tempfile::tempdir()?;
        let ctx = DriftCtx::new(temp.path().into(), false);
        assert!(eval_safety(&ctx)
            .is_err_and(|e| e.contains("required source directory usr/libexec/mios is missing")));
        let dir = temp.path().join(VERB_DIR);
        std::fs::create_dir_all(dir.join("lib"))?;
        std::fs::create_dir_all(dir.join(".cache"))?;
        for index in 0..MIN_SHELL_BACKENDS {
            std::fs::write(
                dir.join(format!("verb-{index:02}")),
                "#!/usr/bin/env bash\necho ok # eval is only named in a comment\n# eval \"$1\"\n",
            )?;
        }
        // Not shell: PowerShell (the legacy "sh" substring matched "netsh") and Python.
        std::fs::write(
            dir.join("Setup-Proxy.ps1"),
            "# configures netsh portproxy\n$mode = 'eval'\n",
        )?;
        std::fs::write(
            dir.join("mios-model"),
            "#!/usr/bin/env python3\nmodel.eval()\n",
        )?;
        std::fs::write(dir.join("test_safety.py"), "import os\nos.system('ls')\n")?;
        std::fs::write(dir.join(".cache/stale.py"), "import os\nos.system('ls')\n")?;
        assert!(eval_safety(&ctx)?.contains(&format!("{MIN_SHELL_BACKENDS} shell verb backend(s)")));
        std::fs::write(
            dir.join("verb-03"),
            "#!/bin/bash\ninputs=\"$(mios-resolver --emit=build-shell)\"\neval \"$inputs\"\n",
        )?;
        assert!(eval_safety(&ctx).is_err_and(|e| e.contains(
            "usr/libexec/mios/verb-03:3: eval in a shell verb backend: eval \"$inputs\""
        )));
        std::fs::write(dir.join("verb-03"), "#!/bin/bash\ninputs=\"$(mios-resolver --emit=build-shell)\"\n# TD-1: eval-safe, input=mios-resolver --emit=build-shell, not agent-controlled\neval \"$inputs\"\n")?;
        assert!(eval_safety(&ctx).is_ok());
        std::fs::write(dir.join("lib/helper.sh"), "run() {\n  eval \"$@\"\n}\n")?;
        assert!(
            eval_safety(&ctx).is_err_and(|e| e.contains("usr/libexec/mios/lib/helper.sh:2: eval"))
        );
        std::fs::remove_file(dir.join("lib/helper.sh"))?;
        std::fs::write(
            dir.join("lib/run.py"),
            "import os\n\nos.system(f'rm {path}')\n",
        )?;
        assert!(eval_safety(&ctx)
            .is_err_and(|e| e.contains("usr/libexec/mios/lib/run.py:3: os.system() is forbidden")));
        std::fs::remove_file(dir.join("lib/run.py"))?;
        std::fs::remove_file(dir.join("verb-00"))?;
        assert!(eval_safety(&ctx).is_err_and(|e| e.contains(&format!(
            "only {} shell verb backend(s)",
            MIN_SHELL_BACKENDS - 1
        ))));
        Ok(())
    }

    #[test]
    fn shell_backends_are_recognised_by_extension_or_shell_shebang_only() {
        assert!(shell_backend("x.sh", ""));
        assert!(shell_backend("x", "#!/bin/sh -e"));
        assert!(shell_backend("x", "#!/usr/bin/env -S bash -euo pipefail"));
        assert!(!shell_backend("x", "#!/usr/bin/env python3"));
        assert!(!shell_backend("x.ps1", "# netsh portproxy helper"));
    }
}
