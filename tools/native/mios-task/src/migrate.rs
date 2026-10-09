// AI-hint: One-shot `mios-task migrate-canonical` -- folds the retired TASKS.jsonl store and the lane file into tasks.jsonl, keeping each record's id, status and every byte it owned (provenance.sources), counting every input both ways.
// AI-related: tasks.jsonl, /usr/lib/mios/schemas/task-record.schema.json, /usr/share/doc/mios/adr/0028-one-canonical-task-list.md
// AI-functions: Inputs, migrate, store_record, lane_only_record, place_deps, lane_ids, lane_deps, kept_sources, chain_after, frozen_table

use crate::check::sha;
use crate::record::{canonical_status, dumps, is_iso_date, Schema};
use serde_json::{json, Map, Value};
use std::collections::{BTreeMap, HashMap, HashSet};

pub struct Inputs {
    /// The retired store's lines, in file order.
    pub store: Vec<Value>,
    /// The lane file's lines, raw and parsed, in file order.
    pub lane: Vec<(String, Value)>,
    /// The lane file's bytes, as the frozen history must rebuild them.
    pub lane_text: String,
    /// The lane file's origin as the store names it, e.g. "MiOS:<lane path>".
    pub lane_origin: String,
    /// Store key -> "mios" | "toolkit", from the operator's decisions files.
    pub classes: HashMap<String, String>,
}

pub struct Outcome {
    pub lines: Vec<String>,
    pub report: Value,
}

const MIOS_SIDE: [&str; 2] = ["MiOS:", "mios-micro:"];
const LANE_FIELDS: [&str; 17] = [
    "id",
    "type",
    "title",
    "status",
    "owner",
    "epic",
    "goal",
    "depends_on",
    "acceptance_criteria",
    "verification",
    "verification_evidence",
    "links",
    "notes",
    "created",
    "updated",
    "archived_dependencies",
    "legacy_status",
];

fn st<'a>(v: &'a Value, k: &str) -> Option<&'a str> {
    v.get(k).and_then(|x| x.as_str())
}

fn strs(v: Option<&Value>) -> Vec<String> {
    v.and_then(|x| x.as_array())
        .map(|a| {
            a.iter()
                .map(|x| {
                    x.as_str()
                        .map(str::to_string)
                        .unwrap_or_else(|| x.to_string())
                })
                .collect()
        })
        .unwrap_or_default()
}

fn opt(v: Option<&str>) -> Value {
    v.map(|s| Value::String(s.to_string()))
        .unwrap_or(Value::Null)
}

/// The JSON text of a v1 `value_json`, as a plain string when it held one.
fn decoded(value_json: &str) -> String {
    match serde_json::from_str::<Value>(value_json) {
        Ok(Value::String(s)) => s,
        _ => value_json.to_string(),
    }
}

fn enum_or_raw(raw: Option<&str>, allowed: &[&str], key: &str, extra: &mut Vec<Value>) -> Value {
    match raw {
        None => Value::Null,
        Some(r) if allowed.contains(&r) => json!(r),
        Some(r) => {
            extra.push(json!({"key": format!("{key}_raw"), "value": r}));
            let head = r.split_whitespace().next().unwrap_or("");
            if allowed.contains(&head) {
                json!(head)
            } else {
                Value::Null
            }
        }
    }
}

fn date(v: Option<&str>, key: &str, extra: &mut Vec<Value>) -> Value {
    match v {
        Some(s) if is_iso_date(s) => json!(s),
        Some("") | None => Value::Null,
        Some(s) => {
            extra.push(json!({"key": format!("{key}_raw"), "value": s}));
            Value::Null
        }
    }
}

struct Kept<'a> {
    rec: &'a Value,
    key: String,
    id: String,
    aliases: Vec<String>,
    classification: &'static str,
    lane: Option<usize>,
}

pub fn migrate(inp: &Inputs, schema: &Schema) -> Result<Outcome, String> {
    let mut fails: Vec<String> = Vec::new();
    let store_keys: HashSet<&str> = inp.store.iter().filter_map(|r| st(r, "key")).collect();
    let mut unknown: Vec<&String> = inp
        .classes
        .keys()
        .filter(|k| !store_keys.contains(k.as_str()))
        .collect();
    unknown.sort();
    for k in unknown {
        fails.push(format!(
            "decisions name key {k:?}, which is not in the store"
        ));
    }
    for (k, c) in &inp.classes {
        if c != "mios" && c != "toolkit" {
            fails.push(format!("decision for {k:?} is {c:?}, not mios or toolkit"));
        }
    }

    // 1. Inclusion.
    let mut kept: Vec<Kept> = Vec::new();
    let mut excluded_toolkit: Vec<String> = Vec::new();
    let mut excluded_devloop: Vec<String> = Vec::new();
    let mut key_map: BTreeMap<String, String> = BTreeMap::new();
    for r in &inp.store {
        let key = st(r, "key").unwrap_or("").to_string();
        let id = st(r, "id").unwrap_or("").to_string();
        let origins: Vec<String> = r
            .get("sources")
            .and_then(|s| s.as_array())
            .map(|a| {
                a.iter()
                    .filter_map(|s| st(s, "source_file").map(str::to_string))
                    .collect()
            })
            .unwrap_or_default();
        let mios = origins
            .iter()
            .all(|o| MIOS_SIDE.iter().any(|p| o.starts_with(p)));
        let toolkit = origins
            .iter()
            .all(|o| !MIOS_SIDE.iter().any(|p| o.starts_with(p)));
        let class = match inp.classes.get(&key).map(String::as_str) {
            Some("mios") => Some("mios-classified"),
            Some("toolkit") => None,
            Some(_) => continue,
            None if mios => Some("mios-source"),
            None if toolkit => {
                excluded_devloop.push(key.clone());
                key_map.insert(key, "excluded:devloop-origin".into());
                continue;
            }
            None => {
                fails.push(format!(
                    "{key}: origins {origins:?} mix MiOS and other repos and no decision classifies it"
                ));
                continue;
            }
        };
        match class {
            Some(c) => kept.push(Kept {
                rec: r,
                key,
                id,
                aliases: vec![],
                classification: c,
                lane: None,
            }),
            None => {
                excluded_toolkit.push(key.clone());
                key_map.insert(key, "excluded:toolkit".into());
            }
        }
    }

    // 2. Ids: a key that is not the id keeps the '#n' form; never remint.
    let plain: HashSet<String> = kept
        .iter()
        .filter(|k| k.key == k.id)
        .map(|k| k.id.clone())
        .collect();
    let mut taken: HashSet<String> = plain.clone();
    for k in kept.iter_mut().filter(|k| k.key != k.id) {
        let new = if k.key.starts_with(&format!("{}#", k.id)) {
            k.key.clone()
        } else {
            let mut n = 2;
            while taken.contains(&format!("{}#{n}", k.id)) {
                n += 1;
            }
            format!("{}#{n}", k.id)
        };
        if !taken.insert(new.clone()) {
            fails.push(format!("{}: its new id {new} is already taken", k.key));
        }
        k.aliases = vec![k.id.clone()];
        k.id = new;
    }

    // 3. Lane records: merge into their store record, or stand alone.
    let lane_tag = format!("{}#", inp.lane_origin);
    let by_key: HashMap<String, usize> = kept
        .iter()
        .enumerate()
        .map(|(i, k)| (k.key.clone(), i))
        .collect();
    let has_lane_source = |r: &Value| {
        r.get("sources")
            .and_then(|s| s.as_array())
            .is_some_and(|a| {
                a.iter()
                    .any(|s| st(s, "source_file") == Some(&inp.lane_origin))
            })
    };
    let mut lane_rename: HashMap<String, String> = HashMap::new();
    let mut lane_only: Vec<usize> = Vec::new();
    let mut merged = 0usize;
    let excluded: HashSet<&str> = excluded_toolkit
        .iter()
        .chain(excluded_devloop.iter())
        .map(String::as_str)
        .collect();
    for (li, (_, l)) in inp.lane.iter().enumerate() {
        let id = st(l, "id").unwrap_or("").to_string();
        let distinct = format!("{lane_tag}{id}");
        let target = match by_key.get(&id) {
            Some(&i) if has_lane_source(kept[i].rec) => Some(i),
            _ => by_key.get(&distinct).copied(),
        };
        match target {
            Some(i) => {
                if kept[i].lane.is_some() {
                    fails.push(format!("lane id {id} appears twice in the lane file"));
                }
                kept[i].lane = Some(li);
                merged += 1;
                if kept[i].id != id {
                    lane_rename.insert(id.clone(), kept[i].id.clone());
                }
            }
            None => {
                if excluded.contains(id.as_str()) || excluded.contains(distinct.as_str()) {
                    fails.push(format!(
                        "lane id {id} merges into a store record the decisions exclude"
                    ));
                } else if taken.contains(&id) {
                    fails.push(format!(
                        "lane-only id {id} collides with a different store task of the same id; decide which keeps it"
                    ));
                } else {
                    taken.insert(id.clone());
                    lane_only.push(li);
                }
            }
        }
    }

    // 4. Statuses: the retired store's word is the record's status; a lane record keeps its lane word,
    // which is newer. Both are reported when they differ, nothing is re-derived from prose.
    let mut changed: Vec<Value> = Vec::new();
    let mut statuses: Vec<&'static str> = Vec::new();
    for k in &kept {
        let old = st(k.rec, "status").unwrap_or("");
        let (new, why): (Option<&'static str>, String) = match k.lane {
            Some(li) => {
                let w = st(&inp.lane[li].1, "status").unwrap_or("");
                (canonical_status(w), format!("lane word {w:?}"))
            }
            None => (canonical_status(old), format!("store word {old:?}")),
        };
        match new {
            Some(s) => {
                if s != old {
                    changed.push(json!({"id": k.id, "from": old, "to": s, "by": why}));
                }
                statuses.push(s);
            }
            None => {
                fails.push(format!("{}: unknown status ({why})", k.key));
                statuses.push("pending");
            }
        }
    }
    for li in &lane_only {
        let l = &inp.lane[*li].1;
        let w = st(l, "status").unwrap_or("");
        if canonical_status(w).is_none() {
            fails.push(format!(
                "lane {}: unknown status word {w:?}",
                st(l, "id").unwrap_or("?")
            ));
        }
    }
    // A merged lane record's store slice must be the lane line as it is now: else the store is stale.
    for k in &kept {
        if let Some(li) = k.lane {
            let line = format!("{}\n", inp.lane[li].0);
            let has = k
                .rec
                .get("sources")
                .and_then(|x| x.as_array())
                .is_some_and(|a| {
                    a.iter().any(|s| {
                        st(s, "source_file") == Some(&inp.lane_origin)
                            && st(s, "text") == Some(line.as_str())
                    })
                });
            if !has {
                fails.push(format!(
                    "{}: the store does not hold lane line {} as it is now -- the store is stale against the lane file",
                    k.key,
                    li + 1
                ));
            }
        }
    }
    if !fails.is_empty() {
        return Err(fails.join("\n"));
    }

    // 5. Records.
    let ids: HashSet<String> = kept
        .iter()
        .map(|k| k.id.clone())
        .chain(
            lane_only
                .iter()
                .map(|i| st(&inp.lane[*i].1, "id").unwrap_or("").to_string()),
        )
        .collect();
    let mut dangling: Vec<Value> = Vec::new();
    let mut lk = LaneLinks {
        ids: &ids,
        rename: &lane_rename,
        rewrites: 0,
        archived: Vec::new(),
    };
    let mut out: Vec<Value> = Vec::new();
    for (k, &status) in kept.iter().zip(statuses.iter()) {
        let lane = k.lane.map(|i| &inp.lane[i]);
        let rec = store_record(k, status, lane, inp, &mut lk, &mut dangling);
        key_map.insert(k.key.clone(), k.id.clone());
        out.push(rec);
    }
    for li in &lane_only {
        let l = &inp.lane[*li].1;
        out.push(lane_only_record(l, inp, &mut lk, &mut dangling));
    }
    let (rewrites, archived) = (lk.rewrites, lk.archived);
    // An unschedulable cycle: each edge inside it becomes `related`, and the report lists it.
    let mut cycle_edges: Vec<Value> = Vec::new();
    for comp in crate::check::cycles(&out) {
        let set: HashSet<&str> = comp.iter().map(String::as_str).collect();
        for r in out.iter_mut() {
            let id = st(r, "id").unwrap_or("").to_string();
            if !set.contains(id.as_str()) {
                continue;
            }
            let deps = strs(r.get("depends_on"));
            let (inside, keep): (Vec<String>, Vec<String>) =
                deps.into_iter().partition(|d| set.contains(d.as_str()));
            if inside.is_empty() {
                continue;
            }
            let mut related: Vec<Value> = r
                .get("related")
                .and_then(|x| x.as_array())
                .cloned()
                .unwrap_or_default();
            for d in inside {
                cycle_edges.push(json!({"id": id, "dep": d, "cycle": comp}));
                let e = json!({"id": d, "type": "related"});
                if !related.contains(&e) {
                    related.push(e);
                }
            }
            if let Some(m) = r.as_object_mut() {
                m.insert("depends_on".into(), json!(keep));
                m.insert("related".into(), Value::Array(related));
            }
        }
    }
    // Lane-only records own their lane line; every slice then learns which record owns the one before it.
    let offsets = line_offsets(&inp.lane_text);
    for (n, li) in lane_only.iter().enumerate() {
        let raw = format!("{}\n", inp.lane[*li].0);
        let r = &mut out[kept.len() + n];
        r["provenance"]["sources"] = json!([slice(&inp.lane_origin, "jsonl", offsets[*li], &raw)]);
    }
    chain_after(&mut out);
    let frozen = frozen_table(inp, &out)?;
    let lines: Vec<String> = out
        .iter()
        .map(|r| dumps(&schema.canon(r, &schema.record)))
        .collect();

    // 6. Both-ways counts; a mismatch is a bug, never a report line.
    let store_in = inp.store.len();
    let (n_kept, n_tool, n_dev) = (kept.len(), excluded_toolkit.len(), excluded_devloop.len());
    if store_in != n_kept + n_tool + n_dev {
        return Err(format!(
            "count mismatch: store_in {store_in} != kept {n_kept} + toolkit {n_tool} + devloop-origin {n_dev}"
        ));
    }
    if inp.lane.len() != merged + lane_only.len() {
        return Err(format!(
            "count mismatch: lane_in {} != merged {merged} + lane_only {}",
            inp.lane.len(),
            lane_only.len()
        ));
    }
    if lines.len() != n_kept + lane_only.len() {
        return Err("count mismatch: out_lines != kept + lane_only".into());
    }
    if key_map.len() != store_keys.len() {
        return Err(format!(
            "count mismatch: {} store keys mapped of {}",
            key_map.len(),
            store_keys.len()
        ));
    }
    let mut lane_map: BTreeMap<String, String> = BTreeMap::new();
    for (_, l) in &inp.lane {
        let id = st(l, "id").unwrap_or("").to_string();
        let to = lane_rename.get(&id).cloned().unwrap_or_else(|| id.clone());
        lane_map.insert(id, to);
    }
    let mut tally: BTreeMap<&str, usize> = BTreeMap::new();
    for c in inp.classes.values() {
        *tally.entry(c.as_str()).or_default() += 1;
    }
    let mut cls: BTreeMap<&str, usize> = BTreeMap::new();
    for k in &kept {
        *cls.entry(k.classification).or_default() += 1;
    }
    let mut by_status: BTreeMap<&str, usize> = BTreeMap::new();
    for r in &out {
        *by_status.entry(st(r, "status").unwrap_or("")).or_default() += 1;
    }
    let report = json!({
        "store_in": store_in,
        "lane_in": inp.lane.len(),
        "kept": n_kept,
        "excluded_toolkit": n_tool,
        "excluded_devloop_origin": n_dev,
        "merged_into_store": merged,
        "lane_only": lane_only.len(),
        "out_lines": lines.len(),
        "balance": {
            "store_in == kept + excluded_toolkit + excluded_devloop_origin": store_in == n_kept + n_tool + n_dev,
            "lane_in == merged_into_store + lane_only": inp.lane.len() == merged + lane_only.len(),
            "out_lines == kept + lane_only": lines.len() == n_kept + lane_only.len(),
        },
        "decisions_tally": tally,
        "kept_by_classification": cls,
        "out_by_status": by_status,
        "excluded_toolkit_keys": excluded_toolkit,
        "excluded_devloop_origin_keys": excluded_devloop,
        "renamed": kept.iter().filter(|k| !k.aliases.is_empty())
            .map(|k| json!({"key": k.key, "id": k.id, "aliases": k.aliases})).collect::<Vec<_>>(),
        "lane_dep_rewrites": rewrites,
        "dangling_blocks_moved_to_related": dangling,
        "archived_dependencies_kept_as_related": archived,
        "cycle_edges_moved_to_related": cycle_edges,
        "changed_status": changed,
        "frozen": frozen,
        "migrated_sha256": crate::check::identity(&out).1,
        "store_key_map": key_map,
        "lane_id_map": lane_map,
    });
    Ok(Outcome { lines, report })
}

fn place_deps(
    id: &str,
    wanted: Vec<String>,
    ids: &HashSet<String>,
    related: &mut Vec<Value>,
    dangling: &mut Vec<Value>,
) -> Vec<String> {
    let mut deps: Vec<String> = Vec::new();
    for d in wanted {
        if d == id || deps.contains(&d) {
            continue;
        }
        if ids.contains(&d) {
            deps.push(d);
        } else {
            dangling.push(json!({"id": id, "dep": d}));
            let e = json!({"id": d, "type": "references"});
            if !related.contains(&e) {
                related.push(e);
            }
        }
    }
    deps
}

fn lane_verification(l: &Value, statements: Vec<String>) -> Value {
    let v = l.get("verification");
    let g = |k: &str| opt(v.and_then(|x| x.get(k)).and_then(|x| x.as_str()));
    json!({
        "positive_cmd": g("positive_cmd"),
        "negative_control_cmd": g("negative_control_cmd"),
        "negative_expect": g("negative_expect"),
        "statements": statements,
    })
}

/// A lane record's ids under `key` (depends_on or archived_dependencies), each renamed to its kept id.
fn lane_ids(
    l: &Value,
    key: &str,
    rename: &HashMap<String, String>,
    rewrites: &mut usize,
) -> Vec<String> {
    strs(l.get(key))
        .into_iter()
        .map(|d| match rename.get(&d) {
            Some(n) => {
                *rewrites += 1;
                n.clone()
            }
            None => d,
        })
        .collect()
}

/// The lane's blocking edges, plus each edge the toolkit archived out of depends_on (archived_dependencies),
/// which stays history: a `related` entry of type "archived", never a block.
fn lane_deps(
    id: &str,
    l: &Value,
    lk: &mut LaneLinks,
    related: &mut Vec<Value>,
    dangling: &mut Vec<Value>,
) -> Vec<String> {
    let wanted = lane_ids(l, "depends_on", lk.rename, &mut lk.rewrites);
    let deps = place_deps(id, wanted, lk.ids, related, dangling);
    for d in lane_ids(l, "archived_dependencies", lk.rename, &mut lk.rewrites) {
        lk.archived.push(json!({"id": id, "dep": d}));
        let e = json!({"id": d, "type": "archived"});
        if !related.contains(&e) {
            related.push(e);
        }
    }
    deps
}

/// What lane-record conversion needs from the whole migration, and what it reports back.
struct LaneLinks<'a> {
    ids: &'a HashSet<String>,
    rename: &'a HashMap<String, String>,
    rewrites: usize,
    archived: Vec<Value>,
}

fn evidence(status: &str, given: &str, origin: &str, raw: Option<&str>) -> String {
    if status == "completed" && given.trim().is_empty() {
        format!(
            "migrated from {origin} (status_raw: {}); see provenance.sources",
            raw.unwrap_or("null")
        )
    } else {
        given.to_string()
    }
}

fn store_record(
    k: &Kept,
    status: &str,
    lane: Option<&(String, Value)>,
    inp: &Inputs,
    lk: &mut LaneLinks,
    dangling: &mut Vec<Value>,
) -> Value {
    let r = k.rec;
    let origin = st(r, "origin").unwrap_or("").to_string();
    let lane_origin = origin == inp.lane_origin;
    let mut extra: Vec<Value> = Vec::new();
    for e in r
        .get("extra")
        .and_then(|x| x.as_array())
        .cloned()
        .unwrap_or_default()
    {
        let key = st(&e, "key").unwrap_or("").to_string();
        if lane_origin && LANE_FIELDS.contains(&key.as_str()) {
            continue; // carried by its typed v2 field
        }
        extra.push(json!({"key": key, "value": decoded(st(&e, "value_json").unwrap_or(""))}));
    }
    let priority = enum_or_raw(
        st(r, "priority"),
        &["P0", "P1", "P2", "P3"],
        "priority",
        &mut extra,
    );
    let size = enum_or_raw(st(r, "size"), &["S", "M", "L", "XL"], "size", &mut extra);
    let rtype = match st(r, "type") {
        Some("epic") => "epic",
        Some("bug") => "bug",
        _ => "task",
    };
    let mut related: Vec<Value> = Vec::new();
    let mut blocks: Vec<String> = Vec::new();
    for d in r
        .get("deps")
        .and_then(|x| x.as_array())
        .cloned()
        .unwrap_or_default()
    {
        let (did, ty) = (
            st(&d, "id").unwrap_or("").to_string(),
            st(&d, "type").unwrap_or(""),
        );
        if ty == "blocks" {
            blocks.push(did);
        } else {
            let e = json!({"id": did, "type": ty});
            if !related.contains(&e) {
                related.push(e);
            }
        }
    }
    let mut also: Vec<String> = r
        .get("sources")
        .and_then(|x| x.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|s| st(s, "source_file").map(str::to_string))
                .filter(|s| *s != origin)
                .collect()
        })
        .unwrap_or_default();
    also.sort();
    also.dedup();
    let conflicts: Vec<Value> = r
        .get("conflicts")
        .and_then(|x| x.as_array())
        .cloned()
        .unwrap_or_default()
        .iter()
        .map(|c| {
            json!({"field": st(c, "field").unwrap_or(""), "origin": st(c, "source_file").unwrap_or(""),
                   "value": decoded(st(c, "value_json").unwrap_or(""))})
        })
        .collect();
    let store_verify = strs(r.get("verify"));
    let (
        owner,
        epic,
        goal,
        deps,
        ac,
        verification,
        ev,
        links,
        notes,
        created,
        updated,
        status_raw,
        title_raw,
    ) = match lane {
        Some((_, l)) => {
            let deps = lane_deps(&k.id, l, lk, &mut related, dangling);
            let goal = st(r, "goal").or(st(l, "goal")).unwrap_or("").to_string();
            let w = st(l, "status");
            (
                st(l, "owner").unwrap_or("").to_string(),
                st(l, "epic").unwrap_or("").to_string(),
                goal,
                deps,
                strs(l.get("acceptance_criteria")),
                lane_verification(l, store_verify),
                evidence(
                    status,
                    st(l, "verification_evidence").unwrap_or(""),
                    &inp.lane_origin,
                    w,
                ),
                strs(l.get("links")),
                st(l, "notes").unwrap_or("").to_string(),
                date(st(l, "created"), "created", &mut extra),
                date(st(l, "updated"), "updated", &mut extra),
                opt(w),
                opt(st(l, "title")),
            )
        }
        None => (
            String::new(),
            String::new(),
            st(r, "goal").unwrap_or("").to_string(),
            place_deps(&k.id, blocks, lk.ids, &mut related, dangling),
            strs(r.get("done_when")),
            json!({"positive_cmd": null, "negative_control_cmd": null, "negative_expect": null,
                       "statements": store_verify}),
            evidence(status, "", &origin, st(r, "status_raw")),
            vec![],
            String::new(),
            Value::Null,
            Value::Null,
            opt(st(r, "status_raw")),
            Value::Null,
        ),
    };
    let owner_raw = if lane_origin {
        Value::Null
    } else {
        opt(st(r, "owner"))
    };
    let mut m = Map::new();
    m.insert("id".into(), json!(k.id));
    m.insert("type".into(), json!(rtype));
    m.insert("title".into(), json!(st(r, "title").unwrap_or("")));
    m.insert("status".into(), json!(status));
    m.insert("owner".into(), json!(owner));
    m.insert("epic".into(), json!(epic));
    m.insert("goal".into(), json!(goal));
    m.insert("priority".into(), priority);
    m.insert("size".into(), size);
    m.insert("workstream".into(), opt(st(r, "workstream")));
    m.insert("domain".into(), opt(st(r, "domain")));
    m.insert("depends_on".into(), json!(deps));
    m.insert("related".into(), json!(related));
    m.insert("acceptance_criteria".into(), json!(ac));
    m.insert("verification".into(), verification);
    m.insert("verification_evidence".into(), json!(ev));
    m.insert(
        "details".into(),
        json!({"what_how": opt(st(r, "what_how")), "where": opt(st(r, "where")),
               "why": opt(st(r, "why")), "do_not": opt(st(r, "do_not"))}),
    );
    m.insert("links".into(), json!(links));
    m.insert("notes".into(), json!(notes));
    m.insert("extra".into(), json!(extra));
    m.insert("created".into(), created);
    m.insert("updated".into(), updated);
    m.insert(
        "provenance".into(),
        json!({
            "origin": origin,
            "key": k.key,
            "aliases": k.aliases,
            "type_raw": opt(st(r, "type")),
            "status_raw": status_raw,
            "owner_raw": owner_raw,
            "title_raw": title_raw,
            "also_in": also,
            "conflicts": conflicts,
            "classification": k.classification,
            "sources": kept_sources(r),
        }),
    );
    Value::Object(m)
}

fn lane_only_record(
    l: &Value,
    inp: &Inputs,
    lk: &mut LaneLinks,
    dangling: &mut Vec<Value>,
) -> Value {
    let id = st(l, "id").unwrap_or("").to_string();
    let w = st(l, "status");
    let status = w.and_then(canonical_status).unwrap_or("pending");
    let mut extra: Vec<Value> = Vec::new();
    if let Some(o) = l.as_object() {
        for (k, v) in o {
            if !LANE_FIELDS.contains(&k.as_str()) {
                extra.push(json!({"key": k, "value": v.as_str().map(str::to_string).unwrap_or_else(|| v.to_string())}));
            }
        }
    }
    let mut related = Vec::new();
    let deps = lane_deps(&id, l, lk, &mut related, dangling);
    let rtype = match st(l, "type") {
        Some("epic") => "epic",
        Some("bug") => "bug",
        _ => "task",
    };
    let created = date(st(l, "created"), "created", &mut extra);
    let updated = date(st(l, "updated"), "updated", &mut extra);
    let mut m = Map::new();
    m.insert("id".into(), json!(id));
    m.insert("type".into(), json!(rtype));
    m.insert("title".into(), json!(st(l, "title").unwrap_or("")));
    m.insert("status".into(), json!(status));
    m.insert("owner".into(), json!(st(l, "owner").unwrap_or("")));
    m.insert("epic".into(), json!(st(l, "epic").unwrap_or("")));
    m.insert("goal".into(), json!(st(l, "goal").unwrap_or("")));
    m.insert("priority".into(), Value::Null);
    m.insert("size".into(), Value::Null);
    m.insert("workstream".into(), Value::Null);
    m.insert("domain".into(), Value::Null);
    m.insert("depends_on".into(), json!(deps));
    m.insert("related".into(), json!(related));
    m.insert(
        "acceptance_criteria".into(),
        json!(strs(l.get("acceptance_criteria"))),
    );
    m.insert("verification".into(), lane_verification(l, vec![]));
    m.insert(
        "verification_evidence".into(),
        json!(evidence(
            status,
            st(l, "verification_evidence").unwrap_or(""),
            &inp.lane_origin,
            w
        )),
    );
    m.insert(
        "details".into(),
        json!({"what_how": null, "where": null, "why": null, "do_not": null}),
    );
    m.insert("links".into(), json!(strs(l.get("links"))));
    m.insert("notes".into(), json!(st(l, "notes").unwrap_or("")));
    m.insert("extra".into(), json!(extra));
    m.insert("created".into(), created);
    m.insert("updated".into(), updated);
    m.insert(
        "provenance".into(),
        json!({
            "origin": inp.lane_origin, "key": id, "aliases": [], "type_raw": opt(st(l, "type")),
            "status_raw": opt(w), "owner_raw": null, "title_raw": opt(st(l, "title")),
            "also_in": [], "conflicts": [], "classification": "lane", "sources": [],
        }),
    );
    Value::Object(m)
}

fn slice(file: &str, kind: &str, offset: usize, text: &str) -> Value {
    json!({"file": file, "kind": kind, "offset": offset, "length": text.len(),
           "sha256": sha(text), "after": "", "text": text})
}

fn line_offsets(text: &str) -> Vec<usize> {
    let mut out = Vec::new();
    let mut at = 0usize;
    for l in text.split_inclusive('\n') {
        out.push(at);
        at += l.len();
    }
    out
}

/// The slices a kept record carries: its MiOS-side slices, or -- for a MiOS record that only the toolkit
/// backlog held -- all of them.
fn kept_sources(r: &Value) -> Value {
    let all: Vec<&Value> = r
        .get("sources")
        .and_then(|x| x.as_array())
        .map(|a| a.iter().collect())
        .unwrap_or_default();
    let mios: Vec<&Value> = all
        .iter()
        .copied()
        .filter(|s| {
            st(s, "source_file").is_some_and(|f| MIOS_SIDE.iter().any(|p| f.starts_with(p)))
        })
        .collect();
    let pick = if mios.is_empty() { all } else { mios };
    Value::Array(
        pick.iter()
            .map(|s| {
                slice(
                    st(s, "source_file").unwrap_or(""),
                    st(s, "kind").unwrap_or(""),
                    s.get("byte_offset").and_then(|x| x.as_u64()).unwrap_or(0) as usize,
                    st(s, "text").unwrap_or(""),
                )
            })
            .collect(),
    )
}

/// Set every slice's `after` to the id of the record owning the slice just before it in the same file.
fn chain_after(out: &mut [Value]) {
    let mut at: Vec<(String, usize, usize, usize)> = Vec::new();
    for (ri, r) in out.iter().enumerate() {
        if let Some(a) = r["provenance"]["sources"].as_array() {
            for (si, s) in a.iter().enumerate() {
                at.push((
                    st(s, "file").unwrap_or("").to_string(),
                    s["offset"].as_u64().unwrap_or(0) as usize,
                    ri,
                    si,
                ));
            }
        }
    }
    at.sort();
    let mut prev: Option<(String, String)> = None;
    for (file, _, ri, si) in at {
        let after = match &prev {
            Some((f, id)) if *f == file => id.clone(),
            _ => String::new(),
        };
        let id = st(&out[ri], "id").unwrap_or("").to_string();
        out[ri]["provenance"]["sources"][si]["after"] = json!(after);
        prev = Some((file, id));
    }
}

/// [tasks.store].frozen for every MiOS-side list the slices hold, each proven to rebuild to the digest the
/// retired store recorded (or, for the lane file, to its bytes now).
fn frozen_table(inp: &Inputs, out: &[Value]) -> Result<Value, String> {
    let mut want: BTreeMap<String, (usize, String)> = BTreeMap::new();
    for r in &inp.store {
        for s in r
            .get("sources")
            .and_then(|x| x.as_array())
            .cloned()
            .unwrap_or_default()
        {
            let f = st(&s, "source_file").unwrap_or("").to_string();
            if MIOS_SIDE.iter().any(|p| f.starts_with(p)) && f != inp.lane_origin {
                let b = s.get("source_bytes").and_then(|x| x.as_u64()).unwrap_or(0) as usize;
                want.insert(f, (b, st(&s, "source_sha256").unwrap_or("").to_string()));
            }
        }
    }
    want.insert(
        inp.lane_origin.clone(),
        (inp.lane_text.len(), sha(&inp.lane_text)),
    );
    let all = crate::check::slices(out, &crate::record::Index::new(out, None));
    let mut table = Vec::new();
    let mut errs = Vec::new();
    for (f, (bytes, digest)) in &want {
        let fz = crate::record::Frozen {
            source: f.clone(),
            bytes: *bytes,
            sha256: digest.clone(),
        };
        match all.get(f) {
            None => errs.push(format!("{f}: no kept record holds a slice of it")),
            Some(sl) => {
                if let Err(e) = crate::check::rebuild(f, sl, Some(&fz)) {
                    errs.extend(e);
                }
            }
        }
        table.push(json!({"source": f, "bytes": bytes, "sha256": digest}));
    }
    if errs.is_empty() {
        Ok(Value::Array(table))
    } else {
        Err(errs.join("\n"))
    }
}
