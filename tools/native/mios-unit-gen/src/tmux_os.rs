// AI-hint: Shared MiOS-Tmux (tmux-os) policy read from SSOT: hidden internal session prefixes, Linux-console client detection, tmux key grammar and quoting, and the tmux formats the theme, keys, selector and executor all use.
// AI-related: /usr/share/mios/mios.toml [terminal.sessions] [keybindings.tmux.float] [theme.tmux.console], tools/native/mios-service-core/src/tmux_theme.rs, src/mios-rs/miosd/src/sessions.rs, tools/native/mios-gen/src/tmux_os.rs
// AI-functions: hidden_session_prefixes, visible_session_format, console_terms, console_client, console_client_format, term_glob, valid_key, tmux_key, quote, plain_text

/// Agent worker parking sessions (`mios-workspace-<head>`) are named by the relay and the MCP server, not by
/// the SSOT: the prefix is part of their storage contract, so it is always hidden and is not tunable.
pub const WORKSPACE_SESSION_PREFIX: &str = "mios-workspace-";

fn get<'a>(doc: &'a toml::Value, path: &str) -> Option<&'a toml::Value> {
    path.split('.').try_fold(doc, |cur, key| cur.get(key))
}

fn str_list(doc: &toml::Value, path: &str) -> Result<Vec<String>, String> {
    get(doc, path)
        .ok_or_else(|| format!("[{path}] is missing from SSOT"))?
        .as_array()
        .ok_or_else(|| format!("{path} must be an array of strings"))?
        .iter()
        .map(|v| v.as_str().map(str::to_owned).ok_or_else(|| format!("{path} must contain only strings")))
        .collect()
}

/// A session-name prefix MiOS can put inside a tmux `#{m:PREFIX*,...}` match without escaping.
fn valid_prefix(p: &str) -> bool {
    !p.is_empty() && p.len() <= 64 && p.bytes().all(|b| b.is_ascii_alphanumeric() || b"._-".contains(&b))
}

/// Effective hidden-session prefixes: `[terminal.sessions].hidden_session_prefixes` plus the fixed workspace
/// prefix. The float session prefix must be listed explicitly, so the SSOT shows every hidden family.
pub fn hidden_session_prefixes(doc: &toml::Value) -> Result<Vec<String>, String> {
    let mut out = str_list(doc, "terminal.sessions.hidden_session_prefixes")?;
    if let Some(bad) = out.iter().find(|p| !valid_prefix(p)) {
        return Err(format!("[terminal.sessions].hidden_session_prefixes: invalid prefix {bad:?}"));
    }
    if let Some(float) = get(doc, "keybindings.tmux.float") {
        let prefix = float
            .get("session_prefix")
            .and_then(toml::Value::as_str)
            .ok_or("[keybindings.tmux.float].session_prefix is missing from SSOT")?;
        if !valid_prefix(prefix) {
            return Err(format!("[keybindings.tmux.float].session_prefix: invalid prefix {prefix:?}"));
        }
        if !out.iter().any(|p| p == prefix) {
            return Err(format!(
                "[keybindings.tmux.float].session_prefix {prefix:?} must be listed in [terminal.sessions].hidden_session_prefixes"
            ));
        }
    }
    if !out.iter().any(|p| p == WORKSPACE_SESSION_PREFIX) {
        out.push(WORKSPACE_SESSION_PREFIX.to_owned());
    }
    Ok(out)
}

/// `true` when `name` is an internal session no picker may list.
pub fn is_hidden_session(prefixes: &[String], name: &str) -> bool {
    prefixes.iter().any(|p| name.starts_with(p.as_str()))
}

/// tmux format that is true for a session every picker may list (`choose-tree -f`, session cycling).
/// `#{!m:` is not tmux syntax; negation is `#{!:#{m:...}}`.
pub fn visible_session_format(prefixes: &[String]) -> String {
    let mut terms = prefixes.iter().map(|p| format!("#{{!:#{{m:{p}*,#{{session_name}}}}}}"));
    let first = terms.next().unwrap_or_else(|| "1".to_owned());
    terms.fold(first, |acc, t| format!("#{{&&:{acc},{t}}}"))
}

fn valid_term_pattern(p: &str) -> bool {
    !p.is_empty() && p.len() <= 64 && p.bytes().all(|b| b.is_ascii_alphanumeric() || b"*?._+-".contains(&b))
}

/// `[theme.tmux.console].terms`: TERM patterns of Linux-console clients.
pub fn console_terms(doc: &toml::Value) -> Result<Vec<String>, String> {
    let terms = str_list(doc, "theme.tmux.console.terms")?;
    if terms.is_empty() {
        return Err("[theme.tmux.console].terms must not be empty".into());
    }
    if let Some(bad) = terms.iter().find(|p| !valid_term_pattern(p)) {
        return Err(format!("[theme.tmux.console].terms: invalid TERM pattern {bad:?}"));
    }
    Ok(terms)
}

/// tmux fnmatch subset used for TERM patterns: `*` any run, `?` one byte, everything else literal.
pub fn term_glob(pattern: &str, term: &str) -> bool {
    fn go(p: &[u8], t: &[u8]) -> bool {
        match (p.first(), t.first()) {
            (None, None) => true,
            (Some(b'*'), _) => go(&p[1..], t) || (!t.is_empty() && go(p, &t[1..])),
            (Some(b'?'), Some(_)) => go(&p[1..], &t[1..]),
            (Some(a), Some(b)) if a == b => go(&p[1..], &t[1..]),
            _ => false,
        }
    }
    go(pattern.as_bytes(), term.as_bytes())
}

/// One console decision for the selector, the launcher flag and tmux (R16): a client is a Linux console when
/// its TERM matches `[theme.tmux.console].terms`, unless it arrives over SSH with a TERM that
/// `[theme.tmux].remote_truecolor_terms` re-declares as `remote_term` before it attaches (phone apps send
/// "linux" and then draw full colour and glyphs).
pub fn console_client(doc: &toml::Value, term: &str, ssh: bool) -> Result<bool, String> {
    let terms = console_terms(doc)?;
    if !terms.iter().any(|p| term_glob(p, term)) {
        return Ok(false);
    }
    if ssh {
        let remote = get(doc, "theme.tmux.remote_truecolor_terms")
            .and_then(toml::Value::as_array)
            .map(|a| a.iter().filter_map(toml::Value::as_str).any(|p| term_glob(p, term)))
            .unwrap_or(false);
        if remote {
            return Ok(false);
        }
    }
    Ok(true)
}

/// tmux format, evaluated per client, that is true for a Linux-console client (a non-UTF-8 client counts:
/// it cannot draw the box-drawing or Nerd glyphs either).
pub fn console_client_format(terms: &[String]) -> String {
    terms.iter().rev().fold("#{!=:#{client_utf8},1}".to_owned(), |acc, p| {
        format!("#{{||:#{{m:{p},#{{client_termname}}}},{acc}}}")
    })
}

/// Prefix-table and menu key grammar: one ASCII letter or digit, one safe punctuation key, or a named key.
pub fn valid_key(key: &str) -> bool {
    const NAMED: &[&str] = &["Space", "Enter", "Tab", "BTab", "BSpace", "Up", "Down", "Left", "Right", "PPage", "NPage"];
    NAMED.contains(&key)
        || (key.len() == 1 && {
            let b = key.as_bytes()[0];
            b.is_ascii_alphanumeric() || b"*`:?[]=,$!&(){}<>".contains(&b)
        })
}

/// A key as one tmux word: letters, digits and named keys stay bare; punctuation is single-quoted
/// (the grammar has no `'`, so no escaping is ever needed).
pub fn tmux_key(key: &str) -> String {
    if key.bytes().all(|b| b.is_ascii_alphanumeric()) {
        key.to_owned()
    } else {
        format!("'{key}'")
    }
}

/// One tmux word in double quotes. tmux expands `$VAR` and `~` and decodes `\\`, `\"` and `\$` inside double
/// quotes, so the result decodes to exactly `s` at every nesting level it is wrapped for.
pub fn quote(s: &str) -> String {
    let mut out = String::with_capacity(s.len() + 2);
    out.push('"');
    for c in s.chars() {
        if matches!(c, '\\' | '"' | '$') {
            out.push('\\');
        }
        out.push(c);
    }
    out.push('"');
    out
}

/// One tmux word: bare when it is a plain identifier, else [`quote`]d (empty words are `""`).
pub fn word(s: &str) -> String {
    if !s.is_empty() && s.bytes().all(|b| b.is_ascii_alphanumeric() || b"_-./".contains(&b)) {
        s.to_owned()
    } else {
        quote(s)
    }
}

/// SSOT text shown by tmux (labels, notes, messages): printable ASCII without quotes, `#`, braces, `$` or
/// backslash, so it can sit inside any quoting level and is never read as a format or a variable.
pub fn plain_text(s: &str) -> bool {
    !s.is_empty() && s.len() <= 120 && s.bytes().all(|b| (0x20..0x7f).contains(&b) && !b"'\"#{}$\\".contains(&b))
}

// ---- mios-keys.tmux.conf: prefix bindings, generated menus, floats, root keys ---------------------------

use crate::UnitGenError;
use std::collections::{BTreeMap, BTreeSet};

/// Human executor the generated helpers run (`mios-terminal --menu/--app/--float/--session-cycle`).
const TERMINAL: &str = "/usr/libexec/mios/mios-terminal";
/// Format for "this pane belongs to a managed AI workspace" (relay/MCP-owned layout): the window's head, an
/// MCP-owned pane, or a parked worker. Option names are the relay's storage contract, not tunables.
const MANAGED_PANE: &str = "#{||:#{@mios-workspace-head},#{||:#{@mios-mcp-owner},#{@mios-workspace-worker}}}";
/// tmux 3.7c stock condition: the application owns the click, or the pane is in a mode other than copy/view.
const MOUSE_PASSTHROUGH: &str = "#{||:#{mouse_any_flag},#{&&:#{pane_in_mode},#{?#{m/r:(copy|view)-mode,#{pane_mode}},0,1}}}";
/// Centre a key-opened menu; put a right-click menu at the pointer (one alias serves both openers).
const MENU_X: &str = "#{?mouse_x,#{popup_mouse_centre_x},#{popup_centre_x}}";
const MENU_Y: &str = "#{?mouse_y,#{popup_mouse_y},#{popup_centre_y}}";
/// Hook slot of the float-session reaper (the theme's workspace hooks use [90]).
const REAP_HOOK: &str = "session-closed[91]";

fn gm(msg: String) -> UnitGenError {
    UnitGenError::GoldenMaster(msg)
}

fn req<'a>(t: &'a toml::Value, section: &str, key: &str) -> Result<&'a toml::Value, UnitGenError> {
    t.get(key).ok_or_else(|| gm(format!("[{section}].{key} is missing from SSOT")))
}

fn req_str(t: &toml::Value, section: &str, key: &str) -> Result<String, UnitGenError> {
    req(t, section, key)?
        .as_str()
        .filter(|v| !v.chars().any(char::is_control))
        .map(str::to_owned)
        .ok_or_else(|| gm(format!("[{section}].{key} must be a string without control characters")))
}

fn req_text(t: &toml::Value, section: &str, key: &str) -> Result<String, UnitGenError> {
    let v = req_str(t, section, key)?;
    if !plain_text(&v) {
        return Err(gm(format!(
            "[{section}].{key} {v:?}: shown text must be printable ASCII without quotes, #, braces, $ or backslash"
        )));
    }
    Ok(v)
}

fn req_int(t: &toml::Value, section: &str, key: &str, range: std::ops::RangeInclusive<i64>) -> Result<i64, UnitGenError> {
    req(t, section, key)?
        .as_integer()
        .filter(|v| range.contains(v))
        .ok_or_else(|| gm(format!("[{section}].{key} must be an integer in {}..={}", range.start(), range.end())))
}

fn percent(t: &toml::Value, section: &str, key: &str) -> Result<u32, UnitGenError> {
    let v = req_str(t, section, key)?;
    v.strip_suffix('%')
        .filter(|n| (1..=3).contains(&n.len()) && n.bytes().all(|b| b.is_ascii_digit()))
        .and_then(|n| n.parse::<u32>().ok())
        .filter(|n| (1..=100).contains(n))
        .ok_or_else(|| gm(format!("[{section}].{key} {v:?} must be a percentage 1%..100%")))
}

/// `platforms` (optional) must list only linux/windows; a missing list means every platform.
fn on_platform(t: &toml::Value, section: &str, platform: &str) -> Result<bool, UnitGenError> {
    match t.get("platforms") {
        None => Ok(true),
        Some(v) => {
            let list = v
                .as_array()
                .ok_or_else(|| gm(format!("[{section}].platforms must be an array")))?
                .iter()
                .map(|p| p.as_str().filter(|p| matches!(*p, "linux" | "windows")))
                .collect::<Option<Vec<_>>>()
                .ok_or_else(|| gm(format!("[{section}].platforms may list only linux and windows")))?;
            Ok(list.contains(&platform))
        }
    }
}

/// A title may reference plain formats such as `#{pane_index}`; everything else follows [`plain_text`].
fn valid_title(t: &str) -> bool {
    let mut rest = t.to_owned();
    while let Some(i) = rest.find("#{") {
        let Some(len) = rest[i + 2..].find('}') else { return false };
        let name = &rest[i + 2..i + 2 + len];
        if name.is_empty() || !name.bytes().all(|b| b.is_ascii_lowercase() || b == b'_') {
            return false;
        }
        rest.replace_range(i..i + 3 + len, "x");
    }
    plain_text(&rest)
}

/// Alias invocations must stay inside quoted strings: a `{ ... }` block is parsed when the file is parsed,
/// before any `set -s command-alias` has run, so a block naming an alias rejects the whole conf.
fn alias_in_block(command: &str) -> bool {
    let mut depth = 0usize;
    let mut quote: Option<char> = None;
    let mut word = String::new();
    for c in command.chars() {
        match (quote, c) {
            (Some(q), c) if c == q => quote = None,
            (Some(_), _) => {}
            (None, '\'' | '"') => quote = Some(c),
            (None, '{') => depth += 1,
            (None, '}') => depth = depth.saturating_sub(1),
            _ => {}
        }
        if c.is_ascii_alphanumeric() || c == '-' {
            word.push(c);
        } else {
            word.clear();
        }
        if depth > 0 && quote.is_none() && word.starts_with("mios-menu-") {
            return true;
        }
    }
    false
}

/// `C-c` (tmux) against `CTRL+c` (global contract): same modifier set, same key, case-insensitive modifiers.
pub fn same_chord(tmux_key: &str, global: &str) -> bool {
    fn tmux_parts(k: &str) -> (BTreeSet<&'static str>, String) {
        let mut mods = BTreeSet::new();
        let mut rest = k;
        loop {
            let m = match rest.get(..2) {
                Some("C-") => "CTRL",
                Some("M-") => "ALT",
                Some("S-") => "SHIFT",
                _ => break,
            };
            mods.insert(m);
            rest = &rest[2..];
        }
        (mods, rest.to_ascii_lowercase())
    }
    let (tm, tk) = tmux_parts(tmux_key);
    let mut parts: Vec<&str> = global.split('+').collect();
    let key = parts.pop().unwrap_or("").to_ascii_lowercase();
    let Some(gm) = parts
        .iter()
        .map(|m| match m.to_ascii_uppercase().as_str() {
            "CTRL" | "CONTROL" => Some("CTRL"),
            "ALT" | "META" => Some("ALT"),
            "SHIFT" => Some("SHIFT"),
            _ => None, // SUPER and friends never reach a terminal application as a tmux key
        })
        .collect::<Option<BTreeSet<&'static str>>>()
    else {
        return false;
    };
    let key = if key == "return" { "enter".to_owned() } else { key };
    tm == gm && tk == key
}

struct Item {
    label: String,
    key: String,
    target: ItemTarget,
    copy_only: bool,
    managed_disable: bool,
}

enum ItemTarget {
    Separator,
    Bound,
    Command(String),
    Menu(String),
}

struct Menu {
    id: String,
    title: String,
    start: Option<i64>,
    items: Vec<Item>,
    rows: i64,
}

struct Bind {
    key: String,
    command: String,
    note: String,
    repeat: bool,
    gate: Option<String>,
}

fn with_label(item: &Item) -> String {
    // Labels are formats; a comma inside a #{?...} branch must be escaped as #, (plain_text has no #).
    let esc = item.label.replace(',', "#,");
    let mut label = item.label.clone();
    if item.copy_only {
        label = format!("#{{?#{{m/r:(copy|view)-mode,#{{pane_mode}}}},{esc},-{esc}}}");
    }
    if item.managed_disable {
        let inner = if item.copy_only { label.clone() } else { esc.clone() };
        label = format!("#{{?{MANAGED_PANE},-{esc},{inner}}}");
    }
    label
}

/// Menus a root-table mouse binding opens get extra `-mouse` aliases (see the alias loop).
const MOUSE_MENUS: &[&str] = &["pane", "root"];

fn menu_opener(id: &str, rows: i64, too_small: &str) -> String {
    menu_opener_for(id, rows, too_small, "")
}

/// `suffix` is "" for key openers and "-mouse" for the right-click openers.
fn menu_opener_for(id: &str, rows: i64, too_small: &str, suffix: &str) -> String {
    format!(
        "if-shell -F '#{{e|<:#{{client_height}},{rows}}}' \"display-message '{too_small}'\" \"if-shell -F '#{{E:@mios-console-client}}' 'mios-menu-{id}-vt{suffix}' 'mios-menu-{id}{suffix}'\""
    )
}

fn helper(args: &str) -> String {
    format!("run-shell -b -c '#{{pane_current_path}}' \"TMUX_PANE=#{{pane_id}} {TERMINAL} {args}\"")
}

fn refuse_if_managed(command: &str, message: &str) -> String {
    format!("if-shell -F '{MANAGED_PANE}' \"display-message '{message}'\" {}", quote(command))
}

/// `[keybindings.agent]` dispatch: the focused CLI's adapter keys, else `fallback`.
fn accept_chain(agent: &toml::Value, fallback: String) -> Result<String, UnitGenError> {
    let mut command = fallback;
    for adapter in agent.get("adapters").and_then(toml::Value::as_array).into_iter().flatten().rev() {
        let kind = req_str(adapter, "keybindings.agent.adapters", "kind")?;
        let keys = adapter
            .get("accept_keys")
            .and_then(toml::Value::as_array)
            .map(|k| k.iter().filter_map(toml::Value::as_str).collect::<Vec<_>>().join(" "))
            .unwrap_or_default();
        if !kind.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_' || b == b'-')
            || keys.is_empty()
            || !keys.bytes().all(|b| b.is_ascii_alphanumeric() || b"- ".contains(&b))
        {
            return Err(gm(format!("[keybindings.agent].adapters invalid adapter: {kind}")));
        }
        command = format!(
            "if-shell -F '#{{m:*{kind}*,#{{pane_current_command}} #{{pane_title}}}}' 'send-keys {keys}' \"{command}\""
        );
    }
    Ok(command)
}

/// The tmux half of `render_keybindings`: everything after the prefix header in mios-keys.tmux.conf.
/// `actions` are the validated `[[keybindings.actions]]` rows as (key, tmux_command, label).
pub(crate) fn render_tmux_keys(
    doc: &toml::Value,
    config: &toml::Value,
    platform: &str,
    keys: &mut BTreeSet<String>,
    actions: &[(String, String, String)],
) -> Result<String, UnitGenError> {
    let linux = platform == "linux";
    let sec = "keybindings.tmux";
    let tmux = req(config, "keybindings", "tmux")?;
    let console = doc
        .get("theme")
        .and_then(|t| t.get("tmux"))
        .and_then(|t| t.get("console"))
        .ok_or_else(|| gm("[theme.tmux.console] is missing from SSOT".into()))?;
    let csec = "theme.tmux.console";
    let mut out = String::new();
    let mut add_key = |k: &str, what: &str| -> Result<(), UnitGenError> {
        if !valid_key(k) || !keys.insert(k.to_owned()) {
            return Err(gm(format!("[keybindings.tmux] {what}: duplicate or invalid key {k:?}")));
        }
        Ok(())
    };
    // Actions were validated (and their keys reserved) by the caller.
    let mut binds: Vec<Bind> = actions
        .iter()
        .map(|(key, command, label)| Bind { key: key.clone(), command: command.clone(), note: label.clone(), repeat: false, gate: None })
        .collect();
    for (_, command, label) in actions {
        if alias_in_block(command) {
            return Err(gm(format!("[keybindings.actions] {label}: menu alias inside a {{ }} block")));
        }
    }

    // ---- menus (structure first: openers need their row counts) ----
    let too_small = req_text(tmux, sec, "menu_too_small_message")?;
    let max_rows = req_int(tmux, sec, "menu_min_client_rows", 8..=200)? - 1; // one status line
    let managed_msg = req_text(tmux, sec, "managed_refusal_message")?;
    let menu_key = req_str(tmux, sec, "menu_key")?;
    let launcher_key = req_str(tmux, sec, "launcher_key")?;
    let float = tmux.get("float");
    let float_on = match float {
        Some(f) => req(f, "keybindings.tmux.float", "enabled")?.as_bool().ok_or_else(|| gm("[keybindings.tmux.float].enabled must be a boolean".into()))?,
        None => false,
    };
    let fsec = "keybindings.tmux.float";

    // Every prefix key this platform binds, before any command is rendered (menus drop items of unbound keys).
    let mut bound_keys: BTreeSet<String> = actions.iter().map(|a| a.0.clone()).collect();
    let rows_cfg = req(tmux, sec, "bindings")?
        .as_array()
        .ok_or_else(|| gm("[keybindings.tmux].bindings must be an array".into()))?;
    for row in rows_cfg {
        let key = req_str(row, "keybindings.tmux.bindings", "key")?;
        add_key(&key, "bindings")?;
        if on_platform(row, "keybindings.tmux.bindings", platform)? {
            bound_keys.insert(key);
        }
    }
    add_key(&menu_key, "menu_key")?;
    bound_keys.insert(menu_key.clone());
    add_key(&launcher_key, "launcher_key")?;
    if linux {
        bound_keys.insert(launcher_key.clone());
    }
    let (new_key, toggle_key, lift_key) = if let Some(f) = float {
        let (n, t, l) = (req_str(f, fsec, "new_key")?, req_str(f, fsec, "toggle_key")?, req_str(f, fsec, "lift_key")?);
        add_key(&n, "float.new_key")?;
        add_key(&t, "float.toggle_key")?;
        add_key(&l, "float.lift_key")?;
        if float_on {
            bound_keys.insert(n.clone());
            if linux {
                bound_keys.insert(t.clone());
                bound_keys.insert(l.clone());
            }
        }
        (n, t, l)
    } else {
        Default::default()
    };
    let agent = req(config, "keybindings", "agent")?;
    let accept_key = agent.get("accept_key").and_then(toml::Value::as_str).map(str::to_owned);
    if let Some(k) = &accept_key {
        add_key(k, "[keybindings.agent].accept_key")?;
        bound_keys.insert(k.clone());
    }

    let menus_cfg = req(tmux, sec, "menus")?
        .as_array()
        .ok_or_else(|| gm("[[keybindings.tmux.menus]] must be an array of tables".into()))?;
    let mut menus: Vec<Menu> = Vec::new();
    for m in menus_cfg {
        let msec = "keybindings.tmux.menus";
        let id = req_str(m, msec, "id")?;
        if id.is_empty() || !id.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-') || menus.iter().any(|x| x.id == id) {
            return Err(gm(format!("[[keybindings.tmux.menus]] duplicate or invalid id {id:?}")));
        }
        let title = req_str(m, msec, "title")?;
        if !valid_title(&title) {
            return Err(gm(format!("menu {id}: invalid title {title:?}")));
        }
        let mut items = Vec::new();
        let mut local = BTreeSet::new();
        for it in req(m, msec, "items")?.as_array().ok_or_else(|| gm(format!("menu {id}: items must be an array")))? {
            if it.get("separator").and_then(toml::Value::as_bool) == Some(true) {
                items.push(Item { label: String::new(), key: String::new(), target: ItemTarget::Separator, copy_only: false, managed_disable: false });
                continue;
            }
            if !on_platform(it, msec, platform)? {
                continue;
            }
            let label = req_text(it, msec, "label")?;
            if label.starts_with('-') {
                return Err(gm(format!("menu {id}: label {label:?} must not start with '-' (tmux disables such items)")));
            }
            let key = req_str(it, msec, "key")?;
            if !valid_key(&key) || !local.insert(key.clone()) {
                return Err(gm(format!("menu {id}: duplicate or invalid item key {key:?}")));
            }
            let target = match (it.get("command"), it.get("menu")) {
                (Some(_), Some(_)) => return Err(gm(format!("menu {id}: item {label:?} sets both command and menu"))),
                (Some(_), None) => {
                    let c = req_str(it, msec, "command")?;
                    if alias_in_block(&c) {
                        return Err(gm(format!("menu {id}: item {label:?}: menu alias inside a {{ }} block")));
                    }
                    ItemTarget::Command(c)
                }
                (None, Some(_)) => ItemTarget::Menu(req_str(it, msec, "menu")?),
                (None, None) if bound_keys.contains(&key) => ItemTarget::Bound,
                (None, None) => continue, // the key is not bound on this platform
            };
            let copy_only = match it.get("mode").and_then(toml::Value::as_str) {
                None => false,
                Some("copy") => true,
                Some(other) => return Err(gm(format!("menu {id}: item mode {other:?} must be copy"))),
            };
            let managed_disable = match it.get("managed").and_then(toml::Value::as_str) {
                None => false,
                Some("disable") => true,
                Some(other) => return Err(gm(format!("menu {id}: item managed {other:?} must be disable"))),
            };
            items.push(Item { label, key, target, copy_only, managed_disable });
        }
        // Separators never lead, trail or repeat once platform items are dropped.
        let mut clean: Vec<Item> = Vec::new();
        for item in items {
            let sep = matches!(item.target, ItemTarget::Separator);
            if sep && clean.last().is_none_or(|l| matches!(l.target, ItemTarget::Separator)) {
                continue;
            }
            clean.push(item);
        }
        if clean.last().is_some_and(|l| matches!(l.target, ItemTarget::Separator)) {
            clean.pop();
        }
        if clean.is_empty() {
            return Err(gm(format!("menu {id}: no items on {platform}")));
        }
        let start = match m.get("start") {
            None => None,
            Some(v) => Some(v.as_integer().filter(|n| (0..clean.len() as i64).contains(n)).ok_or_else(|| gm(format!("menu {id}: start must index an item")))?),
        };
        let rows = clean.len() as i64 + 2;
        if rows > max_rows {
            return Err(gm(format!(
                "menu {id}: {rows} rows (items, separators and borders) do not fit menu_min_client_rows - 1 = {max_rows}"
            )));
        }
        menus.push(Menu { id, title, start, items: clean, rows });
    }
    let menu_rows: BTreeMap<String, i64> = menus.iter().map(|m| (m.id.clone(), m.rows)).collect();
    let opener = |id: &str| -> Result<String, UnitGenError> {
        let rows = menu_rows.get(id).ok_or_else(|| gm(format!("menu {id:?} is not declared in [[keybindings.tmux.menus]]")))?;
        Ok(menu_opener(id, *rows, &too_small))
    };

    // ---- prefix bindings ----
    for row in rows_cfg {
        let bsec = "keybindings.tmux.bindings";
        if !on_platform(row, bsec, platform)? {
            continue;
        }
        let key = req_str(row, bsec, "key")?;
        let note = req_text(row, bsec, "note")?;
        let repeat = row.get("repeat").map(|v| v.as_bool().ok_or_else(|| gm(format!("bindings {key}: repeat must be a boolean")))).transpose()?.unwrap_or(false);
        let mut command = match (row.get("command"), row.get("menu")) {
            (Some(_), None) => {
                let c = req_str(row, bsec, "command")?;
                if c.is_empty() {
                    return Err(gm(format!("bindings {key}: empty command")));
                }
                if alias_in_block(&c) {
                    return Err(gm(format!("bindings {key}: menu alias inside a {{ }} block")));
                }
                c
            }
            (None, Some(_)) => opener(&req_str(row, bsec, "menu")?)?,
            _ => return Err(gm(format!("bindings {key}: set exactly one of command and menu"))),
        };
        match row.get("managed").and_then(toml::Value::as_str) {
            None => {}
            Some("refuse") => command = refuse_if_managed(&command, &managed_msg),
            Some(other) => return Err(gm(format!("bindings {key}: managed {other:?} must be refuse"))),
        }
        binds.push(Bind { key, command, note, repeat, gate: None });
    }
    binds.push(Bind { key: menu_key.clone(), command: opener("root")?, note: req_text(tmux, sec, "menu_note")?, repeat: false, gate: None });
    if linux {
        binds.push(Bind {
            key: launcher_key.clone(),
            command: helper("--menu apps --client #{q:client_name} --console #{?#{E:@mios-console-client},1,0} --rows #{client_height}"),
            note: req_text(tmux, sec, "launcher_note")?,
            repeat: false,
            gate: None,
        });
    }
    if let (Some(f), true) = (float, float_on) {
        let w = percent(f, fsec, "pane_width")?;
        let h = percent(f, fsec, "pane_height")?;
        percent(f, fsec, "shell_width")?;
        percent(f, fsec, "shell_height")?;
        let version = req_str(f, fsec, "min_version")?;
        if version.is_empty() || !version.bytes().all(|b| b.is_ascii_digit() || b == b'.') {
            return Err(gm(format!("[keybindings.tmux.float].min_version {version:?} must look like 3.7")));
        }
        let shell = helper("--float shell --client #{q:client_name} --console #{?#{E:@mios-console-client},1,0}");
        let native = format!("new-pane -c '#{{pane_current_path}}' -x {w}% -y {h}% -X {}% -Y {}%", (100 - w) / 2, (100 - h) / 2);
        binds.push(Bind {
            key: new_key,
            command: if linux { format!("if-shell -F '{MANAGED_PANE}' {} {}", quote(&shell), quote(&native)) } else { native },
            note: req_text(f, fsec, "new_note")?,
            repeat: false,
            gate: Some(version),
        });
        if linux {
            binds.push(Bind { key: toggle_key, command: shell, note: req_text(f, fsec, "toggle_note")?, repeat: false, gate: None });
            binds.push(Bind {
                key: lift_key,
                command: refuse_if_managed(&helper("--float lift --client #{q:client_name} --console #{?#{E:@mios-console-client},1,0}"), &managed_msg),
                note: req_text(f, fsec, "lift_note")?,
                repeat: false,
                gate: None,
            });
        }
    }
    if let Some(k) = &accept_key {
        let message = req_text(agent, "keybindings.agent", "unavailable_message")?;
        binds.push(Bind {
            key: k.clone(),
            command: accept_chain(agent, format!("display-message '{message}'"))?,
            note: req_text(agent, "keybindings.agent", "accept_note")?,
            repeat: false,
            gate: None,
        });
    }
    let commands: BTreeMap<String, String> = binds.iter().map(|b| (b.key.clone(), b.command.clone())).collect();

    out += &format!("set -g @mios-managed-pane '{MANAGED_PANE}'\n");

    // ---- extended keys (S-Enter and other modified keys reach tmux) ----
    let ext = req_str(tmux, sec, "extended_keys")?;
    let fmt = req_str(tmux, sec, "extended_keys_format")?;
    let idx = req_int(tmux, sec, "extended_features_index", 100..=1000)?;
    let slots = req_int(tmux, sec, "extended_feature_slots", 1..=32)?;
    if !matches!(ext.as_str(), "on" | "off" | "always") || !matches!(fmt.as_str(), "csi-u" | "xterm") {
        return Err(gm("[keybindings.tmux].extended_keys must be on|off|always and extended_keys_format csi-u|xterm".into()));
    }
    let terms = req(tmux, sec, "extended_key_terms")?
        .as_array()
        .ok_or_else(|| gm("[keybindings.tmux].extended_key_terms must be an array".into()))?
        .iter()
        .map(|t| t.as_str().filter(|t| valid_term_pattern(t)).map(str::to_owned))
        .collect::<Option<Vec<_>>>()
        .ok_or_else(|| gm("[keybindings.tmux].extended_key_terms: invalid TERM pattern".into()))?;
    if terms.len() as i64 > slots {
        return Err(gm(format!("[keybindings.tmux].extended_key_terms has {} terms for {slots} slots", terms.len())));
    }
    for i in 0..slots {
        out += &format!("set -su terminal-features[{}]\n", idx + i);
    }
    out += &format!("set -s extended-keys {ext}\n");
    if ext != "off" {
        out += &format!("set -s extended-keys-format {fmt}\n");
        for (i, t) in terms.iter().enumerate() {
            out += &format!("set -s terminal-features[{}] '{t}:extkeys'\n", idx + i as i64);
        }
    }

    // ---- menus as command aliases (every slot of the range is cleared first: reloads stay idempotent) ----
    let aidx = req_int(tmux, sec, "command_alias_index", 100..=10_000)?;
    let aslots = req_int(tmux, sec, "command_alias_slots", 2..=128)?;
    let mouse_on = crate::config_bool(config, "keybindings", "mouse")?;
    let mouse_ids: Vec<&str> = if mouse_on { MOUSE_MENUS.to_vec() } else { Vec::new() };
    let needed = menus.len() * 2 + mouse_ids.len() * 2 + 1;
    if needed as i64 > aslots {
        return Err(gm(format!("{} menus need {needed} command-alias slots; command_alias_slots = {aslots}", menus.len())));
    }
    for i in 0..aslots {
        out += &format!("set -su command-alias[{}]\n", aidx + i);
    }
    let console_style = [
        ("-b", req_str(console, csec, "border_lines")?),
        ("-s", req_str(console, csec, "menu_style")?),
        ("-S", req_str(console, csec, "menu_border_style")?),
        ("-H", req_str(console, csec, "menu_selected_style")?),
    ];
    if !matches!(console_style[0].1.as_str(), "single" | "double" | "heavy" | "simple" | "rounded" | "padded" | "none") {
        return Err(gm("[theme.tmux.console].border_lines is not a tmux border-lines value".into()));
    }
    for (_, style) in &console_style[1..] {
        if style.is_empty() || !style.bytes().all(|b| b.is_ascii_alphanumeric() || b"=,-".contains(&b)) {
            return Err(gm(format!("[theme.tmux.console] style {style:?} must be named tmux styles (no #rrggbb)")));
        }
    }
    let mut slot = aidx;
    for menu in &menus {
        let variants: &[(bool, bool)] =
            if mouse_ids.contains(&menu.id.as_str()) { &[(false, false), (true, false), (false, true), (true, true)] } else { &[(false, false), (true, false)] };
        for &(vt, mouse) in variants {
            // No -M: tmux 3.7c ignores -C for a menu that handles the mouse (man display-menu; probed: with -M the
            // menu opens with nothing selected, Enter chooses nothing and Up wraps to the last item, which on the
            // kill confirmation would be "Yes"). A right-click opens the menu from a mouse binding, so it still
            // takes clicks there; key-opened menus are driven by arrows, Enter and the shortcut keys.
            // The -mouse variants, opened by a right-click (or a touch long-press), add -O: the menu stays open when the
            // button is released and a click or tap chooses. -O, like -M, makes tmux ignore -C, so key openers never
            // use them.
            // A right-click menu opens with its top-left corner at the pointer (stock tmux -x M -y M): releasing
            // the button over an item would choose it, so a quick click or tap must land on the border instead.
            let (x, y) = if mouse { ("M", "M") } else { (MENU_X, MENU_Y) };
            let mut args: Vec<String> = vec!["display-menu".into(), "-T".into(), menu.title.clone(), "-x".into(), x.into(), "-y".into(), y.into()];
            if mouse {
                args.push("-O".into());
            } else {
                // Start on an item (the first unless the SSOT says otherwise): Enter alone then chooses, which the
                // controller vocabulary needs.
                args.extend(["-C".into(), menu.start.unwrap_or(0).to_string()]);
            }
            if vt {
                for (flag, value) in &console_style {
                    args.extend([flag.to_string(), value.clone()]);
                }
            }
            for item in &menu.items {
                let command = match &item.target {
                    ItemTarget::Separator => {
                        args.push(String::new());
                        continue;
                    }
                    ItemTarget::Bound => commands.get(&item.key).cloned().ok_or_else(|| gm(format!("menu {}: key {:?} is not bound", menu.id, item.key)))?,
                    ItemTarget::Command(c) => c.clone(),
                    ItemTarget::Menu(id) => {
                        let rows = menu_rows.get(id).ok_or_else(|| gm(format!("menu {}: submenu {id:?} is not declared", menu.id)))?;
                        let alias = if vt { format!("mios-menu-{id}-vt") } else { format!("mios-menu-{id}") };
                        format!("if-shell -F '#{{e|<:#{{client_height}},{rows}}}' \"display-message '{too_small}'\" '{alias}'")
                    }
                };
                args.extend([with_label(item), item.key.clone(), command]);
            }
            let name = format!("mios-menu-{}{}{}", menu.id, if vt { "-vt" } else { "" }, if mouse { "-mouse" } else { "" });
            let text = args.iter().map(|a| word(a)).collect::<Vec<_>>().join(" ");
            out += &format!("set -s command-alias[{slot}] {}\n", quote(&format!("{name}={text}")));
            slot += 1;
        }
    }

    // `mios-menu`: the entry point the controller's `tmux:menu` verb runs (mios-bindings-final FINAL.md 3.3):
    // the same size- and console-aware opener as prefix Space and the status-line right-click.
    out += &format!("set -s command-alias[{slot}] {}\n", quote(&format!("mios-menu={}", opener("root")?)));

    for b in &binds {
        if let Some(v) = &b.gate {
            out += &format!("%if \"#{{>=:#{{version}},{v}}}\"\n");
        }
        let repeat = if b.repeat { " -r" } else { "" };
        out += &format!("bind-key -N {}{repeat} {} {}\n", quote(&b.note), tmux_key(&b.key), b.command);
        if b.gate.is_some() {
            out += "%endif\n";
        }
    }

    // ---- root table: only the right-click menus and the agent accept chord ----
    out += "unbind-key -T root MouseDown3Pane\nunbind-key -T root MouseDown3Status\n";
    let mouse_opener = |id: &str| -> Result<String, UnitGenError> {
        let rows = menu_rows.get(id).ok_or_else(|| gm(format!("menu {id:?} is not declared in [[keybindings.tmux.menus]]")))?;
        Ok(menu_opener_for(id, *rows, &too_small, "-mouse"))
    };
    if mouse_on {
        // Resolve mouse_any_flag after selecting the clicked pane: the previously active pane would route
        // another pane's right-click to the wrong owner. Copy and view mode keep the menu (Go to top/bottom).
        out += &format!(
            "bind-key -N {} -T root MouseDown3Pane select-pane -t = \\; if-shell -F '{MOUSE_PASSTHROUGH}' 'send-keys -M' {}\n",
            quote(&req_text(tmux, sec, "pane_menu_note")?),
            quote(&mouse_opener("pane")?)
        );
        out += &format!(
            "bind-key -N {} -T root MouseDown3Status {}\n",
            quote(&req_text(tmux, sec, "menu_note")?),
            quote(&mouse_opener("root")?)
        );
    }
    // One accept chord for every agent CLI (mobile-safe, no Ctrl-K): tmux receives it (extended keys) and
    // sends the key the focused CLI's adapter expects; unlisted CLIs get the chord itself.
    if let Some(chord) = agent.get("accept_chord").and_then(toml::Value::as_str) {
        if chord.is_empty() || !chord.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-') {
            return Err(gm(format!("[keybindings.agent].accept_chord {chord:?} is not a tmux key name")));
        }
        let reserved = config
            .get("global")
            .and_then(|g| g.get("reserved_terminal"))
            .and_then(toml::Value::as_array)
            .into_iter()
            .flatten()
            .filter_map(toml::Value::as_str);
        let prefix = req_str(config, "keybindings", "tmux_prefix")?;
        for r in reserved {
            if chord != prefix && same_chord(chord, r) {
                return Err(gm(format!(
                    "root key {chord} shadows [keybindings.global].reserved_terminal chord {r:?} in every terminal"
                )));
            }
        }
        out += &format!("unbind-key -T root {chord}\nbind-key -n {chord} {}\n", accept_chain(agent, format!("send-keys {chord}"))?);
    }
    // Float shells die with their parent session, decided and done inside tmux: the hook spawns no process (no
    // per-event interpreter or shell). `if-shell -F` finds the float session of the closed one by name, and
    // `run-shell -C` runs kill-session as a tmux command with its target format-expanded (a plain kill-session
    // target is not). mios-terminal --float also reaps orphans opportunistically whenever a float opens.
    out += &format!("set-hook -gu {REAP_HOOK}\n");
    if linux && float_on {
        out += &format!("set-hook -g {REAP_HOOK} {}\n", quote(&float_reaper(&float_prefix(doc)?)));
    }
    Ok(out)
}

/// `[keybindings.tmux.float].session_prefix`, validated (it is pasted into tmux formats).
fn float_prefix(doc: &toml::Value) -> Result<String, UnitGenError> {
    let p = get(doc, "keybindings.tmux.float.session_prefix")
        .and_then(toml::Value::as_str)
        .ok_or_else(|| gm("[keybindings.tmux.float].session_prefix is missing from SSOT".into()))?;
    if !valid_prefix(p) {
        return Err(gm(format!("[keybindings.tmux.float].session_prefix: invalid prefix {p:?}")));
    }
    Ok(p.to_owned())
}

/// The session-closed hook body: kill `PREFIX<digits of the closed session id>` when it exists. Pure tmux
/// commands only (if-shell -F, run-shell -C), so closing sessions never starts a process.
pub fn float_reaper(prefix: &str) -> String {
    let name = format!("{prefix}#{{s/^.//:hook_session}}");
    format!("if-shell -F '#{{S:#{{?#{{==:#{{session_name}},{name}}},1,}}}}' \"run-shell -C 'kill-session -t ={name}'\"")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn doc(extra: &str) -> toml::Value {
        format!(
            "[terminal.sessions]\nhidden_session_prefixes = [\"mios-float-\"]\n[keybindings.tmux.float]\nsession_prefix = \"mios-float-\"\n[theme.tmux]\nremote_truecolor_terms = [\"linux\", \"vt*\"]\n[theme.tmux.console]\nterms = [\"linux*\", \"vt*\"]\n{extra}"
        )
        .parse()
        .unwrap()
    }

    #[test]
    fn hidden_prefixes_add_the_workspace_family_and_require_the_float_prefix() {
        assert_eq!(hidden_session_prefixes(&doc("")).unwrap(), ["mios-float-", "mios-workspace-"]);
        let mut d = doc("");
        d["terminal"]["sessions"]["hidden_session_prefixes"] = toml::Value::Array(vec![]);
        let err = hidden_session_prefixes(&d).unwrap_err();
        assert!(err.contains("must be listed"), "{err}");
        d["terminal"]["sessions"]["hidden_session_prefixes"] = toml::Value::Array(vec!["bad*".into()]);
        assert!(hidden_session_prefixes(&d).unwrap_err().contains("invalid prefix"));
    }

    #[test]
    fn visible_format_negates_every_prefix() {
        let f = visible_session_format(&["a-".into(), "b-".into()]);
        assert_eq!(f, "#{&&:#{!:#{m:a-*,#{session_name}}},#{!:#{m:b-*,#{session_name}}}}");
        assert!(!f.contains("#{!m:"));
        assert_eq!(visible_session_format(&[]), "1");
    }

    #[test]
    fn console_is_the_local_vt_not_an_upgraded_phone() {
        let d = doc("");
        assert!(console_client(&d, "linux", false).unwrap());
        assert!(console_client(&d, "vt220", false).unwrap());
        assert!(!console_client(&d, "xterm-256color", false).unwrap());
        // A phone over SSH sending "linux" is re-declared as remote_term by mios-terminal.
        assert!(!console_client(&d, "linux", true).unwrap());
        // A console TERM that is not upgraded stays a console over SSH.
        assert!(console_client(&d, "linux-16color", true).unwrap());
    }

    #[test]
    fn console_format_matches_each_term_and_non_utf8() {
        let f = console_client_format(&["linux*".into(), "vt*".into()]);
        assert_eq!(f, "#{||:#{m:linux*,#{client_termname}},#{||:#{m:vt*,#{client_termname}},#{!=:#{client_utf8},1}}}");
    }

    #[test]
    fn key_grammar_accepts_classes_and_rejects_others() {
        for k in ["a", "Z", "7", "*", "`", "{", "$", "Space", "Enter", "PPage", "BSpace"] {
            assert!(valid_key(k), "{k}");
        }
        for k in ["", "ab", "C-a", "'", "\"", "#", ";", "~", "F1", "é"] {
            assert!(!valid_key(k), "{k}");
        }
        assert_eq!(tmux_key("Space"), "Space");
        assert_eq!(tmux_key("{"), "'{'");
    }

    #[test]
    fn quote_escapes_tmux_double_quote_specials() {
        assert_eq!(quote(r#"a "b" $c \d"#), r#""a \"b\" \$c \\d""#);
        assert!(plain_text("Kill pane..."));
        for bad in ["it's", "#{x}", "a$b", "x\"y", "", "tab\there"] {
            assert!(!plain_text(bad), "{bad}");
        }
    }

    const SSOT: &str = include_str!("../../../../usr/share/mios/mios.toml");
    const KEYS: &str = "usr/share/mios/tmux/mios-keys.tmux.conf";

    fn ssot() -> toml::Value {
        toml::from_str(SSOT).unwrap()
    }

    fn render(d: &toml::Value) -> Result<String, String> {
        crate::render_keybindings(&toml::to_string(d).unwrap())
            .map(|m| m[KEYS].clone())
            .map_err(|e| e.to_string())
    }

    fn render_for(d: &toml::Value, platform: &str) -> String {
        crate::render_keybindings_for(&toml::to_string(d).unwrap(), platform).unwrap()[KEYS].clone()
    }

    fn row(key: &str, command: &str) -> toml::Value {
        let mut t = toml::map::Map::new();
        t.insert("key".into(), key.into());
        t.insert("command".into(), command.into());
        t.insert("note".into(), "Test".into());
        toml::Value::Table(t)
    }

    fn push_row(d: &mut toml::Value, r: toml::Value) {
        d["keybindings"]["tmux"]["bindings"].as_array_mut().unwrap().push(r);
    }

    #[test]
    fn every_prefix_key_carries_a_note_and_the_ssot_set_is_bound() {
        let conf = render(&ssot()).unwrap();
        // The prefix itself (pressed twice sends it) is the only unannotated prefix-table key.
        for line in conf.lines().filter(|l| l.starts_with("bind-key") && !l.contains("-T root") && !l.starts_with("bind-key -n") && *l != "bind-key C-b send-prefix") {
            assert!(line.starts_with("bind-key -N \""), "{line}");
        }
        for key in ["Space", "e", "'*'", "'`'", "F", "Enter", "PPage", "NPage", "'?'", "':'", "x", "'&'", "0", "9", "H"] {
            assert!(conf.lines().any(|l| l.starts_with("bind-key -N") && l.split_whitespace().any(|w| w == key)), "missing {key}");
        }
        assert!(conf.contains("bind-key -N \"Resize the pane left by 5\" -r H resize-pane -L 5"));
    }

    #[test]
    fn duplicate_keys_across_tables_fail() {
        for key in ["e", "Space", "*", "Enter", "a"] {
            let mut d = ssot();
            push_row(&mut d, row(key, "display-message dup"));
            let err = render(&d).unwrap_err();
            assert!(err.contains("duplicate"), "{key}: {err}");
        }
        let mut d = ssot();
        d["keybindings"]["tmux"]["float"]["lift_key"] = "e".into();
        assert!(render(&d).unwrap_err().contains("duplicate"));
    }

    #[test]
    fn invalid_keys_and_unsafe_text_fail() {
        let mut d = ssot();
        push_row(&mut d, row("~", "display-message x"));
        assert!(render(&d).unwrap_err().contains("invalid key"));
        let mut d = ssot();
        let mut r = row("Z", "display-message x");
        r["note"] = "it's".into();
        push_row(&mut d, r);
        assert!(render(&d).unwrap_err().contains("printable ASCII"));
        let mut d = ssot();
        d["keybindings"]["tmux"]["menus"][0]["items"][0]["label"] = "#{evil}".into();
        assert!(render(&d).is_err());
    }

    #[test]
    fn alias_inside_a_block_is_rejected_but_quoted_alias_is_fine() {
        let mut d = ssot();
        push_row(&mut d, row("Z", "if-shell -F 1 { mios-menu-root } { display-message no }"));
        assert!(render(&d).unwrap_err().contains("alias inside"));
        let mut d = ssot();
        push_row(&mut d, row("Z", "if-shell -F 1 'mios-menu-root' 'display-message no'"));
        assert!(render(&d).is_ok());
        assert!(!alias_in_block("command-prompt -I '#W' { rename-window -- '%%' }"));
    }

    #[test]
    fn root_accept_chord_may_not_shadow_a_reserved_terminal_chord() {
        let mut d = ssot();
        d["keybindings"]["agent"]["accept_chord"] = "C-l".into();
        assert!(render(&d).unwrap_err().contains("reserved_terminal"));
        assert!(same_chord("C-c", "CTRL+c") && same_chord("S-Enter", "SHIFT+Return"));
        // FINAL.md adds CTRL+b (the prefix) to reserved_terminal: the tier-3 owner's own prefix stays legal.
        let mut d = ssot();
        d["keybindings"]["global"]["reserved_terminal"].as_array_mut().unwrap().extend(["CTRL+b".into(), "CTRL+s".into()]);
        assert!(render(&d).is_ok());
        d["keybindings"]["agent"]["accept_chord"] = "C-s".into();
        assert!(render(&d).unwrap_err().contains("reserved_terminal"));
        assert!(!same_chord("c", "SUPER+c") && !same_chord("C-c", "CTRL+ALT+c"));
    }

    #[test]
    fn extended_keys_come_from_the_ssot_and_off_clears_them() {
        let conf = render(&ssot()).unwrap();
        assert!(conf.contains("set -s extended-keys on\nset -s extended-keys-format csi-u\n"));
        assert!(conf.contains("set -su terminal-features[127]\n"));
        assert!(conf.contains("set -s terminal-features[123] 'alacritty*:extkeys'"));
        let mut d = ssot();
        d["keybindings"]["tmux"]["extended_keys"] = "off".into();
        let off = render(&d).unwrap();
        assert!(off.contains("set -s extended-keys off") && !off.contains(":extkeys") && off.contains("set -su terminal-features[120]"));
    }

    #[test]
    fn prefix_enter_accepts_with_the_unavailable_fallback() {
        let conf = render(&ssot()).unwrap();
        let line = conf.lines().find(|l| l.starts_with("bind-key -N") && l.contains(" Enter if-shell")).unwrap();
        assert!(line.contains("'send-keys C-k'") && line.contains("display-message 'No pending-action key adapter for this pane'"), "{line}");
        assert!(conf.contains("bind-key -n S-Enter if-shell"));
    }

    #[test]
    fn each_menu_has_a_console_variant_whose_submenus_stay_console() {
        let conf = render(&ssot()).unwrap();
        let aliases: Vec<&str> = conf.lines().filter(|l| l.starts_with("set -s command-alias[")).collect();
        assert_eq!(aliases.len(), 2 * ssot()["keybindings"]["tmux"]["menus"].as_array().unwrap().len() + 2 * MOUSE_MENUS.len() + 1);
        // Right-click variants stay open on release (-O) and carry no -C; key variants start on an item.
        let mouse = aliases.iter().find(|l| l.contains("\"mios-menu-pane-mouse=")).unwrap();
        assert!(mouse.contains(" -x M -y M -O ") && !mouse.contains(" -C "), "{mouse}");
        assert!(aliases.iter().any(|l| l.contains("\"mios-menu-root-vt-mouse=") && l.contains("-b single")));
        assert!(conf.lines().any(|l| l.contains("-T root MouseDown3Pane") && l.contains("'mios-menu-pane-mouse'")));
        assert!(conf.lines().any(|l| l.contains("-T root MouseDown3Status") && l.contains("'mios-menu-root-vt-mouse'")));
        assert!(aliases.iter().any(|l| l.contains("\"mios-menu=if-shell -F ")), "controller tmux:menu entry point");
        let vt = aliases.iter().find(|l| l.contains("\"mios-menu-root-vt=")).unwrap();
        assert!(vt.contains("-b single") && vt.contains("mios-menu-pane-vt") && !vt.contains("'mios-menu-pane'"));
        assert!(!vt.contains("#2") && !vt.contains("#F"), "console variant must not carry hex colours");
        let normal = aliases.iter().find(|l| l.contains("\"mios-menu-root=")).unwrap();
        assert!(normal.contains("'mios-menu-pane'") && !normal.contains("-b single"));
        assert!(normal.contains(" -C 0 "), "menus start on their first item: {normal}");
        assert!(!normal.contains(" -M "), "-M would discard -C (no starting item for the controller): {normal}");
        assert!(aliases.iter().any(|l| l.contains("\"mios-menu-kill-pane=") && l.contains(" -C 1 ")), "kill confirmation starts on No");
        // Reloads clear the whole alias range first.
        assert_eq!(conf.matches("set -su command-alias[").count(), 32);
    }

    #[test]
    fn menu_rows_are_enforced_against_the_smallest_client() {
        let mut d = ssot();
        d["keybindings"]["tmux"]["menu_min_client_rows"] = 12.into();
        assert!(render(&d).unwrap_err().contains("do not fit"));
    }

    #[test]
    fn bound_items_take_the_platform_command_and_linux_helpers_stay_off_windows() {
        let conf = render(&ssot()).unwrap();
        let pane = conf.lines().find(|l| l.contains("\"mios-menu-pane=")).unwrap();
        assert!(pane.contains("--workspace-focus next"), "{pane}");
        let mut d = ssot();
        for b in d["keybindings"]["tmux"]["bindings"].as_array_mut().unwrap() {
            if let Some(w) = b.get("command_windows").cloned() {
                b["command"] = w;
            }
        }
        for a in d["keybindings"]["actions"].as_array_mut().unwrap() {
            a["tmux_command"] = a["tmux_command_windows"].clone();
        }
        let win = render_for(&d, "windows");
        assert!(!win.contains("mios-terminal"), "windows render carries a Linux helper");
        assert!(win.contains("select-pane -t :.+") && win.contains("\"mios-menu-session="));
        assert!(!win.contains("New bare Tmux-OS session"));
    }

    #[test]
    fn floats_are_version_gated_and_managed_panes_refuse_layout_keys() {
        let conf = render(&ssot()).unwrap();
        assert!(conf.contains("%if \"#{>=:#{version},3.7}\"\nbind-key -N \"New floating pane (keeps the directory)\" '*' if-shell -F '#{||:#{@mios-workspace-head}"));
        assert!(conf.contains("new-pane -c '#{pane_current_path}' -x 70% -y 60% -X 15% -Y 20%"));
        for key in ["'!'", "'{'", "'}'", "F"] {
            let needle = format!(" {key} if-shell -F '#{{||:#{{@mios-workspace-head}}");
            let line = conf.lines().find(|l| l.starts_with("bind-key -N") && l.contains(&needle)).unwrap_or_else(|| panic!("{key} not managed-guarded"));
            assert!(line.contains("display-message 'Managed AI workspace pane"), "{line}");
        }
        let mut d = ssot();
        d["keybindings"]["tmux"]["float"]["enabled"] = false.into();
        let off = render(&d).unwrap();
        assert!(!off.contains("new-pane") && !off.contains("--float") && off.contains("set-hook -gu session-closed[91]"));
    }

    #[test]
    fn every_generated_run_shell_names_its_pane() {
        let conf = render(&ssot()).unwrap();
        let mut n = 0;
        for (i, _) in conf.match_indices("run-shell") {
            let tail = &conf[i..conf[i..].find('\n').map_or(conf.len(), |e| i + e)];
            // `run-shell -C` runs a tmux command inside the server: no job, no TMUX_PANE to inherit.
            if tail.starts_with("run-shell -C ") {
                continue;
            }
            n += 1;
            assert!(tail.contains("TMUX_PANE=#{pane_id}"), "{tail}");
        }
        assert!(n > 10);
    }

    #[test]
    fn float_reaper_hook_spawns_no_process() {
        let conf = render(&ssot()).unwrap();
        let hook = conf.lines().find(|l| l.starts_with("set-hook -g session-closed[91] ")).expect("reaper hook");
        assert!(hook.contains("run-shell -C 'kill-session -t =mios-float-#{s/^.//:hook_session}'"), "{hook}");
        // Negative control: no shell job, interpreter or MiOS helper runs per session close.
        for bad in ["run-shell -b", "mios-terminal", "python", "TMUX_PANE"] {
            assert!(!hook.contains(bad), "{bad} in {hook}");
        }
        assert!(float_reaper("p-").starts_with("if-shell -F '#{S:#{?#{==:#{session_name},p-#{s/^.//:hook_session}},1,}}'"));
        let mut d = ssot();
        d["keybindings"]["tmux"]["float"]["session_prefix"] = "bad*".into();
        assert!(render(&d).is_err());
    }

    #[test]
    fn blink_layer_is_a_generated_shim() {
        let all = crate::render_keybindings(SSOT).unwrap();
        let blink = &all["usr/share/mios/tmux/blink-mobile-keys.tmux.conf"];
        assert!(blink.starts_with("# AI-hint: Generated from mios.toml") && blink.ends_with("source-file /etc/tmux.conf\n"));
    }

    #[test]
    fn term_glob_follows_the_fnmatch_subset() {
        assert!(term_glob("linux*", "linux-16color"));
        assert!(term_glob("vt?00", "vt100"));
        assert!(!term_glob("vt*", "xterm"));
    }
}
