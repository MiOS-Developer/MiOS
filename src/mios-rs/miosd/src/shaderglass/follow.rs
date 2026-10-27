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

/// Geometry tolerance in logical px (fractional output scales round by up to half a pixel).
const TOLERANCE: f64 = 1.0;
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
    pub fn close_to(&self, o: &Rect) -> bool {
        (self.x - o.x).abs() <= TOLERANCE
            && (self.y - o.y).abs() <= TOLERANCE
            && (self.w - o.w).abs() <= TOLERANCE
            && (self.h - o.h).abs() <= TOLERANCE
    }
}

/// The parts of one `j/clients` entry the follower reads.
#[derive(Clone, Debug, PartialEq)]
pub struct Win {
    pub address: String,
    pub class: String,
    pub title: String,
    pub pid: i64,
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
        let target_moved = self.last_target.is_some_and(|l| !l.close_to(&r))
            && self.placed.is_none_or(|pl| !pl.close_to(&r));
        let pane_moved = self.last_pane.is_some_and(|l| !l.close_to(&pane.rect));
        let pane_was_synced = self.last_pane.is_some_and(|l| self.placed.is_some_and(|pl| pl.close_to(&l)));
        if self.last_target.is_none_or(|l| !l.close_to(&r)) {
            if target_moved {
                self.changed_at = Some(now);
            }
            self.last_target = Some(r);
        }
        let within = |at: Option<Instant>| at.is_some_and(|a| now.saturating_duration_since(a) < p.settle);
        let user_dragged_pane = pane_moved && pane_was_synced && !target_moved && !pane.rect.close_to(&r);
        if user_dragged_pane {
            self.changed_at = Some(now);
        }
        let moving = within(self.changed_at);
        let hot = moving || within(self.poked_at);
        let show = on_screen(t, mons) && !suppressed && !(moving && p.on_move == OnMove::Hide);
        let mut actions = Vec::new();
        if show {
            if user_dragged_pane {
                actions.push(Action::PlaceTarget(pane.rect));
                self.last_target = Some(pane.rect);
                self.placed = Some(pane.rect);
                self.placed_at = Some(now);
            } else {
                // Re-place when the target moved, or when the pane drifted from where it was put (Wine or the
                // operator moved it) and the last placement had time to land.
                let drifted = !pane.rect.close_to(&r) && !within(self.placed_at);
                let needs_place = self.placed.is_none()
                    || target_moved
                    || (self.placed.is_some_and(|pl| !pl.close_to(&r)) && (self.shown != Some(true) || !within(self.placed_at)));
                if needs_place || drifted {
                    actions.push(Action::Place(r));
                    self.placed = Some(r);
                    self.placed_at = Some(now);
                }
            }
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

/// Setup mode on: drop the passthrough tag, focus the pane and send ShaderGlass' menu toggle. Off: send the
/// toggle again (menu hidden) and restore the tag. Focus and shortcut are best effort (pcall).
pub fn setup_lua(address: &str, tag: &str, entering: bool, shortcut: &str) -> String {
    let w = window_ref(address);
    let key = lua_str(shortcut);
    let send = format!("pcall(function() hl.send_shortcut({{mods='', key={key}, window={w}}}) end)");
    if entering {
        format!(
            "{}; pcall(function() hl.dispatch(hl.dsp.focus({{window={w}}})) end); {send}",
            passthrough_lua(address, tag, false)
        )
    } else {
        format!("{send}; {}", passthrough_lua(address, tag, true))
    }
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
        assert_eq!(s.actions, vec![Action::Hide]);
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
    fn a_dragged_pane_moves_its_target_in_lockstep() {
        let t0 = Instant::now();
        let mut f = Follower::new();
        let mut t = win("0xt", 100.0, 100.0, 1);
        let mut pane = win("0xp", 100.0, 100.0, 1);
        assert_eq!(f.step(&policy(), Some(&t), Some(&pane), &mons(1, 0), false, t0).actions, vec![Action::Place(t.rect), Action::Show]);
        // User drags the ShaderGlass overlay pane with Super+LMB while the target underneath hasn't moved yet.
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
        let on = setup_lua("0xab", "pt", true, "m");
        assert!(on.starts_with("hl.dispatch(hl.dsp.window.tag({tag='-pt'") && on.contains("key='m'"), "{on}");
        let off = setup_lua("0xab", "pt", false, "m");
        assert!(off.ends_with("tag='+pt', window='address:0xab'}))") && off.contains("hl.send_shortcut"), "{off}");
        // Quotes in a shortcut cannot break out of the Lua string.
        assert!(setup_lua("0xab", "pt", true, "a'b").contains("key='a\\'b'"));
    }
}
