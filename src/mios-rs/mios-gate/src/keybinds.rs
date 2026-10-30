// AI-hint: Audits every effective keyboard binding for collisions against the layered SSOT: [keybindings.global] (tiers, contexts, bypass, passthrough, reserved terminal chords), the Hyprland binding tables, and the literal hl.bind() lines of the generated session; plus the controller map (nav profiles per MiOS-owned context, console-capable namespaces, contract buttons). One chord normalizer; two-sided unit controls. Contract: usr/share/doc/mios/keybinds.md.
// AI-related: usr/share/mios/mios.toml ([keybindings.global]), usr/share/mios/hyprland/hyprland.lua, usr/share/doc/mios/keybinds.md, docs/design/mios-global-bindings-final.md, tests/test-keybind-collisions.py

use crate::Report;
use std::collections::{BTreeMap, BTreeSet};
use std::path::Path;
use toml::Value;

const CHECK: &str = "keybind-collisions";

/// Every context a binding can be active in (the schema's closed set). `console` is the Linux VT: no compositor
/// runs there, so it is never part of the default set (a row without `contexts` and every compositor bind are
/// active everywhere except the console).
const CONTEXTS: &[&str] = &[
    "desktop-2d",
    "desktop-3d",
    "terminal",
    "vm-passthrough",
    "game",
    "app-capture",
    "console",
];
const CONSOLE: &str = "console";
/// Namespaces that act on a VT (no compositor, no Quickshell): key projection, tmux client verbs, the VT switch.
const CONSOLE_NAMESPACES: &[&str] = &["key", "tmux"];
const CONSOLE_ACTIONS: &[&str] = &["mios:graphical-vt"];

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

/// The default context set: every context a compositor serves (all but `console`).
fn all_contexts() -> BTreeSet<String> {
    CONTEXTS.iter().filter(|c| **c != CONSOLE).map(|c| c.to_string()).collect()
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
        // Native hold release is the other edge of its declared press chord.
        // Count the press once; its lifecycle is verified by the native policy test.
        if t.starts_with("local up=mios_native_hl.bind(") { continue; }
        let rest = if let Some(rest)=t.strip_prefix("hl.bind(") {rest}
            else if let Some((_,rest))=t.split_once("mios_native_hl.bind(") {rest}
            else {continue};
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

/// A tmux key name (`C-a`, `M-x`, `S-Enter`) as a global-contract chord (`CTRL+a`, `ALT+x`, `SHIFT+Return`).
fn tmux_key_chord(key: &str) -> String {
    let mut mods = Vec::new();
    let mut rest = key;
    loop {
        let m = match rest.get(..2) {
            Some("C-") => "CTRL",
            Some("M-") => "ALT",
            Some("S-") => "SHIFT",
            _ => break,
        };
        mods.push(m);
        rest = &rest[2..];
    }
    mods.push(rest);
    mods.join("+")
}

/// rule 8 (tier 3, MiOS-Tmux): every root-table key tmux takes from the focused terminal (the agent accept
/// chord; the right-click menus are mouse events) must not be a reserved terminal chord. The tmux prefix is the
/// tier-3 owner of its chord and is exempt; prefix-table keys are second steps after it and cannot shadow one.
fn tmux_root_findings(ssot: &Value, reserved: &[(String, Vec<String>)]) -> Vec<String> {
    let mut out = Vec::new();
    let Some(kb) = ssot.get("keybindings") else { return out };
    let prefix = kb.get("tmux_prefix").and_then(Value::as_str).unwrap_or("");
    let roots = kb.get("agent").and_then(|a| a.get("accept_chord")).and_then(Value::as_str).into_iter();
    for key in roots.filter(|k| *k != prefix) {
        match normalize_chord(&tmux_key_chord(key), false) {
            Ok(steps) => {
                for (raw, r) in reserved {
                    if *r == steps {
                        out.push(format!("keybindings.agent.accept_chord: tmux root key {key} shadows reserved terminal chord {raw:?} in every terminal"));
                    }
                }
            }
            Err(e) => out.push(format!("keybindings.agent.accept_chord: tmux key {key:?} does not normalise: {e}")),
        }
    }
    out
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
    let native = global.get("native_actions").and_then(Value::as_table);
    let implemented: BTreeSet<String> = global
        .get("action_argv")
        .and_then(Value::as_table)
        .map(|t| t.keys().cloned().collect())
        .unwrap_or_default();
    let mut findings_pre: Vec<String> = Vec::new();
    for (action,backend) in native.into_iter().flatten() {
        if action!="mios:vm-command-modifier" || backend.as_str()!=Some("hyprland") || planned.contains(action) || implemented.contains(action) {
            findings_pre.push(format!("unsupported or conflicting native action realizer: {action}"));
        }
    }
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
    findings.extend(tmux_root_findings(ssot, &reserved));
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
                if (ns == "quickshell" || ns == "mios") && !implemented.contains(action) && !planned.contains(action) && !native.is_some_and(|n|n.contains_key(action)) {
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
        if contexts.contains(CONSOLE) && !console_capable(action) {
            findings.push(format!("{tag}: action {action:?} cannot run on the console (no compositor); use key:, tmux: or mios:graphical-vt"));
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

    findings.extend(controller_audit(ssot, &namespaces, &implemented, &planned, native));

    Ok(Analysis {
        findings,
        bindings: entries.len(),
        legacy: legacy_count,
        lua_literal: lua_count,
        lua_dynamic,
    })
}

fn console_capable(action: &str) -> bool {
    action == "none"
        || CONSOLE_ACTIONS.contains(&action)
        || action.split_once(':').is_some_and(|(ns, _)| CONSOLE_NAMESPACES.contains(&ns))
}

/// Pseudo-inputs of the controller grammar besides the evdev buttons: stick directions and the toggle chord.
const PSEUDO_INPUTS: &[&str] = &[
    "LS_UP", "LS_DOWN", "LS_LEFT", "LS_RIGHT", "RS_UP", "RS_DOWN", "RS_LEFT", "RS_RIGHT", "TOGGLE_CHORD",
];
/// Every input a nav profile of an uncaptured compositor context must map (to an action or "none").
const NAV_REQUIRED: &[&str] = &[
    "BTN_SOUTH", "BTN_EAST", "BTN_WEST", "BTN_DPAD_UP", "BTN_DPAD_DOWN", "BTN_DPAD_LEFT", "BTN_DPAD_RIGHT",
    "LS_UP", "LS_DOWN", "LS_LEFT", "LS_RIGHT", "RS_UP", "RS_DOWN", "RS_LEFT", "RS_RIGHT",
    "BTN_TL2", "BTN_TR2", "BTN_SELECT", "BTN_MODE", "BTN_THUMBL", "BTN_THUMBR",
];
/// Owned by the capture contract or the room on a compositor (North capture, bumpers ShaderGlass, Start cursor mode,
/// the toggle chord); a console profile maps them too, because no compositor runs there.
const CONTRACT_INPUTS: &[&str] = &["BTN_NORTH", "BTN_TL", "BTN_TR", "BTN_START", "TOGGLE_CHORD"];
/// The uncaptured contexts MiOS owns outside the room. Captured contexts never get a nav profile: capture and
/// release are the same for every window kind.
const NAV_CONTEXTS: &[&str] = &["desktop-2d", "terminal", "console"];
const NAV_SETTINGS: &[&str] = &["enabled", "flick_on", "flick_off", "contexts", "repeat"];
const TMUX_VERBS: &[&str] = &["focus", "page-up", "page-down", "session-prev", "session-next", "menu", "keymap", "key"];

fn input_known(name: &str) -> bool {
    PSEUDO_INPUTS.contains(&name) || mios_resolver::keybindings::button_code(name).is_ok()
}

/// Resolve a nav profile through `inherits` (once, no cycles).
fn resolve_profile(nav: &toml::Table, name: &str, out: &mut Vec<String>) -> Option<BTreeMap<String, Value>> {
    let mut chain = Vec::new();
    let mut cur = name.to_string();
    loop {
        if chain.contains(&cur) {
            out.push(format!("controller.nav.{name}: inherits cycle {}", chain.join(" -> ")));
            return None;
        }
        let Some(t) = nav.get(&cur).and_then(Value::as_table) else {
            out.push(format!("controller.nav.{name}: profile {cur:?} does not exist"));
            return None;
        };
        chain.push(cur.clone());
        match t.get("inherits") {
            None => break,
            Some(Value::String(p)) => cur = p.clone(),
            Some(_) => {
                out.push(format!("controller.nav.{cur}: inherits must be a profile name"));
                return None;
            }
        }
    }
    let mut merged = BTreeMap::new();
    for p in chain.iter().rev() {
        for (k, v) in nav[p].as_table().into_iter().flatten() {
            if k != "inherits" {
                merged.insert(k.clone(), v.clone()); // arrays replace atomically
            }
        }
    }
    Some(merged)
}

/// INV-10 and C-23: every input of every MiOS-owned context resolves to an action or "none", values follow the
/// grammar, console rows use console-capable namespaces only, and the contract chords do not overlap.
pub fn controller_audit(
    ssot: &Value,
    namespaces: &BTreeSet<String>,
    implemented: &BTreeSet<String>,
    planned: &BTreeSet<String>,
    native: Option<&toml::Table>,
) -> Vec<String> {
    let mut out = Vec::new();
    let ctl = ssot.get("keybindings").and_then(|k| k.get("global")).and_then(|g| g.get("controller"));
    let Some(ctl) = ctl.and_then(Value::as_table) else { return out };
    let prefix_keys: BTreeSet<String> = ["actions"].iter()
        .filter_map(|k| ssot.get("keybindings").and_then(|kb| kb.get(*k)).and_then(Value::as_array))
        .chain(ssot.get("keybindings").and_then(|kb| kb.get("tmux")).and_then(|t| t.get("bindings")).and_then(Value::as_array))
        .flatten()
        .filter_map(|r| r.get("key").and_then(Value::as_str).map(str::to_owned))
        .collect();
    let check_action = |tag: &str, a: &str, console: bool, out: &mut Vec<String>| {
        if a.is_empty() {
            out.push(format!("{tag}: \"\" is not a value; use \"none\" to unbind"));
            return;
        }
        if a == "none" {
            return;
        }
        if a == "app" {
            out.push(format!("{tag}: \"app\" is not a nav value (an uncaptured context is MiOS-owned; capture with North)"));
            return;
        }
        match a.split_once(':') {
            Some((ns, verb)) if !verb.is_empty() && namespaces.contains(ns) => {
                if (ns == "quickshell" || ns == "mios") && !implemented.contains(a) && !planned.contains(a)
                    && !native.is_some_and(|n| n.contains_key(a)) && !CONSOLE_ACTIONS.contains(&a)
                {
                    out.push(format!("{tag}: action {a:?} is neither in [keybindings.global.action_argv] nor planned_actions"));
                }
                if ns == "tmux" {
                    let (v, arg) = verb.split_once(' ').unwrap_or((verb, ""));
                    if !TMUX_VERBS.contains(&v) {
                        out.push(format!("{tag}: unknown tmux verb {v:?}"));
                    } else if v == "key" && !prefix_keys.contains(arg) {
                        out.push(format!("{tag}: tmux:key {arg:?} is not a prefix key bound in [keybindings.tmux]/[keybindings.actions]"));
                    }
                }
            }
            _ => out.push(format!("{tag}: action {a:?} must be <namespace>:<verb> with a namespace in action_namespaces")),
        }
        if console && !console_capable(a) {
            out.push(format!("{tag}: action {a:?} cannot run on the console (no compositor); use key:, tmux: or mios:graphical-vt"));
        }
    };
    let check_value = |tag: &str, v: &Value, console: bool, out: &mut Vec<String>| match v {
        Value::String(a) => check_action(tag, a, console, out),
        Value::Array(a) if a.len() == 2 && a.iter().all(|x| x.is_str()) => {
            for x in a {
                check_action(tag, x.as_str().unwrap_or(""), console, out);
            }
        }
        _ => out.push(format!("{tag}: value must be \"ns:action\", \"none\" or [\"tap\", \"hold\"]")),
    };
    let chord = |v: Option<&Value>| str_list(v).into_iter().collect::<BTreeSet<String>>();
    let toggle = chord(ssot.get("desktop").and_then(|d| d.get("hyprland")).and_then(|h| h.get("hypr3d")).and_then(|h| h.get("controller")).and_then(|c| c.get("toggle_chord")));
    let over = chord(ctl.get("mode_override_chord"));
    let both: Vec<_> = toggle.intersection(&over).cloned().collect();
    if !both.is_empty() {
        out.push(format!("controller: toggle_chord and mode_override_chord share {}", both.join(",")));
    }
    if let Some(room) = ssot.get("desktop").and_then(|d| d.get("hyprland")).and_then(|h| h.get("hypr3d")).and_then(|h| h.get("controller")).and_then(Value::as_table) {
        let mut roles: BTreeMap<String, String> = BTreeMap::new();
        for (k, v) in room {
            let Some(b) = v.as_str().filter(|_| k.ends_with("_button")) else { continue };
            if !input_known(b) {
                out.push(format!("hypr3d.controller.{k}: unknown controller button {b}"));
            }
            if k == "cursor_mode_button" {
                continue; // mirrors [keybindings.global.controller].cursor_mode_button
            }
            if let Some(prev) = roles.insert(b.to_string(), k.clone()) {
                out.push(format!("hypr3d.controller: {prev} and {k} both use {b}"));
            }
            if toggle.contains(b) {
                out.push(format!("hypr3d.controller.{k}: {b} is part of toggle_chord"));
            }
        }
    }
    if let Some(room) = ctl.get("room").and_then(Value::as_table) {
        for (k, v) in room {
            let tag = format!("controller.room.{k}");
            if !input_known(k) {
                out.push(format!("{tag}: unknown controller input {k}"));
            }
            check_value(&tag, v, false, &mut out);
        }
    }
    let Some(nav) = ctl.get("nav").and_then(Value::as_table) else { return out };
    if nav.get("enabled").and_then(Value::as_bool) == Some(false) {
        return out;
    }
    let f = |k: &str| nav.get(k).and_then(|v| v.as_float().or_else(|| v.as_integer().map(|i| i as f64)));
    if let (Some(on), Some(off)) = (f("flick_on"), f("flick_off")) {
        if !(0.0 < off && off < on && on <= 1.0) {
            out.push(format!("controller.nav: need 0 < flick_off ({off}) < flick_on ({on}) <= 1"));
        }
    }
    for (k, v) in nav.get("repeat").and_then(Value::as_table).into_iter().flatten() {
        let ok = v.as_array().is_some_and(|a| a.len() == 2 && a.iter().all(|x| x.as_integer().is_some_and(|i| i > 0)));
        if !input_known(k) || !ok {
            out.push(format!("controller.nav.repeat.{k}: needs a known input and [delay_ms, rate_ms] > 0"));
        }
    }
    for (name, t) in nav {
        if NAV_SETTINGS.contains(&name.as_str()) {
            continue;
        }
        let Some(t) = t.as_table() else {
            out.push(format!("controller.nav.{name}: must be a profile table"));
            continue;
        };
        for (k, v) in t {
            if k == "inherits" {
                continue;
            }
            if !input_known(k) {
                out.push(format!("controller.nav.{name}.{k}: unknown controller input {k}"));
            }
            check_value(&format!("controller.nav.{name}.{k}"), v, false, &mut out);
        }
    }
    let contexts = nav.get("contexts").and_then(Value::as_table).cloned().unwrap_or_default();
    for (ctx, profile) in &contexts {
        let Some(profile) = profile.as_str() else {
            out.push(format!("controller.nav.contexts.{ctx}: must be a profile name or \"vpad\""));
            continue;
        };
        if !NAV_CONTEXTS.contains(&ctx.as_str()) {
            out.push(format!("controller.nav.contexts.{ctx}: only {} take a nav profile (captured contexts belong to the app; the room has its own map)", NAV_CONTEXTS.join(", ")));
            continue;
        }
        if profile == "vpad" {
            if ctx == CONSOLE {
                out.push("controller.nav.contexts.console: the console has no virtual-pad consumer; give it a profile".into());
            }
            continue;
        }
        let names: Vec<String> = if nav.contains_key(profile) {
            vec![profile.to_string()]
        } else if nav.contains_key(&format!("{profile}_tmux")) || nav.contains_key(&format!("{profile}_plain")) {
            vec![format!("{profile}_tmux"), format!("{profile}_plain")]
        } else {
            vec![profile.to_string()]
        };
        let console = ctx == CONSOLE;
        for name in names {
            let Some(map) = resolve_profile(nav, &name, &mut out) else { continue };
            let tag = format!("controller.nav {ctx} ({name})");
            let required: Vec<&str> = if console { NAV_REQUIRED.iter().chain(CONTRACT_INPUTS).copied().collect() } else { NAV_REQUIRED.to_vec() };
            for input in required {
                match map.get(input) {
                    None => out.push(format!("{tag}: {input} unmapped (map it to an action or \"none\")")),
                    Some(v) => {
                        if console {
                            check_value(&format!("{tag}.{input}"), v, true, &mut out);
                        }
                    }
                }
            }
            if !console {
                for input in CONTRACT_INPUTS {
                    if map.contains_key(*input) {
                        out.push(format!("{tag}: {input} is owned by the capture contract or the room on a compositor; it cannot be remapped here"));
                    }
                }
            }
        }
    }
    out
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
    fn tmux_root_key_may_not_shadow_a_reserved_chord_but_the_prefix_is_exempt() {
        let clean = run("\n[keybindings]\ntmux_prefix = \"C-b\"\n[keybindings.agent]\naccept_chord = \"S-Enter\"\n");
        assert!(clean.findings.is_empty(), "{:?}", clean.findings);
        let bad = run("\n[keybindings]\ntmux_prefix = \"C-b\"\n[keybindings.agent]\naccept_chord = \"C-a\"\n");
        assert!(bad.findings.iter().any(|f| f.contains("tmux root key C-a shadows reserved terminal chord")), "{:?}", bad.findings);
        // A prefix equal to a reserved chord is the tier-3 owner's own choice, not a root-table steal.
        let own = run("\n[keybindings]\ntmux_prefix = \"C-a\"\n[keybindings.agent]\naccept_chord = \"C-a\"\n");
        assert!(own.findings.is_empty(), "{:?}", own.findings);
        assert_eq!(tmux_key_chord("S-Enter"), "SHIFT+Enter");
        assert_eq!(tmux_key_chord("C-M-x"), "CTRL+ALT+x");
    }

    #[test]
    fn missing_global_table_cannot_run() {
        let v: Value = "[x]\ny = 1\n".parse().unwrap();
        assert!(analyze(&v, None).is_err());
    }

    // ---- console context (C-16g) ----
    #[test]
    fn console_is_not_a_default_context_and_takes_console_namespaces_only() {
        // a row without contexts collides with a console-only row on the same chord? No: console is not default.
        let a = run("\n[[keybindings.global.bindings]]\nid = \"vt-a\"\ntier = 2\nchord = \"SUPER+CTRL+F3\"\naction = \"tmux:menu\"\ncontexts = [\"console\"]\n[[keybindings.global.bindings]]\nid = \"desk-a\"\ntier = 1\nchord = \"SUPER+CTRL+F3\"\naction = \"exec:x\"\n");
        assert!(a.findings.iter().all(|f| !f.starts_with("collision")), "{:?}", a.findings);
        assert!(a.findings.is_empty(), "{:?}", a.findings);
        // negative control: the same two rows both in console collide
        let b = run("\n[[keybindings.global.bindings]]\nid = \"vt-a\"\ntier = 2\nchord = \"SUPER+CTRL+F3\"\naction = \"tmux:menu\"\ncontexts = [\"console\"]\n[[keybindings.global.bindings]]\nid = \"vt-b\"\ntier = 2\nchord = \"SUPER+CTRL+F3\"\naction = \"tmux:keymap\"\ncontexts = [\"console\"]\n");
        assert!(b.findings.iter().any(|f| f.starts_with("collision")), "{:?}", b.findings);
        let c = run("\n[[keybindings.global.bindings]]\nid = \"vt-hypr\"\ntier = 2\nchord = \"SUPER+CTRL+F4\"\naction = \"hypr:killactive\"\ncontexts = [\"console\"]\n");
        assert!(c.findings.iter().any(|f| f.contains("cannot run on the console")), "{:?}", c.findings);
    }

    #[test]
    fn compositor_binds_are_not_active_on_the_console() {
        let toml = format!("{BASE}\n[[keybindings.global.bindings]]\nid = \"vt-print\"\ntier = 2\nchord = \"SUPER+CTRL+Print\"\naction = \"tmux:menu\"\ncontexts = [\"console\"]\n");
        let v: Value = toml.parse().unwrap();
        let a = analyze(&v, Some("hl.bind(\"SUPER + CTRL + Print\", x)\n")).unwrap();
        assert!(a.findings.iter().all(|f| !f.starts_with("collision")), "{:?}", a.findings);
    }

    // ---- literal duplicate (C-22) ----
    #[test]
    fn duplicate_compositor_bind_is_reported() {
        let v: Value = BASE.parse().unwrap();
        let one = analyze(&v, Some(" hl.bind(\"Print\", hl.dsp.exec_cmd(\"/usr/bin/mios-shot region\"))\n")).unwrap();
        assert!(one.findings.is_empty(), "{:?}", one.findings);
        // the old generator: a dispatch bind beside the literal one
        let two = analyze(&v, Some(" hl.bind(\"Print\", hl.dsp.exec_cmd(\"/usr/bin/mios-shot region\"))\n local b=mios_native_hl.bind(\"print\",mios_native_hl.dsp.exec_cmd(\"x\"))\n")).unwrap();
        // two hl.bind lines on one chord are an ordinary collision (both [hyprland.lua], every compositor context)
        assert!(two.findings.iter().any(|f| f.starts_with("collision chord print / print: ") && f.matches("[hyprland.lua]").count() == 2), "{:?}", two.findings);
    }

    // ---- controller audit (C-23 / INV-10) ----
    const NAV: &str = r#"
[keybindings]
[[keybindings.actions]]
key = "t"
[keybindings.tmux]
bindings = [{ key = "p" }, { key = "n" }, { key = "w" }, { key = "f" }, { key = "z" }, { key = "y" }]
[keybindings.global]
bypass_mod = "SUPER+CTRL"
action_namespaces = ["hypr", "exec", "quickshell", "mios", "tmux", "readline", "key", "pointer"]
[keybindings.global.action_argv]
"quickshell:keymap-toggle" = ["x"]
"quickshell:drawer-toggle" = ["x"]
[keybindings.global.controller]
mode_override_chord = ["BTN_MODE", "BTN_SELECT"]
[keybindings.global.controller.nav]
enabled = true
flick_on = 0.6
flick_off = 0.3
[keybindings.global.controller.nav.contexts]
desktop-2d = "desktop"
terminal = "terminal"
console = "console"
[keybindings.global.controller.nav.repeat]
BTN_DPAD_UP = [400, 60]
[keybindings.global.controller.nav.desktop]
BTN_SOUTH = ["key:ENTER", "key:SHIFT+ENTER"]
BTN_EAST = ["key:ESC", "key:ALT+LEFT"]
BTN_WEST = ["key:TAB", "key:SHIFT+TAB"]
BTN_DPAD_UP = "key:UP"
BTN_DPAD_DOWN = "key:DOWN"
BTN_DPAD_LEFT = "key:LEFT"
BTN_DPAD_RIGHT = "key:RIGHT"
LS_UP = "pointer:wheel-up"
LS_DOWN = "pointer:wheel-down"
LS_LEFT = "pointer:hwheel-left"
LS_RIGHT = "pointer:hwheel-right"
RS_LEFT = "hypr:movefocus left"
RS_RIGHT = "hypr:movefocus right"
RS_UP = "hypr:movefocus up"
RS_DOWN = "hypr:movefocus down"
BTN_TL2 = "key:CTRL+SHIFT+TAB"
BTN_TR2 = "key:CTRL+TAB"
BTN_SELECT = ["key:COMPOSE", "quickshell:keymap-toggle"]
BTN_MODE = "quickshell:drawer-toggle"
BTN_THUMBL = "key:CTRL+t"
BTN_THUMBR = "key:CTRL+0"
[keybindings.global.controller.nav.terminal_tmux]
inherits = "desktop"
RS_LEFT = "tmux:focus left"
BTN_TL2 = "tmux:key p"
[keybindings.global.controller.nav.terminal_plain]
inherits = "desktop"
BTN_SELECT = ["none", "quickshell:keymap-toggle"]
[keybindings.global.controller.nav.console_plain]
BTN_SOUTH = "key:ENTER"
BTN_EAST = ["key:ESC", "key:CTRL+c"]
BTN_WEST = ["key:TAB", "key:SHIFT+TAB"]
BTN_DPAD_UP = "key:UP"
BTN_DPAD_DOWN = "key:DOWN"
BTN_DPAD_LEFT = "key:LEFT"
BTN_DPAD_RIGHT = "key:RIGHT"
LS_UP = "none"
LS_DOWN = "none"
LS_LEFT = "none"
LS_RIGHT = "none"
RS_UP = "none"
RS_DOWN = "none"
RS_LEFT = "none"
RS_RIGHT = "none"
BTN_TL2 = "none"
BTN_TR2 = "none"
BTN_TL = "none"
BTN_TR = "none"
BTN_NORTH = "none"
BTN_SELECT = "none"
BTN_START = "none"
BTN_MODE = "none"
BTN_THUMBL = "none"
BTN_THUMBR = "none"
TOGGLE_CHORD = "mios:graphical-vt"
[keybindings.global.controller.nav.console_tmux]
inherits = "console_plain"
BTN_MODE = "tmux:menu"
BTN_SELECT = ["tmux:key w", "tmux:keymap"]
[desktop.hyprland.hypr3d.controller]
toggle_chord = ["BTN_THUMBL", "BTN_THUMBR"]
jump_button = "BTN_SOUTH"
crouch_button = "BTN_WEST"
"#;

    fn audit(toml: &str) -> Vec<String> {
        let v: Value = toml.parse().unwrap();
        let g = &v["keybindings"]["global"];
        let ns: BTreeSet<String> = str_list(g.get("action_namespaces")).into_iter().collect();
        let imp: BTreeSet<String> = g.get("action_argv").and_then(Value::as_table).map(|t| t.keys().cloned().collect()).unwrap_or_default();
        controller_audit(&v, &ns, &imp, &BTreeSet::new(), None)
    }

    #[test]
    fn complete_nav_map_has_no_findings() {
        assert!(audit(NAV).is_empty(), "{:?}", audit(NAV));
    }

    #[test]
    fn unmapped_input_is_a_finding_and_none_counts_as_mapped() {
        let f = audit(&NAV.replace("BTN_EAST = [\"key:ESC\", \"key:ALT+LEFT\"]\n", ""));
        assert!(f.iter().any(|x| x.contains("desktop-2d (desktop): BTN_EAST unmapped")), "{f:?}");
        // terminal profiles inherit desktop: they lose BTN_EAST too (inheritance is resolved)
        assert!(f.iter().any(|x| x.contains("terminal (terminal_plain): BTN_EAST unmapped")), "{f:?}");
        let g = audit(&NAV.replace("BTN_EAST = [\"key:ESC\", \"key:ALT+LEFT\"]", "BTN_EAST = \"none\""));
        assert!(g.is_empty(), "{g:?}");
    }

    #[test]
    fn nav_grammar_violations_are_findings() {
        let cases = [
            ("BTN_MODE = \"quickshell:drawer-toggle\"", "BTN_MODE = \"app\"", "\"app\" is not a nav value"),
            ("BTN_MODE = \"quickshell:drawer-toggle\"", "BTN_MODE = \"\"", "use \"none\" to unbind"),
            ("BTN_MODE = \"quickshell:drawer-toggle\"", "BTN_MODE = \"bogus\"", "must be <namespace>:<verb>"),
            ("BTN_MODE = \"tmux:menu\"", "BTN_MODE = \"hypr:killactive\"", "cannot run on the console"),
            ("BTN_TL2 = \"tmux:key p\"", "BTN_TL2 = \"tmux:key q\"", "tmux:key \"q\" is not a prefix key"),
            ("RS_LEFT = \"tmux:focus left\"", "RS_LEFT = \"tmux:warp left\"", "unknown tmux verb"),
            ("BTN_THUMBR = \"key:CTRL+0\"", "BTN_THUMBR = \"key:CTRL+0\"\nBTN_NORTH = \"key:F\"", "owned by the capture contract"),
            ("desktop-2d = \"desktop\"", "desktop-2d = \"desktop\"\ngame = \"desktop\"", "captured contexts belong to the app"),
            ("inherits = \"console_plain\"", "inherits = \"console_tmux\"", "inherits cycle"),
            ("BTN_SOUTH = [\"key:ENTER\", \"key:SHIFT+ENTER\"]", "BTN_SOUTH = [\"key:ENTER\"]", "value must be"),
            ("flick_off = 0.3", "flick_off = 0.7", "flick_off"),
            ("mode_override_chord = [\"BTN_MODE\", \"BTN_SELECT\"]", "mode_override_chord = [\"BTN_MODE\", \"BTN_THUMBL\"]", "share BTN_THUMBL"),
            ("crouch_button = \"BTN_WEST\"", "crouch_button = \"BTN_SOUTH\"", "both use BTN_SOUTH"),
            ("BTN_DPAD_UP = [400, 60]", "BTN_BOGUS = [400, 60]", "repeat.BTN_BOGUS"),
            ("console = \"console\"", "console = \"vpad\"", "the console has no virtual-pad consumer"),
        ];
        for (from, to, want) in cases {
            assert!(NAV.contains(from), "fixture lacks {from}");
            let f = audit(&NAV.replacen(from, to, 1));
            assert!(f.iter().any(|x| x.contains(want)), "{to:?} -> want {want:?}, got {f:?}");
        }
    }

    /// The staged all-steps SSOT (every "#@<STEP>" line enabled, every "@drop" line removed) passes the controller
    /// audit, so the grammar the gate enforces is the one the spec stages.
    #[test]
    fn staged_all_steps_controller_map_passes_the_audit() {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../../../docs/design/mios-global-bindings-staged.patch.toml");
        let Ok(text) = std::fs::read_to_string(&path) else { return };
        let mut out = String::new();
        for line in text.lines() {
            if line.contains("# @drop:") {
                continue;
            }
            let l = match line.strip_prefix("#@") {
                Some(rest) => rest.split_once(' ').map(|x| x.1).unwrap_or(""),
                None => line,
            };
            out.push_str(l);
            out.push('\n');
        }
        let f = audit(&out);
        assert!(f.is_empty(), "{f:#?}");
    }

}
