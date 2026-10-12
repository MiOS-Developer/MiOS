// AI-hint: Native Windows terminal launcher: render layered MiOS SSOT before launching and center the visible frame on the current monitor work area.
// AI-related: usr/share/mios/windows/mios-native-client-setup.ps1, usr/share/mios/mios.toml, usr/share/mios/windows/mios-pc-control.ps1
#![cfg_attr(windows, windows_subsystem = "windows")]

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Bounds {
    x: i32,
    y: i32,
    width: i32,
    height: i32,
}

fn center(work: Bounds, window: Bounds) -> Bounds {
    let width = work.width.min(window.width);
    let height = work.height.min(window.height);
    Bounds {
        x: work.x + (work.width - width) / 2,
        y: work.y + (work.height - height) / 2,
        width,
        height,
    }
}

fn geometry_proof(negative: bool) -> Result<(), String> {
    let areas = [
        Bounds {
            x: 0,
            y: 0,
            width: 1920,
            height: 1040,
        },
        Bounds {
            x: -2160,
            y: 0,
            width: 2160,
            height: 3840,
        },
        Bounds {
            x: 1920,
            y: -1200,
            width: 3840,
            height: 2120,
        },
        Bounds {
            x: 0,
            y: 0,
            width: 800,
            height: 560,
        },
    ];
    for work in areas {
        for scale in [100, 125, 150, 200, 300] {
            let mut result = center(
                work,
                Bounds {
                    x: 153,
                    y: 219,
                    width: 820 * scale / 100,
                    height: 412 * scale / 100,
                },
            );
            if negative {
                result.x = 0;
            }
            if result.width > work.width
                || result.height > work.height
                || (2 * result.x + result.width - (2 * work.x + work.width)).abs() > 1
                || (2 * result.y + result.height - (2 * work.y + work.height)).abs() > 1
            {
                return Err("DEVLOOP-PLANTED-CENTER: geometry mismatch".into());
            }
        }
    }
    Ok(())
}

#[cfg(windows)]
mod desktop {
    use super::{center, Bounds};
    use serde_json::Value;
    use std::{
        env, fs, mem,
        path::{Path, PathBuf},
        process::Command,
        ptr, thread,
        time::{Duration, Instant, SystemTime, UNIX_EPOCH},
    };
    use windows_sys::Win32::{
        Foundation::{HWND, LPARAM, POINT, RECT},
        Graphics::{
            Dwm::{DwmGetWindowAttribute, DWMWA_EXTENDED_FRAME_BOUNDS},
            Gdi::{GetMonitorInfoW, MonitorFromPoint, MONITORINFO, MONITOR_DEFAULTTONEAREST},
        },
        UI::{
            HiDpi::{
                SetProcessDpiAwarenessContext, SetThreadDpiAwarenessContext,
                DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2,
            },
            WindowsAndMessaging::*,
        },
    };

    fn wide(text: &str) -> Vec<u16> {
        text.encode_utf16().chain(Some(0)).collect()
    }
    fn bounds(r: RECT) -> Bounds {
        Bounds {
            x: r.left,
            y: r.top,
            width: r.right - r.left,
            height: r.bottom - r.top,
        }
    }
    fn hidden(command: &mut Command) -> &mut Command {
        mios_service_core::process::configure_hidden(command)
    }
    fn native_bin() -> Result<PathBuf, String> {
        let exe = env::current_exe().map_err(|e| e.to_string())?;
        let parent = exe.parent().ok_or("Executable directory missing")?;
        if parent.join("native-binding.json").is_file() {
            return Ok(parent.into());
        }
        Ok(
            PathBuf::from(env::var_os("ProgramData").ok_or("ProgramData missing")?)
                .join("MiOS/bin"),
        )
    }
    fn json(path: PathBuf) -> Result<Value, String> {
        serde_json::from_slice(&fs::read(path).map_err(|e| e.to_string())?)
            .map_err(|e| e.to_string())
    }
    fn text<'a>(value: &'a Value, key: &str) -> Result<&'a str, String> {
        value[key]
            .as_str()
            .filter(|s| !s.is_empty())
            .ok_or_else(|| format!("SSOT {key} is missing"))
    }
    fn terminal() -> Result<PathBuf, String> {
        for dir in env::split_paths(&env::var_os("PATH").unwrap_or_default()) {
            let path = dir.join("wt.exe");
            if path.is_file() {
                return Ok(path);
            }
        }
        let output = hidden(Command::new("powershell.exe").args([
            "-NoLogo",
            "-NoProfile",
            "-Command",
            "(Get-AppxPackage Microsoft.WindowsTerminal).InstallLocation",
        ]))
        .output()
        .map_err(|e| e.to_string())?;
        let path = PathBuf::from(String::from_utf8_lossy(&output.stdout).trim()).join("wt.exe");
        if !output.status.success() || !path.is_file() {
            return Err("Windows Terminal is missing; run the MiOS installer".into());
        }
        Ok(path)
    }
    fn monitor(point: POINT) -> Result<Bounds, String> {
        let mut info: MONITORINFO = unsafe { mem::zeroed() };
        info.cbSize = mem::size_of::<MONITORINFO>() as u32;
        if unsafe { GetMonitorInfoW(MonitorFromPoint(point, MONITOR_DEFAULTTONEAREST), &mut info) }
            == 0
        {
            return Err("Cannot read display work area".into());
        }
        Ok(bounds(info.rcWork))
    }
    struct Search {
        title: String,
        window_name: String,
        hwnd: HWND,
    }
    unsafe extern "system" fn find(hwnd: HWND, context: LPARAM) -> i32 {
        let search = &mut *(context as *mut Search);
        if IsWindowVisible(hwnd) == 0 {
            return 1;
        }
        let mut class = [0u16; 128];
        let class_len = GetClassNameW(hwnd, class.as_mut_ptr(), class.len() as i32);
        if class_len <= 0
            || String::from_utf16_lossy(&class[..class_len.max(0) as usize])
                != "CASCADIA_HOSTING_WINDOW_CLASS"
        {
            return 1;
        }
        let mut name = [0u16; 512];
        let len = GetWindowTextW(hwnd, name.as_mut_ptr(), name.len() as i32);
        if len <= 0 {
            return 1;
        }
        let current_title = String::from_utf16_lossy(&name[..len as usize]);
        let matches = current_title == search.title
            || current_title.starts_with(&search.title)
            || current_title.contains(&search.title)
            || (!search.window_name.is_empty()
                && (current_title == search.window_name
                    || current_title.starts_with(&search.window_name)
                    || current_title.contains(&search.window_name)))
            || current_title.contains("MiOS")
            || current_title.contains("Terminal");
        if !matches {
            return 1;
        }
        search.hwnd = hwnd;
        0
    }
    fn window(title: &str, window_name: &str) -> HWND {
        let mut search = Search {
            title: title.into(),
            window_name: window_name.into(),
            hwnd: ptr::null_mut(),
        };
        unsafe {
            EnumWindows(Some(find), &mut search as *mut Search as LPARAM);
        }
        search.hwnd
    }
    fn place(hwnd: HWND, point: POINT) -> Result<Bounds, String> {
        if unsafe { IsZoomed(hwnd) } != 0 || unsafe { IsIconic(hwnd) } != 0 {
            unsafe {
                ShowWindow(hwnd, SW_RESTORE);
            }
        }
        let mut r: RECT = unsafe { mem::zeroed() };
        if unsafe { GetWindowRect(hwnd, &mut r) } == 0 {
            return Err("Cannot read terminal window bounds".into());
        }
        let outer = bounds(r);
        let mut frame = r;
        unsafe {
            DwmGetWindowAttribute(
                hwnd,
                DWMWA_EXTENDED_FRAME_BOUNDS as u32,
                &mut frame as *mut RECT as _,
                mem::size_of::<RECT>() as u32,
            );
        }
        let visible = bounds(frame);
        if visible.width <= 0 || visible.height <= 0 {
            return Err("Terminal has no visible frame".into());
        }
        let target = center(monitor(point)?, visible);
        if unsafe {
            SetWindowPos(
                hwnd,
                ptr::null_mut(),
                target.x - (visible.x - outer.x),
                target.y - (visible.y - outer.y),
                target.width + outer.width - visible.width,
                target.height + outer.height - visible.height,
                SWP_NOZORDER | SWP_NOACTIVATE,
            )
        } == 0
        {
            return Err("Terminal placement failed".into());
        }
        Ok(target)
    }
    fn helper(action: &str, args: &[String]) -> Result<(), String> {
        // Keep the complete existing GUI/UIA surface, including right/middle
        // click and real accessibility trees, in its canonical implementation.
        let bin = native_bin()?;
        let binding = json(bin.join("native-binding.json"))?;
        let script = match action {
            "dump" => "mios-uia-dump.ps1",
            "foreground" => "mios-window-foreground.ps1",
            _ => "mios-pc-control.ps1",
        };
        let mut command = Command::new(text(&binding, "engine")?);
        command
            .args(["-NoLogo", "-NoProfile", "-File"])
            .arg(bin.join(script));
        if !matches!(action, "dump" | "foreground") {
            command.arg(action);
        }
        if action == "foreground" {
            command.arg("-ProcessName");
        }
        command.args(args);
        let status = hidden(&mut command).status().map_err(|e| e.to_string())?;
        if !status.success() {
            return Err(format!("{action} failed: {status}"));
        }
        Ok(())
    }
    fn configure_host_font(config: &Value) -> Result<bool, String> {
        use windows_sys::Win32::System::Console::*;
        let family = text(&config["theme"]["font"], "family")?;
        let name = wide(family);
        if name.len() > 32 {
            return Err("SSOT theme.font.family exceeds the console face-name limit".into());
        }
        let size = config["theme"]["font"]["size"]
            .as_i64()
            .filter(|s| *s > 0 && *s <= i16::MAX as i64)
            .ok_or("Invalid SSOT theme.font.size")?;
        let codepage = config["theme"]["terminal"]["windows_codepage"]
            .as_u64()
            .and_then(|v| u32::try_from(v).ok())
            .ok_or("Invalid SSOT Windows codepage")?;
        let handle = unsafe { GetStdHandle(STD_OUTPUT_HANDLE) };
        let palette = mios_service_core::launcher::windows_console_palette(config)?;
        let mut screen: CONSOLE_SCREEN_BUFFER_INFOEX = unsafe { std::mem::zeroed() };
        screen.cbSize = std::mem::size_of::<CONSOLE_SCREEN_BUFFER_INFOEX>() as u32;
        if unsafe { GetConsoleScreenBufferInfoEx(handle, &mut screen) } != 0 {
            screen.ColorTable = palette;
            // SetConsoleScreenBufferInfoEx takes exclusive right/bottom bounds.
            screen.srWindow.Right += 1;
            screen.srWindow.Bottom += 1;
            if unsafe { SetConsoleScreenBufferInfoEx(handle, &screen) } == 0 {
                return Err("Failed to apply SSOT Windows console palette".into());
            }
        }
        let mut font: CONSOLE_FONT_INFOEX = unsafe { std::mem::zeroed() };
        font.cbSize = std::mem::size_of::<CONSOLE_FONT_INFOEX>() as u32;
        if unsafe { GetCurrentConsoleFontEx(handle, 0, &mut font) } == 0 {
            return Ok(false);
        }
        font.FaceName = [0; 32];
        font.FaceName[..name.len()].copy_from_slice(&name);
        font.dwFontSize.Y = size as i16;
        if unsafe { SetCurrentConsoleFontEx(handle, 0, &font) } == 0 {
            return Ok(false);
        }
        if unsafe { SetConsoleOutputCP(codepage) } == 0 || unsafe { SetConsoleCP(codepage) } == 0 {
            return Err("Failed to apply the SSOT Windows console codepage".into());
        }
        if unsafe { GetCurrentConsoleFontEx(handle, 0, &mut font) } == 0 {
            return Ok(false);
        }
        let end = font.FaceName.iter().position(|c| *c == 0).unwrap_or(32);
        Ok(
            String::from_utf16_lossy(&font.FaceName[..end]).eq_ignore_ascii_case(family)
                && family.to_ascii_lowercase().contains("nerd"),
        )
    }

    fn refresh_tmux(
        executable: &str,
        socket: Option<&str>,
        config: &Path,
        timeout: Duration,
    ) -> Result<(), String> {
        let command = || {
            let mut command = Command::new(executable);
            if let Some(socket) = socket {
                command.args(["-L", socket]);
            }
            command
        };
        let probe =
            mios_service_core::process::output_timeout(command().arg("has-session"), timeout)
                .map_err(|e| format!("tmux probe: {e}"))?;
        if !probe.status.success() {
            let error = String::from_utf8_lossy(&probe.stderr);
            if probe.status.code() == Some(1)
                && (error.contains("no server")
                    || error.contains("no sessions")
                    || error.contains("error connecting"))
            {
                return Ok(());
            }
            return Err(format!("tmux probe failed: {}: {error}", probe.status));
        }
        let reload = mios_service_core::process::output_timeout(
            command().arg("source-file").arg(config),
            timeout,
        )
        .map_err(|e| format!("tmux reload: {e}"))?;
        if !reload.status.success() {
            return Err(format!(
                "tmux source-file failed: {}: {}",
                reload.status,
                String::from_utf8_lossy(&reload.stderr)
            ));
        }
        Ok(())
    }

    pub fn run(args: &[String]) -> Result<(), String> {
        if args.first().is_some_and(|a| {
            matches!(
                a.as_str(),
                "--host-terminal" | "--stage-host-terminal" | "--dispatch"
            )
        }) {
            unsafe {
                windows_sys::Win32::System::Console::AttachConsole(
                    windows_sys::Win32::System::Console::ATTACH_PARENT_PROCESS,
                );
            }
        }
        unsafe {
            SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
            SetThreadDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2);
        }
        if let Some(action) = args.first().filter(|s| {
            matches!(
                s.as_str(),
                "dump" | "foreground" | "click" | "move" | "resize"
            )
        }) {
            return helper(action, &args[1..]);
        }
        let bin = native_bin()?;
        let binding = json(bin.join("native-binding.json"))?;
        let engine = text(&binding, "engine")?;
        // Resolve policy in Rust. Bootstrap shells do not render runtime policy.
        let projection = hidden(Command::new("wsl.exe").args([
            "-d",
            text(&binding, "distro")?,
            "-u",
            text(&binding, "linuxUser")?,
            "--",
            "/usr/bin/mios-resolver",
            "--emit=json",
        ]))
        .output()
        .map_err(|e| e.to_string())?;
        if !projection.status.success() {
            return Err("Runtime SSOT projection failed; launch stopped".into());
        }
        // Packaged clients may see a virtualized stale LocalAppData file.
        // Use this invocation's resolved SSOT rather than re-reading a cache.
        let resolved: Value = serde_json::from_slice(&projection.stdout)
            .map_err(|e| format!("Runtime SSOT projection returned invalid JSON: {e}"))?;
        let config = resolved
            .get("merged")
            .ok_or("Native resolver omitted merged SSOT")?;
        if args
            .first()
            .is_some_and(|a| matches!(a.as_str(), "--host-terminal" | "--stage-host-terminal"))
        {
            let stage_only = args[0] == "--stage-host-terminal";
            if !stage_only && std::env::var_os("MIOS_HOST_TMUX").is_some() {
                return Ok(());
            }
            let local = std::env::var_os("LOCALAPPDATA").ok_or("LOCALAPPDATA is unavailable")?;
            let directory = PathBuf::from(local).join("MiOS").join("terminal");
            let config_path = directory.join("mios-host.tmux.conf");
            let home =
                PathBuf::from(std::env::var_os("USERPROFILE").ok_or("USERPROFILE is unavailable")?);
            let local = PathBuf::from(
                std::env::var_os("LOCALAPPDATA").ok_or("LOCALAPPDATA is unavailable")?,
            );
            let legacy = [
                home.join(".tmux.conf"),
                local.join("tmux/tmux.conf"),
                local.join("MiOS/tmux/tmux.conf"),
            ];
            let font_verified = configure_host_font(config)?;
            let owned =
                mios_service_core::host_tmux::stage(config, &config_path, &legacy, font_verified)?;
            let arguments = mios_service_core::launcher::host_tmux_args(
                config,
                &config_path.to_string_lossy(),
            )?;
            let executable = text(&config["terminal"]["windows"], "executable")?;
            if stage_only {
                return Ok(());
            }
            let timeout = config["terminal"]["windows"]["command_timeout_ms"]
                .as_u64()
                .filter(|n| *n > 0 && *n <= 30000)
                .ok_or("Invalid SSOT terminal.windows.command_timeout_ms")?;
            let timeout = Duration::from_millis(timeout);
            refresh_tmux(
                executable,
                Some(text(&config["terminal"]["windows"], "socket_name")?),
                &config_path,
                timeout,
            )?;
            if legacy.iter().any(|p| owned.contains(p)) {
                refresh_tmux(executable, None, &config_path, timeout)?;
            }
            let status = Command::new(text(&config["terminal"]["windows"], "executable")?)
                .args(arguments)
                .env("MIOS_HOST_TMUX", "1")
                .status()
                .map_err(|e| e.to_string())?;
            if !status.success() {
                return Err(format!("Native Windows tmux failed: {status}"));
            }
            return Ok(());
        }
        if args.first().is_some_and(|a| a == "--dispatch") {
            let verb = args.get(1).map(String::as_str).unwrap_or("terminal");
            let rest = args.get(2..).unwrap_or_default();
            let command: Vec<String> = match verb {
                "terminal" | "ai-terminal" => {
                    let mut command = vec!["/usr/libexec/mios/mios-terminal".into()];
                    if verb == "ai-terminal" {
                        command.extend(["--action".into(), "ai".into()]);
                    }
                    command.extend_from_slice(rest);
                    command
                }
                "btop" => std::iter::once("/usr/bin/btop".into())
                    .chain(rest.iter().cloned())
                    .collect(),
                "ai" | "agent" | "agents" | "mon" | "monitor" => {
                    let routed = if verb == "monitor" { "mon" } else { verb };
                    let mut command = vec!["/usr/bin/mios".into(), routed.into()];
                    command.extend_from_slice(rest);
                    command
                }
                _ => {
                    let status = Command::new(engine)
                        .args(["-NoLogo", "-NoProfile", "-File"])
                        .arg(bin.join("mios-native-entry.ps1"))
                        .args(&args[1..])
                        .status()
                        .map_err(|e| e.to_string())?;
                    if !status.success() {
                        return Err(format!("MiOS {verb} failed: {status}"));
                    }
                    return Ok(());
                }
            };
            let policy = mios_service_core::launcher::terminal_config(config, "", false)?;
            let status = Command::new("wsl.exe")
                .args([
                    "-d",
                    text(&binding, "distro")?,
                    "-u",
                    text(&binding, "linuxUser")?,
                    "--cd",
                    &policy[7],
                    "--",
                ])
                .args(command)
                .status()
                .map_err(|e| e.to_string())?;
            if !status.success() {
                return Err(format!("MiOS guest {verb} failed: {status}"));
            }
            return Ok(());
        }
        if args.first().is_some_and(|a| a == "--tmux") {
            let policy = mios_service_core::launcher::terminal_config(config, "", false)?;
            if config["terminal"]["windows_tmux_backend"].as_str() != Some("wsl") {
                return Err("Unsupported SSOT terminal.windows_tmux_backend".into());
            }
            let mut guard = Command::new("wsl.exe");
            guard.args([
                "-d",
                text(&binding, "distro")?,
                "-u",
                text(&binding, "linuxUser")?,
                "--",
                "/usr/bin/miosd",
                "terminal-runtime-check",
                "--root",
                "/",
            ]);
            if !guard.status().map_err(|e| e.to_string())?.success() {
                return Err("Native tmux namespace verification failed; run mios repair".into());
            }
            let status = Command::new("wsl.exe")
                .args([
                    "-d",
                    text(&binding, "distro")?,
                    "-u",
                    text(&binding, "linuxUser")?,
                    "--cd",
                    &policy[7],
                    "--",
                    "env",
                    &format!("TMUX_TMPDIR={}", policy[8]),
                    "tmux",
                ])
                .args(&args[1..])
                .status()
                .map_err(|e| e.to_string())?;
            if !status.success() {
                return Err(format!("MiOS tmux exited: {status}"));
            }
            return Ok(());
        }
        if args.first().is_some_and(|a| a == "--build-monitor") {
            let value = |flag: &str| {
                args.iter()
                    .position(|a| a == flag)
                    .and_then(|i| args.get(i + 1))
                    .map(String::as_str)
                    .ok_or_else(|| format!("{flag} needs a path"))
            };
            let python = value("--python")?;
            let script = value("--monitor-script")?;
            if !std::path::Path::new(python).is_file() || !std::path::Path::new(script).is_file() {
                return Err("Build monitor executable or asset missing".into());
            }
            let mut point = POINT { x: 0, y: 0 };
            let _ = unsafe { GetCursorPos(&mut point) };
            let work = monitor(point)?;
            let mut rendered = mios_service_core::launcher::render_monitor(
                config,
                python,
                script,
                engine,
                work.height > work.width,
            )?;
            let title_index = rendered
                .iter()
                .position(|a| a == "--title")
                .ok_or("Monitor title missing")?
                + 1;
            let title = format!("{}-{}", rendered[title_index], std::process::id());
            rendered[title_index] = title.clone();
            let mut point = POINT { x: 0, y: 0 };
            let _ = unsafe { GetCursorPos(&mut point) };
            let centered = config["theme"]["terminal"]["center_on_launch"]
                .as_bool()
                .ok_or("SSOT center_on_launch must be a boolean")?;
            let mut command = Command::new(terminal()?);
            let status = command
                .args(&rendered)
                .status()
                .map_err(|e| e.to_string())?;
            if !status.success() {
                return Err(format!("Native build monitor launch failed: {status}"));
            }
            return settle_window(
                &title,
                text(&config["terminal"]["monitor"], "window_name")?,
                point,
                centered,
                false,
            );
        }
        let profiles = &config["theme"]["terminal"];
        let centered = profiles["center_on_launch"]
            .as_bool()
            .ok_or("SSOT center_on_launch must be a boolean")?;
        let dev = text(profiles, "dev_profile_name")?;
        let profile = args
            .first()
            .filter(|s| !s.starts_with("--"))
            .map(String::as_str)
            .unwrap_or(dev);
        let action = args
            .iter()
            .position(|a| a == "--action")
            .map(|i| {
                args.get(i + 1)
                    .map(String::as_str)
                    .ok_or("--action needs a MiOS action")
            })
            .transpose()?;
        let ai = action == Some("ai");
        let logical = if ai {
            text(&config["mcp"]["tmux"]["workspace"], "window_name")?
        } else if profile == dev {
            text(profiles, "summon_window_name")?
        } else {
            profile
        };
        let compact = args.iter().any(|a| a == "--compact");
        let dimensions = &config["terminal"];
        let cols = dimensions["cols"]
            .as_u64()
            .filter(|n| *n > 0)
            .ok_or("SSOT terminal.cols missing")?;
        let rows = dimensions["rows"]
            .as_u64()
            .filter(|n| *n > 0)
            .ok_or("SSOT terminal.rows missing")?;
        let title = format!(
            "MiOS-{}-{}",
            std::process::id(),
            SystemTime::now()
                .duration_since(UNIX_EPOCH)
                .map_err(|e| e.to_string())?
                .as_nanos()
        );
        let window_name = if args.iter().any(|a| a == "--test-launch") {
            title.as_str()
        } else {
            logical
        };
        let mut point = POINT { x: 0, y: 0 };
        let _ = unsafe { GetCursorPos(&mut point) };
        let mut command = Command::new(terminal()?);
        command.args([
            "-w",
            window_name,
            "--size",
            &format!("{cols},{rows}"),
            "--focus",
            "new-tab",
            "-p",
            profile,
            "--colorScheme",
            text(profiles, "scheme_name")?,
            "--title",
            &title,
            "--suppressApplicationTitle",
        ]);
        if let Some(action) = action {
            if !config["keybindings"]["actions"]
                .as_array()
                .ok_or("SSOT keybindings missing")?
                .iter()
                .any(|a| a["id"].as_str() == Some(action))
            {
                return Err("Unknown MiOS SSOT action".into());
            }
            command
                .args(["--", engine, "-NoLogo", "-NoProfile", "-File"])
                .arg(bin.join("mios-native-entry.ps1"))
                .args(["terminal", "--action", action]);
            if compact {
                command.arg("--compact");
            }
        }
        command.spawn().map_err(|e| e.to_string())?;
        settle_window(
            &title,
            window_name,
            point,
            centered,
            args.iter().any(|a| a == "--test-launch"),
        )
    }
    fn settle_window(
        title: &str,
        window_name: &str,
        point: POINT,
        centered: bool,
        test: bool,
    ) -> Result<(), String> {
        let deadline = Instant::now() + Duration::from_secs(16);
        let hwnd = loop {
            let hwnd = window(title, window_name);
            if !hwnd.is_null() {
                break hwnd;
            }
            if Instant::now() >= deadline {
                return Err("Launched terminal window did not appear".into());
            }
            thread::sleep(Duration::from_millis(150));
        };
        if !centered {
            return if test {
                Err("SSOT centering is disabled".into())
            } else {
                Ok(())
            };
        }
        for _ in 0..12 {
            // Work area and visible DWM frame are re-read during settling;
            // rotation, taskbar offsets and DPI are never baked into pixels.
            place(hwnd, point)?;
            thread::sleep(Duration::from_millis(500));
        }
        {
            let mut frame: RECT = unsafe { mem::zeroed() };
            if unsafe {
                DwmGetWindowAttribute(
                    hwnd,
                    DWMWA_EXTENDED_FRAME_BOUNDS as u32,
                    &mut frame as *mut RECT as _,
                    mem::size_of::<RECT>() as u32,
                )
            } != 0
            {
                return Err("Visible frame proof unavailable".into());
            }
            let frame = bounds(frame);
            let work = monitor(point)?;
            let off = (2 * frame.x + frame.width - (2 * work.x + work.width)).abs() > 2
                || (2 * frame.y + frame.height - (2 * work.y + work.height)).abs() > 2;
            if off && test {
                return Err("DEVLOOP-PLANTED-CENTER: visible frame is not centered".into());
            }
            if test {
                println!("Native MiOS launch centered: {frame:?}");
            }
        }
        Ok(())
    }
    pub fn error(message: &str) {
        unsafe {
            MessageBoxW(
                ptr::null_mut(),
                wide(message).as_ptr(),
                wide("MiOS SSOT").as_ptr(),
                MB_OK | MB_ICONERROR,
            );
        }
    }
}

fn main() {
    let args: Vec<String> = std::env::args().skip(1).collect();
    let proof = args
        .first()
        .is_some_and(|a| a.starts_with("--test-geometry"));
    let result = if proof {
        geometry_proof(args[0].ends_with("negative"))
    } else {
        #[cfg(windows)]
        {
            desktop::run(&args)
        }
        #[cfg(not(windows))]
        {
            Err("This launcher requires a Windows desktop".into())
        }
    };
    if let Err(error) = result {
        eprintln!("{error}");
        #[cfg(windows)]
        if !proof
            && !args.iter().any(|a| {
                matches!(
                    a.as_str(),
                    "--test-launch" | "--host-terminal" | "--stage-host-terminal" | "--dispatch"
                )
            })
        {
            desktop::error(&error);
        }
        std::process::exit(if proof { 2 } else { 3 });
    }
    if proof {
        println!("20 monitor/orientation/DPI geometry cases passed");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn geometry_and_planted_negative() {
        assert!(geometry_proof(false).is_ok());
        assert!(geometry_proof(true)
            .unwrap_err()
            .contains("DEVLOOP-PLANTED-CENTER"));
    }
    #[test]
    fn clamps_oversized_window_on_negative_portrait_monitor() {
        let work = Bounds {
            x: -1080,
            y: -640,
            width: 1080,
            height: 1840,
        };
        assert_eq!(
            center(
                work,
                Bounds {
                    x: 2000,
                    y: 0,
                    width: 3840,
                    height: 2160
                }
            ),
            work
        );
    }
    #[test]
    fn centers_odd_dimensions_with_one_pixel_rounding() {
        assert_eq!(
            center(
                Bounds {
                    x: 1920,
                    y: 41,
                    width: 1919,
                    height: 1039
                },
                Bounds {
                    x: 0,
                    y: 0,
                    width: 800,
                    height: 400
                }
            ),
            Bounds {
                x: 2479,
                y: 360,
                width: 800,
                height: 400
            }
        );
    }
}
