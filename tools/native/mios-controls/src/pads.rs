// AI-hint: mios-controls pads: the dedicated evdev input thread (pinned with the terminal input thread) that reads controllers -- a pad nobody grabs, a private mios-input-fixture-* pad, or mios-inputd's virtual pad (mios-vctl-*) -- and, while capturing, mice; it turns events into input edges (buttons, HAT and analog d-pad, stick directions with hysteresis, analog triggers, wheel) with bounded auto-repeat for held directions, and grabs pads (EVIOCGRAB through mios-inputd's guarded Device) only for the length of a capture.
// AI-related: tools/native/mios-inputd/src/linux.rs (Device::open/grab/read_events), tools/native/mios-controls/src/input.rs, tools/native/mios-controls/src/tui.rs (Msg::Pad), usr/share/mios/mios.toml [desktop.controls]
// AI-functions: spawn, Translator::feed, parse_events, button_name, PadHandle::send
use crate::devices::{Device as Info, Kind};
use crate::tui::Msg;
use mios_inputd::router::Axis;
use std::collections::{BTreeMap, BTreeSet};
use std::fs::File;
use std::io::{Read, Write};
use std::os::unix::net::UnixStream;
use std::path::PathBuf;
use std::sync::mpsc::{Receiver, Sender};
use std::time::{Duration, Instant};

const EV_KEY: u16 = 1;
const EV_REL: u16 = 2;
const EV_ABS: u16 = 3;

#[derive(Clone, Copy, Debug)]
pub struct PadConfig {
    pub stick_threshold: f64,
    pub trigger_threshold: f64,
    pub repeat_delay: Duration,
    pub repeat_every: Duration,
}

impl Default for PadConfig {
    fn default() -> Self {
        Self { stick_threshold: 0.5, trigger_threshold: 0.3, repeat_delay: Duration::from_millis(400), repeat_every: Duration::from_millis(120) }
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub enum PadCmd {
    /// start/stop listening for capture-to-bind (mice opened, pads grabbed)
    Capture(bool),
    Rescan,
    Quit,
}

/// One input edge: `input` is "pad:BTN_SOUTH", "pad:LS_UP", "mouse:275", "mouse:wheel_up".
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct PadEdge {
    pub device: String,
    pub input: String,
    pub down: bool,
    pub repeat: bool,
}

pub fn button_name(code: u16) -> Option<&'static str> {
    Some(match code {
        0x130 => "BTN_SOUTH",
        0x131 => "BTN_EAST",
        0x133 => "BTN_NORTH",
        0x134 => "BTN_WEST",
        0x136 => "BTN_TL",
        0x137 => "BTN_TR",
        0x138 => "BTN_TL2",
        0x139 => "BTN_TR2",
        0x13a => "BTN_SELECT",
        0x13b => "BTN_START",
        0x13c => "BTN_MODE",
        0x13d => "BTN_THUMBL",
        0x13e => "BTN_THUMBR",
        0x220 => "BTN_DPAD_UP",
        0x221 => "BTN_DPAD_DOWN",
        0x222 => "BTN_DPAD_LEFT",
        0x223 => "BTN_DPAD_RIGHT",
        _ => return None,
    })
}

/// Kernel input_event records (64-bit: timeval 16 bytes, type u16, code u16, value i32) from a read buffer.
pub fn parse_events(bytes: &[u8]) -> Vec<(u16, u16, i32)> {
    bytes
        .chunks_exact(24)
        .map(|c| (u16::from_ne_bytes([c[16], c[17]]), u16::from_ne_bytes([c[18], c[19]]), i32::from_ne_bytes([c[20], c[21], c[22], c[23]])))
        .collect()
}

/// Event -> edge translation for one device.
#[derive(Default)]
pub struct Translator {
    pub axes: BTreeMap<u16, Axis>,
    /// the device reports digital BTN_TL2/BTN_TR2 (analog triggers are then ignored)
    pub digital_triggers: bool,
    /// the device has ABS_RX/RY (then ABS_Z/RZ are triggers, else the right stick)
    pub has_rx: bool,
    held: BTreeSet<String>,
}

impl Translator {
    pub fn new(axes: BTreeMap<u16, Axis>, keys: &BTreeSet<u16>) -> Self {
        let has_rx = axes.contains_key(&3);
        Self { axes, digital_triggers: keys.contains(&0x138) || keys.contains(&0x139), has_rx, held: BTreeSet::new() }
    }

    fn set(&mut self, input: &str, on: bool, out: &mut Vec<(String, bool)>) {
        let was = self.held.contains(input);
        if on && !was {
            self.held.insert(input.to_owned());
            out.push((input.to_owned(), true));
        } else if !on && was {
            self.held.remove(input);
            out.push((input.to_owned(), false));
        }
    }

    fn axis(&mut self, code: u16, value: i32) -> f64 {
        let a = self.axes.entry(code).or_insert(Axis { min: -32768, max: 32767, value: 0 });
        a.value = value;
        a.centered()
    }

    pub fn feed(&mut self, kind: u16, code: u16, value: i32, cfg: &PadConfig) -> Vec<(String, bool)> {
        let mut out = Vec::new();
        match kind {
            EV_KEY if value != 2 => {
                if let Some(b) = button_name(code) {
                    self.set(&format!("pad:{b}"), value != 0, &mut out);
                } else if (0x110..=0x117).contains(&code) {
                    self.set(&format!("mouse:{code}"), value != 0, &mut out);
                }
            }
            EV_ABS => match code {
                16 | 17 => {
                    let (neg, pos) = if code == 16 { ("pad:BTN_DPAD_LEFT", "pad:BTN_DPAD_RIGHT") } else { ("pad:BTN_DPAD_UP", "pad:BTN_DPAD_DOWN") };
                    self.set(neg, value < 0, &mut out);
                    self.set(pos, value > 0, &mut out);
                }
                0 | 1 | 3 | 4 => self.stick(code, value, cfg, &mut out),
                2 | 5 if !self.has_rx => self.stick(code, value, cfg, &mut out),
                2 | 5 if !self.digital_triggers => {
                    self.axis(code, value);
                    let t = self.axes.get(&code).map(Axis::trigger).unwrap_or(0.0);
                    let id = if code == 2 { "pad:BTN_TL2" } else { "pad:BTN_TR2" };
                    let on = if self.held.contains(id) { t > cfg.trigger_threshold * 0.6 } else { t >= cfg.trigger_threshold };
                    self.set(id, on, &mut out);
                }
                _ => {}
            },
            EV_REL => {
                let id = match (code, value.signum()) {
                    (8, 1) => Some("mouse:wheel_up"),
                    (8, -1) => Some("mouse:wheel_down"),
                    (6, 1) => Some("mouse:wheel_right"),
                    (6, -1) => Some("mouse:wheel_left"),
                    _ => None,
                };
                if let Some(id) = id {
                    out.push((id.to_owned(), true));
                    out.push((id.to_owned(), false));
                }
            }
            _ => {}
        }
        out
    }

    fn stick(&mut self, code: u16, value: i32, cfg: &PadConfig, out: &mut Vec<(String, bool)>) {
        let v = self.axis(code, value);
        let (side, vertical) = match code {
            0 => ("LS", false),
            1 => ("LS", true),
            3 | 2 => ("RS", false),
            _ => ("RS", true),
        };
        let (neg, pos) = if vertical { ("UP", "DOWN") } else { ("LEFT", "RIGHT") };
        let (n, p) = (format!("pad:{side}_{neg}"), format!("pad:{side}_{pos}"));
        // hysteresis: a direction is released below 70% of the threshold, so a noisy stick never chatters
        let th = cfg.stick_threshold;
        let on_n = if self.held.contains(&n) { v < -th * 0.7 } else { v <= -th };
        let on_p = if self.held.contains(&p) { v > th * 0.7 } else { v >= th };
        self.set(&n, on_n, out);
        self.set(&p, on_p, out);
    }
}

enum Src {
    /// a controller opened through mios-inputd's guarded open (refuses keyboards and virtual devices); grabbable
    Owned(mios_inputd::linux::Device),
    /// mios-inputd's virtual pad or a mouse: read only, never grabbed
    Plain(File),
}

struct Source {
    name: String,
    src: Src,
    tr: Translator,
    grabbed: bool,
}

impl Source {
    fn fd(&self) -> std::os::fd::BorrowedFd<'_> {
        use std::os::fd::AsFd;
        match &self.src {
            Src::Owned(d) => d.file.as_fd(),
            Src::Plain(f) => f.as_fd(),
        }
    }
    fn read(&mut self) -> std::io::Result<Vec<(u16, u16, i32)>> {
        match &mut self.src {
            Src::Owned(d) => d.read_events().map(|v| v.into_iter().map(|e| (e.kind, e.code, e.value)).collect()),
            Src::Plain(f) => {
                let mut buf = [0u8; 24 * 64];
                let n = f.read(&mut buf)?;
                if n == 0 {
                    return Err(std::io::Error::new(std::io::ErrorKind::UnexpectedEof, "device removed"));
                }
                Ok(parse_events(&buf[..n]))
            }
        }
    }
}

fn dev_path(d: &Info) -> Option<PathBuf> {
    d.event().map(|e| PathBuf::from("/dev/input").join(e))
}

/// Open every controller: pads nobody else owns and private fixtures through mios-inputd's guarded Device, and
/// mios-inputd's virtual pads read-only (their axis ranges come from the physical pad they clone).
fn open_pads(infos: &[Info]) -> (Vec<Source>, Vec<String>) {
    let mut out = Vec::new();
    let mut notes = Vec::new();
    for d in infos.iter().filter(|d| d.kind == Kind::Controller) {
        let Some(path) = dev_path(d) else { continue };
        if d.is_virtual() {
            let physical = d.name.rsplit('-').next().map(|e| PathBuf::from("/dev/input").join(e));
            let axes = physical.and_then(|p| mios_inputd::linux::Device::open(&p, false).ok()).map(|p| p.axes.clone()).unwrap_or_default();
            match File::open(&path) {
                Ok(f) => {
                    notes.push(format!("{} (mios-inputd virtual pad)", d.name));
                    out.push(Source { name: d.name.clone(), src: Src::Plain(f), tr: Translator::new(axes, &d.keys), grabbed: false });
                }
                Err(e) => notes.push(format!("{}: {e}", d.name)),
            }
            continue;
        }
        match mios_inputd::linux::Device::open(&path, d.is_fixture()) {
            Ok(dev) => {
                let tr = Translator::new(dev.axes.clone(), &d.keys);
                notes.push(d.name.clone());
                out.push(Source { name: d.name.clone(), src: Src::Owned(dev), tr, grabbed: false });
            }
            Err(e) => notes.push(format!("{}: {e}", d.name)),
        }
    }
    (out, notes)
}

fn open_mice(infos: &[Info]) -> Vec<Source> {
    infos
        .iter()
        .filter(|d| d.kind == Kind::Mouse && !d.is_virtual())
        .filter_map(|d| {
            let f = File::open(dev_path(d)?).ok()?;
            Some(Source { name: d.name.clone(), src: Src::Plain(f), tr: Translator::default(), grabbed: false })
        })
        .collect()
}

pub struct PadHandle {
    cmd: Sender<PadCmd>,
    wake: UnixStream,
}

impl PadHandle {
    pub fn send(&mut self, c: PadCmd) {
        if self.cmd.send(c).is_ok() {
            let _ = self.wake.write_all(&[1]);
        }
    }
}

impl Drop for PadHandle {
    fn drop(&mut self) {
        self.send(PadCmd::Quit);
    }
}

/// Start the pad thread on `cpu` (the input CPU). It blocks in poll(2) on the pads (and mice while capturing) and
/// its wake socket; it only uses a timeout while a direction is held (auto-repeat), so an idle pad costs nothing.
pub fn spawn(tx: Sender<Msg>, cpu: Option<usize>, cfg: PadConfig) -> Option<PadHandle> {
    let (wake_tx, wake_rx) = UnixStream::pair().ok()?;
    let (cmd_tx, cmd_rx) = std::sync::mpsc::channel();
    std::thread::Builder::new()
        .name("mios-controls-pads".into())
        .spawn(move || {
            if let Some(c) = cpu {
                crate::input::pin_current(c);
            }
            run(tx, cmd_rx, wake_rx, cfg);
        })
        .ok()?;
    Some(PadHandle { cmd: cmd_tx, wake: wake_tx })
}

fn run(tx: Sender<Msg>, cmd: Receiver<PadCmd>, mut wake: UnixStream, cfg: PadConfig) {
    use rustix::event::{poll, PollFd, PollFlags};
    let mut infos = crate::devices::scan();
    let (mut pads, notes) = open_pads(&infos);
    let _ = tx.send(Msg::PadStatus(if notes.is_empty() { "no controller".into() } else { format!("controllers: {}", notes.join("; ")) }));
    let mut mice: Vec<Source> = Vec::new();
    let mut capturing = false;
    // held directions -> (device, next repeat)
    let mut repeat: BTreeMap<(String, String), Instant> = BTreeMap::new();
    loop {
        let timeout = repeat.values().min().map(|t| t.saturating_duration_since(Instant::now()).as_millis().min(i32::MAX as u128) as i32).unwrap_or(-1);
        let ready: Vec<usize>;
        let woke;
        {
            let mut fds: Vec<PollFd> = Vec::with_capacity(pads.len() + mice.len() + 1);
            fds.push(PollFd::new(&wake, PollFlags::IN));
            for s in pads.iter().chain(mice.iter()) {
                fds.push(PollFd::from_borrowed_fd(s.fd(), PollFlags::IN));
            }
            if poll(&mut fds, timeout).is_err() {
                continue;
            }
            woke = !fds[0].revents().is_empty();
            ready = fds[1..].iter().enumerate().filter(|(_, f)| !f.revents().is_empty()).map(|(i, _)| i).collect();
        }
        if woke {
            let mut b = [0u8; 64];
            let _ = wake.read(&mut b);
            while let Ok(c) = cmd.try_recv() {
                match c {
                    PadCmd::Quit => return,
                    PadCmd::Rescan => {
                        infos = crate::devices::scan();
                        let (p, notes) = open_pads(&infos);
                        pads = p;
                        repeat.clear();
                        let _ = tx.send(Msg::PadStatus(if notes.is_empty() { "no controller".into() } else { format!("controllers: {}", notes.join("; ")) }));
                    }
                    PadCmd::Capture(on) => {
                        capturing = on;
                        repeat.clear();
                        if on {
                            infos = crate::devices::scan();
                            mice = open_mice(&infos);
                            let mut grabbed = Vec::new();
                            let mut busy = Vec::new();
                            for p in &mut pads {
                                if let Src::Owned(d) = &mut p.src {
                                    match d.grab() {
                                        Ok(()) => {
                                            p.grabbed = true;
                                            grabbed.push(p.name.clone());
                                        }
                                        Err(_) => busy.push(p.name.clone()),
                                    }
                                }
                            }
                            let mut msg = format!("capture: listening on {} controller(s) and {} mouse/mice", pads.len(), mice.len());
                            if !grabbed.is_empty() {
                                msg.push_str(&format!("; grabbed {}", grabbed.join(", ")));
                            }
                            if !busy.is_empty() {
                                msg.push_str(&format!("; {} owned by mios-inputd (its virtual pad reports what the router passes)", busy.join(", ")));
                            }
                            let _ = tx.send(Msg::PadStatus(msg));
                        } else {
                            mice.clear();
                            for p in &mut pads {
                                if let Src::Owned(d) = &mut p.src {
                                    if p.grabbed {
                                        d.release();
                                        p.grabbed = false;
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
        let mut gone = Vec::new();
        let n_pads = pads.len();
        for i in ready {
            let s = if i < n_pads { &mut pads[i] } else { &mut mice[i - n_pads] };
            match s.read() {
                Ok(events) => {
                    for (k, c, v) in events {
                        for (input, down) in s.tr.feed(k, c, v, &cfg) {
                            let key = (s.name.clone(), input.clone());
                            let dir = input.contains("DPAD_") || input.contains("LS_") || input.contains("RS_");
                            if dir && down && !capturing {
                                repeat.insert(key, Instant::now() + cfg.repeat_delay);
                            } else if !down {
                                repeat.remove(&key);
                            }
                            if tx.send(Msg::Pad(PadEdge { device: s.name.clone(), input, down, repeat: false })).is_err() {
                                return;
                            }
                        }
                    }
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock || e.kind() == std::io::ErrorKind::Interrupted => {}
                Err(_) => gone.push((i < n_pads, s.name.clone())),
            }
        }
        for (pad, name) in gone {
            if pad {
                pads.retain(|p| p.name != name);
            } else {
                mice.retain(|p| p.name != name);
            }
            repeat.retain(|(d, _), _| *d != name);
            let _ = tx.send(Msg::PadStatus(format!("{name} disconnected")));
        }
        let now = Instant::now();
        for ((dev, input), at) in repeat.iter_mut() {
            if *at <= now {
                *at = now + cfg.repeat_every;
                if tx.send(Msg::Pad(PadEdge { device: dev.clone(), input: input.clone(), down: true, repeat: true })).is_err() {
                    return;
                }
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ev(kind: u16, code: u16, value: i32) -> [u8; 24] {
        let mut b = [0u8; 24];
        b[16..18].copy_from_slice(&kind.to_ne_bytes());
        b[18..20].copy_from_slice(&code.to_ne_bytes());
        b[20..24].copy_from_slice(&value.to_ne_bytes());
        b
    }

    #[test]
    fn parses_kernel_event_records() {
        let mut bytes = Vec::new();
        bytes.extend(ev(1, 0x130, 1));
        bytes.extend(ev(3, 16, -1));
        bytes.extend([0u8; 5]);
        assert_eq!(parse_events(&bytes), vec![(1, 0x130, 1), (3, 16, -1)], "a partial record is ignored");
    }

    #[test]
    fn buttons_hat_sticks_triggers_and_wheel_become_edges() {
        let cfg = PadConfig::default();
        let mut axes = BTreeMap::new();
        for c in [0, 1, 3, 4] {
            axes.insert(c, Axis { min: -32768, max: 32767, value: 0 });
        }
        for c in [2, 5] {
            axes.insert(c, Axis { min: 0, max: 255, value: 0 });
        }
        let mut t = Translator::new(axes, &BTreeSet::new());
        assert_eq!(t.feed(1, 0x130, 1, &cfg), vec![("pad:BTN_SOUTH".into(), true)]);
        assert!(t.feed(1, 0x130, 2, &cfg).is_empty(), "autorepeat value is not a press");
        assert_eq!(t.feed(1, 0x130, 0, &cfg), vec![("pad:BTN_SOUTH".into(), false)]);
        assert_eq!(t.feed(3, 16, 1, &cfg), vec![("pad:BTN_DPAD_RIGHT".into(), true)]);
        assert_eq!(t.feed(3, 16, -1, &cfg), vec![("pad:BTN_DPAD_LEFT".into(), true), ("pad:BTN_DPAD_RIGHT".into(), false)]);
        assert_eq!(t.feed(3, 16, 0, &cfg), vec![("pad:BTN_DPAD_LEFT".into(), false)]);
        assert_eq!(t.feed(3, 1, -30000, &cfg), vec![("pad:LS_UP".into(), true)]);
        assert!(t.feed(3, 1, -14000, &cfg).is_empty(), "hysteresis keeps it held above 70% of the threshold");
        assert_eq!(t.feed(3, 1, -5000, &cfg), vec![("pad:LS_UP".into(), false)]);
        assert_eq!(t.feed(3, 3, 32000, &cfg), vec![("pad:RS_RIGHT".into(), true)]);
        assert_eq!(t.feed(3, 5, 200, &cfg), vec![("pad:BTN_TR2".into(), true)], "analog trigger without digital BTN_TR2");
        assert_eq!(t.feed(3, 5, 0, &cfg), vec![("pad:BTN_TR2".into(), false)]);
        assert_eq!(t.feed(2, 8, -1, &cfg), vec![("mouse:wheel_down".into(), true), ("mouse:wheel_down".into(), false)]);
        assert_eq!(t.feed(1, 0x113, 1, &cfg), vec![("mouse:275".into(), true)]);
    }

    #[test]
    fn digital_triggers_win_and_z_rz_are_the_right_stick_without_rx() {
        let cfg = PadConfig::default();
        let keys: BTreeSet<u16> = [0x138, 0x139].into_iter().collect();
        let mut axes = BTreeMap::new();
        axes.insert(3, Axis { min: -32768, max: 32767, value: 0 });
        axes.insert(5, Axis { min: 0, max: 255, value: 0 });
        let mut t = Translator::new(axes, &keys);
        assert!(t.feed(3, 5, 255, &cfg).is_empty(), "a digital BTN_TR2 pad's analog axis is not a second press");
        let mut axes = BTreeMap::new();
        axes.insert(2, Axis { min: -32768, max: 32767, value: 0 });
        axes.insert(5, Axis { min: -32768, max: 32767, value: 0 });
        let mut t = Translator::new(axes, &BTreeSet::new());
        assert_eq!(t.feed(3, 5, -32000, &cfg), vec![("pad:RS_UP".into(), true)]);
        assert_eq!(t.feed(3, 2, 32000, &cfg), vec![("pad:RS_RIGHT".into(), true)]);
    }
}
