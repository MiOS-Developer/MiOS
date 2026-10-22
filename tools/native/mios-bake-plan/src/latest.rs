// AI-hint: Floats bake inputs on their newest upstream release: `latest-image <ref>` and `latest-git <url>` print the concrete ref to fetch, using the release shapes in mios.toml [build.float]; `latest-portal` prints the MiOS-Portal Firefox release, its build and source revision, and the npm `latest` Surfer as key=value lines.
//! Registries that publish no `latest` tag, and git projects whose releases are
//! tags, still need one concrete ref to fetch. "The biggest tag" is wrong on real
//! data: lowercase alpha series sort above uppercase release series, and every
//! registry mixes arch- and variant-suffixed tags in with its releases. So a tag
//! counts only if it matches a release SHAPE.
//!
//! Upstream is asked first. A forge's `releases/latest` (GitHub, Forgejo, Gitea)
//! is the project's own answer, with pre-releases and drafts already excluded,
//! and an image's `org.opencontainers.image.source` label leads back to that
//! forge. The release shapes in [build.float] are the fallback for upstreams
//! that publish neither. Every resolution says on stderr which one decided.

use regex::Regex;
use std::path::Path;
use std::process::Command;

/// [build.float] resolved through the same vendor < host < user layers as
/// every other SSOT read.
pub struct Policy {
    pub image_shapes: Vec<String>,
    pub git_shapes: Vec<String>,
}

impl Policy {
    pub fn from_value(ssot: &toml::Value) -> Result<Policy, String> {
        let float = ssot
            .get("build")
            .and_then(|b| b.get("float"))
            .ok_or("mios.toml has no [build.float] -- nothing says which tags are releases")?;
        let list = |key: &str| -> Result<Vec<String>, String> {
            let shapes: Vec<String> = float
                .get(key)
                .and_then(|v| v.as_array())
                .ok_or(format!("[build.float].{key} is missing or not a list"))?
                .iter()
                .filter_map(|s| s.as_str().map(str::to_string))
                .collect();
            if shapes.is_empty() {
                return Err(format!("[build.float].{key} is empty"));
            }
            for s in &shapes {
                Regex::new(s)
                    .map_err(|e| format!("[build.float].{key}: {s:?} is not a regex: {e}"))?;
            }
            Ok(shapes)
        };
        Ok(Policy {
            image_shapes: list("image_shapes")?,
            git_shapes: list("git_shapes")?,
        })
    }

    pub fn load(root: &Path) -> Result<Policy, String> {
        let merged = mios_resolver::layers::create_figment(Some(root))
            .extract::<toml::Value>()
            .map_err(|e| format!("mios.toml did not resolve: {e}"))?;
        Policy::from_value(&merged)
    }
}

fn numeric_key(tag: &str) -> Vec<u64> {
    let digits = Regex::new(r"\d+").expect("latest: \\d+ is a compile-time literal");
    digits
        .find_iter(tag)
        .filter_map(|m| m.as_str().parse().ok())
        .collect()
}

/// The newest tag of the first shape any tag matches, or None. Shapes were
/// validated as regexes when the policy loaded.
pub fn newest(tags: &[String], shapes: &[String]) -> Option<String> {
    for shape in shapes {
        let re = Regex::new(shape).ok()?;
        if let Some(best) = tags
            .iter()
            .filter(|t| re.is_match(t))
            .max_by(|a, b| numeric_key(a).cmp(&numeric_key(b)))
        {
            return Some(best.clone());
        }
    }
    None
}

/// (repository, tag) for an image reference. A `:` inside the registry host
/// (host:port/x) is not a tag separator; only one after the last `/` is.
pub fn split_ref(reference: &str) -> (String, String) {
    let slash = reference.rfind('/').map(|i| i + 1).unwrap_or(0);
    match reference[slash..].rfind(':') {
        Some(i) => (
            reference[..slash + i].to_string(),
            reference[slash + i + 1..].to_string(),
        ),
        None => (reference.to_string(), "latest".to_string()),
    }
}

/// The Tags array from `skopeo list-tags` JSON, without pulling in a JSON crate.
pub fn parse_skopeo_tags(json: &str) -> Vec<String> {
    let block = Regex::new(r#"(?s)"Tags"\s*:\s*\[(.*?)\]"#).expect("latest: literal regex");
    let item = Regex::new(r#""([^"]+)""#).expect("latest: literal regex");
    block
        .captures(json)
        .map(|c| {
            item.captures_iter(&c[1])
                .map(|m| m[1].to_string())
                .collect()
        })
        .unwrap_or_default()
}

/// Tag names from `git ls-remote --tags --refs` output.
pub fn parse_ls_remote(out: &str) -> Vec<String> {
    out.lines()
        .filter_map(|l| l.split('\t').nth(1))
        .filter_map(|r| r.strip_prefix("refs/tags/"))
        .map(str::to_string)
        .collect()
}

/// The default branch from `git ls-remote --symref <url> HEAD`.
pub fn parse_symref(out: &str) -> Option<String> {
    out.lines()
        .find_map(|l| l.strip_prefix("ref: refs/heads/"))
        .and_then(|r| r.split('\t').next())
        .map(str::to_string)
}

/// The forge API URL for a project's latest release, or None for a host with no
/// known releases API. Accepts clone URLs and web URLs, with or without `.git`.
pub fn release_api(url: &str) -> Option<String> {
    let rest = url.split("://").nth(1)?;
    let mut parts = rest.trim_end_matches('/').split('/');
    let host = parts.next()?;
    let owner = parts.next()?;
    let repo = parts.next()?.trim_end_matches(".git");
    if owner.is_empty() || repo.is_empty() {
        return None;
    }
    Some(if host == "github.com" {
        format!("https://api.github.com/repos/{owner}/{repo}/releases/latest")
    } else {
        // Forgejo and Gitea (codeberg.org, code.forgejo.org, ...) share this path.
        format!("https://{host}/api/v1/repos/{owner}/{repo}/releases/latest")
    })
}

/// `tag_name` from a releases/latest JSON body.
pub fn parse_tag_name(json: &str) -> Option<String> {
    Regex::new(r#""tag_name"\s*:\s*"([^"]+)""#)
        .expect("latest: literal regex")
        .captures(json)
        .map(|c| c[1].to_string())
}

/// The project's own latest release tag, or None when it has none or the forge
/// cannot be reached (the caller then falls back and says so).
fn forge_latest_release(url: &str) -> Option<String> {
    let api = release_api(url)?;
    let body = run(
        "curl",
        &["-fsSL", "--retry", "3", "--connect-timeout", "20", &api],
    )
    .ok()?;
    parse_tag_name(&body)
}

/// org.opencontainers.image.source from `skopeo inspect` JSON.
pub fn parse_source_label(json: &str) -> Option<String> {
    Regex::new(r#""org\.opencontainers\.image\.source"\s*:\s*"([^"]+)""#)
        .expect("latest: literal regex")
        .captures(json)
        .map(|c| c[1].to_string())
}

/// The registry tag for an upstream release, when the registry publishes that
/// exact version. No prefix guessing: a label pointing at the wrong project
/// (a base OS, say) then simply finds no match.
pub fn tag_for_release(tags: &[String], release: &str) -> Option<String> {
    let bare = release.trim_start_matches('v');
    [release.to_string(), bare.to_string(), format!("v{bare}")]
        .into_iter()
        .find(|c| tags.contains(c))
}

fn run(cmd: &str, args: &[&str]) -> Result<String, String> {
    let out = Command::new(cmd)
        .args(args)
        .output()
        .map_err(|e| format!("could not run {cmd}: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "{cmd} {} failed: {}",
            args.join(" "),
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

/// A `:latest` reference, made concrete when its registry publishes no `latest`.
/// Any other tag is returned untouched: only what asks to float is floated.
pub fn resolve_image(reference: &str, policy: &Policy) -> Result<String, String> {
    let (repo, tag) = split_ref(reference);
    if tag != "latest" {
        return Ok(reference.to_string());
    }
    let tags = parse_skopeo_tags(&run("skopeo", &["list-tags", &format!("docker://{repo}")])?);
    if tags.iter().any(|t| t == "latest") {
        return Ok(reference.to_string());
    }
    let shaped = newest(&tags, &policy.image_shapes);
    // Read provenance from the newest shaped tag, then ask that project for its release.
    let upstream = shaped.as_ref().and_then(|t| {
        let meta = run(
            "skopeo",
            &[
                "inspect",
                "--override-os",
                "linux",
                &format!("docker://{repo}:{t}"),
            ],
        )
        .ok()?;
        let release = forge_latest_release(&parse_source_label(&meta)?)?;
        tag_for_release(&tags, &release)
    });
    match (upstream, shaped) {
        (Some(t), _) => {
            eprintln!("mios-bake-plan: {repo}: upstream latest release -> {t}");
            Ok(format!("{repo}:{t}"))
        }
        (None, Some(t)) => {
            eprintln!("mios-bake-plan: {repo}: no traceable upstream release; [build.float] shape -> {t}");
            Ok(format!("{repo}:{t}"))
        }
        (None, None) => Err(format!("{repo} publishes no `latest`, no traceable release and no tag of a [build.float] shape")),
    }
}

/// The newest release tag of a git repository, or its default branch when it
/// has never cut a release.
pub fn resolve_git(url: &str, policy: &Policy) -> Result<String, String> {
    if let Some(t) = forge_latest_release(url) {
        eprintln!("mios-bake-plan: {url}: upstream latest release -> {t}");
        return Ok(t);
    }
    let tags = parse_ls_remote(&run("git", &["ls-remote", "--tags", "--refs", url])?);
    if let Some(t) = newest(&tags, &policy.git_shapes) {
        eprintln!("mios-bake-plan: {url}: no forge release; [build.float] shape -> {t}");
        return Ok(t);
    }
    eprintln!("mios-bake-plan: {url}: no release at all; default branch");
    parse_symref(&run("git", &["ls-remote", "--symref", url, "HEAD"])?)
        .ok_or_else(|| format!("{url} has no release tag and no default branch"))
}

/// The MiOS-Portal upstreams, read from [desktop.portal.builder]: Mozilla's product-details and
/// release archive, and the npm registry that publishes Surfer. URLs only -- no version.
pub struct PortalSources {
    pub product_details: String,
    pub archive: String,
    pub npm_registry: String,
    pub surfer_package: String,
}

impl PortalSources {
    pub fn from_value(ssot: &toml::Value) -> Result<PortalSources, String> {
        let t = ssot
            .get("desktop")
            .and_then(|d| d.get("portal"))
            .and_then(|p| p.get("builder"))
            .ok_or("mios.toml has no [desktop.portal.builder] -- nothing says where MiOS-Portal's upstreams are")?;
        let key = |k: &str| -> Result<String, String> {
            t.get(k)
                .and_then(|v| v.as_str())
                .map(|s| s.trim_end_matches('/').to_string())
                .filter(|s| !s.is_empty())
                .ok_or(format!("[desktop.portal.builder].{k} is missing or empty"))
        };
        Ok(PortalSources {
            product_details: key("product_details_url")?,
            archive: key("archive_url")?,
            npm_registry: key("npm_registry_url")?,
            surfer_package: key("surfer_package")?,
        })
    }
}

/// A top-level JSON string field. Upstream bodies are flat where these are read.
pub fn parse_json_string(json: &str, key: &str) -> Option<String> {
    Regex::new(&format!(r#""{}"\s*:\s*"([^"]+)""#, regex::escape(key)))
        .ok()?
        .captures(json)
        .map(|c| c[1].to_string())
}

/// A Firefox RELEASE version: 158.0 or 158.0.1. Betas (159.0b3), nightlies (160.0a1) and ESR
/// (153.5.0esr) are other products and never win.
pub fn is_firefox_release(v: &str) -> bool {
    Regex::new(r"^\d+\.\d+(\.\d+)?$")
        .expect("latest: literal regex")
        .is_match(v)
}

/// releases["firefox-<version>"].build_number from product-details firefox.json: which candidate
/// build Mozilla shipped as that release.
pub fn parse_release_build(json: &str, version: &str) -> Option<u32> {
    Regex::new(&format!(
        r#""firefox-{}"\s*:\s*\{{[^}}]*?"build_number"\s*:\s*(\d+)"#,
        regex::escape(version)
    ))
    .ok()?
    .captures(json)?[1]
        .parse()
        .ok()
}

/// source.revision (a full 40-hex changeset) from a candidate build's buildhub.json.
pub fn parse_buildhub_revision(json: &str) -> Option<String> {
    Regex::new(r#""source"\s*:\s*\{[^}]*?"revision"\s*:\s*"([0-9a-f]{40})""#)
        .expect("latest: literal regex")
        .captures(json)
        .map(|c| c[1].to_string())
}

/// The latest Firefox release, the candidate build it shipped from, that build's source revision
/// (MOZ_ARTIFACT_REVISION) and the npm `latest` Surfer, as key=value pairs. Every lookup must
/// succeed: a failure is an error, never a remembered or default version.
pub fn resolve_portal(
    src: &PortalSources,
    fetch: &dyn Fn(&str) -> Result<String, String>,
) -> Result<Vec<(String, String)>, String> {
    let versions_url = format!("{}/firefox_versions.json", src.product_details);
    let version = parse_json_string(&fetch(&versions_url)?, "LATEST_FIREFOX_VERSION")
        .ok_or(format!("{versions_url} has no LATEST_FIREFOX_VERSION"))?;
    if !is_firefox_release(&version) {
        return Err(format!("{versions_url}: LATEST_FIREFOX_VERSION {version:?} is not a release version"));
    }
    let releases_url = format!("{}/firefox.json", src.product_details);
    let build = parse_release_build(&fetch(&releases_url)?, &version)
        .ok_or(format!("{releases_url} lists no build_number for firefox-{version}"))?;
    let buildhub_url = format!(
        "{}/candidates/{version}-candidates/build{build}/linux-x86_64/en-US/buildhub.json",
        src.archive
    );
    let revision = parse_buildhub_revision(&fetch(&buildhub_url)?)
        .ok_or(format!("{buildhub_url} has no 40-hex source.revision"))?;
    let npm_url = format!("{}/{}/latest", src.npm_registry, src.surfer_package);
    let surfer = parse_json_string(&fetch(&npm_url)?, "version")
        .filter(|v| Regex::new(r"^\d+\.\d+\.\d+$").expect("latest: literal regex").is_match(v))
        .ok_or(format!("{npm_url} has no release `version`"))?;
    eprintln!("mios-bake-plan: firefox {version} build{build} rev {revision}; surfer {surfer}");
    Ok(vec![
        ("firefox_version".into(), version),
        ("firefox_build".into(), build.to_string()),
        ("firefox_revision".into(), revision),
        ("surfer_version".into(), surfer),
    ])
}

fn curl(url: &str) -> Result<String, String> {
    run(
        "curl",
        &["-fsSL", "--retry", "3", "--connect-timeout", "20", url],
    )
}

/// `latest-image <ref>` / `latest-git <url>` / `latest-portal`. None when no such subcommand was asked for.
pub fn dispatch(args: &[String], root: &Path) -> Option<i32> {
    let finish = |r: Result<String, String>| match r {
        Ok(v) => {
            println!("{v}");
            0
        }
        Err(e) => {
            eprintln!("mios-bake-plan: {e}");
            1
        }
    };
    let (sub, arg) = (args.get(1).map(String::as_str), args.get(2));
    if sub == Some("latest-portal") {
        let resolved = mios_resolver::layers::create_figment(Some(root))
            .extract::<toml::Value>()
            .map_err(|e| format!("mios.toml did not resolve: {e}"))
            .and_then(|v| PortalSources::from_value(&v))
            .and_then(|src| resolve_portal(&src, &curl));
        return Some(match resolved {
            Ok(pairs) => {
                for (k, v) in pairs {
                    println!("{k}={v}");
                }
                0
            }
            Err(e) => {
                eprintln!("mios-bake-plan: {e}");
                1
            }
        });
    }
    if !matches!(sub, Some("latest-image" | "latest-git")) {
        return None;
    }
    let Some(arg) = arg else {
        eprintln!("usage: mios-bake-plan latest-image <ref> | latest-git <url>");
        return Some(2);
    };
    let policy = match Policy::load(root) {
        Ok(p) => p,
        Err(e) => return Some(finish(Err(e))),
    };
    Some(finish(if sub == Some("latest-image") {
        resolve_image(arg, &policy)
    } else {
        resolve_git(arg, &policy)
    }))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::PathBuf;

    /// The policy the image actually ships: these tests exercise the SSOT's
    /// rules, not a copy of them.
    fn shipped() -> Policy {
        let root = std::env::var_os("MIOS_TEST_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../.."));
        let ssot = root.join("usr/share/mios/mios.toml");
        let text = std::fs::read_to_string(&ssot).expect("the repo's mios.toml is readable");
        Policy::from_value(&text.parse().expect("mios.toml parses"))
            .expect("[build.float] is valid")
    }

    fn v(s: &[&str]) -> Vec<String> {
        s.iter().map(|x| x.to_string()).collect()
    }

    // Tag lists below are inputs recorded from the live upstreams, not policy.

    #[test]
    fn an_alpha_series_and_release_candidates_never_win() {
        let tags = v(&[
            "B1", "B1-rc1", "B2", "B3", "B4", "B5", "B5-rc1", "B5.0.1", "B6", "B6-rc1", "B7",
            "B7-rc1", "a1", "a10", "a11", "a12", "a9", "show",
        ]);
        assert_eq!(newest(&tags, &shipped().git_shapes).as_deref(), Some("B7"));
    }

    #[test]
    fn a_semver_series_picks_its_highest_release() {
        let tags = v(&["v0.1.0", "v0.2.0", "v0.2.1", "v0.3.0", "v0.3.1"]);
        assert_eq!(
            newest(&tags, &shipped().git_shapes).as_deref(),
            Some("v0.3.1")
        );
    }

    #[test]
    fn a_repo_without_releases_has_no_release_tag() {
        assert_eq!(newest(&[], &shipped().git_shapes), None);
    }

    #[test]
    fn numeric_order_not_string_order() {
        // As strings "9" sorts above "16".
        let tags = v(&[
            "9",
            "12",
            "16",
            "16.0",
            "16.0.1-rootless",
            "1.21.11-2-amd64-rootless",
        ]);
        assert_eq!(
            newest(&tags, &shipped().image_shapes).as_deref(),
            Some("16")
        );
    }

    #[test]
    fn each_registry_keeps_its_own_shape() {
        let p = shipped();
        let vmajor = v(&["v17", "v19", "v21", "v21.2.0", "v16.2.15-20240529"]);
        assert_eq!(newest(&vmajor, &p.image_shapes).as_deref(), Some("v21"));
        let pg = v(&[
            "pg16",
            "pg17",
            "pg18",
            "0.8.6-pg18-bookworm",
            "pg18-bookworm",
        ]);
        assert_eq!(newest(&pg, &p.image_shapes).as_deref(), Some("pg18"));
        let minor = v(&["5.3-amd64", "5.8", "6.0", "6.2", "6.2-arm64"]);
        assert_eq!(newest(&minor, &p.image_shapes).as_deref(), Some("6.2"));
    }

    #[test]
    fn a_missing_or_broken_policy_is_an_error_not_a_default() {
        let none: toml::Value = "[build]\n".parse().unwrap();
        assert!(Policy::from_value(&none).is_err());
        let empty: toml::Value = "[build.float]\nimage_shapes = []\ngit_shapes = ['^v']\n"
            .parse()
            .unwrap();
        assert!(Policy::from_value(&empty).is_err());
        let bad: toml::Value = "[build.float]\nimage_shapes = ['(']\ngit_shapes = ['^v']\n"
            .parse()
            .unwrap();
        assert!(Policy::from_value(&bad).is_err());
    }

    #[test]
    fn split_ref_ignores_a_port_in_the_host() {
        assert_eq!(
            split_ref("registry.example/org/app:latest"),
            ("registry.example/org/app".into(), "latest".into())
        );
        assert_eq!(
            split_ref("host.example:5000/org/app"),
            ("host.example:5000/org/app".into(), "latest".into())
        );
        assert_eq!(
            split_ref("registry.example/org/app:v2"),
            ("registry.example/org/app".into(), "v2".into())
        );
    }

    #[test]
    fn parsers_read_real_tool_output() {
        let skopeo = "{\n  \"Repository\": \"registry.example/org/app\",\n  \"Tags\": [\n    \"v1\",\n    \"v2\"\n  ]\n}";
        assert_eq!(parse_skopeo_tags(skopeo), v(&["v1", "v2"]));
        let ls = "abc\trefs/tags/v1.0.0\ndef\trefs/tags/v1.0.1\n";
        assert_eq!(parse_ls_remote(ls), v(&["v1.0.0", "v1.0.1"]));
        let sym = "ref: refs/heads/main\tHEAD\n0123\tHEAD\n";
        assert_eq!(parse_symref(sym).as_deref(), Some("main"));
    }

    #[test]
    fn release_api_covers_github_and_forgejo_style_hosts() {
        assert_eq!(
            release_api("https://github.com/owner/proj.git").as_deref(),
            Some("https://api.github.com/repos/owner/proj/releases/latest")
        );
        assert_eq!(
            release_api("https://forge.example/owner/proj").as_deref(),
            Some("https://forge.example/api/v1/repos/owner/proj/releases/latest")
        );
        assert_eq!(release_api("https://forge.example/owner"), None);
        assert_eq!(release_api("not a url"), None);
    }

    #[test]
    fn upstream_release_maps_only_to_an_exact_registry_tag() {
        let tags = v(&["16", "16.0", "16.0.5", "15.0.3"]);
        assert_eq!(tag_for_release(&tags, "v16.0.5").as_deref(), Some("16.0.5"));
        assert_eq!(tag_for_release(&tags, "16.0.5").as_deref(), Some("16.0.5"));
        // A label naming some other project yields a version this registry lacks.
        assert_eq!(tag_for_release(&tags, "10"), None);
    }

    #[test]
    fn json_fields_are_read_from_real_shaped_bodies() {
        let rel = "{\"url\":\"x\",\"tag_name\": \"v0.3.1\",\"prerelease\":false}";
        assert_eq!(parse_tag_name(rel).as_deref(), Some("v0.3.1"));
        let meta =
            "{\"Labels\": {\"org.opencontainers.image.source\": \"https://forge.example/o/p\"}}";
        assert_eq!(
            parse_source_label(meta).as_deref(),
            Some("https://forge.example/o/p")
        );
        assert_eq!(parse_tag_name("{}"), None);
    }

    /// Bodies shaped like the live upstreams on 2026-10-16 (recorded inputs, not policy).
    fn portal_fetch(url: &str) -> Result<String, String> {
        if url.ends_with("/firefox_versions.json") {
            Ok("{\"FIREFOX_DEVEDITION\": \"159.0b3\",\n \"FIREFOX_ESR\": \"153.5.0esr\",\n \"LATEST_FIREFOX_VERSION\": \"158.0\"}".into())
        } else if url.ends_with("/firefox.json") {
            Ok("{\"releases\": {\"firefox-158.0\": {\n \"build_number\": 2,\n \"version\": \"158.0\"},\n \"firefox-158.0b1\": {\"build_number\": 1}}}".into())
        } else if url.ends_with("/158.0-candidates/build2/linux-x86_64/en-US/buildhub.json") {
            Ok("{\"build\": {\"date\": \"x\"}, \"source\": {\"product\": \"firefox\", \"repository\": \"https://hg.example/r\", \"revision\": \"eb9a6e3a736802c8e080a76c44dc81f409d99d54\", \"tree\": \"mozilla-release\"}}".into())
        } else if url.ends_with("/@zen-browser/surfer/latest") {
            Ok("{\"name\":\"@zen-browser/surfer\",\"version\":\"1.14.9\",\"dist\":{}}".into())
        } else {
            Err(format!("unexpected fetch {url}"))
        }
    }

    fn portal_sources() -> PortalSources {
        let root = std::env::var_os("MIOS_TEST_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../.."));
        let text = std::fs::read_to_string(root.join("usr/share/mios/mios.toml"))
            .expect("the repo's mios.toml is readable");
        PortalSources::from_value(&text.parse().expect("mios.toml parses"))
            .expect("[desktop.portal.builder] names the upstreams")
    }

    #[test]
    fn positive_control_portal_resolves_every_upstream_from_the_ssot_urls() {
        let got = resolve_portal(&portal_sources(), &portal_fetch).unwrap();
        let get = |k: &str| got.iter().find(|(n, _)| n == k).map(|(_, v)| v.as_str());
        assert_eq!(get("firefox_version"), Some("158.0"));
        assert_eq!(get("firefox_build"), Some("2"));
        assert_eq!(get("firefox_revision"), Some("eb9a6e3a736802c8e080a76c44dc81f409d99d54"));
        assert_eq!(get("surfer_version"), Some("1.14.9"));
    }

    #[test]
    fn negative_control_a_failed_lookup_is_an_error_not_a_fallback() {
        let src = portal_sources();
        let down = |_: &str| -> Result<String, String> { Err("curl: (6) could not resolve".into()) };
        assert!(resolve_portal(&src, &down).is_err());
        let no_key = |u: &str| -> Result<String, String> {
            if u.ends_with("/firefox_versions.json") { Ok("{}".into()) } else { portal_fetch(u) }
        };
        assert!(resolve_portal(&src, &no_key).unwrap_err().contains("LATEST_FIREFOX_VERSION"));
        let beta = |u: &str| -> Result<String, String> {
            if u.ends_with("/firefox_versions.json") {
                Ok("{\"LATEST_FIREFOX_VERSION\": \"159.0b3\"}".into())
            } else {
                portal_fetch(u)
            }
        };
        assert!(resolve_portal(&src, &beta).unwrap_err().contains("not a release"));
        let no_rev = |u: &str| -> Result<String, String> {
            if u.ends_with("buildhub.json") { Ok("{\"source\": {\"revision\": \"abc\"}}".into()) } else { portal_fetch(u) }
        };
        assert!(resolve_portal(&src, &no_rev).unwrap_err().contains("source.revision"));
        let mut broken: toml::Value = "[desktop.portal.builder]\nproduct_details_url = \"\"\n".parse().unwrap();
        assert!(PortalSources::from_value(&broken).is_err());
        broken = "x = 1".parse().unwrap();
        assert!(PortalSources::from_value(&broken).is_err());
    }

    #[test]
    fn a_non_latest_tag_is_never_touched() {
        // No subprocess runs: an explicit tag short-circuits before skopeo.
        assert_eq!(
            resolve_image("registry.example/org/app:v2", &shipped()).unwrap(),
            "registry.example/org/app:v2"
        );
    }
}
