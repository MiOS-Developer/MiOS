// AI-hint: Convergence and cross-subsystem consistency checks for miosd drift runner.
// AI-related: usr/share/mios/mios.toml, tools/native/mios-gen/src/pod_quadlets.rs, usr/lib/mios/agent-pipe/test_mios_router_parity.py, usr/lib/mios/agent-pipe/tests/router_corpus.json

use super::audit::{self, Audit};
use super::{Check, DriftCtx, Verdict};
use regex::Regex;
use std::collections::BTreeSet;
use std::fs;
use std::process::Command;

pub struct ConvergeSSOTCheck;
impl Check for ConvergeSSOTCheck {
    fn id(&self) -> &'static str {
        "check_converge_ssot"
    }
    fn describe(&self) -> &'static str {
        "Assert system convergence scripts agree with SSOT model"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict(converge(ctx))
    }
}

pub struct GuacamoleConsistencyCheck;
impl Check for GuacamoleConsistencyCheck {
    fn id(&self) -> &'static str {
        "check_guacamole_consistency"
    }
    fn describe(&self) -> &'static str {
        "Assert guacamole configuration matches SSOT"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        super::audit::native(ctx, "mios-gen", &["render-desktop", "--check"])
    }
}

pub struct RouterParityCheck;
impl Check for RouterParityCheck {
    fn id(&self) -> &'static str {
        "check_router_parity"
    }
    fn describe(&self) -> &'static str {
        "Assert model router configuration matches SSOT"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict(router(ctx))
    }
}

const SSOT: &str = "usr/share/mios/mios.toml";
// The selector the ONE heavy lane's engine overlay names (mios-gen pod_quadlets).
const HEAVY_ENGINE: &str = "ai.heavy_engine";

/// 1-based line of `key =` inside `[section]` (else the header) for path:line diagnostics.
fn ssot_line(text: &str, section: &str, key: &str) -> usize {
    let (mut active, mut header) = (false, 0);
    for (index, raw) in text.lines().enumerate() {
        let line = raw.split('#').next().unwrap_or("").trim();
        if line.starts_with('[') {
            active = line.trim_matches(|c| c == '[' || c == ']').trim() == section;
            if active && header == 0 {
                header = index + 1;
            }
        } else if active
            && line
                .strip_prefix(key)
                .is_some_and(|rest| rest.trim_start().starts_with('='))
        {
            return index + 1;
        }
    }
    header.max(1)
}

fn field<'a>(
    doc: &'a toml::Value,
    text: &str,
    section: &str,
    key: &str,
) -> Result<(&'a toml::Value, String), String> {
    let at = format!("{SSOT}:{}", ssot_line(text, section, key));
    let value = audit::at(doc, &format!("{section}.{key}"))
        .map_err(|e| format!("{at}: {e}; declare it"))?;
    Ok((value, at))
}

// Every value used to come from ${MIOS_CONV_*:-literal}, which nothing exports, so the
// legacy check graded its own drifted defaults. Read the SSOT only; a missing key fails.
fn converge(ctx: &DriftCtx) -> Audit {
    let text = audit::read(&ctx.root, SSOT)?;
    let doc: toml::Value = text.parse().map_err(|e| format!("{SSOT}: {e}"))?;
    let mut errors = Vec::new();
    let (engine, engine_at) = field(&doc, &text, "ai", "heavy_engine")?;
    let engine = engine
        .as_str()
        .map(str::trim)
        .filter(|e| !e.is_empty())
        .ok_or_else(|| {
            format!("{engine_at}: [ai].heavy_engine must name the heavy lane's engine")
        })?;
    let lanes = heavy_engine_lanes(&doc, engine, &engine_at, &mut errors);
    let (lora, lora_at) = field(&doc, &text, "converge.inference", "vllm_allow_runtime_lora")?;
    let lora = lora.as_bool().ok_or_else(|| {
        format!("{lora_at}: [converge.inference].vllm_allow_runtime_lora must be a boolean")
    })?;
    if lora && engine != "vllm" {
        errors.push(format!("{lora_at}: [converge.inference].vllm_allow_runtime_lora = true, but runtime LoRA is a vLLM feature and [ai].heavy_engine = {engine:?} ({engine_at}); select \"vllm\" or turn runtime LoRA off"));
    }
    let (dir, at) = field(&doc, &text, "converge.memory", "cold_storage_dir")?;
    let dir = dir
        .as_str()
        .ok_or_else(|| format!("{at}: [converge.memory].cold_storage_dir must be a path string"))?;
    if dir.contains("/tenants/") {
        errors.push(format!("{at}: [converge.memory].cold_storage_dir {dir:?} sits inside a CephFS tenants mount; keep cold archives outside /tenants/"));
    }
    let (days, at) = field(&doc, &text, "converge.memory", "cold_retention_days")?;
    if !days.as_integer().is_some_and(|d| d >= 1) {
        errors.push(format!(
            "{at}: [converge.memory].cold_retention_days must be an integer >= 1, got {days}"
        ));
    }
    let (level, at) = field(&doc, &text, "converge.memory", "cold_zstd_level")?;
    if !level.as_integer().is_some_and(|l| (1..=19).contains(&l)) {
        errors.push(format!(
            "{at}: [converge.memory].cold_zstd_level must be an integer 1..19, got {level}"
        ));
    }
    let (vec, vec_at) = field(&doc, &text, "converge.memory", "sqlite_vec_enable")?;
    let vec = vec.as_bool().ok_or_else(|| {
        format!("{vec_at}: [converge.memory].sqlite_vec_enable must be a boolean")
    })?;
    let mut subjects = 6 + lanes;
    if vec {
        subjects += sqlite_vec_installed(ctx, &doc, &vec_at, &mut errors)?;
    }
    audit::finish(
        subjects,
        errors,
        "[converge] SSOT bounds and the tree surfaces they govern",
    )
}

/// The heavy lane is ONE unit whose engine is an overlay of its spec, selected by
/// [ai].heavy_engine. The selector must name an overlay of every spec that reads it,
/// and at least one spec must read it, or the key selects nothing. Returns the
/// number of specs examined.
fn heavy_engine_lanes(
    doc: &toml::Value,
    engine: &str,
    engine_at: &str,
    errors: &mut Vec<String>,
) -> usize {
    let mut lanes = 0;
    for kind in ["containers", "images"] {
        for (name, spec) in doc
            .get(kind)
            .and_then(toml::Value::as_table)
            .into_iter()
            .flatten()
        {
            let Some(engines) = spec.get("engine").and_then(toml::Value::as_table) else {
                continue;
            };
            if engines.get("select").and_then(toml::Value::as_str) != Some(HEAVY_ENGINE) {
                continue;
            }
            lanes += 1;
            if !engines.get(engine).is_some_and(toml::Value::is_table) {
                let section = format!("{kind}.{name}.engine");
                let mut declared: Vec<&str> = engines
                    .iter()
                    .filter(|(_, overlay)| overlay.is_table())
                    .map(|(key, _)| key.as_str())
                    .collect();
                declared.sort_unstable();
                errors.push(format!("{engine_at}: [ai].heavy_engine = {engine:?} names no overlay of [{section}] (declared: {}); select one of those or declare [{section}.{engine}]", declared.join(", ")));
            }
        }
    }
    if lanes == 0 {
        errors.push(format!("{engine_at}: [ai].heavy_engine selects nothing: no [containers.*.engine] or [images.*.engine] table has select = \"{HEAVY_ENGINE}\""));
    }
    lanes
}

/// PEP 503-normalised project name a requirements.txt line declares, if any.
fn requirement(line: &str) -> Option<String> {
    let line = line.split('#').next()?.trim();
    if line.starts_with('-') {
        return None;
    }
    let name: String = line
        .chars()
        .take_while(|c| c.is_ascii_alphanumeric() || matches!(c, '-' | '_' | '.'))
        .collect();
    let mut out = String::new();
    for c in name.to_ascii_lowercase().chars() {
        if !matches!(c, '-' | '_' | '.') {
            out.push(c);
        } else if !out.ends_with('-') {
            out.push('-');
        }
    }
    (!out.is_empty()).then_some(out)
}

/// The legacy check imported sqlite_vec on the HOST running the gate, which says nothing
/// about the image. The tree property: a package set the image installs declares it --
/// a requirements file phase 72 pip-installs (automation/72-hermes-agent.sh reads exactly
/// [packages.ai].python_requirements) or the Fedora RPM in an enabled [packages.*].pkgs.
fn sqlite_vec_installed(
    ctx: &DriftCtx,
    doc: &toml::Value,
    at: &str,
    errors: &mut Vec<String>,
) -> Result<usize, String> {
    let (mut examined, mut found) = (0, false);
    for file in audit::strings(doc, "packages.ai.python_requirements")? {
        examined += 1;
        found |= audit::read(&ctx.root, file.trim_start_matches('/'))?
            .lines()
            .any(|line| requirement(line).as_deref() == Some("sqlite-vec"));
    }
    let packages = audit::at(doc, "packages")?
        .as_table()
        .ok_or("SSOT packages must be a table")?;
    for section in packages.values().filter_map(toml::Value::as_table) {
        if section.get("enable").and_then(toml::Value::as_bool) == Some(false) {
            continue;
        }
        examined += 1;
        found |= section
            .get("pkgs")
            .and_then(toml::Value::as_array)
            .is_some_and(|pkgs| {
                pkgs.iter()
                    .any(|p| p.as_str() == Some("python3-sqlite-vec"))
            });
    }
    if !found {
        errors.push(format!("{at}: [converge.memory].sqlite_vec_enable = true but no package set the image installs declares sqlite_vec; add sqlite-vec to a [packages.ai].python_requirements file or python3-sqlite-vec to an enabled [packages.*].pkgs"));
    }
    Ok(examined)
}

const PARITY_TEST: &str = "usr/lib/mios/agent-pipe/test_mios_router_parity.py";
const CORPUS: &str = "usr/lib/mios/agent-pipe/tests/router_corpus.json";
const SERVER: &str = "usr/lib/mios/agent-pipe/server.py";
const ROUTING_PACKAGE: &str = "usr/lib/mios/agent-pipe/mios_pipe";

fn router(ctx: &DriftCtx) -> Audit {
    let missing: Vec<String> = [PARITY_TEST, CORPUS]
        .iter()
        .filter(|p| !ctx.root.join(p).is_file())
        .map(|p| format!("{p}:1: router parity input is missing; restore it"))
        .collect();
    if !missing.is_empty() {
        return Err(missing.join("\n"));
    }
    let (rows, intents) = corpus_intents(ctx)?;
    let mut errors = Vec::new();
    if let Err(e) = parity(ctx) {
        errors.push(e);
    }
    let scanned = intent_coverage(ctx, &intents, &mut errors)?;
    audit::finish(
        rows + scanned,
        errors,
        "router Stage-2 parity (corpus rows and routing sources)",
    )
}

fn corpus_intents(ctx: &DriftCtx) -> Result<(usize, BTreeSet<String>), String> {
    let rows: Vec<serde_json::Value> = serde_json::from_str(&audit::read(&ctx.root, CORPUS)?)
        .map_err(|e| {
            format!(
                "{CORPUS}:{}: not a JSON array of corpus rows: {e}",
                e.line()
            )
        })?;
    if rows.is_empty() {
        return Err(format!(
            "{CORPUS}:1: corpus has no rows, so router parity is untested; add rows"
        ));
    }
    let mut intents = BTreeSet::new();
    for (index, row) in rows.iter().enumerate() {
        if !row.is_object() {
            return Err(format!("{CORPUS}:1: row {index} is not an object"));
        }
        match row.get("input").and_then(|input| input.get("intent")) {
            Some(serde_json::Value::String(intent)) if !intent.is_empty() => {
                intents.insert(intent.trim().to_lowercase());
            }
            None | Some(serde_json::Value::Null | serde_json::Value::String(_)) => {}
            Some(other) => {
                return Err(format!(
                    "{CORPUS}:1: row {index} input.intent {other} is not a string"
                ))
            }
        }
    }
    Ok((rows.len(), intents))
}

/// Running the agent-pipe's own parity test is legitimate: the subject IS Python behaviour.
/// Hermetic: the SSOT interpreter with -E -s -B (no PYTHON* env, no user site, no bytecode
/// written into the tree), a cleared environment, and a scratch cwd/HOME/TMPDIR. The
/// script's own directory stays on sys.path because that is how it imports mios_pipe.
fn parity(ctx: &DriftCtx) -> Result<(), String> {
    let config = audit::ssot(ctx)?;
    let python = audit::at(&config, "drift.lint.python")?.as_str().filter(|s| !s.is_empty())
        .ok_or("SSOT drift.lint.python is empty; name the interpreter that runs the router parity test")?.to_owned();
    let stamp = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or_default();
    let scratch = std::env::temp_dir().join(format!(
        "miosd-router-parity-{}-{stamp}",
        std::process::id()
    ));
    fs::create_dir_all(&scratch).map_err(|e| format!("{}: {e}", scratch.display()))?;
    let mut command = Command::new(&python);
    command
        .args(["-E", "-s", "-B"])
        .arg(ctx.root.join(PARITY_TEST))
        .current_dir(&scratch)
        .env_clear()
        .env("HOME", &scratch)
        .env("TMPDIR", &scratch);
    for name in ["PATH", "SYSTEMROOT"] {
        if let Some(value) = std::env::var_os(name) {
            command.env(name, value);
        }
    }
    let output = command.output();
    let _ = fs::remove_dir_all(&scratch);
    let output = output.map_err(|e| format!("{PARITY_TEST}:1: cannot run SSOT drift.lint.python {python:?}: {e}; install it or correct [drift.lint].python"))?;
    if output.status.success() {
        return Ok(());
    }
    let log = format!(
        "{}{}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let detail: Vec<&str> = log
        .lines()
        .filter(|line| !line.starts_with("[PASS]") && !line.trim().is_empty())
        .collect();
    Err(format!("{PARITY_TEST}:1: router parity test {} on {CORPUS}; the Stage-2 router and the server.py intent cascade disagree -- fix the router or the corpus row:\n    {}", output.status, detail.join("\n    ")))
}

/// Port of tools/drift-checks.py check_router_intent_coverage: every `intent == "x"` branch
/// in server.py or mios_pipe/**/*.py must have a corpus row, or parity never exercises it.
fn intent_coverage(
    ctx: &DriftCtx,
    intents: &BTreeSet<String>,
    errors: &mut Vec<String>,
) -> Result<usize, String> {
    let branch = Regex::new(
        r#"(?:intent\s*==|get\s*\(\s*["']intent["']\s*\)\s*==)\s*["']([a-zA-Z0-9_]+)["']"#,
    )
    .map_err(|e| e.to_string())?;
    let mut sources = vec![SERVER.to_owned()];
    sources.extend(
        audit::files(&ctx.root, ROUTING_PACKAGE)?
            .into_iter()
            .filter(|p| p.ends_with(".py")),
    );
    let mut scanned = 0;
    for path in sources {
        if path.rsplit('/').next().unwrap_or("").contains("test_") {
            continue;
        }
        scanned += 1;
        // Unlike the legacy scan, an unreadable source fails instead of being skipped.
        let text = audit::read(&ctx.root, &path)?;
        for capture in branch.captures_iter(&text) {
            let (Some(whole), Some(intent)) = (capture.get(0), capture.get(1)) else {
                continue;
            };
            let intent = intent.as_str().to_lowercase();
            if !intents.contains(&intent) {
                let line = text[..whole.start()].matches('\n').count() + 1;
                errors.push(format!("{path}:{line}: intent == {intent:?} branch is not represented in {CORPUS}; add a corpus row whose input.intent is {intent:?}"));
            }
        }
    }
    Ok(scanned)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn write(
        root: &std::path::Path,
        rel: &str,
        body: &str,
    ) -> Result<(), Box<dyn std::error::Error>> {
        let path = root.join(rel);
        fs::create_dir_all(path.parent().ok_or("no parent")?)?;
        fs::write(path, body)?;
        Ok(())
    }

    const LANE: &str = "[containers.mios-llm-heavy.engine]\nselect = \"ai.heavy_engine\"\n[containers.mios-llm-heavy.engine.vllm.Container]\nImage = \"v\"\n[containers.mios-llm-heavy.engine.sglang.Container]\nImage = \"s\"\n";
    const CONVERGE: &str = "[ai]\nheavy_engine = \"vllm\"\n[converge.inference]\nvllm_allow_runtime_lora = false\n[converge.memory]\nsqlite_vec_enable = false\ncold_storage_dir = \"/var/lib/mios/history/\"\ncold_retention_days = 90\ncold_zstd_level = 10\n";

    #[test]
    fn converge_reads_ssot_bounds_and_fails_on_missing_or_illegal_values(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let temp = tempfile::tempdir()?;
        let root = temp.path();
        let ctx = DriftCtx::new(root.into(), false);
        assert!(converge(&ctx).is_err());
        write(root, SSOT, "[other]\nx = 1\n")?;
        assert!(converge(&ctx).is_err_and(|e| e.contains("ai.heavy_engine is missing")));
        write(root, SSOT, &format!("{CONVERGE}{LANE}"))?;
        assert!(converge(&ctx).is_ok_and(|m| m.contains("7 subject")));
        write(
            root,
            SSOT,
            &format!(
                "{}{LANE}",
                CONVERGE.replace("/var/lib/mios/history/", "/mnt/ceph/tenants/a/")
            ),
        )?;
        assert!(converge(&ctx).is_err_and(|e| e
            .contains("mios.toml:7: [converge.memory].cold_storage_dir")
            && e.contains("tenants mount")));
        write(
            root,
            SSOT,
            &format!("{}{LANE}", CONVERGE.replace("= 90", "= 0")),
        )?;
        assert!(converge(&ctx)
            .is_err_and(|e| e.contains("mios.toml:8:") && e.contains("integer >= 1, got 0")));
        write(
            root,
            SSOT,
            &format!("{}{LANE}", CONVERGE.replace("= 90", "= \"90\"")),
        )?;
        assert!(converge(&ctx).is_err_and(|e| e.contains("cold_retention_days must be an integer")));
        write(
            root,
            SSOT,
            &format!("{}{LANE}", CONVERGE.replace("= 10", "= 20")),
        )?;
        assert!(converge(&ctx)
            .is_err_and(|e| e.contains("mios.toml:9:") && e.contains("1..19, got 20")));
        write(
            root,
            SSOT,
            &format!(
                "{}{LANE}",
                CONVERGE.replace("runtime_lora = false", "runtime_lora = \"no\"")
            ),
        )?;
        assert!(converge(&ctx).is_err_and(|e| e.contains("must be a boolean")));
        Ok(())
    }

    #[test]
    fn the_heavy_engine_must_select_a_declared_overlay_and_bind_runtime_lora(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let temp = tempfile::tempdir()?;
        let root = temp.path();
        let ctx = DriftCtx::new(root.into(), false);
        // Either declared engine passes; the selector reads both overlays.
        let sglang = CONVERGE.replace("\"vllm\"", "\"sglang\"");
        write(root, SSOT, &format!("{sglang}{LANE}"))?;
        assert!(converge(&ctx).is_ok());
        // An engine no overlay declares fails, naming the ones that exist.
        write(
            root,
            SSOT,
            &format!("{}{LANE}", CONVERGE.replace("\"vllm\"", "\"tgi\"")),
        )?;
        assert!(converge(&ctx)
            .is_err_and(|e| e.contains("mios.toml:2: [ai].heavy_engine = \"tgi\"")
                && e.contains("[containers.mios-llm-heavy.engine] (declared: sglang, vllm)")));
        // A selector no lane reads is a dead key.
        write(root, SSOT, CONVERGE)?;
        assert!(converge(&ctx).is_err_and(|e| e.contains("[ai].heavy_engine selects nothing")));
        // Runtime LoRA is vLLM-only: it passes on vllm and fails on sglang.
        let lora = CONVERGE.replace("runtime_lora = false", "runtime_lora = true");
        write(root, SSOT, &format!("{lora}{LANE}"))?;
        assert!(converge(&ctx).is_ok());
        let lora = lora.replace("\"vllm\"", "\"sglang\"");
        write(root, SSOT, &format!("{lora}{LANE}"))?;
        assert!(converge(&ctx).is_err_and(|e| e
            .contains("mios.toml:4: [converge.inference].vllm_allow_runtime_lora = true")
            && e.contains("[ai].heavy_engine = \"sglang\"")));
        Ok(())
    }

    #[test]
    fn sqlite_vec_must_be_declared_by_an_installed_package_set(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let temp = tempfile::tempdir()?;
        let root = temp.path();
        let ctx = DriftCtx::new(root.into(), false);
        let config = format!(
            "{}{LANE}",
            CONVERGE.replace("sqlite_vec_enable = false", "sqlite_vec_enable = true")
        );
        write(root, SSOT, &config)?;
        assert!(
            converge(&ctx).is_err_and(|e| e.contains("packages.ai.python_requirements is missing"))
        );
        let packages = "[packages.ai]\npython_requirements = [\"usr/lib/mios/agent-pipe/requirements.txt\"]\npkgs = [\"python3\"]\n[packages.bloat]\nenable = false\npkgs = [\"python3-sqlite-vec\"]\n";
        write(root, SSOT, &format!("{config}{packages}"))?;
        assert!(converge(&ctx).is_err_and(|e| e.contains("requirements.txt")));
        write(
            root,
            "usr/lib/mios/agent-pipe/requirements.txt",
            "# sqlite-vec\nfastapi\nsqlite-vecx\n",
        )?;
        assert!(converge(&ctx).is_err_and(|e| e.contains(
            "mios.toml:6: [converge.memory].sqlite_vec_enable = true but no package set"
        )));
        write(
            root,
            "usr/lib/mios/agent-pipe/requirements.txt",
            "fastapi\nSqlite_Vec>=0.1 ; python_version >= '3.9'\n",
        )?;
        assert!(converge(&ctx).is_ok());
        write(
            root,
            "usr/lib/mios/agent-pipe/requirements.txt",
            "fastapi\n",
        )?;
        write(
            root,
            SSOT,
            &format!(
                "{config}{}",
                packages.replace("enable = false", "enable = true")
            ),
        )?;
        assert!(converge(&ctx).is_ok());
        assert_eq!(
            requirement("  Foo.Bar__baz[extra]>=1 # c").as_deref(),
            Some("foo-bar-baz")
        );
        assert_eq!(requirement("-r other.txt"), None);
        Ok(())
    }

    #[test]
    fn router_parity_runs_the_test_and_maps_every_intent_branch(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let temp = tempfile::tempdir()?;
        let root = temp.path();
        let ctx = DriftCtx::new(root.into(), false);
        assert!(router(&ctx).is_err_and(|e| e.contains("router parity input is missing")));
        write(root, SSOT, "[drift.lint]\npython = \"python3\"\n")?;
        let test = "import json, os, sys\nrows = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'tests', 'router_corpus.json')))\nbad = [r for r in rows if r.get('expected_mode') != r.get('input', {}).get('intent')]\nfor r in bad:\n    print('[FAIL] ' + r['description'])\nprint('[PASS] ran')\nsys.exit(1 if bad else 0)\n";
        write(root, PARITY_TEST, test)?;
        write(root, CORPUS, "[]")?;
        write(
            root,
            SERVER,
            "def route(plan):\n    if plan.get(\"intent\") == \"chat\":\n        return 1\n",
        )?;
        write(
            root,
            "usr/lib/mios/agent-pipe/mios_pipe/routing/router.py",
            "def r(intent):\n    return intent == 'CHAT'\n",
        )?;
        write(
            root,
            "usr/lib/mios/agent-pipe/mios_pipe/routing/test_router.py",
            "assert intent == 'ghost'\n",
        )?;
        assert!(router(&ctx).is_err_and(|e| e.contains("router_corpus.json:1: corpus has no rows")));
        let corpus = "[{\"description\":\"chat row\",\"input\":{\"intent\":\"chat\"},\"expected_mode\":\"chat\"},{\"description\":\"empty\",\"input\":{}}]";
        write(
            root,
            CORPUS,
            &corpus.replace(",\"input\":{}}", ",\"input\":{},\"expected_mode\":null}"),
        )?;
        assert!(router(&ctx).is_ok_and(|m| m.contains("4 subject")));
        write(
            root,
            CORPUS,
            &corpus.replace("\"expected_mode\":\"chat\"", "\"expected_mode\":\"agent\""),
        )?;
        assert!(router(&ctx).is_err_and(|e| e.contains("router parity test")
            && e.contains("[FAIL] chat row")
            && !e.contains("[PASS]")));
        write(
            root,
            CORPUS,
            &corpus.replace(",\"input\":{}}", ",\"input\":{},\"expected_mode\":null}"),
        )?;
        write(root, SERVER, "def route(plan):\n    if plan.get(\"intent\") == \"chat\":\n        return 1\n    elif intent == 'dag':\n        return 2\n")?;
        assert!(router(&ctx).is_err_and(|e| e
            .contains("server.py:4: intent == \"dag\" branch is not represented")
            && !e.contains("ghost")));
        fs::remove_file(root.join(SERVER))?;
        assert!(router(&ctx).is_err_and(|e| e.contains("server.py")));
        write(root, SERVER, "\n")?;
        write(
            root,
            SSOT,
            "[drift.lint]\npython = \"mios-no-such-python\"\n",
        )?;
        assert!(router(&ctx).is_err_and(|e| e.contains("cannot run SSOT drift.lint.python")));
        Ok(())
    }
}
