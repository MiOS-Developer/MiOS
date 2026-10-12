// AI-hint: Self-test, negative test coverage, and hermeticity checks for miosd drift runner.
// AI-related: tests/drift-gate-negatives.sh, .github/workflows/mios-ci.yml

use super::{Check, DriftCtx, Verdict};

pub struct NegativeTestCoverageCheck;
impl Check for NegativeTestCoverageCheck {
    fn id(&self) -> &'static str {
        "check_negative_test_coverage"
    }
    fn describe(&self) -> &'static str {
        "Assert every registered check has a corresponding test in drift-gate-negatives.sh"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        super::audit::native(ctx, "mios-gate", &["negative-coverage"])
    }
}

pub struct SoftModeNotCommittedCheck;
impl Check for SoftModeNotCommittedCheck {
    fn id(&self) -> &'static str {
        "check_soft_mode_not_committed"
    }
    fn describe(&self) -> &'static str {
        "Assert no MIOS_DRIFT_CHECK_SOFT=1 mode is committed in workflows or Justfile"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        super::audit::verdict((|| {
            let mut paths = vec!["automation/build.sh".to_owned(), "Justfile".to_owned()];
            for directory in [".github/workflows", ".forgejo/workflows"] {
                if ctx.root.join(directory).exists() {
                    paths.extend(
                        super::audit::files(&ctx.root, directory)?
                            .into_iter()
                            .filter(|p| p.ends_with(".yml") || p.ends_with(".yaml")),
                    );
                }
            }
            let mut errors = Vec::new();
            let pattern =
                regex::Regex::new(r#"MIOS_(?:DRIFT_CHECK|SSOT_LINT)_SOFT\s*(?:=|:)\s*['\"]?1"#)
                    .map_err(|e| e.to_string())?;
            for path in &paths {
                for (line, body) in super::audit::read(&ctx.root, path)?.lines().enumerate() {
                    if pattern.is_match(body) {
                        errors.push(format!("{path}:{}: committed soft-mode override", line + 1));
                    }
                }
            }
            super::audit::finish(paths.len(), errors, "CI/build hard enforcement")
        })())
    }
}
