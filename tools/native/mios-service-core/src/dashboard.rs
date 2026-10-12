// AI-hint: Shared Linux and Windows dashboard projects every SSOT metric and categorized endpoint without clipping or a fixed service roster.
// AI-related: /usr/share/mios/templates/rust, usr/share/mios/mios.toml, tools/native/mios-gen/src/main.rs
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::collections::{BTreeMap, BTreeSet};
use std::net::{IpAddr, Ipv4Addr, SocketAddr, TcpStream};
use std::time::Duration;
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
pub struct Endpoint {
    pub category: String,
    pub name: String,
    pub port: u16,
    pub state: String,
}

fn string<'a>(doc: &'a Value, path: &str) -> Result<&'a str, String> {
    doc.pointer(path)
        .and_then(Value::as_str)
        .filter(|s| !s.trim().is_empty() && !s.chars().any(char::is_control))
        .ok_or_else(|| format!("Missing or invalid SSOT {path}"))
}

pub fn milliseconds(doc: &Value, key: &str) -> Result<Duration, String> {
    doc["dashboard"][key]
        .as_u64()
        .filter(|v| *v > 0 && *v <= 10000)
        .map(Duration::from_millis)
        .ok_or_else(|| format!("Invalid SSOT dashboard.{key}"))
}

/// Categories enumerate the public and supporting listeners. The resolver has
/// already derived their values and applied layer precedence; no ports live here.
pub fn catalog(doc: &Value) -> Result<Vec<Endpoint>, String> {
    let categories = doc
        .pointer("/ports/categories")
        .and_then(Value::as_object)
        .ok_or("Missing SSOT ports.categories")?;
    let mut endpoints = Vec::new();
    let mut seen = BTreeSet::new();
    for (category, values) in categories {
        let members = values["members"]
            .as_array()
            .ok_or_else(|| format!("Invalid ports.categories.{category}.members"))?;
        let mut names = Vec::new();
        for name in members {
            let name = name
                .as_str()
                .ok_or("Port category member must be a string")?;
            if !name.is_empty() {
                names.push(name);
            }
        }
        if let Some(pinned) = values.get("pinned") {
            names.extend(
                pinned
                    .as_object()
                    .ok_or("Port category pinned must be a table")?
                    .keys()
                    .map(String::as_str),
            );
        }
        for name in names {
            if !name.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'_') {
                return Err(format!("Invalid SSOT endpoint identity {name}"));
            }
            if !seen.insert(name.to_owned()) {
                return Err(format!("Duplicate SSOT endpoint identity {name}"));
            }
            let offset = doc["ports"]
                .get("stack_id")
                .map(|v| {
                    v.as_i64()
                        .or_else(|| v.as_str().and_then(|s| s.parse().ok()))
                        .ok_or("Invalid SSOT ports.stack_id")
                })
                .transpose()?
                .unwrap_or(0)
                .checked_mul(10000)
                .ok_or("Stack offset overflow")?;
            let port = doc["ports"][name]
                .as_i64()
                .and_then(|v| v.checked_add(offset))
                .and_then(|v| u16::try_from(v).ok())
                .filter(|p| *p > 0)
                .ok_or_else(|| format!("Invalid resolved SSOT ports.{name}"))?;
            endpoints.push(Endpoint {
                category: category.clone(),
                name: name.into(),
                port,
                state: "unprobed".into(),
            });
        }
    }
    if endpoints.is_empty() {
        return Err("SSOT endpoint catalog is empty; nothing to display".into());
    }
    if endpoints.len() > 256 {
        return Err("SSOT endpoint catalog exceeds 256 listeners".into());
    }
    Ok(endpoints)
}

/// TCP reachability is reported as TCP reachability, never application health.
/// Concurrent loopback connects share a single timeout rather than N serial waits.
pub fn probe(endpoints: &mut [Endpoint], timeout: Duration) -> Result<(), String> {
    std::thread::scope(|scope| {
        let tasks: Vec<_> = endpoints
            .iter()
            .map(|endpoint| {
                let port = endpoint.port;
                scope.spawn(move || {
                    TcpStream::connect_timeout(
                        &SocketAddr::new(IpAddr::V4(Ipv4Addr::LOCALHOST), port),
                        timeout,
                    )
                    .is_ok()
                })
            })
            .collect();
        for (endpoint, task) in endpoints.iter_mut().zip(tasks) {
            endpoint.state = if task.join().map_err(|_| "Dashboard TCP probe panicked")? {
                "open"
            } else {
                "closed"
            }
            .into();
        }
        Ok(())
    })
}

fn clean(text: &str) -> String {
    text.chars()
        .map(|c| if c.is_control() { ' ' } else { c })
        .collect()
}

/// Wrap at display-cell boundaries, preserving every value (including CJK).
pub fn wrap(text: &str, width: usize) -> Vec<String> {
    let mut lines = Vec::new();
    let mut line = String::new();
    let mut cells = 0;
    for c in clean(text).chars() {
        let count = c.width().unwrap_or(0);
        if cells + count > width && !line.is_empty() {
            lines.push(std::mem::take(&mut line));
            cells = 0;
        }
        line.push(c);
        cells += count;
    }
    lines.push(line);
    lines
}

pub fn metrics(doc: &Value, facts: &Value) -> Result<BTreeMap<String, String>, String> {
    let array = facts
        .as_array()
        .ok_or("Fastfetch facts must be a JSON array")?;
    let result = |kind: &str| {
        array
            .iter()
            .find(|r| r["type"].as_str() == Some(kind))
            .and_then(|r| r.get("result"))
    };
    let text = |kind: &str, keys: &[&str]| -> String {
        result(kind)
            .and_then(|r| keys.iter().find_map(|key| r[*key].as_str()))
            .unwrap_or("unavailable")
            .into()
    };
    let bytes = |kind: &str| -> String {
        let Some(value) = result(kind) else {
            return "unavailable".into();
        };
        let rows = value
            .as_array()
            .map(|rows| rows.iter().collect::<Vec<_>>())
            .unwrap_or_else(|| vec![value]);
        let totals = rows
            .into_iter()
            .try_fold((0_u64, 0_u64), |(used, total), row| {
                Some((
                    used.checked_add(row["used"].as_u64()?)?,
                    total.checked_add(row["total"].as_u64()?)?,
                ))
            });
        totals
            .map(|(used, total)| {
                format!(
                    "{:.1} / {:.1} GiB ({:.0}%)",
                    used as f64 / 1073741824.0,
                    total as f64 / 1073741824.0,
                    if total == 0 {
                        0.0
                    } else {
                        used as f64 * 100.0 / total as f64
                    }
                )
            })
            .unwrap_or_else(|| "unavailable".into())
    };
    let mut values = BTreeMap::from([
        (
            "version".into(),
            format!("MiOS {}", string(doc, "/meta/mios_version")?),
        ),
        ("user".into(), text("Title", &["userName"])),
        ("host".into(), text("Title", &["hostName"])),
        ("host_os".into(), text("OS", &["prettyName", "name"])),
        ("cpu".into(), text("CPU", &["cpu", "name"])),
        ("kernel".into(), text("Kernel", &["release"])),
        ("shell".into(), text("Shell", &["prettyName", "exeName"])),
        ("ram".into(), bytes("Memory")),
        ("swap".into(), bytes("Swap")),
        (
            "font".into(),
            format!(
                "{} {}pt (configured)",
                string(doc, "/theme/font/family")?,
                doc.pointer("/theme/font/size")
                    .and_then(Value::as_u64)
                    .ok_or("Invalid SSOT theme.font.size")?
            ),
        ),
    ]);
    values.insert(
        "uptime".into(),
        result("Uptime")
            .and_then(|r| r["uptime"].as_u64())
            .map(|ms| {
                format!(
                    "{}d {}h {}m",
                    ms / 86400000,
                    ms / 3600000 % 24,
                    ms / 60000 % 60
                )
            })
            .unwrap_or_else(|| "unavailable".into()),
    );
    values.insert(
        "date".into(),
        result("DateTime")
            .map(|r| {
                r.as_str()
                    .map(str::to_owned)
                    .unwrap_or_else(|| r.to_string())
            })
            .unwrap_or_else(|| "unavailable".into()),
    );
    for (key, kind) in [
        ("gpu", None),
        ("gpu_discrete", Some("Discrete")),
        ("gpu_integrated", Some("Integrated")),
    ] {
        values.insert(
            key.into(),
            result("GPU")
                .and_then(Value::as_array)
                .map(|rows| {
                    rows.iter()
                        .filter(|row| {
                            kind.is_none_or(|kind| {
                                row["type"]
                                    .as_str()
                                    .is_some_and(|value| value.eq_ignore_ascii_case(kind))
                            })
                        })
                        .filter_map(|row| row["name"].as_str())
                        .collect::<Vec<_>>()
                        .join(", ")
                })
                .filter(|s| !s.is_empty())
                .unwrap_or_else(|| "unavailable".into()),
        );
    }
    for (key, mounts) in [("disk_c", ["C:\\", "/"]), ("disk_m", ["M:\\", "/mnt/m"])] {
        let value = result("Disk")
            .and_then(Value::as_array)
            .and_then(|rows| {
                rows.iter().find(|r| {
                    r["mountpoint"]
                        .as_str()
                        .is_some_and(|mount| mounts.contains(&mount))
                })
            })
            .and_then(|r| {
                let used = r["bytes"]["used"].as_u64()?;
                let total = r["bytes"]["total"].as_u64()?;
                Some(format!(
                    "{} {:.1} / {:.1} GiB ({:.0}%)",
                    r["mountpoint"].as_str()?,
                    used as f64 / 1073741824.0,
                    total as f64 / 1073741824.0,
                    if total == 0 {
                        0.0
                    } else {
                        used as f64 * 100.0 / total as f64
                    }
                ))
            })
            .unwrap_or_else(|| "unavailable".into());
        values.insert(key.into(), value);
    }
    Ok(values)
}

pub fn render(
    doc: &Value,
    facts: &Value,
    endpoints: &[Endpoint],
    width: usize,
) -> Result<String, String> {
    if !(24..=512).contains(&width) {
        return Err("Dashboard width must be 24..512 display cells".into());
    }
    let values = metrics(doc, facts)?;
    let rows = doc
        .pointer("/dashboard/rows")
        .and_then(Value::as_array)
        .filter(|r| !r.is_empty())
        .ok_or("Missing SSOT dashboard.rows")?;
    let mut out = String::new();
    let rule = format!("+{}+\n", "-".repeat(width - 2));
    out.push_str(&rule);
    let emit = |out: &mut String, text: &str| {
        for line in wrap(text, width - 4) {
            out.push_str(&format!(
                "| {line}{} |\n",
                " ".repeat((width - 4).saturating_sub(line.width()))
            ));
        }
    };
    if doc["dashboard"]["show_title"]
        .as_bool()
        .ok_or("Invalid SSOT dashboard.show_title")?
    {
        emit(&mut out, string(doc, "/dashboard/title")?);
    }
    out.push_str(&rule);
    // Paired columns retain alignment at narrow widths by wrapping independently.
    for row in rows {
        let row = row
            .as_array()
            .filter(|r| !r.is_empty() && r.len() <= 2)
            .ok_or("Dashboard rows must contain one or two metric keys")?;
        let cells: Result<Vec<_>, String> = row
            .iter()
            .map(|key| {
                let key = key
                    .as_str()
                    .ok_or("Dashboard metric key must be a string")?;
                Ok(format!(
                    "{key}: {}",
                    values
                        .get(key)
                        .ok_or_else(|| format!("Unknown SSOT dashboard metric {key}"))?
                ))
            })
            .collect();
        let cells = cells?;
        if cells.len() == 1 || width < 60 {
            for cell in cells {
                emit(&mut out, &cell);
            }
        } else {
            let left_width = (width - 7) / 2;
            let left = wrap(&cells[0], left_width);
            let right = wrap(&cells[1], width - 7 - left_width);
            for i in 0..left.len().max(right.len()) {
                let l = left.get(i).map(String::as_str).unwrap_or("");
                let r = right.get(i).map(String::as_str).unwrap_or("");
                emit(
                    &mut out,
                    &format!(
                        "{l}{} | {r}",
                        " ".repeat(left_width.saturating_sub(l.width()))
                    ),
                );
            }
        }
    }
    if doc["dashboard"]["show_services"]
        .as_bool()
        .ok_or("Invalid SSOT dashboard.show_services")?
    {
        if endpoints.is_empty() {
            return Err("Dashboard has no SSOT endpoints".into());
        }
        out.push_str(&rule);
        emit(
            &mut out,
            &format!(
                "{} endpoints; TCP reachability only; {} open / {} closed / {} unprobed",
                endpoints.len(),
                endpoints.iter().filter(|r| r.state == "open").count(),
                endpoints.iter().filter(|r| r.state == "closed").count(),
                endpoints.iter().filter(|r| r.state == "unprobed").count()
            ),
        );
        emit(&mut out, "Category / Service / Port / TCP");
        for endpoint in endpoints {
            emit(
                &mut out,
                &format!(
                    "{} / {} / {} / {}",
                    endpoint.category, endpoint.name, endpoint.port, endpoint.state
                ),
            );
        }
    }
    if doc["dashboard"]["show_verb_hints"]
        .as_bool()
        .ok_or("Invalid SSOT dashboard.show_verb_hints")?
    {
        out.push_str(&rule);
        emit(&mut out, string(doc, "/dashboard/verb_hint")?);
    }
    out.push_str(&rule);
    Ok(out)
}
