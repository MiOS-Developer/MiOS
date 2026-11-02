// AI-hint: [remote.device_forwarding] consumer: one capability report for every generic-USB forwarding path MiOS has (RDP URBDRC -> USB/IP vhci, USB/IP, SPICE usbredir, nested-session evdev binding), per-session target selection (targets.json under $XDG_RUNTIME_DIR, read by mios-rdp-server) and the SPICE usbredir filter rendered from the shared allow/deny policy. Read-only probes: never loads a module, claims a device or inspects an excluded VM.
// AI-related: usr/share/mios/mios.toml, src/mios-rdp-server/crates/mios-rdp-server/src/remote_usb.rs, tools/native/mios-gen/src/nested_devices.rs, tests/test-device-forwarding.py, usr/share/doc/mios/remote-usb.md
// AI-functions: run_report, run_select, spice_filter, Policy, probe_vhci, session_targets
//! `mios-gen device-forwarding report|select|spice-filter`.
//!
//! Sessions are named `rdp` (every native RDP connection), `nested:<seat>` (a live nested MiOS
//! session) and `spice:<domain>` (a libvirt domain with a SPICE usbredir channel). Each kind has
//! exactly one native transport; `auto` picks it when available, `none` forwards nothing.
use serde_json::{json, Map, Value as Json};
use std::{
    fs,
    io::Write,
    path::{Path, PathBuf},
    process::Command,
};
use toml::Value;

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Rule {
    Id { vid: u16, pid: Option<u16> },
    Class(u8),
}

fn hex16(s: &str) -> Option<u16> {
    (s.len() == 4)
        .then(|| u16::from_str_radix(s, 16).ok())
        .flatten()
}

/// Same grammar as mios-rdp-server's remote_usb::parse_rule (both read [remote.device_forwarding.usb]).
pub fn parse_rule(raw: &str) -> Result<Rule, String> {
    let s = raw.trim().to_ascii_lowercase();
    if let Some(c) = s.strip_prefix("class:") {
        return (c.len() == 2)
            .then(|| u8::from_str_radix(c, 16).ok())
            .flatten()
            .map(Rule::Class)
            .ok_or_else(|| format!("bad class rule {raw:?}"));
    }
    let (v, p) = s
        .split_once(':')
        .ok_or_else(|| format!("bad rule {raw:?} (want VVVV:PPPP, VVVV:* or class:CC)"))?;
    let vid = hex16(v).ok_or_else(|| format!("bad vendor id in {raw:?}"))?;
    let pid = if p == "*" {
        None
    } else {
        Some(hex16(p).ok_or_else(|| format!("bad product id in {raw:?}"))?)
    };
    Ok(Rule::Id { vid, pid })
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Policy {
    pub allow: Vec<Rule>,
    pub deny: Vec<Rule>,
    pub max_devices: i64,
}

fn strings(v: Option<&Value>, key: &str) -> Result<Vec<String>, String> {
    match v {
        None => Ok(Vec::new()),
        Some(Value::Array(a)) => a
            .iter()
            .map(|x| {
                x.as_str()
                    .map(str::to_owned)
                    .ok_or(format!("{key} entries must be strings"))
            })
            .collect(),
        Some(_) => Err(format!("{key} must be an array of strings")),
    }
}

impl Policy {
    pub fn from_ssot(df: &Value) -> Result<Self, String> {
        let usb = df
            .get("usb")
            .ok_or("[remote.device_forwarding.usb] missing")?;
        let allow = strings(usb.get("allow"), "usb.allow")?
            .iter()
            .map(|r| parse_rule(r))
            .collect::<Result<_, _>>()?;
        let deny = strings(usb.get("deny"), "usb.deny")?
            .iter()
            .map(|r| parse_rule(r))
            .collect::<Result<_, _>>()?;
        let max_devices = usb
            .get("max_devices")
            .and_then(Value::as_integer)
            .ok_or("usb.max_devices must be an integer")?;
        if !(1..=16).contains(&max_devices) {
            return Err("usb.max_devices must be 1..16".into());
        }
        Ok(Self {
            allow,
            deny,
            max_devices,
        })
    }
}

/// usbredirfilter rules (`class,vendor,product,version,allow` joined by `|`, first match wins):
/// every deny rule, then every allow rule, then a final deny-all. An empty allow list yields a
/// filter that redirects nothing.
pub fn spice_filter(p: &Policy) -> String {
    let rule = |r: &Rule, allow: u8| match r {
        Rule::Class(c) => format!("0x{c:02x},-1,-1,-1,{allow}"),
        Rule::Id {
            vid,
            pid: Some(pid),
        } => format!("-1,0x{vid:04x},0x{pid:04x},-1,{allow}"),
        Rule::Id { vid, pid: None } => format!("-1,0x{vid:04x},-1,-1,{allow}"),
    };
    let mut v: Vec<String> = p.deny.iter().map(|r| rule(r, 0)).collect();
    v.extend(p.allow.iter().map(|r| rule(r, 1)));
    v.push("-1,-1,-1,-1,0".into());
    v.join("|")
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VhciPort {
    pub hub: String,
    pub port: u32,
    pub status: u32,
}

pub fn parse_vhci_status(text: &str) -> Vec<VhciPort> {
    text.lines()
        .filter_map(|l| {
            let f: Vec<&str> = l.split_whitespace().collect();
            if f.len() < 5 || !(f[0] == "hs" || f[0] == "ss") {
                return None;
            }
            Some(VhciPort {
                hub: f[0].into(),
                port: f[1].parse().ok()?,
                status: f[2].parse().ok()?,
            })
        })
        .collect()
}

fn module_file(modules: &Path, name: &str) -> bool {
    fs::read_to_string(modules.join("modules.dep"))
        .map(|t| {
            t.lines().any(|l| {
                l.split(':').next().is_some_and(|m| {
                    Path::new(m)
                        .file_name()
                        .and_then(|f| f.to_str())
                        .is_some_and(|f| f.starts_with(&format!("{name}.ko")))
                })
            })
        })
        .unwrap_or(false)
}

/// vhci_hcd state from sysfs + modules.dep only (read-only; nothing is loaded).
pub fn probe_vhci(sys: &Path, modules: &Path) -> Json {
    let dir = sys.join("devices/platform/vhci_hcd.0");
    let module = if sys.join("module/vhci_hcd").exists() {
        "loaded"
    } else if module_file(modules, "vhci-hcd") || module_file(modules, "vhci_hcd") {
        "available_not_loaded"
    } else {
        "absent"
    };
    let mut ports = Vec::new();
    for i in 0..16 {
        let name = if i == 0 {
            "status".to_owned()
        } else {
            format!("status.{i}")
        };
        match fs::read_to_string(dir.join(name)) {
            Ok(t) => ports.extend(parse_vhci_status(&t)),
            Err(_) => break,
        }
    }
    let free: Vec<u32> = ports
        .iter()
        .filter(|p| p.hub == "hs" && p.status == 4)
        .map(|p| p.port)
        .collect();
    let available = dir.is_dir() && !free.is_empty();
    let reason = if available {
        "ok".to_owned()
    } else if !dir.is_dir() {
        match module {
            "available_not_loaded" => "vhci_hcd is installed but not loaded; MiOS never loads kernel modules (an administrator may load it)".into(),
            "loaded" => "vhci_hcd loaded without a controller".into(),
            _ => "vhci_hcd is not installed for this kernel (CONFIG_USBIP_VHCI_HCD module package absent)".into(),
        }
    } else {
        "no free high-speed vhci port".into()
    };
    json!({"module": module, "available": available, "reason": reason, "free_hs_ports": free,
           "used_ports": ports.iter().filter(|p| p.status != 4).map(|p| p.port).collect::<Vec<_>>()})
}

fn which(name: &str, path: &str) -> Option<String> {
    path.split(':')
        .map(|d| Path::new(d).join(name))
        .find(|p| p.is_file())
        .map(|p| p.display().to_string())
}

fn first_existing(cands: &[&str]) -> Option<String> {
    cands
        .iter()
        .find(|p| Path::new(p).exists())
        .map(|p| (*p).to_owned())
}

/// `*` glob only (domain names); used to keep excluded VMs out of every probe.
pub fn glob_match(pattern: &str, name: &str) -> bool {
    let parts: Vec<&str> = pattern.split('*').collect();
    if parts.len() == 1 {
        return pattern == name;
    }
    let (first, last) = (parts[0], parts[parts.len() - 1]);
    if !name.starts_with(first) || !name.ends_with(last) || name.len() < first.len() + last.len() {
        return false;
    }
    let mut rest = &name[first.len()..name.len() - last.len()];
    for mid in &parts[1..parts.len() - 1] {
        match rest.find(mid) {
            Some(i) => rest = &rest[i + mid.len()..],
            None => return false,
        }
    }
    true
}

fn run_bounded(args: &[&str]) -> Result<String, String> {
    let out = Command::new("timeout")
        .arg("5")
        .args(args)
        .output()
        .map_err(|e| format!("{}: {e}", args[0]))?;
    if !out.status.success() {
        return Err(String::from_utf8_lossy(&out.stderr)
            .trim()
            .chars()
            .take(200)
            .collect());
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

fn libvirt_domains(uris: &[String], exclude: &[String], virsh: Option<&str>) -> Json {
    let Some(virsh) = virsh else {
        return json!({"error": "virsh not installed"});
    };
    let mut doms = Vec::new();
    let mut errors = Map::new();
    for uri in uris {
        match run_bounded(&[virsh, "-c", uri, "list", "--all", "--name"]) {
            Err(e) => {
                errors.insert(uri.clone(), json!(e));
            }
            Ok(list) => {
                for name in list.lines().map(str::trim).filter(|n| !n.is_empty()) {
                    if exclude.iter().any(|g| glob_match(g, name)) {
                        continue; // excluded domains are never inspected
                    }
                    let redir = run_bounded(&[virsh, "-c", uri, "dumpxml", "--inactive", name])
                        .map(|x| {
                            x.matches("<redirdev")
                                .filter(|_| x.contains("spicevmc"))
                                .count()
                        })
                        .unwrap_or(0);
                    doms.push(json!({"uri": uri, "name": name, "spice_redirdev": redir}));
                }
            }
        }
    }
    json!({"domains": doms, "errors": errors})
}

pub struct Probe {
    pub sys: PathBuf,
    pub modules: PathBuf,
    pub path: String,
    pub runtime: PathBuf,
    pub libvirt: bool,
}

impl Probe {
    pub fn host(runtime: PathBuf, libvirt: bool) -> Self {
        let release = fs::read_to_string("/proc/sys/kernel/osrelease").unwrap_or_default();
        Self {
            sys: "/sys".into(),
            modules: Path::new("/lib/modules").join(release.trim()),
            path: std::env::var("PATH").unwrap_or_else(|_| "/usr/bin:/usr/sbin".into()),
            runtime,
            libvirt,
        }
    }
}

fn targets_path(df: &Value, runtime: &Path) -> Result<PathBuf, String> {
    let rel = df
        .get("targets_file")
        .and_then(Value::as_str)
        .ok_or("[remote.device_forwarding].targets_file missing")?;
    let p = Path::new(rel);
    if p.is_absolute()
        || p.components()
            .any(|c| !matches!(c, std::path::Component::Normal(_)))
    {
        return Err("targets_file must be relative to $XDG_RUNTIME_DIR".into());
    }
    Ok(runtime.join(p))
}

pub fn read_targets(path: &Path) -> Json {
    fs::read(path)
        .ok()
        .and_then(|b| serde_json::from_slice::<Json>(&b).ok())
        .filter(Json::is_object)
        .unwrap_or_else(|| json!({"sessions": {}}))
}

/// The one transport each session kind can use.
pub fn native_transport(session: &str) -> Option<&'static str> {
    match session.split_once(':').map_or(session, |(k, _)| k) {
        "rdp" => Some("host-vhci"),
        "nested" => Some("nested-evdev"),
        "spice" => Some("spice-usbredir"),
        _ => None,
    }
}

pub fn validate_session(session: &str, exclude: &[String]) -> Result<(), String> {
    match session.split_once(':') {
        None if session == "rdp" => Ok(()),
        Some(("nested", seat))
            if !seat.is_empty()
                && seat.len() <= 32
                && seat
                    .bytes()
                    .all(|b| b.is_ascii_lowercase() || b.is_ascii_digit() || b == b'-') =>
        {
            Ok(())
        }
        Some(("spice", dom))
            if !dom.is_empty()
                && dom.len() <= 64
                && !dom.chars().any(|c| c.is_control() || c == '/') =>
        {
            if exclude.iter().any(|g| glob_match(g, dom)) {
                Err(format!(
                    "domain {dom:?} is excluded by [remote.device_forwarding].exclude_domains"
                ))
            } else {
                Ok(())
            }
        }
        _ => Err(format!(
            "unknown session {session:?} (rdp, nested:<seat> or spice:<domain>)"
        )),
    }
}

pub fn report(data: &Value, probe: &Probe) -> Result<Json, String> {
    let df = data
        .get("remote")
        .and_then(|r| r.get("device_forwarding"))
        .ok_or("[remote.device_forwarding] missing")?;
    let policy = Policy::from_ssot(df);
    let rdp = data.get("remote").and_then(|r| r.get("rdp"));
    let permitted = rdp
        .and_then(|r| r.get("redirect"))
        .and_then(|r| r.get("usb"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    let helper = rdp
        .and_then(|r| r.get("native"))
        .and_then(|n| n.get("usb_helper_socket"))
        .and_then(Value::as_str)
        .unwrap_or("/run/mios-rdp-usb.sock")
        .to_owned();
    let vhci = probe_vhci(&probe.sys, &probe.modules);
    let server = Path::new("/usr/libexec/mios/rdp-native/mios-rdp-server");
    let stats = fs::read(probe.runtime.join("mios-rdp/stats.json"))
        .ok()
        .and_then(|b| serde_json::from_slice::<Json>(&b).ok());
    let live = stats
        .as_ref()
        .and_then(|s| s.pointer("/device_forwarding/usb"))
        .cloned();
    let rdp_available = permitted && vhci["available"] == true && Path::new(&helper).exists();
    let rdp_reason = if !permitted {
        "[remote.rdp.redirect].usb = false".to_owned()
    } else if vhci["available"] != true {
        vhci["reason"].as_str().unwrap_or_default().to_owned()
    } else if !Path::new(&helper).exists() {
        format!("helper socket {helper} not listening (mios-rdp-usb.socket)")
    } else {
        "ok".into()
    };
    let viewers: Vec<String> = ["remote-viewer", "virt-viewer"]
        .iter()
        .filter_map(|v| which(v, &probe.path))
        .collect();
    let usbredirhost = first_existing(&[
        "/usr/lib64/libusbredirhost.so.1",
        "/usr/lib/libusbredirhost.so.1",
        "/usr/lib/x86_64-linux-gnu/libusbredirhost.so.1",
    ]);
    let acl = first_existing(&[
        "/usr/libexec/spice-gtk-x86_64/spice-client-glib-usb-acl-helper",
        "/usr/libexec/spice-client-glib-usb-acl-helper",
    ]);
    let qemu = first_existing(&[
        "/usr/lib64/qemu/hw-usb-redirect.so",
        "/usr/lib/qemu/hw-usb-redirect.so",
    ]);
    let uris = strings(df.get("libvirt_uris"), "libvirt_uris")?;
    let exclude = strings(df.get("exclude_domains"), "exclude_domains")?;
    let domains = if probe.libvirt {
        libvirt_domains(&uris, &exclude, which("virsh", &probe.path).as_deref())
    } else {
        json!({"skipped": true})
    };
    let spice_available = usbredirhost.is_some() && !viewers.is_empty();
    let registry = data
        .get("desktop")
        .and_then(|d| d.get("nested"))
        .and_then(|n| n.get("devices"))
        .and_then(|d| d.get("registry_dir"))
        .and_then(Value::as_str)
        .unwrap_or("mios/nested-sessions");
    let nested: Vec<Json> = crate::nested_devices::live_sessions(&probe.runtime.join(registry), false)
        .into_iter()
        .map(|(_, j)| json!({"seat": j.get("seat"), "devices": j.get("devices").cloned().unwrap_or(json!([]))}))
        .collect();
    let tpath = targets_path(df, &probe.runtime)?;
    let targets = read_targets(&tpath);
    let default_target = df
        .get("default_target")
        .and_then(Value::as_str)
        .unwrap_or("auto");
    let mut sessions = vec!["rdp".to_owned()];
    sessions.extend(
        nested
            .iter()
            .filter_map(|n| n["seat"].as_str().map(|s| format!("nested:{s}"))),
    );
    if let Some(ds) = domains.get("domains").and_then(Json::as_array) {
        sessions.extend(
            ds.iter()
                .filter(|d| d["spice_redirdev"].as_u64().unwrap_or(0) > 0)
                .filter_map(|d| d["name"].as_str().map(|n| format!("spice:{n}"))),
        );
    }
    for s in targets
        .get("sessions")
        .and_then(Json::as_object)
        .into_iter()
        .flat_map(|m| m.keys())
    {
        if !sessions.contains(s) {
            sessions.push(s.clone());
        }
    }
    let available = |t: &str| match t {
        "host-vhci" => rdp_available,
        "spice-usbredir" => spice_available,
        "nested-evdev" => true,
        _ => false,
    };
    let session_rows: Vec<Json> = sessions
        .iter()
        .map(|s| {
            let selected = targets.pointer(&format!("/sessions/{}/target", s.replace('~', "~0").replace('/', "~1"))).and_then(Json::as_str).unwrap_or(default_target).to_owned();
            let native = native_transport(s).unwrap_or("none");
            let effective = match selected.as_str() {
                "none" => "none".to_owned(),
                "auto" if available(native) => native.to_owned(),
                "auto" => "none".to_owned(),
                t if t == native && available(t) => t.to_owned(),
                _ => "none".to_owned(),
            };
            json!({"session": s, "transport": native, "transport_available": available(native), "selected": selected, "effective": effective})
        })
        .collect();
    Ok(json!({
        "policy": match &policy {
            Ok(p) => json!({"valid": true, "allow": strings(df["usb"].get("allow"), "allow")?, "deny": strings(df["usb"].get("deny"), "deny")?,
                           "max_devices": p.max_devices, "spice_filter": spice_filter(p)}),
            Err(e) => json!({"valid": false, "error": e}),
        },
        "transports": {
            "rdp_urbdrc": {"protocol": "MS-RDPEUSB", "permitted": permitted, "available": rdp_available, "reason": rdp_reason,
                           "server_installed": server.exists(), "helper_socket": helper, "endpoint": "usbip-vhci", "live": live,
                           "clients": "Windows clients only (mstsc, Windows App on Windows); Windows App on macOS, iOS/iPadOS, Android, web and Meta Quest has no generic USB redirection"},
            "usbip_vhci": {"vhci": vhci, "usbip_tool": which("usbip", &probe.path), "usbip_host_loaded": probe.sys.join("bus/usb/drivers/usbip-host").exists(),
                           "note": "USB/IP is the native endpoint of RDP URBDRC; exporting host devices (usbip-host) is never done by MiOS"},
            "spice_usbredir": {"available": spice_available, "libusbredirhost": usbredirhost, "viewers": viewers, "usb_acl_helper": acl,
                               "qemu_usb_redirect": qemu, "libvirt": domains, "excluded_domains": exclude},
            "nested_evdev": {"available": true, "sessions": nested, "note": "nested sessions bind evdev input nodes ([desktop.nested.devices]); generic USB is not bound into nested sessions"},
        },
        "targets": {"file": tpath, "default_target": default_target, "sessions": targets.get("sessions").cloned().unwrap_or(json!({}))},
        "sessions": session_rows,
    }))
}

/// Atomic rewrite of the per-session target file (0600).
pub fn select(data: &Value, runtime: &Path, session: &str, target: &str) -> Result<Json, String> {
    let df = data
        .get("remote")
        .and_then(|r| r.get("device_forwarding"))
        .ok_or("[remote.device_forwarding] missing")?;
    let exclude = strings(df.get("exclude_domains"), "exclude_domains")?;
    validate_session(session, &exclude)?;
    let native = native_transport(session).ok_or("unknown session kind")?;
    if !(target == "auto" || target == "none" || target == native) {
        return Err(format!(
            "target {target:?} does not apply to {session:?} (auto, none or {native})"
        ));
    }
    let path = targets_path(df, runtime)?;
    let mut doc = read_targets(&path);
    if !doc.get("sessions").is_some_and(Json::is_object) {
        doc["sessions"] = json!({});
    }
    doc["sessions"][session] = json!({"target": target});
    let dir = path.parent().ok_or("targets_file has no parent")?;
    fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    let tmp = path.with_extension("json.tmp");
    {
        use std::os::unix::fs::OpenOptionsExt;
        let mut f = fs::OpenOptions::new()
            .write(true)
            .create(true)
            .truncate(true)
            .mode(0o600)
            .open(&tmp)
            .map_err(|e| format!("{}: {e}", tmp.display()))?;
        f.write_all(
            serde_json::to_string_pretty(&doc)
                .map_err(|e| e.to_string())?
                .as_bytes(),
        )
        .map_err(|e| e.to_string())?;
        f.sync_all().map_err(|e| e.to_string())?;
    }
    fs::rename(&tmp, &path).map_err(|e| format!("{}: {e}", path.display()))?;
    Ok(doc)
}

pub fn run(
    root: &Path,
    action: &str,
    session: Option<&str>,
    target: Option<&str>,
    runtime: Option<PathBuf>,
    sys: Option<PathBuf>,
    no_libvirt: bool,
) -> Result<(), String> {
    let data = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let runtime = runtime
        .or_else(|| std::env::var_os("XDG_RUNTIME_DIR").map(PathBuf::from))
        .ok_or("XDG_RUNTIME_DIR missing")?;
    match action {
        "report" => {
            let mut probe = Probe::host(runtime, !no_libvirt);
            if let Some(s) = sys {
                probe.modules = s.join("lib-modules");
                probe.sys = s;
            }
            println!(
                "{}",
                serde_json::to_string_pretty(&report(&data, &probe)?).map_err(|e| e.to_string())?
            );
            Ok(())
        }
        "select" => {
            let doc = select(
                &data,
                &runtime,
                session.ok_or("--session is required")?,
                target.ok_or("--target is required")?,
            )?;
            println!("{doc}");
            Ok(())
        }
        "spice-filter" => {
            let df = data
                .get("remote")
                .and_then(|r| r.get("device_forwarding"))
                .ok_or("[remote.device_forwarding] missing")?;
            let policy = Policy::from_ssot(df)?;
            let deny_all = match session {
                Some(s) => {
                    validate_session(s, &strings(df.get("exclude_domains"), "exclude_domains")?)?;
                    if native_transport(s) != Some("spice-usbredir") {
                        return Err(format!("{s:?} is not a spice:<domain> session"));
                    }
                    let t = read_targets(&targets_path(df, &runtime)?);
                    let sel = t
                        .pointer(&format!(
                            "/sessions/{}/target",
                            s.replace('~', "~0").replace('/', "~1")
                        ))
                        .and_then(Json::as_str)
                        .map(str::to_owned);
                    sel.as_deref().unwrap_or(
                        df.get("default_target")
                            .and_then(Value::as_str)
                            .unwrap_or("auto"),
                    ) == "none"
                }
                None => false,
            };
            println!(
                "{}",
                if deny_all {
                    "-1,-1,-1,-1,0".to_owned()
                } else {
                    spice_filter(&policy)
                }
            );
            Ok(())
        }
        other => Err(format!(
            "unknown action {other:?} (report, select, spice-filter)"
        )),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ssot(allow: &str) -> Value {
        toml::from_str(&format!(
            "[remote.rdp.redirect]\nusb = true\n[remote.device_forwarding]\ntargets_file = \"mios/device-forwarding/targets.json\"\ndefault_target = \"auto\"\nlibvirt_uris = []\nexclude_domains = [\"mios-xbox*\"]\n[remote.device_forwarding.usb]\nallow = [{allow}]\ndeny = [\"class:09\", \"class:03\"]\nmax_devices = 4\n"
        ))
        .unwrap()
    }

    #[test]
    fn spice_filter_orders_deny_before_allow_and_ends_deny_all() {
        let p = Policy::from_ssot(
            &ssot("\"1234:abcd\", \"0781:*\", \"class:08\"")["remote"]["device_forwarding"],
        )
        .unwrap();
        assert_eq!(spice_filter(&p), "0x09,-1,-1,-1,0|0x03,-1,-1,-1,0|-1,0x1234,0xabcd,-1,1|-1,0x0781,-1,-1,1|0x08,-1,-1,-1,1|-1,-1,-1,-1,0");
        let empty = Policy::from_ssot(&ssot("")["remote"]["device_forwarding"]).unwrap();
        assert_eq!(
            spice_filter(&empty),
            "0x09,-1,-1,-1,0|0x03,-1,-1,-1,0|-1,-1,-1,-1,0",
            "empty allow redirects nothing"
        );
        assert!(Policy::from_ssot(&ssot("\"nonsense\"")["remote"]["device_forwarding"]).is_err());
    }

    #[test]
    fn excluded_domains_are_never_valid_sessions() {
        let ex = vec!["mios-xbox*".to_owned()];
        assert!(
            glob_match("mios-xbox*", "mios-xbox-current")
                && glob_match("mios-xbox*", "mios-xbox")
                && !glob_match("mios-xbox*", "steamos-recovery")
        );
        assert!(glob_match("a*b*c", "a-x-b-y-c") && !glob_match("a*b*c", "a-c"));
        assert!(validate_session("spice:mios-xbox-current", &ex).is_err());
        assert!(validate_session("spice:steamos-recovery", &ex).is_ok());
        assert!(validate_session("nested:alice", &ex).is_ok());
        for bad in ["nested:Alice", "nested:", "usb", "spice:a/b", "rdp:x"] {
            assert!(validate_session(bad, &ex).is_err(), "{bad}");
        }
    }

    #[test]
    fn select_writes_atomically_and_rejects_foreign_targets() {
        let rt = std::env::temp_dir().join(format!("mios-df-{}", std::process::id()));
        let _ = fs::remove_dir_all(&rt);
        let d = ssot("");
        assert!(select(&d, &rt, "rdp", "spice-usbredir").is_err());
        assert!(select(&d, &rt, "spice:mios-xbox", "none").is_err());
        let doc = select(&d, &rt, "rdp", "none").unwrap();
        assert_eq!(doc["sessions"]["rdp"]["target"], "none");
        select(&d, &rt, "nested:bob", "nested-evdev").unwrap();
        let f = rt.join("mios/device-forwarding/targets.json");
        let back = read_targets(&f);
        assert_eq!(
            (
                back["sessions"]["rdp"]["target"].as_str(),
                back["sessions"]["nested:bob"]["target"].as_str()
            ),
            (Some("none"), Some("nested-evdev"))
        );
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(
            fs::metadata(&f).unwrap().permissions().mode() & 0o777,
            0o600
        );
        let _ = fs::remove_dir_all(&rt);
    }

    #[test]
    fn report_on_fake_host_states_missing_module_honestly() {
        let rt = std::env::temp_dir().join(format!("mios-df-report-{}", std::process::id()));
        let _ = fs::remove_dir_all(&rt);
        let sys = rt.join("sys");
        fs::create_dir_all(sys.join("lib-modules")).unwrap();
        fs::write(
            sys.join("lib-modules/modules.dep"),
            "kernel/drivers/usb/usbip/usbip-core.ko.xz:\n",
        )
        .unwrap();
        let probe = Probe {
            sys: sys.clone(),
            modules: sys.join("lib-modules"),
            path: String::new(),
            runtime: rt.clone(),
            libvirt: false,
        };
        let r = report(&ssot("\"1234:abcd\""), &probe).unwrap();
        assert_eq!(r["transports"]["usbip_vhci"]["vhci"]["module"], "absent");
        assert_eq!(r["transports"]["rdp_urbdrc"]["available"], false);
        assert!(r["transports"]["rdp_urbdrc"]["reason"]
            .as_str()
            .unwrap()
            .contains("not installed"));
        assert_eq!(r["sessions"][0]["session"], "rdp");
        assert_eq!(
            r["sessions"][0]["effective"], "none",
            "auto with no endpoint forwards nothing"
        );
        fs::write(
            sys.join("lib-modules/modules.dep"),
            "kernel/drivers/usb/usbip/vhci-hcd.ko.xz: kernel/drivers/usb/usbip/usbip-core.ko.xz\n",
        )
        .unwrap();
        let r = report(&ssot(""), &probe).unwrap();
        assert_eq!(
            r["transports"]["usbip_vhci"]["vhci"]["module"],
            "available_not_loaded"
        );
        let dir = sys.join("devices/platform/vhci_hcd.0");
        fs::create_dir_all(&dir).unwrap();
        fs::create_dir_all(sys.join("module/vhci_hcd")).unwrap();
        fs::write(
            dir.join("status"),
            "hub port sta spd dev sockfd local_busid\nhs  0000 004 000 00000000 000000 0-0\n",
        )
        .unwrap();
        let r = report(&ssot(""), &probe).unwrap();
        assert_eq!(r["transports"]["usbip_vhci"]["vhci"]["available"], true);
        assert_eq!(
            r["transports"]["usbip_vhci"]["vhci"]["free_hs_ports"],
            json!([0])
        );
        let _ = fs::remove_dir_all(&rt);
    }
}
