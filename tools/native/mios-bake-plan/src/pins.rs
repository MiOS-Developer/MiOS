// AI-hint: `mios-bake-plan refresh-pins [--check]` refreshes the [build.pins] SSOT digests the image bake proves release assets with (automation/lib/upstream-latest.sh mios_pinned_asset): newest release or the release an SSOT ref names, each asset downloaded and sha256-hashed, cross-checked against GitHub's published digest when GH_TOKEN/GITHUB_TOKEN is set, the vendored copy rewritten. Network-bound and operator-run (`just refresh-pins`), never at build time (Law 12 BAKE-NOT-FETCH).
// AI-related: usr/share/mios/mios.toml, automation/lib/upstream-latest.sh, automation/57-gnome.sh, automation/59-tools.sh, Justfile, tools/native/mios-bake-plan/src/latest.rs
//! The bake proves a pinned release with no REST call, so the pins must be
//! fresh for "latest packages" to hold. This is the one place they move: the
//! SSOT text is edited line by line inside each `[build.pins.*]` table, so the
//! rest of mios.toml (comments, alignment, order) is left byte-identical.

use crate::latest::{resolve_git, Policy};
use regex::Regex;
use sha2::{Digest, Sha256};
use std::fs;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;
use toml::Value;

/// One release asset of a pin: `uname -m` key (or noarch), name template, vendored copy.
#[derive(Debug, PartialEq)]
pub struct Asset {
    pub arch: String,
    pub name: String,
    pub sha256: String,
    pub vendored: Option<String>,
}

/// One `[build.pins.<component>]` table.
#[derive(Debug, PartialEq)]
pub struct Pin {
    pub component: String,
    pub repo: String,
    pub tag_prefix: String,
    pub version: String,
    pub version_from: Option<String>,
    pub assets: Vec<Asset>,
}

fn dotted<'a>(v: &'a Value, path: &str) -> Option<&'a Value> {
    path.split('.').try_fold(v, |acc, k| acc.get(k))
}

fn s(v: &Value, key: &str) -> String {
    v.get(key)
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_string()
}

/// owner/name from "owner/name" or a github.com clone or web URL.
pub fn normalize_repo(raw: &str) -> Option<String> {
    let rest = match raw.split_once("://") {
        Some((_, r)) => r.strip_prefix("github.com/")?,
        None => raw,
    };
    let rest = rest.trim_end_matches('/').trim_end_matches(".git");
    let mut parts = rest.split('/');
    match (parts.next(), parts.next(), parts.next()) {
        (Some(o), Some(n), None) if !o.is_empty() && !n.is_empty() => Some(format!("{o}/{n}")),
        _ => None,
    }
}

/// The tag of an image reference, or None for an untagged one.
pub fn image_tag(reference: &str) -> Option<String> {
    let last = reference.rsplit('/').next()?;
    last.split_once(':').map(|(_, t)| t.to_string())
}

pub fn pins_from_value(ssot: &Value) -> Result<Vec<Pin>, String> {
    let table = dotted(ssot, "build.pins")
        .and_then(Value::as_table)
        .ok_or("mios.toml has no [build.pins]")?;
    let mut pins = Vec::new();
    for (component, t) in table {
        let raw = match t.get("repository").and_then(Value::as_str) {
            Some(r) => r.to_string(),
            None => {
                let from = s(t, "repository_from");
                dotted(ssot, &from)
                    .and_then(Value::as_str)
                    .ok_or(format!(
                        "[build.pins.{component}] has no repository and repository_from '{from}' names nothing"
                    ))?
                    .to_string()
            }
        };
        let repo = normalize_repo(&raw).ok_or(format!(
            "[build.pins.{component}]: '{raw}' is not a github.com repository"
        ))?;
        let assets = t
            .get("assets")
            .and_then(Value::as_table)
            .ok_or(format!("[build.pins.{component}] declares no assets"))?
            .iter()
            .map(|(arch, a)| Asset {
                arch: arch.clone(),
                name: s(a, "name"),
                sha256: s(a, "sha256"),
                vendored: a
                    .get("vendored")
                    .and_then(Value::as_str)
                    .map(str::to_string),
            })
            .collect::<Vec<_>>();
        if let Some(a) = assets.iter().find(|a| a.name.is_empty()) {
            return Err(format!(
                "[build.pins.{component}.assets.{}] has no name",
                a.arch
            ));
        }
        pins.push(Pin {
            component: component.clone(),
            repo,
            tag_prefix: s(t, "tag_prefix"),
            version: s(t, "version"),
            version_from: t
                .get("version_from")
                .and_then(Value::as_str)
                .map(str::to_string),
            assets,
        });
    }
    Ok(pins)
}

/// Set `key = "value"` inside `[header]`, keeping the line's alignment and
/// trailing comment; a missing key is added after the table's last key.
pub fn set_in_table(text: &str, header: &str, key: &str, value: &str) -> Result<String, String> {
    let lines: Vec<&str> = text.split_inclusive('\n').collect();
    let want = format!("[{header}]");
    let start = lines
        .iter()
        .position(|l| l.trim() == want)
        .ok_or(format!("mios.toml has no {want} table"))?;
    let end = lines[start + 1..]
        .iter()
        .position(|l| l.trim_start().starts_with('['))
        .map_or(lines.len(), |i| start + 1 + i);
    let re = Regex::new(&format!(
        r#"^(\s*{}\s*=\s*)"[^"]*"(.*)$"#,
        regex::escape(key)
    ))
    .map_err(|e| e.to_string())?;
    let mut out: Vec<String> = lines.iter().map(|l| l.to_string()).collect();
    let mut last_key = start;
    for (i, line) in lines.iter().enumerate().take(end).skip(start + 1) {
        let body = line.trim_end_matches('\n');
        if let Some(c) = re.captures(body) {
            let nl = if line.ends_with('\n') { "\n" } else { "" };
            out[i] = format!("{}\"{value}\"{}{nl}", &c[1], &c[2]);
            return Ok(out.concat());
        }
        if body.contains('=') && !body.trim_start().starts_with('#') {
            last_key = i;
        }
    }
    out.insert(last_key + 1, format!("{key} = \"{value}\"\n"));
    Ok(out.concat())
}

fn sha256_file(path: &Path) -> Result<String, String> {
    let bytes = fs::read(path).map_err(|e| format!("{}: {e}", path.display()))?;
    Ok(Sha256::digest(&bytes)
        .iter()
        .map(|b| format!("{b:02x}"))
        .collect())
}

fn curl_to(url: &str, dest: &Path) -> Result<(), String> {
    let st = Command::new("curl")
        .args(["-fsSL", "--retry", "3", "--connect-timeout", "20", "-o"])
        .arg(dest)
        .arg(url)
        .status()
        .map_err(|e| format!("could not run curl: {e}"))?;
    st.success()
        .then_some(())
        .ok_or(format!("download failed: {url}"))
}

/// GitHub's published sha256 for an asset, when a token is set to ask with.
/// The token reaches curl through a 0600 header file, never argv.
fn github_digest(repo: &str, tag: &str, name: &str, tmp: &Path) -> Option<String> {
    let token = std::env::var("GH_TOKEN")
        .or_else(|_| std::env::var("GITHUB_TOKEN"))
        .ok()
        .filter(|t| !t.trim().is_empty())?;
    let hdr = tmp.join("auth-header");
    let mut f = fs::OpenOptions::new();
    f.write(true).create(true).truncate(true);
    #[cfg(unix)]
    std::os::unix::fs::OpenOptionsExt::mode(&mut f, 0o600);
    writeln!(f.open(&hdr).ok()?, "Authorization: Bearer {}", token.trim()).ok()?;
    let out = Command::new("curl")
        .args(["-fsSL", "--retry", "3", "--connect-timeout", "20"])
        .args(["-H", "Accept: application/vnd.github+json"])
        .arg("-H")
        .arg(format!("@{}", hdr.display()))
        .arg(format!(
            "https://api.github.com/repos/{repo}/releases/tags/{tag}"
        ))
        .output()
        .ok();
    let _ = fs::remove_file(&hdr);
    let json: serde_json::Value =
        serde_json::from_slice(&out.filter(|o| o.status.success())?.stdout).ok()?;
    json.get("assets")?
        .as_array()?
        .iter()
        .find(|a| a.get("name").and_then(|n| n.as_str()) == Some(name))?
        .get("digest")?
        .as_str()?
        .strip_prefix("sha256:")
        .map(str::to_lowercase)
}

/// The release a pin should hold: the tag its `version_from` ref names, else upstream's newest.
fn target_version(pin: &Pin, ssot: &Value, policy: &Policy) -> Result<String, String> {
    let named = pin
        .version_from
        .as_deref()
        .map(|from| {
            dotted(ssot, from)
                .and_then(Value::as_str)
                .ok_or(format!(
                    "[build.pins.{}].version_from '{from}' names nothing",
                    pin.component
                ))
                .map(|r| image_tag(r).unwrap_or_else(|| "latest".into()))
        })
        .transpose()?;
    let tag = match named {
        Some(t) if t != "latest" => return Ok(t.trim_start_matches(&pin.tag_prefix).to_string()),
        _ => resolve_git(&format!("https://github.com/{}.git", pin.repo), policy)?,
    };
    Ok(tag
        .strip_prefix(&pin.tag_prefix)
        .unwrap_or(&tag)
        .to_string())
}

/// Refresh every pin. Returns the edited SSOT text and one line per change;
/// vendored copies are written only when `write` is set.
fn refresh(root: &Path, text: &str, write: bool) -> Result<(String, Vec<String>), String> {
    let ssot: Value = text
        .parse()
        .map_err(|e| format!("mios.toml does not parse: {e}"))?;
    let policy = Policy::from_value(&ssot)?;
    let tmp = std::env::temp_dir().join(format!("mios-refresh-pins.{}", std::process::id()));
    fs::create_dir_all(&tmp).map_err(|e| format!("{}: {e}", tmp.display()))?;
    let result = (|| {
        let mut text = text.to_string();
        let mut changes = Vec::new();
        for pin in pins_from_value(&ssot)? {
            let version = target_version(&pin, &ssot, &policy)?;
            let tag = format!("{}{version}", pin.tag_prefix);
            let table = format!("build.pins.{}", pin.component);
            if pin.version != version {
                changes.push(format!(
                    "{}: version {} -> {version}",
                    pin.component, pin.version
                ));
                text = set_in_table(&text, &table, "version", &version)?;
            }
            for a in &pin.assets {
                let name = a.name.replace("{version}", &version);
                let dl = tmp.join(&name);
                curl_to(
                    &format!(
                        "https://github.com/{}/releases/download/{tag}/{name}",
                        pin.repo
                    ),
                    &dl,
                )?;
                let got = sha256_file(&dl)?;
                if let Some(want) = github_digest(&pin.repo, &tag, &name, &tmp) {
                    if want != got {
                        return Err(format!(
                            "{name}@{tag}: downloaded sha256 {got} != GitHub's {want}"
                        ));
                    }
                }
                if a.sha256 != got {
                    changes.push(format!(
                        "{}.{}: {name} sha256 -> {got}",
                        pin.component, a.arch
                    ));
                    text =
                        set_in_table(&text, &format!("{table}.assets.{}", a.arch), "sha256", &got)?;
                }
                if let Some(v) = &a.vendored {
                    let dest: PathBuf = root.join(v.trim_start_matches('/'));
                    let current = sha256_file(&dest).ok();
                    if current.as_deref() != Some(got.as_str()) {
                        changes.push(format!(
                            "{}.{}: vendored {v} rewritten",
                            pin.component, a.arch
                        ));
                        if write {
                            fs::copy(&dl, &dest).map_err(|e| format!("{}: {e}", dest.display()))?;
                        }
                    }
                }
            }
        }
        // The edit must leave a parseable SSOT whose pins read back as written.
        let back: Value = text
            .parse()
            .map_err(|e| format!("edited mios.toml does not parse: {e}"))?;
        pins_from_value(&back)?;
        Ok((text, changes))
    })();
    let _ = fs::remove_dir_all(&tmp);
    result
}

/// `refresh-pins [--check]`: 0 when current (or refreshed), 1 on drift under --check, 2 on error.
pub fn run(root: &Path, args: &[String]) -> i32 {
    let check = args.iter().any(|a| a == "--check");
    let path = root.join("usr/share/mios/mios.toml");
    let text = match fs::read_to_string(&path) {
        Ok(t) => t,
        Err(e) => {
            eprintln!("mios-bake-plan refresh-pins: {}: {e}", path.display());
            return 2;
        }
    };
    match refresh(root, &text, !check) {
        Err(e) => {
            eprintln!("mios-bake-plan refresh-pins: {e}");
            2
        }
        Ok((_, changes)) if changes.is_empty() => {
            eprintln!("mios-bake-plan refresh-pins: [build.pins] is current");
            0
        }
        Ok((new_text, changes)) => {
            for c in &changes {
                eprintln!("mios-bake-plan refresh-pins: {c}");
            }
            if check {
                return 1;
            }
            if let Err(e) = fs::write(&path, new_text) {
                eprintln!("mios-bake-plan refresh-pins: {}: {e}", path.display());
                return 2;
            }
            0
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const SSOT: &str = r#"[theme.cursor_linux]
source_repo = "ful1e5/Bibata_Cursor"

[image.sidecars]
code_server = "ghcr.io/coder/code-server:4.139.1"   # pinned

[build.pins.code_server]
repository   = "coder/code-server"   # read by 59
version_from = "image.sidecars.code_server"
tag_prefix   = "v"
version      = "4.139.1"

[build.pins.code_server.assets.x86_64]
name   = "code-server-{version}-amd64.rpm"
sha256 = "aaaa"   # keep me

[build.pins.bibata]
repository_from = "theme.cursor_linux.source_repo"
tag_prefix = "v"
version    = "2.0.7"

[build.pins.bibata.assets.noarch]
name     = "Bibata-Modern-Classic.tar.xz"
vendored = "/usr/share/mios/vendored/cursors/bibata.tar.xz"

[after]
version = "untouched"
"#;

    #[test]
    fn pins_read_repository_once_and_follow_repository_from() {
        let pins = pins_from_value(&SSOT.parse().unwrap()).unwrap();
        let bib = pins.iter().find(|p| p.component == "bibata").unwrap();
        assert_eq!(bib.repo, "ful1e5/Bibata_Cursor");
        assert_eq!(
            bib.assets[0].vendored.as_deref(),
            Some("/usr/share/mios/vendored/cursors/bibata.tar.xz")
        );
        let cs = pins.iter().find(|p| p.component == "code_server").unwrap();
        assert_eq!(
            cs.version_from.as_deref(),
            Some("image.sidecars.code_server")
        );
    }

    #[test]
    fn a_value_is_replaced_in_its_own_table_only_keeping_alignment_and_comment() {
        let out = set_in_table(SSOT, "build.pins.code_server", "version", "4.146.0").unwrap();
        assert!(out.contains("version      = \"4.146.0\"\n"));
        assert!(
            out.contains("version    = \"2.0.7\"\n"),
            "another pin changed"
        );
        assert!(
            out.contains("version = \"untouched\"\n"),
            "another table changed"
        );
        let out = set_in_table(
            &out,
            "build.pins.code_server.assets.x86_64",
            "sha256",
            "bbbb",
        )
        .unwrap();
        assert!(out.contains("sha256 = \"bbbb\"   # keep me\n"));
        assert_eq!(out.lines().count(), SSOT.lines().count());
    }

    #[test]
    fn a_missing_key_is_added_inside_its_table() {
        let out = set_in_table(SSOT, "build.pins.bibata.assets.noarch", "sha256", "cccc").unwrap();
        let v: Value = out.parse().unwrap();
        assert_eq!(
            dotted(&v, "build.pins.bibata.assets.noarch.sha256").and_then(Value::as_str),
            Some("cccc")
        );
        assert!(dotted(&v, "after.sha256").is_none());
    }

    #[test]
    fn a_missing_table_is_an_error_not_an_append() {
        assert!(set_in_table(SSOT, "build.pins.nope", "version", "1").is_err());
    }

    #[test]
    fn repositories_and_tags_normalize() {
        assert_eq!(
            normalize_repo("https://github.com/Satty-org/Satty").as_deref(),
            Some("Satty-org/Satty")
        );
        assert_eq!(
            normalize_repo("https://github.com/a/b.git").as_deref(),
            Some("a/b")
        );
        assert_eq!(normalize_repo("https://codeberg.org/a/b"), None);
        assert_eq!(normalize_repo("a/b/c"), None);
        assert_eq!(
            image_tag("ghcr.io/coder/code-server:4.139.1").as_deref(),
            Some("4.139.1")
        );
        assert_eq!(image_tag("localhost:5000/x"), None);
    }

    #[test]
    fn the_shipped_pins_are_complete() {
        let root = std::env::var_os("MIOS_TEST_ROOT")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../.."));
        let text = fs::read_to_string(root.join("usr/share/mios/mios.toml")).unwrap();
        let ssot: Value = text.parse().unwrap();
        let pins = pins_from_value(&ssot).unwrap();
        assert!(!pins.is_empty());
        let hex = Regex::new("^[0-9a-f]{64}$").unwrap();
        for p in &pins {
            assert!(!p.version.is_empty(), "{} pins no version", p.component);
            for a in &p.assets {
                assert!(
                    hex.is_match(&a.sha256),
                    "{}.{} has no sha256",
                    p.component,
                    a.arch
                );
                if let Some(v) = &a.vendored {
                    let got = sha256_file(&root.join(v.trim_start_matches('/'))).unwrap();
                    assert_eq!(got, a.sha256, "{v} is not the {} pin", p.component);
                }
            }
            if let Some(from) = &p.version_from {
                let r = dotted(&ssot, from).and_then(Value::as_str).unwrap();
                if let Some(t) = image_tag(r).filter(|t| t != "latest") {
                    assert_eq!(t, p.version, "{} pin trails {from}", p.component);
                }
            }
        }
    }
}
