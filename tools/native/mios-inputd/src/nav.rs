// AI-hint: Controller navigation grammar (steps I2, I3): [keybindings.global.controller.nav] profiles with inherits, [tap, hold] values, the repeat table, flick thresholds, the key:/pointer:/tmux: projections and their validation, the console (VT) profile rules and the Linux console byte translation of a key: stroke.
// AI-related: usr/share/mios/mios.toml [keybindings.global.controller.nav], tools/native/mios-inputd/src/router.rs, docs/design/mios-global-bindings-final.md (section 3)
// AI-functions: NavConfig::from_value, NavConfig::profile_name, NavConfig::text, parse_key_chord, stroke, keysym, vt_bytes, validate_tmux
use serde::Serialize;
use std::collections::{BTreeMap, BTreeSet};

/// One nav input: a controller button (BTN_* / BTN_DPAD_*), a stick direction (LS_* / RS_*), or the toggle chord.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize)]
pub enum Input {
    Button(u16),
    Stick { right: bool, dir: Dir },
    ToggleChord,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord, Serialize)]
pub enum Dir {
    Up,
    Down,
    Left,
    Right,
}
impl Dir {
    pub fn word(self) -> &'static str {
        match self {
            Dir::Up => "up",
            Dir::Down => "down",
            Dir::Left => "left",
            Dir::Right => "right",
        }
    }
    fn parse(s: &str) -> Option<Self> {
        Some(match s {
            "UP" | "up" => Dir::Up,
            "DOWN" | "down" => Dir::Down,
            "LEFT" | "left" => Dir::Left,
            "RIGHT" | "right" => Dir::Right,
            _ => return None,
        })
    }
}
impl Input {
    pub fn parse(name: &str) -> Result<Self, String> {
        if name == "TOGGLE_CHORD" {
            return Ok(Input::ToggleChord);
        }
        for (prefix, right) in [("LS_", false), ("RS_", true)] {
            if let Some(d) = name.strip_prefix(prefix) {
                return Dir::parse(d).map(|dir| Input::Stick { right, dir }).ok_or_else(|| format!("unknown stick direction {name}"));
            }
        }
        crate::config::button_code(name).map(Input::Button)
    }
    pub fn name(self) -> String {
        match self {
            Input::ToggleChord => "TOGGLE_CHORD".into(),
            Input::Stick { right, dir } => format!("{}_{}", if right { "RS" } else { "LS" }, dir.word().to_ascii_uppercase()),
            Input::Button(c) => BUTTON_NAMES.iter().find(|(_, v)| *v == c).map_or_else(|| format!("0x{c:x}"), |(n, _)| (*n).into()),
        }
    }
}
const BUTTON_NAMES: [(&str, u16); 17] = [
    ("BTN_SOUTH", 0x130), ("BTN_EAST", 0x131), ("BTN_NORTH", 0x133), ("BTN_WEST", 0x134), ("BTN_TL", 0x136),
    ("BTN_TR", 0x137), ("BTN_TL2", 0x138), ("BTN_TR2", 0x139), ("BTN_SELECT", 0x13a), ("BTN_START", 0x13b),
    ("BTN_MODE", 0x13c), ("BTN_THUMBL", 0x13d), ("BTN_THUMBR", 0x13e), ("BTN_DPAD_UP", 0x220),
    ("BTN_DPAD_DOWN", 0x221), ("BTN_DPAD_LEFT", 0x222), ("BTN_DPAD_RIGHT", 0x223),
];
pub const BTN_DPAD_UP: u16 = 0x220;
pub const BTN_DPAD_DOWN: u16 = 0x221;
pub const BTN_DPAD_LEFT: u16 = 0x222;
pub const BTN_DPAD_RIGHT: u16 = 0x223;

/// A nav value: a string fires on press (it repeats when the input has a [nav.repeat] entry); `[tap, hold]` resolves
/// on release before capture_hold_ms or once at it. "none" is a real value that does nothing.
#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub enum Value {
    Press(String),
    TapHold(String, String),
}
impl Value {
    fn parse(v: &toml::Value, what: &str) -> Result<Self, String> {
        match v {
            toml::Value::String(s) => Ok(Value::Press(s.clone())),
            toml::Value::Array(a) if a.len() == 2 => {
                let s = |i: usize| a[i].as_str().map(str::to_owned).ok_or_else(|| format!("{what}: [tap, hold] takes two strings"));
                Ok(Value::TapHold(s(0)?, s(1)?))
            }
            _ => Err(format!("{what}: a nav value is \"ns:action\", \"none\" or [\"tap\", \"hold\"]")),
        }
    }
    pub fn actions(&self) -> Vec<&str> {
        match self {
            Value::Press(a) => vec![a],
            Value::TapHold(t, h) => vec![t, h],
        }
    }
}
pub type Profile = BTreeMap<Input, Value>;

/// The resolved nav projection. `profiles` already has every `inherits` chain applied.
#[derive(Clone, Debug, Default, Serialize)]
pub struct NavConfig {
    pub enabled: bool,
    pub flick_on: f64,
    pub flick_off: f64,
    /// Context -> profile name, or "vpad" (forward the pad to the virtual gamepad, the behaviour before nav).
    pub contexts: BTreeMap<String, String>,
    pub repeat: BTreeMap<Input, (u64, u64)>,
    /// Sockets (tmux -L names) whose clients count as a MiOS tmux client for the `<name>_tmux` profile.
    pub tmux_sockets: Vec<String>,
    pub profiles: BTreeMap<String, Profile>,
}
const RESERVED: [&str; 6] = ["enabled", "flick_on", "flick_off", "contexts", "repeat", "tmux_sockets"];
/// Inputs the ownership contract or the room owns wherever a compositor runs; a desktop profile may not claim them.
const CONTRACT: [&str; 5] = ["BTN_NORTH", "BTN_TL", "BTN_TR", "BTN_START", "TOGGLE_CHORD"];

/// What validation needs from the rest of the SSOT.
pub struct Ctx<'a> {
    pub namespaces: &'a [String],
    pub tmux_prefix_keys: &'a BTreeSet<String>,
    pub contexts: &'a [&'a str],
    pub deadzone: f64,
}

impl NavConfig {
    pub fn from_value(v: Option<&toml::Value>, ctx: &Ctx) -> Result<Self, String> {
        let Some(v) = v else { return Ok(Self::default()) };
        let t = v.as_table().ok_or("[keybindings.global.controller.nav] must be a table")?;
        let num = |k: &str, d: f64| -> Result<f64, String> {
            match t.get(k) {
                None => Ok(d),
                Some(toml::Value::Float(f)) => Ok(*f),
                Some(toml::Value::Integer(i)) => Ok(*i as f64),
                _ => Err(format!("nav.{k} must be a number")),
            }
        };
        let mut cfg = NavConfig {
            enabled: t.get("enabled").map_or(Ok(false), |v| v.as_bool().ok_or("nav.enabled must be a boolean"))?,
            flick_on: num("flick_on", 0.6)?,
            flick_off: num("flick_off", 0.3)?,
            ..Self::default()
        };
        if !(cfg.flick_on.is_finite() && cfg.flick_off.is_finite() && cfg.flick_off >= 0.0 && cfg.flick_off < cfg.flick_on && cfg.flick_on <= 1.0) {
            return Err("nav: 0 <= flick_off < flick_on <= 1".into());
        }
        if let Some(c) = t.get("contexts") {
            for (context, profile) in c.as_table().ok_or("nav.contexts must be a table")? {
                let p = profile.as_str().ok_or_else(|| format!("nav.contexts.{context} must name a profile"))?;
                if !ctx.contexts.contains(&context.as_str()) || ["app-capture", "desktop-3d", "controller"].contains(&context.as_str()) {
                    return Err(format!("nav.contexts: {context} is not an uncaptured window context"));
                }
                cfg.contexts.insert(context.clone(), p.into());
            }
        }
        if let Some(r) = t.get("repeat") {
            for (input, pair) in r.as_table().ok_or("nav.repeat must be a table")? {
                let i = Input::parse(input)?;
                if matches!(i, Input::Stick { right: true, .. }) || matches!(i, Input::ToggleChord) {
                    return Err(format!("nav.repeat.{input}: right-stick flicks and the toggle chord never repeat"));
                }
                let a = pair.as_array().filter(|a| a.len() == 2).ok_or_else(|| format!("nav.repeat.{input} is [delay_ms, rate_ms]"))?;
                let n = |i: usize| a[i].as_integer().filter(|v| *v > 0).map(|v| v as u64).ok_or_else(|| format!("nav.repeat.{input}: positive integers"));
                let (delay, rate) = (n(0)?, n(1)?);
                if !(50..=5000).contains(&delay) || !(16..=5000).contains(&rate) {
                    return Err(format!("nav.repeat.{input}: delay 50..=5000 ms, rate 16..=5000 ms"));
                }
                cfg.repeat.insert(i, (delay, rate));
            }
        }
        if let Some(s) = t.get("tmux_sockets") {
            for name in s.as_array().ok_or("nav.tmux_sockets must be an array")? {
                let n = name.as_str().filter(|n| !n.is_empty() && n.len() <= 64 && n.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'-' || b == b'_'))
                    .ok_or("nav.tmux_sockets: socket names are [A-Za-z0-9_-]{1,64}")?;
                cfg.tmux_sockets.push(n.into());
            }
        }
        let mut raw: BTreeMap<String, (Option<String>, Profile)> = BTreeMap::new();
        for (name, body) in t {
            if RESERVED.contains(&name.as_str()) {
                continue;
            }
            if name == "vpad" || name.is_empty() || !name.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'_') {
                return Err(format!("nav profile name {name:?} must be [a-z0-9_] and not \"vpad\""));
            }
            let body = body.as_table().ok_or_else(|| format!("nav.{name} must be a table"))?;
            let mut inherits = None;
            let mut map = Profile::new();
            for (input, value) in body {
                if input == "inherits" {
                    inherits = Some(value.as_str().ok_or_else(|| format!("nav.{name}.inherits names a profile"))?.to_owned());
                    continue;
                }
                let what = format!("nav.{name}.{input}");
                let i = Input::parse(input).map_err(|e| format!("{what}: {e}"))?;
                let v = Value::parse(value, &what)?;
                for a in v.actions() {
                    validate_value(a, i, ctx).map_err(|e| format!("{what}: {e}"))?;
                }
                if i == Input::Button(0x13c) && matches!(v, Value::TapHold(..)) {
                    return Err(format!("{what}: Guide hold is the system layer modifier; BTN_MODE takes one tap action"));
                }
                if matches!(i, Input::Stick { .. }) && matches!(v, Value::TapHold(..)) {
                    return Err(format!("{what}: a stick direction takes one action (a flick has no hold)"));
                }
                if matches!(v, Value::TapHold(..)) && v.actions().iter().any(|a| a.starts_with("pointer:") && *a != CURSOR) {
                    return Err(format!("{what}: pointer: is analog and cannot be a tap or hold"));
                }
                if i == Input::ToggleChord && matches!(v, Value::TapHold(..)) {
                    return Err(format!("{what}: the toggle chord takes one action (its hold is the capture release)"));
                }
                map.insert(i, v);
            }
            raw.insert(name.clone(), (inherits, map));
        }
        for name in raw.keys() {
            let mut chain = vec![name.clone()];
            let mut merged = Profile::new();
            let mut cur = name.clone();
            loop {
                let (parent, map) = &raw[&cur];
                for (k, v) in map {
                    merged.entry(*k).or_insert_with(|| v.clone());
                }
                match parent {
                    None => break,
                    Some(p) if !raw.contains_key(p) => return Err(format!("nav.{cur}.inherits: unknown profile {p}")),
                    Some(p) if chain.contains(p) || chain.len() > 8 => return Err(format!("nav.{name}: inherits cycle through {p}")),
                    Some(p) => {
                        chain.push(p.clone());
                        cur = p.clone();
                    }
                }
            }
            cfg.profiles.insert(name.clone(), merged);
        }
        for (context, p) in &cfg.contexts {
            if p == "vpad" {
                if context == "console" {
                    return Err("nav.contexts.console: a VT has no virtual-pad consumer; name a profile".into());
                }
                continue;
            }
            let names = cfg.candidates(p);
            if names.is_empty() {
                return Err(format!("nav.contexts.{context}: no profile {p} (or {p}_tmux and {p}_plain)"));
            }
            for n in names {
                for (input, value) in &cfg.profiles[&n] {
                    let name = input.name();
                    if context == "console" {
                        // I3 (C-23): no compositor, shell or room on a VT: only projections that reach a VT.
                        for a in value.actions() {
                            if !console_capable(a) {
                                return Err(format!("nav.{n}.{name}: {a} cannot reach a VT (console rows use key:, tmux:, mios:graphical-vt, pointer:cursor or none)"));
                            }
                        }
                    } else if CONTRACT.contains(&name.as_str()) && value != &Value::Press("none".into()) {
                        return Err(format!("nav.{n}.{name}: owned by the capture contract or the room wherever a compositor runs ({context})"));
                    }
                }
            }
        }
        let _ = ctx.deadzone;
        Ok(cfg)
    }
    /// The profiles a context name can select: the profile itself, or its `_tmux` / `_plain` pair.
    fn candidates(&self, p: &str) -> Vec<String> {
        if self.profiles.contains_key(p) {
            return vec![p.into()];
        }
        let (t, plain) = (format!("{p}_tmux"), format!("{p}_plain"));
        if self.profiles.contains_key(&t) && self.profiles.contains_key(&plain) {
            vec![t, plain]
        } else {
            vec![]
        }
    }
    /// The profile for an uncaptured context: None = no nav (vpad forwarding or an unlisted context).
    pub fn profile_name(&self, context: &str, tmux: bool) -> Option<String> {
        if !self.enabled {
            return None;
        }
        let p = self.contexts.get(context)?;
        if p == "vpad" {
            return None;
        }
        if self.profiles.contains_key(p) {
            return Some(p.clone());
        }
        Some(format!("{p}_{}", if tmux { "tmux" } else { "plain" }))
    }
    /// Every action a nav profile can fire, for the bridge's unresolved-action check.
    pub fn actions(&self) -> BTreeSet<String> {
        self.profiles.values().flat_map(|p| p.values().flat_map(|v| v.actions().into_iter().map(str::to_owned))).collect()
    }
    /// The resolved controller map as text (`mios-inputd map --text`, the tmux:keymap popup): context -> profile, then
    /// each profile's inputs with their tap/hold actions.
    pub fn text(&self, hold_ms: u64) -> String {
        let mut out = format!("MiOS controller map (uncaptured). A hold is {hold_ms} ms; North captures the focused window, a held L3+R3 releases it.\n\n");
        for (context, p) in &self.contexts {
            out += &format!("  {context:<16} {}\n", if self.profiles.contains_key(p) || p == "vpad" { p.clone() } else { format!("{p}_tmux (tmux client) / {p}_plain") });
        }
        for (name, profile) in &self.profiles {
            out += &format!("\n[{name}]\n");
            for (input, value) in profile {
                let v = match value {
                    Value::Press(a) => a.clone(),
                    Value::TapHold(t, h) => format!("tap {t}  hold {h}"),
                };
                out += &format!("  {:<15} {v}\n", input.name());
            }
        }
        out
    }
}
/// I3: the nav value that toggles cursor mode (the sticks drive the virtual pointer, RT/LT click). A button value only.
pub const CURSOR: &str = "pointer:cursor";
/// The projections a VT can receive: the uinput keyboard (the kernel console reads it), tmux verbs for the client on
/// the active VT, the graphical-VT switch, cursor mode (the virtual pointer moves the gpm pointer) and "none".
pub fn console_capable(action: &str) -> bool {
    action == "none" || action == "mios:graphical-vt" || action == CURSOR || action.starts_with("key:") || action.starts_with("tmux:")
}

fn validate_value(action: &str, input: Input, ctx: &Ctx) -> Result<(), String> {
    if action.is_empty() {
        return Err("\"\" is not a value: use \"none\" to unbind".into());
    }
    if action == "none" {
        return Ok(());
    }
    crate::config::validate_action_in(action, ctx.namespaces)?;
    let (ns, verb) = action.split_once(':').unwrap();
    match ns {
        "key" => {
            parse_key_chord(verb)?;
        }
        "pointer" if action == CURSOR => {
            if !matches!(input, Input::Button(_)) {
                return Err("pointer:cursor toggles cursor mode; only a button can fire it".into());
            }
        }
        "pointer" => {
            if !["wheel-up", "wheel-down", "hwheel-left", "hwheel-right"].contains(&verb) {
                return Err(format!("unknown pointer verb {verb} (wheel-up, wheel-down, hwheel-left, hwheel-right, cursor)"));
            }
            if !matches!(input, Input::Stick { .. }) {
                return Err("pointer: is the analog wheel; only LS_*/RS_* can drive it".into());
            }
        }
        "tmux" => validate_tmux(verb, ctx.tmux_prefix_keys)?,
        _ => {}
    }
    Ok(())
}
pub fn validate_tmux(verb: &str, prefix_keys: &BTreeSet<String>) -> Result<(), String> {
    let words: Vec<_> = verb.split_whitespace().collect();
    match words.as_slice() {
        ["key", k] if prefix_keys.contains(*k) => Ok(()),
        ["key", k] => Err(format!("tmux:key {k}: not a prefix-table key the SSOT binds ({})", prefix_keys.iter().cloned().collect::<Vec<_>>().join(" "))),
        ["focus", d] if Dir::parse(d).is_some() && d.chars().all(|c| c.is_ascii_lowercase()) => {
            for k in ["h", "j", "k", "l"] {
                if !prefix_keys.contains(k) {
                    return Err(format!("tmux:focus needs the SSOT prefix key {k} (select-pane)"));
                }
            }
            Ok(())
        }
        ["page-up"] | ["page-down"] | ["session-prev"] | ["session-next"] | ["menu"] | ["keymap"] => Ok(()),
        _ => Err(format!("unknown tmux verb {verb} (key <k>, focus <dir>, page-up, page-down, session-prev, session-next, menu, keymap)")),
    }
}

/// One `key:` chord, pinned to the US layout: modifiers then one key.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct KeyChord {
    pub mods: Vec<u16>,
    pub key: u16,
}
const MODS: [(&str, u16, &str); 4] = [("CTRL", 29, "CTRL"), ("SHIFT", 42, "SHIFT"), ("ALT", 56, "ALT"), ("SUPER", 125, "SUPER")];
/// (name, evdev code, xkb keysym) for the keys a nav value may press. Names are case-insensitive; a single
/// letter or digit is its own name.
const KEYS: [(&str, u16, &str); 38] = [
    ("ENTER", 28, "Return"), ("RETURN", 28, "Return"), ("ESC", 1, "Escape"), ("ESCAPE", 1, "Escape"), ("TAB", 15, "Tab"),
    ("SPACE", 57, "space"), ("BACKSPACE", 14, "BackSpace"), ("DELETE", 111, "Delete"), ("INSERT", 110, "Insert"),
    ("HOME", 102, "Home"), ("END", 107, "End"), ("PAGEUP", 104, "Prior"), ("PAGEDOWN", 109, "Next"), ("UP", 103, "Up"),
    ("DOWN", 108, "Down"), ("LEFT", 105, "Left"), ("RIGHT", 106, "Right"), ("COMPOSE", 127, "Menu"), ("MENU", 127, "Menu"),
    ("MINUS", 12, "minus"), ("EQUAL", 13, "equal"), ("F1", 59, "F1"), ("F2", 60, "F2"), ("F3", 61, "F3"), ("F4", 62, "F4"),
    ("F5", 63, "F5"), ("F6", 64, "F6"), ("F7", 65, "F7"), ("F8", 66, "F8"), ("F9", 67, "F9"), ("F10", 68, "F10"),
    ("F11", 87, "F11"), ("F12", 88, "F12"), ("0", 11, "0"), ("1", 2, "1"), ("2", 3, "2"), ("3", 4, "3"), ("4", 5, "4"),
];
const DIGITS_5_9: [(char, u16); 5] = [('5', 6), ('6', 7), ('7', 8), ('8', 9), ('9', 10)];
const LETTERS: &str = "qwertyuiop asdfghjkl zxcvbnm";
fn letter_code(c: char) -> Option<u16> {
    // evdev rows: Q..P = 16..25, A..L = 30..38, Z..M = 44..50.
    let c = c.to_ascii_lowercase();
    for (row, base) in LETTERS.split(' ').zip([16u16, 30, 44]) {
        if let Some(i) = row.find(c) {
            return Some(base + i as u16);
        }
    }
    None
}
pub fn parse_key_chord(text: &str) -> Result<KeyChord, String> {
    let parts: Vec<_> = text.split('+').collect();
    let (last, mods) = parts.split_last().ok_or("empty key chord")?;
    let mut codes = vec![];
    for m in mods {
        let up = m.to_ascii_uppercase();
        let code = MODS.iter().find(|(n, _, _)| *n == up).map(|(_, c, _)| *c).ok_or_else(|| format!("unknown modifier {m} in key:{text}"))?;
        if codes.contains(&code) {
            return Err(format!("modifier {m} repeated in key:{text}"));
        }
        codes.push(code);
    }
    let key = key_code(last).ok_or_else(|| format!("unknown key {last:?} in key:{text}"))?;
    Ok(KeyChord { mods: codes, key })
}
fn key_code(name: &str) -> Option<u16> {
    let up = name.to_ascii_uppercase();
    if let Some((_, c, _)) = KEYS.iter().find(|(n, _, _)| *n == up) {
        return Some(*c);
    }
    let mut chars = name.chars();
    match (chars.next(), chars.next()) {
        (Some(c), None) if c.is_ascii_alphabetic() => letter_code(c),
        (Some(c), None) => DIGITS_5_9.iter().find(|(d, _)| *d == c).map(|(_, k)| *k),
        _ => None,
    }
}
/// Every key code the virtual keyboard must advertise.
pub fn keyboard_codes() -> Vec<u16> {
    let mut v: BTreeSet<u16> = KEYS.iter().map(|(_, c, _)| *c).chain(MODS.iter().map(|(_, c, _)| *c)).chain(DIGITS_5_9.iter().map(|(_, c)| *c)).collect();
    for c in LETTERS.chars().filter(|c| *c != ' ') {
        v.insert(letter_code(c).unwrap());
    }
    v.into_iter().collect()
}
/// The xkb keysym and the Hyprland modifier mask for a chord: the compositor projection (a nested session's window).
pub fn keysym(chord: &KeyChord) -> (String, String) {
    let mods = chord.mods.iter().filter_map(|c| MODS.iter().find(|(_, m, _)| m == c).map(|(_, _, n)| *n)).collect::<Vec<_>>().join(" ");
    let sym = KEYS.iter().find(|(_, c, _)| *c == chord.key).map(|(_, _, s)| (*s).to_owned())
        .or_else(|| DIGITS_5_9.iter().find(|(_, k)| *k == chord.key).map(|(d, _)| d.to_string()))
        .or_else(|| LETTERS.chars().find(|c| *c != ' ' && letter_code(*c) == Some(chord.key)).map(|c| c.to_string()))
        .unwrap_or_default();
    (sym, mods)
}
/// What the Linux console's default `us` keymap delivers to the tty for one stroke (VC_META escape prefix, normal
/// cursor-key mode): the fixture tier projects these bytes into a pty that stands in for a VT. Returns None for a
/// stroke the console turns into no bytes (Ctrl+Alt+F<n> switches the VT; bare modifiers).
pub fn vt_bytes(chord: &KeyChord) -> Option<Vec<u8>> {
    let has = |c: u16| chord.mods.contains(&c);
    let (ctrl, shift, alt) = (has(29), has(42), has(56));
    let named: Option<&[u8]> = match chord.key {
        28 => Some(b"\r"), 1 => Some(b"\x1b"), 15 => Some(b"\t"), 14 => Some(b"\x7f"), 57 => Some(b" "),
        103 => Some(b"\x1b[A"), 108 => Some(b"\x1b[B"), 106 => Some(b"\x1b[C"), 105 => Some(b"\x1b[D"),
        102 => Some(b"\x1b[1~"), 110 => Some(b"\x1b[2~"), 111 => Some(b"\x1b[3~"), 107 => Some(b"\x1b[4~"),
        104 => Some(b"\x1b[5~"), 109 => Some(b"\x1b[6~"),
        _ => None,
    };
    let fkey = (59..=68).chain([87, 88]).position(|k| k == chord.key);
    if let Some(n) = fkey {
        if ctrl && alt {
            return None; // Console_<n>: a VT switch, no bytes
        }
        const F: [&[u8]; 12] = [b"\x1b[[A", b"\x1b[[B", b"\x1b[[C", b"\x1b[[D", b"\x1b[[E", b"\x1b[17~", b"\x1b[18~", b"\x1b[19~", b"\x1b[20~", b"\x1b[21~", b"\x1b[23~", b"\x1b[24~"];
        return Some(F[n].to_vec());
    }
    let mut out = match named {
        Some(b) => b.to_vec(),
        None => {
            let (sym, _) = keysym(chord);
            let c = sym.chars().next().filter(|_| sym.chars().count() == 1)?;
            let c = if shift { c.to_ascii_uppercase() } else { c };
            if ctrl && c.is_ascii_alphabetic() { vec![(c.to_ascii_lowercase() as u8) & 0x1f] } else { vec![c as u8] }
        }
    };
    if ctrl && chord.key == 1 {
        out = b"\x1b".to_vec();
    }
    // keycode 15: shift and alt both map to Meta_Tab (ESC TAB) in the installed `us` keymaps (kbd xkb and legacy
    // i386/qwerty), which is kcbt (back-tab) in the linux terminfo; ctrl leaves plain Tab.
    if chord.key == 15 && (shift || alt) {
        return Some(b"\x1b\t".to_vec());
    }
    if alt {
        out.insert(0, 0x1b);
    }
    Some(out)
}
/// One atomic stroke (modifiers down, key down, SYN, key up, modifiers up, SYN). A stroke never leaves a key held, so
/// no context change can strand a pressed virtual key (INV-6).
pub fn stroke(chord: &KeyChord) -> Vec<crate::router::Event> {
    use crate::router::{Event, EV_KEY, EV_SYN, SYN_REPORT};
    let mut v: Vec<_> = chord.mods.iter().map(|m| Event::new(EV_KEY, *m, 1)).collect();
    v.push(Event::new(EV_KEY, chord.key, 1));
    v.push(Event::new(EV_SYN, SYN_REPORT, 0));
    v.push(Event::new(EV_KEY, chord.key, 0));
    v.extend(chord.mods.iter().rev().map(|m| Event::new(EV_KEY, *m, 0)));
    v.push(Event::new(EV_SYN, SYN_REPORT, 0));
    v
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn key_chords_parse_to_us_codes() {
        assert_eq!(parse_key_chord("SHIFT+ENTER").unwrap(), KeyChord { mods: vec![42], key: 28 });
        assert_eq!(parse_key_chord("CTRL+SHIFT+TAB").unwrap(), KeyChord { mods: vec![29, 42], key: 15 });
        assert_eq!(parse_key_chord("CTRL+t").unwrap().key, 20);
        assert_eq!(parse_key_chord("CTRL+0").unwrap().key, 11);
        assert_eq!(parse_key_chord("COMPOSE").unwrap().key, 127);
        assert_eq!(parse_key_chord("ALT+LEFT").unwrap(), KeyChord { mods: vec![56], key: 105 });
        for bad in ["", "CTRL+", "HYPER+a", "CTRL+CTRL+a", "ab", "SHIFT+PAGEUPP"] {
            assert!(parse_key_chord(bad).is_err(), "{bad}");
        }
        assert_eq!(keysym(&parse_key_chord("CTRL+SHIFT+TAB").unwrap()), ("Tab".into(), "CTRL SHIFT".into()));
        assert_eq!(keysym(&parse_key_chord("CTRL+t").unwrap()).0, "t");
        let codes = keyboard_codes();
        for c in [1u16, 11, 15, 20, 28, 29, 42, 56, 103, 104, 109, 125, 127] {
            assert!(codes.contains(&c), "{c}");
        }
    }
    fn console_edit(table: &str, input: &str, value: toml::Value) -> Result<(), String> {
        crate::config::tests::try_fixture_nav_with(|v| {
            v["keybindings"]["global"]["controller"]["nav"][table].as_table_mut().unwrap().insert(input.into(), value);
        }).map(|_| ())
    }
    #[test]
    fn console_rows_use_vt_capable_projections_only() {
        let (cfg, _) = crate::config::tests::fixture_nav();
        assert_eq!(cfg.controller.nav.profile_name("console", true).as_deref(), Some("console_tmux"));
        assert!(cfg.controller.nav.profiles["console_tmux"].values().flat_map(|v| v.actions()).all(console_capable), "inherited rows too");
        let e = console_edit("console_plain", "BTN_SOUTH", "hypr:movefocus left".into()).unwrap_err();
        assert!(e.contains("cannot reach a VT"), "{e}");
        let e = console_edit("console_tmux", "BTN_MODE", "quickshell:drawer-toggle".into()).unwrap_err();
        assert!(e.contains("cannot reach a VT"), "{e}");
        assert!(console_edit("console_plain", "BTN_NORTH", "key:ENTER".into()).is_ok(), "a VT may claim the contract inputs");
        let e = console_edit("desktop", "TOGGLE_CHORD", "mios:graphical-vt".into()).unwrap_err();
        assert!(e.contains("capture contract"), "the desktop toggle chord stays the room's: {e}");
        let e = console_edit("console_plain", "TOGGLE_CHORD", toml::Value::Array(vec!["none".into(), "mios:graphical-vt".into()])).unwrap_err();
        assert!(e.contains("toggle chord takes one action"), "{e}");
        let e = console_edit("console_plain", "RS_UP", "pointer:cursor".into()).unwrap_err();
        assert!(e.contains("only a button"), "{e}");
        let e = console_edit("contexts", "console", "vpad".into()).unwrap_err();
        assert!(e.contains("no virtual-pad consumer"), "{e}");
    }
    #[test]
    fn map_text_lists_contexts_and_both_halves() {
        let (cfg, _) = crate::config::tests::fixture_nav();
        let t = cfg.controller.nav.text(500);
        assert!(t.contains("console          console_tmux (tmux client) / console_plain"), "{t}");
        assert!(t.contains("[console_tmux]") && t.contains("BTN_WEST        tap key:TAB  hold key:SHIFT+TAB"), "{t}");
    }
    #[test]
    fn vt_bytes_follow_the_linux_console_keymap() {
        let b = |s: &str| vt_bytes(&parse_key_chord(s).unwrap());
        assert_eq!(b("ENTER").unwrap(), b"\r");
        assert_eq!(b("TAB").unwrap(), b"\t");
        assert_eq!(b("SHIFT+TAB").unwrap(), b"\x1b\t", "T-I3-4: shift keycode 15 = Meta_Tab = ESC TAB, the linux terminfo kcbt (O-6)");
        assert_eq!(b("ALT+TAB").unwrap(), b"\x1b\t", "alt keycode 15 = Meta_Tab too");
        assert_eq!(b("CTRL+TAB").unwrap(), b"\t");
        assert_eq!(b("PAGEDOWN").unwrap(), b"\x1b[6~");
        assert_eq!(b("ESC").unwrap(), b"\x1b");
        assert_eq!(b("CTRL+c").unwrap(), b"\x03");
        assert_eq!(b("UP").unwrap(), b"\x1b[A");
        assert_eq!(b("PAGEUP").unwrap(), b"\x1b[5~");
        assert_eq!(b("F1").unwrap(), b"\x1b[[A");
        assert_eq!(b("CTRL+ALT+F2"), None, "Console_2 switches the VT and sends nothing");
        assert_eq!(b("t").unwrap(), b"t");
        assert_eq!(b("SHIFT+t").unwrap(), b"T");
    }
    #[test]
    fn a_stroke_releases_everything_it_presses() {
        let s = stroke(&parse_key_chord("CTRL+SHIFT+TAB").unwrap());
        let mut held = BTreeSet::new();
        for e in s.iter().filter(|e| e.kind == 1) {
            if e.value == 1 { held.insert(e.code); } else { held.remove(&e.code); }
        }
        assert!(held.is_empty());
        assert_eq!(s.iter().filter(|e| e.kind == 0).count(), 2);
    }
}
