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
    pub snap_width: f64,
}

/// Fill the flank left by the canonical centered opening rectangle, with the shared margin on both sides.
pub fn flank_width(monitor_width:f64,opening_width:f64,margin:f64)->f64 {((monitor_width-opening_width)/2.0-2.0*margin).max(0.0)}

/// Aspect-aware size clamp: width bounded by monitor fractions, height follows the content aspect.
pub fn clamp_size(cur: Rect, mon: Rect, p: &Params) -> (f64, f64) {
    let aspect = if cur.h > 0.0 { cur.w / cur.h } else { 16.0 / 9.0 };
    let mut w = cur.w.clamp(p.min_w_frac * mon.w, (p.max_w_frac * mon.w).min((mon.w-2.0*p.margin).max(1.0)));
    if p.snap_width>0.0 && (w-p.snap_width).abs()<=p.attract {w=p.snap_width;}
    let h=w/aspect;
    let fit=((mon.h-2.0*p.margin).max(1.0)/h).min(1.0);
    (w*fit,h*fit)
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
    // A manually enlarged PiP can span both flanks. Resolve those collisions vertically too.
    let overlaps=|a:Rect,b:Rect|a.x<b.right()-0.5&&a.right()>b.x+0.5&&a.y<b.bottom()-0.5&&a.bottom()>b.y+0.5;
    let mut crowded=false;
    for i in 0..out.len() {
        for j in 0..i {
            if overlaps(out[i],out[j]) {
                let proposed=out[j].bottom()+p.gap;
                if proposed+out[i].h<=mon.bottom()-p.margin {out[i].y=proposed;}else{crowded=true;}
            }
        }
    }
    if crowded {
        let top=mon.y+p.margin;let bottom=mon.bottom()-p.margin;
        let sum=out.iter().map(|r|r.h).sum::<f64>();
        let fit=((bottom-top-p.gap*out.len().saturating_sub(1) as f64).max(1.0)/sum.max(1.0)).min(1.0);
        let mut y=top;
        for r in &mut out {let right=r.cx()>=mon.cx();r.w*=fit;r.h*=fit;r.x=if right {mon.right()-p.margin-r.w}else{mon.x+p.margin};r.y=y;y=r.bottom()+p.gap;}
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
        Params { left: true, right: true, margin: 16.0, gap: 12.0, hud_gap: 12.0, attract: 40.0, min_w_frac: 0.15, max_w_frac: 0.40, snap_width:0.0 }
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
 #[test] fn tall_content_fits_without_changing_aspect(){let p=Params{left:true,right:true,margin:8.0,gap:8.0,hud_gap:8.0,attract:48.0,min_w_frac:0.2,max_w_frac:0.4,snap_width:0.0};let (w,h)=clamp_size(Rect::new(0.0,0.0,200.0,1200.0),Rect::new(0.0,0.0,1920.0,964.0),&p);assert!(h<=948.0);assert!((w/h-1.0/6.0).abs()<1e-9);}
}

#[cfg(test)] mod collision_tests {
 use super::*;
 fn p()->Params {Params{left:true,right:true,margin:8.0,gap:8.0,hud_gap:8.0,attract:48.0,min_w_frac:0.24,max_w_frac:0.24,snap_width:0.0}}
 fn overlap(a:Rect,b:Rect)->bool {a.x<b.right()-0.1&&a.right()>b.x+0.1&&a.y<b.bottom()-0.1&&a.bottom()>b.y+0.1}
 #[test] fn crowded_column_uses_the_other_flank_then_squeezes_without_overlap(){
  let mon=Rect::new(0.0,44.0,1920.0,964.0);
  for count in [2,3,4,8] {let input=vec![Rect::new(1500.0,200.0,460.0,300.0);count];let out=plan(&input,mon,&[],&p());assert_eq!(out.len(),count);
   for (i,a) in out.iter().enumerate(){assert!(a.y>=mon.y&&a.bottom()<=mon.bottom()+0.1);assert!((a.w/a.h-460.0/300.0).abs()<1e-6);for b in &out[i+1..]{assert!(!overlap(*a,*b),"{count}: {out:?}");}}
  }
 }
 #[test] fn moving_one_pip_changes_its_neighbors_target(){let mon=Rect::new(0.0,0.0,1920.0,1080.0);let input=[Rect::new(1500.0,100.0,460.0,260.0),Rect::new(1500.0,180.0,460.0,260.0)];let out=plan(&input,mon,&[],&p());assert!(out[1].y>=out[0].bottom()+p().gap);assert!(!overlap(out[0],out[1]));}
}

#[cfg(test)] mod resize_tests {
 use super::*;
 #[test] fn smaller_and_larger_sizes_remain_free_with_a_launch_width_magnet(){
  let p=Params{left:true,right:true,margin:8.0,gap:8.0,hud_gap:8.0,attract:24.0,min_w_frac:0.02,max_w_frac:1.0,snap_width:464.0};let mon=Rect::new(0.0,44.0,1920.0,964.0);
  for width in [180.0,320.0,700.0,1000.0] {let (w,h)=clamp_size(Rect::new(0.0,0.0,width,width/2.0),mon,&p);assert_eq!(w,width);assert_eq!(h,width/2.0);}
  for width in [444.0,464.0,480.0] {assert_eq!(clamp_size(Rect::new(0.0,0.0,width,width/2.0),mon,&p).0,464.0);}
 }
}

#[cfg(test)] mod enlarged_collision_tests {
 use super::*;
 #[test] fn manually_enlarged_windows_do_not_overlap_the_opposite_flank(){
 let p=Params{left:true,right:true,margin:8.0,gap:8.0,hud_gap:8.0,attract:24.0,min_w_frac:0.02,max_w_frac:1.0,snap_width:464.0};let mon=Rect::new(0.0,44.0,1920.0,964.0);
 let out=plan(&[Rect::new(0.0,100.0,1000.0,620.0),Rect::new(900.0,200.0,1200.0,700.0)],mon,&[],&p);
 let a=out[0];let b=out[1];assert!(a.right()<=b.x||b.right()<=a.x||a.bottom()<=b.y||b.bottom()<=a.y,"{out:?}");assert!(out.iter().all(|r|r.y>=mon.y&&r.bottom()<=mon.bottom()));
 }
}
