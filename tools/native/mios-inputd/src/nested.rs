// AI-hint: The nested-session device contract on the inputd side (I4): read mios-gen's live registry ([desktop.nested].registry_dir) so the host bridge never owns a node assigned to a nested session, and so `bridge --nested <seat>` follows exactly that session (its compositor, its private room packet, its assigned controller nodes).
// AI-related: tools/native/mios-gen/src/nested_devices.rs (Claim, live_sessions), tools/native/mios-gen/src/nested_session.rs (session.json, pad_state), usr/share/mios/mios.toml [desktop.nested]
// AI-functions: start_time, live_sessions, assigned_nodes, session, seat_pad_name_matches
use serde_json::Value as Json;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// Process start time (clock ticks since boot), field 22 of /proc/<pid>/stat: a PID plus its start names one process.
pub fn start_time(proc_root: &Path, pid: u32) -> Option<u64> {
    let stat = std::fs::read_to_string(proc_root.join(pid.to_string()).join("stat")).ok()?;
    stat.rsplit_once(')')?.1.split_whitespace().nth(19)?.parse().ok()
}

/// A registry entry is live while the launcher that wrote it runs (the same liveness test mios-gen applies).
fn alive(proc_root: &Path, entry: &Json) -> bool {
    let (Some(pid), Some(st)) = (entry["launcher_pid"].as_u64(), entry["launcher_start"].as_u64()) else { return false };
    u32::try_from(pid).ok().and_then(|p| start_time(proc_root, p)) == Some(st)
}

/// Live entries of `<runtime>/<registry_dir>/*.json`; unreadable or stale files are ignored (never removed here).
pub fn live_sessions(registry: &Path, proc_root: &Path) -> Vec<(PathBuf, Json)> {
    let mut out = vec![];
    for e in std::fs::read_dir(registry).into_iter().flatten().flatten() {
        let p = e.path();
        if p.extension().is_none_or(|x| x != "json") {
            continue;
        }
        if let Some(j) = std::fs::read(&p).ok().and_then(|b| serde_json::from_slice::<Json>(&b).ok()) {
            if alive(proc_root, &j) {
                out.push((p, j));
            }
        }
    }
    out.sort_by(|a, b| a.0.cmp(&b.0));
    out
}

fn entry_nodes(entry: &Json) -> Vec<(PathBuf, String)> {
    entry["devices"].as_array().into_iter().flatten().flat_map(|d| {
        let kind = d["kind"].as_str().unwrap_or("").to_owned();
        std::iter::once(&d["path"]).chain(d["siblings"].as_array().into_iter().flatten())
            .filter_map(Json::as_str).map(move |p| (PathBuf::from(p), kind.clone())).collect::<Vec<_>>()
    }).collect()
}

/// Every node a live nested session holds -> its seat label ("" for an unlabelled session).
pub fn assigned_nodes(registry: &Path, proc_root: &Path) -> BTreeMap<PathBuf, String> {
    let mut out = BTreeMap::new();
    for (_, j) in live_sessions(registry, proc_root) {
        let seat = j["seat"].as_str().unwrap_or("").to_owned();
        for (p, _) in entry_nodes(&j) {
            out.insert(p, seat.clone());
        }
    }
    out
}

/// What `bridge --nested <seat>` follows: the running session's compositor and private room packet, and the
/// controller nodes assigned to it.
#[derive(Clone, Debug, PartialEq)]
pub struct Session {
    pub seat: String,
    pub entry: PathBuf,
    pub launcher_pid: u32,
    pub launcher_start: u64,
    pub runtime: PathBuf,
    pub instance: String,
    pub wayland_display: String,
    pub pad_state: PathBuf,
    pub controllers: Vec<PathBuf>,
}
impl Session {
    pub fn alive(&self, proc_root: &Path) -> bool {
        start_time(proc_root, self.launcher_pid) == Some(self.launcher_start)
    }
}

/// The live, running session of `seat`. A session with the shared /tmp has no private room packet (its room reads a
/// masked /tmp/mios-hypr3d-pad.bin): it is refused, a labelled seat gets a private /tmp by default.
pub fn session(registry: &Path, proc_root: &Path, seat: &str) -> Result<Session, String> {
    if seat.is_empty() || seat.len() > 32 || !seat.bytes().all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-') || seat.starts_with('-') {
        return Err(format!("seat {seat:?} must be 1..32 of a-z, 0-9 and '-' (not leading)"));
    }
    let (entry, j) = live_sessions(registry, proc_root).into_iter().find(|(_, j)| j["seat"].as_str() == Some(seat))
        .ok_or_else(|| format!("no live nested session for seat {seat:?} in {}", registry.display()))?;
    if j["state"].as_str() != Some("running") {
        return Err(format!("the nested session for seat {seat:?} is not running yet (state {})", j["state"]));
    }
    let shown = entry.display().to_string();
    let s = |k: &str| j[k].as_str().filter(|v| !v.is_empty() && !v.contains('\0')).map(str::to_owned).ok_or_else(|| format!("registry entry {shown}: {k} missing"));
    let pad_state = j["pad_state"].as_str().map(PathBuf::from)
        .ok_or_else(|| format!("the session for seat {seat:?} shares the parent /tmp; its room packet is masked (desktop.nested.private_tmp)"))?;
    let runtime = PathBuf::from(s("runtime")?);
    let instance = s("instance")?;
    if instance.contains('/') || instance.contains("..") || !runtime.is_absolute() || !pad_state.is_absolute() {
        return Err(format!("registry entry {}: invalid runtime, instance or pad_state", entry.display()));
    }
    let controllers = entry_nodes(&j).into_iter().filter(|(p, kind)| kind == "controller" && p.file_name().is_some_and(|n| n.to_string_lossy().starts_with("event"))).map(|(p, _)| p).collect();
    Ok(Session {
        seat: seat.into(),
        launcher_pid: j["launcher_pid"].as_u64().and_then(|v| u32::try_from(v).ok()).ok_or("launcher_pid missing")?,
        launcher_start: j["launcher_start"].as_u64().ok_or("launcher_start missing")?,
        entry,
        runtime,
        instance,
        wayland_display: s("wayland_display")?,
        pad_state,
        controllers,
    })
}

/// [desktop.nested.devices].virtual_pad_name with `{seat}` filled by any label: a seat's pad, never the host's.
pub fn seat_pad_name_matches(template: &str, name: &str) -> bool {
    let Some((pre, post)) = template.split_once("{seat}") else { return false };
    name.len() > pre.len() + post.len() && name.starts_with(pre) && name.ends_with(post)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fake_proc(root: &Path, pid: u32, start: u64) {
        let d = root.join(pid.to_string());
        std::fs::create_dir_all(&d).unwrap();
        let fields: Vec<String> = (0..25).map(|i| if i == 18 { start.to_string() } else { "0".into() }).collect();
        std::fs::write(d.join("stat"), format!("{pid} (mios-gen) S {}\n", fields.join(" "))).unwrap();
    }
    fn entry(seat: &str, pid: u32, start: u64, state: &str, pad: Option<&str>) -> String {
        serde_json::json!({"seat": seat, "launcher_pid": pid, "launcher_start": start, "state": state, "runtime": "/run/user/1000/mios-nested-desktop-7",
            "instance": "abc_1_2", "wayland_display": "wayland-1", "pad_state": pad,
            "devices": [{"path": "/dev/input/event30", "kind": "controller", "siblings": ["/dev/input/js3"]}, {"path": "/dev/input/event31", "kind": "keyboard", "siblings": []}]}).to_string()
    }
    #[test]
    fn live_entries_assign_nodes_and_stale_ones_do_not() {
        let t = tempfile::tempdir().unwrap();
        let (proc_root, reg) = (t.path().join("proc"), t.path().join("reg"));
        std::fs::create_dir_all(&reg).unwrap();
        fake_proc(&proc_root, 41, 900);
        std::fs::write(reg.join("p2.json"), entry("p2", 41, 900, "running", Some("/run/user/1000/x/tmp/mios-hypr3d-pad.bin"))).unwrap();
        // A launcher PID that was recycled (other start time) holds nothing.
        std::fs::write(reg.join("old.json"), entry("old", 41, 1, "running", None).replace("event30", "event40")).unwrap();
        let a = assigned_nodes(&reg, &proc_root);
        assert_eq!(a.get(Path::new("/dev/input/event30")).map(String::as_str), Some("p2"));
        assert!(a.contains_key(Path::new("/dev/input/js3")) && a.contains_key(Path::new("/dev/input/event31")));
        assert!(!a.contains_key(Path::new("/dev/input/event40")), "a stale entry assigns nothing");
        let s = session(&reg, &proc_root, "p2").unwrap();
        assert_eq!(s.controllers, vec![PathBuf::from("/dev/input/event30")], "only controller event nodes");
        assert_eq!(s.instance, "abc_1_2");
        assert!(s.alive(&proc_root));
        fake_proc(&proc_root, 41, 901);
        assert!(!s.alive(&proc_root), "the launcher exited (PID reused)");
    }
    #[test]
    fn a_session_must_run_with_a_private_room_packet() {
        let t = tempfile::tempdir().unwrap();
        let (proc_root, reg) = (t.path().join("proc"), t.path().join("reg"));
        std::fs::create_dir_all(&reg).unwrap();
        fake_proc(&proc_root, 5, 7);
        std::fs::write(reg.join("a.json"), entry("a", 5, 7, "starting", Some("/x/pad.bin"))).unwrap();
        std::fs::write(reg.join("b.json"), entry("b", 5, 7, "running", None)).unwrap();
        assert!(session(&reg, &proc_root, "a").unwrap_err().contains("not running"));
        assert!(session(&reg, &proc_root, "b").unwrap_err().contains("shares the parent /tmp"));
        assert!(session(&reg, &proc_root, "zz").unwrap_err().contains("no live nested session"));
        assert!(session(&reg, &proc_root, "../x").unwrap_err().contains("must be"));
    }
    #[test]
    fn seat_pad_names_follow_the_ssot_template() {
        let t = "MiOS Nested Pad {seat}";
        assert!(seat_pad_name_matches(t, "MiOS Nested Pad p2"));
        assert!(!seat_pad_name_matches(t, "MiOS Nested Pad "));
        assert!(!seat_pad_name_matches(t, "MiOS Agent Pad host"));
        assert!(!seat_pad_name_matches("no placeholder", "no placeholder"));
    }
}
