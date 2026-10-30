// AI-hint: Fullscreen/Theatre matrix for PiP gravity (pure, no I/O). While any window on an output is maximized or fullscreen (the HUDs auto-hide and the reserved area collapses) and for fullscreen_release_ms afterwards, PiPs keep planning against the output's last ordinary bounds, so nothing jumps into the freed space and back. A fullscreen/maximized (or Theatre) PiP keeps its pre-fullscreen slot reserved in the plan, so its neighbours stay put and it restores into an empty slot.
// AI-related: tools/native/mios-pip-gravity/src/main.rs, tools/native/mios-pip-gravity/src/gravity.rs, usr/share/mios/mios.toml [desktop.pip_gravity] fullscreen_release_ms, usr/share/doc/mios/desktop-pip.md
// AI-functions: Latch::bounds, Latch::retain
use crate::gravity::Rect;
use std::collections::HashMap;
use std::time::{Duration, Instant};

/// Planning bounds of one output: its full logical rectangle, the HUD-reserved work area and the settled HUD bars.
#[derive(Clone, Debug, PartialEq)]
pub struct Bounds {
    pub output: Rect,
    pub area: Rect,
    pub huds: Vec<Rect>,
}

struct Held {
    bounds: Bounds,
    fullscreen: bool,
    released: Option<Instant>,
}

/// Per-output bounds latch for the fullscreen matrix.
#[derive(Default)]
pub struct Latch {
    held: HashMap<i64, Held>,
}

impl Latch {
    /// The bounds PiPs on `monitor` plan against. `fullscreen`: a window on the output's visible workspace is
    /// maximized or fullscreen. A changed output geometry always drops the latch (the old bounds no longer fit).
    pub fn bounds(&mut self, monitor: i64, fresh: Bounds, fullscreen: bool, now: Instant, release: Duration) -> Bounds {
        if let Some(h) = self.held.get_mut(&monitor) {
            if h.bounds.output == fresh.output {
                if fullscreen {
                    h.fullscreen = true;
                    h.released = None;
                    return h.bounds.clone();
                }
                if h.fullscreen {
                    let since = *h.released.get_or_insert(now);
                    if now.duration_since(since) < release {
                        return h.bounds.clone();
                    }
                }
            }
        }
        if fullscreen {
            // Never seen without fullscreen (daemon started during it, or the output changed): nothing to hold.
            self.held.remove(&monitor);
            return fresh;
        }
        self.held.insert(monitor, Held { bounds: fresh.clone(), fullscreen: false, released: None });
        fresh
    }

    /// Forget outputs that no longer exist.
    pub fn retain(&mut self, live: &[i64]) {
        self.held.retain(|k, _| live.contains(k));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::gravity::{plan, replan_input, Params, SETTLE_PX};
    const REL: Duration = Duration::from_millis(600);
    fn ordinary() -> Bounds {
        Bounds { output: Rect::new(0.0, 0.0, 1920.0, 1080.0), area: Rect::new(0.0, 44.0, 1920.0, 964.0), huds: vec![Rect::new(700.0, 1016.0, 520.0, 56.0)] }
    }
    fn collapsed() -> Bounds {
        Bounds { output: Rect::new(0.0, 0.0, 1920.0, 1080.0), area: Rect::new(0.0, 0.0, 1920.0, 1080.0), huds: vec![] }
    }

    #[test]
    fn fullscreen_holds_the_ordinary_bounds_until_the_release_period_ends() {
        let t0 = Instant::now();
        let mut l = Latch::default();
        assert_eq!(l.bounds(0, ordinary(), false, t0, REL), ordinary());
        // HUDs hide and the reserved area collapses while another window is fullscreen.
        assert_eq!(l.bounds(0, collapsed(), true, t0 + Duration::from_secs(1), REL), ordinary());
        assert_eq!(l.bounds(0, collapsed(), true, t0 + Duration::from_secs(30), REL), ordinary());
        // Fullscreen ends; the shell has not re-reserved yet: still held.
        assert_eq!(l.bounds(0, collapsed(), false, t0 + Duration::from_secs(31), REL), ordinary());
        assert_eq!(l.bounds(0, ordinary(), false, t0 + Duration::from_millis(31_300), REL), ordinary());
        // After the release period fresh bounds apply again.
        let mut moved = ordinary();
        moved.huds[0].h = 72.0;
        assert_eq!(l.bounds(0, moved.clone(), false, t0 + Duration::from_secs(32), REL), moved);
    }

    #[test]
    fn negative_control_without_fullscreen_fresh_bounds_pass_straight_through() {
        let t0 = Instant::now();
        let mut l = Latch::default();
        l.bounds(0, ordinary(), false, t0, REL);
        assert_eq!(l.bounds(0, collapsed(), false, t0 + Duration::from_millis(10), REL), collapsed(), "an auto-hidden HUD still releases its area");
    }

    #[test]
    fn a_resized_output_or_unknown_start_never_uses_stale_bounds() {
        let t0 = Instant::now();
        let mut l = Latch::default();
        assert_eq!(l.bounds(0, collapsed(), true, t0, REL), collapsed(), "started during fullscreen: nothing to hold");
        l.bounds(0, ordinary(), false, t0, REL);
        let mut resized = collapsed();
        resized.output.w = 1280.0;
        resized.area.w = 1280.0;
        assert_eq!(l.bounds(0, resized.clone(), true, t0 + Duration::from_secs(1), REL), resized);
        l.retain(&[]);
        assert!(l.held.is_empty());
    }

    fn lcg(seed: &mut u64) -> f64 {
        *seed = seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);
        ((*seed >> 33) as f64) / ((1u64 << 31) as f64)
    }

    #[test]
    fn a_fullscreen_pips_reserved_slot_keeps_its_neighbours_in_place() {
        let p = Params { left: true, right: true, margin: 8.0, gap: 8.0, hud_gap: 8.0, attract: 48.0, min_w_frac: 0.02, max_w_frac: 1.0, snap_width: 464.0, min_w_px: 160.0, size_snap: 24.0 };
        let area = Rect::new(0.0, 44.0, 1920.0, 964.0);
        let (mut seed, mut dropped_moves) = (11u64, 0);
        for _ in 0..300 {
            let n = 2 + (lcg(&mut seed) * 6.0) as usize;
            // Integer logical geometry, as the compositor reports it.
            let pips: Vec<Rect> = (0..n).map(|_| { let w = (160.0 + lcg(&mut seed) * 700.0).round(); Rect::new((lcg(&mut seed) * 1800.0).round(), (44.0 + lcg(&mut seed) * 900.0).round(), w, (w / (0.6 + lcg(&mut seed) * 1.4)).round()) }).collect();
            let settled = plan(&pips, area, &[], &p);
            let k = (lcg(&mut seed) * n as f64) as usize % n;
            // PiP k goes fullscreen: its pre-fullscreen slot (position and natural size) stays in the plan, reserved.
            let input = replan_input(&settled, &pips);
            let with_slot = plan(&input, area, &[], &p);
            for (i, (a, b)) in settled.iter().zip(&with_slot).enumerate() {
                assert!((a.x - b.x).abs() < SETTLE_PX && (a.y - b.y).abs() < SETTLE_PX && (a.w - b.w).abs() < SETTLE_PX, "PiP {i} moved: {a:?} -> {b:?}");
            }
            // The old behaviour dropped PiP k from the plan entirely.
            let keep = |v: &[Rect]| -> Vec<Rect> { v.iter().enumerate().filter(|(i, _)| *i != k).map(|(_, r)| *r).collect() };
            let (others, without) = (keep(&settled), plan(&keep(&input), area, &[], &p));
            if others.iter().zip(&without).any(|(a, b)| (a.x - b.x).abs() >= SETTLE_PX || (a.y - b.y).abs() >= SETTLE_PX || (a.w - b.w).abs() >= SETTLE_PX) {
                dropped_moves += 1;
            }
        }
        eprintln!("dropping the fullscreen PiP moved a neighbour in {dropped_moves}/300 random layouts");
        assert!(dropped_moves > 0, "negative control: dropping the slot should move a neighbour in some layouts");
    }
}
