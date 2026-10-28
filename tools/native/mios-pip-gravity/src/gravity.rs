// AI-hint: Pure geometry + spring physics for PiP gravity (no I/O). Computes magnetic flank-rail targets, HUD avoidance/magnetism, aspect-aware size clamping, and a damped-spring step. Fully unit-tested.
// AI-related: tools/native/mios-pip-gravity/src/main.rs, usr/share/mios/mios.toml [desktop.pip_gravity]

#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Rect {
    pub x: f64,
    pub y: f64,
    pub w: f64,
    pub h: f64,
}

impl Rect {
    pub fn new(x: f64, y: f64, w: f64, h: f64) -> Self {
        Rect { x, y, w, h }
    }
    pub fn right(&self) -> f64 {
        self.x + self.w
    }
    pub fn bottom(&self) -> f64 {
        self.y + self.h
    }
    pub fn cx(&self) -> f64 {
        self.x + self.w / 2.0
    }
    pub fn cy(&self) -> f64 {
        self.y + self.h / 2.0
    }
}

#[derive(Clone, Debug)]
pub struct Params {
    pub left: bool,
    pub right: bool,
    pub margin: f64,
    pub gap: f64,
    pub hud_gap: f64,
    pub attract: f64,
    pub min_w_frac: f64,
    pub max_w_frac: f64,
}

/// Aspect-aware size clamp: width bounded by monitor fractions, height follows the content aspect.
pub fn clamp_size(cur: Rect, mon: Rect, p: &Params) -> (f64, f64) {
    let aspect = if cur.h > 0.0 { cur.w / cur.h } else { 16.0 / 9.0 };
    let w = cur.w.clamp(p.min_w_frac * mon.w, p.max_w_frac * mon.w);
    (w, w / aspect)
}

fn pick_rail_right(cur: Rect, mon: Rect, p: &Params) -> bool {
    match (p.left, p.right) {
        (true, false) => false,
        (false, true) => true,
        _ => cur.cx() >= mon.cx(),
    }
}

/// Vertical bounds for a column at [x, x+w): HUD rects overlapping the column push the bounds in.
fn column_bounds(x: f64, w: f64, h: f64, mon: Rect, huds: &[Rect], p: &Params) -> (f64, f64) {
    let _ = h;
    let mut top = mon.y + p.margin;
    let mut bottom = mon.bottom() - p.margin;
    for r in huds {
        if r.right() <= x || r.x >= x + w {
            continue;
        }
        if r.cy() < mon.cy() {
            top = top.max(r.bottom() + p.hud_gap);
        } else {
            bottom = bottom.min(r.y - p.hud_gap);
        }
    }
    (top, bottom)
}

/// Targets for all PiPs on one monitor (same order as input). Idempotent on its own output.
pub fn plan(pips: &[Rect], mon: Rect, huds: &[Rect], p: &Params) -> Vec<Rect> {
    let mut out: Vec<Rect> = pips.to_vec();
    let mut sizes = Vec::with_capacity(pips.len());
    let mut rails = Vec::with_capacity(pips.len());
    for r in pips {
        sizes.push(clamp_size(*r, mon, p));
        rails.push(pick_rail_right(*r, mon, p));
    }
    for side_right in [false, true] {
        let mut idx: Vec<usize> = (0..pips.len()).filter(|&i| rails[i] == side_right).collect();
        idx.sort_by(|&a, &b| pips[a].y.partial_cmp(&pips[b].y).unwrap());
        let mut bounds = Vec::new();
        for &i in &idx {
            let (w, h) = sizes[i];
            let x = if side_right { mon.right() - p.margin - w } else { mon.x + p.margin };
            bounds.push(column_bounds(x, w, h, mon, huds, p));
            out[i] = Rect::new(x, pips[i].y, w, h);
        }
        // forward pass: magnetise + resolve overlap downwards
        let mut prev_bottom: Option<f64> = None;
        for (k, &i) in idx.iter().enumerate() {
            let (top, bottom) = bounds[k];
            let h = out[i].h;
            let mut y = out[i].y.max(top);
            let mut lines = vec![top, bottom - h];
            if let Some(pb) = prev_bottom {
                lines.push(pb + p.gap);
            }
            for l in lines {
                if (y - l).abs() <= p.attract {
                    y = l;
                    break;
                }
            }
            if let Some(pb) = prev_bottom {
                y = y.max(pb + p.gap);
            }
            out[i].y = y;
            prev_bottom = Some(y + h);
        }
        // backward pass: pull the stack up from the bottom bound
        let mut next_top: Option<f64> = None;
        for (k, &i) in idx.iter().enumerate().rev() {
            let (top, bottom) = bounds[k];
            let h = out[i].h;
            let limit = match next_top {
                Some(nt) => (nt - p.gap).min(bottom),
                None => bottom,
            };
            if out[i].y + h > limit {
                out[i].y = (limit - h).max(top);
            }
            next_top = Some(out[i].y);
        }
    }
    out
}

/// One semi-implicit Euler step of a damped spring; returns true once settled.
pub fn spring_step(pos: &mut f64, vel: &mut f64, target: f64, k: f64, c: f64, dt: f64) -> bool {
    let a = -k * (*pos - target) - c * *vel;
    *vel += a * dt;
    *pos += *vel * dt;
    if (*pos - target).abs() < 0.5 && vel.abs() < 2.0 {
        *pos = target;
        *vel = 0.0;
        true
    } else {
        false
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn p() -> Params {
        Params { left: true, right: true, margin: 16.0, gap: 12.0, hud_gap: 12.0, attract: 40.0, min_w_frac: 0.15, max_w_frac: 0.40 }
    }
    const MON: Rect = Rect { x: 1920.0, y: 0.0, w: 1920.0, h: 1080.0 };

    #[test]
    fn snaps_to_right_rail_and_keeps_free_y() {
        let t = plan(&[Rect::new(3000.0, 400.0, 453.0, 255.0)], MON, &[], &p());
        assert_eq!(t[0].x, 3840.0 - 16.0 - 453.0);
        assert_eq!(t[0].y, 400.0); // hover: far from any magnet line, y untouched
    }
    #[test]
    fn snaps_to_left_rail() {
        let t = plan(&[Rect::new(2100.0, 300.0, 453.0, 255.0)], MON, &[], &p());
        assert_eq!(t[0].x, 1936.0);
    }
    #[test]
    fn single_flank_config_forces_rail() {
        let mut q = p();
        q.left = false;
        let t = plan(&[Rect::new(1930.0, 300.0, 453.0, 255.0)], MON, &[], &q);
        assert_eq!(t[0].x, 3840.0 - 16.0 - 453.0);
    }
    #[test]
    fn magnetic_top_and_bottom_lines() {
        let t = plan(&[Rect::new(3300.0, 30.0, 453.0, 255.0)], MON, &[], &p());
        assert_eq!(t[0].y, 16.0);
        let t = plan(&[Rect::new(3300.0, 780.0, 453.0, 255.0)], MON, &[], &p());
        assert_eq!(t[0].y, 1080.0 - 16.0 - 255.0);
    }
    #[test]
    fn stack_never_overlaps_and_keeps_gap() {
        let a = Rect::new(3300.0, 100.0, 453.0, 255.0);
        let b = Rect::new(3300.0, 150.0, 453.0, 255.0);
        let t = plan(&[a, b], MON, &[], &p());
        assert!(t[1].y >= t[0].bottom() + 12.0 - 1e-9);
    }
    #[test]
    fn dock_pushes_column_up_and_topbar_pushes_down() {
        let dock = Rect::new(3600.0, 1016.0, 200.0, 56.0);
        let bar = Rect::new(3600.0, 8.0, 200.0, 28.0);
        let t = plan(&[Rect::new(3300.0, 900.0, 453.0, 255.0)], MON, &[dock], &p());
        assert!(t[0].bottom() <= 1016.0 - 12.0 + 1e-9);
        let t = plan(&[Rect::new(3300.0, 0.0, 453.0, 255.0)], MON, &[bar], &p());
        assert!(t[0].y >= 8.0 + 28.0 + 12.0 - 1e-9);
    }
    #[test]
    fn centered_hud_does_not_affect_flank_column() {
        let dock = Rect::new(2624.0, 1016.0, 512.0, 56.0);
        let t = plan(&[Rect::new(3300.0, 780.0, 453.0, 255.0)], MON, &[dock], &p());
        assert_eq!(t[0].y, 1080.0 - 16.0 - 255.0);
    }
    #[test]
    fn size_is_aspect_aware_and_clamped() {
        let t = plan(&[Rect::new(3000.0, 300.0, 1600.0, 900.0)], MON, &[], &p());
        assert!((t[0].w - 768.0).abs() < 1e-9);
        assert!((t[0].w / t[0].h - 1600.0 / 900.0).abs() < 1e-9);
        let t = plan(&[Rect::new(3000.0, 300.0, 100.0, 100.0)], MON, &[], &p());
        assert!((t[0].w - 288.0).abs() < 1e-9 && (t[0].h - 288.0).abs() < 1e-9);
    }
    #[test]
    fn plan_is_idempotent() {
        let a = Rect::new(3300.0, 100.0, 453.0, 255.0);
        let b = Rect::new(2100.0, 150.0, 453.0, 255.0);
        let t1 = plan(&[a, b], MON, &[], &p());
        let t2 = plan(&t1, MON, &[], &p());
        assert_eq!(t1, t2);
    }
    #[test]
    fn spring_converges_with_overshoot_bounded() {
        let (mut x, mut v) = (0.0, 0.0);
        let mut peak: f64 = 0.0;
        let mut done = false;
        for _ in 0..600 {
            done = spring_step(&mut x, &mut v, 100.0, 220.0, 18.0, 1.0 / 60.0);
            peak = peak.max(x);
            if done { break; }
        }
        assert!(done && x == 100.0);
        assert!(peak > 100.0 && peak < 125.0, "springy but bounded: {peak}");
    }
    #[test]
    fn negative_control_far_from_target_not_settled() {
        let (mut x, mut v) = (0.0, 0.0);
        assert!(!spring_step(&mut x, &mut v, 100.0, 220.0, 18.0, 1.0 / 60.0));
    }
}
