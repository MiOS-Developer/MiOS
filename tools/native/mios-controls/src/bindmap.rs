// AI-hint: mios-controls bindmap: one index of every action bound to every input -- global bindings (and their bypass escapes), compositor binding tables and the rendered session's own hl.bind lines (workspace loop expanded), tmux-os prefix/root keys and pane mouse menus, reserved terminal chords, the Guide layer, the Hypr3D room table and room/legacy pad roles, and the capture contract -- grouped by input and layer, with local conflict detection (the gate's same-physical rule) and the gate's findings mapped back onto chords.
// AI-related: src/mios-rs/mios-gate/src/keybinds.rs, tools/native/mios-inputd/src/router.rs, tools/native/mios-controls/src/layout.rs, usr/share/mios/mios.toml [keybindings] [desktop.hyprland.hypr3d.controller]
// AI-functions: collect, lua_binds, conflicts, finding_chords, layers_of, layer_title, split_chord, Source::editable
use crate::layout::{key_alias, mouse_input, tmux_to_key};
use crate::model::{at, field, str_list, Draft, GLOBAL_BINDINGS, ROOM, SYSTEM_LAYER, TMUX_BINDINGS};
use std::collections::{BTreeMap, BTreeSet};
use toml::Value;

pub const PAD_DESKTOP: &str = "pad:desktop";
pub const PAD_GUIDE: &str = "pad:guide";
pub const PAD_ROOM: &str = "pad:room";
pub const PAD_CAPTURED: &str = "pad:captured";
pub const PAD_LAYERS: &[&str] = &[PAD_DESKTOP, PAD_GUIDE, PAD_ROOM, PAD_CAPTURED];
pub const MODS: &[&str] = &["SUPER", "CTRL", "ALT", "SHIFT", "LEFTCTRL", "RIGHTCTRL"];

/// Where a use comes from; only the first five are editable (they live in [desktop.controls].editable paths).
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Source {
    Global { id: String, bypass: bool },
    Table { path: String, index: usize },
    Guide { button: String },
    Room { button: String },
    Tmux { index: usize },
    /// an hl.bind line of the rendered session with no editable table behind it
    Compositor,
    /// a chord the focused terminal owns (termios / Readline / job control)
    Reserved,
    /// a fixed role or a scalar setting outside the editable paths (SSOT path shown)
    Fixed { path: String },
}

impl Source {
    pub fn editable_path(&self) -> Option<String> {
        match self {
            Source::Global { .. } => Some(GLOBAL_BINDINGS.into()),
            Source::Table { path, .. } => Some(path.clone()),
            Source::Guide { .. } => Some(SYSTEM_LAYER.into()),
            Source::Room { .. } => Some(ROOM.into()),
            Source::Tmux { .. } => Some(TMUX_BINDINGS.into()),
            _ => None,
        }
    }
    pub fn word(&self) -> &'static str {
        match self {
            Source::Global { bypass: false, .. } => "global",
            Source::Global { bypass: true, .. } => "escape",
            Source::Table { .. } => "desktop",
            Source::Guide { .. } => "guide",
            Source::Room { .. } => "room",
            Source::Tmux { .. } => "tmux-os",
            Source::Compositor => "session",
            Source::Reserved => "terminal",
            Source::Fixed { .. } => "fixed",
        }
    }
    /// The SSOT location a person edits for this use.
    pub fn place(&self) -> String {
        match self {
            Source::Global { id, bypass } => format!("[keybindings.global] binding {id}{}", if *bypass { " (bypass)" } else { "" }),
            Source::Table { path, index } => format!("[{path}] entry {}", index + 1),
            Source::Guide { button } => format!("[{SYSTEM_LAYER}].{button}"),
            Source::Room { button } => format!("[{ROOM}].{button}"),
            Source::Tmux { index } => format!("[{TMUX_BINDINGS}] entry {}", index + 1),
            Source::Compositor => "the rendered compositor session (mios-gen render-hyprland)".into(),
            Source::Reserved => "[keybindings.global].reserved_terminal".into(),
            Source::Fixed { path } => path.clone(),
        }
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Use {
    /// "key:<token>" | "mouse:<code|wheel_*>" | "pad:<BTN_*|LS_*|RS_*>"
    pub input: String,
    /// "" (no modifier), "SUPER+SHIFT", "CTRL+b then", "CTRL+b then SHIFT", "tmux-os pane", or a PAD_* layer
    pub layer: String,
    pub chord: String,
    /// conflict key: normalized steps ("SUPER+return", "CTRL+x CTRL+e"), "tmux-prefix:<step>", "pad:<layer>:<button>"
    pub norm: String,
    pub action: String,
    pub label: String,
    pub source: Source,
    pub contexts: Vec<String>,
    pub note: String,
}

impl Use {
    pub fn kind(&self) -> &str {
        self.input.split(':').next().unwrap_or("")
    }
    pub fn editable(&self) -> bool {
        self.source.editable_path().is_some()
    }
    pub fn matches(&self, q: &str) -> bool {
        let q = q.to_ascii_lowercase();
        [&self.input, &self.chord, &self.action, &self.label, &self.note, &self.layer, &self.source.place()].iter().any(|s| s.to_ascii_lowercase().contains(&q))
    }
}

/// A chord split into its earlier steps, the last step's modifiers and its key token (normalized, lowercase).
pub fn split_chord(chord: &str, sep: &str) -> Option<(Vec<String>, Vec<String>, String)> {
    let norm = mios_resolver::keybindings::normalize_chord(chord, sep).ok()?;
    let mut steps: Vec<String> = norm.split(sep).map(str::to_owned).collect();
    let last = steps.pop()?;
    let mut mods = Vec::new();
    let mut key = String::new();
    for t in last.split('+') {
        if MODS.contains(&t) {
            mods.push(t.to_owned());
        } else {
            key = t.to_owned();
        }
    }
    Some((steps, mods, key))
}

pub fn layer_of(pre: &[String], mods: &[String]) -> String {
    let m = mods.join("+");
    if pre.is_empty() {
        m
    } else if m.is_empty() {
        format!("{} then", pre.join(" "))
    } else {
        format!("{} then {m}", pre.join(" "))
    }
}

pub fn layer_title(layer: &str) -> String {
    match layer {
        "" => "no modifier".into(),
        PAD_DESKTOP => "desktop (uncaptured)".into(),
        PAD_GUIDE => "Guide held".into(),
        PAD_ROOM => "Hypr3D room".into(),
        PAD_CAPTURED => "captured app".into(),
        l => l.into(),
    }
}

/// The input id and layer for a keyboard/mouse chord; a modifier pressed alone ("SUPER+Super_L") is that cap with
/// no modifier layer.
fn key_use_parts(pre: &[String], mods: &[String], key: &str) -> (String, String) {
    if let Some(m) = mouse_input(key) {
        return (m, layer_of(pre, mods));
    }
    let alias = key_alias(key);
    if MODS.contains(&alias.as_str()) {
        let rest: Vec<String> = mods.iter().filter(|m| **m != alias).cloned().collect();
        return (format!("key:{alias}"), layer_of(pre, &rest));
    }
    (format!("key:{alias}"), layer_of(pre, mods))
}

/// The contexts of a row without `contexts`: every one except the console (the gate's default set).
fn all_contexts() -> Vec<String> {
    crate::model::default_contexts().map(str::to_owned).collect()
}

/// Make a keyboard/mouse use from a chord, or None (with the chord kept as a pad/other use by the caller).
#[allow(clippy::too_many_arguments)]
fn chord_use(chord: &str, sep: &str, action: &str, label: &str, source: Source, contexts: Vec<String>, norm_prefix: &str) -> Option<Use> {
    let (pre, mods, key) = split_chord(chord, sep)?;
    if key.starts_with("BTN_") || key.is_empty() {
        return None;
    }
    let (input, layer) = key_use_parts(&pre, &mods, &key);
    let mut steps = pre.clone();
    let mut last = mods.clone();
    last.push(key);
    steps.push(last.join("+"));
    Some(Use {
        input,
        layer,
        chord: chord.trim().to_owned(),
        norm: format!("{norm_prefix}{}", steps.join(" ")),
        action: action.to_owned(),
        label: label.to_owned(),
        source,
        contexts,
        note: String::new(),
    })
}

// ---- the rendered compositor session --------------------------------------------------------------------------

/// A literal hl.bind line of the rendered session: (chord, dispatcher text, line flags).
#[derive(Clone, Debug, PartialEq)]
pub struct LuaBind {
    pub chord: String,
    pub dispatcher: String,
}

fn lua_args(rest: &str) -> Vec<String> {
    // split the call's arguments at top-level commas, ending at the call's closing parenthesis
    let mut out = Vec::new();
    let (mut depth, mut quote, mut cur) = (0i32, false, String::new());
    let mut prev = '\0';
    for c in rest.chars() {
        if quote {
            cur.push(c);
            if c == '"' && prev != '\\' {
                quote = false;
            }
            prev = c;
            continue;
        }
        match c {
            '"' => {
                quote = true;
                cur.push(c);
            }
            '(' | '{' | '[' => {
                depth += 1;
                cur.push(c);
            }
            ')' | '}' | ']' if depth == 0 => {
                out.push(cur.trim().to_owned());
                return out;
            }
            ')' | '}' | ']' => {
                depth -= 1;
                cur.push(c);
            }
            ',' if depth == 0 => {
                out.push(cur.trim().to_owned());
                cur.clear();
            }
            _ => cur.push(c),
        }
        prev = c;
    }
    out.push(cur.trim().to_owned());
    out
}

/// Evaluate a Lua string expression of literals and the loop variables `key`/`i`; None when anything else appears.
fn lua_concat(expr: &str, vars: &BTreeMap<&str, String>) -> Option<String> {
    let mut out = String::new();
    for part in expr.split("..") {
        let p = part.trim();
        if let Some(inner) = p.strip_prefix('"').and_then(|s| s.strip_suffix('"')) {
            out.push_str(inner);
        } else {
            out.push_str(vars.get(p)?);
        }
    }
    Some(out)
}

/// Every hl.bind of the rendered session with a chord it can evaluate, including the numbered `for i = a, b`
/// workspace loop (`local key = i % 10`).
pub fn lua_binds(lua: &str) -> Vec<LuaBind> {
    let mut out = Vec::new();
    let mut lines = lua.lines().peekable();
    while let Some(line) = lines.next() {
        let t = line.trim();
        if let Some(range) = t.strip_prefix("for i = ").and_then(|r| r.strip_suffix(" do")) {
            let Some((a, b)) = range.split_once(',') else { continue };
            let (Ok(a), Ok(b)) = (a.trim().parse::<i64>(), b.trim().parse::<i64>()) else { continue };
            let mut body = Vec::new();
            for l in lines.by_ref() {
                if l.trim() == "end" {
                    break;
                }
                body.push(l.trim().to_owned());
            }
            let modulo = body.iter().find_map(|l| l.strip_prefix("local key = i % ").and_then(|m| m.trim().parse::<i64>().ok()));
            for i in a..=b.min(a + 64) {
                let mut vars = BTreeMap::new();
                vars.insert("i", i.to_string());
                vars.insert("key", modulo.map(|m| (i % m).to_string()).unwrap_or_else(|| i.to_string()));
                for l in &body {
                    if let Some(rest) = l.strip_prefix("hl.bind(") {
                        let args = lua_args(rest);
                        if let (Some(c), Some(d)) = (args.first().and_then(|c| lua_concat(c, &vars)), args.get(1)) {
                            out.push(LuaBind { chord: c, dispatcher: d.replace("= i ", &format!("= {i} ")).replace("= i}", &format!("= {i}}}")) });
                        }
                    }
                }
            }
            continue;
        }
        if t.starts_with("local up=") || t.contains("hl.unbind(") {
            continue;
        }
        let rest = if let Some(r) = t.strip_prefix("hl.bind(") {
            r
        } else if let Some((_, r)) = t.split_once("mios_native_hl.bind(") {
            r
        } else {
            continue;
        };
        let args = lua_args(rest);
        if let (Some(c), Some(d)) = (args.first().and_then(|c| lua_concat(c, &BTreeMap::new())), args.get(1)) {
            out.push(LuaBind { chord: c, dispatcher: d.clone() });
        }
    }
    out
}

/// A dispatcher or command line shortened for the drawing's side panel.
pub fn pretty_action(d: &str) -> String {
    let d = d.trim().trim_start_matches("mios_native_hl.dsp.").trim_start_matches("hl.dsp.");
    let cmd = d.strip_prefix("exec_cmd(\"").and_then(|r| r.strip_suffix("\")")).map(|c| c.replace("\\\"", "\"").replace("\\\\", "\\"));
    match cmd {
        Some(c) => pretty_cmd(&c),
        None => d.to_owned(),
    }
}

pub fn pretty_cmd(c: &str) -> String {
    if let Some((_, a)) = c.split_once("mios-inputd dispatch '") {
        return a.trim_end_matches('\'').to_owned();
    }
    if let Some((_, a)) = c.split_once(" call ") {
        return format!("shell: {a}");
    }
    let mut words: Vec<&str> = c.split_whitespace().collect();
    let mut prefix = "";
    if words.first() == Some(&"alacritty") && words.get(1) == Some(&"-e") {
        words.drain(..2);
        prefix = "terminal: ";
    }
    if words.first() == Some(&"env") {
        words.remove(0);
        while words.first() == Some(&"-u") {
            words.drain(..2.min(words.len()));
        }
    }
    let first = words.first().map(|w| w.rsplit('/').next().unwrap_or(w)).unwrap_or("");
    let rest = if words.len() > 1 { format!(" {}", words[1..].join(" ")) } else { String::new() };
    format!("{prefix}{first}{rest}")
}

fn table_action(e: &toml::Table, parent: Option<&Value>) -> String {
    let args = match e.get("args") {
        Some(Value::String(s)) => s.clone(),
        Some(Value::Table(t)) => t.iter().map(|(k, v)| format!("{k}={}", v.as_str().map(str::to_owned).unwrap_or_else(|| v.to_string()))).collect::<Vec<_>>().join(" "),
        _ => String::new(),
    };
    match field(e, "dispatcher") {
        "" => {
            let bin = parent.and_then(|p| p.get("binary")).and_then(Value::as_str).unwrap_or("");
            pretty_cmd(format!("{bin} {args}").trim())
        }
        "exec_cmd" => pretty_cmd(&args),
        d if args.is_empty() => d.to_owned(),
        d => format!("{d} {args}"),
    }
}

// ---- collection -----------------------------------------------------------------------------------------------

/// Descriptions of the reserved terminal chords (what the terminal does with them).
const RESERVED_NOTES: &[(&str, &str)] = &[
    ("c", "interrupt (SIGINT)"),
    ("z", "suspend the job (SIGTSTP)"),
    ("d", "end of input"),
    ("\\", "quit (SIGQUIT)"),
    ("a", "start of line"),
    ("e", "end of line"),
    ("k", "cut to end of line"),
    ("u", "cut to start of line"),
    ("w", "cut previous word"),
    ("r", "search history"),
    ("l", "clear the screen"),
    ("b", "tmux-os prefix"),
    ("s", "flow control (stop output)"),
];

fn s<'a>(v: &'a Value, path: &str) -> &'a str {
    at(v, path).and_then(Value::as_str).unwrap_or("")
}

pub fn collect(merged: &Value, draft: &Draft, lua: Option<&str>) -> Vec<Use> {
    let sep = at(merged, "keybindings.global.chord_sep").and_then(Value::as_str).unwrap_or(" ").to_owned();
    let mut out: Vec<Use> = Vec::new();
    let all = all_contexts();

    // compositor binding tables (editable)
    let mut table_norms: BTreeMap<String, usize> = BTreeMap::new();
    for (path, entries) in &draft.tables {
        let parent = at(merged, path.trim_end_matches(".bindings"));
        for (i, e) in entries.iter().enumerate() {
            let k = field(e, "key");
            if let Some(u) = chord_use(k, "\0", &table_action(e, parent), field(e, "label"), Source::Table { path: path.clone(), index: i }, all.clone(), "") {
                table_norms.insert(u.norm.clone(), out.len());
                out.push(u);
            }
        }
    }

    // global bindings (and bypass escapes)
    for b in &draft.bindings {
        let id = field(b, "id").to_owned();
        let contexts: Vec<String> = match b.get("contexts") {
            Some(v) => str_list(Some(v)),
            None => all.clone(),
        };
        let pass: BTreeSet<String> = str_list(b.get("passthrough")).into_iter().collect();
        let app = field(b, "scope") == "app";
        let effective: Vec<String> = contexts.iter().filter(|c| app || !pass.contains(*c)).cloned().collect();
        let label = if app { format!("{} (the application's own key)", field(b, "note")).trim().to_owned() } else { field(b, "note").to_owned() };
        let chord = field(b, "chord");
        let action = field(b, "action");
        let realized = field(b, "realized_by") == "hyprland";
        match chord_use(chord, &sep, action, &label, Source::Global { id: id.clone(), bypass: false }, effective, "") {
            Some(mut u) => {
                if realized {
                    if let Some(&ti) = table_norms.get(&u.norm) {
                        u.note = format!("realized by {}", out[ti].source.place());
                        out[ti].note = format!("realizes global binding {id}");
                    } else {
                        u.note = "realized by the compositor session".into();
                    }
                }
                if app {
                    u.note = "scope = app: documentation only, the focused app owns it".into();
                }
                out.push(u);
            }
            None => {
                // a controller chord (BTN_*): one use per button, on the desktop pad layer
                if let Ok(n) = mios_resolver::keybindings::normalize_chord(chord, &sep) {
                    if n.split('+').all(|t| t.starts_with("BTN_")) {
                        for btn in n.split('+') {
                            out.push(Use {
                                input: format!("pad:{btn}"),
                                layer: PAD_DESKTOP.into(),
                                chord: n.clone(),
                                norm: format!("pad:{PAD_DESKTOP}:{n}"),
                                action: action.into(),
                                label: label.clone(),
                                source: Source::Global { id: id.clone(), bypass: false },
                                contexts: contexts.clone(),
                                note: String::new(),
                            });
                        }
                    }
                }
            }
        }
        if let Some(by) = b.get("bypass").and_then(Value::as_str) {
            if let Some(mut u) = chord_use(by, &sep, action, &format!("escape for {id}: reaches the host in VMs and captured apps"), Source::Global { id: id.clone(), bypass: true }, all.clone(), "") {
                u.note = "bypass_mod escape".into();
                out.push(u);
            }
        }
    }

    // the rendered session's own binds not explained by a table row or a realized binding
    let explained: BTreeSet<String> = out.iter().filter(|u| matches!(u.source, Source::Table { .. }) || (matches!(u.source, Source::Global { .. }) && u.note.starts_with("realized"))).map(|u| u.norm.clone()).collect();
    let escapes: BTreeSet<String> = out.iter().filter(|u| matches!(u.source, Source::Global { bypass: true, .. })).map(|u| u.norm.clone()).collect();
    let mut seen = BTreeSet::new();
    let desktop_mod = s(merged, "keybindings.desktop_modifier").replace(' ', "+");
    let actions: Vec<(String, String)> = at(merged, "keybindings.actions")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(|x| Some((x.get("key")?.as_str()?.to_owned(), x.get("label")?.as_str()?.to_owned()))).collect())
        .unwrap_or_default();
    for lb in lua.map(lua_binds).unwrap_or_default() {
        let Some(mut u) = chord_use(&lb.chord, "\0", &pretty_action(&lb.dispatcher), "", Source::Compositor, all.clone(), "") else { continue };
        if explained.contains(&u.norm) || escapes.contains(&u.norm) || !seen.insert(u.norm.clone()) {
            continue;
        }
        let action_hit = |k: &str| {
            split_chord(&format!("{desktop_mod}+{k}"), "\0").map(|(p, m, key)| key_use_parts(&p, &m, &key)) == Some((u.input.clone(), u.layer.clone()))
        };
        if let Some((k, label)) = actions.iter().find(|(k, _)| action_hit(k)) {
            u.label = label.clone();
            u.source = Source::Fixed { path: format!("[[keybindings.actions]] key = {k:?} with [keybindings].desktop_modifier") };
        } else if u.action.contains("workspace") {
            u.label = "workspace".into();
        }
        out.push(u);
    }

    // tmux-os: the prefix itself, its prefix-table keys (editable rows from [keybindings.tmux].bindings), the fixed
    // keys (menu, launcher, floats, agent accept, [[keybindings.actions]]), the root accept chord and the mouse menus
    let term = vec!["terminal".to_string()];
    let prefix = s(merged, "keybindings.tmux_prefix");
    let prefix_chord = tmux_to_key(prefix).map(|(m, k, sh)| {
        let mut mods: Vec<String> = m.iter().map(|x| x.to_string()).collect();
        if sh {
            mods.push("SHIFT".into());
        }
        mods.push(k);
        mods.join("+")
    });
    if let Some(pc) = &prefix_chord {
        if let Some(mut u) = chord_use(pc, "\0", "tmux-os prefix: the next key is a tmux-os command", "tmux-os prefix", Source::Fixed { path: "[keybindings].tmux_prefix".into() }, term.clone(), "") {
            u.chord = prefix.into();
            out.push(u);
        }
    }
    let pc = prefix_chord.clone().unwrap_or_default();
    let tmux_use = |key: &str, action: &str, label: &str, source: Source| -> Option<Use> {
        let (mods, token, shift) = tmux_to_key(key)?;
        let mut m: Vec<String> = mods.iter().map(|x| x.to_string()).collect();
        if shift {
            m.push("SHIFT".into());
        }
        let pre = vec![pc.clone()];
        let (input, layer) = key_use_parts(&pre, &m, &token);
        let mut step = m.clone();
        step.push(token);
        Some(Use {
            input,
            layer,
            chord: format!("{prefix} {key}"),
            norm: format!("tmux-prefix:{}", step.join("+")),
            action: action.into(),
            label: label.into(),
            source,
            contexts: vec!["terminal".into()],
            note: String::new(),
        })
    };
    for (i, b) in draft.tmux.iter().enumerate() {
        let cmd = b.get("command").and_then(Value::as_str).map(str::to_owned).or_else(|| b.get("menu").and_then(Value::as_str).map(|m| format!("menu {m}"))).unwrap_or_default();
        if let Some(u) = tmux_use(field(b, "key"), &cmd, field(b, "note"), Source::Tmux { index: i }) {
            out.push(u);
        }
    }
    let fixed_tmux: Vec<(String, String, String)> = {
        let mut v = vec![
            (s(merged, "keybindings.tmux.menu_key").to_owned(), "menu root".to_owned(), s(merged, "keybindings.tmux.menu_note").to_owned(), "[keybindings.tmux].menu_key"),
            (s(merged, "keybindings.tmux.launcher_key").to_owned(), "menu apps".into(), s(merged, "keybindings.tmux.launcher_note").to_owned(), "[keybindings.tmux].launcher_key"),
            (s(merged, "keybindings.tmux.float.new_key").to_owned(), "float new".into(), s(merged, "keybindings.tmux.float.new_note").to_owned(), "[keybindings.tmux.float].new_key"),
            (s(merged, "keybindings.tmux.float.toggle_key").to_owned(), "float shell".into(), s(merged, "keybindings.tmux.float.toggle_note").to_owned(), "[keybindings.tmux.float].toggle_key"),
            (s(merged, "keybindings.tmux.float.lift_key").to_owned(), "float lift".into(), s(merged, "keybindings.tmux.float.lift_note").to_owned(), "[keybindings.tmux.float].lift_key"),
            (s(merged, "keybindings.agent.accept_key").to_owned(), "agent accept".into(), s(merged, "keybindings.agent.accept_note").to_owned(), "[keybindings.agent].accept_key"),
            (prefix.to_owned(), "send-prefix".into(), "Send the prefix to the pane".into(), "[keybindings].tmux_prefix (pressed twice)"),
        ]
        .into_iter()
        .map(|(k, a, n, p)| (k, a, format!("{n}\u{1f}{p}")))
        .collect::<Vec<_>>();
        for (k, label) in &actions {
            v.push((k.clone(), "action".into(), format!("{label}\u{1f}[[keybindings.actions]] key")));
        }
        v
    };
    for (k, a, np) in fixed_tmux {
        if k.is_empty() {
            continue;
        }
        let (note, path) = np.split_once('\u{1f}').unwrap_or((&np, ""));
        if let Some(u) = tmux_use(&k, &a, note, Source::Fixed { path: path.into() }) {
            out.push(u);
        }
    }
    let accept = s(merged, "keybindings.agent.accept_chord");
    if let Some((m, k, sh)) = tmux_to_key(accept) {
        let mut mods: Vec<String> = m.iter().map(|x| x.to_string()).collect();
        if sh {
            mods.push("SHIFT".into());
        }
        mods.push(k);
        if let Some(mut u) = chord_use(&mods.join("+"), "\0", "agent accept", s(merged, "keybindings.agent.accept_note"), Source::Fixed { path: "[keybindings.agent].accept_chord".into() }, term.clone(), "") {
            u.chord = accept.into();
            u.note = "tmux-os root key".into();
            out.push(u);
        }
    }
    if at(merged, "keybindings.mouse").and_then(Value::as_bool) == Some(true) {
        for (where_, note) in [("pane", s(merged, "keybindings.tmux.pane_menu_note")), ("status", s(merged, "keybindings.tmux.menu_note"))] {
            out.push(Use {
                input: "mouse:273".into(),
                layer: format!("tmux-os {where_}"),
                chord: format!("MouseDown3{}", if where_ == "pane" { "Pane" } else { "Status" }),
                norm: format!("tmux-mouse:{where_}:273"),
                action: format!("menu {}", if where_ == "pane" { "pane" } else { "root" }),
                label: note.into(),
                source: Source::Fixed { path: "[keybindings].mouse (right-click menus)".into() },
                contexts: term.clone(),
                note: String::new(),
            });
        }
    }

    // reserved terminal chords
    for r in str_list(at(merged, "keybindings.global.reserved_terminal")) {
        let key = split_chord(&r, "\0").map(|(_, _, k)| k).unwrap_or_default();
        let note = RESERVED_NOTES.iter().find(|(k, _)| *k == key).map(|(_, n)| *n).unwrap_or("reserved for the focused terminal");
        if let Some(u) = chord_use(&r, "\0", note, "reserved for the focused terminal (termios / Readline)", Source::Reserved, term.clone(), "") {
            out.push(u);
        }
    }

    pad_uses(merged, draft, &mut out);
    out
}

fn pad_uses(merged: &Value, draft: &Draft, out: &mut Vec<Use>) {
    let c = |k: &str| at(merged, &format!("keybindings.global.controller.{k}"));
    let h = |k: &str| at(merged, &format!("desktop.hyprland.hypr3d.controller.{k}")).and_then(Value::as_str).unwrap_or("").to_owned();
    let hold = c("capture_hold_ms").and_then(Value::as_integer).unwrap_or(500);
    let ctx = |v: &[&str]| v.iter().map(|s| s.to_string()).collect::<Vec<_>>();
    let uncaptured = ctx(&["desktop-2d", "desktop-3d", "terminal"]);
    #[allow(clippy::too_many_arguments)]
    fn push(out: &mut Vec<Use>, input: &str, layer: &str, chord: String, action: String, label: &str, source: Source, contexts: Vec<String>) {
        let button = input.trim_start_matches("pad:");
        out.push(Use { input: input.into(), layer: layer.into(), chord, norm: format!("pad:{layer}:{button}"), action, label: label.into(), source, contexts, note: String::new() });
    }
    // Guide layer (editable)
    for (b, a) in &draft.system_layer {
        push(out, &format!("pad:{b}"), PAD_GUIDE, format!("Guide + {b}"), a.clone(), "hold Guide, press the button", Source::Guide { button: b.clone() }, uncaptured.clone());
    }
    let guide = c("guide_button").and_then(Value::as_str).unwrap_or("BTN_MODE").to_owned();
    let fixed = |p: &str| Source::Fixed { path: p.into() };
    let ovr = str_list(c("mode_override_chord"));
    if let Some(last) = ovr.iter().find(|b| **b != guide) {
        push(out, &format!("pad:{last}"), PAD_GUIDE, ovr.join("+"), "cursor mode on/off (half-capture: sticks drive the mouse)".into(), "mode override chord", fixed("[keybindings.global.controller].mode_override_chord"), ctx(&["desktop-2d", "desktop-3d", "terminal", "game", "app-capture", "vm-passthrough"]));
    }
    // desktop (uncaptured, outside the room)
    let desk = ctx(&["desktop-2d", "terminal"]);
    push(out, &format!("pad:{guide}"), PAD_DESKTOP, guide.clone(), "hold: the Guide layer".into(), "Guide button", fixed("[keybindings.global.controller].guide_button"), uncaptured.clone());
    let cursor = c("cursor_mode_button").and_then(Value::as_str).unwrap_or("BTN_START").to_owned();
    push(out, &format!("pad:{cursor}"), PAD_DESKTOP, cursor.clone(), format!("cursor mode on/off in {}", str_list(c("cursor_mode_contexts")).join(", ")), "cursor mode", fixed("[keybindings.global.controller].cursor_mode_button"), desk.clone());
    let north = h("fullscreen_button");
    if !north.is_empty() {
        push(out, &format!("pad:{north}"), PAD_DESKTOP, north.clone(), format!("tap: the aimed window takes every control; hold {hold} ms: fullscreen"), "take control", fixed("[desktop.hyprland.hypr3d.controller].fullscreen_button"), desk.clone());
    }
    for (k, a) in [("shaderglass_toggle_button", "mios:shaderglass toggle"), ("shaderglass_cycle_button", "mios:shaderglass cycle")] {
        let b = h(k);
        if !b.is_empty() {
            push(out, &format!("pad:{b}"), PAD_DESKTOP, b.clone(), a.into(), "ShaderGlass", fixed(&format!("[desktop.hyprland.hypr3d.controller].{k}")), desk.clone());
        }
    }
    let toggle = str_list(at(merged, "desktop.hyprland.hypr3d.controller.toggle_chord"));
    for b in &toggle {
        push(out, &format!("pad:{b}"), PAD_DESKTOP, toggle.join("+"), "press together: Hypr3D room on/off".into(), "room toggle chord", fixed("[desktop.hyprland.hypr3d.controller].toggle_chord"), desk.clone());
    }
    for b in ["BTN_SOUTH", "BTN_EAST", "BTN_WEST", "BTN_SELECT", "BTN_TL2", "BTN_TR2", "BTN_DPAD_UP", "BTN_DPAD_DOWN", "BTN_DPAD_LEFT", "BTN_DPAD_RIGHT"] {
        let taken = out.iter().any(|u| u.layer == PAD_DESKTOP && u.input == format!("pad:{b}"));
        if !taken {
            push(out, &format!("pad:{b}"), PAD_DESKTOP, b.into(), "the focused app (mios-inputd's virtual pad)".into(), "passes through", fixed("mios-inputd router"), desk.clone());
        }
    }
    // room (desktop-3d): the editable room table first, then the room's built-in map (shadowed where the table has it)
    let room = ctx(&["desktop-3d"]);
    for (b, v) in &draft.room {
        let action = match v {
            Value::Array(a) => format!("tap {} / hold {}", a.first().and_then(Value::as_str).unwrap_or(""), a.get(1).and_then(Value::as_str).unwrap_or("")),
            Value::String(s) => s.clone(),
            other => other.to_string(),
        };
        push(out, &format!("pad:{b}"), PAD_ROOM, b.clone(), action, "room table", Source::Room { button: b.clone() }, room.clone());
    }
    let builtin: Vec<(String, String, String)> = vec![
        (h("jump_button"), "jump; double press: fly / hover".into(), "jump_button".into()),
        (h("crouch_button"), "posture (stand, crouch, sit); hold: fly down".into(), "crouch_button".into()),
        (h("grab_button"), "hold: grab and move the aimed window".into(), "grab_button".into()),
        (h("resize_button"), "hold: resize the aimed window".into(), "resize_button".into()),
        (h("view_mode_button"), "camera view cycle".into(), "view_mode_button".into()),
        (h("shaderglass_toggle_button"), "CRT on/off".into(), "shaderglass_toggle_button".into()),
        (h("shaderglass_cycle_button"), "CRT cycle".into(), "shaderglass_cycle_button".into()),
        (h("cursor_mode_button"), "room cursor".into(), "cursor_mode_button".into()),
        (north.clone(), format!("tap: the aimed window takes every control; hold {hold} ms: fullscreen"), "fullscreen_button".into()),
    ];
    for (b, a, k) in builtin {
        if b.is_empty() {
            continue;
        }
        let shadow = draft.room.contains_key(&b);
        push(out, &format!("pad:{b}"), PAD_ROOM, b.clone(), a, "room built-in", fixed(&format!("[desktop.hyprland.hypr3d.controller].{k}")), room.clone());
        if shadow {
            out.last_mut().unwrap().note = "overridden by the room table".into();
        }
    }
    for b in &toggle {
        push(out, &format!("pad:{b}"), PAD_ROOM, toggle.join("+"), "press together: leave the room".into(), "room toggle chord", fixed("[desktop.hyprland.hypr3d.controller].toggle_chord"), room.clone());
    }
    for (dir, what) in [("UP", "forward"), ("DOWN", "back"), ("LEFT", "left"), ("RIGHT", "right")] {
        push(out, &format!("pad:LS_{dir}"), PAD_ROOM, format!("LS {dir}"), format!("move {what}"), "left stick", fixed("[desktop.hyprland.hypr3d.controller].left_stick_mode"), room.clone());
        push(out, &format!("pad:RS_{dir}"), PAD_ROOM, format!("RS {dir}"), format!("look {}", what.replace("forward", "up").replace("back", "down")), "right stick", fixed("[desktop.hyprland.hypr3d.controller].right_stick_mode"), room.clone());
        if at(merged, "desktop.hyprland.hypr3d.controller.dpad_enabled").and_then(Value::as_bool) != Some(false) {
            push(out, &format!("pad:BTN_DPAD_{dir}"), PAD_ROOM, format!("D-pad {dir}"), format!("step {what}"), "d-pad", fixed("[desktop.hyprland.hypr3d.controller].dpad_enabled"), room.clone());
        }
    }
    // captured: the app owns everything but the escapes
    let cap = ctx(&["app-capture", "game", "vm-passthrough"]);
    for b in &toggle {
        push(out, &format!("pad:{b}"), PAD_CAPTURED, toggle.join("+"), format!("hold {hold} ms together: hand control back to the desktop"), "release", fixed("[desktop.hyprland.hypr3d.controller].toggle_chord + capture_hold_ms"), cap.clone());
    }
    if c("captured_system_layer").and_then(Value::as_bool) == Some(true) {
        for (b, a) in &draft.system_layer {
            push(out, &format!("pad:{b}"), PAD_CAPTURED, format!("Guide + {b}"), a.clone(), "Guide layer (captured_system_layer = true)", Source::Guide { button: b.clone() }, cap.clone());
        }
    }
}

// ---- conflicts ------------------------------------------------------------------------------------------------

fn host_source(s: &Source) -> bool {
    matches!(s, Source::Global { .. } | Source::Table { .. } | Source::Compositor | Source::Reserved | Source::Fixed { .. } | Source::Tmux { .. })
}

/// Indices of uses in conflict: two different physical binds on the same chord (or tmux-os prefix key) in an
/// intersecting context. A realized global binding and its compositor row are one bind, as in the gate; an
/// application-owned (scope = app) row never conflicts; Guide-layer and room entries are maps (one per button).
pub fn conflicts(uses: &[Use]) -> BTreeSet<usize> {
    let mut by: BTreeMap<&str, Vec<usize>> = BTreeMap::new();
    for (i, u) in uses.iter().enumerate() {
        if u.norm.starts_with("pad:") || u.note.starts_with("scope = app") || !host_source(&u.source) {
            continue;
        }
        by.entry(u.norm.as_str()).or_default().push(i);
    }
    let mut out = BTreeSet::new();
    for idx in by.values() {
        for (n, &a) in idx.iter().enumerate() {
            for &b in &idx[n + 1..] {
                let (x, y) = (&uses[a], &uses[b]);
                let same_bind = |p: &Use, q: &Use| matches!((&p.source, &q.source), (Source::Global { id, .. }, Source::Table { .. }) if q.note == format!("realizes global binding {id}"));
                if same_bind(x, y) || same_bind(y, x) {
                    continue;
                }
                // a reserved terminal chord only loses to a host (compositor) bind; tmux-os runs inside the terminal and
                // its prefix is the tier-3 owner of its own chord (the gate's rule 3/8)
                let host = |s: &Source| matches!(s, Source::Global { .. } | Source::Table { .. } | Source::Compositor);
                if (x.source == Source::Reserved && !host(&y.source)) || (y.source == Source::Reserved && !host(&x.source)) {
                    continue;
                }
                if x.contexts.iter().any(|c| y.contexts.contains(c)) {
                    out.insert(a);
                    out.insert(b);
                }
            }
        }
    }
    out
}

/// The chords the gate's findings name ("collision chord A / B: ...", "<tag>: chord A shadows ...").
pub fn finding_chords(findings: &[String]) -> BTreeSet<String> {
    let mut out = BTreeSet::new();
    for f in findings {
        if let Some(rest) = f.strip_prefix("collision chord ").or_else(|| f.strip_prefix("prefix-collision chord ")) {
            if let Some((pair, _)) = rest.split_once(": ") {
                for c in pair.split(" / ") {
                    out.insert(c.trim().to_owned());
                }
            }
        } else if let Some((_, rest)) = f.split_once(": chord ") {
            for stop in [" shadows", " is host-bound"] {
                if let Some((c, _)) = rest.split_once(stop) {
                    out.insert(c.trim().to_owned());
                }
            }
        }
    }
    out
}

/// The layers present among uses of one input kind ("key", "mouse", "pad"), in a stable, readable order.
pub fn layers_of(uses: &[Use], kind: &str) -> Vec<String> {
    if kind == "pad" {
        return PAD_LAYERS.iter().map(|s| s.to_string()).collect();
    }
    let set: BTreeSet<&str> = uses.iter().filter(|u| u.kind() == kind).map(|u| u.layer.as_str()).collect();
    let mut v: Vec<String> = set.into_iter().map(str::to_owned).collect();
    v.sort_by_key(|l| (l.contains(" then") || l.starts_with("tmux-os"), l.matches('+').count() + usize::from(!l.is_empty()), l.clone()));
    v
}

#[cfg(test)]
mod tests {
    use super::*;

    const SSOT: &str = r#"
[keybindings]
tmux_prefix = "C-b"
desktop_modifier = "CTRL ALT SHIFT"
mouse = true
[[keybindings.actions]]
id = "terminal"
label = "MiOS Terminal"
key = "t"
[keybindings.agent]
accept_key = "Enter"
accept_chord = "S-Enter"
accept_note = "Accept"
[keybindings.tmux]
menu_key = "Space"
launcher_key = "e"
bindings = [ { key = "h", command = "select-pane -L", note = "Pane left" }, { key = "H", command = "resize-pane -L 5", note = "Resize left" }, { key = "$", command = "rename", note = "Rename" } ]
[keybindings.tmux.float]
new_key = "*"
toggle_key = "`"
lift_key = "F"
[keybindings.global]
chord_sep = " "
reserved_terminal = ["CTRL+c", "CTRL+b"]
[[keybindings.global.bindings]]
id = "close-window"
tier = 1
chord = "SUPER+c"
action = "hypr:killactive"
realized_by = "hyprland"
[[keybindings.global.bindings]]
id = "keymap"
tier = 1
chord = "SUPER+Escape"
bypass = "SUPER+CTRL+Escape"
action = "quickshell:keymap-toggle"
[[keybindings.global.bindings]]
id = "editor"
tier = 3
chord = "CTRL+x CTRL+e"
action = "readline:edit"
contexts = ["terminal"]
scope = "app"
[[keybindings.global.bindings]]
id = "wheel"
tier = 1
chord = "SUPER+CTRL+mouse_up"
action = "mios:session-scale-up"
realized_by = "hyprland"
[keybindings.global.controller]
guide_button = "BTN_MODE"
cursor_mode_button = "BTN_START"
mode_override_chord = ["BTN_MODE", "BTN_SELECT"]
capture_hold_ms = 500
[keybindings.global.controller.system_layer]
BTN_SOUTH = "hypr:togglefloating"
[keybindings.global.controller.room]
BTN_SELECT = ["hypr:hypr3d:view", "quickshell:keymap-toggle"]
[desktop.hyprland]
bindings = [ { key = "SUPER + C", dispatcher = "window.close", label = "Close window" }, { key = "SUPER + mouse:272", dispatcher = "window.drag", label = "Move window" } ]
[desktop.hyprland.hypr3d.controller]
toggle_chord = ["BTN_THUMBL", "BTN_THUMBR"]
fullscreen_button = "BTN_NORTH"
view_mode_button = "BTN_SELECT"
jump_button = "BTN_SOUTH"
shaderglass_toggle_button = "BTN_TR"
"#;

    const LUA: &str = "hl.bind(\"SUPER + C\", hl.dsp.window.close())\nhl.bind(\"SUPER + Q\", hl.dsp.exec_cmd(\"alacritty -e env -u TMUX /usr/libexec/mios/mios-terminal\"))\nhl.bind(\"CTRL + ALT + SHIFT + t\", hl.dsp.exec_cmd(\"x\"))\nfor i = 1, 10 do\n local key = i % 10\n hl.bind(\"SUPER\" .. \" + \" .. key, hl.dsp.focus({ workspace = i }))\nend\n local b=mios_native_hl.bind(\"SUPER+CTRL+mouse_up\",mios_native_hl.dsp.exec_cmd(\"/usr/libexec/mios/mios-inputd dispatch 'mios:session-scale-up'\"))\n local up=mios_native_hl.bind(\"SUPER+CTRL+m\",x,{release=true})\n hl.unbind(\"SUPER + F12\")\n";

    fn uses() -> Vec<Use> {
        let v: Value = SSOT.parse().unwrap();
        collect(&v, &Draft::from_merged(&v), Some(LUA))
    }

    fn on<'a>(u: &'a [Use], input: &str, layer: &str) -> Vec<&'a Use> {
        u.iter().filter(|x| x.input == input && x.layer == layer).collect()
    }

    #[test]
    fn global_table_and_session_binds_meet_on_one_key_without_double_counting() {
        let u = uses();
        let c = on(&u, "key:c", "SUPER");
        assert_eq!(c.len(), 2, "{c:#?}");
        assert!(c.iter().any(|x| matches!(x.source, Source::Global { .. }) && x.note.contains("realized by [desktop.hyprland.bindings]")));
        assert!(c.iter().any(|x| matches!(x.source, Source::Table { .. }) && x.label == "Close window"));
        assert!(conflicts(&u).is_empty(), "a realized binding and its table row are one bind: {:?}", conflicts(&u).iter().map(|i| &u[*i]).collect::<Vec<_>>());
        let q = on(&u, "key:q", "SUPER");
        assert_eq!(q.len(), 1);
        assert_eq!(q[0].source, Source::Compositor);
        assert_eq!(q[0].action, "terminal: mios-terminal");
    }

    #[test]
    fn workspace_loop_is_expanded_and_actions_keys_are_attributed() {
        let u = uses();
        for k in ["1", "5", "9", "0"] {
            assert_eq!(on(&u, &format!("key:{k}"), "SUPER").len(), 1, "workspace key {k}");
        }
        assert!(on(&u, "key:0", "SUPER")[0].action.contains("workspace = 10"), "{:?}", on(&u, "key:0", "SUPER"));
        let t = on(&u, "key:t", "CTRL+ALT+SHIFT");
        assert_eq!(t.len(), 1);
        assert_eq!(t[0].label, "MiOS Terminal");
        assert!(matches!(&t[0].source, Source::Fixed { path } if path.contains("keybindings.actions")));
    }

    #[test]
    fn mouse_tmux_reserved_bypass_and_multistep_layers() {
        let u = uses();
        assert_eq!(on(&u, "mouse:wheel_up", "SUPER+CTRL").len(), 1, "the wheel bind is the global row; its native session line is explained");
        assert_eq!(on(&u, "mouse:272", "SUPER")[0].label, "Move window");
        assert_eq!(on(&u, "mouse:273", "tmux-os pane").len(), 1);
        assert_eq!(on(&u, "key:h", "CTRL+b then")[0].source, Source::Tmux { index: 0 });
        assert_eq!(on(&u, "key:h", "CTRL+b then SHIFT")[0].label, "Resize left");
        assert_eq!(on(&u, "key:4", "CTRL+b then SHIFT")[0].label, "Rename", "$ is Shift+4");
        assert_eq!(on(&u, "key:space", "CTRL+b then").len(), 1, "menu key");
        assert_eq!(on(&u, "key:8", "CTRL+b then SHIFT").len(), 1, "float new *");
        assert_eq!(on(&u, "key:return", "SHIFT")[0].chord, "S-Enter");
        assert!(on(&u, "key:b", "CTRL").iter().any(|x| x.source == Source::Reserved));
        assert!(on(&u, "key:b", "CTRL").iter().any(|x| x.label == "tmux-os prefix"));
        assert!(on(&u, "key:escape", "SUPER+CTRL").iter().any(|x| matches!(x.source, Source::Global { bypass: true, .. })));
        assert_eq!(on(&u, "key:e", "CTRL+x then CTRL").len(), 1);
        assert!(on(&u, "key:e", "CTRL+x then CTRL")[0].note.starts_with("scope = app"));
    }

    #[test]
    fn pad_layers_carry_guide_room_contract_and_capture_roles() {
        let u = uses();
        assert_eq!(on(&u, "pad:BTN_SOUTH", PAD_GUIDE)[0].action, "hypr:togglefloating");
        let sel = on(&u, "pad:BTN_SELECT", PAD_ROOM);
        assert!(sel.iter().any(|x| x.source == Source::Room { button: "BTN_SELECT".into() } && x.action.contains("tap hypr:hypr3d:view")));
        assert!(sel.iter().any(|x| x.note == "overridden by the room table"));
        assert!(on(&u, "pad:BTN_NORTH", PAD_DESKTOP)[0].action.contains("hold 500 ms"));
        assert!(on(&u, "pad:BTN_THUMBL", PAD_CAPTURED)[0].action.contains("hand control back"));
        assert!(on(&u, "pad:BTN_SELECT", PAD_GUIDE)[0].action.contains("cursor mode"));
        assert!(on(&u, "pad:LS_UP", PAD_ROOM)[0].action.contains("move forward"));
        assert_eq!(on(&u, "pad:BTN_TR", PAD_DESKTOP)[0].action, "mios:shaderglass toggle");
    }

    #[test]
    fn local_conflicts_flag_a_second_bind_and_reserved_chords() {
        let mut v: Value = SSOT.parse().unwrap();
        let mut d = Draft::from_merged(&v);
        d.bindings[1].insert("chord".into(), Value::String("SUPER+q".into()));
        let u = collect(&v, &d, Some(LUA));
        let c = conflicts(&u);
        let names: Vec<_> = c.iter().map(|i| (u[*i].source.word(), u[*i].input.as_str())).collect();
        assert!(names.contains(&("global", "key:q")) && names.contains(&("session", "key:q")), "{names:?}");
        v["keybindings"]["tmux"]["bindings"].as_array_mut().unwrap()[0]["key"] = Value::String("e".into());
        let d = Draft::from_merged(&v);
        let u = collect(&v, &d, Some(LUA));
        assert!(conflicts(&u).iter().any(|i| u[*i].input == "key:e" && u[*i].layer == "CTRL+b then"), "tmux-os h moved onto the launcher key");
    }

    #[test]
    fn gate_findings_map_back_to_chords() {
        let f = vec![
            "collision chord SUPER+c / SUPER+c: terminal [keybindings.global] vs hl.bind(SUPER + C) [hyprland.lua] in desktop-2d".to_string(),
            "steal: chord CTRL+c shadows reserved terminal chord \"CTRL+c\" in the terminal context".to_string(),
            "x: chord SUPER+p is host-bound in vm-passthrough but ...".to_string(),
        ];
        let c = finding_chords(&f);
        assert!(c.contains("SUPER+c") && c.contains("CTRL+c") && c.contains("SUPER+p"), "{c:?}");
    }

    #[test]
    fn layers_are_ordered_plain_first_then_modifiers_then_prefixes() {
        let u = uses();
        let l = layers_of(&u, "key");
        let pos = |x: &str| l.iter().position(|y| y == x).unwrap_or(usize::MAX);
        assert!(pos("SUPER") < pos("SUPER+CTRL"));
        assert!(pos("SUPER+CTRL") < pos("CTRL+b then"));
        assert_eq!(layers_of(&u, "pad").len(), 4);
    }
}
