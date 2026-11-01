// AI-hint: Pure geometry + spring physics for PiP gravity (no I/O). Computes magnetic flank-rail targets packed across both rails, HUD avoidance/magnetism, aspect-aware size clamping with min/launch/max size magnets, an exact integer fixed-point plan from natural sizes (a settled layout never moves again), and a damped-spring step. Fully unit-tested.
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
    pub snap_width: f64,
    /// Absolute smallest PiP width (logical px); a smaller resize springs back to it.
    pub min_w_px: f64,
    /// Size magnet range: within this many px of the minimum, launch (flank) or maximum width the size snaps exactly.
    pub size_snap: f64,
    /// Margin snap proximity range (px) for edge resizing and adjacent window margins.
    pub resize_snap_proximity: f64,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct Motion {
    pub anchor: Option<Rect>,
    pub anchor_rail: Option<bool>,
    pub velocity: (f64, f64),
}

/// Normalized mass / size factor m in [0.1, 1.0].
pub fn mass_factor(w: f64, h: f64, mon_w: f64, mon_h: f64) -> f64 {
    let area = (w * h).max(0.0);
    let mon_area = (mon_w * mon_h).max(1.0);
    (area / mon_area).clamp(0.1, 1.0)
}

/// Flick velocity threshold (px/s) scaling INVERSELY with mass factor m.
/// Larger PiP (higher m) requires LESS flick velocity to break free and swap rails.
pub fn flick_velocity_threshold(m: f64) -> f64 {
    let m_clamped = m.clamp(0.1, 1.0);
    150.0 + 30.0 / m_clamped
}

/// Displacement threshold (px) scaling INVERSELY with mass factor m.
/// Larger PiP (higher m) requires LESS displacement to swap rails.
pub fn flick_displacement_threshold(m: f64, travel: f64) -> f64 {
    let m_clamped = m.clamp(0.1, 1.0);
    let base = 120.0_f64.min(0.25 * travel.max(1.0));
    base * (0.5 + 0.05 / m_clamped)
}

/// Gravity attraction pull distance (px) scaling INVERSELY with mass factor m.
/// Smaller PiP is snappier with higher attraction pull toward rails.
pub fn gravity_pull(base_attract: f64, m: f64) -> f64 {
    let m_clamped = m.clamp(0.1, 1.0);
    base_attract * (0.6 + 0.04 / m_clamped)
}

/// Spring stiffness (k) scaling INVERSELY with mass factor m.
/// Smaller PiP has higher spring stiffness (snappier settle), larger has higher inertia.
pub fn mass_stiffness(base_k: f64, m: f64) -> f64 {
    let m_clamped = m.clamp(0.1, 1.0);
    base_k * (0.7 + 0.03 / m_clamped)
}

/// Fill the flank left by the canonical centered opening rectangle, with the shared margin on both sides.
pub fn flank_width(monitor_width:f64,opening_width:f64,margin:f64)->f64 {((monitor_width-opening_width)/2.0-2.0*margin).max(0.0)}

/// Aspect-aware size clamp: width bounded by the minimum (absolute px and monitor fraction) and the maximum (monitor
/// fraction and the largest aspect-true size inside the usable area); height follows the content aspect. Inside
/// `size_snap` of the minimum, the launch (flank) width or the maximum, the width snaps exactly to it.
pub fn clamp_size(cur: Rect, mon: Rect, p: &Params) -> (f64, f64) {
    let aspect = if cur.h > 0.0 && cur.w > 0.0 { cur.w / cur.h } else { 16.0 / 9.0 };
    let max_h = (p.max_w_frac * mon.h).min((mon.h - 2.0 * p.margin).max(1.0));
    let max_w_from_h = max_h * aspect;
    let max_w = (p.max_w_frac * mon.w).min((mon.w - 2.0 * p.margin).max(1.0)).min(max_w_from_h).max(1.0);
    let min_w = (p.min_w_frac * mon.w).max(p.min_w_px).min(max_w);
    let mut w = cur.w.clamp(min_w, max_w);
    let magnets = [min_w, p.snap_width, max_w];
    if let Some(m) = magnets.iter().copied().filter(|m| *m > 0.0 && (w - m).abs() <= p.size_snap).min_by(|a, b| (w - a).abs().total_cmp(&(w - b).abs())) {
        w = m.clamp(min_w, max_w);
    }
    (w, (w / aspect).clamp(1.0, max_h))
}

pub fn pick_rail(cur: Rect, motion: Option<&Motion>, mon: Rect, p: &Params) -> bool {
    match (p.left, p.right) {
        (true, false) => return false,
        (false, true) => return true,
        _ => {}
    }
    let is_portrait = mon.h > mon.w;
    let m = mass_factor(cur.w, cur.h, mon.w, mon.h);
    let v_thresh = flick_velocity_threshold(m);

    if !is_portrait {
        // Landscape: Left (false) <-> Right (true)
        let travel = (mon.w - 2.0 * p.margin - cur.w).max(1.0);
        let d_thresh = flick_displacement_threshold(m, travel);
        if let Some(mot) = motion {
            match mot.anchor_rail {
                Some(true) => {
                    let anchor_x = mot.anchor.map_or(mon.right() - p.margin - cur.w, |a| a.x);
                    let dx = anchor_x - cur.x;
                    let vx = mot.velocity.0;
                    if dx >= d_thresh || vx <= -v_thresh || cur.cx() < mon.cx() {
                        return false;
                    } else {
                        return true;
                    }
                }
                Some(false) => {
                    let anchor_x = mot.anchor.map_or(mon.x + p.margin, |a| a.x);
                    let dx = cur.x - anchor_x;
                    let vx = mot.velocity.0;
                    if dx >= d_thresh || vx >= v_thresh || cur.cx() >= mon.cx() {
                        return true;
                    } else {
                        return false;
                    }
                }
                None => {}
            }
        }
        cur.cx() >= mon.cx()
    } else {
        // Portrait: Top (false) <-> Bottom (true)
        let travel_y = (mon.h - 2.0 * p.margin - cur.h).max(1.0);
        let d_thresh = flick_displacement_threshold(m, travel_y);
        if let Some(mot) = motion {
            match mot.anchor_rail {
                Some(true) => {
                    let anchor_y = mot.anchor.map_or(mon.bottom() - p.margin - cur.h, |a| a.y);
                    let dy = anchor_y - cur.y;
                    let vy = mot.velocity.1;
                    if dy >= d_thresh || vy <= -v_thresh || cur.cy() < mon.cy() {
                        return false;
                    } else {
                        return true;
                    }
                }
                Some(false) => {
                    let anchor_y = mot.anchor.map_or(mon.y + p.margin, |a| a.y);
                    let dy = cur.y - anchor_y;
                    let vy = mot.velocity.1;
                    if dy >= d_thresh || vy >= v_thresh || cur.cy() >= mon.cy() {
                        return true;
                    } else {
                        return false;
                    }
                }
                None => {}
            }
        }
        cur.cy() >= mon.cy()
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

/// A planned rect within this many logical px (each of x, y, w, h) of the current one counts as settled.
pub const SETTLE_PX: f64 = 3.0;

/// Passes `plan` iterates at most before taking the last one (positions settle in a few).
const PLAN_PASSES: usize = 64;

/// Targets for all PiPs on one monitor (same order as input). `pips` carry each PiP's current POSITION and its
/// NATURAL size: the size the operator (or the client) chose, never a size the plan squeezed it to. Clamping, the size
/// magnets and the overfull squeeze always start from the natural size, so a squeezed width can never drift into a
/// magnet and snap a settled layout on a later tick. Only positions are iterated, to their exact fixed point (stacking
/// and rail choice depend on the planned positions); passes that cycle return one canonical member of the cycle, which
/// every member re-plans to. Output is integer geometry, so re-planning the applied output with the same natural sizes
/// reproduces it exactly.
pub fn snap_resize_edges(
    cur: Rect,
    prev: Option<Rect>,
    mon: Rect,
    huds: &[Rect],
    others: &[Rect],
    p: &Params,
) -> Rect {
    let prox = if p.resize_snap_proximity > 0.0 { p.resize_snap_proximity } else { 32.0 };
    let gap = p.gap;
    let mut r = cur;

    let (work_l, work_r) = (mon.x + p.margin, mon.right() - p.margin);
    let (work_t, work_b) = (mon.y + p.margin, mon.bottom() - p.margin);

    // Compute HUD top and bottom bounds overlapping cur horizontally
    let x_overlap_hud = |h: &Rect| cur.x < h.right() + prox && cur.right() > h.x - prox;
    let mut top_line = work_t;
    let mut bottom_line = work_b;
    for h in huds.iter().filter(|h| x_overlap_hud(h)) {
        if h.cy() < mon.cy() {
            top_line = top_line.max(h.bottom() + p.hud_gap);
        } else {
            bottom_line = bottom_line.min(h.y - p.hud_gap);
        }
    }

    // Determine edge movements
    let (l_moved, r_moved, t_moved, b_moved) = if let Some(p0) = prev {
        (
            (cur.x - p0.x).abs() >= SETTLE_PX,
            (cur.right() - p0.right()).abs() >= SETTLE_PX,
            (cur.y - p0.y).abs() >= SETTLE_PX,
            (cur.bottom() - p0.bottom()).abs() >= SETTLE_PX,
        )
    } else {
        // No previous reference: determine which edge is resting on a rail / margin
        let near_r = (cur.right() - work_r).abs() <= prox;
        let near_l = (cur.x - work_l).abs() <= prox;
        let near_t = (cur.y - top_line).abs() <= prox;
        let near_b = (cur.bottom() - bottom_line).abs() <= prox;
        if near_r && !near_l {
            (true, false, false, false)
        } else if near_l && !near_r {
            (false, true, false, false)
        } else if near_b && !near_t {
            (false, false, true, false)
        } else if near_t && !near_b {
            (false, false, false, true)
        } else {
            (true, true, true, true)
        }
    };

    let y_overlap = |o: &Rect| cur.y < o.bottom() + prox && cur.bottom() > o.y - prox;

    // Horizontal edge snapping
    if l_moved && !r_moved {
        let anchor_r = prev.map_or(cur.right(), |p0| p0.right());
        let mut candidates = vec![work_l];
        for o in others.iter().filter(|o| y_overlap(o)) {
            candidates.push(o.right() + gap); // facing edge (8px gap)
            candidates.push(o.x);             // aligned edge
        }
        if let Some(target) = candidates
            .into_iter()
            .filter(|&t| (cur.x - t).abs() <= prox)
            .min_by(|a, b| (cur.x - *a).abs().total_cmp(&(cur.x - *b).abs()))
        {
            r.x = target;
            r.w = (anchor_r - target).max(p.min_w_px);
        }
    } else if r_moved && !l_moved {
        let anchor_l = prev.map_or(cur.x, |p0| p0.x);
        let mut candidates = vec![work_r];
        for o in others.iter().filter(|o| y_overlap(o)) {
            candidates.push(o.x - gap);       // facing edge (8px gap)
            candidates.push(o.right());       // aligned edge
        }
        if let Some(target) = candidates
            .into_iter()
            .filter(|&t| (cur.right() - t).abs() <= prox)
            .min_by(|a, b| (cur.right() - *a).abs().total_cmp(&(cur.right() - *b).abs()))
        {
            r.x = anchor_l;
            r.w = (target - anchor_l).max(p.min_w_px);
        }
    } else if l_moved && r_moved {
        let mut best_l: Option<f64> = None;
        let mut best_l_dist = prox + 1.0;
        let mut l_candidates = vec![work_l];
        for o in others.iter().filter(|o| y_overlap(o)) {
            l_candidates.push(o.right() + gap);
            l_candidates.push(o.x);
        }
        for t in l_candidates {
            let d = (cur.x - t).abs();
            if d <= prox && d < best_l_dist {
                best_l_dist = d;
                best_l = Some(t);
            }
        }

        let mut best_r: Option<f64> = None;
        let mut best_r_dist = prox + 1.0;
        let mut r_candidates = vec![work_r];
        for o in others.iter().filter(|o| y_overlap(o)) {
            r_candidates.push(o.x - gap);
            r_candidates.push(o.right());
        }
        for t in r_candidates {
            let d = (cur.right() - t).abs();
            if d <= prox && d < best_r_dist {
                best_r_dist = d;
                best_r = Some(t);
            }
        }

        if let Some(tl) = best_l {
            if let Some(tr) = best_r {
                r.x = tl;
                r.w = (tr - tl).max(p.min_w_px);
            } else {
                let old_r = r.right();
                r.x = tl;
                r.w = (old_r - tl).max(p.min_w_px);
            }
        } else if let Some(tr) = best_r {
            let old_l = r.x;
            r.w = (tr - old_l).max(p.min_w_px);
        }
    }

    // Vertical edge snapping
    let x_overlap_r = |o: &Rect| r.x < o.right() + prox && r.right() > o.x - prox;

    if t_moved && !b_moved {
        let anchor_b = prev.map_or(cur.bottom(), |p0| p0.bottom());
        let mut candidates = vec![top_line, work_t];
        for o in others.iter().filter(|o| x_overlap_r(o)) {
            candidates.push(o.bottom() + gap); // facing edge (8px gap)
            candidates.push(o.y);              // aligned edge
        }
        if let Some(target) = candidates
            .into_iter()
            .filter(|&t| (cur.y - t).abs() <= prox)
            .min_by(|a, b| (cur.y - *a).abs().total_cmp(&(cur.y - *b).abs()))
        {
            r.y = target;
            r.h = (anchor_b - target).max(100.0);
        }
    } else if b_moved && !t_moved {
        let anchor_t = prev.map_or(cur.y, |p0| p0.y);
        let mut candidates = vec![bottom_line, work_b];
        for o in others.iter().filter(|o| x_overlap_r(o)) {
            candidates.push(o.y - gap);        // facing edge (8px gap)
            candidates.push(o.bottom());       // aligned edge
        }
        if let Some(target) = candidates
            .into_iter()
            .filter(|&t| (cur.bottom() - t).abs() <= prox)
            .min_by(|a, b| (cur.bottom() - *a).abs().total_cmp(&(cur.bottom() - *b).abs()))
        {
            r.y = anchor_t;
            r.h = (target - anchor_t).max(100.0);
        }
    } else if t_moved && b_moved {
        let mut best_t: Option<f64> = None;
        let mut best_t_dist = prox + 1.0;
        let mut t_candidates = vec![top_line, work_t];
        for o in others.iter().filter(|o| x_overlap_r(o)) {
            t_candidates.push(o.bottom() + gap);
            t_candidates.push(o.y);
        }
        for t in t_candidates {
            let d = (cur.y - t).abs();
            if d <= prox && d < best_t_dist {
                best_t_dist = d;
                best_t = Some(t);
            }
        }

        let mut best_b: Option<f64> = None;
        let mut best_b_dist = prox + 1.0;
        let mut b_candidates = vec![bottom_line, work_b];
        for o in others.iter().filter(|o| x_overlap_r(o)) {
            b_candidates.push(o.y - gap);
            b_candidates.push(o.bottom());
        }
        for t in b_candidates {
            let d = (cur.bottom() - t).abs();
            if d <= prox && d < best_b_dist {
                best_b_dist = d;
                best_b = Some(t);
            }
        }

        if let Some(tt) = best_t {
            if let Some(tb) = best_b {
                r.y = tt;
                r.h = (tb - tt).max(100.0);
            } else {
                let old_b = r.bottom();
                r.y = tt;
                r.h = (old_b - tt).max(100.0);
            }
        } else if let Some(tb) = best_b {
            let old_t = r.y;
            r.h = (tb - old_t).max(100.0);
        }
    }

    r
}

/// Targets for all PiPs on one monitor (same order as input). `pips` carry each PiP's current POSITION and its
/// NATURAL size: the size the operator (or the client) chose, never a size the plan squeezed it to. Clamping, the size
/// magnets and the overfull squeeze always start from the natural size, so a squeezed width can never drift into a
/// magnet and snap a settled layout on a later tick. Only positions are iterated, to their exact fixed point (stacking
/// and rail choice depend on the planned positions); passes that cycle return one canonical member of the cycle, which
/// every member re-plans to. Output is integer geometry, so re-planning the applied output with the same natural sizes
/// reproduces it exactly.
#[allow(dead_code)]
pub fn plan(pips: &[Rect], mon: Rect, huds: &[Rect], others: &[Rect], p: &Params) -> Vec<Rect> {
    plan_with_motion(pips, &[], mon, huds, others, p)
}

pub fn plan_with_motion(
    pips: &[Rect],
    motions: &[Motion],
    mon: Rect,
    huds: &[Rect],
    others: &[Rect],
    p: &Params,
) -> Vec<Rect> {
    let same = |a: &[Rect], b: &[Rect]| a.iter().zip(b).all(|(a, b)| (a.x - b.x).abs() < 1e-3 && (a.y - b.y).abs() < 1e-3 && (a.w - b.w).abs() < 1e-3 && (a.h - b.h).abs() < 1e-3);
    let key = |s: &[Rect]| s.iter().fold((0.0, 0.0, 0.0), |k, r| (k.0 + r.y, k.1 + r.x, k.2 + r.w));
    let at = |out: &[Rect]| -> Vec<Rect> { out.iter().zip(pips).map(|(o, n)| Rect::new(o.x, o.y, n.w, n.h)).collect() };
    let mut seen = vec![plan_once(pips, motions, mon, huds, others, p)];
    for _ in 0..PLAN_PASSES {
        let last = seen.last().expect("one pass");
        let next = plan_once(&at(last), motions, mon, huds, others, p);
        if same(&next, last) {
            return next;
        }
        if let Some(j) = seen.iter().position(|s| same(s, &next)) {
            let cycle = seen.split_off(j);
            return cycle.into_iter().min_by(|a, b| key(a).partial_cmp(&key(b)).unwrap_or(std::cmp::Ordering::Equal)).expect("cycle member");
        }
        seen.push(next);
    }
    seen.pop().expect("one pass")
}

/// The next tick's planning input for a settled layout: the integer geometry the daemon applied (positions) with the
/// PiPs' unchanged natural sizes.
#[cfg(test)]
pub fn replan_input(applied: &[Rect], natural: &[Rect]) -> Vec<Rect> {
    applied.iter().zip(natural).map(|(a, n)| Rect::new(a.x.round(), a.y.round(), n.w, n.h)).collect()
}

fn plan_once(
    pips: &[Rect],
    motions: &[Motion],
    mon: Rect,
    huds: &[Rect],
    others: &[Rect],
    p: &Params,
) -> Vec<Rect> {
    let is_portrait = mon.h > mon.w;
    let mut out: Vec<Rect> = pips.to_vec();
    let mut sizes = Vec::with_capacity(pips.len());
    let mut rails = Vec::with_capacity(pips.len());
    let mut portrait_bottom = Vec::with_capacity(pips.len());
    for (i, r) in pips.iter().enumerate() {
        sizes.push(clamp_size(*r, mon, p));
        let mot = motions.get(i);
        let rail_choice = pick_rail(*r, mot, mon, p);
        if is_portrait {
            portrait_bottom.push(rail_choice);
            rails.push(r.cx() >= mon.cx());
        } else {
            rails.push(rail_choice);
        }
    }

    if p.left && p.right {
        let capacity = (mon.h - 2.0 * p.margin).max(1.0);
        for side in [false, true] {
            let total = |side: bool, rails: &[bool]| {
                let idx: Vec<_> = (0..pips.len()).filter(|i| rails[*i] == side).collect();
                idx.iter().map(|i| sizes[*i].1).sum::<f64>() + p.gap * idx.len().saturating_sub(1) as f64
            };
            while total(side, &rails) > capacity {
                let other = total(!side, &rails);
                let candidate = (0..pips.len()).rev().find(|i| {
                    rails[*i] == side && other + sizes[*i].1 + if other > 0.0 { p.gap } else { 0.0 } <= capacity
                });
                let Some(i) = candidate else { break };
                rails[i] = !side;
            }
        }
    }
    for side_right in [false, true] {
        let mut idx: Vec<usize> = (0..pips.len()).filter(|&i| rails[i] == side_right).collect();
        idx.sort_by(|&a, &b| pips[a].y.partial_cmp(&pips[b].y).unwrap());
        let available = idx.iter().map(|i| {
            let (w, h) = sizes[*i];
            let x = if side_right { mon.right() - p.margin - w } else { mon.x + p.margin };
            let (top, bottom) = column_bounds(x, w, h, mon, huds, p);
            (bottom - top).max(1.0)
        }).reduce(f64::min).unwrap_or(mon.h);
        let sum = idx.iter().map(|i| sizes[*i].1).sum::<f64>();
        let scale = if idx.len() > 1 && sum > 0.0 {
            ((available - p.gap * idx.len().saturating_sub(1) as f64).max(1.0) / sum).min(1.0)
        } else if idx.len() == 1 && sum > available {
            (available / sum).min(1.0)
        } else {
            1.0
        };
        for &i in &idx {
            sizes[i].0 *= scale;
            sizes[i].1 *= scale;
        }
        let mut bounds = Vec::new();
        for &i in &idx {
            let (w, h) = sizes[i];
            let x = if side_right { mon.right() - p.margin - w } else { mon.x + p.margin };
            bounds.push(column_bounds(x, w, h, mon, huds, p));
            let y = if is_portrait {
                if portrait_bottom[i] {
                    mon.bottom() - p.margin - h
                } else {
                    mon.y + p.margin
                }
            } else {
                pips[i].y
            };
            out[i] = Rect::new(x, y, w, h);
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
            let m = mass_factor(out[i].w, out[i].h, mon.w, mon.h);
            let attract = gravity_pull(p.attract, m);
            for l in lines {
                if (y - l).abs() <= attract {
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
    // A manually enlarged PiP can span both flanks. Resolve collisions vertically too.
    let overlaps = |a: Rect, b: Rect| a.x < b.right() - 0.5 && a.right() > b.x + 0.5 && a.y < b.bottom() - 0.5 && a.bottom() > b.y + 0.5;
    let mut crowded = false;
    for i in 0..out.len() {
        for j in 0..i {
            if overlaps(out[i], out[j]) {
                let proposed = out[j].bottom() + p.gap;
                let (_, bottom) = column_bounds(out[i].x, out[i].w, out[i].h, mon, huds, p);
                if proposed + out[i].h <= bottom {
                    out[i].y = proposed;
                } else {
                    crowded = true;
                }
            }
        }
    }
    crowded |= (0..out.len()).any(|i| (0..i).any(|j| overlaps(out[i], out[j])));
    if crowded {
        let mut order: Vec<usize> = (0..out.len()).collect();
        order.sort_by(|&a, &b| pips[a].y.total_cmp(&pips[b].y).then(a.cmp(&b)));
        let (mut top, mut bottom) = (mon.y + p.margin, mon.bottom() - p.margin);
        for &i in &order {
            let x = if rails[i] { mon.right() - p.margin - out[i].w } else { mon.x + p.margin };
            let (t, b) = column_bounds(x, out[i].w, out[i].h, mon, huds, p);
            top = top.max(t);
            bottom = bottom.min(b);
        }
        let sum = out.iter().map(|r| r.h).sum::<f64>();
        let fit = ((bottom - top - p.gap * out.len().saturating_sub(1) as f64).max(1.0) / sum.max(1.0)).min(1.0);
        let mut y = top;
        for &i in &order {
            let r = &mut out[i];
            r.w *= fit;
            r.h *= fit;
            r.x = if rails[i] { mon.right() - p.margin - r.w } else { mon.x + p.margin };
            r.y = y;
            y = r.bottom() + p.gap;
        }
    }

    if !others.is_empty() {
        for i in 0..out.len() {
            out[i] = snap_resize_edges(out[i], None, mon, huds, others, p);
        }
    }

    for r in &mut out {
        *r = Rect::new(r.x.round(), r.y.round(), r.w.round().max(1.0), r.h.round().max(1.0));
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
        Params { left: true, right: true, margin: 16.0, gap: 12.0, hud_gap: 12.0, attract: 40.0, min_w_frac: 0.15, max_w_frac: 0.40, snap_width:0.0, min_w_px:0.0, size_snap:0.0, resize_snap_proximity: 0.0 }
    }
    const MON: Rect = Rect { x: 1920.0, y: 0.0, w: 1920.0, h: 1080.0 };

    #[test]
    fn snaps_to_right_rail_and_keeps_free_y() {
        let t = plan(&[Rect::new(3000.0, 400.0, 453.0, 255.0)], MON, &[], &[], &p());
        assert_eq!(t[0].x, 3840.0 - 16.0 - 453.0);
        assert_eq!(t[0].y, 400.0); // hover: far from any magnet line, y untouched
    }
    #[test]
    fn snaps_to_left_rail() {
        let t = plan(&[Rect::new(2100.0, 300.0, 453.0, 255.0)], MON, &[], &[], &p());
        assert_eq!(t[0].x, 1936.0);
    }
    #[test]
    fn single_flank_config_forces_rail() {
        let mut q = p();
        q.left = false;
        let t = plan(&[Rect::new(1930.0, 300.0, 453.0, 255.0)], MON, &[], &[], &q);
        assert_eq!(t[0].x, 3840.0 - 16.0 - 453.0);
    }
    #[test]
    fn magnetic_top_and_bottom_lines() {
        let t = plan(&[Rect::new(3300.0, 30.0, 453.0, 255.0)], MON, &[], &[], &p());
        assert_eq!(t[0].y, 16.0);
        let t = plan(&[Rect::new(3300.0, 780.0, 453.0, 255.0)], MON, &[], &[], &p());
        assert_eq!(t[0].y, 1080.0 - 16.0 - 255.0);
    }
    #[test]
    fn stack_never_overlaps_and_keeps_gap() {
        let a = Rect::new(3300.0, 100.0, 453.0, 255.0);
        let b = Rect::new(3300.0, 150.0, 453.0, 255.0);
        let t = plan(&[a, b], MON, &[], &[], &p());
        assert!(t[1].y >= t[0].bottom() + 12.0 - 1e-9);
    }
    #[test]
    fn dock_pushes_column_up_and_topbar_pushes_down() {
        let dock = Rect::new(3600.0, 1016.0, 200.0, 56.0);
        let bar = Rect::new(3600.0, 8.0, 200.0, 28.0);
        let t = plan(&[Rect::new(3300.0, 900.0, 453.0, 255.0)], MON, &[dock], &[], &p());
        assert!(t[0].bottom() <= 1016.0 - 12.0 + 1e-9);
        let t = plan(&[Rect::new(3300.0, 0.0, 453.0, 255.0)], MON, &[bar], &[], &p());
        assert!(t[0].y >= 8.0 + 28.0 + 12.0 - 1e-9);
    }
    #[test]
    fn centered_hud_does_not_affect_flank_column() {
        let dock = Rect::new(2624.0, 1016.0, 512.0, 56.0);
        let t = plan(&[Rect::new(3300.0, 780.0, 453.0, 255.0)], MON, &[dock], &[], &p());
        assert_eq!(t[0].y, 1080.0 - 16.0 - 255.0);
    }
    #[test]
    fn size_is_aspect_aware_and_clamped() {
        let t = plan(&[Rect::new(3000.0, 300.0, 1600.0, 900.0)], MON, &[], &[], &p());
        assert!((t[0].w - 768.0).abs() < 1e-9);
        assert!((t[0].w / t[0].h - 1600.0 / 900.0).abs() < 1e-9);
        let t = plan(&[Rect::new(3000.0, 300.0, 100.0, 100.0)], MON, &[], &[], &p());
        assert!((t[0].w - 288.0).abs() < 1e-9 && (t[0].h - 288.0).abs() < 1e-9);
    }
    #[test]
    fn plan_is_idempotent() {
        let a = Rect::new(3300.0, 100.0, 453.0, 255.0);
        let b = Rect::new(2100.0, 150.0, 453.0, 255.0);
        let t1 = plan(&[a, b], MON, &[], &[], &p());
        let t2 = plan(&t1, MON, &[], &[], &p());
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

#[cfg(test)] mod flank_tests {
 use super::*;
 #[test] fn flank_uses_opening_geometry_and_global_margin(){assert_eq!(flank_width(1920.0,960.0,8.0),464.0);assert_eq!(flank_width(3840.0,1800.0,16.0),988.0);assert_eq!(flank_width(400.0,400.0,8.0),0.0);}
 #[test] fn tall_content_fits_without_changing_aspect(){let p=Params{left:true,right:true,margin:8.0,gap:8.0,hud_gap:8.0,attract:48.0,min_w_frac:0.2,max_w_frac:0.4,snap_width:0.0,min_w_px:0.0,size_snap:0.0,resize_snap_proximity:0.0};let (w,h)=clamp_size(Rect::new(0.0,0.0,200.0,1200.0),Rect::new(0.0,0.0,1920.0,964.0),&p);assert!(h<=948.0);assert!((w/h-1.0/6.0).abs()<1e-9);}
}

#[cfg(test)] mod collision_tests {
 use super::*;
 fn p()->Params {Params{left:true,right:true,margin:8.0,gap:8.0,hud_gap:8.0,attract:48.0,min_w_frac:0.24,max_w_frac:0.24,snap_width:0.0,min_w_px:0.0,size_snap:0.0,resize_snap_proximity:0.0}}
 fn overlap(a:Rect,b:Rect)->bool {a.x<b.right()-0.1&&a.right()>b.x+0.1&&a.y<b.bottom()-0.1&&a.bottom()>b.y+0.1}
 #[test] fn crowded_column_uses_the_other_flank_then_squeezes_without_overlap(){
  let mon=Rect::new(0.0,44.0,1920.0,964.0);
  for count in [2,3,4,8] {let input=vec![Rect::new(1500.0,200.0,460.0,300.0);count];let out=plan(&input,mon,&[],&[],&p());assert_eq!(out.len(),count);
   for (i,a) in out.iter().enumerate(){assert!(a.y>=mon.y&&a.bottom()<=mon.bottom()+0.1);assert!((a.w/a.h-460.0/300.0).abs()<=1.0/a.h+1.0/a.w,"aspect within integer rounding: {a:?}");for b in &out[i+1..]{assert!(!overlap(*a,*b),"{count}: {out:?}");}}
  }
 }
 #[test] fn moving_one_pip_changes_its_neighbors_target(){let mon=Rect::new(0.0,0.0,1920.0,1080.0);let input=[Rect::new(1500.0,100.0,460.0,260.0),Rect::new(1500.0,180.0,460.0,260.0)];let out=plan(&input,mon,&[],&[],&p());assert!(out[1].y>=out[0].bottom()+p().gap);assert!(!overlap(out[0],out[1]));}
}

#[cfg(test)] mod resize_tests {
 use super::*;
 #[test] fn smaller_and_larger_sizes_remain_free_with_a_launch_width_magnet(){
  let p=Params{left:true,right:true,margin:8.0,gap:8.0,hud_gap:8.0,attract:24.0,min_w_frac:0.02,max_w_frac:1.0,snap_width:464.0,min_w_px:0.0,size_snap:24.0,resize_snap_proximity:0.0};let mon=Rect::new(0.0,44.0,1920.0,964.0);
  for width in [180.0,320.0,700.0,1000.0] {let (w,h)=clamp_size(Rect::new(0.0,0.0,width,width/2.0),mon,&p);assert_eq!(w,width);assert_eq!(h,width/2.0);}
  for width in [444.0,464.0,480.0] {assert_eq!(clamp_size(Rect::new(0.0,0.0,width,width/2.0),mon,&p).0,464.0);}
 }
}

#[cfg(test)] mod enlarged_collision_tests {
 use super::*;
 #[test] fn manually_enlarged_windows_do_not_overlap_the_opposite_flank(){
 let p=Params{left:true,right:true,margin:8.0,gap:8.0,hud_gap:8.0,attract:24.0,min_w_frac:0.02,max_w_frac:1.0,snap_width:464.0,min_w_px:0.0,size_snap:24.0,resize_snap_proximity:0.0};let mon=Rect::new(0.0,44.0,1920.0,964.0);
 let out=plan(&[Rect::new(0.0,100.0,1000.0,620.0),Rect::new(900.0,200.0,1200.0,700.0)],mon,&[],&[],&p);
 let a=out[0];let b=out[1];assert!(a.right()<=b.x||b.right()<=a.x||a.bottom()<=b.y||b.bottom()<=a.y,"{out:?}");assert!(out.iter().all(|r|r.y>=mon.y&&r.bottom()<=mon.bottom()));
 }
}

#[cfg(test)] mod pack_tests {
 use super::*;
 fn p()->Params {Params{left:true,right:true,margin:8.0,gap:8.0,hud_gap:8.0,attract:48.0,min_w_frac:0.02,max_w_frac:1.0,snap_width:464.0,min_w_px:160.0,size_snap:24.0,resize_snap_proximity:0.0}}
 fn overlap(a:Rect,b:Rect)->bool {a.x<b.right()-0.5&&a.right()>b.x+0.5&&a.y<b.bottom()-0.5&&a.bottom()>b.y+0.5}
 fn check(out:&[Rect],mon:Rect,huds:&[Rect],q:&Params) {
  for (i,a) in out.iter().enumerate() {
   assert!(a.x>=mon.x+q.margin-0.5&&a.right()<=mon.right()-q.margin+0.5&&a.y>=mon.y+q.margin-0.5&&a.bottom()<=mon.bottom()-q.margin+0.5,"outside the work area: {a:?}");
   let on_rail=(a.x-(mon.x+q.margin)).abs()<0.5||(a.right()-(mon.right()-q.margin)).abs()<0.5;assert!(on_rail,"off the rails: {a:?}");
   for h in huds {assert!(!overlap(*a,*h),"PiP {a:?} under HUD {h:?}");}
   for b in &out[..i] {assert!(!overlap(*a,*b),"PiPs collide: {a:?} {b:?}");}
  }
 }
 fn lcg(seed:&mut u64)->f64 {*seed=seed.wrapping_mul(6364136223846793005).wrapping_add(1442695040888963407);((*seed>>33) as f64)/((1u64<<31) as f64)}
 #[test] fn six_pips_fill_both_rails_without_collisions(){
  let mon=Rect::new(0.0,44.0,1920.0,964.0);let q=p();
  let pips:Vec<Rect>=(0..6).map(|i|Rect::new(if i%3==0 {40.0} else {1400.0},100.0+i as f64*60.0,464.0,261.0)).collect();
  let out=plan(&pips,mon,&[],&[],&q);check(&out,mon,&[],&q);
  assert!(out.iter().any(|r|r.x<mon.cx())&&out.iter().any(|r|r.x>mon.cx()),"both rails used: {out:?}");
  assert!(out.iter().all(|r|(r.w-464.0).abs()<0.5),"three per rail fit at the launch width: {out:?}");
 }
 #[test] fn crowded_fallback_stays_clear_of_hud_bars_inside_the_work_area(){
  // An unreserved full-width bar inside the work area (auto-hide topbar shown) and two enlarged PiPs spanning both flanks.
  let mon=Rect::new(0.0,0.0,1920.0,1080.0);let q=p();let bar=Rect::new(0.0,0.0,1920.0,44.0);
  let input=[Rect::new(0.0,100.0,1000.0,620.0),Rect::new(900.0,200.0,1200.0,700.0)];
  let out=plan(&input,mon,&[bar],&[],&q);
  check(&out,mon,&[bar],&q);
  // Every single pass must already be clean: the fixed-point loop is capped, so a later pass cannot be relied on to
  // repair a crowded squeeze that ran under the bar (the old fallback started at mon.y+margin).
  check(&plan_once(&input,&[],mon,&[bar],&[],&q),mon,&[bar],&q);
 }
 #[test] fn packing_is_stable_for_random_sets(){
  let mut seed=7u64;
  for case in 0..300 {
   let mon=Rect::new(0.0,44.0,1920.0,964.0);let q=p();
   let n=1+(lcg(&mut seed)*7.0) as usize;
   let pips:Vec<Rect>=(0..n).map(|_|{let w=120.0+lcg(&mut seed)*1300.0;let a=0.5+lcg(&mut seed)*1.5;Rect::new(lcg(&mut seed)*1800.0,44.0+lcg(&mut seed)*900.0,w,w/a)}).collect();
   let huds=[Rect::new(0.0,44.0,1920.0,40.0)];
   let out=plan(&pips,mon,&huds,&[],&q);check(&out,mon,&huds,&q);
   // The daemon applies integer geometry and re-plans from it (with the unchanged natural sizes) on the next tick.
   let again=plan(&replan_input(&out,&pips),mon,&huds,&[],&q);
   // The daemon re-animates a settled PiP only when its target differs by SETTLE_PX or more.
   for (a,b) in out.iter().zip(&again) {assert!((a.x-b.x).abs()<SETTLE_PX&&(a.y-b.y).abs()<SETTLE_PX&&(a.w-b.w).abs()<SETTLE_PX&&(a.h-b.h).abs()<SETTLE_PX,"case {case}: settled layout moves again {a:?} -> {b:?} (input {pips:?})");}
  }
 }
 /// Small outputs (a phone or a squeezed nested session) with more PiPs than fit at min_width_px: the overfull squeeze
 /// wins over the minimum (no overlap, never under a HUD) and the clamp never fights it tick to tick.
 #[test] fn overfull_small_outputs_squeeze_below_the_minimum_without_jitter(){
  let mut seed=23u64;let q=p();
  for (case,mon) in [Rect::new(0.0,176.0,900.0,324.0),Rect::new(0.0,132.0,1288.0,556.0),Rect::new(0.0,44.0,412.0,800.0)].into_iter().cycle().take(240).enumerate() {
   let n=6+(lcg(&mut seed)*7.0) as usize;
   let pips:Vec<Rect>=(0..n).map(|_|{let w=160.0+lcg(&mut seed)*400.0;Rect::new(lcg(&mut seed)*mon.w,mon.y+lcg(&mut seed)*mon.h,w,w/(1.2+lcg(&mut seed)*1.2))}).collect();
   let huds=[Rect::new(mon.x,mon.y-40.0,mon.w,40.0)];
   let out=plan(&pips,mon,&huds,&[],&q);check(&out,mon,&huds,&q);
   for again in [plan(&replan_input(&out,&pips),mon,&huds,&[],&q)] {
    for (a,b) in out.iter().zip(&again) {assert!((a.x-b.x).abs()<SETTLE_PX&&(a.y-b.y).abs()<SETTLE_PX&&(a.w-b.w).abs()<SETTLE_PX&&(a.h-b.h).abs()<SETTLE_PX,"case {case}: squeezed layout moves again {a:?} -> {b:?}");}
   }
  }
 }
}

#[cfg(test)] mod size_snap_tests {
 use super::*;
 fn p()->Params {Params{left:true,right:true,margin:8.0,gap:8.0,hud_gap:8.0,attract:48.0,min_w_frac:0.02,max_w_frac:1.0,snap_width:464.0,min_w_px:160.0,size_snap:24.0,resize_snap_proximity:0.0}}
 const MON:Rect=Rect{x:0.0,y:44.0,w:1920.0,h:964.0};
 #[test] fn smaller_than_the_minimum_springs_back_to_it(){let (w,h)=clamp_size(Rect::new(0.0,0.0,60.0,34.0),MON,&p());assert_eq!(w,160.0);assert!((w/h-60.0/34.0).abs()<1e-9);}
 #[test] fn near_the_minimum_snaps_to_it_and_free_sizes_stay(){assert_eq!(clamp_size(Rect::new(0.0,0.0,180.0,100.0),MON,&p()).0,160.0);assert_eq!(clamp_size(Rect::new(0.0,0.0,200.0,100.0),MON,&p()).0,200.0);}
 #[test] fn the_maximum_keeps_the_aspect_inside_the_work_area_and_snaps(){
  let q=p();let (w,h)=clamp_size(Rect::new(0.0,0.0,1900.0,1069.0),MON,&q);assert!(h<=MON.h-2.0*q.margin+1e-9&&(w/h-1900.0/1069.0).abs()<1e-9,"{w}x{h}");
  let max=w;let (w2,_)=clamp_size(Rect::new(0.0,0.0,max-20.0,(max-20.0)*1069.0/1900.0),MON,&q);assert!((w2-max).abs()<1e-6,"inside size_snap of the maximum snaps to it: {w2} vs {max}");
  let (w3,_)=clamp_size(Rect::new(0.0,0.0,max-60.0,(max-60.0)*1069.0/1900.0),MON,&q);assert!((w3-(max-60.0)).abs()<1e-9,"outside size_snap stays free");
 }
 #[test] fn zero_snap_range_disables_magnets(){let mut q=p();q.size_snap=0.0;assert_eq!(clamp_size(Rect::new(0.0,0.0,470.0,264.0),MON,&q).0,470.0);}
}

#[cfg(test)]
mod resize_snap_and_throw_tests {
    use super::*;

    fn p() -> Params {
        Params {
            left: true,
            right: true,
            margin: 8.0,
            gap: 8.0,
            hud_gap: 8.0,
            attract: 48.0,
            min_w_frac: 0.02,
            max_w_frac: 1.0,
            snap_width: 464.0,
            min_w_px: 160.0,
            size_snap: 24.0,
            resize_snap_proximity: 32.0,
        }
    }

    const MON: Rect = Rect { x: 0.0, y: 44.0, w: 1920.0, h: 964.0 };

    #[test]
    fn magnetic_left_edge_snap_to_adjacent_window_8px_gap() {
        // Spotify window on left: x=0..960, right edge=960
        let spotify = Rect::new(0.0, 44.0, 960.0, 964.0);
        // PiP window on right rail: right edge anchored at 1912 (1920 - 8)
        let initial = Rect::new(1448.0, 44.0, 464.0, 964.0);
        // User resizes left edge towards Spotify: cur.x = 962 (difference from 960+8=968 is 6px <= 32px)
        let cur = Rect::new(962.0, 44.0, 950.0, 964.0);
        let snapped = snap_resize_edges(cur, Some(initial), MON, &[], &[spotify], &p());
        assert_eq!(snapped.x, 968.0, "left edge snaps to other.right + 8px gap");
        assert_eq!(snapped.right(), 1912.0, "right edge remains anchored");
        assert_eq!(snapped.w, 944.0, "width adjusted to match stationary right edge");
        assert_eq!(snapped.x - spotify.right(), 8.0, "inter-window margin is exactly 8px");
    }

    #[test]
    fn magnetic_right_edge_snap_to_adjacent_window_8px_gap() {
        // Window on right: x=1000..1912, left edge=1000
        let other = Rect::new(1000.0, 44.0, 912.0, 964.0);
        // PiP on left rail: left edge anchored at 8.0 (mon.x + margin)
        let initial = Rect::new(8.0, 44.0, 464.0, 964.0);
        // User resizes right edge towards other: right edge = 984 (difference from 1000-8=992 is 8px <= 32px)
        let cur = Rect::new(8.0, 44.0, 976.0, 964.0);
        let snapped = snap_resize_edges(cur, Some(initial), MON, &[], &[other], &p());
        assert_eq!(snapped.x, 8.0, "left edge remains anchored");
        assert_eq!(snapped.right(), 992.0, "right edge snaps to other.left - 8px gap");
        assert_eq!(snapped.w, 984.0, "width adjusted to match stationary left edge");
        assert_eq!(other.x - snapped.right(), 8.0, "inter-window margin is exactly 8px");
    }

    #[test]
    fn workarea_hud_top_and_bottom_snap() {
        let workarea_top = MON.y + 8.0; // 52.0
        let workarea_bottom = MON.bottom() - 8.0; // 1000.0
        let initial = Rect::new(1000.0, 200.0, 464.0, 400.0);

        // Top edge resized near workarea top (y=50 vs 52, diff=2px <= 32px)
        let cur_top = Rect::new(1000.0, 50.0, 464.0, 550.0);
        let snapped_top = snap_resize_edges(cur_top, Some(initial), MON, &[], &[], &p());
        assert_eq!(snapped_top.y, workarea_top, "top edge snaps to workarea top margin");
        assert_eq!(snapped_top.bottom(), initial.bottom(), "bottom edge remains anchored");

        // Bottom edge resized near workarea bottom (bottom=995 vs 1000, diff=5px <= 32px)
        let cur_bot = Rect::new(1000.0, 200.0, 464.0, 795.0);
        let snapped_bot = snap_resize_edges(cur_bot, Some(initial), MON, &[], &[], &p());
        assert_eq!(snapped_bot.y, initial.y, "top edge remains anchored");
        assert_eq!(snapped_bot.bottom(), workarea_bottom, "bottom edge snaps to workarea bottom margin");
    }

    #[test]
    fn negative_control_outside_proximity_does_not_snap() {
        let spotify = Rect::new(0.0, 44.0, 960.0, 964.0);
        let initial = Rect::new(1448.0, 44.0, 464.0, 964.0);
        // Resized to cur.x = 900 (difference from 968 is 68px > 32px proximity)
        let cur = Rect::new(900.0, 44.0, 1012.0, 964.0);
        let snapped = snap_resize_edges(cur, Some(initial), MON, &[], &[spotify], &p());
        assert_eq!(snapped.x, 900.0, "outside proximity gravity, edge does not snap");
    }

    #[test]
    fn max_ratio_relaxation_user_resized_large_pip() {
        let q = p();
        // User intentionally resizes a large PiP window: 1200x900 on 1920x964
        let large = Rect::new(712.0, 44.0, 1200.0, 900.0);
        let (w, h) = clamp_size(large, MON, &q);
        assert_eq!(w, 1200.0, "large resized width is not force-shrunk");
        assert_eq!(h, 900.0, "large resized height is not force-shrunk");

        let out = plan(&[large], MON, &[], &[], &q);
        assert_eq!(out[0].w, 1200.0, "planned width retains user-resized dimensions");
        assert_eq!(out[0].h, 900.0, "planned height retains user-resized dimensions");
    }

    #[test]
    fn mass_scaling_flick_threshold_large_less_than_small() {
        let small_m = mass_factor(200.0, 150.0, 1920.0, 1080.0);
        let large_m = mass_factor(1200.0, 900.0, 1920.0, 1080.0);
        assert!(large_m > small_m, "large PiP has higher mass factor");

        let v_small = flick_velocity_threshold(small_m);
        let v_large = flick_velocity_threshold(large_m);
        assert!(v_large < v_small, "larger PiP takes LESS flick velocity to swap rails ({v_large} < {v_small})");

        let d_small = flick_displacement_threshold(small_m, 700.0);
        let d_large = flick_displacement_threshold(large_m, 700.0);
        assert!(d_large < d_small, "larger PiP takes LESS displacement to swap rails ({d_large} < {d_small})");
    }

    #[test]
    fn gravity_snappiness_small_greater_than_large() {
        let small_m = mass_factor(200.0, 150.0, 1920.0, 1080.0);
        let large_m = mass_factor(1200.0, 900.0, 1920.0, 1080.0);

        let pull_small = gravity_pull(48.0, small_m);
        let pull_large = gravity_pull(48.0, large_m);
        assert!(pull_small > pull_large, "smaller PiP is snappier with higher gravity attraction ({pull_small} > {pull_large})");

        let k_small = mass_stiffness(260.0, small_m);
        let k_large = mass_stiffness(260.0, large_m);
        assert!(k_small > k_large, "smaller PiP has higher spring stiffness ({k_small} > {k_large})");
    }

    #[test]
    fn landscape_throw_for_any_size_pip() {
        let q = p();
        let mon = Rect::new(0.0, 0.0, 1920.0, 1080.0);
        // Large PiP window starting on Right rail
        let large_pip = Rect::new(712.0, 100.0, 1200.0, 800.0);
        let motion_flick_left = Motion {
            anchor: Some(Rect::new(712.0, 100.0, 1200.0, 800.0)),
            anchor_rail: Some(true), // Right rail
            velocity: (-250.0, 0.0), // Leftward flick
        };

        let rail = pick_rail(large_pip, Some(&motion_flick_left), mon, &q);
        assert_eq!(rail, false, "flick leftward swaps to Left rail (false)");

        let out = plan_with_motion(&[large_pip], &[motion_flick_left], mon, &[], &[], &q);
        assert_eq!(out[0].x, mon.x + q.margin, "lands on Left rail");
        assert_eq!(out[0].w, 1200.0, "user-resized width preserved on landing");
        assert_eq!(out[0].h, 800.0, "user-resized height preserved on landing");
    }

    #[test]
    fn portrait_throw_for_any_size_pip() {
        let q = p();
        // Portrait monitor (e.g. 1080x1920)
        let mon_portrait = Rect::new(0.0, 0.0, 1080.0, 1920.0);
        // Large PiP starting on Top rail
        let large_pip = Rect::new(8.0, 8.0, 900.0, 600.0);
        let motion_flick_down = Motion {
            anchor: Some(Rect::new(8.0, 8.0, 900.0, 600.0)),
            anchor_rail: Some(false), // Top rail
            velocity: (0.0, 350.0),   // Downward flick
        };

        let rail = pick_rail(large_pip, Some(&motion_flick_down), mon_portrait, &q);
        assert_eq!(rail, true, "flick downward in portrait swaps to Bottom rail (true)");

        let out = plan_with_motion(&[large_pip], &[motion_flick_down], mon_portrait, &[], &[], &q);
        assert_eq!(out[0].y, mon_portrait.bottom() - q.margin - 600.0, "lands on Bottom rail");
        assert_eq!(out[0].w, 900.0, "user-resized width preserved on landing");
        assert_eq!(out[0].h, 600.0, "user-resized height preserved on landing");
    }
}
