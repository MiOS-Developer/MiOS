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
}

/// Fill the flank left by the canonical centered opening rectangle, with the shared margin on both sides.
pub fn flank_width(monitor_width:f64,opening_width:f64,margin:f64)->f64 {((monitor_width-opening_width)/2.0-2.0*margin).max(0.0)}

/// Aspect-aware size clamp: width bounded by the minimum (absolute px and monitor fraction) and the maximum (monitor
/// fraction and the largest aspect-true size inside the usable area); height follows the content aspect. Inside
/// `size_snap` of the minimum, the launch (flank) width or the maximum, the width snaps exactly to it.
pub fn clamp_size(cur: Rect, mon: Rect, p: &Params) -> (f64, f64) {
    let aspect = if cur.h > 0.0 && cur.w > 0.0 { cur.w / cur.h } else { 16.0 / 9.0 };
    let max_w = (p.max_w_frac * mon.w).min((mon.w - 2.0 * p.margin).max(1.0)).min((mon.h - 2.0 * p.margin).max(1.0) * aspect).max(1.0);
    let min_w = (p.min_w_frac * mon.w).max(p.min_w_px).min(max_w);
    let mut w = cur.w.clamp(min_w, max_w);
    let magnets = [min_w, p.snap_width, max_w];
    if let Some(m) = magnets.iter().copied().filter(|m| *m > 0.0 && (w - m).abs() <= p.size_snap).min_by(|a, b| (w - a).abs().total_cmp(&(w - b).abs())) {
        w = m.clamp(min_w, max_w);
    }
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
pub fn plan(pips: &[Rect], mon: Rect, huds: &[Rect], p: &Params) -> Vec<Rect> {
    let same = |a: &[Rect], b: &[Rect]| a.iter().zip(b).all(|(a, b)| (a.x - b.x).abs() < 1e-3 && (a.y - b.y).abs() < 1e-3 && (a.w - b.w).abs() < 1e-3 && (a.h - b.h).abs() < 1e-3);
    let key = |s: &[Rect]| s.iter().fold((0.0, 0.0, 0.0), |k, r| (k.0 + r.y, k.1 + r.x, k.2 + r.w));
    let at = |out: &[Rect]| -> Vec<Rect> { out.iter().zip(pips).map(|(o, n)| Rect::new(o.x, o.y, n.w, n.h)).collect() };
    let mut seen = vec![plan_once(pips, mon, huds, p)];
    for _ in 0..PLAN_PASSES {
        let last = seen.last().expect("one pass");
        let next = plan_once(&at(last), mon, huds, p);
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

fn plan_once(pips: &[Rect], mon: Rect, huds: &[Rect], p: &Params) -> Vec<Rect> {
    let mut out: Vec<Rect> = pips.to_vec();
    let mut sizes = Vec::with_capacity(pips.len());
    let mut rails = Vec::with_capacity(pips.len());
    for r in pips {
        sizes.push(clamp_size(*r, mon, p));
        rails.push(pick_rail_right(*r, mon, p));
    }
    // Use the other flank before squeezing an overfull column. Preserve the operator's side when it fits.
    if p.left && p.right {
        let capacity=(mon.h-2.0*p.margin).max(1.0);
        for side in [false,true] {
            let total=|side:bool,rails:&[bool]|{let idx:Vec<_>=(0..pips.len()).filter(|i|rails[*i]==side).collect();idx.iter().map(|i|sizes[*i].1).sum::<f64>()+p.gap*idx.len().saturating_sub(1) as f64};
            while total(side,&rails)>capacity {
                let other=total(!side,&rails);
                let candidate=(0..pips.len()).rev().find(|i|rails[*i]==side&&other+sizes[*i].1+if other>0.0{p.gap}else{0.0}<=capacity);
                let Some(i)=candidate else {break};rails[i]=!side;
            }
        }
    }
    for side_right in [false, true] {
        let mut idx: Vec<usize> = (0..pips.len()).filter(|&i| rails[i] == side_right).collect();
        idx.sort_by(|&a, &b| pips[a].y.partial_cmp(&pips[b].y).unwrap());
        // Once both flanks are packed, stack at a uniformly smaller scale instead of overlapping.
        let available=idx.iter().map(|i|{let (w,h)=sizes[*i];let x=if side_right {mon.right()-p.margin-w}else{mon.x+p.margin};let (top,bottom)=column_bounds(x,w,h,mon,huds,p);(bottom-top).max(1.0)}).reduce(f64::min).unwrap_or(mon.h);
        let sum=idx.iter().map(|i|sizes[*i].1).sum::<f64>();
        let scale=if sum>0.0 {((available-p.gap*idx.len().saturating_sub(1) as f64).max(1.0)/sum).min(1.0)}else{1.0};
        for &i in &idx {sizes[i].0*=scale;sizes[i].1*=scale;}
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
    // A manually enlarged PiP can span both flanks. Resolve those collisions vertically too, inside the HUD bounds.
    let overlaps=|a:Rect,b:Rect|a.x<b.right()-0.5&&a.right()>b.x+0.5&&a.y<b.bottom()-0.5&&a.bottom()>b.y+0.5;
    let mut crowded=false;
    for i in 0..out.len() {
        for j in 0..i {
            if overlaps(out[i],out[j]) {
                let proposed=out[j].bottom()+p.gap;
                let (_,bottom)=column_bounds(out[i].x,out[i].w,out[i].h,mon,huds,p);
                if proposed+out[i].h<=bottom {out[i].y=proposed;}else{crowded=true;}
            }
        }
    }
    crowded|=(0..out.len()).any(|i|(0..i).any(|j|overlaps(out[i],out[j])));
    // MIOS-PIP-CROWDED-BEGIN
    if crowded {
        // One shared top-to-bottom sequence (operator order) at a uniform scale, on each PiP's configured rail and
        // clear of every HUD bar that crosses either flank column.
        let mut order:Vec<usize>=(0..out.len()).collect();
        order.sort_by(|&a,&b|pips[a].y.total_cmp(&pips[b].y).then(a.cmp(&b)));
        let (mut top,mut bottom)=(mon.y+p.margin,mon.bottom()-p.margin);
        for &i in &order {
            let x=if rails[i] {mon.right()-p.margin-out[i].w}else{mon.x+p.margin};
            let (t,b)=column_bounds(x,out[i].w,out[i].h,mon,huds,p);top=top.max(t);bottom=bottom.min(b);
        }
        let sum=out.iter().map(|r|r.h).sum::<f64>();
        let fit=((bottom-top-p.gap*out.len().saturating_sub(1) as f64).max(1.0)/sum.max(1.0)).min(1.0);
        let mut y=top;
        for &i in &order {let r=&mut out[i];r.w*=fit;r.h*=fit;r.x=if rails[i] {mon.right()-p.margin-r.w}else{mon.x+p.margin};r.y=y;y=r.bottom()+p.gap;}
    }
    // MIOS-PIP-CROWDED-END
    // Emit the integer logical geometry the daemon applies. Magnets are thresholds: a fractional target 48.09 px from a
    // line rounds to 48 when applied and snaps on the next tick. Planning on integers makes re-planning the applied
    // geometry exact.
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
        Params { left: true, right: true, margin: 16.0, gap: 12.0, hud_gap: 12.0, attract: 40.0, min_w_frac: 0.15, max_w_frac: 0.40, snap_width:0.0, min_w_px:0.0, size_snap:0.0 }
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

#[cfg(test)] mod flank_tests {
 use super::*;
 #[test] fn flank_uses_opening_geometry_and_global_margin(){assert_eq!(flank_width(1920.0,960.0,8.0),464.0);assert_eq!(flank_width(3840.0,1800.0,16.0),988.0);assert_eq!(flank_width(400.0,400.0,8.0),0.0);}
 #[test] fn tall_content_fits_without_changing_aspect(){let p=Params{left:true,right:true,margin:8.0,gap:8.0,hud_gap:8.0,attract:48.0,min_w_frac:0.2,max_w_frac:0.4,snap_width:0.0,min_w_px:0.0,size_snap:0.0};let (w,h)=clamp_size(Rect::new(0.0,0.0,200.0,1200.0),Rect::new(0.0,0.0,1920.0,964.0),&p);assert!(h<=948.0);assert!((w/h-1.0/6.0).abs()<1e-9);}
}

#[cfg(test)] mod collision_tests {
 use super::*;
 fn p()->Params {Params{left:true,right:true,margin:8.0,gap:8.0,hud_gap:8.0,attract:48.0,min_w_frac:0.24,max_w_frac:0.24,snap_width:0.0,min_w_px:0.0,size_snap:0.0}}
 fn overlap(a:Rect,b:Rect)->bool {a.x<b.right()-0.1&&a.right()>b.x+0.1&&a.y<b.bottom()-0.1&&a.bottom()>b.y+0.1}
 #[test] fn crowded_column_uses_the_other_flank_then_squeezes_without_overlap(){
  let mon=Rect::new(0.0,44.0,1920.0,964.0);
  for count in [2,3,4,8] {let input=vec![Rect::new(1500.0,200.0,460.0,300.0);count];let out=plan(&input,mon,&[],&p());assert_eq!(out.len(),count);
   for (i,a) in out.iter().enumerate(){assert!(a.y>=mon.y&&a.bottom()<=mon.bottom()+0.1);assert!((a.w/a.h-460.0/300.0).abs()<=1.0/a.h+1.0/a.w,"aspect within integer rounding: {a:?}");for b in &out[i+1..]{assert!(!overlap(*a,*b),"{count}: {out:?}");}}
  }
 }
 #[test] fn moving_one_pip_changes_its_neighbors_target(){let mon=Rect::new(0.0,0.0,1920.0,1080.0);let input=[Rect::new(1500.0,100.0,460.0,260.0),Rect::new(1500.0,180.0,460.0,260.0)];let out=plan(&input,mon,&[],&p());assert!(out[1].y>=out[0].bottom()+p().gap);assert!(!overlap(out[0],out[1]));}
}

#[cfg(test)] mod resize_tests {
 use super::*;
 #[test] fn smaller_and_larger_sizes_remain_free_with_a_launch_width_magnet(){
  let p=Params{left:true,right:true,margin:8.0,gap:8.0,hud_gap:8.0,attract:24.0,min_w_frac:0.02,max_w_frac:1.0,snap_width:464.0,min_w_px:0.0,size_snap:24.0};let mon=Rect::new(0.0,44.0,1920.0,964.0);
  for width in [180.0,320.0,700.0,1000.0] {let (w,h)=clamp_size(Rect::new(0.0,0.0,width,width/2.0),mon,&p);assert_eq!(w,width);assert_eq!(h,width/2.0);}
  for width in [444.0,464.0,480.0] {assert_eq!(clamp_size(Rect::new(0.0,0.0,width,width/2.0),mon,&p).0,464.0);}
 }
}

#[cfg(test)] mod enlarged_collision_tests {
 use super::*;
 #[test] fn manually_enlarged_windows_do_not_overlap_the_opposite_flank(){
 let p=Params{left:true,right:true,margin:8.0,gap:8.0,hud_gap:8.0,attract:24.0,min_w_frac:0.02,max_w_frac:1.0,snap_width:464.0,min_w_px:0.0,size_snap:24.0};let mon=Rect::new(0.0,44.0,1920.0,964.0);
 let out=plan(&[Rect::new(0.0,100.0,1000.0,620.0),Rect::new(900.0,200.0,1200.0,700.0)],mon,&[],&p);
 let a=out[0];let b=out[1];assert!(a.right()<=b.x||b.right()<=a.x||a.bottom()<=b.y||b.bottom()<=a.y,"{out:?}");assert!(out.iter().all(|r|r.y>=mon.y&&r.bottom()<=mon.bottom()));
 }
}

#[cfg(test)] mod pack_tests {
 use super::*;
 fn p()->Params {Params{left:true,right:true,margin:8.0,gap:8.0,hud_gap:8.0,attract:48.0,min_w_frac:0.02,max_w_frac:1.0,snap_width:464.0,min_w_px:160.0,size_snap:24.0}}
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
  let out=plan(&pips,mon,&[],&q);check(&out,mon,&[],&q);
  assert!(out.iter().any(|r|r.x<mon.cx())&&out.iter().any(|r|r.x>mon.cx()),"both rails used: {out:?}");
  assert!(out.iter().all(|r|(r.w-464.0).abs()<0.5),"three per rail fit at the launch width: {out:?}");
 }
 #[test] fn crowded_fallback_stays_clear_of_hud_bars_inside_the_work_area(){
  // An unreserved full-width bar inside the work area (auto-hide topbar shown) and two enlarged PiPs spanning both flanks.
  let mon=Rect::new(0.0,0.0,1920.0,1080.0);let q=p();let bar=Rect::new(0.0,0.0,1920.0,44.0);
  let input=[Rect::new(0.0,100.0,1000.0,620.0),Rect::new(900.0,200.0,1200.0,700.0)];
  let out=plan(&input,mon,&[bar],&q);
  check(&out,mon,&[bar],&q);
  // Every single pass must already be clean: the fixed-point loop is capped, so a later pass cannot be relied on to
  // repair a crowded squeeze that ran under the bar (the old fallback started at mon.y+margin).
  check(&plan_once(&input,mon,&[bar],&q),mon,&[bar],&q);
 }
 #[test] fn packing_is_stable_for_random_sets(){
  let mut seed=7u64;
  for case in 0..300 {
   let mon=Rect::new(0.0,44.0,1920.0,964.0);let q=p();
   let n=1+(lcg(&mut seed)*7.0) as usize;
   let pips:Vec<Rect>=(0..n).map(|_|{let w=120.0+lcg(&mut seed)*1300.0;let a=0.5+lcg(&mut seed)*1.5;Rect::new(lcg(&mut seed)*1800.0,44.0+lcg(&mut seed)*900.0,w,w/a)}).collect();
   let huds=[Rect::new(0.0,44.0,1920.0,40.0)];
   let out=plan(&pips,mon,&huds,&q);check(&out,mon,&huds,&q);
   // The daemon applies integer geometry and re-plans from it (with the unchanged natural sizes) on the next tick.
   let again=plan(&replan_input(&out,&pips),mon,&huds,&q);
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
   let out=plan(&pips,mon,&huds,&q);check(&out,mon,&huds,&q);
   for again in [plan(&replan_input(&out,&pips),mon,&huds,&q)] {
    for (a,b) in out.iter().zip(&again) {assert!((a.x-b.x).abs()<SETTLE_PX&&(a.y-b.y).abs()<SETTLE_PX&&(a.w-b.w).abs()<SETTLE_PX&&(a.h-b.h).abs()<SETTLE_PX,"case {case}: squeezed layout moves again {a:?} -> {b:?}");}
   }
  }
 }
}

#[cfg(test)] mod size_snap_tests {
 use super::*;
 fn p()->Params {Params{left:true,right:true,margin:8.0,gap:8.0,hud_gap:8.0,attract:48.0,min_w_frac:0.02,max_w_frac:1.0,snap_width:464.0,min_w_px:160.0,size_snap:24.0}}
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
