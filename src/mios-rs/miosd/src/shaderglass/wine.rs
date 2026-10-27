// AI-hint: miosd shaderglass wine: the per-user Wine prefix (created headless), the environment ShaderGlass runs with (driver, DXVK/wined3d and the GPU DXVK renders on, WineCap portal options) and the seeded default profile.
// AI-related: src/mios-rs/miosd/src/shaderglass.rs, usr/share/mios/mios.toml [desktop.shaderglass.wine] [desktop.shaderglass.profile] [desktop.shaderglass.capture]
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::{Duration, Instant};

use super::{Config, D3d, Driver, Gpu};

/// How long the first `wineboot -i` may take.
const WINEBOOT_TIMEOUT: Duration = Duration::from_secs(300);

pub fn data_home() -> PathBuf {
    std::env::var_os("XDG_DATA_HOME")
        .map(PathBuf::from)
        .filter(|p| p.is_absolute())
        .unwrap_or_else(|| super::home().join(".local/share"))
}

pub fn prefix_dir(cfg: &Config) -> PathBuf {
    data_home().join(&cfg.wine.prefix)
}

/// The environment of every Wine process for this prefix: (set, remove).
pub fn env(cfg: &Config, token_file: Option<&Path>) -> (Vec<(String, String)>, Vec<&'static str>) {
    let mut set = vec![
        ("WINEPREFIX".to_owned(), prefix_dir(cfg).display().to_string()),
        ("WINEDEBUG".to_owned(), cfg.wine.debug.clone()),
        ("MIOS_WINECAP_SOURCE_TYPES".to_owned(), cfg.capture.source_types.clone()),
        ("MIOS_WINECAP_CURSOR".to_owned(), cfg.capture.cursor.clone()),
        // Keep the Win32 client/swapchain aligned with the compositor-owned pane.
        ("MIOS_WINECAP_FIT_WINDOW".to_owned(), "1".to_owned()),
    ];
    let mut overrides = cfg.wine.dll_overrides.clone();
    if cfg.wine.d3d == D3d::Wined3d {
        // wine-dxvk installs DXVK through alternatives; builtin (wined3d) wins only by override.
        if !overrides.is_empty() {
            overrides.push(';');
        }
        overrides.push_str("d3d11,dxgi=b");
    }
    if !overrides.is_empty() {
        set.push(("WINEDLLOVERRIDES".to_owned(), overrides));
    }
    if let Some(t) = token_file {
        set.push(("MIOS_WINECAP_TOKEN_FILE".to_owned(), t.display().to_string()));
    }
    if let Some(v) = dxvk_filter(cfg, std::env::var("AQ_DRM_DEVICES").ok().as_deref(), Path::new("/sys/class/drm")) {
        set.push(("DXVK_FILTER_DEVICE_NAME".to_owned(), v.to_owned()));
    }
    // Wine prefers winex11 whenever DISPLAY is set; without it winewayland.drv takes the session.
    let remove = match cfg.wine.driver {
        Driver::X11 => vec![],
        Driver::Wayland => vec!["DISPLAY"],
    };
    (set, remove)
}

/// DXVK_FILTER_DEVICE_NAME for [desktop.shaderglass.wine].gpu = "compositor": the vendor word of the
/// compositor's primary DRM device (the first of AQ_DRM_DEVICES, which Hyprland's environment carries), so
/// DXVK renders where the frames are composited instead of copying every frame across PRIME from another
/// GPU. Derived at run time from sysfs (nothing about the hardware lives in the SSOT); None leaves DXVK's own
/// choice (gpu = "any", wined3d, no AQ_DRM_DEVICES, an unknown vendor).
pub fn dxvk_filter(cfg: &Config, aq_drm_devices: Option<&str>, sys_drm: &Path) -> Option<&'static str> {
    if cfg.wine.d3d != D3d::Dxvk || cfg.wine.gpu != Gpu::Compositor {
        return None;
    }
    gpu_vendor_word(aq_drm_devices?, sys_drm)
}

/// The Vulkan device-name word of the first DRM device in an AQ_DRM_DEVICES list (":"-separated; a
/// by-path entry has colons of its own, so entries split only before the next absolute path).
pub fn gpu_vendor_word(aq_drm_devices: &str, sys_drm: &Path) -> Option<&'static str> {
    let first = aq_drm_devices.split(":/").next()?.trim();
    if !first.starts_with('/') {
        return None;
    }
    let dev = std::fs::canonicalize(first).unwrap_or_else(|_| PathBuf::from(first));
    let card = dev.file_name()?.to_str()?;
    if !card.starts_with("card") {
        return None;
    }
    let vendor = std::fs::read_to_string(sys_drm.join(card).join("device/vendor")).ok()?;
    // Vulkan device names: "NVIDIA GeForce ..." (proprietary and NVK), "AMD Radeon ..." (RADV), "Intel(R) ..." (ANV).
    match vendor.trim().to_ascii_lowercase().as_str() {
        "0x10de" => Some("NVIDIA"),
        "0x1002" => Some("AMD"),
        "0x8086" => Some("Intel"),
        _ => None,
    }
}

/// Create the prefix once, with no display (so Wine's "updating configuration" window never appears).
pub fn ensure_prefix(cfg: &Config) -> Result<bool, String> {
    let prefix = prefix_dir(cfg);
    if prefix.join("system.reg").is_file() {
        return Ok(false);
    }
    if let Some(parent) = prefix.parent() {
        super::ensure_private_dir(parent)?;
    }
    super::ensure_private_dir(&prefix)?;
    let (set, _) = env(cfg, None);
    let mut child = Command::new(&cfg.wine.wineboot)
        .arg("-i")
        .envs(set)
        .env_remove("DISPLAY")
        .env_remove("WAYLAND_DISPLAY")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("{}: {e} (install [packages.shaderglass])", cfg.wine.wineboot))?;
    let started = Instant::now();
    loop {
        if let Some(status) = child.try_wait().map_err(|e| e.to_string())? {
            if !status.success() {
                return Err(format!("wineboot -i failed: {status}"));
            }
            break;
        }
        if started.elapsed() > WINEBOOT_TIMEOUT {
            let _ = child.kill();
            return Err("wineboot -i timed out".into());
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    // wineboot returns while wineserver still holds the registry in memory; it writes system.reg when it
    // exits. Wait for that (wineserver -w, the sibling of the SSOT's wineboot) before checking the prefix.
    let wineserver = Path::new(&cfg.wine.wineboot).with_file_name("wineserver");
    let (set, _) = env(cfg, None);
    let mut server = Command::new(&wineserver)
        .arg("-w")
        .envs(set)
        .env_remove("DISPLAY")
        .env_remove("WAYLAND_DISPLAY")
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(Stdio::inherit())
        .spawn()
        .map_err(|e| format!("{}: {e} (install [packages.shaderglass])", wineserver.display()))?;
    loop {
        if server.try_wait().map_err(|e| e.to_string())?.is_some() {
            break;
        }
        if started.elapsed() > WINEBOOT_TIMEOUT {
            let _ = server.kill();
            return Err("wineserver -w timed out after wineboot -i".into());
        }
        std::thread::sleep(Duration::from_millis(200));
    }
    if !prefix.join("system.reg").is_file() {
        return Err(format!("{}: wineboot left no system.reg", prefix.display()));
    }
    Ok(true)
}

/// ShaderGlass' profile syntax: `Key "value"` per line (std::quoted: backslash and quote escaped).
pub fn profile_text(profile: &[(String, String)]) -> String {
    let mut out = String::new();
    for (k, v) in profile {
        let q: String = v
            .chars()
            .flat_map(|c| if c == '"' || c == '\\' { vec!['\\', c] } else { vec![c] })
            .collect();
        out += &format!("{k} \"{q}\"\r\n");
    }
    out
}

/// %LOCALAPPDATA% of the prefix user: drive_c/users/<user>/AppData/Local (the user Wine created).
pub fn local_appdata(prefix: &Path) -> Option<PathBuf> {
    let users = prefix.join("drive_c/users");
    let mine = std::env::var("USER").ok().filter(|u| !u.is_empty());
    if let Some(u) = mine {
        if users.join(&u).is_dir() {
            return Some(users.join(u).join("AppData/Local"));
        }
    }
    std::fs::read_dir(&users)
        .ok()?
        .flatten()
        .map(|e| e.path())
        .find(|p| p.is_dir() && p.file_name().is_some_and(|n| n != "Public"))
        .map(|p| p.join("AppData/Local"))
}

/// Seed ShaderGlassDefault.sgp when the prefix has none; never overwrite the operator's "Set as default".
pub fn seed_profile(cfg: &Config) -> Result<Option<PathBuf>, String> {
    let Some(dir) = local_appdata(&prefix_dir(cfg)) else {
        return Err("the Wine prefix has no user profile directory".into());
    };
    let path = dir.join("ShaderGlassDefault.sgp");
    if path.exists() {
        let existing = std::fs::read_to_string(&path).unwrap_or_default();
        let legacy_seed = existing.contains("ShaderName \"crt-geom\"")
            && !existing.contains("Param-1-interlace_detect");
        if !legacy_seed {
            return Ok(None);
        }
    }
    std::fs::create_dir_all(&dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    super::write_private(&path, profile_text(&cfg.profile).as_bytes())?;
    Ok(Some(path))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn profile_lines_are_quoted_like_std_quoted() {
        let p = vec![("ShaderName".to_owned(), "crt-geom".to_owned()), ("X".to_owned(), "a\"b\\c".to_owned())];
        assert_eq!(profile_text(&p), "ShaderName \"crt-geom\"\r\nX \"a\\\"b\\\\c\"\r\n");
    }

    #[test]
    fn gpu_vendor_word_follows_the_compositors_first_drm_device() {
        let d = tempfile::tempdir().unwrap();
        let sys = d.path().join("sys");
        for (card, vendor) in [("card1", "0x1002\n"), ("card2", "0x10de\n"), ("card3", "0x1234\n")] {
            std::fs::create_dir_all(sys.join(card).join("device")).unwrap();
            std::fs::write(sys.join(card).join("device/vendor"), vendor).unwrap();
        }
        assert_eq!(gpu_vendor_word("/dev/dri/card2:/dev/dri/card1", &sys), Some("NVIDIA"));
        assert_eq!(gpu_vendor_word("/dev/dri/card1:/dev/dri/card2", &sys), Some("AMD"));
        assert_eq!(gpu_vendor_word("/dev/dri/card3", &sys), None, "unknown vendor: DXVK chooses");
        assert_eq!(gpu_vendor_word("/dev/dri/card9", &sys), None, "no sysfs node");
        assert_eq!(gpu_vendor_word("", &sys), None);
        assert_eq!(gpu_vendor_word("card2", &sys), None, "not a device path");
        // A by-path symlink (its name has colons) resolves to its card node.
        let dev = d.path().join("dev");
        std::fs::create_dir_all(dev.join("by-path")).unwrap();
        std::fs::write(dev.join("card2"), b"").unwrap();
        let link = dev.join("by-path/pci-0000:01:00.0-card");
        std::os::unix::fs::symlink(dev.join("card2"), &link).unwrap();
        assert_eq!(gpu_vendor_word(&format!("{}:/dev/dri/card1", link.display()), &sys), Some("NVIDIA"));
    }

    #[test]
    fn local_appdata_prefers_the_session_user_then_any_real_user() {
        let d = tempfile::tempdir().unwrap();
        std::fs::create_dir_all(d.path().join("drive_c/users/Public")).unwrap();
        assert!(local_appdata(d.path()).is_none(), "Public is not a user");
        std::fs::create_dir_all(d.path().join("drive_c/users/someone")).unwrap();
        assert_eq!(local_appdata(d.path()).unwrap(), d.path().join("drive_c/users/someone/AppData/Local"));
    }
}
