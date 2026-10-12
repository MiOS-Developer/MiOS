// AI-hint: Projectors for build artifacts: BIB filesystem configs under config/artifacts/ and the tools/native workspace Cargo.toml (ADR-0021, Law 14).
// AI-doc: usr/share/doc/mios/manual/tools.md
// AI-related: usr/share/mios/mios.toml, config/artifacts/bib.toml, config/artifacts/iso.toml, tools/native/Cargo.toml, tools/sync-generated.sh

pub mod bib_configs {
    use regex::Regex;
    use std::fs;
    use std::path::{Path, PathBuf};

    const SSOT_REL: &str = "usr/share/mios/mios.toml";
    const BIB_REL: &str = "config/artifacts/bib.toml";
    const ISO_REL: &str = "config/artifacts/iso.toml";

    const DEFAULT_RAW_SIZE: &str = "80 GiB";
    const DEFAULT_ISO_SIZE: &str = "150 GiB";

    /// Result of checking or rendering BIB artifact configs.
    #[derive(Debug, Clone, PartialEq, Eq)]
    pub struct BibConfigsResult {
        pub raw_size: String,
        pub iso_size: String,
        pub stale_files: Vec<String>,
        pub clean: bool,
    }

    /// Extract sizes from mios.toml SSOT.
    pub fn extract_ssot_sizes(root: &Path) -> Result<(String, String), String> {
        let ssot_path = root.join(SSOT_REL);
        if !ssot_path.is_file() {
            return Err(format!("ERROR: {} not found", ssot_path.display()));
        }

        let content = fs::read_to_string(&ssot_path)
            .map_err(|e| format!("Failed to read {}: {}", ssot_path.display(), e))?;

        let doc: toml::Value = content
            .parse::<toml::Value>()
            .map_err(|e| format!("Failed to parse {}: {}", ssot_path.display(), e))?;

        let deploy = doc.get("deploy").and_then(|d| d.get("artifacts"));
        let raw_size = deploy
            .and_then(|d| d.get("raw"))
            .and_then(|r| r.get("size"))
            .and_then(|s| s.as_str())
            .unwrap_or(DEFAULT_RAW_SIZE)
            .to_string();

        let iso_size = deploy
            .and_then(|d| d.get("iso"))
            .and_then(|i| i.get("minsize"))
            .and_then(|s| s.as_str())
            .unwrap_or(DEFAULT_ISO_SIZE)
            .to_string();

        Ok((raw_size, iso_size))
    }

    /// Render the updated content for a single artifact config file.
    pub fn render_artifact_config(content: &str, size: &str) -> String {
        let re = Regex::new(r#"minsize\s*=\s*"[^"]+""#).expect("valid regex");
        let replacement = format!(r#"minsize = "{}""#, size);
        re.replace(content, replacement.as_str()).to_string()
    }

    /// Normalize newlines to LF for clean cross-platform comparison.
    fn normalize_newlines(s: &str) -> String {
        s.replace("\r\n", "\n")
    }

    /// Run check or write for BIB artifact configs.
    pub fn run_bib_configs(root: &Path, check: bool) -> Result<BibConfigsResult, String> {
        let (raw_size, iso_size) = extract_ssot_sizes(root)?;

        let bib_path = root.join(BIB_REL);
        let iso_path = root.join(ISO_REL);

        let targets = [
            (BIB_REL, bib_path, &raw_size),
            (ISO_REL, iso_path, &iso_size),
        ];

        let mut stale_files = Vec::new();
        let mut rendered_contents: Vec<(PathBuf, String)> = Vec::new();

        for (rel_path, abs_path, target_size) in &targets {
            if !abs_path.is_file() {
                stale_files.push(format!("{} (missing)", rel_path));
                continue;
            }

            let raw_content = fs::read_to_string(abs_path)
                .map_err(|e| format!("Failed to read {}: {}", abs_path.display(), e))?;

            let rendered = render_artifact_config(&raw_content, target_size);
            let rendered_normalized = normalize_newlines(&rendered);

            if raw_content != rendered_normalized {
                stale_files.push(rel_path.to_string());
            }

            rendered_contents.push((abs_path.clone(), rendered_normalized));
        }

        let is_clean = stale_files.is_empty();

        if check {
            if !is_clean {
                return Err(format!(
                "ERROR: BIB artifact configs out of sync with mios.toml [deploy.artifacts] (raw={}, iso={}): {}",
                raw_size,
                iso_size,
                stale_files.join(", ")
            ));
            }
        } else {
            // Write mode
            for (abs_path, content) in rendered_contents {
                if let Some(parent) = abs_path.parent() {
                    let _ = fs::create_dir_all(parent);
                }
                fs::write(&abs_path, content.as_bytes())
                    .map_err(|e| format!("Failed to write {}: {}", abs_path.display(), e))?;
            }
        }

        Ok(BibConfigsResult {
            raw_size,
            iso_size,
            stale_files,
            clean: is_clean,
        })
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_render_artifact_config_basic() {
            let input = "mountpoint = \"/\"\nminsize = \"80 GiB\"\n";
            let output = render_artifact_config(input, "120 GiB");
            assert_eq!(output, "mountpoint = \"/\"\nminsize = \"120 GiB\"\n");
        }

        #[test]
        fn test_render_artifact_config_spacing_normalized() {
            let input = "mountpoint = \"/\"\n  minsize   =   \"old-size\"  \n";
            let output = render_artifact_config(input, "100 GiB");
            assert_eq!(output, "mountpoint = \"/\"\n  minsize = \"100 GiB\"  \n");
        }

        #[test]
        fn test_render_artifact_config_no_match() {
            let input = "mountpoint = \"/\"\n";
            let output = render_artifact_config(input, "100 GiB");
            assert_eq!(output, input);
        }
    }
}

pub mod cargo_manifests {
    use std::fs;
    use std::path::Path;

    const SSOT_REL: &str = "usr/share/mios/mios.toml";
    const VERSION_REL: &str = "VERSION";
    const NATIVE_REL: &str = "tools/native";
    const CARGO_TOML_REL: &str = "tools/native/Cargo.toml";

    const DEFAULT_VERSION: &str = "0.3.0";

    /// Result of checking or projecting cargo manifests.
    #[derive(Debug, Clone, PartialEq, Eq)]
    pub struct CargoManifestResult {
        pub members_count: usize,
        pub version: String,
        pub clean: bool,
    }

    /// Extract SSOT version from mios.toml or VERSION file.
    pub fn get_ssot_version(root: &Path) -> String {
        let ssot_path = root.join(SSOT_REL);
        if ssot_path.is_file() {
            if let Ok(content) = fs::read_to_string(&ssot_path) {
                if let Ok(doc) = content.parse::<toml::Value>() {
                    if let Some(v) = doc
                        .get("meta")
                        .and_then(|m| m.get("mios_version"))
                        .and_then(|s| s.as_str())
                    {
                        return v.to_string();
                    }
                }
            }
        }

        let version_path = root.join(VERSION_REL);
        if version_path.is_file() {
            if let Ok(content) = fs::read_to_string(&version_path) {
                let trimmed = content.trim();
                if !trimmed.is_empty() {
                    return trimmed.to_string();
                }
            }
        }

        DEFAULT_VERSION.to_string()
    }

    /// Enumerate all crate member directories under tools/native holding a Cargo.toml.
    pub fn enumerate_members(native_dir: &Path) -> Result<Vec<String>, String> {
        if !native_dir.is_dir() {
            return Ok(Vec::new());
        }

        let entries = fs::read_dir(native_dir)
            .map_err(|e| format!("Failed to read {}: {}", native_dir.display(), e))?;

        let mut members = Vec::new();
        for entry in entries.flatten() {
            let path = entry.path();
            if path.is_dir() && path.join("Cargo.toml").is_file() {
                if let Some(name) = path.file_name().and_then(|n| n.to_str()) {
                    members.push(name.to_string());
                }
            }
        }

        members.sort();
        Ok(members)
    }

    /// Render the complete tools/native/Cargo.toml content.
    pub fn render_cargo_manifest(members: &[String], version: &str) -> String {
        let mut listed = String::new();
        for m in members {
            listed.push_str(&format!("    \"{}\",\n", m));
        }

        format!(
        "# AI-hint: Generated from mios.toml SSOT by mios-gen cargo-manifests. DO NOT EDIT DIRECTLY.\n\
        [workspace]\n\
        members = [\n\
        {}\
        ]\n\
        resolver = \"2\"\n\
        \n\
        [workspace.package]\n\
        version = \"{}\"\n\
        edition = \"2021\"\n\
        \n\
        [workspace.dependencies]\n\
        clap = {{ version = \"4.5\", features = [\"derive\"] }}\n\
        figment = {{ version = \"0.10\", features = [\"toml\", \"env\"] }}\n\
        flate2 = \"1.0\"\n\
        miette = {{ version = \"5.10\", features = [\"fancy\"] }}\n\
        regex = \"1.10\"\n\
        serde = {{ version = \"1.0\", features = [\"derive\"] }}\n\
        serde_json = \"1.0\"\n\
        sha2 = \"0.10\"\n\
        tempfile = \"3.10\"\n\
        thiserror = \"1.0\"\n\
        toml = \"0.8\"\n\
        toml_edit = \"0.20\"\n\
        walkdir = \"2.4\"\n\
        \n\
        # Size-optimized release for the Windows wallpaper daemon (its profile\n\
        # previously sat in the member manifest, where cargo silently ignored\n\
        # it). lto/strip/panic are workspace-level profile knobs cargo cannot\n\
        # set per package.\n\
        [profile.release.package.mios-wallpaperd]\n\
        opt-level = \"z\"\n\
        codegen-units = 1\n",
        listed, version
    )
    }

    /// Normalize newlines to LF for clean cross-platform comparison.
    fn normalize_newlines(s: &str) -> String {
        s.replace("\r\n", "\n")
    }

    /// Run check or projection for tools/native/Cargo.toml.
    pub fn run_cargo_manifests(root: &Path, check: bool) -> Result<CargoManifestResult, String> {
        let native_dir = root.join(NATIVE_REL);
        let cargo_toml_path = root.join(CARGO_TOML_REL);

        let members = enumerate_members(&native_dir)?;
        if members.is_empty() {
            return Err(format!(
            "[generate-cargo-manifests] FAIL: no crate directory under {}, so the projection would empty the workspace",
            native_dir.display()
        ));
        }

        let version = get_ssot_version(root);
        let projected_content = render_cargo_manifest(&members, &version);

        if check {
            if !cargo_toml_path.is_file() {
                return Err(format!(
                    "[generate-cargo-manifests] FAIL: cannot read {} (file not found)",
                    cargo_toml_path.display()
                ));
            }

            let committed_content = fs::read_to_string(&cargo_toml_path).map_err(|e| {
                format!(
                    "[generate-cargo-manifests] FAIL: cannot read {} ({})",
                    cargo_toml_path.display(),
                    e
                )
            })?;

            let to_write = normalize_newlines(&projected_content);
            if committed_content != to_write {
                return Err(
                "[generate-cargo-manifests] FAIL: tools/native/Cargo.toml differs from its projection".to_string()
            );
            }

            Ok(CargoManifestResult {
                members_count: members.len(),
                version,
                clean: true,
            })
        } else {
            // Write mode
            let to_write = normalize_newlines(&projected_content);
            fs::write(&cargo_toml_path, to_write.as_bytes())
                .map_err(|e| format!("Failed to write {}: {}", cargo_toml_path.display(), e))?;

            Ok(CargoManifestResult {
                members_count: members.len(),
                version,
                clean: true,
            })
        }
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        #[test]
        fn test_render_cargo_manifest_format() {
            let members = vec!["crate-a".to_string(), "crate-b".to_string()];
            let rendered = render_cargo_manifest(&members, "0.3.0");
            assert!(rendered.contains("    \"crate-a\",\n"));
            assert!(rendered.contains("    \"crate-b\",\n"));
            assert!(rendered.contains("version = \"0.3.0\"\n"));
            assert!(rendered.contains("resolver = \"2\"\n"));
        }

        #[test]
        fn test_normalize_newlines() {
            assert_eq!(normalize_newlines("a\r\nb\r\nc"), "a\nb\nc");
        }
    }
}
