// AI-hint: mios-fanctl -- CLI + ratatui TUI for the ventulusd fan-control daemon (status, fans, sensors, profiles, time-boxed overrides, pump-role provisioning).
// AI-related: /usr/lib/systemd/system/ventulusd.service.d/10-mios.conf, /usr/share/mios/fan-control/mios-fanctl.toml, /usr/share/mios/fan-control/profiles/mios-default.json
// AI-functions: main, run, cmd_status, cmd_fans, cmd_sensors, cmd_override, cmd_apply_roles, role_layers, apply_role_layers, check_override_pct

mod api;
mod config;
mod tui;
mod view;

use serde_json::{json, Value};
use std::collections::BTreeMap;
use std::time::{Duration, Instant};

const USAGE: &str = "mios-fanctl -- client for the ventulusd fan-control daemon (MiOS)

USAGE:
    mios-fanctl [--socket PATH] [--json] [COMMAND]

COMMANDS:
    status                     Daemon health, thermal state, active profile (default)
    fans                       Fan headers: role, control mode, duty, RPM
    sensors                    Temperature sensors (CPU first)
    profile                    Show the active profile
    profiles                   List profiles the daemon can activate
    activate [ID]              Activate a profile (default: client.default_profile)
    deactivate                 Deactivate; every header goes back to its firmware mode
    override CONTROL PCT [--secs N]
                               Hold a control at PCT % for N s (default 30, max 600),
                               then release; never below the floor (>= 30 %)
    apply-roles                Assign header roles from [roles] in the config (idempotent)
    tui                        Live dashboard (q quits, r refreshes)
    config                     Print the resolved client configuration
    raw PATH                   GET an API path and print the JSON

CONFIG: /usr/share/mios/fan-control/mios-fanctl.toml, overridden by
        /etc/mios/fan-control/mios-fanctl.toml and $MIOS_FANCTL_CONFIG.";

fn main() {
    let code = match run() {
        Ok(()) => 0,
        Err(e) => {
            eprintln!("mios-fanctl: {e}");
            1
        }
    };
    std::process::exit(code);
}

fn take_flag_value(args: &mut Vec<String>, flag: &str) -> Result<Option<String>, String> {
    if let Some(i) = args.iter().position(|a| a == flag) {
        args.remove(i);
        if i >= args.len() {
            return Err(format!("{flag} needs a value"));
        }
        return Ok(Some(args.remove(i)));
    }
    Ok(None)
}

fn take_switch(args: &mut Vec<String>, flag: &str) -> bool {
    if let Some(i) = args.iter().position(|a| a == flag) {
        args.remove(i);
        true
    } else {
        false
    }
}

fn run() -> Result<(), String> {
    let mut args: Vec<String> = std::env::args().skip(1).collect();
    if take_switch(&mut args, "--version") || take_switch(&mut args, "-V") {
        println!("mios-fanctl {}", env!("CARGO_PKG_VERSION"));
        return Ok(());
    }
    if args.iter().any(|a| a == "-h" || a == "--help" || a == "help") {
        println!("{USAGE}");
        return Ok(());
    }
    let socket = take_flag_value(&mut args, "--socket")?;
    let as_json = take_switch(&mut args, "--json");
    let cmd = if args.is_empty() { "status".to_string() } else { args.remove(0) };
    if cmd == "apply-roles" {
        // Pump roles are safety settings: a broken admin layer must not stop the vendor ones.
        let (mut cfg, layers, errors) = role_layers(&config_paths());
        if let Some(s) = socket {
            cfg.client.socket = s;
        }
        let c = api::Client::new(&cfg.client.socket, cfg.client.timeout_ms);
        return cmd_apply_roles(&c, &layers, &errors);
    }
    let mut cfg = config::load()?;
    if let Some(s) = socket {
        cfg.client.socket = s;
    }
    let c = api::Client::new(&cfg.client.socket, cfg.client.timeout_ms);

    match cmd.as_str() {
        "status" => cmd_status(&c, as_json),
        "fans" => cmd_fans(&c, as_json),
        "sensors" => cmd_sensors(&c, as_json),
        "headers" => print_json(&c.get("/hwmon/headers")?),
        "profile" => {
            let v = c.get("/profile/active")?;
            if as_json {
                print_json(&v)
            } else {
                println!("active profile: {}", view::active_profile(&v));
                Ok(())
            }
        }
        "profiles" => {
            let v = c.get("/profiles")?;
            if as_json {
                return print_json(&v);
            }
            for p in v.get("profiles").and_then(Value::as_array).cloned().unwrap_or_default() {
                println!(
                    "{:<24} {}",
                    p.get("id").and_then(Value::as_str).unwrap_or("?"),
                    p.get("name").and_then(Value::as_str).unwrap_or("")
                );
            }
            Ok(())
        }
        "activate" => {
            let id = args.first().cloned().unwrap_or_else(|| cfg.client.default_profile.clone());
            let v = c.post("/profile/activate", &json!({ "profile_id": id }))?;
            if as_json {
                print_json(&v)
            } else {
                println!("activated profile {id}");
                Ok(())
            }
        }
        "deactivate" => {
            let v = c.post("/profile/deactivate", &json!({}))?;
            if as_json {
                print_json(&v)
            } else {
                println!("profile deactivated: headers handed back to firmware");
                Ok(())
            }
        }
        "override" => cmd_override(&c, &cfg, &mut args),
        "tui" => tui::run(&c, &cfg),
        "config" => {
            println!("# sources: {}", if cfg.sources.is_empty() { "(built-in defaults)".into() } else { cfg.sources.join(", ") });
            print!("{}", toml::to_string_pretty(&cfg).map_err(|e| e.to_string())?);
            Ok(())
        }
        "raw" => {
            let p = args.first().ok_or("raw needs an API path, e.g. /status")?;
            print_json(&c.get(p)?)
        }
        other => Err(format!("unknown command {other:?}\n\n{USAGE}")),
    }
}

fn print_json(v: &Value) -> Result<(), String> {
    println!("{}", serde_json::to_string_pretty(v).map_err(|e| e.to_string())?);
    Ok(())
}

fn cmd_status(c: &api::Client, as_json: bool) -> Result<(), String> {
    let st = c.get("/status")?;
    let active = c.get("/profile/active")?;
    if as_json {
        return print_json(&json!({ "status": st, "profile": active }));
    }
    let s = |k: &str| st.get(k).and_then(Value::as_str).unwrap_or("?").to_string();
    println!("daemon        ventulusd {} (up {}s)", s("daemon_version"), st.get("uptime_seconds").and_then(Value::as_u64).unwrap_or(0));
    println!("overall       {}", s("overall_status"));
    println!("thermal_state {}", s("thermal_state"));
    println!("profile       {}", view::active_profile(&active));
    if let Some(r) = st.pointer("/readiness/top_summary").and_then(Value::as_str) {
        println!("readiness     {r}");
    }
    for o in st.get("overrides").and_then(Value::as_array).cloned().unwrap_or_default() {
        println!(
            "override      {} at {}% ({}s left)",
            o.get("control_id").and_then(Value::as_str).unwrap_or("?"),
            o.get("pwm_percent").and_then(Value::as_u64).unwrap_or(0),
            o.get("expires_in_secs").and_then(Value::as_u64).unwrap_or(0)
        );
    }
    if let Some(causes) = st.get("emergency_causes").and_then(Value::as_array).filter(|a| !a.is_empty()) {
        println!("EMERGENCY     {}", Value::Array(causes.clone()));
    }
    for sub in st.get("subsystems").and_then(Value::as_array).cloned().unwrap_or_default() {
        println!(
            "  {:<9} {:<9} {}",
            sub.get("name").and_then(Value::as_str).unwrap_or("?"),
            sub.get("status").and_then(Value::as_str).unwrap_or("?"),
            sub.get("reason").and_then(Value::as_str).unwrap_or("")
        );
    }
    Ok(())
}

fn cmd_fans(c: &api::Client, as_json: bool) -> Result<(), String> {
    let fans = c.get("/fans")?;
    let headers = c.get("/hwmon/headers")?;
    if as_json {
        return print_json(&json!({ "fans": fans, "headers": headers }));
    }
    println!("{:<16} {:<12} {:<16} {:>5} {:>6}  {:<12} FLAGS", "HEADER", "ROLE", "MODE", "DUTY", "RPM", "BAR");
    for r in view::fan_rows(&fans, &headers) {
        println!(
            "{:<16} {:<12} {:<16} {:>4}% {:>6}  {:<12} {}",
            r.header,
            r.role,
            r.mode,
            r.duty_pct.map(|d| d.to_string()).unwrap_or("-".into()),
            r.rpm.map(|d| d.to_string()).unwrap_or("-".into()),
            view::duty_bar(r.duty_pct, 10),
            r.flags
        );
    }
    Ok(())
}

fn cmd_sensors(c: &api::Client, as_json: bool) -> Result<(), String> {
    let v = c.get("/sensors")?;
    if as_json {
        return print_json(&v);
    }
    println!("{:<12} {:<16} {:<30} {:>8}", "KIND", "CHIP", "LABEL", "TEMP");
    for r in view::sensor_rows(&v) {
        println!(
            "{:<12} {:<16} {:<30} {:>8}",
            r.kind,
            r.chip,
            r.label,
            r.value_c.map(|t| format!("{t:.1}C")).unwrap_or("-".into())
        );
    }
    Ok(())
}

pub fn check_override_pct(pct: &str, floor: u8) -> Result<u8, String> {
    let p: u8 = pct.parse().map_err(|_| format!("PCT must be 0-100, got {pct:?}"))?;
    if p > 100 {
        return Err(format!("PCT must be 0-100, got {p}"));
    }
    if p < floor {
        return Err(format!("{p}% is below the safety floor of {floor}%"));
    }
    Ok(p)
}

fn cmd_override(c: &api::Client, cfg: &config::Config, args: &mut Vec<String>) -> Result<(), String> {
    let secs: u64 = take_flag_value(args, "--secs")?
        .map(|s| s.parse().map_err(|_| format!("--secs must be a number, got {s:?}")))
        .transpose()?
        .unwrap_or(30)
        .clamp(1, 600);
    let [control, pct] = args.as_slice() else {
        return Err("usage: mios-fanctl override CONTROL PCT [--secs N]".into());
    };
    let pct = check_override_pct(pct, cfg.effective_floor())?;
    let base = format!("/control/{control}/override");
    let grant = c.post(&base, &json!({ "pwm_percent": pct }))?;
    let token = grant
        .get("override_token")
        .and_then(Value::as_u64)
        .ok_or("daemon returned no override_token")?;
    let renew = Duration::from_secs(grant.get("renew_secs").and_then(Value::as_u64).unwrap_or(5).max(1));
    println!("override {control} -> {pct}% for {secs}s (token {token}; it lapses on its own if this client dies)");
    let deadline = Instant::now() + Duration::from_secs(secs);
    let mut result = Ok(());
    while Instant::now() < deadline {
        std::thread::sleep(renew.min(deadline.saturating_duration_since(Instant::now())));
        if Instant::now() >= deadline {
            break;
        }
        if let Err(e) = c.post(&format!("{base}/renew"), &json!({ "override_token": token })) {
            result = Err(format!("renew failed, override will lapse: {e}"));
            break;
        }
    }
    match c.delete(&base, &json!({ "override_token": token })) {
        Ok(_) => println!("override {control} released; back on its curve"),
        Err(e) => eprintln!("release failed ({e}); the override expires within its TTL"),
    }
    result
}

/// The config layers, lowest first (same order as `config::load`).
fn config_paths() -> Vec<String> {
    let mut paths = vec![config::VENDOR_PATH.to_string(), config::ADMIN_PATH.to_string()];
    if let Ok(p) = std::env::var("MIOS_FANCTL_CONFIG") {
        paths.push(p);
    }
    paths
}

/// One config layer's [roles], in apply order.
#[derive(Debug, Clone, PartialEq)]
struct RoleLayer {
    source: String,
    roles: BTreeMap<String, String>,
}

/// Tolerant layering for apply-roles: each present layer is validated on its own; one that
/// cannot be read, does not parse or has an unknown key is reported and skipped, so the
/// other layers (the vendor pump roles first of all) still apply. Returns the merged
/// config of the valid layers, their roles in order, and the errors.
fn role_layers(paths: &[String]) -> (config::Config, Vec<RoleLayer>, Vec<String>) {
    let mut good = Vec::new();
    let mut layers = Vec::new();
    let mut errors = Vec::new();
    for p in paths {
        match std::fs::read_to_string(p) {
            Ok(_) => {}
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => continue,
            Err(e) => {
                errors.push(format!("{p}: {e}"));
                continue;
            }
        }
        match config::load_from(std::slice::from_ref(p)) {
            Ok(c) => {
                good.push(p.clone());
                layers.push(RoleLayer { source: p.clone(), roles: c.roles });
            }
            Err(e) if e.starts_with(p.as_str()) => errors.push(e),
            Err(e) => errors.push(format!("{p}: {e}")),
        }
    }
    let cfg = config::load_from(&good).unwrap_or_else(|e| {
        errors.push(e);
        config::Config::default()
    });
    (cfg, layers, errors)
}

/// What apply-roles needs from the daemon (a trait so the ordering is testable offline).
trait RoleApi {
    fn headers(&self) -> Result<Value, String>;
    fn set_role(&self, id: &str, role: &str) -> Result<(), String>;
}

impl RoleApi for api::Client {
    fn headers(&self) -> Result<Value, String> {
        self.get("/hwmon/headers")
    }
    fn set_role(&self, id: &str, role: &str) -> Result<(), String> {
        self.post("/config/header-role", &json!({ "header_id": id, "role": role })).map(|_| ())
    }
}

/// Apply each layer's roles in order (vendor first, then the overlays), re-reading the
/// headers per layer; returns the number of failed assignments.
fn apply_role_layers(c: &dyn RoleApi, layers: &[RoleLayer]) -> Result<usize, String> {
    let mut failed = 0;
    for layer in layers.iter().filter(|l| !l.roles.is_empty()) {
        let headers = c.headers()?;
        let list = headers.get("headers").and_then(Value::as_array).cloned().unwrap_or_default();
        for (id, role) in &layer.roles {
            let Some(h) = list.iter().find(|h| h.get("id").and_then(Value::as_str) == Some(id.as_str())) else {
                println!("skip {id}: header not present on this machine");
                continue;
            };
            let current = h.get("role").and_then(Value::as_str).unwrap_or("");
            let source = h.get("role_source").and_then(Value::as_str).unwrap_or("");
            if current == role && source == "user_assigned" {
                println!("ok   {} already {role}", view::short_header(id));
                continue;
            }
            match c.set_role(id, role) {
                Ok(()) => println!("set  {} -> {role} ({})", view::short_header(id), layer.source),
                Err(e) => {
                    failed += 1;
                    eprintln!("fail {id}: {e}");
                }
            }
        }
    }
    Ok(failed)
}

fn cmd_apply_roles(c: &dyn RoleApi, layers: &[RoleLayer], errors: &[String]) -> Result<(), String> {
    for e in errors {
        eprintln!("config error (layer skipped; the other layers' roles still apply): {e}");
    }
    if layers.iter().all(|l| l.roles.is_empty()) {
        println!("no [roles] configured");
    }
    let failed = apply_role_layers(c, layers)?;
    match (failed, errors.len()) {
        (0, 0) => Ok(()),
        (0, n) => Err(format!("{n} config layer(s) skipped; valid roles were applied")),
        (f, 0) => Err(format!("{f} role assignment(s) failed")),
        (f, n) => Err(format!("{f} role assignment(s) failed; {n} config layer(s) skipped")),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn override_floor_enforced() {
        assert!(check_override_pct("29", 30).is_err());
        assert_eq!(check_override_pct("30", 30), Ok(30));
        assert!(check_override_pct("101", 30).is_err());
        assert!(check_override_pct("x", 30).is_err());
    }

    #[test]
    fn flag_parsing() {
        let mut a: Vec<String> = ["override", "c", "50", "--secs", "9"].iter().map(|s| s.to_string()).collect();
        assert_eq!(take_flag_value(&mut a, "--secs").unwrap(), Some("9".into()));
        assert_eq!(a.len(), 3);
        let mut b: Vec<String> = vec!["--secs".into()];
        assert!(take_flag_value(&mut b, "--secs").is_err());
    }

    use std::cell::RefCell;

    /// A fake daemon: headers with roles, recording every assignment in order.
    struct FakeDaemon {
        roles: RefCell<BTreeMap<String, String>>,
        posts: RefCell<Vec<(String, String)>>,
    }

    impl FakeDaemon {
        fn new(ids: &[&str]) -> Self {
            FakeDaemon {
                roles: RefCell::new(ids.iter().map(|i| (i.to_string(), "unknown".to_string())).collect()),
                posts: RefCell::new(Vec::new()),
            }
        }
    }

    impl RoleApi for FakeDaemon {
        fn headers(&self) -> Result<Value, String> {
            let list: Vec<Value> = self
                .roles
                .borrow()
                .iter()
                .map(|(id, r)| json!({"id": id, "role": r, "role_source": if r == "unknown" { "auto" } else { "user_assigned" }}))
                .collect();
            Ok(json!({ "headers": list }))
        }
        fn set_role(&self, id: &str, role: &str) -> Result<(), String> {
            self.posts.borrow_mut().push((id.into(), role.into()));
            self.roles.borrow_mut().insert(id.into(), role.into());
            Ok(())
        }
    }

    const PUMP6: &str = "hwmon:nct6799:nct6775.656:pwm6:pwm6";
    const PUMP7: &str = "hwmon:nct6799:nct6775.656:pwm7:pwm7";
    const VENDOR: &str = "[client]\nsocket = \"/run/ventulus/ventulus.sock\"\n[roles]\n\"hwmon:nct6799:nct6775.656:pwm6:pwm6\" = \"pump\"\n\"hwmon:nct6799:nct6775.656:pwm7:pwm7\" = \"pump\"\n";

    fn layer_files(tag: &str, admin: &str) -> (std::path::PathBuf, Vec<String>) {
        let d = std::env::temp_dir().join(format!("mios-fanctl-roles-{tag}-{}", std::process::id()));
        std::fs::create_dir_all(&d).unwrap();
        std::fs::write(d.join("vendor.toml"), VENDOR).unwrap();
        std::fs::write(d.join("admin.toml"), admin).unwrap();
        let paths = vec![
            d.join("vendor.toml").display().to_string(),
            d.join("admin.toml").display().to_string(),
            d.join("absent.toml").display().to_string(),
        ];
        (d, paths)
    }

    #[test]
    fn valid_admin_overlay_applies_after_vendor_roles() {
        let (d, paths) = layer_files("ok", "[client]\nrefresh_ms = 250\n[roles]\n\"hwmon:x:pwm2:pwm2\" = \"cpu_fan\"\n");
        let (cfg, layers, errors) = role_layers(&paths);
        assert!(errors.is_empty(), "{errors:?}");
        assert_eq!(cfg.client.refresh_ms, 250);
        assert_eq!(layers.len(), 2);
        let daemon = FakeDaemon::new(&[PUMP6, PUMP7, "hwmon:x:pwm2:pwm2"]);
        assert!(cmd_apply_roles(&daemon, &layers, &errors).is_ok());
        let posts = daemon.posts.borrow().clone();
        assert_eq!(
            posts,
            [(PUMP6.into(), "pump".into()), (PUMP7.into(), "pump".into()), ("hwmon:x:pwm2:pwm2".into(), "cpu_fan".into())]
        );
        // Idempotent: a second run assigns nothing.
        daemon.posts.borrow_mut().clear();
        assert!(cmd_apply_roles(&daemon, &layers, &errors).is_ok());
        assert!(daemon.posts.borrow().is_empty());
        std::fs::remove_dir_all(&d).ok();
    }

    #[test]
    fn broken_admin_config_still_applies_vendor_pump_roles() {
        for (tag, admin) in [
            ("unknown-key", "[client]\nsokcet = \"/tmp/x\"\n[roles]\n\"hwmon:x:pwm2:pwm2\" = \"cpu_fan\"\n"),
            ("parse", "[roles\n\"hwmon:x:pwm2:pwm2\" = "),
        ] {
            let (d, paths) = layer_files(tag, admin);
            // NEGATIVE control: the strict loader (what apply-roles used before) rejects the stack.
            assert!(config::load_from(&paths).is_err(), "{tag}");
            let (cfg, layers, errors) = role_layers(&paths);
            assert_eq!(errors.len(), 1, "{tag}: {errors:?}");
            assert!(errors[0].contains("admin.toml"), "{tag}: {errors:?}");
            assert_eq!(cfg.client.socket, "/run/ventulus/ventulus.sock");
            assert_eq!(layers.len(), 1, "{tag}");
            let daemon = FakeDaemon::new(&[PUMP6, PUMP7, "hwmon:x:pwm2:pwm2"]);
            let r = cmd_apply_roles(&daemon, &layers, &errors);
            // The config error is reported (non-zero exit) but the pumps got their roles.
            assert!(r.as_ref().is_err_and(|e| e.contains("skipped")), "{tag}: {r:?}");
            assert_eq!(*daemon.posts.borrow(), [(PUMP6.into(), "pump".into()), (PUMP7.into(), "pump".into())], "{tag}");
            std::fs::remove_dir_all(&d).ok();
        }
    }
}
