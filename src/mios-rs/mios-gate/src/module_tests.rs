// AI-hint: Asserts every agent-pipe module is NAMED by a unit test (imported, loaded by file, or reached through its re-export shim), and every tools/ and libexec Python module has a sibling test that names it or sits in the shrink-only grandfather ledger.
// AI-related: automation/98-drift-checks.sh, tests/drift-gate-negatives.sh, usr/share/mios/reference/python-untested-baseline.txt, usr/share/mios/mios.toml

use crate::Report;
use regex::Regex;
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};

const CHECK: &str = "module-test-coverage";

/// The component whose modules each need a unit test: its top-level `mios_*.py`
/// and every module under its `[refactor].submodule_roots` packages.
const COMPONENT: &str = "usr/lib/mios/agent-pipe";
/// Where the unit tests that may name them live. task_1006 folded the per-module
/// siblings into subject suites, and a module's suite may sit under tests/: what
/// counts is that a test NAMES the module, not what the test file is called.
const SUITE_DIRS: [&str; 2] = [COMPONENT, "tests"];
/// Python tooling held to the sibling-test rule, and the ledger that
/// grandfathers what predates it. The ledger only shrinks.
const RATCHET_DIRS: [&str; 2] = ["tools", "usr/libexec/mios"];
const BASELINE: &str = "usr/share/mios/reference/python-untested-baseline.txt";

struct Patterns {
    import: Regex,
    from: Regex,
    dotted: Regex,
    shim: Regex,
}

impl Patterns {
    fn new() -> Result<Self, regex::Error> {
        Ok(Self {
            import: Regex::new(r"(?m)(?:^|[;:])[ \t]*import[ \t]+([^\n;]+)")?,
            from: Regex::new(
                r"(?m)(?:^|[;:])[ \t]*from[ \t]+([A-Za-z_][\w.]*)[ \t]+import[ \t]*(\([^)]*\)|[^\n;]+)",
            )?,
            dotted: Regex::new(r"^[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*$")?,
            shim: Regex::new(r#"_ShimModule\(\s*__name__\s*,\s*["']([A-Za-z_][\w.]*)["']\s*\)"#)?,
        })
    }
}

fn report(ok: bool, summary: String, findings: Vec<String>) -> Report {
    Report {
        check: CHECK.to_string(),
        ok,
        could_not_run: None,
        summary,
        findings,
    }
}

/// Splits Python source into its code, with every string literal replaced by
/// `""` and every comment removed, and the bodies of those string literals.
/// Imports are read from the code alone, so an `import` inside a string (a
/// generated sitecustomize, a doc example) or a comment never counts.
fn lex(src: &str) -> (String, Vec<String>) {
    let b = src.as_bytes();
    let mut code: Vec<u8> = Vec::with_capacity(b.len());
    let mut strings = Vec::new();
    let mut i = 0;
    while i < b.len() {
        let c = b[i];
        if c == b'#' {
            while i < b.len() && b[i] != b'\n' {
                i += 1;
            }
            continue;
        }
        if c == b'"' || c == b'\'' {
            let triple = b.get(i + 1) == Some(&c) && b.get(i + 2) == Some(&c);
            let width = if triple { 3 } else { 1 };
            let start = i + width;
            let mut j = start;
            let end = loop {
                let Some(&ch) = b.get(j) else {
                    i = b.len();
                    break b.len();
                };
                if ch == b'\\' {
                    j += 2;
                } else if ch == c
                    && (!triple || (b.get(j + 1) == Some(&c) && b.get(j + 2) == Some(&c)))
                {
                    i = j + width;
                    break j;
                } else if ch == b'\n' && !triple {
                    // An unterminated one-line string ends at the line, as the
                    // tokenizer would refuse it there.
                    i = j;
                    break j;
                } else {
                    j += 1;
                }
            };
            let body = b.get(start..end.max(start)).unwrap_or_default();
            strings.push(String::from_utf8_lossy(body).into_owned());
            code.extend_from_slice(b"\"\"");
            continue;
        }
        code.push(c);
        i += 1;
    }
    (String::from_utf8_lossy(&code).into_owned(), strings)
}

fn insert_with_prefixes(dotted: &str, out: &mut BTreeSet<String>) {
    let mut acc = String::new();
    for part in dotted.split('.') {
        if !acc.is_empty() {
            acc.push('.');
        }
        acc.push_str(part);
        out.insert(acc.clone());
    }
}

/// Every module a Python source names: what its import statements bind
/// (`import a.b` -> a, a.b; `from a import b` -> a, a.b) and every
/// module-shaped string literal -- an importlib or mock.patch target with its
/// dotted prefixes, or a `.py` path by each of its suffixes, so
/// `".../mios_pipe/routing/x.py"` names `mios_pipe.routing.x`.
fn named_modules(src: &str, p: &Patterns) -> BTreeSet<String> {
    let (code, strings) = lex(src);
    let code = code.replace("\\\r\n", " ").replace("\\\n", " ");
    let mut out = BTreeSet::new();
    for cap in p.import.captures_iter(&code) {
        let Some(list) = cap.get(1) else { continue };
        for item in list.as_str().split(',') {
            if let Some(name) = item.split_whitespace().next() {
                if p.dotted.is_match(name) {
                    insert_with_prefixes(name, &mut out);
                }
            }
        }
    }
    for cap in p.from.captures_iter(&code) {
        let (Some(module), Some(list)) = (cap.get(1), cap.get(2)) else {
            continue;
        };
        let module = module.as_str().trim_end_matches('.');
        insert_with_prefixes(module, &mut out);
        let list = list.as_str().trim_start_matches('(').trim_end_matches(')');
        for item in list.split([',', '\n']) {
            if let Some(name) = item.split_whitespace().next() {
                if name != "*" && p.dotted.is_match(name) {
                    out.insert(format!("{module}.{name}"));
                }
            }
        }
    }
    for s in &strings {
        let s = s.trim();
        if p.dotted.is_match(s) {
            insert_with_prefixes(s, &mut out);
        }
        if let Some(stem) = s.strip_suffix(".py") {
            let segs: Vec<&str> = stem.split(['/', '\\']).filter(|x| !x.is_empty()).collect();
            let mut tail = segs.as_slice();
            while let Some((_, rest)) = tail.split_first() {
                out.insert(tail.join("."));
                tail = rest;
            }
        }
    }
    out
}

fn read_lossy(path: &Path) -> Option<String> {
    std::fs::read(path)
        .ok()
        .map(|b| String::from_utf8_lossy(&b).into_owned())
}

fn walk_py(dir: &Path, depth: usize, out: &mut Vec<PathBuf>) {
    if depth > 32 {
        return;
    }
    let Ok(rd) = std::fs::read_dir(dir) else {
        return;
    };
    for entry in rd.flatten() {
        let path = entry.path();
        let name = entry.file_name().to_string_lossy().into_owned();
        if path.is_dir() {
            if !(name.starts_with('.') || name == "__pycache__" || name == "node_modules") {
                walk_py(&path, depth + 1, out);
            }
        } else if name.ends_with(".py") {
            out.push(path);
        }
    }
}

fn file_name(path: &Path) -> String {
    path.file_name()
        .map(|n| n.to_string_lossy().into_owned())
        .unwrap_or_default()
}

fn rel(root: &Path, path: &Path) -> String {
    path.strip_prefix(root)
        .unwrap_or(path)
        .to_string_lossy()
        .replace('\\', "/")
}

/// The package roots inside the component, from `[refactor].submodule_roots`.
fn submodule_roots(root: &Path) -> Result<Vec<String>, String> {
    let path = root.join("usr/share/mios/mios.toml");
    let text = std::fs::read_to_string(&path)
        .map_err(|e| format!("cannot read {}: {e}", path.display()))?;
    let doc: toml::Value = text
        .parse()
        .map_err(|e| format!("mios.toml does not parse: {e}"))?;
    let roots: Vec<String> = doc
        .get("refactor")
        .and_then(|r| r.get("submodule_roots"))
        .and_then(|v| v.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|v| v.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default();
    if roots.is_empty() {
        return Err(
            "[refactor].submodule_roots lists no package, so no package module would be checked"
                .into(),
        );
    }
    Ok(roots)
}

/// The agent-pipe leg. Returns (modules checked, test files read).
fn component_leg(root: &Path, p: &Patterns, findings: &mut Vec<String>) -> (usize, usize) {
    let comp = root.join(COMPONENT);
    if !comp.is_dir() {
        findings.push(format!(
            "{COMPONENT} is absent -- a tracked deliverable is missing, so no module was checked"
        ));
        return (0, 0);
    }
    let packages = match submodule_roots(root) {
        Ok(r) => r,
        Err(e) => {
            findings.push(e);
            Vec::new()
        }
    };

    // The modules: (dotted name, path relative to the component).
    let mut modules: Vec<(String, String)> = Vec::new();
    // Re-export shims, both ways: a test of the shim is a test of its target.
    let mut aliases: BTreeMap<String, BTreeSet<String>> = BTreeMap::new();
    if let Ok(rd) = std::fs::read_dir(&comp) {
        for entry in rd.flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            if !(entry.path().is_file() && name.starts_with("mios_") && name.ends_with(".py")) {
                continue;
            }
            let stem = name.trim_end_matches(".py").to_string();
            if let Some(src) = read_lossy(&entry.path()) {
                if let Some(target) = p.shim.captures(&src).and_then(|c| c.get(1)) {
                    let target = target.as_str().to_string();
                    aliases
                        .entry(target.clone())
                        .or_default()
                        .insert(stem.clone());
                    aliases.entry(stem.clone()).or_default().insert(target);
                }
            }
            modules.push((stem, name));
        }
    }
    for pkg in &packages {
        let pkg = pkg.trim_end_matches('/');
        let dir = comp.join(pkg);
        if !dir.is_dir() {
            findings.push(format!(
                "[refactor].submodule_roots names {pkg}/, which is not a directory under {COMPONENT}"
            ));
            continue;
        }
        let mut files = Vec::new();
        walk_py(&dir, 0, &mut files);
        for f in files {
            let name = file_name(&f);
            if name.starts_with("test_") || name == "__init__.py" {
                continue;
            }
            let r = rel(&comp, &f);
            modules.push((r.trim_end_matches(".py").replace('/', "."), r));
        }
    }
    modules.sort();

    let mut named: BTreeSet<String> = BTreeSet::new();
    let mut read = 0;
    for d in SUITE_DIRS {
        let mut files = Vec::new();
        walk_py(&root.join(d), 0, &mut files);
        files.sort();
        for f in files {
            let name = file_name(&f);
            if !(name.starts_with("test_") || name.starts_with("test-")) {
                continue;
            }
            if let Some(src) = read_lossy(&f) {
                read += 1;
                named.extend(named_modules(&src, p));
            }
        }
    }

    if modules.is_empty() {
        findings.push(format!(
            "no module found under {COMPONENT}, so nothing was checked -- the scan is wrong"
        ));
    }
    if read == 0 {
        findings.push(format!(
            "no unit test file found under {} -- the corpus is wrong, so nothing can count as covered",
            SUITE_DIRS.join(" or ")
        ));
    }
    for (dotted, r) in &modules {
        let covered = named.contains(dotted)
            || aliases
                .get(dotted)
                .is_some_and(|a| a.iter().any(|alias| named.contains(alias)));
        if !covered {
            findings.push(format!(
                "{COMPONENT}/{r} is named by no unit test -- import it, load its file, or import its re-export shim from a test under {}",
                SUITE_DIRS.join(" or ")
            ));
        }
    }
    (modules.len(), read)
}

/// The tools/libexec leg. Returns (modules tested, modules grandfathered).
fn ratchet_leg(root: &Path, p: &Patterns, findings: &mut Vec<String>) -> (usize, usize) {
    let baseline_path = root.join(BASELINE);
    let baseline: BTreeSet<String> = match std::fs::read_to_string(&baseline_path) {
        Ok(text) => text
            .lines()
            .map(str::trim)
            .filter(|l| !l.is_empty() && !l.starts_with('#'))
            .map(str::to_string)
            .collect(),
        Err(_) => {
            findings.push(format!(
                "{BASELINE} is missing, so the tools/libexec sibling-test ratchet has no ledger to hold new modules against"
            ));
            return (0, 0);
        }
    };

    // Whether a sibling test exists for dir/name, and whether it names the module.
    let sibling = |dir: &Path, name: &str| -> Option<(String, bool)> {
        let stem = name.trim_end_matches(".py");
        let norm = stem.replace('-', "_");
        [
            format!("test_{name}"),
            format!("test_{stem}.py"),
            format!("test_{norm}.py"),
        ]
        .into_iter()
        .map(|t| dir.join(t))
        .find(|t| t.is_file())
        .map(|t| {
            let names = read_lossy(&t)
                .map(|src| named_modules(&src, p))
                .unwrap_or_default();
            (file_name(&t), names.contains(stem) || names.contains(&norm))
        })
    };

    let (mut tested, mut grandfathered) = (0, 0);
    for d in RATCHET_DIRS {
        let dir = root.join(d);
        let Ok(rd) = std::fs::read_dir(&dir) else {
            continue;
        };
        let mut names: Vec<String> = rd
            .flatten()
            .filter(|e| e.path().is_file())
            .map(|e| e.file_name().to_string_lossy().into_owned())
            .filter(|n| n.ends_with(".py") && !n.starts_with("test_") && n != "__init__.py")
            .collect();
        names.sort();
        for name in names {
            let r = format!("{d}/{name}");
            match sibling(&dir, &name) {
                Some((_, true)) => tested += 1,
                Some((t, false)) => findings.push(format!(
                    "{r}: its sibling {t} never names it (no import, no load of its file), so it tests something else"
                )),
                None if baseline.contains(&r) => grandfathered += 1,
                None => findings.push(format!(
                    "untested python module not in baseline: {r} -- author a sibling test_<module>.py that loads it"
                )),
            }
        }
    }
    for entry in &baseline {
        let path = root.join(entry);
        if !path.is_file() {
            findings.push(format!(
                "{BASELINE} grandfathers {entry}, which no longer exists -- remove the entry (the ledger only shrinks)"
            ));
            continue;
        }
        let (dir, name) = (
            path.parent().map(Path::to_path_buf).unwrap_or_default(),
            file_name(&path),
        );
        if let Some((t, true)) = sibling(&dir, &name) {
            findings.push(format!(
                "{BASELINE} grandfathers {entry}, which {t} now tests -- remove the entry (the ledger only shrinks)"
            ));
        }
    }
    (tested, grandfathered)
}

pub fn check(root: &Path) -> Report {
    // Static patterns, but a gate never panics: a compile failure is a finding.
    let p = match Patterns::new() {
        Ok(p) => p,
        Err(e) => {
            return report(
                false,
                String::new(),
                vec![format!(
                    "internal: module-name patterns failed to compile: {e}"
                )],
            )
        }
    };
    let mut findings = Vec::new();
    let (modules, read) = component_leg(root, &p, &mut findings);
    let (tested, grandfathered) = ratchet_leg(root, &p, &mut findings);
    findings.sort();
    findings.dedup();
    let ok = findings.is_empty();
    report(
        ok,
        format!(
            "{modules} {COMPONENT} module(s) each named by one of {read} unit test file(s); \
             {tested} tools/libexec python module(s) named by a sibling test, {grandfathered} grandfathered"
        ),
        findings,
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn names(src: &str) -> BTreeSet<String> {
        named_modules(src, &Patterns::new().unwrap())
    }

    #[test]
    fn imports_bind_modules_and_their_parents() {
        let n = names(
            "import os, mios_pipe.routing.chat as C\n\
             from mios_pipe.routing import (dispatch_cmd as D,\n    vision_context)\n\
             try: import mios_kvfork\nexcept ImportError: pass\n",
        );
        for want in [
            "os",
            "mios_pipe",
            "mios_pipe.routing",
            "mios_pipe.routing.chat",
            "mios_pipe.routing.dispatch_cmd",
            "mios_pipe.routing.vision_context",
            "mios_kvfork",
        ] {
            assert!(n.contains(want), "missing {want}: {n:?}");
        }
    }

    #[test]
    fn strings_name_patch_targets_and_files() {
        let n = names(
            "patch(\"mios_pipe.memory.pg.execute\")\n\
             p = os.path.join(ROOT, 'usr', 'lib', 'mios_net_anomaly.py')\n\
             spec = load('tools/check-testhygiene.py')\n",
        );
        for want in [
            "mios_pipe.memory.pg",
            "mios_net_anomaly",
            "check-testhygiene",
        ] {
            assert!(n.contains(want), "missing {want}: {n:?}");
        }
    }

    #[test]
    fn comments_prose_and_code_inside_strings_name_nothing() {
        let n = names(
            "# import mios_ghost\n\
             \"\"\"Unit test for mios_phantom.\"\"\"\n\
             BLOB = '''import mios_hidden\nfrom mios_hidden2 import x'''\n\
             print('testing mios_prose now')\n",
        );
        for absent in [
            "mios_ghost",
            "mios_phantom",
            "mios_hidden",
            "mios_hidden2",
            "mios_prose",
        ] {
            assert!(!n.contains(absent), "{absent} should not count: {n:?}");
        }
    }

    #[test]
    fn escaped_quotes_do_not_end_a_string() {
        let n = names("s = \"a \\\" import mios_trap\"\nimport mios_real\n");
        assert!(n.contains("mios_real"));
        assert!(!n.contains("mios_trap"));
    }

    // Fixture trees: the exit-code and diagnostic contract.
    const PIPE: &str = COMPONENT;

    fn write(dir: &Path, rel: &str, text: &str) {
        let path = dir.join(rel);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, text).unwrap();
    }

    /// A tree every module of which is named by a test, each a different way:
    /// a sibling import, a re-export shim, a file load from tests/, and a
    /// `from package import module` inside a subject suite.
    fn clean(dir: &Path) {
        write(
            dir,
            "usr/share/mios/mios.toml",
            "[refactor]\nsubmodule_roots = [\"mios_pipe/\"]\n",
        );
        write(dir, &format!("{PIPE}/server.py"), "import mios_alpha\n");
        write(dir, &format!("{PIPE}/mios_alpha.py"), "X = 1\n");
        write(
            dir,
            &format!("{PIPE}/mios_beta.py"),
            "# AI-hint: Re-export shim for mios_pipe.pkg.beta\n\
             sys.modules[__name__] = _ShimModule(__name__, \"mios_pipe.pkg.beta\")\n",
        );
        write(dir, &format!("{PIPE}/mios_pipe/__init__.py"), "");
        write(dir, &format!("{PIPE}/mios_pipe/pkg/__init__.py"), "");
        write(dir, &format!("{PIPE}/mios_pipe/pkg/beta.py"), "Y = 2\n");
        write(dir, &format!("{PIPE}/mios_pipe/pkg/gamma.py"), "Z = 3\n");
        write(dir, &format!("{PIPE}/mios_pipe/pkg/delta.py"), "W = 4\n");
        write(
            dir,
            &format!("{PIPE}/test_mios_alpha.py"),
            "import mios_alpha as A\nassert A.X == 1\n",
        );
        write(
            dir,
            &format!("{PIPE}/test_mios_subjects.py"),
            "import mios_beta\nfrom mios_pipe.pkg import (\n    delta as D,\n)\n",
        );
        write(
            dir,
            "tests/test-gamma.py",
            "P = os.path.join(ROOT, 'usr', 'lib', 'mios', 'agent-pipe', 'mios_pipe/pkg/gamma.py')\n",
        );
        write(dir, "tools/foo-bar.py", "def run(): pass\n");
        write(
            dir,
            "tools/test_foo_bar.py",
            "spec = importlib.util.spec_from_file_location('foo_bar', HERE / 'foo-bar.py')\n",
        );
        write(dir, "tools/legacy.py", "def old(): pass\n");
        write(dir, "usr/libexec/mios/config-tool.py", "pass\n");
        write(
            dir,
            BASELINE,
            "# grandfathered\ntools/legacy.py\nusr/libexec/mios/config-tool.py\n",
        );
    }

    /// The gate's exit code and everything it would print.
    fn run(dir: &Path) -> (u8, String) {
        let r = check(dir);
        let mut text = r.findings.join("\n");
        text.push('\n');
        text.push_str(&r.summary);
        (r.code(), text)
    }

    fn assert_names(dir: &Path, want: &str) {
        let (code, text) = run(dir);
        assert_eq!(code, 1, "output: {text}");
        assert!(
            text.contains(want),
            "diagnostic must contain {want:?}: {text}"
        );
    }

    #[test]
    fn every_module_named_by_a_test_exits_zero() {
        let dir = tempfile::tempdir().unwrap();
        clean(dir.path());
        let (code, text) = run(dir.path());
        assert_eq!(code, 0, "output: {text}");
        // mios_alpha, mios_beta, beta, gamma, delta; 3 test files; 1 tested, 2 grandfathered.
        assert!(
            text.contains("5 usr/lib/mios/agent-pipe module(s)"),
            "{text}"
        );
        assert!(text.contains("one of 3 unit test file(s)"), "{text}");
        assert!(
            text.contains(
                "1 tools/libexec python module(s) named by a sibling test, 2 grandfathered"
            ),
            "{text}"
        );
    }

    #[test]
    fn an_unnamed_package_module_is_named() {
        let dir = tempfile::tempdir().unwrap();
        clean(dir.path());
        write(
            dir.path(),
            &format!("{PIPE}/mios_pipe/pkg/epsilon.py"),
            "V = 5\n",
        );
        assert_names(
            dir.path(),
            "mios_pipe/pkg/epsilon.py is named by no unit test",
        );
    }

    #[test]
    fn a_test_file_named_after_the_module_is_not_evidence() {
        let dir = tempfile::tempdir().unwrap();
        clean(dir.path());
        write(dir.path(), &format!("{PIPE}/mios_omega.py"), "O = 1\n");
        // The pre-task_1006 rule: a zero-byte file with the right name passed.
        write(dir.path(), &format!("{PIPE}/test_mios_omega.py"), "");
        assert_names(dir.path(), "mios_omega.py is named by no unit test");
    }

    #[test]
    fn comments_docstrings_and_strings_of_code_do_not_count() {
        let dir = tempfile::tempdir().unwrap();
        clean(dir.path());
        write(
            dir.path(),
            &format!("{PIPE}/mios_pipe/pkg/zeta.py"),
            "pass\n",
        );
        write(
            dir.path(),
            &format!("{PIPE}/test_mios_prose.py"),
            "# import mios_pipe.pkg.zeta\n\
             \"\"\"Covers mios_pipe.pkg.zeta, honestly.\"\"\"\n\
             CODE = '''from mios_pipe.pkg import zeta'''\n",
        );
        assert_names(dir.path(), "mios_pipe/pkg/zeta.py is named by no unit test");
    }

    #[test]
    fn a_shim_carries_coverage_both_ways_and_only_while_named() {
        let dir = tempfile::tempdir().unwrap();
        clean(dir.path());
        // Drop the shim import: the shim and its target both go unnamed.
        write(
            dir.path(),
            &format!("{PIPE}/test_mios_subjects.py"),
            "from mios_pipe.pkg import delta\n",
        );
        let (code, text) = run(dir.path());
        assert_eq!(code, 1, "output: {text}");
        assert!(
            text.contains("mios_beta.py is named by no unit test"),
            "{text}"
        );
        assert!(
            text.contains("mios_pipe/pkg/beta.py is named by no unit test"),
            "{text}"
        );
    }

    #[test]
    fn a_new_untested_tool_outside_the_ledger_is_named() {
        let dir = tempfile::tempdir().unwrap();
        clean(dir.path());
        write(dir.path(), "tools/new-tool.py", "pass\n");
        assert_names(
            dir.path(),
            "untested python module not in baseline: tools/new-tool.py",
        );
    }

    #[test]
    fn a_sibling_that_never_loads_its_module_is_named() {
        let dir = tempfile::tempdir().unwrap();
        clean(dir.path());
        write(
            dir.path(),
            "tools/test_foo_bar.py",
            "def test(): assert True\n",
        );
        assert_names(
            dir.path(),
            "tools/foo-bar.py: its sibling test_foo_bar.py never names it",
        );
    }

    #[test]
    fn the_ledger_only_shrinks() {
        let dir = tempfile::tempdir().unwrap();
        clean(dir.path());
        fs::remove_file(dir.path().join("tools/legacy.py")).unwrap();
        assert_names(
            dir.path(),
            "grandfathers tools/legacy.py, which no longer exists",
        );

        let dir = tempfile::tempdir().unwrap();
        clean(dir.path());
        write(
            dir.path(),
            "usr/libexec/mios/test_config_tool.py",
            "load('config-tool.py')\n",
        );
        assert_names(
            dir.path(),
            "grandfathers usr/libexec/mios/config-tool.py, which test_config_tool.py now tests",
        );
    }

    #[test]
    fn a_missing_ledger_is_a_violation_not_a_skip() {
        let dir = tempfile::tempdir().unwrap();
        clean(dir.path());
        fs::remove_file(dir.path().join(BASELINE)).unwrap();
        assert_names(dir.path(), "python-untested-baseline.txt is missing");
    }

    #[test]
    fn missing_inputs_are_violations() {
        let dir = tempfile::tempdir().unwrap();
        clean(dir.path());
        write(
            dir.path(),
            "usr/share/mios/mios.toml",
            "[refactor]\nsubmodule_roots = []\n",
        );
        assert_names(dir.path(), "[refactor].submodule_roots lists no package");

        let dir = tempfile::tempdir().unwrap();
        clean(dir.path());
        fs::remove_dir_all(dir.path().join(PIPE)).unwrap();
        assert_names(dir.path(), "usr/lib/mios/agent-pipe is absent");
    }
}
