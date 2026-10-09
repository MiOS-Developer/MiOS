// AI-hint: Transactional caller-owned agent mailboxes with leases, idempotent sends and recipient receipts.
// AI-related: usr/libexec/mios/mios-mcp-server, usr/share/mios/mios.toml [mcp.agents]
// AI-functions: main, dispatch
use serde_json::{json, Value};
use sha2::{Digest, Sha256};
use std::{
    fs::{self, OpenOptions},
    io::{IsTerminal, Read, Write},
    path::{Path, PathBuf},
    process::{Command, Stdio},
    time::{Duration, Instant, SystemTime, UNIX_EPOCH},
};

fn display_text(value: &Value) -> String {
    value
        .as_str()
        .unwrap_or("")
        .chars()
        .filter(|c| {
            !c.is_control() && !matches!(*c, '\u{202a}'..='\u{202e}' | '\u{2066}'..='\u{2069}')
        })
        .take(160)
        .collect()
}

fn observation(state: &Value, request: &Value, now: u64) -> Result<Value, String> {
    let max_rows = request["observation"]["max_rows"]
        .as_u64()
        .filter(|v| (1..=256).contains(v))
        .ok_or("invalid observation max_rows")? as usize;
    let messages = state["messages"]
        .as_array()
        .ok_or("invalid relay messages")?;
    let agents: Vec<Value> = state["agents"]
        .as_object()
        .ok_or("invalid relay agents")?
        .iter()
        .take(max_rows)
        .map(|(id, a)| {
            json!({
                "agent_id":display_text(&json!(id)), "kind":display_text(&a["kind"]),
                "label":display_text(&a["label"]), "expires":a["expires"].as_u64().unwrap_or(0),
                "online":a["expires"].as_u64().unwrap_or(0)>now,
                "pending":messages.iter().filter(|m| m["to"]==*id && m["status"]=="queued").count()
            })
        })
        .collect();
    let receipts: Vec<Value> = messages
        .iter()
        .rev()
        .take(max_rows)
        .map(|m| {
            json!({
                "message_id":display_text(&m["message_id"]), "from":display_text(&m["from"]),
                "to":display_text(&m["to"]), "status":display_text(&m["status"]),
                "created":m["created"].as_u64(), "received":m["received"].as_u64()
            })
        })
        .collect();
    Ok(json!({"schema":"mios.agents.observation.v1", "ts":now,
        "agents":agents, "messages":receipts, "panes":[], "errors":[]}))
}

fn owned_path(path: &Path) -> bool {
    mios_service_core::socket::owned_path(path)
}

fn socket_candidates(root: &Path, human: &str, depth: usize) -> Vec<PathBuf> {
    mios_service_core::socket::socket_candidates(root, human, depth)
}

fn pane_metadata(socket: &Path) -> Result<Vec<Value>, String> {
    let mut child = Command::new("tmux").args(["-S"]).arg(socket)
        .args(["list-panes", "-a", "-F", "#{session_id}\t#{window_index}\t#{pane_id}\t#{pane_pid}\t#{pane_current_command}\t#{pane_dead}\t#{@mios-agent-kind}\t#{session_name}\t#{@mios-workspace-anchor-for}\t#{@mios-workspace-observer-for}\t#{@mios-workspace-slot}\t#{@mios-workspace-head}"])
        .stdout(Stdio::piped()).stderr(Stdio::null()).spawn().map_err(|e| e.to_string())?;
    let start = Instant::now();
    let status = loop {
        if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
            break status;
        }
        if start.elapsed() > Duration::from_millis(500) {
            let _ = child.kill();
            let _ = child.wait();
            return Err("tmux metadata probe timed out".into());
        }
        std::thread::sleep(Duration::from_millis(10));
    };
    if !status.success() {
        return Err("tmux socket unavailable".into());
    }
    let mut output = String::new();
    child
        .stdout
        .take()
        .ok_or("missing tmux stdout")?
        .take(65536)
        .read_to_string(&mut output)
        .map_err(|e| e.to_string())?;
    Ok(output
        .lines()
        .take(256)
        .filter_map(|line| {
            let fields: Vec<&str> = line.split('\t').collect();
            if fields.len() != 12 || fields[7] == "mios-anchor" || !fields[8].is_empty() || !fields[9].is_empty() {
                return None;
            }
            let role = if fields[2] == fields[11] { "Head".to_string() }
                else if !fields[10].is_empty() { format!("W{}", fields[10]) }
                else { "Shell".to_string() };
            Some(
                json!({"socket":display_text(&json!(socket.to_string_lossy())),
            "session":display_text(&json!(fields[0])), "session_name":display_text(&json!(fields[7])), "window":display_text(&json!(fields[1])),
            "pane":display_text(&json!(fields[2])), "pid":fields[3].parse::<u32>().ok(),
            "command":display_text(&json!(fields[4])), "dead":fields[5]=="1", "agent_kind":display_text(&json!(fields[6])), "role":role}),
            )
        })
        .collect())
}

fn observe(directory: &Path, request: &Value) -> Result<Value, Box<dyn std::error::Error>> {
    let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
    let path = directory.join("state.json");
    let exists = path.exists();
    if directory.exists() && !owned_path(directory) {
        return Err("unsafe agent state directory".into());
    }
    let state: Value = if exists {
        if !owned_path(&path) {
            return Err("unsafe agent state file".into());
        }
        if fs::metadata(&path)?.len() > 2 * 1024 * 1024 {
            return Err("agent state exceeds observer limit".into());
        }
        serde_json::from_slice(&fs::read(&path)?)?
    } else {
        json!({"agents":{},"messages":[]})
    };
    let mut result = observation(&state, request, now).map_err(std::io::Error::other)?;
    result["registry"] = json!(if exists { "present" } else { "missing" });
    let human = string(&request["observation"], "human_socket")?;
    identifier(human)?;
    if human.contains(':') {
        return Err("invalid human socket name".into());
    }
    let roots = request["observation"]["socket_roots"]
        .as_array()
        .ok_or("missing socket roots")?;
    let mut sockets = Vec::new();
    for root in roots.iter().take(8).filter_map(Value::as_str) {
        let root = Path::new(root);
        if root.is_absolute() {
            sockets.extend(socket_candidates(root, human, 0));
        }
    }
    sockets.sort();
    sockets.dedup();
    // A busy headless fleet must not hide the desktop the operator is viewing.
    sockets.sort_by_key(|p| {
        (
            p.file_name().and_then(|s| s.to_str()) != Some(human),
            p.clone(),
        )
    });
    sockets.truncate(16);
    let mut panes = Vec::new();
    let mut errors = Vec::new();
    for socket in sockets {
        match pane_metadata(&socket) {
            Ok(rows) => panes.extend(rows),
            Err(error) => errors.push(
                json!({"socket":display_text(&json!(socket.to_string_lossy())),"error":error}),
            ),
        }
    }
    panes.truncate(request["observation"]["max_rows"].as_u64().unwrap_or(32) as usize);
    result["panes"] = json!(panes);
    result["errors"] = json!(errors);
    Ok(result)
}

fn render_observation(snapshot: &Value, request: &Value) -> Result<String, String> {
    let color = |name: &str| -> Result<String, String> {
        let hex = string(&request["observation"]["colors"], name)?;
        if hex.len() != 7
            || !hex.starts_with('#')
            || !hex[1..].bytes().all(|b| b.is_ascii_hexdigit())
        {
            return Err("invalid observation color".into());
        }
        Ok(format!(
            "\x1b[38;2;{};{};{}m",
            u8::from_str_radix(&hex[1..3], 16).unwrap(),
            u8::from_str_radix(&hex[3..5], 16).unwrap(),
            u8::from_str_radix(&hex[5..7], 16).unwrap()
        ))
    };
    let size = Command::new("stty")
        .args(["-F", "/dev/tty", "size"])
        .stderr(Stdio::null())
        .output()
        .ok()
        .filter(|p| p.status.success())
        .and_then(|p| String::from_utf8(p.stdout).ok())
        .and_then(|s| {
            let v: Vec<usize> = s
                .split_whitespace()
                .filter_map(|n| n.parse().ok())
                .collect();
            if v.len() == 2 {
                Some((v[0], v[1]))
            } else {
                None
            }
        });
    let width = size.map(|s| s.1).unwrap_or(80).clamp(20, 240);
    let height = size.map(|s| s.0).unwrap_or(24).clamp(3, 160);
    let lines = observation_lines(snapshot, request, width, height)?;
    Ok(format!(
        "\x1b[0m\x1b[H\x1b[2J{}{}\x1b[0m",
        color("fg")?,
        lines.join("\n")
    ))
}

fn short_identity(id: &Value, label: &Value, kind: &Value) -> String {
    let id = display_text(id);
    let label = display_text(label);
    let kind = display_text(kind);
    let name = if !label.is_empty() && label != id && label.chars().count() <= 18 {
        label
    } else if !kind.is_empty() {
        kind
    } else {
        id.split(':')
            .next()
            .unwrap_or("agent")
            .chars()
            .take(12)
            .collect()
    };
    format!("{} #{}", name, &digest(&id)[..4])
}

fn observation_lines(
    snapshot: &Value,
    request: &Value,
    width: usize,
    height: usize,
) -> Result<Vec<String>, String> {
    let agents = snapshot["agents"]
        .as_array()
        .ok_or("invalid observed agents")?;
    let messages = snapshot["messages"]
        .as_array()
        .ok_or("invalid observed messages")?;
    let panes = snapshot["panes"]
        .as_array()
        .ok_or("invalid observed panes")?;
    let mut groups = vec![
        (
            format!(
                "Relay: {} online / {}",
                agents.iter().filter(|a| a["online"] == true).count(),
                agents.len()
            ),
            agents
                .iter()
                .map(|a| {
                    format!(
                        "{} {} q:{}",
                        if a["online"] == true { "+" } else { "-" },
                        short_identity(&a["agent_id"], &a["label"], &a["kind"]),
                        a["pending"]
                    )
                })
                .collect::<Vec<_>>(),
        ),
        (
            "Receipts: read != done".into(),
            messages
                .iter()
                .map(|m| {
                    format!(
                        "{} {} > {}",
                        if m["status"] == "received" {
                            "read"
                        } else {
                            "queued"
                        },
                        display_text(&m["from"])
                            .split(':')
                            .next()
                            .unwrap_or("agent"),
                        display_text(&m["to"]).split(':').next().unwrap_or("agent")
                    )
                })
                .collect(),
        ),
        (
            format!("Panes: {} (not registrations)", panes.len()),
            panes
                .iter()
                .map(|p| {
                    format!(
                        "{} {} {}{}",
                        display_text(&p["role"]),
                        display_text(&p["pane"]),
                        if p["command"] == "sleep" {
                            "empty".into()
                        } else if p["role"] == "Head" && p["command"] == "python3" {
                            "chooser".into()
                        } else if p["agent_kind"].as_str().is_some_and(|s| !s.is_empty()) {
                            display_text(&p["agent_kind"])
                        } else {
                            display_text(&p["command"])
                        },
                        if p["dead"] == true { " exited" } else { "" }
                    )
                })
                .collect(),
        ),
    ];
    let mut lines = vec![if snapshot["registry"] == "missing" {
        "MiOS Agents (no registry)".into()
    } else {
        "MiOS Agents".into()
    }];
    let refresh = request["observation"]["refresh_s"]
        .as_u64()
        .unwrap_or(2)
        .max(1);
    let tick = snapshot["ts"].as_u64().unwrap_or(0) / (refresh * 3);
    if height < 10 {
        let selected = tick as usize % groups.len();
        groups = vec![groups.remove(selected)];
    } else {
        lines.push("Ctrl-b o: next | z: zoom".into());
    }
    let quota = height
        .saturating_sub(lines.len() + groups.len() + 1)
        .checked_div(groups.len())
        .unwrap_or(0)
        .max(1);
    for (title, rows) in groups {
        let pages = rows.len().div_ceil(quota).max(1);
        let index = tick as usize % pages;
        lines.push(if pages > 1 {
            format!("{title} {}/{}", index + 1, pages)
        } else {
            title
        });
        lines.extend(rows.into_iter().skip(index * quota).take(quota));
    }
    for e in snapshot["errors"]
        .as_array()
        .ok_or("missing observation errors")?
    {
        lines.push(format!("ERROR {}", display_text(&e["error"])));
    }
    Ok(lines
        .into_iter()
        .take(height.saturating_sub(1).max(1))
        .map(|s| s.chars().take(width.saturating_sub(1).max(1)).collect())
        .collect())
}

fn identifier(value: &str) -> Result<(), String> {
    if value.is_empty()
        || value.len() > 160
        || !value
            .bytes()
            .all(|c| c.is_ascii_alphanumeric() || b"-_.:".contains(&c))
    {
        return Err("invalid agent or message identifier".into());
    }
    Ok(())
}
fn string<'a>(request: &'a Value, field: &str) -> Result<&'a str, String> {
    request[field]
        .as_str()
        .ok_or_else(|| format!("missing {field}"))
}
fn digest(value: &str) -> String {
    format!("{:x}", Sha256::digest(value.as_bytes()))
}

fn format_iso(secs: u64) -> String {
    let s = secs % 60;
    let m = (secs / 60) % 60;
    let h = (secs / 3600) % 24;
    let days = secs / 86400;

    let z = days + 719468;
    let era = z / 146097;
    let doe = z - era * 146097;
    let yoe = (doe - doe / 1460 + doe / 36524 - doe / 146096) / 365;
    let y = yoe + era * 400;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let d = doy - (153 * mp + 2) / 5 + 1;
    let m_month = if mp < 10 { mp + 3 } else { mp - 9 };
    let y_final = if m_month <= 2 { y + 1 } else { y };

    format!(
        "{:04}-{:02}-{:02}T{:02}:{:02}:{:02}Z",
        y_final, m_month, d, h, m, s
    )
}

fn parse_iso(s: &str) -> Option<u64> {
    let s = s.trim();
    if s.len() < 19 {
        return None;
    }
    let year: u64 = s[0..4].parse().ok()?;
    let month: u64 = s[5..7].parse().ok()?;
    let day: u64 = s[8..10].parse().ok()?;
    let hour: u64 = s[11..13].parse().ok()?;
    let min: u64 = s[14..16].parse().ok()?;
    let sec: u64 = s[17..19].parse().ok()?;
    if !(1..=12).contains(&month) || !(1..=31).contains(&day) || hour > 23 || min > 59 || sec > 60 {
        return None;
    }
    let y = if month <= 2 {
        year.checked_sub(1)?
    } else {
        year
    };
    let era = y / 400;
    let yoe = y - era * 400;
    let m = if month > 2 { month - 3 } else { month + 9 };
    let doy = (153 * m + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = (era * 146097 + doe).checked_sub(719468)?;
    Some(days * 86400 + hour * 3600 + min * 60 + sec)
}

fn resolve_host_path(path_str: &str) -> PathBuf {
    #[cfg(unix)]
    {
        let trimmed = path_str.trim();
        if trimmed.len() >= 3
            && trimmed.as_bytes()[1] == b':'
            && (trimmed.as_bytes()[2] == b'\\' || trimmed.as_bytes()[2] == b'/')
        {
            let drive = (trimmed.as_bytes()[0] as char).to_ascii_lowercase();
            let rest = trimmed[3..].replace('\\', "/");
            return PathBuf::from(format!("/mnt/{drive}/{rest}"));
        }
    }
    PathBuf::from(path_str)
}

fn save_json_file(path: &Path, value: &Value) -> Result<(), String> {
    let dir = path.parent().ok_or("missing parent directory")?;
    fs::create_dir_all(dir).map_err(|e| e.to_string())?;
    let mut temp = tempfile::NamedTempFile::new_in(dir).map_err(|e| e.to_string())?;
    serde_json::to_writer_pretty(temp.as_file_mut(), value).map_err(|e| e.to_string())?;
    writeln!(temp.as_file_mut()).map_err(|e| e.to_string())?;
    temp.as_file_mut().sync_all().map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(temp.path(), fs::Permissions::from_mode(0o600));
    }
    temp.persist(path).map_err(|e| e.to_string())?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        let _ = fs::set_permissions(path, fs::Permissions::from_mode(0o600));
    }
    Ok(())
}

fn journal(dir: &Path, event_type: &str, fields: Value, now: u64) {
    let journal_path = dir.join("journal.jsonl");
    let mut rec = json!({
        "object": "agent.event",
        "type": event_type,
        "created_at": format_iso(now),
    });
    if let Value::Object(map) = fields {
        for (k, v) in map {
            rec[k] = v;
        }
    }
    if let Ok(mut file) = OpenOptions::new()
        .create(true)
        .append(true)
        .open(journal_path)
    {
        if let Ok(line) = serde_json::to_string(&rec) {
            let _ = writeln!(file, "{line}");
        }
    }
}

fn handle_lease(
    dir: &Path,
    state: &mut Value,
    request: &Value,
    subaction: &str,
    now: u64,
) -> Result<Value, String> {
    let lease_path = dir.join("lease.json");
    let lease: Option<Value> = if lease_path.exists() {
        fs::read_to_string(&lease_path)
            .ok()
            .and_then(|content| serde_json::from_str(&content).ok())
    } else if state
        .get("coordinator_lease")
        .is_some_and(|l| l.is_object())
    {
        Some(state["coordinator_lease"].clone())
    } else {
        None
    };

    match subaction {
        "get" | "show" => {
            let l =
                lease.ok_or("lease.json is missing; ask the operator, do not invent a lease")?;
            Ok(l)
        }
        "renew" => {
            let mut l = lease.ok_or("refused: lease.json is missing")?;
            let caller = request["me"]
                .as_str()
                .or_else(|| request["holder"].as_str())
                .or_else(|| request["agent_id"].as_str())
                .or_else(|| request["holder_identity"].as_str())
                .ok_or("missing caller identity ('me' or 'agent_id')")?;
            let current_holder = l["holder_identity"].as_str().unwrap_or("");
            if current_holder != caller {
                return Err(format!(
                    "refused: {current_holder} holds the lease, not {caller}"
                ));
            }
            let t = format_iso(now);
            l["renew_time"] = json!(t);
            l["state"] = json!("held");

            save_json_file(&lease_path, &l)?;
            state["coordinator_lease"] = l.clone();

            let status_path = dir.join(format!("{caller}.status.json"));
            let mut st: Value = if status_path.exists() {
                fs::read_to_string(&status_path)
                    .ok()
                    .and_then(|c| serde_json::from_str(&c).ok())
                    .unwrap_or_else(|| json!({"object":"agent.status","agent_id":caller}))
            } else {
                json!({"object":"agent.status","agent_id":caller})
            };
            st["updated_at"] = json!(t);
            if let Some(s) = request["state"].as_str() {
                st["state"] = json!(s);
            }
            if let Some(n) = request["note"].as_str() {
                st["note"] = json!(n);
            }
            if request.get("five_hour").is_some() || request.get("weekly").is_some() {
                if !st["usage"].is_object() {
                    st["usage"] = json!({});
                }
                if let Some(fh) = request["five_hour"].as_i64() {
                    st["usage"]["five_hour"] = json!({
                        "percent_used": fh,
                        "resets_at": request["five_hour_resets"].as_str().unwrap_or("")
                    });
                }
                if let Some(wk) = request["weekly"].as_i64() {
                    st["usage"]["weekly"] = json!({
                        "percent_used": wk,
                        "resets_at": request["weekly_resets"].as_str().unwrap_or("")
                    });
                }
                st["usage"]["read_at"] = json!(t);
            }
            save_json_file(&status_path, &st)?;

            journal(
                dir,
                "lease.renewed",
                json!({"agent_id": caller, "state": st.get("state"), "note": request.get("note")}),
                now,
            );
            Ok(json!({"ok": true, "renewed": t, "lease": l}))
        }
        "release" => {
            let mut l = lease.ok_or("refused: lease.json is missing")?;
            let caller = request["me"]
                .as_str()
                .or_else(|| request["holder"].as_str())
                .or_else(|| request["agent_id"].as_str())
                .or_else(|| request["holder_identity"].as_str())
                .ok_or("missing caller identity ('me' or 'agent_id')")?;
            let current_holder = l["holder_identity"].as_str().unwrap_or("");
            if current_holder != caller {
                return Err(format!(
                    "refused: {current_holder} holds the lease, not {caller}"
                ));
            }
            let to = request["to"]
                .as_str()
                .or_else(|| request["handoff_to"].as_str());
            let t = format_iso(now);
            l["state"] = json!("released");
            l["handoff_to"] = if let Some(target) = to {
                json!(target)
            } else {
                Value::Null
            };
            l["renew_time"] = json!(t);

            save_json_file(&lease_path, &l)?;
            state["coordinator_lease"] = l.clone();

            journal(
                dir,
                "lease.released",
                json!({"agent_id": caller, "handoff_to": to, "note": request.get("note")}),
                now,
            );
            Ok(json!({"ok": true, "released_to": to, "lease": l}))
        }
        "acquire" => {
            let mut l = lease
                .ok_or("refused: lease.json is missing; ask the operator, do not invent a lease")?;
            let caller = request["me"]
                .as_str()
                .or_else(|| request["holder"].as_str())
                .or_else(|| request["agent_id"].as_str())
                .or_else(|| request["holder_identity"].as_str())
                .ok_or("missing caller identity ('me' or 'agent_id')")?;
            let verdict = request["verdict"]
                .as_str()
                .ok_or("missing takeover verdict")?;

            if l["holder_identity"].as_str() == Some(caller) {
                return Err("refused: already held by you".into());
            }

            let is_successor = l["successors"]
                .as_array()
                .map(|arr| arr.iter().any(|v| v.as_str() == Some(caller)))
                .unwrap_or(false);
            let is_preferred = l["preferred_holder"].as_str() == Some(caller);
            if !is_successor && !is_preferred {
                return Err(format!("refused: {caller} is not a successor"));
            }

            const TAKEOVER_VERDICTS: &[&str] = &["released", "exhausted", "silent"];
            if !TAKEOVER_VERDICTS.contains(&verdict) {
                return Err(
                    "refused: verdict must be one of [\"exhausted\", \"released\", \"silent\"] from probe.ps1".to_string(),
                );
            }

            if verdict == "released" && l["state"].as_str() != Some("released") {
                return Err("refused: acquire on valid unreleased lease".into());
            }

            if l["state"].as_str() == Some("released") {
                if let Some(handoff_to) = l["handoff_to"].as_str() {
                    if !handoff_to.is_empty() && handoff_to != caller {
                        return Err(format!("refused: released to {handoff_to}"));
                    }
                }
            }

            let prev = l["holder_identity"].as_str().unwrap_or("").to_string();
            let t = format_iso(now);
            l["holder_identity"] = json!(caller);
            l["state"] = json!("held");
            l["acquire_time"] = json!(t);
            l["renew_time"] = json!(t);
            let transitions = l["lease_transitions"].as_u64().unwrap_or(0) + 1;
            l["lease_transitions"] = json!(transitions);
            l["handoff_to"] = Value::Null;

            let mut new_successors = vec![json!(prev)];
            if let Some(arr) = l["successors"].as_array() {
                for s in arr {
                    if let Some(s_str) = s.as_str() {
                        if s_str != prev && s_str != caller {
                            new_successors.push(json!(s_str));
                        }
                    }
                }
            }
            l["successors"] = json!(new_successors);

            save_json_file(&lease_path, &l)?;
            state["coordinator_lease"] = l.clone();

            journal(
                dir,
                "lease.acquired",
                json!({
                    "agent_id": caller,
                    "previous_holder": prev,
                    "verdict": verdict,
                    "note": request.get("note")
                }),
                now,
            );
            Ok(json!({
                "ok": true,
                "acquired": caller,
                "from": prev,
                "verdict": verdict,
                "transitions": transitions,
                "lease": l
            }))
        }
        _ => Err(format!("unknown lease subaction: {subaction}")),
    }
}

fn handle_status(
    dir: &Path,
    _state: &mut Value,
    request: &Value,
    now: u64,
) -> Result<Value, String> {
    let agent_id = request["agent_id"]
        .as_str()
        .or_else(|| request["me"].as_str())
        .ok_or("missing agent identity ('agent_id' or 'me')")?;
    let status_path = dir.join(format!("{agent_id}.status.json"));

    let is_get = request["subaction"].as_str() == Some("get")
        || (request.get("state").is_none()
            && request.get("note").is_none()
            && request.get("transcript").is_none()
            && request.get("usage").is_none()
            && request.get("role").is_none());

    if is_get {
        if status_path.exists() {
            let content = fs::read_to_string(&status_path).map_err(|e| e.to_string())?;
            let st: Value = serde_json::from_str(&content).map_err(|e| e.to_string())?;
            return Ok(st);
        } else {
            return Ok(json!({
                "object": "agent.status",
                "agent_id": agent_id,
                "state": "unknown"
            }));
        }
    }

    let mut st: Value = if status_path.exists() {
        fs::read_to_string(&status_path)
            .ok()
            .and_then(|c| serde_json::from_str(&c).ok())
            .unwrap_or_else(|| json!({"object":"agent.status","agent_id":agent_id}))
    } else {
        json!({"object":"agent.status","agent_id":agent_id})
    };

    let t = format_iso(now);
    st["updated_at"] = json!(t);
    if let Some(v) = request["state"].as_str() {
        st["state"] = json!(v);
    }
    if let Some(v) = request["note"].as_str() {
        st["note"] = json!(v);
    }
    if let Some(v) = request["role"].as_str() {
        st["role"] = json!(v);
    }
    if let Some(v) = request["kind"].as_str() {
        st["kind"] = json!(v);
    }
    if let Some(v) = request["harness"].as_str() {
        st["harness"] = json!(v);
    }
    if let Some(v) = request["transcript"].as_str() {
        st["transcript"] = json!(v);
    }
    if let Some(v) = request["activity_dirs"].as_array() {
        st["activity_dirs"] = json!(v);
    }
    if let Some(v) = request["usage"].as_object() {
        st["usage"] = json!(v);
    }
    if let Some(v) = request["handoff"].as_str() {
        st["handoff"] = json!(v);
    }
    if let Some(v) = request["relay_id"].as_str() {
        st["relay_id"] = json!(v);
    }

    save_json_file(&status_path, &st)?;
    Ok(st)
}

fn handle_probe(dir: &Path, request: &Value, now: u64) -> Result<Value, String> {
    let lease_path = dir.join("lease.json");
    let me = request["me"]
        .as_str()
        .or_else(|| request["agent_id"].as_str())
        .unwrap_or("antigravity");

    let tail_lines = request["tail_lines"]
        .as_u64()
        .or_else(|| request["config"]["succession"]["tail_lines"].as_u64())
        .unwrap_or(80) as usize;
    let min_outage_s = request["min_outage_s"]
        .as_i64()
        .or_else(|| request["config"]["succession"]["min_outage_s"].as_i64())
        .unwrap_or(900);

    if !lease_path.exists() {
        return Ok(json!({
            "object": "agent.probe",
            "probed_at": format_iso(now),
            "me": me,
            "holder": Value::Null,
            "verdict": "no_lease",
            "take_over": false,
            "reason": "lease.json is missing; ask the operator, do not invent a lease",
            "next_probe_s": 600
        }));
    }

    let lease_content = fs::read_to_string(&lease_path).map_err(|e| e.to_string())?;
    let lease: Value = serde_json::from_str(&lease_content).map_err(|e| e.to_string())?;

    let holder = request["holder"]
        .as_str()
        .or_else(|| lease["holder_identity"].as_str())
        .unwrap_or("")
        .to_string();
    let duration = request["lease_duration_s"]
        .as_u64()
        .or_else(|| request["config"]["succession"]["lease_duration_s"].as_u64())
        .or_else(|| lease["lease_duration_seconds"].as_u64())
        .unwrap_or(5400);

    let may_take = lease["successors"]
        .as_array()
        .map(|arr| arr.iter().any(|v| v.as_str() == Some(me)))
        .unwrap_or(false);

    if holder == me {
        return Ok(json!({
            "object": "agent.probe",
            "probed_at": format_iso(now),
            "me": me,
            "holder": holder,
            "verdict": "held_by_me",
            "take_over": false,
            "reason": "you already hold the coordinator lease",
            "next_probe_s": 600
        }));
    }

    if lease["state"].as_str() == Some("released") {
        let to = lease["handoff_to"].as_str().filter(|s| !s.is_empty());
        let ok = may_take && (to.is_none() || to == Some(me));
        let reason = format!(
            "holder released the lease{}",
            to.map(|t| format!(" to {t}")).unwrap_or_default()
        );
        return Ok(json!({
            "object": "agent.probe",
            "probed_at": format_iso(now),
            "me": me,
            "holder": holder,
            "verdict": "released",
            "take_over": ok,
            "reason": reason,
            "next_probe_s": 60
        }));
    }

    let status_path = dir.join(format!("{holder}.status.json"));
    let status: Option<Value> = if status_path.exists() {
        fs::read_to_string(&status_path)
            .ok()
            .and_then(|c| serde_json::from_str(&c).ok())
    } else {
        None
    };

    let transcript_path_str = status
        .as_ref()
        .and_then(|s| s["transcript"].as_str())
        .map(str::to_string);

    let mut paths_to_check: Vec<PathBuf> = Vec::new();
    if let Some(ref t_str) = transcript_path_str {
        let p = resolve_host_path(t_str);
        if p.exists() {
            paths_to_check.push(p);
        }
    }
    if let Some(ref st) = status {
        if let Some(dirs) = st["activity_dirs"].as_array() {
            for d in dirs {
                if let Some(d_str) = d.as_str() {
                    let dir_p = resolve_host_path(d_str);
                    if dir_p.is_dir() {
                        if let Ok(entries) = fs::read_dir(dir_p) {
                            for entry in entries.flatten() {
                                let ep = entry.path();
                                if ep.is_file()
                                    && ep.extension().and_then(|x| x.to_str()) == Some("jsonl")
                                {
                                    paths_to_check.push(ep);
                                }
                            }
                        }
                    }
                }
            }
        }
    }
    if status_path.exists() {
        paths_to_check.push(status_path.clone());
    }

    let mut newest_secs: Option<u64> = None;
    for p in &paths_to_check {
        if let Ok(meta) = fs::metadata(p) {
            if let Ok(mtime) = meta.modified() {
                if let Ok(dur) = mtime.duration_since(UNIX_EPOCH) {
                    let secs = dur.as_secs();
                    if newest_secs.is_none() || Some(secs) > newest_secs {
                        newest_secs = Some(secs);
                    }
                }
            }
        }
    }

    let idle: Option<i64> = newest_secs.map(|s| now.saturating_sub(s) as i64);

    let mut blocked: Option<Value> = None;
    if let Some(ref t_str) = transcript_path_str {
        let tp = resolve_host_path(t_str);
        if tp.is_file() {
            if let Ok(content) = fs::read_to_string(&tp) {
                let lines: Vec<&str> = content.lines().collect();
                let tail_start = lines.len().saturating_sub(tail_lines);
                for line in lines[tail_start..].iter().rev() {
                    let Ok(e) = serde_json::from_str::<Value>(line.trim()) else {
                        continue;
                    };
                    if e["type"].as_str() != Some("assistant")
                        || e["isSidechain"].as_bool() == Some(true)
                    {
                        continue;
                    }
                    if e["isApiErrorMessage"].as_bool() == Some(true) {
                        let q = &e["quotaLimits"];
                        let resets_at = q["resetsAt"].as_i64().map(|ts| format_iso(ts as u64));
                        let text = e["message"]["content"]
                            .as_array()
                            .and_then(|arr| arr.iter().find(|item| item["type"] == "text"))
                            .and_then(|item| item["text"].as_str())
                            .map(|s| s.to_string());

                        blocked = Some(json!({
                            "error": e.get("error").cloned().unwrap_or(Value::Null),
                            "api_error": e.get("apiError").cloned().unwrap_or(Value::Null),
                            "http_status": e.get("apiErrorStatus").cloned().unwrap_or(Value::Null),
                            "limit_type": q.get("rateLimitType").cloned().unwrap_or(Value::Null),
                            "overage": q.get("overageDisabledReason").cloned().unwrap_or(Value::Null),
                            "resets_at": resets_at.map(Value::String).unwrap_or(Value::Null),
                            "at": e.get("timestamp").cloned().unwrap_or(Value::Null),
                            "text": text.map(Value::String).unwrap_or(Value::Null),
                        }));
                    }
                    break;
                }
            }
        }
    }

    if let Some(ref b) = blocked {
        let wait = b["resets_at"]
            .as_str()
            .and_then(parse_iso)
            .map(|resets| resets as i64 - now as i64);
        if let Some(wait_s) = wait {
            if wait_s > 0 && wait_s <= min_outage_s {
                let next_probe = std::cmp::max(60, (wait_s + 60) as u64);
                return Ok(json!({
                    "object": "agent.probe",
                    "probed_at": format_iso(now),
                    "me": me,
                    "holder": holder,
                    "verdict": "exhausted_briefly",
                    "take_over": false,
                    "reason": format!("holder is rate-limited but resets in {wait_s} s; keep working as a worker"),
                    "next_probe_s": next_probe,
                    "blocked": b,
                    "idle_s": idle
                }));
            }
        }
        let api_err = b["api_error"].as_str().unwrap_or("");
        let limit_type = b["limit_type"].as_str().unwrap_or("");
        return Ok(json!({
            "object": "agent.probe",
            "probed_at": format_iso(now),
            "me": me,
            "holder": holder,
            "verdict": "exhausted",
            "take_over": may_take,
            "reason": format!("holder's latest turn is an API error ({api_err}; {limit_type})"),
            "next_probe_s": 600,
            "blocked": b,
            "idle_s": idle
        }));
    }

    let renew_secs = lease["renew_time"].as_str().and_then(parse_iso);
    let lease_age = renew_secs.map(|r| now.saturating_sub(r) as i64);

    if let Some(idle_s) = idle {
        if idle_s as u64 > duration && (lease_age.is_none() || lease_age.unwrap() as u64 > duration)
        {
            let lease_age_str = lease_age.map(|a| a.to_string()).unwrap_or_default();
            return Ok(json!({
                "object": "agent.probe",
                "probed_at": format_iso(now),
                "me": me,
                "holder": holder,
                "verdict": "silent",
                "take_over": may_take,
                "reason": format!("no holder activity for {idle_s} s and the lease is {lease_age_str} s old (limit {duration} s)"),
                "next_probe_s": 600,
                "idle_s": idle_s,
                "lease_age_s": lease_age
            }));
        }
    }

    let state = status.as_ref().and_then(|s| s["state"].as_str());
    let v = if state == Some("waiting") {
        "waiting"
    } else {
        "alive"
    };
    let idle_str = idle.map(|i| i.to_string()).unwrap_or_default();
    let lease_age_str = lease_age.map(|a| a.to_string()).unwrap_or_default();
    let note = status.as_ref().and_then(|s| s["note"].as_str());

    Ok(json!({
        "object": "agent.probe",
        "probed_at": format_iso(now),
        "me": me,
        "holder": holder,
        "verdict": v,
        "take_over": false,
        "reason": format!("holder is active (idle {idle_str} s, lease age {lease_age_str} s)"),
        "next_probe_s": 600,
        "idle_s": idle,
        "lease_age_s": lease_age,
        "holder_state": state,
        "holder_note": note
    }))
}

fn dispatch(state: &mut Value, request: &Value, now: u64) -> Result<Value, String> {
    let action = string(request, "action")?;
    let dir_path = request["dir"]
        .as_str()
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("."));

    if action == "lease"
        || action == "acquire"
        || action == "renew"
        || action == "release"
        || action == "get_lease"
    {
        let subaction = if action == "lease" {
            request["subaction"]
                .as_str()
                .or_else(|| request["lease_action"].as_str())
                .or_else(|| request["op"].as_str())
                .unwrap_or("get")
        } else if action == "get_lease" {
            "get"
        } else {
            action
        };
        return handle_lease(&dir_path, state, request, subaction, now);
    }
    if action == "status" {
        return handle_status(&dir_path, state, request, now);
    }
    if action == "probe" {
        return handle_probe(&dir_path, request, now);
    }

    let cfg = &request["config"];
    let limit = |key: &str| {
        cfg[key]
            .as_u64()
            .filter(|n| *n > 0)
            .ok_or_else(|| format!("invalid [mcp.agents].{key}"))
    };
    let ttl = limit("lease_s")?;
    let max_agents = limit("max_agents")?;
    let max_pending = limit("max_pending")?;
    let max_bytes = limit("max_message_bytes")?;
    let max_receipts = limit("max_receipts")?;
    // Existing stdio clients may retain the previous SSOT schema until their
    // connection restarts. Preserve its conservative send policy while still
    // allowing the original token to resume an already queued inbox.
    let retention = if cfg.get("mailbox_retention_s").is_some() {
        limit("mailbox_retention_s")?
    } else {
        ttl
    };
    let queue_offline = match cfg.get("queue_offline") {
        None => false,
        Some(value) => value
            .as_bool()
            .ok_or("invalid [mcp.agents].queue_offline")?,
    };

    if action == "list" {
        let agents:Vec<Value>=state["agents"].as_object().unwrap().iter().map(|(id,a)|json!({
            "agent_id":id,"kind":a["kind"],"label":a["label"],"online":a["expires"].as_u64().unwrap_or(0)>now,
            "pending":state["messages"].as_array().unwrap().iter().filter(|m|m["to"]==*id && m["status"]=="queued").count()
        })).collect();
        return Ok(json!({"agents":agents}));
    }
    let id = string(request, "agent_id")?;
    identifier(id)?;
    let token = string(request, "token")?;
    if token.len() < 32 || token.len() > 256 {
        return Err("invalid agent lease token".into());
    }
    if action == "register" {
        // Presence expiry is not permission to steal a mailbox. Preserve both
        // ends of pending messages; retire only unreferenced dormant identities.
        let protected: std::collections::HashSet<String> = state["messages"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|m| m["status"] == "queued")
            .flat_map(|m| [m["from"].as_str(), m["to"].as_str()])
            .flatten()
            .map(str::to_owned)
            .collect();
        let retired: Vec<String> = state["agents"]
            .as_object()
            .unwrap()
            .iter()
            .filter(|(other, a)| {
                other.as_str() != id
                    && !protected.contains(other.as_str())
                    && a["expires"].as_u64().unwrap_or(0).saturating_add(retention) <= now
            })
            .map(|(other, _)| other.clone())
            .collect();
        for other in &retired {
            state["agents"].as_object_mut().unwrap().remove(other);
        }
        state["messages"].as_array_mut().unwrap().retain(|m| {
            !retired
                .iter()
                .any(|other| m["from"] == *other || m["to"] == *other)
        });
        let agents = state["agents"].as_object_mut().unwrap();
        if let Some(prior) = agents.get(id) {
            if prior["token_hash"] != digest(token) {
                return Err("agent identifier already registered by another lease".into());
            }
        } else if agents.len() as u64 >= max_agents {
            return Err("agent registry is full".into());
        }
        let kind = string(request, "kind")?;
        identifier(kind)?;
        let label = request["label"].as_str().unwrap_or(id);
        if label.len() > max_bytes as usize {
            return Err("agent label is too long".into());
        }
        agents.insert(
            id.into(),
            json!({"kind":kind,"label":label,"token_hash":digest(token),"expires":now+ttl}),
        );
        return Ok(json!({"agent_id":id,"expires":now+ttl}));
    }
    let agent = &state["agents"][id];
    if agent["token_hash"].as_str() != Some(digest(token).as_str()) {
        return Err("unknown agent or invalid lease".into());
    }
    if agent["closed"] == true {
        return Err("agent session closed; register again with its original token".into());
    }
    state["agents"][id]["expires"] = json!(now + ttl);
    match action {
        "unregister" => {
            state["agents"][id]["expires"] = json!(now);
            state["agents"][id]["closed"] = json!(true);
            Ok(json!({"agent_id":id,"closed":true}))
        }
        "send" => {
            let to = string(request, "to")?;
            identifier(to)?;
            let body = string(request, "message")?;
            if body.is_empty() || body.len() as u64 > max_bytes {
                return Err("empty or oversized agent message".into());
            }
            let message_id = string(request, "message_id")?;
            identifier(message_id)?;
            let recipient_online = state["agents"][to]["expires"].as_u64().unwrap_or(0) > now;
            let recipient_registered =
                state["agents"][to].is_object() && state["agents"][to]["closed"] != true;
            let messages = state["messages"].as_array_mut().unwrap();
            if let Some(prior) = messages.iter().find(|m| m["message_id"] == message_id) {
                if prior["from"] != id || prior["to"] != to || prior["message"] != body {
                    return Err("message identifier reused with different content".into());
                }
                return Ok(
                    json!({"message_id":message_id,"to":to,"status":prior["status"],"duplicate":true}),
                );
            }
            if !recipient_registered || (!recipient_online && !queue_offline) {
                return Err("recipient is not registered or is offline".into());
            }
            if messages.iter().filter(|m| m["status"] == "queued").count() as u64 >= max_pending {
                return Err("agent message queue is full".into());
            }
            messages.push(json!({"message_id":message_id,"from":id,"to":to,"message":body,"created":now,"status":"queued"}));
            while messages
                .iter()
                .filter(|m| m["status"] == "received")
                .count() as u64
                > max_receipts
            {
                if let Some(index) = messages.iter().position(|m| m["status"] == "received") {
                    messages.remove(index);
                }
            }
            Ok(
                json!({"message_id":message_id,"to":to,"status":"queued","recipient_online":recipient_online}),
            )
        }
        "receive" => {
            let messages: Vec<&Value> = state["messages"]
                .as_array()
                .unwrap()
                .iter()
                .filter(|m| m["to"] == id && m["status"] == "queued")
                .collect();
            Ok(json!({"agent_id":id,"messages":messages}))
        }
        "ack" => {
            let message_id = string(request, "message_id")?;
            let row = state["messages"]
                .as_array_mut()
                .unwrap()
                .iter_mut()
                .find(|m| m["to"] == id && m["message_id"] == message_id)
                .ok_or("message does not belong to this recipient")?;
            row["status"] = json!("received");
            row["received"] = json!(now);
            Ok(json!({"message_id":message_id,"status":"received"}))
        }
        _ => Err("unknown agent relay action".into()),
    }
}
fn run(directory: &Path, request: &Value) -> Result<Value, Box<dyn std::error::Error>> {
    if request["action"] == "observe" {
        return observe(directory, request);
    }
    if request["action"] == "probe" {
        let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
        let dir = request["dir"].as_str().map(Path::new).unwrap_or(directory);
        return handle_probe(dir, request, now).map_err(|e| std::io::Error::other(e).into());
    }
    if directory.is_symlink() {
        return Err("agent state directory is a symlink".into());
    }
    fs::create_dir_all(directory)?;
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        if fs::metadata(directory)?.uid() != fs::metadata("/proc/self")?.uid() {
            return Err("agent state belongs to another user".into());
        }
        fs::set_permissions(directory, fs::Permissions::from_mode(0o700))?;
    }
    let lock_path = directory.join("lock");
    if lock_path.is_symlink() {
        return Err("agent lock is a symlink".into());
    }
    let lock = OpenOptions::new()
        .create(true)
        .truncate(false)
        .read(true)
        .write(true)
        .open(lock_path)?;
    lock.lock()?;
    let path = directory.join("state.json");
    if path.is_symlink() {
        return Err("agent state is a symlink".into());
    }
    let mut state: Value = if path.exists() {
        serde_json::from_slice(&fs::read(&path)?)?
    } else {
        json!({"agents":{},"messages":[]})
    };
    if !state["agents"].is_object() || !state["messages"].is_array() {
        return Err("invalid agent relay state".into());
    }
    let now = SystemTime::now().duration_since(UNIX_EPOCH)?.as_secs();
    let mut req = request.clone();
    if req.get("dir").is_none() {
        req["dir"] = json!(directory.to_string_lossy());
    }
    let result = dispatch(&mut state, &req, now).map_err(std::io::Error::other)?;
    if request["action"] != "list" && request["action"] != "probe" {
        let mut temporary = tempfile::NamedTempFile::new_in(directory)?;
        serde_json::to_writer(temporary.as_file_mut(), &state)?;
        temporary.as_file_mut().sync_all()?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(temporary.path(), fs::Permissions::from_mode(0o600))?;
        }
        temporary.persist(&path)?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(&path, fs::Permissions::from_mode(0o600))?;
        }
    }
    Ok(result)
}
// The workspace owns blank reservations explicitly. MCP may claim those panes;
// it must never infer ownership from an idle shell in the operator's window.
fn workspace_tmux(socket: &str, args: &[&str]) -> Result<String, String> {
    let output = Command::new("tmux")
        .args(["-S", socket])
        .args(args)
        .output()
        .map_err(|e| e.to_string())?;
    if !output.status.success() {
        return Err(format!(
            "tmux {}: {}",
            args.first().unwrap_or(&"command"),
            String::from_utf8_lossy(&output.stderr).trim()
        ));
    }
    Ok(String::from_utf8_lossy(&output.stdout)
        .trim_end_matches('\n')
        .to_string())
}

fn workspace_lock(path: &Path, name: &str) -> Result<fs::File, String> {
    mios_service_core::process::workspace_lock(path, name).map_err(|e| e.to_string())
}

fn workspace_number(config: &Value, name: &str, lower: u64, upper: u64) -> Result<usize, String> {
    config[name]
        .as_u64()
        .filter(|n| (lower..=upper).contains(n))
        .map(|n| n as usize)
        .ok_or_else(|| format!("invalid [mcp.tmux.workspace].{name}"))
}

#[derive(Clone, Copy, Debug)]
struct WorkspaceRect {
    w: usize,
    h: usize,
    x: usize,
    y: usize,
}
impl WorkspaceRect {
    fn prefix(self) -> String {
        format!("{}x{},{},{}", self.w, self.h, self.x, self.y)
    }
    fn leaf(self, pane: &str) -> Result<String, String> {
        let id = pane
            .strip_prefix('%')
            .filter(|s| !s.is_empty() && s.bytes().all(|c| c.is_ascii_digit()))
            .ok_or("invalid workspace pane ID")?;
        Ok(format!("{},{id}", self.prefix()))
    }
    fn branch(self, horizontal: bool, children: &[String]) -> String {
        let (open, close) = if horizontal { ('{', '}') } else { ('[', ']') };
        format!("{}{open}{}{close}", self.prefix(), children.join(","))
    }
}

fn workspace_grid(rect: WorkspaceRect, panes: &[String], columns: usize) -> Result<String, String> {
    if panes.is_empty() {
        return Err("workspace has no worker panes".into());
    }
    let rows = panes.len().div_ceil(columns);
    if rect.w < columns * 2 - 1 || rect.h < rows * 2 - 1 {
        return Err("workspace terminal is too small".into());
    }
    let mut cells = Vec::new();
    let mut y = rect.y;
    for (row, group) in panes.chunks(columns).enumerate() {
        let h = if row + 1 == rows {
            rect.y + rect.h - y
        } else {
            (rect.h - rows + 1) / rows
        };
        let mut x = rect.x;
        let mut leaves = Vec::new();
        for (col, pane) in group.iter().enumerate() {
            let w = if col + 1 == group.len() {
                rect.x + rect.w - x
            } else {
                (rect.w - group.len() + 1) / group.len()
            };
            leaves.push(WorkspaceRect { w, h, x, y }.leaf(pane)?);
            x += w + 1;
        }
        let row_rect = WorkspaceRect { h, y, ..rect };
        cells.push(if leaves.len() == 1 {
            leaves.remove(0)
        } else {
            row_rect.branch(true, &leaves)
        });
        y += h + 1;
    }
    Ok(if cells.len() == 1 {
        cells.remove(0)
    } else {
        rect.branch(false, &cells)
    })
}

fn workspace_compact(config: &Value, w: usize, h: usize) -> Result<bool, String> {
    Ok(
        w < workspace_number(config, "desktop_min_columns", 20, 1000)?
            || h < workspace_number(config, "desktop_min_rows", 12, 200)?
            || w * 100 < h * workspace_number(config, "portrait_ratio_percent", 50, 400)?,
    )
}

fn workspace_layout(
    config: &Value,
    w: usize,
    h: usize,
    head: &str,
    workers: &[String],
    observer: Option<&str>,
) -> Result<String, String> {
    let rect = WorkspaceRect { w, h, x: 0, y: 0 };
    if w < 12 || h < 8 {
        return Err("workspace terminal is too small".into());
    }
    let layout = if let Some(observer) = observer {
        if w * 100 >= h * workspace_number(config, "portrait_ratio_percent", 50, 400)? {
            let head_pct = workspace_number(config, "head_percent", 20, 70)?;
            let head_w = (w * head_pct / 100).clamp(3, w.saturating_sub(24).max(3));
            let observer_w = w - head_w - 1;
            rect.branch(
                true,
                &[
                    WorkspaceRect { w: head_w, ..rect }.leaf(head)?,
                    WorkspaceRect {
                        w: observer_w,
                        x: head_w + 1,
                        ..rect
                    }
                    .leaf(observer)?,
                ],
            )
        } else {
            let minimum = workspace_number(config, "minimum_head_rows", 3, 40)?.min(h - 4);
            let maximum = h
                .checked_sub(minimum + 1)
                .filter(|n| *n >= 3)
                .ok_or("portrait terminal is too short")?;
            // One split: the monitor takes whatever the head does not.
            let observer_pct = 100 - workspace_number(config, "head_percent", 20, 70)?;
            let observer_h = (h * observer_pct / 100).clamp(3, maximum);
            let head_h = h - observer_h - 1;
            rect.branch(
                false,
                &[
                    WorkspaceRect {
                        h: observer_h,
                        ..rect
                    }
                    .leaf(observer)?,
                    WorkspaceRect {
                        h: head_h,
                        y: observer_h + 1,
                        ..rect
                    }
                    .leaf(head)?,
                ],
            )
        }
    } else {
        let left = (w * workspace_number(config, "head_percent", 20, 70)? / 100).clamp(3, w - 4);
        rect.branch(
            true,
            &[
                WorkspaceRect { w: left, ..rect }.leaf(head)?,
                workspace_grid(
                    WorkspaceRect {
                        w: w - left - 1,
                        x: left + 1,
                        ..rect
                    },
                    workers,
                    2.min(workers.len()),
                )?,
            ],
        )
    };
    let checksum = layout.bytes().fold(0_u16, |sum, byte| {
        sum.rotate_right(1).wrapping_add(byte as u16)
    });
    Ok(format!("{checksum:04x},{layout}"))
}

fn workspace(request: &Value) -> Result<Value, String> {
    let config = &request["workspace"];
    if config["enabled"] != true {
        return Err("native AI workspaces are disabled in SSOT".into());
    }
    let count = workspace_number(config, "worker_panes", 1, 8)?;
    // Validate every layout policy before creating any terminal process.
    workspace_compact(config, 180, 48)?;
    let sample: Vec<String> = (1..=count).map(|i| format!("%{i}")).collect();
    workspace_layout(config, 180, 48, "%0", &sample, None)?;
    workspace_layout(config, 80, 48, "%0", &sample, Some("%99"))?;
    let socket = string(request, "socket")?;
    let path = Path::new(socket);
    let file_name = path.file_name().and_then(|s| s.to_str()).unwrap_or("");
    let human_socket = request["human_socket"].as_str().unwrap_or("");
    let is_tmux_env = std::env::var("TMUX")
        .ok()
        .and_then(|t| t.split(',').next().map(|s| s.to_string()))
        .as_deref()
        == Some(socket);
    let is_valid_name = file_name == human_socket
        || file_name == "default"
        || file_name.starts_with("tmux-")
        || file_name.starts_with("mios-")
        || is_tmux_env;

    if !path.is_absolute()
        || path.canonicalize().ok().as_deref() != Some(path)
        || path.as_os_str().len() >= 104
        || !owned_path(path)
        || !owned_path(path.parent().ok_or("missing socket parent")?)
        || !is_valid_name
    {
        return Err("unsafe native workspace socket".into());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::{FileTypeExt, PermissionsExt};
        if !fs::symlink_metadata(path)
            .map_err(|e| e.to_string())?
            .file_type()
            .is_socket()
        {
            return Err("workspace path is not a socket".into());
        }
        if fs::symlink_metadata(path.parent().unwrap())
            .map_err(|e| e.to_string())?
            .permissions()
            .mode()
            & 0o077
            != 0
        {
            return Err("workspace socket parent is not private".into());
        }
    }
    let tmux = |args: &[&str]| workspace_tmux(socket, args);
    let action = string(request, "action")?;
    let mut head = request["head"].as_str().unwrap_or("").to_string();
    if action == "open" {
        let session = string(request, "session")?;
        let _opening = workspace_lock(
            path,
            &format!("mios-workspace-open-{}.lock", digest(session)),
        )?;
        // Re-enter the existing human workspace; opening a terminal must not
        // start another head or duplicate its worker reservations.
        let panes = tmux(&["list-panes", "-a", "-F",
            "#{pane_id}\t#{session_name}\t#{@mios-workspace-head}\t#{@mios-workspace-window}\t#{pane_dead}"]).unwrap_or_default();
        for row in panes
            .lines()
            .map(|line| line.split('\t').collect::<Vec<_>>())
        {
            if row.len() == 5
                && row[0] == row[2]
                && row[3].starts_with('@')
                && row[4] == "0"
                && tmux(&["display-message", "-p", "-t", row[3], "#{session_name}"])
                    .is_ok_and(|name| name == session)
            {
                let mut resize = request.clone();
                resize["head"] = json!(row[0]);
                resize["action"] = json!("resize");
                let mut result = workspace(&resize)?;
                if result["managed"] == true {
                    if request["view"] == "compact" {
                        resize["action"] = json!("view");
                        resize["view"] = json!("compact");
                        result = workspace(&resize)?;
                    }
                    let active = result["active"].as_str().unwrap_or(row[0]);
                    tmux(&["select-window", "-t", row[3]])?;
                    tmux(&["select-pane", "-t", active])?;
                    return Ok(json!({"head":row[0],"worker_panes":count,"reused":true}));
                }
            }
        }
        let target = format!("={session}:");
        let session_target = format!("={session}");
        let directory = request["directory"]
            .as_str()
            .map(str::to_string)
            .unwrap_or_else(|| {
                std::env::current_dir()
                    .unwrap_or_default()
                    .to_string_lossy()
                    .into_owned()
            });
        if !Path::new(&directory).is_absolute() || !Path::new(&directory).is_dir() {
            return Err("workspace directory must be an existing absolute path".into());
        }
        let latch = string(request, "latch")?;
        identifier(latch)?;
        let command = format!(
            "tmux -S '{}' wait-for '{}'; exec {}",
            socket.replace('\'', "'\\''"),
            latch,
            string(request, "command")?
        );
        let session_exists = tmux(&["has-session", "-t", &session_target]).is_ok();
        head = if !session_exists {
            tmux(&[
                "new-session",
                "-d",
                "-P",
                "-F",
                "#{pane_id}",
                "-s",
                session,
                "-n",
                string(config, "window_name")?,
                "-c",
                &directory,
                &command,
            ])?
        } else {
            tmux(&[
                "new-window",
                "-d",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                &target,
                "-n",
                string(config, "window_name")?,
                "-c",
                &directory,
                &command,
            ])?
        };
        let mut created_observer = None;
        let storage = format!("mios-workspace-{}", head.trim_start_matches('%'));
        let created = (|| -> Result<(), String> {
            tmux(&[
                "set-option",
                "-w",
                "-t",
                &head,
                "@mios-workspace-head",
                &head,
            ])?;
            let window = tmux(&["display-message", "-p", "-t", &head, "#{window_id}"])?;
            for (key, value) in [
                ("@mios-workspace-head", &head),
                ("@mios-workspace-window", &window),
            ] {
                tmux(&["set-option", "-p", "-t", &head, key, value])?;
            }
            if let Some(view) = request["view"].as_str() {
                if !matches!(view, "auto" | "compact") {
                    return Err("invalid workspace view".into());
                }
                tmux(&[
                    "set-option",
                    "-w",
                    "-t",
                    &head,
                    "@mios-workspace-view",
                    view,
                ])?;
            }
            tmux(&["set-option", "-w", "-t", &head, "window-size", "latest"])?;
            // Park managed panes outside the operator's window/tab list. Pane
            // IDs and processes survive moves between this session and the view.
            let anchor = tmux(&[
                "new-session",
                "-d",
                "-P",
                "-F",
                "#{pane_id}",
                "-s",
                &storage,
                "-n",
                string(config, "workers_window_name")?,
                "-c",
                &directory,
                "exec /usr/bin/sleep infinity",
            ])?;
            tmux(&[
                "set-option",
                "-t",
                &storage,
                "@mios-workspace-storage-for",
                &head,
            ])?;
            tmux(&[
                "set-option",
                "-w",
                "-t",
                &head,
                "@mios-workspace-anchor",
                &anchor,
            ])?;
            tmux(&[
                "set-option",
                "-p",
                "-t",
                &anchor,
                "@mios-workspace-anchor-for",
                &head,
            ])?;
            for slot in 1..=count {
                let pane = tmux(&[
                    "split-window",
                    "-d",
                    "-P",
                    "-F",
                    "#{pane_id}",
                    "-t",
                    &head,
                    "-c",
                    &directory,
                    "exec /usr/bin/sleep infinity",
                ])?;
                let pid = tmux(&["display-message", "-p", "-t", &pane, "#{pane_pid}"])?;
                for (key, value) in [
                    ("@mios-workspace-worker", head.clone()),
                    ("@mios-workspace-slot", slot.to_string()),
                    ("@mios-workspace-reserved", pid),
                ] {
                    tmux(&["set-option", "-p", "-t", &pane, key, &value])?;
                }
                tmux(&[
                    "select-pane",
                    "-t",
                    &pane,
                    "-T",
                    &format!("Worker {slot} (empty)"),
                ])?;
                tmux(&["select-layout", "-t", &head, "tiled"])?;
            }
            let observer = tmux(&[
                "new-window",
                "-d",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                &format!("={storage}:"),
                "-n",
                "MiOS AI Agents",
                "-c",
                &directory,
                string(request, "observer_command")?,
            ])?;
            created_observer = Some(observer.clone());
            tmux(&[
                "set-option",
                "-w",
                "-t",
                &head,
                "@mios-workspace-observer",
                &observer,
            ])?;
            tmux(&[
                "set-option",
                "-p",
                "-t",
                &observer,
                "@mios-workspace-observer-for",
                &head,
            ])?;
            tmux(&[
                "set-option",
                "-w",
                "-t",
                &head,
                "@mios-workspace-observer-command",
                string(request, "observer_command")?,
            ])?;
            // A resize hook runs in the server's environment, which may still
            // identify another pane. Bind it to the verified head and daemon.
            let witness = tmux(&[
                "display-message",
                "-p",
                "-t",
                &head,
                "#{socket_path},#{pid},#{session_id}",
            ])?;
            let shell_quote = |s: &str| format!("'{}'", s.replace('\'', "'\\''"));
            let hook_command = format!(
                "env TMUX={} TMUX_PANE={} {} --workspace-resize {} {}",
                shell_quote(&witness),
                shell_quote(&head),
                string(request, "adapter")?,
                shell_quote(&head),
                shell_quote(socket)
            );
            let hook = format!("run-shell -b {}", shell_quote(&hook_command));
            tmux(&["set-hook", "-w", "-t", &head, "window-resized[100]", &hook])?;
            Ok(())
        })();
        let mut resize = request.clone();
        resize["head"] = json!(head);
        resize["action"] = json!("resize");
        let ready = created
            .and_then(|()| workspace(&resize).map(|_| ()))
            .and_then(|()| tmux(&["wait-for", "-S", latch]).map(|_| ()));
        if let Err(error) = ready {
            if let Some(observer) = created_observer {
                let _ = tmux(&["kill-pane", "-t", &observer]);
            }
            let _ = tmux(&["kill-window", "-t", &head]);
            let _ = tmux(&["kill-session", "-t", &storage]);
            return Err(error);
        }
        tmux(&["select-window", "-t", &head])?;
        tmux(&["select-pane", "-t", &head])?;
        return Ok(json!({"head":head,"worker_panes":count}));
    }
    let mut window = match tmux(&[
        "display-message",
        "-p",
        "-t",
        &head,
        "#{@mios-workspace-window}",
    ]) {
        Ok(w) => w,
        Err(_) => return Ok(json!({"managed": false})),
    };
    if window.is_empty() {
        window = match tmux(&["display-message", "-p", "-t", &head, "#{window_id}"]) {
            Ok(w) => w,
            Err(_) => return Ok(json!({"managed": false})),
        };
    }
    let context = match tmux(&["display-message", "-p", "-t", &window, "#{session_name}\t#{window_id}\t#{@mios-workspace-head}\t#{window_width}\t#{window_height}\t#{@mios-workspace-observer}\t#{window_zoomed_flag}"]) {
        Ok(c) => c,
        Err(_) => return Ok(json!({"managed": false})),
    };
    let fields: Vec<&str> = context.split('\t').collect();
    if fields.len() != 7 || fields[0] != string(request, "session")? || fields[2] != head {
        return Ok(json!({"managed":false}));
    }
    let window = fields[1];
    tmux(&[
        "set-option",
        "-p",
        "-t",
        &head,
        "@mios-workspace-head",
        &head,
    ])?;
    tmux(&[
        "set-option",
        "-p",
        "-t",
        &head,
        "@mios-workspace-window",
        window,
    ])?;
    let _layout = workspace_lock(
        path,
        &format!("mios-workspace-{}.lock", head.trim_start_matches('%')),
    )?;
    let rows = tmux(&["list-panes", "-a", "-F", "#{pane_id}\t#{@mios-workspace-worker}\t#{@mios-workspace-slot}\t#{@mios-workspace-reserved}\t#{pane_pid}\t#{pane_current_command}\t#{@mios-mcp-owner}"])?;
    if action == "claim" || action == "release" {
        let slot = request["slot"]
            .as_u64()
            .filter(|n| (1..=count as u64).contains(n));
        let Some(slot) = slot else {
            return Ok(json!({"managed":false}));
        };
        let row = rows
            .lines()
            .map(|s| s.split('\t').collect::<Vec<_>>())
            .find(|r| r.len() == 7 && r[1] == head && r[2] == slot.to_string());
        let Some(row) = row else {
            return Ok(json!({"managed":false}));
        };
        let pane = row[0];
        let owner = string(request, "owner")?;
        identifier(owner)?;
        if action == "claim" {
            if !row[6].is_empty() {
                return Ok(json!({"managed":false}));
            }
            if row[3].is_empty() || row[3] != row[4] || row[5] != "sleep" {
                return Err("workspace reservation witness changed".into());
            }
            tmux(&["set-option", "-p", "-t", pane, "@mios-mcp-owner", owner])?;
            tmux(&[
                "respawn-pane",
                "-k",
                "-t",
                pane,
                string(request, "command")?,
            ])?;
            tmux(&["set-option", "-pu", "-t", pane, "@mios-workspace-reserved"])?;
        } else {
            if row[6] != owner || request["pane"].as_str() != Some(pane) {
                return Err("workspace release ownership changed".into());
            }
            tmux(&[
                "respawn-pane",
                "-k",
                "-t",
                pane,
                "exec /usr/bin/sleep infinity",
            ])?;
            let pid = tmux(&["display-message", "-p", "-t", pane, "#{pane_pid}"])?;
            for key in ["@mios-mcp-owner", "@mcp_pane", "@mcp_owner", "@mcp_slot"] {
                tmux(&["set-option", "-pu", "-t", pane, key])?;
            }
            tmux(&[
                "set-option",
                "-p",
                "-t",
                pane,
                "@mios-workspace-reserved",
                &pid,
            ])?;
        }
        return Ok(json!({"managed":true,"pane":pane}));
    }
    if !matches!(action, "resize" | "focus" | "view") {
        return Err("unknown workspace action".into());
    }
    if fields[6] == "1" {
        return Ok(json!({"managed":true,"zoomed":true}));
    }
    let w = fields[3].parse::<usize>().map_err(|e| e.to_string())?;
    let h = fields[4].parse::<usize>().map_err(|e| e.to_string())?;
    // The observer pane can exit or be closed by the operator. Every later step
    // joins/breaks it, so a dead id made each window-resized hook fail with
    // "can't find pane". Rebuild it in this head's storage session.
    let mut observer_id = fields[5].to_string();
    let observer_alive = !observer_id.is_empty()
        && tmux(&["display-message", "-p", "-t", &observer_id, "#{pane_id}"])
            .map(|p| p == observer_id)
            .unwrap_or(false);
    if !observer_alive {
        let command = tmux(&[
            "display-message",
            "-p",
            "-t",
            window,
            "#{@mios-workspace-observer-command}",
        ])
        .ok()
        .filter(|c| !c.is_empty())
        .or_else(|| {
            request
                .get("observer_command")
                .and_then(|v| v.as_str())
                .filter(|c| !c.is_empty())
                .map(str::to_string)
        });
        let Some(command) = command else {
            return Ok(json!({"managed":true,"layout":"observer_missing"}));
        };
        let storage = format!("mios-workspace-{}", head.trim_start_matches('%'));
        observer_id = if tmux(&["has-session", "-t", &format!("={storage}")]).is_ok() {
            tmux(&[
                "new-window",
                "-d",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                &format!("={storage}:"),
                "-n",
                "MiOS AI Agents",
                &command,
            ])?
        } else {
            let pane = tmux(&[
                "new-session",
                "-d",
                "-P",
                "-F",
                "#{pane_id}",
                "-s",
                &storage,
                "-n",
                "MiOS AI Agents",
                &command,
            ])?;
            tmux(&[
                "set-option",
                "-t",
                &storage,
                "@mios-workspace-storage-for",
                &head,
            ])?;
            pane
        };
        tmux(&[
            "set-option",
            "-w",
            "-t",
            window,
            "@mios-workspace-observer",
            &observer_id,
        ])?;
        tmux(&[
            "set-option",
            "-w",
            "-t",
            window,
            "@mios-workspace-observer-command",
            &command,
        ])?;
        tmux(&[
            "set-option",
            "-p",
            "-t",
            &observer_id,
            "@mios-workspace-observer-for",
            &head,
        ])?;
    }
    let observer = observer_id.as_str();
    let mut workers = Vec::new();
    let mut ordered = Vec::new();
    for row in rows.lines().map(|s| s.split('\t').collect::<Vec<_>>()) {
        if row.len() != 7 {
            return Err("invalid workspace metadata".into());
        }
        if row[1] == head && row[0] != head {
            ordered.push((
                row[2].parse::<usize>().unwrap_or(usize::MAX),
                row[0].to_string(),
            ));
        }
    }
    ordered.sort();
    workers.extend(ordered.into_iter().map(|r| r.1));
    let mut view = tmux(&[
        "display-message",
        "-p",
        "-t",
        window,
        "#{@mios-workspace-view}",
    ])?;
    if action == "view" {
        view = match string(request, "view")? {
            "toggle" if view == "compact" => "auto",
            "toggle" | "compact" => "compact",
            "auto" => "auto",
            _ => return Err("invalid workspace view".into()),
        }
        .to_string();
        tmux(&[
            "set-option",
            "-w",
            "-t",
            window,
            "@mios-workspace-view",
            &view,
        ])?;
    }
    let compact = view == "compact" || workspace_compact(config, w, h)?;
    let portrait =
        compact && w * 100 < h * workspace_number(config, "portrait_ratio_percent", 50, 400)?;
    let mut active = tmux(&[
        "display-message",
        "-p",
        "-t",
        window,
        "#{@mios-workspace-active}",
    ])?;
    let mut members = vec![head.clone()];
    members.extend(workers.iter().cloned());
    if !members.contains(&active) {
        active = head.clone();
    }
    if action == "focus" {
        let target = string(request, "target")?;
        active = if target == "next" {
            members[(members.iter().position(|p| p == &active).unwrap_or(0) + 1) % members.len()]
                .clone()
        } else if members.iter().any(|p| p == target) {
            target.to_string()
        } else {
            return Err("pane is not a member of this workspace".into());
        };
    }
    // Refuse to move panes the operator added to the managed window.
    let present = tmux(&["list-panes", "-t", window, "-F", "#{pane_id}"])?;
    if present
        .lines()
        .any(|p| p != observer && !members.iter().any(|m| m == p))
    {
        return Ok(json!({"managed":true,"layout":"operator_modified"}));
    }
    let mut anchor = tmux(&[
        "display-message",
        "-p",
        "-t",
        window,
        "#{@mios-workspace-anchor}",
    ])?;
    // A recorded anchor pane can be killed (operator closed it, storage session
    // reaped). Joining onto a dead anchor fails every resize with
    // "can't find pane"; forget it so it is rebuilt below.
    if !anchor.is_empty()
        && tmux(&["display-message", "-p", "-t", &anchor, "#{pane_id}"])
            .map(|p| p != anchor)
            .unwrap_or(true)
    {
        anchor.clear();
    }
    let storage = format!("mios-workspace-{}", head.trim_start_matches('%'));
    let storage_exists = tmux(&["has-session", "-t", &format!("={storage}")]).is_ok();
    if storage_exists {
        if tmux(&[
            "display-message",
            "-p",
            "-t",
            &storage,
            "#{@mios-workspace-storage-for}",
        ])? != head
        {
            return Err("workspace storage ownership changed".into());
        }
    } else if !anchor.is_empty() {
        // Upgrade an older workspace without reclaiming an operator pane.
        let stored = tmux(&[
            "list-panes",
            "-t",
            &anchor,
            "-F",
            "#{pane_id}\t#{@mios-workspace-worker}\t#{@mios-workspace-anchor-for}",
        ])?;
        if stored.lines().any(|line| {
            let row: Vec<&str> = line.split('\t').collect();
            row.len() != 3 || (row[1] != head && row[2] != head)
        }) {
            return Err("workspace storage contains an operator pane".into());
        }
        let temporary = tmux(&[
            "new-session",
            "-d",
            "-P",
            "-F",
            "#{pane_id}",
            "-s",
            &storage,
            "exec /usr/bin/sleep infinity",
        ])?;
        tmux(&[
            "set-option",
            "-t",
            &storage,
            "@mios-workspace-storage-for",
            &head,
        ])?;
        let parked = tmux(&["display-message", "-p", "-t", &anchor, "#{window_id}"])?;
        tmux(&[
            "move-window",
            "-d",
            "-s",
            &parked,
            "-t",
            &format!("={storage}:"),
        ])?;
        tmux(&["kill-pane", "-t", &temporary])?;
    }
    if anchor.is_empty() {
        anchor = if storage_exists {
            tmux(&[
                "new-window",
                "-d",
                "-P",
                "-F",
                "#{pane_id}",
                "-t",
                &format!("={storage}:"),
                "-n",
                string(config, "workers_window_name")?,
                "exec /usr/bin/sleep infinity",
            ])?
        } else {
            tmux(&[
                "new-session",
                "-d",
                "-P",
                "-F",
                "#{pane_id}",
                "-s",
                &storage,
                "-n",
                string(config, "workers_window_name")?,
                "exec /usr/bin/sleep infinity",
            ])?
        };
        tmux(&[
            "set-option",
            "-t",
            &storage,
            "@mios-workspace-storage-for",
            &head,
        ])?;
        tmux(&[
            "set-option",
            "-w",
            "-t",
            window,
            "@mios-workspace-anchor",
            &anchor,
        ])?;
        tmux(&[
            "set-option",
            "-p",
            "-t",
            &anchor,
            "@mios-workspace-anchor-for",
            &head,
        ])?;
    }
    for pane in &members {
        let destination = if !compact || pane == &active {
            window.to_string()
        } else {
            tmux(&["display-message", "-p", "-t", &anchor, "#{window_id}"])?
        };
        if tmux(&["display-message", "-p", "-t", pane, "#{window_id}"])? != destination {
            let target = if destination == window {
                tmux(&["display-message", "-p", "-t", window, "#{pane_id}"])?
            } else {
                anchor.clone()
            };
            tmux(&["join-pane", "-d", "-s", pane, "-t", &target])?;
            tmux(&["select-layout", "-t", &destination, "tiled"])?;
        }
    }
    let layout = workspace_layout(
        config,
        w,
        h,
        if compact { &active } else { &head },
        &workers,
        if compact { Some(observer) } else { None },
    )?;
    let observer_window = tmux(&["display-message", "-p", "-t", observer, "#{window_id}"])?;
    if compact && observer_window != window {
        if portrait {
            tmux(&["join-pane", "-d", "-b", "-v", "-s", observer, "-t", &active])?;
        } else {
            tmux(&["join-pane", "-d", "-h", "-s", observer, "-t", &active])?;
        }
    } else if !compact && observer_window == window {
        tmux(&[
            "break-pane",
            "-d",
            "-s",
            observer,
            "-t",
            &format!("={storage}:"),
            "-n",
            "MiOS AI Agents",
        ])?;
    } else if !compact
        && tmux(&["display-message", "-p", "-t", observer, "#{session_name}"])? != storage
    {
        tmux(&[
            "move-window",
            "-d",
            "-s",
            &observer_window,
            "-t",
            &format!("={storage}:"),
        ])?;
    }
    tmux(&["select-layout", "-t", window, &layout])?;
    // Older tmux releases ignore pane IDs in a saved layout and assign cells
    // in index order. Reconcile by pane ID without restarting any process.
    let positioned = tmux(&[
        "list-panes",
        "-t",
        window,
        "-F",
        "#{pane_id}\t#{pane_top}\t#{pane_left}",
    ])?;
    let mut cells: Vec<(usize, usize, String)> = positioned
        .lines()
        .map(|line| {
            let row: Vec<&str> = line.split('\t').collect();
            Ok((
                row[1].parse::<usize>().map_err(|e| e.to_string())?,
                row[2].parse::<usize>().map_err(|e| e.to_string())?,
                row[0].to_string(),
            ))
        })
        .collect::<Result<_, String>>()?;
    cells.sort();
    let mut desired = if portrait {
        vec![if compact {
            observer.to_string()
        } else {
            active.clone()
        }]
    } else {
        vec![if compact {
            active.clone()
        } else {
            head.clone()
        }]
    };
    if compact {
        desired.push(if portrait {
            active.clone()
        } else {
            observer.to_string()
        });
    } else {
        desired.extend(workers.iter().cloned());
    }
    for (i, pane) in desired.iter().enumerate() {
        if cells[i].2 != *pane {
            let j = cells
                .iter()
                .position(|row| row.2 == *pane)
                .ok_or("workspace pane vanished during layout")?;
            tmux(&["swap-pane", "-d", "-s", pane, "-t", &cells[i].2])?;
            let displaced = cells[i].2.clone();
            cells[i].2 = pane.clone();
            cells[j].2 = displaced;
        }
    }
    tmux(&[
        "set-option",
        "-w",
        "-t",
        window,
        "@mios-workspace-layout",
        if portrait {
            "portrait"
        } else if compact {
            "compact"
        } else {
            "desktop"
        },
    ])?;
    tmux(&[
        "set-option",
        "-w",
        "-t",
        window,
        "@mios-workspace-active",
        &active,
    ])?;
    if action == "focus" {
        tmux(&["select-window", "-t", window])?;
        tmux(&["select-pane", "-t", &active])?;
    }
    Ok(
        json!({"managed":true,"layout":if portrait {"portrait"} else if compact {"compact"} else {"desktop"},"head":head,"active":active,"workers":workers,"observer":observer}),
    )
}

// Read the actual PTY dimensions while waiting for a complete input line. A
// portrait resize must redraw the chooser without starting or stopping a CLI.
#[cfg(target_os = "linux")]
fn workspace_terminal(tty: &fs::File) -> std::io::Result<(usize, usize, bool)> {
    use std::os::fd::AsRawFd;
    #[repr(C)]
    struct Winsize {
        rows: u16,
        cols: u16,
        x: u16,
        y: u16,
    }
    #[repr(C)]
    struct PollFd {
        fd: i32,
        events: i16,
        revents: i16,
    }
    unsafe extern "C" {
        fn ioctl(fd: i32, request: std::ffi::c_ulong, ...) -> i32;
        fn poll(fds: *mut PollFd, count: std::ffi::c_ulong, timeout: i32) -> i32;
    }
    let mut size = Winsize {
        rows: 0,
        cols: 0,
        x: 0,
        y: 0,
    };
    // Linux TIOCGWINSZ; the storage lives for the complete ioctl call.
    if unsafe { ioctl(tty.as_raw_fd(), 0x5413, &mut size) } < 0 {
        return Err(std::io::Error::last_os_error());
    }
    let mut fd = PollFd {
        fd: tty.as_raw_fd(),
        events: 1,
        revents: 0,
    };
    let ready = unsafe { poll(&mut fd, 1, 250) };
    if ready < 0 && std::io::Error::last_os_error().kind() != std::io::ErrorKind::Interrupted {
        return Err(std::io::Error::last_os_error());
    }
    Ok((
        usize::from(size.cols).max(1),
        usize::from(size.rows).max(1),
        ready > 0,
    ))
}
#[cfg(not(target_os = "linux"))]
fn workspace_terminal(_: &fs::File) -> std::io::Result<(usize, usize, bool)> {
    Err(std::io::Error::other(
        "native workspace chooser requires a Linux PTY",
    ))
}

fn wrap_display(text: &str, width: usize) -> Vec<String> {
    let limit = width.saturating_sub(1).max(1);
    let mut lines = Vec::new();
    let mut line = String::new();
    for word in text.split_whitespace() {
        if !line.is_empty() && line.chars().count() + 1 + word.chars().count() > limit {
            lines.push(std::mem::take(&mut line));
        }
        for ch in word.chars() {
            if line.chars().count() == limit {
                lines.push(std::mem::take(&mut line));
            }
            line.push(ch);
        }
        if line.chars().count() < limit {
            line.push(' ');
        }
    }
    if !line.trim().is_empty() {
        lines.push(line.trim_end().into());
    }
    lines
        .into_iter()
        .map(|s| s.trim_end().to_string())
        .collect()
}

fn workspace_menu_screen(
    request: &Value,
    width: usize,
    height: usize,
    page: usize,
    status: &str,
) -> Result<(String, usize), String> {
    let cfg = &request["workspace"];
    let agents = request["agents"]
        .as_array()
        .filter(|a| !a.is_empty())
        .ok_or("empty client catalog")?;
    let mut introduction = wrap_display(&display_text(&cfg["introduction"]), width);
    let selection = wrap_display(&display_text(&cfg["selection_hint"]), width);
    let navigation = if height >= 7 {
        wrap_display(
            &display_text(
                &cfg[if width < 140 {
                    "navigation_hint_compact"
                } else {
                    "navigation_hint"
                }],
            ),
            width,
        )
    } else {
        Vec::new()
    };
    // On very short displays reserve a choice and its prompt before extra prose.
    introduction.truncate(
        height
            .saturating_sub(selection.len() + navigation.len() + 3)
            .max(1),
    );
    let fixed = introduction.len()
        + selection.len()
        + navigation.len()
        + 1
        + usize::from(height >= 5)
        + usize::from(height >= 9);
    let rows = height.saturating_sub(fixed).max(1);
    let pages = agents.len().div_ceil(rows);
    let page = page % pages;
    let mut lines = Vec::new();
    if height >= 5 {
        lines.push(display_text(&cfg["window_name"]));
    }
    lines.extend(introduction);
    for (index, agent) in agents.iter().enumerate().skip(page * rows).take(rows) {
        let name = string(agent, "name")?;
        identifier(name)?;
        lines.push(format!(
            "{}. {}  {} / {}",
            index + 1,
            name,
            if agent["installed"] == true {
                "ready"
            } else {
                "missing"
            },
            if agent["mcp"] == true {
                "MCP"
            } else {
                "CLI only"
            }
        ));
    }
    if height >= 9 {
        lines.push(if status.is_empty() {
            format!("Clients: {} | page {}/{}", agents.len(), page + 1, pages)
        } else {
            status.to_string()
        });
    }
    lines.extend(selection);
    lines.extend(navigation);
    lines.push("Client> ".into());
    if height < 4 {
        lines = vec!["Client number/name (q to close)> ".into()];
    }
    // Leave the last column unused to prevent a terminal autowrap from scrolling
    // the introduction away. Small panes page the catalog, never truncate it.
    let limit = width.saturating_sub(1).max(1);
    let clipped: Vec<String> = lines
        .into_iter()
        .map(|line| {
            if line.chars().count() <= limit {
                line
            } else {
                let mut text: String = line.chars().take(limit.saturating_sub(3)).collect();
                text.push_str(&"..."[..limit.min(3)]);
                text
            }
        })
        .collect();
    Ok((clipped.join("\n"), pages))
}

fn workspace_menu(request: &Value) -> Result<Value, Box<dyn std::error::Error>> {
    use std::io::{BufRead, BufReader};
    let agents = request["agents"]
        .as_array()
        .ok_or("missing client catalog")?;
    let mut input = BufReader::with_capacity(
        1,
        OpenOptions::new().read(true).write(true).open("/dev/tty")?,
    );
    let palette = &request["colors"];
    let color = |name: &str, background: bool| -> Result<String, Box<dyn std::error::Error>> {
        let hex = string(palette, name)?;
        if hex.len() != 7
            || !hex.starts_with('#')
            || !hex[1..].bytes().all(|b| b.is_ascii_hexdigit())
        {
            return Err("invalid SSOT menu color".into());
        }
        Ok(format!(
            "\x1b[{};2;{};{};{}m",
            if background { 48 } else { 38 },
            u8::from_str_radix(&hex[1..3], 16)?,
            u8::from_str_radix(&hex[3..5], 16)?,
            u8::from_str_radix(&hex[5..7], 16)?
        ))
    };
    let (mut page, mut status, mut drawn) = (0_usize, String::new(), None);
    loop {
        let (width, height, ready) = workspace_terminal(input.get_ref())?;
        let (_, pages) = workspace_menu_screen(request, width, height, page, &status)?;
        if drawn != Some((width, height, page)) {
            let (screen, _) = workspace_menu_screen(request, width, height, page, &status)?;
            print!(
                "\x1b[0m{}{}\x1b[H\x1b[2J{screen}",
                color("bg", true)?,
                color("fg", false)?
            );
            std::io::stdout().flush()?;
            drawn = Some((width, height, page));
        }
        if !ready {
            continue;
        }
        let mut choice = String::new();
        if input.read_line(&mut choice)? == 0 || matches!(choice.trim(), "q" | "quit" | "exit") {
            return Ok(json!({"closed":true}));
        }
        drawn = None;
        if matches!(choice.trim(), "n" | "p") {
            page = if choice.trim() == "n" {
                (page + 1) % pages
            } else {
                (page + pages - 1) % pages
            };
            status.clear();
            continue;
        }
        let index = choice
            .trim()
            .parse::<usize>()
            .ok()
            .and_then(|n| n.checked_sub(1));
        let selected = agents
            .iter()
            .enumerate()
            .find(|(i, a)| Some(*i) == index || a["name"].as_str() == Some(choice.trim()));
        let Some((_, agent)) = selected.filter(|(_, a)| a["installed"] == true) else {
            status = "Choose an installed client by number or name.".into();
            continue;
        };
        status.clear();
        let name = string(agent, "name")?;
        // Reopen /dev/tty for the CLI; JSON configuration stdin is already consumed.
        let status = Command::new("/usr/bin/mios")
            .args(["agent", name])
            .stdin(Stdio::from(
                OpenOptions::new().read(true).write(true).open("/dev/tty")?,
            ))
            .status()?;
        println!(
            "\x1b[0m\n{name} exited ({}). Press Enter to choose another client.",
            status.code().unwrap_or(1)
        );
        std::io::stdout().flush()?;
        choice.clear();
        input.read_line(&mut choice)?;
    }
}

fn main() {
    let args: Vec<String> = std::env::args().collect();
    let result = (|| -> Result<Value, Box<dyn std::error::Error>> {
        if args.len() == 2 && args[1] == "--workspace-menu" {
            let mut raw = String::new();
            std::io::stdin()
                .take(2 * 1024 * 1024)
                .read_to_string(&mut raw)?;
            return workspace_menu(&serde_json::from_str(&raw)?);
        }
        if args.len() == 2 && args[1] == "--workspace" {
            let mut raw = String::new();
            std::io::stdin()
                .take(2 * 1024 * 1024)
                .read_to_string(&mut raw)?;
            return workspace(&serde_json::from_str(&raw)?)
                .map_err(|e| std::io::Error::other(e).into());
        }
        if !(args.len() == 3
            || (args.len() == 4 && args[3] == "--observe")
            || (args.len() == 5 && args[3] == "--watch"))
            || args[1] != "--state"
        {
            return Err(
                "Usage: mios-agent-relay --state PATH [--observe | --watch SECONDS] < request.json"
                    .into(),
            );
        }
        let mut raw = String::new();
        std::io::stdin()
            .take(2 * 1024 * 1024)
            .read_to_string(&mut raw)?;
        let mut request: Value = serde_json::from_str(&raw)?;
        if args.len() > 3 {
            request["action"] = json!("observe");
        }
        if args.get(3).is_some_and(|v| v == "--watch") {
            let seconds = args[4].parse::<u64>()?;
            if !(1..=60).contains(&seconds) {
                return Err("watch refresh must be 1..60 seconds".into());
            }
            if std::io::stdout().is_terminal() {
                loop {
                    let snapshot = observe(Path::new(&args[2]), &request)?;
                    print!(
                        "{}",
                        render_observation(&snapshot, &request).map_err(std::io::Error::other)?
                    );
                    std::io::stdout().flush()?;
                    std::thread::sleep(Duration::from_secs(seconds));
                }
            }
        }
        run(Path::new(&args[2]), &request)
    })();
    match result {
        Ok(value) if args.get(1).is_some_and(|a| a == "--workspace-menu") => {
            let _ = value;
            println!("\x1b[0m");
        }
        Ok(value) => println!("{}", json!({"ok":true,"result":value})),
        Err(error) => {
            println!("{}", json!({"ok":false,"error":error.to_string()}));
            std::process::exit(2);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn chooser_keeps_intro_choices_and_hints_visible_at_portrait_sizes() {
        let request = json!({"workspace":{"window_name":"MiOS AI", "introduction":"Choose a head CLI.",
            "selection_hint":"Number/name; n/p pages; q closes.", "navigation_hint_compact":"Ctrl-b z: zoom", "navigation_hint":"Ctrl-b w: panes"},
            "agents":(1..=7).map(|i| json!({"name":format!("client{i}"),"installed":true,"mcp":true})).collect::<Vec<_>>()});
        for (w, h) in [(35, 19), (60, 16), (80, 8), (30, 6), (140, 40)] {
            let (screen, pages) = workspace_menu_screen(&request, w, h, 0, "").unwrap();
            assert!(screen.lines().count() <= h);
            assert!(screen.lines().all(|line| line.chars().count() < w));
            assert!(screen.contains("Choose a head CLI."));
            assert!(screen.contains("client1"));
            assert!(screen.contains("Client>"));
            let last = workspace_menu_screen(&request, w, h, pages - 1, "")
                .unwrap()
                .0;
            assert!(last.contains("client7"));
        }
        let mut invalid = request;
        invalid["agents"][0]["name"] = json!("DEVLOOP-PLANTED-CLIENT;false");
        assert!(workspace_menu_screen(&invalid, 80, 16, 0, "").is_err());
    }
    #[test]
    fn compact_observer_keeps_identities_and_receipts_readable() {
        let state = json!({"agents":{
            "codex:01a10766-1ae4-78b2-b94d-0b01e333022d:live-20261005":{"kind":"codex","label":"Codex head","expires":500},
            "agy:e5ae0f98-a15b-454a-b5b9-f9a8620bcf72":{"kind":"agy","label":"agy:e5ae0f98-a15b-454a-b5b9-f9a8620bcf72","expires":500}},
            "messages":[{"message_id":"DEVLOOP-LONG-RECEIPT-ID","from":"codex:head","to":"agy:worker","status":"received"}]});
        let mut snapshot = observation(&state, &observer_request(), 100).unwrap();
        snapshot["registry"] = json!("present");
        snapshot["panes"] = json!([{"pane":"%1","role":"Head","command":"python3"},{"pane":"%2","role":"W1","command":"sleep"}]);
        for (w, h) in [(35, 19), (44, 19), (46, 9), (24, 6)] {
            let lines = observation_lines(&snapshot, &observer_request(), w, h).unwrap();
            assert!(lines.len() < h);
            assert!(lines.iter().all(|s| s.chars().count() < w));
            let text = lines.join("\n");
            assert!(!text.contains("01a10766") && !text.contains("DEVLOOP-LONG"));
            if h >= 10 {
                assert!(text.contains("Codex head") && text.contains("agy #"));
                assert!(text.contains("read codex > agy") && text.contains("read != done"));
                assert!(text.contains("Head %1 chooser") && text.contains("W1 %2 empty"));
            }
        }
    }
    fn request(action: &str, id: &str) -> Value {
        json!({"action":action,"agent_id":id,"token":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","kind":"codex","config":{"lease_s":60,"mailbox_retention_s":120,"queue_offline":true,"max_agents":2,"max_pending":2,"max_message_bytes":256,"max_receipts":2}})
    }
    fn observer_request() -> Value {
        json!({"action":"observe","observation":{"max_rows":32,"human_socket":"mios-human",
            "socket_roots":[],"colors":{"fg":"#E7DFD3"}}})
    }
    #[test]
    fn observation_is_sanitized_and_does_not_certify_delivery() {
        let state = json!({"agents":{"head":{"kind":"codex","label":"safe\u{001b}\n\u{202e}label",
            "token_hash":"DEVLOOP-PLANTED-LEASE","expires":50}},"messages":[
            {"message_id":"task","from":"head","to":"worker","status":"queued",
             "message":"DEVLOOP-PLANTED-BODY","created":10}]});
        let result = observation(&state, &observer_request(), 100).unwrap();
        assert_eq!(result["agents"][0]["online"], false);
        assert_eq!(result["agents"][0]["label"], "safelabel");
        assert_eq!(result["messages"][0]["status"], "queued");
        let text = serde_json::to_string(&result).unwrap();
        assert!(
            !text.contains("PLANTED-LEASE")
                && !text.contains("PLANTED-BODY")
                && !text.contains("token_hash")
        );
        let mut bad = observer_request();
        bad["observation"]["max_rows"] = json!(0);
        assert!(observation(&state, &bad, 100)
            .unwrap_err()
            .contains("max_rows"));
    }
    #[test]
    fn observation_missing_registry_and_read_only_state() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("registry");
        assert_eq!(
            observe(&path, &observer_request()).unwrap()["registry"],
            "missing"
        );
        assert!(!path.exists());
        run(&path, &request("register", "head")).unwrap();
        let before = fs::read(path.join("state.json")).unwrap();
        assert_eq!(
            observe(&path, &observer_request()).unwrap()["registry"],
            "present"
        );
        assert_eq!(before, fs::read(path.join("state.json")).unwrap());
        fs::write(path.join("state.json"), "DEVLOOP-PLANTED-MALFORMED").unwrap();
        assert!(observe(&path, &observer_request()).is_err());
    }
    #[cfg(unix)]
    #[test]
    fn observation_rejects_symlinks_and_private_state_is_not_public() {
        use std::os::unix::fs::{symlink, PermissionsExt};
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("registry");
        run(&path, &request("register", "head")).unwrap();
        assert_eq!(
            fs::metadata(path.join("state.json"))
                .unwrap()
                .permissions()
                .mode()
                & 0o777,
            0o600
        );
        assert_eq!(
            fs::metadata(&path).unwrap().permissions().mode() & 0o777,
            0o700
        );
        let alias = temp.path().join("DEVLOOP-PLANTED-SYMLINK");
        symlink(&path, &alias).unwrap();
        assert!(observe(&alias, &observer_request())
            .unwrap_err()
            .to_string()
            .contains("unsafe"));
        assert!(socket_candidates(&alias, "mios-human", 0).is_empty());
    }
    #[cfg(unix)]
    #[test]
    fn socket_discovery_accepts_owned_socket_and_rejects_alias_or_regular_file() {
        use std::os::unix::{fs::symlink, net::UnixListener};
        let temp = tempfile::tempdir().unwrap();
        let dir = temp.path().join("uid-1-test").join("tmux-1");
        fs::create_dir_all(&dir).unwrap();
        let socket = dir.join("mcp-headless");
        let _listener = UnixListener::bind(&socket).unwrap();
        fs::write(dir.join("mios-human"), "DEVLOOP-PLANTED-NONSOCKET").unwrap();
        assert_eq!(
            socket_candidates(temp.path(), "mios-human", 0),
            vec![socket]
        );
        symlink(&dir, temp.path().join("uid-PLANTED-ALIAS")).unwrap();
        assert_eq!(socket_candidates(temp.path(), "mios-human", 0).len(), 1);
    }
    #[test]
    fn recipient_receipt_and_idempotent_send() {
        let mut state = json!({"agents":{},"messages":[]});
        dispatch(&mut state, &request("register", "head"), 1).unwrap();
        dispatch(&mut state, &request("register", "worker"), 1).unwrap();
        let mut send = request("send", "head");
        send["to"] = json!("worker");
        send["message"] = json!("bounded task");
        send["message_id"] = json!("task-1");
        assert_eq!(dispatch(&mut state, &send, 2).unwrap()["status"], "queued");
        assert_eq!(dispatch(&mut state, &send, 2).unwrap()["duplicate"], true);
        assert_eq!(
            dispatch(&mut state, &request("receive", "worker"), 3).unwrap()["messages"]
                .as_array()
                .unwrap()
                .len(),
            1
        );
        let mut ack = request("ack", "worker");
        ack["message_id"] = json!("task-1");
        assert_eq!(dispatch(&mut state, &ack, 4).unwrap()["status"], "received");
        assert_eq!(
            dispatch(&mut state, &send, 5).unwrap()["status"],
            "received"
        );
        dispatch(&mut state, &request("unregister", "worker"), 6).unwrap();
        assert_eq!(
            dispatch(&mut state, &send, 7).unwrap()["status"],
            "received"
        );
        send["message_id"] = json!("DEVLOOP-PLANTED-OFFLINE");
        assert!(dispatch(&mut state, &send, 7)
            .unwrap_err()
            .contains("offline"));
        assert!(dispatch(&mut state, &request("receive", "worker"), 100)
            .unwrap_err()
            .contains("closed"));
    }
    #[test]
    fn cached_schema_clients_can_resume_and_reply() {
        let mut state = json!({"agents":{},"messages":[]});
        let mut legacy = request("register", "worker");
        legacy["config"]
            .as_object_mut()
            .unwrap()
            .remove("queue_offline");
        legacy["config"]
            .as_object_mut()
            .unwrap()
            .remove("mailbox_retention_s");
        dispatch(&mut state, &legacy, 1).unwrap();
        dispatch(&mut state, &request("register", "head"), 1).unwrap();
        let mut send = request("send", "head");
        send["to"] = json!("worker");
        send["message"] = json!("resume task");
        send["message_id"] = json!("queued-for-legacy");
        dispatch(&mut state, &send, 100).unwrap();
        legacy["action"] = json!("receive");
        assert_eq!(
            dispatch(&mut state, &legacy, 101).unwrap()["messages"][0]["message_id"],
            "queued-for-legacy"
        );
        legacy["action"] = json!("send");
        legacy["to"] = json!("head");
        legacy["message"] = json!("reply");
        legacy["message_id"] = json!("legacy-reply");
        assert_eq!(
            dispatch(&mut state, &legacy, 102).unwrap()["status"],
            "queued"
        );
        legacy["message_id"] = json!("DEVLOOP-PLANTED-LEGACY-OFFLINE");
        assert!(dispatch(&mut state, &legacy, 200)
            .unwrap_err()
            .contains("offline"));
    }
    #[test]
    fn dormant_mailboxes_resume_without_identity_takeover() {
        let mut state = json!({"agents":{},"messages":[]});
        dispatch(&mut state, &request("register", "head"), 1).unwrap();
        dispatch(&mut state, &request("register", "worker"), 1).unwrap();
        let mut send = request("send", "head");
        send["to"] = json!("worker");
        send["message"] = json!("task while worker is busy");
        send["message_id"] = json!("offline-task");
        let mut deny_offline = send.clone();
        deny_offline["config"]["queue_offline"] = json!(false);
        assert!(dispatch(&mut state, &deny_offline, 100)
            .unwrap_err()
            .contains("offline"));
        assert!(state["messages"].as_array().unwrap().is_empty());
        let queued = dispatch(&mut state, &send, 100).unwrap();
        assert_eq!(queued["status"], "queued");
        assert_eq!(queued["recipient_online"], false);
        let mut third = request("register", "third");
        assert!(dispatch(&mut state, &third, 300)
            .unwrap_err()
            .contains("full"));
        third["config"]["max_agents"] = json!(3);
        dispatch(&mut state, &third, 300).unwrap();
        assert!(state["agents"]["worker"].is_object());
        let mut imposter = request("register", "worker");
        imposter["token"] = json!("DEVLOOP-PLANTED-IMPERSONATION-00000");
        assert!(dispatch(&mut state, &imposter, 301)
            .unwrap_err()
            .contains("another lease"));
        imposter["action"] = json!("receive");
        assert!(dispatch(&mut state, &imposter, 301)
            .unwrap_err()
            .contains("invalid lease"));
        let inbox = dispatch(&mut state, &request("receive", "worker"), 302).unwrap();
        assert_eq!(inbox["messages"][0]["message_id"], "offline-task");
        assert_eq!(inbox["messages"][0]["status"], "queued");
        let mut ack = request("ack", "worker");
        ack["message_id"] = json!("offline-task");
        assert_eq!(
            dispatch(&mut state, &ack, 303).unwrap()["status"],
            "received"
        );
        dispatch(&mut state, &third, 600).unwrap();
        assert!(!state["agents"]["worker"].is_object());
        assert!(!state["agents"]["head"].is_object());
        assert!(state["messages"].as_array().unwrap().is_empty());
    }
    #[test]
    fn planted_misrouting_and_lease_impersonation_fail() {
        let mut state = json!({"agents":{},"messages":[]});
        dispatch(&mut state, &request("register", "head"), 1).unwrap();
        let mut stolen = request("register", "head");
        stolen["token"] = json!("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb");
        assert!(dispatch(&mut state, &stolen, 2)
            .unwrap_err()
            .contains("another lease"));
        let mut send = request("send", "head");
        send["to"] = json!("DEVLOOP-PLANTED-ABSENT");
        send["message"] = json!("negative");
        send["message_id"] = json!("task-1");
        assert!(dispatch(&mut state, &send, 2)
            .unwrap_err()
            .contains("offline"));
        assert!(state["messages"].as_array().unwrap().is_empty());
        stolen["action"] = json!("receive");
        assert!(dispatch(&mut state, &stolen, 2)
            .unwrap_err()
            .contains("invalid lease"));
    }
    #[test]
    fn simultaneous_sends_preserve_every_message() {
        let temp = tempfile::tempdir().unwrap();
        let path = temp.path().join("registry");
        run(&path, &request("register", "head")).unwrap();
        run(&path, &request("register", "worker")).unwrap();
        let jobs: Vec<_> = (0..8)
            .map(|i| {
                let path = path.clone();
                std::thread::spawn(move || {
                    let mut send = request("send", "head");
                    send["config"]["max_pending"] = json!(16);
                    send["to"] = json!("worker");
                    send["message"] = json!("hello");
                    send["message_id"] = json!(format!("task-{i}"));
                    run(&path, &send).unwrap();
                })
            })
            .collect();
        for job in jobs {
            job.join().unwrap();
        }
        assert_eq!(
            run(&path, &request("receive", "worker")).unwrap()["messages"]
                .as_array()
                .unwrap()
                .len(),
            8
        );
    }

    fn write_test_lease(
        dir: &Path,
        holder: &str,
        state: &str,
        handoff_to: Option<&str>,
        successors: Vec<&str>,
        now: u64,
        duration: u64,
    ) {
        let lease = json!({
            "holder_identity": holder,
            "lease_duration_seconds": duration,
            "acquire_time": format_iso(now.saturating_sub(3600)),
            "renew_time": format_iso(now.saturating_sub(100)),
            "lease_transitions": 1,
            "preferred_holder": "claude-code",
            "successors": successors,
            "state": state,
            "handoff_to": handoff_to,
        });
        save_json_file(&dir.join("lease.json"), &lease).unwrap();
    }

    #[test]
    fn test_probe_verdict_no_lease() {
        let temp = tempfile::tempdir().unwrap();
        let now = 1_700_000_000u64;
        let req = json!({
            "action": "probe",
            "me": "antigravity"
        });
        let res = handle_probe(temp.path(), &req, now).unwrap();
        assert_eq!(res["object"], "agent.probe");
        assert_eq!(res["verdict"], "no_lease");
        assert_eq!(res["take_over"], false);
        assert_eq!(res["next_probe_s"], 600);
        assert_eq!(
            res["reason"],
            "lease.json is missing; ask the operator, do not invent a lease"
        );
        assert_eq!(res["holder"], Value::Null);
    }

    #[test]
    fn test_probe_verdict_held_by_me() {
        let temp = tempfile::tempdir().unwrap();
        let now = 1_700_000_000u64;
        write_test_lease(
            temp.path(),
            "antigravity",
            "held",
            None,
            vec!["claude-code"],
            now,
            5400,
        );
        let req = json!({
            "action": "probe",
            "me": "antigravity"
        });
        let res = handle_probe(temp.path(), &req, now).unwrap();
        assert_eq!(res["verdict"], "held_by_me");
        assert_eq!(res["take_over"], false);
        assert_eq!(res["next_probe_s"], 600);
        assert_eq!(res["reason"], "you already hold the coordinator lease");
        assert_eq!(res["holder"], "antigravity");
    }

    #[test]
    fn test_probe_verdict_released_and_handoff_targeting() {
        let temp = tempfile::tempdir().unwrap();
        let now = 1_700_000_000u64;
        // Case 1: released to antigravity, successor may take
        write_test_lease(
            temp.path(),
            "claude-code",
            "released",
            Some("antigravity"),
            vec!["antigravity"],
            now,
            5400,
        );
        let req = json!({
            "action": "probe",
            "me": "antigravity"
        });
        let res = handle_probe(temp.path(), &req, now).unwrap();
        assert_eq!(res["verdict"], "released");
        assert_eq!(res["take_over"], true);
        assert_eq!(res["next_probe_s"], 60);
        assert_eq!(res["reason"], "holder released the lease to antigravity");

        // Case 2: released to another agent (planted negative for take_over)
        write_test_lease(
            temp.path(),
            "claude-code",
            "released",
            Some("codex"),
            vec!["antigravity"],
            now,
            5400,
        );
        let res2 = handle_probe(temp.path(), &req, now).unwrap();
        assert_eq!(res2["verdict"], "released");
        assert_eq!(res2["take_over"], false);
        assert_eq!(res2["reason"], "holder released the lease to codex");

        // Case 3: released with no specific handoff_to, but caller is not in successors
        write_test_lease(
            temp.path(),
            "claude-code",
            "released",
            None,
            vec!["codex"],
            now,
            5400,
        );
        let res3 = handle_probe(temp.path(), &req, now).unwrap();
        assert_eq!(res3["verdict"], "released");
        assert_eq!(res3["take_over"], false);
        assert_eq!(res3["reason"], "holder released the lease");
    }

    #[test]
    fn test_probe_verdict_exhausted_briefly() {
        let temp = tempfile::tempdir().unwrap();
        let transcript_path = temp.path().join("claude_transcript.jsonl");
        let status_path = temp.path().join("claude-code.status.json");

        let mtime = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let now = mtime;
        write_test_lease(
            temp.path(),
            "claude-code",
            "held",
            None,
            vec!["antigravity"],
            now,
            5400,
        );

        let resets_at = now + 300;
        let transcript_line = json!({
            "type": "assistant",
            "isApiErrorMessage": true,
            "apiError": "rate_limit_exceeded",
            "apiErrorStatus": 429,
            "quotaLimits": {
                "rateLimitType": "five_hour",
                "resetsAt": resets_at
            },
            "timestamp": format_iso(now),
            "message": {
                "content": [{"type": "text", "text": "Out of 5-hour tokens"}]
            }
        });
        fs::write(&transcript_path, format!("{}\n", transcript_line)).unwrap();

        let status = json!({
            "object": "agent.status",
            "agent_id": "claude-code",
            "transcript": transcript_path.to_str().unwrap(),
            "state": "working"
        });
        save_json_file(&status_path, &status).unwrap();

        let req = json!({
            "action": "probe",
            "me": "antigravity"
        });
        let res = handle_probe(temp.path(), &req, now).unwrap();
        assert_eq!(res["verdict"], "exhausted_briefly");
        assert_eq!(res["take_over"], false);
        assert_eq!(res["next_probe_s"], 360); // 300 + 60
        assert!(res["reason"].as_str().unwrap().contains("resets in 300 s"));
        assert_eq!(res["blocked"]["limit_type"], "five_hour");
    }

    #[test]
    fn test_probe_verdict_exhausted() {
        let temp = tempfile::tempdir().unwrap();
        let transcript_path = temp.path().join("claude_transcript.jsonl");
        let status_path = temp.path().join("claude-code.status.json");

        let now = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        write_test_lease(
            temp.path(),
            "claude-code",
            "held",
            None,
            vec!["antigravity"],
            now,
            5400,
        );

        let resets_at = now + 1800; // > min_outage_s (900)
        let transcript_line = json!({
            "type": "assistant",
            "isApiErrorMessage": true,
            "apiError": "rate_limit_exceeded",
            "apiErrorStatus": 429,
            "quotaLimits": {
                "rateLimitType": "weekly",
                "resetsAt": resets_at
            },
            "timestamp": format_iso(now),
            "message": {
                "content": [{"type": "text", "text": "Weekly quota exhausted"}]
            }
        });
        fs::write(&transcript_path, format!("{}\n", transcript_line)).unwrap();

        let status = json!({
            "object": "agent.status",
            "agent_id": "claude-code",
            "transcript": transcript_path.to_str().unwrap(),
            "state": "working"
        });
        save_json_file(&status_path, &status).unwrap();

        let req = json!({
            "action": "probe",
            "me": "antigravity"
        });
        let res = handle_probe(temp.path(), &req, now).unwrap();
        assert_eq!(res["verdict"], "exhausted");
        assert_eq!(res["take_over"], true);
        assert_eq!(res["next_probe_s"], 600);
        assert!(res["reason"]
            .as_str()
            .unwrap()
            .contains("API error (rate_limit_exceeded; weekly)"));
    }

    #[test]
    fn test_probe_verdict_silent() {
        let temp = tempfile::tempdir().unwrap();
        let status_path = temp.path().join("claude-code.status.json");
        let status = json!({
            "object": "agent.status",
            "agent_id": "claude-code",
            "state": "working"
        });
        save_json_file(&status_path, &status).unwrap();

        let file_mtime = fs::metadata(&status_path)
            .unwrap()
            .modified()
            .unwrap()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();

        // Advance simulated `now` by 6000 seconds past file_mtime and renew_time
        let duration = 5400u64;
        let now = file_mtime + 6000;
        let lease = json!({
            "holder_identity": "claude-code",
            "lease_duration_seconds": duration,
            "acquire_time": format_iso(now.saturating_sub(10000)),
            "renew_time": format_iso(file_mtime), // lease age = 6000s
            "lease_transitions": 1,
            "preferred_holder": "claude-code",
            "successors": ["antigravity"],
            "state": "held",
            "handoff_to": Value::Null,
        });
        save_json_file(&temp.path().join("lease.json"), &lease).unwrap();

        let req = json!({
            "action": "probe",
            "me": "antigravity"
        });
        let res = handle_probe(temp.path(), &req, now).unwrap();
        assert_eq!(res["verdict"], "silent");
        assert_eq!(res["take_over"], true);
        assert_eq!(res["next_probe_s"], 600);
        assert!(res["reason"]
            .as_str()
            .unwrap()
            .contains("no holder activity for 6000 s and the lease is 6000 s old"));
    }

    #[test]
    fn test_probe_verdict_waiting() {
        let temp = tempfile::tempdir().unwrap();
        let status_path = temp.path().join("claude-code.status.json");
        let status = json!({
            "object": "agent.status",
            "agent_id": "claude-code",
            "state": "waiting",
            "note": "waiting for operator review"
        });
        save_json_file(&status_path, &status).unwrap();

        let file_mtime = fs::metadata(&status_path)
            .unwrap()
            .modified()
            .unwrap()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let now = file_mtime + 10;
        write_test_lease(
            temp.path(),
            "claude-code",
            "held",
            None,
            vec!["antigravity"],
            now,
            5400,
        );

        let req = json!({
            "action": "probe",
            "me": "antigravity"
        });
        let res = handle_probe(temp.path(), &req, now).unwrap();
        assert_eq!(res["verdict"], "waiting");
        assert_eq!(res["take_over"], false);
        assert_eq!(res["next_probe_s"], 600);
        assert_eq!(res["holder_state"], "waiting");
        assert_eq!(res["holder_note"], "waiting for operator review");
    }

    #[test]
    fn test_probe_verdict_alive() {
        let temp = tempfile::tempdir().unwrap();
        let status_path = temp.path().join("claude-code.status.json");
        let status = json!({
            "object": "agent.status",
            "agent_id": "claude-code",
            "state": "working",
            "note": "running lane tasks"
        });
        save_json_file(&status_path, &status).unwrap();

        let file_mtime = fs::metadata(&status_path)
            .unwrap()
            .modified()
            .unwrap()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs();
        let now = file_mtime + 10;
        write_test_lease(
            temp.path(),
            "claude-code",
            "held",
            None,
            vec!["antigravity"],
            now,
            5400,
        );

        let req = json!({
            "action": "probe",
            "me": "antigravity"
        });
        let res = handle_probe(temp.path(), &req, now).unwrap();
        assert_eq!(res["verdict"], "alive");
        assert_eq!(res["take_over"], false);
        assert_eq!(res["next_probe_s"], 600);
        assert_eq!(res["holder_state"], "working");
        assert_eq!(res["holder_note"], "running lane tasks");
    }

    #[test]
    fn test_planted_negatives_and_lease_lifecycle() {
        let temp = tempfile::tempdir().unwrap();
        let now = 1_700_000_000u64;

        // 1. Initial lease: held by claude-code, successors: [antigravity]
        write_test_lease(
            temp.path(),
            "claude-code",
            "held",
            None,
            vec!["antigravity"],
            now,
            5400,
        );

        let mut dummy_state = json!({"agents":{},"messages":[]});

        // Planted Negative Control 1: Non-holder renew refusal
        let renew_unauthorized = json!({
            "action": "lease",
            "subaction": "renew",
            "me": "antigravity"
        });
        let err = handle_lease(
            temp.path(),
            &mut dummy_state,
            &renew_unauthorized,
            "renew",
            now,
        )
        .unwrap_err();
        assert!(err.contains("refused: claude-code holds the lease, not antigravity"));

        // Planted Negative Control 2: Acquire on valid unreleased lease refusal
        let acquire_unreleased = json!({
            "action": "lease",
            "subaction": "acquire",
            "me": "antigravity",
            "verdict": "released"
        });
        let err2 = handle_lease(
            temp.path(),
            &mut dummy_state,
            &acquire_unreleased,
            "acquire",
            now,
        )
        .unwrap_err();
        assert!(err2.contains("refused: acquire on valid unreleased lease"));

        // Planted Negative Control 3: Non-successor acquire refusal
        // First release the lease
        let release_cmd = json!({
            "action": "lease",
            "subaction": "release",
            "me": "claude-code",
            "to": "antigravity"
        });
        let rel_res =
            handle_lease(temp.path(), &mut dummy_state, &release_cmd, "release", now).unwrap();
        assert_eq!(rel_res["ok"], true);
        assert_eq!(rel_res["lease"]["state"], "released");

        let acquire_non_successor = json!({
            "action": "lease",
            "subaction": "acquire",
            "me": "rogue-agent",
            "verdict": "released"
        });
        let err3 = handle_lease(
            temp.path(),
            &mut dummy_state,
            &acquire_non_successor,
            "acquire",
            now,
        )
        .unwrap_err();
        assert!(err3.contains("refused: rogue-agent is not a successor"));

        // Planted Negative Control 4: Error-then-reply is not exhausted
        let transcript_path = temp.path().join("recovered_transcript.jsonl");
        let line1 = json!({
            "type": "assistant",
            "isApiErrorMessage": true,
            "apiError": "rate_limit_exceeded",
            "apiErrorStatus": 429,
            "quotaLimits": {"rateLimitType": "five_hour"}
        });
        let line2 = json!({
            "type": "assistant",
            "isApiErrorMessage": false,
            "message": {
                "content": [{"type": "text", "text": "Recovery successful; turn finished"}]
            }
        });
        fs::write(&transcript_path, format!("{}\n{}\n", line1, line2)).unwrap();

        let status_recovered = json!({
            "object": "agent.status",
            "agent_id": "claude-code",
            "transcript": transcript_path.to_str().unwrap(),
            "state": "working"
        });
        save_json_file(
            &temp.path().join("claude-code.status.json"),
            &status_recovered,
        )
        .unwrap();

        // Make lease held again so probe inspects transcript
        write_test_lease(
            temp.path(),
            "claude-code",
            "held",
            None,
            vec!["antigravity"],
            now,
            5400,
        );
        let probe_req = json!({
            "action": "probe",
            "me": "antigravity"
        });
        let probe_res = handle_probe(temp.path(), &probe_req, now).unwrap();
        // Since latest assistant turn was a successful reply (line 2), verdict is alive, NOT exhausted
        assert_eq!(probe_res["verdict"], "alive");
        assert_ne!(probe_res["verdict"], "exhausted");
        assert_ne!(probe_res["verdict"], "exhausted_briefly");

        // Lifecycle: Valid acquire by successor after graceful release
        let release_again = json!({
            "action": "lease",
            "subaction": "release",
            "me": "claude-code",
            "to": "antigravity"
        });
        handle_lease(
            temp.path(),
            &mut dummy_state,
            &release_again,
            "release",
            now,
        )
        .unwrap();

        let acquire_valid = json!({
            "action": "lease",
            "subaction": "acquire",
            "me": "antigravity",
            "verdict": "released"
        });
        let acq_res = handle_lease(
            temp.path(),
            &mut dummy_state,
            &acquire_valid,
            "acquire",
            now,
        )
        .unwrap();
        assert_eq!(acq_res["ok"], true);
        assert_eq!(acq_res["acquired"], "antigravity");
        assert_eq!(acq_res["from"], "claude-code");
        assert_eq!(acq_res["transitions"], 2);
        assert_eq!(acq_res["lease"]["holder_identity"], "antigravity");
        assert_eq!(acq_res["lease"]["state"], "held");
        // Successors now lists former holder
        assert_eq!(acq_res["lease"]["successors"], json!(["claude-code"]));

        // Attempt acquire when already held
        let err4 = handle_lease(
            temp.path(),
            &mut dummy_state,
            &acquire_valid,
            "acquire",
            now,
        )
        .unwrap_err();
        assert!(err4.contains("refused: already held by you"));

        // Renew by new holder succeeds
        let renew_cmd = json!({
            "action": "lease",
            "subaction": "renew",
            "me": "antigravity",
            "note": "working fine"
        });
        let ren_res =
            handle_lease(temp.path(), &mut dummy_state, &renew_cmd, "renew", now + 50).unwrap();
        assert_eq!(ren_res["ok"], true);
        assert_eq!(ren_res["lease"]["renew_time"], format_iso(now + 50));

        // Status update and get
        let status_set = json!({
            "agent_id": "antigravity",
            "state": "working",
            "note": "standing by"
        });
        handle_status(temp.path(), &mut dummy_state, &status_set, now + 50).unwrap();
        let status_get = json!({
            "agent_id": "antigravity",
            "subaction": "get"
        });
        let st = handle_status(temp.path(), &mut dummy_state, &status_get, now + 50).unwrap();
        assert_eq!(st["state"], "working");
        assert_eq!(st["note"], "standing by");
    }
}
