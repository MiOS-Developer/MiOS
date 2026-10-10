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

// Coordinator succession: lease, status and journal beside the relay registry;
// identities are relay registrations, numbers come from [mcp.agents.succession].

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

/// ISO-8601 / RFC 3339 to Unix seconds. `Z`, `+HH:MM`, `-HHMM`, `+HH` and
/// fractional seconds are honoured; a missing offset is UTC, as probe.ps1
/// parses with AssumeUniversal.
fn parse_iso(s: &str) -> Option<u64> {
    let s = s.trim();
    let b = s.as_bytes();
    if b.len() < 19
        || b[4] != b'-'
        || b[7] != b'-'
        || !matches!(b[10], b'T' | b't' | b' ')
        || b[13] != b':'
        || b[16] != b':'
    {
        return None;
    }
    let digits = |range: std::ops::Range<usize>| -> Option<i64> {
        if b[range.clone()].iter().all(u8::is_ascii_digit) {
            s[range].parse().ok()
        } else {
            None
        }
    };
    let (year, month, day) = (digits(0..4)?, digits(5..7)?, digits(8..10)?);
    let (hour, min, sec) = (digits(11..13)?, digits(14..16)?, digits(17..19)?);
    if !(1..=12).contains(&month) || !(1..=31).contains(&day) || hour > 23 || min > 59 || sec > 60 {
        return None;
    }
    let mut rest = &s[19..];
    if let Some(fraction) = rest.strip_prefix('.') {
        let n = fraction.bytes().take_while(u8::is_ascii_digit).count();
        if n == 0 {
            return None;
        }
        rest = &fraction[n..];
    }
    let offset = match rest {
        "" | "Z" | "z" => 0,
        _ => {
            let r = rest.as_bytes();
            let sign = match r[0] {
                b'+' => 1,
                b'-' => -1,
                _ => return None,
            };
            let (h, m): (&[u8], &[u8]) = match r.len() {
                3 => (&r[1..3], &b"00"[..]),
                5 => (&r[1..3], &r[3..5]),
                6 if r[3] == b':' => (&r[1..3], &r[4..6]),
                _ => return None,
            };
            if !h.iter().chain(m).all(u8::is_ascii_digit) {
                return None;
            }
            let h = i64::from(h[0] - b'0') * 10 + i64::from(h[1] - b'0');
            let m = i64::from(m[0] - b'0') * 10 + i64::from(m[1] - b'0');
            if h > 23 || m > 59 {
                return None;
            }
            sign * (h * 3600 + m * 60)
        }
    };
    let y = if month <= 2 { year - 1 } else { year };
    let era = y.div_euclid(400);
    let yoe = y - era * 400;
    let mp = if month > 2 { month - 3 } else { month + 9 };
    let doy = (153 * mp + 2) / 5 + day - 1;
    let doe = yoe * 365 + yoe / 4 - yoe / 100 + doy;
    let days = era * 146097 + doe - 719468;
    u64::try_from(days * 86400 + hour * 3600 + min * 60 + sec - offset).ok()
}

/// A Unix time as Claude Code records `resetsAt`: an integer, a float or a
/// numeric string. Zero or negative means "no reset time".
fn epoch_seconds(value: &Value) -> Option<u64> {
    value
        .as_u64()
        .or_else(|| {
            value
                .as_f64()
                .filter(|f| f.is_finite() && *f > 0.0)
                .map(|f| f as u64)
        })
        .or_else(|| {
            value
                .as_str()
                .and_then(|s| s.trim().parse::<f64>().ok())
                .filter(|f| f.is_finite() && *f > 0.0)
                .map(|f| f as u64)
        })
        .filter(|t| *t > 0)
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

/// `[mcp.agents.succession]`: every number and name the coordinator lease uses.
struct Succession {
    lease_duration_s: u64,
    renew_interval_s: u64,
    probe_interval_s: u64,
    retry_probe_s: u64,
    min_outage_s: u64,
    tail_lines: usize,
    preferred_holder: String,
    successors: Vec<String>,
}

fn succession_config(request: &Value) -> Result<Succession, String> {
    let cfg = &request["config"]["succession"];
    let invalid = |key: &str| format!("invalid [mcp.agents.succession].{key}");
    let number = |key: &str| {
        cfg[key]
            .as_u64()
            .filter(|n| *n > 0)
            .ok_or_else(|| invalid(key))
    };
    let name = |value: &Value, key: &str| -> Result<String, String> {
        let id = value.as_str().ok_or_else(|| invalid(key))?;
        path_identity(id).map_err(|_| invalid(key))?;
        Ok(id.to_string())
    };
    let s = Succession {
        lease_duration_s: number("lease_duration_s")?,
        renew_interval_s: number("renew_interval_s")?,
        probe_interval_s: number("probe_interval_s")?,
        retry_probe_s: number("retry_probe_s")?,
        min_outage_s: number("min_outage_s")?,
        tail_lines: number("tail_lines")? as usize,
        preferred_holder: name(&cfg["preferred_holder"], "preferred_holder")?,
        successors: cfg["successors"]
            .as_array()
            .ok_or_else(|| invalid("successors"))?
            .iter()
            .map(|v| name(v, "successors"))
            .collect::<Result<_, _>>()?,
    };
    // client-go leader election: leaseDuration > renewDeadline > retryPeriod x 1.2.
    if s.lease_duration_s <= s.renew_interval_s
        || s.renew_interval_s.saturating_mul(5) <= s.probe_interval_s.saturating_mul(6)
    {
        return Err("[mcp.agents.succession] must keep lease_duration_s > renew_interval_s > 1.2 x probe_interval_s".into());
    }
    Ok(s)
}

/// An identity that names a file beneath the relay state directory: the relay
/// identifier alphabet, an alphanumeric first byte, and never `..`.
fn path_identity(value: &str) -> Result<(), String> {
    identifier(value)?;
    if !value.as_bytes()[0].is_ascii_alphanumeric() || value.contains("..") {
        return Err("invalid agent identity".into());
    }
    Ok(())
}

/// Resolve `name` beneath the state directory and confirm it stays there.
fn contained(directory: &Path, name: &str) -> Result<PathBuf, String> {
    let root = fs::canonicalize(directory).map_err(|e| e.to_string())?;
    let path = root.join(name);
    if path.parent() != Some(root.as_path()) || path.is_symlink() {
        return Err("path escapes the agent state directory".into());
    }
    Ok(path)
}

fn status_path(directory: &Path, agent_id: &str) -> Result<PathBuf, String> {
    path_identity(agent_id)?;
    contained(directory, &format!("{agent_id}.status.json"))
}

/// Read a JSON object written by this relay or by Windows PowerShell 5.1,
/// which prefixes UTF-8 with a byte-order mark.
fn read_json(path: &Path) -> Result<Option<Value>, String> {
    let bytes = match fs::read(path) {
        Ok(bytes) => bytes,
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(e) => return Err(e.to_string()),
    };
    let text = String::from_utf8(bytes).map_err(|e| e.to_string())?;
    let value: Value = serde_json::from_str(text.trim_start_matches('\u{feff}'))
        .map_err(|e| format!("{}: {e}", path.display()))?;
    if !value.is_object() {
        return Err(format!("{} is not a JSON object", path.display()));
    }
    Ok(Some(value))
}

/// The last `count` lines of a transcript, read backwards from its end: a
/// long session's head is never loaded or decoded.
fn read_tail(path: &Path, count: usize) -> Result<Vec<String>, String> {
    use std::io::{Seek, SeekFrom};
    const BLOCK: u64 = 64 * 1024;
    let mut file = fs::File::open(path).map_err(|e| e.to_string())?;
    let mut start = file.metadata().map_err(|e| e.to_string())?.len();
    let mut buffer: Vec<u8> = Vec::new();
    let mut newlines = 0;
    while start > 0 && newlines <= count {
        let from = start.saturating_sub(BLOCK);
        let mut chunk = vec![0; (start - from) as usize];
        file.seek(SeekFrom::Start(from))
            .map_err(|e| e.to_string())?;
        file.read_exact(&mut chunk).map_err(|e| e.to_string())?;
        newlines += chunk.iter().filter(|b| **b == b'\n').count();
        chunk.extend_from_slice(&buffer);
        buffer = chunk;
        start = from;
    }
    let text = String::from_utf8_lossy(&buffer);
    let mut lines: Vec<&str> = text.lines().collect();
    if start > 0 && !lines.is_empty() {
        lines.remove(0); // a partial line at the block boundary
    }
    let skip = lines.len().saturating_sub(count);
    Ok(lines[skip..].iter().map(|l| l.to_string()).collect())
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

/// Append one `agent.event` to the private journal. Callers pass only public
/// fields; a relay token is never among them.
fn journal(directory: &Path, event_type: &str, fields: Value, now: u64) -> Result<(), String> {
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
    let mut options = OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options
        .open(contained(directory, "journal.jsonl")?)
        .map_err(|e| e.to_string())?;
    writeln!(file, "{rec}").map_err(|e| e.to_string())
}

/// Lease identity is relay registration: the caller proves `agent_id` with the
/// token it registered. Only the token's digest is ever stored.
fn authenticate<'a>(state: &Value, request: &'a Value) -> Result<&'a str, String> {
    let id = string(request, "agent_id")?;
    path_identity(id)?;
    let token = string(request, "token")?;
    if token.len() < 32 || token.len() > 256 {
        return Err("invalid agent lease token".into());
    }
    let agent = &state["agents"][id];
    if agent["token_hash"].as_str() != Some(digest(token).as_str()) || agent["closed"] == true {
        return Err("refused: agent_id and token do not match a live relay registration".into());
    }
    Ok(id)
}

/// An optional caller string, bounded by `[mcp.agents].max_message_bytes`.
fn bounded<'a>(request: &'a Value, field: &str) -> Result<Option<&'a str>, String> {
    let max = request["config"]["max_message_bytes"]
        .as_u64()
        .filter(|n| *n > 0)
        .ok_or("invalid [mcp.agents].max_message_bytes")?;
    match request.get(field) {
        None | Some(Value::Null) => Ok(None),
        Some(Value::String(s)) if s.len() as u64 <= max => Ok(Some(s)),
        Some(_) => Err(format!("invalid or oversized {field}")),
    }
}

fn load_lease(directory: &Path, s: &Succession) -> Result<Value, String> {
    Ok(match read_json(&contained(directory, "lease.json")?)? {
        Some(lease) => lease,
        // A fresh install: the SSOT names the identities and nobody holds the
        // lease yet. The first eligible caller's acquire persists it.
        None => json!({
            "object": "agent.lease",
            "holder_identity": Value::Null,
            "state": "released",
            "handoff_to": Value::Null,
            "lease_duration_seconds": s.lease_duration_s,
            "acquire_time": Value::Null,
            "renew_time": Value::Null,
            "lease_transitions": 0,
            "preferred_holder": s.preferred_holder,
            "successors": s.successors,
        }),
    })
}

/// Identities a persisted lease names. Registration cleanup must not free
/// them for another token to claim.
fn lease_identities(directory: &Path) -> Result<Vec<String>, String> {
    let Some(lease) = read_json(&contained(directory, "lease.json")?)? else {
        return Ok(Vec::new());
    };
    let mut ids: Vec<String> = ["holder_identity", "preferred_holder", "handoff_to"]
        .iter()
        .filter_map(|k| lease[*k].as_str().map(str::to_owned))
        .collect();
    ids.extend(
        lease["successors"]
            .as_array()
            .into_iter()
            .flatten()
            .filter_map(Value::as_str)
            .map(str::to_owned),
    );
    Ok(ids)
}

fn require_holder(lease: &Value, caller: &str) -> Result<(), String> {
    match lease["holder_identity"].as_str() {
        Some(holder) if holder == caller => Ok(()),
        Some(holder) => Err(format!("refused: {holder} holds the lease, not {caller}")),
        None => Err("refused: nobody holds the lease".into()),
    }
}

/// The caller's own status with `updated_at`, `state` and `note` applied and
/// validated, not yet written. Lease changes refresh it so the probe always
/// has activity evidence.
fn status_update(
    directory: &Path,
    agent_id: &str,
    request: &Value,
    t: &str,
) -> Result<(PathBuf, Value), String> {
    let path = status_path(directory, agent_id)?;
    let mut st = read_json(&path)?
        .unwrap_or_else(|| json!({"object": "agent.status", "agent_id": agent_id}));
    st["updated_at"] = json!(t);
    if let Some(v) = request.get("state") {
        let v = v.as_str().ok_or("invalid state")?;
        identifier(v)?;
        st["state"] = json!(v);
    }
    if let Some(v) = bounded(request, "note")? {
        st["note"] = json!(v);
    }
    Ok((path, st))
}

fn handle_lease(
    directory: &Path,
    state: &Value,
    request: &Value,
    now: u64,
) -> Result<Value, String> {
    let s = succession_config(request)?;
    let subaction = request["subaction"].as_str().unwrap_or("get");
    let mut lease = load_lease(directory, &s)?;
    if subaction == "get" {
        return Ok(lease);
    }
    let caller = authenticate(state, request)?;
    let note = bounded(request, "note")?;
    let t = format_iso(now);
    let path = contained(directory, "lease.json")?;
    match subaction {
        "acquire" => {
            // The relay probes; a caller's own verdict is never read.
            let probe = evaluate_probe(directory, &lease, caller, &s, now)?;
            if probe["take_over"] != true {
                return Err(format!(
                    "refused: the relay's probe says {} ({})",
                    probe["verdict"].as_str().unwrap_or("unknown"),
                    probe["reason"].as_str().unwrap_or("")
                ));
            }
            // A fresh lease has no previous holder.
            let prev: Option<String> = lease["holder_identity"].as_str().map(str::to_owned);
            let transitions = lease["lease_transitions"].as_u64().unwrap_or(0) + 1;
            // The previous holder becomes the first successor.
            let successors: Vec<Value> = prev
                .iter()
                .map(String::as_str)
                .chain(
                    lease["successors"]
                        .as_array()
                        .into_iter()
                        .flatten()
                        .filter_map(Value::as_str)
                        .filter(|x| Some(*x) != prev.as_deref() && *x != caller),
                )
                .map(|x| json!(x))
                .collect();
            lease["object"] = json!("agent.lease");
            lease["holder_identity"] = json!(caller);
            lease["state"] = json!("held");
            lease["acquire_time"] = json!(t);
            lease["renew_time"] = json!(t);
            lease["lease_transitions"] = json!(transitions);
            lease["handoff_to"] = Value::Null;
            lease["successors"] = json!(successors);
            let (status_file, st) = status_update(directory, caller, request, &t)?;
            save_json_file(&path, &lease)?;
            save_json_file(&status_file, &st)?;
            journal(
                directory,
                "lease.acquired",
                json!({"agent_id": caller, "previous_holder": prev, "verdict": probe["verdict"], "note": note}),
                now,
            )?;
            Ok(json!({
                "ok": true, "acquired": caller, "from": prev, "verdict": probe["verdict"],
                "transitions": transitions, "next_renew_s": s.renew_interval_s, "lease": lease
            }))
        }
        "renew" => {
            require_holder(&lease, caller)?;
            lease["renew_time"] = json!(t);
            lease["state"] = json!("held");
            let (status_file, st) = status_update(directory, caller, request, &t)?;
            save_json_file(&path, &lease)?;
            save_json_file(&status_file, &st)?;
            journal(
                directory,
                "lease.renewed",
                json!({"agent_id": caller, "state": st.get("state"), "note": note}),
                now,
            )?;
            Ok(
                json!({"ok": true, "renewed": t, "next_renew_s": s.renew_interval_s, "lease": lease}),
            )
        }
        "release" => {
            require_holder(&lease, caller)?;
            let to = bounded(request, "handoff_to")?;
            if let Some(target) = to {
                path_identity(target)?;
                let listed = lease["successors"]
                    .as_array()
                    .is_some_and(|a| a.iter().any(|v| v.as_str() == Some(target)));
                if !listed && lease["preferred_holder"].as_str() != Some(target) {
                    return Err(format!(
                        "refused: {target} is neither a successor nor the preferred holder"
                    ));
                }
            }
            lease["state"] = json!("released");
            lease["handoff_to"] = json!(to);
            lease["renew_time"] = json!(t);
            save_json_file(&path, &lease)?;
            journal(
                directory,
                "lease.released",
                json!({"agent_id": caller, "handoff_to": to, "note": note}),
                now,
            )?;
            Ok(json!({"ok": true, "released_to": to, "lease": lease}))
        }
        _ => Err(format!("unknown lease action: {subaction}")),
    }
}

fn handle_status(
    directory: &Path,
    state: &Value,
    request: &Value,
    now: u64,
) -> Result<Value, String> {
    const FIELDS: &[&str] = &[
        "state",
        "note",
        "transcript",
        "activity_dirs",
        "usage",
        "role",
        "kind",
        "harness",
        "handoff",
    ];
    let agent_id = string(request, "agent_id")?;
    let path = status_path(directory, agent_id)?;
    let publish = match request["subaction"].as_str() {
        Some("publish") => true,
        Some("get") => false,
        None => FIELDS.iter().any(|f| request.get(*f).is_some()),
        Some(other) => return Err(format!("unknown status action: {other}")),
    };
    if !publish {
        return Ok(read_json(&path)?.unwrap_or_else(
            || json!({"object": "agent.status", "agent_id": agent_id, "state": "unknown"}),
        ));
    }
    // Status steers the probe (transcript, state), so only its owner writes it.
    authenticate(state, request)?;
    let t = format_iso(now);
    let (path, mut st) = status_update(directory, agent_id, request, &t)?;
    for key in ["role", "kind", "harness"] {
        if let Some(v) = request[key].as_str() {
            identifier(v)?;
            st[key] = json!(v);
        }
    }
    if let Some(v) = bounded(request, "handoff")? {
        st["handoff"] = json!(v);
    }
    let absolute = |v: &Value| -> Result<String, String> {
        let p = v.as_str().ok_or("activity paths must be strings")?;
        if p.contains('\0') || !resolve_host_path(p).is_absolute() {
            return Err("activity paths must be absolute".into());
        }
        Ok(p.to_string())
    };
    if let Some(v) = request.get("transcript") {
        bounded(request, "transcript")?;
        st["transcript"] = json!(absolute(v)?);
    }
    if let Some(v) = request.get("activity_dirs") {
        let dirs = v.as_array().ok_or("activity_dirs must be an array")?;
        st["activity_dirs"] = json!(dirs.iter().map(absolute).collect::<Result<Vec<_>, _>>()?);
    }
    if let Some(v) = request.get("usage") {
        st["usage"] = json!(v.as_object().ok_or("usage must be an object")?);
    }
    save_json_file(&path, &st)?;
    Ok(st)
}

fn handle_probe(directory: &Path, request: &Value, now: u64) -> Result<Value, String> {
    let s = succession_config(request)?;
    let me = string(request, "agent_id")?;
    path_identity(me)?;
    let lease = load_lease(directory, &s)?;
    evaluate_probe(directory, &lease, me, &s, now)
}

/// The ADR-0029 verdict for `me`, from the lease and the holder's own
/// evidence. Read-only; the holder is read only from the lease.
fn evaluate_probe(
    directory: &Path,
    lease: &Value,
    me: &str,
    s: &Succession,
    now: u64,
) -> Result<Value, String> {
    let holder = match &lease["holder_identity"] {
        Value::Null => None,
        Value::String(h) if path_identity(h).is_ok() => Some(h.as_str()),
        _ => return Err("invalid lease holder_identity".into()),
    };
    let duration = lease["lease_duration_seconds"]
        .as_u64()
        .unwrap_or(s.lease_duration_s) as i64;
    let released = lease["state"] == "released";
    let listed = lease["successors"]
        .as_array()
        .is_some_and(|a| a.iter().any(|v| v.as_str() == Some(me)));
    // A listed successor takes over; the preferred holder takes a released
    // lease back, never by seizure.
    let may_take = listed || (released && lease["preferred_holder"].as_str() == Some(me));
    let emit = |verdict: &str, take_over: bool, reason: String, next: u64, extra: Value| {
        let mut o = json!({
            "object": "agent.probe", "probed_at": format_iso(now), "me": me, "holder": holder,
            "verdict": verdict, "take_over": take_over, "reason": reason, "next_probe_s": next
        });
        if let Value::Object(map) = extra {
            for (k, v) in map {
                o[k] = v;
            }
        }
        Ok(o)
    };

    if holder == Some(me) {
        return emit(
            "held_by_me",
            false,
            "you already hold the coordinator lease".into(),
            s.probe_interval_s,
            json!({}),
        );
    }
    if released {
        let to = lease["handoff_to"].as_str().filter(|t| !t.is_empty());
        let ok = may_take && to.is_none_or(|t| t == me);
        let reason = format!(
            "holder released the lease{}",
            to.map(|t| format!(" to {t}")).unwrap_or_default()
        );
        return emit("released", ok, reason, s.retry_probe_s, json!({}));
    }
    let holder = holder.ok_or("invalid lease: held without a holder_identity")?;

    let status_file = status_path(directory, holder)?;
    let status = read_json(&status_file)?;
    let transcript = status
        .as_ref()
        .and_then(|st| st["transcript"].as_str())
        .map(resolve_host_path);

    // Newest activity across the transcript, the activity directories and the status file.
    let mut paths: Vec<PathBuf> = Vec::new();
    if let Some(t) = transcript.as_ref().filter(|t| t.exists()) {
        paths.push(t.clone());
    }
    for dir in status
        .as_ref()
        .and_then(|st| st["activity_dirs"].as_array())
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .map(resolve_host_path)
    {
        if let Ok(entries) = fs::read_dir(dir) {
            paths.extend(entries.flatten().map(|e| e.path()).filter(|p| {
                p.is_file() && p.extension().and_then(|x| x.to_str()) == Some("jsonl")
            }));
        }
    }
    if status_file.exists() {
        paths.push(status_file);
    }
    let newest = paths
        .iter()
        .filter_map(|p| fs::metadata(p).and_then(|m| m.modified()).ok())
        .filter_map(|t| t.duration_since(UNIX_EPOCH).ok())
        .map(|d| d.as_secs())
        .max();
    let idle: Option<i64> = newest.map(|t| now as i64 - t as i64);

    // Exhaustion: the holder's latest own assistant entry is an API error.
    let mut blocked: Option<(Value, Option<u64>)> = None;
    if let Some(t) = transcript.as_ref().filter(|t| t.is_file()) {
        for line in read_tail(t, s.tail_lines)?.iter().rev() {
            let Ok(e) = serde_json::from_str::<Value>(line.trim().trim_start_matches('\u{feff}'))
            else {
                continue;
            };
            // Sidechain entries are subagent turns, not the holder's own.
            if e["type"] != "assistant" || e["isSidechain"] == true {
                continue;
            }
            if e["isApiErrorMessage"] == true {
                let q = &e["quotaLimits"];
                let resets = epoch_seconds(&q["resetsAt"]);
                let text = e["message"]["content"]
                    .as_array()
                    .and_then(|arr| arr.iter().find(|item| item["type"] == "text"))
                    .map(|item| item["text"].clone())
                    .unwrap_or(Value::Null);
                blocked = Some((
                    json!({
                        "error": e["error"], "api_error": e["apiError"], "http_status": e["apiErrorStatus"],
                        "limit_type": q["rateLimitType"], "overage": q["overageDisabledReason"],
                        "resets_at": resets.map(format_iso), "at": e["timestamp"], "text": text
                    }),
                    resets,
                ));
            }
            break; // only the latest assistant entry decides
        }
    }
    if let Some((b, resets)) = blocked {
        let wait = resets.map(|r| r as i64 - now as i64);
        if let Some(wait) = wait.filter(|w| *w > 0 && *w as u64 <= s.min_outage_s) {
            return emit(
                "exhausted_briefly",
                false,
                format!("holder is rate-limited but resets in {wait} s; keep working as a worker"),
                wait as u64 + s.retry_probe_s,
                json!({"blocked": b, "idle_s": idle}),
            );
        }
        let reason = format!(
            "holder's latest turn is an API error ({}; {})",
            b["api_error"].as_str().unwrap_or(""),
            b["limit_type"].as_str().unwrap_or("")
        );
        return emit(
            "exhausted",
            may_take,
            reason,
            s.probe_interval_s,
            json!({"blocked": b, "idle_s": idle}),
        );
    }

    // Silence: no activity and no renewal for longer than the lease's own duration.
    let lease_age = lease["renew_time"]
        .as_str()
        .and_then(parse_iso)
        .map(|r| now as i64 - r as i64);
    let show = |v: Option<i64>| v.map(|n| n.to_string()).unwrap_or_default();
    if let Some(idle_s) = idle {
        if idle_s > duration && lease_age.is_none_or(|a| a > duration) {
            return emit(
                "silent",
                may_take,
                format!(
                    "no holder activity for {idle_s} s and the lease is {} s old (limit {duration} s)",
                    show(lease_age)
                ),
                s.probe_interval_s,
                json!({"idle_s": idle_s, "lease_age_s": lease_age}),
            );
        }
    }

    let holder_state = status.as_ref().and_then(|st| st["state"].as_str());
    let verdict = if holder_state == Some("waiting") {
        "waiting"
    } else {
        "alive"
    };
    emit(
        verdict,
        false,
        format!(
            "holder is active (idle {} s, lease age {} s)",
            show(idle),
            show(lease_age)
        ),
        s.probe_interval_s,
        json!({
            "idle_s": idle, "lease_age_s": lease_age, "holder_state": holder_state,
            "holder_note": status.as_ref().map(|st| st["note"].clone()).unwrap_or(Value::Null)
        }),
    )
}

fn dispatch(state: &mut Value, request: &Value, now: u64) -> Result<Value, String> {
    dispatch_relay(state, request, now, &[])
}

/// The relay's mailbox actions. `held` names identities a persisted
/// coordinator lease references; registration cleanup never retires them.
fn dispatch_relay(
    state: &mut Value,
    request: &Value,
    now: u64,
    held: &[String],
) -> Result<Value, String> {
    let action = string(request, "action")?;

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
        // ends of pending messages and every identity the coordinator lease
        // names; retire only unreferenced dormant identities.
        let protected: std::collections::HashSet<String> = state["messages"]
            .as_array()
            .unwrap()
            .iter()
            .filter(|m| m["status"] == "queued")
            .flat_map(|m| [m["from"].as_str(), m["to"].as_str()])
            .flatten()
            .map(str::to_owned)
            .chain(held.iter().cloned())
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
    // Succession files resolve beneath this registry's own locked directory;
    // a request never chooses where they live.
    let action = request["action"].as_str().unwrap_or("");
    let result = match action {
        "lease" => handle_lease(directory, &state, request, now),
        "status" => handle_status(directory, &state, request, now),
        "probe" => handle_probe(directory, request, now),
        "register" => lease_identities(directory)
            .and_then(|held| dispatch_relay(&mut state, request, now, &held)),
        _ => dispatch(&mut state, request, now),
    }
    .map_err(std::io::Error::other)?;
    if !matches!(action, "list" | "lease" | "status" | "probe") {
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
}

// Lease, status and probe through `run`; each `planted_*` test plants one
// review defect and fails on the code before its fix.
#[cfg(test)]
mod succession {
    use super::*;

    fn config() -> Value {
        json!({"lease_s":60,"mailbox_retention_s":120,"queue_offline":true,"max_agents":8,
            "max_pending":8,"max_message_bytes":256,"max_receipts":8,
            "succession":{"lease_duration_s":5400,"renew_interval_s":1800,"probe_interval_s":600,
                "retry_probe_s":60,"min_outage_s":900,"tail_lines":80,
                "preferred_holder":"claude-code","successors":["antigravity"]}})
    }
    fn merge(target: &mut Value, extra: Value) {
        if let Value::Object(map) = extra {
            for (k, v) in map {
                target[k] = v;
            }
        }
    }
    fn call_with(dir: &Path, mut request: Value, config: Value) -> Result<Value, String> {
        request["config"] = config;
        run(dir, &request).map_err(|e| e.to_string())
    }
    fn call(dir: &Path, request: Value) -> Result<Value, String> {
        call_with(dir, request, config())
    }
    fn token(id: &str) -> String {
        format!("{id}-relay-token-{}", "0123456789abcdef".repeat(2))
    }
    fn register(dir: &Path, id: &str) -> String {
        let t = token(id);
        call(
            dir,
            json!({"action":"register","agent_id":id,"kind":"codex","token":t}),
        )
        .unwrap();
        t
    }
    fn lease(dir: &Path, sub: &str, id: &str, extra: Value) -> Result<Value, String> {
        let mut request = json!({"action":"lease","subaction":sub,"agent_id":id,"token":token(id)});
        merge(&mut request, extra);
        call(dir, request)
    }
    fn probe(dir: &Path, me: &str) -> Result<Value, String> {
        call(dir, json!({"action":"probe","agent_id":me}))
    }
    fn now() -> u64 {
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap()
            .as_secs()
    }
    fn age(path: &Path, secs: u64) {
        let file = fs::File::options().write(true).open(path).unwrap();
        file.set_modified(SystemTime::now() - Duration::from_secs(secs))
            .unwrap();
    }
    fn plant_lease(dir: &Path, holder: &str, state: &str, renewed_s_ago: u64, extra: Value) {
        let mut l = json!({"object":"agent.lease","holder_identity":holder,"state":state,
            "handoff_to":null,"lease_duration_seconds":5400,
            "acquire_time":format_iso(now() - 20000),"renew_time":format_iso(now() - renewed_s_ago),
            "lease_transitions":1,"preferred_holder":"claude-code","successors":["antigravity"]});
        merge(&mut l, extra);
        fs::create_dir_all(dir).unwrap();
        fs::write(dir.join("lease.json"), l.to_string()).unwrap();
    }
    fn plant_status(dir: &Path, holder: &str, extra: Value, age_s: u64) -> PathBuf {
        let mut st = json!({"object":"agent.status","agent_id":holder,"state":"working"});
        merge(&mut st, extra);
        let path = dir.join(format!("{holder}.status.json"));
        fs::create_dir_all(dir).unwrap();
        fs::write(&path, st.to_string()).unwrap();
        age(&path, age_s);
        path
    }
    fn plant_transcript(dir: &Path, holder: &str, lines: &[Value]) -> PathBuf {
        let path = dir.join(format!("{holder}-transcript.jsonl"));
        let text: String = lines.iter().map(|l| format!("{l}\n")).collect();
        fs::write(&path, text).unwrap();
        plant_status(
            dir,
            holder,
            json!({"transcript": path.to_str().unwrap()}),
            0,
        );
        path
    }
    fn api_error(resets_at: Value) -> Value {
        json!({"type":"assistant","isApiErrorMessage":true,"apiError":"usage_limit_reached",
            "error":"rate_limit","apiErrorStatus":429,"quotaLimits":{"status":"rejected",
            "rateLimitType":"five_hour","resetsAt":resets_at,"overageDisabledReason":"out_of_credits"},
            "message":{"content":[{"type":"text","text":"You've hit your session limit"}]}})
    }
    fn reply() -> Value {
        json!({"type":"assistant","message":{"content":[{"type":"text","text":"turn finished"}]}})
    }
    fn holder_of(dir: &Path) -> Value {
        serde_json::from_slice::<Value>(&fs::read(dir.join("lease.json")).unwrap()).unwrap()
            ["holder_identity"]
            .clone()
    }
    fn registry() -> (tempfile::TempDir, PathBuf) {
        let temp = tempfile::tempdir().unwrap();
        let dir = temp.path().join("registry");
        (temp, dir)
    }

    #[test]
    fn verdict_held_by_me_and_released_targeting() {
        let (_t, dir) = registry();
        plant_lease(
            &dir,
            "antigravity",
            "held",
            5,
            json!({"successors":["claude-code"]}),
        );
        let held = probe(&dir, "antigravity").unwrap();
        assert_eq!(
            (held["verdict"].as_str(), held["take_over"].as_bool()),
            (Some("held_by_me"), Some(false))
        );
        plant_lease(
            &dir,
            "claude-code",
            "released",
            5,
            json!({"handoff_to":"antigravity"}),
        );
        let mine = probe(&dir, "antigravity").unwrap();
        assert_eq!(
            (mine["verdict"].as_str(), mine["take_over"].as_bool()),
            (Some("released"), Some(true))
        );
        assert_eq!(mine["next_probe_s"], 60);
        assert_eq!(mine["reason"], "holder released the lease to antigravity");
        // Planted: released to another successor, or to anyone but I am not listed.
        plant_lease(
            &dir,
            "claude-code",
            "released",
            5,
            json!({"handoff_to":"codex","successors":["antigravity","codex"]}),
        );
        assert_eq!(probe(&dir, "antigravity").unwrap()["take_over"], false);
        plant_lease(
            &dir,
            "claude-code",
            "released",
            5,
            json!({"successors":["codex"]}),
        );
        assert_eq!(probe(&dir, "antigravity").unwrap()["take_over"], false);
    }

    #[test]
    fn verdict_exhausted_briefly_exhausted_and_recovered() {
        let (_t, dir) = registry();
        plant_lease(&dir, "claude-code", "held", 5, json!({}));
        plant_transcript(
            &dir,
            "claude-code",
            &[reply(), api_error(json!(now() + 3600))],
        );
        let long = probe(&dir, "antigravity").unwrap();
        assert_eq!(
            (long["verdict"].as_str(), long["take_over"].as_bool()),
            (Some("exhausted"), Some(true))
        );
        assert_eq!(long["blocked"]["api_error"], "usage_limit_reached");
        assert_eq!(long["blocked"]["limit_type"], "five_hour");
        plant_transcript(&dir, "claude-code", &[api_error(json!(now() + 300))]);
        let brief = probe(&dir, "antigravity").unwrap();
        assert_eq!(
            (brief["verdict"].as_str(), brief["take_over"].as_bool()),
            (Some("exhausted_briefly"), Some(false))
        );
        let next = brief["next_probe_s"].as_u64().unwrap();
        assert!((350..=360).contains(&next), "{brief}");
        // Planted: an error followed by a normal turn is not exhaustion.
        plant_transcript(
            &dir,
            "claude-code",
            &[api_error(json!(now() + 3600)), reply()],
        );
        assert_eq!(probe(&dir, "antigravity").unwrap()["verdict"], "alive");
    }

    #[test]
    fn sidechain_error_does_not_decide() {
        let (_t, dir) = registry();
        plant_lease(&dir, "claude-code", "held", 5, json!({}));
        let mut subagent = api_error(json!(now() + 3600));
        subagent["isSidechain"] = json!(true);
        plant_transcript(&dir, "claude-code", &[reply(), subagent]);
        let res = probe(&dir, "antigravity").unwrap();
        assert_eq!(
            (res["verdict"].as_str(), res["take_over"].as_bool()),
            (Some("alive"), Some(false)),
            "{res}"
        );
    }

    #[test]
    fn verdict_silent_waiting_and_alive() {
        let (_t, dir) = registry();
        plant_lease(&dir, "claude-code", "held", 6000, json!({}));
        let status = plant_status(&dir, "claude-code", json!({}), 6000);
        let silent = probe(&dir, "antigravity").unwrap();
        assert_eq!(
            (silent["verdict"].as_str(), silent["take_over"].as_bool()),
            (Some("silent"), Some(true)),
            "{silent}"
        );
        // Planted: a recent renewal alone keeps the holder.
        plant_lease(&dir, "claude-code", "held", 5, json!({}));
        assert_eq!(probe(&dir, "antigravity").unwrap()["verdict"], "alive");
        // Planted: recent activity alone keeps the holder.
        plant_lease(&dir, "claude-code", "held", 6000, json!({}));
        age(&status, 10);
        assert_eq!(probe(&dir, "antigravity").unwrap()["verdict"], "alive");
        plant_status(
            &dir,
            "claude-code",
            json!({"state":"waiting","note":"operator review"}),
            10,
        );
        let waiting = probe(&dir, "antigravity").unwrap();
        assert_eq!(
            (waiting["verdict"].as_str(), waiting["take_over"].as_bool()),
            (Some("waiting"), Some(false))
        );
        assert_eq!(waiting["holder_note"], "operator review");
    }

    #[test]
    fn planted_seizure_of_a_live_lease_is_refused() {
        let (_t, dir) = registry();
        register(&dir, "claude-code");
        register(&dir, "antigravity");
        plant_lease(&dir, "claude-code", "held", 5, json!({}));
        let status = plant_status(&dir, "claude-code", json!({}), 0);
        let seized = lease(&dir, "acquire", "antigravity", json!({"verdict":"silent"}));
        assert!(
            seized.as_ref().is_err_and(|e| e.contains("refused")),
            "{seized:?}"
        );
        assert_eq!(holder_of(&dir), "claude-code");
        // Once the relay itself observes silence, the same successor acquires.
        plant_lease(&dir, "claude-code", "held", 6000, json!({}));
        age(&status, 6000);
        let acquired = lease(&dir, "acquire", "antigravity", json!({})).unwrap();
        assert_eq!(
            (acquired["verdict"].as_str(), acquired["from"].as_str()),
            (Some("silent"), Some("claude-code"))
        );
        assert_eq!(acquired["lease"]["successors"], json!(["claude-code"]));
        assert_eq!(acquired["lease"]["lease_transitions"], 2);
        assert_eq!(holder_of(&dir), "antigravity");
        let again = lease(&dir, "acquire", "antigravity", json!({}));
        assert!(again.is_err_and(|e| e.contains("held_by_me")));
        let journal = fs::read_to_string(dir.join("journal.jsonl")).unwrap();
        assert!(
            journal.contains("\"lease.acquired\"") && journal.contains("\"verdict\":\"silent\"")
        );
    }

    #[test]
    fn planted_unauthenticated_lease_changes_are_refused() {
        let (_t, dir) = registry();
        let holder = register(&dir, "claude-code");
        let successor = register(&dir, "antigravity");
        plant_lease(&dir, "claude-code", "held", 5, json!({}));
        plant_status(&dir, "claude-code", json!({}), 0);
        for forged in [
            json!({"action":"lease","subaction":"release","holder":"claude-code"}),
            json!({"action":"lease","subaction":"release","agent_id":"claude-code"}),
            json!({"action":"lease","subaction":"release","agent_id":"claude-code","token":"DEVLOOP-PLANTED-FORGED-TOKEN-000000000000"}),
            json!({"action":"lease","subaction":"release","agent_id":"claude-code","token":successor}),
            json!({"action":"lease","subaction":"renew","agent_id":"claude-code"}),
            json!({"action":"status","subaction":"publish","agent_id":"claude-code","state":"working","transcript":"/DEVLOOP-PLANTED-TRANSCRIPT.jsonl"}),
        ] {
            let result = call(&dir, forged.clone());
            assert!(result.is_err(), "{forged} -> {result:?}");
        }
        assert_eq!(holder_of(&dir), "claude-code");
        let renewed = lease(&dir, "renew", "claude-code", json!({"note":"renewed"})).unwrap();
        assert_eq!(renewed["next_renew_s"], 1800);
        let released = lease(
            &dir,
            "release",
            "claude-code",
            json!({"handoff_to":"antigravity"}),
        )
        .unwrap();
        assert_eq!(released["lease"]["state"], "released");
        // Tokens never reach disk; only their digests do.
        for entry in fs::read_dir(&dir).unwrap() {
            let bytes = fs::read(entry.unwrap().path()).unwrap();
            let text = String::from_utf8_lossy(&bytes);
            assert!(!text.contains(&holder) && !text.contains(&successor));
        }
    }

    #[test]
    fn planted_path_traversal_is_contained() {
        let (temp, dir) = registry();
        register(&dir, "claude-code");
        let escaped = temp.path().join("escaped.status.json");
        let forged = call(
            &dir,
            json!({"action":"status","subaction":"publish","agent_id":"../escaped",
            "token":token("claude-code"),"state":"working"}),
        );
        assert!(forged.is_err() && !escaped.exists(), "{forged:?}");
        fs::write(
            &escaped,
            json!({"state":"waiting","note":"DEVLOOP-PLANTED-ESCAPE"}).to_string(),
        )
        .unwrap();
        for id in ["../escaped", "..", ".hidden", "a/b", "a\\b"] {
            let read = call(
                &dir,
                json!({"action":"status","subaction":"get","agent_id":id}),
            );
            assert!(read.is_err(), "{id} -> {read:?}");
        }
        // The probe's holder -> status path.
        plant_lease(&dir, "../escaped", "held", 5, json!({}));
        let probed = probe(&dir, "antigravity");
        assert!(probed.is_err(), "{probed:?}");
    }

    #[test]
    fn planted_caller_named_holder_and_directory_are_ignored() {
        let (temp, dir) = registry();
        plant_lease(&dir, "claude-code", "held", 5, json!({}));
        plant_status(&dir, "claude-code", json!({}), 0);
        let named = call(
            &dir,
            json!({"action":"probe","agent_id":"antigravity","holder":"DEVLOOP-PLANTED-HOLDER"}),
        )
        .unwrap();
        assert_eq!(named["holder"], "claude-code");
        let other = temp.path().join("other");
        plant_lease(&other, "DEVLOOP-PLANTED-DIR", "released", 5, json!({}));
        let moved = call(
            &dir,
            json!({"action":"probe","agent_id":"antigravity","dir":other.to_str().unwrap()}),
        )
        .unwrap();
        assert_eq!(moved["holder"], "claude-code");
    }

    #[test]
    fn planted_succession_numbers_come_from_ssot() {
        let (_t, dir) = registry();
        let mut cfg = config();
        cfg["succession"]["lease_duration_s"] = json!(4321);
        cfg["succession"]["probe_interval_s"] = json!(700);
        cfg["succession"]["retry_probe_s"] = json!(45);
        cfg["succession"]["successors"] = json!(["antigravity", "codex"]);
        // A fresh install: the lease is derived from SSOT, and reading it writes nothing.
        let fresh = call_with(
            &dir,
            json!({"action":"lease","subaction":"get"}),
            cfg.clone(),
        )
        .unwrap();
        assert_eq!(
            (fresh["holder_identity"].clone(), fresh["state"].as_str()),
            (Value::Null, Some("released"))
        );
        assert_eq!(fresh["lease_duration_seconds"], 4321);
        assert_eq!(fresh["preferred_holder"], "claude-code");
        assert_eq!(fresh["successors"], json!(["antigravity", "codex"]));
        assert!(!dir.join("lease.json").exists());
        let open = call_with(
            &dir,
            json!({"action":"probe","agent_id":"antigravity"}),
            cfg.clone(),
        )
        .unwrap();
        assert_eq!(
            (
                open["verdict"].as_str(),
                open["take_over"].as_bool(),
                open["next_probe_s"].as_u64()
            ),
            (Some("released"), Some(true), Some(45))
        );
        let rogue = call_with(
            &dir,
            json!({"action":"probe","agent_id":"rogue"}),
            cfg.clone(),
        )
        .unwrap();
        assert_eq!(rogue["take_over"], false);
        let t = token("antigravity");
        call_with(
            &dir,
            json!({"action":"register","agent_id":"antigravity","kind":"codex","token":t}),
            cfg.clone(),
        )
        .unwrap();
        let first = call_with(
            &dir,
            json!({"action":"lease","subaction":"acquire","agent_id":"antigravity","token":t}),
            cfg.clone(),
        )
        .unwrap();
        assert_eq!(first["lease"]["successors"], json!(["codex"]));
        assert_eq!(first["from"], Value::Null);
        assert_eq!(holder_of(&dir), "antigravity");
        let alive = call_with(
            &dir,
            json!({"action":"probe","agent_id":"codex"}),
            cfg.clone(),
        )
        .unwrap();
        assert_eq!(
            (alive["verdict"].as_str(), alive["next_probe_s"].as_u64()),
            (Some("alive"), Some(700))
        );
        // Planted: no literal fallback for any key.
        for key in [
            "lease_duration_s",
            "renew_interval_s",
            "probe_interval_s",
            "retry_probe_s",
            "min_outage_s",
            "tail_lines",
            "preferred_holder",
            "successors",
        ] {
            let mut missing = cfg.clone();
            missing["succession"].as_object_mut().unwrap().remove(key);
            let result = call_with(&dir, json!({"action":"probe","agent_id":"codex"}), missing);
            assert!(
                result.as_ref().is_err_and(|e| e.contains(key)),
                "{key} -> {result:?}"
            );
        }
        // client-go: lease_duration_s > renew_interval_s > 1.2 x probe_interval_s.
        let mut tight = cfg.clone();
        tight["succession"]["renew_interval_s"] = json!(800);
        assert!(call_with(&dir, json!({"action":"probe","agent_id":"codex"}), tight).is_err());
    }

    #[test]
    fn planted_bom_prefixed_status_is_read() {
        let (_t, dir) = registry();
        plant_lease(&dir, "claude-code", "held", 5, json!({}));
        let status = json!({"object":"agent.status","agent_id":"claude-code","state":"waiting","note":"bom-note"});
        fs::write(
            dir.join("claude-code.status.json"),
            format!("\u{feff}{status}"),
        )
        .unwrap();
        let res = probe(&dir, "antigravity").unwrap();
        assert_eq!(
            (res["verdict"].as_str(), res["holder_note"].as_str()),
            (Some("waiting"), Some("bom-note")),
            "{res}"
        );
    }

    #[test]
    fn planted_utc_offsets_are_honoured() {
        let utc = Some(1_791_561_600);
        assert_eq!(parse_iso("2026-10-09T16:00:00Z"), utc);
        assert_eq!(parse_iso("2026-10-09T16:00:00"), utc);
        assert_eq!(parse_iso("2026-10-09T12:00:00-04:00"), utc);
        assert_eq!(parse_iso("2026-10-09T21:30:00+05:30"), utc);
        assert_eq!(parse_iso("2026-10-09T12:00:00-0400"), utc);
        assert_eq!(parse_iso("2026-10-09T16:00:00.250Z"), utc);
        for bad in [
            "2026-10-09T16:00:00+25:00",
            "2026-10-09T16:00:00 junk",
            "2026-13-09T16:00:00Z",
            "é026-10-09T16:00:00Z",
        ] {
            assert_eq!(parse_iso(bad), None, "{bad}");
        }
        // A renewal ten minutes ago, written with a -04:00 offset, is not four hours old.
        let (_t, dir) = registry();
        let local = format_iso(now() - 600 - 4 * 3600).replace('Z', "-04:00");
        plant_lease(&dir, "claude-code", "held", 0, json!({"renew_time": local}));
        plant_status(&dir, "claude-code", json!({}), 7200);
        let res = probe(&dir, "antigravity").unwrap();
        assert_eq!(res["verdict"], "alive", "{res}");
        assert!(
            (600..=660).contains(&res["lease_age_s"].as_i64().unwrap()),
            "{res}"
        );
    }

    #[test]
    fn planted_lease_duration_is_the_lease_own() {
        let (_t, dir) = registry();
        plant_lease(
            &dir,
            "claude-code",
            "held",
            10800,
            json!({"lease_duration_seconds": 100000}),
        );
        plant_status(&dir, "claude-code", json!({}), 10800);
        let res = probe(&dir, "antigravity").unwrap();
        assert_eq!(
            (res["verdict"].as_str(), res["take_over"].as_bool()),
            (Some("alive"), Some(false)),
            "{res}"
        );
    }

    #[test]
    fn planted_float_and_string_resets_at_are_times() {
        let (_t, dir) = registry();
        plant_lease(&dir, "claude-code", "held", 5, json!({}));
        for resets in [
            json!((now() + 300) as f64),
            json!(format!("{}", now() + 300)),
        ] {
            plant_transcript(&dir, "claude-code", &[api_error(resets.clone())]);
            let res = probe(&dir, "antigravity").unwrap();
            assert_eq!(
                (res["verdict"].as_str(), res["take_over"].as_bool()),
                (Some("exhausted_briefly"), Some(false)),
                "{resets} -> {res}"
            );
        }
    }

    #[test]
    fn planted_registry_lease_copy_is_not_a_second_source() {
        let (_t, dir) = registry();
        fs::create_dir_all(&dir).unwrap();
        let planted = json!({"agents":{},"messages":[],"coordinator_lease":{"object":"agent.lease",
            "holder_identity":"DEVLOOP-PLANTED-STATE","state":"held","successors":["antigravity"],
            "lease_duration_seconds":5400,"renew_time":format_iso(now())}});
        fs::write(dir.join("state.json"), planted.to_string()).unwrap();
        let got = call(&dir, json!({"action":"lease","subaction":"get"})).unwrap();
        assert_eq!(got["holder_identity"], Value::Null, "{got}");
        let res = probe(&dir, "antigravity").unwrap();
        assert_eq!(
            (res["holder"].clone(), res["verdict"].as_str()),
            (Value::Null, Some("released")),
            "{res}"
        );
    }

    #[test]
    fn planted_transcript_head_is_not_read() {
        let (_t, dir) = registry();
        plant_lease(&dir, "claude-code", "held", 5, json!({}));
        let path = dir.join("long-transcript.jsonl");
        let mut bytes = vec![0xff_u8; 256 * 1024];
        bytes.push(b'\n');
        for n in 0..200 {
            bytes.extend(format!("{}\n", json!({"type":"user","n":n})).as_bytes());
        }
        bytes.extend(format!("{}\n", api_error(json!(now() + 3600))).as_bytes());
        fs::write(&path, &bytes).unwrap();
        plant_status(
            &dir,
            "claude-code",
            json!({"transcript": path.to_str().unwrap()}),
            0,
        );
        assert_eq!(probe(&dir, "antigravity").unwrap()["verdict"], "exhausted");
        // An error older than the tail does not decide.
        let mut old = format!("{}\n", api_error(json!(now() + 3600)));
        for n in 0..100 {
            old.push_str(&format!("{}\n", json!({"type":"user","n":n})));
        }
        fs::write(&path, old).unwrap();
        assert_eq!(probe(&dir, "antigravity").unwrap()["verdict"], "alive");
    }

    #[test]
    fn planted_lease_identities_survive_registration_cleanup() {
        let (_t, dir) = registry();
        register(&dir, "claude-code");
        register(&dir, "antigravity");
        plant_lease(&dir, "claude-code", "held", 5, json!({}));
        let path = dir.join("state.json");
        let mut state: Value = serde_json::from_slice(&fs::read(&path).unwrap()).unwrap();
        for id in ["claude-code", "antigravity"] {
            state["agents"][id]["expires"] = json!(1);
        }
        fs::write(&path, state.to_string()).unwrap();
        register(&dir, "codex"); // runs dormant-identity cleanup
        let imposter = call(
            &dir,
            json!({"action":"register","agent_id":"claude-code","kind":"codex",
            "token":"DEVLOOP-PLANTED-IMPOSTER-TOKEN-000000000000"}),
        );
        assert!(
            imposter
                .as_ref()
                .is_err_and(|e| e.contains("another lease")),
            "{imposter:?}"
        );
        lease(&dir, "renew", "claude-code", json!({})).unwrap();
    }

    #[test]
    fn planted_release_to_an_ineligible_identity_is_refused() {
        let (_t, dir) = registry();
        register(&dir, "claude-code");
        plant_lease(&dir, "claude-code", "held", 5, json!({}));
        let released = lease(
            &dir,
            "release",
            "claude-code",
            json!({"handoff_to":"DEVLOOP-PLANTED-ROGUE"}),
        );
        assert!(released.is_err(), "{released:?}");
        assert_eq!(holder_of(&dir), "claude-code");
    }

    #[test]
    fn status_publish_is_owned_bounded_and_absolute() {
        let (_t, dir) = registry();
        register(&dir, "antigravity");
        let identity =
            json!({"action":"status","agent_id":"antigravity","token":token("antigravity")});
        let mut publish = identity.clone();
        merge(
            &mut publish,
            json!({"subaction":"publish","state":"working","note":"standing by","transcript":"/var/tmp/antigravity.jsonl"}),
        );
        call(&dir, publish.clone()).unwrap();
        let got = call(&dir, json!({"action":"status","agent_id":"antigravity"})).unwrap();
        assert_eq!(
            (got["state"].as_str(), got["note"].as_str()),
            (Some("working"), Some("standing by"))
        );
        let mut relative = publish.clone();
        relative["transcript"] = json!("DEVLOOP-PLANTED-RELATIVE.jsonl");
        assert!(call(&dir, relative).is_err());
        let mut long = publish;
        long["note"] = json!("x".repeat(300));
        assert!(call(&dir, long).is_err());
    }
}
