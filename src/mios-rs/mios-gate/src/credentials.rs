// AI-hint: Law-11 extension gate: fails any credential literal baked into a world-readable unit whose exact path:KEY=value is not on the shrink-only register.
// AI-related: usr/share/mios/mios.toml, usr/share/containers/systemd, usr/lib/systemd/system, tools/native/mios-gen/src/pod_quadlets.rs

use crate::Report;
use std::collections::BTreeSet;
use std::path::Path;

const CHECK: &str = "credential-literals";

/// Law 11 scans only .env files; a Quadlet is a world-readable unit under /usr
/// and carries the same hazard.
const UNIT_DIRS: [&str; 2] = ["usr/share/containers/systemd", "usr/lib/systemd/system"];

fn cannot_run(why: impl Into<String>) -> Report {
    Report {
        check: CHECK.to_string(),
        ok: false,
        could_not_run: Some(why.into()),
        summary: String::new(),
        findings: Vec::new(),
    }
}

/// A credential-shaped variable name or an explicit key from [security.secret_keys].
/// Counters and feature flags are not credentials, however they are spelled.
fn is_credential_key(key: &str, explicit: &BTreeSet<String>) -> bool {
    if explicit.contains(key) {
        return true;
    }
    let looks = key.contains("PASSWORD")
        || key.contains("SECRET")
        || key.contains("APIKEY")
        || key.contains("API_KEY")
        || key.contains("TOKEN");
    if !looks {
        return false;
    }
    let not_cred = key.contains("MAX_TOKENS")
        || key.ends_with("_TOKENS")
        || key.starts_with("ENABLE_")
        || key.ends_with("_ENABLED")
        || key.contains("NUM_")
        || key.ends_with("_LIMIT");
    !not_cred
}

/// True when the value is a real baked literal rather than an indirection.
fn is_literal(val: &str) -> bool {
    if val.is_empty() || val.starts_with("${") || val.starts_with('%') {
        return false; // indirected through an env var or a systemd specifier
    }
    let lower = val.to_ascii_lowercase();
    if lower == "true" || lower == "false" {
        return false;
    }
    !val.chars().all(|c| c.is_ascii_digit())
}

/// Every KEY=VALUE on one unit line that could carry a credential.
///
/// `Environment=` is the declarative surface. `--env KEY=VALUE` on an Exec line
/// is the OTHER one, and scanning only the first measured the wrong property:
/// mios-agents.service hands its container a password as
/// `--env PASSWORD=${MIOS_IDENTITY_DEFAULT_PASSWORD}` on an ExecStart continuation, so a
/// plain literal planted there passed this gate at rc=0 while the identical
/// literal on an `Environment=` line failed it. Both controls were run.
///
/// Bare `-e` is deliberately NOT matched: it collides with ordinary flags such
/// as `bash -e`, and nothing in the corpus uses it to pass an environment pair.
fn credential_pairs(line: &str) -> Vec<(&str, &str)> {
    let trimmed = line.trim();
    let mut out = Vec::new();

    if let Some(rest) = trimmed.strip_prefix("Environment=") {
        if let Some(pair) = rest.split_once('=') {
            out.push(pair);
        }
    }

    // A single Exec line can carry several `--env` flags, so walk them all
    // rather than taking the first.
    let mut hay = trimmed;
    while let Some(idx) = hay.find("--env") {
        let after = &hay[idx + "--env".len()..];
        hay = after;
        // `--env KEY=V` and `--env=KEY=V` both occur in podman invocations.
        let after = after.strip_prefix('=').unwrap_or(after);
        // Whitespace-split drops the trailing line-continuation backslash.
        let Some(token) = after.split_whitespace().next() else {
            continue;
        };
        if let Some(pair) = token.split_once('=') {
            out.push(pair);
        }
    }

    out
}

fn walk(dir: &Path, out: &mut Vec<std::path::PathBuf>) {
    let Ok(rd) = std::fs::read_dir(dir) else {
        return;
    };
    let mut entries: Vec<_> = rd.flatten().map(|e| e.path()).collect();
    entries.sort();
    for p in entries {
        if p.is_dir() {
            walk(&p, out);
        } else if p.is_file() {
            out.push(p);
        }
    }
}

pub fn check(root: &Path) -> Report {
    let ssot = root.join("usr/share/mios/mios.toml");
    if !ssot.is_file() {
        return cannot_run(format!("{} is missing", ssot.display()));
    }
    let Ok(text) = std::fs::read_to_string(&ssot) else {
        return cannot_run("usr/share/mios/mios.toml could not be read");
    };
    let Ok(val) = text.parse::<toml::Value>() else {
        return cannot_run("usr/share/mios/mios.toml did not parse");
    };

    let mut files: Vec<std::path::PathBuf> = Vec::new();
    let mut present_dirs = 0usize;
    for d in UNIT_DIRS {
        let full = root.join(d);
        if full.is_dir() {
            present_dirs += 1;
            walk(&full, &mut files);
        }
    }
    // An empty scan is not a clean scan: a moved unit directory would otherwise
    // report "no credential literals" forever.
    if present_dirs == 0 || files.is_empty() {
        return cannot_run("no unit files were scanned, so nothing was compared");
    }

    let sec_table = val.get("security");
    let explicit_secrets: BTreeSet<String> = sec_table
        .and_then(|s| s.get("secret_keys"))
        .and_then(|sk| sk.get("keys"))
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str())
                .map(|s| s.trim().to_string())
                .collect()
        })
        .unwrap_or_default();

    let mut found: BTreeSet<String> = BTreeSet::new();
    for path in &files {
        let Ok(body) = std::fs::read_to_string(path) else {
            continue;
        };
        // Forward slashes always: a backslash spelling made every entry read as
        // NEW on one host and as removed on the other, for an identical tree.
        let rel = path
            .strip_prefix(root)
            .unwrap_or(path)
            .to_string_lossy()
            .replace('\\', "/");
        for line in body.lines() {
            for (key, value) in credential_pairs(line) {
                if !key
                    .chars()
                    .all(|c| c.is_ascii_uppercase() || c.is_ascii_digit() || c == '_')
                {
                    continue;
                }
                if !is_credential_key(key, &explicit_secrets) {
                    continue;
                }
                let value = value.trim();
                if !is_literal(value) {
                    continue;
                }
                // The register pins the VALUE, not just the key. A key-only register
                // cannot tell the shipped placeholder from an operator's real
                // password baked in by a build-environment variable.
                found.insert(format!("{rel}:{key}={value}"));
            }
        }
    }

    let cfg = sec_table.and_then(|s| s.get("credential_literals"));
    let allowed: BTreeSet<String> = cfg
        .and_then(|c| c.get("grandfathered"))
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str())
                .map(|s| s.trim().to_string())
                .collect()
        })
        .unwrap_or_default();

    let mut findings: Vec<String> = Vec::new();
    if let Some(sec) = sec_table {
        if let Some(sk) = sec.get("secret_keys") {
            let min_keys = sk.get("min_keys").and_then(|m| m.as_integer()).unwrap_or(0);
            if (explicit_secrets.len() as i64) < min_keys {
                findings.push(format!(
                    "[security.secret_keys] declares {} keys, below min_keys floor of {}",
                    explicit_secrets.len(),
                    min_keys
                ));
            }
        }
    }
    for e in &allowed {
        if !e.contains('=') {
            findings.push(format!(
                "{e}: register entry pins no value -- write it as path:KEY=value so a changed \
                 credential is a new finding rather than a silent pass"
            ));
        }
    }
    for f in found.difference(&allowed) {
        findings.push(format!(
            "NEW credential literal baked into a world-readable unit: {f}"
        ));
    }
    for f in allowed.difference(&found) {
        if f.contains('=') {
            findings.push(format!(
                "grandfathered entry no longer present (shrink the list): {f}"
            ));
        }
    }

    let ok = findings.is_empty();
    Report {
        check: CHECK.to_string(),
        ok,
        could_not_run: None,
        summary: format!(
            "no new unit credential literals ({} grandfathered, shrink-only, {} unit file(s) scanned)",
            found.len(),
            files.len()
        ),
        findings,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// The surface that was unscanned. mios-agents.service passes its container
    /// a password as `--env PASSWORD=...` on an ExecStart continuation; before
    /// credential_pairs, a plain literal there passed the gate at rc=0 while the
    /// identical literal on an `Environment=` line failed it.
    #[test]
    fn exec_env_flag_is_a_credential_surface() {
        assert_eq!(
            credential_pairs("  --env PASSWORD=hunter2 \\"),
            vec![("PASSWORD", "hunter2")]
        );
        assert_eq!(
            credential_pairs("  --env=PASSWORD=hunter2 \\"),
            vec![("PASSWORD", "hunter2")]
        );
    }

    #[test]
    fn environment_lines_still_parse() {
        assert_eq!(
            credential_pairs("Environment=PASSWORD=hunter2"),
            vec![("PASSWORD", "hunter2")]
        );
    }

    /// One Exec line can carry many flags; taking only the first would leave
    /// every later pair unscanned.
    #[test]
    fn every_env_flag_on_a_line_is_extracted() {
        let pairs =
            credential_pairs("ExecStart=/usr/bin/podman run --env A=1 --env PASSWORD=x --env B=2");
        assert_eq!(pairs, vec![("A", "1"), ("PASSWORD", "x"), ("B", "2")]);
    }

    /// Indirection is not a literal -- this is what keeps the shipped
    /// `--env PASSWORD=${MIOS_IDENTITY_DEFAULT_PASSWORD}` from being reported.
    #[test]
    fn indirected_values_are_not_literals() {
        let pairs = credential_pairs("  --env PASSWORD=${MIOS_IDENTITY_DEFAULT_PASSWORD} \\");
        assert_eq!(
            pairs,
            vec![("PASSWORD", "${MIOS_IDENTITY_DEFAULT_PASSWORD}")]
        );
        assert!(!is_literal(pairs[0].1));
        assert!(is_literal("hunter2"));
    }

    /// A bare `-e` is an ordinary flag (`bash -e`), not an environment pair.
    #[test]
    fn bare_dash_e_is_not_matched() {
        assert!(credential_pairs("ExecStart=/bin/bash -e PASSWORD=x").is_empty());
    }
}
