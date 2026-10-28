// AI-hint: mios-pip-gravity daemon -- gives floating Picture-in-Picture windows soft magnetic flank rails (left/right columns stacked top-to-bottom), HUD (topbar/dock) magnetism without hiding them, aspect-aware sizing and a springy settle. Polls the Hyprland IPC; idles while Hypr3D is active (PiP are freely resizable there). Config comes only from the layered SSOT table [desktop.pip_gravity].
// AI-related: usr/share/mios/mios.toml [desktop.pip_gravity], usr/lib/systemd/user/mios-pip-gravity.service, tools/native/mios-pip-gravity/src/gravity.rs, usr/share/mios/hyprland/hyprland.lua
mod gravity;

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
    matchers: Vec<(Option<Regex>, Option<Regex>)>,
    hud_ns: Vec<String>,
    stiffness: f64,
    damping: f64,
    settle_ms: u128,
    idle_ms: u64,
    active_ms: u64,
    status_file: String,
}

fn merge(dst: &mut toml::Value, src: toml::Value) {
    match (dst, src) {
        (toml::Value::Table(d), toml::Value::Table(s)) => {
            for (k, v) in s {
                match d.get_mut(&k) {
                    Some(e) => merge(e, v),
                    None => {
                        d.insert(k, v);
                    }
                }
            }
        }
        (d, s) => *d = s,
    }
}

fn load_cfg() -> Result<Cfg, String> {
    let home = std::env::var("HOME").unwrap_or_default();
    let layers = [
        "/usr/share/mios/mios.toml".to_string(),
        "/etc/mios/mios.toml".to_string(),
        format!("{home}/.config/mios/mios.toml"),
    ];
    let mut acc = toml::Value::Table(Default::default());
    for p in layers {
        if let Ok(s) = std::fs::read_to_string(&p) {
            if let Ok(v) = s.parse::<toml::Value>() {
                if let Some(t) = v.get("desktop").and_then(|d| d.get("pip_gravity")) {
                    merge(&mut acc, t.clone());
                }
            }
        }
    }
    let f = |k: &str| -> Result<f64, String> {
        acc.get(k).and_then(|v| v.as_float().or(v.as_integer().map(|i| i as f64))).ok_or(format!("[desktop.pip_gravity].{k} missing"))
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
        matchers.push((rx("class")?, rx("title")?));
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
        },
        matchers,
        hud_ns: acc.get("hud_namespaces").and_then(|v| v.as_array()).map(|a| a.iter().filter_map(|v| v.as_str().map(String::from)).collect()).unwrap_or_default(),
        stiffness: f("spring_stiffness")?,
        damping: f("spring_damping")?,
        settle_ms: f("settle_ms")? as u128,
        idle_ms: f("idle_poll_ms")? as u64,
        active_ms: f("active_poll_ms")? as u64,
        status_file: acc.get("hypr3d_status_file").and_then(|v| v.as_str()).unwrap_or("").to_string(),
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

struct Track {
    last: Rect,
    expected: Option<Rect>,
    stable_since: Instant,
    settled: bool,
    anim: Option<([f64; 4], [f64; 4], Rect)>,
}

fn main() {
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
    let cfg = load_cfg().unwrap_or_else(|e| {
        eprintln!("mios-pip-gravity: {e}");
        std::process::exit(2)
    });
    let ipc = Ipc::new().unwrap_or_else(|e| {
        eprintln!("mios-pip-gravity: {e}");
        std::process::exit(2)
    });
    let mut tracks: HashMap<String, Track> = HashMap::new();
    let mut huds: Vec<(String, Rect)> = Vec::new();
    let mut huds_at = Instant::now() - Duration::from_secs(60);
    let mut last_tick = Instant::now();
    loop {
        let busy = tracks.values().any(|t| t.anim.is_some() || !t.settled);
        std::thread::sleep(Duration::from_millis(if busy { cfg.active_ms } else { cfg.idle_ms }));
        let now = Instant::now();
        let dt = now.duration_since(last_tick).as_secs_f64().min(0.05);
        last_tick = now;
        if !cfg.enabled || hypr3d_active(&cfg.status_file) {
            tracks.clear();
            continue;
        }
        let (Some(clients), Some(mons)) = (ipc.json("clients"), ipc.json("monitors")) else { continue };
        if now.duration_since(huds_at) > Duration::from_secs(2) {
            huds.clear();
            if let Some(Value::Object(m)) = ipc.json("layers") {
                for (_, mon) in m {
                    for (_, lvl) in mon.get("levels").and_then(|l| l.as_object()).into_iter().flatten() {
                        for l in lvl.as_array().into_iter().flatten() {
                            let ns = l.get("namespace").and_then(|n| n.as_str()).unwrap_or("");
                            if cfg.hud_ns.iter().any(|h| h == ns) {
                                let g = |k: &str| l.get(k).and_then(|v| v.as_f64()).unwrap_or(0.0);
                                huds.push((ns.to_string(), Rect::new(g("x"), g("y"), g("w"), g("h"))));
                            }
                        }
                    }
                }
            }
            huds_at = now;
        }
        // candidate PiPs, grouped by monitor id
        let mut by_mon: HashMap<i64, Vec<(String, Rect)>> = HashMap::new();
        for c in clients.as_array().into_iter().flatten() {
            let s = |k: &str| c.get(k).and_then(|v| v.as_str()).unwrap_or("");
            let b = |k: &str| c.get(k).and_then(|v| v.as_bool()).unwrap_or(false);
            let ws = c.get("workspace").and_then(|w| w.get("name")).and_then(|n| n.as_str()).unwrap_or("");
            if !b("mapped") || b("hidden") || !b("floating") || ws.starts_with("special") || c.get("fullscreen").and_then(|f| f.as_i64()).unwrap_or(0) != 0 {
                continue;
            }
            let hit = cfg.matchers.iter().any(|(cl, ti)| cl.as_ref().map_or(true, |r| r.is_match(s("class"))) && ti.as_ref().map_or(true, |r| r.is_match(s("title"))));
            if !hit {
                continue;
            }
            let at = c.get("at").cloned().unwrap_or_default();
            let sz = c.get("size").cloned().unwrap_or_default();
            let rect = Rect::new(num(&at, 0), num(&at, 1), num(&sz, 0), num(&sz, 1));
            by_mon.entry(c.get("monitor").and_then(|m| m.as_i64()).unwrap_or(0)).or_default().push((s("address").to_string(), rect));
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
            let mon_huds: Vec<Rect> = huds.iter().map(|(_, r)| *r).filter(|r| r.cx() >= mx && r.cx() < mx + mw).collect();
            let rects: Vec<Rect> = pips.iter().map(|(_, r)| *r).collect();
            let targets = plan(&rects, mon, &mon_huds, &cfg.params);
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
                    let w = format!("address:{addr}");
                    let cmd = format!(
                        "eval hl.dispatch(hl.dsp.window.resize({{x={},y={},exact=true,window='{w}'}}));hl.dispatch(hl.dsp.window.move({{x={},y={},window='{w}'}}))",
                        r.w, r.h, r.x, r.y
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
