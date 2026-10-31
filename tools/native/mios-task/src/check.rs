// AI-hint: `mios-task check` -- every rule of ADR-0028 over tasks.jsonl, its frozen provenance slices, its overrides and TASKS.md == render; once migrated also the number rules (unique, creation order, never reissued) and no purged id anywhere in the list.
// AI-related: tasks.jsonl, TASKS.md, /usr/lib/mios/schemas/task-record.schema.json, /usr/share/mios/mios.toml [tasks.store], automation/98-drift-checks.sh
// AI-functions: State::load, check, numbers, purged_in_list, hygiene, cycles, retired_stores, first_difference, sha, Slice, slices, rebuild, verify, cr_problem, migrated_shape, identity, archived_edges, former_ids, notes

use crate::overrides::{self, Block, Effective};
use crate::record::ids::{Ids, Shapes};
use crate::record::{dumps, lane_word, Frozen, Index, Line, Schema, Store};
use regex::Regex;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs;
use std::path::Path;
use std::sync::OnceLock;

/// The dev-loop toolkit's status-dialect marker line; not a task. Only the OpenAI dialect is MiOS's.
pub const DIALECT_KEY: &str = "_dialect";

pub fn is_marker(v: &Value) -> bool {
    v.as_object()
        .is_some_and(|o| o.len() == 1 && o.contains_key(DIALECT_KEY))
}

/// Everything a reader needs: the lines, their parses, the schema, the id index, and TASKS.md with its block.
pub struct State {
    pub lines: Vec<Line>,
    pub records: Vec<Value>,
    pub schema: Schema,
    pub md: Option<String>,
    pub block: Result<Block, Vec<String>>,
    /// The id spellings; `purged` once `mios-task migrate-ids` has run.
    pub ids: Ids,
    pub purged: bool,
    pub idx: Index,
}

impl State {
    pub fn load(st: &Store) -> Result<State, String> {
        let file = Schema::load(&st.schema_file())?;
        let ids = Ids::from_schema(&file.record);
        let schema = if st.is_purged() {
            file
        } else {
            st.load_schema()?
        };
        let lines = st.read()?;
        let records: Vec<Value> = lines
            .iter()
            .filter_map(|l| l.value.clone())
            .filter(|v| !is_marker(v))
            .collect();
        let md = fs::read_to_string(st.doc_file()).ok();
        let block = match &md {
            Some(t) => overrides::parse_block(&st.doc, t),
            None => Err(vec![format!(
                "{} is missing -- run: mios-task render",
                st.doc
            )]),
        };
        let purged = st.is_purged();
        let idx = Index::new(&records, purged.then_some(&ids));
        Ok(State {
            lines,
            records,
            schema,
            md,
            block,
            ids,
            purged,
            idx,
        })
    }

    /// How a record is named to people: its typed id once migrated, its verbatim id before.
    pub fn label(&self, r: &Value) -> String {
        self.idx.label(id_of(r))
    }

    pub fn override_lines(&self) -> &[overrides::OverrideLine] {
        match &self.block {
            Ok(b) => &b.lines,
            Err(_) => &[],
        }
    }

    pub fn effective(&self) -> Effective {
        overrides::apply(&self.records, self.override_lines(), &self.idx)
    }

    /// The bytes render keeps between the markers: the existing block, or the one-comment default.
    pub fn inner(&self) -> String {
        match &self.block {
            Ok(b) => b.inner.clone(),
            _ => overrides::empty_block(
                self.idx
                    .labels
                    .first()
                    .map(String::as_str)
                    .unwrap_or("task"),
            ),
        }
    }
}

fn id_of(v: &Value) -> &str {
    v.get("id").and_then(|x| x.as_str()).unwrap_or("?")
}

fn hygiene_rx() -> &'static Regex {
    static R: OnceLock<Regex> = OnceLock::new();
    R.get_or_init(|| {
        // A Temp *path* (after a separator), not the English word; a session id is a UUID.
        Regex::new(r"AppData|[\\/]Temp\b|[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
            .expect("static regex")
    })
}

fn walk_strings(v: &Value, path: &str, out: &mut Vec<(String, String)>) {
    match v {
        Value::String(s) => out.push((path.to_string(), s.clone())),
        Value::Array(a) => {
            for (i, x) in a.iter().enumerate() {
                walk_strings(x, &format!("{path}[{i}]"), out);
            }
        }
        Value::Object(o) => {
            for (k, x) in o {
                let p = if path.is_empty() {
                    k.clone()
                } else {
                    format!("{path}.{k}")
                };
                walk_strings(x, &p, out);
            }
        }
        _ => {}
    }
}

/// Coordination hygiene: no AppData/Temp path or session UUID in a live record field or an override line.
/// Frozen provenance is history and is checked as the rebuilt lists (`mios-task source`), not here.
pub fn hygiene(state: &State, doc: &str) -> Vec<String> {
    let rx = hygiene_rx();
    let mut out = Vec::new();
    for r in &state.records {
        let mut strs = Vec::new();
        if let Some(o) = r.as_object() {
            for (k, x) in o {
                if k != "provenance" {
                    walk_strings(x, k, &mut strs);
                }
            }
        }
        for (p, s) in strs {
            if let Some(m) = rx.find(&s) {
                out.push(format!(
                    "{}: {p} contains an AppData/Temp path or session id ({:?})",
                    state.label(r),
                    m.as_str()
                ));
            }
        }
    }
    for l in state.override_lines() {
        if let Some(m) = rx.find(&l.raw) {
            out.push(format!(
                "{doc}:{}: override line contains an AppData/Temp path or session id ({:?})",
                l.n,
                m.as_str()
            ));
        }
    }
    out
}

/// A directory entry named exactly `name` (case-sensitive even on a case-insensitive filesystem).
fn entry_exists(root: &Path, rel: &str) -> bool {
    let p = root.join(rel);
    let (Some(dir), Some(name)) = (p.parent(), p.file_name()) else {
        return false;
    };
    fs::read_dir(dir)
        .map(|rd| rd.flatten().any(|e| e.file_name() == name))
        .unwrap_or(false)
}

/// The retired stores declared in the SSOT, plus any file named like the canonical list (any case) at the
/// root or one directory down: each is a second task list.
pub fn retired_stores(st: &Store) -> Vec<String> {
    let mut out = Vec::new();
    let mut named: HashSet<std::path::PathBuf> = HashSet::new();
    for rel in &st.retired {
        if entry_exists(&st.root, rel) {
            named.insert(st.root.join(rel));
            out.push(format!(
                "retired task store present: {rel} -- {} is the only task list (ADR-0028); move its change into {} and delete it",
                st.path, st.path
            ));
        }
    }
    let want = Path::new(&st.path)
        .file_name()
        .map(|n| n.to_string_lossy().to_lowercase())
        .unwrap_or_default();
    let canonical_name = Path::new(&st.path).file_name().map(|n| n.to_os_string());
    let mut dirs = vec![st.root.clone()];
    if let Ok(rd) = fs::read_dir(&st.root) {
        let mut subs: Vec<_> = rd
            .flatten()
            .filter(|e| e.file_type().map(|t| t.is_dir()).unwrap_or(false))
            .map(|e| e.path())
            .collect();
        subs.sort();
        dirs.extend(subs);
    }
    for d in dirs {
        let Ok(rd) = fs::read_dir(&d) else { continue };
        let mut hits: Vec<_> = rd
            .flatten()
            .filter(|e| e.file_name().to_string_lossy().to_lowercase() == want)
            .filter(|e| !(d == st.root && Some(e.file_name()) == canonical_name))
            .map(|e| e.path())
            .filter(|p| !named.contains(p))
            .collect();
        hits.sort();
        for p in hits {
            let rel = p
                .strip_prefix(&st.root)
                .unwrap_or(&p)
                .display()
                .to_string()
                .replace('\\', "/");
            out.push(format!(
                "second task store present: {rel} -- {} is the only task list (ADR-0028); delete it",
                st.path
            ));
        }
    }
    out
}

/// Strongly connected components of size > 1 (or a self-edge) in the depends_on graph.
pub fn cycles(recs: &[Value]) -> Vec<Vec<String>> {
    let ids: Vec<String> = recs.iter().map(|r| id_of(r).to_string()).collect();
    let pos: HashMap<&str, usize> = ids
        .iter()
        .enumerate()
        .map(|(i, s)| (s.as_str(), i))
        .collect();
    let adj: Vec<Vec<usize>> = recs
        .iter()
        .map(|r| {
            r.get("depends_on")
                .and_then(|d| d.as_array())
                .map(|a| {
                    a.iter()
                        .filter_map(|x| x.as_str().and_then(|s| pos.get(s).copied()))
                        .collect()
                })
                .unwrap_or_default()
        })
        .collect();
    // Iterative Tarjan.
    let n = recs.len();
    let (mut index, mut low, mut on) = (vec![usize::MAX; n], vec![0; n], vec![false; n]);
    let (mut stack, mut next, mut out) = (Vec::new(), 0usize, Vec::new());
    for s in 0..n {
        if index[s] != usize::MAX {
            continue;
        }
        let mut call: Vec<(usize, usize)> = vec![(s, 0)];
        index[s] = next;
        low[s] = next;
        next += 1;
        stack.push(s);
        on[s] = true;
        while let Some(&mut (v, ref mut ei)) = call.last_mut() {
            if *ei < adj[v].len() {
                let w = adj[v][*ei];
                *ei += 1;
                if index[w] == usize::MAX {
                    index[w] = next;
                    low[w] = next;
                    next += 1;
                    stack.push(w);
                    on[w] = true;
                    call.push((w, 0));
                } else if on[w] {
                    low[v] = low[v].min(index[w]);
                }
            } else {
                call.pop();
                if let Some(&(p, _)) = call.last() {
                    low[p] = low[p].min(low[v]);
                }
                if low[v] == index[v] {
                    let mut comp = Vec::new();
                    while let Some(w) = stack.pop() {
                        on[w] = false;
                        comp.push(ids[w].clone());
                        if w == v {
                            break;
                        }
                    }
                    if comp.len() > 1 || adj[v].contains(&v) {
                        comp.sort();
                        out.push(comp);
                    }
                }
            }
        }
    }
    out.sort();
    out
}

/// The first differing line (1-based) and that line of `a`, or None when the texts are equal. Lines split on
/// '\n' only, so a carriage return is a difference on its own line.
pub fn first_difference(a: &str, b: &str) -> Option<(usize, String)> {
    let (mut la, mut lb) = (a.split('\n'), b.split('\n'));
    let mut n = 0;
    loop {
        n += 1;
        match (la.next(), lb.next()) {
            (None, None) => {
                return if a == b {
                    None
                } else {
                    Some((n, String::new()))
                }
            }
            (x, y) if x != y => return Some((n, x.unwrap_or("").to_string())),
            _ => {}
        }
    }
}

pub fn cr_problem(path: &str, n: usize) -> String {
    format!("{path}:{n}: carriage return in the line (a CRLF line end) -- {path} lines end in LF only; run: mios-task fmt")
}

/// A migrated record owns at least one slice, and a '#n' id names the id it was renamed from.
fn migrated_shape(id: &str, prov: &Value) -> Vec<String> {
    let mut p = Vec::new();
    if prov
        .get("sources")
        .and_then(|x| x.as_array())
        .is_none_or(|a| a.is_empty())
    {
        p.push(format!(
            "{id}: a migrated record with no provenance.sources -- every migrated record keeps the bytes it owned (ADR-0028)"
        ));
    }
    if let Some((base, _)) = id.split_once('#') {
        let named = prov
            .get("aliases")
            .and_then(|x| x.as_array())
            .is_some_and(|a| a.iter().any(|x| x.as_str() == Some(base)));
        if !named {
            p.push(format!(
                "{id}: a renamed id whose provenance.aliases does not name {base} -- the record of the rename is gone"
            ));
        }
    }
    p
}

/// The migrated set as one digest: per record its provenance key, id, aliases and slice set (once migrated:
/// its number, opaque id and slice set), sorted.
/// [tasks.store].migrated_sha256 freezes it, so a swap that keeps the count, a rewritten key, a dropped alias
/// or a stripped slice outside the frozen lists all fail.
pub fn identity(records: &[Value]) -> (usize, String) {
    let mut rows: Vec<String> = Vec::new();
    for r in records {
        let Some(p) = r.get("provenance").filter(|p| p.is_object()) else {
            continue;
        };
        let s = |v: &Value, k: &str| v.get(k).and_then(|x| x.as_str()).unwrap_or("").to_string();
        let aliases: Vec<String> = p
            .get("aliases")
            .and_then(|x| x.as_array())
            .map(|a| {
                a.iter()
                    .filter_map(|x| x.as_str().map(str::to_string))
                    .collect()
            })
            .unwrap_or_default();
        let mut sl: Vec<String> = p
            .get("sources")
            .and_then(|x| x.as_array())
            .map(|a| {
                a.iter()
                    .map(|x| {
                        format!(
                            "{}@{}+{}:{}",
                            s(x, "file"),
                            x.get("offset").and_then(|v| v.as_u64()).unwrap_or(0),
                            x.get("length").and_then(|v| v.as_u64()).unwrap_or(0),
                            s(x, "sha256")
                        )
                    })
                    .collect()
            })
            .unwrap_or_default();
        sl.sort();
        rows.push(match r.get("number").and_then(|x| x.as_u64()) {
            // A migrated list: the number and the opaque id are the identity; the old key and aliases are purged.
            Some(n) => format!("{n}\t{}\t{}", id_of(r), sl.join(",")),
            None => format!(
                "{}\t{}\t{}\t{}",
                s(p, "key"),
                id_of(r),
                aliases.join(","),
                sl.join(",")
            ),
        });
    }
    rows.sort();
    (rows.len(), sha(&rows.join("\n")))
}

/// Each migrated lane line's archived_dependencies (edges the toolkit took out of depends_on because the target
/// was archived) must not be back in the record's depends_on.
fn archived_edges(records: &[Value], idx: &Index) -> Vec<String> {
    // A lane id the migration renamed: the record keyed "<lane file>#<lane id>" answers to it.
    let mut renamed: HashMap<String, String> = HashMap::new();
    for r in records {
        let key = r
            .get("provenance")
            .and_then(|p| p.get("key"))
            .and_then(|x| x.as_str())
            .unwrap_or("");
        renamed.insert(key.to_string(), id_of(r).to_string());
    }
    let mut out = Vec::new();
    for r in records {
        let id = id_of(r);
        let deps: HashSet<&str> = r
            .get("depends_on")
            .and_then(|x| x.as_array())
            .map(|a| a.iter().filter_map(|x| x.as_str()).collect())
            .unwrap_or_default();
        let slices = r
            .get("provenance")
            .and_then(|p| p.get("sources"))
            .and_then(|x| x.as_array())
            .cloned()
            .unwrap_or_default();
        for sl in slices
            .iter()
            .filter(|x| x.get("kind").and_then(|k| k.as_str()) == Some("jsonl"))
        {
            let file = sl.get("file").and_then(|x| x.as_str()).unwrap_or("");
            let line: Value = sl
                .get("text")
                .and_then(|x| x.as_str())
                .and_then(|t| serde_json::from_str(t).ok())
                .unwrap_or(Value::Null);
            for d in line
                .get("archived_dependencies")
                .and_then(|x| x.as_array())
                .cloned()
                .unwrap_or_default()
            {
                let d = d.as_str().unwrap_or("");
                // Before the migration a renamed lane id answers through its key; after it, the frozen lane line
                // names the task by its typed id, which the index resolves.
                let now = renamed
                    .get(&format!("{file}#{d}"))
                    .cloned()
                    .or_else(|| idx.find(d).map(|i| idx.ids[i].clone()))
                    .unwrap_or_else(|| d.to_string());
                if deps.contains(now.as_str()) {
                    out.push(format!(
                        "{}: depends_on {} is an edge the lane file archived (archived_dependencies) -- it is history, a related entry of type \"archived\", never a block (ADR-0028)",
                        idx.label(id),
                        idx.label(&now)
                    ));
                }
            }
        }
    }
    out
}

/// Every former id that also names a live task, as (former id, the record it was renamed to).
pub fn former_ids(records: &[Value]) -> Vec<(String, String)> {
    let ids: HashSet<&str> = records.iter().map(id_of).collect();
    let mut out = Vec::new();
    for r in records {
        for a in r
            .get("provenance")
            .and_then(|p| p.get("aliases"))
            .and_then(|x| x.as_array())
            .cloned()
            .unwrap_or_default()
        {
            let a = a.as_str().unwrap_or("").to_string();
            if ids.contains(a.as_str()) && a != id_of(r) {
                out.push((a, id_of(r).to_string()));
            }
        }
    }
    out.sort();
    out
}

/// A record born in the list must not take an id a migrated record answered to.
fn born_aliases(records: &[Value]) -> Vec<String> {
    let born: HashSet<&str> = records
        .iter()
        .filter(|r| r.get("provenance").is_some_and(Value::is_null))
        .map(id_of)
        .collect();
    former_ids(records)
        .into_iter()
        .filter(|(a, _)| born.contains(a.as_str()))
        .map(|(a, to)| {
            format!(
                "{a}: a record born in the list reuses {a}, the former id of {to} -- pick a new id"
            )
        })
        .collect()
}

/// Ids that are a task and also a former id of another task (ADR-0028 decision 6).
pub fn notes(records: &[Value]) -> Vec<String> {
    former_ids(records)
        .into_iter()
        .map(|(a, to)| {
            format!("note: {a} names task {a} and is a former id of {to}; edit {to} by its own id")
        })
        .collect()
}

/// Every problem in the canonical list, its overrides and its rendered doc. Empty means clean.
pub fn check(st: &Store, state: &State, only: Option<&str>) -> Vec<String> {
    let mut p = Vec::new();
    if only == Some("hygiene") {
        p.extend(hygiene(state, &st.doc));
        return p;
    }
    p.extend(retired_stores(st));
    let node = &state.schema.record;
    let mut seen: HashSet<String> = HashSet::new();
    let mut migrated = 0usize;
    for l in &state.lines {
        let Some(v) = &l.value else {
            p.push(format!(
                "{}:{}: not JSON ({})",
                st.path,
                l.n,
                l.error.clone().unwrap_or_default()
            ));
            continue;
        };
        if is_marker(v) {
            if l.raw.contains('\r') {
                p.push(cr_problem(&st.path, l.n));
            }
            if v.get(DIALECT_KEY).and_then(|x| x.as_str()) != Some("openai") {
                p.push(format!(
                    "{}:{}: dialect marker {} -- {} keeps the OpenAI plan-status words",
                    st.path, l.n, l.raw, st.path
                ));
            }
            continue;
        }
        let id = state.label(v);
        let mut errs = Vec::new();
        state.schema.conforms(v, node, &id, &mut errs);
        p.extend(errs);
        if let Some(w) = v.get("status").and_then(|x| x.as_str()) {
            if lane_word(w) {
                p.push(format!(
                    "{}:{} ({id}): legacy status word {w:?} on disk -- run mios-task fmt",
                    st.path, l.n
                ));
            }
        }
        if l.raw.contains('\r') {
            p.push(cr_problem(&st.path, l.n));
        } else if dumps(&state.schema.canon(v, node)) != l.raw {
            p.push(format!(
                "{}:{} ({id}): not in canonical serialization -- run mios-task fmt",
                st.path, l.n
            ));
        }
        if !seen.insert(id_of(v).to_string()) {
            p.push(format!("duplicate id {}", id_of(v)));
        }
        match v.get("provenance") {
            Some(x) if x.is_object() => {
                migrated += 1;
                p.extend(migrated_shape(&id, x));
            }
            Some(Value::Null) => {
                let ac = v
                    .get("acceptance_criteria")
                    .and_then(|x| x.as_array())
                    .is_some_and(|a| {
                        a.iter()
                            .any(|x| x.as_str().is_some_and(|s| !s.trim().is_empty()))
                    });
                let cmd = |k: &str| {
                    v.get("verification")
                        .and_then(|x| x.get(k))
                        .and_then(|x| x.as_str())
                        .is_some_and(|s| !s.trim().is_empty())
                };
                if !ac {
                    p.push(format!(
                        "{id}: record born in {} lacks acceptance criteria",
                        st.path
                    ));
                }
                if !cmd("positive_cmd") {
                    p.push(format!(
                        "{id}: record born in {} lacks a positive control",
                        st.path
                    ));
                }
                if !cmd("negative_control_cmd") {
                    p.push(format!(
                        "{id}: record born in {} lacks a negative control",
                        st.path
                    ));
                }
            }
            _ => {}
        }
    }
    // Every migrated record is still here, and the frozen history still rebuilds byte for byte.
    if migrated != st.migrated {
        p.push(format!(
            "{} holds {migrated} migrated record(s) but [tasks.store].migrated is {} -- a migrated record was {}",
            st.path,
            st.migrated,
            if migrated < st.migrated { "dropped" } else { "forged" }
        ));
    }
    let (n, digest) = identity(&state.records);
    match &st.migrated_sha256 {
        Some(want) if *want == digest => {}
        Some(want) => p.push(format!(
            "the {n} migrated record(s) hash to {digest}, not [tasks.store].migrated_sha256 {want} -- a migrated record was swapped, renamed, or its provenance key, aliases or slices were edited (ADR-0028); `git diff {}` names it",
            st.path
        )),
        None if st.migrated > 0 => p.push(format!(
            "[tasks.store].migrated_sha256 is missing; the {n} migrated record(s) hash to {digest}"
        )),
        None => {}
    }
    p.extend(verify(&state.records, &st.frozen, &state.idx));
    p.extend(archived_edges(&state.records, &state.idx));
    p.extend(born_aliases(&state.records));
    if state.purged {
        p.extend(numbers(st, state));
        p.extend(purged_in_list(st, state));
    }
    // Overrides: block structure and every line.
    let idx = &state.idx;
    match &state.block {
        Ok(b) => p.extend(overrides::validate(&st.doc, &state.schema, idx, &b.lines)),
        Err(e) => p.extend(e.iter().cloned()),
    }
    // Effective view: evidence, resolution, cycles.
    let eff = state.effective();
    for r in &eff.records {
        let id = state.label(r);
        let id = id.as_str();
        if r.get("status").and_then(|x| x.as_str()) == Some("completed")
            && r.get("verification_evidence")
                .and_then(|x| x.as_str())
                .is_none_or(|s| s.trim().is_empty())
        {
            p.push(format!("{id}: completed without verification_evidence"));
        }
        for d in r
            .get("depends_on")
            .and_then(|x| x.as_array())
            .cloned()
            .unwrap_or_default()
        {
            let d = d.as_str().unwrap_or("");
            if idx.find(d).is_none() {
                p.push(format!("{id}: depends_on unknown {d}"));
            }
        }
        let ep = r.get("epic").and_then(|x| x.as_str()).unwrap_or("");
        if !ep.is_empty() && idx.find(ep).is_none() {
            p.push(format!("{id}: epic unknown {ep}"));
        }
        if state.purged {
            for x in r
                .get("related")
                .and_then(|x| x.as_array())
                .cloned()
                .unwrap_or_default()
            {
                let d = x.get("id").and_then(|v| v.as_str()).unwrap_or("");
                if idx.find(d).is_none() {
                    p.push(format!("{id}: related unknown {d}"));
                }
            }
        }
    }
    for c in cycles(&eff.records) {
        let mut c: Vec<String> = c.iter().map(|x| idx.label(x)).collect();
        c.sort();
        p.push(format!("depends_on cycle among [{}]", c.join(", ")));
    }
    p.extend(overrides::find_contradictions(&eff.records, idx));
    p.extend(hygiene(state, &st.doc));
    // The doc is a projection: it must equal the render byte for byte.
    if let Some(md) = &state.md {
        if state.block.is_ok() {
            let want = overrides::render(&st.path, &eff, &state.inner(), &state.idx);
            if let Some((n, got)) = first_difference(md, &want) {
                p.push(format!(
                    "{}:{n} differs from the render of {} (a stale render, or a hand edit outside the overrides block): {:?} -- run: mios-task render",
                    st.doc,
                    st.path,
                    got.chars().take(120).collect::<String>()
                ));
            }
        }
    }
    p
}

/// The number rules of a migrated list: every number unique, numbers in creation order (the opaque ids, which
/// begin with their creation time, sort the way the numbers do), and the highest never below
/// [tasks.store].numbered, so a dropped last task cannot hand its number to the next one.
pub fn numbers(st: &Store, state: &State) -> Vec<String> {
    let mut p = Vec::new();
    let mut by_n: Vec<(u64, &str)> = Vec::new();
    let mut seen: HashMap<u64, &str> = HashMap::new();
    for r in &state.records {
        let Some(n) = r.get("number").and_then(|x| x.as_u64()) else {
            continue; // the schema names the missing field
        };
        let id = id_of(r);
        if let Some(prev) = seen.insert(n, id) {
            p.push(format!(
                "duplicate number {n}: {prev} and {id} are both {} -- a number names one task, ever",
                state.ids.typed(n)
            ));
        }
        by_n.push((n, id));
    }
    by_n.sort();
    for w in by_n.windows(2) {
        let ((a, ia), (b, ib)) = (w[0], w[1]);
        if a != b && ia >= ib {
            p.push(format!(
                "{}: its id {ib} sorts before {}'s {ia} though its number is higher -- numbers follow creation order; {} was reissued or hand-assigned",
                state.ids.typed(b),
                state.ids.typed(a),
                state.ids.typed(b)
            ));
        }
    }
    let high = by_n.last().map(|x| x.0).unwrap_or(0);
    if let Some(floor) = st.numbered {
        if high < floor {
            p.push(format!(
                "the highest task number is {high} but [tasks.store].numbered is {floor} -- {} was dropped, and its number would be issued again",
                state.ids.typed(floor)
            ));
        }
    }
    p
}

/// No purged id in the list itself: not in a record (frozen slices included) and not in an override line.
pub fn purged_in_list(st: &Store, state: &State) -> Vec<String> {
    let shapes = match Shapes::from_list(st.purged.clone()) {
        Ok(s) => s,
        Err(e) => return vec![e],
    };
    let mut p = Vec::new();
    let mut found = |at: String, text: &str| {
        for h in crate::record::ids::scan(text.as_bytes(), &mut |t| shapes.matches(t).then_some(()))
        {
            p.push(format!(
                "{at} carries the purged id {} -- name the task by its typed id {}_<number> (ADR-0028)",
                h.token, state.ids.object
            ));
        }
    };
    for r in &state.records {
        let mut strs = Vec::new();
        walk_strings(r, "", &mut strs);
        for (path, s) in strs {
            found(format!("{}: {path}", state.label(r)), &s);
        }
    }
    for l in state.override_lines() {
        found(format!("{}:{}: override line", st.doc, l.n), &l.raw);
    }
    p
}

pub fn sha(s: &str) -> String {
    format!("{:x}", Sha256::digest(s.as_bytes()))
}

/// One provenance slice with the record that owns it.
pub struct Slice {
    pub owner: String,
    pub offset: usize,
    pub after: String,
    pub sha256: String,
    pub length: usize,
    pub text: String,
}

/// Every slice of every record, grouped by file and sorted by offset; each names its owner by `idx`'s label.
pub fn slices(records: &[Value], idx: &Index) -> BTreeMap<String, Vec<Slice>> {
    let mut out: BTreeMap<String, Vec<Slice>> = BTreeMap::new();
    for (i, r) in records.iter().enumerate() {
        let owner = idx.labels.get(i).map(String::as_str).unwrap_or("?");
        let Some(a) = r
            .get("provenance")
            .and_then(|p| p.get("sources"))
            .and_then(|s| s.as_array())
        else {
            continue;
        };
        for s in a {
            let g = |k: &str| s.get(k).and_then(|x| x.as_str()).unwrap_or("").to_string();
            let n = |k: &str| s.get(k).and_then(|x| x.as_u64()).unwrap_or(0) as usize;
            out.entry(g("file")).or_default().push(Slice {
                owner: owner.to_string(),
                offset: n("offset"),
                after: g("after"),
                sha256: g("sha256"),
                length: n("length"),
                text: g("text"),
            });
        }
    }
    for v in out.values_mut() {
        v.sort_by_key(|s| s.offset);
    }
    out
}

/// The bytes of one frozen list, rebuilt from its slices; Err names every gap, overlap or edited slice.
pub fn rebuild(file: &str, sl: &[Slice], want: Option<&Frozen>) -> Result<String, Vec<String>> {
    let mut errs = Vec::new();
    let mut out = String::new();
    let mut at = 0usize;
    for s in sl {
        if sha(&s.text) != s.sha256 || s.text.len() != s.length {
            errs.push(format!(
                "{file}: the slice {} owns at offset {} was edited (sha256/length no longer match its text)",
                s.owner, s.offset
            ));
        }
        if s.offset > at {
            errs.push(format!(
                "{file}: bytes {at}..{} are missing -- record {} was dropped (the slice owned by {} follows it)",
                s.offset,
                if s.after.is_empty() { "(unnamed)" } else { s.after.as_str() },
                s.owner
            ));
        } else if s.offset < at {
            errs.push(format!(
                "{file}: the slice {} owns at offset {} overlaps the one before it (a record was duplicated)",
                s.owner, s.offset
            ));
            continue;
        }
        out.push_str(&s.text);
        at = s.offset + s.text.len();
    }
    if let Some(f) = want {
        if at < f.bytes {
            let last = sl.last().map(|s| s.owner.as_str()).unwrap_or("(none)");
            errs.push(format!(
                "{file}: bytes {at}..{} are missing at the end -- a record after {last} was dropped",
                f.bytes
            ));
        }
        if errs.is_empty() && sha(&out) != f.sha256 {
            errs.push(format!(
                "{file}: the rebuilt bytes differ from [tasks.store].frozen sha256"
            ));
        }
    }
    if errs.is_empty() {
        Ok(out)
    } else {
        Err(errs)
    }
}

/// Every problem with the frozen history: each declared list rebuilds to its digest, every other slice is intact.
pub fn verify(records: &[Value], frozen: &[Frozen], idx: &Index) -> Vec<String> {
    let all = slices(records, idx);
    let mut errs = Vec::new();
    for f in frozen {
        match all.get(&f.source) {
            None => errs.push(format!(
                "{}: no record carries its slices any more -- every record migrated from it was dropped",
                f.source
            )),
            Some(sl) => {
                if let Err(e) = rebuild(&f.source, sl, Some(f)) {
                    errs.extend(e);
                }
            }
        }
    }
    // Slices of a list that is not frozen whole (a record moved from a sibling's backlog) still keep their digest.
    for (file, sl) in &all {
        if frozen.iter().any(|f| &f.source == file) {
            continue;
        }
        for s in sl {
            if sha(&s.text) != s.sha256 || s.text.len() != s.length {
                errs.push(format!(
                    "{file}: the slice {} owns at offset {} was edited (sha256/length no longer match its text)",
                    s.owner, s.offset
                ));
            }
        }
    }
    errs
}
