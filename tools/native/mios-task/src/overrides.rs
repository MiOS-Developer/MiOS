// AI-hint: TASKS.md -- the operator-overrides block in the dev-loop toolkit's format ({"id": ..., field: value} lines under '# TASKS'), validated and applied on top of tasks.jsonl, and the deterministic render around it.
// AI-related: TASKS.md, tasks.jsonl, /usr/lib/mios/schemas/task-record.schema.json, /usr/share/doc/mios/adr/0028-one-canonical-task-list.md
// AI-functions: parse_block, validate, apply, Effective, override_value, render, empty_block, line

use crate::record::{canonical_status, Index, Schema, STATUSES};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet, HashMap};

/// The first line of a TASKS.md the dev-loop toolkit reads overrides from; any other first line makes it foreign.
pub const BANNER: &str = "# TASKS";
pub const BEGIN: &str = "<!-- overrides:begin -->";
pub const END: &str = "<!-- overrides:end -->";
/// Record fields an override may not set: identity and history.
pub const FIXED: [&str; 4] = ["id", "object", "number", "provenance"];

pub struct OverrideLine {
    /// 1-based line number in TASKS.md.
    pub n: usize,
    pub raw: String,
    pub value: Option<Value>,
}

pub struct Block {
    /// Bytes strictly between the begin and end marker lines, copied verbatim by render.
    pub inner: String,
    pub lines: Vec<OverrideLine>,
}

/// The JSON text of a block line that is an override: it starts with '{' (or '- {'), as the toolkit reads it.
fn override_text(line: &str) -> Option<&str> {
    let t = line.trim();
    let t = t.strip_prefix("- ").map(str::trim_start).unwrap_or(t);
    t.starts_with('{').then_some(t)
}

/// The block of an existing TASKS.md. Errors name the file line.
pub fn parse_block(doc: &str, md: &str) -> Result<Block, Vec<String>> {
    let lines: Vec<&str> = md.split_inclusive('\n').collect();
    let strip = |l: &str| l.trim_end_matches(['\n', '\r']).to_string();
    if lines.first().map(|l| strip(l)) != Some(BANNER.to_string()) {
        return Err(vec![format!(
            "{doc}:1: does not open with the '{BANNER}' banner, so the dev-loop toolkit would not apply its overrides -- run: mios-task render"
        )]);
    }
    let begins: Vec<usize> = (0..lines.len())
        .filter(|i| strip(lines[*i]) == BEGIN)
        .collect();
    let ends: Vec<usize> = (0..lines.len())
        .filter(|i| strip(lines[*i]) == END)
        .collect();
    let mut errs = Vec::new();
    if begins.len() != 1 {
        errs.push(format!(
            "{doc}: the overrides begin marker appears {} time(s), not once ({BEGIN})",
            begins.len()
        ));
    }
    if ends.len() != 1 {
        errs.push(format!(
            "{doc}: the overrides end marker appears {} time(s), not once ({END})",
            ends.len()
        ));
    }
    if !errs.is_empty() {
        return Err(errs);
    }
    let (b, e) = (begins[0], ends[0]);
    if e < b {
        return Err(vec![format!(
            "{doc}:{}: the overrides end marker comes before the begin marker",
            e + 1
        )]);
    }
    let inner: String = lines[b + 1..e].concat();
    let mut out = Vec::new();
    for (i, l) in lines.iter().enumerate().take(e).skip(b + 1) {
        let raw = strip(l);
        if let Some(t) = override_text(&raw) {
            out.push(OverrideLine {
                n: i + 1,
                value: serde_json::from_str(t).ok(),
                raw,
            });
        }
    }
    Ok(Block { inner, lines: out })
}

/// The value an override sets, as the record would hold it: a status in either dialect becomes the canonical word,
/// and a task named in depends_on or epic by any spelling the index knows (typed task_<number> once migrated)
/// becomes the record's id.
pub fn override_value(field: &str, v: &Value, idx: &Index) -> Value {
    let canon = |s: &str| -> Value {
        Value::String(
            idx.find(s)
                .map(|i| idx.ids[i].clone())
                .unwrap_or_else(|| s.to_string()),
        )
    };
    match (field, v) {
        ("status", Value::String(w)) => match canonical_status(w) {
            Some(c) => Value::String(c.to_string()),
            None => v.clone(),
        },
        ("epic", Value::String(e)) if !e.is_empty() => canon(e),
        ("depends_on", Value::Array(a)) => Value::Array(
            a.iter()
                .map(|x| x.as_str().map(canon).unwrap_or_else(|| x.clone()))
                .collect(),
        ),
        _ => v.clone(),
    }
}

/// Every refusal for the block's lines against the records; each names "<doc>:<line>".
pub fn validate(doc: &str, schema: &Schema, idx: &Index, lines: &[OverrideLine]) -> Vec<String> {
    let mut errs = Vec::new();
    let props = schema
        .record
        .get("properties")
        .and_then(|p| p.as_object())
        .cloned()
        .unwrap_or_default();
    for l in lines {
        let at = format!("{doc}:{}", l.n);
        let Some(o) = l.value.as_ref().and_then(|v| v.as_object()) else {
            errs.push(format!("{at}: override line is not a JSON object"));
            continue;
        };
        let Some(id) = o.get("id").and_then(|x| x.as_str()) else {
            errs.push(format!("{at}: override needs an \"id\""));
            continue;
        };
        if idx.find(id).is_none() {
            errs.push(format!("{at}: override for unknown task {id:?}"));
            continue;
        }
        if o.len() < 2 {
            errs.push(format!("{at}: override of {id} sets no field"));
        }
        for (k, x) in o {
            if k == "id" {
                continue;
            }
            if FIXED.contains(&k.as_str()) {
                errs.push(format!("{at}: {id}.{k} cannot be overridden"));
                continue;
            }
            let Some(node) = props.get(k) else {
                errs.push(format!("{at}: {id}: {k} is not a task record field"));
                continue;
            };
            let mut e = Vec::new();
            schema.conforms(
                &override_value(k, x, idx),
                node,
                &format!("{id}.{k}"),
                &mut e,
            );
            for x in e {
                errs.push(format!("{at}: {x}"));
            }
            if k == "depends_on" {
                for d in x.as_array().cloned().unwrap_or_default() {
                    let d = d.as_str().unwrap_or("");
                    if idx.find(d).is_none() {
                        errs.push(format!("{at}: {id}: depends_on unknown {d}"));
                    }
                }
            }
        }
    }
    errs
}

/// tasks.jsonl as every reader sees it: overrides applied in block order, the last line winning per (id, field).
pub struct Effective {
    pub records: Vec<Value>,
    pub overridden: HashMap<String, BTreeSet<String>>,
}

pub fn apply(records: &[Value], lines: &[OverrideLine], idx: &Index) -> Effective {
    let mut recs = records.to_vec();
    let mut overridden: HashMap<String, BTreeSet<String>> = HashMap::new();
    for l in lines {
        let Some(o) = l.value.as_ref().and_then(|v| v.as_object()) else {
            continue;
        };
        let key = o.get("id").and_then(|x| x.as_str()).unwrap_or("");
        let Some(i) = idx.find(key).filter(|i| *i < recs.len()) else {
            continue;
        };
        for (k, x) in o {
            if FIXED.contains(&k.as_str()) {
                continue;
            }
            overridden
                .entry(idx.ids[i].clone())
                .or_default()
                .insert(k.clone());
            if let Some(m) = recs[i].as_object_mut() {
                m.insert(k.clone(), override_value(k, x, idx));
            }
        }
    }
    Effective {
        records: recs,
        overridden,
    }
}

// ---------------------------------------------------------------- render

/// A group heading that sorts named groups before the unnamed one.
type SortKey = (u8, String);

/// The block a first render writes: one comment saying what a line looks like, no override. `example` is how
/// the first task is named (its typed id once the list is migrated).
pub fn empty_block(example: &str) -> String {
    format!(
        "<!-- One JSON object per line: {{\"id\": \"{example}\", \"status\": \"incomplete\", \"owner\": \"operator\"}}. \
Its fields are applied on top of the record whenever a task tool reads the list. -->\n"
    )
}

fn s<'a>(r: &'a Value, k: &str) -> &'a str {
    r.get(k).and_then(|x| x.as_str()).unwrap_or("")
}

fn one_line(t: &str) -> String {
    t.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// TASKS.md for `eff`, with `inner` (the bytes between the override markers) copied verbatim.
pub fn render(store: &str, eff: &Effective, inner: &str, idx: &Index) -> String {
    let mut o = String::new();
    o.push_str(BANNER);
    o.push_str("\n\n");
    o.push_str(&format!(
        "<!-- GENERATED by `mios-task render` from {store}. Do not edit this file outside the overrides block: \
change tasks in {store} (mios-task add/set/claim, or the dev-loop task tools), or add an override line below, \
then run `mios-task render`. `mios-task check` fails on any other difference. -->\n\n"
    ));
    o.push_str(&format!(
        "`{store}` at the repo root is the only canonical MiOS task list (ADR-0028). This file is its generated \
documentation. The two edit surfaces are `{store}` and the operator-overrides block below; every task reader \
(mios-task check, render, ready/next, claim, set, and the dev-loop task tools) applies the overrides on top of \
`{store}`.\n\n"
    ));
    o.push_str("## Operator overrides\n\n");
    o.push_str(&format!(
        "Each line between the markers that starts with `{{` is one override: a JSON object with an `\"id\"` and the \
record fields to apply (`status` in either dialect). The last line wins per (id, field); an unknown id, an unknown \
field or a value the schema refuses fails `mios-task check`. `mios-task overrides fold <id>` moves an override \
into `{store}`. Fields shown with `[override]` below come from this block.\n\n"
    ));
    o.push_str(BEGIN);
    o.push('\n');
    o.push_str(inner);
    o.push_str(END);
    o.push_str("\n\n");

    let mut counts: BTreeMap<&str, usize> = BTreeMap::new();
    for r in &eff.records {
        *counts.entry(s(r, "status")).or_default() += 1;
    }
    o.push_str("## Summary\n\n| Status | Records |\n|---|---|\n");
    for st in STATUSES {
        o.push_str(&format!(
            "| {st} | {} |\n",
            counts.get(st).copied().unwrap_or(0)
        ));
    }
    o.push_str(&format!("| total | {} |\n", eff.records.len()));
    o.push_str(&format!(
        "\n{} record(s) carry at least one override.\n",
        eff.overridden.len()
    ));

    // Workstream, then epic, then file order.
    let mut groups: BTreeMap<SortKey, BTreeMap<SortKey, Vec<&Value>>> = BTreeMap::new();
    for r in &eff.records {
        let ws = r.get("workstream").and_then(|x| x.as_str()).unwrap_or("");
        let wk = if ws.is_empty() {
            (1, "(no workstream)".to_string())
        } else {
            (0, one_line(ws))
        };
        let ep = s(r, "epic");
        let ek = if ep.is_empty() {
            (0, String::new())
        } else {
            (1, idx.label(ep))
        };
        groups.entry(wk).or_default().entry(ek).or_default().push(r);
    }
    for ((_, ws), epics) in &groups {
        o.push_str(&format!("\n## {ws}\n"));
        for ((_, ep), recs) in epics {
            if ep.is_empty() {
                o.push_str("\n### No epic\n\n");
            } else {
                o.push_str(&format!("\n### Epic {ep}\n\n"));
            }
            for r in recs {
                o.push_str(&line(r, eff, idx));
                o.push('\n');
            }
        }
    }
    o
}

fn line(r: &Value, eff: &Effective, idx: &Index) -> String {
    let id = s(r, "id");
    let ov = eff.overridden.get(id);
    let tag = |f: &str| {
        if ov.is_some_and(|x| x.contains(f)) {
            " [override]"
        } else {
            ""
        }
    };
    let mut parts = vec![format!("{}{}", s(r, "status"), tag("status"))];
    let owner = s(r, "owner");
    if !owner.is_empty() || !tag("owner").is_empty() {
        let shown = if owner.is_empty() {
            "(unclaimed)"
        } else {
            owner
        };
        parts.push(format!("owner {}{}", shown, tag("owner")));
    }
    if let Some(p) = r.get("priority").and_then(|x| x.as_str()) {
        parts.push(format!("{p}{}", tag("priority")));
    }
    if let Some(z) = r.get("size").and_then(|x| x.as_str()) {
        parts.push(format!("size {z}{}", tag("size")));
    }
    let deps: Vec<String> = r
        .get("depends_on")
        .and_then(|d| d.as_array())
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_str())
                .map(|d| idx.label(d))
                .collect()
        })
        .unwrap_or_default();
    if !deps.is_empty() || !tag("depends_on").is_empty() {
        parts.push(format!(
            "depends_on {}{}",
            deps.join(", "),
            tag("depends_on")
        ));
    }
    let shown_elsewhere = ["id", "status", "owner", "priority", "size", "depends_on"];
    if let Some(fields) = ov {
        let rest: Vec<&str> = fields
            .iter()
            .map(String::as_str)
            .filter(|f| !shown_elsewhere.contains(f))
            .collect();
        if !rest.is_empty() {
            parts.push(format!("{} [override]", rest.join(", ")));
        }
    }
    format!(
        "- `{}` {} -- {}",
        idx.label(id),
        one_line(s(r, "title")),
        parts.join(" · ")
    )
}
