// AI-hint: mios-pip-gravity daemon -- gives floating Picture-in-Picture windows soft magnetic flank rails (left/right columns stacked top-to-bottom, packed across both rails), HUD (topbar/dock) magnetism without hiding them, aspect-aware sizing with min/launch/max size magnets on each PiP's natural size, and a springy settle. Adopts Spotify/Discord/browser PiP windows on map, keeps them on top through Hyprland's native pinned layer, and never moves them for maximize/fullscreen/Theatre. Woken by compositor events (paced), polling as the fallback; flat placement pauses while Hypr3D is active and resumes from the kept slots. Config comes only from the layered SSOT table [desktop.pip_gravity].
// AI-related: usr/share/mios/mios.toml [desktop.pip_gravity], usr/lib/systemd/user/mios-pip-gravity.service, tools/native/mios-pip-gravity/src/{gravity.rs,matrix.rs,window.rs,hud.rs}, usr/share/mios/hyprland/hyprland.lua, usr/share/doc/mios/desktop-pip.md, tests/test-pip-behaviours-live.py
mod gravity;
mod hud;
mod matrix;
mod window;

use gravity::{plan, spring_step, Params, Rect};
use regex::Regex;
use serde_json::Value;
use std::collections::HashMap;
use std::io::{BufRead, BufReader, Read, Write};
use std::os::unix::net::UnixStream;
use std::sync::mpsc;
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
    fullscreen_release: Duration,
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
/// Automatic-adoption rules from `[desktop.pip_gravity].match`.
fn matchers_from(acc:&toml::Value)->Result<Vec<Matcher>,String> {
    let mut matchers = Vec::new();
    for m in acc.get("match").and_then(|v| v.as_array()).ok_or("[desktop.pip_gravity].match missing")? {
        let rx = |k: &str| m.get(k).and_then(|v| v.as_str()).map(|s| Regex::new(s).map_err(|e| e.to_string())).transpose();
        matchers.push(Matcher{class:rx("class")?,title:rx("title")?,initial_title:rx("initial_title")?,sibling_class:rx("sibling_class")?});
    }
    Ok(matchers)
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
    let matchers = matchers_from(acc)?;
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
            min_w_px: f("min_width_px")?,
            size_snap: f("size_snap_px")?,
        },
        matchers,
        initial_width_frac:f("initial_width_frac")?,
        opening:mios_resolver::window_opening::Policy::from_ssot(&doc)?,
        fit_default_window:acc.get("fit_default_window").and_then(|v|v.as_bool()).ok_or("PiP fit_default_window missing")?,
        keep_above:acc.get("keep_above").and_then(|v|v.as_bool()).ok_or("PiP keep_above missing")?,
        hud_ns: acc.get("hud_namespaces").and_then(|v| v.as_array()).map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect()).unwrap_or_default(),
        hud_settle: Duration::from_millis(f("hud_settle_ms")? as u64),
        hud_max_height_frac: { let v = f("hud_max_height_frac")?; if !(0.0..=1.0).contains(&v) { return Err("hud_max_height_frac must be 0..1".into()); } v },
        fullscreen_release: Duration::from_millis(f("fullscreen_release_ms")? as u64),
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
    /// The size the operator (or the client) chose: planning input for clamping, size magnets and the squeeze. Only a
    /// resize that is not ours updates it, so a squeezed size never feeds back into the plan (see gravity::plan).
    natural: (f64, f64),
    /// The last frames this daemon dispatched (newest last). A busy compositor applies a frame a tick or more late, so
    /// a window showing ANY of them is ours, not an operator resize (that misread adopted animation frames as natural
    /// sizes and shrank PiPs step by step).
    expected: Vec<Rect>,
    stable_since: Instant,
    settled: bool,
    anim: Option<([f64; 4], [f64; 4], Rect)>,
    /// Hypr3D owned placement since `last` was observed: the room may move the window and change its native raster
    /// size, and leaving the room resumes flat placement from the kept slot (`last`, `natural`), not from what the
    /// room left behind.
    from_room: bool,
}

/// Dispatched frames remembered per PiP (about 260 ms of animation at the active tick).
const EXPECTED_FRAMES: usize = 16;

impl Track {
    fn new(cur: Rect, now: Instant) -> Self {
        Track { last: cur, natural: (cur.w, cur.h), expected: Vec::new(), stable_since: now, settled: false, anim: None, from_room: false }
    }
    /// Planning input: the kept flat slot when returning from the room, else the current position; always the
    /// natural size.
    fn plan_input(&self, cur: Rect) -> Rect {
        let at = if self.from_room { self.last } else { cur };
        Rect::new(at.x, at.y, self.natural.0, self.natural.1)
    }
    /// The window shows frames of ours. Position and size are matched independently: a move applies at once while a
    /// resize lands only when the client commits a buffer of the new size, so under load the position can come from a
    /// newer frame than the size.
    fn ours(&self, cur: Rect) -> bool {
        let near = |a: f64, b: f64| (a - b).abs() < gravity::SETTLE_PX;
        self.expected.iter().any(|e| near(e.x, cur.x) && near(e.y, cur.y)) && self.expected.iter().any(|e| near(e.w, cur.w) && near(e.h, cur.h))
    }
    /// Record a frame dispatched to the compositor.
    fn dispatched(&mut self, frame: Rect) {
        if self.expected.len() >= EXPECTED_FRAMES {
            self.expected.remove(0);
        }
        self.expected.push(frame);
    }
    /// Observe the window's current geometry once per flat tick (before the settle/animate step).
    fn observe(&mut self, cur: Rect, now: Instant, settle: Duration) {
        let close = |a: Rect, b: Rect| (a.x - b.x).abs() < gravity::SETTLE_PX && (a.y - b.y).abs() < gravity::SETTLE_PX && (a.w - b.w).abs() < gravity::SETTLE_PX && (a.h - b.h).abs() < gravity::SETTLE_PX;
        if self.from_room {
            // Back from the room: whatever moved or resized it there was the room, not the operator. Keep the natural
            // size and restore the kept slot at once.
            self.from_room = false;
            self.expected.clear();
            if !close(self.last, cur) {
                self.anim = None;
                self.settled = false;
                self.stable_since = now.checked_sub(settle).unwrap_or(now);
            }
        } else if !self.ours(cur) && !close(self.last, cur) {
            // moved/resized by the operator (or the client): wait until they let go, then re-plan
            if (cur.w - self.last.w).abs() >= gravity::SETTLE_PX || (cur.h - self.last.h).abs() >= gravity::SETTLE_PX {
                trace(&format!("natural size {:?} -> {:?} (resize not ours; last {:?}, newest frame {:?})", self.natural, (cur.w, cur.h), self.last, self.expected.last()));
                self.natural = (cur.w, cur.h);
            }
            self.anim = None;
            self.settled = false;
            self.stable_since = now;
        }
        self.last = cur;
    }
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
/// Native stay-on-top (see window::stacking): re-pin a floating managed PiP that has stayed unpinned for `grace`
/// (a fullscreen PiP is unpinned natively and restored by binds:allow_pin_fullscreen, so a transient never counts), and
/// raise a PiP only above an overlapping PINNED non-PiP window.
fn keep_above(ipc:&Ipc,clients:&Value,unpinned:&mut HashMap<String,Instant>,now:Instant,grace:Duration)->Result<(),String>{
 let list=clients.as_array().ok_or("native window list missing")?;
 let (repin,raise)=window::stacking(clients);
 let lost:Vec<String>=repin.iter().filter_map(|i|list[*i]["address"].as_str().map(String::from)).collect();
 unpinned.retain(|a,_|lost.contains(a));
 for i in repin {
  let w=&list[i];let Some(addr)=w["address"].as_str() else {continue};
  let since=*unpinned.entry(addr.to_string()).or_insert(now);
  if now.duration_since(since)<grace {continue;}
  let id=window::Identity::read(w)?;
  let reply=ipc.send(&format!("/eval {}dispatch(hl.dsp.window.pin({{window=target,action='enable'}}));error('MIOS_PIP_PINNED',0)",id.guard()))?;
  if !reply.contains("MIOS_PIP_PINNED"){return Err(reply);}
  unpinned.remove(addr);
 }
 for i in raise {
  let id=window::Identity::read(&list[i])?;
  let reply=ipc.send(&format!("/eval {}dispatch(hl.dsp.window.alter_zorder({{mode='top',window=target}}));error('MIOS_PIP_RAISED',0)",id.guard()))?;
  if !reply.contains("MIOS_PIP_RAISED"){return Err(reply);}
 }
 Ok(())
}
/// Compositor events wake the loop at once (adoption on map, stay-on-top on focus/pin/fullscreen changes); polling
/// remains the fallback when the event socket is unavailable. Title changes are deliberately not wake events: a client can
/// retitle every frame, and no adoption rule needs a late title (Discord matches its initial title, Spotify its sibling).
const WAKE_EVENTS:&[&str]=&["openwindow","closewindow","activewindowv2","fullscreen","changefloatingmode","pin","movewindowv2","workspacev2","focusedmonv2","monitoraddedv2","monitorremovedv2","openlayer","closelayer","activespecialv2"];
fn spawn_events(tx:mpsc::SyncSender<()>) {
 let Ok(rt)=std::env::var("XDG_RUNTIME_DIR") else {return};let Ok(sig)=std::env::var("HYPRLAND_INSTANCE_SIGNATURE") else {return};
 let path=format!("{rt}/hypr/{sig}/.socket2.sock");
 std::thread::spawn(move||loop {
  if let Ok(s)=UnixStream::connect(&path) {
   for line in BufReader::new(s).lines() {
    let Ok(line)=line else {break};
    if WAKE_EVENTS.contains(&line.split(">>").next().unwrap_or("")) {let _=tx.try_send(());}
   }
  }
  std::thread::sleep(Duration::from_secs(1));
 });
}
/// Floor for the event-paced tick (ms); `active_poll_ms` below it is raised to it.
const MIN_TICK_MS:u64=8;
/// Wait for the next tick: the poll timeout, or an earlier compositor event. Event wakes are coalesced and paced, so a
/// burst of events (hover focus, a title updating every frame, a layout storm) yields at most one tick per `floor`
/// (one round of native IPC requests, never a process spawn), not one tick per event.
fn wait_tick(wake:&mpsc::Receiver<()>,timeout:Duration,last_tick:Instant,floor:Duration) {
 let _=wake.recv_timeout(timeout);
 let gap=floor.saturating_sub(last_tick.elapsed());
 if !gap.is_zero() {std::thread::sleep(gap);}
 while wake.try_recv().is_ok() {}
}
/// Opt-in diagnostics on stderr (MIOS_PIP_GRAVITY_TRACE=1): room transitions, dropped tracks, natural-size updates.
fn trace(msg:&str) {
 static ON: std::sync::OnceLock<bool> = std::sync::OnceLock::new();
 if *ON.get_or_init(||std::env::var("MIOS_PIP_GRAVITY_TRACE").is_ok_and(|v|v=="1")) {eprintln!("mios-pip-gravity: {msg}");}
}
/// A track (kept slot + natural size) survives while its window still exists as a managed PiP, even when it is
/// transiently out of the plan (hidden, on a special workspace, not floating, mid room transition); it is dropped only
/// once the window closed or left PiP.
fn keep_track(addr:&str,planned:&[String],clients:&Value)->bool {
 planned.iter().any(|a|a==addr)||clients.as_array().into_iter().flatten().any(|c|c["address"].as_str()==Some(addr)&&window::has_tag(c,window::MANAGED)&&!window::has_tag(c,window::OPT_OUT))
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
    let (wake_tx, wake) = mpsc::sync_channel::<()>(1);
    spawn_events(wake_tx);
    let mut tracks: HashMap<String, Track> = HashMap::new();
    let mut huds = hud::HudTracker::default();
    let mut latch = matrix::Latch::default();
    let mut unpinned: HashMap<String, Instant> = HashMap::new();
    let mut last_tick = Instant::now();
    let mut config_at=Instant::now();
    let mut was_room = false;
    loop {
        let busy = tracks.values().any(|t| t.anim.is_some() || !t.settled);
        wait_tick(&wake, Duration::from_millis(if busy { cfg.active_ms } else { cfg.idle_ms }), last_tick, Duration::from_millis(cfg.active_ms.max(MIN_TICK_MS)));
        let now = Instant::now();
        let dt = now.duration_since(last_tick).as_secs_f64().min(0.05);
        last_tick = now;
        if now.duration_since(config_at)>=Duration::from_millis(cfg.config_poll_ms.max(1)) {
            config_at=now;
            match load_cfg(){Ok(next)=>cfg=next,Err(e)=>eprintln!("PiP config rejected; retaining validated policy: {e}")}
        }
        if !cfg.enabled {tracks.clear();continue;}
        // An unanswered room query (the compositor busy in a Hypr3D transition) skips the tick but keeps every track:
        // dropping them lost each PiP's kept slot and natural size, so the next tick re-packed from transient geometry.
        let room=match room_active(&ipc){Ok(v)=>v,Err(e)=>{trace(&format!("room status unavailable: {e}"));for t in tracks.values_mut() {t.anim=None;}continue;}};
        if room != was_room {trace(&format!("room active={room}"));was_room=room;}
        let (Some(clients), Some(mons)) = (ipc.json("clients"), ipc.json("monitors")) else { continue };
        // Adoption also occurs while the room is active. Only flat-desktop gravity is suspended there.
        for c in clients.as_array().into_iter().flatten() {
            if c["mapped"]==true&&c["hidden"]==false&&c["fullscreen"].as_i64()==Some(0)
                &&!window::has_tag(c,window::MANAGED)&&!window::has_tag(c,window::OPT_OUT)
                &&cfg.matchers.iter().any(|m|m.matches(c,&clients)) {
                if let Err(e)=apply(&ipc,&cfg,c,"on"){eprintln!("PiP adoption: {e}");}
            }
        }
        if room {
            // Hypr3D owns placement (Theatre included). Keep each PiP's last flat slot, drop animations; re-plan on exit.
            for t in tracks.values_mut() {t.anim=None;t.settled=true;t.from_room=true;}
            continue;
        }
        if cfg.keep_above {if let Err(e)=keep_above(&ipc,&clients,&mut unpinned,now,Duration::from_millis(cfg.settle_ms as u64)){eprintln!("PiP stacking: {e}");}}
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
        // candidate PiPs, grouped by monitor id; `frozen` marks a maximized/fullscreen PiP whose pre-fullscreen slot
        // stays reserved (planned around, never moved) so its neighbours hold still and it restores into it.
        let mut by_mon: HashMap<i64, Vec<(String, Rect, bool)>> = HashMap::new();
        for c in clients.as_array().into_iter().flatten() {
            let s = |k: &str| c.get(k).and_then(Value::as_str).unwrap_or("");
            let b = |k: &str| c.get(k).and_then(|v| v.as_bool()).unwrap_or(false);
            let ws = c.get("workspace").and_then(|w| w.get("name")).and_then(|n| n.as_str()).unwrap_or("");
            if !b("mapped") || b("hidden") || ws.starts_with("special") {
                continue;
            }
            if window::has_tag(c,window::OPT_OUT) || !window::has_tag(c,window::MANAGED) {continue;}
            if c.get("fullscreen").and_then(|f| f.as_i64()).unwrap_or(0) != 0 {
                if let Some(t) = tracks.get(s("address")) {
                    let mid = monitor_at(&mons, t.last.cx(), t.last.cy()).unwrap_or_else(|| c.get("monitor").and_then(|m| m.as_i64()).unwrap_or(0));
                    by_mon.entry(mid).or_default().push((s("address").to_string(), t.last, true));
                }
                continue;
            }
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
            by_mon.entry(mid).or_default().push((s("address").to_string(), rect, false));
        }
        let live: Vec<String> = by_mon.values().flatten().map(|(a, _, _)| a.clone()).collect();
        let before = tracks.len();
        let kept: Vec<&String> = tracks.keys().filter(|a| !live.contains(a) && keep_track(a, &live, &clients)).collect();
        if !kept.is_empty() {trace(&format!("kept {} unplanned PiP track(s) {kept:?}", kept.len()));}
        tracks.retain(|a, _| keep_track(a, &live, &clients));
        if tracks.len() != before {trace(&format!("dropped {} track(s) of closed or unmanaged windows", before - tracks.len()));}
        latch.retain(&mons.as_array().into_iter().flatten().filter_map(|m| m.get("id").and_then(|i| i.as_i64())).collect::<Vec<_>>());
        for (mid, pips) in by_mon {
            let Some(m) = mons.as_array().and_then(|a| a.iter().find(|m| m.get("id").and_then(|i| i.as_i64()) == Some(mid))) else { continue };
            let sc = m.get("scale").and_then(|s| s.as_f64()).unwrap_or(1.0).max(0.1);
            let res = m.get("reserved").cloned().unwrap_or_default();
            let (mx, my) = (m.get("x").and_then(|v| v.as_f64()).unwrap_or(0.0), m.get("y").and_then(|v| v.as_f64()).unwrap_or(0.0));
            let (mw, mh) = (m.get("width").and_then(|v| v.as_f64()).unwrap_or(0.0) / sc, m.get("height").and_then(|v| v.as_f64()).unwrap_or(0.0) / sc);
            let fresh = matrix::Bounds {
                output: Rect::new(mx, my, mw, mh),
                area: Rect::new(mx + num(&res, 0), my + num(&res, 1), mw - num(&res, 0) - num(&res, 2), mh - num(&res, 1) - num(&res, 3)),
                huds: huds.rects().into_iter().filter(|r| r.cx() >= mx && r.cx() < mx + mw).collect(),
            };
            // Any maximized/fullscreen window on this output's visible workspace: the HUDs auto-hide and the reserved
            // area collapses, so PiPs keep the last ordinary bounds instead of jumping into the freed space and back.
            let visible_ws = [m.get("activeWorkspace"), m.get("specialWorkspace")].into_iter().flatten().filter_map(|w| w.get("id").and_then(|i| i.as_i64())).filter(|id| *id != 0).collect::<Vec<_>>();
            let fullscreen = clients.as_array().into_iter().flatten().any(|c| c["mapped"] == true && c["hidden"] == false && c["fullscreen"].as_i64().unwrap_or(0) != 0
                && c["workspace"]["id"].as_i64().is_some_and(|id| visible_ws.contains(&id)));
            let bounds = latch.bounds(mid, fresh, fullscreen, now, cfg.fullscreen_release);
            let (mon, mon_huds) = (bounds.area, bounds.huds);
            // Current positions with natural sizes (a PiP seen for the first time: its current size).
            let rects: Vec<Rect> = pips.iter().map(|(a, r, frozen)| match tracks.get(a) { Some(t) if !*frozen => t.plan_input(*r), Some(t) => Rect::new(t.last.x, t.last.y, t.natural.0, t.natural.1), None => *r }).collect();
            let targets = plan(&rects, mon, &mon_huds, &rail_params(&cfg,mon));
            for ((addr, cur, frozen), tgt) in pips.iter().zip(targets) {
                if *frozen {continue;}
                let t = tracks.entry(addr.clone()).or_insert_with(|| Track::new(*cur, now));
                let close = |a: Rect, b: Rect| (a.x - b.x).abs() < gravity::SETTLE_PX && (a.y - b.y).abs() < gravity::SETTLE_PX && (a.w - b.w).abs() < gravity::SETTLE_PX && (a.h - b.h).abs() < gravity::SETTLE_PX;
                let natural = t.natural;
                t.observe(*cur, now, Duration::from_millis(cfg.settle_ms as u64));
                if t.natural != natural {trace(&format!("  ^ {addr} fullscreen={} anim={}", clients.as_array().into_iter().flatten().find(|c| c["address"].as_str() == Some(addr)).map_or(-1, |c| c["fullscreen"].as_i64().unwrap_or(-1)), t.anim.is_some()));}
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
                    t.dispatched(r);
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
mod track_tests {
 use super::*;
 const SETTLE: Duration = Duration::from_millis(350);
 #[test] fn returning_from_the_room_restores_the_kept_slot_and_natural_size_at_once() {
  let t0 = Instant::now();
  let slot = Rect::new(1172.0, 140.0, 212.0, 100.0);
  // Settled in a squeezed slot (our own plan); the natural size is what the operator chose.
  let mut t = Track::new(Rect::new(1172.0, 140.0, 272.0, 128.0), t0);
  t.last = slot;
  t.settled = true;
  t.from_room = true; // the room ran
  let room_left = Rect::new(1153.0, 571.0, 231.0, 109.0); // moved and re-rastered by Hypr3D
  assert_eq!(t.plan_input(room_left), Rect::new(1172.0, 140.0, 272.0, 128.0), "plans from the kept slot and the natural size");
  let now = t0 + Duration::from_secs(5);
  t.observe(room_left, now, SETTLE);
  assert_eq!(t.natural, (272.0, 128.0), "the room's raster size is not an operator resize");
  assert!(!t.settled && now.duration_since(t.stable_since) >= SETTLE, "restored at once, no settle wait");
  assert!(!t.from_room);
  assert_eq!(t.plan_input(room_left), Rect::new(1153.0, 571.0, 272.0, 128.0), "afterwards ordinary planning from the current position");
 }
 #[test] fn negative_control_an_operator_resize_updates_the_natural_size_and_waits_for_the_release() {
  let t0 = Instant::now();
  let mut t = Track::new(Rect::new(8.0, 140.0, 272.0, 128.0), t0);
  t.settled = true;
  let now = t0 + Duration::from_secs(1);
  t.observe(Rect::new(8.0, 140.0, 400.0, 188.0), now, SETTLE);
  assert_eq!(t.natural, (400.0, 188.0));
  assert!(!t.settled && t.stable_since == now, "waits settle_ms after the operator lets go");
  // A pure move keeps the natural size.
  t.observe(Rect::new(300.0, 300.0, 400.0, 188.0), now + Duration::from_secs(1), SETTLE);
  assert_eq!(t.natural, (400.0, 188.0));
 }
 #[test] fn a_transiently_unplanned_pip_keeps_its_track_until_it_closes_or_leaves_pip() {
  let managed = serde_json::json!([{"address":"0x1","tags":[window::MANAGED],"hidden":true},{"address":"0x2","tags":[]}]);
  assert!(keep_track("0x1", &[], &managed), "hidden / special workspace / room transition: keep");
  assert!(keep_track("0x3", &["0x3".to_string()], &managed), "planned this tick");
  assert!(!keep_track("0x2", &[], &managed), "left PiP");
  assert!(!keep_track("0x9", &[], &managed), "closed");
  let opted = serde_json::json!([{"address":"0x1","tags":[window::MANAGED, window::OPT_OUT]}]);
  assert!(!keep_track("0x1", &[], &opted), "opted out");
 }
 #[test] fn a_lagging_compositor_showing_an_older_frame_of_ours_is_not_an_operator_resize() {
  let t0 = Instant::now();
  let mut t = Track::new(Rect::new(8.0, 140.0, 272.0, 128.0), t0);
  let frames = [Rect::new(8.0, 150.0, 262.0, 123.0), Rect::new(8.0, 160.0, 250.0, 118.0), Rect::new(8.0, 168.0, 240.0, 113.0)];
  for f in frames { t.dispatched(f); }
  t.anim = Some(([8.0, 168.0, 240.0, 113.0], [0.0; 4], Rect::new(8.0, 180.0, 212.0, 100.0)));
  t.observe(frames[0], t0 + Duration::from_millis(48), SETTLE); // the compositor is two frames behind
  assert_eq!(t.natural, (272.0, 128.0), "an in-flight frame of ours never becomes the natural size");
  assert!(t.anim.is_some(), "the animation continues");
  // negative control: the single-frame memory the daemon had before misreads the same lag as an operator resize
  let mut old = Track::new(Rect::new(8.0, 140.0, 272.0, 128.0), t0);
  old.dispatched(frames[2]);
  old.expected.drain(..old.expected.len() - 1);
  old.observe(frames[0], t0 + Duration::from_millis(48), SETTLE);
  assert_eq!(old.natural, (262.0, 123.0), "one remembered frame: the lagging frame is taken for an operator resize");
 }
 #[test] fn a_size_that_lags_the_position_is_still_ours_but_a_foreign_size_is_not() {
  let t0 = Instant::now();
  let mut t = Track::new(Rect::new(8.0, 140.0, 272.0, 128.0), t0);
  let frames = [Rect::new(8.0, 150.0, 262.0, 123.0), Rect::new(8.0, 160.0, 250.0, 118.0), Rect::new(8.0, 168.0, 240.0, 113.0)];
  for f in frames { t.dispatched(f); }
  // newest position, the client still at the oldest size
  t.observe(Rect::new(8.0, 168.0, 262.0, 123.0), t0 + Duration::from_millis(48), SETTLE);
  assert_eq!(t.natural, (272.0, 128.0));
  // whole-frame matching (the previous rule) misreads the same observation
  assert!(!t.expected.iter().any(|e| (e.x - 8.0).abs() < 3.0 && (e.y - 168.0).abs() < 3.0 && (e.w - 262.0).abs() < 3.0), "no single frame matches");
  // negative control: a size we never dispatched is the operator's
  t.observe(Rect::new(8.0, 168.0, 400.0, 188.0), t0 + Duration::from_millis(64), SETTLE);
  assert_eq!(t.natural, (400.0, 188.0));
 }
 #[test] fn our_own_animation_frames_never_change_the_natural_size() {
  let t0 = Instant::now();
  let mut t = Track::new(Rect::new(8.0, 140.0, 272.0, 128.0), t0);
  let frame = Rect::new(8.0, 160.0, 212.0, 100.0);
  t.dispatched(frame);
  t.settled = true;
  t.observe(frame, t0 + Duration::from_millis(16), SETTLE);
  assert_eq!(t.natural, (272.0, 128.0));
  assert!(t.settled);
 }
}

#[cfg(test)]
mod pacing_tests {
 use super::*;
 /// An event storm (10 000 events in ~250 ms) never ticks faster than the floor; the unpaced loop ticked per event.
 #[test] fn an_event_storm_is_coalesced_to_at_most_one_tick_per_floor() {
  let (tx,rx)=mpsc::sync_channel::<()>(1);
  let storm=std::thread::spawn(move||{let end=Instant::now()+Duration::from_millis(250);let mut n=0u32;while Instant::now()<end {let _=tx.try_send(());n+=1;if n%40==0 {std::thread::sleep(Duration::from_micros(1000));}}n});
  let floor=Duration::from_millis(16);
  let (start,mut last,mut ticks)=(Instant::now(),Instant::now(),0u32);
  while start.elapsed()<Duration::from_millis(250) {wait_tick(&rx,Duration::from_millis(120),last,floor);last=Instant::now();ticks+=1;}
  let sent=storm.join().unwrap();
  assert!(sent>1000,"storm too small: {sent}");
  assert!(ticks<=250/16+2,"{ticks} ticks for {sent} events");
  // negative control: without the floor the same receiver ticks on (nearly) every event
  let (tx,rx)=mpsc::sync_channel::<()>(1);
  let storm=std::thread::spawn(move||{let end=Instant::now()+Duration::from_millis(100);while Instant::now()<end {let _=tx.try_send(());std::thread::sleep(Duration::from_micros(200));}});
  let (start,mut last,mut unpaced)=(Instant::now(),Instant::now(),0u32);
  while start.elapsed()<Duration::from_millis(100) {wait_tick(&rx,Duration::from_millis(120),last,Duration::ZERO);last=Instant::now();unpaced+=1;}
  storm.join().unwrap();
  assert!(unpaced>3*(100/16+2),"unpaced loop should tick per event: {unpaced}");
 }
 #[test] fn map_and_fullscreen_wake_but_per_frame_title_updates_do_not() {
  for e in ["openwindow","closewindow","fullscreen","pin","changefloatingmode"] {assert!(WAKE_EVENTS.contains(&e),"{e}");}
  for e in ["windowtitle","windowtitlev2","mousemove"] {assert!(!WAKE_EVENTS.contains(&e),"{e} must not wake the loop");}
 }
 #[test] fn without_events_the_poll_timeout_still_ticks() {
  let (_tx,rx)=mpsc::sync_channel::<()>(1);let t=Instant::now();
  wait_tick(&rx,Duration::from_millis(30),Instant::now(),Duration::from_millis(16));
  let e=t.elapsed();assert!(e>=Duration::from_millis(29)&&e<Duration::from_millis(200),"{e:?}");
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
 /// The shipped SSOT rules: Discord (native/Flatpak/PTB/Canary/Vesktop) call, stream and video popouts, Spotify's
 /// mini-player, browser PiP; and the windows that must never be captured.
 #[test] fn shipped_rules_adopt_spotify_discord_and_browser_pip_only() {
  let path=concat!(env!("CARGO_MANIFEST_DIR"),"/../../../usr/share/mios/mios.toml");
  let doc:toml::Value=toml::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
  let ms=matchers_from(&doc["desktop"]["pip_gravity"]).unwrap();
  let all=serde_json::json!([{"address":"0x10","pid":500,"class":"Spotify","title":"Spotify Premium","initialTitle":"Spotify"}]);
  let hit=|w:serde_json::Value|ms.iter().any(|m|m.matches(&w,&all));
  for (class,initial) in [("discord","Discord Popout"),("com.discordapp.Discord","Discord Popout"),("discordcanary","Discord Popout"),("vesktop","Discord Popout")] {
   assert!(hit(serde_json::json!({"address":"0x1","pid":1,"class":class,"title":"Voice Connected","initialTitle":initial})),"{class} popout");
  }
  assert!(hit(serde_json::json!({"address":"0x2","pid":500,"class":"chromium-browser","title":"Any track • Any artist","initialTitle":"Spotify"})),"Spotify mini-player");
  assert!(hit(serde_json::json!({"address":"0x3","pid":2,"class":"firefox","title":"Picture-in-Picture","initialTitle":"Picture-in-Picture"})));
  assert!(hit(serde_json::json!({"address":"0x4","pid":3,"class":"chromium","title":"Picture in picture","initialTitle":"Picture in picture"})));
  // negative controls: the Discord and Spotify main windows, an unrelated Chromium window, a channel named like a popout
  assert!(!hit(serde_json::json!({"address":"0x5","pid":1,"class":"discord","title":"Friends - Discord","initialTitle":"Discord"})));
  assert!(!hit(serde_json::json!({"address":"0x6","pid":1,"class":"discord","title":"Discord Popout","initialTitle":"#general | Server - Discord"})));
  assert!(!hit(serde_json::json!({"address":"0x10","pid":500,"class":"Spotify","title":"Spotify Premium","initialTitle":"Spotify"})));
  assert!(!hit(serde_json::json!({"address":"0x7","pid":900,"class":"chromium-browser","title":"Any track • Any artist","initialTitle":"New Tab"})));
  assert!(!hit(serde_json::json!({"address":"0x8","pid":4,"class":"Alacritty","title":"Discord Popout","initialTitle":"Discord Popout"})));
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
