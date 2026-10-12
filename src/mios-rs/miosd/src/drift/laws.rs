// AI-hint: Laws and filesystem layout enforcement checks for miosd drift runner.
// AI-related: automation/98-drift-checks.sh, usr/share/mios/mios.toml, automation/lib/mios_var_closure.py, usr/share/mios/reference/var-closure-baseline.tsv

use super::audit::{self, at, files, finish, ini, read, ssot, strings};
use super::{Check, DriftCtx, Verdict};
use std::collections::{HashMap, HashSet};
use std::fs;

pub struct LawEnforcersCheck;
impl Check for LawEnforcersCheck {
    fn id(&self) -> &'static str {
        "check_law_enforcers"
    }
    fn describe(&self) -> &'static str {
        "Assert all laws in mios.toml have valid enforced_by target declarations"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        // task_980. This used to test `p.exists()` and then return
        // Pass("Law enforcers resolution validated clean") -- a claim about a
        // file it never opened. Touching ctx.root to BUILD a path is not
        // reading the tree, which is why two successive stub detectors let it
        // through. CLAUDE.md calls [laws] the canonical registry; this now
        // checks it.
        let p = ctx.root.join("usr/share/mios/mios.toml");
        let text = match fs::read_to_string(&p) {
            Ok(t) => t,
            Err(e) => return Verdict::Fail(format!("mios.toml unreadable: {}", e)),
        };
        let parsed: toml::Value = match text.parse() {
            Ok(v) => v,
            Err(e) => return Verdict::Fail(format!("mios.toml did not parse: {}", e)),
        };
        let laws = parsed
            .get("laws")
            .and_then(|l| l.get("laws"))
            .and_then(|v| v.as_array());
        // An absent or empty registry is not "no violations"; it is the law
        // list having vanished.
        let laws = match laws {
            Some(a) if !a.is_empty() => a,
            _ => return Verdict::Fail("[laws].laws is absent or empty".to_string()),
        };

        let mut bad: Vec<String> = Vec::new();
        let mut seen_slugs: HashSet<String> = HashSet::new();
        let mut file_cache: HashMap<String, Option<String>> = HashMap::new();

        for (i, law) in laws.iter().enumerate() {
            let id = law.get("id").and_then(|v| v.as_integer());
            let slug = law.get("slug").and_then(|v| v.as_str()).unwrap_or("");
            let applies = law.get("applies_to").and_then(|v| v.as_str()).unwrap_or("");
            let enforced = law
                .get("enforced_by")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let label = if slug.is_empty() {
                format!("law #{}", i + 1)
            } else {
                slug.to_string()
            };

            // Dense 1..N: a gap means a law was dropped and nobody renumbered.
            if id != Some(i as i64 + 1) {
                bad.push(format!(
                    "{}: id is {:?}, expected {} (the registry must be dense 1..N)",
                    label,
                    id,
                    i + 1
                ));
            }
            if slug.is_empty() {
                bad.push(format!("{}: no slug", label));
            } else if !seen_slugs.insert(slug.to_string()) {
                bad.push(format!("{}: duplicate slug", label));
            }
            if !matches!(applies, "bootc" | "wsl" | "both") {
                bad.push(format!(
                    "{}: applies_to is {:?}, not bootc|wsl|both",
                    label, applies
                ));
            }
            if enforced.is_empty() {
                bad.push(format!("{}: no enforced_by -- the law is advisory", label));
                continue;
            }
            // enforced_by is "<file>:<symbol>[,<symbol>...]".
            let Some((file, symbols)) = enforced.split_once(':') else {
                bad.push(format!(
                    "{}: enforced_by {:?} has no <file>:<symbol> form",
                    label, enforced
                ));
                continue;
            };
            // `process:` is a real scheme, not a malformed entry: Law 15's
            // triple-check-before-acting cannot be gated, only followed. It
            // still has to SAY something, which the emptiness test above covers.
            if file == "process" {
                continue;
            }
            let body = file_cache
                .entry(file.to_string())
                .or_insert_with(|| fs::read_to_string(ctx.root.join("automation").join(file)).ok());
            let Some(body) = body else {
                bad.push(format!(
                    "{}: enforcer file automation/{} does not exist",
                    label, file
                ));
                continue;
            };
            for sym in symbols.split(',').map(str::trim).filter(|s| !s.is_empty()) {
                if !body.contains(sym) {
                    bad.push(format!(
                        "{}: enforced_by names {} but automation/{} does not contain it",
                        label, sym, file
                    ));
                }
            }
        }

        if bad.is_empty() {
            Verdict::Pass(format!(
                "{} law(s) each resolve to an enforcer that exists",
                laws.len()
            ))
        } else {
            Verdict::Fail(bad.join("; "))
        }
    }
}

pub struct QuadletPrivilegeCheck;
impl Check for QuadletPrivilegeCheck {
    fn id(&self) -> &'static str {
        "check_quadlet_privilege"
    }
    fn describe(&self) -> &'static str {
        "Assert root quadlet privilege whitelist matches committed roster"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict((|| {
            let policy = ssot(ctx)?;
            let root = strings(&policy, "security.privileged_quadlets.root")?;
            let exempt = strings(&policy, "security.privileged_quadlets.no_group_delegate")?;
            let mut paths = files(&ctx.root, "usr/share/containers/systemd")?;
            if ctx.root.join("etc/containers/systemd").exists() {
                paths.extend(files(&ctx.root, "etc/containers/systemd")?);
            }
            let mut errors = Vec::new();
            let mut count = 0;
            for path in paths.iter().filter(|p| p.ends_with(".container")) {
                count += 1;
                let name = path.rsplit('/').next().unwrap_or(path);
                let body = read(&ctx.root, path)?;
                let user = ini(&body, "Container", "User")
                    .or_else(|| ini(&body, "Service", "User"))
                    .unwrap_or_default();
                let is_root = matches!(user.as_str(), "" | "root" | "0");
                if is_root && !root.iter().any(|s| s == name) {
                    errors.push(format!(
                        "{path}: User={user} runs as root without a roster entry"
                    ));
                }
                if !exempt.iter().any(|s| s == name) {
                    if !is_root
                        && ini(&body, "Container", "Group")
                            .or_else(|| ini(&body, "Service", "Group"))
                            .filter(|s| !s.is_empty())
                            .is_none()
                    {
                        errors.push(format!("{path}: non-root User requires Group="));
                    }
                    if ini(&body, "Service", "Delegate").as_deref() != Some("yes") {
                        errors.push(format!("{path}: missing Delegate=yes"));
                    }
                }
            }
            finish(count, errors, "Quadlet privilege roster")
        })())
    }
}

pub struct CouncilGateSSOTCheck;
impl Check for CouncilGateSSOTCheck {
    fn id(&self) -> &'static str {
        "check_council_gate_ssot"
    }
    fn describe(&self) -> &'static str {
        "Assert council gate configuration matches SSOT"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict((|| {
            let policy = ssot(ctx)?;
            let council = at(&policy, "agent_pipe.council")?;
            let paths = files(&ctx.root, "usr/lib/mios/agent-pipe")?;
            let mut code = String::new();
            let mut count = 0;
            for path in paths.iter().filter(|p| {
                p.ends_with(".py")
                    && !p.contains("/tests/")
                    && !p.rsplit('/').next().unwrap_or("").starts_with("test_")
            }) {
                count += 1;
                code.push_str(&read(&ctx.root, path)?);
                code.push('\n');
            }
            let errors = [
                "diversity_gate",
                "diversity_threshold",
                "aggregator_bypass",
                "aggregator_bypass_threshold",
            ]
            .iter()
            .filter(|key| council.get(**key).is_none() || !code.contains(**key))
            .map(|key| format!("agent_pipe.council.{key}: missing policy or source consumer"))
            .collect();
            finish(count, errors, "council policy consumer audit")
        })())
    }
}

pub struct UsrOverEtcCheck;
impl Check for UsrOverEtcCheck {
    fn id(&self) -> &'static str {
        "check_usr_over_etc"
    }
    fn describe(&self) -> &'static str {
        "Assert /usr defaults take precedence over /etc in vendor config"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict((|| {
            let paths = audit::tracked(ctx)?;
            let mut errors = Vec::new();
            for path in paths.iter().filter(|p| p.starts_with("etc/")) {
                if [
                    "etc/containers/",
                    "etc/wsl.conf",
                    "etc/wsl-distribution.conf",
                    "etc/cockpit/",
                    "etc/greenboot/",
                    "etc/mios/",
                    "etc/skel/",
                    "etc/profile.d/",
                ]
                .iter()
                .any(|p| path.starts_with(p))
                    || path.contains(".d/")
                    || path.rsplit('/').next().unwrap_or("").contains(".d")
                {
                    continue;
                }
                read(&ctx.root, path)?;
                for base in ["usr/share", "usr/lib"] {
                    let vendor = format!("{base}/{}", &path[4..]);
                    if ctx.root.join(&vendor).is_file() {
                        errors.push(format!("{path} shadows {vendor}"));
                    }
                }
            }
            finish(paths.len(), errors, "tracked /etc vendor shadow audit")
        })())
    }
}

pub struct EtcDuplicatesCheck;
impl Check for EtcDuplicatesCheck {
    fn id(&self) -> &'static str {
        "check_etc_duplicates"
    }
    fn describe(&self) -> &'static str {
        "Assert no duplicate file declarations in /etc tree"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict((|| {
            let vendor = files(&ctx.root, "usr/share/containers/systemd")?;
            let mut errors = Vec::new();
            if ctx.root.join("etc/containers/systemd").exists() {
                for path in files(&ctx.root, "etc/containers/systemd")? {
                    if ![".container", ".pod", ".network", ".volume"]
                        .iter()
                        .any(|ext| path.ends_with(ext))
                    {
                        continue;
                    }
                    let suffix = path.trim_start_matches("etc/containers/systemd/");
                    let target = format!("usr/share/containers/systemd/{suffix}");
                    if vendor.contains(&target) {
                        errors.push(format!("{path} shadows generated {target}"));
                    }
                }
            }
            finish(vendor.len(), errors, "Quadlet duplicate audit")
        })())
    }
}

pub struct NoMkdirInVarCheck;
impl Check for NoMkdirInVarCheck {
    fn id(&self) -> &'static str {
        "check_no_mkdir_in_var"
    }
    fn describe(&self) -> &'static str {
        "Assert scripts do not invoke explicit mkdir in /var"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict((|| {
            let mut paths = files(&ctx.root, "automation")?;
            paths.retain(|p| {
                !p[11..].contains('/')
                    && p.ends_with(".sh")
                    && p.as_bytes().get(11).is_some_and(u8::is_ascii_digit)
            });
            for entry in fs::read_dir(&ctx.root).map_err(|e| e.to_string())? {
                let entry = entry.map_err(|e| e.to_string())?;
                if entry.file_type().map_err(|e| e.to_string())?.is_file()
                    && entry
                        .file_name()
                        .to_string_lossy()
                        .starts_with("Containerfile")
                {
                    paths.push(entry.file_name().to_string_lossy().into_owned());
                }
            }
            let pattern =
                regex::Regex::new(r#"\bmkdir[^;&|#]*[\s'\"]/var/"#).map_err(|e| e.to_string())?;
            let mut errors = Vec::new();
            for path in &paths {
                for (line, text) in read(&ctx.root, path)?.lines().enumerate() {
                    if !text.trim_start().starts_with('#') && pattern.is_match(text) {
                        errors.push(format!(
                            "{path}:{}: imperative /var mkdir; declare tmpfiles instead",
                            line + 1
                        ));
                    }
                }
            }
            finish(paths.len(), errors, "numbered build /var mkdir audit")
        })())
    }
}

/// Port of automation/lib/mios_var_closure.py. The legacy gate and this one
/// share one exact-count ledger and both run (CI gate tier, image bake), so
/// they must measure the SAME referenced-but-unemitted set: every rule below
/// is the legacy tool's, quirks included, and the tests pin each one.
pub struct VarClosureCheck;
impl Check for VarClosureCheck {
    fn id(&self) -> &'static str {
        "check_var_closure"
    }
    fn describe(&self) -> &'static str {
        "Assert every referenced MIOS_* variable is emitted by the SSOT cascade or on the exact var-closure ledger"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict((|| {
            let emitted = closure_emitted(ctx)?;
            let (missing, scanned) = closure_referenced(&ctx.root, &emitted)?;
            closure_ratchet(ctx, &missing, scanned)
        })())
    }
}

const CLOSURE_LEDGER: &str = "usr/share/mios/reference/var-closure-baseline.tsv";
const CLOSURE_DIRECTIVES: [&str; 12] = [
    "MIOS_APPLY_CLASS",
    "MIOS_SUBSTRATE",
    "MIOS_ROOT",
    "MIOS_VENDOR_TOML",
    "MIOS_HOST_TOML",
    "MIOS_USER_TOML",
    "MIOS_VENDOR_TOML_D",
    "MIOS_HOST_TOML_D",
    "MIOS_USER_TOML_D",
    "MIOS_CONFIG_DIR",
    "MIOS_TOML_ROOT",
    "MIOS_TOML",
];
// Matched as suffixes of the root-relative path, so a copy of an emitter
// anywhere in the tree is excluded too.
const CLOSURE_EMITTERS: [&str; 11] = [
    "usr/lib/mios/userenv.sh",
    "tools/lib/userenv.sh",
    "usr/libexec/mios/system-sync-env.sh",
    "usr/share/mios/names.generated.txt",
    "usr/share/doc/mios/reference/naming-unification.md",
    "automation/lib/globals.sh",
    "automation/lib/globals.ps1",
    "tools/render-globals.py",
    "tools/render-ports.py",
    "usr/share/mios/mios.toml",
    "Justfile",
];
const CLOSURE_SUFFIXES: [&str; 11] = [
    ".container",
    ".service",
    ".timer",
    ".py",
    ".sh",
    ".toml",
    ".ps1",
    ".psm1",
    ".yaml",
    ".yml",
    ".tmpl",
];
// Substrings of the directory path AS WALKED (root included), not path
// components: "/.git" also prunes .github, "/target" also prunes targets/.
const CLOSURE_PRUNE: [&str; 8] = [
    "/.git",
    "/.venv",
    "/node_modules",
    "/target",
    "/.claude",
    "/.agents",
    "/.gemini",
    "/.system_generated",
];

/// Python's str.isspace() (what `\s` means in a str pattern): Rust's
/// White_Space plus the four ASCII separators U+001C..U+001F.
fn closure_space(c: char) -> bool {
    c.is_whitespace() || ('\u{1c}'..='\u{1f}').contains(&c)
}

/// open(..., encoding="utf-8", errors="ignore"): invalid bytes are DROPPED,
/// not replaced, so they cannot split a name the way U+FFFD would.
fn closure_decode(bytes: &[u8]) -> String {
    bytes.utf8_chunks().map(|chunk| chunk.valid()).collect()
}

/// Iterating a text-mode file: universal newlines, so a lone CR ends a line.
fn closure_lines(text: &str) -> impl Iterator<Item = &str> {
    text.split("\r\n").flat_map(|part| part.split(['\r', '\n']))
}

/// EMITTED is what the legacy reads back from `bash -c '. userenv.sh; env'`
/// with every MIOS_* scrubbed from the environment and every tier but the
/// vendor file pinned away (MIOS_HOST_TOML=/dev/null, *_D=/nonexistent).
/// userenv.sh evals `mios-resolver --emit=shell` WITHOUT --root, so the
/// referenced_names.txt pass-through never applies and a key whose value is
/// empty is not exported: declared-but-empty does not close a reference.
/// Rust calls that same projection in process instead of running bash, and
/// reads it over the vendor file alone, never the tree's etc/mios tiers.
fn closure_emitted(ctx: &DriftCtx) -> Result<HashSet<String>, String> {
    let policy = ssot(ctx)?;
    let mut merged = policy.clone();
    mios_resolver::ports::derive_ports(&mut merged);
    let shell = mios_resolver::emit_shell::emit_shell(
        &merged,
        mios_resolver::stack_offset_of(&merged),
        None,
    );
    let exports = closure_shell_exports(&shell)?;
    if !exports.iter().any(|(key, _)| key.starts_with("MIOS_")) {
        return Err(
            "mios-resolver --emit=shell produced no MIOS_* variable (resolver broken?)".into(),
        );
    }
    let mut emitted = closure_env_names(&exports)?;
    // userenv.sh's own exports after the cascade (MIOS_PG_BIND_ADDR). The
    // file must exist: without it the legacy's emitted set collapses.
    let userenv = read(&ctx.root, "usr/lib/mios/userenv.sh")?;
    let export = regex::Regex::new(r"\bexport\s+(MIOS_[A-Z0-9_]+)=").map_err(|e| e.to_string())?;
    for line in userenv.lines() {
        for found in export.captures_iter(line.split('#').next().unwrap_or("")) {
            emitted.insert(found[1].to_owned());
        }
    }
    for key in policy.as_table().ok_or("SSOT is not a table")?.keys() {
        emitted.insert(format!(
            "MIOS_{}_",
            key.to_uppercase().replace(['-', '.'], "_")
        ));
    }
    // A consumer that sources a tracked .env projection gets its names
    // without the resolver cascade.
    let assignment = regex::Regex::new(
        r"^[\s\x1C-\x1F]*(?:export[\s\x1C-\x1F]+)?(MIOS_[A-Z0-9_]+)[\s\x1C-\x1F]*=",
    )
    .map_err(|e| e.to_string())?;
    for surface in at(&policy, "laws.projection_registry.surfaces")?
        .as_array()
        .ok_or("SSOT laws.projection_registry.surfaces must be an array")?
    {
        for output in surface
            .get("output")
            .and_then(toml::Value::as_str)
            .unwrap_or("")
            .split(',')
            .map(str::trim)
            .filter(|output| output.ends_with(".env"))
        {
            let Ok(bytes) = fs::read(ctx.root.join(output)) else {
                continue;
            };
            for line in closure_lines(&closure_decode(&bytes)) {
                if let Some(found) = assignment.captures(line) {
                    emitted.insert(found[1].to_owned());
                }
            }
        }
    }
    Ok(emitted)
}

/// The names the legacy reads from `env`: it prints NAME=VALUE raw and splits
/// with str.splitlines(), so a value line that starts with MIOS_ reads as a
/// variable of its own.
fn closure_env_names(exports: &[(String, String)]) -> Result<HashSet<String>, String> {
    let name = regex::Regex::new(r"^MIOS_[A-Z0-9_]+").map_err(|e| e.to_string())?;
    let mut names = HashSet::new();
    for (key, value) in exports {
        let printed = format!("{key}={value}");
        for line in printed.split([
            '\n', '\r', '\u{b}', '\u{c}', '\u{1c}', '\u{1d}', '\u{1e}', '\u{85}', '\u{2028}',
            '\u{2029}',
        ]) {
            if !line.starts_with("MIOS_") {
                continue;
            }
            if let Some(hit) = name.find(line.split('=').next().unwrap_or(line)) {
                names.insert(hit.as_str().to_owned());
            }
        }
    }
    Ok(names)
}

/// `export NAME=VALUE` records as emit_shell writes them: VALUE is bare, or
/// single-quoted with ' spelled '"'"', and a quoted value may span lines.
fn closure_shell_exports(shell: &str) -> Result<Vec<(String, String)>, String> {
    let mut exports = Vec::new();
    let mut rest = shell;
    while !rest.is_empty() {
        let record = rest.strip_prefix("export ").ok_or_else(|| {
            format!(
                "mios-resolver --emit=shell: unexpected line {:?}",
                rest.lines().next().unwrap_or("")
            )
        })?;
        let (key, mut tail) = record
            .split_once('=')
            .ok_or("mios-resolver --emit=shell: export without '='")?;
        let mut value = String::new();
        if tail.starts_with('\'') {
            while let Some(quoted) = tail.strip_prefix('\'') {
                let end = quoted
                    .find('\'')
                    .ok_or("mios-resolver --emit=shell: unterminated quote")?;
                value.push_str(&quoted[..end]);
                tail = &quoted[end + 1..];
                match tail.strip_prefix("\"'\"") {
                    Some(next) => {
                        value.push('\'');
                        tail = next;
                    }
                    None => break,
                }
            }
        } else {
            let end = tail.find('\n').unwrap_or(tail.len());
            value.push_str(&tail[..end]);
            tail = &tail[end..];
        }
        rest = match tail.strip_prefix('\n') {
            Some(next) => next,
            None if tail.is_empty() => tail,
            None => {
                return Err(format!(
                    "mios-resolver --emit=shell: trailing text after {key}"
                ))
            }
        };
        exports.push((key.to_owned(), value));
    }
    Ok(exports)
}

/// referenced_set(): an os.walk of the tree as it is on disk (untracked files
/// included), keeping the legacy's skip rules exactly. Returns each
/// referenced-but-unemitted name with its first location, and the number of
/// files read.
fn closure_referenced(
    root: &std::path::Path,
    emitted: &HashSet<String>,
) -> Result<(std::collections::BTreeMap<String, String>, usize), String> {
    // No leading \b: VAR_MIOS_DIR and _MIOS_CHECK_ID yield MIOS_DIR and
    // MIOS_CHECK_ID, as in the legacy VAR_RE.
    let variable = regex::Regex::new(r"MIOS_[A-Z0-9_]+").map_err(|e| e.to_string())?;
    let prefixes: Vec<&str> = emitted
        .iter()
        .filter(|name| name.ends_with('_'))
        .map(String::as_str)
        .collect();
    let mut missing = std::collections::BTreeMap::new();
    let mut scanned = 0;
    let mut pending = vec![(root.to_path_buf(), String::new())];
    while let Some((directory, relative)) = pending.pop() {
        let walked = directory.to_string_lossy().replace('\\', "/");
        if CLOSURE_PRUNE.iter().any(|p| walked.contains(p)) {
            continue;
        }
        let mut dirs = Vec::new();
        let mut names = Vec::new();
        // os.walk drops an unreadable directory silently; that hides every
        // reference under it, so here it is a failure.
        for entry in
            fs::read_dir(&directory).map_err(|e| format!("{}: {e}", directory.display()))?
        {
            let entry = entry.map_err(|e| format!("{}: {e}", directory.display()))?;
            let name = entry.file_name().to_string_lossy().into_owned();
            // DirEntry.is_dir() follows a link; os.walk lists a linked
            // directory but does not descend into it.
            if fs::metadata(entry.path()).is_ok_and(|m| m.is_dir()) {
                let link = entry.file_type().is_ok_and(|t| t.is_symlink());
                dirs.push((name, link));
            } else {
                names.push(name);
            }
        }
        dirs.sort();
        names.sort();
        // A .git below the root marks a nested checkout: another repo's source.
        if !relative.is_empty()
            && (dirs.iter().any(|(n, _)| n == ".git") || names.iter().any(|n| n == ".git"))
        {
            continue;
        }
        let join = |name: &str| {
            if relative.is_empty() {
                name.to_owned()
            } else {
                format!("{relative}/{name}")
            }
        };
        for (name, link) in dirs.iter().rev() {
            if !link {
                pending.push((directory.join(name), join(name)));
            }
        }
        // The legacy `continue`s here without clearing dirs: a design doc
        // excludes its own directory's files, not the subdirectories.
        if relative.starts_with("docs/") && names.iter().any(|n| n == "_design.md") {
            continue;
        }
        // "/tests/" needs a component AFTER tests, so the files directly in
        // automation/tests are scanned; only the top-level tests/ is skipped whole.
        let test_dir =
            walked.contains("/tests/") || relative == "tests" || relative.starts_with("tests/");
        for name in &names {
            if name.starts_with("test_") || name.ends_with("_test.py") || test_dir {
                continue;
            }
            let path = join(name);
            if CLOSURE_EMITTERS.iter().any(|e| path.ends_with(e))
                || !(name == "Justfile"
                    || name == ".env.mios"
                    || CLOSURE_SUFFIXES.iter().any(|s| name.ends_with(s)))
            {
                continue;
            }
            // open() fails on a dangling link and is skipped; a FIFO would block.
            let file = directory.join(name);
            if !fs::metadata(&file).is_ok_and(|m| m.is_file()) {
                continue;
            }
            let Ok(bytes) = fs::read(&file) else {
                continue;
            };
            scanned += 1;
            for (number, line) in closure_lines(&closure_decode(&bytes)).enumerate() {
                let code = line.split('#').next().unwrap_or("");
                let code = code.split("//").next().unwrap_or("");
                for hit in variable.find_iter(code) {
                    let name = hit.as_str();
                    if CLOSURE_DIRECTIVES.contains(&name)
                        || name.ends_with('_')
                        || emitted.contains(name)
                        || prefixes.iter().any(|p| name.starts_with(p))
                        || closure_assigned(code, name)
                    {
                        continue;
                    }
                    missing
                        .entry(name.to_owned())
                        .or_insert_with(|| format!("{path}:{}", number + 1));
                }
            }
        }
    }
    Ok((missing, scanned))
}

/// re.search(rf"\b{name}\s*=", code): an assignment ANYWHERE on the line
/// exempts every occurrence on it, so `X="${X:-d}"` is not a reference. The
/// \b is here even though the scan has none: VAR_X=1 still references X.
fn closure_assigned(code: &str, name: &str) -> bool {
    // Every start offset, overlaps included, as a regex search tries them.
    let mut from = 0;
    while let Some(offset) = code[from..].find(name) {
        let at = from + offset;
        let bounded = code[..at]
            .chars()
            .next_back()
            .is_none_or(|c| !(c.is_alphanumeric() || c == '_'));
        if bounded
            && code[at + name.len()..]
                .trim_start_matches(closure_space)
                .starts_with('=')
        {
            return true;
        }
        // name starts with the one-byte 'M', so at + 1 is a char boundary.
        from = at + 1;
    }
    false
}

/// check_var_closure's verdict: the ledger names exactly the found set and
/// #!ceiling equals both its row count and the found count.
fn closure_ratchet(
    ctx: &DriftCtx,
    missing: &std::collections::BTreeMap<String, String>,
    scanned: usize,
) -> audit::Audit {
    let ledger = read(&ctx.root, CLOSURE_LEDGER)?;
    // sed -n 's/^#!ceiling \([0-9]*\).*/\1/p' | head -1: the FIRST such line
    // decides, and its leading digits are the number.
    let digits: String = ledger
        .lines()
        .find_map(|line| line.strip_prefix("#!ceiling "))
        .unwrap_or("")
        .chars()
        .take_while(char::is_ascii_digit)
        .collect();
    let ceiling: usize = digits.parse().map_err(|_| {
        format!(
            "{CLOSURE_LEDGER}: carries no #!ceiling <n> -- an unbounded ledger is not a ratchet"
        )
    })?;
    let rows: Vec<&str> = ledger
        .lines()
        .filter(|line| line.starts_with("MIOS_"))
        .map(|line| line.split('\t').next().unwrap_or(line))
        .collect();
    let declared: HashSet<&str> = rows.iter().copied().collect();
    let mut errors = Vec::new();
    for (name, source) in missing {
        if !declared.contains(name.as_str()) {
            errors.push(format!(
                "{source}: {name} referenced but not emitted or on closure ledger -- emit it from the SSOT cascade or stop referencing it"
            ));
        }
    }
    let mut stale: Vec<&&str> = declared
        .iter()
        .filter(|name| !missing.contains_key(**name))
        .collect();
    stale.sort();
    for name in stale {
        errors.push(format!(
            "{name}: stale closure ledger entry; remove it and lower ceiling in {CLOSURE_LEDGER}"
        ));
    }
    if rows.len() != ceiling {
        errors.push(format!(
            "{CLOSURE_LEDGER}: holds {} name row(s) but declares #!ceiling {ceiling}",
            rows.len()
        ));
    }
    if missing.len() != ceiling {
        errors.push(format!(
            "var closure exact ratchet: found {} referenced-but-unemitted name(s) against #!ceiling {ceiling}",
            missing.len()
        ));
    }
    finish(scanned, errors, "MIOS_* variable closure").map(|done| {
        format!(
            "{done}; {} referenced-but-unemitted name(s), all on the ledger (ceiling {ceiling})",
            missing.len()
        )
    })
}

#[cfg(test)]
mod var_closure_tests {
    use super::*;
    use std::path::Path;

    type TestResult = Result<(), Box<dyn std::error::Error>>;

    fn put(root: &Path, path: &str, body: impl AsRef<[u8]>) -> std::io::Result<()> {
        let path = root.join(path);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(path, body)
    }

    /// One fixture per legacy rule, each with the side that IS a reference
    /// and the side that is not. `public_host` toggles the empty-value rule.
    fn fixture(root: &Path, public_host: &str) -> std::io::Result<()> {
        put(
            root,
            "usr/share/mios/mios.toml",
            format!(
                "[laws.projection_registry]\nsurfaces = [\n  {{ output = \"etc/mios/fixture.env\" }},\n  {{ output = \"usr/share/mios/absent.env, etc/mios/second.env\" }},\n]\n\n[identity]\nusername = \"mios\"\n\n[portal]\npublic_host = \"{public_host}\"\n"
            ),
        )?;
        put(
            root,
            "usr/lib/mios/userenv.sh",
            "eval \"$(mios-resolver --emit=shell)\"\nexport MIOS_USERENV_ONLY=\"1\"\n# export MIOS_COMMENTED_EXPORT=1\n",
        )?;
        put(
            root,
            "etc/mios/fixture.env",
            "export MIOS_FROM_ENV=1\nMIOS_SECOND_FROM_ENV = 2\n",
        )?;
        put(root, "etc/mios/second.env", "MIOS_FROM_SECOND_SURFACE=1\n")?;
        put(
            root,
            "automation/rules.sh",
            concat!(
                "VAR_MIOS_DIR=\"${TARGET_ROOT}/var/lib/mios\"\n",
                "export MIOS_AGENT_DEFAULT=\"${MIOS_AGENT_DEFAULT:-hermes}\"\n",
                "MIOS_BUILT=${MIOS_BUILT}\n",
                "value=\"${MIOS_READ_ONLY:-}\"\n",
                "echo \"$MIOS_PG_BIND_ADDR $MIOS_USERENV_ONLY $MIOS_COMMENTED_EXPORT\"\n",
                "echo \"$MIOS_FROM_ENV $MIOS_SECOND_FROM_ENV $MIOS_FROM_SECOND_SURFACE\"\n",
                "echo \"$MIOS_IDENTITY_ANYTHING $MIOS_ROOT $MIOS_PUBLIC_HOST $MIOS_TRAILING_\"\n",
                "curl \"http://example.invalid/$MIOS_AFTER_URL\"\n",
                "echo \"$MIOS_BEFORE_HASH\" # $MIOS_IN_HASH_COMMENT\n",
            ),
        )?;
        put(
            root,
            "automation/tests/test-97-fixture.sh",
            "run --x ${MIOS_FIXTURE_OK:-d}\n",
        )?;
        put(
            root,
            "automation/tests/deeper/case.sh",
            "echo ${MIOS_NESTED_TESTS_DIR}\n",
        )?;
        put(root, "tests/top.sh", "echo $MIOS_TOP_TESTS_DIR\n")?;
        put(root, "automation/test_unit.py", "MIOS_TEST_PREFIXED\n")?;
        put(root, "automation/unit_test.py", "MIOS_TEST_SUFFIXED\n")?;
        put(
            root,
            ".github/workflows/ci.yml",
            "x: ${{ env.MIOS_IN_GITHUB }}\n",
        )?;
        put(root, "targets/build.sh", "echo $MIOS_IN_TARGETS\n")?;
        put(root, "vendor/nested/.git", "gitdir: elsewhere\n")?;
        put(root, "vendor/nested/x.sh", "echo $MIOS_NESTED_REPO\n")?;
        put(root, "docs/design/_design.md", "design\n")?;
        put(root, "docs/design/note.py", "MIOS_DESIGN_NOTE\n")?;
        put(root, "docs/design/deeper/more.py", "MIOS_DESIGN_DEEPER\n")?;
        put(root, "docs/_design.md", "design\n")?;
        put(root, "docs/top.py", "MIOS_DOCS_TOP\n")?;
        put(root, "notes/README.md", "$MIOS_IN_MARKDOWN\n")?;
        put(
            root,
            "copy/usr/lib/mios/userenv.sh",
            "echo $MIOS_IN_EMITTER_COPY\n",
        )?;
        put(root, "Justfile", "x := env('MIOS_IN_JUSTFILE')\n")?;
        put(root, ".env.mios", "MIOS_DOTENV_SET=$MIOS_DOTENV_SOURCE\n")?;
        put(
            root,
            "automation/cr.sh",
            "a=1\r# $MIOS_CR_COMMENT\rb=$MIOS_AFTER_CR\r",
        )?;
        put(root, "automation/bytes.sh", b"x=$MIOS_SPL\xffIT\n")?;
        Ok(())
    }

    fn expected(public_host: &str) -> Vec<&'static str> {
        let mut names = vec![
            "MIOS_AFTER_CR",
            "MIOS_BEFORE_HASH",
            "MIOS_COMMENTED_EXPORT",
            "MIOS_DESIGN_DEEPER",
            "MIOS_DIR",
            "MIOS_DOCS_TOP",
            "MIOS_DOTENV_SOURCE",
            "MIOS_FIXTURE_OK",
            "MIOS_READ_ONLY",
            "MIOS_SPLIT",
        ];
        if public_host.is_empty() {
            names.push("MIOS_PUBLIC_HOST");
        }
        names.sort_unstable();
        names
    }

    fn ledger(root: &Path, names: &[&str], ceiling: &str) -> std::io::Result<()> {
        let rows: String = names.iter().map(|n| format!("{n}\tsample:1\n")).collect();
        put(
            root,
            CLOSURE_LEDGER,
            format!("# header\n#!ceiling {ceiling}\n# name\tsample location\n{rows}"),
        )
    }

    fn found(root: &Path) -> Result<Vec<String>, Box<dyn std::error::Error>> {
        let ctx = DriftCtx::new(root.into(), false);
        let emitted = closure_emitted(&ctx)?;
        Ok(closure_referenced(root, &emitted)?.0.into_keys().collect())
    }

    #[test]
    fn closure_set_follows_every_legacy_rule() -> TestResult {
        for public_host in ["", "portal.example.invalid"] {
            let temp = tempfile::tempdir()?;
            fixture(temp.path(), public_host)?;
            assert_eq!(
                found(temp.path())?,
                expected(public_host),
                "public_host={public_host:?}"
            );
        }
        Ok(())
    }

    /// open() follows a linked file; os.walk lists a linked directory but does
    /// not descend into it; a dangling link is skipped.
    #[cfg(unix)]
    #[test]
    fn closure_links_follow_files_not_directories() -> TestResult {
        let temp = tempfile::tempdir()?;
        let outside = tempfile::tempdir()?;
        fixture(temp.path(), "set")?;
        put(outside.path(), "x.sh", "echo $MIOS_THROUGH_DIR_LINK\n")?;
        put(outside.path(), "y.sh", "echo $MIOS_THROUGH_FILE_LINK\n")?;
        std::os::unix::fs::symlink(outside.path(), temp.path().join("linked"))?;
        std::os::unix::fs::symlink(
            outside.path().join("y.sh"),
            temp.path().join("automation/linked.sh"),
        )?;
        std::os::unix::fs::symlink(
            "/nonexistent/zz.sh",
            temp.path().join("automation/dangling.sh"),
        )?;
        let mut want = expected("set");
        want.push("MIOS_THROUGH_FILE_LINK");
        want.sort_unstable();
        assert_eq!(found(temp.path())?, want);
        Ok(())
    }

    #[test]
    fn closure_exact_ledger_passes_and_every_drift_fails() -> TestResult {
        let temp = tempfile::tempdir()?;
        let root = temp.path();
        fixture(root, "")?;
        let want = expected("");
        let count = want.len().to_string();
        let run = || VarClosureCheck.run(&DriftCtx::new(root.into(), false));

        ledger(root, &want, &count)?;
        let verdict = run();
        assert!(
            matches!(verdict, Verdict::Pass(ref m) if m.contains("11 referenced-but-unemitted")),
            "{verdict}"
        );

        ledger(root, &want[1..], &(want.len() - 1).to_string())?;
        assert!(matches!(run(), Verdict::Fail(ref m)
            if m.contains("automation/cr.sh:3: MIOS_AFTER_CR referenced but not emitted")));

        let mut extra = want.clone();
        extra.push("MIOS_GONE");
        ledger(root, &extra, &extra.len().to_string())?;
        assert!(
            matches!(run(), Verdict::Fail(ref m) if m.contains("MIOS_GONE: stale closure ledger entry"))
        );

        ledger(root, &want, "12")?;
        assert!(matches!(run(), Verdict::Fail(ref m)
            if m.contains("holds 11 name row(s) but declares #!ceiling 12") && m.contains("found 11")));

        let mut doubled = want.clone();
        doubled.push(want[0]);
        ledger(root, &doubled, &count)?;
        assert!(matches!(run(), Verdict::Fail(ref m) if m.contains("holds 12 name row(s)")));

        ledger(root, &want, "none")?;
        assert!(matches!(run(), Verdict::Fail(ref m) if m.contains("carries no #!ceiling")));
        Ok(())
    }

    #[test]
    fn closure_fails_closed_without_subjects_or_emitter() -> TestResult {
        let temp = tempfile::tempdir()?;
        let root = temp.path();
        fixture(root, "")?;
        for dir in [
            "automation",
            "docs",
            "notes",
            "tests",
            "targets",
            "vendor",
            "copy",
            ".github",
        ] {
            fs::remove_dir_all(root.join(dir))?;
        }
        fs::remove_file(root.join(".env.mios"))?;
        ledger(root, &[], "0")?;
        let run = || VarClosureCheck.run(&DriftCtx::new(root.into(), false));
        let verdict = run();
        assert!(
            matches!(verdict, Verdict::Fail(ref m) if m.contains("no subjects examined")),
            "{verdict}"
        );

        put(root, "automation/ok.sh", "echo $MIOS_IDENTITY_USERNAME\n")?;
        let verdict = run();
        assert!(matches!(verdict, Verdict::Pass(_)), "{verdict}");
        fs::remove_file(root.join("usr/lib/mios/userenv.sh"))?;
        assert!(matches!(run(), Verdict::Fail(ref m) if m.contains("usr/lib/mios/userenv.sh")));
        put(root, "usr/lib/mios/userenv.sh", "")?;
        fs::remove_file(root.join(CLOSURE_LEDGER))?;
        assert!(matches!(run(), Verdict::Fail(ref m) if m.contains("var-closure-baseline.tsv")));
        Ok(())
    }

    #[test]
    fn closure_reads_shell_exports_as_env_prints_them() -> TestResult {
        let exports = closure_shell_exports(
            "export A=plain\nexport MIOS_B='it'\"'\"'s\nMIOS_HIDDEN=1 x'\nexport MIOS_C=''\n",
        )?;
        assert_eq!(
            exports,
            [
                ("A".to_owned(), "plain".to_owned()),
                ("MIOS_B".to_owned(), "it's\nMIOS_HIDDEN=1 x".to_owned()),
                ("MIOS_C".to_owned(), String::new()),
            ]
        );
        let mut names: Vec<_> = closure_env_names(&exports)?.into_iter().collect();
        names.sort();
        assert_eq!(names, ["MIOS_B", "MIOS_C", "MIOS_HIDDEN"]);
        assert!(closure_shell_exports("export A='open\n").is_err());
        assert!(closure_shell_exports("A=1\n").is_err());
        Ok(())
    }

    #[test]
    fn closure_assignment_rule_needs_a_word_boundary() {
        assert!(closure_assigned("export X MIOS_A=1", "MIOS_A"));
        assert!(closure_assigned("MIOS_A =\"${MIOS_A:-d}\"", "MIOS_A"));
        assert!(!closure_assigned("VAR_MIOS_A=1", "MIOS_A"));
        assert!(!closure_assigned("MIOS_AB=1 $MIOS_A", "MIOS_A"));
        assert!(!closure_assigned("x=\"${MIOS_A:-}\"", "MIOS_A"));
    }
}
