// AI-hint: Fails when a committed artifact recipe carries a placeholder or a credential; a disk's credential is rendered from the operator at build time by miosd artifact-build, never stored in a recipe.
// AI-related: config/artifacts/, usr/share/mios/mios.toml, src/mios-rs/mios-build/src/artifacts.rs, tests/drift-gate-negatives.sh

use crate::Report;
use mios_build::artifacts::{KICKSTART_CREDENTIALS, PLACEHOLDER};
use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

const CHECK: &str = "artifact-recipes";

fn cannot_run(why: impl Into<String>) -> Report {
    Report {
        check: CHECK.to_string(),
        ok: false,
        could_not_run: Some(why.into()),
        summary: String::new(),
        findings: Vec::new(),
    }
}

/// The directories [deploy.formats] keeps recipes in: the shared recipe's and
/// every format's. Every TOML file there is a recipe, claimed or not.
fn recipe_dirs(root: &Path, ssot: &toml::Value) -> BTreeSet<PathBuf> {
    let formats = ssot.get("deploy").and_then(|d| d.get("formats"));
    let mut named: Vec<&str> = Vec::new();
    if let Some(shared) = formats
        .and_then(|f| f.get("shared_recipe"))
        .and_then(toml::Value::as_str)
    {
        named.push(shared);
    }
    for spec in formats
        .and_then(toml::Value::as_table)
        .into_iter()
        .flat_map(|t| t.values())
    {
        if let Some(recipe) = spec.get("recipe").and_then(toml::Value::as_str) {
            named.push(recipe);
        }
    }
    named
        .into_iter()
        .filter(|r| !r.trim().is_empty())
        .filter_map(|r| Path::new(r).parent().map(|p| root.join(p)))
        .collect()
}

/// What one recipe commits that it must not.
fn violations(rel: &str, body: &str) -> Vec<String> {
    let mut out = Vec::new();
    for (n, line) in body.lines().enumerate() {
        if line.contains(PLACEHOLDER) {
            out.push(format!(
                "{rel}:{}: carries a {PLACEHOLDER} placeholder, which a build would bake into the disk",
                n + 1
            ));
        }
    }
    let doc = match body.parse::<toml::Value>() {
        Ok(doc) => doc,
        Err(e) => {
            out.push(format!("{rel}: does not parse as TOML: {e}"));
            return out;
        }
    };
    let customizations = doc.get("customizations");
    for (i, user) in customizations
        .and_then(|c| c.get("user"))
        .and_then(toml::Value::as_array)
        .into_iter()
        .flatten()
        .enumerate()
    {
        for field in ["password", "key"] {
            if user.get(field).is_some() {
                out.push(format!(
                    "{rel}: customizations.user[{i}].{field} is a committed credential; \
                     miosd artifact-build renders it from the operator"
                ));
            }
        }
    }
    if let Some(contents) = customizations
        .and_then(|c| c.get("installer"))
        .and_then(|i| i.get("kickstart"))
        .and_then(|k| k.get("contents"))
        .and_then(toml::Value::as_str)
    {
        for line in contents.lines() {
            let command = line.split_whitespace().next().unwrap_or("");
            if KICKSTART_CREDENTIALS.contains(&command) {
                out.push(format!(
                    "{rel}: kickstart `{command}` is a committed account or credential; \
                     miosd artifact-build renders it from [identity] and the operator"
                ));
            }
        }
    }
    out
}

pub fn check(root: &Path) -> Report {
    let ssot_path = root.join("usr/share/mios/mios.toml");
    let Ok(text) = std::fs::read_to_string(&ssot_path) else {
        return cannot_run(format!("{} could not be read", ssot_path.display()));
    };
    let Ok(ssot) = text.parse::<toml::Value>() else {
        return cannot_run("usr/share/mios/mios.toml did not parse");
    };
    let dirs = recipe_dirs(root, &ssot);
    if dirs.is_empty() {
        return cannot_run("[deploy.formats] names no recipe directory, so nothing was compared");
    }

    let mut recipes = Vec::new();
    for dir in &dirs {
        let Ok(entries) = std::fs::read_dir(dir) else {
            return cannot_run(format!("{} could not be listed", dir.display()));
        };
        recipes.extend(
            entries
                .flatten()
                .map(|e| e.path())
                .filter(|p| p.extension().is_some_and(|x| x == "toml")),
        );
    }
    recipes.sort();
    if recipes.is_empty() {
        return cannot_run("the recipe directories hold no *.toml recipe, so nothing was compared");
    }

    let mut findings = Vec::new();
    for path in &recipes {
        let rel = path
            .strip_prefix(root)
            .unwrap_or(path)
            .to_string_lossy()
            .replace('\\', "/");
        match std::fs::read_to_string(path) {
            Ok(body) => findings.extend(violations(&rel, &body)),
            Err(e) => findings.push(format!("{rel}: unreadable: {e}")),
        }
    }
    Report {
        check: CHECK.to_string(),
        ok: findings.is_empty(),
        could_not_run: None,
        summary: format!(
            "{} artifact recipe(s) carry no placeholder and no credential; disks get the operator's at build time",
            recipes.len()
        ),
        findings,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tree(recipes: &[(&str, &str)]) -> tempfile::TempDir {
        let dir = tempfile::tempdir().unwrap();
        let ssot = dir.path().join("usr/share/mios");
        std::fs::create_dir_all(&ssot).unwrap();
        std::fs::write(
            ssot.join("mios.toml"),
            "[deploy.formats]\nshared_recipe = \"config/artifacts/bib.toml\"\n\
             [deploy.formats.vhdx]\nrecipe = \"\"\n\
             [deploy.formats.iso]\nrecipe = \"config/artifacts/iso.toml\"\n",
        )
        .unwrap();
        let art = dir.path().join("config/artifacts");
        std::fs::create_dir_all(&art).unwrap();
        for (name, body) in recipes {
            std::fs::write(art.join(name), body).unwrap();
        }
        dir
    }

    const CLEAN_ISO: &str =
        "[customizations.installer.kickstart]\ncontents = \"\"\"\ntext\nreboot --eject\n\"\"\"\n";
    const FLOOR: &str = "[[customizations.filesystem]]\nmountpoint = \"/\"\nminsize = \"80 GiB\"\n";

    #[test]
    fn clean_recipes_pass() {
        let dir = tree(&[("bib.toml", FLOOR), ("iso.toml", CLEAN_ISO)]);
        let report = check(dir.path());
        assert!(report.ok, "{:?}", report.findings);
        assert!(report.summary.starts_with("2 artifact recipe(s)"));
    }

    #[test]
    fn the_old_placeholder_recipe_fails() {
        let old = "[[customizations.user]]\nname = \"mios\"\n\
                   password = \"$6$REPLACEME_WITH_SHA512_HASH$REPLACEME\"\n\
                   key = \"ssh-ed25519 AAAA_REPLACE_WITH_REAL_PUBKEY mios@operator\"\n";
        let dir = tree(&[("bib.toml", FLOOR), ("vhdx.toml", old)]);
        let report = check(dir.path());
        assert!(!report.ok);
        let all = report.findings.join("\n");
        assert!(all.contains("vhdx.toml:3: carries a REPLACE"), "{all}");
        assert!(all.contains("user[0].password"), "{all}");
        assert!(all.contains("user[0].key"), "{all}");
    }

    #[test]
    fn a_real_looking_credential_fails_without_any_placeholder() {
        let committed = "[[customizations.user]]\nname = \"op\"\nkey = \"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIrealbody op@host\"\n";
        let dir = tree(&[("bib.toml", committed)]);
        assert!(!check(dir.path()).ok);
        let kickstart = CLEAN_ISO.replace(
            "text\n",
            "text\nsshkey --username=op \"ssh-ed25519 AAAA\"\n",
        );
        let dir = tree(&[("bib.toml", FLOOR), ("iso.toml", &kickstart)]);
        let report = check(dir.path());
        assert!(report.findings.join("\n").contains("kickstart `sshkey`"));
    }

    #[test]
    fn an_account_without_a_credential_is_not_a_finding() {
        let wsl = "[[customizations.user]]\nname = \"mios\"\ngroups = [\"wheel\"]\n";
        let dir = tree(&[("bib.toml", FLOOR), ("wsl2.toml", wsl)]);
        assert!(check(dir.path()).ok);
    }

    #[test]
    fn nothing_to_compare_cannot_run() {
        let dir = tree(&[]);
        assert!(check(dir.path()).could_not_run.is_some());
    }
}
