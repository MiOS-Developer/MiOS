// AI-hint: Per-controller input state machine: ownership contract first, then the nav projection of uncaptured windows (tap/hold, repeat, flicks, HAT fold, thumb/Guide disambiguation); system actions never enter app replay.
// AI-related: usr/libexec/mios/mios-gamepad-hypr3d, keybindings.global.controller
use crate::config::{axis_code, button_code, ControllerConfig, GlobalConfig, LegacyConfig};
use crate::nav::{Dir, Input, Value, BTN_DPAD_DOWN, BTN_DPAD_LEFT, BTN_DPAD_RIGHT, BTN_DPAD_UP};
use serde::{Deserialize, Serialize};
use std::collections::{BTreeMap, BTreeSet};
pub const EV_SYN: u16 = 0;
pub const EV_KEY: u16 = 1;
pub const EV_REL: u16 = 2;
pub const EV_ABS: u16 = 3;
pub const SYN_REPORT: u16 = 0;
pub const SYN_DROPPED: u16 = 3;
#[derive(Clone, Copy, Debug, Deserialize, Serialize, PartialEq, Eq)]
pub struct Event {
    pub kind: u16,
    pub code: u16,
    pub value: i32,
}
impl Event {
    pub fn new(kind: u16, code: u16, value: i32) -> Self {
        Self { kind, code, value }
    }
}
#[derive(Clone, Copy, Debug, Deserialize, Serialize)]
pub struct Axis {
    pub min: i32,
    pub max: i32,
    pub value: i32,
}
impl Axis {
    pub fn centered(&self) -> f64 {
        if self.max <= self.min {
            return 0.0;
        }
        let span = f64::from(self.max) - f64::from(self.min);
        ((f64::from(self.value) - f64::from(self.min)) * 2.0 / span - 1.0).clamp(-1.0, 1.0)
    }
    pub fn trigger(&self) -> f64 {
        if self.max <= self.min {
            return 0.0;
        }
        ((f64::from(self.value) - f64::from(self.min))
            / (f64::from(self.max) - f64::from(self.min)))
        .clamp(0.0, 1.0)
    }
    pub fn neutral(&self, code: u16) -> i32 {
        if [2, 5].contains(&code) {
            self.min
        } else {
            ((i64::from(self.min) + i64::from(self.max)) / 2) as i32
        }
    }
}
#[derive(Default, Clone, Debug, Serialize)]
pub struct Output {
    pub app: Vec<Event>,
    pub pointer: Vec<Event>,
    pub actions: Vec<String>,
}
impl Output {
    pub fn extend(&mut self, other: Self) {
        self.app.extend(other.app);
        self.pointer.extend(other.pointer);
        self.actions.extend(other.actions);
    }
}
#[derive(Default, Clone, Debug, Serialize)]
pub struct PadState {
    pub lx: f32,
    pub ly: f32,
    pub rx: f32,
    pub ry: f32,
    pub rt: bool,
    pub lt: bool,
    pub south: bool,
    pub west: bool,
    pub sequences: [u32; 8],
}
impl PadState {
    pub fn packet(&self, now_ns: i64) -> [u8; 68] {
        let mut b = [0; 68];
        b[0..4].copy_from_slice(&0x4d493344u32.to_le_bytes());
        b[4..8].copy_from_slice(&1u32.to_le_bytes());
        b[8..16].copy_from_slice(&now_ns.to_le_bytes());
        for (i, v) in [self.lx, self.ly, self.rx, self.ry].into_iter().enumerate() {
            b[16 + i * 4..20 + i * 4].copy_from_slice(&v.to_le_bytes());
        }
        for (i, v) in [self.rt, self.lt, self.south, self.west]
            .into_iter()
            .enumerate()
        {
            b[32 + i] = u8::from(v);
        }
        for (i, v) in self.sequences.into_iter().enumerate() {
            b[36 + i * 4..40 + i * 4].copy_from_slice(&v.to_le_bytes());
        }
        b
    }
    pub fn seed_sequences(&mut self, b: &[u8]) {
        if b.len() != 68 || b[..4] != 0x4d493344u32.to_le_bytes() || b[4..8] != 1u32.to_le_bytes() {
            return;
        }
        for i in 0..8 {
            self.sequences[i] = u32::from_le_bytes(b[36 + i * 4..40 + i * 4].try_into().unwrap());
        }
    }
    fn increment(&mut self, i: usize) {
        self.sequences[i] = self.sequences[i].wrapping_add(1);
    }
    pub fn neutralize(&mut self) {
        self.lx = 0.0;
        self.ly = 0.0;
        self.rx = 0.0;
        self.ry = 0.0;
        self.rt = false;
        self.lt = false;
        self.south = false;
        self.west = false;
    }
}
/// Who consumes an axis this frame (INV-5). Exactly one sink per axis per frame, so no stick can move an app and the
/// room camera (or the host pointer) at the same time.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Sink {
    /// The virtual pad: the captured app (or an uncaptured context whose nav profile is "vpad").
    App,
    /// The Hypr3D room packet (camera, movement, grab/resize).
    Room,
    /// The host pointer (cursor mode or half-capture).
    Pointer,
    /// The nav projection of an uncaptured window (I2): sticks become wheel/flicks, the HAT becomes arrows.
    Nav,
}
/// One nav input held down: its value (frozen at press), timers and whether its hold already fired.
#[derive(Clone, Debug)]
struct Held {
    value: Value,
    elapsed: f64,
    fired: bool,
    next: f64,
    repeat: Option<(u64, u64)>,
    /// A thumbstick click resolves on release (it may still become part of the toggle chord).
    deferred: bool,
}
/// Per-device nav state (INV-6: flushed on any context, profile, owner or target change, a Guide press and SYN_DROPPED).
#[derive(Clone, Debug, Default)]
struct NavState {
    held: BTreeMap<Input, Held>,
    profile: Option<String>,
    armed: [bool; 2],
    stick_dir: [Option<Dir>; 2],
    hat: (i32, i32),
    triggers: [bool; 2],
    thumbs_cancelled: bool,
    wheel: f64,
    hwheel: f64,
}
pub struct Router {
    global: GlobalConfig,
    legacy: LegacyConfig,
    pub axes: BTreeMap<u16, Axis>,
    pressed: BTreeSet<u16>,
    app_held: BTreeSet<u16>,
    pointer_held: BTreeSet<u16>,
    suppressed: BTreeSet<u16>,
    blocked: BTreeSet<u16>,
    latched: BTreeSet<String>,
    chord_times: BTreeMap<u16, u64>,
    last_toggle: Option<u64>,
    last_jump: Option<u64>,
    pub context: String,
    pub cursor: bool,
    pub pad: PadState,
    dropping: bool,
    capture_target: Option<(String,u64)>,
    room_capture: bool,
    /// A capture/release this bridge requested that the compositor has not confirmed yet (C-03). It expires after
    /// context_stale_ms, or at once when the ownership transaction fails, and the snapshot decides again.
    capture_override: Option<bool>,
    override_ms: f64,
    north_pending: bool,
    north_elapsed: f64,
    north_fired: bool,
    escape_elapsed: f64,
    escape_fired: bool,
    pending_guide: bool,
    fraction_x: f64,
    fraction_y: f64,
    fraction_scroll: f64,
    fraction_hwheel: f64,
    nav: NavState,
    /// A MiOS tmux client runs under the focused terminal (C-09): selects `<profile>_tmux` over `<profile>_plain`.
    pub tmux: bool,
    /// The pad reports analog triggers only (no BTN_TL2/TR2, e.g. xpad): nav synthesizes their digital edges.
    pub analog_triggers: bool,
    /// Kernel-truth repair is releasing buttons: a repaired release never fires a tap.
    repairing: bool,
    /// Guide pressed alone and not yet held past capture_hold_ms (R-26): its release is the Guide tap.
    guide_tap: Option<f64>,
    /// Guide + a toggle-chord button: the system-layer action waits for the release, so Guide+L3+R3 fires neither.
    guide_thumb: Option<u16>,
    guide_thumb_cancel: bool,
    #[cfg(test)]
    pub(crate) mutant_room_forward: bool,
}
impl Router {
    pub fn new(global: GlobalConfig, legacy: LegacyConfig, axes: BTreeMap<u16, Axis>) -> Self {
        Self {
            global,
            legacy,
            axes,
            pressed: BTreeSet::new(),
            app_held: BTreeSet::new(),
            pointer_held: BTreeSet::new(),
            suppressed: BTreeSet::new(),
            blocked: BTreeSet::new(),
            latched: BTreeSet::new(),
            chord_times: BTreeMap::new(),
            last_toggle: None,
            last_jump: None,
            context: "desktop-2d".into(),
            cursor: false,
            pad: PadState::default(),
            dropping: false,
            capture_target: None,
            room_capture: false,
            capture_override: None,
            override_ms: 0.0,
            north_pending: false,
            north_elapsed: 0.0,
            north_fired: false,
            escape_elapsed: 0.0,
            escape_fired: false,
            pending_guide: false,
            fraction_x: 0.0,
            fraction_y: 0.0,
            fraction_scroll: 0.0,
            fraction_hwheel: 0.0,
            nav: NavState { armed: [true; 2], ..NavState::default() },
            tmux: false,
            analog_triggers: false,
            repairing: false,
            guide_tap: None,
            guide_thumb: None,
            guide_thumb_cancel: false,
            #[cfg(test)]
            mutant_room_forward: false,
        }
    }
    /// Construct and settle the nav profile of the initial context.
    pub fn with_context(global: GlobalConfig, legacy: LegacyConfig, axes: BTreeMap<u16, Axis>, context: &str) -> Self {
        let mut r = Self::new(global, legacy, axes);
        r.set_context(context);
        r
    }
    pub fn policy(&self) -> &GlobalConfig {
        &self.global
    }
    pub fn legacy_toggle_chord(&self) -> Vec<String> {
        self.legacy.toggle_chord.clone()
    }
    /// Capture is decided by ownership alone (North capture, fullscreen==2, capture_tag or the room's input_owner,
    /// cancelled by release_tag): the snapshot names that "app-capture". A window's class (game, VM, remote client,
    /// terminal, nested session, PiP) never captures the controller by itself, so capture and release are the same
    /// for every window kind.
    pub fn owns_controller(context: &str) -> bool { context == "app-capture" }
    fn app_context(context: &str) -> bool { Self::owns_controller(context) }
    pub fn app_owned(&self) -> bool { self.capture_override.unwrap_or_else(||Self::app_context(&self.context)) }
    /// Half-capture (C-01): the app still owns every button; only the sticks and LT/RT drive the host pointer.
    pub fn half_capture(&self) -> bool { self.app_owned() && self.cursor }
    /// The pending (unconfirmed) ownership this router requested, if any.
    pub fn ownership(&self) -> Option<bool> { self.capture_override }
    pub fn set_focus(&mut self, context: &str, target: Option<(String,u64)>, from_room: bool) -> Output {
        let target_changed=self.capture_target!=target;
        let (owned,room)=(self.app_owned(),self.room());
        let force=target_changed && (owned || Self::app_context(context));
        if target_changed || self.capture_override==Some(Self::app_context(context)) {
            self.capture_override=None;
        }
        self.capture_target=target; self.room_capture=from_room || context=="desktop-3d";
        let mut o=self.transition_context(context,force,owned,room);
        // INV-6: a held nav input never repeats into, or resolves its tap/hold in, another window.
        if target_changed { o.extend(self.nav_flush()); }
        o
    }
    /// C-09: whether a MiOS tmux client runs under the focused terminal. A profile change flushes nav state.
    pub fn set_tmux(&mut self, tmux: bool) -> Output {
        self.tmux=tmux;
        self.nav_sync()
    }
    pub fn set_context(&mut self, context: &str) -> Output {
        let (owned,room)=(self.app_owned(),self.room());
        self.transition_context(context,false,owned,room)
    }
    fn transition_context(&mut self, context: &str, force: bool, was_owned: bool, was_room: bool) -> Output {
        // Pointer drags, held keys and cursor mode survive focus moving between uncaptured clients of any kind
        // (and a compositor confirming a capture this bridge already applied). Ownership or room boundaries reset.
        let previous=std::mem::replace(&mut self.context,context.into());
        if !force && self.app_owned()==was_owned && self.room()==was_room {
            return self.nav_sync();
        }
        self.context=previous;
        let mut o = self.release_outputs();
        self.suppressed.extend(&self.pressed);
        self.blocked.extend(&self.pressed);
        self.context = context.into();
        self.cursor = false;
        self.pending_guide = false;
        self.north_pending = false;
        self.last_jump = None;
        self.pad.neutralize();
        // A held axis must be released before the next context owns it.
        for (code, axis) in &mut self.axes {
            axis.value = axis.neutral(*code);
        }
        if !o.app.is_empty() {
            o.app.push(Event::new(EV_SYN, SYN_REPORT, 0));
        }
        o.extend(self.nav_sync());
        o
    }
    pub fn reset(&mut self) -> Output {
        let mut o = self.release_outputs();
        for (code, axis) in &mut self.axes {
            axis.value = axis.neutral(*code);
            o.app.push(Event::new(EV_ABS, *code, axis.value));
        }
        o.app.push(Event::new(EV_SYN, SYN_REPORT, 0));
        self.pressed.clear();
        self.suppressed.clear();
        self.blocked.clear();
        self.latched.clear();
        self.chord_times.clear();
        self.last_jump = None;
        self.pending_guide = false;
        self.north_pending=false;
        self.escape_elapsed=0.0;
        self.escape_fired=false;
        self.pad.neutralize();
        o.extend(self.nav_flush());
        self.guide_tap=None;
        self.guide_thumb=None;
        o
    }
    fn release_outputs(&mut self) -> Output {
        let mut app: Vec<_> = self
            .app_held
            .iter()
            .map(|c| Event::new(EV_KEY, *c, 0))
            .collect();
        self.app_held.clear();
        for (code, axis) in &self.axes {
            app.push(Event::new(EV_ABS, *code, axis.neutral(*code)));
        }
        if !app.is_empty() {
            app.push(Event::new(EV_SYN, SYN_REPORT, 0));
        }
        let mut pointer: Vec<_> = self
            .pointer_held
            .iter()
            .map(|c| Event::new(EV_KEY, *c, 0))
            .collect();
        self.pointer_held.clear();
        if !pointer.is_empty() {
            pointer.push(Event::new(EV_SYN, SYN_REPORT, 0));
        }
        Output {
            app,
            pointer,
            actions: vec![],
        }
    }
    pub fn resync(&mut self, keys: BTreeSet<u16>, axes: BTreeMap<u16, Axis>) -> Output {
        let o = self.reset();
        self.pressed = keys.clone();
        self.blocked = keys.clone();
        self.suppressed = keys;
        self.axes = axes;
        self.dropping = false;
        o
    }
    /// Kernel truth may repair missed releases; it never invents a press or an
    /// action. Preserve genuinely held chords and release already-forwarded
    /// app/pointer buttons so a repaired router cannot leave a guest stuck.
    pub fn reconcile(&mut self, held: &BTreeSet<u16>, now: u64) -> Output {
        if self.dropping { return Output::default(); }
        let released: Vec<_> = self.pressed.difference(held).copied().collect();
        let guide = button_code(&self.global.controller.guide_button).unwrap();
        if !held.contains(&guide) { self.pending_guide = false; }
        if !held.contains(&button_code(&self.legacy.fullscreen_button).unwrap()) { self.north_pending=false; }
        let mut out = Output::default();
        if !held.contains(&guide) { self.guide_tap = None; }
        self.repairing = true;
        for code in released { out.extend(self.event(Event::new(EV_KEY, code, 0), now)); }
        self.repairing = false;
        self.blocked.retain(|c| held.contains(c));
        self.suppressed.retain(|c| held.contains(c));
        self.chord_times.retain(|c, _| held.contains(c));
        self.update_pad();
        if !out.app.is_empty() { out.app.push(Event::new(EV_SYN,SYN_REPORT,0)); }
        out
    }
    fn button(&self, name: &str) -> bool {
        button_code(name).is_ok_and(|c| self.pressed.contains(&c) && !self.blocked.contains(&c))
    }
    fn room(&self) -> bool {
        !self.app_owned() && (self.context == "desktop-3d" || (self.capture_override==Some(false) && self.room_capture))
    }
    /// The single sink of one axis this frame (INV-5).
    pub fn axis_sink(&self, code: u16) -> Sink {
        if self.room() {
            return Sink::Room;
        }
        if self.half_capture() {
            return if self.pointer_axis(code) { Sink::Pointer } else { Sink::App };
        }
        if self.app_owned() {
            return Sink::App;
        }
        if self.pointer_mode() && self.pointer_axis(code) {
            return Sink::Pointer;
        }
        if self.nav_active() {
            return Sink::Nav;
        }
        if self.pointer_mode() {
            return Sink::Pointer;
        }
        Sink::App
    }
    /// Axes the host pointer reads in cursor mode: both sticks and the analog triggers.
    fn pointer_axis(&self, code: u16) -> bool {
        let (rx, ry) = self.right_axes();
        [0, 1, rx, ry].contains(&code)
            || axis_code(&self.legacy.grab_axis).ok() == Some(code)
            || axis_code(&self.legacy.resize_axis).ok() == Some(code)
    }
    fn set_ownership(&mut self, owned: bool) -> Output {
        let room=self.room();
        let out=self.release_outputs();
        if owned { self.room_capture=room; }
        self.capture_override=Some(owned);
        self.override_ms=0.0;
        self.cursor=!owned && !self.room_capture;
        self.north_pending=false;
        self.pending_guide=false;
        self.pad.neutralize();
        self.suppressed.extend(&self.pressed);
        let mut out = out;
        out.extend(self.nav_sync());
        out
    }
    fn capture_window(&mut self, fullscreen: bool) -> Output {
        let mut out=self.set_ownership(true);
        out.actions.push(format!("input:window {}",if fullscreen {"fullscreen"} else {"on"}));
        out
    }
    fn release_window(&mut self) -> Output {
        let mut out=self.set_ownership(false);
        out.actions.push("input:window off".into());
        out
    }
    /// Another controller of this session captured or released the focused window: ownership is per session, never
    /// per pad, so every router follows it at once (no second pad may keep driving the room or the app).
    pub fn adopt_ownership(&mut self, owned: bool) -> Output {
        if self.capture_override==Some(owned) {
            return Output::default();
        }
        self.set_ownership(owned)
    }
    /// The ownership transaction failed (or was never confirmed): drop the pending override and follow the snapshot.
    pub fn ownership_failed(&mut self) -> Output {
        if self.capture_override.is_none() {
            return Output::default();
        }
        let (owned,room)=(self.app_owned(),self.room());
        self.capture_override=None;
        self.override_ms=0.0;
        let context=self.context.clone();
        self.transition_context(&context,false,owned,room)
    }
    fn pointer_mode(&self) -> bool {
        self.cursor && !self.room()
    }
    /// Frames (SYN and non-axis events) reach the app whenever any of its input can.
    fn forwards_frames(&self) -> bool {
        !self.room() && (!self.pointer_mode() || self.half_capture()) && !self.nav_active()
    }
    fn legacy_action(&mut self, code: u16, now: u64, o: &mut Output) -> bool {
        let room = self.room();
        if code == button_code(&self.global.controller.cursor_mode_button).unwrap() {
            if room {
                self.pad.increment(1);
            } else if self
                .global
                .controller
                .cursor_mode_contexts
                .iter()
                .any(|ctx| ctx == &self.context)
                || self.cursor
            {
                o.extend(self.release_outputs());
                self.suppressed.extend(&self.pressed);
                self.cursor = !self.cursor;
                o.actions.push(format!(
                    "input:cursor {}",
                    if self.cursor { "on" } else { "off" }
                ));
            } else {
                return false;
            }
            return true;
        }
        if code == button_code(&self.legacy.shaderglass_toggle_button).unwrap() {
            if room {
                self.pad.increment(6);
            } else {
                o.actions.push("mios:shaderglass toggle".into());
            }
            return true;
        }
        if code == button_code(&self.legacy.shaderglass_cycle_button).unwrap() {
            if room {
                self.pad.increment(7);
            } else {
                o.actions.push("mios:shaderglass cycle".into());
            }
            return true;
        }
        if code == button_code(&self.legacy.fullscreen_button).unwrap() {
            self.north_pending=true;
            self.north_elapsed=0.0;
            self.north_fired=false;
            return true;
        }
        if room {
            if code == button_code(&self.legacy.view_mode_button).unwrap() {
                self.pad.increment(0);
                return true;
            }
            if code == button_code(&self.legacy.jump_button).unwrap() {
                if self.last_jump.is_some_and(|last| {
                    now.saturating_sub(last) >= 20
                        && now.saturating_sub(last) <= self.legacy.double_press_ms
                }) {
                    self.pad.increment(3);
                    self.last_jump = None;
                } else {
                    self.pad.increment(2);
                    self.last_jump = Some(now);
                }
                return true;
            }
            if code == button_code(&self.legacy.crouch_button).unwrap() {
                self.pad.increment(4);
                return true;
            }
        }
        false
    }
    pub fn event(&mut self, e: Event, now: u64) -> Output {
        let mut o = Output::default();
        if e.kind == EV_SYN && e.code == SYN_DROPPED {
            self.dropping = true;
            return self.reset();
        }
        // The evdev caller resynchronizes with EVIOCGKEY/EVIOCGABS after the
        // first SYN_REPORT following a drop. Never act on a partial frame.
        if self.dropping {
            return o;
        }
        if !self.global.enabled || !self.global.controller.enabled {
            o.app.push(e);
            return o;
        }
        if e.kind == EV_KEY {
            let already = self.pressed.contains(&e.code);
            if e.value != 0 {
                self.pressed.insert(e.code);
            } else {
                self.pressed.remove(&e.code);
                self.blocked.remove(&e.code);
                self.chord_times.remove(&e.code);
                self.latched.clear();
            }
            if e.value == 1 && !already {
                self.chord_times.insert(e.code, now);
            }
            let toggle_codes: Vec<u16> = self.legacy.toggle_chord.iter().map(|n| button_code(n).unwrap()).collect();
            // C-13: Guide + a toggle-chord button fires its system-layer action on release, and only if the other
            // chord button never joined (Guide+L3+R3 = neither lock nor the VM latch).
            if e.value == 0 && self.guide_thumb == Some(e.code) {
                self.guide_thumb = None;
                if !self.guide_thumb_cancel && !self.repairing {
                    if let Some(action) = self.system_action(e.code) { o.actions.push(action); }
                }
            }
            if !toggle_codes.iter().any(|c| self.pressed.contains(c)) {
                self.guide_thumb_cancel = false;
                self.nav.thumbs_cancelled = false;
            }
            // R-23: any overlap of the toggle-chord buttons cancels both single clicks, fast (room toggle) or slow.
            if e.value == 1 && toggle_codes.contains(&e.code) && toggle_codes.iter().all(|c| self.pressed.contains(c)) {
                self.nav.thumbs_cancelled = true;
                for c in &toggle_codes { self.nav.held.remove(&Input::Button(*c)); }
                if self.guide_thumb.is_some() { self.guide_thumb = None; self.guide_thumb_cancel = true; }
            }
            if e.code==button_code(&self.legacy.fullscreen_button).unwrap() && e.value==0 && self.north_pending {
                self.north_pending=false;
                self.suppressed.remove(&e.code);
                if !self.north_fired { o.extend(self.capture_window(false)); }
                return o;
            }
            if e.value == 0 && self.suppressed.remove(&e.code) {
                return o;
            }
            if self.suppressed.contains(&e.code) {
                return o;
            }
            let guide = button_code(&self.global.controller.guide_button).unwrap();
            let guide_held = self.pressed.contains(&guide);
            let owned = self.app_owned();
            let half = self.half_capture();
            // The captured app owns Guide and every button (full and half capture alike). Only the
            // mode_override_chord (half-capture toggle), Start in half-capture and the held toggle_chord are host input.
            let app_guide = owned && !self.global.controller.captured_system_layer;
            if half && e.value == 1 && !already && e.code == button_code(&self.global.controller.cursor_mode_button).unwrap() {
                o.extend(self.release_outputs());
                self.suppressed.insert(e.code);
                self.pending_guide = false;
                self.cursor = false;
                o.actions.push("input:cursor off".into());
                return o;
            }
            if app_guide {
                let escape:Vec<_>=self.global.controller.mode_override_chord.iter().map(|v|button_code(v).unwrap()).collect();
                if e.value==1 && !already && escape.contains(&e.code) && escape.iter().all(|c|self.pressed.contains(c)) {
                    o.extend(self.release_outputs()); self.suppressed.extend(&escape); self.pending_guide=false;
                    self.cursor = !self.cursor;
                    o.actions.push(format!("input:cursor {}",if self.cursor {"on"} else {"off"}));
                    return o;
                }
                if e.code==guide && e.value==1 { self.pending_guide=true; return o; }
                if self.pending_guide && e.code!=guide && e.value==1 {
                    self.pending_guide=false; self.app_held.insert(guide); o.app.push(Event::new(EV_KEY,guide,1));
                }
                if e.code==guide && e.value==0 && self.pending_guide {
                    self.pending_guide=false;
                    o.app.extend([Event::new(EV_KEY,guide,1),Event::new(EV_KEY,guide,0)]); return o;
                }
            }
            if !app_guide && e.code == guide {
                if e.value == 1 {
                    o.extend(self.release_outputs());
                    self.suppressed
                        .extend(self.pressed.iter().filter(|c| **c != guide));
                    // INV-6/R-26: a Guide press cancels pending nav timers; alone, it may become the Guide tap.
                    o.extend(self.nav_flush());
                    self.guide_tap = (self.pressed.len() == 1).then_some(0.0);
                } else if e.value == 0 && self.guide_tap.take().is_some() && !self.repairing && self.nav_active() {
                    if let Some(Value::Press(a)) = self.nav_value(Input::Button(guide)) { Self::fire(&a, &mut o); }
                }
                return o;
            }
            if !app_guide && guide_held {
                if e.value == 1 && !already {
                    self.guide_tap = None;
                }
                if e.value == 1 && !already && toggle_codes.contains(&e.code) && self.system_action(e.code).is_some() {
                    if !self.guide_thumb_cancel {
                        if self.guide_thumb.is_some() { self.guide_thumb = None; self.guide_thumb_cancel = true; } else { self.guide_thumb = Some(e.code); }
                    }
                    self.suppressed.insert(e.code);
                    return o;
                }
                if e.value == 1 && !already {
                    let override_codes: Vec<_> = self
                        .global
                        .controller
                        .mode_override_chord
                        .iter()
                        .map(|n| button_code(n).unwrap())
                        .collect();
                    if override_codes.contains(&e.code) && override_codes.iter().all(|c| self.pressed.contains(c))
                        && self.latched.insert("mode-override".into())
                    {
                        o.extend(self.release_outputs());
                        self.cursor = !self.cursor;
                        if self.room() {
                            self.pad.increment(1);
                        } else {
                            o.actions.push(format!(
                                "input:cursor {}",
                                if self.cursor { "on" } else { "off" }
                            ));
                        }
                    } else if let Some(action) = self
                        .global
                        .controller
                        .system_layer
                        .iter()
                        .find(|(name, _)| button_code(name).ok() == Some(e.code))
                        .map(|(_, a)| a.clone())
                    {
                        o.actions.push(action);
                    }
                }
                if e.value != 0 {
                    self.suppressed.insert(e.code);
                }
                return o;
            }
            if e.value == 1 && !already {
                let toggle: Vec<_> = self
                    .legacy
                    .toggle_chord
                    .iter()
                    .map(|n| button_code(n).unwrap())
                    .collect();
                if !owned
                    && toggle.contains(&e.code)
                    && toggle.iter().all(|c| self.pressed.contains(c))
                {
                    let (min, max) = toggle
                        .iter()
                        .filter_map(|c| self.chord_times.get(c))
                        .fold((u64::MAX, 0), |(a, b), t| (a.min(*t), b.max(*t)));
                    if max.saturating_sub(min) <= self.global.controller.debounce_ms
                        && self.last_toggle.is_none_or(|t| {
                            now.saturating_sub(t) >= self.global.controller.debounce_ms
                        })
                        && self.latched.insert("3d-toggle".into())
                    {
                        self.last_toggle = Some(now);
                        o.extend(self.release_outputs());
                        self.suppressed.extend(&toggle);
                        o.actions.push("mios:hyprland-3d-toggle".into());
                        return o;
                    }
                }
                let bindings = self.global.bindings.clone();
                for b in &bindings {
                    if app_guide || !b.chord.starts_with("BTN_") || !b.active_in(&self.context) {
                        continue;
                    }
                    let codes: Vec<_> = b
                        .chord
                        .split('+')
                        .map(button_code)
                        .collect::<Result<_, _>>()
                        .unwrap_or_default();
                    if !codes.is_empty()
                        && codes.contains(&e.code)
                        && codes.iter().all(|c| self.pressed.contains(c))
                        && self.latched.insert(b.id.clone())
                    {
                        o.extend(self.release_outputs());
                        self.suppressed.extend(codes);
                        o.actions.push(b.action.clone());
                        return o;
                    }
                }
                // Host (legacy) actions -- ShaderGlass, cursor mode, North capture, room buttons -- belong to an
                // uncaptured controller only. A captured app keeps every one of these buttons, half-capture included.
                if !owned && self.legacy_action(e.code, now, &mut o) {
                    self.suppressed.insert(e.code);
                    return o;
                }
            }
            // Pointer click state is held independently of gamepad replay.
            if self.pointer_mode() {
                let mouse = if e.code == button_code(&self.legacy.grab_button).unwrap() {
                    Some(0x110)
                } else if e.code == button_code(&self.legacy.resize_button).unwrap() {
                    Some(0x111)
                } else {
                    None
                };
                if let Some(code) = mouse {
                    self.pointer_button(code, e.value != 0, &mut o);
                    return o;
                }
                if !half {
                    // C-11: cursor mode keeps every other button's nav meaning.
                    if self.nav_active() { self.nav_button(e, &mut o); }
                    return o;
                }
            }
            if self.room() {
                return o;
            }
            if self.nav_active() {
                self.nav_button(e, &mut o);
                return o;
            }
            if e.value == 0 {
                if self.app_held.remove(&e.code) {
                    o.app.push(e);
                }
            } else if e.value == 1 {
                self.app_held.insert(e.code);
                o.app.push(e);
            } else if self.app_held.contains(&e.code) {
                o.app.push(e);
            }
        } else if e.kind == EV_ABS {
            if let Some(a) = self.axes.get_mut(&e.code) {
                a.value = e.value;
            }
            let sink = self.axis_sink(e.code);
            #[cfg(test)]
            let sink = if self.mutant_room_forward && sink == Sink::Room { Sink::App } else { sink };
            if sink == Sink::App
                && (self.app_owned() || !self.button(&self.global.controller.guide_button))
            {
                o.app.push(e);
            }
            self.nav_axis(e.code, sink == Sink::Nav, &mut o);
        } else if e.kind == EV_SYN && e.code == SYN_REPORT {
            self.update_pad();
            if self.nav_active() && !self.button(&self.global.controller.guide_button) {
                self.nav_flicks(&mut o);
            }
            if self.forwards_frames() {
                o.app.push(e);
            }
        } else if self.forwards_frames() {
            o.app.push(e);
        }
        o
    }
    fn pointer_button(&mut self, code: u16, down: bool, o: &mut Output) {
        let changed = if down {
            self.pointer_held.insert(code)
        } else {
            self.pointer_held.remove(&code)
        };
        if changed {
            o.pointer.push(Event::new(EV_KEY, code, i32::from(down)));
            o.pointer.push(Event::new(EV_SYN, SYN_REPORT, 0));
        }
    }
    fn stick(&self, x: u16, y: u16, deadzone: f64, sensitivity: f64) -> (f64, f64) {
        let x = self.axes.get(&x).map_or(0.0, Axis::centered);
        let y = self.axes.get(&y).map_or(0.0, Axis::centered);
        radial(x, y, deadzone, sensitivity)
    }
    fn right_axes(&self) -> (u16, u16) {
        if self.axes.contains_key(&3) && self.axes.contains_key(&4) {
            (3, 4)
        } else {
            (
                axis_code(&self.legacy.fallback_axes[0]).unwrap(),
                axis_code(&self.legacy.fallback_axes[1]).unwrap(),
            )
        }
    }
    fn update_pad(&mut self) {
        if !self.room() || self.button(&self.global.controller.guide_button) {
            self.pad.neutralize();
            return;
        }
        let mut left = self.stick(
            0,
            1,
            self.legacy.deadzone,
            self.legacy.sensitivity * self.legacy.move_sensitivity,
        );
        if self.legacy.dpad_enabled {
            if let Some(a) = self.axes.get(&16).filter(|a| a.value != 0) {
                left.0 = f64::from(a.value.clamp(-1, 1));
            }
            if let Some(a) = self.axes.get(&17).filter(|a| a.value != 0) {
                left.1 = f64::from(a.value.clamp(-1, 1));
            }
        }
        let (rx, ry) = self.right_axes();
        let right = self.stick(
            rx,
            ry,
            self.legacy.deadzone,
            self.legacy.sensitivity * self.legacy.camera_sensitivity,
        );
        self.pad.lx = left.0 as f32;
        self.pad.ly = left.1 as f32;
        self.pad.rx = right.0 as f32;
        self.pad.ry = (right.1 * if self.legacy.invert_y { -1.0 } else { 1.0 }) as f32;
        self.pad.rt = self.trigger_down(
            &self.legacy.grab_button,
            &self.legacy.grab_axis,
            self.pad.rt,
        );
        self.pad.lt = self.trigger_down(
            &self.legacy.resize_button,
            &self.legacy.resize_axis,
            self.pad.lt,
        );
        self.pad.south = self.button(&self.legacy.jump_button);
        self.pad.west = self.button(&self.legacy.crouch_button);
    }
    fn trigger_down(&self, button: &str, axis: &str, was: bool) -> bool {
        if self.button(button) {
            return true;
        }
        // Z/RZ may be a right stick; never also interpret it as a trigger.
        let (rx, ry) = self.right_axes();
        let code = axis_code(axis).unwrap();
        if code == rx || code == ry {
            return false;
        }
        self.axes.get(&code).is_some_and(|a| {
            a.trigger()
                >= if was {
                    (self.legacy.trigger_threshold - 0.1).max(0.05)
                } else {
                    self.legacy.trigger_threshold
                }
        })
    }
    pub fn tick(&mut self, dt: f64) -> Output {
        let mut o = Output::default();
        let dt=if dt.is_finite() {dt.clamp(0.0,0.1)} else {0.0};
        if self.capture_override.is_some() {
            // C-03: a capture/release the compositor never confirmed must not keep the pad (or the room) forever.
            self.override_ms+=dt*1000.0;
            if self.override_ms>=self.global.controller.context_stale_ms as f64 {
                o.extend(self.ownership_failed());
            }
        }
        if self.north_pending && !self.north_fired {
            self.north_elapsed+=dt;
            if self.north_elapsed*1000.0>=self.global.controller.capture_hold_ms as f64 {
                self.north_fired=true;
                o.extend(self.capture_window(true));
            }
        }
        let held=self.legacy.toggle_chord.iter().all(|b|self.button(b));
        if self.app_owned() && held && !self.escape_fired {
            self.escape_elapsed+=dt;
            if self.escape_elapsed*1000.0>=self.global.controller.capture_hold_ms as f64 {
                self.escape_fired=true;
                o.extend(self.release_window());
            }
        } else if !held {
            self.escape_elapsed=0.0;
            self.escape_fired=false;
        }
        if let Some(t) = &mut self.guide_tap {
            *t += dt * 1000.0;
            if *t >= self.global.controller.capture_hold_ms as f64 { self.guide_tap = None; }
        }
        self.nav_timers(dt, &mut o);
        self.update_pad();
        if !self.pointer_mode() {
            if self.nav_active() && !self.button(&self.global.controller.guide_button) { self.nav_wheel(dt, &mut o); }
            return o;
        }
        let ControllerConfig {
            stick_deadzone,
            pointer_max_speed_px,
            pointer_hz,
            scroll_sensitivity,
            ..
        } = self.global.controller;
        let (rx, ry) = self.right_axes();
        let (x, y) = self.stick(rx, ry, stick_deadzone, 1.0);
        self.fraction_x += x * pointer_max_speed_px * f64::from(pointer_hz) * dt;
        self.fraction_y += y * pointer_max_speed_px * f64::from(pointer_hz) * dt;
        for (code, frac) in [(0, &mut self.fraction_x), (1, &mut self.fraction_y)] {
            let value = frac.trunc() as i32;
            *frac -= f64::from(value);
            if value != 0 {
                o.pointer.push(Event::new(EV_REL, code, value));
            }
        }
        let (hscroll, scroll) = self.stick(0, 1, stick_deadzone, 1.0);
        self.fraction_scroll -= scroll * scroll_sensitivity * dt;
        let wheel = self.fraction_scroll.trunc() as i32;
        self.fraction_scroll -= f64::from(wheel);
        if wheel != 0 {
            o.pointer.push(Event::new(EV_REL, 8, wheel));
        }
        // C-11: the left stick's X scrolls sideways (REL_HWHEEL) in cursor mode too.
        self.fraction_hwheel += hscroll * scroll_sensitivity * dt;
        let hwheel = self.fraction_hwheel.trunc() as i32;
        self.fraction_hwheel -= f64::from(hwheel);
        if hwheel != 0 {
            o.pointer.push(Event::new(EV_REL, 6, hwheel));
        }
        let rt = self.trigger_down(
            &self.legacy.grab_button,
            &self.legacy.grab_axis,
            self.pointer_held.contains(&0x110),
        );
        let lt = self.trigger_down(
            &self.legacy.resize_button,
            &self.legacy.resize_axis,
            self.pointer_held.contains(&0x111),
        );
        self.pointer_button(0x110, rt, &mut o);
        self.pointer_button(0x111, lt, &mut o);
        if !o.pointer.is_empty() {
            o.pointer.push(Event::new(EV_SYN, SYN_REPORT, 0));
        }
        o
    }
}
impl Router {
    fn system_action(&self, code: u16) -> Option<String> {
        self.global.controller.system_layer.iter().find(|(name, _)| button_code(name).ok() == Some(code)).map(|(_, a)| a.clone())
    }
    /// The nav profile that owns the controller now, or None (captured, the room, an unlisted context or "vpad").
    pub fn nav_profile(&self) -> Option<String> {
        if self.app_owned() || self.room() {
            return None;
        }
        self.global.controller.nav.profile_name(&self.context, self.tmux)
    }
    pub fn nav_active(&self) -> bool {
        self.nav.profile.is_some() && !self.app_owned() && !self.room()
    }
    fn nav_value(&self, input: Input) -> Option<Value> {
        let p = self.nav.profile.as_ref()?;
        self.global.controller.nav.profiles.get(p)?.get(&input).cloned()
    }
    fn fire(action: &str, o: &mut Output) {
        if action != "none" {
            o.actions.push(action.to_owned());
        }
    }
    /// INV-6: cancel every tap/hold timer and auto-repeat, and forget held nav inputs (their release fires nothing).
    /// Key output is atomic strokes, so no virtual key is ever left down.
    fn nav_flush(&mut self) -> Output {
        self.nav.held.clear();
        self.nav.stick_dir = [None; 2];
        self.nav.wheel = 0.0;
        self.nav.hwheel = 0.0;
        let (lx, ly) = self.stick(0, 1, self.global.controller.stick_deadzone, 1.0);
        let (rx, ry) = self.right_axes();
        let (rx, ry) = self.stick(rx, ry, self.global.controller.stick_deadzone, 1.0);
        let off = self.global.controller.nav.flick_off;
        self.nav.armed = [lx.hypot(ly) < off, rx.hypot(ry) < off];
        Output::default()
    }
    /// Follow the current profile; a change flushes nav state, and moving between vpad and nav releases the pad.
    fn nav_sync(&mut self) -> Output {
        let want = self.nav_profile();
        if want == self.nav.profile {
            return Output::default();
        }
        let crossing = want.is_some() != self.nav.profile.is_some();
        let mut o = self.nav_flush();
        if crossing && !self.app_owned() {
            let mut app: Vec<_> = self.app_held.iter().map(|c| Event::new(EV_KEY, *c, 0)).collect();
            self.app_held.clear();
            for (code, axis) in &self.axes {
                app.push(Event::new(EV_ABS, *code, axis.neutral(*code)));
            }
            if !app.is_empty() {
                app.push(Event::new(EV_SYN, SYN_REPORT, 0));
            }
            o.app.extend(app);
        }
        self.nav.profile = want;
        o
    }
    fn nav_press(&mut self, input: Input, o: &mut Output) {
        let thumb = matches!(input, Input::Button(0x13d | 0x13e));
        if thumb && (self.nav.thumbs_cancelled || self.thumb_stick_deflected(input)) {
            return;
        }
        let Some(value) = self.nav_value(input) else { return };
        let repeat = match &value {
            Value::Press(a) if !thumb => {
                Self::fire(a, o);
                self.global.controller.nav.repeat.get(&input).copied()
            }
            _ => None,
        };
        let next = repeat.map_or(0.0, |(d, _)| d as f64);
        self.nav.held.insert(input, Held { value, elapsed: 0.0, fired: false, next, repeat, deferred: thumb });
    }
    fn nav_release(&mut self, input: Input, o: &mut Output) {
        let Some(h) = self.nav.held.remove(&input) else { return };
        if self.repairing || (h.deferred && self.thumb_stick_deflected(input)) {
            return;
        }
        match &h.value {
            Value::TapHold(t, _) if !h.fired => Self::fire(t, o),
            Value::Press(a) if h.deferred => Self::fire(a, o),
            _ => {}
        }
    }
    /// R-23: an L3 or R3 click while its own stick is outside stick_deadzone is a sloppy click, never an action.
    fn thumb_stick_deflected(&self, input: Input) -> bool {
        let (x, y) = if input == Input::Button(0x13d) { (0, 1) } else { self.right_axes() };
        let (x, y) = self.stick(x, y, self.global.controller.stick_deadzone, 1.0);
        x != 0.0 || y != 0.0
    }
    fn nav_button(&mut self, e: Event, o: &mut Output) {
        if e.value == 1 && !self.nav.held.contains_key(&Input::Button(e.code)) {
            self.nav_press(Input::Button(e.code), o);
        } else if e.value == 0 {
            self.nav_release(Input::Button(e.code), o);
        }
    }
    /// The HAT d-pad folds into BTN_DPAD_* and analog-only triggers get digital edges, for nav only.
    fn nav_axis(&mut self, code: u16, nav: bool, o: &mut Output) {
        let guide = self.button(&self.global.controller.guide_button);
        if code == 16 || code == 17 {
            let v = self.axes.get(&code).map_or(0, |a| a.value.signum());
            let old = if code == 16 { self.nav.hat.0 } else { self.nav.hat.1 };
            if code == 16 { self.nav.hat.0 = v } else { self.nav.hat.1 = v }
            if !nav || guide || v == old {
                return;
            }
            let button = |v: i32| match (code, v) {
                (16, -1) => Some(BTN_DPAD_LEFT),
                (16, 1) => Some(BTN_DPAD_RIGHT),
                (17, -1) => Some(BTN_DPAD_UP),
                (17, 1) => Some(BTN_DPAD_DOWN),
                _ => None,
            };
            if let Some(b) = button(old) { self.nav_release(Input::Button(b), o); }
            if let Some(b) = button(v) { self.nav_press(Input::Button(b), o); }
            return;
        }
        let (rx, ry) = self.right_axes();
        let triggers = [(axis_code(&self.legacy.resize_axis).unwrap(), 0x138u16), (axis_code(&self.legacy.grab_axis).unwrap(), 0x139)];
        for (i, (a, button)) in triggers.into_iter().enumerate() {
            if a != code || a == rx || a == ry {
                continue;
            }
            let t = self.axes.get(&a).map_or(0.0, Axis::trigger);
            let on = if self.nav.triggers[i] { t >= (self.legacy.trigger_threshold - 0.1).max(0.05) } else { t >= self.legacy.trigger_threshold };
            if on == self.nav.triggers[i] {
                return;
            }
            self.nav.triggers[i] = on;
            if nav && self.analog_triggers && !guide {
                if on { self.nav_press(Input::Button(button), o) } else { self.nav_release(Input::Button(button), o) }
            }
            return;
        }
    }
    fn stick_axes(&self, right: bool) -> (u16, u16) {
        if right { self.right_axes() } else { (0, 1) }
    }
    fn is_pointer(&self, input: Input) -> bool {
        matches!(self.nav_value(input), Some(Value::Press(a)) if a.starts_with("pointer:"))
    }
    /// Discrete stick directions: one flick per deflection past flick_on; the stick re-arms below flick_off.
    fn nav_flicks(&mut self, o: &mut Output) {
        let (on, off) = (self.global.controller.nav.flick_on, self.global.controller.nav.flick_off);
        for (i, right) in [(0usize, false), (1, true)] {
            let (xa, ya) = self.stick_axes(right);
            let (x, y) = self.stick(xa, ya, self.global.controller.stick_deadzone, 1.0);
            let mag = x.hypot(y);
            if mag < off {
                if let Some(d) = self.nav.stick_dir[i].take() {
                    self.nav_release(Input::Stick { right, dir: d }, o);
                }
                self.nav.armed[i] = true;
                continue;
            }
            if !self.nav.armed[i] || mag < on {
                continue;
            }
            let dir = if x.abs() >= y.abs() { if x > 0.0 { Dir::Right } else { Dir::Left } } else if y > 0.0 { Dir::Down } else { Dir::Up };
            let input = Input::Stick { right, dir };
            if self.is_pointer(input) {
                continue;
            }
            self.nav.armed[i] = false;
            self.nav.stick_dir[i] = Some(dir);
            self.nav_press(input, o);
        }
    }
    /// Analog wheel for stick directions mapped to pointer: (the vertical and horizontal parts separately).
    fn nav_wheel(&mut self, dt: f64, o: &mut Output) {
        let sens = self.global.controller.scroll_sensitivity;
        for right in [false, true] {
            let (xa, ya) = self.stick_axes(right);
            let (x, y) = self.stick(xa, ya, self.global.controller.stick_deadzone, 1.0);
            for (amount, dir) in [(-y, if y < 0.0 { Dir::Up } else { Dir::Down }), (x, if x > 0.0 { Dir::Right } else { Dir::Left })] {
                if amount == 0.0 {
                    continue;
                }
                let Some(Value::Press(a)) = self.nav_value(Input::Stick { right, dir }) else { continue };
                let m = amount.abs() * sens * dt;
                match a.as_str() {
                    "pointer:wheel-up" => self.nav.wheel += m,
                    "pointer:wheel-down" => self.nav.wheel -= m,
                    "pointer:hwheel-right" => self.nav.hwheel += m,
                    "pointer:hwheel-left" => self.nav.hwheel -= m,
                    _ => {}
                }
            }
        }
        let mut any = false;
        for (code, frac) in [(8u16, &mut self.nav.wheel), (6, &mut self.nav.hwheel)] {
            let v = frac.trunc() as i32;
            *frac -= f64::from(v);
            if v != 0 {
                o.pointer.push(Event::new(EV_REL, code, v));
                any = true;
            }
        }
        if any {
            o.pointer.push(Event::new(EV_SYN, SYN_REPORT, 0));
        }
    }
    /// Tap/hold resolution at capture_hold_ms (a hold fires once) and auto-repeat for string values with a repeat entry.
    fn nav_timers(&mut self, dt: f64, o: &mut Output) {
        if self.nav.held.is_empty() {
            return;
        }
        let hold = self.global.controller.capture_hold_ms as f64;
        let ms = dt * 1000.0;
        let mut fire = vec![];
        for h in self.nav.held.values_mut() {
            h.elapsed += ms;
            match &h.value {
                Value::TapHold(_, hold_action) if !h.fired && h.elapsed + 1e-6 >= hold => {
                    h.fired = true;
                    fire.push(hold_action.clone());
                }
                Value::Press(a) if !h.deferred => {
                    if let Some((_, rate)) = h.repeat {
                        let mut n = 0;
                        while h.elapsed + 1e-6 >= h.next && n < 16 {
                            fire.push(a.clone());
                            h.next += rate as f64;
                            n += 1;
                        }
                    }
                }
                _ => {}
            }
        }
        for a in fire {
            Self::fire(&a, o);
        }
    }
}
pub fn radial(mut x: f64, mut y: f64, deadzone: f64, sensitivity: f64) -> (f64, f64) {
    if x.abs() < deadzone * 0.5 {
        x = 0.0;
    }
    if y.abs() < deadzone * 0.5 {
        y = 0.0;
    }
    let mag = x.hypot(y);
    if !mag.is_finite() || mag <= deadzone || deadzone >= 1.0 {
        return (0.0, 0.0);
    }
    let scale = ((mag.min(1.0) - deadzone) / (1.0 - deadzone)) * sensitivity / mag;
    ((x * scale).clamp(-1.0, 1.0), (y * scale).clamp(-1.0, 1.0))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn router() -> Router {
        let (c, l) = crate::config::tests::fixture();
        let mut axes = BTreeMap::new();
        for c in [0, 1, 3, 4] {
            axes.insert(
                c,
                Axis {
                    min: -32768,
                    max: 32767,
                    value: 0,
                },
            );
        }
        Router::new(c, l, axes)
    }
    fn key(r: &mut Router, c: u16, v: i32, t: u64) -> Output {
        r.event(Event::new(EV_KEY, c, v), t)
    }
    #[test]
    fn kernel_truth_repairs_releases_without_unlatching_held_chords() {
        let mut r=router();
        key(&mut r,0x130,1,1);
        r.latched.insert("held-test".into());
        let held=BTreeSet::from([0x130]);
        let out=r.reconcile(&held,2);
        assert!(out.actions.is_empty() && out.app.is_empty() && out.pointer.is_empty());
        assert!(r.latched.contains("held-test"));
        let out=r.reconcile(&BTreeSet::new(),3);
        assert!(r.pressed.is_empty() && r.latched.is_empty() && r.chord_times.is_empty());
        assert!(out.actions.is_empty());
        assert!(out.app.contains(&Event::new(EV_KEY,0x130,0)));
        let next=key(&mut r,0x130,1,4);
        assert!(next.app.contains(&Event::new(EV_KEY,0x130,1)));
    }
    #[test]
    fn kernel_truth_never_creates_a_guide_tap_or_a_new_press() {
        let mut r=router();r.global.controller.captured_system_layer=false;r.set_context("app-capture");
        key(&mut r,0x13c,1,1);assert!(r.pending_guide);
        let out=r.reconcile(&BTreeSet::from([0x130]),2);
        assert!(out.app.is_empty() && out.pointer.is_empty() && out.actions.is_empty());
        assert!(!r.pending_guide && r.pressed.is_empty());
        assert!(!r.app_held.contains(&0x130));
        r.suppressed.insert(0x131);r.blocked.insert(0x131);
        r.reconcile(&BTreeSet::new(),3);
        assert!(r.suppressed.is_empty() && r.blocked.is_empty());
    }

    #[test]
    fn captured_application_owns_guide_combinations_by_default() {
        let mut r=router();r.global.controller.captured_system_layer=false;r.set_context("app-capture");
        let mut out=Output::default();
        for (c,v,t) in [(0x13c,1,1),(0x136,1,2),(0x136,0,3),(0x13c,0,4)] {out.extend(key(&mut r,c,v,t));}
        assert!(out.actions.is_empty(),"captured app must never switch workspace or fire system actions");
        assert_eq!(out.app,vec![Event::new(EV_KEY,0x13c,1),Event::new(EV_KEY,0x136,1),Event::new(EV_KEY,0x136,0),Event::new(EV_KEY,0x13c,0)]);
    }
    #[test]
    fn explicit_capture_escape_returns_the_host_cursor_without_leaking_keys() {
        let mut r=router();r.global.controller.captured_system_layer=false;r.set_context("app-capture");
        assert!(key(&mut r,0x13c,1,1).app.is_empty());
        let out=key(&mut r,0x13a,1,2);assert_eq!(out.actions,vec!["input:cursor on"]);assert!(!out.app.iter().any(|e|e.kind==EV_KEY));assert!(r.cursor);
    }
    #[test]
    fn host_cursor_and_drag_survive_terminal_to_desktop_focus_changes() {
        let mut r=router();r.set_context("terminal");key(&mut r,0x13b,1,1);key(&mut r,0x13b,0,2);
        key(&mut r,0x138,1,3);assert!(r.cursor);
        let out=r.set_context("desktop-2d");assert!(r.cursor);assert!(out.pointer.is_empty(),"focus change must not release a drag");
    }
    #[test]
    fn captured_window_identity_change_releases_previous_target() {
        let mut r=router();r.set_focus("app-capture",Some(("0xaaa".into(),1)),false);
        key(&mut r,0x130,1,1);
        let out=r.set_focus("app-capture",Some(("0xbbb".into(),2)),false);
        assert!(out.app.contains(&Event::new(EV_KEY,0x130,0)));
        assert!(key(&mut r,0x130,2,2).app.is_empty(),"a held key must not leak into the next window");
    }
    #[test]
    fn selected_room_window_preserves_the_documented_thumbstick_return_chord() {
        let mut r=router();r.global.controller.captured_system_layer=false;
        r.set_focus("app-capture",Some(("0xabc".into(),42)),true);
        key(&mut r,0x13d,1,1);let out=key(&mut r,0x13e,1,2);
        assert!(out.actions.is_empty(),"a short chord belongs to the captured application");
        for _ in 0..4 {assert!(r.tick(0.1).actions.is_empty());}
        assert_eq!(r.tick(0.1).actions,vec!["input:window off"]);
        assert!(r.room(),"release returns ownership without closing the room");
        // A captured window of any kind keeps both thumbstick buttons for a short press; only the hold releases.
        let mut game=router();game.set_focus("app-capture",Some(("0xgame".into(),7)),false);
        key(&mut game,0x13d,1,1);assert!(key(&mut game,0x13e,1,2).actions.is_empty(),"a captured window keeps both thumbstick buttons for a short press");
        let mut fired=vec![];for _ in 0..8 {fired.extend(game.tick(0.1).actions);}
        assert_eq!(fired,vec!["input:window off"],"a held L3+R3 releases a captured game exactly once");
    }
    #[test]
    fn north_tap_hands_all_controls_to_a_window_without_fullscreen_or_room_motion() {
        let mut r=router();r.global.controller.captured_system_layer=false;
        r.set_focus("desktop-3d",Some(("0xabc".into(),42)),false);
        let north=button_code(&r.legacy.fullscreen_button).unwrap();
        assert!(key(&mut r,north,1,1).actions.is_empty());
        assert!(r.tick(0.1).actions.is_empty());
        assert_eq!(key(&mut r,north,0,2).actions,vec!["input:window on"]);
        // A stale compositor snapshot must not reactivate host controls while the transaction runs.
        r.set_focus("desktop-3d",Some(("0xabc".into(),42)),false);
        let seq=r.pad.sequences;
        for code in [0x130,0x131,0x133,0x134,0x136,0x137,0x138,0x139,0x13a,0x13b,0x13d,0x13e] {
            let down=key(&mut r,code,1,3);assert!(down.actions.is_empty(),"host action for {code}");
            assert!(down.app.contains(&Event::new(EV_KEY,code,1)),"missing app input {code}");
            key(&mut r,code,0,4);
        }
        for code in [0,1,2,3,4,5,16,17] {
            let event=Event::new(EV_ABS,code,123);
            assert!(r.event(event,5).app.contains(&event));
        }
        assert!(r.tick(0.01).actions.is_empty());
        assert_eq!((r.pad.lx,r.pad.ly,r.pad.rx,r.pad.ry),(0.0,0.0,0.0,0.0));
        assert_eq!(r.pad.sequences,seq,"no compositor action sequence may leak");
        r.set_focus("app-capture",Some(("0xabc".into(),42)),true);
        assert!(r.app_owned());
    }
    #[test]
    fn north_hold_enters_fullscreen_once_and_a_repaired_release_never_captures() {
        let mut r=router();r.set_context("desktop-3d");
        let north=button_code(&r.legacy.fullscreen_button).unwrap();
        key(&mut r,north,1,1);
        for _ in 0..4 {assert!(r.tick(0.1).actions.is_empty());}
        assert_eq!(r.tick(0.1).actions,vec!["input:window fullscreen"]);
        assert!(r.tick(0.1).actions.is_empty());
        assert!(key(&mut r,north,0,2).actions.is_empty());
        let mut missed=router();missed.set_context("desktop-3d");key(&mut missed,north,1,1);
        assert!(missed.reconcile(&BTreeSet::new(),2).actions.is_empty());
        for _ in 0..8 {assert!(missed.tick(0.1).actions.is_empty());}
    }
    #[test]
    fn captured_guide_does_not_swallow_application_sticks() {
        let mut r=router();r.global.controller.captured_system_layer=false;r.set_context("app-capture");
        key(&mut r,0x13c,1,1);
        let axis=Event::new(EV_ABS,0,32767);
        assert!(r.event(axis,2).app.contains(&axis));
        assert_eq!(r.pad.lx,0.0);
    }
    #[test]
    fn ordinary_input_reaches_game() {
        let mut r = router();
        r.set_context("game");
        let o = key(&mut r, 0x130, 1, 1);
        assert_eq!(o.app, vec![Event::new(EV_KEY, 0x130, 1)]);
        assert!(o.actions.is_empty());
        assert_eq!(key(&mut r, 0x130, 0, 2).app[0].value, 0);
    }
    #[test]
    fn guide_layer_consumes_and_routes_in_vm() {
        let mut r = router();
        r.set_context("vm-passthrough");
        key(&mut r, 0x13c, 1, 1);
        let o = key(&mut r, 0x136, 1, 2);
        assert_eq!(o.actions, vec!["hypr:workspace e-1"]);
        assert!(o.app.is_empty());
        assert!(key(&mut r, 0x136, 0, 3).app.is_empty());
        assert!(key(&mut r, 0x13c, 0, 4).app.is_empty());
    }
    #[test]
    fn guide_releases_previous_app_buttons() {
        let mut r = router();
        r.set_context("game");
        key(&mut r, 0x130, 1, 1);
        let o = key(&mut r, 0x13c, 1, 2);
        assert!(o.app.contains(&Event::new(EV_KEY, 0x130, 0)));
        assert!(key(&mut r, 0x130, 0, 3).app.is_empty());
    }
    #[test]
    fn system_shoulder_keeps_legacy_shader_cycle() {
        let mut r = router();
        assert_eq!(
            key(&mut r, 0x136, 1, 1).actions,
            vec!["mios:shaderglass cycle"]
        );
        assert!(key(&mut r, 0x136, 2, 2).actions.is_empty());
    }
    #[test]
    fn captured_shoulder_passes_without_guide() {
        let mut r = router();
        r.set_context("app-capture");
        let o = key(&mut r, 0x136, 1, 1);
        assert!(o.actions.is_empty() && o.app.contains(&Event::new(EV_KEY, 0x136, 1)));
        // An uncaptured game is an ordinary window: LB is the host ShaderGlass cycle until North captures it.
        let mut g = router();
        g.set_context("game");
        assert_eq!(key(&mut g, 0x136, 1, 1).actions, vec!["mios:shaderglass cycle"]);
    }
    #[test]
    fn start_toggles_2d_pointer_never_session() {
        let mut r = router();
        let o = key(&mut r, 0x13b, 1, 1);
        assert!(r.cursor);
        assert_eq!(o.actions, vec!["input:cursor on"]);
        key(&mut r, 0x13b, 0, 2);
        key(&mut r, 0x13b, 1, 3);
        assert!(!r.cursor);
    }
    #[test]
    fn controller_pointer_click_and_motion_work_in_2d() {
        let mut r = router();
        key(&mut r, 0x13b, 1, 1);
        key(&mut r, 0x13b, 0, 2);
        r.event(Event::new(EV_ABS, 3, 32767), 3);
        let o = r.tick(0.01);
        assert!(o
            .pointer
            .iter()
            .any(|e| e.kind == EV_REL && e.code == 0 && e.value > 0));
        assert!(o.app.is_empty());
        let click = key(&mut r, 0x139, 1, 4);
        assert!(click.pointer.contains(&Event::new(EV_KEY, 0x110, 1)));
        let release = key(&mut r, 0x139, 0, 5);
        assert!(release.pointer.contains(&Event::new(EV_KEY, 0x110, 0)));
    }
    #[test]
    fn changing_context_releases_clicks() {
        let mut r = router();
        key(&mut r, 0x13b, 1, 1);
        key(&mut r, 0x139, 1, 2);
        let o = r.set_context("app-capture");
        assert!(o.pointer.contains(&Event::new(EV_KEY, 0x110, 0)));
        assert!(!r.cursor);
    }
    #[test]
    fn held_keys_do_not_cross_contexts() {
        let mut r = router();
        r.set_context("app-capture");
        key(&mut r, 0x130, 1, 1);
        let o = r.set_context("terminal");
        assert!(o.app.contains(&Event::new(EV_KEY, 0x130, 0)));
        assert!(key(&mut r, 0x130, 2, 2).app.is_empty());
        assert!(key(&mut r, 0x130, 0, 3).app.is_empty());
    }
    #[test]
    fn l3_r3_is_once_per_press() {
        let mut r = router();
        key(&mut r, 0x13d, 1, 10);
        let o = key(&mut r, 0x13e, 1, 20);
        assert_eq!(o.actions, vec!["mios:hyprland-3d-toggle"]);
        assert!(key(&mut r, 0x13e, 2, 21).actions.is_empty());
    }
    #[test]
    fn separate_controllers_cannot_form_a_chord() {
        let mut a = router();
        let mut b = router();
        key(&mut a, 0x13d, 1, 10);
        assert!(key(&mut b, 0x13e, 1, 20).actions.is_empty());
    }
    #[test]
    fn select_and_start_have_distinct_3d_sequences() {
        let mut r = router();
        r.set_context("desktop-3d");
        key(&mut r, 0x13a, 1, 1);
        key(&mut r, 0x13b, 1, 2);
        assert_eq!(&r.pad.sequences[..2], &[1, 1]);
    }
    #[test]
    fn double_jump_maps_to_flight() {
        let mut r = router();
        r.set_context("desktop-3d");
        key(&mut r, 0x130, 1, 100);
        key(&mut r, 0x130, 0, 120);
        key(&mut r, 0x130, 1, 200);
        assert_eq!(r.pad.sequences[2], 1);
        assert_eq!(r.pad.sequences[3], 1);
    }
    #[test]
    fn no_trigger_from_right_stick_fallback() {
        let mut r = router();
        r.axes.remove(&3);
        r.axes.remove(&4);
        r.axes.insert(
            2,
            Axis {
                min: -32768,
                max: 32767,
                value: 32767,
            },
        );
        r.axes.insert(
            5,
            Axis {
                min: -32768,
                max: 32767,
                value: 32767,
            },
        );
        r.set_context("desktop-3d");
        r.axes.get_mut(&5).unwrap().value = 32767;
        r.tick(0.01);
        assert!(!r.pad.rt);
    }
    #[test]
    fn dropped_frame_cannot_fire_actions() {
        let mut r = router();
        r.event(Event::new(EV_SYN, SYN_DROPPED, 0), 1);
        assert!(key(&mut r, 0x136, 1, 2).actions.is_empty());
        r.resync(BTreeSet::from([0x136]), BTreeMap::new());
        assert!(key(&mut r, 0x136, 2, 3).actions.is_empty());
        key(&mut r, 0x136, 0, 4);
        assert_eq!(key(&mut r, 0x136, 1, 5).actions.len(), 1);
    }
    #[test]
    fn packet_is_the_native_68_byte_abi() {
        let mut p = PadState {
            lx: 0.5,
            rt: true,
            ..PadState::default()
        };
        p.sequences[7] = 23;
        let b = p.packet(123);
        assert_eq!(b.len(), 68);
        assert_eq!(u32::from_le_bytes(b[..4].try_into().unwrap()), 0x4d493344);
        assert_eq!(b[32], 1);
        assert_eq!(u32::from_le_bytes(b[64..68].try_into().unwrap()), 23);
        let mut next = PadState::default();
        next.seed_sequences(&b);
        assert_eq!(next.sequences[7], 23);
    }
    #[test]
    fn invalid_packet_never_seeds_replayed_actions() {
        let mut p = PadState::default();
        p.seed_sequences(&[255; 68]);
        assert_eq!(p.sequences, [0; 8]);
    }
    #[test]
    fn deadzone_and_unsigned_axis_ranges() {
        assert_eq!(radial(0.05, 0.02, 0.15, 1.0), (0.0, 0.0));
        assert_eq!(radial(1.0, 1.0, 1.0, 1.0), (0.0, 0.0));
        assert!(
            Axis {
                min: 0,
                max: 255,
                value: 255
            }
            .centered()
                > 0.99
        );
        assert_eq!(
            Axis {
                min: 0,
                max: 255,
                value: 0
            }
            .trigger(),
            0.0
        );
    }
}

#[cfg(test)]
mod ownership_contract {
    //! I1: capture/release are the same for every window kind; half-capture keeps every button with the app;
    //! one axis sink per frame; pending ownership expires or reverts.
    use super::*;
    const SOUTH: u16 = 0x130; const EAST: u16 = 0x131; const NORTH: u16 = 0x133; const LB: u16 = 0x136;
    const RB: u16 = 0x137; const RT: u16 = 0x139; const SELECT: u16 = 0x13a; const START: u16 = 0x13b;
    const GUIDE: u16 = 0x13c; const L3: u16 = 0x13d; const R3: u16 = 0x13e;
    fn router() -> Router {
        let (c, l) = crate::config::tests::fixture();
        let mut axes = BTreeMap::new();
        for c in [0, 1, 3, 4] { axes.insert(c, Axis { min: -32768, max: 32767, value: 0 }); }
        for c in [2, 5] { axes.insert(c, Axis { min: 0, max: 255, value: 0 }); }
        for c in [16, 17] { axes.insert(c, Axis { min: -1, max: 1, value: 0 }); }
        Router::new(c, l, axes)
    }
    fn key(r: &mut Router, c: u16, v: i32, t: u64) -> Output { r.event(Event::new(EV_KEY, c, v), t) }
    fn tap(r: &mut Router, c: u16, t: u64) -> Output { let mut o = key(r, c, 1, t); o.extend(key(r, c, 0, t + 1)); o }
    fn hold(r: &mut Router, ms: u64) -> Output { let mut o = Output::default(); for _ in 0..ms / 50 { o.extend(r.tick(0.05)); } o }
    fn app_keys(o: &Output) -> Vec<(u16, i32)> { o.app.iter().filter(|e| e.kind == EV_KEY).map(|e| (e.code, e.value)).collect() }
    // Every window kind the operator named: native, Flatpak, game, VM, remote, nested, PiP, Theatre, terminal.
    const KINDS: [(&str, &str); 9] = [("org.gnome.Nautilus", "desktop-2d"), ("org.any.Flatpak", "desktop-2d"), ("steam_app_570", "game"),
        ("looking-glass-client", "vm-passthrough"), ("xfreerdp", "vm-passthrough"), ("mios-nested-desktop", "desktop-2d"),
        ("mios-pip-player", "desktop-2d"), ("mios-theatre", "desktop-2d"), ("Alacritty", "terminal")];

    #[test]
    fn capture_and_release_are_identical_for_every_window_kind() {
        let (mut cfg, _) = crate::config::tests::fixture();
        cfg.contexts.get_mut("vm-passthrough").unwrap().push("xfreerdp".into());
        let mut transcripts = vec![];
        for (class, expected) in KINDS {
            let context = cfg.classify_window(Some(class), false, false);
            let mut r = router();
            r.set_focus(&context, Some((format!("0x{class}"), 9)), false);
            let mut t = vec![context.clone(), format!("owned={}", r.app_owned())];
            // North tap captures; RB, Guide and Start then belong to the app; a held L3+R3 releases.
            t.extend(tap(&mut r, NORTH, 10).actions);
            r.set_focus(&cfg.classify_window(Some(class), false, true), Some((format!("0x{class}"), 9)), false);
            let o = tap(&mut r, RB, 20); t.push(format!("rb-actions={:?} rb-app={:?}", o.actions, app_keys(&o)));
            key(&mut r, L3, 1, 30); key(&mut r, R3, 1, 31);
            t.extend(hold(&mut r, 700).actions);
            key(&mut r, L3, 0, 40); key(&mut r, R3, 0, 41);
            r.set_focus(&cfg.classify_window(Some(class), false, false), Some((format!("0x{class}"), 9)), false);
            t.push(format!("owned-after={}", r.app_owned()));
            assert_eq!(context, expected, "{class}");
            transcripts.push((class, t[1..].to_vec()));
        }
        let first = transcripts[0].1.clone();
        assert_eq!(first, vec!["owned=false".to_string(), "input:window on".into(),
            "rb-actions=[] rb-app=[(311, 1), (311, 0)]".into(), "input:window off".into(), "owned-after=false".into()]);
        for (class, t) in &transcripts { assert_eq!(t, &first, "{class} must capture and release like every other window"); }
    }
    #[test]
    fn half_capture_keeps_every_button_with_the_app() {
        // tests.md C-01: the live bug was RB firing ShaderGlass after Guide+Select in a captured app.
        let mut r = router(); r.set_context("app-capture");
        let mut o = Output::default();
        for (c, v, t) in [(GUIDE, 1, 1), (SELECT, 1, 2), (SELECT, 0, 3), (GUIDE, 0, 4), (RB, 1, 5), (RB, 0, 6)] { o.extend(key(&mut r, c, v, t)); }
        assert_eq!(o.actions, vec!["input:cursor on"]);
        assert!(r.half_capture());
        assert_eq!(app_keys(&o), vec![(RB, 1), (RB, 0)], "RB reaches the app, never ShaderGlass");
        // Guide+East after the escape: the app gets Guide then East; no hypr:killactive.
        let mut o = Output::default();
        for (c, v, t) in [(GUIDE, 1, 7), (EAST, 1, 8), (EAST, 0, 9), (GUIDE, 0, 10)] { o.extend(key(&mut r, c, v, t)); }
        assert!(o.actions.is_empty(), "{:?}", o.actions);
        assert_eq!(app_keys(&o), vec![(GUIDE, 1), (EAST, 1), (EAST, 0), (GUIDE, 0)]);
        // North after the escape belongs to the app: no capture transaction.
        let o = tap(&mut r, NORTH, 11);
        assert!(o.actions.is_empty() && app_keys(&o) == vec![(NORTH, 1), (NORTH, 0)]);
        assert!(hold(&mut r, 600).actions.is_empty());
        // Sticks and RT are host pointer input; the app never sees them in half-capture.
        let stick = r.event(Event::new(EV_ABS, 3, 32767), 12);
        assert!(stick.app.is_empty());
        let moved = r.tick(0.02);
        assert!(moved.pointer.iter().any(|e| e.kind == EV_REL && e.code == 0 && e.value > 0));
        let click = key(&mut r, RT, 1, 13);
        assert!(click.pointer.contains(&Event::new(EV_KEY, 0x110, 1)) && click.app.is_empty());
        key(&mut r, RT, 0, 14);
        // The HAT d-pad is a button set: it stays with the app.
        let hat = Event::new(EV_ABS, 16, 1);
        assert!(r.event(hat, 15).app.contains(&hat));
        // Start returns to full capture.
        let o = key(&mut r, START, 1, 16);
        assert_eq!(o.actions, vec!["input:cursor off"]);
        assert!(!r.half_capture() && r.app_owned());
        key(&mut r, START, 0, 17);
        assert_eq!(app_keys(&tap(&mut r, START, 18)), vec![(START, 1), (START, 0)], "Start in full capture is the app's");
        // Guide+Select twice: on, then off again.
        let mut o = Output::default();
        for (c, v, t) in [(GUIDE, 1, 20), (SELECT, 1, 21), (SELECT, 0, 22), (GUIDE, 0, 23), (GUIDE, 1, 24), (SELECT, 1, 25), (SELECT, 0, 26), (GUIDE, 0, 27)] { o.extend(key(&mut r, c, v, t)); }
        assert_eq!(o.actions, vec!["input:cursor on", "input:cursor off"]);
        assert!(app_keys(&o).is_empty(), "the escape chord is never replayed to the app");
    }
    #[test]
    fn uncaptured_contexts_keep_host_shaderglass_and_cursor() {
        // Negative control for C-01: the same sequence uncaptured still reaches the host.
        for context in ["desktop-2d", "game", "vm-passthrough", "terminal"] {
            let mut r = router(); r.set_context(context);
            let mut o = Output::default();
            for (c, v, t) in [(GUIDE, 1, 1), (SELECT, 1, 2), (SELECT, 0, 3), (GUIDE, 0, 4)] { o.extend(key(&mut r, c, v, t)); }
            assert_eq!(o.actions, vec!["input:cursor on"], "{context}");
            assert_eq!(tap(&mut r, RB, 5).actions, vec!["mios:shaderglass toggle"], "{context}");
            assert_eq!(tap(&mut r, NORTH, 6).actions, vec!["input:window on"], "North captures from cursor mode too ({context})");
        }
        let mut r = router(); r.set_context("desktop-2d");
        assert_eq!(tap(&mut r, NORTH, 1).actions, vec!["input:window on"]);
    }
    #[test]
    fn captured_system_layer_fixture_choice_matters() {
        // C-27 negative control: with captured_system_layer=true the Guide layer is host input in a capture.
        let mut r = router(); r.global.controller.captured_system_layer = true; r.set_context("app-capture");
        let mut o = Output::default();
        for (c, v, t) in [(GUIDE, 1, 1), (LB, 1, 2), (LB, 0, 3), (GUIDE, 0, 4)] { o.extend(key(&mut r, c, v, t)); }
        assert_eq!(o.actions, vec!["hypr:workspace e-1"]);
        let mut r = router(); r.set_context("app-capture");
        let mut o = Output::default();
        for (c, v, t) in [(GUIDE, 1, 1), (LB, 1, 2), (LB, 0, 3), (GUIDE, 0, 4)] { o.extend(key(&mut r, c, v, t)); }
        assert!(o.actions.is_empty());
    }
    #[test]
    fn ownership_is_per_session_not_per_pad() {
        let mut a = router(); let mut b = router();
        for r in [&mut a, &mut b] { r.set_focus("desktop-3d", Some(("0xroom".into(), 3)), false); }
        assert_eq!(tap(&mut a, NORTH, 1).actions, vec!["input:window on"]);
        b.event(Event::new(EV_ABS, 0, 32767), 2); b.tick(0.01);
        assert!(b.pad.lx != 0.0, "precondition: the second pad drives the room before it adopts the capture");
        let o = b.adopt_ownership(true);
        assert!(o.app.iter().any(|e| e.kind == EV_ABS), "adoption flushes the second pad's axes");
        assert!(b.app_owned() && b.pad.lx == 0.0);
        let stick = b.event(Event::new(EV_ABS, 0, 32767), 3); b.tick(0.01);
        assert!(stick.app.contains(&Event::new(EV_ABS, 0, 32767)) && b.pad.lx == 0.0, "the second pad now feeds only the app");
        assert!(b.adopt_ownership(true).app.is_empty(), "adopting the same ownership twice is a no-op");
    }
    #[test]
    fn unconfirmed_capture_reverts_after_context_stale_ms() {
        // tests.md C-03 positive: a snapshot without the tag keeps the override pending; one with it commits.
        let mut r = router(); r.set_focus("desktop-2d", Some(("0xa".into(), 1)), false);
        tap(&mut r, NORTH, 1);
        r.set_focus("desktop-2d", Some(("0xa".into(), 1)), false);
        r.tick(0.5);
        assert!(r.app_owned(), "pending inside context_stale_ms");
        r.set_focus("app-capture", Some(("0xa".into(), 1)), false);
        assert_eq!(r.ownership(), None, "the snapshot confirmed the capture");
        r.tick(0.1); r.tick(0.1); for _ in 0..20 { r.tick(0.1); }
        assert!(r.app_owned(), "a confirmed capture never expires");
        // Negative: never confirmed -> the app does not keep the pad.
        let mut r = router(); r.set_focus("desktop-2d", Some(("0xa".into(), 1)), false);
        tap(&mut r, NORTH, 1);
        for _ in 0..9 { r.set_focus("desktop-2d", Some(("0xa".into(), 1)), false); r.tick(0.1); }
        assert!(r.app_owned());
        r.tick(0.1); r.tick(0.05);
        assert!(!r.app_owned() && r.ownership().is_none(), "unconfirmed capture reverted after context_stale_ms");
        // Negative: a failed ownership transaction reverts at once and releases what the app held.
        let mut r = router(); r.set_focus("desktop-2d", Some(("0xa".into(), 1)), false);
        tap(&mut r, NORTH, 1); key(&mut r, SOUTH, 1, 2);
        let o = r.ownership_failed();
        assert!(!r.app_owned() && o.app.contains(&Event::new(EV_KEY, SOUTH, 0)));
    }
    #[test]
    fn confirmation_does_not_flush_a_held_stick() {
        let mut r = router(); r.set_focus("desktop-2d", Some(("0xa".into(), 1)), false);
        tap(&mut r, NORTH, 1);
        r.event(Event::new(EV_ABS, 0, 30000), 2);
        let o = r.set_focus("app-capture", Some(("0xa".into(), 1)), false);
        assert!(o.app.is_empty(), "the compositor confirming our own capture must not zero the game's stick");
    }

    struct Lcg(u64);
    impl Lcg { fn next(&mut self) -> u64 { self.0 = self.0.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407); self.0 >> 33 } }
    /// INV-5 / C-04: random streams in every context. A frame never has a non-neutral room packet together with a
    /// non-neutral stick event forwarded to the app or host pointer motion.
    fn axis_owner_property(mutant: bool) -> Result<(), String> {
        let contexts = ["desktop-2d", "desktop-3d", "terminal", "game", "vm-passthrough", "app-capture"];
        let buttons = [SOUTH, EAST, NORTH, 0x134, LB, RB, 0x138, RT, SELECT, START, GUIDE, L3, R3];
        for seed in 0..48u64 {
            let mut g = Lcg(seed * 7919 + 13);
            let mut r = router(); r.mutant_room_forward = mutant;
            let mut held = BTreeSet::new();
            let mut now = 1000;
            for step in 0..400 {
                now += 1 + g.next() % 40;
                let mut o = Output::default();
                match g.next() % 10 {
                    0 => { let c = contexts[(g.next() % contexts.len() as u64) as usize]; let t = format!("0x{}", g.next() % 3);
                           o.extend(r.set_focus(c, Some((t, 1)), c == "desktop-3d")); }
                    1..=3 => { let b = buttons[(g.next() % buttons.len() as u64) as usize];
                               let v = i32::from(!held.contains(&b)); if v == 1 { held.insert(b); } else { held.remove(&b); }
                               o.extend(r.event(Event::new(EV_KEY, b, v), now)); }
                    4..=7 => { let code = [0u16, 1, 3, 4][(g.next() % 4) as usize]; let v = (g.next() % 65536) as i32 - 32768;
                               o.extend(r.event(Event::new(EV_ABS, code, v), now)); o.extend(r.event(Event::new(EV_SYN, SYN_REPORT, 0), now)); }
                    8 => { if g.next().is_multiple_of(2) { o.extend(r.adopt_ownership(g.next().is_multiple_of(2))); } else { o.extend(r.ownership_failed()); } }
                    _ => o.extend(r.tick(0.02)),
                }
                o.extend(r.tick(0.005));
                let pad_live = r.pad.lx != 0.0 || r.pad.ly != 0.0 || r.pad.rx != 0.0 || r.pad.ry != 0.0;
                let app_live = o.app.iter().any(|e| e.kind == EV_ABS && [0, 1, 3, 4].contains(&e.code) && e.value.unsigned_abs() > 4000);
                let pointer_live = o.pointer.iter().any(|e| e.kind == EV_REL);
                if pad_live && (app_live || pointer_live) {
                    return Err(format!("seed {seed} step {step} context {} owned {} cursor {}: room and {} share a frame",
                        r.context, r.app_owned(), r.cursor, if app_live { "app" } else { "pointer" }));
                }
                if app_live && pointer_live {
                    return Err(format!("seed {seed} step {step}: app and pointer share a stick frame"));
                }
            }
        }
        Ok(())
    }
    #[test]
    fn one_axis_owner_per_frame_property() {
        axis_owner_property(false).unwrap();
    }
    #[test]
    fn mutant_forwarding_in_the_room_fails_the_property() {
        assert!(axis_owner_property(true).is_err(), "the property must catch a router that forwards room axes to the app");
    }
}

#[cfg(test)]
mod held_parity {
    use super::*;
    #[test]
    fn consumed_jump_preserves_hold_without_replaying_to_app() {
        let (g, l) = crate::config::tests::fixture();
        let mut r = Router::new(g, l, BTreeMap::new());
        r.set_context("desktop-3d");
        let o = r.event(Event::new(EV_KEY, 0x130, 1), 1000);
        assert!(o.app.is_empty());
        r.tick(0.01);
        assert!(r.pad.south);
        r.event(Event::new(EV_KEY, 0x130, 0), 1010);
        r.tick(0.01);
        assert!(!r.pad.south);
    }
}

#[cfg(test)]
mod nav_contract {
    //! I2: the navigation grammar for every uncaptured window (tests.md C-07..C-13, INV-6, R-23, R-26).
    use super::*;
    const SOUTH: u16 = 0x130; const EAST: u16 = 0x131; const NORTH: u16 = 0x133; const LB: u16 = 0x136;
    const TL2: u16 = 0x138; const TR2: u16 = 0x139; const SELECT: u16 = 0x13a; const START: u16 = 0x13b;
    const GUIDE: u16 = 0x13c; const L3: u16 = 0x13d; const R3: u16 = 0x13e;
    fn axes() -> BTreeMap<u16, Axis> {
        let mut axes = BTreeMap::new();
        for c in [0, 1, 3, 4] { axes.insert(c, Axis { min: -32768, max: 32767, value: 0 }); }
        for c in [2, 5] { axes.insert(c, Axis { min: 0, max: 255, value: 0 }); }
        for c in [16, 17] { axes.insert(c, Axis { min: -1, max: 1, value: 0 }); }
        axes
    }
    fn nav(context: &str) -> Router {
        let (c, l) = crate::config::tests::fixture_nav();
        Router::with_context(c, l, axes(), context)
    }
    fn plain(context: &str) -> Router {
        let (c, l) = crate::config::tests::fixture();
        Router::with_context(c, l, axes(), context)
    }
    fn key(r: &mut Router, c: u16, v: i32) -> Output { r.event(Event::new(EV_KEY, c, v), 0) }
    fn abs(r: &mut Router, c: u16, v: i32) -> Output { let mut o = r.event(Event::new(EV_ABS, c, v), 0); o.extend(r.event(Event::new(EV_SYN, SYN_REPORT, 0), 0)); o }
    fn wait(r: &mut Router, ms: u64) -> Output { let mut o = Output::default(); for _ in 0..ms / 10 { o.extend(r.tick(0.01)); } o }
    fn tap(r: &mut Router, c: u16, ms: u64) -> Output { let mut o = key(r, c, 1); o.extend(wait(r, ms)); o.extend(key(r, c, 0)); o }

    #[test]
    fn south_tap_is_enter_and_hold_is_shift_enter_once() {
        let mut r = nav("desktop-2d");
        assert_eq!(r.nav_profile().as_deref(), Some("desktop"));
        let o = tap(&mut r, SOUTH, 100);
        assert_eq!(o.actions, vec!["key:ENTER"]);
        assert!(o.app.is_empty(), "an uncaptured window gets nav, not the virtual pad");
        let o = tap(&mut r, SOUTH, 600);
        assert_eq!(o.actions, vec!["key:SHIFT+ENTER"], "the hold fires once at capture_hold_ms; the release adds nothing");
        assert_eq!(tap(&mut r, SOUTH, 2000).actions, vec!["key:SHIFT+ENTER"], "a 2 s hold never repeats");
        assert_eq!(tap(&mut r, EAST, 50).actions, vec!["key:ESC"]);
        assert_eq!(tap(&mut r, 0x134, 50).actions, vec!["key:TAB"]);
    }
    #[test]
    fn hat_dpad_repeats_400_60_and_stops_on_focus_change() {
        let mut r = nav("desktop-2d");
        let mut o = abs(&mut r, 17, -1);
        o.extend(wait(&mut r, 1000));
        assert_eq!(o.actions, vec!["key:UP"; 12], "1 + (1 + floor((1000-400)/60)) arrows");
        assert!(abs(&mut r, 17, 0).actions.is_empty());
        // INV-6: a held arrow stops repeating the moment focus moves to another window, of any class.
        let mut r = nav("desktop-2d");
        r.set_focus("desktop-2d", Some(("0xa".into(), 1)), false);
        abs(&mut r, 16, 1); let before = wait(&mut r, 500).actions.len();
        r.set_focus("game", Some(("0xgame".into(), 2)), false);
        assert!(wait(&mut r, 1000).actions.is_empty(), "repeat must stop on a focus change ({before} before)");
        assert!(abs(&mut r, 16, 0).actions.is_empty(), "the release in the next window fires nothing");
        // ... and on a capture.
        let mut r = nav("desktop-2d");
        abs(&mut r, 16, -1); wait(&mut r, 450);
        r.set_focus("app-capture", None, false);
        let o = wait(&mut r, 500);
        assert!(o.actions.is_empty());
    }
    #[test]
    fn every_uncaptured_window_kind_navigates_alike() {
        for context in ["desktop-2d", "game", "vm-passthrough"] {
            let mut r = nav(context);
            assert_eq!(tap(&mut r, SOUTH, 50).actions, vec!["key:ENTER"], "{context}");
            assert_eq!(tap(&mut r, TR2, 50).actions, vec!["key:CTRL+TAB"], "{context}");
            assert_eq!(tap(&mut r, NORTH, 50).actions, vec!["input:window on"], "North still captures in {context}");
        }
        // Negative control: without nav (the pre-I2 SSOT) an uncaptured window got raw pad buttons.
        let mut r = plain("desktop-2d");
        let o = tap(&mut r, SOUTH, 50);
        assert!(o.actions.is_empty() && o.app.contains(&Event::new(EV_KEY, SOUTH, 1)));
        // O-1 revert line: desktop-2d = "vpad" forwards the pad again.
        let (c, l) = crate::config::tests::fixture_nav_with(|v| { v["keybindings"]["global"]["controller"]["nav"]["contexts"]["desktop-2d"] = "vpad".into(); });
        let mut r = Router::with_context(c, l, axes(), "desktop-2d");
        let o = tap(&mut r, SOUTH, 50);
        assert!(o.actions.is_empty() && o.app.contains(&Event::new(EV_KEY, SOUTH, 1)), "vpad forwards");
    }
    #[test]
    fn captured_and_room_never_navigate() {
        let mut r = nav("app-capture");
        assert!(r.nav_profile().is_none());
        let o = tap(&mut r, SOUTH, 50);
        assert!(o.actions.is_empty() && o.app.contains(&Event::new(EV_KEY, SOUTH, 1)));
        let mut r = nav("desktop-3d");
        assert!(tap(&mut r, SOUTH, 50).actions.is_empty() && r.pad.sequences[2] == 1, "the room keeps South = jump");
    }
    #[test]
    fn terminal_profile_follows_the_tmux_client() {
        let mut r = nav("terminal");
        assert_eq!(r.nav_profile().as_deref(), Some("terminal_plain"));
        assert_eq!(tap(&mut r, TL2, 50).actions, vec!["key:SHIFT+PAGEUP"]);
        assert_eq!(tap(&mut r, EAST, 600).actions, vec!["key:CTRL+c"]);
        r.set_tmux(true);
        assert_eq!(r.nav_profile().as_deref(), Some("terminal_tmux"));
        assert_eq!(tap(&mut r, TL2, 50).actions, vec!["tmux:key p"]);
        assert_eq!(tap(&mut r, SOUTH, 50).actions, vec!["key:ENTER"], "inherits desktop");
        assert_eq!(tap(&mut r, SELECT, 50).actions, vec!["tmux:key w"]);
        // A tmux client appearing mid-hold flushes the plain profile's timers (INV-6).
        let mut r = nav("terminal");
        key(&mut r, SOUTH, 1); wait(&mut r, 200);
        r.set_tmux(true);
        assert!(wait(&mut r, 600).actions.is_empty() && key(&mut r, SOUTH, 0).actions.is_empty());
        // tmux: never fires without a client: the plain profile has no tmux: value at all.
        let plain = nav("terminal");
        let p = &plain.global.controller.nav.profiles["terminal_plain"];
        assert!(p.values().all(|v| v.actions().iter().all(|a| !a.starts_with("tmux:"))));
    }
    #[test]
    fn right_stick_flicks_focus_once_per_deflection() {
        let mut r = nav("desktop-2d");
        let mut o = abs(&mut r, 3, 30000);
        o.extend(wait(&mut r, 1500));
        o.extend(abs(&mut r, 3, 25000));
        assert_eq!(o.actions, vec!["hypr:movefocus right"], "one flick, no repeat");
        assert!(abs(&mut r, 3, 15000).actions.is_empty(), "between flick_off and flick_on: still disarmed");
        abs(&mut r, 3, 0);
        assert_eq!(abs(&mut r, 4, -30000).actions, vec!["hypr:movefocus up"]);
        abs(&mut r, 4, 0);
        r.set_tmux(true); r.set_context("terminal");
        assert_eq!(abs(&mut r, 3, -30000).actions, vec!["tmux:focus left"]);
        assert!(abs(&mut r, 3, 15000).actions.is_empty(), "small reverse deflection is no flick");
    }
    #[test]
    fn left_stick_scrolls_both_wheels_analog() {
        let mut r = nav("desktop-2d");
        abs(&mut r, 1, -32767);
        let o = wait(&mut r, 200);
        let wheel: i32 = o.pointer.iter().filter(|e| e.kind == EV_REL && e.code == 8).map(|e| e.value).sum();
        assert!(wheel > 0, "stick up = wheel up ({wheel})");
        assert!(o.actions.is_empty() && o.app.is_empty());
        abs(&mut r, 1, 0); abs(&mut r, 0, 32767);
        let o = wait(&mut r, 200);
        assert!(o.pointer.iter().any(|e| e.kind == EV_REL && e.code == 6 && e.value > 0), "stick right = REL_HWHEEL right");
        assert!(o.pointer.iter().all(|e| !(e.kind == EV_REL && e.code == 8)));
    }
    #[test]
    fn cursor_mode_keeps_button_meanings_and_adds_hwheel() {
        let mut r = nav("desktop-2d");
        assert_eq!(tap(&mut r, START, 10).actions, vec!["input:cursor on"]);
        assert_eq!(tap(&mut r, SOUTH, 50).actions, vec!["key:ENTER"], "C-11: South is Enter in cursor mode");
        let o = key(&mut r, TR2, 1);
        assert!(o.pointer.contains(&Event::new(EV_KEY, 0x110, 1)) && o.actions.is_empty(), "RT is still the left click");
        let o = wait(&mut r, 800); assert!(o.actions.iter().all(|a| a != "key:CTRL+TAB"));
        key(&mut r, TR2, 0);
        abs(&mut r, 0, 32767);
        assert!(wait(&mut r, 100).pointer.iter().any(|e| e.kind == EV_REL && e.code == 6 && e.value > 0));
        abs(&mut r, 0, 0);
        assert_eq!(abs(&mut r, 17, 1).actions, vec!["key:DOWN"], "the d-pad is arrows in cursor mode");
        // Negative: the pre-I2 router dropped buttons in cursor mode.
        let mut p = plain("desktop-2d");
        tap(&mut p, START, 10);
        let o = tap(&mut p, SOUTH, 50);
        assert!(o.actions.is_empty() && o.app.is_empty());
    }
    #[test]
    fn thumb_clicks_resolve_on_release_and_the_chord_cancels_them() {
        let mut r = nav("desktop-2d");
        assert!(key(&mut r, L3, 1).actions.is_empty(), "L3 waits for the release");
        assert_eq!(key(&mut r, L3, 0).actions, vec!["key:CTRL+t"]);
        assert_eq!(tap(&mut r, R3, 50).actions, vec!["key:CTRL+0"]);
        // Fast chord: the room toggle only.
        let mut o = r.event(Event::new(EV_KEY, L3, 1), 1000);
        o.extend(r.event(Event::new(EV_KEY, R3, 1), 1100));
        o.extend(key(&mut r, L3, 0)); o.extend(key(&mut r, R3, 0));
        assert_eq!(o.actions, vec!["mios:hyprland-3d-toggle"]);
        // Slow overlap (outside debounce_ms): no singles and no toggle.
        let mut r = nav("desktop-2d");
        let mut o = r.event(Event::new(EV_KEY, L3, 1), 1000);
        o.extend(wait(&mut r, 400));
        o.extend(r.event(Event::new(EV_KEY, R3, 1), 1400));
        o.extend(key(&mut r, R3, 0)); o.extend(key(&mut r, L3, 0));
        assert!(o.actions.is_empty(), "{:?}", o.actions);
        // Sloppy click: L3 while the left stick is deflected does nothing.
        let mut r = nav("desktop-2d");
        abs(&mut r, 0, 30000);
        assert!(tap(&mut r, L3, 50).actions.is_empty());
        abs(&mut r, 0, 0);
        assert_eq!(tap(&mut r, L3, 50).actions, vec!["key:CTRL+t"]);
    }
    #[test]
    fn guide_l3_r3_fires_neither_lock_nor_latch() {
        let (mut c, l) = crate::config::tests::fixture_nav();
        c.controller.system_layer.insert("BTN_THUMBL".into(), "quickshell:lock".into());
        c.controller.system_layer.insert("BTN_THUMBR".into(), "mios:toggle_vm_passthrough".into());
        let mut r = Router::with_context(c, l, axes(), "desktop-2d");
        let mut o = key(&mut r, GUIDE, 1);
        for (code, v) in [(L3, 1), (R3, 1), (L3, 0), (R3, 0), (GUIDE, 0)] { o.extend(key(&mut r, code, v)); }
        assert!(o.actions.is_empty(), "{:?}", o.actions);
        let mut o = key(&mut r, GUIDE, 1);
        for (code, v) in [(L3, 1), (L3, 0), (GUIDE, 0)] { o.extend(key(&mut r, code, v)); }
        assert_eq!(o.actions, vec!["quickshell:lock"], "Guide+L3 alone still locks, on release");
    }
    #[test]
    fn guide_tap_opens_the_drawer_only_when_alone_and_short() {
        let mut r = nav("desktop-2d");
        assert_eq!(tap(&mut r, GUIDE, 100).actions, vec!["quickshell:drawer-toggle"]);
        assert!(tap(&mut r, GUIDE, 600).actions.is_empty(), "a Guide hold is the system-layer modifier");
        let mut o = key(&mut r, GUIDE, 1);
        o.extend(tap(&mut r, LB, 20)); o.extend(key(&mut r, GUIDE, 0));
        assert_eq!(o.actions, vec!["hypr:workspace e-1"], "Guide+LB is the system layer, never the drawer too");
        // R-26: a Guide press cancels a pending South hold.
        key(&mut r, SOUTH, 1); wait(&mut r, 200);
        let mut o = key(&mut r, GUIDE, 1); o.extend(wait(&mut r, 600)); o.extend(key(&mut r, GUIDE, 0)); o.extend(key(&mut r, SOUTH, 0));
        assert!(o.actions.is_empty(), "{:?}", o.actions);
        // Negative: the pre-I2 router swallowed the uncaptured Guide tap.
        let mut p = plain("desktop-2d");
        assert!(tap(&mut p, GUIDE, 100).actions.is_empty());
    }
    #[test]
    fn analog_only_triggers_get_digital_edges() {
        let mut r = nav("desktop-2d"); r.analog_triggers = true;
        assert_eq!(abs(&mut r, 5, 200).actions, vec!["key:CTRL+TAB"]);
        let o = wait(&mut r, 760);
        assert_eq!(o.actions, vec!["key:CTRL+TAB"; 2], "500/250 repeat: at 500 and 750 ms");
        assert!(abs(&mut r, 5, 0).actions.is_empty());
        assert_eq!(abs(&mut r, 2, 255).actions, vec!["key:CTRL+SHIFT+TAB"]);
        // A pad with digital triggers (DualSense) gets exactly one action per pull, from the button.
        let mut d = nav("desktop-2d");
        let mut o = abs(&mut d, 5, 200); o.extend(key(&mut d, TR2, 1)); o.extend(key(&mut d, TR2, 0)); o.extend(abs(&mut d, 5, 0));
        assert_eq!(o.actions, vec!["key:CTRL+TAB"]);
    }
    #[test]
    fn repaired_releases_fire_nothing() {
        let mut r = nav("desktop-2d");
        key(&mut r, SOUTH, 1);
        let o = r.reconcile(&BTreeSet::new(), 5);
        assert!(o.actions.is_empty(), "a missed release repaired from kernel truth is not a tap");
        key(&mut r, GUIDE, 1);
        assert!(r.reconcile(&BTreeSet::new(), 6).actions.is_empty());
    }
    #[test]
    fn grammar_validation_two_sided() {
        let with = |path: &[&str], val: toml::Value| crate::config::tests::try_fixture_nav_with(|v| {
            let mut t = &mut v["keybindings"]["global"]["controller"]["nav"];
            for p in &path[..path.len() - 1] { t = &mut t[*p]; }
            t.as_table_mut().unwrap().insert(path[path.len() - 1].into(), val);
        });
        // Arrays replace atomically: a user [tap, "none"] really unbinds the hold.
        let (c, l) = with(&["desktop", "BTN_SOUTH"], toml::Value::Array(vec!["key:SPACE".into(), "none".into()])).unwrap();
        let mut r = Router::with_context(c, l, axes(), "desktop-2d");
        assert!(tap(&mut r, SOUTH, 700).actions.is_empty());
        assert_eq!(tap(&mut r, SOUTH, 50).actions, vec!["key:SPACE"]);
        for (path, val, why) in [
            (vec!["desktop", "BTN_SOUTH"], toml::Value::from(""), "\"\" is not a value"),
            (vec!["desktop", "BTN_SOUTH"], toml::Value::from("app"), "namespace"),
            (vec!["terminal_tmux", "BTN_TL2"], toml::Value::from("tmux:key q"), "prefix-table key"),
            (vec!["desktop", "BTN_TL"], toml::Value::from("key:ENTER"), "capture contract"),
            (vec!["desktop", "BTN_MODE"], toml::Value::Array(vec!["key:ENTER".into(), "key:ESC".into()]), "Guide hold"),
            (vec!["desktop", "BTN_SOUTH"], toml::Value::from("pointer:wheel-up"), "only LS_*/RS_*"),
            (vec!["desktop", "BTN_SOUTH"], toml::Value::from("key:HYPER+a"), "unknown modifier"),
            (vec!["desktop", "inherits"], toml::Value::from("terminal_plain"), "cycle"),
            (vec!["repeat", "RS_LEFT"], toml::Value::Array(vec![400.into(), 60.into()]), "never repeat"),
            (vec!["contexts", "app-capture"], toml::Value::from("desktop"), "not an uncaptured"),
            (vec!["contexts", "desktop-2d"], toml::Value::from("missing"), "no profile"),
        ] {
            let e = with(&path, val).err().unwrap_or_default();
            assert!(e.contains(why), "{path:?}: {e}");
        }
    }
    #[test]
    fn axis_owner_property_holds_with_nav() {
        // INV-5 with the nav sink in play: the room packet and host wheel/pointer never share a frame.
        let mut g = 12345u64;
        let mut next = || { g = g.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407); g >> 33 };
        for seed in 0..24 {
            let mut r = nav("desktop-2d");
            let _ = seed;
            for _ in 0..300 {
                let mut o = Output::default();
                match next() % 6 {
                    0 => { let c = ["desktop-2d", "desktop-3d", "terminal", "game", "app-capture"][(next() % 5) as usize]; o.extend(r.set_focus(c, Some((format!("0x{}", next() % 3), 1)), c == "desktop-3d")); }
                    1 => { let b = [SOUTH, START, L3, R3, NORTH, GUIDE][(next() % 6) as usize]; o.extend(key(&mut r, b, (next() % 2) as i32)); }
                    _ => { let code = [0u16, 1, 3, 4][(next() % 4) as usize]; o.extend(abs(&mut r, code, (next() % 65536) as i32 - 32768)); }
                }
                o.extend(r.tick(0.01));
                let pad_live = r.pad.lx != 0.0 || r.pad.ly != 0.0 || r.pad.rx != 0.0 || r.pad.ry != 0.0;
                let host = o.pointer.iter().any(|e| e.kind == EV_REL);
                let app = o.app.iter().any(|e| e.kind == EV_ABS && [0, 1, 3, 4].contains(&e.code) && e.value.unsigned_abs() > 4000);
                assert!(!(pad_live && (host || app)) && !(host && app), "context {} owned {} cursor {}", r.context, r.app_owned(), r.cursor);
            }
        }
    }
}
