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

        // WebView2 keeps its profile in a user data folder whose default is next to the exe
        // (C:\Program Files\MiOS\mios-wallpaperd.exe.WebView2): the standard desktop user cannot write there, so
        // every host died "create webview ... 0x80070005 Access is denied" (round-1 boot of
        // build-20261023T224037Z: exit 101, respawned every 3 s, black desktop). Use the user's LocalAppData.
        let Some(data_dir) = super::webview_data_dir(local_app_data().as_deref()) else {
            fail(None, "no LocalAppData known folder for the signed-in user");
        };
        let mut web_context = wry::WebContext::new(Some(std::path::PathBuf::from(&data_dir)));

        // Build the WebView on the (now re-parented) window.
        let _webview = match WebViewBuilder::new(&window)
            .with_web_context(&mut web_context)
            .with_url(&url)
            .build()
        {
            Ok(w) => w,
            Err(e) => fail(Some(&data_dir), &format!("create webview (user data folder {data_dir}): {e}")),
        };

        event_loop.run(move |event, _target, control_flow| {
            *control_flow = ControlFlow::Wait;
            // Keep the window + webview alive for the process lifetime; the service restarts us on exit.
            let _ = (&event, &window, &_webview, &web_context);
        });
    }

    /// The signed-in user's LocalAppData from the known-folder API (the process token, not the environment block a
    /// session-0 parent may have handed down); %LOCALAPPDATA% only when the API fails.
    fn local_app_data() -> Option<String> {
        use windows::Win32::System::Com::CoTaskMemFree;
        use windows::Win32::UI::Shell::{FOLDERID_LocalAppData, SHGetKnownFolderPath, KF_FLAG_DEFAULT};
        unsafe {
            if let Ok(p) = SHGetKnownFolderPath(&FOLDERID_LocalAppData, KF_FLAG_DEFAULT, None) {
                let s = p.to_string().ok();
                CoTaskMemFree(Some(p.0 as *const core::ffi::c_void));
                if s.as_deref().is_some_and(|v| !v.is_empty()) {
                    return s;
                }
            }
        }
        std::env::var("LOCALAPPDATA").ok()
    }

    /// Record why the host cannot run (next to its data folder, readable by the user) and exit with
    /// [`super::HOST_EXIT_FAILED`]; the service's respawn backoff spaces the retries.
    fn fail(data_dir: Option<&str>, why: &str) -> ! {
        if let Some(log) = data_dir.and_then(super::host_log_path) {
            if let Some(parent) = std::path::Path::new(&log).parent() {
                let _ = std::fs::create_dir_all(parent);
            }
            if let Ok(mut f) = std::fs::OpenOptions::new().create(true).append(true).open(&log) {
                use std::io::Write;
                let _ = writeln!(f, "[mios-wallpaperd host] {why}");
            }
        }
        std::process::exit(super::HOST_EXIT_FAILED);
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
/// Exit code of a Windows host that cannot run (the reason is in [`host_log_path`]); a Rust panic exits 101.
pub const HOST_EXIT_FAILED: i32 = 3;

/// The Windows host's WebView2 user data folder: `<LocalAppData>\MiOS\wallpaperd\WebView2`, per user and writable
/// by the signed-in desktop user. None without a LocalAppData path -- never the exe directory under Program Files.
pub fn webview_data_dir(local_app_data: Option<&str>) -> Option<String> {
    let base = local_app_data?.trim().trim_end_matches(['\\', '/']);
    if base.is_empty() {
        return None;
    }
    Some(format!("{base}\\MiOS\\wallpaperd\\WebView2"))
}

/// `host.log` beside the WebView2 data folder (`<LocalAppData>\MiOS\wallpaperd\host.log`).
pub fn host_log_path(data_dir: &str) -> Option<String> {
    let parent = data_dir.trim_end_matches(['\\', '/']).rsplit_once('\\')?.0;
    (!parent.is_empty()).then(|| format!("{parent}\\host.log"))
}

/// Spacing of host respawns by the session-0 service. A host that dies within [`RespawnBackoff::FAST_EXIT`] of its
/// start doubles the wait ([`RespawnBackoff::MIN`] .. [`RespawnBackoff::MAX`]); one that ran longer resets it.
/// Without it a host that cannot start was relaunched every 3 s for ever (round-1 boot: ~1 200 crashes an hour).
#[derive(Debug, Default)]
pub struct RespawnBackoff {
    started: Option<std::time::Instant>,
    not_before: Option<std::time::Instant>,
    delay: std::time::Duration,
}

impl RespawnBackoff {
    pub const FAST_EXIT: std::time::Duration = std::time::Duration::from_secs(30);
    pub const MIN: std::time::Duration = std::time::Duration::from_secs(3);
    pub const MAX: std::time::Duration = std::time::Duration::from_secs(300);

    /// The host was started at `now`.
    pub fn spawned(&mut self, now: std::time::Instant) {
        self.started = Some(now);
    }
    /// The host was found dead at `now`.
    pub fn exited(&mut self, now: std::time::Instant) {
        let lived = self.started.take().map(|s| now.saturating_duration_since(s));
        self.delay = match lived {
            Some(l) if l >= Self::FAST_EXIT => std::time::Duration::ZERO,
            _ if self.delay.is_zero() => Self::MIN,
            _ => (self.delay * 2).min(Self::MAX),
        };
        self.not_before = Some(now + self.delay);
    }
    /// True when a (re)spawn is allowed at `now`.
    pub fn may_spawn(&self, now: std::time::Instant) -> bool {
        self.not_before.is_none_or(|t| now >= t)
    }
    /// The current wait after a death (zero after a long-lived host).
    pub fn delay(&self) -> std::time::Duration {
        self.delay
    }
}

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
    // get(), never data["theme"]: toml's Index panics on a missing key, and the MiOS-Xbox guest resolves an SSOT
    // without [theme] (no layer files on the Windows side), so every host died "index not found" before it could
    // fall back to the registry WallpaperUrl (round-1 debug guest, fixed host build).
    let config = data
        .get("theme")
        .and_then(|t| t.get("wallpaper"))
        .ok_or("[theme.wallpaper] is missing from the resolved SSOT")?;
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

/// The native host's live step rate ([desktop.framerate]; operator 2026-10-23: "wallpaper(s) are 1/2 rate globally--
/// except for Unlocked mode(s)"): `ctl` = the state file miosd framerate writes under $XDG_RUNTIME_DIR, which Host.qml
/// reads once a second (wallpaper_fps). The URL's fps stays the look's reference rate. Nothing when the policy is
/// absent or disabled (the host then steps at the reference rate, as before) or for the web host (the HTML's own FPS).
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
fn rate_query(data: &toml::Value, renderer_native: bool, runtime: &std::path::Path) -> Result<Option<String>, String> {
    if !renderer_native {
        return Ok(None);
    }
    let Some(policy) = mios_resolver::framerate::Policy::from_ssot(data)?.filter(|p| p.enabled) else {
        return Ok(None);
    };
    let path = policy.state_path(runtime);
    let path = path.to_str().ok_or("XDG_RUNTIME_DIR is not UTF-8")?;
    let mut enc = String::new();
    for b in path.bytes() {
        if b.is_ascii_alphanumeric() || b"/._-".contains(&b) {
            enc.push(b as char);
        } else {
            enc.push_str(&format!("%{b:02X}"));
        }
    }
    Ok(Some(format!("ctl={enc}")))
}

/// Linux host for the configured renderer: "native" (GPU shaders, Host.qml) or "web" (QtWebEngine, WebHost.qml).
/// Only linux_main calls it; the Windows build (build.native.windows lints warning-fatal) keeps it for the shared tests.
#[cfg_attr(not(target_os = "linux"), allow(dead_code))]
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
    let native = config.get("renderer").and_then(toml::Value::as_str) == Some("native");
    let url = match rate_query(&data, native, runtime_path)? {
        Some(q) => format!("{url}&{q}"),
        None => url,
    };

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
            // Host.qml reads the frame-rate state file through XMLHttpRequest (Qt refuses file:// reads without it)
            .env("QML_XHR_ALLOW_FILE_READ", "1")
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
    fn a_resolved_ssot_without_theme_is_an_error_not_a_panic() {
        // negative: the MiOS-Xbox guest resolves an SSOT without [theme] (no Windows-side layer files); the old
        // data["theme"]["wallpaper"] index panicked "index not found" in every host
        let empty = toml::Value::Table(toml::map::Map::new());
        for windows in [true, false] {
            let r = std::panic::catch_unwind(|| wallpaper_url(&empty, windows));
            assert!(r.expect("must not panic").unwrap_err().contains("[theme.wallpaper]"));
        }
        let mut no_wp: toml::Value = toml::from_str("[theme]\nname = \"x\"\n").unwrap();
        assert!(wallpaper_url(&no_wp, true).is_err());
        no_wp.as_table_mut().unwrap().remove("theme");
        assert!(wallpaper_url(&no_wp, false).is_err());
        // positive: the fixture SSOT still renders on both platforms
        assert!(wallpaper_url(&fixture(), true).unwrap().starts_with("file:///"));
        assert!(wallpaper_url(&fixture(), false).is_ok());
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
    fn native_host_gets_the_live_rate_file_and_the_web_host_does_not() {
        let mut doc = fixture();
        let rt = std::path::Path::new("/run/user/1000");
        assert_eq!(rate_query(&doc, true, rt).unwrap(), None, "no [desktop.framerate]: the reference rate only");
        let policy: toml::Value = toml::from_str(FRAMERATE).unwrap();
        doc.as_table_mut().unwrap().insert("desktop".into(), policy["desktop"].clone());
        assert_eq!(rate_query(&doc, true, rt).unwrap().as_deref(), Some("ctl=/run/user/1000/mios/framerate/state.json"));
        assert_eq!(rate_query(&doc, false, rt).unwrap(), None, "the web host keeps the HTML's own rate");
        assert_eq!(rate_query(&doc, true, std::path::Path::new("/run/user/1 000")).unwrap().as_deref(), Some("ctl=/run/user/1%20000/mios/framerate/state.json"));
        doc["desktop"]["framerate"]["enabled"] = toml::Value::Boolean(false);
        assert_eq!(rate_query(&doc, true, rt).unwrap(), None, "disabled: the reference rate only");
        doc["desktop"]["framerate"]["enabled"] = toml::Value::String("yes".into());
        assert!(rate_query(&doc, true, rt).is_err(), "a broken policy is an error, not a silent default");
        // the shared query is unchanged: fps stays the reference rate on both platforms
        let linux = wallpaper_url(&doc, false).unwrap();
        assert!(linux.contains("fps=30") && !linux.contains("ctl="));
    }

    const FRAMERATE: &str = r#"
[desktop.framerate]
enabled = true
unlocked = false
debounce_ms = 750
self_check_s = 60
gaming_exit_hold_ms = 3000
mechanism = "auto"
modes = "advertised"
revert_timeout_ms = 5000
mode_confirm_s = 15
modeset_hold_ms = 5000
modeset_min_interval_s = 30
exclude_prefixes = ["HEADLESS-"]
pacing_plugin = "mios-pacing"
state_file = "mios/framerate/state.json"
user_fragment = "50-framerate.toml"
[desktop.framerate.profiles]
power-saver = "power-saver"
balanced = "balanced"
performance = "performance"
unknown = "balanced"
[desktop.framerate.gaming]
gamemode = true
fullscreen_game = false
game_class = '^steam_app_[0-9]+$'
[desktop.framerate.tiers.unlocked]
desktop_hz = 0
glass_divisor = 2
wallpaper_divisor = 1
[desktop.framerate.tiers.gaming]
desktop_hz = 120
glass_divisor = 2
wallpaper_divisor = 2
[desktop.framerate.tiers.battery]
desktop_hz = 60
glass_divisor = 3
wallpaper_divisor = 2
[desktop.framerate.tiers.performance]
desktop_hz = 90
glass_divisor = 3
wallpaper_divisor = 2
[desktop.framerate.tiers.balanced]
desktop_hz = 60
glass_divisor = 3
wallpaper_divisor = 2
[desktop.framerate.tiers.power-saver]
desktop_hz = 60
glass_divisor = 3
wallpaper_divisor = 2
"#;

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

        // Supervise a host child in the active user session until asked to stop (a host that keeps dying is
        // respawned with a growing delay, never every 3 s for ever).
        let mut backoff = super::RespawnBackoff::default();
        loop {
            if shutdown_rx.recv_timeout(Duration::from_secs(3)).is_ok() {
                break;
            }
            super::session::ensure_host_running(&mut backoff);
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
    use windows::Win32::System::Environment::{CreateEnvironmentBlock, DestroyEnvironmentBlock};
    use windows::Win32::System::RemoteDesktop::{WTSGetActiveConsoleSessionId, WTSQueryUserToken};
    use windows::Win32::System::Threading::{
        CreateProcessAsUserW, OpenProcess, TerminateProcess, CREATE_UNICODE_ENVIRONMENT,
        PROCESS_CREATION_FLAGS, PROCESS_INFORMATION, PROCESS_TERMINATE, STARTUPINFOW,
    };

    static HOST_PID: AtomicU32 = AtomicU32::new(0);

    pub fn ensure_host_running(backoff: &mut super::RespawnBackoff) {
        let now = std::time::Instant::now();
        let pid = HOST_PID.load(Ordering::SeqCst);
        if pid != 0 {
            if super::util::process_alive(pid) {
                return;
            }
            HOST_PID.store(0, Ordering::SeqCst);
            backoff.exited(now);
        }
        if !backoff.may_spawn(now) {
            return;
        }
        if let Some(new_pid) = spawn_host_in_session() {
            HOST_PID.store(new_pid, Ordering::SeqCst);
            backoff.spawned(now);
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
            // The interactive desktop of the user's session (a NULL lpDesktop inherits the session-0 service's
            // window station) and the USER's environment block (else the host inherits SYSTEM's, e.g. its
            // %LOCALAPPDATA% and %TEMP%).
            let mut desktop = super::util::wide("winsta0\\default");
            let si = STARTUPINFOW {
                cb: std::mem::size_of::<STARTUPINFOW>() as u32,
                lpDesktop: windows::core::PWSTR(desktop.as_mut_ptr()),
                ..Default::default()
            };
            let mut env: *mut core::ffi::c_void = std::ptr::null_mut();
            let have_env = CreateEnvironmentBlock(&mut env, token, false).is_ok() && !env.is_null();
            let flags = if have_env { CREATE_UNICODE_ENVIRONMENT } else { PROCESS_CREATION_FLAGS(0) };
            let mut pi = PROCESS_INFORMATION::default();
            let ok = CreateProcessAsUserW(
                token,
                windows::core::PCWSTR(exe.as_ptr()),
                windows::core::PWSTR(cmd.as_mut_ptr()),
                None,
                None,
                false,
                flags,
                if have_env { Some(env as *const core::ffi::c_void) } else { None },
                None,
                &si,
                &mut pi,
            );
            if have_env {
                let _ = DestroyEnvironmentBlock(env);
            }
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

    #[test]
    fn webview_data_dir_is_per_user_never_the_exe_dir() {
        // positive: the signed-in user's LocalAppData (round-1 guest), with or without a trailing separator
        let d = webview_data_dir(Some(r"C:\Users\user\AppData\Local")).unwrap();
        assert_eq!(d, r"C:\Users\user\AppData\Local\MiOS\wallpaperd\WebView2");
        assert_eq!(webview_data_dir(Some("C:\\Users\\user\\AppData\\Local\\")).unwrap(), d);
        assert_eq!(host_log_path(&d).unwrap(), r"C:\Users\user\AppData\Local\MiOS\wallpaperd\host.log");
        // negative: no known folder -> no data dir (the host then fails with a record), never a Program Files
        // default; the WebView2 default the round-1 host used was <exe dir>\mios-wallpaperd.exe.WebView2
        assert_eq!(webview_data_dir(None), None);
        assert_eq!(webview_data_dir(Some("  ")), None);
        assert!(!d.to_ascii_lowercase().contains("program files"));
        assert_eq!(host_log_path("WebView2"), None);
    }

    #[test]
    fn respawn_backoff_spaces_a_crash_loop_and_resets_after_a_good_run() {
        use std::time::Duration;
        let t0 = std::time::Instant::now();
        let mut b = RespawnBackoff::default();
        assert!(b.may_spawn(t0), "the first host starts at once");
        // the round-1 host died 0.3 s after each start: the waits double 3 s .. 300 s
        let mut t = t0;
        let mut waits = Vec::new();
        for _ in 0..10 {
            b.spawned(t);
            t += Duration::from_millis(300);
            b.exited(t);
            assert!(!b.may_spawn(t), "no respawn straight after a fast death");
            waits.push(b.delay().as_secs());
            t += b.delay();
            assert!(b.may_spawn(t));
        }
        assert_eq!(waits, vec![3, 6, 12, 24, 48, 96, 192, 300, 300, 300]);
        // negative control: the old supervisor respawned every 3 s -> 1200 starts an hour; the backoff allows 18
        let mut starts = 0;
        let mut b = RespawnBackoff::default();
        let mut t = t0;
        while t < t0 + Duration::from_secs(3600) {
            if b.may_spawn(t) {
                starts += 1;
                b.spawned(t);
                b.exited(t + Duration::from_millis(300));
            }
            t += Duration::from_secs(3);
        }
        assert!(starts <= 20, "{starts} starts in an hour");
        // a host that ran past FAST_EXIT (a session ending, an update) is restarted without delay
        let mut b = RespawnBackoff::default();
        b.spawned(t0);
        b.exited(t0 + Duration::from_millis(100));
        b.spawned(t0 + Duration::from_secs(3));
        b.exited(t0 + Duration::from_secs(3) + RespawnBackoff::FAST_EXIT);
        assert_eq!(b.delay(), Duration::ZERO);
        assert!(b.may_spawn(t0 + Duration::from_secs(3) + RespawnBackoff::FAST_EXIT));
    }
}
