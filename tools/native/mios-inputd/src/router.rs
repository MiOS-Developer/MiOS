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
    pending_guide: bool,
    fraction_x: f64,
    fraction_y: f64,
    fraction_scroll: f64,
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
            pending_guide: false,
            fraction_x: 0.0,
            fraction_y: 0.0,
            fraction_scroll: 0.0,
        }
    }
    pub fn policy(&self) -> &GlobalConfig {
        &self.global
    }
    pub fn set_focus(&mut self, context: &str, target: Option<(String,u64)>, from_room: bool) -> Output {
        let force=self.capture_target!=target && (self.app_owned() || Self::app_context(context));
        self.capture_target=target; self.room_capture=from_room;
        self.transition_context(context,force)
    }
    fn app_context(context: &str) -> bool { ["game","vm-passthrough","app-capture"].contains(&context) }
    fn app_owned(&self) -> bool { Self::app_context(&self.context) }
    pub fn set_context(&mut self, context: &str) -> Output { self.transition_context(context,false) }
    fn transition_context(&mut self, context: &str, force: bool) -> Output {
        // Pointer drags and cursor mode survive focus moving between ordinary
        // desktop clients, terminals and HUDs. Actual capture boundaries reset.
        let host2d=|v:&str| ["desktop-2d","terminal"].contains(&v);
        if !force && (self.context == context || (host2d(&self.context) && host2d(context))) {
            self.context=context.into();
            return Output::default();
        }
        let mut o = self.release_outputs();
        self.suppressed.extend(&self.pressed);
        self.blocked.extend(&self.pressed);
        self.context = context.into();
        self.cursor = false;
        self.pending_guide = false;
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
        self.context == "desktop-3d"
    }
    fn pointer_mode(&self) -> bool {
        self.cursor && !self.room()
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
            if room {
                self.pad.increment(5);
            } else {
                o.actions.push("hypr:fullscreen".into());
            }
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
            if e.value == 0 && self.suppressed.remove(&e.code) {
                return o;
            }
            if self.suppressed.contains(&e.code) {
                return o;
            }
            let guide = button_code(&self.global.controller.guide_button).unwrap();
            let guide_held = self.pressed.contains(&guide);
            let captured=self.app_owned() && !self.cursor && !self.global.controller.captured_system_layer;
            if captured {
                let escape:Vec<_>=self.global.controller.mode_override_chord.iter().map(|v|button_code(v).unwrap()).collect();
                if e.value==1 && !already && escape.contains(&e.code) && escape.iter().all(|c|self.pressed.contains(c)) {
                    o.extend(self.release_outputs()); self.suppressed.extend(&escape); self.pending_guide=false;
                    self.cursor=true; o.actions.push("input:cursor on".into()); return o;
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
            if !captured && e.code == guide {
                if e.value == 1 {
                    o.extend(self.release_outputs());
                    self.suppressed
                        .extend(self.pressed.iter().filter(|c| **c != guide));
                }
                return o;
            }
            if !captured && guide_held {
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
                if (!self.app_owned() || self.room_capture)
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
                    if captured || !b.chord.starts_with("BTN_") || !b.active_in(&self.context) {
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
                let app_context = self.app_owned();
                if (!app_context || self.cursor) && self.legacy_action(e.code, now, &mut o) {
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
                }
                return o;
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
            if !self.room()
                && !self.pointer_mode()
                && !self.button(&self.global.controller.guide_button)
            {
                o.app.push(e);
            }
        } else if e.kind == EV_SYN && e.code == SYN_REPORT {
            self.update_pad();
            if !self.room() && !self.pointer_mode() {
                o.app.push(e);
            }
        } else if !self.room() && !self.pointer_mode() {
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
        assert_eq!(out.actions,vec!["mios:hyprland-3d-toggle"]);
        let mut game=router();game.global.controller.captured_system_layer=false;game.set_context("game");
        key(&mut game,0x13d,1,1);assert!(key(&mut game,0x13e,1,2).actions.is_empty(),"ordinary games retain both thumbstick buttons");
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
    fn game_shoulder_passes_without_guide() {
        let mut r = router();
        r.set_context("game");
        assert!(!key(&mut r, 0x136, 1, 1).app.is_empty());
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
        let o = r.set_context("game");
        assert!(o.pointer.contains(&Event::new(EV_KEY, 0x110, 0)));
        assert!(!r.cursor);
    }
    #[test]
    fn held_keys_do_not_cross_contexts() {
        let mut r = router();
        r.set_context("game");
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
