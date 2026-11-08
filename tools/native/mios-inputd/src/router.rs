// AI-hint: Per-controller input state machine: ownership contract first, then the nav projection of uncaptured windows (tap/hold, repeat, flicks, HAT fold, thumb/Guide disambiguation); system actions never enter app replay.
// AI-related: usr/libexec/mios/mios-gamepad-hypr3d, keybindings.global.controller
use crate::config::{
    axis_code, button_code, system_layer_code, ControllerConfig, GlobalConfig, LegacyConfig,
    RoomAction,
};
use crate::nav::{
    Dir, FocusGesture, Input, Value, BTN_DPAD_DOWN, BTN_DPAD_LEFT, BTN_DPAD_RIGHT, BTN_DPAD_UP,
};
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
/// A room-table press waiting for its release (tap) or its hold threshold (spec P1, C-14b).
#[derive(Clone, Debug)]
struct RoomPending {
    tap: String,
    hold: Option<String>,
    elapsed: f64,
    fired: bool,
    cancelled: bool,
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
/// Per-device nav state (INV-6: flushed on any context, profile, app, owner or target change, a cursor-mode toggle, a
/// Guide press and SYN_DROPPED).
#[derive(Clone, Debug, Default)]
struct NavState {
    held: BTreeMap<Input, Held>,
    profile: Option<String>,
    /// P4: the verified foreground app whose layer selected `profile` (None: the base profile applies).
    app: Option<String>,
    armed: [bool; 2],
    stick_dir: [Option<Dir>; 2],
    /// P5: camera profiles treat each stick's x and y as separate half-axes: [stick][axis] armed and held direction.
    axis_armed: [[bool; 2]; 2],
    axis_dir: [[Option<Dir>; 2]; 2],
    hat: (i32, i32),
    triggers: [bool; 2],
    thumbs_cancelled: bool,
    wheel: f64,
    hwheel: f64,
    /// task3773: the right-stick focus-gesture tracker (see `FocusGesture` for units).
    focus: FocusTracker,
}

/// task3773 right-stick focus-gesture tracker. Magnitudes are post-deadzone normalized
/// fractions (the same scale `nav_flicks` uses); `now_ms` advances by `dt` every tick, so
/// every deadline is wall-clock tick time and independent of the polling rate.
#[derive(Clone, Debug, Default)]
struct FocusTracker {
    now_ms: f64,
    /// Continuous dwell below neutral_pct; ANY excursion above it resets this to zero.
    dwell_ms: f64,
    /// Set once the dwell completed; consumed by the first excursion above neutral, so
    /// jitter around the boundary can never start a rise without a real dwell.
    armed: bool,
    /// When the current rise left neutral and its cardinal direction.
    rising_since: Option<(f64, Dir)>,
    /// (when the peak locked, the locked cardinal direction) while waiting for the return.
    peak: Option<(f64, Dir)>,
    /// Firing sets this; no new gesture may leave neutral before it passes.
    cooldown_until: f64,
}
impl FocusTracker {
    /// Cancel any pending gesture (every INV-6 flush path calls this); the clock and the
    /// post-fire cooldown survive, so a context switch cannot shortcut a bounce guard.
    fn cancel(&mut self) {
        self.dwell_ms = 0.0;
        self.armed = false;
        self.rising_since = None;
        self.peak = None;
    }
}
enum GuideSingle {
    Action(String),
    Room(String),
    Raw(u16),
    None,
}
struct GuidePending {
    at: u64,
    elapsed: f64,
    single: GuideSingle,
}
enum CaptureConfirmation {
    Pending,
    Selected(String),
}
/// Cursor mode, context and capture target saved while a shell overlay owns navigation.
type SavedShellCursor = (bool, String, Option<(String, u64)>);

/// Continuous pointer velocity. Integrating the exponential response analytically makes travel independent of
/// polling rate; radial shaping keeps diagonals balanced. A centered stick stops immediately, without drift.
#[derive(Default)]
struct PointerMotion {
    velocity: [f64; 2],
}
impl PointerMotion {
    fn advance(
        &mut self,
        input: (f64, f64),
        dt: f64,
        speed: f64,
        exponent: f64,
        tau: f64,
    ) -> [f64; 2] {
        let magnitude = input.0.hypot(input.1).min(1.0);
        if magnitude == 0.0 {
            self.velocity = [0.0; 2];
            return [0.0; 2];
        }
        let shape = magnitude.powf(exponent - 1.0) * speed;
        let target = [input.0 * shape, input.1 * shape];
        let mut travel = [0.0; 2];
        for i in 0..2 {
            if tau <= 0.0 {
                self.velocity[i] = target[i];
                travel[i] = target[i] * dt;
            } else {
                let decay = (-dt / tau).exp();
                travel[i] = target[i] * dt + (self.velocity[i] - target[i]) * tau * (1.0 - decay);
                self.velocity[i] = target[i] + (self.velocity[i] - target[i]) * decay;
            }
        }
        travel
    }
}
pub struct Router {
    /// Room-table presses waiting for their release or hold (spec P1, C-14b).
    room_pending: BTreeMap<u16, RoomPending>,
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
    capture_target: Option<(String, u64)>,
    capture_confirmation: Option<CaptureConfirmation>,
    capture_navigation_context: Option<String>,
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
    pointer_motion: PointerMotion,
    fraction_x: f64,
    fraction_y: f64,
    fraction_scroll: f64,
    fraction_hwheel: f64,
    nav: NavState,
    /// A MiOS tmux client runs under the focused terminal (C-09): selects `<profile>_tmux` over `<profile>_plain`.
    pub tmux: bool,
    /// P3/P4: the verified foreground app of the focused terminal (or this route's pane), a key of [nav.apps]. It is
    /// bound to the target it was verified for: a focus change clears it until the new target is verified.
    app: Option<String>,
    /// The pad reports analog triggers only (no BTN_TL2/TR2, e.g. xpad): nav synthesizes their digital edges.
    pub analog_triggers: bool,
    /// Kernel-truth repair is releasing buttons: a repaired release never fires a tap.
    repairing: bool,
    /// Guide pressed alone and not yet held past capture_hold_ms (R-26): its release is the Guide tap (the nav
    /// profile's BTN_MODE in an uncaptured window, the room table's BTN_MODE in the room). (press time, ticked ms)
    guide_tap: Option<(u64, f64)>,
    /// Clean physical Guide press, measured by both event and tick clocks.
    guide_clean: Option<(u64, f64)>,
    /// The first tap's original owner resolves only when a second tap can no longer join.
    guide_single: Option<GuidePending>,
    /// Keyboard navigation belongs to the open shell surface, independently of window capture.
    shell_overlay: bool,
    shell_cursor: Option<SavedShellCursor>,
    /// Guide + a toggle-chord button: the system-layer action waits for the release, so Guide+L3+R3 fires neither.
    guide_thumb: Option<u16>,
    guide_thumb_cancel: bool,
    #[cfg(test)]
    pub(crate) mutant_room_forward: bool,
}
impl Router {
    pub fn new(global: GlobalConfig, legacy: LegacyConfig, axes: BTreeMap<u16, Axis>) -> Self {
        Self {
            room_pending: BTreeMap::new(),
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
            capture_confirmation: None,
            capture_navigation_context: None,
            room_capture: false,
            capture_override: None,
            override_ms: 0.0,
            north_pending: false,
            north_elapsed: 0.0,
            north_fired: false,
            escape_elapsed: 0.0,
            escape_fired: false,
            pending_guide: false,
            pointer_motion: PointerMotion::default(),
            fraction_x: 0.0,
            fraction_y: 0.0,
            fraction_scroll: 0.0,
            fraction_hwheel: 0.0,
            nav: NavState {
                armed: [true; 2],
                axis_armed: [[true; 2]; 2],
                ..NavState::default()
            },
            tmux: false,
            app: None,
            analog_triggers: false,
            repairing: false,
            guide_tap: None,
            guide_clean: None,
            guide_single: None,
            shell_overlay: false,
            shell_cursor: None,
            guide_thumb: None,
            guide_thumb_cancel: false,
            #[cfg(test)]
            mutant_room_forward: false,
        }
    }
    /// Construct and settle the nav profile of the initial context.
    pub fn with_context(
        global: GlobalConfig,
        legacy: LegacyConfig,
        axes: BTreeMap<u16, Axis>,
        context: &str,
    ) -> Self {
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
    pub fn owns_controller(context: &str) -> bool {
        context == "app-capture"
    }
    fn app_context(context: &str) -> bool {
        Self::owns_controller(context)
    }
    pub fn app_owned(&self) -> bool {
        self.capture_override
            .unwrap_or_else(|| Self::app_context(&self.context))
    }
    /// Half-capture (C-01): the app still owns every button; only the sticks and LT/RT drive the host pointer.
    pub fn half_capture(&self) -> bool {
        self.app_owned() && self.cursor
    }
    /// The pending (unconfirmed) ownership this router requested, if any.
    pub fn ownership(&self) -> Option<bool> {
        self.capture_override
    }
    /// Select navigation delivery for a captured client's unowned context. The runtime supplies the configured
    /// base classification and targets its output to the captured client; ownership and release remain unchanged.
    pub fn set_capture_navigation_context(&mut self, context: Option<&str>) -> Output {
        let context = context
            .filter(|c| {
                self.global
                    .controller
                    .nav
                    .profile_name(c, self.tmux)
                    .is_some()
            })
            .map(str::to_owned);
        if context == self.capture_navigation_context {
            return Output::default();
        }
        let mut out = if self.app_owned() {
            self.release_outputs()
        } else {
            Output::default()
        };
        self.capture_navigation_context = context;
        if self.app_owned() {
            self.suppressed.extend(&self.pressed);
            self.blocked.extend(&self.pressed);
        }
        out.extend(self.nav_sync());
        out
    }
    /// A focused shell layer owns navigation while the underlying window retains its capture state.
    pub fn set_shell_overlay(&mut self, open: bool) -> Output {
        let open = open && !self.console();
        if self.shell_overlay == open {
            return Output::default();
        }
        let mut out = self.release_outputs();
        out.extend(self.nav_flush());
        if open {
            self.shell_cursor = Some((
                self.cursor,
                self.context.clone(),
                self.capture_target.clone(),
            ));
            self.cursor = false;
        } else if let Some((cursor, context, target)) = self.shell_cursor.take() {
            let neutral = self.axes.iter().all(|(code, axis)| {
                if *code == 2 || *code == 5 {
                    axis.trigger() <= self.legacy.trigger_threshold
                } else {
                    axis.centered().abs() <= self.global.controller.stick_deadzone
                }
            });
            self.cursor =
                cursor && context == self.context && target == self.capture_target && neutral;
        }
        self.shell_overlay = open;
        self.guide_clean = None;
        self.guide_single = None;
        self.guide_tap = None;
        self.pending_guide = false;
        self.guide_thumb = None;
        self.north_pending = false;
        self.room_pending.clear();
        self.suppressed.extend(&self.pressed);
        self.blocked.extend(&self.pressed);
        self.pad.neutralize();
        out.extend(self.nav_sync());
        out
    }
    pub fn shell_overlay(&self) -> bool {
        self.shell_overlay
    }
    pub fn captured_navigation(&self) -> bool {
        !self.shell_overlay
            && self.app_owned()
            && self.capture_navigation_context.as_ref().is_some_and(|c| {
                self.global
                    .controller
                    .nav
                    .profile_name(c, self.tmux)
                    .is_some()
            })
    }
    /// Bind a successful capture to the window the compositor selected, rather than the pre-press cached focus.
    /// The caller supplies only the latest successful capture receipt; failures use ownership_failed instead.
    pub fn confirm_capture_target(&mut self, address: &str) {
        if self.capture_override == Some(true)
            && self.capture_confirmation.is_some()
            && !address.is_empty()
        {
            self.capture_confirmation = Some(CaptureConfirmation::Selected(address.into()));
        }
    }
    pub fn set_focus(
        &mut self,
        context: &str,
        target: Option<(String, u64)>,
        from_room: bool,
    ) -> Output {
        let target_changed = self.capture_target != target;
        if target_changed {
            self.escape_elapsed = 0.0;
            self.escape_fired = false;
            self.guide_clean = None;
            self.guide_single = None;
            // P8: an app verified for the previous window never applies to the next one.
            self.app = None;
        }
        if self.capture_override == Some(true) {
            let pending = matches!(
                self.capture_confirmation,
                Some(CaptureConfirmation::Pending)
            );
            let selected = matches!(&self.capture_confirmation,Some(CaptureConfirmation::Selected(address))
                if target.as_ref().is_some_and(|(actual,_)| actual==address));
            if pending || selected {
                let (owned, room) = (self.app_owned(), self.room());
                self.capture_target = target;
                // Only a receipt followed by its matching owned snapshot confirms the transaction. Until then
                // context_stale_ms still bounds deferral, even when a snapshot already carries a capture tag.
                if selected && Self::app_context(context) {
                    self.capture_confirmation = None;
                    self.capture_override = None;
                }
                let mut out = self.transition_context(context, false, owned, room);
                if target_changed {
                    out.extend(self.nav_flush());
                }
                return out;
            }
        }
        let (owned, room) = (self.app_owned(), self.room());
        let receipt_mismatch = matches!(&self.capture_confirmation,Some(CaptureConfirmation::Selected(address))
            if !target.as_ref().is_some_and(|(actual,_)| actual==address));
        let force = receipt_mismatch || (target_changed && (owned || Self::app_context(context)));
        if receipt_mismatch
            || target_changed
            || self.capture_override == Some(Self::app_context(context))
        {
            self.capture_override = None;
            self.capture_confirmation = None;
        }
        self.capture_target = target;
        self.room_capture = from_room || context == "desktop-3d";
        let mut o = self.transition_context(context, force, owned, room);
        // INV-6: a held nav input never repeats into, or resolves its tap/hold in, another window.
        if target_changed {
            o.extend(self.nav_flush());
        }
        o
    }
    /// C-09: whether a MiOS tmux client runs under the focused terminal. A profile change flushes nav state.
    pub fn set_tmux(&mut self, tmux: bool) -> Output {
        if self.tmux != tmux {
            self.guide_clean = None;
            self.guide_single = None;
        }
        self.tmux = tmux;
        self.nav_sync()
    }
    /// P4: the verified foreground app (a key of [nav.apps]; anything else counts as none) and the target it was
    /// verified for (the focused window's address and pid; None on a VT or an explicit route). A result for another
    /// target than the current one is stale (focus moved while it was verified) and selects nothing. Any change flushes
    /// held nav input, then the profile follows (an app layer applies only to a base context it refines).
    pub fn set_app(&mut self, app: Option<&str>, verified_for: Option<&(String, u64)>) -> Output {
        let app = app
            .filter(|a| self.global.controller.nav.apps.contains_key(*a))
            .filter(|_| verified_for == self.capture_target.as_ref())
            .map(str::to_owned);
        let mut out = Output::default();
        if self.app != app {
            self.guide_clean = None;
            self.guide_single = None;
            self.app = app;
            out.extend(self.nav_flush());
        }
        out.extend(self.nav_sync());
        out
    }
    pub fn app(&self) -> Option<&str> {
        self.app.as_deref()
    }
    /// The focused window this router follows (address, pid); None on a VT or an explicit route.
    pub fn target(&self) -> Option<&(String, u64)> {
        self.capture_target.as_ref()
    }
    /// P5-P7: the active profile was selected by a camera app (axis-separable, repeating stick flicks).
    pub fn camera(&self) -> bool {
        self.nav.profile.is_some()
            && self
                .nav
                .app
                .as_ref()
                .and_then(|a| self.global.controller.nav.apps.get(a))
                .is_some_and(|a| a.camera)
    }
    pub fn set_context(&mut self, context: &str) -> Output {
        let (owned, room) = (self.app_owned(), self.room());
        self.transition_context(context, false, owned, room)
    }
    fn transition_context(
        &mut self,
        context: &str,
        force: bool,
        was_owned: bool,
        was_room: bool,
    ) -> Output {
        if context == "console" && self.shell_overlay {
            self.shell_overlay = false;
            self.shell_cursor = None;
            self.cursor = false;
        }
        // Pointer drags, held keys and cursor mode survive focus moving between uncaptured clients of any kind
        // (and a compositor confirming a capture this bridge already applied). Ownership or room boundaries reset.
        if self.context != context {
            self.guide_clean = None;
            self.guide_single = None;
        }
        let previous = std::mem::replace(&mut self.context, context.into());
        if !force && self.app_owned() == was_owned && self.room() == was_room {
            return self.nav_sync();
        }
        self.context = previous;
        let mut o = self.release_outputs();
        self.suppressed.extend(&self.pressed);
        self.blocked.extend(&self.pressed);
        self.room_pending.clear();
        self.escape_elapsed = 0.0;
        self.escape_fired = false;
        self.guide_tap = None;
        self.context = context.into();
        self.cursor = false;
        self.pending_guide = false;
        self.north_pending = false;
        self.capture_confirmation = None;
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
        self.guide_clean = None;
        self.guide_single = None;
        self.shell_cursor = None;
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
        self.north_pending = false;
        self.capture_confirmation = None;
        self.escape_elapsed = 0.0;
        self.escape_fired = false;
        self.room_pending.clear();
        self.pad.neutralize();
        o.extend(self.nav_flush());
        self.guide_tap = None;
        self.guide_thumb = None;
        o
    }
    fn release_outputs(&mut self) -> Output {
        self.pointer_motion = PointerMotion::default();
        self.fraction_x = 0.0;
        self.fraction_y = 0.0;
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
        let mut o = self.reset();
        self.pressed = keys.clone();
        self.blocked = keys.clone();
        self.suppressed = keys;
        self.axes = axes;
        self.dropping = false;
        // P5: re-arm from the kernel's axes, not the neutralized ones: a stick still deflected fires only after it
        // falls below flick_off.
        o.extend(self.nav_flush());
        o
    }
    /// Kernel truth may repair missed releases; it never invents a press or an
    /// action. Preserve genuinely held chords and release already-forwarded
    /// app/pointer buttons so a repaired router cannot leave a guest stuck.
    pub fn reconcile(&mut self, held: &BTreeSet<u16>, now: u64) -> Output {
        if self.dropping {
            return Output::default();
        }
        let released: Vec<_> = self.pressed.difference(held).copied().collect();
        let guide = button_code(&self.global.controller.guide_button).unwrap();
        // A repaired release never resolves a room tap or a Guide tap: it is not evidence of the operator's release.
        for c in &released {
            if let Some(p) = self.room_pending.get_mut(c) {
                p.cancelled = true;
            }
        }
        if !held.contains(&guide) {
            self.pending_guide = false;
        }
        if !held.contains(&button_code(&self.legacy.fullscreen_button).unwrap()) {
            self.north_pending = false;
        }
        let mut out = Output::default();
        if !held.contains(&guide) {
            self.guide_tap = None;
        }
        self.repairing = true;
        for code in released {
            out.extend(self.event(Event::new(EV_KEY, code, 0), now));
        }
        self.repairing = false;
        self.blocked.retain(|c| held.contains(c));
        self.suppressed.retain(|c| held.contains(c));
        self.chord_times.retain(|c, _| held.contains(c));
        self.update_pad();
        if !out.app.is_empty() {
            out.app.push(Event::new(EV_SYN, SYN_REPORT, 0));
        }
        out
    }
    fn button(&self, name: &str) -> bool {
        button_code(name).is_ok_and(|c| self.pressed.contains(&c) && !self.blocked.contains(&c))
    }
    fn room(&self) -> bool {
        !self.shell_overlay
            && !self.app_owned()
            && (self.context == "desktop-3d"
                || (self.capture_override == Some(false) && self.room_capture))
    }
    /// The single sink of one axis this frame (INV-5).
    pub fn axis_sink(&self, code: u16) -> Sink {
        if self.shell_overlay {
            return Sink::Nav;
        }
        if self.room() {
            return Sink::Room;
        }
        if self.half_capture() {
            return if self.pointer_axis(code) {
                Sink::Pointer
            } else if self.captured_navigation() {
                Sink::Nav
            } else {
                Sink::App
            };
        }
        if self.app_owned() {
            return if self.captured_navigation() {
                Sink::Nav
            } else {
                Sink::App
            };
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
        self.escape_elapsed = 0.0;
        self.escape_fired = false;
        self.guide_clean = None;
        self.guide_single = None;
        let room = self.room();
        let out = self.release_outputs();
        self.room_pending.clear();
        self.guide_tap = None;
        if owned && !self.app_owned() {
            self.room_capture = room;
        }
        self.capture_override = Some(owned);
        self.capture_confirmation = if owned {
            Some(CaptureConfirmation::Pending)
        } else {
            None
        };
        self.override_ms = 0.0;
        self.cursor = !owned && !self.room_capture;
        self.north_pending = false;
        self.pending_guide = false;
        self.pad.neutralize();
        self.suppressed.extend(&self.pressed);
        let mut out = out;
        // INV-6: an owner change flushes held nav input even when a captured terminal keeps its profile, so a held
        // camera stick never repeats across a North capture or a release.
        out.extend(self.nav_flush());
        out.extend(self.nav_sync());
        out
    }
    fn capture_window(&mut self, fullscreen: bool) -> Output {
        let mut out = self.set_ownership(true);
        out.actions.push(format!(
            "input:window {}",
            if fullscreen { "fullscreen" } else { "on" }
        ));
        out
    }
    fn north_capture(&mut self) -> Output {
        let out = self.capture_window(false);
        self.north_pending = true;
        self.north_elapsed = 0.0;
        self.north_fired = false;
        out
    }
    fn release_window(&mut self) -> Output {
        let mut out = self.set_ownership(false);
        out.actions.push("input:window off".into());
        out
    }
    /// Another controller of this session captured or released the focused window: ownership is per session, never
    /// per pad, so every router follows it at once (no second pad may keep driving the room or the app).
    pub fn adopt_ownership(&mut self, owned: bool) -> Output {
        if self.capture_override == Some(owned) {
            return Output::default();
        }
        self.set_ownership(owned)
    }
    /// The ownership transaction failed (or was never confirmed): drop the pending override and follow the snapshot.
    pub fn ownership_failed(&mut self) -> Output {
        if self.capture_override.is_none() {
            return Output::default();
        }
        let (owned, room) = (self.app_owned(), self.room());
        self.capture_override = None;
        self.capture_confirmation = None;
        self.north_pending = false;
        self.override_ms = 0.0;
        let context = self.context.clone();
        self.transition_context(&context, false, owned, room)
    }
    fn room_entry(&self, code: u16) -> Option<RoomAction> {
        self.global
            .controller
            .room
            .iter()
            .find(|(name, _)| button_code(name).ok() == Some(code))
            .map(|(_, v)| v.clone())
    }
    /// A room action: "none" does nothing; a bare hypr:hypr3d:view is the packet's view slot (no process, next room
    /// frame); anything else is dispatched like every other action.
    fn room_fire(&mut self, action: &str, o: &mut Output) {
        match action {
            "none" | "" => {}
            "hypr:hypr3d:view" => self.pad.increment(0),
            a => o.actions.push(a.into()),
        }
    }
    fn toggle_codes(&self) -> Vec<u16> {
        self.legacy
            .toggle_chord
            .iter()
            .filter_map(|n| button_code(n).ok())
            .collect()
    }
    fn stick_deflected(&self, code: u16) -> bool {
        let (x, y) = if code == button_code("BTN_THUMBL").unwrap_or(0x13d) {
            (0, 1)
        } else {
            self.right_axes()
        };
        let x = self.axes.get(&x).map_or(0.0, Axis::centered);
        let y = self.axes.get(&y).map_or(0.0, Axis::centered);
        x.hypot(y) > self.global.controller.stick_deadzone
    }
    /// The room table for a press (spec P1, C-14b). Returns true when the table owns the button.
    fn room_press(&mut self, code: u16, o: &mut Output) -> bool {
        let Some(value) = self.room_entry(code) else {
            return false;
        };
        let toggle = self.toggle_codes();
        let (tap, hold) = value.parts();
        let (tap, hold) = (tap.to_owned(), hold.map(str::to_owned));
        if hold.is_none() && !toggle.contains(&code) {
            self.room_fire(&tap, o);
        } else {
            // L3/R3 (toggle_chord members) fire on a clean release: any overlap with another chord button cancels
            // both singles (R-23). R3 is also ignored while the right stick is deflected; L3 is not, so the sprint
            // toggle works while walking with the left stick.
            let overlap = toggle.contains(&code)
                && toggle
                    .iter()
                    .any(|c| *c != code && self.pressed.contains(c));
            let r3 = button_code("BTN_THUMBR").ok() == Some(code);
            let cancelled = overlap || (r3 && self.stick_deflected(code));
            self.room_pending.insert(
                code,
                RoomPending {
                    tap,
                    hold,
                    elapsed: 0.0,
                    fired: false,
                    cancelled,
                },
            );
        }
        true
    }
    fn pointer_mode(&self) -> bool {
        !self.shell_overlay && self.cursor && !self.room()
    }
    /// Frames (SYN and non-axis events) reach the app whenever any of its input can.
    fn forwards_frames(&self) -> bool {
        !self.shell_overlay
            && !self.room()
            && (!self.pointer_mode() || self.half_capture())
            && !self.nav_active()
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
                // P7: a held nav direction (a camera stick, a console LS arrow) stops at the toggle.
                o.extend(self.nav_flush());
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
            o.extend(self.north_capture());
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
    fn guide_double_enabled(&self) -> bool {
        self.global.enabled
            && self.global.controller.enabled
            && !self.console()
            && self.global.controller.guide_double_press_ms > 0
            && !self.global.controller.guide_double_press_action.is_empty()
            && self.global.controller.guide_double_press_action != "none"
    }
    fn deliver_guide_single(&mut self, single: GuideSingle, out: &mut Output) {
        match single {
            GuideSingle::Action(action) => self.fire(&action, out),
            GuideSingle::Room(action) => self.room_fire(&action, out),
            GuideSingle::Raw(code) => out.app.extend([
                Event::new(EV_KEY, code, 1),
                Event::new(EV_KEY, code, 0),
                Event::new(EV_SYN, SYN_REPORT, 0),
            ]),
            GuideSingle::None => {}
        }
    }
    fn finish_guide_single(&mut self, single: GuideSingle, now: u64, out: &mut Output) {
        let clean = self.guide_clean.is_some_and(|(at, elapsed)| {
            now.saturating_sub(at) < self.global.controller.capture_hold_ms
                && elapsed < self.global.controller.capture_hold_ms as f64
        });
        if !self.guide_double_enabled() || !clean || self.repairing {
            self.deliver_guide_single(single, out);
            return;
        }
        if self.guide_single.take().is_some_and(|pending| {
            now.saturating_sub(pending.at) < self.global.controller.guide_double_press_ms
                && pending.elapsed < self.global.controller.guide_double_press_ms as f64
        }) {
            out.actions
                .push(self.global.controller.guide_double_press_action.clone());
        } else {
            self.guide_single = Some(GuidePending {
                at: now,
                elapsed: 0.0,
                single,
            });
        }
    }
    fn guide_motion(&self, e: Event) -> bool {
        if e.kind != EV_ABS {
            return false;
        }
        if e.code == 16 || e.code == 17 {
            return e.value != 0;
        }
        let Some(axis) = self.axes.get(&e.code) else {
            return false;
        };
        let mut axis = *axis;
        axis.value = e.value;
        if e.code == 2 || e.code == 5 {
            axis.trigger() > self.legacy.trigger_threshold
        } else {
            axis.centered().abs() > self.global.controller.stick_deadzone
        }
    }
    pub fn event(&mut self, e: Event, now: u64) -> Output {
        let mut out = Output::default();
        if self.repairing
            || !self.guide_double_enabled()
            || (e.kind == EV_SYN && e.code == SYN_DROPPED)
        {
            self.guide_clean = None;
            self.guide_single = None;
        }
        if self.guide_single.as_ref().is_some_and(|pending| {
            now.saturating_sub(pending.at) >= self.global.controller.guide_double_press_ms
        }) {
            let pending = self.guide_single.take().unwrap();
            self.deliver_guide_single(pending.single, &mut out);
        }
        let guide = button_code(&self.global.controller.guide_button).unwrap();
        if self.repairing {
            self.guide_clean = None;
            self.guide_single = None;
        }
        if e.kind == EV_KEY && e.code == guide && e.value == 1 && !self.pressed.contains(&guide) {
            self.guide_clean = self.pressed.is_empty().then_some((now, 0.0));
        } else if (e.kind == EV_KEY && e.code != guide && e.value != 0) || self.guide_motion(e) {
            self.guide_clean = None;
            self.guide_single = None;
        }
        out.extend(self.event_inner(e, now));
        if e.kind == EV_KEY && e.code == guide && e.value == 0 {
            self.guide_clean = None;
        }
        out
    }
    fn event_inner(&mut self, e: Event, now: u64) -> Output {
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
        // After the hold qualifies, input stays drained until the physical chord has fully released.
        if self.escape_fired && e.kind != EV_KEY {
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
            if !self.escape_fired && !self.legacy.toggle_chord.iter().all(|b| self.button(b)) {
                self.escape_elapsed = 0.0;
            }
            // A qualified return chord first drains every app/pointer output. Keep ownership until BOTH physical
            // buttons are up: a held thumbstick must never become a room click during the asynchronous handoff.
            if self.escape_fired {
                if !self.legacy.toggle_chord.iter().any(|b| self.button(b)) {
                    self.escape_fired = false;
                    self.escape_elapsed = 0.0;
                    if !self.repairing {
                        o.extend(self.release_window());
                    }
                }
                return o;
            }
            if self.shell_overlay {
                if e.value == 0 && self.suppressed.remove(&e.code) {
                    return o;
                }
                if self.suppressed.contains(&e.code) || self.blocked.contains(&e.code) {
                    return o;
                }
                let guide = button_code(&self.global.controller.guide_button).unwrap();
                if e.code == guide {
                    if e.value == 1 && !already {
                        self.guide_tap = (self.pressed.len() == 1).then_some((now, 0.0));
                    } else if e.value == 0
                        && !self.repairing
                        && self.guide_tap.take().is_some_and(|(at, _)| {
                            now.saturating_sub(at) < self.global.controller.capture_hold_ms
                        })
                    {
                        let single = match self.nav_value(Input::Button(guide)) {
                            Some(Value::Press(action)) => GuideSingle::Action(action),
                            _ => GuideSingle::None,
                        };
                        self.finish_guide_single(single, now, &mut o);
                    }
                } else {
                    if e.value == 1 {
                        self.guide_tap = None;
                    }
                    let toggle = self.toggle_codes();
                    if e.value == 1
                        && toggle.contains(&e.code)
                        && toggle.iter().all(|code| self.pressed.contains(code))
                    {
                        o.extend(self.nav_flush());
                        self.suppressed.extend(toggle);
                        if let Some(Value::Press(action)) = self.nav_value(Input::ToggleChord) {
                            self.fire(&action, &mut o);
                        }
                    } else {
                        self.nav_button(e, &mut o);
                    }
                }
                return o;
            }
            let toggle_codes: Vec<u16> = self
                .legacy
                .toggle_chord
                .iter()
                .map(|n| button_code(n).unwrap())
                .collect();
            // C-13: Guide + a toggle-chord button fires its system-layer action on release, and only if the other
            // chord button never joined (Guide+L3+R3 = neither lock nor the VM latch).
            if e.value == 0 && self.guide_thumb == Some(e.code) {
                self.guide_thumb = None;
                if !self.guide_thumb_cancel && !self.repairing {
                    if let Some(action) = self.system_action(e.code) {
                        o.actions.push(action);
                    }
                }
            }
            if !toggle_codes.iter().any(|c| self.pressed.contains(c)) {
                self.guide_thumb_cancel = false;
                self.nav.thumbs_cancelled = false;
            }
            // R-23: any overlap of the toggle-chord buttons cancels both single clicks, fast (room toggle) or slow.
            if e.value == 1
                && toggle_codes.contains(&e.code)
                && toggle_codes.iter().all(|c| self.pressed.contains(c))
            {
                self.nav.thumbs_cancelled = true;
                // R-23 and the chord ruling: any L3+R3 overlap cancels both singles and every held nav direction.
                o.extend(self.nav_flush());
                if self.guide_thumb.is_some() {
                    self.guide_thumb = None;
                    self.guide_thumb_cancel = true;
                }
            }
            if e.code == button_code(&self.legacy.fullscreen_button).unwrap()
                && e.value == 0
                && self.north_pending
            {
                self.north_pending = false;
                self.suppressed.remove(&e.code);
                return o;
            }
            // Room table: a pending tap resolves on its own release (before any suppression swallows it).
            if e.value == 0 {
                if let Some(p) = self.room_pending.remove(&e.code) {
                    self.suppressed.remove(&e.code);
                    if !p.fired && !p.cancelled && self.room() {
                        self.room_fire(&p.tap, &mut o);
                    }
                    return o;
                }
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
            // I3: on a VT no compositor runs: no system layer behind Guide (R-08), no capture, no room, no ShaderGlass;
            // every button is a nav input and the toggle chord fires the console profile's TOGGLE_CHORD.
            let console = self.console();
            let half = self.half_capture();
            // The captured app owns Guide and every button (full and half capture alike). Only the
            // mode_override_chord (half-capture toggle), Start in half-capture and the held toggle_chord are host input.
            let app_guide = owned
                && (!self.global.controller.captured_system_layer || self.captured_navigation());
            if self.captured_navigation()
                && !guide_held
                && e.value == 1
                && !already
                && e.code == button_code(&self.legacy.fullscreen_button).unwrap()
            {
                o.extend(self.north_capture());
                return o;
            }
            if half
                && e.value == 1
                && !already
                && e.code == button_code(&self.global.controller.cursor_mode_button).unwrap()
            {
                o.extend(self.release_outputs());
                self.suppressed.insert(e.code);
                self.pending_guide = false;
                self.cursor = false;
                o.extend(self.nav_flush());
                o.actions.push("input:cursor off".into());
                return o;
            }
            if app_guide {
                let escape: Vec<_> = self
                    .global
                    .controller
                    .mode_override_chord
                    .iter()
                    .map(|v| button_code(v).unwrap())
                    .collect();
                if e.value == 1
                    && !already
                    && escape.contains(&e.code)
                    && escape.iter().all(|c| self.pressed.contains(c))
                {
                    o.extend(self.release_outputs());
                    self.suppressed.extend(&escape);
                    self.pending_guide = false;
                    self.cursor = !self.cursor;
                    o.extend(self.nav_flush());
                    o.actions.push(format!(
                        "input:cursor {}",
                        if self.cursor { "on" } else { "off" }
                    ));
                    return o;
                }
                if e.code == guide && e.value == 1 {
                    self.pending_guide = true;
                    if self.captured_navigation() {
                        o.extend(self.nav_flush());
                        self.guide_tap = (self.pressed.len() == 1).then_some((now, 0.0));
                    }
                    return o;
                }
                if self.pending_guide && e.code != guide && e.value == 1 {
                    self.pending_guide = false;
                    if self.captured_navigation() {
                        self.guide_tap = None;
                    } else {
                        self.app_held.insert(guide);
                        o.app.push(Event::new(EV_KEY, guide, 1));
                    }
                }
                if e.code == guide && e.value == 0 && self.pending_guide {
                    self.pending_guide = false;
                    if self.captured_navigation() {
                        if self.guide_tap.take().is_some_and(|(at, _)| {
                            now.saturating_sub(at) < self.global.controller.capture_hold_ms
                        }) && !self.repairing
                        {
                            let single = match self.nav_value(Input::Button(guide)) {
                                Some(Value::Press(action)) => GuideSingle::Action(action),
                                _ => GuideSingle::None,
                            };
                            self.finish_guide_single(single, now, &mut o);
                        }
                    } else {
                        self.finish_guide_single(GuideSingle::Raw(guide), now, &mut o);
                    }
                    return o;
                }
            }
            if !app_guide && e.code == guide {
                if e.value == 1 && console {
                    // R-08: Guide hold is no modifier on a VT; a press still cancels nav timers, alone it may be a tap.
                    o.extend(self.nav_flush());
                    self.guide_tap = (self.pressed.len() == 1).then_some((now, 0.0));
                } else if e.value == 1 {
                    o.extend(self.release_outputs());
                    self.suppressed
                        .extend(self.pressed.iter().filter(|c| **c != guide));
                    // INV-6/R-26: a Guide press cancels pending nav timers; alone, it may become the Guide tap.
                    // A Guide press also cancels pending room taps/holds (INV-6).
                    o.extend(self.nav_flush());
                    for p in self.room_pending.values_mut() {
                        p.cancelled = true;
                    }
                    if !already {
                        self.guide_tap = (self.pressed.len() == 1).then_some((now, 0.0));
                    }
                } else if e.value == 0 && !self.repairing {
                    // The layer gave the pad back: a captured app (captured_system_layer) got rest at the Guide press
                    // and nothing while the layer held it, so it gets the axes' live values again now.
                    if owned {
                        let live: Vec<_> = self
                            .axes
                            .iter()
                            .filter(|(c, _)| self.axis_sink(**c) == Sink::App)
                            .map(|(c, a)| Event::new(EV_ABS, *c, a.value))
                            .collect();
                        if !live.is_empty() {
                            o.app.extend(live);
                            o.app.push(Event::new(EV_SYN, SYN_REPORT, 0));
                        }
                    }
                    // A tap is short on the tick clock (the live loop) and on the event clock (replays without ticks).
                    let hold = self.global.controller.capture_hold_ms;
                    if self
                        .guide_tap
                        .take()
                        .is_some_and(|(at, _)| now.saturating_sub(at) < hold)
                    {
                        let single = if self.nav_active() {
                            match self.nav_value(Input::Button(guide)) {
                                Some(Value::Press(action)) => GuideSingle::Action(action),
                                _ => GuideSingle::None,
                            }
                        } else if self.room() {
                            self.room_entry(guide)
                                .map(|value| GuideSingle::Room(value.parts().0.to_owned()))
                                .unwrap_or(GuideSingle::None)
                        } else {
                            GuideSingle::None
                        };
                        self.finish_guide_single(single, now, &mut o);
                    }
                }
                return o;
            }
            if console && guide_held && e.value == 1 && !already {
                self.guide_tap = None;
            }
            if !app_guide && guide_held && !console {
                if e.value == 1 && !already {
                    self.guide_tap = None;
                }
                if e.value == 1
                    && !already
                    && toggle_codes.contains(&e.code)
                    && self.system_action(e.code).is_some()
                {
                    if !self.guide_thumb_cancel {
                        if self.guide_thumb.is_some() {
                            self.guide_thumb = None;
                            self.guide_thumb_cancel = true;
                        } else {
                            self.guide_thumb = Some(e.code);
                        }
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
                    if override_codes.contains(&e.code)
                        && override_codes.iter().all(|c| self.pressed.contains(c))
                        && self.latched.insert("mode-override".into())
                    {
                        o.extend(self.release_outputs());
                        self.cursor = !self.cursor;
                        o.extend(self.nav_flush());
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
                if self.room() {
                    // R-23: any overlap of the toggle_chord buttons cancels both room singles (L3 sprint, R3 recenter).
                    let toggle = self.toggle_codes();
                    if toggle.contains(&e.code)
                        && toggle
                            .iter()
                            .any(|c| *c != e.code && self.pressed.contains(c))
                    {
                        for c in &toggle {
                            if let Some(p) = self.room_pending.get_mut(c) {
                                p.cancelled = true;
                            }
                        }
                    }
                }
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
                        if console {
                            // C-17: the console profile's chord action (mios:graphical-vt); a VT has no room.
                            if let Some(Value::Press(a)) = self.nav_value(Input::ToggleChord) {
                                self.fire(&a, &mut o);
                            }
                        } else {
                            o.actions.push("mios:hyprland-3d-toggle".into());
                        }
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
                // The room table (spec P1) and the host (legacy) actions -- ShaderGlass, cursor mode, North capture,
                // room buttons -- belong to an uncaptured controller only. A captured app keeps every one of these
                // buttons, half-capture and a room capture included.
                if !owned && !console && self.room() && self.room_press(e.code, &mut o) {
                    self.suppressed.insert(e.code);
                    return o;
                }
                if !owned && !console && self.legacy_action(e.code, now, &mut o) {
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
                    if self.nav_active() {
                        self.nav_button(e, &mut o);
                    }
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
            let sink = if self.mutant_room_forward && sink == Sink::Room {
                Sink::App
            } else {
                sink
            };
            // While the Guide system layer holds the pad (captured_system_layer = true lets it reach a captured app),
            // the HAT, sticks and analog triggers are layer input (one action per press/flick): the app does not
            // also receive them, exactly as it does not receive Guide + a button (D2).
            if sink == Sink::App
                && (self.app_owned() || !self.button(&self.global.controller.guide_button))
                && !self.system_layer_held()
            {
                o.app.push(e);
            }
            self.nav_axis(e.code, sink == Sink::Nav, &mut o);
        } else if e.kind == EV_SYN && e.code == SYN_REPORT {
            self.update_pad();
            if self.nav_active() && !self.guide_layer() {
                self.nav_flicks(&mut o);
            } else if self.system_layer_held() {
                self.layer_flicks(&mut o);
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
        let dt = if dt.is_finite() {
            dt.clamp(0.0, 0.1)
        } else {
            0.0
        };
        // task3773: the focus-gesture tracker's monotonic clock (ms).
        self.nav.focus.now_ms += dt * 1000.0;
        if self.capture_override.is_some() {
            // C-03: a capture/release the compositor never confirmed must not keep the pad (or the room) forever.
            self.override_ms += dt * 1000.0;
            if self.override_ms >= self.global.controller.context_stale_ms as f64 {
                o.extend(self.ownership_failed());
            }
        }
        if let Some((_, elapsed)) = &mut self.guide_clean {
            *elapsed += dt * 1000.0;
            if *elapsed >= self.global.controller.capture_hold_ms as f64 {
                self.guide_clean = None;
                self.guide_single = None;
            }
        }
        if let Some(pending) = &mut self.guide_single {
            pending.elapsed += dt * 1000.0;
            if pending.elapsed >= self.global.controller.guide_double_press_ms as f64 {
                let pending = self.guide_single.take().unwrap();
                self.deliver_guide_single(pending.single, &mut o);
            }
        }
        if self.shell_overlay {
            self.nav.focus.cancel();
            self.nav_timers(dt, &mut o);
            self.nav_wheel(dt, &mut o);
            self.pad.neutralize();
            return o;
        }
        if self.north_pending && !self.north_fired {
            self.north_elapsed += dt;
            if self.north_elapsed * 1000.0 >= self.global.controller.capture_hold_ms as f64 {
                self.north_fired = true;
                // Ownership already changed on press. Do not reset held app controls or the room return path.
                o.actions.push("input:window fullscreen".into());
            }
        }
        if self.room() {
            // Room [tap, hold]: the hold fires once at capture_hold_ms (spec P1).
            let hold_ms = self.global.controller.capture_hold_ms as f64;
            let mut due = Vec::new();
            for p in self.room_pending.values_mut() {
                if let Some(hold) = p.hold.clone().filter(|_| !p.fired && !p.cancelled) {
                    p.elapsed += dt;
                    if p.elapsed * 1000.0 >= hold_ms {
                        p.fired = true;
                        due.push(hold);
                    }
                }
            }
            for a in due {
                self.room_fire(&a, &mut o);
            }
        }
        let held = self.legacy.toggle_chord.iter().all(|b| self.button(b));
        if self.app_owned() && held && !self.escape_fired {
            self.escape_elapsed += dt;
            if self.escape_elapsed * 1000.0 >= self.global.controller.capture_hold_ms as f64 {
                self.escape_fired = true;
                o.extend(self.release_outputs());
                o.extend(self.nav_flush());
                self.suppressed.extend(&self.pressed);
                self.pad.neutralize();
                for (code, axis) in &mut self.axes {
                    axis.value = axis.neutral(*code);
                }
            }
        } else if !held && !self.escape_fired {
            self.escape_elapsed = 0.0;
        }
        if self.escape_fired {
            return o;
        }
        if let Some((_, t)) = &mut self.guide_tap {
            *t += dt * 1000.0;
            if *t >= self.global.controller.capture_hold_ms as f64 {
                self.guide_tap = None;
            }
        }
        self.nav_timers(dt, &mut o);
        self.update_pad();
        // Neutral dwell and gesture deadlines must advance even when an idle
        // controller emits no SYN_REPORT. Event samples contribute no dwell.
        self.focus_gesture_sample(dt, &mut o);
        if !self.pointer_mode() {
            self.pointer_motion = PointerMotion::default();
            self.fraction_x = 0.0;
            self.fraction_y = 0.0;
            if self.nav_active() && !self.guide_layer() {
                self.nav_wheel(dt, &mut o);
            }
            return o;
        }
        // C-15b (I4): with Guide held the sticks are system-layer flicks: no pointer motion, wheel or clicks.
        if self.system_layer_held() {
            return o;
        }
        let ControllerConfig {
            stick_deadzone,
            pointer_max_speed_px,
            pointer_max_speed_px_s,
            pointer_response_exponent,
            pointer_smoothing_ms,
            pointer_hz,
            scroll_sensitivity,
            ..
        } = self.global.controller;
        let (rx, ry) = self.right_axes();
        let (x, y) = self.stick(rx, ry, stick_deadzone, 1.0);
        let travel = self.pointer_motion.advance(
            (x, y),
            dt,
            pointer_max_speed_px_s.unwrap_or(pointer_max_speed_px * f64::from(pointer_hz)),
            pointer_response_exponent.unwrap_or(1.0),
            pointer_smoothing_ms / 1000.0,
        );
        if x == 0.0 && y == 0.0 {
            self.fraction_x = 0.0;
            self.fraction_y = 0.0;
        }
        self.fraction_x += travel[0];
        self.fraction_y += travel[1];
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
        self.global
            .controller
            .system_layer
            .iter()
            .find(|(name, _)| system_layer_code(name).ok() == Some(code))
            .map(|(_, a)| a.clone())
    }
    /// The shell surface, captured navigation consumer or ordinary window's profile; None for raw app/room input.
    pub fn nav_profile(&self) -> Option<String> {
        self.nav_target().0
    }
    /// P4: (profile, the app whose layer selected it). The shell overlay and the room keep their own rules; otherwise a
    /// verified app refines the base context (the captured terminal's navigation context, or the window's own).
    fn nav_target(&self) -> (Option<String>, Option<String>) {
        let nav = &self.global.controller.nav;
        if self.shell_overlay {
            return (nav.profile_name("shell", false), None);
        }
        if self.room() {
            return (None, None);
        }
        let base = if self.app_owned() {
            match self.capture_navigation_context.as_deref() {
                Some(c) => c,
                None => return (None, None),
            }
        } else {
            &self.context
        };
        if let Some(app) = &self.app {
            if let Some(p) = nav.app_profile(app, base, self.tmux) {
                return (Some(p), Some(app.clone()));
            }
        }
        (nav.profile_name(base, self.tmux), None)
    }
    pub fn nav_active(&self) -> bool {
        self.nav.profile.is_some()
            && (self.shell_overlay
                || ((!self.app_owned() || self.captured_navigation()) && !self.room()))
    }
    fn nav_value(&self, input: Input) -> Option<Value> {
        let p = self.nav.profile.as_ref()?;
        self.global
            .controller
            .nav
            .profiles
            .get(p)?
            .get(&input)
            .cloned()
    }
    /// The Linux console (I3): no compositor runs, so nothing behind it can capture, toggle the room or hold Guide.
    pub fn console(&self) -> bool {
        self.context == "console"
    }
    /// Guide held as the system-layer modifier (never on a VT, R-08).
    fn guide_layer(&self) -> bool {
        !self.shell_overlay && !self.console() && self.button(&self.global.controller.guide_button)
    }
    /// The Guide system layer is live: Guide held, a compositor runs, and no captured app owns Guide (unless the SSOT
    /// sets captured_system_layer). Its actions come from [controller.system_layer], keyed by button or LS_*/RS_*.
    pub fn system_layer_held(&self) -> bool {
        self.guide_layer()
            && !self.captured_navigation()
            && !(self.app_owned() && !self.global.controller.captured_system_layer)
    }
    /// C-15b (I4): one system-layer action per stick flick (LS_*/RS_* pseudo-keys), re-armed below flick_off. A stick
    /// already deflected when Guide went down is not armed (the Guide press flushed nav with the current deflection).
    fn layer_flicks(&mut self, o: &mut Output) {
        let (on, off) = (
            self.global.controller.nav.flick_on,
            self.global.controller.nav.flick_off,
        );
        for (i, right) in [(0usize, false), (1, true)] {
            let (xa, ya) = self.stick_axes(right);
            let (x, y) = self.stick(xa, ya, self.global.controller.stick_deadzone, 1.0);
            let mag = x.hypot(y);
            if mag < off {
                self.nav.armed[i] = true;
                self.nav.axis_armed[i] = [true; 2];
                continue;
            }
            if !self.nav.armed[i] || mag < on {
                continue;
            }
            self.nav.armed[i] = false;
            // A layer flick also disarms a camera profile's half-axes: releasing Guide never turns it into a pan.
            self.nav.axis_armed[i] = [false; 2];
            let dir = if x.abs() >= y.abs() {
                if x > 0.0 {
                    3
                } else {
                    2
                }
            } else if y > 0.0 {
                1
            } else {
                0
            };
            let code = mios_resolver::keybindings::STICK_PSEUDO + if right { 4 } else { 0 } + dir;
            // Any flick makes the Guide press a modifier, never a Guide tap (drawer).
            self.guide_tap = None;
            if let Some(a) = self.system_action(code) {
                o.actions.push(a);
            }
        }
    }
    fn fire(&mut self, action: &str, o: &mut Output) {
        if action == "none" {
            return;
        }
        if action == crate::nav::CURSOR {
            // I3: cursor mode from a nav value (the VT has no compositor-side Start toggle): the sticks drive the
            // virtual pointer (the gpm pointer on a VT), RT/LT click, every other button keeps its nav meaning.
            o.extend(self.release_outputs());
            self.cursor = !self.cursor;
            // P7: toggling cursor mode stops every held nav direction (the sticks become the pointer, or come back).
            o.extend(self.nav_flush());
            o.actions.push(format!(
                "input:cursor {}",
                if self.cursor { "on" } else { "off" }
            ));
            return;
        }
        o.actions.push(action.to_owned());
    }
    /// INV-6: cancel every tap/hold timer and auto-repeat, and forget held nav inputs (their release fires nothing).
    /// Key output is atomic strokes, so no virtual key is ever left down. A stick (in a camera profile: each of its
    /// half-axes) still at or past flick_off must fall below it before it can fire again.
    fn nav_flush(&mut self) -> Output {
        self.nav.focus.cancel();
        self.nav.held.clear();
        self.nav.stick_dir = [None; 2];
        self.nav.axis_dir = [[None; 2]; 2];
        self.nav.wheel = 0.0;
        self.nav.hwheel = 0.0;
        let (lx, ly) = self.stick(0, 1, self.global.controller.stick_deadzone, 1.0);
        let (rx, ry) = self.right_axes();
        let (rx, ry) = self.stick(rx, ry, self.global.controller.stick_deadzone, 1.0);
        let off = self.global.controller.nav.flick_off;
        self.nav.armed = [lx.hypot(ly) < off, rx.hypot(ry) < off];
        self.nav.axis_armed = [
            [lx.abs() < off, ly.abs() < off],
            [rx.abs() < off, ry.abs() < off],
        ];
        Output::default()
    }
    /// Follow the current profile; a change flushes nav state, and moving between vpad and nav releases the pad.
    fn nav_sync(&mut self) -> Output {
        let (want, app) = self.nav_target();
        if want == self.nav.profile && app == self.nav.app {
            return Output::default();
        }
        let crossing = want.is_some() != self.nav.profile.is_some();
        let mut o = self.nav_flush();
        if crossing && (!self.app_owned() || self.captured_navigation()) {
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
            o.app.extend(app);
        }
        self.nav.profile = want;
        self.nav.app = app;
        o
    }
    /// P5: a camera app's own repeat row (the only place a right-stick flick repeats), else [nav.repeat].
    fn repeat_for(&self, input: Input) -> Option<(u64, u64)> {
        let nav = &self.global.controller.nav;
        if let Some(app) = self
            .nav
            .app
            .as_ref()
            .and_then(|a| nav.apps.get(a))
            .filter(|a| a.camera)
        {
            if let Some(r) = app.repeat.get(&input) {
                return Some(*r);
            }
        }
        nav.repeat.get(&input).copied()
    }
    fn nav_press(&mut self, input: Input, o: &mut Output) {
        let thumb = matches!(input, Input::Button(0x13d | 0x13e));
        if thumb && (self.nav.thumbs_cancelled || self.thumb_stick_deflected(input)) {
            return;
        }
        let Some(value) = self.nav_value(input) else {
            return;
        };
        let repeat = match &value {
            Value::Press(a) if !thumb => {
                let a = a.clone();
                self.fire(&a, o);
                self.repeat_for(input)
            }
            _ => None,
        };
        let next = repeat.map_or(0.0, |(d, _)| d as f64);
        self.nav.held.insert(
            input,
            Held {
                value,
                elapsed: 0.0,
                fired: false,
                next,
                repeat,
                deferred: thumb,
            },
        );
    }
    fn nav_release(&mut self, input: Input, o: &mut Output) {
        let Some(h) = self.nav.held.remove(&input) else {
            return;
        };
        if self.repairing || (h.deferred && self.thumb_stick_deflected(input)) {
            return;
        }
        match &h.value {
            Value::TapHold(t, _) if !h.fired => self.fire(t, o),
            Value::Press(a) if h.deferred => self.fire(a, o),
            _ => {}
        }
    }
    /// R-23: an L3 or R3 click while its own stick is outside stick_deadzone is a sloppy click, never an action.
    fn thumb_stick_deflected(&self, input: Input) -> bool {
        let (x, y) = if input == Input::Button(0x13d) {
            (0, 1)
        } else {
            self.right_axes()
        };
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
        let guide = self.guide_layer();
        if code == 16 || code == 17 {
            let v = self.axes.get(&code).map_or(0, |a| a.value.signum());
            let old = if code == 16 {
                self.nav.hat.0
            } else {
                self.nav.hat.1
            };
            if code == 16 {
                self.nav.hat.0 = v
            } else {
                self.nav.hat.1 = v
            }
            if v == old {
                return;
            }
            let button = |v: i32| match (code, v) {
                (16, -1) => Some(BTN_DPAD_LEFT),
                (16, 1) => Some(BTN_DPAD_RIGHT),
                (17, -1) => Some(BTN_DPAD_UP),
                (17, 1) => Some(BTN_DPAD_DOWN),
                _ => None,
            };
            // C-15b (I4): under Guide the HAT is the system layer's BTN_DPAD_* (one action per press, no repeat), so
            // a HAT pad (DualSense, xpad) focuses windows exactly like a BTN_DPAD pad.
            if self.system_layer_held() {
                if let Some(a) = button(v).and_then(|b| self.system_action(b)) {
                    o.actions.push(a);
                    self.guide_tap = None;
                }
                return;
            }
            if !nav || guide {
                return;
            }
            if let Some(b) = button(old) {
                self.nav_release(Input::Button(b), o);
            }
            if let Some(b) = button(v) {
                self.nav_press(Input::Button(b), o);
            }
            return;
        }
        let (rx, ry) = self.right_axes();
        let triggers = [
            (axis_code(&self.legacy.resize_axis).unwrap(), 0x138u16),
            (axis_code(&self.legacy.grab_axis).unwrap(), 0x139),
        ];
        for (i, (a, button)) in triggers.into_iter().enumerate() {
            if a != code || a == rx || a == ry {
                continue;
            }
            let t = self.axes.get(&a).map_or(0.0, Axis::trigger);
            let on = if self.nav.triggers[i] {
                t >= (self.legacy.trigger_threshold - 0.1).max(0.05)
            } else {
                t >= self.legacy.trigger_threshold
            };
            if on == self.nav.triggers[i] {
                return;
            }
            self.nav.triggers[i] = on;
            // C-15b (I4): a pad without BTN_TL2/TR2 (xpad) reaches the system layer's LT/RT through the same edge.
            if self.analog_triggers && self.system_layer_held() {
                if on {
                    if let Some(a) = self.system_action(button) {
                        o.actions.push(a);
                        self.guide_tap = None;
                    }
                }
                return;
            }
            if nav && self.analog_triggers && !guide {
                if on {
                    self.nav_press(Input::Button(button), o)
                } else {
                    self.nav_release(Input::Button(button), o)
                }
            }
            return;
        }
    }
    fn stick_axes(&self, right: bool) -> (u16, u16) {
        if right {
            self.right_axes()
        } else {
            (0, 1)
        }
    }
    fn is_pointer(&self, input: Input) -> bool {
        matches!(self.nav_value(input), Some(Value::Press(a)) if a.starts_with("pointer:"))
    }
    /// P5: a camera profile's sticks. After radial() (stick_deadzone), each stick's x and y are independent half-axes:
    /// a component at or past flick_on presses Right/Down (positive) or Left/Up (negative) and fires at once, it
    /// repeats while held (repeat_for), and falling below flick_off releases it, firing nothing. Both axes may be held
    /// (a diagonal), and rolling the stick releases one direction and presses the next without centring. P7: only a
    /// stick routed to navigation projects camera keys; a stick the pointer owns (cursor mode) holds nothing and
    /// re-arms only below flick_off.
    fn camera_flicks(&mut self, o: &mut Output) {
        let (on, off) = (
            self.global.controller.nav.flick_on,
            self.global.controller.nav.flick_off,
        );
        for (i, right) in [(0usize, false), (1, true)] {
            let (xa, ya) = self.stick_axes(right);
            let (x, y) = self.stick(xa, ya, self.global.controller.stick_deadzone, 1.0);
            let routed = self.axis_sink(xa) == Sink::Nav;
            for (j, v) in [x, y].into_iter().enumerate() {
                if !routed {
                    if let Some(dir) = self.nav.axis_dir[i][j].take() {
                        self.nav.held.remove(&Input::Stick { right, dir });
                    }
                    self.nav.axis_armed[i][j] = v.abs() < off;
                    continue;
                }
                let dir = match (j, v > 0.0) {
                    (0, true) => Dir::Right,
                    (0, false) => Dir::Left,
                    (_, true) => Dir::Down,
                    (_, false) => Dir::Up,
                };
                // Each half-axis is its own input: a sign change between two samples (the other half was below
                // flick_off) releases the held direction and may press the opposite one at once.
                let flipped = self.nav.axis_dir[i][j].is_some_and(|d| d != dir);
                if v.abs() < off || flipped {
                    if let Some(held) = self.nav.axis_dir[i][j].take() {
                        self.nav_release(Input::Stick { right, dir: held }, o);
                    }
                    self.nav.axis_armed[i][j] = true;
                    if v.abs() < off {
                        continue;
                    }
                }
                if self.nav.axis_dir[i][j].is_some() || !self.nav.axis_armed[i][j] || v.abs() < on {
                    continue;
                }
                self.nav.axis_armed[i][j] = false;
                self.nav.axis_dir[i][j] = Some(dir);
                self.nav_press(Input::Stick { right, dir }, o);
            }
        }
    }
    /// Discrete stick directions: one flick per deflection past flick_on; the stick re-arms below flick_off.
    fn nav_flicks(&mut self, o: &mut Output) {
        if self.camera() {
            return self.camera_flicks(o);
        }
        let (on, off) = (
            self.global.controller.nav.flick_on,
            self.global.controller.nav.flick_off,
        );
        for (i, right) in [(0usize, false), (1, true)] {
            let (xa, ya) = self.stick_axes(right);
            let (x, y) = self.stick(xa, ya, self.global.controller.stick_deadzone, 1.0);
            let mag = x.hypot(y);
            if self.axis_sink(xa) != Sink::Nav {
                // Cursor/half-capture owns this stick: pointer motion cannot also focus a window.
                if right {
                    self.nav.focus.cancel();
                }
                if let Some(dir) = self.nav.stick_dir[i].take() {
                    self.nav.held.remove(&Input::Stick { right, dir });
                }
                self.nav.armed[i] = mag < off;
                continue;
            }
            // task3773: with a focus_gesture policy, a RIGHT stick whose dominant
            // direction resolves to a window/pane focus binding qualifies only
            // through the deliberate out-and-return gesture machine below. Window
            // move bindings, wheels, `none`, the left stick and a policy-free
            // configuration all keep the legacy magnitude flicks unchanged.
            if right && self.focus_gesture_sample(0.0, o) {
                self.nav.armed[i] = mag < off;
                continue;
            }
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
            let dir = if x.abs() >= y.abs() {
                if x > 0.0 {
                    Dir::Right
                } else {
                    Dir::Left
                }
            } else if y > 0.0 {
                Dir::Down
            } else {
                Dir::Up
            };
            let input = Input::Stick { right, dir };
            if self.is_pointer(input) {
                continue;
            }
            self.nav.armed[i] = false;
            self.nav.stick_dir[i] = Some(dir);
            self.nav_press(input, o);
        }
    }
    /// A right-stick binding qualifies for the focus gesture only when it moves
    /// window/pane focus: `tmux:focus *` on a tmux profile, `hypr:movefocus *` on a
    /// plain desktop profile. Moving a window, pointer wheels, menus and `none` do not.
    fn is_focus_action(&self, input: Input) -> bool {
        matches!(self.nav_value(input), Some(Value::Press(a))
            if a.starts_with("tmux:focus ") || a.starts_with("hypr:movefocus "))
    }

    /// Sample only the qualified right-stick focus lane. Other bindings keep
    /// their existing event-driven flick/camera/pointer behavior.
    fn focus_gesture_sample(&mut self, dt: f64, o: &mut Output) -> bool {
        let Some(g) = self.global.controller.nav.focus_gesture.clone() else {
            self.nav.focus.cancel();
            return false;
        };
        let (xa, ya) = self.right_axes();
        if !self.nav_active()
            || self.guide_layer()
            || self.camera()
            || self.axis_sink(xa) != Sink::Nav
        {
            self.nav.focus.cancel();
            return false;
        }
        let any_focus = [Dir::Left, Dir::Right, Dir::Up, Dir::Down]
            .into_iter()
            .any(|dir| self.is_focus_action(Input::Stick { right: true, dir }));
        if !any_focus {
            self.nav.focus.cancel();
            return false;
        }
        let (x, y) = self.stick(xa, ya, self.global.controller.stick_deadzone, 1.0);
        let dominant = if x.abs() >= y.abs() {
            if x > 0.0 {
                Dir::Right
            } else {
                Dir::Left
            }
        } else if y > 0.0 {
            Dir::Down
        } else {
            Dir::Up
        };
        let focus = self.is_focus_action(Input::Stick {
            right: true,
            dir: dominant,
        });
        if x.hypot(y) >= g.neutral_pct && !focus {
            self.nav.focus.cancel();
            return false;
        }
        self.focus_gesture(&g, x, y, dt, o);
        focus
    }

    /// One deliberate out-and-return focus gesture (task3773). Phases: a continuous
    /// neutral dwell; a rise to the peak inside its window locked to a cardinal cone; a
    /// return to neutral inside its window; then exactly ONE focus action and a cooldown.
    /// Slow ramps, sustained holds, jitter, partial travel, rapid bounces, circular
    /// sweeps and held diagonals never fire. Every INV-6 flush cancels a pending gesture
    /// through `nav_flush`; a button/key RELEASE never becomes an action (the atomic
    /// press+release here is the gesture completing, not a release firing).
    fn focus_gesture(&mut self, g: &FocusGesture, x: f64, y: f64, dt: f64, o: &mut Output) {
        let mag = x.hypot(y);
        let now = self.nav.focus.now_ms;
        if mag < g.neutral_pct {
            if let Some((at, dir)) = self.nav.focus.peak.take() {
                if now - at <= g.return_ms as f64 {
                    let input = Input::Stick { right: true, dir };
                    self.nav.armed[1] = false;
                    self.nav_press(input, o);
                    self.nav_release(input, o);
                    self.nav.focus.cooldown_until = now + g.cooldown_ms as f64;
                }
                // Too slow back to neutral, or fired: either way a fresh dwell is required.
                self.nav.focus.cancel();
                return;
            }
            if self.nav.focus.rising_since.take().is_some() {
                // Returned to neutral without ever peaking: partial travel, restart the dwell.
                self.nav.focus.dwell_ms = 0.0;
                self.nav.focus.armed = false;
            }
            self.nav.focus.dwell_ms += dt * 1000.0;
            if self.nav.focus.dwell_ms >= g.neutral_ms as f64
                && now >= self.nav.focus.cooldown_until
            {
                self.nav.focus.armed = true;
            }
            return;
        }
        // Above neutral: preserve an in-progress rise across event and tick
        // samples. Arming is consumed only when the rise begins.
        self.nav.focus.dwell_ms = 0.0;
        if let Some((at, dir)) = self.nav.focus.peak {
            // A hold past the deadline or a sweep outside the locked cone
            // cancels the peak; neither may later become a return gesture.
            if now - at > g.return_ms as f64 || cardinal(x, y, g.direction_deg) != Some(dir) {
                self.nav.focus.cancel();
            }
            return;
        }
        if now < self.nav.focus.cooldown_until {
            // A cooldown bounce or an excursion without a completed dwell never rises.
            self.nav.focus.cancel();
            return;
        }
        let (since, dir) = if let Some(rise) = self.nav.focus.rising_since {
            rise
        } else if self.nav.focus.armed {
            self.nav.focus.armed = false;
            let Some(dir) = cardinal(x, y, g.direction_deg) else {
                self.nav.focus.cancel();
                return;
            };
            self.nav.focus.rising_since = Some((now, dir));
            (now, dir)
        } else {
            return;
        };
        if now - since > g.rise_ms as f64 || cardinal(x, y, g.direction_deg) != Some(dir) {
            self.nav.focus.cancel();
            return;
        }
        if mag >= g.rise_pct {
            self.nav.focus.rising_since = None;
            self.nav.focus.peak = Some((now, dir));
        }
    }

    /// Analog wheel for stick directions mapped to pointer: (the vertical and horizontal parts separately).
    fn nav_wheel(&mut self, dt: f64, o: &mut Output) {
        let sens = self.global.controller.scroll_sensitivity;
        for right in [false, true] {
            let (xa, ya) = self.stick_axes(right);
            let (x, y) = self.stick(xa, ya, self.global.controller.stick_deadzone, 1.0);
            for (amount, dir) in [
                (-y, if y < 0.0 { Dir::Up } else { Dir::Down }),
                (x, if x > 0.0 { Dir::Right } else { Dir::Left }),
            ] {
                if amount == 0.0 {
                    continue;
                }
                let Some(Value::Press(a)) = self.nav_value(Input::Stick { right, dir }) else {
                    continue;
                };
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
            let cursor = a == crate::nav::CURSOR;
            self.fire(&a, o);
            if cursor {
                // P7: the toggle flushed every held input; nothing else of this tick fires after it.
                break;
            }
        }
    }
}
/// The cardinal direction whose cone (± `cone_deg`) contains the vector, if any.
/// Screen coordinates: x grows right, y grows down; the angle is measured from +x.
fn cardinal(x: f64, y: f64, cone_deg: u64) -> Option<Dir> {
    let cone = cone_deg.min(45) as f64 * std::f64::consts::PI / 180.0;
    let angle = y.atan2(x);
    [
        (0.0, Dir::Right),
        (std::f64::consts::PI, Dir::Left),
        (std::f64::consts::FRAC_PI_2, Dir::Down),
        (-std::f64::consts::FRAC_PI_2, Dir::Up),
    ]
    .into_iter()
    .find_map(|(center, dir)| {
        let mut delta = (angle - center).abs();
        if delta > std::f64::consts::PI {
            delta = 2.0 * std::f64::consts::PI - delta;
        }
        (delta <= cone).then_some(dir)
    })
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
    fn pointer_velocity_is_rate_independent_curved_and_quiet_at_center() {
        let mut totals = vec![];
        for hz in [50, 125, 250, 1000] {
            let mut r = router();
            r.cursor = true;
            r.global.controller.pointer_hz = hz;
            r.global.controller.pointer_max_speed_px_s = Some(1200.0);
            r.global.controller.pointer_response_exponent = Some(2.0);
            r.global.controller.pointer_smoothing_ms = 60.0;
            r.axes.get_mut(&3).unwrap().value = 32767;
            let mut total = 0;
            for _ in 0..hz {
                total += r
                    .tick(1.0 / f64::from(hz))
                    .pointer
                    .iter()
                    .filter(|e| e.kind == EV_REL && e.code == 0)
                    .map(|e| e.value)
                    .sum::<i32>();
            }
            assert!((1127..=1129).contains(&total), "{hz} Hz: {total}");
            totals.push(total);
            r.axes.get_mut(&3).unwrap().value = 1000;
            for _ in 0..hz {
                assert!(
                    r.tick(1.0 / f64::from(hz)).pointer.is_empty(),
                    "center must never glide"
                );
            }
        }
        assert!(totals.iter().all(|n| (n - totals[0]).abs() <= 1));
        let mut low = PointerMotion::default();
        let fine = low.advance((0.25, 0.0), 1.0, 1200.0, 2.0, 0.0);
        assert_eq!(
            fine,
            [75.0, 0.0],
            "quarter deflection is fine motion, not a linear jump"
        );
        let mut diagonal = PointerMotion::default();
        let d = diagonal.advance((0.5_f64.sqrt(), 0.5_f64.sqrt()), 1.0, 1200.0, 2.0, 0.0);
        assert!((d[0].hypot(d[1]) - 1200.0).abs() < 1e-6);
    }
    #[test]
    fn pointer_smoothing_avoids_steps_and_ownership_changes_clear_momentum() {
        let mut motion = PointerMotion::default();
        let initial = motion.advance((1.0, 0.0), 0.004, 1200.0, 2.0, 0.06);
        assert!(initial[0] > 0.0 && initial[0] < 0.2);
        let mut r = router();
        r.pointer_motion.velocity = [900.0, 300.0];
        r.fraction_x = 0.9;
        r.release_outputs();
        assert_eq!(r.pointer_motion.velocity, [0.0; 2]);
        assert_eq!(r.fraction_x, 0.0);
    }
    #[test]
    fn kernel_truth_repairs_releases_without_unlatching_held_chords() {
        let mut r = router();
        key(&mut r, 0x130, 1, 1);
        r.latched.insert("held-test".into());
        let held = BTreeSet::from([0x130]);
        let out = r.reconcile(&held, 2);
        assert!(out.actions.is_empty() && out.app.is_empty() && out.pointer.is_empty());
        assert!(r.latched.contains("held-test"));
        let out = r.reconcile(&BTreeSet::new(), 3);
        assert!(r.pressed.is_empty() && r.latched.is_empty() && r.chord_times.is_empty());
        assert!(out.actions.is_empty());
        assert!(out.app.contains(&Event::new(EV_KEY, 0x130, 0)));
        let next = key(&mut r, 0x130, 1, 4);
        assert!(next.app.contains(&Event::new(EV_KEY, 0x130, 1)));
    }
    #[test]
    fn kernel_truth_never_creates_a_guide_tap_or_a_new_press() {
        let mut r = router();
        r.global.controller.captured_system_layer = false;
        r.set_context("app-capture");
        key(&mut r, 0x13c, 1, 1);
        assert!(r.pending_guide);
        let out = r.reconcile(&BTreeSet::from([0x130]), 2);
        assert!(out.app.is_empty() && out.pointer.is_empty() && out.actions.is_empty());
        assert!(!r.pending_guide && r.pressed.is_empty());
        assert!(!r.app_held.contains(&0x130));
        r.suppressed.insert(0x131);
        r.blocked.insert(0x131);
        r.reconcile(&BTreeSet::new(), 3);
        assert!(r.suppressed.is_empty() && r.blocked.is_empty());
    }

    #[test]
    fn captured_application_owns_guide_combinations_by_default() {
        let mut r = router();
        r.global.controller.captured_system_layer = false;
        r.set_context("app-capture");
        let mut out = Output::default();
        for (c, v, t) in [(0x13c, 1, 1), (0x136, 1, 2), (0x136, 0, 3), (0x13c, 0, 4)] {
            out.extend(key(&mut r, c, v, t));
        }
        assert!(
            out.actions.is_empty(),
            "captured app must never switch workspace or fire system actions"
        );
        assert_eq!(
            out.app,
            vec![
                Event::new(EV_KEY, 0x13c, 1),
                Event::new(EV_KEY, 0x136, 1),
                Event::new(EV_KEY, 0x136, 0),
                Event::new(EV_KEY, 0x13c, 0)
            ]
        );
    }
    #[test]
    fn explicit_capture_escape_returns_the_host_cursor_without_leaking_keys() {
        let mut r = router();
        r.global.controller.captured_system_layer = false;
        r.set_context("app-capture");
        assert!(key(&mut r, 0x13c, 1, 1).app.is_empty());
        let out = key(&mut r, 0x13a, 1, 2);
        assert_eq!(out.actions, vec!["input:cursor on"]);
        assert!(!out.app.iter().any(|e| e.kind == EV_KEY));
        assert!(r.cursor);
    }
    #[test]
    fn host_cursor_and_drag_survive_terminal_to_desktop_focus_changes() {
        let mut r = router();
        r.set_context("terminal");
        key(&mut r, 0x13b, 1, 1);
        key(&mut r, 0x13b, 0, 2);
        key(&mut r, 0x138, 1, 3);
        assert!(r.cursor);
        let out = r.set_context("desktop-2d");
        assert!(r.cursor);
        assert!(
            out.pointer.is_empty(),
            "focus change must not release a drag"
        );
    }
    #[test]
    fn captured_window_identity_change_releases_previous_target() {
        let mut r = router();
        r.set_focus("app-capture", Some(("0xaaa".into(), 1)), false);
        key(&mut r, 0x130, 1, 1);
        let out = r.set_focus("app-capture", Some(("0xbbb".into(), 2)), false);
        assert!(out.app.contains(&Event::new(EV_KEY, 0x130, 0)));
        assert!(
            key(&mut r, 0x130, 2, 2).app.is_empty(),
            "a held key must not leak into the next window"
        );
    }
    #[test]
    fn selected_room_window_preserves_the_documented_thumbstick_return_chord() {
        let mut r = router();
        r.global.controller.captured_system_layer = false;
        r.set_focus("app-capture", Some(("0xabc".into(), 42)), true);
        key(&mut r, 0x13d, 1, 1);
        let out = key(&mut r, 0x13e, 1, 2);
        assert!(
            out.actions.is_empty(),
            "a short chord belongs to the captured application"
        );
        for _ in 0..4 {
            assert!(r.tick(0.1).actions.is_empty());
        }
        assert!(r.tick(0.1).actions.is_empty());
        assert!(
            r.app_owned(),
            "the held chord keeps ownership after draining"
        );
        assert!(key(&mut r, 0x13d, 0, 3).actions.is_empty());
        assert!(
            r.app_owned(),
            "one physical release cannot return to the room"
        );
        assert_eq!(key(&mut r, 0x13e, 0, 4).actions, vec!["input:window off"]);
        assert!(
            r.room(),
            "release returns ownership without closing the room"
        );
        // A captured window of any kind keeps both thumbstick buttons for a short press; only the hold releases.
        let mut game = router();
        game.set_focus("app-capture", Some(("0xgame".into(), 7)), false);
        key(&mut game, 0x13d, 1, 1);
        assert!(
            key(&mut game, 0x13e, 1, 2).actions.is_empty(),
            "a captured window keeps both thumbstick buttons for a short press"
        );
        let mut fired = vec![];
        for _ in 0..8 {
            fired.extend(game.tick(0.1).actions);
        }
        assert!(fired.is_empty(), "a held L3+R3 only arms the return");
        assert!(key(&mut game, 0x13e, 0, 3).actions.is_empty());
        fired.extend(key(&mut game, 0x13d, 0, 4).actions);
        assert_eq!(fired, vec!["input:window off"]);
        assert!(game.tick(0.1).actions.is_empty());
    }
    #[test]
    fn return_chord_drains_before_both_releases_and_never_replays_held_input() {
        for first in [0x13d, 0x13e] {
            let last = if first == 0x13d { 0x13e } else { 0x13d };
            let mut r = router();
            r.set_focus("app-capture", Some(("0xapp".into(), 42)), true);
            key(&mut r, 0x130, 1, 1);
            r.event(Event::new(EV_ABS, 3, 26000), 2);
            key(&mut r, 0x13d, 1, 3);
            key(&mut r, 0x13e, 1, 4);
            let mut out = Output::default();
            for _ in 0..5 {
                out.extend(r.tick(0.1));
            }
            assert!(
                r.app_owned() && !r.room(),
                "hold must not restore room ownership"
            );
            assert!(out.actions.is_empty());
            for code in [0x130, 0x13d, 0x13e] {
                assert!(
                    out.app.contains(&Event::new(EV_KEY, code, 0)),
                    "drain {code}"
                );
            }
            assert!(out.app.contains(&Event::new(EV_ABS, 3, 0)));
            assert!(r.event(Event::new(EV_ABS, 3, 32000), 5).app.is_empty());
            assert!(key(&mut r, 0x131, 1, 6).app.is_empty());
            assert!(r.tick(0.1).pointer.is_empty());
            assert!(key(&mut r, first, 0, 7).actions.is_empty());
            assert!(r.app_owned());
            let out = key(&mut r, last, 0, 8);
            assert_eq!(out.actions, vec!["input:window off"]);
            assert!(out.app.iter().all(|e| e.kind != EV_KEY || e.value == 0));
            assert!(r.room());
            assert_eq!((r.pad.rx, r.pad.ry), (0.0, 0.0));
            assert!(key(&mut r, 0x130, 0, 9).actions.is_empty());
            assert!(key(&mut r, 0x131, 0, 10).actions.is_empty());
            assert!(r.tick(0.1).actions.is_empty());
        }
    }
    #[test]
    fn short_return_chord_and_repaired_release_never_request_ownership() {
        let mut r = router();
        r.set_focus("app-capture", Some(("0xapp".into(), 42)), true);
        key(&mut r, 0x13d, 1, 1);
        key(&mut r, 0x13e, 1, 2);
        for _ in 0..4 {
            assert!(r.tick(0.1).actions.is_empty());
        }
        assert!(key(&mut r, 0x13d, 0, 3).actions.is_empty());
        assert!(key(&mut r, 0x13e, 0, 4).actions.is_empty());
        assert!(r.app_owned());
        key(&mut r, 0x13d, 1, 5);
        key(&mut r, 0x13e, 1, 6);
        for _ in 0..5 {
            r.tick(0.1);
        }
        let repaired = r.reconcile(&BTreeSet::new(), 7);
        assert!(
            repaired.actions.is_empty(),
            "kernel repair cannot invent an ownership action"
        );
        assert!(r.app_owned());
    }
    #[test]
    fn north_tap_hands_all_controls_to_a_window_without_fullscreen_or_room_motion() {
        let mut r = router();
        r.global.controller.captured_system_layer = false;
        r.set_focus("desktop-3d", Some(("0xabc".into(), 42)), false);
        let north = button_code(&r.legacy.fullscreen_button).unwrap();
        assert_eq!(key(&mut r, north, 1, 1).actions, vec!["input:window on"]);
        assert!(r.tick(0.1).actions.is_empty());
        assert!(key(&mut r, north, 0, 2).actions.is_empty());
        // A stale compositor snapshot must not reactivate host controls while the transaction runs.
        r.set_focus("desktop-3d", Some(("0xabc".into(), 42)), false);
        let seq = r.pad.sequences;
        for code in [
            0x130, 0x131, 0x133, 0x134, 0x136, 0x137, 0x138, 0x139, 0x13a, 0x13b, 0x13d, 0x13e,
        ] {
            let down = key(&mut r, code, 1, 3);
            assert!(down.actions.is_empty(), "host action for {code}");
            assert!(
                down.app.contains(&Event::new(EV_KEY, code, 1)),
                "missing app input {code}"
            );
            key(&mut r, code, 0, 4);
        }
        for code in [0, 1, 2, 3, 4, 5, 16, 17] {
            let event = Event::new(EV_ABS, code, 123);
            assert!(r.event(event, 5).app.contains(&event));
        }
        assert!(r.tick(0.01).actions.is_empty());
        assert_eq!(
            (r.pad.lx, r.pad.ly, r.pad.rx, r.pad.ry),
            (0.0, 0.0, 0.0, 0.0)
        );
        assert_eq!(
            r.pad.sequences, seq,
            "no compositor action sequence may leak"
        );
        r.set_focus("app-capture", Some(("0xabc".into(), 42)), true);
        assert!(r.app_owned());
    }
    #[test]
    fn north_hold_enters_fullscreen_once_and_a_repaired_release_never_captures() {
        let mut r = router();
        r.set_context("desktop-3d");
        let north = button_code(&r.legacy.fullscreen_button).unwrap();
        key(&mut r, north, 1, 1);
        for _ in 0..4 {
            assert!(r.tick(0.1).actions.is_empty());
        }
        assert_eq!(r.tick(0.1).actions, vec!["input:window fullscreen"]);
        assert!(r.tick(0.1).actions.is_empty());
        assert!(key(&mut r, north, 0, 2).actions.is_empty());
        let mut missed = router();
        missed.set_context("desktop-3d");
        key(&mut missed, north, 1, 1);
        assert!(missed.reconcile(&BTreeSet::new(), 2).actions.is_empty());
        for _ in 0..8 {
            assert!(missed.tick(0.1).actions.is_empty());
        }
    }
    #[test]
    fn north_press_captures_cursor_focused_windows_and_suppresses_its_entire_press() {
        for context in ["desktop-2d", "terminal", "game", "vm-passthrough"] {
            let mut r = router();
            r.global.controller.captured_system_layer = false;
            r.set_focus("terminal", Some(("0xabc".into(), 42)), false);
            // Cursor focus crosses ordinary window boundaries without leaving cursor mode.
            key(&mut r, 0x13b, 1, 1);
            key(&mut r, 0x13b, 0, 2);
            r.set_focus(context, Some(("0xdef".into(), 43)), false);
            assert!(r.cursor, "{context}");
            let north = button_code(&r.legacy.fullscreen_button).unwrap();
            let out = key(&mut r, north, 1, 3);
            assert_eq!(out.actions, vec!["input:window on"], "{context}");
            assert!(
                !out.app.iter().any(|e| e.kind == EV_KEY) && out.pointer.is_empty(),
                "{context}"
            );
            assert!(
                r.app_owned() && !r.cursor,
                "capture must start on press: {context}"
            );
            assert!(
                key(&mut r, north, 2, 4).app.is_empty(),
                "repeat cannot leak: {context}"
            );
            let released = key(&mut r, north, 0, 5);
            assert!(
                released.actions.is_empty() && released.app.is_empty(),
                "{context}"
            );
            for _ in 0..6 {
                assert!(r.tick(0.1).actions.is_empty());
            }
        }
    }
    #[test]
    fn north_hold_survives_capture_confirmation_without_releasing_other_app_controls() {
        let mut r = router();
        r.global.controller.captured_system_layer = false;
        r.set_focus("desktop-3d", Some(("0xabc".into(), 42)), false);
        let north = button_code(&r.legacy.fullscreen_button).unwrap();
        assert_eq!(key(&mut r, north, 1, 1).actions, vec!["input:window on"]);
        r.tick(0.1);
        r.confirm_capture_target("0xabc");
        r.set_focus("app-capture", Some(("0xabc".into(), 42)), true);
        assert!(
            r.north_pending,
            "same-window confirmation must preserve the hold"
        );
        assert!(key(&mut r, 0x130, 1, 2)
            .app
            .contains(&Event::new(EV_KEY, 0x130, 1)));
        for _ in 0..3 {
            assert!(r.tick(0.1).actions.is_empty());
        }
        let out = r.tick(0.1);
        assert_eq!(out.actions, vec!["input:window fullscreen"]);
        assert!(
            out.app.is_empty(),
            "fullscreen upgrade cannot release an app's held controls"
        );
        assert!(
            r.room_capture,
            "fullscreen upgrade preserves the room return path"
        );
        assert!(r.tick(0.1).actions.is_empty());
        assert!(key(&mut r, north, 0, 3).actions.is_empty());
        assert!(key(&mut r, 0x130, 0, 4)
            .app
            .contains(&Event::new(EV_KEY, 0x130, 0)));
    }
    #[test]
    fn north_pending_hold_cannot_move_to_a_different_focus_target_or_survive_failed_capture() {
        for failed in [false, true] {
            let mut r = router();
            r.set_focus("desktop-2d", Some(("0xabc".into(), 42)), false);
            let north = button_code(&r.legacy.fullscreen_button).unwrap();
            assert_eq!(key(&mut r, north, 1, 1).actions, vec!["input:window on"]);
            if failed {
                r.ownership_failed();
            } else {
                r.confirm_capture_target("0xabc");
                r.set_focus("desktop-2d", Some(("0xdef".into(), 43)), false);
            }
            for _ in 0..6 {
                assert!(r.tick(0.1).actions.is_empty());
            }
            let out = key(&mut r, north, 0, 2);
            assert!(out.actions.is_empty() && out.app.is_empty());
        }
    }
    #[test]
    fn delayed_capture_receipt_rebinds_cached_focus_without_cancelling_north_hold() {
        let mut r = router();
        r.set_focus("terminal", Some(("0xaaa".into(), 1)), false);
        let north = button_code(&r.legacy.fullscreen_button).unwrap();
        assert_eq!(key(&mut r, north, 1, 1).actions, vec!["input:window on"]);
        // Cursor focus reached B before the old cached snapshot caught up or the transaction replied.
        r.set_focus("terminal", Some(("0xbbb".into(), 2)), false);
        assert!(r.app_owned() && r.north_pending);
        r.tick(0.1);
        r.confirm_capture_target("0xbbb");
        r.set_focus("app-capture", Some(("0xbbb".into(), 2)), false);
        assert!(r.north_pending && r.app_owned());
        assert_eq!(r.capture_target, Some(("0xbbb".into(), 2)));
        assert!(r.capture_override.is_none() && r.capture_confirmation.is_none());
        for _ in 0..3 {
            assert!(r.tick(0.1).actions.is_empty());
        }
        assert_eq!(r.tick(0.1).actions, vec!["input:window fullscreen"]);
        assert!(key(&mut r, north, 0, 2).actions.is_empty());
    }
    #[test]
    fn confirmed_capture_receipt_does_not_allow_north_hold_to_follow_a_third_window() {
        let mut r = router();
        r.set_focus("terminal", Some(("0xaaa".into(), 1)), false);
        let north = button_code(&r.legacy.fullscreen_button).unwrap();
        key(&mut r, north, 1, 1);
        r.set_focus("terminal", Some(("0xbbb".into(), 2)), false);
        r.confirm_capture_target("0xbbb");
        r.set_focus("app-capture", Some(("0xbbb".into(), 2)), false);
        key(&mut r, 0x130, 1, 2);
        let out = r.set_focus("app-capture", Some(("0xccc".into(), 3)), false);
        assert!(out.app.contains(&Event::new(EV_KEY, 0x130, 0)));
        assert!(!r.north_pending);
        for _ in 0..6 {
            assert!(r.tick(0.1).actions.is_empty());
        }
        assert!(key(&mut r, north, 0, 3).actions.is_empty());
        assert!(
            key(&mut r, 0x130, 2, 4).app.is_empty(),
            "held controls cannot move into C"
        );
    }
    #[test]
    fn capture_receipt_rejects_a_different_focus_even_when_the_cached_identity_is_unchanged() {
        let mut r = router();
        r.set_focus("terminal", Some(("0xaaa".into(), 1)), false);
        let north = button_code(&r.legacy.fullscreen_button).unwrap();
        key(&mut r, north, 1, 1);
        r.set_focus("terminal", Some(("0xccc".into(), 3)), false);
        r.confirm_capture_target("0xbbb");
        r.set_focus("terminal", Some(("0xccc".into(), 3)), false);
        assert!(!r.north_pending && !r.app_owned());
        for _ in 0..6 {
            assert!(r.tick(0.1).actions.is_empty());
        }
        assert!(key(&mut r, north, 0, 2).actions.is_empty());
    }
    #[test]
    fn unconfirmed_capture_snapshot_cannot_extend_the_receipt_deadline_or_survive_failure() {
        for fail in [false, true] {
            let mut r = router();
            r.global.controller.capture_hold_ms = 2000;
            r.set_focus("terminal", Some(("0xaaa".into(), 1)), false);
            let north = button_code(&r.legacy.fullscreen_button).unwrap();
            key(&mut r, north, 1, 1);
            r.set_focus("app-capture", Some(("0xbbb".into(), 2)), false);
            assert!(r.capture_override == Some(true));
            if fail {
                r.ownership_failed();
            } else {
                for _ in 0..11 {
                    assert!(r.tick(0.1).actions.is_empty());
                }
            }
            assert!(
                !r.north_pending
                    && r.capture_confirmation.is_none()
                    && r.capture_override.is_none()
            );
            r.confirm_capture_target("0xaaa");
            assert!(
                r.capture_confirmation.is_none(),
                "a late receipt cannot restore expired ownership"
            );
            for _ in 0..20 {
                assert!(r.tick(0.1).actions.is_empty());
            }
            assert!(key(&mut r, north, 0, 2).actions.is_empty());
        }
    }
    #[test]
    fn captured_guide_does_not_swallow_application_sticks() {
        let mut r = router();
        r.global.controller.captured_system_layer = false;
        r.set_context("app-capture");
        key(&mut r, 0x13c, 1, 1);
        let axis = Event::new(EV_ABS, 0, 32767);
        assert!(r.event(axis, 2).app.contains(&axis));
        assert_eq!(r.pad.lx, 0.0);
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
        assert_eq!(
            key(&mut g, 0x136, 1, 1).actions,
            vec!["mios:shaderglass cycle"]
        );
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
    const SOUTH: u16 = 0x130;
    const EAST: u16 = 0x131;
    const NORTH: u16 = 0x133;
    const LB: u16 = 0x136;
    const RB: u16 = 0x137;
    const RT: u16 = 0x139;
    const SELECT: u16 = 0x13a;
    const START: u16 = 0x13b;
    const GUIDE: u16 = 0x13c;
    const L3: u16 = 0x13d;
    const R3: u16 = 0x13e;
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
        for c in [2, 5] {
            axes.insert(
                c,
                Axis {
                    min: 0,
                    max: 255,
                    value: 0,
                },
            );
        }
        for c in [16, 17] {
            axes.insert(
                c,
                Axis {
                    min: -1,
                    max: 1,
                    value: 0,
                },
            );
        }
        Router::new(c, l, axes)
    }
    fn key(r: &mut Router, c: u16, v: i32, t: u64) -> Output {
        r.event(Event::new(EV_KEY, c, v), t)
    }
    fn tap(r: &mut Router, c: u16, t: u64) -> Output {
        let mut o = key(r, c, 1, t);
        o.extend(key(r, c, 0, t + 1));
        o
    }
    fn hold(r: &mut Router, ms: u64) -> Output {
        let mut o = Output::default();
        for _ in 0..ms / 50 {
            o.extend(r.tick(0.05));
        }
        o
    }
    fn app_keys(o: &Output) -> Vec<(u16, i32)> {
        o.app
            .iter()
            .filter(|e| e.kind == EV_KEY)
            .map(|e| (e.code, e.value))
            .collect()
    }
    // Every window kind the operator named: native, Flatpak, game, VM, remote, nested, PiP, Theatre, terminal.
    const KINDS: [(&str, &str); 9] = [
        ("org.gnome.Nautilus", "desktop-2d"),
        ("org.any.Flatpak", "desktop-2d"),
        ("steam_app_570", "game"),
        ("looking-glass-client", "vm-passthrough"),
        ("xfreerdp", "vm-passthrough"),
        ("mios-nested-desktop", "desktop-2d"),
        ("mios-pip-player", "desktop-2d"),
        ("mios-theatre", "desktop-2d"),
        ("Alacritty", "terminal"),
    ];

    #[test]
    fn capture_and_release_are_identical_for_every_window_kind() {
        let (mut cfg, _) = crate::config::tests::fixture();
        cfg.contexts
            .get_mut("vm-passthrough")
            .unwrap()
            .push("xfreerdp".into());
        let mut transcripts = vec![];
        for (class, expected) in KINDS {
            let context = cfg.classify_window(Some(class), false, false);
            let mut r = router();
            r.set_focus(&context, Some((format!("0x{class}"), 9)), false);
            let mut t = vec![context.clone(), format!("owned={}", r.app_owned())];
            // North tap captures; RB, Guide and Start then belong to the app; a held L3+R3 releases.
            t.extend(tap(&mut r, NORTH, 10).actions);
            r.set_focus(
                &cfg.classify_window(Some(class), false, true),
                Some((format!("0x{class}"), 9)),
                false,
            );
            let o = tap(&mut r, RB, 20);
            t.push(format!(
                "rb-actions={:?} rb-app={:?}",
                o.actions,
                app_keys(&o)
            ));
            key(&mut r, L3, 1, 30);
            key(&mut r, R3, 1, 31);
            t.extend(hold(&mut r, 700).actions);
            t.extend(key(&mut r, L3, 0, 40).actions);
            t.extend(key(&mut r, R3, 0, 41).actions);
            r.set_focus(
                &cfg.classify_window(Some(class), false, false),
                Some((format!("0x{class}"), 9)),
                false,
            );
            t.push(format!("owned-after={}", r.app_owned()));
            assert_eq!(context, expected, "{class}");
            transcripts.push((class, t[1..].to_vec()));
        }
        let first = transcripts[0].1.clone();
        assert_eq!(
            first,
            vec![
                "owned=false".to_string(),
                "input:window on".into(),
                "rb-actions=[] rb-app=[(311, 1), (311, 0)]".into(),
                "input:window off".into(),
                "owned-after=false".into()
            ]
        );
        for (class, t) in &transcripts {
            assert_eq!(
                t, &first,
                "{class} must capture and release like every other window"
            );
        }
    }
    #[test]
    fn half_capture_keeps_every_button_with_the_app() {
        // tests.md C-01: the live bug was RB firing ShaderGlass after Guide+Select in a captured app.
        let mut r = router();
        r.set_context("app-capture");
        let mut o = Output::default();
        for (c, v, t) in [
            (GUIDE, 1, 1),
            (SELECT, 1, 2),
            (SELECT, 0, 3),
            (GUIDE, 0, 4),
            (RB, 1, 5),
            (RB, 0, 6),
        ] {
            o.extend(key(&mut r, c, v, t));
        }
        assert_eq!(o.actions, vec!["input:cursor on"]);
        assert!(r.half_capture());
        assert_eq!(
            app_keys(&o),
            vec![(RB, 1), (RB, 0)],
            "RB reaches the app, never ShaderGlass"
        );
        // Guide+East after the escape: the app gets Guide then East; no hypr:killactive.
        let mut o = Output::default();
        for (c, v, t) in [(GUIDE, 1, 7), (EAST, 1, 8), (EAST, 0, 9), (GUIDE, 0, 10)] {
            o.extend(key(&mut r, c, v, t));
        }
        assert!(o.actions.is_empty(), "{:?}", o.actions);
        assert_eq!(
            app_keys(&o),
            vec![(GUIDE, 1), (EAST, 1), (EAST, 0), (GUIDE, 0)]
        );
        // North after the escape belongs to the app: no capture transaction.
        let o = tap(&mut r, NORTH, 11);
        assert!(o.actions.is_empty() && app_keys(&o) == vec![(NORTH, 1), (NORTH, 0)]);
        assert!(hold(&mut r, 600).actions.is_empty());
        // Sticks and RT are host pointer input; the app never sees them in half-capture.
        let stick = r.event(Event::new(EV_ABS, 3, 32767), 12);
        assert!(stick.app.is_empty());
        let moved = r.tick(0.02);
        assert!(moved
            .pointer
            .iter()
            .any(|e| e.kind == EV_REL && e.code == 0 && e.value > 0));
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
        assert_eq!(
            app_keys(&tap(&mut r, START, 18)),
            vec![(START, 1), (START, 0)],
            "Start in full capture is the app's"
        );
        // Guide+Select twice: on, then off again.
        let mut o = Output::default();
        for (c, v, t) in [
            (GUIDE, 1, 20),
            (SELECT, 1, 21),
            (SELECT, 0, 22),
            (GUIDE, 0, 23),
            (GUIDE, 1, 24),
            (SELECT, 1, 25),
            (SELECT, 0, 26),
            (GUIDE, 0, 27),
        ] {
            o.extend(key(&mut r, c, v, t));
        }
        assert_eq!(o.actions, vec!["input:cursor on", "input:cursor off"]);
        assert!(
            app_keys(&o).is_empty(),
            "the escape chord is never replayed to the app"
        );
    }
    #[test]
    fn uncaptured_contexts_keep_host_shaderglass_and_cursor() {
        // Negative control for C-01: the same sequence uncaptured still reaches the host.
        for context in ["desktop-2d", "game", "vm-passthrough", "terminal"] {
            let mut r = router();
            r.set_context(context);
            let mut o = Output::default();
            for (c, v, t) in [(GUIDE, 1, 1), (SELECT, 1, 2), (SELECT, 0, 3), (GUIDE, 0, 4)] {
                o.extend(key(&mut r, c, v, t));
            }
            assert_eq!(o.actions, vec!["input:cursor on"], "{context}");
            assert_eq!(
                tap(&mut r, RB, 5).actions,
                vec!["mios:shaderglass toggle"],
                "{context}"
            );
            assert_eq!(
                tap(&mut r, NORTH, 6).actions,
                vec!["input:window on"],
                "North captures from cursor mode too ({context})"
            );
        }
        let mut r = router();
        r.set_context("desktop-2d");
        assert_eq!(tap(&mut r, NORTH, 1).actions, vec!["input:window on"]);
    }
    #[test]
    fn captured_system_layer_fixture_choice_matters() {
        // C-27 negative control: with captured_system_layer=true the Guide layer is host input in a capture.
        let mut r = router();
        r.global.controller.captured_system_layer = true;
        r.set_context("app-capture");
        let mut o = Output::default();
        for (c, v, t) in [(GUIDE, 1, 1), (LB, 1, 2), (LB, 0, 3), (GUIDE, 0, 4)] {
            o.extend(key(&mut r, c, v, t));
        }
        assert_eq!(o.actions, vec!["hypr:workspace e-1"]);
        let mut r = router();
        r.set_context("app-capture");
        let mut o = Output::default();
        for (c, v, t) in [(GUIDE, 1, 1), (LB, 1, 2), (LB, 0, 3), (GUIDE, 0, 4)] {
            o.extend(key(&mut r, c, v, t));
        }
        assert!(o.actions.is_empty());
    }
    #[test]
    fn ownership_is_per_session_not_per_pad() {
        let mut a = router();
        let mut b = router();
        for r in [&mut a, &mut b] {
            r.set_focus("desktop-3d", Some(("0xroom".into(), 3)), false);
        }
        assert_eq!(tap(&mut a, NORTH, 1).actions, vec!["input:window on"]);
        b.event(Event::new(EV_ABS, 0, 32767), 2);
        b.tick(0.01);
        assert!(
            b.pad.lx != 0.0,
            "precondition: the second pad drives the room before it adopts the capture"
        );
        let o = b.adopt_ownership(true);
        assert!(
            o.app.iter().any(|e| e.kind == EV_ABS),
            "adoption flushes the second pad's axes"
        );
        assert!(b.app_owned() && b.pad.lx == 0.0);
        let stick = b.event(Event::new(EV_ABS, 0, 32767), 3);
        b.tick(0.01);
        assert!(
            stick.app.contains(&Event::new(EV_ABS, 0, 32767)) && b.pad.lx == 0.0,
            "the second pad now feeds only the app"
        );
        assert!(
            b.adopt_ownership(true).app.is_empty(),
            "adopting the same ownership twice is a no-op"
        );
    }
    #[test]
    fn unconfirmed_capture_reverts_after_context_stale_ms() {
        // C-03: the receipt identifies the selected window, then its owned snapshot commits the capture.
        let mut r = router();
        r.set_focus("desktop-2d", Some(("0xa".into(), 1)), false);
        tap(&mut r, NORTH, 1);
        r.set_focus("desktop-2d", Some(("0xa".into(), 1)), false);
        r.tick(0.5);
        assert!(r.app_owned(), "pending inside context_stale_ms");
        r.confirm_capture_target("0xa");
        r.set_focus("app-capture", Some(("0xa".into(), 1)), false);
        assert_eq!(r.ownership(), None, "the snapshot confirmed the capture");
        r.tick(0.1);
        r.tick(0.1);
        for _ in 0..20 {
            r.tick(0.1);
        }
        assert!(r.app_owned(), "a confirmed capture never expires");
        // Negative: never confirmed -> the app does not keep the pad.
        let mut r = router();
        r.set_focus("desktop-2d", Some(("0xa".into(), 1)), false);
        tap(&mut r, NORTH, 1);
        for _ in 0..9 {
            r.set_focus("desktop-2d", Some(("0xa".into(), 1)), false);
            r.tick(0.1);
        }
        assert!(r.app_owned());
        r.tick(0.1);
        r.tick(0.05);
        assert!(
            !r.app_owned() && r.ownership().is_none(),
            "unconfirmed capture reverted after context_stale_ms"
        );
        // Negative: a failed ownership transaction reverts at once and releases what the app held.
        let mut r = router();
        r.set_focus("desktop-2d", Some(("0xa".into(), 1)), false);
        tap(&mut r, NORTH, 1);
        key(&mut r, SOUTH, 1, 2);
        let o = r.ownership_failed();
        assert!(!r.app_owned() && o.app.contains(&Event::new(EV_KEY, SOUTH, 0)));
    }
    #[test]
    fn confirmation_does_not_flush_a_held_stick() {
        let mut r = router();
        r.set_focus("desktop-2d", Some(("0xa".into(), 1)), false);
        tap(&mut r, NORTH, 1);
        r.event(Event::new(EV_ABS, 0, 30000), 2);
        let o = r.set_focus("app-capture", Some(("0xa".into(), 1)), false);
        assert!(
            o.app.is_empty(),
            "the compositor confirming our own capture must not zero the game's stick"
        );
    }

    struct Lcg(u64);
    impl Lcg {
        fn next(&mut self) -> u64 {
            self.0 = self
                .0
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            self.0 >> 33
        }
    }
    /// INV-5 / C-04: random streams in every context. A frame never has a non-neutral room packet together with a
    /// non-neutral stick event forwarded to the app or host pointer motion.
    fn axis_owner_property(mutant: bool) -> Result<(), String> {
        let contexts = [
            "desktop-2d",
            "desktop-3d",
            "terminal",
            "game",
            "vm-passthrough",
            "app-capture",
        ];
        let buttons = [
            SOUTH, EAST, NORTH, 0x134, LB, RB, 0x138, RT, SELECT, START, GUIDE, L3, R3,
        ];
        for seed in 0..48u64 {
            let mut g = Lcg(seed * 7919 + 13);
            let mut r = router();
            r.mutant_room_forward = mutant;
            let mut held = BTreeSet::new();
            let mut now = 1000;
            for step in 0..400 {
                now += 1 + g.next() % 40;
                let mut o = Output::default();
                match g.next() % 10 {
                    0 => {
                        let c = contexts[(g.next() % contexts.len() as u64) as usize];
                        let t = format!("0x{}", g.next() % 3);
                        o.extend(r.set_focus(c, Some((t, 1)), c == "desktop-3d"));
                    }
                    1..=3 => {
                        let b = buttons[(g.next() % buttons.len() as u64) as usize];
                        let v = i32::from(!held.contains(&b));
                        if v == 1 {
                            held.insert(b);
                        } else {
                            held.remove(&b);
                        }
                        o.extend(r.event(Event::new(EV_KEY, b, v), now));
                    }
                    4..=7 => {
                        let code = [0u16, 1, 3, 4][(g.next() % 4) as usize];
                        let v = (g.next() % 65536) as i32 - 32768;
                        o.extend(r.event(Event::new(EV_ABS, code, v), now));
                        o.extend(r.event(Event::new(EV_SYN, SYN_REPORT, 0), now));
                    }
                    8 => {
                        if g.next().is_multiple_of(2) {
                            o.extend(r.adopt_ownership(g.next().is_multiple_of(2)));
                        } else {
                            o.extend(r.ownership_failed());
                        }
                    }
                    _ => o.extend(r.tick(0.02)),
                }
                o.extend(r.tick(0.005));
                let pad_live =
                    r.pad.lx != 0.0 || r.pad.ly != 0.0 || r.pad.rx != 0.0 || r.pad.ry != 0.0;
                let app_live = o.app.iter().any(|e| {
                    e.kind == EV_ABS
                        && [0, 1, 3, 4].contains(&e.code)
                        && e.value.unsigned_abs() > 4000
                });
                let pointer_live = o.pointer.iter().any(|e| e.kind == EV_REL);
                if pad_live && (app_live || pointer_live) {
                    return Err(format!("seed {seed} step {step} context {} owned {} cursor {}: room and {} share a frame",
                        r.context, r.app_owned(), r.cursor, if app_live { "app" } else { "pointer" }));
                }
                if app_live && pointer_live {
                    return Err(format!(
                        "seed {seed} step {step}: app and pointer share a stick frame"
                    ));
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
        assert!(
            axis_owner_property(true).is_err(),
            "the property must catch a router that forwards room axes to the app"
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
    // ---- spec P1 (C-14b): [keybindings.global.controller.room] from the vendor SSOT ----
    fn key(r: &mut Router, c: u16, v: i32, t: u64) -> Output {
        r.event(Event::new(EV_KEY, c, v), t)
    }
    fn vendor_room() -> Router {
        let v: toml::Value = include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap();
        let (g, l) = GlobalConfig::from_value(&v).unwrap();
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
        let mut r = Router::new(g, l, axes);
        r.set_context("desktop-3d");
        r
    }
    const EAST: u16 = 0x131;
    const SELECT: u16 = 0x13a;
    const GUIDE: u16 = 0x13c;
    const L3: u16 = 0x13d;
    const R3: u16 = 0x13e;
    #[test]
    fn room_east_cancels_on_press_and_never_reaches_an_app() {
        let mut r = vendor_room();
        let o = key(&mut r, EAST, 1, 10);
        assert_eq!(o.actions, vec!["hypr:hypr3d:cancel".to_string()]);
        assert!(o.app.is_empty());
        assert!(
            key(&mut r, EAST, 0, 20).actions.is_empty(),
            "release fires nothing more"
        );
    }
    #[test]
    fn room_l3_sprints_and_r3_recenters_on_a_clean_release() {
        let mut r = vendor_room();
        assert!(
            key(&mut r, L3, 1, 10).actions.is_empty(),
            "a stick click waits for its release (chord disambiguation)"
        );
        assert_eq!(
            key(&mut r, L3, 0, 60).actions,
            vec!["hypr:hypr3d:sprint".to_string()]
        );
        assert!(key(&mut r, R3, 1, 600).actions.is_empty());
        assert_eq!(
            key(&mut r, R3, 0, 650).actions,
            vec!["hypr:hypr3d:recenter".to_string()]
        );
        // L3 is NOT ignored while walking (left stick deflected): sprint is used while moving
        r.axes.get_mut(&1).unwrap().value = -30000;
        key(&mut r, L3, 1, 1000);
        assert_eq!(
            key(&mut r, L3, 0, 1050).actions,
            vec!["hypr:hypr3d:sprint".to_string()]
        );
    }
    #[test]
    fn room_stick_click_overlap_is_the_room_toggle_never_a_single() {
        let mut r = vendor_room();
        key(&mut r, L3, 1, 10);
        let o = key(&mut r, R3, 1, 30);
        assert_eq!(
            o.actions,
            vec!["mios:hyprland-3d-toggle".to_string()],
            "L3+R3 inside debounce = the room toggle"
        );
        let mut all = key(&mut r, L3, 0, 80).actions;
        all.extend(key(&mut r, R3, 0, 90).actions);
        assert!(
            all.is_empty(),
            "NEGATIVE: the chord never also fires sprint or recenter: {all:?}"
        );
        // slow overlap (outside debounce): no toggle and still no singles (R-23)
        let mut r = vendor_room();
        key(&mut r, L3, 1, 10);
        let mut all = key(&mut r, R3, 1, 410).actions;
        all.extend(key(&mut r, R3, 0, 450).actions);
        all.extend(key(&mut r, L3, 0, 460).actions);
        assert!(all.is_empty(), "slow overlap fires nothing: {all:?}");
    }
    #[test]
    fn room_r3_is_ignored_while_the_right_stick_is_deflected() {
        let mut r = vendor_room();
        r.axes.get_mut(&3).unwrap().value = 30000;
        key(&mut r, R3, 1, 10);
        assert!(key(&mut r, R3, 0, 50).actions.is_empty());
    }
    #[test]
    fn room_select_tap_is_the_view_slot_and_hold_is_the_keymap() {
        let mut r = vendor_room();
        key(&mut r, SELECT, 1, 10);
        let o = key(&mut r, SELECT, 0, 100);
        assert!(
            o.actions.is_empty(),
            "the tap spawns no process: {:?}",
            o.actions
        );
        assert_eq!(
            r.pad.sequences[0], 1,
            "tap = packet view slot 0 at release (next room frame)"
        );
        key(&mut r, SELECT, 1, 200);
        // one tick is clamped to 100 ms: advance the clock in 100 ms steps
        let ticks =
            |r: &mut Router, n: usize| (0..n).flat_map(|_| r.tick(0.1).actions).collect::<Vec<_>>();
        assert!(
            ticks(&mut r, 4).is_empty(),
            "no hold before capture_hold_ms"
        );
        assert_eq!(
            ticks(&mut r, 1),
            vec!["quickshell:keymap-toggle".to_string()]
        );
        assert!(ticks(&mut r, 10).is_empty(), "a hold fires once");
        assert!(key(&mut r, SELECT, 0, 2000).actions.is_empty());
        assert_eq!(
            r.pad.sequences[0], 1,
            "NEGATIVE: a hold does not also cycle the view"
        );
    }
    #[test]
    fn room_guide_tap_opens_the_drawer_only_as_a_clean_tap() {
        let mut r = vendor_room();
        key(&mut r, GUIDE, 1, 10);
        assert!(
            key(&mut r, GUIDE, 0, 120).actions.is_empty(),
            "the first tap waits for double-Guide disambiguation"
        );
        let mut single = Output::default();
        for _ in 0..r.global.controller.guide_double_press_ms.div_ceil(10) {
            single.extend(r.tick(0.01));
        }
        assert_eq!(single.actions, vec!["quickshell:drawer-toggle".to_string()]);
        // Guide + a button is the system layer, not a tap
        key(&mut r, GUIDE, 1, 1000);
        let layer = key(&mut r, 0x130, 1, 1010).actions;
        key(&mut r, 0x130, 0, 1020);
        assert_eq!(layer, vec!["hypr:togglefloating".to_string()]);
        assert!(
            key(&mut r, GUIDE, 0, 1100).actions.is_empty(),
            "NEGATIVE: Guide+South never also opens the drawer"
        );
        // a long Guide hold is not a tap
        key(&mut r, GUIDE, 1, 3000);
        assert!(
            key(&mut r, GUIDE, 0, 3600).actions.is_empty(),
            "NEGATIVE: held past capture_hold_ms"
        );
    }
    #[test]
    fn room_table_is_room_only_and_respects_capture() {
        // desktop-2d: East is not the room's cancel
        let mut r = vendor_room();
        r.set_context("desktop-2d");
        let o = key(&mut r, EAST, 1, 10);
        assert!(
            !o.actions.iter().any(|a| a.contains("hypr3d")),
            "outside the room East is not cancel: {:?}",
            o.actions
        );
        // a captured window owns East: it reaches the app, never the room table
        let mut r = vendor_room();
        r.global.controller.captured_system_layer = false;
        r.set_context("app-capture");
        let o = key(&mut r, EAST, 1, 10);
        assert!(
            o.actions.is_empty() && o.app.contains(&Event::new(EV_KEY, EAST, 1)),
            "app-capture: {o:?}"
        );
        // O-1/O-4: an uncaptured game or VM class is an ordinary window: neither the room table nor the app sees East
        for ctx in ["game", "vm-passthrough"] {
            let mut r = vendor_room();
            r.set_context(ctx);
            let mut o = key(&mut r, EAST, 1, 10);
            o.extend(key(&mut r, EAST, 0, 40));
            assert!(
                o.app.iter().all(|e| e.code != EAST),
                "{ctx}: an uncaptured class never gets the pad: {o:?}"
            );
            assert_eq!(
                o.actions,
                vec!["key:ESC".to_string()],
                "{ctx}: East tap is the nav Escape"
            );
        }
        // a room capture (North tap): the captured window owns East too
        let mut r = vendor_room();
        r.set_focus("desktop-3d", Some(("0xabc".into(), 42)), false);
        key(&mut r, 0x133, 1, 10);
        key(&mut r, 0x133, 0, 50);
        let o = key(&mut r, EAST, 1, 100);
        assert!(
            o.actions.is_empty() && o.app.contains(&Event::new(EV_KEY, EAST, 1)),
            "room capture owns East: {o:?}"
        );
    }
    #[test]
    fn room_pending_is_flushed_on_context_change_and_kernel_repair() {
        let mut r = vendor_room();
        key(&mut r, SELECT, 1, 10);
        r.set_context("desktop-2d");
        r.set_context("desktop-3d");
        assert!(
            (0..10).all(|_| r.tick(0.1).actions.is_empty()),
            "INV-6: a context change cancels the pending hold"
        );
        let mut r = vendor_room();
        key(&mut r, L3, 1, 10);
        let o = r.reconcile(&BTreeSet::new(), 20);
        assert!(
            o.actions.is_empty(),
            "a repaired release is not a tap: {:?}",
            o.actions
        );
    }
}

#[cfg(test)]
mod nav_contract {
    //! I2: the navigation grammar for every uncaptured window (tests.md C-07..C-13, INV-6, R-23, R-26).
    use super::*;
    const SOUTH: u16 = 0x130;
    const EAST: u16 = 0x131;
    const NORTH: u16 = 0x133;
    const LB: u16 = 0x136;
    const TL2: u16 = 0x138;
    const TR2: u16 = 0x139;
    const SELECT: u16 = 0x13a;
    const START: u16 = 0x13b;
    const GUIDE: u16 = 0x13c;
    const L3: u16 = 0x13d;
    const R3: u16 = 0x13e;
    fn axes() -> BTreeMap<u16, Axis> {
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
        for c in [2, 5] {
            axes.insert(
                c,
                Axis {
                    min: 0,
                    max: 255,
                    value: 0,
                },
            );
        }
        for c in [16, 17] {
            axes.insert(
                c,
                Axis {
                    min: -1,
                    max: 1,
                    value: 0,
                },
            );
        }
        axes
    }
    /// This module holds the PRE-task3773 magnitude-flick contracts, so its fixture
    /// removes the vendor focus_gesture table: an absent table is the documented
    /// legacy policy, and nothing here is silently re-qualified as a gesture.
    fn nav(context: &str) -> Router {
        let (c, l) = crate::config::tests::try_fixture_nav_with(|v| {
            v["keybindings"]["global"]["controller"]["nav"]
                .as_table_mut()
                .unwrap()
                .remove("focus_gesture");
        })
        .unwrap();
        Router::with_context(c, l, axes(), context)
    }
    fn plain(context: &str) -> Router {
        let (c, l) = crate::config::tests::fixture();
        Router::with_context(c, l, axes(), context)
    }
    fn key(r: &mut Router, c: u16, v: i32) -> Output {
        r.event(Event::new(EV_KEY, c, v), 0)
    }
    fn abs(r: &mut Router, c: u16, v: i32) -> Output {
        let mut o = r.event(Event::new(EV_ABS, c, v), 0);
        o.extend(r.event(Event::new(EV_SYN, SYN_REPORT, 0), 0));
        o
    }
    fn wait(r: &mut Router, ms: u64) -> Output {
        let mut o = Output::default();
        for _ in 0..ms / 10 {
            o.extend(r.tick(0.01));
        }
        o
    }
    fn tap(r: &mut Router, c: u16, ms: u64) -> Output {
        let mut o = key(r, c, 1);
        o.extend(wait(r, ms));
        o.extend(key(r, c, 0));
        o
    }

    fn captured_terminal(tmux: bool) -> Router {
        let mut r = nav("terminal");
        r.set_tmux(tmux);
        r.set_capture_navigation_context(Some("terminal"));
        r.set_focus("terminal", Some(("0xterminal".into(), 42)), false);
        assert_eq!(key(&mut r, NORTH, 1).actions, vec!["input:window on"]);
        assert!(key(&mut r, NORTH, 0).actions.is_empty());
        r.confirm_capture_target("0xterminal");
        r.set_focus("app-capture", Some(("0xterminal".into(), 42)), false);
        r
    }
    #[test]
    fn captured_terminal_delivers_navigation_and_keeps_ownership_without_raw_pad_frames() {
        for tmux in [false, true] {
            let mut r = captured_terminal(tmux);
            assert!(r.app_owned() && r.captured_navigation() && r.nav_active());
            assert_eq!(
                r.nav_profile().as_deref(),
                Some(if tmux {
                    "terminal_tmux"
                } else {
                    "terminal_plain"
                })
            );
            let o = tap(&mut r, SOUTH, 50);
            assert_eq!(o.actions, vec!["key:ENTER"]);
            assert!(o.app.is_empty() && o.pointer.is_empty());
            assert_eq!(tap(&mut r, SOUTH, 600).actions, vec!["key:SHIFT+ENTER"]);
            assert_eq!(
                tap(&mut r, TR2, 50).actions,
                vec![if tmux {
                    "tmux:key n"
                } else {
                    "key:SHIFT+PAGEDOWN"
                }]
            );
            let o = abs(&mut r, 3, 32767);
            assert_eq!(
                o.actions,
                vec![if tmux {
                    "tmux:focus right"
                } else {
                    "hypr:movefocus right"
                }]
            );
            assert!(o.app.is_empty());
            assert_eq!(r.axis_sink(3), Sink::Nav);
            assert!(r.event(Event::new(EV_SYN, SYN_REPORT, 0), 0).app.is_empty());
            assert!(
                r.app_owned(),
                "navigation delivery never releases ownership"
            );
        }
        let mut r = captured_terminal(true);
        assert_eq!(tap(&mut r, GUIDE, 50).actions, vec!["tmux:menu"]);
        let mut out = key(&mut r, GUIDE, 1);
        out.extend(key(&mut r, LB, 1));
        out.extend(key(&mut r, LB, 0));
        out.extend(key(&mut r, GUIDE, 0));
        assert!(
            out.app.is_empty() && out.actions.is_empty(),
            "Guide must not leak raw input or invoke the host layer"
        );
        assert_eq!(key(&mut r, NORTH, 1).actions, vec!["input:window on"]);
        assert_eq!(wait(&mut r, 500).actions, vec!["input:window fullscreen"]);
        assert!(key(&mut r, NORTH, 0).actions.is_empty());
    }
    #[test]
    fn captured_terminal_half_capture_keeps_nav_buttons_and_explicit_pointer_escape() {
        let mut r = captured_terminal(true);
        key(&mut r, GUIDE, 1);
        assert_eq!(key(&mut r, SELECT, 1).actions, vec!["input:cursor on"]);
        key(&mut r, SELECT, 0);
        key(&mut r, GUIDE, 0);
        assert!(r.half_capture() && r.captured_navigation());
        let o = tap(&mut r, SOUTH, 50);
        assert_eq!(o.actions, vec!["key:ENTER"]);
        assert!(
            o.app.is_empty(),
            "terminal buttons never become raw gamepad input"
        );
        assert_eq!(r.axis_sink(3), Sink::Pointer);
        assert!(abs(&mut r, 3, 32767).app.is_empty());
        assert!(wait(&mut r, 20)
            .pointer
            .iter()
            .any(|e| e.kind == EV_REL && e.code == 0 && e.value > 0));
        assert_eq!(key(&mut r, START, 1).actions, vec!["input:cursor off"]);
        key(&mut r, START, 0);
        assert!(r.app_owned() && !r.half_capture() && r.nav_active());
        assert_eq!(tap(&mut r, TR2, 50).actions, vec!["tmux:key n"]);
    }
    #[test]
    fn captured_terminal_recapture_preserves_room_return_and_guide_never_uses_the_host_layer() {
        let mut r = nav("desktop-3d");
        r.set_tmux(true);
        r.set_capture_navigation_context(Some("terminal"));
        r.set_focus("desktop-3d", Some(("0xroom-terminal".into(), 42)), true);
        tap(&mut r, NORTH, 50);
        r.confirm_capture_target("0xroom-terminal");
        r.set_focus("app-capture", Some(("0xroom-terminal".into(), 42)), true);
        assert!(r.captured_navigation() && r.room_capture);
        tap(&mut r, NORTH, 50);
        assert!(
            r.room_capture,
            "recapture must preserve the room release path"
        );
        r.global.controller.captured_system_layer = true;
        key(&mut r, GUIDE, 1);
        assert!(
            !r.system_layer_held(),
            "captured terminal navigation cannot use the global Guide layer"
        );
        let out = tap(&mut r, LB, 50);
        assert!(out.actions.is_empty() && out.app.is_empty());
        key(&mut r, GUIDE, 0);
        key(&mut r, L3, 1);
        key(&mut r, R3, 1);
        assert!(wait(&mut r, 500).actions.is_empty());
        assert!(key(&mut r, L3, 0).actions.is_empty());
        assert_eq!(key(&mut r, R3, 0).actions, vec!["input:window off"]);
        assert!(r.room(), "held release returns to the room");
    }
    #[test]
    fn captured_navigation_is_explicit_and_game_vm_raw_capture_remains_unchanged() {
        for context in ["terminal", "game", "vm-passthrough"] {
            let mut r = nav(context);
            r.set_focus(context, Some(("0xraw".into(), 7)), false);
            tap(&mut r, NORTH, 50);
            assert!(r.app_owned() && !r.captured_navigation() && !r.nav_active());
            let out = tap(&mut r, SOUTH, 50);
            assert!(out.actions.is_empty());
            assert!(
                out.app.contains(&Event::new(EV_KEY, SOUTH, 1))
                    && out.app.contains(&Event::new(EV_KEY, SOUTH, 0))
            );
            assert_eq!(r.axis_sink(3), Sink::App);
        }
        let mut r = captured_terminal(true);
        key(&mut r, SOUTH, 1);
        r.set_capture_navigation_context(None);
        assert!(!r.captured_navigation() && r.app_owned());
        assert!(
            key(&mut r, SOUTH, 0).actions.is_empty(),
            "old nav tap cannot resolve after delivery changes"
        );
    }
    #[test]
    fn captured_terminal_focus_and_profile_changes_cancel_pending_navigation_and_release_still_works(
    ) {
        let mut r = captured_terminal(true);
        key(&mut r, SOUTH, 1);
        r.set_focus("app-capture", Some(("0xother".into(), 43)), false);
        assert!(wait(&mut r, 600).actions.is_empty());
        assert!(key(&mut r, SOUTH, 0).actions.is_empty());
        key(&mut r, SOUTH, 1);
        r.set_tmux(false);
        assert!(wait(&mut r, 600).actions.is_empty());
        assert!(key(&mut r, SOUTH, 0).actions.is_empty());
        key(&mut r, L3, 1);
        key(&mut r, R3, 1);
        assert!(wait(&mut r, 500).actions.is_empty());
        assert!(key(&mut r, L3, 0).actions.is_empty());
        assert_eq!(key(&mut r, R3, 0).actions, vec!["input:window off"]);
        assert!(!r.app_owned() && !r.captured_navigation());
    }
    #[test]
    fn south_tap_is_enter_and_hold_is_shift_enter_once() {
        let mut r = nav("desktop-2d");
        assert_eq!(r.nav_profile().as_deref(), Some("desktop"));
        let o = tap(&mut r, SOUTH, 100);
        assert_eq!(o.actions, vec!["key:ENTER"]);
        assert!(
            o.app.is_empty(),
            "an uncaptured window gets nav, not the virtual pad"
        );
        let o = tap(&mut r, SOUTH, 600);
        assert_eq!(
            o.actions,
            vec!["key:SHIFT+ENTER"],
            "the hold fires once at capture_hold_ms; the release adds nothing"
        );
        assert_eq!(
            tap(&mut r, SOUTH, 2000).actions,
            vec!["key:SHIFT+ENTER"],
            "a 2 s hold never repeats"
        );
        assert_eq!(tap(&mut r, EAST, 50).actions, vec!["key:ESC"]);
        assert_eq!(tap(&mut r, 0x134, 50).actions, vec!["key:TAB"]);
    }
    #[test]
    fn hat_dpad_repeats_400_60_and_stops_on_focus_change() {
        let mut r = nav("desktop-2d");
        let mut o = abs(&mut r, 17, -1);
        o.extend(wait(&mut r, 1000));
        assert_eq!(
            o.actions,
            vec!["key:UP"; 12],
            "1 + (1 + floor((1000-400)/60)) arrows"
        );
        assert!(abs(&mut r, 17, 0).actions.is_empty());
        // INV-6: a held arrow stops repeating the moment focus moves to another window, of any class.
        let mut r = nav("desktop-2d");
        r.set_focus("desktop-2d", Some(("0xa".into(), 1)), false);
        abs(&mut r, 16, 1);
        let before = wait(&mut r, 500).actions.len();
        r.set_focus("game", Some(("0xgame".into(), 2)), false);
        assert!(
            wait(&mut r, 1000).actions.is_empty(),
            "repeat must stop on a focus change ({before} before)"
        );
        assert!(
            abs(&mut r, 16, 0).actions.is_empty(),
            "the release in the next window fires nothing"
        );
        // ... and on a capture.
        let mut r = nav("desktop-2d");
        abs(&mut r, 16, -1);
        wait(&mut r, 450);
        r.set_focus("app-capture", None, false);
        let o = wait(&mut r, 500);
        assert!(o.actions.is_empty());
    }
    #[test]
    fn every_uncaptured_window_kind_navigates_alike() {
        for context in ["desktop-2d", "game", "vm-passthrough"] {
            let mut r = nav(context);
            assert_eq!(
                tap(&mut r, SOUTH, 50).actions,
                vec!["key:ENTER"],
                "{context}"
            );
            assert_eq!(
                tap(&mut r, TR2, 50).actions,
                vec!["key:CTRL+TAB"],
                "{context}"
            );
            assert_eq!(
                tap(&mut r, NORTH, 50).actions,
                vec!["input:window on"],
                "North still captures in {context}"
            );
        }
        // Negative control: without nav (the pre-I2 SSOT) an uncaptured window got raw pad buttons.
        let mut r = plain("desktop-2d");
        let o = tap(&mut r, SOUTH, 50);
        assert!(o.actions.is_empty() && o.app.contains(&Event::new(EV_KEY, SOUTH, 1)));
        // O-1 revert line: desktop-2d = "vpad" forwards the pad again.
        let (c, l) = crate::config::tests::fixture_nav_with(|v| {
            v["keybindings"]["global"]["controller"]["nav"]["contexts"]["desktop-2d"] =
                "vpad".into();
        });
        let mut r = Router::with_context(c, l, axes(), "desktop-2d");
        let o = tap(&mut r, SOUTH, 50);
        assert!(
            o.actions.is_empty() && o.app.contains(&Event::new(EV_KEY, SOUTH, 1)),
            "vpad forwards"
        );
    }
    #[test]
    fn captured_and_room_never_navigate() {
        let mut r = nav("app-capture");
        assert!(r.nav_profile().is_none());
        let o = tap(&mut r, SOUTH, 50);
        assert!(o.actions.is_empty() && o.app.contains(&Event::new(EV_KEY, SOUTH, 1)));
        let mut r = nav("desktop-3d");
        assert!(
            tap(&mut r, SOUTH, 50).actions.is_empty() && r.pad.sequences[2] == 1,
            "the room keeps South = jump"
        );
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
        assert_eq!(
            tap(&mut r, SOUTH, 50).actions,
            vec!["key:ENTER"],
            "inherits desktop"
        );
        assert_eq!(tap(&mut r, SELECT, 50).actions, vec!["tmux:key w"]);
        // A tmux client appearing mid-hold flushes the plain profile's timers (INV-6).
        let mut r = nav("terminal");
        key(&mut r, SOUTH, 1);
        wait(&mut r, 200);
        r.set_tmux(true);
        assert!(wait(&mut r, 600).actions.is_empty() && key(&mut r, SOUTH, 0).actions.is_empty());
        // tmux: never fires without a client: the plain profile has no tmux: value at all.
        let plain = nav("terminal");
        let p = &plain.global.controller.nav.profiles["terminal_plain"];
        assert!(p
            .values()
            .all(|v| v.actions().iter().all(|a| !a.starts_with("tmux:"))));
    }
    #[test]
    fn right_stick_flicks_focus_once_per_deflection() {
        let mut r = nav("desktop-2d");
        let mut o = abs(&mut r, 3, 30000);
        o.extend(wait(&mut r, 1500));
        o.extend(abs(&mut r, 3, 25000));
        assert_eq!(
            o.actions,
            vec!["hypr:movefocus right"],
            "one flick, no repeat"
        );
        assert!(
            abs(&mut r, 3, 15000).actions.is_empty(),
            "between flick_off and flick_on: still disarmed"
        );
        abs(&mut r, 3, 0);
        assert_eq!(abs(&mut r, 4, -30000).actions, vec!["hypr:movefocus up"]);
        abs(&mut r, 4, 0);
        r.set_tmux(true);
        r.set_context("terminal");
        assert_eq!(abs(&mut r, 3, -30000).actions, vec!["tmux:focus left"]);
        assert!(
            abs(&mut r, 3, 15000).actions.is_empty(),
            "small reverse deflection is no flick"
        );
    }
    #[test]
    fn cursor_owned_right_stick_moves_without_window_or_pane_focus() {
        for (context, tmux) in [
            ("desktop-2d", false),
            ("game", false),
            ("vm-passthrough", false),
            ("terminal", false),
            ("terminal", true),
        ] {
            let mut r = nav(context);
            r.set_tmux(tmux);
            tap(&mut r, START, 10);
            assert!(r.cursor, "{context}");
            assert_eq!(r.axis_sink(3), Sink::Pointer);
            let mut output = Output::default();
            for (axis, value) in [(3, 30000), (3, -30000), (3, 0), (4, 30000), (4, -30000)] {
                output.extend(abs(&mut r, axis, value));
                output.extend(wait(&mut r, 100));
            }
            assert!(
                output.actions.is_empty(),
                "{context} tmux={tmux}: pointer motion also navigated: {:?}",
                output.actions
            );
            assert!(
                output
                    .pointer
                    .iter()
                    .any(|e| e.kind == EV_REL && e.value != 0),
                "{context}: this must exercise actual pointer output"
            );
        }
    }

    #[test]
    fn cursor_stick_does_not_focus_when_pointer_mode_ends_until_centered() {
        let mut r = nav("desktop-2d");
        tap(&mut r, START, 10);
        assert!(abs(&mut r, 3, 30000).actions.is_empty());
        wait(&mut r, 100);
        let toggle = tap(&mut r, START, 10);
        assert_eq!(toggle.actions, vec!["input:cursor off"]);
        assert!(abs(&mut r, 3, 30000).actions.is_empty());
        assert!(wait(&mut r, 200).actions.is_empty());
        assert!(abs(&mut r, 3, 0).actions.is_empty());
        assert_eq!(abs(&mut r, 3, 30000).actions, vec!["hypr:movefocus right"]);
    }

    #[test]
    fn left_stick_scrolls_both_wheels_analog() {
        let mut r = nav("desktop-2d");
        abs(&mut r, 1, -32767);
        let o = wait(&mut r, 200);
        let wheel: i32 = o
            .pointer
            .iter()
            .filter(|e| e.kind == EV_REL && e.code == 8)
            .map(|e| e.value)
            .sum();
        assert!(wheel > 0, "stick up = wheel up ({wheel})");
        assert!(o.actions.is_empty() && o.app.is_empty());
        abs(&mut r, 1, 0);
        abs(&mut r, 0, 32767);
        let o = wait(&mut r, 200);
        assert!(
            o.pointer
                .iter()
                .any(|e| e.kind == EV_REL && e.code == 6 && e.value > 0),
            "stick right = REL_HWHEEL right"
        );
        assert!(o.pointer.iter().all(|e| !(e.kind == EV_REL && e.code == 8)));
    }
    #[test]
    fn cursor_mode_keeps_button_meanings_and_adds_hwheel() {
        let mut r = nav("desktop-2d");
        assert_eq!(tap(&mut r, START, 10).actions, vec!["input:cursor on"]);
        assert_eq!(
            tap(&mut r, SOUTH, 50).actions,
            vec!["key:ENTER"],
            "C-11: South is Enter in cursor mode"
        );
        let o = key(&mut r, TR2, 1);
        assert!(
            o.pointer.contains(&Event::new(EV_KEY, 0x110, 1)) && o.actions.is_empty(),
            "RT is still the left click"
        );
        let o = wait(&mut r, 800);
        assert!(o.actions.iter().all(|a| a != "key:CTRL+TAB"));
        key(&mut r, TR2, 0);
        abs(&mut r, 0, 32767);
        assert!(wait(&mut r, 100)
            .pointer
            .iter()
            .any(|e| e.kind == EV_REL && e.code == 6 && e.value > 0));
        abs(&mut r, 0, 0);
        assert_eq!(
            abs(&mut r, 17, 1).actions,
            vec!["key:DOWN"],
            "the d-pad is arrows in cursor mode"
        );
        // Negative: the pre-I2 router dropped buttons in cursor mode.
        let mut p = plain("desktop-2d");
        tap(&mut p, START, 10);
        let o = tap(&mut p, SOUTH, 50);
        assert!(o.actions.is_empty() && o.app.is_empty());
    }
    #[test]
    fn thumb_clicks_resolve_on_release_and_the_chord_cancels_them() {
        let mut r = nav("desktop-2d");
        assert!(
            key(&mut r, L3, 1).actions.is_empty(),
            "L3 waits for the release"
        );
        assert_eq!(key(&mut r, L3, 0).actions, vec!["key:CTRL+t"]);
        assert_eq!(tap(&mut r, R3, 50).actions, vec!["key:CTRL+0"]);
        // Fast chord: the room toggle only.
        let mut o = r.event(Event::new(EV_KEY, L3, 1), 1000);
        o.extend(r.event(Event::new(EV_KEY, R3, 1), 1100));
        o.extend(key(&mut r, L3, 0));
        o.extend(key(&mut r, R3, 0));
        assert_eq!(o.actions, vec!["mios:hyprland-3d-toggle"]);
        // Slow overlap (outside debounce_ms): no singles and no toggle.
        let mut r = nav("desktop-2d");
        let mut o = r.event(Event::new(EV_KEY, L3, 1), 1000);
        o.extend(wait(&mut r, 400));
        o.extend(r.event(Event::new(EV_KEY, R3, 1), 1400));
        o.extend(key(&mut r, R3, 0));
        o.extend(key(&mut r, L3, 0));
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
        c.controller
            .system_layer
            .insert("BTN_THUMBL".into(), "quickshell:lock".into());
        c.controller
            .system_layer
            .insert("BTN_THUMBR".into(), "mios:toggle_vm_passthrough".into());
        let mut r = Router::with_context(c, l, axes(), "desktop-2d");
        let mut o = key(&mut r, GUIDE, 1);
        for (code, v) in [(L3, 1), (R3, 1), (L3, 0), (R3, 0), (GUIDE, 0)] {
            o.extend(key(&mut r, code, v));
        }
        assert!(o.actions.is_empty(), "{:?}", o.actions);
        let mut o = key(&mut r, GUIDE, 1);
        for (code, v) in [(L3, 1), (L3, 0), (GUIDE, 0)] {
            o.extend(key(&mut r, code, v));
        }
        assert_eq!(
            o.actions,
            vec!["quickshell:lock"],
            "Guide+L3 alone still locks, on release"
        );
    }
    #[test]
    fn guide_tap_opens_the_drawer_only_when_alone_and_short() {
        let mut r = nav("desktop-2d");
        assert_eq!(
            tap(&mut r, GUIDE, 100).actions,
            vec!["quickshell:drawer-toggle"]
        );
        assert!(
            tap(&mut r, GUIDE, 600).actions.is_empty(),
            "a Guide hold is the system-layer modifier"
        );
        let mut o = key(&mut r, GUIDE, 1);
        o.extend(tap(&mut r, LB, 20));
        o.extend(key(&mut r, GUIDE, 0));
        assert_eq!(
            o.actions,
            vec!["hypr:workspace e-1"],
            "Guide+LB is the system layer, never the drawer too"
        );
        // R-26: a Guide press cancels a pending South hold.
        key(&mut r, SOUTH, 1);
        wait(&mut r, 200);
        let mut o = key(&mut r, GUIDE, 1);
        o.extend(wait(&mut r, 600));
        o.extend(key(&mut r, GUIDE, 0));
        o.extend(key(&mut r, SOUTH, 0));
        assert!(o.actions.is_empty(), "{:?}", o.actions);
        // Negative: the pre-I2 router swallowed the uncaptured Guide tap.
        let mut p = plain("desktop-2d");
        assert!(tap(&mut p, GUIDE, 100).actions.is_empty());
    }
    #[test]
    fn analog_only_triggers_get_digital_edges() {
        let mut r = nav("desktop-2d");
        r.analog_triggers = true;
        assert_eq!(abs(&mut r, 5, 200).actions, vec!["key:CTRL+TAB"]);
        let o = wait(&mut r, 760);
        assert_eq!(
            o.actions,
            vec!["key:CTRL+TAB"; 2],
            "500/250 repeat: at 500 and 750 ms"
        );
        assert!(abs(&mut r, 5, 0).actions.is_empty());
        assert_eq!(abs(&mut r, 2, 255).actions, vec!["key:CTRL+SHIFT+TAB"]);
        // A pad with digital triggers (DualSense) gets exactly one action per pull, from the button.
        let mut d = nav("desktop-2d");
        let mut o = abs(&mut d, 5, 200);
        o.extend(key(&mut d, TR2, 1));
        o.extend(key(&mut d, TR2, 0));
        o.extend(abs(&mut d, 5, 0));
        assert_eq!(o.actions, vec!["key:CTRL+TAB"]);
    }
    #[test]
    fn repaired_releases_fire_nothing() {
        let mut r = nav("desktop-2d");
        key(&mut r, SOUTH, 1);
        let o = r.reconcile(&BTreeSet::new(), 5);
        assert!(
            o.actions.is_empty(),
            "a missed release repaired from kernel truth is not a tap"
        );
        key(&mut r, GUIDE, 1);
        assert!(r.reconcile(&BTreeSet::new(), 6).actions.is_empty());
    }
    #[test]
    fn grammar_validation_two_sided() {
        let with = |path: &[&str], val: toml::Value| {
            crate::config::tests::try_fixture_nav_with(|v| {
                let mut t = &mut v["keybindings"]["global"]["controller"]["nav"];
                for p in &path[..path.len() - 1] {
                    t = &mut t[*p];
                }
                t.as_table_mut()
                    .unwrap()
                    .insert(path[path.len() - 1].into(), val);
            })
        };
        // Arrays replace atomically: a user [tap, "none"] really unbinds the hold.
        let (c, l) = with(
            &["desktop", "BTN_SOUTH"],
            toml::Value::Array(vec!["key:SPACE".into(), "none".into()]),
        )
        .unwrap();
        let mut r = Router::with_context(c, l, axes(), "desktop-2d");
        assert!(tap(&mut r, SOUTH, 700).actions.is_empty());
        assert_eq!(tap(&mut r, SOUTH, 50).actions, vec!["key:SPACE"]);
        for (path, val, why) in [
            (
                vec!["desktop", "BTN_SOUTH"],
                toml::Value::from(""),
                "\"\" is not a value",
            ),
            (
                vec!["desktop", "BTN_SOUTH"],
                toml::Value::from("app"),
                "namespace",
            ),
            (
                vec!["terminal_tmux", "BTN_TL2"],
                toml::Value::from("tmux:key r"),
                "prefix-table key",
            ),
            (
                vec!["desktop", "BTN_TL"],
                toml::Value::from("key:ENTER"),
                "capture contract",
            ),
            (
                vec!["desktop", "BTN_MODE"],
                toml::Value::Array(vec!["key:ENTER".into(), "key:ESC".into()]),
                "Guide hold",
            ),
            (
                vec!["desktop", "BTN_SOUTH"],
                toml::Value::from("pointer:wheel-up"),
                "only LS_*/RS_*",
            ),
            (
                vec!["desktop", "BTN_SOUTH"],
                toml::Value::from("key:HYPER+a"),
                "unknown modifier",
            ),
            (
                vec!["desktop", "inherits"],
                toml::Value::from("terminal_plain"),
                "cycle",
            ),
            (
                vec!["repeat", "RS_LEFT"],
                toml::Value::Array(vec![400.into(), 60.into()]),
                "never repeat",
            ),
            (
                vec!["contexts", "app-capture"],
                toml::Value::from("desktop"),
                "not an uncaptured",
            ),
            (
                vec!["contexts", "desktop-2d"],
                toml::Value::from("missing"),
                "no profile",
            ),
        ] {
            let e = with(&path, val).err().unwrap_or_default();
            assert!(e.contains(why), "{path:?}: {e}");
        }
    }
    #[test]
    fn axis_owner_property_holds_with_nav() {
        // INV-5 with the nav sink in play: the room packet and host wheel/pointer never share a frame.
        let mut g = 12345u64;
        let mut next = || {
            g = g
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            g >> 33
        };
        for seed in 0..24 {
            let mut r = nav("desktop-2d");
            let _ = seed;
            for _ in 0..300 {
                let mut o = Output::default();
                match next() % 6 {
                    0 => {
                        let c = [
                            "desktop-2d",
                            "desktop-3d",
                            "terminal",
                            "game",
                            "app-capture",
                        ][(next() % 5) as usize];
                        o.extend(r.set_focus(
                            c,
                            Some((format!("0x{}", next() % 3), 1)),
                            c == "desktop-3d",
                        ));
                    }
                    1 => {
                        let b = [SOUTH, START, L3, R3, NORTH, GUIDE][(next() % 6) as usize];
                        o.extend(key(&mut r, b, (next() % 2) as i32));
                    }
                    _ => {
                        let code = [0u16, 1, 3, 4][(next() % 4) as usize];
                        o.extend(abs(&mut r, code, (next() % 65536) as i32 - 32768));
                    }
                }
                o.extend(r.tick(0.01));
                let pad_live =
                    r.pad.lx != 0.0 || r.pad.ly != 0.0 || r.pad.rx != 0.0 || r.pad.ry != 0.0;
                let host = o.pointer.iter().any(|e| e.kind == EV_REL);
                let app = o.app.iter().any(|e| {
                    e.kind == EV_ABS
                        && [0, 1, 3, 4].contains(&e.code)
                        && e.value.unsigned_abs() > 4000
                });
                assert!(
                    !(pad_live && (host || app)) && !(host && app),
                    "context {} owned {} cursor {}",
                    r.context,
                    r.app_owned(),
                    r.cursor
                );
            }
        }
    }
}

#[cfg(test)]
mod console_contract {
    //! I3: the Linux console (tests.md C-16, C-17, R-08, R-09, console_plain, O-6).
    use super::*;
    const SOUTH: u16 = 0x130;
    const WEST: u16 = 0x134;
    const NORTH: u16 = 0x133;
    const LB: u16 = 0x136;
    const TR2: u16 = 0x139;
    const START: u16 = 0x13b;
    const GUIDE: u16 = 0x13c;
    const L3: u16 = 0x13d;
    const R3: u16 = 0x13e;
    fn axes() -> BTreeMap<u16, Axis> {
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
        for c in [2, 5] {
            axes.insert(
                c,
                Axis {
                    min: 0,
                    max: 255,
                    value: 0,
                },
            );
        }
        for c in [16, 17] {
            axes.insert(
                c,
                Axis {
                    min: -1,
                    max: 1,
                    value: 0,
                },
            );
        }
        axes
    }
    fn vt(tmux: bool) -> Router {
        let (c, l) = crate::config::tests::fixture_nav();
        let mut r = Router::new(c, l, axes());
        r.tmux = tmux;
        r.set_context("console");
        r
    }
    fn key(r: &mut Router, c: u16, v: i32) -> Output {
        r.event(Event::new(EV_KEY, c, v), 0)
    }
    fn abs(r: &mut Router, c: u16, v: i32) -> Output {
        let mut o = r.event(Event::new(EV_ABS, c, v), 0);
        o.extend(r.event(Event::new(EV_SYN, SYN_REPORT, 0), 0));
        o
    }
    fn wait(r: &mut Router, ms: u64) -> Output {
        let mut o = Output::default();
        for _ in 0..ms / 10 {
            o.extend(r.tick(0.01));
        }
        o
    }
    fn tap(r: &mut Router, c: u16, ms: u64) -> Output {
        let mut o = key(r, c, 1);
        o.extend(wait(r, ms));
        o.extend(key(r, c, 0));
        o
    }

    #[test]
    fn console_profiles_follow_the_vt_tmux_client() {
        assert_eq!(vt(true).nav_profile().as_deref(), Some("console_tmux"));
        assert_eq!(vt(false).nav_profile().as_deref(), Some("console_plain"));
        let mut r = vt(true);
        assert_eq!(
            tap(&mut r, SOUTH, 600).actions,
            vec!["key:ENTER"],
            "VT South: Enter on press, no Shift+Enter hold"
        );
        assert_eq!(tap(&mut r, WEST, 50).actions, vec!["key:TAB"]);
        assert_eq!(
            tap(&mut r, WEST, 600).actions,
            vec!["key:SHIFT+TAB"],
            "O-6: the desktop stroke; the VT keymap makes it ESC TAB (kcbt)"
        );
        assert_eq!(
            tap(&mut r, NORTH, 50).actions,
            vec!["tmux:key z"],
            "no compositor: North zooms the pane, never captures"
        );
        assert_eq!(tap(&mut r, LB, 50).actions, vec!["tmux:session-prev"]);
        assert_eq!(tap(&mut r, TR2, 50).actions, vec!["tmux:key n"]);
        let mut o = abs(&mut r, 1, -30000);
        o.extend(wait(&mut r, 800));
        assert_eq!(
            o.actions,
            vec!["tmux:page-up"; 3],
            "left stick up pages (500/250)"
        );
        abs(&mut r, 1, 0);
        assert!(
            r.axis_sink(1) == Sink::Nav && r.pad.lx == 0.0,
            "no room packet on a VT"
        );
    }
    #[test]
    fn console_plain_projects_only_safe_keys() {
        let mut r = vt(false);
        let mut o = abs(&mut r, 1, -30000);
        o.extend(wait(&mut r, 800));
        assert_eq!(
            o.actions,
            vec!["key:UP"; 3],
            "C-18: the left stick is arrows on a bare VT (500/250), never PageUp"
        );
        assert!(o.pointer.is_empty());
        abs(&mut r, 1, 0);
        assert_eq!(
            tap(&mut r, LB, 50).actions,
            vec!["key:SHIFT+TAB"],
            "bumpers = BTab / Tab"
        );
        assert_eq!(tap(&mut r, 0x137, 50).actions, vec!["key:TAB"]);
        assert_eq!(
            tap(&mut r, TR2, 50).actions,
            vec!["key:PAGEDOWN"],
            "triggers = PageUp / PageDown"
        );
        assert_eq!(tap(&mut r, 0x138, 50).actions, vec!["key:PAGEUP"]);
        for b in [NORTH, GUIDE, L3] {
            assert!(
                tap(&mut r, b, 50).actions.is_empty(),
                "{b:#x} is none on a bare VT"
            );
        }
        let (c, _) = crate::config::tests::fixture_nav();
        for (input, v) in &c.controller.nav.profiles["console_plain"] {
            for a in v.actions() {
                assert!(
                    a == "none"
                        || a == "mios:graphical-vt"
                        || a == "pointer:cursor"
                        || [
                            "key:ENTER",
                            "key:ESC",
                            "key:CTRL+c",
                            "key:TAB",
                            "key:SHIFT+TAB",
                            "key:UP",
                            "key:DOWN",
                            "key:LEFT",
                            "key:RIGHT",
                            "key:PAGEUP",
                            "key:PAGEDOWN"
                        ]
                        .contains(&a),
                    "{}: {a}",
                    input.name()
                );
            }
        }
    }
    #[test]
    fn toggle_chord_switches_to_the_graphical_vt_never_the_room() {
        for tmux in [true, false] {
            let mut r = vt(tmux);
            key(&mut r, L3, 1);
            let o = key(&mut r, R3, 1);
            assert_eq!(o.actions, vec!["mios:graphical-vt"], "tmux={tmux}");
            let mut o = wait(&mut r, 800);
            o.extend(key(&mut r, L3, 0));
            o.extend(key(&mut r, R3, 0));
            assert!(
                o.actions.is_empty(),
                "the hold adds nothing and no single click fires"
            );
        }
        // Negative: on the desktop the same chord still toggles the room.
        let (c, l) = crate::config::tests::fixture_nav();
        let mut r = Router::with_context(c, l, axes(), "desktop-2d");
        key(&mut r, L3, 1);
        assert_eq!(key(&mut r, R3, 1).actions, vec!["mios:hyprland-3d-toggle"]);
    }
    #[test]
    fn guide_is_no_modifier_on_a_vt() {
        let mut r = vt(true);
        assert_eq!(
            tap(&mut r, GUIDE, 50).actions,
            vec!["tmux:menu"],
            "Guide tap = the MiOS tmux menu"
        );
        let mut o = key(&mut r, GUIDE, 1);
        o.extend(tap(&mut r, LB, 50));
        o.extend(key(&mut r, GUIDE, 0));
        assert_eq!(
            o.actions,
            vec!["tmux:session-prev"],
            "R-08: Guide+LB is plain LB on a VT, and no Guide tap"
        );
        // Negative: with a compositor, Guide+LB is the system layer.
        let (c, l) = crate::config::tests::fixture_nav();
        let mut r = Router::with_context(c, l, axes(), "terminal");
        let mut o = key(&mut r, GUIDE, 1);
        o.extend(key(&mut r, LB, 1));
        assert_eq!(o.actions, vec!["hypr:workspace e-1"]);
    }
    #[test]
    fn start_hold_drives_the_pointer_and_back() {
        let mut r = vt(true);
        assert_eq!(
            tap(&mut r, START, 50).actions,
            vec!["tmux:key y"],
            "VT tmux: Start tap = copy mode"
        );
        assert_eq!(
            tap(&mut r, START, 600).actions,
            vec!["input:cursor on"],
            "Start hold = cursor mode"
        );
        assert_eq!(tap(&mut r, START, 600).actions, vec!["input:cursor off"]);
        let mut r = vt(false);
        assert_eq!(
            tap(&mut r, START, 50).actions,
            vec!["input:cursor on"],
            "a bare VT: Start = cursor mode, as on the desktop"
        );
        abs(&mut r, 3, 30000);
        let o = wait(&mut r, 100);
        assert!(
            o.pointer
                .iter()
                .any(|e| e.kind == EV_REL && e.code == 0 && e.value > 0),
            "the right stick moves the pointer"
        );
        abs(&mut r, 3, 0);
        let mut o = key(&mut r, TR2, 1);
        assert!(o.actions.is_empty());
        o.extend(wait(&mut r, 20));
        assert!(
            o.pointer.contains(&Event::new(EV_KEY, 0x110, 1)),
            "RT = left click (gpm select)"
        );
        key(&mut r, TR2, 0);
        wait(&mut r, 20);
        assert_eq!(
            tap(&mut r, SOUTH, 50).actions,
            vec!["key:ENTER"],
            "buttons keep their nav meaning in cursor mode"
        );
        assert_eq!(tap(&mut r, START, 50).actions, vec!["input:cursor off"]);
        assert!(!r.cursor);
    }
    #[test]
    fn host_controller_bindings_without_contexts_skip_the_console() {
        let (mut c, l) = crate::config::tests::fixture_nav();
        c.bindings = vec![crate::config::Binding {
            always_active: false,
            id: "pad-term".into(),
            tier: 4,
            chord: "BTN_SELECT+BTN_START".into(),
            action: "mios:launch-browser".into(),
            bypass: None,
            contexts: vec![],
            passthrough: vec![],
            scope: "host".into(),
            note: String::new(),
        }];
        assert!(
            c.bindings[0].active_in("desktop-2d") && !c.bindings[0].active_in("console"),
            "R-09 scoped default"
        );
        let mut r = Router::new(c, l, axes());
        r.set_context("console");
        key(&mut r, 0x13a, 1);
        let o = key(&mut r, START, 1);
        assert!(!o.actions.contains(&"mios:launch-browser".to_owned()));
    }
}

#[cfg(test)]
mod guide_layer_contract {
    //! I4 (C-15b): the Guide system layer on every pad shape: HAT d-pads, stick flicks (LS_*/RS_*), analog-only
    //! triggers; one action per press or flick, no pointer under Guide, nothing leaks to a captured app.
    use super::*;
    const GUIDE: u16 = 0x13c;
    fn axes() -> BTreeMap<u16, Axis> {
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
        for c in [2, 5] {
            axes.insert(
                c,
                Axis {
                    min: 0,
                    max: 255,
                    value: 0,
                },
            );
        }
        for c in [16, 17] {
            axes.insert(
                c,
                Axis {
                    min: -1,
                    max: 1,
                    value: 0,
                },
            );
        }
        axes
    }
    /// Legacy module (pre-task3773): strip the vendor focus_gesture table so the
    /// magnitude flicks below keep their original contract.
    fn vendor(context: &str) -> Router {
        let mut v: toml::Value = include_str!("../../../../usr/share/mios/mios.toml")
            .parse()
            .unwrap();
        v["keybindings"]["global"]["controller"]["nav"]
            .as_table_mut()
            .unwrap()
            .remove("focus_gesture");
        let (c, l) = GlobalConfig::from_value(&v).unwrap();
        Router::with_context(c, l, axes(), context)
    }
    fn key(r: &mut Router, c: u16, v: i32) -> Output {
        r.event(Event::new(EV_KEY, c, v), 0)
    }
    fn abs(r: &mut Router, c: u16, v: i32) -> Output {
        let mut o = r.event(Event::new(EV_ABS, c, v), 0);
        o.extend(r.event(Event::new(EV_SYN, SYN_REPORT, 0), 0));
        o
    }
    fn wait(r: &mut Router, ms: u64) -> Output {
        let mut o = Output::default();
        for _ in 0..ms / 10 {
            o.extend(r.tick(0.01));
        }
        o
    }

    #[test]
    fn vendor_layer_declares_every_i4_row() {
        let r = vendor("desktop-2d");
        let l = &r.policy().controller.system_layer;
        assert_eq!(l["BTN_START"], "mios:system-monitor");
        for (k, v) in [
            ("LS_UP", "mios:volume up"),
            ("LS_DOWN", "mios:volume down"),
            ("LS_LEFT", "mios:media previous"),
            ("LS_RIGHT", "mios:media next"),
            ("RS_LEFT", "hypr:movewindow left"),
            ("RS_RIGHT", "hypr:movewindow right"),
            ("RS_UP", "hypr:movewindow up"),
            ("RS_DOWN", "hypr:movewindow down"),
        ] {
            assert_eq!(l[k], v);
        }
    }
    #[test]
    fn hat_under_guide_focuses_once_per_press_and_never_repeats() {
        let mut r = vendor("desktop-2d");
        let mut o = key(&mut r, GUIDE, 1);
        o.extend(abs(&mut r, 16, -1));
        o.extend(wait(&mut r, 1000));
        assert_eq!(
            o.actions,
            vec!["hypr:movefocus left"],
            "one action, no 400/60 repeat under Guide"
        );
        let mut o = abs(&mut r, 16, 0);
        o.extend(abs(&mut r, 17, 1));
        o.extend(abs(&mut r, 17, 0));
        o.extend(abs(&mut r, 17, -1));
        assert_eq!(o.actions, vec!["hypr:movefocus down", "hypr:movefocus up"]);
        let o = key(&mut r, GUIDE, 0);
        assert!(
            o.actions.is_empty() && o.pointer.is_empty() && o.app.is_empty(),
            "{o:?}"
        );
    }
    #[test]
    fn a_quick_guide_hat_is_never_also_a_guide_tap() {
        let mut r = vendor("desktop-2d");
        let mut o = key(&mut r, GUIDE, 1);
        o.extend(abs(&mut r, 16, 1));
        o.extend(abs(&mut r, 16, 0));
        o.extend(key(&mut r, GUIDE, 0));
        assert_eq!(
            o.actions,
            vec!["hypr:movefocus right"],
            "no drawer on the Guide release"
        );
    }
    #[test]
    fn hat_without_guide_reaches_a_captured_app_unchanged_and_guide_stays_the_apps() {
        let mut r = vendor("app-capture");
        let o = abs(&mut r, 16, -1);
        assert!(o.actions.is_empty());
        assert!(o.app.contains(&Event::new(EV_ABS, 16, -1)), "{o:?}");
        abs(&mut r, 16, 0);
        let mut o = key(&mut r, GUIDE, 1);
        o.extend(abs(&mut r, 16, 1));
        o.extend(key(&mut r, GUIDE, 0));
        assert!(
            o.actions.is_empty(),
            "captured_system_layer=false: the app owns Guide + d-pad"
        );
        assert!(
            o.app.contains(&Event::new(EV_KEY, GUIDE, 1))
                && o.app.contains(&Event::new(EV_ABS, 16, 1)),
            "{o:?}"
        );
    }
    #[test]
    fn left_stick_flicks_set_volume_and_media_once_per_deflection() {
        let mut r = vendor("desktop-2d");
        let mut o = key(&mut r, GUIDE, 1);
        o.extend(abs(&mut r, 1, -32767));
        o.extend(wait(&mut r, 800));
        o.extend(abs(&mut r, 1, -30000));
        assert_eq!(
            o.actions,
            vec!["mios:volume up"],
            "held deflection does not repeat"
        );
        assert!(
            o.pointer.is_empty(),
            "no wheel under Guide: {:?}",
            o.pointer
        );
        let mut o = abs(&mut r, 1, 0);
        o.extend(abs(&mut r, 1, 32767));
        o.extend(abs(&mut r, 1, 0));
        o.extend(abs(&mut r, 0, -32767));
        o.extend(abs(&mut r, 0, 0));
        o.extend(abs(&mut r, 0, 32767));
        assert_eq!(
            o.actions,
            vec!["mios:volume down", "mios:media previous", "mios:media next"]
        );
    }
    #[test]
    fn right_stick_flick_moves_the_window_and_no_pointer_moves_under_guide() {
        let mut r = vendor("desktop-2d");
        let o = key(&mut r, 0x13b, 1);
        assert!(o.actions.iter().any(|a| a == "input:cursor on"), "{o:?}");
        key(&mut r, 0x13b, 0);
        let mut o = key(&mut r, GUIDE, 1);
        o.extend(abs(&mut r, 3, 32767));
        o.extend(wait(&mut r, 300));
        o.extend(abs(&mut r, 5, 255));
        o.extend(wait(&mut r, 100));
        assert_eq!(o.actions, vec!["hypr:movewindow right"]);
        assert!(
            o.pointer
                .iter()
                .all(|e| e.kind == EV_KEY && e.value == 0 || e.kind == EV_SYN),
            "no motion or click under Guide: {:?}",
            o.pointer
        );
        // Guide released with the stick still deflected: cursor mode moves the pointer again, nothing else fires.
        let mut o = key(&mut r, GUIDE, 0);
        o.extend(wait(&mut r, 100));
        assert!(
            o.pointer.iter().any(|e| e.kind == EV_REL && e.code == 0),
            "the pointer is back after Guide: {:?}",
            o.pointer
        );
        assert!(o.actions.is_empty(), "{:?}", o.actions);
    }
    #[test]
    fn a_stick_already_deflected_when_guide_goes_down_does_not_fire() {
        let mut r = vendor("desktop-2d");
        abs(&mut r, 3, 32767);
        let mut o = key(&mut r, GUIDE, 1);
        o.extend(abs(&mut r, 3, 32000));
        assert!(o.actions.is_empty(), "{:?}", o.actions);
        let mut o = abs(&mut r, 3, 0);
        o.extend(abs(&mut r, 3, -32767));
        assert_eq!(
            o.actions,
            vec!["hypr:movewindow left"],
            "re-armed below flick_off"
        );
    }
    #[test]
    fn analog_only_triggers_reach_the_layer_and_digital_pads_fire_once() {
        let mut r = vendor("desktop-2d");
        r.analog_triggers = true;
        let mut o = key(&mut r, GUIDE, 1);
        o.extend(abs(&mut r, 2, 255));
        o.extend(abs(&mut r, 2, 200));
        o.extend(abs(&mut r, 2, 0));
        o.extend(abs(&mut r, 5, 255));
        assert_eq!(
            o.actions,
            vec!["hypr:movetoworkspace e-1", "hypr:movetoworkspace e+1"]
        );
        // A DualSense reports BTN_TL2 and ABS_Z together: the button alone fires.
        let mut r = vendor("desktop-2d");
        let mut o = key(&mut r, GUIDE, 1);
        o.extend(key(&mut r, 0x138, 1));
        o.extend(abs(&mut r, 2, 255));
        assert_eq!(o.actions, vec!["hypr:movetoworkspace e-1"]);
    }
    #[test]
    fn captured_system_layer_delivers_guide_hat_and_flicks_once_never_also_to_the_app() {
        // D2: with captured_system_layer = true the layer reaches a captured app; its HAT/stick/trigger input is then
        // layer input only (as Guide + South is), and the app gets the live axes back when Guide goes up.
        let mut r = vendor("app-capture");
        r.global.controller.captured_system_layer = true;
        let mut o = key(&mut r, GUIDE, 1);
        assert!(
            o.app
                .iter()
                .filter(|e| e.kind == EV_ABS)
                .all(|e| e.value == 0 || [2, 5].contains(&e.code)),
            "rest at the Guide press: {o:?}"
        );
        o = abs(&mut r, 16, -1);
        o.extend(abs(&mut r, 16, 0));
        o.extend(abs(&mut r, 3, 32767));
        o.extend(abs(&mut r, 2, 255));
        assert_eq!(
            o.actions,
            vec!["hypr:movefocus left", "hypr:movewindow right"]
        );
        assert!(
            !o.app.iter().any(|e| e.kind == EV_ABS),
            "no HAT/stick/trigger reaches the app under the layer: {:?}",
            o.app
        );
        let mut o = key(&mut r, GUIDE, 0);
        assert!(
            o.app.contains(&Event::new(EV_ABS, 3, 32767))
                && o.app.contains(&Event::new(EV_ABS, 2, 255)),
            "the app gets the live axes back: {:?}",
            o.app
        );
        assert!(
            !o.app.contains(&Event::new(EV_KEY, GUIDE, 1)),
            "Guide itself stays the layer's"
        );
        o.extend(abs(&mut r, 3, 0));
        assert!(
            o.app.contains(&Event::new(EV_ABS, 3, 0)),
            "ordinary motion reaches the app again"
        );
        assert!(o.actions.is_empty(), "{:?}", o.actions);
    }
    #[test]
    fn guide_stays_no_modifier_on_a_vt() {
        let mut r = vendor("console");
        let mut o = key(&mut r, GUIDE, 1);
        o.extend(abs(&mut r, 16, -1));
        o.extend(abs(&mut r, 1, -32767));
        assert!(
            !o.actions
                .iter()
                .any(|a| a.starts_with("hypr:") || a.starts_with("mios:volume")),
            "{:?}",
            o.actions
        );
    }
}

#[cfg(test)]
mod guide_dock_contract {
    use super::*;
    const GUIDE: u16 = 0x13c;
    fn router(context: &str) -> Router {
        let (mut cfg, legacy) = crate::config::tests::fixture_nav();
        cfg.controller.guide_double_press_ms = 300;
        cfg.controller.guide_double_press_action = "quickshell:drawer-toggle".into();
        cfg.controller
            .nav
            .contexts
            .insert("shell".into(), "shell".into());
        let profile = [
            ("BTN_SOUTH", "key:ENTER"),
            ("BTN_EAST", "key:ESC"),
            ("BTN_WEST", "key:TAB"),
            ("BTN_NORTH", "key:COMPOSE"),
            ("BTN_START", "key:ENTER"),
            ("BTN_MODE", "key:ESC"),
            ("BTN_TL", "key:PAGEUP"),
            ("BTN_TR", "key:PAGEDOWN"),
            ("TOGGLE_CHORD", "key:ESC"),
            ("BTN_DPAD_UP", "key:UP"),
            ("BTN_DPAD_DOWN", "key:DOWN"),
            ("BTN_DPAD_LEFT", "key:LEFT"),
            ("BTN_DPAD_RIGHT", "key:RIGHT"),
            ("LS_LEFT", "key:LEFT"),
            ("LS_RIGHT", "key:RIGHT"),
            ("LS_UP", "key:UP"),
            ("LS_DOWN", "key:DOWN"),
        ]
        .into_iter()
        .map(|(input, action)| (Input::parse(input).unwrap(), Value::Press(action.into())))
        .collect();
        cfg.controller.nav.profiles.insert("shell".into(), profile);
        let mut axes = BTreeMap::new();
        for code in [0, 1, 3, 4] {
            axes.insert(
                code,
                Axis {
                    min: -32768,
                    max: 32767,
                    value: 0,
                },
            );
        }
        for code in [2, 5] {
            axes.insert(
                code,
                Axis {
                    min: 0,
                    max: 255,
                    value: 0,
                },
            );
        }
        for code in [16, 17] {
            axes.insert(
                code,
                Axis {
                    min: -1,
                    max: 1,
                    value: 0,
                },
            );
        }
        Router::with_context(cfg, legacy, axes, context)
    }
    fn key(router: &mut Router, code: u16, value: i32, now: u64) -> Output {
        router.event(Event::new(EV_KEY, code, value), now)
    }
    fn tap(router: &mut Router, code: u16, now: u64) -> Output {
        let mut out = key(router, code, 1, now);
        out.extend(key(router, code, 0, now + 30));
        out
    }
    fn wait(router: &mut Router, ms: u64) -> Output {
        let mut out = Output::default();
        for _ in 0..ms / 10 {
            out.extend(router.tick(0.01));
        }
        out
    }
    #[test]
    fn double_guide_is_one_global_action_in_nav_room_and_raw_capture() {
        for context in ["desktop-2d", "terminal", "desktop-3d", "app-capture"] {
            let mut router = router(context);
            router.global.controller.captured_system_layer = false;
            assert!(tap(&mut router, GUIDE, 100).actions.is_empty());
            let out = tap(&mut router, GUIDE, 200);
            assert_eq!(out.actions, ["quickshell:drawer-toggle"], "{context}");
            assert!(
                !out.app.iter().any(|e| e.kind == EV_KEY && e.code == GUIDE),
                "{context}"
            );
            assert!(
                wait(&mut router, 400).actions.is_empty(),
                "{context}: no delayed first menu"
            );
        }
    }
    #[test]
    fn one_guide_tap_preserves_its_delayed_nav_action_and_raw_pulse() {
        let mut nav = router("terminal");
        nav.set_tmux(true);
        assert!(tap(&mut nav, GUIDE, 100).actions.is_empty());
        assert!(wait(&mut nav, 290).actions.is_empty());
        assert_eq!(wait(&mut nav, 10).actions, ["tmux:menu"]);
        assert!(wait(&mut nav, 400).actions.is_empty());
        let mut raw = router("app-capture");
        raw.global.controller.captured_system_layer = false;
        assert!(tap(&mut raw, GUIDE, 100).app.is_empty());
        let out = wait(&mut raw, 300);
        assert_eq!(
            out.app,
            [
                Event::new(EV_KEY, GUIDE, 1),
                Event::new(EV_KEY, GUIDE, 0),
                Event::new(EV_SYN, SYN_REPORT, 0)
            ]
        );
    }
    #[test]
    fn event_clock_expiry_never_turns_two_slow_taps_into_double() {
        let mut router = router("terminal");
        router.set_tmux(true);
        tap(&mut router, GUIDE, 100);
        let mut out = tap(&mut router, GUIDE, 500);
        out.extend(wait(&mut router, 300));
        assert_eq!(out.actions, ["tmux:menu", "tmux:menu"]);
    }
    #[test]
    fn guide_chords_holds_and_axis_motion_never_open_the_dock() {
        let mut router = router("desktop-2d");
        tap(&mut router, GUIDE, 100);
        key(&mut router, GUIDE, 1, 200);
        let mut out = key(&mut router, 0x136, 1, 220);
        out.extend(key(&mut router, 0x136, 0, 240));
        out.extend(key(&mut router, GUIDE, 0, 260));
        out.extend(wait(&mut router, 600));
        assert_eq!(
            out.actions,
            ["hypr:workspace e-1"],
            "no pending single or double alongside a chord"
        );
        key(&mut router, GUIDE, 1, 1000);
        let mut out = wait(&mut router, 600);
        out.extend(key(&mut router, GUIDE, 0, 1600));
        out.extend(wait(&mut router, 400));
        assert!(out.actions.is_empty());
        tap(&mut router, GUIDE, 2000);
        router.event(Event::new(EV_ABS, 0, 32767), 2100);
        let out = tap(&mut router, GUIDE, 2200);
        assert!(
            out.actions.is_empty(),
            "axis motion cancels the earlier tap, so this is a new first tap"
        );
        assert_eq!(
            wait(&mut router, 300).actions,
            ["quickshell:drawer-toggle"],
            "only the new single remains"
        );
    }
    #[test]
    fn focus_reset_drop_and_kernel_repair_discard_pending_gestures() {
        for boundary in 0..4 {
            let mut router = router("terminal");
            router.set_tmux(true);
            tap(&mut router, GUIDE, 100);
            match boundary {
                0 => {
                    router.set_focus("terminal", Some(("0xnext".into(), 2)), false);
                }
                1 => {
                    router.reset();
                }
                2 => {
                    router.event(Event::new(EV_SYN, SYN_DROPPED, 0), 180);
                }
                _ => {
                    key(&mut router, GUIDE, 1, 180);
                    router.reconcile(&BTreeSet::new(), 200);
                }
            }
            assert!(
                wait(&mut router, 500).actions.is_empty(),
                "boundary {boundary}"
            );
        }
    }
    #[test]
    fn a_late_drop_or_repaired_release_cannot_flush_an_expired_tap() {
        let mut dropped = router("terminal");
        dropped.set_tmux(true);
        tap(&mut dropped, GUIDE, 100);
        let out = dropped.event(Event::new(EV_SYN, SYN_DROPPED, 0), 1000);
        assert!(out.actions.is_empty());
        let mut repaired = router("terminal");
        repaired.set_tmux(true);
        tap(&mut repaired, GUIDE, 100);
        key(&mut repaired, GUIDE, 1, 200);
        let out = repaired.reconcile(&BTreeSet::new(), 1000);
        assert!(out.actions.is_empty());
    }
    #[test]
    fn shell_overlay_owns_keys_without_capturing_the_window_behind_it() {
        let mut router = router("app-capture");
        router.set_shell_overlay(true);
        assert!(router.shell_overlay() && router.app_owned() && !router.captured_navigation());
        assert_eq!(router.nav_profile().as_deref(), Some("shell"));
        for (button, action) in [
            (0x130, "key:ENTER"),
            (0x131, "key:ESC"),
            (0x134, "key:TAB"),
            (0x133, "key:COMPOSE"),
            (0x13b, "key:ENTER"),
            (0x136, "key:PAGEUP"),
            (0x137, "key:PAGEDOWN"),
        ] {
            let out = tap(&mut router, button, 100);
            assert_eq!(out.actions, [action]);
            assert!(out.app.is_empty() && out.pointer.is_empty());
        }
        key(&mut router, 0x13d, 1, 200);
        let out = key(&mut router, 0x13e, 1, 220);
        assert_eq!(out.actions, ["key:ESC"]);
        key(&mut router, 0x13d, 0, 230);
        key(&mut router, 0x13e, 0, 240);
        assert!(wait(&mut router, 1000).actions.is_empty());
        assert!(
            router.app_owned(),
            "opening or closing the shell does not change window ownership"
        );
        for code in [0, 1, 3, 4, 2, 5] {
            assert_eq!(router.axis_sink(code), Sink::Nav);
        }
    }
    #[test]
    fn overlay_guide_chords_are_shell_keys_and_double_guide_toggles_once() {
        let mut router = router("terminal");
        router.set_shell_overlay(true);
        key(&mut router, GUIDE, 1, 100);
        let mut out = tap(&mut router, 0x136, 120);
        out.extend(key(&mut router, GUIDE, 0, 200));
        out.extend(wait(&mut router, 400));
        assert_eq!(out.actions, ["key:PAGEUP"]);
        tap(&mut router, GUIDE, 1000);
        let out = tap(&mut router, GUIDE, 1100);
        assert_eq!(out.actions, ["quickshell:drawer-toggle"]);
        assert!(
            wait(&mut router, 400).actions.is_empty(),
            "no delayed Escape after a double"
        );
    }
    #[test]
    fn overlay_restores_pointer_mode_only_for_the_same_neutral_target() {
        let mut router = router("desktop-2d");
        router.cursor = true;
        router.set_shell_overlay(true);
        assert!(!router.cursor);
        router.set_shell_overlay(false);
        assert!(router.cursor);
        router.set_shell_overlay(true);
        router.set_focus("desktop-2d", Some(("other".into(), 9)), false);
        router.set_shell_overlay(false);
        assert!(!router.cursor);
    }
    #[test]
    fn console_never_defers_guide_for_a_global_ui_gesture() {
        let mut router = router("console");
        router.set_tmux(true);
        router.set_shell_overlay(true);
        assert!(
            !router.shell_overlay(),
            "a graphical overlay is never attached to a VT or headless console route"
        );
        assert_eq!(tap(&mut router, GUIDE, 100).actions, ["tmux:menu"]);
        assert_eq!(tap(&mut router, GUIDE, 200).actions, ["tmux:menu"]);
        assert!(wait(&mut router, 400).actions.is_empty());
    }
}

#[cfg(test)]
mod camera_contract {
    //! Protocol P4-P7 (camera profiles) and the root rulings: delayed focus, key release, chord flush, editor
    //! isolation. Fixtures inject the P1 tables (config::tests::fixture_mon); the vendor SSOT does not carry them yet.
    use super::*;
    const NORTH: u16 = 0x133;
    const TL2: u16 = 0x138;
    const TR2: u16 = 0x139;
    const START: u16 = 0x13b;
    const GUIDE: u16 = 0x13c;
    const L3: u16 = 0x13d;
    const R3: u16 = 0x13e;
    fn axes() -> BTreeMap<u16, Axis> {
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
        for c in [2, 5] {
            axes.insert(
                c,
                Axis {
                    min: 0,
                    max: 255,
                    value: 0,
                },
            );
        }
        for c in [16, 17] {
            axes.insert(
                c,
                Axis {
                    min: -1,
                    max: 1,
                    value: 0,
                },
            );
        }
        axes
    }
    /// A terminal (tmux client) whose verified foreground app is `app`.
    fn mon(app: Option<&str>) -> Router {
        let (c, l) = crate::config::tests::fixture_mon();
        let mut r = Router::with_context(c, l, axes(), "terminal");
        r.set_tmux(true);
        r.set_app(app, None);
        r
    }
    fn key(r: &mut Router, c: u16, v: i32) -> Output {
        r.event(Event::new(EV_KEY, c, v), 0)
    }
    /// The raw centered value whose post-radial component (stick_deadzone 0.12) is `x` with the other at `o`.
    fn raw(x: f64, o: f64) -> i32 {
        let post = x.hypot(o);
        if post == 0.0 {
            return 0;
        }
        let dz = 0.12;
        let c = x * (post * (1.0 - dz) + dz) / post;
        (((c + 1.0) * 65535.0 / 2.0) - 32768.0).round() as i32
    }
    /// One stick sample (x, y in post-radial units), then SYN.
    fn stick(r: &mut Router, right: bool, x: f64, y: f64) -> Output {
        let (xa, ya) = if right { (3, 4) } else { (0, 1) };
        let mut o = r.event(Event::new(EV_ABS, xa, raw(x, y)), 0);
        o.extend(r.event(Event::new(EV_ABS, ya, raw(y, x)), 0));
        o.extend(r.event(Event::new(EV_SYN, SYN_REPORT, 0), 0));
        o
    }
    fn wait(r: &mut Router, ms: u64) -> Output {
        let mut o = Output::default();
        for _ in 0..ms / 10 {
            o.extend(r.tick(0.01));
        }
        o
    }
    fn count(o: &Output, action: &str) -> usize {
        o.actions.iter().filter(|a| *a == action).count()
    }
    fn camera_keys(o: &Output) -> Vec<String> {
        o.actions
            .iter()
            .filter(|a| a.starts_with("key:ALT+"))
            .cloned()
            .collect()
    }
    #[test]
    fn camera_profile_follows_the_verified_app_only_where_it_refines_the_base() {
        let mut r = mon(Some("mon"));
        assert_eq!(r.nav_profile().as_deref(), Some("mon_tmux"));
        assert!(r.camera() && r.app() == Some("mon"));
        r.set_tmux(false);
        assert_eq!(r.nav_profile().as_deref(), Some("mon_plain"));
        r.set_app(None, None);
        assert_eq!(r.nav_profile().as_deref(), Some("terminal_plain"));
        assert!(!r.camera());
        r.set_app(Some("unknown"), None);
        assert!(r.app().is_none(), "only a [nav.apps] name selects anything");
        let (c, l) = crate::config::tests::fixture_mon();
        let mut vt = Router::with_context(c, l, axes(), "console");
        vt.set_app(Some("mon"), None);
        assert_eq!(vt.nav_profile().as_deref(), Some("mon_console_plain"));
        vt.set_tmux(true);
        assert_eq!(vt.nav_profile().as_deref(), Some("mon_console_tmux"));
        let (c, l) = crate::config::tests::fixture_mon();
        let mut desk = Router::with_context(c, l, axes(), "desktop-2d");
        desk.set_app(Some("mon"), None);
        assert_eq!(
            desk.nav_profile().as_deref(),
            Some("desktop"),
            "an app refines only terminal and console"
        );
        assert!(!desk.camera());
    }
    /// Root ruling (backward compatibility): with an invalid app row the router keeps the base profile.
    #[test]
    fn an_invalid_app_layer_leaves_the_base_profile_live() {
        let (c, l) = crate::config::tests::try_fixture_mon_with(|v| {
            v["keybindings"]["global"]["controller"]["nav"]["app_poll_ms"] = 49.into();
            // The base-profile flick this test asserts is the legacy magnitude
            // flick; the vendor gesture policy is out of scope here.
            v["keybindings"]["global"]["controller"]["nav"]
                .as_table_mut()
                .unwrap()
                .remove("focus_gesture");
        })
        .unwrap();
        assert!(c.controller.nav.app_error.is_some());
        let mut r = Router::with_context(c, l, axes(), "terminal");
        r.set_tmux(true);
        r.set_app(Some("mon"), None);
        assert_eq!(r.nav_profile().as_deref(), Some("terminal_tmux"));
        assert!(!r.camera() && r.app().is_none());
        assert_eq!(stick(&mut r, true, 0.8, 0.0).actions, ["tmux:focus right"]);
    }
    /// INPUTD spec test 2 (negative: repeat_for ignoring the app table gives [500, 250], 4 fires).
    #[test]
    fn camera_axis_separable_flicks() {
        let mut r = mon(Some("mon"));
        let mut o = stick(&mut r, false, 0.8, 0.0);
        assert_eq!(o.actions, ["key:ALT+6"], "fires at once");
        o.extend(wait(&mut r, 1000));
        assert_eq!(
            count(&o, "key:ALT+6"),
            1 + 1 + (1000 - 200) / 70,
            "[200, 70]: {:?}",
            o.actions
        );
        assert_eq!(o.actions.len(), 13);
        let o = stick(&mut r, false, 0.2, 0.0);
        assert!(o.actions.is_empty(), "the release fires nothing");
        assert!(wait(&mut r, 1000).actions.is_empty());
    }
    /// INPUTD spec test 3 (negative: the dominant-direction path gives one direction on a diagonal).
    #[test]
    fn camera_diagonal_and_roll() {
        let mut r = mon(Some("mon"));
        let o = stick(&mut r, false, 0.7, -0.7);
        assert_eq!(o.actions, ["key:ALT+6", "key:ALT+8"], "both half-axes");
        let o = wait(&mut r, 270);
        assert_eq!(
            (count(&o, "key:ALT+6"), count(&o, "key:ALT+8")),
            (2, 2),
            "both repeat"
        );
        stick(&mut r, false, 0.0, 0.0);
        let o = stick(&mut r, false, 0.8, 0.0);
        assert_eq!(o.actions, ["key:ALT+6"]);
        let o = stick(&mut r, false, 0.2, -0.8);
        assert_eq!(
            o.actions,
            ["key:ALT+8"],
            "rolling releases right and presses up without centring"
        );
        let o = wait(&mut r, 300);
        assert!(count(&o, "key:ALT+8") > 0 && count(&o, "key:ALT+6") == 0);
        stick(&mut r, false, 0.0, 0.0);
        stick(&mut r, false, 0.8, 0.0);
        let o = stick(&mut r, false, -0.8, 0.0);
        assert_eq!(o.actions, ["key:ALT+4"], "a sign flip between two samples");
        let o = wait(&mut r, 300);
        assert!(count(&o, "key:ALT+4") > 0 && count(&o, "key:ALT+6") == 0);
        // The right stick: orbit on x, tilt on y (RS_UP = Alt+3, RS_DOWN = Alt+1).
        let o = stick(&mut r, true, -0.7, 0.7);
        assert_eq!(o.actions, ["key:ALT+7", "key:ALT+1"]);
    }
    /// INPUTD spec test 4 (negatives: RS in [nav.repeat] is a load error; an ungated app repeat repeats tmux:focus).
    #[test]
    fn rs_repeats_only_in_camera_apps() {
        let mut r = mon(None);
        assert_eq!(r.nav_profile().as_deref(), Some("terminal_tmux"));
        // The base profile now requires a deliberate gesture. It still has no
        // right-stick repeat, unlike the verified camera app below.
        let mut o = wait(&mut r, 100);
        o.extend(stick(&mut r, true, 0.35, 0.0));
        o.extend(wait(&mut r, 40));
        o.extend(stick(&mut r, true, 0.95, 0.0));
        o.extend(wait(&mut r, 40));
        assert!(o.actions.is_empty(), "focus waits for the neutral return");
        o.extend(stick(&mut r, true, 0.0, 0.0));
        assert_eq!(o.actions, ["tmux:focus right"]);
        o.extend(wait(&mut r, 2000));
        o.extend(stick(&mut r, true, 0.75, 0.0));
        o.extend(wait(&mut r, 2000));
        o.extend(stick(&mut r, true, 0.0, 0.0));
        assert_eq!(
            o.actions,
            ["tmux:focus right"],
            "one gesture, never a repeat or partial-travel focus"
        );
        let mut r = mon(Some("mon"));
        let mut o = stick(&mut r, true, 0.8, 0.0);
        o.extend(wait(&mut r, 2000));
        assert_eq!(count(&o, "key:ALT+9"), 1 + 1 + (2000 - 200) / 70);
    }
    /// INPUTD spec test 5 (negative: releasing below flick_on instead of flick_off stops the 0.4 repeat).
    #[test]
    fn camera_deadzone_and_hysteresis() {
        let mut r = mon(Some("mon"));
        let o = r.event(Event::new(EV_ABS, 0, (0.11 * 32767.0) as i32), 0);
        assert!(o.actions.is_empty());
        let mut o = r.event(Event::new(EV_SYN, SYN_REPORT, 0), 0);
        o.extend(wait(&mut r, 300));
        assert!(
            o.actions.is_empty(),
            "raw 0.11 is inside stick_deadzone 0.12"
        );
        let mut o = stick(&mut r, false, 0.55, 0.0);
        o.extend(wait(&mut r, 300));
        assert!(o.actions.is_empty(), "0.55 is below flick_on");
        assert_eq!(stick(&mut r, false, 0.65, 0.0).actions, ["key:ALT+6"]);
        let mut o = stick(&mut r, false, 0.4, 0.0);
        o.extend(wait(&mut r, 300));
        assert!(count(&o, "key:ALT+6") > 0, "0.4 is above flick_off: held");
        let mut o = stick(&mut r, false, 0.25, 0.0);
        o.extend(wait(&mut r, 300));
        assert!(o.actions.is_empty(), "0.25 releases");
    }
    /// INPUTD spec test 6: every flush stops the repeat, the release fires nothing, and a still-deflected stick
    /// re-arms only below flick_off (negative: drop the START cursor-toggle flush in legacy_action).
    #[test]
    fn camera_flush_cases() {
        type Case = fn(&mut Router) -> Output;
        let cases: [(&str, Case); 9] = [
            ("set_app(None)", |r| r.set_app(None, None)),
            ("focus target change", |r| {
                r.set_focus("terminal", Some(("0xother".into(), 9)), false)
            }),
            ("tmux toggle", |r| r.set_tmux(false)),
            ("Guide press", |r| {
                let mut o = key(r, GUIDE, 1);
                o.extend(key(r, GUIDE, 0));
                o
            }),
            ("shell overlay", |r| r.set_shell_overlay(true)),
            ("SYN_DROPPED + resync", |r| {
                let mut o = r.event(Event::new(EV_SYN, SYN_DROPPED, 0), 0);
                let mut axes = axes();
                axes.get_mut(&0).unwrap().value = raw(0.8, 0.0);
                o.extend(r.resync(BTreeSet::new(), axes));
                o
            }),
            ("North capture", |r| {
                let mut o = key(r, NORTH, 1);
                o.extend(key(r, NORTH, 0));
                o
            }),
            ("START cursor toggle", |r| key(r, START, 1)),
            ("L3+R3 chord", |r| {
                let mut o = key(r, L3, 1);
                o.extend(key(r, R3, 1));
                o
            }),
        ];
        for (name, case) in cases {
            let mut r = mon(Some("mon"));
            r.set_capture_navigation_context(Some("terminal"));
            assert_eq!(
                stick(&mut r, false, 0.8, 0.0).actions,
                ["key:ALT+6"],
                "{name}"
            );
            wait(&mut r, 250);
            let mut o = case(&mut r);
            o.extend(wait(&mut r, 600));
            assert!(
                camera_keys(&o).is_empty(),
                "{name}: no held direction survives the flush: {:?}",
                o.actions
            );
            if name == "START cursor toggle" {
                key(&mut r, START, 0);
                key(&mut r, START, 1);
                key(&mut r, START, 0);
            }
            if name == "L3+R3 chord" {
                key(&mut r, L3, 0);
                key(&mut r, R3, 0);
            }
            let o = stick(&mut r, false, 0.8, 0.0);
            assert!(
                camera_keys(&o).is_empty(),
                "{name}: a still-deflected stick stays disarmed: {:?}",
                o.actions
            );
            if r.camera() {
                stick(&mut r, false, 0.2, 0.0);
                assert_eq!(
                    stick(&mut r, false, 0.8, 0.0).actions,
                    ["key:ALT+6"],
                    "{name}: re-armed below flick_off"
                );
            }
        }
    }
    /// INPUTD spec test 7: cursor mode owns both sticks and the triggers; no camera key is projected there.
    #[test]
    fn cursor_mode_owns_sticks_and_triggers() {
        let mut r = mon(Some("mon"));
        assert_eq!(key(&mut r, START, 1).actions, ["input:cursor on"]);
        key(&mut r, START, 0);
        let mut o = stick(&mut r, false, 0.9, 0.0);
        o.extend(stick(&mut r, true, 0.0, -0.9));
        o.extend(wait(&mut r, 500));
        assert!(o.actions.is_empty(), "{:?}", o.actions);
        assert!(o.pointer.iter().any(|e| e.kind == EV_REL && e.code == 1));
        assert!(o.pointer.iter().any(|e| e.kind == EV_REL && e.code == 6));
        let o = key(&mut r, TR2, 1);
        assert!(o.pointer.contains(&Event::new(EV_KEY, 0x110, 1)) && o.actions.is_empty());
        key(&mut r, TR2, 0);
        assert_eq!(key(&mut r, START, 1).actions, ["input:cursor off"]);
        key(&mut r, START, 0);
        assert!(camera_keys(&stick(&mut r, false, 0.9, 0.0)).is_empty());
        stick(&mut r, false, 0.0, 0.0);
        assert_eq!(stick(&mut r, false, 0.9, 0.0).actions, ["key:ALT+6"]);
    }
    /// INPUTD spec test 8 and the chord ruling (negative: drop the L3+R3 overlap flush; Alt+6 keeps repeating).
    #[test]
    fn camera_triggers_and_thumbs() {
        let mut r = mon(Some("mon"));
        let mut o = key(&mut r, TR2, 1);
        o.extend(wait(&mut r, 1000));
        key(&mut r, TR2, 0);
        assert_eq!(count(&o, "key:ALT+EQUAL"), 1 + 1 + (1000 - 250) / 90);
        let mut a = mon(Some("mon"));
        a.analog_triggers = true;
        let mut o = a.event(Event::new(EV_ABS, 2, 200), 0);
        o.extend(wait(&mut a, 1000));
        assert_eq!(count(&o, "key:ALT+MINUS"), 1 + 1 + (1000 - 250) / 90);
        assert!(a.event(Event::new(EV_ABS, 2, 0), 0).actions.is_empty());
        assert!(
            key(&mut r, R3, 1).actions.is_empty(),
            "R3 resolves on release"
        );
        let o = wait(&mut r, 1000);
        assert!(o.actions.is_empty(), "thumbs never repeat");
        assert_eq!(key(&mut r, R3, 0).actions, ["key:ALT+5"]);
        let mut o = key(&mut r, L3, 1);
        o.extend(key(&mut r, L3, 0));
        assert_eq!(o.actions, ["key:ALT+z"]);
        stick(&mut r, true, 0.5, 0.0);
        let mut o = key(&mut r, R3, 1);
        o.extend(key(&mut r, R3, 0));
        assert!(o.actions.is_empty(), "R-23: R3 with its stick deflected");
        stick(&mut r, true, 0.0, 0.0);
        // L3+R3: the toggle chord, neither single, and every held camera direction stops.
        let mut r = mon(Some("mon"));
        stick(&mut r, true, 0.8, 0.0);
        let mut o = r.event(Event::new(EV_KEY, L3, 1), 1000);
        o.extend(r.event(Event::new(EV_KEY, R3, 1), 1050));
        o.extend(wait(&mut r, 800));
        o.extend(key(&mut r, L3, 0));
        o.extend(key(&mut r, R3, 0));
        assert_eq!(o.actions, ["mios:hyprland-3d-toggle"], "{:?}", o.actions);
    }
    /// INPUTD spec test 9: the room, the shell overlay and a captured non-terminal own their input even with app mon.
    #[test]
    fn camera_never_in_room_shell_or_captured_app() {
        let (c, l) = crate::config::tests::fixture_mon();
        let mut r = Router::with_context(c, l, axes(), "desktop-3d");
        r.set_app(Some("mon"), None);
        assert_eq!(r.axis_sink(0), Sink::Room);
        assert!(r.nav_profile().is_none() && !r.camera());
        let mut r = mon(Some("mon"));
        r.set_shell_overlay(true);
        assert_eq!(r.nav_profile().as_deref(), Some("shell"));
        assert!(!r.camera());
        let (c, l) = crate::config::tests::fixture_mon();
        let mut r = Router::with_context(c, l, axes(), "app-capture");
        r.set_app(Some("mon"), None);
        assert_eq!(r.axis_sink(0), Sink::App);
        assert!(!r.camera());
        let o = stick(&mut r, false, 0.9, 0.0);
        assert!(o.actions.is_empty() && !o.app.is_empty(), "raw to the app");
    }
    /// Root ruling (delayed focus): a verification that resolves after a focus change never applies to the new
    /// window (negative: drop the verified_for filter in set_app).
    #[test]
    fn a_delayed_app_result_never_applies_to_the_next_window() {
        let mut r = mon(None);
        let a = ("0xmon".to_owned(), 41u64);
        let b = ("0xshell".to_owned(), 42u64);
        r.set_focus("terminal", Some(a.clone()), false);
        r.set_app(Some("mon"), Some(&a));
        assert!(r.camera());
        stick(&mut r, false, 0.8, 0.0);
        r.set_focus("terminal", Some(b.clone()), false);
        assert!(
            r.app().is_none() && !r.camera(),
            "the focus change clears it"
        );
        let mut o = r.set_app(Some("mon"), Some(&a));
        o.extend(wait(&mut r, 500));
        assert!(
            r.app().is_none() && camera_keys(&o).is_empty(),
            "the result verified for the old window is stale"
        );
        r.set_app(Some("mon"), Some(&b));
        assert!(r.camera(), "a result verified for the new window applies");
        r.set_app(Some("mon"), None);
        assert!(!r.camera(), "an unbound result never applies to a window");
    }
    /// Root rulings (editor isolation, no care or locomotion effect): with no verified app no stick, trigger or
    /// thumb ever projects an Alt camera form, and in the camera profile they project only atomic Alt strokes, never a
    /// plain key that could type into the seed editor, a raw pad event or a pointer event (negative: let the app layer
    /// apply without a verified app).
    #[test]
    fn camera_forms_only_reach_a_verified_app_and_are_atomic_alt_strokes() {
        let inputs: [(u16, u16, i32); 10] = [
            (EV_ABS, 0, 30000),
            (EV_ABS, 0, -30000),
            (EV_ABS, 1, 30000),
            (EV_ABS, 1, -30000),
            (EV_ABS, 3, 30000),
            (EV_ABS, 4, -30000),
            (EV_KEY, TL2, 1),
            (EV_KEY, TR2, 1),
            (EV_KEY, L3, 1),
            (EV_KEY, R3, 1),
        ];
        let mut g = 7u64;
        let mut next = || {
            g = g
                .wrapping_mul(6364136223846793005)
                .wrapping_add(1442695040888963407);
            g >> 33
        };
        for (context, tmux) in [
            ("terminal", true),
            ("terminal", false),
            ("console", true),
            ("console", false),
        ] {
            for app in [None, Some("mon")] {
                let (c, l) = crate::config::tests::fixture_mon();
                let mut r = Router::with_context(c, l, axes(), context);
                r.set_tmux(tmux);
                r.set_app(app, None);
                let mut out = Output::default();
                for _ in 0..400 {
                    let (kind, code, v) = inputs[(next() % 10) as usize];
                    let v = if next() % 2 == 0 { 0 } else { v };
                    out.extend(r.event(Event::new(kind, code, v), 0));
                    out.extend(r.event(Event::new(EV_SYN, SYN_REPORT, 0), 0));
                    out.extend(r.tick(0.03));
                }
                let alt: Vec<_> = camera_keys(&out);
                if app.is_none() {
                    assert!(alt.is_empty(), "{context} tmux={tmux}: {alt:?}");
                    continue;
                }
                assert!(!alt.is_empty(), "{context}: the camera profile projects");
                // The L3+R3 overlap stays the contract's toggle chord (the room toggle, or the graphical VT).
                let chord = ["mios:hyprland-3d-toggle", "mios:graphical-vt"];
                assert!(
                    out.actions
                        .iter()
                        .all(|a| a.starts_with("key:ALT+") || chord.contains(&a.as_str())),
                    "{context} tmux={tmux}: only Alt forms: {:?}",
                    out.actions
                );
                assert!(
                    out.app
                        .iter()
                        .all(|e| e.kind != EV_KEY && (e.kind != EV_ABS || e.value == 0)),
                    "no pad input reaches the terminal, only rest values"
                );
                assert!(out.pointer.is_empty());
                for a in out.actions.iter().filter(|a| a.starts_with("key:")) {
                    let chord = crate::nav::parse_key_chord(&a[4..]).unwrap();
                    assert_eq!(chord.mods, [56], "{a}: exactly Alt");
                    let mut held = BTreeSet::new();
                    for e in crate::nav::stroke(&chord)
                        .iter()
                        .filter(|e| e.kind == EV_KEY)
                    {
                        if e.value == 1 {
                            held.insert(e.code);
                        } else {
                            held.remove(&e.code);
                        }
                    }
                    assert!(held.is_empty(), "{a}: nothing stays held across a change");
                }
            }
        }
    }
}

#[cfg(test)]
mod focus_gesture_contract {
    //! task3773: the deliberate out-and-return RIGHT-stick focus gesture. The vendor
    //! SSOT ships the focus_gesture table, so these fixtures keep it; the legacy
    //! modules above strip it for their pre-gesture magnitude contracts. Units:
    //! thresholds are POST-DEADZONE fractions (1.0 = full deflection); `raw(f)`
    //! converts to the raw axis value through the vendor 0.12 deadzone
    //! (raw = 0.12 + f*0.88), i.e. 15% post-deadzone neutral = ~25.2% physical.
    use super::*;
    const SOUTH: u16 = 0x130;

    fn axes() -> BTreeMap<u16, Axis> {
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
        axes
    }
    fn gesture_router(context: &str, tmux: bool) -> Router {
        let (c, l) = crate::config::tests::fixture_nav();
        let mut r = Router::with_context(c, l, axes(), context);
        r.set_tmux(tmux);
        r
    }
    fn abs(r: &mut Router, c: u16, v: i32) -> Output {
        let mut o = r.event(Event::new(EV_ABS, c, v), 0);
        o.extend(r.event(Event::new(EV_SYN, SYN_REPORT, 0), 0));
        o
    }
    fn wait(r: &mut Router, ms: u64) -> Output {
        let mut o = Output::default();
        for _ in 0..ms / 10 {
            o.extend(r.tick(0.01));
        }
        o
    }
    /// Raw axis value for a post-deadzone magnitude fraction (vendor 0.12 deadzone).
    fn raw(f: f64) -> i32 {
        // Round up so a positive sample requested at rise_pct is not just
        // below that threshold after integer axis quantization.
        ((0.12 + f * 0.88) * 32767.0).ceil() as i32
    }
    /// One complete qualifying gesture on one axis: neutral dwell, fast rise to
    /// `peak`, quick return. The returned Output collects everything that fired.
    fn gesture(r: &mut Router, axis: u16, sign: i32, peak: f64) -> Output {
        let mut o = wait(r, 100);
        o.extend(abs(r, axis, sign * raw(peak)));
        o.extend(wait(r, 60));
        o.extend(abs(r, axis, 0));
        o.extend(wait(r, 30));
        o
    }
    fn actions(o: &Output) -> Vec<String> {
        o.actions.clone()
    }

    #[test]
    fn each_direction_focuses_once_in_tmux_and_plain_profiles() {
        // terminal_tmux: RS is tmux:focus; each deliberate gesture fires exactly once.
        let mut r = gesture_router("terminal", true);
        for (axis, sign, action) in [
            (3u16, 1, "tmux:focus right"),
            (3, -1, "tmux:focus left"),
            (4, 1, "tmux:focus down"),
            (4, -1, "tmux:focus up"),
        ] {
            let o = gesture(&mut r, axis, sign, 0.95);
            assert_eq!(actions(&o), [action], "axis {axis} sign {sign}");
            assert!(
                actions(&wait(&mut r, 600)).is_empty(),
                "cooldown holds: no repeat for {action}"
            );
        }
        // terminal_plain: RS is hypr:movefocus; same contract through the compositor verb.
        let mut r = gesture_router("terminal", false);
        for (axis, sign, action) in [
            (3u16, 1, "hypr:movefocus right"),
            (3, -1, "hypr:movefocus left"),
            (4, 1, "hypr:movefocus down"),
            (4, -1, "hypr:movefocus up"),
        ] {
            let o = gesture(&mut r, axis, sign, 0.95);
            assert_eq!(actions(&o), [action]);
            assert!(actions(&wait(&mut r, 600)).is_empty());
        }
    }

    #[test]
    fn idle_neutral_ticks_arm_without_controller_frames() {
        let mut r = gesture_router("terminal", true);
        assert!(wait(&mut r, 80).actions.is_empty());
        assert!(r.nav.focus.armed, "an idle pad emits no SYN_REPORT frames");
        assert!(abs(&mut r, 3, raw(0.95)).actions.is_empty());
        assert!(wait(&mut r, 50).actions.is_empty());
        let out = abs(&mut r, 3, 0);
        assert_eq!(out.actions, ["tmux:focus right"]);
        assert!(out.app.is_empty() && out.pointer.is_empty());
    }

    #[test]
    fn a_fast_multisample_rise_fires_only_after_the_return() {
        let mut r = gesture_router("terminal", true);
        wait(&mut r, 100);
        let mut out = abs(&mut r, 3, raw(0.3));
        out.extend(wait(&mut r, 30));
        out.extend(abs(&mut r, 3, raw(0.6)));
        out.extend(wait(&mut r, 30));
        out.extend(abs(&mut r, 3, raw(0.95)));
        out.extend(wait(&mut r, 30));
        assert!(out.actions.is_empty(), "the peak is not a focus action");
        out.extend(abs(&mut r, 3, 0));
        out.extend(wait(&mut r, 20));
        assert_eq!(out.actions, ["tmux:focus right"]);
        assert!(out.app.is_empty() && out.pointer.is_empty());
    }

    #[test]
    fn app_profile_changes_discard_a_pending_base_focus_gesture() {
        let (c, l) = crate::config::tests::fixture_mon();
        let mut r = Router::with_context(c, l, axes(), "terminal");
        r.set_tmux(true);
        wait(&mut r, 100);
        abs(&mut r, 3, raw(0.95));
        r.set_app(Some("mon"), None);
        let mut out = abs(&mut r, 3, 0);
        out.extend(wait(&mut r, 100));
        assert!(!out
            .actions
            .iter()
            .any(|a| a.starts_with("tmux:focus ") || a.starts_with("hypr:movefocus ")));
        assert!(r.nav.focus.peak.is_none());
        r.set_app(None, None);
        assert_eq!(gesture(&mut r, 3, 1, 0.95).actions, ["tmux:focus right"]);
    }

    #[test]
    fn profile_changes_do_not_shortcut_the_post_fire_cooldown() {
        let mut r = gesture_router("terminal", true);
        assert_eq!(gesture(&mut r, 3, 1, 0.95).actions, ["tmux:focus right"]);
        r.set_tmux(false);
        assert!(gesture(&mut r, 3, 1, 0.95).actions.is_empty());
        wait(&mut r, 400);
        assert_eq!(
            gesture(&mut r, 3, 1, 0.95).actions,
            ["hypr:movefocus right"]
        );
    }

    #[test]
    fn slow_ramps_sustained_holds_and_partial_travel_never_focus() {
        let mut r = gesture_router("terminal", true);
        // Slow ramp: crossing 90% long after the 120 ms rise window expired.
        let mut o = wait(&mut r, 120);
        for f in [0.3, 0.5, 0.7, 0.85] {
            o.extend(abs(&mut r, 3, raw(f)));
            o.extend(wait(&mut r, 60));
        }
        o.extend(abs(&mut r, 3, raw(0.95)));
        o.extend(wait(&mut r, 60));
        o.extend(abs(&mut r, 3, 0));
        o.extend(wait(&mut r, 100));
        assert!(actions(&o).is_empty(), "slow ramp fired: {:?}", actions(&o));
        // Long hold at the peak: the 250 ms return window expires first.
        let mut o = wait(&mut r, 120);
        o.extend(abs(&mut r, 3, raw(0.95)));
        o.extend(wait(&mut r, 400));
        o.extend(abs(&mut r, 3, 0));
        o.extend(wait(&mut r, 100));
        assert!(actions(&o).is_empty(), "long hold fired: {:?}", actions(&o));
        // Partial travel: peaks below rise_pct and returns without ever locking.
        let mut o = wait(&mut r, 120);
        o.extend(abs(&mut r, 3, raw(0.6)));
        o.extend(wait(&mut r, 40));
        o.extend(abs(&mut r, 3, 0));
        o.extend(wait(&mut r, 100));
        assert!(
            actions(&o).is_empty(),
            "partial travel fired: {:?}",
            actions(&o)
        );
        // The tracker still works afterwards: one clean gesture fires.
        assert_eq!(actions(&gesture(&mut r, 3, 1, 0.95)), ["tmux:focus right"]);
    }

    #[test]
    fn boundary_jitter_without_a_real_dwell_never_rises() {
        let mut r = gesture_router("terminal", true);
        // Jitter around the 15% neutral boundary, never dwelling 80 ms below it.
        let mut o = Output::default();
        for below in [true, false] {
            o.extend(abs(&mut r, 3, if below { raw(0.05) } else { raw(0.2) }));
            o.extend(wait(&mut r, 20));
        }
        // Then a fast qualifying rise whose dwell never completed.
        o.extend(abs(&mut r, 3, raw(0.95)));
        o.extend(wait(&mut r, 50));
        o.extend(abs(&mut r, 3, 0));
        o.extend(wait(&mut r, 100));
        assert!(
            actions(&o).is_empty(),
            "unarmed rise fired: {:?}",
            actions(&o)
        );
        // After a real dwell the same motion focuses.
        assert_eq!(actions(&gesture(&mut r, 3, 1, 0.95)), ["tmux:focus right"]);
    }

    #[test]
    fn cooldown_bounces_never_double_fire() {
        let mut r = gesture_router("terminal", true);
        assert_eq!(actions(&gesture(&mut r, 3, 1, 0.95)), ["tmux:focus right"]);
        // An immediate second gesture inside the 400 ms cooldown stays silent...
        let mut o = wait(&mut r, 150);
        o.extend(abs(&mut r, 3, raw(0.95)));
        o.extend(wait(&mut r, 60));
        o.extend(abs(&mut r, 3, 0));
        o.extend(wait(&mut r, 100));
        assert!(
            actions(&o).is_empty(),
            "cooldown bounce fired: {:?}",
            actions(&o)
        );
        // ...and a gesture after the cooldown fires once again.
        wait(&mut r, 300);
        assert_eq!(actions(&gesture(&mut r, 3, 1, 0.95)), ["tmux:focus right"]);
    }

    #[test]
    fn diagonals_circular_sweeps_and_direction_changes_never_lock() {
        let mut r = gesture_router("terminal", true);
        // Held diagonal at the peak: 45 degrees is outside every 20-degree cone.
        let mut o = wait(&mut r, 120);
        o.extend(abs(&mut r, 3, raw(0.7)));
        o.extend(abs(&mut r, 4, raw(0.7)));
        o.extend(wait(&mut r, 60));
        o.extend(abs(&mut r, 3, 0));
        o.extend(abs(&mut r, 4, 0));
        o.extend(wait(&mut r, 100));
        assert!(actions(&o).is_empty(), "diagonal fired: {:?}", actions(&o));
        // Circular sweep: peak at a cardinal, then rotate while staying deflected;
        // the return window expires before the stick ever reaches neutral.
        let mut o = wait(&mut r, 120);
        o.extend(abs(&mut r, 3, raw(0.95)));
        o.extend(wait(&mut r, 60));
        for (x, y) in [(0.7_f64, 0.7_f64), (0.0, 0.95), (-0.7, 0.7), (-0.95, 0.0)] {
            o.extend(abs(&mut r, 3, raw(x.abs()) * x.signum() as i32));
            o.extend(abs(&mut r, 4, raw(y.abs()) * y.signum() as i32));
            o.extend(wait(&mut r, 80));
        }
        o.extend(abs(&mut r, 3, 0));
        o.extend(abs(&mut r, 4, 0));
        o.extend(wait(&mut r, 100));
        assert!(
            actions(&o).is_empty(),
            "circular sweep fired: {:?}",
            actions(&o)
        );
        // A clean cardinal gesture still works after the failures.
        assert_eq!(actions(&gesture(&mut r, 3, 1, 0.95)), ["tmux:focus right"]);
    }

    #[test]
    fn cursor_mode_context_switch_and_flush_cancel_pending_gestures() {
        // Cursor mode owns the sticks: the tracker cancels and nothing focuses.
        let mut r = gesture_router("terminal", true);
        r.cursor = true;
        let mut o = wait(&mut r, 120);
        o.extend(abs(&mut r, 3, raw(0.95)));
        o.extend(wait(&mut r, 60));
        o.extend(abs(&mut r, 3, 0));
        o.extend(wait(&mut r, 100));
        assert!(
            actions(&o).is_empty(),
            "cursor mode fired: {:?}",
            actions(&o)
        );
        // A held stick while cursor mode ENDS: the toggle flushes, so the still-held
        // deflection cannot complete a stale gesture; only a fresh full gesture fires.
        r.cursor = false;
        assert_eq!(actions(&gesture(&mut r, 3, 1, 0.95)), ["tmux:focus right"]);
        // A context/target switch at the peak cancels the pending lock.
        let mut r = gesture_router("terminal", true);
        wait(&mut r, 120);
        abs(&mut r, 3, raw(0.95));
        wait(&mut r, 60);
        r.set_focus("terminal", Some(("other-window".into(), 9)), false);
        let o = {
            let mut o = abs(&mut r, 3, 0);
            o.extend(wait(&mut r, 100));
            o
        };
        assert!(
            actions(&o).is_empty(),
            "flushed gesture fired: {:?}",
            actions(&o)
        );
        assert_eq!(actions(&gesture(&mut r, 3, 1, 0.95)), ["tmux:focus right"]);
    }

    #[test]
    fn no_button_release_or_menu_input_becomes_a_focus_action() {
        let mut r = gesture_router("terminal", true);
        let press = r.event(Event::new(EV_KEY, SOUTH, 1), 0);
        let release = r.event(Event::new(EV_KEY, SOUTH, 0), 0);
        // South is a declared [tap, hold] binding: a short release delivers
        // Enter. It must not be confused with the stick's return-to-neutral.
        assert!(press.actions.is_empty());
        assert_eq!(release.actions, ["key:ENTER"]);
        let press = r.event(Event::new(EV_KEY, BTN_DPAD_UP, 1), 0);
        let release = r.event(Event::new(EV_KEY, BTN_DPAD_UP, 0), 0);
        assert_eq!(press.actions, ["key:UP"]);
        assert!(
            release.actions.is_empty(),
            "a scalar binding release never acts"
        );
        assert!(wait(&mut r, 600).actions.is_empty());
        let guide = button_code(&r.global.controller.guide_button).unwrap();
        let mut menu = r.event(Event::new(EV_KEY, guide, 1), 0);
        menu.extend(r.event(Event::new(EV_KEY, guide, 0), 0));
        menu.extend(wait(&mut r, 600));
        assert_eq!(
            menu.actions,
            ["tmux:menu"],
            "menu input cannot complete a focus gesture"
        );
    }

    #[test]
    fn legacy_policy_without_the_table_keeps_magnitude_flicks() {
        // Absent table = the documented legacy policy: one magnitude flick, no gesture.
        let (c, l) = crate::config::tests::try_fixture_nav_with(|v| {
            v["keybindings"]["global"]["controller"]["nav"]
                .as_table_mut()
                .unwrap()
                .remove("focus_gesture");
        })
        .unwrap();
        let mut r = Router::with_context(c, l, axes(), "terminal");
        r.set_tmux(true);
        let mut o = abs(&mut r, 3, raw(0.8));
        o.extend(wait(&mut r, 200));
        assert_eq!(actions(&o), ["tmux:focus right"]);
    }

    #[test]
    fn camera_profiles_and_pointer_axes_stay_outside_the_gesture() {
        // A camera app keeps the continuous P5 semantics: instant, no qualification.
        let (c, l) = crate::config::tests::fixture_mon();
        let mut r = Router::with_context(c, l, axes(), "terminal");
        r.set_tmux(true);
        r.set_app(Some("mon"), None);
        let mut o = abs(&mut r, 3, raw(0.9));
        o.extend(wait(&mut r, 20));
        assert_eq!(actions(&o), ["key:ALT+9"], "camera RS fires at once");
        // Explicitly bind window movement: the shipped desktop nav profile
        // binds movefocus, which must use the gesture instead.
        let (c, l) = crate::config::tests::fixture_nav_with(|v| {
            v["keybindings"]["global"]["controller"]["nav"]["desktop"]["RS_RIGHT"] =
                "hypr:movewindow right".into();
        });
        let mut r = Router::with_context(c, l, axes(), "desktop-2d");
        let mut o = abs(&mut r, 3, raw(0.8));
        o.extend(wait(&mut r, 200));
        assert_eq!(actions(&o), ["hypr:movewindow right"]);
    }

    #[test]
    fn a_gesture_at_the_configured_boundaries_qualifies() {
        // Exactly at the thresholds: dwell 80 ms, rise window 120 ms, peak 0.9.
        let mut r = gesture_router("terminal", true);
        wait(&mut r, 80);
        let mut o = abs(&mut r, 3, raw(0.9));
        o.extend(wait(&mut r, 120));
        o.extend(abs(&mut r, 3, 0));
        o.extend(wait(&mut r, 250));
        assert_eq!(actions(&o), ["tmux:focus right"]);
    }
}
