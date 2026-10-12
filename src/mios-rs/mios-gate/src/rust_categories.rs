// AI-hint: Asserts [rust.categories] SSOT registry integrity on both layers: crates (owner, binary, install_dir, role; on disk and registered) and scripts (each universe script claimed by exactly one category, replaces= deleted, unowned under max_unowned).
// AI-related: usr/share/mios/mios.toml, usr/share/doc/mios/adr/0021-rust-static-binary-consolidation.md, docs/design/doc-rust-static-port.md, automation/98-drift-checks.sh, tests/drift-gate-negatives.sh

use crate::Report;
use regex::Regex;
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use std::process::Command;

const CHECK: &str = "rust-categories";
const SSOT: &str = "usr/share/mios/mios.toml";
const EXEMPT: &str = "exempt";
/// [rust.categories] keys that configure the gate itself; every other key is
/// a category table.
const META_KEYS: [&str; 6] = [
    "doc",
    "binaries",
    "max_unowned",
    "universe",
    "owners",
    "roles",
];
/// Keys a category table must not carry, each with the one place it is declared.
const RESTATED_KEYS: [(&str, &str); 3] = [
    (
        "owner",
        "[rust.categories.owners] lists every category under its lane",
    ),
    (
        "role",
        "[rust.categories.roles] lists every category under its role",
    ),
    (
        "install_dir",
        "its role's [build.native.categories.<role>].install_dir is the one declaration",
    ),
];
/// How many unowned examples to list before truncating to the summary.
const MAX_EXAMPLES: usize = 20;

fn cannot_run(why: impl Into<String>) -> Report {
    Report {
        check: CHECK.to_string(),
        ok: false,
        could_not_run: Some(why.into()),
        summary: String::new(),
        findings: Vec::new(),
    }
}

/// Repo-relative glob -> anchored regex. `**/` also matches zero directories;
/// `*` and `?` never cross a `/`; everything else is literal.
fn glob_to_regex(pattern: &str) -> Option<Regex> {
    let mut out = String::from("^");
    let chars: Vec<char> = pattern.chars().collect();
    let mut i = 0;
    while i < chars.len() {
        if chars[i] == '*' && i + 1 < chars.len() && chars[i + 1] == '*' {
            if i + 2 < chars.len() && chars[i + 2] == '/' {
                out.push_str("(?:.*/)?");
                i += 3;
            } else {
                out.push_str(".*");
                i += 2;
            }
        } else if chars[i] == '*' {
            out.push_str("[^/]*");
            i += 1;
        } else if chars[i] == '?' {
            out.push_str("[^/]");
            i += 1;
        } else {
            out.push_str(&regex::escape(&chars[i].to_string()));
            i += 1;
        }
    }
    out.push('$');
    Regex::new(&out).ok()
}

fn is_script(rel: &str) -> bool {
    ["sh", "ps1", "py"]
        .iter()
        .any(|e| rel.ends_with(&format!(".{e}")))
}

/// The tracked-file universe, as ports-bound reads it: `git ls-files`, so
/// ignored scratch never counts and no directory walk invents files.
fn tracked_files(root: &Path) -> Result<Vec<String>, String> {
    let out = Command::new("git")
        .arg("-C")
        .arg(root)
        .arg("ls-files")
        .output()
        .map_err(|e| format!("cannot run git ls-files: {e}"))?;
    if !out.status.success() {
        return Err(format!(
            "git ls-files failed: {}",
            String::from_utf8_lossy(&out.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&out.stdout)
        .lines()
        .map(|l| l.trim().replace('\\', "/"))
        .filter(|l| !l.is_empty())
        .collect())
}

/// `[rust.categories.<key>]` (`label = [category, ...]`) read as
/// `category -> [label, ...]`, so a category under no label or several is
/// visible to the caller.
fn memberships(
    categories: &toml::Table,
    key: &str,
    findings: &mut Vec<String>,
) -> BTreeMap<String, Vec<String>> {
    let mut out: BTreeMap<String, Vec<String>> = BTreeMap::new();
    let Some(table) = categories.get(key).and_then(|v| v.as_table()) else {
        findings.push(format!(
            "[rust.categories.{key}] is missing -- each category's entry is declared there, once per label"
        ));
        return out;
    };
    for (label, list) in table {
        let Some(list) = list.as_array() else {
            findings.push(format!(
                "[rust.categories.{key}].{label} is not a list of categories"
            ));
            continue;
        };
        for cat in list {
            match cat.as_str() {
                Some(c) => out.entry(c.to_string()).or_default().push(label.clone()),
                None => findings.push(format!(
                    "[rust.categories.{key}].{label} holds a non-string entry"
                )),
            }
        }
    }
    out
}

struct Category {
    porting: bool,
    globs: Vec<(String, Regex)>,
}

pub fn check(root: &Path) -> Report {
    let ssot_path = root.join(SSOT);
    if !ssot_path.is_file() {
        return cannot_run(format!("{} is missing", ssot_path.display()));
    }
    let Ok(ssot_text) = std::fs::read_to_string(&ssot_path) else {
        return cannot_run("usr/share/mios/mios.toml could not be read");
    };
    let Ok(ssot_val) = ssot_text.parse::<toml::Value>() else {
        return cannot_run("usr/share/mios/mios.toml did not parse as valid TOML");
    };

    let Some(categories) = ssot_val
        .get("rust")
        .and_then(|r| r.get("categories"))
        .and_then(|c| c.as_table())
    else {
        return Report {
            check: CHECK.to_string(),
            ok: false,
            could_not_run: None,
            summary: String::new(),
            findings: vec![
                "[rust.categories] table is missing from usr/share/mios/mios.toml".to_string(),
            ],
        };
    };

    let mut findings: Vec<String> = Vec::new();
    let mut cataloged_crates: BTreeSet<String> = BTreeSet::new();

    // --- gate configuration (script layer) ----------------------------------
    let binaries: BTreeSet<String> = categories
        .get("binaries")
        .and_then(|b| b.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    if binaries.is_empty() {
        findings.push(
            "[rust.categories].binaries is missing or empty -- the allowed destination-binary set is the registry's contract"
                .to_string(),
        );
    }
    let max_unowned: i64 = categories
        .get("max_unowned")
        .and_then(|m| m.as_integer())
        .unwrap_or(-1);
    if max_unowned < 0 {
        findings.push(
            "[rust.categories].max_unowned is missing or negative -- the unowned-script ceiling is shrink-only and must be a non-negative integer"
                .to_string(),
        );
    }
    let universe: Vec<String> = categories
        .get("universe")
        .and_then(|u| u.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    if universe.is_empty() {
        findings.push(
            "[rust.categories].universe is missing or empty -- the script universe (root prefixes) decides what the gate must account for"
                .to_string(),
        );
    }

    // --- 1. audit each category table ---------------------------------------
    // Owner and role labels are declared ONCE each, as lists of categories:
    // repeating `owner = "native-tier"` in ten tables stated one label ten
    // times, and a typo in one of them passed every presence check.
    let owners = memberships(categories, "owners", &mut findings);
    let roles = memberships(categories, "roles", &mut findings);
    for (key, listed) in [("owners", &owners), ("roles", &roles)] {
        for cat in listed.keys() {
            if META_KEYS.contains(&cat.as_str())
                || !categories.get(cat).is_some_and(toml::Value::is_table)
            {
                findings.push(format!(
                    "[rust.categories.{key}] lists '{cat}', which is no [rust.categories] category"
                ));
            }
        }
    }
    let native_roles = ssot_val
        .get("build")
        .and_then(|b| b.get("native"))
        .and_then(|n| n.get("categories"));
    let mut cats: BTreeMap<String, Category> = BTreeMap::new();
    let mut replaces_claims: usize = 0;
    for (cat_name, cat_val) in categories {
        if META_KEYS.contains(&cat_name.as_str()) {
            continue;
        }
        let Some(table) = cat_val.as_table() else {
            findings.push(format!("[rust.categories.{cat_name}] is not a table"));
            continue;
        };

        // A per-category copy of a fact declared elsewhere is a second
        // declaration that can disagree with the first.
        for (key, owner) in RESTATED_KEYS {
            if table.contains_key(key) {
                findings.push(format!(
                    "category '{cat_name}' declares {key} inline -- {owner}"
                ));
            }
        }
        match owners.get(cat_name).map(Vec::as_slice) {
            Some([_]) => {}
            Some(many) if many.len() > 1 => findings.push(format!(
                "category '{cat_name}' is listed under {} owners ({}) -- one category, one owner",
                many.len(),
                many.join(", ")
            )),
            _ => findings.push(format!(
                "category '{cat_name}' has no owner -- list it under one [rust.categories.owners] lane"
            )),
        }
        let role = match roles.get(cat_name).map(Vec::as_slice) {
            Some([role]) => Some(role.as_str()),
            Some(many) if many.len() > 1 => {
                findings.push(format!(
                    "category '{cat_name}' is listed under {} roles ({}) -- one category, one role",
                    many.len(),
                    many.join(", ")
                ));
                None
            }
            _ => {
                findings.push(format!(
                    "category '{cat_name}' has no role specified -- list it under one [rust.categories.roles] role"
                ));
                None
            }
        };
        let is_exempt = role == Some(EXEMPT);

        // Binary: an exemption has none and must say why; a porting category
        // targets a binary inside the allowed set.
        let binary = table.get("binary").and_then(|b| b.as_str()).unwrap_or("");
        if is_exempt {
            if table.contains_key("binary") {
                findings.push(format!(
                    "category '{cat_name}' is exempt but names a binary -- an exemption has no destination"
                ));
            }
            let reason = table
                .get("description")
                .and_then(|d| d.as_str())
                .unwrap_or("");
            if reason.trim().is_empty() {
                findings.push(format!(
                    "category '{cat_name}' is exempt but carries no description -- an exemption without its reason cannot be reviewed"
                ));
            }
        } else if binary.trim().is_empty() {
            findings.push(format!("category '{cat_name}' has no destination binary"));
        } else if !binaries.is_empty() && !binaries.contains(binary) {
            findings.push(format!(
                "category '{cat_name}' targets binary '{binary}', which [rust.categories].binaries does not list"
            ));
        }

        // The role names the build table that owns the install directory.
        if let (false, Some(role)) = (is_exempt, role) {
            let dir = native_roles
                .and_then(|r| r.get(role))
                .and_then(|t| t.get("install_dir"))
                .and_then(|d| d.as_str())
                .unwrap_or("");
            if dir.trim().is_empty() {
                findings.push(format!(
                    "category '{cat_name}' has role '{role}', but [build.native.categories.{role}] declares no install_dir -- the role is where its binary installs"
                ));
            }
        }

        // Crates: the destination binary's own crate is implied by `binary`;
        // `crates` lists only the others whose code folds into it.
        let crate_exists = |name: &str| {
            root.join("tools/native")
                .join(name)
                .join("Cargo.toml")
                .is_file()
                || root
                    .join("src/mios-rs")
                    .join(name)
                    .join("Cargo.toml")
                    .is_file()
        };
        let mut owned_crates = 0usize;
        if !is_exempt && !binary.trim().is_empty() && crate_exists(binary) {
            cataloged_crates.insert(binary.to_string());
            owned_crates += 1;
        }
        for cr in table
            .get("crates")
            .and_then(|c| c.as_array())
            .map(Vec::as_slice)
            .unwrap_or_default()
        {
            let Some(crate_name) = cr.as_str() else {
                findings.push(format!(
                    "category '{cat_name}' contains non-string crate entry"
                ));
                continue;
            };
            if crate_name == binary {
                findings.push(format!(
                    "category '{cat_name}' lists its own destination crate '{crate_name}' -- binary already says so"
                ));
            }
            cataloged_crates.insert(crate_name.to_string());
            owned_crates += 1;
            if !crate_exists(crate_name) {
                findings.push(format!(
                    "category '{cat_name}' references crate '{crate_name}', but no Cargo.toml found in tools/native/ or src/mios-rs/"
                ));
            }
        }
        if owned_crates == 0 && !is_exempt {
            findings.push(format!("category '{cat_name}' defines no crates"));
        }

        // Replaced scripts check: any script listed in `replaces` must NOT
        // exist on disk (per ADR-0021 / doc-rust-static-port.md: scripts are
        // deleted in the same commit that proves parity).
        if let Some(replaces) = table.get("replaces").and_then(|r| r.as_array()) {
            for script_val in replaces {
                if let Some(script_path) = script_val.as_str() {
                    replaces_claims += 1;
                    if root.join(script_path).exists() {
                        findings.push(format!(
                            "script '{script_path}' is listed in [rust.categories.{cat_name}].replaces but still exists on disk (must be deleted in same commit that proves parity)"
                        ));
                    }
                }
            }
        }

        // Script scope globs: must compile; dead globs surface after the
        // tracked-file listing is read below.
        let mut globs: Vec<(String, Regex)> = Vec::new();
        if let Some(scopes) = table.get("scope").and_then(|s| s.as_array()) {
            for s in scopes.iter().filter_map(|v| v.as_str()) {
                match glob_to_regex(s) {
                    Some(re) => globs.push((s.to_string(), re)),
                    None => findings.push(format!(
                        "category '{cat_name}' has scope glob '{s}' that does not compile"
                    )),
                }
            }
        }
        cats.insert(
            cat_name.clone(),
            Category {
                porting: !is_exempt,
                globs,
            },
        );
    }

    if cats.is_empty() {
        findings.push(
            "[rust.categories] declares no categories -- the registry is the port plan, not a banner"
                .to_string(),
        );
    }

    // --- 2. every crate on disk must be registered ---------------------------
    let mut disk_crates: BTreeSet<String> = BTreeSet::new();
    for ws in ["tools/native", "src/mios-rs"] {
        if let Ok(entries) = std::fs::read_dir(root.join(ws)) {
            for entry in entries.flatten() {
                let name = entry.file_name();
                if name == "target" {
                    continue;
                }
                if entry.path().join("Cargo.toml").is_file() {
                    disk_crates.insert(name.to_string_lossy().to_string());
                }
            }
        }
    }
    for disk_crate in &disk_crates {
        if !cataloged_crates.contains(disk_crate) {
            findings.push(format!(
                "crate '{disk_crate}' on disk is not registered in [rust.categories]"
            ));
        }
    }

    // --- 3. script ownership over the declared universe -----------------------
    let tracked = match tracked_files(root) {
        Ok(t) => t,
        Err(why) => return cannot_run(why),
    };

    for (name, cat) in &cats {
        for (glob, re) in &cat.globs {
            if !tracked.iter().any(|f| re.is_match(f)) {
                findings.push(format!(
                    "category '{name}' scope glob '{glob}' matches no tracked file -- a scope is coverage, not decoration"
                ));
            }
        }
    }

    let in_universe = |rel: &str| {
        is_script(rel)
            && universe.iter().any(|p| {
                if p == "." {
                    !rel.contains('/')
                } else {
                    rel.starts_with(&format!("{}/", p.trim_end_matches('/')))
                }
            })
    };
    let universe_files: Vec<&String> = tracked.iter().filter(|f| in_universe(f)).collect();

    let mut unowned: Vec<&String> = Vec::new();
    let mut owned_porting = 0usize;
    let mut owned_exempt = 0usize;
    for file in &universe_files {
        let mut porting_owners: Vec<&str> = Vec::new();
        let mut exempt_owners: Vec<&str> = Vec::new();
        for (name, cat) in &cats {
            if cat.globs.iter().any(|(_, re)| re.is_match(file)) {
                if cat.porting {
                    porting_owners.push(name);
                } else {
                    exempt_owners.push(name);
                }
            }
        }
        if porting_owners.len() > 1 {
            findings.push(format!(
                "script '{}' is claimed by {} porting categories ({}) -- one script, one destination binary",
                file,
                porting_owners.len(),
                porting_owners.join(", ")
            ));
        }
        if !porting_owners.is_empty() {
            owned_porting += 1;
        } else if !exempt_owners.is_empty() {
            owned_exempt += 1;
        } else {
            unowned.push(file);
        }
    }

    if max_unowned >= 0 && unowned.len() as i64 > max_unowned {
        for f in unowned.iter().take(MAX_EXAMPLES) {
            findings.push(format!(
                "script '{}' is owned by no [rust.categories] entry -- give it a category scope; the ceiling only shrinks",
                f
            ));
        }
        if unowned.len() > MAX_EXAMPLES {
            findings.push(format!(
                "... and {} more unowned scripts (ceiling {})",
                unowned.len() - MAX_EXAMPLES,
                max_unowned
            ));
        }
    }

    let ok = findings.is_empty();
    let summary = if ok {
        format!(
            "{} crate(s) cataloged across {} categories; {} script(s) in universe: {} porting-owned, {} exempt, {} unowned (ceiling {}); {} replaces= claims verified absent",
            cataloged_crates.len(),
            cats.len(),
            universe_files.len(),
            owned_porting,
            owned_exempt,
            unowned.len(),
            max_unowned,
            replaces_claims,
        )
    } else {
        format!(
            "{} violation(s) in [rust.categories] ({} unowned of {} universe scripts, ceiling {})",
            findings.len(),
            unowned.len(),
            universe_files.len(),
            max_unowned,
        )
    };

    Report {
        check: CHECK.to_string(),
        ok,
        could_not_run: None,
        summary,
        findings,
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used, clippy::panic)]
mod tests {
    use super::*;
    use std::path::PathBuf;
    use std::process::Command as StdCommand;

    fn fixture(kind: &str) -> PathBuf {
        let dir = tempfile::tempdir().expect("tempdir");
        let root = dir.keep();
        std::fs::create_dir_all(root.join("usr/share/mios")).expect("dirs");
        std::fs::create_dir_all(root.join("usr/libexec/mios/db")).expect("dirs");
        std::fs::create_dir_all(root.join("usr/lib/mios/agent-pipe")).expect("dirs");
        std::fs::create_dir_all(root.join("tools/native/mios-serve")).expect("dirs");
        std::fs::write(
            root.join("tools/native/mios-serve/Cargo.toml"),
            "[package]\n",
        )
        .expect("write");
        std::fs::write(root.join("usr/libexec/mios/db/backup.py"), "#!/bin/sh\n").expect("write");
        std::fs::write(root.join("usr/lib/mios/agent-pipe/server.py"), "x = 1\n").expect("write");
        if kind == "unowned_over_ceiling" {
            std::fs::write(root.join("tool.ps1"), "exit 0\n").expect("write");
        }
        std::fs::write(root.join("usr/share/mios/mios.toml"), registry(kind)).expect("write");
        // The universe is tracked files; make the fixture a repository of itself.
        StdCommand::new("git")
            .arg("-C")
            .arg(&root)
            .args(["init", "-q"])
            .status()
            .expect("git init");
        StdCommand::new("git")
            .arg("-C")
            .arg(&root)
            .args(["add", "-A"])
            .status()
            .expect("git add");
        root
    }

    fn registry(kind: &str) -> String {
        let base = "[rust.categories]\nbinaries = [\"mios-serve\"]\nmax_unowned = 0\nuniverse = [\"usr/libexec/mios/\", \"usr/lib/mios/\", \".\"]\n[rust.categories.owners]\nport-lane = [\"serve\"]\nagent-plane = [\"ai-plane\"]\n[rust.categories.roles]\nservices = [\"serve\"]\nexempt = [\"ai-plane\"]\n[rust.categories.serve]\nbinary = \"mios-serve\"\nscope = [\"usr/libexec/mios/db/*.py\"]\n[rust.categories.ai-plane]\ndescription = \"python AI plane\"\nscope = [\"usr/lib/mios/agent-pipe/**\"]\n[build.native.categories.services]\ninstall_dir = \"/usr/libexec/mios\"\n";
        match kind {
            "missing_owner" => base.replace("port-lane = [\"serve\"]\n", ""),
            "bad_binary" => {
                base.replace("binary = \"mios-serve\"\nscope", "binary = \"mios-nonsense\"\nscope")
            }
            "dead_glob" => base.replace(
                "scope = [\"usr/libexec/mios/db/*.py\"]",
                "scope = [\"usr/libexec/mios/db/*.py\", \"usr/libexec/mios/nowhere/*.py\"]",
            ),
            "unowned_over_ceiling" => base.to_string(),
            "phantom_replaces" => base.replace(
                "scope = [\"usr/libexec/mios/db/*.py\"]",
                "scope = [\"usr/libexec/mios/db/*.py\"]\nreplaces = [\"usr/libexec/mios/db/backup.py\"]",
            ),
            "overlap" => base.replace(
                "[rust.categories.ai-plane]",
                "[rust.categories.second]\nbinary = \"mios-serve\"\nscope = [\"usr/libexec/mios/db/*.py\"]\n\n[rust.categories.ai-plane]",
            ),
            "restated" => base.replace(
                "binary = \"mios-serve\"\nscope",
                "binary = \"mios-serve\"\nrole = \"services\"\ncrates = [\"mios-serve\"]\nscope",
            ),
            "two_owners" => base.replace("agent-plane = [\"ai-plane\"]", "agent-plane = [\"ai-plane\", \"serve\"]"),
            "missing_registry" => "meta = 1\n".to_string(),
            _ => base.to_string(),
        }
    }

    #[test]
    fn valid_registry_is_clean() {
        let root = fixture("valid");
        let r = check(&root);
        assert!(
            r.could_not_run.is_none(),
            "unexpected could_not_run: {:?}",
            r.could_not_run
        );
        assert!(r.findings.is_empty(), "findings: {:#?}", r.findings);
        assert!(r.ok);
    }

    #[test]
    fn missing_registry_is_a_violation() {
        let root = fixture("missing_registry");
        let r = check(&root);
        assert!(r.could_not_run.is_none());
        assert!(!r.ok, "a deleted registry must violate, not pass");
        assert!(
            r.findings.iter().any(|f| f.contains("table is missing")),
            "findings: {:#?}",
            r.findings
        );
    }

    #[test]
    fn missing_owner_fails_for_that_reason() {
        let root = fixture("missing_owner");
        let r = check(&root);
        assert!(!r.ok);
        assert!(
            r.findings
                .iter()
                .any(|f| f.contains("'serve' has no owner")),
            "findings: {:#?}",
            r.findings
        );
    }

    #[test]
    fn a_fact_restated_in_a_category_fails() {
        // The role list and the binary already say both of these.
        let r = check(&fixture("restated"));
        assert!(!r.ok);
        for needle in [
            "'serve' declares role inline",
            "lists its own destination crate 'mios-serve'",
        ] {
            assert!(
                r.findings.iter().any(|f| f.contains(needle)),
                "{needle}: {:#?}",
                r.findings
            );
        }
    }

    #[test]
    fn a_category_under_two_owners_fails() {
        let r = check(&fixture("two_owners"));
        assert!(!r.ok);
        assert!(
            r.findings
                .iter()
                .any(|f| f.contains("'serve' is listed under 2 owners")),
            "findings: {:#?}",
            r.findings
        );
    }

    #[test]
    fn binary_outside_allowed_set_fails() {
        let root = fixture("bad_binary");
        let r = check(&root);
        assert!(!r.ok);
        assert!(
            r.findings
                .iter()
                .any(|f| f.contains("'mios-nonsense'") && f.contains("does not list")),
            "findings: {:#?}",
            r.findings
        );
    }

    #[test]
    fn dead_scope_glob_fails() {
        let root = fixture("dead_glob");
        let r = check(&root);
        assert!(!r.ok);
        assert!(
            r.findings
                .iter()
                .any(|f| f.contains("nowhere") && f.contains("matches no tracked file")),
            "findings: {:#?}",
            r.findings
        );
    }

    #[test]
    fn unowned_over_ceiling_fails() {
        // tool.ps1 sits in the universe ("." root) and no scope claims it.
        let root = fixture("unowned_over_ceiling");
        let r = check(&root);
        assert!(!r.ok);
        assert!(
            r.findings
                .iter()
                .any(|f| f.contains("tool.ps1") && f.contains("owned by no")),
            "findings: {:#?}",
            r.findings
        );
    }

    #[test]
    fn phantom_replaces_claim_fails() {
        let root = fixture("phantom_replaces");
        let r = check(&root);
        assert!(!r.ok);
        assert!(
            r.findings
                .iter()
                .any(|f| f.contains("backup.py") && f.contains("still exists on disk")),
            "findings: {:#?}",
            r.findings
        );
    }

    #[test]
    fn two_porting_owners_of_one_script_fail() {
        let root = fixture("overlap");
        let r = check(&root);
        assert!(!r.ok);
        assert!(
            r.findings
                .iter()
                .any(|f| f.contains("claimed by 2 porting categories")),
            "findings: {:#?}",
            r.findings
        );
    }

    #[test]
    fn glob_star_does_not_cross_slashes() {
        let re = glob_to_regex("a/*/c.rs").expect("compiles");
        assert!(re.is_match("a/b/c.rs"));
        assert!(!re.is_match("a/b/d/c.rs"));
    }

    #[test]
    fn glob_doublestar_spans_directories() {
        let re = glob_to_regex("a/**/*.py").expect("compiles");
        assert!(re.is_match("a/x.py"));
        assert!(re.is_match("a/b/c/d.py"));
        assert!(!re.is_match("b/a/x.py"));
    }
}
