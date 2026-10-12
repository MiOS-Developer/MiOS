// AI-hint: Governance and language policy checks for miosd drift runner.
// AI-related: usr/share/mios/mios.toml [laws.target_languages]

use super::audit::{self, files, finish, read, ssot, strings};
use super::{Check, DriftCtx, Verdict};

pub struct TargetLanguagesCheck;
impl Check for TargetLanguagesCheck {
    fn id(&self) -> &'static str {
        "check_target_languages"
    }
    fn describe(&self) -> &'static str {
        "Assert codebase strictly adheres to Law 14 target language domain mapping"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict((|| {
            let policy = ssot(ctx)?;
            let allowed = strings(&policy, "laws.target_languages.grandfathered_cs")?;
            let mut paths = audit::tracked(ctx)?;
            let output = std::process::Command::new("git")
                .arg("-C")
                .arg(&ctx.root)
                .args(["ls-files", "--others", "--exclude-standard", "-z"])
                .output()
                .map_err(|e| e.to_string())?;
            if !output.status.success() {
                return Err(format!(
                    "git untracked census {}: {}",
                    output.status,
                    String::from_utf8_lossy(&output.stderr)
                ));
            }
            paths.extend(
                String::from_utf8(output.stdout)
                    .map_err(|e| e.to_string())?
                    .split('\0')
                    .filter(|s| !s.is_empty())
                    .map(str::to_owned),
            );
            let errors = paths
                .iter()
                .filter(|p| {
                    !p.starts_with("tools/mios-portal-app/")
                        && ([".bat", ".cmd", ".go", ".cpp", ".cxx", ".cc"]
                            .iter()
                            .any(|ext| p.ends_with(ext))
                            || (p.ends_with(".cs") && !allowed.contains(p)))
                })
                .map(|p| {
                    format!("{p}: non-target language outside the declared grandfather roster")
                })
                .collect();
            finish(paths.len(), errors, "Law 14 language audit")
        })())
    }
}

pub struct LintIsFinalCheck;
impl Check for LintIsFinalCheck {
    fn id(&self) -> &'static str {
        "check_lint_is_final"
    }
    fn describe(&self) -> &'static str {
        "Assert lint enforcement is non-bypassable"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict((|| {
            let paths = files(&ctx.root, "")?;
            let label =
                regex::Regex::new(r#"(?i)^LABEL\s+.*containers\.bootc=[\"']?1(?:[\"']|\s|$)"#)
                    .map_err(|e| e.to_string())?;
            let mut count = 0;
            let mut errors = Vec::new();
            for path in paths
                .iter()
                .filter(|p| !p.contains('/') && p.starts_with("Containerfile"))
            {
                let body = read(&ctx.root, path)?;
                let lines: Vec<_> = body
                    .lines()
                    .map(str::trim)
                    .filter(|s| !s.is_empty() && !s.starts_with('#'))
                    .collect();
                let bootc = lines.iter().any(|s| label.is_match(s));
                if bootc {
                    count += 1;
                    if lines.last().copied() != Some("RUN bootc container lint") {
                        errors.push(format!(
                            "{path}: final instruction must be RUN bootc container lint"
                        ));
                    }
                } else if lines.iter().any(|s| s.contains("RUN bootc container lint")) {
                    errors.push(format!(
                        "{path}: non-bootc image invokes bootc container lint"
                    ));
                }
            }
            finish(count, errors, "bootc final lint instruction")
        })())
    }
}
