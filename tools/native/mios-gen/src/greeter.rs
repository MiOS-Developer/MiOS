// AI-hint: SSOT projector for greetd's /etc/greetd/config.toml from mios.toml [desktop.greeter]: kind = "glass"
// renders the MiOS glass Quickshell greeter launcher (which falls back to tuigreet on any failure),
// kind = "tuigreet" renders plain tuigreet. "glass" degrades to "tuigreet" when the launcher, greeter
// or compositor is missing on the target, so a projection can never point greetd at nothing.
// AI-related: usr/share/mios/mios.toml [desktop.greeter], usr/libexec/mios/mios-greeter-session,
//   usr/share/mios/greeter/shell.qml, tests/greeter/test-greeter.py

#![forbid(unsafe_code)]

use std::fs;
use std::io::Write;
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};
use toml::Value;

#[derive(Debug, Clone, PartialEq)]
pub struct Greeter {
    pub kind: String,
    pub vt: i64,
    pub user: String,
    pub launcher: String,
    pub greeter_dir: String,
    pub outputs: String,
    pub start_timeout_s: i64,
    pub heartbeat_timeout_s: i64,
    pub default_session: String,
    pub wallpaper: String,
    pub session_dirs: Vec<String>,
    pub tuigreet: String,
    pub tuigreet_args: Vec<String>,
    pub session_cmd: String,
    pub boot_sync: bool,
    /// greetd [initial_session] (skips the greeter once per boot); false = the greeter always runs.
    pub autologin: bool,
    pub autologin_user: String,
    /// Paint the greeter VT with [colors].ansi_* before tuigreet starts.
    pub console_palette: bool,
    /// tuigreet --greeting/--width/--*-padding/--greet-align/--time-format/--theme from the SSOT.
    pub tuigreet_style: Vec<String>,
}

#[derive(Debug)]
pub struct Rendered {
    /// "glass" or "tuigreet": what the projected config actually runs.
    pub effective: String,
    /// Why "glass" was degraded to "tuigreet" (empty when it was not).
    pub degraded: Vec<String>,
    pub text: String,
}

const TABLE: &str = "[desktop.greeter]";

fn s(t: &toml::Table, k: &str) -> Result<String, String> {
    t.get(k)
        .and_then(Value::as_str)
        .map(str::to_string)
        .ok_or_else(|| format!("{TABLE}.{k}: expected a string"))
}

fn abs(t: &toml::Table, k: &str) -> Result<String, String> {
    let v = s(t, k)?;
    if !v.starts_with('/') {
        return Err(format!("{TABLE}.{k}: expected an absolute path, got {v:?}"));
    }
    Ok(v)
}

fn int(t: &toml::Table, k: &str, lo: i64, hi: i64) -> Result<i64, String> {
    let v = t
        .get(k)
        .and_then(Value::as_integer)
        .ok_or_else(|| format!("{TABLE}.{k}: expected an integer"))?;
    if v < lo || v > hi {
        return Err(format!("{TABLE}.{k}: {v} outside {lo}..={hi}"));
    }
    Ok(v)
}

fn list(t: &toml::Table, k: &str) -> Result<Vec<String>, String> {
    t.get(k)
        .and_then(Value::as_array)
        .ok_or_else(|| format!("{TABLE}.{k}: expected an array of strings"))?
        .iter()
        .map(|v| {
            v.as_str()
                .map(str::to_string)
                .ok_or_else(|| format!("{TABLE}.{k}: expected an array of strings"))
        })
        .collect()
}

impl Greeter {
    pub fn from_doc(doc: &Value) -> Result<Self, String> {
        let t = doc
            .get("desktop")
            .and_then(|d| d.get("greeter"))
            .and_then(Value::as_table)
            .ok_or_else(|| format!("Missing {TABLE} in layered mios.toml"))?;
        let kind = s(t, "kind")?;
        if kind != "glass" && kind != "tuigreet" {
            return Err(format!("{TABLE}.kind: expected \"glass\" or \"tuigreet\", got {kind:?}"));
        }
        let fallback = s(t, "fallback")?;
        if fallback != "tuigreet" {
            return Err(format!("{TABLE}.fallback: only \"tuigreet\" is supported, got {fallback:?}"));
        }
        let compositor = s(t, "compositor")?;
        if compositor != "cage" {
            return Err(format!("{TABLE}.compositor: only \"cage\" is supported, got {compositor:?}"));
        }
        let outputs = s(t, "outputs")?;
        if outputs != "extend" && outputs != "last" {
            return Err(format!("{TABLE}.outputs: expected \"extend\" or \"last\", got {outputs:?}"));
        }
        let user = s(t, "user")?;
        if user.is_empty() || !user.bytes().all(|c| c.is_ascii_alphanumeric() || b"_-.".contains(&c)) {
            return Err(format!("{TABLE}.user: invalid account name {user:?}"));
        }
        let default_session = s(t, "default_session")?;
        if default_session.is_empty() || default_session.contains('/') {
            return Err(format!("{TABLE}.default_session: expected a desktop-file id, got {default_session:?}"));
        }
        let session_dirs = list(t, "session_dirs")?;
        if session_dirs.is_empty() || session_dirs.iter().any(|d| !d.starts_with('/') || d.contains(':')) {
            return Err(format!("{TABLE}.session_dirs: expected absolute directories without ':'"));
        }
        let wallpaper = s(t, "wallpaper")?;
        if !wallpaper.is_empty() && !wallpaper.starts_with('/') {
            return Err(format!("{TABLE}.wallpaper: expected \"\" or an absolute path"));
        }
        let (autologin, autologin_user) = autologin(t)?;
        Ok(Self {
            kind,
            vt: int(t, "vt", 1, 63)?,
            user,
            launcher: abs(t, "launcher")?,
            greeter_dir: abs(t, "greeter_dir")?,
            outputs,
            start_timeout_s: int(t, "start_timeout_s", 3, 600)?,
            heartbeat_timeout_s: int(t, "heartbeat_timeout_s", 10, 3600)?,
            default_session,
            wallpaper,
            session_dirs,
            tuigreet: abs(t, "tuigreet")?,
            tuigreet_args: list(t, "tuigreet_args")?,
            session_cmd: abs(t, "session_cmd")?,
            boot_sync: crate::render::desktop_boot::enabled(doc)?,
            autologin,
            autologin_user,
            console_palette: opt_bool(t, "console_palette")?,
            tuigreet_style: tuigreet_style(t)?,
        })
    }

    fn tuigreet_argv(&self) -> Vec<String> {
        let mut v = vec![self.tuigreet.clone()];
        v.extend(self.tuigreet_args.iter().cloned());
        v.extend(self.tuigreet_style.iter().cloned());
        v.push("--sessions".into());
        v.push(self.session_dirs.join(":"));
        v.push("--cmd".into());
        v.push(self.session_cmd.clone());
        v
    }

    fn glass_argv(&self) -> Vec<String> {
        let mut v = vec![
            self.launcher.clone(),
            "--greeter-dir".into(),
            self.greeter_dir.clone(),
            "--sessions".into(),
            self.session_dirs.join(":"),
            "--default-session".into(),
            self.default_session.clone(),
            "--start-timeout".into(),
            self.start_timeout_s.to_string(),
            "--heartbeat-timeout".into(),
            self.heartbeat_timeout_s.to_string(),
            "--outputs".into(),
            self.outputs.clone(),
        ];
        if !self.wallpaper.is_empty() {
            v.push("--wallpaper".into());
            v.push(self.wallpaper.clone());
        }
        v.push("--".into());
        v.extend(self.tuigreet_argv());
        v
    }

    /// Render greetd's config.toml. `sysroot` is the filesystem the config will run on ("/" live,
    /// a staging dir in tests): "glass" is only rendered when its launcher, greeter and cage exist there.
    pub fn render(&self, sysroot: &Path) -> Rendered {
        let on = |p: &str| sysroot.join(p.trim_start_matches('/'));
        let mut degraded = Vec::new();
        if self.kind == "glass" {
            let launcher = on(&self.launcher);
            let exec_ok = fs::metadata(&launcher).is_ok_and(|m| m.is_file() && m.permissions().mode() & 0o111 != 0);
            if !exec_ok {
                degraded.push(format!("launcher {} is not an executable file", launcher.display()));
            }
            let shell = on(&self.greeter_dir).join("shell.qml");
            if !shell.is_file() {
                degraded.push(format!("greeter {} is missing", shell.display()));
            }
            if !on("/usr/bin/cage").is_file() {
                degraded.push("compositor /usr/bin/cage is missing".into());
            }
        }
        let effective = if self.kind == "glass" && degraded.is_empty() { "glass" } else { "tuigreet" };
        let argv = if self.boot_sync {
            vec!["/usr/bin/mios-gen".into(), "hyprland-greeter".into(), "--kind".into(), effective.into()]
        } else if effective == "glass" { self.glass_argv() } else { self.tuigreet_argv() };
        let command = argv.iter().map(|a| sh_quote(a)).collect::<Vec<_>>().join(" ");
        let mut text = String::new();
        text.push_str("# AI-hint: greetd configuration projected by `mios-gen render-greetd` from mios.toml [desktop.greeter].\n");
        text.push_str("# DO NOT EDIT: change [desktop.greeter] and re-render. kind = \"tuigreet\" reverts to the text greeter.\n");
        text.push_str(&format!("# greeter: {effective}"));
        if !degraded.is_empty() {
            text.push_str(&format!(" (glass requested; degraded: {})", degraded.join("; ")));
        }
        text.push_str("\n\n[terminal]\n");
        text.push_str(&format!("vt = {}\n\n[default_session]\n", self.vt));
        text.push_str(&format!("command = {}\n", Value::String(command)));
        text.push_str(&format!("user = {}\n", Value::String(self.user.clone())));
        if self.autologin {
            text.push_str("\n[initial_session]\n");
            text.push_str(&format!("command = {}\n", Value::String(self.session_cmd.clone())));
            text.push_str(&format!("user = {}\n", Value::String(self.autologin_user.clone())));
        }
        Rendered { effective: effective.into(), degraded, text }
    }
}

/// tuigreet --theme colour names (ratatui); they resolve through the VT palette painted from [colors].ansi_*.
const TUI_COLORS: &[&str] = &[
    "black", "red", "green", "yellow", "blue", "magenta", "cyan", "gray", "darkgray", "lightred", "lightgreen",
    "lightyellow", "lightblue", "lightmagenta", "lightcyan", "white",
];
const TUI_THEME_KEYS: &[&str] = &["text", "time", "container", "border", "title", "greet", "prompt", "input", "action", "button"];

fn opt_bool(t: &toml::Table, k: &str) -> Result<bool, String> {
    match t.get(k) {
        None => Ok(false),
        Some(v) => v.as_bool().ok_or_else(|| format!("{TABLE}.{k}: expected a boolean")),
    }
}

/// greetd [initial_session]: off unless the SSOT asks for it, so the greeter always runs by default.
fn autologin(t: &toml::Table) -> Result<(bool, String), String> {
    let on = opt_bool(t, "autologin")?;
    let user = match t.get("autologin_user") {
        None => String::new(),
        Some(v) => v.as_str().ok_or_else(|| format!("{TABLE}.autologin_user: expected a string"))?.to_string(),
    };
    if on && (user.is_empty() || !user.bytes().all(|c| c.is_ascii_alphanumeric() || b"_-.".contains(&c))) {
        return Err(format!("{TABLE}.autologin_user: autologin = true needs a valid account name, got {user:?}"));
    }
    Ok((on, user))
}

/// tuigreet presentation flags from [desktop.greeter] and [desktop.greeter.tuigreet_theme].
fn tuigreet_style(t: &toml::Table) -> Result<Vec<String>, String> {
    let mut v = Vec::new();
    if let Some(x) = t.get("greeting") {
        let s = x.as_str().ok_or_else(|| format!("{TABLE}.greeting: expected a string"))?;
        if s.contains('\n') {
            return Err(format!("{TABLE}.greeting: newlines are not allowed"));
        }
        if !s.is_empty() {
            v.push("--greeting".to_string());
            v.push(s.to_string());
        }
    }
    let numbers: [(&str, &str, i64, i64); 4] = [
        ("width", "--width", 20, 200),
        ("window_padding", "--window-padding", 0, 20),
        ("container_padding", "--container-padding", 0, 20),
        ("prompt_padding", "--prompt-padding", 0, 10),
    ];
    for (key, flag, lo, hi) in numbers {
        if let Some(x) = t.get(key) {
            let n = x
                .as_integer()
                .filter(|n| (lo..=hi).contains(n))
                .ok_or_else(|| format!("{TABLE}.{key}: expected an integer in {lo}..={hi}"))?;
            v.push(flag.to_string());
            v.push(n.to_string());
        }
    }
    if let Some(x) = t.get("greet_align") {
        let s = x.as_str().unwrap_or("");
        if !matches!(s, "left" | "center" | "right") {
            return Err(format!("{TABLE}.greet_align: expected \"left\", \"center\" or \"right\""));
        }
        v.push("--greet-align".to_string());
        v.push(s.to_string());
    }
    if let Some(x) = t.get("time_format") {
        let s = x.as_str().unwrap_or("");
        if s.is_empty() || s.contains('\n') {
            return Err(format!("{TABLE}.time_format: expected a non-empty single-line strftime format"));
        }
        v.push("--time-format".to_string());
        v.push(s.to_string());
    }
    if let Some(x) = t.get("tuigreet_theme") {
        let th = x.as_table().ok_or_else(|| format!("{TABLE}.tuigreet_theme: expected a table"))?;
        for k in th.keys() {
            if !TUI_THEME_KEYS.contains(&k.as_str()) {
                return Err(format!("{TABLE}.tuigreet_theme.{k}: unknown tuigreet theme component"));
            }
        }
        let mut parts = Vec::new();
        for key in TUI_THEME_KEYS {
            if let Some(c) = th.get(*key) {
                let c = c.as_str().ok_or_else(|| format!("{TABLE}.tuigreet_theme.{key}: expected a string"))?;
                let hex = c.len() == 7 && c.starts_with('#') && c[1..].bytes().all(|b| b.is_ascii_hexdigit());
                if !hex && !TUI_COLORS.contains(&c) {
                    return Err(format!("{TABLE}.tuigreet_theme.{key}: expected an ANSI colour name or #RRGGBB, got {c:?}"));
                }
                parts.push(format!("{key}={c}"));
            }
        }
        if !parts.is_empty() {
            v.push("--theme".to_string());
            v.push(parts.join(";"));
        }
    }
    Ok(v)
}

/// Paint the greeter's virtual console with the SSOT ANSI palette (terminal::console) before tuigreet draws.
fn paint_console(doc: &Value) {
    let on_vt = fs::read_link("/proc/self/fd/1").ok().and_then(|p| p.to_str().map(str::to_string)).is_some_and(|p| {
        p.strip_prefix("/dev/tty").is_some_and(|n| !n.is_empty() && n.bytes().all(|b| b.is_ascii_digit()))
    });
    if !on_vt {
        return;
    }
    if let Ok(esc) = crate::terminal::console::escapes(doc, "linux") {
        let mut out = std::io::stdout();
        let _ = out.write_all(esc.as_bytes());
        let _ = out.write_all(b"\x1b[2J\x1b[H");
        let _ = out.flush();
    }
}

fn boot_settings(doc: &Value, cmdline: &str, kind: Option<&str>) -> Result<(Greeter, String, Vec<String>), String> {
    let mut g = Greeter::from_doc(doc)?;
    let profile = crate::render::desktop_boot::boot_profile(doc, cmdline)?;
    let file = doc["desktop"]["hyprland"]["hardware_profiles"][&profile]["session_file"]
        .as_str().filter(|v| v.ends_with(".desktop") && !v.contains('/') && !v.contains(".."))
        .ok_or("Invalid GPU session file")?;
    g.default_session = file.trim_end_matches(".desktop").into();
    g.session_cmd = format!("/usr/bin/mios-gen hyprland-session --hardware-profile {profile}");
    // A global boot-selected cache gives every username the same initial GPU.
    // The chooser can still select another GPU, which becomes a boot request.
    g.tuigreet_args.retain(|a| a != "--remember-user-session" && a != "--remember-session");
    g.tuigreet_args.push("--remember-session".into());
    let effective = kind.unwrap_or(&g.kind);
    let argv = match effective {
        "tuigreet" => g.tuigreet_argv(), "glass" => g.glass_argv(),
        _ => return Err("Invalid greeter kind".into()),
    };
    Ok((g, profile, argv))
}

/// Resolve the booted GPU before either login UI reads its remembered session.
pub fn runtime(kind: Option<&str>, dry_run: bool) -> Result<String, String> {
    use std::os::unix::process::CommandExt;
    let doc = mios_resolver::resolve_projection(Path::new("/")).map_err(|e| e.to_string())?;
    let (g, profile, argv) = boot_settings(&doc,
        &fs::read_to_string("/proc/cmdline").map_err(|e| e.to_string())?, kind)?;
    let file = format!("{}.desktop",g.default_session);
    if dry_run {
        return Ok(serde_json::json!({"hardware_profile":profile,"session_file":file,"argv":argv}).to_string());
    }
    write_atomic(Path::new("/var/lib/greetd/tuigreet-lastsession-path"),
        &format!("/usr/share/wayland-sessions/{file}"))?;
    if g.console_palette {
        paint_console(&doc);
    }
    let mut command = std::process::Command::new(&argv[0]);
    command.args(&argv[1..]).env("MIOS_GREETER_BOOT_SESSION", &g.default_session);
    Err(format!("Failed to start boot-selected greeter: {}", command.exec()))
}

/// greetd runs the command through /bin/sh, so every argument is shell-quoted when needed.
pub fn sh_quote(a: &str) -> String {
    if !a.is_empty() && a.bytes().all(|c| c.is_ascii_alphanumeric() || b"_@%+=:,./-".contains(&c)) {
        a.to_string()
    } else {
        format!("'{}'", a.replace('\'', "'\\''"))
    }
}

/// Atomic replace: temp file in the target's directory, fsync, keep the old mode/owner, rename, fsync dir.
pub fn write_atomic(target: &Path, data: &str) -> Result<(), String> {
    let dir = target
        .parent()
        .filter(|p| !p.as_os_str().is_empty())
        .unwrap_or_else(|| Path::new("."));
    let old = fs::metadata(target).ok();
    let mut tmp = tempfile::Builder::new()
        .prefix(".config.toml.mios-gen.")
        .tempfile_in(dir)
        .map_err(|e| format!("temp file in {}: {e}", dir.display()))?;
    tmp.write_all(data.as_bytes()).map_err(|e| e.to_string())?;
    tmp.as_file().sync_all().map_err(|e| e.to_string())?;
    let mode = old.as_ref().map_or(0o644, |m| m.permissions().mode() & 0o7777);
    fs::set_permissions(tmp.path(), fs::Permissions::from_mode(mode)).map_err(|e| e.to_string())?;
    if let Some(m) = &old {
        std::os::unix::fs::chown(tmp.path(), Some(m.uid()), Some(m.gid()))
            .map_err(|e| format!("chown {}: {e}", tmp.path().display()))?;
    }
    tmp.persist(target).map_err(|e| format!("rename onto {}: {}", target.display(), e.error))?;
    fs::File::open(dir).and_then(|d| d.sync_all()).map_err(|e| e.to_string())?;
    Ok(())
}

pub enum Mode {
    Stdout,
    Write(PathBuf),
    Check(PathBuf),
}

/// `mios-gen render-greetd`: resolve vendor+host SSOT under `root`, render, then print / write / check.
pub fn run(root: &Path, sysroot: &Path, mode: Mode) -> Result<Rendered, String> {
    let doc = mios_resolver::resolve_projection(root).map_err(|e| e.to_string())?;
    let r = Greeter::from_doc(&doc)?.render(sysroot);
    match mode {
        Mode::Stdout => print!("{}", r.text),
        Mode::Write(p) => write_atomic(&p, &r.text)?,
        Mode::Check(p) => {
            let disk = fs::read_to_string(&p).map_err(|e| format!("read {}: {e}", p.display()))?;
            if disk != r.text {
                return Err(format!("{} differs from the [desktop.greeter] projection", p.display()));
            }
        }
    }
    Ok(r)
}

#[cfg(test)]
mod tests {
    use super::*;

    const FRAGMENT: &str = include_str!("../tests/fixtures/greeter-ssot.toml");

    #[test]
    fn gpu_boot_choices_set_both_greeters_and_override_stale_session_memory() {
        let mut doc: Value = FRAGMENT.parse().unwrap();
        doc["desktop"].as_table_mut().unwrap().insert("hyprland".into(), toml::toml! {
            hardware_profile = "igpu"
            [boot_sync]
            enabled = true
            [hardware_profiles.igpu]
            session_file = "hyprland.desktop"
            [hardware_profiles.dgpu]
            session_file = "mios-desktop-dgpu.desktop"
        }.into());
        for (profile, file) in [("igpu", "hyprland"), ("dgpu", "mios-desktop-dgpu")] {
            for kind in ["glass", "tuigreet"] {
                let (g, chosen, args) = boot_settings(&doc, &format!("quiet mios.desktop.gpu={profile}"), Some(kind)).unwrap();
                assert_eq!(chosen,profile);
                assert_eq!(g.default_session,file);
                assert_eq!(g.session_cmd,format!("/usr/bin/mios-gen hyprland-session --hardware-profile {profile}"));
                assert!(!args.iter().any(|v| v == "--remember-user-session"));
                assert!(args.iter().any(|v| v == "--remember-session"));
                assert!(args.windows(2).any(|v| v[0] == "--cmd" && v[1] == g.session_cmd));
                if kind == "glass" {
                    assert!(args.windows(2).any(|v| v[0] == "--default-session" && v[1] == file));
                }
            }
        }
        let sys = sysroot(true,true,true);
        let output = Greeter::from_doc(&doc).unwrap().render(sys.path());
        assert!(output.text.contains("/usr/bin/mios-gen hyprland-greeter --kind glass"));
        assert!(boot_settings(&doc,"mios.desktop.gpu=unknown",Some("tuigreet")).is_err());
        assert!(boot_settings(&doc,"mios.desktop.gpu=dgpu",Some("unknown")).is_err());
    }

    fn tmp() -> tempfile::TempDir {
        tempfile::tempdir().unwrap()
    }

    fn root_with(fragment: &str) -> tempfile::TempDir {
        let d = tmp();
        let p = d.path().join("usr/share/mios");
        fs::create_dir_all(&p).unwrap();
        fs::write(p.join("mios.toml"), fragment).unwrap();
        d
    }

    fn sysroot(launcher: bool, greeter: bool, cage: bool) -> tempfile::TempDir {
        let d = tmp();
        let r = d.path();
        if launcher {
            fs::create_dir_all(r.join("usr/libexec/mios")).unwrap();
            let l = r.join("usr/libexec/mios/mios-greeter-session");
            fs::write(&l, "#!/bin/sh\n").unwrap();
            fs::set_permissions(&l, fs::Permissions::from_mode(0o755)).unwrap();
        }
        if greeter {
            fs::create_dir_all(r.join("usr/share/mios/greeter")).unwrap();
            fs::write(r.join("usr/share/mios/greeter/shell.qml"), "").unwrap();
        }
        if cage {
            fs::create_dir_all(r.join("usr/bin")).unwrap();
            fs::write(r.join("usr/bin/cage"), "").unwrap();
        }
        d
    }

    fn cfg(fragment: &str) -> Result<Greeter, String> {
        Greeter::from_doc(&toml::from_str::<Value>(fragment).unwrap())
    }

    const LIVE_TUIGREET: &str = "/usr/bin/tuigreet --time --asterisks --remember --remember-user-session --sessions /usr/share/wayland-sessions:/usr/share/xsessions --cmd /usr/local/bin/start-hyprland-igpu";

    #[test]
    fn glass_renders_launcher_with_tuigreet_fallback() {
        let sys = sysroot(true, true, true);
        let r = cfg(FRAGMENT).unwrap().render(sys.path());
        assert_eq!(r.effective, "glass", "{:?}", r.degraded);
        let doc: Value = toml::from_str(&r.text).unwrap();
        let cmd = doc["default_session"]["command"].as_str().unwrap();
        assert!(cmd.starts_with("/usr/libexec/mios/mios-greeter-session --greeter-dir /usr/share/mios/greeter "), "{cmd}");
        assert!(cmd.ends_with(&format!(" -- {LIVE_TUIGREET}")), "{cmd}");
        assert!(cmd.contains("--default-session hyprland --start-timeout 20 --heartbeat-timeout 30 --outputs extend"));
        assert_eq!(doc["default_session"]["user"].as_str(), Some("greetd"));
        assert_eq!(doc["terminal"]["vt"].as_integer(), Some(1));
    }

    #[test]
    fn tuigreet_kind_reproduces_the_live_config_command() {
        let frag = FRAGMENT.replace("kind                = \"glass\"", "kind = \"tuigreet\"");
        let sys = sysroot(true, true, true);
        let r = cfg(&frag).unwrap().render(sys.path());
        assert_eq!(r.effective, "tuigreet");
        let doc: Value = toml::from_str(&r.text).unwrap();
        assert_eq!(doc["default_session"]["command"].as_str(), Some(LIVE_TUIGREET));
    }

    #[test]
    fn negative_glass_degrades_when_launcher_greeter_or_cage_missing() {
        for (l, g, c) in [(false, true, true), (true, false, true), (true, true, false)] {
            let sys = sysroot(l, g, c);
            let r = cfg(FRAGMENT).unwrap().render(sys.path());
            assert_eq!(r.effective, "tuigreet", "launcher={l} greeter={g} cage={c}");
            assert_eq!(r.degraded.len(), 1);
            assert!(r.text.contains(LIVE_TUIGREET));
        }
        // Not executable counts as missing.
        let sys = sysroot(true, true, true);
        let l = sys.path().join("usr/libexec/mios/mios-greeter-session");
        fs::set_permissions(&l, fs::Permissions::from_mode(0o644)).unwrap();
        assert_eq!(cfg(FRAGMENT).unwrap().render(sys.path()).effective, "tuigreet");
    }

    #[test]
    fn negative_invalid_values_are_rejected() {
        for (from, to) in [
            ("kind                = \"glass\"", "kind = \"gdm\""),
            ("fallback            = \"tuigreet\"", "fallback = \"none\""),
            ("compositor          = \"cage\"", "compositor = \"weston\""),
            ("outputs             = \"extend\"", "outputs = \"all\""),
            ("start_timeout_s     = 20", "start_timeout_s = 0"),
            ("session_cmd         = \"/usr/local/bin/start-hyprland-igpu\"", "session_cmd = \"start-hyprland\""),
            ("user                = \"greetd\"", "user = \"greetd; rm\""),
        ] {
            let frag = FRAGMENT.replace(from, to);
            assert_ne!(frag, FRAGMENT, "fixture drifted: {from}");
            assert!(cfg(&frag).is_err(), "{to} must be rejected");
        }
        assert!(Greeter::from_doc(&toml::from_str::<Value>("[desktop]\nsession = \"x\"\n").unwrap()).is_err());
    }

    #[test]
    fn autologin_and_tuigreet_theme_are_projected_from_the_ssot() {
        let off = cfg(FRAGMENT).unwrap();
        assert!(!off.autologin && !off.console_palette);
        assert!(!off.render(Path::new("/nonexistent")).text.contains("[initial_session]"));
        let frag = FRAGMENT.replacen(
            "[desktop.greeter]\n",
            "[desktop.greeter]\nautologin = true\nautologin_user = \"mios\"\nconsole_palette = true\ngreeting = \"MiOS\"\nwidth = 64\ngreet_align = \"center\"\n",
            1,
        ) + "\n[desktop.greeter.tuigreet_theme]\ncontainer = \"black\"\nborder = \"lightblue\"\ntitle = \"#F35C15\"\n";
        let g = cfg(&frag).unwrap();
        assert!(g.console_palette);
        let text = g.render(Path::new("/nonexistent")).text;
        assert!(text.contains("\n[initial_session]\n") && text.contains("user = \"mios\""), "{text}");
        let joined = g.tuigreet_argv().join(" ");
        assert!(joined.contains("--greeting MiOS --width 64 --greet-align center --theme container=black;border=lightblue;title=#F35C15"), "{joined}");
    }

    #[test]
    fn negative_autologin_and_tuigreet_style_values_are_rejected() {
        for extra in ["autologin = true\n", "autologin = true\nautologin_user = \"bad user\"\n", "greet_align = \"middle\"\n", "width = 5\n", "autologin = \"yes\"\n"] {
            let frag = FRAGMENT.replacen("[desktop.greeter]\n", &format!("[desktop.greeter]\n{extra}"), 1);
            assert!(cfg(&frag).is_err(), "{extra}");
        }
        for theme in ["border = \"chartreuse\"\n", "sparkle = \"red\"\n", "border = 3\n"] {
            let frag = format!("{FRAGMENT}\n[desktop.greeter.tuigreet_theme]\n{theme}");
            assert!(cfg(&frag).is_err(), "{theme}");
        }
    }

    #[test]
    fn shell_quoting_survives_spaces_and_quotes() {
        assert_eq!(sh_quote("/usr/bin/tuigreet"), "/usr/bin/tuigreet");
        assert_eq!(sh_quote("a b"), "'a b'");
        assert_eq!(sh_quote("it's"), "'it'\\''s'");
        assert_eq!(sh_quote(""), "''");
        let frag = FRAGMENT.replace("wallpaper           = \"\"", "wallpaper = \"/srv/my walls/it's.png\"");
        let sys = sysroot(true, true, true);
        let r = cfg(&frag).unwrap().render(sys.path());
        let doc: Value = toml::from_str(&r.text).unwrap();
        let cmd = doc["default_session"]["command"].as_str().unwrap();
        assert!(cmd.contains("--wallpaper '/srv/my walls/it'\\''s.png' --"), "{cmd}");
    }

    #[test]
    fn run_reads_temp_ssot_writes_atomically_and_checks() {
        let root = root_with(FRAGMENT);
        let sys = sysroot(true, true, true);
        let out = sys.path().join("etc/greetd");
        fs::create_dir_all(&out).unwrap();
        let target = out.join("config.toml");
        fs::write(&target, "old").unwrap();
        fs::set_permissions(&target, fs::Permissions::from_mode(0o640)).unwrap();
        let r = run(root.path(), sys.path(), Mode::Write(target.clone())).unwrap();
        assert_eq!(r.effective, "glass");
        assert_eq!(fs::read_to_string(&target).unwrap(), r.text);
        assert_eq!(fs::metadata(&target).unwrap().permissions().mode() & 0o777, 0o640, "mode preserved");
        let leftovers: Vec<_> = fs::read_dir(&out).unwrap().filter_map(|e| e.ok()).map(|e| e.file_name()).collect();
        assert_eq!(leftovers.len(), 1, "no temp files left: {leftovers:?}");
        run(root.path(), sys.path(), Mode::Check(target.clone())).unwrap();
        // Negative: drift is reported.
        fs::write(&target, "drifted").unwrap();
        assert!(run(root.path(), sys.path(), Mode::Check(target)).is_err());
        // Negative: an SSOT without the table fails instead of inventing defaults.
        let bare = root_with("[desktop]\nsession = \"gnome\"\n");
        assert!(run(bare.path(), sys.path(), Mode::Stdout).is_err());
    }
}
