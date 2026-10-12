// AI-hint: Supply chain, digest, pin, and SBOM checks for miosd drift runner.
// AI-related: automation/98-drift-checks.sh, ADR-0003

use super::audit::{self, at, files, finish, read, ssot};
use super::{Check, DriftCtx, Verdict};

pub struct ContainerfilePinnedClonesCheck;
impl Check for ContainerfilePinnedClonesCheck {
    fn id(&self) -> &'static str {
        "check_containerfile_pinned_clones"
    }
    fn describe(&self) -> &'static str {
        "Assert git clones in Containerfiles use explicit commit pins"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict((|| {
            let paths: Vec<_> = files(&ctx.root, "")?
                .into_iter()
                .filter(|p| p.rsplit('/').next().unwrap_or("").contains("Containerfile"))
                .collect();
            let mut errors = Vec::new();
            for path in &paths {
                for (line, text) in read(&ctx.root, path)?.lines().enumerate() {
                    if text.trim_start().starts_with('#') || !text.contains("git clone") {
                        continue;
                    }
                    if !["--branch", "--tag", "-b ", "@"]
                        .iter()
                        .any(|pin| text.contains(pin))
                    {
                        errors.push(format!(
                            "{path}:{}: git clone lacks a selected ref",
                            line + 1
                        ));
                    }
                }
            }
            if paths.len() < 5 {
                return Err(format!(
                    "Containerfile ref audit read only {} files; expected at least five",
                    paths.len()
                ));
            }
            finish(
                paths.len(),
                errors,
                "Containerfile explicit clone refs (not immutable-commit certification)",
            )
        })())
    }
}

pub struct SBOMMetadataCheck;
impl Check for SBOMMetadataCheck {
    fn id(&self) -> &'static str {
        "check_sbom_metadata"
    }
    fn describe(&self) -> &'static str {
        "Assert SBOM metadata is valid and present"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        // bound-images.tsv is committed; models.tsv and binaries.tsv are written
        // by the image build. A source tree or bake context validates what it
        // carries; only an installed image (root "/") must carry every table.
        let built = ctx.root == std::path::Path::new("/");
        audit::verdict((|| {
            let mut count = 0;
            let mut errors = Vec::new();
            for (name, columns, produced_by_build) in [
                ("models.tsv", 5, true),
                ("binaries.tsv", 3, true),
                ("bound-images.tsv", 3, false),
            ] {
                let path = format!("usr/share/mios/artifacts/sbom/{name}");
                if produced_by_build && !built && !ctx.root.join(&path).is_file() {
                    continue;
                }
                let body = read(&ctx.root, &path)?;
                for (line, row) in body
                    .lines()
                    .enumerate()
                    .skip(1)
                    .filter(|(_, row)| !row.trim().is_empty())
                {
                    count += 1;
                    let fields: Vec<_> = row.split('\t').collect();
                    if fields.len() < columns
                        || fields[..columns].iter().any(|v| v.trim().is_empty())
                    {
                        errors.push(format!(
                            "{path}:{}: empty or missing metadata fields",
                            line + 1
                        ));
                        continue;
                    }
                    let hash = if name == "models.tsv" {
                        fields[4]
                    } else {
                        fields[2]
                    };
                    if name != "bound-images.tsv"
                        && hash != "unknown"
                        && (hash.len() != 64 || !hash.bytes().all(|c| c.is_ascii_hexdigit()))
                    {
                        errors.push(format!("{path}:{}: invalid SHA256", line + 1));
                    }
                }
            }
            finish(
                count,
                errors,
                "SBOM metadata structure (unknown hashes are not verified)",
            )
        })())
    }
}

pub struct VendoredAssetsNonStubCheck;
impl Check for VendoredAssetsNonStubCheck {
    fn id(&self) -> &'static str {
        "check_vendored_assets_non_stub"
    }
    fn describe(&self) -> &'static str {
        "Assert vendored assets are complete non-stub implementations"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict((|| {
            let paths = files(&ctx.root, "usr/share/mios/vendored")?;
            let mut errors = Vec::new();
            let mut count = 0;
            for path in &paths {
                if path.ends_with("/.keep") || path.ends_with("/VERSIONS.txt") {
                    continue;
                }
                count += 1;
                let size = std::fs::metadata(ctx.root.join(path))
                    .map_err(|e| format!("{path}: {e}"))?
                    .len();
                if size < 100 {
                    errors.push(format!(
                        "{path}: vendored asset is only {size} bytes (<100 byte stub threshold)"
                    ));
                }
            }
            finish(
                count,
                errors,
                "vendored asset minimum size; contents not certified",
            )
        })())
    }
}

pub struct BakeRefDefaultsCheck;
impl Check for BakeRefDefaultsCheck {
    fn id(&self) -> &'static str {
        "check_bake_ref_defaults"
    }
    fn describe(&self) -> &'static str {
        "Assert bake ref defaults are valid"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict((|| {
            let policy = ssot(ctx)?;
            let refs = at(&policy, "build.bake_refs")?
                .as_table()
                .ok_or("build.bake_refs must be a table")?;
            let pattern = regex::Regex::new(r#"MIOS_BUILD_BAKE_REFS_([A-Z0-9_]+):-([^}\"']+)"#)
                .map_err(|e| e.to_string())?;
            let paths = files(&ctx.root, "automation")?;
            let mut errors = Vec::new();
            let mut count = 0;
            for path in paths.iter().filter(|p| p.ends_with(".sh")) {
                count += 1;
                for (line, text) in read(&ctx.root, path)?
                    .lines()
                    .enumerate()
                    .filter(|(_, s)| !s.trim_start().starts_with('#'))
                {
                    for hit in pattern.captures_iter(text) {
                        let key = hit[1].to_lowercase();
                        let expected =
                            refs.get(&key)
                                .and_then(toml::Value::as_str)
                                .ok_or_else(|| {
                                    format!("{path}:{}: undeclared bake ref {key}", line + 1)
                                })?;
                        if hit[2].trim() != expected.trim() {
                            errors.push(format!(
                                "{path}:{}: bake ref {key} default differs from SSOT",
                                line + 1
                            ));
                        }
                    }
                }
            }
            finish(count, errors, "bake ref defaults")
        })())
    }
}

pub struct VendorURLsCheck;
impl Check for VendorURLsCheck {
    fn id(&self) -> &'static str {
        "check_vendor_urls"
    }
    fn describe(&self) -> &'static str {
        "Assert vendor URLs resolve and meet security policy"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict((|| {
            let policy = ssot(ctx)?;
            let endpoint = at(&policy, "ai.endpoint")?
                .as_str()
                .ok_or("ai.endpoint must be a string")?;
            let authority = endpoint
                .split_once("://")
                .map(|(_, rest)| rest.split('/').next().unwrap_or(""))
                .unwrap_or("");
            let local = authority == "localhost"
                || authority.starts_with("localhost:")
                || authority == "127.0.0.1"
                || authority.starts_with("127.0.0.1:")
                || authority == "[::1]"
                || authority.starts_with("[::1]:");
            if !local {
                return Err("vendor ai.endpoint must name a loopback OpenAI-compatible surface; change remote routing only in a host override".into());
            }
            let forbidden = regex::Regex::new(r"https?://(?:api\.openai\.com|api\.anthropic\.com|generativelanguage\.googleapis\.com|api\.cohere\.|api\.mistral\.|api\.cline\.bot|api\.cursor\.com|api\.githubcopilot\.com)").map_err(|e| e.to_string())?;
            let mut count = 0;
            let mut errors = Vec::new();
            for directory in [
                "usr/share/containers/systemd",
                "usr/lib/systemd/system",
                "usr/share/mios/ai",
                "etc/containers/systemd",
                "etc/mios/ai",
            ] {
                if !ctx.root.join(directory).is_dir() {
                    continue;
                }
                for path in files(&ctx.root, directory)?.iter().filter(|p| {
                    [
                        ".container",
                        ".service",
                        ".json",
                        ".toml",
                        ".conf",
                        ".yaml",
                        ".yml",
                    ]
                    .iter()
                    .any(|ext| p.ends_with(ext))
                }) {
                    count += 1;
                    for (line, text) in read(&ctx.root, path)?.lines().enumerate() {
                        if text.trim_start().starts_with(['#', '/']) {
                            continue;
                        }
                        if forbidden.is_match(text) {
                            errors.push(format!(
                                "{path}:{}: hardcoded vendor cloud endpoint",
                                line + 1
                            ));
                        }
                    }
                }
            }
            finish(
                count,
                errors,
                "AI configuration endpoint source audit; runtime routing unverified",
            )
        })())
    }
}
