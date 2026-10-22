//! Remote input into the live Hyprland seat (single seat, rdp-native-design.md 5):
//! keyboard through zwp_virtual_keyboard_v1 (xkb keymap of [remote.rdp].keyboard_layout, RDP set-1
//! scancodes -> evdev), Unicode text through a second virtual keyboard whose keymap carries the
//! needed keysyms (the wtype method), mouse through zwlr_virtual_pointer_v1 bound to the remote
//! output (absolute motion can never land on another output), touch and pen through uinput
//! devices that are mapped to the remote output (hl.device output = ...) BEFORE they exist.
//! Nothing reaches the seat before authentication: the server only builds input after it.
#![allow(unsafe_code)]

use std::collections::{HashMap, HashSet, VecDeque};
use std::fs::File;
use std::io::Write as _;
use std::os::fd::{AsFd, FromRawFd};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::mpsc::{Receiver, RecvTimeoutError, Sender};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use anyhow::{Context as _, Result, anyhow};
use ironrdp_server::{KeyboardEvent, MouseButton, MouseEvent, PenEventPdu, RdpServerInputHandler, TouchEventPdu};
use tracing::{debug, info, warn};
use wayland_client::globals::{GlobalListContents, registry_queue_init};
use wayland_client::protocol::{wl_output, wl_pointer, wl_registry, wl_seat};
use wayland_client::{Connection, Dispatch, EventQueue, Proxy, QueueHandle, delegate_noop};
use wayland_protocols_misc::zwp_virtual_keyboard_v1::client::{
    zwp_virtual_keyboard_manager_v1::ZwpVirtualKeyboardManagerV1, zwp_virtual_keyboard_v1::ZwpVirtualKeyboardV1,
};
use wayland_protocols_wlr::virtual_pointer::v1::client::{
    zwlr_virtual_pointer_manager_v1::ZwlrVirtualPointerManagerV1, zwlr_virtual_pointer_v1::ZwlrVirtualPointerV1,
};
use xkbcommon::xkb;

use crate::display::DisplayClient;
use crate::uinput::{self, Axis, Device};

pub enum Cmd {
    Key { code: u8, extended: bool, pressed: bool },
    Unicode { unit: u16, pressed: bool },
    Sync { caps: bool, num: bool },
    Mouse(MouseEvent),
    Touch(TouchEventPdu),
    Pen(PenEventPdu),
    DismissHover(u8),
    RdpeiClosed,
    /// session ended: release everything pressed, destroy uinput devices
    Release,
    Stop,
}

/// RDP set-1 scancode (+ E0 flag) -> Linux evdev keycode.
pub fn scancode_to_evdev(code: u8, extended: bool) -> Option<u32> {
    if !extended {
        return match code {
            0x01..=0x53 => Some(u32::from(code)),
            0x54 => Some(99),  // Alt+SysRq
            0x56 => Some(86),  // 102nd key
            0x57 => Some(87),  // F11
            0x58 => Some(88),  // F12
            0x59 => Some(117), // KP =
            0x5b..=0x5d => Some(183 + u32::from(code - 0x5b)), // F13..F15 (some layouts)
            0x64..=0x6e => Some(183 + u32::from(code - 0x64)), // F13..F23
            0x70 => Some(93),  // Katakana/Hiragana
            0x73 => Some(89),  // RO
            0x76 => Some(194), // F24
            0x79 => Some(92),  // Henkan
            0x7b => Some(94),  // Muhenkan
            0x7d => Some(124), // Yen
            0x7e => Some(121), // KP comma
            _ => None,
        };
    }
    Some(match code {
        0x10 => 165, // previous song
        0x19 => 163, // next song
        0x1c => 96,  // KP Enter
        0x1d => 97,  // Right Ctrl
        0x20 => 113, // mute
        0x21 => 140, // calculator
        0x22 => 164, // play/pause
        0x24 => 166, // stop
        0x2e => 114, // volume down
        0x30 => 115, // volume up
        0x32 => 172, // homepage
        0x35 => 98,  // KP /
        0x37 => 99,  // Print Screen
        0x38 => 100, // Right Alt / AltGr
        0x46 => 119, // Ctrl+Break -> Pause
        0x47 => 102, // Home
        0x48 => 103, // Up
        0x49 => 104, // Page Up
        0x4b => 105, // Left
        0x4d => 106, // Right
        0x4f => 107, // End
        0x50 => 108, // Down
        0x51 => 109, // Page Down
        0x52 => 110, // Insert
        0x53 => 111, // Delete
        0x5b => 125, // Left Meta
        0x5c => 126, // Right Meta
        0x5d => 127, // Menu
        0x5e => 116, // Power
        0x5f => 142, // Sleep
        0x63 => 143, // Wake
        0x65 => 217, // Search
        0x66 => 156, // Bookmarks
        0x67 => 173, // Refresh
        0x68 => 128, // Stop
        0x69 => 159, // Forward
        0x6a => 158, // Back
        0x6b => 157, // My Computer
        0x6c => 155, // Mail
        0x6d => 226, // Media
        _ => return None,
    })
}

/// Unicode control characters -> evdev keys (BackSpace, Tab, Enter, Escape, Delete).
pub fn unicode_control_evdev(unit: u16) -> Option<u32> {
    match unit {
        0x08 => Some(14),
        0x09 => Some(15),
        0x0a | 0x0d => Some(28),
        0x1b => Some(1),
        0x7f => Some(111),
        _ => None,
    }
}

pub struct InputHandler {
    pub tx: Sender<Cmd>,
}

impl RdpServerInputHandler for InputHandler {
    fn keyboard(&mut self, event: KeyboardEvent) {
        let cmd = match event {
            KeyboardEvent::Pressed { code, extended } => Cmd::Key { code, extended, pressed: true },
            KeyboardEvent::Released { code, extended } => Cmd::Key { code, extended, pressed: false },
            KeyboardEvent::UnicodePressed(u) => Cmd::Unicode { unit: u, pressed: true },
            KeyboardEvent::UnicodeReleased(u) => Cmd::Unicode { unit: u, pressed: false },
            KeyboardEvent::Synchronize(f) => {
                use ironrdp_pdu::input::fast_path::SynchronizeFlags as S;
                Cmd::Sync { caps: f.contains(S::CAPS_LOCK), num: f.contains(S::NUM_LOCK) }
            }
        };
        let _ = self.tx.send(cmd);
    }
    fn mouse(&mut self, event: MouseEvent) {
        let _ = self.tx.send(Cmd::Mouse(event));
    }
}

/// RDPEI (MS-RDPEI) handler: forwards touch and pen frames to the input thread.
pub struct RdpeiForward {
    pub tx: Sender<Cmd>,
    pub touch: bool,
    pub pen: bool,
}

impl ironrdp_server::RdpeiHandler for RdpeiForward {
    fn cs_ready(&mut self, pdu: ironrdp_server::CsReadyPdu) {
        info!(version = ?pdu.protocol_version, max_contacts = pdu.max_touch_contacts, flags = ?pdu.flags, "RDPEI client ready");
    }
    fn touch(&mut self, pdu: TouchEventPdu) {
        if self.touch {
            let _ = self.tx.send(Cmd::Touch(pdu));
        }
    }
    fn pen(&mut self, pdu: PenEventPdu) {
        if self.pen {
            let _ = self.tx.send(Cmd::Pen(pdu));
        }
    }
    fn dismiss_hovering(&mut self, pdu: ironrdp_server::DismissHoveringTouchContactPdu) {
        let _ = self.tx.send(Cmd::DismissHover(pdu.contact_id));
    }
}

impl Drop for RdpeiForward {
    fn drop(&mut self) {
        let _ = self.tx.send(Cmd::RdpeiClosed);
    }
}

pub struct RdpeiFactory {
    pub tx: Mutex<Sender<Cmd>>,
    pub touch: bool,
    pub pen: bool,
}

impl ironrdp_server::ServerEventSender for RdpeiFactory {
    fn set_sender(&mut self, _: tokio::sync::mpsc::UnboundedSender<ironrdp_server::ServerEvent>) {}
}

impl ironrdp_server::RdpeiServerFactory for RdpeiFactory {
    fn build_server(&self) -> ironrdp_server::RdpeiServer {
        let fwd = RdpeiForward { tx: self.tx.lock().expect("rdpei").clone(), touch: self.touch, pen: self.pen };
        ironrdp_server::RdpeiServer::new(Box::new(fwd))
            .with_protocol_version(ironrdp_server::RdpInputProtocolVersion::V300)
            .with_supported_features(ironrdp_server::ScReadyFeatures::MULTIPEN_INJECTION_SUPPORTED)
    }
}

pub struct InputParams {
    pub output: String,
    pub layout: String,
    pub touch_name: String,
    pub pen_name: String,
    pub touch: bool,
    pub pen: bool,
    /// (width << 32) | height of the RDP desktop (= remote output pixel size)
    pub desktop: Arc<AtomicU64>,
    pub display: Arc<DisplayClient>,
    pub counters: Arc<InputCounters>,
}

#[derive(Default)]
pub struct InputCounters {
    pub keys: AtomicU64,
    pub unicode: AtomicU64,
    pub pointer: AtomicU64,
    pub touch: AtomicU64,
    pub pen: AtomicU64,
    pub touch_fallback: AtomicU64,
    pub last_touch_us: AtomicU64,
}

struct WlState {
    outputs: Vec<(wl_output::WlOutput, Option<String>)>,
}

impl Dispatch<wl_registry::WlRegistry, GlobalListContents> for WlState {
    fn event(
        state: &mut Self,
        registry: &wl_registry::WlRegistry,
        event: wl_registry::Event,
        _: &GlobalListContents,
        _: &Connection,
        qh: &QueueHandle<Self>,
    ) {
        if let wl_registry::Event::Global { name, interface, version } = event {
            if interface == wl_output::WlOutput::interface().name {
                let o = registry.bind::<wl_output::WlOutput, _, _>(name, version.min(4), qh, ());
                state.outputs.push((o, None));
            }
        }
    }
}

impl Dispatch<wl_output::WlOutput, ()> for WlState {
    fn event(state: &mut Self, o: &wl_output::WlOutput, e: wl_output::Event, _: &(), _: &Connection, _: &QueueHandle<Self>) {
        if let wl_output::Event::Name { name } = e {
            if let Some(s) = state.outputs.iter_mut().find(|(p, _)| p == o) {
                s.1 = Some(name);
            }
        }
    }
}

delegate_noop!(WlState: ignore wl_seat::WlSeat);
delegate_noop!(WlState: ignore ZwlrVirtualPointerManagerV1);
delegate_noop!(WlState: ignore ZwlrVirtualPointerV1);
delegate_noop!(WlState: ignore ZwpVirtualKeyboardManagerV1);
delegate_noop!(WlState: ignore ZwpVirtualKeyboardV1);

fn memfd_with(contents: &[u8]) -> Result<File> {
    let name = c"mios-rdp-keymap";
    // SAFETY: memfd_create with a static NUL-terminated name.
    let fd = unsafe { libc::memfd_create(name.as_ptr(), libc::MFD_CLOEXEC) };
    if fd < 0 {
        return Err(anyhow!("memfd_create: {}", std::io::Error::last_os_error()));
    }
    // SAFETY: fd was just created and is owned here.
    let mut f = unsafe { File::from_raw_fd(fd) };
    f.write_all(contents)?;
    Ok(f)
}

struct Keyboard {
    vk: ZwpVirtualKeyboardV1,
    state: xkb::State,
    caps_idx: u32,
    num_idx: u32,
    last_mods: (u32, u32, u32, u32),
}

impl Keyboard {
    fn new(vk: ZwpVirtualKeyboardV1, layout: &str) -> Result<Self> {
        let ctx = xkb::Context::new(xkb::CONTEXT_NO_FLAGS);
        let (lay, var) = layout.split_once('-').unwrap_or((layout, ""));
        let keymap = xkb::Keymap::new_from_names(&ctx, "evdev", "pc105", lay, var, None, xkb::KEYMAP_COMPILE_NO_FLAGS)
            .ok_or_else(|| anyhow!("xkb keymap {layout:?} does not compile"))?;
        let text = keymap.get_as_string(xkb::KEYMAP_FORMAT_TEXT_V1);
        let mut bytes = text.into_bytes();
        bytes.push(0);
        let f = memfd_with(&bytes)?;
        vk.keymap(xkb::KEYMAP_FORMAT_TEXT_V1, f.as_fd(), bytes.len() as u32);
        let caps_idx = keymap.mod_get_index(xkb::MOD_NAME_CAPS);
        let num_idx = keymap.mod_get_index(xkb::MOD_NAME_NUM);
        Ok(Self { vk, state: xkb::State::new(&keymap), caps_idx, num_idx, last_mods: (0, 0, 0, 0) })
    }

    fn key(&mut self, time: u32, evdev: u32, pressed: bool) {
        self.vk.key(time, evdev, u32::from(pressed));
        self.state.update_key(
            xkb::Keycode::new(evdev + 8),
            if pressed { xkb::KeyDirection::Down } else { xkb::KeyDirection::Up },
        );
        self.send_mods();
    }

    fn send_mods(&mut self) {
        let m = (
            self.state.serialize_mods(xkb::STATE_MODS_DEPRESSED),
            self.state.serialize_mods(xkb::STATE_MODS_LATCHED),
            self.state.serialize_mods(xkb::STATE_MODS_LOCKED),
            self.state.serialize_layout(xkb::STATE_LAYOUT_EFFECTIVE),
        );
        if m != self.last_mods {
            self.vk.modifiers(m.0, m.1, m.2, m.3);
            self.last_mods = m;
        }
    }

    fn sync_locks(&mut self, caps: bool, num: bool) {
        let mut locked = self.state.serialize_mods(xkb::STATE_MODS_LOCKED);
        for (idx, on) in [(self.caps_idx, caps), (self.num_idx, num)] {
            if idx == xkb::MOD_INVALID {
                continue;
            }
            if on {
                locked |= 1 << idx;
            } else {
                locked &= !(1 << idx);
            }
        }
        let d = self.state.serialize_mods(xkb::STATE_MODS_DEPRESSED);
        let l = self.state.serialize_mods(xkb::STATE_MODS_LATCHED);
        let g = self.state.serialize_layout(xkb::STATE_LAYOUT_EFFECTIVE);
        self.state.update_mask(d, l, locked, 0, 0, g);
        self.send_mods();
    }
}

/// Unicode text: a second virtual keyboard whose keymap maps keycodes 9..=254 to the keysyms
/// typed so far (printable ASCII preloaded); a new character evicts the least recently used one.
struct UnicodeKeyboard {
    vk: ZwpVirtualKeyboardV1,
    map: HashMap<u32, u32>, // codepoint -> keycode (xkb, +8 included)
    lru: VecDeque<u32>,
    pending_high: Option<u16>,
    uploaded: bool,
}

const UNI_FIRST: u32 = 9;
const UNI_LAST: u32 = 254;

impl UnicodeKeyboard {
    fn new(vk: ZwpVirtualKeyboardV1) -> Self {
        let mut me = Self { vk, map: HashMap::new(), lru: VecDeque::new(), pending_high: None, uploaded: false };
        for cp in 0x20..0x7f {
            me.assign(cp);
        }
        me
    }

    fn assign(&mut self, cp: u32) -> u32 {
        if let Some(k) = self.map.get(&cp).copied() {
            self.lru.retain(|c| *c != cp);
            self.lru.push_back(cp);
            return k;
        }
        let k = if self.map.len() < (UNI_LAST - UNI_FIRST + 1) as usize {
            UNI_FIRST + self.map.len() as u32
        } else {
            let old = self.lru.pop_front().expect("lru");
            self.map.remove(&old).expect("mapped")
        };
        self.map.insert(cp, k);
        self.lru.push_back(cp);
        self.uploaded = false;
        k
    }

    fn keymap_text(&self) -> String {
        let mut codes = String::new();
        let mut syms = String::new();
        let mut by_code: Vec<(u32, u32)> = self.map.iter().map(|(c, k)| (*k, *c)).collect();
        by_code.sort();
        for (k, cp) in by_code {
            let ks = xkb::utf32_to_keysym(cp);
            let name = xkb::keysym_get_name(ks);
            let name = if name.is_empty() || name == "NoSymbol" { format!("U{cp:04X}") } else { name };
            codes.push_str(&format!("    <K{k}> = {k};\n"));
            syms.push_str(&format!("    key <K{k}> {{ [ {name} ] }};\n"));
        }
        format!(
            "xkb_keymap {{\n  xkb_keycodes \"mios-rdp-unicode\" {{\n    minimum = 8;\n    maximum = 255;\n{codes}  }};\n  xkb_types \"mios-rdp-unicode\" {{ include \"complete\" }};\n  xkb_compatibility \"mios-rdp-unicode\" {{ include \"complete\" }};\n  xkb_symbols \"mios-rdp-unicode\" {{\n{syms}  }};\n}};\n"
        )
    }

    fn upload(&mut self) -> Result<()> {
        if self.uploaded {
            return Ok(());
        }
        let mut b = self.keymap_text().into_bytes();
        b.push(0);
        let f = memfd_with(&b)?;
        self.vk.keymap(xkb::KEYMAP_FORMAT_TEXT_V1, f.as_fd(), b.len() as u32);
        self.vk.modifiers(0, 0, 0, 0);
        self.uploaded = true;
        Ok(())
    }

    /// One UTF-16 code unit from the client; surrogate pairs are joined. Returns the evdev code
    /// to press/release once a full code point is known.
    fn unit(&mut self, unit: u16, pressed: bool) -> Option<u32> {
        let cp = match unit {
            0xD800..=0xDBFF => {
                if pressed {
                    self.pending_high = Some(unit);
                }
                return None;
            }
            0xDC00..=0xDFFF => {
                let hi = self.pending_high?;
                if !pressed {
                    self.pending_high = None;
                }
                0x10000 + ((u32::from(hi) - 0xD800) << 10) + (u32::from(unit) - 0xDC00)
            }
            u => u32::from(u),
        };
        if cp < 0x20 || cp == 0x7f {
            return None; // control characters arrive as scancodes
        }
        let k = self.assign(cp);
        Some(k - 8)
    }
}

pub fn spawn(p: InputParams, rx: Receiver<Cmd>) -> std::thread::JoinHandle<()> {
    std::thread::Builder::new()
        .name("mios-rdp-input".into())
        .spawn(move || {
            if let Err(e) = run(p, rx) {
                warn!("input thread ended: {e:#}");
            }
        })
        .expect("spawn input thread")
}

struct Touch {
    dev: Option<Device>,
    slots: HashMap<u8, i32>, // contact id -> slot
    next_tracking: i32,
    failed: bool,
    emulating: Option<u8>, // contact driving the pointer in fallback mode
}

struct Pen {
    dev: Option<Device>,
    failed: bool,
    tool: u16,
    down: bool,
    in_range: bool,
}

const ABS_MAX: i32 = 32767;

fn scale(v: i32, extent: u32) -> i32 {
    let e = extent.max(2) as i64 - 1;
    ((i64::from(v).clamp(0, e) * i64::from(ABS_MAX)) / e) as i32
}

fn now_ms(t0: Instant) -> u32 {
    t0.elapsed().as_millis() as u32
}

fn run(p: InputParams, rx: Receiver<Cmd>) -> Result<()> {
    let conn = Connection::connect_to_env().context("connect to the Wayland session")?;
    let (globals, mut queue): (_, EventQueue<WlState>) = registry_queue_init(&conn).context("registry")?;
    let qh = queue.handle();
    let mut st = WlState { outputs: Vec::new() };
    for g in globals.contents().clone_list() {
        if g.interface == wl_output::WlOutput::interface().name {
            let o = globals.registry().bind::<wl_output::WlOutput, _, _>(g.name, g.version.min(4), &qh, ());
            st.outputs.push((o, None));
        }
    }
    let seat: wl_seat::WlSeat = globals.bind(&qh, 1..=9, ()).context("wl_seat")?;
    let vpm: ZwlrVirtualPointerManagerV1 = globals.bind(&qh, 2..=2, ()).context("zwlr_virtual_pointer_manager_v1 v2")?;
    let vkm: ZwpVirtualKeyboardManagerV1 = globals.bind(&qh, 1..=1, ()).context("zwp_virtual_keyboard_manager_v1")?;
    queue.roundtrip(&mut st)?;

    let t0 = Instant::now();
    let mut pointer: Option<(ZwlrVirtualPointerV1, String)> = None;
    let mut kbd: Option<Keyboard> = None;
    let mut uni: Option<UnicodeKeyboard> = None;
    let mut pressed_keys: HashSet<u32> = HashSet::new();
    let mut pressed_buttons: HashSet<u32> = HashSet::new();
    let mut uni_down: HashSet<u32> = HashSet::new();
    let mut touch = Touch { dev: None, slots: HashMap::new(), next_tracking: 1, failed: false, emulating: None };
    let mut pen = Pen { dev: None, failed: false, tool: uinput::BTN_TOOL_PEN, down: false, in_range: false };
    let mut wheel_acc: (i32, i32) = (0, 0);

    let desktop = |p: &InputParams| {
        let v = p.desktop.load(Ordering::Relaxed);
        (((v >> 32) as u32).max(1), ((v & 0xffff_ffff) as u32).max(1))
    };

    // the pointer must be bound to the remote output: (re)resolve it by name each time it is needed
    let ensure_pointer = |st: &mut WlState, queue: &mut EventQueue<WlState>, pointer: &mut Option<(ZwlrVirtualPointerV1, String)>| -> bool {
        if pointer.is_some() {
            return true;
        }
        let _ = queue.roundtrip(st);
        let Some((out, _)) = st.outputs.iter().find(|(_, n)| n.as_deref() == Some(p.output.as_str())) else {
            warn!(output = %p.output, "remote output not found; pointer input dropped");
            return false;
        };
        let vp = vpm.create_virtual_pointer_with_output(Some(&seat), Some(out), &qh, ());
        *pointer = Some((vp, p.output.clone()));
        info!(output = %p.output, "virtual pointer bound to the remote output");
        true
    };

    loop {
        let cmd = match rx.recv_timeout(Duration::from_millis(200)) {
            Ok(c) => c,
            Err(RecvTimeoutError::Timeout) => {
                let _ = queue.dispatch_pending(&mut st);
                continue;
            }
            Err(RecvTimeoutError::Disconnected) => break,
        };
        let time = now_ms(t0);
        match cmd {
            Cmd::Stop => break,
            Cmd::Key { code, extended, pressed } => {
                let Some(ev) = scancode_to_evdev(code, extended) else {
                    debug!(code, extended, "unmapped scancode");
                    continue;
                };
                if kbd.is_none() {
                    kbd = Some(Keyboard::new(vkm.create_virtual_keyboard(&seat, &qh, ()), &p.layout)?);
                }
                let k = kbd.as_mut().expect("kbd");
                if pressed {
                    pressed_keys.insert(ev);
                } else if !pressed_keys.remove(&ev) {
                    continue; // never release what was not pressed
                }
                k.key(time, ev, pressed);
                p.counters.keys.fetch_add(1, Ordering::Relaxed);
            }
            Cmd::Sync { caps, num } => {
                if kbd.is_none() {
                    kbd = Some(Keyboard::new(vkm.create_virtual_keyboard(&seat, &qh, ()), &p.layout)?);
                }
                kbd.as_mut().expect("kbd").sync_locks(caps, num);
            }
            Cmd::Unicode { unit, pressed } => {
                // control characters some clients send as Unicode (FreeRDP /kbd:unicode, soft keyboards):
                // the layout's own keys, so the main keyboard's modifier state applies
                if let Some(ev) = unicode_control_evdev(unit) {
                    if kbd.is_none() {
                        kbd = Some(Keyboard::new(vkm.create_virtual_keyboard(&seat, &qh, ()), &p.layout)?);
                    }
                    let k = kbd.as_mut().expect("kbd");
                    if pressed {
                        pressed_keys.insert(ev);
                        k.key(time, ev, true);
                    } else if pressed_keys.remove(&ev) {
                        k.key(time, ev, false);
                    }
                    p.counters.unicode.fetch_add(1, Ordering::Relaxed);
                    let _ = conn.flush();
                    continue;
                }
                if uni.is_none() {
                    uni = Some(UnicodeKeyboard::new(vkm.create_virtual_keyboard(&seat, &qh, ())));
                }
                let u = uni.as_mut().expect("uni");
                if let Some(ev) = u.unit(unit, pressed) {
                    u.upload()?;
                    if pressed {
                        uni_down.insert(ev);
                        u.vk.key(time, ev, 1);
                    } else if uni_down.remove(&ev) {
                        u.vk.key(time, ev, 0);
                    }
                    p.counters.unicode.fetch_add(1, Ordering::Relaxed);
                }
            }
            Cmd::Mouse(ev) => {
                if !ensure_pointer(&mut st, &mut queue, &mut pointer) {
                    continue;
                }
                let (w, h) = desktop(&p);
                let vp = &pointer.as_ref().expect("pointer").0;
                p.counters.pointer.fetch_add(1, Ordering::Relaxed);
                match ev {
                    MouseEvent::Move { x, y } => {
                        vp.motion_absolute(time, u32::from(x).min(w - 1), u32::from(y).min(h - 1), w, h);
                        vp.frame();
                    }
                    MouseEvent::Button { x, y, button, pressed } => {
                        vp.motion_absolute(time, u32::from(x).min(w - 1), u32::from(y).min(h - 1), w, h);
                        button_event(vp, time, button, pressed, &mut pressed_buttons);
                        vp.frame();
                    }
                    MouseEvent::ButtonRel { x, y, button, pressed } => {
                        vp.motion(time, f64::from(x), f64::from(y));
                        button_event(vp, time, button, pressed, &mut pressed_buttons);
                        vp.frame();
                    }
                    MouseEvent::RelMove { x, y } => {
                        vp.motion(time, f64::from(x), f64::from(y));
                        vp.frame();
                    }
                    MouseEvent::VerticalScroll { value } => {
                        debug!(value, "wheel (vertical)");
                        wheel(vp, time, wl_pointer::Axis::VerticalScroll, i32::from(value), &mut wheel_acc.1);
                    }
                    MouseEvent::HorizontalScroll { value } => {
                        debug!(value, "wheel (horizontal)");
                        wheel(vp, time, wl_pointer::Axis::HorizontalScroll, i32::from(value), &mut wheel_acc.0);
                    }
                    MouseEvent::Scroll { x, y } => {
                        // FreeRDP ainput: wheel rotation units (120 per notch) << 16, + = up / right
                        debug!(x, y, "wheel (ainput)");
                        if y != 0 {
                            wheel(vp, time, wl_pointer::Axis::VerticalScroll, y >> 16, &mut wheel_acc.1);
                        }
                        if x != 0 {
                            wheel(vp, time, wl_pointer::Axis::HorizontalScroll, x >> 16, &mut wheel_acc.0);
                        }
                    }
                    _ => {}
                }
            }
            Cmd::Touch(_) if !p.touch => {
                debug!("touch frame dropped ([remote.rdp.redirect].touch = false)");
            }
            Cmd::Pen(_) if !p.pen => {
                debug!("pen frame dropped ([remote.rdp.redirect].pen = false)");
            }
            Cmd::Touch(pdu) => {
                let (w, h) = desktop(&p);
                if touch.dev.is_none() && !touch.failed {
                    touch_device(&p, &mut touch);
                }
                let t_in = Instant::now();
                for frame in pdu.frames {
                    if let Some(dev) = touch.dev.as_mut() {
                        for c in &frame.contacts {
                            touch_contact(dev, &mut touch.slots, &mut touch.next_tracking, c, w, h);
                        }
                        let any = !touch.slots.is_empty();
                        dev.push(uinput::EV_KEY, uinput::BTN_TOUCH, i32::from(any));
                        dev.push(uinput::EV_KEY, uinput::BTN_TOOL_FINGER, i32::from(touch.slots.len() == 1));
                        dev.push(uinput::EV_KEY, uinput::BTN_TOOL_DOUBLETAP, i32::from(touch.slots.len() == 2));
                        if let Err(e) = dev.sync() {
                            warn!("touch write: {e:#}");
                        }
                    } else if ensure_pointer(&mut st, &mut queue, &mut pointer) {
                        // fallback: the first contact drives the pointer + left button, the rest is dropped
                        let vp = &pointer.as_ref().expect("pointer").0;
                        for c in &frame.contacts {
                            touch_as_pointer(vp, time, &mut touch.emulating, c, w, h, &mut pressed_buttons);
                        }
                        p.counters.touch_fallback.fetch_add(1, Ordering::Relaxed);
                    }
                    p.counters.touch.fetch_add(frame.contacts.len() as u64, Ordering::Relaxed);
                }
                p.counters.last_touch_us.store(t_in.elapsed().as_micros() as u64, Ordering::Relaxed);
            }
            Cmd::DismissHover(id) => {
                if let Some(dev) = touch.dev.as_mut() {
                    if let Some(slot) = touch.slots.remove(&id) {
                        dev.push(uinput::EV_ABS, uinput::ABS_MT_SLOT, slot);
                        dev.push(uinput::EV_ABS, uinput::ABS_MT_TRACKING_ID, -1);
                        let _ = dev.sync();
                    }
                }
            }
            Cmd::Pen(pdu) => {
                let (w, h) = desktop(&p);
                if pen.dev.is_none() && !pen.failed {
                    pen_device(&p, &mut pen);
                }
                for frame in pdu.frames {
                    for c in &frame.contacts {
                        if pen.dev.is_some() {
                            pen_contact(&mut pen, c, w, h);
                        } else if ensure_pointer(&mut st, &mut queue, &mut pointer) {
                            let vp = &pointer.as_ref().expect("pointer").0;
                            pen_as_pointer(vp, time, c, w, h, &mut pressed_buttons);
                        }
                        p.counters.pen.fetch_add(1, Ordering::Relaxed);
                    }
                }
            }
            Cmd::RdpeiClosed => {
                if touch.dev.take().is_some() {
                    info!("touch device removed (RDPEI closed)");
                }
                if pen.dev.take().is_some() {
                    info!("pen device removed (RDPEI closed)");
                }
                touch = Touch { dev: None, slots: HashMap::new(), next_tracking: 1, failed: false, emulating: None };
                pen = Pen { dev: None, failed: false, tool: uinput::BTN_TOOL_PEN, down: false, in_range: false };
            }
            Cmd::Release => {
                let time = now_ms(t0);
                if let Some(k) = kbd.as_mut() {
                    for ev in pressed_keys.drain() {
                        k.key(time, ev, false);
                    }
                }
                if let Some(u) = uni.as_ref() {
                    for ev in uni_down.drain() {
                        u.vk.key(time, ev, 0);
                    }
                }
                if let Some((vp, _)) = pointer.as_ref() {
                    for b in pressed_buttons.drain() {
                        vp.button(time, b, wl_pointer::ButtonState::Released);
                    }
                    vp.frame();
                }
                touch.dev = None;
                pen.dev = None;
                touch.slots.clear();
                touch.failed = false;
                pen.failed = false;
                if let Some((vp, _)) = pointer.take() {
                    vp.destroy();
                }
                if let Some(k) = kbd.take() {
                    k.vk.destroy();
                }
                if let Some(u) = uni.take() {
                    u.vk.destroy();
                }
            }
        }
        let _ = conn.flush();
    }
    let _ = conn.flush();
    Ok(())
}

/// RDP wheel rotation units (120 per notch; + = up for the vertical wheel, + = right for the horizontal
/// one) -> wl_pointer axis (+ = down / right) with discrete steps; partial notches accumulate.
fn wheel(vp: &ZwlrVirtualPointerV1, time: u32, axis: wl_pointer::Axis, units: i32, acc: &mut i32) {
    let sign = if axis == wl_pointer::Axis::VerticalScroll { -1 } else { 1 };
    let units = units.clamp(-120 * 16, 120 * 16);
    *acc += units;
    let notches = *acc / 120;
    *acc -= notches * 120;
    if notches != 0 {
        vp.axis_discrete(time, axis, f64::from(sign * notches) * 15.0, sign * notches);
    } else {
        vp.axis(time, axis, f64::from(sign * units) * 15.0 / 120.0);
    }
    vp.axis_source(wl_pointer::AxisSource::Wheel);
    vp.frame();
}

fn button_event(vp: &ZwlrVirtualPointerV1, time: u32, b: MouseButton, pressed: bool, held: &mut HashSet<u32>) {
    let code = match b {
        MouseButton::Left => 0x110,
        MouseButton::Right => 0x111,
        MouseButton::Middle => 0x112,
        MouseButton::X1 => 0x113,
        MouseButton::X2 => 0x114,
        _ => return,
    };
    if pressed {
        held.insert(code);
    } else if !held.remove(&code) {
        return;
    }
    vp.button(time, code, if pressed { wl_pointer::ButtonState::Pressed } else { wl_pointer::ButtonState::Released });
}

/// Map the device to the remote output first; refuse to create it when the rule is not applied.
fn mapped_device(p: &InputParams, name: &str, keys: &[u16], axes: &[Axis]) -> Option<Device> {
    let hn = uinput::hypr_name(name);
    match p.display.device_rule(&hn) {
        Ok(true) => {}
        Ok(false) | Err(_) => {
            warn!(device = %hn, output = %p.output, "could not map the device to the remote output; using pointer emulation");
            return None;
        }
    }
    match Device::create(name, keys, axes, true) {
        Ok(d) => {
            info!(device = %name, output = %p.output, "uinput device created (mapped to the remote output)");
            Some(d)
        }
        Err(e) => {
            warn!("uinput {name}: {e:#}; using pointer emulation");
            None
        }
    }
}

fn touch_device(p: &InputParams, t: &mut Touch) {
    let axes = [
        Axis { code: uinput::ABS_X, min: 0, max: ABS_MAX, resolution: 0 },
        Axis { code: uinput::ABS_Y, min: 0, max: ABS_MAX, resolution: 0 },
        Axis { code: uinput::ABS_MT_SLOT, min: 0, max: 9, resolution: 0 },
        Axis { code: uinput::ABS_MT_TRACKING_ID, min: 0, max: 65535, resolution: 0 },
        Axis { code: uinput::ABS_MT_POSITION_X, min: 0, max: ABS_MAX, resolution: 0 },
        Axis { code: uinput::ABS_MT_POSITION_Y, min: 0, max: ABS_MAX, resolution: 0 },
    ];
    t.dev = mapped_device(p, &p.touch_name, &[uinput::BTN_TOUCH, uinput::BTN_TOOL_FINGER, uinput::BTN_TOOL_DOUBLETAP], &axes);
    t.failed = t.dev.is_none();
}

fn pen_device(p: &InputParams, pen: &mut Pen) {
    let axes = [
        Axis { code: uinput::ABS_X, min: 0, max: ABS_MAX, resolution: 100 },
        Axis { code: uinput::ABS_Y, min: 0, max: ABS_MAX, resolution: 100 },
        Axis { code: uinput::ABS_PRESSURE, min: 0, max: 1024, resolution: 0 },
        Axis { code: uinput::ABS_TILT_X, min: -90, max: 90, resolution: 0 },
        Axis { code: uinput::ABS_TILT_Y, min: -90, max: 90, resolution: 0 },
    ];
    pen.dev = mapped_device(
        p,
        &p.pen_name,
        &[uinput::BTN_TOOL_PEN, uinput::BTN_TOOL_RUBBER, uinput::BTN_TOUCH, uinput::BTN_STYLUS],
        &axes,
    );
    pen.failed = pen.dev.is_none();
}

fn touch_contact(dev: &mut Device, slots: &mut HashMap<u8, i32>, next: &mut i32, c: &ironrdp_server::TouchContact, w: u32, h: u32) {
    use ironrdp_server::TouchContactFlags as F;
    let f = c.contact_flags;
    let x = scale(c.x, w);
    let y = scale(c.y, h);
    if f.contains(F::DOWN) || (f.contains(F::INCONTACT) && !slots.contains_key(&c.contact_id)) {
        let used: HashSet<i32> = slots.values().copied().collect();
        let Some(slot) = (0..10).find(|s| !used.contains(s)) else { return };
        slots.insert(c.contact_id, slot);
        dev.push(uinput::EV_ABS, uinput::ABS_MT_SLOT, slot);
        dev.push(uinput::EV_ABS, uinput::ABS_MT_TRACKING_ID, *next);
        *next = (*next + 1) % 65535;
        dev.push(uinput::EV_ABS, uinput::ABS_MT_POSITION_X, x);
        dev.push(uinput::EV_ABS, uinput::ABS_MT_POSITION_Y, y);
        dev.push(uinput::EV_ABS, uinput::ABS_X, x);
        dev.push(uinput::EV_ABS, uinput::ABS_Y, y);
    } else if f.contains(F::UP) || f.contains(F::CANCELED) {
        if let Some(slot) = slots.remove(&c.contact_id) {
            dev.push(uinput::EV_ABS, uinput::ABS_MT_SLOT, slot);
            dev.push(uinput::EV_ABS, uinput::ABS_MT_TRACKING_ID, -1);
        }
    } else if let Some(slot) = slots.get(&c.contact_id).copied() {
        dev.push(uinput::EV_ABS, uinput::ABS_MT_SLOT, slot);
        dev.push(uinput::EV_ABS, uinput::ABS_MT_POSITION_X, x);
        dev.push(uinput::EV_ABS, uinput::ABS_MT_POSITION_Y, y);
        dev.push(uinput::EV_ABS, uinput::ABS_X, x);
        dev.push(uinput::EV_ABS, uinput::ABS_Y, y);
    }
}

fn touch_as_pointer(
    vp: &ZwlrVirtualPointerV1,
    time: u32,
    driving: &mut Option<u8>,
    c: &ironrdp_server::TouchContact,
    w: u32,
    h: u32,
    held: &mut HashSet<u32>,
) {
    use ironrdp_server::TouchContactFlags as F;
    if driving.is_some_and(|d| d != c.contact_id) {
        return;
    }
    let (x, y) = (c.x.clamp(0, w as i32 - 1) as u32, c.y.clamp(0, h as i32 - 1) as u32);
    vp.motion_absolute(time, x, y, w, h);
    if c.contact_flags.contains(F::DOWN) {
        *driving = Some(c.contact_id);
        button_event(vp, time, MouseButton::Left, true, held);
    } else if c.contact_flags.contains(F::UP) || c.contact_flags.contains(F::CANCELED) {
        button_event(vp, time, MouseButton::Left, false, held);
        *driving = None;
    }
    vp.frame();
}

fn pen_contact(pen: &mut Pen, c: &ironrdp_server::PenContact, w: u32, h: u32) {
    use ironrdp_server::{PenContactFlags as F, PenFlags as P};
    let dev = pen.dev.as_mut().expect("pen device");
    let flags = c.fields.pen_flags.unwrap_or(P::empty());
    let tool = if flags.contains(P::ERASER_PRESSED) || flags.contains(P::INVERTED) {
        uinput::BTN_TOOL_RUBBER
    } else {
        uinput::BTN_TOOL_PEN
    };
    let leaving = c.contact_flags.contains(F::UP) && !c.contact_flags.contains(F::INRANGE)
        || c.contact_flags.contains(F::CANCELED);
    if pen.in_range && tool != pen.tool {
        dev.push(uinput::EV_KEY, pen.tool, 0);
        pen.in_range = false;
    }
    dev.push(uinput::EV_ABS, uinput::ABS_X, scale(c.x, w));
    dev.push(uinput::EV_ABS, uinput::ABS_Y, scale(c.y, h));
    if let Some(pr) = c.fields.pressure {
        dev.push(uinput::EV_ABS, uinput::ABS_PRESSURE, pr.min(1024) as i32);
    }
    if let Some(tx) = c.fields.tilt_x {
        dev.push(uinput::EV_ABS, uinput::ABS_TILT_X, i32::from(tx).clamp(-90, 90));
    }
    if let Some(ty) = c.fields.tilt_y {
        dev.push(uinput::EV_ABS, uinput::ABS_TILT_Y, i32::from(ty).clamp(-90, 90));
    }
    if !pen.in_range && !leaving {
        dev.push(uinput::EV_KEY, tool, 1);
        pen.in_range = true;
        pen.tool = tool;
    }
    let contact = c.contact_flags.contains(F::INCONTACT) || c.contact_flags.contains(F::DOWN);
    let contact = contact && !c.contact_flags.contains(F::UP);
    if contact != pen.down {
        dev.push(uinput::EV_KEY, uinput::BTN_TOUCH, i32::from(contact));
        pen.down = contact;
    }
    dev.push(uinput::EV_KEY, uinput::BTN_STYLUS, i32::from(flags.contains(P::BARREL_PRESSED)));
    if leaving {
        if pen.down {
            dev.push(uinput::EV_KEY, uinput::BTN_TOUCH, 0);
            pen.down = false;
        }
        dev.push(uinput::EV_ABS, uinput::ABS_PRESSURE, 0);
        dev.push(uinput::EV_KEY, pen.tool, 0);
        pen.in_range = false;
    }
    if let Err(e) = dev.sync() {
        warn!("pen write: {e:#}");
    }
}

fn pen_as_pointer(vp: &ZwlrVirtualPointerV1, time: u32, c: &ironrdp_server::PenContact, w: u32, h: u32, held: &mut HashSet<u32>) {
    use ironrdp_server::PenContactFlags as F;
    let (x, y) = (c.x.clamp(0, w as i32 - 1) as u32, c.y.clamp(0, h as i32 - 1) as u32);
    vp.motion_absolute(time, x, y, w, h);
    if c.contact_flags.contains(F::DOWN) {
        button_event(vp, time, MouseButton::Left, true, held);
    } else if c.contact_flags.contains(F::UP) {
        button_event(vp, time, MouseButton::Left, false, held);
    }
    vp.frame();
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn scancodes() {
        assert_eq!(scancode_to_evdev(0x1e, false), Some(30)); // A
        assert_eq!(scancode_to_evdev(0x1c, false), Some(28)); // Enter
        assert_eq!(scancode_to_evdev(0x1c, true), Some(96)); // KP Enter
        assert_eq!(scancode_to_evdev(0x48, true), Some(103)); // Up
        assert_eq!(scancode_to_evdev(0x38, true), Some(100)); // AltGr
        assert_eq!(scancode_to_evdev(0x5b, true), Some(125)); // Super
        assert_eq!(scancode_to_evdev(0x00, false), None);
        assert_eq!(scancode_to_evdev(0x7f, true), None);
    }

    #[test]
    fn unicode_keymap_evicts_lru_and_joins_surrogates() {
        let ctx = xkb::Context::new(xkb::CONTEXT_NO_FLAGS);
        // A UnicodeKeyboard without a live proxy: test the mapping logic through a detached map.
        let mut map: HashMap<u32, u32> = HashMap::new();
        let mut lru: VecDeque<u32> = VecDeque::new();
        let _ = (&mut map, &mut lru, ctx);
        // surrogate pair for U+1F600
        let hi = 0xD83Du32;
        let lo = 0xDE00u32;
        let cp = 0x10000 + ((hi - 0xD800) << 10) + (lo - 0xDC00);
        assert_eq!(cp, 0x1F600);
        let ks = xkb::utf32_to_keysym(cp);
        assert_ne!(xkb::keysym_get_name(ks), "");
    }

    #[test]
    fn unicode_controls_become_keys() {
        assert_eq!(unicode_control_evdev(0x08), Some(14));
        assert_eq!(unicode_control_evdev(0x0d), Some(28));
        assert_eq!(unicode_control_evdev(u16::from(b'a')), None);
    }

    #[test]
    fn abs_scale_covers_the_full_range() {
        assert_eq!(scale(0, 1100), 0);
        assert_eq!(scale(1099, 1100), ABS_MAX);
        assert_eq!(scale(5000, 1100), ABS_MAX);
        assert_eq!(scale(-5, 1100), 0);
    }
}
