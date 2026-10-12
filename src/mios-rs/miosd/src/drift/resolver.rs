// AI-hint: Resolver parity and cross-language equivalence checks for miosd drift runner.
// AI-related: tools/native/mios-resolver, automation/lib/globals.ps1, tools/lib/userenv.sh, usr/lib/mios/userenv.sh, automation/59-tools.sh

use super::{Check, DriftCtx, Verdict};

pub struct ResolverParityCheck;
impl Check for ResolverParityCheck {
    fn id(&self) -> &'static str {
        "check_userenv_parity"
    }
    fn describe(&self) -> &'static str {
        "Assert single-sourced resolver parity across shell and Python implementations"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        super::audit::verdict(userenv_parity(ctx))
    }
}

const USERENV_AUTHORITATIVE: &str = "tools/lib/userenv.sh";
const USERENV_SHIPPED: &str = "usr/lib/mios/userenv.sh";

/// The shipped twin must be byte-identical to the authoritative one, which
/// 59-tools.sh installs over it at bake. Byte-exact on purpose: both are `*.sh`
/// under one `.gitattributes` eol rule, so every checkout and bake gives them the
/// same line endings, and a twin differing only by CR is a hand-copied file whose
/// CRs would reach bash. Normalising would hide exactly that.
fn userenv_parity(ctx: &DriftCtx) -> super::audit::Audit {
    let read = |path: &str| std::fs::read(ctx.root.join(path)).map_err(|e| format!("{path}: {e}"));
    let (authoritative, shipped) = match (read(USERENV_AUTHORITATIVE), read(USERENV_SHIPPED)) {
        (Ok(authoritative), Ok(shipped)) => (authoritative, shipped),
        (left, right) => {
            // Both twins are tracked deliverables: absence is a defect, never a pass.
            let missing: Vec<_> = [left.err(), right.err()].into_iter().flatten().collect();
            return Err(format!(
                "userenv.sh twin missing, so parity is unverifiable: {}",
                missing.join("; ")
            ));
        }
    };
    if authoritative == shipped {
        return Ok(format!(
            "{USERENV_SHIPPED} matches authoritative {USERENV_AUTHORITATIVE} ({} bytes)",
            shipped.len()
        ));
    }
    let line = authoritative
        .split(|b| *b == b'\n')
        .zip(shipped.split(|b| *b == b'\n'))
        .position(|(a, b)| a != b)
        .unwrap_or_else(|| {
            authoritative
                .iter()
                .filter(|b| **b == b'\n')
                .count()
                .min(shipped.iter().filter(|b| **b == b'\n').count())
        })
        + 1;
    Err(format!(
        "{USERENV_SHIPPED}:{line}: drifted from the authoritative {USERENV_AUTHORITATIVE} ({} vs {} bytes; 59-tools.sh installs the authoritative copy) -- resync: cp {USERENV_AUTHORITATIVE} {USERENV_SHIPPED}",
        shipped.len(),
        authoritative.len()
    ))
}

pub struct GlobalsPortsCheck;
impl Check for GlobalsPortsCheck {
    fn id(&self) -> &'static str {
        "check_globals_ports"
    }
    fn describe(&self) -> &'static str {
        "Assert PowerShell globals.ps1 ports match mios.toml [ports] SSOT"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        super::audit::native(ctx, "mios-gen", &["render-globals", "--check"])
    }
}

pub struct GlobalsImageParityCheck;
impl Check for GlobalsImageParityCheck {
    fn id(&self) -> &'static str {
        "check_globals_image_parity"
    }
    fn describe(&self) -> &'static str {
        "Assert PowerShell globals.ps1 image references match SSOT"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        super::audit::native(ctx, "mios-gen", &["render-globals", "--check"])
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn userenv_twins_must_both_exist_and_match_byte_for_byte(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let temp = tempfile::tempdir()?;
        let root = temp.path();
        let ctx = DriftCtx::new(root.into(), false);
        let missing = userenv_parity(&ctx).err().unwrap_or_default();
        assert!(
            missing.contains("twin missing")
                && missing.contains(USERENV_AUTHORITATIVE)
                && missing.contains(USERENV_SHIPPED),
            "{missing}"
        );
        fs::create_dir_all(root.join("tools/lib"))?;
        fs::create_dir_all(root.join("usr/lib/mios"))?;
        let body = "#!/bin/bash\n: \"${MIOS_PORTS_HERMES:=8720}\"\nexport MIOS_PORTS_HERMES\n";
        fs::write(root.join(USERENV_AUTHORITATIVE), body)?;
        let one = userenv_parity(&ctx).err().unwrap_or_default();
        assert!(
            one.contains(USERENV_SHIPPED) && !one.contains(&format!("{USERENV_AUTHORITATIVE}:")),
            "{one}"
        );
        fs::write(root.join(USERENV_SHIPPED), body)?;
        assert!(userenv_parity(&ctx)?.contains("matches authoritative"));
        fs::write(root.join(USERENV_SHIPPED), body.replace("8720", "8642"))?;
        assert!(userenv_parity(&ctx).is_err_and(|e| e
            .contains("usr/lib/mios/userenv.sh:2: drifted")
            && e.contains("resync: cp")));
        fs::write(root.join(USERENV_SHIPPED), body.replace('\n', "\r\n"))?;
        assert!(userenv_parity(&ctx).is_err_and(|e| e.contains("userenv.sh:1: drifted")));
        fs::write(root.join(USERENV_SHIPPED), format!("{body}echo extra\n"))?;
        assert!(userenv_parity(&ctx).is_err_and(|e| e.contains("userenv.sh:4: drifted")));
        Ok(())
    }
}
