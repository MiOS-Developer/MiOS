// AI-hint: DB seed coverage, RBAC tiers, CLI SQL safety and DB-TOML roundtrip checks for miosd drift runner.
// AI-related: usr/libexec/mios/seed-db-config.py, usr/libexec/mios/materialize-config-toml.py, usr/share/mios/mios.toml, usr/libexec/mios

use super::audit;
use super::{Check, DriftCtx, Verdict};
use regex::Regex;
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::path::{Path, PathBuf};
use std::process::Command;
use toml::Value;

const SSOT: &str = "usr/share/mios/mios.toml";
const SEED: &str = "usr/libexec/mios/seed-db-config.py";
const MATERIALIZE: &str = "usr/libexec/mios/materialize-config-toml.py";
const LIBEXEC: &str = "usr/libexec/mios";

pub struct DBSeedCoverageCheck;
impl Check for DBSeedCoverageCheck {
    fn id(&self) -> &'static str {
        "check_db_seed_coverage"
    }
    fn describe(&self) -> &'static str {
        "Assert all SSOT sections and verbs are covered by DB seed script"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict(seed_coverage(ctx))
    }
}

fn interpreter(doc: &Value) -> Result<String, String> {
    audit::at(doc, "drift.lint.python")?
        .as_str()
        .map(str::trim)
        .filter(|s| !s.is_empty())
        .map(str::to_owned)
        .ok_or_else(|| "SSOT drift.lint.python must name the Python interpreter".to_owned())
}

fn required(ctx: &DriftCtx, rel: &str, what: &str) -> Result<PathBuf, String> {
    let path = ctx.root.join(rel);
    if path.is_file() {
        Ok(path)
    } else {
        Err(format!(
            "{rel}: {what} is missing; it is a tracked deliverable, so this check cannot run"
        ))
    }
}

/// `mios.toml:LINE` of the first table header for a dotted SSOT path, so a
/// violation names where to edit. Each segment may be quoted in the header.
fn locate(text: &str, dotted: &str) -> String {
    let segments: Vec<String> = dotted
        .split('.')
        .map(|s| format!("\"?{}\"?", regex::escape(s)))
        .collect();
    Regex::new(&format!(
        r"^\s*\[\[?\s*{}\s*[\].]",
        segments.join(r"\s*\.\s*")
    ))
    .ok()
    .and_then(|re| text.lines().position(|line| re.is_match(line)))
    .map_or_else(|| SSOT.to_owned(), |index| format!("{SSOT}:{}", index + 1))
}

// Syntax facts about seed-db-config.py. The script is parsed, never imported:
// importing it would run its module body, and the facts needed here (the
// allowlist literal, who consults it, which sections it reads by name) are
// all syntactic. Rust applies the coverage policy to them.
const SEED_AST: &str = r#"import ast, json, sys
tree = ast.parse(open(sys.argv[1], 'rb').read(), sys.argv[1])
def literal(node):
    kind = type(node).__name__.lower()
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('set', 'frozenset') and not node.keywords and len(node.args) < 2:
        kind = node.func.id
        if not node.args:
            return kind, []
        node = node.args[0]
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)) and all(isinstance(e, ast.Constant) and isinstance(e.value, str) for e in node.elts):
        return kind, [[e.value, e.lineno] for e in node.elts]
    return kind, None
assigns, functions, calls, parsed, reads = [], {}, [], [], []
for node in tree.body:
    targets = node.targets if isinstance(node, ast.Assign) else [node.target] if isinstance(node, (ast.AnnAssign, ast.AugAssign)) else []
    for target in targets:
        if isinstance(target, ast.Name):
            kind, strings = ('augmented', None) if isinstance(node, ast.AugAssign) else literal(node.value) if node.value is not None else ('declared', None)
            assigns.append({'name': target.id, 'line': node.lineno, 'kind': kind, 'strings': strings})
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        functions[node.name] = sorted({n.id for n in ast.walk(node) if isinstance(n, ast.Name)})
def key(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None
def visit(node, owner):
    for child in ast.iter_child_nodes(node):
        inner = child.name if owner is None and isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)) else owner
        if isinstance(child, ast.Call):
            func = child.func
            if isinstance(func, ast.Name):
                calls.append({'name': func.id, 'owner': inner})
            elif isinstance(func, ast.Attribute) and func.attr == 'get' and isinstance(func.value, ast.Name) and child.args and key(child.args[0]) is not None:
                reads.append({'var': func.value.id, 'key': key(child.args[0])})
        elif isinstance(child, ast.Subscript) and isinstance(child.value, ast.Name) and key(child.slice) is not None:
            reads.append({'var': child.value.id, 'key': key(child.slice)})
        elif isinstance(child, ast.Assign) and isinstance(child.value, ast.Call) and isinstance(child.value.func, ast.Attribute) and isinstance(child.value.func.value, ast.Name):
            for target in child.targets:
                if isinstance(target, ast.Name):
                    parsed.append({'var': target.id, 'module': child.value.func.value.id, 'attr': child.value.func.attr})
        visit(child, inner)
visit(tree, None)
print(json.dumps({'assigns': assigns, 'functions': functions, 'calls': calls, 'parsed': parsed, 'reads': reads}))
"#;

#[derive(serde::Deserialize)]
struct SeedAssign {
    name: String,
    line: usize,
    kind: String,
    strings: Option<Vec<(String, usize)>>,
}

#[derive(serde::Deserialize)]
struct SeedCall {
    name: String,
    owner: Option<String>,
}

#[derive(serde::Deserialize)]
struct SeedParse {
    var: String,
    module: String,
    attr: String,
}

#[derive(serde::Deserialize)]
struct SeedRead {
    var: String,
    key: String,
}

#[derive(serde::Deserialize)]
struct SeedSyntax {
    assigns: Vec<SeedAssign>,
    functions: HashMap<String, Vec<String>>,
    calls: Vec<SeedCall>,
    parsed: Vec<SeedParse>,
    reads: Vec<SeedRead>,
}

fn seed_syntax(ctx: &DriftCtx, python: &str, path: &Path) -> Result<SeedSyntax, String> {
    let output = Command::new(python)
        .args(["-I", "-B", "-c", SEED_AST])
        .arg(path)
        .current_dir(&ctx.root)
        .output()
        .map_err(|e| {
            format!("Required Python interpreter {python} (SSOT drift.lint.python): {e}")
        })?;
    if !output.status.success() {
        return Err(format!(
            "{SEED}: Python AST {}: {}",
            output.status,
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    serde_json::from_slice(&output.stdout)
        .map_err(|e| format!("{SEED}: invalid Python AST receipt: {e}"))
}

/// The seeder's `_CANONICAL_SECTIONS` literal (name, line): the sections
/// get_seeded_sections mirrors into config_kv. Absent, computed or empty is a
/// failure, because then nothing says which sections must reach the database.
fn allowlist(syntax: &SeedSyntax) -> Result<&[(String, usize)], String> {
    let canonical = syntax.assigns.iter().rev().find(|a| a.name == "_CANONICAL_SECTIONS")
        .ok_or_else(|| format!("{SEED}: _CANONICAL_SECTIONS is absent, so which SSOT sections must reach config_kv is unknown"))?;
    let listed = canonical.strings.as_deref().filter(|_| matches!(canonical.kind.as_str(), "set" | "frozenset"))
        .ok_or_else(|| format!("{SEED}:{}: _CANONICAL_SECTIONS must be a literal set of section names (found {}); a computed allowlist cannot be audited", canonical.line, canonical.kind))?;
    if listed.is_empty() {
        return Err(format!(
            "{SEED}:{}: _CANONICAL_SECTIONS is empty, so it would prove nothing",
            canonical.line
        ));
    }
    Ok(listed)
}

/// Every top-level SSOT section must reach the database: either listed in the
/// seeder's `_CANONICAL_SECTIONS` allowlist (what `get_seeded_sections` mirrors
/// into config_kv) or read by name from the parsed SSOT by a dedicated seeding
/// path (verbs -> verb, packages -> package_set). The allowlist is checked both
/// ways: an entry the SSOT no longer has is a rotted name from a dropped table.
/// The legacy gate hard-coded {verbs, packages} as "handled separately"; here
/// that set is derived from the seeder's own named reads, so deleting the verb
/// seeding path un-covers [verbs] instead of leaving it exempt.
fn seed_coverage(ctx: &DriftCtx) -> audit::Audit {
    let doc = audit::ssot(ctx)?;
    let text = audit::read(&ctx.root, SSOT)?;
    let seed = required(ctx, SEED, "the db seeder script")?;
    let syntax = seed_syntax(ctx, &interpreter(&doc)?, &seed)?;
    let allowlist = allowlist(&syntax)?;
    let selector = "get_seeded_sections";
    let names = syntax.functions.get(selector).ok_or_else(|| format!("{SEED}: {selector}() is absent, so nothing selects which SSOT sections reach config_kv"))?;
    if !names.iter().any(|n| n == "_CANONICAL_SECTIONS") {
        return Err(format!("{SEED}: {selector}() does not consult _CANONICAL_SECTIONS, so the allowlist does not decide what is seeded"));
    }
    if !syntax
        .calls
        .iter()
        .any(|c| c.name == selector && c.owner.as_deref() != Some(selector))
    {
        return Err(format!(
            "{SEED}: {selector}() is never called, so no SSOT section is seeded into config_kv"
        ));
    }
    let parsed: BTreeSet<&str> = syntax
        .parsed
        .iter()
        .filter(|p| {
            matches!(p.module.as_str(), "tomllib" | "tomli")
                && matches!(p.attr.as_str(), "load" | "loads")
        })
        .map(|p| p.var.as_str())
        .collect();
    if parsed.is_empty() {
        return Err(format!("{SEED}: the SSOT is never parsed with tomllib.load, so the sections it seeds by name cannot be derived"));
    }
    let named: BTreeSet<&str> = syntax
        .reads
        .iter()
        .filter(|r| parsed.contains(r.var.as_str()))
        .map(|r| r.key.as_str())
        .collect();
    let listed: BTreeSet<&str> = allowlist.iter().map(|(name, _)| name.as_str()).collect();
    let sections = doc
        .as_table()
        .ok_or("mios.toml: the document root is not a table")?;
    let mut errors = Vec::new();
    for name in sections
        .keys()
        .filter(|n| !listed.contains(n.as_str()) && !named.contains(n.as_str()))
    {
        errors.push(format!("{}: section [{name}] is not handled by {SEED}: add it to _CANONICAL_SECTIONS (seeded into config_kv) or seed it by name, or delete the dead section", locate(&text, name)));
    }
    for (name, line) in allowlist
        .iter()
        .filter(|(name, _)| !sections.contains_key(name))
    {
        errors.push(format!("{SEED}:{line}: '{name}' is listed in _CANONICAL_SECTIONS but {SSOT} no longer has it; drop the rotted entry"));
    }
    let label = format!(
        "SSOT sections covered by {SEED} ({} allowlisted, seeded by name: {})",
        listed.len(),
        named.iter().copied().collect::<Vec<_>>().join(", ")
    );
    audit::finish(sections.len(), errors, &label)
}

pub struct RBACTiersCheck;
impl Check for RBACTiersCheck {
    fn id(&self) -> &'static str {
        "check_rbac_tiers"
    }
    fn describe(&self) -> &'static str {
        "Assert RBAC permission tiers are valid and PDP fails closed"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        super::audit::verdict(rbac(ctx))
    }
}

/// Every [agents.*]/[users.*] principal's max_permission ceiling must name a
/// tier from the SSOT catalog [ai].permission_tiers. As in the legacy gate, an
/// absent [agents] or [users] table means no such principals, and an absent or
/// empty max_permission is the documented "no ceiling" ([agents._defaults]);
/// each principal table is examined either way. A present section that is not
/// a table fails, and so does a missing or empty catalog (the legacy fell back
/// to a hard-coded read/write/interactive list).
fn rbac(ctx: &DriftCtx) -> super::audit::Audit {
    let doc = super::audit::ssot(ctx)?;
    let tiers: std::collections::BTreeSet<_> = super::audit::strings(&doc, "ai.permission_tiers")?
        .into_iter()
        .map(|v| v.trim().to_lowercase())
        .collect();
    if tiers.is_empty() || tiers.contains("") {
        return Err("Permission tier catalog is empty or contains an empty identity".into());
    }
    let mut count = 0;
    let mut errors = Vec::new();
    for section in ["agents", "users"] {
        let Some(members) = doc.get(section) else {
            continue;
        };
        let members = members
            .as_table()
            .ok_or_else(|| format!("Invalid SSOT {section}: must be a table of principals"))?;
        for (name, value) in members.iter().filter(|(_, value)| value.is_table()) {
            count += 1;
            if let Some(tier) = value.get("max_permission") {
                match tier.as_str().map(|t| t.trim().to_lowercase()) {
                    Some(t) if t.is_empty() || tiers.contains(&t) => {}
                    _ => errors.push(format!(
                        "{SSOT}: {section}.{name}.max_permission {tier} not in SSOT permission tiers {tiers:?} -- name a catalog tier, or \"\" for no ceiling"
                    )),
                }
            }
        }
    }
    super::audit::finish(count, errors, "RBAC tier identities")
}

pub struct CLISQLSafetyCheck;
impl Check for CLISQLSafetyCheck {
    fn id(&self) -> &'static str {
        "check_cli_sql_safety"
    }
    fn describe(&self) -> &'static str {
        "Assert no dynamic SQL query string concatenation exists in CLI verbs"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict(sql_safety(ctx))
    }
}

/// The retired legacy DB transport (post_sql/_sql/:8000/sql, surreal-ns) and
/// hand-rolled SQL escaping (_pgesc/_pgq) must not reappear in a libexec CLI.
/// Same corpus as the legacy `find -maxdepth 1 -type f`: regular files directly
/// in the directory (a symlink is not followed), minus test_* and *.pyc, with
/// comment-only lines dropped before matching.
fn sql_safety(ctx: &DriftCtx) -> audit::Audit {
    let dir = ctx.root.join(LIBEXEC);
    if !dir.is_dir() {
        return Err(format!("{LIBEXEC}: libexec dir absent -- a tracked deliverable is missing, so this check cannot run"));
    }
    let pattern = Regex::new(r#"(_pgesc\(|_pgq\(|post_sql\(|def _sql\(|/sql"|surreal-ns)"#)
        .map_err(|e| e.to_string())?;
    let mut entries = std::fs::read_dir(&dir)
        .and_then(|entries| entries.collect::<Result<Vec<_>, _>>())
        .map_err(|e| format!("{LIBEXEC}: {e}"))?;
    entries.sort_by_key(|entry| entry.file_name());
    let (mut subjects, mut errors) = (0, Vec::new());
    for entry in entries {
        if !entry
            .file_type()
            .map_err(|e| format!("{LIBEXEC}: {e}"))?
            .is_file()
        {
            continue;
        }
        let name = entry.file_name().to_string_lossy().into_owned();
        if name.starts_with("test_") || name.ends_with(".pyc") {
            continue;
        }
        subjects += 1;
        let bytes = std::fs::read(entry.path()).map_err(|e| format!("{LIBEXEC}/{name}: {e}"))?;
        for (index, line) in String::from_utf8_lossy(&bytes).lines().enumerate() {
            // sed's [[:space:]] also covers the vertical tab.
            if line
                .trim_start_matches(|c: char| c.is_ascii_whitespace() || c == '\x0b')
                .starts_with('#')
            {
                continue;
            }
            if let Some(hit) = pattern.find(line) {
                errors.push(format!("{LIBEXEC}/{name}:{}: `{}` is the retired legacy DB transport or hand-rolled SQL escaping -- use parameterized pg via mios-pg-query --exec-json / mios-db --pg-json", index + 1, hit.as_str()));
            }
        }
    }
    audit::finish(
        subjects,
        errors,
        "libexec CLIs free of legacy DB transport and hand-rolled SQL escaping",
    )
}

pub struct DriftProjectionCheck;
impl Check for DriftProjectionCheck {
    fn id(&self) -> &'static str {
        "check_drift_projection"
    }
    fn describe(&self) -> &'static str {
        "Assert DB to TOML materialization round-trip is lossless"
    }
    fn run(&self, ctx: &DriftCtx) -> Verdict {
        audit::verdict(projection(ctx))
    }
}

// In-memory stand-in for the PostgreSQL tables the seeder writes and the
// materializer reads (config_kv, verb, domain_verb). The subject under test IS
// the behaviour of the two Python scripts, so they run for real against this
// double. INSERT rows are mapped by the statement's own column list, and the
// verb SELECT answers in the statement's own column order, so a reordered
// column cannot silently shift values.
//
// mios_toml is withheld: every attribute raises. The materializer backfills
// any table missing from the DB out of the vendor mios.toml, which is the very
// file the result is compared against -- with that path open (as the legacy
// gate left it) a scope the seeder dropped was restored from the file and the
// round trip could not fail for the defect it exists to catch. With it shut,
// whatever the materializer prints came out of the database.
const HARNESS: &str = r#"import contextlib, io, json, os, re, runpy, sys, traceback, types
seed, materialize = sys.argv[1], sys.argv[2]
store = {"config_kv": {}, "verb": {}, "domain_verb": []}
JSONB = {"value", "params", "examples", "aliases"}
def literal(item, params):
    item = item.strip()
    if item == "%s":
        return next(params)
    item = item.split("::")[0]
    if item.upper() == "NULL":
        return None
    return item[1:-1].replace("''", "'") if item.startswith("'") else int(item)
def insert(query, params):
    cols, vals = re.search(r"\(([^)]*)\)\s*VALUES\s*\(([^)]*)\)", query, re.I | re.S).groups()
    params = iter(params or ())
    row = {c.strip().lower(): literal(v, params) for c, v in zip(cols.split(","), vals.split(","))}
    return {k: json.loads(v) if k in JSONB and isinstance(v, str) else v for k, v in row.items()}
class Cursor:
    rows = ()
    def __enter__(self): return self
    def __exit__(self, *exc): return False
    def fetchall(self):
        rows, self.rows = list(self.rows), []
        return rows
    def fetchone(self):
        return self.rows.pop(0) if self.rows else None
    def execute(self, query, params=None):
        q = " ".join(query.upper().split())
        self.rows = []
        if "INSERT INTO CONFIG_KV" in q:
            row = insert(query, params)
            store["config_kv"][(row["scope"], row["key"], row.get("layer", 0))] = row["value"]
        elif "INSERT INTO VERB " in q or "INSERT INTO VERB(" in q:
            row = insert(query, params)
            store["verb"][row["name"]] = row
        elif "TRUNCATE TABLE DOMAIN_VERB" in q:
            store["domain_verb"] = []
        elif "INSERT INTO DOMAIN_VERB" in q:
            store["domain_verb"].append(insert(query, params))
        elif "SELECT 1 FROM VERB WHERE NAME =" in q:
            self.rows = [(1,)] if params[0] in store["verb"] else []
        elif "SELECT SCOPE, KEY, VALUE::TEXT, LAYER FROM CONFIG_KV" in q:
            self.rows = [(s, k, json.dumps(v), l) for (s, k, l), v in sorted(store["config_kv"].items())]
        elif "SELECT VALUE FROM CONFIG_KV WHERE SCOPE = 'VERBS' AND KEY = '_DEFAULTS'" in q:
            item = store["config_kv"].get(("verbs", "_defaults", 0))
            self.rows = [(item,)] if item is not None else []
        elif "SELECT DOMAIN, DESCRIPTION, ARRAY_AGG" in q:
            groups = {}
            for row in store["domain_verb"]:
                groups.setdefault((row["domain"], row["description"]), []).append(row["verb_name"])
            self.rows = [(d, desc, sorted(v)) for (d, desc), v in sorted(groups.items())]
        elif re.match(r"SELECT .* FROM VERB ", q + " "):
            cols = [c.strip().lower() for c in re.match(r"SELECT (.*?) FROM VERB", q).group(1).split(",")]
            self.rows = [tuple(store["verb"][n].get(c) for c in cols) for n in sorted(store["verb"])]
class Connection:
    def __enter__(self): return self
    def __exit__(self, *exc): return False
    def cursor(self): return Cursor()
    def commit(self): pass
psycopg = types.ModuleType("psycopg")
psycopg.connect = lambda *a, **k: Connection()
sys.modules["psycopg"] = psycopg
def withheld(name):
    raise RuntimeError("the file SSOT is withheld from the DB round trip (mios_toml.%s)" % name)
mios_toml = types.ModuleType("mios_toml")
mios_toml.__getattr__ = withheld
sys.modules["mios_toml"] = mios_toml
def run(path):
    out = io.StringIO()
    sys.argv = [path]
    try:
        with contextlib.redirect_stdout(out):
            runpy.run_path(path, run_name="__main__")
    except SystemExit as e:
        if e.code not in (None, 0):
            return "exit status %r" % (e.code,), out.getvalue()
    except BaseException:
        return traceback.format_exc(), out.getvalue()
    return None, out.getvalue()
receipt = {"seed": run(seed)[0], "materialize": None, "toml": ""}
os.environ.pop("MIOS_TOML", None)
if receipt["seed"] is None:
    receipt["materialize"], receipt["toml"] = run(materialize)
receipt["config_kv_scopes"] = sorted({s for s, _k, _l in store["config_kv"]})
sys.__stdout__.write(json.dumps(receipt))
"#;

#[derive(serde::Deserialize)]
struct Receipt {
    seed: Option<String>,
    materialize: Option<String>,
    toml: String,
    config_kv_scopes: Vec<String>,
}

/// A private scratch directory (cwd/HOME/TMPDIR of the harness), removed on drop.
struct Scratch(PathBuf);
impl Scratch {
    fn new() -> Result<Self, String> {
        static NEXT: std::sync::atomic::AtomicUsize = std::sync::atomic::AtomicUsize::new(0);
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| d.as_nanos());
        let next = NEXT.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "mios-drift-projection-{}-{nanos}-{next}",
            std::process::id()
        ));
        std::fs::create_dir(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        Ok(Self(path))
    }
}
impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

// The verb-table columns the seeder writes and the materializer reads back.
const VERB_FIELDS: [&str; 14] = [
    "sig",
    "desc",
    "tier",
    "permission",
    "cmd",
    "params",
    "section",
    "examples",
    "model_name",
    "hidden",
    "aliases",
    "conflict_group",
    "parallel_limit",
    "max_result_chars",
];

fn entries(value: Option<&Value>) -> BTreeMap<&str, &Value> {
    value
        .and_then(Value::as_table)
        .map(|t| t.iter().map(|(k, v)| (k.as_str(), v)).collect())
        .unwrap_or_default()
}

fn show(value: Option<&Value>) -> String {
    // Any scalar or nested table may contain an operator credential. Report
    // presence/type, never serialize the value into build logs or telemetry.
    value.map_or_else(
        || "absent".to_owned(),
        |v| format!("{} (value withheld)", v.type_str()),
    )
}

/// Python truthiness, which the seeder's `or`/`bool()` coercions apply.
fn truthy(value: Option<&Value>) -> bool {
    match value {
        None => false,
        Some(Value::Boolean(b)) => *b,
        Some(Value::Integer(i)) => *i != 0,
        Some(Value::Float(f)) => *f != 0.0,
        Some(Value::String(s)) => !s.is_empty(),
        Some(Value::Array(a)) => !a.is_empty(),
        Some(Value::Table(t)) => !t.is_empty(),
        Some(Value::Datetime(_)) => true,
    }
}

/// Python `int(value or 0)`, the coercion the seeder applies to the limit columns.
fn py_int(value: Option<&Value>) -> Result<i64, String> {
    match value {
        _ if !truthy(value) => Ok(0),
        Some(Value::Integer(i)) => Ok(*i),
        Some(Value::Boolean(_)) => Ok(1),
        Some(Value::Float(f)) if f.is_finite() => Ok(f.trunc() as i64),
        Some(Value::String(s)) => s
            .trim()
            .parse()
            .map_err(|_| "string is not an integer (value withheld)".to_owned()),
        other => Err(format!("{} is not an integer", show(other))),
    }
}

/// The seeder stores `hidden_aliases or aliases` in the aliases column and the
/// materializer writes it back as hidden_aliases. The legacy gate compared the
/// key `aliases` on both sides, which the materializer never emits, so the
/// column was compared as None == None; here it goes through the same mapping.
fn field<'a>(merged: &BTreeMap<&str, &'a Value>, name: &str) -> Option<&'a Value> {
    if name == "aliases" {
        let hidden = merged.get("hidden_aliases").copied();
        return if truthy(hidden) {
            hidden
        } else {
            merged.get("aliases").copied()
        };
    }
    merged.get(name).copied()
}

/// The legacy equivalences: an empty string, list or table is an absent value,
/// `hidden` compares by truthiness and the limits by integer value.
fn normalize(name: &str, value: Option<&Value>) -> Result<Option<Value>, String> {
    Ok(match name {
        "sig" | "desc" | "cmd" | "section" | "model_name" | "conflict_group" => {
            value.filter(|v| v.as_str() != Some("")).cloned()
        }
        "examples" | "aliases" => value
            .filter(|v| !v.as_array().is_some_and(|a| a.is_empty()))
            .cloned(),
        "params" => value
            .filter(|v| !v.as_table().is_some_and(|t| t.is_empty()))
            .cloned(),
        "hidden" => Some(Value::Boolean(truthy(value))),
        "parallel_limit" | "max_result_chars" => Some(Value::Integer(py_int(value)?)),
        _ => value.cloned(),
    })
}

fn merged<'a>(
    defaults: &BTreeMap<&'a str, &'a Value>,
    verb: &'a toml::map::Map<String, Value>,
) -> BTreeMap<&'a str, &'a Value> {
    let mut out = defaults.clone();
    out.extend(verb.iter().map(|(k, v)| (k.as_str(), v)));
    out
}

type Domains = BTreeMap<String, (Value, Vec<String>)>;

/// routing.domains normalized as the legacy gate did: desc defaults to "", and
/// the verb list compares as a sorted list (domain_verb has no order).
fn domains(root: &Value, side: &str) -> Result<Domains, String> {
    let mut out = BTreeMap::new();
    for (name, value) in entries(
        root.get("routing")
            .and_then(|routing| routing.get("domains")),
    ) {
        let table = value
            .as_table()
            .ok_or_else(|| format!("{side}: routing.domains.{name} is not a table"))?;
        let desc = table
            .get("desc")
            .cloned()
            .unwrap_or_else(|| Value::String(String::new()));
        let mut verbs = match table.get("verbs") {
            None => Vec::new(),
            Some(Value::Array(items)) => items
                .iter()
                .map(|v| v.as_str().map(str::to_owned))
                .collect::<Option<Vec<_>>>()
                .ok_or_else(|| {
                    format!("{side}: routing.domains.{name}.verbs holds a non-string")
                })?,
            Some(other) => {
                return Err(format!(
                    "{side}: routing.domains.{name}.verbs must be a list, found {}",
                    show(Some(other))
                ))
            }
        };
        verbs.sort();
        out.insert(name.to_owned(), (desc, verbs));
    }
    Ok(out)
}

/// Seed the SSOT into an in-memory database with seed-db-config.py, materialize
/// it back with materialize-config-toml.py, and require the result to equal the
/// SSOT for every config_kv scope, for routing.domains (via domain_verb) and for
/// [verbs] (via the verb table, field by field). The legacy gate compared a
/// hand-copied list of eleven scopes. Here the scopes that must round-trip are
/// every SSOT table the seeder's `_CANONICAL_SECTIONS` allowlists (so a seeder
/// that silently skips one at runtime is caught) plus any scope it actually
/// wrote (so nothing seeded goes uncompared).
fn projection(ctx: &DriftCtx) -> audit::Audit {
    let text = audit::read(&ctx.root, SSOT)?;
    let doc: Value = text.parse().map_err(|e: toml::de::Error| {
        format!(
            "{SSOT}: invalid TOML at byte range {:?} (values withheld)",
            e.span()
        )
    })?;
    let python = interpreter(&doc)?;
    let seed = required(ctx, SEED, "the db seeder script")?;
    let materialize = required(ctx, MATERIALIZE, "the DB->TOML materializer")?;
    let syntax = seed_syntax(ctx, &python, &seed)?;
    let listed: BTreeSet<&str> = allowlist(&syntax)?
        .iter()
        .map(|(name, _)| name.as_str())
        .collect();
    let scratch = Scratch::new()?;
    let mut command = Command::new(&python);
    command
        .args(["-I", "-B", "-c", HARNESS])
        .arg(&seed)
        .arg(&materialize)
        .current_dir(&scratch.0)
        .env_clear();
    for key in ["PATH", "SYSTEMROOT"] {
        if let Some(value) = std::env::var_os(key) {
            command.env(key, value);
        }
    }
    for key in ["HOME", "TMPDIR", "TMP", "TEMP"] {
        command.env(key, &scratch.0);
    }
    let output = command
        .env("MIOS_TOML", ctx.root.join(SSOT))
        .output()
        .map_err(|e| {
            format!("Required Python interpreter {python} (SSOT drift.lint.python): {e}")
        })?;
    // Seeder/materializer exceptions, stderr and TOML parse snippets can echo
    // arbitrary SSOT values. Keep the failing component and exit status only.
    let log = format!("{} stderr byte(s) withheld", output.stderr.len());
    if !output.status.success() {
        return Err(format!("DB round-trip harness {}:\n{log}", output.status));
    }
    let receipt: Receipt = serde_json::from_slice(&output.stdout).map_err(|e| {
        format!(
            "DB round-trip harness: invalid receipt at line {}, column {} (values withheld)\n{log}",
            e.line(),
            e.column()
        )
    })?;
    if receipt.seed.is_some() {
        return Err(format!(
            "{SEED}: seeding the in-memory database failed (diagnostic values withheld)\n{log}"
        ));
    }
    if receipt.materialize.is_some() {
        return Err(format!(
            "{MATERIALIZE}: materializing from the seeded database failed (diagnostic values withheld)\n{log}"
        ));
    }
    let mat: Value = receipt
        .toml
        .parse()
        .map_err(|e: toml::de::Error| format!("{MATERIALIZE}: the materialized TOML does not parse at byte range {:?} (values withheld)", e.span()))?;
    let mut scopes: BTreeSet<&str> = entries(Some(&doc))
        .into_iter()
        .filter(|(name, value)| value.is_table() && listed.contains(name))
        .map(|(name, _)| name)
        .collect();
    scopes.extend(receipt.config_kv_scopes.iter().map(String::as_str));
    scopes.remove("verbs");
    if scopes.is_empty() {
        return Err(format!("{SEED}: no config_kv scope is allowlisted for or written by the seeder, so nothing round-tripped through the database"));
    }
    let mut errors = Vec::new();
    for scope in &scopes {
        let (mut want, mut got) = (entries(doc.get(scope)), entries(mat.get(scope)));
        if *scope == "routing" {
            want.remove("domains");
            got.remove("domains");
        }
        let keys: BTreeSet<&str> = want.keys().chain(got.keys()).copied().collect();
        for key in keys.into_iter().filter(|key| want.get(key) != got.get(key)) {
            errors.push(format!(
                "{}: [{scope}].{key} is not lossless through config_kv: SSOT {} -> materialized {}",
                locate(&text, scope),
                show(want.get(key).copied()),
                show(got.get(key).copied())
            ));
        }
    }
    let (want_domains, got_domains) = (domains(&doc, SSOT)?, domains(&mat, MATERIALIZE)?);
    let at = locate(&text, "routing.domains");
    for name in want_domains
        .keys()
        .chain(got_domains.keys())
        .collect::<BTreeSet<_>>()
    {
        match (want_domains.get(name), got_domains.get(name)) {
            (Some((want_desc, want_verbs)), Some((got_desc, got_verbs))) => {
                if want_desc != got_desc { errors.push(format!("{at}: routing.domains.{name}.desc is not lossless through domain_verb: SSOT {} -> materialized {}", show(Some(want_desc)), show(Some(got_desc)))); }
                if want_verbs != got_verbs {
                    let lost: Vec<_> = want_verbs.iter().filter(|v| !got_verbs.contains(v)).collect();
                    let gained: Vec<_> = got_verbs.iter().filter(|v| !want_verbs.contains(v)).collect();
                    errors.push(format!("{at}: routing.domains.{name}.verbs is not lossless through domain_verb: lost {lost:?}, gained {gained:?} (a domain verb must name a seeded [verbs] entry)"));
                }
            }
            (Some(_), None) => errors.push(format!("{at}: routing.domains.{name} is lost through domain_verb (a domain with no seeded verb has no rows)")),
            (None, _) => errors.push(format!("{at}: routing.domains.{name} appears after the round trip but is not in the SSOT")),
        }
    }
    let (want_verbs, got_verbs) = (entries(doc.get("verbs")), entries(mat.get("verbs")));
    if want_verbs.get("_defaults") != got_verbs.get("_defaults") {
        errors.push(format!(
            "{}: [verbs._defaults] is not lossless through config_kv: SSOT {} -> materialized {}",
            locate(&text, "verbs._defaults"),
            show(want_verbs.get("_defaults").copied()),
            show(got_verbs.get("_defaults").copied())
        ));
    }
    let (want_defaults, got_defaults) = (
        entries(want_verbs.get("_defaults").copied()),
        entries(got_verbs.get("_defaults").copied()),
    );
    let mut verbs = 0;
    for (name, value) in want_verbs.iter().filter(|(name, _)| **name != "_defaults") {
        verbs += 1;
        let at = locate(&text, &format!("verbs.{name}"));
        let Some(want) = value.as_table() else {
            errors.push(format!(
                "{at}: verbs.{name} is not a table, so the seeder skips it"
            ));
            continue;
        };
        let Some(got) = got_verbs.get(name).and_then(|v| v.as_table()) else {
            errors.push(format!("{at}: verbs.{name} is lost through the verb table"));
            continue;
        };
        let (want, got) = (merged(&want_defaults, want), merged(&got_defaults, got));
        for column in VERB_FIELDS {
            match (normalize(column, field(&want, column)), normalize(column, field(&got, column))) {
                (Ok(w), Ok(g)) if w == g => {}
                (Ok(w), Ok(g)) => errors.push(format!("{at}: verbs.{name}.{column} is not lossless through the verb table: SSOT {} -> materialized {}", show(w.as_ref()), show(g.as_ref()))),
                (Err(e), _) | (_, Err(e)) => errors.push(format!("{at}: verbs.{name}.{column}: {e}")),
            }
        }
    }
    for name in got_verbs
        .keys()
        .filter(|name| !want_verbs.contains_key(*name))
    {
        errors.push(format!(
            "{}: verbs.{name} appears after the round trip but is not in the SSOT",
            locate(&text, "verbs")
        ));
    }
    let label = format!(
        "DB->TOML round trip lossless ({} config_kv scopes, {} routing domains, {verbs} verbs)",
        scopes.len(),
        want_domains.len()
    );
    audit::finish(scopes.len() + want_domains.len() + verbs, errors, &label)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn rbac_operator_tiers_apply_and_unknown_empty_and_missing_catalog_fail(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let temp = tempfile::tempdir()?;
        std::fs::create_dir_all(temp.path().join("usr/share/mios"))?;
        let path = temp.path().join("usr/share/mios/mios.toml");
        let ctx = DriftCtx::new(temp.path().into(), false);
        std::fs::write(&path, "[ai]\npermission_tiers=['observe','operator']\n[agents.example]\nmax_permission='OPERATOR'\n[users]\n")?;
        assert!(rbac(&ctx).is_ok());
        // An absent [users] means no users; "" (or no key) is the documented "no ceiling".
        std::fs::write(&path, "[ai]\npermission_tiers=['observe','operator']\n[agents._defaults]\nmax_permission=''\n[agents.edge]\nmax_permission=' '\n[agents.example]\nmax_permission='observe'\n[agents.inherits]\nlane='gpu'\n")?;
        assert!(rbac(&ctx)?.contains("4 subject"));
        std::fs::write(&path, "[ai]\npermission_tiers=['observe']\n[agents.example]\nmax_permission='operator'\n[users]\n")?;
        assert!(rbac(&ctx).is_err_and(|e| e.contains("agents.example.max_permission")));
        std::fs::write(&path, "users='alice'\n[ai]\npermission_tiers=['observe']\n[agents.example]\nmax_permission='observe'\n")?;
        assert!(rbac(&ctx).is_err_and(|e| e.contains("Invalid SSOT users")));
        std::fs::write(
            &path,
            "[ai]\npermission_tiers=['observe']\n[agents]\nnot_a_principal='x'\n",
        )?;
        assert!(rbac(&ctx).is_err_and(|e| e.contains("no subjects")));
        std::fs::write(&path, "[ai]\npermission_tiers=[]\n[agents]\n[users]\n")?;
        assert!(rbac(&ctx).is_err());
        std::fs::write(&path, "[agents]\n[users]\n")?;
        assert!(rbac(&ctx).is_err());
        Ok(())
    }

    fn fixture(ssot: &str) -> Result<(tempfile::TempDir, DriftCtx), Box<dyn std::error::Error>> {
        let temp = tempfile::tempdir()?;
        std::fs::create_dir_all(temp.path().join("usr/share/mios"))?;
        std::fs::create_dir_all(temp.path().join(LIBEXEC))?;
        std::fs::write(temp.path().join(SSOT), ssot)?;
        let ctx = DriftCtx::new(temp.path().into(), false);
        Ok((temp, ctx))
    }

    #[cfg(unix)]
    const COVERAGE_SSOT: &str = "[drift.lint]\npython='python3'\n[ports]\nweb=1\n[verbs.fetch]\nsig='fetch(url)'\n[packages.base]\npkgs=['bash']\n";
    #[cfg(unix)]
    const COVERAGE_SEED: &str = "import tomllib\n_CANONICAL_SECTIONS = {\n    'drift', 'ports',\n}\ndef get_seeded_sections(data):\n    return [k for k in data if k in _CANONICAL_SECTIONS]\ndef main():\n    data = tomllib.load(open('x', 'rb'))\n    for sec in get_seeded_sections(data):\n        pass\n    verbs = data.get('verbs') or {}\n    packages = data['packages']\nopen('SHOULD-NOT-EXECUTE', 'w')\n";

    #[cfg(unix)]
    #[test]
    fn seed_coverage_derives_named_sections_and_fails_unseeded_rotted_and_unauditable_allowlists(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let (temp, ctx) = fixture(COVERAGE_SSOT)?;
        assert!(seed_coverage(&ctx).is_err_and(|e| e.contains(SEED) && e.contains("missing")));
        let seed = temp.path().join(SEED);
        std::fs::write(&seed, COVERAGE_SEED)?;
        let passed = seed_coverage(&ctx)?;
        assert!(
            passed.contains("4 subject") && passed.contains("packages, verbs"),
            "{passed}"
        );
        assert!(!temp.path().join("SHOULD-NOT-EXECUTE").exists());
        std::fs::write(
            temp.path().join(SSOT),
            format!("{COVERAGE_SSOT}[orphan]\nx=1\n"),
        )?;
        assert!(seed_coverage(&ctx)
            .is_err_and(|e| e.contains("mios.toml:9: section [orphan] is not handled")));
        std::fs::write(temp.path().join(SSOT), COVERAGE_SSOT)?;
        std::fs::write(
            &seed,
            COVERAGE_SEED.replace("'drift', 'ports',", "'drift', 'ports',\n    'gone',"),
        )?;
        assert!(
            seed_coverage(&ctx).is_err_and(|e| e.contains(&format!("{SEED}:4: 'gone' is listed")))
        );
        std::fs::write(
            &seed,
            COVERAGE_SEED.replace("    verbs = data.get('verbs') or {}\n", ""),
        )?;
        assert!(seed_coverage(&ctx).is_err_and(|e| e.contains("section [verbs] is not handled")));
        std::fs::write(
            &seed,
            COVERAGE_SEED.replace(
                "_CANONICAL_SECTIONS = {\n    'drift', 'ports',\n}",
                "_CANONICAL_SECTIONS = ['drift', 'ports']",
            ),
        )?;
        assert!(seed_coverage(&ctx).is_err_and(|e| e.contains("must be a literal set")));
        std::fs::write(
            &seed,
            COVERAGE_SEED.replace(
                "return [k for k in data if k in _CANONICAL_SECTIONS]",
                "return list(data)",
            ),
        )?;
        assert!(
            seed_coverage(&ctx).is_err_and(|e| e.contains("does not consult _CANONICAL_SECTIONS"))
        );
        std::fs::write(
            &seed,
            COVERAGE_SEED.replace("in get_seeded_sections(data)", "in data"),
        )?;
        assert!(seed_coverage(&ctx).is_err_and(|e| e.contains("never called")));
        std::fs::write(
            &seed,
            COVERAGE_SEED.replace(
                "_CANONICAL_SECTIONS = {\n    'drift', 'ports',\n}",
                "_CANONICAL_SECTIONS = set()",
            ),
        )?;
        assert!(seed_coverage(&ctx).is_err_and(|e| e.contains("is empty")));
        Ok(())
    }

    #[test]
    fn sql_safety_names_file_and_line_skips_comments_tests_and_subdirs_and_fails_empty_or_absent(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let (temp, ctx) = fixture("")?;
        let dir = temp.path().join(LIBEXEC);
        assert!(sql_safety(&ctx).is_err_and(|e| e.contains("no subjects")));
        std::fs::write(dir.join("mios-db"), "#!/bin/bash\n  # post_sql( in a comment is history\nmios-pg-query --exec-json \"$1\"\n")?;
        std::fs::write(dir.join("test_mios_db.py"), "post_sql(\"x\")\n")?;
        std::fs::write(dir.join("stale.pyc"), "_pgesc(\n")?;
        std::fs::create_dir_all(dir.join("nested"))?;
        std::fs::write(dir.join("nested/legacy"), "post_sql(\"x\")\n")?;
        assert!(sql_safety(&ctx)?.contains("1 subject"));
        std::fs::write(
            dir.join("mios-kv"),
            "#!/usr/bin/env python3\nimport json\nq = _pgq(name)  # hand-rolled\n",
        )?;
        assert!(
            sql_safety(&ctx).is_err_and(|e| e.contains(&format!("{LIBEXEC}/mios-kv:3: `_pgq(`")))
        );
        std::fs::write(
            dir.join("mios-kv"),
            "curl -s http://127.0.0.1:8000/sql\" -H surreal-ns: mios\n",
        )?;
        assert!(sql_safety(&ctx).is_err_and(|e| e.contains("mios-kv:1: `/sql\"`")));
        std::fs::remove_dir_all(&dir)?;
        assert!(sql_safety(&ctx).is_err_and(|e| e.contains("libexec dir absent")));
        Ok(())
    }

    #[cfg(unix)]
    const PROJECTION_SSOT: &str = "[drift.lint]\npython='python3'\n[ports]\nweb=8080\nratio=0.5\n[routing]\nmode='council'\n[routing.domains.web]\ndesc='Web'\nverbs=['fetch']\n[verbs._defaults]\ntier='common'\n[verbs.fetch]\nsig='fetch(url)'\ndesc='Fetch a page'\nhidden_aliases=['get']\n";
    // Mirrors the real seeder's statements: config_kv per allowlisted section
    // (routing minus domains), the verbs._defaults literal row, verb rows, and
    // domain_verb rows guarded by the verb lookup.
    #[cfg(unix)]
    const PROJECTION_SEED: &str = r#"import json, os, sys, tomllib, psycopg
_CANONICAL_SECTIONS = {"ports", "routing"}
data = tomllib.load(open(os.environ["MIOS_TOML"], "rb"))
with psycopg.connect("") as conn, conn.cursor() as cur:
    for sec in ("ports", "routing"):
        for k, v in data[sec].items():
            if k != "domains":
                cur.execute("INSERT INTO config_kv (scope, key, value, layer, description) VALUES (%s, %s, %s, 0, %s)", (sec, k, json.dumps(v), "d"))
    verbs = data["verbs"]
    cur.execute("INSERT INTO config_kv (scope, key, value, layer, description) VALUES ('verbs', '_defaults', %s, 0, 'Verbs defaults')", (json.dumps(verbs["_defaults"]),))
    for name, v in verbs.items():
        if name != "_defaults":
            m = dict(verbs["_defaults"], **v)
            aliases = m.get("hidden_aliases") or m.get("aliases")
            cur.execute("INSERT INTO verb (name, sig, desc_default, tier, aliases) VALUES (%s, %s, %s, %s, %s)", (name, m.get("sig", ""), m.get("desc", ""), m.get("tier", "common"), json.dumps(aliases)))
    for dom, d in data["routing"]["domains"].items():
        for verb in d["verbs"]:
            cur.execute("SELECT 1 FROM verb WHERE name = %s", (verb,))
            if cur.fetchone():
                cur.execute("INSERT INTO domain_verb (domain, verb_name, description) VALUES (%s, %s, %s)", (dom, verb, d.get("desc", "")))
"#;
    #[cfg(unix)]
    const PROJECTION_MATERIALIZE: &str = r#"import json, psycopg
out = {}
with psycopg.connect("") as conn, conn.cursor() as cur:
    cur.execute("SELECT scope, key, value::text, layer FROM config_kv ORDER BY layer, scope, key")
    for s, k, v, _ in cur.fetchall():
        out.setdefault(s, {})[k] = json.loads(v)
    cur.execute("SELECT domain, description, array_agg(verb_name ORDER BY verb_name) FROM domain_verb GROUP BY domain, description")
    for dom, desc, verbs in cur.fetchall():
        out.setdefault("routing", {}).setdefault("domains", {})[dom] = {"desc": desc, "verbs": verbs}
    cur.execute("SELECT name, desc_default, sig, aliases FROM verb ORDER BY name")
    for name, desc, sig, aliases in cur.fetchall():
        out["verbs"][name] = {k: v for k, v in (("sig", sig), ("desc", desc), ("hidden_aliases", aliases)) if v}
def emit(prefix, table):
    print("[" + prefix + "]")
    for k, v in table.items():
        if not isinstance(v, dict):
            print(k, "=", json.dumps(v))
    for k, v in table.items():
        if isinstance(v, dict):
            emit(prefix + "." + k, v)
for s, t in out.items():
    emit(s, t)
"#;

    #[cfg(unix)]
    #[test]
    fn projection_diagnostics_do_not_disclose_operator_values(
    ) -> Result<(), Box<dyn std::error::Error>> {
        const SECRET: &str = "synthetic-private-roundtrip-sentinel";
        let config = format!(
            "{PROJECTION_SSOT}\n[auth]\npassword='{SECRET}'\n[auth.nested]\napi_key='{SECRET}'\n"
        );
        let (temp, ctx) = fixture(&config)?;
        let seed = temp.path().join(SEED);
        let materialize = temp.path().join(MATERIALIZE);
        let seeder = PROJECTION_SEED.replace(
            "{\"ports\", \"routing\"}",
            "{\"ports\", \"routing\", \"auth\"}",
        );
        std::fs::write(&seed, &seeder)?;
        std::fs::write(&materialize, PROJECTION_MATERIALIZE)?;
        let drift = projection(&ctx)
            .err()
            .ok_or("the unseeded auth scope must fail")?;
        assert!(
            drift.contains("[auth].password") && drift.contains("[auth].nested"),
            "{drift}"
        );
        assert!(!drift.contains(SECRET), "operator values must be withheld");
        std::fs::write(&seed, format!("{seeder}\nraise RuntimeError('{SECRET}')\n"))?;
        let failure = projection(&ctx)
            .err()
            .ok_or("the broken seeder must fail")?;
        assert!(failure.contains("seeding the in-memory database failed"));
        assert!(
            !failure.contains(SECRET),
            "exception diagnostics must be withheld"
        );
        std::fs::write(
            temp.path().join(SSOT),
            format!("[auth]\npassword = {SECRET}\n"),
        )?;
        let invalid = projection(&ctx).err().ok_or("malformed SSOT must fail")?;
        assert!(invalid.contains("invalid TOML at byte range"));
        assert!(!invalid.contains(SECRET), "parse snippets must be withheld");
        Ok(())
    }

    #[cfg(unix)]
    #[test]
    fn projection_round_trips_through_the_scripts_and_catches_loss_without_vendor_backfill(
    ) -> Result<(), Box<dyn std::error::Error>> {
        let (temp, ctx) = fixture(PROJECTION_SSOT)?;
        assert!(projection(&ctx).is_err_and(|e| e.contains(SEED) && e.contains("missing")));
        let (seed, materialize) = (temp.path().join(SEED), temp.path().join(MATERIALIZE));
        std::fs::write(&seed, PROJECTION_SEED)?;
        std::fs::write(&materialize, PROJECTION_MATERIALIZE)?;
        let passed = projection(&ctx)?;
        assert!(
            passed.contains("2 config_kv scopes, 1 routing domains, 1 verbs"),
            "{passed}"
        );
        // A seeder that drops a key loses it; the file SSOT cannot paper over it.
        std::fs::write(
            &seed,
            PROJECTION_SEED.replace(
                "if k != \"domains\":",
                "if k not in (\"domains\", \"ratio\"):",
            ),
        )?;
        assert!(projection(&ctx).is_err_and(|e| e.contains("mios.toml:3: [ports].ratio is not lossless through config_kv: SSOT float (value withheld) -> materialized absent")));
        // A seeder that skips a whole allowlisted section at runtime is caught too.
        std::fs::write(
            &seed,
            PROJECTION_SEED.replace(
                "for sec in (\"ports\", \"routing\"):",
                "for sec in (\"routing\",):",
            ),
        )?;
        assert!(projection(&ctx).is_err_and(|e| e.contains(
            "[ports].web is not lossless through config_kv: SSOT integer (value withheld) -> materialized absent"
        )));
        // A materializer that falls back to the vendor file is refused, not trusted.
        std::fs::write(&seed, PROJECTION_SEED)?;
        std::fs::write(
            &materialize,
            "import mios_toml\nprint(mios_toml.load_vendor())\n",
        )?;
        assert!(projection(&ctx).is_err_and(|e| e.contains("withheld")));
        // The aliases column round-trips through hidden_aliases, field by field.
        std::fs::write(
            &materialize,
            PROJECTION_MATERIALIZE.replace(
                "(\"hidden_aliases\", aliases)",
                "(\"hidden_aliases\", None)",
            ),
        )?;
        assert!(
            projection(&ctx).is_err_and(|e| e.contains("verbs.fetch.aliases is not lossless")
                && e.contains("SSOT array (value withheld) -> materialized absent"))
        );
        // A domain verb that is not a seeded verb is dropped by domain_verb.
        std::fs::write(&materialize, PROJECTION_MATERIALIZE)?;
        std::fs::write(
            temp.path().join(SSOT),
            PROJECTION_SSOT.replace("verbs=['fetch']", "verbs=['fetch', 'ghost']"),
        )?;
        assert!(projection(&ctx).is_err_and(|e| e.contains(
            "routing.domains.web.verbs is not lossless through domain_verb: lost [\"ghost\"]"
        )));
        std::fs::write(temp.path().join(SSOT), PROJECTION_SSOT)?;
        std::fs::write(
            &seed,
            "_CANONICAL_SECTIONS = {'ports'}\nimport sys\nsys.exit(3)\n",
        )?;
        assert!(projection(&ctx)
            .is_err_and(|e| e
                .contains("seeding the in-memory database failed (diagnostic values withheld)")));
        std::fs::write(&seed, "pass\n")?;
        assert!(projection(&ctx).is_err_and(|e| e.contains("_CANONICAL_SECTIONS is absent")));
        std::fs::write(&seed, "_CANONICAL_SECTIONS = {'retired'}\n")?;
        assert!(projection(&ctx).is_err_and(
            |e| e.contains("no config_kv scope is allowlisted for or written by the seeder")
        ));
        Ok(())
    }
}
