// AI-hint: Audits every effective keyboard binding for collisions against the layered SSOT: [keybindings.global] (tiers, contexts, bypass, passthrough, reserved terminal chords), the Hyprland binding tables, and the literal hl.bind() lines of the generated session. One chord normalizer; two-sided unit controls.
// AI-related: usr/share/mios/mios.toml ([keybindings.global]), usr/share/mios/hyprland/hyprland.lua, usr/share/doc/mios/keybinds.md, tests/test-keybind-collisions.py, /var/tmp/mios-keybinds/schema.md

use crate::Report;
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use toml::Value;

const CHECK: &str = "keybind-collisions";

/// Every context a binding can be active in (the schema's closed set).
const CONTEXTS: &[&str] = &[
    "desktop-2d",
    "desktop-3d",
    "terminal",
    "vm-passthrough",
    "game",
];

fn cannot_run(why: impl Into<String>) -> Report {
    Report {
        check: CHECK.to_string(),
        ok: false,
        could_not_run: Some(why.into()),
        summary: String::new(),
        findings: Vec::new(),
    }
}

/// The neutral SSOT resolver owns normalization for both the gate and daemon.
fn normalize_step(raw: &str) -> Result<String, String> {
    mios_resolver::keybindings::normalize_chord(raw, "\0")
}
pub fn normalize_chord(raw: &str, multi: bool) -> Result<Vec<String>, String> {
    let canonical =
        mios_resolver::keybindings::normalize_chord(raw, if multi { " " } else { "\0" })?;
    Ok(if multi {
        canonical.split(' ').map(str::to_owned).collect()
    } else {
        vec![canonical]
    })
}

#[derive(Debug, Clone)]
struct Bound {
    source: String,
    id: String,
    steps: Vec<String>,
    contexts: BTreeSet<String>,
    legacy: bool,
    realized: bool,
}

fn all_contexts() -> BTreeSet<String> {
    CONTEXTS.iter().map(|c| c.to_string()).collect()
}

fn str_list(v: Option<&Value>) -> Vec<String> {
    v.and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(|x| x.as_str().map(str::to_string))
                .collect()
        })
        .unwrap_or_default()
}

/// Collect every array-of-tables named `bindings` below [desktop.hyprland] that carries `key` strings.
fn legacy_tables<'a>(v: &'a Value, path: String, out: &mut Vec<(String, &'a Vec<Value>)>) {
    if let Some(t) = v.as_table() {
        for (k, child) in t {
            let p = if path.is_empty() {
                k.clone()
            } else {
                format!("{path}.{k}")
            };
            if k == "bindings" {
                if let Some(arr) = child.as_array() {
                    if arr
                        .iter()
                        .any(|e| e.get("key").and_then(Value::as_str).is_some())
                    {
                        out.push((p.clone(), arr));
                    }
                }
            }
            legacy_tables(child, p, out);
        }
    }
}

/// Evaluate the literal part of a Lua string expression: "a" .. " + " .. "b". None when it is dynamic.
fn lua_literal(expr: &str) -> Option<String> {
    let mut out = String::new();
    for part in expr.split("..") {
        let p = part.trim();
        let inner = p.strip_prefix('"')?.strip_suffix('"')?;
        out.push_str(inner);
    }
    Some(out)
}

/// The literal hl.bind("<chord>", ...) lines of the generated session; dynamic ones are counted, not guessed.
fn lua_binds(lua: &str) -> (Vec<String>, usize) {
    let mut chords = Vec::new();
    let mut dynamic = 0;
    for line in lua.lines() {
        let t = line.trim_start();
        let Some(rest) = t.strip_prefix("hl.bind(") else {
            continue;
        };
        // the chord expression ends at the first top-level comma that follows the closing quote
        let mut depth_quote = false;
        let mut end = None;
        for (i, c) in rest.char_indices() {
            if c == '"' {
                depth_quote = !depth_quote;
            }
            if c == ',' && !depth_quote {
                end = Some(i);
                break;
            }
        }
        let Some(end) = end else { continue };
        match lua_literal(&rest[..end]) {
            Some(c) => chords.push(c),
            None => dynamic += 1,
        }
    }
    (chords, dynamic)
}

pub struct Analysis {
    pub findings: Vec<String>,
    pub bindings: usize,
    pub legacy: usize,
    pub lua_literal: usize,
    pub lua_dynamic: usize,
}

fn steps_prefix(a: &[String], b: &[String]) -> bool {
    a.len() < b.len() && b[..a.len()] == *a
}

pub fn analyze(ssot: &Value, lua: Option<&str>) -> Result<Analysis, String> {
    let global = ssot
        .get("keybindings")
        .and_then(|k| k.get("global"))
        .and_then(Value::as_table)
        .ok_or("mios.toml declares no [keybindings.global] table")?;
    let bypass_mod = global
        .get("bypass_mod")
        .and_then(Value::as_str)
        .ok_or("[keybindings.global].bypass_mod is missing")?;
    let bypass_mod_norm =
        normalize_step(&format!("{bypass_mod}+x")).map_err(|e| format!("bypass_mod: {e}"))?;
    let bypass_prefix = bypass_mod_norm.trim_end_matches("+x").to_string();
    let namespaces: BTreeSet<String> = str_list(global.get("action_namespaces"))
        .into_iter()
        .collect();
    if namespaces.is_empty() {
        return Err("[keybindings.global].action_namespaces is empty".into());
    }
    let planned: BTreeSet<String> = str_list(global.get("planned_actions")).into_iter().collect();
    let implemented: BTreeSet<String> = global
        .get("action_argv")
        .and_then(Value::as_table)
        .map(|t| t.keys().cloned().collect())
        .unwrap_or_default();
    let mut findings_pre: Vec<String> = Vec::new();
    for (a, v) in global.get("action_argv").and_then(Value::as_table).into_iter().flatten() {
        let ok = v.as_array().is_some_and(|x| !x.is_empty() && x.iter().all(|e| e.as_str().is_some_and(|s| !s.is_empty())));
        if !ok {
            findings_pre.push(format!("action_argv[{a}] must be a non-empty array of non-empty strings"));
        }
        if planned.contains(a) {
            findings_pre.push(format!("action {a} is both implemented (action_argv) and planned"));
        }
    }
    let reserved: Vec<(String, Vec<String>)> = str_list(global.get("reserved_terminal"))
        .into_iter()
        .map(|c| normalize_chord(&c, true).map(|s| (c, s)))
        .collect::<Result<_, _>>()
        .map_err(|e| format!("reserved_terminal: {e}"))?;

    let mut findings = findings_pre;
    let mut bound: Vec<Bound> = Vec::new();
    let mut ids = BTreeSet::new();
    let mut realized: Vec<(String, Vec<String>)> = Vec::new();

    let entries = global
        .get("bindings")
        .and_then(Value::as_array)
        .cloned()
        .unwrap_or_default();
    for (i, e) in entries.iter().enumerate() {
        let id = e
            .get("id")
            .and_then(Value::as_str)
            .unwrap_or("")
            .to_string();
        let tag = if id.is_empty() {
            format!("keybindings.global.bindings[{i}]")
        } else {
            id.clone()
        };
        if id.is_empty()
            || !id
                .bytes()
                .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
        {
            findings.push(format!("{tag}: id must be non-empty [a-z0-9-]"));
        }
        if !ids.insert(id.clone()) && !id.is_empty() {
            findings.push(format!("{tag}: duplicate binding id"));
        }
        match e.get("tier").and_then(Value::as_integer) {
            Some(1..=4) => {}
            _ => findings.push(format!("{tag}: tier must be an integer 1..=4")),
        }
        let action = e.get("action").and_then(Value::as_str).unwrap_or("");
        match action.split_once(':') {
            Some((ns, verb)) if !verb.is_empty() && namespaces.contains(ns) => {
                // rule 7: quickshell:/mios: actions are dispatched by a handler we ship; they must be implemented
                // (action_argv) or explicitly planned, so an unfinished action is visible instead of silently inert
                if (ns == "quickshell" || ns == "mios") && !implemented.contains(action) && !planned.contains(action) {
                    findings.push(format!("{tag}: action {action:?} is neither in [keybindings.global.action_argv] nor planned_actions"));
                }
            }
            _ => findings.push(format!("{tag}: action {action:?} must be <namespace>:<verb> with a namespace in action_namespaces")),
        }
        let contexts: BTreeSet<String> = match e.get("contexts") {
            Some(v) => str_list(Some(v)).into_iter().collect(),
            None => all_contexts(),
        };
        for c in &contexts {
            if !CONTEXTS.contains(&c.as_str()) {
                findings.push(format!("{tag}: unknown context {c:?}"));
            }
        }
        let passthrough: BTreeSet<String> = str_list(e.get("passthrough")).into_iter().collect();
        let effective: BTreeSet<String> = contexts.difference(&passthrough).cloned().collect();
        let scope = e.get("scope").and_then(Value::as_str).unwrap_or("host");
        let chord_raw = e.get("chord").and_then(Value::as_str).unwrap_or("");
        let steps = match normalize_chord(chord_raw, true) {
            Ok(s) => s,
            Err(err) => {
                findings.push(format!("{tag}: chord {chord_raw:?}: {err}"));
                continue;
            }
        };
        // rule 2: in vm-passthrough only bypass_mod chords may be host-bound
        if scope == "host"
            && effective.contains("vm-passthrough")
            && !steps[0].starts_with(&bypass_prefix)
        {
            findings.push(format!(
                "{tag}: chord {} is host-bound in vm-passthrough but does not use bypass_mod {bypass_prefix}; list vm-passthrough in passthrough or use the bypass chord",
                steps.join(" ")
            ));
        }
        // rule 3: POSIX/Readline reserved chords stay with the focused terminal
        if scope == "host" && effective.contains("terminal") {
            for (raw, r) in &reserved {
                if r == &steps || steps_prefix(r, &steps) || steps_prefix(&steps, r) {
                    findings.push(format!("{tag}: chord {} shadows reserved terminal chord {raw:?} in the terminal context", steps.join(" ")));
                }
            }
        }
        if let Some(by) = e.get("bypass").and_then(Value::as_str) {
            match normalize_chord(by, true) {
                Ok(bs) => {
                    if !bs[0].starts_with(&bypass_prefix) {
                        findings.push(format!(
                            "{tag}: bypass {by:?} must use bypass_mod {bypass_prefix}"
                        ));
                    }
                    bound.push(Bound {
                        source: "keybindings.global".into(),
                        id: format!("{id}#bypass"),
                        steps: bs,
                        contexts: all_contexts(),
                        legacy: false,
                        realized: false,
                    });
                }
                Err(err) => findings.push(format!("{tag}: bypass {by:?}: {err}")),
            }
        }
        if scope != "app" {
            let realized_by = e.get("realized_by").and_then(Value::as_str).unwrap_or("");
            if !realized_by.is_empty() && realized_by != "hyprland" {
                findings.push(format!(
                    "{tag}: realized_by {realized_by:?} is not a known realizer (hyprland)"
                ));
            }
            if !realized_by.is_empty() {
                realized.push((tag.clone(), steps.clone()));
            }
            bound.push(Bound {
                source: "keybindings.global".into(),
                id: id.clone(),
                steps,
                contexts: effective,
                legacy: false,
                realized: !realized_by.is_empty(),
            });
        }
    }

    // legacy compositor tables: bound in every context the compositor serves; they are the effective Tier 1 today
    let mut tables = Vec::new();
    if let Some(d) = ssot.get("desktop") {
        legacy_tables(d, "desktop".into(), &mut tables);
    }
    let mut legacy_count = 0;
    for (path, arr) in tables {
        for (i, e) in arr.iter().enumerate() {
            let Some(k) = e.get("key").and_then(Value::as_str) else {
                continue;
            };
            let label = e.get("label").and_then(Value::as_str).unwrap_or("");
            match normalize_chord(k, false) {
                Ok(steps) => {
                    legacy_count += 1;
                    bound.push(Bound {
                        source: path.clone(),
                        id: if label.is_empty() {
                            format!("{path}[{i}]")
                        } else {
                            format!("{path}:{label}")
                        },
                        steps,
                        contexts: all_contexts(),
                        legacy: true,
                        realized: false,
                    });
                }
                Err(err) => findings.push(format!("{path}[{i}]: key {k:?}: {err}")),
            }
        }
    }

    let (lua_chords, lua_dynamic) = lua.map(lua_binds).unwrap_or_default();
    let mut lua_count = 0;
    for c in &lua_chords {
        match normalize_chord(c, false) {
            Ok(steps) => {
                lua_count += 1;
                bound.push(Bound {
                    source: "hyprland.lua".into(),
                    id: format!("hl.bind({c})"),
                    steps,
                    contexts: all_contexts(),
                    legacy: true,
                    realized: false,
                });
            }
            Err(err) => findings.push(format!("hyprland.lua: hl.bind({c:?}): {err}")),
        }
    }

    // a declared binding that claims the compositor realizes it must exist in the compositor tables
    for (tag, steps) in &realized {
        let found = bound
            .iter()
            .any(|b| b.source == "hyprland.lua" && &b.steps == steps);
        if !found {
            findings.push(format!(
                "{tag}: realized_by hyprland but no compositor binding for {} exists",
                steps.join(" ")
            ));
        }
    }

    // rules 5/6/8: same chord (or a strict multi-step prefix) in intersecting contexts. The same physical
    // bind appears in the SSOT table AND in the generated Lua; those two are one binding, not a collision.
    let mut seen: BTreeMap<(String, String), ()> = BTreeMap::new();
    for i in 0..bound.len() {
        for j in (i + 1)..bound.len() {
            let (a, b) = (&bound[i], &bound[j]);
            let same_physical = a.steps == b.steps
                && ((a.legacy
                    && b.legacy
                    && ((a.source == "hyprland.lua") != (b.source == "hyprland.lua")))
                    || (a.realized && b.legacy)
                    || (b.realized && a.legacy));
            if same_physical {
                continue;
            }
            let shared: Vec<&String> = a.contexts.intersection(&b.contexts).collect();
            if shared.is_empty() {
                continue;
            }
            let same = a.steps == b.steps;
            let prefix = steps_prefix(&a.steps, &b.steps) || steps_prefix(&b.steps, &a.steps);
            if !(same || prefix) {
                continue;
            }
            let key = (a.id.clone(), b.id.clone());
            if seen.insert(key, ()).is_some() {
                continue;
            }
            findings.push(format!(
                "{} chord {} / {}: {} [{}] vs {} [{}] in {}",
                if same {
                    "collision"
                } else {
                    "prefix-collision"
                },
                a.steps.join(" "),
                b.steps.join(" "),
                a.id,
                a.source,
                b.id,
                b.source,
                shared
                    .iter()
                    .map(|s| s.as_str())
                    .collect::<Vec<_>>()
                    .join(",")
            ));
        }
    }

    Ok(Analysis {
        findings,
        bindings: entries.len(),
        legacy: legacy_count,
        lua_literal: lua_count,
        lua_dynamic,
    })
}

pub fn check(root: &Path) -> Report {
    let ssot_path = root.join("usr/share/mios/mios.toml");
    if !ssot_path.is_file() {
        return cannot_run(format!("{} is missing", ssot_path.display()));
    }
    let resolved = match mios_resolver::resolve_vendor_projection(root) {
        Ok(v) => v,
        Err(e) => return cannot_run(format!("the vendor SSOT did not resolve: {e}")),
    };
    let lua = match std::fs::read_to_string(root.join("usr/share/mios/hyprland/hyprland.lua")) {
        Ok(v) => Some(v),
        Err(e) => {
            return cannot_run(format!(
                "required compositor projection missing/unreadable: {e}"
            ))
        }
    };
    match analyze(&resolved, lua.as_deref()) {
        Err(why) => cannot_run(why),
        Ok(a) => Report {
            check: CHECK.to_string(),
            ok: a.findings.is_empty(),
            could_not_run: None,
            summary: format!(
                "{} declared binding(s), {} compositor table binding(s), {} literal hl.bind line(s) ({} dynamic, not audited): {} finding(s)",
                a.bindings,
                a.legacy,
                a.lua_literal,
                a.lua_dynamic,
                a.findings.len()
            ),
            findings: a.findings,
        },
    }
}

#[cfg(test)]
#[allow(clippy::unwrap_used, clippy::expect_used)]
mod tests {
    use super::*;

    const BASE: &str = r#"
[keybindings.global]
bypass_mod = "SUPER+CTRL"
planned_actions = ["quickshell:launcher", "mios:toggle_vm_passthrough"]
action_namespaces = ["hypr", "exec", "quickshell", "mios", "tmux", "readline"]
reserved_terminal = ["CTRL+C", "CTRL+Z", "CTRL+D", "CTRL+A", "CTRL+E"]

[[keybindings.global.bindings]]
id = "app-drawer"
tier = 1
chord = "SUPER+Space"
bypass = "SUPER+CTRL+Space"
action = "quickshell:launcher"
passthrough = ["vm-passthrough"]

[[keybindings.global.bindings]]
id = "vm-toggle"
tier = 2
chord = "SUPER+ALT+p"
action = "mios:toggle_vm_passthrough"
passthrough = ["vm-passthrough"]
"#;

    fn run(extra: &str) -> Analysis {
        let v: Value = format!("{BASE}{extra}").parse().unwrap();
        analyze(&v, None).unwrap()
    }

    #[test]
    fn clean_ssot_has_no_findings() {
        assert!(run("").findings.is_empty(), "{:?}", run("").findings);
    }

    #[test]
    fn normalizer_orders_modifiers_and_accepts_research_notation() {
        assert_eq!(
            normalize_chord("shift + super + C", false).unwrap(),
            vec!["SUPER+SHIFT+c"]
        );
        assert_eq!(
            normalize_chord("SUPER_CTRL+Space", false).unwrap(),
            normalize_chord("CTRL+SUPER+space", false).unwrap()
        );
        assert_eq!(
            normalize_chord("CTRL+X CTRL+B", true).unwrap(),
            vec!["CTRL+x", "CTRL+b"]
        );
        assert!(normalize_chord("", true).is_err());
        assert!(normalize_chord("SUPER+a+b", false).is_err());
    }

    // negative controls: each deliberately bad input must produce a finding
    #[test]
    fn duplicate_chord_in_same_context_is_a_collision() {
        let a = run("\n[[keybindings.global.bindings]]\nid = \"dup\"\ntier = 1\nchord = \"super+space\"\naction = \"exec:x\"\n");
        assert!(
            a.findings.iter().any(|f| f.starts_with("collision")),
            "{:?}",
            a.findings
        );
    }

    #[test]
    fn same_chord_in_disjoint_contexts_is_fine() {
        let a = run("\n[[keybindings.global.bindings]]\nid = \"other\"\ntier = 1\nchord = \"SUPER+Space\"\naction = \"exec:x\"\ncontexts = [\"terminal\"]\npassthrough = [\"terminal\"]\n");
        assert!(
            a.findings.iter().all(|f| !f.starts_with("collision")),
            "{:?}",
            a.findings
        );
    }

    #[test]
    fn bypass_not_using_bypass_mod_is_rejected() {
        let a = run("\n[[keybindings.global.bindings]]\nid = \"bad-bypass\"\ntier = 1\nchord = \"SUPER+b\"\nbypass = \"SUPER+SHIFT+b\"\naction = \"exec:x\"\npassthrough = [\"vm-passthrough\"]\n");
        assert!(
            a.findings.iter().any(|f| f.contains("must use bypass_mod")),
            "{:?}",
            a.findings
        );
    }

    #[test]
    fn host_bound_in_vm_passthrough_without_bypass_mod_is_rejected() {
        let a = run("\n[[keybindings.global.bindings]]\nid = \"leaky\"\ntier = 1\nchord = \"SUPER+n\"\naction = \"exec:x\"\n");
        assert!(
            a.findings
                .iter()
                .any(|f| f.contains("host-bound in vm-passthrough")),
            "{:?}",
            a.findings
        );
    }

    #[test]
    fn reserved_terminal_chord_is_protected() {
        let a = run("\n[[keybindings.global.bindings]]\nid = \"steals-ctrl-c\"\ntier = 3\nchord = \"CTRL+c\"\naction = \"exec:x\"\ncontexts = [\"terminal\"]\n");
        assert!(
            a.findings
                .iter()
                .any(|f| f.contains("reserved terminal chord")),
            "{:?}",
            a.findings
        );
    }

    #[test]
    fn multi_step_prefix_collision_is_found() {
        let a = run("\n[[keybindings.global.bindings]]\nid = \"p1\"\ntier = 3\nchord = \"CTRL+x\"\naction = \"exec:x\"\ncontexts = [\"game\"]\n[[keybindings.global.bindings]]\nid = \"p2\"\ntier = 3\nchord = \"CTRL+x CTRL+b\"\naction = \"exec:y\"\ncontexts = [\"game\"]\n");
        assert!(
            a.findings.iter().any(|f| f.starts_with("prefix-collision")),
            "{:?}",
            a.findings
        );
    }

    #[test]
    fn unknown_namespace_duplicate_id_and_bad_tier_are_rejected() {
        let a = run("\n[[keybindings.global.bindings]]\nid = \"app-drawer\"\ntier = 9\nchord = \"SUPER+F9\"\naction = \"nope:x\"\ncontexts = [\"game\"]\n");
        assert!(a
            .findings
            .iter()
            .any(|f| f.contains("duplicate binding id")));
        assert!(a.findings.iter().any(|f| f.contains("tier must be")));
        assert!(a.findings.iter().any(|f| f.contains("namespace")));
    }

    #[test]
    fn legacy_table_and_lua_same_bind_is_one_binding_but_two_tables_collide() {
        let toml = format!("{BASE}\n[desktop.hyprland]\nbindings = [ {{ key = \"SUPER + C\", label = \"close\" }}, {{ key = \"super+c\", label = \"copy\" }} ]\n");
        let v: Value = toml.parse().unwrap();
        let lua = "hl.bind(\"SUPER + C\", hl.dsp.window.close())\nhl.bind(\"SUPER\" .. \" + \" .. key, x)\n";
        let a = analyze(&v, Some(lua)).unwrap();
        assert_eq!(a.lua_literal, 1);
        assert_eq!(a.lua_dynamic, 1);
        // close vs copy collide; the Lua line duplicates `close`/`copy` physically and is not reported again
        assert_eq!(
            a.findings
                .iter()
                .filter(|f| f.starts_with("collision"))
                .count(),
            1,
            "{:?}",
            a.findings
        );
    }

    #[test]
    fn realized_by_matches_a_compositor_bind_and_drift_is_found() {
        let toml = format!("{BASE}\n[[keybindings.global.bindings]]\nid = \"close\"\ntier = 1\nchord = \"SUPER+c\"\naction = \"hypr:killactive\"\nrealized_by = \"hyprland\"\npassthrough = [\"vm-passthrough\"]\n[desktop.hyprland]\nbindings = [ {{ key = \"SUPER + C\", label = \"close\" }} ]\n");
        let v: Value = toml.parse().unwrap();
        let a = analyze(
            &v,
            Some("hl.bind(\"SUPER\" .. \" + \" .. \"C\", hl.dsp.window.close())"),
        )
        .unwrap();
        assert!(a.findings.is_empty(), "{:?}", a.findings);
        let missing_projection = analyze(&v, Some("-- binding removed from projection")).unwrap();
        assert!(missing_projection
            .findings
            .iter()
            .any(|f| f.contains("realized_by hyprland but no compositor binding")));
        // negative control: the compositor bind is gone -> drift
        let toml2 = format!("{BASE}\n[[keybindings.global.bindings]]\nid = \"close\"\ntier = 1\nchord = \"SUPER+c\"\naction = \"hypr:killactive\"\nrealized_by = \"hyprland\"\npassthrough = [\"vm-passthrough\"]\n");
        let v2: Value = toml2.parse().unwrap();
        let a2 = analyze(&v2, None).unwrap();
        assert!(
            a2.findings
                .iter()
                .any(|f| f.contains("realized_by hyprland but no compositor binding")),
            "{:?}",
            a2.findings
        );
    }

    /// The shared normalizer vectors (usr/share/mios/keybinds/chord-vectors.json): mios-inputd's
    /// normalize_chord must pass the same file, so the gate and the daemon cannot drift apart.
    #[test]
    fn shared_chord_vectors_hold() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../../usr/share/mios/keybinds/chord-vectors.json");
        let text = std::fs::read_to_string(&path)
            .expect("shared chord vectors are required, including detached build snapshots");
        let v: serde_json::Value = serde_json::from_str(&text).unwrap();
        let cases = v["cases"].as_array().unwrap();
        assert!(!cases.is_empty());
        for c in cases {
            let input = c["input"].as_str().unwrap();
            let multi = c["multi"].as_bool().unwrap_or(false);
            match c["expect"].as_array() {
                Some(exp) => {
                    let want: Vec<String> = exp
                        .iter()
                        .map(|x| x.as_str().unwrap().to_string())
                        .collect();
                    assert_eq!(
                        normalize_chord(input, multi).unwrap(),
                        want,
                        "input {input:?}"
                    );
                }
                None => assert!(
                    normalize_chord(input, multi).is_err(),
                    "input {input:?} must be rejected"
                ),
            }
        }
    }

    #[test]
    fn unimplemented_unplanned_quickshell_or_mios_action_is_rejected() {
        // negative control: a mios: action in neither action_argv nor planned_actions
        let a = run("\n[[keybindings.global.bindings]]\nid = \"ghost-action\"\ntier = 1\nchord = \"SUPER+F6\"\naction = \"mios:no-such-verb\"\ncontexts = [\"game\"]\n");
        assert!(a.findings.iter().any(|f| f.contains("neither in [keybindings.global.action_argv] nor planned_actions")), "{:?}", a.findings);
        // positive control: the same action listed as planned is accepted
        let toml = format!("{BASE}\n[[keybindings.global.bindings]]\nid = \"ghost-action\"\ntier = 1\nchord = \"SUPER+F6\"\naction = \"mios:no-such-verb\"\ncontexts = [\"game\"]\n").replace("planned_actions = [\"quickshell:launcher\"", "planned_actions = [\"mios:no-such-verb\", \"quickshell:launcher\"");
        let v: Value = toml.parse().unwrap();
        assert!(analyze(&v, None).unwrap().findings.is_empty());
    }

    #[test]
    fn missing_global_table_cannot_run() {
        let v: Value = "[x]\ny = 1\n".parse().unwrap();
        assert!(analyze(&v, None).is_err());
    }
}
