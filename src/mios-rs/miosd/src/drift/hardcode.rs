// AI-hint: Hardcode linting with anchored allowlist for miosd drift runner.
// AI-related: usr/libexec/mios/mios-hardcode-lint, usr/share/mios/mios.toml

use super::{Check, DriftCtx, Verdict};

pub struct HardcodeLintCheck;
impl Check for HardcodeLintCheck {
    fn id(&self) -> &'static str {
        "check_no_hardcode"
    }
    fn describe(&self) -> &'static str {
        "Assert no un-exempted IP, port, or secret hardcodes exist in codebase"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        super::audit::native(ctx, "mios-hardcode-lint", &[])
    }
}

pub struct HardcodeVersionCheck;
impl Check for HardcodeVersionCheck {
    fn id(&self) -> &'static str {
        "check_no_hardcode_version"
    }
    fn describe(&self) -> &'static str {
        "Assert no hardcoded Fedora version literals exist outside SSOT"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        super::audit::native(ctx, "mios-gate", &["version-literals-ssot"])
    }
}

pub struct HardcodedSSOTLiteralCheck;
impl Check for HardcodedSSOTLiteralCheck {
    fn id(&self) -> &'static str {
        "check_no_hardcoded_ssot_literal"
    }
    fn describe(&self) -> &'static str {
        "Assert no hardcoded version literals exist in SSOT files"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        super::audit::verdict((|| {
            let pattern = regex::Regex::new(r"fedora-[0-9]{2}|stable:/v[0-9]+\.[0-9]+")
                .map_err(|e| e.to_string())?;
            let mut errors = Vec::new();
            let mut count = 0;
            for directory in ["automation", "usr/share/mios", "usr/share/containers"] {
                for path in super::audit::files(&ctx.root, directory)? {
                    if path.ends_with("98-drift-checks.sh")
                        || path.ends_with("mios.toml")
                        || path.ends_with(".repo")
                        || ["/reference/", "/artifacts/", "/configurator/", "/.claude/"]
                            .iter()
                            .any(|p| path.contains(p))
                    {
                        continue;
                    }
                    let bytes =
                        std::fs::read(ctx.root.join(&path)).map_err(|e| format!("{path}: {e}"))?;
                    if bytes.contains(&0) {
                        continue;
                    }
                    let Ok(body) = std::str::from_utf8(&bytes) else {
                        continue;
                    };
                    count += 1;
                    for (line, text) in body.lines().enumerate() {
                        if pattern.is_match(text)
                            && !["fedora-$", "fedora-%", "$MIOS_", "$FEDORA_", "mios.toml"]
                                .iter()
                                .any(|s| text.contains(s))
                        {
                            errors.push(format!(
                                "{path}:{}: hardcoded SSOT version literal",
                                line + 1
                            ));
                        }
                    }
                }
            }
            super::audit::finish(
                count,
                errors,
                "Fedora/Kubernetes source version literal scan",
            )
        })())
    }
}
