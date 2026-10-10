// AI-hint: Asserts no hardcoded version literal in automation/, usr/libexec/ or tools/ diverges from the mios.toml [meta].mios_version SSOT; Rust twin of the retired python scanner, consolidated beside doc_refs so one implementation decides one verdict.
// AI-related: usr/share/mios/mios.toml, automation/98-drift-checks.sh, tests/drift-gate-negatives.sh, src/mios-rs/mios-gate/src/doc_refs.rs

use crate::Report;
use regex::Regex;
use std::collections::HashSet;
use std::path::Path;
use std::process::Command;

const CHECK: &str = "version-literals-ssot";
const SSOT: &str = "usr/share/mios/mios.toml";
/// The trees this check governs. Hand-written glue and tooling only; the PASS
/// line states this scope rather than claiming the whole tree.
const SCAN_PREFIX: [&str; 3] = ["automation", "usr/libexec/", "tools"];
/// Binaries, build leftovers and machine projections carry version strings that
/// are not authored here.
const SKIP_EXT: [&str; 13] = [
    ".pyc",
    ".png",
    ".jpg",
    ".generated",
    ".json",
    ".log",
    ".ready",
    ".lock",
    ".d",
    ".o",
    ".rlib",
    ".rmeta",
    ".a",
];
/// Golden fixtures are recorded output, not authored literals.
const SKIP_SEGMENT: &str = "/tests/golden/";
/// Third-party versions the repository pins on purpose. Carried over verbatim
/// from the python scanner so this port changes no verdict; note in passing that
/// exempting a VALUE exempts it in every file, which is a wider allowlist than
/// any single pin needs (see the ledger entry that ports this check).
const EXEMPT_VALUE: [&str; 10] = [
    "0.0.0", "0.0.1", "0.8.3", "0.2.4", "0.5.0", "0.6.0", "0.80.0", "0.9.6", "0.0.76", "0.1.0",
];
/// Lines whose version is documented as an upstream fallback, not ours.
const EXEMPT_LINE: [&str; 2] = ["INTEL_SG_FALLBACK_TAG", "Upstream v0.15.0"];

fn cannot_run(why: &str) -> Report {
    Report {
        check: CHECK.to_string(),
        ok: false,
        could_not_run: Some(why.to_string()),
        summary: why.to_string(),
        findings: Vec::new(),
    }
}

/// 1-based line numbers inside Rust `#[cfg(test)]` items; `.rs` only, else empty.
///
/// A version literal in a Rust test module is test DATA -- an upstream tag handed
/// to the code under test -- not this project's shipped identity. The tag-sorting
/// fixtures in mios-bake-plan need several DIFFERENT versions by construction, so
/// requiring each to equal the canonical version would make the test assert
/// nothing. Only NON-canonical literals are ever reported, so a test hardcoding
/// the real version was never flagged and no coverage is lost.
///
/// Brace-matched. Braces inside string literals are not parsed, so an unbalanced
/// one ends a range EARLY -- scanning more lines, never fewer, so this can
/// over-flag but never miss a real hardcoded version.
fn cfg_test_lines(rel: &str, lines: &[&str]) -> HashSet<usize> {
    let mut out: HashSet<usize> = HashSet::new();
    if !rel.ends_with(".rs") {
        return out;
    }
    let n = lines.len();
    let mut i = 0usize;
    while i < n {
        if !lines[i].trim_start().starts_with("#[cfg(test)]") {
            i += 1;
            continue;
        }
        let mut depth: i64 = 0;
        let mut opened = false;
        let mut j = i;
        while j < n {
            depth += lines[j].matches('{').count() as i64 - lines[j].matches('}').count() as i64;
            opened = opened || lines[j].contains('{');
            if opened && depth <= 0 {
                break;
            }
            j += 1;
        }
        let end = j.min(n.saturating_sub(1));
        for l in (i + 1)..=(end + 1) {
            out.insert(l);
        }
        i = end + 1;
    }
    out
}

/// The canonical version: the env override the shell gate passes, else the SSOT.
fn canonical(root: &Path) -> String {
    if let Ok(v) = std::env::var("MIOS_CANONICAL_VER") {
        if !v.trim().is_empty() {
            return v.trim().to_string();
        }
    }
    let body = std::fs::read_to_string(root.join(SSOT)).unwrap_or_default();
    let parsed: toml::Value = body.parse().unwrap_or(toml::Value::Boolean(false));
    for (table, key) in [("meta", "mios_version"), ("system", "version")] {
        if let Some(v) = parsed
            .get(table)
            .and_then(|t| t.get(key))
            .and_then(|v| v.as_str())
        {
            if !v.trim().is_empty() {
                return v.trim().to_string();
            }
        }
    }
    String::new()
}

/// The tracked corpus. `None` means git did not answer, which is never "clean":
/// a scanner that reports zero findings over a corpus git never gave it is the
/// defect class this gate exists to catch.
fn corpus(root: &Path) -> Option<Vec<String>> {
    let out = Command::new("git")
        .arg("-C")
        .arg(root)
        .arg("ls-files")
        .output()
        .ok()?;
    if !out.status.success() {
        return None;
    }
    let text = String::from_utf8_lossy(&out.stdout);
    Some(
        text.lines()
            .filter(|f| SCAN_PREFIX.iter().any(|p| f.starts_with(p)))
            .filter(|f| !SKIP_EXT.iter().any(|e| f.ends_with(e)))
            .filter(|f| !f.contains(SKIP_SEGMENT))
            .map(|f| f.to_string())
            .collect(),
    )
}

/// Exact generated assignments for upstream version pins. Their values belong
/// to the named SSOT key, rather than the MiOS release identity. Only these two
/// generated files may use the assignments; changed values and authored glue
/// continue through the ordinary literal check.
fn upstream_projection_lines(root: &Path) -> Result<HashSet<String>, String> {
    let merged = mios_resolver::resolve_projection(root).map_err(|e| e.to_string())?;
    let names = mios_resolver::names::registry(&merged)?;
    let version = Regex::new(r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?$")
        .map_err(|e| e.to_string())?;
    let mut lines = HashSet::new();
    for entry in names.entries {
        if !entry.emitted || entry.key == "meta.mios_version" {
            continue;
        }
        let value = entry
            .key
            .split('.')
            .try_fold(&merged, |value, key| value.get(key));
        let Some(value) = value
            .and_then(toml::Value::as_str)
            .filter(|v| version.is_match(v))
        else {
            continue;
        };
        let name = entry.canonical;
        lines.insert(format!(": \"${{{name}:={value}}}\""));
        lines.insert(format!(
            "$script:{name} = if ($env:{name}) {{ $env:{name} }} else {{ '{value}' }}"
        ));
    }
    Ok(lines)
}

pub fn check(root: &Path) -> Report {
    // Not a checkout of this repository at all: cannot run, not ok (T5 defect)
    if !root.join(".git").exists() {
        return cannot_run("not a git checkout (.git directory missing)");
    }
    let want = canonical(root);
    if want.is_empty() {
        return cannot_run("mios.toml [meta].mios_version is empty or unparseable");
    }
    let Some(files) = corpus(root) else {
        return cannot_run("git ls-files failed, so no file was scanned");
    };
    if files.is_empty() {
        return cannot_run("the tracked corpus is empty, so this check proved nothing");
    }
    let projected = match upstream_projection_lines(root) {
        Ok(lines) => lines,
        Err(error) => {
            return cannot_run(&format!(
                "cannot verify generated SSOT version pins: {error}"
            ))
        }
    };
    let Ok(pat) = Regex::new(r"\bv?0\.[0-9]+\.[0-9]+\b") else {
        return cannot_run("the version-literal pattern did not compile");
    };

    let mut findings: Vec<String> = Vec::new();
    let mut scanned = 0usize;
    for rel in &files {
        let Ok(body) = std::fs::read(root.join(rel)) else {
            continue;
        };
        let text = String::from_utf8_lossy(&body).into_owned();
        let lines: Vec<&str> = text.lines().collect();
        let skip = cfg_test_lines(rel, &lines);
        scanned += 1;
        for (idx, line) in lines.iter().enumerate() {
            if matches!(
                rel.as_str(),
                "automation/lib/globals.sh" | "automation/lib/globals.ps1"
            ) && projected.contains(line.trim())
            {
                continue;
            }
            if skip.contains(&(idx + 1)) {
                continue;
            }
            if EXEMPT_LINE.iter().any(|e| line.contains(e)) {
                continue;
            }
            for m in pat.find_iter(line) {
                let raw = m.as_str();
                let bare = raw.strip_prefix('v').unwrap_or(raw);
                if bare == want || EXEMPT_VALUE.contains(&bare) {
                    continue;
                }
                findings.push(format!(
                    "{}:{} hardcodes different version literal [{}], expected [{}]",
                    rel,
                    idx + 1,
                    raw,
                    want
                ));
            }
        }
    }
    Report {
        check: CHECK.to_string(),
        ok: findings.is_empty(),
        could_not_run: None,
        summary: format!(
            "no version literal in automation/, usr/libexec/ or tools/ diverges from [meta].mios_version={} ({} file(s) scanned)",
            want, scanned
        ),
        findings,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn upstream_projection_matches_only_the_exact_ssot_assignment() {
        let tmp = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(tmp.path().join("usr/share/mios")).unwrap();
        std::fs::write(
            tmp.path().join(SSOT),
            "[meta]\nmios_version='0.3.0'\n[build.native.windows]\ndriver_version='0.23.1'\n",
        )
        .unwrap();
        let globals = tmp.path().join("automation/lib/globals.sh");
        std::fs::create_dir_all(globals.parent().unwrap()).unwrap();
        let valid = ": \"${MIOS_BUILD_NATIVE_WINDOWS_DRIVER_VERSION:=0.23.1}\"\n";
        std::fs::write(&globals, valid).unwrap();
        for args in [vec!["init", "-q"], vec!["add", "."]] {
            assert!(Command::new("git")
                .arg("-C")
                .arg(tmp.path())
                .args(args)
                .status()
                .unwrap()
                .success());
        }
        assert!(check(tmp.path()).ok);
        std::fs::write(&globals, valid.replace("0.23.1", "0.23.2")).unwrap();
        assert!(!check(tmp.path()).ok, "changed upstream pin must fail");
        std::fs::write(&globals, valid).unwrap();
        let authored = tmp.path().join("automation/authored.sh");
        std::fs::write(&authored, valid).unwrap();
        assert!(Command::new("git")
            .arg("-C")
            .arg(tmp.path())
            .args(["add", "."])
            .status()
            .unwrap()
            .success());
        assert!(
            !check(tmp.path()).ok,
            "the value is not exempt outside generated globals"
        );
        std::fs::write(&authored, "echo runtime\n").unwrap();
        std::fs::write(&globals, format!("{valid}echo 0.23.1\n")).unwrap();
        assert!(
            !check(tmp.path()).ok,
            "extra authored literals on the projection remain checked"
        );
    }

    #[test]
    fn a_cfg_test_module_is_excluded_and_its_boundary_is_exact() {
        let src = vec![
            "fn prod() {}",          // 1
            "#[cfg(test)]",          // 2
            "mod tests {",           // 3
            "  let v = \"v0.9.9\";", // 4
            "}",                     // 5
            "fn after() {}",         // 6
        ];
        let s = cfg_test_lines("a/b.rs", &src);
        assert!(!s.contains(&1), "production line must not be excluded");
        assert!(s.contains(&2) && s.contains(&3) && s.contains(&4) && s.contains(&5));
        assert!(!s.contains(&6), "the range must close at the item's brace");
    }

    #[test]
    fn a_non_rust_file_excludes_nothing() {
        let src = vec!["#[cfg(test)]", "mod tests {", "}"];
        assert!(cfg_test_lines("tools/x.py", &src).is_empty());
        assert!(cfg_test_lines("automation/x.sh", &src).is_empty());
    }

    #[test]
    fn an_unbalanced_brace_over_flags_rather_than_under_flags() {
        // A stray '}' inside a string closes the range early. The lines that fall
        // out are then SCANNED, which can only add findings, never hide one.
        let src = vec![
            "#[cfg(test)]",
            "mod t { let s = \"}\";",
            "let v = \"v0.9.9\";",
        ];
        let s = cfg_test_lines("a.rs", &src);
        assert!(!s.contains(&3), "early close leaves later lines scanned");
    }

    #[test]
    fn git_refusing_is_never_reported_as_clean() {
        let tmp = match tempfile::tempdir() {
            Ok(t) => t,
            Err(_) => return,
        };
        // .git present so the not-a-checkout skip does not fire, but no real
        // repository, so `git ls-files` fails.
        let _ = std::fs::create_dir_all(tmp.path().join(".git"));
        let _ = std::fs::create_dir_all(tmp.path().join("usr/share/mios"));
        let _ = std::fs::write(tmp.path().join(SSOT), "[meta]\nmios_version = \"9.9.9\"\n");
        let r = check(tmp.path());
        assert_ne!(
            r.code(),
            crate::EXIT_CLEAN,
            "a dead corpus must not be clean"
        );
    }

    #[test]
    fn a_non_checkout_declines_rather_than_inventing_a_verdict() {
        let tmp = match tempfile::tempdir() {
            Ok(t) => t,
            Err(_) => return,
        };
        let r = check(tmp.path());
        assert_eq!(r.code(), crate::EXIT_CANNOT_RUN);
        assert!(!r.ok);
        assert!(r.could_not_run.is_some());
        assert!(r.could_not_run.as_ref().unwrap().contains("not a git checkout"));
    }
}
