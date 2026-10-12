// AI-hint: Bake plan and capacity budget checks for miosd drift runner.
// AI-related: tools/native/mios-bake-plan, usr/lib/mios/bake/plan.d/

use super::{Check, DriftCtx, Verdict};

pub struct BakePlanCheck;
impl Check for BakePlanCheck {
    fn id(&self) -> &'static str {
        "check_bake_plan"
    }
    fn describe(&self) -> &'static str {
        "Assert OCI bake plan layers and budgets conform to SSOT limits"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        super::audit::native(ctx, "mios-bake-plan", &["--check"])
    }
}

pub struct BakeBudgetCheck;
impl Check for BakeBudgetCheck {
    fn id(&self) -> &'static str {
        "check_bake_budget"
    }
    fn describe(&self) -> &'static str {
        "Assert OCI bake layer size budgets stay within runner constraints"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        super::audit::verdict(budget(ctx))
    }
}

pub fn budget(ctx: &DriftCtx) -> super::audit::Audit {
    let doc = super::audit::ssot(ctx)?;
    let budget = super::audit::at(&doc, "build.bake.runner_disk_budget_gb")?;
    let budget = budget
        .as_float()
        .or_else(|| budget.as_integer().map(|n| n as f64))
        .filter(|v| v.is_finite() && *v > 0.0)
        .ok_or("Invalid SSOT build.bake.runner_disk_budget_gb")?;
    let text = super::audit::read(&ctx.root, "usr/share/mios/artifacts/sbom/bound-images.tsv")?;
    let mut rows = text
        .lines()
        .filter(|line| !line.trim().is_empty() && !line.trim_start().starts_with('#'));
    let header: Vec<_> = rows
        .next()
        .ok_or("Empty bound-images.tsv")?
        .split('\t')
        .collect();
    let size_index = header
        .iter()
        .position(|key| *key == "size_gb")
        .ok_or("bound-images.tsv missing size_gb column")?;
    let group_index = header
        .iter()
        .position(|key| *key == "group")
        .ok_or("bound-images.tsv missing group column")?;
    let mut total = 0.0;
    let mut count = 0;
    for (line, row) in rows.enumerate() {
        let fields: Vec<_> = row.split('\t').collect();
        let size = fields
            .get(size_index)
            .ok_or_else(|| format!("bound-images.tsv:{}: missing size", line + 2))?
            .parse::<f64>()
            .map_err(|e| format!("bound-images.tsv:{}: invalid size: {e}", line + 2))?;
        if !size.is_finite() || size < 0.0 {
            return Err(format!(
                "bound-images.tsv:{}: size must be finite and nonnegative",
                line + 2
            ));
        }
        let group = fields
            .get(group_index)
            .ok_or_else(|| format!("bound-images.tsv:{}: missing group", line + 2))?;
        if *group != "firstboot" {
            total += size;
            count += 1;
        }
    }
    if count == 0 {
        return Err("Bake budget: no Day-0 subjects examined".into());
    }
    if total > budget {
        return Err(format!(
            "Day-0 size {total:.2} GB exceeds SSOT runner budget {budget:.2} GB ({count} images)"
        ));
    }
    Ok(format!(
        "Day-0 size {total:.2} GB <= SSOT runner budget {budget:.2} GB ({count} images examined)"
    ))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn budget_uses_day_zero_rows_and_rejects_overflow_nan_empty_and_missing(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let temp = tempfile::tempdir()?;
        std::fs::create_dir_all(temp.path().join("usr/share/mios/artifacts/sbom"))?;
        std::fs::write(
            temp.path().join("usr/share/mios/mios.toml"),
            "[build.bake]\nrunner_disk_budget_gb = 10\n",
        )?;
        let ctx = DriftCtx::new(temp.path().into(), false);
        let path = temp
            .path()
            .join("usr/share/mios/artifacts/sbom/bound-images.tsv");
        std::fs::write(
            &path,
            "image\tgroup\tsize_gb\nbase\tcore\t8\noptional\tfirstboot\t200\n",
        )?;
        assert!(budget(&ctx)?.contains("8.00 GB"));
        for fixture in [
            "image\tgroup\tsize_gb\nbase\tcore\t11\n",
            "image\tgroup\tsize_gb\nbase\tcore\tNaN\n",
            "image\tgroup\tsize_gb\n",
            "image\tgroup\tsize_gb\nbase\tcore\t-1\n",
            "image\tgroup\nbase\tcore\n",
        ] {
            std::fs::write(&path, fixture)?;
            assert!(
                budget(&ctx).is_err(),
                "invalid budget fixture passed: {fixture}"
            );
        }
        std::fs::remove_file(path)?;
        assert!(budget(&ctx).is_err());
        Ok(())
    }
}
