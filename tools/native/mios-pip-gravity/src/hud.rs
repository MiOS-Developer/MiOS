// AI-hint: Settled HUD rectangles for PiP gravity: a HUD layer (topbar, dock, app menu) counts only once it has held the same rect for hud_settle_ms and is bar-sized (height <= hud_max_height_frac of its output); while it morphs or opens into a full pane (the dock becoming the app drawer) the last settled rect stays, so PiPs never move or shrink for a transient overlay.
// AI-related: tools/native/mios-pip-gravity/src/main.rs, usr/share/mios/quickshell/Sidebar.qml (dock -> drawer morph), usr/share/mios/mios.toml [desktop.pip_gravity] hud_settle_ms hud_max_height_frac
// AI-functions: HudTracker::update, HudTracker::rects
use crate::gravity::Rect;
use std::collections::HashMap;
use std::time::{Duration, Instant};

fn same(a: &Rect, b: &Rect) -> bool {
    (a.x - b.x).abs() < 1.0 && (a.y - b.y).abs() < 1.0 && (a.w - b.w).abs() < 1.0 && (a.h - b.h).abs() < 1.0
}

/// One HUD layer observed on one output: (output name/namespace key, rect, output logical height).
pub struct Sample {
    pub key: String,
    pub rect: Rect,
    pub output_height: f64,
}

#[derive(Default)]
pub struct HudTracker {
    seen: HashMap<String, (Rect, Instant)>,
    settled: HashMap<String, Rect>,
}

impl HudTracker {
    pub fn update(&mut self, samples: &[Sample], now: Instant, settle: Duration, max_height_frac: f64) {
        self.seen.retain(|k, _| samples.iter().any(|s| &s.key == k));
        // A HUD that is gone (auto-hidden dock) no longer constrains PiPs.
        self.settled.retain(|k, _| samples.iter().any(|s| &s.key == k));
        for s in samples {
            let since = match self.seen.get(&s.key) {
                Some((r, t)) if same(r, &s.rect) => *t,
                _ => now,
            };
            self.seen.insert(s.key.clone(), (s.rect, since));
            let bar = s.output_height <= 0.0 || s.rect.h <= max_height_frac * s.output_height;
            if bar && now.duration_since(since) >= settle {
                self.settled.insert(s.key.clone(), s.rect);
            }
        }
    }

    pub fn rects(&self) -> Vec<Rect> {
        self.settled.values().copied().collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn s(h: f64) -> Vec<Sample> {
        vec![Sample { key: "DP-3/mios-dock".into(), rect: Rect::new(700.0, 1080.0 - 8.0 - h, 512.0, h), output_height: 1080.0 }]
    }

    const SETTLE: Duration = Duration::from_millis(250);

    #[test]
    fn a_resting_dock_settles_and_the_drawer_morph_never_replaces_it() {
        let t0 = Instant::now();
        let mut h = HudTracker::default();
        h.update(&s(56.0), t0, SETTLE, 0.25);
        assert!(h.rects().is_empty(), "not settled yet");
        h.update(&s(56.0), t0 + Duration::from_millis(300), SETTLE, 0.25);
        assert_eq!(h.rects()[0].h, 56.0);
        // dock -> drawer morph frames, then the open full pane held for seconds
        let mut t = t0 + Duration::from_millis(300);
        for hh in [120.0, 400.0, 800.0, 980.0] {
            t += Duration::from_millis(16);
            h.update(&s(hh), t, SETTLE, 0.25);
            assert_eq!(h.rects()[0].h, 56.0, "mid-morph frame {hh} must not constrain PiPs");
        }
        h.update(&s(980.0), t + Duration::from_secs(5), SETTLE, 0.25);
        assert_eq!(h.rects()[0].h, 56.0, "an open full-pane drawer is not a HUD bar");
        // closing: a brief small frame does not settle until it holds
        h.update(&s(60.0), t + Duration::from_secs(6), SETTLE, 0.25);
        assert_eq!(h.rects()[0].h, 56.0);
    }

    #[test]
    fn a_real_bar_change_is_adopted_once_it_holds_and_a_hidden_hud_is_released() {
        let t0 = Instant::now();
        let mut h = HudTracker::default();
        h.update(&s(56.0), t0, SETTLE, 0.25);
        h.update(&s(56.0), t0 + SETTLE, SETTLE, 0.25);
        h.update(&s(72.0), t0 + Duration::from_secs(1), SETTLE, 0.25);
        assert_eq!(h.rects()[0].h, 56.0);
        h.update(&s(72.0), t0 + Duration::from_secs(1) + SETTLE, SETTLE, 0.25);
        assert_eq!(h.rects()[0].h, 72.0, "a taller but still bar-sized dock is adopted after it holds");
        h.update(&[], t0 + Duration::from_secs(2), SETTLE, 0.25);
        assert!(h.rects().is_empty(), "auto-hidden dock no longer constrains PiPs");
    }
}
