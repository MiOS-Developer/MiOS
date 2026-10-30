// AI-hint: `mios-gen dock` -- the backend of the MiOS dock HUD (usr/share/mios/quickshell/Sidebar.qml, DockExpanded.qml).
// The dock is the localhost/sessions half of the HUD: apps, windows, sessions and VMs and moving windows between them.
//   targets  Where a window can go, each with the capability it really has (JSON): every output of this compositor
//            (local and the RDP remote display: a live move, the window and its state move as they are), the nested
//            sessions in the registry and this session's parent (relaunch: the app restarts there, carrying only what
//            it saves itself), the SSOT Looking Glass VMs (their desktop streams into a local window; nothing moves
//            into a VM) and waypipe remotes (only when waypipe is installed and [desktop.dock.targets].waypipe_hosts
//            lists hosts). A live Wayland client handoff between compositors is reported unsupported with the reason.
//   move     Performs one move and verifies it (the window is on the target output, or a new client of the app mapped
//            in the target compositor); anything else fails with the reason -- a move is never reported unverified.
//   osk      Runs the Phosh on-screen keyboard (stevia) nested in the expanded dock: a small Wayland proxy between the
//            keyboard and the compositor rewrites its layer surface (full-width, TOP, exclusive) to the dock's own rect
//            (width x height, bottom margin over the dock bar, centred like the dock, the dock's layer and namespace so
//            the dock's glass and yield rules apply), so the keyboard sits INSIDE the expanded panel. The keyboard is
//            the dock's child: SIGTERM (the dock collapsing) tears down both, and the keyboard dies with this process.
// AI-related: usr/share/mios/mios.toml [desktop.dock] [desktop.dock.osk] [desktop.dock.targets] [remote.rdp] [vm.looking_glass],
// usr/share/mios/quickshell/DockExpanded.qml, usr/share/mios/quickshell/DockConfig.qml, tools/native/mios-gen/src/nested_devices.rs,
// usr/share/doc/mios/guides/hud.md, tests/test-dock-hud.py
use serde_json::{json, Value as Json};
use std::collections::{HashMap, HashSet};
use std::env;
use std::fs;
use std::io::{Read, Write};
use std::os::fd::{AsRawFd, RawFd};
use std::os::unix::net::{UnixListener, UnixStream};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

type Toml = toml::Value;

fn ssot(root: &Path) -> Result<Toml, String> {
    mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())
}
fn get<'a>(t: &'a Toml, path: &str) -> Option<&'a Toml> {
    path.split('.').try_fold(t, |cur, k| cur.get(k))
}
fn s<'a>(t: &'a Toml, path: &str, d: &'a str) -> &'a str {
    get(t, path).and_then(Toml::as_str).unwrap_or(d)
}
fn n(t: &Toml, path: &str, d: i64) -> i64 {
    get(t, path).and_then(Toml::as_integer).unwrap_or(d)
}
fn strs(t: &Toml, path: &str) -> Vec<String> {
    get(t, path).and_then(Toml::as_array).map(|a| a.iter().filter_map(|v| v.as_str().map(str::to_owned)).collect()).unwrap_or_default()
}

// ---- compositor IPC ------------------------------------------------------------------------------------------------
#[derive(Clone, Debug)]
pub struct Instance {
    pub runtime: PathBuf,
    pub signature: String,
    pub wayland: PathBuf,
}
impl Instance {
    fn current() -> Result<Self, String> {
        let runtime = PathBuf::from(env::var_os("XDG_RUNTIME_DIR").ok_or("XDG_RUNTIME_DIR missing")?);
        let signature = env::var("HYPRLAND_INSTANCE_SIGNATURE").map_err(|_| "HYPRLAND_INSTANCE_SIGNATURE missing")?;
        let wl = env::var("WAYLAND_DISPLAY").unwrap_or_else(|_| "wayland-1".into());
        let wayland = if Path::new(&wl).is_absolute() { PathBuf::from(wl) } else { runtime.join(wl) };
        Ok(Instance { runtime, signature, wayland })
    }
    fn ipc(&self, cmd: &str) -> Result<String, String> {
        let mut st = UnixStream::connect(self.runtime.join("hypr").join(&self.signature).join(".socket.sock")).map_err(|e| format!("{}: {e}", self.signature))?;
        st.set_read_timeout(Some(Duration::from_secs(2))).map_err(|e| e.to_string())?;
        st.write_all(cmd.as_bytes()).map_err(|e| e.to_string())?;
        let mut out = String::new();
        st.read_to_string(&mut out).map_err(|e| e.to_string())?;
        Ok(out)
    }
    fn json(&self, what: &str) -> Result<Json, String> {
        serde_json::from_str(&self.ipc(&format!("j/{what}"))?).map_err(|e| format!("{what}: {e}"))
    }
    fn eval(&self, code: &str) -> Result<(), String> {
        let r = self.ipc(&format!("/eval {code}"))?;
        if r.trim() == "ok" { Ok(()) } else { Err(r.trim().to_owned()) }
    }
}
fn lua_str(v: &str) -> String {
    serde_json::to_string(v).unwrap()
}

// ---- targets -------------------------------------------------------------------------------------------------------
/// The registry of nested sessions lives in the runtime of the compositor that launched them: ours, or -- inside a
/// nested session -- the parent's (session.json in our runtime names it).
fn registry_root(me: &Instance) -> (PathBuf, Option<Json>) {
    let desc = fs::read(me.runtime.join("session.json")).ok().and_then(|b| serde_json::from_slice::<Json>(&b).ok());
    match desc.as_ref().and_then(|d| d["parent_runtime"].as_str()) {
        Some(p) => (PathBuf::from(p), desc.clone()),
        None => (me.runtime.clone(), None),
    }
}

fn which(bin: &str) -> bool {
    env::var_os("PATH").map(|p| env::split_paths(&p).any(|d| d.join(bin).is_file())).unwrap_or(false)
}

pub fn targets_json(data: &Toml, me: &Instance, monitors: &Json, sessions: &[Json], parent: Option<&Json>) -> Json {
    let rdp = s(data, "remote.rdp.virtual_output", "HEADLESS-MIOS-RDP");
    let local_label = s(data, "desktop.dock.targets.local_label", "This desktop");
    let mut out = Vec::new();
    for m in monitors.as_array().into_iter().flatten() {
        let name = m["name"].as_str().unwrap_or_default();
        if name.is_empty() {
            continue;
        }
        let remote = name.starts_with(rdp);
        out.push(json!({
            "id": format!("output:{name}"), "kind": if remote { "remote-rdp" } else { "local-output" },
            "label": if remote { format!("Remote display {name}") } else { format!("{local_label} · {name}") },
            "available": !m["disabled"].as_bool().unwrap_or(false),
            "move": {"method": "monitor-move", "live_state": true,
                     "note": if remote { "The window moves onto the remote client's display as it is (RDP streams it)." } else { "The window moves as it is." }},
        }));
    }
    let relaunch_note = "The app restarts in the target session; it carries only the state it saves itself (session restore). Single-instance apps that reuse a running process cannot move this way.";
    for sj in sessions {
        let inst = sj["instance"].as_str().unwrap_or_default();
        if inst.is_empty() || inst == me.signature {
            continue;
        }
        let seat = sj["seat"].as_str().unwrap_or_default();
        let user = sj["user"].as_str().unwrap_or_default();
        let label = if !user.is_empty() { format!("Session {user}") } else if !seat.is_empty() { format!("Session {seat}") } else { format!("Nested session {}", sj["compositor_pid"]) };
        out.push(json!({
            "id": format!("session:{inst}"), "kind": "nested", "label": label, "available": sj["state"] == "running",
            "runtime": sj["runtime"], "instance": inst, "wayland_display": sj["wayland_display"],
            "move": {"method": "relaunch", "live_state": false, "note": relaunch_note},
        }));
    }
    if let Some(p) = parent {
        if let (Some(rt), Some(inst)) = (p["parent_runtime"].as_str(), p["parent_instance"].as_str()) {
            out.push(json!({
                "id": format!("session:{inst}"), "kind": "parent", "label": "Host desktop", "available": true,
                "runtime": rt, "instance": inst, "wayland_display": Json::Null,
                "move": {"method": "relaunch", "live_state": false, "note": relaunch_note},
            }));
        }
    }
    if get(data, "vm.looking_glass.enabled").and_then(Toml::as_bool).unwrap_or(false) {
        for d in strs(data, "vm.looking_glass.domains") {
            out.push(json!({
                "id": format!("vm:{d}"), "kind": "vm", "label": format!("VM {d}"), "available": true,
                "state": "not queried",
                "move": {"method": "none", "live_state": false, "note": "Nothing moves into a VM; its desktop streams into a local window (Looking Glass)."},
                "stream": {"method": "looking-glass", "client": s(data, "vm.looking_glass.client", "/usr/bin/looking-glass-client")},
            }));
        }
    }
    let waypipe = which("waypipe");
    for h in strs(data, "desktop.dock.targets.waypipe_hosts") {
        out.push(json!({
            "id": format!("waypipe:{h}"), "kind": "waypipe", "label": format!("Remote {h}"), "available": waypipe,
            "move": {"method": if waypipe { "waypipe-relaunch" } else { "none" }, "live_state": false,
                     "note": if waypipe { "The app restarts on the remote host and displays here through waypipe." } else { "waypipe is not installed." }},
        }));
    }
    json!({
        "version": 1,
        "instance": me.signature,
        "targets": out,
        "methods": {
            "monitor-move": {"supported": true},
            "relaunch": {"supported": true},
            "waypipe": {"supported": waypipe, "reason": if waypipe { "" } else { "waypipe is not installed" }},
            "wayland-handoff": {"supported": false, "reason": "A live Wayland client cannot be moved between compositors: its surfaces, buffers and protocol objects belong to one display connection, and only toolkits that rebuild all of them on a new connection (Qt 6.6+ with QT_WAYLAND_RECONNECT, for a compositor restart) survive a switch. No reconnecting proxy for arbitrary apps is shipped; MiOS relaunches instead."},
            "vm-stream": {"supported": true},
        }
    })
}

pub fn targets(root: &Path, address: Option<&str>) -> Result<Json, String> {
    let data = ssot(root)?;
    let me = Instance::current()?;
    let monitors = me.json("monitors").unwrap_or(Json::Array(vec![]));
    let (reg_root, desc) = registry_root(&me);
    let reg = reg_root.join(s(&data, "desktop.nested.registry_dir", "mios/nested-sessions"));
    let sessions: Vec<Json> = crate::nested_devices::live_sessions(&reg, false).into_iter().map(|(_, j)| j).collect();
    let mut v = targets_json(&data, &me, &monitors, &sessions, desc.as_ref());
    // With a window: how THIS app would move to each target (migrate / stream / live), shown by the dock per chip.
    if let Some(a) = address {
        let clients = me.json("clients")?;
        let w = find_client(&clients, a).ok_or_else(|| format!("no window {a}"))?;
        let class = w["class"].as_str().unwrap_or_default();
        let p = policy_for(&data, w["pid"].as_u64().unwrap_or(0), class);
        for t in v["targets"].as_array_mut().into_iter().flatten() {
            let how = match t["move"]["method"].as_str().unwrap_or("none") {
                "monitor-move" => "live",
                "relaunch" | "waypipe-relaunch" if p.migrate => "migrate",
                "relaunch" | "waypipe-relaunch" => "stream-unavailable",
                _ => if t.get("stream").is_some() { "stream-only" } else { "none" },
            };
            t["for_window"] = json!({"how": how});
        }
        v["window"] = json!({"address": a, "class": class, "kind": p.kind, "app_id": p.app_id, "migrate": p.migrate});
    }
    Ok(v)
}

// ---- move ----------------------------------------------------------------------------------------------------------
fn proc_argv(pid: u64) -> Result<Vec<String>, String> {
    let raw = fs::read(format!("/proc/{pid}/cmdline")).map_err(|e| format!("pid {pid}: {e}"))?;
    let v: Vec<String> = raw.split(|b| *b == 0).filter(|a| !a.is_empty()).map(|a| String::from_utf8_lossy(a).into_owned()).collect();
    if v.is_empty() { Err(format!("pid {pid} has no command line")) } else { Ok(v) }
}

/// How an app can move (operator: "both, per app" -- migrate containerized/stateless apps, stream the rest):
/// a Flatpak (its state lives in ~/.var/app and its runtime is the same in every session) or a podman/toolbox
/// container app migrates by relaunch, as does any class listed in [desktop.dock.targets].stateless_apps; every other
/// app would have to be streamed.
#[derive(Debug, Clone, PartialEq)]
pub struct AppPolicy {
    pub kind: &'static str,       // flatpak | container | stateless | native
    pub app_id: String,           // the Flatpak app id (relaunch with `flatpak run`), else the window class
    pub migrate: bool,
}
pub fn classify(cgroup: &str, flatpak_info: Option<&str>, class: &str, stateless: &[String]) -> AppPolicy {
    if let Some(info) = flatpak_info {
        let id = info.lines().find_map(|l| l.strip_prefix("name=")).unwrap_or(class).trim().to_owned();
        return AppPolicy { kind: "flatpak", app_id: id, migrate: true };
    }
    if cgroup.contains("app-flatpak-") {
        return AppPolicy { kind: "flatpak", app_id: class.to_owned(), migrate: true };
    }
    if cgroup.contains("libpod-") || cgroup.contains("toolbox") {
        return AppPolicy { kind: "container", app_id: class.to_owned(), migrate: true };
    }
    if stateless.iter().any(|c| c.eq_ignore_ascii_case(class)) {
        return AppPolicy { kind: "stateless", app_id: class.to_owned(), migrate: true };
    }
    AppPolicy { kind: "native", app_id: class.to_owned(), migrate: false }
}
fn policy_for(data: &Toml, pid: u64, class: &str) -> AppPolicy {
    let cg = fs::read_to_string(format!("/proc/{pid}/cgroup")).unwrap_or_default();
    let fi = fs::read_to_string(format!("/proc/{pid}/root/.flatpak-info")).ok();
    classify(&cg, fi.as_deref(), class, &strs(data, "desktop.dock.targets.stateless_apps"))
}

fn find_client<'a>(clients: &'a Json, address: &str) -> Option<&'a Json> {
    clients.as_array()?.iter().find(|c| c["address"].as_str().is_some_and(|a| a.eq_ignore_ascii_case(address)))
}

pub fn move_window(root: &Path, target: &str, address: &str, dry_run: bool) -> Result<Json, String> {
    if !address.starts_with("0x") || !address[2..].chars().all(|c| c.is_ascii_hexdigit()) || address.len() < 3 {
        return Err("address must be a Hyprland window address (0x...)".into());
    }
    let data = ssot(root)?;
    let me = Instance::current()?;
    let all = targets(root, None)?;
    let t = all["targets"].as_array().into_iter().flatten().find(|t| t["id"] == target).cloned().ok_or_else(|| format!("unknown target {target}"))?;
    let clients = me.json("clients")?;
    let win = find_client(&clients, address).cloned().ok_or_else(|| format!("no window {address} in this session"))?;
    let method = t["move"]["method"].as_str().unwrap_or("none");
    match method {
        "monitor-move" => {
            let output = target.trim_start_matches("output:");
            let code = format!("hl.dispatch(hl.dsp.window.move({{monitor = {}, window = {}, follow = false}}))", lua_str(output), lua_str(&format!("address:{address}")));
            if dry_run {
                return Ok(json!({"target": target, "method": method, "dry_run": true, "eval": code}));
            }
            me.eval(&code)?;
            let mon_id = me.json("monitors")?.as_array().into_iter().flatten().find(|m| m["name"] == output).and_then(|m| m["id"].as_i64());
            let deadline = Instant::now() + Duration::from_millis(n(&data, "desktop.dock.targets.verify_ms", 2000) as u64);
            loop {
                let now = find_client(&me.json("clients")?, address).and_then(|c| c["monitor"].as_i64());
                if now.is_some() && now == mon_id {
                    return Ok(json!({"target": target, "method": method, "moved": true, "live_state": true, "address": address}));
                }
                if Instant::now() > deadline {
                    return Err(format!("{address} did not reach {output} (monitor {:?})", now));
                }
                thread::sleep(Duration::from_millis(50));
            }
        }
        "relaunch" => {
            let pid = win["pid"].as_u64().filter(|p| *p > 1).ok_or("window has no process")?;
            let class = win["class"].as_str().unwrap_or_default().to_owned();
            let policy = policy_for(&data, pid, &class);
            if !policy.migrate {
                return Err(format!(
                    "{class} is a native app with local state: it would be streamed, not migrated, and streaming a window into {} is not available yet (move it to an output instead)",
                    t["label"].as_str().unwrap_or(target)
                ));
            }
            let argv = if policy.kind == "flatpak" { vec!["flatpak".to_owned(), "run".to_owned(), policy.app_id.clone()] } else { proc_argv(pid)? };
            let tgt = Instance {
                runtime: PathBuf::from(t["runtime"].as_str().ok_or("target runtime missing")?),
                signature: t["instance"].as_str().ok_or("target instance missing")?.to_owned(),
                wayland: PathBuf::new(),
            };
            let inst = tgt.json("instances").ok();
            let wl = inst.as_ref().and_then(|v| v.as_array()).and_then(|a| a.iter().find(|i| i["instance"] == tgt.signature)).and_then(|i| i["wl_socket"].as_str()).map(str::to_owned)
                .or_else(|| t["wayland_display"].as_str().map(str::to_owned)).ok_or("target Wayland socket unknown")?;
            let wayland = tgt.runtime.join(&wl);
            if dry_run {
                return Ok(json!({"target": target, "method": method, "dry_run": true, "argv": argv, "wayland_display": wayland, "close_source": true}));
            }
            let before: HashSet<String> = tgt.json("clients")?.as_array().into_iter().flatten().filter_map(|c| c["address"].as_str().map(str::to_owned)).collect();
            // Close first so a single-instance app (a browser profile lock) can start again; the app saves what it saves.
            me.eval(&format!("hl.dispatch(hl.dsp.window.close({{window = {}}}))", lua_str(&format!("address:{address}"))))?;
            let close_wait = Duration::from_millis(n(&data, "desktop.dock.targets.close_timeout_ms", 5000) as u64);
            let others = clients.as_array().into_iter().flatten().filter(|c| c["pid"].as_u64() == Some(pid) && c["address"] != address).count();
            if others == 0 {
                let until = Instant::now() + close_wait;
                while Path::new(&format!("/proc/{pid}")).exists() && Instant::now() < until {
                    thread::sleep(Duration::from_millis(50));
                }
            }
            let mut cmd = Command::new(&argv[0]);
            cmd.args(&argv[1..]).env("WAYLAND_DISPLAY", &wayland).env("HYPRLAND_INSTANCE_SIGNATURE", &tgt.signature).env_remove("DISPLAY")
                .stdin(Stdio::null()).stdout(Stdio::null()).stderr(Stdio::null()).process_group(0);
            cmd.spawn().map_err(|e| format!("relaunch {}: {e}", argv[0]))?;
            let deadline = Instant::now() + Duration::from_millis(n(&data, "desktop.dock.targets.relaunch_timeout_ms", 15000) as u64);
            loop {
                let now = tgt.json("clients").unwrap_or(Json::Null);
                if let Some(c) = now.as_array().into_iter().flatten().find(|c| {
                    c["address"].as_str().is_some_and(|a| !before.contains(a)) && (class.is_empty() || c["class"].as_str() == Some(class.as_str()))
                }) {
                    return Ok(json!({"target": target, "method": method, "moved": true, "live_state": false, "address": c["address"], "class": c["class"]}));
                }
                if Instant::now() > deadline {
                    return Err(format!("{class} did not map in {} after relaunch (a single-instance app may have reused its running process)", tgt.signature));
                }
                thread::sleep(Duration::from_millis(100));
            }
        }
        "waypipe-relaunch" => Err("waypipe moves are listed but not performed by this build: start the app on the host with `waypipe ssh <host> <app>`".into()),
        _ => Err(format!("{target}: {}", t["move"]["note"].as_str().unwrap_or("this target cannot receive windows"))),
    }
}

// ---- osk: the keyboard nested in the expanded dock -----------------------------------------------------------------
/// Geometry and identity the keyboard's layer surface is rewritten to (logical px of the output).
#[derive(Clone, Debug, PartialEq)]
pub struct OskRect {
    pub width: u32,
    pub height: u32,
    pub bottom: i32,
    pub layer: u32,
    pub namespace: String,
}

const ANCHOR_BOTTOM: u32 = 2;

#[derive(Default)]
pub struct Tracker {
    registries: HashSet<u32>,
    layer_shells: HashSet<u32>,
    layer_surfaces: HashSet<u32>,
    pub rewritten: u64,
}

fn rd(b: &[u8], at: usize) -> u32 {
    u32::from_le_bytes([b[at], b[at + 1], b[at + 2], b[at + 3]])
}
fn header(obj: u32, op: u16, len: usize) -> Vec<u8> {
    let mut v = obj.to_le_bytes().to_vec();
    v.extend_from_slice(&(((len as u32) << 16) | op as u32).to_le_bytes());
    v
}
fn wl_string(sv: &str) -> Vec<u8> {
    let mut v = ((sv.len() + 1) as u32).to_le_bytes().to_vec();
    v.extend_from_slice(sv.as_bytes());
    v.push(0);
    while v.len() % 4 != 0 {
        v.push(0);
    }
    v
}
/// Reads a wl string argument at `at`; returns (text, bytes consumed).
fn read_string(b: &[u8], at: usize) -> Option<(String, usize)> {
    let len = rd(b, at) as usize;
    let padded = (len + 3) & !3;
    if at + 4 + padded > b.len() || len == 0 {
        return None;
    }
    Some((String::from_utf8_lossy(&b[at + 4..at + 4 + len - 1]).into_owned(), 4 + padded))
}
fn msg(obj: u32, op: u16, args: &[u32]) -> Vec<u8> {
    let mut v = header(obj, op, 8 + 4 * args.len());
    for a in args {
        v.extend_from_slice(&a.to_le_bytes());
    }
    v
}
/// The layer-surface requests that put the keyboard inside the dock: size, bottom anchor (centred), the edge it ignores
/// other zones from (-1: margins count from the output edge), the bottom margin over the dock bar, no keyboard focus.
fn placement(id: u32, r: &OskRect) -> Vec<u8> {
    let mut v = msg(id, 0, &[r.width, r.height]);
    v.extend(msg(id, 1, &[ANCHOR_BOTTOM]));
    v.extend(msg(id, 2, &[(-1i32) as u32]));
    v.extend(msg(id, 3, &[0, 0, r.bottom as u32, 0]));
    v
}

/// Rewrites one client->compositor message (wl wire format, whole message in `m`). Returns the bytes to send.
pub fn rewrite_request(t: &mut Tracker, r: &OskRect, m: &[u8]) -> Vec<u8> {
    let obj = rd(m, 0);
    let op = (rd(m, 4) & 0xffff) as u16;
    if obj == 1 && op == 1 && m.len() >= 12 {
        t.registries.insert(rd(m, 8)); // wl_display.get_registry(new_id)
    } else if t.registries.contains(&obj) && op == 0 {
        // wl_registry.bind(name, interface: string, version, new_id)
        if let Some((iface, used)) = read_string(m, 12) {
            if iface == "zwlr_layer_shell_v1" && m.len() >= 12 + used + 8 {
                t.layer_shells.insert(rd(m, 12 + used + 4));
            }
        }
    } else if t.layer_shells.contains(&obj) && op == 0 && m.len() >= 24 {
        // get_layer_surface(new_id, surface, output, layer, namespace): the dock's layer and namespace, then placement
        let id = rd(m, 8);
        t.layer_surfaces.insert(id);
        t.rewritten += 1;
        let mut out = header(obj, 0, 0);
        for a in [id, rd(m, 12), rd(m, 16), r.layer] {
            out.extend_from_slice(&a.to_le_bytes());
        }
        out.extend(wl_string(&r.namespace));
        let len = out.len() as u32;
        out[4..8].copy_from_slice(&((len << 16) | 0u32).to_le_bytes());
        out.extend(placement(id, r));
        return out;
    } else if t.layer_surfaces.contains(&obj) {
        t.rewritten += 1;
        return match op {
            0 => msg(obj, 0, &[r.width, r.height]),
            1 => msg(obj, 1, &[ANCHOR_BOTTOM]),
            2 => msg(obj, 2, &[(-1i32) as u32]),
            3 => msg(obj, 3, &[0, 0, r.bottom as u32, 0]),
            8 => msg(obj, 8, &[r.layer]),
            _ => {
                t.rewritten -= 1;
                m.to_vec()
            }
        };
    }
    m.to_vec()
}
/// Watches compositor->client events for wl_display.delete_id, so a recycled object id is never rewritten.
pub fn observe_event(t: &mut Tracker, m: &[u8]) {
    if rd(m, 0) == 1 && (rd(m, 4) & 0xffff) == 1 && m.len() >= 12 {
        let id = rd(m, 8);
        t.registries.remove(&id);
        t.layer_shells.remove(&id);
        t.layer_surfaces.remove(&id);
    }
}

fn recv(fd: RawFd, buf: &mut [u8], fds: &mut Vec<RawFd>) -> std::io::Result<usize> {
    let mut cmsg = [0u8; 512];
    let mut iov = libc::iovec { iov_base: buf.as_mut_ptr().cast(), iov_len: buf.len() };
    let mut mh: libc::msghdr = unsafe { std::mem::zeroed() };
    mh.msg_iov = &mut iov;
    mh.msg_iovlen = 1;
    mh.msg_control = cmsg.as_mut_ptr().cast();
    mh.msg_controllen = cmsg.len() as _;
    let r = unsafe { libc::recvmsg(fd, &mut mh, libc::MSG_CMSG_CLOEXEC) };
    if r < 0 {
        return Err(std::io::Error::last_os_error());
    }
    unsafe {
        let mut c = libc::CMSG_FIRSTHDR(&mh);
        while !c.is_null() {
            if (*c).cmsg_level == libc::SOL_SOCKET && (*c).cmsg_type == libc::SCM_RIGHTS {
                let data = libc::CMSG_DATA(c) as *const RawFd;
                let count = ((*c).cmsg_len as usize - libc::CMSG_LEN(0) as usize) / std::mem::size_of::<RawFd>();
                for i in 0..count {
                    fds.push(*data.add(i));
                }
            }
            c = libc::CMSG_NXTHDR(&mh, c);
        }
    }
    Ok(r as usize)
}
fn send(fd: RawFd, data: &[u8], fds: &[RawFd]) -> std::io::Result<()> {
    let mut off = 0;
    let mut first = true;
    while off < data.len() {
        let mut iov = libc::iovec { iov_base: data[off..].as_ptr() as *mut _, iov_len: data.len() - off };
        let mut mh: libc::msghdr = unsafe { std::mem::zeroed() };
        mh.msg_iov = &mut iov;
        mh.msg_iovlen = 1;
        let space = unsafe { libc::CMSG_SPACE((fds.len() * std::mem::size_of::<RawFd>()) as u32) } as usize;
        let mut cmsg = vec![0u8; space.max(1)];
        if first && !fds.is_empty() {
            mh.msg_control = cmsg.as_mut_ptr().cast();
            mh.msg_controllen = space as _;
            unsafe {
                let c = libc::CMSG_FIRSTHDR(&mh);
                (*c).cmsg_level = libc::SOL_SOCKET;
                (*c).cmsg_type = libc::SCM_RIGHTS;
                (*c).cmsg_len = libc::CMSG_LEN((fds.len() * std::mem::size_of::<RawFd>()) as u32) as _;
                std::ptr::copy_nonoverlapping(fds.as_ptr(), libc::CMSG_DATA(c) as *mut RawFd, fds.len());
            }
        }
        let r = unsafe { libc::sendmsg(fd, &mh, libc::MSG_NOSIGNAL) };
        if r < 0 {
            let e = std::io::Error::last_os_error();
            if e.kind() == std::io::ErrorKind::Interrupted {
                continue;
            }
            return Err(e);
        }
        first = false;
        off += r as usize;
    }
    Ok(())
}

/// One direction of a proxied connection: whole messages are parsed (and requests rewritten), fds ride with the next
/// non-empty chunk (libwayland queues fds apart from the bytes, so order is all that matters).
fn pump(from: UnixStream, to: UnixStream, tracker: Arc<Mutex<Tracker>>, rect: Option<OskRect>) {
    let mut buf = vec![0u8; 65536];
    let mut pending: Vec<u8> = Vec::new();
    let mut fds: Vec<RawFd> = Vec::new();
    loop {
        let got = match recv(from.as_raw_fd(), &mut buf, &mut fds) {
            Ok(0) | Err(_) => break,
            Ok(n) => n,
        };
        pending.extend_from_slice(&buf[..got]);
        let mut out = Vec::new();
        let mut at = 0;
        while pending.len() - at >= 8 {
            let size = (rd(&pending, at + 4) >> 16) as usize;
            if size < 8 || pending.len() - at < size {
                break;
            }
            let m = &pending[at..at + size];
            let mut t = tracker.lock().unwrap();
            match &rect {
                Some(r) => out.extend(rewrite_request(&mut t, r, m)),
                None => {
                    observe_event(&mut t, m);
                    out.extend_from_slice(m);
                }
            }
            at += size;
        }
        pending.drain(..at);
        if !out.is_empty() {
            if send(to.as_raw_fd(), &out, &fds).is_err() {
                break;
            }
            for f in fds.drain(..) {
                unsafe { libc::close(f) };
            }
        }
    }
    let _ = to.shutdown(std::net::Shutdown::Both);
    for f in fds {
        unsafe { libc::close(f) };
    }
}

static STOP: AtomicBool = AtomicBool::new(false);
extern "C" fn on_term(_: libc::c_int) {
    STOP.store(true, Ordering::SeqCst);
}

pub struct OskOptions {
    pub width: u32,
    pub height: Option<u32>,
    pub bottom: i32,
    pub force_show: bool,
    pub private_bus: bool,
}

pub fn osk(root: &Path, o: &OskOptions) -> Result<(), String> {
    let data = ssot(root)?;
    if get(&data, "desktop.dock.osk.enabled").and_then(Toml::as_bool) == Some(false) {
        return Err("[desktop.dock.osk] is disabled".into());
    }
    let argv = strs(&data, "desktop.dock.osk.command");
    let argv = if argv.is_empty() { vec!["/usr/bin/phosh-osk-stevia".to_owned(), "--replace".to_owned()] } else { argv };
    let layer = match s(&data, "desktop.dock.osk.layer", "overlay") { "top" => 2, "bottom" => 1, "background" => 0, _ => 3 };
    let rect = OskRect {
        width: o.width.max(1),
        height: o.height.unwrap_or(n(&data, "desktop.dock.osk.height_px", 220) as u32).max(1),
        bottom: o.bottom,
        layer,
        namespace: s(&data, "desktop.dock.osk.namespace", "mios-dock").to_owned(),
    };
    let me = Instance::current()?;
    let name = format!("mios-dock-osk-{}", std::process::id());
    let sock = me.runtime.join(&name);
    let _ = fs::remove_file(&sock);
    let listener = UnixListener::bind(&sock).map_err(|e| format!("{}: {e}", sock.display()))?;
    listener.set_nonblocking(true).map_err(|e| e.to_string())?;
    unsafe {
        libc::signal(libc::SIGTERM, on_term as libc::sighandler_t);
        libc::signal(libc::SIGINT, on_term as libc::sighandler_t);
    }
    let mut cmd = if o.private_bus {
        // A private session bus for the keyboard's sm.puri.OSK0 name: never the operator's bus (test sessions).
        let mut c = Command::new("dbus-run-session");
        c.arg("--").args(&argv);
        c
    } else {
        let mut c = Command::new(&argv[0]);
        c.args(&argv[1..]);
        c
    };
    cmd.env("WAYLAND_DISPLAY", &name).env("XDG_RUNTIME_DIR", &me.runtime).env("GDK_BACKEND", "wayland").env_remove("DISPLAY").stdin(Stdio::null());
    if o.force_show {
        cmd.env("POS_DEBUG", "force-show");
    }
    unsafe {
        cmd.pre_exec(|| {
            libc::prctl(libc::PR_SET_PDEATHSIG, libc::SIGTERM);
            Ok(())
        });
    }
    let mut child = cmd.spawn().map_err(|e| format!("{}: {e}", argv[0]))?;
    let mut status = None;
    while !STOP.load(Ordering::SeqCst) {
        if let Some(st) = child.try_wait().map_err(|e| e.to_string())? {
            status = Some(st);
            break;
        }
        match listener.accept() {
            Ok((client, _)) => {
                let _ = client.set_nonblocking(false);
                let upstream = UnixStream::connect(&me.wayland).map_err(|e| format!("{}: {e}", me.wayland.display()))?;
                let tracker = Arc::new(Mutex::new(Tracker::default()));
                let (c2, u2) = (client.try_clone().map_err(|e| e.to_string())?, upstream.try_clone().map_err(|e| e.to_string())?);
                let (t1, t2, r) = (tracker.clone(), tracker, rect.clone());
                thread::spawn(move || pump(client, upstream, t1, Some(r)));
                thread::spawn(move || pump(u2, c2, t2, None));
            }
            Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => thread::sleep(Duration::from_millis(50)),
            Err(e) => return Err(e.to_string()),
        }
    }
    let _ = fs::remove_file(&sock);
    if status.is_none() {
        unsafe { libc::kill(child.id() as i32, libc::SIGTERM) };
        let until = Instant::now() + Duration::from_secs(2);
        while child.try_wait().ok().flatten().is_none() && Instant::now() < until {
            thread::sleep(Duration::from_millis(20));
        }
        let _ = child.kill();
        let _ = child.wait();
        return Ok(());
    }
    match status {
        Some(st) if st.success() => Ok(()),
        Some(st) => Err(format!("{} exited {st}", argv[0])),
        None => Ok(()),
    }
}

/// Expose the tracker counters for status lines (tests).
pub fn _tracker_counts(t: &Tracker) -> HashMap<&'static str, usize> {
    HashMap::from([("layer_shells", t.layer_shells.len()), ("layer_surfaces", t.layer_surfaces.len())])
}

#[cfg(test)]
mod tests {
    use super::*;

    fn rect() -> OskRect {
        OskRect { width: 640, height: 220, bottom: 72, layer: 3, namespace: "mios-dock".into() }
    }
    fn bind(registry: u32, iface: &str, id: u32) -> Vec<u8> {
        let mut body = 7u32.to_le_bytes().to_vec();
        body.extend(wl_string(iface));
        body.extend(4u32.to_le_bytes());
        body.extend(id.to_le_bytes());
        let mut m = header(registry, 0, 8 + body.len());
        m.extend(body);
        m
    }
    fn get_layer_surface(shell: u32, id: u32, layer: u32, ns: &str) -> Vec<u8> {
        let mut body = Vec::new();
        for a in [id, 9, 0, layer] {
            body.extend(a.to_le_bytes());
        }
        body.extend(wl_string(ns));
        let mut m = header(shell, 0, 8 + body.len());
        m.extend(body);
        m
    }
    fn split(b: &[u8]) -> Vec<(u32, u16, Vec<u32>)> {
        let mut v = Vec::new();
        let mut at = 0;
        while at < b.len() {
            let size = (rd(b, at + 4) >> 16) as usize;
            let args = (8..size).step_by(4).map(|o| rd(b, at + o)).collect();
            v.push((rd(b, at), (rd(b, at + 4) & 0xffff) as u16, args));
            at += size;
        }
        v
    }

    #[test]
    fn layer_surface_lands_in_the_dock() {
        let mut t = Tracker::default();
        let r = rect();
        assert_eq!(rewrite_request(&mut t, &r, &msg(1, 1, &[2])), msg(1, 1, &[2]));
        assert_eq!(rewrite_request(&mut t, &r, &bind(2, "zwlr_layer_shell_v1", 5)), bind(2, "zwlr_layer_shell_v1", 5));
        let out = rewrite_request(&mut t, &r, &get_layer_surface(5, 11, 2, "osk"));
        let msgs = split(&out);
        // get_layer_surface: same ids, the dock's layer, the dock's namespace
        assert_eq!(msgs[0].0, 5);
        assert_eq!(&msgs[0].2[..4], &[11, 9, 0, 3]);
        assert!(String::from_utf8_lossy(&out).contains("mios-dock"));
        // then the placement: size, anchor bottom, exclusive -1, bottom margin over the bar
        assert_eq!(msgs[1], (11, 0, vec![640, 220]));
        assert_eq!(msgs[2], (11, 1, vec![ANCHOR_BOTTOM]));
        assert_eq!(msgs[3], (11, 2, vec![u32::MAX]));
        assert_eq!(msgs[4], (11, 3, vec![0, 0, 72, 0]));
        // stevia's own full-width requests are overridden
        assert_eq!(rewrite_request(&mut t, &r, &msg(11, 1, &[2 | 4 | 8])), msg(11, 1, &[ANCHOR_BOTTOM]));
        assert_eq!(rewrite_request(&mut t, &r, &msg(11, 2, &[180])), msg(11, 2, &[u32::MAX]));
        assert_eq!(rewrite_request(&mut t, &r, &msg(11, 0, &[0, 180])), msg(11, 0, &[640, 220]));
        // ack_configure passes untouched
        assert_eq!(rewrite_request(&mut t, &r, &msg(11, 6, &[42])), msg(11, 6, &[42]));
    }

    #[test]
    fn other_objects_and_recycled_ids_are_untouched() {
        let mut t = Tracker::default();
        let r = rect();
        rewrite_request(&mut t, &r, &msg(1, 1, &[2]));
        // negative control: another global bound -> no rewrite of its requests
        rewrite_request(&mut t, &r, &bind(2, "wl_compositor", 5));
        let m = get_layer_surface(5, 11, 2, "osk");
        assert_eq!(rewrite_request(&mut t, &r, &m), m);
        // a layer surface whose id is deleted and recycled for a wl_surface is no longer rewritten
        rewrite_request(&mut t, &r, &bind(2, "zwlr_layer_shell_v1", 6));
        rewrite_request(&mut t, &r, &get_layer_surface(6, 12, 2, "osk"));
        observe_event(&mut t, &msg(1, 1, &[12]));
        assert_eq!(rewrite_request(&mut t, &r, &msg(12, 1, &[3])), msg(12, 1, &[3]));
    }

    #[test]
    fn apps_migrate_only_when_containerized_or_stateless() {
        let none: Vec<String> = vec![];
        let f = classify("0::/user.slice/app-flatpak-org.gnome.Calculator-123.scope", Some("[Application]\nname=org.gnome.Calculator\n"), "org.gnome.Calculator", &none);
        assert_eq!((f.kind, f.migrate, f.app_id.as_str()), ("flatpak", true, "org.gnome.Calculator"));
        assert_eq!(classify("0::/machine.slice/libpod-abc.scope", None, "x", &none).kind, "container");
        assert!(classify("0::/user.slice/app.slice/app-foo.scope", None, "foot", &["foot".into()]).migrate);
        // negative control: a native app with local state is never "migrated"
        let n = classify("0::/user.slice/app.slice/app-zen.scope", None, "zen", &none);
        assert_eq!((n.kind, n.migrate), ("native", false));
    }

    #[test]
    fn targets_report_honest_capabilities() {
        let data: Toml = toml::from_str(
            "[remote.rdp]\nvirtual_output = \"HEADLESS-MIOS-RDP\"\n[vm.looking_glass]\nenabled = true\ndomains = [\"vm-a\"]\n[desktop.dock.targets]\nlocal_label = \"This desktop\"\nwaypipe_hosts = [\"node2\"]\n",
        )
        .unwrap();
        let me = Instance { runtime: "/run/x".into(), signature: "sig-me".into(), wayland: "/run/x/wayland-1".into() };
        let mons = json!([{"name": "DP-1", "id": 0}, {"name": "HEADLESS-MIOS-RDP", "id": 1}]);
        let sessions = vec![
            json!({"instance": "sig-me", "state": "running"}),
            json!({"instance": "sig-n", "state": "running", "seat": "dockhud", "user": "dockhud", "runtime": "/run/x/n", "wayland_display": "wayland-1"}),
        ];
        let v = targets_json(&data, &me, &mons, &sessions, None);
        let ts = v["targets"].as_array().unwrap();
        let by = |id: &str| ts.iter().find(|t| t["id"] == id).cloned().unwrap();
        assert_eq!(by("output:DP-1")["move"]["method"], "monitor-move");
        assert_eq!(by("output:HEADLESS-MIOS-RDP")["kind"], "remote-rdp");
        assert_eq!(by("session:sig-n")["move"]["method"], "relaunch");
        assert_eq!(by("session:sig-n")["move"]["live_state"], false);
        assert!(!ts.iter().any(|t| t["id"] == "session:sig-me"), "own session is not a target");
        assert_eq!(by("vm:vm-a")["move"]["method"], "none");
        assert_eq!(v["methods"]["wayland-handoff"]["supported"], false);
        // waypipe is offered only as available as the binary is
        assert_eq!(by("waypipe:node2")["available"], which("waypipe"));
    }
}
