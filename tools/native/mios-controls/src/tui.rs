// AI-hint: mios-controls TUI: device views (keyboard with every key, mouse with wheel and side buttons, controller per family) drawn as key caps that light up per modifier/pad layer, the list of every action bound to the selected input (global, compositor, tmux-os, Guide layer, Hypr3D room, terminal, fixed roles), rebind / change action / new / delete through the draft -> validate -> apply pipeline, capture-to-bind (press the physical key, button, stick or wheel), search, conflict highlighting, a per-device profile view, undo/redo, and full keyboard, mouse and controller navigation of the editor itself (action menu, pickers); input arrives from the dedicated input threads, validation runs on a worker, the render thread only blocks on its channel.
// AI-related: tools/native/mios-controls/src/model.rs, tools/native/mios-controls/src/bindmap.rs, tools/native/mios-controls/src/layout.rs, tools/native/mios-controls/src/pads.rs, tools/native/mios-controls/src/validate.rs, tools/native/mios-controls/src/apply.rs, tools/native/mios-controls/src/input.rs, tools/native/mios-controls/src/devices.rs, usr/share/mios/mios.toml
// AI-functions: run, App::handle, App::draw, App::rebind, App::capture_done, App::pad_nav, App::menu_items, App::panel_uses
use crate::bindmap::{self, layer_title, Source, Use, PAD_DESKTOP, PAD_GUIDE, PAD_ROOM};
use crate::devices::{Device, Family, Kind};
use crate::layout::{self, El, Glyphs, Look};
use crate::model::{at, field, str_list, Draft, Layers, CONTEXTS};
use crate::pads::{PadCmd, PadEdge, PadHandle};
use crate::validate::{Outcome, Validator};
use ratatui::crossterm::event::{
    DisableFocusChange, DisableMouseCapture, EnableFocusChange, EnableMouseCapture, Event, KeyCode,
    KeyEvent, KeyEventKind, KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
};
use ratatui::crossterm::execute;
use ratatui::layout::{Constraint, Layout, Position, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{
    Block, BorderType, Borders, Cell, Clear, Paragraph, Row, Table, TableState, Wrap,
};
use ratatui::Frame;
use std::collections::BTreeSet;
use std::sync::mpsc;
use std::time::{Duration, Instant};
use toml::Value;

pub enum Msg {
    Input(Event),
    InputFailed(String),
    Validated(u64, Result<Outcome, String>),
    Pad(PadEdge),
    PadStatus(String),
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum View {
    Keyboard,
    Mouse,
    Controller,
    Bindings,
    Devices,
}

const VIEWS: [View; 5] = [
    View::Keyboard,
    View::Mouse,
    View::Controller,
    View::Bindings,
    View::Devices,
];

impl View {
    fn title(self) -> &'static str {
        match self {
            View::Keyboard => "Keyboard",
            View::Mouse => "Mouse",
            View::Controller => "Controller",
            View::Bindings => "Bindings",
            View::Devices => "Devices",
        }
    }
    fn kind(self) -> &'static str {
        match self {
            View::Keyboard => "key",
            View::Mouse => "mouse",
            View::Controller => "pad",
            _ => "",
        }
    }
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Focus {
    Drawing,
    Uses,
}

#[derive(Clone, Copy, PartialEq, Eq, Debug)]
enum Field {
    Chord,
    Action,
    Contexts,
    Passthrough,
    Bypass,
    Tier,
    NewId,
    Export,
    Import,
    Search,
    UseAction,
    NewAction,
    TmuxCommand,
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
    Revert,
    Delete,
    DeleteUse,
    Quit,
}

/// Everything the action menu and the controller can ask for.
#[derive(Clone, Copy, PartialEq, Eq, Debug)]
pub enum Act {
    Enter,
    Back,
    Menu,
    Capture,
    FocusNext,
    Help,
    LayerPrev,
    LayerNext,
    ViewPrev,
    ViewNext,
    PageUp,
    PageDown,
    Up,
    Down,
    Left,
    Right,
    Rebind,
    ChangeAction,
    NewBinding,
    DeleteUse,
    Undo,
    Redo,
    Validate,
    Apply,
    Revert,
    Defaults,
    Search,
    Family,
    Rescan,
    Quit,
}

struct Menu {
    title: String,
    items: Vec<(String, Act)>,
    sel: usize,
}

/// A list to choose from (actions for a new binding or a change), usable with arrows, d-pad, mouse or Enter.
struct Picker {
    title: String,
    items: Vec<String>,
    sel: usize,
    then: PickThen,
}

#[derive(Clone)]
enum PickThen {
    ChangeAction(Use),
    NewOn(String, String),
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
            return Self {
                fg: Color::White,
                accent: Color::Cyan,
                warn: Color::Yellow,
                error: Color::Red,
                ok: Color::Green,
                muted: Color::DarkGray,
            };
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

/// What a mouse click lands on.
#[derive(Clone, Debug, PartialEq)]
enum Hit {
    Input(String),
    Tab(View),
    Layer(usize),
    Use(usize),
    Row(usize),
    Device(usize),
    MenuItem(usize),
    PickItem(usize),
}

/// Inputs the diagram shows as notes, not buttons: MiOS binds none of them (FINAL.md section 3, Touchpad and Motion rows).
const PAD_NOTES: &[&str] = &[
    "[Touchpad] host touchpad (libinput): pointer, click, 2-finger scroll",
    "[Motion] gyro/accelerometer: not forwarded, no MiOS use",
    "[D-pad] HAT (ABS_HAT0X/Y) or BTN_DPAD_* keys: one meaning",
];

pub struct App {
    layers: Layers,
    validator: Validator,
    merged: Value,
    sep: String,
    saved: Draft,
    draft: Draft,
    undo: Vec<(Draft, String)>,
    redo: Vec<(Draft, String)>,
    undo_depth: usize,
    uses: Vec<Use>,
    flagged: BTreeSet<usize>,
    lua: Option<String>,
    devices: Vec<Device>,
    families: Vec<Family>,
    family_override: Option<usize>,
    dev_sel: usize,
    pad_dev: Option<usize>,
    mouse_dev: Option<usize>,
    view: View,
    focus: Focus,
    layer: [usize; 3],
    cursor: [String; 3],
    uses_sel: usize,
    sel: usize,
    ctx: usize,
    search: Option<String>,
    pending: Option<Use>,
    capture: Option<Instant>,
    capture_timeout: Duration,
    last_captured: Option<String>,
    prompt: Option<Prompt>,
    confirm: Option<Confirm>,
    menu: Option<Menu>,
    picker: Option<Picker>,
    outcome: Option<Outcome>,
    gen: u64,
    validating: bool,
    status: String,
    pad_status: String,
    help: bool,
    help_scroll: u16,
    compact: bool,
    quit: bool,
    focused: bool,
    theme: Theme,
    glyphs: Glyphs,
    backup_suffix: String,
    pad_nav: Vec<(String, String)>,
    els: Vec<El>,
    hits: Vec<(Rect, Hit)>,
    tx: mpsc::Sender<Msg>,
    pads: Option<PadHandle>,
}

fn kind_index(kind: &str) -> usize {
    match kind {
        "mouse" => 1,
        "pad" => 2,
        _ => 0,
    }
}

fn view_of(kind: &str) -> View {
    match kind {
        "mouse" => View::Mouse,
        "pad" => View::Controller,
        _ => View::Keyboard,
    }
}

impl App {
    fn new(layers: Layers, tx: mpsc::Sender<Msg>) -> Self {
        let merged = layers.effective();
        let draft = Draft::from_merged(&merged);
        let sep = at(&merged, "keybindings.global.chord_sep")
            .and_then(Value::as_str)
            .unwrap_or(" ")
            .to_owned();
        let backup_suffix = crate::controls_str(&merged, "backup_suffix", "controls-backup");
        let term = std::env::var("TERM").unwrap_or_default();
        let glyphs = Glyphs::for_term(&crate::controls_str(&merged, "glyphs", "auto"), &term);
        // [desktop.controls.pad_nav]; without it the editor still answers the pad (degrade open)
        let pad_nav: Vec<(String, String)> = at(&merged, "desktop.controls.pad_nav")
            .and_then(Value::as_table)
            .map(|t| {
                t.iter()
                    .filter_map(|(k, v)| v.as_str().map(|v| (k.clone(), v.to_owned())))
                    .collect()
            })
            .unwrap_or_else(|| {
                [
                    ("BTN_SOUTH", "enter"),
                    ("BTN_EAST", "back"),
                    ("BTN_WEST", "menu"),
                    ("BTN_NORTH", "capture"),
                    ("BTN_SELECT", "focus"),
                    ("BTN_START", "help"),
                    ("BTN_TL", "layer-prev"),
                    ("BTN_TR", "layer-next"),
                    ("BTN_TL2", "view-prev"),
                    ("BTN_TR2", "view-next"),
                ]
                .iter()
                .map(|(k, v)| (k.to_string(), v.to_string()))
                .collect()
            });
        let devices = crate::devices::scan();
        let families = Family::from_ssot(&merged);
        let names = pad_names(&merged);
        let pad_dev = devices.iter().position(|d| {
            d.kind == Kind::Controller && !d.is_virtual() && crate::pads::wanted(&names, &d.name)
        });
        let mouse_dev = devices
            .iter()
            .position(|d| d.kind == Kind::Mouse && !d.is_virtual());
        let mut app = Self {
            validator: Validator::from_ssot(&merged),
            theme: Theme::from(&merged),
            undo_depth: crate::controls_int(&merged, "undo_depth")
                .unwrap_or(64)
                .max(1) as usize,
            capture_timeout: Duration::from_millis(
                crate::controls_int(&merged, "capture_timeout_ms")
                    .unwrap_or(10_000)
                    .max(500) as u64,
            ),
            sep,
            saved: draft.clone(),
            draft,
            undo: Vec::new(),
            redo: Vec::new(),
            uses: Vec::new(),
            flagged: BTreeSet::new(),
            lua: None,
            merged,
            layers,
            devices,
            families,
            family_override: None,
            dev_sel: 0,
            pad_dev,
            mouse_dev,
            view: View::Keyboard,
            focus: Focus::Drawing,
            layer: [0; 3],
            cursor: [
                "key:return".into(),
                "mouse:272".into(),
                "pad:BTN_SOUTH".into(),
            ],
            uses_sel: 0,
            sel: 0,
            ctx: 0,
            search: None,
            pending: None,
            capture: None,
            last_captured: None,
            prompt: None,
            confirm: None,
            menu: None,
            picker: None,
            outcome: None,
            gen: 0,
            validating: false,
            status: String::new(),
            pad_status: String::new(),
            help: false,
            help_scroll: 0,
            compact: false,
            quit: false,
            focused: true,
            glyphs,
            backup_suffix,
            pad_nav,
            els: Vec::new(),
            hits: Vec::new(),
            tx,
            pads: None,
        };
        app.refresh_uses();
        app
    }

    fn dirty(&self) -> bool {
        self.draft != self.saved
    }

    fn context(&self) -> Option<&'static str> {
        if self.ctx == 0 {
            None
        } else {
            Some(CONTEXTS[self.ctx - 1])
        }
    }

    fn kind(&self) -> &'static str {
        self.view.kind()
    }

    fn ki(&self) -> usize {
        kind_index(self.kind())
    }

    /// Layers of the current device kind; index 0 is "all".
    fn layer_list(&self, kind: &str) -> Vec<String> {
        let mut v = vec!["*".to_string()];
        v.extend(bindmap::layers_of(&self.uses, kind));
        v
    }

    fn current_layer(&self) -> Option<String> {
        let kind = self.kind();
        if kind.is_empty() {
            return None;
        }
        let list = self.layer_list(kind);
        let i = self.layer[kind_index(kind)].min(list.len() - 1);
        if i == 0 {
            None
        } else {
            Some(list[i].clone())
        }
    }

    fn cursor(&self) -> &str {
        &self.cursor[self.ki()]
    }

    fn refresh_uses(&mut self) {
        self.uses = bindmap::collect(&self.merged, &self.draft, self.lua.as_deref());
        let mut flagged = bindmap::conflicts(&self.uses);
        if let Some(o) = &self.outcome {
            let chords = bindmap::finding_chords(&o.findings);
            for (i, u) in self.uses.iter().enumerate() {
                let hit = chords.contains(&u.norm)
                    || match &u.source {
                        Source::Global { id, .. } => !o.mentions(id).is_empty(),
                        Source::Guide { button } => o
                            .findings
                            .iter()
                            .any(|f| f.contains(&format!("system_layer.{button}"))),
                        Source::Room { button } => o
                            .findings
                            .iter()
                            .any(|f| f.contains(&format!("room {button}"))),
                        Source::Tmux { .. } => {
                            o.findings.iter().any(|f| f.starts_with("tmux-os:"))
                                && self.saved.tmux != self.draft.tmux
                                && self.tmux_changed(u)
                        }
                        _ => false,
                    };
                if hit {
                    flagged.insert(i);
                }
            }
        }
        self.flagged = flagged;
    }

    fn tmux_changed(&self, u: &Use) -> bool {
        match &u.source {
            Source::Tmux { index } => self.saved.tmux.get(*index) != self.draft.tmux.get(*index),
            _ => false,
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

    /// Record an edit: the previous draft goes on the undo stack, redo is cleared, the map and checks refresh.
    fn edit(&mut self, before: Draft, what: String) {
        if before == self.draft {
            self.status = format!("{what} (no change)");
            return;
        }
        self.undo.push((before, what.clone()));
        if self.undo.len() > self.undo_depth {
            self.undo.remove(0);
        }
        self.redo.clear();
        self.status = what;
        self.refresh_uses();
        self.revalidate();
    }

    fn undo(&mut self) {
        match self.undo.pop() {
            Some((d, what)) => {
                let now = std::mem::replace(&mut self.draft, d);
                self.redo.push((now, what.clone()));
                self.status = format!("undid: {what}");
                self.refresh_uses();
                self.revalidate();
            }
            None => self.status = "nothing to undo".into(),
        }
    }

    fn redo(&mut self) {
        match self.redo.pop() {
            Some((d, what)) => {
                let now = std::mem::replace(&mut self.draft, d);
                self.undo.push((now, what.clone()));
                self.status = format!("redid: {what}");
                self.refresh_uses();
                self.revalidate();
            }
            None => self.status = "nothing to redo".into(),
        }
    }

    fn validated(&mut self, gen: u64, res: Result<Outcome, String>) {
        if gen != self.gen {
            return;
        }
        self.validating = false;
        match res {
            Ok(o) => {
                if o.lua.is_some() {
                    self.lua = o.lua.clone();
                }
                self.outcome = Some(o);
            }
            Err(e) => {
                self.outcome = None;
                self.status = format!("validation could not run: {e}");
            }
        }
        self.refresh_uses();
    }

    // ---- the uses panel ----------------------------------------------------------------------------------

    /// Indices of the uses the side panel lists: search results, or every use of the selected input (the current
    /// layer's first).
    fn panel_uses(&self) -> Vec<usize> {
        if let Some(q) = self.search.as_deref().filter(|q| !q.is_empty()) {
            return (0..self.uses.len())
                .filter(|i| self.uses[*i].matches(q))
                .collect();
        }
        if self.kind().is_empty() {
            return Vec::new();
        }
        let cur = self.cursor().to_owned();
        let layer = self.current_layer();
        let mut v: Vec<usize> = (0..self.uses.len())
            .filter(|i| self.uses[*i].input == cur)
            .collect();
        v.sort_by_key(|i| {
            let u = &self.uses[*i];
            (
                layer.as_deref().is_some_and(|l| l != u.layer),
                u.layer.clone(),
                !u.editable(),
            )
        });
        v
    }

    fn selected_use(&self) -> Option<Use> {
        let p = self.panel_uses();
        p.get(self.uses_sel.min(p.len().saturating_sub(1)))
            .map(|i| self.uses[*i].clone())
    }

    fn input_bound(&self, input: &str, layer: Option<&str>) -> bool {
        self.uses.iter().any(|u| {
            u.input == input
                && layer.is_none_or(|l| u.layer == l)
                && !matches!(u.source, Source::Reserved)
                && u.action != "the focused app (mios-inputd's virtual pad)"
        })
    }

    // ---- input -------------------------------------------------------------------------------------------

    pub fn handle(&mut self, ev: Event) {
        match ev {
            Event::Key(k) if k.kind != KeyEventKind::Release => self.key_event(k),
            Event::Mouse(m) => self.mouse(m),
            Event::FocusGained => self.focused = true,
            Event::FocusLost => self.focused = false,
            _ => {}
        }
    }

    fn modal(&self) -> bool {
        self.prompt.is_some()
            || self.confirm.is_some()
            || self.menu.is_some()
            || self.picker.is_some()
            || self.help
    }

    fn mouse(&mut self, m: MouseEvent) {
        if self.capture.is_some() {
            let input = match m.kind {
                MouseEventKind::Down(MouseButton::Left) => Some("mouse:272"),
                MouseEventKind::Down(MouseButton::Right) => Some("mouse:273"),
                MouseEventKind::Down(MouseButton::Middle) => Some("mouse:274"),
                MouseEventKind::ScrollUp => Some("mouse:wheel_up"),
                MouseEventKind::ScrollDown => Some("mouse:wheel_down"),
                MouseEventKind::ScrollLeft => Some("mouse:wheel_left"),
                MouseEventKind::ScrollRight => Some("mouse:wheel_right"),
                _ => None,
            };
            if let Some(i) = input {
                self.capture_done(i.to_owned(), Vec::new());
            }
            return;
        }
        if self.prompt.is_some() || self.confirm.is_some() {
            return;
        }
        let at = Position::new(m.column, m.row);
        let hit = self
            .hits
            .iter()
            .rev()
            .find(|(r, _)| r.contains(at))
            .map(|(_, h)| h.clone());
        match m.kind {
            MouseEventKind::Down(b @ (MouseButton::Left | MouseButton::Right)) => {
                let right = b == MouseButton::Right;
                if self.menu.is_some() || self.picker.is_some() || self.help {
                    match hit {
                        Some(Hit::MenuItem(i)) => {
                            if let Some(menu) = self.menu.as_mut() {
                                menu.sel = i;
                            }
                            self.key_event(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
                        }
                        Some(Hit::PickItem(i)) => {
                            if let Some(p) = self.picker.as_mut() {
                                p.sel = i;
                            }
                            self.key_event(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE));
                        }
                        _ => {
                            self.menu = None;
                            self.picker = None;
                            self.help = false;
                        }
                    }
                    return;
                }
                match hit {
                    Some(Hit::Tab(v)) => self.set_view(v),
                    Some(Hit::Layer(i)) => {
                        let ki = self.ki();
                        self.layer[ki] = i;
                    }
                    Some(Hit::Input(id)) => {
                        let again = self.cursor() == id && self.focus == Focus::Drawing;
                        let ki = self.ki();
                        self.cursor[ki] = id;
                        self.uses_sel = 0;
                        self.focus = Focus::Drawing;
                        if right {
                            self.open_menu();
                        } else if again {
                            self.act(Act::Enter);
                        }
                    }
                    Some(Hit::Use(i)) => {
                        let again = self.uses_sel == i && self.focus == Focus::Uses;
                        self.uses_sel = i;
                        self.focus = Focus::Uses;
                        if right || again {
                            self.open_menu();
                        }
                    }
                    Some(Hit::Row(i)) => {
                        self.sel = i;
                        if right {
                            self.open_menu();
                        }
                    }
                    Some(Hit::Device(i)) => {
                        let again = self.dev_sel == i;
                        self.dev_sel = i;
                        if again {
                            self.act(Act::Enter);
                        }
                    }
                    _ => {
                        if right {
                            self.open_menu();
                        }
                    }
                }
            }
            MouseEventKind::ScrollDown if !self.modal() => self.act(Act::Down),
            MouseEventKind::ScrollUp if !self.modal() => self.act(Act::Up),
            _ => {}
        }
    }

    /// Controller edges: capture first, then the editor's own navigation ([desktop.controls.pad_nav]); ignored while
    /// the editor's terminal does not have focus (the pad may be driving another window).
    fn pad(&mut self, e: PadEdge) {
        if !self.focused {
            return;
        }
        // the controller drawing follows the pad that was used last
        if e.down && e.input.starts_with("pad:") {
            if let Some(i) = self
                .devices
                .iter()
                .position(|d| d.kind == Kind::Controller && d.name == e.device)
            {
                self.pad_dev = Some(i);
            }
        }
        if self.capture.is_some() {
            if e.down && !e.repeat {
                self.status = format!("captured from {}", e.device);
                self.capture_done(e.input, Vec::new());
            }
            return;
        }
        if !e.down || e.input.starts_with("mouse:") {
            return;
        }
        let name = e.input.trim_start_matches("pad:");
        let act = match name {
            "BTN_DPAD_UP" | "LS_UP" => Some(Act::Up),
            "BTN_DPAD_DOWN" | "LS_DOWN" => Some(Act::Down),
            "BTN_DPAD_LEFT" | "LS_LEFT" => Some(Act::Left),
            "BTN_DPAD_RIGHT" | "LS_RIGHT" => Some(Act::Right),
            "RS_UP" => Some(Act::PageUp),
            "RS_DOWN" => Some(Act::PageDown),
            _ if e.repeat => None,
            b => self
                .pad_nav
                .iter()
                .find(|(k, _)| k == b)
                .and_then(|(_, v)| verb(v)),
        };
        if let Some(a) = act {
            self.act(a);
        }
    }

    /// Run an action from any input (keys map to these, the menu lists them, the pad sends them).
    pub fn act(&mut self, a: Act) {
        let key = |c: KeyCode| KeyEvent::new(c, KeyModifiers::NONE);
        if self.modal() {
            let k = match a {
                Act::Enter => key(KeyCode::Enter),
                Act::Back | Act::Menu => key(KeyCode::Esc),
                Act::Up => key(KeyCode::Up),
                Act::Down => key(KeyCode::Down),
                Act::Left => key(KeyCode::Left),
                Act::Right => key(KeyCode::Right),
                Act::PageUp => key(KeyCode::PageUp),
                Act::PageDown => key(KeyCode::PageDown),
                _ => return,
            };
            self.key_event(k);
            return;
        }
        match a {
            Act::Enter => self.enter(),
            Act::Back => self.back(),
            Act::Menu => self.open_menu(),
            Act::Capture => self.start_capture(),
            Act::FocusNext => self.focus_next(),
            Act::Help => {
                self.help = true;
                self.help_scroll = 0;
            }
            Act::LayerPrev | Act::LayerNext => {
                self.step_layer(if a == Act::LayerNext { 1 } else { -1 })
            }
            Act::ViewPrev | Act::ViewNext => {
                let i = VIEWS.iter().position(|v| *v == self.view).unwrap_or(0) as i64;
                let n = (i + if a == Act::ViewNext { 1 } else { -1 }).rem_euclid(VIEWS.len() as i64)
                    as usize;
                self.set_view(VIEWS[n]);
            }
            Act::PageUp => self.step(-8),
            Act::PageDown => self.step(8),
            Act::Up => self.nav(0, -1),
            Act::Down => self.nav(0, 1),
            Act::Left => self.nav(-1, 0),
            Act::Right => self.nav(1, 0),
            Act::Rebind => self.start_rebind(),
            Act::ChangeAction => self.change_action(),
            Act::NewBinding => self.new_binding(),
            Act::DeleteUse => {
                if self.view == View::Bindings {
                    self.confirm = Some(Confirm::Delete);
                } else if let Some(u) = self.selected_use() {
                    if u.editable() {
                        self.confirm = Some(Confirm::DeleteUse);
                    } else {
                        self.status = format!("read-only: {}", u.source.place());
                    }
                }
            }
            Act::Undo => self.undo(),
            Act::Redo => self.redo(),
            Act::Validate => {
                self.status = "validating".into();
                self.revalidate();
            }
            Act::Apply => self.request_apply(),
            Act::Revert => self.confirm = Some(Confirm::Revert),
            Act::Defaults => self.confirm = Some(Confirm::Restore),
            Act::Search => self.ask(
                Field::Search,
                "search keys, buttons, actions, sources",
                self.search.clone().unwrap_or_default().as_str(),
            ),
            Act::Family => {
                let n = self.families.len();
                self.family_override = Some(self.family_override.map(|i| (i + 1) % n).unwrap_or(0));
                self.status = format!("controller drawing: {}", self.family().title);
            }
            Act::Rescan => {
                self.devices = crate::devices::scan();
                self.dev_sel = self.dev_sel.min(self.devices.len().saturating_sub(1));
                if let Some(p) = self.pads.as_mut() {
                    p.send(PadCmd::Rescan);
                }
                self.status = format!("{} input device(s)", self.devices.len());
            }
            Act::Quit => {
                if self.dirty() {
                    self.confirm = Some(Confirm::Quit);
                } else {
                    self.quit = true;
                }
            }
        }
    }

    fn set_view(&mut self, v: View) {
        self.view = v;
        self.uses_sel = 0;
        if v != View::Bindings && v != View::Devices {
            self.focus = Focus::Drawing;
        }
    }

    fn focus_next(&mut self) {
        if self.kind().is_empty() {
            self.act(Act::ViewNext);
            return;
        }
        self.focus = if self.focus == Focus::Drawing {
            Focus::Uses
        } else {
            Focus::Drawing
        };
    }

    fn step_layer(&mut self, d: i64) {
        let kind = self.kind();
        if kind.is_empty() {
            return;
        }
        let n = self.layer_list(kind).len() as i64;
        let ki = kind_index(kind);
        self.layer[ki] = (self.layer[ki] as i64 + d).rem_euclid(n) as usize;
        self.uses_sel = 0;
    }

    fn step(&mut self, d: i64) {
        let mv = |cur: usize, len: usize| -> usize {
            if len == 0 {
                0
            } else {
                (cur as i64 + d).clamp(0, len as i64 - 1) as usize
            }
        };
        match self.view {
            View::Bindings => self.sel = mv(self.sel, self.draft.bindings.len()),
            View::Devices => self.dev_sel = mv(self.dev_sel, self.devices.len()),
            _ => {
                if self.focus == Focus::Uses || self.search.is_some() {
                    self.uses_sel = mv(self.uses_sel, self.panel_uses().len());
                } else {
                    self.nav(0, d.signum() as i32);
                }
            }
        }
    }

    fn nav(&mut self, dx: i32, dy: i32) {
        if matches!(self.view, View::Bindings | View::Devices)
            || self.focus == Focus::Uses
            || self.search.is_some()
        {
            if dy != 0 {
                self.step(dy as i64);
            } else if self.focus == Focus::Uses && dx < 0 {
                self.focus = Focus::Drawing;
            }
            return;
        }
        let cur = self.cursor().to_owned();
        let next = if self.els.iter().any(|e| e.input == cur) {
            layout::neighbour(&self.els, &cur, dx, dy)
        } else {
            self.els
                .iter()
                .find(|e| !e.input.is_empty())
                .map(|e| e.input.clone())
        };
        if let Some(n) = next {
            let ki = self.ki();
            self.cursor[ki] = n;
            self.uses_sel = 0;
        } else if dx > 0 {
            self.focus = Focus::Uses;
        }
    }

    fn enter(&mut self) {
        match self.view {
            View::Bindings => {
                if let Some(b) = self.draft.bindings.get(self.sel).cloned() {
                    self.ask(Field::Chord, "chord", field(&b, "chord"));
                }
            }
            View::Devices => {
                let Some(d) = self.devices.get(self.dev_sel).cloned() else {
                    return;
                };
                match d.kind {
                    Kind::Controller => {
                        self.pad_dev = Some(self.dev_sel);
                        self.family_override = None;
                        self.set_view(View::Controller);
                    }
                    Kind::Mouse => {
                        self.mouse_dev = Some(self.dev_sel);
                        self.set_view(View::Mouse);
                    }
                    Kind::Keyboard => self.set_view(View::Keyboard),
                }
                self.status = format!("profile: {}", d.name);
            }
            _ => {
                if let Some(p) = self.pending.clone() {
                    let target = self.cursor().to_owned();
                    self.finish_rebind(&p, &target, Vec::new());
                    return;
                }
                if self.focus == Focus::Drawing && self.search.is_none() {
                    if self.panel_uses().is_empty() {
                        self.open_menu();
                    } else {
                        self.focus = Focus::Uses;
                        self.uses_sel = 0;
                    }
                } else if let Some(u) = self.selected_use() {
                    if self.search.is_some() {
                        // jump to the result's input on its own drawing
                        let view = view_of(u.kind());
                        let ki = kind_index(u.kind());
                        self.search = None;
                        self.view = view;
                        self.cursor[ki] = u.input.clone();
                        let list = self.layer_list(u.kind());
                        self.layer[ki] = list.iter().position(|l| *l == u.layer).unwrap_or(0);
                        self.focus = Focus::Uses;
                        self.uses_sel = self
                            .panel_uses()
                            .iter()
                            .position(|i| self.uses[*i] == u)
                            .unwrap_or(0);
                        self.status = format!("{} on the {} drawing", u.chord, view.title());
                    } else {
                        self.open_menu();
                    }
                }
            }
        }
    }

    fn back(&mut self) {
        if self.pending.take().is_some() {
            self.status = "rebind cancelled".into();
        } else if self.search.take().is_some() {
            self.status = "search cleared".into();
        } else if self.focus == Focus::Uses && !self.kind().is_empty() {
            self.focus = Focus::Drawing;
        } else {
            self.act(Act::Quit);
        }
    }

    fn key_event(&mut self, k: KeyEvent) {
        if k.code == KeyCode::Char('c')
            && k.modifiers.contains(KeyModifiers::CONTROL)
            && self.prompt.is_none()
            && self.capture.is_none()
        {
            self.quit = true;
            return;
        }
        if self.capture.is_some() {
            if k.code == KeyCode::Esc {
                self.stop_capture();
                self.status = "capture cancelled".into();
                return;
            }
            if let Some((input, mods)) = key_input(&k) {
                self.capture_done(input, mods);
            }
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
        if let Some(p) = self.picker.as_mut() {
            match k.code {
                KeyCode::Up => p.sel = p.sel.saturating_sub(1),
                KeyCode::Down => p.sel = (p.sel + 1).min(p.items.len().saturating_sub(1)),
                KeyCode::PageUp => p.sel = p.sel.saturating_sub(8),
                KeyCode::PageDown => p.sel = (p.sel + 8).min(p.items.len().saturating_sub(1)),
                KeyCode::Enter => {
                    let p = self.picker.take().unwrap();
                    let item = p.items.get(p.sel).cloned().unwrap_or_default();
                    self.picked(p.then, &item);
                }
                KeyCode::Esc | KeyCode::Left => self.picker = None,
                _ => {}
            }
            return;
        }
        if let Some(m) = self.menu.as_mut() {
            match k.code {
                KeyCode::Up => m.sel = m.sel.saturating_sub(1),
                KeyCode::Down => m.sel = (m.sel + 1).min(m.items.len().saturating_sub(1)),
                KeyCode::PageUp | KeyCode::Home => m.sel = 0,
                KeyCode::PageDown | KeyCode::End => m.sel = m.items.len().saturating_sub(1),
                KeyCode::Enter | KeyCode::Right => {
                    let a = m.items.get(m.sel).map(|(_, a)| *a);
                    self.menu = None;
                    if let Some(a) = a {
                        self.act(a);
                    }
                }
                _ => self.menu = None,
            }
            return;
        }
        if self.help {
            match k.code {
                KeyCode::Down | KeyCode::Char('j') => {
                    self.help_scroll = self.help_scroll.saturating_add(1)
                }
                KeyCode::Up | KeyCode::Char('k') => {
                    self.help_scroll = self.help_scroll.saturating_sub(1)
                }
                KeyCode::PageDown => self.help_scroll = self.help_scroll.saturating_add(8),
                KeyCode::PageUp => self.help_scroll = self.help_scroll.saturating_sub(8),
                _ => self.help = false,
            }
            return;
        }
        let ctrl = k.modifiers.contains(KeyModifiers::CONTROL);
        match k.code {
            KeyCode::Char('q') => self.act(Act::Quit),
            KeyCode::Esc => self.act(Act::Back),
            KeyCode::Char('?') | KeyCode::F(1) => self.act(Act::Help),
            KeyCode::Tab => self.act(Act::FocusNext),
            KeyCode::BackTab => self.act(Act::FocusNext),
            KeyCode::Char(c @ '1'..='5') if !ctrl => {
                self.set_view(VIEWS[(c as u8 - b'1') as usize])
            }
            KeyCode::Char('>') => self.act(Act::ViewNext),
            KeyCode::Char('<') => self.act(Act::ViewPrev),
            KeyCode::Char(']') => self.ctx = (self.ctx + 1) % (CONTEXTS.len() + 1),
            KeyCode::Char('[') => self.ctx = (self.ctx + CONTEXTS.len()) % (CONTEXTS.len() + 1),
            KeyCode::Char('l') => self.act(Act::LayerNext),
            KeyCode::Char('L') => self.act(Act::LayerPrev),
            KeyCode::Char('/') => self.act(Act::Search),
            KeyCode::Char('m') | KeyCode::Menu => self.act(Act::Menu),
            KeyCode::Char('v') => self.act(Act::Validate),
            KeyCode::Char('w') => self.act(Act::Apply),
            KeyCode::Char('R') => self.act(Act::Defaults),
            KeyCode::Char('u') => self.act(Act::Undo),
            KeyCode::Char('U') => self.act(Act::Redo),
            KeyCode::Char('y') if ctrl => self.act(Act::Redo),
            KeyCode::Char('z') => self.act(Act::Revert),
            KeyCode::Char('e') => self.ask(Field::Export, "export the binding map to file", ""),
            KeyCode::Char('i') => self.ask(Field::Import, "import a binding map from file", ""),
            KeyCode::Char('D') => self.act(Act::Rescan),
            KeyCode::Up => self.act(Act::Up),
            KeyCode::Down => self.act(Act::Down),
            KeyCode::Left => self.act(Act::Left),
            KeyCode::Right => self.act(Act::Right),
            KeyCode::PageUp => self.act(Act::PageUp),
            KeyCode::PageDown => self.act(Act::PageDown),
            KeyCode::Home => self.step(-100_000),
            KeyCode::End => self.step(100_000),
            KeyCode::Enter => self.act(Act::Enter),
            _ => match self.view {
                View::Bindings => self.binding_key(k),
                View::Devices => {}
                _ => match k.code {
                    KeyCode::Char('c') => self.act(Act::Capture),
                    KeyCode::Char('r') => self.act(Act::Rebind),
                    KeyCode::Char('a') => self.act(Act::ChangeAction),
                    KeyCode::Char('n') => self.act(Act::NewBinding),
                    KeyCode::Char('d') | KeyCode::Delete => self.act(Act::DeleteUse),
                    KeyCode::Char('f') => self.act(Act::Family),
                    _ => {}
                },
            },
        }
    }

    fn binding_key(&mut self, k: KeyEvent) {
        let Some(b) = self.draft.bindings.get(self.sel).cloned() else {
            if k.code == KeyCode::Char('n') {
                self.ask(Field::NewId, "new binding id [a-z0-9-]", "");
            }
            return;
        };
        let list = |key: &str| str_list(b.get(key)).join(",");
        match k.code {
            KeyCode::Char('c') => self.ask(Field::Chord, "chord", field(&b, "chord")),
            KeyCode::Char('a') => self.ask(
                Field::Action,
                "action <namespace>:<verb>",
                field(&b, "action"),
            ),
            KeyCode::Char('x') => self.ask(
                Field::Contexts,
                "contexts (comma list; empty = all)",
                &list("contexts"),
            ),
            KeyCode::Char('p') => self.ask(
                Field::Passthrough,
                "passthrough contexts (comma list)",
                &list("passthrough"),
            ),
            KeyCode::Char('b') => self.ask(
                Field::Bypass,
                "bypass chord (empty = none)",
                field(&b, "bypass"),
            ),
            KeyCode::Char('t') => {
                let tier = b
                    .get("tier")
                    .and_then(Value::as_integer)
                    .unwrap_or(1)
                    .to_string();
                self.ask(Field::Tier, "tier 1..4", &tier)
            }
            KeyCode::Char('n') => self.ask(Field::NewId, "new binding id [a-z0-9-]", ""),
            KeyCode::Char('d') | KeyCode::Delete => self.confirm = Some(Confirm::Delete),
            _ => {}
        }
    }

    fn ask(&mut self, field: Field, label: &str, cur: &str) {
        self.prompt = Some(Prompt {
            field,
            label: label.into(),
            buf: cur.into(),
        });
    }

    fn prompt_key(&mut self, k: KeyEvent) {
        let p = self.prompt.as_mut().unwrap();
        let live = p.field == Field::Search;
        match k.code {
            KeyCode::Esc => {
                self.prompt = None;
                if live {
                    self.search = None;
                }
                return;
            }
            KeyCode::Enter => {
                let p = self.prompt.take().unwrap();
                if let Err(e) = self.commit(p.field, p.buf.trim()) {
                    self.status = e;
                }
                return;
            }
            KeyCode::Backspace => {
                p.buf.pop();
            }
            KeyCode::Char('u') if k.modifiers.contains(KeyModifiers::CONTROL) => p.buf.clear(),
            KeyCode::Char(c) => p.buf.push(c),
            _ => {}
        }
        if live {
            let q = self
                .prompt
                .as_ref()
                .map(|p| p.buf.clone())
                .unwrap_or_default();
            self.search = Some(q);
            self.uses_sel = 0;
        }
    }

    fn commit(&mut self, f: Field, v: &str) -> Result<(), String> {
        let list = |v: &str| -> Value {
            Value::Array(
                v.split(',')
                    .map(str::trim)
                    .filter(|s| !s.is_empty())
                    .map(|s| Value::String(s.into()))
                    .collect(),
            )
        };
        let editable = self.layers.editable();
        match f {
            Field::Search => {
                self.search = if v.is_empty() {
                    None
                } else {
                    Some(v.to_owned())
                };
                self.uses_sel = 0;
                self.focus = Focus::Uses;
                self.status = format!(
                    "{} match(es); Enter jumps to one, Esc clears",
                    self.panel_uses().len()
                );
                return Ok(());
            }
            Field::Export => {
                let text = crate::model::keybindings_fragment(&self.draft, &editable)?;
                crate::write_private_new(std::path::Path::new(v), &text)?;
                self.status = format!("exported the binding map to {v}");
                return Ok(());
            }
            Field::Import => {
                let text = std::fs::read_to_string(v).map_err(|e| format!("{v}: {e}"))?;
                let before = self.draft.clone();
                self.draft = crate::model::import_fragment(&text, &self.draft, &editable)?;
                self.sel = 0;
                self.edit(before, format!("imported {v} into the draft (not applied)"));
                return Ok(());
            }
            Field::UseAction => {
                if let Some(u) = self.selected_use() {
                    return self.set_use_action(&u, v);
                }
                return Err("no use selected".into());
            }
            Field::NewAction => {
                let target = self.cursor().to_owned();
                let layer = self.current_layer().unwrap_or_default();
                return self.create_on(&target, &layer, v);
            }
            Field::TmuxCommand => {
                if let Some(u) = self.selected_use() {
                    if let Source::Tmux { index } = u.source {
                        let before = self.draft.clone();
                        let b = self
                            .draft
                            .tmux
                            .get_mut(index)
                            .ok_or("no such tmux-os key")?;
                        b.remove("menu");
                        b.insert("command".into(), Value::String(v.into()));
                        self.edit(before, format!("tmux-os {}: command = {v}", u.chord));
                        return Ok(());
                    }
                }
                return Err("no tmux-os key selected".into());
            }
            Field::NewId => {
                if v.is_empty()
                    || !v
                        .bytes()
                        .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
                {
                    return Err("id must be non-empty [a-z0-9-]".into());
                }
                if self.draft.index(v).is_some() {
                    return Err(format!("binding {v} already exists"));
                }
                let before = self.draft.clone();
                let mut b = toml::Table::new();
                b.insert("id".into(), Value::String(v.into()));
                b.insert("tier".into(), Value::Integer(1));
                b.insert("chord".into(), Value::String(String::new()));
                b.insert("action".into(), Value::String(String::new()));
                b.insert("passthrough".into(), list("vm-passthrough"));
                self.draft.bindings.push(b);
                self.sel = self.draft.bindings.len() - 1;
                self.edit(
                    before,
                    format!("new binding {v}: set its chord, then its action (a)"),
                );
                self.ask(Field::Chord, "chord", "");
                return Ok(());
            }
            _ => {}
        }
        let before = self.draft.clone();
        let sep = self.sep.clone();
        let b = self
            .draft
            .bindings
            .get_mut(self.sel)
            .ok_or("no binding selected")?;
        let id = field(b, "id").to_owned();
        match f {
            Field::Chord => {
                mios_resolver::keybindings::normalize_chord(v, &sep)?;
                b.insert("chord".into(), Value::String(v.into()));
            }
            Field::Action => {
                b.insert("action".into(), Value::String(v.into()));
            }
            Field::Bypass if v.is_empty() => {
                b.remove("bypass");
            }
            Field::Bypass => {
                mios_resolver::keybindings::normalize_chord(v, &sep)?;
                b.insert("bypass".into(), Value::String(v.into()));
            }
            Field::Contexts if v.is_empty() => {
                b.remove("contexts");
            }
            Field::Contexts | Field::Passthrough => {
                let key = if f == Field::Contexts {
                    "contexts"
                } else {
                    "passthrough"
                };
                if let Some(bad) = v
                    .split(',')
                    .map(str::trim)
                    .find(|c| !c.is_empty() && !CONTEXTS.contains(c))
                {
                    return Err(format!(
                        "unknown context {bad:?}; one of {}",
                        CONTEXTS.join(",")
                    ));
                }
                b.insert(key.into(), list(v));
            }
            Field::Tier => {
                let t: i64 = v.parse().map_err(|_| "tier must be 1..4".to_string())?;
                b.insert("tier".into(), Value::Integer(t));
            }
            _ => {}
        }
        let what = match f {
            Field::Chord => "chord",
            Field::Action => "action",
            Field::Bypass => "bypass",
            Field::Contexts => "contexts",
            Field::Passthrough => "passthrough",
            _ => "tier",
        };
        self.edit(before, format!("{id}: {what} = {v}"));
        Ok(())
    }

    // ---- capture, rebind, actions ------------------------------------------------------------------------

    fn start_capture(&mut self) {
        if self.kind().is_empty() {
            self.status = "capture works on the keyboard, mouse and controller views".into();
            return;
        }
        self.capture = Some(Instant::now());
        if let Some(p) = self.pads.as_mut() {
            p.send(PadCmd::Capture(true));
        }
        self.status = format!(
            "{}press the key, mouse button, wheel, controller button or stick to choose it (Esc cancels, {} s)",
            if self.pending.is_some() { "rebind: " } else { "" },
            self.capture_timeout.as_secs()
        );
    }

    fn stop_capture(&mut self) {
        self.capture = None;
        if let Some(p) = self.pads.as_mut() {
            p.send(PadCmd::Capture(false));
        }
    }

    /// Give up a capture that ran past [desktop.controls].capture_timeout_ms.
    pub fn tick(&mut self) {
        if self
            .capture
            .is_some_and(|t| t.elapsed() >= self.capture_timeout)
        {
            self.stop_capture();
            self.status = "capture timed out: nothing was pressed".into();
        }
    }

    fn capture_timeout_left(&self) -> Option<Duration> {
        self.capture
            .map(|t| self.capture_timeout.saturating_sub(t.elapsed()))
    }

    /// A captured input: select it on its drawing, and finish a pending rebind onto it.
    fn capture_done(&mut self, input: String, mods: Vec<String>) {
        self.stop_capture();
        let kind = input.split(':').next().unwrap_or("key").to_owned();
        let ki = kind_index(&kind);
        self.view = view_of(&kind);
        self.cursor[ki] = input.clone();
        self.uses_sel = 0;
        self.focus = Focus::Drawing;
        self.last_captured = Some(input.clone());
        let what = if mods.is_empty() {
            input.clone()
        } else {
            format!("{}+{}", mods.join("+"), input)
        };
        if let Some(p) = self.pending.clone() {
            self.finish_rebind(&p, &input, mods);
        } else {
            let n = self.panel_uses().len();
            self.status = format!("captured {what}: {n} use(s)");
        }
    }

    fn start_rebind(&mut self) {
        let Some(u) = self.selected_use() else {
            self.status = "select a use in the list first (Tab or Enter)".into();
            return;
        };
        if !u.editable() {
            self.status = format!("read-only here: change it in {}", u.source.place());
            return;
        }
        self.status = format!(
            "rebind {} {}: move to the new input and press Enter, or press c and then the input",
            u.source.word(),
            u.chord
        );
        self.pending = Some(u);
        self.focus = Focus::Drawing;
    }

    /// The modifiers and earlier steps a rebind onto a key/mouse input keeps: the selected layer, else the use's own.
    fn target_layer(&self, u: &Use) -> String {
        match self.current_layer() {
            Some(l) if !l.starts_with("pad:") && !l.starts_with("tmux-os") => l,
            _ => u.layer.clone(),
        }
    }

    fn finish_rebind(&mut self, u: &Use, target: &str, extra: Vec<String>) {
        let before = self.draft.clone();
        let layer = self.target_layer(u);
        match self.rebind(u, target, &layer, &extra) {
            Ok(what) => {
                self.pending = None;
                self.edit(before, what);
            }
            Err(e) => {
                self.draft = before;
                self.status = format!("rebind refused: {e}");
            }
        }
    }

    /// Move a use onto `target` within the draft (the caller records undo). Global bindings keep their compositor
    /// realization in step; Guide-layer and room entries swap with the target button.
    pub fn rebind(
        &mut self,
        u: &Use,
        target: &str,
        layer: &str,
        extra: &[String],
    ) -> Result<String, String> {
        let sep = self.sep.clone();
        let (tkind, tname) = target.split_once(':').ok_or("bad input")?;
        let chord_for = |table_style: bool| -> Result<String, String> {
            let (pre, mut mods) = parse_layer(layer);
            for m in extra {
                if !mods.contains(m) {
                    mods.push(m.clone());
                }
            }
            let key = match tkind {
                "key" if bindmap::MODS.contains(&tname) => {
                    return Err("pick a key, not a modifier".into())
                }
                "key" => tname.to_owned(),
                "mouse" => layout::mouse_token(target).ok_or("bad mouse input")?,
                _ => return Err("pick a key or mouse input".into()),
            };
            let key = if table_style && key.len() == 1 {
                key.to_ascii_uppercase()
            } else {
                key
            };
            let mut last = order_mods(&mods);
            last.push(key);
            let step = last.join(if table_style { " + " } else { "+" });
            let mut steps = pre;
            steps.push(step);
            Ok(steps.join(if table_style { " " } else { sep.as_str() }))
        };
        match &u.source {
            Source::Global { id, bypass } => {
                let i = self
                    .draft
                    .index(id)
                    .ok_or_else(|| format!("no binding {id}"))?;
                let new = if tkind == "pad" {
                    if !tname.starts_with("BTN_") {
                        return Err("a binding chord takes buttons, not stick directions".into());
                    }
                    tname.to_owned()
                } else {
                    chord_for(false)?
                };
                mios_resolver::keybindings::normalize_chord(&new, &sep)?;
                let old_norm = u.norm.clone();
                let key = if *bypass { "bypass" } else { "chord" };
                self.draft.bindings[i].insert(key.into(), Value::String(new.clone()));
                let realized =
                    field(&self.draft.bindings[i], "realized_by") == "hyprland" && !*bypass;
                if realized {
                    // the compositor row that realizes it moves too (in the table's own "MOD + Key" style)
                    let editable = self.layers.editable();
                    let style = if tkind == "pad" {
                        new.clone()
                    } else {
                        chord_for(true)?
                    };
                    let mut moved = false;
                    for (path, entries) in self.draft.tables.iter_mut() {
                        for e in entries.iter_mut() {
                            if bindmap::split_chord(field(e, "key"), "\0")
                                .map(|(p, m, k)| norm_of(&p, &m, &k))
                                == Some(old_norm.clone())
                            {
                                if !editable.contains(path) {
                                    return Err(format!(
                                        "{id} is realized by [{path}], which is not editable here"
                                    ));
                                }
                                e.insert("key".into(), Value::String(style.clone()));
                                moved = true;
                            }
                        }
                    }
                    if !moved {
                        return Err(format!("{id} is realized by the compositor session's own bind ({}), which this editor cannot move; change it there", u.chord));
                    }
                }
                Ok(format!("{id}: {key} {} -> {new}", u.chord))
            }
            Source::Table { path, index } => {
                let new = chord_for(true)?;
                mios_resolver::keybindings::normalize_chord(&new, "\0")?;
                let old_norm = u.norm.clone();
                let e = self
                    .draft
                    .tables
                    .get_mut(path)
                    .and_then(|v| v.get_mut(*index))
                    .ok_or("no such table entry")?;
                e.insert("key".into(), Value::String(new.clone()));
                // a global binding this entry realizes moves with it
                let compact = chord_for(false)?;
                for b in self.draft.bindings.iter_mut() {
                    if field(b, "realized_by") == "hyprland"
                        && bindmap::split_chord(field(b, "chord"), &sep)
                            .map(|(p, m, k)| norm_of(&p, &m, &k))
                            == Some(old_norm.clone())
                    {
                        b.insert("chord".into(), Value::String(compact.clone()));
                    }
                }
                Ok(format!(
                    "{}: {} -> {new}",
                    if u.label.is_empty() {
                        path.as_str()
                    } else {
                        u.label.as_str()
                    },
                    u.chord
                ))
            }
            Source::Guide { button } | Source::Room { button } => {
                if tkind != "pad" || !tname.starts_with("BTN_") {
                    return Err(
                        "pick a controller button (stick directions are not bindable here)".into(),
                    );
                }
                let guide = at(&self.merged, "keybindings.global.controller.guide_button")
                    .and_then(Value::as_str)
                    .unwrap_or("BTN_MODE");
                let room = matches!(u.source, Source::Room { .. });
                if !room && tname == guide {
                    return Err(format!("{guide} is the Guide layer's own modifier"));
                }
                if tname == button {
                    return Err("that is the same button".into());
                }
                if room {
                    let a = self
                        .draft
                        .room
                        .get(button)
                        .cloned()
                        .ok_or("no room entry")?;
                    let b = self.draft.room.insert(tname.into(), a);
                    match b {
                        Some(b) => {
                            self.draft.room.insert(button.clone(), b);
                        }
                        None if self.layers.removable_room(button) => {
                            self.draft.room.remove(button);
                        }
                        None => {
                            self.draft
                                .room
                                .insert(button.clone(), Value::String("none".into()));
                        }
                    }
                    Ok(format!("room {button} -> {tname}"))
                } else {
                    let a = self
                        .draft
                        .system_layer
                        .get(button)
                        .cloned()
                        .ok_or("no Guide-layer entry")?;
                    let b = self.draft.system_layer.get(tname).cloned();
                    match b {
                        Some(b) => {
                            self.draft.system_layer.insert(tname.into(), a);
                            self.draft.system_layer.insert(button.clone(), b);
                            Ok(format!("Guide + {button} <-> Guide + {tname} (swapped)"))
                        }
                        None if self.layers.removable_button(button) => {
                            self.draft.system_layer.remove(button);
                            self.draft.system_layer.insert(tname.into(), a);
                            Ok(format!("Guide + {button} -> Guide + {tname}"))
                        }
                        None => Err(format!("Guide + {button} is set below your tier and cannot be emptied; move it onto a button that has an action (they swap)")),
                    }
                }
            }
            Source::Tmux { index } => {
                if tkind != "key" {
                    return Err("tmux-os prefix keys are keyboard keys".into());
                }
                let (_, mods) = parse_layer(layer);
                let mut mods: Vec<String> = mods.into_iter().chain(extra.iter().cloned()).collect();
                mods.dedup();
                let shift = mods.iter().any(|m| m == "SHIFT");
                if mods.iter().any(|m| m != "SHIFT") {
                    return Err("a tmux-os prefix key takes no modifier except Shift".into());
                }
                let key = layout::key_to_tmux(tname, shift)
                    .ok_or_else(|| format!("tmux-os cannot bind {tname} after the prefix"))?;
                let e = self
                    .draft
                    .tmux
                    .get_mut(*index)
                    .ok_or("no such tmux-os key")?;
                e.insert("key".into(), Value::String(key.clone()));
                Ok(format!("tmux-os {} -> prefix {key}", u.chord))
            }
            other => Err(format!("read-only here: change it in {}", other.place())),
        }
    }

    fn known_actions(&self, u: Option<&Use>) -> Vec<String> {
        let mut set = BTreeSet::new();
        let room = matches!(u.map(|u| &u.source), Some(Source::Room { .. }));
        if room {
            set.insert("none".to_string());
            for v in self.draft.room.values() {
                match v {
                    Value::String(s) => {
                        set.insert(s.clone());
                    }
                    Value::Array(a) => {
                        for x in a.iter().filter_map(Value::as_str) {
                            set.insert(x.to_owned());
                        }
                    }
                    _ => {}
                }
            }
            for v in [
                "hypr:hypr3d:cancel",
                "hypr:hypr3d:sprint",
                "hypr:hypr3d:recenter",
                "hypr:hypr3d:view",
            ] {
                set.insert(v.to_owned());
            }
        } else {
            for k in ["action_argv", "native_actions"] {
                if let Some(t) =
                    at(&self.merged, &format!("keybindings.global.{k}")).and_then(Value::as_table)
                {
                    set.extend(t.keys().cloned());
                }
            }
            for b in &self.draft.bindings {
                if !field(b, "action").is_empty() && field(b, "scope") != "app" {
                    set.insert(field(b, "action").to_owned());
                }
            }
            set.extend(self.draft.system_layer.values().cloned());
            set.extend(str_list(at(
                &self.merged,
                "keybindings.global.planned_actions",
            )));
        }
        let mut v: Vec<String> = set.into_iter().collect();
        v.insert(0, "type an action...".into());
        v
    }

    fn change_action(&mut self) {
        let Some(u) = self.selected_use() else {
            self.status = "select a use in the list first".into();
            return;
        };
        match &u.source {
            Source::Global { .. } | Source::Guide { .. } | Source::Room { .. } => {
                let items = self.known_actions(Some(&u));
                let cur = items.iter().position(|a| *a == u.action).unwrap_or(0);
                self.picker = Some(Picker { title: format!("action for {}", u.chord), items, sel: cur, then: PickThen::ChangeAction(u) });
            }
            Source::Tmux { .. } => self.ask(Field::TmuxCommand, &format!("tmux-os command for {}", u.chord), &u.action),
            Source::Table { path, .. } => self.status = format!("the compositor table's dispatcher is edited in [{path}]; this editor moves its key (r)"),
            other => self.status = format!("read-only here: change it in {}", other.place()),
        }
    }

    fn picked(&mut self, then: PickThen, item: &str) {
        if item == "type an action..." {
            match then {
                PickThen::ChangeAction(u) => self.ask(
                    Field::UseAction,
                    &format!("action for {}", u.chord),
                    &u.action,
                ),
                PickThen::NewOn(..) => self.ask(Field::NewAction, "action <namespace>:<verb>", ""),
            }
            return;
        }
        let res = match then {
            PickThen::ChangeAction(u) => self.set_use_action(&u, item),
            PickThen::NewOn(input, layer) => self.create_on(&input, &layer, item),
        };
        if let Err(e) = res {
            self.status = e;
        }
    }

    fn set_use_action(&mut self, u: &Use, action: &str) -> Result<(), String> {
        if action.is_empty() {
            return Err("empty action".into());
        }
        let before = self.draft.clone();
        match &u.source {
            Source::Global { id, .. } => {
                let i = self.draft.index(id).ok_or("no such binding")?;
                self.draft.bindings[i].insert("action".into(), Value::String(action.into()));
            }
            Source::Guide { button } => {
                self.draft
                    .system_layer
                    .insert(button.clone(), action.into());
            }
            Source::Room { button } => {
                self.draft
                    .room
                    .insert(button.clone(), Value::String(action.into()));
            }
            other => return Err(format!("read-only here: change it in {}", other.place())),
        }
        self.edit(before, format!("{}: action = {action}", u.chord));
        Ok(())
    }

    fn new_binding(&mut self) {
        if self.kind().is_empty() {
            return;
        }
        let input = self.cursor().to_owned();
        let layer = self.current_layer().unwrap_or_else(|| {
            if self.kind() == "pad" {
                PAD_GUIDE.to_string()
            } else {
                "SUPER".to_string()
            }
        });
        if layer.contains(" then") {
            self.ask(
                Field::NewAction,
                &format!(
                    "tmux-os command for prefix + {}",
                    input.trim_start_matches("key:")
                ),
                "",
            );
            return;
        }
        let items = self.known_actions(None);
        self.picker = Some(Picker {
            title: format!("new: {} on {}", layer_title(&layer), input),
            items,
            sel: 1.min(self.known_actions(None).len() - 1),
            then: PickThen::NewOn(input, layer),
        });
    }

    /// A new use of `input` on `layer`: a Guide-layer or room entry for pad layers, a tmux-os prefix key for a prefix
    /// layer, else a new [keybindings.global] binding.
    fn create_on(&mut self, input: &str, layer: &str, action: &str) -> Result<(), String> {
        if action.is_empty() {
            return Err("empty action".into());
        }
        let before = self.draft.clone();
        let (kind, name) = input.split_once(':').ok_or("bad input")?;
        let what = if layer == PAD_GUIDE || layer == PAD_ROOM {
            if kind != "pad" || !name.starts_with("BTN_") {
                return Err("pick a controller button".into());
            }
            if layer == PAD_GUIDE {
                self.draft.system_layer.insert(name.into(), action.into());
                format!("Guide + {name} = {action}")
            } else {
                self.draft
                    .room
                    .insert(name.into(), Value::String(action.into()));
                format!("room {name} = {action}")
            }
        } else if self.is_tmux_layer(layer) {
            let (_, mods) = parse_layer(layer);
            let key = layout::key_to_tmux(name, mods.iter().any(|m| m == "SHIFT"))
                .ok_or("tmux-os cannot bind that key after the prefix")?;
            let mut t = toml::Table::new();
            t.insert("key".into(), Value::String(key.clone()));
            t.insert("command".into(), Value::String(action.into()));
            t.insert("note".into(), Value::String(action.into()));
            self.draft.tmux.push(t);
            format!("tmux-os prefix {key} = {action}")
        } else {
            let base = format!(
                "user-{}",
                name.chars()
                    .map(|c| if c.is_ascii_alphanumeric() {
                        c.to_ascii_lowercase()
                    } else {
                        '-'
                    })
                    .collect::<String>()
            )
            .trim_matches('-')
            .to_owned();
            let mut id = base.clone();
            let mut n = 2;
            while self.draft.index(&id).is_some() {
                id = format!("{base}-{n}");
                n += 1;
            }
            let mut b = toml::Table::new();
            b.insert("id".into(), Value::String(id.clone()));
            b.insert(
                "tier".into(),
                Value::Integer(if kind == "pad" { 4 } else { 1 }),
            );
            b.insert("chord".into(), Value::String(String::new()));
            b.insert("action".into(), Value::String(action.into()));
            b.insert(
                "passthrough".into(),
                Value::Array(vec![Value::String("vm-passthrough".into())]),
            );
            self.draft.bindings.push(b);
            let u = Use {
                input: input.into(),
                layer: layer.into(),
                chord: String::new(),
                norm: String::new(),
                action: action.into(),
                label: String::new(),
                source: Source::Global {
                    id: id.clone(),
                    bypass: false,
                },
                contexts: Vec::new(),
                note: String::new(),
            };
            let layer = if layer.starts_with("pad:") {
                String::new()
            } else {
                layer.to_owned()
            };
            match self.rebind(&u, input, &layer, &[]) {
                Ok(_) => {}
                Err(e) => {
                    self.draft = before;
                    return Err(e);
                }
            }
            let chord = self
                .draft
                .bindings
                .last()
                .map(|b| field(b, "chord").to_owned())
                .unwrap_or_default();
            format!("new binding {id}: {chord} = {action}")
        };
        self.edit(before, what);
        Ok(())
    }

    fn is_tmux_layer(&self, layer: &str) -> bool {
        let prefix = at(&self.merged, "keybindings.tmux_prefix")
            .and_then(Value::as_str)
            .unwrap_or("");
        let pc = layout::tmux_to_key(prefix).map(|(m, k, s)| {
            let mut v: Vec<String> = m.iter().map(|x| x.to_string()).collect();
            if s {
                v.push("SHIFT".into());
            }
            v.push(k);
            v.join("+")
        });
        pc.is_some_and(|pc| layer.starts_with(&format!("{pc} then")))
    }

    fn delete_use(&mut self) {
        let Some(u) = self.selected_use() else { return };
        let before = self.draft.clone();
        let res: Result<String, String> = match &u.source {
            Source::Global { id, bypass } => match self.draft.index(id) {
                Some(i) if *bypass => {
                    self.draft.bindings[i].remove("bypass");
                    Ok(format!("{id}: bypass removed"))
                }
                Some(i) => {
                    self.draft.bindings.remove(i);
                    Ok(format!("deleted binding {id}"))
                }
                None => Err("no such binding".into()),
            },
            Source::Guide { button } => {
                if self.layers.removable_button(button) {
                    self.draft.system_layer.remove(button);
                    Ok(format!("removed Guide + {button}"))
                } else {
                    Err(format!("Guide + {button} is set below your tier; rebind or change it instead (a higher tier cannot remove it)"))
                }
            }
            Source::Room { button } => {
                if self.layers.removable_room(button) {
                    self.draft.room.remove(button);
                } else {
                    self.draft
                        .room
                        .insert(button.clone(), Value::String("none".into()));
                }
                Ok(format!("room {button}: unbound"))
            }
            Source::Tmux { index } => {
                if *index < self.draft.tmux.len() {
                    self.draft.tmux.remove(*index);
                    Ok(format!("tmux-os {} removed", u.chord))
                } else {
                    Err("no such tmux-os key".into())
                }
            }
            Source::Table { path, index } => match self.draft.tables.get_mut(path) {
                Some(v) if *index < v.len() => {
                    v.remove(*index);
                    Ok(format!("[{path}] {} removed", u.chord))
                }
                _ => Err("no such entry".into()),
            },
            other => Err(format!("read-only here: change it in {}", other.place())),
        };
        match res {
            Ok(w) => {
                self.uses_sel = 0;
                self.edit(before, w)
            }
            Err(e) => self.status = e,
        }
    }

    fn open_menu(&mut self) {
        let mut items: Vec<(String, Act)> = Vec::new();
        let u = if self.kind().is_empty() {
            None
        } else {
            self.selected_use()
                .filter(|_| self.focus == Focus::Uses || self.search.is_some())
        };
        if let Some(u) = &u {
            if u.editable() {
                items.push((format!("Rebind {} to another input", u.chord), Act::Rebind));
                items.push((
                    format!("Change the action of {}", u.chord),
                    Act::ChangeAction,
                ));
                items.push((format!("Delete {}", u.chord), Act::DeleteUse));
            }
        }
        if !self.kind().is_empty() {
            items.push(("Capture: press an input to select it".into(), Act::Capture));
            items.push((format!("New binding on {}", self.cursor()), Act::NewBinding));
            items.push(("Next layer".into(), Act::LayerNext));
            items.push(("Previous layer".into(), Act::LayerPrev));
        }
        if self.view == View::Controller {
            items.push(("Next controller drawing (family)".into(), Act::Family));
        }
        if self.view == View::Bindings && !self.draft.bindings.is_empty() {
            items.push(("Edit the chord".into(), Act::Enter));
            items.push(("Delete the binding".into(), Act::DeleteUse));
        }
        items.extend([
            ("Search".to_string(), Act::Search),
            ("Next view".into(), Act::ViewNext),
            ("Previous view".into(), Act::ViewPrev),
            ("Undo".into(), Act::Undo),
            ("Redo".into(), Act::Redo),
            ("Validate now".into(), Act::Validate),
            ("Apply the draft".into(), Act::Apply),
            ("Revert the draft".into(), Act::Revert),
            ("Start from the defaults".into(), Act::Defaults),
            ("Rescan devices".into(), Act::Rescan),
            ("Help".into(), Act::Help),
            ("Quit".into(), Act::Quit),
        ]);
        self.menu = Some(Menu {
            title: u
                .map(|u| format!(" {} ", u.chord))
                .unwrap_or_else(|| " actions ".into()),
            items,
            sel: 0,
        });
    }

    fn request_apply(&mut self) {
        if !self.dirty() {
            self.status = "nothing to apply: the draft equals the saved map".into();
        } else if self.validating {
            self.status = "validation still running; apply when it finishes".into();
        } else {
            match &self.outcome {
                Some(o) if o.new_findings().is_empty() => self.confirm = Some(Confirm::Apply),
                Some(o) => {
                    self.status = format!(
                        "apply blocked: the draft adds {} finding(s)",
                        o.new_findings().len()
                    )
                }
                None => self.status = "validate first (v)".into(),
            }
        }
    }

    fn confirmed(&mut self, c: Confirm) {
        match c {
            Confirm::Quit => self.quit = true,
            Confirm::Delete => {
                if self.sel < self.draft.bindings.len() {
                    let before = self.draft.clone();
                    let b = self.draft.bindings.remove(self.sel);
                    self.sel = self.sel.min(self.draft.bindings.len().saturating_sub(1));
                    self.edit(
                        before,
                        format!("deleted {} from the draft", field(&b, "id")),
                    );
                }
            }
            Confirm::DeleteUse => self.delete_use(),
            Confirm::Revert => {
                let before = self.draft.clone();
                self.draft = self.saved.clone();
                self.edit(before, "draft reverted to the saved map".into());
            }
            Confirm::Restore => {
                let before = self.draft.clone();
                self.draft = self.layers.defaults();
                self.sel = 0;
                self.edit(
                    before,
                    "draft = vendor/host defaults (apply to remove your overrides)".into(),
                );
            }
            Confirm::Apply => match self
                .layers
                .user_doc_for(&self.draft)
                .map(|d| d.to_string())
                .and_then(|t| crate::apply::apply(&self.layers, &t, &self.backup_suffix))
            {
                Ok(backup) => {
                    self.status = format!(
                        "applied to {}{}; the compositor picks it up on its next config reload",
                        self.layers.user_path.display(),
                        backup
                            .map(|b| format!(" (previous tier: {})", b.display()))
                            .unwrap_or_default()
                    );
                    match Layers::load(&self.layers.root) {
                        Ok(l) => {
                            self.layers = l;
                            self.merged = self.layers.effective();
                            self.saved = Draft::from_merged(&self.merged);
                            self.draft = self.saved.clone();
                            self.refresh_uses();
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

    fn family(&self) -> &Family {
        if let Some(i) = self.family_override {
            return &self.families[i % self.families.len()];
        }
        match self.pad_dev.and_then(|i| self.devices.get(i)) {
            Some(d) => d.family(&self.families),
            None => self
                .families
                .iter()
                .find(|f| f.id == "generic")
                .unwrap_or(&self.families[0]),
        }
    }

    fn block<'a>(&self, title: impl Into<Line<'a>>, focused: bool) -> Block<'a> {
        let t = &self.theme;
        Block::default()
            .borders(Borders::ALL)
            .border_type(BorderType::Plain)
            .border_set(if self.glyphs.ascii_lines {
                ASCII_BORDER
            } else {
                ratatui::symbols::border::PLAIN
            })
            .title(title)
            .border_style(if focused {
                Style::new().fg(t.accent)
            } else {
                Style::new().fg(t.muted)
            })
    }

    /// Full layout on a large terminal; on a small one (PiP, phone, split pane, 80x24 console) one pane at a time.
    pub fn draw(&mut self, fr: &mut Frame) {
        let area = fr.area();
        self.compact = area.width < 120 || area.height < 32;
        self.hits.clear();
        let [top, tabs, body, status] = Layout::vertical([
            Constraint::Length(1),
            Constraint::Length(1),
            Constraint::Min(6),
            Constraint::Length(if self.compact { 2 } else { 3 }),
        ])
        .areas(area);
        self.draw_header(fr, top);
        self.draw_tabs(fr, tabs);
        match self.view {
            View::Bindings => {
                if self.compact {
                    let [table, detail] =
                        Layout::vertical([Constraint::Min(4), Constraint::Length(6)]).areas(body);
                    self.draw_table(fr, table);
                    self.draw_binding_detail(fr, detail);
                } else {
                    let [table, detail] =
                        Layout::horizontal([Constraint::Min(60), Constraint::Length(46)])
                            .areas(body);
                    self.draw_table(fr, table);
                    self.draw_binding_detail(fr, detail);
                }
            }
            View::Devices => self.draw_devices(fr, body),
            _ => {
                let [chips, rest] =
                    Layout::vertical([Constraint::Length(1), Constraint::Min(4)]).areas(body);
                self.draw_layers(fr, chips);
                if self.compact {
                    if self.focus == Focus::Drawing && self.search.is_none() {
                        self.draw_device(fr, rest);
                    } else {
                        self.draw_uses(fr, rest);
                    }
                } else {
                    let [drawing, side] =
                        Layout::horizontal([Constraint::Min(60), Constraint::Length(50)])
                            .areas(rest);
                    self.draw_device(fr, drawing);
                    self.draw_uses(fr, side);
                }
            }
        }
        self.draw_status(fr, status);
        if self.help {
            self.draw_help(fr);
        }
        if let Some(m) = &self.menu {
            let h = (m.items.len() as u16 + 2).min(area.height);
            let w = (m
                .items
                .iter()
                .map(|(l, _)| l.chars().count())
                .max()
                .unwrap_or(10) as u16
                + 4)
            .max(24)
            .min(area.width);
            let r = centered(area, w, h);
            fr.render_widget(Clear, r);
            let first = m.sel.saturating_sub(h.saturating_sub(3) as usize);
            let lines: Vec<Line> = m
                .items
                .iter()
                .enumerate()
                .skip(first)
                .map(|(i, (l, _))| {
                    Line::styled(
                        format!(" {l}"),
                        if i == m.sel {
                            Style::new().add_modifier(Modifier::REVERSED)
                        } else {
                            Style::new()
                        },
                    )
                })
                .collect();
            let title = m.title.clone();
            fr.render_widget(Paragraph::new(lines).block(self.block(title, true)), r);
            for (row, i) in (first..m.items.len()).enumerate() {
                if (row as u16) < h.saturating_sub(2) {
                    self.hits.push((
                        Rect::new(r.x + 1, r.y + 1 + row as u16, r.width.saturating_sub(2), 1),
                        Hit::MenuItem(i),
                    ));
                }
            }
        }
        if let Some(p) = &self.picker {
            let h = (p.items.len() as u16 + 2)
                .clamp(5, area.height.saturating_sub(2).max(5))
                .min(area.height);
            let w = 64.min(area.width);
            let r = centered(area, w, h);
            fr.render_widget(Clear, r);
            let rows = h.saturating_sub(2) as usize;
            let first = p.sel.saturating_sub(rows.saturating_sub(1));
            let lines: Vec<Line> = p
                .items
                .iter()
                .enumerate()
                .skip(first)
                .take(rows)
                .map(|(i, l)| {
                    Line::styled(
                        format!(" {l}"),
                        if i == p.sel {
                            Style::new().add_modifier(Modifier::REVERSED)
                        } else {
                            Style::new()
                        },
                    )
                })
                .collect();
            let title = format!(" {} (Enter picks, Esc closes) ", p.title);
            fr.render_widget(Paragraph::new(lines).block(self.block(title, true)), r);
            for row in 0..rows.min(p.items.len().saturating_sub(first)) {
                self.hits.push((
                    Rect::new(r.x + 1, r.y + 1 + row as u16, r.width.saturating_sub(2), 1),
                    Hit::PickItem(first + row),
                ));
            }
        }
        if let Some(p) = &self.prompt {
            let r = centered(area, 70, 4);
            fr.render_widget(Clear, r);
            let line = Line::from(vec![
                Span::raw(p.buf.clone()),
                Span::styled("_", Style::new().add_modifier(Modifier::SLOW_BLINK)),
            ]);
            let title = format!(" {} (Enter/Esc) ", p.label);
            fr.render_widget(
                Paragraph::new(line)
                    .wrap(Wrap { trim: false })
                    .block(self.block(title, true)),
                r,
            );
        }
        if let Some(c) = self.confirm {
            let q = match c {
                Confirm::Apply => format!(
                    "Write the draft to {}? (y/n)",
                    self.layers.user_path.display()
                ),
                Confirm::Restore => "Replace the draft with the vendor/host defaults? (y/n)".into(),
                Confirm::Revert => "Throw away every draft change? (y/n)".into(),
                Confirm::Delete => format!(
                    "Delete {} from the draft? (y/n)",
                    self.draft
                        .bindings
                        .get(self.sel)
                        .map(|b| field(b, "id"))
                        .unwrap_or("")
                ),
                Confirm::DeleteUse => format!(
                    "Delete {} from the draft? (y/n)",
                    self.selected_use().map(|u| u.chord).unwrap_or_default()
                ),
                Confirm::Quit => "Quit and discard the unsaved draft? (y/n)".into(),
            };
            let r = centered(area, 70, 5);
            fr.render_widget(Clear, r);
            fr.render_widget(
                Paragraph::new(q)
                    .wrap(Wrap { trim: false })
                    .block(self.block(" confirm ", true)),
                r,
            );
        }
    }

    fn draw_header(&self, fr: &mut Frame, area: Rect) {
        let t = &self.theme;
        let ctx = self.context().unwrap_or("all");
        let state = if self.dirty() {
            format!("DRAFT ({} edit(s))", self.undo.len())
        } else {
            "saved".into()
        };
        let mut spans = vec![Span::styled(
            " MiOS Controls ",
            Style::new().fg(t.accent).add_modifier(Modifier::BOLD),
        )];
        if self.compact {
            spans.push(Span::raw(format!("[{ctx}] {state}  m menu  ? help")));
        } else {
            spans.push(Span::raw(format!(
                "| context [{ctx}] (keys [ and ]) | {state} | m menu | ? help"
            )));
        }
        if self.capture.is_some() {
            spans.push(Span::styled(
                format!(
                    "  CAPTURE {}s",
                    self.capture_timeout_left()
                        .map(|d| d.as_secs())
                        .unwrap_or(0)
                ),
                Style::new().fg(t.warn).add_modifier(Modifier::BOLD),
            ));
        }
        if let Some(p) = &self.pending {
            spans.push(Span::styled(
                format!("  REBIND {}", p.chord),
                Style::new().fg(t.warn).add_modifier(Modifier::BOLD),
            ));
        }
        fr.render_widget(Paragraph::new(Line::from(spans)), area);
    }

    fn draw_tabs(&mut self, fr: &mut Frame, area: Rect) {
        let t = &self.theme;
        let mut x = area.x;
        let mut spans = Vec::new();
        for (i, v) in VIEWS.iter().enumerate() {
            let label = if self.compact && area.width < 60 {
                format!(" {} {} ", i + 1, &v.title()[..3])
            } else {
                format!(" {} {} ", i + 1, v.title())
            };
            let w = label.chars().count() as u16;
            let st = if *v == self.view {
                Style::new()
                    .fg(t.accent)
                    .add_modifier(Modifier::REVERSED | Modifier::BOLD)
            } else {
                Style::new().fg(t.fg)
            };
            self.hits.push((
                Rect::new(x, area.y, w.min(area.right().saturating_sub(x)), 1),
                Hit::Tab(*v),
            ));
            spans.push(Span::styled(label, st));
            spans.push(Span::raw(" "));
            x = x.saturating_add(w + 1);
        }
        fr.render_widget(Paragraph::new(Line::from(spans)), area);
    }

    fn draw_layers(&mut self, fr: &mut Frame, area: Rect) {
        let t = &self.theme;
        let kind = self.kind();
        let list = self.layer_list(kind);
        let cur = self.layer[kind_index(kind)].min(list.len() - 1);
        let name = |l: &str| {
            if l == "*" {
                "all".to_string()
            } else {
                layer_title(l)
            }
        };
        let mut spans = vec![Span::styled("Layer ", Style::new().fg(t.muted))];
        let mut x = area.x + 6;
        let full: u16 = list
            .iter()
            .map(|l| name(l).chars().count() as u16 + 3)
            .sum();
        if full + 6 <= area.width {
            for (i, l) in list.iter().enumerate() {
                let label = format!("[{}]", name(l));
                let w = label.chars().count() as u16;
                let st = if i == cur {
                    Style::new().fg(t.warn).add_modifier(Modifier::REVERSED)
                } else {
                    Style::new().fg(t.fg)
                };
                self.hits.push((Rect::new(x, area.y, w, 1), Hit::Layer(i)));
                spans.push(Span::styled(label, st));
                spans.push(Span::raw(" "));
                x += w + 1;
            }
        } else {
            let label = format!("{}/{}: {}  (l / L)", cur + 1, list.len(), name(&list[cur]));
            self.hits.push((
                Rect::new(x, area.y, label.chars().count() as u16, 1),
                Hit::Layer((cur + 1) % list.len()),
            ));
            spans.push(Span::styled(label, Style::new().fg(t.warn)));
        }
        fr.render_widget(Paragraph::new(Line::from(spans)), area);
    }

    /// Per-input look for the drawing: bound on the layer, conflict, the layer's held modifiers and prefix keys, the
    /// cursor, a pending rebind's source, and inputs the selected device does not have.
    fn looks(&self) -> impl Fn(&str) -> Look + '_ {
        let t = &self.theme;
        let layer = self.current_layer();
        let kind = self.kind();
        let bound: BTreeSet<String> = self
            .uses
            .iter()
            .filter(|u| u.kind() == kind)
            .filter(|u| self.input_bound(&u.input, layer.as_deref()))
            .map(|u| u.input.clone())
            .collect();
        let conflict: BTreeSet<String> = self
            .flagged
            .iter()
            .map(|i| &self.uses[*i])
            .filter(|u| layer.as_deref().is_none_or(|l| u.layer == l))
            .map(|u| u.input.clone())
            .collect();
        let mut held: BTreeSet<String> = BTreeSet::new();
        if let Some(l) = &layer {
            if !l.starts_with("pad:") {
                let (pre, mods) = parse_layer(l);
                for m in mods {
                    held.insert(format!("key:{m}"));
                }
                for step in pre {
                    for tok in step.split('+') {
                        held.insert(format!("key:{}", layout::key_alias(tok)));
                    }
                }
            } else if l == PAD_GUIDE {
                held.insert("pad:BTN_MODE".into());
            }
        }
        let absent = self.absent();
        let search: BTreeSet<String> = self
            .search
            .as_deref()
            .filter(|q| !q.is_empty())
            .map(|q| {
                self.uses
                    .iter()
                    .filter(|u| u.matches(q))
                    .map(|u| u.input.clone())
                    .collect()
            })
            .unwrap_or_default();
        let cursor = self.cursor().to_owned();
        let pending = self.pending.as_ref().map(|p| p.input.clone());
        let focused = self.focus == Focus::Drawing;
        let (fg, accent, warn, error, muted) = (t.fg, t.accent, t.warn, t.error, t.muted);
        move |input: &str| {
            if input.is_empty() {
                return Look {
                    border: Style::new().fg(muted),
                    label: Style::new(),
                    prio: 0,
                };
            }
            let mut border = Style::new().fg(muted);
            let mut label = Style::new().fg(fg);
            let mut prio = 1;
            if absent.contains(input) {
                label = Style::new().fg(muted).add_modifier(Modifier::DIM);
            }
            if bound.contains(input) {
                border = Style::new().fg(accent);
                label = Style::new().fg(accent).add_modifier(Modifier::BOLD);
                prio = 2;
            }
            if held.contains(input) {
                border = Style::new().fg(warn);
                label = Style::new().fg(warn).add_modifier(Modifier::BOLD);
                prio = 3;
            }
            if conflict.contains(input) {
                border = Style::new().fg(error);
                label = Style::new().fg(error).add_modifier(Modifier::BOLD);
                prio = 4;
            }
            if search.contains(input) {
                label = label.add_modifier(Modifier::UNDERLINED);
            }
            if pending.as_deref() == Some(input) {
                label = label.add_modifier(Modifier::UNDERLINED | Modifier::SLOW_BLINK);
            }
            if input == cursor {
                label = label.add_modifier(Modifier::REVERSED);
                if focused {
                    border = border.add_modifier(Modifier::BOLD);
                }
                prio = 5;
            }
            Look {
                border,
                label,
                prio,
            }
        }
    }

    /// Inputs the selected device lacks (mouse buttons, pad buttons without a HAT or key).
    fn absent(&self) -> BTreeSet<String> {
        let dev = match self.view {
            View::Mouse => self.mouse_dev,
            View::Controller => self.pad_dev,
            _ => None,
        };
        dev.and_then(|i| self.devices.get(i))
            .map(absent_inputs)
            .unwrap_or_default()
    }

    fn draw_device(&mut self, fr: &mut Frame, area: Rect) {
        let t = &self.theme;
        let title = match self.view {
            View::Keyboard => " Keyboard ".to_string(),
            View::Mouse => format!(
                " Mouse{} ",
                self.mouse_dev
                    .and_then(|i| self.devices.get(i))
                    .map(|d| format!(": {}", d.name))
                    .unwrap_or_default()
            ),
            _ => format!(
                " Controller: {}{} ",
                self.family().title,
                self.pad_dev
                    .and_then(|i| self.devices.get(i))
                    .map(|d| format!(" ({})", d.name))
                    .unwrap_or_default()
            ),
        };
        let block = self.block(title, self.focus == Focus::Drawing);
        let inner = block.inner(area);
        fr.render_widget(block, area);
        let g = self.glyphs;
        let legend_rows = if inner.height >= 18 { 2 } else { 0 };
        // The controller drawing's notes (touchpad, motion, d-pad) go under it when the panel has the rows to spare.
        let notes = if self.view == View::Controller
            && legend_rows > 0
            && inner.height >= layout::PAD_H + legend_rows + PAD_NOTES.len() as u16
        {
            PAD_NOTES.len() as u16
        } else {
            0
        };
        let legend_rows = legend_rows + notes;
        let draw_area = Rect::new(
            inner.x,
            inner.y,
            inner.width,
            inner.height.saturating_sub(legend_rows),
        );
        let (els, boxed): (Vec<El>, bool) = match self.view {
            View::Keyboard => match layout::keyboard_elements(draw_area.width, draw_area.height, g)
            {
                Some((e, _)) => (e, true),
                None => (
                    layout::keyboard_flat(draw_area.width, draw_area.height, g),
                    false,
                ),
            },
            View::Mouse => {
                if draw_area.width >= layout::MOUSE_W && draw_area.height >= layout::MOUSE_H {
                    (
                        layout::mouse_elements(
                            if draw_area.width >= layout::MOUSE_W * 2 + 2 {
                                2
                            } else {
                                1
                            },
                            g,
                        ),
                        true,
                    )
                } else {
                    let mut v = Vec::new();
                    let ids = [
                        "mouse:272",
                        "mouse:274",
                        "mouse:273",
                        "mouse:wheel_up",
                        "mouse:wheel_down",
                        "mouse:wheel_left",
                        "mouse:wheel_right",
                        "mouse:275",
                        "mouse:276",
                        "mouse:277",
                        "mouse:278",
                        "mouse:279",
                    ];
                    let (mut x, mut y) = (0u16, 0u16);
                    for id in ids {
                        let label = layout::mouse_elements(1, g)
                            .into_iter()
                            .find(|e| e.input == id)
                            .map(|e| e.label)
                            .unwrap_or_default();
                        let w = label.chars().count() as u16 + 1;
                        if x + w > draw_area.width {
                            x = 0;
                            y += 1;
                        }
                        v.push(El {
                            input: id.into(),
                            label,
                            x0: x,
                            y0: y,
                            x1: x + w,
                            y1: y,
                        });
                        x += w + 1;
                    }
                    (v, false)
                }
            }
            _ => {
                let fam = self.family().clone();
                if draw_area.width >= layout::PAD_W && draw_area.height >= layout::PAD_H {
                    (
                        layout::pad_ids(layout::pad_elements(
                            &fam,
                            if draw_area.width >= layout::PAD_W * 2 {
                                2
                            } else {
                                1
                            },
                            g,
                        )),
                        true,
                    )
                } else {
                    (layout::pad_flat(&fam, g), false)
                }
            }
        };
        let look = self.looks();
        let hits = {
            let buf = fr.buffer_mut();
            if boxed {
                layout::paint(buf, draw_area, &els, g, &look)
            } else {
                layout::paint_flat(buf, draw_area, &els, &look)
            }
        };
        drop(look);
        // The legend's "also bound here" reads the caps this frame drew, not the previous frame's.
        self.els = els;
        if legend_rows > 0 {
            let y = inner.y + inner.height - legend_rows;
            let legend = Line::from(vec![
                Span::styled(
                    " bound ",
                    Style::new().fg(t.accent).add_modifier(Modifier::BOLD),
                ),
                Span::styled(
                    " held/prefix ",
                    Style::new().fg(t.warn).add_modifier(Modifier::BOLD),
                ),
                Span::styled(
                    " conflict ",
                    Style::new().fg(t.error).add_modifier(Modifier::BOLD),
                ),
                Span::styled(" cursor ", Style::new().add_modifier(Modifier::REVERSED)),
                Span::styled(
                    " not on this device",
                    Style::new().fg(t.muted).add_modifier(Modifier::DIM),
                ),
            ]);
            fr.render_widget(
                Paragraph::new(legend),
                Rect::new(inner.x, y, inner.width, 1),
            );
            let hint = if self.view == View::Controller {
                format!("Tap North: the aimed window takes control. Hold North: fullscreen. Hold {}: release.", self.release_label())
            } else {
                let extra = self.extra_keys();
                if extra.is_empty() {
                    "arrows move, Enter lists the uses, c captures, l switches layers".into()
                } else {
                    format!("also bound here: {}", extra.join(" "))
                }
            };
            fr.render_widget(
                Paragraph::new(Line::styled(hint, Style::new().fg(t.muted))),
                Rect::new(inner.x, y + 1, inner.width, 1),
            );
            for (i, note) in PAD_NOTES.iter().take(notes as usize).enumerate() {
                fr.render_widget(
                    Paragraph::new(Line::styled(
                        *note,
                        Style::new().fg(t.muted).add_modifier(Modifier::DIM),
                    )),
                    Rect::new(inner.x, y + 2 + i as u16, inner.width, 1),
                );
            }
        }
        for (r, id) in hits {
            self.hits.push((r, Hit::Input(id)));
        }
    }

    /// Keys with uses that the drawing has no cap for (media keys, XF86 names...).
    fn extra_keys(&self) -> Vec<String> {
        let on: BTreeSet<&str> = self.els.iter().map(|e| e.input.as_str()).collect();
        let set: BTreeSet<String> = self
            .uses
            .iter()
            .filter(|u| u.kind() == "key" && !on.contains(u.input.as_str()))
            .map(|u| u.input.trim_start_matches("key:").to_owned())
            .collect();
        set.into_iter().collect()
    }

    fn draw_uses(&mut self, fr: &mut Frame, area: Rect) {
        let t = &self.theme;
        let panel = self.panel_uses();
        let title = match self.search.as_deref() {
            Some(q) => format!(" Search {q:?}: {} ", panel.len()),
            None => format!(
                " {} ({}) ",
                input_title(self.cursor(), self.family(), self.glyphs),
                panel.len()
            ),
        };
        let block = self.block(title, self.focus == Focus::Uses || self.search.is_some());
        let inner = block.inner(area);
        fr.render_widget(block, area);
        let detail_h = if inner.height >= 16 { 7 } else { 0 };
        let list_area = Rect::new(
            inner.x,
            inner.y,
            inner.width,
            inner.height.saturating_sub(detail_h),
        );
        let layer = self.current_layer();
        let sel = self.uses_sel.min(panel.len().saturating_sub(1));
        let rows_per = 2u16;
        let fit = (list_area.height / rows_per).max(1) as usize;
        let first = sel.saturating_sub(fit.saturating_sub(1));
        let mut lines: Vec<Line> = Vec::new();
        if panel.is_empty() {
            lines.push(Line::styled(
                "nothing is bound here",
                Style::new().fg(t.muted),
            ));
            if !self.kind().is_empty() {
                lines.push(Line::styled(
                    "n: new binding on it   c: capture another input",
                    Style::new().fg(t.muted),
                ));
            }
        }
        for (row, idx) in panel.iter().enumerate().skip(first).take(fit) {
            let u = &self.uses[*idx];
            let flagged = self.flagged.contains(idx);
            let off_layer = layer.as_deref().is_some_and(|l| l != u.layer) && self.search.is_none();
            let mut st = Style::new().fg(if flagged {
                t.error
            } else if !u.editable() || off_layer {
                t.muted
            } else {
                t.fg
            });
            if row == sel && (self.focus == Focus::Uses || self.search.is_some()) {
                st = st.add_modifier(Modifier::REVERSED);
            }
            let mark = if flagged {
                "!"
            } else if u.editable() {
                " "
            } else {
                "-"
            };
            let head = format!("{mark}{:<8} {}", u.source.word(), u.chord);
            let action = if u.label.is_empty() || u.label == u.action {
                u.action.clone()
            } else {
                format!("{} - {}", u.action, u.label)
            };
            lines.push(Line::styled(head, st.add_modifier(Modifier::BOLD)));
            lines.push(Line::styled(format!("   {action}"), st));
            let y = list_area.y + ((row - first) as u16) * rows_per;
            self.hits.push((
                Rect::new(list_area.x, y, list_area.width, rows_per),
                Hit::Use(row),
            ));
        }
        fr.render_widget(Paragraph::new(lines), list_area);
        if detail_h > 0 {
            let d = Rect::new(
                inner.x,
                inner.y + inner.height - detail_h,
                inner.width,
                detail_h,
            );
            let mut lines = vec![Line::styled(
                "\u{2500}".repeat(d.width as usize).replace(
                    '\u{2500}',
                    if self.glyphs.ascii_lines {
                        "-"
                    } else {
                        "\u{2500}"
                    },
                ),
                Style::new().fg(t.muted),
            )];
            if let Some(u) = panel.get(sel).map(|i| &self.uses[*i]) {
                lines.push(Line::from(vec![
                    Span::styled("layer  ", Style::new().fg(t.muted)),
                    Span::raw(layer_title(&u.layer)),
                ]));
                lines.push(Line::from(vec![
                    Span::styled("where  ", Style::new().fg(t.muted)),
                    Span::raw(u.source.place()),
                ]));
                lines.push(Line::from(vec![
                    Span::styled("in     ", Style::new().fg(t.muted)),
                    Span::raw(if u.contexts.is_empty() {
                        "-".into()
                    } else {
                        u.contexts.join(",")
                    }),
                ]));
                if !u.note.is_empty() {
                    lines.push(Line::styled(u.note.clone(), Style::new().fg(t.muted)));
                }
                if let Some(o) = &self.outcome {
                    if let Source::Global { id, .. } = &u.source {
                        for f in o.mentions(id) {
                            lines.push(Line::styled(f.clone(), Style::new().fg(t.error)));
                        }
                    }
                }
                lines.push(Line::styled(
                    if u.editable() {
                        "r rebind  a action  d delete  m menu"
                    } else {
                        "read-only here (fixed role or another table)"
                    },
                    Style::new().fg(t.muted),
                ));
            }
            fr.render_widget(Paragraph::new(lines).wrap(Wrap { trim: true }), d);
        }
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
            None => crate::model::default_contexts()
                .map(str::to_owned)
                .collect(),
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

    fn draw_table(&mut self, fr: &mut Frame, area: Rect) {
        let t = &self.theme;
        let flagged_ids: BTreeSet<String> = self
            .flagged
            .iter()
            .filter_map(|i| match &self.uses[*i].source {
                Source::Global { id, .. } => Some(id.clone()),
                _ => None,
            })
            .collect();
        let rows: Vec<Row> = self
            .draft
            .bindings
            .iter()
            .map(|b| {
                let hit = flagged_ids.contains(field(b, "id"))
                    || self
                        .outcome
                        .as_ref()
                        .map(|o| !o.mentions(field(b, "id")).is_empty())
                        .unwrap_or(false);
                let changed = self
                    .saved
                    .index(field(b, "id"))
                    .map(|i| &self.saved.bindings[i] != b)
                    .unwrap_or(true);
                let (state, st) = self.binding_state(b);
                let mut cells = vec![
                    Cell::from(if hit {
                        "!"
                    } else if changed {
                        "*"
                    } else {
                        " "
                    })
                    .style(Style::new().fg(if hit {
                        t.error
                    } else {
                        t.warn
                    })),
                    Cell::from(field(b, "id").to_owned()),
                    Cell::from(
                        b.get("tier")
                            .and_then(Value::as_integer)
                            .map(|x| x.to_string())
                            .unwrap_or_default(),
                    ),
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
                vec![
                    Constraint::Length(1),
                    Constraint::Percentage(30),
                    Constraint::Percentage(28),
                    Constraint::Min(8),
                    Constraint::Length(5),
                ],
                vec!["", "id", "chord", "action", "here"],
            )
        } else {
            (
                vec![
                    Constraint::Length(1),
                    Constraint::Length(24),
                    Constraint::Length(4),
                    Constraint::Length(22),
                    Constraint::Min(20),
                    Constraint::Length(6),
                ],
                vec!["", "id", "tier", "chord", "action", "here"],
            )
        };
        let table = Table::new(rows, widths)
            .header(Row::new(header).style(Style::new().fg(t.accent)))
            .row_highlight_style(Style::new().add_modifier(Modifier::REVERSED))
            .block(self.block(" Bindings  (! finding  * changed) ", true));
        let mut st = TableState::default().with_selected(if self.draft.bindings.is_empty() {
            None
        } else {
            Some(self.sel)
        });
        fr.render_stateful_widget(table, area, &mut st);
        let offset = st.offset();
        for row in 0..area.height.saturating_sub(3) {
            let i = offset + row as usize;
            if i < self.draft.bindings.len() {
                self.hits.push((
                    Rect::new(
                        area.x + 1,
                        area.y + 2 + row,
                        area.width.saturating_sub(2),
                        1,
                    ),
                    Hit::Row(i),
                ));
            }
        }
    }

    fn draw_binding_detail(&self, fr: &mut Frame, area: Rect) {
        let t = &self.theme;
        let mut lines: Vec<Line> = Vec::new();
        if let Some(b) = self.draft.bindings.get(self.sel) {
            let (state, st) = self.binding_state(b);
            lines.push(Line::styled(
                field(b, "id").to_owned(),
                Style::new().fg(t.accent).add_modifier(Modifier::BOLD),
            ));
            lines.push(Line::from(format!("chord  {}", field(b, "chord"))));
            if !field(b, "bypass").is_empty() {
                lines.push(Line::from(format!("bypass {}", field(b, "bypass"))));
            }
            lines.push(Line::from(format!("action {}", field(b, "action"))));
            let ctxs = str_list(b.get("contexts"));
            lines.push(Line::from(format!(
                "in     {}",
                if ctxs.is_empty() {
                    "all contexts".into()
                } else {
                    ctxs.join(",")
                }
            )));
            let pass = str_list(b.get("passthrough"));
            if !pass.is_empty() {
                lines.push(Line::from(format!("passes {} to the app", pass.join(","))));
            }
            lines.push(Line::from(vec![
                Span::raw("here   "),
                Span::styled(state, st),
            ]));
            if !field(b, "note").is_empty() {
                lines.push(Line::styled(
                    field(b, "note").to_owned(),
                    Style::new().fg(t.muted),
                ));
            }
            if let Some(o) = &self.outcome {
                for f in o.mentions(field(b, "id")) {
                    lines.push(Line::styled(f.clone(), Style::new().fg(t.error)));
                }
            }
            lines.push(Line::styled(
                "c chord  a action  x contexts  p passthrough  b bypass  t tier  n new  d delete",
                Style::new().fg(t.muted),
            ));
        }
        fr.render_widget(
            Paragraph::new(lines)
                .wrap(Wrap { trim: true })
                .block(self.block(" Selected ", false)),
            area,
        );
    }

    fn draw_devices(&mut self, fr: &mut Frame, area: Rect) {
        let t = &self.theme;
        let [list, profile] = if self.compact {
            Layout::vertical([
                Constraint::Length((self.devices.len() as u16 + 2).min(area.height / 2).max(3)),
                Constraint::Min(3),
            ])
            .areas(area)
        } else {
            Layout::horizontal([Constraint::Length(48), Constraint::Min(30)]).areas(area)
        };
        let block = self.block(" Devices (Enter: use it in its view, D rescan) ", true);
        let inner = block.inner(list);
        fr.render_widget(block, list);
        let mut lines = Vec::new();
        for (i, d) in self.devices.iter().enumerate() {
            let tag = match d.kind {
                Kind::Keyboard => "kbd",
                Kind::Mouse => "mouse",
                Kind::Controller => "pad",
            };
            let flag = if d.is_virtual() {
                " (inputd virtual)"
            } else if d.is_fixture() {
                " (fixture)"
            } else {
                ""
            };
            let st = if i == self.dev_sel {
                Style::new().add_modifier(Modifier::REVERSED)
            } else {
                Style::new()
            };
            lines.push(Line::styled(format!("{tag:<5} {}{flag}", d.name), st));
            if (i as u16) < inner.height {
                self.hits.push((
                    Rect::new(inner.x, inner.y + i as u16, inner.width, 1),
                    Hit::Device(i),
                ));
            }
        }
        if lines.is_empty() {
            lines.push(Line::styled(
                "no input devices visible in /proc/bus/input/devices",
                Style::new().fg(t.muted),
            ));
        }
        let first = self
            .dev_sel
            .saturating_sub(inner.height.saturating_sub(1) as usize);
        fr.render_widget(Paragraph::new(lines).scroll((first as u16, 0)), inner);
        let mut p: Vec<Line> = Vec::new();
        if let Some(d) = self.devices.get(self.dev_sel) {
            p.push(Line::styled(
                d.name.clone(),
                Style::new().fg(t.accent).add_modifier(Modifier::BOLD),
            ));
            p.push(Line::from(format!(
                "{}  usb {:04x}:{:04x}  {}",
                d.kind.word(),
                d.vendor,
                d.product,
                d.event().unwrap_or("-")
            )));
            let kind = match d.kind {
                Kind::Keyboard => "key",
                Kind::Mouse => "mouse",
                Kind::Controller => "pad",
            };
            if d.kind == Kind::Controller {
                let fam = d.family(&self.families);
                p.push(Line::from(format!(
                    "family {} ({} layout)",
                    fam.title, fam.layout
                )));
                let buttons: Vec<String> = layout::PAD_INPUTS
                    .iter()
                    .filter(|b| {
                        b.starts_with("BTN_")
                            && d.has(mios_resolver::keybindings::button_code(b).unwrap_or(0))
                    })
                    .map(|b| fam.label(b, self.glyphs.ascii_labels))
                    .collect();
                p.push(Line::from(format!("buttons {}", buttons.join(" "))));
                p.push(Line::from(format!(
                    "axes {}",
                    d.abs
                        .iter()
                        .map(|a| a.to_string())
                        .collect::<Vec<_>>()
                        .join(" ")
                )));
            }
            if d.kind == Kind::Mouse {
                let b: Vec<&str> = layout::MOUSE_BUTTONS
                    .iter()
                    .filter(|(c, _, _)| d.has(*c))
                    .map(|(_, l, _)| *l)
                    .collect();
                p.push(Line::from(format!(
                    "buttons {}  wheel {}  tilt {}",
                    b.join(" "),
                    if d.rel.contains(&8) { "yes" } else { "no" },
                    if d.rel.contains(&6) { "yes" } else { "no" }
                )));
            }
            let reach: Vec<&Use> = self.uses.iter().filter(|u| u.kind() == kind).collect();
            let editable = reach.iter().filter(|u| u.editable()).count();
            let conflicts = self
                .flagged
                .iter()
                .filter(|i| self.uses[**i].kind() == kind)
                .count();
            p.push(Line::from(format!(
                "{} use(s) on this kind of device: {editable} editable, {conflicts} in conflict",
                reach.len()
            )));
            p.push(Line::styled(
                "profile (layer: chord -> action)",
                Style::new().fg(t.muted),
            ));
            for u in reach.iter().take(profile.height.saturating_sub(8) as usize) {
                let st = if self.flagged.iter().any(|i| &self.uses[*i] == *u) {
                    Style::new().fg(t.error)
                } else {
                    Style::new()
                };
                p.push(Line::styled(
                    format!("{:<16} {:<22} {}", layer_title(&u.layer), u.chord, u.action),
                    st,
                ));
            }
        }
        fr.render_widget(
            Paragraph::new(p).block(self.block(" Profile ", false)),
            profile,
        );
    }

    fn draw_status(&self, fr: &mut Frame, area: Rect) {
        let t = &self.theme;
        let check = if self.validating {
            Span::styled("validating...", Style::new().fg(t.warn))
        } else {
            match &self.outcome {
                None => Span::styled("not validated", Style::new().fg(t.muted)),
                Some(o) if o.new_findings().is_empty() => {
                    Span::styled(format!("valid: {}", o.summary), Style::new().fg(t.ok))
                }
                Some(o) => Span::styled(
                    format!(
                        "{} new finding(s): {}",
                        o.new_findings().len(),
                        o.new_findings()[0]
                    ),
                    Style::new().fg(t.error),
                ),
            }
        };
        let mut lines = vec![
            Line::from(check),
            Line::from(Span::raw(self.status.clone())),
        ];
        if !self.compact {
            let keys = match self.view {
                View::Bindings => "1-5 views  arrows move  Enter/c chord  a action  x p b t  n new  d del  u/U undo/redo  v check  w apply  m menu  / search  ? help  q quit",
                View::Devices => "1-5 views  arrows move  Enter use this device  D rescan  m menu  ? help  q quit",
                _ => "1-5 views  arrows move  Tab list  l/L layer  c capture  r rebind  a action  n new  d del  u/U undo/redo  / search  w apply  m menu  ? help",
            };
            let pads = if self.pad_status.is_empty() {
                String::new()
            } else {
                format!("  [{}]", self.pad_status)
            };
            lines.push(Line::styled(
                format!("{keys}{pads}"),
                Style::new().fg(t.muted),
            ));
        }
        fr.render_widget(Paragraph::new(lines), area);
    }

    /// The release chord in the current family's labels, from its one source
    /// ([desktop.hyprland.hypr3d.controller].toggle_chord; never a hard-coded L3+R3).
    fn release_label(&self) -> String {
        let fam = self.family();
        crate::model::toggle_chord(&self.merged)
            .iter()
            .map(|b| fam.label(b, self.glyphs.ascii_labels))
            .collect::<Vec<_>>()
            .join("+")
    }

    /// [keybindings.global.controller].capture_hold_ms: how long North or the toggle chord is held before it counts as a hold.
    fn hold_ms(&self) -> i64 {
        at(
            &self.merged,
            "keybindings.global.controller.capture_hold_ms",
        )
        .and_then(Value::as_integer)
        .unwrap_or(500)
    }

    fn draw_help(&self, fr: &mut Frame) {
        let full = fr.area();
        // as tall as the screen allows (the help scrolls when it still does not fit)
        let area = if self.compact {
            full
        } else {
            centered(
                full,
                100.min(full.width.saturating_sub(4)),
                44.min(full.height.saturating_sub(2)),
            )
        };
        fr.render_widget(Clear, area);
        let hold = self.hold_ms();
        let text = [
            "MOVING AROUND  1-5 (or < >) pick a view: Keyboard, Mouse, Controller, Bindings, Devices. Arrows move on the drawing; Tab switches between the drawing and the list of what the selected input does. Enter opens what is selected. Esc goes back. m (or a right click) opens the action menu: every command is in it.".to_string(),
            "A CONTROLLER drives the editor too: d-pad or left stick = arrows, right stick = page up/down, South = Enter, East = back, West = the action menu, Select = Tab, triggers = previous/next view ([desktop.controls.pad_nav]). The editor reads the pad only while its terminal has focus.".to_string(),
            "THE MOUSE clicks keys, buttons, tabs, layers and list rows; a second click opens; the wheel scrolls.".to_string(),
            "LAYERS  l / L step through the modifier layers (no modifier, SUPER, SUPER+SHIFT, ...), the tmux-os prefix layers (CTRL+b then ...) and the controller layers (desktop, Guide held, Hypr3D room, captured app). Keys bound on the layer light up; its held modifiers and prefix keys glow; conflicts are red.".to_string(),
            "EVERY USE  The list shows everything the selected input does: global bindings, compositor binds, tmux-os keys, terminal-reserved chords, the Guide layer, the Hypr3D room and fixed roles. A dash marks a read-only one (its place is shown).".to_string(),
            "REBIND  r on a use, then move to the new input and press Enter (or press c and then the physical input). a changes the action (a list, or type one), n binds something new on the selected input, d deletes. Realized compositor binds move together with their global binding; Guide-layer and room buttons swap.".to_string(),
            "CAPTURE  c, then press a key, mouse button or wheel, controller button, trigger or stick: it is selected on its drawing. Esc cancels; it stops by itself after [desktop.controls].capture_timeout_ms.".to_string(),
            format!("TAKING CONTROL  Tap North: the window you aim at gets every control and you stay in the room. Hold North {hold} ms: fullscreen. Hold {} {hold} ms: control returns to the desktop.", self.release_label()),
            "SEARCH  / finds keys, buttons, actions and sources; Enter jumps to a result on its drawing.".to_string(),
            "DEVICES  the connected keyboards, mice and controllers with their family, buttons and a profile of what is bound; Enter shows that device's drawing (its missing buttons are dimmed). f cycles the controller drawing (Xbox, PlayStation, Nintendo, generic).".to_string(),
            "SAVING  Changes are a draft. u undoes, U redoes, z throws the draft away, R starts from the defaults. w applies only when the draft adds no conflict (the compositor session and tmux-os keys are re-rendered and checked first). e / i export or import the binding map.".to_string(),
        ]
        .join("\n\n");
        fr.render_widget(
            Paragraph::new(text)
                .wrap(Wrap { trim: true })
                .scroll((self.help_scroll, 0))
                .block(self.block(" help: arrows scroll, any other key closes ", true)),
            area,
        );
    }
}

const ASCII_BORDER: ratatui::symbols::border::Set = ratatui::symbols::border::Set {
    top_left: "+",
    top_right: "+",
    bottom_left: "+",
    bottom_right: "+",
    vertical_left: "|",
    vertical_right: "|",
    horizontal_top: "-",
    horizontal_bottom: "-",
};

/// The drawing's inputs a device does not have: mouse buttons and wheel axes it never reports, controller buttons,
/// triggers, d-pad and stick directions without a key or axis. A keyboard lacks nothing (every layout key is drawn).
pub fn absent_inputs(d: &Device) -> BTreeSet<String> {
    let mut out = BTreeSet::new();
    match d.kind {
        Kind::Mouse => {
            for (code, _, _) in layout::MOUSE_BUTTONS {
                if !d.has(*code) {
                    out.insert(format!("mouse:{code}"));
                }
            }
            if !d.rel.contains(&8) {
                out.insert("mouse:wheel_up".into());
                out.insert("mouse:wheel_down".into());
            }
            if !d.rel.contains(&6) {
                out.insert("mouse:wheel_left".into());
                out.insert("mouse:wheel_right".into());
            }
        }
        Kind::Controller => {
            for id in layout::PAD_INPUTS {
                let present = match *id {
                    b if b.starts_with("BTN_DPAD_") => {
                        d.has(mios_resolver::keybindings::button_code(b).unwrap_or(0))
                            || d.abs.contains(&16)
                    }
                    "BTN_TL2" => d.has(0x138) || d.abs.contains(&2),
                    "BTN_TR2" => d.has(0x139) || d.abs.contains(&5),
                    b if b.starts_with("LS_") => d.abs.contains(&0),
                    b if b.starts_with("RS_") => d.abs.contains(&3) || d.abs.contains(&5),
                    b => d.has(mios_resolver::keybindings::button_code(b).unwrap_or(0)),
                };
                if !present {
                    out.insert(format!("pad:{id}"));
                }
            }
        }
        Kind::Keyboard => {}
    }
    out
}

fn verb(v: &str) -> Option<Act> {
    Some(match v {
        "enter" => Act::Enter,
        "back" => Act::Back,
        "menu" => Act::Menu,
        "capture" => Act::Capture,
        "focus" => Act::FocusNext,
        "help" => Act::Help,
        "layer-prev" => Act::LayerPrev,
        "layer-next" => Act::LayerNext,
        "view-prev" => Act::ViewPrev,
        "view-next" => Act::ViewNext,
        "page-up" => Act::PageUp,
        "page-down" => Act::PageDown,
        _ => return None,
    })
}

/// A layer string back into its earlier steps and modifiers ("CTRL+b then SHIFT" -> (["CTRL+b"], ["SHIFT"])).
pub fn parse_layer(layer: &str) -> (Vec<String>, Vec<String>) {
    if layer.starts_with("pad:") || layer.starts_with("tmux-os") || layer == "*" {
        return (Vec::new(), Vec::new());
    }
    let (pre, mods) = match layer.split_once(" then") {
        Some((p, m)) => (
            p.split(' ')
                .filter(|s| !s.is_empty())
                .map(str::to_owned)
                .collect(),
            m.trim(),
        ),
        None => (Vec::new(), layer),
    };
    (
        pre,
        mods.split('+')
            .filter(|s| !s.is_empty())
            .map(str::to_owned)
            .collect(),
    )
}

fn order_mods(mods: &[String]) -> Vec<String> {
    bindmap::MODS
        .iter()
        .filter(|m| mods.iter().any(|x| x == *m))
        .map(|m| m.to_string())
        .collect()
}

fn norm_of(pre: &[String], mods: &[String], key: &str) -> String {
    let mut steps = pre.to_vec();
    let mut last = mods.to_vec();
    last.push(key.to_owned());
    steps.push(last.join("+"));
    steps.join(" ")
}

/// A terminal key event as an input id plus the modifiers the terminal reported (for capture).
fn key_input(k: &KeyEvent) -> Option<(String, Vec<String>)> {
    let mut mods = Vec::new();
    if k.modifiers.contains(KeyModifiers::SUPER) {
        mods.push("SUPER".to_string());
    }
    if k.modifiers.contains(KeyModifiers::CONTROL) {
        mods.push("CTRL".into());
    }
    if k.modifiers.contains(KeyModifiers::ALT) {
        mods.push("ALT".into());
    }
    let token = match k.code {
        KeyCode::Char(c) => {
            let (base, shift) = layout::shifted(c)?;
            if shift || k.modifiers.contains(KeyModifiers::SHIFT) && c.is_ascii_alphabetic() {
                mods.push("SHIFT".into());
            }
            base
        }
        KeyCode::Enter => "return".into(),
        KeyCode::Tab => "tab".into(),
        KeyCode::BackTab => {
            mods.push("SHIFT".into());
            "tab".into()
        }
        KeyCode::Backspace => "backspace".into(),
        KeyCode::Delete => "delete".into(),
        KeyCode::Insert => "insert".into(),
        KeyCode::Home => "home".into(),
        KeyCode::End => "end".into(),
        KeyCode::PageUp => "prior".into(),
        KeyCode::PageDown => "next".into(),
        KeyCode::Up => "up".into(),
        KeyCode::Down => "down".into(),
        KeyCode::Left => "left".into(),
        KeyCode::Right => "right".into(),
        KeyCode::F(n) if (1..=12).contains(&n) => format!("f{n}"),
        KeyCode::Menu => "menu".into(),
        KeyCode::CapsLock => "caps_lock".into(),
        KeyCode::ScrollLock => "scroll_lock".into(),
        KeyCode::NumLock => "num_lock".into(),
        KeyCode::PrintScreen => "print".into(),
        KeyCode::Pause => "pause".into(),
        _ => return None,
    };
    if k.modifiers.contains(KeyModifiers::SHIFT)
        && !mods.iter().any(|m| m == "SHIFT")
        && !matches!(k.code, KeyCode::Char(_))
    {
        mods.push("SHIFT".into());
    }
    Some((format!("key:{token}"), order_mods(&mods)))
}

/// A human title for an input id.
fn input_title(input: &str, fam: &Family, g: Glyphs) -> String {
    match input.split_once(':') {
        Some(("key", k)) => format!("key {k}"),
        Some(("mouse", _)) => layout::mouse_name(input),
        Some(("pad", b)) if b.starts_with("BTN_") && !b.starts_with("BTN_DPAD") => {
            format!("{} ({b})", fam.label(b, g.ascii_labels))
        }
        Some(("pad", b)) => b.replace('_', " "),
        _ => input.into(),
    }
}

fn centered(area: Rect, w: u16, h: u16) -> Rect {
    let w = w.min(area.width);
    let h = h.min(area.height);
    Rect::new(
        area.x + (area.width - w) / 2,
        area.y + (area.height - h) / 2,
        w,
        h,
    )
}

/// [desktop.controls].pad_names: the controllers the editor reads (empty = all).
fn pad_names(merged: &Value) -> Vec<String> {
    at(merged, "desktop.controls.pad_names")
        .and_then(Value::as_array)
        .map(|a| {
            a.iter()
                .filter_map(Value::as_str)
                .filter(|s| !s.is_empty())
                .map(str::to_owned)
                .collect()
        })
        .unwrap_or_default()
}

pub fn run(layers: Layers) -> Result<(), String> {
    let (tx, rx) = mpsc::channel();
    let merged = layers.effective();
    let input_cpu = crate::controls_int(&merged, "input_cpu");
    let f = |k: &str, d: f64| {
        at(&merged, &format!("desktop.controls.{k}"))
            .and_then(|v| v.as_float().or(v.as_integer().map(|i| i as f64)))
            .unwrap_or(d)
    };
    let pad_cfg = crate::pads::PadConfig {
        stick_threshold: f("stick_threshold", 0.5).clamp(0.05, 0.95),
        trigger_threshold: f("trigger_threshold", 0.3).clamp(0.05, 0.95),
        repeat_delay: Duration::from_millis(
            crate::controls_int(&merged, "nav_repeat_delay_ms")
                .unwrap_or(400)
                .max(50) as u64,
        ),
        repeat_every: Duration::from_millis(
            crate::controls_int(&merged, "nav_repeat_ms")
                .unwrap_or(120)
                .max(30) as u64,
        ),
        pad_names: pad_names(&merged),
    };
    let mut app = App::new(layers, tx.clone());
    let mut terminal = ratatui::init();
    let _ = execute!(std::io::stdout(), EnableMouseCapture, EnableFocusChange);
    let (_note, cpu) = crate::input::spawn(tx.clone(), input_cpu);
    app.pads = crate::pads::spawn(tx, cpu, pad_cfg);
    app.revalidate();
    let res = (|| -> Result<(), String> {
        loop {
            terminal.draw(|f| app.draw(f)).map_err(|e| e.to_string())?;
            // Block until something happens: input, a pad edge, a validation result. A capture waits at most until
            // its deadline. No polling interval otherwise.
            let msg = match app.capture_timeout_left() {
                Some(left) => match rx.recv_timeout(left) {
                    Ok(m) => Some(m),
                    Err(mpsc::RecvTimeoutError::Timeout) => None,
                    Err(e) => return Err(e.to_string()),
                },
                None => Some(rx.recv().map_err(|e| e.to_string())?),
            };
            match msg {
                Some(Msg::Input(ev)) => app.handle(ev),
                Some(Msg::InputFailed(e)) => return Err(format!("terminal input failed: {e}")),
                Some(Msg::Validated(gen, res)) => app.validated(gen, res),
                Some(Msg::Pad(e)) => app.pad(e),
                Some(Msg::PadStatus(s)) => {
                    if s.starts_with("capture:") {
                        app.status = s.clone();
                    }
                    app.pad_status = s;
                }
                None => {}
            }
            app.tick();
            if app.quit {
                return Ok(());
            }
        }
    })();
    app.pads = None;
    let _ = execute!(std::io::stdout(), DisableMouseCapture, DisableFocusChange);
    ratatui::restore();
    res
}

/// Find a use by a CLI reference: global:ID, bypass:ID, guide:BTN, room:BTN, tmux:KEY, table:PATH#N (1-based).
pub fn find_use<'a>(uses: &'a [Use], reference: &str) -> Result<&'a Use, String> {
    let (kind, rest) = reference.split_once(':').ok_or(
        "a reference is global:ID, bypass:ID, guide:BTN, room:BTN, tmux:KEY or table:PATH#N",
    )?;
    uses.iter()
        .find(|u| match (&u.source, kind) {
            (Source::Global { id, bypass: false }, "global") => id == rest,
            (Source::Global { id, bypass: true }, "bypass") => id == rest,
            (Source::Guide { button }, "guide") => button == rest && u.layer == PAD_GUIDE,
            (Source::Room { button }, "room") => button == rest,
            (Source::Tmux { .. }, "tmux") => u.chord.split_once(' ').map(|(_, k)| k) == Some(rest),
            (Source::Table { path, index }, "table") => rest
                .split_once('#')
                .is_some_and(|(p, n)| p == path && n.parse::<usize>().ok() == Some(index + 1)),
            _ => false,
        })
        .ok_or_else(|| format!("no editable use {reference}"))
}

/// The headless rebind behind `mios-controls rebind`: the same move the TUI makes, on a fresh draft.
pub fn cli_rebind(
    layers: Layers,
    reference: &str,
    to: &str,
    layer: Option<&str>,
) -> Result<(Draft, String), String> {
    let (tx, _rx) = mpsc::channel();
    let mut app = App::new(layers, tx);
    let u = find_use(&app.uses, reference)?.clone();
    let target = normalize_input(to)?;
    let (target, extra, chord_layer) = match target {
        (t, Some(l)) => (t, Vec::new(), l),
        (t, None) => (
            t,
            Vec::new(),
            layer.map(str::to_owned).unwrap_or_else(|| u.layer.clone()),
        ),
    };
    let what = app.rebind(&u, &target, &chord_layer, &extra)?;
    Ok((app.draft, what))
}

/// An input given on the command line: an id (key:x, mouse:275, pad:BTN_WEST) or a chord ("SUPER+SHIFT+x", which
/// also fixes the layer).
pub fn normalize_input(s: &str) -> Result<(String, Option<String>), String> {
    if let Some((k, _)) = s.split_once(':') {
        if ["key", "mouse", "pad"].contains(&k) && !s.ends_with(':') {
            return Ok((s.to_owned(), None));
        }
        return Err(format!("{s:?} is not a supported input id (key:, mouse:, pad:)"));
    }
    if s.starts_with("BTN_") {
        return Ok((format!("pad:{s}"), None));
    }
    let (pre, mods, key) = bindmap::split_chord(s, " ")
        .ok_or_else(|| format!("{s:?} is neither an input id nor a chord"))?;
    let input =
        layout::mouse_input(&key).unwrap_or_else(|| format!("key:{}", layout::key_alias(&key)));
    Ok((input, Some(bindmap::layer_of(&pre, &mods))))
}

/// Render one device drawing as text (the `layout` verb and tests): the same painter the TUI uses.
#[allow(clippy::too_many_arguments)]
pub fn render_drawing(
    layers: Layers,
    view: &str,
    width: u16,
    height: u16,
    family: Option<&str>,
    layer: Option<&str>,
    glyphs: Option<&str>,
    lua: Option<String>,
    select: Option<&str>,
) -> Result<String, String> {
    let (tx, _rx) = mpsc::channel();
    let mut app = App::new(layers, tx);
    if let Some(g) = glyphs {
        app.glyphs = Glyphs::for_term(g, &std::env::var("TERM").unwrap_or_default());
    }
    if lua.is_some() {
        app.lua = lua;
        app.refresh_uses();
    }
    app.view = match view {
        "keyboard" => View::Keyboard,
        "mouse" => View::Mouse,
        "controller" | "pad" => View::Controller,
        "bindings" => View::Bindings,
        "devices" => View::Devices,
        v => {
            return Err(format!(
                "unknown view {v} (keyboard, mouse, controller, bindings, devices)"
            ))
        }
    };
    if let Some(f) = family {
        // an id (xbox), a title (PlayStation) or a name hint (dualsense, dualshock, joy-con), any case
        let want = f.to_lowercase();
        let pos = app
            .families
            .iter()
            .position(|x| x.id == want || x.title.to_lowercase() == want)
            .or_else(|| {
                app.families
                    .iter()
                    .position(|x| x.name_hints.iter().any(|h| h.to_lowercase() == want))
            });
        let ids: Vec<&str> = app.families.iter().map(|x| x.id.as_str()).collect();
        app.family_override =
            Some(pos.ok_or_else(|| format!("unknown family {f} ({})", ids.join(", ")))?);
    }
    if let Some(l) = layer {
        let kind = app.kind();
        let list = app.layer_list(kind);
        let want = match l {
            "plain" | "none" => "",
            "guide" => PAD_GUIDE,
            "room" => PAD_ROOM,
            "desktop" => PAD_DESKTOP,
            "captured" => bindmap::PAD_CAPTURED,
            other => other,
        };
        app.layer[kind_index(kind)] = list
            .iter()
            .position(|x| x == want)
            .ok_or_else(|| format!("no layer {l:?}; layers: {}", list.join(", ")))?;
    }
    if let Some(s) = select {
        let (input, l) = normalize_input(s)?;
        let kind = input.split(':').next().unwrap_or("key").to_owned();
        app.view = view_of(&kind);
        app.cursor[kind_index(&kind)] = input;
        if let Some(l) = l {
            let list = app.layer_list(&kind);
            if let Some(i) = list.iter().position(|x| *x == l) {
                app.layer[kind_index(&kind)] = i;
            }
        }
        app.focus = Focus::Uses;
    }
    let mut term = ratatui::Terminal::new(ratatui::backend::TestBackend::new(width, height))
        .map_err(|e| e.to_string())?;
    term.draw(|f| app.draw(f)).map_err(|e| e.to_string())?;
    let buf = term.backend().buffer().clone();
    Ok((0..height)
        .map(|y| {
            (0..width)
                .map(|x| buf[(x, y)].symbol().to_string())
                .collect::<String>()
                .trim_end()
                .to_owned()
                + "\n"
        })
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    const BASE: &str = "[keybindings]\ntmux_prefix = \"C-b\"\n[keybindings.tmux]\nbindings = [ { key = \"h\", command = \"select-pane -L\", note = \"Pane left\" } ]\n[keybindings.global]\nchord_sep = \" \"\naction_namespaces = [\"hypr\", \"exec\", \"mios\"]\n[[keybindings.global.bindings]]\nid = \"terminal\"\ntier = 1\nchord = \"SUPER+Return\"\naction = \"exec:mios-terminal\"\n[[keybindings.global.bindings]]\nid = \"close-window\"\ntier = 1\nchord = \"SUPER+c\"\naction = \"hypr:killactive\"\nrealized_by = \"hyprland\"\n[keybindings.global.controller]\nguide_button = \"BTN_MODE\"\n[keybindings.global.controller.system_layer]\nBTN_SOUTH = \"hypr:togglefloating\"\nBTN_EAST = \"hypr:killactive\"\n[keybindings.global.controller.room]\nBTN_EAST = \"hypr:hypr3d:cancel\"\n[desktop.hyprland]\nbindings = [ { key = \"SUPER + C\", dispatcher = \"window.close\", label = \"Close window\" } ]\n[desktop.controls]\neditable = [\"keybindings.global.bindings\", \"keybindings.global.controller.system_layer\", \"keybindings.global.controller.room\", \"keybindings.tmux.bindings\", \"desktop.hyprland.bindings\"]\n";

    fn app() -> App {
        let base: Value = BASE.parse().unwrap();
        let layers = Layers {
            root: "/nonexistent".into(),
            user_path: "/nonexistent/u.toml".into(),
            base,
            user_text: String::new(),
            user_value: Value::Table(Default::default()),
        };
        App::new(layers, mpsc::channel().0)
    }

    fn render(app: &mut App, w: u16, h: u16) -> String {
        let mut term = ratatui::Terminal::new(ratatui::backend::TestBackend::new(w, h)).unwrap();
        term.draw(|f| app.draw(f)).unwrap();
        let buf = term.backend().buffer().clone();
        (0..h)
            .map(|y| {
                (0..w)
                    .map(|x| buf[(x, y)].symbol().to_string())
                    .collect::<String>()
                    + "\n"
            })
            .collect()
    }

    fn key(a: &mut App, c: KeyCode) {
        a.handle(Event::Key(KeyEvent::new(c, KeyModifiers::NONE)));
    }

    fn use_of(a: &App, input: &str, word: &str) -> Use {
        a.uses
            .iter()
            .find(|u| u.input == input && u.source.word() == word)
            .cloned()
            .unwrap_or_else(|| {
                panic!(
                    "{input} {word}: {:#?}",
                    a.uses
                        .iter()
                        .filter(|u| u.input == input)
                        .collect::<Vec<_>>()
                )
            })
    }

    #[test]
    fn every_view_fits_pip_phone_console_and_desktop_sizes() {
        for (w, h) in [(40, 12), (66, 18), (80, 24), (100, 30), (170, 46)] {
            let mut a = app();
            for v in VIEWS {
                a.set_view(v);
                for focus in [Focus::Drawing, Focus::Uses] {
                    a.focus = focus;
                    let s = render(&mut a, w, h);
                    assert!(s.contains("MiOS Controls"), "{w}x{h} {v:?}:\n{s}");
                }
            }
            a.help = true;
            let s = render(&mut a, w, h);
            assert!(s.contains("MOVING AROUND"), "help at {w}x{h}:\n{s}");
            a.help = false;
            a.open_menu();
            let s = render(&mut a, w, h);
            assert!(s.contains("Search"), "menu at {w}x{h}:\n{s}");
        }
    }

    #[test]
    fn controller_notes_show_under_a_tall_drawing_only() {
        let mut a = app();
        a.set_view(View::Controller);
        let tall = render(&mut a, 170, 60);
        assert!(
            tall.contains("[Touchpad]") && tall.contains("[Motion]"),
            "tall controller view lists the notes:\n{tall}"
        );
        let short = render(&mut a, 80, 24);
        assert!(
            !short.contains("[Motion]"),
            "an 80x24 console keeps the rows for the drawing:\n{short}"
        );
    }

    #[test]
    fn keyboard_drawing_has_boxed_caps_and_bound_keys_highlight_on_their_layer() {
        let mut a = app();
        let s = render(&mut a, 170, 46);
        assert!(
            s.contains("Esc") && s.contains("Bksp") && s.contains("PgUp") && s.contains("Num"),
            "full layout:\n{s}"
        );
        assert!(
            s.contains('\u{252c}') || s.contains('\u{253c}'),
            "shared borders become junctions:\n{s}"
        );
        let list = a.layer_list("key");
        a.layer[0] = list.iter().position(|l| l == "SUPER").unwrap();
        a.cursor[0] = "key:return".into();
        let s = render(&mut a, 170, 46);
        assert!(
            s.contains("exec:mios-terminal"),
            "the uses list shows the binding of the selected key:\n{s}"
        );
        let buf = {
            let mut term =
                ratatui::Terminal::new(ratatui::backend::TestBackend::new(170, 46)).unwrap();
            term.draw(|f| a.draw(f)).unwrap();
            term.backend().buffer().clone()
        };
        let enter = a
            .hits
            .iter()
            .find(|(_, h)| *h == Hit::Input("key:return".into()))
            .map(|(r, _)| *r)
            .unwrap();
        let cell = &buf[(enter.x, enter.y)];
        assert_eq!(
            cell.fg, a.theme.accent,
            "a bound key's cap border is the accent colour"
        );
        let super_cap = a
            .hits
            .iter()
            .find(|(_, h)| *h == Hit::Input("key:SUPER".into()))
            .map(|(r, _)| *r)
            .unwrap();
        assert_eq!(
            buf[(super_cap.x, super_cap.y)].fg,
            a.theme.warn,
            "the layer's held modifier glows"
        );
    }

    #[test]
    fn vt_drawings_are_ascii_safe_labels() {
        let mut a = app();
        a.glyphs = Glyphs::for_term("ascii", "linux");
        for v in [View::Keyboard, View::Mouse, View::Controller] {
            a.set_view(v);
            let s = render(&mut a, 120, 40);
            let drawing: String = s.lines().skip(3).take(20).collect::<Vec<_>>().join("\n");
            assert!(drawing.is_ascii(), "{v:?} not ASCII:\n{drawing}");
        }
    }

    #[test]
    fn controller_family_changes_glyphs_and_layout() {
        let mut a = app();
        a.families = Family::from_ssot(&format!("{}\n[[desktop.controls.pad_families]]\nid=\"playstation\"\ntitle=\"PlayStation\"\nlayout=\"symmetric\"\nlabels={{BTN_SOUTH=\"\\u2715\", BTN_START=\"Options\"}}\nvt_labels={{BTN_SOUTH=\"X\"}}\n", BASE).parse::<Value>().unwrap());
        a.set_view(View::Controller);
        a.family_override = Some(
            a.families
                .iter()
                .position(|f| f.id == "playstation")
                .unwrap(),
        );
        a.glyphs = Glyphs::for_term("unicode", "xterm");
        let s = render(&mut a, 170, 46);
        assert!(s.contains('\u{2715}') && s.contains("Options"), "{s}");
        a.glyphs = Glyphs::for_term("auto", "linux");
        let s = render(&mut a, 170, 46);
        assert!(!s.contains('\u{2715}'), "the VT gets the ASCII label");
    }

    #[test]
    fn navigation_by_arrows_mouse_and_pad_selects_inputs_and_uses() {
        let mut a = app();
        render(&mut a, 170, 46);
        a.cursor[0] = "key:q".into();
        key(&mut a, KeyCode::Right);
        assert_eq!(a.cursor(), "key:w");
        key(&mut a, KeyCode::Down);
        assert_eq!(a.cursor(), "key:s");
        let r = a
            .hits
            .iter()
            .find(|(_, h)| *h == Hit::Input("key:return".into()))
            .map(|(r, _)| *r)
            .unwrap();
        a.handle(Event::Mouse(MouseEvent {
            kind: MouseEventKind::Down(MouseButton::Left),
            column: r.x + 1,
            row: r.y + 1,
            modifiers: KeyModifiers::NONE,
        }));
        assert_eq!(a.cursor(), "key:return");
        a.pad(PadEdge {
            device: "pad".into(),
            input: "pad:BTN_SOUTH".into(),
            down: true,
            repeat: false,
        });
        assert_eq!(a.focus, Focus::Uses, "South = Enter: into the list");
        a.pad(PadEdge {
            device: "pad".into(),
            input: "pad:BTN_WEST".into(),
            down: true,
            repeat: false,
        });
        assert!(a.menu.is_some(), "West = the action menu");
        a.pad(PadEdge {
            device: "pad".into(),
            input: "pad:BTN_EAST".into(),
            down: true,
            repeat: false,
        });
        assert!(a.menu.is_none(), "East closes it");
        a.pad(PadEdge {
            device: "pad".into(),
            input: "pad:BTN_TR2".into(),
            down: true,
            repeat: false,
        });
        assert_eq!(a.view, View::Mouse, "RT = next view");
        a.focused = false;
        a.pad(PadEdge {
            device: "pad".into(),
            input: "pad:BTN_TR2".into(),
            down: true,
            repeat: false,
        });
        assert_eq!(a.view, View::Mouse, "an unfocused editor ignores the pad");
    }

    #[test]
    fn the_controller_drawing_follows_the_pad_that_was_used() {
        let mut a = app();
        a.devices = crate::devices::parse("I: Bus=0003 Vendor=04d9 Product=a293\nN: Name=\"OBINS AnnePro2\"\nH: Handlers=event13 js0\nB: KEY=ffff000000000000 0 0 0 0\nB: ABS=3b\n\nI: Bus=0003 Vendor=045e Product=0b12\nN: Name=\"mios-input-fixture-t-xbox\"\nH: Handlers=event40 js1\nB: KEY=7cdb000000000000 0 0 0 0\nB: ABS=3003f\n");
        a.families.insert(
            0,
            Family {
                id: "xbox".into(),
                vendors: vec![0x045e],
                ..Family::generic()
            },
        );
        a.pad_dev = Some(0);
        assert_eq!(a.family().id, "generic");
        a.pad(PadEdge {
            device: "mios-input-fixture-t-xbox".into(),
            input: "pad:BTN_TR2".into(),
            down: true,
            repeat: false,
        });
        assert_eq!(a.pad_dev, Some(1));
        assert_eq!(a.family().id, "xbox", "the Xbox glyphs follow the Xbox pad");
        a.pad(PadEdge {
            device: "OBINS AnnePro2".into(),
            input: "pad:BTN_TR2".into(),
            down: false,
            repeat: false,
        });
        assert_eq!(a.pad_dev, Some(1), "a release does not switch the drawing");
    }

    #[test]
    fn capture_selects_the_pressed_input_and_finishes_a_pending_rebind() {
        let mut a = app();
        render(&mut a, 170, 46);
        key(&mut a, KeyCode::Char('c'));
        assert!(a.capture.is_some());
        a.handle(Event::Key(KeyEvent::new(
            KeyCode::Char('Q'),
            KeyModifiers::SHIFT,
        )));
        assert!(a.capture.is_none());
        assert_eq!(a.cursor(), "key:q");
        a.set_view(View::Controller);
        key(&mut a, KeyCode::Char('c'));
        a.pad(PadEdge {
            device: "fixture".into(),
            input: "pad:BTN_NORTH".into(),
            down: true,
            repeat: false,
        });
        assert_eq!((a.view, a.cursor()), (View::Controller, "pad:BTN_NORTH"));
        // rebind Guide+South onto West (empty, user-removable source not needed: South is vendor -> West empty fails)
        let u = use_of(&a, "pad:BTN_SOUTH", "guide");
        a.pending = Some(u.clone());
        key(&mut a, KeyCode::Char('c'));
        a.pad(PadEdge {
            device: "fixture".into(),
            input: "pad:BTN_EAST".into(),
            down: true,
            repeat: false,
        });
        assert_eq!(
            a.draft.system_layer.get("BTN_EAST").map(String::as_str),
            Some("hypr:togglefloating"),
            "swapped onto East"
        );
        assert_eq!(
            a.draft.system_layer.get("BTN_SOUTH").map(String::as_str),
            Some("hypr:killactive")
        );
        key(&mut a, KeyCode::Char('u'));
        assert_eq!(a.draft, a.saved, "undo restores the draft");
        key(&mut a, KeyCode::Char('U'));
        assert_ne!(a.draft, a.saved, "redo re-applies it");
        a.pending = Some(u);
        let before = a.draft.clone();
        a.cursor[2] = "pad:BTN_WEST".into();
        a.act(Act::Enter);
        assert_eq!(
            a.draft, before,
            "a vendor Guide button cannot move onto an empty one (it cannot be emptied)"
        );
        assert!(a.status.contains("cannot be emptied"), "{}", a.status);
    }

    #[test]
    fn capture_times_out_and_escape_cancels() {
        let mut a = app();
        a.capture_timeout = Duration::from_millis(1);
        a.act(Act::Capture);
        std::thread::sleep(Duration::from_millis(3));
        a.tick();
        assert!(a.capture.is_none() && a.status.contains("timed out"));
        a.act(Act::Capture);
        key(&mut a, KeyCode::Esc);
        assert!(a.capture.is_none() && a.status.contains("cancelled"));
    }

    #[test]
    fn rebinding_a_realized_global_binding_moves_its_compositor_row() {
        let mut a = app();
        let u = use_of(&a, "key:c", "global");
        let w = a.rebind(&u, "key:x", "SUPER", &[]).unwrap();
        assert!(w.contains("SUPER+x"), "{w}");
        assert_eq!(field(&a.draft.bindings[1], "chord"), "SUPER+x");
        assert_eq!(
            field(&a.draft.tables["desktop.hyprland.bindings"][0], "key"),
            "SUPER + X",
            "the realizing table row moved too"
        );
        let mut a = app();
        let u = use_of(&a, "key:c", "desktop");
        a.rebind(&u, "key:v", "SUPER", &[]).unwrap();
        assert_eq!(
            field(&a.draft.bindings[1], "chord"),
            "SUPER+v",
            "moving the table row moves the global binding it realizes"
        );
        let mut a = app();
        let u = use_of(&a, "key:return", "global");
        assert!(
            a.rebind(&u, "key:SHIFT", "SUPER", &[]).is_err(),
            "a modifier is not a key"
        );
        a.rebind(&u, "mouse:275", "SUPER", &[]).unwrap();
        assert_eq!(
            field(&a.draft.bindings[0], "chord"),
            "SUPER+mouse:275",
            "a key binding can move to a mouse button"
        );
        a.refresh_uses();
        let u = use_of(&a, "mouse:275", "global");
        a.rebind(&u, "pad:BTN_TL", "", &[]).unwrap();
        assert_eq!(field(&a.draft.bindings[0], "chord"), "BTN_TL");
        a.refresh_uses();
        assert!(
            a.rebind(&use_of(&a, "pad:BTN_TL", "global"), "pad:LS_UP", "", &[])
                .is_err(),
            "stick directions are not chords"
        );
    }

    #[test]
    fn tmux_and_room_rebinds_and_new_bindings() {
        let mut a = app();
        let h = use_of(&a, "key:h", "tmux-os");
        assert!(
            a.rebind(&h, "key:f5", "CTRL+b then", &[]).is_err(),
            "no function keys after the prefix"
        );
        assert!(
            a.rebind(&h, "key:x", "CTRL+b then", &["CTRL".into()])
                .is_err(),
            "no Ctrl after the prefix"
        );
        a.rebind(&h, "key:4", "CTRL+b then SHIFT", &[]).unwrap();
        assert_eq!(field(&a.draft.tmux[0], "key"), "$");
        let r = use_of(&a, "pad:BTN_EAST", "room");
        a.rebind(&r, "pad:BTN_WEST", "", &[]).unwrap();
        assert_eq!(
            a.draft.room.get("BTN_WEST").and_then(Value::as_str),
            Some("hypr:hypr3d:cancel")
        );
        assert_eq!(
            a.draft.room.get("BTN_EAST").and_then(Value::as_str),
            Some("none"),
            "a vendor room button is unbound with none"
        );
        a.create_on("key:f7", "SUPER+SHIFT", "exec:foot").unwrap();
        let b = a
            .draft
            .bindings
            .iter()
            .find(|b| field(b, "id") == "user-f7")
            .unwrap();
        assert_eq!(field(b, "chord"), "SUPER+SHIFT+f7");
        a.create_on("pad:BTN_WEST", PAD_GUIDE, "exec:foot").unwrap();
        assert_eq!(
            a.draft.system_layer.get("BTN_WEST").map(String::as_str),
            Some("exec:foot")
        );
        a.create_on("key:y", "CTRL+b then", "copy-mode").unwrap();
        assert_eq!(field(a.draft.tmux.last().unwrap(), "key"), "y");
        assert_eq!(a.undo.len(), 3, "each new binding is one undo step");
    }

    #[test]
    fn read_only_uses_explain_where_to_change_them() {
        let mut a = app();
        a.cursor[0] = "key:b".into();
        let list = a.layer_list("key");
        a.layer[0] = list.iter().position(|l| l == "CTRL").unwrap();
        a.focus = Focus::Uses;
        a.uses_sel = a
            .panel_uses()
            .iter()
            .position(|i| a.uses[*i].source.word() == "fixed")
            .unwrap();
        a.act(Act::Rebind);
        assert!(
            a.pending.is_none() && a.status.contains("read-only"),
            "{}",
            a.status
        );
    }

    #[test]
    fn search_finds_uses_and_enter_jumps_to_the_drawing() {
        let mut a = app();
        key(&mut a, KeyCode::Char('/'));
        for c in "togglefloat".chars() {
            key(&mut a, KeyCode::Char(c));
        }
        assert!(a
            .panel_uses()
            .iter()
            .any(|i| a.uses[*i].input == "pad:BTN_SOUTH"));
        key(&mut a, KeyCode::Enter);
        key(&mut a, KeyCode::Enter);
        assert_eq!((a.view, a.cursor()), (View::Controller, "pad:BTN_SOUTH"));
        assert!(a.search.is_none());
    }

    #[test]
    fn local_conflicts_are_flagged_before_validation() {
        let mut a = app();
        let u = use_of(&a, "key:return", "global");
        let before = a.draft.clone();
        let w = a.rebind(&u, "key:c", "SUPER", &[]).unwrap();
        a.edit(before, w);
        assert!(
            a.flagged.iter().any(|i| a.uses[*i].input == "key:c"),
            "SUPER+c twice"
        );
    }

    #[test]
    fn layer_strings_parse_back() {
        assert_eq!(
            parse_layer("CTRL+b then SHIFT"),
            (vec!["CTRL+b".to_string()], vec!["SHIFT".to_string()])
        );
        assert_eq!(
            parse_layer("SUPER+CTRL"),
            (vec![], vec!["SUPER".to_string(), "CTRL".to_string()])
        );
        assert_eq!(parse_layer(""), (vec![], vec![]));
        assert_eq!(parse_layer(PAD_GUIDE), (vec![], vec![]));
    }

    #[test]
    fn vt_gets_named_colours() {
        std::env::set_var("TERM", "linux");
        let t = Theme::from(&Value::Table(Default::default()));
        assert_eq!(t.accent, Color::Cyan);
    }
}
