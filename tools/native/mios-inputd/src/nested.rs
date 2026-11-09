// AI-hint: The nested-session device contract on the inputd side (I4): read mios-gen's live registry ([desktop.nested].registry_dir) so the host bridge never owns a node assigned to a nested session, and so `bridge --nested <seat>` follows exactly that session (its compositor, its private room packet, its assigned controller nodes).
// AI-related: tools/native/mios-gen/src/nested_devices.rs (Claim/live_sessions), tools/native/mios-gen/src/nested_session.rs (session.json/pad_state), usr/share/mios/mios.toml [desktop.nested]
// AI-functions: start_time, live_sessions, assigned_nodes, session, host_service_env, seat_pad_name_matches
use serde_json::Value as Json;
use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

/// Process start time (clock ticks since boot), field 22 of /proc/<pid>/stat: a PID plus its start names one process.
pub fn start_time(proc_root: &Path, pid: u32) -> Option<u64> {
    let stat = std::fs::read_to_string(proc_root.join(pid.to_string()).join("stat")).ok()?;
    stat.rsplit_once(')')?
        .1
        .split_whitespace()
        .nth(19)?
        .parse()
        .ok()
}

/// A registry entry is live while the launcher that wrote it runs (the same liveness test mios-gen applies).
fn alive(proc_root: &Path, entry: &Json) -> bool {
    let (Some(pid), Some(st)) = (
        entry["launcher_pid"].as_u64(),
        entry["launcher_start"].as_u64(),
    ) else {
        return false;
    };
    u32::try_from(pid)
        .ok()
        .and_then(|p| start_time(proc_root, p))
        == Some(st)
}

/// Live entries of `<runtime>/<registry_dir>/*.json`; unreadable, symlinked or stale files are ignored (never removed here).
pub fn live_sessions(registry: &Path, proc_root: &Path) -> Vec<(PathBuf, Json)> {
    let mut out = vec![];
    for e in std::fs::read_dir(registry).into_iter().flatten().flatten() {
        let p = e.path();
        if p.extension().is_none_or(|x| x != "json") {
            continue;
        }
        if std::fs::symlink_metadata(&p)
            .map(|m| m.file_type().is_symlink())
            .unwrap_or(true)
        {
            continue;
        }
        if let Some(j) = std::fs::read(&p)
            .ok()
            .and_then(|b| serde_json::from_slice::<Json>(&b).ok())
        {
            if alive(proc_root, &j) {
                out.push((p, j));
            }
        }
    }
    out.sort_by(|a, b| a.0.cmp(&b.0));
    out
}

fn entry_nodes(entry: &Json) -> Vec<(PathBuf, String)> {
    entry["devices"]
        .as_array()
        .into_iter()
        .flatten()
        .flat_map(|d| {
            let kind = d["kind"].as_str().unwrap_or("").to_owned();
            std::iter::once(&d["path"])
                .chain(d["siblings"].as_array().into_iter().flatten())
                .filter_map(Json::as_str)
                .map(move |p| (PathBuf::from(p), kind.clone()))
                .collect::<Vec<_>>()
        })
        .collect()
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
    pub control_socket: Option<PathBuf>,
}
impl Session {
    pub fn alive(&self, proc_root: &Path) -> bool {
        start_time(proc_root, self.launcher_pid) == Some(self.launcher_start)
    }

    /// Verify that a control-socket peer (`peer_uid`, `peer_pid`) is the continuing launcher of this session,
    /// and that any optional request-level seat / launcher_pid / launcher_start claims match.
    pub fn verify_launcher_peer(
        &self,
        proc_root: &Path,
        expected_uid: u32,
        peer: (u32, u32),
        req_seat: Option<&str>,
        req_pid: Option<u32>,
        req_start: Option<u64>,
    ) -> Result<(), String> {
        let (peer_uid, peer_pid) = peer;
        if peer_uid != expected_uid {
            return Err(format!(
                "unauthorized: peer UID {peer_uid} does not match expected UID {expected_uid}"
            ));
        }
        if peer_pid == 0 || peer_pid != self.launcher_pid {
            return Err(format!(
                "unauthorized: peer PID {peer_pid} is not session launcher PID {}",
                self.launcher_pid
            ));
        }
        let actual_start = start_time(proc_root, peer_pid).ok_or_else(|| {
            format!("unauthorized: peer process {peer_pid} not found in proc")
        })?;
        if actual_start != self.launcher_start {
            return Err(format!(
                "unauthorized: peer PID {peer_pid} start ticks {actual_start} != launcher start {}",
                self.launcher_start
            ));
        }
        if let Some(s) = req_seat {
            if s != self.seat {
                return Err(format!(
                    "unauthorized: request seat {s:?} does not match session seat {:?}",
                    self.seat
                ));
            }
        }
        if let Some(p) = req_pid {
            if p != self.launcher_pid {
                return Err(format!(
                    "unauthorized: request launcher_pid {p} does not match session launcher_pid {}",
                    self.launcher_pid
                ));
            }
        }
        if let Some(st) = req_start {
            if st != self.launcher_start {
                return Err(format!(
                    "unauthorized: request launcher_start {st} does not match session launcher_start {}",
                    self.launcher_start
                ));
            }
        }
        let meta = std::fs::symlink_metadata(&self.entry).map_err(|e| {
            format!(
                "cannot stat registry entry {}: {e}",
                self.entry.display()
            )
        })?;
        if meta.file_type().is_symlink() {
            return Err(format!(
                "registry entry {} is a symlink",
                self.entry.display()
            ));
        }
        let bytes = std::fs::read(&self.entry)
            .map_err(|e| format!("cannot read registry entry {}: {e}", self.entry.display()))?;
        let j: Json = serde_json::from_slice(&bytes)
            .map_err(|e| format!("corrupt registry entry {}: {e}", self.entry.display()))?;
        if j["seat"].as_str() != Some(self.seat.as_str())
            || j["launcher_pid"].as_u64() != Some(u64::from(self.launcher_pid))
            || j["launcher_start"].as_u64() != Some(self.launcher_start)
        {
            return Err(format!(
                "registry entry {} identity changed on disk",
                self.entry.display()
            ));
        }
        Ok(())
    }

    /// Refresh the controllers assigned to this session by re-reading the authoritative registry entry on disk.
    /// Returns the updated list of controller event nodes.
    pub fn refresh_controllers(&mut self, proc_root: &Path) -> Result<Vec<PathBuf>, String> {
        if !self.alive(proc_root) {
            return Err(format!(
                "launcher PID {} has exited or was recycled",
                self.launcher_pid
            ));
        }
        let meta = std::fs::symlink_metadata(&self.entry).map_err(|e| {
            format!(
                "cannot stat registry entry {}: {e}",
                self.entry.display()
            )
        })?;
        if meta.file_type().is_symlink() {
            return Err(format!(
                "registry entry {} is a symlink",
                self.entry.display()
            ));
        }
        let bytes = std::fs::read(&self.entry)
            .map_err(|e| format!("cannot read registry entry {}: {e}", self.entry.display()))?;
        let j: Json = serde_json::from_slice(&bytes)
            .map_err(|e| format!("corrupt registry entry {}: {e}", self.entry.display()))?;
        if j["seat"].as_str() != Some(self.seat.as_str())
            || j["launcher_pid"].as_u64() != Some(u64::from(self.launcher_pid))
            || j["launcher_start"].as_u64() != Some(self.launcher_start)
        {
            return Err(format!(
                "registry entry {} identity mismatch on refresh",
                self.entry.display()
            ));
        }
        let controllers: Vec<PathBuf> = entry_nodes(&j)
            .into_iter()
            .filter(|(p, kind)| {
                kind == "controller"
                    && p.file_name()
                        .is_some_and(|n| n.to_string_lossy().starts_with("event"))
            })
            .map(|(p, _)| p)
            .collect();
        self.controllers = controllers.clone();
        if let Some(cs) = j["control_socket"].as_str() {
            self.control_socket = Some(PathBuf::from(cs));
        }
        Ok(controllers)
    }
}

/// The live, running session of `seat`. A session with the shared /tmp has no private room packet (its room reads a
/// masked /tmp/mios-hypr3d-pad.bin): it is refused, a labelled seat gets a private /tmp by default.
pub fn session(registry: &Path, proc_root: &Path, seat: &str) -> Result<Session, String> {
    if seat.is_empty()
        || seat.len() > 32
        || !seat
            .bytes()
            .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-')
        || seat.starts_with('-')
    {
        return Err(format!(
            "seat {seat:?} must be 1..32 of a-z, 0-9 and '-' (not leading)"
        ));
    }
    let (entry, j) = live_sessions(registry, proc_root)
        .into_iter()
        .find(|(_, j)| j["seat"].as_str() == Some(seat))
        .ok_or_else(|| {
            format!(
                "no live nested session for seat {seat:?} in {}",
                registry.display()
            )
        })?;
    if j["state"].as_str() != Some("running") {
        return Err(format!(
            "the nested session for seat {seat:?} is not running yet (state {})",
            j["state"]
        ));
    }
    let shown = entry.display().to_string();
    let s = |k: &str| {
        j[k].as_str()
            .filter(|v| !v.is_empty() && !v.contains('\0'))
            .map(str::to_owned)
            .ok_or_else(|| format!("registry entry {shown}: {k} missing"))
    };
    let pad_state = j["pad_state"].as_str().map(PathBuf::from)
        .ok_or_else(|| format!("the session for seat {seat:?} shares the parent /tmp; its room packet is masked (desktop.nested.private_tmp)"))?;
    let runtime = PathBuf::from(s("runtime")?);
    let instance = s("instance")?;
    if instance.contains('/')
        || instance.contains("..")
        || !runtime.is_absolute()
        || !pad_state.is_absolute()
    {
        return Err(format!(
            "registry entry {}: invalid runtime, instance or pad_state",
            entry.display()
        ));
    }
    let controllers = entry_nodes(&j)
        .into_iter()
        .filter(|(p, kind)| {
            kind == "controller"
                && p.file_name()
                    .is_some_and(|n| n.to_string_lossy().starts_with("event"))
        })
        .map(|(p, _)| p)
        .collect();
    let control_socket = j["control_socket"].as_str().map(PathBuf::from);
    Ok(Session {
        seat: seat.into(),
        launcher_pid: j["launcher_pid"]
            .as_u64()
            .and_then(|v| u32::try_from(v).ok())
            .ok_or("launcher_pid missing")?,
        launcher_start: j["launcher_start"]
            .as_u64()
            .ok_or("launcher_start missing")?,
        entry,
        runtime,
        instance,
        wayland_display: s("wayland_display")?,
        pad_state,
        controllers,
        control_socket,
    })
}

/// The host services a nested bridge's action children still reach after it switches XDG_RUNTIME_DIR to the session
/// runtime (which has no pipewire-0 and no bus socket): PipeWire through PIPEWIRE_RUNTIME_DIR (the same variable
/// mios-gen gives the session itself) and the user bus through DBUS_SESSION_BUS_ADDRESS (playerctl, notifications).
/// A value the bridge already inherited wins; the bus is named only when its socket exists.
pub fn host_service_env(
    host_runtime: &Path,
    inherited: impl Fn(&str) -> Option<String>,
) -> Vec<(&'static str, String)> {
    let mut out = vec![];
    if inherited("PIPEWIRE_RUNTIME_DIR").is_none_or(|v| v.is_empty()) {
        out.push(("PIPEWIRE_RUNTIME_DIR", host_runtime.display().to_string()));
    }
    let bus = host_runtime.join("bus");
    if inherited("DBUS_SESSION_BUS_ADDRESS").is_none_or(|v| v.is_empty()) && bus.exists() {
        out.push((
            "DBUS_SESSION_BUS_ADDRESS",
            format!("unix:path={}", bus.display()),
        ));
    }
    out
}

/// [desktop.nested.devices].virtual_pad_name with `{seat}` filled by any label: a seat's pad, never the host's.
pub fn seat_pad_name_matches(template: &str, name: &str) -> bool {
    let Some((pre, post)) = template.split_once("{seat}") else {
        return false;
    };
    name.len() > pre.len() + post.len() && name.starts_with(pre) && name.ends_with(post)
}

#[cfg(test)]
mod tests {
    use super::*;
    fn fake_proc(root: &Path, pid: u32, start: u64) {
        let d = root.join(pid.to_string());
        std::fs::create_dir_all(&d).unwrap();
        let fields: Vec<String> = (0..25)
            .map(|i| {
                if i == 18 {
                    start.to_string()
                } else {
                    "0".into()
                }
            })
            .collect();
        std::fs::write(
            d.join("stat"),
            format!("{pid} (mios-gen) S {}\n", fields.join(" ")),
        )
        .unwrap();
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
        std::fs::write(
            reg.join("p2.json"),
            entry(
                "p2",
                41,
                900,
                "running",
                Some("/run/user/1000/x/tmp/mios-hypr3d-pad.bin"),
            ),
        )
        .unwrap();
        // A launcher PID that was recycled (other start time) holds nothing.
        std::fs::write(
            reg.join("old.json"),
            entry("old", 41, 1, "running", None).replace("event30", "event40"),
        )
        .unwrap();
        let a = assigned_nodes(&reg, &proc_root);
        assert_eq!(
            a.get(Path::new("/dev/input/event30")).map(String::as_str),
            Some("p2")
        );
        assert!(
            a.contains_key(Path::new("/dev/input/js3"))
                && a.contains_key(Path::new("/dev/input/event31"))
        );
        assert!(
            !a.contains_key(Path::new("/dev/input/event40")),
            "a stale entry assigns nothing"
        );
        let s = session(&reg, &proc_root, "p2").unwrap();
        assert_eq!(
            s.controllers,
            vec![PathBuf::from("/dev/input/event30")],
            "only controller event nodes"
        );
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
        std::fs::write(
            reg.join("a.json"),
            entry("a", 5, 7, "starting", Some("/x/pad.bin")),
        )
        .unwrap();
        std::fs::write(reg.join("b.json"), entry("b", 5, 7, "running", None)).unwrap();
        assert!(session(&reg, &proc_root, "a")
            .unwrap_err()
            .contains("not running"));
        assert!(session(&reg, &proc_root, "b")
            .unwrap_err()
            .contains("shares the parent /tmp"));
        assert!(session(&reg, &proc_root, "zz")
            .unwrap_err()
            .contains("no live nested session"));
        assert!(session(&reg, &proc_root, "../x")
            .unwrap_err()
            .contains("must be"));
    }
    #[test]
    fn seat_pad_names_follow_the_ssot_template() {
        let t = "MiOS Nested Pad {seat}";
        assert!(seat_pad_name_matches(t, "MiOS Nested Pad p2"));
        assert!(!seat_pad_name_matches(t, "MiOS Nested Pad "));
        assert!(!seat_pad_name_matches(t, "MiOS Agent Pad host"));
        assert!(!seat_pad_name_matches("no placeholder", "no placeholder"));
    }
    #[test]
    fn a_nested_bridge_keeps_the_host_pipewire_and_bus_for_its_actions() {
        // D3: the session runtime has no pipewire-0 or bus; wpctl/playerctl children must still reach the host's.
        let t = tempfile::tempdir().unwrap();
        let none = |_: &str| None;
        let env = host_service_env(t.path(), none);
        assert_eq!(
            env,
            vec![("PIPEWIRE_RUNTIME_DIR", t.path().display().to_string())],
            "no bus socket: no bus address"
        );
        std::fs::write(t.path().join("bus"), "").unwrap();
        let env = host_service_env(t.path(), none);
        assert!(
            env.contains(&(
                "DBUS_SESSION_BUS_ADDRESS",
                format!("unix:path={}/bus", t.path().display())
            )),
            "{env:?}"
        );
        let inherited = |k: &str| {
            (k == "DBUS_SESSION_BUS_ADDRESS" || k == "PIPEWIRE_RUNTIME_DIR")
                .then(|| "kept".to_string())
        };
        assert!(
            host_service_env(t.path(), inherited).is_empty(),
            "an inherited value wins"
        );
    }
    #[test]
    fn concurrent_seats_keep_devices_and_room_packets_independent() {
        let t = tempfile::tempdir().unwrap();
        let proc_root = t.path().join("proc");
        let registry = t.path().join("registry");
        std::fs::create_dir_all(&registry).unwrap();
        fake_proc(&proc_root, 41, 900);
        fake_proc(&proc_root, 42, 901);
        let mut expected = Vec::new();
        for (seat, pid, start, event, js, instance) in [
            ("pet-one", 41, 900, "event30", "js3", "first_1_41"),
            ("pet-two", 42, 901, "event32", "js4", "second_1_42"),
        ] {
            let mut record: Json =
                serde_json::from_str(&entry(seat, pid, start, "running", Some("/placeholder")))
                    .unwrap();
            let runtime = format!("/run/user/1000/mios-nested-{seat}");
            let packet = format!("{runtime}/tmp/mios-hypr3d-pad.bin");
            let event = format!("/dev/input/{event}");
            let js = format!("/dev/input/{js}");
            record["runtime"] = runtime.clone().into();
            record["instance"] = instance.into();
            record["pad_state"] = packet.clone().into();
            record["devices"] = serde_json::json!([
                {"path":event,"kind":"controller","siblings":[js]},
                {"path":format!("{runtime}/keyboard"),"kind":"keyboard","siblings":[]}
            ]);
            std::fs::write(registry.join(format!("{seat}.json")), record.to_string()).unwrap();
            expected.push((seat, runtime, packet, event, js));
        }
        let first = session(&registry, &proc_root, "pet-one").unwrap();
        let second = session(&registry, &proc_root, "pet-two").unwrap();
        assert_ne!(first.runtime, second.runtime);
        assert_ne!(first.instance, second.instance);
        assert_ne!(first.pad_state, second.pad_state);
        let assigned = assigned_nodes(&registry, &proc_root);
        for (seat, runtime, packet, event, js) in &expected {
            let resolved = session(&registry, &proc_root, seat).unwrap();
            assert_eq!(resolved.runtime, PathBuf::from(runtime));
            assert_eq!(resolved.pad_state, PathBuf::from(packet));
            assert_eq!(resolved.controllers, vec![PathBuf::from(event)]);
            assert_eq!(
                assigned.get(Path::new(event)).map(String::as_str),
                Some(*seat)
            );
            assert_eq!(assigned.get(Path::new(js)).map(String::as_str), Some(*seat));
        }
        assert!(!assigned.contains_key(Path::new("/dev/input/event14")));
        assert!(session(&registry, &proc_root, "unassigned").is_err());

        // Recycling one launcher releases only its devices; the other seat
        // keeps its own controller and packet without falling back to the host.
        fake_proc(&proc_root, 41, 902);
        assert!(session(&registry, &proc_root, "pet-one").is_err());
        let remaining = assigned_nodes(&registry, &proc_root);
        assert!(!remaining.contains_key(Path::new(&expected[0].3)));
        assert!(!remaining.contains_key(Path::new(&expected[0].4)));
        assert_eq!(
            remaining.get(Path::new(&expected[1].3)).map(String::as_str),
            Some("pet-two")
        );
        assert_eq!(session(&registry, &proc_root, "pet-two").unwrap(), second);
    }

    #[test]
    fn session_refresh_controllers_and_control_socket() {
        let t = tempfile::tempdir().unwrap();
        let (proc_root, reg) = (t.path().join("proc"), t.path().join("reg"));
        std::fs::create_dir_all(&reg).unwrap();
        fake_proc(&proc_root, 50, 1000);

        let mut record: Json = serde_json::from_str(&entry(
            "p3",
            50,
            1000,
            "running",
            Some("/tmp/mios-hypr3d-pad.bin"),
        ))
        .unwrap();
        record["control_socket"] = "/run/user/1000/mios-nested-p3/control.sock".into();
        std::fs::write(reg.join("p3.json"), record.to_string()).unwrap();

        let mut s = session(&reg, &proc_root, "p3").unwrap();
        assert_eq!(
            s.control_socket,
            Some(PathBuf::from("/run/user/1000/mios-nested-p3/control.sock"))
        );
        assert_eq!(s.controllers, vec![PathBuf::from("/dev/input/event30")]);

        // When a controller is returned, registry devices array is updated on disk
        record["devices"] = serde_json::json!([
            {"path": "/dev/input/event31", "kind": "keyboard", "siblings": []}
        ]);
        std::fs::write(reg.join("p3.json"), record.to_string()).unwrap();

        // Refresh picks up the return and updates controllers
        let updated = s.refresh_controllers(&proc_root).unwrap();
        assert!(updated.is_empty(), "controller should be removed");
        assert!(s.controllers.is_empty());

        // When launcher dies / recycled PID, refresh fails
        fake_proc(&proc_root, 50, 1001);
        assert!(s.refresh_controllers(&proc_root).is_err());
    }

    #[test]
    fn verify_launcher_peer_and_symlink_hardening() {
        let t = tempfile::tempdir().unwrap();
        let (proc_root, reg) = (t.path().join("proc"), t.path().join("reg"));
        std::fs::create_dir_all(&reg).unwrap();
        fake_proc(&proc_root, 77, 2000);
        fake_proc(&proc_root, 78, 2000);

        let record = entry(
            "p4",
            77,
            2000,
            "running",
            Some("/tmp/mios-hypr3d-pad.bin"),
        );
        std::fs::write(reg.join("p4.json"), &record).unwrap();

        let s = session(&reg, &proc_root, "p4").unwrap();
        assert!(
            s.verify_launcher_peer(&proc_root, 1000, (1000, 77), Some("p4"), Some(77), Some(2000))
                .is_ok()
        );
        // Wrong UID refuses
        assert!(
            s.verify_launcher_peer(&proc_root, 1000, (1001, 77), Some("p4"), Some(77), Some(2000))
                .unwrap_err()
                .contains("peer UID")
        );
        // Same-UID non-launcher PID refuses
        assert!(
            s.verify_launcher_peer(&proc_root, 1000, (1000, 78), Some("p4"), Some(77), Some(2000))
                .unwrap_err()
                .contains("not session launcher PID")
        );
        // Wrong request seat refuses
        assert!(
            s.verify_launcher_peer(&proc_root, 1000, (1000, 77), Some("p5"), Some(77), Some(2000))
                .unwrap_err()
                .contains("request seat")
        );
        // Recycled start ticks refuses
        fake_proc(&proc_root, 77, 2001);
        assert!(
            s.verify_launcher_peer(&proc_root, 1000, (1000, 77), Some("p4"), Some(77), Some(2000))
                .unwrap_err()
                .contains("start ticks")
        );
        fake_proc(&proc_root, 77, 2000);

        // Symlinked registry entry is ignored by live_sessions and refused by verify_launcher_peer
        std::fs::remove_file(reg.join("p4.json")).unwrap();
        let outside = t.path().join("outside.json");
        std::fs::write(&outside, &record).unwrap();
        #[cfg(unix)]
        {
            std::os::unix::fs::symlink(&outside, reg.join("p4.json")).unwrap();
            assert!(live_sessions(&reg, &proc_root).is_empty());
            assert!(
                s.verify_launcher_peer(&proc_root, 1000, (1000, 77), Some("p4"), None, None)
                    .unwrap_err()
                    .contains("symlink")
            );
        }
    }
}
