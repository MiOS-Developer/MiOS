// AI-hint: Main entry point for mios-wallpaperd wallpaper daemon on Windows.
#![windows_subsystem = "windows"] // no console window, ever.

#[cfg(windows)]
mod guiwatch {
    use std::collections::HashSet;
    use std::time::Duration;
    use windows::core::PWSTR;
    use windows::Win32::Foundation::{CloseHandle, BOOL, HWND, LPARAM, RECT, TRUE};
    use windows::Win32::System::Threading::{
        OpenProcess, QueryFullProcessImageNameW, PROCESS_NAME_FORMAT,
        PROCESS_QUERY_LIMITED_INFORMATION,
    };
    use windows::Win32::UI::WindowsAndMessaging::{
        EnumWindows, GetSystemMetrics, GetWindowRect, GetWindowThreadProcessId, IsIconic,
        IsWindowVisible, SetWindowPos, SM_CXSCREEN, SM_CYSCREEN, SWP_NOACTIVATE, SWP_NOZORDER,
    };

    const MIN_W: i32 = 1600;
    const MIN_H: i32 = 1000;

    pub fn run_forever() {
        let mut adopted: HashSet<isize> = HashSet::new();
        loop {
            unsafe {
                let _ = EnumWindows(Some(enum_proc), LPARAM(&mut adopted as *mut _ as isize));
            }
            std::thread::sleep(Duration::from_millis(500));
        }
    }

    unsafe extern "system" fn enum_proc(hwnd: HWND, lparam: LPARAM) -> BOOL {
        let adopted = &mut *(lparam.0 as *mut HashSet<isize>);
        let key = hwnd.0 as isize;
        if key == 0 || adopted.contains(&key) {
            return TRUE;
        }
        if !IsWindowVisible(hwnd).as_bool() || IsIconic(hwnd).as_bool() {
            return TRUE;
        }
        if !is_msrdc(hwnd) {
            return TRUE;
        }
        let mut r = RECT::default();
        if GetWindowRect(hwnd, &mut r).is_err() {
            return TRUE;
        }
        let (w, h) = (r.right - r.left, r.bottom - r.top);
        if w >= MIN_W && h >= MIN_H {
            return TRUE; // already usable -- don't touch
        }
        let (nw, nh) = (w.max(MIN_W), h.max(MIN_H));
        let (sw, sh) = (GetSystemMetrics(SM_CXSCREEN), GetSystemMetrics(SM_CYSCREEN));
        let (x, y) = (((sw - nw) / 2).max(0), ((sh - nh) / 2).max(0));
        let _ = SetWindowPos(
            hwnd,
            HWND::default(),
            x,
            y,
            nw,
            nh,
            SWP_NOZORDER | SWP_NOACTIVATE,
        );
        adopted.insert(key);
        TRUE
    }

    fn is_msrdc(hwnd: HWND) -> bool {
        unsafe {
            let mut pid = 0u32;
            GetWindowThreadProcessId(hwnd, Some(&mut pid));
            if pid == 0 {
                return false;
            }
            let h = match OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) {
                Ok(h) => h,
                Err(_) => return false,
            };
            let mut buf = [0u16; 260];
            let mut len = buf.len() as u32;
            let ok = QueryFullProcessImageNameW(
                h,
                PROCESS_NAME_FORMAT(0),
                PWSTR(buf.as_mut_ptr()),
                &mut len,
            )
            .is_ok();
            let _ = CloseHandle(h);
            if !ok {
                return false;
            }
            String::from_utf16_lossy(&buf[..len as usize])
                .to_lowercase()
                .ends_with("msrdc.exe")
        }
    }
}
#[cfg(windows)]
mod host {
    // The wallpaper host (runs in the interactive session): a borderless WebView sized to the virtual
    // screen, SetParent'd onto the WorkerW so it renders behind the desktop icons. Reads the SSOT
    // WallpaperUrl from the registry; folds the WSLg gui-watch in as a background thread (no pwsh, no
    // console flash). Built #![windows_subsystem="windows"] -> never surfaces a taskbar/console window.
    use tao::dpi::{PhysicalPosition, PhysicalSize};
    use tao::event_loop::{ControlFlow, EventLoopBuilder};
    use tao::platform::windows::{WindowBuilderExtWindows, WindowExtWindows};
    use tao::window::WindowBuilder;
    use windows::Win32::Foundation::HWND;
    use windows::Win32::UI::WindowsAndMessaging::{
        GetSystemMetrics, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN, SM_XVIRTUALSCREEN,
        SM_YVIRTUALSCREEN,
    };
    use wry::WebViewBuilder;

    pub fn run_host() {
        // Folded WSLg window-centering daemon (was mios-gui-watch.ps1) -- one process, no separate pwsh.
        std::thread::spawn(super::guiwatch::run_forever);

        let url = read_wallpaper_url();

        let event_loop = EventLoopBuilder::new().build();
        let (vx, vy, vw, vh) = unsafe {
            (
                GetSystemMetrics(SM_XVIRTUALSCREEN),
                GetSystemMetrics(SM_YVIRTUALSCREEN),
                GetSystemMetrics(SM_CXVIRTUALSCREEN),
                GetSystemMetrics(SM_CYVIRTUALSCREEN),
            )
        };
        let window = WindowBuilder::new()
            .with_decorations(false)
            .with_skip_taskbar(true)
            .with_inner_size(PhysicalSize::new(vw.max(1) as u32, vh.max(1) as u32))
            .build(&event_loop)
            .expect("create wallpaper window");
        window.set_outer_position(PhysicalPosition::new(vx, vy));

        // Attach onto the WorkerW so the WebView composites as the wallpaper (behind the icons).
        let hwnd = HWND(window.hwnd() as _);
        if let Some(workerw) = super::workerw::find_wallpaper_workerw() {
            super::workerw::attach(hwnd, workerw);
        }

        // Build the WebView on the (now re-parented) window.
        let _webview = WebViewBuilder::new(&window)
            .with_url(&url)
            .build()
            .expect("create webview");

        event_loop.run(move |event, _target, control_flow| {
            *control_flow = ControlFlow::Wait;
            // Keep the window + webview alive for the process lifetime; the service restarts us on exit.
            let _ = (&event, &window, &_webview);
        });
    }

    fn read_wallpaper_url() -> String {
        if let Ok(data) = mios_resolver::resolve_merged(None, false) {
            if let Ok(url) = super::wallpaper_url(&data, true) {
                return url;
            }
        }
        crate::util::reg_read_sz(super::WALLPAPER_URL_KEY, super::WALLPAPER_URL_VALUE)
            .filter(|s| !s.is_empty())
            .unwrap_or_else(|| super::WALLPAPER_URL_FALLBACK.to_string())
    }
}
#[cfg(windows)]
mod workerw {
    // WorkerW attach -- place a window BEHIND the desktop icons (the live-wallpaper layer). Standard
    // Explorer technique: ask Progman (message 0x052C) to spawn the WorkerW sublayer, then find the
    // WorkerW that hosts the wallpaper and SetParent our window onto it. Mirrors the proven C# host.
    use windows::core::{w, PCWSTR};
    use windows::Win32::Foundation::{BOOL, FALSE, HWND, LPARAM, TRUE, WPARAM};
    use windows::Win32::UI::WindowsAndMessaging::{
        EnumWindows, FindWindowExW, FindWindowW, SendMessageTimeoutW, SetParent, SMTO_NORMAL,
    };

    pub fn find_wallpaper_workerw() -> Option<HWND> {
        unsafe {
            let progman = FindWindowW(w!("Progman"), PCWSTR::null()).ok()?;
            // Tell Progman to create the WorkerW behind the icon layer.
            let mut _res: usize = 0;
            let _ = SendMessageTimeoutW(
                progman,
                0x052C,
                WPARAM(0),
                LPARAM(0),
                SMTO_NORMAL,
                1000,
                Some(&mut _res as *mut usize),
            );

            // Newer / modified shells: a WorkerW directly under Progman.
            if let Ok(w) = FindWindowExW(progman, HWND::default(), w!("WorkerW"), PCWSTR::null()) {
                if !w.0.is_null() {
                    return Some(w);
                }
            }

            // Else: enumerate top-level windows for the WorkerW that owns SHELLDLL_DefView (the icon
            // layer); the wallpaper WorkerW is the sibling WorkerW found after it.
            let mut found = Found { hwnd: None };
            let _ = EnumWindows(Some(enum_proc), LPARAM(&mut found as *mut _ as isize));
            found.hwnd
        }
    }

    struct Found {
        hwnd: Option<HWND>,
    }

    unsafe extern "system" fn enum_proc(top: HWND, lparam: LPARAM) -> BOOL {
        if let Ok(dv) = FindWindowExW(top, HWND::default(), w!("SHELLDLL_DefView"), PCWSTR::null())
        {
            if !dv.0.is_null() {
                if let Ok(wp) = FindWindowExW(HWND::default(), top, w!("WorkerW"), PCWSTR::null()) {
                    if !wp.0.is_null() {
                        (*(lparam.0 as *mut Found)).hwnd = Some(wp);
                        return FALSE; // stop enumeration
                    }
                }
            }
        }
        TRUE
    }

    pub fn attach(child: HWND, parent: HWND) {
        unsafe {
            let _ = SetParent(child, parent);
        }
    }
}

#[cfg(windows)]
use std::ffi::OsString;
#[cfg(windows)]
use std::time::Duration;

pub const SERVICE_NAME: &str = "MiOS-Wallpaper-Service";
pub const HOST_ARG: &str = "host";
pub const GUIWATCH_ARG: &str = "gui-watch";
/// SSOT-derived wallpaper URL, written by Set-MiOSWallpaper.ps1 (a0..a15 palette, mode-less for live
/// theme sync). Read at host start; the host also watches it for change and reloads.
pub const WALLPAPER_URL_KEY: &str = r"SOFTWARE\MiOS";
pub const WALLPAPER_URL_VALUE: &str = "WallpaperUrl";
pub const WALLPAPER_URL_FALLBACK: &str = "file:///C:/Windows/Web/MiOS/living-wallpaper.html";

#[derive(Debug, Clone)]
pub struct WallpaperConfig {
    pub html_path: String,
    pub framerate: u32,
}

impl WallpaperConfig {
    pub fn default_config() -> Self {
        let html_path = mios_service_core::ssot::require_str("theme.wallpaper.html_path")
            .unwrap_or_else(|_| "/usr/share/mios/branding/living-wallpaper.html".to_string());
        let framerate = mios_service_core::ssot::require_port("theme.wallpaper.framerate")
            .map(|p| p as u32)
            .unwrap_or(30);
        Self {
            html_path,
            framerate,
        }
    }
}

#[cfg(windows)]
fn main() {
    let arg = std::env::args().nth(1).unwrap_or_default();
    match arg.as_str() {
        HOST_ARG => host::run_host(),
        GUIWATCH_ARG => guiwatch::run_forever(),
        // No arg (SCM launch) or explicit "service": become the Windows service.
        _ => {
            if let Err(_e) = service::run() {
                // If the SCM isn't driving us (e.g. run interactively for a smoke test), fall back to
                // hosting directly so a developer still sees the wallpaper.
                host::run_host();
            }
        }
    }
}

// AI-hint: Both native hosts use the same SSOT-colored HTML; Linux supplies a Wayland background surface.
fn file_url(path: &str) -> String {
    let path = path.replace('\\', "/");
    let mut encoded = String::new();
    for byte in path.bytes() {
        if byte.is_ascii_alphanumeric() || b"/:-._~".contains(&byte) {
            encoded.push(byte as char);
        } else {
            encoded.push_str(&format!("%{byte:02X}"));
        }
    }
    if encoded.starts_with('/') {
        format!("file://{encoded}")
    } else {
        format!("file:///{encoded}")
    }
}

fn wallpaper_url(data: &toml::Value, windows: bool) -> Result<String, String> {
    let config = &data["theme"]["wallpaper"];
    let path_key = if windows {
        "windows_html_path"
    } else {
        "html_path"
    };
    let path = config
        .get(path_key)
        .and_then(toml::Value::as_str)
        .filter(|s| !s.is_empty())
        .ok_or("Wallpaper HTML path is missing")?;
    let palette = data
        .get("colors")
        .and_then(toml::Value::as_table)
        .ok_or("Wallpaper palette is missing")?;
    let hex = |value: &toml::Value| -> Result<String, String> {
        value
            .as_str()
            .and_then(|s| s.strip_prefix('#'))
            .filter(|h| h.len() == 6 && h.bytes().all(|c| c.is_ascii_hexdigit()))
            .map(str::to_owned)
            .ok_or("Wallpaper colors must be #RRGGBB".into())
    };
    let mut query = Vec::new();
    for slot in 0..16 {
        let prefix = format!("ansi_{slot}_");
        let matches = palette
            .iter()
            .filter(|(key, _)| key.starts_with(&prefix))
            .collect::<Vec<_>>();
        if matches.len() != 1 {
            return Err(format!("Wallpaper needs exactly one ANSI slot {slot}"));
        }
        query.push(format!("a{slot}={}", hex(matches[0].1)?));
    }
    for role in ["bg", "fg"] {
        query.push(format!(
            "{role}={}",
            hex(palette.get(role).ok_or("Missing wallpaper anchor color")?)?
        ));
    }
    let mode = data["theme"]["mode"]
        .as_str()
        .filter(|m| matches!(*m, "dark" | "light"))
        .ok_or("Wallpaper mode must be dark or light")?;
    query.push(format!("mode={mode}"));
    query.extend(motion_query(config)?);
    Ok(format!("{}?{}", file_url(path), query.join("&")))
}

/// The living-wallpaper motion contract shared by the Windows HTML and the native Linux host: the same URL
/// query keys on both platforms, from [theme.wallpaper] framerate, [theme.wallpaper.motion] and
/// [theme.wallpaper.native] (the HTML ignores the native-only `nsim`).
fn motion_query(config: &toml::Value) -> Result<Vec<String>, String> {
    let num = |table: &toml::Value, key: &str, lo: f64, hi: f64| -> Result<f64, String> {
        let v = table
            .get(key)
            .and_then(|v| v.as_float().or_else(|| v.as_integer().map(|i| i as f64)))
            .ok_or(format!("Wallpaper motion key {key} is missing or not a number"))?;
        if v.is_finite() && (lo..=hi).contains(&v) {
            Ok(v)
        } else {
            Err(format!("Wallpaper motion key {key} = {v} is outside {lo}..={hi}"))
        }
    };
    let motion = config
        .get("motion")
        .ok_or("Wallpaper [theme.wallpaper.motion] is missing")?;
    let native = config
        .get("native")
        .ok_or("Wallpaper [theme.wallpaper.native] is missing")?;
    let fmt = |v: f64| {
        let s = format!("{v:.6}");
        s.trim_end_matches('0').trim_end_matches('.').to_string()
    };
    let mut q = vec![
        format!("fps={}", fmt(num(config, "framerate", 1.0, 240.0)?)),
        format!("speed={}", fmt(num(motion, "speed", 0.0, 10.0)?)),
        format!("intro={}", fmt(num(motion, "intro_ms", 0.0, 60000.0)?)),
        format!("dpr={}", fmt(num(motion, "dpr_cap", 0.25, 8.0)?)),
        format!("sim={}", fmt(num(motion, "sim_scale", 0.05, 2.0)?)),
        // The native SIM shader unrolls a constant 24-emitter loop (as the HTML compiles it), so 24 is the cap.
        format!("n={}", fmt(num(motion, "emitters", 1.0, 24.0)?.round())),
        format!("nsim={}", fmt(num(native, "sim_scale", 0.02, 2.0)?)),
    ];
    let weights = motion
        .get("weights")
        .and_then(toml::Value::as_array)
        .filter(|w| w.len() == 16)
        .ok_or("Wallpaper motion weights must list 16 role weights (ansi_0..ansi_15)")?;
    for (slot, w) in weights.iter().enumerate() {
        let w = w
            .as_float()
            .or_else(|| w.as_integer().map(|i| i as f64))
            .filter(|w| (0.0..=1.0).contains(w))
            .ok_or(format!("Wallpaper weight w{slot} must be a number in 0..=1"))?;
        q.push(format!("w{slot}={}", fmt(w)));
    }
    Ok(q)
}

/// Linux host for the configured renderer: "native" (GPU shaders, Host.qml) or "web" (QtWebEngine, WebHost.qml).
fn linux_host(config: &toml::Value) -> Result<&str, String> {
    let key = match config.get("renderer").and_then(toml::Value::as_str) {
        Some("native") => "linux_host",
        Some("web") => "linux_web_host",
        other => return Err(format!("Wallpaper renderer must be \"native\" or \"web\", not {other:?}")),
    };
    config
        .get(key)
        .and_then(toml::Value::as_str)
        .filter(|s| !s.is_empty())
        .ok_or(format!("Missing Linux wallpaper {key}"))
}

#[cfg(target_os = "linux")]
fn check_wayland_socket(runtime: &std::path::Path) -> bool {
    use std::os::unix::net::UnixStream;
    let display_names: Vec<String> = if let Ok(val) = std::env::var("WAYLAND_DISPLAY") {
        if !val.trim().is_empty() {
            vec![val.trim().to_string()]
        } else {
            vec!["wayland-1".to_string(), "wayland-0".to_string()]
        }
    } else {
        vec!["wayland-1".to_string(), "wayland-0".to_string()]
    };

    for name in display_names {
        let sock_path = runtime.join(&name);
        if sock_path.exists() && UnixStream::connect(&sock_path).is_ok() {
            return true;
        }
    }
    false
}

#[cfg(target_os = "linux")]
fn wait_for_wayland(runtime: &std::path::Path, timeout: std::time::Duration) -> bool {
    let start = std::time::Instant::now();
    while start.elapsed() < timeout {
        if check_wayland_socket(runtime) {
            return true;
        }
        std::thread::sleep(std::time::Duration::from_millis(250));
    }
    check_wayland_socket(runtime)
}

#[cfg(target_os = "linux")]
fn linux_main() -> Result<(), String> {
    use std::{
        env,
        path::Path,
        process::Command,
        time::{Duration, Instant},
    };
    let data = mios_resolver::resolve_merged(None, false).map_err(|e| e.to_string())?;
    let url = wallpaper_url(&data, false)?;
    match env::args().nth(1).as_deref() {
        Some("--url") => {
            println!("{url}");
            return Ok(());
        }
        None | Some("host") => {}
        _ => return Err("Usage: mios-wallpaperd [host | --url]".into()),
    }
    if data["branding"]["living_wallpaper"].as_bool() != Some(true) {
        return Ok(());
    }
    let runtime = env::var_os("XDG_RUNTIME_DIR").ok_or("A Wayland login session is required")?;
    let runtime_path = Path::new(&runtime);
    let config = &data["theme"]["wallpaper"];
    let runner = config["linux_runner"]
        .as_str()
        .ok_or("Missing Linux wallpaper runner")?;
    let host = linux_host(config)?;
    if !Path::new(host).is_file() {
        return Err("Linux wallpaper host is not installed".into());
    }

    println!("[mios-wallpaperd] Starting wallpaper supervisor daemon for {host}...");
    let mut backoff_secs = 2u64;
    let min_backoff = 2u64;
    let max_backoff = 30u64;
    let stability_threshold = Duration::from_secs(60);

    loop {
        // 1. Wayland socket readiness check
        if !wait_for_wayland(runtime_path, Duration::from_secs(5)) {
            eprintln!(
                "[mios-wallpaperd] Wayland display socket not ready; waiting {backoff_secs}s before retry..."
            );
            std::thread::sleep(Duration::from_secs(backoff_secs));
            backoff_secs = (backoff_secs * 2).min(max_backoff);
            continue;
        }

        println!("[mios-wallpaperd] Spawning wallpaper host process under flock...");
        let start_time = Instant::now();
        let mut child = match Command::new("flock")
            .args(["--nonblock", "--close"])
            .arg(runtime_path.join("mios-wallpaper.lock"))
            .arg(runner)
            .arg(host)
            .arg("--")
            .arg(&url)
            .env("QT_QPA_PLATFORM", "wayland")
            .env("QT_WAYLAND_SHELL_INTEGRATION", "layer-shell")
            .env_remove("TMUX")
            .env_remove("TMUX_PANE")
            .spawn()
        {
            Ok(c) => c,
            Err(e) => {
                eprintln!("[mios-wallpaperd] Failed to spawn wallpaper process: {e}");
                std::thread::sleep(Duration::from_secs(backoff_secs));
                backoff_secs = (backoff_secs * 2).min(max_backoff);
                continue;
            }
        };

        let pid = child.id();
        println!("[mios-wallpaperd] Wallpaper process running (PID {pid})");

        let mut exit_status = None;
        loop {
            match child.try_wait() {
                Ok(Some(status)) => {
                    exit_status = Some(status);
                    break;
                }
                Ok(None) => {
                    if start_time.elapsed() >= stability_threshold && backoff_secs > min_backoff {
                        println!(
                            "[mios-wallpaperd] Process ran stably for {:?}; resetting backoff to {min_backoff}s.",
                            start_time.elapsed()
                        );
                        backoff_secs = min_backoff;
                    }
                    std::thread::sleep(Duration::from_millis(500));
                }
                Err(e) => {
                    eprintln!("[mios-wallpaperd] Error waiting on child PID {pid}: {e}");
                    break;
                }
            }
        }

        let elapsed = start_time.elapsed();
        if let Some(status) = exit_status {
            if elapsed >= stability_threshold {
                backoff_secs = min_backoff;
            }

            if status.success() {
                println!(
                    "[mios-wallpaperd] Child PID {pid} exited normally (code 0) after {:?}.",
                    elapsed
                );
                std::thread::sleep(Duration::from_secs(min_backoff));
            } else {
                eprintln!(
                    "[mios-wallpaperd] Child PID {pid} exited with {:?} (uptime: {:?}). Applying backoff {backoff_secs}s before restart...",
                    status, elapsed
                );
                std::thread::sleep(Duration::from_secs(backoff_secs));
                backoff_secs = (backoff_secs * 2).min(max_backoff);
            }
        }
    }
}
#[cfg(target_os = "linux")]
fn main() {
    if let Err(error) = linux_main() {
        eprintln!("mios-wallpaperd: {error}");
        std::process::exit(1);
    }
}
#[cfg(not(any(windows, target_os = "linux")))]
fn main() {
    eprintln!("This platform has no native wallpaper host.");
}

#[cfg(test)]
mod shared_wallpaper_tests {
    use super::*;
    fn fixture() -> toml::Value {
        let mut colors = toml::map::Map::new();
        for slot in 0..16 {
            colors.insert(
                format!("ansi_{slot}_slot"),
                toml::Value::String(format!("#{slot:06X}")),
            );
        }
        colors.insert("bg".into(), toml::Value::String("#123456".into()));
        colors.insert("fg".into(), toml::Value::String("#ABCDEF".into()));
        let mut doc:toml::Value=toml::from_str("[theme]\nmode='dark'\n[theme.wallpaper]\nhtml_path='/usr/share/MiOS Wallpaper/living.html'\nwindows_html_path='C:/Windows/Web/MiOS/living.html'\nrenderer='native'\nlinux_host='/h/Host.qml'\nlinux_web_host='/h/WebHost.qml'\nframerate=30\n[theme.wallpaper.motion]\nspeed=0.4\nintro_ms=3500\ndpr_cap=1.5\nsim_scale=0.6\nemitters=24\nweights=[1.0,0.3,0.6,0.55,0.85,0.35,0.7,0.95,0.55,0.22,0.3,0.28,0.45,0.22,0.55,0.18]\n[theme.wallpaper.native]\nsim_scale=0.25\n").unwrap();
        doc.as_table_mut()
            .unwrap()
            .insert("colors".into(), toml::Value::Table(colors));
        doc
    }
    #[test]
    fn both_platforms_project_every_ansi_slot_and_share_query() {
        let doc = fixture();
        let linux = wallpaper_url(&doc, false).unwrap();
        let windows = wallpaper_url(&doc, true).unwrap();
        assert!(linux.starts_with("file:///usr/share/MiOS%20Wallpaper/"));
        assert!(windows.starts_with("file:///C:/Windows/"));
        assert_eq!(
            linux.split_once('?').unwrap().1,
            windows.split_once('?').unwrap().1
        );
        for slot in 0..16 {
            assert!(linux.contains(&format!("a{slot}={slot:06X}")));
        }
    }
    #[test]
    fn motion_contract_is_shared_and_validated() {
        let doc = fixture();
        let linux = wallpaper_url(&doc, false).unwrap();
        let windows = wallpaper_url(&doc, true).unwrap();
        for key in [
            "fps=30", "speed=0.4", "intro=3500", "dpr=1.5", "sim=0.6", "n=24", "nsim=0.25", "w0=1",
            "w7=0.95", "w15=0.18",
        ] {
            assert!(linux.contains(&format!("&{key}")), "{key} missing from {linux}");
            assert!(windows.contains(&format!("&{key}")), "{key} missing from {windows}");
        }
        // Negative controls: a missing table, a short weight list, an out-of-range value, too many emitters.
        let mut doc = fixture();
        doc["theme"]["wallpaper"].as_table_mut().unwrap().remove("motion");
        assert!(wallpaper_url(&doc, false).unwrap_err().contains("motion"));
        let mut doc = fixture();
        doc["theme"]["wallpaper"]["motion"]["weights"] = toml::Value::Array(vec![toml::Value::Float(0.5); 15]);
        assert!(wallpaper_url(&doc, false).unwrap_err().contains("16"));
        let mut doc = fixture();
        doc["theme"]["wallpaper"]["motion"]["speed"] = toml::Value::Float(-1.0);
        assert!(wallpaper_url(&doc, false).unwrap_err().contains("speed"));
        let mut doc = fixture();
        doc["theme"]["wallpaper"]["motion"]["emitters"] = toml::Value::Integer(32);
        assert!(wallpaper_url(&doc, false).unwrap_err().contains("emitters"));
    }
    #[test]
    fn renderer_selects_the_native_or_web_host() {
        let mut doc = fixture();
        assert_eq!(linux_host(&doc["theme"]["wallpaper"]).unwrap(), "/h/Host.qml");
        doc["theme"]["wallpaper"]["renderer"] = toml::Value::String("web".into());
        assert_eq!(linux_host(&doc["theme"]["wallpaper"]).unwrap(), "/h/WebHost.qml");
        doc["theme"]["wallpaper"]["renderer"] = toml::Value::String("chromium".into());
        assert!(linux_host(&doc["theme"]["wallpaper"]).is_err());
        doc["theme"]["wallpaper"].as_table_mut().unwrap().remove("renderer");
        assert!(linux_host(&doc["theme"]["wallpaper"]).is_err());
    }
    #[test]
    fn malformed_or_duplicate_slots_are_rejected() {
        let mut doc = fixture();
        doc["colors"]["ansi_4_slot"] = toml::Value::String("#bad".into());
        assert!(wallpaper_url(&doc, false).is_err());
        let mut doc = fixture();
        doc["colors"]
            .as_table_mut()
            .unwrap()
            .insert("ansi_4_other".into(), toml::Value::String("#123456".into()));
        assert!(wallpaper_url(&doc, false).unwrap_err().contains("slot 4"));
    }
}

/// Windows-service controller (session 0). It cannot draw on the user's desktop itself, so it keeps a
/// "host" child alive in the active interactive session and relaunches it on exit or session change.
#[cfg(windows)]
mod service {
    use super::*;
    use windows_service::service::{
        ServiceControl, ServiceControlAccept, ServiceExitCode, ServiceState, ServiceStatus,
        ServiceType,
    };
    use windows_service::service_control_handler::{self, ServiceControlHandlerResult};
    use windows_service::service_dispatcher;

    pub fn run() -> windows_service::Result<()> {
        service_dispatcher::start(SERVICE_NAME, ffi_service_main)
    }

    windows_service::define_windows_service!(ffi_service_main, service_main);

    fn service_main(_args: Vec<OsString>) {
        let (shutdown_tx, shutdown_rx) = std::sync::mpsc::channel();
        let handler = move |control| -> ServiceControlHandlerResult {
            match control {
                ServiceControl::Stop | ServiceControl::Shutdown => {
                    let _ = shutdown_tx.send(());
                    ServiceControlHandlerResult::NoError
                }
                ServiceControl::SessionChange(_) => ServiceControlHandlerResult::NoError,
                ServiceControl::Interrogate => ServiceControlHandlerResult::NoError,
                _ => ServiceControlHandlerResult::NotImplemented,
            }
        };
        let status_handle = match service_control_handler::register(SERVICE_NAME, handler) {
            Ok(h) => h,
            Err(_) => return,
        };
        let running = ServiceStatus {
            service_type: ServiceType::OWN_PROCESS,
            current_state: ServiceState::Running,
            controls_accepted: ServiceControlAccept::STOP | ServiceControlAccept::SHUTDOWN,
            exit_code: ServiceExitCode::Win32(0),
            checkpoint: 0,
            wait_hint: Duration::default(),
            process_id: None,
        };
        let _ = status_handle.set_service_status(running.clone());

        // Supervise a host child in the active user session until asked to stop.
        loop {
            if shutdown_rx.recv_timeout(Duration::from_secs(3)).is_ok() {
                break;
            }
            super::session::ensure_host_running();
        }

        super::session::kill_host();
        let _ = status_handle.set_service_status(ServiceStatus {
            current_state: ServiceState::Stopped,
            ..running
        });
    }
}

/// Launch/track the "host" child inside the active interactive session (CreateProcessAsUser with the
/// console-session token) so the wallpaper renders on the real desktop, not session 0.
#[cfg(windows)]
mod session {
    use std::sync::atomic::{AtomicU32, Ordering};
    use windows::Win32::Foundation::{CloseHandle, HANDLE};
    use windows::Win32::System::RemoteDesktop::{WTSGetActiveConsoleSessionId, WTSQueryUserToken};
    use windows::Win32::System::Threading::{
        CreateProcessAsUserW, OpenProcess, TerminateProcess, PROCESS_INFORMATION,
        PROCESS_TERMINATE, STARTUPINFOW,
    };

    static HOST_PID: AtomicU32 = AtomicU32::new(0);

    pub fn ensure_host_running() {
        let pid = HOST_PID.load(Ordering::SeqCst);
        if pid != 0 && super::util::process_alive(pid) {
            return;
        }
        if let Some(new_pid) = spawn_host_in_session() {
            HOST_PID.store(new_pid, Ordering::SeqCst);
        }
    }

    pub fn kill_host() {
        let pid = HOST_PID.swap(0, Ordering::SeqCst);
        if pid == 0 {
            return;
        }
        unsafe {
            if let Ok(h) = OpenProcess(PROCESS_TERMINATE, false, pid) {
                let _ = TerminateProcess(h, 0);
                let _ = CloseHandle(h);
            }
        }
    }

    fn spawn_host_in_session() -> Option<u32> {
        unsafe {
            let session = WTSGetActiveConsoleSessionId();
            if session == 0xFFFF_FFFF {
                return None; // no interactive session yet (login screen)
            }
            let mut token = HANDLE::default();
            if WTSQueryUserToken(session, &mut token).is_err() {
                return None;
            }
            let exe = super::util::current_exe_wide();
            let mut cmd = super::util::wide(&format!(
                "\"{}\" {}",
                super::util::current_exe_string(),
                super::HOST_ARG
            ));
            let si = STARTUPINFOW {
                cb: std::mem::size_of::<STARTUPINFOW>() as u32,
                ..Default::default()
            };
            let mut pi = PROCESS_INFORMATION::default();
            let ok = CreateProcessAsUserW(
                token,
                windows::core::PCWSTR(exe.as_ptr()),
                windows::core::PWSTR(cmd.as_mut_ptr()),
                None,
                None,
                false,
                Default::default(),
                None,
                None,
                &si,
                &mut pi,
            );
            let _ = CloseHandle(token);
            if ok.is_err() {
                return None;
            }
            let _ = CloseHandle(pi.hThread);
            let _ = CloseHandle(pi.hProcess);
            Some(pi.dwProcessId)
        }
    }
}

/// Small shared helpers (UTF-16 conversion, process liveness, current exe path).
pub mod util {
    #[cfg(windows)]
    use windows::Win32::Foundation::CloseHandle;
    #[cfg(windows)]
    use windows::Win32::System::Threading::{
        GetExitCodeProcess, OpenProcess, PROCESS_QUERY_LIMITED_INFORMATION,
    };

    pub fn wide(s: &str) -> Vec<u16> {
        s.encode_utf16().chain(std::iter::once(0)).collect()
    }
    pub fn current_exe_string() -> String {
        std::env::current_exe()
            .map(|p| p.to_string_lossy().into_owned())
            .unwrap_or_default()
    }
    pub fn current_exe_wide() -> Vec<u16> {
        wide(&current_exe_string())
    }

    #[cfg(windows)]
    pub fn process_alive(pid: u32) -> bool {
        const STILL_ACTIVE: u32 = 259;
        unsafe {
            match OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid) {
                Ok(h) => {
                    let mut code = 0u32;
                    let alive = GetExitCodeProcess(h, &mut code).is_ok() && code == STILL_ACTIVE;
                    let _ = CloseHandle(h);
                    alive
                }
                Err(_) => false,
            }
        }
    }

    /// Read a REG_SZ under HKLM (used to fetch the SSOT WallpaperUrl written by Set-MiOSWallpaper).
    #[cfg(windows)]
    pub fn reg_read_sz(subkey: &str, value: &str) -> Option<String> {
        use windows::Win32::System::Registry::{RegGetValueW, HKEY_LOCAL_MACHINE, RRF_RT_REG_SZ};
        unsafe {
            let sub = wide(subkey);
            let val = wide(value);
            let mut buf = vec![0u16; 2048];
            let mut size = (buf.len() * 2) as u32;
            let rc = RegGetValueW(
                HKEY_LOCAL_MACHINE,
                windows::core::PCWSTR(sub.as_ptr()),
                windows::core::PCWSTR(val.as_ptr()),
                RRF_RT_REG_SZ,
                None,
                Some(buf.as_mut_ptr() as *mut core::ffi::c_void),
                Some(&mut size),
            );
            if rc.0 == 0 {
                let chars = (size as usize / 2).saturating_sub(1);
                Some(String::from_utf16_lossy(&buf[..chars]))
            } else {
                None
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_util_wide() {
        let w = util::wide("hello");
        assert_eq!(w, vec![104, 101, 108, 108, 111, 0]);
    }
}
