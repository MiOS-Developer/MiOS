// AI-hint: mios-displays -- native backend of the MiOS Displays app. Lists Hyprland outputs, applies a validated monitor change live through `hyprctl eval` hl.monitor with a GNOME-style "keep these settings?" timed revert (detached watchdog), and saves the confirmed layout as a [desktop.hyprland.monitors] mios.d drop-in that mios-gen render-hyprland re-projects.
// AI-related: tools/native/mios-displays/src/model.rs, usr/share/mios/displays/Displays.qml, usr/share/mios/mios.toml [desktop.displays] [desktop.hyprland.monitors], tools/native/mios-gen/src/render.rs, usr/share/applications/mios-displays.desktop
// AI-functions: main, run, settings, hyprctl, monitors, cmd_list, cmd_apply, cmd_confirm, cmd_revert, cmd_save, cmd_gui, restore, write_fragment, projection_lines, saved_entries, merged_fragment, stage, save_to

#![forbid(unsafe_code)]

mod model;

use model::{Change, Monitor, Rule};
use serde_json::{json, Value};
use std::os::unix::process::CommandExt;
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::sync::OnceLock;
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use std::{env, fs, thread};

const USAGE: &str = "mios-displays -- MiOS monitor settings backend (Hyprland, SSOT-persisted)

USAGE:
    mios-displays list                       Outputs, modes, saved SSOT layout, settings (JSON)
    mios-displays apply OUTPUT [--mode WxH@Hz|preferred|highres|highrr] [--position XxY|auto]
                     [--scale S] [--transform 0-7] [--enable|--disable] [--confirm SECS]
                                             Validate and apply live; reverts after SECS
                                             (default [desktop.displays].confirm_seconds; 0 = no timer)
                                             unless `confirm` runs first
    mios-displays confirm                    Keep the applied settings (cancels the revert)
    mios-displays revert                     Restore the settings from before the pending apply
    mios-displays save [--primary OUTPUT] [--tier host|user]
                                             Persist the live layout as the [desktop.hyprland.monitors]
                                             drop-in ([desktop.displays].fragment) and re-check the projection
    mios-displays suggest                    Recommended mode/refresh/scale per output, computed from
                                             what each output reports (native size, highest refresh,
                                             EDID physical size vs [desktop.displays.setup].reference_dpi)
    mios-displays gui [--setup]              Open the Displays app ([desktop.displays].ui); --setup opens
                                             the guided first-logon mode with the recommendation preselected
    mios-displays firstrun                   Once per user (marker [desktop.displays.setup].marker under
                                             $XDG_STATE_HOME): open the guided setup unless every connected
                                             output already has a saved layout
    mios-displays setup-done                 Write the first-logon marker

All commands print one JSON object on stdout; failures also exit 1.";

fn main() {
    let args: Vec<String> = env::args().skip(1).collect();
    match run(args) {
        Ok(v) => println!("{v}"),
        Err(e) => {
            println!("{}", json!({"ok": false, "error": e}));
            eprintln!("mios-displays: {e}");
            std::process::exit(1);
        }
    }
}

fn run(mut args: Vec<String>) -> Result<Value, String> {
    if args.is_empty() || args.iter().any(|a| a == "-h" || a == "--help" || a == "help") {
        println!("{USAGE}");
        std::process::exit(if args.is_empty() { 2 } else { 0 });
    }
    let cmd = args.remove(0);
    match cmd.as_str() {
        "list" => cmd_list(),
        "apply" => cmd_apply(args),
        "confirm" => cmd_confirm(),
        "revert" => cmd_revert(args),
        "save" => cmd_save(args),
        "gui" => cmd_gui(args),
        "suggest" => cmd_suggest(),
        "firstrun" => cmd_firstrun(),
        "setup-done" => cmd_setup_done(),
        other => Err(format!("unknown command {other:?}; see --help")),
    }
}

fn take(args: &mut Vec<String>, flag: &str) -> Result<Option<String>, String> {
    match args.iter().position(|a| a == flag) {
        Some(i) if i + 1 < args.len() => {
            args.remove(i);
            Ok(Some(args.remove(i)))
        }
        Some(_) => Err(format!("{flag} needs a value")),
        None => Ok(None),
    }
}

fn switch(args: &mut Vec<String>, flag: &str) -> bool {
    let at = args.iter().position(|a| a == flag);
    if let Some(i) = at {
        args.remove(i);
    }
    at.is_some()
}

fn no_extra(args: &[String]) -> Result<(), String> {
    match args.first() {
        Some(a) => Err(format!("unexpected argument {a:?}")),
        None => Ok(()),
    }
}

// ---- SSOT -------------------------------------------------------------------------------------

fn layered() -> Result<toml::Value, String> {
    let paths = mios_resolver::layers::resolve_layer_paths(None);
    mios_resolver::layers::merge_layer_files(&paths).map_err(|e| e.to_string())
}

fn at<'a>(v: &'a toml::Value, path: &str) -> Option<&'a toml::Value> {
    path.split('.').try_fold(v, |v, k| v.get(k))
}

/// [desktop.displays] from the layered SSOT.
fn settings(data: &toml::Value) -> Result<toml::Value, String> {
    at(data, "desktop.displays")
        .cloned()
        .ok_or_else(|| "missing SSOT [desktop.displays]".into())
}

fn setting_str(s: &toml::Value, key: &str) -> Result<String, String> {
    s.get(key)
        .and_then(toml::Value::as_str)
        .filter(|v| !v.is_empty())
        .map(str::to_string)
        .ok_or_else(|| format!("[desktop.displays].{key} must be a non-empty string"))
}

// ---- Hyprland ---------------------------------------------------------------------------------

fn runtime_dir() -> Result<PathBuf, String> {
    env::var_os("XDG_RUNTIME_DIR")
        .map(PathBuf::from)
        .ok_or_else(|| "XDG_RUNTIME_DIR is unset (a logind session is required)".into())
}

/// The live instance: the inherited signature if its socket exists, else the newest one
/// (a Hyprland restart leaves shells holding a stale signature).
fn instance() -> Result<String, String> {
    let base = runtime_dir()?.join("hypr");
    if let Ok(sig) = env::var("HYPRLAND_INSTANCE_SIGNATURE") {
        if base.join(&sig).join(".socket.sock").exists() {
            return Ok(sig);
        }
    }
    fs::read_dir(&base)
        .map_err(|e| format!("{}: {e}", base.display()))?
        .flatten()
        .filter(|e| e.path().join(".socket.sock").exists())
        .max_by_key(|e| e.metadata().and_then(|m| m.modified()).unwrap_or(UNIX_EPOCH))
        .map(|e| e.file_name().to_string_lossy().into_owned())
        .ok_or_else(|| "no running Hyprland instance".into())
}

fn hyprctl(args: &[&str]) -> Result<String, String> {
    let out = Command::new("hyprctl")
        .args(args)
        .env("HYPRLAND_INSTANCE_SIGNATURE", instance()?)
        .stdin(Stdio::null())
        .output()
        .map_err(|e| format!("hyprctl: {e}"))?;
    let text = String::from_utf8_lossy(&out.stdout).trim().to_string();
    if !out.status.success() {
        return Err(format!("hyprctl {}: {text}", args.first().unwrap_or(&"")));
    }
    Ok(text)
}

/// [desktop.displays].ignore_prefixes: virtual outputs (the RDP/VNC headless capture) that
/// the app neither shows, counts as "lit", nor persists into the SSOT.
fn ignore_prefixes() -> &'static [String] {
    static PREFIXES: OnceLock<Vec<String>> = OnceLock::new();
    PREFIXES.get_or_init(|| {
        layered()
            .ok()
            .and_then(|d| at(&d, "desktop.displays.ignore_prefixes").cloned())
            .and_then(|v| v.as_array().cloned())
            .map(|a| a.iter().filter_map(|s| s.as_str().map(str::to_string)).collect())
            .unwrap_or_default()
    })
}

fn monitors() -> Result<Vec<Monitor>, String> {
    let raw = hyprctl(&["-j", "monitors", "all"])?;
    let v: Value = serde_json::from_str(&raw).map_err(|e| format!("hyprctl monitors: {e}"))?;
    let all = v
        .as_array()
        .ok_or("hyprctl monitors: expected an array")?
        .iter()
        .map(Monitor::from_json)
        .collect::<Result<Vec<_>, _>>()?;
    let skip = ignore_prefixes();
    Ok(all
        .into_iter()
        .filter(|m| !skip.iter().any(|p| !p.is_empty() && m.name.starts_with(p.as_str())))
        .collect())
}

fn eval(lua: &str) -> Result<(), String> {
    let reply = hyprctl(&["eval", lua])?;
    if reply != "ok" {
        return Err(format!("Hyprland refused {lua}: {reply}"));
    }
    Ok(())
}

/// Whether the live output now shows `rule`.
fn shows(m: &Monitor, r: &Rule) -> bool {
    if r.disabled {
        return m.disabled;
    }
    if m.disabled || (m.scale - r.scale).abs() > 0.01 || m.transform != r.transform {
        return false;
    }
    if let Some((w, h, hz)) = model::parse_mode(&r.mode) {
        if m.width != w || m.height != h || hz.is_some_and(|hz| (m.refresh - hz).abs() > 0.05) {
            return false;
        }
    }
    !r.position.contains('x') || r.position.starts_with("auto") || r.position == m.position()
}

fn apply_rules(rules: &[Rule]) -> Result<(), String> {
    // Enable before disabling so an output is always lit.
    let (off, on): (Vec<&Rule>, Vec<&Rule>) = rules.iter().partition(|r| r.disabled);
    for r in on.into_iter().chain(off) {
        eval(&model::lua_rule(r))?;
    }
    Ok(())
}

fn wait_for(rules: &[Rule]) -> Result<Vec<Monitor>, String> {
    let mut last = Vec::new();
    for _ in 0..30 {
        last = monitors()?;
        if rules
            .iter()
            .all(|r| last.iter().find(|m| m.name == r.output).is_some_and(|m| shows(m, r)))
        {
            return Ok(last);
        }
        thread::sleep(Duration::from_millis(100));
    }
    Err(format!(
        "Hyprland did not reach the requested state; live: {}",
        last.iter().map(|m| m.to_json().to_string()).collect::<Vec<_>>().join(" ")
    ))
}

// ---- pending revert state ---------------------------------------------------------------------

fn state_dir() -> Result<PathBuf, String> {
    let d = runtime_dir()?.join("mios-displays");
    fs::create_dir_all(&d).map_err(|e| format!("{}: {e}", d.display()))?;
    Ok(d)
}

fn now() -> u64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0)
}

fn rule_json(r: &Rule) -> Value {
    json!({"output": r.output, "mode": r.mode, "position": r.position, "scale": r.scale,
           "transform": r.transform, "disabled": r.disabled})
}

fn rule_from_json(v: &Value) -> Option<Rule> {
    Some(Rule {
        output: v.get("output")?.as_str()?.to_string(),
        mode: v.get("mode")?.as_str()?.to_string(),
        position: v.get("position")?.as_str()?.to_string(),
        scale: v.get("scale")?.as_f64()?,
        transform: v.get("transform")?.as_i64()?,
        disabled: v.get("disabled")?.as_bool()?,
    })
}

/// {token, deadline, snapshot: [rules before the first unconfirmed apply]}
fn pending() -> Result<Option<Value>, String> {
    match fs::read_to_string(state_dir()?.join("pending.json")) {
        Ok(s) => Ok(serde_json::from_str(&s).ok()),
        Err(_) => Ok(None),
    }
}

fn clear_pending() -> Result<(), String> {
    match fs::remove_file(state_dir()?.join("pending.json")) {
        Err(e) if e.kind() != std::io::ErrorKind::NotFound => Err(e.to_string()),
        _ => Ok(()),
    }
}

fn restore(p: &Value) -> Result<Vec<Monitor>, String> {
    let rules: Vec<Rule> = p
        .get("snapshot")
        .and_then(Value::as_array)
        .map(|a| a.iter().filter_map(rule_from_json).collect())
        .unwrap_or_default();
    if rules.is_empty() {
        return Err("no snapshot to restore".into());
    }
    apply_rules(&rules)?;
    clear_pending()?;
    wait_for(&rules)
}

// ---- commands ---------------------------------------------------------------------------------

fn monitors_json(ms: &[Monitor]) -> Value {
    Value::Array(ms.iter().map(Monitor::to_json).collect())
}

fn cmd_list() -> Result<Value, String> {
    let data = layered()?;
    let s = settings(&data)?;
    let ms = monitors()?;
    let pend = pending()?.map(|p| {
        json!({"remaining": p.get("deadline").and_then(Value::as_u64).map(|d| d.saturating_sub(now())),
               "token": p.get("token")})
    });
    Ok(json!({
        "ok": true,
        "monitors": monitors_json(&ms),
        "saved": serde_json::to_value(at(&data, "desktop.hyprland.monitors").cloned()
            .unwrap_or(toml::Value::Table(Default::default()))).map_err(|e| e.to_string())?,
        "settings": serde_json::to_value(&s).map_err(|e| e.to_string())?,
        "pending": pend,
    }))
}

fn cmd_apply(mut args: Vec<String>) -> Result<Value, String> {
    let data = layered()?;
    let s = settings(&data)?;
    let mut change = Change {
        mode: take(&mut args, "--mode")?,
        position: take(&mut args, "--position")?,
        ..Default::default()
    };
    if let Some(v) = take(&mut args, "--scale")? {
        change.scale = Some(v.parse().map_err(|_| format!("invalid scale {v:?}"))?);
    }
    if let Some(v) = take(&mut args, "--transform")? {
        change.transform = Some(v.parse().map_err(|_| format!("invalid transform {v:?}"))?);
    }
    let confirm = match take(&mut args, "--confirm")? {
        Some(v) => v.parse::<u64>().map_err(|_| format!("invalid --confirm {v:?}"))?,
        None => s
            .get("confirm_seconds")
            .and_then(toml::Value::as_integer)
            .filter(|n| *n >= 0)
            .ok_or("[desktop.displays].confirm_seconds must be a non-negative integer")? as u64,
    };
    match (switch(&mut args, "--enable"), switch(&mut args, "--disable")) {
        (true, true) => return Err("--enable and --disable are exclusive".into()),
        (true, false) => change.disabled = Some(false),
        (false, true) => change.disabled = Some(true),
        _ => {}
    }
    if args.is_empty() {
        return Err("apply needs an OUTPUT".into());
    }
    change.output = args.remove(0);
    no_extra(&args)?;

    let before = monitors()?;
    // Validation happens before anything touches the compositor.
    let rule = change.validate(&before)?;
    let previous = pending()?;
    let snapshot = previous
        .as_ref()
        .and_then(|p| p.get("snapshot").cloned())
        .unwrap_or_else(|| Value::Array(before.iter().map(|m| rule_json(&Rule::of(m))).collect()));
    let token = format!("{}-{}", std::process::id(), SystemTime::now().duration_since(UNIX_EPOCH).map(|d| d.as_nanos()).unwrap_or(0));
    let pend = json!({"token": token, "deadline": now() + confirm, "snapshot": snapshot});
    let path = state_dir()?.join("pending.json");
    fs::write(&path, pend.to_string()).map_err(|e| format!("{}: {e}", path.display()))?;

    let applied = eval(&model::lua_rule(&rule)).and_then(|_| wait_for(std::slice::from_ref(&rule)));
    let after = match applied {
        Ok(after) => after,
        Err(e) => {
            let restored = restore(&pend).map(|_| "restored").unwrap_or("restore failed");
            return Err(format!("{e} ({restored} the previous settings)"));
        }
    };
    if confirm == 0 {
        clear_pending()?;
    } else {
        let exe = env::current_exe().map_err(|e| e.to_string())?;
        Command::new(exe)
            .args(["revert", "--if-pending", &token, "--after", &confirm.to_string()])
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .process_group(0)
            .spawn()
            .map_err(|e| format!("revert watchdog: {e}"))?;
    }
    Ok(json!({
        "ok": true,
        "applied": rule_json(&rule),
        "lua": model::lua_rule(&rule),
        "pending": (confirm > 0).then(|| json!({"token": token, "remaining": confirm})),
        "monitors": monitors_json(&after),
    }))
}

fn cmd_confirm() -> Result<Value, String> {
    let had = pending()?.is_some();
    clear_pending()?;
    Ok(json!({"ok": true, "confirmed": had}))
}

fn cmd_revert(mut args: Vec<String>) -> Result<Value, String> {
    let only = take(&mut args, "--if-pending")?;
    let after = take(&mut args, "--after")?
        .map(|v| v.parse::<u64>().map_err(|_| format!("invalid --after {v:?}")))
        .transpose()?;
    no_extra(&args)?;
    if let Some(secs) = after {
        // Watchdog: wait out the confirm window, polling so a confirm ends it early.
        let deadline = now() + secs;
        while now() < deadline {
            let current = pending()?;
            let token = current.as_ref().and_then(|p| p.get("token")).and_then(Value::as_str);
            if only.is_some() && token != only.as_deref() {
                return Ok(json!({"ok": true, "reverted": false}));
            }
            thread::sleep(Duration::from_millis(250));
        }
    }
    let Some(p) = pending()? else {
        return Ok(json!({"ok": true, "reverted": false}));
    };
    if only.is_some() && p.get("token").and_then(Value::as_str) != only.as_deref() {
        return Ok(json!({"ok": true, "reverted": false}));
    }
    let ms = restore(&p)?;
    Ok(json!({"ok": true, "reverted": true, "monitors": monitors_json(&ms)}))
}

/// Write `body` to `dest` atomically (sibling `.<name>.new`, then rename), escalating with
/// non-interactive sudo when the tier is root-owned. The temp name has no `.toml` extension,
/// so a reader never layers a half-written fragment.
fn write_fragment(dest: &Path, body: &str) -> Result<(), String> {
    let name = dest.file_name().ok_or("fragment has no file name")?.to_string_lossy().into_owned();
    let parent = dest.parent().ok_or("fragment has no parent directory")?;
    let staged = parent.join(format!(".{name}.new"));
    let direct = fs::create_dir_all(parent)
        .and_then(|_| write_synced(&staged, body))
        .and_then(|_| fs::rename(&staged, dest))
        .map_err(|e| e.to_string());
    if direct.is_ok() {
        // Best effort: persist the rename itself.
        let _ = fs::File::open(parent).and_then(|d| d.sync_all());
        return Ok(());
    }
    let _ = fs::remove_file(&staged);
    let tmp = state_dir()?.join("fragment.toml");
    fs::write(&tmp, body).map_err(|e| e.to_string())?;
    let installed = sudo(&["install", "-D", "-m", "0644"], &[&tmp, &staged]);
    let _ = fs::remove_file(&tmp);
    // `sync FILE` fsyncs the staged copy before the rename and the directory after it.
    let moved = installed
        .and_then(|_| sudo(&["sync", "--"], &[&staged]))
        .and_then(|_| sudo(&["mv", "-f", "-T"], &[&staged, dest]));
    if moved.is_err() {
        let _ = sudo(&["rm", "-f", "--"], &[&staged]);
    } else {
        let _ = sudo(&["sync", "--"], &[parent]);
    }
    moved.map_err(|e| format!("cannot write {} ({}; {e})", dest.display(), direct.err().unwrap_or_default()))
}

/// Write `body` to a new file and fsync it before it is renamed into place.
fn write_synced(path: &Path, body: &str) -> std::io::Result<()> {
    use std::io::Write;
    let mut f = fs::File::create(path)?;
    f.write_all(body.as_bytes())?;
    f.sync_all()
}

fn sudo(args: &[&str], paths: &[&Path]) -> Result<(), String> {
    let st = Command::new("sudo")
        .arg("-n")
        .args(args)
        .args(paths)
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .map_err(|e| format!("sudo: {e}"))?;
    if st.success() {
        Ok(())
    } else {
        Err(format!("sudo -n {} failed", args.first().unwrap_or(&"")))
    }
}

/// Remove a fragment, escalating like `write_fragment`; a missing file is fine.
fn remove_fragment(path: &Path) -> Result<(), String> {
    match fs::remove_file(path) {
        Ok(()) => Ok(()),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(()),
        Err(e) => sudo(&["rm", "-f", "--"], &[path]).map_err(|s| format!("{}: {e} ({s})", path.display())),
    }
}

/// Put `dest` back to `previous` (None: it did not exist).
fn restore_fragment(dest: &Path, previous: &Option<String>) -> Result<(), String> {
    match previous {
        Some(body) => write_fragment(dest, body),
        None => remove_fragment(dest),
    }
}

fn read_optional(path: &Path) -> Result<Option<String>, String> {
    match fs::read_to_string(path) {
        Ok(s) => Ok(Some(s)),
        Err(e) if e.kind() == std::io::ErrorKind::NotFound => Ok(None),
        Err(e) => Err(format!("{}: {e}", path.display())),
    }
}

/// The host and user drop-in directories a save writes into and a projection layers.
#[derive(Debug, Clone)]
struct Tiers {
    host_d: PathBuf,
    user_d: PathBuf,
}

/// The hl.monitor lines the runtime projection emits; `staged` re-points the host/user
/// drop-in tiers (the vendor and monolith tiers stay the deployed ones) so a candidate
/// layout is rendered before anything live changes.
fn projection_lines(staged: Option<&Tiers>) -> Result<Vec<String>, String> {
    let mut cmd = Command::new("mios-gen");
    cmd.args(["render-hyprland", "--root", "/", "--runtime"]).stdin(Stdio::null());
    if let Some(t) = staged {
        cmd.env("MIOS_HOST_TOML_D", &t.host_d).env("MIOS_USER_TOML_D", &t.user_d);
    }
    let out = cmd.output().map_err(|e| format!("mios-gen: {e}"))?;
    if !out.status.success() {
        return Err(format!("mios-gen render-hyprland failed: {}", String::from_utf8_lossy(&out.stderr).trim()));
    }
    Ok(String::from_utf8_lossy(&out.stdout)
        .lines()
        .filter(|l| l.starts_with("hl.monitor("))
        .map(str::to_string)
        .collect())
}

/// Every saved output must come out of the projection (mios-gen skips an unreadable overlay
/// silently, so a missing rule is how a bad drop-in shows).
fn check_projection(lines: &[String], outputs: &[String]) -> Result<(), String> {
    for o in outputs {
        let needle = format!("[\"output\"] = {}", serde_json::to_string(o).unwrap_or_default());
        if !lines.iter().any(|l| l.contains(&needle)) {
            return Err(format!("the projection has no rule for {o}"));
        }
    }
    Ok(())
}

type Monitors = toml::map::Map<String, toml::Value>;

/// The [desktop.hyprland.monitors] entries already saved in this drop-in, lowest tier first
/// (the user copy outranks the host one). An unreadable copy is refused, never overwritten.
fn saved_entries(paths: &[PathBuf]) -> Result<Monitors, String> {
    let mut out = Monitors::new();
    for p in paths {
        let Some(text) = read_optional(p)? else { continue };
        let v: toml::Value = text
            .parse()
            .map_err(|e| format!("{}: {e} (fix or remove it; refusing to overwrite saved layouts it cannot read)", p.display()))?;
        if let Some(t) = at(&v, "desktop.hyprland.monitors").and_then(toml::Value::as_table) {
            for (k, v) in t {
                out.insert(k.clone(), v.clone());
            }
        }
    }
    Ok(out)
}

/// The drop-in body: live outputs as `rules`, plus every saved entry for an output that is
/// not connected now (an unplugged monitor keeps its layout). `primary` is the only primary.
fn merged_fragment(rules: &[Rule], primary: Option<&str>, saved: &Monitors, skip: &[String]) -> Result<String, String> {
    let live = model::fragment_toml(rules, primary)?;
    let (header, body) = live.split_once("\n\n").ok_or("fragment without a header")?;
    let mut doc: toml::Value = body.parse().map_err(|e| format!("fragment: {e}"))?;
    let monitors = doc
        .get_mut("desktop")
        .and_then(|d| d.get_mut("hyprland"))
        .and_then(|h| h.get_mut("monitors"))
        .and_then(toml::Value::as_table_mut)
        .ok_or("fragment without [desktop.hyprland.monitors]")?;
    for (name, entry) in saved {
        if monitors.contains_key(name) || skip.iter().any(|p| !p.is_empty() && name.starts_with(p.as_str())) {
            continue;
        }
        let mut entry = entry.clone();
        let t = entry
            .as_table_mut()
            .ok_or_else(|| format!("saved desktop.hyprland.monitors.{name} is not a table"))?;
        t.remove("primary");
        if primary == Some(name.as_str()) {
            t.insert("primary".into(), toml::Value::Boolean(true));
        }
        monitors.insert(name.clone(), entry);
    }
    let body = toml::to_string(&doc).map_err(|e| e.to_string())?;
    Ok(format!("{header}\n\n{body}"))
}

/// The outputs a drop-in body saves (live and kept), each of which the projection must emit.
fn fragment_outputs(body: &str) -> Result<Vec<String>, String> {
    let v: toml::Value = body.parse().map_err(|e| format!("fragment: {e}"))?;
    Ok(at(&v, "desktop.hyprland.monitors")
        .and_then(toml::Value::as_table)
        .map(|t| t.keys().cloned().collect())
        .unwrap_or_default())
}

/// Copy the *.toml fragments of `tiers` into `dir`, then place `body` as the candidate
/// drop-in (and drop the user shadow for a host save), the way the save would leave them.
fn stage(tiers: &Tiers, fragment: &str, to_user: bool, body: &str, dir: &Path) -> Result<Tiers, String> {
    let staged = Tiers { host_d: dir.join("host.d"), user_d: dir.join("user.d") };
    for (from, to) in [(&tiers.host_d, &staged.host_d), (&tiers.user_d, &staged.user_d)] {
        fs::create_dir_all(to).map_err(|e| format!("{}: {e}", to.display()))?;
        let Ok(rd) = fs::read_dir(from) else { continue };
        for e in rd.flatten() {
            let p = e.path();
            if p.is_file() && p.extension().is_some_and(|x| x == "toml") {
                fs::copy(&p, to.join(e.file_name())).map_err(|err| format!("{}: {err}", p.display()))?;
            }
        }
    }
    let target = if to_user { &staged.user_d } else { &staged.host_d };
    fs::write(target.join(fragment), body).map_err(|e| e.to_string())?;
    if !to_user {
        let _ = fs::remove_file(staged.user_d.join(fragment));
    }
    Ok(staged)
}

#[derive(Debug)]
enum SaveError {
    /// The tier refused the write; nothing changed (the host save may fall back to the user tier).
    Write(String),
    /// Validation or post-write verification failed; any write was rolled back.
    Failed(String),
}

impl SaveError {
    fn text(self) -> String {
        match self {
            SaveError::Write(e) | SaveError::Failed(e) => e,
        }
    }
}

/// Validate `body` in a staged copy of the tiers, then write it atomically, remove the user
/// shadow (host save) and re-check the live projection; on failure restore both files.
fn save_to(
    tiers: &Tiers,
    fragment: &str,
    to_user: bool,
    body: &str,
    outputs: &[String],
    project: &dyn Fn(Option<&Tiers>) -> Result<Vec<String>, String>,
    stage_dir: &Path,
) -> Result<(PathBuf, Vec<String>, Vec<String>), SaveError> {
    let _ = fs::remove_dir_all(stage_dir);
    let checked = stage(tiers, fragment, to_user, body, stage_dir)
        .and_then(|staged| project(Some(&staged)))
        .and_then(|lines| check_projection(&lines, outputs));
    let _ = fs::remove_dir_all(stage_dir);
    checked.map_err(|e| SaveError::Failed(format!("not saved, nothing changed: the new layout fails the projection: {e}")))?;

    let target = if to_user { &tiers.user_d } else { &tiers.host_d }.join(fragment);
    let shadow = tiers.user_d.join(fragment);
    let prev_target = read_optional(&target).map_err(SaveError::Failed)?;
    let prev_shadow = if to_user { None } else { read_optional(&shadow).map_err(SaveError::Failed)? };
    write_fragment(&target, body).map_err(SaveError::Write)?;
    let mut notes = Vec::new();
    let verified = (|| {
        if prev_shadow.is_some() {
            // A user-tier copy of this drop-in would outrank a host save; the app owns both names.
            fs::remove_file(&shadow).map_err(|e| format!("{}: {e}", shadow.display()))?;
            notes.push(format!("removed the shadowing user-tier {}", shadow.display()));
        }
        let lines = project(None)?;
        check_projection(&lines, outputs)?;
        Ok::<_, String>(lines)
    })();
    match verified {
        Ok(lines) => Ok((target, notes, lines)),
        Err(e) => {
            let mut undo = vec![restore_fragment(&target, &prev_target)];
            if prev_shadow.is_some() {
                undo.push(restore_fragment(&shadow, &prev_shadow));
            }
            let undo: Vec<String> = undo.into_iter().filter_map(Result::err).collect();
            Err(SaveError::Failed(if undo.is_empty() {
                format!("saved {} but {e}; rolled back to the previous layout", target.display())
            } else {
                format!("saved {} but {e}; ROLLBACK FAILED: {}", target.display(), undo.join("; "))
            }))
        }
    }
}

fn cmd_save(mut args: Vec<String>) -> Result<Value, String> {
    let data = layered()?;
    let s = settings(&data)?;
    let tier = match take(&mut args, "--tier")? {
        Some(t) => t,
        None => setting_str(&s, "save_tier")?,
    };
    let primary_arg = take(&mut args, "--primary")?;
    no_extra(&args)?;
    if tier != "host" && tier != "user" {
        return Err(format!("unknown tier {tier:?}: expected host|user"));
    }
    if pending()?.is_some() {
        return Err("a change is still waiting for confirmation; confirm or revert it first".into());
    }
    let fragment = setting_str(&s, "fragment")?;
    if fragment.contains('/') || !fragment.ends_with(".toml") {
        return Err("[desktop.displays].fragment must be a bare *.toml file name".into());
    }
    let (_, _, _, host_d, _, user_d) = mios_resolver::layers::resolve_tier_dirs(None);
    let tiers = Tiers { host_d, user_d };
    // Saved entries for outputs that are not connected now survive the save.
    let saved = saved_entries(&[tiers.host_d.join(&fragment), tiers.user_d.join(&fragment)])?;
    let ms = monitors()?;
    let primary = primary_arg.or_else(|| {
        at(&data, "desktop.hyprland.monitors")
            .and_then(toml::Value::as_table)
            .and_then(|t| {
                t.iter()
                    .find(|(_, v)| v.get("primary").and_then(toml::Value::as_bool) == Some(true))
                    .map(|(k, _)| k.clone())
            })
    });
    if let Some(p) = &primary {
        if !ms.iter().any(|m| &m.name == p) && !saved.contains_key(p) {
            return Err(format!("primary {p:?} is not a known output ({})", model::names(&ms)));
        }
    }
    let rules: Vec<Rule> = ms.iter().map(Rule::of).collect();
    let body = merged_fragment(&rules, primary.as_deref(), &saved, ignore_prefixes())?;
    let outputs = fragment_outputs(&body)?;
    let fallback = s.get("fallback_user_tier").and_then(toml::Value::as_bool).unwrap_or(false);
    let stage_dir = state_dir()?.join(format!("stage-{}", std::process::id()));
    let project = |t: Option<&Tiers>| projection_lines(t);
    let save = |to_user| save_to(&tiers, &fragment, to_user, &body, &outputs, &project, &stage_dir);
    let (written, mut notes, lines) = match tier.as_str() {
        "host" => match save(false) {
            Ok(r) => r,
            Err(SaveError::Write(e)) if fallback => {
                let (p, mut n, l) = save(true).map_err(SaveError::text)?;
                n.insert(0, format!("host tier refused ({e}); saved to the user tier"));
                (p, n, l)
            }
            Err(e) => return Err(e.text()),
        },
        _ => save(true).map_err(SaveError::text)?,
    };
    let kept: Vec<&String> = outputs.iter().filter(|o| !ms.iter().any(|m| &&m.name == o)).collect();
    if !kept.is_empty() {
        notes.push(format!(
            "kept the saved layout of disconnected {}",
            kept.iter().map(|s| s.as_str()).collect::<Vec<_>>().join(", ")
        ));
    }
    Ok(json!({"ok": true, "path": written, "notes": notes, "projection": lines}))
}

fn cmd_gui(mut args: Vec<String>) -> Result<Value, String> {
    let setup = switch(&mut args, "--setup");
    no_extra(&args)?;
    let s = settings(&layered()?)?;
    let ui = setting_str(&s, "ui")?;
    let mut cmd = Command::new("quickshell");
    cmd.args(["--no-duplicate", "--path", &ui]);
    if setup {
        cmd.env("MIOS_DISPLAYS_SETUP", "1");
    }
    let err = cmd.exec();
    Err(format!("quickshell: {err}"))
}

fn setup_table(s: &toml::Value) -> Result<&toml::Value, String> {
    s.get("setup").ok_or_else(|| "missing SSOT [desktop.displays.setup]".into())
}

fn cmd_suggest() -> Result<Value, String> {
    let s = settings(&layered()?)?;
    let setup = setup_table(&s)?;
    let reference = setup
        .get("reference_dpi")
        .and_then(|v| v.as_float().or_else(|| v.as_integer().map(|i| i as f64)))
        .filter(|d| d.is_finite() && *d > 0.0)
        .ok_or("[desktop.displays.setup].reference_dpi must be a positive number")?;
    let scales: Vec<f64> = s
        .get("scales")
        .and_then(toml::Value::as_array)
        .map(|a| a.iter().filter_map(|v| v.as_float().or_else(|| v.as_integer().map(|i| i as f64))).collect())
        .unwrap_or_default();
    if scales.is_empty() {
        return Err("[desktop.displays].scales must list at least one scale".into());
    }
    let outputs: Vec<Value> = monitors()?
        .iter()
        .filter_map(|m| model::recommend(m, &scales, reference))
        .map(|r| json!({"output": r.output, "mode": r.mode.render(), "scale": r.scale, "dpi": r.dpi}))
        .collect();
    Ok(json!({"ok": true, "outputs": outputs, "reference_dpi": reference}))
}

fn marker() -> Result<PathBuf, String> {
    let s = settings(&layered()?)?;
    let rel = setup_table(&s)?
        .get("marker")
        .and_then(toml::Value::as_str)
        .filter(|m| !m.is_empty() && !m.starts_with('/') && !m.contains(".."))
        .ok_or("[desktop.displays.setup].marker must be a relative path")?
        .to_string();
    let state = env::var_os("XDG_STATE_HOME")
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .or_else(|| env::var_os("HOME").map(|h| PathBuf::from(h).join(".local/state")))
        .ok_or("neither XDG_STATE_HOME nor HOME is set")?;
    Ok(state.join(rel))
}

fn cmd_setup_done() -> Result<Value, String> {
    let m = marker()?;
    if let Some(d) = m.parent() {
        fs::create_dir_all(d).map_err(|e| format!("{}: {e}", d.display()))?;
    }
    fs::write(&m, format!("{}\n", now())).map_err(|e| format!("{}: {e}", m.display()))?;
    Ok(json!({"ok": true, "marker": m}))
}

fn cmd_firstrun() -> Result<Value, String> {
    let m = marker()?;
    if m.exists() {
        return Ok(json!({"ok": true, "ran": false, "reason": "already set up", "marker": m}));
    }
    let data = layered()?;
    let saved = at(&data, "desktop.hyprland.monitors").and_then(toml::Value::as_table);
    let outputs = monitors()?;
    let unsaved: Vec<&str> = outputs
        .iter()
        .filter(|o| !saved.is_some_and(|t| t.contains_key(&o.name)))
        .map(|o| o.name.as_str())
        .collect();
    if !outputs.is_empty() && unsaved.is_empty() {
        // Every connected output already has an operator layout (e.g. saved at setup by
        // another user or the admin): nothing to ask, just remember that this user was set up.
        cmd_setup_done()?;
        return Ok(json!({"ok": true, "ran": false, "reason": "every output has a saved layout"}));
    }
    cmd_gui(vec!["--setup".into()])
}

#[cfg(test)]
mod save_tests {
    use super::*;
    use std::cell::Cell;

    const FRAG: &str = "50-displays.toml";

    /// A private temp host + user tier (never the live /etc/mios/mios.d).
    struct TempTiers {
        root: PathBuf,
        tiers: Tiers,
    }

    impl TempTiers {
        fn new(tag: &str) -> TempTiers {
            let root = env::temp_dir().join(format!(
                "mios-displays-test-{tag}-{}-{}",
                std::process::id(),
                SystemTime::now().duration_since(UNIX_EPOCH).unwrap().as_nanos()
            ));
            let tiers = Tiers { host_d: root.join("etc/mios/mios.d"), user_d: root.join("home/.config/mios/mios.d") };
            fs::create_dir_all(&tiers.host_d).unwrap();
            fs::create_dir_all(&tiers.user_d).unwrap();
            TempTiers { root, tiers }
        }
        fn host(&self) -> PathBuf {
            self.tiers.host_d.join(FRAG)
        }
        fn shadow(&self) -> PathBuf {
            self.tiers.user_d.join(FRAG)
        }
        fn stage(&self) -> PathBuf {
            self.root.join("stage")
        }
    }

    impl Drop for TempTiers {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.root);
        }
    }

    fn rule(output: &str, mode: &str, position: &str) -> Rule {
        Rule { output: output.into(), mode: mode.into(), position: position.into(), scale: 1.0, transform: 0, disabled: false }
    }

    const SAVED: &str = "[desktop.hyprland.monitors.HDMI-A-1]\nmode = \"1920x1080@144.00\"\nposition = \"0x0\"\nscale = 1\ntransform = 0\nprimary = true\n\n[desktop.hyprland.monitors.DP-2]\nmode = \"2560x1440@165.00\"\nposition = \"1920x0\"\nscale = 1\ntransform = 0\n";

    /// A stand-in projection: layers host.d then user.d like mios-gen and emits one
    /// hl.monitor line per saved output; unparsable fragments are skipped like mios-gen does.
    fn fake_project(t: &Tiers) -> Vec<String> {
        let mut merged = Monitors::new();
        for d in [&t.host_d, &t.user_d] {
            let mut files: Vec<PathBuf> = fs::read_dir(d)
                .map(|rd| rd.flatten().map(|e| e.path()).filter(|p| p.extension().is_some_and(|x| x == "toml")).collect())
                .unwrap_or_default();
            files.sort();
            for f in files {
                let Ok(v) = fs::read_to_string(&f).unwrap().parse::<toml::Value>() else { continue };
                if let Some(m) = at(&v, "desktop.hyprland.monitors").and_then(toml::Value::as_table) {
                    for (k, v) in m {
                        merged.insert(k.clone(), v.clone());
                    }
                }
            }
        }
        merged.keys().map(|k| format!("hl.monitor({{[\"output\"] = {}}})", serde_json::to_string(k).unwrap())).collect()
    }

    /// Sorted: entry order depends on toml's `preserve_order` feature, which a shared workspace
    /// build (mios-gen enables it) switches on; the set of kept monitors is what matters.
    fn outputs_of(body: &str) -> Vec<String> {
        let mut outputs = fragment_outputs(body).unwrap();
        outputs.sort();
        outputs
    }

    #[test]
    fn merge_keeps_unplugged_saved_monitors() {
        let t = TempTiers::new("merge");
        fs::write(t.host(), SAVED).unwrap();
        let saved = saved_entries(&[t.host()]).unwrap();
        // POSITIVE: DP-2 is unplugged now; its saved layout survives, HDMI-A-1 takes the live values.
        let body = merged_fragment(&[rule("HDMI-A-1", "1920x1080@60.00", "0x0")], Some("HDMI-A-1"), &saved, &[]).unwrap();
        let v: toml::Value = body.split_once("\n\n").unwrap().1.parse().unwrap();
        let m = &v["desktop"]["hyprland"]["monitors"];
        assert_eq!(m["HDMI-A-1"]["mode"].as_str(), Some("1920x1080@60.00"));
        assert_eq!(m["HDMI-A-1"]["primary"].as_bool(), Some(true));
        assert_eq!(m["DP-2"]["mode"].as_str(), Some("2560x1440@165.00"));
        assert_eq!(m["DP-2"]["position"].as_str(), Some("1920x0"));
        assert!(m["DP-2"].get("primary").is_none());
        assert!(body.starts_with("# AI-hint:"));
        // NEGATIVE (the defect): the live-only body drops DP-2.
        let live_only = model::fragment_toml(&[rule("HDMI-A-1", "1920x1080@60.00", "0x0")], Some("HDMI-A-1")).unwrap();
        assert!(!outputs_of(&live_only).contains(&"DP-2".to_string()));
        assert_eq!(outputs_of(&body), ["DP-2", "HDMI-A-1"]);
        // Primary moves to the kept output, and only one entry stays primary.
        let body = merged_fragment(&[rule("HDMI-A-1", "1920x1080@60.00", "0x0")], Some("DP-2"), &saved, &[]).unwrap();
        let v: toml::Value = body.split_once("\n\n").unwrap().1.parse().unwrap();
        assert_eq!(v["desktop"]["hyprland"]["monitors"]["DP-2"]["primary"].as_bool(), Some(true));
        assert!(v["desktop"]["hyprland"]["monitors"]["HDMI-A-1"].get("primary").is_none());
        // Ignored virtual outputs are never carried over.
        let body = merged_fragment(&[rule("HDMI-A-1", "1920x1080@60.00", "0x0")], None, &saved, &["DP-".into()]).unwrap();
        assert_eq!(outputs_of(&body), ["HDMI-A-1"]);
    }

    #[test]
    fn unreadable_saved_fragment_is_refused_not_overwritten() {
        let t = TempTiers::new("corrupt");
        fs::write(t.host(), "[desktop.hyprland.monitors.DP-2\nmode = ").unwrap();
        assert!(saved_entries(&[t.host(), t.shadow()]).unwrap_err().contains("refusing to overwrite"));
        // The user copy outranks the host copy.
        fs::write(t.host(), SAVED).unwrap();
        fs::write(t.shadow(), "[desktop.hyprland.monitors.DP-2]\nmode = \"preferred\"\n").unwrap();
        let s = saved_entries(&[t.host(), t.shadow()]).unwrap();
        assert_eq!(s["DP-2"]["mode"].as_str(), Some("preferred"));
        assert_eq!(s["HDMI-A-1"]["mode"].as_str(), Some("1920x1080@144.00"));
    }

    #[test]
    fn host_save_validates_writes_atomically_and_removes_the_shadow() {
        let t = TempTiers::new("ok");
        fs::write(t.host(), SAVED).unwrap();
        fs::write(t.shadow(), "[desktop.hyprland.monitors.HDMI-A-1]\nmode = \"preferred\"\n").unwrap();
        let saved = saved_entries(&[t.host(), t.shadow()]).unwrap();
        let body = merged_fragment(&[rule("HDMI-A-1", "1920x1080@144.00", "0x0")], Some("HDMI-A-1"), &saved, &[]).unwrap();
        let outputs = outputs_of(&body);
        let staged_seen = Cell::new(false);
        let project = |s: Option<&Tiers>| {
            let tiers = s.unwrap_or(&t.tiers);
            if s.is_some() {
                // The candidate is rendered from the stage, before the live tier changes.
                staged_seen.set(true);
                assert_eq!(fs::read_to_string(t.host()).unwrap(), SAVED);
                assert!(!tiers.user_d.join(FRAG).exists());
            }
            Ok(fake_project(tiers))
        };
        let (path, notes, lines) = save_to(&t.tiers, FRAG, false, &body, &outputs, &project, &t.stage()).unwrap();
        assert!(staged_seen.get());
        assert_eq!(path, t.host());
        assert_eq!(fs::read_to_string(t.host()).unwrap(), body);
        assert!(!t.shadow().exists());
        assert!(notes.iter().any(|n| n.contains("shadowing")));
        assert_eq!(lines.len(), 2);
        assert!(!t.stage().exists());
        let leftovers: Vec<_> = fs::read_dir(&t.tiers.host_d).unwrap().flatten().map(|e| e.file_name()).collect();
        assert_eq!(leftovers, [std::ffi::OsString::from(FRAG)]);
    }

    #[test]
    fn invalid_layout_is_rejected_before_anything_is_written() {
        let t = TempTiers::new("invalid");
        fs::write(t.host(), SAVED).unwrap();
        fs::write(t.shadow(), "[desktop.hyprland.monitors.HDMI-A-1]\nmode = \"preferred\"\n").unwrap();
        let project = |s: Option<&Tiers>| {
            assert!(s.is_some(), "the live projection must not run for a rejected layout");
            Err::<Vec<String>, _>("desktop.hyprland.monitors.HDMI-A-1.mode must be preferred|...".to_string())
        };
        let body = merged_fragment(&[rule("HDMI-A-1", "1920x1080@144.00", "0x0")], None, &Monitors::new(), &[]).unwrap();
        let err = save_to(&t.tiers, FRAG, false, &body, &["HDMI-A-1".into()], &project, &t.stage()).unwrap_err();
        assert!(matches!(&err, SaveError::Failed(e) if e.contains("nothing changed")), "{err:?}");
        // NEGATIVE control on the defect: host drop-in and the user copy are untouched.
        assert_eq!(fs::read_to_string(t.host()).unwrap(), SAVED);
        assert!(t.shadow().exists());
        // A staged projection that silently skips a bad drop-in (no rule) is a failure too.
        let skipping = |_: Option<&Tiers>| Ok(Vec::<String>::new());
        assert!(save_to(&t.tiers, FRAG, false, &body, &["HDMI-A-1".into()], &skipping, &t.stage()).is_err());
        assert_eq!(fs::read_to_string(t.host()).unwrap(), SAVED);
    }

    #[test]
    fn failed_live_check_rolls_back_target_and_shadow() {
        let t = TempTiers::new("rollback");
        fs::write(t.host(), SAVED).unwrap();
        let shadow = "[desktop.hyprland.monitors.HDMI-A-1]\nmode = \"preferred\"\n";
        fs::write(t.shadow(), shadow).unwrap();
        let body = merged_fragment(&[rule("HDMI-A-1", "1920x1080@60.00", "0x0")], None, &Monitors::new(), &[]).unwrap();
        let project = |s: Option<&Tiers>| match s {
            Some(st) => Ok(fake_project(st)),
            None => Err("mios-gen render-hyprland failed: boom".to_string()),
        };
        let err = save_to(&t.tiers, FRAG, false, &body, &["HDMI-A-1".into()], &project, &t.stage()).unwrap_err();
        assert!(matches!(&err, SaveError::Failed(e) if e.contains("rolled back")), "{err:?}");
        assert_eq!(fs::read_to_string(t.host()).unwrap(), SAVED);
        assert_eq!(fs::read_to_string(t.shadow()).unwrap(), shadow);
        // A first save (no previous drop-in) is removed again on failure.
        fs::remove_file(t.host()).unwrap();
        assert!(save_to(&t.tiers, FRAG, false, &body, &["HDMI-A-1".into()], &project, &t.stage()).is_err());
        assert!(!t.host().exists());
    }

    /// The real projection over a temp host tier, when mios-gen is installed (host runs).
    #[test]
    fn real_projection_renders_the_staged_candidate() {
        // Only where a mios-gen with render-hyprland projects the deployed tiers (the host,
        // not the build container's older toolchain image).
        if projection_lines(None).is_err() {
            eprintln!("skip: no working mios-gen render-hyprland here");
            return;
        }
        let t = TempTiers::new("real");
        let project = |s: Option<&Tiers>| projection_lines(Some(s.unwrap_or(&t.tiers)));
        let good = merged_fragment(&[rule("TEST-9", "1280x720@60.00", "5000x0")], None, &Monitors::new(), &[]).unwrap();
        let staged = stage(&t.tiers, FRAG, false, &good, &t.stage()).unwrap();
        let lines = projection_lines(Some(&staged)).unwrap();
        assert!(check_projection(&lines, &["TEST-9".into()]).is_ok(), "{lines:?}");
        let bad = good.replace("1280x720@60.00", "garbage");
        let err = save_to(&t.tiers, FRAG, false, &bad, &["TEST-9".into()], &project, &t.stage());
        assert!(matches!(&err, Err(SaveError::Failed(e)) if e.contains("nothing changed")), "{err:?}");
        assert!(!t.host().exists());
    }
}
