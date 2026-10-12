// AI-hint: Version SSOT drift checks using native mios-version-check.
// AI-related: tools/native/mios-version-check, usr/share/mios/mios.toml

use super::{Check, DriftCtx, Verdict};

pub struct VersionSSOTCheck;
impl Check for VersionSSOTCheck {
    fn id(&self) -> &'static str {
        "check_version_ssot"
    }
    fn describe(&self) -> &'static str {
        "Assert mios_version equality across all version-dupe files"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        let result = (|| {
            let policy = super::audit::ssot(ctx)?;
            let expected = super::audit::at(&policy, "meta.mios_version")?
                .as_str()
                .filter(|s| !s.is_empty())
                .ok_or("empty meta.mios_version")?;
            let version = super::audit::read(&ctx.root, "VERSION")?;
            if version.trim() != expected {
                return Err("VERSION differs from meta.mios_version".into());
            }
            let mut errors = Vec::new();
            let paths = super::audit::files(&ctx.root, "")?;
            let pattern = regex::Regex::new(r#"^ARG\s+MIOS_VERSION\s*=\s*[\"']?([^\"'\s]+)"#)
                .map_err(|e| e.to_string())?;
            let mut count = 1;
            for path in paths
                .iter()
                .filter(|p| !p.contains('/') && p.starts_with("Containerfile"))
            {
                for line in super::audit::read(&ctx.root, path)?.lines() {
                    if let Some(hit) = pattern.captures(line.trim()) {
                        count += 1;
                        if &hit[1] != expected {
                            errors.push(format!(
                                "{path}: ARG MIOS_VERSION differs from meta.mios_version"
                            ));
                        }
                    }
                }
            }
            for path in ["tools/native/Cargo.toml", "src/mios-rs/Cargo.toml"] {
                let text = super::audit::read(&ctx.root, path)?;
                let cargo: toml::Value = text.parse().map_err(|e| format!("{path}: {e}"))?;
                count += 1;
                if super::audit::at(&cargo, "workspace.package.version")?.as_str() != Some(expected)
                {
                    errors.push(format!(
                        "{path}: workspace version differs from meta.mios_version"
                    ));
                }
            }
            let path = "usr/lib/os-release";
            if ctx.root.join(path).is_file() {
                for (key, value) in super::audit::read(&ctx.root, path)?
                    .lines()
                    .filter_map(|s| s.split_once('='))
                {
                    if matches!(key, "VERSION_ID" | "BUILD_ID" | "IMAGE_VERSION") {
                        count += 1;
                        if value.trim_matches('"') != expected {
                            errors.push(format!("{path}: {key} differs from meta.mios_version"));
                        }
                    }
                }
            }
            super::audit::finish(count, errors, "version projections")
        })();
        if let Err(message) = result {
            return Verdict::Fail(message);
        }
        super::audit::native(ctx, "mios-gate", &["version-literals-ssot"])
    }
}

pub struct RootTomlSubsetCheck;
impl Check for RootTomlSubsetCheck {
    fn id(&self) -> &'static str {
        "check_root_toml_subset"
    }
    fn describe(&self) -> &'static str {
        "Assert root mios.toml is a valid subset of canonical SSOT"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        if !ctx.root.join("mios.toml").exists() {
            return Verdict::Skip(
                "optional root mios.toml absent; no subset comparison applicable".into(),
            );
        }
        super::audit::verdict((|| {
            fn keys(
                value: &toml::Value,
                prefix: &str,
                out: &mut std::collections::BTreeSet<String>,
            ) {
                if let Some(table) = value.as_table() {
                    for (key, child) in table {
                        let path = if prefix.is_empty() {
                            key.to_owned()
                        } else {
                            format!("{prefix}.{key}")
                        };
                        out.insert(path.clone());
                        keys(child, &path, out);
                    }
                }
            }
            let canonical = super::audit::ssot(ctx)?;
            let root: toml::Value = super::audit::read(&ctx.root, "mios.toml")?
                .parse()
                .map_err(|e| format!("root mios.toml: {e}"))?;
            let mut expected = std::collections::BTreeSet::new();
            let mut actual = std::collections::BTreeSet::new();
            keys(&canonical, "", &mut expected);
            keys(&root, "", &mut actual);
            let exceptions = [
                "autounattend",
                "bootstrap",
                "medicat",
                "containers",
                "ports.lan_firewall",
                "quadlets",
                "smoke_tests",
                "terminal.startup",
                "branding.windows",
                "branding.cursor",
                "branding.oem_",
                "branding.wallpaper",
                "branding.lockscreen",
                "branding.ui_font",
                "branding.font_substitute",
                "ai.enable_",
                "branding.living_wallpaper",
                "terminal.gui_min",
                "theme.terminal.dev_profile_name",
                "theme.terminal.hub_target_profile",
                "theme.terminal.summon_keys",
                "theme.terminal.summon_window_name",
                "mios_app",
            ];
            let errors = actual
                .difference(&expected)
                .filter(|key| !exceptions.iter().any(|prefix| key.starts_with(prefix)))
                .map(|key| format!("root mios.toml: {key} absent from canonical schema"))
                .collect();
            super::audit::finish(actual.len(), errors, "root TOML schema subset")
        })())
    }
}
