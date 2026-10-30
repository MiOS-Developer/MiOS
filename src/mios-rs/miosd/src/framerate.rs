// AI-hint: miosd framerate -- the desktop frame-rate policy daemon ([desktop.framerate]; user unit mios-framerate.service).
//          Watches the power profile (net.hadess.PowerProfiles / tuned), UPower OnBattery and GameMode's client count
//          on D-Bus plus Hyprland's event socket; debounces; picks the tier (unlocked > gaming > battery > profile,
//          mios_resolver::framerate) and applies it in ONE `hyprctl eval`: the mios-pacing cap (VRR outputs), the
//          Hyprglass glass rate, Hypr3D's room/glass/dome rates; on an output without VRR (or in mode_outputs) it
//          switches to the nearest advertised mode or, with modes = "extended-vblank", a modeline derived from the
//          panel's sysfs EDID (`miosd framerate modeline` prints it) (verified, reverted and blocklisted on failure; hysteresis; pinned modes untouched; a new
//          mode is restored after mode_confirm_s unless `miosd framerate confirm-mode` keeps it; stopping restores every
//          mode the daemon changed). Writes
//          $XDG_RUNTIME_DIR/<state_file> for the login projection, Host.qml (wallpaper rate) and the Hypr3D room toggle.
// AI-related: src/mios-rs/miosd/src/framerate/plan.rs, tools/native/mios-resolver/src/framerate.rs,
// AI-related: usr/share/mios/mios.toml [desktop.framerate] [desktop.hyprland.mios-pacing], usr/lib/systemd/user/mios-framerate.service,
// AI-related: usr/share/mios/hyprland/plugins/mios-pacing, usr/share/mios/wallpaper/Host.qml

pub mod plan;

use std::collections::{BTreeMap, BTreeSet};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant};

use futures_util::StreamExt;
use mios_resolver::framerate::{ppd_profile_for_tuned, Inputs, Policy, State};
use serde_json::Value as Json;
use toml::Value;
use zbus::Connection;

use crate::placement::Hypr;
use plan::{Consumers, Env, GamingHold, Mech, ModeSets, ModesetGate, Monitor, PendingMode, Plan};

#[derive(clap::Subcommand, Debug)]
pub enum FramerateCommand {
    /// Run the policy daemon (user unit mios-framerate.service): watch the signals, apply the tier, write the state file
    Serve,
    /// The running tier and rates (the daemon's state file), or the plan for the current inputs when it is not running
    Status {
        #[arg(long)]
        json: bool,
    },
    /// Compute the plan from the current inputs and outputs and apply it once (--dry-run: print it, change nothing)
    Apply {
        #[arg(long)]
        dry_run: bool,
    },
    /// Persist the unlocked opt-in ("at risk": no cap) in the user tier and tell the running daemon (SIGHUP)
    SetUnlocked {
        #[arg(value_parser = ["on", "off", "toggle"])]
        state: String,
        /// Write the user-tier fragment only; do not signal mios-framerate.service
        #[arg(long)]
        no_reload: bool,
    },
    /// Keep the display mode the daemon just switched to (without this it restores the previous mode after
    /// [desktop.framerate].mode_confirm_s and makes no more automatic modesets on that panel); --reset forgets every
    /// confirmed and declined panel instead
    ConfirmMode {
        #[arg(long)]
        reset: bool,
    },
    /// Read-only: the extended-vblank modeline for OUTPUT (the panel's fastest EDID timing at its current size slowed
    /// to --hz, default the current tier's desktop rate, by a longer vertical front porch), with the `hl.monitor` that
    /// applies it and the one that restores the current mode. `--edid FILE --hz N [--size WxH]` needs no compositor.
    Modeline {
        output: Option<String>,
        #[arg(long)]
        hz: Option<f64>,
        /// an EDID blob (binary, or hex text with # comments) instead of the output's sysfs EDID
        #[arg(long)]
        edid: Option<PathBuf>,
        /// WxH for --edid (default: the size of its first detailed timing)
        #[arg(long)]
        size: Option<String>,
        #[arg(long)]
        json: bool,
    },
}

pub async fn run(cmd: &FramerateCommand) -> Result<(), String> {
    match cmd {
        FramerateCommand::Serve => serve().await,
        FramerateCommand::Status { json } => status(*json).await,
        FramerateCommand::Apply { dry_run } => apply_once(*dry_run).await,
        FramerateCommand::SetUnlocked { state, no_reload } => set_unlocked(state, *no_reload),
        FramerateCommand::ConfirmMode { reset } => confirm_mode(*reset),
        FramerateCommand::Modeline { output, hz, edid, size, json } => modeline(output.as_deref(), *hz, edid.as_deref(), size.as_deref(), *json).await,
    }
}

fn log(msg: &str) {
    eprintln!("[miosd framerate] {msg}");
}

// ---------------------------------------------------------------- configuration

/// Everything the daemon reads from the merged SSOT (reloaded on SIGHUP).
#[derive(Clone, Debug)]
pub struct Config {
    pub policy: Policy,
    pub require_vrr: bool,
    pub pinned: BTreeSet<String>,
    /// each output's configured monitor-rule mode ([desktop.hyprland.monitors.<name>].mode), and the catch-all's
    /// ([desktop.hyprland.monitor].mode, "preferred" by default): the way back from a mode an output shows but does
    /// not advertise (plan::restorable_mode)
    pub rules: BTreeMap<String, String>,
    pub default_rule: String,
}

fn mios_root() -> PathBuf {
    std::env::var_os("MIOS_ROOT").map(PathBuf::from).unwrap_or_else(|| PathBuf::from("/"))
}

impl Config {
    /// Ok(None): [desktop.framerate] absent or disabled.
    pub fn from_ssot(doc: &Value) -> Result<Option<Config>, String> {
        let Some(policy) = Policy::from_ssot(doc)?.filter(|p| p.enabled) else { return Ok(None) };
        let pacing = doc.get("desktop").and_then(|d| d.get("hyprland")).and_then(|h| h.get(&policy.pacing_plugin));
        let require_vrr = match pacing.and_then(|p| p.get("config")).and_then(|c| c.get("require_vrr")) {
            None => true,
            Some(v) => v.as_bool().ok_or(format!("[desktop.hyprland.{}.config] require_vrr must be a boolean", policy.pacing_plugin))?,
        };
        // A mode the operator pinned ("WxH@Hz") is never changed; preferred / highrr / highres are not pins.
        let pinned = doc
            .get("desktop")
            .and_then(|d| d.get("hyprland"))
            .and_then(|h| h.get("monitors"))
            .and_then(Value::as_table)
            .into_iter()
            .flatten()
            .filter(|(_, m)| m.get("mode").and_then(Value::as_str).and_then(plan::parse_mode).is_some())
            .map(|(name, _)| name.clone())
            .collect();
        let hypr = doc.get("desktop").and_then(|d| d.get("hyprland"));
        let rules = hypr
            .and_then(|h| h.get("monitors"))
            .and_then(Value::as_table)
            .into_iter()
            .flatten()
            .filter_map(|(name, m)| m.get("mode").and_then(Value::as_str).map(|v| (name.clone(), v.trim().to_owned())))
            .collect();
        let default_rule = hypr.and_then(|h| h.get("monitor")).and_then(|m| m.get("mode")).and_then(Value::as_str).unwrap_or("preferred").trim().to_owned();
        Ok(Some(Config { policy, require_vrr, pinned, rules, default_rule }))
    }

    /// The configured monitor-rule mode of `output` (its own rule, else the catch-all's).
    pub fn rule(&self, output: &str) -> String {
        self.rules.get(output).cloned().unwrap_or_else(|| self.default_rule.clone())
    }

    pub fn load() -> Result<Option<Config>, String> {
        let doc = mios_resolver::resolve_merged(Some(&mios_root()), false).map_err(|e| e.to_string())?;
        Config::from_ssot(&doc)
    }
}

// ---------------------------------------------------------------- signals (D-Bus, sysfs fallbacks)

const CALL: Duration = Duration::from_millis(1500);
const PPD: [(&str, &str, &str); 2] = [
    ("net.hadess.PowerProfiles", "/net/hadess/PowerProfiles", "net.hadess.PowerProfiles"),
    ("org.freedesktop.UPower.PowerProfiles", "/org/freedesktop/UPower/PowerProfiles", "org.freedesktop.UPower.PowerProfiles"),
];
const UPOWER: (&str, &str, &str) = ("org.freedesktop.UPower", "/org/freedesktop/UPower", "org.freedesktop.UPower");
const GAMEMODE: (&str, &str, &str) = ("com.feralinteractive.GameMode", "/com/feralinteractive/GameMode", "com.feralinteractive.GameMode");
const TUNED: (&str, &str, &str) = ("com.redhat.tuned", "/Tuned", "com.redhat.tuned.control");
/// /usr/local/libexec/mios-gamemode (gamemode.ini [custom]) marks a running game here.
const GAMEMODE_MARK: &str = "/run/mios-power/gamemode.active";

async fn property(conn: &Connection, (dest, path, iface): (&str, &str, &str), name: &str) -> Option<zbus::zvariant::OwnedValue> {
    let proxy = zbus::fdo::PropertiesProxy::builder(conn).destination(dest).ok()?.path(path).ok()?.build().await.ok()?;
    let iface = zbus::names::InterfaceName::try_from(iface).ok()?;
    tokio::time::timeout(CALL, proxy.get(iface, name)).await.ok()?.ok()
}

/// Where each input came from (status output, logs).
#[derive(Clone, Debug, Default, PartialEq, serde::Serialize)]
pub struct Sources {
    pub profile: String,
    pub battery: String,
    pub gaming: String,
}

/// The raw inputs (gaming before the exit hold) from D-Bus, with the sysfs / file fallbacks.
pub async fn read_inputs(sys: Option<&Connection>, ses: Option<&Connection>, cfg: &Config, hypr: Option<&Hypr>) -> (Inputs, Sources) {
    let mut src = Sources::default();
    let mut profile = None;
    if let Some(c) = sys {
        for svc in PPD {
            if let Some(p) = property(c, svc, "ActiveProfile").await.and_then(|v| String::try_from(v).ok()) {
                src.profile = svc.0.into();
                profile = Some(p);
                break;
            }
        }
        if profile.is_none() {
            let reply = tokio::time::timeout(CALL, c.call_method(Some(TUNED.0), TUNED.1, Some(TUNED.2), "active_profile", &())).await;
            if let Ok(Ok(msg)) = reply {
                if let Ok(tuned) = msg.body().deserialize::<String>() {
                    let conf = std::fs::read_to_string(mios_root().join("etc/tuned/ppd.conf")).unwrap_or_default();
                    profile = ppd_profile_for_tuned(&conf, &tuned);
                    src.profile = format!("com.redhat.tuned ({tuned})");
                }
            }
        }
    }
    if profile.is_none() && src.profile.is_empty() {
        profile = mios_resolver::framerate::login_profile(&mios_root());
        src.profile = "etc/tuned/ppd_base_profile".into();
    }
    let mut on_battery = None;
    if let Some(c) = sys {
        on_battery = property(c, UPOWER, "OnBattery").await.and_then(|v| bool::try_from(v).ok());
        if on_battery.is_some() {
            src.battery = UPOWER.0.into();
        }
    }
    let on_battery = on_battery.unwrap_or_else(|| {
        src.battery = "sysfs power_supply".into();
        mios_resolver::framerate::on_battery(&mios_resolver::framerate::read_power_supplies(&mios_root()))
    });
    let mut gaming = false;
    if cfg.policy.gaming.gamemode {
        let clients = match ses {
            Some(c) => property(c, GAMEMODE, "ClientCount").await.and_then(|v| i32::try_from(v).ok()),
            None => None,
        };
        gaming = match clients {
            Some(n) => {
                src.gaming = format!("{} ClientCount={n}", GAMEMODE.0);
                n > 0
            }
            None => {
                src.gaming = GAMEMODE_MARK.into();
                Path::new(GAMEMODE_MARK).exists()
            }
        };
    }
    if !gaming && cfg.policy.gaming.fullscreen_game {
        if let Some(h) = hypr {
            if fullscreen_game(h, &cfg.policy) {
                src.gaming = "fullscreen game window".into();
                gaming = true;
            }
        }
    }
    (Inputs { gaming, on_battery, profile }, src)
}

/// The active window is fullscreen on a local output and is a game (content type "game" or class matching game_class).
fn fullscreen_game(h: &Hypr, policy: &Policy) -> bool {
    let Ok(w) = h.json("activewindow") else { return false };
    let Ok(class) = regex::Regex::new(&policy.gaming.game_class) else { return false };
    let full = w.get("fullscreen").and_then(Json::as_i64).unwrap_or(0) > 0;
    let game = w.get("contentType").and_then(Json::as_str) == Some("game")
        || class.is_match(w.get("class").and_then(Json::as_str).unwrap_or(""));
    let monitor = w.get("monitor").and_then(Json::as_i64);
    let local = h
        .json("monitors")
        .ok()
        .and_then(|m| m.as_array().cloned())
        .into_iter()
        .flatten()
        .find(|m| m.get("id").and_then(Json::as_i64) == monitor)
        .and_then(|m| m.get("name").and_then(Json::as_str).map(|n| !policy.excluded(n)))
        .unwrap_or(false);
    full && game && local
}

// ---------------------------------------------------------------- compositor

fn monitors(h: &Hypr) -> Result<Vec<Monitor>, String> {
    let mut m = plan::parse_monitors(&h.json("monitors all")?);
    if let Some(spec) = std::env::var_os("MIOS_FRAMERATE_TEST_MODES") {
        plan::inject_test_modes(&mut m, &spec.to_string_lossy());
    }
    for x in &mut m {
        x.edid = output_edid(&x.name);
    }
    Ok(m)
}

const DRM_SYSFS: &str = "/sys/class/drm";

/// The connector's EDID: /sys/class/drm/card<N>-<output>/edid. Test hook MIOS_FRAMERATE_TEST_EDID =
/// "NAME=FILE;NAME2=FILE2" supplies one for an output WITHOUT a DRM connector only (a nested or virtual output); a real
/// panel always reports its own.
fn output_edid(name: &str) -> Option<plan::Edid> {
    let suffix = format!("-{name}");
    let real = std::fs::read_dir(DRM_SYSFS).ok().and_then(|d| {
        d.flatten()
            .find(|e| e.file_name().to_str().is_some_and(|n| n.starts_with("card") && n.ends_with(&suffix)))
            .map(|e| e.path().join("edid"))
    });
    if let Some(path) = real {
        return plan::Edid::parse(&std::fs::read(path).ok()?);
    }
    let spec = std::env::var("MIOS_FRAMERATE_TEST_EDID").ok()?;
    let file = spec.split(';').filter_map(|e| e.split_once('=')).find(|(n, _)| n.trim() == name).map(|(_, f)| f.trim().to_owned())?;
    plan::Edid::parse(&read_edid_file(Path::new(&file)).ok()?)
}

/// An EDID file: the binary blob (sysfs), or hex bytes with `#` comment lines (tests/fixtures/framerate).
fn read_edid_file(path: &Path) -> Result<Vec<u8>, String> {
    let raw = std::fs::read(path).map_err(|e| format!("{}: {e}", path.display()))?;
    if raw.starts_with(&[0, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0]) {
        return Ok(raw);
    }
    let text = String::from_utf8(raw).map_err(|_| format!("{}: neither an EDID blob nor hex text", path.display()))?;
    text.lines()
        .filter(|l| !l.trim_start().starts_with('#'))
        .flat_map(str::split_whitespace)
        .map(|b| u8::from_str_radix(b, 16).map_err(|_| format!("{}: bad hex byte {b:?}", path.display())))
        .collect()
}

/// An option exists (its plugin is loaded) and its int value.
fn option_int(h: &Hypr, name: &str) -> Option<i64> {
    let v = h.json(&format!("getoption {name}")).ok()?;
    v.get("int").and_then(Json::as_i64)
}

/// The loaded plugins (`hyprctl plugin list`, read-only: status and --dry-run never run Lua in the compositor).
fn consumers(h: &Hypr) -> Consumers {
    let names: Vec<String> = h
        .json("plugin list")
        .ok()
        .and_then(|v| v.as_array().cloned())
        .into_iter()
        .flatten()
        .filter_map(|p| p.get("name").and_then(Json::as_str).map(str::to_owned))
        .collect();
    let has = |n: &str| names.iter().any(|x| x == n);
    let pacing = has("mios-pacing") && option_int(h, "plugin:mios_pacing:max_fps").is_some();
    // The wallpaper throttle needs the option AND the live CWLSurfaceResource::frame hook (the status says so).
    let pacing_wallpaper = pacing
        && option_int(h, "plugin:mios_pacing:wallpaper_fps").is_some()
        && h.json("mios-pacing status").ok().and_then(|v| v.get("wallpaper_hooked").and_then(Json::as_bool)) == Some(true);
    Consumers {
        pacing,
        pacing_wallpaper,
        hyprglass: has("hyprglass") && option_int(h, "plugin:hyprglass:layers:live_resample_fps").is_some(),
        hypr3d: has("hypr3d"),
    }
}

/// Values that drifted from the plan (a config or plugin reload), read back through getoption.
fn drift(h: &Hypr, p: &Plan, c: Consumers) -> Vec<String> {
    let mut out = Vec::new();
    let mut check = |name: &str, want: u32| match option_int(h, name) {
        Some(v) if v == i64::from(want) => {}
        Some(v) => out.push(format!("{name}={v} (want {want})")),
        None => out.push(format!("{name} missing")),
    };
    if c.pacing {
        check("plugin:mios_pacing:max_fps", p.rates.cap_hz);
    }
    if c.pacing_wallpaper {
        check("plugin:mios_pacing:wallpaper_fps", plan::wallpaper_throttle(&p.rates, c));
    }
    if c.hyprglass {
        check("plugin:hyprglass:layers:live_resample_fps", p.rates.glass_fps);
        check("plugin:hyprglass:windows:live_resample_fps", p.rates.glass_fps);
    }
    out
}

// ---------------------------------------------------------------- files

fn runtime_dir() -> PathBuf {
    crate::placement::runtime_dir()
}

fn state_home() -> PathBuf {
    std::env::var_os("XDG_STATE_HOME")
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .unwrap_or_else(|| PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".local/state"))
}

fn bad_modes_path() -> PathBuf {
    state_home().join("mios/framerate/bad-modes.json")
}

/// Creates the missing directories of `dir` as 0755 (set explicitly: the session umask may be 077), leaving the
/// modes of directories that already exist alone.
fn mkdirs(dir: &Path) -> Result<(), String> {
    use std::os::unix::fs::PermissionsExt;
    let mut missing = Vec::new();
    let mut cur = Some(dir);
    while let Some(d) = cur {
        if d.is_dir() {
            break;
        }
        missing.push(d.to_path_buf());
        cur = d.parent();
    }
    for d in missing.iter().rev() {
        match std::fs::create_dir(d) {
            Ok(()) => std::fs::set_permissions(d, std::fs::Permissions::from_mode(0o755)).map_err(|e| format!("{}: {e}", d.display()))?,
            Err(e) if e.kind() == std::io::ErrorKind::AlreadyExists => {}
            Err(e) => return Err(format!("{}: {e}", d.display())),
        }
    }
    Ok(())
}

/// Temp file beside `path`, mode `mode`, fsync, rename, fsync the directory; the written size is checked.
pub fn write_atomic(path: &Path, body: &[u8], mode: u32) -> Result<(), String> {
    use std::os::unix::fs::OpenOptionsExt;
    let dir = path.parent().ok_or("no parent directory")?;
    mkdirs(dir)?;
    let tmp = dir.join(format!(".{}.{}.tmp", path.file_name().and_then(|n| n.to_str()).unwrap_or("f"), std::process::id()));
    let res = (|| -> std::io::Result<()> {
        let mut f = std::fs::OpenOptions::new().write(true).create(true).truncate(true).mode(mode).open(&tmp)?;
        f.write_all(body)?;
        f.sync_all()?;
        std::fs::set_permissions(&tmp, std::os::unix::fs::PermissionsExt::from_mode(mode))?;
        if std::fs::metadata(&tmp)?.len() != body.len() as u64 {
            return Err(std::io::Error::other("short write"));
        }
        std::fs::rename(&tmp, path)?;
        std::fs::File::open(dir)?.sync_all()
    })();
    if res.is_err() {
        let _ = std::fs::remove_file(&tmp);
    }
    res.map_err(|e| format!("{}: {e}", path.display()))
}

/// The operator's answers to automatic modesets ($XDG_STATE_HOME/mios/framerate/mode-confirm.json).
#[derive(Clone, Debug, Default, PartialEq, serde::Serialize, serde::Deserialize)]
pub struct ModeConfirm {
    /// panel identity -> modes the operator kept (no confirmation needed again)
    #[serde(default)]
    pub confirmed: ModeSets,
    /// panel identities whose last automatic modeset was not confirmed in time (no automatic modesets there)
    #[serde(default)]
    pub declined: BTreeSet<String>,
}

fn mode_confirm_path() -> PathBuf {
    state_home().join("mios/framerate/mode-confirm.json")
}

fn read_confirm() -> ModeConfirm {
    std::fs::read_to_string(mode_confirm_path()).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_default()
}

fn write_confirm(c: &ModeConfirm) {
    let body = serde_json::to_vec_pretty(c).unwrap_or_default();
    if let Err(e) = write_atomic(&mode_confirm_path(), &body, 0o644) {
        log(&format!("mode-confirm: {e}"));
    }
}

/// $XDG_RUNTIME_DIR/mios/framerate/revert.json: the modeset in flight or waiting for confirmation (output, the mode,
/// the previous mode and the Lua that restores it, the deadline) -- for `status`, apply-live.sh and a crash.
fn pending_path() -> PathBuf {
    runtime_dir().join("mios/framerate/revert.json")
}

fn read_bad() -> BTreeMap<String, BTreeSet<String>> {
    std::fs::read_to_string(bad_modes_path()).ok().and_then(|s| serde_json::from_str(&s).ok()).unwrap_or_default()
}

fn write_bad(bad: &BTreeMap<String, BTreeSet<String>>) {
    let body = serde_json::to_vec_pretty(bad).unwrap_or_default();
    if let Err(e) = write_atomic(&bad_modes_path(), &body, 0o644) {
        log(&format!("bad-modes: {e}"));
    }
}

/// The user-tier fragment `set-unlocked` writes: only [desktop.framerate].unlocked.
pub fn unlocked_fragment(on: bool) -> String {
    format!(
        "# AI-hint: Written by `miosd framerate set-unlocked` (user tier): the [desktop.framerate] unlocked opt-in. Delete this file to follow the vendor/host value again.\n[desktop.framerate]\nunlocked = {on}\n"
    )
}

pub fn write_unlocked(dir: &Path, fragment: &str, on: bool) -> Result<PathBuf, String> {
    let path = dir.join(fragment);
    let body = unlocked_fragment(on);
    write_atomic(&path, body.as_bytes(), 0o644)?;
    let back = std::fs::read_to_string(&path).map_err(|e| e.to_string())?;
    let parsed: Value = back.parse().map_err(|e| format!("{}: {e}", path.display()))?;
    if back.len() != body.len() || parsed["desktop"]["framerate"]["unlocked"].as_bool() != Some(on) {
        return Err(format!("{}: read-back mismatch", path.display()));
    }
    Ok(path)
}

/// `confirm-mode`: SIGUSR1 to the running daemon (the pid in its live state file) keeps the pending mode;
/// `--reset` forgets the confirmed and declined panels and makes the daemon re-read them (SIGHUP).
fn confirm_mode(reset: bool) -> Result<(), String> {
    let cfg = Config::load()?.ok_or("[desktop.framerate] is absent or disabled")?;
    let daemon = State::read_live(&cfg.policy.state_path(&runtime_dir()), Path::new("/")).map(|s| s.pid);
    if reset {
        let path = mode_confirm_path();
        match std::fs::remove_file(&path) {
            Ok(()) => println!("forgot the confirmed and declined modes ({})", path.display()),
            Err(e) if e.kind() == std::io::ErrorKind::NotFound => println!("nothing to forget ({})", path.display()),
            Err(e) => return Err(format!("{}: {e}", path.display())),
        }
        if let Some(pid) = daemon {
            if unsafe { libc::kill(pid as libc::pid_t, libc::SIGHUP) } != 0 {
                return Err(format!("could not signal the daemon (pid {pid})"));
            }
            println!("mios-framerate (pid {pid}) re-reads it");
        }
        return Ok(());
    }
    let pending = std::fs::read_to_string(pending_path()).ok().and_then(|s| serde_json::from_str::<Json>(&s).ok());
    let Some(p) = pending.filter(|p| p.get("deadline_unix").is_some()) else {
        return Err("no display mode is waiting for confirmation".into());
    };
    let pid = daemon.ok_or("mios-framerate is not running")?;
    if unsafe { libc::kill(pid as libc::pid_t, libc::SIGUSR1) } != 0 {
        return Err(format!("could not signal the daemon (pid {pid})"));
    }
    println!(
        "keeping {} on {} (mios-framerate pid {pid})",
        p.get("mode").and_then(Json::as_str).unwrap_or("?"),
        p.get("output").and_then(Json::as_str).unwrap_or("?")
    );
    Ok(())
}

fn set_unlocked(state: &str, no_reload: bool) -> Result<(), String> {
    let doc = mios_resolver::resolve_merged(Some(&mios_root()), false).map_err(|e| e.to_string())?;
    let policy = Policy::from_ssot(&doc)?.ok_or("[desktop.framerate] is not in the SSOT")?;
    let on = match state {
        "on" => true,
        "off" => false,
        _ => !policy.unlocked,
    };
    let user_d = mios_resolver::layers::resolve_tier_dirs(None).5;
    let path = write_unlocked(&user_d, &policy.user_fragment, on)?;
    println!("[desktop.framerate].unlocked = {on} ({})", path.display());
    if !no_reload {
        let active = std::process::Command::new("systemctl")
            .args(["--user", "is-active", "--quiet", "mios-framerate.service"])
            .status()
            .is_ok_and(|s| s.success());
        if active {
            let ok = std::process::Command::new("systemctl")
                .args(["--user", "kill", "-s", "HUP", "mios-framerate.service"])
                .status()
                .is_ok_and(|s| s.success());
            println!("{}", if ok { "mios-framerate.service reloads it" } else { "could not signal mios-framerate.service" });
        } else {
            println!("mios-framerate.service is not running: the next start (or login) uses it");
        }
    }
    Ok(())
}

// ---------------------------------------------------------------- the daemon

enum Ev {
    Hypr(String),
    HyprLost,
    Dbus(&'static str),
    Hup,
    /// SIGUSR1 (`miosd framerate confirm-mode`): keep the pending display mode
    Confirm,
    Term,
}

type Tx = tokio::sync::mpsc::UnboundedSender<Ev>;

fn spawn_hypr_events(tx: Tx) {
    std::thread::spawn(move || loop {
        if let Ok(s) = Hypr::connect().and_then(|h| h.events()) {
            for line in BufReader::new(s).lines() {
                let Ok(l) = line else { break };
                if tx.send(Ev::Hypr(l)).is_err() {
                    return;
                }
            }
        }
        if tx.send(Ev::HyprLost).is_err() {
            return;
        }
        std::thread::sleep(Duration::from_secs(1));
    });
}

/// PropertiesChanged of one service object -> Ev::Dbus(label). A service that is absent now is picked up by the
/// periodic self-check; the match rule follows the well-known name when it (re)appears.
fn watch_properties(conn: Connection, (dest, path, _): (&'static str, &'static str, &'static str), label: &'static str, tx: Tx) {
    tokio::spawn(async move {
        let Ok(b) = zbus::fdo::PropertiesProxy::builder(&conn).destination(dest) else { return };
        let Ok(b) = b.path(path) else { return };
        let Ok(proxy) = b.build().await else { return };
        let Ok(mut stream) = proxy.receive_properties_changed().await else { return };
        while stream.next().await.is_some() {
            if tx.send(Ev::Dbus(label)).is_err() {
                return;
            }
        }
    });
}

fn watch_tuned(conn: Connection, tx: Tx) {
    tokio::spawn(async move {
        let rule = zbus::MatchRule::builder()
            .msg_type(zbus::message::Type::Signal)
            .interface(TUNED.2)
            .and_then(|b| b.member("profile_changed"))
            .map(|b| b.build());
        let Ok(rule) = rule else { return };
        let Ok(mut stream) = zbus::MessageStream::for_match_rule(rule, &conn, Some(16)).await else { return };
        while stream.next().await.is_some() {
            if tx.send(Ev::Dbus("tuned")).is_err() {
                return;
            }
        }
    });
}

fn spawn_signals(tx: Tx) {
    use tokio::signal::unix::{signal, SignalKind};
    tokio::spawn(async move {
        let (Ok(mut hup), Ok(mut term), Ok(mut int), Ok(mut usr1)) =
            (signal(SignalKind::hangup()), signal(SignalKind::terminate()), signal(SignalKind::interrupt()), signal(SignalKind::user_defined1()))
        else {
            return;
        };
        loop {
            let ev = tokio::select! {
                _ = hup.recv() => Ev::Hup,
                _ = usr1.recv() => Ev::Confirm,
                _ = term.recv() => Ev::Term,
                _ = int.recv() => Ev::Term,
            };
            if tx.send(ev).is_err() {
                return;
            }
        }
    });
}

/// Hyprland events that can change the plan or undo an apply.
fn relevant(line: &str, fullscreen_game: bool) -> Option<bool> {
    let ev = line.split_once(">>").map_or(line, |(e, _)| e);
    match ev {
        // re-apply even when the plan is unchanged: a reload re-ran the Lua, a new output starts uncapped
        "configreloaded" | "monitoradded" | "monitoraddedv2" | "monitorremoved" | "monitorremovedv2" => Some(true),
        "fullscreen" | "activewindowv2" if fullscreen_game => Some(false),
        _ => None,
    }
}

/// Log suffix for a way back that is not the shown mode (plan::restorable_mode case 4).
fn not_shown(m: &Monitor, r: &plan::Restore) -> String {
    if r.exact {
        String::new()
    } else {
        format!(
            " (the configured rule's mode: it shows {}, a mode it does not advertise and the frame-rate daemon did not set)",
            plan::mode_string(m.width, m.height, m.refresh)
        )
    }
}

struct Server {
    cfg: Config,
    sys: Option<Connection>,
    ses: Option<Connection>,
    hypr: Option<Hypr>,
    gaming: GamingHold,
    gate: ModesetGate,
    /// the mode an output showed before the daemon first switched it (restored when no mode is wanted any more), as
    /// plan::restorable_mode names it: never a "WxH@Hz" of a mode the output does not advertise
    baseline: BTreeMap<String, plan::Restore>,
    /// the exact mode string (advertised or the daemon's own modeline) the daemon last put on each output: the way back
    /// to it is that string, not the WxH@Hz hyprctl reports for it
    set: BTreeMap<String, String>,
    bad: BTreeMap<String, BTreeSet<String>>,
    confirm: ModeConfirm,
    /// a verified modeset waiting for the operator (restored at its deadline)
    pending: Option<PendingMode>,
    applied: Option<(Plan, Consumers)>,
    seq: u64,
    wake: Option<Instant>,
}

impl Server {
    fn state_path(&self) -> PathBuf {
        self.cfg.policy.state_path(&runtime_dir())
    }

    fn connect_hypr(&mut self) {
        match Hypr::connect() {
            Ok(h) => {
                if self.hypr.as_ref().map(|o| o.dir() != h.dir()).unwrap_or(true) {
                    log(&format!("compositor {}", h.dir().display()));
                    self.applied = None;
                }
                self.hypr = Some(h);
            }
            Err(e) => {
                if self.hypr.take().is_some() {
                    log(&format!("compositor gone: {e}"));
                }
            }
        }
    }

    fn env(&self, c: Consumers) -> Env {
        Env {
            pacing: c.pacing,
            require_vrr: self.cfg.require_vrr,
            pinned: self.cfg.pinned.clone(),
            bad: self.bad.clone(),
            declined: self.confirm.declined.clone(),
        }
    }

    /// Read every input, plan, and apply what changed (or everything when `force`).
    async fn evaluate(&mut self, force: bool) {
        self.wake = None;
        if self.hypr.is_none() {
            self.connect_hypr();
        }
        let (mut inputs, src) = read_inputs(self.sys.as_ref(), self.ses.as_ref(), &self.cfg, self.hypr.as_ref()).await;
        let now = Instant::now();
        let (held, until) = self.gaming.update(inputs.gaming, now, Duration::from_millis(self.cfg.policy.gaming_exit_hold_ms));
        inputs.gaming = held;
        self.wake = until;
        let Some(h) = self.hypr.as_ref() else {
            // No compositor: publish the tier anyway (Host.qml, the next login), nothing to apply (and nothing paces
            // the wallpaper: it keeps its own timer).
            let p = plan::plan(&self.cfg.policy, &inputs, &[], &Env::default());
            self.publish(&p, &inputs, Consumers::default());
            return;
        };
        let mons = match monitors(h) {
            Ok(m) => m,
            Err(e) => {
                log(&format!("monitors: {e}"));
                self.hypr = None;
                return;
            }
        };
        let c = consumers(h);
        let p = plan::plan(&self.cfg.policy, &inputs, &mons, &self.env(c));
        let changed = self.applied.as_ref().map(|(ap, ac)| ap != &p || *ac != c).unwrap_or(true);
        let drifted = if !changed && !force { drift(h, &p, c) } else { Vec::new() };
        if changed || force || !drifted.is_empty() {
            let lua = plan::rates_lua(&p, &self.cfg.policy, c);
            match h.eval(&lua) {
                Ok(()) => {
                    log(&format!(
                        "tier {} (profile {:?} via {}, battery {} via {}, gaming {} via {}): D={} cap={} G={} W={}{}",
                        p.rates.tier,
                        inputs.profile,
                        src.profile,
                        inputs.on_battery,
                        src.battery,
                        inputs.gaming,
                        src.gaming,
                        p.rates.desktop_hz,
                        p.rates.cap_hz,
                        p.rates.glass_fps,
                        p.rates.wallpaper_fps,
                        if drifted.is_empty() { String::new() } else { format!(" (re-applied: {})", drifted.join(", ")) }
                    ));
                    let left = drift(h, &p, c);
                    if !left.is_empty() {
                        log(&format!("not applied: {}", left.join(", ")));
                    }
                    self.applied = Some((p.clone(), c));
                }
                Err(e) => {
                    log(&format!("eval: {e}"));
                    self.applied = None;
                }
            }
        }
        self.modes(&mons, &p).await;
        // The wallpaper is announced as paced only once the throttle is applied (else Host.qml would step per frame
        // at the full desktop rate): an eval that failed leaves `applied` empty.
        let applied = self.applied.as_ref().map(|(_, ac)| *ac).unwrap_or_default();
        self.publish(&p, &inputs, applied);
    }

    /// Advertised-mode fallback for outputs without the cap, with hysteresis, verification, revert and blocklist.
    async fn modes(&mut self, mons: &[Monitor], p: &Plan) {
        let pol = self.cfg.policy.clone();
        let hold = Duration::from_millis(pol.modeset_hold_ms);
        let gap = Duration::from_secs(pol.modeset_min_interval_s);
        for m in mons.iter().filter(|m| m.local(&pol)) {
            // an output whose last switch waits for the operator is left alone until the answer (or the deadline)
            if self.pending.as_ref().is_some_and(|p| p.output == m.name) {
                continue;
            }
            let here = plan::restorable_mode(m, self.set.get(&m.name).map(String::as_str), &self.cfg.rule(&m.name));
            let want = match p.outputs.get(&m.name).map(|o| &o.mech) {
                Some(Mech::Mode(mode)) => {
                    if !self.baseline.contains_key(&m.name) {
                        log(&format!("{}: baseline {}{}", m.name, here.mode, not_shown(m, &here)));
                        self.baseline.insert(m.name.clone(), here.clone());
                    }
                    mode.clone()
                }
                // no mode wanted any more: back to what it showed before the first switch
                _ => match self.baseline.get(&m.name) {
                    Some(b) if plan::mode_dims(&b.mode).is_some() => b.mode.clone(),
                    // a named rule mode (no advertised mode at that size): left as found until the daemon stops
                    Some(_) => continue,
                    None => continue,
                },
            };
            let shows = plan::verified(Some(m), &want);
            let (now_go, wake) = self.gate.decide(&m.name, &want, shows, Instant::now(), hold, gap);
            if let Some(w) = wake {
                self.wake = Some(self.wake.map_or(w, |x| x.min(w)));
            }
            if shows && !matches!(p.outputs.get(&m.name).map(|o| &o.mech), Some(Mech::Mode(_))) {
                self.baseline.remove(&m.name);
            }
            if !now_go {
                continue;
            }
            self.switch(m, &here, &want, pol.revert_timeout_ms, pol.mode_confirm_s).await;
        }
    }

    async fn switch(&mut self, m: &Monitor, here: &plan::Restore, want: &str, timeout_ms: u64, confirm_s: u64) {
        let Some(h) = self.hypr.as_ref() else { return };
        let current = here.mode.as_str();
        let snapshot = plan::monitor_lua(m, current);
        let revert = pending_path();
        let _ = write_atomic(&revert, serde_json::json!({"output": m.name, "mode": want, "previous": current, "lua": snapshot}).to_string().as_bytes(), 0o644);
        log(&format!("{}: mode {current}{} -> {want} (a modeset: no active VRR cap, or a mode output)", m.name, not_shown(m, here)));
        let started = Instant::now();
        let mut ok = h.eval(&plan::monitor_lua(m, want)).is_ok();
        if ok {
            ok = false;
            while started.elapsed() < Duration::from_millis(timeout_ms) {
                tokio::time::sleep(Duration::from_millis(250)).await;
                let now = monitors(h).unwrap_or_default();
                if plan::verified(now.iter().find(|x| x.name == m.name), want) {
                    ok = true;
                    break;
                }
            }
        }
        if ok {
            log(&format!("{}: {want} verified after {} ms", m.name, started.elapsed().as_millis()));
            self.set.insert(m.name.clone(), want.to_owned());
            // going back to the baseline needs no answer only when the baseline is what the output really showed
            let baseline = self.baseline.get(&m.name).filter(|b| b.exact).map(|b| b.mode.clone());
            if plan::needs_confirm(confirm_s, &m.identity, want, baseline.as_deref(), &self.confirm.confirmed) {
                // A display-mode change the operator has not seen on this panel: keep the way back armed. Unless
                // `miosd framerate confirm-mode` answers within confirm_s, the previous mode comes back.
                let deadline = Instant::now() + Duration::from_secs(confirm_s);
                let unix = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0) + confirm_s;
                let body = serde_json::json!({"output": m.name, "mode": want, "previous": current, "lua": snapshot,
                    "deadline_unix": unix, "confirm": "miosd framerate confirm-mode"});
                let _ = write_atomic(&revert, body.to_string().as_bytes(), 0o644);
                let msg = format!("Display {} is now {want} (frame-rate tier). Keep it: run `miosd framerate confirm-mode` within {confirm_s} s; otherwise {current} comes back.", m.name);
                let _ = h.notify(0, (confirm_s * 1000) as u32, &msg);
                log(&format!("{}: {want} waits for confirmation ({confirm_s} s; else back to {current})", m.name));
                self.pending = Some(PendingMode {
                    output: m.name.clone(),
                    identity: m.identity.clone(),
                    mode: want.to_owned(),
                    previous: current.to_owned(),
                    restore_lua: snapshot,
                    deadline,
                });
                return;
            }
        } else {
            log(&format!("{}: {want} not verified within {timeout_ms} ms: reverting to {current}, blocklisting {want}", m.name));
            let _ = h.eval(&snapshot);
            self.set.insert(m.name.clone(), current.to_owned());
            self.bad.entry(m.identity.clone()).or_default().insert(want.to_owned());
            write_bad(&self.bad);
            self.gate.forget(&m.name);
        }
        let _ = std::fs::remove_file(&revert);
    }

    /// `confirm-mode` (SIGUSR1): the operator keeps the pending mode; later switches to it on this panel need no answer.
    fn confirm_pending(&mut self) {
        let Some(p) = self.pending.take() else {
            log("confirm-mode: no display mode is waiting for confirmation");
            return;
        };
        self.confirm.confirmed.entry(p.identity.clone()).or_default().insert(p.mode.clone());
        write_confirm(&self.confirm);
        let _ = std::fs::remove_file(pending_path());
        if let Some(h) = self.hypr.as_ref() {
            let _ = h.notify(5, 4000, &format!("Display {} keeps {}.", p.output, p.mode));
        }
        log(&format!("{}: {} confirmed by the operator", p.output, p.mode));
    }

    /// The deadline passed without an answer: restore the previous mode, and make no more automatic modesets on that
    /// panel (else the plan would try the next-nearest mode and blank the screen again) until confirm-mode --reset.
    fn expire_pending(&mut self) {
        let Some(p) = self.pending.take() else { return };
        log(&format!("{}: {} not confirmed within the deadline: restoring {}; no more automatic modesets on this panel", p.output, p.mode, p.previous));
        if let Some(h) = self.hypr.as_ref() {
            if let Err(e) = h.eval(&p.restore_lua) {
                log(&format!("{}: restore {}: {e}", p.output, p.previous));
            }
            let _ = h.notify(0, 6000, &format!("Display {} is back to {} (not confirmed).", p.output, p.previous));
        }
        self.set.insert(p.output.clone(), p.previous.clone());
        self.confirm.declined.insert(p.identity.clone());
        write_confirm(&self.confirm);
        self.gate.forget(&p.output);
        let _ = std::fs::remove_file(pending_path());
    }

    /// Stopping (or the policy disabled): every display mode the daemon changed goes back -- a modeset still waiting
    /// for its answer first, then each output's baseline (the mode it showed before the first switch).
    fn restore_modes(&mut self) {
        let Some(h) = self.hypr.as_ref() else { return };
        if let Some(p) = self.pending.take() {
            log(&format!("{}: stopping before {} was confirmed: restoring {}", p.output, p.mode, p.previous));
            let _ = h.eval(&p.restore_lua);
            let _ = std::fs::remove_file(pending_path());
            // the baseline below may still differ from `previous`
            std::thread::sleep(Duration::from_millis(300));
        }
        let mons = monitors(h).unwrap_or_default();
        self.set.clear();
        for (name, base) in std::mem::take(&mut self.baseline) {
            let base = base.mode;
            let Some(m) = mons.iter().find(|m| m.name == name) else { continue };
            if plan::verified(Some(m), &base) {
                continue;
            }
            log(&format!("{name}: stopping: restoring the mode it had before the frame-rate policy, {base}"));
            if let Err(e) = h.eval(&plan::monitor_lua(m, &base)) {
                log(&format!("{name}: restore {base}: {e}"));
            }
        }
    }

    /// Stopping: the wallpaper goes back to its own timer (the state file goes away), so the compositor must stop
    /// holding its frame callbacks too; the other rates stay until the next config reload.
    fn release_wallpaper(&self) {
        if let (Some(h), Some((_, c))) = (self.hypr.as_ref(), self.applied.as_ref()) {
            if c.pacing_wallpaper {
                let _ = h.eval("(function() pcall(hl.config, {plugin = {mios_pacing = {wallpaper_fps = 0}}}) end)()");
            }
        }
    }

    fn publish(&mut self, p: &Plan, inputs: &Inputs, c: Consumers) {
        self.seq += 1;
        let s = plan::state(p, &self.cfg.policy, inputs, c, std::process::id(), self.seq);
        if let Err(e) = write_atomic(&self.state_path(), s.to_json().as_bytes(), 0o644) {
            log(&format!("state: {e}"));
        }
    }
}

async fn serve() -> Result<(), String> {
    let (tx, mut rx) = tokio::sync::mpsc::unbounded_channel::<Ev>();
    spawn_signals(tx.clone());
    let cfg = loop {
        match Config::load()? {
            Some(c) => break c,
            None => {
                log("[desktop.framerate] is absent or disabled: idle until SIGHUP");
                match rx.recv().await {
                    Some(Ev::Hup) => continue,
                    _ => return Ok(()),
                }
            }
        }
    };
    let sys = Connection::system().await.map_err(|e| log(&format!("system bus: {e}"))).ok();
    let ses = Connection::session().await.map_err(|e| log(&format!("session bus: {e}"))).ok();
    if let Some(c) = &sys {
        watch_properties(c.clone(), PPD[0], "power-profile", tx.clone());
        watch_properties(c.clone(), PPD[1], "power-profile", tx.clone());
        watch_properties(c.clone(), UPOWER, "battery", tx.clone());
        watch_tuned(c.clone(), tx.clone());
    }
    if let Some(c) = &ses {
        watch_properties(c.clone(), GAMEMODE, "gamemode", tx.clone());
    }
    spawn_hypr_events(tx.clone());
    let mut srv = Server {
        bad: read_bad(),
        confirm: read_confirm(),
        pending: None,
        cfg,
        sys,
        ses,
        hypr: None,
        gaming: GamingHold::default(),
        gate: ModesetGate::default(),
        baseline: BTreeMap::new(),
        set: BTreeMap::new(),
        applied: None,
        seq: 0,
        wake: None,
    };
    srv.connect_hypr();
    log(&format!("serving (state {})", srv.state_path().display()));
    srv.evaluate(true).await;
    let mut dirty: Option<Instant> = None;
    let mut force = false;
    let mut check = Instant::now() + Duration::from_secs(srv.cfg.policy.self_check_s);
    loop {
        let next = [dirty, srv.wake, srv.pending.as_ref().map(|p| p.deadline), Some(check)].into_iter().flatten().min().unwrap_or(check);
        let wait = next.saturating_duration_since(Instant::now());
        let debounce = Duration::from_millis(srv.cfg.policy.debounce_ms);
        match tokio::time::timeout(wait, rx.recv()).await {
            Ok(Some(Ev::Hypr(line))) => {
                if let Some(f) = relevant(&line, srv.cfg.policy.gaming.fullscreen_game) {
                    force |= f;
                    dirty.get_or_insert(Instant::now() + debounce);
                }
            }
            Ok(Some(Ev::HyprLost)) => {
                srv.hypr = None;
                force = true;
                dirty.get_or_insert(Instant::now() + debounce);
            }
            Ok(Some(Ev::Dbus(what))) => {
                let _ = what;
                dirty.get_or_insert(Instant::now() + debounce);
            }
            Ok(Some(Ev::Hup)) => match Config::load() {
                Ok(Some(c)) => {
                    log("SSOT reloaded");
                    srv.cfg = c;
                    srv.confirm = read_confirm();
                    force = true;
                    dirty = Some(Instant::now());
                }
                Ok(None) => {
                    log("[desktop.framerate] disabled: stopping (the last rates stay until the next config reload)");
                    let _ = std::fs::remove_file(srv.state_path());
                    srv.release_wallpaper();
                    srv.restore_modes();
                    return Ok(());
                }
                Err(e) => log(&format!("SSOT reload refused, keeping the running policy: {e}")),
            },
            Ok(Some(Ev::Confirm)) => srv.confirm_pending(),
            Ok(Some(Ev::Term)) | Ok(None) => {
                let _ = std::fs::remove_file(srv.state_path());
                srv.release_wallpaper();
                srv.restore_modes();
                log("stopped");
                return Ok(());
            }
            Err(_) => {}
        }
        let now = Instant::now();
        if srv.pending.as_ref().is_some_and(|p| p.due(now)) {
            srv.expire_pending();
            dirty.get_or_insert(now);
        }
        if now >= check {
            check = now + Duration::from_secs(srv.cfg.policy.self_check_s);
            if srv.hypr.is_none() {
                srv.connect_hypr();
                force = true;
            }
            dirty.get_or_insert(now);
        }
        if srv.wake.is_some_and(|w| now >= w) {
            dirty.get_or_insert(now);
        }
        if dirty.is_some_and(|d| now >= d) {
            dirty = None;
            if force {
                srv.connect_hypr();
            }
            srv.evaluate(std::mem::take(&mut force)).await;
        }
    }
}

// ---------------------------------------------------------------- CLI: status, apply

async fn current_plan(cfg: &Config) -> (Inputs, Sources, Plan, Option<Hypr>, Consumers, Vec<Monitor>) {
    let sys = Connection::system().await.ok();
    let ses = Connection::session().await.ok();
    let hypr = Hypr::connect().ok();
    let (inputs, src) = read_inputs(sys.as_ref(), ses.as_ref(), cfg, hypr.as_ref()).await;
    let (mons, c) = match &hypr {
        Some(h) => (monitors(h).unwrap_or_default(), consumers(h)),
        None => (Vec::new(), Consumers::default()),
    };
    let env = Env { pacing: c.pacing, require_vrr: cfg.require_vrr, pinned: cfg.pinned.clone(), bad: read_bad(), declined: read_confirm().declined };
    let p = plan::plan(&cfg.policy, &inputs, &mons, &env);
    (inputs, src, p, hypr, c, mons)
}

fn print_state(s: &State, header: &str) {
    println!("{header}");
    println!("tier {}  desktop {} Hz (cap {})  glass {} fps  wallpaper {} fps  (max {} Hz)", s.tier, s.desktop_hz, if s.cap_hz == 0 { "none".to_string() } else { s.cap_hz.to_string() }, s.glass_fps, s.wallpaper_fps, s.max_hz);
    println!("inputs: profile {:?}, on battery {}, gaming {}, unlocked {}; wallpaper paced by the compositor {}", s.inputs.profile, s.inputs.on_battery, s.inputs.gaming, s.unlocked, s.wallpaper_paced);
    for (name, o) in &s.outputs {
        println!("  {name}: {} cap {} max {} Hz now {:.3} Hz{}", o.mechanism, o.cap_hz, o.max_hz, o.refresh_hz, o.mode.as_ref().map(|m| format!(" mode {m}")).unwrap_or_default());
    }
    if let Some(p) = std::fs::read_to_string(pending_path()).ok().and_then(|t| serde_json::from_str::<Json>(&t).ok()) {
        if let Some(unix) = p.get("deadline_unix").and_then(Json::as_u64) {
            let now = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
            println!(
                "  waiting for confirmation: {} {} ({} s left; `miosd framerate confirm-mode` keeps it, else {} comes back)",
                p.get("output").and_then(Json::as_str).unwrap_or("?"),
                p.get("mode").and_then(Json::as_str).unwrap_or("?"),
                unix.saturating_sub(now),
                p.get("previous").and_then(Json::as_str).unwrap_or("?")
            );
        }
    }
    let c = read_confirm();
    if !c.declined.is_empty() {
        println!("  no automatic modesets (not confirmed): {} (`miosd framerate confirm-mode --reset` re-enables)", c.declined.iter().cloned().collect::<Vec<_>>().join(", "));
    }
}

async fn status(json: bool) -> Result<(), String> {
    let cfg = Config::load()?.ok_or("[desktop.framerate] is absent or disabled")?;
    let path = cfg.policy.state_path(&runtime_dir());
    if let Some(s) = State::read_live(&path, Path::new("/")) {
        if json {
            print!("{}", s.to_json());
        } else {
            print_state(&s, &format!("mios-framerate (pid {}, apply #{}, {}):", s.pid, s.seq, path.display()));
        }
        return Ok(());
    }
    let (inputs, src, p, _, c, _) = current_plan(&cfg).await;
    let s = plan::state(&p, &cfg.policy, &inputs, c, 0, 0);
    if json {
        print!("{}", s.to_json());
    } else {
        print_state(&s, "mios-framerate is not running; the plan for the current inputs:");
        println!("sources: profile {}, battery {}, gaming {}", src.profile, src.battery, src.gaming);
    }
    Ok(())
}

async fn apply_once(dry_run: bool) -> Result<(), String> {
    let cfg = Config::load()?.ok_or("[desktop.framerate] is absent or disabled")?;
    let (inputs, src, p, hypr, c, mons) = current_plan(&cfg).await;
    let s = plan::state(&p, &cfg.policy, &inputs, c, std::process::id(), 0);
    let lua = plan::rates_lua(&p, &cfg.policy, c);
    let modes: Vec<String> = mons
        .iter()
        .filter_map(|m| match p.outputs.get(&m.name).map(|o| &o.mech) {
            Some(Mech::Mode(mode)) if !plan::verified(Some(m), mode) => Some(plan::monitor_lua(m, mode)),
            _ => None,
        })
        .collect();
    if dry_run {
        print_state(&s, "plan (dry run, nothing applied):");
        println!("sources: profile {}, battery {}, gaming {}", src.profile, src.battery, src.gaming);
        println!("consumers loaded: pacing {} (wallpaper hook {}), hyprglass {}, hypr3d {}", c.pacing, c.pacing_wallpaper, c.hyprglass, c.hypr3d);
        println!("hyprctl eval:\n{lua}");
        for m in &modes {
            println!("modeset (hysteresis and verification in serve): {m}");
        }
        if hypr.is_none() {
            println!("(no Hyprland instance: outputs unknown)");
        }
        return Ok(());
    }
    let h = hypr.ok_or("no live Hyprland instance")?;
    h.eval(&lua)?;
    let left = drift(&h, &p, c);
    print_state(&s, "applied:");
    if !left.is_empty() {
        return Err(format!("not applied: {}", left.join(", ")));
    }
    if !modes.is_empty() {
        println!("outputs without the VRR cap want a modeset; `serve` does that with verification and revert:");
        for m in &modes {
            println!("  {m}");
        }
    }
    Ok(())
}

// ---------------------------------------------------------------- CLI: modeline (read-only)

async fn modeline(output: Option<&str>, hz: Option<f64>, edid: Option<&Path>, size: Option<&str>, json: bool) -> Result<(), String> {
    let cfg = Config::load()?;
    let tolerance = cfg.as_ref().map(|c| c.policy.mode_tolerance_hz).unwrap_or(0.5);
    let (m, target) = if let Some(path) = edid {
        let e = plan::Edid::parse(&read_edid_file(path)?).ok_or_else(|| format!("{}: no detailed timing (not an EDID?)", path.display()))?;
        let (w, h) = match size {
            Some(s) => {
                let (w, h) = s.split_once('x').ok_or("--size must be WxH")?;
                (w.parse().map_err(|_| "--size must be WxH")?, h.parse().map_err(|_| "--size must be WxH")?)
            }
            None => (e.timings[0].hdisplay, e.timings[0].vdisplay),
        };
        let base = mios_resolver::edid_timing::fastest_at(&e.timings, w, h).ok_or_else(|| format!("no EDID timing at {w}x{h}"))?;
        let m = Monitor { name: output.unwrap_or("EDID").to_owned(), width: w, height: h, refresh: base.refresh_hz(), scale: 1.0, dpms: true, edid: Some(e), ..Default::default() };
        (m, hz.ok_or("--edid needs --hz (no compositor, no tier)")?)
    } else {
        let cfg = cfg.as_ref().ok_or("[desktop.framerate] is absent or disabled (or pass --edid FILE --hz N)")?;
        let (_, _, p, hypr, _, mons) = current_plan(&cfg).await;
        hypr.ok_or("no live Hyprland instance (or pass --edid FILE --hz N)")?;
        let m = match output {
            Some(o) => mons.into_iter().find(|m| m.name == o).ok_or_else(|| format!("no output {o}"))?,
            None => mons.into_iter().find(|m| m.local(&cfg.policy) && m.edid.is_some()).ok_or("no local output with an EDID")?,
        };
        let target = hz.unwrap_or(f64::from(p.rates.desktop_hz));
        if target <= 0.0 {
            return Err("the tier has no desktop rate (unlocked runs the output maximum): pass --hz".into());
        }
        (m, target)
    };
    let line = plan::extended_mode(&m, target, tolerance).map_err(|e| format!("{}: no extended-vblank mode for {target} Hz: {e}", m.name))?;
    let t = mios_resolver::edid_timing::Timing::parse_modeline(&line).ok_or("internal: the modeline does not parse")?;
    let e = m.edid.as_ref().expect("extended_mode needs the EDID");
    let base = mios_resolver::edid_timing::fastest_at(&e.timings, m.width, m.height).expect("extended_mode found a base");
    let shows = plan::mode_string(m.width, m.height, m.refresh);
    let rule = cfg.as_ref().map(|c| c.rule(&m.name)).unwrap_or_else(|| "preferred".into());
    let back = plan::restorable_mode(&m, None, &rule);
    let current = back.mode.clone();
    let apply = plan::monitor_lua(&m, &line);
    let restore = plan::monitor_lua(&m, &current);
    if json {
        let v = serde_json::json!({
            "output": m.name, "identity": m.identity, "width": m.width, "height": m.height, "target_hz": target,
            "modeline": line, "refresh_hz": t.refresh_hz(), "line_khz": t.line_khz(), "vtotal": t.vtotal,
            "base_modeline": base.modeline(), "base_hz": base.refresh_hz(), "base_vtotal": base.vtotal,
            "range": e.range.map(|r| serde_json::json!({"min_v_hz": r.min_v_hz, "max_v_hz": r.max_v_hz, "min_h_khz": r.min_h_khz, "max_h_khz": r.max_h_khz, "max_clock_mhz": r.max_clock_mhz})),
            "vrr": m.vrr, "current": current, "shows": shows, "restore_exact": back.exact, "apply_lua": apply, "restore_lua": restore,
        });
        println!("{v}");
    } else {
        println!("{}: base {} ({:.3} Hz, line {:.3} kHz)", m.name, base.modeline(), base.refresh_hz(), base.line_khz());
        println!("{}: {target} Hz extended vblank {line} ({:.4} Hz, {} more blank lines)", m.name, t.refresh_hz(), t.vtotal - base.vtotal);
        println!("apply:   {apply}");
        println!("restore: {restore}");
        if !back.exact {
            println!("note: {} shows {shows}, a mode it does not advertise: the restore is the configured rule's mode, not that one", m.name);
        }
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn config_reads_pins_and_the_pacing_option() {
        let text = std::fs::read_to_string(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../usr/share/mios/mios.toml")).unwrap();
        let mut doc: Value = text.parse().unwrap();
        let c = Config::from_ssot(&doc).unwrap().expect("[desktop.framerate] enabled in the SSOT");
        assert!(c.require_vrr && c.pinned.is_empty());
        let mons: Value = "[DP-3]\nmode = \"1920x1080@144\"\n[HDMI-A-1]\nmode = \"preferred\"\n".parse().unwrap();
        doc["desktop"]["hyprland"].as_table_mut().unwrap().insert("monitors".into(), mons);
        let c = Config::from_ssot(&doc).unwrap().unwrap();
        assert_eq!(c.pinned, ["DP-3".to_string()].into(), "only an explicit WxH@Hz is a pin");
        // the monitor rules the way back falls to (plan::restorable_mode): an output's own, else the catch-all's
        assert_eq!((c.rule("HDMI-A-1").as_str(), c.rule("eDP-1").as_str()), ("preferred", "preferred"));
        doc["desktop"]["hyprland"]["monitor"]["mode"] = Value::String("highrr".into());
        let c = Config::from_ssot(&doc).unwrap().unwrap();
        assert_eq!((c.rule("HDMI-A-1").as_str(), c.rule("eDP-1").as_str()), ("preferred", "highrr"));
        doc["desktop"]["framerate"]["enabled"] = Value::Boolean(false);
        assert!(Config::from_ssot(&doc).unwrap().is_none(), "disabled: no daemon work");
        doc["desktop"]["framerate"]["enabled"] = Value::Boolean(true);
        doc["desktop"]["hyprland"]["mios-pacing"]["config"]["require_vrr"] = Value::String("yes".into());
        assert!(Config::from_ssot(&doc).unwrap_err().contains("require_vrr"));
    }

    #[test]
    fn unlocked_fragment_is_written_atomically_and_parses() {
        let dir = tempfile::tempdir().unwrap();
        let d = dir.path().join("mios.d");
        let p = write_unlocked(&d, "50-framerate.toml", true).unwrap();
        let v: Value = std::fs::read_to_string(&p).unwrap().parse().unwrap();
        assert_eq!(v["desktop"]["framerate"]["unlocked"].as_bool(), Some(true));
        assert_eq!(v["desktop"]["framerate"].as_table().unwrap().len(), 1, "only the unlocked key");
        use std::os::unix::fs::PermissionsExt;
        assert_eq!(std::fs::metadata(&p).unwrap().permissions().mode() & 0o777, 0o644);
        write_unlocked(&d, "50-framerate.toml", false).unwrap();
        let v: Value = std::fs::read_to_string(&p).unwrap().parse().unwrap();
        assert_eq!(v["desktop"]["framerate"]["unlocked"].as_bool(), Some(false));
        let leftovers: Vec<_> = std::fs::read_dir(&d).unwrap().flatten().filter(|e| e.file_name().to_string_lossy().ends_with(".tmp")).collect();
        assert!(leftovers.is_empty(), "no temp files left behind");
        // the fragment is an ordinary mios.d layer for the resolver
        let merged = mios_resolver::layers::merge_layer_files(&[p.clone()]).unwrap();
        assert_eq!(merged["desktop"]["framerate"]["unlocked"].as_bool(), Some(false));
    }

    #[test]
    fn state_directories_are_created_world_readable_whatever_the_umask() {
        use std::os::unix::fs::PermissionsExt;
        let dir = tempfile::tempdir().unwrap();
        let old = unsafe { libc::umask(0o077) };
        let path = dir.path().join("mios/framerate/state.json");
        write_atomic(&path, b"{}", 0o644).unwrap();
        unsafe { libc::umask(old) };
        for d in [dir.path().join("mios"), dir.path().join("mios/framerate")] {
            assert_eq!(std::fs::metadata(&d).unwrap().permissions().mode() & 0o777, 0o755, "{}", d.display());
        }
        assert_eq!(std::fs::metadata(&path).unwrap().permissions().mode() & 0o777, 0o644);
    }

    #[test]
    fn edid_files_read_as_blob_or_commented_hex_and_garbage_is_refused() {
        let fixture = Path::new(concat!(env!("CARGO_MANIFEST_DIR"), "/../../../tests/fixtures/framerate/dp3-lg-24gl600f.edid.hex"));
        let bytes = read_edid_file(fixture).unwrap();
        assert_eq!(bytes.len(), 256);
        let dir = std::env::temp_dir().join(format!("mios-fr-edid-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let bin = dir.join("edid");
        std::fs::write(&bin, &bytes).unwrap();
        assert_eq!(read_edid_file(&bin).unwrap(), bytes, "the sysfs binary form");
        let e = plan::Edid::parse(&bytes).unwrap();
        assert_eq!(e.range.map(|r| (r.min_v_hz, r.max_v_hz)), Some((48, 144)));
        let junk = dir.join("junk");
        std::fs::write(&junk, "00 ff zz\n").unwrap();
        assert!(read_edid_file(&junk).unwrap_err().contains("bad hex byte"));
        assert!(read_edid_file(&dir.join("absent")).is_err());
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn only_plan_changing_events_wake_the_daemon() {
        assert_eq!(relevant("configreloaded>>", false), Some(true));
        assert_eq!(relevant("monitoraddedv2>>1,DP-3,desc", false), Some(true));
        assert_eq!(relevant("monitorremoved>>DP-3", false), Some(true));
        assert_eq!(relevant("activewindowv2>>0x1", false), None, "focus changes are irrelevant without fullscreen_game");
        assert_eq!(relevant("activewindowv2>>0x1", true), Some(false));
        assert_eq!(relevant("fullscreen>>1", true), Some(false));
        assert_eq!(relevant("openwindow>>0x1,1,kitty,kitty", true), None);
    }
}
