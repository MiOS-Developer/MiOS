// AI-hint: Per-controller input state machine; system actions never enter app replay.
// AI-related: usr/libexec/mios/mios-gamepad-hypr3d, keybindings.global.controller
use crate::config::{axis_code, button_code, ControllerConfig, GlobalConfig, LegacyConfig};
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
    /// The virtual pad: the captured app (or, uncaptured until I2, the focused client).
    App,
    /// The Hypr3D room packet (camera, movement, grab/resize).
    Room,
    /// The host pointer (cursor mode or half-capture).
    Pointer,
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
            #[cfg(test)]
            mutant_room_forward: false,
        }
    }
    pub fn policy(&self) -> &GlobalConfig {
        &self.global
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
        self.transition_context(context,force,owned,room)
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
            return Output::default();
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
        for code in released { out.extend(self.event(Event::new(EV_KEY, code, 0), now)); }
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
        if self.pointer_mode() && (!self.half_capture() || self.pointer_axis(code)) {
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
        !self.room() && (!self.pointer_mode() || self.half_capture())
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
                    self.cursor=!self.cursor;
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
                }
                return o;
            }
            if !app_guide && guide_held {
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
                    return o;
                }
            }
            if self.room() {
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
        } else if e.kind == EV_SYN && e.code == SYN_REPORT {
            self.update_pad();
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
        self.update_pad();
        if !self.pointer_mode() {
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
        let (_, scroll) = self.stick(0, 1, stick_deadzone, 1.0);
        self.fraction_scroll -= scroll * scroll_sensitivity * dt;
        let wheel = self.fraction_scroll.trunc() as i32;
        self.fraction_scroll -= f64::from(wheel);
        if wheel != 0 {
            o.pointer.push(Event::new(EV_REL, 8, wheel));
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
                    8 => { if g.next() % 2 == 0 { o.extend(r.adopt_ownership(g.next() % 2 == 0)); } else { o.extend(r.ownership_failed()); } }
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
