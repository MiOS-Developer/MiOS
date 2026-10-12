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
            .unwrap_or(60);
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

#[cfg(not(windows))]
fn main() {
    eprintln!("mios-wallpaperd is a Windows-only service daemon.");
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
