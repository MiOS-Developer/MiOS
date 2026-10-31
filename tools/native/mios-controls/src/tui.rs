// AI-hint: mios-controls TUI: peripheral selector, keyboard and controller diagrams, the binding table per context, collision markers and a draft -> validate -> apply workflow; input arrives from the dedicated input thread, validation runs on a worker, the render thread only blocks on its channel.
// AI-related: tools/native/mios-controls/src/model.rs, tools/native/mios-controls/src/validate.rs, tools/native/mios-controls/src/apply.rs, tools/native/mios-controls/src/input.rs, tools/native/mios-controls/src/devices.rs, usr/share/mios/mios.toml
// AI-functions: run, App::handle, App::draw, keyboard_rows, pad_lines
use crate::devices::{Device, Kind};
use crate::model::{at, field, str_list, Draft, Layers, CONTEXTS, PAD_BUTTONS};
use crate::validate::{Outcome, Validator};
use ratatui::crossterm::event::{
    DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers, MouseButton, MouseEvent,
    MouseEventKind,
};
use ratatui::crossterm::execute;
use ratatui::layout::{Constraint, Layout, Position, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Cell, Clear, List, ListItem, ListState, Paragraph, Row, Table, TableState, Wrap};
use ratatui::Frame;
use std::collections::BTreeSet;
use std::sync::mpsc;
use toml::Value;

pub enum Msg {
    Input(Event),
    InputFailed(String),
    Validated(u64, Result<Outcome, String>),
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Focus {
    Devices,
    Diagram,
    Bindings,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Field {
    Chord,
    Action,
    Contexts,
    Passthrough,
    Bypass,
    Tier,
    NewId,
    PadAction,
    Export,
    Import,
}

struct Prompt {
    field: Field,
    label: String,
    buf: String,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum Confirm {
    Apply,
    Restore,
    Delete,
    Quit,
}

struct Theme {
    fg: Color,
    accent: Color,
    warn: Color,
    error: Color,
    ok: Color,
    muted: Color,
}

fn hex(c: &str, fallback: Color) -> Color {
    let h = c.trim_start_matches('#');
    match (h.len(), u32::from_str_radix(h, 16)) {
        (6, Ok(v)) => Color::Rgb((v >> 16) as u8, (v >> 8) as u8, v as u8),
        _ => fallback,
    }
}

impl Theme {
    /// [colors] from the SSOT; the Linux VT has 16 colours, so it gets the named ANSI set instead of RGB.
    fn from(merged: &Value) -> Self {
        if std::env::var("TERM").is_ok_and(|t| t == "linux") {
            return Self { fg: Color::White, accent: Color::Cyan, warn: Color::Yellow, error: Color::Red, ok: Color::Green, muted: Color::DarkGray };
        }
        let p = mios_resolver::palette::resolve(merged);
        let g = |k: &str, f: Color| p.get(k).map(|c| hex(c, f)).unwrap_or(f);
        Self {
            fg: g("fg", Color::White),
            accent: g("subtle", Color::Cyan),
            warn: g("warning", Color::Yellow),
            error: g("error", Color::Red),
            ok: g("ansi_10_bright_green", Color::Green),
            muted: g("muted", Color::DarkGray),
        }
    }
}

/// Keyboard diagram rows: (label, key name as chords spell it). Modifier cells carry the modifier token.
fn keyboard_rows() -> Vec<Vec<(&'static str, &'static str)>> {
    let f: Vec<(&'static str, &'static str)> = vec![
        ("Esc", "Escape"), ("F1", "F1"), ("F2", "F2"), ("F3", "F3"), ("F4", "F4"), ("F5", "F5"), ("F6", "F6"),
        ("F7", "F7"), ("F8", "F8"), ("F9", "F9"), ("F10", "F10"), ("F11", "F11"), ("F12", "F12"),
    ];
    let row = |s: &'static str| s.split(' ').map(|k| (k, k)).collect::<Vec<_>>();
    let mut r1 = vec![("`", "grave")];
    r1.extend(row("1 2 3 4 5 6 7 8 9 0"));
    r1.extend([("-", "minus"), ("=", "equal"), ("Bksp", "BackSpace")]);
    let mut r2 = vec![("Tab", "Tab")];
    r2.extend(row("q w e r t y u i o p"));
    r2.extend([("[", "bracketleft"), ("]", "bracketright"), ("\\", "backslash")]);
    let mut r3 = vec![("Caps", "Caps_Lock")];
    r3.extend(row("a s d f g h j k l"));
    r3.extend([(";", "semicolon"), ("'", "apostrophe"), ("Enter", "Return")]);
    let mut r4 = vec![("Shift", "SHIFT")];
    r4.extend(row("z x c v b n m"));
    r4.extend([(",", "comma"), (".", "period"), ("/", "slash"), ("Shift", "SHIFT")]);
    let r5 = vec![("Ctrl", "CTRL"), ("Super", "SUPER"), ("Alt", "ALT"), ("   Space   ", "Space"), ("Alt", "ALT"), ("Super", "SUPER"), ("Ctrl", "CTRL")];
    vec![f, r1, r2, r3, r4, r5]
}

const MODS: &[&str] = &["SUPER", "CTRL", "ALT", "SHIFT"];

/// The normalized steps of a chord (each "MOD+MOD+key"), or None when it does not parse.
fn steps(chord: &str, sep: &str) -> Option<Vec<String>> {
    mios_resolver::keybindings::normalize_chord(chord, sep).ok().map(|c| c.split(sep).map(str::to_owned).collect())
}

fn key_token(name: &str) -> String {
    mios_resolver::keybindings::normalize_chord(name, "\0").unwrap_or_else(|_| name.to_ascii_lowercase())
}

/// Inputs the diagram shows as notes, not buttons: MiOS binds none of them (FINAL.md section 3, Touchpad and Motion rows).
const PAD_NOTES: &[&str] = &[
    "[Touchpad] host touchpad (libinput): pointer, click, 2-finger scroll",
    "[Motion] gyro/accelerometer: not forwarded, no MiOS use",
    "[D-pad] HAT (ABS_HAT0X/Y) or BTN_DPAD_* keys: one meaning",
];
/// Contract annotations the input-ownership work defines; shown, never edited here.
const OWNERSHIP: &[(&str, &str)] = &[
    ("BTN_NORTH", "tap: the aimed window gets every control (you stay in the room); hold: fullscreen"),
    ("BTN_THUMBL", "hold L3+R3: hand control back to the desktop"),
    ("BTN_THUMBR", "hold L3+R3: hand control back to the desktop"),
];

pub struct App {
    layers: Layers,
    validator: Validator,
    merged: Value,
    sep: String,
    saved: Draft,
    draft: Draft,
    devices: Vec<Device>,
    dev: usize,
    focus: Focus,
    ctx: usize,
    sel: usize,
    key: (usize, usize),
    button: usize,
    prompt: Option<Prompt>,
    confirm: Option<Confirm>,
    outcome: Option<Outcome>,
    gen: u64,
    validating: bool,
    status: String,
    help: bool,
    help_scroll: u16,
    compact: bool,
    quit: bool,
    theme: Theme,
    backup_suffix: String,
    areas: [Rect; 3],
    tx: mpsc::Sender<Msg>,
}

impl App {
    fn new(layers: Layers, tx: mpsc::Sender<Msg>) -> Self {
        let merged = layers.effective();
        let draft = Draft::from_merged(&merged);
        let mut devices = vec![Device { name: "Keyboard map".into(), kind: Kind::Keyboard, handlers: String::new() }, Device {
            name: "Controller map".into(),
            kind: Kind::Controller,
            handlers: String::new(),
        }];
        devices.extend(crate::devices::scan());
        let sep = at(&merged, "keybindings.global.chord_sep").and_then(Value::as_str).unwrap_or(" ").to_owned();
        let backup_suffix = crate::controls_str(&merged, "backup_suffix", "controls-backup");
        Self {
            validator: Validator::from_ssot(&merged),
            theme: Theme::from(&merged),
            sep,
            saved: draft.clone(),
            draft,
            merged,
            layers,
            devices,
            dev: 0,
            focus: Focus::Bindings,
            ctx: 0,
            sel: 0,
            key: (0, 0),
            button: 0,
            prompt: None,
            confirm: None,
            outcome: None,
            gen: 0,
            validating: false,
            status: String::new(),
            help: false,
            help_scroll: 0,
            compact: false,
            quit: false,
            backup_suffix,
            areas: [Rect::default(); 3],
            tx,
        }
    }

    fn dirty(&self) -> bool {
        self.draft != self.saved
    }

    fn device(&self) -> &Device {
        &self.devices[self.dev]
    }

    fn context(&self) -> Option<&'static str> {
        if self.ctx == 0 {
            None
        } else {
            Some(CONTEXTS[self.ctx - 1])
        }
    }

    /// Re-validate the current draft on a worker; only the newest generation's result is kept.
    fn revalidate(&mut self) {
        self.gen += 1;
        self.validating = true;
        let gen = self.gen;
        let tx = self.tx.clone();
        let validator = self.validator.clone();
        let text = self.layers.user_doc_for(&self.draft).map(|d| d.to_string());
        let layers = match Layers::load(&self.layers.root) {
            Ok(l) => l,
            Err(e) => {
                let _ = tx.send(Msg::Validated(gen, Err(e)));
                return;
            }
        };
        std::thread::spawn(move || {
            let res = text.and_then(|t| validator.run(&layers, &t));
            let _ = tx.send(Msg::Validated(gen, res));
        });
    }

    fn edited(&mut self, what: String) {
        self.status = what;
        self.revalidate();
    }

    fn binding_state(&self, b: &toml::Table) -> (&'static str, Style) {
        let t = &self.theme;
        if field(b, "scope") == "app" {
            return ("app", Style::new().fg(t.muted));
        }
        let Some(ctx) = self.context() else {
            return ("host", Style::new().fg(t.fg));
        };
        let contexts: BTreeSet<String> = match b.get("contexts") {
            Some(v) => str_list(Some(v)).into_iter().collect(),
            None => crate::model::default_contexts().map(str::to_owned).collect(),
        };
        let pass: BTreeSet<String> = str_list(b.get("passthrough")).into_iter().collect();
        if !contexts.contains(ctx) {
            ("-", Style::new().fg(t.muted))
        } else if pass.contains(ctx) {
            ("->app", Style::new().fg(t.muted))
        } else {
            ("on", Style::new().fg(t.ok))
        }
    }

    // ---- input -------------------------------------------------------------------------------------------

    pub fn handle(&mut self, ev: Event) {
        match ev {
            Event::Key(k) if k.kind != KeyEventKind::Release => self.key_event(k),
            Event::Mouse(m) => self.mouse(m),
            _ => {}
        }
    }

    fn mouse(&mut self, m: MouseEvent) {
        if self.prompt.is_some() || self.confirm.is_some() {
            return;
        }
        let at = Position::new(m.column, m.row);
        let hit = [Focus::Devices, Focus::Diagram, Focus::Bindings].into_iter().zip(self.areas).find(|(_, r)| r.contains(at));
        match m.kind {
            MouseEventKind::Down(MouseButton::Left) => {
                if let Some((f, r)) = hit {
                    self.focus = f;
                    let row = m.row.saturating_sub(r.y + 1) as usize;
                    match f {
                        Focus::Devices if row < self.devices.len() => self.dev = row,
                        Focus::Bindings => {
                            let first = self.sel.saturating_sub(r.height.saturating_sub(4) as usize);
                            let i = first + row.saturating_sub(1);
                            if row >= 1 && i < self.draft.bindings.len() {
                                self.sel = i;
                            }
                        }
                        _ => {}
                    }
                }
            }
            MouseEventKind::ScrollDown => self.step(1),
            MouseEventKind::ScrollUp => self.step(-1),
            _ => {}
        }
    }

    fn step(&mut self, d: i64) {
        let mv = |cur: usize, len: usize| -> usize {
            if len == 0 {
                0
            } else {
                (cur as i64 + d).clamp(0, len as i64 - 1) as usize
            }
        };
        match self.focus {
            Focus::Devices => self.dev = mv(self.dev, self.devices.len()),
            Focus::Bindings => self.sel = mv(self.sel, self.draft.bindings.len()),
            Focus::Diagram => match self.device().kind {
                Kind::Controller => self.button = mv(self.button, PAD_BUTTONS.len()),
                Kind::Keyboard => {
                    let rows = keyboard_rows();
                    self.key.0 = mv(self.key.0, rows.len());
                    self.key.1 = self.key.1.min(rows[self.key.0].len() - 1);
                }
            },
        }
    }

    fn key_event(&mut self, k: KeyEvent) {
        if k.code == KeyCode::Char('c') && k.modifiers.contains(KeyModifiers::CONTROL) && self.prompt.is_none() {
            self.quit = true;
            return;
        }
        if let Some(c) = self.confirm {
            match k.code {
                KeyCode::Char('y') | KeyCode::Char('Y') | KeyCode::Enter => {
                    self.confirm = None;
                    self.confirmed(c);
                }
                _ => {
                    self.confirm = None;
                    self.status = "cancelled".into();
                }
            }
            return;
        }
        if self.prompt.is_some() {
            self.prompt_key(k);
            return;
        }
        if self.help {
            match k.code {
                KeyCode::Down | KeyCode::Char('j') => self.help_scroll = self.help_scroll.saturating_add(1),
                KeyCode::Up | KeyCode::Char('k') => self.help_scroll = self.help_scroll.saturating_sub(1),
                KeyCode::PageDown => self.help_scroll = self.help_scroll.saturating_add(8),
                KeyCode::PageUp => self.help_scroll = self.help_scroll.saturating_sub(8),
                _ => self.help = false,
            }
            return;
        }
        match k.code {
            KeyCode::Char('q') | KeyCode::Esc => {
                if self.dirty() {
                    self.confirm = Some(Confirm::Quit);
                } else {
                    self.quit = true;
                }
            }
            KeyCode::Char('?') | KeyCode::F(1) => {
                self.help = true;
                self.help_scroll = 0;
            }
            KeyCode::Tab => self.focus = match self.focus { Focus::Devices => Focus::Diagram, Focus::Diagram => Focus::Bindings, Focus::Bindings => Focus::Devices },
            KeyCode::BackTab => self.focus = match self.focus { Focus::Devices => Focus::Bindings, Focus::Diagram => Focus::Devices, Focus::Bindings => Focus::Diagram },
            KeyCode::Char(']') => self.ctx = (self.ctx + 1) % (CONTEXTS.len() + 1),
            KeyCode::Char('[') => self.ctx = (self.ctx + CONTEXTS.len()) % (CONTEXTS.len() + 1),
            KeyCode::Char('v') => {
                self.status = "validating".into();
                self.revalidate();
            }
            KeyCode::Char('w') => self.request_apply(),
            KeyCode::Char('R') => self.confirm = Some(Confirm::Restore),
            KeyCode::Char('u') => {
                self.draft = self.saved.clone();
                self.edited("draft reverted to the saved map".into());
            }
            KeyCode::Char('e') => self.ask(Field::Export, "export the binding map to file", ""),
            KeyCode::Char('i') => self.ask(Field::Import, "import a binding map from file", ""),
            KeyCode::Up => self.nav(0, -1),
            KeyCode::Down => self.nav(0, 1),
            KeyCode::Left => self.nav(-1, 0),
            KeyCode::Right => self.nav(1, 0),
            KeyCode::PageUp => self.step(-10),
            KeyCode::PageDown => self.step(10),
            KeyCode::Home => self.step(-100_000),
            KeyCode::End => self.step(100_000),
            _ => match self.focus {
                Focus::Bindings => self.binding_key(k),
                Focus::Diagram => self.diagram_key(k),
                Focus::Devices => {
                    if k.code == KeyCode::Enter {
                        self.focus = Focus::Diagram;
                    }
                }
            },
        }
    }

    fn nav(&mut self, dx: i64, dy: i64) {
        if self.focus == Focus::Diagram && self.device().kind == Kind::Keyboard {
            let rows = keyboard_rows();
            if dy != 0 {
                self.key.0 = (self.key.0 as i64 + dy).clamp(0, rows.len() as i64 - 1) as usize;
            }
            let len = rows[self.key.0].len() as i64;
            self.key.1 = (self.key.1 as i64 + dx).clamp(0, len - 1) as usize;
        } else if self.focus == Focus::Diagram {
            self.step(dx + dy);
        } else if dy != 0 {
            self.step(dy);
        }
    }

    fn selected(&self) -> Option<&toml::Table> {
        self.draft.bindings.get(self.sel)
    }

    fn binding_key(&mut self, k: KeyEvent) {
        let Some(b) = self.selected().cloned() else {
            if k.code == KeyCode::Char('n') {
                self.ask(Field::NewId, "new binding id [a-z0-9-]", "");
            }
            return;
        };
        let list = |key: &str| str_list(b.get(key)).join(",");
        match k.code {
            KeyCode::Char('c') | KeyCode::Enter => self.ask(Field::Chord, "chord", field(&b, "chord")),
            KeyCode::Char('a') => self.ask(Field::Action, "action <namespace>:<verb>", field(&b, "action")),
            KeyCode::Char('x') => self.ask(Field::Contexts, "contexts (comma list; empty = all)", &list("contexts")),
            KeyCode::Char('p') => self.ask(Field::Passthrough, "passthrough contexts (comma list)", &list("passthrough")),
            KeyCode::Char('b') => self.ask(Field::Bypass, "bypass chord (empty = none)", field(&b, "bypass")),
            KeyCode::Char('t') => {
                let tier = b.get("tier").and_then(Value::as_integer).unwrap_or(1).to_string();
                self.ask(Field::Tier, "tier 1..4", &tier)
            }
            KeyCode::Char('n') => self.ask(Field::NewId, "new binding id [a-z0-9-]", ""),
            KeyCode::Char('d') | KeyCode::Delete => self.confirm = Some(Confirm::Delete),
            _ => {}
        }
    }

    fn diagram_key(&mut self, k: KeyEvent) {
        match self.device().kind {
            Kind::Controller => {
                let (button, _) = PAD_BUTTONS[self.button];
                match k.code {
                    KeyCode::Enter | KeyCode::Char('a') => {
                        let cur = self.draft.system_layer.get(button).cloned().unwrap_or_default();
                        self.ask(Field::PadAction, &format!("Guide + {button} action"), &cur);
                    }
                    KeyCode::Delete | KeyCode::Char('d') => {
                        if !self.draft.system_layer.contains_key(button) {
                            self.status = format!("{button} has no system-layer action");
                        } else if !self.layers.removable_button(button) {
                            self.status = format!("{button} is set below the user tier; rebind it instead (a higher tier cannot remove it)");
                        } else {
                            self.draft.system_layer.remove(button);
                            self.edited(format!("removed Guide + {button}"));
                        }
                    }
                    _ => {}
                }
            }
            Kind::Keyboard => {
                if k.code == KeyCode::Enter {
                    let (_, name) = keyboard_rows()[self.key.0][self.key.1];
                    if MODS.contains(&name) {
                        return;
                    }
                    let tok = key_token(name);
                    if let Some(i) = self.draft.bindings.iter().position(|b| self.final_key(b).as_deref() == Some(tok.as_str())) {
                        self.sel = i;
                        self.focus = Focus::Bindings;
                    } else {
                        self.status = format!("no binding on {name}; press n in the table to add SUPER+{name}");
                    }
                }
            }
        }
    }

    fn final_key(&self, b: &toml::Table) -> Option<String> {
        let s = steps(field(b, "chord"), &self.sep)?;
        s.last()?.rsplit('+').next().map(str::to_owned)
    }

    fn ask(&mut self, field: Field, label: &str, cur: &str) {
        self.prompt = Some(Prompt { field, label: label.into(), buf: cur.into() });
    }

    fn prompt_key(&mut self, k: KeyEvent) {
        let p = self.prompt.as_mut().unwrap();
        match k.code {
            KeyCode::Esc => self.prompt = None,
            KeyCode::Enter => {
                let p = self.prompt.take().unwrap();
                if let Err(e) = self.commit(p.field, p.buf.trim()) {
                    self.status = e;
                }
            }
            KeyCode::Backspace => {
                p.buf.pop();
            }
            KeyCode::Char('u') if k.modifiers.contains(KeyModifiers::CONTROL) => p.buf.clear(),
            KeyCode::Char(c) => p.buf.push(c),
            _ => {}
        }
    }

    fn commit(&mut self, f: Field, v: &str) -> Result<(), String> {
        let list = |v: &str| -> Value {
            Value::Array(v.split(',').map(str::trim).filter(|s| !s.is_empty()).map(|s| Value::String(s.into())).collect())
        };
        match f {
            Field::Export => {
                let text = crate::model::keybindings_fragment(&self.draft)?;
                crate::write_private_new(std::path::Path::new(v), &text)?;
                self.status = format!("exported {} binding(s) to {v}", self.draft.bindings.len());
                return Ok(());
            }
            Field::Import => {
                let text = std::fs::read_to_string(v).map_err(|e| format!("{v}: {e}"))?;
                self.draft = crate::model::import_fragment(&text, &self.draft)?;
                self.sel = 0;
                self.edited(format!("imported {v} into the draft (not applied)"));
                return Ok(());
            }
            Field::PadAction => {
                let (button, _) = PAD_BUTTONS[self.button];
                if v.is_empty() {
                    return Err("empty action; use d to remove a user-tier button".into());
                }
                self.draft.system_layer.insert(button.into(), v.into());
                self.edited(format!("Guide + {button} = {v}"));
                return Ok(());
            }
            Field::NewId => {
                if v.is_empty() || !v.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-') {
                    return Err("id must be non-empty [a-z0-9-]".into());
                }
                if self.draft.index(v).is_some() {
                    return Err(format!("binding {v} already exists"));
                }
                let mut b = toml::Table::new();
                b.insert("id".into(), Value::String(v.into()));
                b.insert("tier".into(), Value::Integer(1));
                b.insert("chord".into(), Value::String(String::new()));
                b.insert("action".into(), Value::String(String::new()));
                b.insert("passthrough".into(), list("vm-passthrough"));
                self.draft.bindings.push(b);
                self.sel = self.draft.bindings.len() - 1;
                self.ask(Field::Chord, "chord", "");
                self.status = format!("new binding {v}: set its chord, then its action (a)");
                return Ok(());
            }
            _ => {}
        }
        let b = self.draft.bindings.get_mut(self.sel).ok_or("no binding selected")?;
        let id = field(b, "id").to_owned();
        match f {
            Field::Chord => {
                mios_resolver::keybindings::normalize_chord(v, &self.sep)?;
                b.insert("chord".into(), Value::String(v.into()));
            }
            Field::Action => {
                b.insert("action".into(), Value::String(v.into()));
            }
            Field::Bypass if v.is_empty() => {
                b.remove("bypass");
            }
            Field::Bypass => {
                mios_resolver::keybindings::normalize_chord(v, &self.sep)?;
                b.insert("bypass".into(), Value::String(v.into()));
            }
            Field::Contexts if v.is_empty() => {
                b.remove("contexts");
            }
            Field::Contexts | Field::Passthrough => {
                let key = if f == Field::Contexts { "contexts" } else { "passthrough" };
                if let Some(bad) = v.split(',').map(str::trim).find(|c| !c.is_empty() && !CONTEXTS.contains(c)) {
                    return Err(format!("unknown context {bad:?}; one of {}", CONTEXTS.join(",")));
                }
                b.insert(key.into(), list(v));
            }
            Field::Tier => {
                let t: i64 = v.parse().map_err(|_| "tier must be 1..4".to_string())?;
                b.insert("tier".into(), Value::Integer(t));
            }
            _ => {}
        }
        self.edited(format!("{id}: {} = {v}", match f { Field::Chord => "chord", Field::Action => "action", Field::Bypass => "bypass", Field::Contexts => "contexts", Field::Passthrough => "passthrough", _ => "tier" }));
        Ok(())
    }

    fn request_apply(&mut self) {
        if !self.dirty() {
            self.status = "nothing to apply: the draft equals the saved map".into();
        } else if self.validating {
            self.status = "validation still running; apply when it finishes".into();
        } else {
            match &self.outcome {
                Some(o) if o.new_findings().is_empty() => self.confirm = Some(Confirm::Apply),
                Some(o) => self.status = format!("apply blocked: the draft adds {} finding(s)", o.new_findings().len()),
                None => self.status = "validate first (v)".into(),
            }
        }
    }

    fn confirmed(&mut self, c: Confirm) {
        match c {
            Confirm::Quit => self.quit = true,
            Confirm::Delete => {
                if self.sel < self.draft.bindings.len() {
                    let b = self.draft.bindings.remove(self.sel);
                    self.sel = self.sel.min(self.draft.bindings.len().saturating_sub(1));
                    self.edited(format!("deleted {} from the draft", field(&b, "id")));
                }
            }
            Confirm::Restore => {
                self.draft = self.layers.defaults();
                self.sel = 0;
                self.edited("draft = vendor/host defaults (apply to remove your overrides)".into());
            }
            Confirm::Apply => match self.layers.user_doc_for(&self.draft).map(|d| d.to_string()).and_then(|t| crate::apply::apply(&self.layers, &t, &self.backup_suffix)) {
                Ok(backup) => {
                    self.status = format!(
                        "applied to {}{}; the compositor picks it up on its next config reload",
                        self.layers.user_path.display(),
                        backup.map(|b| format!(" (previous tier: {})", b.display())).unwrap_or_default()
                    );
                    match Layers::load(&self.layers.root) {
                        Ok(l) => {
                            self.layers = l;
                            self.merged = self.layers.effective();
                            self.saved = Draft::from_merged(&self.merged);
                            self.draft = self.saved.clone();
                            self.revalidate();
                        }
                        Err(e) => self.status = format!("applied, but reloading failed: {e}"),
                    }
                }
                Err(e) => self.status = format!("apply failed: {e}"),
            },
        }
    }

    // ---- drawing -----------------------------------------------------------------------------------------

    fn block<'a>(&self, title: &'a str, f: Focus) -> Block<'a> {
        let t = &self.theme;
        Block::default().borders(Borders::ALL).title(title).border_style(if self.focus == f { Style::new().fg(t.accent) } else { Style::new().fg(t.muted) })
    }

    /// Full layout on a large terminal; on a small one (PiP, phone, split pane, 80x24 console) one pane at a time,
    /// the focused one, with the details and status below it.
    pub fn draw(&mut self, fr: &mut Frame) {
        let area = fr.area();
        self.compact = area.width < 120 || area.height < 32;
        let t = &self.theme;
        let ctx = self.context().unwrap_or("all");
        let state = if self.dirty() { "DRAFT" } else { "saved" };
        let head = if self.compact {
            Line::from(vec![Span::styled("MiOS Controls ", Style::new().fg(t.accent).add_modifier(Modifier::BOLD)), Span::raw(format!("[{ctx}] {state}  Tab pane  ? help"))])
        } else {
            Line::from(vec![
                Span::styled(" MiOS Controls ", Style::new().fg(t.accent).add_modifier(Modifier::BOLD)),
                Span::raw(format!("| context [{ctx}] ([ ]) | {} | ? help", if self.dirty() { "DRAFT (unsaved)" } else { "saved" })),
            ])
        };
        if self.compact {
            let [top, main, detail, status] = Layout::vertical([Constraint::Length(1), Constraint::Min(6), Constraint::Length(6), Constraint::Length(2)]).areas(area);
            fr.render_widget(Paragraph::new(head), top);
            self.areas = [main, main, main];
            match self.focus {
                Focus::Devices => self.draw_devices(fr, main),
                Focus::Diagram => match self.device().kind {
                    Kind::Keyboard => self.draw_keyboard(fr, main),
                    Kind::Controller => self.draw_pad(fr, main),
                },
                Focus::Bindings => self.draw_table(fr, main),
            }
            self.draw_detail(fr, detail);
            self.draw_status(fr, status);
        } else {
            let [top, body, table, status] = Layout::vertical([Constraint::Length(1), Constraint::Length(13), Constraint::Min(5), Constraint::Length(3)]).areas(area);
            fr.render_widget(Paragraph::new(head), top);
            let [devs, diagram, detail] = Layout::horizontal([Constraint::Length(26), Constraint::Min(40), Constraint::Length(44)]).areas(body);
            self.areas = [devs, diagram, table];
            self.draw_devices(fr, devs);
            match self.device().kind {
                Kind::Keyboard => self.draw_keyboard(fr, diagram),
                Kind::Controller => self.draw_pad(fr, diagram),
            }
            self.draw_detail(fr, detail);
            self.draw_table(fr, table);
            self.draw_status(fr, status);
        }
        if self.help {
            self.draw_help(fr);
        }
        if let Some(p) = &self.prompt {
            let area = centered(fr.area(), 70, 4);
            fr.render_widget(Clear, area);
            let line = Line::from(vec![Span::raw(p.buf.clone()), Span::styled("_", Style::new().add_modifier(Modifier::SLOW_BLINK))]);
            fr.render_widget(Paragraph::new(line).wrap(Wrap { trim: false }).block(Block::default().borders(Borders::ALL).title(format!(" {} (Enter/Esc) ", p.label))), area);
        }
        if let Some(c) = self.confirm {
            let q = match c {
                Confirm::Apply => format!("Write the draft to {}? (y/n)", self.layers.user_path.display()),
                Confirm::Restore => "Replace the draft with the vendor/host defaults? (y/n)".into(),
                Confirm::Delete => format!("Delete {} from the draft? (y/n)", self.selected().map(|b| field(b, "id")).unwrap_or("")),
                Confirm::Quit => "Quit and discard the unsaved draft? (y/n)".into(),
            };
            let area = centered(fr.area(), 70, 5);
            fr.render_widget(Clear, area);
            fr.render_widget(Paragraph::new(q).wrap(Wrap { trim: false }).block(Block::default().borders(Borders::ALL).title(" confirm ")), area);
        }
    }

    fn draw_devices(&self, fr: &mut Frame, area: Rect) {
        let items: Vec<ListItem> = self
            .devices
            .iter()
            .enumerate()
            .map(|(i, d)| {
                let tag = if i < 2 { "map" } else if d.kind == Kind::Controller { "pad" } else { "kbd" };
                ListItem::new(format!("{tag} {}", d.name))
            })
            .collect();
        let mut st = ListState::default().with_selected(Some(self.dev));
        let list = List::new(items).block(self.block(" Peripherals ", Focus::Devices)).highlight_style(Style::new().add_modifier(Modifier::REVERSED));
        fr.render_stateful_widget(list, area, &mut st);
    }

    fn selected_steps(&self) -> Vec<String> {
        self.selected().and_then(|b| steps(field(b, "chord"), &self.sep)).unwrap_or_default()
    }

    fn draw_keyboard(&self, fr: &mut Frame, area: Rect) {
        let t = &self.theme;
        let sel_steps = self.selected_steps();
        let sel_tokens: BTreeSet<String> = sel_steps.iter().flat_map(|s| s.split('+').map(str::to_owned).collect::<Vec<_>>()).collect();
        let bound: BTreeSet<String> = self.draft.bindings.iter().filter_map(|b| self.final_key(b)).collect();
        let lines: Vec<Line> = keyboard_rows()
            .iter()
            .enumerate()
            .map(|(r, row)| {
                Line::from(
                    row.iter()
                        .enumerate()
                        .flat_map(|(c, (label, name))| {
                            let tok = if MODS.contains(name) { name.to_string() } else { key_token(name) };
                            let mut st = Style::new().fg(t.muted);
                            if bound.contains(&tok) {
                                st = Style::new().fg(t.accent);
                            }
                            if sel_tokens.contains(&tok) {
                                st = Style::new().fg(t.warn).add_modifier(Modifier::BOLD);
                            }
                            if self.focus == Focus::Diagram && self.key == (r, c) {
                                st = st.add_modifier(Modifier::REVERSED);
                            }
                            [Span::styled(if self.compact { label.trim().to_string() } else { format!("[{label}]") }, st), Span::raw(" ")]
                        })
                        .collect::<Vec<_>>(),
                )
            })
            .collect();
        let mut lines = lines;
        if !self.compact {
            lines.push(Line::from(""));
            lines.push(Line::styled("bound keys highlighted; the selected binding's chord in bold", Style::new().fg(t.muted)));
        }
        fr.render_widget(Paragraph::new(lines).block(self.block(" Keyboard ", Focus::Diagram)), area);
    }

    fn pad_role(&self, button: &str) -> Vec<String> {
        let mut out = Vec::new();
        let m = &self.merged;
        let g = |k: &str| at(m, &format!("keybindings.global.controller.{k}"));
        if g("guide_button").and_then(Value::as_str) == Some(button) {
            out.push("hold: system layer (Guide + button)".into());
        }
        if g("cursor_mode_button").and_then(Value::as_str) == Some(button) {
            out.push(format!("tap: cursor mode in {}", str_list(g("cursor_mode_contexts")).join(",")));
        }
        if str_list(g("mode_override_chord")).iter().any(|b| b == button) {
            out.push(format!("mode override chord {}", str_list(g("mode_override_chord")).join("+")));
        }
        let chord = crate::model::toggle_chord(m);
        if chord.iter().any(|b| b == button) {
            out.push(format!("toggle chord {}: tap = Hypr3D room, hold = release capture", chord.join("+")));
        }
        for (b, note) in OWNERSHIP {
            if *b == button {
                out.push((*note).into());
            }
        }
        for b in &self.draft.bindings {
            if steps(field(b, "chord"), &self.sep).is_some_and(|s| s.iter().any(|st| st.split('+').any(|x| x == button))) {
                out.push(format!("binding {}: {} -> {}", field(b, "id"), field(b, "chord"), field(b, "action")));
            }
        }
        out
    }

    /// [keybindings.global.controller].capture_hold_ms: how long North or L3+R3 is held before it counts as a hold.
    fn hold_ms(&self) -> i64 {
        at(&self.merged, "keybindings.global.controller.capture_hold_ms").and_then(Value::as_integer).unwrap_or(500)
    }

    fn draw_pad(&self, fr: &mut Frame, area: Rect) {
        let t = &self.theme;
        let cell = |button: &str| -> Span<'static> {
            let i = PAD_BUTTONS.iter().position(|(b, _)| *b == button).unwrap_or(0);
            let label = PAD_BUTTONS[i].1;
            let mut st = Style::new().fg(t.muted);
            if self.draft.system_layer.contains_key(button) {
                st = Style::new().fg(t.accent);
            }
            if OWNERSHIP.iter().any(|(b, _)| *b == button) {
                st = st.add_modifier(Modifier::UNDERLINED);
            }
            if self.focus == Focus::Diagram && self.button == i {
                st = st.add_modifier(Modifier::REVERSED);
            }
            Span::styled(format!("[{label}]"), st)
        };
        let s = |n: usize| Span::raw(" ".repeat(n));
        let mut lines = vec![
            Line::from(vec![s(2), cell("BTN_TL2"), s(28), cell("BTN_TR2")]),
            Line::from(vec![s(2), cell("BTN_TL"), s(28), cell("BTN_TR")]),
            Line::from(vec![s(6), cell("BTN_DPAD_UP"), s(5), cell("BTN_SELECT"), cell("BTN_MODE"), cell("BTN_START"), s(4), cell("BTN_NORTH")]),
            Line::from(vec![s(1), cell("BTN_DPAD_LEFT"), s(1), cell("BTN_DPAD_RIGHT"), s(19), cell("BTN_WEST"), s(3), cell("BTN_EAST")]),
            Line::from(vec![s(5), cell("BTN_DPAD_DOWN"), s(26), cell("BTN_SOUTH")]),
            Line::from(vec![s(10), Span::raw("( L )"), cell("BTN_THUMBL"), s(4), cell("BTN_THUMBR"), Span::raw("( R )")]),
        ];
        if !self.compact {
            lines.insert(2, Line::from(""));
            lines.push(Line::from(vec![s(10), Span::styled("left stick", Style::new().fg(t.muted)), s(6), Span::styled("right stick", Style::new().fg(t.muted))]));
            lines.push(Line::from(""));
            lines.push(Line::styled("highlighted: has a Guide action; underlined: takes or returns control", Style::new().fg(t.muted)));
            for note in PAD_NOTES {
                lines.push(Line::styled((*note).to_string(), Style::new().fg(t.muted)));
            }
        }
        fr.render_widget(Paragraph::new(lines).block(self.block(" Controller ", Focus::Diagram)), area);
    }

    fn draw_detail(&self, fr: &mut Frame, area: Rect) {
        let t = &self.theme;
        let mut lines: Vec<Line> = Vec::new();
        if self.device().kind == Kind::Controller {
            let (button, label) = PAD_BUTTONS[self.button];
            lines.push(Line::styled(format!("{label} ({button})"), Style::new().fg(t.accent).add_modifier(Modifier::BOLD)));
            let chord = crate::model::toggle_chord(&self.merged);
            if button == "BTN_NORTH" || chord.iter().any(|b| b == button) {
                let hold = self.hold_ms();
                let release = chord.iter().map(|b| PAD_BUTTONS.iter().find(|(n, _)| n == b).map(|(_, l)| *l).unwrap_or(b.as_str())).collect::<Vec<_>>().join("+");
                lines.push(Line::styled(format!("Tap N: aimed window takes control. Hold N {hold} ms: fullscreen. Hold {release} {hold} ms: release."), Style::new().fg(t.warn)));
            }
            lines.push(Line::from(format!("Guide + {label}: {}", self.draft.system_layer.get(button).map(String::as_str).unwrap_or("-"))));
            for r in self.pad_role(button) {
                lines.push(Line::from(format!("- {r}")));
            }
            let captured = at(&self.merged, "keybindings.global.controller.captured_system_layer").and_then(Value::as_bool) == Some(true);
            lines.push(Line::styled(
                if captured { "captured apps still see the Guide layer" } else { "captured apps own every chord (Guide layer off)" },
                Style::new().fg(t.muted),
            ));
            if let Some(o) = &self.outcome {
                for f in o.findings.iter().filter(|f| f.contains(&format!("system_layer.{button}"))) {
                    lines.push(Line::styled(f.clone(), Style::new().fg(t.error)));
                }
            }
        } else if let Some(b) = self.selected() {
            let (state, st) = self.binding_state(b);
            lines.push(Line::styled(field(b, "id").to_owned(), Style::new().fg(t.accent).add_modifier(Modifier::BOLD)));
            lines.push(Line::from(format!("chord  {}", field(b, "chord"))));
            if !field(b, "bypass").is_empty() {
                lines.push(Line::from(format!("bypass {}", field(b, "bypass"))));
            }
            lines.push(Line::from(format!("action {}", field(b, "action"))));
            let ctxs = str_list(b.get("contexts"));
            lines.push(Line::from(format!("in     {}", if ctxs.is_empty() { "all contexts".into() } else { ctxs.join(",") })));
            let pass = str_list(b.get("passthrough"));
            if !pass.is_empty() {
                lines.push(Line::from(format!("passes {} to the app", pass.join(","))));
            }
            lines.push(Line::from(vec![Span::raw("here   "), Span::styled(state, st)]));
            if !field(b, "note").is_empty() {
                lines.push(Line::styled(field(b, "note").to_owned(), Style::new().fg(t.muted)));
            }
            if let Some(o) = &self.outcome {
                for f in o.mentions(field(b, "id")) {
                    lines.push(Line::styled(f.clone(), Style::new().fg(t.error)));
                }
            }
        }
        fr.render_widget(Paragraph::new(lines).wrap(Wrap { trim: true }).block(Block::default().borders(Borders::ALL).title(" Selected ")), area);
    }

    fn draw_table(&self, fr: &mut Frame, area: Rect) {
        let t = &self.theme;
        let rows: Vec<Row> = self
            .draft
            .bindings
            .iter()
            .map(|b| {
                let hit = self.outcome.as_ref().map(|o| !o.mentions(field(b, "id")).is_empty()).unwrap_or(false);
                let changed = self.saved.index(field(b, "id")).map(|i| &self.saved.bindings[i] != b).unwrap_or(true);
                let (state, st) = self.binding_state(b);
                let mut cells = vec![
                    Cell::from(if hit { "!" } else if changed { "*" } else { " " }).style(Style::new().fg(if hit { t.error } else { t.warn })),
                    Cell::from(field(b, "id").to_owned()),
                    Cell::from(b.get("tier").and_then(Value::as_integer).map(|x| x.to_string()).unwrap_or_default()),
                    Cell::from(field(b, "chord").to_owned()),
                    Cell::from(field(b, "action").to_owned()),
                    Cell::from(state).style(st),
                ];
                if self.compact {
                    cells.remove(2);
                }
                Row::new(cells)
            })
            .collect();
        let (widths, header) = if self.compact {
            (
                vec![Constraint::Length(1), Constraint::Percentage(30), Constraint::Percentage(28), Constraint::Min(8), Constraint::Length(5)],
                vec!["", "id", "chord", "action", "here"],
            )
        } else {
            (
                vec![Constraint::Length(1), Constraint::Length(24), Constraint::Length(4), Constraint::Length(22), Constraint::Min(20), Constraint::Length(6)],
                vec!["", "id", "tier", "chord", "action", "here"],
            )
        };
        let table = Table::new(rows, widths)
            .header(Row::new(header).style(Style::new().fg(t.accent)))
            .row_highlight_style(Style::new().add_modifier(Modifier::REVERSED))
            .block(self.block(" Bindings  (! finding  * changed) ", Focus::Bindings));
        let mut st = TableState::default().with_selected(if self.draft.bindings.is_empty() { None } else { Some(self.sel) });
        fr.render_stateful_widget(table, area, &mut st);
    }

    fn draw_status(&self, fr: &mut Frame, area: Rect) {
        let t = &self.theme;
        let check = if self.validating {
            Span::styled("validating...", Style::new().fg(t.warn))
        } else {
            match &self.outcome {
                None => Span::styled("not validated", Style::new().fg(t.muted)),
                Some(o) if o.new_findings().is_empty() => Span::styled(format!("valid: {}", o.summary), Style::new().fg(t.ok)),
                Some(o) => Span::styled(format!("{} new finding(s): {}", o.new_findings().len(), o.new_findings()[0]), Style::new().fg(t.error)),
            }
        };
        let mut lines = vec![Line::from(check), Line::from(Span::raw(self.status.clone()))];
        if !self.compact {
            lines.push(Line::styled("Tab pane  arrows move  Enter edit  n new  d del  v check  w apply  R defaults  e/i export/import  u revert  ? help  q quit", Style::new().fg(t.muted)));
        }
        fr.render_widget(Paragraph::new(lines), area);
    }

    fn draw_help(&self, fr: &mut Frame) {
        let area = if self.compact { fr.area() } else { centered(fr.area(), 84, 26) };
        fr.render_widget(Clear, area);
        let hold = self.hold_ms();
        let text = [
            "MOVING AROUND  Tab / Shift+Tab switch between Peripherals, the diagram and the Bindings list (on a small screen one of them fills it). Arrows, PgUp/PgDn and Home/End move. Enter opens or edits what is selected. Esc closes a box.".to_string(),
            "A controller works the same way: D-pad or stick = arrows, South = Enter, East = Esc, bumpers = Tab.".to_string(),
            "PERIPHERALS  Choose the keyboard map or the controller map. Connected devices are listed.".to_string(),
            "KEYBOARD  Highlighted keys have a binding; the selected binding's keys are bold. Enter on a key shows its binding.".to_string(),
            "CONTROLLER  Highlighted buttons have a Guide action (hold Guide + button). Enter sets it, d removes one you added.".to_string(),
            format!("TAKING CONTROL  Tap North: the window you aim at gets every control and you stay in the room. Hold North {hold} ms: fullscreen. Hold L3+R3 {hold} ms: control returns to the desktop."),
            "BINDINGS  c chord, a action, x where it works, p let the app have it, b escape chord, t tier, n new, d delete. [ and ] pick a place: on = MiOS handles it, ->app = the app gets it, app = the app's own key, - = not used there. ! = conflict, * = changed.".to_string(),
            "SAVING  Changes are a draft until you press w; w only works when the draft has no new conflict. R starts from the defaults, u undoes the draft, e / i export or import a map.".to_string(),
        ]
        .join("\n\n");
        fr.render_widget(
            Paragraph::new(text)
                .wrap(Wrap { trim: true })
                .scroll((self.help_scroll, 0))
                .block(Block::default().borders(Borders::ALL).title(" help: arrows scroll, any other key closes ")),
            area,
        );
    }

}

fn centered(area: Rect, w: u16, h: u16) -> Rect {
    let w = w.min(area.width);
    let h = h.min(area.height);
    Rect::new(area.x + (area.width - w) / 2, area.y + (area.height - h) / 2, w, h)
}

pub fn run(layers: Layers) -> Result<(), String> {
    let (tx, rx) = mpsc::channel();
    let input_cpu = crate::controls_int(&layers.effective(), "input_cpu");
    let mut app = App::new(layers, tx.clone());
    let mut terminal = ratatui::init();
    let _ = execute!(std::io::stdout(), EnableMouseCapture);
    let _input = crate::input::spawn(tx, input_cpu);
    app.revalidate();
    let res = (|| -> Result<(), String> {
        loop {
            terminal.draw(|f| app.draw(f)).map_err(|e| e.to_string())?;
            // Block until something happens: input, a validation result. No polling interval.
            let msg = rx.recv().map_err(|e| e.to_string())?;
            match msg {
                Msg::Input(ev) => app.handle(ev),
                Msg::InputFailed(e) => return Err(format!("terminal input failed: {e}")),
                Msg::Validated(gen, res) if gen == app.gen => {
                    app.validating = false;
                    match res {
                        Ok(o) => app.outcome = Some(o),
                        Err(e) => {
                            app.outcome = None;
                            app.status = format!("validation could not run: {e}");
                        }
                    }
                }
                Msg::Validated(..) => {}
            }
            if app.quit {
                return Ok(());
            }
        }
    })();
    let _ = execute!(std::io::stdout(), DisableMouseCapture);
    ratatui::restore();
    res
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_keyboard_cell_names_a_parseable_key_or_modifier() {
        for row in keyboard_rows() {
            for (_, name) in row {
                if MODS.contains(&name) {
                    continue;
                }
                assert!(mios_resolver::keybindings::normalize_chord(&format!("SUPER+{name}"), "\0").is_ok(), "{name}");
            }
        }
    }

    #[test]
    fn every_pad_button_is_a_real_evdev_button() {
        for (b, _) in PAD_BUTTONS {
            mios_resolver::keybindings::button_code(b).unwrap();
        }
    }

    fn app() -> App {
        let base: Value = "[keybindings.global]\nchord_sep = \" \"\n[[keybindings.global.bindings]]\nid = \"terminal\"\ntier = 1\nchord = \"SUPER+Return\"\naction = \"exec:mios-terminal\"\n[keybindings.global.controller]\nguide_button = \"BTN_MODE\"\n[keybindings.global.controller.system_layer]\nBTN_SOUTH = \"hypr:togglefloating\"\n".parse().unwrap();
        let layers = Layers { root: "/nonexistent".into(), user_path: "/nonexistent/u.toml".into(), base, user_text: String::new(), user_value: Value::Table(Default::default()) };
        App::new(layers, mpsc::channel().0)
    }

    fn render(app: &mut App, w: u16, h: u16) -> String {
        let mut term = ratatui::Terminal::new(ratatui::backend::TestBackend::new(w, h)).unwrap();
        term.draw(|f| app.draw(f)).unwrap();
        let buf = term.backend().buffer().clone();
        (0..h).map(|y| (0..w).map(|x| buf[(x, y)].symbol().to_string()).collect::<String>() + "\n").collect()
    }

    #[test]
    fn every_pane_fits_pip_phone_console_and_desktop_sizes() {
        for (w, h) in [(40, 12), (66, 18), (80, 24), (170, 46)] {
            let mut a = app();
            let s = render(&mut a, w, h);
            assert!(s.contains("MiOS Controls"), "{w}x{h}:\n{s}");
            assert!(s.contains("terminal"), "binding visible at {w}x{h}:\n{s}");
            assert_eq!(a.compact, w < 120 || h < 32);
            for focus in [Focus::Devices, Focus::Diagram, Focus::Bindings] {
                a.focus = focus;
                render(&mut a, w, h);
            }
            a.help = true;
            let s = render(&mut a, w, h);
            assert!(s.contains("MOVING AROUND"), "help at {w}x{h}:\n{s}");
        }
    }

    #[test]
    fn compact_mode_shows_the_focused_pane_and_scrolls_help() {
        let mut a = app();
        a.focus = Focus::Diagram;
        a.dev = 1;
        let s = render(&mut a, 66, 18);
        assert!(s.contains("Controller") && !s.contains(" Bindings "), "{s}");
        a.help = true;
        let first = render(&mut a, 66, 18);
        a.handle(Event::Key(KeyEvent::new(KeyCode::PageDown, KeyModifiers::NONE)));
        assert!(a.help, "scrolling keeps help open");
        assert_ne!(render(&mut a, 66, 18), first, "help scrolled");
        a.handle(Event::Key(KeyEvent::new(KeyCode::Char('x'), KeyModifiers::NONE)));
        assert!(!a.help);
    }

    #[test]
    fn vt_gets_named_colours() {
        std::env::set_var("TERM", "linux");
        let t = Theme::from(&Value::Table(Default::default()));
        assert_eq!(t.accent, Color::Cyan);
    }
}
