// AI-hint: mios-task verbs over tasks.jsonl -- check/gate, fmt, render, ready/next, set, claim, release, add (mints task_<ULID> once migrated), fold, source, and migration one-shots.
// AI-related: tasks.jsonl, TASKS.md, /usr/share/mios/mios.toml [tasks.store], /usr/lib/mios/schemas/task-record.schema.json, /usr/share/doc/mios/adr/0028-one-canonical-task-list.md
// AI-functions: run, Args, cmd_check, cmd_fmt, cmd_render, cmd_ready, resolve, cmd_set, cmd_claim, cmd_add, mint, cmd_fold, cmd_source, cmd_migrate

use crate::check::{self, State};
use crate::ids;
use crate::migrate::{self, Inputs};
use crate::overrides;
use crate::purge;
use crate::record::{
    self, canonical_status, dumps, find_root, today, write_atomic, Index, Schema, Store,
};
use serde_json::{json, Map, Value};
use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::process::ExitCode;

pub const VERBS: [&str; 14] = [
    "check",
    "fmt",
    "render",
    "ready",
    "next",
    "set",
    "claim",
    "release",
    "add",
    "overrides",
    "source",
    "migrate-canonical",
    "migrate-ids",
    "rewrite-ids",
];

pub const USAGE: &str = "usage: mios-task <verb> [--root DIR] ...
  ID is the typed task_<number> or the opaque task_<ULID> once the list is migrated (migrate-ids), the verbatim id before.
  check [--json] [--only hygiene]      validate tasks.jsonl, its frozen history, its overrides and TASKS.md == render
  check --tree                         fail on an old task id anywhere in the tracked tree (before the migration: on
                                        one migrate-ids could not rewrite)
  fmt [--check]                        rewrite every line canonically (fills missing fields, legacy -> OpenAI words)
  render                               write TASKS.md from tasks.jsonl, keeping the overrides block
  ready|next [--json] [--limit N]      pending and unblocked (overrides applied); next sorts by priority
  set ID [--status S] [--owner O] [--evidence E] [--note TEXT] [--related ID[:TYPE]]... [--exact]
  claim ID LANE [--exact]              compare-and-set owner; exit 2 if another lane owns ID
  release ID LANE [--exact]            (an ID that is also a former id of another task needs --exact;
                                        an ID that is only a former id resolves to the renamed task)
  add --title T --ac TEXT... --positive CMD --negative CMD [--expect E] [--type T]
      (once migrated add mints task_<ULID> and the next number; before it, --id ID is required)
      [--priority P] [--size S] [--workstream W] [--domain D] [--epic E] [--goal G]
      [--depends-on ID]... [--related ID[:TYPE]]... [--note TEXT] [--owner O]
      (--related TYPE: related (default), converted_to, converted_from, references, archived)
  overrides fold ID                    bake ID's override values into tasks.jsonl, delete its lines
  source REPO:PATH                     print a retired list rebuilt byte for byte from the provenance slices
  migrate-canonical --store F --lane F --classification F... --out F --report F
      [--schema F] [--lane-origin MiOS:PATH] [--dry-run]
  migrate-ids [--rewrite-tree] [--map OLD=OLD2]... [--report F] [--mapping-out F] [--dry-run]
                                       give every task an opaque task_<ULID> and a number, purge the old ids
                                        from the list, its frozen history and overrides, and with --rewrite-tree
                                        from every tracked file (ADR-0028); a migrated list is left as it is
  rewrite-ids --mapping F --root DIR [--dry-run]
                                       the same tree rewrite over a sibling repository, from migrate-ids'
                                        --mapping-out (written outside both trees)";

/// Flags with values (repeatable), bare switches and positionals.
pub struct Args {
    pub pos: Vec<String>,
    pub flags: HashMap<String, Vec<String>>,
    pub switches: HashSet<String>,
}

const SWITCHES: [&str; 6] = [
    "--json",
    "--check",
    "--dry-run",
    "--exact",
    "--tree",
    "--rewrite-tree",
];

impl Args {
    pub fn parse(v: &[String]) -> Result<Args, String> {
        let mut a = Args {
            pos: vec![],
            flags: HashMap::new(),
            switches: HashSet::new(),
        };
        let mut i = 0;
        while i < v.len() {
            let x = &v[i];
            if SWITCHES.contains(&x.as_str()) {
                a.switches.insert(x.clone());
            } else if let Some(name) = x.strip_prefix("--") {
                let val = v
                    .get(i + 1)
                    .ok_or(format!("--{name} needs a value"))?
                    .clone();
                a.flags.entry(name.to_string()).or_default().push(val);
                i += 1;
            } else {
                a.pos.push(x.clone());
            }
            i += 1;
        }
        Ok(a)
    }

    pub fn one(&self, k: &str) -> Option<&str> {
        self.flags.get(k).and_then(|v| v.last()).map(String::as_str)
    }

    pub fn all(&self, k: &str) -> Vec<String> {
        self.flags.get(k).cloned().unwrap_or_default()
    }

    pub fn has(&self, k: &str) -> bool {
        self.switches.contains(k)
    }
}

enum Fail {
    /// Exit 1: the data is wrong, or the request was refused.
    Bad(String),
    /// Exit 2: a compare-and-set lost to another owner.
    Owned(String),
    /// Exit 64: usage.
    Usage(String),
}

impl From<String> for Fail {
    fn from(s: String) -> Fail {
        Fail::Bad(s)
    }
}

pub fn run(verb: &str, rest: &[String]) -> ExitCode {
    let r = Args::parse(rest)
        .map_err(Fail::Usage)
        .and_then(|a| dispatch(verb, &a));
    match r {
        Ok(()) => ExitCode::SUCCESS,
        Err(Fail::Bad(e)) => {
            eprintln!("mios-task: {e}");
            ExitCode::from(1)
        }
        Err(Fail::Owned(e)) => {
            eprintln!("mios-task: {e}");
            ExitCode::from(2)
        }
        Err(Fail::Usage(e)) => {
            eprintln!("mios-task: {e}\n{USAGE}");
            ExitCode::from(64)
        }
    }
}

fn root_of(a: &Args) -> Result<PathBuf, Fail> {
    match a.one("root") {
        Some(r) => Ok(PathBuf::from(r)),
        None => {
            let cwd = std::env::current_dir().map_err(|e| Fail::Bad(e.to_string()))?;
            find_root(&cwd).ok_or(Fail::Bad(format!(
                "no {} above {}; pass --root",
                record::SSOT,
                cwd.display()
            )))
        }
    }
}

fn dispatch(verb: &str, a: &Args) -> Result<(), Fail> {
    if verb == "migrate-canonical" {
        return cmd_migrate(a);
    }
    if verb == "rewrite-ids" {
        return purge::cmd_rewrite(a).map_err(Fail::Bad);
    }
    let root = root_of(a)?;
    let st = Store::open(&root)?;
    match verb {
        "check" => cmd_check(&st, a),
        "fmt" => cmd_fmt(&st, a),
        "render" => cmd_render(&st),
        "ready" | "next" => cmd_ready(&st, a, verb == "next"),
        "set" => cmd_set(&st, a),
        "claim" => cmd_claim(&st, a, true),
        "release" => cmd_claim(&st, a, false),
        "add" => cmd_add(&st, a),
        "source" => cmd_source(&st, a),
        "migrate-ids" => purge::cmd(&st, a).map_err(Fail::Bad),
        "overrides" => match a.pos.first().map(String::as_str) {
            Some("fold") => cmd_fold(&st, a),
            _ => Err(Fail::Usage("overrides takes: fold ID".into())),
        },
        _ => Err(Fail::Usage(format!("unknown verb {verb}"))),
    }
}

fn cmd_check(st: &Store, a: &Args) -> Result<(), Fail> {
    let state = State::load(st)?;
    if a.has("--tree") {
        let problems = purge::tree(st, &state)?;
        for p in &problems {
            println!("{p}");
        }
        return if problems.is_empty() {
            println!(
                "tracked tree ok: {}",
                if st.is_purged() {
                    "no purged task id ([tasks.store].purged)"
                } else {
                    "migrate-ids can rewrite every old task id"
                }
            );
            Ok(())
        } else {
            Err(Fail::Bad(format!(
                "check --tree failed: {} old task id(s)",
                problems.len()
            )))
        };
    }
    let only = a.one("only");
    if only.is_some_and(|o| o != "hygiene") {
        return Err(Fail::Usage("--only takes: hygiene".into()));
    }
    let problems = check::check(st, &state, only);
    let notes = if only.is_some() {
        vec![]
    } else {
        check::notes(&state.records)
    };
    if a.has("--json") {
        println!(
            "{}",
            dumps(
                &json!({"ok": problems.is_empty(), "records": state.records.len(),
                          "overrides": state.override_lines().len(), "problems": problems,
                          "notes": notes})
            )
        );
    } else {
        for p in problems.iter().chain(notes.iter()) {
            println!("{p}");
        }
    }
    if problems.is_empty() {
        if !a.has("--json") {
            println!(
                "{} ok: {} record(s), {} override line(s){}",
                st.path,
                state.records.len(),
                state.override_lines().len(),
                if only.is_some() {
                    " (hygiene only)"
                } else {
                    ""
                }
            );
        }
        Ok(())
    } else {
        Err(Fail::Bad(format!(
            "check failed: {} problem(s)",
            problems.len()
        )))
    }
}

/// A default for a schema node: null where allowed, else the type's empty value.
fn default_for(node: &Value) -> Value {
    let types: Vec<&str> = match node.get("type") {
        Some(Value::String(t)) => vec![t.as_str()],
        Some(Value::Array(a)) => a.iter().filter_map(|x| x.as_str()).collect(),
        _ => vec![],
    };
    if types.contains(&"null") {
        return Value::Null;
    }
    match types.first().copied() {
        Some("string") => json!(""),
        Some("array") => json!([]),
        Some("boolean") => json!(false),
        Some("object") => {
            let mut m = Map::new();
            fill(&mut m, node);
            Value::Object(m)
        }
        _ => Value::Null,
    }
}

fn fill(m: &mut Map<String, Value>, node: &Value) {
    let props = node.get("properties").and_then(|p| p.as_object());
    for k in node
        .get("required")
        .and_then(|r| r.as_array())
        .cloned()
        .unwrap_or_default()
    {
        let Some(k) = k.as_str() else { continue };
        let child = props.and_then(|p| p.get(k)).cloned().unwrap_or(Value::Null);
        match m.get_mut(k) {
            None => {
                m.insert(k.to_string(), default_for(&child));
            }
            Some(Value::Object(inner)) => fill(inner, &child),
            _ => {}
        }
    }
}

/// The canonical form of one record: v2 defaults filled, lane words mapped, schema key order.
fn canonical(schema: &Schema, v: &Value) -> Value {
    let mut v = v.clone();
    if let Some(m) = v.as_object_mut() {
        fill(m, &schema.record);
        if let Some(w) = m.get("status").and_then(|x| x.as_str()) {
            if let Some(c) = canonical_status(w) {
                m.insert("status".into(), json!(c));
            }
        }
    }
    schema.canon(&v, &schema.record)
}

fn cmd_fmt(st: &Store, a: &Args) -> Result<(), Fail> {
    let _lock = st.locked()?;
    let state = State::load(st)?;
    let mut out = Vec::new();
    let mut changed = Vec::new();
    for l in &state.lines {
        let Some(v) = &l.value else {
            return Err(Fail::Bad(format!(
                "{}:{}: not JSON; fmt will not guess",
                st.path, l.n
            )));
        };
        if l.raw.contains('\r') {
            changed.push(check::cr_problem(&st.path, l.n));
        }
        if check::is_marker(v) {
            // Outside a string a CR is JSON whitespace, and a raw CR inside one is not JSON: dropping it is exact.
            out.push(l.raw.replace('\r', ""));
            continue;
        }
        let c = dumps(&canonical(&state.schema, v));
        if c != l.raw && !l.raw.contains('\r') {
            changed.push(format!(
                "{}:{} ({}): not canonical -- run mios-task fmt",
                st.path,
                l.n,
                v.get("id").and_then(|x| x.as_str()).unwrap_or("?")
            ));
        }
        out.push(c);
    }
    if a.has("--check") {
        for c in &changed {
            println!("{c}");
        }
        return if changed.is_empty() {
            println!("{} is canonical: {} line(s)", st.path, out.len());
            Ok(())
        } else {
            Err(Fail::Bad(format!(
                "{} line(s) not canonical",
                changed.len()
            )))
        };
    }
    if !changed.is_empty() {
        st.write_lines(&out)?;
    }
    println!("fmt: {} of {} line(s) rewritten", changed.len(), out.len());
    Ok(())
}

/// Write TASKS.md from the records in `state` (whose block bytes are kept), unless that would clobber a broken block.
fn write_doc(
    st: &Store,
    records: &[Value],
    state: &State,
    inner: Option<String>,
) -> Result<(), Fail> {
    if state.md.is_some() {
        if let Err(e) = &state.block {
            return Err(Fail::Bad(format!(
                "{} has a broken overrides block; fix it before rendering:\n{}",
                st.doc,
                e.join("\n")
            )));
        }
    }
    let inner = inner.unwrap_or_else(|| state.inner());
    let lines = overrides::parse_block(
        &st.doc,
        &format!(
            "{}\n{}\n{inner}{}\n",
            overrides::BANNER,
            overrides::BEGIN,
            overrides::END
        ),
    )
    .map(|b| b.lines)
    .map_err(|e| Fail::Bad(e.join("\n")))?;
    let idx = Index::new(records, state.purged.then_some(&state.ids));
    let eff = overrides::apply(records, &lines, &idx);
    write_atomic(
        &st.doc_file(),
        &overrides::render(&st.path, &eff, &inner, &idx),
    )?;
    Ok(())
}

fn cmd_render(st: &Store) -> Result<(), Fail> {
    let _lock = st.locked()?;
    let state = State::load(st)?;
    write_doc(st, &state.records, &state, None)?;
    println!(
        "{} rendered from {} ({} record(s))",
        st.doc,
        st.path,
        state.records.len()
    );
    Ok(())
}

fn prio_rank(r: &Value) -> u8 {
    match r.get("priority").and_then(|x| x.as_str()) {
        Some("P0") => 0,
        Some("P1") => 1,
        Some("P2") => 2,
        Some("P3") => 3,
        _ => 4,
    }
}

fn cmd_ready(st: &Store, a: &Args, next: bool) -> Result<(), Fail> {
    let state = State::load(st)?;
    let eff = state.effective();
    let status: HashMap<&str, &str> = eff
        .records
        .iter()
        .filter_map(|r| Some((r.get("id")?.as_str()?, r.get("status")?.as_str()?)))
        .collect();
    let mut out: Vec<&Value> = eff
        .records
        .iter()
        .filter(|r| r.get("status").and_then(|x| x.as_str()) == Some("pending"))
        .filter(|r| r.get("type").and_then(|x| x.as_str()) != Some("epic"))
        .filter(|r| {
            r.get("depends_on")
                .and_then(|d| d.as_array())
                .is_none_or(|d| {
                    d.iter().all(|x| {
                        matches!(
                            status.get(x.as_str().unwrap_or("")),
                            Some(&"completed") | Some(&"cancelled")
                        )
                    })
                })
        })
        .collect();
    if next {
        out.sort_by_key(|r| prio_rank(r)); // stable: file order within a priority
    }
    let total = out.len();
    let limit = match a.one("limit") {
        Some(n) => n
            .parse::<usize>()
            .map_err(|_| Fail::Usage(format!("--limit {n} is not a number")))?,
        None => total,
    };
    out.truncate(limit);
    if a.has("--json") {
        let v: Vec<Value> = out
            .iter()
            .map(|r| {
                if state.purged {
                    json!({"id": r["id"], "object": state.ids.object, "number": r["number"],
                           "priority": r["priority"], "owner": r["owner"], "title": r["title"]})
                } else {
                    json!({"id": r["id"], "priority": r["priority"], "owner": r["owner"], "title": r["title"]})
                }
            })
            .collect();
        println!("{}", dumps(&Value::Array(v)));
    } else {
        for r in &out {
            println!(
                "{}\t{}\t{}",
                state.label(r),
                r["priority"].as_str().unwrap_or("-"),
                r["title"].as_str().unwrap_or("")
            );
        }
        if total > out.len() {
            println!("(+{} more)", total - out.len());
        }
    }
    Ok(())
}

/// Load under the lock, let `f` change records by index, write tasks.jsonl and re-render TASKS.md.
fn mutate<F>(st: &Store, f: F) -> Result<(), Fail>
where
    F: FnOnce(&State, &mut Vec<Value>, &mut HashSet<usize>) -> Result<Option<String>, Fail>,
{
    let _lock = st.locked()?;
    let state = State::load(st)?;
    if let Some(l) = state.lines.iter().find(|l| l.value.is_none()) {
        return Err(Fail::Bad(format!(
            "{}:{}: not JSON; refusing to rewrite the file",
            st.path, l.n
        )));
    }
    if let Some(l) = state.lines.iter().find(|l| l.raw.contains('\r')) {
        return Err(Fail::Bad(format!(
            "{}; refusing to rewrite the file around it",
            check::cr_problem(&st.path, l.n)
        )));
    }
    let mut recs = state.records.clone();
    let mut touched = HashSet::new();
    let inner = f(&state, &mut recs, &mut touched)?;
    // Untouched lines (a dialect marker included) keep their bytes; changed and appended records are
    // written canonically, so the dev-loop toolkit and mios-task produce the same line.
    let mut lines: Vec<String> = Vec::with_capacity(state.lines.len() + 1);
    let mut i = 0usize;
    for l in &state.lines {
        match &l.value {
            Some(v) if check::is_marker(v) => lines.push(l.raw.clone()),
            _ => {
                lines.push(if touched.contains(&i) {
                    dumps(&canonical(&state.schema, &recs[i]))
                } else {
                    l.raw.clone()
                });
                i += 1;
            }
        }
    }
    for r in &recs[i..] {
        lines.push(dumps(&canonical(&state.schema, r)));
    }
    st.write_lines(&lines)?;
    write_doc(st, &recs, &state, inner)
}

/// The record an edit by `id` means. Once migrated: the typed task_<number> or the opaque task_<ULID>, nothing
/// else. Before: the verbatim id; a former id (provenance.aliases) of exactly one record resolves to it, and an
/// id that is a task AND a former id of another is refused unless `exact` -- never a silent retarget.
fn resolve(state: &State, recs: &[Value], id: &str, exact: bool) -> Result<usize, Fail> {
    if state.purged {
        return Index::new(recs, Some(&state.ids))
            .find(id)
            .ok_or(Fail::Bad(format!(
                "no task {id} -- name it {}_<number> or by its opaque id",
                state.ids.object
            )));
    }
    let hit = recs
        .iter()
        .position(|r| r.get("id").and_then(|x| x.as_str()) == Some(id));
    if exact {
        return hit.ok_or(Fail::Bad(format!("no task {id}")));
    }
    let renamed: Vec<usize> = recs
        .iter()
        .enumerate()
        .filter(|(i, r)| {
            Some(*i) != hit
                && r.get("provenance")
                    .and_then(|p| p.get("aliases"))
                    .and_then(|x| x.as_array())
                    .is_some_and(|a| a.iter().any(|x| x.as_str() == Some(id)))
        })
        .map(|(i, _)| i)
        .collect();
    let name = |i: usize| recs[i]["id"].as_str().unwrap_or("?").to_string();
    match (hit, renamed.as_slice()) {
        (Some(i), []) => Ok(i),
        (None, [j]) => {
            eprintln!(
                "mios-task: {id} is a former id of {}; editing {}",
                name(*j),
                name(*j)
            );
            Ok(*j)
        }
        (None, []) => Err(Fail::Bad(format!("no task {id}"))),
        (h, many) => {
            let to: Vec<String> = many.iter().map(|&j| name(j)).collect();
            Err(Fail::Bad(format!(
                "{id} is ambiguous: {}it is a former id of {} (renamed by the migration, ADR-0028); name {} or pass --exact{}",
                if h.is_some() { format!("it is task {id}, and ") } else { String::new() },
                to.join(", "),
                to.join(" or "),
                if h.is_some() { format!(" to edit task {id}") } else { String::new() }
            )))
        }
    }
}

/// The --related ID[:TYPE] edges, each naming a task by any spelling the index knows and linked by its id.
fn related_edges(a: &Args, idx: &Index) -> Result<Vec<Value>, Fail> {
    const TYPES: [&str; 5] = [
        "related",
        "converted_to",
        "converted_from",
        "references",
        "archived",
    ];
    let mut out = Vec::new();
    for x in a.all("related") {
        let (key, ty) = match x.rsplit_once(':') {
            Some((k, t)) if TYPES.contains(&t) => (k, t),
            _ => (x.as_str(), "related"),
        };
        let i = idx
            .find(key)
            .ok_or(Fail::Bad(format!("related unknown {key}")))?;
        let e = json!({"id": idx.ids[i], "type": ty});
        if !out.contains(&e) {
            out.push(e);
        }
    }
    Ok(out)
}

fn cmd_set(st: &Store, a: &Args) -> Result<(), Fail> {
    let id = a
        .pos
        .first()
        .cloned()
        .ok_or(Fail::Usage("set needs an ID".into()))?;
    if ["status", "owner", "evidence", "note", "related"]
        .iter()
        .all(|k| a.one(k).is_none())
    {
        return Err(Fail::Usage(
            "set needs --status, --owner, --evidence, --note or --related".into(),
        ));
    }
    let status = match a.one("status") {
        Some(w) => Some(canonical_status(w).ok_or(Fail::Usage(format!(
            "--status {w}: not a status word (pending|in_progress|completed|incomplete|cancelled, or open|done|blocked)"
        )))?),
        None => None,
    };
    let exact = a.has("--exact");
    let mut name = id.clone();
    mutate(st, |state, recs, touched| {
        let i = resolve(state, recs, &id, exact)?;
        let idx = Index::new(recs, state.purged.then_some(&state.ids));
        name = idx.labels[i].clone();
        let edges = related_edges(a, &idx)?;
        let r = recs[i]
            .as_object_mut()
            .ok_or(Fail::Bad(format!("{id} is not an object")))?;
        if let Some(n) = a.one("note") {
            r.insert("notes".into(), json!(n));
        }
        if !edges.is_empty() {
            let rel = r
                .entry("related")
                .or_insert_with(|| json!([]))
                .as_array_mut()
                .ok_or(Fail::Bad(format!("{id}: related is not a list")))?;
            for e in edges {
                if !rel.contains(&e) {
                    rel.push(e);
                }
            }
        }
        if let Some(s) = status {
            r.insert("status".into(), json!(s));
        }
        if let Some(o) = a.one("owner") {
            r.insert("owner".into(), json!(o));
        }
        if let Some(e) = a.one("evidence") {
            r.insert("verification_evidence".into(), json!(e));
        }
        let ev = r
            .get("verification_evidence")
            .and_then(|x| x.as_str())
            .unwrap_or("");
        if r.get("status").and_then(|x| x.as_str()) == Some("completed") && ev.trim().is_empty() {
            return Err(Fail::Bad(format!(
                "{id}: completed needs --evidence citing both controls"
            )));
        }
        r.insert("updated".into(), json!(today()));
        touched.insert(i);
        Ok(None)
    })?;
    println!("{name} updated");
    Ok(())
}

fn cmd_claim(st: &Store, a: &Args, claim: bool) -> Result<(), Fail> {
    let (Some(id), Some(lane)) = (a.pos.first().cloned(), a.pos.get(1).cloned()) else {
        return Err(Fail::Usage("claim/release need ID LANE".into()));
    };
    if lane.trim().is_empty() {
        return Err(Fail::Usage("LANE must not be empty".into()));
    }
    let mut noop = false;
    let exact = a.has("--exact");
    let mut name = id.clone();
    mutate(st, |state, recs, touched| {
        let i = resolve(state, recs, &id, exact)?;
        name = Index::new(recs, state.purged.then_some(&state.ids)).labels[i].clone();
        let eff = state.effective();
        let owner = eff.records[i]
            .get("owner")
            .and_then(|x| x.as_str())
            .unwrap_or("")
            .to_string();
        let want = if claim {
            if !owner.is_empty() && owner != lane {
                return Err(Fail::Owned(format!(
                    "{id} is owned by {owner}; not claimed"
                )));
            }
            if owner == lane {
                noop = true;
                return Ok(None);
            }
            lane.clone()
        } else {
            if owner != lane {
                return Err(Fail::Owned(format!(
                    "{id} is owned by {:?}, not {lane}; not released",
                    owner
                )));
            }
            String::new()
        };
        let r = recs[i]
            .as_object_mut()
            .ok_or(Fail::Bad(format!("{id} is not an object")))?;
        r.insert("owner".into(), json!(want));
        r.insert("updated".into(), json!(today()));
        touched.insert(i);
        Ok(None)
    })?;
    match (claim, noop) {
        (true, true) => println!("{name} already owned by {lane}"),
        (true, false) => println!("{name} claimed by {lane}"),
        _ => println!("{name} released by {lane}"),
    }
    Ok(())
}

/// A new opaque id: the ULID of `ms` (never earlier than the newest id in the list, so ids keep sorting in
/// number order) and 80 bits hashed from the clock, the process and the record.
fn mint(ids: &ids::Ids, recs: &[Value], title: &str, number: u64) -> String {
    let now = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default();
    let newest = recs
        .iter()
        .filter_map(|r| ids.ms_of(r.get("id")?.as_str()?))
        .max();
    let ms = match newest {
        Some(m) if m >= now.as_millis() as u64 => m + 1,
        _ => now.as_millis() as u64,
    };
    let seed = format!(
        "{}\0{}\0{title}\0{number}",
        now.as_nanos(),
        std::process::id()
    );
    use sha2::{Digest, Sha256};
    ids.opaque(&ids::ulid(ms, &Sha256::digest(seed.as_bytes())))
}

fn cmd_add(st: &Store, a: &Args) -> Result<(), Fail> {
    let need = |k: &str| {
        a.one(k)
            .filter(|s| !s.trim().is_empty())
            .map(str::to_string)
            .ok_or(Fail::Usage(format!("add needs --{k}")))
    };
    let purged = st.is_purged();
    let given = if purged {
        if a.one("id").is_some() {
            return Err(Fail::Usage(
                "--id is gone: the list is migrated, so add mints the opaque task_<ULID> and the next number itself (ADR-0028)".into(),
            ));
        }
        None
    } else {
        Some(need("id")?)
    };
    let title = need("title")?;
    let positive = need("positive")?;
    let negative = need("negative")?;
    let ac = a.all("ac");
    if ac.iter().all(|x| x.trim().is_empty()) {
        return Err(Fail::Usage("add needs at least one --ac".into()));
    }
    let status = canonical_status(a.one("status").unwrap_or("pending"))
        .ok_or(Fail::Usage("--status: not a status word".into()))?;
    let o = |k: &str| a.one(k).map(|s| json!(s)).unwrap_or(Value::Null);
    let day = today();
    let mut shown = String::new();
    mutate(st, |state, recs, _| {
        let idx = Index::new(recs, purged.then_some(&state.ids));
        let mut errs = Vec::new();
        // A task named by any spelling the index knows is linked by its id.
        let mut link = |d: &str, what: &str| -> String {
            match idx.find(d) {
                Some(i) => idx.ids[i].clone(),
                None => {
                    errs.push(format!("{what} unknown {d}"));
                    d.to_string()
                }
            }
        };
        let deps: Vec<String> = a
            .all("depends-on")
            .iter()
            .map(|d| link(d, "depends_on"))
            .collect();
        let epic = match a.one("epic") {
            Some(e) if !e.is_empty() => link(e, "epic"),
            _ => String::new(),
        };
        let related = match related_edges(a, &idx) {
            Ok(r) => r,
            Err(Fail::Bad(e)) | Err(Fail::Usage(e)) | Err(Fail::Owned(e)) => {
                errs.push(e);
                vec![]
            }
        };
        let (id, number) = match &given {
            Some(id) => (id.clone(), None),
            None => {
                let high = recs
                    .iter()
                    .filter_map(|r| r.get("number").and_then(|x| x.as_u64()))
                    .max()
                    .unwrap_or(0)
                    .max(st.numbered.unwrap_or(0));
                (mint(&state.ids, recs, &title, high + 1), Some(high + 1))
            }
        };
        if recs
            .iter()
            .any(|r| r.get("id").and_then(|x| x.as_str()) == Some(&id))
        {
            return Err(Fail::Bad(format!("duplicate id {id}")));
        }
        shown = match number {
            Some(n) => format!("{} ({id})", state.ids.typed(n)),
            None => id.clone(),
        };
        let mut rec = json!({
            "id": id, "type": a.one("type").unwrap_or("task"), "title": title, "status": status,
            "owner": a.one("owner").unwrap_or(""), "epic": epic,
            "goal": a.one("goal").unwrap_or(""), "priority": o("priority"), "size": o("size"),
            "workstream": o("workstream"), "domain": o("domain"), "depends_on": deps,
            "related": related, "acceptance_criteria": ac,
            "verification": {"positive_cmd": positive, "negative_control_cmd": negative,
                             "negative_expect": o("expect"), "statements": []},
            "verification_evidence": a.one("evidence").unwrap_or(""),
            "details": {"what_how": null, "where": null, "why": null, "do_not": null},
            "links": [], "notes": a.one("note").unwrap_or(""), "extra": [], "created": day,
            "updated": day, "provenance": null,
        });
        if let (Some(n), Some(m)) = (number, rec.as_object_mut()) {
            m.insert("object".into(), json!(state.ids.object));
            m.insert("number".into(), json!(n));
        }
        state
            .schema
            .conforms(&rec, &state.schema.record, &shown, &mut errs);
        if status == "completed" && a.one("evidence").is_none_or(|e| e.trim().is_empty()) {
            errs.push(format!("{shown}: completed without verification_evidence"));
        }
        if !errs.is_empty() {
            return Err(Fail::Bad(errs.join("\n")));
        }
        recs.push(rec);
        Ok(None)
    })?;
    println!("{shown} added");
    Ok(())
}

fn cmd_fold(st: &Store, a: &Args) -> Result<(), Fail> {
    let id = a
        .pos
        .get(1)
        .cloned()
        .ok_or(Fail::Usage("overrides fold needs an ID".into()))?;
    let mut name = id.clone();
    mutate(st, |state, recs, touched| {
        let block = state.block.as_ref().map_err(|e| Fail::Bad(e.join("\n")))?;
        let i = resolve(state, recs, &id, true)?;
        let idx = &state.idx;
        name = idx.labels[i].clone();
        // An override line names the task by any spelling the index knows.
        let mine: Vec<&overrides::OverrideLine> = block
            .lines
            .iter()
            .filter(|l| {
                l.value
                    .as_ref()
                    .and_then(|v| v.get("id"))
                    .and_then(|x| x.as_str())
                    .and_then(|k| idx.find(k))
                    == Some(i)
            })
            .collect();
        if mine.is_empty() {
            return Err(Fail::Bad(format!("no override line names {id}")));
        }
        let eff = overrides::apply(&state.records, &block.lines, idx);
        let fields = eff.overridden.get(&idx.ids[i]).cloned().unwrap_or_default();
        let r = recs[i]
            .as_object_mut()
            .ok_or(Fail::Bad(format!("{id} is not an object")))?;
        for f in &fields {
            r.insert(f.clone(), eff.records[i][f.as_str()].clone());
        }
        r.insert("updated".into(), json!(today()));
        touched.insert(i);
        let drop: HashSet<usize> = mine.iter().map(|l| l.n).collect();
        // Keep every other byte of the block; remove only this task's lines.
        let md = state.md.clone().unwrap_or_default();
        let mut inner = String::new();
        let mut inside = false;
        for (n, l) in md.split_inclusive('\n').enumerate() {
            let t = l.trim_end_matches(['\n', '\r']);
            if t == overrides::END {
                break;
            }
            if inside && !drop.contains(&(n + 1)) {
                inner.push_str(l);
            }
            if t == overrides::BEGIN {
                inside = true;
            }
        }
        Ok(Some(inner))
    })?;
    println!("{name}: override folded into the record and its line(s) removed");
    Ok(())
}

fn cmd_source(st: &Store, a: &Args) -> Result<(), Fail> {
    let file = a.pos.first().cloned().ok_or(Fail::Usage(
        "source needs REPO:PATH, e.g. MiOS:AGY-TASKS.md".into(),
    ))?;
    let state = State::load(st)?;
    let all = check::slices(&state.records, &state.idx);
    let sl = all.get(&file).ok_or(Fail::Bad(format!(
        "{file}: no record carries a slice of it; frozen lists: {}",
        st.frozen
            .iter()
            .map(|f| f.source.as_str())
            .collect::<Vec<_>>()
            .join(", ")
    )))?;
    let want = st.frozen.iter().find(|f| f.source == file);
    let text = check::rebuild(&file, sl, want).map_err(|e| Fail::Bad(e.join("\n")))?;
    use std::io::Write;
    std::io::stdout()
        .write_all(text.as_bytes())
        .map_err(|e| Fail::Bad(e.to_string()))?;
    Ok(())
}

fn read_json(p: &Path) -> Result<Value, Fail> {
    let t = fs::read_to_string(p).map_err(|e| Fail::Bad(format!("{}: {e}", p.display())))?;
    serde_json::from_str(&t).map_err(|e| Fail::Bad(format!("{}: {e}", p.display())))
}

fn cmd_migrate(a: &Args) -> Result<(), Fail> {
    let need = |k: &str| {
        a.one(k)
            .map(PathBuf::from)
            .ok_or(Fail::Usage(format!("migrate-canonical needs --{k}")))
    };
    let (store_p, lane_arg, out, report) =
        (need("store")?, need("lane")?, need("out")?, need("report")?);
    // The retired store migrates to the legacy shape (verbatim ids); migrate-ids takes it from there.
    let schema = match a.one("schema") {
        Some(s) => Schema::load(Path::new(s))?,
        None => Schema::from_record(ids::legacy_record_schema())?,
    };
    let store = record::read_lines(&store_p)?
        .into_iter()
        .map(|l| {
            l.value
                .ok_or(format!("{}:{}: not JSON", store_p.display(), l.n))
        })
        .collect::<Result<Vec<_>, _>>()?;
    let lane = record::read_lines(&lane_arg)?
        .into_iter()
        .map(|l| match l.value {
            Some(v) => Ok((l.raw, v)),
            None => Err(format!("{}:{}: not JSON", lane_arg.display(), l.n)),
        })
        .collect::<Result<Vec<_>, _>>()?;
    let mut classes: HashMap<String, String> = HashMap::new();
    let files = a.all("classification");
    if files.is_empty() {
        return Err(Fail::Usage(
            "migrate-canonical needs --classification".into(),
        ));
    }
    for f in files {
        let v = read_json(Path::new(&f))?;
        let items = v
            .as_array()
            .ok_or(Fail::Bad(format!("{f}: not a JSON list of {{key, class}}")))?;
        for d in items {
            let (Some(k), Some(c)) = (
                d.get("key").and_then(|x| x.as_str()),
                d.get("class").and_then(|x| x.as_str()),
            ) else {
                return Err(Fail::Bad(format!("{f}: an entry lacks key or class")));
            };
            if let Some(prev) = classes.insert(k.to_string(), c.to_string()) {
                if prev != c {
                    return Err(Fail::Bad(format!("{k}: decided both {prev} and {c}")));
                }
            }
        }
    }
    // The lane file's origin as the retired store spells it: given, or the lane path under the root.
    let lane_origin = match a.one("lane-origin") {
        Some(o) => o.to_string(),
        None => {
            let root = root_of(a).ok();
            let rel = root
                .as_ref()
                .and_then(|r| lane_arg.strip_prefix(r).ok())
                .unwrap_or(&lane_arg);
            format!("MiOS:{}", rel.to_string_lossy().trim_start_matches("./"))
        }
    };
    let lane_text = fs::read_to_string(&lane_arg)
        .map_err(|e| Fail::Bad(format!("{}: {e}", lane_arg.display())))?;
    if lane_text.contains('\r') {
        return Err(Fail::Bad(format!(
            "{}: CRLF line ends; the lane file is pinned eol=lf",
            lane_arg.display()
        )));
    }
    let inp = Inputs {
        store,
        lane,
        lane_text,
        lane_origin,
        classes,
    };
    let o = migrate::migrate(&inp, &schema)?;
    let mut rep = o.report;
    rep["dry_run"] = json!(a.has("--dry-run"));
    write_atomic(
        &report,
        &(serde_json::to_string_pretty(&rep).map_err(|e| e.to_string())? + "\n"),
    )?;
    if !a.has("--dry-run") {
        let mut s = String::new();
        for l in &o.lines {
            s.push_str(l);
            s.push('\n');
        }
        write_atomic(&out, &s)?;
    }
    println!(
        "migrate-canonical: store_in {} = kept {} + excluded_toolkit {} + excluded_devloop_origin {}; lane_in {} = merged {} + lane_only {}; out_lines {}{}",
        rep["store_in"], rep["kept"], rep["excluded_toolkit"], rep["excluded_devloop_origin"],
        rep["lane_in"], rep["merged_into_store"], rep["lane_only"], rep["out_lines"],
        if a.has("--dry-run") { " (dry run: nothing written but the report)" } else { "" }
    );
    Ok(())
}
