// AI-hint: miosd shaderglass follow: the pure decisions that keep a ShaderGlass pane on its target window (visibility, geometry, polling cadence) and the Hyprland 0.56 Lua each decision dispatches.
// AI-related: src/mios-rs/miosd/src/shaderglass.rs, usr/share/mios/mios.toml [desktop.shaderglass.follow]
//! Hyprland 0.56 emits no geometry events (`movewindow` means "moved to another workspace"), so the
//! supervisor polls. [`Follower::step`] turns one snapshot (target, pane, monitors) into the actions that
//! put the pane exactly over the target, hide it while the target is not on screen, and the delay until
//! the next poll: the target output's frame interval while the target moves (or right after an event or
//! while a pointer button is held), the idle interval otherwise.

use std::time::{Duration, Instant};

use serde_json::Value as Json;

use crate::placement::{lua_str, pair};

/// Loose geometry tolerance in logical px, for checks that only ask "roughly there" (tests).
#[cfg(test)]
const TOLERANCE: f64 = 1.0;
/// hyprctl reports integer logical geometry, so the pane matches its target only when every edge is equal:
/// half a pixel separates "equal" from "one pixel off". (A 1.0 tolerance here left a pane one pixel wider
/// than its target for good: a 1 px target resize never counted as a move, a 1 px drift never as drift.)
const EXACT: f64 = 0.5;
/// How often one target rectangle is re-sent to a pane that does not land on it (a fractional output scale
/// that cannot represent it, a client that snaps its size) before the follower accepts where it is. A new
/// target rectangle gets a new budget, and so does a pane that moves away from where it kept landing (the
/// operator or Wine pushed it off): a bounded number of dispatches per change, never a loop, and a large drift
/// after a give-up is still corrected.
pub const PLACE_RETRIES: u32 = 3;
/// Shortest poll interval, whatever the refresh rate claims.
const MIN_FRAME: Duration = Duration::from_millis(2);

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Rect {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
}

impl Rect {
    /// Roughly equal (the old 1 px tolerance): kept for tests that show why it is not used to follow.
    #[cfg(test)]
    pub fn close_to(&self, o: &Rect) -> bool {
        (self.x - o.x).abs() <= TOLERANCE
            && (self.y - o.y).abs() <= TOLERANCE
            && (self.w - o.w).abs() <= TOLERANCE
            && (self.h - o.h).abs() <= TOLERANCE
    }

    /// Equal on integer logical geometry: no edge more than half a pixel off.
    pub fn same(&self, o: &Rect) -> bool {
        (self.x - o.x).abs() <= EXACT
            && (self.y - o.y).abs() <= EXACT
            && (self.w - o.w).abs() <= EXACT
            && (self.h - o.h).abs() <= EXACT
    }
}

/// The parts of one `j/clients` entry the follower reads.
#[derive(Clone, Debug, PartialEq)]
pub struct Win {
    pub address: String,
    pub class: String,
    pub title: String,
    pub pid: i64,
    pub stable_id: String,
    pub mapped: bool,
    pub hidden: bool,
    pub pinned: bool,
    pub rect: Rect,
    pub workspace_id: i64,
    pub workspace_name: String,
    pub monitor: i64,
}

impl Win {
    pub fn from_json(c: &Json) -> Option<Win> {
        let (x, y) = pair(c, "at")?;
        let (w, h) = pair(c, "size")?;
        let s = |k: &str| c.get(k).and_then(Json::as_str).unwrap_or("").to_owned();
        Some(Win {
            address: c.get("address")?.as_str()?.to_owned(),
            class: s("class"),
            title: s("title"),
            pid: c.get("pid").and_then(Json::as_i64).unwrap_or(-1),
            stable_id: c.get("stableId").map(|v| v.as_str().map(str::to_owned).unwrap_or_else(|| v.to_string())).unwrap_or_default(),
            mapped: c.get("mapped").and_then(Json::as_bool).unwrap_or(false),
            hidden: c.get("hidden").and_then(Json::as_bool).unwrap_or(false),
            pinned: c.get("pinned").and_then(Json::as_bool).unwrap_or(false),
            rect: Rect { x, y, w, h },
            workspace_id: c.pointer("/workspace/id").and_then(Json::as_i64).unwrap_or(0),
            workspace_name: c
                .pointer("/workspace/name")
                .and_then(Json::as_str)
                .unwrap_or("")
                .to_owned(),
            monitor: c.get("monitor").and_then(Json::as_i64).unwrap_or(-1),
        })
    }
}

/// The parts of one `j/monitors` entry the follower reads.
#[derive(Clone, Debug, PartialEq)]
pub struct Mon {
    pub id: i64,
    pub active_ws: i64,
    /// 0 when no special workspace is shown on this output.
    pub special_ws: i64,
    pub refresh: f64,
}

pub fn monitors_from_json(m: &Json) -> Vec<Mon> {
    m.as_array()
        .into_iter()
        .flatten()
        .filter_map(|m| {
            Some(Mon {
                id: m.get("id")?.as_i64()?,
                active_ws: m.pointer("/activeWorkspace/id").and_then(Json::as_i64).unwrap_or(0),
                special_ws: m.pointer("/specialWorkspace/id").and_then(Json::as_i64).unwrap_or(0),
                refresh: m.get("refreshRate").and_then(Json::as_f64).unwrap_or(60.0),
            })
        })
        .collect()
}

/// Whether the target is on screen: mapped, not hidden, on its output's active workspace with no special
/// workspace drawn over it, or on the special workspace that output shows (lamp-minimized windows live on
/// `special:minimized`, which is never shown, so they count as off screen). A pinned target follows its
/// output's active workspace.
pub fn on_screen(t: &Win, mons: &[Mon]) -> bool {
    if !t.mapped || t.hidden {
        return false;
    }
    let Some(m) = mons.iter().find(|m| m.id == t.monitor) else {
        return false;
    };
    if t.workspace_name.starts_with("special:") {
        return t.workspace_id != 0 && m.special_ws == t.workspace_id;
    }
    m.special_ws == 0 && (t.pinned || m.active_ws == t.workspace_id)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum OnMove {
    Follow,
    Hide,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Policy {
    /// Fast poll rate; 0 = the target output's refresh rate.
    pub follow_hz: f64,
    pub idle: Duration,
    pub settle: Duration,
    pub on_move: OnMove,
    pub close_with_target: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub enum Action {
    Place(Rect),
    PlaceTarget(Rect),
    Workspace(String),
    Show,
    Hide,
    Close,
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum Outcome {
    Continue,
    TargetGone,
    PaneGone,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Step {
    pub actions: Vec<Action>,
    pub next: Duration,
    pub outcome: Outcome,
}

#[derive(Clone, Debug, Default)]
pub struct Follower {
    placed: Option<Rect>,
    placed_at: Option<Instant>,
    shown: Option<bool>,
    last_target: Option<Rect>,
    last_pane: Option<Rect>,
    changed_at: Option<Instant>,
    poked_at: Option<Instant>,
    dragging: bool,
    workspace: Option<(i64, i64)>,
    /// Drift re-placements of the current target rectangle that did not land ([`PLACE_RETRIES`]).
    misses: u32,
    /// Where the pane was when the last of those was counted: a pane found anywhere else moved on its own.
    missed_at: Option<Rect>,
}

impl Follower {
    pub fn new() -> Self {
        Self::default()
    }

    /// An event that may move the target (layout change, workspace switch) or a held pointer button:
    /// poll at the frame rate for the settle time.
    pub fn poke(&mut self, now: Instant) {
        self.poked_at = Some(now);
    }

    /// Forget what was placed and shown (after the pane was re-tagged or the compositor reconnected), so
    /// the next step re-sends geometry and visibility.
    pub fn reset(&mut self) {
        self.placed = None;
        self.placed_at = None;
        self.shown = None;
        self.last_pane = None;
        self.workspace = None;
        self.misses = 0;
        self.missed_at = None;
    }

    /// Whether the follower gave up re-sending the current target rectangle (the pane cannot land on it).
    pub fn gave_up(&self) -> bool {
        self.misses >= PLACE_RETRIES
    }

    /// Only an explicit setup-mode pointer drag may drive the application
    /// from the overlay. A compositor/Wine reflow is never user intent.
    pub fn set_dragging(&mut self, dragging: bool) {
        self.dragging = dragging;
    }

    fn frame(p: &Policy, target: &Win, mons: &[Mon]) -> Duration {
        let hz = if p.follow_hz > 0.0 {
            p.follow_hz
        } else {
            mons.iter()
                .find(|m| m.id == target.monitor)
                .map(|m| m.refresh)
                .filter(|r| r.is_finite() && *r > 0.0)
                .unwrap_or(60.0)
        };
        Duration::from_secs_f64(1.0 / hz).max(MIN_FRAME)
    }

    /// One decision. `suppressed` hides the pane whatever the target does (Hypr3DGlass active).
    pub fn step(
        &mut self,
        p: &Policy,
        target: Option<&Win>,
        pane: Option<&Win>,
        mons: &[Mon],
        suppressed: bool,
        now: Instant,
    ) -> Step {
        let Some(pane) = pane else {
            return Step { actions: Vec::new(), next: p.idle, outcome: Outcome::PaneGone };
        };
        let Some(t) = target else {
            let actions = if p.close_with_target { vec![Action::Close] } else { Vec::new() };
            return Step { actions, next: p.idle, outcome: Outcome::TargetGone };
        };
        let frame = Self::frame(p, t, mons);
        if !pane.mapped || !t.mapped {
            // Never dispatch on an unmapped window (Hyprland 0.56 crashes resizing a window with no layout target).
            return Step { actions: Vec::new(), next: frame, outcome: Outcome::Continue };
        }
        let r = t.rect;
        let target_moved = self.last_target.is_some_and(|l| !l.same(&r))
            && self.placed.is_none_or(|pl| !pl.same(&r));
        let pane_moved = self.last_pane.is_some_and(|l| !l.same(&pane.rect));
        let pane_was_synced = self.last_pane.is_some_and(|l| self.placed.is_some_and(|pl| pl.same(&l)));
        if self.last_target.is_none_or(|l| !l.same(&r)) {
            if target_moved {
                self.changed_at = Some(now);
            }
            self.last_target = Some(r);
            self.misses = 0;
            self.missed_at = None;
        }
        // Landed, or moved away from where it kept missing: a new budget.
        if pane.rect.same(&r) || self.missed_at.is_some_and(|m| !m.same(&pane.rect)) {
            self.misses = 0;
            self.missed_at = None;
        }
        let within = |at: Option<Instant>| at.is_some_and(|a| now.saturating_duration_since(a) < p.settle);
        let user_dragged_pane = self.dragging && self.shown == Some(true)
            && !suppressed && on_screen(t, mons)
            && pane_moved && pane_was_synced && !target_moved && !pane.rect.same(&r);
        if user_dragged_pane {
            self.changed_at = Some(now);
        }
        let moving = within(self.changed_at);
        let hot = moving || within(self.poked_at);
        let show = on_screen(t, mons) && !suppressed && !(moving && p.on_move == OnMove::Hide);
        let mut actions = Vec::new();
        // A room-hidden pane still renders the same live shader profile for the native
        // target capture. Keep its raster and output synchronized without showing it or
        // treating a held button as a request to move/resize the application.
        if show || (suppressed && on_screen(t, mons)) {
            if pane.monitor != t.monitor
                && (self.workspace != Some((t.monitor, t.workspace_id)) || !within(self.placed_at)) {
                let workspace = if t.workspace_name.starts_with("special:") {
                    t.workspace_name.clone()
                } else {
                    t.workspace_id.to_string()
                };
                actions.push(Action::Workspace(workspace));
                self.workspace = Some((t.monitor, t.workspace_id));
            }
            if user_dragged_pane {
                actions.push(Action::PlaceTarget(pane.rect));
                self.last_target = Some(pane.rect);
                self.placed = Some(pane.rect);
                self.placed_at = Some(now);
            } else {
                // Re-place when the target moved, or when the pane drifted from where it was put (Wine or the
                // operator moved it) and the last placement had time to land -- at most PLACE_RETRIES times
                // for one target rectangle the pane keeps missing.
                let drifted = !pane.rect.same(&r) && !within(self.placed_at) && self.misses < PLACE_RETRIES;
                let needs_place = self.placed.is_none()
                    || target_moved
                    || (self.placed.is_some_and(|pl| !pl.same(&r)) && (self.shown != Some(true) || !within(self.placed_at)));
                if needs_place || drifted {
                    if drifted && !needs_place {
                        self.misses += 1;
                        self.missed_at = Some(pane.rect);
                    }
                    actions.push(Action::Place(r));
                    self.placed = Some(r);
                    self.placed_at = Some(now);
                }
            }
        }
        if show {
            if self.shown != Some(true) {
                actions.push(Action::Show);
                self.shown = Some(true);
            }
        } else if self.shown != Some(false) {
            actions.push(Action::Hide);
            self.shown = Some(false);
        }
        self.last_pane = Some(pane.rect);
        Step { actions, next: if hot { frame } else { p.idle }, outcome: Outcome::Continue }
    }
}

fn window_ref(address: &str) -> String {
    lua_str(&format!("address:{address}"))
}

/// The Lua for `actions` on the pane at `address` and its target at `target_address` (one `hyprctl eval`).
/// Opacity goes through set_prop (Hyprland skips drawing a window at alpha 0, so a hidden pane costs nothing
/// and shows nothing); geometry through resize + move, the same pair `miosd placement` uses.
pub fn actions_lua(address: &str, target_address: &str, actions: &[Action]) -> String {
    let mut parts = vec![format!("local w={}", window_ref(address))];
    if actions.iter().any(|a| matches!(a, Action::PlaceTarget(_))) {
        parts.push(format!("local t={}", window_ref(target_address)));
    }
    for a in actions {
        parts.push(match a {
            Action::Place(r) => format!(
                "hl.dispatch(hl.dsp.window.resize({{x={}, y={}, window=w}})); hl.dispatch(hl.dsp.window.move({{x={}, y={}, window=w}}))",
                r.w.round(),
                r.h.round(),
                r.x.round(),
                r.y.round()
            ),
            Action::PlaceTarget(r) => format!(
                "hl.dispatch(hl.dsp.window.resize({{x={}, y={}, window=t}})); hl.dispatch(hl.dsp.window.move({{x={}, y={}, window=t}}))",
                r.w.round(),
                r.h.round(),
                r.x.round(),
                r.y.round()
            ),
            Action::Workspace(workspace) => format!(
                "hl.dispatch(hl.dsp.window.move({{workspace={},follow=false,window=w}}))",
                lua_str(workspace)
            ),
            Action::Show => "hl.dispatch(hl.dsp.window.set_prop({prop='opacity', value='0.85 override 0.85 override', window=w}))".into(),
            Action::Hide => "hl.dispatch(hl.dsp.window.set_prop({prop='opacity', value='0 override', window=w}))".into(),
            Action::Close => "pcall(function() hl.dispatch(hl.dsp.window.close({window=w})) end)".into(),
        });
    }
    parts.join("; ")
}

/// Add (overlay: click-through) or remove (setup: focusable) the passthrough tag.
pub fn passthrough_lua(address: &str, tag: &str, on: bool) -> String {
    format!(
        "hl.dispatch(hl.dsp.window.tag({{tag={}, window={}}}))",
        lua_str(&format!("{}{tag}", if on { "+" } else { "-" })),
        window_ref(address)
    )
}

/// One key pressed and released on the window at `address` (best effort). A press and a release through
/// send_key_state: hl.send_shortcut never reached a Wine window (live: ShaderGlass' own keys did nothing under
/// winewayland), so the key fallbacks of an older WineCap did nothing either.
pub fn key_press_lua(address: &str, key: &str) -> String {
    let w = window_ref(address);
    let k = lua_str(key);
    format!(
        "pcall(function() hl.dispatch(hl.dsp.send_key_state({{mods='', key={k}, state='down', window={w}}})); hl.dispatch(hl.dsp.send_key_state({{mods='', key={k}, state='up', window={w}}})) end)"
    )
}

/// Setup mode on: drop the passthrough tag, focus the pane and send ShaderGlass' menu toggle. Off: send the
/// toggle again (menu hidden) and restore the tag. Focus and shortcut are best effort (pcall). `shortcut`
/// None: the menu is toggled inside ShaderGlass through the WineCap command channel instead (the same
/// IDM_TOGGLEMENU the key triggers, without depending on the key reaching Wine), so no key is sent.
pub fn setup_lua(address: &str, tag: &str, entering: bool, shortcut: Option<&str>) -> String {
    let w = window_ref(address);
    let send = shortcut.map(|k| key_press_lua(address, k));
    let mut parts = Vec::new();
    if entering {
        parts.push(passthrough_lua(address, tag, false));
        parts.push(format!("pcall(function() hl.dispatch(hl.dsp.focus({{window={w}}})) end)"));
        parts.extend(send);
    } else {
        parts.extend(send);
        parts.push(passthrough_lua(address, tag, true));
    }
    parts.join("; ")
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn policy() -> Policy {
        Policy {
            follow_hz: 0.0,
            idle: Duration::from_millis(100),
            settle: Duration::from_millis(300),
            on_move: OnMove::Follow,
            close_with_target: true,
        }
    }

    fn win(addr: &str, x: f64, y: f64, ws: i64) -> Win {
        Win {
            address: addr.into(),
            class: "kitty".into(),
            title: "t".into(),
            pid: 10,
            stable_id: String::new(),
            mapped: true,
            hidden: false,
            pinned: false,
            rect: Rect { x, y, w: 800.0, h: 600.0 },
            workspace_id: ws,
            workspace_name: ws.to_string(),
            monitor: 0,
        }
    }

    fn mons(active: i64, special: i64) -> Vec<Mon> {
        vec![Mon { id: 0, active_ws: active, special_ws: special, refresh: 144.0 }]
    }

    #[test]
    fn parses_hyprctl_clients_and_monitors() {
        let c = json!({"address": "0xabc", "class": "kitty", "title": "x", "pid": 42, "mapped": true, "hidden": false,
            "pinned": false, "at": [10, 20], "size": [300, 200], "workspace": {"id": 3, "name": "3"}, "monitor": 1});
        let w = Win::from_json(&c).unwrap();
        assert_eq!((w.rect.x, w.rect.y, w.rect.w, w.rect.h, w.workspace_id, w.monitor, w.pid), (10.0, 20.0, 300.0, 200.0, 3, 1, 42));
        assert!(Win::from_json(&json!({"address": "0x1"})).is_none(), "no geometry, no window");
        let m = monitors_from_json(&json!([{"id": 1, "refreshRate": 143.98, "activeWorkspace": {"id": 3}, "specialWorkspace": {"id": 0, "name": ""}}]));
        assert_eq!(m, vec![Mon { id: 1, active_ws: 3, special_ws: 0, refresh: 143.98 }]);
    }

    #[test]
    fn on_screen_follows_workspaces_specials_and_minimize() {
        let t = win("0xt", 0.0, 0.0, 1);
        assert!(on_screen(&t, &mons(1, 0)));
        assert!(!on_screen(&t, &mons(2, 0)), "hidden workspace");
        assert!(!on_screen(&t, &mons(1, -98)), "special workspace drawn over it");
        let mut s = t.clone();
        s.workspace_id = -98;
        s.workspace_name = "special:scratch".into();
        assert!(on_screen(&s, &mons(1, -98)), "on the special workspace that is shown");
        s.workspace_name = "special:minimized".into();
        s.workspace_id = -97;
        assert!(!on_screen(&s, &mons(1, -98)), "lamp-minimized");
        let mut p = t.clone();
        p.pinned = true;
        assert!(on_screen(&p, &mons(5, 0)), "pinned target is on every workspace");
        let mut h = t.clone();
        h.hidden = true;
        assert!(!on_screen(&h, &mons(1, 0)));
        let mut u = t.clone();
        u.monitor = 7;
        assert!(!on_screen(&u, &mons(1, 0)), "unknown output");
    }

    #[test]
    fn first_step_places_then_shows_and_idles() {
        let mut f = Follower::new();
        let now = Instant::now();
        let t = win("0xt", 100.0, 50.0, 1);
        let pane = win("0xp", 0.0, 0.0, 1);
        let s = f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, now);
        assert_eq!(s.actions, vec![Action::Place(t.rect), Action::Show]);
        assert_eq!(s.outcome, Outcome::Continue);
        assert_eq!(s.next, Duration::from_millis(100), "a target that has not moved polls at the idle rate");
        // Nothing to do once the pane sits on the target.
        let mut placed = pane.clone();
        placed.rect = t.rect;
        let s = f.step(&policy(), Some(&t), Some(&placed), &mons(1, 0), false, now + Duration::from_millis(500));
        assert!(s.actions.is_empty(), "{:?}", s.actions);
    }

    #[test]
    fn a_moving_target_is_followed_at_the_output_refresh() {
        let mut f = Follower::new();
        let t0 = Instant::now();
        let mut t = win("0xt", 100.0, 50.0, 1);
        let mut pane = win("0xp", 0.0, 0.0, 1);
        f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0);
        pane.rect = t.rect;
        t.rect.x += 40.0;
        let s = f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0 + Duration::from_millis(100));
        assert_eq!(s.actions, vec![Action::Place(t.rect)]);
        let frame = Duration::from_secs_f64(1.0 / 144.0);
        assert_eq!(s.next, frame, "moving: one poll per frame of the target's output");
        // Still fast within the settle time, idle after it.
        pane.rect = t.rect;
        let s = f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0 + Duration::from_millis(200));
        assert_eq!(s.next, frame);
        let s = f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0 + Duration::from_millis(500));
        assert_eq!(s.next, Duration::from_millis(100));
        // An explicit rate overrides the refresh.
        let mut p = policy();
        p.follow_hz = 50.0;
        f.poke(t0 + Duration::from_millis(600));
        let s = f.step(&p, Some(&t), Some(&pane), &mons(1, 0), false, t0 + Duration::from_millis(610));
        assert_eq!(s.next, Duration::from_millis(20));
    }

    #[test]
    fn hide_policy_and_suppression_and_off_screen_targets() {
        let t0 = Instant::now();
        let mut p = policy();
        p.on_move = OnMove::Hide;
        let mut f = Follower::new();
        let mut t = win("0xt", 0.0, 0.0, 1);
        let pane = win("0xp", 0.0, 0.0, 1);
        assert_eq!(f.step(&p, Some(&t), Some(&pane), &mons(1, 0), false, t0).actions, vec![Action::Place(t.rect), Action::Show]);
        t.rect.y += 30.0;
        let s = f.step(&p, Some(&t), Some(&pane), &mons(1, 0), false, t0 + Duration::from_millis(50));
        assert_eq!(s.actions, vec![Action::Hide], "hide while the target moves");
        let s = f.step(&p, Some(&t), Some(&pane), &mons(1, 0), false, t0 + Duration::from_millis(400));
        assert_eq!(s.actions, vec![Action::Place(t.rect), Action::Show], "back once settled");
        // Workspace switch away and back.
        let s = f.step(&p, Some(&t), Some(&pane), &mons(2, 0), false, t0 + Duration::from_millis(500));
        assert_eq!(s.actions, vec![Action::Hide]);
        let s = f.step(&p, Some(&t), Some(&pane), &mons(2, 0), false, t0 + Duration::from_millis(600));
        assert!(s.actions.is_empty(), "hidden once, not every poll");
        // Hypr3DGlass active.
        let mut g = Follower::new();
        let s = g.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), true, t0);
        assert_eq!(s.actions, vec![Action::Place(t.rect), Action::Hide]);
    }

    #[test]
    fn closes_with_its_target_and_ends_with_its_pane_and_waits_for_mapping() {
        let t0 = Instant::now();
        let mut f = Follower::new();
        let t = win("0xt", 0.0, 0.0, 1);
        let pane = win("0xp", 0.0, 0.0, 1);
        let s = f.step(&policy(), None, Some(&pane), &mons(1, 0), false, t0);
        assert_eq!((s.actions, s.outcome), (vec![Action::Close], Outcome::TargetGone));
        let mut keep = policy();
        keep.close_with_target = false;
        let s = f.step(&keep, None, Some(&pane), &mons(1, 0), false, t0);
        assert_eq!((s.actions, s.outcome), (vec![], Outcome::TargetGone));
        let s = f.step(&policy(), Some(&t), None, &mons(1, 0), false, t0);
        assert_eq!(s.outcome, Outcome::PaneGone);
        let mut unmapped = pane.clone();
        unmapped.mapped = false;
        let s = f.step(&policy(), Some(&t), Some(&unmapped), &mons(1, 0), false, t0);
        assert!(s.actions.is_empty(), "never dispatch on an unmapped window");
    }

    #[test]
    fn drift_is_corrected_but_not_hammered() {
        let t0 = Instant::now();
        let mut f = Follower::new();
        let t = win("0xt", 100.0, 100.0, 1);
        let mut pane = win("0xp", 0.0, 0.0, 1);
        assert_eq!(f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0).actions.len(), 2);
        // The compositor has not applied the placement yet: no second dispatch within the settle time.
        let s = f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0 + Duration::from_millis(10));
        assert!(s.actions.is_empty(), "{:?}", s.actions);
        // Still off after the settle time (Wine or the operator moved it): placed again.
        let s = f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0 + Duration::from_millis(400));
        assert_eq!(s.actions, vec![Action::Place(t.rect)]);
        pane.rect = Rect { x: 100.4, y: 99.6, w: 800.0, h: 600.0 };
        f.reset();
        let s = f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0 + Duration::from_millis(900));
        assert_eq!(s.actions, vec![Action::Place(t.rect), Action::Show], "reset re-sends geometry and visibility");
    }

    #[test]
    fn a_pane_one_pixel_off_its_target_is_corrected() {
        let t0 = Instant::now();
        let mut f = Follower::new();
        let t = win("0xt", 97.0, 141.0, 1);
        let mut pane = t.clone();
        pane.address = "0xp".into();
        assert_eq!(f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0).actions, vec![Action::Place(t.rect), Action::Show]);
        // The pane lands one pixel wider (the reported bug): drift, re-placed once the placement had time to land.
        pane.rect.w += 1.0;
        let s = f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0 + Duration::from_millis(400));
        assert_eq!(s.actions, vec![Action::Place(t.rect)], "a 1 px wider pane is drift");
        // Negative control: the old 1.0 px tolerance calls these two rectangles equal (the bug).
        assert!(pane.rect.close_to(&t.rect) && !pane.rect.same(&t.rect));
    }

    #[test]
    fn a_one_pixel_target_resize_is_followed() {
        let t0 = Instant::now();
        let mut f = Follower::new();
        let mut t = win("0xt", 100.0, 100.0, 1);
        let mut pane = t.clone();
        pane.address = "0xp".into();
        f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0);
        // The target shrinks by one pixel (a drag ending one pixel short, a layout reflow): the pane follows at once.
        t.rect.w -= 1.0;
        let s = f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0 + Duration::from_millis(500));
        assert_eq!(s.actions, vec![Action::Place(t.rect)]);
        pane.rect = t.rect;
        assert!(f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0 + Duration::from_millis(900)).actions.is_empty());
    }

    #[test]
    fn a_pane_that_cannot_land_is_re_sent_a_bounded_number_of_times() {
        let t0 = Instant::now();
        let mut f = Follower::new();
        let mut t = win("0xt", 100.0, 100.0, 1);
        let mut pane = t.clone();
        pane.address = "0xp".into();
        f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0);
        // A fractional scale: the pane always lands one pixel short.
        pane.rect.w -= 1.0;
        let mut sent = 0;
        for i in 1..=40u64 {
            let s = f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0 + Duration::from_millis(400 * i));
            sent += s.actions.iter().filter(|a| matches!(a, Action::Place(_))).count() as u32;
        }
        assert_eq!(sent, PLACE_RETRIES, "re-sent PLACE_RETRIES times, then left alone (no loop)");
        assert!(f.gave_up());
        // A new target rectangle gets a new budget.
        t.rect.x += 10.0;
        let s = f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0 + Duration::from_secs(20));
        assert_eq!(s.actions, vec![Action::Place(t.rect)]);
        assert!(!f.gave_up());
    }

    #[test]
    fn a_large_drift_after_a_give_up_is_still_corrected() {
        let t0 = Instant::now();
        let mut f = Follower::new();
        let t = win("0xt", 100.0, 100.0, 1);
        let mut pane = t.clone();
        pane.address = "0xp".into();
        f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0);
        // The fractional-scale case: the pane always lands one pixel short, until the follower gives up.
        pane.rect.w -= 1.0;
        let mut at = t0;
        for _ in 0..10 {
            at += Duration::from_millis(400);
            f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, at);
        }
        assert!(f.gave_up());
        // Then the pane is pushed 200 px away, the target unchanged (the verifier's probe): re-placed.
        let short = pane.rect;
        pane.rect.x += 200.0;
        let mut sent = 0;
        for _ in 0..30 {
            at += Duration::from_millis(400);
            let s = f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, at);
            if s.actions.iter().any(|a| matches!(a, Action::Place(_))) {
                sent += 1;
                // It lands where it always lands (one pixel short) again.
                pane.rect = short;
            }
        }
        assert!(sent >= 1, "a 200 px drift after the give-up is corrected");
        assert!(sent <= PLACE_RETRIES + 1, "and still bounded: {sent}");
        assert!(f.gave_up(), "back at the spot it cannot leave: given up again, no loop");
    }

    #[test]
    fn a_dragged_pane_moves_its_target_in_lockstep() {
        let t0 = Instant::now();
        let mut f = Follower::new();
        let mut t = win("0xt", 100.0, 100.0, 1);
        let mut pane = win("0xp", 100.0, 100.0, 1);
        assert_eq!(f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0).actions, vec![Action::Place(t.rect), Action::Show]);
        // A setup-mode physical drag, not a compositor-driven reflow.
        f.set_dragging(true);
        pane.rect.x = 220.0;
        pane.rect.y = 180.0;
        let s = f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0 + Duration::from_millis(50));
        assert_eq!(s.actions, vec![Action::PlaceTarget(pane.rect)]);
        assert_eq!(s.next, Duration::from_secs_f64(1.0 / 144.0));
        // Once the target catches up, no extra actions are dispatched.
        t.rect = pane.rect;
        let s = f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0 + Duration::from_millis(60));
        assert!(s.actions.is_empty(), "{:?}", s.actions);
    }

    #[test]
    fn compositor_pane_reflow_never_resizes_the_target() {
        let t0 = Instant::now();
        let mut f = Follower::new();
        let t = win("0xt", 100.0, 100.0, 1);
        let mut pane = win("0xp", 100.0, 100.0, 1);
        f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0);
        f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0 + Duration::from_millis(400));
        pane.rect = Rect { x: 0.0, y: 0.0, w: 1920.0, h: 1080.0 };
        let step = f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0 + Duration::from_millis(800));
        assert_eq!(step.actions, vec![Action::Place(t.rect)]);
        assert!(!step.actions.iter().any(|a| matches!(a, Action::PlaceTarget(_))));
    }

    #[test]
    fn suppressed_mode_roundtrip_restores_overlay_without_moving_application() {
        let t0 = Instant::now();
        let mut f = Follower::new();
        let t = win("0xt", 100.0, 100.0, 1);
        let mut pane = win("0xp", 100.0, 100.0, 1);
        f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0);
        let hidden = f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), true, t0 + Duration::from_millis(400));
        assert_eq!(hidden.actions, vec![Action::Hide]);
        pane.rect = Rect { x: 0.0, y: 0.0, w: 1920.0, h: 1080.0 };
        f.set_dragging(true); // a held button across a transition is not a pane drag
        let returned = f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0 + Duration::from_millis(800));
        assert_eq!(returned.actions, vec![Action::Place(t.rect), Action::Show]);
    }

    #[test]
    fn room_hidden_effect_tracks_native_raster_without_stealing_target_geometry() {
        let t0 = Instant::now();
        let mut f = Follower::new();
        let mut target = win("0xt", 100.0, 100.0, 1);
        let pane = win("0xp", 100.0, 100.0, 1);
        f.step(&policy(), Some(&target), Some(&pane), &mons(1, 0), false, t0);
        f.set_dragging(true);
        target.rect = Rect { x: 42.0, y: 60.0, w: 1280.0, h: 720.0 };
        let step = f.step(&policy(), Some(&target), Some(&pane), &mons(1, 0), true, t0 + Duration::from_millis(400));
        assert_eq!(step.actions, vec![Action::Place(target.rect), Action::Hide]);
        assert!(!step.actions.iter().any(|a| matches!(a, Action::PlaceTarget(_) | Action::Show)));
        target.workspace_id = 7;
        let offscreen = f.step(&policy(), Some(&target), Some(&pane), &mons(1, 0), true, t0 + Duration::from_millis(800));
        assert!(offscreen.actions.is_empty(), "off-workspace effects must stay dormant");
    }

    #[test]
    fn pane_output_membership_follows_the_target_not_only_its_pixels() {
        let now = Instant::now();
        let mut f = Follower::new();
        let mut target = win("0xt", 2100.0, 50.0, 3);
        target.monitor = 1;
        let mut pane = win("0xp", 100.0, 100.0, 1);
        pane.pinned = true;
        let monitors = vec![Mon { id: 1, active_ws: 3, special_ws: 0, refresh: 60.0 }];
        let step = f.step(&policy(), Some(&target), Some(&pane), &monitors, false, now);
        assert_eq!(step.actions, vec![Action::Workspace("3".into()), Action::Place(target.rect), Action::Show]);
        let lua = actions_lua(&pane.address, &target.address, &step.actions);
        assert!(lua.contains("workspace='3',follow=false,window=w"));
        assert!(!lua.contains("workspace='3',follow=false,window=t"));
    }

    #[test]
    fn lua_for_actions_tags_and_setup() {
        let r = Rect { x: 10.4, y: 20.6, w: 800.0, h: 600.0 };
        let l = actions_lua("0xab", "0xcd", &[Action::Place(r), Action::Show, Action::Hide, Action::Close]);
        assert!(l.starts_with("local w='address:0xab'; "), "{l}");
        assert!(l.contains("hl.dsp.window.resize({x=800, y=600, window=w})"), "{l}");
        assert!(l.contains("hl.dsp.window.move({x=10, y=21, window=w})"), "{l}");
        assert!(l.contains("value='0.85 override 0.85 override'") && l.contains("value='0 override'"), "{l}");
        assert!(l.contains("pcall(function() hl.dispatch(hl.dsp.window.close({window=w})) end)"), "{l}");
        let lt = actions_lua("0xab", "0xcd", &[Action::PlaceTarget(r)]);
        assert!(lt.contains("local t='address:0xcd'") && lt.contains("window=t"), "{lt}");
        assert_eq!(passthrough_lua("0xab", "pt", true), "hl.dispatch(hl.dsp.window.tag({tag='+pt', window='address:0xab'}))");
        assert!(passthrough_lua("0xab", "pt", false).contains("tag='-pt'"));
        let on = setup_lua("0xab", "pt", true, Some("m"));
        assert!(on.starts_with("hl.dispatch(hl.dsp.window.tag({tag='-pt'") && on.contains("key='m'"), "{on}");
        let off = setup_lua("0xab", "pt", false, Some("m"));
        assert!(off.ends_with("tag='+pt', window='address:0xab'}))") && off.contains("send_key_state"), "{off}");
        // A press and a release, never hl.send_shortcut (it does not reach Wine).
        let k = key_press_lua("0xab", "n");
        assert!(k.contains("state='down'") && k.contains("state='up'") && k.find("state='down'") < k.find("state='up'") && !k.contains("send_shortcut"), "{k}");
        // Quotes in a shortcut cannot break out of the Lua string.
        assert!(setup_lua("0xab", "pt", true, Some("a'b")).contains("key='a\\'b'"));
        // The WineCap channel toggles the menu: no key is sent, the tag and focus still change.
        let on = setup_lua("0xab", "pt", true, None);
        assert!(on.contains("tag='-pt'") && on.contains("hl.dsp.focus") && !on.contains("send_key_state"), "{on}");
        assert_eq!(setup_lua("0xab", "pt", false, None), passthrough_lua("0xab", "pt", true));
    }
}
