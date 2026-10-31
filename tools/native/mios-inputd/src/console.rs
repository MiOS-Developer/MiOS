// AI-hint: Linux console (VT) pty bridge for tmux-os (I3, C-18): wraps a VT tmux client in a pty, owns the mouse modes the client asks the terminal for, and turns gpm (or evdev pointer) events into SGR 1006 / legacy mouse reports only while the client has mouse mode on.
// AI-related: usr/share/mios/mios.toml [keybindings.global.controller.console], usr/libexec/mios/mios-terminal, tools/native/mios-inputd/src/runtime.rs, docs/design/mios-global-bindings-final.md (I3)
// AI-functions: MouseModes::filter, MouseModes::report, Pointer::gpm, Pointer::evdev, run
use std::io::{self, Read, Write};
use std::os::fd::{AsRawFd, FromRawFd, OwnedFd, RawFd};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::{Duration, Instant};

/// Mouse tracking the client enabled (DECSET 9 / 1000 / 1002 / 1003) and its encoding (1006 = SGR).
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct MouseModes {
    pub x10: bool,
    pub normal: bool,
    pub button: bool,
    pub any: bool,
    pub sgr: bool,
    pending: Vec<u8>,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Tracking {
    Off,
    X10,
    Normal,
    Button,
    Any,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Btn {
    Left,
    Middle,
    Right,
    WheelUp,
    WheelDown,
}
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Press,
    Release,
    Motion,
}
/// DECSET/DECRST private modes the bridge owns: the VT itself never enters mouse reporting (gpm would then also feed
/// the kernel's legacy reports), and the client sees exactly the reports this bridge writes.
const MOUSE_MODES: [u32; 8] = [9, 1000, 1001, 1002, 1003, 1005, 1006, 1015];
impl MouseModes {
    pub fn tracking(&self) -> Tracking {
        if self.any {
            Tracking::Any
        } else if self.button {
            Tracking::Button
        } else if self.normal {
            Tracking::Normal
        } else if self.x10 {
            Tracking::X10
        } else {
            Tracking::Off
        }
    }
    fn set(&mut self, mode: u32, on: bool) {
        match mode {
            9 => self.x10 = on,
            1000 => self.normal = on,
            1002 => self.button = on,
            1003 => self.any = on,
            1006 => self.sgr = on,
            _ => {}
        }
    }
    fn reset(&mut self) {
        let pending = std::mem::take(&mut self.pending);
        *self = Self { pending, ..Self::default() };
    }
    /// Filter the client's output for the real terminal: mouse-mode DECSET/DECRST parameters update this state and are
    /// removed (other parameters of the same sequence pass); RIS (ESC c) resets. A sequence split across reads waits.
    pub fn filter(&mut self, input: &[u8]) -> Vec<u8> {
        let mut buf = std::mem::take(&mut self.pending);
        buf.extend_from_slice(input);
        let mut out = Vec::with_capacity(buf.len());
        let mut i = 0;
        while i < buf.len() {
            if buf[i] != 0x1b {
                out.push(buf[i]);
                i += 1;
                continue;
            }
            let rest = &buf[i..];
            if rest.len() < 3 && (rest.len() == 1 || rest[1] == b'[') {
                self.pending = rest.to_vec();
                break;
            }
            if rest[1] == b'c' {
                self.reset();
                out.extend_from_slice(&rest[..2]);
                i += 2;
                continue;
            }
            if rest[1] != b'[' || rest[2] != b'?' {
                out.push(0x1b);
                i += 1;
                continue;
            }
            let end = rest[3..].iter().position(|b| !(b.is_ascii_digit() || *b == b';')).map(|p| p + 3);
            let Some(end) = end else {
                if rest.len() < 64 {
                    self.pending = rest.to_vec();
                    break;
                }
                out.extend_from_slice(rest);
                break;
            };
            let fin = rest[end];
            if fin != b'h' && fin != b'l' {
                out.extend_from_slice(&rest[..=end]);
                i += end + 1;
                continue;
            }
            let params: Vec<&[u8]> = rest[3..end].split(|b| *b == b';').collect();
            let mut keep: Vec<&[u8]> = vec![];
            for p in params {
                match std::str::from_utf8(p).ok().and_then(|t| t.parse::<u32>().ok()) {
                    Some(m) if MOUSE_MODES.contains(&m) => self.set(m, fin == b'h'),
                    _ => keep.push(p),
                }
            }
            if !keep.is_empty() {
                out.extend_from_slice(b"\x1b[?");
                out.extend_from_slice(&keep.join(&b';'));
                out.push(fin);
            }
            i += end + 1;
        }
        out
    }
    /// The report the client asked for (None while that event class is not tracked). `x`/`y` are 1-based cells;
    /// `mods` is the xterm modifier bits (4 shift, 8 meta, 16 control).
    pub fn report(&self, kind: Kind, btn: Option<Btn>, x: u16, y: u16, mods: u8) -> Option<Vec<u8>> {
        let t = self.tracking();
        let wheel = matches!(btn, Some(Btn::WheelUp | Btn::WheelDown));
        let ok = match (t, kind) {
            (Tracking::Off, _) => false,
            (Tracking::X10, Kind::Press) => true,
            (Tracking::X10, _) => false,
            (_, Kind::Press) => btn.is_some(),
            (_, Kind::Release) => btn.is_some() && !wheel,
            (Tracking::Button, Kind::Motion) => btn.is_some() && !wheel,
            (Tracking::Any, Kind::Motion) => !wheel,
            _ => false,
        };
        if !ok {
            return None;
        }
        let base: u8 = match btn {
            Some(Btn::Left) => 0,
            Some(Btn::Middle) => 1,
            Some(Btn::Right) => 2,
            Some(Btn::WheelUp) => 64,
            Some(Btn::WheelDown) => 65,
            None => 3,
        };
        let mods = if t == Tracking::X10 { 0 } else { mods & 28 };
        let motion = if kind == Kind::Motion { 32 } else { 0 };
        let (x, y) = (x.max(1), y.max(1));
        if self.sgr {
            let fin = if kind == Kind::Release { 'm' } else { 'M' };
            return Some(format!("\x1b[<{};{x};{y}{fin}", base + motion + mods).into_bytes());
        }
        // Legacy (X10 / normal encoding): one byte per field, so cells beyond 223 cannot be reported.
        if x > 223 || y > 223 {
            return None;
        }
        let code = if kind == Kind::Release { 3 } else { base } + motion + mods;
        Some(vec![0x1b, b'[', b'M', 32 + code, 32 + x as u8, 32 + y as u8])
    }
}

/// The bridge's pointer: the cell under it, held buttons, and (evdev source) the sub-cell pixel position.
#[derive(Clone, Debug)]
pub struct Pointer {
    pub x: u16,
    pub y: u16,
    held: [bool; 3],
    px: (f64, f64),
    cell: (f64, f64),
    pending: (bool, i32),
}
pub const GPM_EVENT_SIZE: usize = 28;
const GPM_MOVE: i32 = 1;
const GPM_DRAG: i32 = 2;
const GPM_DOWN: i32 = 4;
const GPM_UP: i32 = 8;
const GPM_HARD: u16 = 256;
fn btn_index(b: Btn) -> Option<usize> {
    match b {
        Btn::Left => Some(0),
        Btn::Middle => Some(1),
        Btn::Right => Some(2),
        _ => None,
    }
}
impl Pointer {
    pub fn new(cols: u16, rows: u16, cell_px: [u32; 2]) -> Self {
        let cell = (f64::from(cell_px[0]), f64::from(cell_px[1]));
        let (x, y) = ((cols / 2).max(1), (rows / 2).max(1));
        Self { x, y, held: [false; 3], px: ((f64::from(x) - 0.5) * cell.0, (f64::from(y) - 0.5) * cell.1), cell, pending: (false, 0) }
    }
    fn held_button(&self) -> Option<Btn> {
        [Btn::Left, Btn::Middle, Btn::Right].into_iter().zip(self.held).find(|(_, h)| *h).map(|(b, _)| b)
    }
    fn button(&mut self, b: Btn, down: bool, mods: u8, m: &MouseModes, out: &mut Vec<u8>) {
        if let Some(i) = btn_index(b) {
            if self.held[i] == down {
                return;
            }
            self.held[i] = down;
        }
        let kind = if down { Kind::Press } else { Kind::Release };
        if let Some(r) = m.report(kind, Some(b), self.x, self.y, mods) {
            out.extend(r);
        }
    }
    fn motion(&mut self, x: u16, y: u16, mods: u8, m: &MouseModes, out: &mut Vec<u8>) {
        if (x, y) == (self.x, self.y) {
            return;
        }
        self.x = x;
        self.y = y;
        if let Some(r) = m.report(Kind::Motion, self.held_button(), x, y, mods) {
            out.extend(r);
        }
    }
    /// One gpm `Gpm_Event` (gpm.h, 28 bytes on Linux): buttons, modifiers, vc, dx, dy, x, y, type, clicks, margin, wdx, wdy.
    pub fn gpm(&mut self, ev: &[u8], cols: u16, rows: u16, m: &MouseModes) -> Vec<u8> {
        let mut out = vec![];
        if ev.len() < GPM_EVENT_SIZE {
            return out;
        }
        let i16_at = |o: usize| i16::from_ne_bytes([ev[o], ev[o + 1]]);
        let i32_at = |o: usize| i32::from_ne_bytes([ev[o], ev[o + 1], ev[o + 2], ev[o + 3]]);
        let (buttons, gmods) = (ev[0], ev[1]);
        let (x, y) = (i16_at(8), i16_at(10));
        let kind = i32_at(12);
        let wdy = i16_at(26);
        // Kernel shift state (1 shift, 4 ctrl, 8 alt) -> xterm bits (4 shift, 16 control, 8 meta).
        let mods = (if gmods & 1 != 0 { 4 } else { 0 }) | (if gmods & 4 != 0 { 16 } else { 0 }) | (if gmods & 8 != 0 { 8 } else { 0 });
        let (x, y) = ((x.max(1) as u16).min(cols.max(1)), (y.max(1) as u16).min(rows.max(1)));
        if kind & (GPM_MOVE | GPM_DRAG | GPM_DOWN) != 0 {
            self.motion(x, y, mods, m, &mut out);
        }
        let map = [(4u8, Btn::Left), (2, Btn::Middle), (1, Btn::Right)];
        if kind & GPM_DOWN != 0 {
            for (bit, b) in map {
                if buttons & bit != 0 {
                    self.button(b, true, mods, m, &mut out);
                }
            }
            for (bit, b) in [(16u8, Btn::WheelUp), (32, Btn::WheelDown)] {
                if buttons & bit != 0 {
                    self.button(b, true, mods, m, &mut out);
                }
            }
        }
        if kind & GPM_UP != 0 {
            for (bit, b) in map {
                if buttons & bit != 0 {
                    self.button(b, false, mods, m, &mut out);
                }
            }
        }
        for _ in 0..wdy.unsigned_abs().min(5) {
            self.button(if wdy > 0 { Btn::WheelUp } else { Btn::WheelDown }, true, mods, m, &mut out);
        }
        out
    }
    /// One evdev event (EV_REL / EV_KEY / SYN_REPORT) of a relative pointer, read without a grab; motion is reported per
    /// frame once it crosses a cell (cell_px pixels per cell).
    pub fn evdev(&mut self, kind: u16, code: u16, value: i32, cols: u16, rows: u16, m: &MouseModes) -> Vec<u8> {
        let mut out = vec![];
        match (kind, code) {
            (2, 0) => {
                self.px.0 = (self.px.0 + f64::from(value)).clamp(0.0, f64::from(cols.max(1)) * self.cell.0 - 1.0);
                self.pending.0 = true;
            }
            (2, 1) => {
                self.px.1 = (self.px.1 + f64::from(value)).clamp(0.0, f64::from(rows.max(1)) * self.cell.1 - 1.0);
                self.pending.0 = true;
            }
            (2, 8) => self.pending.1 += value,
            (1, 0x110..=0x112) => {
                let b = match code { 0x110 => Btn::Left, 0x111 => Btn::Right, _ => Btn::Middle };
                self.button(b, value != 0, 0, m, &mut out);
            }
            (0, 0) => {
                if std::mem::take(&mut self.pending.0) {
                    let x = (self.px.0 / self.cell.0) as u16 + 1;
                    let y = (self.px.1 / self.cell.1) as u16 + 1;
                    self.motion(x.min(cols.max(1)), y.min(rows.max(1)), 0, m, &mut out);
                }
                let w = std::mem::take(&mut self.pending.1);
                for _ in 0..w.unsigned_abs().min(5) {
                    self.button(if w > 0 { Btn::WheelUp } else { Btn::WheelDown }, true, 0, m, &mut out);
                }
            }
            _ => {}
        }
        out
    }
}

pub struct Options {
    pub argv: Vec<String>,
    pub vt: Option<u32>,
    /// "gpm", "evdev" or "off" (resolved from the SSOT's "auto" by the caller).
    pub mouse: String,
    pub gpm_socket: PathBuf,
    /// evdev source: an explicit node (or a FIFO of input_event records in tests); else the first device whose name is
    /// in `pointer_names`.
    pub pointer_device: Option<PathBuf>,
    pub pointer_names: Vec<String>,
    pub cell_px: [u32; 2],
    /// JSON-lines log (tests); the bridge never writes diagnostics to the terminal it serves.
    pub log: Option<PathBuf>,
}
static WINCH: AtomicBool = AtomicBool::new(false);
static HANGUP: AtomicBool = AtomicBool::new(false);
extern "C" fn on_winch(_: libc::c_int) {
    WINCH.store(true, Ordering::Relaxed);
}
extern "C" fn on_hangup(_: libc::c_int) {
    HANGUP.store(true, Ordering::Relaxed);
}
struct RawGuard(Option<libc::termios>);
impl Drop for RawGuard {
    fn drop(&mut self) {
        if let Some(t) = &self.0 {
            // SAFETY: restores the termios captured from fd 0 at start.
            unsafe { libc::tcsetattr(0, libc::TCSANOW, t) };
        }
    }
}
fn winsize(fd: RawFd) -> libc::winsize {
    let mut ws = libc::winsize { ws_row: 24, ws_col: 80, ws_xpixel: 0, ws_ypixel: 0 };
    // SAFETY: TIOCGWINSZ fills one initialized winsize.
    if unsafe { libc::ioctl(fd, libc::TIOCGWINSZ, &mut ws) } < 0 || ws.ws_col == 0 || ws.ws_row == 0 {
        ws = libc::winsize { ws_row: 24, ws_col: 80, ws_xpixel: 0, ws_ypixel: 0 };
    }
    ws
}
fn write_all(fd: RawFd, mut b: &[u8]) -> io::Result<()> {
    while !b.is_empty() {
        // SAFETY: writes from a live slice to a descriptor this process owns.
        let n = unsafe { libc::write(fd, b.as_ptr().cast(), b.len()) };
        if n < 0 {
            let e = io::Error::last_os_error();
            if e.kind() == io::ErrorKind::Interrupted {
                continue;
            }
            if e.kind() == io::ErrorKind::WouldBlock {
                std::thread::sleep(Duration::from_millis(2));
                continue;
            }
            return Err(e);
        }
        b = &b[n as usize..];
    }
    Ok(())
}
fn read_fd(fd: RawFd, buf: &mut [u8]) -> io::Result<usize> {
    // SAFETY: reads into a live, writable buffer.
    let n = unsafe { libc::read(fd, buf.as_mut_ptr().cast(), buf.len()) };
    if n < 0 { Err(io::Error::last_os_error()) } else { Ok(n as usize) }
}
/// The VT number of fd 0 (/dev/tty<N>), for gpm's per-console client stack.
fn own_vt() -> Option<u32> {
    let link = std::fs::read_link("/proc/self/fd/0").ok()?;
    link.to_str()?.strip_prefix("/dev/tty")?.parse().ok().filter(|n| *n > 0)
}
fn gpm_connect(path: &Path, vt: u32) -> io::Result<std::os::unix::net::UnixStream> {
    let mut s = std::os::unix::net::UnixStream::connect(path)?;
    // Gpm_Connect: eventMask, defaultMask (gpm keeps drawing its pointer), minMod, maxMod, pid, vc.
    let mut req = Vec::with_capacity(16);
    req.extend(0xffffu16.to_ne_bytes());
    req.extend((GPM_MOVE as u16 | GPM_HARD).to_ne_bytes());
    req.extend(0u16.to_ne_bytes());
    req.extend(0xffffu16.to_ne_bytes());
    req.extend((std::process::id() as i32).to_ne_bytes());
    req.extend((vt as i32).to_ne_bytes());
    s.set_write_timeout(Some(Duration::from_millis(500)))?;
    s.write_all(&req)?;
    s.set_nonblocking(true)?;
    Ok(s)
}
fn evdev_named(names: &[String]) -> Option<std::fs::File> {
    use std::os::unix::fs::OpenOptionsExt;
    for path in crate::linux::discover().ok()? {
        let Ok(f) = std::fs::OpenOptions::new().read(true).custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC | libc::O_NOFOLLOW).open(&path) else { continue };
        let mut b = [0u8; 256];
        // SAFETY: EVIOCGNAME(256) fills at most 256 bytes of a live buffer.
        if unsafe { libc::ioctl(f.as_raw_fd(), ((2u64 << 30) | (256u64 << 16) | ((b'E' as u64) << 8) | 0x06) as _, b.as_mut_ptr()) } < 0 {
            continue;
        }
        let end = b.iter().position(|c| *c == 0).unwrap_or(b.len());
        if names.iter().any(|n| n.as_bytes() == &b[..end]) {
            return Some(f);
        }
    }
    None
}
/// Run `argv` (a VT tmux client) on a new pty; copy the VT to it and back; own the mouse modes; feed pointer reports.
/// Returns the client's exit code.
pub fn run(opt: Options) -> Result<i32, String> {
    let (program, rest) = opt.argv.split_first().ok_or("console-bridge needs a client argv after --")?;
    let mut logf = match &opt.log {
        Some(p) => Some(std::fs::OpenOptions::new().create(true).append(true).open(p).map_err(|e| e.to_string())?),
        None => None,
    };
    let mut log = |v: serde_json::Value| {
        if let Some(f) = &mut logf {
            let _ = writeln!(f, "{v}");
        }
    };
    // SAFETY: isatty/tcgetattr only read fd 0's state into an initialized struct.
    let tty = unsafe { libc::isatty(0) } == 1;
    let mut original: libc::termios = unsafe { std::mem::zeroed() };
    let have_termios = tty && unsafe { libc::tcgetattr(0, &mut original) } == 0;
    let mut ws = winsize(0);
    let (mut master, mut slave) = (-1, -1);
    // SAFETY: openpty writes two descriptors; the termios/winsize pointers reference initialized values.
    if unsafe { libc::openpty(&mut master, &mut slave, std::ptr::null_mut(), if have_termios { &original } else { std::ptr::null() }, &ws) } < 0 {
        // No pty: the client still attaches, directly on the VT (no console mouse), instead of the login failing.
        use std::os::unix::process::CommandExt;
        let e = io::Error::last_os_error();
        log(serde_json::json!({"console_bridge": "no pty; exec the client directly", "error": e.to_string()}));
        return Err(format!("openpty: {e}; exec {program}: {}", std::process::Command::new(program).args(rest).exec()));
    }
    // SAFETY: both descriptors were just returned by openpty and are owned here.
    let (master, slave) = unsafe { (OwnedFd::from_raw_fd(master), OwnedFd::from_raw_fd(slave)) };
    // SAFETY: FD_CLOEXEC on owned descriptors so the client inherits only its stdio.
    unsafe {
        libc::fcntl(master.as_raw_fd(), libc::F_SETFD, libc::FD_CLOEXEC);
        libc::fcntl(slave.as_raw_fd(), libc::F_SETFD, libc::FD_CLOEXEC);
    }
    let mut cmd = std::process::Command::new(program);
    cmd.args(rest).env("MIOS_CONSOLE_BRIDGE", "1");
    let (s1, s2) = (slave.try_clone().map_err(|e| e.to_string())?, slave.try_clone().map_err(|e| e.to_string())?);
    cmd.stdin(std::process::Stdio::from(slave)).stdout(std::process::Stdio::from(s1)).stderr(std::process::Stdio::from(s2));
    // SAFETY: setsid + TIOCSCTTY in the child before exec make the pty its controlling terminal (async-signal-safe calls).
    unsafe {
        use std::os::unix::process::CommandExt;
        cmd.pre_exec(|| {
            if libc::setsid() < 0 || libc::ioctl(0, libc::TIOCSCTTY, 0) < 0 {
                return Err(io::Error::last_os_error());
            }
            Ok(())
        });
    }
    let mut child = cmd.spawn().map_err(|e| format!("{program}: {e}"))?;
    drop(cmd);
    let _raw = if have_termios {
        let mut raw = original;
        // SAFETY: cfmakeraw edits a local copy; tcsetattr applies it to fd 0 (restored by the guard).
        unsafe {
            libc::cfmakeraw(&mut raw);
            libc::tcsetattr(0, libc::TCSANOW, &raw);
        }
        RawGuard(Some(original))
    } else {
        RawGuard(None)
    };
    // SAFETY: the handlers only store to atomics.
    unsafe {
        libc::signal(libc::SIGWINCH, on_winch as *const () as libc::sighandler_t);
        libc::signal(libc::SIGHUP, on_hangup as *const () as libc::sighandler_t);
        libc::signal(libc::SIGTERM, on_hangup as *const () as libc::sighandler_t);
    }
    let vt = opt.vt.or_else(own_vt).unwrap_or(0);
    log(serde_json::json!({"console_bridge": "started", "mouse": opt.mouse, "vt": vt, "cols": ws.ws_col, "rows": ws.ws_row}));
    let mut modes = MouseModes::default();
    let mut pointer = Pointer::new(ws.ws_col, ws.ws_row, opt.cell_px);
    let mut gpm: Option<std::os::unix::net::UnixStream> = None;
    let mut gpm_buf: Vec<u8> = vec![];
    let mut evdev: Option<std::fs::File> = None;
    let mut ev_buf: Vec<u8> = vec![];
    let mut last_try = Instant::now().checked_sub(Duration::from_secs(10)).unwrap_or_else(Instant::now);
    let mut stdin_open = true;
    let mfd = master.as_raw_fd();
    let mut buf = vec![0u8; 16384];
    let status = loop {
        if HANGUP.swap(false, Ordering::Relaxed) {
            // SAFETY: signals the client's session (its own process group after setsid).
            unsafe { libc::kill(-(child.id() as i32), libc::SIGHUP) };
        }
        if WINCH.swap(false, Ordering::Relaxed) {
            ws = winsize(0);
            // SAFETY: TIOCSWINSZ from an initialized winsize; the kernel signals the client's foreground group.
            unsafe { libc::ioctl(mfd, libc::TIOCSWINSZ, &ws) };
        }
        let tracking = modes.tracking() != Tracking::Off;
        // The pointer source is open exactly while the client tracks the mouse: with tracking off, gpm keeps its
        // own selection behaviour on this VT and no evdev node is held.
        if !tracking {
            if gpm.take().is_some() {
                log(serde_json::json!({"gpm": "disconnected"}));
            }
            evdev = None;
            last_try = Instant::now().checked_sub(Duration::from_secs(10)).unwrap_or(last_try);
        } else if last_try.elapsed() >= Duration::from_millis(2000) {
            if opt.mouse == "gpm" && gpm.is_none() {
                last_try = Instant::now();
                match gpm_connect(&opt.gpm_socket, vt) {
                    Ok(s) => {
                        log(serde_json::json!({"gpm": "connected", "vt": vt}));
                        gpm = Some(s);
                    }
                    Err(e) => log(serde_json::json!({"gpm": "unavailable", "error": e.to_string()})),
                }
            } else if opt.mouse == "evdev" && evdev.is_none() {
                last_try = Instant::now();
                evdev = match &opt.pointer_device {
                    Some(p) => {
                        use std::os::unix::fs::OpenOptionsExt;
                        std::fs::OpenOptions::new().read(true).custom_flags(libc::O_NONBLOCK | libc::O_CLOEXEC).open(p).ok()
                    }
                    None => evdev_named(&opt.pointer_names),
                };
                log(serde_json::json!({"evdev": if evdev.is_some() {"open"} else {"unavailable"}}));
            }
        }
        let mut fds = vec![libc::pollfd { fd: mfd, events: libc::POLLIN, revents: 0 }];
        if stdin_open {
            fds.push(libc::pollfd { fd: 0, events: libc::POLLIN, revents: 0 });
        }
        let gi = gpm.as_ref().map(|g| { fds.push(libc::pollfd { fd: g.as_raw_fd(), events: libc::POLLIN, revents: 0 }); fds.len() - 1 });
        let ei = evdev.as_ref().map(|e| { fds.push(libc::pollfd { fd: e.as_raw_fd(), events: libc::POLLIN, revents: 0 }); fds.len() - 1 });
        // Idle costs nothing: the poll sleeps until VT input, client output or a pointer event (a signal interrupts it).
        // It wakes early only to retry a missing pointer source while the client tracks the mouse.
        let waiting = tracking && gpm.is_none() && evdev.is_none() && opt.mouse != "off";
        let timeout = if waiting { 500 } else { 5000 };
        // SAFETY: a live, initialized pollfd array of the given length.
        let n = unsafe { libc::poll(fds.as_mut_ptr(), fds.len() as libc::nfds_t, timeout) };
        if n < 0 {
            if io::Error::last_os_error().kind() == io::ErrorKind::Interrupted {
                continue;
            }
            break None;
        }
        if fds[0].revents & (libc::POLLIN | libc::POLLHUP | libc::POLLERR) != 0 {
            match read_fd(mfd, &mut buf) {
                Ok(0) | Err(_) => break None,
                Ok(k) => {
                    let before = modes.tracking();
                    let out = modes.filter(&buf[..k]);
                    if modes.tracking() != before {
                        log(serde_json::json!({"mouse_mode": format!("{:?}", modes.tracking()), "sgr": modes.sgr}));
                    }
                    if write_all(1, &out).is_err() {
                        break None;
                    }
                }
            }
        }
        if stdin_open && fds[1].revents & (libc::POLLIN | libc::POLLHUP | libc::POLLERR) != 0 {
            match read_fd(0, &mut buf) {
                Ok(0) | Err(_) => stdin_open = false,
                Ok(k) => {
                    if write_all(mfd, &buf[..k]).is_err() {
                        break None;
                    }
                }
            }
        }
        let mut reports = vec![];
        if let Some(i) = gi {
            if fds[i].revents != 0 {
                let mut b = [0u8; GPM_EVENT_SIZE * 32];
                match gpm.as_mut().map(|g| g.read(&mut b)) {
                    Some(Ok(k)) if k > 0 => {
                        gpm_buf.extend_from_slice(&b[..k]);
                        while gpm_buf.len() >= GPM_EVENT_SIZE {
                            let ev: Vec<u8> = gpm_buf.drain(..GPM_EVENT_SIZE).collect();
                            reports.extend(pointer.gpm(&ev, ws.ws_col, ws.ws_row, &modes));
                        }
                    }
                    Some(Err(e)) if e.kind() == io::ErrorKind::WouldBlock => {}
                    _ => {
                        // gpm closed the connection (daemon restart): retry after the back-off.
                        log(serde_json::json!({"gpm": "closed"}));
                        gpm = None;
                        gpm_buf.clear();
                    }
                }
            }
        }
        if let Some(i) = ei {
            if fds[i].revents != 0 {
                let mut b = [0u8; 24 * 64];
                match evdev.as_mut().map(|e| e.read(&mut b)) {
                    Some(Ok(k)) if k > 0 => {
                        ev_buf.extend_from_slice(&b[..k]);
                        while ev_buf.len() >= 24 {
                            let e: Vec<u8> = ev_buf.drain(..24).collect();
                            let kind = u16::from_ne_bytes([e[16], e[17]]);
                            let code = u16::from_ne_bytes([e[18], e[19]]);
                            let value = i32::from_ne_bytes([e[20], e[21], e[22], e[23]]);
                            reports.extend(pointer.evdev(kind, code, value, ws.ws_col, ws.ws_row, &modes));
                        }
                    }
                    Some(Err(e)) if e.kind() == io::ErrorKind::WouldBlock => {}
                    _ => {
                        log(serde_json::json!({"evdev": "closed"}));
                        evdev = None;
                        ev_buf.clear();
                    }
                }
            }
        }
        if !reports.is_empty() {
            log(serde_json::json!({"reports": String::from_utf8_lossy(&reports)}));
            if write_all(mfd, &reports).is_err() {
                break None;
            }
        }
        if let Ok(Some(st)) = child.try_wait() {
            break Some(st);
        }
    };
    let status = match status {
        Some(s) => s,
        None => child.wait().map_err(|e| e.to_string())?,
    };
    // A client that exits with mouse modes on must not leave the VT in them: they were never forwarded.
    log(serde_json::json!({"console_bridge": "exited", "status": status.code()}));
    Ok(status.code().unwrap_or(1))
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn mouse_modes_are_consumed_and_other_private_modes_pass() {
        let mut m = MouseModes::default();
        assert_eq!(m.filter(b"a\x1b[?1000h\x1b[?1006hb"), b"ab");
        assert_eq!((m.tracking(), m.sgr), (Tracking::Normal, true));
        assert_eq!(m.filter(b"\x1b[?1002;25h"), b"\x1b[?25h", "a combined sequence keeps its non-mouse parameter");
        assert_eq!(m.tracking(), Tracking::Button);
        assert_eq!(m.filter(b"\x1b[?10"), b"", "a split sequence waits");
        assert_eq!(m.filter(b"03h\x1b[2J"), b"\x1b[2J");
        assert_eq!(m.tracking(), Tracking::Any);
        assert_eq!(m.filter(b"\x1b[?1003l\x1b[?1002l\x1b[?1000l"), b"");
        assert_eq!(m.tracking(), Tracking::Off);
        m.filter(b"\x1b[?1000h");
        assert_eq!(m.filter(b"\x1bc"), b"\x1bc", "RIS passes and resets");
        assert_eq!(m.tracking(), Tracking::Off);
        assert_eq!(m.filter(b"\x1b[?1049h\x1b[31m\x1b"), b"\x1b[?1049h\x1b[31m");
        assert_eq!(m.filter(b"[A"), b"\x1b[A");
    }
    #[test]
    fn reports_follow_the_tracking_mode_and_encoding() {
        let mut m = MouseModes::default();
        assert_eq!(m.report(Kind::Press, Some(Btn::Left), 3, 4, 0), None, "mouse mode off: nothing");
        m.filter(b"\x1b[?1000h\x1b[?1006h");
        assert_eq!(m.report(Kind::Press, Some(Btn::Left), 3, 4, 0).unwrap(), b"\x1b[<0;3;4M");
        assert_eq!(m.report(Kind::Release, Some(Btn::Left), 3, 4, 0).unwrap(), b"\x1b[<0;3;4m");
        assert_eq!(m.report(Kind::Press, Some(Btn::Right), 300, 90, 16).unwrap(), b"\x1b[<18;300;90M", "SGR has no 223 limit");
        assert_eq!(m.report(Kind::Press, Some(Btn::WheelUp), 1, 1, 0).unwrap(), b"\x1b[<64;1;1M");
        assert_eq!(m.report(Kind::Motion, Some(Btn::Left), 5, 5, 0), None, "1000 reports no motion");
        m.filter(b"\x1b[?1002h");
        assert_eq!(m.report(Kind::Motion, Some(Btn::Left), 5, 5, 0).unwrap(), b"\x1b[<32;5;5M");
        assert_eq!(m.report(Kind::Motion, None, 5, 5, 0), None, "1002 reports only drags");
        m.filter(b"\x1b[?1003h");
        assert_eq!(m.report(Kind::Motion, None, 5, 5, 0).unwrap(), b"\x1b[<35;5;5M");
        m.filter(b"\x1b[?1006l\x1b[?1003l\x1b[?1002l");
        assert_eq!(m.report(Kind::Press, Some(Btn::Left), 1, 2, 0).unwrap(), [0x1b, b'[', b'M', 32, 33, 34]);
        assert_eq!(m.report(Kind::Release, Some(Btn::Left), 1, 2, 0).unwrap(), [0x1b, b'[', b'M', 35, 33, 34]);
        assert_eq!(m.report(Kind::Press, Some(Btn::Left), 300, 2, 0), None, "legacy cannot encode column 300");
    }
    fn gpm_event(buttons: u8, mods: u8, x: i16, y: i16, kind: i32, wdy: i16) -> Vec<u8> {
        let mut v = vec![buttons, mods];
        v.extend(7u16.to_ne_bytes());
        v.extend([0i16, 0, x, y].iter().flat_map(|n| n.to_ne_bytes()));
        v.extend(kind.to_ne_bytes());
        v.extend(1i32.to_ne_bytes());
        v.extend(0i32.to_ne_bytes());
        v.extend(0i16.to_ne_bytes());
        v.extend(wdy.to_ne_bytes());
        assert_eq!(v.len(), GPM_EVENT_SIZE);
        v
    }
    #[test]
    fn gpm_events_become_sgr_clicks_drags_and_wheel() {
        let mut m = MouseModes::default();
        m.filter(b"\x1b[?1002h\x1b[?1006h");
        let mut p = Pointer::new(100, 30, [8, 16]);
        assert_eq!(p.gpm(&gpm_event(4, 0, 10, 5, GPM_DOWN | 16, 0), 100, 30, &m), b"\x1b[<0;10;5M");
        assert_eq!(p.gpm(&gpm_event(4, 0, 12, 5, GPM_DRAG, 0), 100, 30, &m), b"\x1b[<32;12;5M");
        assert_eq!(p.gpm(&gpm_event(4, 0, 12, 5, GPM_UP, 0), 100, 30, &m), b"\x1b[<0;12;5m");
        assert_eq!(p.gpm(&gpm_event(0, 4, 12, 5, GPM_MOVE, 2), 100, 30, &m), b"\x1b[<80;12;5M\x1b[<80;12;5M", "ctrl + wheel up x2");
        assert_eq!(p.gpm(&gpm_event(0, 0, 500, 500, GPM_MOVE, 0), 100, 30, &m), b"", "clamped to the screen; no drag without a button");
        assert_eq!((p.x, p.y), (100, 30));
        m.filter(b"\x1b[?1002l");
        assert_eq!(p.gpm(&gpm_event(1, 0, 3, 3, GPM_DOWN, 0), 100, 30, &MouseModes::default()), b"", "tracking off: no reports");
    }
    #[test]
    fn evdev_motion_crosses_cells_and_clicks() {
        let mut m = MouseModes::default();
        m.filter(b"\x1b[?1000h\x1b[?1006h");
        let mut p = Pointer::new(80, 24, [8, 16]);
        assert_eq!((p.x, p.y), (40, 12));
        assert!(p.evdev(2, 0, 3, 80, 24, &m).is_empty());
        assert!(p.evdev(0, 0, 0, 80, 24, &m).is_empty(), "1000: motion is never reported");
        assert_eq!((p.x, p.y), (40, 12), "3 px stays inside the cell");
        p.evdev(2, 0, 16, 80, 24, &m);
        p.evdev(2, 1, -32, 80, 24, &m);
        p.evdev(0, 0, 0, 80, 24, &m);
        assert_eq!((p.x, p.y), (42, 10));
        assert_eq!(p.evdev(1, 0x110, 1, 80, 24, &m), b"\x1b[<0;42;10M");
        assert_eq!(p.evdev(1, 0x110, 0, 80, 24, &m), b"\x1b[<0;42;10m");
        p.evdev(2, 8, -1, 80, 24, &m);
        assert_eq!(p.evdev(0, 0, 0, 80, 24, &m), b"\x1b[<65;42;10M");
    }
}
