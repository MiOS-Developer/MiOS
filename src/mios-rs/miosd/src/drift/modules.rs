// AI-hint: Module boundary, length, and unwired module hygiene checks for miosd drift runner.
// AI-related: usr/lib/mios/agent-pipe/, automation/98-drift-checks.sh

use super::{Check, DriftCtx, Verdict};

pub struct ModuleBoundaryCheck;
impl Check for ModuleBoundaryCheck {
    fn id(&self) -> &'static str {
        "check_module_boundary"
    }
    fn describe(&self) -> &'static str {
        "Assert agent-pipe python modules respect architectural boundary rules"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        super::audit::verdict(boundary(ctx))
    }
}

pub struct ModuleLengthCheck;
impl Check for ModuleLengthCheck {
    fn id(&self) -> &'static str {
        "check_module_length"
    }
    fn describe(&self) -> &'static str {
        "Assert no python module exceeds maximum allowed line count"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        super::audit::verdict(length(ctx))
    }
}

fn boundary(ctx: &DriftCtx) -> super::audit::Audit {
    let mut count = 0;
    let mut errors = Vec::new();
    for path in super::audit::files(&ctx.root, "usr/lib/mios/agent-pipe")? {
        let filename = path.rsplit('/').next().unwrap_or("");
        if !filename.starts_with("mios_")
            || !filename.ends_with(".py")
            || path
                .trim_start_matches("usr/lib/mios/agent-pipe/")
                .contains('/')
        {
            continue;
        }
        count += 1;
        for import in super::audit::python_imports(ctx, &path)? {
            let forbidden = |name: &str| name == "server" || name.starts_with("server.");
            if import
                .module
                .as_deref()
                .map(forbidden)
                .unwrap_or_else(|| import.names.iter().any(|name| forbidden(name)))
            {
                errors.push(format!(
                    "{path}:{}: sibling imports server monolith",
                    import.line
                ));
            }
        }
    }
    super::audit::finish(
        count,
        errors,
        "top-level sibling import declarations (server-free boundary)",
    )
}

fn length(ctx: &DriftCtx) -> super::audit::Audit {
    let doc = super::audit::ssot(ctx)?;
    let limit = super::audit::at(&doc, "refactor.max_lines")?
        .as_integer()
        .filter(|n| *n > 0)
        .ok_or("Invalid SSOT refactor.max_lines")?;
    let registered = super::audit::at(&doc, "refactor.oversize")?
        .as_array()
        .ok_or("Invalid SSOT refactor.oversize")?;
    // The ceiling governs sub-modules only (operator ruling): modules under a
    // declared sub-module package. A component's main modules (the agent-pipe
    // root) are where features fold in, so they carry no line ceiling.
    let submodule_roots = super::audit::strings(&doc, "refactor.submodule_roots")?;
    if submodule_roots.is_empty() || submodule_roots.iter().any(|r| !r.ends_with('/')) {
        return Err(
            "SSOT refactor.submodule_roots must list package prefixes ending in '/'".into(),
        );
    }
    let mut expected = std::collections::BTreeMap::new();
    for row in registered {
        let path = row
            .get("path")
            .and_then(toml::Value::as_str)
            .ok_or("Oversize register omitted path")?;
        let lines = row
            .get("lines")
            .and_then(toml::Value::as_integer)
            .filter(|n| *n > 0)
            .ok_or("Invalid oversize registered line count")?;
        if expected.insert(path, lines).is_some() {
            return Err(format!("Duplicate oversize module registration {path}"));
        }
    }
    let mut seen = std::collections::BTreeSet::new();
    let mut errors = Vec::new();
    let mut count = 0;
    for path in super::audit::files(&ctx.root, "usr/lib/mios/agent-pipe")? {
        let rel = path
            .strip_prefix("usr/lib/mios/agent-pipe/")
            .ok_or("Module outside agent-pipe")?;
        if !rel.ends_with(".py")
            || rel.ends_with("__init__.py")
            || !submodule_roots
                .iter()
                .any(|root| rel.starts_with(root.as_str()))
        {
            continue;
        }
        let text = super::audit::read(&ctx.root, &path)?;
        if text
            .chars()
            .take(400)
            .collect::<String>()
            .contains("Re-export shim for")
        {
            continue;
        }
        count += 1;
        let lines = text.lines().count() as i64;
        if let Some(recorded) = expected.get(rel) {
            seen.insert(rel.to_owned());
            if lines != *recorded {
                errors.push(format!("{path}: {lines} lines differs from registered {recorded}; growth forbidden, shrinkage must lower register"));
            }
        } else if lines > limit {
            errors.push(format!(
                "{path}: {lines} lines exceeds SSOT limit {limit}; split module"
            ));
        }
    }
    for path in expected.keys() {
        if !seen.contains(*path) {
            errors.push(format!(
                "Oversize register names absent or excluded module {path}"
            ));
        }
    }
    super::audit::finish(count, errors, "agent-pipe module line/identity ratchet")
}

pub struct UnwiredModulesCheck;
impl Check for UnwiredModulesCheck {
    fn id(&self) -> &'static str {
        "check_unwired_modules"
    }
    fn describe(&self) -> &'static str {
        "Assert no dead or unwired modules exist without explicit allowlist"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        super::audit::verdict(wiring(ctx))
    }
}

fn wiring(ctx: &DriftCtx) -> super::audit::Audit {
    use super::audit;
    use std::collections::BTreeSet;
    let policy = audit::ssot(ctx)?;
    let allowed: BTreeSet<_> = audit::strings(&policy, "drift.denylist")?
        .into_iter()
        .collect();
    let roots = audit::strings(&policy, "drift.modules.import_roots")?;
    if roots.is_empty() {
        return Err("Module wiring import roots are empty".into());
    }
    let core = "usr/lib/mios/agent-pipe";
    if !roots.iter().any(|root| root == core) {
        return Err("Module wiring must examine the agent-pipe core".into());
    }
    let mut references = Vec::new();
    let mut modules = BTreeSet::new();
    let is_test = |path: &str| {
        let name = path.rsplit('/').next().unwrap_or("");
        name.starts_with("test_")
            || name.ends_with("_test.py")
            || path.split('/').any(|part| matches!(part, "test" | "tests"))
    };
    for root in roots {
        if root.starts_with('/') || root.contains('\\') || root.split('/').any(|part| part == "..")
        {
            return Err(format!("Unconfined module import root: {root}"));
        }
        for path in audit::files(&ctx.root, &root)? {
            if !path.ends_with(".py") || is_test(&path) {
                continue;
            }
            if path
                .rsplit_once('/')
                .is_some_and(|(parent, name)| parent == core && name.starts_with("mios_"))
            {
                modules.insert(
                    path.rsplit('/')
                        .next()
                        .ok_or("Module filename missing")?
                        .trim_end_matches(".py")
                        .to_owned(),
                );
            }
            let syntax = audit::python_syntax(ctx, &path)?;
            references.push((path, syntax));
        }
    }
    let mut dead = BTreeSet::new();
    for module in &modules {
        let own = format!("{core}/{module}.py");
        let binds = |node: &audit::PythonImport| {
            node.level == 0
                && (node.module.as_deref() == Some(module.as_str())
                    || (node.module.is_none() && node.names.iter().any(|name| name == module)))
        };
        let imported = references.iter().any(|(path, syntax)| {
            path.starts_with(&format!("{core}/"))
                && path != &own
                && syntax.imports.iter().any(binds)
        });
        if !imported {
            continue;
        }
        let wired = references.iter().any(|(path, syntax)| {
            path != &own
                && syntax
                    .imports
                    .iter()
                    .filter(|node| binds(node))
                    .any(|node| {
                        node.names.iter().any(|name| name == "*")
                            || node
                                .names
                                .iter()
                                .zip(&node.bindings)
                                .any(|(name, binding)| {
                                    (node.module.is_some() || name == module)
                                        && syntax.names.contains(binding)
                                })
                    })
        });
        if !wired {
            dead.insert(module.clone());
        }
    }
    let mut errors: Vec<_> = dead
        .difference(&allowed)
        .map(|module| {
            format!(
                "{module}: imported by agent-pipe without a non-test reference; wire or remove it"
            )
        })
        .collect();
    errors.extend(allowed.difference(&dead).map(|module| format!("{module}: stale unwired-module register entry; module is wired, absent or no longer imported")));
    audit::finish(
        modules.len(),
        errors,
        "AST module import/reference and shrink-only register audit",
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn wiring_distinguishes_alias_references_tests_docstrings_and_stale_allowances(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let temp = tempfile::tempdir()?;
        let base = temp.path().join("usr/lib/mios/agent-pipe");
        std::fs::create_dir_all(&base)?;
        std::fs::create_dir_all(temp.path().join("usr/share/mios"))?;
        let policy = temp.path().join("usr/share/mios/mios.toml");
        let config = "[drift]\ndenylist=[]\n[drift.lint]\npython='python3'\n[drift.modules]\nimport_roots=['usr/lib/mios/agent-pipe']\n";
        std::fs::write(&policy, config)?;
        let ctx = DriftCtx::new(temp.path().into(), false);
        assert!(wiring(&ctx).is_err());
        std::fs::write(base.join("mios_subject.py"), "def ping(): return 'value'\n")?;
        let caller = base.join("mios_caller.py");
        std::fs::write(&caller, "import mios_subject as subject\nsubject.ping()\n")?;
        assert!(wiring(&ctx).is_ok());
        std::fs::write(&caller, "import mios_subject as subject\n")?;
        std::fs::write(
            base.join("test_consumer.py"),
            "import mios_subject\nmios_subject.ping()\n",
        )?;
        assert!(wiring(&ctx).is_err_and(|error| error.contains("mios_subject: imported")));
        std::fs::write(
            &policy,
            config.replace("denylist=[]", "denylist=['mios_subject']"),
        )?;
        assert!(wiring(&ctx).is_ok());
        std::fs::write(&caller, "from mios_subject import ping as call\ncall()\n")?;
        assert!(wiring(&ctx).is_err_and(|error| error.contains("stale unwired-module")));
        std::fs::write(&policy, config)?;
        assert!(wiring(&ctx).is_ok());
        std::fs::write(&caller, "'''import mios_subject'''\n")?;
        assert!(wiring(&ctx).is_ok());
        std::fs::write(&caller, "def malformed(:\n")?;
        assert!(wiring(&ctx).is_err_and(|error| error.contains("Python AST")));
        Ok(())
    }

    #[test]
    fn boundary_and_length_read_subjects_and_detect_injected_import_growth_and_stale_identity(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let temp = tempfile::tempdir()?;
        let base = temp.path().join("usr/lib/mios/agent-pipe");
        std::fs::create_dir_all(&base)?;
        std::fs::create_dir_all(temp.path().join("usr/share/mios"))?;
        let policy = temp.path().join("usr/share/mios/mios.toml");
        std::fs::write(
            &policy,
            "[drift.lint]\npython='python3'\n[refactor]\nmax_lines = 2\nsubmodule_roots = ['mios_pipe/']\noversize = []\n",
        )?;
        let ctx = DriftCtx::new(temp.path().into(), false);
        assert!(boundary(&ctx).is_err());
        assert!(length(&ctx).is_err());
        let subject = base.join("mios_example.py");
        std::fs::write(&subject, "# import server\nimport serverless\n")?;
        assert!(boundary(&ctx).is_ok());
        // A root module is the component's main module and carries no ceiling,
        // so with no sub-module yet the ratchet has nothing to examine.
        assert!(length(&ctx).is_err_and(|e| e.contains("no subjects")));
        std::fs::create_dir_all(base.join("mios_pipe"))?;
        let sub = base.join("mios_pipe/example.py");
        std::fs::write(&sub, "a = 1\n")?;
        assert!(length(&ctx).is_ok());
        std::fs::write(
            &subject,
            "\"\"\"from server import app\nimport server\"\"\"\nimport os, serverless\n",
        )?;
        assert!(boundary(&ctx).is_ok());
        std::fs::write(&subject, "from server import app\n")?;
        assert!(boundary(&ctx).is_err_and(|e| e.contains("mios_example.py:1")));
        std::fs::write(&subject, "import os, server as alias\n")?;
        assert!(boundary(&ctx).is_err());
        // Main modules absorb folded features; sub-modules hold the ceiling.
        std::fs::write(&subject, "a = 1\nb = 2\nc = 3\n")?;
        assert!(length(&ctx).is_ok());
        std::fs::write(&sub, "a = 1\nb = 2\nc = 3\n")?;
        assert!(length(&ctx).is_err_and(|e| e.contains("exceeds SSOT limit 2")));
        std::fs::write(&policy, "[drift.lint]\npython='python3'\n[refactor]\nmax_lines = 2\nsubmodule_roots = ['mios_pipe/']\noversize = [{path='mios_pipe/example.py',lines=3}]\n")?;
        assert!(length(&ctx).is_ok());
        std::fs::write(&sub, "a = 1\n")?;
        assert!(length(&ctx).is_err_and(|e| e.contains("shrinkage must lower")));
        std::fs::write(&policy, "[drift.lint]\npython='python3'\n[refactor]\nmax_lines = 2\nsubmodule_roots = ['mios_pipe']\noversize = []\n")?;
        assert!(length(&ctx).is_err_and(|e| e.contains("submodule_roots")));
        std::fs::remove_file(sub)?;
        assert!(length(&ctx).is_err());
        Ok(())
    }
}
