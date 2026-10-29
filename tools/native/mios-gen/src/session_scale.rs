// AI-hint: mios-gen session-scale up|down|reset: scales the whole session (apps, HUD, shell) on the focused output by stepping its Hyprland scale along the valid 1/120 grid, live and saved to the user tier as [desktop.hyprland.monitors.<output>].scale; when the focused window is a nested MiOS session it zooms that session instead.
// AI-related: tools/native/mios-gen/src/display_scale.rs, tools/native/mios-gen/src/nested_session.rs (mios_nested_zoom), usr/share/mios/mios.toml [desktop.scaling] session_step/session_min/session_max, [keybindings.global] session-scale bindings
// AI-functions: run, step_scale, user_tier_with_scale, nested_target
use serde_json::Value as Json;
use std::fs::{File, OpenOptions};
use std::io::Write;
use std::os::unix::fs::OpenOptionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use toml::Value;

use crate::display_scale::{self, GRID};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Dir {
    Up,
    Down,
    Reset,
}

impl Dir {
    pub fn parse(s: &str) -> Result<Dir, String> {
        match s {
            "up" | "+" => Ok(Dir::Up),
            "down" | "-" => Ok(Dir::Down),
            "reset" | "0" => Ok(Dir::Reset),
            _ => Err(format!("session-scale: {s:?} is not up, down or reset")),
        }
    }
    fn word(self) -> &'static str {
        match self {
            Dir::Up => "up",
            Dir::Down => "down",
            Dir::Reset => "reset",
        }
    }
}

/// [desktop.scaling] session_step / session_min / session_max (the manual range; the automatic policy keeps its own
/// min_scale/max_scale).
pub struct Range {
    pub step: f64,
    pub min: f64,
    pub max: f64,
}

impl Range {
    pub fn from_ssot(data: &Value) -> Result<Range, String> {
        let s = data.get("desktop").and_then(|d| d.get("scaling")).ok_or("[desktop.scaling] is missing")?;
        let f = |k: &str| -> Result<f64, String> {
            let v = s.get(k).and_then(|v| v.as_float().or_else(|| v.as_integer().map(|i| i as f64))).ok_or(format!("[desktop.scaling].{k} must be a number"))?;
            if v.is_finite() && v > 0.0 && v <= 8.0 {
                Ok(v)
            } else {
                Err(format!("[desktop.scaling].{k} must be in (0, 8]"))
            }
        };
        let r = Range { step: f("session_step")?, min: f("session_min")?, max: f("session_max")? };
        if r.min > r.max {
            return Err("[desktop.scaling].session_min exceeds session_max".into());
        }
        Ok(r)
    }
}

fn valid(w: i64, h: i64, k: i64) -> bool {
    k >= 1 && (w * GRID) % k == 0 && (h * GRID) % k == 0
}

/// The next scale (in 120ths) from `cur` one step up or down that Hyprland accepts for a w x h mode: at least one
/// full step away, inside [min, max], with a whole logical size. None when the range ends there.
pub fn step_scale(w: i64, h: i64, cur: f64, dir: Dir, r: &Range) -> Option<i64> {
    let g = GRID as f64;
    let cur_k = (cur * g).round() as i64;
    let step_k = ((r.step * g).round() as i64).max(1);
    let (lo, hi) = ((r.min * g).ceil() as i64, (r.max * g).floor() as i64);
    match dir {
        Dir::Up => (cur_k + step_k..=hi).find(|k| valid(w, h, *k)),
        Dir::Down => (lo..=cur_k - step_k).rev().find(|k| valid(w, h, *k)),
        Dir::Reset => None,
    }
}

fn hyprctl(args: &[&str], env: &[(&str, &str)]) -> Result<String, String> {
    let out = Command::new("hyprctl").args(args).envs(env.iter().copied()).output().map_err(|e| format!("hyprctl: {e}"))?;
    if !out.status.success() {
        return Err(format!("hyprctl {}: {}", args.join(" "), String::from_utf8_lossy(&out.stderr).trim()));
    }
    Ok(String::from_utf8_lossy(&out.stdout).into_owned())
}

fn eval(lua: &str, env: &[(&str, &str)]) -> Result<(), String> {
    let out = hyprctl(&["eval", lua], env)?;
    if out.trim() != "ok" && !out.trim().is_empty() {
        return Err(format!("compositor refused the change: {}", out.trim()));
    }
    Ok(())
}

/// The nested MiOS session whose parent PiP/window is `address`: (runtime dir, instance signature).
pub fn nested_target(runtime: &Path, address: &str) -> Option<(PathBuf, String)> {
    let want = address.trim_start_matches("0x");
    for e in std::fs::read_dir(runtime).ok()?.flatten() {
        let name = e.file_name();
        if !name.to_string_lossy().starts_with("mios-nested-desktop-") {
            continue;
        }
        let Ok(text) = std::fs::read_to_string(e.path().join("session.json")) else { continue };
        let Ok(v) = serde_json::from_str::<Json>(&text) else { continue };
        if v["parent_window"].as_str().map(|a| a.trim_start_matches("0x")) == Some(want) {
            let inst = v["instance"]["instance"].as_str().or_else(|| v["instance"].as_str())?.to_owned();
            return Some((PathBuf::from(v["runtime"].as_str()?), inst));
        }
    }
    None
}

/// True when `runtime` is a nested MiOS session's own runtime directory (mios-gen nested-session creates it with a
/// session.json descriptor).
pub fn is_nested_runtime(runtime: &Path) -> bool {
    runtime.file_name().is_some_and(|n| n.to_string_lossy().starts_with("mios-nested-desktop-")) && runtime.join("session.json").is_file()
}

/// The user tier with [desktop.hyprland.monitors.<output>].scale set (Some) or removed (None); every other byte kept.
pub fn user_tier_with_scale(text: &str, output: &str, scale: Option<f64>) -> Result<String, String> {
    let mut doc: toml_edit::Document = text.parse().map_err(|e| format!("user tier: {e}"))?;
    let root = doc.as_table_mut();
    let path = ["desktop", "hyprland", "monitors", output];
    if let Some(s) = scale {
        let mut t = &mut *root;
        for key in path {
            if !t.contains_key(key) {
                let mut child = toml_edit::Table::new();
                child.set_implicit(key != output);
                t.insert(key, toml_edit::Item::Table(child));
            }
            t = t.get_mut(key).and_then(toml_edit::Item::as_table_mut).ok_or(format!("user tier: {key} is not a table"))?;
        }
        t.insert("scale", toml_edit::value(s));
    } else {
        fn prune(t: &mut toml_edit::Table, path: &[&str]) {
            if path.len() == 1 {
                if let Some(o) = t.get_mut(path[0]).and_then(toml_edit::Item::as_table_mut) {
                    o.remove("scale");
                    if o.is_empty() {
                        t.remove(path[0]);
                    }
                }
                return;
            }
            if let Some(c) = t.get_mut(path[0]).and_then(toml_edit::Item::as_table_mut) {
                prune(c, &path[1..]);
                if c.is_empty() {
                    t.remove(path[0]);
                }
            }
        }
        prune(root, &path);
    }
    Ok(doc.to_string())
}

/// Atomic private write of the user tier under its lock; refuses when it changed since `before` was read.
fn save_user_tier(path: &Path, before: &str, after: &str) -> Result<(), String> {
    if before == after {
        return Ok(());
    }
    let dir = path.parent().ok_or("user tier has no directory")?;
    std::fs::create_dir_all(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    let lock_path = dir.join(format!(".{}.lock", path.file_name().and_then(|n| n.to_str()).unwrap_or("mios.toml")));
    let lock = OpenOptions::new().read(true).write(true).create(true).truncate(false).mode(0o600).open(&lock_path).map_err(|e| format!("{}: {e}", lock_path.display()))?;
    lock.lock().map_err(|e| format!("lock {}: {e}", lock_path.display()))?;
    let now = std::fs::read_to_string(path).unwrap_or_default();
    if now != before {
        return Err(format!("{} changed while scaling; nothing saved", path.display()));
    }
    let tmp = dir.join(format!(".mios-session-scale-{}.tmp", std::process::id()));
    {
        let mut f = OpenOptions::new().write(true).create(true).truncate(true).mode(0o600).open(&tmp).map_err(|e| format!("{}: {e}", tmp.display()))?;
        f.write_all(after.as_bytes()).and_then(|_| f.sync_all()).map_err(|e| format!("{}: {e}", tmp.display()))?;
    }
    std::fs::rename(&tmp, path).map_err(|e| {
        let _ = std::fs::remove_file(&tmp);
        format!("{}: {e}", path.display())
    })?;
    File::open(dir).and_then(|d| d.sync_all()).map_err(|e| e.to_string())
}

pub fn run(root: &Path, dir: Dir) -> Result<String, String> {
    let runtime = PathBuf::from(std::env::var_os("XDG_RUNTIME_DIR").ok_or("XDG_RUNTIME_DIR is not set")?);
    std::env::var("HYPRLAND_INSTANCE_SIGNATURE").map_err(|_| "no compositor (HYPRLAND_INSTANCE_SIGNATURE) to scale")?;
    // A focused nested MiOS session is scaled from inside: its own desktop zooms, the parent output stays.
    // No focused window is a normal state (hyprctl then fails or prints nothing): it just means "not a nested session".
    let active: Json = hyprctl(&["-j", "activewindow"], &[]).ok().and_then(|t| serde_json::from_str(&t).ok()).unwrap_or(Json::Null);
    if let Some(addr) = active["address"].as_str() {
        if let Some((rt, sig)) = nested_target(&runtime, addr) {
            let rt_s = rt.display().to_string();
            eval(&format!("mios_nested_zoom('{}')", dir.word()), &[("XDG_RUNTIME_DIR", &rt_s), ("HYPRLAND_INSTANCE_SIGNATURE", &sig)])?;
            return Ok(format!("nested session zoom {}", dir.word()));
        }
    }
    // Inside a nested session (its runtime directory is the session's own): zoom this session; never persist a
    // nested output's scale into the operator's user tier, which nested sessions share.
    if is_nested_runtime(&runtime) {
        eval(&format!("mios_nested_zoom('{}')", dir.word()), &[])?;
        return Ok(format!("this nested session zoom {}", dir.word()));
    }
    let data = mios_resolver::resolve_merged(Some(root), false).map_err(|e| e.to_string())?;
    let range = Range::from_ssot(&data)?;
    let monitors: Json = serde_json::from_str(&hyprctl(&["-j", "monitors"], &[])?).map_err(|e| e.to_string())?;
    let m = monitors.as_array().and_then(|a| a.iter().find(|m| m["focused"].as_bool() == Some(true))).ok_or("no focused output")?;
    let name = m["name"].as_str().filter(|n| !n.is_empty() && n.bytes().all(|c| c.is_ascii_alphanumeric() || c == b'-' || c == b'_')).ok_or("focused output has no usable name")?;
    let (w, h) = (m["width"].as_i64().unwrap_or(0), m["height"].as_i64().unwrap_or(0));
    let cur = m["scale"].as_f64().unwrap_or(1.0);
    let (scale, explicit) = match dir {
        Dir::Reset => {
            // Back to what the tiers below the user chose: a host-saved scale, else the automatic policy.
            let below = mios_resolver::resolve_below_user(Some(root)).map_err(|e| e.to_string())?;
            let saved = crate::render::render_hyprland::explicit_scales(&below)?.get(name).and_then(|v| v.as_float());
            let p = display_scale::Policy::from_ssot(&data)?;
            let pw = m["physicalWidth"].as_f64().unwrap_or(0.0);
            let ph = m["physicalHeight"].as_f64().unwrap_or(0.0);
            let d = display_scale::compute(
                &p,
                &display_scale::Input { width: w, height: h, phys_mm: Some((pw, ph)), dpi: None, transform: m["transform"].as_i64().unwrap_or(0), remote: false, explicit_scale: saved },
            );
            (d.scale, saved.is_some())
        }
        _ => match step_scale(w, h, cur, dir, &range) {
            Some(k) => (k as f64 / GRID as f64, true),
            None => return Ok(format!("{name}: scale {cur} is at the {} end of [{}, {}]", if dir == Dir::Up { "upper" } else { "lower" }, range.min, range.max)),
        },
    };
    let mode = format!("{w}x{h}@{:.3}", m["refreshRate"].as_f64().unwrap_or(60.0));
    let pos = format!("{}x{}", m["x"].as_i64().unwrap_or(0), m["y"].as_i64().unwrap_or(0));
    let mark = if explicit { format!("{scale}") } else { "nil".into() };
    eval(
        &format!(
            "MIOS_SCALE_EXPLICIT = MIOS_SCALE_EXPLICIT or {{}}; MIOS_SCALE_EXPLICIT['{name}'] = {mark}; hl.monitor({{output='{name}', mode='{mode}', position='{pos}', scale={scale}, transform={}}})",
            m["transform"].as_i64().unwrap_or(0)
        ),
        &[],
    )?;
    let user = mios_resolver::user_toml_path(Some(root));
    let before = std::fs::read_to_string(&user).unwrap_or_default();
    let after = user_tier_with_scale(&before, name, if dir == Dir::Reset { None } else { Some(scale) })?;
    save_user_tier(&user, &before, &after)?;
    Ok(format!("{name}: scale {cur} -> {scale}"))
}

#[cfg(test)]
mod tests {
    use super::*;

    const R: Range = Range { step: 0.125, min: 0.5, max: 3.0 };

    #[test]
    fn steps_are_whole_steps_on_valid_scales_inside_the_range() {
        // 3440x1440: 1.0 -> next valid >= 1.125
        let up = step_scale(3440, 1440, 1.0, Dir::Up, &R).unwrap();
        assert!(up >= 135 && valid(3440, 1440, up), "{up}");
        let down = step_scale(3440, 1440, 1.0, Dir::Down, &R).unwrap();
        assert!(down <= 105 && valid(3440, 1440, down), "{down}");
        assert_eq!(step_scale(1920, 1080, 1.0, Dir::Up, &R), Some(144), "1920x1080 1.0 -> 1.2, the first whole-logical scale >= 1.125");
        assert_eq!(step_scale(1920, 1080, 3.0, Dir::Up, &R), None, "upper end");
        assert_eq!(step_scale(1920, 1080, 0.5, Dir::Down, &R), None, "lower end");
        assert_eq!(step_scale(1920, 1080, 1.0, Dir::Reset, &R), None);
    }

    #[test]
    fn user_tier_scale_round_trip_keeps_every_other_byte() {
        let base = "# notes\n[identity]\nsecret = \"s\"\n";
        let with = user_tier_with_scale(base, "DP-3", Some(1.25)).unwrap();
        assert!(with.starts_with(base), "{with}");
        let v: Value = with.parse().unwrap();
        assert_eq!(v["desktop"]["hyprland"]["monitors"]["DP-3"]["scale"].as_float(), Some(1.25));
        let again = user_tier_with_scale(&with, "DP-3", Some(1.5)).unwrap();
        assert_eq!(again.parse::<Value>().unwrap()["desktop"]["hyprland"]["monitors"]["DP-3"]["scale"].as_float(), Some(1.5));
        assert_eq!(user_tier_with_scale(&again, "DP-3", None).unwrap().trim_end(), base.trim_end(), "reset removes the key and stub tables");
        let other = "[desktop.hyprland.monitors.DP-3]\nposition = \"0x0\"\nscale = 2.0\n";
        let kept = user_tier_with_scale(other, "DP-3", None).unwrap();
        assert!(kept.contains("position") && !kept.contains("scale"), "{kept}");
    }

    #[test]
    fn nested_target_matches_only_its_parent_window() {
        let d = tempfile::tempdir().unwrap();
        let s = d.path().join("mios-nested-desktop-42");
        std::fs::create_dir(&s).unwrap();
        std::fs::write(s.join("session.json"), r#"{"runtime":"/run/x","instance":{"instance":"sig-1"},"parent_window":"0xabc"}"#).unwrap();
        assert_eq!(nested_target(d.path(), "0xabc"), Some((PathBuf::from("/run/x"), "sig-1".into())));
        assert_eq!(nested_target(d.path(), "abc").map(|t| t.1), Some("sig-1".into()));
        assert_eq!(nested_target(d.path(), "0xdef"), None);
    }

    #[test]
    fn nested_runtime_is_recognised_only_with_its_descriptor() {
        let d = tempfile::tempdir().unwrap();
        let n = d.path().join("mios-nested-desktop-7");
        std::fs::create_dir(&n).unwrap();
        assert!(!is_nested_runtime(&n), "no descriptor yet");
        std::fs::write(n.join("session.json"), "{}").unwrap();
        assert!(is_nested_runtime(&n));
        let other = d.path().join("run-user");
        std::fs::create_dir(&other).unwrap();
        std::fs::write(other.join("session.json"), "{}").unwrap();
        assert!(!is_nested_runtime(&other));
    }

    #[test]
    fn direction_words_and_range_validation() {
        assert_eq!(Dir::parse("+").unwrap(), Dir::Up);
        assert!(Dir::parse("sideways").is_err());
        let ok: Value = "[desktop.scaling]\nsession_step=0.125\nsession_min=0.5\nsession_max=3.0".parse().unwrap();
        assert!(Range::from_ssot(&ok).is_ok());
        for bad in ["session_step=0\nsession_min=0.5\nsession_max=3", "session_step=0.1\nsession_min=4\nsession_max=3", "session_step=0.1\nsession_min=0.5"] {
            let v: Value = format!("[desktop.scaling]\n{bad}").parse().unwrap();
            assert!(Range::from_ssot(&v).is_err(), "{bad}");
        }
    }
}
