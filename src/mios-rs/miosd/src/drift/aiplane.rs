// AI-hint: AI-plane lint, hint coverage, and manifest integrity checks for miosd drift runner.
// AI-related: tools/native/mios-aiplane-lint, usr/lib/mios/agent-pipe/, usr/libexec/mios/mios-ai-tag, usr/share/mios/ai/v1/, usr/share/mios/skills/, usr/share/containers/systemd/

use super::{audit, Check, DriftCtx, Verdict};
use regex::Regex;
use serde_json::Value as Json;
use std::collections::{BTreeMap, BTreeSet};
use std::io::Read;
use std::path::Path;
use std::process::Command;

const SSOT: &str = "usr/share/mios/mios.toml";

pub struct AgentPipeBudgetsCheck;
impl Check for AgentPipeBudgetsCheck {
    fn id(&self) -> &'static str {
        "check_agent_pipe_budgets"
    }
    fn describe(&self) -> &'static str {
        "Assert agent pipe context token budgets are within bounds"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        super::audit::native(ctx, "mios-aiplane-lint", &[])
    }
}

pub struct VLLMNameCanonicalCheck;
impl Check for VLLMNameCanonicalCheck {
    fn id(&self) -> &'static str {
        "check_vllm_name_canonical"
    }
    fn describe(&self) -> &'static str {
        "Assert canonical MIOS_AI_VLLM_* environment variable naming"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        super::audit::verdict((|| {
            let pattern =
                regex::Regex::new(r"\bMIOS_AI_(?:VLLM|SGLANG)_").map_err(|e| e.to_string())?;
            let mut errors = Vec::new();
            let mut count = 0;
            for directory in ["automation", "usr/lib/mios"] {
                for path in super::audit::files(&ctx.root, directory)? {
                    if path == "automation/98-drift-checks.sh" {
                        continue;
                    }
                    let bytes =
                        std::fs::read(ctx.root.join(&path)).map_err(|e| format!("{path}: {e}"))?;
                    if bytes.contains(&0) {
                        continue;
                    }
                    let Ok(text) = std::str::from_utf8(&bytes) else {
                        continue;
                    };
                    count += 1;
                    for (line, text) in text.lines().enumerate() {
                        if pattern.is_match(text) {
                            errors.push(format!(
                                "{path}:{}: legacy long-form inference variable",
                                line + 1
                            ));
                        }
                    }
                }
            }
            super::audit::finish(count, errors, "canonical inference variable scan")
        })())
    }
}

pub struct HintCoverageCheck;
impl Check for HintCoverageCheck {
    fn id(&self) -> &'static str {
        "check_hint_coverage"
    }
    fn describe(&self) -> &'static str {
        "Assert AI-hint comment header coverage meets ratchet baseline"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict(coverage(ctx))
    }
}

/// `path:line` of a `[table]` header, or of `key =` inside it, in mios.toml text.
fn ssot_at(text: &str, table: &str, key: Option<&str>) -> String {
    let mut active = false;
    for (index, line) in text.lines().enumerate() {
        let line = line.trim();
        if let Some(header) = line.strip_prefix('[') {
            active = header.split(']').next() == Some(table);
            if active && key.is_none() {
                return format!("{SSOT}:{}", index + 1);
            }
        } else if active && key.is_some() && line.split('=').next().map(str::trim) == key {
            return format!("{SSOT}:{}", index + 1);
        }
    }
    SSOT.to_owned()
}

// Ported from usr/libexec/mios/mios-ai-hint-coverage, which deliberately does not
// define taggability: it imports mios-ai-tag and reuses walk()/existing_hint(). The
// tagger's tables are read from its syntax tree here for the same reason, so this
// gate and the tool that writes the headers cannot disagree about which files need
// one. Only literals are evaluated; no tagger code runs.
const TAGGER: &str = "usr/libexec/mios/mios-ai-tag";
const TAGGABILITY: &str = r#"import ast,json,sys
p=sys.argv[1];o={}
for n in ast.parse(open(p,'rb').read(),p).body:
    if not(isinstance(n,ast.Assign) and len(n.targets)==1 and isinstance(n.targets[0],ast.Name)):continue
    k,v=n.targets[0].id,n.value
    if k in('EXT','BASENAMES','JSON_EXT'):o[k]=sorted(ast.literal_eval(v))
    elif k in('SKIP_DIR','SKIP_SUFFIX','AI_LINE_RE','AI_HINT_TXT_RE') and isinstance(v,ast.Call) and ast.unparse(v.func)=='re.compile' and v.args:o[k]=[ast.literal_eval(v.args[0])]+[ast.unparse(a) for a in v.args[1:]+v.keywords]
print(json.dumps(o))"#;

struct Taggability {
    ext: BTreeSet<String>,
    basenames: BTreeSet<String>,
    json_ext: BTreeSet<String>,
    skip_dir: Regex,
    skip_suffix: Regex,
    ai_line: Regex,
    ai_hint: Regex,
    continuation: Regex,
    indent: Regex,
}

fn taggability(ctx: &DriftCtx, config: &toml::Value) -> Result<Taggability, String> {
    let python = audit::at(config, "drift.lint.python")?
        .as_str()
        .filter(|s| !s.trim().is_empty())
        .ok_or("Invalid SSOT drift.lint.python")?;
    let tagger = ctx.root.join(TAGGER);
    if !tagger.is_file() {
        return Err(format!("{TAGGER}: missing; it defines which files are taggable, so coverage cannot be measured -- restore it"));
    }
    let output = Command::new(python)
        .args(["-I", "-c", TAGGABILITY])
        .arg(&tagger)
        .current_dir(&ctx.root)
        .output()
        .map_err(|e| format!("Python syntax parser {python}: {e}"))?;
    if !output.status.success() {
        return Err(format!(
            "{TAGGER}: Python AST {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    let tables: BTreeMap<String, Vec<String>> = serde_json::from_slice(&output.stdout)
        .map_err(|e| format!("{TAGGER}: invalid taggability receipt: {e}"))?;
    let table = |name: &str| {
        tables.get(name).ok_or_else(|| format!("{TAGGER}: no literal {name} table; the tagger's taggability rules moved, port them here"))
    };
    let set =
        |name: &str| table(name).map(|values| values.iter().cloned().collect::<BTreeSet<_>>());
    let pattern = |name: &str| -> Result<Regex, String> {
        let (source, flags) = table(name)?
            .split_first()
            .ok_or_else(|| format!("{TAGGER}: {name} has no pattern"))?;
        let mut prefix = "";
        for flag in flags {
            match flag.as_str() {
                "re.I" | "re.IGNORECASE" => prefix = "(?i)",
                other => {
                    return Err(format!(
                        "{TAGGER}: {name} uses regex flag {other}, which this gate does not port"
                    ))
                }
            }
        }
        Regex::new(&format!("{prefix}{source}"))
            .map_err(|e| format!("{TAGGER}: {name} is not a portable pattern: {e}"))
    };
    let code = |source: &str| Regex::new(source).map_err(|e| e.to_string());
    Ok(Taggability {
        ext: set("EXT")?,
        basenames: set("BASENAMES")?,
        json_ext: set("JSON_EXT")?,
        skip_dir: pattern("SKIP_DIR")?,
        skip_suffix: pattern("SKIP_SUFFIX")?,
        ai_line: pattern("AI_LINE_RE")?,
        ai_hint: pattern("AI_HINT_TXT_RE")?,
        // existing_hint()'s wrapped-continuation patterns are code, not tables.
        continuation: code(r"^[#/<!;\-*\s]*\s{2,}\S")?,
        indent: code(r"^[#/<!;\-*\s]*\s{2,}")?,
    })
}

fn prefix(path: &Path, limit: u64, rel: &str) -> Result<Vec<u8>, String> {
    let mut bytes = Vec::new();
    std::fs::File::open(path)
        .and_then(|file| file.take(limit).read_to_end(&mut bytes))
        .map_err(|e| format!("{rel}: {e}"))?;
    Ok(bytes)
}

/// Python's `os.path.splitext` extension: leading dots of a name are not one.
fn extension(base: &str) -> &str {
    match base.rfind('.') {
        Some(dot) if !base[..dot].chars().all(|c| c == '.') => &base[dot..],
        _ => "",
    }
}

fn indexable(rules: &Taggability, path: &Path, rel: &str) -> Result<bool, String> {
    if rules.skip_suffix.is_match(rel) {
        return Ok(false);
    }
    let base = rel.rsplit('/').next().unwrap_or(rel);
    let ext = extension(base).to_lowercase();
    if rules.json_ext.contains(&ext) {
        return Ok(false);
    }
    if rules.ext.contains(&ext) || rules.basenames.contains(base) {
        return Ok(true);
    }
    Ok(ext.is_empty() && prefix(path, 2, rel)? == b"#!")
}

/// The tagger's non-git fallback walk: SKIP_DIR prunes directories; directory
/// symlinks are listed but never descended (os.walk followlinks=False).
fn walk(
    root: &Path,
    under: &str,
    rules: &Taggability,
    out: &mut Vec<String>,
) -> Result<(), String> {
    for entry in std::fs::read_dir(root.join(under)).map_err(|e| format!("{under}: {e}"))? {
        let entry = entry.map_err(|e| format!("{under}: {e}"))?;
        let name = entry.file_name();
        let rel = if under.is_empty() {
            name.to_string_lossy().into_owned()
        } else {
            format!("{under}/{}", name.to_string_lossy())
        };
        if entry
            .file_type()
            .map_err(|e| format!("{rel}: {e}"))?
            .is_dir()
        {
            if !rules.skip_dir.is_match(&rel) {
                walk(root, &rel, rules, out)?;
            }
        } else {
            out.push(rel);
        }
    }
    Ok(())
}

/// mios-ai-tag walk(): the tracked set in a checkout (the legacy tool's gitignore
/// filter is a no-op there, since check-ignore never reports a tracked path), the
/// filesystem otherwise. Selection is root-relative; the legacy matched SKIP_DIR
/// against absolute paths, so a root under e.g. `/srv/dist/` scanned nothing.
fn taggable(ctx: &DriftCtx, rules: &Taggability) -> Result<Vec<String>, String> {
    let candidates = if ctx.git_ok {
        audit::tracked(ctx)?
    } else {
        let mut out = Vec::new();
        walk(&ctx.root, "", rules, &mut out)?;
        out
    };
    let mut selected = Vec::new();
    for rel in candidates {
        if ctx.git_ok && rules.skip_dir.is_match(&rel) {
            continue;
        }
        let path = ctx.root.join(&rel);
        if path.is_file()
            && indexable(rules, &path, &rel)?
            && !prefix(&path, 2048, &rel)?.contains(&0)
        {
            selected.push(rel);
        }
    }
    selected.sort();
    selected.dedup();
    Ok(selected)
}

/// The tagger reads 8192 universal-newline characters and Python-splitlines them.
fn head(path: &Path, rel: &str) -> Result<String, String> {
    let text = String::from_utf8_lossy(&prefix(path, 8192 * 4 + 4, rel)?)
        .replace("\r\n", "\n")
        .replace('\r', "\n");
    Ok(text.chars().take(8192).collect())
}

fn splitlines(text: &str) -> Vec<&str> {
    let mut lines = Vec::new();
    let mut start = 0;
    for (index, c) in text.char_indices() {
        if matches!(
            c,
            '\n' | '\x0b' | '\x0c' | '\x1c' | '\x1d' | '\x1e' | '\u{85}' | '\u{2028}' | '\u{2029}'
        ) {
            lines.push(&text[start..index]);
            start = index + c.len_utf8();
        }
    }
    if start < text.len() {
        lines.push(&text[start..]);
    }
    lines
}

/// mios-ai-tag existing_hint(): the first AI-hint line within 40 lines, plus its
/// wrapped continuation, is a header only if text survives `_clean` (whitespace
/// and surrounding quotes stripped) -- `AI-hint: ""` is no hint.
fn hinted(rules: &Taggability, head: &str) -> bool {
    let trim = |s: &str| s.trim().trim_end_matches(['-', '>']).trim().to_owned();
    let lines: Vec<&str> = splitlines(head).into_iter().take(40).collect();
    let Some((index, hint)) = lines
        .iter()
        .enumerate()
        .find_map(|(i, line)| rules.ai_hint.captures(line).map(|c| (i, c)))
    else {
        return false;
    };
    let mut text = trim(hint.get(1).map_or("", |m| m.as_str()));
    for line in lines.iter().skip(index + 1) {
        if rules.ai_line.is_match(line) || !rules.continuation.is_match(line) {
            break;
        }
        text.push(' ');
        text.push_str(&trim(
            rules.indent.find(line).map_or(*line, |m| &line[m.end()..]),
        ));
    }
    text.chars()
        .any(|c| !(c == '"' || c == '\'' || c.is_whitespace()))
}

fn coverage(ctx: &DriftCtx) -> audit::Audit {
    let config = audit::ssot(ctx)?;
    let ceiling = audit::at(&config, "ai_tag.max_untagged")?
        .as_integer()
        .and_then(|n| usize::try_from(n).ok())
        .ok_or("SSOT ai_tag.max_untagged must be a non-negative integer")?;
    let rules = taggability(ctx, &config)?;
    let files = taggable(ctx, &rules)?;
    let mut missing = Vec::new();
    for rel in &files {
        if !hinted(&rules, &head(&ctx.root.join(rel), rel)?) {
            missing.push(rel.as_str());
        }
    }
    let untagged = missing.len();
    let at = || {
        audit::read(&ctx.root, SSOT)
            .map(|text| ssot_at(&text, "ai_tag", Some("max_untagged")))
            .unwrap_or_else(|_| SSOT.to_owned())
    };
    let mut errors = Vec::new();
    if untagged > ceiling {
        errors.extend(missing.iter().map(|rel| {
            format!("{rel}:1: taggable file has no AI-hint header in its first 40 lines")
        }));
        errors.push(format!("{}: {untagged} untagged > [ai_tag].max_untagged {ceiling}; tag them with mios-ai-tag, or raise the ceiling only for prompt/data files that must stay header-free", at()));
    } else if untagged < ceiling && ctx.git_ok && !ctx.in_image && !ctx.incomplete_tree {
        // The SSOT comment's own defect: "shrink-only, set to the measured count";
        // slack lets that many new untagged files land unseen. Only a complete
        // tracked tree measures the true count (an image context holds a subset).
        errors.push(format!("{}: [ai_tag].max_untagged {ceiling} leaves {} file(s) of slack over the measured {untagged} untagged; lower it to {untagged}", at(), ceiling - untagged));
    }
    audit::finish(files.len(), errors, &format!("AI-hint coverage: {} of {} taggable file(s) tagged, {untagged} untagged, ceiling [ai_tag].max_untagged {ceiling}", files.len() - untagged, files.len()))
}

pub struct StructuredAIManifestCheck;
impl Check for StructuredAIManifestCheck {
    fn id(&self) -> &'static str {
        "check_structured"
    }
    fn describe(&self) -> &'static str {
        "Assert structured AI manifest reference integrity"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict(structured(ctx))
    }
}

const UNIT_DIRS: [&str; 3] = [
    "usr/share/containers/systemd",
    "usr/lib/systemd/system",
    "etc/containers/systemd",
];
const AI_V1: &str = "usr/share/mios/ai/v1";

/// Shell-style expansion against the SSOT port exports (`MIOS_PORTS_<KEY>` =
/// [ports].<key>); an unknown variable takes its `:-default`, as it would unset.
/// Endpoints and units float their ports, so the legacy literal-port regexes
/// matched no node at all: every lane passed without being compared.
fn expand(text: &str, ports: &BTreeMap<String, String>, placeholder: &Regex) -> String {
    placeholder
        .replace_all(text, |c: &regex::Captures| {
            let name = c.get(1).or_else(|| c.get(3)).map_or("", |m| m.as_str());
            ports
                .get(name)
                .cloned()
                .or_else(|| c.get(2).map(|m| m.as_str().to_owned()))
                .unwrap_or_else(|| c.get(0).map_or("", |m| m.as_str()).to_owned())
        })
        .into_owned()
}

fn tool_references(ctx: &DriftCtx, rel: &str, doc: &Json, errors: &mut Vec<String>) -> usize {
    let Some(entries) = doc.get("data").and_then(Json::as_array) else {
        errors.push(format!("{rel}: data must be an array of tool entries"));
        return 0;
    };
    let mut count = 0;
    for entry in entries.iter().filter_map(Json::as_object) {
        for key in ["chat_completions", "responses", "schema_output"] {
            let Some(target) = entry
                .get(key)
                .and_then(Json::as_str)
                .filter(|t| t.starts_with("/usr/"))
            else {
                continue;
            };
            count += 1;
            let name = entry
                .get("name")
                .map_or_else(|| "null".to_owned(), Json::to_string);
            let relative = target.trim_start_matches('/');
            if relative.split('/').any(|part| part == "..") {
                errors.push(format!("{rel}: {name} {key} -> {target} escapes /usr"));
            } else if !ctx.root.join(relative).exists() {
                errors.push(format!("{rel}: {name} {key} -> {target} is missing from the tree; ship it or drop the reference"));
            }
        }
    }
    count
}

fn structured(ctx: &DriftCtx) -> audit::Audit {
    let config = audit::ssot(ctx)?;
    let text = audit::read(&ctx.root, SSOT)?;
    let ports: BTreeMap<String, String> = audit::at(&config, "ports")?
        .as_table()
        .ok_or("SSOT ports must be a table")?
        .iter()
        .filter_map(|(key, value)| {
            value.as_integer().map(|port| {
                (
                    format!("MIOS_PORTS_{}", key.to_uppercase()),
                    port.to_string(),
                )
            })
        })
        .collect();
    let compile = |source: &str| Regex::new(source).map_err(|e| e.to_string());
    let placeholder =
        compile(r"\$(?:\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}|([A-Za-z_][A-Za-z0-9_]*))")?;
    let listeners = [
        compile(r":(\d{4,5})\b")?,
        compile(r"(?:--port[= ]|PublishPort[= ])(\d{4,5})")?,
    ];
    let local = compile(r"://(?:localhost|127\.0\.0\.1|host\.containers\.internal):(\d{4,5})")?;
    let unresolved = compile(r"://(?:localhost|127\.0\.0\.1|host\.containers\.internal):\$")?;
    let tracked = if ctx.git_ok {
        Some(audit::tracked(ctx)?)
    } else {
        None
    };
    let mut errors = Vec::new();

    let mut served = BTreeSet::new();
    let mut units = 0;
    for dir in UNIT_DIRS {
        if !ctx.root.join(dir).is_dir() {
            let prefix = format!("{dir}/");
            if tracked
                .iter()
                .flatten()
                .any(|path| path.starts_with(&prefix))
            {
                errors.push(format!(
                    "{dir}: tracked unit directory is missing from the tree; restore it"
                ));
            }
            continue;
        }
        for path in audit::files(&ctx.root, dir)? {
            if !path.ends_with(".container") && !path.ends_with(".service") {
                continue;
            }
            units += 1;
            let bytes = std::fs::read(ctx.root.join(&path)).map_err(|e| format!("{path}: {e}"))?;
            let unit = expand(&String::from_utf8_lossy(&bytes), &ports, &placeholder);
            for listener in &listeners {
                served.extend(
                    listener
                        .captures_iter(&unit)
                        .filter_map(|c| c.get(1))
                        .map(|m| m.as_str().to_owned()),
                );
            }
        }
    }
    if units == 0 {
        errors.push(format!(
            "{}: no .container/.service unit found, so no lane can be shown served",
            UNIT_DIRS.join(", ")
        ));
    }

    let mut lanes = 0;
    for (name, node) in config
        .get("nodes")
        .and_then(toml::Value::as_table)
        .into_iter()
        .flatten()
    {
        let Some(node) = node.as_table() else {
            continue;
        };
        let at = ssot_at(&text, &format!("nodes.{name}"), None);
        let endpoint = match node.get("endpoint").map(toml::Value::as_str) {
            None => "",
            Some(Some(endpoint)) => endpoint.trim(),
            Some(None) => {
                errors.push(format!("{at}: [nodes.{name}] endpoint must be a string"));
                continue;
            }
        };
        if endpoint.is_empty() {
            continue;
        } // inert node, skipped by the loader
        let resolved = expand(endpoint, &ports, &placeholder);
        if let Some(port) = local.captures(&resolved).and_then(|c| c.get(1)) {
            lanes += 1;
            // A lane served on the Windows host (WSL loopback) names the host
            // server config that serves it; that config must exist and listen on
            // the lane's resolved port, or the lane is as dangling as before.
            let host_served = match node.get("host_served").map(toml::Value::as_str) {
                None => None,
                Some(Some(path)) => Some(path.trim()),
                Some(None) => {
                    errors.push(format!(
                        "{at}: [nodes.{name}] host_served must be a repo path"
                    ));
                    continue;
                }
            };
            if let Some(path) = host_served {
                match std::fs::read_to_string(ctx.root.join(path)) {
                    Ok(body)
                        if body
                            .split_whitespace()
                            .collect::<Vec<_>>()
                            .windows(2)
                            .any(|w| {
                                w[0].eq_ignore_ascii_case("-Port") && w[1] == port.as_str()
                            })
                            || body.contains(&format!(":{}", port.as_str())) => {}
                    Ok(_) => errors.push(format!(
                        "{at}: [nodes.{name}] host_served {path} does not listen on localhost:{}",
                        port.as_str()
                    )),
                    Err(e) => errors.push(format!("{at}: [nodes.{name}] host_served {path}: {e}")),
                }
            } else if !served.contains(port.as_str()) {
                errors.push(format!("{at}: [nodes.{name}] endpoint {endpoint} -> localhost:{} is served by no shipped unit (dangling lane); ship a unit that listens on it, declare the host server config that serves it (host_served), or move the lane to an operator overlay", port.as_str()));
            }
        } else if unresolved.is_match(&resolved) {
            errors.push(format!("{at}: [nodes.{name}] endpoint {endpoint} names a port variable that is not an SSOT [ports] key"));
        } // a remote endpoint is an operator overlay, unverifiable from the tree
    }
    if lanes == 0 {
        errors.push(format!(
            "{SSOT}: no [nodes.*] endpoint resolves to a localhost lane, so no lane was checked"
        ));
    }

    let table = |key: &str| config.get(key).and_then(toml::Value::as_table);
    let observability = table("observability");
    match observability.and_then(|o| o.get("surface_default")) {
        None => errors.push(format!(
            "{}: [observability] surface_default is missing",
            ssot_at(&text, "observability", None)
        )),
        Some(value) if !matches!(value.as_str(), Some("clean" | "inline")) => errors.push(format!(
            "{}: [observability] surface_default {value} must be 'clean' or 'inline'",
            ssot_at(&text, "observability", Some("surface_default"))
        )),
        Some(_) => {}
    }
    let channels = observability
        .and_then(|o| o.get("channels"))
        .and_then(toml::Value::as_table);
    for key in [
        "thinking",
        "plan",
        "tool_call",
        "tool_result",
        "source",
        "content",
    ] {
        if !channels.is_some_and(|c| c.contains_key(key)) {
            errors.push(format!(
                "{}: [observability.channels] key '{key}' is missing",
                ssot_at(&text, "observability.channels", None)
            ));
        }
    }
    for lane in ["light", "sglang", "vllm"] {
        let Some(spec) = table("lanes").and_then(|l| l.get(lane)) else {
            errors.push(format!("{SSOT}: [lanes.{lane}] section is missing"));
            continue;
        };
        for key in [
            "stream_thinking",
            "tool_call_parser",
            "reasoning_parser",
            "constrained_tools",
        ] {
            if !spec.as_table().is_some_and(|t| t.contains_key(key)) {
                errors.push(format!(
                    "{}: [lanes.{lane}].{key} is missing",
                    ssot_at(&text, &format!("lanes.{lane}"), None)
                ));
            }
        }
    }
    for key in ["tool_loop_limit", "reflexion_limit", "reflexion_enable"] {
        if !table("agent_pipe").is_some_and(|t| t.contains_key(key)) {
            errors.push(format!(
                "{}: [agent_pipe].{key} is missing",
                ssot_at(&text, "agent_pipe", None)
            ));
        }
    }

    let mut names = Vec::new();
    for entry in std::fs::read_dir(ctx.root.join(AI_V1))
        .map_err(|e| format!("{AI_V1}: {e}; the ai/v1 manifest directory is required"))?
    {
        names.push(
            entry
                .map_err(|e| format!("{AI_V1}: {e}"))?
                .file_name()
                .to_string_lossy()
                .into_owned(),
        );
    }
    names.sort();
    let (mut manifests, mut references, mut tools) = (0, 0, false);
    for name in names.iter().filter(|name| name.ends_with(".json")) {
        manifests += 1;
        let rel = format!("{AI_V1}/{name}");
        let doc: Json = match std::fs::read(ctx.root.join(&rel)) {
            Err(e) => {
                errors.push(format!("{rel}: unreadable: {e}"));
                continue;
            }
            Ok(bytes) => match serde_json::from_slice(&bytes) {
                Ok(doc) => doc,
                Err(e) => {
                    errors.push(format!("{rel}:{}: does not parse as JSON: {e}", e.line()));
                    continue;
                }
            },
        };
        if name == "tools.json" {
            tools = true;
            references += tool_references(ctx, &rel, &doc, &mut errors);
        }
    }
    if manifests == 0 {
        errors.push(format!("{AI_V1}: no *.json manifest found"));
    }
    if !tools {
        errors.push(format!(
            "{AI_V1}/tools.json: missing, so no tool reference was resolved"
        ));
    } else if references == 0 {
        errors.push(format!(
            "{AI_V1}/tools.json: declares no /usr/ reference, so none was resolved"
        ));
    }
    audit::finish(lanes + units + manifests + references, errors, &format!(
        "structured AI plane: {lanes} localhost lane(s) served by {units} shipped unit(s), SSOT observability/lanes/agent_pipe keys present, {manifests} ai/v1 manifest(s) parsed, {references} tools.json reference(s) resolved"))
}

pub struct CapabilityManifestCheck;
impl Check for CapabilityManifestCheck {
    fn id(&self) -> &'static str {
        "check_capability_manifest"
    }
    fn describe(&self) -> &'static str {
        "Assert capability manifest matches SSOT definitions"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict(capabilities(ctx))
    }
}

// Ported from mios_pipe/lifecycle/capreg.py (project_from_toml + diff_capabilities),
// the core of mios-ai-capabilities-gen. No native generator renders this file.
const CAPS: &str = "usr/share/mios/ai/v1/capabilities.generated.json";
const SKILLS: &str = "usr/share/mios/skills";

type Key = (String, String);

struct Capability {
    tier: String,
    platforms: Vec<String>,
}

/// SSOT [ai].permission_tiers, safest first. The committed artifact is projected
/// at the top of the lattice (the generator's ceiling=interactive, "every
/// known-tier capability"), so a tier is admitted iff it is a known one.
struct Tiers {
    raw: Vec<String>,
    known: Vec<String>,
}

impl Tiers {
    fn rank(&self, tier: &str) -> usize {
        let tier = tier.trim().to_lowercase();
        self.known
            .iter()
            .position(|known| *known == tier)
            .unwrap_or(self.known.len())
    }
    fn allowed(&self, tier: &str) -> bool {
        self.rank(tier) < self.known.len()
    }
}

fn toml_truthy(value: &toml::Value) -> bool {
    match value {
        toml::Value::String(s) => !s.is_empty(),
        toml::Value::Integer(n) => *n != 0,
        toml::Value::Float(f) => f.classify() != std::num::FpCategory::Zero,
        toml::Value::Boolean(b) => *b,
        toml::Value::Array(a) => !a.is_empty(),
        toml::Value::Table(t) => !t.is_empty(),
        toml::Value::Datetime(_) => true,
    }
}

fn json_truthy(value: &Json) -> bool {
    match value {
        Json::Null => false,
        Json::Bool(b) => *b,
        Json::Number(n) => n
            .as_f64()
            .is_some_and(|f| f.classify() != std::num::FpCategory::Zero),
        Json::String(s) => !s.is_empty(),
        Json::Array(a) => !a.is_empty(),
        Json::Object(o) => !o.is_empty(),
    }
}

fn json_text(value: &Json) -> String {
    value
        .as_str()
        .map_or_else(|| value.to_string(), str::to_owned)
}

fn permission(spec: &toml::Table) -> String {
    match spec.get("permission") {
        None => "read".to_owned(),
        Some(toml::Value::String(tier)) => tier.clone(),
        Some(other) => other.to_string(),
    }
}

/// capreg skill_steps(): body.steps[].verb, the DAG edges out of a skill.
fn skill_steps(spec: &Json) -> Result<Vec<String>, String> {
    let body = spec.get("body").filter(|b| json_truthy(b)).unwrap_or(spec);
    if !body.is_object() {
        return Err("body must be a JSON object".into());
    }
    let steps = match body.get("steps") {
        Some(Json::Array(steps)) => steps.as_slice(),
        Some(other) if json_truthy(other) => return Err("body.steps must be an array".into()),
        _ => return Ok(Vec::new()),
    };
    let mut verbs = Vec::new();
    for verb in steps
        .iter()
        .filter_map(|step| step.as_object()?.get("verb"))
        .filter(|v| json_truthy(v))
    {
        verbs.push(
            verb.as_str()
                .ok_or_else(|| format!("step verb {verb} must be a string"))?
                .to_owned(),
        );
    }
    Ok(verbs)
}

fn skills(ctx: &DriftCtx) -> Result<BTreeMap<String, Vec<String>>, String> {
    let mut files = Vec::new();
    for entry in std::fs::read_dir(ctx.root.join(SKILLS))
        .map_err(|e| format!("{SKILLS}: {e}; the structured skills directory is required"))?
    {
        let name = entry
            .map_err(|e| format!("{SKILLS}: {e}"))?
            .file_name()
            .to_string_lossy()
            .into_owned();
        if name.ends_with(".json") && !name.starts_with('.') {
            files.push(name);
        }
    }
    files.sort();
    let mut skills = BTreeMap::new();
    for file in files {
        let rel = format!("{SKILLS}/{file}");
        let bytes = std::fs::read(ctx.root.join(&rel)).map_err(|e| format!("{rel}: {e}"))?;
        let spec: Json = serde_json::from_slice(&bytes)
            .map_err(|e| format!("{rel}:{}: does not parse as JSON: {e}", e.line()))?;
        if !spec.is_object() {
            return Err(format!("{rel}: a skill must be a JSON object"));
        }
        let name = match spec.get("name").filter(|n| json_truthy(n)) {
            None => file.trim_end_matches(".json").to_owned(),
            Some(Json::String(name)) => name.clone(),
            Some(other) => return Err(format!("{rel}: name {other} must be a string")),
        };
        let steps = skill_steps(&spec).map_err(|e| format!("{rel}: {e}"))?;
        // Python's dict kept the last file silently, so one skill vanished from the
        // projection and the committed manifest could not show it.
        if skills.insert(name.clone(), steps).is_some() {
            return Err(format!(
                "{rel}: skill {name} is declared by more than one file"
            ));
        }
    }
    Ok(skills)
}

/// capreg skill_effective_tier(): a skill is no safer than the most privileged
/// capability it reaches; a dangling component is unknown (never admitted) and a
/// skill cycle takes the strictest known tier.
fn skill_tier(
    name: &str,
    skills: &BTreeMap<String, Vec<String>>,
    verbs: &BTreeMap<&str, String>,
    tiers: &Tiers,
    seen: &BTreeSet<String>,
) -> String {
    if seen.contains(name) {
        return tiers.raw.last().cloned().unwrap_or_default();
    }
    let mut seen = seen.clone();
    seen.insert(name.to_owned());
    let (mut best, mut best_rank) = ("read".to_owned(), None);
    for component in skills.get(name).into_iter().flatten() {
        let tier = if let Some(tier) = verbs.get(component.as_str()) {
            tier.clone()
        } else if skills.contains_key(component) {
            skill_tier(component, skills, verbs, tiers, &seen)
        } else {
            return "(unknown)".to_owned();
        };
        let rank = tiers.rank(&tier);
        if best_rank.is_none_or(|previous| rank > previous) {
            best_rank = Some(rank);
            best = tier;
        }
    }
    best
}

fn projection(ctx: &DriftCtx, config: &toml::Value) -> Result<BTreeMap<Key, Capability>, String> {
    let raw = audit::strings(config, "ai.permission_tiers")?;
    let known: Vec<String> = raw.iter().map(|tier| tier.trim().to_lowercase()).collect();
    if known.is_empty() || known.iter().any(String::is_empty) {
        return Err("SSOT ai.permission_tiers must be a non-empty lattice of named tiers".into());
    }
    let tiers = Tiers { raw, known };
    let empty = toml::Table::new();
    let section = |key: &str| {
        config
            .get(key)
            .and_then(toml::Value::as_table)
            .unwrap_or(&empty)
    };
    // Agent verbs carry `section`; entries without one are configurator buttons.
    let verbs: BTreeMap<&str, String> = section("verbs")
        .iter()
        .filter_map(|(name, spec)| {
            spec.as_table()
                .filter(|t| t.contains_key("section"))
                .map(|t| (name.as_str(), permission(t)))
        })
        .collect();
    let mut out = BTreeMap::new();
    for (name, tier) in &verbs {
        if tiers.allowed(tier) {
            out.insert(
                ("verb".to_owned(), (*name).to_owned()),
                Capability {
                    tier: tier.clone(),
                    platforms: Vec::new(),
                },
            );
        }
    }
    for (name, spec) in section("recipes")
        .iter()
        .filter_map(|(name, spec)| spec.as_table().map(|t| (name, t)))
    {
        let tier = permission(spec);
        if !tiers.allowed(&tier) {
            continue;
        }
        let platforms = ["linux", "windows"]
            .into_iter()
            .filter(|p| spec.get(*p).is_some_and(toml_truthy))
            .map(str::to_owned)
            .collect();
        out.insert(
            ("recipe".to_owned(), name.clone()),
            Capability { tier, platforms },
        );
    }
    let skills = skills(ctx)?;
    for (name, uses) in &skills {
        let tier = skill_tier(name, &skills, &verbs, &tiers, &BTreeSet::new());
        if !tiers.allowed(&tier)
            || !uses
                .iter()
                .filter_map(|verb| verbs.get(verb.as_str()))
                .all(|t| tiers.allowed(t))
        {
            continue;
        }
        out.insert(
            ("skill".to_owned(), name.clone()),
            Capability {
                tier,
                platforms: Vec::new(),
            },
        );
    }
    Ok(out)
}

fn capabilities(ctx: &DriftCtx) -> audit::Audit {
    let config = audit::ssot(ctx)?;
    let generated = projection(ctx, &config)?;
    let bytes = std::fs::read(ctx.root.join(CAPS))
        .map_err(|e| format!("{CAPS}: {e}; regenerate it with mios-ai-capabilities-gen"))?;
    let doc: Json = serde_json::from_slice(&bytes)
        .map_err(|e| format!("{CAPS}:{}: does not parse as JSON: {e}", e.line()))?;
    let none: &[Json] = &[];
    let data = match doc
        .as_object()
        .ok_or_else(|| format!("{CAPS}: must be a JSON object"))?
        .get("data")
    {
        None | Some(Json::Null) => none,
        Some(Json::Array(data)) => data.as_slice(),
        Some(_) => return Err(format!("{CAPS}: data must be an array")),
    };
    let mut errors = Vec::new();
    let mut committed: BTreeMap<Key, (Option<&Json>, Vec<String>)> = BTreeMap::new();
    for entry in data {
        let entry = entry
            .as_object()
            .ok_or_else(|| format!("{CAPS}: data entry {entry} is not an object"))?;
        let field = |key: &str| entry.get(key).map_or_else(String::new, json_text);
        let key = (field("kind"), field("name"));
        let mut platforms = match entry.get("platforms") {
            Some(Json::Array(list)) => list.iter().map(json_text).collect(),
            Some(other) if json_truthy(other) => vec![json_text(other)],
            _ => Vec::new(),
        };
        platforms.sort();
        if committed
            .insert(key.clone(), (entry.get("tier"), platforms))
            .is_some()
        {
            errors.push(format!("{CAPS}: duplicate entry {}:{}", key.0, key.1));
        }
    }
    for (kind, name) in generated.keys().filter(|key| !committed.contains_key(*key)) {
        errors.push(format!(
            "{CAPS}: + {kind}:{name} (in SSOT, missing from committed)"
        ));
    }
    for (kind, name) in committed.keys().filter(|key| !generated.contains_key(*key)) {
        errors.push(format!(
            "{CAPS}: - {kind}:{name} (committed, no longer in SSOT)"
        ));
    }
    for ((kind, name), capability) in &generated {
        let Some((tier, platforms)) = committed.get(&(kind.clone(), name.clone())) else {
            continue;
        };
        if tier.and_then(Json::as_str) != Some(capability.tier.as_str()) {
            errors.push(format!(
                "{CAPS}: ~ {kind}:{name} tier {} -> \"{}\"",
                tier.map_or_else(|| "None".to_owned(), Json::to_string),
                capability.tier
            ));
        }
        if *platforms != capability.platforms {
            errors.push(format!(
                "{CAPS}: ~ {kind}:{name} platforms {platforms:?} -> {:?}",
                capability.platforms
            ));
        }
    }
    if !errors.is_empty() {
        errors.push(format!("{CAPS}: stale against {SSOT} [verbs.*]+[recipes.*] and {SKILLS}/*.json; regenerate with mios-ai-capabilities-gen"));
    }
    let subjects = generated
        .keys()
        .chain(committed.keys())
        .collect::<BTreeSet<_>>()
        .len();
    audit::finish(subjects, errors, &format!("capability manifest: {} SSOT capabilities match {CAPS} by kind, name, tier and platforms", generated.len()))
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    type Outcome = Result<(), Box<dyn std::error::Error>>;

    fn write(root: &Path, rel: &str, text: &str) -> Outcome {
        let path = root.join(rel);
        if let Some(parent) = path.parent() {
            fs::create_dir_all(parent)?;
        }
        fs::write(path, text)?;
        Ok(())
    }

    // No shebang and no extension: the fixture tagger is not itself taggable.
    #[cfg(unix)]
    const TAGGER_FIXTURE: &str = r#"import re
EXT = {".sh", ".md"}
BASENAMES = {"Justfile"}
JSON_EXT = {".json"}
SKIP_DIR = re.compile(r"(^|/)(\.git|target)(/|$)")
SKIP_SUFFIX = re.compile(r"\.(png|lock)$", re.I)
AI_LINE_RE = re.compile(r"^[#/<!;\-*\s]*AI-(?:hint|related|functions):.*?(-->)?\s*$", re.I)
AI_HINT_TXT_RE = re.compile(r"AI-hint:\s*(.+?)\s*(?:-->)?\s*$", re.I)
"#;
    #[cfg(unix)]
    const HINT_POLICY: &str = "[drift.lint]\npython='python3'\n[ai_tag]\nmax_untagged=1\n";

    #[cfg(unix)]
    #[test]
    fn hint_coverage_applies_tagger_rules_against_the_ssot_ceiling() -> Outcome {
        let temp = tempfile::tempdir()?;
        let root = temp.path();
        write(root, SSOT, HINT_POLICY)?;
        let ctx = DriftCtx::new(root.into(), false);
        assert!(coverage(&ctx).is_err_and(|e| e.contains(TAGGER)));
        write(root, TAGGER, TAGGER_FIXTURE)?;
        assert!(coverage(&ctx).is_err_and(|e| e.contains("no subjects examined")));
        write(root, "a.sh", "#!/bin/sh\n# AI-hint: tagged script\n")?;
        write(root, "doc.md", "<!-- AI-hint: tagged doc -->\n")?;
        write(
            root,
            "wrapped.sh",
            "# AI-hint: \"\n#     the wrapped continuation carries it\n",
        )?;
        write(
            root,
            "tool",
            "#!/usr/bin/env python3\n# AI-hint: shebang tool\n",
        )?;
        write(root, "prompt.md", "header-free prompt\n")?;
        write(root, "data.json", "{}\n")?;
        write(root, "target/build.sh", "echo\n")?;
        write(root, "image.png", "x")?;
        fs::write(root.join("blob.sh"), b"\0binary")?;
        assert!(coverage(&ctx).is_ok_and(|m| m.contains("4 of 5 taggable")));
        write(root, "empty.sh", "# AI-hint: \"\"\n")?;
        assert!(coverage(&ctx).is_err_and(|e| e.contains("empty.sh:1:")
            && e.contains("prompt.md:1:")
            && e.contains("2 untagged > [ai_tag].max_untagged 1")));
        write(
            root,
            "empty.sh",
            &format!("{}# AI-hint: past the 40-line window\n", "\n".repeat(40)),
        )?;
        assert!(coverage(&ctx).is_err_and(|e| e.contains("empty.sh:1:")));
        fs::remove_file(root.join("empty.sh"))?;
        write(root, SSOT, "[drift.lint]\npython='python3'\n")?;
        assert!(coverage(&ctx).is_err_and(|e| e.contains("ai_tag.max_untagged")));
        write(root, SSOT, HINT_POLICY)?;
        write(
            root,
            TAGGER,
            &TAGGER_FIXTURE.replace("re.I)\nAI_LINE_RE", "re.M)\nAI_LINE_RE"),
        )?;
        assert!(coverage(&ctx).is_err_and(|e| e.contains("re.M")));
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn hint_coverage_ratchet_is_exact_over_the_tracked_set() -> Outcome {
        let temp = tempfile::tempdir()?;
        let root = temp.path();
        write(
            root,
            SSOT,
            &HINT_POLICY.replace("max_untagged=1", "max_untagged=3"),
        )?;
        write(root, TAGGER, TAGGER_FIXTURE)?;
        write(root, "a.sh", "# AI-hint: tagged\n")?;
        write(root, "b.md", "untagged\n")?;
        for args in [vec!["init", "-q"], vec!["add", "."]] {
            assert!(Command::new("git")
                .arg("-C")
                .arg(root)
                .args(args)
                .status()?
                .success());
        }
        write(
            root,
            "scratch.md",
            "untracked scratch is not shipped coverage\n",
        )?;
        let ctx = DriftCtx::new(root.into(), false);
        assert!(coverage(&ctx)
            .is_err_and(|e| e.contains("2 file(s) of slack") && e.contains("lower it to 1")));
        write(root, SSOT, HINT_POLICY)?;
        assert!(coverage(&ctx).is_ok_and(|m| m.contains("1 of 2 taggable")));
        Ok(())
    }

    fn structured_ssot() -> String {
        let mut ssot = String::from("[ports]\nllm_light = 8500\ncpu_node = 8510\n[nodes.local-light]\nendpoint = \"http://localhost:${MIOS_PORTS_LLM_LIGHT}/v1\"\n[nodes.remote]\nendpoint = \"http://peer.tailnet:9000/v1\"\n[nodes.inert]\nendpoint = \"\"\n[observability]\nsurface_default = \"clean\"\n[observability.channels]\n");
        for channel in [
            "thinking",
            "plan",
            "tool_call",
            "tool_result",
            "source",
            "content",
        ] {
            ssot.push_str(&format!("{channel} = true\n"));
        }
        for lane in ["light", "sglang", "vllm"] {
            ssot.push_str(&format!("[lanes.{lane}]\nstream_thinking = true\ntool_call_parser = \"hermes\"\nreasoning_parser = \"qwen3\"\nconstrained_tools = true\n"));
        }
        ssot.push_str(
            "[agent_pipe]\ntool_loop_limit = 8\nreflexion_limit = 2\nreflexion_enable = true\n",
        );
        ssot
    }

    #[test]
    fn structured_resolves_floated_ports_ssot_contract_and_manifest_references() -> Outcome {
        let temp = tempfile::tempdir()?;
        let root = temp.path();
        let base = structured_ssot();
        write(root, SSOT, &base)?;
        let ctx = DriftCtx::new(root.into(), false);
        assert!(structured(&ctx).is_err());
        write(
            root,
            "usr/share/containers/systemd/light.container",
            "[Container]\nExec=--listen 0.0.0.0:${MIOS_PORTS_LLM_LIGHT}\n",
        )?;
        write(
            root,
            "usr/share/mios/ai/v1/tools.json",
            r#"{"data":[{"name":"t","chat_completions":"/usr/lib/mios/tools/t.json","schema_output":null}]}"#,
        )?;
        write(root, "usr/lib/mios/tools/t.json", "{}")?;
        assert!(structured(&ctx).is_ok_and(
            |m| m.contains("1 localhost lane(s)") && m.contains("1 tools.json reference")
        ));
        write(root, SSOT, &format!("{base}[nodes.local-cpu]\nendpoint = \"http://127.0.0.1:${{MIOS_PORTS_CPU_NODE}}/v1\"\n"))?;
        assert!(
            structured(&ctx).is_err_and(|e| e.contains("[nodes.local-cpu]")
                && e.contains("localhost:8510")
                && e.contains("dangling"))
        );
        write(
            root,
            "usr/lib/systemd/system/cpu.service",
            "[Service]\nExecStart=/usr/bin/server --port ${MIOS_PORTS_CPU_NODE:-1}\n",
        )?;
        assert!(structured(&ctx).is_ok_and(|m| m.contains("2 localhost lane(s)")));
        // A Windows-host lane is served by its declared host config, which must
        // listen on the lane's port; a missing or mismatched config fails.
        let host = format!("{base}[nodes.local-host]\nendpoint = \"http://127.0.0.1:${{MIOS_PORTS_CPU_NODE}}/v1\"\nhost_served = \"usr/share/mios/windows/host.cfg\"\n");
        fs::remove_file(root.join("usr/lib/systemd/system/cpu.service"))?;
        write(root, SSOT, &host)?;
        assert!(structured(&ctx)
            .is_err_and(|e| e.contains("host_served usr/share/mios/windows/host.cfg")));
        write(
            root,
            "usr/share/mios/windows/host.cfg",
            "-File srv.ps1 -Mode Server -Port 9999\n",
        )?;
        assert!(structured(&ctx).is_err_and(|e| e.contains("does not listen on localhost:8510")));
        write(
            root,
            "usr/share/mios/windows/host.cfg",
            "-File srv.ps1 -Mode Server -Port 8510\n",
        )?;
        assert!(structured(&ctx).is_ok_and(|m| m.contains("2 localhost lane(s)")));
        write(
            root,
            "usr/lib/systemd/system/cpu.service",
            "[Service]\nExecStart=/usr/bin/server --port ${MIOS_PORTS_CPU_NODE:-1}\n",
        )?;
        write(root, SSOT, &format!("{base}[nodes.local-typo]\nendpoint = \"http://localhost:${{MIOS_PORTS_NOPE}}/v1\"\n"))?;
        assert!(structured(&ctx).is_err_and(
            |e| e.contains("[nodes.local-typo]") && e.contains("not an SSOT [ports] key")
        ));
        write(root, SSOT, &base)?;
        write(root, "usr/share/mios/ai/v1/broken.json", "{")?;
        assert!(
            structured(&ctx).is_err_and(|e| e.contains("broken.json:1: does not parse as JSON"))
        );
        fs::remove_file(root.join("usr/share/mios/ai/v1/broken.json"))?;
        fs::remove_file(root.join("usr/lib/mios/tools/t.json"))?;
        assert!(
            structured(&ctx).is_err_and(|e| e.contains("/usr/lib/mios/tools/t.json is missing"))
        );
        write(root, "usr/lib/mios/tools/t.json", "{}")?;
        write(
            root,
            SSOT,
            &base.replace("surface_default = \"clean\"", "surface_default = \"loud\""),
        )?;
        assert!(structured(&ctx).is_err_and(|e| e.contains("surface_default \"loud\"")));
        write(
            root,
            SSOT,
            &base
                .replace("reflexion_enable = true\n", "")
                .replace("[lanes.vllm]", "[lanes.other]"),
        )?;
        assert!(
            structured(&ctx).is_err_and(|e| e.contains("[agent_pipe].reflexion_enable")
                && e.contains("[lanes.vllm] section is missing"))
        );
        write(
            root,
            SSOT,
            &base.replace("localhost:${MIOS_PORTS_LLM_LIGHT}", "peer:9001"),
        )?;
        assert!(structured(&ctx)
            .is_err_and(|e| e.contains("no [nodes.*] endpoint resolves to a localhost lane")));
        Ok(())
    }

    const CAPS_SSOT: &str = "[ai]\npermission_tiers = ['read', 'write', 'interactive']\n[verbs.alpha]\nsection = 'Core'\npermission = 'write'\n[verbs.button]\ndesc = 'configurator button, not an agent verb'\n[verbs.root]\nsection = 'Core'\npermission = 'admin'\n[recipes.list]\nlinux = 'ls'\nwindows = ''\n";

    fn committed(tier: &str, platforms: &str) -> String {
        format!(
            r#"{{"object":"mios.capability.manifest","ceiling":"interactive","data":[{{"name":"list","kind":"recipe","tier":"read","platforms":{platforms}}},{{"name":"flow","kind":"skill","tier":"write","uses":["alpha"]}},{{"name":"alpha","kind":"verb","tier":"{tier}"}}]}}"#
        )
    }

    #[test]
    fn capability_manifest_reprojects_verbs_recipes_and_skills() -> Outcome {
        let temp = tempfile::tempdir()?;
        let root = temp.path();
        write(root, SSOT, CAPS_SSOT)?;
        write(
            root,
            &format!("{SKILLS}/flow.json"),
            r#"{"name":"flow","body":{"steps":[{"verb":"alpha"}]}}"#,
        )?;
        write(
            root,
            &format!("{SKILLS}/dangling.json"),
            r#"{"body":{"steps":[{"verb":"missing"}]}}"#,
        )?;
        let ctx = DriftCtx::new(root.into(), false);
        assert!(capabilities(&ctx).is_err_and(|e| e.contains(CAPS)));
        write(root, CAPS, &committed("write", r#"["linux"]"#))?;
        assert!(capabilities(&ctx).is_ok_and(|m| m.contains("3 SSOT capabilities")));
        write(root, CAPS, &committed("read", r#"["linux"]"#))?;
        assert!(capabilities(&ctx)
            .is_err_and(|e| e.contains("~ verb:alpha tier \"read\" -> \"write\"")
                && e.contains("mios-ai-capabilities-gen")));
        write(root, CAPS, &committed("write", r#"["linux","windows"]"#))?;
        assert!(capabilities(&ctx).is_err_and(|e| e.contains("~ recipe:list platforms")));
        write(root, CAPS, &committed("write", r#"["linux"]"#))?;
        write(
            root,
            SSOT,
            &format!("{CAPS_SSOT}[verbs.beta]\nsection = 'Core'\n"),
        )?;
        assert!(capabilities(&ctx)
            .is_err_and(|e| e.contains("+ verb:beta (in SSOT, missing from committed)")));
        write(
            root,
            SSOT,
            &CAPS_SSOT.replace("permission = 'write'", "permission = 'admin'"),
        )?;
        assert!(capabilities(&ctx)
            .is_err_and(|e| e.contains("- verb:alpha") && e.contains("- skill:flow")));
        write(root, &format!("{SKILLS}/again.json"), r#"{"name":"flow"}"#)?;
        assert!(capabilities(&ctx).is_err_and(|e| e.contains("more than one file")));
        fs::remove_file(root.join(format!("{SKILLS}/again.json")))?;
        fs::remove_file(root.join(format!("{SKILLS}/flow.json")))?;
        fs::remove_file(root.join(format!("{SKILLS}/dangling.json")))?;
        write(
            root,
            SSOT,
            "[ai]\npermission_tiers = ['read', 'write', 'interactive']\n",
        )?;
        write(root, CAPS, r#"{"data":[]}"#)?;
        assert!(capabilities(&ctx).is_err_and(|e| e.contains("no subjects examined")));
        Ok(())
    }
}
