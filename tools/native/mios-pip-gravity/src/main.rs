// AI-hint: mios-pip-gravity daemon -- gives floating Picture-in-Picture windows soft magnetic flank rails (left/right columns stacked top-to-bottom), HUD (topbar/dock) magnetism without hiding them, aspect-aware sizing and a springy settle. Polls the Hyprland IPC; idles while Hypr3D is active (PiP are freely resizable there). Config comes only from the layered SSOT table [desktop.pip_gravity].
// AI-related: usr/share/mios/mios.toml [desktop.pip_gravity], usr/lib/systemd/user/mios-pip-gravity.service, tools/native/mios-pip-gravity/src/gravity.rs, usr/share/mios/hyprland/hyprland.lua
mod gravity;
mod hud;
mod window;

use gravity::{plan, spring_step, Params, Rect};
use regex::Regex;
use serde_json::Value;
use std::collections::HashMap;
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::time::{Duration, Instant};

struct Cfg {
    enabled: bool,
    params: Params,
    matchers: Vec<Matcher>,
    initial_width_frac:f64,
    opening:Option<mios_resolver::window_opening::Policy>,
    fit_default_window:bool,
    keep_above:bool,
    hud_ns: Vec<String>,
    hud_settle: Duration,
    hud_max_height_frac: f64,
    stiffness: f64,
    damping: f64,
    settle_ms: u128,
    idle_ms: u64,
    config_poll_ms:u64,
    active_ms: u64,
}

struct Matcher {class:Option<Regex>,title:Option<Regex>,initial_title:Option<Regex>,sibling_class:Option<Regex>}
impl Matcher {
 fn matches(&self,w:&Value,all:&Value)->bool {
  let text=|key:&str|w.get(key).and_then(Value::as_str).unwrap_or("");
  let test=|r:&Option<Regex>,key:&str|r.as_ref().is_none_or(|r|r.is_match(text(key)));
  test(&self.class,"class") && test(&self.title,"title") && test(&self.initial_title,"initialTitle") && self.sibling_class.as_ref().is_none_or(|r|all.as_array().into_iter().flatten().any(|v|v["pid"]==w["pid"] && v["address"]!=w["address"] && v["class"].as_str().is_some_and(|c|r.is_match(c))))
 }
}
fn load_cfg() -> Result<Cfg, String> {
    let doc=mios_resolver::resolve_merged(None,false).map_err(|e|e.to_string())?;
    let acc=doc.get("desktop").and_then(|d|d.get("pip_gravity")).ok_or("desktop.pip_gravity missing")?;
    let f = |k: &str| -> Result<f64, String> {
        let value=acc.get(k).ok_or(format!("[desktop.pip_gravity].{k} missing"))?;
        let value=if let Some(path)=value.as_str(){path.split('.').try_fold(&doc,|v,k|v.get(k)).ok_or(format!("invalid PiP metric reference: {path}"))?}else{value};
        value.as_float().or(value.as_integer().map(|i|i as f64)).filter(|v|v.is_finite()&&*v>=0.0).ok_or(format!("invalid PiP metric {k}"))
    };
    let rails: Vec<String> = acc
        .get("rails")
        .and_then(|v| v.as_array())
        .ok_or("[desktop.pip_gravity].rails missing")?
        .iter()
        .filter_map(|v| v.as_str().map(String::from))
        .collect();
    let mut matchers = Vec::new();
    for m in acc.get("match").and_then(|v| v.as_array()).ok_or("[desktop.pip_gravity].match missing")? {
        let rx = |k: &str| m.get(k).and_then(|v| v.as_str()).map(|s| Regex::new(s).map_err(|e| e.to_string())).transpose();
        matchers.push(Matcher{class:rx("class")?,title:rx("title")?,initial_title:rx("initial_title")?,sibling_class:rx("sibling_class")?});
    }
    Ok(Cfg {
        enabled: acc.get("enabled").and_then(|v| v.as_bool()).unwrap_or(false),
        params: Params {
            left: rails.iter().any(|r| r == "left"),
            right: rails.iter().any(|r| r == "right"),
            margin: f("rail_margin")?,
            gap: f("stack_gap")?,
            hud_gap: f("hud_gap")?,
            attract: f("attract_px")?,
            min_w_frac: f("min_width_frac")?,
            max_w_frac: f("max_width_frac")?,
            snap_width:0.0,
        },
        matchers,
        initial_width_frac:f("initial_width_frac")?,
        opening:mios_resolver::window_opening::Policy::from_ssot(&doc)?,
        fit_default_window:acc.get("fit_default_window").and_then(|v|v.as_bool()).ok_or("PiP fit_default_window missing")?,
        keep_above:acc.get("keep_above").and_then(|v|v.as_bool()).ok_or("PiP keep_above missing")?,
        hud_ns: acc.get("hud_namespaces").and_then(|v| v.as_array()).map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect()).unwrap_or_default(),
        hud_settle: Duration::from_millis(f("hud_settle_ms")? as u64),
        hud_max_height_frac: { let v = f("hud_max_height_frac")?; if !(0.0..=1.0).contains(&v) { return Err("hud_max_height_frac must be 0..1".into()); } v },
        stiffness: f("spring_stiffness")?,
        damping: f("spring_damping")?,
        settle_ms: f("settle_ms")? as u128,
        idle_ms: f("idle_poll_ms")? as u64,
        config_poll_ms:f("config_poll_ms")? as u64,
        active_ms: f("active_poll_ms")? as u64,
    })
}

struct Ipc {
    path: String,
}
impl Ipc {
    fn new() -> Result<Self, String> {
        let rt = std::env::var("XDG_RUNTIME_DIR").map_err(|_| "XDG_RUNTIME_DIR unset")?;
        let sig = std::env::var("HYPRLAND_INSTANCE_SIGNATURE").map_err(|_| "HYPRLAND_INSTANCE_SIGNATURE unset")?;
        Ok(Ipc { path: format!("{rt}/hypr/{sig}/.socket.sock") })
    }
    fn send(&self, cmd: &str) -> Result<String, String> {
        let mut s = UnixStream::connect(&self.path).map_err(|e| e.to_string())?;
        s.set_read_timeout(Some(Duration::from_secs(2))).ok();
        s.write_all(cmd.as_bytes()).map_err(|e| e.to_string())?;
        let mut out = String::new();
        s.read_to_string(&mut out).map_err(|e| e.to_string())?;
        Ok(out)
    }
    fn json(&self, what: &str) -> Option<Value> {
        serde_json::from_str(&self.send(&format!("j/{what}")).ok()?).ok()
    }
}

#[cfg(test)]
fn hypr3d_active(path: &str) -> bool {
    if path.is_empty() {
        return false;
    }
    std::fs::read(path)
        .map(|b| String::from_utf8_lossy(&b).lines().rev().find(|l| l.starts_with("active=")).map(|l| l.starts_with("active=1")).unwrap_or(false))
        .unwrap_or(false)
}

fn num(v: &Value, i: usize) -> f64 {
    v.get(i).and_then(|x| x.as_f64()).unwrap_or(0.0)
}

/// Id of the monitor whose logical rectangle contains (x, y).
fn monitor_at(mons: &Value, x: f64, y: f64) -> Option<i64> {
    mons.as_array()?.iter().find_map(|m| {
        let g = |k: &str| m.get(k).and_then(|v| v.as_f64()).unwrap_or(0.0);
        let sc = g("scale").max(0.1);
        let (mx, my, mw, mh) = (g("x"), g("y"), g("width") / sc, g("height") / sc);
        (x >= mx && x < mx + mw && y >= my && y < my + mh).then(|| m.get("id").and_then(|i| i.as_i64())).flatten()
    })
}

#[cfg(test)]
mod monitor_tests {
    use super::monitor_at;
    #[test]
    fn centre_picks_owning_monitor_and_misses_outside() {
        let m: serde_json::Value = serde_json::json!([
            {"id": 0, "x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1.0},
            {"id": 1, "x": 1920, "y": 0, "width": 1920, "height": 1080, "scale": 1.0}
        ]);
        assert_eq!(monitor_at(&m, 1526.0, 827.0), Some(0));
        assert_eq!(monitor_at(&m, 2100.0, 100.0), Some(1));
        assert_eq!(monitor_at(&m, 5000.0, 100.0), None);
    }

    #[test]
    fn hypr3d_status_uses_the_latest_frame_line() {
        let p = std::env::temp_dir().join(format!("mios-pg-status-{}", std::process::id()));
        std::fs::write(&p, "active=1 frames=186 transition=0.6 target=0\nrender: x\nactive=0 frames=187 transition=0 target=0\n").unwrap();
        assert!(!super::hypr3d_active(p.to_str().unwrap()));
        std::fs::write(&p, "active=0 frames=1\n--- frame 2\nactive=1 frames=2 transition=1 target=1\nrender: y\n").unwrap();
        assert!(super::hypr3d_active(p.to_str().unwrap()));
        let _ = std::fs::remove_file(&p);
        assert!(!super::hypr3d_active(p.to_str().unwrap()), "missing file means flat desktop");
        assert!(!super::hypr3d_active(""), "unset path means flat desktop");
    }
}

struct Track {
    last: Rect,
    expected: Option<Rect>,
    stable_since: Instant,
    settled: bool,
    anim: Option<([f64; 4], [f64; 4], Rect)>,
}

fn room_active(ipc:&Ipc)->Result<bool,String> {
 let reply=ipc.send("/eval local p=hl.plugin and hl.plugin.hypr3d;local a=false;if p and p.active then local ok,v=pcall(p.active);a=ok and v and true or false end;error('MIOS_PIP_ROOM:'..tostring(a),0)")?;
 if reply.contains("MIOS_PIP_ROOM:true") {Ok(true)} else if reply.contains("MIOS_PIP_ROOM:false") {Ok(false)} else {Err("native room status unavailable".into())}
}
fn rail_params(cfg:&Cfg,mon:Rect)->Params {
 let mut p=cfg.params.clone();
 if cfg.fit_default_window {if let Some(policy)=&cfg.opening {
  let (opening_w,_)=policy.size(mon.w,mon.h,None);
  let width=gravity::flank_width(mon.w,opening_w,p.margin);
  if width>=1.0 {p.snap_width=width;}
 }}
 p
}
fn keep_above(ipc:&Ipc,clients:&Value)->Result<(),String>{
 let list=clients.as_array().ok_or("native window list missing")?;
 let mut pip_seen=false;let mut obscured=false;
 for w in list {if window::has_tag(w,window::MANAGED){pip_seen=true;}else if pip_seen&&w["mapped"]==true&&w["hidden"]==false&&w["floating"]==true{obscured=true;}}
 if !obscured{return Ok(());}
 for w in list.iter().filter(|w|window::has_tag(w,window::MANAGED)&&w["hidden"]==false){
  let id=window::Identity::read(w)?;
  let reply=ipc.send(&format!("/eval {}dispatch(hl.dsp.window.alter_zorder({{mode='top',window=target}}));error('MIOS_PIP_RAISED',0)",id.guard()))?;
  if !reply.contains("MIOS_PIP_RAISED"){return Err(reply);}
 }
 Ok(())
}
fn apply(ipc:&Ipc,cfg:&Cfg,w:&Value,mode:&str)->Result<(),String> {
 let mons=ipc.json("monitors").ok_or("native monitors unavailable")?;
 let mid=w["monitor"].as_i64().ok_or("window monitor missing")?;
 let mon=mons.as_array().and_then(|v|v.iter().find(|m|m["id"].as_i64()==Some(mid))).ok_or("window monitor removed")?;
 let scale=mon["scale"].as_f64().unwrap_or(1.0).max(0.1);
 let reserved=mon.get("reserved").cloned().unwrap_or_default();
 let area=Rect::new(mon["x"].as_f64().unwrap_or(0.0)+num(&reserved,0),mon["y"].as_f64().unwrap_or(0.0)+num(&reserved,1),mon["width"].as_f64().ok_or("monitor width missing")?/scale-num(&reserved,0)-num(&reserved,2),mon["height"].as_f64().ok_or("monitor height missing")?/scale-num(&reserved,1)-num(&reserved,3));
 let p=rail_params(cfg,area);
 let width=if p.snap_width>0.0 {p.snap_width}else{area.w*cfg.initial_width_frac};
 if let Some(lua)=window::plan(w,mode,width)? {
  let reply=ipc.send(&format!("/eval {lua}"))?;
  if !reply.contains("MIOS_PIP_APPLIED") {return Err(reply);}
 }
 Ok(())
}
fn window_command()->Result<(),String> {
 let args:Vec<_>=std::env::args().skip(1).collect();
 let mode=args.first().ok_or("PiP command missing")?;
 let arg=|name:&str|args.windows(2).find(|v|v[0]==name).map(|v|v[1].as_str());
 let address=arg("--window").ok_or("--window required")?;
 let ipc=Ipc::new()?;let clients=ipc.json("clients").ok_or("native window list unavailable")?;
 let w=clients.as_array().and_then(|v|v.iter().find(|w|w["address"].as_str()==Some(address))).ok_or("window no longer exists")?;
 let id=window::Identity::read(w)?;
 if let Some(pid)=arg("--pid") {if pid.parse::<u64>().ok()!=Some(id.pid) {return Err("preview window process changed".into());}}
 if let Some(stable)=arg("--stable-id") {if stable!=id.stable {return Err("preview native window identity changed".into());}}
 if mode=="status" {println!("{}",window::status(w));return Ok(());}
 apply(&ipc,&load_cfg()?,w,mode)?;
 let refreshed=ipc.json("clients").ok_or("could not confirm native PiP state")?;
 let live=refreshed.as_array().and_then(|v|v.iter().find(|w|id.matches(w))).ok_or("window changed before confirming PiP state")?;
 let mut result=window::status(live);result["applied"]=serde_json::json!(true);println!("{result}");Ok(())
}
fn main() {
 if matches!(std::env::args().nth(1).as_deref(),Some("toggle"|"on"|"off"|"status")) {if let Err(e)=window_command() {eprintln!("mios-pip-gravity: {e}");std::process::exit(1);}return;}

    if std::env::args().nth(1).as_deref() == Some("check") {
        match load_cfg() {
            Ok(c) => println!("ok enabled={} rails L={} R={} matchers={}", c.enabled, c.params.left, c.params.right, c.matchers.len()),
            Err(e) => {
                eprintln!("{e}");
                std::process::exit(2)
            }
        }
        return;
    }
    let mut cfg = load_cfg().unwrap_or_else(|e| {
        eprintln!("mios-pip-gravity: {e}");
        std::process::exit(2)
    });
    let ipc = Ipc::new().unwrap_or_else(|e| {
        eprintln!("mios-pip-gravity: {e}");
        std::process::exit(2)
    });
    let mut tracks: HashMap<String, Track> = HashMap::new();
    let mut huds = hud::HudTracker::default();
    let mut last_tick = Instant::now();
    let mut config_at=Instant::now();
    loop {
        let busy = tracks.values().any(|t| t.anim.is_some() || !t.settled);
        std::thread::sleep(Duration::from_millis(if busy { cfg.active_ms } else { cfg.idle_ms }));
        let now = Instant::now();
        let dt = now.duration_since(last_tick).as_secs_f64().min(0.05);
        last_tick = now;
        if now.duration_since(config_at)>=Duration::from_millis(cfg.config_poll_ms.max(1)) {
            config_at=now;
            match load_cfg(){Ok(next)=>cfg=next,Err(e)=>eprintln!("PiP config rejected; retaining validated policy: {e}")}
        }
        if !cfg.enabled {tracks.clear();continue;}
        let room=match room_active(&ipc){Ok(v)=>v,Err(_)=>{tracks.clear();continue;}};
        let (Some(clients), Some(mons)) = (ipc.json("clients"), ipc.json("monitors")) else { continue };
        // Adoption also occurs while the room is active. Only flat-desktop gravity is suspended there.
        for c in clients.as_array().into_iter().flatten() {
            if c["mapped"]==true&&c["hidden"]==false&&c["fullscreen"].as_i64()==Some(0)
                &&!window::has_tag(c,window::MANAGED)&&!window::has_tag(c,window::OPT_OUT)
                &&cfg.matchers.iter().any(|m|m.matches(c,&clients)) {
                if let Err(e)=apply(&ipc,&cfg,c,"on"){eprintln!("PiP adoption: {e}");}
            }
        }
        if room {tracks.clear();continue;}
        if cfg.keep_above {if let Err(e)=keep_above(&ipc,&clients){eprintln!("PiP stacking: {e}");}}
        // HUD bars, sampled every tick; only settled, bar-sized rects constrain PiPs (the dock morphing into the
        // full-pane app drawer is a transient overlay, not a bar).
        {
            let heights: HashMap<String, f64> = mons.as_array().into_iter().flatten().filter_map(|m| {
                let sc = m.get("scale").and_then(|s| s.as_f64()).unwrap_or(1.0).max(0.1);
                Some((m.get("name")?.as_str()?.to_string(), m.get("height")?.as_f64()? / sc))
            }).collect();
            let mut samples = Vec::new();
            if let Some(Value::Object(m)) = ipc.json("layers") {
                for (out, mon) in m {
                    for (_, lvl) in mon.get("levels").and_then(|l| l.as_object()).into_iter().flatten() {
                        for l in lvl.as_array().into_iter().flatten() {
                            let ns = l.get("namespace").and_then(|n| n.as_str()).unwrap_or("");
                            if cfg.hud_ns.iter().any(|h| h == ns) {
                                let g = |k: &str| l.get(k).and_then(|v| v.as_f64()).unwrap_or(0.0);
                                samples.push(hud::Sample { key: format!("{out}/{ns}"), rect: Rect::new(g("x"), g("y"), g("w"), g("h")), output_height: heights.get(&out).copied().unwrap_or(0.0) });
                            }
                        }
                    }
                }
            }
            huds.update(&samples, now, cfg.hud_settle, cfg.hud_max_height_frac);
        }
        // candidate PiPs, grouped by monitor id
        let mut by_mon: HashMap<i64, Vec<(String, Rect)>> = HashMap::new();
        for c in clients.as_array().into_iter().flatten() {
            let s = |k: &str| c.get(k).and_then(Value::as_str).unwrap_or("");
            let b = |k: &str| c.get(k).and_then(|v| v.as_bool()).unwrap_or(false);
            let ws = c.get("workspace").and_then(|w| w.get("name")).and_then(|n| n.as_str()).unwrap_or("");
            if !b("mapped") || b("hidden") || ws.starts_with("special") || c.get("fullscreen").and_then(|f| f.as_i64()).unwrap_or(0) != 0 {
                continue;
            }
            if window::has_tag(c,window::OPT_OUT) {continue;}
            let hit = cfg.matchers.iter().any(|m|m.matches(c,&clients));
            let managed=window::has_tag(c,window::MANAGED);
            if !hit && !managed {continue;}
            if !managed {continue;}
            if !b("floating") {continue;}
            let at = c.get("at").cloned().unwrap_or_default();
            let sz = c.get("size").cloned().unwrap_or_default();
            let rect = Rect::new(num(&at, 0), num(&at, 1), num(&sz, 0), num(&sz, 1));
            // The monitor owning the window's centre wins: Hyprland keeps a pinned window's old
            // monitor id after it is moved across outputs, which would plan it on the wrong one.
            let field = c.get("monitor").and_then(|m| m.as_i64()).unwrap_or(0);
            let mid = monitor_at(&mons, rect.cx(), rect.cy()).unwrap_or(field);
            if mid != field {
                // Re-home it so the compositor renders it on the output it now sits on (pin is kept).
                if let Some(name) = mons.as_array().and_then(|a| a.iter().find(|m| m.get("id").and_then(|i| i.as_i64()) == Some(mid))).and_then(|m| m.get("name")).and_then(|n| n.as_str()) {
                    // move(monitor) resets the position, so put it back where it was dropped.
                    let Ok(id) = window::Identity::read(c) else {continue};
                    let _ = ipc.send(&format!(
                        "/eval {}hl.dispatch(hl.dsp.window.move({{monitor={},window=target}}));hl.dispatch(hl.dsp.window.move({{x={},y={},window=target}}))",
                        id.guard(), serde_json::to_string(name).unwrap(), rect.x.round(), rect.y.round()
                    ));
                }
            }
            by_mon.entry(mid).or_default().push((s("address").to_string(), rect));
        }
        let live: Vec<String> = by_mon.values().flatten().map(|(a, _)| a.clone()).collect();
        tracks.retain(|a, _| live.contains(a));
        for (mid, pips) in by_mon {
            let Some(m) = mons.as_array().and_then(|a| a.iter().find(|m| m.get("id").and_then(|i| i.as_i64()) == Some(mid))) else { continue };
            let sc = m.get("scale").and_then(|s| s.as_f64()).unwrap_or(1.0).max(0.1);
            let res = m.get("reserved").cloned().unwrap_or_default();
            let (mx, my) = (m.get("x").and_then(|v| v.as_f64()).unwrap_or(0.0), m.get("y").and_then(|v| v.as_f64()).unwrap_or(0.0));
            let (mw, mh) = (m.get("width").and_then(|v| v.as_f64()).unwrap_or(0.0) / sc, m.get("height").and_then(|v| v.as_f64()).unwrap_or(0.0) / sc);
            let mon = Rect::new(mx + num(&res, 0), my + num(&res, 1), mw - num(&res, 0) - num(&res, 2), mh - num(&res, 1) - num(&res, 3));
            let mon_huds: Vec<Rect> = huds.rects().into_iter().filter(|r| r.cx() >= mx && r.cx() < mx + mw).collect();
            let rects: Vec<Rect> = pips.iter().map(|(_, r)| *r).collect();
            let targets = plan(&rects, mon, &mon_huds, &rail_params(&cfg,mon));
            for ((addr, cur), tgt) in pips.iter().zip(targets) {
                let t = tracks.entry(addr.clone()).or_insert(Track { last: *cur, expected: None, stable_since: now, settled: false, anim: None });
                let close = |a: Rect, b: Rect| (a.x - b.x).abs() < 3.0 && (a.y - b.y).abs() < 3.0 && (a.w - b.w).abs() < 3.0 && (a.h - b.h).abs() < 3.0;
                let ours = t.expected.map_or(false, |e| close(e, *cur));
                if !ours && !close(t.last, *cur) {
                    // moved/resized by the operator: wait until they let go, then re-plan
                    t.anim = None;
                    t.settled = false;
                    t.stable_since = now;
                }
                t.last = *cur;
                // Another PiP can change a settled window's target while this window itself is still.
                if t.settled && !close(tgt,*cur) {
                    t.settled=false;
                    t.stable_since=now-Duration::from_millis(cfg.settle_ms as u64);
                }
                if let Some((_,_,goal))=t.anim.as_mut() {*goal=tgt;}
                if t.anim.is_none() && !t.settled && now.duration_since(t.stable_since).as_millis() >= cfg.settle_ms {
                    if close(tgt, *cur) {
                        t.settled = true;
                    } else {
                        t.anim = Some(([cur.x, cur.y, cur.w, cur.h], [0.0; 4], tgt));
                    }
                }
                if let Some((p, v, tg)) = t.anim.as_mut() {
                    let goal = [tg.x, tg.y, tg.w, tg.h];
                    let mut done = true;
                    for i in 0..4 {
                        done &= spring_step(&mut p[i], &mut v[i], goal[i], cfg.stiffness, cfg.damping, dt);
                    }
                    let r = Rect::new(p[0].round(), p[1].round(), p[2].round().max(1.0), p[3].round().max(1.0));
                    let Some(native) = clients.as_array().and_then(|v|v.iter().find(|w|w["address"].as_str()==Some(addr))) else {continue};
                    let Ok(id) = window::Identity::read(native) else {continue};
                    let cmd = format!(
                        "/eval {}hl.dispatch(hl.dsp.window.resize({{x={},y={},exact=true,window=target}}));hl.dispatch(hl.dsp.window.move({{x={},y={},window=target}}))",
                        id.guard(), r.w, r.h, r.x, r.y
                    );
                    let _ = ipc.send(&cmd);
                    t.expected = Some(r);
                    if done {
                        t.anim = None;
                        t.settled = true;
                    }
                }
            }
        }
    }
}

#[cfg(test)]
mod matcher_tests {
 use super::*;
 #[test] fn discord_popout_uses_stable_initial_title() {
  let m=Matcher{class:Some(Regex::new("(?i)^discord$").unwrap()),initial_title:Some(Regex::new("^Discord Popout$").unwrap()),title:None,sibling_class:None};
  let pop=serde_json::json!({"class":"discord","title":"Changing channel name","initialTitle":"Discord Popout"});
  assert!(m.matches(&pop,&Value::Null));
  let main=serde_json::json!({"class":"discord","title":"Discord Popout","initialTitle":"#general | Server - Discord"});
  assert!(!m.matches(&main,&Value::Null));
 }
 #[test] fn spotify_browser_tab_is_not_the_native_miniplayer() {
  let m=Matcher{class:Some(Regex::new("^chromium-browser$").unwrap()),initial_title:None,sibling_class:Some(Regex::new("(?i)^spotify$").unwrap()),title:None};
  let pop=serde_json::json!({"address":"0x1","pid":123,"class":"chromium-browser","initialTitle":"Spotify - Web Player: Music for everyone"});
  assert!(!m.matches(&pop,&serde_json::json!([])));
  assert!(!m.matches(&pop,&serde_json::json!([{"address":"0x2","pid":124,"class":"spotify"}])));
  assert!(m.matches(&pop,&serde_json::json!([{"address":"0x2","pid":123,"class":"spotify"}])));
  let mut track=pop.clone();track["initialTitle"]=serde_json::json!("Any track • Any artist");assert!(m.matches(&track,&serde_json::json!([{"address":"0x2","pid":123,"class":"spotify"}])));
 }
}
