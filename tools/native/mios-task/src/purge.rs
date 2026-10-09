// AI-hint: `mios-task migrate-ids` (one-shot) assigns each task an opaque task_<ULID> id and creation-order number, purging old ids from list, history, overrides, and tracked files.
// AI-related: tasks.jsonl, TASKS.md, /usr/share/mios/mios.toml [tasks.store], /usr/lib/mios/schemas/task-record.schema.json, tools/native/mios-task/src/ids.rs, /usr/share/doc/mios/adr/0028-one-canonical-task-list.md
// AI-functions: Resolver::new, Resolver::text, Resolver::structured, rewrite, cmd, plan, tree, ssot_section, prefix_of, publish

use crate::check::{self, sha, Slice, State};
use crate::cli::Args;
use crate::ids::{self, Ids, Shapes};
use crate::overrides;
use crate::record::{dumps, write_atomic, Index, Schema, Store};
use serde_json::{json, Value};
use std::collections::{BTreeMap, HashMap, HashSet};
use std::fs;
use std::path::Path;

/// How an old token found its task.
#[derive(Clone, Copy, PartialEq, Eq, Hash, PartialOrd, Ord, Debug)]
pub enum Via {
    /// The token is a task's id.
    Id,
    /// A former id (provenance.aliases) of a renamed task: inside that task's own text it names the task itself;
    /// elsewhere a former id that is also a live id names the live task.
    Alias,
    /// An operator decision on the command line (--map OLD=OLD2).
    Map,
    /// A task whose title opens with the token ("QSCH-01: ...") answers to it.
    Title,
    /// A member of a range a task covers ("KAG-123" in the "KAG-123..259" banner) names that banner task.
    Range,
}

impl Via {
    fn name(self) -> &'static str {
        match self {
            Via::Id => "id",
            Via::Alias => "alias",
            Via::Map => "map",
            Via::Title => "title",
            Via::Range => "range",
        }
    }
}

/// The token's prefix, for counts: the text before its first '-' or ' '.
pub fn prefix_of(tok: &str) -> String {
    tok.split(['-', ' ', '#']).next().unwrap_or(tok).to_string()
}

/// Old token -> task, over a list that is not yet migrated.
pub struct Resolver {
    exact: HashMap<String, usize>,
    former: HashMap<String, usize>,
    own: HashMap<usize, HashSet<String>>,
    /// Ids that are also workstream names (a record's `workstream`): in text they name the workstream, which
    /// stays; only structured references to the task are rewritten.
    pub labels: HashSet<String>,
    maps: HashMap<String, usize>,
    titles: HashMap<String, Option<usize>>,
    ranges: Vec<(String, u64, u64, usize)>,
    pub shapes: Shapes,
}

fn s<'a>(v: &'a Value, k: &str) -> &'a str {
    v.get(k).and_then(|x| x.as_str()).unwrap_or("")
}

fn strs(v: Option<&Value>) -> Vec<String> {
    v.and_then(|x| x.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default()
}

/// "P-a..b" or "P-a..P-b" as (P-, a, b), when a <= b.
fn range_of(id: &str) -> Option<(String, u64, u64)> {
    let (lo, hi) = id.split_once("..")?;
    let digits = lo.bytes().rev().take_while(|c| c.is_ascii_digit()).count();
    let (p, a) = lo.split_at(lo.len() - digits);
    if digits == 0 || p.is_empty() {
        return None;
    }
    let b = hi.strip_prefix(p).unwrap_or(hi);
    if b.is_empty() || !b.bytes().all(|c| c.is_ascii_digit()) {
        return None;
    }
    let (a, b): (u64, u64) = (a.parse().ok()?, b.parse().ok()?);
    (a <= b).then(|| (p.to_string(), a, b))
}

impl Resolver {
    pub fn new(recs: &[Value], maps: &[(String, String)]) -> Result<Resolver, String> {
        let mut exact = HashMap::new();
        for (i, r) in recs.iter().enumerate() {
            exact.entry(s(r, "id").to_string()).or_insert(i);
        }
        let workstreams: HashSet<&str> = recs.iter().map(|r| s(r, "workstream")).collect();
        let labels: HashSet<String> = exact
            .keys()
            .filter(|k| workstreams.contains(k.as_str()))
            .cloned()
            .collect();
        let mut former = HashMap::new();
        let mut own: HashMap<usize, HashSet<String>> = HashMap::new();
        for (i, r) in recs.iter().enumerate() {
            for a in strs(r.get("provenance").and_then(|p| p.get("aliases"))) {
                own.entry(i).or_default().insert(a.clone());
                if !exact.contains_key(&a) {
                    former.insert(a, i);
                }
            }
        }
        let all_ids: Vec<String> = exact.keys().chain(former.keys()).cloned().collect();
        let shapes = Shapes::from_ids(all_ids.iter().map(String::as_str), &|i| labels.contains(i));
        let mut ranges = Vec::new();
        for (i, r) in recs.iter().enumerate() {
            if let Some((p, a, b)) = range_of(s(r, "id")) {
                ranges.push((p, a, b, i));
            }
        }
        // A title that opens with an old token not otherwise a task ("QSCH-01: ...") answers to it.
        let mut titles: HashMap<String, Option<usize>> = HashMap::new();
        for (i, r) in recs.iter().enumerate() {
            let t = s(r, "title");
            let hits = ids::scan(t.as_bytes(), &mut |tok| shapes.matches(tok).then_some(()));
            if let Some(h) = hits.first().filter(|h| h.start == 0) {
                let rest = &t[h.end..];
                if (rest.starts_with(':')
                    || rest.starts_with(" --")
                    || rest.starts_with(" \u{2014}"))
                    && !exact.contains_key(&h.token)
                    && !former.contains_key(&h.token)
                {
                    let e = titles.entry(h.token.clone()).or_insert(Some(i));
                    if *e != Some(i) {
                        *e = None;
                    }
                }
            }
        }
        let mut r = Resolver {
            exact,
            former,
            own,
            labels,
            maps: HashMap::new(),
            titles,
            ranges,
            shapes,
        };
        for (old, to) in maps {
            if r.exact.contains_key(old) {
                return Err(format!(
                    "--map {old}={to}: {old} is a task; --map only decides a token that names none"
                ));
            }
            let Some((i, _)) = r.structured(to) else {
                return Err(format!("--map {old}={to}: {to} names no task"));
            };
            r.maps.insert(old.clone(), i);
        }
        Ok(r)
    }

    fn cover(&self, tok: &str) -> Option<usize> {
        let digits = tok.bytes().rev().take_while(|c| c.is_ascii_digit()).count();
        let (p, n) = tok.split_at(tok.len() - digits);
        let n: u64 = n.parse().ok()?;
        self.ranges
            .iter()
            .filter(|(q, a, b, _)| q == p && *a <= n && n <= *b)
            .min_by_key(|(_, a, b, _)| b - a)
            .map(|x| x.3)
    }

    /// A token in a structured field (depends_on, epic, related), most specific first: a task's id, a former
    /// id, an operator decision, the task whose title opens with it, then the banner whose range covers it
    /// (so "QCAT-01" names the task titled "QCAT-01: ...", not the "QCAT-01..04" banner).
    pub fn structured(&self, tok: &str) -> Option<(usize, Via)> {
        if let Some(&i) = self.exact.get(tok) {
            return Some((i, Via::Id));
        }
        if let Some(&i) = self.former.get(tok) {
            return Some((i, Via::Alias));
        }
        if let Some(&i) = self.maps.get(tok) {
            return Some((i, Via::Map));
        }
        if let Some(i) = self.titles.get(tok).copied().flatten() {
            return Some((i, Via::Title));
        }
        self.cover(tok).map(|i| (i, Via::Range))
    }

    /// A token in text, inside the text of record `owner` (or a tracked file when None). A workstream name
    /// stays; a former id inside its own task's text names that task.
    pub fn text(&self, tok: &str, owner: Option<usize>) -> Option<(usize, Via)> {
        if self.labels.contains(tok) {
            return None;
        }
        if let Some(i) = owner.filter(|i| self.own.get(i).is_some_and(|a| a.contains(tok))) {
            return Some((i, Via::Alias));
        }
        self.structured(tok)
    }
}

/// What a rewrite did, counted.
#[derive(Default)]
pub struct Tally {
    pub by_prefix: BTreeMap<String, usize>,
    pub by_via: BTreeMap<&'static str, usize>,
    pub range_ends: usize,
    pub range_ends_unresolved: Vec<String>,
    pub spans_changed: Vec<String>,
    pub unresolved: Vec<String>,
}

impl Tally {
    fn json(&self) -> Value {
        json!({
            "by_prefix": self.by_prefix,
            "by_via": self.by_via,
            "total": self.by_prefix.values().sum::<usize>(),
            "range_ends_rewritten": self.range_ends,
            "range_ends_unresolved": self.range_ends_unresolved,
            "ranges_whose_members_moved": self.spans_changed.len(),
            "ranges_whose_members_moved_examples": self.spans_changed.iter().take(20).collect::<Vec<_>>(),
        })
    }
}

/// What the rewrite needs besides the resolver: the numbers and the id spelling.
pub struct Names<'a> {
    pub numbers: &'a [u64],
    pub ids: &'a Ids,
    pub by_number: &'a HashMap<u64, usize>,
}

/// Rewrite every old token in `b` to the typed id of its task. `at` names the place for unresolved tokens.
pub fn rewrite(
    b: &[u8],
    owner: Option<usize>,
    r: &Resolver,
    n: &Names,
    t: &mut Tally,
    at: &str,
) -> Vec<u8> {
    enum D {
        Hit(usize, Via),
        Miss,
    }
    let hits = ids::scan(b, &mut |tok| {
        if !tok.contains(['-', ' ']) {
            return None;
        }
        match r.text(tok, owner) {
            Some((i, v)) => Some(D::Hit(i, v)),
            None if r.shapes.matches(tok) && !r.labels.contains(tok) => Some(D::Miss),
            None => None,
        }
    });
    if hits.is_empty() {
        return b.to_vec();
    }
    let mut out = Vec::with_capacity(b.len());
    let mut at_byte = 0usize;
    let mut line = 1usize;
    let mut counted = 0usize;
    // The hit just written, for an explicit "P-a..P-b" span.
    let mut last: Option<(usize, usize, String)> = None;
    for h in hits {
        if h.start < at_byte {
            continue; // consumed by a range end
        }
        out.extend_from_slice(&b[at_byte..h.start]);
        at_byte = h.start;
        let (i, via) = match h.what {
            D::Hit(i, v) => (i, v),
            D::Miss => {
                line += b[counted..h.start].iter().filter(|c| **c == b'\n').count();
                counted = h.start;
                t.unresolved.push(format!("{at}:{line}: {}", h.token));
                continue;
            }
        };
        *t.by_prefix.entry(prefix_of(&h.token)).or_default() += 1;
        *t.by_via.entry(via.name()).or_default() += 1;
        if let Some((e, j, lo)) = &last {
            if *e + 2 == h.start && &b[*e..h.start] == b".." {
                span_check(lo, &h.token, *j, i, r, n, t);
            }
        }
        let mut end = h.end;
        let mut text = n.ids.typed(n.numbers[i]);
        // "P-a..b": the bare end is P-b.
        if !h.token.contains("..") && b[end..].starts_with(b"..") {
            let d = b[end + 2..]
                .iter()
                .take_while(|c| c.is_ascii_digit())
                .count();
            let after = b.get(end + 2 + d).copied();
            if d > 0 && !after.is_some_and(|c| c.is_ascii_alphanumeric() || c == b'_' || c == b'-')
            {
                let digits = std::str::from_utf8(&b[end + 2..end + 2 + d]).unwrap_or("");
                let p = &h.token[..h.token.len()
                    - h.token
                        .bytes()
                        .rev()
                        .take_while(|c| c.is_ascii_digit())
                        .count()];
                let hi = format!("{p}{digits}");
                match r.text(&hi, owner) {
                    Some((j, _)) => {
                        text = format!("{text}..{}", n.ids.typed(n.numbers[j]));
                        end += 2 + d;
                        t.range_ends += 1;
                        span_check(&h.token, &hi, i, j, r, n, t);
                    }
                    None => t
                        .range_ends_unresolved
                        .push(format!("{at}: {}..{digits}", h.token)),
                }
            }
        }
        out.extend_from_slice(text.as_bytes());
        at_byte = end;
        last = Some((end, i, h.token.clone()));
    }
    out.extend_from_slice(&b[at_byte..]);
    out
}

/// A rewritten range "a..b" means the numbers a..b now; record when those are not the tasks the old range named.
fn span_check(lo: &str, hi: &str, i: usize, j: usize, r: &Resolver, n: &Names, t: &mut Tally) {
    let split = |x: &str| -> Option<(String, u64, usize)> {
        let d = x.bytes().rev().take_while(|c| c.is_ascii_digit()).count();
        let (p, k) = x.split_at(x.len() - d);
        if d == 0 {
            return None;
        }
        Some((p.to_string(), k.parse().ok()?, d))
    };
    let (Some((p, a, w)), Some((q, b, _))) = (split(lo), split(hi)) else {
        return;
    };
    if p != q || a > b {
        return;
    }
    let old: HashSet<usize> = (a..=b)
        .filter_map(|k| r.exact.get(&format!("{p}{k:0w$}")).copied())
        .collect();
    let (x, y) = (
        n.numbers[i].min(n.numbers[j]),
        n.numbers[i].max(n.numbers[j]),
    );
    let new: HashSet<usize> = (x..=y)
        .filter_map(|k| n.by_number.get(&k).copied())
        .collect();
    if old != new {
        t.spans_changed.push(format!(
            "{lo}..{hi} -> {}..{}: {} task(s) then, {} now",
            n.ids.typed(x),
            n.ids.typed(y),
            old.len(),
            new.len()
        ));
    }
}

/// Every string of `v` but the structured references and the provenance bookkeeping, through `f`.
fn walk_text(v: &mut Value, path: &str, f: &mut dyn FnMut(&str, &str) -> String) {
    const SKIP: [&str; 13] = [
        "id",
        "epic",
        "depends_on",
        "workstream",
        "related",
        "provenance.origin",
        "provenance.key",
        "provenance.aliases",
        "provenance.also_in",
        "provenance.sources.file",
        "provenance.sources.kind",
        "provenance.sources.sha256",
        "provenance.sources.after",
    ];
    if SKIP.contains(&path) {
        return;
    }
    match v {
        Value::String(x) => {
            let y = f(path, x);
            if y != *x {
                *x = y;
            }
        }
        Value::Array(a) => {
            for x in a.iter_mut() {
                walk_text(x, path, f);
            }
        }
        Value::Object(o) => {
            for (k, x) in o.iter_mut() {
                let p = if path.is_empty() {
                    k.clone()
                } else {
                    format!("{path}.{k}")
                };
                walk_text(x, &p, f);
            }
        }
        _ => {}
    }
}

/// The plan of a migration, computed in memory before anything is written.
struct Plan {
    lines: Vec<String>,
    records: Vec<Value>,
    numbers: Vec<u64>,
    frozen: Vec<(String, usize, String)>,
    digest: String,
    shapes: Vec<String>,
    inner: String,
    store: Tally,
    files: Vec<(String, Vec<u8>, usize)>,
    renames: Vec<(String, String)>,
    tree: Tally,
    labels: Vec<String>,
    mapping: Value,
}

fn plan(st: &Store, state: &State, a: &Args, ids: &Ids) -> Result<Plan, String> {
    let recs = &state.records;
    let mut maps = Vec::new();
    for m in a.all("map") {
        let (o, t) = m
            .split_once('=')
            .ok_or(format!("--map {m}: write OLD=OLD2"))?;
        maps.push((o.to_string(), t.to_string()));
    }
    let r = Resolver::new(recs, &maps)?;
    let old: Vec<String> = recs.iter().map(|x| s(x, "id").to_string()).collect();
    // Numbers: creation order -- the created date (a record without one is older than any that has one),
    // then the order of the list.
    let mut order: Vec<usize> = (0..recs.len()).collect();
    order.sort_by(|&x, &y| {
        let c = |i: usize| {
            recs[i]
                .get("created")
                .and_then(|v| v.as_str())
                .unwrap_or("")
        };
        c(x).cmp(c(y)).then(x.cmp(&y))
    });
    let mut numbers = vec![0u64; recs.len()];
    let mut ms = vec![0u64; recs.len()];
    let mut rank: HashMap<&str, u64> = HashMap::new();
    for (pos, &i) in order.iter().enumerate() {
        numbers[i] = pos as u64 + 1;
        let day = recs[i]
            .get("created")
            .and_then(|v| v.as_str())
            .unwrap_or("");
        let k = rank.entry(day).or_insert(0);
        // The day's first millisecond plus the rank within the day: ids sort the way numbers do.
        ms[i] = ids::day_ms(day).unwrap_or(0) + *k;
        *k += 1;
    }
    use sha2::{Digest, Sha256};
    let opaque: Vec<String> = (0..recs.len())
        .map(|i| {
            let h = Sha256::digest(format!("mios-task migrate-ids\0{}", old[i]).as_bytes());
            ids.opaque(&ids::ulid(ms[i], &h))
        })
        .collect();
    if opaque.iter().collect::<HashSet<_>>().len() != opaque.len() {
        return Err("two tasks minted the same opaque id; nothing written".into());
    }
    let by_number: HashMap<u64, usize> = numbers.iter().enumerate().map(|(i, n)| (*n, i)).collect();
    let names = Names {
        numbers: &numbers,
        ids,
        by_number: &by_number,
    };
    let mut store = Tally::default();
    let mut errs = Vec::new();
    let mut out: Vec<Value> = Vec::with_capacity(recs.len());
    for (i, rec) in recs.iter().enumerate() {
        let mut v = rec.clone();
        let label = ids.typed(numbers[i]);
        walk_text(&mut v, "", &mut |path, x| {
            let b = rewrite(
                x.as_bytes(),
                Some(i),
                &r,
                &names,
                &mut store,
                &format!("{label} ({}) {path}", old[i]),
            );
            String::from_utf8(b).expect("ascii splices keep UTF-8")
        });
        let link = |d: &str, what: &str, errs: &mut Vec<String>| -> String {
            match r.structured(d) {
                Some((j, _)) => opaque[j].clone(),
                None => {
                    errs.push(format!("{} ({}): {what} {d} names no task", label, old[i]));
                    d.to_string()
                }
            }
        };
        let m = v.as_object_mut().ok_or("a record is not an object")?;
        let deps: Vec<String> = strs(m.get("depends_on"))
            .iter()
            .map(|d| link(d, "depends_on", &mut errs))
            .collect();
        m.insert("depends_on".into(), json!(deps));
        let ep = s(rec, "epic");
        if !ep.is_empty() {
            let e = link(ep, "epic", &mut errs);
            m.insert("epic".into(), json!(e));
        }
        if let Some(rel) = m.get_mut("related").and_then(|x| x.as_array_mut()) {
            for e in rel.iter_mut() {
                let d = s(e, "id").to_string();
                let new = link(&d, "related", &mut errs);
                e["id"] = json!(new);
            }
        }
        if r.labels.contains(&old[i]) && m.get("workstream").is_none_or(Value::is_null) {
            m.insert("workstream".into(), json!(old[i]));
        }
        m.insert("id".into(), json!(opaque[i]));
        m.insert("object".into(), json!(ids.object));
        m.insert("number".into(), json!(numbers[i]));
        if let Some(p) = m.get_mut("provenance").and_then(|x| x.as_object_mut()) {
            p.remove("key");
            p.remove("aliases");
        }
        out.push(v);
    }
    // Slices: new lengths shift the offsets after them; `after` names the owner of the slice before.
    let mut at: Vec<(String, u64, usize, usize)> = Vec::new();
    for (i, v) in out.iter().enumerate() {
        if let Some(a) = v.pointer("/provenance/sources").and_then(|x| x.as_array()) {
            for (j, sl) in a.iter().enumerate() {
                at.push((
                    s(sl, "file").to_string(),
                    sl["offset"].as_u64().unwrap_or(0),
                    i,
                    j,
                ));
            }
        }
    }
    at.sort();
    let mut delta: i64 = 0;
    let mut prev: Option<(String, usize)> = None;
    for (file, off, i, j) in at {
        if prev.as_ref().is_none_or(|p| p.0 != file) {
            delta = 0;
        }
        let sl = &mut out[i]["provenance"]["sources"][j];
        let text = s(sl, "text").to_string();
        let old_len = sl["length"].as_u64().unwrap_or(0) as i64;
        let new_off = (off as i64 + delta) as u64;
        let after = match &prev {
            Some((f, o)) if *f == file => ids.typed(numbers[*o]),
            _ => String::new(),
        };
        sl["offset"] = json!(new_off);
        sl["length"] = json!(text.len());
        sl["sha256"] = json!(sha(&text));
        sl["after"] = json!(after);
        delta += text.len() as i64 - old_len;
        prev = Some((file, i));
    }
    // The purged schema decides every record.
    let schema = Schema::load(&st.schema_file())?;
    let mut lines = Vec::with_capacity(out.len());
    for (i, v) in out.iter_mut().enumerate() {
        *v = schema.canon(v, &schema.record);
        schema.conforms(
            v,
            &schema.record,
            &format!("{} ({})", ids.typed(numbers[i]), old[i]),
            &mut errs,
        );
        lines.push(dumps(v));
    }
    let idx = Index::new(&out, Some(ids));
    let all = check::slices(&out, &idx);
    let mut frozen = Vec::new();
    for f in &st.frozen {
        let sl: &[Slice] = all.get(&f.source).map(Vec::as_slice).unwrap_or(&[]);
        match check::rebuild(&f.source, sl, None) {
            Ok(t) => frozen.push((f.source.clone(), t.len(), sha(&t))),
            Err(e) => errs.extend(e),
        }
    }
    let inner = String::from_utf8(rewrite(
        state.inner().as_bytes(),
        None,
        &r,
        &names,
        &mut store,
        &st.doc,
    ))
    .expect("utf-8");
    errs.extend(
        store
            .unresolved
            .iter()
            .map(|u| format!("{u} names no task")),
    );
    // The tracked tree, when asked.
    let mut tree = Tally::default();
    let mut files: Vec<(String, Vec<u8>, usize)> = Vec::new();
    let mut renames: Vec<(String, String)> = Vec::new();
    if a.has("--rewrite-tree") {
        let (f, n) = rewrite_tree(
            &st.root,
            &[&st.path, &st.doc],
            &r,
            &names,
            &mut tree,
            &mut errs,
        )?;
        files = f;
        renames = n;
    }
    if !errs.is_empty() {
        let n = errs.len();
        errs.truncate(60);
        return Err(format!(
            "migrate-ids refused, nothing written -- {n} problem(s); decide each token with --map OLD=OLD2, or fix the text:\n{}",
            errs.join("\n")
        ));
    }
    let mut labels: Vec<String> = r.labels.iter().cloned().collect();
    labels.sort();
    let mapping = mapping(&r, &numbers, ids);
    Ok(Plan {
        digest: check::identity(&out).1,
        lines,
        records: out,
        numbers,
        frozen,
        shapes: r.shapes.list.clone(),
        inner,
        store,
        files,
        renames,
        tree,
        labels,
        mapping,
    })
}

/// Every tracked file under `root` but `skip`, rewritten in memory: the changed contents (with their counts)
/// and the paths that name an old id. A symlink or other non-regular entry that would need a rewrite is a
/// problem for a person, never a silent skip.
#[allow(clippy::type_complexity)]
fn rewrite_tree(
    root: &Path,
    skip: &[&str],
    r: &Resolver,
    names: &Names,
    tree: &mut Tally,
    errs: &mut Vec<String>,
) -> Result<(Vec<(String, Vec<u8>, usize)>, Vec<(String, String)>), String> {
    let (mut files, mut renames) = (Vec::new(), Vec::new());
    for f in ids::tracked_files(root)? {
        if skip.contains(&f.as_str()) {
            continue;
        }
        let p = root.join(&f);
        let md = fs::symlink_metadata(&p)
            .map_err(|e| format!("tracked source {f} cannot be read: {e}"))?;
        if !md.is_file() {
            let renamed = rewrite(f.as_bytes(), None, r, names, tree, &format!("path {f}"));
            let linked = if md.file_type().is_symlink() {
                let link = fs::read_link(&p).map_err(|e| format!("{f}: {e}"))?;
                let text = link
                    .to_str()
                    .ok_or_else(|| format!("{f}: non-UTF-8 symlink target"))?;
                rewrite(
                    text.as_bytes(),
                    None,
                    r,
                    names,
                    tree,
                    &format!("symlink {f}"),
                ) != text.as_bytes()
            } else {
                false
            };
            if renamed != f.as_bytes() || linked {
                errs.push(format!(
                    "{f}: a non-regular tracked source needs a task rewrite; handle it explicitly"
                ));
            }
            continue;
        }
        let b = fs::read(&p).map_err(|e| format!("{f}: {e}"))?;
        if ids::is_text(&b) {
            let before = tree.by_prefix.values().sum::<usize>();
            let nb = rewrite(&b, None, r, names, tree, &f);
            if nb != b {
                let n = tree.by_prefix.values().sum::<usize>() - before;
                files.push((f.clone(), nb, n));
            }
        }
        let np = rewrite(f.as_bytes(), None, r, names, tree, &format!("path {f}"));
        if np != f.as_bytes() {
            renames.push((f.clone(), String::from_utf8_lossy(&np).into_owned()));
        }
    }
    errs.extend(tree.unresolved.iter().map(|u| format!("{u} names no task")));
    Ok((files, renames))
}

/// Every token the migration resolves, with the typed id it becomes: task ids, former ids, operator
/// decisions, title names and the members of every range banner, in the order `structured` prefers them.
/// `--mapping-out` writes it outside the tree so a sibling repository can apply the same rewrite
/// (`mios-task rewrite-ids`); the tree itself keeps none of it.
fn mapping(r: &Resolver, numbers: &[u64], ids: &Ids) -> Value {
    let mut m: BTreeMap<String, String> = BTreeMap::new();
    let mut put = |k: &str, i: usize| {
        m.entry(k.to_string())
            .or_insert_with(|| ids.typed(numbers[i]));
    };
    for (k, &i) in &r.exact {
        if !r.labels.contains(k) {
            put(k, i);
        }
    }
    for (k, &i) in &r.former {
        put(k, i);
    }
    for (k, &i) in &r.maps {
        put(k, i);
    }
    for (k, i) in &r.titles {
        if let Some(i) = i {
            put(k, *i);
        }
    }
    for (k, &i) in &r.exact {
        let Some((p, a, b)) = range_of(k) else {
            continue;
        };
        let w = k.split("..").next().unwrap_or("").len() - p.len();
        for n in a..=b {
            let tok = format!("{p}{n:0w$}");
            if r.shapes.matches(&tok) {
                put(&tok, i);
            }
        }
    }
    let mut keep: Vec<&String> = r.labels.iter().collect();
    keep.sort();
    json!({"object": ids.object, "map": m, "purged": r.shapes.list, "keep": keep})
}

/// `mios-task rewrite-ids --mapping FILE --root DIR`: the tree rewrite of migrate-ids, from a mapping it wrote,
/// over a repository that has no task list of its own (mios-bootstrap mirrors MiOS files, Law 15).
pub fn cmd_rewrite(a: &Args) -> Result<(), String> {
    let root = std::path::PathBuf::from(a.one("root").ok_or("rewrite-ids needs --root DIR")?);
    let mf = a
        .one("mapping")
        .ok_or("rewrite-ids needs --mapping FILE (migrate-ids --mapping-out)")?;
    let v: Value = serde_json::from_str(&fs::read_to_string(mf).map_err(|e| format!("{mf}: {e}"))?)
        .map_err(|e| format!("{mf}: {e}"))?;
    let ids = Ids {
        object: v["object"]
            .as_str()
            .ok_or(format!("{mf}: no object"))?
            .to_string(),
    };
    let map = v["map"].as_object().ok_or(format!("{mf}: no map"))?;
    let mut numbers: Vec<u64> = Vec::new();
    let mut index: HashMap<u64, usize> = HashMap::new();
    let mut exact = HashMap::new();
    for (tok, typed) in map {
        let n: u64 = typed
            .as_str()
            .and_then(|t| t.strip_prefix(&format!("{}_", ids.object)))
            .and_then(|d| d.parse().ok())
            .ok_or(format!("{mf}: {tok} maps to {typed}, not a typed id"))?;
        let i = *index.entry(n).or_insert_with(|| {
            numbers.push(n);
            numbers.len() - 1
        });
        exact.insert(tok.clone(), i);
    }
    let strs = |k: &str| -> Vec<String> {
        v[k].as_array()
            .map(|x| {
                x.iter()
                    .filter_map(|y| y.as_str().map(str::to_string))
                    .collect()
            })
            .unwrap_or_default()
    };
    let r = Resolver {
        exact,
        former: HashMap::new(),
        own: HashMap::new(),
        labels: strs("keep").into_iter().collect(),
        maps: HashMap::new(),
        titles: HashMap::new(),
        ranges: Vec::new(),
        shapes: Shapes::from_list(strs("purged"))?,
    };
    let by_number: HashMap<u64, usize> = index.clone();
    let names = Names {
        numbers: &numbers,
        ids: &ids,
        by_number: &by_number,
    };
    let (mut tree, mut errs) = (Tally::default(), Vec::new());
    let (files, renames) = rewrite_tree(&root, &[], &r, &names, &mut tree, &mut errs)?;
    if !errs.is_empty() {
        let n = errs.len();
        errs.truncate(60);
        return Err(format!(
            "rewrite-ids refused, nothing written -- {n} problem(s); fix the text or extend the mapping:\n{}",
            errs.join("\n")
        ));
    }
    println!(
        "rewrite-ids: {} reference(s) in {} tracked file(s), {} path(s) renamed{}",
        tree.by_prefix.values().sum::<usize>(),
        files.len(),
        renames.len(),
        if a.has("--dry-run") {
            " (dry run: nothing written)"
        } else {
            ""
        }
    );
    for (k, v) in &tree.by_prefix {
        println!("  {k}: {v}");
    }
    if a.has("--dry-run") {
        return Ok(());
    }
    let mut writes = BTreeMap::new();
    for (f, b, _) in files {
        let mode = fs::metadata(root.join(&f))
            .map_err(|e| e.to_string())?
            .permissions();
        writes.insert(f, (b, mode));
    }
    let mut removes = Vec::new();
    for (from, to) in &renames {
        if fs::symlink_metadata(root.join(to)).is_ok() || writes.contains_key(to) {
            return Err(format!(
                "rename {from} -> {to}: destination already exists; nothing written"
            ));
        }
        let data = match writes.remove(from) {
            Some(d) => d,
            None => (
                fs::read(root.join(from)).map_err(|e| e.to_string())?,
                fs::metadata(root.join(from))
                    .map_err(|e| e.to_string())?
                    .permissions(),
            ),
        };
        writes.insert(to.clone(), data);
        removes.push(from.clone());
    }
    publish(&root, &writes, &removes, || Ok(()))
}

/// mios.toml with [tasks.store]'s migration keys rewritten: the identity digest, the frozen digests, and the
/// new `numbered` and `purged`. Everything else, line ends included, keeps its bytes.
fn ssot_section(text: &str, p: &Plan) -> Result<String, String> {
    let eol = if text.contains("[tasks.store]\r\n") {
        "\r\n"
    } else {
        "\n"
    };
    let start = text
        .find("[tasks.store]")
        .ok_or("mios.toml has no [tasks.store]")?;
    let body_end = text[start + 1..]
        .match_indices("\n[")
        .map(|(k, _)| start + 1 + k + 1)
        .next()
        .unwrap_or(text.len());
    let sect = &text[start..body_end];
    let from = sect
        .find("# Records carried by the migration")
        .or_else(|| sect.find("migrated ="))
        .ok_or("[tasks.store] has no migrated key")?;
    let fz = sect
        .find("frozen = [")
        .ok_or("[tasks.store] has no frozen list")?;
    let close = sect[fz..]
        .find(&format!("{eol}]"))
        .map(|k| fz + k + eol.len() + 1)
        .ok_or("[tasks.store].frozen is not closed by a ']' line")?;
    let migrated = sect[from..]
        .lines()
        .find_map(|l| {
            l.trim()
                .strip_prefix("migrated =")
                .map(|v| v.trim().to_string())
        })
        .ok_or("[tasks.store] has no migrated key")?;
    let w = p.frozen.iter().map(|f| f.0.len()).max().unwrap_or(0) + 3;
    let mut s = String::new();
    let mut put = |l: &str| {
        s.push_str(l);
        s.push_str(eol);
    };
    put("# Records carried by the migration; each keeps a non-null provenance and at least one slice, so a dropped one");
    put("# fails the check. The digest freezes their identity -- number, opaque id and slice set (mios-task check names");
    put("# the current digest) -- so a swap that keeps the count, a renumbered task or a stripped slice fails too.");
    put(&format!("migrated = {migrated}"));
    put(&format!("migrated_sha256 = \"{}\"", p.digest));
    put("# Frozen history: every byte of these lists, as last merged and with every old task id rewritten to its typed");
    put("# id by `mios-task migrate-ids`, lives in the records' provenance.sources and rebuilds to this digest");
    put("# (`mios-task source <source>`). Git history keeps the bytes from before the rewrite.");
    put("frozen = [");
    for (src, bytes, digest) in &p.frozen {
        let q = format!("\"{src}\",");
        put(&format!(
            "  {{ source = {q:<w$} bytes = {bytes:>8}, sha256 = \"{digest}\" }},"
        ));
    }
    put("]");
    put("# The highest task number issued (ADR-0028). Numbers are never reissued: the list's highest may not fall");
    put("# below this, so a dropped last task cannot hand its number on. Raising it to the current highest tightens it.");
    put(&format!(
        "numbered = {}",
        p.numbers.iter().max().copied().unwrap_or(0)
    ));
    put("# The old task-id shapes migrate-ids purged. `mios-task check --tree` fails on any of them in a tracked file;");
    put("# a task is named by its typed id task_<number> (the opaque task_<ULID> in tasks.jsonl's structured fields).");
    put("purged = [");
    for x in &p.shapes {
        put(&format!("  '{x}',"));
    }
    put("]");
    let rest = &sect[close..];
    let rest = rest.strip_prefix(eol).unwrap_or(rest);
    Ok(format!(
        "{}{}{s}{rest}{}",
        &text[..start],
        &sect[..from],
        &text[body_end..]
    ))
}

/// Publish an owned worktree migration with byte/mode backups. Each replacement is atomic;
/// an ordinary write or final validation error restores all touched paths. Backups stay on disk
/// if restoration fails, so the error names recoverable evidence instead of claiming success.
fn publish(
    root: &Path,
    writes: &BTreeMap<String, (Vec<u8>, fs::Permissions)>,
    removes: &[String],
    validate: impl FnOnce() -> Result<(), String>,
) -> Result<(), String> {
    use std::path::Component;
    let paths: std::collections::BTreeSet<&String> = writes.keys().chain(removes.iter()).collect();
    let mut before = BTreeMap::new();
    for rel in paths {
        let path = Path::new(rel);
        if path
            .components()
            .any(|c| !matches!(c, Component::Normal(_)))
            || path
                .components()
                .next()
                .is_some_and(|c| c.as_os_str() == ".git")
        {
            return Err(format!("unsafe migration path {rel}; nothing written"));
        }
        let mut current = root.to_path_buf();
        for part in path.components() {
            current.push(part);
            match fs::symlink_metadata(&current) {
                Ok(m) if m.file_type().is_symlink() => {
                    return Err(format!(
                        "migration path is a symlink: {}; nothing written",
                        current.display()
                    ));
                }
                Ok(m) if current != root.join(rel) && !m.is_dir() => {
                    return Err(format!(
                        "migration parent is not a directory: {}; nothing written",
                        current.display()
                    ));
                }
                Ok(_) => {}
                Err(e) if e.kind() == std::io::ErrorKind::NotFound => {}
                Err(e) => return Err(format!("{}: {e}; nothing written", current.display())),
            }
        }
        let dst = root.join(rel);
        let old = match fs::symlink_metadata(&dst) {
            Ok(m) if m.is_file() => Some((
                fs::read(&dst).map_err(|e| format!("{rel}: {e}"))?,
                m.permissions(),
            )),
            Ok(_) => {
                return Err(format!(
                    "migration target is not a regular file: {rel}; nothing written"
                ))
            }
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => None,
            Err(e) => return Err(format!("{rel}: {e}; nothing written")),
        };
        before.insert(rel.clone(), old);
    }
    let nonce = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map_err(|e| e.to_string())?
        .as_nanos();
    let stage = root.join(format!(".mios-task-migrate-{}-{nonce}", std::process::id()));
    fs::create_dir(&stage).map_err(|e| format!("{}: {e}", stage.display()))?;
    let cleanup = || -> Result<(), String> {
        for entry in fs::read_dir(&stage).map_err(|e| e.to_string())? {
            fs::remove_file(entry.map_err(|e| e.to_string())?.path()).map_err(|e| e.to_string())?;
        }
        fs::remove_dir(&stage).map_err(|e| e.to_string())
    };
    let prepared = (|| -> Result<(), String> {
        for (i, (_, old)) in before.iter().enumerate() {
            if let Some((bytes, mode)) = old {
                let backup = stage.join(format!("{i}.old"));
                fs::write(&backup, bytes).map_err(|e| e.to_string())?;
                fs::set_permissions(backup, mode.clone()).map_err(|e| e.to_string())?;
            }
        }
        for (i, (_, (bytes, mode))) in writes.iter().enumerate() {
            let file = stage.join(format!("{i}.new"));
            fs::write(&file, bytes).map_err(|e| e.to_string())?;
            fs::set_permissions(file, mode.clone()).map_err(|e| e.to_string())?;
        }
        Ok(())
    })();
    if let Err(e) = prepared {
        let clean = cleanup();
        return Err(format!(
            "migration staging failed, source unchanged: {e}; cleanup: {clean:?}"
        ));
    }
    let mut directories = Vec::new();
    let applied = (|| -> Result<(), String> {
        for (i, (rel, _)) in writes.iter().enumerate() {
            let dst = root.join(rel);
            let mut absent = Vec::new();
            let mut parent = dst.parent();
            while let Some(p) = parent {
                if p.exists() {
                    break;
                }
                absent.push(p.to_path_buf());
                parent = p.parent();
            }
            for p in absent.into_iter().rev() {
                fs::create_dir(&p).map_err(|e| format!("{}: {e}", p.display()))?;
                directories.push(p);
            }
            fs::rename(stage.join(format!("{i}.new")), &dst).map_err(|e| format!("{rel}: {e}"))?;
        }
        for rel in removes {
            fs::remove_file(root.join(rel)).map_err(|e| format!("{rel}: {e}"))?;
        }
        validate()
    })();
    if let Err(error) = applied {
        let mut failures = Vec::new();
        for (i, (rel, old)) in before.iter().enumerate() {
            let dst = root.join(rel);
            let result = if old.is_some() {
                fs::rename(stage.join(format!("{i}.old")), &dst)
            } else if dst.exists() {
                fs::remove_file(&dst)
            } else {
                Ok(())
            };
            if let Err(e) = result {
                failures.push(format!("{rel}: {e}"));
            }
        }
        for dir in directories.into_iter().rev() {
            if let Err(e) = fs::remove_dir(&dir) {
                failures.push(format!("{}: {e}", dir.display()));
            }
        }
        if !failures.is_empty() {
            return Err(format!(
                "migration failed: {error}; restoration incomplete: {}; backups: {}",
                failures.join("; "),
                stage.display()
            ));
        }
        cleanup()
            .map_err(|e| format!("migration restored after {error}; backup cleanup failed: {e}"))?;
        return Err(format!("migration refused and restored: {error}"));
    }
    cleanup().map_err(|e| {
        format!(
            "migration validated; backup cleanup failed at {}: {e}",
            stage.display()
        )
    })
}

pub fn cmd(st: &Store, a: &Args) -> Result<(), String> {
    let _lock = st.locked()?;
    let state = State::load(st)?;
    let problems = check::check(st, &state, None);
    if !problems.is_empty() {
        return Err(format!(
            "{} fails mios-task check ({} problem(s)); migrate a clean list:\n{}",
            st.path,
            problems.len(),
            problems
                .iter()
                .take(20)
                .cloned()
                .collect::<Vec<_>>()
                .join("\n")
        ));
    }
    if st.is_purged() {
        println!(
            "migrate-ids: {} is already migrated ([tasks.store].numbered = {}); nothing to do",
            st.path,
            st.numbered.unwrap_or(0)
        );
        return Ok(());
    }
    let ids = state.ids.clone();
    let p = plan(st, &state, a, &ids)?;
    let mut prefixes = p.store.by_prefix.clone();
    for (k, v) in &p.tree.by_prefix {
        *prefixes.entry(k.clone()).or_default() += v;
    }
    let report = json!({
        "records": p.records.len(),
        "numbered": p.numbers.iter().max(),
        "first": {"number": 1, "id": p.records.iter().find(|r| r["number"] == 1).map(|r| r["id"].clone())},
        "store": p.store.json(),
        "tree": p.tree.json(),
        "references_by_prefix": prefixes,
        "files_touched": p.files.len(),
        "files": p.files.iter().map(|(f, _, n)| json!({"path": f, "rewritten": n})).collect::<Vec<_>>(),
        "renamed": p.renames.iter().map(|(a, b)| json!({"from": a, "to": b})).collect::<Vec<_>>(),
        "workstream_names_kept": p.labels,
        "frozen": p.frozen.iter().map(|(s, b, d)| json!({"source": s, "bytes": b, "sha256": d})).collect::<Vec<_>>(),
        "migrated_sha256": p.digest,
        "purged": p.shapes,
        "dry_run": a.has("--dry-run"),
        "phase": "planned",
    });
    println!(
        "migrate-ids: {} task(s) numbered 1..{}; {} reference(s) rewritten in the list, {} in {} tracked file(s), {} path(s) renamed{}",
        p.records.len(),
        p.numbers.iter().max().unwrap_or(&0),
        p.store.by_prefix.values().sum::<usize>(),
        p.tree.by_prefix.values().sum::<usize>(),
        p.files.len(),
        p.renames.len(),
        if a.has("--dry-run") { " (dry run: source unchanged; only an explicitly requested report may be written)" } else { "" }
    );
    for (k, v) in &prefixes {
        println!("  {k}: {v}");
    }
    let toml_p = st.root.join(crate::record::SSOT);
    let text = fs::read_to_string(&toml_p).map_err(|e| format!("{}: {e}", toml_p.display()))?;
    // The SSOT may itself contain old task references. Rewrite those first, then apply the new
    // identity/frozen section once; a later tree write must never put the legacy section back.
    let rewritten = p
        .files
        .iter()
        .find(|(f, _, _)| *f == crate::record::SSOT)
        .map(|(_, b, _)| std::str::from_utf8(b))
        .transpose()
        .map_err(|e| e.to_string())?;
    let new = ssot_section(rewritten.unwrap_or(&text), &p)?;
    new.parse::<toml::Value>()
        .map_err(|e| format!("the rewritten mios.toml would not parse, nothing written: {e}"))?;
    let md = format!(
        "{}\n{}\n{}{}\n",
        overrides::BANNER,
        overrides::BEGIN,
        p.inner,
        overrides::END
    );
    let lines = overrides::parse_block(&st.doc, &md)
        .map(|b| b.lines)
        .map_err(|e| e.join("\n"))?;
    let idx = Index::new(&p.records, Some(&ids));
    let eff = overrides::apply(&p.records, &lines, &idx);
    let mut writes = BTreeMap::new();
    for (f, b, _) in &p.files {
        if f != crate::record::SSOT {
            writes.insert(
                f.clone(),
                (
                    b.clone(),
                    fs::metadata(st.root.join(f))
                        .map_err(|e| e.to_string())?
                        .permissions(),
                ),
            );
        }
    }
    for (f, b) in [
        (st.path.clone(), (p.lines.join("\n") + "\n").into_bytes()),
        (crate::record::SSOT.into(), new.into_bytes()),
        (
            st.doc.clone(),
            overrides::render(&st.path, &eff, &p.inner, &idx).into_bytes(),
        ),
    ] {
        let mode = fs::metadata(st.root.join(&f))
            .map_err(|e| format!("{f}: {e}"))?
            .permissions();
        writes.insert(f, (b, mode));
    }
    let mut removes = Vec::new();
    for (from, to) in &p.renames {
        if fs::symlink_metadata(st.root.join(to)).is_ok() || writes.contains_key(to) {
            return Err(format!(
                "rename {from} -> {to}: destination already exists; nothing written"
            ));
        }
        let data = match writes.remove(from) {
            Some(data) => data,
            None => (
                fs::read(st.root.join(from)).map_err(|e| e.to_string())?,
                fs::metadata(st.root.join(from))
                    .map_err(|e| e.to_string())?
                    .permissions(),
            ),
        };
        writes.insert(to.clone(), data);
        removes.push(from.clone());
    }
    if let Some(f) = a.one("report") {
        let report_path = std::path::absolute(f).map_err(|e| e.to_string())?;
        let source_root = fs::canonicalize(&st.root).map_err(|e| e.to_string())?;
        let resolved = fs::canonicalize(&report_path).unwrap_or(report_path);
        if writes
            .keys()
            .chain(removes.iter())
            .any(|p| source_root.join(p) == resolved)
        {
            return Err("report path overlaps a migration input; nothing written".into());
        }
        write_atomic(
            Path::new(f),
            &(serde_json::to_string_pretty(&report).map_err(|e| e.to_string())? + "\n"),
        )?;
    }
    if let Some(f) = a.one("mapping-out") {
        // The old ids live on only here, outside the tree: the tree keeps none of them.
        let out = std::path::absolute(f).map_err(|e| e.to_string())?;
        let source_root = fs::canonicalize(&st.root).map_err(|e| e.to_string())?;
        let parent = out.parent().and_then(|d| fs::canonicalize(d).ok());
        if parent.is_some_and(|d| d.starts_with(&source_root)) {
            return Err(
                "--mapping-out must lie outside the tree it purges; nothing written".into(),
            );
        }
        write_atomic(
            Path::new(f),
            &(serde_json::to_string_pretty(&p.mapping).map_err(|e| e.to_string())? + "\n"),
        )?;
    }
    if a.has("--dry-run") {
        return Ok(());
    }
    publish(&st.root, &writes, &removes, || {
        let st2 = Store::open(&st.root)?;
        let state2 = State::load(&st2)?;
        let after = check::check(&st2, &state2, None);
        if after.is_empty() {
            Ok(())
        } else {
            Err(format!(
                "migrated list fails check ({} problem(s)):\n{}",
                after.len(),
                after
                    .iter()
                    .take(20)
                    .cloned()
                    .collect::<Vec<_>>()
                    .join("\n")
            ))
        }
    })?;
    println!(
        "migrate-ids: {} is migrated and checks clean; [tasks.store] gained numbered and purged",
        st.path
    );
    Ok(())
}

/// The old-id gate over the tracked tree. Migrated: no purged shape anywhere. Before the migration: every old
/// token is one migrate-ids can rewrite (so the migration leaves none behind).
pub fn tree(st: &Store, state: &State) -> Result<Vec<String>, String> {
    let resolver;
    let shapes;
    let purged = st.is_purged();
    if purged {
        shapes = Shapes::from_list(st.purged.clone())?;
        resolver = None;
    } else {
        let r = Resolver::new(&state.records, &[])?;
        shapes = Shapes::from_list(r.shapes.list.clone())?;
        resolver = Some(r);
    }
    let mut out = Vec::new();
    let mut found = |at: &str, b: &[u8]| {
        let hits = ids::scan(b, &mut |tok| {
            if !tok.contains(['-', ' ']) {
                return None;
            }
            match &resolver {
                None => shapes.matches(tok).then_some(true),
                Some(r) => match r.text(tok, None) {
                    Some(_) => Some(false),
                    None if shapes.matches(tok) && !r.labels.contains(tok) => Some(true),
                    None => None,
                },
            }
        });
        let mut line = 1usize;
        let mut counted = 0usize;
        for h in hits.into_iter().filter(|h| h.what) {
            line += b[counted..h.start].iter().filter(|c| **c == b'\n').count();
            counted = h.start;
            out.push(if purged {
                format!(
                    "{at}:{line}: {} is a purged task id -- name the task {}_<number> (ADR-0028)",
                    h.token, state.ids.object
                )
            } else {
                format!(
                    "{at}:{line}: {} names no task in {}, so migrate-ids could not rewrite it -- name a task that exists",
                    h.token, st.path
                )
            });
        }
    };
    for f in ids::tracked_files(&st.root)? {
        found(&format!("{f} (path)"), f.as_bytes());
        let p = st.root.join(&f);
        let md = fs::symlink_metadata(&p)
            .map_err(|e| format!("tracked source {f} cannot be read: {e}"))?;
        if !md.is_file() {
            if md.file_type().is_symlink() {
                let link = fs::read_link(&p).map_err(|e| format!("{f}: {e}"))?;
                let text = link
                    .to_str()
                    .ok_or_else(|| format!("{f}: non-UTF-8 symlink target"))?;
                found(&format!("{f} (symlink target)"), text.as_bytes());
            }
            continue;
        }
        let b = fs::read(&p).map_err(|e| format!("{f}: {e}"))?;
        if ids::is_text(&b) {
            found(&f, &b);
        }
    }
    Ok(out)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn publish_restores_bytes_modes_renames_and_directories_after_validation_failure() {
        let nonce = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root =
            std::env::temp_dir().join(format!("mios-task-rollback-{}-{nonce}", std::process::id()));
        fs::create_dir(&root).unwrap();
        fs::write(root.join("a"), b"before").unwrap();
        fs::write(root.join("old"), b"rename contents").unwrap();
        let mode = fs::metadata(root.join("a")).unwrap().permissions();
        let writes = BTreeMap::from([
            ("a".into(), (b"after".to_vec(), mode.clone())),
            (
                "nested/new".into(),
                (b"rename contents".to_vec(), mode.clone()),
            ),
        ]);
        let error = publish(&root, &writes, &["old".into()], || {
            assert_eq!(fs::read(root.join("a")).unwrap(), b"after");
            assert!(!root.join("old").exists());
            assert!(root.join("nested/new").exists());
            Err("planted final validation failure".into())
        })
        .unwrap_err();
        assert!(error.contains("restored"), "{error}");
        assert_eq!(fs::read(root.join("a")).unwrap(), b"before");
        assert_eq!(fs::read(root.join("old")).unwrap(), b"rename contents");
        assert_eq!(fs::metadata(root.join("a")).unwrap().permissions(), mode);
        assert!(!root.join("nested").exists());
        assert_eq!(
            fs::read_dir(&root).unwrap().count(),
            2,
            "staging evidence removed only after restoration"
        );
        publish(&root, &writes, &["old".into()], || Ok(())).unwrap();
        assert_eq!(fs::read(root.join("a")).unwrap(), b"after");
        assert!(!root.join("old").exists());
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn publish_rejects_an_invalid_destination_before_touching_any_source() {
        let nonce = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .unwrap()
            .as_nanos();
        let root = std::env::temp_dir().join(format!(
            "mios-task-preflight-{}-{nonce}",
            std::process::id()
        ));
        fs::create_dir(&root).unwrap();
        fs::write(root.join("a"), b"before").unwrap();
        fs::create_dir(root.join("z")).unwrap();
        let mode = fs::metadata(root.join("a")).unwrap().permissions();
        let writes = BTreeMap::from([
            ("a".into(), (b"after".to_vec(), mode.clone())),
            ("z".into(), (b"invalid".to_vec(), mode)),
        ]);
        let error =
            publish(&root, &writes, &[], || panic!("must not reach validation")).unwrap_err();
        assert!(error.contains("not a regular file"), "{error}");
        assert_eq!(fs::read(root.join("a")).unwrap(), b"before");
        assert_eq!(fs::read_dir(&root).unwrap().count(), 2);
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn ranges_parse_both_spellings_and_refuse_backwards_ones() {
        assert_eq!(range_of("KAG-40..44"), Some(("KAG-".into(), 40, 44)));
        assert_eq!(
            range_of("KAG-503..KAG-510"),
            Some(("KAG-".into(), 503, 510))
        );
        assert_eq!(range_of("QCAT-01..04"), Some(("QCAT-".into(), 1, 4)));
        assert_eq!(range_of("KAG-1692..60"), None);
        assert_eq!(range_of("K-001"), None);
        assert_eq!(prefix_of("QDUP-COLOR-01"), "QDUP");
        assert_eq!(prefix_of("QG-TASK 1"), "QG");
        assert_eq!(prefix_of("K-031#2"), "K");
    }

    #[test]
    fn walk_text_skips_structured_fields() {
        let mut v = json!({"id": "A-1", "title": "A-1 x", "depends_on": ["A-1"],
                           "provenance": {"key": "A-1", "sources": [{"file": "A-1", "text": "A-1"}]}});
        walk_text(&mut v, "", &mut |_, x| x.replace("A-1", "Z"));
        assert_eq!(v["id"], "A-1");
        assert_eq!(v["title"], "Z x");
        assert_eq!(v["depends_on"][0], "A-1");
        assert_eq!(v["provenance"]["key"], "A-1");
        assert_eq!(v["provenance"]["sources"][0]["file"], "A-1");
        assert_eq!(v["provenance"]["sources"][0]["text"], "Z");
    }
}
